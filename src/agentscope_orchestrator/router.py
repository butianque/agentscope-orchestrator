"""多频道机械负载均衡 + 健康 failover（替代框架的模型路由，避免"模型选模型"软判定）。

策略：轮询分派到健康频道；某频道连续失败达到阈值即视为 down（冷却），
call() 自动 failover 到下一个健康频道；失败计数/冷却计时驱动恢复。
"""
from __future__ import annotations

import asyncio
import logging
import time

from .msgs import Msg

from agentscope_orchestrator.supervisor import TransientError

logger = logging.getLogger(__name__)

FAIL_THRESHOLD = 2      # 连续失败达此值 → down
COOLDOWN_SEC = 30.0     # down 后冷却期
MAX_ATTEMPTS = 4        # call() 总尝试次数**下限**（实际见 _attempts_budget）
# ★ 2026-10-14 修：原 `MAX_ATTEMPTS = 4` 被当作**固定**总次数，而它**恰好等于**当时
#   配置的通道数（4）⇒ 轮询一圈正好每通道各一次 ⇒ **唯一活通道一次瞬时失败即耗尽
#   配额**，整轮报 round_error。实测证据（09:11 真跑）：
#       4 通道里只有 dddddd 活（aaa/ggg 503 model_not_found、mf 401）
#       ⇒ 3/4 的尝试**必然**浪费在已知死通道上
#   ⇒ 尝试预算必须**随通道数缩放**，而不是固定常数。
_ATTEMPTS_PER_CHANNEL = 2
# ★ R62：全槽冷却时最多等多久（秒）。等到了就重试，超了就抛。
#   取值理由：上游 504 自己给的 `retry_after` 是 120 秒；本值给足几个来回，
#   又不至于让无人值守卡死（超时会如实记账，不是静默挂起）。
WAIT_BUDGET_SEC = 600.0


# ★ R72：一次 `call()` 里最多向"外面"要几次新槽表（防死循环）。
#   给 2 次：① 没有健康槽时问一次（可能是恢复）；② 预算耗尽时再问一次。
_刷新轮数上限 = 2
# ★ R72：两次刷新之间的最小间隔（秒）。刷新 = 重新读配置 + 重新探活 = 网络动作。
#   ★ 节流是**必须**的：`call()` 在失败路径上会被频繁调用，不节流就是拿
#     探活请求打上游（而且会把"抖动"放大成"洪水"）。
_刷新最小间隔 = 30.0

# ★★★★★ R164：慢槽让路倍数。
#   实测：最快的槽能干完是 40 秒，慢的 70 秒、超时的 110 秒。
#   1.5 倍是「明显更慢」的门槛 —— 太小会让轮询退化成「永远只用最快那个」，
#   太大则慢槽照样把时间烧光。★ 这是**门槛**，不是性能参数，所以写死并说明。
_慢槽让路倍数 = 1.5
_EWMA系数 = 0.3          # 耗时估计的平滑系数

# ★★★★★ R168（2026-10-27）：**失败的代价不一样，冷却不能一样。**（第 81 层）
#
# 【真数据 —— 第 50 次真跑 err 里的两类失败】
#   快错：`zai/glm-5.3` 17:41:38 → 17:41:40 ⇒ **1–2 秒**（503，立刻知道不行）
#   慢错：`ali/qwen3.8-max-0902` 17:48:34 → 17:50:40 ⇒ **110 秒**（504，等到网关超时）
#   同一个冷却 30 秒 ⇒ **慢错 30 秒后又回来再烧 110 秒**，
#   9 个槽里 7 个是这类 ⇒ 每个窗口先付 **约 250 秒** 试错税。
#
# 【为什么 R164 没修住这件事】
#   R164 只从**成功**里学耗时（失败那次的耗时被当成「不是干活的耗时」）。
#   ⇒ 死槽永远没有样本 ⇒ `_pick` 的让路逻辑（要求两边都有样本）**永远不触发**。
#   ★ 我又犯了「修了一侧，另一侧没修」：成功路径记了，失败路径没记。
#
# 【口径】
#   · 失败也记耗时，但**只用来分级**，不混进「干活耗时」的 EWMA；
#   · 一次失败就花了 ≥ `_慢失败秒`（默认 60 秒）⇒ 冷却 `_慢失败冷却秒`（默认 600 秒）；
#   · 快失败仍走原来的 `FAIL_THRESHOLD` + `COOLDOWN_SEC`（30 秒）。
#   ⇒ 一整趟之后，慢死槽全部沉下去 10 分钟，只剩真能干的槽在轮换。
_慢失败秒 = 60.0
_慢失败冷却秒 = 600.0


_状态落盘最小间隔 = 1.0     # 秒：节流，避免每次失败都写盘
_状态陈旧上限 = 900.0       # 秒：超过 15 分钟的旧状态不认（上游早变了）


def _熔断状态文件():
    """熔断健康表落在哪：`ORCH_STATE_DIR`，退化到 `TAIXU_DB` 所在目录。

    ★ 2026-10-30：**熔断记忆原本只活在进程内**。实测后果：服务器每重启一次，
      已证明坏掉的槽全部"复活"，第一个任务照样把每个死槽各试一遍（慢死槽 2s/次、
      网关 504 槽 110s/次）。这跟成本账当初"只在内存里"是同一个病 ——
      **同一缺陷形状出现两次，说明我没把它当一类问题在防。**
      口径与成本账一致：**文件是唯一真相源，内存只是缓存。**
    """
    import os
    from pathlib import Path as _P
    d = os.environ.get("ORCH_STATE_DIR")
    if not d:
        db = os.environ.get("TAIXU_DB")
        d = str(_P(db).parent) if db else ""
    return (_P(d) / "熔断状态.json") if d else None


