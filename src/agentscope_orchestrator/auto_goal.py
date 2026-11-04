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
    kind: str                       # pre_emit | constraint_check | errand | acceptance
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
    gs = [g for g in (c.gates or []) if isinstance(g, dict) and g.get("type")]
    out: list[dict] = []
    for i, g in enumerate(gs):
        ng = dict(g)
        ng["name"] = f"accept::{g.get('name') or g.get('type') or i}"
        out.append(ng)
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
        # ★ 修复（2026-10-12）：原用 `cmd: ["true"]` 恒真占位门。约束无真实判据时
        #   就该没有门（上游充分性检查拦），而不是假绿门。
        tasks.append(Task(tid=tid, name=con, kind="constraint_check",
                          gates=list(c.gates or [])))
    errand_tids: list[str] = []
    pre_tids = [t.tid for t in tasks if t.kind == "pre_emit"]
    for i, g in enumerate(c.gates):
        tid = f"task_{i}"
        errand_tids.append(tid)
        tasks.append(Task(tid=tid, name=f"实现#{i}", kind="errand",
                          gates=[g], depends_on=pre_tids + constraint_tids))
    if c.acceptance:
        # ★ 修复（2026-10-12）：验收门原为 `cmd: ["true"]` 恒真占位 → 改为
        #   载体判据槽的合取（真值判据）。无判据槽 ⇒ 验收为空 ⇒ 不生成假绿验收任务。
        acc_gates = acceptance_gates(c)
        if acc_gates:
            tasks.append(Task(
                tid="accept", name=c.acceptance[0], kind="acceptance",
                gates=acc_gates,
                depends_on=errand_tids))
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
    return {
        "carrier": c.name,
        "rules": rules,
        "rule_space": space,
        "tasks": [t.__dict__ for t in tasks],
        "topo": 序,
        "conserved": True,
        "acceptance": c.acceptance,
    }


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


def _selftest() -> int:
    from pathlib import Path as _P130
    fails = []
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
            plan = plan_tasks(spec)
        except ValueError as e:
            # ★ 守恒违例/载体不合法 → 计划器**拒发**（这是设计，不是崩溃）
            print(f"计划器拒发：{e}")
            return 1
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print(f"载体     : {plan.get('carrier')}")
            print(f"守恒     : {plan.get('conserved')}")
            print(f"规则空间 : {plan.get('rule_space')}")
            print(f"验收判据 : {plan.get('acceptance')}")
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
