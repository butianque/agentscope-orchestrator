#!/usr/bin/env python
"""编排判据形式化规格的机器复核（tests/lean_spec.py）

为什么要有这一条：交付说明里剩下的那些"边界"，上一版还是**散文**。散文会被复制、
会被忘、会被下一个人写得更漂亮但更假。这个文件把它们钉在 Lean 定理上：
**编译不过 = 红；出现 sorry = 红；公理足迹超出白名单 = 红**。

它复核的对象是 `形式化验证/formal_proof/MyMeta/OrchJudge.lean`
（14 条定理：裁决四值 / 回滚恒等 / 选基域外 / 加门不放行 / 区分力单调 /
退出码门被骗 / 计数门抓住 / 差分门两向 / 指纹单射前提 / **语义等价不可判（Rice）**）。

项目路径：
  - 环境变量 `ORCH_LEAN_PROJECT` 指到 lean 项目根（含 lakefile.lean）优先；
  - 否则用档案里登记的绝对路径；
  - 都不存在 ⇒ **未验**（退出码 3），不假装通过。

跑法：python tests/lean_spec.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 配置

默认项目 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\形式化验证\formal_proof")
模块相对 = Path("MyMeta") / "OrchJudge.lean"

# 允许的公理足迹（Lean 标准三件套）。出现其它名字（尤其 sorryAx）一律红。
公理白名单 = {"propext", "Classical.choice", "Quot.sound"}

# 必须被 `#print axioms` 覆盖到的定理（少一条就是红：防止有人删掉断言）
必查定理 = [
    "verdict_exhaustive",
    "verdict_exclusive",
    "unverified_ne_ok",
    "restore_idempotent",
    "snapshot_after_restore",
    "pickBase_none",
    "pickBase_some",
    "addGate_not_looser",
    "discrimination_monotone",
    "exitGate_fooled",
    "countGate_catches",
    "gates_inequivalent",
    "agree_of_semEq",
    "notSemEq_of_disagree",
    "fingerprint_sound",
    "no_inj_counterexample",
    "ref_equivalence_undecidable",
]

通过: list[str] = []
失败: list[str] = []
未验: list[str] = []


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}" + (f"：{说明}" if 说明 else ""))


def 判未验(名: str, 说明: str = "") -> None:
    """★ 2026-11-04：**给「验不成」一个出口。**

    起因：CI 里这项反复红，原话是 `rc=1；error: permission denied (error code: 13)`，
    而**同一个脚本单独跑 6/6 全绿** —— 是并行会话的 `lake` 占着锁。
    老实现**已经认出**了这一类（`【归类】像环境撞锁/权限`），却**没有出口**：
    识别结果只写进说明文字，然后照样 `[FAIL]`。

    ⇒ 后果：`红` 这一格里混着「产物坏了」和「今天环境不好」两种东西，
      于是「**CI 红不红**」这个动作**不再可信** —— 看见红的人得先证伪一遍
      （本轮实测：为此多跑了一次 382 秒的完整 suite）。

    所以：真判不成的走 `失败`（红）；**认得出是环境、且现场三个证据齐全**的走这里
    （`未验`，`run_all` 按 `rc=3` 单列一档，**不算通过**）。
    ★ 口径：**「我没验成」≠「它坏了」** —— 但**两者都不等于「通过」**。
    """
    未验.append(名)
    print(f"  [未验] {名}" + (f"：{说明}" if 说明 else ""))


def _看得见别的lake() -> bool:
    """**看得见**别的 lake/lean 吗？

    ★ 名字只敢说到这一步。实测（2026-11-04，真流程）：
      当时 `Get-CimInstance` 明明看见 `lake.exe` pid=5904 与三个 `lean.exe`，
      而 `tasklist /FI "IMAGENAME eq lake.exe" /NH` 回的是
      `INFO: No tasks are running which match the specified criteria.`
      ⇒ **`tasklist` 只看得见本会话的进程。**
    ⇒ 所以「看不见」**永远不能**读成「没人在跑」，
      它只能当**旁证**，**不许当门** ——
      （我曾经拿它当门，代价是把一次真撞锁判成了红。）
    """
    import subprocess as _s
    for 名 in ("lake.exe", "lean.exe"):
        try:
            w = _s.run(["tasklist", "/FI", f"IMAGENAME eq {名}", "/NH"],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
            if 名 in (w.stdout or ""):
                return True
        except Exception:                      # noqa: BLE001
            continue
    return False


def 像撞锁(出: str, 项: Path, *, 进程探测=None) -> tuple[bool, str]:
    """这一次 Lean 失败**是不是环境撞锁**（而不是代码编不过）？

    ★ 三个条件**都要成立**才认，缺一个就照实报红 —— 因为**红是更强的断言**：
      宁可把撞锁报成红（人还能证伪），**也不许把真编译错误吞成「未验」**。
      **但门只能落在可复核的事实上**（文件、输出原文）。

        ① 输出里有 `permission denied`
        ② 现场真有 `.lock` 文件（仓内可查）
        ③ 被拒的路径在 **`.lake` 下**（锁冲突打的是 lake 自己的账本文件，
           不是你的源码文件）—— 路径从输出的 `file: …` 行里取

    ★→★ **这里原来还有第 ③ 条是「当时真有别的 lake 在跑」，已经被删掉。**
      原因是真流程当场证伪了它：`tasklist` 只看得见**本会话**的进程，
      于是它把一次**真撞锁**判成了红（详见 `_看得见别的lake` 的说明）。
      ⇒ 教训：**「看不见」不能当「没有」**；一道门要是靠"某个探测看不见"
        来把结论**降级**，那它降级的很可能是**对的结论**。
      进程探测现在只作**旁证**写进说明，不作门。

    ★ `进程探测` 可注入 —— 好让判据**能驱动它**（并证明它**不改变结论**）。
      「一段**驱动不了**的接线，等于没有接线。」
    """
    if "permission denied" not in (出 or "").lower():
        return False, "输出里没有 `permission denied` ⇒ 不像撞锁（照实报红）"
    证据 = ["输出含 `permission denied`"]
    try:
        import glob as _g
        import os as _o
        锁s = _g.glob(str(项 / ".lake" / "**" / "*.lock"), recursive=True)
    except Exception:                          # noqa: BLE001
        锁s = []
    if not 锁s:
        return False, "｜".join(证据 + ["现场**没有** `.lock` 文件 ⇒ 更像真权限/真错误（照实报红）"])
    证据.append(f"现场 .lock {len(锁s)} 个：{[_o.path.basename(x) for x in 锁s[:3]]}")
    m = re.search(r"file:\s*(\S+)", 出 or "")
    被拒 = m.group(1) if m else ""
    if not 被拒 or ".lake" not in 被拒.replace("\\", "/").lower():
        return False, "｜".join(证据 + [
            f"被拒的路径不在 `.lake` 下（{被拒 or '输出里没有 `file:` 行'}）"
            "⇒ 更像真权限问题/真错误（照实报红）"])
    证据.append(f"被拒的是 lake 自己的账本文件：{被拒}")
    try:
        看得见 = bool((进程探测 or _看得见别的lake)())
    except Exception:                          # noqa: BLE001
        看得见 = False
    证据.append(
        "旁证：探测**看得见**别的 lake/lean 在跑" if 看得见 else
        "旁证：探测当时**没看见**别的 lake/lean —— "
        "★ 看不见**不能**当「没人在跑」（`tasklist` 只看得见本会话），"
        "所以这条**不影响结论**")
    return True, "｜".join(证据)


def 退出码(失败: list[str], 未验: list[str]) -> int:
    """尾部那一段单独成函数 —— **好让判据直接驱动它**，
    而不是靠「读源码里有没有某个字符串」。

    0 = 全绿；**3 = 没有真红、但有没验成的**（`run_all` 按 `rc=3` 单列一档，不算通过）；
    1 = 有真红。★ **真红优先**：有一条真红就不许被「未验」稀释掉。
    """
    return 1 if 失败 else (3 if 未验 else 0)


def 找项目() -> Path | None:
    环 = os.environ.get("ORCH_LEAN_PROJECT", "").strip()
    if 环:
        p = Path(环)
        if (p / "lakefile.lean").is_file():
            return p
        print(f"  [WARN] ORCH_LEAN_PROJECT={环} 下没有 lakefile.lean ⇒ 忽略")
    if (默认项目 / "lakefile.lean").is_file():
        return 默认项目
    return None


def 找sorry(文: str) -> list[int]:
    """找真正的 `sorry`（排除注释里的提及与 `sorryAx` 之外的巧合）。"""
    # 先剥掉块注释/文档注释（`/-! ... -/`），否则"零 sorry"这句话自己会被判成 sorry
    文 = re.sub(r"/-.*?-/", "", 文, flags=re.S)
    行号: list[int] = []
    for i, 行 in enumerate(文.splitlines(), 1):
        # 去掉行注释后再找
        净 = 行.split("--")[0]
        if re.search(r"(?<![\w'.])sorry(?![\w'])", 净):
            行号.append(i)
    return 行号


def 解析公理(输出: str) -> dict[str, set[str]]:
    果: dict[str, set[str]] = {}
    for 行 in 输出.splitlines():
        行 = 行.strip().strip("'")
        m = re.match(r"OrchJudge\.(\w+)'? (?:depends on axioms: \[(.*)\]|does not depend on any axioms)", 行)
        if not m:
            continue
        名, 列 = m.group(1), m.group(2)
        果[名] = {x.strip() for x in 列.split(",")} if 列 else set()
    return 果


def main() -> int:
    print("lean_spec：复核形式化规格（编译 + 无 sorry + 公理足迹 + 定理全覆盖）")
    项 = 找项目()
    if 项 is None:
        print("  [未验] 找不到 Lean 项目（lakefile.lean）。设 ORCH_LEAN_PROJECT 指过去再跑。")
        return 3
    模 = 项 / 模块相对
    if not 模.is_file():
        print(f"  [FAIL] 规格模块不存在：{模}")
        return 1
    文 = 模.read_text(encoding="utf-8", errors="replace")

    判("① 规格模块存在", True, str(模.relative_to(项)))
    判("② 源码里没有 sorry", not 找sorry(文), f"命中行={找sorry(文)}")

    # 编真跑（真 Lean 内核）
    #
    # ★★ 2026-11-04：这段原来写 `subprocess.run(..., timeout=900)`，
    #   **看起来有超时、有出口，实际会把 CI 挂死**（本轮实测挂 26 分钟；
    #   把超时改成 180 之后**单跑仍超过 632 秒没返回**）。
    #
    #   原因不在超时值：`lake env lean` 是 lake 起 lean ⇒ 孙进程继承了
    #   stdout 管道；超时到了只杀 `lake`，`communicate()` 收输出时读不到
    #   EOF ⇒ **一直等**。所以"有 `except TimeoutExpired`"不等于"有出口"。
    #
    #   ⇒ 改用 `subproc_safe.跑()`：输出走临时文件（不靠 EOF）+ 超时
    #     `taskkill /T` 杀**整棵树**。判据 `tests/subproc_safe.py` 用
    #     真的"起孙进程并让它持管道"的夹具证明：老写法 8 秒后**还活着**，
    #     新写法 2 秒返回、孙进程也查不到了。
    #
    #   语义也顺过来：**超时不是红，是未验**（"今天没验成" ≠ "产物坏了"）；
    #   真编译错误照旧走 `唤.返回码 != 0` 那条路报红，一个字没动。
    from agentscope_orchestrator import subproc_safe as _SS
    唤 = _SS.跑(["lake", "env", "lean", str(模块相对)], cwd=str(项), timeout=180)
    if 唤.超时:
        现场 = "（现场探测失败）"
        try:
            现 = subprocess.run(["tasklist", "/FI", "IMAGENAME eq lake.exe", "/NH"],
                                capture_output=True, text=True, timeout=30).stdout or ""
            现场 = ("当时**有别的 `lake.exe` 在跑**" if "lake.exe" in 现
                    else "当时**没看到别的 `lake.exe`**（那就更可能不是撞锁，值得查）")
        except Exception:                                    # noqa: BLE001
            pass
        判未验("③ Lean 编译通过（0 error）",
              f"**180 秒没返回 —— 没验成，不是它坏了**。极可能是别的 lake/lean "
              f"占着锁在排队（这种形态不会像「被拒」那样打印 permission denied，"
              f"只能靠超时认出来）｜现场：{现场}")
        return 3
    出 = 唤.输出

    # ★★ 失败时**必须带上足够定位的信息**，否则这条红"看不清"。
    #
    #   实测（2026-11-03）：CI 里这项反复红，报的是
    #     `rc=1；首行错误=error: permission denied (error code: 13)`
    #   而**同一个脚本单独跑 6/6 全绿**。差别在环境，但原实现只报首行 **200 字**：
    #     · 不知道是哪个文件被拒（`permission denied` 后面那行才有文件名）
    #     · 不知道是锁冲突还是真权限
    #     · 不知道当时有没有别的 lake 在跑
    #   ⇒ 于是这个红**没法被诊断**，只能反复重跑碰运气 —— 那正是"测来测去兜圈子"。
    #
    #   ⇒ 口径：**一条会红的判据，必须让自己红得可定位**。
    #     红的输出里要包含"下一次能怎么办"所需要的全部现场。
    错因 = ""
    撞锁, 撞锁证 = False, ""
    if 唤.返回码 != 0:
        行s = [x for x in 出.splitlines() if x.strip()]
        错因 = "｜".join(行s[:6])[:900]              # 完整错误，不只首行
        撞锁, 撞锁证 = 像撞锁(出, 项)                # ★ 三个条件都成立才认（见 像撞锁 的说明）
        错因 += "｜【归类】" + 撞锁证

    编过 = 唤.返回码 == 0
    if 编过:
        判("③ Lean 编译通过（0 error）", True, "rc=0")
    elif 撞锁:
        # ★ 2026-11-04：**认出是环境 ⇒ 走「未验」，不许记成红。**
        #   老实现把这条识别只写进说明文字、然后照样 [FAIL] ⇒
        #   `红` 里混着「产物坏了」和「今天环境不好」，红就不再可信。
        判未验("③ Lean 编译通过（0 error）",
              f"rc={唤.返回码}；**没验成，不是它坏了** —— 别的 lake/lean 占着锁｜{错因}")
    else:
        判("③ Lean 编译通过（0 error）", False, f"rc={唤.返回码}；{错因}")

    公理 = 解析公理(出)
    if not 编过 and 撞锁:
        # ★ 编都没编成，`#print axioms` 当然什么都没印 ⇒ 这一项**同样是验不成**。
        #   不许拿「编译根本没发生过」去断言「定理覆盖不全」——那是**假红**。
        判未验("④ #print axioms 覆盖全部必查定理",
              "编译没跑成（撞锁）⇒ 拿不到 axioms 输出，**这一项没法判**"
              f"（不是「缺 {len(必查定理)} 条」）")
    else:
        判("④ #print axioms 覆盖全部必查定理", set(必查定理) <= set(公理),
          f"缺={sorted(set(必查定理) - set(公理))}")

    坏 = {k: sorted(v - 公理白名单) for k, v in 公理.items() if v - 公理白名单}
    判("⑤ 公理足迹不越界（无 sorryAx）", not 坏, f"越界={坏}" if 坏 else "仅 propext/choice/Quot.sound")

    # 关键定理必须真的存在（清理/误删时不许静默）
    缺声明 = [t for t in 必查定理 if not re.search(rf"^theorem {re.escape(t)}\b", 文, re.M)]
    判("⑥ 规格未缩水（声明的定理都在）", not 缺声明, f"缺={缺声明}")

    print()
    if 失败:
        print(f"lean_spec：{len(失败)} 项红 —— {失败}")
    if 未验:
        print(f"lean_spec：{len(未验)} 项**未验**（不算通过）—— {未验}；"
              "口径：**「我没验成」≠「它坏了」**（但**两者都不等于「通过」**）")
    if not 失败 and not 未验:
        print(f"lean_spec：{len(通过)}/{len(通过)} 全绿（编译通过 · 零 sorry · "
              f"公理足迹合规 · {len(必查定理)} 条定理全覆盖）")
    return 退出码(失败, 未验)


if __name__ == "__main__":
    sys.exit(main())



