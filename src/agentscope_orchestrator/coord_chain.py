"""坐标链 —— 四方文档（快照/任务书/回执/状态索引）的坐标绑定与篡改检测。

## 为什么需要这个模块（不是"又一套日志"）

本项目有一条被反复验证的结论：**AI 做长线工程的根本失败模式，是它没有项目的判定基准，
于是用预训练里的统计规律去补 —— 补得越顺，错得越系统**。补的办法不能是"让模型记住"
（它记不住），只能是**让机器把项目自己的事实管起来，模型按需查**。

"项目事实"里最要紧的一样是：**这件事是谁、什么时候、在哪个文档上做的，能不能对上账。**
散落的回执、任务书、快照、状态索引四份文档，如果互不相认，那么：
  - 快照说了 A，回执做了 B，没人发现；
  - 任务书写在快照 v3 上，快照已经到 v7，没人发现；
  - 状态索引少了一轮，看起来像"那轮没做"，其实做了只是没归档。

⇒ 本模块给每个动作一个**坐标**，把四方文档绑成一条**链**，让"对不上"变成机器判据。

## 机制来源（不是我自己编的）

★ 本项目有条硬规矩：**`src/` 下的工具源码不许出现任何具体项目的专有名词。**
  那条判据在本模块上**判红过两次**，两次都是我把出处的**具体名称**写进了源码 docstring：
  第一次是内部形式化库的目录路径，第二次是那个库的中文说法本身。
  ⇒ 教训很清楚：**"引用出处"这件事，在通用工具源码里也必须用通用措辞说。**
  具体出处（哪个库、哪条定理、哪个文件）写在**回执**里 —— 那里才是项目语境。

形式化定义给出的机制名（本模块是它们的运行时落地）：

| 本模块 | 机制名 | 内容 |
|:--|:--|:--|
| `坐标` | 时空坐标单射 | 坐标由分量唯一确定 |
| `四方坐标.校验()` | 坐标链校验 | 四方完备 + 互异 ⇒ 通过 |
| 指纹 | 篡改必改坐标 | 内容不同 ⇒ 坐标必不同；坐标相同 ⇒ 内容相同 |
| `追加()` | 链确定工作流 | 链相等 ⇒ 各分量相等 |
| 干支计数 | 组合计数 | 全组合 120；匹配后 60 |

## ★ 一处必须写下来的**架构约束**（否则这套东西会用坏）

干支**全组合是 120 对**（10 天干 × 12 地支），**匹配后是 60**
⇒ **六十甲子只有 60 个，它以 60 为周期重复。**

这决定了一件要紧事：

> **天干地支不能单独当唯一坐标用。** 第 61 步的坐标会和第 1 步**长得一模一样**。

（本模块的判据里有一条专门测这个：步 1 与步 61 干支同为甲子，**但指纹不同**。）

上游设计里坐标写成 `{干, 支, 步, 前坐标}` —— 它其实已经处理了这件事：
**唯一性来自 `步` 与 `前`，天干地支只是可读的显示层。**
本模块照此实现，并且**把这条理由写进代码**，免得后来者把干支当主键用。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════
# 显示层：天干地支（60 甲子，**会重复**，只用于人读，不当主键）
# ══════════════════════════════════════════════════════════════════════════
天干 = "甲乙丙丁戊己庚辛壬癸"
地支 = "子丑寅卯辰巳午未申酉戌亥"

# 阴阳匹配：阳干配阳支、阴干配阴支 ⇒ 5×6+5×6 = 60（STC-02 已证）
_阳干 = set("甲丙戊庚壬")
_阳支 = set("子寅辰午申戌")


def _干支(步: int) -> tuple[str, str]:
    """把单调步数映射到 60 甲子（显示用）。**不保证唯一** —— 见模块 docstring。

    ★ 修过一个 off-by-one（2026-11-03）：原来写 `n = 步 % 60` 再取 `天干[n%10]`，
      于是**步 1 → 乙丑**（应为甲子）、**步 60 → 甲子**。
      这个错的小巧之处在于它**把必然的重复给盖住了**：
      步 1 与步 61 本该同干支（60 一循环，这是要人一眼看见的事实），
      错版反而让它们看起来不同 ⇒ 读的人不会意识到"60 步之后坐标显示会重复"。
      ⇒ 口径：**步数是 1-based，取模前先减 1。** 干支表按 `步-1` 索引才是甲子起。
    """
    n = (步 - 1) % 60
    return 天干[n % 10], 地支[n % 12]


@dataclass(frozen=True)
class 坐标:
    """一个时空坐标。**唯一性来自 `步` + `前`，不来自干支。**

    对齐形式化定义里的 `coord_projection_injective`：坐标由分量唯一确定；
    这里的分量是 `(步, 前, 种类, 引用)`，不是 `(天干, 地支)`。
    """

    步: int
    前: str            # 前一坐标的指纹（"" = 链首）
    种类: str          # 事件种类：回执/任务书/快照/状态索引/判据/盲区…
    引用: str = ""     # 该事件指向的产物（相对路径）
    时刻: str = ""
    指纹: str = ""

    @staticmethod
    def 造(步: int, 前: str, 种类: str, 引用: str = "", 时刻: str | None = None) -> "坐标":
        c = 坐标(步=步, 前=前, 种类=种类, 引用=引用,
                 时刻=时刻 or time.strftime("%Y-%m-%dT%H:%M:%S"))
        return 坐标(步=c.步, 前=c.前, 种类=c.种类, 引用=c.引用, 时刻=c.时刻, 指纹=_指纹(c))

    @property
    def 干支(self) -> tuple[str, str]:
        return _干支(self.步)

    def 显示(self) -> str:
        g, z = self.干支
        return f"{self.步:>6d}·{g}{z}"

    def 行(self) -> dict:
        return {"步": self.步, "前": self.前, "种类": self.种类,
                "引用": self.引用, "时刻": self.时刻, "指纹": self.指纹,
                "干": self.干支[0], "支": self.干支[1]}

    @staticmethod
    def 从行(r: dict) -> "坐标":
        return 坐标(步=int(r["步"]), 前=r.get("前", ""), 种类=r.get("种类", ""),
                    引用=r.get("引用", ""), 时刻=r.get("时刻", ""), 指纹=r.get("指纹", ""))


def _指纹(c: 坐标) -> str:
    """坐标指纹 = 全分量哈希。**改任何一个分量，指纹必变**（Lean `tamper_changes_coord`）。"""
    物 = json.dumps([c.步, c.前, c.种类, c.引用, c.时刻],
                    ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(物.encode("utf-8")).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════════════
# 四方文档坐标链（对齐形式化定义里的 `CoordChain`）
# ══════════════════════════════════════════════════════════════════════════
四方 = ("快照", "任务书", "回执", "状态索引")


@dataclass
class 四方坐标:
    """一轮工作里四方文档各自的坐标。对齐 Lean `CoordChain`。"""

    快照: str = ""
    任务书: str = ""
    回执: str = ""
    状态索引: str = ""

    def 校验(self) -> tuple[bool, list[str]]:
        """对齐 Lean `verifyChain`：四方**完备且互异**才算通过。

        Lean 原文：
            def verifyChain (c : CoordChain) : Bool :=
              c.snapshot ≠ c.taskbook ∧ c.snapshot ≠ c.receipt ∧ ...

        ★ 为什么是"互异"而不是"都存在"：四方文档如果坐标相同，说明**它们被当成了同一次事件**
          —— 那链就退化成一条，无法区分"先写快照再写回执"还是"根本没写快照只是复制了坐标"。
          互异是**可执行判据**；"都存在"不是（空串也能满足）。
        """
        缺 = [k for k in 四方 if not getattr(self, k)]
        重 = []
        for i, a in enumerate(四方):
            for b in 四方[i + 1:]:
                if getattr(self, a) and getattr(self, a) == getattr(self, b):
                    重.append(f"{a}≡{b}")
        问题 = []
        if 缺:
            问题.append(f"四方不全，缺：{'/'.join(缺)}")
        if 重:
            问题.append(f"坐标重复（单射被破坏）：{'/'.join(重)}")
        return (not 问题), 问题


# ══════════════════════════════════════════════════════════════════════════
# 链本体：只追加的坐标序列，落成 jsonl
# ══════════════════════════════════════════════════════════════════════════
链文件名 = "coord_chain.jsonl"


def _状态根() -> Path:
    """链存在**状态根**下，不在仓里（它是运行期事实，不是源）。

    ★ 与 project_kb 同一条口径：`ORCH_STATE_DIR` 优先，否则 `~/.orchestrator-state`。
      链进 git 会让每次跑都产生 diff 噪声，而它本身可以从回执全量重建（见 `重建()`）。
    """
    return Path(os.environ.get("ORCH_STATE_DIR") or (Path.home() / ".orchestrator-state"))


def 链路径() -> Path:
    return _状态根() / "chain" / 链文件名


def _读() -> list[坐标]:
    p = 链路径()
    if not p.is_file():
        return []
    出: list[坐标] = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                出.append(坐标.从行(json.loads(ln)))
            except Exception:            # noqa: BLE001 单行坏了不拖垮整链
                continue
    except OSError:
        return []
    return 出


def _写(链: list[坐标]) -> None:
    p = 链路径()
    p.parent.mkdir(parents=True, exist_ok=True)
    文 = "\n".join(json.dumps(c.行(), ensure_ascii=False) for c in 链)
    p.write_text(文 + ("\n" if 文 else ""), encoding="utf-8")


def 追加(种类: str, 引用: str = "", 时刻: str | None = None) -> 坐标:
    """往链尾追加一个坐标，返回它。

    ★ 并发必须安全：本函数的**读-算-写**不是原子的。调用方若并发（套件里确实会），
      两个进程可能拿到同一个 `步`。所以用 **OS 级文件锁**把追加做成临界区。

      ★ 为什么不用既有的 `window_lock`：它的语义是"按目标文件互斥，抢不到就**跳过**"
        （多窗开发要的是不阻塞），而这里要的是"**必须串行、抢不到就等**" ——
        跳过会导致**静默丢坐标**。语义不同，不能复用，宁可用十行自己写一个。
      ★ 也刻意**不用 `O_CREAT|O_EXCL` 抢锁文件**：那种锁在进程被强杀时会留下残锁，
        要额外实现陈旧检测（`window_lock` 就为此带了 `max_age`）。
        `msvcrt.locking` / `fcntl.flock` 由**操作系统在进程退出时自动释放**
        ⇒ 崩溃不留残锁，这才是这个场景该用的原语。
    """
    p = 链路径()
    p.parent.mkdir(parents=True, exist_ok=True)
    锁p = p.parent / (链文件名 + ".lock")
    with open(锁p, "a+b") as 锁f:
        _锁住(锁f)
        try:
            链 = _读()
            前 = 链[-1].指纹 if 链 else ""
            c = 坐标.造(步=len(链) + 1, 前=前, 种类=种类, 引用=引用, 时刻=时刻)
            链.append(c)
            _写(链)
            return c
        finally:
            _解锁(锁f)


def _锁住(f) -> None:
    """跨平台排他锁（阻塞直到拿到）。进程崩溃由 OS 自动释放，不留残锁。"""
    if os.name == "nt":
        import msvcrt
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.02)      # LK_LOCK 本身会重试 10 次×1 秒，这里兜底
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)


def _解锁(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def 首步() -> int:
    return 1


def 查(种类: str | None = None, 引用含: str | None = None) -> list[坐标]:
    """按需点查（**AI 简单用简单查**：不遍历大库，只查想要的）。"""
    链 = _读()
    if 种类:
        链 = [c for c in 链 if c.种类 == 种类]
    if 引用含:
        链 = [c for c in 链 if 引用含 in c.引用]
    return 链


# ══════════════════════════════════════════════════════════════════════════
# 校验：链的完整性（这是要变成判据门的东西）
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class 体检:
    条数: int = 0
    断裂: list[str] = field(default_factory=list)
    跳号: list[str] = field(default_factory=list)
    重复: list[str] = field(default_factory=list)
    指纹不符: list[str] = field(default_factory=list)

    @property
    def 通过(self) -> bool:
        return not (self.断裂 or self.跳号 or self.重复 or self.指纹不符)

    def 摘要(self) -> str:
        if self.通过:
            return f"链完整（{self.条数} 个坐标）"
        段 = []
        for 名, 项 in (("断裂", self.断裂), ("跳号", self.跳号),
                       ("重复", self.重复), ("指纹不符", self.指纹不符)):
            if 项:
                段.append(f"{名} {len(项)} 处：{'; '.join(项[:3])}")
        return f"链受损（{self.条数} 个坐标）—— " + " · ".join(段)


def 校验() -> 体检:
    """链体检。**四种损坏都要能报**（DS-05 TRIG-04 的触发条件）。

    对应 DS-05 §36.8 TRIG-04：
        连续坐标不连续 / 坐标跳跃 / 坐标重复 / 坐标缺失

    ★ 注意"断裂"与"跳号"是两件事，分开报：
        跳号 = `步` 不是 1,2,3,…（有洞）
        断裂 = 某个坐标的 `前` 不等于上一个坐标的 `指纹`（**链关系坏了**）
      前者说明中间被删了，后者说明被改过/拼接错了。修法不同，不能混成一类 ——
      这与本项目"判据要把责任分对"的一贯口径一致。
    """
    链 = _读()
    体 = 体检(条数=len(链))
    for i, c in enumerate(链):
        期 = i + 1
        if c.步 != 期:
            体.跳号.append(f"第{i}条：步={c.步}，期望 {期}")
        if i > 0 and c.前 != 链[i - 1].指纹:
            体.断裂.append(f"第{i}条（步{c.步}）：前={c.前 or '空'}，上一个指纹={链[i-1].指纹}")
        if c.指纹 and c.指纹 != _指纹(c):
            体.指纹不符.append(f"第{i}条（步{c.步}）：存盘指纹={c.指纹}，实算={_指纹(c)}")
    见 = {}
    for c in 链:
        if c.指纹 in 见:
            体.重复.append(f"坐标 {c.指纹} 出现在步 {见[c.指纹]} 与 {c.步}")
        见.setdefault(c.指纹, c.步)
    return 体


def 重建(来源目录, 过滤: str | None = "回执") -> int:
    """从回执目录全量重建链，返回重建的坐标数。

    ★ 为什么要它：**索引只是缓存**（与本项目 `project_kb` 同一条口径）。
      链文件删了/坏了，只要回执还在，就能重建。判断标准也从"链文件在不在"
      变成"链能不能从产物重算出来"。

    ★★ 2026-11-03 实测踩到并修掉的一件事：**第一版不过滤，把目录里所有 .md 都当回执。**
      在真实 `.audit_trail` 上跑，它有 **1682 份 .md，而只有 719 份名字里含「回执」**
      ⇒ 链一下子多了 963 个**根本不是回执的坐标**（报告、清单、临时件……）。

      这为什么严重（不是"多点噪声"而已）：
        坐标链的用途是**对账** —— 「这一轮的回执有没有归档、有没有缺项」。
        链里混进非回执，对账就会**拿无关文件去比**，结论必然错。
        而它错的方式是最坏的一种：**链是完整的、体检是绿的**，你看不出来。
      ⇒ 这就是本项目反复出现的那个病：**看起来接上了，其实接错了东西。**
      ⇒ 口径：**默认只收名字像回执的**（`过滤` 参数），并且**把过滤掉了多少条报出来**
        （见 `重建` 的返回值与 doctor 的输出）—— 静默丢弃和静默混入一样坏。

    过滤=None ⇒ 不过滤（调用方明确知道自己在干什么时用，例如专门的测试桶）。

    ★ 重建**不保证与旧链逐字相同**（原来的步序无法从产物完全还原：同一分钟可能有多份回执）。
      所以它产出的是**一条合法的新链**，而不是"恢复原链"。这一点如实写在返回值里 ——
      把"重算"说成"恢复"是自欺。
    """
    来源目录 = Path(来源目录)
    链: list[坐标] = []
    跳过 = 0
    if not 来源目录.is_dir():
        return 0
    候选: list[tuple[str, str]] = []
    for p in sorted(来源目录.glob("*.md")):
        if 过滤 and 过滤 not in p.name:
            跳过 += 1
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        候选.append((time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)), p.name))
    候选.sort()
    for ts, name in 候选:
        前 = 链[-1].指纹 if 链 else ""
        c = 坐标.造(步=len(链) + 1, 前=前, 种类="回执", 引用=name, 时刻=ts)
        链.append(c)
    _写(链)
    return len(链)


def 重建_明细(来源目录, 过滤: str | None = "回执") -> dict:
    """`重建` 的**如实版**：除了条数，还报"过滤掉了多少、按什么过滤"。

    ★ 为什么要有这个函数而不是让 `重建` 返回 dict：`重建` 的返回类型已被
      `doctor` 按 int 用。与 `ts_receipt.claim_batch` 那边同样的处理 ——
      **不改旧签名**（会破兼容），另给一个信息更全的入口。
    """
    src = Path(来源目录)
    总 = len(list(src.glob("*.md"))) if src.is_dir() else 0
    n = 重建(src, 过滤=过滤)
    return {"条数": n, "目录内_md总数": 总, "非回执被跳过": 总 - n,
            "过滤": 过滤 or "无（全部收）", "目录": str(src)}


def 统计() -> dict:
    """给 doctor / 只读控制台用：永不抛，坏了也返回可读结论。"""
    try:
        链 = _读()
        体 = 校验()
        种: dict = {}
        for c in 链:
            种[c.种类] = 种.get(c.种类, 0) + 1
        return {"条数": len(链), "按种类": 种, "链": str(链路径()),
                "完整": 体.通过, "体检": 体.摘要(),
                "首": 链[0].显示() if 链 else None,
                "尾": 链[-1].显示() if 链 else None}
    except Exception as e:                       # noqa: BLE001
        return {"条数": 0, "错误": f"{type(e).__name__}: {e}", "链": str(链路径())}
