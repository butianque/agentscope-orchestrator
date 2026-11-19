"""auto_goal：目标→任务序列 机器计划器（编排层工具位 ⑦）。

分工（与编排口径一致，遵『主 agent 不 Edit 引擎源码』纪律）：
  - 语义映射（自然语言目标 → 结构化载体规约）= 真模型产出 JSON，本模块只校验结构合法。
  - 分解计划（载体槽 → 规则升阶 → 接线 DAG → 任务序列）= 本模块纯机器计算（0-LLM）。
  - 每个子任务的实现内容（cmd / OLD-NEW）= 真模型产出；执行与裁决走 run_errand/develop 的
    机器判据门（逐段合取正确性），红则红滚+账本续跑。本模块绝不 Edit/Write 引擎源码。

机制锚定（全部来自活库已机证 Lean，非模型自造）：
  - 载体律（universal_carrier）：目标 = 有序槽（前提 pre / 约束 constraints / 判据 gates /
    验收 acceptance），前提 = 前序槽已发射。
  - 规则升阶（rule_ascent RA-01~03）：升阶维度 = 载体槽字段，规则空间 |R_n| = |R_1|·2ⁿ
    （ruleSpaceN (n+1) = δ(n)，逐槽涌生，不手写分解规则）。
  - 接线守恒（wire_plan / TS226_AllWiring）：任务 DAG 按接线法则（树状/网状/回环/全局）
    生成；全列守恒——每任务输入槽 ⊆ 前提 ∪ 前序任务输出，未知依赖拒绝。
  - 合取正确性（GeneralAlgorithm 组合律 + TS266aj）：目标闭合 = 各段子任务正确的合取，
    每段由机器判据门（GateSpec）单独裁决。

用法（自检）：
  venv\\Scripts\\python.exe src\\agentscope_orchestrator\\auto_goal.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

# ════════════════════════════════════════════════════════════════
# 全称载体：目标 = 有序槽（结构真值一等载体）
# ════════════════════════════════════════════════════════════════

_CARRIER_KEYS = ("pre", "constraints", "gates", "acceptance")


@dataclass
class Carrier:
    name: str
    pre: list[str] = field(default_factory=list)          # 前提槽（先序已发射）
    constraints: list[str] = field(default_factory=list)  # 约束槽（资源/规则约束）
    gates: list[dict] = field(default_factory=list)       # 判据槽（每任务 GateSpec）
    acceptance: list[str] = field(default_factory=list)   # 验收槽（目标闭合判据）
# ★ R178：门缺字段是**可纠正**的（回喂重规划即可），不是产品缺陷。
#   ★ 不另造异常类 —— `generic_goal.规约可纠正` 已是这一族的**唯一**基类；
#     再定义一个就是「两把尺子」（R117 同族）。
def _gate_wellformed(g: dict) -> str | None:
    """按**门类型**校验 GateSpec，返回 None 合法 / 否则拒因。

    ★ 修复（2026-10-12）：原实现一律要求 `type + cmd + cwd` 三个字段都存在。
      但 `gate_spec._TYPES` 里的**文本门**（grep_forbidden / grep_required /
      whole_reading）**天生没有 cmd**（它是读文件比对，不是跑命令）。
      ⇒ 真模型产出完全正确的文本门时，载体校验恒判"不完整"而拒，
        规划链在载体层就断掉（端到端实测：目标"创建 calc.py"产出的
        whole_reading 门被此校验拒死）。两个模块的契约不一致。
      现按 gate_spec 的真实字段契约分类型校验，与 evaluate 所需字段严格一致。"""
    if not isinstance(g, dict):
        return f"判据门不是 dict：{g!r}"
    t = g.get("type")
    if not t:
        return "判据门缺 type"
    # ★★★★★ R130b（同一趟真跑的第三层 · 最讽刺的一层）：
    #   **R100 的注释写着「下面的 `_门字段表` 既是 `_gate_wellformed` 的依据」，
    #     而 `_gate_wellformed` 根本没用这张表 —— 它是手写 if 链。**
    #
    #   实测（我把 `axiom_footprint` / `kernel_statement` 补进表之后）：
    #     `_gate_wellformed({"type":"axiom_footprint", …})`
    #       → 「未知门类型：'axiom_footprint'。合法类型只有这些：… axiom_footprint …」
    #   ⇒ **报错信息自己列出了 `axiom_footprint` 是合法的，却同时说它未知。**
    #   ★ 一句话：**表说它合法，if 链说它非法，而注释说两者是同一件事。**
    #   ★ 这是「同一个事实两处实现」的最纯粹形态（R117 同族），
    #     也是「声明了与被使用之间断了」的第 43 层。
    #
    #   ★ 修法：**未知类型判定改由表决定**（`t not in _门字段表`），
    #     不再依赖 if 链的 fallthrough。
    #   ★ 而具体字段检查**保持原样**（它比表更宽松：`whole_reading` 接受
    #     `in/file` 与 `must_contain/patterns/must_not_contain/mode` 多种别名，
    #     表里只写了最常用的一组）。★ **不能为了「统一」而收紧校验** ——
    #     那会把原本合法的门判死（R100 当初修的正是这件事）。
    if t not in _门字段表:
        _合 = 合法门类型()
        return (f"未知门类型：{t!r}。**合法类型只有这些**：{' | '.join(_合)}\n"
                f"★ 你想要的若是「跑一条命令看退出码」，那是 `compile` 门"
                f"（字段：cmd）。`command` / `shell` / `test` 都不是合法类型名。")
    if t == "axiom_footprint":
        if not g.get("cmd"):
            return "axiom_footprint 门缺 cmd"
        return None
    if t == "kernel_statement":
        if not (g.get("in") or g.get("file")):
            return "kernel_statement 门缺 in/file（被测文件）"
        if not (g.get("must_contain") or g.get("patterns")):
            return "kernel_statement 门缺 must_contain/patterns"
        return None
    if t == "compile":
        if not g.get("cmd"):
            return "compile 门缺 cmd"
        # cwd 可省（evaluate 会回落到 cwd_default）；给了就必须是 str
        if g.get("cwd") is not None and not isinstance(g.get("cwd"), str):
            return "compile 门 cwd 必须是字符串"
        return None
    if t in ("grep_forbidden", "grep_required"):
        if not g.get("in"):
            return f"{t} 门缺 in（被测文件）"
        if not (g.get("patterns") or g.get("pattern")):
            return f"{t} 门缺 pattern/patterns"
        return None
    if t == "scan_output":
        if not (g.get("src") or g.get("cmd")):
            return "scan_output 门缺 src/cmd（被测来源）"
        return None
    if t == "whole_reading":
        if not (g.get("in") or g.get("file")):
            return "whole_reading 门缺 in/file（被测文件）"
        if not (g.get("must_contain") or g.get("patterns")
                or g.get("must_not_contain") or g.get("mode")):
            return "whole_reading 门缺 must_contain/patterns/must_not_contain/mode"
        return None
    # ★★★★★ R255（2026-11-05 · 计划器真用不了最常用的门）：
    #   `_门字段表` 补 `test_counts` 等四类之后，**if 链没跟着补** ⇒
    #   真读数（我直接调 plan_tasks）：一份规约里带
    #     {"type": "test_counts", "cmd": [...]}
    #   会直接抛
    #     `AssertionError: R130：门类型 'test_counts' 通过了守卫却没被任何分支处理`
    #   ⇒ **计划器根本没法用"测试通过数"这个门** —— 而那正是工程验收最常用的判据。
    #   ★ 病与 R130b 同一句：「表说它合法，if 链说它非法」；反方向也发生了。
    #   ⇒ 口径：**表里每一类都要有分支**，且分支只查"执行器真的要什么"（不收紧）。
    if t in ("test_counts", "oracle"):
        if not g.get("cmd"):
            return f"{t} 门缺 cmd（要跑的测试/证明命令 argv）"
        if g.get("cwd") is not None and not isinstance(g.get("cwd"), str):
            return f"{t} 门 cwd 必须是字符串"
        return None
    if t == "differential":
        if not g.get("cmd"):
            return "differential 门缺 cmd（被测命令 argv）"
        if not g.get("ref"):
            return "differential 门缺 ref（参照命令 argv：黄金实现/旧实现/独立实现）"
        return None
    if t == "property":
        if not g.get("cmd"):
            return "property 门缺 cmd（被测命令 argv）"
        if not g.get("cases"):
            return ("property 门缺 cases（每个 case = 追加参数列表；"
                    "要求在**全部** case 上成立）—— 没有 case 就没有属性，等于恒真门")
        return None
    if t == "regression_parity":
        if not g.get("cmd"):
            return "regression_parity 门缺 cmd（当前版要跑的命令 argv）"
        if not g.get("baseline_cmd"):
            return ("regression_parity 门缺 baseline_cmd（基线版要跑的命令 argv）"
                    "—— 没有基线就只能无条件判绿，那不是回归判据")
        return None
    if t == "instance_agreement":
        if not g.get("cmd"):
            return "instance_agreement 门缺 cmd（要跑的测试命令 argv）"
        return None
    if t == "precondition":
        if not (g.get("cmd") or g.get("in") or g.get("file")):
            return "precondition 门缺 cmd/in/file（前提是什么都没写）"
        return None
    # ★★★★★ R130d：这里原本还有**第二份**「未知门类型」报错（R130 第一版加的），
    #   而函数开头的守卫（`if t not in _门字段表`）已经覆盖了全部非法类型
    #   ⇒ 这一段**不可达**，是死代码 + 第二把尺子。
    #   ★ 实测代价：装齿时注入 `if False:` 把守卫关掉，自检**仍然全绿** ——
    #     因为这一份接住了 `command`。**齿咬不住，是因为缺陷有两个出口。**
    #   ★ 这是我自己刚犯的「同一个事实两处实现」（R117 同族），
    #     修 R130 的时候又造了一个。
    #   ⇒ 删掉，让报错只有**一个出口**。
    #   ★ 若将来 `gate_spec` 新增了类型而这里忘了加进 `_门字段表`，
    #     会被 `门类型一致()` 那条齿抓住（它比对两边集合），不会静默漏掉。
    raise AssertionError(
        f"R130：门类型 {t!r} 通过了守卫却没被任何分支处理 "
        f"—— 说明 `_门字段表` 有它而 if 链没有（表与实现不一致）")


# ══════════════════════════════════════════════════════════════════════
# ★★★★★ R100：**门字段契约只能有一份**
# ══════════════════════════════════════════════════════════════════════
# 真跑抓出（2026-10-19 · 第 5 次端到端）：
#
#   主心骨给出了 4 条判据，**每条都带否定样本**，理由写得很清楚 ——
#   但它把 `grep_required` 门写成了：
#       {"type":"grep_required", "cmd":["grep","-rn","…","src"], "must_contain":[…]}
#   而机器要的是：
#       {"type":"grep_required", "in":"src/x.rs", "patterns":[…]}
#   ⇒ `_gate_wellformed` 判「缺 in（被测文件）」⇒ **整次重规划失败**。
#
#   ★ 主心骨不是不聪明，是**没人告诉它字段名**。它的提示里只写了
#     「type: compile|grep_required|…」，没写每种类型要哪些字段 ⇒ 它只能猜，
#     而它猜得**很像**（cmd + must_contain 在别处确实是合法写法）。
#
#   ★ 这与 R95 / R98 / R99 是同一个形状的第 14 层：
#     「**声明了**判据」与「判据**能被机器执行**」之间断了。
#
#   ★ 修法不是「在提示里再手写一份字段表」—— 那就是第二把尺子
#     （本库反复吃亏的形状）。⇒ 让**校验器自己**把契约定说出来：
#     下面的 `_门字段表` 既是 `_gate_wellformed` 的依据，
#     也是 `门契约说明()` 渲染给主心骨的**同一份数据**。
_门字段表: dict[str, list[tuple[str, bool, str]]] = {
    # type → [(字段, 是否必填, 说明)]
    "compile": [("cmd", True, "命令 argv 列表，如 [\"cargo\",\"check\"]"),
                ("cwd", False, "工作目录，默认 .")],
    "grep_required": [("in", True, "被测文件（相对路径，**不是目录**）"),
                      ("patterns", True, "必须出现的正则/串，列表")],
    "grep_forbidden": [("in", True, "被测文件（相对路径，**不是目录**）"),
                       ("patterns", True, "不得出现的正则/串，列表")],
    "scan_output": [("cmd", True, "要跑的命令 argv；或给 src 指定已有输出")],
    "whole_reading": [("in", True, "被测文件（相对路径）"),
                      ("must_contain", True, "必须包含的片段，列表")],
    # ★★★★★ R130（真跑抓出的第 43 层 · 2026-10-21 · 第二十六次端到端）：
    #   **这张表少了两行，而 `gate_spec._run_one` 是能执行它们的。**
    #
    #   真证据：重规划第 3 次失败，原文
    #     `判据槽 GateSpec 不完整（未知门类型：command）：
    #      {"name": "广域键行为自检通过", "type": "command",
    #       "cwd": "{repo}", "cmd": ["cargo","test","--lib", …]}`
    #   ⇒ 模型给了一个**不存在**的类型 `command`（它想要的其实是 compile 门），
    #     机器回了「未知门类型：command」，**但一个字都没说合法的是什么**。
    #   ⇒ 模型只能**盲猜**，连续 3 次都猜不中 ⇒ 诚实停机。
    #
    #   ★ 而 `门契约说明()`（R100 建的唯一事实源）**明明就在旁边**，
    #     报错分支却没有引用它。
    #   ★ 这是「声明了与被使用之间断了」的第 43 层，
    #     而且是这一族最讽刺的形态：**修复机制建好了，但没接到报错的地方。**
    #
    #   ★ 同时补上这两个**本来就支持**的类型 —— 它们不在表里 ⇒
    #     `_gate_wellformed` 会把合法门判成「未知类型」⇒ **第二把尺子**。
    #     （`gate_spec._run_one` 有 `if typ == "axiom_footprint"` /
    #       `if typ == "kernel_statement"`，而这里没有 ⇒ 同一件事两个答案。）
    "axiom_footprint": [("cmd", True, "要跑的命令 argv（输出里须有公理足迹行）")],
    "kernel_statement": [("in", True, "被测文件（相对路径）"),
                         ("must_contain", True, "内核陈述必须包含的片段，列表")],
    # ★ 2026-11-03：**补上四个既有缺口**。
    #   `门类型一致()` 早就在报「表里缺 ['differential','oracle','property','test_counts']」，
    #   但那四个门是 2026-11-01 加的，契约表没同步 ⇒ 合法门会被 `_gate_wellformed` 判
    #   「未知类型」。这正是本仓反复吃亏的「第二把尺子」：执行器认，校验器不认。
    #   守卫报了但没人看 —— 所以这次连同**顺手补上**，让守卫回到绿。
    "test_counts": [("cmd", True, "要跑的测试命令 argv"),
                    ("min_passed", False, "至少通过几条（默认 1）"),
                    ("max_skipped", False, "最多允许跳过几条（默认 0：skip 不算过）"),
                    ("max_failed", False, "最多允许失败几条（默认 0）"),
                    ("allow_unparsed", False, "输出里数不出计数时是否放行（默认 false ⇒ 未知）")],
    "oracle": [("cmd", True, "要跑的命令 argv（输出里须有证明/反例标记）"),
               ("witness_re", False, "自定义见证正则（默认认证明/反例两种标记）"),
               ("allow_unparsed", False, "判不出结论时是否放行（默认 false）")],
    "differential": [("cmd", True, "被测命令 argv"),
                     ("ref", True, "参照命令 argv（黄金实现/旧实现/独立实现）"),
                     ("same_output", False, "是否要求两版输出逐字相同（默认 true）"),
                     ("normalize", False, "比较前先做的规范化（默认按本仓口径）"),
                     ("cases", False, "逐个追加的参数列表（多组输入各比一次）"),
                     ("repeat", False, "每组重复次数（抓「跑两次两个结果」）"),
                     ("require", False, "必须出现的片段，列表")],
    "property": [("cmd", True, "被测命令 argv"),
                 ("cases", True, "每个 case = 追加参数列表；要求在**全部** case 上成立"),
                 ("repeat", False, "重复次数，且要求输出一致（确定性属性）"),
                 ("require", False, "必须出现的片段，列表"),
                 ("forbid", False, "不得出现的片段，列表")],
    # ★ 2026-11-03 新增三档：把"抖动/假故障/回归"从**人的推理**变成**可机判结论**。
    #   它们补的是同一类洞：判定权跑到了机器之外（跑进了人的直觉）。
    "regression_parity": [
        ("baseline_cmd", True, "**基线版**要跑的命令 argv（如旧版/上一 commit）"),
        ("cmd", True, "**当前版**要跑的同一条命令 argv"),
        ("baseline_cwd", False, "基线命令的工作目录，默认同 cwd"),
        ("cwd", False, "当前命令的工作目录，默认 ."),
        ("allow_baseline_red", False, "基线本身红时是否仍只比增量（默认 false ⇒ 判未知）"),
    ],
    "instance_agreement": [
        ("cmd", True, "要跑的测试命令 argv（会在**异构环境**里各跑一次）"),
        ("instances", False, "跑几个异构实例，默认 2（＜2 会被抬到 2）"),
        ("require_same_verdict", False, "是否要求各实例结论一致，默认 true"),
        ("cwd", False, "工作目录，默认 ."),
    ],
    "precondition": [
        ("in", True, "被测文件或目录（相对路径）—— 前提不成立时报的是**前提**，不是产物错"),
        ("needs_writable_dir", False, "true ⇒ 验「能不能在该路径下建目录再删」（临时目录前提）"),
        ("requires_env", False, "必须存在且非空的环境变量名，列表"),
        ("requires_path", False, "必须存在的路径，列表"),
    ],
}

# ★ R130：**合法类型集合只能有一份** —— 从 `gate_spec._run_one` 的
#   `if typ == "…"` 分支**运行时数出来**，再与 `_门字段表` 的键对齐。
#   ★ 为什么不在本文件里再手写一遍：那就是第三把尺子。
#   ★ 与 R68「分母必须从源码数出来」同一思路。
def 合法门类型() -> list[str]:
    """从 `gate_spec._run_one` 源码里**数出**真正能执行的类型（唯一来源）。"""
    import re as _re
    from pathlib import Path as _P
    try:
        _src = (_P(__file__).with_name("gate_spec.py")).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return sorted(_门字段表)
    return sorted(set(_re.findall(r'\btyp\s*==\s*"([a-z_]+)"', _src)))


def 门类型一致() -> tuple[bool, str]:
    """`_门字段表` 与 `gate_spec._run_one` 的合法类型集合是否一致。"""
    _真 = set(合法门类型())
    _表 = set(_门字段表)
    if _真 == _表:
        return True, ""
    _缺 = sorted(_真 - _表)      # 能执行但表里没有 ⇒ 合法门被判「未知类型」
    _多 = sorted(_表 - _真)      # 表里有但执行不了 ⇒ 表在骗人
    return False, f"表里缺 {_缺}（能执行却判未知）；表里多 {_多}（表在骗人）"


def 门契约说明() -> str:
    """把 `_门字段表` 渲染成给主心骨看的字段契约（**唯一事实源**）。

    ★ 由校验器同一份数据生成 ⇒ 改了校验就不会忘记改提示。
      这正是 R68 那条「分母必须从源码数出来」的同一思路。
    """
    段 = []
    for 类, 字段们 in _门字段表.items():
        必 = [f"`{n}`（{说}）" for n, 必填, 说 in 字段们 if 必填]
        可 = [f"`{n}`（{说}）" for n, 必填, 说 in 字段们 if not 必填]
        行 = f"· **{类}** 必填：{'、'.join(必)}"
        if 可:
            行 += f"；可选：{'、'.join(可)}"
        段.append(行)
    return "\n".join(段)


def carrier_from_spec(spec: dict) -> Carrier:
    """把真模型产出的结构化规约解析为合法载体。

    结构校验（纯机器，不猜语义）：四槽齐全、gates 每项是合法 GateSpec
    （**按门类型判其必需字段**，见 _gate_wellformed）。非法 → 抛 ValueError，
    拒绝生成计划（与 wire_plan『未知拒绝』一致）。"""
    if not isinstance(spec, dict) or "name" not in spec:
        raise ValueError("载体规约必须是 dict 且含 name")
    name = str(spec["name"]).strip()
    if not name:
        raise ValueError("name 为空")
    c = Carrier(name=name)
    for k in _CARRIER_KEYS:
        v = spec.get(k, [])
        if not isinstance(v, list):
            raise ValueError(f"槽 {k} 必须是 list")
        setattr(c, k, v)
    for g in c.gates:
        why = _gate_wellformed(g)
        if why:
            raise ValueError(
                f"判据槽 GateSpec 不完整（{why}）：{json.dumps(g, ensure_ascii=False)[:150]}")
    return c


def carrier_valid(c: Carrier) -> bool:
    """载体合法性（自检对应 universal_carrier 载体律）：槽有序、判据门可机算。"""
    try:
        carrier_from_spec(json.loads(json.dumps(
            {"name": c.name, "pre": c.pre, "constraints": c.constraints,
             "gates": c.gates, "acceptance": c.acceptance})))
        return True
    except ValueError:
        return False


# ════════════════════════════════════════════════════════════════
# 规则升阶：分解规则从载体槽涌生（RA-01~03），不手写
# ════════════════════════════════════════════════════════════════

def rule_ascent(c: Carrier) -> tuple[list[str], int]:
    """从载体槽涌生分解规则。

    升阶维度 = 载体槽字段（pre/constraints/gates/acceptance），每字段一条
    『子任务生成规则』；规则空间按 RA-03 指数翻倍 |R_n| = |R_1|·2ⁿ，
    返回 (规则名列表, 规则空间大小)。规则 = 槽→任务的生成律，非人写死。"""
    rules = [f"rule_{c.name}_from_{k}" for k in _CARRIER_KEYS]
    return rules, 1 << len(_CARRIER_KEYS)   # |R_1|=1, 翻倍 4 次 → 16


# ════════════════════════════════════════════════════════════════
# 接线计划：任务 DAG（TS226 接线法则 + 全列守恒）
# ════════════════════════════════════════════════════════════════

@dataclass
class Task:
    tid: str
    name: str
    kind: str   # pre_emit | constraint_check | errand | subgoal | acceptance
    gates: list[dict] = field(default_factory=list)   # 该任务的机器判据门（逐段合取）
    depends_on: list[str] = field(default_factory=list)


def acceptance_gates(c: Carrier) -> list[dict]:
    """★ 验收门 = 载体自身判据槽的**合取**（真值判据，非占位）。

    修复（2026-10-12）：原实现把验收门写成 `{"cmd": ["true"], "expect_exit": 0}` ——
    `/usr/bin/true` 恒返回 0，**验收恒绿**，与目标是否达成无关。这使"验收"退化为
    恒真占位：判据门（真检查）与验收门（假检查）脱钩，交付判定失去意义。

    现按「语义真值 = 系统读目标结构所得的判别式 T；真值等价 = T 的核」
    （该形式化结论由项目档案/产物库提供）落形：
      验收真值 T_accept(carrier) := ⋀_{g ∈ 判据槽} T_g(产物)
    即**验收不另立检查，而是判据槽的合取闭包**——真值由判据决定，不由名字决定。
    没有任何判据槽 ⇒ 无可验收（返回空，由上层拒绝，绝不退化成恒真）。

    实现上：把每条判据门原样复用（同一 GateSpec、同一 evaluate），
    额外要求它必须带 `name` 以便逐条回执——验收由此可逐门归因，而不是一个布尔。"""
    # ★★★★★ R270（2026-11-05 真读数）：**约束门不属于"目标达成"的合取**。
    #   真证据：约束门 `{type:compile, constraint:"不许改 tests/", cmd:[git diff --quiet …]}`
    #     被同时挂进验收：`accept::约束` / `accept::功能` 并列。
    #   合取上是"安全"的（任一门红仍判未达成，实测 `{'accept::约束': True, 'accept::功能': False}`
    #   ⇒ 全绿 False），**但它污染的是"这个读数是什么意思"**：
    #     ① 约束是**边界条件**（不许破坏），不是**达成证据**（做成了什么）——
    #        两类真值混在一个合取里，验收就不再回答"目标达成了吗"；
    #     ② 归因被稀释：验收红时分不清是"没做成"还是"踩了边界"，
    #        而这两种红的下一步动作完全不同（继续做 / 先回滚）。
    #   修法：约束门只当约束门的，验收合取只取**非约束**判据门。
    #   ★ 反向纪律：若全部判据门都带 constraint（没有真正的达成判据），
    #     验收合取为空 ⇒ 上层按"无可验收"拒绝，**不许因为空就判绿**。
    gs = [g for g in (c.gates or [])
          if isinstance(g, dict) and g.get("type") and not g.get("constraint")]
    out: list[dict] = []
    for i, g in enumerate(gs):
        ng = dict(g)
        ng["name"] = f"accept::{g.get('name') or g.get('type') or i}"
        out.append(ng)
    return out


def _约束门269(c: Carrier, con: str) -> list[dict]:
    """挑出**真属于这条约束**的门（R269）。

    ★ 为什么必须这么挑（真读数，2026-11-05 · case02 真目标）：
      原实现 `gates=list(c.gates or [])` 把**全部实现判据门**挂到每个
      `constraint_check` 任务上。于是一个叫「不许改 tests/」的任务身上
      挂着 `unittest discover` 和 `grep_required 月度退款额`。
      两重后果：① 约束绿 ⇒ 看起来目标达成（**替实现判达标**）；
                ② 约束任务依赖着全量判据 ⇒ 实现没做完时它必红，
                   而 errand 依赖它 ⇒ **实现任务死锁跑不起来**。

    ★ 判据：`GateSpec` 里显式写了 `constraint` 字段且与约束文本相关的门才算。
      没写 ⇒ 这条约束**没有可机判的门**（空表），由上游充分性检查拦 ——
      不许拿实现判据冒充约束判据。这条纪律与 R178「门缺字段可纠正」一致。
    """
    out: list[dict] = []
    for g in (c.gates or []):
        if not isinstance(g, dict):
            continue
        _c = g.get("constraint")
        if not _c:
            continue
        # 粗匹配即可：约束文本互相包含，或门声明的 constraint 就是这条约束的序号/全文
        if str(_c) == str(con) or str(_c) in str(con) or str(con) in str(_c):
            out.append(g)
    return out


def plan_to_submissions(plan: dict) -> list[dict]:
    """把机器计划翻成**可提交的任务序列**（R273）。

    ★★★ 为什么必须做这一步（真读数，2026-11-05）：
      `run_errand(unit_id, cmd, cwd, gates, name, priority, depends_on)`
      —— **没有 `tid`，也没有 `kind`**。
      而计划任务的字段是 `['tid', 'name', 'kind', 'gates', 'depends_on']`。
      ⇒ 「计划 → 执行」这一段**根本没有接线**：
        · `tid` 丢了 ⇒ 依赖链没法指回计划（调用方拿不到"计划内 id ↔ 真 job id"的映射，
          R254 注释里那句"计划内 id 不是真 job id ⇒ 调用方无法照它接线"就是这个坑）；
        · `kind` 丢了 ⇒ 执行期分不清"这是子目标汇总 / 约束检查 / 实现 / 验收"，
          R269（约束不许替实现判达标）、R271/R272（子目标不许是装饰）在**真跑时全部失效**。

    ⇒ 本函数输出**按 topo 序**的提交清单，每项给足执行侧要的字段，并且：
      · `depends_on_plan`：本任务的**计划内**前置 tid（供调用方换成真 job id）；
      · `will_run`：这一项**是否真的要起一个执行体**（`subgoal` / `acceptance` 是**汇总点**，
        它们不干活，只等，所以 `will_run=False`——**避免把汇总点当任务派出去**）；
      · `gates`：真判据门（子目标/验收带着各自的门，执行侧据此判）。

    ★ 诚实边界：本函数**只做翻译，不提交**。提交要真 job id，只有执行层能生成。
      「一段驱动不了的接线等于没有接线」——所以配套调用方式写在这里：
        按返回顺序逐项提交，把前序 `tid` 用真 job id 替换后填进 `depends_on`。
    """
    if not isinstance(plan, dict):
        return []
    tasks = {t.get("tid"): t for t in (plan.get("tasks") or []) if isinstance(t, dict)}
    topo = plan.get("topo") or list(tasks)
    # ★★★★★ R274b（2026-11-05 真端到端抓的）：**"不提交的汇总点"不能出现在下游依赖里。**
    #   真读数（按 submissions 清单驱动真仓）：
    #     pre_0  will_run=False        ← 永不提交
    #     task_0 will_run=True  deps=['pre_0']
    #   ⇒ 下游等一个**永远不会通过的依赖** ⇒ **实现任务永久阻塞**，整棵目标树开不了工。
    #   （我的驱动脚本里 `task_0` 报的是"上游未落地，停"，一开始我还以为那是
    #     "合理等待"——直到发现 pre_0 根本不打算运行。）
    #   口径：`depends_on` 只对**会落地的**依赖有意义。汇总点/发射点不落地，
    #     把它们的 tid 交给下游 = 交了一个永远不会 satisfied 的闸。
    #   ⇒ 翻译层就把它们剔掉，并在 `dropped_deps` 里如实交代（不许静默丢）。
    _不落地 = {tid for tid in topo
               if str((tasks.get(tid) or {}).get("kind") or "") not in
               ("errand", "constraint_check")}
    out: list[dict] = []
    for tid in topo:
        t = tasks.get(tid)
        if not t:
            continue
        kind = str(t.get("kind") or "")
        _原依赖 = [d for d in (t.get("depends_on") or [])]
        _真依赖 = [d for d in _原依赖 if d not in _不落地]
        out.append({
            "tid": tid,
            "kind": kind,
            "name": str(t.get("name") or tid),
            "gates": [g for g in (t.get("gates") or []) if isinstance(g, dict)],
            "depends_on_plan": _真依赖,
            "dropped_deps": [d for d in _原依赖 if d in _不落地],
            # 只有真干活的任务才起执行体；汇总点只等依赖
            "will_run": kind in ("errand", "constraint_check"),
            "reason": ("" if kind in ("errand", "constraint_check")
                       else f"{kind} 是汇总/发射点，不单独起执行体"),
        })
    return out


def wire_tasks(c: Carrier) -> list[Task]:
    """按载体槽序接线，产出任务 DAG（默认网状法则：实现任务直依前提）。

    - 每个前提槽 → pre_emit 任务（发射前提槽，供后续引用）
    - 每个约束槽 → constraint_check 任务（挂全部判据门，卡资源/规则）
    - 每条判据门 → errand 任务（实现子任务；gates = 该判据；依赖 = 前提 + 约束任务）
    - acceptance 槽 → acceptance 验收任务（依赖全部 errand；目标闭合点）

    守恒由 consume_ok 单独机验（wire_plan 全列守恒），本函数只产出 DAG 不假定守恒。"""
    tasks: list[Task] = []
    for i, p in enumerate(c.pre):
        tasks.append(Task(tid=f"pre_{i}", name=p, kind="pre_emit"))
    constraint_tids = []
    for i, con in enumerate(c.constraints):
        tid = f"con_{i}"
        constraint_tids.append(tid)
        # ★★★★★ R269（2026-11-05 真读数抓的）：**约束任务不许挂实现判据门**。
        #   真证据（case02 真目标，三条判据门）：
        #     con_0 (constraint_check): name='不许改 tests/'
        #       gates=[{'type':'compile','cmd':[...unittest...]},
        #              {'type':'grep_required','pattern':'月度退款额'}, …]
        #   —— 一个叫「不许改 tests/」的任务身上挂着**全部实现判据门**。
        #   后果有两层，都是坏的：
        #     ① 约束检查会**替实现判达标**：「约束任务绿了」看起来像「目标达成了」；
        #     ② 约束任务的 gate 是全量判据 ⇒ 实现没做完时它必红 ⇒
        #        下游 errand 依赖它 ⇒ **实现任务永远跑不起来**（死锁）。
        #   ⇒ 约束任务只在**真有一条对应约束的门**时才挂门；没有就不挂（空 = 无门可判，
        #     由上游充分性检查拦），**绝不拿实现判据冒充约束判据**。
        tasks.append(Task(tid=tid, name=con, kind="constraint_check",
                          gates=_约束门269(c, con)))
    errand_tids: list[str] = []
    pre_tids = [t.tid for t in tasks if t.kind == "pre_emit"]
    # ★★★★★ R271（2026-11-05 真读数）：**子目标（父子目标嵌套）此前根本不存在**。
    #   真读数：`auto_goal` 源码里 sub_goal/subgoal/子目标/children/parent 出现 **0 次**；
    #     把两条带 `goal:"子目标A"` 的门喂进去 ⇒ 计划器把 `goal` 字段**原样保留却忽略**，
    #     产出 [task_0 errand, task_1 errand, accept] —— 两条门被压平成**同级**实现任务。
    #   后果：真仓上的大目标（case02 的「实现订单取消流程」其实由 service/inventory/report
    #     三段组成）**无法表达"子目标整体达成"**。三段里任何一段做完，父目标可能被判完成，
    #     而"子目标 A 全做完"这件事在这个数据结构里**没有位置**。
    #   ⇒ 加一层：门可带 `goal` 字段（子目标名）。同名的门归到同一个子目标，
    #     每个子目标生成一个 `subgoal` 汇总任务（依赖它的全部 errand），
    #     acceptance 依赖**子目标**（没有子目标时才回退直接依赖 errand）。
    #     ★ 零回退：不带 `goal` 门的计划与原来**逐字节一致**（子目标层不发生）。
    _归271: dict[str, list[str]] = {}
    for i, g in enumerate(c.gates):
        tid = f"task_{i}"
        errand_tids.append(tid)
        _子 = str((g or {}).get("goal") or "").strip() if isinstance(g, dict) else ""
        tasks.append(Task(tid=tid, name=(_子 or f"实现#{i}"), kind="errand",
                          gates=[g], depends_on=pre_tids + constraint_tids))
        if _子:
            _归271.setdefault(_子, []).append(tid)
    _子目标tids: list[str] = []
    for _k, (_名, _成员) in enumerate(_归271.items()):
        _sid = f"subgoal_{_k}"
        _子目标tids.append(_sid)
        tasks.append(Task(tid=_sid, name=_名, kind="subgoal",
                          gates=[], depends_on=list(_成员)))
    if c.acceptance:
        # ★ 修复（2026-10-12）：验收门原为 `cmd: ["true"]` 恒真占位 → 改为
        #   载体判据槽的合取（真值判据）。无判据槽 ⇒ 验收为空 ⇒ 不生成假绿验收任务。
        acc_gates = acceptance_gates(c)
        if acc_gates:
            tasks.append(Task(
                tid="accept", name=c.acceptance[0], kind="acceptance",
                gates=acc_gates,
                # R271：有子目标层时验收依赖**子目标**（"每段都整体达成"），否则回退直接依赖 errand。
                depends_on=(_子目标tids + [t for t in errand_tids
                                           if not any(t in m for m in _归271.values())])
                if _子目标tids else errand_tids))
    return tasks


def consume_ok(c: Carrier, tasks: list[Task]) -> tuple[bool, str]:
    """全列守恒：每任务输入槽 ⊆ 前提 ∪ 前序任务输出（wire_plan 守恒机验）。

    tasks 按拓扑序（pre_emit → constraint → errand → acceptance）排列；
    以任务 tid 为发射单位追踪（依赖列引用前序任务 tid；前提槽 pre 视为已发射，
    供 name 级引用）。违例返回 (False, 未知槽名)——计划器拒发该 DAG。

    ★ 修复（2026-10-12）：**空任务表必须拒**。
      原实现在 tasks=[] 时循环体一次都不进、直接 `return True, ""` ⇒
      `plan_tasks({"name":"x"})` 返回 `conserved=True, tasks=0`（实测）。
      "没有任何任务"不等于"守恒"，而是"无可执行计划"，必须拒发。"""
    if not tasks:
        return False, ("空计划：无任何可执行任务（无可验收的判据落点）⇒ 拒发。"
                       "规约须至少给出一条判据门(gates)/前提(pre)/约束(constraints)。")
    emitted: set[str] = set()
    for t in tasks:
        for d in t.depends_on:
            if d not in emitted:
                return False, f"任务 {t.tid} 依赖未发射槽 {d}"
        emitted.add(t.tid)
    return True, ""


# ════════════════════════════════════════════════════════════════
# 主入口：载体 → 计划（纯机器）
# ════════════════════════════════════════════════════════════════

def topo_order(tasks: list) -> list:
    """★★★★★ 修复 R4-F9：DAG 的**可执行拓扑序**（Kahn）+ **环检测**。

    ★ 为什么必须有（原缺陷）：`plan_tasks` 的 `depends_on` 写的是**计划内 id**
      （`task_0`/`task_1`），不是真 job id ⇒ 调用方**无法照它接线**；
      且计划里**没有拓扑序** ⇒ 调用方可以乱序提交而**无人拦**。
      ⇒ 那份 DAG 是**展示品**，不是执行序。这正是"计划很好看、跑起来乱套"。

    ★ 本函数把 DAG 变成**可判定**的：
      · 返回一个**拓扑序**（依赖必在前）；
      · **有环 ⇒ 抛错**（不是"排不出来就算了"，是拒发）；
      · 调用方只需**照序提交**，并把前序任务的**真 job id** 填进 `depends_on`。
    """
    by_id = {t.tid: t for t in tasks}
    入度 = {t.tid: 0 for t in tasks}
    后继: dict = {t.tid: [] for t in tasks}
    for t in tasks:
        for d in (t.depends_on or []):
            if d not in by_id:
                raise ValueError(f"计划内依赖不存在：{t.tid} → {d}（拒发）")
            入度[t.tid] += 1
            后继[d].append(t.tid)
    # 稳定序：同层按 tid 排，保证**同一份规约出同一份计划**（可复现）
    就绪 = sorted([k for k, v in 入度.items() if v == 0])
    序: list = []
    while 就绪:
        k = 就绪.pop(0)
        序.append(k)
        for n in sorted(后继[k]):
            入度[n] -= 1
            if 入度[n] == 0:
                就绪.append(n)
                就绪.sort()
    if len(序) != len(tasks):
        环 = sorted(k for k, v in 入度.items() if v > 0)
        raise ValueError(f"计划 DAG 成环，拒发（环内：{环}）")
    return 序


def plan_tasks(spec: dict) -> dict:
    """计划器主入口：规约 JSON → {tasks, topo, conserved, rule_space, carrier}。

    纯机器计算：载体校验 → 规则升阶 → 接线 DAG → **拓扑序** → 守恒机验。
    语义映射（自然语言→规约）由真模型完成，本函数不猜语义。

    ★ R4-F9：多返回一个 `topo`（**可执行拓扑序**）。调用方照 `topo` 顺序提交，
      并把前序任务的**真 job id** 写进后序任务的 `depends_on`
      ⇒ DAG 从"展示品"变成"执行序"。
    """
    c = carrier_from_spec(spec)
    rules, space = rule_ascent(c)
    tasks = wire_tasks(c)
    ok, why = consume_ok(c, tasks)
    if not ok:
        raise ValueError(f"接线守恒违例：{why}")
    # ★ 拓扑序在**返回之前**算：成环 ⇒ 这里就抛，绝不发一份跑不起来的计划
    序 = topo_order(tasks)
    # ★★★★★ R254（2026-11-05 · 目标树纪律）：**父目标不许假完成**。
    #   口径：一个"目标"要能算**闭合**，必须满足三条，缺一条就不是完成：
    #     ① 它的 acceptance 任务真的在计划里（不是空表）；
    #     ② acceptance 任务的依赖**全部指向计划内的任务**（没有凭空依赖）；
    #     ③ acceptance 必须能**沿依赖链追到每一个 errand**（任何一条实现任务
    #        不在它的依赖闭包里 ⇒ 那个实现做没做都不影响"目标完成" ⇒ 可以假完成）。
    #   真证据形状（本机可直接复现）：一份 gates 有 3 条的计划，若 acceptance 只依赖
    #   其中 1 个 errand，另 2 个**做不做都判"目标闭合"** —— 这正是"父目标假完成"。
    #   ⇒ 不改任何已有键（老调用方零影响），只多两个：`closed` 与 `uncovered`。
    _闭 = _闭合检查(tasks, 序)
    return {
        "carrier": c.name,
        "rules": rules,
        "rule_space": space,
        "tasks": [t.__dict__ for t in tasks],
        "topo": 序,
        "conserved": True,
        "acceptance": c.acceptance,
        "closed": _闭["closed"],
        "uncovered": _闭["uncovered"],
    }


def _闭合检查(tasks: list, 序: list) -> dict:
    """父目标闭合机验：acceptance 必须沿依赖链覆盖**全部实现任务**。

    返回 `{"closed": bool, "uncovered": [tid...], "why": str}`。
    ★ 这是**机器算的**，不问模型"你觉得完成了吗"。
    """
    by_tid = {t.tid: t for t in tasks}
    acc = [t for t in tasks if t.kind == "acceptance"]
    if not acc:
        return {"closed": False, "uncovered": [],
                "why": "计划里没有 acceptance 任务 ⇒ 没有闭合点，不叫完成"}
    # 从 accept 出发，反向收集全部可达依赖
    可达: set = set()
    栈 = [a.tid for a in acc]
    while 栈:
        _t = 栈.pop()
        if _t in 可达:
            continue
        可达.add(_t)
        for _d in (by_tid.get(_t).depends_on if by_tid.get(_t) else []):
            栈.append(_d)
    未知 = sorted(_t for _t in 可达 if _t not in by_tid)
    实现 = [t.tid for t in tasks if t.kind == "errand"]
    未覆盖 = sorted(_t for _t in 实现 if _t not in 可达)
    if 未知:
        return {"closed": False, "uncovered": 未覆盖,
                "why": f"acceptance 依赖了计划外的任务：{未知}（凭空依赖）"}
    if 未覆盖:
        return {"closed": False, "uncovered": 未覆盖,
                "why": (f"{len(未覆盖)} 个实现任务不在 acceptance 的依赖闭包里 ⇒ "
                        f"它们做不做都判「目标完成」= **父目标可假完成**")}
    # ★★★★★ R272（2026-11-05 真读数）：**子目标必须真被验，否则它只是装饰**。
    #   真证据：上一轮加了 `subgoal` 汇总任务之后，
    #     把整个子目标层砍掉、验收直接依赖 errand ⇒ `closed` **照样是 True**
    #     （why: "acceptance 覆盖全部实现任务"）。
    #   原因：本函数只统计 `kind == "errand"`，`subgoal` 既不算实现任务也不被验。
    #   ⇒ 那一层"驱动不了"：计划里看着有分段，机器一个判据都不看它。
    #   修：① 每个子目标也必须在可达闭包里（否则它做不做都判完成）；
    #       ② 子目标的成员必须是**真实的 errand**，且成员不许"只属名字不属依赖"
    #          （成员必须真在本子目标的 depends_on 里）。
    子目标 = [t for t in tasks if t.kind == "subgoal"]
    for _s in 子目标:
        if _s.tid not in 可达:
            return {"closed": False, "uncovered": [_s.tid],
                    "why": (f"子目标 {_s.tid}（{_s.name}）不在 acceptance 的依赖闭包里 ⇒ "
                            "这一段做不做都判「目标完成」= 子目标是装饰")}
        _成员 = list(_s.depends_on or [])
        if not _成员:
            return {"closed": False, "uncovered": [_s.tid],
                    "why": f"子目标 {_s.tid}（{_s.name}）没有任何成员任务 ⇒ 空子目标恒真"}
        _假 = [_m for _m in _成员 if _m not in by_tid]
        if _假:
            return {"closed": False, "uncovered": [_s.tid],
                    "why": f"子目标 {_s.tid} 依赖了计划外的任务：{_假}（凭空成员）"}
        _非实现 = [_m for _m in _成员 if by_tid[_m].kind != "errand"]
        if _非实现:
            return {"closed": False, "uncovered": [_s.tid],
                    "why": (f"子目标 {_s.tid} 的成员里有非实现任务 {_非实现} ⇒ "
                            "子目标可以靠非实现任务凑绿")}
    return {"closed": True, "uncovered": [],
            "why": (f"acceptance 覆盖全部实现任务"
                    + (f"与 {len(子目标)} 个子目标" if 子目标 else ""))}


# ════════════════════════════════════════════════════════════════
# 自检门：把已机证定律移植成机器断言（0 sorry 的工程对应物）
# ════════════════════════════════════════════════════════════════

# ★★★★★ R130c（同一趟真跑的第四层）：**分母硬编码，而且 `len(int)` 会崩。**
#   实测：我加了 6 处 `fails.append` 之后，自检仍打「12/12 全绿」。
#   ⇒ 齿加了、分母没动 —— 这是 R68 / R70c / R72 已经犯过三次的同一个错，
#     第四次复发。
#   ★ 更糟的是失败分支写的是 `len(_SELF_GATES)` —— 而 `_SELF_GATES` 是 **int**
#     ⇒ `len(12)` 抛 `TypeError`。也就是说：**这条自检只在「全绿」时能跑通，
#     一旦真有失败，它会自己崩掉，而不是报出失败。**
#   ★ 一句话：**「能报绿」和「能报红」是两件事，而我只验过前者。**
#   ⇒ 修法同 gateway/router（R72）：分母**运行时从源码数**，另配下限防成批删除。
_SELF_GATES下限 = 12



# ══════════════════════════════════════════════════════════════════════════
# ★★★★ R535：**①→② 接线** —— 定计划**之前**先做真实盘点。
#
# 断点（实测）：`plan_tasks` 只吃规约 JSON，**不读任何盘点** ⇒
#   计划里引用的路径对不对，规划阶段无从知道（186 条判据一条都没判过）。
# 用户的原话是「任意项目启动**自动建知识图谱**，**根据**目标自动定计划」
#   ⇒ 两层都在，**中间没接**。这里把它接上，并且**引用不存在的路径就拒发**。
# ══════════════════════════════════════════════════════════════════════════

def _抽路径(tasks) -> list:
    r"""从计划的门里抽出**像路径的东西** → `[(tid, 原样, 相对路径)]`。

    ★ 只认两类（**故意保守**，口径 344：不许声称没验过的事）：
      · 门的 `cwd` —— 它**按定义**就是路径；
      · `cmd` 里**含路径分隔符**（`/` 或 `\`）的参数。
    ★ 不把裸 token 当路径：否则 `cargo --lib` / `pytest -q` 里的
      `lib`、`q` 会被误判成路径 ⇒ **误报比漏报更坏**（口径 311：造出不存在的东西）。
    """
    出 = []
    for t in (tasks or []):
        tid = t.get("tid", "?")
        for g in (t.get("gates") or []):
            if not isinstance(g, dict):
                continue
            cwd = g.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                出.append((tid, "cwd", cwd.strip()))
            for x in (g.get("cmd") or []):
                s = str(x)
                if "/" in s or "\\" in s:
                    出.append((tid, "cmd", s))
    return 出


def _核对计划路径(计划: dict, 根) -> dict:
    """计划引用的路径必须真在**索引里**或**盘上** —— 逐条对真值（R535）。

    ★ 为什么对**两处**：索引是快照（口径 367），盘是当下；
      索引里有而盘上没了 ⇒ 是**漂移**，要报出来但不算「凭空引用」。
    ★ `cwd` 常写成**相对根**的目录；`cmd` 里的路径可能相对 `cwd` ⇒
      两者都试，**试到哪个算哪个**，并把「怎么算出来的」记下来（口径 307）。
    """
    # ★★ 局部导入：本模块级**没有** `pathlib`（第三回栽在这上面 ——
    #   判 187 是 `pathlib`、判 188 是 `re`、这里是 `pathlib`）。
    #   ★ 口径：**别假设目标模块的模块级导入**；测试/接线代码自己带齐。
    import pathlib
    根 = pathlib.Path(根).resolve()
    索 = pathlib.Path(计划.get("盘点", {}).get("索引文件") or "")
    有索引 = set()
    if 索.is_file():
        import json as _j
        有索引 = {_j.loads(x)[0] for x in 索.read_text(encoding="utf-8").splitlines() if x}
    查了, 不存在, 限定 = [], [], {"索引里有但盘上没了": [], "在盘上但不在索引里": []}
    占位 = []
    for tid, 来自, 原样 in _抽路径(计划.get("tasks")):
        # ★★★★ R535b：**占位符不是路径** —— `{repo}` / `{root}` 这类模板变量
        #   在真计划里到处都是（`_selftest` 的夹具就是
        #   `{"cmd": ["make"], "cwd": "{repo}"}`）。
        #   ★ 不跳过的话，**每一份正常计划都会被误判成「引用了不存在的路径」而拒发**
        #     ⇒ 这不是「判得严」，这是**把工具用坏**（口径 311：误报比漏报更坏）。
        #   ⇒ 单列一栏 `占位符没查`，**明说没查**（口径 92：允许没查 + 必须说明）。
        if "{" in 原样 and "}" in 原样:
            占位.append({"tid": tid, "来自": 来自, "原样": 原样})
            continue
        正 = (根 / 原样.replace("\\", "/")).resolve()
        try:
            相 = 正.relative_to(根).as_posix()
        except ValueError:
            相 = None
        索引有 = 相 is not None and (相 in 有索引 or any(x.startswith(相 + "/") for x in 有索引))
        盘上有 = 正.exists() or 正.is_dir()
        查了.append({"tid": tid, "来自": 来自, "原样": 原样,
                   "在索引里": bool(索引有), "在盘上": bool(盘上有)})
        if not 索引有 and not 盘上有:
            不存在.append({"tid": tid, "来自": 来自, "原样": 原样})
        elif 索引有 and not 盘上有:
            限定["索引里有但盘上没了"].append(原样)
        elif 盘上有 and not 索引有:
            限定["在盘上但不在索引里"].append(原样)
    return {
        "查了": 查了,
        "不存在": 不存在,
        "占位符没查": 占位,
        "★占位符为什么不算": "`{repo}`/`{root}` 是**模板变量**，由执行侧填 —— "
                     "把它们当路径会**误杀每一份正常计划**（R535b 实测："
                     "`_selftest` 的夹具 `cwd` 就是 `{repo}`）。**明说没查**，不假装查过。",
        "★我只查了什么": "门的 `cwd` 与 `cmd` 里**含路径分隔符**的参数 —— "
                    "其余 token（命令名/开关）**不当路径**，否则 `cargo --lib` "
                    "会被误判（口径 344/311：误报比漏报更坏）",
        "★对了两处": "**索引**（快照，口径 367）与**盘上**（当下）都要对；"
                  "两者不一致的单独列在下面，不算「凭空引用」但**要留痕**",
        "★索引与盘不一致": 限定,
    }


def 带盘点定计划(根, spec: dict, 打印=print) -> dict:
    """①→② 接线：**先真实盘点，再定计划，再核对计划里引用的路径**（R535）。

    ★ 返回 `plan_tasks(spec)` 的全部键 **+ 两个新键**（老调用方零影响）：
      · `盘点`      —— `知库.盘点(根)`（秒级真实盘点）
      · `★路径核对` —— 计划引用的路径逐条对真值
    ★ **引用不存在的路径 ⇒ 当场抛** —— 与「成环 ⇒ 拒发」同款纪律：
      **绝不发一份跑不起来的计划**（口径：计划是要执行的，不是展示品）。
    """
    try:
        from . import 知库                       # 包内
    except ImportError:                          # noqa: PERF203
        import importlib
        知库 = importlib.import_module("agentscope_orchestrator.知库")
    盘 = 知库.盘点(根, 打印=打印)
    计划 = plan_tasks(spec)
    计划["盘点"] = 盘
    核 = _核对计划路径(计划, 根)
    计划["★路径核对"] = 核
    if 核["不存在"]:
        坏 = 核["不存在"][:4]
        raise ValueError(
            f"计划引用了**不存在的路径** ⇒ 拒发（{len(核['不存在'])} 条）：{坏}；"
            f"★ 这不是「计划不好」，是**这份计划跑不起来** —— "
            f"根是 `{盘['根']}`（文件 {盘['文件数']:,}），"
            f"索引在 `{盘['索引文件']}`（口径 358：我这边查过的地方都在核对里）")
    return 计划


def _selftest() -> int:
    from pathlib import Path as _P130
    fails = []
    # ★★★★★ R254：**父目标不许假完成** —— acceptance 必须覆盖全部实现任务。
    #   这道齿要能报红：人为把 accept 的依赖砍到只剩一个 errand，
    #   `closed` 必须变 False 并且**点名**哪些实现没被覆盖。
    try:
        _规254 = {"name": "闭合齿", "pre": ["原料"], "constraints": ["资源"],
                  "gates": [{"type": "compile", "cmd": ["make"], "cwd": "{repo}"},
                            {"type": "grep_required", "in": "src/a.py",
                             "patterns": ["def f"]}],
                  "acceptance": ["全绿"]}
        _r254 = plan_tasks(_规254)
        if not _r254.get("closed"):
            fails.append(f"【R254】正常计划的环闭合被判否：{_r254.get('uncovered')}")
        _实254 = [t["tid"] for t in _r254["tasks"] if t["kind"] == "errand"]
        if len(_实254) < 2:
            fails.append(f"【R254】齿的夹具只切出 {len(_实254)} 个实现任务 ⇒ 测不出假完成")
        else:
            class _T254:
                def __init__(s, d):
                    s.__dict__.update(d)
            _砍254 = [dict(t) for t in _r254["tasks"]]
            for _t in _砍254:
                if _t["kind"] == "acceptance":
                    _t["depends_on"] = _实254[:1]
            _闭254 = _闭合检查([_T254(d) for d in _砍254], _r254["topo"])
            if _闭254["closed"]:
                fails.append("【R254】accept 只依赖一个实现任务，闭合却没被否 ⇒ 父目标可假完成")
            if not _闭254["uncovered"]:
                fails.append("【R254】闭合被否了却**没点名**未覆盖的任务 ⇒ 红得没法修")
    except Exception as _e254:                    # noqa: BLE001
        fails.append(f"【R254】齿自身异常：{type(_e254).__name__}: {_e254}")
    # ★★★★★ R255：**表与 if 链必须逐类对齐** —— 每一类合法门都要有校验分支。
    #   真读数：补 `test_counts` 进 `_门字段表` 之后，if 链没跟上 ⇒ 计划器一带
    #   test_counts 门就直接抛「R130：通过了守卫却没被任何分支处理」⇒
    #   **最常用的"测试通过数"门计划器用不了**。
    #   这道齿的做法：把每一类的**最小合法门**都过一遍 `_gate_wellformed`，
    #   任何一类抛异常/报未知，就是"表与实现不一致"。
    _最小门255 = {
        "compile": {"cmd": ["make"]},
        "test_counts": {"cmd": ["make", "test"]},
        "oracle": {"cmd": ["prove"]},
        "differential": {"cmd": ["new"], "ref": ["old"]},
        "property": {"cmd": ["x"], "cases": [["1"]]},
        "regression_parity": {"cmd": ["new"], "baseline_cmd": ["old"]},
        "instance_agreement": {"cmd": ["pytest"]},
        "precondition": {"cmd": ["test", "-f", "a"]},
        "grep_required": {"in": "a.py", "patterns": ["def f"]},
        "grep_forbidden": {"in": "a.py", "patterns": ["TODO"]},
        "scan_output": {"cmd": ["run"]},
        "whole_reading": {"in": "a.py", "must_contain": ["x"]},
        "axiom_footprint": {"cmd": ["check"]},
        "kernel_statement": {"in": "a.lean", "must_contain": ["theorem"]},
    }
    for _t255, _g255 in _最小门255.items():
        try:
            _why255 = _gate_wellformed(dict(_g255, type=_t255))
        except Exception as _ex255:               # noqa: BLE001
            fails.append(f"【R255】门类型 {_t255} 的最小合法门**抛异常**"
                         f"（表与 if 链不一致）：{type(_ex255).__name__}: {_ex255}")
            continue
        if _why255:
            fails.append(f"【R255】门类型 {_t255} 的最小合法门被判非法：{_why255}")
    _缺255 = set(_门字段表) - set(_最小门255)
    if _缺255:
        fails.append(f"【R255】`_门字段表` 里有类型没进这张齿表：{sorted(_缺255)}")
    # ★★★★★ R256：**执行层的闭合闸**必须真的会拦（不然 `closed` 只是打印了一份真相）。
    try:
        from agentscope_orchestrator.taixu_mcp import accept_closed as _ac256
    except Exception:                              # noqa: BLE001  MCP 层不在时跳过（不冒充验过）
        _ac256 = None
    if _ac256 is not None:
        _好256, _ = _ac256(_r254)
        if not _好256:
            fails.append("【R256】正常计划被执行层闭合闸拒 ⇒ 会把对的目标也拦住")
        _坏256 = dict(_r254, closed=False, uncovered=["task_1"])
        _好256b, _why256 = _ac256(_坏256)
        if _好256b:
            fails.append("【R256】不闭合的计划被执行层放行 ⇒ 父目标仍可假完成")
        elif "task_1" not in str(_why256):
            fails.append(f"【R256】拦了但没点名未覆盖任务（红得没法修）：{_why256}")
        _旧256 = {k: v for k, v in _r254.items() if k not in ("closed", "uncovered")}
        if _ac256(_旧256)[0]:
            fails.append("【R256】没有 closed 字段的旧计划被放行 ⇒ 无法证明闭合却照跑")
    # 反向：该红的必须红（证明齿能报红，不是只求绿）
    for _t255b, _g255b in (("property", {"cmd": ["x"]}),
                           ("differential", {"cmd": ["x"]}),
                           ("regression_parity", {"cmd": ["x"]}),
                           ("test_counts", {})):
        if not _gate_wellformed(dict(_g255b, type=_t255b)):
            fails.append(f"【R255】{_t255b} 缺必填字段却没被判红 ⇒ 校验形同虚设")
    # ① 载体合法：完整规约通过；缺门/空名拒绝（carrier 律 + 未知拒绝）
    good = {"name": "g1", "pre": ["原料"], "constraints": ["资源"],
            "gates": [{"type": "compile", "cmd": ["make"], "cwd": ".", "expect_exit": 0}],
            "acceptance": ["闭合"]}
    if not carrier_valid(carrier_from_spec(good)):
        fails.append("carrier_valid 完整规约应通过")
    for bad, why in (({"name": ""}, "空名"), ({"name": "x", "gates": [{"type": "compile"}]},
                      "门缺 cmd")):
        try:
            carrier_from_spec(bad)
            fails.append(f"非法载体应拒绝：{why}")
        except ValueError:
            pass
    # ★★★★★ R130：**合法门类型只能有一份**，且报错必须给清单。
    #   真证据：模型给 `type: "command"`，机器回「未知门类型：command」，
    #   一个字都没说合法的是什么 ⇒ 模型盲猜 3 次 ⇒ 诚实停机。
    # ★★★★★ R274b：**"不提交的汇总点"不许出现在下游依赖里**。
    #   真读数（按 submissions 清单驱动真仓）：
    #     pre_0  will_run=False（永不提交）   task_0 will_run=True deps=['pre_0']
    #   ⇒ 下游等一个永远不会通过的依赖 ⇒ **实现任务永久阻塞**，整棵树开不了工。
    #   我第一眼还以为那是"合理等待"，直到发现 pre_0 根本不打算运行。
    try:
        _p274 = plan_tasks({"name": "r274b", "pre": ["仓可编译"],
                            "gates": [{"type": "compile", "cmd": ["x"], "cwd": ".",
                                       "goal": "段A"}],
                            "acceptance": ["全绿"]})
        _s274 = plan_to_submissions(_p274)
        _不落274 = {x["tid"] for x in _s274 if not x["will_run"]}
        for _x in _s274:
            if not _x["will_run"]:
                continue
            _坏274 = [d for d in _x["depends_on_plan"] if d in _不落274]
            if _坏274:
                fails.append(f"【R274b】{_x['tid']} 依赖了永不提交的 {_坏274} "
                             "⇒ 实现任务永久阻塞，整棵目标树开不了工")
        # 剔掉的依赖必须如实交代（不许静默丢）
        if any(_x["dropped_deps"] and not _x["depends_on_plan"] is None for _x in _s274):
            _有丢 = [x for x in _s274 if x["dropped_deps"]]
            if not _有丢:
                fails.append("【R274b】剔掉了依赖却没进 dropped_deps ⇒ 静默丢依赖")
        # 反向：真依赖（errand 之间的）不许被误剔
        _p274c = plan_tasks({"name": "r274c", "gates": [
            {"type": "compile", "cmd": ["x"], "cwd": ".", "goal": "段A"}],
            "acceptance": ["全绿"]})
        _s274c = {x["tid"]: x for x in plan_to_submissions(_p274c)}
        if "subgoal_0" in _s274c and _s274c["subgoal_0"]["will_run"]:
            fails.append("【R274b】汇总点被标成要跑")
    except Exception as _e274:                    # noqa: BLE001
        fails.append(f"【R274b】齿自身异常：{type(_e274).__name__}: {_e274}")
    # ★★★★★ R273：**「计划 → 执行」这一段必须真接得上**。
    #   真读数：run_errand(unit_id, cmd, cwd, gates, name, priority, depends_on)
    #   —— **没有 tid，也没有 kind**；而计划任务字段是
    #   ['tid','name','kind','gates','depends_on']。
    #   ⇒ tid 丢了：调用方拿不到"计划内 id ↔ 真 job id"的映射（R254 注释里
    #      "计划内 id 不是真 job id ⇒ 调用方无法照它接线"就是这个坑）；
    #     kind 丢了：执行期分不清子目标/约束/实现/验收 ⇒
    #      R269/R271/R272 三条纪律在**真跑时全部失效**。
    try:
        _p273 = plan_tasks({"name": "r273", "pre": ["仓可编译"],
                            "constraints": ["不许改 tests/"],
                            "gates": [{"type": "compile", "cmd": ["x"], "cwd": ".",
                                       "goal": "段A"},
                                      {"type": "grep_required", "in": "a.py",
                                       "patterns": ["x"], "goal": "段A"},
                                      {"type": "grep_required", "in": "b.py",
                                       "patterns": ["y"]}],
                            "acceptance": ["全绿"]})
        _s273 = plan_to_submissions(_p273)
        if not _s273:
            fails.append("【R273】计划翻不出提交序列 ⇒ 计划到执行没有接线")
        else:
            if [x["tid"] for x in _s273] != list(_p273.get("topo") or []):
                fails.append("【R273】提交序列没按 topo 序 ⇒ 执行序不受计划约束")
            _有273 = {x["tid"]: x for x in _s273}
            # 汇总点不许被派出去（否则拿 0 条门去跑）
            for _k in ("subgoal_0", "accept"):
                if _k in _有273 and _有273[_k]["will_run"]:
                    fails.append(f"【R273】{_k}（{_有273[_k]['kind']}）被标成要真跑 "
                                 "⇒ 汇总点会拿 0 条门去跑（空判据）")
            # 真干活的必须被派
            for _k in ("task_0", "task_1", "task_2", "con_0"):
                if _k in _有273 and not _有273[_k]["will_run"]:
                    fails.append(f"【R273】{_k}（{_有273[_k]['kind']}）没被标成要跑 "
                                 "⇒ 实现任务不会被执行")
            # 计划内依赖必须原样交出去（调用方才能换成真 job id）
            # ★ R274b 之后口径变了：`pre_0`（pre_emit）永不提交 ⇒ 必须剔出下游依赖，
            #   所以这里不能还要求 depends_on_plan 原样含 pre_0。
            #   **正确的是：真依赖（con_0，会提交的）必须在，永不提交的必须在 dropped_deps 里。**
            _t273 = _有273.get("task_0", {})
            if "con_0" not in (_t273.get("depends_on_plan") or []):
                fails.append("【R273】会提交的真依赖（con_0）没交出去 "
                             "⇒ 调用方无法照它接真 job id")
            if "pre_0" not in (_t273.get("dropped_deps") or []):
                fails.append("【R273】永不提交的 pre_0 既没进依赖也没进 dropped_deps "
                             "⇒ 依赖被静默丢了")
            # 每项都必须有 kind（丢了 kind 就等于丢了纪律）
            if any(not x.get("kind") for x in _s273):
                fails.append("【R273】提交项缺 kind ⇒ 执行期分不清任务种类，"
                             "R269/R271/R272 在真跑时全失效")
    except Exception as _e273:                    # noqa: BLE001
        fails.append(f"【R273】齿自身异常：{type(_e273).__name__}: {_e273}")
    # ★★★★★ R272：**子目标必须真被验，否则它只是装饰**。
    #   真读数：上一轮加了 subgoal 汇总任务后，验收**绕过**子目标直接依赖 errand
    #   ⇒ 旧 `_闭合检查` 照样 closed=True（它只统计 kind=="errand"）。
    #   口径：一段驱动不了的接线等于没有接线 —— 计划里看着有分段，
    #   机器一个判据都不看它，那这一段做不做都判完成。
    try:
        _c272 = Carrier(name="r272", gates=[
            {"type": "compile", "cmd": ["x"], "cwd": ".", "goal": "段A"},
            {"type": "grep_required", "in": "a.py", "patterns": ["x"], "goal": "段A"},
            {"type": "grep_required", "in": "b.py", "patterns": ["y"], "goal": "段B"}],
            acceptance=["全绿"])
        _ts272 = [dict(tid=t.tid, name=t.name, kind=t.kind,
                       gates=list(t.gates), depends_on=list(t.depends_on))
                  for t in wire_tasks(_c272)]

        class _T272:
            def __init__(s, d):
                s.__dict__.update(d)

        _序272 = [t["tid"] for t in _ts272]
        _好272 = _闭合检查([_T272(d) for d in _ts272], _序272)
        if not _好272.get("closed"):
            fails.append(f"【R272】正常的子目标计划被判不闭合（{_好272.get('why')}）⇒ 齿夹具错")
        # 架空虚设：验收绕过子目标、直接依赖 errand
        _绕272 = [dict(d) for d in _ts272]
        for _d in _绕272:
            if _d["kind"] == "acceptance":
                _d["depends_on"] = [x["tid"] for x in _ts272 if x["kind"] == "errand"]
        _闭272 = _闭合检查([_T272(d) for d in _绕272], _序272)
        if _闭272.get("closed"):
            fails.append("【R272】验收绕过子目标直接依赖 errand 却判闭合 "
                         "⇒ 子目标层是装饰（做不做都判完成）")
        elif "subgoal" not in str(_闭272.get("why") or ""):
            fails.append(f"【R272】红得没点名是哪个子目标（{_闭272.get('why')}）⇒ 红得没法修")
        # 空子目标不许恒真
        _空272 = [dict(d) for d in _ts272]
        for _d in _空272:
            if _d["kind"] == "subgoal" and _d["tid"] == "subgoal_0":
                _d["depends_on"] = []
        _闭272b = _闭合检查([_T272(d) for d in _空272], _序272)
        if _闭272b.get("closed"):
            fails.append("【R272】空子目标被判闭合 ⇒ 恒真空段")
    except Exception as _e272:                    # noqa: BLE001
        fails.append(f"【R272】齿自身异常：{type(_e272).__name__}: {_e272}")
    # ★★★★★ R271：**子目标（父子目标嵌套）必须有位置**。
    #   真读数：`auto_goal` 里 sub_goal/subgoal/子目标/children/parent 出现 **0 次**；
    #   两条带 `goal:"子目标A"` 的门喂进去 ⇒ 被压平成**同级** errand，
    #   "子目标 A 整体达成"这个判断在数据结构里没有位置。
    #   真仓的大目标（三段：取消流程 / 库存释放 / 报表）就是这样表达不出来的。
    try:
        _c271 = Carrier(name="r271", pre=["仓可编译"],
                        gates=[{"type": "compile", "cmd": ["x"], "cwd": ".", "goal": "取消流程"},
                               {"type": "grep_required", "in": "a.py", "patterns": ["x"],
                                "goal": "取消流程"},
                               {"type": "grep_required", "in": "b.py", "patterns": ["y"],
                                "goal": "报表"},
                               {"type": "compile", "cmd": ["z"], "cwd": "."}],
                        acceptance=["全绿"])
        _ts271 = wire_tasks(_c271)
        _sg271 = [t for t in _ts271 if t.kind == "subgoal"]
        if len(_sg271) != 2:
            fails.append(f"【R271】子目标层没生成（应 2 个，实得 {len(_sg271)}）"
                         " ⇒ 大目标各段的整体达成无法表达")
        else:
            _d271 = {t.tid: list(t.depends_on) for t in _sg271}
            if sorted(map(tuple, _d271.values()))[0] != ("task_0", "task_1") and \
                    sorted(_d271.values(), key=len)[0] != ["task_0", "task_1"]:
                fails.append(f"【R271】子目标成员不对 {_d271} ⇒ 同名门没归到同一段")
            _acc271 = [t for t in _ts271 if t.kind == "acceptance"][0]
            if not all(s.tid in _acc271.depends_on for s in _sg271):
                fails.append(f"【R271】验收没依赖子目标（deps={_acc271.depends_on}）"
                             " ⇒ 某段整体没达成也能判父目标闭合")
            if "task_3" not in _acc271.depends_on:
                fails.append("【R271】没有子目标的门被漏挂在验收外 ⇒ 它做不做都判完成")
        # 反向：不带 goal 的旧计划**逐字节不变**（零回退），不许冒出 subgoal 任务
        _c271b = Carrier(name="r271b", gates=[{"type": "compile", "cmd": ["x"], "cwd": "."}],
                         acceptance=["全绿"])
        if any(t.kind == "subgoal" for t in wire_tasks(_c271b)):
            fails.append("【R271】无 goal 字段的旧计划也长出了子目标层 ⇒ 非零回退")
    except Exception as _e271:                    # noqa: BLE001
        fails.append(f"【R271】齿自身异常：{type(_e271).__name__}: {_e271}")
    # ★★★★★ R270：**约束门不属于"目标达成"的合取**。
    #   真读数：约束门 `{type:compile, constraint:"不许改 tests/"}` 被同时挂进验收
    #   ⇒ `['accept::约束', 'accept::功能']` 并列。
    #   合取上安全（任一门红仍判未达成，实测 {'accept::约束':True,'accept::功能':False}
    #   ⇒ 全绿 False），但**污染的是"这个读数是什么意思"**：
    #     ① 约束是边界条件（不许破坏），不是达成证据（做成了什么）；
    #     ② 归因被稀释：验收红时分不清"没做成"还是"踩了边界"，
    #        而这两种红下一步动作完全不同（继续做 / 先回滚）。
    try:
        _c270 = Carrier(name="r270", constraints=["不许改 tests/"],
                        gates=[{"type": "compile", "cmd": ["x"], "cwd": ".",
                                "constraint": "不许改 tests/"},
                               {"type": "grep_required", "in": "a.py",
                                "patterns": ["x"]}],
                        acceptance=["全绿"])
        _ts270 = wire_tasks(_c270)
        _acc270 = [t for t in _ts270 if t.kind == "acceptance"]
        if not _acc270:
            fails.append("【R270】夹具没切出验收任务 ⇒ 测不出来")
        else:
            _名270 = [g.get("name") for g in (_acc270[0].gates or [])]
            if any("约束" in str(n) for n in _名270):
                fails.append(f"【R270】约束门混进了验收合取 {_名270} "
                             "⇒ 边界条件被当成达成证据，归因被稀释")
            if len(_acc270[0].gates or []) != 1:
                fails.append(f"【R270】验收只该剩非约束判据门（实得 {_名270}）")
        # 反向：全是约束门 ⇒ 无可验收 ⇒ 不生成验收任务，**不许假绿**
        _c270b = Carrier(name="r270b", constraints=["不许改 tests/"],
                         gates=[{"type": "compile", "cmd": ["x"], "cwd": ".",
                                 "constraint": "不许改 tests/"}],
                         acceptance=["全绿"])
        _ts270b = wire_tasks(_c270b)
        if any(t.kind == "acceptance" for t in _ts270b):
            fails.append("【R270】只有约束门却生成了验收任务 ⇒ 无达成证据也能判闭合（假绿）")
        _闭270 = _闭合检查(_ts270b, [t.tid for t in _ts270b])
        if _闭270.get("closed"):
            fails.append("【R270】只有约束门的计划被判闭合 ⇒ 假完成")
    except Exception as _e270:                    # noqa: BLE001
        fails.append(f"【R270】齿自身异常：{type(_e270).__name__}: {_e270}")
    # ★★★★★ R269：**约束任务不许替实现判达标**。
    #   真读数（case02 真目标，两条判据门 + 一条约束）：
    #     con_0 (constraint_check) name='不许改 tests/'
    #       gates=[{'compile': ...unittest...}, {'grep_required': '月度退款额'}]
    #   —— 一个叫「不许改 tests/」的任务身上挂着**全部实现判据门**。
    #   两重后果：① 约束绿 ⇒ 看起来目标达成（**替实现判达标**）；
    #             ② 约束任务依赖全量判据 ⇒ 实现没做完时它必红，
    #                而 errand 依赖它 ⇒ **实现任务死锁跑不起来**。
    try:
        _c269 = Carrier(name="r269", pre=["仓可编译"], constraints=["不许改 tests/"],
                        gates=[{"type": "compile", "cmd": ["make"]},
                               {"type": "grep_required", "in": "a.py",
                                "patterns": ["x"]}],
                        acceptance=["全绿"])
        _ts269 = wire_tasks(_c269)
        _con269 = [t for t in _ts269 if t.kind == "constraint_check"]
        _err269 = [t for t in _ts269 if t.kind == "errand"]
        if not _con269:
            fails.append("【R269】齿的夹具没切出约束任务 ⇒ 测不出来")
        else:
            _挂269 = list(_con269[0].gates or [])
            if _挂269:
                fails.append(f"【R269】约束任务身上挂着 {len(_挂269)} 条实现判据门 "
                             "⇒ 约束绿就看起来目标达成，且实现任务会被它卡死")
        if len(_err269) != 2:
            fails.append(f"【R269】判据门没切成对应的实现任务（实得 {len(_err269)}）")
        # 正向：真写了 `constraint` 关联的门**必须**挂上去（不许一刀切删光）
        _c269b = Carrier(name="r269b", constraints=["不许改 tests/"],
                         gates=[{"type": "grep_forbidden", "in": "tests/a.py",
                                 "patterns": ["TODO"], "constraint": "不许改 tests/"},
                                {"type": "compile", "cmd": ["make"]}],
                         acceptance=["全绿"])
        _con269b = [t for t in wire_tasks(_c269b) if t.kind == "constraint_check"]
        if _con269b and len(_con269b[0].gates or []) != 1:
            fails.append("【R269】显式声明 `constraint` 关联的门没被挂到约束任务上 "
                         "⇒ 一刀切删光（约束就没有可机判的门了）")
    except Exception as _e269:                    # noqa: BLE001
        fails.append(f"【R269】齿自身异常：{type(_e269).__name__}: {_e269}")
    _一130, _因130 = 门类型一致()
    if not _一130:
        fails.append(f"R130 ★ `_门字段表` 与 `gate_spec._run_one` 不一致：{_因130} "
                     f"⇒ 能执行的门被判「未知类型」（第二把尺子）")
    # ① 报错必须给合法清单（不许只说「你错了」）
    # ★ 齿自己也要**接住异常**：守卫被关掉时 `command` 会走到末尾的
    #   `raise AssertionError` ⇒ 异常逃出去 ⇒ **整个自检崩在 traceback 上**，
    #   而不是报红。★ 第一版就是这样：注入后 rc=1 但输出里没有一条 `✗`，
    #   我一开始还以为是「红了但没点名」。
    #   ⇒ 把异常文本也当成「报错文本」来查 —— 它同样必须含合法清单。
    try:
        _错130 = _gate_wellformed({"type": "command", "cmd": ["cargo", "test"]}) or ""
    except Exception as _e130a:               # noqa: BLE001
        _错130 = f"{type(_e130a).__name__}: {_e130a}"
    if "合法类型只有这些" not in _错130:
        fails.append("R130 ★ 未知门类型的报错**没给合法清单** ⇒ 模型只能盲猜 "
                     "（真跑实测：连续 3 次重规划都猜不中 ⇒ 停机）")
    if "command" not in _错130 or "compile" not in _错130:
        fails.append("R130 ★ 报错里没同时出现「错的」与「对的」⇒ 模型仍要猜")
    # ② 表里有的类型必须真能过校验（否则表在骗人）
    for _t130, _g130 in (
            ("axiom_footprint", {"type": "axiom_footprint", "cmd": ["lake"]}),
            ("kernel_statement", {"type": "kernel_statement", "in": "a.rs",
                                  "must_contain": ["x"]}),
            ("whole_reading", {"type": "whole_reading", "in": "a.rs",
                               "must_contain": ["x"]})):
        _r130 = _gate_wellformed(_g130)
        if _r130 is not None:
            fails.append(f"R130 ★ `{_t130}` 在表里却被判非法（{_r130}）⇒ "
                         f"表说合法、校验说非法 ⇒ 两把尺子")
    # ③ 反向：真非法的不许放过（不放松）
    for _t130b, _g130b in (
            ("command", {"type": "command", "cmd": ["x"]}),
            ("shell", {"type": "shell", "cmd": ["x"]}),
            ("compile缺cmd", {"type": "compile"}),
            ("grep缺in", {"type": "grep_required", "patterns": ["x"]})):
        # ★ 齿必须把**抛异常**也算「拒收」：守卫关掉后 `command` 会走到
        #   末尾的 `raise AssertionError`（那是比返回字符串**更强**的拒收）。
        #   ★ 第一版只判 `is None` ⇒ 注入 `if False:` 时自检**红了但没点名**
        #     （异常逃出去被别处接住）—— 齿判的是「返回了什么」，
        #     而这里真正要判的是「有没有放过去」。
        try:
            _r130b = _gate_wellformed(_g130b)
        except Exception:                      # noqa: BLE001 - 抛异常 = 拒收
            continue
        if _r130b is None:
            fails.append(f"R130 ★ 非法门 `{_t130b}` 被放过了 ⇒ 校验被放松")

    # ★ R130c：**分母不许是常量** —— 常量分母会让「齿加了、分母没动」，
    #   报表照样打「12/12 全绿」而实际有 21 条齿。
    #   ★ R68 / R70c / R72 已经犯过三次，这是第四次。
    import ast as _ast130
    _树130 = _ast130.parse(_P130(__file__).read_text(encoding="utf-8"))
    _分130 = False
    for _n in _ast130.walk(_树130):
        if (isinstance(_n, _ast130.Assign)
                and isinstance(_n.targets[0], _ast130.Name)
                and _n.targets[0].id == "_源码数"):
            if isinstance(_n.value, _ast130.Constant):
                fails.append("R130 ★ 自检分母 `_源码数` 是**常量** ⇒ "
                             "齿加了分母不动，报表会打「N/N 全绿」而齿更多")
            else:
                _分130 = True
    if not _分130:
        fails.append("R130 ★ 没找到 `_源码数` 的赋值 ⇒ 分母来源不明")

    # ② 规则升阶 RA-03：4 字段 → 规则空间 = 16（|R_n|=|R_1|·2ⁿ，1·2⁴）
    c = carrier_from_spec(good)
    rules, space = rule_ascent(c)
    if len(rules) != 4 or space != 16:
        fails.append(f"rule_ascent 应 4 规则/空间16，得 {len(rules)}/{space}")
    # ③ 接线 DAG：pre 2 约束 1 判据 2 验收 1 → 6 任务；errand 依赖前提+约束
    c2 = carrier_from_spec({"name": "g2", "pre": ["a", "b"], "constraints": ["c"],
                            "gates": [{"type": "compile", "cmd": ["x"], "cwd": ".", "expect_exit": 0},
                                      {"type": "compile", "cmd": ["y"], "cwd": ".", "expect_exit": 0}],
                            "acceptance": ["z"]})
    tasks = wire_tasks(c2)
    if len(tasks) != 6:
        fails.append(f"DAG 应 6 任务，得 {len(tasks)}")
    if tasks[-1].kind != "acceptance" or tasks[-1].depends_on != ["task_0", "task_1"]:
        fails.append("验收任务应依赖全部 errand")
    # ④ 全列守恒：合法 DAG 守恒；依赖未发射槽 → 拒绝
    ok, why = consume_ok(c2, tasks)
    if not ok:
        fails.append(f"合法 DAG 应守恒：{why}")
    bad_task = Task(tid="t9", name="幽灵", kind="errand", gates=[], depends_on=["不存在槽"])
    ok, why = consume_ok(c2, [bad_task])
    if ok:
        fails.append("依赖未发射槽应判违例")
    # ⑤ 计划器整体：plan_tasks 产出 conserved=True + 任务序列
    plan = plan_tasks(good)
    if not plan["conserved"] or len(plan["tasks"]) != 4:
        fails.append(f"plan_tasks 应 conserved+4任务，得 {plan['conserved']}/{len(plan['tasks'])}")
    # ★★★★★ ⑥ R4-F9 判据力：`topo` 必须是**真拓扑序** —— 每个任务的每个依赖
    #   都必须排在它**前面**。这条门存在的意义：把 DAG 从"展示品"变成"执行序"。
    #   （旧实现根本不返回 topo ⇒ 调用方只能自己猜顺序。）
    _p2 = plan_tasks(c2.__dict__ if hasattr(c2, "__dict__") else {
        "name": "g2", "pre": ["a", "b"], "constraints": ["c"],
        "gates": [{"type": "compile", "cmd": ["x"], "cwd": ".", "expect_exit": 0},
                  {"type": "compile", "cmd": ["y"], "cwd": ".", "expect_exit": 0}],
        "acceptance": ["z"]})
    _序 = _p2["topo"]
    _位 = {tid: i for i, tid in enumerate(_序)}
    if sorted(_序) != sorted(t["tid"] for t in _p2["tasks"]):
        fails.append("topo 必须是全部任务的一个排列")
    _违 = [f"{t['tid']}→{d}" for t in _p2["tasks"]
           for d in (t.get("depends_on") or [])
           if _位.get(d, 10 ** 9) >= _位.get(t["tid"], -1)]
    if _违:
        fails.append(f"topo 不是真拓扑序（依赖排在后面）：{_违}")
    # ★★★★★ ⑦ R4-F9 判据力：**成环必须拒发**（不是"排不出来就算了"）
    _环 = [Task(tid="a", name="A", kind="errand", gates=[], depends_on=["b"]),
           Task(tid="b", name="B", kind="errand", gates=[], depends_on=["a"])]
    try:
        topo_order(_环)
        fails.append("成环的计划必须抛错拒发，实际通过了")
    except ValueError:
        pass
    # ★ ⑧ 依赖指向不存在的任务 ⇒ 拒发（不是静默忽略）
    try:
        topo_order([Task(tid="a", name="A", kind="errand", gates=[],
                         depends_on=["幽灵"])])
        fails.append("依赖不存在必须拒发，实际通过了")
    except ValueError:
        pass
    # ★★★★★ ⑨ 计划**可复现**：同一份规约两次 ⇒ 同一份拓扑序。
    #   不可复现的计划没法进账本（账本要能比对"这一轮和上一轮是不是同一件事"）。
    if plan_tasks(good)["topo"] != plan_tasks(good)["topo"]:
        fails.append("同一规约两次 plan_tasks 的 topo 不一致（不可复现）")
    # ★ R130c：分母**从源码数**（不再硬编码），并配下限防成批删除。
    #   ★ 为什么必须数：我刚加了 6 条齿，硬编码的分母照样打「12/12 全绿」
    #     —— 报表说全绿，而齿根本没进分母。**报表在骗人。**
    _源码数 = len(open(__file__, encoding="utf-8").read().split("fails.append")) - 1
    if _源码数 < _SELF_GATES下限:
        fails.append(f"R130 ★ 判据点只有 {_源码数} 个（下限 {_SELF_GATES下限}）"
                     f"—— 齿被成批删掉而报表照样绿")
    if fails:
        print(f"auto_goal 自检：{_源码数 - len(fails)}/{_源码数} 绿")
        for f_ in fails:
            print(f"  ✗ {f_}")
        return 1
    print(f"auto_goal 自检：{_源码数}/{_源码数} 全绿")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="目标→任务序列 机器计划器")
    ap.add_argument("--selftest", action="store_true", help="跑自检门")
    # ★ 补真 CLI（2026-10-13）：此前本入口**只有 --selftest**，
    #   于是 `orch-plan` 这个 console_script 装了也没用（README 会写成假的）。
    #   计划器是 **0 模型调用** 的纯机器分解 ⇒ 适合让人先看"它会拆成什么"。
    ap.add_argument("--goal-json", default=None,
                    help="结构化规约 JSON（字符串或 @文件路径）；只做机器计划，不调模型")
    ap.add_argument("--root", default=".",
                    help="①→② 接线的**根**：定计划前先在这里真实盘点（默认当前目录）")
    ap.add_argument("--no-inventory", action="store_true",
                    help="★ 显式关掉盘点（默认**必须**盘点）；关掉时输出里会明写「没盘点」")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.goal_json:
        raw = args.goal_json
        if raw.startswith("@"):
            from pathlib import Path as _P
            raw = _P(raw[1:]).read_text(encoding="utf-8")
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"规约 JSON 解析失败：{e}")
            return 2
        try:
            # ★★★★★ R536g：**入口必须走接线后的那条路**（口径 388）。
            #
            #   真断点（2026-11-16 端到端跑 CLI 才抓到）：
            #     `带盘点定计划` **写好了、判一百八十九也绿了**，
            #     但**本入口调的还是 `plan_tasks(spec)`** ⇒
            #     用户敲 `orch-plan --goal-json …` **走的是老路：一步盘点都没有** ✗
            #     ⇒ **「舱内有个能用的函数」≠「装机后敲命令走这条路」**。
            #   ★ 判据测的是**函数**（单元），这里断的是**入口**（端到端）——
            #     只有真敲一次命令才能发现（口径 388）。
            #   ★ `--no-inventory` 是**显式**逃生门：关掉也**必须在输出里写明没盘点**，
            #     不许让「没盘点」看起来像「盘点过了、没问题」（口径 92）。
            # ★ R542：先把「根」这个参数本身验干净，别让下游的内部 assert 代劳。
            #   参数级错误要报成**参数级**的话，不要报成内部断言。
            if not args.no_inventory:
                import pathlib as _pl542          # ★ 局部导入：本模块不在顶层 import pathlib
                _根 = _pl542.Path(str(args.root))
                if not _根.exists():
                    print(f"计划器拒发：--root 指向的目录**不存在**：{_根}\n"
                          f"  ⇒ 计划是要被执行的，根都不存在就没有可执行的作业面。\n"
                          f"  ⇒ 换个已存在的目录，或先 `orch-init <文件夹>` 生成档案。")
                    return 2
                if not _根.is_dir():
                    print(f"计划器拒发：--root 不是目录：{_根}")
                    return 2
            if args.no_inventory:
                plan = plan_tasks(spec)
                plan["盘点"] = {"做没做": "没做（--no-inventory）",
                                "★后果": "计划里引用的路径**没有对过真值**"}
            else:
                plan = 带盘点定计划(args.root, spec)
        except (ValueError, AssertionError) as e:
            # ★ 守恒违例/载体不合法/计划引用了**不存在**的路径 → 计划器**拒发**
            #   （这是设计，不是崩溃 —— R535 起「拒发」也包含路径核对不过）
            # ★ R542：`AssertionError` 也必须在这里兜住。实测 `--root` 指到不存在的目录时，
            #   `知库.盘点` 里那句 `assert 根.is_dir()` 会**穿出去变成裸栈** ⇒
            #   用户看到 Python Traceback，会以为程序坏了，其实是他给的参数不存在。
            #   口径：**拒发就明说拒发，并说清哪个参数不对**。
            print(f"计划器拒发：{e}")
            return 1
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print(f"载体     : {plan.get('carrier')}")
            print(f"守恒     : {plan.get('conserved')}")
            print(f"规则空间 : {plan.get('rule_space')}")
            print(f"验收判据 : {plan.get('acceptance')}")
            盘 = plan.get("盘点") or {}
            # ★★★ R537：这里原来问的是 `做没做` / `根数` —— **生产方（`知库.盘点`）
            #   从来不设这两个键** ⇒ 真读数明明是好的，屏幕上却永远显示 `？` 和 `?`
            #   （实测：外来项目 `D:\tools` 已扫出 10,362 个文件，这一行仍写 `根 ? 个`）。
            #   ★ 这就是「问对了地方吗」的反面（口径 383）：**读数取错了键，
            #   和没有读数在屏幕上看不出区别**。现在按生产方真实给的键取。
            有盘 = isinstance(盘.get("文件数"), int)
            if 有盘:
                print(f"盘点     : 根 {盘.get('根', '?')} · "
                      f"文件 {盘['文件数']:,} · 后缀 {盘.get('后缀种数', '?')} 种 · "
                      f"{盘.get('总字节', 0) / 1048576:.1f}MB · "
                      f"建索引 {盘.get('耗时秒', '?')} 秒"
                      + (f" · ★{盘.get('★后果')}" if 盘.get("★后果") else ""))
            else:
                print(f"盘点     : {盘.get('做没做', '★没盘点')} · "
                      f"根 {盘.get('根数', '?')} 个 · 文件 {盘.get('文件数', '?')} 个"
                      + (f" · ★{盘.get('★后果')}" if 盘.get("★后果") else ""))
            核 = plan.get("★路径核对") or plan.get("路径核对") or {}
            if 核:
                # ★ R537：生产方给的是 `查了`（列表），不是 `核对了` —— 键名不匹配
                #   和「没读数」在屏幕上长得一模一样（口径 383）。数它。
                print(f"路径核对 : 核对 {len(核.get('查了') or [])} 条 · "
                      f"不存在 {len(核.get('不存在') or [])} 条 · "
                      f"占位符没查 {len(核.get('占位符没查') or [])} 条"
                      + (f" · ★索引外 {len(核.get('索引里没有') or [])} 条"
                         if 核.get("索引里没有") else ""))
                if 核.get("★索引与盘不一致"):
                    坏 = {k: v for k, v in 核["★索引与盘不一致"].items() if v}
                    if 坏:
                        print("           ★ 索引与盘不一致：" + " · ".join(
                            f"{k} {v}" for k, v in 坏.items()))
            print(f"任务数   : {len(plan.get('tasks') or [])}")
            for t in (plan.get("tasks") or []):
                dep = ",".join(t.get("depends_on") or []) or "-"
                print(f"  · {t.get('tid'):8s} {t.get('kind'):16s} "
                      f"{t.get('name','')}  依赖={dep}  门={len(t.get('gates') or [])}")
        return 0 if plan.get("conserved") else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