def _读熔断状态() -> dict:
    import json
    f = _熔断状态文件()
    if not f or not f.exists():
        return {}
    try:
        if time.time() - f.stat().st_mtime > _状态陈旧上限:
            return {}
        return json.loads(f.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _写熔断状态(表: dict) -> None:
    import json, os
    f = _熔断状态文件()
    if not f:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        临 = f.with_suffix(f".{os.getpid()}.tmp")
        临.write_text(json.dumps(表, ensure_ascii=False), encoding="utf-8")
        os.replace(临, f)
    except OSError:
        pass



class Router:
    def __init__(self, name_model_pairs: list[tuple[str, object]],
                 刷新器=None, 刷新最小间隔: float | None = None) -> None:
        # (name, model, fail_count, down_until, **实测耗时**, **样本数**)
        # ★★★★★ R164（2026-10-27）：第 5/6 列是**实测耗时**（EWMA 秒）。
        #   理由见 `_pick` 上方那段注释 —— 「能应答」不等于「能按时干完」。
        self._slots = [
            [name, model, 0, 0.0, 0.0, 0] for name, model in name_model_pairs
        ]
        # ★ 跨重启记忆：进程内是缓存，文件才是真相源（与成本账同一口径）
        _旧 = _读熔断状态()
        for _sl in self._slots:
            _r = _旧.get(_sl[0]) if isinstance(_旧, dict) else None
            if not isinstance(_r, dict):
                continue
            try:
                _sl[2] = int(_r.get("fail") or 0)
                _sl[3] = float(_r.get("down_until") or 0.0)
                _sl[4] = float(_r.get("ewma") or 0.0)
                _sl[5] = int(_r.get("samples") or 0)
            except (TypeError, ValueError):
                continue
        self._rr = 0
        self._上次落盘 = 0.0
        self._forced_down: set[str] = set()  # 测试用：强制某频道不可用
        # ★★★★★ R72（2026-10-17）：**自动重连**。
        #   刷新器 = 一个无参可调用对象，返回**当前可用**的 [(槽名, 模型), …]。
        #   `None` ⇒ 不刷新（行为与旧版完全一致 ⇒ 老自检仍有效）。
        self._刷新器 = 刷新器
        self._刷新最小间隔 = (_刷新最小间隔 if 刷新最小间隔 is not None
                              else globals()["_刷新最小间隔"])
        self._上次刷新 = 0.0
        self.刷新次数 = 0        # 真的问了外面几次
        self.新接纳 = 0          # 累计"从池子外回到池子里"的槽数

    @property
    def _attempts_budget(self) -> int:
        """总尝试次数：随通道数缩放，且不低于 MAX_ATTEMPTS。

        ★ 固定常数会退化：通道数 = 常数时，轮询一圈正好每通道一次
          ⇒ 唯一活通道抖一下就全失败（2026-10-14 实测）。
          给每通道 `_ATTEMPTS_PER_CHANNEL` 次机会，死通道会被
          `FAIL_THRESHOLD` 快速摘掉，所以多出的预算不会真的浪费。
        """
        return max(MAX_ATTEMPTS, len(self._slots) * _ATTEMPTS_PER_CHANNEL)

    def _healthy(self, slot) -> bool:
        name = slot[0]
        if name in self._forced_down:
            return False
        return time.time() >= slot[3]

    def _落盘健康表(self):
        """把健康表写盘（节流 1 秒）。失败不影响调用：落盘是增强，不是链路。"""
        if time.time() - getattr(self, "_上次落盘", 0.0) < _状态落盘最小间隔:
            return
        self._上次落盘 = time.time()
        _写熔断状态({sl[0]: {"fail": sl[2], "down_until": sl[3],
                             "ewma": round(float(sl[4]), 3), "samples": sl[5]}
                     for sl in self._slots})

    def _pick(self):
        """选出轮询顺序上的下一健康槽；无健康槽返回 None。

        ★★★★★ R164（2026-10-27）：**「能应答」不等于「能按时干完」。**（第 79 层）

        【真数据 —— 同一份真实工程提示、不传 max_tokens、实测】
          aaa:mm/minimax-m3.1-flash            content=1626 字  **40.4 秒**  ★能干活
          aaa:deepseek-ai/deepseek-v4.1-flash  content= 995 字   69.8 秒
          aaa:ali/qwen3.8-max-0902             ——       **110.6 秒 ⇒ 504**
          dddddd:deepseek-ai/deepseek-v4.1-flash  ——     **110.6 秒 ⇒ 504**
        而网关（nginx）在约 110 秒返回 **504 Gateway Time-out**。
        ⇒ **慢槽不是「慢一点」，是「永远拿不到结果」。**
          它每次都会烧掉 110 秒再失败，然后冷却、再试、再失败。

        【为什么之前的选槽看不见这件事】
        `_pick` 是**纯轮询**：只问「健康吗」，不问「上一次花了多久」。
        而探活（`probe_*`）发的是 **1 token 的小请求**，2 秒就回 ⇒
        **探活永远全绿**，慢槽在探活眼里和快槽一模一样。
        ★ 这又是「手里那个量不是判据需要的那个量」：
          我手里有「能不能应答」，判据需要「**能不能在超时内干完**」。

        【口径（保留探索，不做成硬编码偏好）】
          · 仍按轮询走；**只有**在当前轮到的槽**已有实测**、
            且比已知最快的槽慢 1.5 倍以上时，才让给最快的那个；
          · 没有实测的槽（新槽/刚刷出来的）**照常轮到** ——
            否则新槽永远没机会证明自己（那就成了另一种偏见）；
          · 失败会走冷却，不会一直霸占。
        """
        n = len(self._slots)
        起点 = None
        for _ in range(n):
            self._rr = (self._rr + 1) % n
            slot = self._slots[self._rr]
            if self._healthy(slot):
                起点 = slot
                break
        if 起点 is None:
            return None
        # ★ R164：只在**两边都有实测**时才比较 —— 未知不判负。
        有测 = [s for s in self._slots if self._healthy(s) and len(s) > 5 and s[5] > 0]
        if len(有测) > 1 and len(起点) > 5 and 起点[5] > 0:
            最快 = min(有测, key=lambda s: s[4])
            if 最快 is not 起点 and 起点[4] > 最快[4] * _慢槽让路倍数:
                return 最快
        return 起点

    def _record(self, name: str, ok: bool, 耗时: float | None = None):
        """记一次结果。★ R164：成功时顺带记**实测耗时**（EWMA）。

        ★ 只在**成功**时记耗时：失败那次的耗时是「超时/立刻报错」，
          不是「这个槽干完活要多久」，混进来会把估计拉歪。
        """
        _对上了 = False
        for slot in self._slots:
            if slot[0] == name:
                _对上了 = True
                slot[2] = slot[2] + 1 if not ok else 0
                # ★★ 2026-10-31 真事故（我自己造的循环）：慢失败（≥60s）会把槽冷却 **600s**，
                #   而"唯一活槽"被冷却 = **必然全灭**：下一次 `call()` 直接 `[CIRCUIT_OPEN]`，
                #   于是"上游慢"被我自己的策略放大成"通道不可用"。
                #   口径：**最后一个可用槽不许被摘**——"没有槽"是保证失败，"慢槽"至少还可能成。
                现在 = time.time()
                还有 = [x for x in self._slots
                        if x[0] not in self._forced_down and x[3] <= 现在]
                if (not ok) and len(还有) <= 1 and slot in 还有:
                    if slot[2] >= FAIL_THRESHOLD:
                        logger.warning("[router] ★ 唯一可用槽 %s 失败，但**不摘**"
                                       "（摘了就全灭；宁可留着慢槽）", slot[0])
                        slot[2] = FAIL_THRESHOLD - 1
                elif slot[2] >= FAIL_THRESHOLD:
                    slot[3] = time.time() + COOLDOWN_SEC  # down + 冷却
                if ok and 耗时 is not None and len(slot) > 5:
                    _秒 = max(0.0, float(耗时))
                    slot[4] = _秒 if slot[5] == 0 else (1 - _EWMA系数) * slot[4] + _EWMA系数 * _秒
                    slot[5] += 1
                elif (not ok) and 耗时 is not None:
                    # ★ R168：**慢失败**（≥60 秒才失败的）冷却加长 ——
                    #   它不是「抖了一下」，是「这个槽在网关超时前干不完」。
                    #   30 秒后又放它回来，等于每 30 秒白烧 110 秒。
                    if max(0.0, float(耗时)) >= _慢失败秒:
                        现在2 = time.time()
                        还有2 = [x for x in self._slots
                                 if x[0] not in self._forced_down and x[3] <= 现在2]
                        if len(还有2) <= 1 and slot in 还有2:
                            logger.warning("[router] ★ 唯一可用槽 %s 慢失败，但**不冷却 600s**"
                                           "（否则整条通道被我自己的策略关掉）", slot[0])
                        else:
                            slot[3] = max(slot[3], time.time() + _慢失败冷却秒)
                self._落盘健康表()
                return

    def force_down(self, name: str) -> None:
        self._forced_down.add(name)

    def channel_names(self) -> list[str]:
        return [s[0] for s in self._slots]

    # ── ★ R72：自动重连（重新装载 + 新接纳）──────────────────────
    def 重新装载(self, name_model_pairs: list[tuple[str, object]]) -> bool:
        """换一套槽表，**保住老槽的失败计数与冷却**。返回是否有**新槽**进来。

        ★ 为什么要保住老槽的计数：新槽表通常与老槽表**大部分重合**
          （只是多了一个刚恢复的）。若把计数清零，那个一直失败的老槽
          会立刻被当成"健康"再试一遍 ⇒ 把刚建立的判断推翻、白烧一次。
        ★ 返回值只看**新名字**：没有新名字 ⇒ 池子没变 ⇒ 调用方不该
          因此重试（否则"刷新成功但什么也没变"会变成死循环）。
        """
        旧 = {s[0]: s for s in self._slots}
        新槽 = []
        for name, model in name_model_pairs:
            if name in 旧:
                _老 = 旧[name]
                新槽.append([name, model, _老[2], _老[3],
                             _老[4] if len(_老) > 4 else 0.0,
                             _老[5] if len(_老) > 5 else 0])
            else:
                新槽.append([name, model, 0, 0.0, 0.0, 0])
        self._slots = 新槽
        if self._rr >= len(self._slots):
            self._rr = 0
        新名 = set(旧) ^ set(s[0] for s in 新槽)
        真正新 = {s[0] for s in 新槽} - set(旧)
        if 真正新:
            self.新接纳 += len(真正新)
        return bool(真正新)

    def _试刷新(self) -> bool:
        """向"外面"要一次新槽表（自动重连）。返回**有没有新槽进来**。

        ★ 三重防护，缺一不可：
          ① 没有刷新器 ⇒ 直接 False（老行为）；
          ② **节流**：距上次刷新不足 `_刷新最小间隔` ⇒ 不刷（网络动作）；
          ③ 刷新器抛异常 ⇒ 记 warning 并返回 False
             —— ★ **不许让"探活失败"把整次调用搞崩**：探活是辅助，
               真调用才是判据（R18 的口径）。
        """
        if self._刷新器 is None:
            return False
        now = time.time()
        if now - self._上次刷新 < self._刷新最小间隔:
            return False
        self._上次刷新 = now
        self.刷新次数 += 1
        try:
            新对 = self._刷新器()
        except Exception as exc:                      # noqa: BLE001
            logger.warning("R72 刷新渠道失败（不影响本次调用）：%s: %s",
                           type(exc).__name__, exc)
            return False
        if not 新对:
            return False
        return self.重新装载(list(新对))

    async def call(self, msgs: list[Msg], patience: str = "auto"):
        """patience: "auto" = 无人值守（熬过上游抖动，R62 原意）；
                     "fast" = 交互式（全槽冷却 ⇒ 立刻报，不睡等）。

        ★★★ 2026-10-30 实测（同一 Router 连续三次调用，2 死 1 活）：
          任务1 4.1s 成功、任务2 4.1s 成功、任务3 **32.0s 失败** —— 其中 28 秒是
          "无健康槽 ⇒ 睡等到冷却到期"，睡完照样失败。
          【判据错在哪】R62 把"要不要等"写成了**路由器属性**，可它是**调用方属性**：
            · 无人值守（develop 修复环）没有人在等，熬 5 分钟是划算的；
            · 交互式（MCP 一次调用）有人在等，32 秒换一句"没有结论"是纯浪费 ——
              调用方真正需要的是"**现在不行，X 秒后再来**"这个可决策信息。
          ⇒ 同一机制服务两个主体 ⇒ 必须由调用方声明 patience，不许路由器替它决定。
        """
        """在健康频道间尝试，成功即返回响应，全失败抛最后异常。

        ★★★★★ R62（真无人值守暴露 · 2026-10-16）：**全槽冷却时不该立刻死。**
          【现象】实测渠道文件声明 4 渠道 6 模型，**只剩 1 个可用槽**
            （`dddddd|deepseek-ai/deepseek-v4.1-flash`），而上游频繁 504。
            单槽连失败 2 次（`FAIL_THRESHOLD`）⇒ 槽进 30 秒冷却 ⇒
            `_pick` 返回 None ⇒ **立刻抛错，整轮报错**。
          【为什么这是产品缺陷】无人值守的全部价值就是**熬过上游抖动**：
            上游 504 是**暂时的**（它自己都说 retry_after=120），
            而产品在 30 秒冷却期里**本来就会自己恢复**，却选择当场死掉。
            ★ 越是"只剩一个槽"，这条越致命 —— 恰恰是现实中最常见的情形。
          ⇒ 口径：**没有健康槽 ≠ 没有希望**。等到最早的冷却到期再试，
            但有**总等待上限**（不能无限卡死无人值守）。
          ★ 边界（不许死等的两种情形）：
            · 全部槽都被 `force_down`（测试/人为摘除）⇒ **没有未来**，立刻抛；
            · 总等待预算已耗尽 ⇒ 立刻抛（如实报，不假装还在努力）。

        ★★★★★ R72（2026-10-17）：**在"等"之前，先问一次外面变没变。**
          R62 解决了"全槽冷却时不该立刻死"，但它等的是**同一个槽表**。
          而真实世界里变的不只是"冷却到期"，还有**池子本身**：
            渠道恢复、凭据补上、用户改了渠道文件。
          ⇒ 顺序是：**先问外面（可能多了槽）→ 再等冷却（同一批槽）**。
            ★ 顺序不能反：先等 30 秒再发现"其实早就多了一个可用槽"
              就是把时间白扔了。
          ★ 刷新**有上限**（`_刷新轮数上限`）：否则"刷新成功但没带来健康槽"
            会变成死循环。超上限后仍走 R62 的等待逻辑。
        """
        last_err: Exception | None = None
        已等 = 0.0
        已刷新 = 0
        _试次 = 0
        等待预算 = 0.0 if str(patience) == "fast" else WAIT_BUDGET_SEC
        while True:
            用尽 = False
            for _ in range(self._attempts_budget):
                slot = self._pick()
                if slot is None:
                    # ── ★ R72 第一优先：问外面有没有新槽（自动重连）──
                    if 已刷新 < _刷新轮数上限 and self._试刷新():
                        已刷新 += 1
                        logger.warning("[router] ★ R72 刷新到新槽 ⇒ 重试"
                                       "（自动重连；池子现有 %d 槽）",
                                       len(self._slots))
                        break          # 重开内层循环（预算按新槽表重算）
                    # ── 算还有没有"未来"：只看**没被强制摘除**的槽 ──
                    候 = [s[3] for s in self._slots
                          if s[0] not in self._forced_down]
                    现在 = time.time()
                    if not 候:
                        # 全被强制摘除 ⇒ 等下去没有意义
                        raise last_err or TransientError("no healthy channel")
                    等到 = min(候)
                    余 = 等到 - 现在
                    剩 = 等待预算 - 已等
                    if 余 <= 0 or 剩 <= 0:
                        if str(patience) == "fast":
                            # 交互式：不睡。把"最早何时可再试"如实交出去（可决策）
                            raise TransientError(
                                f"[CIRCUIT_OPEN] 共试 {_试次} 次，全部通道冷却中，"
                                f"最早 {max(0.0, 余):.0f} 秒后可试；"
                                f"这是**基础设施暂不可用**，不是任务失败")
                        用尽 = True
                        break
                    睡 = min(余, 剩)
                    logger.warning(
                        "[router] 无健康槽 ⇒ 等 %.0f 秒（已等 %.0f/%.0f 秒）后重试"
                        "（上游抖动是暂时的，无人值守要熬过去）",
                        睡, 已等, WAIT_BUDGET_SEC)
                    await asyncio.sleep(睡)
                    已等 += 睡
                    continue
                _试次 += 1
                name, model = slot[0], slot[1]
                # ★ R164：实测这次调用花了多久（用于下次挑槽）
                _起164 = time.monotonic()
                try:
                    resp = await model(msgs)
                    self._record(name, True, time.monotonic() - _起164)
                    return resp
                except Exception as e:  # noqa: BLE001 - 频道级瞬态处理
                    # ★ R168：失败也带耗时 —— 快错和慢错要区别对待
                    self._record(name, False, time.monotonic() - _起164)
                    last_err = e
            else:
                # 内层循环**跑满预算**（不是 break 出来的）⇒ 最后问一次外面
                if 已刷新 < _刷新轮数上限 and self._试刷新():
                    已刷新 += 1
                    logger.warning("[router] ★ R72 预算耗尽但刷新到新槽 ⇒ 再试一轮")
                    continue
                用尽 = True
            if 用尽:
                raise last_err or TransientError(
                    f"no healthy channel（试了 {_试次} 次，已等 {已等:.0f} 秒，"
                    f"等待预算 {WAIT_BUDGET_SEC:.0f} 秒）")
            # 走到这里 = 内层被 `break` 打断（刷新到新槽）⇒ 重开一轮


# ════════════════════════════════════════════════════════════════
# 纯机器自检（不碰网络）
# ════════════════════════════════════════════════════════════════
def _selftest() -> int:
    import asyncio
    from pathlib import Path

    fails: list[str] = []
    # ★★★★★ R70c/R72（2026-10-17）：**分母必须机器数出来，不能声明。**
    #   本文件原来写的是 `N = 13` —— 一个**手写常数**。这一轮我加了 7 条 R72 齿、
    #   忘了改它，报表照样打「13/13 全绿」：**齿加了、分母没动**。
    #   ★ 这正是 `generic_goal` 那边 R70c 刚犯过的同一个病（R68 之后第二次）。
    #   ★ 危险方向是「**齿被删掉了、而报表照样全绿**」⇒ 无齿看起来像绿。
    #   ⇒ 分母改为运行时从自己的源码数；另配下限防成批删除。
    #   ★ 口径也改：不再写「M/M 全绿」—— 判据点个数 ≠ 执行了多少次检查，
    #     写「M/M」是在**声明**一个我数不准的东西。如实写判据点 + 失败数。
    _源码 = Path(__file__).read_text(encoding="utf-8", errors="replace")
    N = _源码.count("fails.append")
    _判据下限 = 25
    if N < _判据下限:
        fails.append(f"R72：判据点只有 {N} 个（下限 {_判据下限}）"
                     "—— 齿被成批删掉而报表照样绿")

    class 假模型:
        """按名字决定成败；记录被调用次数。"""

        def __init__(self, name: str, 行为):
            self.name = name
            self.行为 = 行为
            self.次数 = 0

        async def __call__(self, msgs):  # noqa: ANN001, ANN204
            self.次数 += 1
            r = self.行为(self.次数)
            if isinstance(r, Exception):
                raise r
            return f"{self.name}-ok"

    def 跑(pairs, 行为表):
        models = {}
        for name, _ in pairs:
            models[name] = 假模型(name, 行为表[name])
        r = Router([(n, models[n]) for n, _ in pairs])
        try:
            out = asyncio.run(r.call([]))
            err = None
        except Exception as exc:      # noqa: BLE001
            out, err = None, exc
        return r, models, out, err

    # ① 尝试预算随通道数缩放（这是本次修的核心）
    r4 = Router([("a", None), ("b", None), ("c", None), ("d", None)])
    if r4._attempts_budget < 8:
        fails.append(f"4 通道预算应≥8，实得 {r4._attempts_budget}")
    r1 = Router([("a", None)])
    if r1._attempts_budget != MAX_ATTEMPTS:
        fails.append(f"单通道预算应兜底为 {MAX_ATTEMPTS}，实得 {r1._attempts_budget}")

    # ② ★ 反例（本次真缺陷）：4 通道、3 死 1 活、活通道**头一次失败**后应仍能成功。
    #    固定预算 4 时这里**必失败**（轮询一圈正好每通道一次）。
    pairs = [("dead1", None), ("live", None), ("dead2", None), ("dead3", None)]
    表 = {
        "dead1": lambda n: RuntimeError("503 model_not_found"),
        "dead2": lambda n: RuntimeError("503 model_not_found"),
        "dead3": lambda n: RuntimeError("503 model_not_found"),
        # 活通道第 1 次失败（瞬时抖动），之后成功
        "live": lambda n: (RuntimeError("瞬态超时") if n == 1 else "ok"),
    }
    _, models, out, err = 跑(pairs, 表)
    if err is not None or out != "live-ok":
        fails.append(f"活通道抖动一次后应仍成功，实得 out={out!r} err={err!r}")

    # ③ ★ 反例：若预算退回固定 4，②必须**变红**（证明本门有判别力）
    _原 = globals()["MAX_ATTEMPTS"]
    try:
        globals()["MAX_ATTEMPTS"] = 1
        r_old = Router([("a", None), ("b", None), ("c", None), ("d", None)])
        # 预算 = max(1, 4*2) = 8，仍≥8 ⇒ 单靠 MAX_ATTEMPTS 改不动
        # 直接验「固定 4」这个旧口径：构造预算恰为 4 的场景
        class _固定4(Router):
            @property
            def _attempts_budget(self) -> int:
                return 4
        models2 = {n: 假模型(n, 表[n]) for n, _ in pairs}
        rr = _固定4([(n, models2[n]) for n, _ in pairs])
        try:
            asyncio.run(rr.call([]))
            err_old = None
        except Exception as exc:      # noqa: BLE001
            err_old = exc
        if err_old is None:
            fails.append("旧口径（固定 4）在本场景应失败 —— 本门因此无判别力")
    finally:
        globals()["MAX_ATTEMPTS"] = _原

    # ④ 全死通道 ⇒ 抛错（不得静默返回）
    pairs_dead = [("d1", None), ("d2", None)]
    表_dead = {n: (lambda n: RuntimeError("down")) for n, _ in pairs_dead}
    _, _, out4, err4 = 跑(pairs_dead, 表_dead)
    if err4 is None:
        fails.append("全死通道应抛错，实得成功")

    # ⑤ 连续失败达阈值 ⇒ 该通道被摘掉（不再被挑中）
    r5, m5, _, _ = 跑([("bad", None), ("good", None)],
                      {"bad": lambda n: RuntimeError("x"),
                       "good": lambda n: "ok"})
    if not r5._healthy([s for s in r5._slots if s[0] == "bad"][0]):
        fails.append("阈值未到时不应提前摘除")

    # ⑥ 成功一次即清零失败计数
    r6 = Router([("a", None)])
    r6._record("a", False)
    r6._record("a", True)
    if r6._slots[0][2] != 0:
        fails.append(f"成功后失败计数应清零，实得 {r6._slots[0][2]}")

    # ⑦ 强制下线生效
    r7 = Router([("a", None), ("b", None)])
    r7.force_down("a")
    r7._rr = 0
    if r7._pick()[0] == "a":
        fails.append("force_down 未生效")

    # ⑧ channel_names 保序（供回执登记）
    if Router([("x", None), ("y", None)]).channel_names() != ["x", "y"]:
        fails.append("channel_names 未保序")

    # ⑨ 单通道全失败 ⇒ 抛最后异常（不得吞成 None）
    _, _, out9, err9 = 跑([("only", None)],
                          {"only": lambda n: RuntimeError("唯一失败")})
    if err9 is None or "唯一失败" not in str(err9):
        fails.append(f"单通道失败应抛原异常，实得 {err9!r}")

    # ⑩ 预算与通道数单调（加通道不得减少预算）
    b3 = Router([("a", None)] * 3)._attempts_budget
    b6 = Router([("a", None)] * 6)._attempts_budget
    if b6 < b3:
        fails.append(f"预算应随通道数单调不减：{b3} → {b6}")

    # ═══ ★ R62：全槽冷却时不许当场死（无人值守的全部价值就是熬过上游抖动）═══
    #   ★★ 陷阱（我第一版就踩了）：自检被**直接运行**时（`python router.py`），
    #     本模块以 `__main__` 存在，而 `import agentscope_orchestrator.router`
    #     会**再加载一份** ⇒ patch 打在另一份上，被测代码读到的还是旧值
    #     ⇒ 齿**看起来在跑，实际没生效**（实测：等 30 秒而不是 0.3 秒）。
    #     ⇒ 必须 patch `globals()`（跑的是哪一份就改哪一份）。
    G = globals()
    旧冷, 旧预算 = G["COOLDOWN_SEC"], G["WAIT_BUDGET_SEC"]
    try:
        # ⑪ 单槽抖两次（模拟 504）进冷却 ⇒ **必须熬过去并成功**。
        #    ★ 越是"只剩一个槽"这条越致命 —— 那正是现实中最常见的情形。
        G["COOLDOWN_SEC"], G["WAIT_BUDGET_SEC"] = 0.2, 10.0
        r11, m11, out11, err11 = 跑(
            [("only", None)],
            {"only": lambda n: (RuntimeError("上游 504") if n <= 2 else "ok")})
        if err11 is not None or out11 is None:
            fails.append(f"R62：单槽抖动后应熬过去，实得 out={out11!r} err={err11!r}")
        if m11["only"].次数 < 3:
            fails.append(f"R62：没真的重试（只调了 {m11['only'].次数} 次）")

        # ⑫ 全被**强制摘除** ⇒ 没有未来 ⇒ **立刻**抛，不许死等
        r12 = Router([("a", None), ("b", None)])
        r12.force_down("a")
        r12.force_down("b")
        t12 = time.time()
        try:
            asyncio.run(r12.call([]))
            fails.append("R62：全强制摘除应抛错")
        except Exception:      # noqa: BLE001
            pass
        if time.time() - t12 > 1.0:
            fails.append(f"R62：全强制摘除应**立刻**抛，实等 {time.time() - t12:.1f} 秒")

        # ⑬ 等待**必须有上限**（否则无人值守会静默卡死）
        G["COOLDOWN_SEC"], G["WAIT_BUDGET_SEC"] = 100.0, 0.3
        t13 = time.time()
        _, _, out13, err13 = 跑([("only", None)],
                                {"only": lambda n: RuntimeError("一直失败")})
        if err13 is None:
            fails.append("R62：等待预算耗尽应抛错")
        if time.time() - t13 > 5.0:
            fails.append(f"R62：等待预算没生效，实等 {time.time() - t13:.1f} 秒"
                         "（无人值守会静默卡死）")
    finally:
        G["COOLDOWN_SEC"], G["WAIT_BUDGET_SEC"] = 旧冷, 旧预算

    # ══════════════════════════════════════════════════════════════
    # ★★★★★ R72：**自动重连** —— 池子变了要能用上（2026-10-17）
    # ══════════════════════════════════════════════════════════════
    # 真背景：`gateway.py` 自己的注释记着「`ggg` 23:01 是 200、09:11 变 503」
    #   ⇒ **可用性是时间的函数**。而原实现把"启动时探一次"的结论**冻结**成
    #   Router 槽表 ⇒ 整趟无人值守里，**恢复的渠道永远回不来**。
    # 这一组齿锁的就是"回得来"，以及"回不来时不许把机器搞坏"。
    try:
        G["COOLDOWN_SEC"], G["WAIT_BUDGET_SEC"] = 100.0, 0.5   # 冷却长、预算短

        # ⑭ 没有刷新器 ⇒ 行为与旧版**完全一致**（不许因为加了刷新就改变老路径）
        r14 = Router([("only", None)])
        if r14._试刷新() is not False:
            fails.append("R72：没有刷新器时应返回 False（老行为不许变）")
        if r14.刷新次数 != 0:
            fails.append(f"R72：没有刷新器却计了刷新次数 {r14.刷新次数}")

        # ⑮ ★★ 核心：唯一槽抖到冷却 ⇒ 刷新器带回**新槽** ⇒ 必须用上它并成功。
        #    ★ 这是本轮的**判别力**所在：没有 R72 时，这里只能等到冷却到期
        #      （100 秒）或预算耗尽（0.5 秒）⇒ 必失败。
        _新模型 = 假模型("新", lambda n: "ok")
        _问 = {"次": 0}

        def _刷新15():
            _问["次"] += 1
            return [("新", _新模型)]

        r15 = Router([("旧", None)], 刷新器=_刷新15, 刷新最小间隔=0.0)
        r15._slots[0][2] = FAIL_THRESHOLD          # 旧槽直接判死
        r15._slots[0][3] = time.time() + 100.0     # 且冷却 100 秒（等不起）
        r15._slots[0][1] = 假模型("旧", lambda n: RuntimeError("503"))
        t15 = time.time()
        try:
            out15 = asyncio.run(r15.call([]))
            err15 = None
        except Exception as exc:      # noqa: BLE001
            out15, err15 = None, exc
        if err15 is not None or out15 != "新-ok":
            fails.append(f"R72：刷新到新槽后应成功，实得 out={out15!r} err={err15!r}")
        if time.time() - t15 > 5.0:
            fails.append(f"R72：应先问外面再等冷却（实等 {time.time() - t15:.1f} 秒）"
                         "—— 顺序反了就是把时间白扔")
        if _问["次"] == 0:
            fails.append("R72：**根本没问外面**（刷新器没被调用 ⇒ 自动重连是死的）")

        # ⑯ 刷新器抛异常 ⇒ **不许把整次调用搞崩**（探活是辅助，真调用才是判据）
        def _炸():
            raise RuntimeError("探活服务自己挂了")

        r16 = Router([("only", None)], 刷新器=_炸, 刷新最小间隔=0.0)
        r16._slots[0][2] = FAIL_THRESHOLD
        r16._slots[0][3] = time.time() + 100.0
        r16._slots[0][1] = 假模型("only", lambda n: RuntimeError("503"))
        try:
            asyncio.run(r16.call([]))
            fails.append("R72：刷新器抛异常且无可用槽时应抛错（不该假装成功）")
        except Exception as exc:      # noqa: BLE001
            if "探活服务自己挂了" in str(exc):
                fails.append("R72：刷新器的异常**泄漏**成了调用结果"
                             "（探活失败不该冒充模型故障）")

        # ⑰ ★ 刷新**有上限**：刷新器一直成功但**不带来新槽** ⇒ 不许死循环
        _空刷 = {"次": 0}

        def _刷新17():
            _空刷["次"] += 1
            return [("only", None)]     # 同一个名字 ⇒ 没有新槽

        r17 = Router([("only", None)], 刷新器=_刷新17, 刷新最小间隔=0.0)
        r17._slots[0][2] = FAIL_THRESHOLD
        r17._slots[0][3] = time.time() + 100.0
        r17._slots[0][1] = 假模型("only", lambda n: RuntimeError("503"))
        t17 = time.time()
        try:
            asyncio.run(r17.call([]))
            fails.append("R72：无新槽且全冷却时应抛错")
        except Exception:      # noqa: BLE001
            pass
        if time.time() - t17 > 5.0:
            fails.append(f"R72：无新槽时**刷新成了死循环**（跑了 "
                         f"{time.time() - t17:.1f} 秒）")
        if _空刷["次"] > _刷新轮数上限:
            fails.append(f"R72：刷新次数 {_空刷['次']} 超过上限 {_刷新轮数上限}")

        # ⑱ **节流**：两次刷新间隔不足 ⇒ 不许再问外面（否则拿探活打上游）
        _节流 = {"次": 0}

        def _刷新18():
            _节流["次"] += 1
            return [("only", None)]

        r18 = Router([("only", None)], 刷新器=_刷新18, 刷新最小间隔=999.0)
        r18._试刷新()
        _第一次 = _节流["次"]
        r18._试刷新()
        r18._试刷新()
        if _节流["次"] != _第一次:
            fails.append(f"R72：节流没生效（问了 {_节流['次']} 次，"
                         f"第一次后应仍是 {_第一次}）")

        # ⑲ 重新装载必须**保住老槽的失败计数**（否则刚判死的槽立刻又"健康"）
        r19 = Router([("a", None), ("b", None)])
        r19._slots[0][2] = 2
        r19._slots[0][3] = time.time() + 100.0
        r19.重新装载([("a", None), ("b", None), ("c", None)])
        _a19 = [s for s in r19._slots if s[0] == "a"][0]
        if _a19[2] != 2 or _a19[3] < time.time() + 50:
            fails.append(f"R72：重新装载把老槽的失败计数/冷却清了"
                         f"（{_a19[2]} / 剩 {_a19[3] - time.time():.0f} 秒）")

        # ⑳ 反向齿：**没有新槽**时 `重新装载` 必须返回 False
        #    （否则"刷新成功但池子没变"会被当成有进展 ⇒ 死循环）
        r20 = Router([("a", None)])
        if r20.重新装载([("a", None)]) is not False:
            fails.append("R72：没有新槽时 `重新装载` 应返回 False")
        if r20.重新装载([("a", None), ("z", None)]) is not True:
            fails.append("R72：有真新槽时 `重新装载` 应返回 True")
    finally:
        G["COOLDOWN_SEC"], G["WAIT_BUDGET_SEC"] = 旧冷, 旧预算

    # ★★★★★ 【R164】「能应答」不等于「能按时干完」（第 79 层）
    #   真数据（同一份真实工程提示、不传 max_tokens、实测）：
    #     mm/minimax-m3.1-flash     content=1626 字   40.4 秒  ★能干活
    #     deepseek-ai/…-flash       content= 995 字   69.8 秒
    #     ali/qwen3.8-max-0902      ——           **110.6 秒 ⇒ 504**
    #   而网关在约 110 秒回 504 ⇒ **慢槽不是「慢一点」，是「永远拿不到结果」**。
    #   探活发的是 1 token 小请求（2 秒回）⇒ **探活永远全绿**，看不见这件事。
    # ★ 齿要测**结果**：给两个假模型（快 0.01s / 慢 0.2s），跑几轮后
    #   看它是不是**真的改用快槽**；同时新槽必须还有机会被试（防饿死）。
    try:
        import asyncio as _a164

        _计164 = {"快": 0, "慢": 0}

        class _快164:
            async def __call__(self, msgs):
                await _a164.sleep(0.01)
                _计164["快"] += 1
                return "ok"

        class _慢164:
            async def __call__(self, msgs):
                await _a164.sleep(0.2)
                _计164["慢"] += 1
                return "ok"

        _r164 = Router([("慢槽", _慢164()), ("快槽", _快164())])
        for _ in range(8):
            _a164.run(_r164.call(["x"]))
        _总164 = max(1, _计164["快"] + _计164["慢"])
        _快比 = _计164["快"] / _总164
        if _快比 < 0.6:
            fails.append(
                f"【R164】★★ 学过耗时之后仍频繁选慢槽"
                f"（快槽占比 {_快比:.0%}，快 {_计164['快']} / 慢 {_计164['慢']}）"
                " ⇒ 「能应答」仍被当成「能按时干完」；"
                "慢槽会把网关超时烧光并永远拿不到结果")
        # 耗时估计必须真的被记下来（不是只挑不改）
        _快槽164 = [s for s in _r164._slots if s[0] == "快槽"][0]
        if len(_快槽164) < 6 or _快槽164[5] == 0:
            fails.append("【R164】★ 没记录实测耗时（第 5/6 列为空）")
        elif not (0 < _快槽164[4] < 0.15):
            fails.append(f"【R164】★ 快槽耗时估计失真（{_快槽164[4]:.3f}s）")
        # ★ 边界：新槽不许被永久饿死（否则换成另一种偏见）
        _新164 = {"n": 0}

        class _新槽164:
            async def __call__(self, msgs):
                await _a164.sleep(0.01)
                _新164["n"] += 1
                return "ok"

        # ★ R168 齿：**慢失败必须冷却更久**。
        #   真数据：快错 1–2 秒（glm-5.3 17:41:38→40），慢错 110 秒
        #   （ali 17:48:34→17:50:40）；同一个 30 秒冷却 ⇒ 慢槽每 30 秒
        #   回来再烧 110 秒，每个窗口白付约 250 秒。
        #   ★ 齿测**结果**：给同一时刻、同一 fail_count 的两条记录，
        #     只有「耗时」不同 ⇒ 冷却必须不同，且慢的那条必须长得多。
        _G169 = globals()
        _旧169 = (_G169["_慢失败秒"], _G169["_慢失败冷却秒"],
                  _G169["COOLDOWN_SEC"])
        try:
            _G169["COOLDOWN_SEC"] = 30.0
            _G169["_慢失败秒"] = 60.0
            _G169["_慢失败冷却秒"] = 600.0
            _r169 = Router([("快错", None), ("慢错", None)])
            _现在 = time.time()
            _r169._record("快错", False, 1.5)     # 1.5 秒就失败
            _r169._record("慢错", False, 110.0)   # 烧了 110 秒才失败
            _快冷 = [s for s in _r169._slots if s[0] == "快错"][0][3] - _现在
            _慢冷 = [s for s in _r169._slots if s[0] == "慢错"][0][3] - _现在
            if _慢冷 < 500:
                fails.append(
                    f"【R168】★★ 慢失败（110 秒）只冷却 {_慢冷:.0f} 秒"
                    " ⇒ 30 秒后又回来再烧 110 秒，每个窗口白付试错税"
                    "（实测 9 槽 7 慢 ⇒ 约 250 秒/窗口）")
            if _快冷 > 100:
                fails.append(
                    f"【R168】★ 快失败（1.5 秒）也冷却 {_快冷:.0f} 秒 ⇒ "
                    "把「1 秒能问清的事」也拖成 10 分钟，过度惩罚")
            if _慢冷 <= _快冷:
                fails.append(
                    f"【R168】★★ 慢失败和快失败冷却一样（{_快冷:.0f} vs "
                    f"{_慢冷:.0f} 秒）⇒ 失败的**代价**没被看见")
        finally:
            (_G169["_慢失败秒"], _G169["_慢失败冷却秒"],
             _G169["COOLDOWN_SEC"]) = _旧169

        _r164.重新装载([("慢槽", _r164._slots[0][1]),
                        ("快槽", _r164._slots[1][1]),
                        ("新槽", _新槽164())])
        for _ in range(6):
            _a164.run(_r164.call(["x"]))
        if _新164["n"] == 0:
            fails.append("【R164】★★ 新槽一次都没被试 ⇒ 被永久饿死"
                         "（那是另一种偏见，会让刚恢复的槽永远回不来）")
    except Exception as _e164:
        fails.append(f"【R164】齿自身异常：{type(_e164).__name__}: {_e164}")
    for _f164 in [x for x in fails if "【R164】" in str(x)]:
        print(f"  ✗ router {_f164}", flush=True)

    if fails:
        print(f"router 自检：失败 {len(fails)} · 判据点 {N}（下限 {_判据下限}）"
              f"—— 红 {len(fails)}")
        for f_ in fails:
            print(f"  ✗ {f_}")
        return 1
    print(f"router 自检：全绿 · 判据点 {N}（下限 {_判据下限}）· 失败 0"
          "（含 ★R72 自动重连：刷新到新槽 / 无新槽不死循环 / 节流 / 保住老计数）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
