# -*- coding: utf-8 -*-
"""派生门端到端探针（N7 剩余）：**盲区/判据 → 门 → 真判**这条链必须闭环且有区分力。

## 为什么需要它（这条链原来断在哪）

编排器的主张是「门集唯一来源是 `derive_gates(P)`，不由模型造」。但此前**只有各段各自被测**：
`derive_gates` 有没有派生（有）、`gate_spec.validate` 格式合不合（合）、`evaluate` 能不能跑（能）——
**没有一条判据把三段连起来问一句**：派生出来的门，**真的能判、且判得出坏东西吗？**

口径 388：判据测函数，交付测入口。这里有第二层同理 ——
**「每段都绿」不等于「连起来能判」**。所以本探针只做一件事：
拿一个真 `P`（判据），派生门，**在真文件上跑**，再**在坏文件上跑**，看它**变不变红**。

## 判据（逐条可回放）

1. **派生非空且来源唯一**：`derive_gates(P)` 出 2 条门，都带 `derive=True`，
   `assert_derived_gates` 无违例（门不许混入非 Δ(P) 的东西）。
2. **门规格自洽**：`gate_spec.validate` 返回 `None`、`gates_consistent` 为真。
3. **派生是确定的**：同一 `P` 连派两次，**逐字相同**（0-LLM、无随机）。
4. **否定样本必须搬到门（R95）**：`P` 里写了 `否定样本`，门里必须带着 ——
   原来这条搬运通道是**逐键白名单**，`否定样本` 不在白名单里会被**静默丢掉**。
5. **好产物全绿**：两条门在正确文件上都 `ok=True`。
6. **坏产物必须红**（区分力，口径 311）：
   · 删掉必须出现的标记 ⇒ `whole_reading` 门红（且 `compile` 门仍绿 —— 说明**是哪条**在报）；
   · 写坏语法 ⇒ `compile` 门红（`whole_reading` 仍绿）。
   ★ 两条门**各自单独**变红过一次，才算"分得开"，不是"一起红"。

## 诚实边界

· 这里用的是**自造的最小 fixture**，只证明链路闭环与门对**机械破坏**敏感；
  不证明"门集对某个真实目标充分"（那要 `sufficiency_bound` + 真任务）。
· 不调用模型、不写仓、只动临时目录（`tempfile`），跑完即删。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from agentscope_orchestrator import gate_derive, gate_spec
except Exception:  # noqa: BLE001 —— 未装 wheel 时退到源码树（判据测函数要能跑）
    _包 = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_包 / "src"))
    from agentscope_orchestrator import gate_derive, gate_spec


_好文 = (
    "MAGIC_TOKEN = 1\n"
    "\n"
    "def f():\n"
    "    return MAGIC_TOKEN\n"
)

_坏文_缺标记 = (
    "OTHER = 1\n"
    "\n"
    "def f():\n"
    "    return OTHER\n"
)

# ★ 语法坏文**保留 MAGIC_TOKEN** —— 这样「读全文」应绿、「能编译」应红，
#   两条门**各自单独**变红过一次，才证明它们判的是不同对象（不是一起红）。
_坏文_语法 = "MAGIC_TOKEN = 1\n\n\ndef f(:\n    return MAGIC_TOKEN\n"


def _P(解释器: str) -> dict:
    """一个真 `P`：读全文 + 能编译，两个 kind，各自带否定样本。"""
    return {
        "checks": [
            {
                "kind": "whole_reading",
                "name": "读全文",
                "file": "good.py",
                "mode": "contains",
                "must_contain": ["MAGIC_TOKEN", "def f"],
                "否定样本": "坏文_缺标记",
            },
            {
                "kind": "compile",
                "name": "能编译",
                "cwd": "{repo}",
                "cmd": [
                    解释器,
                    "-c",
                    "import ast,pathlib;ast.parse(pathlib.Path('good.py')"
                    ".read_text(encoding='utf-8'))",
                ],
            },
        ]
    }


def _填占位(门们: list[dict], 仓: pathlib.Path) -> list[dict]:
    """把执行侧负责填的模板占位符（`{repo}` / `{work}`）落成真路径。

    ★ 为什么要这一步（实测 2026-11-18）：`derive_gates` 派生的门里 `cwd` 是 **`"{repo}"` 字面量**，
      它**故意**不烘焙绝对路径（门要可搬、可跨实例复现）。执行侧负责替换 ——
      `bootstrap.py:1094` 的判据就是「装载后 `{repo}` 应当已被替换」。
      本探针没走装载流程，所以必须自己填；不填的后果是
      `[WinError 267] 目录名称无效 ⇒ 命令无法启动 ⇒ compile 门红`，
      而这条红**看起来像"产物坏了"，其实是"我没填占位符"** —— 正是口径 344 那类误读。
    """
    出 = json.loads(json.dumps(门们, ensure_ascii=False))
    工作 = 仓 / "_work"
    工作.mkdir(exist_ok=True)
    for g in 出:
        值 = g.get("cwd")
        if isinstance(值, str):
            g["cwd"] = 值.replace("{repo}", str(仓)).replace("{work}", str(工作))
    return 出


def main() -> int:
    失: list[str] = []
    py = sys.executable
    仓 = pathlib.Path(tempfile.mkdtemp(prefix="派生门端到端_"))
    目标 = 仓 / "good.py"
    try:
        目标.write_text(_好文, encoding="utf-8")
        P = _P(py)

        # ── ① 派生：非空 + 来源唯一 ──
        门们_未填 = gate_derive.derive_gates(P, str(仓))
        print(f"派生门 {len(门们_未填)} 条：{[g.get('name') for g in 门们_未填]}")
        占位 = [g.get("cwd") for g in 门们_未填 if isinstance(g.get("cwd"), str)]
        print(f"派生出的 cwd 占位符 = {占位}（应由执行侧填，不烘焙绝对路径）")
        if 门们_未填 and all(占位) and not any("{repo}" == c for c in 占位):
            失.append("派生的门 cwd 里没有 `{repo}` 占位符（不该烘焙绝对路径）")
        门们 = _填占位(门们_未填, 仓)
        if len(门们) != 2:
            失.append(f"派生门应为 2 条，实得 {len(门们)}")
        if not all(g.get("derive") is True for g in 门们):
            失.append("有门没带 derive=True（来源不明）")
        违例 = gate_derive.assert_derived_gates(门们, P)
        print(f"来源唯一违例 {len(违例)} 条")
        if 违例:
            失.append(f"assert_derived_gates 报违例：{违例[:3]}")

        # ── ② 门规格自洽 ──
        规格错 = gate_spec.validate(门们)
        一致, 不一致 = gate_derive.gates_consistent(门们)
        print(f"规格校验 = {规格错} · 一致性 = {一致}")
        if 规格错 is not None:
            失.append(f"gate_spec.validate 报错：{规格错}")
        if not 一致:
            失.append(f"gates_consistent 不通过：{不一致[:3]}")

        # ── ③ 派生确定 ──
        再 = gate_derive.derive_gates(P, str(仓))
        # ★ 比的是**同一形态**：两次都拿"未填占位符"的产物比（拿填过绝对路径的比会永远不等）
        if json.dumps(再, ensure_ascii=False, sort_keys=True) != json.dumps(
            门们_未填, ensure_ascii=False, sort_keys=True
        ):
            失.append("同一 P 连派两次结果不同（不确定 ⇒ 不是 0-LLM 闭式）")
        print("派生确定 = " + ("是" if 再 == 门们_未填 else "否"))

        # ── ④ 否定样本搬到门（R95） ──
        带否 = [g for g in 门们 if g.get("否定样本")]
        print(f"带否定样本的门 {len(带否)}/2")
        if len(带否) != 1:
            失.append("P 里写了 否定样本，门里没搬过去（R95 的静默丢键）")

        # ── ⑤ 好产物全绿 ──
        好, 审计好 = gate_spec.evaluate(门们, 目标, 仓)
        print("好产物：" + " · ".join(f"{k}={'绿' if v else '红'}" for k, v in 好.items()))
        if not all(好.values()):
            失.append(f"好产物上仍有红门：{ {k: v for k, v in 好.items() if not v} }")

        # ── ⑥ 坏产物必须红，且逐条分得开 ──
        目标.write_text(_坏文_缺标记, encoding="utf-8")
        缺, 审计缺 = gate_spec.evaluate(门们, 目标, 仓)
        目标.write_text(_坏文_语法, encoding="utf-8")
        歪, 审计歪 = gate_spec.evaluate(门们, 目标, 仓)
        print("坏文_缺标记：" + " · ".join(f"{k}={'绿' if v else '红'}" for k, v in 缺.items()))
        print("坏文_语法　：" + " · ".join(f"{k}={'绿' if v else '红'}" for k, v in 歪.items()))

        if 缺.get("读全文"):
            失.append("删掉必须出现的标记，whole_reading 门仍然绿（门没有区分力）")
        if not 缺.get("能编译"):
            失.append("坏文_缺标记 语法没坏，能编译门却红了（门判错了对象）")
        if 歪.get("能编译"):
            失.append("写坏语法，compile 门仍然绿（门没有区分力）")
        if not 歪.get("读全文"):
            失.append("坏文_语法 仍含标记，读全文门却红了（门判错了对象）")

        # 回放留痕（审计结构必须逐条可回放，口径 191）
        审计 = 审计好 + 审计缺 + 审计歪
        可回放 = all({"gate", "file", "ok", "cause"} <= set(a) for a in 审计)
        print(f"审计条目 {len(审计)} · 四字段齐全 = {可回放}")
        if not 可回放:
            失.append("审计条目缺 gate/file/ok/cause（不可回放）")
    finally:
        shutil.rmtree(仓, ignore_errors=True)

    if 失:
        for x in 失:
            print(f"✗ {x}")
        print("判定=不达标")
        return 1
    print("判定=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
