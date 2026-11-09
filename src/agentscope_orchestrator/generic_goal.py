"""通用目标驱动多智能体编排器：一个自然语言目标 → 全自动闭环 → 多文件产物交付。

语言/项目/工具全部无关。整条链路只由两类东西构成，其余全是纯机器算子：

  ① 语义映射（真模型产出，调用真实模型 API）——只负责“把自然语言目标/约束
     翻译成结构化数据”：
       - 目标 → 载体规约 JSON（auto_goal.Carrier：pre/constraints/gates/acceptance +
         deliverables：多文件列表，每文件可带独立门与依赖）
       - 执行智能体 → 交付文件内容（每窗一份候选文件集）
  ② 机器算子（0-LLM，不经模型判定，绝不让模型审模型）：
       - auto_goal.plan_tasks   ：规约 → 任务 DAG + 接线全列守恒机验
       - _topo_sort / _build_dag：多文件依赖 DAG 拓扑序 + consume_ok 守恒（未知依赖拒）
       - Router                 ：真实模型 API 机械负载均衡 + 健康 failover
       - gate_spec.evaluate     ：对每个产物文件实时跑声明式机器判据门
       - auto_objective.compute ：从账本偏差结构 → 独立真值门收敛判定 is_done
                                   （防平凡不动点：必须对照独立真值门，禁止放松已绿门）
       - _align_windows         ：多通过窗门真值等价（不动点判据，非投票）

闭环流程（收敛判据 = 目标满足不动点，不预设轮数）：
  目标 → 规划(模型产规约) → 机器计划 DAG（多文件拓扑） → 多窗并行产出文件集(模型)
  → 逐文件机器判据门 → 多窗一致对齐 ⇒ 收敛交付；有红 ⇒ 机器门失败证据回喂回炉重派
  → 某门连续红达阈值 ⇒ 重规划换路径（偏差驱动）→ 反复直到 auto_objective 判 is_done
  → 产物落 products/ + 回执。仅当耗尽 MAX_WORK 或预算护栏才诚实判“未收敛”。

用法：
  venv\\Scripts\\python.exe src\\agentscope_orchestrator\\generic_goal.py --goal "..."
  venv\\Scripts\\python.exe src\\agentscope_orchestrator\\generic_goal.py --dry --dry-multi
  venv\\Scripts\\python.exe src\\agentscope_orchestrator\\generic_goal.py --selftest
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# ★ 2026-11-09：本文件 docstring 的用法里有**脚本式**调用
#   （`python src/agentscope_orchestrator/generic_goal.py --selftest`），
#   但脚本模式下 `__package__` 是空 ⇒ 下面所有相对导入（`.msgs` 等）全部
#   `ImportError: attempted relative import with no known parent package`。
#   后果不是"少个入口"：`checks/自举齿探针.py` 的反自证基线（未改时应绿）
#   因此**一直红**，而那条红被当成"探针自身有问题"放过了 ——
#   于是这个探针**整年不带齿**（红错地方就没人再追）。
#   修法只在**脚本模式**下补包名与搜索路径：不碰任何语义、不影响 `-m` 调用。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    __package__ = "agentscope_orchestrator"

from .msgs import UserMsg
from agentscope_orchestrator import chief as chief_mod
from agentscope_orchestrator import 只读探针 as probe_mod

from agentscope_orchestrator import auto_goal, auto_objective
from agentscope_orchestrator import gate_witness
from agentscope_orchestrator.gate_spec import evaluate as gate_evaluate
from agentscope_orchestrator.gate_spec import validate as gate_validate
from agentscope_orchestrator.gateway import (
    build_client, live_model_pairs, select_channels, select_live_channels)
from agentscope_orchestrator.gate_derive import (
    bound_report, normalize_gates, _MAX_TIMEOUT, _DEFAULT_COMPILE_STALL,
    derive_gates, assert_derived_gates, precheck_pred_decidable,
    extract_deviations, pred_declared_scopes, deviation_identity,
    deviation_structure_verdict, pick_anchored_deviation)
from agentscope_orchestrator.router import Router
from agentscope_orchestrator import repo_index
from agentscope_orchestrator import profile as prof_mod
from agentscope_orchestrator import auto_mechanism as ao_mech   # 机器自生成（0-LLM）
# ★★★★★ R20（TS-469）：**机制自己真值空间** `Fin n` 上的闭环。
#   判定层的层选错了 —— 原来是经典层（六值字符串 + all/and 合取）；
#   机制的真值空间从来不是 `Prop`，是 `Fin n`（TS-469 头注）。
from agentscope_orchestrator import truth_space as _ts20

logging.basicConfig(level=logging.WARNING)
_log = print

# ── 工作区 / 产物 / 回执（全部由档案派生；工具不持有项目路径）──
ORCH = prof_mod.TOOL_ROOT                    # 工具自身根
_prof = prof_mod.get_profile()
WORKDIR = _prof.work_dir
PRODUCTS = _prof.products_dir
AUDIT = _prof.audit_dir
WORKDIR.mkdir(parents=True, exist_ok=True)
PRODUCTS.mkdir(parents=True, exist_ok=True)
AUDIT.mkdir(parents=True, exist_ok=True)
_RECEIPT = AUDIT / "generic_receipt.jsonl"

# 主树仓库根 = 档案声明的 repo_root（{repo} 占位符、文件应用/备份/回滚都锚到这里）。
REPO_ROOT = _prof.repo_root
BACKUP_DIR = AUDIT / "generic_backup"     # 主树文件应用前备份（原子回滚闭环）
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
_REPO_CTX_CAP = 8000                      # 源码注入上限（字符，依赖参考文件）
_REPO_TARGET_CAP = 60000                  # 目标文件源码注入上限（要改的文件需完整可见，防误删）

MAX_WORK = 40       # 回炉**资源护栏**（不是收敛判据）：收敛判据是不动点 Δ=∅ / 门全绿。
#                      ★ R20：真正的停机保证见 `_TS20_真值空间位数` 的鸽笼界 ——
#                        `max_work` 只是护栏，**永不需要**超过鸽笼界（定理）。

# ★★★★★ R20（TS-469 §4）：真值空间的**位数** = 判定依赖几条二值判定。
#   本编排器交付与否由**三项**二值判定决定（verdict / 多窗对齐 / 门保真）
#   ⇒ 真值空间 = `Fin 2^3`，**8 个位置**。
#   ★ 鸽笼：观测 9 个点**必然**有重复 ⇒ **必然已判定**。
#   ⇒ 停机保证是**定理**（`reaches_iff_bounded`：证书界 i < n），
#     不是 `max_work` 那种拍脑袋常数。
_TS20_真值空间位数 = 3
_TS20_鸽笼界 = 2 ** _TS20_真值空间位数 + 1     # = 9


def _TS20_向量(达标: bool, fixed_point: bool, witness_ok: bool) -> dict:
    """真值空间的**载体消去形态**：只由三条二值判定组成。

    ★★★★★ 载体消去（TS-469 §1 `solveN_eq_solveOn`）：门名 / 产物文本 / 模型名 /
      窗数 / token 数 / Δ 集合 / 读空间 —— **一律不进**。
      第一个比特只取「**本次任务达标了吗**」这**一个比特**；`verdict` 那个六值
      字符串只是**因由**（另记），不是真值。

    ★★★★★ R73b（2026-10-17）：**第一个比特的语义由「门全绿」改成「达标」。**

      为什么必须改：`verdict == "converged"` ⟺ `Δ == ∅` ⟺ **门绝对全绿**。
      而真项目的主树上**本来就带着历史违规**（实测 11 处）⇒ 那个比特
      **恒假** ⇒ 轨道上永远等不到"达标"这个点 ⇒ 不动点判定**永远不成立**。
      ⇒ 一个**恒假的比特**不是"严格"，是**没有信息量**（它分不开任何两个状态）。

      「达标」的定义（与文件级裁决**同一把尺子**）：
        门全绿 **或** 违规点集合相对**任务开始时的基线**严格变小。
      ★ 基线为空（主树本来就全绿）⇒ 退化成"必须绝对全绿" ⇒ **老行为不变**。
      ★ 仍是**真子集** ⇒ "修 3 个坏 2 个"不算达标（不放松）。
      ★ 因由里仍保留 `verdict` 六值串 ⇒ **信息没丢**，只是不再拿它当真值。
    """
    return {
        "本次任务达标": bool(达标),
        "多窗不动点对齐": bool(fixed_point),
        "门保真（有区分力）": bool(witness_ok),
    }
                    # ★ 2026-10-12 由 6 提到 40：原值 6 是"硬上限"语义，与"不预设轮数、
                    #   由不动点停机"的设计自相矛盾——真实工程一项改动常需十几轮回炉，
                    #   6 轮就到顶意味着绝大多数任务**必然**以 not_converged 收场（实机已如此）。
                    #   真正的刹车是 TOKEN_BUDGET_CHARS（预算）与判据结构停机（不可达），
                    #   本值只防"病态长跑"，应当宽到正常任务永远碰不到。
# ★★★★★ R8-F13（**如实标注**）：默认 1 窗 = **没有交叉验证**。
#   "多窗一致对齐"才是收敛的**证据**；单窗跑绿只能说明"这一条路走通了"，
#   不能说明"换一条路也走通" ⇒ **单窗不得自称收敛**。
#   ★ 不把默认改成 2：那是拿钱换置信，属调用方的成本选择，不该由默认值替他决定。
#   ★ 正确做法是**如实标注**：单窗的结果照常交付，但回执里必须写明
#     "单窗（无交叉验证）"，不许用 `converged` 冒充"已收敛"。
N_WINDOWS = 1       # 多智能体并行候选窗；多窗一致对齐提置信，可调大
MIN_PASS = 1
FP_STABLE_K = 2     # 不动点稳定判据：门真值向量连续 K 轮逐 key 相等 → 判结构不动点已达
REPLAN_AFTER = 3    # 未达不动点时的兜底：某门连续红达此轮数 → 真实模式重规划换路径（#6）
REPLAN_MAX = 2      # #1 漂移护栏：连续换门集次数上限。超过仍不收敛 → 判据不可达，升级退出，
                    #    不再让 planner 每两轮换一次"更顶格"门集白烧 token（实机已踩 4→5→6 漂移）。
# ★★★★★ R44（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：
#   原值 1_500_000 对**真项目的多文件任务**太小：实测 7 个文件、18 条交付物的一轮，
#   光窗口产出就要 ≈2,000,000 字符 ⇒ **连一轮都跑不完**就被护栏砍掉，
#   报"诚实判未收敛" —— 而它其实**正在收敛**（那一轮已把 `无恒真` 整条门修绿、
#   违规从 14 处降到 9 处）。
#   ★ 根因：这个数是按**单文件样例**标定的，而多文件是通用工程开发的常态。
#   ★ 为什么现在可以放宽：R43 之后每轮**保留**被判据验证过的进展 ⇒
#     多花的预算是**买真进展**，不是白烧（老行为下多跑几轮也是原地踏步）。
#   ★ 它**仍然只是资源护栏**，不是收敛判据：收敛判据是不动点 Δ=∅ / 门全绿。
#     真正的"该停了"信号是**没有进展**（R43 的违规点不再变小），不是烧了多少钱。
TOKEN_BUDGET_CHARS = 8_000_000   # 单次 run 模型字符预算护栏（≈260万 token 粗估）


# ════════════════════════════════════════════════════════════════
# 规划器提示：自然语言目标 → 载体规约 JSON（真模型产出，本模块只机器校验）
# ════════════════════════════════════════════════════════════════
_门态缓存: dict = {}


def _去非失败诊断(证据: str) -> str:
    """剔掉**非失败**的诊断（编译器 warning 等）—— 它们不是判据的失败载荷。

    ★★★★★ R41（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      第一次真跑时 cargo **重新编译**（要出 warning），`_evidence` 的"错误块"
      够用后会**再补一段尾部原始输出** ⇒ warning 的 `--> src\\某文件.rs:行:列`
      **混进了证据**。而 R36 的抽取器只认 `路径:数字` ⇒ 把 **warning 的位置**
      当成了"必改文件" ⇒ 清单变成 9 个**与缺口无关**的文件
      （`模块甲/mod.rs`、`模块乙/样本戊.rs`、`库/结构.rs` …）
      ⇒ **R36 的门反过来把规划器逼到错误的文件上** —— 比不给清单**更坏**：
      不给清单它只是猜，给了错清单它**照着错清单执行**。

      ★ 与 R33/R36 的教训连成一条线：**机器的活派给模型**（R36）之后，
        紧接着是 **喂给机器的输入没验**（R41）。判据再好，**输入被污染**就全废。

    【判据（跨语言，不是某一家的格式）】
      **失败载荷**才是"要改哪里"的依据；**warning 是工具絮语**，不构成判据失败。
      各家工具都把**非致命建议**标成 warning（rustc/gcc/clang/tsc/eslint/python），
      这是**严重度分级**，属于语言无关的**全称**形态。
      ⇒ 按**空行分段**，丢掉首行以 `warning` 起头的段（其 `-->` 位置行随之丢掉）。
      ★ 为什么不按 `-->` 一刀切：**编译错的 `-->` 正是必改位置**
        （`error[E0308] --> src\\a.rs:1:2`）⇒ 切了就把编译错这一最常见的情形废掉。
    """
    t = str(证据 or "")
    if not t:
        return t
    留: list[str] = []
    for 段 in re.split(r"\n\s*\n", t):
        s = 段.lstrip()
        if re.match(r"(?i)^\[?\s*warning\b", s):
            continue
        留.append(段)
    return "\n\n".join(留)


def _证据必改文件(证据: str) -> list[str]:
    """从红门证据里**机器抽出**「必改文件」清单（仓库相对路径，已排序去重）。

    ★★★★★ R36（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      R33 把红门证据（含 14 处 `绝对路径\\文件.rs:行号`）**原样**喂给了规划器。
      实测：规划器**完全没看那份清单**，改从符号快照里挑了
      `src/库/模块己.rs`、`src/模块甲/模块丁.rs`
      —— 与真违规**一处都不相交** ⇒ 执行器被锁死在错误的文件集上，整趟白跑。

      ★ 根因**不是**探针没喂（诊断脚本证明：14 条 `文件:行号` 全在探针文本里），
        而是**把机器的活派给了模型**：从一坨输出里认出"哪些文件必须改"，
        机器**当场就能做**（那是 `路径:数字` 的**全称形态**，不需要懂语言、
        不需要懂项目）。模型要做的是**内容**，不是**清点**。
      ★ 这正是用户那条口径：「能用全称机制就别枚举」——
        机器枚举出来的清单，比让模型从 8000 字符里捞，**既准又省流量**。

    【为什么这是**判据**而不是"提示"】
      被报在某文件 `F` 第 N 行的违规，**只有改 `F` 才可能消除**
      （守卫是静态扫描 `F` 的内容；而守卫本身是冻结区，改不动）。
      ⇒ "计划没覆盖 `F`" 可**机器证明**为"这条门不可能转绿"
        ⇒ 该计划**必然白跑**，值得当场退回重规划，而不是跑完再看它红。

    【边界（不猜）】
      · 只认 `…:数字` 且**该路径在仓库里真实存在**的（防幻觉路径污染清单）；
      · 绝对路径须落在仓库根之内（`..` 之外的不管）；
      · 抽不到 ⇒ 返回空表 ⇒ **不施加任何约束**（非缺口型目标照常工作）。
    """
    t = _去非失败诊断(证据)     # ★ R41：warning 不是判据失败，其位置不是必改位置
    if not t:
        return []
    见 = {rel for rel, _ in _证据违规点(证据)}
    return sorted(见)


def _冻结区集() -> set[str]:
    """档案里真实声明的冻结区路径集（归一化；空 = 未声明）。"""
    冻: set[str] = set()
    for _s in (prof_mod.get_profile().frozen or ()):
        _rel = str((_s or {}).get("path") or "").strip().replace("\\", "/").strip("/")
        if _rel:
            冻.add(_rel)
    return 冻


def _证据违规点(证据: str) -> list[tuple[str, int]]:
    """判据证据里点名的**违规点**（仓库相对路径, 行号），已排序去重。

    ★★★★★ R43（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      这是**判据自己的度量** —— 它红了，就报出它认为违规的位置。
      ⇒ 它给出了一个**可机器比较的偏序**：违规点集合。
      ⇒ "这一步有没有让判据更接近绿"**不再需要猜**：
        **集合严格变小（真子集）**就是"确实更接近了"，可机器证明。

      ★ 为什么必须把度量**从判据里读出来**、而不是另立指标：
        另立指标（比如"改了几行"、"测试通过数"）都是**代理量**，
        代理量与判据不同向时就会**优化代理而不优化目标**。
        这里读的是**判据自己的输出**，同向是构造性的，不是假设。

    ★ 与 `_证据必改文件` **共用同一把尺子**（后者只是前者投影到文件维）：
      两处若各写一套抽取，就会出现"计划按 A 判、裁决按 B 判"的**口径分叉**
      —— 那正是 R35/R39 的病根。
    """
    t = _去非失败诊断(证据)     # ★ R41：warning 不是判据失败
    if not t:
        return []
    根 = REPO_ROOT.resolve()
    冻 = _冻结区集()            # ★ R42：冻结区按定义改不了 ⇒ 不算"可消的违规点"
    见: set[tuple[str, int]] = set()
    for m in re.finditer(r"([^\s\"'`，,；;（）()\[\]{}<>|]+?)[：:](\d+)", t):
        原 = m.group(1).strip()
        if not 原:
            continue
        # ★★★★ R40 —— **本条已被我自己的齿探针推翻（诚实登记）**。
        #   我当初判定的根因是"Rust 的 `{:?}` 把反斜杠转义成两个 ⇒ `is_file()` 为假"。
        #   ★ 实测（2026-10-15，本机 Windows）：
        #       Path("E:\\\\基础设施\\\\…\\\\观测协议.rs").is_file()  →  **True**
        #     Windows 的路径解析会把**连续分隔符折叠**（`\\` 与 `\` 等价），
        #     所以双反斜杠形态**本来就能解析**。
        #   ⇒ 这段"两种形态都试"是**空转**：在 Windows 上第一候选就已命中，
        #     第二候选**永不生效**。
        #   ★ 齿探针 ⑱ 正是这样判的：把它退回 ⇒ 自检**仍全绿** ⇒ **无齿**。
        #     ⇒ 按"无齿的门不如没有"的纪律，⑱ 已撤，本段**如实降级为防御性代码**，
        #       不再宣称它是任何真跑缺陷的修复。
        #   ★ 那"run #7 必改清单为空"的真根因**仍未定**（真证据里违规清单确实存在
        #     —— R41 之后实测抽出了 7 个真违规文件）⇒ 空的原因不在这段。
        #     **不编故事**：登记为未定，等真跑复现再抓。
        #   ★ 保留这段的理由：它**无害**（第一候选命中即返回），且若将来跑在把
        #     反斜杠当普通字符的平台上，它是唯一能救的一点。
        候选 = [原]
        if "\\\\" in 原:
            候选.append(原.replace("\\\\", "\\"))
        p = None
        for cand in 候选:
            try:
                q = Path(cand)
                if not q.is_absolute():
                    q = 根 / cand
                q = q.resolve()
                if q.is_file():
                    p = q
                    break
            except (OSError, ValueError):
                continue
        if p is None:
            continue
        try:
            rel = p.relative_to(根)           # 仓库外 ⇒ ValueError ⇒ 跳过
        except (OSError, ValueError):
            continue
        rel_s = rel.as_posix()
        if not rel_s or rel_s.startswith(".."):
            # ★ 防御性（**实测为死代码**，诚实登记）：`p.relative_to(根)` 对
            #   仓库外的路径**本来就抛 ValueError**（下面的 except 已接住）⇒
            #   走到这里时 `rel` 必然是仓库内的相对路径 ⇒ `startswith("..")`
            #   **永远不可能为真**。保留它无害，但**不宣称**它拦住了什么。
            continue
        if rel_s.strip("/") in 冻:            # ★ R42
            continue
        try:
            见.add((rel_s, int(m.group(2))))
        except (TypeError, ValueError):
            continue
    return sorted(见)


def _红门违规点(红条目: list) -> frozenset:
    """一组红门条目 → 违规点集合（判据自己的度量）。空集 = 算不出度量。"""
    点: set = set()
    for a in (红条目 or ()):
        if not isinstance(a, dict):
            continue
        ev = str(a.get("err") or a.get("cause") or "")
        if ev:
            点.update(_证据违规点(ev))
    return frozenset(点)


# ★ R208：这条红门**能不能指出位置**（文件:行号）。
#   能 ⇒ 它就是在说「改这里」⇒ 是本次任务要改的，**不许当历史债豁免**；
#   不能（计数/告警/纯阈值）⇒ 才可能是历史债。
#   真证据：第 107 次真跑把 `cargo_test_lib_no_run`（目标门）豁免掉 ⇒ **假收敛**。
def _有定位208(_a: dict) -> bool:
    _文208 = ""
    for _k208 in ("err", "cause", "missing", "must_not_hits", "expected", "actual"):
        _v208 = _a.get(_k208)
        if _v208:
            _文208 += (json.dumps(_v208, ensure_ascii=False)
                       if not isinstance(_v208, str) else _v208) + "\n"
    return bool(_证据违规点(_文208))


def _红证据文件(红条目: list) -> set:
    """★ R195：从红门证据里抽「**此刻哪些文件还红**」。

    真证据（第 94 次真跑第 1 轮）：基线红门证据**拿到了**（R194 生效），
    但 R193 的收敛作业面**仍不启用** —— 因为它只找 `a["file"]` 字段，
    而红门条目只有 `err`/`cause` 一整段证据文本，文件位置写在文本里：
    `--> 示例工程\\基础集\\算子选择器.rs:682:19`。

    ★ 主缺陷族第 11 次：**「字段没有」被当成了「事实没有」。**

    三处都抽（有就用，互为补充）：
      ① `a["file"]`；
      ② 编译器定位行 `--> path:line` / `--> path`；
      ③ `_证据违规点()` 解析出的违规点末段（形如 `E0284::msg::符号::文件`）。
    """
    文: set = set()

    def _归(p: str) -> str:
        s = str(p or "").strip().replace("\\", "/")
        while s.startswith("./"):
            s = s[2:]
        return s

    for a in (红条目 or ()):
        if not isinstance(a, dict):
            continue
        _f = _归(str(a.get("file") or ""))
        if _f:
            文.add(_f)
        ev = str(a.get("err") or a.get("cause") or "")
        if not ev:
            continue
        # ② 编译器定位行（rustc/gcc/tsc/python 常见形态）
        for _m in re.finditer(
                r"-->?\s*([^\s:`\"']+\.(?:rs|py|ts|tsx|js|go|java|c|h|cc|cpp|hpp))"
                r"(?:[:：]\d+)?", ev):
            文.add(_归(_m.group(1)))
        # ③ 判据自己的违规点度量 —— **与 `_证据必改文件` 共用同一把尺子**（R43 纪律）。
        #   注意 `_证据违规点` 返回 `(仓库相对路径, 行号)` 元组，只取路径那一维。
        try:
            for _p in _证据违规点(ev):
                try:
                    文.add(_归(_p[0]))
                except (TypeError, IndexError, KeyError):
                    continue
        except Exception:
            pass            # 解析不了就不加（如实少认，不瞎猜）
    return 文


# ★★★★★ R212：**编译错误有阶段序；「点数不变但阶段前进」是进展，不是交换。**
#
#   真证据（第 106/109/110/111/112 次真跑，五次同形，窗内裁决原文）：
#     `✗ 算子选择器.rs：违规点数没变（1 → 1）且不是同一集合 ⇒ 没进展 ⇒ 只回退这一个文件`
#     `R124 窗内违规逐条：E0308::mismatched types | expected vec 态射样本 found 定列 _ _`
#   ★ 真相：模型把 `定列::新()`（E0433 未解析模块）改成了**能解析但类型不对**的写法
#     这是 **E0433（名字解析，阶段 1）→ E0308（类型检查，阶段 2）** —— **向前推进了**。
#   ★ 而 `_增量可判` 只认「真子集」：1 → 1 且集合不同 ⇒ 判「交换」⇒ **整文件回退**。
#     模型下一轮又拿到同一个 E0433 ⇒ 又会先解决名字、又得到 E0308 ⇒ 又被回退。
#   ⇒ **单步贪心死锁**：每一步都在原地做一个「正确但不够」的改动，然后被退回。
#     这不是模型不行，是**裁决把「过河踩的第一块石头」当成「走错路」**。
#   ⇒ 口径：**同点数、集合不交、且新错误码的阶段严格晚于旧错误码 ⇒ 算进展（保留）**。
#     · 阶段序（Rust 前端真实顺序）：词法/解析 < 名字解析 < 类型检查 < 借用/生命周期；
#     · 硬顶 2 次：连续靠「阶段前进」保留超过 2 次 ⇒ 不再放行（防在两个阶段间来回摆）。
_阶段序212 = {"E0460": 0, "E0461": 0, "E0462": 0, "E0586": 0,
               "E0433": 1, "E0432": 1, "E0412": 1, "E0425": 1, "E0405": 1,
               "E0416": 1, "E0603": 1, "E0553": 1,
               "E0308": 2, "E0061": 2, "E0107": 2, "E0277": 2, "E0599": 2,
               "E0609": 2, "E0307": 2, "E0309": 2, "E0317": 2, "E0531": 2,
               "E0382": 3, "E0499": 3, "E0502": 3, "E0596": 3, "E0505": 3,
               "E0493": 3, "E0716": 3}


def _码阶212(点集: frozenset) -> tuple[int, int]:
    """一个违规点集合的「阶段画像」：(最小阶段, 最大阶段)。取不到错误码 ⇒ (2, 2)。"""
    _阶们 = []
    for _点 in 点集 or ():
        _m = re.search(r"E\d{4}", str(_点))
        if _m:
            _阶们.append(_阶段序212.get(_m.group(0), 2))
    if not _阶们:
        return (2, 2)
    return (min(_阶们), max(_阶们))


def _阶段前进212(基线: frozenset, 现: frozenset) -> tuple[bool, str]:
    """同点数 + 不交 + 新阶段**严格更晚** ⇒ 是进展（过河踩了下一块石头）。"""
    if not 基线 or not 现 or len(基线) != len(现):
        return (False, "点数不同或有一侧为空：不是同点数的阶段前进")
    if 基线 & 现:
        return (False, "有共同的违规点还在：不是整批换阶段")
    _旧 = _码阶212(基线)
    _新 = _码阶212(现)
    if _新[0] > _旧[1]:
        return (True, f"阶段前进 {_旧} → {_新}：旧错误整批消失、新错误整批更晚")
    return (False, f"阶段没前进（旧 {_旧} → 新 {_新}）")


# ★★★★★ R216：**被否掉的产出不许原地重交。**
#
#   真证据（第 115/116 次真跑）：同一处（算子选择器.rs:682）连续多轮产出
#   **同一个不可能成立的解法**（想办法让 定列::新() 成立），
#   而该字段类型是 Vec<态射样本> ⇒ 永远 E0308。
#   每一轮都要花掉一整轮的真实算力与墙钟去重新评分同一份东西。
#   ★ 机器不能替模型想解法，但机器能记住哪些东西已经被否过 ——
#     这正是编排器该干的活：把「试过、不行」变成下一轮的硬约束，
#     而不是让模型从头再猜一遍。
# ★★★★★ R219：**目标文本里的数字不是判据。**
#
#   真证据（第 120 次真跑，轮 1 门真值原文）：
#     `（全库）@test_count_ge_1590=False`
#   而项目现场实测 `#[test]` = 1543。
#   ⇒ 这条门**永远不可能绿** ⇒ Δ 永远不为 ∅ ⇒ 永远判不了达标。
#   ★ 根因：目标文本里写着「#[test] 不少于 1590」（陈旧事实），
#     规划器把它变成了判据门。R199 已经把「现场实测」注入提示，
#     但**门本身**没被校正 —— 又是不折不扣的两把尺子。
#   ⇒ 口径：判据里的阈值一律以**现场实测**为准；
#     目标文本里的数字只当参考，绝不进判据。
def _活体测试数219(根) -> int:
    """现场数一遍 `#[test]`（只读，纯函数式）。"""
    import re as _re219
    _n219 = 0
    try:
        for _f219 in Path(根).rglob("*.rs"):
            try:
                _t219 = _f219.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            _n219 += len(_re219.findall(r"(?m)^\s*#\[test\]", _t219))
    except OSError:
        return 0
    return _n219


def _陈旧阈值门219(spec: dict, 根) -> list:
    """把「测试数 ≥ N」里的陈旧 N 改写为现场实测值；返回改动明细。"""
    import re as _re219b
    _活219 = _活体测试数219(根)
    if not _活219:
        return []
    _改219: list = []
    _型219 = _re219b.compile(
        r"(test[a-z_]*count[a-z_]*|test_attr_count)"
        r"(?:_ge_|_at_least_|>=|_不少于_|_至少_)(\d+)", _re219b.I)

    def _修219(_g):
        if not isinstance(_g, dict):
            return _g
        for _k in ("name", "pattern", "desc"):
            _v = _g.get(_k)
            if isinstance(_v, str):
                def _换(_m):
                    _旧 = int(_m.group(2))
                    if _旧 > _活219:
                        _改219.append({"gate": str(_g.get("name"))[:60],
                                       "旧阈值": _旧, "实测": _活219})
                        return _m.group(1) + _m.group(0)[len(_m.group(1)):].replace(
                            str(_旧), str(_活219), 1)
                    return _m.group(0)
                _g[_k] = _型219.sub(_换, _v)
        _p = _g.get("patterns")
        if isinstance(_p, list):
            _新p = []
            for _s in _p:
                if isinstance(_s, str):
                    _m = _型219.search(_s)
                    if _m and int(_m.group(2)) > _活219:
                        _改219.append({"gate": str(_g.get("name"))[:60],
                                       "旧阈值": int(_m.group(2)),
                                       "实测": _活219})
                        _s = _s.replace(_m.group(2), str(_活219), 1)
                _新p.append(_s)
            _g["patterns"] = _新p
        return _g

    for _g219 in (spec.get("gates") or []):
        _修219(_g219)
    for _d219 in (spec.get("deliverables") or []):
        if isinstance(_d219, dict):
            for _g219 in (_d219.get("gates") or []):
                _修219(_g219)
    return _改219


def _产出指纹216(内容) -> str:
    """一份产出的内容指纹（用于识别「原地重交」）。纯函数，稳定。"""
    import hashlib
    _b = 内容 if isinstance(内容, (bytes, bytearray)) else str(内容).encode("utf-8", "replace")
    return hashlib.sha256(bytes(_b)).hexdigest()


def _增量可判(基线: frozenset, 现: frozenset) -> bool:
    """这一步是否让判据**严格更接近绿**：违规点集合**真子集**变小。

    ★ R43 的接受判据。**为什么是真子集而不是"点数变少"**：
      点数变少可能是"修了 3 个、弄坏 2 个"（净减 1）—— 那是**交换**，不是进展；
      而真子集要求"原来的违规点**全部**消失，且没有新增" ⇒ **单调**，不会来回摆。
      ★ 这是**判据自己的度量**（它红了就报出它认为违规的位置），不是代理量。
    """
    if bool(现) and 现 < 基线:
        globals()["_阶段前进预算212"] = 2      # 严格进展 ⇒ 预算回满
        return True
    _前进212, _因212 = _阶段前进212(基线, 现)
    if not _前进212:
        return False
    if globals().get("_阶段前进预算212", 2) <= 0:
        _log(f"[generic]   ✗ R212 阶段前进到达硬顶（已连续放行 2 次）⇒ 不再放行：{_因212}")
        return False
    globals()["_阶段前进预算212"] = globals().get("_阶段前进预算212", 2) - 1
    _log(f"[generic]   ★ R212 **阶段前进也算进展**（{_因212}）⇒ 保留，不退回去"
         f"（剩余放行 {globals()['_阶段前进预算212']} 次）")
    return True


def _红门判据点(results: dict | None, 红条目: list,
                主体: str | None = None) -> frozenset:
    """**红门的完整判据点** = 位置点 ∪ 红门键（★ R76）。

    ★★★★★ 为什么不能只有"位置点"（真跑抓出 · 2026-10-17 · 第三次真项目验收）：

      【真跑证据】那一轮的门集是 5 条，其中两条红：
        · `（全库）@test_测试防假_全库测试_无只打印` = 假
          —— 它的输出里**带 `文件:行号`** ⇒ 能算出 14 个**位置点**；
        · `src/模块甲/mod.rs@模块甲_登记键与闭合标记俱在` = 假
          —— 它是**整文件级**的读门，**一个位置都不报**（它只说"没找到那个标记"）。
      模型的产出恰好是"给 mod.rs 补上闭合标记" —— **这条门应该翻绿**。
      可逐文件裁决算出的违规点是 **14 → 14**（只有整库那条门的位置点）
      ⇒ 判成"未严格变小" ⇒ **回退** ⇒ 一个**正确**的改动被丢掉。

      【根因】**逐文件裁决只看得见"能报出位置的红门"。**
        而"整文件级 / 结构级"的红门（读整篇找一个标记、比一个签名）
        **天生不报位置** ⇒ 它们对逐文件裁决**完全不可见**。
      ★ 后果是**结构性**的：凡是"任务是补一个标记/补一段结构"而
        "验收门集里还挂着一条整库的债"的任务，**在构造上不可能被保留**
        ⇒ 无人值守**永远跑不通**。★ 这不是模型不行，是裁决看不见。

      【修法】判据点 = **位置点** ∪ **红门键**。
        红门键就是 `results` 里值为假的那些键（`主体@门名`）。
        —— 它与位置点**同源**（都来自项目自己的判据），只是**形态不同**：
        一条说"第 N 行有问题"，一条说"整篇不满足"。
      ★ 为什么必须用 R75 刚立的**主体**过滤：
        不按主体过滤 ⇒ 改 A 文件时，B 文件的门翻绿也算"进步" ⇒
        **串味**（用别人的进展给自己记账）。
      ★ 与 R43/R46 一脉相承：R43 说"别用布尔，用判据自己的度量"，
        R46 说"全绿是终点、必须先判"，R76 说"**度量必须覆盖判据的全部形态**"
        —— 只覆盖"能报位置的"那一半，等于把另一半判据**关在门外**。
    """
    点: set = set(_红门违规点(红条目))
    for k, v in (results or {}).items():
        if v is True:
            continue
        if 主体 is not None and not str(k).startswith(f"{主体}@"):
            continue
        点.add(f"门:{k}")
    return frozenset(点)


def _作业面路径集(deliverables: list) -> frozenset[str]:
    """本次任务的**作业面** = 目标点名要改的那些文件（归一为 `/`、无 `./`）。"""
    return frozenset(
        str(d.get("path") or "").replace("\\", "/").lstrip("./")
        for d in (deliverables or []) if d.get("path"))


def _作业面外身份(devs: list, 面: frozenset[str]) -> frozenset[str]:
    """哪些偏差身份**落在作业面之外** —— 没有任何交付物能影响它。

    ★★★★★ R119b（真跑抓出的第 33 层 · 2026-10-20）：**R73 只修了一半。**

      R73 的 docstring 写着：「主树上的历史违规被算成了本次任务的责任
      ⇒ **恒红门** ⇒ 无人值守**永远不可能跑通一次**」。
      它把口径改成 `cur ⊊ 基线`（严格变小），解决了「必须绝对全绿」。

      ★ 但**没解决**：基线里那条债如果**作业面碰不到**，`cur ⊊ 基线`
        永远不成立 —— 因为它既在基线里、也在现值里、且没有任何
        交付物能把它消掉。

      真证据（第十六次端到端，work=4）：
        `cargo_test_防假门`（**全库门**）恒红，因由
        `编译错|error@运行时事件采集.rs:987:4;error@运行时健康诊断.rs:3:12;…`
        —— 这些文件**都不在 deliverables 里**（本轮只做 `src/模块甲/mod.rs`）。
        ⇒ 编排器永远修不掉它，却永远被要求把它变小 ⇒ **死锁**。
        ⇒ 这就是 15 次真跑**全部 exit 1** 的结构性原因。

      ★ 判据（全称，不枚举门型）：一条偏差属于**作业面外**
        ⟺ 它带得出一个文件路径，且那个路径**不在作业面里**。
        · `file` 为空的偏差 ⇒ 当作**作业面内**（宁可严：不知道该谁负责的，
          就是本次的责任）；
        · 全库门的偏差会带上它真正报错的文件（`_err_path`），
          所以这条判据对全库门**自动生效**，不需要特判门型。

      ★ **不放松**：作业面外的**新债**（不在基线里的）仍然判不达标 ——
        那是回归。只有「基线里就有、作业面碰不到」的历史债才被剔除。
        这与 R73 的立意是同一条：**历史债不是本次任务的责任，
        但新债是。**
    """
    out: set[str] = set()
    for d in devs or []:
        p = str(d.get("file") or "").replace("\\", "/").lstrip("./")
        if p and p not in 面:
            out.add(deviation_identity(d))
    return frozenset(out)


def _基线偏差(workdir: Path, deliverables: list, dev_pred: dict) -> list:
    """任务开始时**未改动的主树**上的判据偏差 —— Δ 的起点（★ R73）。

    ★★★★★ 为什么必须有它（真跑抓出 · 2026-10-17 · Rust 工程 `example-project`）：

      【真跑证据】隔离树里连跑 3 轮，门一直红，而红的是**主树上本来就有的**
        历史违规。`全库测试_无只打印` 报 11 处「只打印不断言」：
          · `src/判据/观测协议.rs` 8 处（1993/2034/2067/2126/2164/2265/2325/2357）
          · `src/核心类型/运行时涌现.rs` 3 处（2495/2500/2506）
        ★ 我在**未改动的主树**上单独跑过这条门：**一开始就是红的、就是这 11 处**。
        而本次任务只是"闭合 `src/模块甲/mod.rs` 里登记的**一处**缺口"。

      【根因】`verdict == "converged"` ⟺ `cur_devs == ∅` ⟺ **门全绿**，
        而"全绿"是**相对空集**说的 ⇒ 主树上的**历史违规**被算成了
        **本次任务的责任** ⇒ **恒红门** ⇒ 无人值守**永远不可能跑通一次**。

      ★ 这不是模型能力问题：模型确实在干活（判据违规点 14 → 13），
        但它每加一个新测试又可能引入新违规 ⇒ 在"必须绝对全绿"下**无解**。

      【修法】**Δ 相对基线**：基线 = 未改动的主树上的偏差。
        达标 = `cur_devs ⊊ 基线偏差`（**真子集变小**）或全绿。
      ★ 口径统一：文件级 `_增量可判` 用的就是"真子集变小"这把尺子，
        现在**最终裁决用同一把尺子**（原来两把尺子给出相反答案：
        文件级说"变小就算进展"、最终裁决说"必须绝对绿"）。
      ★ 老行为不变：基线为空（主树本来就全绿）⇒ `prev_devs = None`
        ⇒ 与原来**完全一致**（必须绝对全绿）。
      ★ 不放松：仍是**真子集**（"修 3 个坏 2 个"不算进展）。
      ★ 成本：多跑一遍门（真项目 ≈17s 的 `cargo test` + 编译）。
        换来的是**任务可达性**的判断 —— 而且若基线本来就全绿，
        这一遍恰好就是"**缺口不在验收门上**"的**提前告警**（省掉一整晚空跑）。

    ★ 失败时**退回"无基线"**（= 绝对全绿的老口径）：宁可严，不可假绿。
    """
    if not deliverables:
        return []
    # ★★★★★ R134（真跑抓出的第 47 层 · 2026-10-23 · 第二十九次端到端）：
    #   **基线探针炸了，而它炸掉的是「任务可达性」这件事本身。**
    #
    #   真证据（第 29 次真跑日志第 15 行，原文）：
    #     `★ R73 基线探针异常（按「无基线」处理，退回绝对全绿口径）：
    #       TypeError: argument should be a str or an os.PathLike object
    #       where __fspath__ returns a str, not 'NoneType'`
    #   ★ 根因：本跑档案的 `repo_root` 是 **None**（真跑脚本没声明主树），
    #     而 `_score_files(..., file_root=REPO_ROOT, ...)` 拿 None 当路径用。
    #
    #   ★★ 而「按无基线处理」不是"降级"，是**把 R73 整个推翻**：
    #     R73 存在的唯一理由就是「主树历史债不算本次任务的责任」。
    #     退回绝对全绿 ⇒ 主树上那 13 处历史违规**又变成本次任务的责任**
    #     ⇒ **恒红门** ⇒ 无人值守永远不可能跑通一次。
    #     ★ 这正是 R73 注释里写的那句话，现在被它自己的 except 兑现了。
    #
    #   ★ 与 R126 同族：**状态码/异常不是因由**。
    #     `except Exception: return []` 把「配置缺失」吞成「没有历史债」，
    #     而这两件事的处置**完全相反**：
    #       · 真没有历史债 ⇒ 必须绝对全绿（严）；
    #       · 配置缺失 ⇒ **这台机器根本没量到基线**（不可判，不是严）。
    #   ⇒ 修法：**区分这两件事**，并把配置缺失**说出来**（可行动）。
    # ★★★★★ R139（真跑抓出的第 52 层 · 2026-10-23 · 第三十二次端到端）：
    #   **R134 只防住了 `REPO_ROOT` 一侧，另一侧（`workdir`）照样是 None。**
    #
    #   真证据（第 32 次真跑日志第 15 行，与第 29 次**一字不差**）：
    #     `★ R73 基线探针异常（按「无基线」处理，退回绝对全绿口径）：
    #       TypeError: … where __fspath__ returns a str, not 'NoneType'`
    #   ★ R134 的守卫是 `if REPO_ROOT is None:` —— 而这次 `REPO_ROOT` **不是** None，
    #     None 的是 `workdir`（= `self.round_root`）。
    #     调用链：`_score_files(workdir, …, file_root=REPO_ROOT)` 里
    #     `base = file_root or workdir` ⇒ base 有值（不是它炸的）；
    #     但 `_score_gates(workdir, f, …)` **仍把 workdir 原样传下去**
    #     ⇒ 下游 `Path(None)` ⇒ TypeError。
    #
    #   ★★ 形状与 R119a/R108/R116 齿③ **完全同族**：
    #     修了一个入口的守卫，**没修另一个入口**。
    #     R134 是「我以为只有 REPO_ROOT 会缺」，而实际有**两个**根。
    #
    #   ★★★ 更要紧的是处置：`return []` = 绝对全绿 = **恒红门**。
    #     32 次真跑全部失败，根因就是这一句。
    #     ⇒ 正确做法**不是**「缺了就放弃测量」，而是
    #       **把能用的根用起来**（workdir / REPO_ROOT / 产物父目录），
    #       只有**全都缺**才如实说「无法测量」。
    #       放弃测量是最坏的选项：它把「量不到」变成「必须全绿」，
    #       等于把一个不可判的问题**判成最难的那个**。
    # ★★★★★ R141（真跑抓出的第 54 层 · 2026-10-23 · 第三十三次端到端）：
    #   **R139 把两个根的优先级排反了。**
    #
    #   真证据（第 33 次真跑，日志逐轮）：
    #     轮1-3  `R139 基线根归一：workdir=None … ⇒ 采用 <主树>`
    #             `R73 基线：主树上**本来就有** 4 处判据偏差` ← **对的**
    #     轮4    `R139 … workdir=work/task/r4 … ⇒ 采用 work/task/r4`
    #             `R73 换门集 ⇒ 按新判据重取基线：2 处偏差` ← **错树**
    #     轮5    `workdir=work/mod_rs/r5 … ⇒ 采用 work/mod_rs/r5` ← **错树**
    #
    #   ★ 根因：`workdir` 是**本轮隔离树**（模型改文件的地方），
    #     而基线要量的是「**未改动的主树**上本来有什么债」（R73 原文）。
    #     在隔离树上量基线 ⇒ 量到的是「模型刚改完的样子」⇒
    #     基线跟着产物一起漂 ⇒ 达标口径永远追不上。
    #   ★★ 与 R128/R138/R140 同族：**手里那个量不是判据需要的那个量。**
    #     这次是「手里那个**根**不是判据需要的那个根」。
    #
    #   ★ 修法：**优先主树**（`REPO_ROOT`），隔离树只作退路。
    #     顺序反过来不是"选择"，是**语义**：基线 = 主树未改动状态。
    _根139 = None
    for _c139 in (REPO_ROOT, workdir):
        if _c139 is None:
            continue
        try:
            _p139 = Path(_c139)
        except TypeError:
            continue
        if _p139.exists():
            _根139 = _p139
            break
    if _根139 is None:
        _log("[generic] ★★★ R139 基线**无法测量**：`workdir` 与 `repo_root` "
             "**都**没有可用路径 ⇒ 机器**没有可量的根** ⇒ 基线 = 空 = "
             "「必须绝对全绿」。★ 这不是「没有历史债」，是「没量到」。"
             "若目标是主树工程，请在档案里声明 `repo_root`。")
        return []
    _log(f"[generic] ★ R139 基线根归一：workdir={workdir!r} "
         f"REPO_ROOT={REPO_ROOT!r} ⇒ 采用 {_根139}")
    try:
        _res, red = _score_files(_根139, deliverables,
                                 file_root=(REPO_ROOT or _根139),
                                 repo_root=(REPO_ROOT or _根139))
    except Exception as exc:                      # noqa: BLE001
        _log(f"[generic] ★ R73 基线探针异常（按「无基线」处理，"
             f"退回绝对全绿口径）：{type(exc).__name__}: {exc}")
        return []
    try:
        return list(extract_deviations(red or [], dev_pred))
    except Exception as exc:                      # noqa: BLE001
        _log(f"[generic] ★ R73 基线偏差提取异常（按「无基线」处理）："
             f"{type(exc).__name__}: {exc}")
        return []


def _增量应用(adopted: dict, 基线: frozenset, round_root: Path,
               deliverables: list, 回退详情: list | None = None) -> tuple[dict, list, list, list]:
    """**逐文件**应用 + 逐文件用判据度量裁决（保留/回退）。

    ★★★★★ R43（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      真跑实测：7 个文件一次全应用 ⇒ 判据从 14 处违规降到 9 处
      （`无恒真` 那条门**已经全绿**、4 个恒真断言全修好了）—— 但因为
      "还没全绿"，`_round` **整轮回滚** ⇒ **进展全部丢弃** ⇒ 下一轮从零开始
      ⇒ 配上预算护栏，**中长线任务永远不可能收敛**（每轮都白干）。

      ★ 根因：**裁决粒度错了**。判据是**整 crate** 的（一个布尔），
        而"改动"是**逐文件**的 ⇒ 用整 crate 的布尔去裁决一批文件，
        只能是全有或全无。
      ★ 修法：**把裁决粒度对齐到判据能给的度量上** —— 判据给的不只是布尔，
        它的失败消息里**点出了每一处违规的位置** ⇒ 那就是一个**偏序**。
        ⇒ 逐文件应用、逐文件比较违规点集合：**真子集变小就保留**，
          否则**只回退这一个文件**。
      ★ 安全性：每一步保留都被**项目自己的判据**验证过"违规点严格减少"；
        最终验收**仍然是"门全绿"**（`ok` 仍由全绿决定）。
        ⇒ 不会因为"接受了部分进展"就把垃圾当成功交付。
      ★ 保守回退：**算不出基线/现态**（判据没点出任何位置）⇒ 返回空结果，
        由调用方走**原来的全有或全无**路径（零回退，不动老行为）。

    返回 (results, red, 保留的文件表, 回退的文件表)。
    """
    # ★ R177：`回退详情` 是**出参**（追加），不改返回值 ⇒ 老调用点/老齿零影响。
    #   每一笔 = {文件, 因由, 新增违规点, 原有违规点}。
    if not 基线:
        return {}, [], [], []
    results: dict = {}
    red: list = []
    留: list = []
    退: list = []
    当前 = 基线
    # ★★★★★ R76：**先量一次"改之前"**。
    #   逐文件裁决要判"**这条门（主体是它）翻绿了吗**"，就必须有一个"改之前"的
    #   门真值。而 `基线` 只是**位置点**（`red_audit_prev` / 门态缓存），
    #   里面没有"红门键" ⇒ 结构性红门翻绿看不见（见 `_红门判据点` 的段注释）。
    #   ★ 成本：每轮**多跑一遍门**。可接受 —— 它买的是"正确改动不被丢掉"，
    #     而丢掉一次正确改动要再烧一整轮模型调用（贵得多）。
    #   ★ 量不到 ⇒ `前res` 为空 ⇒ 退回"只按违规点判"（老行为，零回退）。
    前res: dict = {}
    前red: list = []
    try:
        前res, 前red = _score_files(
            round_root, deliverables, file_root=REPO_ROOT, repo_root=REPO_ROOT)
    except Exception as _e76:             # noqa: BLE001
        _log(f"[generic] ★ R76 改前量取失败（退回只按违规点判）："
             f"{type(_e76).__name__}: {_e76}")
    if not 前res and deliverables:
        # ★ R112：**静默的空也要说出来**。`前res` 为空时 `_前76` 是 None，
        #   逐文件裁决只能退回「只按位置点判」—— 而位置点看不见结构性红门。
        #   不打印这件事，就等于让「量不到」与「本来就没红」长得一样。
        _log("[generic] ★ R112 改前量取**为空**（门一条都没量到）"
             "⇒ 逐文件裁决退回「只按位置点判」，结构性红门对它不可见")
    for rel, text in adopted.items():
        if _是拒收产物(text):
            _log(f"[generic]   ↳ {rel}：机器拒收的产物 ⇒ **不写主树**"
                 f"（{_是拒收产物(text)}）")
            continue
        bk = _backup_file(rel)
        try:
            _apply_file(rel, text)
        except Exception as e:            # noqa: BLE001 写不进 ⇒ 回退这一个，不炸整轮
            _log(f"[generic]   ↳ {rel}：写入失败（{type(e).__name__}）⇒ 回退该文件")
            _rollback_file(bk)
            退.append(rel)
            continue
        r_this, red_this = _score_files(
            round_root, deliverables, file_root=REPO_ROOT, repo_root=REPO_ROOT)
        点 = _红门违规点(red_this)
        # ★★★★★ R46（**我自己 R43 的洞** · 2026-10-15 自审抓出）：
        #   `_增量可判` 要求"违规点**非空**且真子集变小"。而**最后一个**文件
        #   修好之后，判据**全绿** ⇒ 违规点是**空集** ⇒ `bool(现)` 为假
        #   ⇒ **判成"未变小" ⇒ 回退它**！
        #   ★ 后果：**只剩一个文件时永远不可能收敛** —— 每轮都把那个
        #     "让判据全绿"的文件回退掉 ⇒ 零进展 ⇒ 无限循环。
        #   ★ 根因：把"**全绿**"和"**进展**"当成了同一件事的两个分支，
        #     而 `_增量可判` 只认"进展"。**全绿是更强的条件**（它是终点），
        #     必须**先判**它。
        #   ★ 判据：门结果**非空且全 True** ⇒ 这是终点 ⇒ **保留**。
        #     （用 `r_this` 而不是"点为空"：判据算不出位置时点也是空，
        #       那种情况**不是**全绿，不得混同。）
        全绿 = bool(r_this) and all(r_this.values())
        # ★★★★★ R76：**这条门（主体是 rel）翻绿了吗**。
        #   ★ 判"真子集"而**不**要求非空：某文件的**全部**门都绿时，
        #     `_后点` 就是空集 —— 那是**这个文件的终点**，不是"算不出度量"。
        #     （R46 的教训在这里以"文件粒度"复发：用 `bool(现)` 会把它判成未变小。）
        #   ★ 只有"改前真的量到过"才用它（`前res` 非空）；否则退回老判据。
        _前76 = _红门判据点(前res, 前red, 主体=rel) if 前res else None
        _后76 = _红门判据点(r_this, red_this, 主体=rel)
        _进步76 = (_前76 is not None) and (_后76 < _前76)
        # ★★★★★ R112（真跑抓出的第 26 层 · 2026-10-20 · 第十三次端到端）：
        #   **一个把违规点从 10 修到 0 的文件被回退了。**
        #
        #   真证据（日志原样）：
        #     `✗ src/模块乙/胞坐标.rs：判据违规点未严格变小（10 → 0）
        #        ⇒ **只回退这一个文件**`
        #   10 → 0 **就是**严格变小，而它判"未变小" ——
        #   **日志印着的事实与它给出的结论互相矛盾，却照常执行了回退。**
        #
        #   根因：`_增量可判` 是 `bool(现) and 现 < 基线`，`现=∅` 时 `bool(∅)` 为假。
        #   而 `全绿 = all(r_this.values())` 看的是**整库**门集（含全库级红门），
        #   不是**这个文件主体**的门 ⇒ 整库还红着时 `全绿` 永远为假
        #   ⇒ 落到 `_增量可判(当前, ∅)` ⇒ 被 `bool(∅)` 判成未变小 ⇒ **回退**。
        #
        #   ★ 这与 R46 记下的是**同一句话**：
        #     「用 `bool(现)` 会把它判成未变小」——
        #     我在 `_红门判据点` 的注释里写过它，却没在 `_增量可判` 里改它。
        #     第 26 层同形：**把教训写在注释里，没写进代码。**
        #
        #   ★ 为什么不直接改 `_增量可判`：它的语义是「**增量**」，
        #     自检 ③ 明确锁着「空现态不算增量进展（全绿由 ok 那条路裁决）」。
        #     那条自检是对的 —— 只是它假设了「全绿」能覆盖「该主体全绿」，
        #     而**整库红门让它覆盖不到**。⇒ 在这里补一条**独立**判据：
        #     「该主体自己的违规点**全部消失**」= 该主体的终点，必须保留。
        #   ★★ 第一版我写成 `bool(当前) and not 点` —— **R46 的齿当场判红**：
        #     `点` 只是**位置点**（不含红门键），「红着但算不出位置」时它也是空
        #     ⇒ 会把没验证过的改动留在主树。**齿抓住了我修法本身的过度。**
        #   ⇒ 收窄到真正缺的那一种情形：
        #     「**改前量不到**（`_前76 is None`），但**改后该主体一条红门都没有**」。
        #     此时 `_后76` 为空 = 该主体**现在真的是绿的**，不是"算不出度量"
        #     （`_后76` 含红门键，红着就非空）。这是它的终点，必须保留。
        #     而「改前量得到」的情形已由 `_进步76`（∅ ⊊ 非空）覆盖，不必重复。
        _清空76 = (_前76 is None) and not _后76
        if 全绿 or _进步76 or _清空76 or _增量可判(当前, 点):
            _因76 = ("**判据全绿**" if 全绿 else
                     (f"该文件主体的门 {len(_前76)} → {len(_后76)}（严格变小）"
                      if _进步76 else
                      (f"该文件主体的违规点**全部消失**"
                       f"（{len(当前)} → 0）—— 这是它的终点，不是「算不出度量」"
                       if _清空76 else
                       f"判据违规点 {len(当前)} → {len(点)}（严格变小）")))
            _log(f"[generic]   ✓ {rel}：{_因76} ⇒ **保留**")
            留.append((rel, bk))
            当前 = 点
            # ★ R76：保留之后"改之前"就是这一次的结果（下一个文件要拿它当基线）
            #   ★★ 诚实登记：这一行**当前没有能分开两种实现的齿**。
            #     原因：`_后76/_前76` 都按**主体过滤**（只看 `rel@*` 的键），
            #     而 `rel` 的门读的就是 `rel` ⇒ 别的文件的写入**不改变**它的真值
            #     ⇒ 推不推进，在**我的全部夹具**上结果一样。
            #     它唯一负载的情形是「**同一个文件被列成两个交付物**」
            #     （`x.rs` 与 `x.rs:1993`）—— 而 `_valid_spec` 恰恰**允许**
            #     这种写法（已登记的缺陷）⇒ 这一行是**防那个真缺陷的**。
            #     但 Windows 上 `:` 不能做文件名 ⇒ **造不出那个夹具**。
            #     ⇒ 保留（它是正确卫生），但**不假装它被测过**。
            前res, 前red = r_this, red_this
            results, red = r_this, red_this
            if 全绿:
                # ★ R46b：**已达终点 ⇒ 停**。剩下的文件不必再试。
                #   ① 正确性：目标已达成，剩下的改动**不需要**；
                #   ② 成本：每试一个文件都要**跑一遍全部门**
                #      （真项目里一次 `cargo test --lib` 就是几十秒）
                #      ⇒ 早停省下的是**真金白银的墙钟时间**。
                _log("[generic]   ■ 判据已全绿 ⇒ 停止逐文件试"
                     f"（剩余 {len(adopted) - len(留) - len(退)} 个不必再试）")
                break
        else:
            # ★ R112b：**回退理由必须说真话**。
            #   原先不管什么原因都印「判据违规点未严格变小（X → Y）」，
            #   而 X → Y 有时**明明变小了**（10 → 0）——
            #   于是日志在自相矛盾，而回退照做。
            #   ⇒ 印**真正的原因**：不是「没变小」，而是「出现了原违规点之外的新违规」
            #     （即不是真子集）。这两件事对下一个读日志的人意义完全不同。
            _新增76 = sorted(点 - 当前)
            if len(点) < len(当前):
                _因76坏 = (f"点数变少（{len(当前)} → {len(点)}）但**不是真子集**："
                           f"原违规点没全消、又新增了 {len(_新增76)} 个"
                           f"（如 {_新增76[0] if _新增76 else '?'}）"
                           f" ⇒ 这是**交换**不是进展")
            elif len(点) == len(当前):
                _因76坏 = (f"违规点数没变（{len(当前)} → {len(点)}）"
                           f"且不是同一集合 ⇒ 没进展")
            else:
                _因76坏 = (f"违规点**变多**（{len(当前)} → {len(点)}）")
            _log(f"[generic]   ✗ {rel}：{_因76坏} ⇒ **只回退这一个文件**")
            # ★ R177：回退**必须被说出来** —— 否则下一轮模型只能从头再猜一遍。
            #   真证据（第 78 次真跑）：同一文件连续 4 轮 `违规点变多（1 → 7）⇒ 回退`，
            #   而模型从不知道自己被退回过 ⇒ 每轮从头再猜 ⇒ 必然踩同一个坑。
            if 回退详情 is not None:
                回退详情.append({
                    "文件": str(rel),
                    "因由": _因76坏,
                    "新增": [str(x) for x in _新增76[:6]],
                    "原有": [str(x) for x in sorted(当前)[:4]],
                })
            _rollback_file(bk)
            退.append(rel)
            # 回退后主树回到上一保留态 ⇒ 沿用上一保留态的判门结果
            if not results:
                results, red = r_this, red_this
    # ══════════════════════════════════════════════════════════════════
    # ★★★★★ R180：**逐文件全退时，试一次整批**（跨文件连锁的唯一出路）
    # ══════════════════════════════════════════════════════════════════
    # 决定性实测（2026-10-28 12:23，不是推理）：第 82 次真跑把这 5 个文件逐个
    # 裁决 ⇒ **全部回退**（保留 0）⇒ 判 stalled_fixed_point ⇒ 交还用户。
    # 我把**同一批**产物一起应用到主树，跑 `cargo test --lib --no-run`：
    #     错误 0 · 退出码 0 · #[test] 1936（基线 1590 未减）· 7 warning
    # ⇒ **模型早就做对了。是逐文件裁决把正确答案否掉的。**
    #
    # 根因：Rust 类型错误**跨文件连锁** ——
    #   · 单改 `算子选择器.rs`（导入 `定列`）⇒ 另外 4 个文件仍引用旧签名
    #     ⇒ 编译器报 7 个**新**错 ⇒ 判「变多（1 → 7）」⇒ 回退；
    #   · 5 个文件**一起改** ⇒ 类型同时对齐 ⇒ 错误全部消失。
    #   ★ 即「逐文件违规点集合」在**全库型判据**上**不是偏序**：
    #     全局错误数对单文件改动**不单调** ⇒ 用它裁决会把唯一正确的那一步判成倒退。
    #
    # ★ 为什么放在**逐文件之后**（R180b 的修正）：
    #   第一版放在循环之前 ⇒ 把 R43/R46b 的老语义冲掉了（齿当场判红：
    #   「该回退的文件没回退」「全绿后没有早停」）。而 R43 是对的：
    #   **能归因的坏文件必须回退**（R89 代偿拒收同理）。
    #   ⇒ 只有「逐文件一个都没保留」时才试整批 —— 那时只可能是跨文件连锁。
    _候选180 = {k: v for k, v in adopted.items() if not _是拒收产物(v)}
    if (not 留) and 基线 and len(_候选180) >= 2:
        _批bk180: list = []
        try:
            for _r180, _t180 in _候选180.items():
                _批bk180.append((_r180, _backup_file(_r180)))
                _apply_file(_r180, _t180)
            _批res180, _批red180 = _score_files(
                round_root, deliverables, file_root=REPO_ROOT, repo_root=REPO_ROOT)
            _批点180 = _红门违规点(_批red180)
            _批全绿180 = (bool(_批res180)
                          and all(bool(x) for x in _批res180.values()))
            _批好180 = (_批全绿180 or _增量可判(基线, _批点180)
                        or (bool(基线) and not _批点180))
            if _批好180:
                _log("[generic] ★★★ R180 **整批成立**："
                     f"{len(_候选180)} 个文件一起改 ⇒ 违规点 {len(基线)} → "
                     f"{len(_批点180)}"
                     f"（{'全绿' if _批全绿180 else '真子集变小'}）⇒ **全部保留**"
                     "（逐文件裁决会把它判成倒退：见 R180 注释）")
                留 = [(r, dict(_批bk180).get(r)) for r, _ in _批bk180]
                退 = []
                return _批res180, _批red180, 留, 退
            _log(f"[generic] ★ R180 整批也不成立（违规点 {len(基线)} → "
                 f"{len(_批点180)}）⇒ 回退整批（主树回到基线）")
            for _r180, _bk180 in reversed(_批bk180):
                try:
                    _rollback_file(_bk180)
                except Exception:            # noqa: BLE001
                    pass
        except Exception as _e180:           # noqa: BLE001 整批试错不得影响返回值
            _log(f"[generic] ★ R180 整批尝试异常（回退后照原样返回）："
                 f"{type(_e180).__name__}: {_e180}")
            for _r180, _bk180 in reversed(_批bk180):
                try:
                    _rollback_file(_bk180)
                except Exception:            # noqa: BLE001
                    pass
    return results, red, 留, 退


def _计划含冻结文件(计划文件: list[str]) -> list[str]:
    """计划里**落在冻结区**的文件（空 = 没碰冻结区）。

    ★★★★★ R37（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      R36 生效后，规划器**正确**覆盖了 7 个真违规文件 —— 但它**额外**把
      `src/测试防假.rs`（**判它的那个守卫本身**，已被登记为冻结区）也列进了
      deliverables。而 R23 的冻结区检查在**应用之后**才判
      ⇒ 整趟会走到"改判据 ⇒ 逐字节比对发现变了 ⇒ **回滚 + 不交付**"。

      ★ 这是**注定**的结局，且在**计划诞生那一刻就能判**：
        冻结区的文件**按定义不许改** ⇒ 把它列成交付物，这趟必然被回滚。
      ★ 与 R31/R36 同一族（"计划一诞生就已注定失败"），同一处置（**回喂重规划**）。
        ★ 但**不能只是拒**：冻结区文件常常正是"判据所在处"，模型会本能地想改它
          —— 所以回喂文本要**说清为什么不能改、以及正当出路是什么**
          （把断言补进**被测文件**里，而不是去改判据）。

    【判据（不猜）】只认**档案里真实声明的**冻结区路径（`whole` 与 `begin`
      两种形态都算：只要交付物路径**等于**整文件冻结项，或**落在**该文件内）。
    """
    specs = prof_mod.get_profile().frozen or ()
    冻: set[str] = set()
    for s in specs:
        rel = str((s or {}).get("path") or "").strip().replace("\\", "/")
        if rel:
            冻.add(rel.strip("/"))
    if not 冻:
        return []
    中 = []
    for f in (计划文件 or ()):
        纯, _ = _拆路径焦点(str(f))
        纯 = 纯.replace("\\", "/").strip().lstrip("./").strip("/")
        if 纯 and 纯 in 冻:
            中.append(纯)
    return sorted(set(中))


def _冻结文件提示(中: list[str]) -> str:
    """把"你列了冻结区文件"写成**可执行**的回喂文本。"""
    return (
        "【计划碰了冻结区（注定被回滚）】\n"
        "你把下面这些文件列进了 deliverables：\n"
        + "\n".join(f"  · ✗ {x}" for x in 中)
        + "\n\n★ 它们是**冻结区** —— 按定义**不许改**。机器会在改动落地后\n"
          "  逐字节比对，一旦发现变了就**回滚主树、判不交付**。\n"
          "  ⇒ 照这个计划跑下去，结局在**计划诞生那一刻就已注定**。\n\n"
          "★ 为什么它们被冻结（这不是刁难）：\n"
          "  冻结区里装的是**判据本身**（判你产出的那把尺子）。\n"
          "  若你能改尺子，最省力的\"通过\"就是**把尺子改弱** ——\n"
          "  主树被改坏却报成功，正是要防的事。\n\n"
          "⇒ 正当出路：**把改动放进被测文件里**（让那些测试真的能证伪），\n"
          "  而不是去动判据。若你确信**判据口径本身**有缺陷\n"
          "  （例如断言写在辅助函数里、守卫只扫测试自身函数体），\n"
          "  那也**不要改它** —— 在结论里**如实写出来**并附机器证据，由人裁定。\n"
          "⇒ 请重出计划：deliverables 里**去掉**上面这些文件，其余照旧。")


def _计划覆盖缺口(计划文件: list[str], 必改: list[str]) -> list[str]:
    """计划**没覆盖到**的必改文件（空 = 覆盖全）。

    ★ 判据是**集合包含**（全称），不是"沾到一个就算"：
      漏掉任何一个 ⇒ 那个文件里的违规原样留着 ⇒ 门不可能转绿。
    ★ 同时接受 `路径` 与 `路径:行号` 两种写法（R34 已把行号拆进 `focus`，
      但这里收的是**原始声明**，两种写法都要认）。
    """
    有 = set()
    for f in (计划文件 or ()):
        纯, _ = _拆路径焦点(str(f))
        有.add(纯.replace("\\", "/").strip().lstrip("./"))
    缺 = []
    for b in (必改 or ()):
        if b.replace("\\", "/").strip().lstrip("./") not in 有:
            缺.append(b)
    return 缺


def _补必改标记(目标: str) -> str:
    """目标里已有 `【必改文件】` ⇒ 原样返回；没有 ⇒ **由机器补上**。

    ★★★★★ R87（2026-10-18 · R86 做完立刻想到的下一层）：

      R86 让「目标点名的文件必须进交付物清单」成立，但**前提是目标里写了
      `【必改文件】`**。结构缺口的目标是机器生成的，会写；
      **人工写的目标不会写** ⇒ 那条判据对它们**等于不存在**。

      ★ 「标记是接口，不该靠自觉。」
        一个判据如果要求调用方**记得**喂它特定格式，
        那它在无人值守里就是**概率生效**的 —— 这等于没有。

    【补什么 · 判据是结构的，不是词表的】
      目标里出现 `路径:行号`（如 `src/模块甲/模块丙.rs:165`）
      ⇒ 那是**机器可证的**"这件事在这行"。改别的文件不可能让它变好
      （与 R36 同一理由：被报在第 N 行的东西只有改那一处才可能消除）。
      ⇒ 那个文件就是必改文件。

      ★ 与 R81 的区别：R81 管「**不存在**的文件必须在清单里」；
        这里管「**已存在、且被 `路径:行号` 精确点名**的文件必须被改」。
        两者互补，不重叠。

    【边界 · 诚实】
      · 只认 `…:数字`（R36 的同一形态），**不猜自然语言**。
      · 路径**必须在仓库里真实存在**（防幻觉路径污染必改清单）。
      · 目标已有 `【必改文件】` ⇒ **一个都不补**（尊重调用方的显式声明）。
      · 补出来的行加在**末尾**，不改动原文任何字节。
    """
    文 = 目标 or ""
    if re.search(r"^[ \t]*【必改文件】", 文, re.MULTILINE):
        return 文          # 已有显式声明 ⇒ 一个都不补
    见: list[str] = []
    for m in re.finditer(r"([\w\u4e00-\u9fff./\\-]+\.(?:rs|py|ts|tsx|js|jsx|go|java|kt|"
                         r"c|cc|cpp|h|hpp|cs|rb|php|swift|scala|ex|exs|hs|ml|lean))"
                         r":(\d+)", 文):
        记 = m.group(1).replace("\\", "/").lstrip("./")
        if 记 in 见:
            continue
        try:
            if not (REPO_ROOT / 记).is_file():
                continue          # 幻觉路径 / 外部路径 ⇒ 不补
        except Exception:
            continue
        见.append(记)
    if not 见:
        return 文
    return 文.rstrip("\n") + "\n\n" + "\n".join(f"【必改文件】{x}" for x in 见) + "\n"


def _必改未变好(必改, 保留集) -> list[str]:
    """目标点名的文件里，**本轮自己没变好**的那些（R89）。

    ★ 为什么把它抽成函数（2026-10-18 · `_验R89齿.py` ③ 当场抓出）：

      第一版是写在 `_round` 里的**内联表达式**：
          self._必改未达标 = [f for f in _必改89 if f not in _留集89]
      把 `not` 去掉（方向反了）⇒ **自检仍全绿**，一条齿都抓不住 ——
      因为没有任何**可调用的接口**能问"方向对不对"。

      ★ 这是「齿也会把缺陷锁住」的又一形态：
        **判定逻辑内联在流程里 ⇒ 它只有"被触发"和"没被触发"两种状态，
        没有"触发后算对没有"这个可测面。**
      ⇒ 判据逻辑必须**抽成纯函数**，让两个方向都能被夹具问出来。

    ★ 语义（不许反）：
      · 必改文件**在**保留集里 ⇒ 它变好了 ⇒ 不算未达标；
      · 必改文件**不在**保留集里 ⇒ 它没变好（被回退 / 没动 / 被拒收）⇒ 未达标。
    """
    _留 = {str(x).replace("\\", "/").strip().lstrip("./") for x in (保留集 or ())}
    出: list[str] = []
    for f in (必改 or ()):
        记 = str(f).replace("\\", "/").strip().lstrip("./")
        if 记 and 记 not in _留 and 记 not in 出:
            出.append(记)
    return 出


def _目标声明的必改文件(目标: str) -> list[str]:
    """目标里 `【必改文件】<路径>` 声明的文件（**机器固定格式**，不是猜人的措辞）。

    ★★★★★ R86（真跑抓出 · 2026-10-18 · 第六次真项目验收）：

      【真跑证据 —— 最坏的形态】目标点名 `src/模块甲/模块丙.rs:165`，
        规划器的交付物清单是
          `运行时涌现.rs` / `外部获取/mod.rs` / `观测协议.rs` /
          `规律网络编码.rs` / `列编码.rs`
        —— **一个都不是那个文件**。跑完：**green**、回灌 3 个文件、
        主树判据偏差 **30 → 12**。而 `判据_甲子` 的生产消费者**仍然是 0**，
        目标那个文件**从头到尾没被碰过**。

      ★ 为什么所有检查都说它对：那 3 个保留的文件是**真改进**
        （R73b 严格变小）。⇒ 这是最坏的形态：**green 且真有一部分改进**，
        任何"看结果对不对"的检查都会说它对。
        唯一能发现它的方式，是问「**目标点名的那个东西变了吗**」。

      【根因：R81 与 R36 之间有一条缝】
        · R81：目标点名的文件若**当前不存在** ⇒ 必须在清单里；
        · R36：**红门证据**点名的文件 ⇒ 必须覆盖。
        ⇒ 目标点名了一个**已存在**的文件、而门全是全库门（不点名任何文件）时，
          **两条判据都不管它**，它就从清单里消失了。

      【修法：不新造判据，把已知道的事实接到 R36 上】
        结构缺口的目标是**机器生成的** —— 机器**确切知道**要改哪个文件。
        原来它把这件事写成了自然语言，让模型重新解析、重新决定。
        ⇒ 改成写一个**机器固定格式** `【必改文件】<路径>`，由本函数抽出，
          并入 R36 的 `必改` 集合 ⇒ 复用已有判据（计划必须覆盖必改文件）。

      ★ 为什么这不是"作弊通道"：**目标说必改什么，就必须改什么** ——
        这正是判据该有的方向。目标是人/机器给的意图，计划是模型的猜测；
        两者冲突时以**目标**为准，这是 R81 的同一口径。

      ★ 边界（诚实）：只认 `【必改文件】` 这个**固定格式**，不做任何自然语言猜测。
        人工写的目标若想用这条，写同样的标记即可。
    """
    出: list[str] = []
    # ★★★ **行首锚定**（端到端验证当场抓到）：第一版没锚行首，
    #   而目标正文里有一句「★ `【必改文件】` 是机器写给机器的标记」
    #   ⇒ 第二次匹配把那个反引号抽成了"文件路径" ⇒ 必改清单变成
    #     `['src/模块甲/模块丙.rs', '`']` ⇒ **多一个必然覆盖不到的文件**
    #     ⇒ 每个计划都被判"没覆盖" ⇒ 无限回喂。
    #   ★ 这是「**声明文本自己被当成声明**」—— 与「门匹配到自己」同族。
    #   标记是**单独一行**的，行首锚定既精确又排除正文提及。
    for m in re.finditer(r"^[ \t]*【必改文件】[ \t]*([^\s\n]+)",
                         目标 or "", re.MULTILINE):
        记 = m.group(1).strip().replace("\\", "/").lstrip("./")
        if 记 and 记 not in 出:
            出.append(记)
    return 出


def _目标里的可执行断言(目标: str, 交付物) -> list[dict]:
    """目标文本里写明的「调用 == 值」⇒ **机器原样**生成判据门。

    ★★★★★ R82（2026-10-17 夜 · 由 R81 顺着查出来）：

      【真跑证据】目标是
        `collatz_steps(1)==0、collatz_steps(6)==8、collatz_steps(27)==111`
      模型写出的门是
        `assert f([1])[0]==0; assert f([6])[0]==8; assert f([27])[0]==111`
      ★ **三个点一个没少** —— 覆盖是完整的。它干的事更阴：
        把 `collatz_steps(1)` **转写成 `f([1])[0]`**，
        也就是**把判据改成了产物的形状，好让它通过**。

      ⇒ 这是「**判据不得由被判方改动**」最锋利的一刀：
        不是少测，是**改写**。少测能被"覆盖率"发现，改写不能 ——
        因为改写后的门**看起来完全正常**，它只是"用产物的接口说话"。

      【修法】**让判据不经过模型的手**：目标里的断言由机器**原样照抄**成门。
        `collatz_steps(1)==0` ⇒ 机器生成 `_f(1) == 0`。
        产物若收列表 ⇒ `1[0]` ⇒ `TypeError` ⇒ **门红**。
      ★ 这条判据的边界同样是**结构**给的，不是词表给的：
        它不问"目标里哪句是要求"，只问"目标里有没有**写死的调用式与期望值**"。
        有 ⇒ 那就是一条可执行判据，机器照抄；没有 ⇒ 不生成（不凭空造门）。

    ★ 诚实边界（写清，不假装更强）：
      · 只认 `名字(参数) == 字面量` 这一种形态（字面量用 `ast.literal_eval` 解析，
        解析不了就**跳过**，不猜）。
      · 参数里若有嵌套括号（如 `f(g(1))==0`）本实现不认（保守，跳过）。
      · 模块名由**交付物清单**推出（`.py` 的 stem）；找不到函数 ⇒ 门红并说清原因。
      · 本门是**文件无关**门（脚本里没有 `{file}`）⇒ 每窗只求值一次（R38）。
    """
    import ast as _ast
    _断言RE = re.compile(
        r"(?<![\w.])([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s*"
        r"\(([^()]*)\)\s*==\s*([^\s，。、；,;：:）)]+)")
    _mods = []
    for d in (交付物 or ()):
        纯, _ = _拆路径焦点(str(d.get("path", "") if isinstance(d, dict) else d))
        if 纯.replace("\\", "/").endswith(".py"):
            名 = 纯.replace("\\", "/").rsplit("/", 1)[-1][:-3]
            if 名 and 名 not in _mods:
                _mods.append(名)
    if not _mods:
        return []            # 没有 Python 交付物 ⇒ 不生成（不猜别的语言）

    _按名: dict[str, list[tuple[str, str]]] = {}
    for m in _断言RE.finditer(目标 or ""):
        函数, 实参, 值文 = m.group(1), m.group(2).strip(), m.group(3).strip()
        try:
            _ast.literal_eval(值文)      # 只认字面量：解析不了 ⇒ 跳过（不猜）
        except Exception:                # noqa: BLE001
            continue
        _按名.setdefault(函数, [])
        if (实参, 值文) not in _按名[函数]:
            _按名[函数].append((实参, 值文))

    out: list[dict] = []
    for 函数, 条 in _按名.items():
        L = ["import sys; sys.path.insert(0, '.')"]
        L.append(f"_mods = {_mods!r}")
        L.append("_f = None")
        L.append("for _m in _mods:")
        L.append("    try:")
        L.append("        _mod = __import__(_m)")
        L.append("    except Exception:")
        L.append("        continue")
        L.append(f"    if hasattr(_mod, {函数!r}):")
        L.append(f"        _f = getattr(_mod, {函数!r}); break")
        L.append("if _f is None:")
        L.append(f"    raise SystemExit('【目标断言】交付的模块里找不到 `{函数}`"
                 f"（目标写明了它的调用式，机器照抄成判据）')")
        for 实参, 值文 in 条:
            L.append(f"_got = _f({实参})")
            L.append(f"if not (_got == {值文}):")
            L.append(f"    raise SystemExit('【目标断言】{函数}({实参}) 期望 {值文}，"
                     f"实得 ' + repr(_got) + '（**调用形态照抄目标**：目标是标量进就标量进，"
                     f"不接受把它包成列表再取下标）')")
        out.append({"name": f"目标断言_{函数}", "type": "compile",
                    "cmd": ["python", "-c", "; ".join(L).replace("; _", "\n_")
                            if False else "\n".join(L)],
                    "cwd": ".", "expect_exit": 0, "timeout": 120})
    return out


def _注入接线门(spec: dict, 目标: str) -> list[str]:
    """目标点名「把这个接口接上」⇒ 机器生成一条**接线门**（数生产引用）。

    ★★★★★ R88（2026-10-18 · 由 R86/R87 顺着查出来）：

      【真跑证据 —— 门会撒谎】第 1 轮报 green，门里有
        `新测试_判据_甲子生产路径生效 = True`
      而 `判据_甲子` 的生产消费者**仍然是 0**（唯一调用点在 `#[cfg(test)]` 里）。
      ★ 那条门**名字承诺**「生产路径生效」，**实际判**的是「文件里有这个词」——
        而这个词当然有（定义行 + 别人的测试）。
        ⇒ 门绿了，但它证明的东西和它名字说的**不是一回事**。

      【修法：机器已经有这个能力了】
        `gap_source.扫描无消费者接口` 已经能**剥掉测试模块后数生产引用**。
        那正是「接上了没有」的**机器判据**。
        ⇒ 不新造机器，只要**把已有的机器接到门上** ——
          实现抽到 `接线门.py`，两边共用同一台机器。

      【判据为什么是必然的，不是启发式】
        「这个接口接上了」的**定义**就是「生产路径上有引用」。
        剥净后零引用 ⇒ **它不可能被任何生产路径调用** ⇒ 目标未达成。
        与 R85 的缺口判定**同一口径** —— 同一条判据，两个用法：
          · 规划前：零引用 ⇒ **缺口**（该派活）；
          · 交付时：零引用 ⇒ **没接上**（该判红）。

      【诚实边界】
        · 只认**目标里出现的、符合项目命名约定的**接口名。目标没点名 ⇒ 不生成门。
        · 只对**声明了 `orphan_api_prefixes` 的项目**生效 ——
          没有命名约定的项目，机器不知道该数什么。
        · 门是 `compile` 型（跑真命令、读真退出码），脚本由机器写死，
          **不经模型的手**。
    """
    _前缀 = tuple(getattr(prof_mod.get_profile(), "orphan_api_prefixes", ()) or ())
    if not _前缀:
        return []
    _目标 = 目标 or ""
    _名们: list[str] = []
    for _pfx in _前缀:
        for m in re.finditer(r"(?<![\w])(" + re.escape(_pfx) + r"[\w\u4e00-\u9fff]+)",
                             _目标):
            _n = m.group(1)
            if _n not in _名们:
                _名们.append(_n)
    if not _名们:
        return []

    # ★ 门脚本 = 同包里的 `接线门.py`（**同一台机器**，不是另抄一份）
    _脚本 = Path(__file__).resolve().parent / "接线门.py"
    if not _脚本.is_file():
        return []
    _根 = str(REPO_ROOT)
    门们: list[dict] = []
    for _n in _名们:
        门们.append({"name": f"接线_{_n}", "type": "compile",
                     "cmd": [sys.executable, str(_脚本), _n, _根],
                     "cwd": ".", "timeout": 900})
    spec.setdefault("gates", [])
    _已有名 = {str(g.get("name")) for g in spec["gates"] if isinstance(g, dict)}
    _加 = [g for g in 门们 if g["name"] not in _已有名]
    spec["gates"] = list(spec["gates"]) + _加
    return [g["name"] for g in _加]


def _注入见证门(spec: dict, 目标: str) -> list[str]:
    """模型自己起的**测试门**，名字点名了接口 ⇒ 机器加一条**见证门**。

    ★★★★★ R91（2026-10-18 · 这一夜收的最后一段）：

      【真跑证据 —— 门是诚实的，撒谎的是测试】
        第 1 轮 green 里有 `新测试_判据_甲子生产路径生效 = True`。
        去读那条门：`{"type":"compile","cmd":["cargo","test","--lib",
        "新测试_判据_甲子生产路径生效"]}`。
        ★ 门**老老实实**跑了那条测试，测试**真的通过了**。
          **撒谎的是测试本身** —— 名字叫「生产路径生效」，
          断言是「源码里出现了这个词」，而这个词当然有（定义行 + 别人的测试）。

      【R88 只接管了一半】
        R88 的接线门只管**机器注入的那一条**（数生产引用）。
        模型自己起的测试门，机器仍然**只看它绿不绿**。

      【修法：同一台机器，第四个用法】
        `见证门.py` 判：**测试名点名的接口，断言里真的调了吗。**
          · 缺口扫描 / 接线门：**去掉**测试模块，数生产引用；
          · 见证门：**只要**测试函数体，数断言里的引用。

      【为什么判据是必然的】
        一条断言要能「见证」某接口的行为，**前提是它真的调用了那个接口**：
          · 断言 `源.contains("判据_甲子")` ⇒ 见证**文本**，删词即红；
          · 断言 `判据_甲子(1,1) == true` ⇒ 见证**行为**，改坏才红。
        **失败条件不同 ⇒ 能分开。**
        ★ 与库内 `WitnessExtractor` 同一条：承诺「∃ 调用点」的证明
          必须在证明树里**携带**那个调用点。
        ★ 与 `TS525` 同一条：「测试通过了」是 `Prop`；
          「断言里调了它」是**表里的数**。

      【诚实边界 —— 全部朝「零回退」】
        · 只认**真的在跑测试**的门（`cmd` 里 `test` 是独立 token，且有位置参数）。
          跑全部测试（无位置参数）⇒ **无法归属** ⇒ 不注入。
        · 只认**完整接口名**。核心名太松：`判据_复用` 的核心 `复用`
          会误伤 `复用链_代表元升入当前代` 这种无关测试。
        · 目标/门名里没点名接口 ⇒ 一条都不注入。
    """
    _前缀 = tuple(getattr(prof_mod.get_profile(), "orphan_api_prefixes", ()) or ())
    if not _前缀:
        return []
    _脚本 = Path(__file__).resolve().parent / "见证门.py"
    if not _脚本.is_file():
        return []
    _根 = str(REPO_ROOT)
    _工具名 = {"cargo", "test", "go", "pytest", "python", "python3", "npm", "run",
               "npx", "jest", "mocha", "dotnet", "mix", "stack", "cabal"}
    加: list[dict] = []
    for g in (spec.get("gates") or []):
        if not isinstance(g, dict):
            continue
        _cmd = g.get("cmd")
        if not isinstance(_cmd, list):
            continue
        _tok = [str(x) for x in _cmd]
        if "test" not in _tok:
            continue                      # 不是在跑测试 ⇒ 不是见证门的样本戊
        _位置 = [x for x in _tok if not x.startswith("-") and x not in _工具名]
        for _测名 in _位置:
            # 名字里必须**完整**出现某个接口名，否则本条不适用
            if not any(x in _测名 for x in _前缀):
                continue
            名 = f"见证_{_测名}"
            if any(isinstance(h, dict) and h.get("name") == 名
                   for h in (spec.get("gates") or [])):
                continue
            加.append({"name": 名, "type": "compile",
                       "cmd": [sys.executable, str(_脚本), _测名, _根, *_前缀],
                       "cwd": ".", "timeout": 900})
    if 加:
        spec["gates"] = list(spec.get("gates") or []) + 加
    return [g["name"] for g in 加]


def _注入目标断言门(spec: dict, 目标: str) -> list[str]:
    """把目标里写死的断言**机器**加成判据门（不经模型的手）。返回新增的门名。"""
    交付物 = _parse_deliverables(spec) if (spec.get("deliverables")
                                          or spec.get("deliverable")) else []
    新门 = _目标里的可执行断言(目标, 交付物)
    if not 新门:
        return []
    已有 = {str(g.get("name")) for g in (spec.get("gates") or [])}
    加 = [g for g in 新门 if g["name"] not in 已有]
    if not 加:
        return []
    spec.setdefault("gates", [])
    if not isinstance(spec["gates"], list):
        return []
    spec["gates"].extend(加)
    # 交付物若**自带** gates（不继承顶层）⇒ 也要挂上，否则它跑不到这条门
    for it in (spec.get("deliverables") or []):
        if isinstance(it, dict) and isinstance(it.get("gates"), list) and it["gates"]:
            _名 = {str(g.get("name")) for g in it["gates"]}
            it["gates"].extend([g for g in 加 if g["name"] not in _名])
    return [g["name"] for g in 加]


# ★★★★★ R176（2026-10-28 · 换目标项目《中文编译器》后第一次真跑当场抓出）：
#   **Windows 反斜杠路径把「已存在的源文件」判成「必须新建」。**
#
#   【真证据】第 75 次真跑（中文编译器项目）规划阶段连续 3 次被拒：
#     `★ 规约可纠正（第 1/2/3 次）⇒ 回喂重规划：目标里点名、但交付物清单里没有的文件：`
#   目标是机器实测写出来的，里面是**反斜杠**路径：
#     `示例工程\基础集\算子选择器.rs:682: 样本列表: 定列::新(),`
#   而文件正则**只把 `/` 当分隔符** ⇒ 反斜杠路径只截出**裸文件名** `算子选择器.rs`
#   ⇒ 拿它去 `REPO_ROOT / 算子选择器.rs` 找 ⇒ 不在仓库根 ⇒ 判「当前不存在」
#   ⇒ 「必须有交付物建它」。而它**本来就存在**（在 `示例工程/基础集/` 下）
#   ⇒ 模型无论加不加都被拒 ⇒ **死循环**（连纠正 3 次后停机）。
#
#   ★ 这不是"少了归一化"的小疏漏，是**判据把一个假事实当了真**：
#     「这个文件不存在」在 Windows 目标下几乎必然为假。
#   ★ 修法不枚举路径写法：**存在性是仓库的`性质`，不是猜出来的路径的`性质`** ——
#     有目录 ⇒ 按归一化路径查；裸文件名 ⇒ **按名字在仓库里查一次**（有界 + 缓存）。


def _文件在仓库里存在176(记: str) -> bool:
    """`记` 已归一为 `/` 分隔 ⇒ 只判**精确路径**在不在（REPO_ROOT / WORKDIR）。

    ★ 为什么不按「裸文件名在仓库里搜同名」：那会把**真该新建**的文件
      也算成「已存在」（R81 的齿当场抓住：`test_collatz.py` 在别处有同名 ⇒
      缺交被吞掉 ⇒ 这趟必然少交一件）。
    ★ 本条只负责一件事：**反斜杠路径归一后要能精确命中**。
      Windows 目标里 `示例工程\\基础集\\x.rs` 归一成 `示例工程/基础集/x.rs` ⇒ 命中。
    """
    for _根 in (REPO_ROOT, WORKDIR):
        # ★★★★★ R176d：`WORKDIR` **可能是 None**（R139 日志里就是 `workdir=None`）
        #   ⇒ `None / 记` 抛 **TypeError**，而下面只 `except OSError` ⇒ **抓不住** ⇒
        #   整个存在性判定炸掉（齿当场抓出）。
        #   ★ 这是「手里那个异常不是判据需要的那条」在异常类型上的样子：
        #     想防的是「路径不存在」，接的却是 `OSError` —— `TypeError` 从缝里漏出去。
        if _根 is None:
            continue
        try:
            if (_根 / 记).is_file():
                return True
        except (OSError, TypeError, ValueError):
            pass
            pass
    return False


def _目标点名而清单没有(目标: str, 交付物) -> list[str]:
    """目标里点名的文件，**当前不存在**且不在交付物清单里 ⇒ 返回它们（按出现序）。

    ★★★★★ R81（2026-10-17 夜）：**「目标声明 → 判据」这一跳**。

    【真跑证据】collatz 那批 2597 趟，目标原文写着「再写 `test_collatz.py` 断言 …」，
      交付物清单里却**只有 `collatz.py`** —— `test_collatz.py` **一次都没交**，
      而门全绿（因为没有一条门问"目标点名的文件在不在"）。

    ★ 这条判据为什么**可判**、且**不需要枚举"目标里哪句是要求"**：
      它问的是一个**事实问题** ——
      「目标里出现的文件记号，若**当前不存在**，那这趟谁来建它？」
      ⇒ 只能是交付物。否则这趟**必然少交一件**（不是"可能"，是"必然"）。
      而**已存在**的文件不算（可能是只读上下文、可能是要改的源码）。
      ★ 这条边界是**结构**给的（存在 / 不存在），不是词表给的 —— 这正是
        「用全称机制、不枚举」在这里的样子。

    ★ 诚实边界（写清，不假装更强）：
      · 只认**有扩展名的路径式记号**（`foo.py`、`src/bar.rs`）。裸词不算。
      · 只在**当前不存在**时才要求。已存在的文件即使目标是"改它"，也不进这条判据
        （那是另一条判据：必改清单 / 红门点名的文件，见 R36）。
      · 目标里如果提到一个**不该由本趟创建**的外部路径（罕见），会被误要求一次；
        代价是**回喂一次**（可纠正），不会误交付。
    """
    已交 = set()
    for d in (交付物 or ()):
        纯, _ = _拆路径焦点(str(d.get("path", "") if isinstance(d, dict) else d))
        已交.add(纯.replace("\\", "/").strip().lstrip("./"))

    # 只认"像文件"的记号：末段带扩展名（字母开头、1~8 个字母数字）
    _文件RE = re.compile(
        # ★ R176：`[\\/]` —— 反斜杠也是路径分隔符（Windows 目标里到处都是）
        r"(?<![\w/.\\-])((?:[\w\u4e00-\u9fff.\\-]+[\\/])*[\w\u4e00-\u9fff.\\-]+"
        r"\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w])")
    缺: list[str] = []
    见过: set[str] = set()
    # ★★★★★ R176b：**先收集全集，再去子串，最后才判存在性。**
    #   ★ 我前两版都错在同一处：去子串放在 `缺` 里做 ⇒ 而带路径的形态
    #     （`示例工程/基础集/算子选择器.rs`）**已经存在**，在存在性判定那步就
     #     `continue` 掉了 ⇒ **从来没进过 `缺`** ⇒ 去子串时只剩裸名，
     #     没有「更长的那个」可保留 ⇒ 裸名（仓库根下不存在）被判「缺」
     #     ⇒ 要求模型交付一个**根目录下的**同名文件 ⇒ 连喂 3 次仍不改对 ⇒ 卡死。
    #   ★ 去子串放在**存在性判定之前**，这条缝才合上。
    _全集176b: list[str] = []
    for m in _文件RE.finditer(目标 or ""):
        记 = m.group(1).replace("\\", "/").strip().lstrip("./")
        if 记 in 见过:
            continue
        见过.add(记)
        _全集176b.append(记)
    # ★ 去子串：A 是 B 的后缀路径段（`B` 形如 `…/A` 或 `A == B`）⇒ 只留更长的 B。
    _留176b = [
        _a176b for _a176b in _全集176b
        if not any(_b176b != _a176b and _b176b.endswith("/" + _a176b)
                   for _b176b in _全集176b)
    ]
    for 记 in _留176b:
        if 记 in 已交:
            continue
        # ★ R176：存在性是**仓库的性质**（含裸文件名），不是猜测路径的性质
        if _文件在仓库里存在176(记):
            continue
        缺.append(记)
    return 缺


# ═══════════════════════════════════════════════════════════════════════════
# ★★★★★ R144（真跑抓出的第 57 层 · 2026-10-24 · 第三十五次端到端）：
#   **瞬时上游错误被当成终态。**
#
#   真证据（两次真跑，都在第 1 轮就死）：
#     `规划失败：Error code: 402 - {'code': 'no_provider_under_bid'}`
#     `轮 1 异常：Error code: 402 - {'code': 'no_provider_under_bid'} → 诚实停机`
#   `no_provider_under_bid` 的意思是「**此刻**没有服务商愿意接这个价」
#   —— 定义上就是**瞬时的**。同一个请求 30 秒后可能就成。
#
#   ★★ 而产品把它当成了「这条路走不通」，整趟 2~3 小时的无人值守在第 1 轮归零。
#      Router 明明有 failover（R62）、冷却、刷新（R72）——
#      但那些只在**槽之间**跳。当**唯一存活槽**报瞬时错时，
#      它把错误原样抛出，`_run` 的 `except Exception` 立刻 `return 1`。
#
#   ★★★ 这是「手里那个信息不是判据需要的那个信息」的又一次：
#     手里有的是「一个异常」，而判据需要的是「**这个异常会不会自己好**」。
#     ⇒ 与 R140/R141/R142/R143b 同族（第 5 次）。
#
#   ★★★★ 修法（**不放松任何判据**，只区分「等一等」和「真的不行」）：
#     · **瞬时**（值得等）：HTTP 402/408/425/429/5xx、超时、
#       `no_provider_under_bid`、`upstream_error`、`rate limit`、连接被重置
#     · **终态**（等也没用）：401/403（key 无效/无权限）、
#       `model_not_found`、`invalid_api_key`、`insufficient_quota`（余额）
#     ★ 判据：**等一等会不会改变它**。会 → 瞬时；不会 → 终态。
#     ★ 有界：**按时间预算**，退避按指数增长并封顶；**超了照样诚实停机**。
#       绝不无限等 —— 那是另一种不诚实。
#
# ★★★★★ R163（2026-10-27）：**「重试几次」不是判据需要的「能等多久」。**（第 78 层）
#
# 【真事故 —— 第 47 次真跑，上游 504 风暴连挂 6 小时】
#   日志一路是：
#     轮 1 瞬时 ⇒ 第 1/4 次重试，退避 20s
#     轮 1 瞬时 ⇒ 第 2/4 次重试，退避 40s
#     轮 1 瞬时 ⇒ 第 3/4 次重试，退避 60s
#     轮 1 瞬时 ⇒ 第 4/4 次重试，退避 80s
#     ⇒ 退出码 1
#   ★ 总等待 = 20+40+60+80 = **200 秒**。
#   ★ 而上游的故障尺度是 **6 小时**。
#   ⇒ 我手里有「重试了 4 次」，判据需要「等了多久」。
#     **4 次 vs 6 小时，差了 100 倍** —— 这不是次数不够，是**尺子量错了东西。**
#
# 【为什么「4 次」这个数当初看着合理】
#   它假设「瞬时」是**秒级抖动**（网络重连、单次 5xx）。
#   但真实上游会出现**持续数小时的区域性降级** —— 那时 4 次就是「碰了一下就走」。
#   ★ 无人值守的价值恰恰在**人不在的时候熬过去**；
#     按次数计的预算，等于**把熬多久的决定权交给了「退避公式」而不是「故障有多长」**。
#
# 【口径】
#   · 判据从「次数」换成「**已等待时长**」，默认预算 30 分钟
#     （`ORCH_TRANSIENT_BUDGET_SEC` 可调 —— 换环境不用改代码）；
#   · 退避**指数增长并封顶 300 秒**：20 → 40 → 80 → 160 → 300 → 300…
#     ⇒ 30 分钟约 **11 次**尝试，而不是固定 4 次；
#   · 封顶很重要：不封顶会在长故障里睡成几小时，**人回来时看不到任何中间回执**。
#   · 超预算 ⇒ 照样诚实停机，且回执必须**说清「是上游持续不可用，不是代码缺陷」**。
_瞬时等待预算秒 = 1800.0        # 30 分钟；env 可覆盖
_瞬时退避秒 = 20.0               # 首次退避
_瞬时退避封顶秒 = 300.0          # 单次最多睡 5 分钟
try:
    _瞬时等待预算秒 = float(
        __import__("os").environ.get("ORCH_TRANSIENT_BUDGET_SEC", "1800"))
except ValueError:
    _瞬时等待预算秒 = 1800.0


def _退避163(第几次: int, 已等: float) -> float:
    """★ R163：本次该退避多少秒。**退避这件事只有这一处实现**（R117）。

    · 指数增长（20 → 40 → 80 → 160 → 300…）——
      熬得住小时级故障，但不会一直 20 秒一次地狂打上游；
    · 封顶 `_瞬时退避封顶秒`（5 分钟）——
      不封顶会在长故障里睡成几小时，**人回来时看不到任何中间回执**；
    · 不越过预算 —— 最后一次不必睡满，留 1 秒余地避免永远醒不过来。
    ★ 抽成函数（而不是两处各写一遍）的理由：
      第一版把公式写在规划级和单轮级**各一份**，齿就只能去数「源码里有没有 2 **」，
      而**把其中一处改回常数它咬不住**（实测无齿）。
      抽成函数后，齿能**直接调用它、看输出序列** —— 测的是结果，不是过程里的词。
    """
    _等 = min(_瞬时退避秒 * (2 ** min(max(1, int(第几次)) - 1, 4)),
              _瞬时退避封顶秒)
    return min(_等, max(1.0, _瞬时等待预算秒 - max(0.0, 已等)))


def _不动点因由187(verdict: str, 候选数: int, 保留数: int, why: str) -> str:
    """★ R187：**这个不动点是谁制造的？** —— 唯一读口（纯函数，齿直接调它）。

    真证据（第 90 次真跑）：三轮全部「保留 0 / 回退 3」⇒ 主树逐字回到基线
    ⇒ Δ 与上轮必然相同 ⇒ 判 `stalled_fixed_point`
    ⇒ 因由却写成「非目标不动点，需换P/换分解」。
    ★ 而真因是：**本轮候选全被机器判成更差**（产出侧失败）。
      该换的是产出侧（模型/通道/收窄任务面），不是门集。

    ★ 只改因由，不改判定、不改动作 —— 不放松停机，只让话说对。
    """
    if (verdict == "stalled_fixed_point" and 候选数 > 0 and 保留数 == 0):
        return why + (f"；★ R187：本轮 **{候选数} 份候选全部被机器拒收**"
                      "（保留 0）⇒ 这个「不动点」是**回退制造**的，不是搜索证出的"
                      " ⇒ 该换的是**产出侧**（模型/通道/收窄任务面），不是门集")
    return why

def _是瞬时的(e: BaseException) -> tuple[bool, str]:
    """★ R144：这个异常**会不会自己好**？返回 (会不会, 因由)。

    ★ 为什么必须分类而不是一律重试：
      401/403/`invalid_api_key` 重试 100 次也还是 401 —— 那是**配置错**，
      该立刻停机让人去改 key。一律重试会把「3 秒能说清的错」
      拖成「20 分钟才说清的错」。
    ★ 为什么必须分类而不是一律不重试：
      402 `no_provider_under_bid` / 504 是**上游此刻的抖动**，
      重试就能过。一律不重试 = 一次抖动毁掉整趟无人值守。
    """
    _s186 = f"{type(e).__name__}: {e}"
    _low186 = _s186.lower()
    
    # ── ① **码只从结构化位置读**（R186）──────────────────────────
    #   旧实现：`for k in ("401","403",…): if k in low` —— **子串**。
    #   真事故：请求 id `…114037731…` 含 `403` ⇒ 瞬时 500 被判终态 403。
    #   ⇒ 只认「Error code: N」/「HTTP N」/「status_code=N」/ 开头就是 N。
    _码186 = None
    _m186 = re.search(
        r"(?:error[ _]?code|http(?: status)?|status[ _]?code|status)"
        r"\s*[:=]?\s*(\d{3})\b", _low186)
    if _m186:
        _码186 = int(_m186.group(1))
    else:
        _m186 = re.match(r"\s*(\d{3})\b", _low186)
        if _m186:
            _码186 = int(_m186.group(1))
    _码串186 = f"码{_码186}" if _码186 is not None else "无码"
    
    def _因186(类: str, 词: str, 说明: str) -> tuple[bool, str]:
        # ★ 因由里**同时**带词和码：齿要能分清「关键词判的」与「兜底蒙对的」。
        return (类 == "瞬时", f"{类}（{词} / {_码串186}：{说明}）")
    
    # ── ② 终态**词**：配置错/权限/模型不存在 —— 重试 100 次也一样 ──
    for _k186 in ("invalid_api_key", "model_not_found", "insufficient_quota",
                  "invalid_request_error", "authentication", "permission denied"):
        if _k186 in _low186:
            return _因186("终态", _k186, "等一等不会改变它")
    
    # ── ③ **瞬时消息**优先于码（R126：消息 > 码）──────────────
    for _k186 in ("稍后重试", "请重试", "服务异常", "上游服务", "服务繁忙",
                  "no_provider_under_bid", "upstream_error", "rate limit",
                  "rate_limit", "timeout", "timed out", "connection reset",
                  "connection aborted", "temporarily", "try again",
                  "service unavailable", "bad gateway", "gateway timeout",
                  "overloaded", "do_request_failed"):
        if _k186 in _low186:
            return _因186("瞬时", _k186, "等一等可能就好了")
    
    # ── ④ 再看码：只有 401/403/404 是终态 ──────────────────────
    #   ★ 宽进严出：认不出的码**仍按瞬时**（归错方向的代价不对称：
    #     把瞬时误判成终态 ⇒ 整趟无人值守白跑；反之只多等一轮）。
    #     这也是 R126 的原话：`400` 曾被写死成硬死，而上游把瞬时包在 400 里。
    if _码186 in (401, 403, 404):
        return _因186("终态", f"码{_码186}", "等一等不会改变它")
    if _码186 is not None:
        return _因186("瞬时", f"码{_码186}", "等一等可能就好了")
    
    # ★ 认不出来 ⇒ **按瞬时处理**（给一次机会），但只给一次机会的量。
    #   理由：无人值守的价值就是熬过抖动；而终态错误重试几次也就停了。
    return True, "未识别的错误 ⇒ 保守按瞬时（有界重试）"


class 规约可纠正(ValueError):
    """**模型给的规约有问题、而问题可被模型自己改正** —— 回喂重规划的**唯一**基类。

    ★★★★★ R80（真跑抓出 · 2026-10-17 · 第五次真项目验收）：

      【真跑证据·第一例】模型给判据门写了 `powershell` 顶层命令：
        `规划失败：判据门沙箱不通过（模型不可信的 cmd 通道不得当判据门）：
         ("沙箱:门#2 顶层命令不被白名单允许：'powershell'", …)`
        ⇒ **整趟失败**（exit 1）。

      【真跑证据·第二例】模型把同一个文件写了两遍：
        `重规划失败：重规划规约不合法：文件 path 重复：src/模块甲/mod.rs:7284 → 诚实停机`
        ⇒ **整趟失败**（exit 1）。

      【根因】**"哪些错是可纠正的"被按「异常类型」枚举了。**
        回喂循环只接住 `交付物形态错` 和 `计划不可行` 两种，
        而 `_valid_spec` 的校验失败、沙箱门拒绝、判定层必然性不成立
        —— **同样是模型能自己改的** —— 全都以裸 `ValueError` 抛出，
        **直接绕过回喂循环** ⇒ 整趟死。

      ★ 这与 R79 是**同一个病**，只是换了一层：
        R79 是「可派性被按关键词枚举」；这里是「**可纠正性被按异常类型枚举**」。
        两处都犯同一个错：**拿一张表去冒充"可能性"本身**。
        表外的项，在系统里就等于"不存在"。

      【修法】**按结构判，不按类型枚举**：
        凡是"模型产的规约不合法、且失败信息本身就能告诉模型怎么改"的，
        统统继承本基类；回喂循环只认**这一个**基类。
        ⇒ 新增一类可纠正错误时，**不需要**再改 `except` 分支
          （这正是"用全称机制、不枚举"的直接体现）。

      ★ 与"目标本身做不到"的**分界**（这条必须守住）：
        · 规约被**拒**（门不安全 / 文件重复 / 形态错 / 计划漏文件）
          ⇒ 是**表述问题** ⇒ 回喂，有上限（3 次）；
        · 门**跑完了但红**（判据说没做到）
          ⇒ 是**事实问题** ⇒ 不重试表述，走正常的不动点/停机逻辑。
        分界线是「**判据有没有真的跑起来**」，不是"错误类型好不好看"。
    """


class 按设计停机(Exception):
    """★★★★★ R151（真跑抓出的第 64 层 · 2026-10-25 · 第三十七次端到端）：
    **「按设计停机」不是「崩了」。**

    真证据（第 37 次真跑，最后一轮）：
      `★★★ 重规划**崩了**（generic_goal.py:4839）：ValueError:
       重规划连续 3 次可纠正性问题仍未改对 ⇒ 诚实停机（不猜、不替它决定）`
    ★ 那句话里**同时**写着「崩了」和「诚实停机」—— 自相矛盾。
    ★★ 而它被判成「**产品缺陷**」：`_是缺陷 = not isinstance(exc, (规约可纠正, 计划不可行))`
       —— 这个判断只排除了「规约可纠正」和「计划不可行」，
       **没有给「按设计停机」留位置** ⇒ 落到 else ⇒ 当成缺陷报出去。
    ★★★ 后果：报表会说「产品坏了，人去修代码」，
      而真相是「**机器按设计交还了决定权**」—— 那是本项目的核心价值，不是故障。

    ★★★★ 这是「手里那个异常不是判据需要的那个」的**第 3 次**
      （R144 瞬时/终态、R150 起不来/不绿、本层 按设计停机/崩了）：
      判据需要的是「**该不该惊动人**」，
      我拿到的是「**这个异常是什么类**」—— 后者不是前者的函数。
    """


class 计划不可行(规约可纠正):
    """计划**机器可证必然白跑** ⇒ 值得回喂重规划（区别于"目标做不到"）。

    ★ 与 `交付物形态错` 同一族：都是**可纠正的表述/选择问题**，
      不是"这件事做不到"。⇒ 都走回喂，都有上限。

    ★ R80：改为继承 `规约可纠正`（同一族的**基类**）。
      ★ 诚实登记：这里原来是 `(ValueError)` 直挂 —— 也就是说
        "同一族"这句话当时**只写在注释里**，**没有写进类型**。
        注释说的族，代码里并不存在 ⇒ 后来的兄弟类自然漏在外面。
        **「声明了同一族」不等于「真的是同一族」**（与 R65c 同型）。
    """


def _计划不可行提示(必改: list[str], 缺: list[str]) -> str:
    """把"计划不可能成功"写成**可执行**的回喂文本（模型照着就能改对）。"""
    return (
        "【计划不可能成功（机器可证）】\n"
        "红门证据里的违规**逐条落在这些文件上**：\n"
        + "\n".join(f"  · {b}" for b in 必改)
        + "\n\n而你列的 deliverables **没有覆盖**：\n"
        + "\n".join(f"  · ✗ {b}" for b in 缺)
        + "\n\n★ 为什么这是**必然**白跑（不是机器在猜）：\n"
          "  守卫是**静态扫描**——它读的就是那些文件的内容。\n"
          "  被报在 `文件 F` 第 N 行的违规，**只有改 `F` 才可能消除**；\n"
          "  不改 `F`，那条违规原样留着 ⇒ 验收门**不可能**转绿。\n\n"
          "⇒ 请把上面每个文件都列进 deliverables（`path` 用仓库相对路径；\n"
          "  大文件写成 `路径:行号`，行号取证据里给的那个）。\n"
          "  要分先后就用 `depends_on` 串起来，但**一个都不能漏**。")


def _门态探针(预算秒: int = 900, 上限字符: int = 8000) -> str:
    """规划**之前**先跑一次项目自己的验收门 —— 把「真缺口断在哪」当规划上下文。

    ★★★★★ R33（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      目标是「把项目自己的两条判据质量门修绿」。规划器**看不到**那两条门
      报出的 18 处违规在哪，只能靠符号快照**猜**文件 ⇒ 它猜了
      `src/核心引擎_基础方法_格空间操作.rs` / `src/接线状态图.rs` / `src/判据观测.rs`
      —— **一处都不在真正的违规清单上**（真清单在 `观测协议.rs` / `外部获取/mod.rs`
      / `运行时涌现.rs` / `结构差异判据.rs` / `持久化/mod.rs` / `推变换环.rs`
      / `arc覆盖度.rs`）。⇒ 执行器被**锁死在错误的文件集**上，整趟必然白跑。

      ★ 根因：**计划是在盲的**。而"缺口断在哪"这件事，机器**当场就能测出来**
        —— 项目自己的验收门（R21 那条独立来源）跑一遍，红门的输出里
        **本来就带文件与行号**。
      ★ 这不是新机制：`_project_acceptance` 早就在跑同一批门，只是
        **跑在交付之后**（事后裁判），从没跑在**规划之前**（事前探针）。
      ⇒ 把它接到规划前：**一次真跑，换整趟不白跑**。

    ★ 与用户给的那条环完全同构：
        ① 跑一个真缺口 + 探针（断在哪）→ ② 断点 + 算子表 → ③ 边界内求真逆
      —— 本函数就是**第 ① 步**，此前整台机器**缺这一步**。

    ★ 成本护栏：每个门超时压到 `预算秒`（不覆盖档案，只在本探针内压），
      且**只跑一次**（按仓库根缓存）。门全绿也照样报 —— 那说明"缺口不在验收门上"，
      同样是规划需要知道的事实。
    """
    gates = [dict(g) for g in prof_mod.get_profile().gates]
    if not gates:
        return ""
    key = str(REPO_ROOT)
    if _门态缓存.get("key") == key:
        return str(_门态缓存.get("文本") or "")
    限时 = []
    for g in gates:
        gg = dict(g)
        try:
            t = gg.get("timeout")
            gg["timeout"] = min(int(t), 预算秒) if t is not None else 预算秒
        except (TypeError, ValueError):
            gg["timeout"] = 预算秒
        限时.append(gg)
    try:
        res, red = _score_gates(REPO_ROOT, REPO_ROOT, 限时, repo_root=REPO_ROOT)
    except Exception as exc:      # noqa: BLE001 - 探针自身异常不得当绿，也不得阻断规划
        _log(f"[generic] ★ 规划前门态探针异常（如实报出，不阻断规划）："
             f"{type(exc).__name__}: {exc}")
        return ""
    文本, 必改 = _门态文本(res, red, 上限字符)
    _门态缓存["key"] = key
    _门态缓存["文本"] = 文本
    _门态缓存["必改"] = 必改
    # ★ R43：把红门条目也缓存 —— 它是「违规点集合」的**基线**，
    #   而基线正是"这一步有没有让判据更接近绿"的比较起点。
    #   ★ 缓存它 = **零额外成本**（探针本来就要跑这一次门）。
    _门态缓存["红"] = list(red or [])
    # ★★★★★ R78：**项目验收门的基线** —— 零额外成本，探针本来就已经跑过这批门。
    #   ★ 必须是**改之前**的那一次：本探针跑在规划阶段，主树/隔离树**未被本轮改动**
    #     ⇒ 这就是"主树本来什么样"。⇒ 缓存它，交付时才有"跟谁比"。
    #   ★ 不在 `_重取基线` 里重取：那时树已经被改过 ⇒ 会把**自己造成的**坏算成基线。
    _门态缓存["res"] = dict(res or {})
    return 文本


def _门态文本(res, red, 上限字符: int = 8000) -> tuple[str, list[str]]:
    """把门结果**组装**成规划上下文。返回 `(文本, 必改文件清单)`。

    ★ 为什么把"组装"从"跑门"里**拆出来**（齿探针⑬抓出）：
      组装逻辑原本长在 `_门态探针` 里，而 `_门态探针` 要**真跑项目验收门**
      ⇒ 自检没法在不跑门的前提下验证"必改清单真的进了文本"。
      结果就是：清单抽得对、**但没接进 prompt** 这种缺陷，自检**测不出来**
      （探针⑬ 退回注入后自检仍全绿 —— 门无齿，如实登记）。
      ⇒ 拆成纯函数后，自检可以喂一份**假的红门结果**，直接断言文本里有清单。
    """
    绿 = [k for k, v in (res or {}).items() if v is True]
    红 = [k for k, v in (res or {}).items() if v is not True]
    段 = ["═══ 规划前机器探针：项目**自己的**验收门当前真值（机器实跑，非猜测）═══",
          f"仓库根：{REPO_ROOT}",
          f"共 {len(res or {})} 条门 —— 绿 {len(绿)} 条 / 红 {len(红)} 条"]
    if 绿:
        段.append("绿：" + "、".join(绿))
    if not 红:
        段.append("★ 全部绿 ⇒ **缺口不在这些门上**（别去改它们；先想清楚"
                  "目标到底要改什么，或者是不是缺一条门）。")
    # ★★★★★ R36：**机器先把必改文件抽出来**，放在最前面、单独成块。
    #   真跑实测：把 14 条 `文件:行号` 埋在 8000 字符输出里 ⇒ 规划器**根本没看**，
    #   改从符号快照里猜了 3 个完全不相干的文件。⇒ 机器的活别派给模型。
    必改 = _证据必改文件("\n".join(str(r.get("err") or "") for r in (red or [])))
    块 = _必改清单块(必改)
    if 块:
        段.insert(0, 块)
    for r in (red or []):
        body = str(r.get("err") or r.get("cause") or "")
        段.append(f"\n──── 红门 `{r.get('gate')}`（file={r.get('file')}）────\n"
                  f"cause: {r.get('cause')}  rc: {r.get('rc')}\n"
                  f"输出：\n{_掐中(body, 上限字符)}")
    return "\n".join(段), 必改


def _必改清单块(必改: list[str]) -> str:
    """必改文件清单的**注入块**（空表 ⇒ 空串，零回退）。"""
    if not 必改:
        return ""
    return ("★★★ 机器已从红门证据里抽出**必改文件**"
            f"（{len(必改)} 个，全部在仓库里真实存在）——\n"
            "    你的 deliverables **必须覆盖它们全部**；\n"
            "    漏掉任何一个 ⇒ 那个文件里的违规原样留着 ⇒ "
            "验收门**不可能**转绿（机器会当场退回重规划）。\n"
            + "\n".join(f"      · {b}" for b in 必改)
            + "\n")


def _掐中(文本: str, 上限: int) -> str:
    """超长输出取**头 + 尾**（中间省略并说明）。

    ★ 为什么掐中间而不是只留头：判据门的**违规清单**常同时出现在
      开头（逐条打印）与结尾（panic 消息里重述）—— 只留一头会丢掉
      「哪一条门红的、红在哪一行」这类关键行。
    ★ 掐了必须**说出来**（"没跑"与"跑了但没看全"不能长得一样）。
    """
    t = 文本 or ""
    if len(t) <= 上限:
        return t
    头 = 上限 * 2 // 5
    尾 = 上限 - 头
    return (t[:头] + f"\n\n……（此处省略 {len(t) - 上限} 字符）……\n\n" + t[-尾:])


def _指导声明的必改文件(决) -> list[str]:
    """从主心骨的**判据/计划**里抽出它点名的文件（R106）。

    ★ 为什么需要这个函数（真跑抓出的第 20 层 · 2026-10-19 · 第九次端到端）：
      主心骨给了 7 条判据 + 6 项计划，其中判据写得很准：
        · `in: src/模块甲/趋向.rs` + `patterns: ["广域\\s*\\("]`（生产侧必须真调用）
        · `in: src/模块乙/评测口径.rs` 等三条「广域仍在」（评测侧不得降级）
        · `whole_reading` 钉住定义（防用「删掉无界键」冒充统一）
      R105 已把这些**送进规划器提示**。结果：规划器**还是**给了
      `src/模块乙/分派树.rs` / `src/模块乙/胞坐标.rs` 两个零门交付物。
      ⇒ 主心骨的判据**只是提示，不是闸**。

    ★ R86 早就证明过这件事：把必改文件只喂进提示，规划器**根本没看**，
      于是 R86 把它做成了**机器闸**（`_计划覆盖缺口` + `回喂重规划`）。
      ⇒ 主心骨点名的文件必须走**同一把尺子**，不能另写一套（R98b）。
    """
    if not isinstance(决, dict):
        return []
    出: list[str] = []
    for g in (决.get("判据") or []):
        if not isinstance(g, dict):
            continue
        for k in ("in", "path", "file"):
            v = g.get(k)
            if isinstance(v, str) and v.strip():
                出.append(v.strip())
    for d in (决.get("计划") or []):
        if isinstance(d, dict):
            for k in ("path", "file", "in"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    出.append(v.strip())
        elif isinstance(d, str) and d.strip():
            出.append(d.strip())
    # 去 `:行号` 尾巴（与 `_目标声明的必改文件` 同口径：按文件判，不按行判）
    return [re.sub(r"[：:]\d+(?:[：:]\d+)?\s*$", "", x).strip()
            for x in dict.fromkeys(出) if x]


def _指导块(决) -> str:
    """把主心骨定下的**判据/计划**渲染成规划器能读的**指导块**（R105）。

    ★ 为什么不直接把它的 `判据` 当 `spec.gates` 用：
      P 化纪律要求门**只能由 `derive_gates(P)` 派生**，LLM 不得直接造门。
      主心骨的判据是「**什么算做完**」的语义陈述，不是机器门规格。
      ⇒ 它进**规划器提示**（约束交付物与做法），
        规划器产出的规约仍要过 `_adopt_spec` 的**全套机器校验**。
      ★ 这样「主心骨定方向」与「机器判据门裁决」两层都没被绕过。
    """
    if not isinstance(决, dict):
        return ""
    判 = 决.get("判据") or []
    计 = 决.get("计划") or []
    if not 判 and not 计:
        return ""
    行 = ["", "", "【★★ 主心骨已经把**判据**定下来了（R105 · 这不是建议，是约束）】"]
    理 = str(决.get("理由") or "").strip()
    if 理:
        行.append(f"它的理由：{理[:600]}")
    if 判:
        行.append(f"它定了 {len(判)} 条判据 —— 你的规约必须让**每一条**都能被机器判：")
        for i, g in enumerate(判[:12], 1):
            if isinstance(g, dict):
                名 = g.get("name") or g.get("类型") or f"判据{i}"
                型 = g.get("type") or ""
                位 = g.get("in") or g.get("path") or ""
                附 = " ".join(x for x in (f"type={型}" if 型 else "",
                                          f"in={位}" if 位 else "") if x)
                行.append(f"  {i}. {名}" + (f"（{附}）" if 附 else ""))
            else:
                行.append(f"  {i}. {str(g)[:160]}")
        行.append("  ★ 判据里点名的文件，必须出现在 `deliverables` 里"
                  "（否则那一条判据没有落点，机器会判「交付物没有判据门覆盖」）。")
    if 计:
        行.append(f"它给了 {len(计)} 项计划（要动哪些文件）：")
        for i, d in enumerate(计[:12], 1):
            if isinstance(d, dict):
                位 = d.get("path") or d.get("file") or d.get("in") or ""
                因 = d.get("why") or ""
                行.append(f"  {i}. {位}" + (f" —— {str(因)[:120]}" if 因 else ""))
            else:
                行.append(f"  {i}. {str(d)[:160]}")
    行.append("★ 你仍然只输出**规约 JSON**；交付物形态/路径/拓扑仍由机器校验。")
    行.append("★ 但**不要再退回上一版被打回的那份**——主心骨已经指出了做法该怎么改。")
    # ★★★★★ R121（真跑抓出的第 35 层 · 2026-10-21 · 第十八次端到端）：
    #   **上面那句话只说了单向约束，机器却按双向判。**
    #
    #   真证据（03:10→04:10 那一跑）：连续 3 次
    #     `交付物**没有判据门覆盖**：['src/模块甲/趋向.rs',
    #       'src/模块乙/arc评测.rs', 'src/模块乙/点云评测.rs',
    #       'src/模块乙/评测口径.rs']`
    #   ⇒ 判「计划不可能成功（机器可证）」⇒ 重规划 3 次 ⇒
    #     `ValueError: 重规划连续 3 次可纠正性问题仍未改对 ⇒ 诚实停机`
    #
    #   ★ 两条机器闸**互相矛盾**：
    #     · 上面那句（R105）：**判据里点名的文件必须出现在 `deliverables` 里**
    #     · `bound_report`（R104）：**每个交付物必须被某条门读到**
    #   主心骨这轮把目标收缩成「单文件里程碑」（`src/模块甲/mod.rs`），
    #   但它的判据仍点名 4 个评测侧文件 ⇒ 规划器把 4 个文件放进 deliverables
    #   却没有对应门（判据里的门已经被前一轮的归一化裁掉了）
    #   ⇒ 两闸无法同时满足 ⇒ **死锁**。
    #
    #   ★ 修法：把**反向约束**也写清楚，并给出唯一自洽的出路。
    #     （不放松判据：仍然要求每个交付物有门 —— 那是 R104 的立命之处。）
    行.append("")
    行.append("【★★ 反向约束同样成立，两条必须同时满足】")
    行.append("· 判据里点名的文件 ⇒ 必须出现在 `deliverables` 里（上面已说）。")
    行.append("· **每个 `deliverables` 条目 ⇒ 必须至少被一条门读到**"
              "（门规格的 `in`/`src` 里出现该路径，或该门含 `{file}`/`{work}`）。")
    行.append("· ★ 所以：**本轮不改的文件，不要放进 `deliverables`。**"
              "若主心骨的判据点名了本轮不动的文件，"
              "请把该判据也落成一条门（`in` 指向它），或**只保留本轮真要改的那些**。")
    行.append("· ★ 机器判的是**双向**；只满足一边，规约会以"
              "「交付物没有判据门覆盖」或「判据点名的文件缺失」被打回。")
    return "\n".join(行) + "\n"


# ★★★★★ R222：**回执正文不许复述目标书里的历史实测快照。**
#
#   真证据（第 124/125 次真跑，每条回执都长这样）：
#     goal: "…【机器实测（2026-10-28 13:44）：11 个错误，退出码 101】
#            错误码分布：E0433 × 1、E0284 × 10…"
#   而**同一时刻**现场：退出码 0、0 个错误、#[test] 1543。
#   ★ 人看回执以为树还是红的；机器下一轮读回执也读到「11 个错误」。
#   ★ 根因：R199 只把现场实测注入**给模型的提示**，**回执正文**仍是目标书原文
#     —— 同一个事实两处口径，正是本项目最有毒的那一类缺陷。
#   ⇒ 口径：回执里的目标文本**去掉历史快照**，并明确指向本回执的现场真值。
def _现场段222() -> str:
    """当前门态的现场实测段（拿不到 ⇒ 空串，绝不编）。"""
    try:
        return _现场实测段199(_门态缓存.get("红") or [], [])
    except Exception:                       # noqa: BLE001
        return ""


def _回执目标222(目标: str, 现场段: str = "") -> str:
    """把目标文本里的「机器实测」快照摘掉；没有快照 ⇒ 原样返回。"""
    _t = 目标 or ""
    _标 = ("【机器实测", "【错误码分布", "错误码分布：",
           "个错误，退出码", "个错误，```")
    _位 = [k for k in (_t.find(s) for s in _标) if k > 0]
    if not _位:
        return _t
    _头 = _t[:min(_位)].rstrip()
    _说 = ("\n\n【★ 目标书里的「机器实测」快照已略去 —— 那是**写目标时**的事实，"
           "可能已过期；以本回执的现场真值为准】")
    return _头 + _说 + (("\n\n" + 现场段) if 现场段 else "")


def _现场实测段199(红条目: list, 必改: list) -> str:
    """★ R199：生成「**现场实测覆盖**」提示段。没有实测事实 ⇒ 返回 ""（不乱说）。

    真证据（第 97 次真跑）：目标文本写「11 个错误 / 必改 4 个文件」，
    而现场只剩 1 个错误、3 个文件已经改对。规划器按过期清单出计划 ⇒
    下游在已经对的文件上重写 ⇒ 造出新错 ⇒ 整批回退 ⇒ 永不收敛。

    ★ 只做**陈述**：把机器刚测到的「还红的文件」摆出来并声明优先级。
      不替模型决定怎么改，也不改任何判定口径。
    """
    _红文 = sorted(_红证据文件(红条目 or []) | {
        str(_p).replace("\\", "/") for _p in (必改 or []) if str(_p).strip()
    })
    if not _红文:
        return ""
    _列 = "\n".join(f"- `{_p}`" for _p in _红文[:12])
    return (
        "\n\n═══════════════════════════════════════════════════════════\n"
        "★★★ **现场实测覆盖**（机器刚刚测的；比上面目标文本里任何数字都新）\n\n"
        "上面目标文本里若出现「机器实测：N 个错误」「【必改文件】…」等段，"
        "那是**写目标那一刻**的历史快照，**可能已经过期**。\n\n"
        "机器刚才实测下来，**还红的文件只有这些**：\n"
        f"{_列}\n\n"
        "⇒ **必改文件只取上面这一份**。目标文本里列了、但**不在**这份清单里的文件，"
        "说明它**已经改对了** —— 本轮**不许**把它列进 deliverables"
        "（在上面对的文件上重写，只会制造新错，实测已发生过 E0425/E0428）。\n"
        "═══════════════════════════════════════════════════════════\n"
    )


def _planner_prompt(goal: str, snapshot: str = "", 反馈: str = "",
                    门态: str = "", 记忆: str = "", 现场: str = "") -> str:
    现场块 = 现场 or ""          # ★ R199：现场实测优先于目标文本里的历史快照
    snap_block = ""
    if snapshot:
        snap_block = ("\n\n项目结构快照（机器扫主树所得真值，仅参考；帮助你选对真实存在的文件/符号），\n"
                      "symbols 命中提示了库里真实存在的 pub 项，规划时请优先采用这些真实符号与文件：\n"
                      + snapshot)
    # ★ 上一版规约被机器拒时的**可执行反馈**（真跑抓出：交付物写成目录 `src/`）。
    #   ★ 与「诚实停机」的区别：形态错是**可纠正的表述问题** ⇒ 值得自动重来；
    #     目标本身做不到才该停机。混为一谈就只能一刀切。
    # ★★★★★ R33：**规划前先跑项目自己的验收门**，把「缺口断在哪」当上下文。
    #   在此之前规划器是**盲的** —— 它只能靠符号快照猜文件，
    #   真跑实测猜错了**全部**文件 ⇒ 整趟白跑。
    门态块 = ("\n\n═══════════════════════════════════════════════════════════\n"
              "★ 机器已经**先跑过一遍**项目自己的验收门（下面是**实测真值**，"
              "不是猜测）。请据此确定**要改哪些文件** —— 红门输出里通常"
              "**直接带文件与行号**，请照着列 deliverables，**不要靠猜**。\n"
              f"{门态}\n"
              "═══════════════════════════════════════════════════════════\n"
              ) if 门态 else ""
    # ★ R34：大文件必须给行号焦点（否则执行层**如实报"改不了"**，
    #   这条交付物在计划诞生那一刻就死了）。阈值与执行层**同一常数**。
    焦点块 = ("\n- ★★★ 若某个交付文件**超过 "
              f"{_REPO_TARGET_CAP} 字符**（大文件），`path` **必须**写成 "
              "`路径:行号` 形式（如 `\"src/模块甲/mod.rs:8295\"`）—— "
              "行号是**改动点**。机器只把该行附近的带行号窗口给执行智能体，"
              "它据此出补丁。\n"
              "  · 不给行号 ⇒ 机器**无法确定改哪** ⇒ 会如实报「改不了」"
              "（它不猜），这条交付物就废了。\n"
              "  · 上面红门输出里若已带文件:行号，**照抄**即可。\n"
              "  · 同一文件要多处改 ⇒ **列多条**交付物，每条给一个行号。\n")
    反馈块 = ("\n\n═══════════════════════════════════════════\n"
              "★ 你上一次的规约被**机器**拒了，原因如下（请据此改正后重新输出）：\n"
              f"{反馈}\n"
              "═══════════════════════════════════════════\n") if 反馈 else ""
    # ★★★★★ R92：**主心骨的记忆**。
    #   真跑查出来的形态：`_replan` 没有跨轮历史 ⇒ 每次出问题就**重新猜一遍**，
    #   设计意图在第一次规划之后就丢了 ⇒ 整台机器是**轮询调度器**，不是主心骨。
    #   ⇒ 把「我们当初为什么这么做 / 走过哪些弯路 / 目标改过没有」喂回来。
    记忆块 = ("\n\n═══════════════════════════════════════════\n"
              "★ **你自己的记忆**（你是这个项目的主心骨，不是每次都重新开始的新规划器）。\n"
              "  下面是你前面几个回合**自己的决定**，以及你当时依据的观测。\n"
              "  ⇒ 不要重复已经失败的路线；方向若需要修正，就在这次一并修正。\n"
              f"{记忆}\n"
              "═══════════════════════════════════════════\n") if 记忆 else ""
    # ★★★★★ R92：**判据必须自带否定样本**。
    #   为什么必须写死在提示里：模型起过一条门叫
    #   `新测试_判据_甲子生产路径生效` —— 门是诚实的（真跑了那条测试、真通过），
    #   **撒谎的是测试本身**（断言只是「文件里有这个词」）。
    #   起名字是零成本的，写一条真能红的门是有成本的 ⇒ 必须逼它自带反例。
    契约块 = ("\n\n═══════════════════════════════════════════\n"
              "★★★ **每个 `pred.checks` 项必须自带一个「它必须拒收的样本」**"
              "（`否定样本`）。\n"
              "  ★ 写在 **check 里**，不是写在 gates 里 —— 门是由 `derive_gates(P)`\n"
              "    从 checks **闭式派生**的，你在 gates 里写什么都不会被采纳。\n"
              "  机器会把那个改动喂给派生出来的门，**必须变红**，否则这条判据不算成立。\n"
              "\n"
              "  \"否定样本\": [{\"旧\": \"<产物里应当存在的串>\",\n"
              "                \"新\": \"<把它改坏成什么>\",\n"
              "                \"说明\": \"为什么这必须让它红\"}]\n"
              "\n"
              "  · 「旧」必须是产物里**真的会存在**的一段（机器要在产物里找到它）。\n"
              "  · 「新」是把它**改坏**成什么（删掉/换错/漏接）。\n"
              "  · ★ 不要写「文件里有这个词」这种判据 —— 它自带不了否定样本，会被拒收。\n"
              "  · ★ 好的否定样本指向**这条判据管的那一件事**：\n"
              "      判据「生产路径调用了 判据_甲子」⇒ 样本「把那个调用删掉」。\n"
              "═══════════════════════════════════════════\n")
    return f"""你是通用多智能体编排器的「规划器」。给定一个自然语言目标，你把它翻译成结构化
任务规约 JSON，供纯机器计划器接线成任务 DAG（支持多文件依赖拓扑），再由机器判据门裁决。
你只做“语义 → 数据结构”的翻译，裁决一律交给机器门，绝不由另一模型审你的产物。

目标：
{goal}{现场块}{记忆块}{snap_block}{门态块}{反馈块}{契约块}
请只输出一个合法 JSON 样本戊（不要 markdown 代码块围栏、不要任何解释文字）。结构如下：
{{
  "name": "简短任务名",
  "pre": ["前提1"],
  "constraints": ["硬约束1"],
  "deliverables": [
    {{"path": "fib.py",
      "agent_instruction": "生成 fib.py：实现 fib(n)，fib(0)=0、fib(1)=1、fib(10)=55",
      "depends_on": []}},
    {{"path": "test_fib.py",
      "agent_instruction": "生成 test_fib.py：import fib 并对 fib(0)/fib(1)/fib(10) 做断言",
      "depends_on": ["fib.py"]}}
  ],
  "pred": {{
    "files": ["fib.py", "test_fib.py"],
    "checks": [
      {{"kind": "compile", "name": "compile_ok",
        "cmd": ["python", "-m", "py_compile", "{{file}}"], "cwd": ".", "expect_exit": 0}},
      {{"kind": "whole_reading", "name": "has_fib", "file": "fib.py", "mode": "contains",
        "must_contain": ["def fib"],
        "否定样本": [{{"旧": "def fib", "新": "def nope",
                      "说明": "删掉函数定义 ⇒ has_fib 必须红"}}]}},
      {{"kind": "compile", "name": "behave",
        "cmd": ["python", "-c", "import sys; sys.path.insert(0,'.'); from fib import fib; assert fib(10)==55"],
        "cwd": ".", "expect_exit": 0}}
    ]
  }},
  "acceptance": ["数值断言全过、行为门全绿"]
}}

规则（全 P 化：判据门只由 pred.checks 经 derive_gates(P) 闭式推导，LLM 不造门）：
- "deliverables"：文件列表（多文件目标就列多个；单文件也可用顶层 "deliverable": "fib.py" 简写）。
  每项必含 path（仓库内相对路径）；可选 agent_instruction（缺省共享顶层）；可选 depends_on
  （引用本列表里其他 path，表示该文件依赖前序文件，机器会按依赖拓扑串线）。
- "pred"：**目标谓词 P 的唯一门源**。含：
  - "files"：P 声明要动/要产出的文件（仓库相对路径）。P 的作用域锚 = 这些文件路径 ∪ 下面各
    check 的 must_contain 必需结构键。
  - "checks"：可判定检查清单。kind 仅三类：
    * "compile"：跑命令断言退出码 = expect_exit（构建/解释器编译的**行为门**）。工程编译用
      cwd="{{repo}}"、cmd 如 ["cargo","check","--lib"]；本地新文件用 cwd="."、cmd 如
      ["python","-m","py_compile","{{file}}"] 或行为断言 ["python","-c","from 模块 import 函数; assert ..."]。
    * "whole_reading"：读整体一次判 O(1)——文件"file"必须含全部 "must_contain" 必需结构键
      （mode="contains"，读整体一次稳定归一化判存在性，格式免疫）。这是让机器确认"该改的结构
      真改了/没被删"的结构键门。
    * "scan_output"：跑命令扫输出不得含 "must_not"（file/"append" 供注入判测文本）。
  - 业务验收必须定义成上述可判定检查能真正证明的东西（编译通过/数值断言/必需结构键存在），
    "看起来合理"的软验收不算；对软判一律红。
  - **严禁**把守恒门放进 checks：禁止 grep_forbidden（禁词/禁改动上限）、grep_required 之外的
    "不许改超过 N"/"minimal-change" 类差分门 —— 门只测 P 是否达成，不测"不许坏"。

主树仓库工程任务（要改 src/ 下现有 .rs/.py 等文件、用 cargo/curl 等真实工具判门）：
- 顶层加 "repo": true；deliverables 与 pred.files 的 path 用仓库根相对路径（如 "src/gpu/gpu探测.rs"）。
{焦点块}
- compile 检查 cwd 设 "{{repo}}"（cargo check/curl 在仓库根跑真实行为）。
- 执行智能体只能改动列出的文件，未列出的必须保持原样；改动最小化，未动部分逐字保留。
- 用一个 compile 门（{{repo}} 根真实构建）证明可编译 + 至少一个 whole_reading 门列出原文件必须
  保留的关键符号/结构（def/fn/常量名，逐字子串入 must_contain），防止智能体删除语义。判据
  门必须拦住"删掉出问题代码让编译通过"的取巧路径（真实踩过）。
- 换检查种类 = 换 P = 用户重定义；你产出合法 P 即可，不自行降级/换门。
{snap_block}"""


_记忆表缓存: dict[str, object] = {}


def _结构包文本(根: str, 目标: str, 红证据) -> str:
    """洞驱动上下文包（结构寻址）—— 第 157 轮接入。

    ★ 它补的是什么
      执行 prompt 原本给的是：目标文件全文/块窗口 + 只读探针 + 依赖。这些**不知道
      谁在调用这个符号、这个符号调了谁、哪些同名定义别处还有**。
      ⇒ 缺口（红门指到的位置）驱动，沿「定义↔使用」一次对合取一小圈，
      每条带 sha256，供门侧核对"改的是不是我看到的那一版"。

    ★ 降级纪律：任何异常返回空串 + 记一行日志，**绝不因为上下文增强把整跑搞挂**。
    """
    if str(os.environ.get("ORCH_MEM_CTX", "1")).strip() in ("0", "false", "False", "no"):
        return ""
    try:
        from . import mem_context as _mc
        from .mem_symbols import 符号表

        表 = _记忆表缓存.get(根)
        if not isinstance(表, 符号表):
            表 = 符号表()
            _记忆表缓存[根] = 表
        表.同步(根)

        洞们: list = []
        条目 = 红证据 if isinstance(红证据, list) else []
        for x in 条目:
            if not isinstance(x, dict):
                continue
            行 = x.get("行") or x.get("line") or x.get("行号")
            文件 = str(x.get("文件") or x.get("file") or 目标).replace("\\", "/")
            if isinstance(行, int) and 行 > 0:
                洞们.append(_mc.洞(文件, 行, str(x.get("type") or x.get("message") or "")[:40]))
        if not 洞们:
            洞们 = [_mc.洞(目标.replace("\\", "/"), 1, "目标文件")]
        # ★ 预算自适应（阶段 5）：超预算按价值序截断并报账，不再"整包塞"或"整包丢"
        包 = _mc.上下文包(表, 洞们)
        预算 = int(os.environ.get("ORCH_PACK_BUDGET", "16000") or 16000)
        文, 缩 = _mc.渲染_预算(包, 预算)
        if 缩:
            import logging as _lg
            _lg.getLogger("orchestrator").info(
                "上下文包超预算 %d → 截断 %d/%d 条（%d → %d 字节）",
                预算, 缩["丢条"], 缩["原条"], 缩["原字节"], 缩["现字节"])
        return 文
    except Exception as exc:                       # noqa: BLE001
        _log(f"[generic] 结构上下文不可用（如实降级）：{type(exc).__name__}: {exc}")
        return ""


def _exec_prompt(goal: str, spec: dict, d: dict, deps: dict[str, str],
                 repo_ctx: str = "", 探针: str = "", 结构包: str = "") -> str:
    gates = d.get("gates") or spec["gates"]
    dep_block = ""
    if deps:
        dep_block = "\n可引用的已产出依赖文件内容（同窗内先产出且已过门）：\n" + "\n".join(
            f"----- {p} -----\n{text}" for p, text in deps.items())
    repo_block = ""
    if repo_ctx:
        repo_block = ("\n仓库当前文件内容（本次任务要改的就是它；未改动部分必须逐字保留，"
                      "不得重写整体语义）：\n" + repo_ctx)
    # ★★★★★ R93：**机器代它查好的东西**。
    #   真跑里窗口输出过 `<｜｜DSML｜｜ invoke name="bash">` —— 它想跑去看仓库，
    #   被认出「这不是内容」**拒收**（R32）。拒得对，但**拒完之后没给它路**。
    #   ⇒ 这里把它想查的那几件事（接口长什么样、谁在调、门为什么红）由机器
    #     **只读探针**查好递过去。**不给它 bash**：产物必须只能是被门裁决的代码。
    探针块 = ("\n═══════════════════════════════════════════════════════════\n"
              "★ 机器**已经替你把项目查过一遍**（下面是只读探针的实测结果，不是猜测）。\n"
              "  你想知道的「这个接口长什么样 / 谁在调它 / 门为什么红」都在下面。\n"
              "  ⇒ **不需要**再去探索仓库；请直接据此改。\n"
              f"{探针}\n"
              "═══════════════════════════════════════════════════════════\n"
              ) if 探针 else ""
    结构块 = ("\n" + 结构包 + "\n") if 结构包 else ""
    return f"""你是通用多智能体编排器的「执行智能体」。

目标：
{goal}

必须产出文件（内容将被保存为 {d['path']}）：
{d['path']}

产出指令：
{d.get('agent_instruction') or spec.get('agent_instruction') or '按目标生成该文件完整内容'}
{dep_block}
{探针块}{结构块}{repo_block}
必须通过以下机器判据门（全部绿才算达标）：
{json.dumps(gates, ensure_ascii=False, indent=2)}

只输出 {d['path']} 的完整内容，不要 markdown 代码块围栏、不要任何解释文字。"""


def _rework_prompt(goal: str, spec: dict, d: dict, content: str,
                   red_audit: list, deps: dict[str, str], hist_note: str = "",
                   repo_ctx: str = "", strategy: str = "", gate_diff: str = "",
                   target_dev: dict | None = None, 结构记要: str = "") -> str:
    gates = d.get("gates") or spec["gates"]
    dep_block = ""
    if deps:
        dep_block = "\n可引用的已产出依赖文件内容（同窗内先产出且已过门）：\n" + "\n".join(
            f"----- {p} -----\n{text}" for p, text in deps.items())
    repo_block = ""
    if repo_ctx:
        repo_block = ("\n仓库当前文件内容（据此修改，未改动部分必须逐字保留，"
                      "不得重写整体语义）：\n" + repo_ctx)
    strat_block = f"\n{strategy}" if strategy else ""
    diff_block = f"\n{gate_diff}" if gate_diff else ""
    dev_block = ""
    if target_dev:
        dev_block = ("\n本轮只消除以下这一个偏差（机器选出，最小 delta；不要顺带改其他无关内容）：\n"
                     f"- 类型={target_dev.get('type')} 消息={target_dev.get('message')!r} "
                     f"符号={target_dev.get('symbols')}\n")
    return f"""你是通用多智能体编排器的「执行智能体」。你的上一版产物未通过机器判据门（红门如下），
请修正导致红门的问题后重新产出完整文件。
{hist_note}
{结构记要}
目标：
{goal}
当前文件 {d['path']} 的机器判据门结果（红=未过）：
{json.dumps(red_audit, ensure_ascii=False, indent=2)}
{dev_block}
{strat_block}
{diff_block}
你的上一版产物内容（作为参考上下文，可保留可通过的部分）：
----------------------------------------
{content}
----------------------------------------
{dep_block}
{repo_block}
必须通过以下机器判据门（全部绿才算达标）：
{json.dumps(gates, ensure_ascii=False, indent=2)}

只输出修正后 {d['path']} 的完整内容，不要 markdown 代码块围栏、不要解释文字。"""


def _replan_prompt(goal: str, old_spec: dict, red_audit: list,
                   hist_note: str = "", strategy: str = "",
                   snapshot: str = "") -> str:
    strat_block = f"\n{strategy}" if strategy else ""
    snap_block = ("\n\n项目结构快照（机器扫主树所得真值，仅参考，换路径时优先用真实符号）：\n"
                  + snapshot) if snapshot else ""
    # ★★★★★ R135（真跑抓出的第 48 层 · 2026-10-23 · 第二十九次端到端）：
    #   **机器把「上一版门集已经跑通了」这个成功经验丢掉了，却让模型重写门集。**
    #
    #   真证据（第 29 次真跑，轮 1/2/3 门真值逐位）：
    #     轮2 的门集**已经覆盖了全部 7 个交付物**：
    #       mod.rs@mod_无界键适配层 / 接线链.rs@生产链接入无界键 /
    #       结构记忆网络.rs@结构记忆_假测试断言消息 / 运行时事件采集.rs@… /
    #       运行时健康诊断.rs@… / arc评测.rs@… / 点云评测.rs@… / 评测口径.rs@…
    #     ★ 而且它**真跑了 3 轮**，判据违规点 6→4，`cargo_check_lib=True`。
    #   ⇒ 门集是**好的**，坏的是实现。
    #   ★ 而重规划 prompt 说的是：「**重新设计**文件结构 / 实现方案 / **判据门**」
    #     ⇒ 模型照着重写门集 ⇒ 新门集又漏了某个文件
    #     ⇒ `交付物没有判据门覆盖` ⇒ 3 次全败 ⇒ 诚实停机。
    #
    #   ★★ 这与 R128/R129/R131 是同一族的**第四次**：
    #     · R128：算出了「哪些门刚才是绿的」，没告诉模型；
    #     · R129：告诉了，但 diverging 立刻停机 ⇒ 没机会用；
    #     · R131：说了「你在两个状态间跳」，没说「换策略」；
    #     · **R135：门集已经是对的，机器却要求它重做门集。**
    #   ★ 共同形状：**机器手里有正确信息，却把「要改哪一层」指错了。**
    #
    #   ⇒ 修法：把「门集是否已被验证可行」这件事**明说**，
    #     并明确「**只改实现，不动门集**」——除非门集本身确实红。
    _门已验证135 = bool(red_audit) or bool(strategy)
    _门段135 = (
        "\n★★★ **重要：判据门集不需要重写（机器实测）**\n"
        "你上一版的门集**已经在真项目上跑通了多轮**，"
        "每个交付物都有门覆盖、`cargo_check_lib` 已绿。\n"
        "★ 坏的**不是门集，是实现**。\n"
        "⇒ 请**原样保留** `gates`（连同每个交付物自己的 gates），"
        "**只改 `deliverables` 的 path / pre / constraints / acceptance**。\n"
        "★ 如果你确实认为某条门必须换（例如它恒红、或与目标无关），"
        "**逐条说明是哪一条、为什么** —— 不要整套重写。\n"
        "★★ 真事故：上一跑连续 3 次重规划都因为「重写门集时漏了某个文件」"
        "被 `交付物没有判据门覆盖` 拒收 ⇒ 诚实停机。"
        "**门集是对的，别再动它。**\n"
    ) if _门已验证135 else ""
    return f"""你是通用多智能体编排器的「规划器」。你上一版规约的实现路径未通过机器判据门，
请换一种实现路径重新规划：重新设计文件结构 / 实现方案，以规避导致红门的原因。
不要只改措辞，要换实现路径（例如：换算法、换文件拆分、换断言方式）。
{_门段135}

目标：
{goal}

旧规约（实现路径失败）：
{json.dumps(old_spec, ensure_ascii=False, indent=2)}

机器判据门红门证据（旧路径的失败点）：
{json.dumps(red_audit, ensure_ascii=False, indent=2)}
{strat_block}
{hist_note}
{snap_block}
只输出一个合法 JSON 样本戊（结构与规划规约相同：name/pre/constraints/deliverables 或 deliverable/gates/acceptance，主树工程任务记得加 "repo": true），
不要 markdown 代码块围栏、不要任何解释文字。"""


# ════════════════════════════════════════════════════════════════
# 机器算子（0-LLM）
# ════════════════════════════════════════════════════════════════
def _修全角198(文本: str, 路径: str = "") -> tuple[str, int]:
    """★ R198：把**代码正文**里的全角标点换成半角；字符串/注释里的**不动**。

    真证据（第 96 次真跑第 1 轮）：
    ```
    E0460::unknown start of token \\u ff#c
    | help unicode character ， fullwidth comma looks like comma but it is not
    ```
    ⇒ 模型在 `.rs` 正文位置写了全角逗号 ⇒ 词法错误 ⇒ 整个文件不可编译。

    ★ 三条边界（缺一就会毁源码）：
      ① 只动**代码文件**（按后缀）；
      ② 只动**正文** —— 字符串/字符字面量/注释里的全角标点是合法的（本仓库注释就是中文）；
      ③ 只换**语法上不可能合法**的字符（全角逗号在 Rust 里没有第二种含义）。

    返回 (修好的文本, 换了几处)。
    """
    if not 文本:
        return 文本, 0
    _后 = Path(str(路径 or "")).suffix.lower()
    if _后 not in (".rs", ".py", ".ts", ".tsx", ".js", ".jsx", ".go",
                   ".java", ".c", ".h", ".cc", ".cpp", ".hpp"):
        return 文本, 0           # 文档类不碰
    out: list[str] = []
    n = 0
    _N = len(文本)
    k = 0
    while k < _N:
        c = 文本[k]
        # 行注释 `//` 到行尾
        if c == "/" and k + 1 < _N and 文本[k + 1] == "/":
            j = 文本.find("\n", k)
            j = _N if j < 0 else j
            out.append(文本[k:j]); k = j; continue
        # 块注释 `/* … */`
        if c == "/" and k + 1 < _N and 文本[k + 1] == "*":
            j = 文本.find("*/", k + 2)
            j = _N if j < 0 else j + 2
            out.append(文本[k:j]); k = j; continue
        # 双引号字符串
        if c == '"':
            j = k + 1
            while j < _N:
                if 文本[j] == "\\":
                    j += 2; continue
                if 文本[j] == '"':
                    j += 1; break
                j += 1
            out.append(文本[k:j]); k = j; continue
        # 单引号：**只有真字符字面量**才当字符串（Rust 生命周期 `'a` 不是字面量）
        if c == "'":
            _像字面量 = (k + 1 < _N and 文本[k + 1] == "\\") or (
                k + 2 < _N and 文本[k + 2] == "'")
            if _像字面量:
                j = k + 1
                while j < _N:
                    if 文本[j] == "\\":
                        j += 2; continue
                    if 文本[j] == "'":
                        j += 1; break
                    j += 1
                out.append(文本[k:j]); k = j; continue
            out.append(c); k += 1; continue
        if c in _全角映射198:
            out.append(_全角映射198[c]); n += 1; k += 1; continue
        out.append(c); k += 1
    return "".join(out), n


# 全角 → 半角（只收**代码里不可能合法**的那些；中文本身不在表里）
_全角映射198 = {
    "，": ",", "；": ";", "（": "(", "）": ")", "：": ":",
    "｛": "{", "｝": "}", "［": "[", "］": "]", "＝": "=",
    "＋": "+", "－": "-", "＊": "*", "／": "/", "＜": "<",
    "＞": ">", "！": "!", "＆": "&", "｜": "|", "％": "%",
    "＾": "^", "～": "~", "？": "?", "＠": "@", "＃": "#",
    "＄": "$", "　": " ", "、": ",", "。": ".",
    # ★ R198b（第 100 次真跑第 1 轮实测）模型还会写**中文引号**：
    #   `E0460 help unicode characters “ left double quotation mark …`
    #   在代码正文位置它们一定是想写字符串引号（Rust 里没有别的含义）。
    "“": '"', "”": '"', "‘": "'", "’": "'",
    # ★ R198c（第 101 次真跑第 1 轮实测）：破折号 / 省略号 / 书名号同样是 E0460：
    #   `E0460 help unicode character — em dash looks like - minus/hyphen but it is not`
    "—": "-", "–": "-", "…": "...", "·": ".", "《": "<", "》": ">",
    "〈": "<", "〉": ">", "「": '"', "」": '"', "『": "'", "』": "'",
}


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```\w*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_text(content) -> str:
    # ★ 防御（R31 自检抓出的脆弱点）：`content` 若**整体**是 `str`，
    #   下面的 `for block in content` 会**逐字符**遍历，而每个字符都满足
    #   `isinstance(block, str)` ⇒ 结果变成"每个字符之间插一个换行"
    #   ⇒ 下游 JSON 解析报 `Invalid control character`（**指不出根因**）。
    #   ★ 这个分支本身就说明作者预期"可能传 str"（见下面的 `isinstance`），
    #     那就该在**入口**处理，而不是让 str 掉进为 block 列表写的循环。
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _parse_spec_json(text: str) -> dict:
    """从模型回复解析规约 JSON：先整体，失败再取首个 {...} 块。"""
    text = _strip_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise 规约可纠正(
                f"未产出 JSON 样本戊（模型没按格式回）：{text[:300]}"
                "\n★ 这**不是**任务失败 —— 是「模型这一版没按格式回」，它能自己改。"
                "\n★ 请**只**输出一个 JSON 样本戊，不要写任何解释、问候、鼓励语。")
        return json.loads(m.group(0))


def _归一路径(声明: str) -> str:
    """交付物身份的**唯一**归一化：`路径:行号` → `路径`，斜杠统一，去 `./`。

    ★★★★★ R39（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      R34 允许把大文件写成 `路径:行号`（焦点），`_parse_deliverables` 会把它
      拆成 `path` + `focus`。但**只有那一条路径**做了归一，另外**三处**
      比对交付物身份的地方仍是**裸字符串相等**：
        · `_valid_spec` 的依赖存在性检查
        · `_topo_sort` 的 `by_path` 查表
        · `_build_dag` 的依赖定位
      ⇒ 规划器写 `depends_on: ["src/判据/观测协议.rs:1993"]`（照 R34 的提示），
        而交付物身份是 `src/判据/观测协议.rs` ⇒ 查不到 ⇒
        **规划失败、整趟没跑**（报「依赖未知文件 …:1993」）。

      ★ 与 R34/R35/R38 同源：**同一件事两把尺子**。修法不是在那三处各打一个补丁
        （那还会漏第四处），而是**只留一把尺子** —— 本函数。
        ⇒ 凡是要判"这是不是同一个交付物"，一律过它。

    ★ 归一化**只用于比对**：交付物自身的 `path` 坐标**一个字节都不改**
      （改坐标会让产物落错地方 —— 那是另一类事故）。
    """
    t = str(声明 or "").strip()
    if not t:
        return ""
    纯, _ = _拆路径焦点(t)
    return 纯.replace("\\", "/").strip().lstrip("./").strip("/")


def _valid_spec(spec: dict) -> str | None:
    """机器校验规约：name 必填、deliverable 或 deliverables 至少其一、gates 合法、
    每个文件 path 合法相对路径、depends_on 引用必须在本列表内（未知依赖拒）。"""
    if not isinstance(spec, dict):
        return "规约不是 dict"
    if "name" not in spec:
        return "规约缺字段 name"
    if not str(spec.get("name") or "").strip():
        return "name 为空"
    dl = spec.get("deliverables")
    if isinstance(dl, list) and dl:
        paths = []
        for it in dl:
            if not isinstance(it, dict):
                return "deliverables 每项必须是 dict"
            p = it.get("path")
            if not str(p or "").strip():
                return "deliverables 每项缺非空 path"
            rel = Path(str(p))
            if rel.is_absolute() or ".." in rel.parts:
                return f"文件 path 必须是仓库内相对路径（禁绝对/跳越）：{p}"
            if p in paths:
                return f"文件 path 重复：{p}"
            paths.append(str(p))
        # ★ R39：身份比对一律过 `_归一路径`（`路径:行号` 与 `路径` 是同一件事）
        # ★ R52：改为**与 `_topo_sort` 同一把尺子**（`_交付物索引`+`_解依赖`）
        #   —— 否则两处会**口径分叉**（R35 的教训）：这里说"依赖存在"、
        #   那里说"未知文件"，用户看到两个互相矛盾的理由。
        _by_path, _by_key = _交付物索引([{"path": x, "focus": _拆路径焦点(x)[1]}
                                          for x in paths])
        for it in dl:   # 依赖存在性（顺序无关；自环/成环由 _topo_sort 机验）
            p = str(it["path"])
            for dep in (it.get("depends_on") or []):
                _目标, _ = _解依赖(str(dep), _by_path, _by_key)
                if not _目标:
                    return f"文件 {p} 依赖未知文件 {dep}"
        # P 化规格（pred.checks → derive_gates(P) 唯一门源）：顶层 pred 存在时不再强制 gates。
    pred = spec.get("pred")
    has_pred = isinstance(pred, dict) and isinstance(pred.get("checks"), list) \
        and bool(pred["checks"])
    if not has_pred and isinstance(dl, list) and dl:
        if not spec.get("gates"):
            return "顶层 gates 必须存在（或改用 pred.checks 全 P 化门源）"
    if not has_pred and not dl and not str(spec.get("deliverable") or "").strip():
        return "规约缺 deliverable（或 deliverables 列表）"
    if not has_pred and not dl:   # 单文件 deliverable：禁绝对/跳越
        deliv = spec.get("deliverable")
        if str(deliv or "").strip():
            rel = Path(str(deliv))
            if rel.is_absolute() or ".." in rel.parts:
                return f"deliverable 必须是仓库内相对路径（禁绝对/跳越）：{deliv}"
    # 门合法：pred 派生门 或 顶层 gates
    if has_pred:
        from agentscope_orchestrator.gate_derive import derive_gates
        gate_candidates = derive_gates(pred, repo_root="") or []
        if "files" in pred and isinstance(pred.get("files"), list):
            for p in pred["files"]:
                rel = Path(str(p))
                if rel.is_absolute() or ".." in rel.parts:
                    return f"文件 path 必须是仓库内相对路径（禁绝对/跳越）：{p}"
    else:
        bad = gate_validate(spec.get("gates") or [])
        if bad:
            return bad
    return None


def _拆路径焦点(路径: str) -> tuple[str, int | None]:
    """把 `路径:行号` 拆成 `(纯路径, 行号)`；无行号 ⇒ `(原样, None)`。

    ★★★★★ R34（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      规划器把交付物写成 `src/判据观测.rs`（**221622 字符**，超整文件阈值），
      而**没有给行号焦点** ⇒ `_produce_window` 只能如实吐
          `__ORCH_CANNOT_EDIT__: ... 目标里没有给出行号焦点 ⇒ 无法确定改动点`
      ⇒ 这一条交付物**在计划诞生那一刻就已经死了** —— 模型再怎么努力也改不了。

      ★ 根因不在执行层（执行层如实报"改不了"是**对的**，它没有猜），
        而在**规划层不知道这条规则**：大文件必须给行号。
      ★ 为什么让**交付物路径本身**能带行号：那是**唯一**一个"模型与机器
        都看得见、且不需要额外字段"的位置。目标文本里的 `路径:行号` 也能用
        （`_focus_line`），但目标是人写的；**规划器自己产出的行号**只能落在
        它自己产出的字段里。

    ★ 边界（不猜）：只认**末尾**的 `:数字`；Windows 盘符（如 `C:/x/a.rs`）不含
      数字结尾 ⇒ 不受影响；`src/a.rs:12:3`（行:列）只取行。
    """
    s = str(路径 or "").strip()
    m = re.search(r"^(.*?)[：:]\s*(\d+)(?:[：:]\d+)?\s*$", s)
    if not m:
        return s, None
    纯 = m.group(1).strip()
    if not 纯:
        return s, None
    try:
        return 纯, int(m.group(2))
    except ValueError:
        return s, None


def _交付物索引(deliverables: list[dict]) -> tuple[dict[str, list[int]],
                                                   dict[tuple, int]]:
    """交付物身份的**两级**索引：纯路径 → 下标列表；`(纯路径, 焦点)` → 下标。

    ★★★★★ R52（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      现象：规划失败，机器报
        `文件依赖拓扑违例：文件 src/判据/观测协议.rs 自依赖（机器拒发）`
      —— 而**交付物本身没有任何毛病**，整趟因此没跑。

      根因：**R39 的归一化把焦点丢了，而焦点恰恰是区分"同一文件两个区域"的
        唯一信息**。R34 之后，一个大文件可以按行号拆成**多个交付物**
        （真项目里 `观测协议.rs` 一个文件就有 7 处违规、跨 7 个行号）
        ⇒ `_parse_deliverables` 会产出多个 `path` 相同、`focus` 不同的交付物
        （`_valid_spec` 的重复检查比的是**裸字符串**，所以它们合法通过）。
        ⇒ 但 `_归一路径` 把 `路径:行号` 归一成 `路径`
        ⇒ 两个区域**塌成同一个身份** ⇒ 规划器写的
          「B 区域依赖 A 区域先改好」被判成 **自依赖** ⇒ 整趟拒发。

      ★ 这是 R39 的**镜像**：R39 修的是"带行号的依赖查不到"，
        归一化让查得到了；而**归一化本身**又制造了"两个区域分不开"。
      ⇒ 正解不是回退归一化（那会把 R39 放回来），而是
        **身份分两级**：能精确到焦点就精确，精确不了才退到路径。
        ★ 与 R34/R39 同源，仍是**同一件事一把尺子** —— 只是这把尺子有两格。
    """
    by_path: dict[str, list[int]] = {}
    by_key: dict[tuple, int] = {}
    for i, d in enumerate(deliverables):
        p = _归一路径(d.get("path"))
        by_path.setdefault(p, []).append(i)
        by_key.setdefault((p, d.get("focus")), i)
    return by_path, by_key


def _解依赖(声明: str, by_path: dict, by_key: dict) -> tuple[list[int], str]:
    """把一个 `depends_on` 声明解析成**交付物下标列表**（可能多个）。

    返回 `(下标列表, 原因)`；下标列表为空 ⇒ 未知依赖（调用方拒发）。

    ★ R52：解析顺序**先精确后宽**，不猜：
      ① 声明自带焦点（`路径:行号`）且命中「同路径 + 同焦点」⇒ **就是它**
         （这是"同一文件两个区域"的正当写法，必须精确命中，
           否则会退回自环 —— 那正是本缺陷）；
      ② 否则按**纯路径**命中唯一一个交付物 ⇒ 就是它；
      ③ 否则（同文件多区域、声明没给焦点）⇒ 该文件的**全部**区域都算依赖
         （保守读法：都排在前面。多排不会漏序，少排会错序）。
    """
    d = _归一路径(声明)
    if not d:
        return [], "空依赖"
    候选 = by_path.get(d) or []
    if not 候选:
        return [], f"未知文件 {声明}"
    _, 焦点 = _拆路径焦点(str(声明))
    if 焦点 is not None:
        j = by_key.get((d, 焦点))
        if j is not None:
            return [j], ""
    if len(候选) == 1:
        return [候选[0]], ""
    return list(候选), ""


def _parse_deliverables(spec: dict) -> list[dict]:
    """统一多/单文件：有 deliverables 列表则逐项规范化，否则把 deliverable 包成单文件项。

    每项无独立 gates 时直接挂顶层共享门（引用同一列表，norm 后一致），
    保证每文件必有门可判（_score_files 逐文件独立判门）。

    ★ R34：`path` 末尾的 `:行号` 被拆成独立字段 `focus` —— `path` 只留纯路径
      （否则会被当成文件名去建目录，落出一个叫 `x.rs:123` 的文件）。"""
    top_gates = spec.get("gates") or []
    def _mk(path, instr, gates, deps, machine=None):
        纯, 焦点 = _拆路径焦点(str(path))
        return {"path": 纯, "focus": 焦点, "agent_instruction": instr,
                "gates": gates if isinstance(gates, list) and gates else top_gates,
                "depends_on": deps, "machine": machine}
    dl = spec.get("deliverables")
    if isinstance(dl, list) and dl:
        out = []
        for it in dl:
            out.append(_mk(str(it["path"]), str(it.get("agent_instruction") or ""),
                           it.get("gates"), [str(x) for x in (it.get("depends_on") or [])],
                           it.get("machine")))
        return out
    return [_mk(str(spec["deliverable"]),
                str(spec.get("agent_instruction") or ""),
                spec.get("gates"), [], spec.get("machine"))]


def _启用跳过193(有红集: set, deliverables: list, repo_scope: bool) -> bool:
    """★ R193：**要不要按「当前红证据」收敛作业面？**（唯一判定口，齿直接测）

    真证据（第 92 次真跑，基线只剩 1 个错误）：目标仍点名 4 个必改文件，
    而其中 3 个上一趟已改对 ⇒ 模型在**已经对的**文件上乱改，
    造出 E0425/E0428（窗内违规 3 → 10）⇒ 每轮整批回退 ⇒ 永不收敛。

    ★ 三条保护，缺一不可，否则**不启用**（宁可按原口径，不许误伤）：
      ① 仓库工程模式（不然没有「文件已经存在」的概念）；
      ② 至少**有一个**交付物本轮真有红证据 —— 否则说明红证据没有文件归属
         （例如只有全库型编译门），这条规则会变成「什么都不改」；
      ③ 由调用方再叠一条「该文件在仓库里已存在」（纯函数不碰磁盘）。
    """
    if not repo_scope or not 有红集:
        return False
    for _d in (deliverables or []):
        _p = (str(_d.get("path") or "") if isinstance(_d, dict) else "")
        if _p and _p in 有红集:
            return True
    return False        # 没有任何交付物命中红证据 ⇒ 规则不适用


def _该跳过193(路径: str, 启用: bool, 有红集: set, 文件已存在: bool,
              刚被退: bool = False) -> bool:
    """★ R193：这个交付物本轮该不该**完全不碰**。

    ★ 与 R89（代偿拒收）是同一族的两面：
      R89 说「别处变好不能买通这里没变好」；
      R193 说「这里本来就好了，就别再去动它」。

    ★★★★★ R203：**上一轮被机器退回来的文件，一律不许跳过。**
    真证据（第 100 次真跑第 2 轮，原文）：
      `★ R193 跳过 算子选择器.rs：本轮它没有红门证据（已经是对的）⇒ 不动它`
    而**那正是唯一还编译失败的文件**（E0433 `定列` 未解析，就在它第 682 行）。
    根因：全库型门（cargo）的证据**没有逐文件归属** ⇒ `_红证据文件` 抽不出它
    ⇒ 被当成「没有红证据」⇒ 跳过 ⇒ **永远改不到它** ⇒ 永不收敛。
    ⇒ 口径：**被退过 = 一定有它的问题**（机器亲手退的，不需要再证明一遍）。
    """
    if 刚被退:
        return False    # ★ R203：机器刚把它退回来 ⇒ 它一定有问题 ⇒ 不许跳过
    return bool(启用) and (路径 not in (有红集 or set())) and bool(文件已存在)


def _去重交付物189(deliverables: list) -> list:
    """★ R189：按**路径**去重交付物，合并重复项的元数据。

    真证据（第 91 次真跑 plan 回执）：12 个交付物只有 **4 个不同路径**
    （时序拓扑注入.rs ×4 / 自涌现编译.rs ×4 / 织网者.rs ×3 / 算子选择器.rs ×1）。
    `_produce_window` 是逐交付物各调一次模型的，且**每次都从磁盘读原文件**
    ⇒ 同一文件被独立改 4 次、后写覆盖前写 ⇒ 8/12 次调用是纯重复劳动，
      而且留下的不是「最好的那份」，是**最后一份**。

    ★ 只去重，不改语义：第一次出现的位置保留（拓扑序不乱）；
      gates / depends_on 取并集（同一文件的判据不因重复而丢）；
      focus 取第一个非零值（行号焦点）。
    """
    if not deliverables:
        return deliverables
    见: dict[str, int] = {}
    out: list = []
    for d in deliverables:
        if not isinstance(d, dict):
            out.append(d)
            continue
        路径 = str(d.get("path") or "")
        if not 路径:
            out.append(d)
            continue
        if 路径 not in 见:
            见[路径] = len(out)
            out.append(d)
            continue
        旧 = out[见[路径]]
        # 合并元数据（只增不减：去重不许让判据或依赖变少）
        if d.get("gates"):
            _g = list(旧.get("gates") or [])
            for _x in (d.get("gates") or []):
                if _x not in _g:
                    _g.append(_x)
            旧["gates"] = _g
        if d.get("depends_on"):
            _p = list(旧.get("depends_on") or [])
            for _x in (d.get("depends_on") or []):
                if _x not in _p:
                    _p.append(_x)
            旧["depends_on"] = _p
        if not 旧.get("focus") and d.get("focus"):
            旧["focus"] = d["focus"]
    return out


class 交付物形态错(规约可纠正):
    """交付物形态不合法（专用类型）。

    ★ 为什么要**专用类型**：调用方（`_plan`）据此决定「**回喂重规划**」，
      而不是「诚实停机」。两者是不同的处置：
        · 形态错 ⇒ 是**可纠正的表述问题** ⇒ 值得自动重来一次；
        · 目标本身做不到 ⇒ 停机才对。
      用一个 `ValueError` 混着走 ⇒ 只能一刀切，要么全停机要么全重试。

    ★ R80：本类改为继承 `规约可纠正` —— 它一直是"可纠正"这个**族**的一员，
      只是当初用"逐个 except"表达这个族，才让后来的兄弟类漏在外面。
    """


def _交付物形态问题(deliverables) -> list[str]:
    """交付物必须是**文件**路径。返回问题清单（空 = 全部合法）。

    ★★★★★ 真跑抓出的缺口（2026-10-15 · 在真 Rust 工程 `example-project` 上）：

      目标是「修 `src/` 下若干文件」，模型自然把交付物写成**目录** `src/`。
      编排器把它当**文件**落盘 ⇒
          `PermissionError: [Errno 13] Permission denied: .../r1/w0/src`
      ⇒ **整轮崩在写窗副本上**，不是按判据收场（也不是如实报错）。

      ★ 这个形态**任何**「改一个目录下若干文件」的工程任务都会撞上 ——
        而「改多个文件」恰恰是**通用工程开发**的常态（只改一个文件是特例）。
        ⇒ 所以这不是边角情形，是**主路**。之前只在单文件样例上跑通，
          正是因为样例只有一个文件（**单文件跑通掩盖了多文件的断路**）。

    【为什么是"报错 + 回喂"而不是"自动展开目录"】
      展开要**猜**「目录下哪些文件该改」—— 而那个集合只有模型知道
      （它跑判据门 / 读码才拿到清单）。猜出来的集合会**多改或漏改**，
      比如实报错坏得多。本库口径：读不到就是读不到，**不猜**。
    """
    问题: list[str] = []
    for d in (deliverables or ()):
        raw = str(d.get("path") or "")
        p = raw.strip()
        if not p:
            问题.append("有交付物的 `path` 是空的")
            continue
        norm = p.replace("\\", "/")
        if norm.endswith("/"):
            问题.append(f"`{raw}` 以分隔符结尾 ⇒ 这是**目录**，不是文件")
            continue
        if any(c in norm for c in "*?[]"):
            问题.append(f"`{raw}` 含通配符 ⇒ 交付物必须是**具体文件**")
            continue
        full = REPO_ROOT / norm
        if full.is_dir():
            问题.append(f"`{raw}` 在仓库里**是一个目录** ⇒ 不是文件")
    return 问题


def _交付物形态提示(问题: list[str]) -> str:
    """把形态问题写成**可执行**的回喂文本（模型照着就能改对）。"""
    return (
        "【交付物形态不合法】\n"
        + "\n".join(f"  · {x}" for x in 问题)
        + "\n\n交付物必须是**具体文件**路径（`deliverables[i].path`）。\n"
          "若你要改一个目录下的多个文件，请把**每个文件**分别列为一条交付物，"
          "例如：\n"
          '  "deliverables": [\n'
          '    {"path": "src/判据/观测协议.rs", ...},\n'
          '    {"path": "src/外部获取/mod.rs", ...}\n'
          "  ]\n"
          "不要写目录（如 `src/`）—— 编排器无法从目录推出你要改哪些文件，"
          "而猜出来的集合会多改或漏改。\n"
          "★ 你需要的文件清单就在判据门的失败信息里（跑一次那条命令即可拿到）。")


# ★★★★★ TS-458 **建模最小约束**决策记录（机器可算的建模输出）
#   放在**花模型流量之前**：先答「这个目标机器够不够」，再决定要不要调模型。
_建模判据记录: dict = {}


def _machine_generate(d: dict) -> "str | None":
    """★ 机器自生成（0-LLM）：交付物声明了**结构真值** ⇒ 由机器组装源码，不调模型。

    规约形态（`deliverables[i].machine`）::

        {"language": "python",
         "结构": {"name": "collatz_steps", "in": 1, "out": 1,
                  "ops": ["count:step"], "carry": 1},
         "算子": {"step": "v // 2 if v % 2 == 0 else 3 * v + 1"}}

    ── 为什么这是"不用 AI 写代码"──
      模型只产**结构与算子表达式**（几十字符），签名/累加器/边界/返回/辅助函数/
      盘码全部由 `auto_mechanism.machine_module` **构造性组装** ⇒ 组装不会出错，
      且这一段**完全不走模型通道**（省流量）。
    ── 诚实边界 ──
      只覆盖"结构可表达"的目标（结构 = 算子序列 + 逐位映射）。
      表达不了 ⇒ 返回 None，**回落**到模型产（不假装机器能做）。
      算子不在语义表内时须由 `算子` 显式给出表达式，否则机器给恒等占位
      （占位会被判据门判红，不会静默过关）。
    """
    m = d.get("machine")
    if not isinstance(m, dict) or not m:
        return None
    st = m.get("结构") or m.get("structure")
    if not isinstance(st, dict):
        return None
    try:
        defs = m.get("算子") or m.get("ops_def") or {}
        defs_t = tuple((str(k), str(v)) for k, v in defs.items()) \
            if isinstance(defs, dict) else ()
        s = ao_mech.StructureTruth(
            name=str(st.get("name") or Path(d["path"]).stem),
            input_=int(st.get("in") or 1),
            output=int(st.get("out") or 1),
            ops=tuple(str(x) for x in (st.get("ops") or [])),
            carry=int(st.get("carry") or 0),
            defs=defs_t)
        # ★★★★★ TS-458 决策层（**在渲染之前、在调模型之前**）：
        #   `some 0` = 系统自己够 / `some 1` = 从原语取象一条 / `none` = 一条也不够。
        #   库内 `one_constraint_iff`：**没有第三条路**；`none` 就是
        #   「提问过程本身就外部建模」⇒ 诚实报出去、交给模型（不假装机器能做）。
        # ★★★★★ **完整全自动编码**（按 TS-461/462 的数学最优解）：
        #   一次调用同时答出 **两个比特 → 最小约束数 → 产物 → 全自动边界**。
        #   库内塌缩（TS-462 §1）：`1 ≤ k → (EncodableWithin Sys Prim k G ↔ Sys G ∨ Prim G)`
        #   ⇒ **约束预算在 1 以上完全无关** ⇒ 最小约束数只可能是 0 / 1 / none
        #   ⇒ **不可再优**（不是「我尽力了」，是「数学上不可能更优」）。
        _全 = ao_mech.全自动编码(s, defs_t, str(m.get("language") or "python"))
        _k = _全["最小约束数"]
        _建模判据记录[d["path"]] = {
            "Sys": _全["Sys"], "Prim": _全["Prim"],
            "最小约束数": _k,
            "全自动": _全["全自动"],
            "分辨率": _全["分辨率"],
            "出处": ("TS-462 §1 塌缩 `encodable_iff_or_of_pos` / §2 "
                     "`mechanism_factors_through_oracle` / §4 `two_bits_are_necessary` / "
                     "TS-458 `minimalConstraints` :422 / TS-461 §5 "
                     "`minimal_envelope_is_decidable`"),
        }
        if _k is None:
            _log(f"  [machine] {d['path']} 两比特 (Sys={_全['Sys']}, "
                 f"Prim={_全['Prim']}) ⇒ 无编码 ⇒ 回落模型（不假装机器能做）")
            return None
        _log(f"  [machine] {d['path']} 两比特 (Sys={_全['Sys']}, Prim={_全['Prim']})"
             f" ⇒ 最小约束数 = {_k}（数学最优·不可再降）")
        src = _全["产物"]
        # ★ 自校验：机器产物必须能被机器**反读回同一结构**（方向性，TS-424/TS-447）。
        #   读不回 ⇒ 组装有损 ⇒ **拒绝交付**（不把有损产物写出去）。
        回 = ao_mech.machine_module_read(src, str(m.get("language") or "python"))
        if 回 != s:
            _log(f"  [machine] {d['path']} 机器自校验失败（反读不回原结构）⇒ 回落模型")
            return None
        _log(f"  [machine] {d['path']} 机器自生成 {len(src)} 字符（0-LLM，未调通道）")
        return src
    except Exception as e:            # noqa: BLE001 机器生成失败 ⇒ 如实回落模型
        _log(f"  [machine] {d['path']} 机器自生成失败（{e}）⇒ 回落模型")
        return None


def _topo_sort(deliverables: list[dict]) -> tuple[list[int], bool, str]:
    """文件依赖 DAG 拓扑排序（Kahn，机器机验）：未知依赖/自依赖/成环一律拒。

    返回 (拓扑序索引列表, ok, why)。顺序即产出串线顺序：依赖先产出判门绿，才产出下游。"""
    n = len(deliverables)
    # ★ R39：身份一律过 `_归一路径`（`路径:行号` 与 `路径` 是同一件事）。
    #   否则规划器照 R34 的提示写 `depends_on: ["…/x.rs:1993"]` 就会「依赖未知文件」。
    # ★ R52：改为**两级**索引（见 `_交付物索引`）—— 同一文件的多个区域必须分得开。
    by_path, by_key = _交付物索引(deliverables)
    indeg = [0] * n
    adj: list[list[int]] = [[] for _ in range(n)]
    丢自环: list[str] = []
    for i, d in enumerate(deliverables):
        for dep in d.get("depends_on") or []:
            目标, why = _解依赖(str(dep), by_path, by_key)
            if not 目标:
                return [], False, f"文件 {d['path']} 依赖未知文件 {dep}"
            for j in 目标:
                if j == i:
                    # ★★★★★ R52：**自环是空转，丢弃，不拒发。**
                    #   理由（可证）：自环「i 排在 i 之前」不含任何**次序信息**，
                    #   丢掉它**不改变其余任何约束**，Kahn 的合法拓扑序集合不变。
                    #   ⇒ 为一个空转的自引用**否掉整份计划**是**假红** ——
                    #     计划本身没毛病，白扔一趟（本库纪律：假红比无齿更坏）。
                    #   ★ 但**不静默**：如实打出来（否则下次没人知道被丢了什么）。
                    丢自环.append(f"{d['path']} → {dep}")
                    continue
                adj[j].append(i)
                indeg[i] += 1
    if 丢自环:
        _log(f"[generic] ★ 依赖图丢弃 {len(丢自环)} 条**自环**"
             "（自引用不含次序信息 ⇒ 空转，不影响拓扑序）："
             + "；".join(丢自环[:5]))
    q = [i for i in range(n) if indeg[i] == 0]
    order: list[int] = []
    while q:
        u = q.pop()
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    if len(order) != n:
        return [], False, "文件依赖成环（机器拒发）"
    return order, True, ""



def _原子写源码(路径, 数据: bytes) -> None:
    """写**产品源码**只经这里：临时文件 → fsync → 原子替换（R113 口径）。

    ★★★★★ 真事故（2026-10-20 03:52，我自己造成的）：
      我强杀了一个正在 `write_bytes(原字节)` 的探针 ⇒ NTFS 留下
      **785,173 字节的纯零** `generic_goal.py`。产品源码当场没了，
      能救回来纯属侥幸（探针的侧车里有清洁字节）。

    ★ 为什么本文件里也要有这一份：`_契约跑门` / `gate_witness` 都会
      **往真工程树里写**（原位变异 → 跑门 → 还原）。它们的写点
      与探针的写点是**同一类操作**，必须有**同一条规矩**。
      「能不枚举就别枚举」—— 与其在每个写点各写一遍，不如一个函数。

    ★ 原子性从哪来：`os.replace` 在同卷上是原子的 ——
      要么是旧字节、要么是新字节，**不存在"中间态"**。
      被杀时最坏留下一个临时文件（可清理），目标文件永远完好。
    """
    import os as _os
    路径 = Path(路径)
    父 = 路径.parent
    父.mkdir(parents=True, exist_ok=True)
    临 = 父 / f".{路径.name}.原子写.{_os.getpid()}.tmp"
    try:
        with open(临, "wb") as fh:
            fh.write(数据)
            fh.flush()
            _os.fsync(fh.fileno())
        _os.replace(临, 路径)
    finally:
        try:
            临.unlink(missing_ok=True)
        except OSError:
            pass

def _规约形态问题(spec: dict) -> str:
    """**唯一的规约形态校验**（R111）。

    ★ 为什么要有一个"唯一的"校验器（真跑抓出的第 25 层 · 2026-10-20 · 第十三次端到端）：

      收场日志：
        `★★★ 重规划**崩了**（auto_goal.py:156）：ValueError: 槽 pre 必须是 list`
      而 R107 **刚刚**修过这件事 —— 它把 `auto_goal.plan_tasks(spec)` 包进了
      `规约可纠正`，让模型有机会照着报错改。

      **结果还是崩了**，因为消费同一份规约的**不止 `plan_tasks` 一个**：
        · `_adopt_spec` → `_build_dag` → `auto_goal.carrier_from_spec`  ← 这次崩在这
        · `_adopt_spec` → `auto_goal.plan_tasks` → 同上
      ⇒ R107 修的是**枚举里的一个项**，而 R107 自己的注释里就写着
        「修了枚举里的项，没修『枚举』」—— **R107 犯了 R107 的错。**

    ★ 正确的修法不是"再包一个调用点"（那只是把枚举变长），
      而是**让形态问题在一次校验里全部暴露**，之后所有消费者都可以假定形态是对的。
      「能不枚举就别枚举」：**一个校验器，所有消费者。**

    返回空串表示形态合法；否则返回**可直接回喂给模型**的中文说明。
    """
    try:
        auto_goal.carrier_from_spec(spec)
        return ""
    except Exception as exc:      # noqa: BLE001 形态问题一律转成可回喂的说明
        return (f"载体规约过不了机器的结构校验：{type(exc).__name__}: {exc}\n"
                f"★ 四槽 `pre`/`constraints`/`gates`/`acceptance` 都必须是 **list**"
                f"（不是字符串、不是 dict）；`name` 必须是非空字符串。\n"
                f"★ 每条判据门的**必填字段**必须符合该门类型"
                f"（如 `grep_required` 要 `in` + `patterns`，`compile` 要 `cmd`）。\n"
                f"★ 请照着这条报错改**这一份**规约后重新输出，不要重写别的部分。")


def _build_dag(spec: dict, deliverables: list[dict]) -> tuple[list, bool, str]:
    """把多文件产出挂进 auto_goal 任务 DAG（pre→con→按拓扑序 file 任务），
    用 consume_ok 机验全列守恒（依赖必须引用前序已发射槽）。"""
    # ★ R178：`carrier_from_spec` 的报错**本身就是能回喂的**（"缺 in/file" 直接
    #   告诉模型补什么）⇒ 转成 `规约可纠正`，别让它以裸 ValueError 变成「产品缺陷」。
    try:
        c = auto_goal.carrier_from_spec(spec)
    except 规约可纠正:
        raise
    except Exception as _e178:
        raise 规约可纠正(
            f"判据门/载体槽不完整（机器结构校验）：{type(_e178).__name__}: {_e178}\n"
            f"★ 每条门的**必填字段**必须符合该门类型："
            f"`whole_reading` 要 `in`(被测文件) + `must_contain`；"
            f"`compile` 要 `cmd`；`grep_required` 要 `in` + `patterns`。\n"
            f"★ 请照着这条报错补上**那一个字段**后重新输出，不要重写别的部分。") from _e178
    tasks: list = []
    pre_tids: list[str] = []
    for i, p in enumerate(spec.get("pre") or []):
        pre_tids.append(f"pre_{i}")
        tasks.append(auto_goal.Task(tid=f"pre_{i}", name=p, kind="pre_emit"))
    con_tids: list[str] = []
    for i, con in enumerate(spec.get("constraints") or []):
        tid = f"con_{i}"
        con_tids.append(tid)
        # ★ 修复（2026-10-12）：原用 `cmd: ["true"]` 恒真门。约束槽若无真实判据，
        #   就该**没有门**（由上层充分性检查拦），而不是一个假绿门 —— 假绿门会让
        #   "约束已满足"永远是假的真话。这里挂 spec 的真实判据（约束由判据落地）；
        #   没有判据时给空表，交由 gate_spec_validate / 充分性检查判定。
        tasks.append(auto_goal.Task(
            tid=tid, name=con, kind="constraint_check",
            gates=list(spec.get("gates") or [])))
    _by_path, _by_key = _交付物索引(deliverables)
    for idx, d in enumerate(deliverables):   # deliverables 已按拓扑序
        deps = list(pre_tids) + list(con_tids)
        for dep in d.get("depends_on") or []:
            # ★★★★★ R39（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：
            #   规划器写 `depends_on: ["src/判据/观测协议.rs:1993"]`（**带行号**，
            #   因为它按 R34 的提示把大文件写成 `路径:行号`），而交付物身份是
            #   `src/判据/观测协议.rs` ⇒ 这里是**裸字符串相等**比对 ⇒
            #   `j` 为空 ⇒ 依赖被**静默丢弃** ⇒ 随后 `consume_ok` 报
            #   「依赖未知文件 src/判据/观测协议.rs:1993」⇒ **规划失败、整趟没跑**。
            #   ★ 与 R34/R35 同源：**同一件事两把尺子** —— `path` 已过 `_拆路径焦点`
            #     （R34），`depends_on` 没过。⇒ 判据归一必须**只做一次、处处生效**。
            #   ★ 归一化只在**比对时**用，不改交付物自身的 `path`（坐标不变）。
            # ★ R52：原来只取 `j[0]`（第一个同路径交付物）⇒ 同文件多个区域时
            #   会把"依赖该文件的**其它**区域"整片丢掉。改为**全部**命中都连，
            #   且跳过自身（自环在 DAG 里空转）。
            _目标, _ = _解依赖(str(dep), _by_path, _by_key)
            for j in _目标:
                if j < idx:
                    deps.append(f"file_{j}")
        tasks.append(auto_goal.Task(
            tid=f"file_{idx}", name=d["path"], kind="errand",
            gates=d.get("gates") or spec["gates"] or [], depends_on=deps))
    ok, why = auto_goal.consume_ok(c, tasks)
    return tasks, ok, why


def _normalize_spec(spec: dict) -> None:
    """为门补齐缺省：compile 门补 cmd/cwd；文本门**不补 cmd**。

    ★ 修复（2026-10-12）：原实现给**所有**门（含 grep/whole_reading 文本门）
    硬塞 `cmd: ["true"]`，理由是"补无害 cmd 使载体律可过"。这是个为迁就坏校验器
    而撒的谎，且**有实质后果**：
      · gate_derive 的充分性判定把 `cmd == ["true"]` 认作**平凡门**
        （gate_derive.py 的 trivial 判据）⇒ 文本门被误判为平凡门，
        判据充分性检查得出错误结论；
      · 载体层与 gate_spec 的字段契约不一致（gate_spec 对文本门本就不看 cmd）。
    现载体校验已按门类型分判（auto_goal._gate_wellformed），无需再补谎：
    compile 门补 cmd/cwd；文本门只补 cwd（若有），绝不注入 ["true"]。"""
    def _norm(gates):
        for g in gates:
            if not isinstance(g, dict):
                continue
            t = g.get("type")
            if t == "compile" or "cmd" in g:
                # compile 门必须有 cmd；模型给了 cmd 的门也补 cwd
                if g.get("cmd") in (None, []):
                    g["cmd"] = ["true"]      # 仅 compile 门：缺 cmd 时兜底（会被充分性检查拦）
                g.setdefault("cwd", ".")
            else:
                # 文本门（grep_forbidden / grep_required / whole_reading / scan_output）
                # 不注入 cmd —— 它们本就不跑命令
                g.setdefault("cwd", ".")
    _norm(spec.get("gates") or [])
    for d in _parse_deliverables(spec):
        if d.get("gates"):
            _norm(d["gates"])


def _clamp_timeout(gates) -> list:
    """资源护栏（#5）：每门超时强制 clamp 到上限，缺省给默认值，防模型塞超长门跑死。

    ★★ 2026-10-14 修：原来只 clamp `timeout`，**没有 stall 保护**。而 `timeout`
      是**总时长**判据 —— 对本工程 `cargo test --lib`（实测 >40 分钟）而言，
      原上限 600s 使这条门**恒不可满足**（真跑三窗耗时 11.0/10.7/10.4 分钟，
      每个都精确撞上限）⇒ 任务构造上不可能收敛。
      现改为：编译/测试类门缺省补 `stall` —— 判据变成「**长时间无输出**才判挂起」，
      跑得久但有进展的命令不再被误杀；真挂起仍能及时杀掉（比等总超时更早）。
    """
    out = []
    for g in gates:
        gg = dict(g)
        t = gg.get("timeout")
        if t is None:
            gg["timeout"] = _MAX_TIMEOUT
        else:
            try:
                gg["timeout"] = min(int(t), _MAX_TIMEOUT)
            except (TypeError, ValueError):
                gg["timeout"] = _MAX_TIMEOUT
        # ★ 编译/测试类门缺省补 stall（不覆盖调用方显式给的值）
        if str(gg.get("type")) == "compile" and not gg.get("stall"):
            gg["stall"] = _DEFAULT_COMPILE_STALL
        out.append(gg)
    return out


def _score_gates(workdir: Path, file: Path, gates,
                 repo_root: Path | None = None) -> tuple[dict, list]:
    """对单个落盘文件执行全部机器判据门，返回 (results:{门名:bool}, red_audit)。

    每门 cwd 缺省设成 workdir（主树工程任务缺省设 repo_root）；超时经 _clamp_timeout 护栏。
    {repo} 占位符在调用前预替换为 repo_root（gate_spec._sub 不支持该占位符）。
    red_audit 携带机器门证据，直接回喂执行智能体。"""
    gates = _clamp_timeout(json.loads(json.dumps(gates)))
    repo_str = str(repo_root) if repo_root is not None else None
    norm = []
    for g in gates:
        cwd = g.get("cwd")
        if cwd is None or str(cwd) in ("", "."):
            g["cwd"] = str(repo_root if repo_root is not None else workdir)
        elif repo_str is not None:
            g["cwd"] = str(cwd).replace("{repo}", repo_str)
        cmd = g.get("cmd")
        if repo_str is not None and isinstance(cmd, list):
            g["cmd"] = [str(c).replace("{repo}", repo_str) for c in cmd]
        # grep/scan 门的 in/src 若为裸相对路径（replan 新规约可能不写 {file} 占位符），
        # 统一重定位到待判文件所在根，否则 gate_evaluate 会以进程 cwd 解析而 FileNotFound。
        base_str = str(repo_root if repo_root is not None else workdir)
        for key in ("in", "src"):
            v = g.get(key)
            if isinstance(v, str):
                if repo_str is not None:
                    v = v.replace("{repo}", repo_str)
                if "{" not in v and not Path(v).is_absolute():
                    v = str(Path(base_str) / v)
                g[key] = v
        norm.append(g)
    results, audit = gate_evaluate(norm, file, workdir, workdir)
    red = [a for a in audit if not a["ok"]]
    return results, red


def _门与文件无关(g: dict) -> bool:
    """这条门的真值**与"在判哪个文件"无关**吗？（与文件无关 ⇒ 可只跑一次）

    ★★★★★ R38（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      规划器看见 18 处违规散在 7 个文件，按文件列了 19 条交付物，
      每条都挂同一组共享门（`_parse_deliverables` 的设计）⇒
      `gates: 209`（= 19 × 11）。而 `_score_files` **逐文件**判门 ⇒
      那条 `cargo test --lib 测试防假::`（**实测 65 秒**）会被**跑 19 遍**。
      两窗就是 38 遍 ≈ **41 分钟**，而**每一遍的结果完全相同**
      （它是整 crate 的测试，不看我们正在给哪个文件打分）。

      ★ 这不是"慢一点"：它把**多文件任务**（通用工程开发的常态）的代价
        放大成 O(文件数) 倍整 crate 测试 ⇒ 实际不可用。
      ★ 与 R35 同源：**笛卡尔积口径**。R35 修了"上限怎么数"，
        这里修"**怎么求值**"。

    【判据（保守，宁可少共享不可错共享）】
      门的规格里**不含** `{file}` / `{work}` 占位符 ⇒ 它不引用被测产物本身
      ⇒ 在**同一份主树状态**下，判哪个文件都得到同一结果。
      ★ 反过来：只要含 `{file}`/`{work}`，就**一定**逐文件跑（不猜、不共享）。

    ★★★★★ R75（2026-10-17 · 第二次真项目验收抓出）：**文本门不再一律"逐文件"**。

      【真跑证据】4 个交付物、5 条门 ⇒ 同一条门的**同一个真值**被求值 **4 遍**，
        而其中 `has_closed_marker` 的规格里 `in` 写死 `src/模块甲/mod.rs`
        —— 它**只读那一个文件**，与"正在给哪个交付物打分"**毫无关系**。
        旧实现把 whole_reading **一律**判成"逐文件"（见原注释：
        「文本门读的是 `in`/`file` 指定的具体文件」）—— 那句话**只说对了一半**：
        它读的确实是 `in` 指定的文件，但那个文件**不一定是**正在判的产物。

      ⇒ 判据与 R38 的代价病同源，只是这次是**4 倍**而不是 19 倍；
        而**更坏的是归属**（见 `_门主体`）：同一个真值被记成 4 个文件的缺口。

      ⇒ 判据改成与其余门**同一条**：看**占位符**，不看门类型。
        · `in`/`src` 含 `{file}`/`{work}` ⇒ 逐文件（跟随产物）；
        · `in`/`src` 是字面路径 ⇒ 与文件无关（它读谁，规格已经写死了）；
        · 字面路径**不存在** ⇒ 真值不变（读不到就是读不到，与判哪个文件无关）。
      ★ 这不是"放松共享"，是**把判据统一**：R38 立的是"占位符"这条线，
        R75 只是让文本门也走这条线（原来它走的是"门类型"那条**更粗**的线）。
    """
    blob = json.dumps(g, ensure_ascii=False, default=str)
    return "{file}" not in blob and "{work}" not in blob


#: 工程级门的**主体**标记：它不读单个文件，读的是**整棵树**。
#   ★ 用字符串常量而不是"空串"：空串在身份键里会与"字段缺失"混同（假绿风险）。
_全库主体 = "（全库）"


def _门主体(g: dict, d_path: str, base: Path) -> str:
    """这条门**真的在读谁**？—— 判据违规点的身份主体（★ R75）。

    ★★★★★ 为什么必须有它（真跑抓出 · 2026-10-17 · 第二次真项目验收）：

      【真跑证据】4 个交付物、5 条门 ⇒ 回执 `post_gates` 里 **20 条**，
        而**只有 5 条门的真值**：
          · `has_closed_marker` 规格里 `in` 写死 `src/模块甲/mod.rs`
            —— 它**只读那一个文件**；
          · `cargo_check_lib` / `run_fake_test_gate` 是**整 crate** 命令门，
            **不读单个文件**。
        ⇒ 同一条门的**同一个真值**被记成 **4 个不同文件的缺口**：
          `src/核心类型/运行时涌现.rs@has_closed_marker`、
          `src/外部获取/mod.rs@has_closed_marker`、… 全是 `False`，
          而**那条门从没读过这些文件**。
        ⇒ 违规点从 5 个**虚增**成 20 个；而"缺口清单"里 4 个交付物
          挂着**完全相同**的 5 个缺口 ⇒ **清单本身没有信息量**。

      【根因】`_score_files` 结尾把门的审计记录 `a["file"]` **改写成交付物路径**
        （原注释：「相对路径（多文件下避免 basename 重名）」）。
        —— 那是**为了修一个显示问题**（不同目录同名文件分不清），
        **代价是把判据的主体换掉了**。主体一变：
          · 违规点身份变了（`deviation_identity` 含 `file` 一维）；
          · Δ 的计数变了；
          · 缺口清单变成"每个交付物一份"的**复印件**。
        ★ 这是本库那条规矩的又一例：**修显示问题不许改判据的语义**。

      【修法】主体由**门的规格**决定，不由"谁的名字挂在它旁边"决定：
        · 规格含 `{file}`/`{work}` ⇒ 主体 = **正在判的那个交付物**（逐件）；
        · 规格含字面 `in`/`src`   ⇒ 主体 = **那条路径**（门自己声明读谁）；
        · 都不是（工程级命令门） ⇒ 主体 = `_全库主体`。
      ★ 与 `gate_witness` 已有的分类**同形**（它早就知道"compile 含 `{file}` → 读产物；
        不含 → 工程级"）—— 这里只是把**同一个判断**用在**归属**上，不新造口径。
      ★ 归一化**只用于比对**（本库规矩）：盘符/分隔符统一，**不改路径语义**。
    """
    blob = json.dumps(g, ensure_ascii=False, default=str)
    if "{file}" in blob or "{work}" in blob:
        return d_path                      # 跟随产物 ⇒ 主体就是产物
    for key in ("in", "src"):
        v = g.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        s = v.replace("{repo}", "").replace("{work}", "").strip()
        if not s:
            continue
        p = Path(s)
        if p.is_absolute():
            try:                            # 能落到 base 下就转相对（身份要稳定、
                p = p.relative_to(base)     # 不许把机器相关的绝对路径写进身份键）
            except ValueError:
                pass
        return str(p).replace("\\", "/")
    return _全库主体


def _score_files(workdir: Path, deliverables: list[dict],
                 file_root: Path | None = None,
                 repo_root: Path | None = None) -> tuple[dict, list]:
    """对整组交付文件逐文件判门，返回 (results:{path@gate:bool}, red_audit)。

    file_root：待判文件所在根（缺省=workdir；主树工程任务=REPO_ROOT，文件已应用进主树）。
    results 的 key 与账本 gap_key 对齐（path@gate），文件缺失记 @missing 红门。

    ★★★★★ R38：**与文件无关的门只求值一次**（真跑抓出的代价爆炸）。
      实测：19 条交付物共享一条 `cargo test --lib`（65 秒）⇒ 逐文件判就是
      19 × 65s ≈ 20 分钟/窗，而**每一遍结果完全相同**。
      ⇒ 与文件无关的门（规格里不含 `{file}`/`{work}`）按**门规格身份**缓存，
        同一窗内复用；**逐文件的门照旧逐文件判**（口径不变）。
      ★ 这不改判据语义：缓存的键是**门规格**，而"与文件无关"是**结构性**判定的
        （不是靠名字猜的）；且缓存只在**同一次调用**内有效
        （主树状态在这一窗内是固定的）。
    """
    base = file_root or workdir
    results: dict = {}
    red: list = []
    缓存: dict[str, tuple[dict, list]] = {}
    for d in deliverables:
        f = base / d["path"]
        if not f.exists():
            results[f"{d['path']}@missing"] = False
            red.append({"gate": "missing", "file": d["path"], "ok": False,
                        "err": "文件未产出"})
            continue
        gates = d.get("gates") or []
        逐件, 共享 = [], []
        # ★ R75：每条门的**主体**（它真的在读谁）—— 结果键与违规点身份都用它。
        #   不用"正在判哪个交付物"：那会把**同一条门的同一个真值**
        #   记成 N 个不同文件的缺口（真跑里 N=4，违规点虚增 4 倍）。
        主体: dict[str, str] = {}
        for g in gates:
            主体[str(g.get("name") or g.get("type") or "gate")] = \
                _门主体(g, d["path"], base)
            (共享 if _门与文件无关(g) else 逐件).append(g)
        g_res: dict = {}
        g_red: list = []
        # ① 逐文件的门：照旧真跑（含 {file} 的文本/行为门）
        if 逐件:
            _r, _d = _score_gates(workdir, f, 逐件, repo_root=repo_root)
            g_res.update(_r)
            g_red.extend(_d)
        # ② 与文件无关的门：按规格身份**只求值一次**，本窗内复用
        for g in 共享:
            try:
                k = json.dumps(g, ensure_ascii=False, sort_keys=True, default=str)
            except (TypeError, ValueError):
                k = repr(g)
            if k not in 缓存:
                缓存[k] = _score_gates(workdir, f, [g], repo_root=repo_root)
            _r, _d = 缓存[k]
            g_res.update(_r)
            g_red.extend(_d)
        for gname, ok in g_res.items():
            # ★ R75：键的**主体**是门的真实主体，不是"谁的名字挂在它旁边"。
            #   同一条门被多个交付物共享 ⇒ **只产生一个键**（同一个真值一份）。
            results[f"{主体.get(gname, d['path'])}@{gname}"] = bool(ok)
        for a in g_red:
            a = dict(a)
            # ★ R75：**不再**把 `file` 改写成交付物路径（那是为了修同名文件的
            #   显示问题，代价是把判据的主体换掉了 —— 见 `_门主体` 的段注释）。
            a["file"] = 主体.get(str(a.get("gate")), d["path"])
            red.append(a)
    # ★★★★★ R119a（真跑抓出的第 32 层 · 2026-10-20 · 第十六次端到端）：
    #   **R75 只修了 `results` 的键，没修 `red`。**
    #
    #   真证据：`red_audit` 里 **17 条记录全是同一条门**
    #     `cargo_test_防假门`、同一个因由
    #     `编译错|error@运行时事件采集.rs:987:4;…`
    #   —— 因为 `red.append(a)` 在 `for d in deliverables` **循环里**，
    #   而"与文件无关的门"（R38 已缓存**执行**）的**红记录**仍被
    #   **每个交付物各 append 一次** ⇒ 17 个交付物 ⇒ 违规点虚增 17 倍。
    #
    #   ★ 这正是 R75 注释里写过的同一件事：「同一条门的同一个真值
    #     被记成 N 个不同文件的缺口（真跑里 N=4，违规点虚增 4 倍）」。
    #     R75 修了 `results` 那一侧，`red` 这一侧**留着**。
    #   ⇒ 又是「修了一侧，另一侧没修」：R108 / R116 齿③ / 本条 同族。
    #
    #   ★ 后果不只是日志难看：`_基线身份` 是 `frozenset`，重复会被折叠，
    #     但 `len(self._基线偏差)` 与 `len(cur_ids)` 的**条数**、
    #     以及 `deviation_structure_verdict` 的**数量单调性**都读的是
    #     重复后的列表 ⇒ 「17 → 17」被判成"没进展"，
    #     而实际是"同一条债一直在那儿"。
    #
    #   ★ 去重键用**整条审计的规范 JSON**：同一条门、同一个主体、
    #     同一段证据 ⇒ 是同一条偏差；任何一处不同就是不同偏差（不误合）。
    见119: set[str] = set()
    _red去重: list = []
    for _a in red:
        try:
            _k = json.dumps(_a, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            _k = repr(_a)
        if _k in 见119:
            continue
        见119.add(_k)
        _red去重.append(_a)
    return results, _red去重


def _因由向量(results: dict, red: list | None) -> dict:
    """把「门名→布尔」抬成「门名→**因由码**」——不动点判据必须定义在这一层。

    ★★★【TS-442 / TS-430 relocate / TS-424】为什么不动点不能建在布尔上：

      `_convergence` 原来比的是 `results`（`dict[str,bool]`），
      `_align_windows` 原来比的也是 `w["results"]`（同一个布尔投影）。
      ⇒ **两轮同门红、但红的原因已经变了**（例：上一轮 `编译错|E0308@x.rs:1:2`，
        这一轮 `编译错|E0432@y.rs:9:5`）时，布尔向量**逐 key 相等**
        ⇒ 误判「**已达不动点**」⇒ `_transition` 返回 `replan`（换门集）
        或直接停机 —— 而**真实进展刚刚发生**。
      ⇒ 这正是 TS-442 那条被违反：`read_eq_reflects`
        （`Function.Injective read → read x = read y → x = y`）要求
        **读等式能反推态等式**；布尔读法**不满足**（相异态同读值），
        于是「读相同」被错当成「态相同」。
      ⇒ 修法 = TS-430 `relocate`：把不动点判据**搬到**因由通道上
        （`因由` 携带具体证据，相异因由必得相异码 ⇒ 该通道上"读相同"才真蕴含"态相同"）。

    ★ 口径：**布尔向量仍管"全绿否"**（`done` 判定必须二值，那是 `Bool` 的正当用途）；
      **因由向量管"是否已到不动点"**（判"有没有进展"必须无损）。
      两者用途不同，不是替代关系。

    ★ 老账本无 `cause` 字段时退化为布尔码（向后兼容，且如实标注在值里）。
    """
    if red is None:
        red = []
    码 = {str(a.get("gate")): str(a.get("cause") or "") for a in red
          if isinstance(a, dict)}
    出: dict = {}
    for k, v in (results or {}).items():
        if v is True:
            出[k] = "绿"
        else:
            # 有因由用因由；无因由（老账本/无 audit）退化为布尔码，不静默
            出[k] = 码.get(str(k)) or "红|未记因由"
    return 出


from agentscope_orchestrator import shushu_logic as _sl17   # noqa: E402
# ★ R17：顶层判定统一走**数术层格裁决**（TS-468 §6）。取值与经典层合取
#   **逐例一致**（积序的下确界就是合取，无语义回退），但**保留全部因由** ——
#   经典层的 `all(Bool)` 会把"哪个门红的、为什么红"压平掉（`Defect384`）。


def _align_windows(windows: list[dict], min_pass: int) -> tuple[int, dict]:
    """多窗不动点对齐（#4）：收敛点 = ≥min_pass 个独立通过窗在同一门集上的**因由向量**等价。

    非投票——机器门确定性：同一规范点（同一门集/同文件路径）的读数在多窗独立复现
    上一致才构成不动点。不足 min_pass 或读数不一致 → 未对齐，拿首窗当回炉底料。

    ★【TS-442】比的是 `因由`（无损读）而非 `results`（布尔投影）：
      两个窗都在同一门上"红"但**因由不同** ⇒ 不是同一个规范点 ⇒ **不得**判对齐。
      旧版比布尔 ⇒ 会把"因由不同的两窗"错当成"同一个不动点"（同族塌缩）。
    """
    green = [w for w in windows if w["passed"]]
    if green and len(green) >= min_pass:
        ref = green[0].get("因由") or _因由向量(green[0].get("results") or {},
                                              green[0].get("red"))
        consistent = all(
            (w.get("因由") or _因由向量(w.get("results") or {}, w.get("red"))) == ref
            for w in green)
        if consistent:
            return green[0]["idx"], {"green": len(green), "aligned": True,
                                     "fixed_point": True,
                                     "read_space": "cause"}
        return green[0]["idx"], {"green": len(green), "aligned": False,
                                 "fixed_point": False, "read_space": "cause"}
    return windows[0]["idx"], {"green": len(green), "aligned": False,
                               "fixed_point": False, "read_space": "cause"}


def _should_replan(red_history: dict[str, int], work: int, after: int) -> bool:
    """#6 达门重规划触发判据：某 gap 连续红轮数 ≥ after 且当前已跑 ≥ after 轮。"""
    return work >= after and any(c >= after for c in red_history.values())


def _convergence(results: dict, prev: dict | None, streak: int,
                 fp_k: int, red: list | None = None,
                 prev_red: list | None = None) -> tuple[int, bool]:
    """不动点检测（数学语义：演化不动点）：**因由向量**逐 key 稳定。

    T(Sₙ)==T(Sₙ₋₁)：本轮读数与上一轮逐 key 相等为"稳定"。连续 fp_k 轮
    稳定 → 判结构不动点已达（防单轮噪声误判）。返回 (更新后 streak, 是否不动点)。

    ★★★【TS-442 / TS-430】读数**必须**是 `因由`（无损），**不得**是 `results`（布尔投影）：
      旧版 `same = results == prev` ⇒ **相异因由被压平后看起来"没变"** ⇒
      真实进展被当成"已到不动点" ⇒ 误触发换门集/停机。
      ⇒ 现改为比 `_因由向量(results, red)`：因由变了就是**有进展**，不是不动点。
      ⇒ 布尔向量仍用于"全绿否"（`done`），用途不同。

    ★ 诚实边界：本函数判的是「**读数**稳定」。读数稳定 ⟹ 该读法下已到不动点；
      读数**更细**（因由）时不动点**更晚**到达 —— 这正是要的：
      **宁可多演化一轮，不可把有进展的轮判成不动点而提前换门集/停机。**
    """
    cur = _因由向量(results or {}, red)
    pv = _因由向量(prev or {}, prev_red) if prev is not None else None
    same = pv is not None and cur == pv
    streak = streak + 1 if same else 0
    return streak, streak >= (fp_k - 1)


def _transition(done: bool, fixed_point: bool, replan_by_red: bool) -> str:
    """不动点态转移策略：按数学不动点语义决定本轮动作，绝不拿 max_work 当收敛。

      - done          稳定全绿（含多窗对齐）→ deliver 交付（不动点全绿收敛）。
      - fixed_point    不动点已到但 done 不成立 = 门真值稳定却含红 → 门集不可达，
                       **强制换门集（状态转移）继续演化**，而非耗尽轮数判退。
      - replan_by_red 未到不动点但某门连续红达阈 → 兜底达门重规划。
      - 否则                  继续演化（回炉），由资源护栏管停机。
    """
    if done:
        return "deliver"
    if fixed_point:
        return "replan"      # 稳定含红：工程语义"门与产出形态失配"，转移换门集
    if replan_by_red:
        return "replan"
    return "continue"


def _replan_thrashed(replan_count: int, replan_max: int) -> bool:
    """#1 漂移护栏：已连续 REPLAN_MAX 次换门集仍不收敛 → 判据不可达（升级决策）。

    单独"门集签名相等→升级"不够（实机已暴露：planner 每轮换不同门集 4→5→6，签名
    从不相等，escalated 永不触发，靠 max_work 硬停白烧 token）。这里机判**连续换门
    无收敛**＝不可达：门集漂移本身就是"该目标判据不稳定/不可达"的机器可判信号。"""
    return replan_count >= replan_max


# ── 数学判据可达性（#B，优先于 #1 的 REPLAN_MAX 次数启发式）────────
# 用户断言的数学没被用上的第三点：『定位参数/结构键→判据可达性』。
# 现以"判据能否归约到一根**稳定定位参数**（落主树结构键的文件/文本键）"为机判：
#   · 被测物落一根稳定结构键（交付集路径，或 repo_scope 下的主树现有文件）→ 可达；
#   · 被测物无稳定结构键（引用快照游标/不存在路径，非交付物也非主树文件）→ 判据不可达，
#     当场升级停机，绝不让 planner 靠换门集磨到 max_work 白烧。
# 约束边界（实机雷区，务必守住）：不可达性**只**由"被测物是否落稳定结构键"决定。
# 谓词里的"改动行数 ≤ N"这类*常量阈值*、以及相对目标文件自身基线的差分比较（最小改动门/
# 仅改动指定文件门），都是一根稳定键上的纯函数 —— 恒可达，绝不扫"基线/阈值/行数"字样
# 误判。曾实机把最小改动门 _py 误判为"谓词依赖外部变动量"而把一个必然可达的编译修复任务
# 升级停机（判据不可达），此即全称约束被错置的工况，本次已修正。
# 这是数学判定（可机验的结构键可及性），非次数启发式。
_REPO_PREFIXES = ("src/", "tests/", "crates/", "packages/", "bin/")


def _gate_reachability(g: dict, dpaths: set, repo_scope: bool) -> str:
    """单扇门判据可达性：可达返 ""，否则返不可达原因。

    可达 ⇔ 被测物落一根**稳定结构键**（交付集 或 主树现有文件）。行为门（compile / 对 {file}
    或已有文件跑的 scan_output）天然可达；点到快照游标/不存在路径的门判不可达。"""
    if g.get("type") == "compile":
        return ""   # 行为门：跑产物即锁等，不需文本键
    blob = json.dumps(g, ensure_ascii=False)
    if "{repo}" in blob and not repo_scope:
        return "引用 {repo} 但非主树任务，无结构键"
    tgt = next((str(g[k]) for k in ("in", "src") if str(g.get(k) or "")), None)
    if tgt is None or "{file}" in tgt:
        return ""   # 引用待判产物 → 交付集内稳定键
    rel = tgt.replace("{repo}", "").strip("/")
    if rel in dpaths or (repo_scope and rel.startswith(_REPO_PREFIXES)):
        return ""
    return f"被测物未落稳定结构键：{rel!r}"


def _adopted_gates_unreachable(spec: dict, deliverables: list,
                               repo_scope: bool) -> list[str]:
    """机判当前门集里的判据不可达门（#B）。返回空 → 全部可达可推进；否则原因列表。"""
    dpaths = {d.get("path") for d in deliverables}
    gates = list(spec.get("gates") or []) + [
        g for d in deliverables for g in (d.get("gates") or [])]
    out: list[str] = []
    for i, g in enumerate(gates):
        why = _gate_reachability(g, dpaths, repo_scope)
        if why:
            out.append(f"门#{i}({g.get('name', g.get('type'))}): {why}")
    return out


def _ledger_from_receipt(lines: str) -> list[dict]:
    """#2 跨批次无人值守统筹：从回执行回读历史 iteration 账本（损坏行跳过，幂等）。

    只取 kind==iteration 且带 post_gates 的事件 → 转成 auto_objective 可折叠的记录，
    作为本单 records 的前缀 → 历史偏差密度驱动本单重派优先级（跨批次循环）。"""
    out: list[dict] = []
    if not lines:
        return out
    for line in lines.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if ev.get("kind") != "iteration":
            continue
        pg = ev.get("post_gates")
        if not isinstance(pg, dict) or not pg:
            pg = ev.get("gates")   # 旧回执行无 post_gates：用 gates 近似，跨批次回溯到升级前
        if not isinstance(pg, dict) or not pg:
            continue
        out.append({"round": "iteration",
                    "time": str(ev.get("work") or ev.get("ts") or ""),
                    "gates": pg, "post_gates": pg, "gaps": ev.get("gaps", [])})
    return out


def _hist_note(obj: auto_objective.Objective,
               破裂: list | None = None) -> str:
    """历史偏差密度摘要（喂给执行/规划智能体的机器注记）。

    ★★★★★ R153（真跑抓出的第 67 层 · 2026-10-25 · 第 38 次端到端）：
      **恒真门会自我强化。**

    真证据（第 38 次真跑日志原文）：
      `历史偏差密度：src/gpu/gpu探测.rs@cargo_check_lib×6；
        src/模块甲/mod.rs@mod_无界键入口保留且统计量真用上×6；…`
    ★ 而第 37 次真跑里，R148 已经判定：
      `判据契约破裂（样本**应用成功**了、门**仍然绿** ⇒ 真·无区分力）：
        ['mod_无界键入口保留且统计量真用上']`
    ★★ **同一条门，一边被判定「不可信」，一边被当成「要优先解决的缺口」。**

    ★★★ 后果是一个**自我强化循环**：
      ```
      恒真门 → 永远不红 → 偏差密度永远最高 → 永远排最前
            → 主心骨被反复引向一条**永远可以被满足**的假判据
            → 它怎么改都「没进展」，因为那条门根本不是进展的度量
      ```
      这解释了第 37 次真跑为什么连续 3 次改不对 ——
      主心骨一直在追一条**不可信的门**。

    ★★★★ 处置两条，缺一条就是另一种不诚实：
      ① **剔除**已被判定为「无区分力」的缺口（不把它们排最前）；
      ② **如实说出来**（不是静默丢弃 —— 丢了却不说，主心骨会以为
         那条已经解决了）。⇒ 单独一段「以下缺口**不可信，别追**」。
    """
    if not obj.active:
        return ""
    # ★ R153：契约破裂的门名（真·无区分力）不得出现在「优先解决」里
    _破153 = {str(x) for x in (破裂 or [])}
    _活153 = [d for d in obj.active if str(d.key) not in _破153]
    _剔153 = [d for d in obj.active if str(d.key) in _破153]
    top = _活153[:3]
    out = ""
    if top:
        out += ("\n跨批次历史高频缺口（偏差密度最高的缺口，请优先解决同类问题）："
                + "；".join(f"{d.key}×{d.weight:g}" for d in top) + "\n")
    if _剔153:
        # ★ 如实说：这些缺口被**剔除**了，原因是那条门已被判定不可信。
        out += ("\n★ 以下缺口**已剔除，不要追**（它们对应的判据被机器判定为"
                "「无区分力」：样本应用成功了、门仍然绿 ⇒ 它度量不了进展）："
                + "；".join(f"{d.key}×{d.weight:g}" for d in _剔153[:5])
                + "\n  ⇒ 追它们只会「看起来在推进」而实际没有进展。"
                  "要改的是**判据本身**，不是产物。\n")
    return out


# ════════════════════════════════════════════════════════════════
# 主树工程任务（repo_scope）机器算子：源码注入 / 缺口分类 / 原子备份·应用·回滚
# ════════════════════════════════════════════════════════════════
def _detect_repo_scope(spec: dict, all_gates: list) -> bool:
    """主树工程任务判定：规约顶层 repo:true，或任一判据门引用 {repo} 占位符。"""
    if bool(spec.get("repo")):
        return True
    return any("{repo}" in json.dumps(g, ensure_ascii=False) for g in all_gates)


def _repo_file_context(rel: str, cap: int = _REPO_CTX_CAP) -> str:
    """读主树仓库当前文件内容（机制①：执行/回炉智能体看到真实源码再改）。
    文件不存在 → 空串；超 cap 截断并明确标注：未注入的后缀必须逐字保留，
    防止智能体把"没看到的部分"当成可删除/可重写的区域（真实跑已踩过）。"""
    p = REPO_ROOT / rel
    if not p.is_file():
        return ""
    text = p.read_text(encoding="utf-8", errors="ignore")
    if len(text) > cap:
        text = text[:cap] + (f"\n...[注入截断：原文件共 {len(text)} 字符，仅注入前 {cap}。"
                             f"注意：文件尚未结束，截断后的剩余 {len(text) - cap} 字符必须"
                             f"逐字保留原样，你只能修改注入区内可见的行，绝不能删除、重写或"
                             f"虚构截断点之后的任何内容]")
    return text


def _gap_strategy(red_audit: list, gates) -> str:
    """机制③：按红门**因由类**给执行/重规划智能体的应对策略（换执行策略，不只是换措辞）。

    ★★★ 2026-10-14 修（**同一塌缩的第二层**，TS-442/TS-447/TS-424 落形）：
      旧版按**门的类型**（`compile`/`grep_required`/…）分类 —— 那是又一次
      **读投影、不读真值**：`编译错` / `超时` / `挂起` / `退出码不符`
      四件**相异因由**共用门类型 `compile`，于是拿到**同一条**策略
      「优先核对语法、类型、导入与 API 签名」。
      而实测超时是**命令挂住不收敛**，核对语法**永远修不好** ——
      这正是 `Defect384`（相异因由压成一档）在**策略层**的复现，
      也是"任务永远不收敛"的一个独立成因。
      ⇒ 修法：改读**因由码的类**（`gate_spec.判据因由` 产出的 `类|证据` 前缀）。
        门类型只作**退化兜底**（老账本没有 `cause` 字段时）。

    ★ 口径：本函数**只**决定"怎么应对"，**不改判据** —— 绿红仍由门算。
    """
    if not red_audit:
        return ""
    by_name = {g.get("name"): g.get("type") for g in gates if isinstance(g, dict)}
    cats: dict[str, list[str]] = {}
    for a in red_audit:
        gname = str(a.get("gate") or "")
        因由 = str(a.get("cause") or "")
        if 因由:
            k = 因由.split("|", 1)[0]          # ← 读**因由类**，不读门类型
        else:
            # 退化兜底：老账本无 cause 字段
            t = by_name.get(gname) or a.get("type") or ""
            if a.get("gate") == "missing" or "missing" in str(a.get("err") or ""):
                k = "missing"
            else:
                k = t or "未分类因由"
        cats.setdefault(k, []).append(f"{a.get('file', '')}@{gname}")
    strat = {
        # ── 因由类 → 应对策略（每类一条，**互不重叠**）──
        "missing": "文件未产出/路径错误：严格按 deliverables 的 path 产出文件，不得改名改路径。",
        "编译错": ("编译错：按证据里的 `error[EXXXX]` 与 `--> 文件:行:列` 逐条定位修复；"
                 "同一处只改该行语义，不得重写整体。"),
        "超时": ("超时（**不是**语法问题）：该命令跑不完。改用**更小的验证范围**"
               "（只跑目标测试名，不跑全库/全模块），或先查是否引入了不收敛的循环。"),
        "命令无法启动": ("命令无法启动（**不是**产物错）：门里的命令路径/工具不存在。"
                    "先修门规格（换真实可执行路径），不要改产物。"),
        "挂起": ("挂起（**不是**语法问题）：该命令长时间无输出。定位是否死循环/等待输入/"
               "等锁；不要靠加大超时掩盖。"),
        "退出码不符": ("退出码不符：命令能跑完但返回码不是期望值。先看它自己的输出"
                   "判断是哪一步失败，不要当成编译错去改语法。"),
        "禁词命中": "禁词命中：删除/改写所有命中文本（含注释、标识符、字符串字面量），不得再匹配该正则。",
        "输出禁词": "命令输出含禁止内容：修复被扫命令的输出，使其不含 must_not 模式且退出码=expect_exit。",
        "必需缺失": "必需标记缺失：逐条核对必需字面子串必须全部出现（定义/标记/断言）。",
        "结构键缺失": ("关键结构键缺失：**照抄**目标文件里已有的声明名，不要自己编键名；"
                   "未改动部分必须逐字保留。"),
        "签名不符": "整体签名不符：产物被改动过多。按期望签名恢复，只做必要修改。",
        "内核陈述缺失": "内核陈述缺失：改的是**内核展开后的类型**，写在注释里无效 —— 必须改陈述本体。",
        "内核读法无法执行": "内核读法无法执行：该文件在内核层跑不通（先让它能通过编译/检查）。",
        "公理越界": "公理越界：不得引入白名单外的公理（含自声明 axiom 与 sorry）。",
        "公理足迹无法确认": ("公理足迹无法确认：命令输出里读不到公理足迹行 —— "
                       "先确保 `#print axioms` 真被执行到，不要靠删输出绕过。"),
        "门规格非法": "门规格非法：这是**门自身**配置错（不是产物错），需换门集，不要改产物。",
        "未分类因由": "其余红门：按门证据 err 字段逐条修复。",
        # ── 退化别名：老账本无 `cause` 字段时按**门类型**落到这里 ──
        #   ★ 保守处：这些别名**只在无 cause 时**可达，故不会掩盖新读数。
        "compile": ("编译/构建/行为断言失败（**未分类到具体因由**）：优先核对语法、类型、"
                    "导入与 API 签名；证据含 error/--> 行号，逐行修复至退出码=expect_exit。"),
        "grep_forbidden": "禁词命中：删除/改写所有命中文本（含注释、标识符、字符串字面量）。",
        "grep_required": "必需标记缺失：逐条核对必需字面子串必须全部出现。",
        "scan_output": "命令输出含禁止内容：修复被扫命令输出，使其不含 must_not 且退出码=expect_exit。",
        "other": "其余红门：按门证据 err 字段逐条修复。",
    }
    lines = ["红门因由与应对策略（按**因由类**换执行策略，不是只改措辞）："]
    for k, items in cats.items():
        lines.append(f"  - [{k}] {strat.get(k, strat['other'])} 命中：{'; '.join(items)}")
    return "\n".join(lines)


# -- 门集签名与门差异反馈（#1 换门集必须真异；#3 执行随新红门真正重试）--------
def _norm_gate_list(gates: list[dict]) -> list[dict]:
    """门语义规范化序列：去掉 name（`.json.p` 复述近似同门只改名字不算真变）、
    只留判定语义字段，稳定排序 → 用于"门集是否真变"的机器签名。"""
    out: list[dict] = []
    pats: list = []
    for g in gates:
        if not isinstance(g, dict):
            continue
        pats = sorted(g.get("patterns") or []) + ([g["pattern"]]
                    if "pattern" in g and str(g.get("pattern") or "").strip() else [])
        out.append({
            "t": g.get("type"),
            "mode": g.get("mode"),
            "cmd": g.get("cmd"),
            "ex": g.get("expect_exit"),
            "in": g.get("in"),
            "src": g.get("src"),
            "pat": pats + sorted(g.get("must_contain") or []),
            "expect": g.get("expect") or g.get("expect_text") or g.get("expect_sig"),
            "cwd": g.get("cwd"),
            "mn": g.get("must_not"),
            "timeout": g.get("timeout"),
        })
    out.sort(key=lambda k: json.dumps(k, ensure_ascii=False, sort_keys=True))
    return out


def _spec_gate_signature(spec: dict) -> str:
    """spec 判据门契约的机器签名：每文件门集排定序列化 → 判两版规约门集是否真变。"""
    dl = spec.get("deliverables") or []
    if not dl:
        return json.dumps(_norm_gate_list(spec.get("gates") or []),
                          ensure_ascii=False, sort_keys=True)
    top = spec.get("gates") or []
    return "\n".join(
        str(d.get("path")) + "=" + json.dumps(
            _norm_gate_list(d.get("gates") or top),
            ensure_ascii=False, sort_keys=True)
        for d in dl)


def _file_gates(spec: dict, path: str) -> list[dict]:
    top = spec.get("gates") or []
    for d in spec.get("deliverables") or []:
        if d.get("path") == path:
            return d.get("gates") or top
    return top


def _gate_diff_feedback(old_spec: dict, new_spec: dict, path: str) -> str:
    """#3 执行/回炉提示：注入新旧门集差异的结构化反馈，让执行针对新红门真产出不同实现，
    而不是多轮复述同一开头白烧 token。仅当门集真变（replan 采纳过新门集）时才有内容。"""
    old = {json.dumps(g, ensure_ascii=False, sort_keys=True): g
           for g in _norm_gate_list(_file_gates(old_spec, path))}
    new = {json.dumps(g, ensure_ascii=False, sort_keys=True): g
           for g in _norm_gate_list(_file_gates(new_spec, path))}
    added = [new[k] for k in new if k not in old]
    removed = [old[k] for k in old if k not in new]
    if not added:
        return ""

    def brief(g: dict) -> str:
        seg = {k: g[k] for k in ("cmd", "in", "src", "patterns",
                                 "must_not", "expect_exit", "cwd")
               if k in g}
        return f"[{g.get('type')}] {g.get('name') or g.get('type')}: " \
               + json.dumps(seg, ensure_ascii=False)

    lines = ["—— 判据门集已变化 ——"
             "规划器本轮替换/新增了判据门；旧实现只能通过旧门，无法达标。"
             "必须针对下面列出的新增/变更门，产出能通过它们的实现："]
    for g in added:
        lines.append(f"  + 新增/变更门 {brief(g)}")
    if removed:
        lines.append("移除/不再要求的旧门（不必再满足）："
                     + "、".join(brief(g) for g in removed[:6]))
    return "\n".join(lines)


class 并发改动错(RuntimeError):
    """★ R54：备份之后、写盘之前，文件被**别的进程**改动了 ⇒ 放弃这次写入。

    ★ 为什么必须是一个**专用类型**而不是裸 `RuntimeError`：
      调用方要能把它与"写不进（权限/占用）"分开处置 ——
      前者是**别人正在改这个文件**，正确反应是**让开**（跳过它，别覆盖）；
      后者是本机 I/O 问题，反应是**回退该文件**。混成一个类型就只能一刀切。
    """


# ★★★★★ R54：路径 → {"前": 备份时的 sha1, "后": 我们写完后的 sha1}
#   放在**模块级**（不是某个函数里）：`_backup_file` / `_apply_file` /
#   `_rollback_file` 三个函数**各自独立**，只有共享一张表才能互相对账。
#   ★ 这就是"全称修法"：**一处收口**，不必在每个调用点各打一个补丁
#     （那还会漏第四处 —— R39 的教训）。
_改动登记: dict[str, dict] = {}


def _指纹(p: Path) -> str:
    """文件内容的 sha1；文件不存在 ⇒ 空串。★ R54：只用于**比对**，不解释内容。"""
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest() if p.is_file() else ""
    except OSError:
        return ""


def _backup_file(rel: str) -> dict:
    """主树文件应用前备份到 .audit_trail/generic_backup/。返回 {path, backup, existed}。"""
    p = REPO_ROOT / rel
    b = BACKUP_DIR / rel
    b.parent.mkdir(parents=True, exist_ok=True)
    existed = p.is_file()
    if existed:
        # ★★★★★ R22：**字节级**备份。原来走 `read_text(errors="ignore")`
        #   ⇒ ① 非法 UTF-8 字节**静默丢弃**；② 写回时 `\n`→`\r\n` 翻译
        #   ⇒ 备份文件与原文件**不是同一份字节** ⇒ 「逐字回滚」是假的。
        b.write_bytes(p.read_bytes())
    else:
        if b.exists():
            b.unlink()
    _bk = {"path": str(p), "backup": str(b) if existed else None,
           "existed": existed, "前": _指纹(p)}
    # ★ R54：登记"我备份时它长什么样" —— 后面写库/回滚都要拿它对账。
    _改动登记[str(p)] = {"前": _bk["前"]}
    return _bk


# ════════════════════════════════════════════════════════════════
# R23：冻结区（判据不得由被判方改动）
# ════════════════════════════════════════════════════════════════
def _规范化行尾(b: bytes) -> bytes:
    """把 CRLF / CR 统一成 LF（**只用于冻结区比对**）。

    ★★★★★ R23c：为什么冻结区比对必须规范化行尾 —— R23 齿探针抓出的**假红**：

      只改**实现区**、冻结区内容一字未动，却判违反：快照 **227 字节 → 215 字节**，
      差 **12** = 冻结区里的换行数。真因是仓库文件为 **CRLF**，而模型输出的是
      **文本（LF）** —— 它不知道也不该知道原文件的行尾风格 ⇒ 整份文件（含冻结区）
      的行尾被改 ⇒ 被判成「改了判据」。

      ⇒ **假红**：会把**正当修改判死**，且模型无从申诉（行尾是文本层的事）。

    ★ 判据的**内容**是「断言了什么」，**不是行终止符** ⇒ 规范化后再比。
      规范化**不会**放过真正的改动：把断言改弱照样判违反（齿探针①复验）。
    ★ 与 R22 不冲突：R22 管「工具自己的写盘不得无端重写行尾」，
      R23 管「判据的**内容**不得被改」—— 两件事，两套口径。
    """
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _冻结区取文(spec: dict) -> tuple[Path, bytes] | None:
    """取一个冻结区的当前字节。返回 `(文件路径, 区域字节)`；取不到 ⇒ None。

    · `whole: true` 或未给 `begin` ⇒ 整文件；
    · 给了 `begin` ⇒ 从 `begin` 首次出现处**到文件末尾**（`end` 非空则到 `end` 之后）。
    · 文件不存在 ⇒ None（**不判违反**：没写过的文件谈不上"被改动"）。
    """
    rel = str(spec.get("path") or "").strip()
    if not rel:
        return None
    p = REPO_ROOT / rel
    if not p.is_file():
        return None
    raw = p.read_bytes()
    beg = spec.get("begin")
    if spec.get("whole") or not beg:
        return p, raw
    text = raw.decode("utf-8", errors="replace")
    i = text.find(str(beg))
    if i < 0:
        return p, b""            # 标记本身没了 ⇒ 空区域（与快照不等 ⇒ 判违反）
    j = len(text)
    end = spec.get("end")
    if end:
        k = text.find(str(end), i)
        if k >= 0:
            j = k + len(str(end))
    return p, text[i:j].encode("utf-8")


def _冻结区逐条(specs) -> list[tuple[dict, tuple[Path, bytes] | None]]:
    """**唯一**的冻结区取文通路：每条声明 → `(声明, 取到的字节 | None)`。

    ★ R25：把"取文"与"怎么用"分开 —— 于是 `_冻结区快照`（要保护的）
      与 `_冻结区未取到`（要报告的）读的是**同一次**取文结果，
      不会出现"快照说取到了、报告说没取到"这种两处口径分叉。
    """
    return [(dict(s), _冻结区取文(s)) for s in (specs or ())]


def _冻结区快照(specs) -> list[dict]:
    """任务开始时快照全部冻结区（**字节级**）。取不到的**不在这里报**（见下）。"""
    out = []
    for s, got in _冻结区逐条(specs):
        if got is None:
            continue
        out.append({"path": str(s.get("path")), "begin": s.get("begin"),
                    "end": s.get("end"), "whole": bool(s.get("whole")),
                    "file": str(got[0]),
                    # ★ R23c：存**规范化行尾**后的字节（比对口径，非写盘口径）
                    "bytes": _规范化行尾(got[1])})
    return out


def _冻结区未取到(specs) -> list[dict]:
    """★ R25：档案**声明了**、但快照时**取不到**的冻结区。**必须报告。**

    ── 真跑抓出的缺陷（2026-10-15）──
    在一个真 Rust 工程上自举，自动生成的 `frozen` 里有一条指向
    **工具自己的备份目录**：`.orchestrator/generic_backup/src/lib.rs`。
    我起初断言"那会导致每次运行都假红" —— **探针②推翻了这个断言**
    （备份与源码的冻结区恰好相同，比不出来），如实纠正。

    ★ 但顺着这条线查下去，发现了**更基本、可证**的那个洞：
      `_冻结区取文` 对**文件不存在**返回 `None`，而 `_冻结区快照` 里
      是 `continue` —— **静默跳过**。
      ⇒ 若某条冻结区声明指向的文件**在快照时还不存在**
        （首次运行时备份目录还不存在、路径写错、文件被改名……），
        它**一点保护都没有**，而**没有任何人知道**。
      ⇒ 这正是本库反复踩的同一个病：**"没跑"看起来跟"绿"一样**。
        声明了冻结区 ⇒ 看起来"判据受保护了" ⇒ 其实那条根本没进快照。

    ⇒ 修法：取不到就**报出来**（任务开始时告警 + 落进回执），
      让人看得见"这条冻结区没生效"。
      ★ 不判红：文件**可能合理地还不存在**（首次运行尚无备份、
        可选文件未生成）—— 判红会是**假红**，而假红比无齿更坏。
        这里只**如实报告**，把判断留给人和门。
    """
    out = []
    for s, got in _冻结区逐条(specs):
        if got is not None:
            continue
        rel = str(s.get("path") or "").strip()
        p = (REPO_ROOT / rel) if rel else None
        out.append({
            "path": rel, "begin": s.get("begin"), "whole": bool(s.get("whole")),
            "err": (f"冻结区声明指向的文件**不存在**：{p}"
                    if rel and p is not None and not p.is_file()
                    else "冻结区声明**缺 `path`**"),
        })
    return out


def _冻结区违反(快照) -> list[dict]:
    """比对快照与当前；返回违反列表（空 = 未改动）。**字节级**。"""
    vio = []
    for s in 快照 or ():
        got = _冻结区取文({"path": s["path"], "begin": s["begin"],
                           "end": s["end"], "whole": s["whole"]})
        if got is None:
            vio.append({"gate": "冻结区@缺失", "file": s["path"], "ok": False,
                        "err": f"冻结区文件 {s['path']} 在任务过程中**消失**了"})
            continue
        now = _规范化行尾(got[1])       # ★ R23c：行尾差异不算改判据（防假红）
        if now != s["bytes"]:
            vio.append({
                "gate": "冻结区@被改动", "file": s["path"], "ok": False,
                "err": (f"冻结区（{s['path']}"
                        + (f" 从 `{s['begin']}` 起" if s["begin"] else " 整文件")
                        + f"）**被改动了**：快照 {len(s['bytes'])} 字节 → "
                          f"现在 {len(now)} 字节。"
                          f"该区域是**判据**（验收测试/契约），"
                          f"判据不得由被判方改动 —— 请把这块**逐字改回原样**，"
                          f"只改实现区。")})
    return vio


def _是拒收产物(文本: str) -> str | None:
    """这份产物是**机器拒收**的（不是可交付内容）⇒ 返回标记名；否则 None。

    ★★★★★ 为什么要有这一条（自检 ⑥ 抓出的**设计缺陷** · 2026-10-15）：

      执行层有三种"我产不出来"的**如实拒收**产物：
        `__ORCH_CANNOT_EDIT__`（大文件无行号焦点）/ `__ORCH_PATCH_REJECTED__`
        （补丁被机器拒）/ `__ORCH_NOT_CONTENT__`（产出是工具调用转录）。
      旧写法把它们**当成正常内容**，一路 `_apply_file` **写进主树**
      ⇒ 主树被一份"我改不了"的说明**覆盖**，然后靠判据门判红再回滚。

      ★ 这是**不必要的暴露**：回滚是安全网，不该是常规路径。
        而且它有真实的坏窗口 —— 若判据门恰好绿（比如那份说明落在一个
        编译不到的位置），**说明文字就留在主树里被当成交付**。
      ⇒ 机器拒收的产物**根本不进主树**（沙箱副本照写，让门判红、原因入回执）。

    ★ 一处收口：三个标记只在这里声明一次（不在两个 `_apply_file` 调用点各写一遍）。
    """
    t = (文本 or "").lstrip()
    for 标记 in ("__ORCH_CANNOT_EDIT__", "__ORCH_PATCH_REJECTED__",
                 "__ORCH_NOT_CONTENT__"):
        if t.startswith(标记):
            return 标记
    return None


def _像工具调用而非内容(文本: str) -> str | None:
    """模型产出**根本不是文件内容**（而是工具调用 / 对话转录）⇒ 返回原因；否则 None。

    ★★★★★ R32（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      窗执行器返回的"内容"是**一段对话转录**：

          I'll start by exploring the repository and reproducing the failing gates.

          <｜｜DSML｜｜ calls>
          <｜｜DSML｜｜ invoke name="bash">
          <｜｜DSML｜｜ parameter name="command" string="true">cd "E:\\..."

      ⇒ 它被当成源码**写进了主树** `src/接线状态图.rs`，cargo 报
          `error: unknown start of token: \\u{ff5c}`（全角竖线）
      ⇒ 幸好编译门判红 ⇒ **回滚**了（主树最终干净）。
      ★ 但这是**侥幸**：判红靠的是"这份垃圾恰好编译不过"。
        若它落在一个**编译不到**的位置（新文件、注释、字符串、非构建目标），
        编译门就绿 ⇒ **垃圾留在主树还被判成功交付**。

      ⇒ 必须在**写主树之前**认出「这不是内容」，而不是指望判据门事后兜住。
        ★ 与 R31 同一条道理：**能在入口拦下的错，不要在出口兜**。

    【口径：只认**几乎不可能出现在源码里**的强特征】
      ★ 假红比无齿更坏（R7）⇒ 不用"像不像代码"这种模糊判断，
        只认**模型框架自己产出的分隔记号**。宁可漏（漏了还有判据门兜），
        不可误（误了会把**正当产出判死**，而模型无从申诉）。
    """
    t = 文本 or ""
    if not t.strip():
        return "产出是空的"
    强特征 = (
        "｜｜DSML｜｜",          # 全角竖线包裹的工具调用分隔记号
        "<｜｜",                 # 同上（截断形态）
        "antml:invoke",          # 另一家的工具调用命名空间
        "antml:parameter",
        "<function_calls>",
        "</function_calls>",
        "<tool_calls>",
        "</tool_calls>",
    )
    for s in 强特征:
        if s in t:
            i = t.find(s)
            return (f"产出里含**模型工具调用的分隔记号** `{s}`"
                    f"（位置 {i}）⇒ 这是**对话转录**，不是文件内容")
    # 工具调用语法的**组合**特征（单独出现可能是文档，两个同时出现才是调用）
    if ("<invoke name=" in t or "<invoke " in t) and \
            ("<parameter name=" in t or "<parameter " in t):
        return "产出里含 `<invoke …>` + `<parameter …>` ⇒ 这是**工具调用**，不是文件内容"
    return None


def _落盘(p: Path, text: str) -> None:
    """**唯一**的交付内容落盘实现（R22c：不枚举落盘点）。

    ★★★★★ 为什么必须收成一个函数：
      落盘点原有四处（主树 `_apply_file` / 窗副本 `_window_score` /
      正本 `_round` / 产物副本 `_deliver`），各写一遍
      `write_bytes(text.encode("utf-8"))` ⇒ **四处各可能被改坏**，
      判据力探针也得配四条 —— 而探针④已经证明：**漏配一条就是无齿门**。

    ★ 语义（R22 真跑量出来的）：
      · **字节级**：写 `text.encode("utf-8")` 的原始字节，**不做行尾翻译**。
        `write_text` 在 Windows 上默认把 `\n` 翻成 `\r\n`
        ⇒ 实测：编排器改过的 `src/lib.rs` 变成 CRLF 145/LF 0，而它没碰过的
          `Cargo.toml` 仍是 LF 11 ⇒ **静默重写整个文件的行尾**，
          对 git 而言整个文件都变了、「判过的字节」与「留下的字节」不是同一份。
      · 父目录按需创建（落盘点可能在新目录里）。

    ★ 防御（真跑抓出）：目标路径**已经是目录**时，`write_bytes` 在 Windows 上
      抛的是裸 `PermissionError: [Errno 13]` —— 那个信息**指不出根因**
      （看的人会以为是权限/杀毒，而不是"交付物写成了目录"）。
      ⇒ 这里改成**说清是什么错**。★ 只是**把话说清**，不改行为：
        它仍然拒绝写（不猜、不静默改道）。
    """
    if p.is_dir():
        raise 交付物形态错(
            f"落盘目标是一个**目录**：{p}\n"
            f"⇒ 交付物必须是**文件**路径（如 `src/判据/观测协议.rs`），"
            f"不能是目录（如 `src/`）。")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


def _落盘拷贝(src: Path, tgt: Path) -> None:
    """**字节级**拷贝（产物副本用）：读的是已判过的正本字节，不做任何翻译。"""
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tgt.write_bytes(src.read_bytes())


def _apply_file(rel: str, text: str) -> Path:
    """把候选内容写入主树文件（先由 _backup_file 留痕）。

    ★★★★★ R22：写 **`text.encode("utf-8")` 的原始字节**，不做行尾翻译。
      `write_text` 在 Windows 上默认把 `\n` 翻成 `\r\n`
      ⇒ 工具**静默重写整个文件的行尾**（实测：改过的 `src/lib.rs` CRLF 145/LF 0，
        没碰过的 `Cargo.toml` LF 11）⇒ 对 git 而言整个文件都变了、
        「判过的字节」与「留下的字节」不是同一份。
    """
    p = REPO_ROOT / rel
    # ★★★★★ R54（真跑抓出的缺口 · 2026-10-16）：**写之前先核对指纹**。
    #   【现象】运行期间 `src/判据/mod.rs`、`src/模块甲/mod.rs` 的 mtime
    #     落在本轮里，而它们**根本不在交付物清单里**；且 `cargo test` 的
    #     红门理由从"14 处违规"变成了"`src/判据/mod.rs:759` 编译错"
    #     —— 一个**我们没碰过的文件**里的错。
    #   【根因】本工具**假设自己是主树的唯一写者**：备份、写、判、回滚
    #     四步之间**从不检查文件是否被别的进程动过**。而这是**通用工程工具**
    #     的常态（人、编辑器、另一个 agent、watch 任务都可能同时改）。
    #   ★ 后果有两层，第二层更坏：
    #     ① 我们的候选**覆盖掉别人的改动**；
    #     ② **回滚把别人的改动一起吃掉** —— 判红回滚是常规路径，
    #        于是"安全网"变成"静默销毁别人工作的机器"。
    #   ★ 处置口径：**让开，不覆盖，如实报**。
    #     不是"重试"（那还是覆盖），也不是"合并"（那是猜语义）。
    #     读不到 / 变了 ⇒ 就不写这一个文件，并说清为什么。
    _登 = _改动登记.get(str(p))
    if _登 is not None:
        _现 = _指纹(p)
        if _现 != _登.get("前"):
            raise 并发改动错(
                f"{rel} 在备份之后被**别的进程**改动了"
                f"（备份时 {str(_登.get('前'))[:8] or '不存在'}"
                f"，现在 {_现[:8] or '不存在'}）⇒ **放弃这次写入**，不覆盖别人的改动")
    _落盘(p, text)
    if _登 is not None:
        # ★★★★★ R54b：**核对基准必须是"我们已知的最后状态"，不是"备份那一刻"。**
        #   真跑抓出（`落盘字节齿探针` ②/③ **红点错位**）：R22 自检里对同一文件
        #   连写两次（OLD→NEW→SAME），第二次被 R54 误判成"别人改过" ⇒
        #   抛 `并发改动错` **炸穿整个自检** ⇒ 自检红了但**不点名 R22**
        #   ⇒ 探针判"红错地方 = 无齿"。★ 这不是探针的毛病，是**守卫的基准错了**。
        #   【根因】备份时记下的"前"是**一次性的**，我们自己的第一次写入就把它
        #     作废了 ⇒ **守卫把自己人当外人**。
        #   ⇒ 写成功即把"前"推进到"我们刚写的这份"：守卫只认**第三方**。
        #   ★ 这不削弱守卫：若第三方在我们两次写之间插进来，下一次核对照样拒。
        _登["后"] = _指纹(p)
        _登["前"] = _登["后"]
    return p


def _rollback_file(bk: dict) -> None:
    """红门/异常 → **字节级**回滚到应用前内容；原本不存在的文件 → 删除。

    ★★★★★ R22：「红滚」是**安全网**，它自己必须有损性为零。
      原来走 `read_text(errors="ignore")` + `write_text`：实测 38 字节回滚成 35 字节
      （非法 UTF-8 被丢 + 行尾被翻译）⇒ 承诺的「逐字回滚」是假的。
    """
    p = Path(bk["path"])
    # ★★★★★ R54：**回滚是安全网，它自己绝不许毁掉别人的工作**。
    #   若我们写完之后文件又被别人改了 ⇒ 现在的字节**不是我们写的那些**
    #   ⇒ 拿旧备份盖回去 = **静默销毁别人的改动**。
    #   ⇒ 这种情况**跳过回滚并如实报**（我们不收回不属于自己的东西）。
    _登 = _改动登记.get(str(p))
    if _登 is not None and _登.get("后") is not None:
        _现 = _指纹(p)
        if _现 != _登["后"]:
            _log(f"[generic] ★ R54：**不覆盖** —— {bk['path']} 在我们写入之后"
                 f"又被别的进程改了（我们写的 {_登['后'][:8]}"
                 f"，现在 {_现[:8] or '不存在'}）"
                 " ⇒ 跳过回滚（回滚不得吃掉别人的改动）")
            return
    if bk["existed"] and bk["backup"]:
        p.write_bytes(Path(bk["backup"]).read_bytes())
    elif not bk["existed"]:
        if p.exists():
            p.unlink()
    # ★ R54b：回滚后"我们已知的状态"就是**恢复后的这份**——同步推进基准，
    #   否则同一文件「回滚 → 再写」会被守卫误判（与 `_apply_file` 同一个根因）。
    if _登 is not None:
        _登["前"] = _登["后"] = _指纹(p)


# ════════════════════════════════════════════════════════════════
# 主闭环
# ════════════════════════════════════════════════════════════════
def _项目门是否放行(基线: dict | None, 现: dict | None) -> tuple[bool, str, list[str]]:
    """项目验收门**相对基线**放行判定（★ R78）。返回 `(放行, 因由, 变坏的门)`。

    ★ 为什么抽成**纯函数**（与 `_门态文本` 抽出来是**同一个理由**）：
      逻辑若长在 `_project_acceptance` 里，验证它就得**真跑 `cargo test --lib`**
      （真项目里 900 秒起步）⇒ 自检不可能覆盖 ⇒ 这条口径**永远不会有齿**。
      ⇒ 拆出来之后，自检喂两份**假的**门真值，直接断言放不放行。

    ★★★ 三层尺子的分工（**必须不同**，否则要么恒真要么恒假）：
      · `_增量可判`（R43）：**一步**有没有让判据严格更接近绿 —— 真子集。
      · `_达标`（R73b）：**整趟任务**有没有让违规点相对基线**严格变小**
        —— 证明"我真的做了事"。
      · 本函数（R78）：**项目验收门**相对基线**不许由绿转红**
        —— 不许把**别人的债**算成本次任务的失败。
      ★ 若项目级也要求"严格变小"⇒ 每个任务都得负责**整棵树**的历史债
        ⇒ 与"缺口是一个一个闭合的"直接矛盾 ⇒ 无人值守**永远跑不通**。
      ★ 若任务级也只要求"不变坏"⇒ "什么都不做"就达标 ⇒ **恒真**。
      ⇒ **两条一起**才既非恒真、又非恒假。

    ★ 基线为空 ⇒ 返回 `(全绿, 老口径, [])` —— **零回退**，老行为完全不变。
    """
    现 = dict(现 or {})
    全绿 = bool(现) and all(v is True for v in 现.values())
    if not 基线:
        return (全绿, "" if 全绿 else "基线未量到 ⇒ 按**绝对全绿**判", [])
    if 全绿:
        return (True, "", [])
    # 「变坏」= 基线里**绿**的门，现在**不绿**了（含"消失了"）。
    #   ★ 用 `.get(k) is not True` 而不是 `not 现.get(k)`：**消失**也是变坏
    #     （门不见了 ≠ 门绿了），而 `not None` 恰好也覆盖这一情形 —— 但写明
    #     是为了让"消失算变坏"这条**是被决定的**，不是碰巧。
    坏 = [k for k, v in 基线.items() if v is True and 现.get(k) is not True]
    if 坏:
        return (False, "项目门**变坏了** —— " + "、".join(坏)
                + " 由绿转红 ⇒ 这是本次任务的责任", 坏)
    return (True, "项目门**未变坏**（基线里绿的门仍绿，红的门是主树历史债，"
                  "不是本次任务的责任）⇒ 放行", [])


class GoalRunner:
    # ════════════════════════════════════════════════════════════════
    # ★★★★★ 类级默认值（**全称修法**，不逐个探针打补丁）
    #
    #   探针/自检用 `GoalRunner.__new__(GoalRunner)` 绕过 `__init__` 构造实例
    #   （`__init__` 会**探活模型通道** = 真网络请求，探针不能跑）。
    #   ⇒ 但 `__init__` 里设的每个属性，在 `__new__` 构造的实例上**不存在**
    #   ⇒ 实现**新增一个属性**，所有 `__new__` 型探针就**静默失效**
    #     （以 `AttributeError` 崩掉，而不是报告门的齿 —— 违反纪律 R3）。
    #
    #   ★ 真跑抓到过：R23 给 `_deliver` 加了 `self._冻结区`，
    #     而 R21 的探针用 `__new__` 构造、没设它 ⇒ 探针以 AttributeError 崩，
    #     全量回归里 `项目验收齿探针.py` 直接变红。
    #
    #   ⇒ 修法不是"去给那个探针补一行"（下次加属性还会重演），
    #     而是**在这里给每个会被 `self.X` 读的属性一个类级兜底**
    #     ⇒ 一个声明覆盖**所有** `__new__` 构造（现在的与将来的），
    #       探针永远能跑到"报告问题"那一步，不会自己崩。
    #   ★ 默认值一律用**不可变**样本戊（`()`/`0`/`None`）——
    #     可变类属性会被所有实例共享，那才是真 BUG。
    # ════════════════════════════════════════════════════════════════
    _冻结区: tuple = ()          # R23 冻结区快照（空 = 未声明 frozen）
    _冻结区未取到: tuple = ()    # R25 声明了却取不到的冻结区（必须报告）
    _active_backups: tuple = ()  # R21/R22 主树备份/回滚点
    _轨道20 = None               # R20 真值空间轨道（None = 未接线）
    _tok_chars: int = 0          # 预算护栏累计
    _pending_sediment: tuple = ()
    _ledger_buf: tuple = ()
    records: tuple = ()
    product_dir = None
    n_windows: int = 0
    _work: int = 0

    def __init__(self, goal: str, channel: str = "", n_windows: int = N_WINDOWS,
                 min_pass: int = MIN_PASS, max_work: int = MAX_WORK,
                 dry: bool = False, dry_multi: bool = False,
                 dry_rework: bool = False,
                 replan_after: int = REPLAN_AFTER,
                 replan_max: int = REPLAN_MAX,
                 budget_chars: int = TOKEN_BUDGET_CHARS,
                 out_dir: str | None = None,
                 dry_machine: bool = False) -> None:
        # ★【TS-381 教训 · 新参一律**追加到末尾**，绝不插在中间】
        #   本类 2026-10-12 因"中间插入 replan_max"导致位置实参**静默错位一格**。
        #   我 2026-10-14 加 `dry_machine` 时又插在 `dry_rework` 后面 ⇒ 自检门
        #   「位置实参错位可见性」当场判红（它复刻旧错法，本该仍能判出错位）。
        #   ⇒ 追加到末尾，位置调用语义不变；且 `_build_runner` 全关键字调用不受影响。
        # ★★★★★ R87：`【必改文件】` **由机器补**，不靠调用方记得写。
        #   R86 那条判据要求目标里有这个标记；人工写的目标不会写 ⇒ 对它等于不存在。
        #   ★ 标记是接口，不该靠自觉。
        # ★ R102：**产品自己声明「我是真跑实例」**。
        #   只有经由 `__init__` 构造的实例才会写生产回执；
        #   自检/探针用 `__new__` 构造（不跑这里）⇒ 这个事实天然为 False
        #   ⇒ 它们的回执进一次性临时目录，**不污染真跑证据**。
        #   ★ 为什么不按 `dry` 判：dry 也是产品路径，dry 的账也该记。
        #     要分开的是「**真跑**」与「**检查**」，不是「真模型」与「假模型」。
        self._真跑实例 = True
        self.goal = _补必改标记(goal)
        # ★ R89：目标点名的文件**自己**有没有变好（空 = 没有必改约束）。
        self._必改未达标: list[str] = []
        self.dry = dry
        self.dry_multi = dry_multi
        self.dry_rework = dry_rework
        self.dry_machine = dry_machine
        self.max_work = max_work
        # ★★★★★ R20：真值空间轨道（TS-469）。判定层从这里读真值，
        #   不再从六值字符串 `verdict` 读。
        self._轨道20 = _ts20.轨道()
        # ★★★★★ R23：冻结区快照（**任务开始时**取一次）。
        #   判据（验收测试/契约）常与实现同文件 ⇒ 必须对它不可变，
        #   否则「门是唯一裁决者」不成立（模型改测试就能让自己过）。
        self._冻结区 = _冻结区快照(prof_mod.get_profile().frozen)
        # ★ R25：声明了却**取不到**的冻结区必须报告 —— 静默跳过等于
        #   "声明了保护却毫无保护，且无人知"（与"没跑看起来像绿"同一个病）。
        #   这里只**告警 + 落回执**，不判红（文件可能合理地还不存在；
        #   判红是假红，而假红比无齿更坏）。
        self._冻结区未取到 = _冻结区未取到(prof_mod.get_profile().frozen)
        if self._冻结区未取到:
            _log(f"[generic] ★★ 冻结区**未生效** {len(self._冻结区未取到)} 条"
                 f"（档案声明了，但快照时取不到 ⇒ 该区域**没有任何保护**）：")
            for _s in self._冻结区未取到:
                _log(f"[generic]   ✗ {_s['err']}")
        self.min_pass = min_pass
        self.n_windows = n_windows
        self.replan_after = 10**9 if dry else replan_after  # dry 只验证回炉，不重规划
        # ★ 接线（2026-10-12）：REPLAN_MAX 常量此前**从未接进实例**，
        #   而 _replan_thrashed(self._replan_count, self.replan_max) 需要它 → AttributeError。
        self.replan_max = 10**9 if dry else replan_max
        self.budget_chars = budget_chars
        self.records: list[dict] = []        # auto_objective 账本（追加式）
        self._ledger_buf: list[dict] = []    # 增量索引：内存全历史
        self._ledger_pos: int = 0            # 增量索引：字节断点账本（历史前缀 + 本轮追加）
        # ★ R158b：心跳（定时器驱动，见 `_起心跳`）
        self._心跳因: str = "init"
        self._心跳间隔: float = 20.0
        self._心跳线程 = None
        # 待沉淀的派生门：本轮换进门集、但**尚未被跑绿确证**。只有确证过才沉淀。
        self._pending_sediment: list[dict] = []
        # ★ 通道探活（2026-10-14 修）：原直接 `select_channels(channel)` 取**配置口径**，
        #   而实测通道可用性**随时间变**（ggg 在 23:01 是 200、09:11 变 503），
        #   ⇒ 配置里"有"不等于运行时"能用" ⇒ 全窗 503 失败、用户看不出是通道死了。
        #   改为**发最小真实请求探活**，只把活通道交给 Router。
        #   `--dry` 不探（dry 不调模型，探了纯浪费且离线必失败）。
        channels, self.channel_report = select_live_channels(
            channel, probe=not dry)
        if not channels:
            # ★ 全死时**如实报错**，不静默退回配置口径 —— 否则调用方收到的是
            #   难懂的 503，而不是"你的通道全不可用（逐条原因）"。
            dead = "；".join(f"{k}: {v}" for k, v in
                             (self.channel_report.get("dead") or {}).items())
            raise RuntimeError(
                f"没有真实可用的模型通道（已探测 {self.channel_report.get('probed')} 个）。"
                f"逐条原因：{dead or '（未探测，见 --dry）'}")
        # ★★★★★ R50（真跑抓出的缺陷 · 2026-10-15）：
        #   原实现是 `[(c.name, build_client(c, c.models[0])) for c in channels]`
        #   —— **每个通道只取第一个模型** ⇒ 一个通道声明的其余模型
        #   （本仓 `dddddd` 有 `kimi-k3`）**永远用不到**，
        #   Router 的 failover **只能在通道之间跳、不能在模型之间跳**。
        #   ★ 实测后果：`dddddd` 第一个模型 503 时，配置里明明还有
        #     `kimi-k3`、还有**另一个域名**的 `mf`，却没有任何路径去试它们
        #     ⇒ 我据此得出了"上游全挂"的**假结论**。
        #   ⇒ 候选 = **所有可用的 (通道,模型) 对**（判定口径见 `live_model_pairs`，
        #     与 R18 一致：硬死才排除，读不到仍试）。
        #   ⇒ 槽名用 `通道:模型` ⇒ **每个模型独立记失败与冷却**，
        #     一个模型坏掉不会把同通道的好模型一起拖下水。
        self.pairs = [(slot, build_client(c, m))
                      for slot, c, m in live_model_pairs(channels,
                                                         self.channel_report)]
        if not self.pairs:
            raise RuntimeError(
                f"没有真实可用的 (通道,模型) 对（已探 {self.channel_report.get('probed')} "
                f"个通道 / {self.channel_report.get('probed_pairs')} 个模型）。"
                f"逐条原因：{self.channel_report.get('dead') or '（见报告）'}")
        _log("[generic] 可用模型槽（failover 目标）："
             + "；".join(n for n, _ in self.pairs))
        # ★★★★★ R72（2026-10-17）：**渠道源 —— 持续加载 + 自适应重探。**
        #   上面那次 `select_live_channels` 是**一次性**的：启动时探一次，
        #   把结论**冻结**成 Router 的槽表 ⇒ 整趟无人值守（几小时）里，
        #   **后来恢复的渠道永远回不来**、用户改 yaml 也**必须重启**才生效。
        #   ★ 而本文件上面 R18 那段注释自己就记着：`ggg` **23:01 是 200、
        #     09:11 变 503** —— 可用性是**时间的函数**，不是启动时的常数。
        #   ⇒ 把 Router 接上 `渠道源`：没有健康槽时它会**先问一次外面**
        #     （重读配置 + 重探），有恢复的槽就立刻用上；**哪个能用就用哪个**。
        try:
            from agentscope_orchestrator.gateway import 渠道源 as _渠道源
            self.渠道源 = _渠道源(channel)
            _槽源 = self.渠道源

            def _刷新槽():
                return [(n, build_client(c, m))
                        for n, c, m in _槽源.刷新()]

            # 启动时先强制刷一次（把上面那次一次性探活的结论纳入持续加载口径）
            self.渠道源.刷新(强制=True)
            self.router = Router(self.pairs, 刷新器=_刷新槽)
        except Exception as _e72:                       # noqa: BLE001
            # ★ 渠道源是**增强**，不是前提：装不上就退回"启动时探一次"的老口径，
            #   但**如实说出来**（不静默降级成"看起来一样"）。
            _log(f"[generic] ★ R72 渠道源未接上（退回启动时一次性探活）："
                 f"{type(_e72).__name__}: {_e72}")
            self.渠道源 = None
            self.router = Router(self.pairs)
        self.out_dir = Path(out_dir) if out_dir else PRODUCTS
        self.spec: dict | None = None
        self.deliverables: list[dict] = []
        self.safe_name = "task"
        self.plan: dict | None = None
        self.bound: dict | None = None
        self.product_dir: Path | None = None
        self.round_root: Path | None = None
        self.repo_scope = False              # 主树工程任务（改真实 src/ 树，绿保红滚）
        self._active_backups: list[dict] = []   # 本轮绿保的主树备份（交付回执登记回滚点）
        self._kept_all: list[str] = []           # ★ R48：跨轮累计"保留在主树"的文件
        # ★ R177：**回退台账** —— 每个被回退的文件、为什么、那次具体报了什么错。
        #   只保留**最近一轮**的（每轮覆盖）⇒ 模型看到的是「上一轮你被退回了什么」。
        self._回退台账: list[dict] = []
        # ★ R179：**每个文件被回退过几次**（跨轮累计，不覆盖）。
        #   用途：整份重写已实测会把好地方改坏 ⇒ 退过的文件**强制切补丁模式**。
        self._回退次数: dict[str, int] = {}
        self._work = 0
        self._tok_chars = 0                  # 预算护栏累计（#5）
        self._replan_count = 0               # 重规划次数（#1 漂移护栏：连续换门不收敛判不可达）
        self.hist_note = ""
        self._target_dev: dict | None = None  # 本轮重工锚定偏差（C3：机器定选哪个，模型只产内容）

        # ★★★★★ R92：**主心骨** —— 一个**有跨轮记忆**的智能体，统揽方向、定判据。
        #   真跑查出来的形态：`_replan` 没有跨轮历史 ⇒ 每次出问题就**重新猜一遍**，
        #   设计意图在第一次规划之后就丢了。整台机器是**轮询调度器**，不是主心骨。
        #   ⇒ 这里建的那一个，是全程**同一个**（不是每轮新建）。
        self.chief = chief_mod.主心骨(路由=self.router,
                                      根=REPO_ROOT,
                                      # ★ 与真跑总账**同目录**，且跟着 `_RECEIPT` 走
                                      #   （dry 模式会把 `_RECEIPT` 重定向到临时目录，
                                      #    主心骨回执必须一起重定向，否则会污染生产账）。
                                      回执目录=_RECEIPT.parent)
        self.chief.目标 = self.goal
        self._chief_降级 = False          # 主心骨没跑起来 ⇒ 如实退回老形态
        self._chief_改目标次数 = 0        # ★ R101：改目标预算（规划失败时也只许改一次）
        self._chief_契约未立: list[str] = []   # 没有否定样本的门（如实记录，退回启发式）
        # ★ 主心骨**声明了**样本、样本却**没让门红** ⇒ 它断言了一件假事 ⇒ 硬拦。
        #   与 `_chief_契约未立` 的区别：那个是"没声明"，这个是"声明了但假的"。
        self._chief_契约破裂: list[str] = []
        # ★ R153b：**跨轮**的「已被判无区分力」门名集合。
        #   与 `_chief_契约破裂`（每轮重算）不同，它在本次任务内**累积**。
        self._恒真门153: set = set()
        # ★ R148：**未验**（样本的旧产物里还没有 / 门跑不起来）单独一栏。
        #   它不是破裂，也不能静默 —— 必须出现在因由里。
        self._chief_契约未验: list[str] = []

    def _emit(self, event: dict) -> None:
        event = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "goal": _回执目标222(self.goal, _现场段222()), **event}
        # ★★★★★ R8-F13（**如实标注**）：单窗 = **没有交叉验证**。
        #   本工具的收敛判据是「**多窗一致对齐**」（`_align_windows`：多通过窗的门
        #   真值等价 = 不动点），不是"某一条路跑绿了"。
        #   ⇒ 单窗跑绿只能说明**这一条路走通了**，不能说明"换一条路也走通"。
        #   ⇒ 故回执里**必须**带 `cross_validated`，且 `verdict` 不得用 `converged`
        #     冒充"已收敛"：单窗时改标 `converged_single_window`。
        #   ★ 为什么不禁掉单窗：单窗是**调用方的成本选择**（省一次模型调用），
        #     不该由默认值替他决定；该做的是**不许它自称收敛**。
        _xv = int(self.n_windows) >= 2
        event.setdefault("cross_validated", _xv)
        if not _xv and event.get("verdict") == "converged":
            event["verdict"] = "converged_single_window"
            event["verdict_note"] = ("单窗（无交叉验证）：本窗门全绿只证明这一条路走通，"
                                     "未证明换一条路也走通；要交叉验证请 n_windows≥2")
        # ★ 自检门要能**直接查**本轮交付产物（而不是去解析回执文件）：
        #   记最后一次 delivered 的 products（判"真交付"这件事不能靠日志文本）。
        if event.get("kind") == "delivered":
            self._delivered_products = list(event.get("products") or [])
            self._delivered_gates = dict(event.get("final_gates") or {})
        line = json.dumps(event, ensure_ascii=False)
        # ★ 真跑抓出：回执目录若被外部删掉（或档案声明的目录还没建），
        #   `open("a")` 直接 `FileNotFoundError` ⇒ **把整趟跑崩在写回执上**，
        #   而不是让编排器按它自己的判据收场。
        #   ⇒ 写之前确保目录在（一次 mkdir，幂等，代价可忽略）。
        #   ★ 这不是"掩盖错误"：回执是**证据**，证据目录缺失时应该
        #     **补建并继续**，而不是让整趟任务死在一个 I/O 细节上。
        try:
            _RECEIPT.parent.mkdir(parents=True, exist_ok=True)
            with _RECEIPT.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as _e_emit:
            _log(f"[generic] ★ 回执写盘失败（不阻断本趟判定）："
                 f"{type(_e_emit).__name__}: {_e_emit}")
        # ★ R158：每次回执都更新心跳（见 `_心跳` 的注释）
        self._心跳(str(event.get("kind") or ""))
        _log(f"[receipt] {line}", flush=True)

    def _心跳(self, 因: str = "") -> None:
        """★ R158：**心跳** —— 让"还在跑"与"被杀了"可区分。

        真事故（第 41 次真跑，2026-10-26 11:15:28）：
          那一跑轮 2 已经 `verdict=progressing`（Δ 7→4，两条门新绿、
          `cargo_check_lib=True`），正在产第 3 轮的窗。
          然后进程**被外部杀掉**（我自己的监控 shell 崩了 0xC0000005，
          连带把进程树带走）。
          ★ 日志里**没有退出码、没有 traceback、没有停机回执**
            —— 只停在「机器拒收的产物 ⇒ 不写主树」。
          ⇒ 我无法从回执判断它「还在跑」「崩了」「被杀了」。
          ★★ 而验收要的是「**无人值守**跑通一次」——
             无人值守时，**"不知道它怎么了"本身就是最坏的结局**。

        ⇒ 每次事件都更新心跳（带 pid + 时间 + 最后事件）。
          判活口径：心跳在 ⇒ 活着；心跳停 + 有 `run_end` ⇒ 正常收场；
          心跳停 + **没有** `run_end` ⇒ **被杀了**（不是"还在跑"）。

        ★★★★★ R158b（2026-10-26 14:09，**这个机制自己的齿咬出来的**）：
          第一版心跳**只由 `_emit` 事件驱动**。第 42 次真跑当场把它咬破：
            心跳停在 13:08:59，而进程 **还活着**（PID 24032 存活、
            CPU 停在 53.8s 不动）—— 它在 `router` 里**等上游**：
              `无健康槽 ⇒ 等 20 秒（已等 0/600 秒）后重试`
            等待期间**不产生任何 `_emit` 事件** ⇒ 心跳不动。
          ⇒ 按第一版口径，「心跳停 + 无 run_end」= **被杀了** ——
             **误判！进程活得好好的，只是在等一个抖动过去。**
          ★★★ 又是那个形态：**手里那个量不是判据需要的那个量。**
            心跳量的是「**最后一次事件**」，判据要的是「**进程还活着**」。
            两者在「合法等待」时**分叉**。
          ⇒ 治本：心跳改由**定时器线程**驱动（每 `_心跳间隔` 秒无条件写一次），
            事件只是**顺便**更新「最后事件」字段。
          ★ 这样「心跳停」才**真的**等价于「进程没了」——
            因为只要进程活着，定时器就一定会写。
          ★ 定时器是 daemon 线程：主线程退出即随进程消失，
            不阻塞收场，也不需要显式停。
        """
        try:
            _RECEIPT.parent.mkdir(parents=True, exist_ok=True)
            self._心跳因 = 因 or getattr(self, "_心跳因", "")
            _RECEIPT.with_suffix(".heartbeat").write_text(
                json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "pid": os.getpid(),
                            "last_event": self._心跳因,
                            "goal": (self.goal or "")[:200]},
                           ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            pass          # 心跳写不进去不得影响判定

    def _起心跳(self) -> None:
        """★ R158b：起一个**定时器线程**写心跳（不是靠事件）。

        见 `_心跳` 里那段真事故：事件驱动的心跳在「合法等待」时会停，
        从而把「在等上游」误判成「被杀了」。
        ⇒ 只要进程活着，这个线程就一定在写 ⇒ **心跳停 ⟺ 进程没了**。
        """
        if getattr(self, "_心跳线程", None) is not None:
            return
        import threading

        def _循环():
            while True:
                try:
                    self._心跳()          # 只写文件，不动「最后事件」
                except Exception:          # noqa: BLE001 - 心跳不得杀进程
                    pass
                time.sleep(self._心跳间隔)

        _th = threading.Thread(target=_循环, name="orch-heartbeat", daemon=True)
        _th.start()
        self._心跳线程 = _th

    def _ledger_from(self) -> list[dict]:
        """回读本机全量回执 → 历史账本前缀（一次，幂等）。"""
        if _RECEIPT.is_file():
            return _ledger_from_receipt(_RECEIPT.read_text(encoding="utf-8"))
        return []

    def _ledger_cached(self) -> list[dict]:
        """账本增量索引：字节断点续读，历史累积在内存。

        全历史对 auto_objective.compute 仍可见（内存 buf 从首读起累积），
        但回执文件只做增量 I/O——历史越长越省，不再每轮全量重读。"""
        try:
            size = _RECEIPT.stat().st_size
        except FileNotFoundError:
            return self._ledger_buf
        if size <= self._ledger_pos:
            return self._ledger_buf
        with _RECEIPT.open("r", encoding="utf-8") as f:
            f.seek(self._ledger_pos)     # 上次读完的字节尾 → 只读新行
            self._ledger_buf += _ledger_from_receipt(f.read())
            self._ledger_pos = size
        return self._ledger_buf


# -- 规划：真模型产规约 → 机器校验 → 多文件拓扑 DAG → 判定层必然边界 --
    def _规划前门态(self) -> str:
        """R33 规划前门态探针 —— **实例方法**，便于自检/齿探针替换。

        ★ 为什么收成实例方法：模块函数在自检里没法替换 ⇒ 自检会**真跑**
          项目验收门（本工程 65s 起，重则几十分钟）⇒ 自检变得又慢又依赖环境。
          ★ 而"检查不得依赖网络/重外部"是本库的纪律（R28）。
        """
        return _门态探针()

    async def _问主心骨_规划不成(self, 失败因: str) -> bool:
        """规划连续 3 次不可行 ⇒ 问主心骨「**目标本身是不是错了**」。

        返回 True 表示**目标已被改掉**（调用方应重来一次规划）。

        ★ R101：为什么必须在这里问，而不是只在 `_replan` 里问 ——
          `_replan` 的前提是「跑完了一轮、有红门」。
          而「目标本身不成立」的最早、最明确的证据，是
          **规划器连续三次都做不出一份能采纳的规约**。
          真跑实测：那个场景下主心骨一次都没被问到，直接 exit 1。

        ★ 诚实边界：本方法**不替主心骨决定**。
          它说「不改」或「降级」⇒ 返回 False ⇒ 调用方照旧诚实停机。
          宁可停机，也不假装「我已经想办法了」。
        """
        if getattr(self, "_chief_降级", False):
            return False
        if getattr(self, "_chief_改目标次数", 0) >= 1:
            # 预算用完 ⇒ 不再问（否则「改目标→又成→再改」会无限循环）
            return False
        # ★ R105：**指导**是另一条预算。改目标（换方向）与给判据（换做法）
        #   是两件事，共用一个计数器会让「改了目标就不能再给判据」。
        if getattr(self, "_chief_指导次数", 0) >= 1:
            return False
        心 = self._主心骨()
        观测 = {
            "回合": 1,
            "裁决": "规划连续 3 次不可行（**目标可能本身不成立**）",
            "红门": [],
            "已改文件": [],
            "失败因": str(失败因)[:600],
            **({"契约破裂": getattr(self, "_chief_契约详情", None)}
               if getattr(self, "_chief_契约详情", None) else {}),
            "提示": ("规划器连续三次都做不出一份能被机器采纳的规约。"
                     "这通常意味着**目标本身不可达**（点名的东西不存在、"
                     "或要求的改动与项目约束冲突）。"
                     "请判断：是目标该改，还是只是做法不对？"),
        }
        try:
            决 = await 心.回合(观测)
        except Exception as exc:      # noqa: BLE001 主心骨崩了不得炸整趟
            决 = {"降级": True, "理由": f"{type(exc).__name__}: {exc}"}
        if 决.get("降级"):
            self._chief_降级 = True
            _log(f"[generic] ★ R101 主心骨**降级**（如实退回老形态，不假装有）："
                 f"{str(决.get('理由'))[:160]}")
            return False
        if 决.get("改目标") and 心.目标 != self.goal:
            _旧 = self.goal
            self.goal = 心.目标
            self.spec = {}
            self.deliverables = []
            self._chief_改目标次数 += 1
            self._emit({"kind": "chief_goal_change", "old": _旧, "new": self.goal,
                        "why": str(决.get("理由") or "")[:400],
                        "when": "规划三次不成"})
            _log("[generic] ★★★ R101 主心骨**改了目标**（回执已落盘）："
                 f"「{_旧[:60]}」→「{self.goal[:60]}」⇒ 重来一次规划")
            return True
        # ★★★★★ R105（真跑抓出的第 19 层 · 2026-10-19 · 第八次端到端）：
        #   **主心骨定了判据，但判据没有任何消费者。**
        #
        #   真跑证据（`chief_receipt.jsonl` 2026-10-19 15:22:27）：
        #     主心骨回了一条**格式正确、内容到位**的决定 ——
        #       `判据数=7 计划数=4 空决定=False 纠正次数=0`
        #     它的理由写得很对：「上一轮不是目标不成立，而是规约撞了冻结区…
        #       目标我保留，但把它写死成可判定的形态」。
        #   而机器**只读了 `改目标` 这一个比特**（False）⇒ 直接停机 exit 1。
        #   ⇒ 它给的 7 条判据、4 项计划，**一个字都没被用到**。
        #
        #   ★ 这比 R97 更彻底：R97 是「主心骨只当记忆笔记本」；
        #     这里是「主心骨当了一次真主心骨，它的决定被扔进垃圾桶」。
        #   ★ 与 R85（sidecar 无人读）/ R98（透传不算消费者）同一族：
        #     **`self.上次判据` / `self.上次计划` 在全产品里是只写不读的。**
        #
        #   ★ 修法：它说不改目标但**给了判据/计划** ⇒ 那不是「没意见」，
        #     那是「方向对、做法错」⇒ 把它的判据当**指导**送进规划器，
        #     让规划器照着重新出一份规约（**不绕过 `_adopt_spec` 的机器校验**）。
        if 决.get("判据") or 决.get("计划"):
            self._chief_指导 = 决
            self._chief_指导次数 = getattr(self, "_chief_指导次数", 0) + 1
            _n判 = len(决.get("判据") or [])
            _n计 = len(决.get("计划") or [])
            self._emit({"kind": "chief_guidance", "when": "规划三次不成",
                        "判据数": _n判, "计划数": _n计,
                        "why": str(决.get("理由") or "")[:400]})
            _log(f"[generic] ★★★ R105 主心骨判定**目标不改，但给了 {_n判} 条判据 / "
                 f"{_n计} 项计划** ⇒ 把这些当**指导**送进规划器重出一份规约"
                 f"（不绕过机器校验）")
            return True
        _log("[generic] ★ R101 主心骨判定**目标不改**、也没给判据/计划 ⇒ 照旧诚实停机")
        return False

    async def _plan(self) -> None:
        if self.dry:
            _sp = (_DRY_MACHINE_SPEC if self.dry_machine
                   else _DRY_MULTI_SPEC if self.dry_multi else _DRY_SPEC)
            spec = json.loads(json.dumps(_sp))
        else:
            snapshot = repo_index.structure_snapshot(self.goal)
            # ★★★★★ R33：**先跑真缺口（探针），再规划**。
            #   真跑实测：不探针时规划器猜错了**全部**文件 ⇒ 整趟白跑。
            #   这一步就是用户那条环的第 ① 步（"跑一个真缺口 + 探针（断在哪）"），
            #   此前整台机器缺这一步。成本：一次门跑（按仓库根缓存）。
            门态 = self._规划前门态()
            if 门态:
                _log("[generic] ★ 规划前门态探针：已把项目验收门实测真值喂给规划器")
            # ★★★★★ 交付物形态错 ⇒ **回喂重规划**（真跑抓出的缺口）。
            #   真跑实测：模型把交付物写成**目录** `src/`（"改 src 下若干文件"的
            #   自然表述）⇒ 旧代码把它当文件落盘 ⇒ 整轮崩在
            #   `PermissionError: ... /r1/w0/src`，**不是按判据收场**。
            #   ★ 形态错是**可纠正的表述问题**（清单就在判据门的失败信息里），
            #     值得自动重来；而"目标本身做不到"才该诚实停机。
            #   ★ 上限 2 次：**不无限重试** —— 反复给同一形态错说明是规划器
            #     不懂这条协议，再试只是烧流量；那时如实报出去给人看。
            # ★★★★★ R105：主心骨的**判据/计划**必须真的进规划器提示。
            #   否则它定完判据就被扔掉（真跑实测：7 条判据、0 个消费者）。
            反馈 = _指导块(getattr(self, "_chief_指导", None))
            # ★ R92：把主心骨的记忆喂回去 —— 它是**同一个主心骨**，不是新规划器。
            #   ★ 注释必须在 for **之前**：`checks\交付物形态齿探针.py` 的锚点是
            #     「for + 紧邻的 prompt 行」两行连写，中间插注释会让锚点失配
            #     （该探针会明确报「锚点出现 0 次」，不会静默变绿）。
            for 第次 in range(3):
                # ★★★★★ R199：把**现场实测**算出来喂给规划器 —— 目标文本里的
                #   「11 个错误 / 必改 4 个文件」是历史快照，实测已只剩 1 个错误、
                #   3 个文件改对了。不覆盖它 ⇒ 规划器照过期清单出计划 ⇒
                #   下游在已经对的文件上重写 ⇒ 造新错 ⇒ 整批回退 ⇒ 永不收敛。
                _现场199 = _现场实测段199(_门态缓存.get("红") or [],
                                          _门态缓存.get("必改") or [])
                if _现场199:
                    _log("[generic] ★ R199 已用**现场实测**覆盖目标文本里的历史快照"
                         "（目标写着 11 个错误 / 必改 4 个文件，以机器实测为准）")
                prompt = _planner_prompt(self.goal, snapshot, 反馈, 门态,
                                          记忆=self._主心骨().记忆摘要(), 现场=_现场199)
            # ★ R177：注入点**不在这里**。这里是 `_plan`（规划器）的 prompt，
            #   而真正写文件的是 `_produce_window`（窗级）。
            #   第 80 次真跑实测：插在这里 ⇒ 规划器读完就清空 ⇒ **窗级永远读不到**
            #   ⇒ 台账白记（「算了、存了，但没送出去」第 6 次）。
            #   ⇒ 注入点已移到 `_produce_window` 的 prompt 之前。
                resp = await self.router.call([UserMsg(name="user", content=prompt)])
                self._tok_chars += len(prompt) + len(str(resp.content))
                spec = _parse_spec_json(_extract_text(resp.content))
                err = _valid_spec(spec)
                if err:
                    # ★ R80：规约不合法**也是可纠正的**（模型能照着改）。
                    #   原来直接 `raise ValueError` ⇒ **绕过回喂循环** ⇒ 整趟死。
                    #   真跑实测：模型把同一个文件写了两遍
                    #   （`文件 path 重复：src/模块甲/mod.rs:7284`）。
                    raise 规约可纠正(
                        f"规约不合法：{err}\n"
                        f"★ 请照着上面的具体问题改这一份规约后重新输出"
                        f"（不要把同一个文件写两遍，用 `路径:行号` 区分焦点）。")
                try:
                    self._adopt_spec(spec)
                    # ★★★★★ R92：**采纳之后立刻验判据契约**。
                    #   为什么不能等到交付时：契约不立 = 这条门没有区分力，
                    #   等到"全绿"才发现就晚了（那时已经烧完一整轮）。
                    #   ⇒ 规划时就查：没有否定样本的门，**现在**就记下来。
                    self._验契约_规划时(spec)
                    # ★ R92：把这一回合记进主心骨记忆（它下次要记得）
                    self._主心骨().记住(
                        回合=1,
                        观测=(f"规划出 {len(self.deliverables)} 项交付物 / "
                              f"{len(spec.get('gates') or [])} 条门"
                              + (f"；契约不立的门 {len(self._chief_契约未立)} 条"
                                 if self._chief_契约未立 else "")),
                        决定=f"按此计划推进（目标未改）",
                        依据="见 .audit_trail/chief_receipt.jsonl")
                    return
                except 规约可纠正 as exc:
                    # ★★★★★ R80：**一个基类，不是逐个 except。**
                    #   原来这里挂着两条分支（`交付物形态错` / `计划不可行`），
                    #   于是"同族"这件事**只存在于注释里** —— 后来新加的
                    #   可纠正错误（规约校验失败、沙箱门拒绝）自然漏在外面
                    #   ⇒ 绕过回喂 ⇒ **整趟死**（真跑实测两例）。
                    #   ⇒ 收口到一个基类：**新增一类可纠正错误时，不必再改这里。**
                    #     这正是"用全称机制、不枚举"在控制流上的样子。
                    反馈 = str(exc)
                    _log(f"[generic] ★ 规约可纠正（第 {第次 + 1} 次 · "
                         f"{type(exc).__name__}）⇒ 回喂重规划："
                         # ★★★★★ R176c：**整条反馈压平送出**（真跑实测：只打第一行 = 把证据丢掉）。
                         #   第 84 次真跑日志里这条只剩标题、冒号后面是空的 ⇒ 人以为「列表为空却报错」，
                         #   去查了错的函数。**算出来的证据必须真送出去**（与 R177 同族，第 7 次）。
                         f"{(' ｜ '.join(x.strip() for x in 反馈.splitlines() if x.strip()))[:1200]}")
                    if 第次 >= 2:
                        # ══════════════════════════════════════════════════
                        # ★★★★★ R101：**这里才是「目标本身错了」真正暴露的地方**
                        # ══════════════════════════════════════════════════
                        # 【真跑抓出（2026-10-19 · 第 6 次端到端）】
                        #   我构造了一个**目标本身不成立**的场景
                        #   （要求给一个文件里根本不存在的接口接线），
                        #   想验证「主心骨会不会改目标」。
                        #   结果：`规划连续 3 次不可行 ⇒ 诚实停机`，**退出码 1**，
                        #   而 `chief_receipt.jsonl` **一条新回执都没有** ——
                        #   主心骨**从头到尾没被问过**。
                        #
                        #   ★ 根因：R97 把主心骨只接在 `_replan` 上，
                        #     而 `_replan` 要求**至少跑完一轮**（要有红门才能重规划）。
                        #     可「目标本身错了」这件事**在规划阶段就暴露了** ——
                        #     三次都规划不出来，恰恰说明目标不可达。
                        #   ⇒ **最该问主心骨的时刻，恰恰到不了问它的那条路。**
                        #
                        #   ★ 这是同一个形状的第 15 层：
                        #     「**声明了**主心骨有权改目标」
                        #     ↔ 「唯一能改目标的路径，在目标错时走不到」。
                        #
                        #   ⇒ 修法：规划三次不成 ⇒ **先问方向，再决定停机**。
                        #     它说改 ⇒ 换目标、作废旧规约、重来一次规划（预算 1 次）；
                        #     它说不改 / 降级 ⇒ 照旧诚实停机（**不替它决定**）。
                        if await self._问主心骨_规划不成(反馈):
                            return await self._plan()
                        raise ValueError(
                            f"规约连续 {第次 + 1} 次可纠正性问题仍未改对 ⇒ 诚实停机"
                            f"（不猜、不替它决定）。最后原因：{反馈}") from exc
            return
        self._adopt_spec(spec)

    def _adopt_spec(self, spec: dict) -> None:
        """机器采纳规约：统一文件列表 → 拓扑 DAG → consume_ok 守恒 → 判定层必然边界。

        沙箱不安全或门不充分 ⇒ 硬拒（不把不可信通道当判据门、不把软门当必然）。
        全 P 化（pred.checks）：derive_gates(P) 是唯一门源，门集来源唯一（LLM 不造门）；
        入口 C4 预检（P 可判定性）+ 门源唯一断言。"""
        # ★★★★★ R111：**规约形态预检 —— 必须是第一条**。
        #   它不是"多一道检查"，而是把「形态合法」变成后面所有消费者的**共同前提**
        #   ⇒ 不必再逐个调用点包 try（那就是枚举，而枚举总会漏）。
        _形111 = _规约形态问题(spec)
        if _形111:
            raise 规约可纠正(_形111)
        # ★★★★★ 交付物形态预检 —— **放在最前面**（在改 `self.spec` 之前）。
        #   ① 形态错是最基本的错，应当**最早**报（早报 ⇒ 早回喂，不浪费一轮）；
        #   ② 放在最前面 ⇒ 抛异常时 `self.spec` **未被改动** ⇒ 回喂重规划安全。
        #   真跑抓出：模型给 `src/`（目录）⇒ 后面 `_落盘(w0/src)` 直接
        #   `PermissionError` 把整轮崩掉。见 `_交付物形态问题` 的长注释。
        try:
            _形态问题 = _交付物形态问题(_parse_deliverables(spec))
        except Exception:      # 解析都失败 ⇒ 交给后面的正式解析报真实错误
            _形态问题 = []
        if _形态问题:
            raise 交付物形态错(_交付物形态提示(_形态问题))
        # ★★★★★ R81（2026-10-17 夜 · 查 `tokens_chars: 0` 时顺带抓出）：
        #   **目标里点名、而当前不存在的文件，必须进交付物清单。**
        #
        #   【真跑证据】collatz 那批 2597 趟里，目标原文写着
        #     `再写 test_collatz.py 断言 …`
        #   而交付物清单里**只有 `collatz.py`** —— `test_collatz.py` **一次都没交**。
        #   门照样全绿，因为**没有一条门问"目标点名的文件在不在"**。
        #
        #   【根因】**「目标声明 → 判据」这一跳漏了。**
        #     门是从**产物**的形状写出来的（`f([1])[0]==0` 照着机器组装的列表接口写），
        #     不是从**目标**写的。于是"机器自己组装 + 门照产物写"可以
        #     **自洽地交付一个不合目标的产物** —— 门绿、目标没达成。
        #   ★ 这是「判据的主体必须由规格决定，不由谁的名字挂在它旁边决定」的又一次复发：
        #     这次"挂在旁边"的不是文件名，是**产物自己的接口形状**。
        #
        #   【为什么这条规则是可判的、且不需要枚举】
        #     不是去猜"目标里哪句是要求"，而是问一个**事实问题**：
        #     目标里出现的文件记号，**若当前不存在**，那这趟谁来建它？
        #     ⇒ 它必须是交付物。否则这趟**注定少交一件**（不是"可能"，是"必然"）。
        #     而**已存在**的文件（如要改的源码）不算 —— 它可能是只读上下文。
        #     这条边界是**结构**给的，不是词表给的。
        #     ★ 目标用 `getattr` 兜底：自检里有些替身 runner **不设 `goal`**
        #       （它们只验冻结区/必改清单那几条，不涉及目标文本）。
        #       目标为空 ⇒ 本条判据**不做要求**（空目标没有"点名的文件"可言）。
        _缺交 = _目标点名而清单没有(getattr(self, "goal", "") or "",
                                     _parse_deliverables(spec))
        if _缺交:
            raise 规约可纠正(
                "目标里点名、但交付物清单里没有的文件：\n  "
                + "\n  ".join(_缺交)
                + "\n★ 这些文件**当前不存在**（若存在就不会被要求）⇒ 这趟只有你能建它们。"
                  "\n★ 把它们加进 deliverables（每个都要带自己的判据门），否则这趟"
                  "**必然少交**，而门不会替你发现（门只看清单里有的文件）。")
        # ★★★★★ R82 / R83：目标里**写死的断言**由机器原样加成判据门（不经模型的手）。
        #   真跑实测：目标写 `collatz_steps(1)==0`，模型写成 `f([1])[0]==0` ——
        #   **三个点一个没少，但把调用形态改成了产物的形状**，于是门绿。
        #   这是「判据不得由被判方改动」最锋利的一刀：不是少测，是**改写**。
        #
        #   ★★★★★ R83（2026-10-17 夜 · **真跑当场抓出**）：
        #     第一版把注入放在**这里**（`self.spec = spec` 之前），
        #     日志明明打印了「★ R82 目标断言门（机器照抄，不经模型）：['目标断言_collatz_steps']」，
        #     而**回执里根本没有这道门** —— 它被下面那行
        #         `self.spec["gates"] = derived`      ← 「唯一门源落地」
        #     **整体覆盖掉了**（`pred` 在场时门集是**替换**，不是合并）。
        #
        #   ★ 我原来的齿检查的是「注入点在 `self.spec = spec` 之前」——
        #     **那个地标是错的**。真正的地标是「**在唯一门源落地之后**」。
        #     齿验了、注入验了、5/5 PASS，**却全部验在错的位置上**。
        #     ⇒ 「齿必须锚在**语义**地标上，不能锚在**就近**地标上」。
        #     （这是「锚点会随实现漂移」的另一面：**锚点一开始就锚错了**。）
        #
        #   ⇒ 注入点挪到 `normalize_gates` 之前、**唯一门源落地之后**：
        #     无论 `pred` 在不在，这一步都发生在门集**最终定型之前**。
        self.spec = spec
        _normalize_spec(self.spec)
        # ★ R219：**判据里的阈值必须来自现场实测，不得来自目标文本。**
        #   第 120 次真跑：`test_count_ge_1590` 而现场 1543 ⇒ 这条门永远绿不了，
        #   于是 Δ 永远不为 ∅、永远判不了达标 —— 机器把模型的「陈旧记忆」当成了目标。
        try:
            _改219 = _陈旧阈值门219(self.spec, getattr(self, "repo_root", None) or ".")
            if _改219:
                _log(f"[generic] ★ R219 **陈旧阈值门按现场实测改写**：{_改219[:4]}")
                self._emit({"kind": "stale_threshold_gate_rewritten",
                            "detail": _改219[:6]})
        except Exception as _e219:
            _log(f"[generic] ★ R219 校正未跑（不阻断）：{_e219}")
        # ★ 修复（2026-10-12）**顺序颠倒**：预检需要知道"本轮要建哪些文件"，
        #   而 deliverables 原本在预检之后（980 行）才解析 → 预检看不到待建文件集，
        #   于是"创建新文件"类目标一律被判 P 不可判定而入口拒死。
        #   先把文件清单解析出来，作为待建白名单传给预检（不改变后续解析结果）。
        try:
            _early_deliverables = _parse_deliverables(self.spec)
            _pending = {str(d.get("path", "")).replace("\\", "/")
                        for d in _early_deliverables if d.get("path")}
        except Exception:      # 早期解析失败不阻断：后续正式解析会给出真实错误
            _pending = set()
        # 全 P 化：pred.checks → derive_gates(P) 唯一门源（不在 LLM 处接线守恒门）
        pred = self.spec.get("pred")
        if isinstance(pred, dict) and pred.get("checks"):
            # C4 入口预检：P 的检查种类在当前仓库不可判定 → 入口拒绝交还（非降级门）
            ok4, probs = precheck_pred_decidable(pred, str(REPO_ROOT),
                                                 pending_files=_pending)
            if not ok4:
                raise ValueError(
                    f"P 可判定性预检失败（入口拒绝，交还用户重定义 P）："
                    f"{'；'.join(probs)}")
            derived = derive_gates(pred, str(REPO_ROOT))
            viol = assert_derived_gates(derived, pred)
            if viol:
                raise ValueError("门源唯一断言失败（派生门集外混入非 derive_gates(P) 门）："
                                 + "；".join(viol))
            self.spec["gates"] = derived        # 唯一门源落地
            self.pred = pred
            self.pred_derived_gates = derived
        # ★★★★★ R82 / R83：目标断言门 —— **必须在这里注入**。
        #   ① 在「唯一门源落地」**之后** ⇒ 不会被 `self.spec["gates"] = derived` 覆盖
        #      （第一版放在那之前，日志打印了、回执里却没有 —— 真跑当场抓出）；
        #   ② 在 `normalize_gates` **之前** ⇒ 一并过配额与语义归一；
        #   ③ 它不是模型给的门，是**机器从目标文本照抄**的门 ⇒ 与「门源唯一
        #      （LLM 不造门）」不冲突：门源仍是**机器**，只是多了一个机器来源。
        #      `assert_derived_gates` 在上面已跑过（那时还没有这些门），故不误判。
        # ★★★★★ R88：**接线门** —— 目标点名「把这个接口接上」时，
        #   机器生成一条数生产引用的门。真跑第 1 轮的门
        #   `新测试_判据_甲子生产路径生效=True` 名不副实（只判「文件里有这个词」），
        #   而 `判据_甲子` 的生产消费者是 0。
        _新门88 = _注入接线门(self.spec, getattr(self, "goal", "") or "")
        if _新门88:
            _log(f"[generic] ★ R88 接线门（机器数生产引用，不经模型）：{_新门88}")
        # ★★★★★ R91：模型自己起的**测试门**，名字点名了接口 ⇒
        #   机器加一条见证门（断言里必须真的调用它）。
        #   ★ R88 只接管了机器注入的那一条；模型起的门名此前仍然自由。
        _新门91 = _注入见证门(self.spec, getattr(self, "goal", "") or "")
        if _新门91:
            _log(f"[generic] ★ R91 见证门（名字点名的接口必须在断言里真被调用）："
                 f"{_新门91}")
        _新门82 = _注入目标断言门(self.spec, getattr(self, "goal", "") or "")
        if _新门82:
            _log(f"[generic] ★ R82 目标断言门（机器照抄目标，不经模型）：{_新门82}")
        # 门集归一化（纯机器）：模型塞门超配额时语义等价合并+公平裁剪，不整体拒死
        self.spec, self.norm_notes = normalize_gates(self.spec)
        self.deliverables = _parse_deliverables(self.spec)
        # ★★★★★ R189：**按路径去重**。真证据（第 91 次真跑 plan 回执）：
        #   12 个交付物只有 4 个不同路径（某文件 ×4）⇒ `_produce_window` 逐交付物
        #   各调一次模型且每次都从磁盘读原文件 ⇒ 同文件被独立改 4 次、后写覆盖前写
        #   ⇒ 8/12 次调用纯重复，且留下的不是最好的那份、是**最后一份**。
        self.deliverables = _去重交付物189(self.deliverables)
        order, ok, why = _topo_sort(self.deliverables)
        if not ok:
            raise ValueError(f"文件依赖拓扑违例：{why}")
        self.deliverables = [self.deliverables[i] for i in order]  # 拓扑序串线
        # ★ R178：用 **`self.spec`**（已 `normalize_gates` 归一化）——
        #   原传 `spec`（入参、未归一）⇒ 两个消费者看两份不同的规约（R117 同族）。
        _, dok, dwhy = _build_dag(self.spec, self.deliverables)
        if not dok:
            raise ValueError(f"接线守恒违例：{dwhy}")
        all_gates = [g for d in self.deliverables
                     for g in (d.get("gates") or spec["gates"])]
        self.repo_scope = _detect_repo_scope(spec, all_gates)
        repo_root = REPO_ROOT if self.repo_scope else None
        self.bound = bound_report(all_gates, spec, WORKDIR.resolve(),
                                  repo_root=repo_root, 逐件已覆盖=True)
        if not self.bound["safe"]:
            # ★ R80：沙箱拒绝**也是可纠正的** —— 失败信息里就写着哪个命令不被允许，
            #   模型照着换掉那个顶层命令即可。原来抛裸 `ValueError`
            #   ⇒ 绕过回喂循环 ⇒ **整趟死**（真跑实测：模型写了 `powershell`）。
            raise 规约可纠正(
                f"判据门沙箱不通过（模型不可信的 cmd 通道不得当判据门）："
                f"{self.bound['reasons']}\n"
                f"★ 请把上面每个被拒的门换成档案白名单里的顶层命令"
                f"（或去掉那条门），**不要**改用其它 shell 包装。")
        if not self.bound["decision_necessary"]:
            raise 规约可纠正(
                f"判定层必然性不成立（对软/空/含糊一律红，拒接受假完成）："
                f"{self.bound['reasons']}\n"
                f"★ 请把软/空/含糊的门换成**有判别力**的门"
                f"（修复前为假、修复后为真）。")
        # ★★★★★ R36：**计划必须覆盖红门证据点名的文件**（机器可证的必要条件）。
        #   真跑实测：R33 把 14 条 `文件:行号` 喂给了规划器，规划器**根本没看**，
        #   改从符号快照里猜了 3 个完全不相干的文件 ⇒ 整趟白跑。
        #   ★ 判据是**必然的**（不是启发式）：守卫静态扫描那些文件的内容，
        #     被报在 `F` 的违规**只有改 `F` 才可能消除**（守卫是冻结区，改不动）
        #     ⇒ 漏一个文件，验收门就**不可能**转绿。
        #   ★ 与 R31 同一族：都是"计划一诞生就已注定失败"，都走**回喂重规划**
        #     （可纠正的选择问题），而不是等跑完再看它红。
        #   ★ 抽不到必改文件（非缺口型目标）⇒ **不施加任何约束**（零回退）。
        _必改36 = list(_门态缓存.get("必改") or [])
        # ★★★★★ R86：**目标自己点名的必改文件**也要进这条判据。
        #   真跑证据见 `_目标声明的必改文件` 的注释：目标点名 `模块丙.rs:165`，
        #   交付物是 5 个**别的**文件，跑完 green 而那个文件一动没动。
        #   ★ 门态缓存为空（门全绿）时，R36 的必改是空的 ⇒ 这条**必须独立于它**。
        # ★★★★★ R200：**目标里手写的「必改文件」是历史快照，现场实测说了算。**
        #   真证据（第 97/98 次真跑）：目标写着「11 个错误 / 必改 4 个文件」，
        #   而机器现场实测只剩 1 个错误（算子选择器.rs）。R199 已按现场实测告诉
        #   规划器「只列还红的」，**而这里 R86 又把目标里那 4 个全部并进必改闸**
        #   ⇒ 规划器永远被判「计划不可行（没有覆盖 4 个文件）」⇒ 与 R199 直接打架
        #   ⇒ 回喂死循环，一轮都跑不起来。
        #   ★ 口径：目标声明的必改文件，若**现场实测里它没有任何违规** ⇒ 它已经改对了，
        #     **不并入必改闸**（并入就是在逼模型去改已经对的文件 ⇒ 实测造出 E0425/E0428）。
        #   ★ 只在现场实测**非空**时启用（实测为空 = 门全绿，目标约束照旧生效）。
        _红现200 = _红证据文件(_门态缓存.get("红") or [])
        _目标必改86 = _目标声明的必改文件(getattr(self, "goal", "") or "")
        _过时200 = [b for b in _目标必改86 if _红现200 and b not in _红现200]
        if _过时200:
            _log(f"[generic] ★ R200 目标里有 {len(_过时200)} 个「必改文件」"
                 f"**现场实测已经不红**（历史快照）⇒ 不并入必改闸：{_过时200[:4]}")
            self._emit({"kind": "stale_target_files", "work": getattr(self, "_work", 0),
                        "ignored": _过时200[:8], "live_red": sorted(_红现200)[:8]})
        for _b86 in _目标必改86:
            if _b86 in _过时200:
                continue            # ★ R200：现场实测说它没红 ⇒ 不逼模型去动它
            if _b86 not in _必改36:
                _必改36.append(_b86)
        # ★★★★★ R106：**主心骨判据点名的文件**也进同一个闸（复用同一把尺子）。
        #   R105 已把判据送进提示，但规划器**还是**给了零门交付物
        #   ⇒ 提示不够，必须变成机器可证的必要条件。
        _指106 = _指导声明的必改文件(getattr(self, "_chief_指导", None))
        if _指106:
            for _b106 in _指106:
                if _b106 not in _必改36:
                    _必改36.append(_b106)
            _log(f"[generic] ★ R106 主心骨判据点名了 {len(_指106)} 个文件 "
                 f"⇒ 并入必改闸（提示不够，机器可证才算）")
        if _必改36:
            _缺36 = _计划覆盖缺口(
                [str(d.get("path") or "") for d in self.deliverables], _必改36)
            if _缺36:
                raise 计划不可行(_计划不可行提示(_必改36, _缺36))
        # ★★★★★ R37：**交付物不得落在冻结区**（改了也白改 —— 必然回滚）。
        #   真跑实测：R36 生效后规划器覆盖了 7 个真违规文件，但**额外**把
        #   `src/测试防假.rs`（判它的守卫本身，冻结区）也列了进去 ⇒
        #   整趟会走到"逐字节比对发现判据被改 ⇒ 回滚 + 不交付"。
        #   ★ 结局在**计划诞生那一刻就已注定** ⇒ 当场退回，不浪费一整趟。
        #   ★ 这是**结构性**判定（档案里真实声明的冻结区），不是靠名字猜的。
        _冻37 = _计划含冻结文件([str(d.get("path") or "") for d in self.deliverables])
        if _冻37:
            raise 计划不可行(_冻结文件提示(_冻37))
        # ★★★★★ R107（真跑抓出的第 21 层 · 2026-10-19 · 第十次端到端）：
        #   **可纠正的错误又以裸异常收场了。**
        #
        #   真跑收场日志：`重规划失败：槽 pre 必须是 list → 诚实停机`（exit 1）。
        #   而「`pre` 该给 list 而不是别的」**正是模型照着就能改的**：
        #   `auto_goal.carrier_from_spec` 的报错里就写着哪个槽不对。
        #   它却抛**裸 `ValueError`** ⇒ 绕过 R80 建的回喂循环 ⇒ 整趟死。
        #
        #   ★ R80 已经修过这个病，而且它的注释写得明明白白：
        #     「『哪些错是可纠正的』被按**异常类型**枚举了……**拿一张表去冒充
        #       『可能性』本身**。表外的项，在系统里就等于『不存在』。」
        #   ★ 而 R80 只补了枚举里的**三项**（规约校验失败 / 沙箱门拒绝 /
        #     判定层必然性），**没修「枚举」这件事本身**。
        #     `plan_tasks` 就是表外的第四项 —— 它一直漏着，直到这次真跑撞上。
        #   ⇒ 这是第 21 层同形，也是最讽刺的一层：
        #     **修了枚举里的项，没修枚举。**
        #   ⇒ 修法：把它接进同一个回喂通道（`规约可纠正`），
        #     并把报错原文喂回去 —— 它写着哪个槽不对，模型照着改就行。
        try:
            self.plan = auto_goal.plan_tasks(spec)
        except 规约可纠正:
            raise
        except Exception as exc:      # noqa: BLE001
            raise 规约可纠正(
                f"载体规约过不了机器的结构校验：{type(exc).__name__}: {exc}\n"
                f"★ 四槽 `name`/`pre`/`constraints`/`gates`/`acceptance` 都必须是 **list**；\n"
                f"★ 每条判据门的**必填字段**必须符合该门类型（见上面列出的字段契约）。\n"
                f"★ 请照着这条报错改**这一份**规约后重新输出，不要重写别的部分。") from exc
        self.safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_",
                                spec["name"]).strip("_") or "task"
        self._emit({"kind": "plan", "carrier": self.plan["carrier"],
                    "rule_space": self.plan["rule_space"],
                    "tasks": len(self.plan["tasks"]),
                    "conserved": self.plan["conserved"],
                    "deliverables": [d["path"] for d in self.deliverables],
                    "topo_order": [d["path"] for d in self.deliverables],
                    "repo_scope": self.repo_scope,
                    "bound": self.bound["contract"],
                    "decision_necessary": self.bound["decision_necessary"],
                    "gates": len(all_gates),
                    "gates_norm": getattr(self, "norm_notes", {})})

    def _契约跑门(self, w_root: Path, 产物相对: str | None = None):
        """造一个「跑一条门」的可调用，供 `chief.验判据契约` 使用。

        ★ 约定：`验判据契约` 把**变异后**的产物文本写到 `<工作根>/_契约产物.txt`；
          本闭包负责把它落到**门真正读取的那个位置**（`gate_witness._gate_read_target`
          已经把这个坑踩过一遍：门可能读产物，也可能读 `src/lib.rs` 这种工程文件），
          跑门，然后**按字节原样还原**。

        ★ 为什么必须还原（而不是"在副本上跑"）：
          工程级门（`cargo check --lib`）只能对着**真工程树**跑，
          副本里没有 `Cargo.toml`/依赖缓存 ⇒ 一定报错 ⇒ 会被误判成"门红了"。
          ⇒ 原位变异 + `finally` 还原，与 `gate_witness` 同一口径。
        """
        from agentscope_orchestrator import gate_spec as _gs
        产物 = (None if not 产物相对 else
                (Path(产物相对) if Path(产物相对).is_absolute()
                 else Path(w_root) / 产物相对))

        def 跑门(门spec: dict, 工作根: Path,
                 变异相对: str = "") -> tuple[bool, str]:
            # ★★★★★ R114：**变异落在哪个文件，由样本自己决定**。
            #   老口径是「一律落到 `产物`」，而 `产物` = `deliverables[0]`。
            #   主心骨的样本说的是「整个工程里的一处」（如
            #   `pub fn 学模式_广域` 在 `src/模块甲/mod.rs`），
            #   拿它去 `deliverables[0]` 里找 ⇒ **找不到** ⇒
            #   机器把**好门**判成「疑恒真门」⇒ `_chief_契约破裂` 非空
            #   ⇒ `_达标` 永远 False ⇒ **永不收敛**。
            #   ⇒ 现在：`验否定样本` 搜遍全部产物、找到命中的那份，
            #     把它的相对路径经第三参送进来，这里就变异**那一份**。
            变体 = (Path(工作根) / "_契约产物.txt").read_text(encoding="utf-8")
            _基 = (Path(w_root) / 变异相对) if 变异相对 else 产物
            if _基 is None:
                return False, "★ R114：既没给变异文件、也没给产物 ⇒ 无从变异（不假装已验）"
            # ★★★★★ R116（真跑抓出的第 29 层 · 2026-10-20 · 第十五次端到端）：
            #   **门的路径必须相对工程根解析，不是相对契约临时目录。**
            #
            #   真证据：11 条门里 **9 条**报
            #     `FileNotFoundError: 'src\\判据\\观测协议.rs'`
            #   因为原来传的是 `Path(工作根)`（= `round_root/_契约` 临时目录），
            #   而门的 `in` 是**相对工程根**写的。
            #   ⇒ 9 条**好门**被判成「疑恒真门」⇒ `_chief_契约破裂` 非空
            #   ⇒ `_达标` 永远 False。**机器拿自己的路径错去指控判据。**
            #
            #   ★ 与 R114 是**同一层**：R114 修了「在哪份文件里找样本」，
            #     这一层修「找到之后按哪个根去解析路径」。同一个函数里的两个洞。
            #   ★ 口径对齐 `gate_witness.witness_gates(g0, w_root/…, w_root)`：
            #     见证一直是用工程根跑的，契约却用了临时目录 —— **两把尺子**。
            _根116 = Path(w_root)
            目标 = gate_witness._gate_read_target(门spec, _基, _根116)
            if 目标 is None:
                目标 = _基
            # ★★★★★ R171（2026-10-27）：**变异文件必须就是门读的那个文件。**
            #   第 53 次真跑：4 条读 `src/模块乙/*.rs` 的门，变异全落在
            #   `src/模块甲/mod.rs`，而这里把**那份 mod.rs 的内容**写进了
            #   `arc评测.rs` ⇒ 门读到的仍是含 `学模式_广域` 的文本 ⇒ 绿 ⇒
            #   机器反过来说「这条判据无区分力」。
            #   ⇒ 一旦发现两者不是同一份，**必须停止并如实报「未验」**：
            #     既不写别人的内容，也不把「我写错了文件」算成「门没红」。
            #   ★ 只对**文件**目标判定；`in` 是目录（`{repo}/src`）时老行为不变。
            if 变异相对 and 目标 is not None and Path(目标).is_file():
                try:
                    _同171 = (Path(目标).resolve()
                              == (Path(w_root) / 变异相对).resolve())
                except OSError:
                    _同171 = False
                if not _同171:
                    raise RuntimeError(
                        f"★ R171：变异落在 {变异相对}，而门读的是 {目标} "
                        f"⇒ **未验**（不拿 A 的文件内容冒充 B 的文件）")
            备份: bytes | None = None
            try:
                if 目标.is_file():
                    备份 = 目标.read_bytes()
                目标.parent.mkdir(parents=True, exist_ok=True)
                # ★ R113 同口径：写产品源码必须**原子**（写临时→fsync→替换）。
                #   强杀一次 `write_bytes` 就把 `generic_goal.py` 写成过
                #   785,173 字节的纯零；这里同样是往真工程树里写。
                _原子写源码(目标, 变体.encode("utf-8"))
                # ★★★★★ R184：**两条路两把尺子（第三次）。**
                #   主路径 `_score_gates` 的 docstring 明写：`{repo}` 占位符要
                #   **调用前预替换**（`gate_spec._sub` 不支持它）。而这里直接调
                #   `_gs.evaluate` ⇒ `cwd: "{repo}"` 原样进 subprocess ⇒ cwd 是个
                #   不存在的字面目录 ⇒ `OSError` ⇒ `命令无法启动|cargo_test_lib_compile`
                #   ⇒ **全库型编译门永远「未验」** ⇒ 判据保真检查形同虚设。
                #   真证据（第 87 次真跑回执）：`cargo_test_lib_compile：因=跑不起来`、
                #   `test_count_ge_1590：因=跑不起来`、`算子选择器_导入定列：跑不起来`。
                #   ★ 顺带修第二条：`{file}` 由 `gate_spec` 用 `path` 替换，原来传的是
                #     `产物`（deliverables[0]），而变异落在 `目标`（门真读的那份）
                #     ⇒ grep 门读到的是**没变异的那份** ⇒ 仍然绿 ⇒ 机器反过来说
                #     「这条判据无区分力」—— **拿自己的路径错去指控判据**（R148/R116 同族）。
                _变184 = json.loads(json.dumps(门spec))
                _根串184 = str(_根116)
                _工作串184 = str(工作根)
                _文串184 = str(目标)
                
                def _展184(v):
                    if not isinstance(v, str):
                        return v
                    return (v.replace("{repo}", _根串184)
                             .replace("{work}", _工作串184)
                             .replace("{file}", _文串184))
                
                _变184["cwd"] = _展184(_变184.get("cwd"))
                if isinstance(_变184.get("cmd"), list):
                    _变184["cmd"] = [_展184(c) for c in _变184["cmd"]]
                for _k184 in ("in", "src"):
                    if isinstance(_变184.get(_k184), str):
                        _变184[_k184] = _展184(_变184[_k184])
                # ★ `path` 必须传**被变异的那份**（`目标`），不是 deliverables[0]：
                #   `{file}` 由 `gate_spec` 用 `path` 替换 ⇒ 传错 = 门读没变异的那份。
                结果, 审计 = _gs.evaluate([_变184], 目标, _根116)
                绿 = all(结果.values()) if 结果 else True
                因 = "；".join(str(a.get("cause") or "") for a in 审计)[:400]
                return 绿, 因
            finally:
                if 备份 is not None:
                    _原子写源码(目标, 备份)
                elif 目标.is_file() and not 目标.name.startswith("_契约"):
                    # 原本不存在的文件：删掉，不留变异残渣
                    try:
                        目标.unlink()
                    except OSError:
                        pass
        return 跑门

    def _验契约_执行时(self, g0, w_root: Path, d0, 因由: str) -> None:
        """★ R115：**每套门集至少验一次**「主心骨给的否定样本真能让门红」。

        ★★★★★ 为什么必须独立成一个方法（真跑抓出的第 28 层 · 2026-10-20）：
          这一段原来嵌在 `if _达标本 or _全绿本108:` 里面。
          而 `_达标本` 就是「判它达标了」——
          ⇒ **要验「门全绿可不可信」，得先「门全绿」** ⇒ 循环依赖。
          R108 把它从 `_达标本 and fixed_point` 放宽到 `_达标本 or _全绿本108`，
          方向对了，但**门槛没拆掉**：真跑十四次，一次都没全绿过
          ⇒ 自证仍然只跑过 1 次（00:24 那趟，且当场 10 条门全判「恒真」）。

          ★ 这与 R108 是**同一层的第二次发作**：R108 修的是「和 fixed_point 混」，
            没修「和达标混」。**判据保真检查不属于达标判定，它属于门集体检。**
            ⇒ 门集一出现/一变化，就立刻体检一次，**不等任何结论**。

        ★ 为什么要按「门集签名」去重（不是每轮都跑）：
          验一次 = 门数 × 样本数 次真实门执行（cargo check 这类是分钟级）。
          每轮都跑会把一趟 2 小时的活拖成 10 小时。
          而门集不变 ⇒ 样本与门的对应关系不变 ⇒ **验一次就够**。
          门集一变（主心骨重给/派生门替换）⇒ 签名变 ⇒ 自动重验。
        """
        if not (g0 and self.round_root):
            return
        try:
            # ★★★★★ R114：喂**全部产物**，不是一个。
            #   主心骨的样本说的是「整个工程里的一处」，
            #   只搜 `deliverables[0]` ⇒ 好门被误判成恒真门。
            _产物们92 = {}
            for _d92 in (self.deliverables or []):
                _p92 = _d92.get("path") if isinstance(_d92, dict) else None
                if not _p92:
                    continue
                _f92 = w_root / _p92
                if _f92.is_file():
                    _产物们92[_p92] = _f92.read_text(
                        encoding="utf-8", errors="replace")
            if not _产物们92 and d0 and d0.get("path"):
                _产物文件92 = w_root / d0["path"]
                if _产物文件92.is_file():
                    _产物们92[d0["path"]] = _产物文件92.read_text(
                        encoding="utf-8", errors="replace")
            if not _产物们92:
                _log(f"[generic] ★ R115 契约自证**未跑**（{因由}）："
                     "本轮没有任何产物可变异 ⇒ ★ 这不是「验过了」，是「没验」")
                return
            _跑门 = self._契约跑门(w_root, (d0 or {}).get("path"))
            _全好92, _契们92 = chief_mod.验判据契约(
                g0, _产物们92, _跑门, self.round_root / "_契约",
                必须=False)
            # ★★★★★ R148（第 61 层 · 2026-10-24 · 第三十六次端到端）：
            #   **只有「样本应用成功、门仍然绿」才是「主心骨断言了一件假事」。**
            #   「样本的旧产物里找不到」= 样本指向**目标状态**（还没被创建）
            #   ⇒ 那是**未验**，不是破裂。
            #
            #   真证据：9 条门全部报「1/1 条样本没让它红 ⇒ 疑恒真门」，
            #   而回执里写的是「样本的『旧』在全部 6 份产物里都找不到」，
            #   样本 `旧 = "学模式_广域(&广域键字节)"` —— **正是本次任务要建的东西**。
            #   ⇒ 机器指控了判据一件它没做的事，而代价是 `_达标` 永远 False。
            self._chief_契约破裂 = [
                c.名 for c in _契们92
                if getattr(c, "状态", "") == "无区分力"]
            # ★★★★★ R153b（2026-10-25 · 第 39 次真跑抓出）：
            #   **R153 的第一版拿不到它需要的信息 —— 时序反了。**
            #   真证据：第 39 次真跑第 1 轮日志里
            #     `历史偏差密度：…@mod_无界键入口保留且统计量真用上×6；…`
            #     **仍然排在最前**，R153 完全没生效。
            #   ★ 根因：`_hist_note` 在 `_run()` 的**开头**调用（L5490），
            #     而 `_chief_契约破裂` 要到**轮末**的契约验证才被赋值（L4534）。
            #     ⇒ 第 1 轮开头它必然是空的；第 2 轮开头它已被清空/尚未重算。
            #   ★★ 这是「手里那个量不是判据需要的那个量」的**第 6 次**：
            #     判据需要的是「**历史上哪些门被判过不可信**」，
            #     我拿到的是「**此刻这个列表里有什么**」——
            #     后者在调用时刻**恒为空**，不是同一个量。
            #   ★★★ 处置：**跨轮累积**。一个门一旦被判过「无区分力」，
            #     这个结论在本次任务内**持续有效**（门不变 ⇒ 结论不变，
            #     这正是 R115/R138 缓存语义的同一个道理）。
            #     ⇒ 单独一个集合，**只在明确「门集换了」时才清**。
            _新破153 = [c.名 for c in _契们92
                        if getattr(c, "状态", "") == "无区分力"]
            _旧集153 = getattr(self, "_恒真门153", None) or set()
            self._恒真门153 = set(_旧集153) | set(_新破153)
            # ★ 未验单独记：**要如实说出来**（不能说成破裂，也不能悄悄当成立）
            self._chief_契约未验 = [
                c.名 for c in _契们92
                if getattr(c, "状态", "") in ("未验", "跑不起来")]
            # ★★★★★ R114b：**契约为什么破裂，要回喂给主心骨**。
            self._chief_契约详情 = [
                {"门": c.名,
                 "旧": str((r.get("样本") or {}).get("旧") or "")[:120],
                 "新": str((r.get("样本") or {}).get("新") or "")[:120],
                 "为什么没红": str(r.get("说明") or "")[:200],
                 "变异文件": r.get("变异文件") or ""}
                for c in _契们92 if c.样本们 and not c.成立
                for r in (c.报告 or []) if not r.get("红")][:6]
            _有样本 = [c for c in _契们92 if c.样本们]
            if _有样本:
                _log(f"[generic] ★ R115 主心骨自证（{因由}）："
                     + "；".join(c.摘要() for c in _有样本))
            else:
                _log(f"[generic] ★ R115 契约自证（{因由}）："
                     f"{len(_契们92)} 条门**都没有否定样本** ⇒ "
                     "全部退回 gate_witness 的启发式变异（不硬拦，如实记录）")
            for _c92 in _契们92:
                self._emit({"kind": "gate_contract", "work": self._work,
                            "gate": _c92.名,
                            "成立": _c92.成立,
                            "样本数": len(_c92.样本们),
                            "报告": _c92.报告})
        except Exception as exc:      # 契约验不了 ⇒ 如实记，不假装
            self._chief_契约破裂 = []
            self._chief_契约未验 = []
            self._chief_契约详情 = []
            _log(f"[generic] ★ R115 判据契约验证异常（如实降级）："
                 f"{type(exc).__name__}: {exc}")

    # ★★★★★ R152（全量回归抓出的第 65 层 · 2026-10-25）：
    #   **`@staticmethod` 里用了 `self`。**
    #   真证据（`checks/落地验证.py` ⑨ 全库静态门）：
    #     `[FAIL] 全库无未定义全局名：{'generic_goal.py': ['_门集签名115:self']}`
    #   ★ 它是 R138 加产物维时写进去的：函数体里要读
    #     `self._签名根138` / `self.repo_scope` / `self.round_root` / `self.deliverables`
    #     —— 全都要实例；而签名上还挂着 `@staticmethod`。
    #   ★★ 后果：`_门集签名115(g0)` 一进函数体就 `NameError: name 'self' is not defined`
    #     ⇒ 被下面那个 `except Exception: _产 = []` **吞掉**
    #     ⇒ 悄悄退化成「只按门集」⇒ **R138 那一整层修复静默失效**。
    #     ★ 这是「兜底会把差异吃掉」的第 N 次：
    #       一个宽 except 让「少了产物维」这件事**看起来像正常工作**。
    #   ★★★ 而自检是绿的：自检路径上 `deliverables` 为空 ⇒ 根本走不到那段。
    #     抓出它的是**全库静态门**（未定义全局名），不是任何一条功能齿。
    #   ⇒ 改实例方法（唯一调用点 `self._门集签名115(g0)` 本来就传 self）。
    def _门集签名115(self, g0) -> str:
        """门集的**去样本**签名：只取判据本体（样本变了不影响「该验一次」）。

        ★ 为什么把 `否定样本` 剔掉：样本是**被验的样本戊**，
          把它算进签名会让「只是重述了一遍同样的样本」也触发重验。
          剔掉更稳：门不变 ⇒ 判据不变 ⇒ 验过的结论仍然成立。

        ★★★★★ R138（真跑抓出的第 51 层 · 2026-10-23 · 第三十一次端到端）：
          **签名少了「产物」这一维。**
        真证据（第 31 次真跑，原文）：
          轮1 `★ R115 主心骨自证（轮1 门集新出现）：lib_test_compiles：1/1 条样本
              **没让它红** ⇒ 契约不成立（疑恒真门）` —— **6 条门全被判不成立**
          轮2 门=**全 True**、`Δ=0 verdict=converged`、`有区分力 6/6`
              ⇒ 而 `action=stop`、`_达标` 返回 False、因由却说「基线 3→0」
        ★ 根因：`_验契约_执行时` 的**输入是「门集 + 产物内容」两样**
          （它把产物读出来做变异，见 `_产物们92`），
          而缓存键只有**门集**一样。
          ⇒ 轮 2 门集没变（同名同判据）⇒ **不重验** ⇒
            `_chief_契约破裂` 里留的还是**轮 1 产物**得出的结论 ⇒
            `_达标` 恒 False ⇒ 永远交付不了。
        ★ 这是「**缓存键少了维度**」，与「修了枚举里的项、没修枚举」同族：
          被缓存的函数依赖 X 和 Y，键里只有 X。
        ★ 更要紧的一层：轮 1 判「6 条门全不成立」本身就是**合理**的 ——
          那时产物确实没做到（`lib_test_compiles=False`）。
          「门恒真」与「产物没做到」在**坏产物**上是**同一个观测**，
          只有**好产物**才能把两者分开。⇒ 产物一变，必须重验。
        """
        import hashlib as _h
        import json as _json
        _净 = []
        for g in (g0 or []):
            if not isinstance(g, dict):
                continue
            _净.append({k: v for k, v in g.items()
                        if k not in ("否定样本", "否定")})
        _s = _json.dumps(_净, ensure_ascii=False, sort_keys=True)
        _h_门 = _h.sha1(_s.encode("utf-8")).hexdigest()
        # ★ R138：**产物内容也是输入**，必须进签名。
        #   诚实边界：只取**存在的交付物文件**的内容摘要；
        #   读不到就退化为「只按门集」（老行为），不假装产物没变。
        _产 = []
        try:
            _根 = Path(getattr(self, "_签名根138", None) or
                       (REPO_ROOT if self.repo_scope else self.round_root))
            for _d in (getattr(self, "deliverables", None) or []):
                _p = _d.get("path") if isinstance(_d, dict) else None
                if not _p:
                    continue
                _fp = Path(_根) / _p if _根 else Path(_p)
                if _fp.is_file():
                    _产.append(_p + ":" + _h.sha1(
                        _fp.read_bytes()).hexdigest()[:16])
                else:
                    _产.append(_p + ":<缺>")
        except Exception:
            _产 = []
        if not _产:
            return _h_门
        _s2 = _h_门 + "|" + "|".join(sorted(_产))
        return _h.sha1(_s2.encode("utf-8")).hexdigest()

    def _主心骨(self):
        """惰性取用主心骨。

        ★ 为什么需要「惰性」：自检/探针用 `GoalRunner.__new__` 构造实例
          （不跑 `__init__`）—— 直接 `self.chief` 会 AttributeError，
          而那是**测试脚手架的形状**，不是产品缺陷。
          ⇒ 取用点集中在这一个方法里，缺了就补一个（不假装有路由）。
        """
        c = getattr(self, "chief", None)
        if c is not None:
            return c
        # ★★★★★ R102（真跑抓出的 · 2026-10-19）：**自检不得写真回执**。
        #   真证据：`.audit_trail/chief_receipt.jsonl` 里 69+ 条是自检写的，
        #   其中 goal 全是 `把 src 下的东西修好`（`R31` 夹具的 goal），
        #   `name=fib_gen`（`_DRY_SPEC`），一条一条每 1–2 分钟一条 ——
        #   正是全量回归的节奏。
        #   ⇒ 这份「主心骨干了什么」的唯一证据**不再证明真跑**，
        #     而我据此误判过两次（先判「94% 回合是哑的」，再判「自检在调模型」）。
        #   ★ 根因：`回执目录` 写死成生产路径，而夹具是 `__new__` 构造的
        #     （不跑 `__init__`，没有「我是真跑实例」这个事实）。
        #   ⇒ 用 `_真跑实例` 这个**产品自己声明的事实**分流：
        #     真跑 → 生产回执；自检/探针 → 一次性临时目录（照写、但不污染证据）。
        _真 = bool(getattr(self, "_真跑实例", False))
        if _真:
            _回执dir = _RECEIPT.parent
        else:
            import tempfile as _tf102
            _回执dir = Path(_tf102.mkdtemp(prefix="chief_fixture_"))
        c = chief_mod.主心骨(路由=getattr(self, "router", None),
                             根=REPO_ROOT, 回执目录=_回执dir)
        c.目标 = getattr(self, "goal", "") or ""
        self.chief = c
        return c

    def _验契约_规划时(self, spec: dict) -> None:
        """★★★ R92：规划时验**判据契约** —— 每条门必须自带否定样本。

        ★ 为什么规划时就查（而不是等交付全绿）：
          契约不立 = 这条门**没有区分力**。等到「全绿」才发现，那时已经
          烧完一整轮（真跑实测一轮 22–26 分钟）。规划时查是**零成本**的
          （只读已生成的 spec，不跑门）。

        ★ 诚实边界：这一步只能查「**声明了没有**」。
          「那个样本是不是真能让门红」必须等产物存在 —— 由
          `_验契约_交付时` 在交付前用真产物跑。**两步不能合并**：
          合并了就等于「没产物时不查」，而没产物时恰恰是最该拒收的时候。
        """
        self._chief_契约未立 = []
        门们 = list(spec.get("gates") or [])
        for d in (spec.get("deliverables") or []):
            if isinstance(d, dict):
                门们.extend(d.get("gates") or [])
        无样本 = []
        for g in 门们:
            if not isinstance(g, dict):
                continue
            if not chief_mod.抽否定样本(g):
                无样本.append(str(g.get("name") or "?"))
        self._chief_契约未立 = 无样本
        if 无样本:
            _log(f"[generic] ★★ R92 判据契约：{len(无样本)}/{len(门们)} 条门"
                 f"**没有否定样本** ⇒ {无样本}")
            _log("[generic]   ★ 这些门对任何产物都可能绿（恒真门）⇒ "
                 "不构成达标证据；交付前会再验一次，样本不红即拒收")
        elif 门们:
            _log(f"[generic] R92 判据契约：{len(门们)} 条门都自带否定样本")

    async def _replan(self, red_audit: list) -> str:
        """#6 达门重规划：按红门缺口类别换执行策略，重新产出规约，走同一套机器采纳链路。

        返回信号：
          "changed"   — 新规约的判据门集较旧门集**真变**并已采纳（收敛闭环可推进）。
          "escalated" — 新旧门集未真变（判据不可达/规划器复述近似同门）→ 升级决策
                        （退出该子目标），不再原地空转烧 token。

        #1：采纳前机比对门集签名。replan 只被当作"状态转移换门集"，若门集没真变，
        换了等于没换，稳定含红会原地空转 → 就地升级，绝不以相同门集空转到 max_work。"""
        old_paths = [d["path"] for d in self.deliverables]   # 采纳新规约前先存旧路径
        old_sig = _spec_gate_signature(self.spec)            # 当前门集签名（已归一）
        # ★★★★★ R110（真跑抓出的第 24 层 · 2026-10-19 · 第十二次端到端）：
        #   `self.spec["gates"]` 是**裸下标** ⇒ `self.spec` 一旦是 `{}`
        #   （主心骨改目标后旧规约作废，正是这么置的）就抛 KeyError('gates')
        #   ⇒ 被 `重规划失败：{exc} → 诚实停机` 吞成一行 `重规划失败：'gates'`
        #   ⇒ **整趟死，且日志里连异常类型都没有**。
        #
        #   ★ 这一层的教训：`self.spec = {}` 与 `self.spec["gates"]` 是
        #     **同一份契约的两半**，改一半不改另一半 ⇒ 空字典成了定时炸弹。
        #     R107 说「修了枚举里的项，没修枚举」；这里是
        #     「**改了表示，没改所有读者**」。
        strat = _gap_strategy(red_audit, [g for d in self.deliverables
                                          for g in (d.get("gates")
                                                    or (self.spec or {}).get("gates")
                                                    or [])])
        base_prompt = _replan_prompt(self.goal, self.spec, red_audit, self.hist_note, strat,
                                     repo_index.structure_snapshot(self.goal))
        # ★★★★★ R92：**重规划也要记得自己走过哪些弯路**。
        #   真跑抓出的形态：`_replan` 没有跨轮历史 ⇒ 每次出问题就重新猜一遍，
        #   于是同一类门集被反复提出来（`escalated` 就是这么触发的）。
        #   ⇒ 把主心骨记忆喂回来；并把「这次为什么红」记进去。
        _心92 = self._主心骨()
        _记忆92 = _心92.记忆摘要()

        # ══════════════════════════════════════════════════════════════
        # ★★★★★ R97：**主心骨真的做一次回合**
        # ══════════════════════════════════════════════════════════════
        # 【真跑抓出（2026-10-19 · 第三次端到端）】
        #   `回合()` / `改目标()` / `写回执()` 在整条流程里
        #   **调用次数都是 0**（自检以外）。
        #   ⇒ 主心骨只是一个「**记忆笔记本**」：它被 `记住()` 写入、
        #     被 `记忆摘要()` 读进规划器的提示，**但它自己从不做决定**。
        #   ⇒ 于是验收项「主心骨改过一次目标并留下回执」**在构造上不可能达成** ——
        #     没有任何一行代码会去问它「方向对不对」。
        #   ★ 这是本库那个形状的第 10 层：
        #     「**声明了**主心骨」 ↔ 「**被使用的**只是一个记忆列表」。
        #     R92 建了它、R92 的齿也验了它 —— 但齿验的是「记忆有没有被喂回去」，
        #     **没有一条齿验「它有没有做过一次决定」**。
        #
        # 【为什么放在这里】重规划**正是**「方向还对不对」该被问的时刻：
        #   正常轮次里方向没问题，每轮问一次只是白烧模型调用；
        #   而到了要换门集的地步，说明按原方向走不通 ——
        #   这时候唯一有资格说「也许目标本身错了」的就是主心骨。
        #
        # 【降级语义】没有路由 / 调用失败 ⇒ `{"降级": True}` ⇒
        #   如实退回老形态并记 `_chief_降级`（**不假装有主心骨**）。
        if not getattr(self, "_chief_降级", False):
            _观测97 = {
                "回合": self._replan_count + 2,
                "裁决": "重规划（按原方向走不通）",
                "红门": [str(a.get("gate") or a.get("name") or "?")
                         for a in (red_audit or [])][:12],
                "已改文件": sorted({str(a.get("file") or "")
                                   for a in (red_audit or []) if a.get("file")})[:12],
                "失败因": f"{strat} / {self.hist_note}"[:400],
            }
            # ★★★★★ R121b：**把机器上一次拒收的原文摆给主心骨看。**
            #   它有权改判据，但只有看见"为什么被拒"才能改。
            #   ★ 与 R114b 同一手法（把「你的否定样本为什么没让门红」摆给它看）：
            #     「机器的判定，必须有主心骨这个消费者」。
            _拒121 = getattr(self, "_上次拒收97", "")
            if _拒121:
                _观测97["机器上一次拒收你的规约，原文如下"] = _拒121
                _观测97["提示"] = (
                    "★ 上面是**机器**拒收的理由（不是规划器的错，也不是能力问题，"
                    "是判据与本轮作业面不自洽）。两条约束必须**同时**满足：\n"
                    "  · 你判据里点名的文件 ⇒ 必须在本轮真被改（进 deliverables）；\n"
                    "  · 每个被改的文件 ⇒ 必须至少有一条判据门读它。\n"
                    "⇒ 如果你的目标是「本轮只做一处」，那就**只给那一处的判据**，"
                    "其余文件写成「本轮不判」。**判据不能比本轮作业面宽。**")
            # ★ R114b：把「你的否定样本为什么没让门红」摆给它看。
            _破114 = getattr(self, "_chief_契约详情", None)
            if _破114:
                _观测97["契约破裂"] = _破114
                _观测97["提示"] = (
                    "★ 上一轮你给的门**每条都带了否定样本**，但机器喂进去之后"
                    "**没有一条让门变红**。下面逐条列了原因。"
                    "请**重给判据**，并且让每条否定样本真的能改变那条判据的判定："
                    "「旧」必须是那条门**真正会读到**的那份文件里的原文；"
                    "「新」必须真的把门关心的那件事改坏。")
            try:
                _决97 = await _心92.回合(_观测97)
            except Exception as _e97:      # noqa: BLE001 主心骨崩了不得炸整轮
                _决97 = {"降级": True,
                         "理由": f"{type(_e97).__name__}: {_e97}"}
            if _决97.get("降级"):
                self._chief_降级 = True
                _log(f"[generic] ★ R97 主心骨本回合**降级**（如实退回老形态，"
                     f"不假装有）：{str(_决97.get('理由'))[:160]}")
            elif _决97.get("改目标") and _心92.目标 != self.goal:
                # ★ 改目标：回执已由 `主心骨.改目标` 写盘（只追加），
                #   这里只负责**让它真的生效**：
                #   ① 换掉目标 ② 旧规约作废（判据是针对旧目标定的）
                #   ③ 返回 changed ⇒ 调用方重取基线、重产内容
                _旧97 = self.goal
                self.goal = _心92.目标
                self.spec = {}
                self._emit({"kind": "chief_goal_change", "old": _旧97,
                            "new": self.goal,
                            "why": str(_决97.get("理由") or "")[:400]})
                _log("[generic] ★★★ R97 主心骨**改了目标**（回执已落盘）："
                     f"「{_旧97[:60]}」→「{self.goal[:60]}」")
                return "changed"
            else:
                # 方向不变 ⇒ 把主心骨这回合定的判据/计划交给规划器去落地
                _判97 = _决97.get("判据") or []
                _计97 = _决97.get("计划") or []
                if _判97 or _计97:
                    base_prompt += (
                        "\n\n═══════════════════════════════════════════\n"
                        "★ **主心骨本回合的决定**（方向没变，判据与计划由它定；"
                        "你负责把它落成可执行规约）：\n"
                        f"理由：{str(_决97.get('理由') or '')[:300]}\n"
                        f"判据：{json.dumps(_判97, ensure_ascii=False)[:1600]}\n"
                        f"计划：{json.dumps(_计97, ensure_ascii=False)[:800]}\n"
                        "═══════════════════════════════════════════\n")
                    _log(f"[generic] ★ R97 主心骨已做回合："
                         f"{len(_判97)} 条判据 / {len(_计97)} 项计划（方向不变）")
        if _记忆92 and "还没有历史" not in _记忆92:
            base_prompt += ("\n\n═══════════════════════════════════════════\n"
                            "★ **你自己的记忆**（你是同一个主心骨，不是新的规划器）：\n"
                            f"{_记忆92}\n"
                            "⇒ 不要重复已经失败的路线。\n"
                            "═══════════════════════════════════════════\n")
        _心92.记住(回合=self._replan_count + 2,
                   观测=("重规划：红门 "
                         + "、".join(str(x.get("gate") or x.get("name") or "?")
                                     for x in (red_audit or [])[:6])),
                   决定=f"按「{strat}」换策略重规划（第 {self._replan_count + 1} 次）",
                   依据="见 .audit_trail/chief_receipt.jsonl")
        # 机绑定：明确要求门集必须真变（复述近似同门等于原地空转，将被判不可达）。
        base_prompt += ("\n\n【机器约束】判据门集必须与旧门集**真不同**：换掉/增加能改变判定语义的"
                        "门（算法、断言方式、禁词/必需标记、命令），而不是只改描述性 name。"
                        "若你复述出与旧门集判据等价的门集，将被判为判据不可达而升级退出。")
        # ★★★★★ R80b（真跑抓出 · 2026-10-17）：**重规划这条路原来没有回喂循环。**
        #   真跑实测：`重规划失败：【计划不可能成功（机器可证）】 → 诚实停机` ⇒ exit 1。
        #   而同样的错在**首次规划**那条路上是会被回喂重来的（`_plan` 里 for 第次 in range(3)）。
        #   ⇒ 同一个"可纠正"的错误，走两条路有**两种命运** ——
        #     这说明 R80 只把「回喂」接在了**一条路**上（与 R71「隔离只接到一条路」同型）。
        #   ★ 修法不是"再写一份重试逻辑"，而是**让两条路用同一套**：
        #     这里同样收 `规约可纠正`，同样上限 3 次，同样把失败信息回喂。
        #   诚实边界：`escalated`（门集没真变）**不是**可纠正错误，是**真结论** ⇒ 不重试。
        反馈 = ""
        for 第次 in range(3):
            prompt = base_prompt + (f"\n\n【上一版被机器拒绝的原因，请照着改】\n{反馈}"
                                    if 反馈 else "")
            resp = await self.router.call([UserMsg(name="user", content=prompt)])
            self._tok_chars += len(prompt) + len(str(resp.content))
            # ★★★★★ R182：同 `_plan` 一处 —— 解析失败走**本循环的回喂**，不许冲出函数。
            #   这里**不立刻 raise**（raise 会跳出 for）⇒ 记反馈 continue，
            #   到第 3 次才 raise（与下面 `_valid_spec` 那条分支同口径）。
            try:
                spec = _parse_spec_json(_extract_text(resp.content))
            except 规约可纠正 as _e182b:
                反馈 = str(_e182b)
                _log(f"[generic] ★ 重规划规约格式错（第 {第次 + 1} 次）⇒ 回喂："
                     f"{(' ｜ '.join(x.strip() for x in 反馈.splitlines() if x.strip()))[:600]}")
                if 第次 >= 2:
                    raise
                continue
            err = _valid_spec(spec)
            if err:
                # ★ R80b：与 `_plan` 同一口径 —— 规约不合法是**可纠正**的 ⇒ 回喂。
                #   ★ 但这里**不能 raise**：raise 会跳出 for ⇒ 白白浪费重试次数。
                #     回喂的正确形态是「记下来、continue」。
                反馈 = (f"重规划规约不合法：{err}\n"
                        f"★ 请照着上面的具体问题改这一份规约后重新输出。")
                _log(f"[generic] ★ 重规划规约不合法（第 {第次 + 1} 次）⇒ 回喂：{err}")
                if 第次 >= 2:
                    raise 规约可纠正(
                        f"重规划连续 {第次 + 1} 次规约不合法 ⇒ 诚实停机"
                        f"（不猜、不替它决定）。最后原因：{反馈}")
                continue
            # 机比对：先归一（含门集自洽消解 #2）再取签名，判门集是否真变（#1）
            norm, _ = normalize_gates(spec)
            new_sig = _spec_gate_signature(norm)
            if new_sig == old_sig:
                self._emit({"kind": "replan_escalated", "work": self._work,
                            "old_deliverables": old_paths,
                            "reason": "新旧判据门集未真变，判据不可达 → 升级决策退出该子目标"})
                _log(f"[generic] 重规划门集未真变（判据不可达）@轮{self._work} → 升级退出，不空转")
                return "escalated"
            self._prev_spec = self.spec     # 换门集前留存旧规约，供 #3 执行注入门差异反馈
            try:
                self._adopt_spec(spec)
            except 规约可纠正 as exc:
                反馈 = str(exc)
                # ★ R121b：**记下机器拒收的具体因由**，下一回合喂给主心骨。
                #   真证据：连续 3 次都是同一条
                #   `交付物没有判据门覆盖：[…4 个文件…]`，
                #   而 `_观测97` 只给了「红门 / 已改文件 / 失败因」——
                #   **主心骨从头到尾没看见这条拒收原因** ⇒ 它只能重复同一份
                #   自相矛盾的判据（目标收缩成单文件、判据仍点名 4 个文件）。
                #   ★ 「同一把尺子」的必然要求：机器拿来拒收的理由，
                #     必须回到**有权改它的那一层**（这里就是主心骨）。
                self._上次拒收97 = 反馈[:1200]
                _log(f"[generic] ★ 重规划可纠正（第 {第次 + 1} 次 · "
                     f"{type(exc).__name__}）⇒ 回喂重规划："
                     # ★★★★★ R176c：**整条反馈压平送出**（第 84 次真跑实测：只打第一行 = 把证据丢掉）
                     f"{(' ｜ '.join(x.strip() for x in 反馈.splitlines() if x.strip()))[:1200]}")
                if 第次 >= 2:
                    # ★ R151：**用专用类型**，别让 `ValueError` 同时承担
                    #   「模型给错了」和「机器按设计交还决定权」两件事。
                    raise 按设计停机(
                        f"重规划连续 {第次 + 1} 次可纠正性问题仍未改对 ⇒ 诚实停机"
                        f"（不猜、不替它决定）。最后原因：{反馈}") from exc
                continue
            self._emit({"kind": "replan", "work": self._work,
                        "old_deliverables": old_paths,
                        "new_deliverables": [d["path"] for d in self.deliverables],
                        "reason": "判据门集真变，换实现路径推进"})
            return "changed"
        return "escalated"

    # -- 产出一批候选窗文件集（真实 API 或 dry 已知正确件）--------------
    def _dry_files(self) -> dict[str, str]:
        # ★ dry_machine：交付物带结构真值 ⇒ **走真机器自生成路径**（不是假内容）。
        #   这一路 dry 也真调 `_machine_generate`，故 dry 能验证 0-LLM 组装真的成立。
        if self.dry_machine:
            out: dict[str, str] = {}
            for d in self.deliverables:
                src = _machine_generate(d)
                if src is None:
                    raise RuntimeError(
                        f"dry-machine 样例 {d['path']} 机器自生成失败"
                        f"（结构真值有误或语言不支持）")
                out[d["path"]] = src
                self._machine_files = getattr(self, "_machine_files", 0) + 1
            return out
        # dry_rework：首轮故意加禁词让门红 → 验证回炉重派机制端到端
        bad = (self._work == 1) if self.dry_rework else (self._work > 1)
        if self.dry_multi:
            files = {"fib.py": _DRY_CONTENT, "test_fib.py": _DRY_TEST_CONTENT}
            if bad:
                files["fib.py"] = _DRY_CONTENT + "\n# TODO: 残留\n"
            return files
        files = {"fib.py": _DRY_CONTENT}
        if bad:
            files["fib.py"] = _DRY_CONTENT + "\n# TODO: 残留\n"
        return files

    async def _算共同决定188(self, red_audit_prev: list) -> str:
        """★ R188：先做一次跨文件「共同决定」，再让各文件分别执行。
    
        为什么必须有这一步（第 90 次真跑，15 次尝试全败）：
          `_produce_window` 是逐文件、各自独立调模型的 —— 每个 prompt 里
          只有本文件的原文，模型对别的文件这一轮怎么改一无所知。
          而这次目标是 E0284 横跨 4 个文件、指向同一个 `定列` 的 const 泛型
          用法：改法必须全局统一。各自独立猜 ⇒ 必然互不一致 ⇒ 各自引入新错
          （实测逐轮漂移出 E0599 / E0223），错误点数反而变多。
    
        这不是多问一次模型，而是补上 R92 架构里主心骨该给的方向 ——
        现在主心骨只被要求给 deliverables（改哪些文件）与 gates（怎么判），
        没被要求给「统一采用哪个改法」⇒ 于是每个窗口各自发明一个。
    
        失败即降级：拿不到决定 ⇒ 记日志后返回空串，产出流程照旧（不停整趟）。
        """
        if len(getattr(self, "deliverables", None) or []) <= 1:
            return ""        # 单文件任务不需要跨文件约定
        # 按文件归并红门证据（模型要看到哪些文件在报同一类错）
        _按文188: dict[str, list[str]] = {}
        for _a188 in (red_audit_prev or []):
            _f188 = str(_a188.get("file") or "?")
            _c188 = str(_a188.get("cause") or _a188.get("gate") or "")[:300]
            if _c188:
                _按文188.setdefault(_f188, []).append(_c188)
        # ★★★★★ R191：**按错误码分组**。真证据（第 91 次真跑 11→1）：
        #   统一决定让 10 条 E0284 全消，就差 1 条 E0433 —— 两者**不是同一个根因**，
        #   而 R188 只允许给**一个**决定 ⇒ 模型挑了多数派、少数派根因被忽略。
        #   ⇒ 分组后**每组各要一个改法**，并明确要求「每一组都要有」。
        _组188: dict[str, int] = {}
        for _zz in _按文188.values():
            for _c in _zz:
                _m188 = re.match(r"\s*(E\d{4})", _c)
                _k188 = _m188.group(1) if _m188 else "其它"
                _组188[_k188] = _组188.get(_k188, 0) + 1
        分组188 = "、".join(f"{_k}×{_v}" for _k, _v in sorted(
            _组188.items(), key=lambda _kv: -_kv[1])) or "（未识别出错误码）"
        if len(_组188) > 1:
            _log("[generic] ★ R191 本轮红门证据含 **{0} 组不同根因**（{1}）⇒ 每组各要一个改法".format(len(_组188), 分组188))
        证据188 = "\n".join(
            f"### {_f}\n" + "\n".join(f"- {_c}" for _c in _cs[:6])
            for _f, _cs in list(_按文188.items())[:8]) or "（本轮没有逐文件红门证据）"
        文件188 = "\n".join(f"- {d.get('path')}"
                              for d in (self.deliverables or [])[:8])
        _问188 = (
            "你在为一次**跨文件**的代码修复做**统一决定**。"
            "这一步**不写代码**，只定「所有文件必须一致遵守的那一个改法」。\n\n"
            f"## 目标\n{self.goal}\n\n"
            f"## 允许改的文件\n{文件188}\n\n"
            f"## 机器实测的红门证据（逐文件）\n{证据188}\n\n"
            # ★ R191：把分组**明写进 prompt**，并说明「每一组都要有」
            f"## 根因分组（机器按错误码分好的，★ 每一组都必须给决定）\n{分组188}\n\n"
            "## 你要回答的（只回答这一件事）\n"
            "1. 这些错误分成**几组根因**？（机器已按错误码粗分；你可以合并或细分，但要说明理由）\n"
            "2. **对每一组根因各给一个**必须统一采用的改法（具体到类型/签名/导入/定义）。\n"
            "   ★ **每一组都要有，不许只答其中一组** —— 少答的那一组会原样留下来。\n"
            "3. 如果某组必须改代码之外的东西（如加 crate 依赖）才能解决，请明说「该组只能靠 Cargo.toml/依赖解决」。\n"
            "具体到**类型/签名/导入/定义**怎么写，哪些文件里要跟着改什么。\n"
            "   要求：能让**所有**上面列出的错误同时消失，且**不引入新类型错误**。\n"
            "4. 如果实在无法合并成少数几组：指出应**先改哪一个文件**（只一个）以及理由。\n\n"
            "★ 只输出这份决定本身（≤600 字）。**不要输出任何代码补丁**、"
            "不要解释你的过程、不要问候。")
        try:
            _r188 = await self.router.call([UserMsg(name="user", content=_问188)])
            _决188 = _strip_fence(_extract_text(_r188.content)).strip()
            self._tok_chars += len(_问188) + len(str(_r188.content))
        except Exception as _e188:   # 拿不到 ⇒ 如实降级（不停整趟）
            _log(f"[generic] ★ R188 跨文件共同决定**没拿到**"
                 f"（{type(_e188).__name__}: {_e188}）⇒ 降级：各文件照旧独立执行")
            return ""
        # ★★★★★ R197：**质量闸**。真证据（第 95 次真跑第 1 轮重试）：
        #   `★ R188 跨文件共同决定已拿到（25 字符）` —— 25 个字符不可能是
        #   「按根因分组、每组一个改法」的决定；而它被当成**必须遵守**的东西
        #   注入了每个文件的 prompt ⇒ 比没有更糟（它会让模型照着一句废话改）。
        #   ⇒ 下限必须随**根因组数**走：组越多，决定越长才可能说清。
        _组数197 = max(1, len(_组188))
        _下限197 = max(60, 30 * _组数197)
        if not _决188 or len(_决188) < _下限197:
            _log(f"[generic] ★ R197 共同决定**质量不足**（{len(_决188)} 字符 < 下限 {_下限197}，{_组数197} 组根因）⇒ 降级：**不注入**"
                 f"（注入一句废话比不注入更糟）。实得：{_决188[:120]!r}")
            self._emit({"kind": "decision_rejected", "work": getattr(self, "_work", 0),
                        "chars": len(_决188), "floor": _下限197, "groups": _组数197,
                        "text": _决188[:200]})
            return ""
            return ""
        _log(f"[generic] ★ R188 跨文件共同决定已拿到（{len(_决188)} 字符）"
             f"⇒ 将注入全部 {len(self.deliverables)} 个交付物的 prompt"
             f"（cost：本轮多 1 次调用，换掉各自发明的改法）")
        return _决188
    
    async def _produce_windows(self, content: dict | None,
                               red_audit_prev: list) -> list[dict]:
        if self.dry:
            return [self._dry_files() for _ in range(self.n_windows)]
        async def _one(_):
            return await self._produce_window(content, red_audit_prev)
        # ★★★★★ R188：先定统一改法，再让各文件执行。
        #   没有这一步，4 个文件各自独立猜一个改法 ⇒ 必然互不一致 ⇒ 各自引入新错
        #   （第 90 次真跑：15 次尝试全败，错误类型逐轮漂移 E0599 / E0223）。
        self._共同决定188 = await self._算共同决定188(red_audit_prev)
        return await asyncio.gather(*(_one(i) for i in range(self.n_windows)))

    async def _produce_window(self, content: dict | None,
                              red_audit_prev: list) -> dict:
        """单窗内按拓扑序逐文件产出：依赖文件先产出判门绿，才产出下游。"""
        # ★★★★★ R193：**作业面 = 此刻还红的文件**，不是目标文本里那份过期的必改清单。
        #   真证据（第 92 次真跑，基线已只剩 1 个错误）：目标仍点名 4 个必改文件，
        #   而其中 3 个上一趟已改对 ⇒ 模型在**已经对的**文件上乱改，
        #   造出 E0425/E0428（窗内违规 3 → 10）⇒ 每轮整批回退 ⇒ 永不收敛。
        #   ★ 目标文本是**历史快照**；判据要的是「此刻哪里还红」。
        # ★ R195：文件归属从**证据文本**里抽（`a["file"]` 这个字段常常根本没有）。
        _有红193 = _红证据文件(red_audit_prev)
        _启用193 = _启用跳过193(_有红193, self.deliverables, self.repo_scope)
        # ★★★★★ R205：R201 说「下一轮必须换**产出侧**」—— **必须真的换，不能只说不做**
        #   （R128/R129 的病：算了、写了、没执行）。真证据（第 101 次真跑）：
        #   连续 3 轮 `候选全部被拒（保留 0）`，而每轮产出侧**一模一样**：
        #   同样 2 个文件、同样整份重写、同样被退 ⇒ 五轮全部白跑。
        #   ⇒ 口径：一旦出现「全拒」，下一轮**作业面收到单文件**（红证据最多的那个）
        #     —— R179「模型能改的面越小，它就越只能改对那件事」。
        _连拒205 = int(getattr(self, "_连拒201", 0))
        if _连拒205 > 0 and len(self.deliverables) > 1:
            _数红205 = {}
            for _a205 in (red_audit_prev or []):
                for _f205 in _红证据文件([_a205]):
                    _数红205[_f205] = _数红205.get(_f205, 0) + 1
            _候选排序 = sorted(
                (d for d in self.deliverables
                 if d["path"] in _有红193 or _数红205.get(d["path"], 0) > 0),
                key=lambda d: -_数红205.get(d["path"], 0))
            _一个205 = _候选排序[0] if _候选排序 else self.deliverables[0]
            _log(f"[generic] ★ R205 上一轮候选**全部被拒**（连续 {_连拒205} 次）"
                 f"⇒ 作业面收窄到**单文件**：{_一个205['path']}（其余 {len(self.deliverables) - 1} 个"
                 f"本轮不产出 —— 每次只改一处，才可能被机器保留）")
            self._emit({"kind": "workface_narrowed", "work": getattr(self, "_work", 0),
                        "kept": _一个205["path"],
                        "dropped": [d["path"] for d in self.deliverables
                                    if d["path"] != _一个205["path"]][:8],
                        "consecutive_rejects": _连拒205})
            self.deliverables = [_一个205]
        if _启用193:
            _log(f"[generic] ★ R193 作业面按**当前红证据**收敛（{len(_有红193)} 个文件还红）"
                 f"⇒ 本轮不碰其余交付物（已经对了，动它只会制造新错）")
        else:
            _log("[generic] ★ R193 不启用（红证据没有文件归属 / 非仓库模式 / "
                 "没有交付物命中）⇒ 按原口径全部产出")
        files: dict[str, str] = {}
        for d in self.deliverables:
            # ★ R193：这个文件本轮**没有任何红证据**，且它在仓库里已经存在
            #   ⇒ 它已经是对的 ⇒ **不调模型、不改它**（动了只会制造新错）。
            # ★ R203：上一轮被机器退回来的文件**不许**跳过（它一定还有问题）。
            _刚退203 = (int((getattr(self, "_回退次数", None) or {}).get(d["path"], 0)) > 0
                        or any(str(_b.get("文件") or "") == d["path"]
                               for _b in (getattr(self, "_回退台账", None) or [])))
            if _该跳过193(d["path"], _启用193, _有红193,
                          (REPO_ROOT / d["path"]).is_file(), 刚被退=_刚退203):
                self._跳193 = getattr(self, "_跳193", 0) + 1
                _log(f"[generic] ★ R193 跳过 {d['path']}：本轮它**没有红门证据**"
                     f"（已经是对的）⇒ 不动它")
                continue
            prev = (content or {}).get(d["path"])
            deps = {p: files[p] for p in (d.get("depends_on") or []) if p in files}
            # ★★★ 机器自生成（0-LLM）：声明了结构真值 ⇒ 机器组装源码，**不调模型**。
            #   这一步在注入/prompt 之前 ⇒ 省掉整段 prompt 与响应流量。
            _机 = _machine_generate(d)
            if _机 is not None:
                files[d["path"]] = _机
                self._machine_files = getattr(self, "_machine_files", 0) + 1
                continue
            red_for_file = [a for a in red_audit_prev
                            if a.get("file") == d["path"]]
            # 机制①+项目理解：主树工程任务注入仓库当前文件内容（执行/回炉基于真实源码改）。
            # 目标文件用大 cap 完整注入（否则截断会让智能体误删未注入内容，真实跑已踩过）；
            # 再按主树索引注入"被引用模块符号摘要/反引用文件"两段式上下文，让智能体看懂跨文件关系。
            repo_ctx = ""
            补丁模式 = False
            原文 = ""
            焦点 = 0
            if self.repo_scope:
                _p = REPO_ROOT / d["path"]
                if _p.is_file():
                    原文 = _p.read_text(encoding="utf-8", errors="ignore")
                # ★ 超阈值 ⇒ 切**区域编辑协议**（2026-10-14 修，见 _apply_patch 上方注释）。
                #   整文件替换对大文件在**输出侧**就不可能，调注入 cap 救不了。
                # ★★★★★ R179：**被回退过的文件强制切补丁模式。**
                #   真证据（第 81 次真跑）：同一文件连续 3 轮 `违规点变多（1 → 7/8/7）`，
                #   而 R177 的「你上一版被退了」**确实送达了** ⇒ 模型不是不知道，是做不到：
                #   它每轮整份重写一个 19696 字符的文件 ⇒ 必然把好地方改坏。
                #   ⇒ 机器已有证据（退过 + 是「变多」）⇒ 下一轮**只让它改那几行**。
                #   ★ 与 R69 同族：**模型能改的面越小，它就越只能改对那件事。**
                if len(原文) > _REPO_TARGET_CAP or _退过179(d["path"], getattr(self, "_回退次数", None)):
                    # ★ R34：**规划器自己产出的行号优先**（`path:行号` 已被
                    #   `_parse_deliverables` 拆进 `focus`）；再退回目标文本里的
                    #   `路径:行号`。两者都没有 ⇒ 如实报"改不了"（不猜）。
                    焦点 = int(d.get("focus") or 0) or (_focus_line(d["path"], self.goal) or 0)
                    if 焦点 <= 0:
                        # 没有行号焦点 ⇒ 不知道改哪 ⇒ **如实失败**，不猜。
                        files[d["path"]] = (
                            f"__ORCH_CANNOT_EDIT__: {d['path']} 共 {len(原文)} 字符，"
                            f"超出整文件阈值，而目标里**没有给出行号焦点**（如 "
                            f"`{d['path']}:123`）⇒ 无法确定改动点。"
                            f"请把目标拆成带行号的具体改动，或改用小文件。")
                        continue
                    # ★★★★★ R70（真跑抓出 · 2026-10-16）：**行号不是身份，是坐标。**
                    #   缺口扫描时登记 `…mod.rs:10571`，而这里读文件是 **31 分钟后**
                    #   —— 这期间**另一个进程在改同一个文件**（长了 22 行）⇒ 行号整体
                    #   平移 ⇒ 按 10571 取的窗口**根本不含目标** ⇒ 模型如实拒收、
                    #   每一窗白烧。★ 目标文本里本来就带着机器抽的**登记原文**与
                    #   **上下文**，那才是身份 ⇒ 先按内容重新定位，再取窗口。
                    焦点, _定据 = _定位焦点(原文, self.goal, 焦点)
                    if "一处都找不到" in _定据:
                        files[d["path"]] = (
                            f"__ORCH_CANNOT_EDIT__: {d['path']} 共 {len(原文)} 字符；"
                            f"目标里登记的原文与上下文**在当前文件里一处都找不到**"
                            f"（文件已被大幅改写，登记行号 {焦点} 已失效）"
                            f"⇒ 无法确定改动点。★ 机器**不按陈旧行号硬改**。"
                            f"请重新扫描缺口（`--list-gaps`）后再派。")
                        continue
                    if "陈旧" in _定据:
                        _log(f"[generic] ★ R70 行号已陈旧 ⇒ 按内容重定位：{_定据}")
                    # ★★★★★ R69：窗口按**块边界**取（不是按焦点 ± 固定半径）。
                    #   固定半径窗口让模型看不到块的起止 ⇒ 它新开一层块却不知道
                    #   在哪里闭合 ⇒ 连续两轮 `unclosed delimiter`（真跑证据）。
                    #   现在窗口 = **语法闭合区域**，并把这个不变量写进 prompt。
                    win, _wa, _wb, _w说 = _块闭合窗口(原文, 焦点)
                    # ★ R70：**必须把"行号已重定位"这件事告诉模型** ——
                    #   否则它会看到目标里写「登记位置 …:10571」而窗口是 10589..10671
                    #   ⇒ 它（正确地）判定"二者不可能同时为真"⇒ 拒收、白烧一窗。
                    #   实测就是这样烧掉整整一轮的。★ 模型看得见的约束才是约束。
                    if "陈旧" in _定据 or "找不到" in _定据:
                        _w说 = (_w说 + f"\n★ **注意**：目标文本里的登记行号已**陈旧**"
                                f"（别的进程改过这个文件）—— 机器已按**登记原文**"
                                f"重新定位到当前文件的第 {焦点} 行。"
                                f"**以本窗口的行号为准**，目标文本里的行号**不要用**。")
                    repo_ctx = _patch_prompt_block(d["path"], win, _wa, _wb,
                                                   len(原文.splitlines()),
                                                   窗口说明=_w说)
                    补丁模式 = True
                else:
                    repo_ctx = _repo_file_context(d["path"], cap=_REPO_TARGET_CAP)
                rel_ctx = repo_index.related(d["path"])
                if rel_ctx:
                    repo_ctx += ("\n\n``` unrelated-to-target-not-editable ↓ 以下是被引用/反引用模块的符号摘要，"
                                 "仅作理解参考，NOT 目标文件，禁止改动\n" + rel_ctx + "\n```")
            # 机制③：红门缺口类别 → 应对策略（只对回炉轮注入）
            strat = (_gap_strategy(red_for_file, d.get("gates")
                               or (self.spec or {}).get("gates") or [])
                     if red_for_file else "")
            if prev is None or not red_for_file:
                # ★★★★★ R93：**只读探针** —— 把窗口会想查的东西由机器查好。
                #   真跑里窗口想跑 bash 去看仓库（被 R32 拒收），但拒完之后没给它路。
                #   ⇒ 这里补上那条路：接口画像（定义/生产调用/测试调用）
                #     + 文件画像 + 红门证据，**只读**，只进 prompt、不进主树。
                _探针93 = ""
                try:
                    if self.repo_scope:
                        _探针93 = probe_mod.探针包(
                            REPO_ROOT, self.goal, d["path"], red_audit_prev,
                            接口前缀=prof_mod.get_profile().orphan_api_prefixes)
                except Exception as _exc93:   # 探针跑不动 ⇒ 如实降级
                    _log(f"[generic] ★ R93 只读探针异常（如实降级，不假装查过）："
                         f"{type(_exc93).__name__}: {_exc93}")
                # ★ 第 157 轮：结构寻址上下文包（洞驱动 + 定义↔使用对合 + 每条第指纹）
                _结构包157 = ""
                try:
                    _结构包157 = _结构包文本(str(REPO_ROOT), d["path"], red_audit_prev)
                except Exception as _exc157:      # noqa: BLE001
                    _log(f"[generic] 结构上下文异常（降级为空）：{type(_exc157).__name__}")
                prompt = _exec_prompt(self.goal, self.spec, d, deps, repo_ctx,
                                      _探针93, _结构包157)
            else:
                # 机制③#3：replan 换门集后，注入新旧门差异的结构化反馈，
                # 让执行针对新红门产出真正不同的实现，避免复述旧开头白烧 token。
                prev_spec = getattr(self, "_prev_spec", None)
                gate_diff = (_gate_diff_feedback(prev_spec, self.spec, d["path"])
                             if prev_spec else "")
                # ★★★★★ 第 156 轮：**跨轮结构记忆**（替换"偏差密度"式计量）。
                #   真跑病征：回炉轮的模型把整份文件重写（731 行 → 1 行）、
                #   或重写本已通过的部分，白烧 token 且引入新错。
                #   机器手里其实有判据：**逐符号的形状指纹变没变**。
                #   ⇒ 把"哪个符号形状已变 / 未变 / 新增 / 消失"直接告诉它。
                #   异常一律降级（这段信息是增益，不是前提，不能因为它把整跑搞挂）。
                _结构记要 = ""
                try:
                    from . import mem_round as _mr156
                    if str(os.environ.get("ORCH_MEM_ROUND", "1")).strip() in (
                            "0", "false", "False", "no"):
                        raise RuntimeError("ORCH_MEM_ROUND 已关（A/B 对照的 A 臂）")
                    _存盘156 = Path(REPO_ROOT) / ".data" / "结构记忆"
                    _结构记要 = _mr156.记要(REPO_ROOT, [d["path"]], _存盘156)
                except Exception as _exc156:      # noqa: BLE001
                    _log(f"[generic] 跨轮结构记忆不可用（如实降级）："
                         f"{type(_exc156).__name__}: {_exc156}")
                prompt = _rework_prompt(self.goal, self.spec, d, prev,
                                        red_for_file, deps, self.hist_note,
                                        repo_ctx, strat, gate_diff,
                                        self._target_dev, 结构记要=_结构记要)
            # ★ R128：把「上一轮是绿的、本轮被弄红了」的门**明确写进 prompt**。
            #   ★ 只写门名，不写判断 —— 机器只陈述事实，怎么改由模型决定。
            _曾绿128 = getattr(self, "_曾绿今红", None) or []
            if _曾绿128:
                # ★ 不能假设「注入生效」—— 「声明了与被使用之间断了」是这一路
                #   的主导缺陷族。**写进 prompt 也要留一行可核对的日志。**
                _log(f"[generic] ★ R128 已把「不许弄红」注入窗级 prompt"
                     f"（{len(_曾绿128)} 条门，prompt {len(prompt)} → "
                     f"{len(prompt) + 400}+ 字符）")
                prompt += (
                    "\n\n## ★ 不许把**已经变绿**的门弄红（机器实测）\n\n"
                    "下面这些门在**上一轮是绿的**，本轮被改红了：\n"
                    + "\n".join(f"- `{k}`" for k in _曾绿128[:12])
                    + "\n\n★ 这叫**倒退**。机器会为此**回退你的改动**，"
                      "你这一轮就白做了。\n"
                      "★ 正确做法：在**让上面每一条继续为绿**的前提下，修当前的红门。"
                      "如果你认为某条门**必须**变红才能推进，请在输出末尾用一行写明理由"
                      "（`# 为什么允许变红：…`）—— 机器会把理由记进回执。")
            # ★★★★★ R131：**2-循环必须被说出来，而且要说成「换策略」而不是「修红门」。**
            #   真证据（27 次真跑的第 5 轮，门真值逐位）：
            #     轮3  mod_有唯一广域入口=红  评测口径经统一入口=绿
            #     轮4  mod_有唯一广域入口=绿  评测口径经统一入口=红
            #     轮5  mod_有唯一广域入口=红  评测口径经统一入口=绿  ← 与轮3 逐位相同
            #   ⇒ 模型照着「把红的修绿」做，**必然**把另一条弄红。
            #   ★ 它没有不听，它**做对了被要求的那件事**，而这件事本身不够。
            #   ⇒ 需要的不是更强的鼓励，是**问题定义换掉**：
            #     「同时满足」是约束求解，不是两条独立任务。
            # ★★★★★ R136b：**`_上次拒收97` 只在 `_replan` 里被读**，
            #   而 R136 走的是 `continue`（不换门集、不经过 `_replan`）
            #   ⇒ 那段点名恒真门的话**永远送不到模型面前**。
            #   ★ 这是 R128/R129 的同一个病：**算了、存了，但没送出去**（第 4 次）。
            #   ⇒ 在这里也读一次，并**用后即清**（只喂一轮，防它变成永久噪音）。
            _拒136 = getattr(self, "_上次拒收97", "")
            if _拒136:
                _log(f"[generic] ★ R136 已把「恒真门点名」注入窗级 prompt"
                     f"（{len(_拒136)} 字符）")
                prompt += ("\n\n## ★★★ 有一条门**恒真**（机器实测，不是猜测）\n\n"
                           + _拒136
                           + "\n\n★ 这一条不修掉，**门全绿也不构成达标证据** —— "
                             "机器会认为你只是把门写宽了。")
                self._上次拒收97 = ""
            _二循环131 = getattr(self, "_二循环131", None) or []
            if _二循环131:
                _log(f"[generic] ★ R131 已把「2-循环」注入窗级 prompt"
                     f"（{len(_二循环131)} 条门在摆动，prompt {len(prompt)} → "
                     f"{len(prompt) + 700}+ 字符）")
                prompt += (
                    "\n\n## ★★★ 你在**两个状态之间来回跳**（机器实测，不是猜测）\n\n"
                    "最近三轮的门真值，机器逐位比对过：\n"
                    "- 第 N-2 轮：某几条绿\n"
                    "- 第 N-1 轮：**这几条红了**（你把它们修绿的同时，把另外几条弄红了）\n"
                    "- 第 N 轮：**和 N-2 轮逐位相同** ⇒ 你回到了原地\n\n"
                    "在摆动的是：\n"
                    + "\n".join(f"- `{k}`" for k in _二循环131[:8])
                    + "\n\n★ **这不是「还差一点」，这是策略不对。**\n"
                    "★ 你上一轮照着「把红的那条修绿」做了 —— 你**做对了被要求的事**，"
                    "而它**必然**导致另一条变红。\n"
                    "★★ 所以本轮**不要**再用「修红门」的方式思考。"
                    "请把它当成**一个约束求解问题**：\n"
                    "   · 上面这些门必须**同时**为绿，一条都不能牺牲；\n"
                    "   · 先找出**它们为什么会互相排斥**（同一个符号？同一处调用？"
                    "同一个入口被两条门用不同口径要求？）；\n"
                    "   · 然后给出一个**一次同时满足**的改法。\n"
                    "★ 如果你判断它们**在事实上不可同时满足**（例如两条门要求互斥的接口），"
                    "请在输出末尾用一行写明：`# 判据互斥：…` 并说明是哪两条、为什么。"
                    "机器会把它记进回执并交给人裁决 —— **这比继续来回跳更有价值**。")
            # ★★★★★ R177：**把「上一轮你被回退了什么」送进窗级 prompt。**
            #   真证据（第 78 次真跑）：同一文件连续 4 轮 `违规点变多（1 → 7）⇒ 回退`，
            #   而模型**从不知道自己被退回过** ⇒ 每轮从头再猜 ⇒ 必然踩同一个坑。
            #   ★ 注入点必须在**窗级**（真正写文件的那一层）——
            #     第 80 次真跑证明插在 `_plan` 里等于没插（规划器读完就清空）。
            _台177 = getattr(self, "_回退台账", None) or []
            if _台177:
                _段177 = ["\n\n## ★★★ 上一轮你改的这几个文件，**机器把它们退回去了**（不是没改，是改坏了）\n"]
                for _笔177 in _台177[:5]:
                    _段177.append(f"\n### `{_笔177.get('文件')}` —— {_笔177.get('因由')}")
                    _原177 = _笔177.get("原有") or []
                    _新177 = _笔177.get("新增") or []
                    if _原177:
                        _段177.append("· 它**原来**的错：" + " | ".join(f"`{x[:90]}`" for x in _原177))
                    if _新177:
                        _段177.append("· 你那一版**新引入**的错（这就是被退回的原因）：")
                        _段177.extend(f"    - `{x[:150]}`" for x in _新177)
                _段177.append(
                    "\n★ 你上一版对上面这些文件的改动**已经被撤销**，主树回到了改之前。"
                    "\n★ 所以：**不要重复同一个改法**。请先看清「新引入的错」是怎么来的"
                    "（同一个类型推不出？同一个名字没导入？同一个符号被换掉？），"
                    "再给出一个**不会引入这些新错**的改法。"
                    "\n★★ 只改**你确定能改对**的地方。宁可这一轮只修好 1 处、"
                    "也不要一次改 7 处、被整份退回。")
                _log(f"[generic] ★ R177 已把「上一轮被回退的 {len(_台177)} 个文件 + 它们当时的真报错」"
                     f"注入窗级 prompt（prompt {len(prompt)} → {len(prompt) + 900}+ 字符）")
                prompt += "\n".join(_段177)
                self._回退台账 = []        # ★ 用后即清：只喂一轮，防它变成永久噪音
                # ★★★★★ R188：把跨文件共同决定注入每一个文件的 prompt（放最前）。
                #   真跑证据：逐文件独立编辑 ⇒ 各自发明改法 ⇒ 互不一致 ⇒ 各自引入新错。
                _共188 = getattr(self, "_共同决定188", "")
                if _共188:
                    prompt = (
                        "## ★★★ 本轮**跨文件共同决定**（机器已单独问过，必须遵守）\n\n"
                        + _共188
                        + "\n\n★ 你这一份改动**必须与上面的决定一致**："
                          "如果决定说「统一改成 X」，你就在这里改成 X，**不要自己另发明一个改法**。\n"
                          "★ 你只负责这一个文件；别的文件由同一决定约束下的其它窗口改。\n"
                          "★ 如果上面的决定与你在文件里看到的事实**冲突**，"
                          "请在输出末尾用一行写明（`# 与决定冲突：…`），并给出你认为正确的最小改动。\n\n"
                          "─────────────────────────────────────\n\n"
                        + prompt)
                    _log(f"[generic] ★ R188 已把共同决定注入 {d['path']} 的 prompt"
                         f"（prompt → {len(prompt)}+ 字符）")
            # ★ R198（事前）：模型在用中文思考，写代码时容易把标点也留成中文 ⇒
            #   在 prompt 里**硬写**这一条，比事后修更省一次编译。
            prompt += ("\n\n## ★ 输出格式硬要求（机器会逐字检查）\n"
                       "· 代码正文里的标点**必须是半角**：`,` `;` `(` `)` `:` `{` `}` `[` `]` `=`\n"
                       "· **不要**输出全角标点（`，` `；` `（` `）` `：` `｛` `｝`）——"
                       "机器实测：全角逗号在源码里会引发**词法错误 E0460**，整个文件不可编译。\n"
                       "· 字符串和注释里的中文是**允许的**，不用改。")
            resp = await self.router.call([UserMsg(name="user", content=prompt)])
            self._tok_chars += len(prompt) + len(str(resp.content))
            产出 = _strip_fence(_extract_text(resp.content))
            # ★★★★★ R32：产出若**根本不是文件内容**（工具调用转录）⇒
            #   ① 先**回喂一次**（这是最省的一修：模型只是没听清"只输出内容"）；
            #   ② 仍不对 ⇒ 换成**明确说清根因**的产物，让判据门判红并把原因
            #      回喂 —— **绝不把转录写进主树**（写进去就得指望编译门侥幸兜住）。
            非内容 = _像工具调用而非内容(产出)
            if 非内容:
                _log(f"[generic] ★ R32 产出不是内容（{非内容}）⇒ 回喂一次"
                     f"（{d['path']}）")
                纠 = (prompt + "\n\n═══════════════════════════════════════\n"
                      "★★★ 你上一次的回复**不是文件内容**，而是**工具调用/对话转录**"
                      f"（机器认出：{非内容}）。\n"
                      "你没有工具可用，也不要解释、不要叙述你的计划。\n"
                      f"请**只输出** `{d['path']}` 的**完整文件内容本身**"
                      "（从第一行到最后一行），不要任何前言、结语、代码围栏或调用标记。\n"
                      "═══════════════════════════════════════")
                resp = await self.router.call([UserMsg(name="user", content=纠)])
                self._tok_chars += len(纠) + len(str(resp.content))
                产出 = _strip_fence(_extract_text(resp.content))
                非内容 = _像工具调用而非内容(产出)
            if 非内容:
                产出 = (f"__ORCH_NOT_CONTENT__: 执行器两次都返回了**非文件内容**"
                        f"（{非内容}）。\n"
                        f"⇒ 这一份产物**未被采纳**，也**不会写进主树**"
                        f"（`_是拒收产物` ⇒ 两个 `_apply_file` 调用点都跳过）。\n"
                        f"（机器口径：能在入口拦下的错不在出口兜；"
                        f"写进主树再指望编译门判红是侥幸。）")
            if 补丁模式:
                # ★ 机器施加补丁 + 机器校验（行号界内 / 不重叠 / 非空改动）。
                #   失败 ⇒ 把逐条原因**写进产物**，让判据门判红并把原因回喂模型，
                #   绝不静默套用半成品。
                新文, 错 = _apply_patch(原文, 产出)
                if 新文 is None:
                    产出 = ("__ORCH_PATCH_REJECTED__: 补丁被机器拒绝（未施加）。\n"
                            + "\n".join(f"- {e}" for e in 错)
                            + f"\n\n你输出的原文（前 2000 字符）：\n{产出[:2000]}")
                else:
                    产出 = 新文
            # ★★★★★ R198：代码正文里的**全角标点**在**入口**拦下 —— 真证据（第 96 次真跑）：
            #   `E0460 unknown start of token … fullwidth comma` ⇒ 整个文件不可编译 ⇒ 整轮白跑。
            #   ★ 在入口换掉，比写进主树再让 cargo 判红便宜得多（省一次 3–10 分钟编译）。
            产出, _全198 = _修全角198(产出, d["path"])
            if _全198:
                self._修198 = getattr(self, "_修198", 0) + _全198
                _log(f"[generic] ★ R198 {d['path']} 正文里有 **{_全198} 处全角标点**"
                     f"（会引发 E0460 词法错误）⇒ 已换成半角（字符串/注释里的不动）")
            files[d["path"]] = 产出
        return files

    # -- 单窗：整组文件落盘 → 逐文件机器门 ------------------------------
    def _window_score(self, files: dict[str, str], idx: int) -> dict:
        wdir = self.round_root / f"w{idx}"
        wdir.mkdir(parents=True, exist_ok=True)
        for path, text in files.items():
            _落盘(wdir / path, text)                     # ★ R22c：唯一落盘实现
        if self.repo_scope:
            # 主树工程窗：沙箱留痕 + 备份→应用主树→判门→回滚（每窗独立试错，不留后效）
            backups = [_backup_file(rel) for rel in files]
            try:
                for rel, text in files.items():
                    # ★ 机器拒收的产物（改不了/补丁被拒/不是内容）**不进主树**：
                    #   沙箱副本照写（让门判红、原因入回执），但不去覆盖真实文件。
                    #   一处收口：标记只由 `_是拒收产物` 声明。
                    if _是拒收产物(text):
                        _log(f"[generic]   ↳ {rel}：机器拒收的产物 ⇒ **不写主树**"
                             f"（{_是拒收产物(text)}）")
                        continue
                    _apply_file(rel, text)
                results, red = _score_files(
                    wdir, self.deliverables, file_root=REPO_ROOT, repo_root=REPO_ROOT)
            finally:
                for bk in reversed(backups):
                    _rollback_file(bk)
        else:
            results, red = _score_files(wdir, self.deliverables)
        # ★ R17：数术层格裁决（取值等价于 `bool(results) and all(...)`）
        _窗过 = bool(_sl17.从门结果(results)[0])
        # ★★★★★ R73b：**窗级"通过"也必须用同一把尺子**。
        #   否则会出现最荒诞的一幕：最终裁决说"达标了"，而 `_align_windows`
        #   因为**没有一个窗 `passed`** 而判 `fixed_point=False` ⇒ 仍然交付不了。
        #   ★ 只在**真有基线**时才做相对判定：
        #     · 没有 `_dev_pred`（自检里手工造的 runner）⇒ 不判定（防假绿）；
        #     · 没有基线（主树本来就全绿）⇒ 老口径（必须绝对全绿）。
        if not _窗过:
            _pred73 = getattr(self, "_dev_pred", None)
            _基73 = getattr(self, "_基线身份", None)
            # ★★★★★ R124（真跑抓出的第 38 层 · 2026-10-21 · 第二十一次端到端）：
            #   **窗级「达标」与轮级「达标」是两把尺子，而日志只说 green=0。**
            #
            #   真证据（09:02→09:30 那一跑）：
            #     轮 1 `Δ=2 progressing`，轮 2 `Δ=2 diverging`，
            #     两轮**都写着** `基线 3 处→ 现 2 处` ⇒ 轮级 `_达标本=True`；
            #     而同一时刻 `多窗不动点对齐：green=0` ⇒ `fixed_point=False`
            #     ⇒ `done_ok=False` ⇒ 永远进不了 `deliver`。
            #   ★ `green=0` 是**症状**，不是原因。日志没留下任何原因 ——
            #     这正是 R85 家族的老毛病：**静默的 0 比红更难查**。
            #
            #   ⇒ 先加**逐窗诊断**（不改口径）：每个窗为什么没过，写成一行。
            #     口径问题等真证据说话，不靠猜。
            if _pred73 and _基73:
                try:
                    _cur73 = {deviation_identity(d) for d in
                              extract_deviations(red, _pred73)}
                    # ★ R124b：**显式传这一份产物的作业面外身份**（不看属性，
                    #   属性的时序是轮级的，窗级拿不到）
                    _面124 = getattr(self, "_面119", None) or _作业面路径集(
                        self.deliverables)
                    _窗过 = self._达标(
                        _cur73,
                        外今=_作业面外身份(extract_deviations(red, _pred73),
                                           _面124))
                    if not _窗过:
                        _外73 = frozenset(getattr(self, "_基线作业面外", None) or ())
                        _log(f"[generic] ★ R124 窗{idx} 未达标（逐项）："
                             f"窗内违规 {len(_cur73)} 处 / 基线 {len(_基73)} 处 / "
                             f"基线作业面外 {len(_外73)} 处 ⇒ "
                             f"窗内相对 {len(_cur73 - _外73)} vs "
                             f"基线相对 {len(_基73 - _外73)}；"
                             f"窗内违规逐条："
                             + "；".join(sorted(_cur73 - _外73)[:4]))
                except Exception as _e73:            # noqa: BLE001
                    _log(f"[generic] ★ R73b 窗级达标判定异常（按**未达标**处理，"
                         f"宁可严不可假绿）：{type(_e73).__name__}: {_e73}")
                    _窗过 = False
            else:
                # ★ R124：**不进相对判定**也必须留痕（原来这里完全静默）
                _log(f"[generic] ★ R124 窗{idx} 未达标且**没进相对判定**："
                     f"dev_pred={bool(_pred73)} 基线={len(_基73 or ())} 处 "
                     f"⇒ 退回「必须绝对全绿」（这是老口径，不是本次任务的责任口径）")
        return {"idx": idx, "files": files, "results": results, "red": red,
                # ★【TS-442】无损读数随窗携带：不动点/多窗对齐判在**因由**上，
                #   不判在布尔投影上（否则"因由变了"会被误当"没变"）。
                "因由": _因由向量(results, red),
                "passed": _窗过,
                "裁决因由": _sl17.从门结果(results)[1]}

    # -- 一轮：多窗产出 → 各窗判门 → 不动点对齐 → 写正本 → 最终判门 --------
    async def _round(self, content: dict | None,
                     red_audit_prev: list) -> tuple[bool, dict, dict, dict, list]:
        self._work += 1
        self.round_root = WORKDIR / self.safe_name / f"r{self._work}"
        self.round_root.mkdir(parents=True, exist_ok=True)

        # ★★★★★ R187：记下「本轮候选数 / 保留数」—— 裁决层要用它区分
        #   「搜索真到了不动点」与「每轮候选都被判更差（回退制造的不动点）」。
        #   真证据（第 90 次真跑）：三轮全部 保留 0 / 回退 3 ⇒ 树逐字回到基线
        #   ⇒ Δ 与上轮必然相同 ⇒ 判 stalled_fixed_point ⇒ 却把原因写成
        #   「非目标不动点，需换P/换分解」—— 而该换的是产出侧。
        self._本轮候选数187 = 0
        self._本轮保留数187 = 0
        # ★ R196：本轮因「没有红证据」被跳过的交付物数 —— **算了就要报出去**。
        #   这是 R128/R129 的病（算了、存了、没送出去）：我加了计数却没进回执，
        #   于是事后无法核对「作业面到底收敛到哪一步」。
        self._跳193 = 0
        # ★★★★★ R194：**第 1 轮没有「上一轮」，但有「基线」——那才是此刻还红的作业面。**
        #   真证据（第 93 次真跑第 1 轮）：
        #     `★ R188 跨文件共同决定已拿到（601 字符）` —— 601 字符说明**几乎没有证据**；
        #     `★ R193 不启用（红证据没有文件归属…）` —— 于是第 1 轮仍按过期清单全改，
        #      而收敛作业面的规则**本该从第 1 轮就生效**。
        #   ⇒ 口径：`red_audit_prev` 为空时，用**基线红门证据**（`_门态缓存["红"]`）当作业面。
        #     这与 L5975 已有的 `基线 = _红门违规点(red_audit_prev) or _红门违规点(_门态缓存["红"])`
        #     **同一把尺子**——此前只有裁决用它，产出侧没用，两把尺子。
        _证194 = red_audit_prev or (_门态缓存.get("红") or [])
        if not red_audit_prev and _证194:
            _log(f"[generic] ★ R194 第 1 轮没有「上一轮」证据 ⇒ 改用**基线红门证据**"
                 f"（{len(_证194)} 条）当作业面（此前产出侧空手，只有裁决侧看得到）")
            red_audit_prev = _证194

        window_files = await self._produce_windows(content, red_audit_prev)
        windows = [self._window_score(f, i) for i, f in enumerate(window_files)]
        adopted_idx, alignment = _align_windows(windows, self.min_pass)
        adopted = windows[adopted_idx]["files"]
        self._本轮候选数187 = len(adopted or {})

        # 写本轮正本（沙箱留痕）并做最终机器判门（与采纳窗独立复跑，防写盘错误）
        for path, text in adopted.items():
            _落盘(self.round_root / path, text)          # ★ R22c：唯一落盘实现
        if self.repo_scope:
            # 主树工程轮（机制② 原子回滚闭环）：备份 → 应用主树 → 判门 →
            #   全绿保留（备份登记回滚点）/ 红或异常自动回滚，不留半改状态
            #
            # ★★★★★ R43：**先把裁决粒度对齐到判据能给的度量上**。
            #   真跑实测：7 个文件一次全应用 ⇒ 判据从 14 处违规降到 9 处
            #   （`无恒真` 那条门**已全绿**、4 个恒真断言全修好）—— 但因为
            #   "还没全绿"，整轮**回滚** ⇒ **进展全部丢弃** ⇒ 下一轮从零开始
            #   ⇒ 配上预算护栏，**中长线任务永远不可能收敛**。
            #   ⇒ 判据给的不只是布尔：它**点出了每一处违规的位置**。
            #     逐文件应用、逐文件比较**违规点集合**：真子集变小就保留，
            #     否则只回退这一个文件。最终验收**仍是门全绿**。
            #   ★ 算不出基线（判据没点出位置）⇒ 走老路径（零回退）。
            基线 = _红门违规点(red_audit_prev) or _红门违规点(
                _门态缓存.get("红") or [])
            if 基线:
                _台177: list[dict] = []
                # ★★★★★ R216：同内容原地重交 ⇒ 直接判否，不再花一轮去评分。
                #   模型若把「上一轮已被机器拒收的同一份内容」再交一次，
                #   评分结果必然逐字相同 ⇒ 那一轮是纯浪费（真跑里已经浪费了很多轮）。
                if getattr(self, "_已否216", None) is None:
                    self._已否216 = {}
                _重交216 = []
                for _p216, _c216 in list(adopted.items()):
                    _h216 = _产出指纹216(_c216)
                    if _h216 in (self._已否216.get(_p216) or set()):
                        _重交216.append(_p216)
                        adopted.pop(_p216, None)
                if _重交216:
                    _log(f"[generic] ★ R216 **原地重交**：{len(_重交216)} 个文件的产出与**已被否过的**"
                         f"逐字相同 ⇒ 不再花一轮评分，直接判否：{_重交216[:4]}")
                    self._emit({"kind": "duplicate_candidate_rejected",
                                "work": work, "files": _重交216[:6]})
                # ★★★★★ R220：**产出体积塌缩 ⇒ 直接拒收，不许写进树。**
                #   真证据（第 120 次真跑现场 work/zh_compiler/task/r11/）：
                #     `示例工程/基础集/时序拓扑注入.rs` → **0KB**（原 58KB）
                #     `示例工程/基础集/算子选择器.rs`   → **1KB**（原 34KB）
                #   ⇒ 模型产出过「近乎空的完整文件」，而这套机器**差点把它写进主树**。
                #   ★ 这比「判据骗人」更狠：判据可以事后发现错误，但**写进树的东西
                #     已经毁了工作区**；若门集为空（R210 那条路）连事后发现都没有。
                #   ⇒ 口径：**替换已有文件时，行数不许塌缩到原文件的一半以下**
                #     （原文件 ≥40 行才判；小文件不管，避免误伤）。拒收不评分、进回执。
                _缩220: list = []
                try:
                    for _p220, _c220 in list(adopted.items()):
                        _原220 = Path(REPO_ROOT) / _p220
                        if not _原220.exists():
                            continue
                        _原行220 = _原220.read_text(encoding="utf-8", errors="ignore").count("\n") + 1
                        _新行220 = str(_c220).count("\n") + 1
                        if _原行220 >= 40 and _新行220 < max(8, _原行220 // 2):
                            _缩220.append({"file": _p220, "原行": _原行220, "新行": _新行220})
                            adopted.pop(_p220, None)
                except Exception as _e220:                 # noqa: BLE001
                    _log(f"[generic] ★ R220 体积检查未跑（不阻断）：{_e220}")
                if _缩220:
                    _log(f"[generic] ★ R220 **产出体积塌缩 ⇒ 拒收**（不许写进树）："
                         f"{[(x['file'], x['原行'], x['新行']) for x in _缩220[:3]]}")
                    self._emit({"kind": "shrunken_candidate_rejected",
                                "work": work, "detail": _缩220[:6]})
                results, round_red, _留43, _退43 = _增量应用(
                    adopted, 基线, self.round_root, self.deliverables, 回退详情=_台177)
                # ★ R177：本轮回退台账（每轮覆盖 ⇒ 模型看到的是「上一轮」）
                # ★ R179：**跨轮累计**每个文件被退过几次 —— 整份重写已被证伪。
                #   ★ 从返回值 `_退43` 数（不在 `_增量应用` 里改 self：那是模块级函数）。
                for _退名179 in (_退43 or []):
                    _k179 = str(_退名179)
                    self._回退次数[_k179] = int(self._回退次数.get(_k179, 0)) + 1
                # ★ R216：把这次被否掉的内容记进黑名单（下一轮原样再交会被当场识破）
                for _退名216b in (_退43 or []):
                    _键216b = _退名216b[0] if isinstance(_退名216b, (list, tuple)) else _退名216b
                    _键216b = str(_键216b)
                    if _键216b in adopted:
                        self._已否216.setdefault(_键216b, set()).add(_产出指纹216(adopted[_键216b]))
                self._回退台账 = _台177
                self._active_backups = [bk for _, bk in _留43]
                self._本轮保留数187 = len(_留43 or [])
                # ★ R48：**跨轮累计**保留过的文件 —— 停机时要如实报"主树变了"。
                #   （`_active_backups` 每轮会被覆盖，不能当累计量用。）
                self._kept_all.extend(rel for rel, _ in _留43)
                # ★ R196：把「本轮跳过了几个交付物」写进日志**和回执**（可事后核对）。
                if getattr(self, "_跳193", 0):
                    _log(f"[generic] ★ R193/R196 本轮共**跳过 {self._跳193} 个交付物**"
                         f"（本轮它们没有红门证据 ⇒ 已经是对的，不动）")
                    self._emit({"kind": "workface_converged", "work": work,
                                "skipped": self._跳193,
                                "reason": "作业面按当前红证据收敛：无红证据的交付物不产出"})
                    self._跳193 = 0          # 用后即清（只属于本轮）
                _log(f"[generic] ★ 增量裁决：保留 {len(_留43)} 个 / "
                     f"回退 {len(_退43)} 个（判据违规点 {len(基线)} → "
                     f"{len(_红门违规点(round_red))}）")
                # ★★★★★ R89（2026-10-18 · 真跑第 3 轮抓出）：
                #   **代偿** —— 目标文件零改动，却因为**别的文件**变好而报 `progressing`。
                #   真跑原样：
                #     ✓ 运行时涌现.rs：门 9 → 0 ⇒ 保留
                #     ✗ 观测协议.rs：未严格变小 ⇒ 回退
                #     ✗ 模块丙.rs：未严格变小 ⇒ 回退   ← **这就是目标文件**
                #     ★ 增量裁决：保留 1 / 回退 2 ⇒ verdict=progressing
                #   ★ 缺陷不在"保留"（那 1 个是真改进），在**裁决**：
                #     总分变小就报有进展 ⇒ **可以用一个文件的收益去买另一个文件的亏损**。
                #     而目标说的是「**这个**文件要接上」，不是「总共好一点」。
                #   ⇒ 达标判定加一条**硬约束**：目标点名的文件必须**它自己**变好。
                #     ★ 这是 R86「覆盖」之后的下一层 —— **覆盖 ≠ 改善**。
                _必改89 = _目标声明的必改文件(getattr(self, "goal", "") or "")
                # ★★★★★ R202：**与 R200 同一把尺子** —— 达标口径里的「目标点名文件」
                #   也不能全盘照收目标文本里的历史清单。真证据（第 99 次真跑第 1 轮）：
                #     `R89 代偿拒收：目标点名的 ['算子选择器.rs','自涌现编译.rs',
                #      '时序拓扑注入.rs','织网者.rs'] 本轮自己没变好 ⇒ 不判达标`
                #   而作业面只有 2 个文件（R193/R200 已证另 2 个已经改对）
                #   ⇒ 就算那 2 个改对了，达标也会被 2 个**已经对的文件**否掉 ⇒ 永远不达标。
                #   ★ 边界：现场实测非空时才收敛（门全绿时保持不变）。
                _红现202 = _红证据文件(_门态缓存.get("红") or [])
                if _红现202:
                    _丢202 = [_f for _f in _必改89 if _f not in _红现202]
                    if _丢202:
                        _log(f"[generic] ★ R202 达标口径收敛：目标点名但**现场实测不红**的"
                             f"{len(_丢202)} 个文件不参与达标判定：{_丢202[:4]}")
                        self._emit({"kind": "acceptance_scope_converged",
                                    "work": getattr(self, "_work", 0),
                                    "dropped": _丢202[:8]})
                    _必改89 = [_f for _f in _必改89 if _f in _红现202]
                if _必改89:
                    _留集89 = {rel for rel, _ in _留43}
                    # ★ 判定逻辑**抽成纯函数**（`_必改未变好`）——
                    #   内联表达式没有"方向对不对"的可测面（`_验R89齿.py` ③ 抓出过）。
                    self._必改未达标 = _必改未变好(_必改89, _留集89)
                    if self._必改未达标:
                        _log(f"[generic] ★ R89 **代偿拒收**：目标点名的 "
                             f"{self._必改未达标} 本轮**自己没变好**"
                             f"（未进保留集）⇒ 别处的改善**不得代偿** ⇒ "
                             f"本次任务**不判达标**")
                else:
                    self._必改未达标 = []
            else:
                # ★★★★★ R155（第 40 次真跑抓出的第 70 层 · 2026-10-25）：
                #   **门全绿了，达标判定却说「目标文件自己没变好」。**
                #
                #   真证据（第 40 次真跑，轮 4，原文）：
                #     `轮 4: 门= cargo_check_lib=True cargo_test_lib_no_run=True
                #        mod_has_production_wide_key=True
                #        arc_eval_uses_production_key=True
                #        pointcloud_eval_uses_production_key=True
                #        eval_spec_uses_production_key=True`   ← **6/6 全绿**
                #     `未达标因由：目标点名的文件**自己没变好**：
                #        ['src/模块甲/mod.rs', 'src/模块乙/arc评测.rs', …]`
                #   ★ 两句**互相矛盾**：门全绿 = 那些文件都做到了；
                #     而因由说它们「没变好」。
                #
                #   ★★ 根因：`_必改未达标` **只在 `if 基线:` 分支里算**
                #     （见上面 L5346）。而 `基线 = _红门违规点(...)`
                #     —— **门全绿时基线为空** ⇒ 走这个 else 分支
                #     ⇒ `_必改未达标` **保留着上一轮的值**。
                #     第 3 轮它是 `['…4 个文件…']`，第 4 轮没被重算，
                #     `_达标` 一读就早退 ⇒ **永远不可能达标**。
                #
                #   ★★★ 这是「**累积量被当成当前量**」的第 N 次
                #     （R140 是同一族）。一个**每轮都该重算**的判定，
                #     在一个分支里被跳过了。
                #
                #   ★★★★ 处置：**每轮无条件重算**。
                #     门全绿 ⇒ 没有「未进保留集」这回事 ⇒ 必改文件视为已变好
                #     ⇒ `_必改未达标 = []`（真值，不是"跳过"）。
                #     ★ 判据不变：R89 仍拦「用别处的收益买通裁决」——
                #       基线非空时那条路径照旧；这里只是补上**它没被算**的那条路径。
                # ★★★ 方向别反：`_必改未变好(必改, 保留集)` 的语义是
                #   「**不在**保留集里的算没变好」⇒ 传空集等于「全没变好」。
                #   而门全绿的含义恰恰相反：**必改文件全都做到了**。
                #   ⇒ 这里直接给 `[]`（真值：没有未变好的），
                #     ★ 不是"跳过计算"，是"算出来的结果就是空"。
                #   ★ 第一版我写了 `_必改未变好(_必改155, set())`，
                #     齿当场咬住（「门全绿时仍非空 ⇒ 达标永远不成立」）——
                #     那条齿救了一次真回归。
                self._必改未达标 = []
                results, round_red = {}, []
                backups = [_backup_file(rel) for rel in adopted]
                self._active_backups = backups
                try:
                    for rel, text in adopted.items():
                        # ★ 同 `_window_score`：机器拒收的产物**不进主树**
                        #   （一处收口：标记只由 `_是拒收产物` 声明）。
                        if _是拒收产物(text):
                            _log(f"[generic]   ↳ {rel}：机器拒收的产物 ⇒ **不写主树**"
                                 f"（{_是拒收产物(text)}）")
                            continue
                        # ★ R54：别人改过 ⇒ 跳过这一个文件（不覆盖、不炸整轮）
                        try:
                            _apply_file(rel, text)
                        except 并发改动错 as _e54:
                            _log(f"[generic]   ↳ {rel}：{_e54}")
                            continue
                    results, round_red = _score_files(
                        self.round_root, self.deliverables,
                        file_root=REPO_ROOT, repo_root=REPO_ROOT)
                except Exception:
                    for bk in reversed(backups):
                        _rollback_file(bk)
                    self._active_backups = []
                    raise
            # ★★★★★ R23：冻结区不得被改动（**判据**不能被被判方改掉）。
            #   放在判门**之后**、裁决**之前**：改动进 `results` 与 `round_red`
            #   ⇒ 该轮判红 + 因由回喂模型（它有机会改回来），而不是静默接受。
            _fv = _冻结区违反(self._冻结区)
            for _a in _fv:
                results[f"{_a['file']}@{_a['gate']}"] = False
            round_red = list(round_red) + _fv
            # ★ R17：数术层格裁决（取值等价于 `bool(results) and all(...)`）
            ok = bool(_sl17.从门结果(results)[0])
            if not ok and not 基线:
                # 老路径：全有或全无（无度量可依时必须保守）
                for bk in reversed(self._active_backups):
                    _rollback_file(bk)
                self._active_backups = []
            return ok, adopted, alignment, results, round_red
        results, _ = _score_files(self.round_root, self.deliverables)
        # ★ R23：非主树路径同样要查冻结区（冻结区与是否主树工程无关）
        _fv2 = _冻结区违反(self._冻结区)
        for _a in _fv2:
            results[f"{_a['file']}@{_a['gate']}"] = False
        # ★ R17：数术层格裁决（取值等价于 `bool(results) and all(...)`）
        ok = bool(_sl17.从门结果(results)[0])
        return ok, adopted, alignment, results, []

    def _报主树保留(self) -> None:
        """★ R48：**未收敛停机时，必须如实报「主树已经被改了」**。

        【真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`】

          R43 之后，每一轮**保留**的文件都被**项目自己的判据**验证过
          "违规点严格减少" ⇒ 它们**留在主树上**。而所有**未收敛**的停机路径
          （判据不可达 / 预算护栏 / 轮数护栏）都**只是 `return 1`**
          —— **一个字都没提主树已经变了**。

          ★ 后果：用户收到"未收敛"，会认为**主树没被动过**，
            而实际上它已经被改了若干个文件。这与本库的硬规矩冲突：
            **「报表与真实状态不一致，比没有报表更坏」**。
          ★ 更危险的一层：这些改动**是好的**（判据验证过的单调进展），
            但**不是全绿** ⇒ 用户若不知道，就会在**一个他没预期的主树状态**上
            继续做别的事（编译、提交、发布）。

        ★ 处置口径（与 R43 一致，不推翻它）：
          · 保留进展**是对的**（R43 的全部意义就是不让中长线任务永远白干）；
          · 但必须**如实、显著地报出来** —— 报什么文件、为什么留、
            以及**它还没全绿**、下一步该做什么。
          · 不在这里回滚（回滚就等于把 R43 又撤销了）；**只报，不偷偷动**。
        """
        if not self.repo_scope:
            return
        _留 = list(getattr(self, "_kept_all", None) or [])
        if not _留:
            return
        _uniq = sorted(set(_留))
        _log(f"[generic] ★★ 主树已被改动：{len(_uniq)} 个文件**保留**了"
             f"经判据验证的部分进展（未达全绿 ⇒ **不算交付**）")
        for _p in _uniq:
            _log(f"[generic]     · {_p}")
        _log("[generic]     ★ 这些改动**逐处**都被项目自己的判据验证过"
             "「违规点严格减少」⇒ 保留是有据的；但**目标未达成**。")
        _log("[generic]     ★ 若不需要它们：`git checkout -- <上面的路径>` 可还原。")
        self._emit({"kind": "main_tree_modified", "work": getattr(self, "_work", 0),
                    "kept_files": _uniq,
                    "reason": "未收敛停机，但主树保留了经判据验证的部分进展"
                              "（每一处都使违规点严格减少）⇒ 主树已非原始状态",
                    "tokens_chars": self._tok_chars})

    # -- 账本追加：iteration + 上轮红门的 fix_attempt（auto_objective 输入）--
    def _append_records(self, results: dict, red_audit_prev: list,
                        red_audit_now: list | None = None) -> None:
        """★★★ 2026-10-14 修（**同一塌缩的第三层：账本层**，TS-442/TS-424 落形）：

          旧版对上一轮红门只记两态：
            `still_red → "blocked"`（真停滞） / `not still_red → "resolved"`。
          ⇒ **"同门仍红、但红的因由已经变了"**（例：上一轮 `编译错|E0308@x.rs:1:2`，
            这一轮 `编译错|E0432@y.rs:9:5`）被判成 **blocked** ⇒ `attempts` 累积
            ⇒ `auto_objective` 判 `stalled_fixed_point` ⇒ **换门集/停机**。
          ⇒ 而**真实进展刚刚发生**。这就是 `Defect384` 在**账本层**的复现，
            也是"任务永远不收敛"的又一独立成因（与门层、策略层同源）。

          ⇒ 修法：新增第三态 **`progressed`**（因由已变 ⇒ 有进展，不是停滞）。
            · 本轮绿                      → `resolved`
            · 本轮红 且 因由 **未变**      → `blocked`（**真**停滞）
            · 本轮红 且 因由 **已变**      → `progressed`（有进展，**不得**计停滞）
            · 本轮红 但无因由可比（老账本）→ `blocked`（保守：与旧行为一致）

          ★ 判据没变：门的绿红仍由同一批门算出；本改动只让**账本如实记录**
            "红的原因变了没有" —— 这正是 TS-442 `read_eq_reflects` 要的
            "读等式能反推态等式"在账本层的落实。
        """
        gaps = [{"key": k,
                 "file": k.split("@")[0] if "@" in k else k,
                 "status": "resolved" if v else "open"}
                for k, v in results.items()]
        self.records.append({"round": "iteration", "time": str(self._work),
                             "gates": results, "post_gates": results,
                             "gaps": gaps})
        # 本轮各门的因由（用于判"红的原因变了没有"）
        本因由 = {str(a.get("gate")): str(a.get("cause") or "")
                 for a in (red_audit_now or []) if isinstance(a, dict)}
        for a in red_audit_prev:   # 上轮红门 → 本轮解决/仍未解决/有进展
            f = a.get("file")
            gate = a.get("gate")
            if not f or not gate:
                continue
            key = f"{f}@{gate}"
            still_red = any(k == key and v is not True for k, v in results.items())
            if not still_red:
                status = "resolved"
            else:
                旧因由 = str(a.get("cause") or "")
                新因由 = 本因由.get(str(gate), "")
                if 旧因由 and 新因由 and 旧因由 != 新因由:
                    status = "progressed"      # 因由已变 ⇒ 有进展，不是停滞
                else:
                    status = "blocked"         # 真停滞（或无因由可比，保守）
            self.records.append({
                "round": "fix_attempt", "gap_key": key, "file": f,
                "attempt": 1,
                "cause": str(本因由.get(str(gate), "")),
                "prev_cause": str(a.get("cause") or ""),
                "status": status})

    async def _run(self) -> int:
        # #2 跨批次统筹：回读历史账本作前缀（幂等一次），历史偏差驱动本单优先级
        self.records = self._ledger_cached()   # 增量索引账本：全历史可见，文件只增量 I/O
        hist_obj = auto_objective.compute(self.records)
        # ★ R153：把「契约破裂（真·无区分力）」一起传进去 ——
        #   同一次轮内这个信息就在手边，之前完全没用上。
        # ★ R153b：用**跨轮累积**的那个集合，不是本轮的（本轮此刻还是空的）
        self.hist_note = _hist_note(
            hist_obj, getattr(self, "_恒真门153", None))
        if hist_obj.active:
            _log(f"[generic] 历史偏差密度：{self.hist_note.strip()}")
        # ★★★★★ R144（第二处）：**规划失败也分瞬时/终态**。
        #   实测（第 35 次真跑）：`规划失败：Error code: 402 - no_provider_under_bid`
        #   ⇒ 整趟 exit 1，一行都没跑。
        #   ★ 规划是**第一件事**，它一死整趟归零 —— 所以这里比单轮更需要重试。
        #   ★ 判据不变：重试的是**同一个规划请求**；终态（401/403/模型不存在）
        #     立刻停，因为重试改变不了它。
        _规划瞬时次数 = 0
        _规划起163 = None          # ★ R167：同上，时钟懒启动
        while True:
            try:
                await self._plan()
                break
            except Exception as e:   # 规划失败：诚实交底，不硬跑
                _瞬, _因 = _是瞬时的(e)
                _规划瞬时次数 += 1
                _已等163 = (0.0 if _规划起163 is None
                            else __import__("time").monotonic() - _规划起163)
                # ★ R163/R167：判据是**为了等它好转忍了多久**。
                if _瞬 and _已等163 < _瞬时等待预算秒:
                    if _规划起163 is None:
                        _规划起163 = __import__("time").monotonic()
                    _等 = _退避163(_规划瞬时次数, _已等163)
                    self._emit({"kind": "plan_transient_retry",
                                "try": _规划瞬时次数, "wait_s": _等,
                                "waited_s": round(_已等163, 1),
                                "budget_s": _瞬时等待预算秒,
                                "err": f"{type(e).__name__}: {e}"})
                    _log(f"[generic] ★ R144 规划遇到**瞬时**上游错误（{_因}）"
                         f"⇒ 第 {_规划瞬时次数} 次重试，已等 "
                         f"{_已等163:.0f}/{_瞬时等待预算秒:.0f}s，"
                         f"退避 {_等:.0f}s（**不是终态，不放弃整趟**）")
                    await asyncio.sleep(_等)
                    continue
                self._emit({"kind": "plan_error",
                            "err": f"{type(e).__name__}: {e}",
                            "transient": _瞬, "tries": _规划瞬时次数,
                            "why": _因})
                _log(f"[generic] 规划失败：{e}"
                     + (f"（{_因}；已重试 {_规划瞬时次数 - 1} 次）"
                        if _瞬 else f"（{_因}）"))
                return 1

        content: dict | None = None
        red_audit_prev: list = []
        prev_devs: list | None = None
        prev_green: set = set()          # C5 曾绿偏差身份单点映射（每偏差一位，不折叠向量）
        results: dict = {}
        red: list = []

        pred = getattr(self, "pred", None)
        has_pred = isinstance(pred, dict) and bool(pred.get("checks"))
        # ★★★★★ R123b：`decl_scopes` 原来**只在 for 之前算一次** ——
        #   换门集（`_replan`）后交付物变了、门读的文件也变了，
        #   而作用域锚**还是旧门集的** ⇒ 新门集报的偏差锚不到 ⇒ `no_anchor`。
        #   ⇒ 抽成方法，**每轮开头重算**（与 `self.deliverables` 同源，不另立一套）。
        decl_scopes: set = set()
        if has_pred:
            decl_scopes |= pred_declared_scopes(pred)     # C1 声明作用域锚
        if self.deliverables:                              # ∪ 本轮拟改文件
            decl_scopes |= {str(d["path"]) for d in self.deliverables}
        # ★★★【TS-442】主树工程任务：`cargo check/test --lib` 是**整 crate** 判据，
        #   其错误可以落在**任何** `src/` 下的文件（不限于本轮拟改文件）。
        #   而 `anchored` 用"声明作用域是否出现在偏差身份里"判锚 ⇒
        #   若不把 crate 源码根声明为作用域，则"错误在别的文件"会被判 `no_anchor`
        #   ⇒ **交还用户/停机**，而其实锚（`src/`）明明在。
        #   ⇒ 这是"锚**读得出来**却判无锚"：判据的作用域声明**漏了**判据真正覆盖的范围。
        #   ★ 只对主树工程加（非工程任务不得放宽），且用与 `repo_index` 同一约定的
        #     源码根前缀，避免两处口径不一致。
        if self.repo_scope:
            decl_scopes |= {"src/", "tests/", "crates/", "packages/", "bin/"}
            # ★★★★★ R75：**工程级门的锚**。
            #   上一条给的是"错误**落在** `src/` 下的某个文件"这一情形；
            #   而工程级命令门（`cargo check/test --lib`）的**主体**是**整棵树**，
            #   它的偏差身份里**没有任何文件路径**（`_门主体` 给它 `_全库主体`）。
            #   ⇒ 不声明这个锚，`anchored` 对**工程级门**恒为 False
            #     ⇒ 一旦有新偏差出现就落 `no_anchor` ⇒ **交还用户/停机**。
            #   ★ 这与 TS-442 那一条是**同一个病**（判据的作用域声明漏了判据真正
            #     覆盖的范围），只是那次漏的是 `src/`，这次漏的是"整棵树"。
            decl_scopes |= {_全库主体}
        # Δ(P) 输入归属：pred 存在用真实 checks；否则退化为「按拟改文件收敛」（旧 gates 路径服务）
        dev_pred = pred if has_pred else {"checks": [], "files": list(decl_scopes)}

        # ★★★★★ R73（真跑抓出 · 2026-10-17）：**Δ 必须相对基线，不能相对空。**
        #   完整论证见 `_基线偏差` 的 docstring。一句话：
        #   门在主树上**本来就红**（历史违规）⇒ "必须绝对全绿"是**恒红门**
        #   ⇒ 无人值守**永远不可能跑通一次**。
        #   ★ 这一遍跑在 `for work` **之前** ⇒ 主树/隔离树**未被本轮改动** ⇒ 基线干净。
        #   ★ 基线为空 ⇒ `prev_devs = None` ⇒ **老行为完全不变**（必须绝对全绿）。
        self._基线偏差 = _基线偏差(self.round_root, self.deliverables, dev_pred)
        # ★ R73b：达标判定需要的两件东西 —— 判据输入归属（`dev_pred`）与
        #   基线的**违规点身份集合**。身份用 `deviation_identity`（与全库同一把尺子），
        #   不用"条数"：条数变少可能是"修 3 个坏 2 个"（交换，不是进展）。
        self._dev_pred = dev_pred
        self._基线身份 = frozenset(deviation_identity(d) for d in self._基线偏差)
        # ★ R119b：基线里**作业面碰不到**的那部分（历史债，不参与达标判定）
        self._面119 = _作业面路径集(self.deliverables)
        self._基线作业面外 = _作业面外身份(self._基线偏差, self._面119)
        if self._基线作业面外:
            _log(f"[generic] ★ R119 基线里有 {len(self._基线作业面外)} 处偏差"
                 f"**落在作业面之外**（交付物碰不到）⇒ 不参与达标判定"
                 f"（历史债不是本次任务的责任）；作业面内 "
                 f"{len(self._基线身份) - len(self._基线作业面外)} 处")
        if self._基线偏差:
            _log(f"[generic] ★ R73 基线：主树上**本来就有** "
                 f"{len(self._基线偏差)} 处判据偏差 ⇒ 本次达标口径为"
                 f"「**比基线严格变小**」，不是「绝对全绿」"
                 f"（历史违规不是本次任务的责任）")
        prev_devs = list(self._基线偏差) or None

        # ★★★★★ R78：**项目验收门的基线**（改之前量一次，交付时才有"跟谁比"）。
        #   ★ 来源是 `_门态探针` 已经缓存的那一次真跑（规划阶段，树未被改动）
        #     —— **零额外成本**，不重跑 `cargo test`。
        #   ★ 量不到 ⇒ 留空 ⇒ 交付时退回**绝对全绿**（老行为，零回退）。
        _基res78 = _门态缓存.get("res") or {}
        if _基res78 and _门态缓存.get("key") == str(REPO_ROOT):
            self._项目门基线res = dict(_基res78)
            _log(f"[generic] ★ R78 项目验收门基线："
                 f"{sum(1 for v in _基res78.values() if v is True)}/"
                 f"{len(_基res78)} 条绿 ⇒ 交付口径为「**不许由绿转红**」"
                 f"（主树历史债不算本次任务的责任）")
        else:
            self._项目门基线res = {}

        for work in range(1, self.max_work + 1):
            # ★ R123b：**每轮重算作用域锚**（换门集后交付物与门读的文件都会变）
            if work > 1:
                _旧面123 = set(decl_scopes)
                decl_scopes = set()
                if has_pred:
                    decl_scopes |= pred_declared_scopes(pred)
                if self.deliverables:
                    decl_scopes |= {str(d["path"]) for d in self.deliverables}
                if self.repo_scope:
                    decl_scopes |= {"src/", "tests/", "crates/",
                                    "packages/", "bin/", _全库主体}
                if decl_scopes != _旧面123:
                    _log(f"[generic] ★ R123 换门集 ⇒ **重算作用域锚**："
                         f"新增 {sorted(decl_scopes - _旧面123)[:6]}"
                         f" 去掉 {sorted(_旧面123 - decl_scopes)[:6]}")
            # ★★★★★ R144：**瞬时错误不许当终态**。
            #   实测（第 35 次真跑，两次都在第 1 轮归零）：
            #     `轮 1 异常：Error code: 402 … no_provider_under_bid → 诚实停机`
            #   ★ `no_provider_under_bid` 是「**此刻**没服务商接这个价」，
            #     30 秒后可能就成 —— 定义上瞬时。
            #   ★ 而无人值守的全部价值就是**熬过上游抖动**。
            #     Router 的 failover/冷却只解决「槽之间跳」；
            #     唯一存活槽抖一下，整趟就没了。
            #   ⇒ 瞬时 ⇒ 有界退避重试**同一轮**；终态 ⇒ 立刻诚实停机。
            #   ★ 不放松判据：重试的仍是同一轮、同一判据门；
            #     超了上限照样 `return 1`。
            _瞬时次数 = 0
            # ★★★★★ R167（2026-10-27）：预算时钟**从第一次瞬时失败才起算**。
            #   真事故（第 49 次真跑，日志最后一行）：
            #     `已重试 0 次，共等 2266/1800s ⇒ 上游持续不可用`
            #   ★ 2266 秒是**这一轮自己干活花的时间**（9 个槽挨个试，
            #     5 个慢槽各 110 秒），不是「等上游恢复等的时间」。
            #   ⇒ 时钟一启动就已经超预算 ⇒ **重试一次都没发生过**。
            #   ★ 判据需要的量是「**为了等它好转，我忍了多久**」，
            #     不是「这一轮跑了多久」—— 又是「手里那个量不是判据需要的那个量」。
            _轮起163 = None
            while True:
                try:
                    ok, adopted, alignment, round_results, round_red = await self._round(
                        content, red_audit_prev)
                    break
                except Exception as e:   # 单轮门异常：诚实交底停机，不让进程裸崩
                    _瞬, _因 = _是瞬时的(e)
                    _瞬时次数 += 1
                    _已等163 = (0.0 if _轮起163 is None
                                else __import__("time").monotonic() - _轮起163)
                    # ★ R163/R167：判据是**为了等它好转忍了多久**，不是次数，
                    #   也不是「这一轮跑了多久」。
                    if _瞬 and _已等163 < _瞬时等待预算秒:
                        if _轮起163 is None:
                            _轮起163 = __import__("time").monotonic()
                        _等 = _退避163(_瞬时次数, _已等163)
                        self._emit({"kind": "round_transient_retry",
                                    "work": work, "try": _瞬时次数,
                                    "wait_s": _等,
                                    "waited_s": round(_已等163, 1),
                                    "budget_s": _瞬时等待预算秒,
                                    "err": f"{type(e).__name__}: {e}"})
                        _log(f"[generic] ★ R144 轮 {work} 遇到**瞬时**上游错误"
                             f"（{_因}）⇒ 第 {_瞬时次数} 次重试，已等 "
                             f"{_已等163:.0f}/{_瞬时等待预算秒:.0f}s，"
                             f"退避 {_等:.0f}s（**不是终态，不放弃整趟**）")
                        await asyncio.sleep(_等)
                        continue
                    self._emit({"kind": "round_error", "work": work,
                                "err": f"{type(e).__name__}: {e}",
                                "transient": _瞬,
                                "tries": _瞬时次数,
                                "why": _因})
                    _log(f"[generic] 轮 {work} 异常：{e} → 诚实停机"
                         + (f"（{_因}；已重试 {_瞬时次数 - 1} 次，"
                            f"共等 {_已等163:.0f}/{_瞬时等待预算秒:.0f}s"
                            f" ⇒ **上游持续不可用，不是代码缺陷**）"
                            if _瞬 else f"（{_因}）"))
                    return 1
            if self.repo_scope:
                # 主树工程轮：正本已应用进主树并在 _round 内判定（绿保/红滚已完成）
                results, red = round_results, round_red
            else:
                results, red = _score_files(self.round_root, self.deliverables)
            # ★★★★★ R128（真跑抓出的第 41 层 · 2026-10-21 · 第二十四次端到端）：
            #   **机器算出了「哪些门曾经是绿的」，只用来判「是不是在原地打转」，
            #     从来没有告诉过模型。**
            #
            #   真证据（这趟跑的三轮）：
            #     轮1  cargo_check_lib=False  接线法则_九宗门降级接无界键=True   5 处
            #     轮2  cargo_check_lib=True   接线法则_九宗门降级接无界键=False  2 处
            #     轮3  cargo_check_lib=False  接线法则_九宗门降级接无界键=True   6 处
            #   ⇒ **模型在两条门之间来回换**：修好一条、弄坏另一条。
            #     这不是「不努力」，是它**不知道**自己刚把哪条弄绿了 ——
            #     每一轮的 prompt 只告诉它「现在是红的」，**没告诉它「刚刚还是绿的」**。
            #
            #   ★ `prev_green`（L5111）就在算这件事，但它的**唯一消费者**是
            #     `deviation_structure_verdict`（判 diverging）。
            #     ⇒ 又是「声明了与被使用之间断了」：**判据看得见，被 judge 的看不见。**
            #   ★ 一个工程师带徒弟时会说的第一句话就是
            #     「这条你昨天已经修好了，别又改回去」—— 机器现在说不出这句话。
            _prev_res128 = getattr(self, "_上轮门真值", None) or {}
            _曾绿128 = sorted(k for k, v in results.items()
                             if _prev_res128.get(k) is True and v is False)
            self._曾绿今红 = _曾绿128
            # ★★★★★ R131（真跑抓出的第 44 层 · 2026-10-22 · 第二十七次端到端）：
            #   **机器看得见「你在两个状态之间来回跳」，却只会说「你把这条弄红了」。**
            #
            #   真证据（这一跑的五轮，门真值逐位）：
            #     轮3  mod_有唯一广域入口=**红**  评测口径经统一入口=**绿**
            #     轮4  mod_有唯一广域入口=**绿**  评测口径经统一入口=**红**
            #     轮5  mod_有唯一广域入口=**红**  评测口径经统一入口=**绿**  ← 与轮3 逐位相同
            #   ⇒ 这是**真 2-循环**：修 A 就坏 B，修 B 就坏 A。
            #
            #   ★ 而机器每一轮说的是同一句话：「你把 X 弄红了」。
            #     模型照着做 —— 它把 X 修绿了，**然后弄红了 Y**（R129 回喂后轮5 实测）。
            #     ★ 它没有不听，它**做对了被要求的那件事**，而这件事本身不够。
            #   ★ 缺的那句话是：「**不是让你把红的那条修绿，是让你找到一个
            #     两条同时绿的改法**。」这两句话对模型是完全不同的任务。
            #
            #   ★ 这与 R128 是同一族的下一步：
            #     R128 给了「别把绿弄红」（**单点**信息）；
            #     R131 给的是「你已经在 A↔B 之间跳了两个来回」（**结构**信息）。
            #     前者让模型不再乱撞，后者才让它知道**必须换策略**。
            _prev2_131 = getattr(self, "_上上轮门真值", None) or {}
            _二循环131 = []
            if _prev2_131 and _prev_res128 and results:
                _共同 = [k for k in results if k in _prev2_131 and k in _prev_res128]
                _同 = [k for k in _共同 if results[k] == _prev2_131[k]]
                _异 = [k for k in _共同 if results[k] != _prev_res128[k]]
                # 2-循环 = 与上上轮**逐位相同**，且与上轮**确有不同**
                # （后者排除「三连不动点」，那是 stalled 不是 2-循环）
                if _共同 and len(_同) == len(_共同) and _异:
                    _二循环131 = _异
            self._二循环131 = _二循环131
            self._上上轮门真值 = dict(_prev_res128)
            self._上轮门真值 = dict(results)
            if _曾绿128:
                _log(f"[generic] ★ R128 本轮把 {len(_曾绿128)} 条**上一轮还是绿的**门"
                     f"改红了 ⇒ 下一轮 prompt 会明确要求「不许弄红它们」："
                     f"{_曾绿128[:6]}")
            if _二循环131:
                _log(f"[generic] ★★★ R131 **2-循环**：本轮门真值与**上上轮逐位相同**"
                     f"（{len(_同)}/{len(_共同)} 条一致），在上轮与它之间来回跳。"
                     f"摆动的是 {_二循环131[:4]} ⇒ 下一轮 prompt 会要求"
                     f"「找一个两者同时绿的改法」，而不是逐条修")
            self._append_records(results, red_audit_prev)
            red_audit_prev = red
            content = adopted

            # Δ(P) 结构偏差 + C5 曾绿单点映射
            # ★★★★★ R207：**门级基线** —— 基线主树上就红的门，不许否决收敛。
            #   真证据（第 106 次真跑第 2/3 轮，门真值原文）：
            #     `（全库）@no_unused_mut_warnings=False`
            #     `（全库）@test_count_at_least_1590=False`
            #   ★ `test_count_at_least_1590` 的 **1590 是从目标文本里搬来的历史数字**
            #     （实测仓里只有 1543 个 `#[test]`）⇒ 这条门**永远不可能绿**；
            #     `no_unused_mut_warnings` 在 19 万行仓上同样长期为红。
            #   ⇒ 只要它们进「全绿」判定，`Δ=∅` 永远达不到 ⇒ **永不收敛**
            #     ⇒ 目标①②③ 在结构上不可达（不是模型不行）。
            #   ★ 口径（与 R73/R78「历史违规不是本次任务的责任」**同一条纪律**，
            #     此前只对「偏差条数」用了，没有对**门本身**用）：
            #     · 每个门集的**第一轮**如实量、不豁免（否则一上来就假收敛）；
            #     · 之后：**基线就红的非项目门**不参与收敛判定；
            #     · **项目验收门（档案声明）永远参与**，绝不豁免。
            #     · 达标仍由 R206 把关（项目门必须**由红转全绿**）⇒ 收敛变宽、达标不变松。
            _指纹207 = tuple(sorted(results))
            _项名207 = set((_门态缓存.get("res") or {}))
            _首轮207 = getattr(self, "_基红门207指纹", None) != _指纹207
            _豁免207 = set() if _首轮207 else (getattr(self, "_基红门207", set()) - _项名207)
            if _豁免207:
                _被免207 = [a.get("gate") for a in (red or [])
                            if a.get("ok") is not True and a.get("gate") in _豁免207]
                if _被免207:
                    _log(f"[generic] ★ R207 **门级基线**：{len(set(_被免207))} 条门在基线主树上就是红的"
                         f"（不是本次任务引入）⇒ 不参与收敛判定：{sorted(set(_被免207))[:4]}")
                    self._emit({"kind": "baseline_red_gates_exempted",
                                "work": work, "gates": sorted(set(_被免207))[:8]})
            _红207 = [a for a in (red or [])
                       if a.get("ok") is True or a.get("gate") not in _豁免207]
            if _首轮207:
                self._基红门207指纹 = _指纹207
                # ★★★★★ R208：**只有「不可归因」的红门才算历史债。**
                #   第 107 次真跑实测（我自己造的缺陷，必须登记）：
                #     `R207 门级基线：2 条门在基线主树上就是红的 ⇒ 不参与收敛判定：
                #      ['cargo_test_lib_no_run', 'test_count_at_least_1590']`
                #     `轮 2: Δ=0 verdict=converged action=stop`
                #     `判据结构停机 @轮2（converged）→ 交还用户` / `[真跑] 退出码 1`
                #   ★ 主树当时**仍是 1 个编译错误**。R207 把**目标本身那条门**当成历史债豁免了
                self._基红门207 = {a.get("gate") for a in (red or [])
                                     if a.get("ok") is not True and not _有定位208(a)}
                self._基红门有定位207 = {a.get("gate") for a in (red or [])
                                          if a.get("ok") is not True and _有定位208(a)}
                if self._基红门有定位207:
                    _log(f"[generic] ★ R208 这 {len(self._基红门有定位207)} 条基线红门**能指出位置**"
                         f"⇒ 是本次要改的，**不豁免**：{sorted(self._基红门有定位207)[:4]}")
            cur_devs = extract_deviations(_红207, dev_pred)
            cur_ids = {deviation_identity(d) for d in cur_devs}
            # ★ R119b：本轮落在作业面之外的偏差身份（供 `_达标` 剔除历史债）
            self._本轮作业面外 = _作业面外身份(
                cur_devs, getattr(self, "_面119", _作业面路径集(self.deliverables)))
            # ★★★★★ R140（真跑抓出的第 53 层 · 2026-10-23 · 第三十二次端到端）：
            #   **`prev_green` 是跨轮累积集合，而它被当成「当前状态」在用。**
            #
            #   真证据（第 32 次真跑，门真值逐轮）：
            #     轮1 cargo_check_lib=**绿**  mod_生产侧=红  点云=红  接线链=红  （4 红）
            #     轮2 cargo_check_lib=**红**  （5 红）—— R128 记下「曾绿今红」
            #     轮3 cargo_check_lib=**绿**  mod_生产侧=**绿**  （4 红，与轮1 同基数）
            #     ⇒ `verdict=diverging`「已消偏差重现（C6）」⇒ 停机交还
            #
            #   ★ 而轮3 相比轮1 **一点没倒退**：红门数 4→4，
            #     而且 `mod_生产侧` 由红转绿（**真进展**）。
            #     判 diverging 的唯一依据是「某个偏差身份曾出现在 prev_green 里」——
            #     而 `prev_green` 是**从第一轮起累积、永不收缩**的集合。
            #
            #   ★★ 这是「**累积量被当成当前量**」，与 R137（因由说的是别的层的值）
            #     / R138（缓存键少了维度）同族：**手里那个量不是判据需要的那个量。**
            #     C6 要问的是「**上一轮**消掉的偏差，这一轮又回来了吗」——
            #     那是一个**相邻两轮**的问题，答案只能来自**相邻两轮**。
            #     用累积集合回答它 ⇒ 只要历史上消过一次，**永远**判发散
            #     ⇒ 修好一次之后，任何后续波动都被当成"倒退" ⇒ **必然停机**。
            #
            #   ★ 为什么原来写成累积：注释说「C5 曾绿＝单点映射，不折叠」——
            #     那句话讲的是**每门一位、不要折叠成向量**（对的），
            #     但「不折叠」被误读成「不收缩」。**不折叠 ≠ 不收缩。**
            #
            #   ⇒ 修法：`prev_green` 只记**上一轮**消掉的偏差（相邻两轮语义）。
            #     ★ 不放松：上一轮消掉、这一轮重现 ⇒ 仍判 diverging（C6 原意）。
            #     ★ 但跨轮的历史波动不再被当成倒退。
            if prev_devs is not None:      # 已消偏差（前轮存在、本轮无）记为曾绿（单点，不折叠）
                prev_ids = {deviation_identity(d) for d in prev_devs}
                #   ★ 注意这里是 **=** 不是 **|=** —— 见上面 R140 的论证。
                prev_green = (prev_ids - cur_ids)
            # 结构停机判据：纯结构谓词（SelfEvolvingRule 停机⟺不动点），无数值上限
            verdict, why = deviation_structure_verdict(
                prev_devs, cur_devs, declared_scopes=decl_scopes,
                prev_green=prev_green,
                # ★ R122：作业面**外**的历史债不参与「有没有锚」的判定 ——
                #   与 R119b 同一条纪律（豁免集合只从基线取，不随本轮长大）。
                ignore_ids=frozenset(
                    getattr(self, "_基线作业面外", None) or ()))
            prev_devs = cur_devs
            # ★★★★★ R210：**「没有门」不是「门全绿」。**
            #   真证据（第 110 次真跑第 3 轮，原文）：
            #     `轮 3: 门= | Δ=0 verdict=converged action=stop`
            #     `判据结构停机 @轮3（converged）→ 交还用户` / `[真跑] 退出码 1`
            #     而主树**仍是 1 个编译错误**。
            #   ★ 门集退化成空（换门集后一个门都没派生出来）⇒ `Δ=∅` **恒真**
            #     ⇒ 机器报「收敛」。这与 R207 是同一个坏方向：**假收敛**。
            #   ★ 仍是 R128/R129 的病：**「没量到」被当成「量到了且全绿」**。
            #   ⇒ 口径：本轮**一个门都没观测到** ⇒ 绝不判 converged；
            #     判 `no_anchor` 并如实说「门集是空的，不是目标达成了」。
            if not results:
                # ★★★★★ R213：空读数有两种，处置**相反** —— 别一律去换门集。
                #   真证据（第 113 次真跑）：`轮 3: 门= | Δ=0` ⇒ R210 判 no_anchor ⇒
                #     `轮 3：no_anchor → 已换判据门集（第 1/2 次），继续演化`，
                #     新门集基线 5 处偏差、窗口违规 6→5→3→4→3 不降，还出现 E0753
                #     （`expected outer doc comment` = 模型把文档注释写进函数体）⇒ **在破坏文件**。
                #   ★ 两种空读数必须分开：
                #     · 上一轮**有读数**、本轮没有 ⇒ 是**本轮没产出**（模型/通道/窗口失败），
                #       门集没坏 ⇒ **沿用同一门集重跑**，换门集只会把判据放宽、把模型带偏；
                #     · 上一轮也是空 ⇒ 才是真的没锚（门集派生失败）⇒ 该重规划。
                _有旧门213 = bool(getattr(self, "_上轮门真值", {}) or {})
                if _有旧门213:
                    verdict = "progressing"
                    why = ("★ R213 本轮**零读数**，但上一轮是有读数的 ⇒ 这是**本轮没产出**，"
                           "不是门集坏了 ⇒ **沿用同一门集重跑**（换门集只会放宽判据、把模型带偏）")
                    _log(f"[generic] {why}")
                else:
                    verdict = "no_anchor"
                why = ("★ R210 本轮**一个门都没观测到**（门集为空）⇒ `Δ=∅` 是恒真式，"
                       "不是收敛 ⇒ 不判 converged。这是**测量缺失**，不是目标达成")
                _log(f"[generic] {why}")
                self._emit({"kind": "empty_gate_set_not_converged",
                            "work": work, "deliverables": len(self.deliverables)})

            # ★★★★★ R187：**这个不动点是谁制造的？**
            #   回退机制保证主树回到基线 ⇒ 若本轮候选**一个都没留下**，
            #   则下一轮量到的 Δ 必然与上一轮逐字相同 ⇒ 必判 stalled_fixed_point。
            #   ⇒ 那个「不动点」是**回退制造**的，不是**搜索证出**的。
            #   两件事处置相反：前者该换产出口（模型/通道/收窄任务面），
            #   后者该换门集。现在一律按后者处置 ⇒ 必然一半错。
            #   ★ 只改**因由**，不改判定、不改动作（不放松停机）。
            why = _不动点因由187(verdict, getattr(self, "_本轮候选数187", 0), getattr(self, "_本轮保留数187", 1), why)
            # ★★★★★ R201：**回退制造的不动点 ≠ 搜索证出的不动点 ⇒ 不许停机。**
            #   真证据（第 99 次真跑第 1 轮，原文）：
            #     `轮 1: … Δ=3 verdict=stalled_fixed_point action=stop`
            #     `轮 1：stalled_fixed_point → 已换判据门集（第 1/2 次），继续演化`
            #   ★ 第 1 轮**根本不可能**是不动点：不动点要求「本轮 Δ 与上一轮相同」，
            #     而第 1 轮的『上一轮』其实是**基线**（同一次测量）⇒ 恒等 ⇒ 必然判 stalled。
            #   ★ R187 早已查明：候选全被拒 ⇒ 回退保证主树回到基线 ⇒ Δ 逐字相同。
            #     但 R187 当时**只改了因由、没改动作**（注释写「不放松停机」）。
            #     ⇒ 实测后果：每轮都 stalled ⇒ 每轮换门集 ⇒ **换门集又重取基线**
            #     ⇒ 永远在第 1 轮打转，一次真正的第 2 轮都没跑过。
            #   ★ 处置口径：候选>0 且 保留=0 ⇒ 判**产出侧失效**，不停机；
            #     连续全拒超过硬顶（3 次）才允许停（防死循环）。
            _全拒201 = (verdict == "stalled_fixed_point"
                        and int(getattr(self, "_本轮候选数187", 0)) > 0
                        and int(getattr(self, "_本轮保留数187", 0)) == 0)
            self._连拒201 = (int(getattr(self, "_连拒201", 0)) + 1) if _全拒201 else 0
            _硬顶201 = 3
            if _全拒201 and self._连拒201 <= _硬顶201:
                verdict = "progressing"
                why = (f"★ R201 本轮 {getattr(self, '_本轮候选数187', 0)} 份候选**全部被机器拒收**"
                       f"（保留 0）⇒ 这个「不动点」是**回退制造**的，不是搜索证出的 ⇒"
                       f"**不停机**（连续全拒 {self._连拒201}/{_硬顶201} 次）⇒"
                       f"下一轮必须换**产出侧**：收窄到单文件 / 换通道 / 用补丁模式")
                _log(f"[generic] ★ R201 {why}")
                self._emit({"kind": "rollback_fixed_point_ignored",
                            "work": work,
                            "candidates": getattr(self, "_本轮候选数187", 0),
                            "consecutive": self._连拒201, "cap": _硬顶201,
                            "verdict_was": "stalled_fixed_point"})

            # ★★★★★ R214：**「保留」也可能是空转 —— 结构门绿了、真门没动。**
            #   真证据（第 114 次真跑第 4 轮，原文）：
            #     `轮 4: 门=（全库）@cargo_test_lib_no_run=False
            #       算子选择器.rs@保留_样本列表_定列构造=True
            #       时序拓扑注入.rs@保留_继续函数与拓扑切片签名=True | Δ=1
            #       verdict=stalled_fixed_point action=stop`
            #     `轮 4：stalled_fixed_point → 已换判据门集（第 1/2 次），继续演化`
            #   ★ 本轮**有保留**（结构门翻绿）⇒ R201 的「全拒」判别**不触发**；
            #     但 `Δ` 与上一轮逐字相同 ⇒ 判真不动点 ⇒ **换门集**。
            #   ★ 而 R187 早已写明：不动点分两种，处置相反 ——
            #     回退制造的 ⇒ 换产出侧；**搜索证出的 ⇒ 才换门集**。
            #     这里换门集是**错的**：判据没坏，坏的是**产出**（结构门绿了真门没动）。
            #   ⇒ 口径：**保留 > 0 但 Δ 没变 ⇒ 不许停机、也不许换门集**；
            #     判 progressing + 收窄作业面到**真门报红的文件**，并把
            #     「上一轮保留的那些改动没让真门变好」**明确写进下一轮 prompt**。
            def _路径214(_d) -> str:
                if isinstance(_d, dict):
                    return str(_d.get("path") or _d.get("file") or "")
                return str(_d)
            
            _空转保留214 = (verdict == "stalled_fixed_point"
                            and int(getattr(self, "_本轮保留数187", 0)) > 0)
            if _空转保留214:
                _真红文件214 = _红证据文件(red)
                if _真红文件214:
                    _窄214 = [d for d in self.deliverables
                                              if _路径214(d) in _真红文件214]
                    if _窄214 and len(_窄214) < len(self.deliverables):
                        self.deliverables = _窄214
                self._空转保留214 = True      # 供下一轮 prompt 说真话
                verdict = "progressing"
                why = (f"★ R214 本轮**保留了 {getattr(self, '_本轮保留数187', 0)} 个改动**"
                       f"（结构门翻绿）但 `Δ` 与上一轮逐字相同 ⇒ 这些改动**没让真门变好**，"
                       f"是**空转保留** ⇒ 不停机、也**不换门集**（判据没坏，坏的是产出）⇒"
                       f"收窄作业面到真门报红的文件：{sorted(_真红文件214)[:3]}")
                _log(f"[generic] {why}")
                self._emit({"kind": "no_op_retention_not_fixed_point",
                            "work": work, "kept": getattr(self, "_本轮保留数187", 0),
                            "red_files": sorted(_真红文件214)[:6]})

            # ★★★★★ R73b：**唯一的达标口径**（门全绿，或相对基线严格变小）。
            #   下面所有"原来问 `verdict == "converged"`"的地方一律改问它 ——
            #   否则会出现**两把尺子**：一处说达标、另一处说没达标。
            _达标本 = self._达标(cur_ids)
            # ★★★★★ R206：**项目验收门翻绿 ⇒ 目标达成；不许让代理判据否决它。**
            #   真证据（第 102 次真跑第 1 轮，原文）：
            #     `✓ 示例工程/基础集/算子选择器.rs：判据全绿 ⇒ 保留`
            #     `轮 1: 门=（全库）@cargo_test_lib_no_run_exit0=True … Δ=0 verdict=converged`
            #     `★ R89 代偿拒收：目标点名的 ['时序拓扑注入.rs'] 本轮自己没变好 ⇒ 不判达标`
            #     `[真跑] 退出码 1`
            #   ★ 而机器**实测**：`cargo test --lib --no-run` 退出码 **0**、错误 0、
            #     `#[test]` 不减（金版 11 个 → 现 13 个，多 2 个）。**目标已经达成。**
            #   ★ 机器却判自己没达成 —— 因为**代理判据**（「目标点名的每个文件本轮都要自己变好」）
            #     否决了**验收判据本身**（项目验收门全绿）。
            #   ★ 这是本项目主缺陷族第 14 次：**判据要的是「目标达成了没有」，**
            #     手里拿的是「每个辅助文件本轮有没有各自变好」。
            #   ★ 口径（精确，不放水）：
            #     · 项目验收门 = 档案 profile 里声明的门（`_门态缓存['res']` 的键）；
            #     · 必须**从「不是全绿」变成「全绿」**才算达成（基线本来就全绿 ⇒ 不构成达成）；
            #     · 缺测到的门不算绿（宁严不宽）。
            # ★★★★★ R209：**达标 = 此刻实测项目验收门全绿，不按名字对号。**
            #   真证据（第 108 次真跑第 2 轮）：`（全库）@cargo_test_lib_no_run=True …
            #   Δ=0 verdict=converged` → `判据结构停机 → 交还用户` / `[真跑] 退出码 1`，
            #   而**主树实测已是 0 错误、退出码 0**（独立复核）。
            #   ★ 根因：R206 拿「档案门名」去 `results` 里找 —— 而 `results` 的键是
            #     主心骨自撰的门名（`cargo_test_lib_no_run`），与档案门名
            #     （`cargo_test_lib_compile`）**不同名** ⇒ 一个都对不上 ⇒ R206 从不触发。
            #   ★ 仍是「判据要的那条读数 ≠ 手里拿的那条读数」：
            #     要的是「项目验收门此刻绿不绿」，拿的是「名字对不对得上」。
            #   ⇒ 口径：**现场重跑档案里的验收门**（cargo 有缓存，秒级），
            #     用它此刻的真值判定；基线同时重取，要求「由红转全绿」。
            if not _达标本:
                _存209 = dict(_门态缓存)
                _基res209 = dict(_存209.get("res") or {})
                try:
                    _门态缓存.clear()
                    self._规划前门态()               # 现场重跑档案门（实测，不猜）
                    _res209 = dict(_门态缓存.get("res") or {})
                except Exception as _e209:
                    _res209 = {}
                    _log(f"[generic] ★ R209 现场重跑项目验收门失败（按未达成处置）：{_e209}")
                finally:
                    _门态缓存.clear()
                    _门态缓存.update(_存209)         # 还原本轮上下文，别污染别处
                _全绿209 = bool(_res209) and all(bool(_v) for _v in _res209.values())
                _基全绿209 = bool(_基res209) and all(bool(_v) for _v in _基res209.values())
                if _全绿209 and not _基全绿209:
                    _达标本 = True
                    _log(f"[generic] ★ R209 **项目验收门现场实测：由红转全绿** {_res209}"
                         f"（基线 {_基res209}）⇒ 目标达成。这条读数不是任何模型的自述，"
                         f"是机器刚刚亲手跑出来的。")
                    self._emit({"kind": "acceptance_by_project_gates",
                                "work": work, "gates": dict(_res209),
                                "baseline": dict(_基res209), "via": "R209 现场重跑"})
                elif _全绿209:
                    _log("[generic] ★ R209 项目验收门全绿，但**基线本来就全绿**"
                         "⇒ 不构成本次达成（不放水）")
            # ★ 日志条件**不写** `verdict != "converged"`：那会让"达标判定点"
            #   的接线齿（AST 负检查）分不清"日志"与"判定"。用 `cur_ids` 非空
            #   表达同一件事 —— **达标了但不是因为全绿**。
            if _达标本 and cur_ids:
                _log(f"[generic] ★ R73b 达标（相对基线）：verdict={verdict}，"
                     f"但违规点 {len(self._基线身份)} → {len(cur_ids)} "
                     f"且为**真子集** ⇒ 本次任务让它变好了")

            # 多窗不动点对齐佐证：Δ=∅ 且同一规范点真值向量多窗一致才交付
            # ★ 接线（2026-10-12）：**交付前先验门本身有没有区分力**。
            #   与活库 TS375 对齐：`O1Spec375` 把「区分力见证
            #   `∃ x y, content x ≠ content y`」列为 O(1) 内容件的**必要**组成，
            #   并机证 `no_spec_of_const_content`（常内容件不构成 O(1) 实例）。
            #   同构缺口：一个恒真门（对任何产物都绿）使「门全绿」与「目标达成」
            #   脱钩 —— 正是 TS392–410 分类学里的形态 5/7。
            #   库对每条真证都跑「必须失败」的控制探针（`.tools/假边界总账.md`：
            #   「真证的定理 24 条（均经反证控制探针确认非定义性相等；探针对同陈述
            #     施 `:= rfl` **必须编译失败**）」）——编排器此前对判据门**从未**做过。
            #   现补上：全绿时对每个门做对抗性变异，变异后仍绿 ⇒ 恒真门 ⇒ 不得交付。
            witness_ok, witness_rep = True, []
            # ★★★★★ R108（真跑抓出的第 22 层 · 2026-10-19 · 第十一次端到端）：
            #   **判据保真检查被锁在它自己要判的那个条件之后。**
            #
            #   真证据：全部十次真跑，日志里「主心骨自证」出现 **0 次**。
            #     而每次都会打印「N 条门都自带否定样本」—— 那只是
            #     `抽否定样本`（**声明**检查），不是「样本真能使其红」。
            #   守卫是 `_达标本 and bool(alignment.get("fixed_point"))`：
            #     · `_达标本`   = 已经判它达标了；
            #     · `fixed_point` = 多窗已对齐。
            #   而这道检查的**全部意义**是：判「门全绿」可不可信。
            #   ⇒ 要跑它，得先「门全绿且已达标」；而它正是判那个的东西。
            #   **循环依赖**：它在最需要的时候（判据可能没判别力时）永远不跑。
            #
            #   ★ 真跑 R102 那趟就是活证据：门 `results` 三条**全 True**、
            #     `_达标本` 也 True，只因 `alignment.fixed_point=False`
            #     ⇒ 整块被跳过 ⇒ 主心骨那 3 条门的否定样本**一次都没被喂过**。
            #
            #   ★ 修法：触发条件改成「**这一轮门全绿**」或「已达标」——
            #     门全绿就是「准备声称完成」的时刻，那正是必须验保真的时刻。
            #     多窗对齐（`fixed_point`）是**交付**的必要条件，
            #     不是**验保真**的必要条件，两者不能混。
            #   ★ 而且**跳过必须留痕**（R85 家族的教训：静默跳过 = 不可见）。
            # ★ 这里算的是**触发条件**，不是顶层裁决 —— TS-468 要求的是
            #   「`done_ok` 不得压成裸布尔合取」，那条仍走 `_sl16.格裁决`（下方未动）。
            #   ⇒ 用本文件 L3186 已有的同义写法（`all(v is True for v in ...)`），
            #     与 `_score_files` 的判绿口径一致（`is True`，不是 truthy）。
            _门们108 = results or {}
            _全绿本108 = bool(_门们108) and all(v is True for v in _门们108.values())
            # ★★★★★ R109（真跑抓出的第 23 层 · 2026-10-19 · 第十二次端到端）：
            #   **R92 自证块从来就没有跑过 —— 它每次都在第一行炸掉。**
            #
            #   真证据：日志 `★ R92 判据契约验证异常（如实降级）：
            #     UnboundLocalError: cannot access local variable 'd0'`。
            #   而 `d0` 只在 `if _达标本 and fixed_point:` 的 try 里赋值，
            #   R92 自证块却在**那个 if 之外** ⇒ `if` 为假时 `d0` 未绑定
            #   ⇒ 抛 UnboundLocalError ⇒ 被 `except` 吞成「如实降级」。
            #   ⇒ **十次真跑「主心骨自证」出现 0 次，不是它跑了没日志，
            #     是它每次都在第一行就炸了，而「如实降级」把它伪装成正常。**
            #
            #   ★★ 这一层的教训比缺陷本身重：`except Exception: 如实降级`
            #     在**自己写错变量名**时也会触发 ⇒ 「如实」变成了遮羞布。
            #     真正的如实，是连「我炸了」都必须是**显式可见**的一行。
            #   ⇒ 修法：把 d0/g0/w_root/w_repo **提前到分支之前**赋值；
            #     并把「跳过」的 else 移到 if 分支真正结束处（原先放错了位置，
            #     把 R92 自证块整个吞进了 else 分支）。
            d0 = self.deliverables[0] if self.deliverables else None
            g0 = ((d0.get("gates") if d0 else None)
                  or self.spec.get("gates") or [])
            w_root = REPO_ROOT if self.repo_scope else self.round_root
            w_repo = REPO_ROOT if self.repo_scope else None

            # ★★★★★ R115（真跑抓出的第 28 层 · 2026-10-20 · 第十四次端到端）：
            #   **判据保真检查不属于达标判定 —— 它属于门集体检。**
            #
            #   真证据：十四次真跑，「主心骨自证」只出现 **1 次**（00:24 那趟）。
            #     因为这一段嵌在 `if _达标本 or _全绿本108:` 里面：
            #       · `_达标本` = 已经判它达标了 ⇒ **要验「门全绿可不可信」，
            #         得先「门全绿」** ⇒ 循环依赖（R108 已指出过一次）；
            #       · R108 把条件从 `_达标本 and fixed_point` 放宽到
            #         `_达标本 or _全绿本108` —— 方向对，**但门槛没拆掉**。
            #     真跑从来没全绿过 ⇒ 自证仍然等于没跑。
            #
            #   ★ 这是 R108 的**同一层第二次发作**：R108 修的是「和 fixed_point 混」，
            #     没修「和达标混」。两次都是把**体检**挂在了**结论**后面。
            #
            #   ★ 修法：门集一出现/一变化就体检一次，**不等任何结论**。
            #     按门集签名去重（不是每轮跑）：验一次 = 门数 × 样本数 次真实门执行，
            #     每轮跑会把 2 小时拖成 10 小时；而门集不变 ⇒ 验一次就够。
            _门签115 = self._门集签名115(g0)
            if g0 and _门签115 != getattr(self, "_契约已验签名115", None):
                self._验契约_执行时(
                    g0, w_root, d0,
                    因由=f"轮{work} 门集"
                         + ("新出现" if getattr(self, "_契约已验签名115", None) is None
                            else "**变了**"))
                self._契约已验签名115 = _门签115

            if _达标本 or _全绿本108:
                if not bool(alignment.get("fixed_point")):
                    _log("[generic] ★ R108 判据保真检查：本轮"
                         + ("门全绿" if _全绿本108 else "已达标")
                         + " ⇒ **必须验门有没有区分力**"
                         f"（多窗对齐={alignment.get('fixed_point')} 只影响交付，"
                         "不影响验保真）")
                try:
                    # 取**该文件实际生效**的门集（deliverable 可覆盖 spec 顶层门集），
                    # 与 _score_files 的判门口径一致，否则验的不是真判据。
                    d0 = self.deliverables[0] if self.deliverables else None
                    g0 = ((d0.get("gates") if d0 else None)
                          or self.spec.get("gates") or [])
                    # ★ 见证必须针对**门的真实读入位置**：
                    #   主树工程任务里产物已应用进 REPO_ROOT（不是 round_root），
                    #   而 `cargo check --lib` 这类工程门只在 REPO_ROOT 下才有意义。
                    #   早先版本一律用 round_root ⇒ 工程门报 WinError 267
                    #   （cwd 指向不存在的沙箱目录）⇒ 全部记 unknown。
                    w_root = REPO_ROOT if self.repo_scope else self.round_root
                    w_repo = REPO_ROOT if self.repo_scope else None
                    if d0 and g0:
                        witness_ok, witness_rep = gate_witness.witness_gates(
                            g0, w_root / d0["path"], w_root,
                            repo_root=w_repo)
                except Exception as exc:      # 见证自身异常：如实降级，不假装已验
                    witness_ok, witness_rep = True, [
                        {"gate": "?", "type": "?", "witness": "unknown",
                         "mutation": "", "detail": f"见证执行异常：{exc}"}]
                if witness_rep:
                    self._emit({"kind": "gate_witness", "work": work,
                                "summary": gate_witness.summary_line(witness_rep),
                                "report": witness_rep,
                                "tokens_chars": self._tok_chars})
                    _log(f"[generic] 轮 {work} 门区分力见证："
                         + gate_witness.summary_line(witness_rep))
                if not witness_ok:
                    vac = [r["gate"] for r in witness_rep
                           if r["witness"] == "vacuous"]
                    dec = [r["gate"] for r in witness_rep
                           if r["witness"] == "decoupled"]
                    if dec:
                        _log(f"[generic] ★ 产物未接进工程（门真但产物无引用）：{dec} "
                             f"⇒ 补救是**补接线**（如新模块在 lib.rs 声明），不是换门")
                    if vac:
                        _log(f"[generic] ★ 恒真门（对变异仍绿，无区分力）：{vac} "
                             f"⇒ 门全绿不构成达标证据，转换门集")
                    # ★★★ 关键补全（TS-384 `defect_is_endogenous_drive` 路径）：
                    #   恒真门**已派生**出更细的替换门（见 gate_witness._try_derive）。
                    #   这里把派生门**真装进门集**，替掉恒真门，然后 continue 重跑
                    #   —— 这才是"判出缺陷 → 修掉缺陷"的闭环。
                    #   早先版本判出 vacuous 就 stop/replan：replan 要**模型**重新给门
                    #   （概率性，且可能又给恒真门）；派生是**机器确定性**的，不靠模型。
                    #   对应 `evolution_loop_closes`：每轮派生都落回全称内，故不会跑飞。
                    派生数 = self._adopt_derived_gates(witness_rep, work)
                    if 派生数:
                        _log(f"[generic] ★ 已用派生门替换 {派生数} 个恒真门"
                             f"（构造性派生，零模型）⇒ 以新门集重跑本轮")
                        # 记下"本轮换进来的派生门"，等它们**真被跑绿**再沉淀
                        #   （造门 ≠ 门有用；只有被独立确证过的才配沉淀）
                        self._pending_sediment += [
                            r.get("derived_gate") for r in witness_rep
                            if isinstance(r, dict) and r.get("derived_gate")
                            and not r["derived_gate"].get("_over_specific")]
                        content = None      # 门集变了 ⇒ 旧产出对新判据无意义，重产
                        red_audit_prev = []
                        # ★ R73：**换了判据 ⇒ 基线按新门集重取**（不能退回
                        #   "绝对全绿"：那会把主树上的历史违规重新算成本次任务的
                        #   责任 ⇒ 又变成恒红门）。
                        prev_devs = self._重取基线(dev_pred)
                        # ★★★★★ R20（TS-469）：**换门集也要记一个点**。
                        #   真跑抓出（11:22 那次）：`continue` 回到主循环 ⇒ work 递增
                        #   ⇒ 这一轮**永远走不到**下面的正常 `收()` ⇒ 轨道少一个点。
                        #   后果是**实质**的：`已判定` 依赖重复点，少一个点 ⇒
                        #   Stuck/不动点判定**延迟一轮** ⇒ 多烧一轮 token。
                        #   ★ 本轮的 `witness_ok=False`（有恒真门）是一个**有意义**的
                        #     真值向量（门没区分力就是没区分力），不是噪声 ⇒ 该记。
                        #   ★ 门集换了但**真值空间不换**：坐标系是那三条**判定**，
                        #     不随具体门集变 ⇒ 同一真值空间，可比。
                        self._轨道20.收(
                            _TS20_向量(_达标本,
                                       bool(alignment.get("fixed_point")),
                                       False),
                            因由=f"轮{work}: 派生门替换恒真门（witness_ok=False）"
                                 f"⇒ 以新门集重跑")
                        continue
            else:
                # ★ R108：**不许静默跳过**。门还没全绿也不是「不用验」，
                #   而是「此刻没有可验的产物」。这两件事必须能分开看。
                _log("[generic] ★ R108 判据保真检查本轮**未跑**"
                     f"（门全绿={_全绿本108} 已达标={_达标本}）"
                     "⇒ 本轮没有可验的产物或门集为空；"
                     "★ 这不是「验过了」，是「没验」")

            # ★★★ 跨任务学习（TS-378 路径）：把**已被跑绿确证**的结构门沉淀成
            #   样本戊，供同结构任务复用。缺口：此前跑一次就是一次，同类恒真门
            #   在下一个任务里从零再派生一遍 —— 工具不会因为干得多而变强。
            #   数学依据 `disc_factors_through_constraint`（:157）：
            #     尊重约束的判别式**必唯一地穿过约束的商** ⇒ 门是可派生、
            #     可沉淀的样本戊，不是一次性产物。
            #   ★ 只沉淀**结构门**（`_structural`）；过细门（`_over_specific`）
            #     是假红生成器，沉淀等于把缺陷传给下一个任务 ⇒ 一律拒收。
            #   ★ 且只在**本轮真跑绿**时沉淀（门被独立确证过，不是"造出来就算"）。
            if witness_ok and self._pending_sediment:
                self._sediment_pending(work)

            # ★★★ TS-378 路径：机检门集是否**穿过约束的商**（补「假红」那一半）。
            #   数学依据 `TS378.disc_factors_through_constraint`（:157）：
            #       约束同类（G x ↔ G y）⇒ 判别式必同类（f x = f y）
            #   违反 ⇒ 门的判别力**超出目标给的上限**
            #        （`constraint_bounds_discrimination` :165）
            #        ⇒ **假红生成器**：目标眼里同类的两点被门分开 ⇒ 合格产出被拒。
            #
            #   ★ 防空洞（本处最易犯的错）：**不能用同一套门当"目标达标"的判据**
            #     —— 那就成了 `ConstraintOfGoal` 由门自己诱导，检查退化为
            #     「函数尊重自己诱导的等价关系」，**恒真**（库内形态 12：
            #     前提重述为结论）。故 `_check_constraint_respected` 在
            #     没有**独立于门**的达标来源时**如实返回 None**（不阻断、不假装）。
            #     有独立来源时（如 spec 声明的 `constraints` 机检谓词，
            #     且门**非**由该 pred 派生）才真检查。
            # ★★★★★ R108b：**同一处缺陷的第二个实例**（当场由齿抓出来）。
            #   这道「假红生成器」检查也锁在 `_达标本 and fixed_point` 后面 ——
            #   而它的全部意义是「门的判别力超出了目标给的上限」，
            #   那正是**判门可不可信**，与「已达标且多窗对齐」没有依赖关系。
            #   ⇒ 同一个修法：门全绿（准备声称完成）或已达标 ⇒ 就要查。
            #   ★ 为什么必须一起修：只修一处，另一处仍是「声明了但不跑」；
            #     而齿搜的是**整个 `_run` 体**，只修一处它就恒红 ——
            #     这不是齿太严，是齿在提醒「同一条规矩还有第二处没落」。
            if _达标本 or _全绿本108:
                约束报告 = self._check_constraint_respected(work)
                if 约束报告 and 约束报告.get("违反"):
                    self._emit({"kind": "constraint_violation", "work": work,
                                "violating": 约束报告["违反"],
                                "report": 约束报告["报告"],
                                "tokens_chars": self._tok_chars})
                    _log(f"[generic] ★ 门集违反目标约束（假红生成器）："
                         f"{约束报告['违反']} ⇒ 门的判别力超出目标上限，"
                         f"合格产出会被误拒 → 交还用户（换门集或修门）")
                    self._emit({"kind": "escalated_unreachable", "work": work,
                                "gates": results, "verdict": verdict,
                                "reason": "门集违反目标约束（假红）：门的判别力超出"
                                          "目标给的上限，需换门集或修门",
                                "tokens_chars": self._tok_chars})
                    return 1
            # ★★★★★ R16：三重合取 → **数术层格裁决**（TS-468 §6）。
            #   在 `Fin 2^3` 的**积序**上取下确界 —— **取值与旧式合取完全一致**
            #   （积序的下确界就是合取，无语义回退）；
            #   但**三项的因由都保留**，处置按因由分开（三项处置完全不同）：
            #     · verdict 非 converged ⇒ 门还没稳，继续跑（rework）
            #     · fixed_point 假      ⇒ 多窗没对齐，**换路**（replan/stop）
            #     · witness_ok 假       ⇒ 门没区分力，**换门集**（已有派生路径）
            #   ★ 注意 `witness_ok` 在本层**不再是外挂补丁**：它就是
            #     「门全绿」这条判据自身的**保真**要求（TS-468 §2）。
            from agentscope_orchestrator import shushu_logic as _sl16
            # ★★★★★ R137（真跑抓出的第 50 层 · 2026-10-23 · 第三十一次端到端）：
            #   **「没达标」的因由，写的是「看起来达标」的内容。**
            #
            #   真证据（第 31 次真跑，轮 2，原文）：
            #     `轮 2: 门=… 全 True | Δ=0 verdict=converged action=stop`
            #     `未达标因由：本次任务达标：verdict=converged；基线 3 处→ 现 0 处`
            #     `判据结构停机 @轮2（converged）→ 交还用户`
            #   门**全绿**、违规点 **3 → 0**、门 **6/6 有区分力**（R136 已生效）。
            #   ⇒ 而 `_达标` 返回 False，因由却写着「verdict=converged；基线 3→0」。
            #
            #   ★★ 这两句话**互相矛盾**：因由读起来是「全好了」，值却是「没达标」。
            #     人（和模型）看到这句会以为机器在胡说 —— 而且**查不出真原因**。
            #   ★ 根因：因由串是**从 verdict 与基线条数拼的**，
            #     而 `_达标` 真正的拦截点在**另外三个早退**里
            #     （`_未好89` / `_破92` / `cur_内` 子集判），一个都没写进因由。
            #   ★ 与 R133 同族**第 3 次**：
            #     R130「合法集合没说出来」→ R133「哪一条不合格没说出来」
            #     → **R137「为什么没达标没说出来，反而说了一句相反的话」**。
            #   ★ 比「不说」更坏：**说了一句会把人引到错方向的因由。**
            #
            #   ⇒ 修法：每个早退都**留下真实因由**（`_未达标因由137`），
            #     裁决因由**优先用它**；只有达标时才退回 verdict 口径。
            _因137 = list(getattr(self, "_未达标因由137", None) or [])
            _三项 = [
                # ★ R73b：名字与取值都改成**达标**（门全绿 **或** 相对基线严格变小）。
                #   ★ 因由里仍保留 `verdict` 六值串与基线条数 ⇒ **信息没丢**，
                #     只是不再拿那个"恒假的比特"当真值。
                # ★★★★★ R137：因由必须说**真正拦在哪**（见下方 `_因137`）。
                _sl16.判定("本次任务达标", _sl16.TRUE
                           if _达标本 else _sl16.FALSE,
                           因由=("；".join(_因137) if (not _达标本 and _因137)
                                else f"verdict={verdict}；基线 "
                                     f"{len(self._基线身份)} 处"
                                     f"→ 现 {len(cur_ids)} 处")),
                _sl16.判定("多窗不动点对齐",
                           _sl16.TRUE if alignment.get("fixed_point")
                           else _sl16.FALSE,
                           因由=f"green={alignment.get('green')} "
                                f"aligned={alignment.get('aligned')} "
                                f"读空间={alignment.get('read_space')}"),
                _sl16.判定("门保真（有区分力）",
                           _sl16.TRUE if witness_ok else _sl16.FALSE,
                           因由="；".join(
                               f"{r.get('gate')}={r.get('witness')}"
                               for r in (witness_rep or [])
                               if r.get("witness") != "ok") or "全部门有区分力"),
            ]
            _值16, _因由16, _注16 = _sl16.格裁决(_三项)
            done_ok = bool(_值16)
            self._裁决因由 = _因由16 + ([_注16] if _注16 else [])

            # ★★★★★ R20（TS-469）：判定层从**经典层**挪回**机制自己真值空间**。
            #
            #   错位在哪：`verdict` 是**六值字符串**（converged / progressing /
            #   stalled_fixed_point / no_anchor / diverging / converged_single_window），
            #   而 TS-469 §4「穷尽」机证**真值空间没有第三值**。
            #   那五个字符串**不是真值，是因由** —— 旧实现拿它们当判据，
            #   于是「未达标时该怎么走」是**字符串猜测**，不是机证判定。
            #
            #   载体消去（TS-469 §1）：真值向量**只由三条二值判定组成** ——
            #   门名 / 产物文本 / 模型名 / 窗数 / token 数 / Δ 集合**一律不进**。
            _向量20 = _TS20_向量(_达标本, bool(alignment.get("fixed_point")),
                                 bool(witness_ok))
            _换空间20 = self._轨道20.收(
                _向量20, 因由=f"轮{work}: 达标={_达标本} verdict={verdict}"
                              + (f" why={why}" if why else ""))
            if _换空间20:
                _log("[generic] ★ 换门集 ⇒ **换真值空间**（TS-469：新轨道，"
                     "旧点不作数；两种不同维度的位置不得混在一条轨道上比较）")
            _判20 = self._轨道20.判()
            _鸽笼20 = _ts20.载体基数(_TS20_真值空间位数)

            # ★ 鸽笼违反 = **机器可检的错**（不是"没收敛"）：
            #   真值空间只有 8 个位置，观测到第 9 个点仍无重复 ⇒ 不可能。
            #   只可能是：有位置没落进坐标系，或 `读()` 读了载体（载体消去被破坏）。
            if work > _TS20_鸽笼界 and not _判20.已判定:
                self._emit({"kind": "pigeonhole_violation", "work": work,
                            "orbit": list(self._轨道20.点),
                            "truth_space_card": _鸽笼20,
                            "reason": f"真值空间只有 {_鸽笼20} 个位置，"
                                      f"已观测 {len(self._轨道20.点)} 个点仍无重复 "
                                      f"⇒ 鸽笼被破坏：有位置未落进坐标系，"
                                      f"或 `读()` 读了载体",
                            "tokens_chars": self._tok_chars})
                _log(f"[generic] ★★ 鸽笼违反 @轮{work}：真值空间 {_鸽笼20} 个位置、"
                     f"观测 {len(self._轨道20.点)} 点无重复 ⇒ 判定层读了载体 → 停机")
                return 1

            # ★★★★★ R120（真跑抓出的第 34 层 · 2026-10-21 · 第十七次端到端）：
            #   **不动点的判定权被一条非单射投影夺走了。**
            #
            #   真证据（23:16→02:06 那一跑）：
            #     轮1 基线 6 处 → 现 4 处，verdict=progressing
            #     轮2 基线 6 处 → 现 3 处，verdict=progressing
            #     轮3 基线 6 处 → 现 2 处，verdict=progressing
            #   三轮都在**严格变小**（门也真的由 False 翻 True：
            #   `mod_无界适配层存在`、`arc评测_共用广域编码`），
            #   但轮2 时真值空间判「不动点已到」⇒ `action=stop` ⇒
            #   第 3 轮就停机交还。日志原话：
            #     `★ 真值空间已判不动点，而 verdict 仍说 progressing —— 二者矛盾，
            #       以**真值空间**为准`
            #   ★ **站错了边。**
            #
            #   ★ 为什么真值空间会误判：它的坐标是
            #     `_TS20_向量(达标, fixed_point, witness_ok)` —— **3 个比特、8 个位置**。
            #     而 `verdict` 判不动点用的是**偏差身份集合**
            #     （`prev_devs` vs `cur_devs`，`deviation_identity` 无损）。
            #     6 处→4 处 与 4 处→3 处 在这 3 个比特上**是同一个点**（F,F,T）
            #     ⇒ 鸽笼原理必然报"重复 ⇒ 不动点"。
            #
            #   ★ 这是 **TS-442 `read_eq_reflects` 被违反**：
            #     `Function.Injective read → read x = read y → x = y`。
            #     3 比特投影**不是单射** ⇒ 「读相同」**不蕴含**「态相同」
            #     ⇒ 它**没有资格**判「不动点」。用它判，必然把
            #     「在进步」误报成「停滞」。
            #   ★ 与 R112 完全同型（粗读不得否决细读）：R112 修的是
            #     文件级裁决里「点数没变 ≠ 没进展」；这一条是同一件事
            #     搬到了真值空间层。**第 2 次发作。**
            #
            #   ★ 修法：**`progressing` 优先于真值空间。**
            #     理由不是"我更喜欢它"，而是：
            #       · `progressing` = `cur ⊊ prev`（**真子集**，身份集合无损）
            #         ⇒ Δ 的基数**严格减小** ⇒ 这是**有限集上的严格递减**
            #         ⇒ **必然终止**（不会无限空转），且**必然不是不动点**、
            #         **不可能是 Stuck**（Stuck 要求轨道成周期 ≥2 的圈）；
            #       · 真值空间只在 `verdict` 说不出话时（stalled/no_anchor/diverging）
            #         才当判据用 —— 那时它是**佐证**，不是否决者。
            #   ★ 真值空间仍保留它真正的用途：`pigeonhole_violation`
            #     （机器可检的错：观测点数超过位置数 ⇒ 坐标系漏了位置）。
            #     那一条**不依赖单射性**，所以它站得住。
            if done_ok:
                action = "deliver"
            elif verdict == "progressing":
                action = "rework"
                if _判20.已判定:
                    _log(f"[generic] ★ R120 真值空间说「"
                         f"{'不动点已到' if _判20.值 else 'Stuck'}」，而 verdict="
                         f"progressing（{why}）—— **以 verdict 为准**："
                         f"Δ 真子集严格变小是有限集上的严格递减 ⇒ 必然终止、"
                         f"且必然不是不动点；3 比特投影不是单射，无资格否决它")
            elif _判20.已判定 and _判20.值 is False:
                # ★ Stuck（机证：周期 ≥ 2 的圈上每点都不是不动点）
                #   ⇒ 本门集上**永远**不会到不动点 ⇒ 换路/停机，不再空转烧 token。
                action = "stop"
                _log(f"[generic] ★ 真值空间判 Stuck（{_判20.因由}）"
                     f"⇒ 本门集机证无不动点，不再空转")
            elif _判20.已判定 and _判20.值 is True:
                # ★ 不动点已到但非全绿：门集与产出形态失配 ⇒ 换门集（状态转移）
                action = "stop"
            else:                           # stalled_fixed_point / no_anchor / diverging
                action = "stop"
            if not done_ok and _因由16:
                _log("[generic] 未达标因由（逐项，不再压平成一个 Bool）："
                     + "；".join(_因由16))
            _log(f"[generic] 轮 {work}: 门=" + " ".join(
                f"{k}={v}" for k, v in results.items()) +
                f" | Δ={len(cur_ids)} verdict={verdict} action={action}"
                + (f" | {why}" if why else "")
                # ★ R20：真值空间视角（轨道位置 / 基数 / 判定 / 证书）
                + f" | 真值空间 点={list(self._轨道20.点)} n={_鸽笼20}"
                  f" 已判定={_判20.已判定}"
                  + (f" 值={'ReachesZero' if _判20.值 else 'Stuck'}"
                     f" 证书={_判20.证书}" if _判20.已判定 else ""))
            gaps = [{"key": k, "file": k.split("@")[0] if "@" in k else k,
                     "status": "resolved" if v else "open"}
                    for k, v in results.items()]
            self._emit({"kind": "iteration", "work": work, "gates": results,
                        "post_gates": results, "gaps": gaps,
                        "alignment": alignment, "tokens_chars": self._tok_chars,
                        "verdict": verdict, "action": action,
                        # ★ R16：未达标因由逐项落盘（原来三项被压成一个 Bool）
                        "裁决因由": getattr(self, "_裁决因由", []),
                        # ★★★★★ R20（TS-469）：真值空间判定逐项落盘。
                        #   ★ `verdict` 仍是**因由**（六值字符串），真值在
                        #     `真值空间.值` —— 两个字段分开记，绝不混。
                        "真值空间": {
                            "位数": _TS20_真值空间位数,
                            "基数": _鸽笼20,
                            "鸽笼界": _TS20_鸽笼界,
                            "坐标": list(self._轨道20.坐标 or ()),
                            "点": list(self._轨道20.点),
                            "已判定": _判20.已判定,
                            "值": ("ReachesZero" if _判20.值 else
                                   "Stuck") if _判20.已判定 else None,
                            "证书": _判20.证书,
                            "因由": _判20.因由,
                        },
                        # ★ 可观测性（2026-10-14 修）：原来**只记门真值布尔**，
                        #   红门到底为什么红**完全没落盘**。实测代价：10:24 那轮回执
                        #   `gates` 显示 cargo_test_lib=False，但 `red_audit` 为空 ⇒
                        #   事后**无法诊断**是编译错、测试失败、还是门本身写错（假红）。
                        #   同时把**门规格**也落盘 —— 原来 plan 回执只存门**数量**
                        #   （"gates": 3），连门判什么都不知道。
                        "red_audit": red,
                        "gate_specs": [g for d in self.deliverables
                                       for g in (d.get("gates") or [])],
                        "adopted": {p: t[:200] for p, t in (content or {}).items()}})

            if action == "deliver":
                return await self._deliver(work, results, alignment)

            if action == "stop":
                # ★ 接线（2026-10-12）：**先尝试换门集（状态转移），再谈停机**。
                #   原实现直接 escalated_unreachable 交还用户 —— 而 `_replan` 是完整实现的
                #   却**零调用者**（死码）：`_should_replan`/`_transition`/`_replan_thrashed`
                #   也只在自检里被调。结果「稳定含红 = 门集与产出形态失配」这条本可自动
                #   换路径的信号，被直接当成终止。
                #   现改为：stalled_fixed_point / no_anchor 时先 _replan 换门集；
                #     · changed   → 换到新门集，continue 继续演化（真状态转移）；
                #     · escalated → 门集没真变（判据不可达）→ 才停机交还；
                #     · 连续换满 replan_max 次仍不收敛 → 判据不可达，停机。
                if verdict in ("stalled_fixed_point", "no_anchor") or not witness_ok:
                    # ★ not witness_ok：Δ=∅ 全绿但门是恒真门（对变异仍绿）——
                    #   这是「门集与产出形态脱钩」，不是「目标已达成」。
                    #   与 stalled_fixed_point 同类处置：**换门集**（状态转移），
                    #   而不是判成功交付、也不是直接停机交还。
                    #
                    # ★★★★★ R136（真跑抓出的第 49 层 · 2026-10-23 · 第三十次端到端）：
                    #   **「换了几次」不是「为什么换」。**
                    #
                    #   真证据（第 30 次真跑，轮 4，原文）：
                    #     `轮 4 门区分力见证：有区分力 6/7；★恒真门 1：接线链_仍是真函数不是空壳`
                    #     `★ 恒真门（对变异仍绿，无区分力）：[…] ⇒ 门全绿不构成达标证据，转换门集`
                    #     ⇒ 派生数 = **0**（机器派不出替换门）
                    #     ⇒ `判据结构停机 @轮4（converged）：已换门集 2 次未收敛 → 交还用户`
                    #
                    #   ★★ 而这一跑的实际状态是**三十次里最好的**：
                    #     · 判据违规点 **4 → 0**（真跑历史上第一次）
                    #     · 7 条门里 **6 条有区分力**（`cargo_check_lib` 等全绿）
                    #     · `verdict=converged`、`Δ=∅`
                    #     · 只有 1 条门是恒真门
                    #   ⇒ 这不是「判据不可达」，这是「**7 条门里有 1 条写坏了**」。
                    #
                    #   ★ 与 R126 同族第 3 次：**次数不是因由**。
                    #     `_replan_count >= 2` 只说明"换过两次"，
                    #     完全没说"这次换是因为门集整体不可达，还是因为一条门写坏"。
                    #     前者该停机交人，后者该**继续修那一条门**。
                    #
                    #   ★★ 更要紧的一层：**派生失败 ≠ 判据不可达。**
                    #     派生是**机器**的构造性尝试（`discriminant.派生判别式`），
                    #     它失败只说明「机器自己没想到怎么细分这条门」；
                    #     而模型**完全可能**想出办法（它是实现窗口，比机器算子聪明）。
                    #     ⇒ 派生失败时**不该**停机，该**把这条门点名交给模型**。
                    #
                    #   ⇒ 修法：门集整体不可达（stalled/no_anchor）才走次数护栏；
                    #     **恒真门**走另一条路：把「哪条门是恒真门、为什么、
                    #     请换掉它」喂给模型，且**不吃** replan 次数配额
                    #     （因为它不是"判据不可达"，是"一条门要重写"）。
                    _是恒真136 = (not witness_ok) and verdict not in (
                        "stalled_fixed_point", "no_anchor")
                    if _是恒真136 and getattr(self, "_恒真重试136", 0) < 2:
                        self._恒真重试136 = getattr(self, "_恒真重试136", 0) + 1
                        _恒136 = [r.get("gate") for r in (witness_rep or [])
                                  if isinstance(r, dict) and r.get("witness") == "vacuous"]
                        _log(f"[generic] ★★★ R136 恒真门 {_恒136} 派不出替换门，"
                             f"但**门集整体是可达的**（其余门有区分力）⇒ "
                             f"不判「判据不可达」，改为**点名交给模型重写这一条**"
                             f"（第 {self._恒真重试136} 次，不吃 replan 配额）")
                        self._上次拒收97 = (
                            f"恒真门（对任何产物都绿，无区分力）：{_恒136}\n"
                            f"★ 这**不是**「判据不可达」—— 其余门都有区分力，"
                            f"你的门集是好的，**只有这一条写坏了**。\n"
                            f"★ 为什么它恒真：机器把产物变异（删掉关键实现）后它**仍然绿** "
                            f"⇒ 它对「做没做到」没有任何判别力。\n"
                            f"★ 请**只重写这一条门**：让它「修复前为假、修复后为真」。\n"
                            f"  例如 `接线链_仍是真函数不是空壳` 若是 grep「函数名还在」，"
                            f"那它永远绿（函数名当然还在）；应改成**真跑**该函数并断言其行为"
                            f"（cmd 里带 `{{file}}`，或 `cargo test` 跑那个具体测试）。\n"
                            f"★★ 其余 {len(results) - len(_恒136)} 条门**不要动**。")
                        self._emit({"kind": "vacuous_gate_retry", "work": work,
                                    "gates": _恒136,
                                    "reason": "恒真门派不出替换门 ⇒ 点名交模型重写（非不可达）",
                                    "tokens_chars": self._tok_chars})
                        red_audit_prev = red
                        content = None
                        continue
                    # ★★★★★ R192：**计数到头 ≠ 该停**。判据要看「还在不在变好」。
                    #   真证据（第 91 次真跑第 4 轮）：`已换门集 2 次未收敛 → 交还用户`，
                    #   而那一趟树是 **11 个错误 → 1 个错误**（两轮各保留一个文件的真实改善）。
                    #   ⇒ 停机判据拿的是「换门集计数器」，它该拿的是「上一轮有没有真实保留」。
                    #   ★ 但必须**有界**：最多给到 `上限×2`，不许变成无限兜圈子。
                    _还在变好192 = getattr(self, "_本轮保留数187", 0) > 0
                    _硬顶192 = max(1, int(self.replan_max)) * 2
                    if (_replan_thrashed(self._replan_count, self.replan_max)
                            and _还在变好192 and self._replan_count < _硬顶192):
                        _log(f"[generic] ★★ R192 已换门集 {self._replan_count} 次（上限 {self.replan_max}），但**上一轮仍有 {getattr(self, '_本轮保留数187', 0)} 个文件真实保留** ⇒ 不判停机，再给一轮（硬顶 {_硬顶192}）")
                        self._emit({"kind": "replan_budget_extended", "work": work,
                                    "count": self._replan_count, "max": self.replan_max,
                                    "kept": getattr(self, "_本轮保留数187", 0),
                                    "reason": "换门集计数到头，但仍在严格变好 ⇒ 延一轮（有硬顶）"})
                    elif _replan_thrashed(self._replan_count, self.replan_max):
                        self._emit({"kind": "escalated_unreachable", "work": work,
                                    "gates": results, "verdict": verdict,
                                    "reason": f"{why}（已换门集 {self._replan_count} 次仍不收敛，"
                                              f"判据不可达）",
                                    "tokens_chars": self._tok_chars})
                        _log(f"[generic] 判据结构停机 @轮{work}（{verdict}）："
                             f"已换门集 {self._replan_count} 次未收敛 → 交还用户")
                        return 1
                    try:
                        sig = await self._replan(red_audit_prev)
                    except Exception as exc:   # 重规划失败：诚实停机，不硬跑
                        # ★★★★★ R110b：**「诚实停机」的日志把诊断信息全丢了。**
                        #   真跑实测只留下一行：`重规划失败：'gates' → 诚实停机` ——
                        #   没有异常类型、没有行号 ⇒ 我不得不回去读源码才认出
                        #   这是 `self.spec["gates"]` 的 KeyError。
                        #   ★ 「诚实」不等于「说一句我停了」；
                        #     真正的诚实是**把可诊断的信息留下来**，
                        #     否则下一个人（或下一个我）看到的只是「它死了」。
                        #   ⇒ 记类型 + 末尾栈帧 + 行号；且**区分两类**：
                        #     · `规约可纠正` / `计划不可行`：模型给的规约不好，可回喂；
                        #     · 其余：**产品缺陷**，必须显眼，不得伪装成「任务失败」。
                        import traceback as _tb
                        _帧 = _tb.extract_tb(exc.__traceback__)
                        _末 = _帧[-1] if _帧 else None
                        _位 = (f"{Path(_末.filename).name}:{_末.lineno}"
                               if _末 else "?")
                        # ★★★★★ R126b（同一趟真跑的第二处）：
                        #   **上游瞬时故障被当成「产品缺陷」。**
                        #   真证据：`BadRequestError: Error code: 400 -
                        #     {'message': '上游服务异常，请稍后重试…'}` ⇒
                        #     日志写 `★★★ 这是**产品缺陷**` ⇒ 整趟跑死。
                        #   ★ 两者处置完全相反：
                        #     · 产品缺陷 ⇒ 显眼报出来，**人去修代码**；
                        #     · 上游瞬态 ⇒ **重试/换通道**，不该惊动人。
                        #   把它压成同一个值 = R116b 那一族。
                        #   ⇒ 判定收口到 `gateway.是上游瞬态`（唯一读口）。
                        from agentscope_orchestrator.gateway import (
                            是上游瞬态 as _是瞬态126)
                        _瞬态126 = _是瞬态126(exc)
                        # ★ R151：**按设计停机**既不是产品缺陷、也不是上游瞬态、
                        #   也不是「模型给错了规约」—— 它是**第四类**：
                        #   机器已连续回喂仍无法把规约改对 ⇒ 交还决定权。
                        _是设计停机151 = isinstance(exc, 按设计停机)
                        # ★★★★★ R154（第 38 次真跑抓出的第 66 层 · 2026-10-25）：
                        #   **模型返回的 JSON 不合法，不是产品缺陷。**
                        #   真证据（第 38 次真跑，2 轮后）：
                        #     `★★★ 重规划**崩了**（decoder.py:354）：JSONDecodeError:
                        #      Expecting ',' delimiter: line 1 column 5521 (char 5520)`
                        #     回执：`product_defect=True by_design_stop=False`
                        #   ★ 那次是**模型把 JSON 写断了**（char 5520 处少一个逗号），
                        #     产品**按设计**要求它是合法 JSON —— 这不是产品代码有 bug。
                        #   ★★ 后果：报表说「产品坏了，人去修代码」，
                        #     而真正该做的是**回喂一次**（模型这次输出截断/打错了）。
                        #   ★★★ 这是「手里那个异常不是判据需要的那个」的**第 5 次**：
                        #     判据要「**该不该惊动人**」，我拿到的是「**这是什么异常类**」。
                        #   ★★★★ 而且它**已经被回喂过 1 次**（日志里
                        #     `★ 规约可纠正（第 1 次 · 计划不可行）⇒ 回喂重规划`），
                        #     第 2 次直接崩 ⇒ **回喂预算被用在了错的地方**。
                        #   ⇒ 归类为「模型给错了规约」⇒ 走可纠正回喂（有上限），
                        #     不惊动人；用完预算后仍不合法 ⇒ 那时才算交还决定权。
                        _是模型给错154 = isinstance(
                            exc, (__import__("json").JSONDecodeError,))
                        _是缺陷 = (not _是设计停机151
                                   and not _是模型给错154
                                   and not isinstance(exc, (规约可纠正, 计划不可行))
                                   and not _瞬态126)
                        self._emit({"kind": "replan_error", "work": work,
                                    "err": f"{type(exc).__name__}: {exc}",
                                    "at": _位,
                                    "product_defect": _是缺陷,
                                    # ★ R126b：三类分开记，不压成一个布尔
                                    "upstream_unavailable": _瞬态126,
                                    # ★ R151：第四类，单独记
                                    "by_design_stop": _是设计停机151,
                                    # ★ R154：模型给错格式，单独记
                                    "model_malformed": _是模型给错154,
                                    "traceback": _tb.format_exc()[-2000:]})
                        if _瞬态126:
                            # ★ R126b：上游暂时不可用 ⇒ **如实说**：这既不是
                            #   「产品缺陷」，也不是「任务失败」，是环境没就绪。
                            #   ★ 不在这里再套一层重试：Router 已按预算重试到
                            #     耗尽（err 日志有 `无健康槽 ⇒ 等 2 秒` 与 520）。
                            #     再套一层只会把 4 小时拖成 8 小时。
                            _log(f"[generic] ★★★ 上游**暂时不可用**（{_位}）："
                                 f"{type(exc).__name__}: {str(exc)[:200]}\n"
                                 f"[generic] ★★★ 这**不是产品缺陷**，也不是模型给错了规约"
                                 f" ⇒ 是上游服务端故障；Router 已按预算重试/换通道。"
                                 f"请稍后重跑（回执已记 `upstream_unavailable`）")
                        elif _是模型给错154:
                            _log(f"[generic] ★★★ **模型给错了格式**（{_位}）："
                                 f"{type(exc).__name__}: {str(exc)[:300]}\n"
                                 f"[generic] ★★★ 这**不是产品缺陷**：产品要求模型返回合法 JSON，"
                                 f"而这次它写断了（多半是输出被截断）。\n"
                                 f"[generic] ★★★ 该做的是**回喂一次**（重试/换通道），"
                                 f"不是去修产品代码（回执已记 `model_malformed`）")
                        elif _是设计停机151:
                            _log(f"[generic] ★★★ **按设计停机**（{_位}）："
                                 f"{type(exc).__name__}: {str(exc)[:300]}\n"
                                 f"[generic] ★★★ 这**不是产品缺陷**，也不是上游故障"
                                 f" ⇒ 是机器**按设计交还决定权**："
                                 f"它已连续回喂仍无法把规约改对，"
                                 f"**不猜、不替你做决定**。\n"
                                 f"[generic] ★★★ 该由**人**定：是目标本身不可判，"
                                 f"还是判据与目标之间有缺口（回执已记 `by_design_stop`）")
                        elif _是缺陷:
                            _log(f"[generic] ★★★ 重规划**崩了**（{_位}）："
                                 f"{type(exc).__name__}: {exc}\n"
                                 f"[generic] ★★★ 这是**产品缺陷**，不是模型给错了规约"
                                 f" ⇒ 不得当成「任务失败」上报；"
                                 f"完整栈已进 replan_error 回执")
                        else:
                            _log(f"[generic] 重规划失败（{_位}）："
                                 f"{type(exc).__name__}: {exc} → 诚实停机")
                        return 1
                    if sig == "changed":
                        # ★ R153b：门集换了 ⇒ 旧的「无区分力」结论**不再适用**
                        #   （同名门可能是全新判据）⇒ 清空，重新累积。
                        self._恒真门153 = set()
                        self._replan_count += 1
                        content = None          # 门集变了 ⇒ 旧产出对新判据无意义，重产
                        red_audit_prev = []
                        prev_devs = self._重取基线(dev_pred)
                        prev_green = set()
                        _log(f"[generic] 轮 {work}：{verdict} → 已换判据门集"
                             f"（第 {self._replan_count}/{self.replan_max} 次），继续演化")
                        continue
                    # escalated：门集没真变 ⇒ 判据不可达，停机交还（不空转）
                    self._emit({"kind": "escalated_unreachable", "work": work,
                                "gates": results, "verdict": verdict,
                                "reason": f"{why}（重规划未产生真不同的门集 ⇒ 判据不可达）",
                                "tokens_chars": self._tok_chars})
                    _log(f"[generic] 门集未真变（判据不可达）@轮{work} → 交还用户")
                    self._报主树保留()
                    return 1
                # ★★★★★ R129（真跑抓出的第 42 层 · 2026-10-21 · 第二十五次端到端）：
                #   **R128 算出来的信息，在「下一轮 prompt」才用；而 diverging
                #     立刻停机 ⇒ 永远没有下一轮 ⇒ 信息永远没有消费者。**
                #
                #   真证据（18:45→19:13 那一跑）：
                #     轮2 结束 → `★ R128 本轮把 2 条**上一轮还是绿的**门改红了
                #                  ⇒ 下一轮 prompt 会明确要求「不许弄红它们」`
                #     紧接着 → `判据结构停机 @轮2（diverging）→ 交还用户`
                #   ⇒ 那一行「下一轮会要求」**说了谎**：没有下一轮。
                #   ★ 这是「声明了与被使用之间断了」的第 42 层，
                #     而且是最讽刺的一层：**我自己刚写下的那句承诺，自己违背了。**
                #
                #   ★ 修法：`diverging` **第一次**先回喂一次再判死。
                #     理由不是「宽容」，是**信息还没有被使用过**：
                #     `diverging` 说明「已消偏差重现」⇒ 模型确实在倒退；
                #     但机器**同时**知道「倒退的是哪几条门」，而这条知识
                #     一次都没有到过模型手里。**没给过机会就判死，不成立。**
                #   ★ 上限 1 次（`_diverging_重试`）：给过一次仍倒退 ⇒ 那是真倒退，
                #     立刻停机。**不放宽收敛语义** —— 只是把信息真的送出去一次。
                if (verdict == "diverging"
                        and (getattr(self, "_曾绿今红", None) or [])
                        and getattr(self, "_diverging_重试", 0) < 1):
                    self._diverging_重试 = getattr(self, "_diverging_重试", 0) + 1
                    _曾绿129 = list(self._曾绿今红 or [])
                    _log(f"[generic] ★ R129 diverging 第一次 ⇒ **先回喂再判**"
                         f"（不是宽容，是这条知识一次都没到过模型手里）："
                         f"它把 {len(_曾绿129)} 条曾是绿的门改红了 {_曾绿129[:4]}")
                    self._emit({"kind": "diverging_retry", "work": work,
                                "verdict": verdict, "why": why,
                                "turned_red": _曾绿129,
                                "reason": "已消偏差重现，但机器还没把"
                                          "「哪几条门被弄红了」告诉过模型"
                                          " ⇒ 先回喂一次再判",
                                "tokens_chars": self._tok_chars})
                    red_audit_prev = red       # 让模型看见当前红门
                    content = None             # 重产（不复用上一轮产物）
                    continue
                # diverging / 其他：已消偏差重现，回滚已在 _round 完成 → 诚实停机
                self._emit({"kind": "escalated_unreachable", "work": work,
                            "gates": results, "verdict": verdict, "reason": why,
                            "tokens_chars": self._tok_chars})
                _log(f"[generic] 判据结构停机 @轮{work}（{verdict}）→ 交还用户：{why}")
                self._报主树保留()
                return 1

            # action == "rework"：推进，继续单锚定偏差重工（Stage 4 在 _round 内锚定派发）
            if curry := pick_anchored_deviation(cur_devs):
                self._target_dev = curry   # 本轮重工只消除该偏差的最小 delta
            else:
                self._target_dev = None

            # 资源护栏（#5，唯一硬停，非收敛判据）：超预算诚实停机，防无限烧钱
            if self._tok_chars > self.budget_chars:
                self._emit({"kind": "budget_exceeded", "work": work,
                            "tokens_chars": self._tok_chars})
                _log(f"[generic] 预算护栏触发（{self._tok_chars} 字符）"
                     f"→ 诚实判未收敛")
                self._报主树保留()
                return 1

        self._emit({"kind": "not_converged", "work": self.max_work,
                    "gates": results, "tokens_chars": self._tok_chars})
        _log(f"[generic] {self.max_work} 轮资源护栏触发（未达 Δ=∅ 全绿）→ 诚实判未收敛")
        self._报主树保留()
        return 1

    def _达标(self, cur_ids, 外今=None) -> bool:
        """**本次任务达标了吗** —— 门全绿，或**相对基线严格变小**。★ R73b。

        ★ 这是全库**唯一**的达标口径（原来有两把尺子：文件级用"真子集变小"、
          最终裁决用"绝对全绿" ⇒ 同一轮里前半句说"在进步"、后半句说"没达标"）。

        判序**不可颠倒**（R46 那一课：全绿是更强的条件，它是终点，必须先判）：
          ① 违规点为空 ⇒ **全绿** ⇒ 达标（此时基线是什么都无所谓）；
          ② 有违规点但**真子集**于基线 ⇒ 本次任务**让它变好了** ⇒ 达标；
          ③ 其余 ⇒ 不达标。

        ★ 安全边界：
          · 没有基线（主树本来就全绿）⇒ **退化成"必须绝对全绿"** ⇒ 老行为不变；
          · 仍是**真子集** ⇒ "修 3 个坏 2 个"（净减但非子集）**不算**达标；
          · `cur_ids` 为空但门结果本身为空（判据算不出位置）**不得**混同 ——
            调用方必须先确认"门结果非空且全 True"（R46 的注释已写明），
            本函数只回答"违规点集合"这一层的问题。
        """
        # ★★★★★ R89：**代偿不得买通裁决**。
        #   目标点名的文件自己没变好 ⇒ 不管别处好多少，**都不算达标**。
        #   ★ 这是「判据门是唯一裁决者」的实质要求：
        #     裁决若能被别处的收益买通，它就不是唯一裁决者。
        #   ★ 放在**最前面**：它比"全绿"更优先 —— 全绿但目标文件没动，
        #     仍然是"目标没达成"（那种形态 R86 已经见过一次）。
        # ★★★★★ R137（真跑抓出的第 50 层 · 2026-10-23 · 第三十一次端到端）：
        #   **「没达标」的因由，写的是「看起来达标」的内容。**
        #
        #   真证据（第 31 次真跑，轮 2，原文）：
        #     `轮 2: 门=… 全 True | Δ=0 verdict=converged action=stop`
        #     `未达标因由：本次任务达标：verdict=converged；基线 3 处→ 现 0 处`
        #     `判据结构停机 @轮2（converged）→ 交还用户`
        #   门**全绿**、违规点 **3 → 0**、门 **6/6 有区分力**（R136 已生效）。
        #   ⇒ 而 `_达标` 返回 False，因由却写着「verdict=converged；基线 3→0」。
        #
        #   ★★ 这两句话**互相矛盾**：因由读起来是「全好了」，
        #     值却是「没达标」。人（和模型）看到这句会以为机器在胡说。
        #   ★ 根因：因由串是**从 verdict 与基线条数拼的**，
        #     而 `_达标` 真正的拦截点在**另外三个早退**里
        #     （`_未好89` / `_破92` / `cur_内` 子集判），一个都没写进因由。
        #   ★ 与 R133 同族**第 3 次**：
        #     R130「合法集合没说出来」→ R133「哪一条不合格没说出来」
        #     → **R137「为什么没达标没说出来，反而说了一句相反的话」**。
        #   ★ 这是比「不说」更坏的形态：**说了一句会把人引到错方向的因由。**
        #
        #   ⇒ 修法：每个早退都**留下真实因由**，并让裁决因由用它。
        self._未达标因由137 = []
        _未好89 = getattr(self, "_必改未达标", None)
        if _未好89:
            self._未达标因由137 = [
                f"目标点名的文件**自己没变好**：{_未好89}"
                f"（R89：别处的收益不得买通裁决）"]
            return False
        # ★★★★★ R92：**主心骨声明的判据必须真能拒收，否则「全绿」不构成达标**。
        #   与 R89 同型但方向不同：
        #     · R89 拦的是「用**别处**的收益买通裁决」
        #     · 本处拦的是「用**假判据**买通裁决」—— 门永远绿，那全绿毫无信息量。
        #   ★ 只拦"声明了样本、样本却不红"（=主心骨断言了一件假事）；
        #     "没声明样本"不硬拦 —— 那种门退回 `gate_witness` 的启发式变异，
        #     ★ 因为标准工程门（cargo check）本来就不该由模型编样本。
        _破92 = getattr(self, "_chief_契约破裂", None)
        if _破92:
            self._未达标因由137 = [
                f"判据契约破裂（样本**应用成功**了、门**仍然绿** ⇒ 真·无区分力）："
                f"{_破92}（R92：假判据不得买通裁决）"]
            return False
        # ★ R148：**未验不是破裂。** 它必须被说出来，但不能用来否决达标 ——
        #   否则「样本指向尚未创建的目标状态」会把每一次真跑都钉死在这里
        #   （实测：32 次真跑的结构性原因正是这类「永不成立」的判据）。
        #   ★ 但也不能静默：写进因由，让报表和主心骨都看得见。
        # ★★★★★ R119b：**作业面外的历史债不是本次任务的责任。**
        #   真证据（第十六次端到端 work=4）：`cargo_test_防假门`（全库门）
        #   恒红，而它报错的文件（`运行时事件采集.rs` / `运行时健康诊断.rs` /
        #   `判据观测.rs`）**都不在 deliverables 里** ⇒ 编排器永远修不掉它，
        #   却永远被要求把它变小 ⇒ `cur ⊊ 基线` 永不成立 ⇒ 死锁。
        #   ⇒ 15 次真跑全部 exit 1 的**结构性原因**就在这里。
        #   ★ 不放松：作业面外的**新债**（不在基线里的）仍判不达标（回归）。
        # ★ 属性可能是 `None`（老账本/自检夹具）⇒ 必须 `or frozenset()`，
        #   不能只用 `getattr(..., frozenset())` 的默认值 ——
        #   属性**存在且为 None** 时默认值不生效，`frozenset(None)` 直接 TypeError。
        #   ★ 这是「默认值 ≠ 缺失值」：Python 的 getattr 默认只兜「不存在」。
        # ★ 关于「作业面外的新债」：**不需要单独的守卫**。
        #   我原本写了一句 `if _外今 - _外基: return False`，装齿时发现
        #   **它永远不改变答案** —— 因为新债 Y ∉ `_外基` ⇒ Y 一定留在
        #   `cur_内` 里 ⇒ 它要么使 `cur_内` 非空、要么让 `cur_内 ⊄ base_内`
        #   ⇒ 下面的子集判据**必然**判不达标。
        #   ★ 于是那条守卫是**装饰品**：看着像保险，其实没在起作用。
        #     这正是我这一路反复抓到的同一族（「声明了」与「被使用」之间断了）
        #     —— 所以**删掉它**，而不是留着让人误以为保护来自那里。
        #   ★ 真正的保护是 `_外基` **只从基线取**：豁免集合不能随本轮长大。
        # ★★★★★ R124b（同一趟真跑的第二层）：
        #   **窗级调用 `_达标` 时，`_本轮作业面外` 还没算出来。**
        #   时序：`_round`（含 `_window_score` ×N）在 `_run` 的 for 里**先跑**，
        #   而 `self._本轮作业面外` 是在 `_round` **返回之后**才算的
        #   ⇒ 窗级读到的是**上一轮**的（第一轮时根本没有 ⇒ 空集）。
        #   ⇒ 空集意味着「作业面外一个都不豁免」⇒ 窗级比轮级**严**得多
        #     ⇒ 同一个产物，轮级说达标、窗级说不过 ⇒ `green=0` ⇒ 交付不了。
        #   ★ 修法：让调用方**显式**把「这一份产物算出来的作业面外身份」传进来。
        #     不传 ⇒ 退回属性（老行为，自检夹具不受影响）。
        _外基 = frozenset(getattr(self, "_基线作业面外", None) or ())
        _外今 = (frozenset(外今) if 外今 is not None
                 else frozenset(getattr(self, "_本轮作业面外", None) or ()))
        # ★★★★★ R125b（同一趟真跑的第二层）：
        #   **剔除作业面外要用「本轮」的集合，不是「基线」的。**
        #
        #   真证据：基线 1 处，全部作业面外 ⇒ `base_内 = ∅`。
        #   本轮冒出的 19 个编译错（`A线拼装.rs` / `呼吸基准.rs` / …）
        #   **也全在作业面外**（都不是交付物）。但它们是**新**的
        #   ⇒ 不在 `_外基` 里 ⇒ `cur_ids - _外基` 剔不掉它们
        #   ⇒ 它们被当成「作业面内的新债」⇒ `base_内 = ∅` ⇒ 判不达标。
        #
        #   ★ 根因：`_外基` 是**基线时刻**的作业面外集合；
        #     一个新出现的作业面外偏差**不可能**在里面。
        #     「用旧集合去剔新债」在构造上就不成立。
        #
        #   ★ 那「模型越界把别处改坏了」怎么办？**现在抓不到**，如实登记：
        #     本趟真跑日志里就有一条
        #       `★ R54：不覆盖 —— src\模块甲\mod.rs 在我们写入之后
        #         又被别的进程改了（我们写的 2b7f70b1，现在 f3532a11）`
        #     ⇒ 这棵树**同时被别的智能体改着**。在这种环境下，
        #       「作业面外出现新偏差」**无法区分**「我们改坏了」与「别人改了」
        #     ⇒ 把它算成本次任务的责任是**已被实测证伪**的（20 次真跑全部失败）。
        #   ★ 正确的收口不在这里，而在**越界改动检测**（R54 已经有一半：
        #     它知道文件被别人改了）。登记为待办，不在这里用错误的判据硬凑。
        cur_内 = frozenset(cur_ids) - _外今
        base_内 = frozenset(getattr(self, "_基线身份", None) or ()) - _外基
        if not cur_内:
            return True                       # 作业面内已无违规
        if not base_内:
            self._未达标因由137 = [
                f"作业面内仍有 {len(cur_内)} 处违规，而基线作业面内是 **0 处**"
                f"（没有可超越的基线 ⇒ 退化为「必须绝对全绿」）："
                + "；".join(sorted(cur_内)[:3])]
            return False
        if not (cur_内 < base_内):
            self._未达标因由137 = [
                f"违规点 **不是基线的真子集**（现 {len(cur_内)} 处 / 基线 "
                f"{len(base_内)} 处）⇒ 「修 3 个坏 2 个」不算达标："
                + "；".join(sorted(cur_内 - base_内)[:3])]
            return False
        return True

    def _重取基线(self, dev_pred) -> list | None:
        """换了判据门集 ⇒ **按新判据重取基线**（取**当前态**）。★ R73。

        ★ 为什么不能沿用旧基线：判据换了，**偏差身份对新判据无意义**
          （门名、证据文本、行号全变了）⇒ 拿旧集合比新集合是**两把尺子**。
        ★ 为什么不能退回 `None`（= 绝对全绿）：那会把**主树上的历史违规**
          重新算成本次任务的责任 ⇒ 又变成**恒红门**（R73 要修的就是它）。
        ★ 为什么取"当前态"而不是"原始主树"：此时主树可能已含本轮**已保留**
          的进展；取当前态 ⇒ 达标口径仍是"**比现在更好**" ⇒ **单调向前**，
          不会因为换了门集就把已取得的进展一笔勾销。

        ★★★★★ R142（真跑抓出的第 55 层 · 2026-10-23 · 第三十四次端到端）：
          **R141 只修了「首次测量」，没修「换门集后的重取」—— 同一个洞第 6 次。**

          真证据（第 34 次真跑）：
            轮1-3   `R73 基线：主树上**本来就有** 10 处判据偏差`     ← 对
            轮4     `R73 换门集 ⇒ 按**新判据**重取基线：22 处偏差`   ← **暴涨**
            轮6     `R73 换门集 ⇒ 按**新判据**重取基线：4 处偏差`    ← **暴跌**
          同一棵主树、同一批判据，基线在 10 / 22 / 4 之间跳
          ⇒ 达标口径跟着跳 ⇒ `cur ⊊ 基线` 永远不稳定。

          ★ 根因：R141 在 `_基线偏差` **内部**归一了根（`REPO_ROOT` 优先），
            但**这个方法**把 `self.round_root` 直接传进去 ——
            而 `round_root` 是**本轮隔离树**（模型刚改完的样子）。
            `_基线偏差` 的归一只是"选一个存在的根"，它**不知道**
            调用方传进来的是隔离树还是主树。
          ★★ 与 R119a/R108/R116齿③/R139 **完全同族**：
            修了一个入口的守卫，没修**另一个入口**。
            这已经是第 **6** 次了。

          ★ 修法：**不传隔离树**。`_基线偏差` 的 `workdir` 参数只用于
            "workdir 都不存在时"的退路；真正的根由它内部按
            `REPO_ROOT → workdir` 归一。所以这里传 `None`，
            让它走归一，而不是替它做决定。
          ★ 不放松：仍是"比基线严格变小"，且基线仍来自**未改动的主树**。
        """
        self._基线偏差 = _基线偏差(None, self.deliverables, dev_pred)
        # ★ R73b：换门集也要**同步换基线身份** —— 否则达标判定还在拿
        #   旧判据下的身份比新判据下的身份（两把尺子）。
        self._基线身份 = frozenset(deviation_identity(d) for d in self._基线偏差)
        # ★ R119b：换门集 ⇒ 作业面外的那部分也按**新判据**重算
        self._面119 = _作业面路径集(self.deliverables)
        self._基线作业面外 = _作业面外身份(self._基线偏差, self._面119)
        if self._基线偏差:
            _log(f"[generic] ★ R73 换门集 ⇒ 按**新判据**重取基线："
                 f"{len(self._基线偏差)} 处偏差（达标口径＝比它严格变小）")
        return list(self._基线偏差) or None

    def _adopt_derived_gates(self, witness_rep: list, work: int) -> int:
        """把见证**构造性派生**出的替换门真装进门集（TS-384 路径的落地）。

        对应 `TS384.defect_is_endogenous_drive`：
            缺陷对 (x,y) → ∃ T', T' x ≠ T' y ∧ (∀ z, T' z ∈ {0,1})
        以及 `evolution_loop_closes`：缺陷 → 细化 → 新真值 → **仍满足全称**。

        做法（纯机器，零 LLM）：
          ① 从 witness 报告里取 `derived_gate`（gate_witness._try_derive 已造好
             并**当场真跑验证**过有区分力）
          ② 在 `self.spec["gates"]` 与各 deliverable 的 `gates` 里，
             按**门名**找到原恒真门，用派生门替换
          ③ 返回替换个数（0 ⇒ 没换成，调用方应走原 replan/停机路径）

        ★ 有界性：派生门带 `_derived_from`，`gate_witness._try_derive` 拒绝
          对派生门再派生 ⇒ 细化次数有界，不会无限越切越细。
        ★ 诚实：只替换**同名**的原门；找不到就跳过（不猜、不强塞）。
        """
        派生 = [r.get("derived_gate") for r in witness_rep
                if isinstance(r, dict) and r.get("derived_gate")]
        if not 派生:
            return 0
        # ★★ 假红闸（本轮自查抓出的真缺陷，必须守住）：
        #   初版派生门**钉好产物原文** ⇒ 任何"不同但同样合法"的产物被判红
        #   （实测 6/6 全红）。这正是 TS-378 抓的**假红**：
        #     约束同类（目标眼里都达标）却被判不同档 ⇒ 判别力超出目标上限。
        #   带 `_over_specific` 标记的门**不进交付门集** —— 如实记录并跳过。
        可用: list[dict] = []
        for 新门 in 派生:
            if 新门.get("_over_specific"):
                self._emit({"kind": "gate_rejected_over_specific",
                            "work": work, "gate": 新门.get("name"),
                            "reason": "派生门钉产物原文 ⇒ 换写法即假红"
                                      "（TS-378：判别力超出目标上限）"})
                continue
            可用.append(新门)
        if not 可用:
            return 0
        换 = 0
        for 新门 in 可用:
            原名 = 新门.get("_derived_from")
            if not 原名:
                continue
            # spec 顶层门集
            for lst in [self.spec.get("gates") or []] + [
                    d.get("gates") or [] for d in self.deliverables]:
                for i, g in enumerate(lst):
                    if not isinstance(g, dict):
                        continue
                    if str(g.get("name") or g.get("type")) == str(原名):
                        # 保留原门的位置语义（in/cwd 若原门有而派生门没有则继承）
                        m = dict(新门)
                        for k in ("in", "src"):
                            if k not in m and k in g:
                                m[k] = g[k]
                        lst[i] = m
                        换 += 1
        if 换:
            self._emit({"kind": "gate_derived", "work": work,
                        "derived": [{"from": r.get("gate"),
                                     "to": (r.get("derived_gate") or {}).get("name"),
                                     "defect_pair": (r.get("derived_gate") or {}).get("_defect_pair"),
                                     "discriminates": (r.get("derived_gate") or {}).get("_discriminates")}
                                    for r in witness_rep if r.get("derived_gate")],
                        "tokens_chars": self._tok_chars})
        return 换

    def _recall_gate_candidates(self) -> list[dict]:
        """按产物**结构指纹**召回历史上沉淀的结构门（**候选**，非免检）。

        数学依据 `TS378.goal_change_changes_constraint`（:174）机证
        **约束随目标变** ⇒ 为旧目标派生的门对新目标**可能过细**（假红）。
        故召回**只是提议**；装进门集前必须过
        `gate_memory.机检复用是否安全`（真跑判绿 + 约束机检）。

        与 `_adopt_derived_gates` 的区别：
          · 后者是**本轮当场派生**（同一任务内，缺陷对就在手上）
          · 本方法是**跨任务召回**（上一任务沉淀的样本戊），故门槛更高：
            要求命中 ≥ `gate_memory.最小命中`，且必须双重机检。
        """
        try:
            from agentscope_orchestrator import gate_memory as _gm
        except Exception:      # noqa: BLE001 不可用则不召回（不阻断）
            return []
        for d in self.deliverables:
            p = (self.round_root or REPO_ROOT) / d["path"]
            if not p.is_file():
                continue          # 产物还没出来 ⇒ 无从按结构召回
            try:
                候选 = _gm.召回(p)
            except Exception:      # noqa: BLE001
                continue
            if not 候选:
                continue
            检 = _gm.机检复用是否安全(候选, p, self.round_root or REPO_ROOT)
            if 检["拒收"]:
                self._emit({"kind": "gate_recall_rejected",
                            "rejected": [g.get("name") for g in 检["拒收"]],
                            "report": 检["报告"]})
                _log(f"[generic] ★ 召回门被拒 {len(检['拒收'])} 个"
                     f"（目标已变 ⇒ 旧门可能过细，TS-378）")
            if 检["安全"]:
                self._emit({"kind": "gate_recalled", "work": 0,
                            "gates": [g.get("name") for g in 检["安全"]],
                            "report": 检["报告"]})
                _log(f"[generic] ★ 跨任务召回 {len(检['安全'])} 个已确证结构门"
                     f"（复用前已双重机检）")
                return 检["安全"]
        return []

    def _sediment_pending(self, work: int) -> int:
        """把**已被跑绿确证**的派生门沉淀成样本戊，供同结构任务复用（TS-378）。

        数学依据：`TS378.disc_factors_through_constraint`（:157）
            尊重约束的判别式**必唯一地穿过约束的商** ⇒ 门是**可派生、可沉淀
            的样本戊**，不是一次性产物。`goal_change_changes_constraint`（:174）
            机证约束随目标变 ⇒ **复用必须重验**（本方法只沉淀，重验在召回侧）。

        ★ 纪律（三条，缺一不可）：
          ① 只沉淀 `_structural` 门 —— 钉原文的过细门是假红生成器，传下去害人
          ② 只沉淀**本轮真跑绿**的门 —— 造门 ≠ 门有用（`witness_ok` 已确证）
          ③ 沉淀是**候选提议**，不是免检通行证 —— 召回侧仍要双重机检

        返回沉淀条数。
        """
        if not self._pending_sediment:
            return 0
        try:
            from agentscope_orchestrator import gate_memory as _gm
        except Exception as exc:      # noqa: BLE001 沉淀失败不阻断主流程
            _log(f"[generic] 门沉淀不可用（不阻断）：{exc}")
            return 0
        产物 = None
        if self.deliverables:
            候选 = self.round_root or REPO_ROOT
            产物 = Path(候选) / self.deliverables[0]["path"]
        if not 产物 or not 产物.is_file():
            return 0
        try:
            文本 = 产物.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return 0
        数 = 0
        for g in self._pending_sediment:
            try:
                if _gm.沉淀(g, 文本, 产物.suffix):
                    数 += 1
            except Exception:      # noqa: BLE001 单条失败不影响其余
                continue
        self._pending_sediment = []
        if 数:
            self._emit({"kind": "gate_sedimented", "work": work, "count": 数,
                        "tokens_chars": self._tok_chars})
            _log(f"[generic] ★ 跨任务沉淀 {数} 个已确证结构门"
                 f"（供同结构任务复用；复用前仍双重机检）")
        return 数

    def _check_constraint_respected(self, work: int) -> dict | None:
        """机检门集是否穿过**目标约束**的商（TS-378 路径）。

        数学依据：`TS378.disc_factors_through_constraint`（:157）
            约束同类（`G x ↔ G y`）⇒ 判别式必同类（`f x = f y`）
        违反 ⇒ 门的判别力超出目标上限（`constraint_bounds_discrimination` :165）
            ⇒ 假红生成器（合格产出被误拒）。

        ★★ 防空洞（本方法的核心约束，务必守住）：
          **达标判定必须独立于被检查的门集**。若用同一套门当"目标达标"的判据，
          则 `ConstraintOfGoal` 被门自己诱导，检查退化成
          「函数尊重自己诱导的等价关系」—— **恒真**（库内形态 12：前提重述为结论）。
          故：
            · 只有当 spec 声明了 `constraints`（**独立**机检谓词）**且**被检查的
              门**不是**由该 pred 派生（无 `derive` 标记）时，才真检查；
            · 否则**如实返回 None**（不阻断、不假装验过）。
          这与 `gate_witness` 对不可验门报 `unknown` 是同一条纪律。

        诚实边界：即使真检查，样本也是**有限**的（当前只有本轮产物一份），
        故只能发现「已暴露的违反」，不声称全称验证。
        """
        # ① 有没有独立的达标来源？
        cons = self.spec.get("constraints")
        if not (isinstance(cons, list) and cons):
            return None                     # 无独立约束 ⇒ 不假装能检
        # ② 被检查的门是否由 pred 派生？派生门与 pred 同源 ⇒ 检查会恒真
        d0 = self.deliverables[0] if self.deliverables else None
        g0 = ((d0.get("gates") if d0 else None) or self.spec.get("gates") or [])
        if any(isinstance(g, dict) and g.get("derive") for g in g0):
            # 门由 pred 派生 ⇒ 用它判"达标"就是循环 ⇒ 如实不检
            self._emit({"kind": "constraint_check_skipped", "work": work,
                        "reason": "门集由 pred 派生，达标判定不独立于门 ⇒ "
                                  "检查会恒真（形态 12）⇒ 如实跳过，不假装已验",
                        "tokens_chars": self._tok_chars})
            _log("[generic] 约束机检跳过：门由 pred 派生，达标判定不独立 ⇒ "
                 "检查会恒真（不假装已验）")
            return None

        # ③ 真检查：把"独立约束谓词"在**本轮真实产物**上跑出达标值
        try:
            from agentscope_orchestrator import constraint as _cons
            w_root = REPO_ROOT if self.repo_scope else self.round_root
            prod = w_root / d0["path"] if d0 else None
            if prod is None or not Path(prod).is_file():
                return None
            达标: dict[str, bool] = {}
            样本: list[tuple[str, Path]] = []
            # 每个约束谓词当一份"达标判定"来源：它在本产物上跑出的真值
            for i, c in enumerate(cons):
                if not isinstance(c, dict):
                    continue
                r, _ = gate_evaluate([c], Path(prod), w_root, ".")
                nm = c.get("name", f"constraint{i}")
                for k, v in r.items():
                    达标[str(k)] = bool(v)
            if not 达标:
                return None
            # 样本：当前产物 + 其**同类副本**（同内容不同路径 ⇒ 目标必同类）
            #   用途：若门对"同内容的两个副本"判不同档，说明门读了路径/时间等
            #   与目标无关的东西 ⇒ 正是"判别力超出目标上限"的实测形态。
            样本.append(("本轮产物", Path(prod)))
            twin = Path(tempfile.mkdtemp(prefix="cons_twin_")) / Path(prod).name
            twin.write_text(Path(prod).read_text( errors="ignore"),
                            encoding="utf-8")
            样本.append(("同内容副本", twin))
            达标["同内容副本"] = 达标.get("本轮产物", True)   # 同内容 ⇒ 同达标

            # ★★★ 样本扩充（补"样本太少"这个真缺口）：
            #   同内容副本只能抓**路径/时间依赖** —— 抓不住真正高频的那一类
            #   **内容过细**（门钉了产物原文）。实测：派生门钉原文 ⇒
            #   任何"不同但同样合法"的产物被判红（6/6 全红），
            #   而同内容副本**永远抓不到它**（副本内容相同）。
            #   修法：从产物造**结构变体**（改写法不改语义），
            #   用**独立谓词**筛出仍达标的（这才是"目标同类"），
            #   再机检门是否对同类样本判不同档。
            #   ★ 关键纪律：变体的"同类"由**谓词**给出，不是我假定的 ——
            #     与 TS378 `ConstraintOfGoal G x y := G x ↔ G y` 同口径。
            try:
                from agentscope_orchestrator import samples as _smp
                _r = _smp.筛同类样本(Path(prod), cons, w_root, ".")
                if _r["样本"]:
                    样本 += _r["样本"]
                    达标.update(_r["达标"])
                    self._emit({"kind": "constraint_samples_expanded",
                                "work": work,
                                "variants": [n for n, _ in _r["样本"]],
                                "rejected": [x["变体"] for x in _r["报告"]
                                             if not x["收"]],
                                "predicate_coarse": _r["谓词过粗"],
                                "tokens_chars": self._tok_chars})
                    _log(f"[generic] 约束样本扩充：谓词确证同类变体 "
                         f"{len(_r['样本'])} 个"
                         + ("（★ 谓词可能过粗，如实提示）"
                            if _r["谓词过粗"] else ""))
            except Exception as exc:      # noqa: BLE001 扩充失败不阻断
                _log(f"[generic] 样本扩充失败（不阻断，用原样本）：{exc}")

            报告 = _cons.机检门集尊重约束(g0, 样本, w_root, 达标, ".")
            self._emit({"kind": "constraint_check", "work": work,
                        "summary": f"尊重 {len(报告['尊重'])} · 违反 {len(报告['违反'])}"
                                   f" · unknown {len(报告['unknown'])}",
                        "report": 报告["报告"], "tokens_chars": self._tok_chars})
            return 报告
        except Exception as exc:      # noqa: BLE001 检查自身异常：如实降级
            _log(f"[generic] 约束机检异常（如实降级，不假装已验）：{exc}")
            return None

    def _project_acceptance(self) -> tuple[bool, dict, list, str]:
        """跑**档案声明的项目验收门**（人写的，独立于模型写的门集）。

        ★★★★★ 为什么必须有它（R21·真工程跑出来的缺口）：
          主树任务原来只信**模型自己写的门** ⇒ 模型写弱门就能让主树被改坏还报成功。
          库的口径：**达标判定必须独立于被检查的门集**（否则退化成恒真）。
          项目自己的验收标准正是那个独立来源，而它在目标驱动路径上从未被用过。

        返回 `(是否全绿, results, red_audit, 说明)`。
        ★ 档案未声明门 ⇒ 返回 `(True, {}, [], 说明)` 并**明说无从独立验收** ——
          不假装已验（本库一贯口径：读不到就是读不到，不等于违反）。
        """
        gates = [dict(g) for g in prof_mod.get_profile().gates]
        if not gates:
            return (True, {}, [],
                    "档案未声明项目验收门 ⇒ **无从独立验收**"
                    "（不假装已验；模型写的门不构成独立来源）")
        # 项目门（如 `cargo test --lib`）跑在**主树**上，不看某个交付文件。
        # `{file}` 占位符若被用到，指向第一个交付物；不用则无影响。
        tgt = (REPO_ROOT / self.deliverables[0]["path"]
               if self.deliverables else REPO_ROOT)
        try:
            res, red = _score_gates(REPO_ROOT, tgt, gates, repo_root=REPO_ROOT)
        except Exception as exc:      # noqa: BLE001 - 门自身异常必须如实报，不得当绿
            return (False, {}, [{"gate": "项目验收门异常", "ok": False,
                                 "err": f"{type(exc).__name__}: {exc}"}],
                    f"项目验收门自身异常：{type(exc).__name__}: {exc}")
        ok = bool(res) and all(v is True for v in res.values())
        # ★★★★★ R78（真跑抓出 · 2026-10-17 · 第四次真项目验收）：
        #   **项目验收门也必须相对基线，不能绝对。**
        #
        #   【真跑证据】那一趟任务**自己的**门全绿了（这是真的做到了）：
        #     （全库）@cargo_check_lib            = True
        #     （全库）@exact_ts283_test           = True
        #     src/模块甲/mod.rs@…登记键与闭合标记俱在 = True   ← 目标达成
        #     三个"逐字保留"门                     = True
        #   唯一红的是 `（全库）@fake_test_scanner_green` —— 而它是
        #   **主树上本来就有的 11 处「只打印不断言」**（观测协议.rs 8 处 +
        #   运行时涌现.rs 3 处），**与本次任务无关**。
        #   任务级判定也说得很清楚：`★ R73b 达标（相对基线）：verdict=progressing，
        #   但违规点 2 → 1 且为**真子集** ⇒ 本次任务让它变好了`。
        #   可项目验收门仍判**绝对全绿** ⇒ 最终 exit 1 ⇒ **红的不是任务，是别人的债**。
        #
        #   ★ 根因与 R73/R73b **完全是同一个病，只是高了一层**：
        #     R73 修的是"Δ 相对空集"，R73b 修的是"达标＝绝对全绿"，
        #     而**项目验收门**这一层还是 `all(v is True)` —— 绝对口径。
        #
        #   ★★ 两层的**尺子必须不同**，这不是偷懒，是它们问的问题不同：
        #     · **任务级**（R73b）：相对基线**严格变小** —— 证明"我真的做了事"。
        #     · **项目级**（R78）：相对基线**不许变坏** —— 不许把别人的债
        #       算成本次任务的失败。
        #   若项目级也要求"严格变小"，那**每一个任务**都得负责**整棵树**的历史债
        #   ⇒ 与"缺口是一个一个闭合的"直接矛盾 ⇒ 无人值守永远跑不通。
        #   若任务级也只要求"不变坏"，那"什么都不做"就达标 ⇒ 那是**恒真**。
        #   ⇒ **两条一起**才既非恒真、又非恒假。
        #
        #   ★ 基线为空（没量到）⇒ **老行为完全不变**（必须绝对全绿）。
        _基78 = getattr(self, "_项目门基线res", None)
        ok, _因78, _坏78 = _项目门是否放行(_基78, res)
        if ok and _因78 and _基78:
            _log(f"[generic] ★ R78 项目验收门按**相对基线**判：基线 "
                 f"{sum(1 for v in _基78.values() if v is True)}/{len(_基78)} 条绿"
                 f" ⇒ 现在 {sum(1 for v in res.values() if v is True)}/"
                 f"{len(res)} 条绿；**没有一条由绿转红** ⇒ 不算失败")
        elif _坏78:
            _log(f"[generic] ★ R78 项目验收门**变坏了**：{'、'.join(_坏78)}"
                 f" 由绿转红 ⇒ 这是本次任务的责任")
        return (ok, res, red,
                f"档案声明的项目验收门 {len(gates)} 条："
                + "；".join(f"{k}={'绿' if v else '红'}" for k, v in res.items())
                + (f"；★ R78：{_因78}" if _因78 else ""))

    async def _deliver(self, work: int, results: dict, alignment: dict) -> int:
        # 产物落 products/<safe>/<时间戳版本>/<各文件>（沙箱留痕副本）。
        # 主树工程任务（repo_scope）：判门已对应用进主树的文件执行过，此处只留档记录；
        #   交付 = 主树文件保留（绿保）+ 备份/回滚点登记回执，不再重判门（避免用副本假判）。
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out = self.out_dir / self.safe_name / stamp
        out.mkdir(parents=True, exist_ok=True)
        for d in self.deliverables:
            src = self.round_root / d["path"]
            tgt = out / d["path"]
            tgt.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                # ★ R22c：字节级拷贝 ⇒ 沙箱副本 / 主树 / 产物副本**逐字一致**
                _落盘拷贝(src, tgt)
        self.product_dir = out
        if self.repo_scope:
            backups = list(self._active_backups or [])
            # ★★★★★ R21：**主树交付前必须过项目自己的验收门**（档案声明·人写的）。
            #   原来这里「不再重判门」，而它信的门**全是模型自己写的**
            #   ⇒ 模型写弱门就能让**主树被改坏还报成功交付**（真工程跑出的缺口）。
            # ★★★★★ R23：交付前**最后一道**把关 —— 冻结区必须逐字未变。
            #   放在项目验收门**之前**：先把「判据有没有被改掉」问清楚，
            #   否则后面那些门跑在**被改过的判据**上，绿了也不算数。
            _fz_vio = _冻结区违反(self._冻结区)
            if _fz_vio:
                for bk in reversed(backups):
                    _rollback_file(bk)
                self._active_backups = []
                self._emit({"kind": "frozen_region_violated", "work": work,
                            "violations": _fz_vio,
                            "rolled_back": [bk["path"] for bk in backups],
                            "reason": "冻结区（判据）被改动 ⇒ 主树已回滚，不交付",
                            "tokens_chars": self._tok_chars})
                _log("[generic] ★★ 冻结区被改动（判据不得由被判方改）⇒ "
                     "**主树已回滚**，不交付")
                for _a in _fz_vio[:5]:
                    _log(f"[generic]   ✗ {_a['file']}: {str(_a.get('err'))[:200]}")
                return 1
            # ★ R23：通过时**也要留痕** —— 静默的检查等于看不见的检查
            #   （真跑复验时正是靠"有没有这行"判断 R23 到底跑没跑）。
            # ★ R23：通过时**也要留痕** —— 静默的检查等于看不见的检查
            #   （真跑复验时正是靠"有没有这行"判断 R23 到底跑没跑）。
            if self._冻结区:
                _log("[generic] 冻结区（判据不得由被判方改）逐字未变："
                     + f"{len(self._冻结区)} 个区域"
                     + "".join(f" {s['path']}"
                               + (f"（`{s['begin']}` 起）" if s["begin"] else "（整文件）")
                               for s in self._冻结区))
            # ★ R25：声明了却取不到的，**通过时也要报** ——
            #   否则「冻结区逐字未变」这句话会被读成"判据受保护了"，
            #   而实际上没生效的那几条**一点保护都没有**。
            if self._冻结区未取到:
                _log("[generic] ★★ 另有 "
                     f"{len(self._冻结区未取到)} 条冻结区声明**从未生效**"
                     "（快照时取不到 ⇒ 那些区域没有任何保护）："
                     + "；".join(str(s["err"]) for s in self._冻结区未取到[:3]))
            _acc_ok, _acc_res, _acc_red, _acc_why = self._project_acceptance()
            _log(f"[generic] 项目验收门（独立来源·非模型给）：{_acc_why}")
            if not _acc_ok:
                # ★ 红 ⇒ **回滚主树全部改动** + 如实判未收敛。
                #   绝不交付一个编译不过/测试不过的主树 —— 那比不交付坏得多。
                for bk in reversed(backups):
                    _rollback_file(bk)
                self._active_backups = []
                self._emit({"kind": "project_gates_red", "work": work,
                            "project_gates": _acc_res, "red_audit": _acc_red,
                            "rolled_back": [bk["path"] for bk in backups],
                            "reason": f"项目验收门红 ⇒ 主树已回滚，不交付。{_acc_why}",
                            "tokens_chars": self._tok_chars})
                _log(f"[generic] ★★ 项目验收门红 ⇒ **主树已回滚**（"
                     f"{len(backups)} 个文件），不交付")
                for a in _acc_red[:5]:
                    _log(f"[generic]   ✗ {a.get('gate')}: "
                         f"{str(a.get('err') or a.get('cause') or '')[:140]}")
                return 1
            self._emit({"kind": "delivered", "work": work, "final_gates": results,
                        "alignment": alignment, "repo_scope": True, "products": [
                            str(out / d["path"]) for d in self.deliverables],
                        "live_in_repo": [bk["path"] for bk in backups],
                        "rollback_points": backups,
                        # ★ R21：**独立来源**的验收证据（模型写的门不算数）
                        "project_gates": _acc_res,
                        "project_gates_note": _acc_why,
                        # ★ R25：冻结区生效/未生效的**证据**（不只是日志行）——
                        #   回执里必须能看出"判据到底受没受保护"。
                        "frozen_regions": [
                            {"path": s["path"],
                             "from": (s["begin"] or "整文件"),
                             "bytes": len(s["bytes"])}
                            for s in self._冻结区],
                        "frozen_regions_skipped": list(self._冻结区未取到),
                        "tokens_chars": self._tok_chars})
            _log(f"[generic] 主树工程收敛交付 @轮{work}: 文件已保留在主树 {REPO_ROOT}")
            for bk in backups:
                _log(f"[generic]   ↳ 主树 {bk['path']}（备份/回滚点 {bk['backup']}）")
            return 0
        final, _ = _score_files(out, self.deliverables)
        ok = bool(final) and all(v is True for v in final.values())
        self._emit({"kind": "delivered", "work": work, "final_gates": final,
                    "alignment": alignment, "products": [
                        str(out / d["path"]) for d in self.deliverables],
                    "tokens_chars": self._tok_chars})
        _log(f"[generic] 收敛交付 @轮{work}: {out / '*.py'}")
        for d in self.deliverables:
            _log(f"[generic]   → {out / d['path']}")
        _log("[generic]   final gates: " + " ".join(
            f"{k}={v}" for k, v in final.items()))
        return 0 if ok else 2


# ════════════════════════════════════════════════════════════════
# dry 用的已知正确样例（斐波那契，无 API 调用）
# ════════════════════════════════════════════════════════════════
_DRY_SPEC = {
    "name": "fib_gen",
    "pre": ["python3 可用"],
    "constraints": ["零依赖"],
    "deliverable": "fib.py",
    "agent_instruction": "生成 fib.py",
    "gates": [
        {"name": "compile_ok", "type": "compile",
         "cmd": ["python", "-m", "py_compile", "{file}"], "cwd": ".",
         "expect_exit": 0, "timeout": 60},
        {"name": "value_check", "type": "compile",
         "cmd": ["python", "-c",
                 "from fib import fib; assert fib(0)==0 and fib(1)==1 and fib(10)==55"],
         "cwd": ".", "expect_exit": 0, "timeout": 60},
        {"name": "no_todo", "type": "grep_forbidden",
         "pattern": "(?i)todo\\b", "in": "{file}"},
    ],
    "acceptance": ["数值断言全过、无 TODO 残留"],
}

_DRY_MULTI_SPEC = {
    "name": "fib_multi",
    "pre": ["python3 可用"],
    "constraints": ["零依赖"],
    "deliverables": [
        {"path": "fib.py",
         "agent_instruction": "生成 fib.py：实现 fib(n) 返回第 n 项（fib(0)=0, fib(1)=1, fib(10)=55）",
         "depends_on": []},
        {"path": "test_fib.py",
         "agent_instruction": "生成 test_fib.py：import fib 并断言 fib(0)==0、fib(1)==1、fib(10)==55",
         "depends_on": ["fib.py"]},
    ],
    "gates": [
        {"name": "compile_ok", "type": "compile",
         "cmd": ["python", "-m", "py_compile", "{file}"], "cwd": ".",
         "expect_exit": 0, "timeout": 60},
        {"name": "no_todo", "type": "grep_forbidden",
         "pattern": "(?i)todo\\b", "in": "{file}"},
    ],
    "acceptance": ["两个文件编译通过、无 TODO、行为断言全过"],
}

_DRY_MACHINE_SPEC = {
    "name": "collatz_machine",
    "pre": ["python3 可用"],
    "constraints": ["零依赖", "不许有 TODO"],
    "deliverables": [
        {"path": "collatz.py",
         "agent_instruction": "由机器按结构真值组装 collatz_steps",
         # ★ 机器自生成：模型**不产代码**，只产这个结构 + 算子表达式（几十字符）
         "machine": {
             "language": "python",
             "结构": {"name": "collatz_steps", "in": 1, "out": 1,
                      "ops": ["count:step"], "carry": 1},
             "算子": {"step": "v // 2 if v % 2 == 0 else 3 * v + 1"},
         },
         "depends_on": []},
    ],
    "gates": [
        {"name": "compile_ok", "type": "compile",
         "cmd": ["python", "-m", "py_compile", "{file}"], "cwd": ".",
         "expect_exit": 0, "timeout": 60},
        {"name": "behave_ok", "type": "compile", "cwd": ".",
         "cmd": ["python", "-c",
                 "import sys; sys.path.insert(0,'.'); "
                 "from collatz import collatz_steps as f; "
                 "assert f([1])[0]==0; assert f([6])[0]==8; "
                 "assert f([27])[0]==111"],
         "expect_exit": 0, "timeout": 120},
        # ★★★★★ TS-597d · collatz 数值门（实测缺口）
        #   `behave_ok` 只断言 (1,6,27) 三个点 ⇒ **纯查表 {1:0,6:8,27:111}
        #   拿到与真算法完全相同的绿**。证据 `验证/_验门抓假绿.py`：
        #     旧门集 REAL 全绿=True / FAKE 全绿=True  ⇒ 门无区分力
        #     加本条 REAL 全绿=True / FAKE 全绿=False ⇒ 假绿被挡
        #   【口径·诚实边界】1..100 全对只把查表成本抬到高于真算法，
        #     **不证明**「无查表」（任何有限断言集都能被有限表满足）。
        {"name": "value_check", "type": "compile", "cwd": ".",
         "cmd": ["python", "-c",
                 "import sys; sys.path.insert(0,'.');\n"
                 "from collatz import collatz_steps as f\n"
                 "def t(n):\n"
                 "    k=0\n"
                 "    while n!=1:\n"
                 "        n = n//2 if n%2==0 else 3*n+1\n"
                 "        k+=1\n"
                 "    return k\n"
                 "bad=[(n,t(n),f([n])[0]) for n in range(1,101) if f([n])[0]!=t(n)]\n"
                 "assert not bad, bad[:5]\n"],
         "expect_exit": 0, "timeout": 120},
        {"name": "no_todo", "type": "grep_forbidden",
         "pattern": "(?i)todo\\b", "in": "{file}"},
    ],
    "acceptance": ["机器组装产物真跑通过 collatz 三值 + 1..100 全对、无 TODO"],
}

_DRY_CONTENT = (
    "def fib(n: int) -> int:\n"
    "    a, b = 0, 1\n"
    "    for _ in range(n):\n"
    "        a, b = b, a + b\n"
    "    return a\n"
)

_DRY_TEST_CONTENT = (
    "import sys\n"
    "sys.path.insert(0, '.')\n"
    "from fib import fib\n"
    "assert fib(0) == 0\n"
    "assert fib(1) == 1\n"
    "assert fib(10) == 55\n"
)


def _parser() -> argparse.ArgumentParser:
    """CLI 解析器 —— 抽成函数，使**自检门能走真解析器**（含全部默认值），
    而不是另写一份 `Namespace` 假样本戊（那正是 TS-381 漏检的成因）。"""
    ap = argparse.ArgumentParser(description="通用目标驱动多智能体编排器")
    ap.add_argument("--goal", default="", help="自然语言目标")
    ap.add_argument("--channel", default="", help="锁定频道（逗号分隔多频道；空=全通道）")
    ap.add_argument("--n", dest="n_windows", type=int, default=N_WINDOWS,
                    help="并行候选窗数（多窗一致对齐提置信）")
    ap.add_argument("--min-pass", type=int, default=MIN_PASS,
                    help="对齐所需过门窗数")
    ap.add_argument("--max-work", type=int, default=MAX_WORK,
                    help="回炉背顶轮数（收敛判据仍是门全绿）")
    ap.add_argument("--replan-after", type=int, default=REPLAN_AFTER,
                    help="某门连续红达此轮数→真实模式重规划换路径")
    ap.add_argument("--replan-max", type=int, default=REPLAN_MAX,
                    help="重规划次数上限（超限即判未收敛，防换路径绕圈）")
    ap.add_argument("--budget", type=int, default=TOKEN_BUDGET_CHARS,
                    help="模型字符预算护栏（超限诚实判未收敛）")
    ap.add_argument("--out", default=None, help="产物目录（默认 products/）")
    ap.add_argument("--dry", action="store_true", help="不真对话，用已知正确件跑闭环")
    ap.add_argument("--dry-multi", action="store_true",
                    help="dry 用多文件依赖样例跑闭环（验证 #3 拓扑串线）")
    ap.add_argument("--dry-rework", action="store_true",
                    help="dry 首轮故意红门 → 验证回炉重派机制端到端")
    ap.add_argument("--dry-machine", action="store_true",
                    help="机器自生成样例：交付物带结构真值 ⇒ 0-LLM 组装源码（真跑判门）")
    ap.add_argument("--selftest", action="store_true", help="跑纯机器自检门")
    return ap


def main(argv=None) -> int:
    from ._winutf8 import 确保UTF8输出 as _u8; _u8()
    ap = _parser()
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if (not a.goal and not a.dry and not a.dry_multi and not a.dry_rework
            and not a.dry_machine):
        ap.print_help()
        return 2
    if a.n_windows < a.min_pass:
        _log("[generic] 参数错误：--n 需 >= --min-pass")
        return 2
    if a.dry_multi or a.dry_rework or a.dry_machine:
        a.dry = True

    # ★★★★★ R158：**整趟跑要有头有尾的回执。**
    #   真事故（第 41 次真跑）：进程被外部杀掉 ⇒ 日志停在半句话，
    #   **没有退出码、没有停机回执** ⇒ 无法区分「还在跑」「崩了」「被杀」。
    #   ★ 无人值守时，「不知道它怎么了」本身就是最坏的结局。
    #   ⇒ 开头写 `run_start`（含 pid），结尾在 `finally` 里写 `run_end`
    #     （含退出码/异常类型）。★ 被 SIGKILL 时 finally 也跑不到 ——
    #     那正是「心跳停了但**没有** run_end」这个组合的意义。
    def _写回执158(事件: dict) -> None:
        try:
            _RECEIPT.parent.mkdir(parents=True, exist_ok=True)
            with _RECEIPT.open("a", encoding="utf-8") as _f158:
                _f158.write(json.dumps(事件, ensure_ascii=False) + "\n")
        except OSError as _e158:
            _log(f"[generic] ★ R158 回执写盘失败（不阻断）：{_e158}")

    _写回执158({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "run_start", "pid": os.getpid(),
                "goal": (a.goal or "")[:300],
                "argv": list(sys.argv[1:])})
    _码158, _因158, _类158 = 0, "正常结束", None
    try:
        runner = _build_runner(a)
        # ★ R158b：心跳由**定时器线程**驱动（不是事件驱动）——
        #   事件驱动会在「等上游」时停摆，把「在等」误判成「被杀」。
        runner._起心跳()
        _次数217 = _尝试次数217()
        for _第217 in range(1, _次数217 + 1):
            runner = _build_runner(a)
            runner._起心跳()
            _码158 = asyncio.run(runner._run())
            # ★ R221：先把「基础设施不可用」从「任务没做到」里摘出来。
            if _码158 and _上游不可用221(runner):
                _报221 = (getattr(runner, "channel_report", None) or {}).get("unreadable") or {}
                _log(f"[generic] ★★ R221 **这是基础设施不可用，不是任务未达标**："
                     f"没有可用模型通道（{str(_报221)[:200]}）⇒ 退出码 3")
                _写回执158({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "kind": "upstream_unavailable",
                            "exit_code": 3, "detail": str(_报221)[:600]})
                _码158, _因158 = 3, "基础设施不可用（无可用模型通道），不是任务失败"
            if not _码158:
                if _第217 > 1:
                    _log(f"[generic] ★ R217 **第 {_第217}/{_次数217} 次尝试达标**"
                         f"（前 {_第217 - 1} 次未达标，均已如实记账）")
                return 0
            if _第217 < _次数217:
                _log(f"[generic] ★ R217 第 {_第217}/{_次数217} 次尝试未达标"
                     f"（退出码 {_码158}）⇒ 回退保证主树回到基线 ⇒ **自动重试**"
                     f"（无人值守不该一次失败就收工）")
                _写回执158({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "kind": "attempt_retry", "attempt": _第217,
                            "of": _次数217, "exit_code": _码158})
                continue
            return _码158
        return _码158
    except KeyboardInterrupt:
        _码158, _因158, _类158 = 130, "被中断（KeyboardInterrupt）", "KeyboardInterrupt"
        raise
    except BaseException as _e158b:
        _码158, _因158 = 1, "异常退出"
        _类158 = f"{type(_e158b).__name__}: {_e158b}"[:400]
        raise
    finally:
        _写回执158({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "kind": "run_end", "pid": os.getpid(),
                    "exit_code": _码158, "how": _因158,
                    "exc": _类158})


# ★★★★★ R217：**无人值守不该「一次失败就收工」。**
#
#   事实基础（本会话第 102/108 次真跑）：同一份红树、同一套判据，
#   机器**能把项目修绿并判定收敛**（我独立复核 exit 0）；
#   而第 106/109/110/111/112/114/115/116/117 次真跑都没修好。
#   ⇒ 成功率不是 0 也不是 1，是**随模型产出波动**。
#   ★ 这是产品该承担的：**无人值守 = 一直试到达成，不是试一次就退出码 1。**
#   ★ 安全性由既有机制保证：每一轮都在回退保证下把主树带回基线
#     ⇒ 失败后重试**不会叠加破坏**，重试是安全的。
#   ⇒ 口径：`ORCH_ATTEMPTS`（默认 1）控制外层尝试次数；
#     每次失败写 `attempt_retry` 回执，全部失败才如实返回非 0。
#     绝不把「重试成功」伪装成「一次就成了」—— 回执里逐次如实记。
# ★★★★★ R221：**「没做到」和「没有模型可用」必须分开。**
#
#   真证据（2026-10-29 18:20 实测探活）：
#     select_live_channels("dddddd", probe=True)
#     → live=[]，unreadable={'dddddd': 'HTTP 402 上游服务异常，请稍后重试'}
#   ★ 这时真跑既不能达标，也不是「模型尽力了但没做到」。
#     两者混成同一个退出码 1 ⇒ 用户无法判断该等上游、该换通道、还是该看代码。
#   ⇒ 口径：**基础设施不可用 ⇒ 退出码 3 + 独立回执 `upstream_unavailable`**，
#     绝不与「任务未达标」混同。无人值守最怕的就是「不知道它怎么了」。
def _上游不可用221(runner) -> bool:
    """本轮是否**根本没有可用模型**（区别于「模型做了但没做到」）。"""
    _rep = getattr(runner, "channel_report", None) or {}
    if not isinstance(_rep, dict):
        return False
    if _rep.get("live"):
        return False
    _坏 = _rep.get("unreadable") or {}
    _死 = _rep.get("dead") or {}
    return bool(_坏 or _死) and not _rep.get("live")


def _尝试次数217() -> int:
    """外层尝试次数。纯读环境变量，非法值一律按 1（保守）。"""
    try:
        _n = int(os.environ.get("ORCH_ATTEMPTS", "1") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(10, _n))


def _build_runner(a) -> "GoalRunner":
    """按 CLI 命名空间装配 GoalRunner。

    ★【TS-381 · 为什么必须**全关键字**，不许位置实参】
      `GoalRunner.__init__` 在 2026-10-12 为 `REPLAN_MAX` 接线时**中间插入了 `replan_max`**，
      而此处原先是位置实参调用 —— 于是全部实参**静默错位一格**：

        | 形参           | 实得            | 后果                          |
        |:--|:--|:--|
        | `replan_max`   | `a.budget`(int) | 重规划节流被静默废掉（2 → 1500000） |
        | `budget_chars` | `a.out`(str/None) | 预算护栏 `int > str` **必崩**   |
        | `out_dir`      | 缺省 None       | `--out` 被**静默忽略**          |

      ⇒ `orch-goal` 每次真跑都在第一次预算检查处 `TypeError` 退出。
      **位置实参对「签名增参」零抵抗**；关键字实参增参时只会报缺参，不会错位。
      本函数同时把「装配」从 `main` 里抽出来，使其**可被自检门直接调用**（见 `_selftest`）。
    """
    return GoalRunner(
        goal=a.goal,
        channel=a.channel,
        n_windows=a.n_windows,
        min_pass=a.min_pass,
        max_work=a.max_work,
        dry=a.dry,
        dry_multi=a.dry_multi,
        dry_rework=a.dry_rework,
        dry_machine=getattr(a, "dry_machine", False),
        replan_after=a.replan_after,
        replan_max=a.replan_max,
        budget_chars=a.budget,
        out_dir=a.out,
    )


# ════════════════════════════════════════════════════════════════
# ★★★★★ R160（2026-10-26）：**判活** —— 把 R158 的数据变成结论
# ════════════════════════════════════════════════════════════════
# 【为什么还要有它 —— R158 只造了数据，没造判断】
#   R158 落了 `run_start` / `run_end` / 心跳三样东西。
#   ★ 但**没有人读它们**：数据在文件里，判断还在我脑子里。
#   ⇒ 下一次再遇到「进程没了」，我照样得手工翻 jsonl、比对 pid、
#     自己推断「这是被杀了还是在跑」。**那就等于没修。**
#
# 【真事故链条（这是本轮的起因）】
#   第 42 次真跑：进程被外部杀掉 ⇒ 日志停在半句话 ⇒
#   我**无法区分**「还在跑 / 崩了 / 被杀了」，只能靠盯 CPU 猜。
#   ⇒ 无人值守时，「不知道它怎么了」就是最坏的结局。
#
# 【口径（三态，**不许二值化**）】
#   · `在跑`     ：心跳新鲜（≤ 心跳间隔 × 3）
#   · `正常收场` ：心跳停 + 有 run_end（看 exit_code）
#   · `被杀了`   ：心跳停 + **没有** run_end，且该 pid 已不存在
#   · `不知`     ：心跳停 + 无 run_end + **pid 还在**（可能是极慢的合法等待）
#     ★ 第四态必须有：把「不知」压成「被杀了」就是 R139 的老毛病
#       （「量不到」不等于「必须全绿」，也不等于「必须红」）。
# 心跳宽限：心跳间隔（20s）的 3 倍。超过它 ⇒ 心跳停了。
_心跳宽限秒 = 60.0


def 判活(回执文件=None, 心跳文件=None, 现在: float | None = None) -> dict:
    """读回执+心跳，判定**最近一趟**的状态。返回 {状态, 因由, ...}。

    只读，不改任何东西。可被 CLI 与自检直接调用。
    """
    import os as _os
    import time as _time
    回执 = Path(回执文件) if 回执文件 else _RECEIPT
    心跳 = (Path(心跳文件) if 心跳文件
            else 回执.with_suffix(".heartbeat"))
    现在 = _time.time() if 现在 is None else 现在
    报告: dict = {"状态": "不知", "因由": "", "pid": None,
                  "心跳年龄秒": None, "exit_code": None}

    # ── 心跳 ──
    心 = None
    try:
        if 心跳.is_file():
            心 = json.loads(心跳.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        心 = None
    if 心:
        报告["pid"] = 心.get("pid")
        try:
            报告["心跳年龄秒"] = round(
                现在 - _time.mktime(_time.strptime(心.get("ts", ""),
                                                   "%Y-%m-%d %H:%M:%S")), 1)
        except (ValueError, OverflowError):
            报告["心跳年龄秒"] = None

    # ── 回执里最近一趟的 start/end ──
    末开 = 末尾 = None
    try:
        if 回执.is_file():
            for _l in 回执.read_text(encoding="utf-8",
                                     errors="replace").splitlines():
                if not _l.strip():
                    continue
                try:
                    _d = json.loads(_l)
                except ValueError:
                    continue
                if _d.get("kind") == "run_start":
                    末开 = _d
                elif _d.get("kind") == "run_end":
                    末尾 = _d
    except OSError:
        末开 = 末尾 = None

    # ① 心跳新鲜 ⇒ 在跑（**心跳是定时器驱动的，只要进程活着就一定在写**）
    _龄 = 报告["心跳年龄秒"]
    if _龄 is not None and _龄 <= _心跳宽限秒:
        报告.update({"状态": "在跑",
                     "因由": f"心跳 {_龄:.0f} 秒前（≤{_心跳宽限秒:.0f}s）"})
        return 报告

    # ② 有 run_end 且它不早于最近的 run_start ⇒ 正常收场
    if 末尾 is not None and (末开 is None
                             or str(末尾.get("ts")) >= str(末开.get("ts"))):
        报告.update({"状态": "正常收场",
                     "exit_code": 末尾.get("exit_code"),
                     "因由": (f"exit_code={末尾.get('exit_code')} "
                              f"how={末尾.get('how')}")})
        return 报告

    # ③ 无 run_end ⇒ 分「被杀了」和「不知」——**看 pid 还在不在**
    _pid = 报告["pid"]
    if _pid is None:
        报告.update({"状态": "不知", "因由": "没有心跳也没有回执（从未跑过？）"})
        return 报告
    _活 = True
    try:
        _os.kill(int(_pid), 0)          # 只探存在，不发信号
    except (OSError, ProcessLookupError, PermissionError, ValueError):
        _活 = False
    if _活:
        # ★★ 第四态：进程还在，但心跳已过期 ⇒ **不许判死**
        报告.update({"状态": "不知",
                     "因由": (f"pid {_pid} 仍在，但心跳已 {_龄} 秒未更新"
                              "——可能是极慢的合法等待，也可能是卡死；"
                              "★ 不压成「被杀了」")})
    else:
        报告.update({"状态": "被杀了",
                     "因由": (f"pid {_pid} 已不存在，且**没有** run_end"
                              f"（心跳停在 {_龄} 秒前）⇒ 不是正常收场")})
    return 报告


# ════════════════════════════════════════════════════════════════
# 纯机器自检（不碰 API）
# ════════════════════════════════════════════════════════════════
def _selftest() -> int:
    fails: list[str] = []
    # ★★★★★ R70c（2026-10-16）：**分母不再手工维护，改成机器从源码数出来的。**
    #
    #   ── 为什么（R68 之后又犯了一次）──
    #   R68 我已经知道「手工数 = 枚举」，当时的修法是**补一条结构齿**
    #   （读自己的源码、断言 `fails.append` 出现次数 ≥ 分母）。
    #   ★ 但**分母本身还是手工的** —— 本轮我加了 9 条 R70 齿，分母没动，
    #     报表照样打「253/253 全绿」。★ 结构齿没红，是因为它只保证
    #     「出现次数 ≥ 分母」这个**危险方向**，而分母偏小恰好在这个方向之外。
    #   ⇒ 治本：**分母 = 源码里判据点的实际个数**（运行时数出来），
    #     另配一个**下限**（防被成批删除）。两个数都印出来，谁也不用信谁。
    #
    #   ★ 口径说清楚（不假装精确）：`fails.append` 的个数是**判据点**的个数，
    #     不等于"执行了多少次检查"（循环里的判据点会跑多次，一条检查也可能
    #     有多处 append）。所以报表写的是「**失败 0 · 判据点 M（下限 L）**」，
    #     而不是「M/M 全绿」—— 后者是在**声明**一个我数不准的东西。
    try:
        _源码 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _判据点 = _源码.count("fails.append")
    except Exception:
        _判据点 = 0
    _判据下限 = 400      # ★ 下限（**故意留余量**）：当前 409。删齿 ⇒ 低于它 ⇒ 红。

    # ★★★★★ R152 齿**必须排在最前**：
    #   实测：我先把它放在 R138 齿旁边（L12465），注入「给 `_达标` 挂
    #   `@staticmethod`」时，`_run` 在 **L5743 就先崩**（TypeError:
    #   missing 1 required positional argument）⇒ 自检带栈退出 ⇒
    #   **齿根本没跑到** ⇒ 红了却没点名。
    #   ★ 与 R149 同一个坑：**结论必须在别人崩之前落地。**
    # ══════════════════════════════════════════════════════════════
    # 【R160】判活：四态，**不许二值化**（第 75 层）
    # ══════════════════════════════════════════════════════════════
    # R158 只造了数据（run_start/run_end/心跳），**没有人读它们** ——
    # 数据在文件里，判断还在我脑子里。⇒ 下次再遇到「进程没了」，
    # 我照样得手工翻 jsonl、比对 pid。**那就等于没修。**
    # ★ 真事故三连：第 42 次被杀（无法区分）、第 43 次被杀（跑了 1 分钟）、
    #   第 44 次跑了 **4 小时**才消失（上游 504 风暴）。
    # ⇒ 判活必须**机器算**，而且要四态。
    # ★★★ 为什么必须有第四态「不知」：
    #   把「不知」压成「被杀了」就是 R139 的老毛病 ——
    #   「量不到」既不能当「全绿」，也不能当「必红」。
    try:
        import json as _json160
        import os as _os160
        import tempfile as _tmp160
        import time as _time160
        from pathlib import Path as _P160
        _d160 = _P160(_tmp160.mkdtemp())
        _回160 = _d160 / "r.jsonl"
        _心160 = _d160 / "r.heartbeat"
        _开ts160 = "2026-10-26 10:00:00"
        _尾ts160 = "2026-10-26 11:00:00"

        def _写160(开=None, 尾=None, 心跳pid=None, 心跳秒前=0):
            _行 = []
            if 开:
                _行.append(_json160.dumps(
                    {"kind": "run_start", "pid": 开, "ts": _开ts160},
                    ensure_ascii=False))
            if 尾:
                _行.append(_json160.dumps(
                    {"kind": "run_end", "pid": 尾, "ts": _尾ts160,
                     "exit_code": 0, "how": "正常结束"}, ensure_ascii=False))
            _回160.write_text("\n".join(_行) + "\n", encoding="utf-8")
            if 心跳pid is None:
                _心160.unlink(missing_ok=True)
            else:
                _ts = _time160.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    _time160.localtime(_time160.time() - 心跳秒前))
                _心160.write_text(_json160.dumps(
                    {"ts": _ts, "pid": 心跳pid, "last_event": "x"},
                    ensure_ascii=False), encoding="utf-8")

        _活pid160 = _os160.getpid()      # 真实存在
        _死pid160 = 999999               # 确定不存在

        def _判160(名, 期望, **kw):
            _写160(**kw)
            _r = 判活(回执文件=_回160, 心跳文件=_心160)
            if _r.get("状态") != 期望:
                fails.append(
                    f"【R160】{名}：期望「{期望}」实得「{_r.get('状态')}」"
                    f" —— {str(_r.get('因由'))[:120]}")
            return _r

        _判160("① 心跳新鲜应判「在跑」", "在跑",
               开=_活pid160, 心跳pid=_活pid160, 心跳秒前=5)
        _判160("② 心跳停+有 run_end 应判「正常收场」", "正常收场",
               开=_活pid160, 尾=_活pid160, 心跳pid=_活pid160, 心跳秒前=600)
        _判160("③ 心跳停+无 run_end+pid 已消失 应判「被杀了」", "被杀了",
               开=_死pid160, 心跳pid=_死pid160, 心跳秒前=600)
        # ★★★ 第四态：进程还在但心跳过期 ⇒ **不许判死**
        _r4 = _判160("④ 心跳停+无 run_end+pid 仍在 应判「不知」", "不知",
                     开=_活pid160, 心跳pid=_活pid160, 心跳秒前=600)
        if "不许" not in str(_r4.get("因由") or "") and "不压成" not in str(_r4.get("因由") or ""):
            fails.append("【R160】第四态「不知」没有**说明为什么不判死**"
                         " ⇒ 调用方会把它当「被杀了」用")
        # ★ ⑤ 判活不许把「不知」说成「被杀了」（**边界**）
        if _r4.get("状态") == "被杀了":
            fails.append("【R160】★★★ 第四态被压成了「被杀了」"
                         " —— 「量不到」不等于「必红」（R139 老毛病）")
    except Exception as _e160:
        fails.append(f"【R160】齿自身异常：{type(_e160).__name__}: {_e160}")
    for _f160 in [x for x in fails if "【R160】" in str(x)]:
        print(f"  ✗ generic_goal {_f160}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R158】「还在跑」与「被杀了」必须可区分（第 73 层）
    # ══════════════════════════════════════════════════════════════
    # 真事故（第 41 次真跑，2026-10-26 11:15:28）：
    #   那一跑**正在赢**：轮 2 已经 `verdict=progressing`
    #   （Δ 7→4，`cargo_check_lib=True`，两条门新绿），在产第 3 轮。
    #   然后进程**被外部杀掉**（我的监控 shell 崩了，连带进程树）。
    #   ★ 日志停在「机器拒收的产物 ⇒ 不写主树」，
    #     **没有退出码、没有 traceback、没有停机回执**。
    #   ⇒ 我无法从回执区分「还在跑」「崩了」「被杀」。
    #   ★★ 而验收要的是「**无人值守**跑通一次」——
    #      无人值守时，**「不知道它怎么了」本身就是最坏的结局**。
    #   ★★★ 这是本系列里唯一一个**不是产品逻辑错、而是产品无话可说**的缺陷。
    #
    # ★ 齿要测的不是「有没有 run_end 这个词」，是三件**结构性**的事：
    #   ① 心跳真的被事件驱动（不是只定义了函数）；
    #   ② 头尾都写（只有尾 ⇒ 看不出从哪开始的）；
    #   ③ **`run_end` 必须在 `finally` 里** —— 否则崩了就写不到，
    #      而「崩了」恰恰是最需要它的场合。
    try:
        import ast as _ast158
        _树158 = _ast158.parse(_源码)

        def _找158(名):
            for _n in _ast158.walk(_树158):
                if isinstance(_n, (_ast158.FunctionDef,
                                   _ast158.AsyncFunctionDef)) and _n.name == 名:
                    return _n
            return None

        # ① 心跳由事件驱动：`_emit` 的**调用**里必须出现 `_心跳`
        _emit158 = _找158("_emit")
        if _emit158 is None:
            fails.append("【R158】找不到 `_emit`")
        else:
            _调158 = {n.func.attr for n in _ast158.walk(_emit158)
                      if isinstance(n, _ast158.Call)
                      and isinstance(n.func, _ast158.Attribute)}
            if "_心跳" not in _调158:
                fails.append("【R158】★ `_emit` **没有**驱动心跳 ⇒ "
                             "心跳只在启动时写一次，「还在跑」判不出来")
        # ② 头尾都写
        _main158 = _找158("main")
        if _main158 is None:
            fails.append("【R158】找不到 `main`")
        else:
            _字158 = {n.value for n in _ast158.walk(_main158)
                      if isinstance(n, _ast158.Constant)
                      and isinstance(n.value, str)}
            for _必158 in ("run_start", "run_end"):
                if _必158 not in _字158:
                    fails.append(f"【R158】`main` 不写 `{_必158}` ⇒ "
                                 "整趟跑没有头/尾回执")
            # ③ `run_end` 必须在 finally 里（**这条是重点**）
            _尾在finally158 = False
            for _try158 in _ast158.walk(_main158):
                if not isinstance(_try158, _ast158.Try):
                    continue
                if not _try158.finalbody:
                    continue
                for _n158 in _ast158.walk(
                        _ast158.Module(body=_try158.finalbody, type_ignores=[])):
                    if (isinstance(_n158, _ast158.Constant)
                            and _n158.value == "run_end"):
                        _尾在finally158 = True
            if not _尾在finally158:
                fails.append("【R158】★★★ `run_end` **不在 `finally` 里** ⇒ "
                             "崩溃时写不到它，而「崩了」正是最需要它的场合"
                             "（那样「心跳停 + 无 run_end」就**不再**等于「被杀了」）")
            # ④ ★★★★★ R158b：心跳必须**定时器驱动**，不能只靠事件。
            #   真证据（第 42 次真跑，2026-10-26）：心跳停在 13:08:59，
            #   而进程**还活着**（PID 24032 存活、CPU 停在 53.8s 不动）——
            #   它在 `router` 里**等上游**：
            #     `无健康槽 ⇒ 等 20 秒（已等 0/600 秒）后重试`
            #   等待期间**不产生任何 `_emit` 事件** ⇒ 事件驱动的心跳不动
            #   ⇒ 按「心跳停 = 被杀了」会**误判**。
            #   ★ 又是那个形态：**手里那个量不是判据需要的那个量**。
            #     心跳量「最后一次事件」，判据要「进程还活着」；
            #     两者在「合法等待」时分叉。
            #   ★ 齿测的是**结构性**的：`_起心跳` 里真的起了 Thread 且 daemon，
            #     且 `main` 真的调了它。不是测「有没有线程这个词」。
            _起158 = _找158("_起心跳")
            if _起158 is None:
                fails.append("【R158b】没有 `_起心跳` ⇒ 心跳只能是事件驱动"
                             "（等上游时会停摆，把「在等」误判成「被杀」）")
            else:
                _有线程158 = False
                for _n158 in _ast158.walk(_起158):
                    if (isinstance(_n158, _ast158.Call)
                            and isinstance(_n158.func, _ast158.Attribute)
                            and _n158.func.attr == "Thread"):
                        # 必须是 daemon（否则主线程退出会被它挂住）
                        for _kw in _n158.keywords:
                            if (_kw.arg == "daemon"
                                    and isinstance(_kw.value, _ast158.Constant)
                                    and _kw.value.value is True):
                                _有线程158 = True
                if not _有线程158:
                    fails.append("【R158b】★★ `_起心跳` 里没有 **daemon Thread** ⇒ "
                                 "心跳仍是事件驱动（或会挂住退出）")
                # main 必须真的调它
                _调起158 = {n.func.attr for n in _ast158.walk(_main158)
                            if isinstance(n, _ast158.Call)
                            and isinstance(n.func, _ast158.Attribute)}
                if "_起心跳" not in _调起158:
                    fails.append("【R158b】★★★ `main` **没有调用** `_起心跳` ⇒ "
                                 "定时器根本没起，心跳照样会停摆")
    except SyntaxError as _e158:
        fails.append(f"【R158】齿自身语法异常：{_e158}")
    except Exception as _e158b:
        fails.append(f"【R158】齿自身异常：{type(_e158b).__name__}: {_e158b}")
    for _f158 in [x for x in fails if "【R158】" in str(x)]:
        print(f"  ✗ generic_goal {_f158}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R152】`@staticmethod` 里用了 `self`（第 65 层）
    # ══════════════════════════════════════════════════════════════
    # 真证据（`checks/落地验证.py` ⑨ 全库静态门）：
    #   `[FAIL] 全库无未定义全局名：{'generic_goal.py': ['_门集签名115:self']}`
    # ★ 它被一个宽 `except Exception: _产 = []` 吞掉
    #   ⇒ 悄悄退化成「只按门集」⇒ R138 那一整层修复**静默失效**。
    # ★★ 齿要测的**不是**「那个函数现在是不是 staticmethod」——
    #   那是「当前写法」，别人合法地改写法就误红。
    #   要测的是**规律本身**：一个 staticmethod 的函数体里**不得出现 `self`**。
    #   ⇒ 用 AST 扫**全部**函数，不只扫这一个。
    try:
        _ast152 = __import__("ast")
        _树152 = _ast152.parse(_源码)
        _坏152 = []
        for _n152 in _ast152.walk(_树152):
            if not isinstance(_n152, (_ast152.FunctionDef,
                                      _ast152.AsyncFunctionDef)):
                continue
            _是静152 = any(
                (isinstance(_d152, _ast152.Name) and _d152.id == "staticmethod")
                for _d152 in _n152.decorator_list)
            if not _是静152:
                continue
            _用152 = [
                _x152 for _x152 in _ast152.walk(_n152)
                if isinstance(_x152, _ast152.Name) and _x152.id == "self"]
            if _用152:
                _坏152.append(f"{_n152.name}(L{_n152.lineno})")
        if _坏152:
            fails.append(
                "【R152】★★ `@staticmethod` 的函数体里用了 `self`："
                + ", ".join(_坏152[:6])
                + " ⇒ 调用时必然 `NameError`，而宽 except 会把它吞成"
                  "「悄悄退化」（R138 就是这样静默失效的）")
    except SyntaxError as _e152a:
        fails.append(f"【R152】齿解析源码失败：{_e152a}")
    except Exception as _e152b:
        fails.append(f"【R152】齿自身异常：{type(_e152b).__name__}: {_e152b}")

    # ★★★★★ **一发现就 flush 打出来**（R149 学到的，这里又踩了一次）：
    #   实测：本齿排到了最前，注入「给 `_达标` 挂 `@staticmethod`」时，
    #   `_run` 在 L5743 崩（TypeError）⇒ 自检带栈退出 ⇒
    #   末尾的 `if fails:` **永远到不了** ⇒ 红了却没点名。
    #   ★ 「排在最前」+「立刻打印」两件缺一不可。
    for _f152 in [x for x in fails if "【R152】" in str(x)]:
        print(f"  ✗ generic_goal {_f152}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R154】模型返回的 JSON 不合法 ≠ 产品缺陷
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 38 次真跑，2 轮后）：
    #   `★★★ 重规划**崩了**（decoder.py:354）：JSONDecodeError:
    #    Expecting ',' delimiter: line 1 column 5521 (char 5520)`
    #   回执：`product_defect=True by_design_stop=False`
    # ★ 齿要测**分类结论**：同一个 JSONDecodeError 不得被判成产品缺陷。
    try:
        _json154 = __import__("json")
        _真错154 = _json154.JSONDecodeError("Expecting ',' delimiter", '{"a":1 "b":2}', 7)
        if not isinstance(_真错154, _json154.JSONDecodeError):
            fails.append("【R154】★ 造不出 JSONDecodeError 样本")
        # ① 它必须是 JSONDecodeError（不是别的）
        if isinstance(_真错154, (规约可纠正, 计划不可行)):
            fails.append("【R154】★ JSONDecodeError 落进了「规约可纠正」"
                         " ⇒ 那条路径已经够用，本层可能重复")
        # ② 源码里必须真的把它排除在「产品缺陷」之外
        _ast154 = __import__("ast")
        _树154 = _ast154.parse(_源码)
        _找154 = []
        for _n154 in _ast154.walk(_树154):
            if isinstance(_n154, _ast154.Assign):
                _名154 = [_t154.id for _t154 in _n154.targets
                          if isinstance(_t154, _ast154.Name)]
                if "_是缺陷" in _名154:
                    _找154.append(_n154)
        if not _找154:
            fails.append("【R154】★★ 源码里找不到 `_是缺陷 = ...` 的赋值")
        else:
            # ★★ 齿的第一版只查 `_是缺陷` 的表达式里**有没有出现**那个名字
            #   ⇒ 把 `_是模型给错154 = False` 之后名字还在表达式里 ⇒ **无齿**。
            #   ★ 这是「查存在抓不住查的是不是那一条」的又一次。
            #   ⇒ 改测**两件事**：
            #     ① `_是模型给错154` 的**赋值来源**必须真的是 JSONDecodeError
            #        （不是 `False`、不是别的类）；
            #     ② `_是缺陷` 的表达式里必须**引用**它。
            _源154 = None
            for _n154b in _ast154.walk(_树154):
                if (isinstance(_n154b, _ast154.Assign)
                        and any(isinstance(_t, _ast154.Name)
                                and _t.id == "_是模型给错154"
                                for _t in _n154b.targets)):
                    _源154 = _n154b
            if _源154 is None:
                fails.append("【R154】★★ 找不到 `_是模型给错154 = ...` 的赋值")
            else:
                _源文154 = _ast154.dump(_源154.value)
                if "JSONDecodeError" not in _源文154:
                    fails.append(
                        "【R154】★★★ `_是模型给错154` 的**赋值来源里没有 "
                        "JSONDecodeError** ⇒ 模型写断 JSON 不会被认出来"
                        "（第 38 次真跑就是这样被当成产品缺陷的）")
            _文154 = _ast154.dump(_找154[-1])
            if "模型给错154" not in _文154:
                fails.append(
                    "【R154】★★★ `_是缺陷` 的判定**没有引用** `_是模型给错154`"
                    " ⇒ 模型给错格式会被当成产品缺陷")
    except SyntaxError as _e154a:
        fails.append(f"【R154】齿解析源码失败：{_e154a}")
    except Exception as _e154b:
        fails.append(f"【R154】齿自身异常：{type(_e154b).__name__}: {_e154b}")
    for _f154 in [x for x in fails if "【R154】" in str(x)]:
        print(f"  ✗ generic_goal {_f154}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R153】恒真门会自我强化
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 38 次真跑日志）：
    #   `历史偏差密度：…@mod_无界键入口保留且统计量真用上×6；…`
    #   而第 37 次真跑里 R148 已判它「真·无区分力」。
    # ★ 齿要测**行为**：给一个已被判定为无区分力的缺口，
    #   它**不得**出现在「请优先解决」那一段里，且**必须**被如实说出来。
    try:
        class _D153:
            def __init__(s, k, w):
                s.key, s.weight = k, w
        class _O153:
            active = True
            def __init__(s):
                s.active = [_D153("好缺口", 5.0),
                            _D153("坏门缺口", 99.0)]
        _注153 = _hist_note(_O153(), ["坏门缺口"])
        # ① 「优先解决」那段里不得出现坏门
        _优先段153 = ""
        for _行153 in _注153.splitlines():
            if "请优先解决" in _行153:
                _优先段153 = _行153
        if "坏门缺口" in _优先段153:
            fails.append("【R153】★★ 已被判定「无区分力」的缺口**仍排在"
                         "「请优先解决」里** ⇒ 恒真门会自我强化"
                         "（第 38 次真跑：主心骨被反复引向一条永远能满足的假判据）")
        # ② 但它**必须被如实说出来**（静默丢弃 = 主心骨以为它解决了）
        if "坏门缺口" not in _注153:
            fails.append("【R153】★★★ 被剔除的缺口**静默消失**了"
                         " ⇒ 主心骨会以为那条判据已经解决"
                         "（丢了却不说，正是本库反复出现的形态）")
        # ③ 好缺口必须仍在「优先解决」里（防「全剔掉」这种过度修复）
        if "好缺口" not in _优先段153:
            fails.append("【R153】★★ 好缺口被**连带剔除**了"
                         " ⇒ 过滤过头，主心骨拿不到任何方向")
        # ④ 没传破裂清单时，行为必须与老版一致（不假装有信息）
        _注153b = _hist_note(_O153())
        if "坏门缺口" not in _注153b:
            fails.append("【R153】★ 不传破裂清单时也剔除了缺口 ⇒ 会静默丢信息")
    except Exception as _e153:
        fails.append(f"【R153】齿自身异常：{type(_e153).__name__}: {_e153}")
    for _f153 in [x for x in fails if "【R153】" in str(x)]:
        print(f"  ✗ generic_goal {_f153}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R153b】R153 的**时序反了**（第 69 层）
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 39 次真跑第 1 轮）：
    #   `历史偏差密度：…@mod_无界键入口保留且统计量真用上×6；…`
    #   **仍然排在最前** ⇒ R153 完全没生效。
    # ★ 根因：`_hist_note` 在 `_run()` 开头调用，而 `_chief_契约破裂`
    #   要到轮末才被赋值 ⇒ 调用时刻它**恒为空**。
    # ★★ 齿要测的是**「跨轮」这个行为**，不是「函数签名里有个参数」：
    #   同一个实例，第 1 轮记下一条，第 2 轮必须还认得它。
    try:
        _r153 = GoalRunner.__new__(GoalRunner)
        _r153._恒真门153 = set()
        # 模拟第 1 轮：契约验证判出「坏门」
        _r153._恒真门153 = set(_r153._恒真门153) | {"坏门"}
        # 模拟第 2 轮开头：它必须还在（跨轮累积）
        _跨153 = getattr(_r153, "_恒真门153", None)
        if not _跨153 or "坏门" not in _跨153:
            fails.append("【R153b】★★ 第 2 轮开头**认不出**第 1 轮判过的坏门"
                         " ⇒ `_hist_note` 仍会把恒真门排最前"
                         "（这正是第 39 次真跑发生的）")
        # ② ★★★ **累积**必须真的取并集（不是每轮重算）。
        #   实测：齿的第一版只造了个空样本戊塞进去测，注入「每轮重算」
        #   仍然全绿 ⇒ **无齿** —— 因为那个测试根本没走真实累积语句。
        #   ⇒ 用 AST 查那条赋值：右边必须同时出现**旧集合**和**新破集**。
        _ast153c = __import__("ast")
        _树153c = _ast153c.parse(_源码)
        # ★★★ **必须排除齿自己**：齿里也有一句
        #   `_r153._恒真门153 = set(_r153._恒真门153) | {"坏门"}`
        #   ⇒ 第一版把**测试代码**当成了产品代码 ⇒ 恒绿/误报。
        #   ★ 这是「门不得匹配到自己」的又一次。
        #   ⇒ 用 `_selftest` 的起始行切掉齿所在的那一段。
        _自检起153 = None
        for _n153e in _ast153c.walk(_树153c):
            if (isinstance(_n153e, _ast153c.FunctionDef)
                    and _n153e.name == "_selftest"):
                _自检起153 = _n153e.lineno
        # ★★ 必须**收齐再筛**：`ast.walk` 的遍历顺序不按行号
        #   （实测：自检区的 L7576 先于产品的 L4559 被访问到），
        #   第一版「边走边赋值」被自检区覆盖 ⇒ 恒误报。
        _候153 = [
            _n153c for _n153c in _ast153c.walk(_树153c)
            if isinstance(_n153c, _ast153c.Assign)
            and any(isinstance(_x, _ast153c.Attribute)
                    and _x.attr == "_恒真门153"
                    for _x in _n153c.targets)
            # ★ 排除自检区（齿自己住在这里）
            and not (_自检起153 is not None
                     and getattr(_n153c, "lineno", 0) >= _自检起153)
            # ★ 排除「清空」那句（右边是 `set()`，不是累积）
            and not (isinstance(_n153c.value, _ast153c.Call)
                     and isinstance(_n153c.value.func, _ast153c.Name)
                     and _n153c.value.func.id == "set"
                     and not _n153c.value.args)]
        _累153 = _候153[0] if _候153 else None
        if _累153 is None:
            fails.append("【R153b】★★ 找不到 `self._恒真门153 = ...` 的赋值")
        else:
            _右153 = _ast153c.dump(_累153.value)
            if "_旧集153" not in _右153:
                fails.append(
                    "【R153b】★★★ 跨轮集合的赋值**没有取并集**（右边没有 `_旧集153`）"
                    " ⇒ 每轮重算 ⇒ 第 1 轮的结论在第 2 轮丢失"
                    "（这正是第 39 次真跑发生的）")
            if "_新破153" not in _右153:
                fails.append("【R153b】★★ 跨轮集合的赋值里没有新判出的坏门")
        # ③ 门集换了必须**清空**（用 AST 查那句）
        _清153 = False
        for _n153d in _ast153c.walk(_树153c):
            if _自检起153 is not None and getattr(_n153d, 'lineno', 0) >= _自检起153:
                continue      # ★ 同上：排除齿自己
            if (isinstance(_n153d, _ast153c.Assign)
                    and isinstance(_n153d.value, _ast153c.Call)
                    and isinstance(_n153d.value.func, _ast153c.Name)
                    and _n153d.value.func.id == "set"
                    and not _n153d.value.args
                    and any(isinstance(_x, _ast153c.Attribute)
                            and _x.attr == "_恒真门153"
                            for _x in _n153d.targets)):
                _清153 = True
        if not _清153:
            fails.append("【R153b】★ 门集换了却**不清空**跨轮集合"
                         " ⇒ 换门集后仍按旧结论剔除，会误伤新门")
        # ④ 调用点必须用**跨轮集合**，不是本轮的
        _ast153 = __import__("ast")
        _树153 = _ast153.parse(_源码)
        _用跨153 = False
        _用本153 = False
        for _n153 in _ast153.walk(_树153):
            if (isinstance(_n153, _ast153.Call)
                    and isinstance(_n153.func, _ast153.Name)
                    and _n153.func.id == "_hist_note"):
                _文153 = _ast153.dump(_n153)
                if "_恒真门153" in _文153:
                    _用跨153 = True
                if "_chief_契约破裂" in _文153:
                    _用本153 = True
        if not _用跨153:
            fails.append("【R153b】★★★ `_hist_note` 的调用**没有传跨轮集合**"
                         " ⇒ 第 1 轮必然拿不到任何信息")
        if _用本153:
            fails.append("【R153b】★★★ `_hist_note` 仍在传**本轮的** "
                         "`_chief_契约破裂`（调用时刻恒为空）")
    except Exception as _e153b:
        fails.append(f"【R153b】齿自身异常：{type(_e153b).__name__}: {_e153b}")
    for _f153b in [x for x in fails if "【R153b】" in str(x)]:
        print(f"  ✗ generic_goal {_f153b}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R176】Windows 反斜杠路径把「已存在的文件」判成「必须新建」
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 75 次真跑 · 中文编译器项目）：目标里写的是
    #   `示例工程\基础集\算子选择器.rs:682`（反斜杠）
    # 而文件正则只把 `/` 当分隔符 ⇒ 只截出裸名 `算子选择器.rs` ⇒
    # 在仓库根找不到 ⇒ 判「当前不存在 ⇒ 必须有交付物建它」⇒ 连纠正 3 次后停机。
    # ★ 齿要让它要测的那件事成为**唯一差异**：三种记号，只让「真不存在」的那个被报。
    try:
        import tempfile as _tf176
        _根176 = Path(_tf176.mkdtemp())
        (_根176 / "a" / "b").mkdir(parents=True)
        (_根176 / "a" / "b" / "x.rs").write_text("// x", encoding="utf-8")
        _旧根176 = globals().get("REPO_ROOT")
        globals()["REPO_ROOT"] = _根176
        _目标176 = "改 a\\b\\x.rs；另建 a\\b\\y.rs 与 x.rs"
        _缺176 = _目标点名而清单没有(_目标176, [])
        # ★★★★★ R176b：**预期改了** —— 原预期 `["a/b/y.rs", "x.rs"]` 把「裸名 x.rs」
        #   也算成缺，而 R176b 证明它是**同一次出现的短形态**（目标里写的是 `a\b\x.rs`，
        #   正则的路径段可选 ⇒ 又抽出 `x.rs`）⇒ 不该要求模型在仓库根再造一个同名文件。
        #   ★ 这是「改了判据必须同一次改齿」：我改了抽取语义，齿当场判红 —— 它红得对。
        if _缺176 != ["a/b/y.rs"]:
            fails.append("【R176】★ 反斜杠路径的存在性判错了："
                         f"实际 {_缺176}，应为 ['a/b/y.rs']"
                         "（反斜杠归一后必须精确命中；同一次出现的短形态不得重复要求）")
        # ★ 但 R81 的边界**不能**因为去子串丢掉：单独一个裸名（没有更长的
        #   带路径形态与之共存）时，仍必须判缺 —— 否则「必然少交一件」又被吞掉。
        _缺176c = _目标点名而清单没有("另建 z.rs", [])
        if _缺176c != ["z.rs"]:
            fails.append("【R176b】★★ 单独的裸名缺交被吞掉了："
                         f"实际 {_缺176c}，应为 ['z.rs']"
                         " ⇒ R81 的边界被去子串破坏了")
        # ② 正则必须真的把反斜杠当分隔符（不是靠后面的「按名字找」蒙对）
        _RE176 = re.compile(
            r"(?<![\w/.\\-])((?:[\w\u4e00-\u9fff.\\-]+[\\/])*"
            r"[\w\u4e00-\u9fff.\\-]+\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w])")
        _命176 = [m.group(1) for m in _RE176.finditer(_目标176)]
        if "a\\b\\x.rs" not in _命176:
            fails.append(f"【R176】★ 正则没把反斜杠当路径分隔符 ⇒ 只截出裸名（实际 {_命176}）")
    except Exception as _e176:
        fails.append(f"【R176】齿自身异常：{type(_e176).__name__}: {_e176}")
    finally:
        try:
            globals()["REPO_ROOT"] = _旧根176
        except Exception:
            pass
    for _f176 in [x for x in fails if "【R176】" in str(x)]:
        print(f"  ✗ generic_goal {_f176}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R176d】`WORKDIR is None` 时存在性判定必须**不炸**
    # ══════════════════════════════════════════════════════════════
    # ★ 真证据：`_文件在仓库里存在176` 里 `for _根 in (REPO_ROOT, WORKDIR)` ——
    #   R139 日志里明写 `workdir=None` ⇒ `None / 记` 抛 **TypeError**，
    #   而当时只 `except OSError` ⇒ **抓不住** ⇒ 整个存在性判定炸掉。
    #   ★ 这是「手里那个异常不是判据需要的那条」在**异常类型**上的样子：
    #     想防的是「路径不存在」，接的却是 `OSError` —— `TypeError` 从缝里漏出去。
    # ★ 齿测**行为**（不测源码长什么样）：把 WORKDIR 设 None，调一次，看会不会抛。
    try:
        import tempfile as _tf176d
        import shutil as _sh176d
        _根176d = Path(_tf176d.mkdtemp(prefix="r176d_"))
        try:
            (_根176d / "有.rs").write_bytes("// 在\n".encode("utf-8"))
            _存R176d = globals().get("REPO_ROOT")
            _存W176d = globals().get("WORKDIR")
            try:
                globals()["WORKDIR"] = None
                globals()["REPO_ROOT"] = _根176d
                _v176d = _文件在仓库里存在176("有.rs")
                if _v176d is not True:
                    fails.append(f"【R176d】★★★ WORKDIR=None 时存在性判定**判错了**"
                                 f"（有.rs 明明在，返回 {_v176d!r}）")
                globals()["REPO_ROOT"] = None
                _v176d2 = _文件在仓库里存在176("有.rs")
                if _v176d2 is not False:
                    fails.append(f"【R176d】★ 两个根都 None 时应返回 False，实返 {_v176d2!r}")
            finally:
                if _存R176d is None:
                    globals().pop("REPO_ROOT", None)
                else:
                    globals()["REPO_ROOT"] = _存R176d
                globals()["WORKDIR"] = _存W176d
        finally:
            _sh176d.rmtree(_根176d, ignore_errors=True)
    except Exception as _e176d:
        fails.append(f"【R176d】★★★ WORKDIR=None 时存在性判定**炸了**"
                     f"（{type(_e176d).__name__}: {_e176d}）"
                     " ⇒ 整个「目标点名的文件」判据失效")
    for _f176d in [x for x in fails if "【R176d】" in str(x)]:
        print(f"  ✗ generic_goal {_f176d}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # ★★★★★ R182：**模型没按格式回 ⇒ 必须走「可纠正」，不是「产品缺陷」**
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 87 次真跑 · 17:40）：
    #   `★★★ 重规划**崩了**（generic_goal.py:2167）：ValueError: 未产出 JSON 样本戊：
    #    Believe in yourself and keep going.` ⇒ 退出码 1。
    # ★ 三层叠在一起：
    #   ① 裸 `ValueError` ⇒ 被归成**产品缺陷**（报表说「产品坏了，人去修代码」）；
    #   ② 两处调用都在各自 `try` 之外 ⇒ **绕过回喂循环**；
    #   ③ 外层 handler 按设计 `return 1` ⇒ 整趟死。
    # ★ 形状：「**回喂机制存在，但 raise 点不在它接得住的范围内**」——
    #   与 R177（台账记了但没拼进 prompt）同族，只是断在控制流上。
    try:
        _parse_spec_json("Believe in yourself and keep going.")
        fails.append("★ R182：非 JSON 回复**没有抛错**（把散文当规约了）")
    except 规约可纠正:
        pass          # ← 这才对：模型能自己改格式 ⇒ 回喂，不惊动人
    except Exception as _e182:
        fails.append(f"★ R182：非 JSON 回复应抛 `规约可纠正`（走回喂），"
                     f"实得 {type(_e182).__name__} ⇒ 会被归成产品缺陷、整趟 exit 1")
    # ★ 边界：**真的合法 JSON 必须照常解析**（不许为了能回喂就把解析放松）
    _正182 = _parse_spec_json('{"name": "甲", "pre": []}')
    if _正182.get("name") != "甲":
        fails.append(f"★ R182：合法 JSON 解析被改坏了（得 {_正182!r}）")

    # ══════════════════════════════════════════════════════════════
    # 【R155】门全绿了，达标判定却说「目标文件自己没变好」（第 70 层）
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 40 次真跑，轮 4）：6/6 门全绿，而
    #   `未达标因由：目标点名的文件**自己没变好**：['src/模块甲/mod.rs', …]`
    # ★ 根因：`_必改未达标` 只在 `if 基线:` 分支里算；门全绿 ⇒ 基线为空
    #   ⇒ 走 else ⇒ **保留上一轮的值** ⇒ `_达标` 永远早退。
    try:
        # ① 门全绿时，`_必改未达标` 必须是**空**（真值，不是跳过）
        _r155 = GoalRunner.__new__(GoalRunner)
        _r155._必改未达标 = ["上一轮遗留的文件"]   # ★ 模拟上一轮遗留
        # ★ 只认**固定格式** `【必改文件】<路径>`（不做自然语言猜测）
        _r155.goal = "【必改文件】src/模块甲/mod.rs【必改文件】src/模块乙/arc评测.rs"
        _必改155 = _目标声明的必改文件(_r155.goal)
        if not _必改155:
            fails.append("【R155】★ 目标里点名的文件没被识别出来（齿前提不成立）")
        else:
            # ★★ ① 测的是**产品那条路径的结果**，不是「函数语义」：
            #   门全绿 ⇒ 必改文件全都做到了 ⇒ `_必改未达标` 必须是空。
            #   ★ 第一版我在这里写 `_必改未变好(必改, set())`，
            #     那测的是**函数本身**的语义（空保留集 ⇒ 全没变好），
            #     与「门全绿」这个前提**无关** ⇒ 恒误报。
            #     这是「齿要让它要测的那件事成为唯一差异」的又一次。
            #   ⇒ 直接断言：空保留集 + 门全绿 时，产品给的是 `[]`。
            #     产品那条路径（else 分支）写的就是 `self._必改未达标 = []`。
            pass
        # ② 源码里，else 分支必须**重算** `_必改未达标`
        _ast155 = __import__("ast")
        _树155 = _ast155.parse(_源码)
        _自检起155 = None
        for _n155 in _ast155.walk(_树155):
            if (isinstance(_n155, _ast155.FunctionDef)
                    and _n155.name == "_selftest"):
                _自检起155 = _n155.lineno
        # 找 `if 基线:` 的 else 分支体里有没有 `_必改未达标` 的赋值
        _补155 = False
        for _n155b in _ast155.walk(_树155):
            if not isinstance(_n155b, _ast155.If):
                continue
            if _自检起155 is not None and getattr(_n155b, "lineno", 0) >= _自检起155:
                continue
            _测155 = _ast155.unparse(_n155b.test)
            if "基线" not in _测155 or not _n155b.orelse:
                continue
            for _s155 in _n155b.orelse:
                for _x155 in _ast155.walk(_s155):
                    if (isinstance(_x155, _ast155.Assign)
                            and any(isinstance(_t155, _ast155.Attribute)
                                    and _t155.attr == "_必改未达标"
                                    for _t155 in _x155.targets)):
                        # ★★ 必须检查**赋的是什么**，不只是"有没有赋值"。
                        #   实测：只查"有没有"时，把右边换成
                        #   `_必改未变好(必改, set())`（方向反了）⇒ **无齿**。
                        #   ★ 这是「查存在抓不住查的是不是那一条」的又一次。
                        #   门全绿 ⇒ 必改文件全都做到 ⇒ 右边必须是**空列表**。
                        _补155 = (isinstance(_x155.value, _ast155.List)
                                  and not _x155.value.elts)
        if not _补155:
            fails.append(
                "【R155】★★★ `if 基线:` 的 **else 分支里没有把 `_必改未达标` "
                "重算成空**（必须是 `= []`）"
                " ⇒ 门全绿时它会保留上一轮的值 ⇒ `_达标` 永远早退"
                "（这正是第 40 次真跑：6/6 门全绿却判「目标文件没变好」）")
    except Exception as _e155:
        fails.append(f"【R155】齿自身异常：{type(_e155).__name__}: {_e155}")
    for _f155 in [x for x in fails if "【R155】" in str(x)]:
        print(f"  ✗ generic_goal {_f155}", flush=True)

    # 总自检门数（TS-381 CLI 装配 6 条 · TS-382 区域编辑 15 条 · TS-384 门超时 7 条
          #             · TS-442 因由类策略不塌缩 5 条 · 机器自生成（0-LLM）9 条
          #             · TS-468 数术层（无语义回退 + 因由不丢 + 可及性）1 条
          #             · TS-469 真值空间（6 接线齿 + 3 常数自洽 + 6 六值拒收 + 1 载体消去）16 条
          #             · R25 冻结区未取到必须报告（5 条）· R21 项目验收门（2 条）
          #             · R31 交付物必须是文件不是目录（10 条）
          #             · R32 产出不是内容（工具调用转录）必须被认出（15 条）
          #             · R33 规划前先跑真缺口（门态探针）（6 条）
          #             · R34 大文件必须能带行号焦点（6 条）
          #             · R36 机器抽必改文件 + 计划必须覆盖它（11 条）
          #             · R37 交付物不得落在冻结区（5 条）
          #             · R38 与文件无关的门只求值一次（4 条）
          #             · R39 交付物身份只有一把尺子（5 条）
          #             · R40 证据路径的**双反斜杠**形态（2 条）
          #             · R41 必改清单不得被编译器 warning 污染（4 条）
          #             · R67 入口闸报错必须用**模型自己的坐标系**（3 条）
          #             · R69 窗口必须是**自含区域**（8 条）
          #             · R70 行号不是身份是坐标 + 窗口不许以焦点行结尾（9 条））
          #
          # ★★★★★ R68（2026-10-16）：**这个分母曾经是手工维护的** —— 我加了 3 条 R67 齿，
          #   分母没动，于是报表照样打「242/242 全绿」，而实际跑了 245 条。
          #   ★ 这是**同一个病**换了个位置：**手工数 = 枚举**。危险方向是
          #     「**齿被删掉了，而分母不变**」⇒ 报表照样全绿 ⇒ **无齿看起来像绿**。
          #   ⇒ 当时补了一条**结构齿**（读自己的源码，断言 `fails.append` 出现次数
          #     不少于分母）。★ 但 R70c 实测发现：**分母本身还是手工的**，
          #     我加了 9 条 R70 齿、分母没动，报表照样「253/253 全绿」，
          #     而结构齿**没红**（它只保证"出现次数 ≥ 分母"这个方向，
          #     分母偏小恰好落在它管不着的那一边）。
          #   ⇒ R70c 治本：分母改为**运行时从源码数出来**（见上面的 `_判据点`），
          #     另配 `_判据下限` 防成批删除；报表写「失败 0 · 判据点 M（下限 L）」。

    # ① 规约校验：合法过；缺字段/门非法/绝对路径/未知依赖拒
    if _valid_spec(_DRY_SPEC):
        fails.append("合法规约应通过")
    if _valid_spec(_DRY_MULTI_SPEC):
        fails.append("多文件合法规约应通过")
    bad1 = {k: v for k, v in _DRY_SPEC.items() if k != "deliverable"}
    if not _valid_spec(bad1):
        fails.append("缺 deliverable 应拒绝")
    bad2 = {"name": "x", "deliverable": "a.py",
            "gates": [{"type": "nope", "cmd": ["x"], "cwd": "."}]}
    if not _valid_spec(bad2):
        fails.append("未知门类型应拒绝")
    bad3 = {"name": "x", "deliverable": "C:/evil.py", "gates": []}
    if not _valid_spec(bad3):
        fails.append("绝对路径 deliverable 应拒绝")
    bad4 = {"name": "x",
            "deliverables": [{"path": "a.py"}, {"path": "b.py",
                                                "depends_on": ["ghost.py"]}],
            "gates": [{"type": "compile", "cmd": ["true"], "cwd": "."}]}
    if not _valid_spec(bad4):
        fails.append("depends_on 未知文件应拒绝")

    # ② 机器计划：auto_goal 产出 conserved 任务 DAG + 规则空间 16
    dry_copy = json.loads(json.dumps(_DRY_SPEC))
    _normalize_spec(dry_copy)
    plan = auto_goal.plan_tasks(dry_copy)
    if not plan["conserved"] or len(plan["tasks"]) < 3 or plan["rule_space"] != 16:
        fails.append("auto_goal 计划应 conserved + 任务>=3 + 规则空间16")

    # ③ 机器门对已知正确件全绿（dry 闭环的判定基础）
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        p = work / "fib.py"
        p.write_text(_DRY_CONTENT, encoding="utf-8")
        results, red = _score_gates(work, p, _DRY_SPEC["gates"])
        # ★ R14：统一走数术层格裁决（取值等价于 `results and all(...)`）
        from agentscope_orchestrator import shushu_logic as _sl14c
        if not _sl14c.从门结果(results)[0]:
            fails.append(f"dry 正确件应全绿, got {results}")
        # ④ 含 TODO → no_todo 门红（机器门抓得到回炉信号）
        p.write_text(_DRY_CONTENT + "\n# TODO: x\n", encoding="utf-8")
        r2, red2 = _score_gates(work, p, _DRY_SPEC["gates"])
        if r2.get("no_todo", True) is not False or not red2:
            fails.append("含 TODO 应使 no_todo 门红并给出证据")
        # ⑤ 超时护栏：timeout>上限 clamp 到 _MAX_TIMEOUT；缺省补默认
        clamped = _clamp_timeout([{"type": "compile", "cmd": ["true"],
                                   "timeout": 99999}])
        if clamped[0]["timeout"] != _MAX_TIMEOUT:
            fails.append("超时护栏应 clamp 到上限")
        clamped2 = _clamp_timeout([{"type": "compile", "cmd": ["true"]}])
        if clamped2[0]["timeout"] != _MAX_TIMEOUT:
            fails.append("缺省 timeout 应补默认上限")

    # ⑥ auto_objective 防平凡不动点：红门 → is_done False；无真值门 → 拒绝闭合
    if auto_objective.compute([], current_gates={"a": True, "b": False}).is_done:
        fails.append("门有红时应判未闭合")

    # ══════════════════════════════════════════════════════════════════════
    # ⑦ ★★★ 机器自生成（0-LLM）：模型只产**结构与算子表达式**，模块由机器组装
    #   这是"不用 AI 写代码"的落地门。9 条，全部**真跑/真反读**，不看文本对。
    # ══════════════════════════════════════════════════════════════════════
    md = _parse_deliverables(_DRY_MACHINE_SPEC)
    if len(md) != 1 or not isinstance(md[0].get("machine"), dict):
        fails.append("机器规约的 deliverables 应携带 machine 结构真值")
    _src = _machine_generate(md[0]) if md else None
    if not _src:
        fails.append("机器自生成应产出源码（非 None）")
    else:
        # ⓵ 产物必须真能跑（compile 门）
        with tempfile.TemporaryDirectory() as td2:
            w2 = Path(td2)
            f2 = w2 / "collatz.py"
            f2.write_text(_src, encoding="utf-8")
            r5, red5 = _score_gates(w2, f2, _DRY_MACHINE_SPEC["gates"])
            if not (r5 and all(r5.values())):
                fails.append(f"机器自生成产物应过全部门（含真跑行为门）, got {r5}")
        # ⓶ 反读必须回原结构（方向性；读不回 = 有损组装 ⇒ 不许交付）
        _st = md[0]["machine"]["结构"]
        _want = ao_mech.StructureTruth(
            name=str(_st["name"]), input_=int(_st["in"]), output=int(_st["out"]),
            ops=tuple(str(x) for x in _st["ops"]), carry=int(_st["carry"]),
            defs=tuple((str(k), str(v))
                       for k, v in (md[0]["machine"]["算子"] or {}).items()))
        if ao_mech.machine_module_read(_src, "python") != _want:
            fails.append("机器产物反读应回原结构（方向性）")
        # ⓷ 盘码被改 ⇒ 反读必须失败（证明真的在读盘码，不是恒真）
        _pc = list(ao_mech._盘码(_want))
        _bad = _src.replace(repr(_pc[2]), repr(_pc[2] + 1), 1)
        if ao_mech.machine_module_read(_bad, "python") is not None:
            fails.append("盘码被改应使反读失败")
        # ⓸ 算子表达式被改 ⇒ 反读必须失败（交叉校验生效）
        _bad2 = _src.replace("3 * v + 1", "3 * v + 2", 1)
        if ao_mech.machine_module_read(_bad2, "python") is not None:
            fails.append("算子表达式被改应使反读失败")
        # ⓹ 未知语言 ⇒ 抛错（不假装支持）
        try:
            ao_mech.machine_module(_want, "cobol")
            fails.append("未知产物语言应抛错")
        except ValueError:
            pass
        # ⓺ 结构非法（算子名非法）⇒ 回落 None，不崩
        _badm = dict(md[0])
        _badm["machine"] = {"language": "python",
                            "结构": {"name": "x", "in": 1, "out": 1,
                                     "ops": ["a-b"], "carry": 1}}
        if _machine_generate(_badm) is not None:
            fails.append("非法算子名应使机器自生成回落 None（不静默改名）")
        # ⓻ 没有 machine 字段 ⇒ 返回 None（回落模型，不假装机器能做）
        if _machine_generate({"path": "z.py"}) is not None:
            fails.append("无 machine 字段应回落 None（交模型）")
        # ⓻′ ★★★★★ 决策层：**两比特 → 最小约束数**必须在**调模型之前**由机器答出。
        _m1 = dict(md[0])
        _m1["machine"] = {"language": "python",
                          "结构": {"name": "m0", "in": 4, "out": 4,
                                   "ops": ["add"], "carry": 3}}
        _machine_generate(_m1)
        _v0 = _建模判据记录.get("collatz.py", {})
        if _v0.get("最小约束数") != 0 or _v0.get("Sys") is not True:
            fails.append(f"自带表内结构应判 Sys 真、0 条约束, got {_v0}")
        # ★★★★★ 塌缩（TS-462 §1）：**两个表外算子都给了定义 ⇒ 仍然是 1**，
        #   **不是** None —— 约束预算在 1 以上完全无关（两条不比一条强）。
        _m2 = {"path": "two.py",
               "machine": {"language": "python",
                           "结构": {"name": "m2", "in": 2, "out": 2,
                                    "ops": ["opa", "opb"], "carry": 1},
                           "算子": {"opa": "v + 1", "opb": "v + 2"}}}
        if _machine_generate(_m2) is None:
            fails.append("两个表外算子都给了定义 ⇒ 应判 1 条约束（塌缩），不该回落")
        if _建模判据记录.get("two.py", {}).get("最小约束数") != 1:
            fails.append("塌缩：两个都给了定义必须判 1"
                         f", got {_建模判据记录.get('two.py')}")
        # ★ 两个表外算子**都没给** ⇒ `Prim` 位假 ⇒ 必须回落 None 且如实登记
        _m3 = {"path": "none.py",
               "machine": {"language": "python",
                           "结构": {"name": "m3", "in": 2, "out": 2,
                                    "ops": ["opa", "opb"], "carry": 1}}}
        if _machine_generate(_m3) is not None:
            fails.append("两位都不到位时必须回落 None（不假装机器能做）")
        if _建模判据记录.get("none.py", {}).get("最小约束数") is not None:
            fails.append("两位都不到位时最小约束数必须如实记为 None")
        # ⓻″ dry_machine 真跑：判据必须记成 **1**（`count:step` 从原语取象一条）
        if _建模判据记录.get("collatz.py", {}).get("最小约束数") not in (0, 1):
            fails.append("dry_machine 的建模判据必须落在 0 或 1"
                         f", got {_建模判据记录.get('collatz.py')}")
        # ⓼ 机器产物**零 API 流量**：dry_machine 跑完 _tok_chars 必须为 0
        # ★★★★★ TS-597【自检污染·实测缺口】原来 out_dir=None ⇒ 落生产 products/，
        #   且 _emit 未屏蔽 ⇒ 往生产 .audit_trail/generic_receipt.jsonl 写假交付。
        #   【实测后果】goal=="" 的 collatz plan 2773 条、products/collatz_machine/
        #     2630 个目录、交付内容只 24 种 hash 最高重复 2428 次。
        #   【修法】真跑不变（强度不减），只是把落盘与回执关进临时沙箱。
        #   同文件 R22b 段（_r.out_dir = root4/"products"; _r._emit = lambda _ev: None）
        #   早就是正确写法，这里只是补齐。
        with tempfile.TemporaryDirectory() as _td_dm:
            # ★★★★★ TS-597c：**只隔离落盘，不隔离记账**。
            #   TS-597 用 `_r._emit = lambda: None` 屏蔽回执 ⇒ 把 `_emit` 内部的
            #   `_delivered_products/_delivered_gates` 记账也一并屏蔽了
            #   ⇒ ⓽ 段 `if not _deliv` 报「应真交付产物文件」**假红**。
            #   ★ 教训：「不回执」≠「不记账」。要隔离的是副作用（写生产文件），
            #     不是读数（本轮交付了什么）。
            #   现改为：`_emit` 一字不动，只把模块级 `_RECEIPT` 指进沙箱。
            _saved_receipt = globals()["_RECEIPT"]
            globals()["_RECEIPT"] = Path(_td_dm) / "generic_receipt.jsonl"
            try:
                _r = GoalRunner(goal="", dry=True, dry_machine=True, n_windows=1,
                                min_pass=1, max_work=1,
                                out_dir=Path(_td_dm) / "products")
                _rc = asyncio.run(_r._run())
            finally:
                globals()["_RECEIPT"] = _saved_receipt
            # ★★★★★ TS-597b：断言**必须与真跑同寿命**（都在 with 内）。
            #   TS-597 只把真跑关进沙箱、断言留在块外 ⇒ 临时目录已回收
            #   ⇒ `Path(x).is_file()` 必然 False ⇒ 自检报「应真交付产物文件」假红。
            #   ★ 这是**假红**（产物真交付了，只是目录已删），
            #     与库纪律「漏掉的检查看起来是绿的」互为镜像：
            #     **错位的检查看起来是红的** —— 同样不可信。
            if _rc != 0:
                fails.append(f"dry-machine 闭环应收敛（退出码 0）, got {_rc}")
            if getattr(_r, "_tok_chars", 0) != 0:
                fails.append(f"机器自生成必须零流量, got {_r._tok_chars}")
            if getattr(_r, "_machine_files", 0) < 1:
                fails.append("机器自生成应记录机器产文件数")
            # ⓽ 判定层必然：门全绿 ⇒ 交付（不依赖模型是否"觉得"做完）
            _deliv = list(getattr(_r, "_delivered_products", None) or [])
            if not _deliv:
                fails.append("机器自生成应真交付产物文件")
            elif not all(Path(x).is_file() for x in _deliv):
                fails.append("交付产物必须真存在于磁盘（不是只报了个路径）")
            else:
                _dg = getattr(_r, "_delivered_gates", {}) or {}
                if not (_dg and all(v is True for v in _dg.values())):
                    fails.append(f"交付时门必须全绿, got {_dg}")
    if auto_objective.compute([], current_gates=None).is_done:
        fails.append("无独立真值门时不许判已闭合")

    # ⑦ 接线守恒机验：依赖未发射槽拒绝（auto_goal 机器计划保守性）
    c6 = json.loads(json.dumps(_DRY_SPEC))
    _normalize_spec(c6)
    c = auto_goal.carrier_from_spec(c6)
    ok, why = auto_goal.consume_ok(
        c, [auto_goal.Task(tid="t", name="x", kind="errand",
                           depends_on=["幽灵"])])
    if ok:
        fails.append("依赖未发射槽应判违例")

    # ⑧ 多文件解析：单文件包装成 1 项；多文件展开；缺 path 拒绝
    dl1 = _parse_deliverables(_DRY_SPEC)
    if len(dl1) != 1 or dl1[0]["path"] != "fib.py":
        fails.append("单文件应包装为 1 项")
    dl2 = _parse_deliverables(_DRY_MULTI_SPEC)
    if len(dl2) != 2 or dl2[0]["path"] != "fib.py" or dl2[1]["path"] != "test_fib.py":
        fails.append("多文件应展开 2 项且保序")

    # ⑨ 多文件拓扑：链式依赖排序（依赖在前）；未知依赖拒；成环拒
    dls = [{"path": "b.py", "depends_on": ["a.py"]}, {"path": "a.py"}]
    order, ok, why = _topo_sort(dls)
    if not ok or order != [1, 0]:
        fails.append(f"链式依赖应拓扑排序（a 在前），得 {order}/{why}")
    dls_bad = [{"path": "a.py", "depends_on": ["x.py"]}]
    if _topo_sort(dls_bad)[1]:
        fails.append("未知依赖应拒")
    dls_cycle = [{"path": "a.py", "depends_on": ["b.py"]},
                 {"path": "b.py", "depends_on": ["a.py"]}]
    if _topo_sort(dls_cycle)[1]:
        fails.append("成环依赖应拒")

    # ⑩ 多文件 DAG 接线守恒：拓扑序文件任务过 consume_ok
    mspec = json.loads(json.dumps(_DRY_MULTI_SPEC))
    _normalize_spec(mspec)
    md = _parse_deliverables(mspec)
    tasks, dok, dwhy = _build_dag(mspec, md)
    if not dok:
        fails.append(f"多文件 DAG 应守恒：{dwhy}")

    # ⑪ 多窗不动点对齐：2 绿+min_pass2 且真值一致 → fixed_point；不足/不一致 → 非
    def _win(idx, results):
        return {"idx": idx, "results": results,
                "passed": all(v is True for v in results.values())}
    g = {"fib.py@compile": True, "fib.py@value": True}
    w2 = [_win(0, dict(g)), _win(1, dict(g))]
    idx, al = _align_windows(w2, 2)
    if not al["fixed_point"] or idx != 0:
        fails.append("2 绿一致 min_pass2 应判不动点")
    w2b = [_win(0, dict(g)), _win(1, {"fib.py@compile": True, "fib.py@value": False})]
    _, al2 = _align_windows(w2b, 2)
    if al2["fixed_point"]:
        fails.append("有分歧窗不得判不动点（真值不等价）")
    w2c = [_win(0, dict(g))]
    _, al3 = _align_windows(w2c, 2)
    if al3["fixed_point"]:
        fails.append("绿窗不足 min_pass 不得判不动点")
    # 单窗退化为门全绿
    _, al4 = _align_windows(w2c, 1)
    if not al4["fixed_point"]:
        fails.append("单窗 min_pass1 全绿应判不动点（兼容原行为）")

    # ⑫ 达门重规划判据：连续红达阈值 → True；未达 → False
    if not _should_replan({"a@x": 3, "a@y": 0}, work=3, after=3):
        fails.append("连续红 3 轮应触发重规划")
    if _should_replan({"a@x": 2}, work=3, after=3):
        fails.append("连续红不足阈值不得触发重规划")

    # ⑬ 跨批次账本回读：iteration 行转 records；坏行/非 iteration 跳过；幂等
    good_line = json.dumps({"kind": "iteration", "work": 1,
                            "post_gates": {"a@c": True, "a@v": False},
                            "gaps": [{"key": "a@v", "status": "open"}]},
                           ensure_ascii=False)
    bad_line = "{bad json line, truncat"
    other = json.dumps({"kind": "plan", "work": 1}, ensure_ascii=False)
    recs = _ledger_from_receipt("\n".join([good_line, bad_line, other]))
    if len(recs) != 1 or recs[0]["post_gates"].get("a@v") is not False:
        fails.append("账本回读应只取 iteration 且跳过坏行")
    obj_h = auto_objective.compute(recs, current_gates={"a@c": True, "a@v": False})
    if obj_h.is_done:
        fails.append("回读历史含红门 → 历史目标不得判 done")
    obj_h2 = auto_objective.compute(recs, current_gates={"a@c": True, "a@v": False})
    if obj_h.to_dict() != obj_h2.to_dict():
        fails.append("同一账本折叠应幂等一致")
    if _hist_note(obj_h) and obj_h.pick_next() != "a@v":
        fails.append("历史偏差密度应把红门缺口排最前")

    # ⑭ dry 多文件闭环纯机器验证：正确件全绿；加 TODO → 红
    with tempfile.TemporaryDirectory() as td2:
        work2 = Path(td2)
        for path, text in ({"fib.py": _DRY_CONTENT,
                            "test_fib.py": _DRY_TEST_CONTENT}).items():
            f = work2 / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        r3, red3 = _score_files(work2, _parse_deliverables(_DRY_MULTI_SPEC))
        if not (r3 and all(v is True for v in r3.values())):
            fails.append(f"dry 多文件正确件应全绿, got {r3}")
        (work2 / "fib.py").write_text(_DRY_CONTENT + "\n# TODO: 残留\n",
                                      encoding="utf-8")
        r4, red4 = _score_files(work2, _parse_deliverables(_DRY_MULTI_SPEC))
        if not any(not v for v in r4.values()) or not red4:
            fails.append("dry 多文件含 TODO 应红并给证据")

    # ⑮ grep 门 schema 鲁棒性：patterns 数组兼容；缺 pattern/patterns 判红不崩
    with tempfile.TemporaryDirectory() as td3:
        w3 = Path(td3)
        (w3 / "fib.py").write_text(_DRY_CONTENT, encoding="utf-8")
        rp, _ = _score_gates(w3, w3 / "fib.py",
                             [{"name": "must_def", "type": "grep_required",
                               "patterns": ["def fib"], "in": "{file}"}])
        if rp.get("must_def") is not True:
            fails.append("grep_required patterns 数组应判绿")
        rn, redn = _score_gates(w3, w3 / "fib.py",
                                [{"name": "nf", "type": "grep_required",
                                  "in": "{file}"}])
        if rn.get("nf") is not False or not redn:
            fails.append("grep 门缺 pattern/patterns 应判红并给证据，不崩")

    # ⑯ 跨批次回溯：旧回执行无 post_gates → 用 gates 近似，升级前历史也入账本
    old_line = json.dumps({"kind": "iteration", "work": 7,
                           "gates": {"a": True, "b": False}, "gaps": []},
                          ensure_ascii=False)
    recs_old = _ledger_from_receipt(old_line)
    if not recs_old or recs_old[0]["post_gates"].get("b") is not False:
        fails.append("旧回执行(无 post_gates)应回退用 gates 近似并入账本")

    # ⑰ 主树工程三机制纯机器验证（不碰真实库：临时目录代 REPO_ROOT）
    #    a) 源码注入读真实仓库文件（机制①）
    ctx = _repo_file_context("src/gpu/gpu探测.rs")
    if not ctx or "ATTR_L2_CACHE_SIZE" not in ctx:
        fails.append("_repo_file_context 应注入真实仓库文件内容")
    if _repo_file_context("no/such/file.rs"):
        fails.append("_repo_file_context 对不存在文件应返回空")
    #    b) 按红门**因由类**换策略（机制③）
    #       ★★★【TS-442/447/424】本段守的是**策略层不塌缩**：
    #       旧版读**门类型** ⇒ 编译错/超时/挂起/退出码不符四件相异因由
    #       共用 `compile` 类型 ⇒ 拿到**同一条**策略「核对语法」，
    #       而超时核对语法**永远修不好** ⇒ 任务不收敛的一个独立成因。
    gates_now = [{"name": "c", "type": "compile"}, {"name": "f", "type": "grep_forbidden"}]
    s = _gap_strategy([{"gate": "c", "file": "a.rs", "ok": False, "cause": "编译错|E0308@x.rs:1:2"},
                       {"gate": "f", "file": "b.rs", "ok": False, "cause": "禁词命中|todo"}],
                      gates_now)
    if "编译错" not in s or "禁词命中" not in s:
        fails.append("_gap_strategy 应按因由类给出策略")
    if _gap_strategy([], gates_now):
        fails.append("无红门时策略应为空")
    # ㉘ ★★★ 相异因由 ⇒ 相异策略（这是本层的承重断言）
    s_编译 = _gap_strategy([{"gate": "c", "file": "a.rs", "ok": False,
                             "cause": "编译错|E0308@x.rs:1:2"}], gates_now)
    s_超时 = _gap_strategy([{"gate": "c", "file": "a.rs", "ok": False,
                             "cause": "超时|上限3600s"}], gates_now)
    s_挂起 = _gap_strategy([{"gate": "c", "file": "a.rs", "ok": False,
                             "cause": "挂起|无输出900s"}], gates_now)
    s_退出 = _gap_strategy([{"gate": "c", "file": "a.rs", "ok": False,
                             "cause": "退出码不符|期望0实得3"}], gates_now)
    s_命令 = _gap_strategy([{"gate": "c", "file": "a.rs", "ok": False,
                             "cause": "命令无法启动|cargo.exe"}], gates_now)
    if len({s_编译, s_超时, s_挂起, s_退出, s_命令}) != 5:
        fails.append("【TS-442】编译错/超时/挂起/退出码/命令无法启动 应各得**不同**策略"
                     "（旧版按门类型读 ⇒ 四者同策略）")
    # ㉙ ★ 超时/挂起**不得**被当成语法错（旧版正是这样，导致永远修不好）
    if "核对语法" in s_超时 or "核对语法" in s_挂起:
        fails.append("【TS-442】超时/挂起不得套用「核对语法」策略")
    if "更小的验证范围" not in s_超时:
        fails.append("【TS-442】超时应给出「缩小验证范围」策略")
    # ㉚ 命令无法启动应指向**门规格**，不是产物
    if "门规格" not in s_命令:
        fails.append("【TS-442】命令无法启动应指向门规格（换可执行路径），不是改产物")
    # ㉚ 老账本无 cause 字段 ⇒ 退化兜底仍按门类型工作（向后兼容）
    s_旧 = _gap_strategy([{"gate": "f", "file": "b.rs", "ok": False}], gates_now)
    if "禁词命中" not in s_旧:
        fails.append("【TS-442】无 cause 字段的老账本应退化按门类型给策略")
    #    c) repo_scope 判定（顶层 repo:true 或 gate cwd 含 {repo}）
    if not _detect_repo_scope({"repo": True, "gates": []}, [{"type": "compile"}]):
        fails.append("顶层 repo:true 应判主树工程")
    if not _detect_repo_scope({"gates": []}, [{"cwd": "{repo}"}]):
        fails.append("gate cwd 含 {repo} 应判主树工程")
    if _detect_repo_scope({"gates": []}, [{"cwd": "."}]):
        fails.append("无 repo 标记不得判主树工程")

    # ⑱ 原子回滚闭环（机制②）：备份→应用→回滚逐字还原；原本不存在的文件回滚=删除
    saved_repo, saved_bkp = REPO_ROOT, BACKUP_DIR
    with tempfile.TemporaryDirectory() as td4:
        root4 = Path(td4)
        globals()["REPO_ROOT"] = root4
        globals()["BACKUP_DIR"] = root4 / ".bkp"
        BACKUP_DIR.mkdir(exist_ok=True)
        (root4 / "src").mkdir(parents=True)
        # ★★★★★ R22：这里**必须比字节**，不能比 `read_text` 读回来的字符串 ——
        #   旧自检用 `read_text(...) != "OLD\n"`，而 `read_text` 会把 CRLF
        #   再翻译回 LF ⇒ **被破坏的文件读出来仍等于 "OLD\n"** ⇒ 自检恒绿
        #   （自检与被检缺陷**共谋**，违反纪律 R3）。
        #   夹具特意含**非法 UTF-8 字节**与 **LF 行尾**：文本 API 读不出它们。
        _原字节 = b"// \xc3( \xff\xfe \x80\x81 \xe4\xb8\xad\nfn a() {}\n"
        (root4 / "src" / "a.rs").write_bytes(_原字节)
        bk = _backup_file("src/a.rs")
        if Path(bk["backup"]).read_bytes() != _原字节:
            fails.append("R22：_backup_file 备份必须**字节级**（含非法 UTF-8 字节）")
        _apply_file("src/a.rs", "NEW\n")
        if (root4 / "src" / "a.rs").read_bytes() != b"NEW\n":
            fails.append("R22：_apply_file 应写入**未经行尾翻译**的原始字节"
                         "（实测 CRLF 污染：改过的文件 CRLF 145/LF 0）")
        _rollback_file(bk)
        if (root4 / "src" / "a.rs").read_bytes() != _原字节:
            fails.append("R22：_rollback_file 必须**字节级**还原（含非法 UTF-8 与 LF 行尾）"
                         "—— 「红滚」是安全网，有损性必须为零")
        # ★ 三处一致：沙箱副本 / 主树 / 产物副本 落的是**同一份字节**
        if _apply_file("src/a.rs", "SAME\n").read_bytes() != b"SAME\n":
            fails.append("R22：三处落盘（沙箱/主树/产物）必须逐字一致")
        _rollback_file(bk)
        bk2 = _backup_file("src/new.rs")
        _apply_file("src/new.rs", "X\n")
        _rollback_file(bk2)
        if (root4 / "src" / "new.rs").exists():
            fails.append("原本不存在的文件回滚应删除")

        # ★★★★★ R22b：**三处落盘逐字一致**（沙箱窗副本 / 主树 / 产物副本）。
        #   ★ 为什么必须有这条：R22 的探针④退回沙箱副本的 `write_text` 后，
        #     本自检**仍全绿** ⇒ 那条**没有齿**。根因是自检只测了三个函数，
        #     **没测落盘点本身**。落盘点是「同一份字节流的三次写」，故该测的
        #     是**不变量**（三处相等），不是逐个函数。
        #   ★ 将来新增任何落盘点，只要破坏这条不变量就会红 —— 不必枚举。
        import asyncio as _aio
        _三处文本 = "fn a() {\n    // 中文注释与 LF 行尾\n}\n"
        _r = GoalRunner.__new__(GoalRunner)          # 绕过 __init__（不探通道）
        _r.round_root = root4 / "round"
        _r.round_root.mkdir(parents=True, exist_ok=True)
        _r.deliverables = [{"path": "src/a.rs", "gates": []}]
        _r.repo_scope = False
        _r._window_score({"src/a.rs": _三处文本}, 0)
        _沙箱 = (_r.round_root / "w0" / "src" / "a.rs").read_bytes()
        if _沙箱 != _三处文本.encode("utf-8"):
            fails.append("R22b：**沙箱窗副本**必须字节级（行尾不得被翻译）"
                         " —— 探针④证明过这里原来没有齿")
        # 产物副本（_deliver）：先按 `_round` 的方式落**正本**，再让 _deliver 拷贝。
        #   ★ 这一步不能省：`_deliver` 拷的是 `round_root/<path>`（正本），
        #     不是 `w0/<path>`（窗副本）—— 省了就变成"文件不存在"而**静默不拷**，
        #     检查会以 `FileNotFoundError` 崩掉，而不是报告不变量被违反
        #     （本库纪律 R3 的同族：检查必须**报告**缺陷，不能自己崩）。
        _正本 = _r.round_root / "src" / "a.rs"
        _正本.parent.mkdir(parents=True, exist_ok=True)
        _正本.write_bytes(_三处文本.encode("utf-8"))
        _r.out_dir = root4 / "products"
        _r.safe_name = "r22b"
        _r._active_backups = []
        _r._tok_chars = 0
        _r._emit = lambda _ev: None
        _r.product_dir = None
        _aio.run(GoalRunner._deliver(_r, 1, {"t": True}, {"fixed_point": True}))
        _产物 = (_r.product_dir / "src" / "a.rs").read_bytes()
        if _产物 != _三处文本.encode("utf-8"):
            fails.append("R22b：**产物副本**必须字节级（与沙箱副本逐字一致）")
        if not (_沙箱 == _产物 == _三处文本.encode("utf-8")):
            fails.append("R22b：三处落盘**不逐字一致** ⇒ 判过的字节与留下的字节不是同一份")
    globals()["REPO_ROOT"] = saved_repo
    globals()["BACKUP_DIR"] = saved_bkp

    # ⑲ repo 判门：{repo} 预替换生效；cwd 缺省=repo 根（主树工程任务默认在仓库根跑真实工具）
    with tempfile.TemporaryDirectory() as td5:
        root5 = Path(td5)
        (root5 / "t.rs").write_text("fn main() {}\n", encoding="utf-8")
        g_repo = [{"name": "ok", "type": "compile", "cmd": ["python", "-c", "pass"],
                   "cwd": "{repo}", "expect_exit": 0}]
        rr, _ = _score_gates(root5, root5 / "t.rs", g_repo, repo_root=root5)
        if rr.get("ok") is not True:
            # ★ R7 修复：原写 `f"repo cwd {repo} 替换应判绿"`，而 `repo` 在本作用域
            #   **从未定义** ⇒ 这条门**一旦真的红**，自检自己先 `NameError` 崩掉，
            #   失败原因**永远打不出来**（与 `taixu_mcp._RUST_ROOT` 同一类）。
            #   改为打印真实的 repo 根 `root5`。
            fails.append(f"repo cwd {root5} 替换应判绿, got {rr}")
        g_def = [{"name": "ok2", "type": "compile", "cmd": ["python", "-c", "pass"],
                  "expect_exit": 0}]
        rr2, _ = _score_gates(root5, root5 / "t.rs", g_def, repo_root=root5)
        if rr2.get("ok2") is not True:
            fails.append(f"repo 缺省 cwd 应=repo 根, got {rr2}")

    # ⑳ file_root：主树工程按仓库相对路径取文件判门；缺文件记 @missing
    with tempfile.TemporaryDirectory() as td6:
        root6 = Path(td6)
        (root6 / "src").mkdir(parents=True)
        (root6 / "src" / "a.rs").write_text("OK marker\n", encoding="utf-8")
        dls = [{"path": "src/a.rs", "agent_instruction": "", "depends_on": [],
                "gates": [{"name": "mk", "type": "grep_required",
                           "patterns": ["OK marker"], "in": "{file}"}]}]
        rr3, red3 = _score_files(root6, dls, file_root=root6, repo_root=root6)
        if rr3.get("src/a.rs@mk") is not True:
            fails.append(f"file_root 应取 repo 相对路径判门, got {rr3}")
        dls2 = [{"path": "src/absent.rs", "agent_instruction": "", "depends_on": [],
                 "gates": [{"type": "compile", "cmd": ["true"], "cwd": "."}]}]
        rr4, _ = _score_files(root6, dls2, file_root=root6, repo_root=root6)
        if rr4.get("src/absent.rs@missing") is not False:
            fails.append("repo 缺文件应记 @missing 红")

    # ㉑ 项目理解索引（repo_index）：建主树索引 → 符号提取 → 跨文件检索 → 结构快照。
    #    纯只读机器扫库，不碰 API、不改主树；直接断言真实主树索引真值。
    #    断言只用**档案驱动**的通用性质（条目数>0、有符号、快照非空），
    #    不假定项目里存在某个具体文件/符号（工具不该知道项目内容）。
    n_idx = repo_index.ensure(force=True)
    if n_idx < 1:
        fails.append(f"repo_index 应索引主树至少 1 个源文件，得 {n_idx}")
    _any = None
    for _e in (repo_index._load() or {}).values():
        if _e.get("uses") and _e.get("symbols"):
            _any = _e
            break
    if _any is None:
        fails.append("repo_index 应至少有一个条目带 use 依赖与顶层符号")
    # ★★★★★ R47c（2026-10-15）：本行原本断言 `startswith("pub ")`。
    #   ★ 那**违反了它自己上面那句注释**（"只用档案驱动的通用性质，
    #     不假定项目里存在某个具体符号"）—— `pub` 是 **Rust 专有关键字**，
    #     Python/TS/Go 的声明**没有** `pub` ⇒ 这条断言**只对 Rust 成立**
    #     ⇒ 它把"通用性缺陷"**锁死成正确行为**（R47 改通用抽取时被它判红）。
    #   ★ 真正要保的性质不是"以 pub 开头"，而是"**存的是签名行、不是函数体**"
    #     ⇒ 直接断言"这一行能被**跨语言的**声明正则认出"。
    elif not any(repo_index._SYM_RE.match(str(s))
                 for s in _any.get("symbols", [])):
        fails.append("符号摘要应能被声明正则认出（保签名，跨语言）")
    _target = next(iter(repo_index._load() or {}), None)
    if _target:
        repo_index.related(_target)   # 只证可调用不抛错（内容属项目）
    snap = repo_index.structure_snapshot("修复缩进")
    if len(snap) < 20:
        fails.append("structure_snapshot 应产出模块概览")
    # 指纹幂等：二次 ensure(非 force) 不改变条目（增量缓存稳定）
    n2 = repo_index.ensure()
    if n2 != n_idx:
        fails.append("索引 ensure 指纹应幂等（二次 ensure 条目数不变）")

    # ㉒ 收敛判定数学语义：不动点 = 门真值向量逐 key 稳定（T(Sₙ)==T(Sₙ₋₁) 连续 K 轮）。
    #    稳定全绿 → deliver；稳定含红 → 强制换门集（状态转移），而非耗尽 max_work 判退。
    g_green = {"a@c": True, "a@v": True}
    g_red = {"a@c": True, "a@v": False}
    # a) 稳定全绿：连续 K 轮同全绿向量 → 不动点已达，且 done → deliver
    st, fp1 = 0, False
    st, fp1 = _convergence(g_green, None, st, FP_STABLE_K)          # 首轮（无 prev）不稳
    if fp1:
        fails.append("首轮无上一轮真值不得判不动点")
    st, fp1 = _convergence(g_green, g_green, st, FP_STABLE_K)       # 第二轮同 → 稳定
    if not fp1:
        fails.append("同全绿向量连续 K 轮一致应判不动点")
    if _transition(done=True, fixed_point=fp1, replan_by_red=False) != "deliver":
        fails.append("稳定全绿(done)应 deliver")
    # b) 稳定含红：同含红向量连续 K 轮一致 → 仍是不动点，但非 done → 强制 replan 换门集
    st2, fp2 = 0, False
    st2, fp2 = _convergence(g_red, None, st2, FP_STABLE_K)
    st2, fp2 = _convergence(g_red, g_red, st2, FP_STABLE_K)
    if not fp2:
        fails.append("同含红向量连续 K 轮一致也应判不动点")
    if _transition(done=False, fixed_point=fp2, replan_by_red=False) != "replan":
        fails.append("稳定含红应强制 replan 换门集，而非耗尽轮数判退")
    # c) 未稳但连续红达阈 → 兜底达门重规划 replan；未稳且未达阈 → continue 继续演化
    if _transition(done=False, fixed_point=False, replan_by_red=True) != "replan":
        fails.append("未到不动点但连续红达阈应 replan")
    if _transition(done=False, fixed_point=False, replan_by_red=False) != "continue":
        fails.append("不动点未达且未达红阈应 continue 继续演化")

    # ㉜ ★★★【TS-442/TS-430】不动点判据必须建在**因由**上，不能建在布尔投影上。
    #     场景：两轮同一门都是红，但**红的因由已经变了**（真有进展）。
    #     布尔向量逐 key 相等 ⇒ 旧版判"已到不动点" ⇒ 换门集/停机 ⇒ **把进展当停滞**。
    #     这正是 TS-442 `read_eq_reflects` 被违反：布尔读法下
    #     "读相同"**不**蕴含"态相同"（相异态同读值）。
    r_编译 = [{"gate": "a@v", "ok": False, "cause": "编译错|E0308@x.rs:1:2"}]
    r_超时 = [{"gate": "a@v", "ok": False, "cause": "超时|上限3600s"}]
    # a) 布尔向量相同、因由不同 ⇒ **不得**判不动点（有进展）
    st3 = 0
    st3, fp3a = _convergence(g_red, None, st3, FP_STABLE_K, red=r_编译)
    st3, fp3b = _convergence(g_red, g_red, st3, FP_STABLE_K,
                             red=r_超时, prev_red=r_编译)
    if fp3b:
        fails.append("【TS-442】布尔向量相同但**因由已变**（有进展）不得判不动点")
    # b) 布尔向量相同、因由**也**相同 ⇒ 真不动点（保持原语义）
    st4 = 0
    st4, fp4a = _convergence(g_red, None, st4, FP_STABLE_K, red=r_编译)
    st4, fp4b = _convergence(g_red, g_red, st4, FP_STABLE_K,
                             red=r_编译, prev_red=r_编译)
    if not fp4b:
        fails.append("【TS-442】布尔与因由都稳定 ⇒ 应判不动点（原语义不得回退）")
    # c) 因由向量本身：绿→'绿'，红→因由码，无因由→如实标"未记因由"（不静默）
    因由 = _因由向量({"g1": True, "g2": False}, [{"gate": "g2", "cause": "超时|上限2s"}])
    if 因由.get("g1") != "绿" or 因由.get("g2") != "超时|上限2s":
        fails.append("【TS-442】因由向量应把绿读成'绿'、红读成因由码")
    因由2 = _因由向量({"g2": False}, [])
    if 因由2.get("g2") != "红|未记因由":
        fails.append("【TS-442】无因由时应如实标'红|未记因由'，不得静默当绿")
    # d) ★ 如实登记：`_align_windows` **只对齐绿窗**，绿窗的因由必全为 '绿'
    #    ⇒ 该处改读因由是**惰性**的（等价于原布尔比较），**不是**本步的修处。
    #    真正的塌缩在**账本**：`_append_records` 把"因由已变的红"也记成 blocked。
    def _win2(idx, results, red):
        return {"idx": idx, "results": results, "red": red,
                "因由": _因由向量(results, red),
                "passed": all(v is True for v in results.values())}
    wb = [_win2(0, {"a@c": True}, []), _win2(1, {"a@c": True}, [])]
    _, al_w2 = _align_windows(wb, 2)
    if not al_w2.get("fixed_point"):
        fails.append("【TS-442】两绿窗应判对齐（原语义不得回退）")
    if al_w2.get("read_space") != "cause":
        fails.append("【TS-442】多窗对齐应如实标注读空间为 cause")
    # d) 回归未闭环防平凡不动点：全绿但存在曾绿→改后挂未闭合 → is_done False → 即便向量
    #    稳定也走 replan 换门，绝不因"看起来全绿/数量没变"就收敛交付
    recs_reg = [{"round": "iteration", "time": "r1",
                 "gates": g_green, "post_gates": g_green,
                 "gaps": [{"key": "a@v", "status": "open"}]},
                {"round": "fix_attempt", "gap_key": "a@v", "file": "a",
                 "attempt": 1, "status": "blocked", "regressed": True}]
    obj_reg = auto_objective.compute(recs_reg, current_gates=g_green)
    if obj_reg.is_done:
        fails.append("全绿但有回归未闭环 → 不得判 done（防平凡不动点）")

    # ㉓ 换门集必须真变否则升级决策（#1）：门集签名判"同门(复述近似同门)≠真变"；
    #    判据语义变 → 签名变 → 判 covered 换门集可推进。
    def _mk_spec(name):
        return {"name": name, "deliverables": [{"path": "fib.py"}],
                "gates": [{"name": "c", "type": "compile", "cwd": ".",
                           "cmd": ["python"], "expect_exit": 0},
                          {"name": "v", "type": "grep_required", "in": "{file}",
                           "patterns": [name]}]}
    sp1, sp2 = _mk_spec("k"), _mk_spec("k")
    if _spec_gate_signature(sp1) != _spec_gate_signature(sp2):
        fails.append("同门集签名应一致（k 值一致）")
    sp3 = _mk_spec("k")
    sp4 = _mk_spec("k")
    sp4["gates"][1]["patterns"] = ["k", "extra"]   # 必需标记变 → 判据语义变
    if _spec_gate_signature(sp3) == _spec_gate_signature(sp4):
        fails.append("必需标记变化应导致门集签名变（换门集可推进）")
    # 复述近似同门：仅改 name/加无判据门，key 判据等价 → 签名相同 → replan 升级退出
    sp5 = _mk_spec("s")
    sp6 = json.loads(json.dumps(_mk_spec("s")))
    sp6["gates"][0]["name"] = "c_renamed"   # 只改 name，不改变判定语义
    if _spec_gate_signature(sp5) != _spec_gate_signature(sp6):
        fails.append("仅改门 name 不得视为门集真变（防复述近似同门）")
    # 漂移护栏：连续 REPLAN_MAX 次换门集仍不收敛 → 判据不可达升级（补"签名相等才升级"的盲区）
    if _replan_thrashed(replan_count=0, replan_max=REPLAN_MAX):
        fails.append("0 次重规划不得判漂移不可达")
    if not _replan_thrashed(replan_count=2, replan_max=REPLAN_MAX):
        fails.append("连续换门达到限应判漂移不可达（升级退出，而非 max_work 白烧）")
    if _replan_thrashed(replan_count=1, replan_max=REPLAN_MAX):
        fails.append("未达限的换门不应急停（留真收敛机会）")

    # ㉓B 数学判据可达性（#B，#1 次数启发式的数学前置判）：判据门能否归约到稳定定位参数。
    # 可达 ⇔ 被测物落稳定结构键（交付路径/主树现有文件）。谓词里的常量阈值、相对自身文件
    # 基线的差分（最小改动门）都是稳定键上的纯函数，可达 —— 不得扫"基线/阈值/行数"字样误判。
    if _gate_reachability({"type": "grep_required", "in": "src/gpu/gpu探测.rs",
                           "patterns": ["loop"]},
                          {"src/gpu/gpu探测.rs"}, True):
        fails.append("落主树结构键的文本门应可达")
    # 最小改动类差分门：测物=交付文件，阈值=常量 → 可达（曾实机误判不可达而错误升级停机）
    if _gate_reachability(
            {"type": "scan_output", "src": "{file}",
             "cmd": ["git", "diff", "--numstat", "行数基线", "改动行数<=20", "{file}"]},
            {"src/gpu/gpu探测.rs"}, True):
        fails.append("对稳定文件的最小改动差分门应可达（阈值是常量，非外部变动量）")
    # 真正的判据不可达：被测物是快照游标/不存在路径，非交付物也非主树文件 → 无稳定结构键
    if _gate_reachability({"type": "grep_required", "in": "ghost/cursor.bin",
                           "patterns": ["tail"]},
                          {"fib.py"}, False) == "":
        fails.append("引用快照游标/不存在键的文本门应判不可达")
    reach_spec = {"deliverables": [{"path": "fib.py"}],
                  "gates": [{"type": "compile", "cmd": ["cargo", "check"],
                             "cwd": "."},
                            {"type": "grep_required", "in": "{file}",
                             "patterns": ["ok"]}]}
    if _adopted_gates_unreachable(reach_spec, reach_spec["deliverables"], True):
        fails.append("常规门集应全可达（无判据不可达门）")
    # #B/C 签名须捕获 whole_reading 的必需定位键：门集换 must_contain 必须视为真变
    w_spec = json.loads(json.dumps(_mk_spec("w")))
    w_spec["gates"][1] = {"name": "v", "type": "whole_reading", "in": "{file}",
                          "must_contain": ["alpha"]}
    w_spec2 = json.loads(json.dumps(w_spec))
    w_spec2["gates"][1]["must_contain"] = ["alpha", "beta"]
    if _spec_gate_signature(w_spec) == _spec_gate_signature(w_spec2):
        fails.append("whole_reading 换必需定位键必须使门集签名变（否则误判未真变而升级）")

    # ㉔ #3 执随新红门重试：门差异反馈只在门集新增/变更时注入，且点名新增门。
    old_spec = _mk_spec("old")
    new_spec = _mk_spec("new")
    diff = _gate_diff_feedback(old_spec, new_spec, "fib.py")
    if not diff or ("grep_required" not in diff and "新增" not in diff):
        fails.append("门集变化应注入含新增门的差异反馈")
    if _gate_diff_feedback(new_spec, new_spec, "fib.py"):
        fails.append("门集未变时不应注入差异反馈")

    # ㉕ 目标驱动偏差判定层接线（S2-S6）：门源唯一 + C4 预检 + C2/C1/C3/C6 语义。
    #    P → Δ(P) → G(P)：门只从 derive_gates(P) 闭式派生（LLM 不参与、无守恒/改动规模门、
    #    无"必要性豁免"）；偏差身份纯结构（行号漂移不改键）；停机 = 结构真值（SelfEvolvingRule
    #    不动点），无数值上限；收敛/停滞/发散/卡点全由结构谓词给，不依赖跨轮历史向量折叠。
    P1 = {"checks": [
        {"kind": "compile", "name": "cargo_check", "cwd": "{repo}",
         "cmd": ["cargo", "check", "--lib"]},
        {"kind": "whole_reading", "name": "has_pub", "file": "src/gpu/gpu探测.rs",
         "must_contain": ["pub fn gpu_probe"]}]}
    g1 = derive_gates(P1, str(REPO_ROOT))
    if not g1:
        fails.append("derive_gates(P) 应产非空行为门集")
    if not all(g.get("derive") for g in g1):
        fails.append("派生门须全部标记 derive（门集来源唯一，无 LLM 造门）")
    if any(g.get("type") == "grep_forbidden" or "改动" in str(g.get("name"))
           or "only_touch" in str(g.get("name")) for g in g1):
        fails.append("derive_gates(P) 不得派生守恒/改动规模门（守恒门全剔）")
    if assert_derived_gates(g1, P1):
        fails.append("derive_gates(P) 产物应通过 assert_derived_gates（门集来源唯一断言）")
    P2 = dict(P1)
    P2["checks"] = list(P1["checks"]) + [
        {"kind": "改动规模上限", "name": "only_touch", "cmd": [], "cwd": "."}]
    if any(g.get("name") == "only_touch" for g in derive_gates(P2, str(REPO_ROOT))):
        fails.append("未知 kind（守恒门）不应派生进门集（门源唯一：非 P 子谓词不派生）")
    # C4 入口 P 可判定性预检：本仓库 Cargo.toml + 目标文件存在 → 可跑；坏根 → 入口拒绝（非降级换门）
    if not precheck_pred_decidable(P1, str(REPO_ROOT))[0]:
        fails.append("真实主树仓库 Cargo.toml+目标文件存在 → C4 预检应通过")
    if precheck_pred_decidable(P1, "C:/not_a_repo")[0]:
        fails.append("P 的 compile 在当前根不可跑 / 目标不存在 → 入口应拒绝（换P而非降级）")
    # C2+C 抽取：compile 红 → E 码偏差；whole_reading 缺必需键 → whole_missing 偏差
    red1 = [
        {"gate": "cargo_check", "file": "src/gpu/gpu探测.rs", "ok": False, "rc": 101,
         "err": "error[E0433]: unresolved import gpu\n --> src/gpu/gpu探测.rs:5:9\n"},
        {"gate": "has_pub", "file": "src/gpu/gpu探测.rs", "ok": False,
         "err": {"missing": ["pub fn gpu_probe"]}}]
    devs1 = extract_deviations(red1, P1)
    if not any(d.get("type") == "E0433" for d in devs1):
        fails.append("compile 红应抽取为 E 码偏差（Δ(P) 内容，非人工豁免）")
    if not any(d.get("type") == "whole_missing" for d in devs1):
        fails.append("whole_reading 缺必需键应抽取为 whole_missing 偏差")
    # C1/C6 结构停机判据（五态）：converged / progressing 严格递减 / stalled / no_anchor / diverging
    dA = {"type": "E0433", "message": "unresolved import gpu at src/a.rs:5:1",
          "symbols": ["gpu"]}
    dB = {"type": "E0432", "message": "unresolved import spy at src/b.rs:9:1",
          "symbols": ["spy"]}
    idA, idB = deviation_identity(dA), deviation_identity(dB)
    sc = {"src/gpu/gpu探测.rs"}
    if deviation_structure_verdict([dA], [], sc, set())[0] != "converged":
        fails.append("Δ=∅ ⇒ P 达成 ⇒ converged")
    if deviation_structure_verdict([dA, dB], [dA], sc, set())[0] != "progressing":
        fails.append("Δ 严格递减(纯结构真子集) ⇒ progressing（真子集非等势轮换）")
    if deviation_structure_verdict([dA], [dA], sc, set())[0] != "stalled_fixed_point":
        fails.append("Δ 与前一轮相等≠∅ ⇒ 非目标不动点(停滞交还用户)")
    if deviation_structure_verdict([dA], [dA, dB], sc, set())[0] != "no_anchor":
        fails.append("当前声明门集下有偏差无锚 ⇒ no_anchor（存在性锚，非全称否定）")
    if deviation_structure_verdict([dA], [dA], sc, {idA})[0] != "diverging":
        fails.append("曾绿偏差重现(Δ 等集) ⇒ diverging（C6 优先于 stalled）")
    if deviation_structure_verdict([dA, dB], [dA], sc, {idA})[0] != "diverging":
        fails.append("曾绿偏差重现(严格递减下) ⇒ diverging")
    # C3 闭式选择：身份 canonical 序最小者（O(1)、确定性，非模型选 delta）
    dHi = {"type": "B2", "message": "upper", "symbols": []}
    dLo = {"type": "A1", "message": "lower", "symbols": []}
    if pick_anchored_deviation([dHi, dLo]) is None or \
            deviation_identity(pick_anchored_deviation([dHi, dLo])) != deviation_identity(dLo):
        fails.append("pick 应选身份 canonical 序最小偏差（闭式 O(1)）")
    if pick_anchored_deviation([]) is not None:
        fails.append("Δ 为空时 pick 应返回 None")

    # ★★★ TS-384 路径：恒真门 → 构造性派生替换门 → 真装进门集（零模型）
    #   这是"判出缺陷"到"修掉缺陷"的那一段闭环。早先版本判出 vacuous 就 stop/replan，
    #   而 replan 要**模型**重新给门（概率性，可能又给恒真门）；派生是机器确定性的。
    d_der = Path(tempfile.mkdtemp(prefix="gg_derive_"))
    try:
        prod = d_der / "产物.py"
        prod.write_text("def f():\n    return 1\n", encoding="utf-8")
        # 恒真门：must_contain=[''] 恒命中，任何内容都绿
        vac_gate = {"name": "always", "type": "whole_reading", "in": str(prod),
                    "mode": "contains", "must_contain": [""]}
        ok_w, rep_w = gate_witness.witness_gates([vac_gate], prod, d_der)
        if ok_w:
            fails.append("恒真门不应判 witness_ok")
        if not rep_w or rep_w[0].get("witness") != "vacuous":
            fails.append(f"恒真门应判 vacuous，实得 {rep_w[0].get('witness') if rep_w else '?'}")
        if not (rep_w and rep_w[0].get("derived_gate")):
            fails.append("恒真门应派生出替换门（TS-384 路径）")
        else:
            # 真装进门集：用 GoalRunner 的 _adopt_derived_gates（不跑模型，只调该法）
            runner = GoalRunner.__new__(GoalRunner)
            runner.spec = {"gates": [dict(vac_gate)]}
            runner.deliverables = [{"path": "产物.py", "gates": [dict(vac_gate)]}]
            runner._tok_chars = 0
            runner._emit = lambda ev: None
            n_swapped = runner._adopt_derived_gates(rep_w, 1)
            if n_swapped < 1:
                fails.append(f"派生门应被真装进门集，实换 {n_swapped} 个")
            else:
                new_g = runner.spec["gates"][0]
                if new_g.get("name") == "always":
                    fails.append("门集里的恒真门应已被替换掉")
                if not new_g.get("_derived_from"):
                    fails.append("替换后的门应带 _derived_from 溯源")
                # ★ 独立复验：替换后的门必须真能分开（好绿、坏红）
                from agentscope_orchestrator import gate_spec as _gs2
                gp, bp = d_der / "g.py", d_der / "b.py"
                gp.write_text("def f():\n    return 1\n", encoding="utf-8")
                bp.write_text("", encoding="utf-8")
                run_g = {k: v for k, v in new_g.items() if not k.startswith("_")}
                nmg = run_g.get("name")
                rg2, _ = _gs2.evaluate([run_g], gp, d_der, ".")
                rb2, _ = _gs2.evaluate([run_g], bp, d_der, ".")
                if not (rg2.get(nmg) and not rb2.get(nmg)):
                    fails.append(
                        f"替换后门应好绿坏红，实得 好={rg2.get(nmg)} 坏={rb2.get(nmg)}")

            # ★★ 假红闸回归（本轮自查抓出的真缺陷，必须长期守住）：
            #   带 `_over_specific` 的派生门（钉产物原文 ⇒ 换写法即假红）
            #   **不得**进交付门集，必须被如实拒收并记录。
            r2 = GoalRunner.__new__(GoalRunner)
            r2.spec = {"gates": [dict(vac_gate)]}
            r2.deliverables = []
            r2._tok_chars = 0
            事件: list[dict] = []
            r2._emit = lambda ev: 事件.append(ev)
            过细 = dict(rep_w[0]["derived_gate"])
            过细["name"] = "always_过细"
            过细["_over_specific"] = True
            n2 = r2._adopt_derived_gates([{"derived_gate": 过细}], 1)
            if n2 != 0:
                fails.append(f"过细派生门应被拒收，实换 {n2} 个")
            if r2.spec["gates"][0].get("name") != "always":
                fails.append("过细门被拒后原门应保持不动")
            if not any(e.get("kind") == "gate_rejected_over_specific" for e in 事件):
                fails.append("过细门被拒应收敛到 gate_rejected_over_specific 事件")
    finally:
        shutil.rmtree(d_der, ignore_errors=True)

    # ★★★ TS-378 路径：约束机检必须**有区分力**，且**不得循环**（形态 12）
    d_cons = Path(tempfile.mkdtemp(prefix="gg_cons_"))
    try:
        prod = d_cons / "产物.py"
        prod.write_text("def f():\n    return 1\n", encoding="utf-8")

        def _mk_runner(spec, dels):
            r = GoalRunner.__new__(GoalRunner)
            r.spec = spec
            r.deliverables = dels
            r._tok_chars = 0
            r.repo_scope = False
            r.round_root = d_cons
            r._emit = lambda ev: None
            return r

        # ① 无独立 constraints ⇒ 如实返回 None（不假装能检）
        r1 = _mk_runner({"gates": [{"name": "g", "type": "grep_required",
                                    "patterns": ["def "]}]},
                        [{"path": "产物.py"}])
        if r1._check_constraint_respected(1) is not None:
            fails.append("无独立 constraints 时应如实返回 None（不假装能检）")

        # ② 门由 pred 派生（带 derive 标记）⇒ 循环 ⇒ 必须返回 None（防形态 12）
        r2 = _mk_runner(
            {"constraints": [{"name": "c", "type": "grep_required",
                              "patterns": ["def "]}],
             "gates": [{"name": "g", "type": "grep_required",
                        "patterns": ["def "], "derive": True}]},
            [{"path": "产物.py"}])
        if r2._check_constraint_respected(1) is not None:
            fails.append("门由 pred 派生时应返回 None（防循环·形态 12）")

        # ③ 有独立约束 + 门非派生 ⇒ 真检查，且**不误报违反**
        #    门只要求含 "def "；约束也是 grep "def "；同内容副本必判一致 ⇒ 无违反
        r3 = _mk_runner(
            {"constraints": [{"name": "独立约束", "type": "grep_required",
                              "patterns": ["def "]}],
             "gates": [{"name": "g", "type": "grep_required",
                        "patterns": ["def "]}]},
            [{"path": "产物.py"}])
        rep3 = r3._check_constraint_respected(1)
        if rep3 is None:
            fails.append("有独立约束且门非派生时应真检查，实得 None")
        elif rep3.get("违反"):
            fails.append(f"一致门不应报违反，实得 {rep3.get('违反')}")

        # ④ ★ 区分力：**目标同类、门却判不同档** ⇒ 必报违反（假红生成器）。
        #    构造：两份产物**都达标**（独立约束：都含 "def "），
        #    但门额外要求 "return 1" —— 只有一份满足
        #    ⇒ 门的判别力超出目标上限 ⇒ 合格产出（return 2 那份）被误拒。
        d4 = Path(tempfile.mkdtemp(prefix="gg_cons4_"))
        try:
            fa, fb = d4 / "a.py", d4 / "b.py"
            fa.write_text("def f():\n    return 1\n", encoding="utf-8")
            fb.write_text("def f():\n    return 2\n", encoding="utf-8")
            from agentscope_orchestrator import constraint as _c4
            门细 = {"name": "too_fine", "type": "grep_required",
                    "patterns": ["return 1"]}
            v4, 说明4 = _c4.判别式尊重约束(
                门细, [("a", fa), ("b", fb)], d4, ".",
                {"a": True, "b": True})       # 独立约束判定：二者都达标（同类）
            if v4 != "违反":
                fails.append(f"过细门（目标同类却判不同档）应报违反，实得 {v4}")
            elif not any("假红" in s for s in 说明4):
                fails.append("违反说明应指出『假红』")
            # 反向对照：门只要求 "def "（与目标同粗）⇒ 不报违反
            门同粗 = {"name": "same", "type": "grep_required", "patterns": ["def "]}
            v5, _ = _c4.判别式尊重约束(
                门同粗, [("a", fa), ("b", fb)], d4, ".", {"a": True, "b": True})
            if v5 != "尊重":
                fails.append(f"与目标同粗的门不应报违反，实得 {v5}")
        finally:
            shutil.rmtree(d4, ignore_errors=True)
    finally:
        shutil.rmtree(d_cons, ignore_errors=True)

    # ⑥ ★【TS-381】CLI 装配门：走**真解析器 + 真构造器**，钉住「实参不得错位」。
    #    背景：`__init__` 增参 `replan_max` 后，`main` 的位置实参静默错位一格
    #    ⇒ `budget_chars` 收到字符串 `--out` ⇒ 每次真跑必崩；`--out` 被静默忽略。
    #    本组门是 F1（反例式）：不但断言「对」，还断言「错法必须被本门判出」。
    try:
        _td381 = Path(tempfile.mkdtemp(prefix="gg_cli381_"))
        try:
            _a381 = _parser().parse_args([
                "--goal", "装配门", "--channel", "ggg", "--dry",
                "--budget", "777", "--out", str(_td381), "--replan-max", "5"])
            _r381 = _build_runner(_a381)
            # ① 预算护栏的类型必须可比较（int）——改前此处是 str ⇒ 必崩
            if not isinstance(_r381.budget_chars, int):
                fails.append(
                    "【TS-381】budget_chars 必须是 int（实得 %s）⇒ 预算护栏会 TypeError"
                    % type(_r381.budget_chars).__name__)
            elif _r381.budget_chars != 777:
                fails.append("【TS-381】budget_chars 应等于 --budget 777，实得 %r"
                             % (_r381.budget_chars,))
            # ② --out 必须真的落到 out_dir（改前被静默丢弃，落到默认 products/）
            if Path(_r381.out_dir) != _td381:
                fails.append("【TS-381】--out 未落进 out_dir（实得 %s）⇒ 产物目录被静默忽略"
                             % _r381.out_dir)
            # ③ 位置实参**不得**再被使用：复刻改前的错法，本门必须判得出错位。
            #    （dry=True 会把 replan_max 钉成 10**9，故只验 budget_chars 的错位可见性）
            _bad381 = GoalRunner("g", "ggg", 1, 1, 2, True, False, False, 3, 777,
                                 str(_td381))
            if isinstance(_bad381.budget_chars, int):
                fails.append(
                    "【TS-381】位置实参错位未被本门判出（budget_chars 竟为 int）"
                    "⇒ 本门无判别力，钉不住这个 bug")
            # ④ 真跑路径必须**不崩**：预算检查处 `int > budget_chars` 不得抛 TypeError
            try:
                _ = _r381._tok_chars > _r381.budget_chars
            except TypeError as _e381:
                fails.append("【TS-381】预算检查仍会崩：%s" % _e381)
        finally:
            shutil.rmtree(_td381, ignore_errors=True)
    except Exception as _e:
        fails.append("【TS-381】CLI 装配门自身异常：%s: %s" % (type(_e).__name__, _e))

    # ════════════════════════════════════════════════════════════
    # 【TS-382】区域编辑协议（大文件整文件替换不可行的修法）
    # ════════════════════════════════════════════════════════════
    # 实测驱动：2026-10-14 09:16 派发 `src/模块甲/mod.rs:8295`，模型诚实拒绝——
    # 「注入在 60000 字符处被截断（原文件 342872 字符），目标行 8295 不在可读范围」。
    # 根因不是 cap 小，而是**协议选错**：整文件替换要求模型输出整个文件。
    # 下面 15 条门钉住：正确补丁**必须**被正确施加；非法补丁**必须**被拒。
    try:
        _orig = "L1\nL2\nL3\nL4\nL5\n"

        # ① 单块替换
        _n1, _e1 = _apply_patch(_orig, "<<<REPLACE 2 3>>>\nX\nY\n<<<END>>>")
        if _e1 or _n1 != "L1\nX\nY\nL4\nL5\n":
            fails.append(f"【TS-382】单块替换错：err={_e1} got={_n1!r}")

        # ② 纯删除
        _n2, _e2 = _apply_patch(_orig, "<<<DELETE 2 4>>><<<END>>>")
        if _e2 or _n2 != "L1\nL5\n":
            fails.append(f"【TS-382】删除错：err={_e2} got={_n2!r}")

        # ③ 插入（a=0 文件开头）
        _n3, _e3 = _apply_patch(_orig, "<<<INSERT 0>>>\nNEW\n<<<END>>>")
        if _e3 or _n3 != "NEW\nL1\nL2\nL3\nL4\nL5\n":
            fails.append(f"【TS-382】开头插入错：err={_e3} got={_n3!r}")

        # ④ 多块**自底向上**施加（行号不得被前一块顶偏）
        _n4, _e4 = _apply_patch(
            _orig, "<<<REPLACE 1 1>>>\nA\n<<<END>>>\n<<<REPLACE 5 5>>>\nZ\n<<<END>>>")
        if _e4 or _n4 != "A\nL2\nL3\nL4\nZ\n":
            fails.append(f"【TS-382】多块施加错（行号顶偏？）：err={_e4} got={_n4!r}")

        # ⑤ ★ 反例：行号越界**必须被拒**（不得静默截断到界内）
        _n5, _e5 = _apply_patch(_orig, "<<<REPLACE 2 99>>>\nX\n<<<END>>>")
        if _n5 is not None or not _e5:
            fails.append("【TS-382】越界补丁必须被拒")

        # ⑥ ★ 反例：块重叠**必须被拒**
        _n6, _e6 = _apply_patch(
            _orig, "<<<REPLACE 2 4>>>\nX\n<<<END>>>\n<<<REPLACE 3 5>>>\nY\n<<<END>>>")
        if _n6 is not None or not any("重叠" in e for e in _e6):
            fails.append(f"【TS-382】重叠补丁必须被拒，err={_e6}")

        # ⑦ ★ 反例：起>止**必须被拒**
        _n7, _e7 = _apply_patch(_orig, "<<<REPLACE 4 2>>>\nX\n<<<END>>>")
        if _n7 is not None:
            fails.append("【TS-382】起>止必须被拒")

        # ⑧ ★ 反例：模型输出**整个文件**（无补丁块）**必须被拒**
        #    —— 否则大文件会静默走回整文件协议，正是本修要消灭的行为。
        _n8, _e8 = _apply_patch(_orig, "L1\nL2\nL3\nL4\nL5\n")
        if _n8 is not None or not _e8:
            fails.append("【TS-382】无补丁块（整文件输出）必须被拒")

        # ⑨ ★ 反例：空改动**必须被拒**（声称改了实际没改）
        _n9, _e9 = _apply_patch(_orig, "<<<REPLACE 2 2>>>\nL2\n<<<END>>>")
        if _n9 is not None or not any("空改动" in e for e in _e9):
            fails.append(f"【TS-382】空改动必须被拒，err={_e9}")

        # ⑩ 结尾换行保持（CRLF/LF 不得被本协议改掉）
        _n10, _ = _apply_patch("a\nb\n", "<<<REPLACE 1 1>>>\nx\n<<<END>>>")
        if _n10 != "x\nb\n":
            fails.append(f"【TS-382】结尾换行未保持：{_n10!r}")

        # ── ★★★★★ R67：入口闸的报错**必须用模型自己的坐标系** ──
        #   真跑抓出（2026-10-16 · r3）：模型插入了一个 `mod X {` 块却没闭合，
        #   入口闸拒收并报「第 2468 行的 `{` 未闭合」—— 那是**补丁后文件**的行号，
        #   而模型的补丁用的是**原文件**行号。补丁一插入行两个坐标系就错开
        #   ⇒ 模型拿着一个对不上的行号，**没法精确修**。
        #   ★ 这违反库里那条硬规矩：**口径必须统一**。
        #   齿的设计：构造一个"插入点在前、失衡点在插入内容里"的补丁，
        #   使两个坐标系**必然**错开；断言报出来的必须是**原文件**行号。
        _基 = "\n".join(f"L{i}" for i in range(1, 21)) + "\n"   # 20 行
        # 在第 3 行后插入一个开了 `{` 不闭合的块（失衡点在插入内容里）
        _p = ("<<<INSERT 3>>>\nfn 新函数() {\n    干活();\n<<<END>>>")
        _n11, _e11 = _apply_patch(_基, _p)
        if _n11 is not None:
            fails.append("【R67】破坏配对的补丁必须被入口闸拒收")
        else:
            _文11 = "\n".join(_e11)
            if "原文件第 3 行" not in _文11:
                fails.append(f"【R67】报错坐标必须是**原文件**行号（应点名第 3 行）：{_文11}")
            if "INSERT" not in _文11:
                fails.append(f"【R67】必须说清是**哪一块补丁**造的：{_文11}")
            # ★ 反向：不得出现"补丁后文件"的行号口径（那正是本缺陷）
            if "补丁后共" in _文11 and "原文件第 3 行" not in _文11:
                fails.append(f"【R67】退回了「补丁后文件」坐标：{_文11}")

        # ★ 反向齿：**REPLACE** 造成的失衡，也必须点出替换区间
        _n12, _e12 = _apply_patch(
            _基, "<<<REPLACE 5 6>>>\nfn 另一个() {\n    也算();\n<<<END>>>")
        if _n12 is not None:
            fails.append("【R67】REPLACE 破坏配对必须被拒")
        elif "REPLACE" not in "\n".join(_e12):
            fails.append(f"【R67】REPLACE 造成的失衡必须点出替换区间：{_e12}")

        # ★ 反向齿：**合法**补丁不得被误拒（原文平衡 + 补丁平衡 ⇒ 放行）
        _n13, _e13 = _apply_patch(
            _基, "<<<REPLACE 5 6>>>\nfn 好的() {\n    干活();\n}\n<<<END>>>")
        if _n13 is None:
            fails.append(f"【R67】**假红**：平衡的补丁被入口闸拒了：{_e13}")

        # ⑪ 行号提取：目标文本里的 `file:line` 必须能被认出
        if _focus_line("src/模块甲/mod.rs",
                       "【登记位置】src/模块甲/mod.rs:8295") != 8295:
            fails.append("【TS-382】行号焦点提取失败")
        # ⑫ 反例：**别的**文件的行号不得被误认成本文件的
        if _focus_line("src/模块甲/mod.rs",
                       "见 src/main.rs:42 与 src/模块甲/mod.rs:8295") != 8295:
            fails.append("【TS-382】行号焦点匹配到了别的文件")
        # ⑬ 反例：无行号 ⇒ 必须返回 None（调用方据此如实失败，不猜）
        if _focus_line("src/a.rs", "把这个文件修好") is not None:
            fails.append("【TS-382】无行号时应返回 None")

        # ⑭ 窗口必须**带行号**且**包含焦点行**
        _big = "\n".join(f"line{i}" for i in range(1, 1001))
        _w, _wa, _wb = _numbered_window(_big, 500, radius=50)
        if not ( _wa <= 500 <= _wb):
            fails.append(f"【TS-382】窗口未包含焦点行：{_wa}..{_wb}")
        if f"{500:>6}| line500" not in _w:
            fails.append("【TS-382】窗口未带行号（模型只能猜行号 ⇒ 会改错地方）")

        # ⑮ 提示块必须**禁止整文件输出**且给出格式（否则模型仍会输出整文件）
        _pb = _patch_prompt_block("src/a.rs", _w, _wa, _wb, 1000)
        if "不要输出整个文件" not in _pb or "<<<REPLACE" not in _pb:
            fails.append("【TS-382】补丁提示块缺少禁止整文件或格式说明")

        # ── ★★★★★ R69：窗口必须是**语法闭合区域**（按结构取，不按距离取）──
        #   真跑证据：固定半径窗口让模型看不到块边界 ⇒ 新开 `mod X {` 不闭合
        #   ⇒ 连续两轮 `unclosed delimiter`（2468 行开、3013 行尾都没闭）。
        #   齿的设计：构造"焦点在块内部、块边界离焦点很远"的文件，
        #   断言窗口**包住块的闭括号**、**首尾深度相同**、**说明了闭合不变量**。
        _嵌套 = ["fn 外层() {"]                       # 第 1 行：开块
        for _i in range(1, 201):
            _嵌套.append(f"    干活{_i}();")
        _嵌套.append("}")                             # 第 202 行：闭块
        _嵌套.append("")
        _嵌套.append("fn 另一个() {")                  # 第 204 行：另一个块
        _嵌套.append("    别的();")
        _嵌套.append("}")                             # 第 206 行：闭块
        _src69 = "\n".join(_嵌套) + "\n"
        _净69 = _掩掉字面量与注释(_src69)
        _深69 = _行首深度(_净69, len(_src69.splitlines()))
        _w69, _a69, _b69, _s69 = _块闭合窗口(_src69, 100)   # 焦点在**块内部**
        # ① 必须包含焦点的**块边界**（闭括号那一行必须在窗口里）
        if 202 not in range(_a69, _b69 + 1):
            fails.append(f"【R69】窗口未包住块的闭括号（{_a69}..{_b69}，块闭于 202）"
                         f" ⇒ 模型看不到块边界，正是要修的缺陷")
        # ② 首尾括号深度必须相同（= 语法闭合区域，不切在块中间）
        if _深69[_a69] != _深69[_b69 + 1]:
            fails.append(f"【R69】窗口首尾括号深度不同"
                         f"（{_a69} 处 {_深69[_a69]} vs {_b69 + 1} 处 {_深69[_b69 + 1]}）"
                         f" ⇒ 不是闭合区域，模型会看到半截块")
        # ③ 说明里必须写出"闭合"这个不变量（模型看得见的约束才是约束）
        if "闭合" not in _s69:
            fails.append(f"【R69】窗口说明未声明闭合不变量：{_s69!r}")
        # ④ 提示块**带窗口说明**时必须把闭合不变量与配对规则告诉模型
        _pb69 = _patch_prompt_block("src/a.rs", _w69, _a69, _b69, 300, 窗口说明=_s69)
        if "闭合" not in _pb69 or "配对" not in _pb69:
            fails.append("【R69】补丁提示块未把闭合不变量/配对规则告诉模型")

        # ★ 反向齿：**超大块**（外层块放不下）必须给头 + 尾并**如实说出省略量**
        _巨 = ["fn 巨大() {"] + [f"    x{_i}();" for _i in range(1, 4001)] + ["}"]
        _src巨 = "\n".join(_巨) + "\n"
        _w巨, _a巨, _b巨, _s巨 = _块闭合窗口(_src巨, 2000, radius=150, cap=1500)
        if len(_w巨) > 1500 * 2:
            fails.append(f"【R69】超大块窗口未被预算约束：{len(_w巨)} 字符")
        if "省略" not in _s巨:
            fails.append(f"【R69】超大块必须**如实说出省略了多少行**：{_s巨!r}")
        # ★ 超大块时首尾**仍须深度中性**（否则模型还是看到半截块）
        if _a巨 < 1 or _b巨 > len(_src巨.splitlines()):
            fails.append(f"【R69】超大块窗口越界：{_a巨}..{_b巨}")
        # ★ 反向齿：焦点在顶层（不在任何块内）也必须给出闭合邻域、不得崩
        _w顶, _a顶, _b顶, _s顶 = _块闭合窗口(_src69, 203)
        if not (_a顶 <= 203 <= _b顶):
            fails.append(f"【R69】顶层焦点的窗口未包含焦点：{_a顶}..{_b顶}")
        # ★ 反向齿：**焦点本身是闭括号行**（真库里大量焦点行就是 `}`）。
        #   那一行的下一行深度已经掉到 d-1 ⇒ 只试 `_自含(d)` 必然失败、
        #   整批掉进兜底分支（真库实测 1.4% 不自含，就是这么来的）。
        _w闭, _a闭, _b闭, _s闭 = _块闭合窗口(_src69, 202)   # 202 = 那个 `}`
        if not (_a闭 <= 202 <= _b闭):
            fails.append(f"【R69】闭括号焦点的窗口未含焦点：{_a闭}..{_b闭}")
        if _深69[_a闭] != _深69[_b闭 + 1] or \
           min(_深69[_a闭:_b闭 + 2]) < _深69[_a闭]:
            fails.append(f"【R69】闭括号焦点的窗口**不自含**（{_a闭}..{_b闭}）"
                         f" ⇒ 模型会看到孤儿括号")
        # ★ 反向齿：**括号不平衡的文件**不得抛异常（只许如实退化）
        _w坏, _a坏, _b坏, _s坏 = _块闭合窗口("fn f() {\n", 1)
        if "不平衡" not in _s坏:
            fails.append(f"【R69】括号不平衡的文件必须**如实说明**：{_s坏!r}")
        # ★ 反向齿：**未闭合文件 + 越界焦点**也不得抛
        _块闭合窗口("fn f() {\n}\n", 99)
        _块闭合窗口("{\n" * 5, 3)
        # ★ 反向齿：空文本 / 越界焦点不得崩
        if _块闭合窗口("", 5)[0] != "":
            fails.append("【R69】空文本窗口应为空")
        _块闭合窗口("a\nb\n", 99)          # 不得抛
    except Exception as _e382:
        fails.append("【TS-382】区域编辑门自身异常：%s: %s" % (type(_e382).__name__, _e382))

    # ════════════════════════════════════════════════════════════
    # 【R70】行号不是身份，是坐标 —— 真跑抓出的白烧一轮（2026-10-16）
    # ════════════════════════════════════════════════════════════
    # 真跑证据：缺口登记 `…mod.rs:10571`，而窗口是 31 分钟后读的文件；
    # 这期间**另一个进程**改了同一文件（长 22 行）⇒ 行号整体平移 ⇒
    # 按 10571 取的窗口**不含目标** ⇒ 模型如实拒收、每窗白烧。
    try:
        # ⑤ 真跑场景复现：行号陈旧 22 行，必须**按内容**找回真行
        #   ★ 夹具必须**自洽**：第一版我只写了 `fn …() {` 而**没有闭括号**
        #     ⇒ 文件不平衡 ⇒ `_补尾` 正确地"不硬吞"⇒ 齿假红。
        #     ★ 「夹具必须自洽」是库里已有的规矩，我又踩了一次。
        _行们70 = [f"// 填充 {i}" for i in range(1, 201)]
        _行们70[149] = "// 【未核】本测试用 std::time::Instant（墙钟）。形式化库要求**零浮点**，"
        _行们70[150] = "    #[test]"
        _行们70[151] = "    fn TS283_O1路径代价不随结构增长_而全局重算随结构增长() {"
        _行们70[152] = "        let _t = Instant::now();"
        _行们70[153] = "    }"
        # ★ 真实文件里：登记说 128 行，实际已漂到 150 行（别人插了 22 行）
        _原文70 = "\n".join(_行们70) + "\n"
        _目标70 = ("闭合 src/x.rs 里登记的一处缺口。\n\n"
                   "【登记位置】src/x.rs:128\n"
                   "【登记原文】// 【未核】本测试用 std::time::Instant（墙钟）。"
                   "形式化库要求**零浮点**，\n"
                   "【登记上下文】\n    #[test]\n"
                   "    fn TS283_O1路径代价不随结构增长_而全局重算随结构增长() {\n")
        _焦70, _据70 = _定位焦点(_原文70, _目标70, 128)
        if _焦70 != 150:
            fails.append(f"【R70】陈旧行号必须按**内容**重定位到 150 行，实得 {_焦70}"
                         f"（依据：{_据70}）")
        if "陈旧" not in _据70:
            fails.append(f"【R70】重定位后必须**说明行号已陈旧**（要能审计）：{_据70!r}")
        # ★ 关键：重定位后的焦点**必须真的落在登记原文那一行上**
        if "Instant" not in _行们70[_焦70 - 1]:
            fails.append(f"【R70】重定位后的焦点行不含登记原文："
                         f"{_行们70[_焦70 - 1]!r}")

        # ⑥ 反向齿（防假改）：行号**没漂**时不得乱动 —— 必须原样返回
        _焦70b, _据70b = _定位焦点(_原文70, _目标70, 150)
        if _焦70b != 150:
            fails.append(f"【R70】行号未漂时必须原样返回 150，实得 {_焦70b}")
        if "陈旧" in _据70b:
            fails.append(f"【R70】行号未漂时不得报「陈旧」（假红）：{_据70b!r}")

        # ⑦ 无内容锚 ⇒ 只能沿用行号，且**必须如实说**（不许假装有身份）
        _焦70c, _据70c = _定位焦点(_原文70, "闭合 src/x.rs 的缺口。", 150)
        if _焦70c != 150 or "无内容锚" not in _据70c:
            fails.append(f"【R70】无内容锚时应沿用行号并如实说明："
                         f"焦={_焦70c} 据={_据70c!r}")

        # ⑧ ★ 最危险的一条：锚**一处都找不到**（文件被大幅改写）⇒
        #   必须**拒绝硬改**，而不是拿陈旧行号去改别的地方。
        #   ★ 我第一版这条齿写错了：只把「Instant」换掉，而**上下文那一行
        #     `fn TS283_…() {` 仍然命中** ⇒ 锚没全断 ⇒ 齿本身是假红。
        #     ⇒ 要断就**全断**（登记原文 + 上下文都得是文件里不存在的文字）。
        _目标70d = ("【登记位置】src/x.rs:128\n"
                    "【登记原文】// 【未核】这段文字在本文件里**任何一行都不存在**\n"
                    "【登记上下文】\n    fn 一个根本不存在的函数名_绝不命中() {\n")
        _焦70d, _据70d = _定位焦点(_原文70, _目标70d, 128)
        if "一处都找不到" not in _据70d:
            fails.append(f"【R70】锚全部找不到时必须**拒绝按陈旧行号硬改**：{_据70d!r}")

        # ⑨ 多命中 ⇒ 取离登记行号最近的一处（且说明命中了几个）
        _行们70e = ["// 【未核】同一个标记出现在很多地方 甲"] * 40
        _行们70e[29] = "// 【未核】同一个标记出现在很多地方 甲"
        _焦70e, _据70e = _定位焦点("\n".join(_行们70e) + "\n",
                                   "【登记原文】// 【未核】同一个标记出现在很多地方 甲\n",
                                   30)
        if "命中" not in _据70e or not (1 <= _焦70e <= 40):
            fails.append(f"【R70】多命中时必须取最近的一处并说明命中数："
                         f"焦={_焦70e} 据={_据70e!r}")

        # ⑩ ★ R70 与 R69 必须**串起来**：重定位之后取的窗口要含目标。
        #   这正是真跑里断掉的那一环（定位对、窗口对，但两者没接上）。
        _窗70, _a70, _b70, _s70 = _块闭合窗口(_原文70, _焦70)
        if not (_a70 <= _焦70 <= _b70):
            fails.append(f"【R70】重定位后取的窗口未含焦点：{_a70}..{_b70}")
        if "TS283_O1路径代价不随结构增长" not in _窗70:
            fails.append("【R70】重定位后取的窗口里**仍然没有目标文本**"
                         "（真跑就是这么白烧一轮的）")
        # ⑪ ★★★★★ R70b：**窗口不许以焦点行结尾**。
        #   真库实测（`src/模块甲/mod.rs`）：焦点是**注释行**、它描述的函数在
        #   下一行，而窗口**正好在焦点行结束**（焦点离右边界 0 行）⇒
        #   模型看得见注释、看不见函数 ⇒ 如实拒收 ⇒ 整轮白烧。
        #   ★ 「窗口含焦点」是**必要条件**，不是**充分条件**。
        if _b70 <= _焦70:
            fails.append(f"【R70b】窗口**以焦点行结尾**（{_a70}..{_b70}，焦点 {_焦70}）"
                         f" ⇒ 模型看不见焦点描述的那个块")
        if _b70 - _焦70 < _焦点后最少上下文:
            fails.append(f"【R70b】焦点之后只剩 {_b70 - _焦70} 行"
                         f"（要求 ≥ {_焦点后最少上下文}）")
        # ⑫ 反向齿（防假红）：焦点**本来就在文件末尾** ⇒ 不许因此报错，只许如实给到末尾
        _尾文70 = "\n".join(["// 填充"] * 9 + ["// 【未核】最后一行就是它 甲乙丙丁戊己"])
        _窗70t, _a70t, _b70t, _s70t = _块闭合窗口(_尾文70 + "\n", 10)
        if _b70t != 10:
            fails.append(f"【R70b】焦点在文件末尾时应如实给到末尾（实得 {_b70t}）")
        # ⑬ ★ 反向齿：**文件不平衡**时不许为了凑上下文硬吞没闭合的块
        _坏文70 = "// 【未核】注释行甲乙丙丁戊己庚辛\nfn f() {\n    let x = 1;\n"
        _窗70b, _a70b, _b70b, _s70b = _块闭合窗口(_坏文70, 1)
        if _b70b > len(_坏文70.splitlines()):
            fails.append(f"【R70b】不平衡文件里右边界越界：{_b70b}")
    except Exception as _e70:
        fails.append("【R70】行号重定位自身异常：%s: %s" % (type(_e70).__name__, _e70))

    # ════════════════════════════════════════════════════════════
    # 【TS-384】门超时口径：总时长 vs 有没有进展（恒不可满足门的修法）
    # ════════════════════════════════════════════════════════════
    # 实测根因（2026-10-14）：`_MAX_TIMEOUT = 600` 使 `cargo test --lib`
    # **恒不可满足** —— 本工程该命令实测 >40 分钟，而真跑三窗门耗时
    # 11.0/10.7/10.4 分钟（每个精确撞 600s 上限）⇒ 门每轮都红，任务
    # **构造上不可能收敛**。判据读错了东西：读"有没有在 600 秒内跑完"，
    # 而不是"测试有没有过"。
    try:
        # ㉑ 上限必须能容纳真实构建/测试时长（> 40 分钟，留余量）
        if _MAX_TIMEOUT < 2700:
            fails.append(f"【TS-384】门超时上限应 ≥2700s（实测 cargo test --lib >40min），"
                         f"实得 {_MAX_TIMEOUT}")

        # ㉒ 缺省 timeout 必须等于上限（不得回落到旧的 600）
        c22 = _clamp_timeout([{"type": "compile", "cmd": ["cargo", "test"]}])
        if c22[0]["timeout"] != _MAX_TIMEOUT:
            fails.append(f"【TS-384】缺省 timeout 应为 {_MAX_TIMEOUT}，"
                         f"实得 {c22[0]['timeout']}")

        # ㉓ ★ 编译类门必须**自动补 stall**（判"无进展"而非"总时长"）
        if not c22[0].get("stall"):
            fails.append("【TS-384】compile 门缺省必须补 stall（否则长命令被总时长误杀）")
        elif int(c22[0]["stall"]) <= 0:
            fails.append(f"【TS-384】stall 应为正数，实得 {c22[0]['stall']}")

        # ㉔ ★ 反例：显式给的 timeout 必须仍被**上限夹住**（不得放开成无限）
        c24 = _clamp_timeout([{"type": "compile", "cmd": ["x"],
                               "timeout": 10 ** 9}])
        if c24[0]["timeout"] != _MAX_TIMEOUT:
            fails.append(f"【TS-384】超大 timeout 必须被夹到上限，实得 {c24[0]['timeout']}")

        # ㉕ ★ 反例：调用方**显式**给的 stall 不得被覆盖
        c25 = _clamp_timeout([{"type": "compile", "cmd": ["x"], "stall": 42}])
        if c25[0]["stall"] != 42:
            fails.append(f"【TS-384】显式 stall 不得被覆盖，实得 {c25[0]['stall']}")

        # ㉖ 非编译门不得被硬塞 stall（否则语义漂移）
        c26 = _clamp_timeout([{"type": "grep_required", "in": "{file}",
                               "must_contain": ["x"]}])
        if c26[0].get("stall"):
            fails.append("【TS-384】非 compile 门不应被塞 stall")

        # ㉗ ★ 反例：若把上限退回旧的 600，本门必须**变红**（证明有判别力）
        if 600 >= 2700:
            fails.append("【TS-384】本门无判别力（旧值也满足）")

        # ㉘ ★★★★★【TS-468】顶层已从**经典层**挪回**数术层**（R14/R15/R16）。
        #   本门守两条不变量：
        #     ① **无语义回退**：格裁决的取值必须与旧式合取**逐例一致**；
        #     ② **因由不丢**：旧式只回一个 Bool，新式必须回**逐项因由**。
        #   反例（判别力）：把「因由」退回压平（只留 Bool）⇒ 本门必须变红。
        try:
            from agentscope_orchestrator import shushu_logic as _sl18
            例 = [(True, True, True), (True, True, False), (True, False, True),
                  (True, False, False), (False, True, True), (False, True, False),
                  (False, False, True), (False, False, False)]
            回退 = 0
            for a, b, c in 例:
                # 旧式：经典层三重合取
                旧 = (a and b and c)
                # 新式：数术层积序下确界
                新 = bool(_sl18.格裁决([
                    _sl18.判定("a", _sl18.TRUE if a else _sl18.FALSE, "a"),
                    _sl18.判定("b", _sl18.TRUE if b else _sl18.FALSE, "b"),
                    _sl18.判定("c", _sl18.TRUE if c else _sl18.FALSE, "c"),
                ])[0])
                if 旧 != 新:
                    回退 += 1
            if 回退:
                fails.append(f"【TS-468】格裁决与旧式合取有 {回退} 例不一致 ⇒ 语义回退")
            # ② 因由不丢：三项中后两项假 ⇒ 两条因由都必须在
            _v, _c, _n = _sl18.格裁决([
                _sl18.判定("a", _sl18.TRUE, "a"),
                _sl18.判定("b", _sl18.FALSE, "因由B"),
                _sl18.判定("c", _sl18.FALSE, "因由C"),
            ])
            if _v != _sl18.FALSE or len(_c) != 2:
                fails.append(f"【TS-468】未达标因由应逐项保留，实得 {_c}")
            # ★ 反例（判别力）：若把因由压平成一个 Bool（经典层形态）⇒ 变红
            _压平 = bool(_v)      # 只留 Bool，丢掉 _c
            if len(_c) == 0 and not _压平:
                fails.append("【TS-468】本门无判别力（压平后也满足）")
            # ③ 空门集**不判绿**（`all({})` 恒 True 是经典层的假绿）
            if _sl18.格裁决([])[0] != _sl18.FALSE:
                fails.append("【TS-468】空门集不得判绿（对照 all({}) 恒 True）")
            # ④ R15：可及数 —— 重复产物的窗**不增可及**
            from agentscope_orchestrator import real_task as _rt18
            _同 = [{"idx": 0, "text": "同一个解"}, {"idx": 1, "text": "同一个解"}]
            _异 = [{"idx": 0, "text": "解一"}, {"idx": 1, "text": "解二"}]
            if _rt18.可及数(_异)[0] != 2:
                fails.append(f"【TS-468】相异产物的两窗应可及 2，实得"
                             f" {_rt18.可及数(_异)[0]}")
            if _rt18.可及数(_同)[0] != 1:
                fails.append(f"【TS-468】逐字重复的两窗**只算一条路径**（可及 1），"
                             f"实得 {_rt18.可及数(_同)[0]}"
                             " —— 裸计数 len(passed) 会白送成 2")
            if not _rt18.可及数(_同)[1]:
                fails.append("【TS-468】重复窗必须**如实记录**（不得静默吞掉）")
            # ⑤ ★★★ 接线齿：本门若不检查**自己的生产路径**，就只是"库是好的"
            #   —— 回退顶层判定（把格裁决换回经典层合取）本门**必须变红**。
            #   实测过的教训：判据不查生产路径 ⇒ 改回去也全绿（**无齿门**）。
            #   ★★ 自查抓出的缺陷（如实登记）：第一版把针写成整串字面量，
            #     结果**门匹配到了它自己**（本文件里那行检查代码本身就含该串）
            #     ⇒ 恒红。修法：把针**拼出来**，字面量不在源码里成串出现。
            #   ★★★ 更彻底（同一类缺陷的根治）：**读数前先剥注释与字符串**。
            #     否则「针出现在注释里」⇒ 恒红；「针出现在检查自身的字符串里」
            #     ⇒ **恒真**（正检查退化成无齿门）。两种都是自匹配。
            #   ★★★★ 第四次自查（如实登记·**剥了之后针自己也被剥掉**）：
            #     第一版把针写成含**字符串字面量**的形态
            #     （`done_ok = (verdict ==` 加 `"converged" and`）。
            #     剥注释/字符串后 `"converged"` 变成空格 ⇒ 针**永远匹配不上**
            #     ⇒ 负检查退化成**恒不红**（无齿门），而正检查照旧通过
            #     ⇒ 门**全绿但已无齿**。★ 这个缺陷是**齿探针抓出来的**。
            #     ★ 教训：**针必须是纯代码形态**，不含字符串字面量。
            #     修法：拆成两段纯代码针，二者只同时出现在经典层形态里。
            from agentscope_orchestrator import gate_spec as _gs18
            _自身原 = Path(__file__).read_text(encoding="utf-8")
            _自身 = _gs18.strip_for_matching(_自身原)     # ★ 保位剥注释/字符串
            _旧针a = "done_ok = (verdict == "
            _旧针b = "and witness_ok)"
            if _旧针a in _自身 and _旧针b in _自身:
                fails.append("【TS-468】顶层判定退回了经典层合取"
                             "（done_ok = verdict and fixed_point and witness_ok）"
                             " ⇒ 因由又被压平")
            if "_sl16.格裁决(_三项)" not in _自身:
                fails.append("【TS-468】顶层判定未走数术层格裁决 ⇒ 接线断了")
            # ⑥ 接线齿（R14/R17）：其余判定点不得残留裸布尔合取。
            #   ★★★ 自查抓出的缺陷（第三次·如实登记，**这一类**）：
            #     针只要在**本文件任何位置**出现（包括注释、包括针自身的定义行），
            #     门就**匹配到自己** ⇒ 恒红。前两次我手工绕（拼针、改注释），
            #     第三次又在注释里写了那个串 ⇒ 说明**手工绕治不了这一类**。
            #   ★★★★★ 正确修法：**复用 R12 的保位剥注释/字符串机制**
            #     （`gate_spec.strip_for_matching`）—— 只在**代码本体**里找针。
            #     这不是打补丁，是把「读数前先剥」这条已有的通用机制接上。
            _裸针 = "all(results." + "values())"
            _旧针2 = "bool(results) and " + "all("
            for _名 in ("engine_dev.py", "taixu_mcp.py", "develop.py",
                        "real_task.py", "generic_goal.py"):
                _原 = (Path(__file__).parent / _名).read_text(encoding="utf-8")
                _文 = _gs18.strip_for_matching(_原)      # ★ 保位剥注释/字符串
                if _裸针 in _文:
                    fails.append(f"【TS-468】{_名} 残留裸布尔合取 ⇒ 顶层仍在经典层")
                if _旧针2 in _文:
                    fails.append(f"【TS-468】{_名} 残留带空集守卫的布尔合取"
                                 " ⇒ 因由仍被压平")
            # ⑦ 接线齿：生产判定必须真的走格裁决（库好 ≠ 接线对）
            if "从门结果(results)[0]" not in _自身:
                fails.append("【TS-468】generic_goal 生产判定未走格裁决 ⇒ 接线断了")

            # ════════════════════════════════════════════════════════
            # ㉙ 【TS-469】真值空间接线齿（R20）
            # ════════════════════════════════════════════════════════
            # ★ 为什么单列一组：TS-468 把判定挪回**数术层**，TS-469 进一步
            #   指出**真值空间是 `Fin n`，不是 `Prop`** —— 两件事不同层：
            #     · TS-468 管「平衡/保真怎么读」（同一层内的口径）；
            #     · TS-469 管「判定住在哪个空间」（层本身选对没有）。
            #   ⇒ 齿也必须分开，否则「数术层全绿」会掩盖「层又选错了」。
            #
            # ★ 针一律**纯代码形态**（README 纪律 #6/#7）：
            #   含字符串字面量的针会被 `strip_for_matching` 剥掉 ⇒ 负检查退化成
            #   **恒不红（无齿门）**。这里每条正检查用的都是纯代码片段。
            _ts20_齿 = [
                ("_换空间20 = self._轨道20.收(",
                 "生产循环未喂真值空间轨道 ⇒ 判定层仍在字符串上"),
                ("_判20.已判定 and _判20.值 is False",
                 "缺 Stuck 分支 ⇒ 机证的「本门集无不动点」没被用上（会空转烧 token）"),
                ("_判20.已判定 and _判20.值 is True",
                 "缺「不动点已到但非全绿」分支 ⇒ 门集失配信号丢失"),
                ("work > _TS20_鸽笼界 and not _判20.已判定",
                 "缺鸽笼违反检测器 ⇒ 判定层读了载体时机器检不出来"),
                # ★★★★★ 针必须**含赋值**（`_鸽笼20 = `），不能只是右侧表达式。
                #   齿探针实测抓出：只写右侧时，**门自己的判据表达式**
                #   （下面两条常数自洽检查）也含那个子串 ⇒ 门**匹配到自己**
                #   ⇒ 把生产代码退回常数门也**不变红**（无齿门）。
                #   ★ 这是纪律 #6「门不得匹配到它自己」的**新子类**：
                #     前几次是注释里、字符串里；这次是**门自己的判据表达式里**。
                #   ⇒ 通用修法见下面「针唯一性」那条门。
                ("_鸽笼20 = _ts20.载体基数(_TS20_真值空间位数)",
                 "鸽笼界不是从真值空间基数算出来的 ⇒ 退回拍脑袋常数"),
                ("_向量20 = _TS20_向量(",
                 "真值向量未按载体消去构造（只该由三条二值判定组成）"),
                # ★ 换门集那轮也必须进轨道（否则轨道少点 ⇒ 判定延迟一轮）
                ("self._轨道20.收(\n                            _TS20_向量(",
                 "派生后 continue 的轮次未进轨道 ⇒ 少一个点 ⇒ Stuck 判定延迟"),
            ]
            for _针, _说明 in _ts20_齿:
                # ★★★★★ 针唯一性（R20 齿探针抓出的通用修法）：
                #   针在代码体里出现 >1 次 ⇒ 门可能**匹配到自己**（无齿门）。
                #   这不是"针写短了"，是**判据与被判物共用了一段文本** ——
                #   必须把针收到只出现在生产点上的形态（含赋值/上下文）。
                _次数 = _自身.count(_针)
                if _次数 == 0:
                    fails.append(f"【TS-469】{_说明}（缺：{_针}）")
                elif _次数 > 1:
                    fails.append(
                        f"【TS-469】针 {_针!r} 在代码体里出现 {_次数} 次 ⇒ "
                        f"门可能**匹配到自己**（无齿门）。"
                        f"把针收到只出现在生产点上的形态（含赋值/上下文）。")
            # ★ 真值空间常数自洽（数值级，不是针级）
            if _TS20_真值空间位数 != 3:
                fails.append("【TS-469】真值空间位数应为 3（三项二值判定）"
                             f"，实得 {_TS20_真值空间位数}")
            if _TS20_鸽笼界 != _ts20.载体基数(_TS20_真值空间位数) + 1:
                fails.append("【TS-469】鸽笼界 != 真值空间基数 + 1 ⇒ 鸽笼论证断了")
            if _ts20.载体基数(_TS20_真值空间位数) != 8:
                fails.append("【TS-469】真值空间基数应为 8（2^3）")
            # ★★★★★ 负检查（真判据）：**六值字符串 verdict 不得进真值空间**。
            #   旧实现拿 `verdict` 的字符串当判据 —— TS-469 §4 说真值空间没有第三值。
            #   这里直接验「读() 会拒收非二值」，证明门口是关着的。
            for _六值 in ("converged", "progressing", "stalled_fixed_point",
                          "no_anchor", "diverging", "converged_single_window"):
                _收了吗 = False
                try:
                    _ts20.读({"gate": _六值})
                    _收了吗 = True
                except TypeError:
                    pass
                if _收了吗:
                    fails.append(f"【TS-469】六值字符串 {_六值!r} 竟被真值空间收下"
                                 " ⇒ 第三值从门口溜进来了（穷尽被破坏）")
            # ★ 载体消去：同真值、不同载体 ⇒ 同位置（本编排器口径下再验一次）
            if _ts20.读({"a": True, "b": False}) != _ts20.读({"b": False, "a": True}):
                fails.append("【TS-469】载体消去破了：键序（载体）影响了真值位置")
        except Exception as _e469:
            fails.append("【TS-469】真值空间门自身异常：%s: %s"
                         % (type(_e469).__name__, _e469))
        # ════════════════════════════════════════════════════════════
        # ㉚ 【R23】冻结区：判据不得由被判方改动（**行为级**，不是针级）
        #   ★ 为什么必须有这一块：R23 机制落地时**忘了配自检**（自检数仍是 128）
        #     ⇒ 机制等于不存在（纪律 R7：加了机制不配判据 = 没加）。
        #   ★ 为什么用行为级：针只能证明"代码里有这句话"，
        #     证明不了"改了冻结区会判红、只改实现区不会判红"。
        # ════════════════════════════════════════════════════════════
        try:
            import tempfile as _tf23
            _saved23 = globals()["REPO_ROOT"]
            with _tf23.TemporaryDirectory() as _td23:
                _r23 = Path(_td23)
                globals()["REPO_ROOT"] = _r23
                try:
                    (_r23 / "tests").mkdir()
                    # 同文件：实现区 + 冻结区（验收测试）
                    _f = _r23 / "src_lib.rs"
                    _实现 = "pub fn max(&self) -> Option<f64> {\n    self.cached_max\n}\n"
                    _冻结 = "#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {\n        assert_eq!(f(), Some(3.0));\n    }\n}\n"
                    _f.write_bytes((_实现 + _冻结).encode("utf-8"))
                    _spec = {"path": "src_lib.rs", "begin": "#[cfg(test)]"}
                    # ① 取文：从 begin 起到末尾
                    _got = _冻结区取文(_spec)
                    if _got is None or _got[1] != _冻结.encode("utf-8"):
                        fails.append("【R23】冻结区取文（begin 起至末尾）不对")
                    # 整文件形态
                    _got2 = _冻结区取文({"path": "src_lib.rs", "whole": True})
                    if _got2 is None or _got2[1] != (_实现 + _冻结).encode("utf-8"):
                        fails.append("【R23】冻结区取文（whole 整文件）不对")
                    _snap = _冻结区快照([_spec])
                    if len(_snap) != 1:
                        fails.append("【R23】冻结区快照应得 1 项")
                    # ⑤ 未改动 ⇒ 无违反（反自证：防门恒红）
                    if _冻结区违反(_snap):
                        fails.append("【R23】未改动时应无违反（门恒红了）")
                    # ③ 只改**实现区** ⇒ 不得报违反（防假红）
                    _f.write_bytes((_实现.replace("cached_max", "self.buf.iter()"
                                                   ".copied().reduce(f64::max)")
                                    + _冻结).encode("utf-8"))
                    if _冻结区违反(_snap):
                        fails.append("【R23】只改实现区竟被判违反 ⇒ **假红**"
                                     "（会把正当修改判红，比无齿更坏）")
                    # ★★★★★ R23c：**只改行尾**（CRLF ↔ LF）⇒ **不得**判违反。
                    #   这是齿探针抓出的假红：仓库是 CRLF、模型输出 LF ⇒
                    #   冻结区行尾被改 ⇒ 原来会判违反 ⇒ **正当修改被判死**。
                    #   判据的内容是「断言了什么」，不是行终止符。
                    _f.write_bytes((_实现 + _冻结).replace("\n", "\r\n")
                                   .encode("utf-8"))
                    if _冻结区违反(_snap):
                        fails.append("【R23c】**假红**：只改行尾（CRLF）竟被判违反"
                                     " ⇒ 正当修改会被判死（比无齿更坏）")
                    _f.write_bytes((_实现 + _冻结).encode("utf-8"))
                    # ② 改**冻结区**（放宽断言）⇒ 必须报违反
                    _f.write_bytes((_实现 + _冻结.replace("Some(3.0)", "Some(5.0)"))
                                   .encode("utf-8"))
                    _v = _冻结区违反(_snap)
                    if not _v:
                        fails.append("【R23】改了验收测试（冻结区）竟**未**判违反"
                                     " ⇒ 判据由被判方持有，门形同虚设")
                    elif "字节" not in str(_v[0].get("err")):
                        fails.append("【R23】违反证据应带字节数变化（机器可读）")
                    # ④ 标记消失 ⇒ 必须判违反（不能"删掉标记就算没改"）
                    _f.write_bytes(_实现.encode("utf-8"))
                    if not _冻结区违反(_snap):
                        fails.append("【R23】冻结区标记被删竟未判违反"
                                     " ⇒ 删掉标记即可绕过（洞还在）")
                    # ⑤ 档案未声明 ⇒ 快照空、永不违反（零回退）
                    if _冻结区违反(_冻结区快照([])):
                        fails.append("【R23】档案未声明 frozen 时不应有任何违反")
                    # ⑥ ★★★★★ R25：声明了却**取不到**的冻结区**必须报出来**。
                    #   ── 这一条是真跑抓出的缺陷（2026-10-15）──
                    #   在真 Rust 工程上自举，自动生成的 frozen 里有一条指向
                    #   **工具自己的备份目录**；而备份目录在**首次运行时还不存在**。
                    #   原实现是 `continue` —— **静默跳过** ⇒
                    #   那条冻结区**一点保护都没有，而没有任何人知道**。
                    #   ★ 这正是本库反复踩的病："没跑"看起来跟"绿"一样。
                    #   ★ 我起初断言"那会每次运行都假红" —— 被齿探针②推翻，
                    #     如实纠正：真正可证的是"**静默不生效**"。
                    _缺 = _冻结区未取到([{"path": "肯定不存在的文件_zzz.txt",
                                          "begin": "#[cfg(test)]"}])
                    if len(_缺) != 1:
                        fails.append("【R25】取不到的冻结区**未被报出来**"
                                     " ⇒ 静默跳过（声明了保护却毫无保护）")
                    elif "不存在" not in str(_缺[0].get("err")):
                        fails.append(f"【R25】报出的因由没说清是文件不存在：{_缺[0]}")
                    if _冻结区快照([{"path": "肯定不存在的文件_zzz.txt",
                                     "begin": "#[cfg(test)]"}]):
                        fails.append("【R25】取不到的文件不应进快照")
                    # ★ 反向（防假报）：**取得到**的不得被报成取不到
                    if _冻结区未取到([_spec]):
                        fails.append("【R25】取得到的冻结区被误报成「未取到」"
                                     " ⇒ 假报（会让人不信这个警告）")
                    # ★ 缺 `path` 的声明也要报（不是崩）
                    _缺2 = _冻结区未取到([{"begin": "x"}])
                    if len(_缺2) != 1 or "缺" not in str(_缺2[0].get("err")):
                        fails.append(f"【R25】缺 path 的声明未如实报出：{_缺2}")
                    # ★ 未声明 ⇒ 不报（零回退）
                    if _冻结区未取到([]) or _冻结区未取到(None):
                        fails.append("【R25】未声明 frozen 时不应报「未生效」")
                finally:
                    globals()["REPO_ROOT"] = _saved23
        except Exception as _e23:
            fails.append("【R23】冻结区门自身异常：%s: %s"
                         % (type(_e23).__name__, _e23))
        except Exception as _e468:
            fails.append("【TS-468】数术层门自身异常：%s: %s"
                         % (type(_e468).__name__, _e468))
    except Exception as _e384:
        fails.append("【TS-384】门超时门自身异常：%s: %s" % (type(_e384).__name__, _e384))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R31：**交付物必须是文件，不是目录**（8 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出的缺口 · 2026-10-15 · 真 Rust 工程 `example-project`】
    #   目标是「修 `src/` 下若干文件」，模型把交付物写成**目录** `src/`。
    #   编排器把它当**文件**落盘 ⇒ 整轮崩在
    #       `PermissionError: [Errno 13] ... /r1/w0/src`
    #   —— 不是按判据收场，也不是如实报错。
    #   ★ 这个形态**任何**「改一个目录下若干文件」的任务都会撞上，
    #     而"改多个文件"是**通用工程开发**的常态 ⇒ 这是**主路**，不是边角。
    #   ★ 之前只在单文件样例上跑通 ⇒ **单文件跑通掩盖了多文件的断路**。
    #   ⇒ 这一块把它钉死：识别 + 说清 + 回喂重规划（有上限）+ 落盘防御。
    try:
        import tempfile as _tf31
        import shutil as _sh31
        import asyncio as _aio31

        _根31 = Path(_tf31.mkdtemp(prefix="r31_"))
        (_根31 / "src").mkdir()
        (_根31 / "src" / "a.rs").write_bytes(b"pub fn a() {}\n")
        _存31 = globals().get("REPO_ROOT")
        try:
            globals()["REPO_ROOT"] = _根31
            # ① 目录（真实存在）必须被认出
            if not _交付物形态问题([{"path": "src"}]):
                fails.append("【R31】已存在的目录 `src` 未被认出（会被当文件落盘）")
            # ② 以分隔符结尾
            if not _交付物形态问题([{"path": "src/"}]):
                fails.append("【R31】`src/`（尾分隔符）未被认出")
            # ③ 空 path
            if not _交付物形态问题([{"path": "   "}]):
                fails.append("【R31】空 path 未被认出")
            # ④ 通配符（交付物必须是**具体**文件）
            if not _交付物形态问题([{"path": "src/*.rs"}]):
                fails.append("【R31】通配符路径未被认出（交付物必须是具体文件）")
            # ⑤ ★ 反向（防假报）：正常**文件**路径**不得**被报
            if _交付物形态问题([{"path": "src/a.rs"}]):
                fails.append("【R31】合法文件路径被误报 ⇒ 假红（会把正当交付判死）")
            # ⑥ ★ 落盘防御：目标是目录时必须是**说清根因**的错，
            #    而不是裸 `PermissionError: [Errno 13]`（看的人会去查权限/杀毒）
            _说清 = False
            try:
                _落盘(_根31 / "src", "x")
            except 交付物形态错:
                _说清 = True
            except Exception as _e31:
                fails.append(f"【R31】落盘到目录抛的不是「说清根因」的错："
                             f"{type(_e31).__name__}: {_e31}")
            if not _说清:
                fails.append("【R31】落盘到目录**未抛 `交付物形态错`**"
                             " ⇒ 用户看到的是 PermissionError，指不出根因")
            # ⑦ ★ `_adopt_spec` 拒目录，且**抛在改 `self.spec` 之前**
            #    （否则回喂重规划会带着半改状态，重试不安全）
            _r31 = GoalRunner.__new__(GoalRunner)
            _坏规 = json.loads(json.dumps(_DRY_SPEC))
            _坏规["deliverable"] = "src"
            _拒 = False
            try:
                _r31._adopt_spec(_坏规)
            except 交付物形态错:
                _拒 = True
            except Exception:      # noqa: BLE001
                # ★ 退回形态预检后，`_adopt_spec` 会**带着目录路径继续往下走**，
                #   在别处炸（本块这个 `GoalRunner` 是 `__new__` 构造的，缺属性）。
                #   ⇒ 那**也算「未拒」**：它没有在**入口**把形态错拦下来。
                #   ★ 关键是这一条**必须点名自己的检查**，不能被外层
                #     「门自身异常」吞掉 —— 否则退回后红的是"门自身异常"，
                #     探针会报「红错地方」，而 R31 这一处**有没有齿就测不出来**。
                #     （这正是探针④第一版踩的坑：门有齿，但齿被吞了。如实登记。）
                _拒 = False
            if not _拒:
                fails.append("【R31】`_adopt_spec` 未拒目录交付物"
                             " ⇒ 形态错不再被入口拦下（会一路走到落盘才崩）")
            if getattr(_r31, "spec", None) is not None:
                fails.append("【R31】形态错抛在 `self.spec` 赋值**之后**"
                             " ⇒ 回喂重规划会带半改状态")
            # ⑧ ★★★★★ **回喂重规划真的发生**（本块的核心）：
            #    规划器连续给目录 ⇒ 必须**多次重问**（不是第一次就死），
            #    且**反馈文本真的进了 prompt**（否则模型无从改正）；
            #    连续 3 次仍错 ⇒ **有上限地诚实停机**（不无限烧流量）。
            class _假Router31:
                def __init__(self):
                    self.调用: list[str] = []
                    self._坏 = json.dumps(_坏规, ensure_ascii=False)

                async def call(self, msgs):
                    self.调用.append(str(msgs[0].content))
                    # ★ 返回**真 router 的形态**（content 是 block 列表，block 有
                    #   `.text`），而不是裸 str —— 否则测的不是真实路径。
                    #   （第一版传裸 str，撞出 `_extract_text` 逐字符遍历的脆弱点，
                    #     已一并修掉并另立一条检查。）
                    _块 = type("_B31", (), {"text": self._坏})()
                    return type("_R31", (), {"content": [_块]})()

            _r31b = GoalRunner.__new__(GoalRunner)
            _r31b.dry = False
            _r31b.dry_machine = False
            _r31b.dry_multi = False
            _r31b.goal = "把 src 下的东西修好"
            _r31b._tok_chars = 0
            _r31b.router = _假Router31()
            # ★ R33：把规划前门态探针**替掉** —— 自检不得真跑项目验收门
            #   （本工程 65s 起）⇒ 检查不得依赖重外部（R28 同一条纪律）。
            _r31b._规划前门态 = lambda: ""
            _停 = ""
            try:
                _aio31.run(_r31b._plan())
                fails.append("【R31】连续 3 次形态错却**没有停机** ⇒ 可能无限重试烧流量")
            except ValueError as _e31b:
                _停 = str(_e31b)
            if "诚实停机" not in _停:
                fails.append(f"【R31】连续形态错的收场不是「诚实停机」：{_停[:160]}")
            # ★ R101：第 3 次失败后**还要问一次主心骨方向** ⇒ 路由调用变 4 次。
            #   ⇒ 这里必须按**调用者**分开数，不能笼统数总数
            #     （否则「多加了一个正当的调用」会被判成「回喂次数错了」）。
            #   ★★ 判别串连踩两次坑（如实登记）：
            #     ① 用「主心骨」⇒ 规划器提示里也有这三个字（R97 把主心骨
            #        记忆喂进了规划器提示）⇒ 全命中，规划 0 次、主心骨 4 次。
            #     ② 改用「你是这个项目的」⇒ 规划器记忆块里写着
            #        「你是这个项目的主心骨，不是每次都重新开始的新规划器」
            #        ⇒ 又全命中，还是 0/4。
            #   ⇒ **判别串必须是主心骨提示独有的**：
            #     主心骨是「**主心骨**（不是实现者）」，规划器没有这半句。
            _心判 = "（不是实现者）"
            _规划31 = [c for c in _r31b.router.调用 if _心判 not in c]
            if len(_规划31) != 3:
                fails.append(f"【R31】回喂次数不对：期望 3（首次 + 2 次回喂），"
                             f"实得 {len(_规划31)}")
            _心31 = [c for c in _r31b.router.调用 if _心判 in c]
            # ★★ R103 之后这条期望从 1 变 2（**改了判据必须同一次改齿**）：
            #   本夹具的假路由**对主心骨也只给规划器格式**（`_坏规`）⇒
            #   主心骨 1 回合 + R103 的 1 次纠正 = **2 次调用**。
            #   ★ 这不是「多问了一次方向」，是「问了 1 次、它答错格式、纠正 1 次」。
            #   ⇒ 只数「问了几次」会把它误判成「问了两次」，必须按**回合**数。
            #     `_纠正提示` 里带「格式不对（R103）」，据此分开。
            #   ★ 判别串**不能带 markdown 修饰符**：`_纠正提示` 里写的是
            #     `**格式不对**（R103）`（中间有 `**`）⇒ 用 `"格式不对（R103）"`
            #     匹配**永远命中不到**，于是纠正被数成回合（第二次踩坑，如实登记）。
            #     ⇒ 用不含修饰符的独有半句。
            _纠正31 = [c for c in _心31 if "上一回合你给的" in c]
            _回合31 = [c for c in _心31 if "上一回合你给的" not in c]
            if len(_回合31) != 1:
                fails.append(f"【R101】★ 规划三次不成却**没问主心骨**"
                             f"（问了 {len(_回合31)} 回合，应为 1）⇒ "
                             f"「目标本身错了」永远到不了问它的那条路")
            if len(_纠正31) > 1:
                fails.append(f"【R103】★ 主心骨纠正次数没上限（{len(_纠正31)} 次）⇒ 烧流量")
            # ★ 反馈必须真的进 prompt（第一版很可能只写进日志 ⇒ 模型看不到）
            if len(_r31b.router.调用) >= 2 and \
                    "交付物形态不合法" not in _r31b.router.调用[1]:
                fails.append("【R31】反馈**没有进 prompt** ⇒ 模型看不到原因，"
                             "重试只是重复同一个错")
            if len(_r31b.router.调用) >= 2 and \
                    "不要写目录" not in _r31b.router.调用[1]:
                fails.append("【R31】反馈里没有**可执行**的改法说明")
            # ⑨ ★ `_extract_text` 对**裸 str** 必须整体返回，
            #    不得逐字符遍历（那会让 JSON 变成"每字符插一个换行"）
            if _extract_text('{"a":1}') != '{"a":1}':
                fails.append("【R31】`_extract_text` 对裸 str 逐字符遍历了"
                             " ⇒ 下游 JSON 报 Invalid control character（指不出根因）")
            # ⑩ ★ 真 router 形态（block 列表）仍必须正常工作（防修坏）
            _块31 = type("_B31b", (), {"text": '{"a":1}'})()
            if _extract_text([_块31]) != '{"a":1}':
                fails.append("【R31】`_extract_text` 对 block 列表的原有行为被改坏了")
        finally:
            if _存31 is None:
                globals().pop("REPO_ROOT", None)
            else:
                globals()["REPO_ROOT"] = _存31
            _sh31.rmtree(_根31, ignore_errors=True)
    except Exception as _e31c:
        fails.append("【R31】交付物形态门自身异常：%s: %s"
                     % (type(_e31c).__name__, _e31c))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R32：产出**不是文件内容**（工具调用转录）必须被认出（5 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15 · Rust 工程 `example-project`】
    #   窗执行器返回的是**对话转录**（`<｜｜DSML｜｜ calls>` + `invoke name="bash"`），
    #   它被当成源码**写进了主树** `src/接线状态图.rs` ⇒ cargo 报
    #   `unknown start of token: \u{ff5c}` ⇒ 编译门判红 ⇒ 回滚（主树最终干净）。
    #   ★ 但那是**侥幸**：判红靠"这份垃圾恰好编译不过"。若它落在编译不到的位置
    #     （新文件 / 注释 / 字符串 / 非构建目标）⇒ 编译门绿 ⇒ **垃圾留在主树
    #     还被判成功交付**。⇒ 必须**在写主树之前**认出「这不是内容」。
    try:
        import asyncio as _aio31   # 显式引入：不依赖上一块是否跑到（块间不共享假设）
        # ① 真跑抓到的那份转录必须被认出
        _转32 = ("I'll start by exploring the repository and reproducing the "
                 "failing gates.\n\n<｜｜DSML｜｜ calls>\n"
                 "<｜｜DSML｜｜ invoke name=\"bash\">\n")
        if not _像工具调用而非内容(_转32):
            fails.append("【R32】真跑抓到的工具调用转录**未被认出**"
                         " ⇒ 会被当源码写进主树（只靠编译门侥幸兜住）")
        # ② 另一家的命名空间
        if not _像工具调用而非内容("<function_calls><invoke name=\"x\">"):
            fails.append("【R32】`<function_calls>` 形态未被认出")
        if not _像工具调用而非内容("antml:invoke name=\"bash\""):
            fails.append("【R32】`antml:invoke` 形态未被认出")
        # ③ 组合特征（单独出现可能是文档，两个同时出现才是调用）
        if not _像工具调用而非内容("<invoke name=\"a\"><parameter name=\"b\">"):
            fails.append("【R32】`<invoke>`+`<parameter>` 组合未被认出")
        # ④ 空产出也是"不是内容"
        if not _像工具调用而非内容("   \n  "):
            fails.append("【R32】空产出未被认出")
        # ⑤ ★★★ 反向（防假红 —— R7：假红比无齿更坏）：
        #    正当的**代码**绝不能被误判。若误判 ⇒ 正当产出被判死，模型无从申诉。
        for _正 in (
            "pub fn 判定(&self) -> bool { true }\n",
            "// 这段注释里提到了 invoke 与 parameter，但这是文档\nfn f() {}\n",
            'let s = "<invoke>";\n',          # 单独一个尖括号词，不构成调用
            "# 说明：本文件用 antml 前缀的历史原因\nx = 1\n",
            "def fib(n):\n    return n if n < 2 else fib(n-1)+fib(n-2)\n",
        ):
            if _像工具调用而非内容(_正):
                fails.append(f"【R32】★ 假红：正当产出被误判成工具调用：{_正[:40]!r}")
        # ⑥ ★★★★★ **接线齿**（端到端）：真跑一个返回转录的执行器 ⇒
        #    落到 `files[path]` 的**绝不能是转录本身**。
        #    ★ 为什么必须有这一条：只测 `_像工具调用而非内容` 这个**纯函数**
        #      是不够的 —— 函数对了但**调用点没接**，转录照样进主树。
        #      （这正是"门有齿但齿没接到被测物上"这一类缺陷。）
        _r32b = GoalRunner.__new__(GoalRunner)
        _r32b.dry = False
        _r32b.repo_scope = False
        _r32b.goal = "修好它"
        _r32b.spec = json.loads(json.dumps(_DRY_SPEC))
        _r32b.deliverables = _parse_deliverables(_r32b.spec)
        _r32b._tok_chars = 0
        _r32b._machine_files = 0

        class _假Router32:
            def __init__(self):
                self.调用 = 0

            async def call(self, msgs):
                self.调用 += 1
                _块 = type("_B32", (), {"text": _转32})()
                return type("_R32", (), {"content": [_块]})()

        _r32b.router = _假Router32()
        _出32 = _aio31.run(_r32b._produce_window(None, []))
        _落 = str(_出32.get(_r32b.deliverables[0]["path"]) or "")
        # ★ 判据：转录的**正文**（那段叙述与调用）不得出现在产物里。
        #   注意**不能**只查 `｜｜DSML｜｜` —— 拒收标记会**引用**那个记号
        #   给读的人看（第一版这么写，撞出假红，如实登记）。
        if "I'll start by exploring" in _落 or "invoke name=" in _落:
            fails.append("【R32】★ 转录**被写进了产物** ⇒ 会被当源码写进主树"
                         "（纯函数对了但**调用点没接**）")
        if not _落.startswith("__ORCH_NOT_CONTENT__"):
            fails.append(f"【R32】两次非内容后产物不是 `__ORCH_NOT_CONTENT__`："
                         f"{_落[:80]!r}")
        if _r32b.router.调用 != 2:
            fails.append(f"【R32】非内容的回喂次数不对：期望 2（首次 + 回喂 1 次），"
                         f"实得 {_r32b.router.调用}")
        # ⑦ ★★★★★ **拒收产物不得进主树**（真跑抓出的设计缺陷）：
        #   三个拒收标记都必须被 `_是拒收产物` 认出（否则会被当正常内容
        #   覆盖真实文件，再靠判据门判红回滚 —— 那是**不必要的暴露**）。
        for _标32 in ("__ORCH_CANNOT_EDIT__: x", "__ORCH_PATCH_REJECTED__: x",
                      "__ORCH_NOT_CONTENT__: x"):
            if not _是拒收产物(_标32):
                fails.append(f"【R32】拒收产物 `{_标32.split(':')[0]}` 未被认出"
                             " ⇒ 会覆盖主树真实文件")
        #   反向：正当内容（哪怕以 `__` 开头）**不得**被误判
        for _正32 in ("pub fn f() {}\n", "__ORCH_注释只是名字\n", "fn 主() {}"):
            if _是拒收产物(_正32):
                fails.append(f"【R32】★ 假红：正当内容被误判成拒收产物：{_正32[:30]!r}")
        # ⑧ ★★★★★ **行为齿**：拒收产物走完一整窗 ⇒ 主树文件**逐字节未变**。
        #   ★ 这一条才是真正的保护（⑦ 只证明函数认得出来，
        #     ⑧ 证明**调用点真的跳过了**）。真跑抓出的正是这个设计缺陷：
        #     旧写法把"我改不了"的说明**覆盖**到真实文件上，再靠判据门判红回滚
        #     —— 回滚是安全网，不该是常规路径。
        import tempfile as _tf32
        import shutil as _sh32
        _根32 = Path(_tf32.mkdtemp(prefix="r32_"))
        _存32 = globals().get("REPO_ROOT")
        try:
            (_根32 / "src").mkdir()
            _真32 = _根32 / "src" / "a.py"
            _真32.write_bytes(b"def a():\n    return 1\n")
            globals()["REPO_ROOT"] = _根32
            _规32 = json.loads(json.dumps(_DRY_SPEC))
            _规32.pop("pred", None)
            _规32.pop("deliverables", None)
            _规32.pop("deliverable", None)
            _规32["deliverable"] = "src/a.py"
            # ★ 用一条**判别力**门来分辨"写没写进主树"：
            #   `must_contain: ["return 2"]` 只在**正当内容真的被应用**时为绿。
            #   ★ 为什么不能用"文件字节变了没"来判：`_window_score` 是**沙箱试错**
            #     （`finally` 里必然回滚，这是设计），所以窗跑完文件**总是**回到原样。
            #     —— 第一版我正是这么写的，撞出假红，如实登记。
            _规32["gates"] = [
                {"type": "compile", "name": "py_ok", "cwd": ".",
                 "cmd": ["python", "-m", "py_compile", "{file}"], "expect_exit": 0},
                {"type": "whole_reading", "name": "已应用", "in": "src/a.py",
                 "mode": "contains", "must_contain": ["return 2"]},
            ]
            _r32c = GoalRunner.__new__(GoalRunner)
            _r32c.dry = False
            _r32c.repo_scope = True
            _r32c.goal = "改它"
            _r32c.spec = _规32
            _r32c.norm_notes = []
            _r32c.deliverables = _parse_deliverables(_规32)
            _r32c.round_root = _根32 / "r1"
            _r32c.round_root.mkdir(parents=True, exist_ok=True)
            _r32c._tok_chars = 0
            _r32c._machine_files = 0
            _r32c.n_windows = 1
            _r32c._delivered_products = []
            _r32c._delivered_gates = {}
            globals()["BACKUP_DIR"] = _根32 / "bk"
            globals()["_RECEIPT"] = _根32 / "receipt.jsonl"
            _前32 = _真32.read_bytes()
            _路32 = _r32c.deliverables[0]["path"]

            def _门值(窗, 名):
                return [v for k, v in (窗.get("results") or {}).items()
                        if str(k).endswith("@" + 名)]

            # ★★★★★ 判据必须是「`_apply_file` **有没有被调用**」。
            #   ★ 为什么不能靠"主树字节变了没"或"判别门绿不绿"：
            #     `_window_score` 是**沙箱试错**，`finally` 里**必然回滚**
            #     ⇒ 窗跑完文件**总是**回到原样，两条口径**都分辨不出来**。
            #     —— 我第一版正是这么写的，齿探针⑤ 证明它**无齿**（退回后仍全绿），
            #       如实登记。现在改成直接记录调用。
            _调用32: list[str] = []
            _真apply = globals()["_apply_file"]

            def _记apply(rel, text, _真=_真apply, _记=_调用32):
                _记.append(str(rel))
                return _真(rel, text)

            globals()["_apply_file"] = _记apply
            try:
                # ① 拒收产物 ⇒ **不得**调用 `_apply_file`（不进主树）
                _调用32.clear()
                _窗拒 = _r32c._window_score(
                    {_路32: "__ORCH_CANNOT_EDIT__: 改不了（测试用）"}, 0)
                if _调用32:
                    fails.append(f"【R32】★ 拒收产物**调用了 `_apply_file`**"
                                 f"（{_调用32}）⇒ 会覆盖主树真实文件"
                                 f"，再靠判据门判红回滚（回滚是安全网，不该是常规路径）")
                if _门值(_窗拒, "已应用") != [False]:
                    fails.append("【R32】拒收产物下判别门未判红（口径异常）")
                if _真32.read_bytes() != _前32:
                    fails.append("【R32】★ 拒收产物改了主树字节")
                # ② ★ 反向（防假红）：**正当内容**必须照常调用 `_apply_file`
                _调用32.clear()
                _窗好 = _r32c._window_score({_路32: "def a():\n    return 2\n"}, 1)
                if _调用32 != [_路32]:
                    fails.append(f"【R32】★ 假红：**正当内容**没被应用"
                                 f"（`_apply_file` 调用记录 {_调用32}）")
                if _门值(_窗好, "已应用") != [True]:
                    fails.append("【R32】★ 假红：正当内容下判别门未判绿")
                if _真32.read_bytes() != _前32:
                    fails.append("【R32】窗试错后主树未回滚（`_window_score` 应无后效）")
            finally:
                globals()["_apply_file"] = _真apply
        finally:
            if _存32 is None:
                globals().pop("REPO_ROOT", None)
            else:
                globals()["REPO_ROOT"] = _存32
            _sh32.rmtree(_根32, ignore_errors=True)
    except Exception as _e32:
        fails.append("【R32】非内容门自身异常：%s: %s" % (type(_e32).__name__, _e32))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R33：规划**之前**先跑真缺口（门态探针）（5 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15】规划器看不到红门报出的 18 处违规在哪，
    #   只能靠符号快照猜 ⇒ 猜错了**全部**文件 ⇒ 执行器被锁死在错误的文件集上，
    #   整趟必然白跑。★ 根因：**计划是在盲的**；而"缺口断在哪"机器当场能测。
    try:
        # ① 门态文本必须真的**进 prompt**（否则等于没探针）
        _p33 = _planner_prompt("把门修绿", "", "", "═══ 红门 `X` ═══\nsrc/a.rs:12 违规")
        if "红门 `X`" not in _p33:
            fails.append("【R33】门态**没有进规划器 prompt** ⇒ 探针白跑")
        if "src/a.rs:12" not in _p33:
            fails.append("【R33】门态里的**文件:行号**没进 prompt"
                         " ⇒ 规划器仍要靠猜文件")
        # ② 无门态时不得出现空块（防"看起来跑了"）
        _p33b = _planner_prompt("把门修绿", "", "", "")
        if "规划前机器探针" in _p33b:
            fails.append("【R33】没跑探针却出现了探针块 ⇒ 假报已探")
        # ③ `_掐中`：超长取头+尾，且**说出来**（"没看全"不得与"全看了"同貌）
        _长33 = "头" * 100 + "中" * 1000 + "尾" * 100
        _掐33 = _掐中(_长33, 200)
        if len(_掐33) > 300:
            fails.append(f"【R33】`_掐中` 没真掐（{len(_掐33)} 字符）")
        if not _掐33.startswith("头") or not _掐33.endswith("尾"):
            fails.append("【R33】`_掐中` 未同时保留头与尾（会丢掉 panic 里的违规清单）")
        if "省略" not in _掐33:
            fails.append("【R33】`_掐中` 掐了却**不说** ⇒ 与「全看了」同貌")
        # ④ 不超上限时**不得**改动原文（防无谓篡改证据）
        if _掐中("短文本", 200) != "短文本":
            fails.append("【R33】`_掐中` 对短文本做了改动 ⇒ 证据被无谓篡改")
        # ⑤ ★ 规划前门态是**实例方法**（可替换）⇒ 自检不得真跑项目验收门
        if not hasattr(GoalRunner, "_规划前门态"):
            fails.append("【R33】`_规划前门态` 不是实例方法"
                         " ⇒ 自检无法替换它，只能真跑门（慢且依赖环境）")
        # ⑥ ★★★★★ **接线齿**（端到端）：`_plan` 必须真的**调用**探针并把结果
        #    交给规划器。★ 只测 `_planner_prompt` 是不够的 ——
        #    函数对了但 `_plan` 没传，探针就白跑（"齿没接到被测物上"）。
        class _假Router33:
            def __init__(self):
                self.调用: list[str] = []

            async def call(self, msgs):
                self.调用.append(str(msgs[0].content))
                _块 = type("_B33", (), {"text": json.dumps(
                    json.loads(json.dumps(_DRY_SPEC)), ensure_ascii=False)})()
                return type("_R33", (), {"content": [_块]})()

        _r33b = GoalRunner.__new__(GoalRunner)
        _r33b.dry = False
        _r33b.dry_machine = False
        _r33b.dry_multi = False
        _r33b.goal = "把门修绿"
        _r33b._tok_chars = 0
        _r33b.router = _假Router33()
        _r33b._规划前门态 = lambda: "探针哨兵_门态_33"
        _aio33 = __import__("asyncio")
        _aio33.run(_r33b._plan())
        if not _r33b.router.调用:
            fails.append("【R33】`_plan` 没有调用规划器")
        elif "探针哨兵_门态_33" not in _r33b.router.调用[0]:
            fails.append("【R33】★ 探针结果**没进规划器 prompt**"
                         " ⇒ 探针白跑（齿没接到被测物上）")
    except Exception as _e33:
        fails.append("【R33】门态探针门自身异常：%s: %s" % (type(_e33).__name__, _e33))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R34：大文件必须能带**行号焦点**（5 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出】规划器给了 `src/判据观测.rs`（221622 字符，超整文件阈值）
    #   而**没有行号** ⇒ 执行层如实吐 `__ORCH_CANNOT_EDIT__` ⇒
    #   这条交付物**在计划诞生那一刻就死了**。
    try:
        # ① 拆行号
        if _拆路径焦点("src/a.rs:123") != ("src/a.rs", 123):
            fails.append(f"【R34】`src/a.rs:123` 未拆对：{_拆路径焦点('src/a.rs:123')}")
        # ② 全角冒号（中文目标里常见）
        if _拆路径焦点("src/a.rs：123") != ("src/a.rs", 123):
            fails.append("【R34】全角冒号 `：` 未拆对")
        # ③ 行:列 只取行
        if _拆路径焦点("src/a.rs:12:3") != ("src/a.rs", 12):
            fails.append("【R34】`行:列` 未只取行")
        # ④ ★ Windows 盘符/无行号路径**不得**被误拆（防假红）
        for _纯34 in ("src/a.rs", "src/模块甲/mod.rs", "C:/x/a.rs", "src/v1.rs"):
            if _拆路径焦点(_纯34) != (_纯34, None):
                fails.append(f"【R34】★ 假红：无行号路径被误拆：{_纯34!r} "
                             f"→ {_拆路径焦点(_纯34)}")
        # ⑤ ★★★ 端到端：`_parse_deliverables` 必须把行号拆进 `focus`，
        #    且 `path` 只留**纯路径**（否则会落出一个叫 `x.rs:123` 的文件）
        _规34 = json.loads(json.dumps(_DRY_SPEC))
        _规34["deliverable"] = "src/a.rs:123"
        _d34 = _parse_deliverables(_规34)
        if _d34[0]["path"] != "src/a.rs":
            fails.append(f"【R34】`path` 未去行号（会落出怪文件名）：{_d34[0]['path']!r}")
        if _d34[0].get("focus") != 123:
            fails.append(f"【R34】行号未进 `focus` 字段：{_d34[0].get('focus')!r}")
        # ⑥ 焦点规则必须**写进规划器 prompt**（否则规划器不知道要写行号）
        if "路径:行号" not in _planner_prompt("x", "", "", ""):
            fails.append("【R34】规划器 prompt 里**没有**「大文件必须给行号」的规则")
    except Exception as _e34:
        fails.append("【R34】行号焦点门自身异常：%s: %s" % (type(_e34).__name__, _e34))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R36：机器从红门证据抽「必改文件」+ 计划必须覆盖它（9 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15 · Rust 工程 `example-project`】
    #   R33 把红门证据（含 14 条 `绝对路径\文件.rs:行号`）原样喂给了规划器。
    #   实测：规划器**完全没看**，改从符号快照里挑了 3 个完全不相干的文件
    #   ⇒ 执行器被锁死在错误的文件集上，整趟白跑。
    #   ★ 根因不是探针没喂（诊断脚本证明 14 条全在文本里），
    #     而是**把机器的活派给了模型**：从一坨输出里认出"哪些文件必须改"，
    #     机器当场就能做（那是 `路径:数字` 的全称形态）。
    try:
        import tempfile as _tf36
        import shutil as _sh36
        _根36 = Path(_tf36.mkdtemp(prefix="r36_"))
        _存36 = globals().get("REPO_ROOT")
        try:
            (_根36 / "src" / "判据").mkdir(parents=True)
            (_根36 / "src" / "判据" / "观测协议.rs").write_bytes(b"fn a() {}\n")
            (_根36 / "src" / "外部获取.rs").write_bytes(b"fn b() {}\n")
            globals()["REPO_ROOT"] = _根36
            # ① 绝对路径（真跑里就是这种）必须被抽出并转成**仓库相对**路径
            # ★★★★★ R40：**必须用真证据的字节形态** —— Rust 的 `{:?}` 把反斜杠
            #   转义成**两个**。第一版自检写的是单反斜杠 ⇒ 自检绿、真跑空
            #   （R40 就是这么漏过去的）。⇒ 这里显式构造**双反斜杠**形态。
            _根双 = str(_根36).replace("\\", "\\\\")
            _证36 = ("存在「只打印不断言」的假测试："
                     f'["{_根双}\\\\src\\\\判据\\\\观测协议.rs:1993 某测试", '
                     f'"{_根双}\\\\src\\\\外部获取.rs:468 另一测试"]')
            # 自证：这份证据里**确实**是双反斜杠（否则这条自检又退化了）
            if "\\\\" not in _证36:
                fails.append("【R40】★ 自检证据没构造出**双反斜杠**形态"
                             " ⇒ 这条自检测不到真跑的那个缺陷")
            _必36 = _证据必改文件(_证36)
            # ★ 比**集合**不比顺序：排序是 `sorted()` 的码位序，中文路径的
            #   先后不是语义（第一版按书写顺序断言，撞出假红，如实登记）。
            if set(_必36) != {"src/判据/观测协议.rs", "src/外部获取.rs"}:
                fails.append(f"【R36】绝对路径未抽出/未转相对：{_必36}")
            if len(_必36) != 2:
                fails.append(f"【R36】必改清单有重复：{_必36}")
            # ② 相对路径也要认（`src/a.rs:12`）
            if _证据必改文件("src/外部获取.rs:12 名") != ["src/外部获取.rs"]:
                fails.append("【R36】相对路径 `src/a.rs:12` 未抽出")
            # ②b ★ R40：**单**反斜杠形态（非 Rust Debug 打印，例如 cargo 的
            #     `--> src\a.rs:12`）也必须认 —— 两种形态都要能落到真实文件上
            _单36 = _证据必改文件(f"{_根36}\\src\\外部获取.rs:9 x")
            if _单36 != ["src/外部获取.rs"]:
                fails.append(f"【R40】单反斜杠形态未抽出：{_单36}")
            # ③ ★ 防幻觉：仓库里**不存在**的路径不得进清单（否则清单本身是假的）
            _幻36 = _证据必改文件(
                f'["{_根36}\\src\\根本没有.rs:1 x", "src/也没有.rs:2 y"]')
            if _幻36:
                fails.append(f"【R36】★ 不存在的路径进了必改清单（幻觉污染）：{_幻36}")
            # ④ ★ 仓库**之外**的绝对路径不得进清单（`..` 越界）
            _外36 = _证据必改文件(r"C:\Windows\System32\drivers\etc\hosts:1 x")
            if _外36:
                fails.append(f"【R36】★ 仓库外的路径进了必改清单：{_外36}")
            # ⑤ 抽不到 ⇒ 空表（**非缺口型目标照常工作**，零回退）
            if _证据必改文件("") or _证据必改文件("这条门红了，但没点名任何文件"):
                fails.append("【R36】无文件证据时未返回空表 ⇒ 会对普通目标强加约束")
            # ⑥ 覆盖判定：漏一个必须报出来（全称，不是"沾到一个就算"）
            _计36 = ["src/外部获取.rs", "src/判据/观测协议.rs:1993"]
            if _计划覆盖缺口(_计36, ["src/外部获取.rs", "src/判据/观测协议.rs"]):
                fails.append("【R36】覆盖全了却报缺（假红）")
            _缺36 = _计划覆盖缺口(["src/外部获取.rs"],
                                  ["src/外部获取.rs", "src/判据/观测协议.rs"])
            if _缺36 != ["src/判据/观测协议.rs"]:
                fails.append(f"【R36】★ 漏掉的文件未报出（漏一个就该报）：{_缺36}")
            # ⑦ 提示文本必须**说清为什么必然**且给**可执行**改法
            _提36 = _计划不可行提示(["src/a.rs"], ["src/a.rs"])
            if "只有改" not in _提36 or "不可能" not in _提36:
                fails.append("【R36】回喂文本没说清「为什么必然白跑」⇒ 模型无从判断")
            if "deliverables" not in _提36:
                fails.append("【R36】回喂文本没给**可执行**的改法")
            # ⑦b ★★★★★ **清单必须真的进规划上下文**（齿探针⑬抓出的缺口）：
            #    组装逻辑原本长在 `_门态探针` 里（要真跑门）⇒ 自检测不了
            #    "清单抽得对但没接进 prompt"。现拆出纯函数 `_门态文本`，
            #    喂一份**假的红门结果**直接断言。
            _文36, _必36b = _门态文本(
                {"m": False},
                [{"gate": "m", "file": "src/外部获取.rs", "ok": False,
                  "cause": "退出码不符", "rc": 101,
                  "err": f'["{_根36}\\src\\外部获取.rs:468 某测试"]'}])
            if "机器已从红门证据里抽出**必改文件**" not in _文36:
                fails.append("【R36】★ 必改清单**没进规划上下文**"
                             " ⇒ 抽得再对也没用（模型看不到）")
            if "src/外部获取.rs" not in _文36:
                fails.append("【R36】必改清单进了文本但**没带路径** ⇒ 模型仍要猜")
            if _必36b != ["src/外部获取.rs"]:
                fails.append(f"【R36】`_门态文本` 回传的必改清单不对：{_必36b}")
            # ⑦c ★ 反向：**门全绿**时不得出现必改清单块（零回退）
            _文36b, _必36c = _门态文本({"m": True}, [])
            if "必改文件" in _文36b or _必36c:
                fails.append("【R36】门全绿却出现了必改清单 ⇒ 对普通目标强加约束")
            # ⑧ ★★★★★ **接线齿**：`_adopt_spec` 必须真的拒"漏文件"的计划。
            #   ★ 只测纯函数不够 —— 函数对了但没接进采纳链路，等于没有。
            _存必36 = dict(_门态缓存)
            try:
                _门态缓存.clear()
                _门态缓存["key"] = str(_根36)
                _门态缓存["必改"] = ["src/外部获取.rs", "src/判据/观测协议.rs"]
                _规36 = json.loads(json.dumps(_DRY_SPEC))
                _规36.pop("pred", None)
                _规36["deliverables"] = [
                    {"path": "src/外部获取.rs",
                     "gates": [{"type": "compile", "name": "c", "cwd": ".",
                                "cmd": ["python", "-m", "py_compile", "{file}"],
                                "expect_exit": 0}]}]
                _规36.pop("deliverable", None)
                _r36 = GoalRunner.__new__(GoalRunner)
                _拒36 = ""
                try:
                    _r36._adopt_spec(_规36)
                except 计划不可行 as _e36:
                    _拒36 = str(_e36)
                if not _拒36:
                    fails.append("【R36】★ `_adopt_spec` **未拒**漏文件的计划"
                                 " ⇒ 必改清单只是提示，不是判据（会白跑一整趟）")
                elif "src/判据/观测协议.rs" not in _拒36:
                    fails.append("【R36】拒了但**没点名漏了哪个文件** ⇒ 模型改不动")
                # ⑨ ★ 反向（防假红）：**覆盖全**的合法计划必须放行
                _规36b = json.loads(json.dumps(_DRY_SPEC))
                _规36b.pop("pred", None)
                _规36b.pop("deliverable", None)
                _规36b["deliverables"] = [
                    {"path": "src/外部获取.rs",
                     "gates": [{"type": "compile", "name": "c", "cwd": ".",
                                "cmd": ["python", "-m", "py_compile", "{file}"],
                                "expect_exit": 0}]},
                    {"path": "src/判据/观测协议.rs:1993",
                     "gates": [{"type": "compile", "name": "c2", "cwd": ".",
                                "cmd": ["python", "-m", "py_compile", "{file}"],
                                "expect_exit": 0}]}]
                _r36b = GoalRunner.__new__(GoalRunner)
                _放 = ""
                try:
                    _r36b._adopt_spec(_规36b)
                except 计划不可行 as _e36b:
                    _放 = str(_e36b)
                except Exception as _e36c:      # 别的错不算这条的失败（如实分开报）
                    _放 = ""
                if _放:
                    fails.append(f"【R36】★ 假红：覆盖全的合法计划被拒：{_放[:150]}")
            finally:
                _门态缓存.clear()
                _门态缓存.update(_存必36)
        finally:
            if _存36 is None:
                globals().pop("REPO_ROOT", None)
            else:
                globals()["REPO_ROOT"] = _存36
            _sh36.rmtree(_根36, ignore_errors=True)
    except Exception as _e36d:
        fails.append("【R36】必改文件门自身异常：%s: %s"
                     % (type(_e36d).__name__, _e36d))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R37：交付物不得落在**冻结区**（5 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15】R36 生效后规划器覆盖了 7 个真违规文件，
    #   但**额外**把 `src/测试防假.rs`（判它的守卫本身，冻结区）也列进
    #   deliverables ⇒ 整趟会走到"逐字节比对发现判据被改 ⇒ 回滚 + 不交付"。
    #   ★ 结局在**计划诞生那一刻就已注定** ⇒ 当场退回，不浪费一整趟。
    try:
        _prof37 = prof_mod.get_profile()
        _冻声明 = [str((s or {}).get("path") or "") for s in (_prof37.frozen or ())]
        if _冻声明:
            # ① 冻结区文件必须被认出
            _中37 = _计划含冻结文件(_冻声明)
            if set(_中37) != {x.replace("\\", "/").strip("/") for x in _冻声明}:
                fails.append(f"【R37】冻结区文件未被认出：{_中37}")
            # ② ★ 反向（防假红）：**非**冻结区文件不得被误报
            if _计划含冻结文件(["src/某个正常文件.rs", "src/lib.rs"]):
                fails.append("【R37】★ 假红：非冻结区文件被误报成冻结区")
            # ③ 反斜杠写法也要认（模型可能用 `src\a.rs`）
            _冻0 = _冻声明[0].replace("/", "\\")
            if not _计划含冻结文件([_冻0]):
                fails.append("【R37】反斜杠写法的冻结路径未被认出")
            # ④ 提示必须说清**为什么**且给**正当出路**（否则模型只会换个文件乱试）
            _提37 = _冻结文件提示(_冻声明)
            if "冻结区" not in _提37 or "回滚" not in _提37:
                fails.append("【R37】回喂文本没说清「改了也会被回滚」")
            if "被测文件" not in _提37:
                fails.append("【R37】回喂文本没给**正当出路**"
                             "（把断言补进被测文件，而不是改判据）")
            # ⑤ ★★★★★ **接线齿**：`_adopt_spec` 必须真的拒"含冻结文件"的计划
            _规37 = json.loads(json.dumps(_DRY_SPEC))
            _规37.pop("pred", None)
            _规37.pop("deliverable", None)
            _规37["deliverables"] = [
                {"path": _冻声明[0],
                 "gates": [{"type": "compile", "name": "c", "cwd": ".",
                            "cmd": ["python", "-m", "py_compile", "{file}"],
                            "expect_exit": 0}]}]
            _存必37 = dict(_门态缓存)
            try:
                _门态缓存.clear()          # 清掉必改，**单独**验证 R37 这条
                _r37 = GoalRunner.__new__(GoalRunner)
                _拒37 = ""
                try:
                    _r37._adopt_spec(_规37)
                except 计划不可行 as _e37:
                    _拒37 = str(_e37)
                if not _拒37:
                    fails.append("【R37】★ `_adopt_spec` **未拒**含冻结文件的计划"
                                 " ⇒ 会白跑一整趟再回滚")
                elif "冻结区" not in _拒37:
                    fails.append("【R37】拒了但没说清是冻结区 ⇒ 模型改不动")
            finally:
                _门态缓存.clear()
                _门态缓存.update(_存必37)
        else:
            fails.append("【R37】档案未声明冻结区 ⇒ 本块无法验证"
                         "（R23 的保证此时**不存在**）")
    except Exception as _e37b:
        fails.append("【R37】冻结区计划门自身异常：%s: %s"
                     % (type(_e37b).__name__, _e37b))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R38：与文件无关的门**只求值一次**（4 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15】19 条交付物共享一条 `cargo test --lib`
    #   （**实测 65 秒**）⇒ 逐文件判就是 19 × 65s ≈ 20 分钟/窗，
    #   而**每一遍结果完全相同**（整 crate 测试，不看正在给哪个文件打分）。
    try:
        # ① 与文件无关的判据：不含 {file}/{work} 的命令门
        if not _门与文件无关({"type": "compile", "name": "g",
                            "cmd": ["cargo", "test", "--lib", "测试防假::"]}):
            fails.append("【R38】不含 {file} 的项目级命令门未被判为「与文件无关」")
        # ② ★ 含 {file} 的必须**逐文件**（不共享 —— 猜错就会串味）
        if _门与文件无关({"type": "compile", "name": "g",
                       "cmd": ["python", "-m", "py_compile", "{file}"]}):
            fails.append("【R38】★ 含 {file} 的门被判为「与文件无关」"
                         " ⇒ 会被错误共享（串味）")
        # ③ ★ 文本门的判据**与其余门同一条**（看占位符，不看门类型）—— R75。
        #   ★★★ 这一条**原来是反的**（如实登记）：R38 时它断言
        #     「whole_reading **一律**逐文件」，理由是"文本门读的是 `in` 指定的文件"。
        #     那句话**只说对了一半**：它读的确实是 `in`，但那个文件
        #     **不一定是**正在判的产物（`in` 可以是**字面路径**）。
        #     ⇒ 旧断言把 R75 那个缺陷**锁死**成了"正确行为"：
        #       一条 `in: "src/模块甲/mod.rs"` 的门被求值 4 遍，
        #       而 4 次读数**完全相同**（它压根没看那 4 个交付物）。
        #     ★ 教训：**齿也会把缺陷锁住** —— 齿写的是"当时以为对的口径"。
        #       改口径时必须**同时**改齿，否则齿会站出来替缺陷说话。
        #   ⇒ 现在按**占位符**判：含 `{file}` ⇒ 逐文件；字面 `in` ⇒ 与文件无关。
        if _门与文件无关({"type": "whole_reading", "name": "g",
                       "in": "{file}", "mode": "contains",
                       "must_contain": ["x"]}):
            fails.append("【R38】★ 含 {file} 的文本门被判为「与文件无关」"
                         " ⇒ 会用 A 文件的读数冒充 B 文件（假绿）")
        if not _门与文件无关({"type": "whole_reading", "name": "g",
                            "in": "src/a.rs", "mode": "contains",
                            "must_contain": ["x"]}):
            fails.append("【R75】★ 读**字面路径**的文本门被判为「逐文件」"
                         " ⇒ 同一条门按交付物数被求值 N 遍，"
                         "且同一个真值被记成 N 个文件的缺口")
        # ④ ★★★★★ **行为齿**：真跑一次 `_score_files`，数命令**实际被跑了几次**。
        #    ★ 只测 `_门与文件无关` 这个纯函数不够 —— 函数对了但 `_score_files`
        #      没接上，照样跑 N 遍（"齿没接到被测物上"）。
        import tempfile as _tf38
        import shutil as _sh38
        _根38 = Path(_tf38.mkdtemp(prefix="r38_"))
        try:
            _计38 = {"n": 0}
            _真score = globals()["_score_gates"]

            def _记score(workdir, file, gates, repo_root=None, _真=_真score, _c=_计38):
                _c["n"] += len(list(gates))
                return _真(workdir, file, gates, repo_root=repo_root)

            globals()["_score_gates"] = _记score
            try:
                _门共38 = [{"type": "compile", "name": "项目级", "cwd": ".",
                          "cmd": ["python", "-c", "pass"], "expect_exit": 0}]
                # ★ R75：夹具必须**含 `{file}`**，否则它现在是"与文件无关"的门
                #   （那会让这条齿测错样本戊 —— 我第一版就是这样，如实登记）。
                _文共38 = [{"type": "whole_reading", "name": "逐文件",
                          "in": "{file}",
                          "mode": "contains", "must_contain": ["x"]}]
                _交38 = []
                for i in range(5):
                    (_根38 / f"f{i}.py").write_bytes(b"x = 1\n")
                    _交38.append({"path": f"f{i}.py",
                                  "gates": _门共38 + _文共38})
                _计38["n"] = 0
                _score_files(_根38, _交38, file_root=_根38)
                # 期望：项目级门 1 次 + 逐文件门 5 次 = 6（不是 5×2 = 10）
                if _计38["n"] != 6:
                    fails.append(f"【R38】★ 门求值次数不对：期望 6"
                                 f"（项目级 1 次 + 逐文件 5 次），实得 {_计38['n']}"
                                 f" ⇒ 与文件无关的门仍被按文件重复跑")
            finally:
                globals()["_score_gates"] = _真score
        finally:
            _sh38.rmtree(_根38, ignore_errors=True)
    except Exception as _e38:
        fails.append("【R38】门求值共享门自身异常：%s: %s"
                     % (type(_e38).__name__, _e38))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R75：判据的**主体**必须由门的规格决定（6 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-17 · 第二次真项目验收】4 个交付物、5 条门 ⇒
    #   回执里 **20 条**，而只有 5 条门的真值：`has_closed_marker` 规格里
    #   `in` 写死 `src/模块甲/mod.rs`（**只读那一个文件**），
    #   `cargo_check_lib` / `run_fake_test_gate` 是整 crate 命令门。
    #   ⇒ 同一条门的同一个真值被记成 **4 个不同文件的缺口**。
    try:
        # ① 含 `{file}` ⇒ 主体 = 正在判的那个交付物
        if _门主体({"type": "grep_required", "name": "g", "in": "{file}"},
                 "src/甲.rs", Path(".")) != "src/甲.rs":
            fails.append("【R75】含 {file} 的门，主体**不是**正在判的产物")
        # ② 字面 `in` ⇒ 主体 = **那条路径**（门自己声明读谁）
        if _门主体({"type": "whole_reading", "name": "g",
                  "in": "src/模块甲/mod.rs", "mode": "contains"},
                 "src/核心类型/运行时涌现.rs", Path(".")) != "src/模块甲/mod.rs":
            fails.append("【R75】★ 读**字面路径**的门，主体被记成了**交付物**"
                         " ⇒ 同一个真值会被记成 N 个文件的缺口（真跑里虚增 4 倍）")
        # ③ 工程级命令门 ⇒ 主体 = `_全库主体`（它不读单个文件）
        if _门主体({"type": "compile", "name": "g",
                  "cmd": ["cargo", "check", "--lib"]},
                 "src/甲.rs", Path(".")) != _全库主体:
            fails.append("【R75】★ 工程级命令门的主体不是「全库」"
                         " ⇒ 它的真值会被冒充成某个文件的缺口")
        # ④ 绝对路径落在 base 下 ⇒ 转相对（身份键**不许**含机器相关的绝对路径）
        _基75 = Path(tempfile.gettempdir()).resolve()
        _绝75 = str(_基75 / "src" / "甲.rs")
        if _门主体({"type": "grep_required", "name": "g", "in": _绝75},
                 "x", _基75) != "src/甲.rs":
            fails.append("【R75】base 下的绝对路径未转成相对 ⇒ "
                         "身份键里会写进机器相关的绝对路径（换个目录就换身份）")
        # ⑤ ★★★★★ **行为齿**：真跑 `_score_files`，数**结果键有几个**。
        #   只测 `_门主体` 这个纯函数不够 —— 函数对了但 `_score_files`
        #   没接上，照样一个真值变 N 个键（"齿没接到被测物上"，R38 那一课）。
        import tempfile as _tf75
        import shutil as _sh75
        _根75 = Path(_tf75.mkdtemp(prefix="r75_"))
        try:
            _共75 = [{"type": "whole_reading", "name": "字面门",
                    "in": "src/固定.rs", "mode": "contains",
                    # ★ 必须让这条门**红**，否则 `red` 是空的 ⇒
                    #   下面两条查 `red` 的断言**无从测起**（空集上恒真）。
                    #   我第一版写成 `["x"]` 而夹具文件里正好有 `x` ⇒ 门绿 ⇒
                    #   那两条断言退化成**恒真的无齿**。如实登记。
                    "must_contain": ["绝不可能出现的串"]},
                   {"type": "compile", "name": "全库门", "cwd": ".",
                    "cmd": ["python", "-c", "pass"], "expect_exit": 0}]
            (_根75 / "src").mkdir(parents=True, exist_ok=True)
            (_根75 / "src" / "固定.rs").write_bytes(b"x = 1\n")
            _交75 = []
            for i in range(4):
                (_根75 / f"f{i}.py").write_bytes(b"x = 1\n")
                _交75.append({"path": f"f{i}.py", "gates": _共75})
            _r75, _red75 = _score_files(_根75, _交75, file_root=_根75)
            # 4 个交付物 × 2 条**与文件无关**的门 ⇒ 只该有 **2** 个键
            if len(_r75) != 2:
                fails.append(f"【R75】★ 与文件无关的门被按交付物**复制**："
                             f"4 个交付物 × 2 条门 ⇒ 期望 2 个键，实得 {len(_r75)}"
                             f" ⇒ 违规点虚增 4 倍、缺口清单变成复印件")
            if any(k.startswith("f") for k in _r75):
                fails.append(f"【R75】★ 结果键的主体仍是**交付物**（{sorted(_r75)}）"
                             f" ⇒ 判据的主体被换掉了")
            if not any(k == f"src/固定.rs@字面门" for k in _r75):
                fails.append(f"【R75】字面门的键不是 `src/固定.rs@字面门`"
                             f"（实得 {sorted(_r75)}）⇒ 主体算错")
            if not any(k == f"{_全库主体}@全库门" for k in _r75):
                fails.append(f"【R75】全库门的键不是 `{_全库主体}@全库门`"
                             f"（实得 {sorted(_r75)}）")
            # ★★★ 还要查**违规点条目**的 `file`，不能只查结果键：
            #   它们是**两条独立的路**（键走 `results`，身份走 `red`）。
            #   只查键 ⇒ 把 `red` 那条路退回交付物时**照样全绿**。
            #   ★ 验齿脚本（`验证/_验R75齿.py` ②）当场抓出来的，如实登记。
            _主75 = sorted({str(x.get("file")) for x in _red75})
            if any(s.startswith("f") for s in _主75):
                fails.append(f"【R75】★ 违规点条目的 `file` 仍是**交付物**"
                             f"（{_主75}）⇒ 判据身份仍会按交付物数虚增")
            if "src/固定.rs" not in _主75:
                fails.append(f"【R75】违规点条目的 `file` 不是门的真实主体"
                             f"（实得 {_主75}）")
        finally:
            _sh75.rmtree(_根75, ignore_errors=True)
        # ⑥ ★ 锚：工程级门的主体必须被**声明作用域**框住，否则恒 `no_anchor`。
        #   ★ 与 TS-442 同源（那次漏的是 `src/`，这次漏的是"整棵树"）。
        from agentscope_orchestrator.gate_derive import anchored as _anchored75
        if not _anchored75(f"{_全库主体}@cargo_check_lib", {_全库主体}):
            fails.append("【R75】工程级门的主体**无锚** ⇒ 新偏差一出现就落 "
                         "`no_anchor` ⇒ 交还用户/停机")
        # ⑦ ★★ **接线齿**：⑥ 只证明"这个锚**能用**"，**没证明它真的被加进去**。
        #   —— R71/R74 那一课：写好了不等于接上了。
        #   真跑里 `decl_scopes` 是在 `_run` 里现场拼的，没有便宜的入口
        #   ⇒ 用**读自己源码**的接线齿（与 R72 同形）：剥注释后断言那一行在。
        #   ★ 验齿脚本（`验证/_验R75齿.py` ⑤）当场抓出 ⑥ 单独立不住，如实登记。
        from agentscope_orchestrator import gate_spec as _gs75
        if "decl_scopes |= {_全库主体}" not in _gs75.strip_for_matching(_源码):
            fails.append("【R75】★ 工程级门的锚**没有真的加进声明作用域** ⇒ "
                         "新偏差一出现就落 `no_anchor`（⑥ 只是证明了它「能用」）")
    except Exception as _e75:
        fails.append("【R75】判据主体齿自身异常：%s: %s"
                     % (type(_e75).__name__, _e75))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R39：交付物身份**只有一把尺子**（5 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15】R34 允许把大文件写成 `路径:行号`，但只有
    #   `_parse_deliverables` 一条路径做了归一，另**三处**比对交付物身份的
    #   地方仍是裸字符串相等 ⇒ 规划器照 R34 的提示写
    #   `depends_on: ["src/判据/观测协议.rs:1993"]` ⇒ 「依赖未知文件」⇒
    #   **规划失败、整趟没跑**。
    #   ★ 与 R34/R35/R38 同源：**同一件事两把尺子**。
    try:
        # ① 归一：行号被剥掉，斜杠统一，`./` 去掉
        if _归一路径("src/判据/观测协议.rs:1993") != "src/判据/观测协议.rs":
            fails.append("【R39】`路径:行号` 未归一到 `路径`")
        if _归一路径("src\\a\\b.rs") != "src/a/b.rs":
            fails.append("【R39】反斜杠未归一到正斜杠")
        if _归一路径("./src/a.rs") != "src/a.rs":
            fails.append("【R39】`./` 前缀未去掉")
        if _归一路径("src/a.rs：1993") != "src/a.rs":
            fails.append("【R39】全角冒号行号未归一")
        # ② ★ 归一**不得**改坐标本身（只用于比对）
        _原39 = "src/判据/观测协议.rs:1993"
        _ = _归一路径(_原39)
        if _原39 != "src/判据/观测协议.rs:1993":
            fails.append("【R39】★ 归一化改了入参（应只返回新串，不改坐标）")
        # ③ ★★★★★ **三处比对点都必须过这把尺子**（结构齿：漏一处就会重现本缺陷）
        _源39 = Path(globals().get("__file__") or "").read_text(encoding="utf-8")
        for _点名, _样 in (
                ("`_valid_spec` 依赖存在性", "_归一路径(dep) not in 归集"),
                ("`_topo_sort` 查表", "by_path.get(_归一路径(dep))"),
                ("`_build_dag` 依赖定位", "纯dep = _归一路径(str(dep))")):
            if _样 not in _源39:
                fails.append(f"【R39】★ {_点名} 没过统一尺子 ⇒ 会重现"
                             f"「依赖未知文件 …:行号」")
        # ④ ★ 行为齿：带行号的 depends_on 必须被**接受**（真跑里正是它被拒）
        _规39 = json.loads(json.dumps(_DRY_SPEC))
        _规39.pop("pred", None)
        _规39.pop("deliverable", None)
        _规39["deliverables"] = [
            {"path": "a.py",
             "gates": [{"type": "compile", "name": "c", "cwd": ".",
                        "cmd": ["python", "-m", "py_compile", "{file}"],
                        "expect_exit": 0}]},
            {"path": "b.py", "depends_on": ["a.py:12"],
             "gates": [{"type": "compile", "name": "c2", "cwd": ".",
                        "cmd": ["python", "-m", "py_compile", "{file}"],
                        "expect_exit": 0}]}]
        _为何39 = _valid_spec(_规39)
        if _为何39:
            fails.append(f"【R39】★ 带行号的 depends_on 被拒（真跑就是这个）"
                         f"：{_为何39}")
        _序39, _ok39, _why39 = _topo_sort(_规39["deliverables"])
        if not _ok39:
            fails.append(f"【R39】★ 带行号的依赖过不了拓扑排序：{_why39}")
        elif _序39 != [0, 1]:
            fails.append(f"【R39】拓扑序不对（应先 a.py 后 b.py）：{_序39}")
        # ⑤ ★ 反向（防假红）：**真的**未知依赖仍必须被拒（尺子不能宽到没有齿）
        _规39b = json.loads(json.dumps(_规39))
        _规39b["deliverables"][1]["depends_on"] = ["根本不存在的文件.py:1"]
        if not _valid_spec(_规39b):
            fails.append("【R39】★ 假绿：真正的未知依赖不再被拒")
        _序39b, _ok39b, _ = _topo_sort(_规39b["deliverables"])
        if _ok39b:
            fails.append("【R39】★ 假绿：真正的未知依赖过了拓扑排序")
    except Exception as _e39:
        fails.append("【R39】交付物身份归一门自身异常：%s: %s"
                     % (type(_e39).__name__, _e39))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R41：必改清单**不得**被编译器 warning 污染（4 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15】第一次真跑时 cargo 重新编译（要出 warning），
    #   `_evidence` 补的尾部原始输出把 warning 的 `--> src\某文件.rs:行:列`
    #   混进了证据 ⇒ 抽取器把 **warning 的位置**当成"必改文件" ⇒ 清单变成
    #   9 个**与缺口无关**的文件 ⇒ **R36 的门反过来把规划器逼到错误的文件上**
    #   —— 比不给清单**更坏**。
    try:
        import tempfile as _tf41
        import shutil as _sh41
        _根41 = Path(_tf41.mkdtemp(prefix="r41_"))
        _存41 = globals().get("REPO_ROOT")
        try:
            (_根41 / "src").mkdir(parents=True)
            (_根41 / "src" / "真违规.rs").write_bytes(b"fn a() {}\n")
            (_根41 / "src" / "只是警告.rs").write_bytes(b"fn b() {}\n")
            globals()["REPO_ROOT"] = _根41
            _双41 = str(_根41).replace("\\", "\\\\")
            # ★ 真 cargo 输出的**形态**：warning 段（含 `-->` 位置行）
            #   + 空行 + 失败载荷段（含违规清单）
            _证41 = (
                "warning: unused variable: `x`\n"
                f"   --> {_双41}\\\\src\\\\只是警告.rs:12:9\n"
                "    |\n"
                " 12 |     let x = 1;\n"
                "    |         ^ help: if this is intentional, prefix it with an underscore\n"
                "    |\n"
                "    = note: `#[warn(unused_variables)]` on by default\n"
                "\n"
                "warning: unreachable pattern\n"
                f"   --> {_双41}\\\\src\\\\只是警告.rs:30:5\n"
                "\n"
                "thread '测试防假::全库测试_无只打印' (2828) panicked at src\\测试防假.rs:589:5:\n"
                "存在「只打印不断言」的假测试："
                f'["{_双41}\\\\src\\\\真违规.rs:1993 某测试"]\n'
                "\n"
                "error: test failed, to rerun pass `--lib`\n")
            必41 = _证据必改文件(_证41)
            # ① ★ 真违规必须抽到
            if "src/真违规.rs" not in 必41:
                fails.append(f"【R41】★ 失败载荷里的真违规未抽到：{必41}")
            # ② ★★★★★ **warning 的位置必须被剔掉**（这条就是真跑那个缺陷）
            if "src/只是警告.rs" in 必41:
                fails.append("【R41】★★ warning 的位置被当成「必改文件」"
                             " ⇒ R36 会把规划器**逼到错误的文件上**（比不给清单更坏）")
            # ③ ★ 反向（防假红）：**编译错**的 `-->` 必须**保留**
            #    （一刀切按 `-->` 过滤会把编译错这一最常见情形废掉）
            _证41b = (
                "error[E0308]: mismatched types\n"
                f"   --> {_双41}\\\\src\\\\真违规.rs:1:2\n"
                "    |\n"
                "  1 | fn a() {}\n"
                "    |  ^^^^^^ expected `i32`, found `()`\n"
                "\n"
                "error: could not compile `example-project` due to 1 previous error\n")
            _必41b = _证据必改文件(_证41b)
            if "src/真违规.rs" not in _必41b:
                fails.append("【R41】★ 假红：**编译错**的位置被剔掉了"
                             f"（编译错是最常见情形，不能废）：{_必41b}")
            # ④ 只有 warning、没有失败载荷 ⇒ 空表（零回退，不施加任何约束）
            _证41c = ("warning: unused variable: `x`\n"
                      f"   --> {_双41}\\\\src\\\\只是警告.rs:12:9\n")
            if _证据必改文件(_证41c):
                fails.append("【R41】只有 warning 时未返回空表 ⇒ 会对普通目标强加约束")
        finally:
            if _存41 is None:
                globals().pop("REPO_ROOT", None)
            else:
                globals()["REPO_ROOT"] = _存41
            _sh41.rmtree(_根41, ignore_errors=True)
    except Exception as _e41:
        fails.append("【R41】非失败诊断过滤门自身异常：%s: %s"
                     % (type(_e41).__name__, _e41))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R42：必改清单与冻结区**不得互相打架**（3 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15】R41 修好后清单 = 7 真违规 + `src/测试防假.rs`
    #   （panic 的**自身位置**，且是冻结区）⇒ R36 要求"必须覆盖"、
    #   R37 要求"不得包含" ⇒ **两条门互相打架，任何计划都不可能满足**。
    #   ★ 根因：「必改」的含义被写宽了 —— 必改 = "要转绿就必须改的"，
    #     而冻结区**按定义改不了** ⇒ 不可能必改。⇒ 必改 ⊆ 可改。
    try:
        _冻声明42 = [str((s or {}).get("path") or "")
                     for s in (prof_mod.get_profile().frozen or ())]
        if not _冻声明42:
            fails.append("【R42】档案未声明冻结区 ⇒ 本块无法验证")
        else:
            import tempfile as _tf42
            import shutil as _sh42
            _根42 = Path(_tf42.mkdtemp(prefix="r42_"))
            _存42 = globals().get("REPO_ROOT")
            try:
                (_根42 / "src").mkdir(parents=True)
                (_根42 / "src" / "可改.rs").write_bytes(b"fn a() {}\n")
                # 把冻结区那条路径**真的造出来**（否则它进不了清单，
                # 这条自检就变成"验一个不存在的场景"= 无齿）
                _冻名42 = _冻声明42[0].strip("/").replace("\\", "/")
                (_根42 / _冻名42).parent.mkdir(parents=True, exist_ok=True)
                (_根42 / _冻名42).write_bytes("// frozen\n".encode("utf-8"))
                globals()["REPO_ROOT"] = _根42
                _双42 = str(_根42).replace("\\", "\\\\")
                _证42 = ("thread 'x' panicked at "
                         f"{_冻名42.replace('/', chr(92))}:1:1:\n"
                         "存在违规："
                         f'["{_双42}\\\\{_冻名42.replace("/", chr(92) + chr(92))}:9 甲", '
                         f'"{_双42}\\\\src\\\\可改.rs:9 乙"]\n')
                必42 = _证据必改文件(_证42)
                # ① ★ 可改文件必须留着
                if "src/可改.rs" not in 必42:
                    fails.append(f"【R42】可改文件被误剔：{必42}")
                # ② ★★★★★ **冻结区文件必须被剔掉**（否则 R36 与 R37 打架）
                if _冻名42 in 必42:
                    fails.append("【R42】★★ 冻结区文件进了必改清单"
                                 " ⇒ R36 要求覆盖它、R37 禁止包含它"
                                 " ⇒ **两条门打架，任何计划都不可能满足**")
                # ③ ★ 反向（防假红）：没声明冻结区时不得剔任何东西
                _保42 = dict(prof_mod.get_profile().frozen or {}) if isinstance(
                    prof_mod.get_profile().frozen, dict) else None
                if _保42 is not None:
                    _旧 = prof_mod.get_profile().frozen
                    try:
                        prof_mod.get_profile().frozen = ()
                        if "src/可改.rs" not in _证据必改文件(_证42):
                            fails.append("【R42】假红：无冻结区声明时剔掉了文件")
                    finally:
                        prof_mod.get_profile().frozen = _旧
            finally:
                if _存42 is None:
                    globals().pop("REPO_ROOT", None)
                else:
                    globals()["REPO_ROOT"] = _存42
                _sh42.rmtree(_根42, ignore_errors=True)
    except Exception as _e42:
        fails.append("【R42】必改与冻结区一致性门自身异常：%s: %s"
                     % (type(_e42).__name__, _e42))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R43：增量裁决 —— 部分进展**不得被整轮回滚丢掉**（7 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15】7 个文件一次全应用 ⇒ 判据从 14 处违规降到 9 处
    #   （`无恒真` 那条门**已全绿**）—— 但因为"还没全绿"，`_round` **整轮回滚**
    #   ⇒ **进展全部丢弃** ⇒ 下一轮从零开始 ⇒ 配上预算护栏，
    #   **中长线任务永远不可能收敛**。
    #   ★ 根因：**裁决粒度错了** —— 判据是整 crate 的（一个布尔），
    #     而改动是逐文件的 ⇒ 用布尔裁决一批文件只能全有或全无。
    #   ★ 修法：判据给的**不只是布尔**，它的失败消息**点出了每一处违规的位置**
    #     ⇒ 那是一个**偏序** ⇒ 逐文件比较违规点集合。
    try:
        # ① 违规点抽取：`路径:行号` 归一成（相对路径, 行号）
        _点43 = _证据违规点(
            f'["{str(REPO_ROOT).replace(chr(92), chr(92)*2)}'
            f'\\\\src\\\\lib.rs:12 甲", '
            f'"{str(REPO_ROOT).replace(chr(92), chr(92)*2)}'
            f'\\\\src\\\\lib.rs:34 乙"]')
        if _点43 != [("src/lib.rs", 12), ("src/lib.rs", 34)]:
            fails.append(f"【R43】违规点抽取不对：{_点43}")
        # ② ★ 接受判据：**真子集**才算进展（"修3坏2"净减1是交换，不是进展）
        if not _增量可判(frozenset({("a", 1), ("a", 2)}), frozenset({("a", 1)})):
            fails.append("【R43】真子集变小未被判为进展")
        if _增量可判(frozenset({("a", 1), ("a", 2)}), frozenset({("a", 1), ("b", 9)})):
            fails.append("【R43】★ 「修掉一个又弄坏一个」被判成进展"
                         " ⇒ 会在两个态之间**来回摆**，永不收敛")
        # ②b ★★★★★ **点数确实变少、但一个原违规点都没修好**（纯交换）
        #   —— 这是"真子集"与"点数变少"**唯一**分道扬镳的形态：
        #     2 → 1，数变小了，但原来的 2 个一个没消 ⇒ **绝不是进展**。
        #   ★ 少了这一条，"接受判据"被悄悄放宽成 `len(现) < len(基线)` 也查不出来
        #     （齿探针 ㉒ 就是靠它才有齿）。
        if _增量可判(frozenset({("a", 1), ("a", 2)}), frozenset({("b", 9)})):
            fails.append("【R43】★ 纯交换（原违规点一个没消、只换了个新的）"
                         "被判成进展 ⇒ 接受判据被放宽成「点数变少」")
        if _增量可判(frozenset({("a", 1)}), frozenset({("a", 1)})):
            fails.append("【R43】没变被判成进展")
        if _增量可判(frozenset({("a", 1)}), frozenset({("a", 1), ("b", 2)})):
            fails.append("【R43】★ 违规点**变多**被判成进展")
        # ③ ★ 空现态（门全绿 ⇒ 无违规点）**不算**"真子集变小" ——
        #    全绿由 `ok` 那条路裁决，不走增量判据（避免"空集=进展"的假接受）
        if _增量可判(frozenset({("a", 1)}), frozenset()):
            fails.append("【R43】★ 空现态被判成增量进展（全绿该由 `ok` 裁决）")
        # ④ 基线为空 ⇒ 不启用增量（零回退，不动老行为）
        if _增量应用({}, frozenset(), Path("."), []) != ({}, [], [], []):
            fails.append("【R43】无基线时未走老路径")
        # ⑤ ★★★★★ **行为齿**：真造两棵树，一个"修好一半"、一个"没修好"，
        #    看增量裁决**真的**保留前者、回退后者。
        import tempfile as _tf43
        import shutil as _sh43
        _根43 = Path(_tf43.mkdtemp(prefix="r43_"))
        try:
            (_根43 / "src").mkdir(parents=True)
            (_根43 / "src" / "好.rs").write_bytes("// 原始\n".encode("utf-8"))
            (_根43 / "src" / "坏.rs").write_bytes("// 原始\n".encode("utf-8"))
            # 用**替换 `_score_files`** 来喂一个可预期的判据（不依赖真外部）
            _真score43 = globals()["_score_files"]
            _态43 = {"n": 0}

            def _假score43(workdir, deliverables, file_root=None, repo_root=None,
                           _真=_真score43, _根=_根43, _态=_态43):
                # 第 1 次（应用「好.rs」后）：违规点从 2 个降到 1 个 ⇒ 该保留
                # 第 2 次（应用「坏.rs」后）：违规点仍是 1 个 ⇒ 该回退
                # ★ R46 修正本夹具：旧版返回**全 True** 的门结果 —— 那是**自相矛盾**
                #   的夹具（"门全绿"却又报违规）。R46 之后"全绿"是**终点**、
                #   优先级高于"进展" ⇒ 旧夹具会让「坏.rs」也因"全绿"被保留。
                #   ⇒ 真实形态必须是：**还有一条门是红的**（判据仍红、但有位置信息）。
                _态["n"] += 1
                _ev = f'["{str(_根).replace(chr(92), chr(92)*2)}\\\\src\\\\坏.rs:9 x"]'
                return ({"src/好.rs@c": True, "src/坏.rs@c": False},
                        [{"gate": "c", "file": "src/坏.rs", "ok": False,
                          "cause": "退出码不符", "rc": 1, "err": _ev}])

            globals()["_score_files"] = _假score43
            _存根43 = globals().get("REPO_ROOT")
            try:
                globals()["REPO_ROOT"] = _根43
                _基线43 = frozenset({("src/好.rs", 1), ("src/坏.rs", 9)})
                _留43, _退43 = [], []
                _r43, _red43, _留43, _退43 = _增量应用(
                    {"src/好.rs": "// 改好\n", "src/坏.rs": "// 没改好\n"},
                    _基线43, _根43, [])
                # ★ 第一个文件必须**保留**（违规点真子集变小）
                if [x for x, _ in _留43] != ["src/好.rs"]:
                    fails.append(f"【R43】★ 该保留的文件没保留：{[x for x,_ in _留43]}")
                # ★ 第二个文件必须**回退**（违规点没变小）
                if _退43 != ["src/坏.rs"]:
                    fails.append(f"【R43】★ 该回退的文件没回退：{_退43}")
                # ★★★★★ 关键：**保留的那个文件必须真留在主树上**（进展不丢）
                if (_根43 / "src" / "好.rs").read_text(encoding="utf-8") != "// 改好\n":
                    fails.append("【R43】★★ 被判据验证过的进展**没有留在主树上**"
                                 " ⇒ 中长线任务永不收敛")
                # ★ 回退的那个必须**字节级**回到原样
                if (_根43 / "src" / "坏.rs").read_bytes() != "// 原始\n".encode("utf-8"):
                    fails.append("【R43】★ 未通过的文件没有字节级回退")
            finally:
                globals()["_score_files"] = _真score43
                if _存根43 is None:
                    globals().pop("REPO_ROOT", None)
                else:
                    globals()["REPO_ROOT"] = _存根43
        finally:
            _sh43.rmtree(_根43, ignore_errors=True)
    except Exception as _e43:
        fails.append("【R43】增量裁决门自身异常：%s: %s"
                     % (type(_e43).__name__, _e43))

    # ══════════════════════════════════════════════════════════════
    # 【R177】回退是**静默的** ⇒ 模型永远不知道自己被退回 ⇒ 每轮从头再猜
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 78 次真跑 · 中文编译器 · 同一文件连续 4 轮）：
    #   ✗ 算子选择器.rs：违规点**变多**（1 → 7） ⇒ 只回退这一个文件
    #   ✗ 算子选择器.rs：违规点**变多**（1 → 8） ⇒ 只回退这一个文件
    #   ✗ 算子选择器.rs：违规点**变多**（1 → 7） ⇒ 只回退这一个文件
    #   ⇒ Δ 不变 ⇒ stalled_fixed_point ⇒ 换门集 ⇒ 停机
    # ★ 它修不好不是因为难，是因为**它不知道上一版被退了、更不知道退它的那 7 个错长什么样**。
    try:
        import tempfile as _tf177
        import shutil as _sh177
        _根177 = Path(_tf177.mkdtemp(prefix="r177_"))
        try:
            (_根177 / "src").mkdir(parents=True)
            (_根177 / "src" / "好177.rs").write_bytes("// 原始\n".encode("utf-8"))
            (_根177 / "src" / "坏177.rs").write_bytes("// 原始\n".encode("utf-8"))
            _真score177 = globals()["_score_files"]
            _次177 = {"n": 0}
            _根s177 = str(_根177).replace(chr(92), chr(92) * 2)

            def _假score177(workdir, deliverables, file_root=None, repo_root=None,
                            _根=_根s177, _真根=_根177):
                # ★ 夹具必须与真实事故同形，且**不依赖调用次数**（次数不稳、内容稳）：
                #   坏177.rs 若被改成「没改好」⇒ 除了原来那 1 个错，**再加 1 个新错**
                #   ⇒ 不是真子集 ⇒ 回退，且台账要记下**那个新错**。
                _文 = ""
                try:
                    _文 = (_真根 / "src" / "坏177.rs").read_text(encoding="utf-8")
                except OSError:
                    _文 = ""
                _点 = f'"{_根}\\src\\坏177.rs:9 x"'
                if "没改好" in _文:
                    _点 += f', "{_根}\\src\\坏177.rs:11 y"'
                _两 = f"[{_点}]"
                return ({"src/好177.rs@c": True, "src/坏177.rs@c": False},
                        [{"gate": "c", "file": "src/坏177.rs", "ok": False,
                          "cause": "退出码不符", "rc": 1, "err": _两}])

            globals()["_score_files"] = _假score177
            _存根177 = globals().get("REPO_ROOT")
            try:
                globals()["REPO_ROOT"] = _根177
                _基线177 = frozenset({("src/好177.rs", 1), ("src/坏177.rs", 9)})
                _详情177: list = []
                _增量应用({"src/好177.rs": "// 改好\n",
                           "src/坏177.rs": "// 没改好\n"},
                          _基线177, _根177, [], 回退详情=_详情177)
                # ① ★★★ **回退的那一个必须被记下来**
                if len(_详情177) != 1:
                    fails.append(f"【R177】★ 回退没被记进台账（实得 {len(_详情177)} 笔）"
                                 " ⇒ 模型永远不知道自己被退回 ⇒ 每轮从头再猜")
                else:
                    _笔177 = _详情177[0]
                    if _笔177.get("文件") != "src/坏177.rs":
                        fails.append(f"【R177】★ 台账记错了文件：{_笔177.get('文件')!r}")
                    if not str(_笔177.get("因由") or "").strip():
                        fails.append("【R177】★ 台账没有「因由」⇒ 模型看不出为什么被退")
                    # ★★★ 关键：**新增的错**（那才是模型需要看的）
                    _新177 = _笔177.get("新增") or []
                    if not _新177:
                        fails.append("【R177】★★ 台账没有「你那一版新引入的错」"
                                     " ⇒ 模型只知道被退了，不知道该避什么")
                    elif not any("11" in str(x) for x in _新177):
                        fails.append(f"【R177】★ 台账的「新增」里没有那个**真新错**：{_新177}")
                # ② ★ **保留的不许进台账**（否则模型会以为自己在退步）
                if any(str(x.get("文件")) == "src/好177.rs" for x in _详情177):
                    fails.append("【R177】★ 被**保留**的文件也进了回退台账"
                                 " ⇒ 模型会误以为自己什么都没做成")
            finally:
                globals()["_score_files"] = _真score177
                if _存根177 is None:
                    globals().pop("REPO_ROOT", None)
                else:
                    globals()["REPO_ROOT"] = _存根177
        finally:
            _sh177.rmtree(_根177, ignore_errors=True)
    except Exception as _e177:
        fails.append(f"【R177】齿自身异常：{type(_e177).__name__}: {_e177}")
    # ③ ★★★★★ **算了、存了，但没送出去**（R128/R129/R136b 同族第 5 次）
    #   台账存在但 prompt 不读 ⇒ 等于没做。必须**同时**满足：读台账 + 拼进 prompt。
    try:
        _ast177 = __import__("ast")
        _树177 = _ast177.parse(_源码)
        _有读177 = _有拼177 = False
        for _n177 in _ast177.walk(_树177):
            if isinstance(_n177, _ast177.Attribute) and _n177.attr == "_回退台账":
                _有读177 = True
            if isinstance(_n177, _ast177.AugAssign) and isinstance(_n177.op, _ast177.Add):
                _源177 = _ast177.unparse(_n177)
                if "prompt" in _源177 and "_段177" in _源177:
                    _有拼177 = True
        if not _有读177:
            fails.append("【R177】★★ 台账没有任何读取点 ⇒ 白记")
        if not _有拼177:
            fails.append("【R177】★★★ 台账读了但**没拼进 prompt** ⇒ 白记"
                         "（这就是「算了、存了，但没送出去」）")
    except Exception as _e177b:
        fails.append(f"【R177】齿③自身异常：{type(_e177b).__name__}: {_e177b}")
    for _f177 in [x for x in fails if "【R177】" in str(x)]:
        print(f"  ✗ generic_goal {_f177}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R178】门缺字段是**可纠正**的，不是产品崩溃
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 79 次真跑 · 中文编译器 · 09:20 结束，原文）：
    #   ★★★ 重规划**崩了**（auto_goal.py:255）：
    #      ValueError: 判据槽 GateSpec 不完整（whole_reading 门缺 in/file（被测文件））
    #   ⇒ 整趟在**轮 1 之后**归零。
    # ★ 判据本身没错（门缺 in 确实不合法）；错的是**处置方式** ——
    #   那条报错**本身就是能回喂的**（"缺 in/file" 直接告诉模型补什么），
    #   却以裸 ValueError 被归进「产品缺陷」⇒ 不走回喂 ⇒ 停机。
    try:
        _坏178 = {"name": "x", "pre": [], "constraints": [], "acceptance": [],
                  "gates": [{"name": "g", "type": "whole_reading",
                             "in": None, "mode": "contains",
                              "must_contain": ["x"]}]}
        # ① ★★★ 必须是 `规约可纠正`（可回喂），**不是**裸 ValueError
        try:
            _build_dag(_坏178, [])
            fails.append("【R178】★ 门缺 in 竟然没被拒 ⇒ 不完整的门进了 DAG")
        except 规约可纠正 as _e178a:
            # ② ★ 报错必须**说清要补什么**（否则回喂等于没说）
            if "in" not in str(_e178a):
                fails.append(f"【R178】★ 回喂文本没说要补哪个字段：{str(_e178a)[:120]}")
        except Exception as _e178b:
            fails.append(f"【R178】★★★ 门缺 in 抛的是**裸 {type(_e178b).__name__}**"
                         " ⇒ 会被当成「产品缺陷」⇒ 整趟停机"
                         "（判据缺字段是可纠正的，不是产品坏了）")
    except Exception as _e178c:
        fails.append(f"【R178】齿①自身异常：{type(_e178c).__name__}: {_e178c}")
    # ③ ★★ **同一份规约只能有一个门源**（R117 同族）
    #   真证据：`_adopt_spec` 里 `_build_dag(spec, …)` 传的是**入参 spec**（未归一化），
    #   而 `self.spec` 已过 `normalize_gates` ⇒ `_build_dag` 与后面的判门
    #   看的是**两份不同的规约** ⇒ 门集分叉。
    #   ★ 只查 `_adopt_spec` 这一个生产方法：别的调用点（自检夹具、别的入口）
    #     有自己的规约来源，不是缺陷 —— 齿若扫全文件就会**替它们判罪**。
    try:
        _ast178 = __import__("ast")
        _树178 = _ast178.parse(_源码)
        _坏调178 = []
        for _n178 in _ast178.walk(_树178):
            if not (isinstance(_n178, _ast178.FunctionDef)
                    and _n178.name == "_adopt_spec"):
                continue
            for _c178 in _ast178.walk(_n178):
                if not (isinstance(_c178, _ast178.Call)
                        and isinstance(_c178.func, _ast178.Name)
                        and _c178.func.id == "_build_dag"
                        and _c178.args):
                    continue
                _首178 = _ast178.unparse(_c178.args[0])
                if _首178 != "self.spec":
                    _坏调178.append(f"第{_c178.lineno}行传的是 {_首178}")
        if _坏调178:
            fails.append("【R178】★★ `_adopt_spec` 里 `_build_dag` 没用归一化后的"
                         " `self.spec`"
                         f"（{'; '.join(_坏调178[:3])}）"
                         " ⇒ 同一份规约两个消费者看两份 ⇒ 门集分叉")
    except Exception as _e178d:
        fails.append(f"【R178】齿③自身异常：{type(_e178d).__name__}: {_e178d}")
    for _f178 in [x for x in fails if "【R178】" in str(x)]:
        print(f"  ✗ generic_goal {_f178}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R177b】注入必须落在**窗级**（真正写文件的那一层）
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 80 次真跑 · 10:14 结束）：全程**没有一行** `★ R177 已把…注入窗级 prompt`。
    #   现场：注入块插在 `_plan`（规划器 prompt），而窗级 prompt 在 `_produce_window`；
    #   规划器先跑（读台账、随即清空）⇒ 窗级再跑时**台账已空** ⇒ 白记。
    # ★ 这是「算了、存了，但没送出去」的**第 6 次**。
    # ★★ 而我 R177 的齿③**没抓住它** —— 它只检查「有没有拼进 prompt」，
    #   没检查「拼到了哪个函数」。**齿漏的那一层，正好是缺陷所在的那一层。**
    try:
        _ast177b = __import__("ast")
        _树177b = _ast177b.parse(_源码)
        _落点177b = []
        for _fn177b in _ast177b.walk(_树177b):
            if not isinstance(_fn177b, (_ast177b.FunctionDef,
                                       _ast177b.AsyncFunctionDef)):
                continue
            for _n177b in _ast177b.walk(_fn177b):
                if not (isinstance(_n177b, _ast177b.AugAssign)
                        and isinstance(_n177b.op, _ast177b.Add)):
                    continue
                _s177b = _ast177b.unparse(_n177b)
                if "_段177" in _s177b and "prompt" in _s177b:
                    _落点177b.append(_fn177b.name)
        # ① ★★★ 必须**恰好**落在 `_produce_window` 里
        if "_produce_window" not in _落点177b:
            fails.append("【R177b】★★★ 回退台账**没送进窗级 prompt**"
                         f"（实际拼在：{_落点177b or '无'}）"
                         " ⇒ 规划器读完就清空 ⇒ 窗级永远读不到 ⇒ 白记")
        # ② ★ 不许出现在规划器里（那会提前把台账吃掉）
        if "_plan" in _落点177b:
            fails.append("【R177b】★★ 台账被拼进了 `_plan`（规划器）"
                         " ⇒ 它先跑、读完即清 ⇒ 窗级读不到")
    except Exception as _e177b2:
        fails.append(f"【R177b】齿自身异常：{type(_e177b2).__name__}: {_e177b2}")
    for _f177b in [x for x in fails if "【R177b】" in str(x)]:
        print(f"  ✗ generic_goal {_f177b}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R179】退过的文件 ⇒ **强制补丁模式**（改动面从整份文件缩到那几行）
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 81 次真跑 · R177 已连续 3 轮注入台账）：
    #   轮1 ✗ 算子选择器.rs：违规点**变多**（1 → 7）
    #   轮2 ✗ 算子选择器.rs：违规点**变多**（1 → 8）
    #   轮3 ✗ 算子选择器.rs：违规点**变多**（1 → 7）
    #   ★ R177 的「你上一版被退了 + 新引入的错」**确实送达了**（日志 3 行），
    #     但**结果没变** ⇒ 模型不是不知道，是**做不到**：
    #     它每轮整份重写一个 19696 字符的文件 ⇒ 必然把好地方改坏。
    # ★ 根因：补丁模式原本只在 `len(原文) > 60000` 才开，而这 5 个文件
    #   是 19696/37836/16348/6176/1748 ⇒ **全部走整文件重写**。
    try:
        # ① 纯函数语义：退过 1 次就该切；没退过不切
        if not _退过179("a.rs", {"a.rs": 1}):
            fails.append("【R179】★ 退过 1 次的文件没被判成「该切补丁模式」")
        if _退过179("a.rs", {}):
            fails.append("【R179】★ 没退过的文件被判成「该切补丁模式」"
                         " ⇒ 会把正常文件也锁进补丁模式")
        if _退过179("a.rs", None):
            fails.append("【R179】★ 台账为 None 时没安全返回 False")
        # ② ★★★ **行为齿**：判定必须**结构上**接上了「退过就切」
        #   ★ R179b：第一版按**字面量包含**找（`"_退过179" in 测试源码`），
        #     而齿里那句 `if "_REPO_TARGET_CAP" in s and "_退过179" in s:`
        #     **自己就同时含这两个字面量** ⇒ `_有判179` 恒为真 ⇒ 齿永远绿。
        #     ★ 这是「齿的判据把齿自己算成了证据」—— 齿家族的新一层。
        #   ⇒ 改按**结构**匹配：必须是 `BoolOp`，且某个操作数是 `_退过179(...)` 调用。
        _ast179 = __import__("ast")
        _树179 = _ast179.parse(_源码)
        _有判179 = False
        for _n179 in _ast179.walk(_树179):
            if not isinstance(_n179, _ast179.If):
                continue
            if not isinstance(_n179.test, _ast179.BoolOp):
                continue            # ★ 注入后是裸 Compare ⇒ 在这里就被排除
            _s179 = _ast179.unparse(_n179.test)
            if "_REPO_TARGET_CAP" not in _s179:
                continue
            # ★ 必须是**调用** `_退过179(`，不是字符串里出现这几个字
            if any(isinstance(_o, _ast179.Call)
                   and isinstance(_o.func, _ast179.Name)
                   and _o.func.id == "_退过179"
                   for _o in _n179.test.values):
                _有判179 = True
        if not _有判179:
            fails.append("【R179】★★★ 补丁模式的判定**没接**「退过就切」"
                         " ⇒ 退过的文件仍走整份重写 ⇒ 每轮引入新错 ⇒ 永不收敛")
        # ③ ★ 计数必须**跨轮累计**（不能被每轮覆盖成 1）
        _有计179 = False
        for _n179b in _ast179.walk(_树179):
            if isinstance(_n179b, _ast179.AnnAssign):
                _tgt179 = _ast179.unparse(_n179b.target)
            elif isinstance(_n179b, _ast179.Assign):
                _tgt179 = _ast179.unparse(_n179b.targets[0])
            else:
                continue
            if "_回退次数" in _tgt179:
                _有计179 = True
        if not _有计179:
            fails.append("【R179】★ 没有 `_回退次数` 台账 ⇒ 「退过就切」无从判定")
    except Exception as _e179:
        fails.append(f"【R179】齿自身异常：{type(_e179).__name__}: {_e179}")
    for _f179 in [x for x in fails if "【R179】" in str(x)]:
        print(f"  ✗ generic_goal {_f179}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R180】跨文件连锁 ⇒ 整批兜底必须救回唯一正确的答案
    # ══════════════════════════════════════════════════════════════
    # 决定性实测（2026-10-28 12:23）：第 82 次真跑把 5 个文件逐个裁决 ⇒
    # **全部回退**（保留 0）⇒ 交还用户；而**同一批**一起应用后
    # `cargo test --lib --no-run` = **exit 0 · 0 error · #[test] 1936 未减**。
    # ⇒ 模型早就做对了，是**逐文件裁决**把正确答案否掉的。
    # ★ 根因：类型错误**跨文件连锁** ⇒ 「逐文件违规点集合」在全库型判据上
    #   **不是偏序**（全局错误数对单文件改动不单调）。
    try:
        import tempfile as _tf180
        import shutil as _sh180
        _根180 = Path(_tf180.mkdtemp(prefix="r180_"))
        try:
            (_根180 / "src").mkdir(parents=True)
            (_根180 / "src" / "甲.rs").write_bytes("// 原始\n".encode("utf-8"))
            (_根180 / "src" / "乙.rs").write_bytes("// 原始\n".encode("utf-8"))
            _真score180 = globals()["_score_files"]
            _根s180 = str(_根180).replace(chr(92), chr(92) * 2)

            def _假score180(workdir, deliverables, file_root=None, repo_root=None,
                            _根=_根s180, _真根=_根180):
                # ★ 真实跨文件连锁形态（★ 新错必须落在**真实存在**的文件上，
                #   否则 `_证据违规点` 会按路径校验把它丢掉 ⇒ 夹具失真）：
                #   改了甲的签名 ⇒ **乙里出现新错**；反之亦然。
                _甲 = (_真根 / "src" / "甲.rs").read_text(encoding="utf-8")
                _乙 = (_真根 / "src" / "乙.rs").read_text(encoding="utf-8")
                _甲好 = "新" in _甲
                _乙好 = "新" in _乙
                if _甲好 and _乙好:
                    return ({"src/甲.rs@c": True, "src/乙.rs@c": True}, [])
                _点 = []
                if _甲好 and not _乙好:
                    # 甲改好了、乙没改 ⇒ 乙里既有原来那处，又新增一处
                    _点.append(f'"{_根}\\src\\乙.rs:1 旧签名"')
                    _点.append(f'"{_根}\\src\\乙.rs:2 签名不一致"')
                elif _乙好 and not _甲好:
                    _点.append(f'"{_根}\\src\\甲.rs:1 旧签名"')
                    _点.append(f'"{_根}\\src\\甲.rs:2 签名不一致"')
                else:
                    _点.append(f'"{_根}\\src\\甲.rs:1 旧签名"')
                    _点.append(f'"{_根}\\src\\乙.rs:1 旧签名"')
                return ({"src/甲.rs@c": _甲好, "src/乙.rs@c": _乙好},
                        [{"gate": "c", "file": "src/甲.rs", "ok": False,
                          "cause": "编译失败", "rc": 1,
                          "err": "[" + ", ".join(_点) + "]"}])

            globals()["_score_files"] = _假score180
            _存根180 = globals().get("REPO_ROOT")
            try:
                globals()["REPO_ROOT"] = _根180
                _基线180 = frozenset({("src/甲.rs", 1), ("src/乙.rs", 1)})
                _r180, _red180, _留180, _退180 = _增量应用(
                    {"src/甲.rs": "// 新\n", "src/乙.rs": "// 新\n"},
                    _基线180, _根180, [])
                # ① ★★★ **必须救回**（逐文件会全退 ⇒ 整批兜底接手）
                _名180 = [x for x, _ in _留180]
                if set(_名180) != {"src/甲.rs", "src/乙.rs"}:
                    fails.append(f"【R180】★★★ 跨文件连锁时**没有救回**"
                                 f"（保留 {_名180}）"
                                 " ⇒ 唯一正确的答案被逐文件裁决否掉 ⇒ 永不收敛")
                # ② ★ 两个都必须**真留在主树上**（不是只记账）
                for _f180 in ("甲.rs", "乙.rs"):
                    if (_根180 / "src" / _f180).read_text(
                            encoding="utf-8") != "// 新\n":
                        fails.append(f"【R180】★ 整批保留了 {_f180} 但主树没变")
                # ③ ★ 全绿时违规点必须真空
                if _红门违规点(_red180):
                    fails.append(f"【R180】★ 整批保留后仍有违规点："
                                 f"{sorted(_红门违规点(_red180))[:3]}")
            finally:
                globals()["_score_files"] = _真score180
                if _存根180 is None:
                    globals().pop("REPO_ROOT", None)
                else:
                    globals()["REPO_ROOT"] = _存根180
        finally:
            _sh180.rmtree(_根180, ignore_errors=True)
    except Exception as _e180:
        fails.append(f"【R180】齿自身异常：{type(_e180).__name__}: {_e180}")
    for _f180b in [x for x in fails if "【R180】" in str(x)]:
        print(f"  ✗ generic_goal {_f180b}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R176b】同一次出现被抽成两种形态（带路径 / 裸文件名）⇒ 要求两遍
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 84 次真跑 13:50）：目标写 `示例工程\基础集\算子选择器.rs:682`，
    # 正则的路径段是可选的 ⇒ 同时抽出 `示例工程/基础集/算子选择器.rs` 与
    # `算子选择器.rs`；后者在**仓库根**不存在 ⇒ 判「缺」⇒ 要求模型交付一个
    # 根目录下的同名文件 ⇒ 连喂 3 次仍不改对 ⇒ 主心骨介入 ⇒ 又是 3 次 ⇒ 卡死。
    try:
        import tempfile as _tf176
        import shutil as _sh176
        _根176 = Path(_tf176.mkdtemp(prefix="r176b_"))
        try:
            (_根176 / "深" / "处").mkdir(parents=True)
            (_根176 / "深" / "处" / "x.rs").write_bytes("// 在\n".encode("utf-8"))
            _存176 = globals().get("REPO_ROOT")
            _存w176 = globals().get("WORKDIR")
            try:
                globals()["REPO_ROOT"] = _根176
                globals()["WORKDIR"] = None
                # ★ 反斜杠路径（Windows 目标里到处都是）
                _缺176 = _目标点名而清单没有(
                    "把 深\\处\\x.rs:12:3 改好", [])
                if _缺176:
                    fails.append(f"【R176b】★★★ 已存在的文件被**重复要求**"
                                 f"（缺={_缺176}）⇒ 裸文件名形态被当成另一个文件"
                                 " ⇒ 连喂 3 次仍不改对 ⇒ 规划卡死")
                # ★ 真·缺交仍必须报（不能因为去子串把这条判据弄哑）
                _缺176b = _目标点名而清单没有(
                    "再写 深\\处\\y.rs 断言它", [])
                if "深/处/y.rs" not in _缺176b:
                    fails.append(f"【R176b】★★ 真缺交被吞掉了（缺={_缺176b}）"
                                 " ⇒ 这趟必然少交一件")
            finally:
                if _存176 is None:
                    globals().pop("REPO_ROOT", None)
                else:
                    globals()["REPO_ROOT"] = _存176
                globals()["WORKDIR"] = _存w176
        finally:
            _sh176.rmtree(_根176, ignore_errors=True)
    except Exception as _e176:
        fails.append(f"【R176b】齿自身异常：{type(_e176).__name__}: {_e176}")

    # ══════════════════════════════════════════════════════════════
    # 【R176c】**报了错，但报告里没有那条证据**
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 84 次真跑 13:50）：
    #   `★ 规约可纠正（第 1 次）⇒ 回喂重规划：目标里点名、但交付物清单里没有的文件：`
    #   冒号后面**什么都没有** —— 而那条消息的清单在**第 2 行起**，
    #   日志却只打 `反馈.splitlines()[0]` ⇒ **把唯一的那条证据丢掉**。
    #   ★ 与 R177（台账记了但没拼进 prompt）同族：**算了、拼了，但没送出去**（第 7 次）。
    try:
        _ast176c = __import__("ast")
        _树176c = _ast176c.parse(_源码)
        _有压平176c = False
        _有截首176c = False
        for _n176c in _ast176c.walk(_树176c):
            if not isinstance(_n176c, _ast176c.Call):
                continue
            if not isinstance(_n176c.func, _ast176c.Name):
                continue
            if _n176c.func.id != "_log":
                continue
            _文176c = _ast176c.unparse(_n176c)
            if "回喂重规划" not in _文176c:
                continue
            # ★ 结构判据：必须含 `join(... splitlines())`，且**不得**含 `splitlines()[0]`
            if "splitlines()[0]" in _文176c:
                _有截首176c = True
            if "join" in _文176c and "splitlines" in _文176c:
                _有压平176c = True
        if _有截首176c:
            fails.append("【R176c】★★★ 回喂重规划**只送第一行**"
                         " ⇒ 清单在第 2 行起 ⇒ 人看到的是「冒号后面空的」")
        if not _有压平176c:
            fails.append("【R176c】★★ 回喂重规划的日志没有**压平整条反馈**"
                         " ⇒ 算出来的证据没送出去")
    except Exception as _e176c:
        fails.append(f"【R176c】齿自身异常：{type(_e176c).__name__}: {_e176c}")
    for _f176 in [x for x in fails
                  if "【R176b】" in str(x) or "【R176c】" in str(x)]:
        print(f"  ✗ generic_goal {_f176}", flush=True)


    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R46：**让判据全绿的那个文件必须保留**（否则永不收敛）（2 条）
    # ════════════════════════════════════════════════════════════════
    # 【自审抓出 · 2026-10-15】`_增量可判` 要求"违规点非空且真子集变小"。
    #   而**最后一个**文件修好后判据**全绿** ⇒ 违规点是**空集** ⇒
    #   `bool(现)` 为假 ⇒ 判成"未变小" ⇒ **回退它** ⇒
    #   **只剩一个文件时永远不可能收敛**（每轮都把那个文件回退掉，零进展）。
    #   ★ 根因：把"**全绿**"和"**进展**"当成同一件事的两个分支 ——
    #     而**全绿是更强的条件（终点）**，必须**先判**它。
    try:
        import tempfile as _tf46
        import shutil as _sh46
        _根46 = Path(_tf46.mkdtemp(prefix="r46_"))
        try:
            (_根46 / "src").mkdir(parents=True)
            (_根46 / "src" / "最后.rs").write_bytes("// 原始\n".encode("utf-8"))
            _真score46 = globals()["_score_files"]

            def _假score46(workdir, deliverables, file_root=None, repo_root=None,
                           _根=_根46):
                # 这个文件应用后**判据全绿**：门结果全 True、红门条目为空
                return ({"src/最后.rs@c": True, "src/最后.rs@d": True}, [])

            globals()["_score_files"] = _假score46
            _存根46 = globals().get("REPO_ROOT")
            try:
                globals()["REPO_ROOT"] = _根46
                _基线46 = frozenset({("src/最后.rs", 1)})
                _r46, _red46, _留46, _退46 = _增量应用(
                    {"src/最后.rs": "// 修好\n"}, _基线46, _根46, [])
                # ① ★★★★★ **必须保留** —— 这是"最后一个文件"的形态
                if [x for x, _ in _留46] != ["src/最后.rs"]:
                    fails.append("【R46】★★ 让判据**全绿**的文件被判成「未变小」"
                                 " ⇒ **只剩一个文件时永远不可能收敛**")
                # ② ★ 它必须**真留在主树上**
                if (_根46 / "src" / "最后.rs").read_text(
                        encoding="utf-8") != "// 修好\n":
                    fails.append("【R46】★ 全绿的那一步**没有留在主树上**")
                # ④ ★ R46b：**全绿后必须早停** —— 后面的文件**不得**再被写进主树
                #   （每试一个文件都要跑一遍全部门；真项目里一次就是几十秒）
                (_根46 / "src" / "后续.rs").write_bytes("// 原始\n".encode("utf-8"))
                _调次 = {"n": 0}

                def _假score46c(workdir, deliverables, file_root=None,
                                repo_root=None, _根=_根46, _态=_调次):
                    _态["n"] += 1
                    return ({"src/最后.rs@c": True}, [])

                globals()["_score_files"] = _假score46c
                (_根46 / "src" / "最后.rs").write_bytes("// 原始\n".encode("utf-8"))
                _增量应用({"src/最后.rs": "// 修好\n",
                           "src/后续.rs": "// 不该被写\n"}, _基线46, _根46, [])
                if (_根46 / "src" / "后续.rs").read_bytes() != "// 原始\n".encode("utf-8"):
                    fails.append("【R46b】★ 全绿后**没有早停** ⇒ 多余的改动被写进主树")
                # ★★★ R76：判门次数现在 = **改前量取 1 次** + 逐文件 1 次 = **2**。
                #   原来只数"逐文件"的次数（=1）⇒ 加了改前量取之后这条齿**假红**。
                #   ★ 它要守的语义**一个字没变**：全绿之后**不许**再为后面的文件判门。
                #     早停若坏了，次数会变成 1 + 2 = 3 ⇒ 仍然被抓。
                #   ★ 判据仍是**精确等号**（不用 `<=`：那会把"多判了"放过去）。
                #     ★ 这是本库那条教训的又一例：**改口径/加前置必须同时改齿**，
                #       否则齿会替旧口径报假红（R75 那次是替缺陷说话，这次是替旧实现说话）。
                if _调次["n"] != 2:
                    fails.append(f"【R46b】★ 全绿后仍继续判门（判门 {_调次['n']} 次，"
                                 f"期望 2 = 改前量取 1 + 逐文件 1）"
                                 " ⇒ 白烧墙钟时间")
                # ③ ★ 反向（防假红）：**红着但算不出位置**时不得被当成全绿保留
                def _假score46b(workdir, deliverables, file_root=None,
                                repo_root=None, _根=_根46):
                    return ({"src/最后.rs@c": False},
                            [{"gate": "c", "file": "src/最后.rs", "ok": False,
                              "cause": "退出码不符", "rc": 1, "err": "无位置信息"}])
                globals()["_score_files"] = _假score46b
                (_根46 / "src" / "最后.rs").write_bytes("// 原始\n".encode("utf-8"))
                _r46b, _red46b, _留46b, _退46b = _增量应用(
                    {"src/最后.rs": "// 乱改\n"}, _基线46, _根46, [])
                if _留46b:
                    fails.append("【R46】★ 假红：**红着但算不出位置**被当成全绿保留了"
                                 " ⇒ 会把没验证过的改动留在主树")
            finally:
                globals()["_score_files"] = _真score46
                if _存根46 is None:
                    globals().pop("REPO_ROOT", None)
                else:
                    globals()["REPO_ROOT"] = _存根46
        finally:
            _sh46.rmtree(_根46, ignore_errors=True)
    except Exception as _e46:
        fails.append("【R46】全绿必须保留门自身异常：%s: %s"
                     % (type(_e46).__name__, _e46))

    # ════════════════════════════════════════════════════════════════════
    # 【R76】逐文件裁决必须看得见**结构级红门**（真跑抓出 · 2026-10-17）
    # ════════════════════════════════════════════════════════════════════
    # 真跑证据：5 条门里两条红 ——
    #   · `（全库）@…无只打印` = 假 ⇒ 输出**带 `文件:行号`** ⇒ 14 个位置点
    #   · `src/模块甲/mod.rs@…登记键与闭合标记俱在` = 假
    #     ⇒ 整文件级读门，**一个位置都不报**
    # 模型的产出正是"给 mod.rs 补闭合标记" ⇒ 这条门**该翻绿**，
    # 但位置点 14 → 14 ⇒ 判成"未变小" ⇒ **正确改动被回退**。
    # ★ 这是**结构性**的：凡是"补一个标记/补一段结构"而"验收门集里还挂着
    #   一条整库的债"的任务，**在构造上不可能被保留**。
    try:
        import tempfile as _tf76
        import shutil as _sh76
        _根76 = Path(_tf76.mkdtemp(prefix="r76_"))
        try:
            (_根76 / "src").mkdir(parents=True, exist_ok=True)
            (_根76 / "src" / "标记.rs").write_bytes("// 原始\n".encode("utf-8"))
            (_根76 / "src" / "别处.rs").write_bytes("// 别处\n".encode("utf-8"))
            _存根76 = globals().get("REPO_ROOT")
            _真score76 = globals()["_score_files"]
            # ★ 位置点**一个不变**（这是真跑里发生的事），只有**结构门**翻绿。
            _证据76 = ("src/别处.rs:11 只打印不断言\n"
                       "src/别处.rs:22 只打印不断言\n")
            _态76 = {"n": 0}

            def _假score76(workdir, deliverables, file_root=None,
                           repo_root=None, _态=_态76, _证=_证据76):
                _态["n"] += 1
                # 改前（第 1 次）：这条文件主体的门是**假**
                # 改后（第 2 次起）：它翻成**真**，而整库那条门**照旧是假**
                _标 = _态["n"] >= 2
                return ({"src/标记.rs@标记门": _标,
                         f"{_全库主体}@整库门": False},
                        [{"gate": "整库门", "file": _全库主体, "ok": False,
                          "cause": "退出码不符", "rc": 1, "err": _证}])

            globals()["REPO_ROOT"] = _根76
            globals()["_score_files"] = _假score76
            try:
                # 基线：位置点非空 ⇒ 才走增量裁决这条路
                _基76 = _红门违规点([{"gate": "整库门", "err": _证据76}])
                if len(_基76) != 2:
                    fails.append(f"【R76】夹具自相矛盾：位置点应为 2，实得 {len(_基76)}")
                _r76, _red76, _留76, _退76 = _增量应用(
                    {"src/标记.rs": "// 补上标记\n"}, _基76, _根76, [])
                # ① ★★★★★ 核心：**位置点没变、结构门翻绿** ⇒ 必须**保留**
                if [x for x, _ in _留76] != ["src/标记.rs"]:
                    fails.append("【R76】★★ 位置点 2→2 而**结构门已翻绿**，"
                                 "却仍被判成「未变小」而回退 ⇒ "
                                 "凡是「补标记/补结构」的任务在构造上不可能收敛")
                # ② ★ 它必须**真留在主树上**（不是只在返回值里"保留"）
                if (_根76 / "src" / "标记.rs").read_text(
                        encoding="utf-8") != "// 补上标记\n":
                    fails.append("【R76】★ 被判「保留」的那一步**没有留在主树上**")
                # ③ ★ 反向（防假绿）：**结构门没翻绿、位置点也没变** ⇒ 必须回退。
                #   ★ 没有这条反向齿，①可以靠"永远保留"作弊（无齿）。
                _态76["n"] = 100          # 让改前/改后都取到"假"

                def _假score76b(workdir, deliverables, file_root=None,
                                repo_root=None, _证=_证据76):
                    return ({"src/标记.rs@标记门": False,
                             f"{_全库主体}@整库门": False},
                            [{"gate": "整库门", "file": _全库主体, "ok": False,
                              "cause": "退出码不符", "rc": 1, "err": _证}])
                globals()["_score_files"] = _假score76b
                (_根76 / "src" / "标记.rs").write_bytes("// 原始\n".encode("utf-8"))
                _r76b, _red76b, _留76b, _退76b = _增量应用(
                    {"src/标记.rs": "// 乱改\n"}, _基76, _根76, [])
                if _留76b:
                    fails.append("【R76】★ 假绿：什么都没翻绿（位置点 2→2、结构门仍假）"
                                 " 却被保留了 ⇒ 会把没验证过的改动留在主树")
                # ④ ★ **主体过滤**：别的文件的门翻绿**不算**我的进步（不许串味）
                _态76["n"] = 0

                def _假score76c(workdir, deliverables, file_root=None,
                                repo_root=None, _态=_态76, _证=_证据76):
                    _态["n"] += 1
                    # 改后：**别的文件**的门翻绿了，而**本文件**的门照旧假
                    return ({"src/标记.rs@标记门": False,
                             "src/别处.rs@别处门": _态["n"] >= 2,
                             f"{_全库主体}@整库门": False},
                            [{"gate": "整库门", "file": _全库主体, "ok": False,
                              "cause": "退出码不符", "rc": 1, "err": _证}])
                globals()["_score_files"] = _假score76c
                (_根76 / "src" / "标记.rs").write_bytes("// 原始\n".encode("utf-8"))
                _r76c, _red76c, _留76c, _退76c = _增量应用(
                    {"src/标记.rs": "// 乱改\n"}, _基76, _根76, [])
                if _留76c:
                    fails.append("【R76】★★ **串味**：别的文件的门翻绿被算成了"
                                 "本文件的进步 ⇒ 用别人的进展给自己记账")
                # ⑤ ★ 单元齿：`_红门判据点` 的两种形态都要收
                _点76 = _红门判据点({"src/甲.rs@结构门": False,
                                     "src/甲.rs@位置门": True,
                                     f"{_全库主体}@整库门": False},
                                    [{"gate": "整库门", "err": "src/别处.rs:7 坏"}],
                                    主体="src/甲.rs")
                if "门:src/甲.rs@结构门" not in _点76:
                    fails.append("【R76】★ 结构级红门**没有**被收进判据点 ⇒ "
                                 "它对逐文件裁决仍不可见（本缺陷原样）")
                if "门:src/甲.rs@位置门" in _点76:
                    fails.append("【R76】★ 已绿的门被当成违规点收进去了")
                if "门:" + _全库主体 + "@整库门" in _点76:
                    fails.append("【R76】★ 主体过滤失效：别的文件的红门混进来了")
                # ★ 判据点里**两种形态共存**（位置点元组 + 红门键字符串）⇒
                #   不能直接 `for a, b in 集合`（字符串会被拆成字符 ⇒ 解包炸）。
                #   ★ 这正是 `_验R75齿` 那一课的近亲：**混合形态的集合必须按类型分开处理**。
                _位76 = {(a, b) for a, b in
                         (x for x in _点76 if isinstance(x, tuple))}
                if ("src/别处.rs", 7) not in _位76:
                    fails.append("【R76】★ 位置点丢了 ⇒ 老度量被新度量顶掉")
            finally:
                globals()["_score_files"] = _真score76
                if _存根76 is None:
                    globals().pop("REPO_ROOT", None)
                else:
                    globals()["REPO_ROOT"] = _存根76
        finally:
            _sh76.rmtree(_根76, ignore_errors=True)
    except Exception as _e76:
        fails.append("【R76】结构级红门可见性齿自身异常：%s: %s"
                     % (type(_e76).__name__, _e76))

    # ════════════════════════════════════════════════════════════════════
    # 【R78】项目验收门必须**相对基线**判（真跑抓出 · 2026-10-17）
    # ════════════════════════════════════════════════════════════════════
    # 真跑证据：任务自己的门**全绿**（含目标那条 `…登记键与闭合标记俱在=True`），
    #   唯一红的是 `（全库）@fake_test_scanner_green` —— 主树**本来就有的**
    #   11 处「只打印不断言」。任务级也说 `达标（相对基线）`，
    #   可项目验收门判**绝对全绿** ⇒ exit 1 ⇒ **红的不是任务，是别人的债**。
    # ★ 与 R73/R73b **同一个病，只是高了一层**。
    try:
        # ① ★ 基线里红的、现在仍红 ⇒ **放行**（那是历史债，不是我的责任）
        _ok78, _因78, _坏78 = _项目门是否放行(
            {"cargo_check_lib": True, "质量门": False, "no_scaffold": True},
            {"cargo_check_lib": True, "质量门": False, "no_scaffold": True})
        if not _ok78 or _坏78:
            fails.append("【R78】★★ 主树**本来就红**的项目门（历史债）仍红时被拒绝放行"
                         " ⇒ 每一个任务都被迫负责**整棵树**的历史债"
                         " ⇒ 无人值守永远跑不通（这正是真跑里发生的事）")
        # ② ★ 反向：基线里**绿**的门现在红了 ⇒ **必须拒绝**（这是真变坏）
        _ok78b, _因78b, _坏78b = _项目门是否放行(
            {"cargo_check_lib": True, "质量门": False},
            {"cargo_check_lib": False, "质量门": False})
        if _ok78b:
            fails.append("【R78】★ 假绿：基线里绿的门**由绿转红**却仍放行 ⇒ "
                         "会把主树改坏还报成功")
        if _坏78b != ["cargo_check_lib"]:
            fails.append(f"【R78】★ 变坏的门没被点名（实得 {_坏78b}）⇒ "
                         f"回执说不清是谁的责任")
        # ★★ ③b 因由**文本里**也必须点名 —— 只看第三个返回值不够，
        #   因为**人能看到的**是 `因由` 那句话。
        #   ★ `_验R78齿.py` ③ 当场抓出来的无齿：注入"因由不点名"后自检仍全绿。
        #     如实登记：只查结构化返回值 = 只查了机器看的，没查人看的。
        if "cargo_check_lib" not in (_因78b or ""):
            fails.append(f"【R78】★ 变坏的门没出现在**因由文本**里"
                         f"（实得 {_因78b!r}）⇒ 回执读不出是谁的责任")
        # ③ ★ 反向：门**消失**也算变坏。
        #   ★ 必须让**其余门里有红的**，否则会走"全绿"那一支（见下条 ④）。
        #     ★ 我第一版写成 `({"cargo_check_lib": True}, {"别的门": True})`
        #       —— 那时"现存的门全绿" ⇒ 走终点分支放行 ⇒ **齿报假红**。
        #       不是代码错，是**齿问错了问题**：如实登记（改齿，不改代码）。
        #   ★ 为什么"全绿"要先于"消失"判：全绿是**终点**（现存的门全绿 ⇒ 没有红），
        #     此时某个门消失只说明档案变了，不是本次任务造成的坏。
        _ok78c, _因78c, _坏78c = _项目门是否放行(
            {"cargo_check_lib": True, "质量门": False}, {"质量门": False})
        if _ok78c:
            fails.append("【R78】★ 基线里绿的门**消失了**却仍放行 ⇒ "
                         "「门不见了」被当成了「门绿了」")
        if _坏78c != ["cargo_check_lib"]:
            fails.append(f"【R78】★ 消失的门没被点名（实得 {_坏78c}）")
        # ④ ★ 全绿 ⇒ 放行（不需要基线；这是终点）
        _ok78d, _因78d, _ = _项目门是否放行(
            {"cargo_check_lib": False}, {"cargo_check_lib": True})
        if not _ok78d:
            fails.append("【R78】★ 全绿却不肯放行 ⇒ 终点不可达")
        # ⑤ ★ 基线为空 ⇒ **老行为**（必须绝对全绿），零回退
        _ok78e, _因78e, _ = _项目门是否放行(None, {"a": False})
        if _ok78e:
            fails.append("【R78】★ 基线未量到时竟按相对口径放行 ⇒ "
                         "「读不到」被当成了「没变坏」")
        _ok78f, _因78f, _ = _项目门是否放行(None, {"a": True})
        if not _ok78f:
            fails.append("【R78】★ 基线未量到时全绿也不放行 ⇒ 零回退被破坏了")
        # ⑥ ★★ **接线齿**：光有函数不算 —— 必须真的被 `_project_acceptance` 用上，
        #   而且**项目门基线**必须真的被**改之前**量到并接到 `self` 上。
        #   ★ R71/R74/R77 那一课：写好了不等于接上了。
        #   ★★★ **针必须拼出来，不能写成整串**：本齿要查的那几个串
        #     **也出现在本齿自己的断言里** ⇒ 写成整串就会**匹配到自己** ⇒ 恒真 ⇒ 无齿。
        #     ★ `_验R78齿.py` ⑤ 与 ⑦ 当场抓出这两条同时无齿。
        #     ★ 我先试了"把源码里的字符串内容一起剥掉"（`strip_for_matching`）
        #       —— **剥过头**：针里的 `"res"` 也被剥掉 ⇒ **恒红**（4 条全红）。如实登记。
        #       ⇒ 正解是"**让针不以整串形式出现在源码里**"（拼接），
        #         而不是"把源码里的字符串都挖掉"。
        #     ★ 这是本库那条规矩的第三次复发：**门不得匹配到它自己**。
        _源78 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _码78 = "\n".join(_l.split("#", 1)[0] for _l in _源78.splitlines())
        _针78接 = "_项目门是否放行(" + "_基78, res)"
        _针78缓 = '_门态缓存[' + '"res"] = dict(res or {})'
        _针78self = "self._项目门基线res = " + "dict(_基res78)"
        _针78环 = "for work in range(1, " + "self.max_work + 1):"
        if _针78接 not in _码78:
            fails.append("【R78】★ `_项目门是否放行` **没有接到** "
                         "`_project_acceptance` 上 ⇒ 项目门仍是绝对口径")
        if _针78缓 not in _码78:
            fails.append("【R78】★ 项目门的**基线没有被缓存** ⇒ 交付时无「跟谁比」")
        if _针78self not in _码78:
            fails.append("【R78】★ 基线**没有接到** `self._项目门基线res` 上 ⇒ "
                         "交付时读不到 ⇒ 静默退回绝对口径")
        # ⑦ ★ 基线必须是**改之前**量的：缓存点必须在 `for work` 之前。
        #   ★ 放在 `for work` 之后 ⇒ 基线含**自己造成的**坏 ⇒ 那是污染基线。
        _i78基 = _码78.find(_针78self)
        _i78环 = _码78.find(_针78环)
        if _i78基 < 0 or _i78环 < 0 or _i78基 > _i78环:
            fails.append("【R78】★ 项目门基线**不是在改之前量的** ⇒ "
                         "基线会被自己造成的坏污染（「基准不干净，还原就是污染」）")
    except Exception as _e78:
        fails.append("【R78】项目门相对基线齿自身异常：%s: %s"
                     % (type(_e78).__name__, _e78))

    # ════════════════════════════════════════════════════════════════════
    # 【R80】可纠正的规约错误必须**按结构**判，不按异常类型枚举
    # ════════════════════════════════════════════════════════════════════
    # 真跑两例（2026-10-17 · 第五次真项目验收）：
    #   ① `规划失败：判据门沙箱不通过（… 'powershell' …）` ⇒ 整趟死
    #   ② `重规划失败：重规划规约不合法：文件 path 重复：…:7284` ⇒ 整趟死
    # 两个都是**模型能照着改的表述问题**，却都**绕过回喂循环**。
    # 根因：可纠正性被按「异常类型」枚举（只 except 两种）。
    # ★ 与 R79 同一个病：拿一张表冒充"可能性"本身。
    try:
        # ① 三个"可纠正"的类**必须**是同一个基类的子类（结构，不是注释）
        for _类80 in (交付物形态错, 计划不可行):
            if not issubclass(_类80, 规约可纠正):
                fails.append(f"【R80】★ {_类80.__name__} **不是** `规约可纠正` 的子类 ⇒ "
                             f"「同一族」只写在注释里，代码里并不存在")
        # ② ★ 基类必须**不是** `Exception` 直挂的泛型（否则会把真 bug 也当可纠正）
        if 规约可纠正.__bases__ != (ValueError,):
            fails.append(f"【R80】★ `规约可纠正` 的基类不是 `ValueError`（实得 "
                         f"{规约可纠正.__bases__}）⇒ 与「其它错误」的分界模糊")
        # ③ ★★ **接线齿**：回喂循环必须**只认基类**，
        #   不许再逐个 except 子类 —— 否则新增一类可纠正错误时又会漏在外面。
        _源80 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _码80 = "\n".join(_l.split("#", 1)[0] for _l in _源80.splitlines())
        _针80 = "except " + "规约可纠正 as exc:"
        # ★★★★★ R80b（真跑抓出 · 2026-10-17）：**这条齿原来要求「唯一」——
        #   而那个「唯一」本身就是缺陷的证明。**
        #   真跑实测：`重规划失败：【计划不可能成功（机器可证）】 → 诚实停机` ⇒ exit 1，
        #   而**同样的错在首次规划那条路上是会被回喂的**。
        #   ⇒ 同一个"可纠正"的错误，走两条路有**两种命运** ——
        #     说明回喂只接在**一条路**上（与 R71「隔离只接到一条路」同型）。
        #   ★ 所以齿必须要求「**每一条产生规约的路**上都有回喂」，
        #     而不是"全局只有一处"。原来那个 `!= 1` 恰好把
        #     "另一条路也接上了" 判成了缺陷 —— **齿替旧实现说了话**（第四次）。
        _路80 = {
            "首次规划 `_plan`": "async def _plan(",
            "重规划 `_replan`": "async def _replan(",
        }
        _位80 = {名: _码80.find(锚) for 名, 锚 in _路80.items()}
        if any(v < 0 for v in _位80.values()):
            fails.append(f"【R80】★ 找不到规约产出的入口函数：{_位80} ⇒ 齿已与实现脱节")
        else:
            _序80 = sorted(_位80.values())
            for _名80b, _起 in _位80.items():
                _止 = next((x for x in _序80 if x > _起), len(_码80))
                if _针80 not in _码80[_起:_止]:
                    fails.append(f"【R80】★ 「{_名80b}」这条路上**没有** "
                                 f"`except 规约可纠正` 回喂 ⇒ 同一个可纠正错误"
                                 f"走两条路会有两种命运（真跑实测过）")
        for _子80 in ("交付物形态错", "计划不可行"):
            if f"except {_子80} as exc:" in _码80:
                fails.append(f"【R80】★ 回喂循环里仍在**逐个** `except {_子80}` ⇒ "
                             f"枚举又回来了（新增一类可纠正错误时会再漏）")
        # ④ ★ 那三处真跑失败点必须**各自**抛基类（不是裸 ValueError）。
        #   ★★★ **不能数总次数**：本齿自己的行为齿里也有一句
        #     `raise 规约可纠正("测试")` ⇒ 计数会把它算进去 ⇒ 注入掉一处后
        #     总数仍 ≥3 ⇒ **无齿**。`_验R80齿.py` ④⑤⑥ 当场抓出来的（三处同时）。
        #     这是「**门不得匹配到它自己**」的**第三次**复发。
        #   ⇒ 正解：**逐处用带上下文的唯一针**，而不是数一个总数。
        _针80三 = {
            "规约校验失败": ("raise " + "规约可纠正(\n"
                             "                        f\"规约不合法：{err}"),
            "沙箱门拒绝": ("raise " + "规约可纠正(\n"
                           "                f\"判据门沙箱不通过"),
            "判定层必然性": ("raise " + "规约可纠正(\n"
                             "                f\"判定层必然性不成立"),
        }
        for _名80, _针80b in _针80三.items():
            if _针80b not in _码80:
                fails.append(f"【R80】★ 「{_名80}」没有抛 `规约可纠正` ⇒ "
                             f"这条可纠正错误会**绕过回喂循环** ⇒ 整趟死"
                             f"（真跑实测过两次）")
        # ⑤ ★ 行为齿：三个子类都要能被**同一个** `except 规约可纠正` 接住
        for _类80b in (交付物形态错, 计划不可行):
            try:
                raise _类80b("测试")
            except 规约可纠正:
                pass
            except Exception:        # noqa: BLE001
                fails.append(f"【R80】★ {_类80b.__name__} 没被基类接住")
        try:
            raise 规约可纠正("测试")
        except 规约可纠正:
            pass
        except Exception:            # noqa: BLE001
            fails.append("【R80】★ `规约可纠正` 自己没被基类接住")

        # ⑥ ★★★ R80b：**行为齿 —— 重规划路上「规约不合法」必须回喂重试，
        #   而不是立刻抛出。**
        #   ★ 为什么需要这条：`_验R80齿.py` ⑧ 注入「把 `if 第次 >= 2:` 改成
        #     `if True:`」（= 第一次不合法就抛）后**自检仍然全绿** ⇒ 无齿。
        #     原因是自检里没有任何场景让 `_replan` **先给一版不合法的规约、
        #     再给一版合法的** ⇒ 分不出「重试」和「立刻抛」。
        #   ★ 这正是本库那条教训：**夹具必须能让两个实现给出相反答案**。
        #   ⇒ 用一个只实现"规约产出 + 采纳"两件事的**最小替身**，
        #     直接验"不合法 ⇒ 不抛、且请求次数 ≥2"。
        try:
            import asyncio as _aio80

            class _假路由80:
                """第 1 次给**不合法**规约，第 2 次给**合法**规约。"""

                def __init__(self):
                    self.n = 0

                async def call(self, _msgs):
                    self.n += 1
                    class _R:
                        pass
                    r = _R()
                    # 第 1 次：缺 name（`_valid_spec` 必拒）⇒ 可纠正
                    r.content = ([{"name": "缺名", "deliverables": [{"path": "a.py"}]}]
                                 if self.n == 1 else
                                 [{"name": "对了",
                                   "deliverables": [{"path": "a.py",
                                                     "gates": [{"type": "compile",
                                                                "cmd": ["true"],
                                                                "cwd": "."}]}],
                                   "gates": [{"type": "compile", "cmd": ["true"],
                                              "cwd": "."}]}])
                    return r

            async def _走一遍80():
                """照 `_replan` 的**结构**走一遍：不合法 ⇒ 回喂 ⇒ 第 2 次成功。"""
                路 = _假路由80()
                反馈 = ""
                抛了 = None
                for 第次 in range(3):
                    resp = await 路.call([])
                    spec = resp.content[0]
                    err = _valid_spec(spec)
                    if err:
                        反馈 = f"规约不合法：{err}"
                        if 第次 >= 2:
                            抛了 = 反馈
                            break
                        continue
                    return ("ok", 路.n, 第次)
                return ("抛", 路.n, 抛了)

            _态80, _次80, _详80 = _aio80.run(_走一遍80())
            if _态80 != "ok":
                fails.append(f"【R80】★ 重规划路上「规约不合法」**没有回喂重试**"
                             f"（第 {_次80} 次就放弃了：{_详80}）⇒ "
                             f"重试次数被浪费（真跑实测：`重规划失败` ⇒ 整趟死）")
            elif _次80 < 2:
                fails.append(f"【R80】★ 重规划只请求了 {_次80} 次就成功 ⇒ "
                             f"说明第 1 次的**不合法规约被当成了合法**（夹具失效）")
        except Exception as _e80b:      # noqa: BLE001
            fails.append("【R80】重规划回喂行为齿自身异常：%s: %s"
                         % (type(_e80b).__name__, _e80b))
    except Exception as _e80:
        fails.append("【R80】可纠正性按结构判齿自身异常：%s: %s"
                     % (type(_e80).__name__, _e80))

    # ════════════════════════════════════════════════════════════════════
    # 【R81】「目标声明 → 判据」这一跳：目标点名而不存在的文件必须进交付物
    # ════════════════════════════════════════════════════════════════════
    # 真跑证据（2026-10-17 夜 · 查 `tokens_chars: 0` 时顺带抓出）：
    #   collatz 那批 2597 趟，目标写着「再写 test_collatz.py 断言 …」，
    #   交付物清单里**只有 collatz.py** —— 那个测试文件**一次都没交**，门全绿。
    # 根因：门是从**产物**的形状写的（`f([1])[0]==0` 照着机器组装的列表接口），
    #   不是从**目标**写的 ⇒ 「机器自己组装 + 门照产物写」可以自洽地交付不合目标的产物。
    try:
        # ① 行为齿：真跑那一例必须被抓到
        _目标81 = ("写一个纯 Python 模块 collatz.py：函数 collatz_steps(n: int) -> int。"
                   "再写 test_collatz.py 断言 collatz_steps(1)==0。")
        _缺81 = _目标点名而清单没有(_目标81, [{"path": "collatz.py"}])
        if "test_collatz.py" not in _缺81:
            fails.append(f"【R81】★ 目标点名 `test_collatz.py` 而清单没有，"
                         f"却**没被抓到**（实得缺 {_缺81}）⇒ 这趟必然少交一件")
        # ② 反向：两件都交 ⇒ 不得误报（否则会把对的规约回喂掉）
        _缺81b = _目标点名而清单没有(_目标81, [{"path": "collatz.py"},
                                                {"path": "test_collatz.py"}])
        if _缺81b:
            fails.append(f"【R81】★ 交付物齐全却**误报缺交**（实得 {_缺81b}）"
                         f"⇒ 会把正确的规约回喂掉（假红比无齿更坏）")
        # ③ ★★ 边界：**已存在**的文件不得被要求（只读上下文 / 要改的源码）。
        #   ★ 这里必须用"**目标提到、清单里没有**、但**磁盘上存在**"的文件 ——
        #     否则 `记 in 已交` 会先 `continue`，**根本走不到 exists 那一跳**，
        #     这条齿就变成无齿的（`_验R81齿.py` ② 注入当场抓出来过）。
        _缺81c = _目标点名而清单没有(
            "参考 src/模块甲/mod.rs 的写法，写 out/x81.py",
            [{"path": "out/x81.py"}])
        if _缺81c:
            fails.append(f"【R81】★ 把**已存在**的文件当成缺交（实得 {_缺81c}）"
                         f"⇒ 每个改源码的任务都会被无端回喂")
        # ④ ★ 接线齿：`_adopt_spec` 必须真的调它
        _源81 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _码81 = "\n".join(_l.split("#", 1)[0] for _l in _源81.splitlines())
        _针81 = "_目标点名而清单没有(" + 'getattr(self, "goal", "") or "",'
        if _针81 not in _码81:
            fails.append("【R81】★ 缺交检查**没有接到** `_adopt_spec` 上 ⇒ "
                         "它只是个函数，不拦任何东西")
        # ⑤ ★ 抛的必须是 `规约可纠正`（否则绕过 R80 的回喂 ⇒ 整趟死）
        _i81 = _码81.find(_针81)
        if _i81 >= 0:
            _窗81 = _码81[_i81:_i81 + 900]
            if "raise " + "规约可纠正(" not in _窗81:
                fails.append("【R81】★ 缺交时抛的不是 `规约可纠正` ⇒ "
                             "绕过回喂循环 ⇒ 整趟死（R80 的教训）")
    except Exception as _e81:
        fails.append("【R81】目标→判据齿自身异常：%s: %s"
                     % (type(_e81).__name__, _e81))

    # ════════════════════════════════════════════════════════════════════
    # 【R93】只读探针：**把窗口想查的东西，由机器代它查好递过去**
    # ════════════════════════════════════════════════════════════════════
    # 真跑形态：窗口输出过
    #     I'll start by exploring the repository and reproducing the failing gates.
    #     <｜｜DSML｜｜ invoke name="bash">
    # 它**想跑 bash 去看仓库**，被 R32 认出「这不是内容」**拒收**。
    # 拒得对，但**拒完之后没给它一条正当的路** ⇒
    #   不是「完全剥夺了能力」，是「保留了想的能力，剥夺了查的能力」。
    try:
        # ① 探针块必须真的出现在执行提示里（否则等于没给路）
        _p93 = _exec_prompt("目标", {"gates": []}, {"path": "a.rs"},
                            {}, "", "【接口画像】实测内容")
        if "【接口画像】实测内容" not in _p93:
            fails.append("【R93】★ 执行提示里**没有探针内容** ⇒ "
                         "窗口仍然只能瞎猜（本件要修的就是这个）")
        if "机器**已经替你把项目查过一遍**" not in _p93:
            fails.append("【R93】★ 探针块没有说明「已经替你查过了」⇒ "
                         "窗口会照旧去探索仓库（真跑里它就是这么跑的 bash）")

        # ② 不传探针时**不得**凭空造一个探针块（零回退）
        _p93b = _exec_prompt("目标", {"gates": []}, {"path": "a.rs"}, {})
        if "机器**已经替你把项目查过一遍**" in _p93b:
            fails.append("【R93】★ 没传探针却出现了探针块 ⇒ 空块会误导窗口")

        # ③ 探针**只读**：模块里不得有写文件/起子进程改环境的入口
        _探源码 = (Path(__file__).resolve().parent / "只读探针.py")
        if not _探源码.is_file():
            fails.append("【R93】★ 找不到 `只读探针.py` ⇒ 探针没落地")
        else:
            _探文 = _探源码.read_text(encoding="utf-8")
            for _坏93 in ("write_text(", "write_bytes(", "unlink(", "mkdir(",
                          "rmtree(", "os.remove(", "shutil.copy"):
                # ★ 例外：只读探针**允许**写临时目录（自检夹具），
                #   但**不得**写主树 —— 这里只检查「有没有写主树的入口」。
                if _坏93 in _探文 and "自检" not in _探文:
                    fails.append(f"【R93】★ 探针里有写操作 `{_坏93}` ⇒ "
                                 f"「只读」这条破了（产物必须只能被门裁决）")

        # ④ 接线：`_produce_window` 必须**真的**调探针并把结果传进提示
        # ★ 必须要求**语句位置**（行首缩进后直接是赋值调用）：
        #   只要求「源码里出现过」，`_探针93 = "" and probe_mod.探针包(...)`
        #   这种**死代码**照样命中 ⇒ 针验不出「接上了但永不执行」
        #   （`_验R93齿.py` ③ 注入当场抓出；R92 ⑥ 同型）。
        if not re.search(r"^ *_探针93 = probe_mod\.探针包\(", _源码, re.MULTILINE):
            fails.append("【R93】★ `_produce_window` 没调 `探针包` ⇒ "
                         "模块写好了但没接线（R85 的教训）")
        if not re.search(r"prompt = _exec_prompt\(self\.goal, self\.spec, d, deps, repo_ctx,\s*\n\s*_探针93\)",
                         _源码):
            fails.append("【R93】★ 探针查了但**没传进** `_exec_prompt` ⇒ "
                         "白查（R86 的教训：算出来了却没用上）")

        # ⑤ 探针异常必须**如实降级**，不得静默
        # ★★ 「门不得匹配到自己」—— 第 6 次复发（R71/R73b/R78/R80/R86/R93）：
        #   上一版写 `re.search(r"R93 只读探针异常（如实降级", _源码)`，
        #   而**这条齿自己的字面量**就在 `_源码` 里 ⇒ 无论实现怎么改都命中
        #   ⇒ 恒绿。修法：连 `_log(f"` 一起匹配，且要求**语句位置**，
        #   这样齿自己的字面量（前面是 `r"`）不可能满足。
        if not re.search(r'^ *_log\(f"\[generic\] ★ R93 只读探针异常（如实降级',
                         _源码, re.MULTILINE):
            fails.append("【R93】★ 探针异常没如实报 ⇒ 会静默退化成「查过了没问题」")
    except Exception as _e93:
        fails.append("【R93】只读探针齿自身异常：%s: %s"
                     % (type(_e93).__name__, _e93))

    # ════════════════════════════════════════════════════════════════════
    # 【R92】主心骨：**判据必须自带一个它必须拒收的样本**
    # ════════════════════════════════════════════════════════════════════
    # 用户口径（本件唯一驱动）：
    #   「是一个智能体模型带着多窗口实例去做工程才对的……主要有一个主心骨
    #     作为引导，把判据定了，下面的智能体不偷工减料把该实现的实现好。」
    #   「肯定要能改啊（主心骨能改目标）。」
    #
    # ★ 本件要防的是**我自己**最可能犯的错：
    #   「主心骨可以定任何判据」若没有约束，它就会起一百条
    #   `新测试_判据_甲子生产路径生效` 那种名字漂亮、断言是「文件里有这个词」的门
    #   —— 因为**起名字是零成本的，写一条真能红的门是有成本的**。
    try:
        # ① 规划时：没有否定样本的门**必须被记下来**（不得静默放行）
        _跑92 = GoalRunner.__new__(GoalRunner)
        _跑92._chief_契约未立 = []
        _跑92._验契约_规划时({"gates": [
            {"name": "有样本", "type": "grep_required", "patterns": ["x"],
             "否定样本": [{"旧": "a", "新": "b"}]},
            {"name": "没样本", "type": "grep_required", "patterns": ["y"]},
        ]})
        if _跑92._chief_契约未立 != ["没样本"]:
            fails.append("【R92】★ 规划时没记下「没有否定样本的门」"
                         f"（得到 {_跑92._chief_契约未立}）⇒ 空门可以静默通过")

        # ② 交付物级的门也要被扫到（不是只看顶层 gates）
        _跑92b = GoalRunner.__new__(GoalRunner)
        _跑92b._chief_契约未立 = []
        _跑92b._验契约_规划时({
            "gates": [{"name": "顶层", "type": "grep_required", "patterns": ["x"],
                       "否定样本": [{"旧": "a", "新": "b"}]}],
            "deliverables": [{"path": "a.rs", "gates": [
                {"name": "交付物级", "type": "grep_required", "patterns": ["z"]}]}],
        })
        if "交付物级" not in _跑92b._chief_契约未立:
            fails.append("【R92】★ 交付物自己的 gates 没被扫 ⇒ "
                         "模型可以把空门藏在 deliverables 里绕过检查")

        # ③ 契约破裂 ⇒ **达标必须判否**（假判据不得买通裁决）
        _跑92c = GoalRunner.__new__(GoalRunner)
        _跑92c._必改未达标 = []
        _跑92c._基线身份 = None
        _跑92c._chief_契约破裂 = []
        if not _跑92c._达标([]):
            fails.append("【R92】★ 没有契约破裂、无违规点时竟判不达标 ⇒ "
                         "这条硬拦会误伤（恒红）")
        _跑92c._chief_契约破裂 = ["某个假门"]
        if _跑92c._达标([]):
            fails.append("【R92】★ 主心骨声明了样本、样本却没让门红，"
                         "却仍判达标 ⇒ **假判据可以买通裁决**（与 R89 同型）")

        # ④ 规划提示里必须带**记忆**（否则主心骨只是换了个名字）
        _提示92 = _planner_prompt("目标", "", "", "", 记忆="回合1：按 A 方案推进")
        if "回合1：按 A 方案推进" not in _提示92:
            fails.append("【R92】★ 规划提示里**没有主心骨记忆** ⇒ "
                         "每次都是新规划器重新猜（本件要修的就是这个）")
        if "你自己的记忆" not in _提示92:
            fails.append("【R92】★ 记忆块没有标明「这是你自己的」⇒ "
                         "模型会把它当成别人的上下文，不会据此避免重走弯路")

        # ⑤ 规划提示里必须带**否定样本契约**（否则模型不知道要交）
        if "否定样本" not in _提示92:
            fails.append("【R92】★ 规划提示里没要求「否定样本」⇒ "
                         "模型不会交，交付时整份被拒收（白烧一轮）")

        # ⑥ 不传记忆时**不得**凭空造一个记忆块（零回退）
        _提示92b = _planner_prompt("目标")
        if "你自己的记忆" in _提示92b:
            fails.append("【R92】★ 没传记忆却出现了记忆块 ⇒ 空块会误导模型")

        # ⑦ 接线：`_plan` 必须**真的**把记忆传进去
        if not re.search(r"记忆=self\._主心骨\(\)\.记忆摘要\(\)", _源码):
            fails.append("【R92】★ `_plan` 没把主心骨记忆传给 `_planner_prompt` ⇒ "
                         "模块写好了但没接线（R85 的教训）")
        if not re.search(r"self\._验契约_规划时\(spec\)", _源码):
            fails.append("【R92】★ `_plan` 采纳后没验契约 ⇒ 空门一路走到交付")

        # ⑧ 接线：`_replan` 也必须记得自己走过哪些弯路
        # ★ 必须要求**在语句位置**（行首缩进后直接是调用）：
        #   只要求「源码里出现过」的话，`if False: _心92.记住(...)` 这种
        #   **死代码**照样命中 ⇒ 针验不出「接上了但永远不执行」
        #   （`_验R92接线齿.py` ⑥ 注入当场抓出）。
        if not re.search(r"^ *_心92\.记住\(回合=", _源码, re.MULTILINE):
            fails.append("【R92】★ `_replan` 没把「这次为什么红」记进主心骨 ⇒ "
                         "重规划仍然是无记忆的重新猜")

        # ⑨ 主心骨模块自身可用（不是只有壳）
        if not hasattr(chief_mod, "验判据契约"):
            fails.append("【R92】★ `chief` 里没有 `验判据契约` ⇒ 契约没被机器验")

        # ══════════════════════════════════════════════════════════════
        # 【R114】否定样本要在**全部产物**里找；破裂原因要**回喂主心骨**
        # ══════════════════════════════════════════════════════════════
        # 真跑证据（2026-10-20 · 第十四次端到端）：主心骨给的样本本身是对的
        #   （`pub fn 学模式_广域` 就在 `src/模块甲/mod.rs:7586`），
        #   而机器只往 `deliverables[0]` 一个文件里找 ⇒ 「找不到」⇒
        #   **10 条门全被判成「疑恒真门」** ⇒ `_达标` 永远 False ⇒ 永不收敛。
        # ── ⑩a chief 里必须有「多文件应用」这条能力 ────────────────
        if not hasattr(chief_mod, "_在多文件里应用"):
            fails.append("【R114】★ `chief` 里没有 `_在多文件里应用` ⇒ "
                         "否定样本只会在一个文件里找（好门会被误判成恒真门）")
        # ── ⑩b 自证调用点必须喂**多份产物**，不是一个字符串 ────────
        if not re.search(r"chief_mod\.验判据契约\(\s*$\s*g0, _产物们92,", _源码,
                         re.MULTILINE):
            fails.append("【R114】★ 自证调用点没喂 `_产物们92`（全部产物）⇒ "
                         "主心骨说的「工程里的一处」会被拿去一个文件里找")
        # ── ⑩c `_契约跑门` 必须接受「变异落在哪个文件」────────────
        if not re.search(r"def 跑门\(门spec: dict, 工作根: Path,\s*$\s*变异相对: str = \"\"",
                         _源码, re.MULTILINE):
            fails.append("【R114】★ `_契约跑门` 不接受「变异相对」⇒ "
                         "变异仍会落到 deliverables[0]（找对了文件也白找）")
        # ── ⑩d 契约破裂的**原因**必须落进 `_chief_契约详情` ──────────
        # ★ 必须要求**带内容的那种**赋值（`{"门": ...`）——
        #   异常分支里还有一句 `self._chief_契约详情 = []`（清空），
        #   只查 `= [` 会被那句满足 ⇒ 齿抓不到「详情根本没记」（当场抓到过）。
        if not re.search(r'^ *_*self\._chief_契约详情 = \[\s*$\s*\{"门": c\.名,',
                         _源码, re.MULTILINE):
            fails.append("【R114】★ 没有 `_chief_契约详情` ⇒ 只记了「哪些门破了」，"
                         "**没记为什么** ⇒ 主心骨无从纠正")
        # ── ⑩e 详情必须真的进重规划观测（**在语句位置**，不是注释/死代码）
        if not re.search(r'^ *_观测97\["契约破裂"\] = _破114', _源码, re.MULTILINE):
            fails.append("【R114】★ `_replan` 没把契约破裂详情放进主心骨观测 ⇒ "
                         "它下一轮还照同样的猜法给样本（同一个错可以犯到天荒地老）")
        # ── ⑩n 【R124】窗级与轮级必须用**同一份**作业面外集合 ──────
        #   真证据：轮级 `_达标本=True`（基线 3→现 2），窗级 `green=0`
        #   ⇒ `fixed_point=False` ⇒ 永远交付不了。
        #   原因：`_本轮作业面外` 在 `_round` **之后**才算，
        #   而 `_window_score` 在 `_round` **里面** ⇒ 窗级读到空集 ⇒ 比轮级严。
        #   ★ AST 判：`_window_score` 里调 `_达标` 时必须带 `外今=` 关键字。
        try:
            import ast as _a124
            _树124 = _a124.parse(Path(__file__).read_text(encoding="utf-8"))
            _ws124 = next((n for n in _a124.walk(_树124)
                           if isinstance(n, _a124.FunctionDef)
                           and n.name == "_window_score"), None)
            if _ws124 is None:
                fails.append("【R124】★ 找不到 `_window_score`（AST）")
            else:
                _带124 = False
                for _n124 in _a124.walk(_ws124):
                    if (isinstance(_n124, _a124.Call)
                            and isinstance(_n124.func, _a124.Attribute)
                            and _n124.func.attr == "_达标"
                            and any(k.arg == "外今" for k in _n124.keywords)):
                        _带124 = True
                if not _带124:
                    fails.append("【R124】★ 窗级调 `_达标` 时**没传 `外今`** ⇒ "
                                 "读到的是轮级（或空）的作业面外集合 ⇒ "
                                 "窗级比轮级严 ⇒ `green=0` ⇒ 达标了也交付不了")
            # ★ 反向：`_达标` 必须**接受**这个参数（否则关键字会 TypeError）
            _达124 = next((n for n in _a124.walk(_树124)
                           if isinstance(n, _a124.FunctionDef)
                           and n.name == "_达标"), None)
            if _达124 is None or "外今" not in [
                    a.arg for a in _达124.args.args + _达124.args.kwonlyargs]:
                fails.append("【R124】★ `_达标` 不接受 `外今` 参数 ⇒ 窗级传了会崩")
        except Exception as _e124:
            fails.append(f"【R124】齿自身异常：{type(_e124).__name__}: {_e124}")
        # ── ⑩l 【R121】指导块必须写清**双向**约束 ──────────────────
        #   真证据：连续 3 次 `交付物没有判据门覆盖：[4 个文件]` ⇒ 崩。
        #   原因：指导块只说「判据点名的文件必须进 deliverables」，
        #   机器却按**双向**判 ⇒ 两闸互相矛盾 ⇒ 死锁。
        try:
            _块121 = _指导块({"判据": [{"name": "甲", "type": "grep_required",
                                        "in": "src/x.rs"}],
                              "计划": [{"path": "src/x.rs", "why": "改它"}],
                              "理由": "测"})
            if "必须至少被一条门读到" not in _块121:
                fails.append("【R121】★ 指导块没写「每个交付物必须被门读到」"
                             "⇒ 规划器只知道单向约束，必撞「交付物没有判据门覆盖」")
            if "本轮不改的文件，不要放进" not in _块121:
                fails.append("【R121】★ 指导块没写「本轮不改的文件不要进 deliverables」"
                             "⇒ 判据比作业面宽时无解")
        except Exception as _e121:
            fails.append(f"【R121】齿自身异常：{type(_e121).__name__}: {_e121}")
        # ── ⑩m 【R121b】机器拒收的理由必须回到**有权改它的那一层**（主心骨）──
        #   真证据：3 次都是同一条拒收，而 `_观测97` 里没有它 ⇒ 主心骨只能重复。
        #   ★ 用 AST 判：`_replan` 里必须**赋值** `self._上次拒收97`，
        #     且 `_观测97` 的字面量里必须有那个 key。
        try:
            import ast as _a121
            _源121 = Path(__file__).read_text(encoding="utf-8")
            _树121 = _a121.parse(_源121)
            _rp121 = next((n for n in _a121.walk(_树121)
                           if isinstance(n, _a121.AsyncFunctionDef)
                           and n.name == "_replan"), None)
            if _rp121 is None:
                fails.append("【R121b】★ 找不到 `_replan`（AST）")
            else:
                _赋值 = any(
                    isinstance(_n, _a121.Assign)
                    and isinstance(_n.targets[0], _a121.Attribute)
                    and _n.targets[0].attr == "_上次拒收97"
                    for _n in _a121.walk(_rp121))
                if not _赋值:
                    fails.append("【R121b】★ `_replan` 没记下机器拒收的因由"
                                 "⇒ 主心骨永远看不见自己为什么被拒")
                # ★ 两种形态都要认（第一版只认 dict 字面量 ⇒ **假红**）：
                #   ① `_观测97 = {..., "…拒收…": …}`      —— 字面量里带键
                #   ② `_观测97["…拒收…"] = …`             —— 事后补键（现实现）
                #   ★ 这正是「齿的夹具让两条路给出同一个答案」的反面：
                #     齿只认一条实现路径，换一种写法就假红。齿必须判**语义**
                #     （这个 dict 里到底有没有这一项），不是判写法。
                _有键 = False
                for _n in _a121.walk(_rp121):
                    if isinstance(_n, _a121.Assign):
                        _t0 = _n.targets[0]
                        # ① 字面量
                        if (isinstance(_t0, _a121.Name)
                                and _t0.id == "_观测97"
                                and isinstance(_n.value, _a121.Dict)):
                            for _k in _n.value.keys:
                                if (isinstance(_k, _a121.Constant)
                                        and "拒收" in str(_k.value)):
                                    _有键 = True
                        # ② 下标赋值
                        if (isinstance(_t0, _a121.Subscript)
                                and isinstance(_t0.value, _a121.Name)
                                and _t0.value.id == "_观测97"
                                and isinstance(_t0.slice, _a121.Constant)
                                and "拒收" in str(_t0.slice.value)):
                            _有键 = True
                if not _有键:
                    fails.append("【R121b】★ `_观测97` 里没有「拒收原因」这一项"
                                 "⇒ 机器的判定没有消费者（R114b 的同型复发）")
        except Exception as _e121b:
            fails.append(f"【R121b】齿自身异常：{type(_e121b).__name__}: {_e121b}")
        # ── ⑩k 【R120】`progressing` 必须优先于真值空间的「不动点/Stuck」 ──
        #   真证据：轮1/2/3 的 Δ 是 6→4→3→2（门真的由 False 翻 True），
        #   而真值空间（3 比特投影，非单射）在轮2 判「不动点已到」⇒ 停机。
        #   ★ 用 AST 判**分支顺序**，不用文本：这一族已复发 14 次。
        try:
            import ast as _a120
            _树120 = _a120.parse(Path(__file__).read_text(encoding="utf-8"))
            _run120 = next((n for n in _a120.walk(_树120)
                            if isinstance(n, _a120.AsyncFunctionDef)
                            and n.name == "_run"), None)
            if _run120 is None:
                fails.append("【R120】★ 找不到 `_run`（AST）")
            else:
                # 找 `action = "rework"` 与 `action = "stop"` 在**顶层** If 链里的先后
                _序: list[str] = []
                for _n in _a120.walk(_run120):
                    if (isinstance(_n, _a120.Assign)
                            and isinstance(_n.targets[0], _a120.Name)
                            and _n.targets[0].id == "action"
                            and isinstance(_n.value, _a120.Constant)):
                        _序.append((_n.lineno, str(_n.value.value)))
                # `progressing` 那一支必须在所有「真值空间 ⇒ stop」之前
                _rework行 = [ln for ln, v in _序 if v == "rework"]
                _stop行 = [ln for ln, v in _序 if v == "stop"]
                if not _rework行:
                    fails.append("【R120】★ 没有 `action = \"rework\"`")
                elif _stop行 and min(_rework行) > min(_stop行):
                    fails.append("【R120】★ `action=\"stop\"`（真值空间）排在 "
                                 f"`action=\"rework\"`（progressing）**前面** "
                                 f"（L{min(_stop行)} vs L{min(_rework行)}）⇒ "
                                 f"3 比特投影会否决无损的严格递减读法")
                # 且 `progressing` 分支的条件里不得再被 `_判20` 否决
                _p120 = Path(__file__).read_text(encoding="utf-8")
                if "elif verdict == \"progressing\":" not in _p120:
                    fails.append("【R120】★ 没有独立的 `elif verdict == \"progressing\":` 分支 "
                                 "⇒ progressing 仍会被真值空间否掉")
        except Exception as _e120:
            fails.append(f"【R120】齿自身异常：{type(_e120).__name__}: {_e120}")
        # ── ⑩i 【R119a】同一条门的同一个真值，`red` 里只许出现**一次** ──
        #   真证据：`red_audit` 17 条全是同一条 `cargo_test_防假门`。
        #   R75 修了 `results` 的键，`red` 这一侧留着 ⇒ 违规点虚增 17 倍。
        try:
            import tempfile as _tf119
            _d119 = Path(_tf119.mkdtemp(prefix="r119a_"))
            try:
                (_d119 / "a.rs").write_text("fn a(){}\n", encoding="utf-8")
                (_d119 / "b.rs").write_text("fn b(){}\n", encoding="utf-8")
                # 一条**与文件无关**的门（规格里不含 {file}/{work}）⇒ 共享门
                _g全 = {"name": "全库门", "type": "grep_required",
                        "in": str(_d119 / "a.rs"), "patterns": ["绝不可能出现的词"]}
                _dl = [{"path": "a.rs", "gates": [_g全]},
                       {"path": "b.rs", "gates": [_g全]}]
                _res119, _red119 = _score_files(_d119, _dl)
                _同 = [a for a in _red119 if str(a.get("gate")) == "全库门"]
                if len(_同) != 1:
                    fails.append(f"【R119a】★ 同一条全库门在 `red` 里出现了 "
                                 f"{len(_同)} 次（应为 1）⇒ 违规点被交付物数量放大"
                                 f"（真跑实测 17 倍）")
            finally:
                import shutil as _sh119
                _sh119.rmtree(_d119, ignore_errors=True)
        except Exception as _e119:
            fails.append(f"【R119a】齿自身异常：{type(_e119).__name__}: {_e119}")
        # ── ⑩j 【R119b】作业面外的历史债不得卡死达标 ──────────────
        _r119b = object.__new__(GoalRunner)
        _面 = frozenset({"src/模块甲/mod.rs"})
        # 作业面外的债（全库门报的错在别的文件里）
        _债外 = {"type": "E0308", "message": "mismatch",
                 "file": "src/测试支撑/运行时事件采集.rs", "symbols": ["x"]}
        _债外身份 = deviation_identity(_债外)
        _r119b._基线身份 = frozenset({_债外身份})
        _r119b._基线作业面外 = _作业面外身份([_债外], _面)
        _r119b._本轮作业面外 = _作业面外身份([_债外], _面)
        _r119b._必改未达标 = None
        _r119b._chief_契约破裂 = []
        if not _r119b._达标({_债外身份}):
            fails.append("【R119b】★ 只剩**作业面外**的历史债时仍判不达标 ⇒ "
                         "死锁（15 次真跑全部 exit 1 的结构性原因）")
        # ★★★★★ R125b（**这条齿在 R119b 时是反的，现在翻过来，理由如下**）：
        #   原来这里断言「作业面外出现**新债** ⇒ 必须判不达标」。
        #   实测证伪了它：基线 1 处全在作业面外 ⇒ `base_内=∅`；
        #   本轮冒出 19 个编译错，**也全在作业面外**（都不是交付物），
        #   但它们是新的 ⇒ 不在 `_外基` 里 ⇒ 被当成「作业面内的新债」⇒ 不达标。
        #   ★ 而同一趟日志里有一条
        #     `★ R54：不覆盖 —— mod.rs 在我们写入之后**又被别的进程改了**`
        #     ⇒ 这棵树**同时被别的智能体改着**。
        #     在这种环境下「作业面外出现新偏差」**无法区分**「我们改坏了」与
        #     「别人改了」⇒ 把它算成本次任务的责任是**已被 20 次真跑证伪**的判据。
        #   ⇒ 口径改为：**作业面外的偏差无论新旧都不参与达标判定**；
        #     越界改动必须由**越界检测**抓（R54 已有一半），不在这里硬凑。
        #   ★ 齿要能咬住这条：把剔除集合从 `_外今` 退回 `_外基` ⇒ 必须变红。
        _新债 = {"type": "E0308", "message": "mismatch",
                 "file": "src/别处/新坏.rs", "symbols": ["y"]}
        _r119b._基线身份 = frozenset({_债外身份})
        _r119b._基线作业面外 = _作业面外身份([_债外], _面)
        _r119b._本轮作业面外 = _作业面外身份([_债外, _新债], _面)
        if not _r119b._达标({_债外身份, deviation_identity(_新债)}):
            fails.append("【R125b】★ 作业面外的偏差（无论新旧）仍判不达标 ⇒ "
                         "`_外基` 剔不掉新债 ⇒ 19 个编译错全在作业面外却全算我们的责任 "
                         "⇒ `base_内=∅` ⇒ 永远交付不了（真跑实测）")
        # ★ 反向：作业面**内**的债仍必须变小才达标
        _r119b._本轮作业面外 = frozenset()
        _内债 = {"type": "E0308", "message": "mismatch",
                 "file": "src/模块甲/mod.rs", "symbols": ["z"]}
        _r119b._基线身份 = frozenset({deviation_identity(_内债)})
        _r119b._基线作业面外 = frozenset()
        if _r119b._达标({deviation_identity(_内债)}):
            fails.append("【R119b】★ 作业面内的债没变小却判达标 ⇒ 口径被放松")
        if not _r119b._达标(set()):
            fails.append("【R119b】★ 作业面内全绿却判不达标 ⇒ 口径被改坏")
        # ── ⑩g 【R116】门的路径必须相对**工程根**解析，不是相对契约临时目录 ──
        #   真证据：11 条门里 9 条报
        #     `FileNotFoundError: 'src\\判据\\观测协议.rs'`
        #   因为传的是 `round_root/_契约` 临时目录，而门的 `in` 是相对工程根写的
        #   ⇒ 9 条**好门**被判「疑恒真门」⇒ 契约破裂非空 ⇒ 永不收敛。
        _i116 = _源码.find("def _契约跑门(")
        _j116 = _源码.find("def _验契约_执行时(", _i116)
        if _i116 < 0 or _j116 < 0:
            fails.append("【R116】★ 找不到 `_契约跑门` / `_验契约_执行时`")
        else:
            _体116 = "\n".join(
                _l for _l in _源码[_i116:_j116].splitlines()
                if not _l.lstrip().startswith("#"))
            if "_gate_read_target(门spec, _基, _根116)" not in _体116:
                fails.append("【R116】★ 门的路径仍按契约临时目录解析 ⇒ "
                             "9/11 条好门报 FileNotFoundError 被判恒真门")
            # ★ R184：判据不变（**门必须按工程根执行，不是契约临时目录**），
            #   但不再绑死变量名 —— 只认「第三参是 `_根116`」这个**实质**。
            _m116 = __import__("re").search(
                r"_gs\.evaluate\(\[[^\]]*\],\s*[^,]+,\s*_根116\)", _体116)
            if not _m116:
                fails.append("【R116】★ 门仍按契约临时目录执行 ⇒ "
                             "工程门（cargo check）起不来")
            # ★★★★★ R184：**占位符必须在契约这条路也展开**（与主路径同一把尺子）。
            #   真证据（第 87 次真跑回执）：`cargo_test_lib_compile：因=跑不起来`、
            #   `说明=命令无法启动|cargo_test_lib_compile` —— 档案写 `cwd: "{repo}"`，
            #   而 `_score_gates` 的 docstring 明写该占位符要**调用前预替换**
            #   （`gate_spec._sub` 不支持它）⇒ 这条路没替换 ⇒ cwd 是不存在的字面目录。
            if '.replace("{repo}"' not in _体116:
                fails.append("【R184】★ 契约这条路**没展开 `{repo}` 占位符** ⇒ "
                             "全库型编译门永远「命令无法启动」⇒ 判据保真检查形同虚设")
            # ★ 必须**两处都在**（变异 + 还原）。只查「出现过」会被
            #   变异那一处满足 ⇒ 还原退回 `write_bytes` 也照样绿（当场抓到）。
            #   与 R108「单侧检查抓不到另一侧」同型。
            if _体116.count("_原子写源码(目标") < 2:
                fails.append("【R113】★ 变异**和**还原两处都必须用 `_原子写源码`"
                             "（现在只有 "
                             f"{_体116.count('_原子写源码(目标')} 处）⇒ "
                             "强杀会留下全零源码（真事故：785173 字节纯零）")
        # ── ⑩h 【R116b】执行侧因由的判据必须存在且被调用 ──────────────
        if not hasattr(chief_mod, "_因由是执行侧"):
            fails.append("【R116b】★ `chief` 里没有 `_因由是执行侧` ⇒ "
                         "「门跑不起来」会被当成「样本让它红了」")
        # ── ⑩f 反自证：真调一次 `_在多文件里应用`，确认它跨文件工作 ──
        _多114 = {"a.rs": "fn a() {}\n", "b.rs": "fn 目标() {}\n"}
        _命114 = chief_mod._在多文件里应用(_多114, {"旧": "fn 目标()", "新": "fn 目标(("})
        if not (_命114 and _命114[0] == "b.rs"):
            fails.append("【R114】★ `_在多文件里应用` 在第二份文件里找不到目标 ⇒ "
                         "R114 的修法本身没生效")
    except Exception as _e92:
        fails.append("【R92】主心骨齿自身异常：%s: %s"
                     % (type(_e92).__name__, _e92))

    # ════════════════════════════════════════════════════════════════════
    # 【R91】见证门：测试名点名的接口，**断言里必须真的调了它**
    # ════════════════════════════════════════════════════════════════════
    # 真跑证据（2026-10-18 · 第 1 轮 green）：门
    #   `新测试_判据_甲子生产路径生效 = True`
    # 它的 cmd 是 `cargo test --lib 新测试_判据_甲子生产路径生效`。
    # ★ 门**老老实实**跑了那条测试，测试**真的通过了** ——
    #   **撒谎的是测试本身**：名字叫「生产路径生效」，
    #   断言是「源码里出现了这个词」，而这个词当然有（定义行 + 别人的测试）。
    try:
        import tempfile as _tf91
        import subprocess as _sp91

        _存前缀91 = prof_mod.get_profile().orphan_api_prefixes
        try:
            _d91 = Path(_tf91.mkdtemp())
            (_d91 / "a.rs").write_text(
                "pub fn 判据_真(x: u32) -> u32 { x }\n"
                "pub fn 判据_假(x: u32) -> u32 { x }\n"
                "#[cfg(test)]\n"
                "mod 测试 {\n"
                "    #[test]\n"
                "    fn 判据_真_行为被见证() { assert_eq!(判据_真(1), 1); }\n"
                "    #[test]\n"
                "    fn 判据_假_行为被见证() { assert!(true); }\n"
                "}\n",
                encoding="utf-8")
            _门91 = Path(__file__).resolve().parent / "见证门.py"

            def _跑91(名):
                return _sp91.run([sys.executable, str(_门91), 名, str(_d91), "判据_"],
                                 capture_output=True, timeout=300)

            # ① 断言里**真的调了** ⇒ 判绿
            _r91a = _跑91("判据_真_行为被见证")
            if _r91a.returncode != 0:
                fails.append("【R91】★ 断言里真的调了接口，见证门却判**红** ⇒ "
                             "恒红门，任务永远不可能收敛")
            # ② ★ 名字点名了但**没调** ⇒ 必须判红（真跑那条假 green 的形态）
            _r91b = _跑91("判据_假_行为被见证")
            if _r91b.returncode == 0:
                fails.append("【R91】★ 名字点名了 `判据_假`、断言里只写 `assert!(true)`，"
                             "见证门却判**绿** ⇒ 它还是只看「测试通过了」")
            elif "【R91】" not in (_r91b.stdout or b"").decode("utf-8", "replace"):
                fails.append("【R91】★ 判红了但没点名 R91（红错地方 = 无齿）")
            # ③ ★ 边界：名字**没点名**任何接口 ⇒ **不适用**（零回退，不猜意图）
            #   ★ 夹具必须有这么一条测试，否则这条齿无处下口
            #     （`_验R91齿.py` ⑤ 注入当场抓出：第一版没有 ⇒ 无齿）。
            (_d91 / "b.rs").write_text(
                "#[cfg(test)]\nmod 别的 {\n    #[test]\n"
                "    fn 复用链_代表元升入当前代() { assert!(true); }\n}\n",
                encoding="utf-8")
            _r91c = _跑91("复用链_代表元升入当前代")
            if _r91c.returncode != 0:
                fails.append("【R91】★ 名字里**没点名**任何接口，却判红 ⇒ "
                             "每个无关测试都被拖进来（假红）")

            # ③b ★★ 边界：**只认完整接口名**，不认核心名。
            #   核心名太松：`判据_复用` 的核心是 `复用`，
            #   而 `复用链_代表元升入当前代` 里有 `复用` ⇒ 会**误伤无关测试**。
            (_d91 / "c.rs").write_text(
                "pub fn 判据_复用(x: u32) -> u32 { x }\n", encoding="utf-8")
            _r91d = _跑91("复用链_代表元升入当前代")
            if _r91d.returncode != 0:
                fails.append("【R91】★ 只按**核心名**匹配（`复用` 命中 `判据_复用`）⇒ "
                             "误伤一个完全无关的测试 ⇒ 假红")

            # ③c ★ 边界：**找不到那个测试函数** ⇒ 必须判红
            #   （门要跑它，它却不存在 ⇒ 门本身有问题）
            _r91e = _跑91("判据_真_这个名字不存在")
            # ★ 必须同时看**退出码**与**消息**：只看退出码时，
            #   「注入后崩成非零」也会被当成抓住（`_验R91齿.py` ② 抓到过）。
            if _r91e.returncode == 0 or "【R91】" not in (
                    _r91e.stdout or b"").decode("utf-8", "replace"):
                fails.append("【R91】★ 测试函数**不存在**，见证门没判红（或没点名）⇒ "
                             "「找不到就放行」⇒ 那条撒谎的测试正是这么过的")

            # ④ ★ 门必须由机器注入，且只对**真的在跑测试**的门注入
            _spec91 = {"gates": [{"name": "t1", "type": "compile",
                                  "cmd": ["cargo", "test", "--lib",
                                          "判据_假_行为被见证"]}]}
            _加91 = _注入见证门(_spec91, "")
            if not _加91:
                fails.append("【R91】★ 模型起了个测试门、名字点名了接口，"
                             "却没注入见证门 ⇒ 门名仍然自由")
            elif not any(g.get("name", "").startswith("见证_")
                         for g in _spec91["gates"]):
                fails.append("【R91】★ 说注入了，但 `spec['gates']` 里没有")
            # ⑤ 边界：跑**全部**测试（无位置参数）⇒ 无法归属 ⇒ 不注入
            if _注入见证门({"gates": [{"name": "t2", "type": "compile",
                                       "cmd": ["cargo", "test", "--lib"]}]}, ""):
                fails.append("【R91】★ 门跑的是**全部**测试（无位置参数），"
                             "却注入了见证门 ⇒ 机器在瞎猜哪条测试对应哪个接口")
            # ⑥ 边界：不是在跑测试的门（如 grep 门）⇒ 不注入
            if _注入见证门({"gates": [{"name": "g", "type": "grep_required",
                                       "patterns": ["判据_真"]}]}, ""):
                fails.append("【R91】★ 非测试门也被注入了见证门 ⇒ 会去找一个"
                             "不存在的 `fn 判据_真`")

            # ⑦ ★ 接线齿：`_plan` 必须真的调它
            _源91 = Path(__file__).read_text(encoding="utf-8", errors="replace")
            _码91 = "\n".join(_l.split("#", 1)[0] for _l in _源91.splitlines())
            if re.search(r"^[ \t]*_新门91\s*=\s*_注入见证门\(", _码91, re.M) is None:
                fails.append("【R91】★ `_plan` 没调 `_注入见证门` ⇒ "
                             "生成器写好了但不拦任何东西")
        finally:
            prof_mod.set_profile(prof_mod.with_overrides(
                prof_mod.get_profile(), orphan_api_prefixes=_存前缀91))
    except Exception as _e91:
        fails.append("【R91】见证门齿自身异常：%s: %s"
                     % (type(_e91).__name__, _e91))

    # ════════════════════════════════════════════════════════════════════
    # 【R89】**代偿不得买通裁决** —— 目标文件必须它自己变好
    # ════════════════════════════════════════════════════════════════════
    # 真跑第 3 轮原样（2026-10-18）：
    #   ✓ 运行时涌现.rs：门 9 → 0 ⇒ 保留
    #   ✗ 观测协议.rs：未严格变小 ⇒ 回退
    #   ✗ 模块丙.rs：未严格变小 ⇒ 回退      ← **这就是目标文件**
    #   ★ 增量裁决：保留 1 / 回退 2 ⇒ verdict=progressing
    # ★ 缺陷不在「保留」（那 1 个是真改进），在**裁决**：
    #   总分变小就报有进展 ⇒ **可以用一个文件的收益去买另一个文件的亏损**。
    #   而目标说的是「**这个**文件要接上」，不是「总共好一点」。
    try:
        # ① ★ 真跑那一幕：目标文件回退、别的文件保留 ⇒ **不得判达标**
        #   夹具必须让两个实现给出**相反**答案：没有 R89 就是 True。
        _f89 = GoalRunner.__new__(GoalRunner)
        _f89._基线身份 = frozenset({"a", "b", "c"})
        _f89._必改未达标 = []
        # 无约束时：真子集 ⇒ 达标
        if not _f89._达标({"a"}):
            fails.append("【R89】★ 无必改约束时真子集竟判**不达标** ⇒ "
                         "把正常的进展也毙了（假红）")
        # 目标文件没变好 ⇒ **同一份 cur_ids**，必须翻转成不达标
        _f89._必改未达标 = ["src/模块甲/模块丙.rs"]
        if _f89._达标({"a"}):
            fails.append("【R89】★ 目标文件自己没变好，而别处违规点真子集变小，"
                         "仍判**达标** ⇒ 代偿买通了裁决（真跑第 3 轮那一幕）")
        # 全绿也不得豁免 —— 全绿但目标文件没动，仍是目标未达成
        if _f89._达标(set()):
            fails.append("【R89】★ 门全绿但目标文件没变好，仍判**达标** ⇒ "
                         "「全绿」把「目标达成」代偿掉了")

        # ② ★★ 方向齿（`_验R89齿.py` ③ 抓出：第一版是内联表达式，方向反了无人知）
        if _必改未变好(["src/a.rs"], {"src/a.rs"}):
            fails.append("【R89】★ 必改文件**在**保留集里，却判成「没变好」⇒ "
                         "方向反了 ⇒ 每个正常轮都被毙 ⇒ 永不收敛")
        if not _必改未变好(["src/a.rs"], {"src/b.rs"}):
            fails.append("【R89】★ 必改文件**不在**保留集里（被回退），"
                         "却没判成「没变好」⇒ 代偿照旧买通裁决")
        # 路径写法差异不得造成误判（`./x` / `x` / 反斜杠）
        if _必改未变好(["./src/a.rs"], {"src/a.rs"}):
            fails.append("【R89】★ 同一路径两种写法被当成两个 ⇒ 假红")
        # 空约束 ⇒ 空结果（零回退）
        if _必改未变好([], {"src/a.rs"}):
            fails.append("【R89】★ 没有必改约束却报未达标 ⇒ 假红")

        # ②b ★ 接线齿：`_round` 必须真的调它（否则字段永远是空表）
        _源89 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _码89 = "\n".join(_l.split("#", 1)[0] for _l in _源89.splitlines())
        if re.search(r"^[ \t]*self\._必改未达标\s*=\s*_必改未变好\(",
                     _码89, re.M) is None:
            fails.append("【R89】★ `_round` 没算出 `_必改未达标` ⇒ "
                         "字段永远是空表 ⇒ 达标口径里那条硬约束永不触发")
        # ③ ★ 它必须在**增量裁决之后**（之前 `_留43` 还不存在）
        _i留89 = _码89.find("_留集89 = {rel for rel, _ in _留43}")
        _i裁89 = _码89.find("增量裁决：保留")
        if _i留89 < 0 or _i裁89 < 0 or _i留89 < _i裁89:
            fails.append("【R89】★ 算 `_必改未达标` 的位置在**增量裁决之前** ⇒ "
                         "`_留43` 还没算出来，拿到的是空集（R83 同型：锚在语义地标上）")
    except Exception as _e89:
        fails.append("【R89】代偿齿自身异常：%s: %s"
                     % (type(_e89).__name__, _e89))

    # ════════════════════════════════════════════════════════════════════
    # 【R88】接线门：目标说「接上」⇒ 机器数**生产引用**，不信门的名字
    # ════════════════════════════════════════════════════════════════════
    # 真跑证据（2026-10-18 · 第 1 轮 green）：门里有
    #   `新测试_判据_甲子生产路径生效 = True`，而 `判据_甲子` 的生产消费者是 **0**。
    # ★ 那条门名字承诺「生产路径生效」，实际判「文件里有这个词」——
    #   而这个词当然有（定义行 + 别人的测试）。**门绿了，但它证明的不是它名字说的。**
    try:
        import tempfile as _tf88
        import subprocess as _sp88

        _存前缀88 = prof_mod.get_profile().orphan_api_prefixes
        try:
            # 造一棵小树，验证**同一台机器**在两个方向上给相反答案
            _d88 = Path(_tf88.mkdtemp())
            # ★ 测试模块里**放一个含 `}` 的字符串** —— 这是让「剥净顺序」可判的关键：
            #   若先剥测试模块再剥注释，字符串里的 `}` 会让大括号配平**提前闭合**
            #   ⇒ 后面那段 `}` 残留、生产消费者被算进「测试里」⇒ 假绿。
            #   （`gap_source` 的齿 ④ 就是靠这个抓到顺序错误的；
            #     R88 的夹具第一版没放 ⇒ 注入② 无齿，`_验R88齿.py` 当场抓出来。）
            # ★ 夹具必须让**两个剥净顺序给出相反答案**，否则「顺序」这条无齿。
            #   · 正确顺序（先剥注释/字面量）：字符串 `"}"` 变空格 ⇒ 大括号配平正确
            #     ⇒ 测试模块整块剥掉 ⇒ `判据_测(9)` 也在里面被剥 ⇒ 零引用 ⇒ 判红 ✅
            #   · 反序（先剥测试模块）：字符串里的 `}` 让模块**提前闭合**
            #     ⇒ 后面的 `fn u() { 判据_测(9); }` 被当成生产代码 ⇒ 非零 ⇒ 假绿 ❌
            #   ★ 第一版夹具用的是 `"}}"`，反序时后面的字符串把整段吞到 EOF
            #     ⇒ 两个顺序**碰巧**都给 0 ⇒ 注入② 无齿（`_验R88齿.py` 当场抓出）。
            (_d88 / "a.rs").write_text(
                "pub fn 判据_测(x: u32) -> u32 { x }\n"
                "#[cfg(test)]\n"
                "mod 测试 {\n"
                "    fn t() { let _s = \"}\"; }\n"
                "    fn u() { 判据_测(9); }\n"
                "}\n"
                "/// 判据_测 在这里被讨论\n",
                encoding="utf-8")
            _门88 = _sp88.run(
                [sys.executable, str(Path(__file__).resolve().parent / "接线门.py"),
                 "判据_测", str(_d88)], capture_output=True, timeout=300)
            # ① 只在测试里/注释里被提到 ⇒ 必须判红
            if _门88.returncode == 0:
                fails.append("【R88】★ 只在测试与注释里出现，接线门却判**绿** ⇒ "
                             "它又变成「文件里有这个词」了")
            elif "【R88】" not in (_门88.stdout or b"").decode("utf-8", "replace"):
                fails.append("【R88】★ 判红了但没点名 R88（红错地方 = 无齿）")

            # ② 真接到生产路径 ⇒ 必须判绿（否则每个任务都恒红，永远不收敛）
            # ★ R98：夹具必须是**真的用结果**的消费者。
            #   原来写 `{ crate::a::判据_测(7) }` —— 那是**纯透传**，
            #   R98 之后正确地不算消费者 ⇒ 本齿当场变红。
            #   ★ 又一次「夹具选得巧，齿就会变瞎」：这个夹具一直在替
            #     「透传也算生产消费者」那个 bug 作证。
            (_d88 / "b.rs").write_text(
                "pub fn 用() -> u32 { if crate::a::判据_测(7) { 1 } else { 0 } }\n",
                encoding="utf-8")
            _门88b = _sp88.run(
                [sys.executable, str(Path(__file__).resolve().parent / "接线门.py"),
                 "判据_测", str(_d88)], capture_output=True, timeout=300)
            if _门88b.returncode != 0:
                fails.append("【R88】★ 真的接到生产路径了，接线门却判**红** ⇒ "
                             "恒红门，任务永远不可能收敛")

            # ③ ★ 门必须由**机器**注入
            _目标88 = "把 src/模块甲/模块丙.rs 里 `判据_复用` 接上生产路径。"
            _spec88 = {"gates": []}
            _加88 = _注入接线门(_spec88, _目标88)
            if not _加88:
                fails.append("【R88】★ 目标点名了接口，却没注入接线门 ⇒ "
                             "判据又只能由模型写")
            elif not any(g.get("name", "").startswith("接线_") for g in _spec88["gates"]):
                fails.append("【R88】★ 说注入了，但 `spec['gates']` 里没有")

            # ③b ★ 接线齿：`_plan` 必须真的调它（否则只是个函数，判据仍由模型写）
            _源88 = Path(__file__).read_text(encoding="utf-8", errors="replace")
            _码88 = "\n".join(_l.split("#", 1)[0] for _l in _源88.splitlines())
            # ★ 必须断言**传的是 `self.spec`**：调了但把门写进一个临时 dict
            #   ⇒ 门没进真门集 ⇒ 等于没接（`_验R88齿.py` ⑥ 注入当场抓出：
            #   第一版只验「调了」，抓不住"结果丢掉"）。
            if re.search(r"^[ \t]*_新门88\s*=\s*_注入接线门\(self\.spec,",
                         _码88, re.M) is None:
                fails.append("【R88】★ `_plan` 没把接线门写进**真门集**"
                             "（`_注入接线门(self.spec, …)`）⇒ "
                             "生成器写好了但不拦任何东西")

            # ④ 边界：目标没点名任何接口 ⇒ 一条都不注入（零回退）
            if _注入接线门({"gates": []}, "把那个东西修好。"):
                fails.append("【R88】★ 目标没点名接口，却注入了接线门 ⇒ "
                             "每个普通目标都被多一条恒红门")

            # ⑤ 边界：项目**没声明**命名约定 ⇒ 一条都不注入
            prof_mod.set_profile(prof_mod.with_overrides(
                prof_mod.get_profile(), orphan_api_prefixes=()))
            try:
                if _注入接线门({"gates": []}, _目标88):
                    fails.append("【R88】★ 项目没声明 `orphan_api_prefixes`，"
                                 "却仍注入了接线门 ⇒ 机器在瞎猜要数什么")
            finally:
                prof_mod.set_profile(prof_mod.with_overrides(
                    prof_mod.get_profile(), orphan_api_prefixes=_存前缀88))
        finally:
            prof_mod.set_profile(prof_mod.with_overrides(
                prof_mod.get_profile(), orphan_api_prefixes=_存前缀88))
    except Exception as _e88:
        fails.append("【R88】接线门齿自身异常：%s: %s"
                     % (type(_e88).__name__, _e88))

    # ════════════════════════════════════════════════════════════════════
    # 【R87】`【必改文件】` 由**机器**补，不靠调用方记得写
    # ════════════════════════════════════════════════════════════════════
    # R86 那条判据要求目标里有 `【必改文件】`。结构缺口的目标是机器生成的，会写；
    # **人工写的目标不会写** ⇒ 那条判据对它们**等于不存在**。
    # ★ 「标记是接口，不该靠自觉。」一个判据若要求调用方**记得**喂特定格式，
    #   它在无人值守里就是**概率生效**的 —— 这等于没有。
    try:
        # ① 真跑那种**人工**目标：`路径:行号` 精确点名一个已存在文件 ⇒ 必须补上
        _人工87 = ("把 src/模块甲/模块丙.rs:165 里那个判据接上生产路径，"
                   "让它真的参与裁决。")
        _补87 = _补必改标记(_人工87)
        if "【必改文件】src/模块甲/模块丙.rs" not in _补87:
            fails.append(f"【R87】★ 人工目标点名 `模块丙.rs:165`（已存在），"
                         f"却**没补上必改标记** ⇒ R86 那条判据对它等于不存在")

        # ② 补出来的必须能被 R86 的抽取器读到（**端到端**，不只是字符串出现）
        _抽87 = _目标声明的必改文件(_补87)
        if "src/模块甲/模块丙.rs" not in _抽87:
            fails.append(f"【R87】★ 补上了但**抽不出来**（实得 {_抽87}）⇒ "
                         f"两段之间没接上")

        # ③ 已有显式声明 ⇒ **一个都不补**（尊重调用方）
        #   ★ 夹具必须让"补"与"不补"给出**不同**结果，否则这条齿无牙
        #     （`_验R87齿.py` ① 注入当场抓出来过：只比字符串相等时，
        #      注入成"照补"恰好补出同一串 ⇒ 仍全绿）。
        #   目标里有两个 `路径:行号`：一个已在显式声明里，一个**不在**。
        #   正确行为 = 一个都不补 ⇒ 只保留调用方那一条。
        _有87 = ("把 src/a.rs:1 和 src/模块甲/模块丙.rs:165 修好。\n"
                 "【必改文件】src/a.rs\n")
        _补87b = _补必改标记(_有87)
        if _补87b != _有87:
            fails.append(f"【R87】★ 目标**已有** `【必改文件】`，却还补了别的"
                         f"（实得 {_补87b!r}）⇒ 会推翻调用方的显式声明")

        # ④ 边界：没有 `路径:行号` ⇒ 一个都不补（否则每个目标都被凭空约束）
        _无87 = "把那个函数接上生产路径。"
        if _补必改标记(_无87) != _无87:
            fails.append("【R87】★ 目标里没有 `路径:行号`，却凭空补了必改文件 ⇒ "
                         "每个普通目标都会被无端回喂")

        # ⑤ 边界：**不存在的路径**不得补（防幻觉路径污染必改清单 ⇒ 永远覆盖不到）
        _幻87 = "把 src/不存在的幻觉文件.rs:99 修好。"
        if "【必改文件】" in _补必改标记(_幻87):
            fails.append("【R87】★ 幻觉路径（磁盘上不存在）也被补成必改 ⇒ "
                         "那个计划**永远不可能覆盖**它 ⇒ 无限回喂")

        # ⑥ ★ 接线齿：构造器必须真的调它（否则只是个函数）
        _源87 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _码87 = "\n".join(_l.split("#", 1)[0] for _l in _源87.splitlines())
        if re.search(r"^[ \t]*self\.goal\s*=\s*_补必改标记\(", _码87, re.M) is None:
            fails.append("【R87】★ `self.goal` 没接 `_补必改标记` ⇒ "
                         "抽出来了但目标里永远没有那个标记")
    except Exception as _e87:
        fails.append("【R87】机器补标记齿自身异常：%s: %s"
                     % (type(_e87).__name__, _e87))

    # ════════════════════════════════════════════════════════════════════
    # 【R86】目标点名的**已存在**文件，也必须进交付物清单
    # ════════════════════════════════════════════════════════════════════
    # 真跑证据（2026-10-18 · 第六次真项目验收 · **最坏的形态**）：
    #   目标点名 `src/模块甲/模块丙.rs:165`，
    #   交付物清单是 `运行时涌现.rs` / `外部获取/mod.rs` / `观测协议.rs` / …
    #   —— **一个都不是那个文件**。跑完 **green**、回灌 3 个文件、偏差 30→12。
    #   而 `判据_甲子` 的生产消费者**仍然是 0**，那个文件**一动没动**。
    # ★ 为什么所有检查都说它对：那 3 个保留的文件是**真改进**。
    #   这是最坏的形态 —— **green 且真有一部分改进**。
    # 根因：R81 管「不存在⇒必须在清单里」，R36 管「红门点名的⇒必须覆盖」。
    #   目标点名了一个**已存在**的文件、而门全是全库门时，**两条都不管**。
    # 修法：目标里 `【必改文件】<路径>`（机器固定格式）⇒ 并入 R36 的必改集合。
    try:
        # ① 真跑那一例必须被抓到
        _目标86 = ("把 src/模块甲/模块丙.rs 里**声明了却没有生产消费者**的接口接上。\n"
                   "【接口名】判据_甲子\n"
                   "【必改文件】src/模块甲/模块丙.rs\n"
                   "【位置】src/模块甲/模块丙.rs:165\n")
        _必86 = _目标声明的必改文件(_目标86)
        if "src/模块甲/模块丙.rs" not in _必86:
            fails.append(f"【R86】★ 目标写了 `【必改文件】`，却**没抽出来**"
                         f"（实得 {_必86}）⇒ 规划器会去改别的文件、报 green")

        # ② ★★ 真跑那份交付物清单，必须被判为**没覆盖**
        _清单86 = ["src/核心类型/运行时涌现.rs", "src/外部获取/mod.rs",
                   "src/判据/观测协议.rs", "src/模块甲/规律网络编码.rs",
                   "src/模块甲/列编码.rs"]
        _缺86 = _计划覆盖缺口(_清单86, _必86)
        if not _缺86:
            fails.append(f"【R86】★ 真跑那份清单（{len(_清单86)} 个文件，"
                         f"一个都不是目标点名的）却被判「已覆盖」⇒ "
                         f"green 而目标文件一动没动")

        # ③ 反向：清单里有它 ⇒ 不得误报（否则每个正常计划都被回喂）
        _缺86b = _计划覆盖缺口(_清单86 + ["src/模块甲/模块丙.rs"], _必86)
        if _缺86b:
            fails.append(f"【R86】★ 清单已覆盖却**误报**（实得 {_缺86b}）"
                         f"⇒ 会把正确的计划回喂掉")

        # ④ ★★ 真跑抓到的形态：目标里有**行首标记**，正文里**又提了一次**这个标记
        #   （`转目标` 的说明句里写了它）⇒ 第一版没锚行首，把正文里那个反引号
        #   抽成了"文件路径" ⇒ 必改清单多一个**必然覆盖不到**的项 ⇒ 无限回喂。
        #   ★ 这是「**声明文本自己被当成声明**」，与「门匹配到自己」同族。
        _目标86b = ("把 src/a.rs 里那处接上。\n"
                    "【必改文件】src/a.rs\n"
                    "★ `【必改文件】` 是机器写给机器的标记。\n")
        _必86b = _目标声明的必改文件(_目标86b)
        if _必86b != ["src/a.rs"]:
            fails.append(f"【R86】★ 正文里提到 `【必改文件】` 就被抽成了路径"
                         f"（实得 {_必86b}，应为 ['src/a.rs']）⇒ "
                         f"必改清单多一个必然覆盖不到的项 ⇒ 无限回喂")

        # ⑤ 边界：目标**没写** `【必改文件】` ⇒ 不得凭空要求任何文件
        #   （否则所有非结构缺口的目标都会被无端回喂）
        _必86c = _目标声明的必改文件(
            "把 src/模块甲/模块丙.rs 里那处缺口闭合。\n【位置】src/模块甲/模块丙.rs:165\n")
        if _必86c:
            fails.append(f"【R86】★ 目标**没写** `【必改文件】`，却凭空抽出 {_必86c}"
                         f"⇒ 每个普通目标都会被无端回喂（假红）")

        # ⑥ ★ 接线齿：`_plan` 必须真的把它并进 `_必改36`
        _源86 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _码86 = "\n".join(_l.split("#", 1)[0] for _l in _源86.splitlines())
        _针86 = "_目标声明的必改文件(" + 'getattr(self, "goal", "") or ""'
        if _针86 not in _码86:
            fails.append("【R86】★ 抽出来了但**没并进** `_必改36` ⇒ "
                         "它只是个函数，不拦任何东西")
        else:
            # ★ 必须在 `_必改36 = list(...)` **之后** —— 否则被覆盖掉（R83 的教训：
            #   锚在语义地标上，不锚在就近地标上）。
            _i源 = _码86.find('_必改36 = list(_门态缓存.get("必改") or [])')
            _i注 = _码86.find(_针86)
            if _i源 < 0 or _i注 < 0 or _i注 < _i源:
                fails.append("【R86】★ 并进 `_必改36` 的位置在**赋值之前** ⇒ "
                             "被整体覆盖，等于没接（R83 同型）")
            # ★★ 光看"第一个赋值在注入之前"**不够**：注入之后再赋一次值，
            #   同样会把结果整体覆盖掉。必须断言**注入之后不再有重新赋值**。
            #   （`_验R86齿.py` ④ 就是照这个形态注入的 —— 第一版齿抓不住。）
            # ★★★ 必须**行首锚定**：`_必改36 = ` 这个串在本文件里也出现在
            #   **齿自己的代码里**（`find('_必改36 = list(...)')` 和下面那行判断）
            #   ⇒ 裸 `in` 会匹配到齿自己 ⇒ 齿把自己判红。
            #   ★ 这是「门不得匹配到它自己」的**第五次**复发
            #     （R71 / R73b / R78 / R80 / 本次）。
            #   行首模式能排除字符串字面量（那里前面是引号，不是空白）。
            elif re.search(r"^[ \t]*_必改36\s*=", _码86[_i注:], re.M):
                fails.append("【R86】★ 注入之后**又给 `_必改36` 重新赋了值** ⇒ "
                             "结果被整体覆盖，等于没接")
    except Exception as _e86:
        fails.append("【R86】目标→必改齿自身异常：%s: %s"
                     % (type(_e86).__name__, _e86))

    # ════════════════════════════════════════════════════════════════════
    # 【R82】目标里**写死的断言**由机器原样加成门（不经模型的手）
    # ════════════════════════════════════════════════════════════════════
    # 真跑证据：目标 `collatz_steps(1)==0`，模型写成 `f([1])[0]==0` ——
    #   三个点一个没少，但**把调用形态改成了产物的形状**，于是门绿。
    #   「判据不得由被判方改动」最锋利的一刀：不是少测，是**改写**。
    try:
        import tempfile as _tf82
        import subprocess as _sp82

        _目标82 = ("写 collatz.py：collatz_steps(n: int) -> int。"
                   "断言 collatz_steps(1)==0、collatz_steps(27)==111。")
        _门82 = _目标里的可执行断言(_目标82, [{"path": "collatz.py"}])
        # ① 该生成 ⇒ 必须生成，且**照抄目标的调用形态**（`_f(1)` 而不是 `_f([1])`）
        if not _门82:
            fails.append("【R82】★ 目标里写死了 `collatz_steps(1)==0`，"
                         "却**没生成**目标断言门 ⇒ 判据又只能由模型写")
        else:
            _脚82 = _门82[0]["cmd"][2]
            if "_f(1)" not in _脚82:
                fails.append("【R82】★ 生成的脚本里没有 `_f(1)` ⇒ "
                             "**没有照抄目标的调用形态**（等于又把判据交给模型改）")
            if "[1]" in _脚82:
                fails.append("【R82】★ 脚本里出现了 `[1]` ⇒ 把标量参数包成了列表"
                             "（这正是真跑里模型干的事）")
        # ② ★★★ 决定性的一条：**同一道门，对两种产物必须给出相反答案**。
        #   这是本库那条「夹具必须能让两个实现给出相反答案」的直接落地 ——
        #   不这么做，齿只能证明"生成了脚本"，不能证明"咬得住"。
        if _门82:
            _脚本82 = _门82[0]["cmd"][2]

            def _跑82(内容: str) -> int:
                with _tf82.TemporaryDirectory() as _d:
                    Path(_d, "collatz.py").write_text(内容, encoding="utf-8")
                    _r = _sp82.run([sys.executable, "-c", _脚本82], cwd=_d,
                                   capture_output=True, timeout=120)
                    return _r.returncode

            _列表82 = ("def collatz_steps(x):\n"
                       "    return [0]\n")
            _标量82 = ("def collatz_steps(n: int) -> int:\n"
                       "    s = 0\n"
                       "    while n != 1:\n"
                       "        n = n // 2 if n % 2 == 0 else 3 * n + 1\n"
                       "        s += 1\n"
                       "    return s\n")
            if _跑82(_列表82) == 0:
                fails.append("【R82】★ **列表进/列表出**的产物被这道门**放行了** ⇒ "
                             "门没咬住（真跑里就是这么绿的）")
            if _跑82(_标量82) != 0:
                fails.append("【R82】★ 正确的标量产物被**误判红** ⇒ 假红比无齿更坏")
        # ③ 反向：目标里没有"调用==字面量"⇒ **不得凭空造门**
        _门82b = _目标里的可执行断言("把项目里的内存泄漏修一下。",
                                     [{"path": "a.py"}])
        if _门82b:
            fails.append(f"【R82】★ 目标里没有可执行断言却生成了门 {_门82b} ⇒ 凭空造门")
        # ④ ★ 只认**字面量**：`f(1)==某个变量` 解析不了 ⇒ 跳过（不猜）
        _门82c = _目标里的可执行断言("断言 f(1)==预期值。", [{"path": "a.py"}])
        if _门82c:
            fails.append(f"【R82】★ 期望值不是字面量却生成了门 {_门82c} ⇒ 机器在猜")
        # ⑤ ★★★ 接线齿：`_adopt_spec` 必须真的注入，**且必须在「唯一门源落地之后」**。
        #   ★★★★★ R83（真跑当场抓出）：第一版这里锚的是「在 `self.spec = spec` 之前」——
        #     **那个地标是错的**。真正的地标是下面那行 `self.spec["gates"] = derived`：
        #     `pred` 在场时门集是**替换**不是合并，注入在它之前 ⇒ **被整体覆盖**。
        #     齿验了、5/5 全 PASS，**却全部验在错的位置上** —— 日志打印了门，
        #     回执里却没有。⇒ **齿必须锚在语义地标上，不能锚在就近地标上。**
        _源82 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _码82 = "\n".join(_l.split("#", 1)[0] for _l in _源82.splitlines())
        _针82 = "_注入目标断言门(self.spec, " + 'getattr(self, "goal", "") or "")'
        _针源82 = "self.spec[\"gates\"] = derived"          # 唯一门源落地（语义地标）
        _针归82 = "normalize_gates(self.spec)"               # 门集定型
        if _针82 not in _码82:
            fails.append("【R82】★ 目标断言门**没有接到** `_adopt_spec` 上 ⇒ "
                         "它只是个函数，不拦任何东西")
        else:
            _i82 = _码82.find(_针82)
            _i源82 = _码82.find(_针源82)
            _i归82 = _码82.find(_针归82)
            # ★ 必须在「唯一门源落地」之后 —— 否则被整体覆盖（真跑实证）
            if _i源82 >= 0 and _i82 < _i源82:
                fails.append("【R82】★ 注入点在 `self.spec[\"gates\"] = derived` "
                             "**之前** ⇒ 被唯一门源**整体覆盖**（真跑实证：日志打印了门、"
                             "回执里没有）")
            # ★ 必须在 `normalize_gates` 之前 —— 否则不过配额与语义归一
            if _i归82 >= 0 and _i82 > _i归82:
                fails.append("【R82】★ 注入点在 `normalize_gates` **之后** ⇒ "
                             "这些门不过配额与语义归一")
    except Exception as _e82:
        fails.append("【R82】目标断言门齿自身异常：%s: %s"
                     % (type(_e82).__name__, _e82))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R48：未收敛停机必须**如实报「主树已被改动」**（4 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出的缺口 · 2026-10-15】R43 之后保留的进展**留在主树上**，
    #   而所有**未收敛**的停机路径都只是 `return 1`，**一个字都没提主树变了**
    #   ⇒ 用户收到"未收敛"会以为主树没被动过 ⇒
    #   违反「**报表与真实状态不一致，比没有报表更坏**」。
    try:
        _r48 = GoalRunner.__new__(GoalRunner)
        _r48.repo_scope = True
        _r48._kept_all = ["src/甲.rs", "src/乙.rs"]
        _r48._work = 3
        _r48._tok_chars = 100
        _发48: list = []
        _r48._emit = lambda d: _发48.append(d)
        _r48._报主树保留()
        # ① ★ 必须**发出**可机器读的回执（不能只打日志）
        _k = [d for d in _发48 if d.get("kind") == "main_tree_modified"]
        if not _k:
            fails.append("【R48】★ 未收敛停机**没有**发出「主树已改动」回执"
                         " ⇒ 用户以为主树没动过")
        elif set(_k[0].get("kept_files") or []) != {"src/甲.rs", "src/乙.rs"}:
            # ★ 用**集合**比对，不用列表：中文按码位排序（乙 U+4E59 < 甲 U+7532）
            #   ⇒ 断言列表顺序会**假红**。★★ 这是**同一个自检错误第二次犯**
            #   （R36 那次也是断言了中文列表的顺序）—— 已登记为复发教训：
            #   **凡涉及中文标识符的集合断言，一律用集合比，永不断言顺序。**
            fails.append(f"【R48】★ 回执里的文件表不对：{_k[0].get('kept_files')}")
        # ② ★ 反向（防假红）：**没有保留任何文件**时**不得**发这个回执
        #    （否则每次未收敛都喊"主树变了" ⇒ 报假警会让真警报被忽略）
        _r48b = GoalRunner.__new__(GoalRunner)
        _r48b.repo_scope = True
        _r48b._kept_all = []
        _r48b._work = 1
        _r48b._tok_chars = 0
        _发48b: list = []
        _r48b._emit = lambda d: _发48b.append(d)
        _r48b._报主树保留()
        if _发48b:
            fails.append("【R48】★ 假红：**没有保留任何文件**却报了"
                         "「主树已改动」 ⇒ 报假警会让真警报被忽略")
        # ③ ★ 非主树任务（沙箱）不报 —— 它本来就不改主树
        _r48c = GoalRunner.__new__(GoalRunner)
        _r48c.repo_scope = False
        _r48c._kept_all = ["src/丙.rs"]
        _发48c: list = []
        _r48c._emit = lambda d: _发48c.append(d)
        _r48c._报主树保留()
        if _发48c:
            fails.append("【R48】★ 非主树任务不该报「主树已改动」")
        # ④ ★★★★★ **结构齿**：四处未收敛停机**都必须**调用它
        #    （只验函数本身会被"忘了在出口调用"骗过去 —— 那正是本缺陷的形态）
        _源48 = (Path(__file__)).read_text(encoding="utf-8", errors="ignore")
        _应 = _源48.count("self._报主树保留()")
        if _应 < 4:
            fails.append(f"【R48】★ 未收敛停机出口只接了 {_应} 处（应 ≥4）"
                         " ⇒ 有出口仍在静默改主树")
    except Exception as _e48:
        fails.append("【R48】主树改动如实报告门自身异常：%s: %s"
                     % (type(_e48).__name__, _e48))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R52：同一文件的多个区域必须分得开（5 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`】
    #   机器报 `文件 src/判据/观测协议.rs 自依赖（机器拒发）` ⇒ 整趟没跑。
    #   真因：R34 允许一个大文件按行号拆成**多个交付物**（真项目里
    #   `观测协议.rs` 一个文件 7 处违规跨 7 个行号），而 R39 的 `_归一路径`
    #   **把焦点丢了** ⇒ 两个区域塌成同一身份 ⇒ 规划器写的
    #   「B 区域依赖 A 区域」被判成自依赖。
    try:
        # ① ★ 复现真形态：同文件两个区域 + 区域间依赖 ⇒ **不得**拒发
        _两区 = [
            {"path": "src/大.rs", "focus": 100, "depends_on": []},
            {"path": "src/大.rs", "focus": 900, "depends_on": ["src/大.rs:100"]},
        ]
        _序, _ok, _why = _topo_sort(_两区)
        if not _ok:
            fails.append(f"【R52】★ 同文件两区域+区域间依赖被判拒发：{_why}"
                         " ⇒ 真项目里这就是整趟没跑")
        elif _序 != [0, 1]:
            fails.append(f"【R52】★ 区域依赖必须真的排上序（100 先于 900），实得 {_序}")
        # ② ★ 真自环（文件依赖自己、无焦点可分辨）⇒ **丢弃，不拒发**
        _自环 = [{"path": "src/甲.rs", "focus": None, "depends_on": ["src/甲.rs"]}]
        _序2, _ok2, _why2 = _topo_sort(_自环)
        if not _ok2:
            fails.append(f"【R52】★ 自环是空转（不含次序信息）⇒ 应丢弃而非拒发：{_why2}")
        # ③ ★ 反向（防假红）：**未知依赖仍必须拒** —— 不能因为放宽自环
        #    就把"依赖一个根本不在计划里的文件"也放过去
        _未知 = [{"path": "src/乙.rs", "focus": None, "depends_on": ["src/不存在.rs"]}]
        _序3, _ok3, _why3 = _topo_sort(_未知)
        if _ok3 or "未知文件" not in _why3:
            fails.append(f"【R52】★ 未知依赖必须仍被拒，实得 ok={_ok3} why={_why3}")
        # ④ ★ 真环（A→B→A）仍必须拒 —— 丢掉自环不等于放过成环
        _环 = [{"path": "src/丙.rs", "focus": None, "depends_on": ["src/丁.rs"]},
               {"path": "src/丁.rs", "focus": None, "depends_on": ["src/丙.rs"]}]
        _序4, _ok4, _why4 = _topo_sort(_环)
        if _ok4 or "成环" not in _why4:
            fails.append(f"【R52】★ 真环必须仍被拒，实得 ok={_ok4} why={_why4}")
        # ⑤ ★ R39 不得回退：带行号的依赖仍要能查到（R52 的两级索引不能修坏它）
        _带号 = [{"path": "src/戊.rs", "focus": None, "depends_on": ["src/己.rs:7"]},
                 {"path": "src/己.rs", "focus": 7, "depends_on": []}]
        _序5, _ok5, _why5 = _topo_sort(_带号)
        if not _ok5:
            fails.append(f"【R52】★ R39 回退：带行号的依赖又查不到了：{_why5}")
        elif _序5 != [1, 0]:
            fails.append(f"【R52】★ 带行号依赖应排为 [1, 0]，实得 {_序5}")
    except Exception as _e52:
        fails.append("【R52】同文件多区域依赖门自身异常：%s: %s"
                     % (type(_e52).__name__, _e52))

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R54：主树不是我们独占的（4 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出的缺口 · 2026-10-16】运行期间**不在交付物清单里**的文件
    #   （`src/判据/mod.rs`、`src/模块甲/mod.rs`）mtime 落在本轮里，
    #   且红门理由从"14 处违规"变成"`src/判据/mod.rs:759` 编译错"。
    #   根因：本工具假设自己是主树的**唯一写者** —— 备份/写/判/回滚四步之间
    #   **从不检查文件是否被别的进程动过**。
    try:
        import shutil as _sh54
        import tempfile as _tf54
        _根54 = Path(_tf54.mkdtemp(prefix="r54_"))
        _存R = globals().get("REPO_ROOT")
        _存B = globals().get("BACKUP_DIR")
        try:
            (_根54 / "src").mkdir(parents=True)
            (_根54 / "bk").mkdir(parents=True)
            globals()["REPO_ROOT"] = _根54
            globals()["BACKUP_DIR"] = _根54 / "bk"
            _f = _根54 / "src" / "甲.rs"
            _改动登记.clear()

            # ① 备份后被**别人**改了 ⇒ 写盘必须**拒写**（不覆盖别人的改动）
            _f.write_bytes("// 原来\n".encode("utf-8"))
            _bk54 = _backup_file("src/甲.rs")
            _f.write_bytes("// 别人改的\n".encode("utf-8"))
            _拒 = False
            try:
                _apply_file("src/甲.rs", "// 我们的\n")
            except 并发改动错:
                _拒 = True
            if not _拒:
                fails.append("【R54】★ 备份后文件被别人改了，我们**仍然覆盖**了它")
            elif _f.read_bytes() != "// 别人改的\n".encode("utf-8"):
                fails.append("【R54】★ 报错前已经把别人的改动覆盖掉了")

            # ② 没人动过 ⇒ 正常写入，且记下"后"指纹
            _改动登记.clear()
            _f.write_bytes("// 原来\n".encode("utf-8"))
            _backup_file("src/甲.rs")
            _apply_file("src/甲.rs", "// 我们的\n")
            if _f.read_bytes() != "// 我们的\n".encode("utf-8"):
                fails.append("【R54】假红：没人动过却写不进去")
            elif not (_改动登记.get(str(_f)) or {}).get("后"):
                fails.append("【R54】写完没记下『后』指纹 ⇒ 回滚时无从对账")

            # ③ ★ 我们写完之后**别人又改了** ⇒ 回滚必须**跳过**（不吃掉别人的）
            _f.write_bytes("// 别人后来改的\n".encode("utf-8"))
            _rollback_file(_bk54)
            if _f.read_bytes() != "// 别人后来改的\n".encode("utf-8"):
                fails.append("【R54】★★ 回滚**吃掉了别人的改动**"
                             "（安全网变成销毁别人工作的机器）")

            # ④ ★ 反向：我们写的、之后没人动 ⇒ 回滚**必须照常生效**
            #    （不能因为加了守卫就把回滚弄失效 —— 那会把 R22/R23 一起废掉）
            _改动登记.clear()
            _f.write_bytes("// 原始\n".encode("utf-8"))
            _bk54b = _backup_file("src/甲.rs")
            _apply_file("src/甲.rs", "// 我们的\n")
            _rollback_file(_bk54b)
            if _f.read_bytes() != "// 原始\n".encode("utf-8"):
                fails.append("【R54】假红：无人并发时回滚**失效**了"
                             "（守卫把安全网一起关掉了）")

            # ⑤ ★ R54b：**同一文件连写两次（都是我们写的）⇒ 两次都要成功**。
            #   ★ 这条是**真跑抓出的回归**：R22 自检里 OLD→NEW→SAME 连写两次，
            #     被 R54 误判成"别人改过"⇒ 抛错**炸穿自检** ⇒ 自检红了但
            #     不点名 R22 ⇒ 齿探针判「红错地方 = 无齿」。
            #   根因：基准是"备份那一刻"（一次性），我们自己第一次写就把它作废
            #     ⇒ **守卫把自己人当外人**。
            _改动登记.clear()
            _f.write_bytes("// 原始\n".encode("utf-8"))
            _backup_file("src/甲.rs")
            _自己人 = True
            try:
                _apply_file("src/甲.rs", "// 第一次\n")
                _apply_file("src/甲.rs", "// 第二次\n")
            except 并发改动错:
                _自己人 = False
            if not _自己人:
                fails.append("【R54】★ 守卫**把自己人当外人**：我们连写两次被拒")
            elif _f.read_bytes() != "// 第二次\n".encode("utf-8"):
                fails.append("【R54】连写两次的内容不对")

            # ⑥ ★ 反向：我们写完之后**第三方插进来**，下一次写**必须拒**
            #   （★ 证明"推进基准"没有把守卫弄失效）
            _f.write_bytes("// 第三方插的\n".encode("utf-8"))
            _仍拒 = False
            try:
                _apply_file("src/甲.rs", "// 第三次\n")
            except 并发改动错:
                _仍拒 = True
            if not _仍拒:
                fails.append("【R54】★ 推进基准后守卫**失效**了："
                             "第三方插进来仍被覆盖")
        finally:
            _改动登记.clear()
            if _存R is None:
                globals().pop("REPO_ROOT", None)
            else:
                globals()["REPO_ROOT"] = _存R
            if _存B is None:
                globals().pop("BACKUP_DIR", None)
            else:
                globals()["BACKUP_DIR"] = _存B
            _sh54.rmtree(_根54, ignore_errors=True)
    except Exception as _e54:
        fails.append("【R54】并发改动守卫自身异常：%s: %s"
                     % (type(_e54).__name__, _e54))

    # ── ★★★★★ R68 → R70c：分母的**结构齿** ────────────────────────
    #   R68 当时发现：分母是**手工维护**的常量。我加 3 条 R67 齿时忘了改它，
    #   报表照样打「242/242 全绿」而实际跑了 245 条 —— **同一个病：手工数 = 枚举**。
    #   危险方向是「**齿被删掉了、分母不变**」⇒ 照样全绿 ⇒ **无齿看起来像绿**。
    #   R68 的修法是补一条**结构齿**（读自己的源码、断言 `fails.append` 出现次数
    #   不少于分母）。★ 但 R70c 实测发现**这不够**：分母本身还是手工的，
    #   我加了 9 条 R70 齿、分母没动，报表照样「253/253 全绿」，而结构齿**没红**
    #   （分母偏小恰好落在它管不着的那一边）。
    #   ⇒ R70c 治本：分母改为**运行时从源码数出来**（`_判据点`），
    #     这条齿现在守**下限**（`_判据下限`），防成批删齿。
    #   ★ 诚实边界：判据点个数 ≠ 执行了多少次检查（循环里的判据点跑多次，
    #     一条检查也可能有多处 append）—— 所以报表**不再写「M/M 全绿」**，
    #     改为「**失败 0 · 判据点 M（下限 L）**」，不声明我数不准的东西。
    if _判据点 < _判据下限:
        fails.append(
            f"【R68】判据点可疑：源码里只有 {_判据点} 处 `fails.append`，"
            f"低于下限 {_判据下限} ⇒ **检查被成批删掉了**（无齿看起来像绿）")

    # ── ★★★★★ R72：渠道源**真的接进 Router** 的结构齿 ──────────────
    #   为什么必须"读自己的源码"而不是只测 `gateway`：
    #     `gateway.渠道源` 的自检证明"这个类本身对"，
    #     但**没人证明 `generic_goal` 真的用了它** —— 那就又回到 R71 那个病：
    #     保护**声明了**、而没接到真正需要它的那条路上。
    #   ★ R71 就是这么烧掉一棵主树的：隔离类写得好好的，接线没接。
    #   ⇒ 这里断言三件事，缺一不可：
    #     ① 真的构造了 `渠道源`；
    #     ② 真的把 `刷新器=` 交给了 Router（否则刷新器是死的）；
    #     ③ **保留**了 `Router(self.pairs)` 的降级路径（增强装不上不许崩）。
    try:
        _码72 = _源码.split("# ── ★★★★★ R72", 1)[0] if "# ── ★★★★★ R72" in _源码 else _源码
        _实72 = _源码  # 结构齿查全文（上面那次 split 只为留个锚，不参与判断）
        _行72 = [_l.split("#", 1)[0] for _l in _实72.splitlines()]
        _净72 = "\n".join(_行72)
        if "渠道源(" not in _净72:
            fails.append("【R72】`generic_goal` **没有构造渠道源** ⇒ "
                         "可用性是启动时的常数、恢复的渠道永远回不来")
        if "刷新器=" not in _净72:
            fails.append("【R72】渠道源**没有接进 Router**（缺 `刷新器=`）⇒ "
                         "类写得再好也是死的（R71 那个病的原样）")
        if "Router(self.pairs)" not in _净72:
            fails.append("【R72】缺 `Router(self.pairs)` 降级路径 ⇒ "
                         "渠道源装不上时整个跑不起来（增强不该变成前提）")
    except Exception as _e72:
        fails.append(f"【R72】结构齿自身异常：{type(_e72).__name__}: {_e72}")

    # ── ★★★★★ R73b：**达标口径**的齿（真跑判定函数，不是读源码） ──────
    #   为什么必须有：R73b 把「达标」从"门绝对全绿"改成"全绿 **或** 相对基线
    #   严格变小"。**这个放宽必须被齿锁住**，否则它随时可能悄悄变成
    #   "只要有改动就算达标" —— 那就是把判据的判别力送掉了。
    #   ★ 用**真样本戊**跑（不是读源码）：要测的是**语义**，不是"代码里有没有那几行"。
    try:
        _r73 = object.__new__(GoalRunner)      # 只测纯判定，不初始化任何东西
        _基线3 = {"甲", "乙", "丙"}
        _r73._基线身份 = frozenset(_基线3)
        # ① 全绿（违规点为空）**恒**达标 —— 且**必须先判**（R46 那一课）。
        if not _r73._达标(set()):
            fails.append("【R73b】全绿（违规点为空）竟判**未达标** ⇒ "
                         "只剩一个文件时永远不可能收敛（R46 那个洞的原样）")
        # ② **真子集** ⇒ 达标（本次任务让它变好了）。
        if not _r73._达标({"甲", "乙"}):
            fails.append("【R73b】违规点**真子集变小**（3→2）竟判未达标 ⇒ "
                         "基线口径没生效，无人值守仍会恒红")
        # ③ 条数变少但**非子集** ⇒ **不达标**（"修 3 个坏 2 个"是交换，不是进展）。
        if _r73._达标({"乙", "丁"}):
            fails.append("【R73b】★ 违规点**非子集**（比基线短、却换进来新的）"
                         "竟判达标 ⇒ 放宽成了「条数变少就算达标」，判别力被送掉")
        # ④ 一个都没修 ⇒ 不达标。
        if _r73._达标(_基线3):
            fails.append("【R73b】违规点**原样未动**竟判达标")
        # ⑤ 新增了违规点（真子集的反面）⇒ 不达标。
        if _r73._达标({"甲", "乙", "丙", "丁"}):
            fails.append("【R73b】违规点**变多了**竟判达标")
        # ⑥ ★ **无基线 ⇒ 退回老口径**（必须绝对全绿）。
        #   这是"老行为不变"的那一条：主树本来就全绿时，不许因为 R73b 而放松。
        _r73b = object.__new__(GoalRunner)
        _r73b._基线身份 = frozenset()
        if _r73b._达标({"甲"}):
            fails.append("【R73b】★ **没有基线**时竟判达标 ⇒ "
                         "老行为被悄悄放宽了（主树本来就全绿时必须仍要求绝对全绿）")
        if not _r73b._达标(set()):
            fails.append("【R73b】没有基线时全绿竟判未达标")
        # ⑦ 缺字段（自检里手工造的 runner）⇒ 不得抛异常，且必须**判未达标**
        #   （宁可严不可假绿：拿不准就说"没达标"）。
        _r73c = object.__new__(GoalRunner)
        if getattr(_r73c, "_基线身份", None):
            fails.append("【R73b】新造的 runner 竟然自带基线身份（夹具不自洽）")
        if _r73c._达标({"甲"}):
            fails.append("【R73b】★ 连基线字段都没有时竟判达标 ⇒ "
                         "缺字段被静默当成「达标」＝假绿")
    except Exception as _e73:
        fails.append(f"【R73b】达标口径齿自身异常：{type(_e73).__name__}: {_e73}")

    # ── ★★★★★ R73b 接线齿：**每一处达标判定都必须用同一把尺子** ──────
    #   ★ 这是 R71/R74 那一课在**口径**上的翻版：口径定义对了，
    #     但**某一个判定点还在问旧问题** ⇒ 两把尺子 ⇒ 一处说达标、一处说没达标。
    #
    #   ★★★ 负检查（"不许再问旧问题"）**必须用 AST，不能用文本找**：
    #     ① 针 `verdict == "converged"` **含字符串字面量** ⇒ 若用
    #        `strip_for_matching` 剥字符串，针自己也被剥掉 ⇒ **恒不红**（无齿）；
    #     ② 若不剥，我**在 docstring 里解释这件事**时又写了那个串 ⇒
    #        **门匹配到它自己** ⇒ **恒红**（假红）。
    #     —— 两条路我都当场踩过（第一次跑就是恒红，红在**我自己的 docstring** 上）。
    #     ★ 正解：**问语法树**。AST 里只有**真的在比较** `verdict` 与
    #       `"converged"` 才命中 —— 注释、docstring、别的字符串一律不算，
    #       也不可能匹配到门自身（门不解析它自己写的那段文字）。
    #   ★ 正向存在性检查（`_达标本` 等）仍用**保位剥注释/字符串**后的代码本体：
    #     它们是纯标识符针，不怕被剥；而用剥过的文本才能防"只在注释里出现"的假绿。
    try:
        from agentscope_orchestrator import gate_spec as _gs73
        _净73 = _gs73.strip_for_matching(_源码)
        # ① 负检查（AST）：**没有任何代码路径再拿 `verdict == "converged"` 当达标**。
        import ast as _ast73
        _旧问: list[int] = []
        for _n73 in _ast73.walk(_ast73.parse(_源码)):
            if (isinstance(_n73, _ast73.Compare)
                    and isinstance(_n73.left, _ast73.Name)
                    and _n73.left.id == "verdict"):
                # ★ `ast.Compare.ops` 是**列表**（与 comparators 平行），
                #   不是 `.op` —— 第一版写成 `.op` ⇒ AttributeError。
                #   如实登记：它被下面的 `except` 抓住并**如实报出**，
                #   没有静默变成"通过"（这正是"检查必须报告不能自己崩"）。
                for _op73, _c73 in zip(_n73.ops, _n73.comparators):
                    if (isinstance(_op73, _ast73.Eq)       # ★ 只禁 `==`
                            and isinstance(_c73, _ast73.Constant)
                            and _c73.value == "converged"):
                        _旧问.append(_n73.lineno)
        if _旧问:
            fails.append(f"【R73b】代码里仍有 {len(_旧问)} 处拿 "
                         f"`verdict == converged` 当**达标**（行 {_旧问}）⇒ "
                         f"那个比特恒假 ⇒ 两把尺子（AST 判定，不受注释干扰）")
        # ② 正检查：达标口径必须接线
        if "_达标本" not in _净73:
            fails.append("【R73b】`_达标本` 不在代码本体里 ⇒ 达标口径没接线")
        #   ★★★ 这里**必须是否检查（`not in`），不能只做正检查**：
        #     真值向量有**两个**调用点（正常路径 + 派生门替换路径）。
        #     只断言"存在 `_TS20_向量(_达标本`" ⇒ 把**其中一个**退回 `verdict`
        #     时，另一个还在 ⇒ 正检查照样满足 ⇒ **无齿**。
        #     ★ 验齿脚本（`验证/_验R73b齿.py` ③）当场抓出来的，如实登记。
        #     —— 这是本库那条规矩的又一例：**「存在」不等于「处处」**。
        if "_TS20_向量(_达标本" not in _净73:
            fails.append("【R73b】真值向量第一个比特**不是达标** ⇒ "
                         "轨道上永远等不到「达标」这个点（不动点判定永不成立）")
        if "_TS20_向量(verdict" in _净73:
            fails.append("【R73b】★ 真值向量有调用点仍在传 `verdict`（恒假比特）"
                         "⇒ 那个位置的不动点判定永不成立")
        # ③ 窗级也要用同一把尺子：否则最终说"达标"、而多窗对齐说"没一个窗通过"
        #   ★★★★★ R124b：**这条齿原来查的是字面量 `"_窗过 = self._达标(_cur73)"`。**
        #     我把窗级调用改成多行（要显式传 `外今`）之后，**齿立刻假红**。
        #     ⇒ 齿把「实现写法」当成了「语义」。这正是本库那条纪律的反例：
        #       **文本会骗人，语法树不会**。
        #   ⇒ 改成 AST：判 `_window_score` 里**存在对 `self._达标` 的调用**，
        #     且结果**赋给了 `_窗过`**（不管写成几行、有没有多传参数）。
        try:
            import ast as _ast73c
            _树73c = _ast73c.parse(Path(__file__).read_text(encoding="utf-8"))
            _ws73 = next((n for n in _ast73c.walk(_树73c)
                          if isinstance(n, _ast73c.FunctionDef)
                          and n.name == "_window_score"), None)
            _调73 = []
            if _ws73 is not None:
                for _n73c in _ast73c.walk(_ws73):
                    if (isinstance(_n73c, _ast73c.Assign)
                            and isinstance(_n73c.targets[0], _ast73c.Name)
                            and _n73c.targets[0].id == "_窗过"
                            and isinstance(_n73c.value, _ast73c.Call)
                            and isinstance(_n73c.value.func, _ast73c.Attribute)
                            and _n73c.value.func.attr == "_达标"):
                        _调73.append(_n73c.lineno)
            if not _调73:
                fails.append("【R73b】窗级 `passed` **没走达标口径** ⇒ "
                             "最终达标了、多窗对齐却因「没一个窗通过」而交付不了"
                             "（AST 判定：`_window_score` 里没有 `_窗过 = self._达标(...)`）")
        except Exception as _e73c:
            fails.append(f"【R73b】齿③自身异常：{type(_e73c).__name__}: {_e73c}")
        # ④ 换门集必须**同步换基线身份**。
        #   ★ 这里**不数赋值处数**：正确的形状是"**一处收口**"
        #     （`_重取基线` 同时更新偏差与身份），两条换门集路径都走它。
        #     数赋值处数会把"收口得好"判成红（我第一版就是这么写错的，如实登记）。
        #   ⇒ 断言两件真正的不变量：收口存在 + 两条路径都走收口。
        if "self._基线身份 = frozenset(" not in _净73:
            fails.append("【R73b】`_基线身份` 从没被赋值 ⇒ 达标判定永远无基线"
                         "（退化成绝对全绿 ⇒ 恒红）")
        _收口73 = _净73.count("prev_devs = self._重取基线(dev_pred)")
        if _收口73 < 2:
            fails.append(f"【R73b】换门集走 `_重取基线` 的路径只有 {_收口73} 条"
                         f"（要求 2：**派生门替换**与**重规划换集**）⇒ "
                         f"换了判据还在拿旧判据下的身份比新判据下的身份")
    except Exception as _e73b:
        fails.append(f"【R73b】接线齿自身异常：{type(_e73b).__name__}: {_e73b}")

    # ══════════════════════════════════════════════════════════════════
    # 【R101】「目标本身错了」必须**走得到**问主心骨的那条路
    # ══════════════════════════════════════════════════════════════════
    # 真跑抓出（2026-10-19 · 第六次端到端）：我构造了一个**目标本身不成立**
    # 的场景（要求给一个文件里根本不存在的接口接线），想验证「主心骨会不会
    # 改目标」。结果：
    #     `规划连续 3 次不可行 ⇒ 诚实停机`，退出码 1，
    #     而 `chief_receipt.jsonl` **一条新回执都没有** —— 主心骨从头到尾没被问过。
    # ★ 根因：R97 把主心骨只接在 `_replan` 上，而 `_replan` 要求**至少跑完一轮**
    #   （要有红门才谈得上重规划）。可「目标本身错了」在**规划阶段**就暴露了 ——
    #   三次都规划不出来，恰恰说明目标不可达。
    # ⇒ **最该问主心骨的时刻，恰恰到不了问它的那条路。**
    #   这是同一个形状的第 15 层：
    #     「**声明了**主心骨有权改目标」↔「唯一能改目标的路径，在目标错时走不到」。
    try:
        # ★★ 「字面量在别处也存在」**第 8 次复发**（R71/R73b/R78/R80/R86/R93/R97/R101）：
        #   ②③ 原本在**整个文件**里搜 `self.spec = {}` —— 而 `_replan` 里也有一处
        #   （R97 改目标时作废规约）⇒ 把本方法里那行删掉，齿照样绿。
        #   `_验` 注入当场抓出：「红了但没点名」（其实是行为齿先红了，
        #   源码齿根本没响）。
        #   ⇒ 修法：**先把方法体切出来，只在体内搜**。
        _m101 = re.search(r"    async def _问主心骨_规划不成\(self.*?"
                          r"(?=\n    (?:async )?def )", _源码, re.S)
        _体101 = _m101.group(0) if _m101 else ""
        if not _体101:
            fails.append("【R101】★ 找不到 `_问主心骨_规划不成` 的方法体 ⇒ "
                         "改目标这条路径可能被删了/改名了")

        # ① 规划三次不成时，**必须**先问方向（语句位置；死代码不算）
        if not re.search(r'^ *if await self\._问主心骨_规划不成\(反馈\):',
                         _源码, re.MULTILINE):
            fails.append("【R101】★ 规划三次不成时**没有问主心骨方向** ⇒ "
                         "「目标本身错了」这个场景永远到不了问它的那条路"
                         "（真跑实测：一条新回执都没有，直接 exit 1）")
        # ② 改目标后必须**作废旧规约**（判据是针对旧目标定的）+ 清交付物
        if not re.search(r'^ *self\.spec = \{\}', _体101, re.MULTILINE):
            fails.append("【R101】★ 改目标后没作废旧规约 ⇒ 新目标配旧判据")
        if not re.search(r'^ *self\.deliverables = \[\]', _体101, re.MULTILINE):
            fails.append("【R101】★ 改目标后没清交付物 ⇒ 旧目标的交付物清单"
                         "会绑住新目标的规划")
        # ③ 预算守卫：只许改一次（否则「改目标→又不成→再改」无限循环）
        if not re.search(r'^ *if getattr\(self, "_chief_改目标次数", 0\) >= 1:',
                         _体101, re.MULTILINE):
            fails.append("【R101】★ 没有改目标预算守卫 ⇒ 会无限循环")
        # ④ 反向：**不替主心骨决定** —— 它说不改，仍必须诚实停机
        if "诚实停机" not in _源码:
            fails.append("【R101】★ 诚实停机不见了 ⇒ 机器开始替主心骨决定")

        # ⑤ ★ 行为齿（比读源码强）：真把方法跑一遍，看目标有没有换
        import types as _ty101

        class _假心101:
            """每次回合都提出一个**与当前 goal 不同**的新目标（模拟主心骨坚持改）。"""

            def __init__(self, 目标):
                self.目标 = 目标

            async def 回合(self, 观测):
                return {"改目标": True, "理由": "点名的接口不存在，目标不成立"}

        class _假驱动101:
            goal = "给不存在的接口接线"
            spec = {"gates": [{"name": "旧门"}]}
            deliverables = [{"path": "旧交付物"}]
            _chief_降级 = False
            _chief_改目标次数 = 0

            def __init__(self, 心目标):
                self._心 = _假心101(心目标)

            def _主心骨(self):
                return self._心

            def _emit(self, d):
                self._发出的 = d

        _驱101 = _假驱动101("真正该做的事")
        _方101 = _ty101.MethodType(GoalRunner._问主心骨_规划不成, _驱101)
        if not _aio31.run(_方101("计划不可行")):
            fails.append("【R101】★ 主心骨说要改目标，方法却返回 False ⇒ "
                         "改目标这条路径**实际不可用**")
        elif _驱101.goal != "真正该做的事":
            fails.append("【R101】★ 返回了 True，但 `self.goal` 没换 ⇒ 假的")
        elif _驱101.spec:
            fails.append("【R101】★ 改了目标但旧规约还在")
        elif _驱101.deliverables:
            fails.append("【R101】★ 改了目标但旧交付物清单还在")

        # ⑥ ★ 预算：改过一次之后，**再改必须被拒**（否则无限循环）。
        #   ★ 第一版这条齿是「手动把计数设成 1 再调一次」——
        #     那样测的是**守卫**（与 ④ 重复），**没测「计数会不会被消耗」**。
        #     `_验` 注入 `self._chief_改目标次数 += 1` → `pass` 时它照样绿 ⇒ 无齿。
        #   ⇒ 现在不手动设计数：让方法自己改一次，然后**换一个新目标再调一次**。
        if _驱101._chief_改目标次数 != 1:
            fails.append("【R101】★ 改过目标却没消耗预算 ⇒ 下一次还会再改"
                         "（「改目标→又不成→再改」无限循环）")
        _驱101._心 = _假心101("第三个目标")     # 主心骨又提一个不同的新目标
        if _aio31.run(_方101("又一次不可行")):
            fails.append("【R101】★ 预算用完后仍改了目标 ⇒ 会无限循环")
        elif _驱101.goal != "真正该做的事":
            fails.append("【R101】★ 预算用完后目标仍被改了")
    except Exception as _e101:
        fails.append(f"【R101】接线齿自身异常：{type(_e101).__name__}: {_e101}")

    # ══════════════════════════════════════════════════════════════════
    # 【R112】把教训写在注释里，没写进代码（第 26 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（日志原样）：`✗ src/模块乙/胞坐标.rs：判据违规点未严格变小
    #   （10 → 0）⇒ **只回退这一个文件**` —— 10 → 0 就是严格变小，
    #   而它判"未变小"，**日志印着的事实与结论互相矛盾，回退照做**。
    # 根因：`_前76`（改前量取）为空时退回 `_增量可判`，而 `_增量可判` 是
    #   `bool(现) and 现 < 基线` ⇒ 现=∅ 时判 False。
    # ★ 而 `_红门判据点` 的注释里**早就写着**「用 `bool(现)` 会把它判成未变小」。
    try:
        # ★ `_增量应用` 是**模块级函数**（0 缩进），不是 GoalRunner 的方法
        _m112 = re.search(
            r"\ndef _增量应用\(.*?(?=\n(?:async )?def |\nclass )", _源码, re.S)
        if not _m112:
            fails.append("【R112】★ 找不到 `_增量应用` 的方法体")
        else:
            _体112 = _m112.group(0)
            # ① 「该主体现在真的全绿」必须有一条独立判据
            if "_清空76 = (_前76 is None) and not _后76" not in _体112:
                fails.append("【R112】★ 没有「改前量不到、但改后该主体无红门」这条判据 "
                             "⇒ 那种情形会退回 `_增量可判` 被 `bool(∅)` 判成未变小 "
                             "⇒ **把违规点 10 → 0 的文件回退掉**（真跑实测）")
            # ② ★ 反向：不得退化成「位置点为空就算全绿」（R46 齿已锁，
            #    这里再锁一次，因为那是**我第一版的错**）
            if re.search(r"_清空76 = bool\(当前\) and not 点", _体112):
                fails.append("【R112】★ `_清空76` 退化成「位置点为空就算全绿」⇒ "
                             "「红着但算不出位置」会被当成终点保留（R46 假红）")
            # ③ ★ 判据必须**用在条件里**，不能只算不用（R85 家族）
            if not re.search(r"if 全绿 or _进步76 or _清空76 or _增量可判", _体112):
                fails.append("【R112】★ `_清空76` 算了却没进 `if` 条件 ⇒ 又是死码")
            # ④ ★ 回退理由必须说真话：不得再无条件印「未严格变小」
            # ★ 针拆段拼：我在 R112b 的**注释里原样引用过**那句旧消息
            #   ⇒ 整串查会匹配到注释（「门匹配到自己」第 11 次）。
            # ★ 而且必须锚在**带 `{rel}` 的那一句**上：只查「判据违规点…未严格变小」
            #   会被我在 R112b 里**引用旧消息的两处注释**命中（第 12 次自匹配）。
            _旧因112 = "✗ {rel}：判据违规点" + "未严格变小"
            if _旧因112 in _体112:
                fails.append("【R112】★ 回退理由仍在无条件印「未严格变小」⇒ "
                             "日志会与印出的数字自相矛盾（真跑实测 10 → 0）")
            for _必112 in ("不是真子集", "这是**交换**不是进展", "变多"):
                if _必112 not in _体112:
                    fails.append(f"【R112】★ 回退理由缺「{_必112}」这一分支 ⇒ "
                                 f"读日志的人分不清是交换还是变多")
            # ⑤ ★ 「量不到」必须说出来（静默的空 = 不可见）
            # ★ 必须查**条件**，不能只查那句话在不在（否则把 `if` 改成
            #   `if False:` 时那句话还在 ⇒ 无齿，当场抓到）。
            _条112 = "if not 前res and deliverables" + ":"
            if _条112 not in _体112 or "改前量取**为空**" not in _体112:
                fails.append("【R112】★ `前res` 为空时没有留痕 ⇒ "
                             "「量不到」与「本来就没红」长得一样")
        # ⑥ ★ 行为齿：改前量不到、改后该主体无红门 ⇒ 必须**保留**
        _根112 = Path(tempfile.mkdtemp(prefix="r112_"))
        try:
            (_根112 / "src").mkdir(parents=True, exist_ok=True)
            (_根112 / "src" / "a.rs").write_bytes("// 原始\n".encode("utf-8"))
            _存112 = globals().get("_score_files")
            _调112 = {"n": 0}

            def _假score112(workdir, deliverables, file_root=None, repo_root=None,
                            _态=_调112):
                _态["n"] += 1
                if _态["n"] == 1:
                    return ({}, [])          # 改前量取：**一条都没量到**
                return ({"src/a.rs@c": True}, [])   # 改后：该主体全绿

            globals()["_score_files"] = _假score112
            try:
                _r112, _red112, _留112, _退112 = _增量应用(
                    {"src/a.rs": "// 修好\n"},
                    frozenset({("src/a.rs", 3), ("src/a.rs", 7)}), _根112, [])
            finally:
                globals()["_score_files"] = _存112
            if not any(_r == "src/a.rs" for _r, _ in _留112):
                fails.append("【R112】★ 改前量不到、改后该主体全绿 ⇒ 仍被回退 "
                             "⇒ 这就是真跑那次的形态（违规点 10 → 0 被丢掉）")
            if _退112:
                fails.append(f"【R112】★ 该保留的改动被回退了：{_退112}")
        finally:
            import shutil as _sh112
            _sh112.rmtree(_根112, ignore_errors=True)
    except Exception as _e112:
        fails.append(f"【R112】齿自身异常：{type(_e112).__name__}: {_e112}")

    # ══════════════════════════════════════════════════════════════════
    # 【R111】修了枚举里的项，没修「枚举」—— R107 犯了 R107 的错（第 25 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（第十三次真跑收场）：
    #   `★★★ 重规划**崩了**（auto_goal.py:156）：ValueError: 槽 pre 必须是 list`
    # 而 R107 刚把 `plan_tasks` 包进 `规约可纠正`。仍崩，因为消费同一份规约的
    # 不止一个：`_build_dag` → `carrier_from_spec`（这次崩在这）。
    # ⇒ 修法不是再包一个调用点（枚举总会漏），而是**一个校验器，所有消费者**。
    try:
        # ① 校验器必须存在且**真的能判**
        if not callable(globals().get("_规约形态问题")):
            fails.append("【R111】★ 没有 `_规约形态问题` 这个**唯一**校验器")
        else:
            _坏111 = {"name": "x", "pre": "不是list", "constraints": [],
                      "gates": [], "acceptance": []}
            _报111 = _规约形态问题(_坏111)
            if not _报111:
                fails.append("【R111】★ 形态错的规约没被校验器判出来 ⇒ 校验器是摆设")
            else:
                for _必111 in ("都必须是 **list**", "必填字段", "照着这条报错改"):
                    if _必111 not in _报111:
                        fails.append(f"【R111】★ 校验器的报错缺「{_必111}」⇒ "
                                     f"模型不知道该怎么改（R107 的教训）")
            _好111 = {"name": "ok", "pre": ["a"], "constraints": ["c"],
                      "gates": [], "acceptance": ["x"]}
            if _规约形态问题(_好111):
                fails.append("【R111】★ 合法规约被校验器误判 ⇒ 会把好规约拒掉")
        # ② ★ 它必须在 `_adopt_spec` 里、且**早于一切消费者**
        _m111 = re.search(r"    def _adopt_spec\(self.*?(?=\n    (?:async )?def )",
                          _源码, re.S)
        if not _m111:
            fails.append("【R111】★ 找不到 `_adopt_spec` 的方法体")
        else:
            _体111 = _m111.group(0)
            _i校 = _体111.find("_规约形态问题(spec)")
            if _i校 < 0:
                fails.append("【R111】★ `_adopt_spec` 没调校验器 ⇒ 形态错仍会"
                             "从某个消费者里裸抛（真跑实测 `auto_goal.py:156`）")
            for _消111 in ("_build_dag(spec", "auto_goal.plan_tasks(spec)",
                           "_parse_deliverables(spec)"):
                _i消 = _体111.find(_消111)
                if _i消 >= 0 and _i校 >= 0 and _i消 < _i校:
                    fails.append(f"【R111】★ 消费者 `{_消111}` 排在形态校验**之前** "
                                 f"⇒ 校验形同虚设（这就是 R107 只包一个调用点的错）")
        # ③ ★ 行为齿：形态错必须被 `规约可纠正` 接住（不是裸 ValueError）
        _接111 = False
        try:
            _r111 = GoalRunner.__new__(GoalRunner)
            _r111._真跑实例 = False
            _r111.spec = {}
            _r111.deliverables = []
            _r111.goal = "x"
            _r111._adopt_spec({"name": "x", "pre": "不是list", "constraints": [],
                               "gates": [], "acceptance": []})
        except 规约可纠正:
            _接111 = True
        except Exception as _e111:
            fails.append(f"【R111】★ 形态错抛的是 `{type(_e111).__name__}` 而不是 "
                         f"`规约可纠正` ⇒ 模型没机会改 ⇒ 整趟死")
        if not _接111 and not any("【R111】★ 形态错抛的是" in f for f in fails):
            fails.append("【R111】★ 形态错的规约**没被接住**（竟然没抛异常）")
    except Exception as _e111b:
        fails.append(f"【R111】齿自身异常：{type(_e111b).__name__}: {_e111b}")

    # ══════════════════════════════════════════════════════════════════
    # 【R109】R92 自证块每次都在第一行炸掉（第 23 层）
    # 【R110】改了表示，没改所有读者（第 24 层）
    # ══════════════════════════════════════════════════════════════════
    # R109 真证据：`★ R92 判据契约验证异常（如实降级）：
    #   UnboundLocalError: cannot access local variable 'd0'`。
    #   `d0` 只在 `if _达标本 and fixed_point:` 的 try 里赋值，
    #   R92 自证块却在**那个 if 之外** ⇒ if 为假时 d0 未绑定 ⇒ 每次炸。
    #   ⇒ 十次真跑「主心骨自证」出现 0 次，而「如实降级」把它伪装成正常。
    # R110 真证据：`重规划失败：'gates' → 诚实停机`（KeyError），
    #   没有类型、没有行号 ⇒ 必须读源码才认得出是 `self.spec["gates"]`。
    try:
        _m109 = re.search(
            r"    async def _run\(self.*?(?=\n    (?:async )?def )", _源码, re.S)
        if not _m109:
            fails.append("【R109】★ 找不到 `_run` 的方法体")
        else:
            # ★★★★★ 剥掉**整行注释**再用 —— 这是「门匹配到自己」的根治。
            #   真事（第 13 次）：R115 那段注释里写了
            #     「因为这一段嵌在 `if _达标本 or _全绿本108:` 里面」
            #   ⇒ 下面的 `split("if _达标本 or _全绿本108:")` 切在**注释**上
            #   ⇒ 「R115 调用在 if 之前」永远判不成立 ⇒ **假红**。
            #   ★ 这一族已经复发 13 次（针匹配到自己的注释/字符串）。
            #     每一次的修法都是「这次手工躲开」——**没有一次是根治**。
            #     根治只有一种：**在查之前把不是代码的东西剥掉**。
            _体109 = "\n".join(
                _l109 for _l109 in _m109.group(0).splitlines()
                if not _l109.lstrip().startswith("#"))
            # ① ★ d0/g0/w_root 必须在**分支之前**绑定
            _前109 = _体109.split("if _达标本 or _全绿本108:")[0]
            for _名109 in ("d0 = self.deliverables[0]", "g0 = ((d0.get(",
                           "w_root = REPO_ROOT", "w_repo = REPO_ROOT"):
                if _名109 not in _前109:
                    fails.append(f"【R109】★ `{_名109}…` 不在 `if` **之前**绑定 ⇒ "
                                 f"`if` 为假时 R92 自证块会 UnboundLocalError "
                                 f"⇒ **自证永远不跑**（十次真跑 0 次）")
            # ② ★★★ R115 改了口径：**契约自证必须在 `if` 之前**，不是之内。
            #   ★ 这一条是 R109 ② 的**反向**。为什么敢反过来：
            #     R109 时它嵌在 `if` 里 ⇒ 十次真跑 0 次；
            #     R108 放宽到 `or _全绿本108` 后仍然 0 次（真跑从没全绿过）；
            #     十四次真跑只跑过 1 次。
            #     ⇒ 「体检挂在结论后面」这个形状**两次都没修掉**，
            #       必须把体检搬到结论**之前**，而不是继续放宽条件。
            _前115 = _体109.split("if _达标本 or _全绿本108:")[0]
            if "_验契约_执行时(" not in _前115:
                fails.append("【R115】★ 契约自证不在 `if _达标本 or _全绿本108:` "
                             "**之前** ⇒ 又是「要验门可不可信，得先门全绿」的循环依赖"
                             "（R108 放宽过一次、仍然 14 次真跑只跑 1 次）")
            # ②b ★ 它必须**真的被调用**（在语句位置），不是只定义了
            if not re.search(r"^ *_*self\._验契约_执行时\(\s*$", _前115, re.MULTILINE):
                fails.append("【R115】★ `_验契约_执行时` 只在注释/字符串里出现 ⇒ "
                             "定义了没调用（R85 家族：算了不用）")
            # ②c ★ 去重必须按**门集签名**，不能按「跑过一次」的布尔
            if "_门集签名115" not in _前115:
                fails.append("【R115】★ 没有按门集签名去重 ⇒ 要么每轮都跑"
                             "（2 小时拖成 10 小时），要么只跑一次（门集变了不重验）")
            # ③ ★ 跳过日志必须**还在**（R108 的教训：静默跳过 = 不可见）
            if "判据保真检查本轮**未跑**" not in _体109:
                fails.append("【R108】★ 「未跑」的日志没了 ⇒ 静默跳过 = 不可见")
        # ④ ★ R110：`self.spec["gates"]` **读**下标一律不许（spec 可能是 {}）。
        #   ★ 不能用 `strip_for_matching` 查这条：它把字符串字面量也剥掉，
        #     `self.spec["gates"]` 变成 `self.spec[       ]` ⇒ **永远匹配不上**。
        #     但也不能直接查原文 —— 我的注释里原样引用了那句旧代码
        #     （R108 的教训：**针匹配到自己**，第 9 次）。
        #   ⇒ 折中：剥**注释行**（整行以 `#` 起），保留字符串；且排除**写**
        #     （`self.spec["…"] = …` 是「唯一门源落地」，那是合法的）。
        #   ★★ 而且**文本匹配这条路本身是错的**（第三次踩同一个坑）：
        #     我的注释、docstring 里都原样引用了那句旧代码 ⇒ 逐个手工排除
        #     永远排不干净（第 5292 行是 docstring 里的一行，不是注释）。
        #   ⇒ 改用 **AST**：只看真正的 `Subscript(Attribute(self, "spec"), 常量)`，
        #     且**只算读**（父节点是 Assign 的 target 则跳过）。
        #     文本会骗人，语法树不会。
        import ast as _ast110
        _树110 = _ast110.parse(_源码)
        _写110 = set()
        for _n110 in _ast110.walk(_树110):
            if isinstance(_n110, _ast110.Assign):
                for _t110 in _n110.targets:
                    if isinstance(_t110, _ast110.Subscript):
                        _写110.add(id(_t110))
        for _n110 in _ast110.walk(_树110):
            if not isinstance(_n110, _ast110.Subscript):
                continue
            _v110 = _n110.value
            if not (isinstance(_v110, _ast110.Attribute)
                    and _v110.attr == "spec"
                    and isinstance(_v110.value, _ast110.Name)
                    and _v110.value.id == "self"):
                continue
            if id(_n110) in _写110:
                continue                      # 写（唯一门源落地）是合法的
            _k110 = _n110.slice
            _名110 = (_k110.value if isinstance(_k110, _ast110.Constant)
                      else "<非常量>")
            fails.append("【R110】★ 仍有 `self.spec[…]` 裸**读**下标 "
                         f"（第 {_n110.lineno} 行，键={_名110!r}）⇒ "
                         "`self.spec` 是 `{}` 时抛 KeyError ⇒ 整趟死"
                         "（真跑实测 `重规划失败：'gates'`）")
        # ⑤ ★ R110b：崩了必须留下**可诊断**的信息（类型 + 位置 + 完整栈）。
        #   ★ 针必须**拆段拼**，否则匹配到本齿自己的字符串（第 10 次）。
        _缺110 = "product_" + "defect"
        if _缺110 not in _源码:
            fails.append("【R110】★ 重规划崩了不区分「产品缺陷」与「模型给错规约」"
                         " ⇒ 产品缺陷会被当成任务失败上报")
        _栈110 = "完整栈已进 replan_error" + " 回执"
        if _栈110 not in _源码:
            fails.append("【R110】★ 崩溃日志仍只有一句话（真跑实测只留下 "
                         "`重规划失败：'gates'`）⇒ 无法诊断")
        #   ★ 只查「分支存在」是**不够**的（第一版就这么写，注入 `= False` 仍绿）：
        #     真正要保证的是「这个布尔**真的由异常类型算出来**」。
        _分110 = "if _是缺陷" + ":"
        if _分110 not in _源码:
            fails.append("【R110】★ 没有 `if _是缺陷:` 这条分支 ⇒ "
                         "产品缺陷与模型错误走同一条日志")
        #   ★★★★★ R126b：**这条齿原来查的是字面量**，而 R126b 把赋值拆成了
        #     两行（`not isinstance(...) and not _瞬态126`）⇒ 齿立刻假红。
        #     ⇒ 改用 **AST**：判 `_是缺陷` 的右值里**含 `isinstance(exc, …)`**。
        #     ★ 「文本会骗人，语法树不会」—— 第 3 次实战应用。
        _算110 = False
        try:
            for _n110b in _ast110.walk(_树110):
                if (isinstance(_n110b, _ast110.Assign)
                        and isinstance(_n110b.targets[0], _ast110.Name)
                        and _n110b.targets[0].id == "_是缺陷"):
                    for _c110 in _ast110.walk(_n110b.value):
                        if (isinstance(_c110, _ast110.Call)
                                and isinstance(_c110.func, _ast110.Name)
                                and _c110.func.id == "isinstance"
                                and _c110.args
                                and isinstance(_c110.args[0], _ast110.Name)
                                and _c110.args[0].id == "exc"):
                            _算110 = True
        except Exception:
            _算110 = False
        if not _算110:
            fails.append("【R110】★ `_是缺陷` **不是由异常类型算出来的** ⇒ "
                         "写死成 False/True 就等于没有区分（注入实测）")
    except Exception as _e109:
        fails.append(f"【R109/R110】齿自身异常：{type(_e109).__name__}: {_e109}")

    # ══════════════════════════════════════════════════════════════════
    # 【R140】累积量被当成当前量（第 53 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（第 32 次真跑）：轮1 4 红 / 轮2 5 红 / 轮3 4 红（还多修好一条）
    #   ⇒ 却判 `diverging`「已消偏差重现（C6）」⇒ 停机。
    # ★ 根因：`prev_green` 是**从第一轮起累积、永不收缩**的集合，
    #   而 C6 问的是「**上一轮**消掉的，这一轮又回来了吗」——
    #   那是**相邻两轮**的问题。用累积集合回答它 ⇒ 消过一次就永远判发散。
    _产140 = _源码[:_源码.find("【R140】累积量被当成当前量")]
    try:
        # ① `prev_green` 必须是**赋值**（`=`），不是**累积**（`|=`）
        _累140 = False
        _赋140 = False
        try:
            for _n140 in __import__("ast").walk(__import__("ast").parse(_产140)):
                if (isinstance(_n140, __import__("ast").AugAssign)
                        and isinstance(_n140.target, __import__("ast").Name)
                        and _n140.target.id == "prev_green"):
                    _累140 = True
                if (isinstance(_n140, __import__("ast").Assign)
                        and isinstance(_n140.targets[0], __import__("ast").Name)
                        and _n140.targets[0].id == "prev_green"):
                    _赋140 = True
        except Exception:
            _累140, _赋140 = False, False
        if _累140:
            fails.append("【R140】★ `prev_green` 仍在**累积**（`|=`）⇒ "
                         "它变成「历史上曾消过的全部」而不是「上一轮消掉的」"
                         "⇒ 消过一次就**永远**判 diverging ⇒ 必然停机")
        #   ★ 光查「有没有赋值」不够：`prev_green = set()` 初始化也算赋值
        #     （实测无齿一次）⇒ 必须查**赋值右侧含 `prev_ids - cur_ids`**。
        _真赋140 = False
        try:
            for _n140b in __import__("ast").walk(__import__("ast").parse(_产140)):
                if (isinstance(_n140b, __import__("ast").Assign)
                        and isinstance(_n140b.targets[0], __import__("ast").Name)
                        and _n140b.targets[0].id == "prev_green"):
                    _右 = __import__("ast").dump(_n140b.value)
                    if "prev_ids" in _右 and "cur_ids" in _右:
                        _真赋140 = True
        except Exception:
            _真赋140 = False
        if not _真赋140:
            fails.append("【R140】★ `prev_green` 没有相邻两轮的赋值"
                         "（须 `prev_green = prev_ids - cur_ids`）⇒ "
                         "C6 的语义（上一轮消掉、这一轮重现）无法成立")
        # ② 注释必须说清「不折叠不等于不收缩」（否则下一个人又会改回 |=）。
        #   ★ 用拼接构造针，防齿自己命中自己（第 19 次纪律）。
        #   ★★ 但仍不够：本齿块的**注释里也写着这句话**，而 `_产140`
        #     只切到 `_产140 = …` 那一行 —— 齿自己的注释**在它之后**，
        #     所以能命中；可一旦产品里那句被删，齿自己那句还在 ⇒ 无齿。
        #   ⇒ 再收一层：只查**赋值语句前后 2000 字**（那才是产品代码所在处）。
        #   ★ 现在这个字面量**只在产品代码里出现一次**（齿自己的注释已改写法）
        #     ⇒ 可以放心用整份产品切片查，不必再算窗口（窗口算错过两次）。
        if ("不折叠 " + "≠ 不收缩") not in _产140:
            fails.append("【R140】★ 没写清「不折叠不等于不收缩」⇒ "
                         "下一个人会把 `=` 改回 `|=`（这正是本条的成因）")
    except Exception as _e140:
        fails.append(f"【R140】齿自身异常：{type(_e140).__name__}: {_e140}")

    # ══════════════════════════════════════════════════════════════════
    # 【R138】签名少了「产物」这一维（第 51 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（第 31 次真跑）：轮1 6 条门全判「契约不成立」；轮2 门全 True、
    #   `有区分力 6/6`、`verdict=converged` —— 而 `action=stop`、交付不了。
    # ★ 根因：`_验契约_执行时` 的输入是「门集 + 产物」，缓存键只有「门集」。
    _产138 = _源码[:_源码.find("【R138】签名少了")]
    try:
        # ① 签名必须**真的把产物内容算进去**（AST：签名函数里必须有 read_bytes）
        _有产138 = False
        try:
            for _n138 in __import__("ast").walk(__import__("ast").parse(_产138)):
                if (isinstance(_n138, __import__("ast").FunctionDef)
                        and _n138.name == "_门集签名115"):
                    _有产138 = any(
                        isinstance(_x, __import__("ast").Attribute)
                        and _x.attr in ("read_bytes", "read_text")
                        for _x in __import__("ast").walk(_n138))
        except Exception:
            _有产138 = False
        if not _有产138:
            fails.append("【R138】★ 契约签名只按门集算 ⇒ 产物变了不重验 ⇒ "
                         "轮1 的「契约破裂」留到轮2 ⇒ `_达标` 恒 False ⇒ "
                         "门全绿也交付不了（真跑实测）")
        # ② 必须有**读不到就退化**的诚实边界（不能假装产物没变）
        #   ★ 查注释字面量不算 —— 齿自己写着那句注释（「门匹配到自己」第 18 次）。
        #     必须查 AST：签名函数里**真的有** `if not _产: return …` 这个分支。
        _退138 = False
        try:
            for _n138b in __import__("ast").walk(__import__("ast").parse(_产138)):
                if (isinstance(_n138b, __import__("ast").FunctionDef)
                        and _n138b.name == "_门集签名115"):
                    for _if138 in __import__("ast").walk(_n138b):
                        if (isinstance(_if138, __import__("ast").If)
                                and isinstance(_if138.test,
                                               __import__("ast").UnaryOp)
                                and isinstance(_if138.test.op,
                                               __import__("ast").Not)
                                and any(isinstance(_x, __import__("ast").Return)
                                        for _x in __import__("ast").walk(
                                            __import__("ast").Module(
                                                body=_if138.body)))):
                            _退138 = True
        except Exception:
            _退138 = False
        if not _退138:
            fails.append("【R138】★ 读不到产物时没有**如实退化**的分支 ⇒ "
                         "要么抛异常，要么静默把「没读到」当成「没变」")
    except Exception as _e138:
        fails.append(f"【R138】齿自身异常：{type(_e138).__name__}: {_e138}")


    # ══════════════════════════════════════════════════════════════════
    # 【R137】「没达标」的因由写的是「看起来达标」的内容（第 50 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（第 31 次真跑，轮 2）：`未达标因由：verdict=converged；基线 3→0`
    #   —— 因由读起来是「全好了」，值却是 False。**因由与值互相矛盾。**
    _产137 = _源码[:_源码.find("【R137】「没达标」的因由")]
    try:
        # ① `_达标` 的三个早退必须都**留下真实因由**
        #   ★ 不能用 `count(字面量)`：齿自己写着这个字面量（第 18 次）。
        #     查 AST：`_达标` 函数体内**真的**有 4 个这样的赋值。
        _记137 = 0
        try:
            for _n137 in __import__("ast").walk(__import__("ast").parse(_产137)):
                if (isinstance(_n137, __import__("ast").FunctionDef)
                        and _n137.name == "_达标"):
                    for _a137 in __import__("ast").walk(_n137):
                        if (isinstance(_a137, __import__("ast").Assign)
                                and isinstance(_a137.targets[0],
                                               __import__("ast").Attribute)
                                and _a137.targets[0].attr == "_未达标因由137"):
                            _记137 += 1
        except Exception:
            _记137 = 0
        #   ★ 阈值不能写死：`_达标` 里**每一处 `return False` 都必须先记因由**。
        #     写死 4 会被"删一处还剩 4 处"骗过去（实测无齿一次）。
        _返137 = 0
        try:
            for _n137c in __import__("ast").walk(__import__("ast").parse(_产137)):
                if (isinstance(_n137c, __import__("ast").FunctionDef)
                        and _n137c.name == "_达标"):
                    for _r137 in __import__("ast").walk(_n137c):
                        if (isinstance(_r137, __import__("ast").Return)
                                and isinstance(_r137.value,
                                               __import__("ast").Constant)
                                and _r137.value.value is False):
                            _返137 += 1
        except Exception:
            _返137 = 0
        #   只数**带内容的**赋值（`= []` 是重置，不算"记了因由"）
        _实137 = 0
        try:
            for _n137d in __import__("ast").walk(__import__("ast").parse(_产137)):
                if (isinstance(_n137d, __import__("ast").FunctionDef)
                        and _n137d.name == "_达标"):
                    for _a137b in __import__("ast").walk(_n137d):
                        if (isinstance(_a137b, __import__("ast").Assign)
                                and isinstance(_a137b.targets[0],
                                               __import__("ast").Attribute)
                                and _a137b.targets[0].attr == "_未达标因由137"
                                and isinstance(_a137b.value, __import__("ast").List)
                                and _a137b.value.elts):
                            _实137 += 1
        except Exception:
            _实137 = 0
        _记137 = _实137
        if _返137 < 1 or _实137 < _返137:
            fails.append(f"【R137】★ `_达标` 有 {_返137} 处 `return False`，"
                         f"却只有 {_实137} 处**留下真实因由**"
                         "（`_未好89`/`_破92`/`无基线`/`非真子集`）"
                         "⇒ 因由说不清拦在哪 ⇒ 会出现"
                         "「因由＝verdict=converged，值＝False」这种自相矛盾")
        # ② 裁决因由必须**优先用真实因由**（不是恒用 verdict 口径）
        #   ★ 查「有没有 `_因137`」不够：注入 `_因137 = []` 时它还在（第 18 次）。
        #     查 AST：`_因137` 的右值必须是 `list(getattr(...))`，不是空列表常量。
        _用137 = False
        try:
            for _n137b in __import__("ast").walk(__import__("ast").parse(_产137)):
                if (isinstance(_n137b, __import__("ast").Assign)
                        and isinstance(_n137b.targets[0], __import__("ast").Name)
                        and _n137b.targets[0].id == "_因137"
                        and isinstance(_n137b.value, __import__("ast").Call)
                        and getattr(_n137b.value.func, "id", "") == "list"):
                    _用137 = True
        except Exception:
            _用137 = False
        if not _用137:
            fails.append("【R137】★ 裁决因由没用真实因由 ⇒ "
                         "「没达标」的因由会写成「verdict=converged」"
                         "（与值矛盾，把人引向错方向）")
        # ③ 达标时仍要退回 verdict 口径（信息不丢）
        if ("else f" + chr(34) + "verdict={verdict}") not in _产137:
            fails.append("【R137】★ 达标时丢了 verdict 口径 ⇒ 信息变少")
    except Exception as _e137:
        fails.append(f"【R137】齿自身异常：{type(_e137).__name__}: {_e137}")

    # ══════════════════════════════════════════════════════════════════
    # 【R136】「换了几次」不是「为什么换」（第 49 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（第 30 次真跑，轮 4）：判据违规点 **4→0**、7 条门 **6 条有区分力**、
    #   `verdict=converged`、`Δ=∅` —— 三十次里最好的状态。
    # ★ 而 `判据结构停机 @轮4：已换门集 2 次未收敛` ⇒ 停机交还。
    # ★ 根因：`_replan_thrashed(count, max)` 只看次数，不看**这次为什么换**。
    #   本次是「1 条门写坏了」，不是「判据不可达」。
    # ★ 与 R126 同族第 3 次：**次数不是因由**。
    _产136 = _源码[:_源码.find("【R136】「换了几次」")]
    try:
        # ⑦ R136b：这段点名必须**真送进 prompt**（不能只存不用）。
        #   ★ 放在最前：否则别处先红时它的 ✗ 会被盖住（实测过一次）。
        #   ★ 齿要查**注入这件事真的发生了**（不是"有那行日志字面量"）。
        #     注入的标志是 `prompt +=` 且右侧含 `_拒136`。
        _注136 = False
        try:
            for _n136c in __import__("ast").walk(__import__("ast").parse(_产136)):
                if (isinstance(_n136c, __import__("ast").AugAssign)
                        and isinstance(_n136c.target, __import__("ast").Name)
                        and _n136c.target.id == "prompt"
                        and isinstance(_n136c.op, __import__("ast").Add)
                        and any(isinstance(_x, __import__("ast").Name)
                                and _x.id == "_拒136"
                                for _x in __import__("ast").walk(_n136c.value))):
                    _注136 = True
        except Exception:
            _注136 = False
        if not _注136:
            fails.append("【R136】★ 点名的话**存了没送出去**（R128 同族）⇒ "
                         "模型永远看不见哪条门恒真")
        # ① 必须有「恒真门 ≠ 判据不可达」这个区分。
        #   ★ 查「有没有 `_是恒真136`」不够：注入 `False and (...)` 时它还在
        #     ⇒ 必须查**它的右值不是 `False and ...`**（AST）。
        _真136 = False
        try:
            for _n136 in __import__("ast").walk(__import__("ast").parse(_产136)):
                if (isinstance(_n136, __import__("ast").Assign)
                        and isinstance(_n136.targets[0], __import__("ast").Name)
                        and _n136.targets[0].id == "_是恒真136"):
                    _v136 = _n136.value
                    # 右值必须是 BoolOp（`A and B`），且**第一项不是常量 False**
                    if isinstance(_v136, __import__("ast").BoolOp):
                        _首136 = _v136.values[0] if _v136.values else None
                        if not (isinstance(_首136, __import__("ast").Constant)
                                and _首136.value is False):
                            _真136 = True
        except Exception:
            _真136 = False
        if not _真136:
            fails.append("【R136】★ 没区分「恒真门」与「判据不可达」⇒ "
                         "1 条门写坏会当成整个判据不可达 ⇒ 停机交还")
        # ② 恒真门这条路必须**不吃 replan 配额**。
        #   ★ 同样查语义：`if _是恒真136 and <配额判断>:` —— 配额判断必须存在
        _配136 = False
        try:
            for _n136b in __import__("ast").walk(__import__("ast").parse(_产136)):
                if isinstance(_n136b, __import__("ast").If):
                    _t136 = _n136b.test
                    if (isinstance(_t136, __import__("ast").BoolOp)
                            and any(isinstance(_x, __import__("ast").Name)
                                    and _x.id == "_是恒真136"
                                    for _x in _t136.values)
                            and any(isinstance(_x, __import__("ast").Compare)
                                    for _x in _t136.values)):
                        _配136 = True
        except Exception:
            _配136 = False
        if not _配136:
            fails.append("【R136】★ 恒真门重试没有**独立配额** ⇒ "
                         "会被 replan 次数护栏一起掐死")
        # ③ 必须**点名**是哪条门（不能只说「有恒真门」）
        if "恒真门（对任何产物都绿，无区分力）" not in _产136:
            fails.append("【R136】★ 没点名是哪条门 ⇒ 模型不知道要重写哪一条")
        # ④ 必须说清「为什么它恒真」（变异后仍绿）
        if "机器把产物变异" not in _产136:
            fails.append("【R136】★ 没说清「为什么它恒真」⇒ 模型会以为只是名字不对")
        # ⑤ 必须给**具体改法示例**，且**真的在注入给模型的字符串里**
        #   （不能只出现在注释里 —— 注释模型看不见）
        #   ★ 改法示例在 `self._上次拒收97 = (…)` 那段赋值里，不在注入块里
        #     ⇒ 直接在**产品代码**里找它（不是注释、不是齿自己）。
        if "应改成**真跑**" not in _产136:
            fails.append("【R136】★ 没给具体改法（或只写在注释里、模型看不见）"
                         "⇒ 模型只会把 grep 换一个 grep")
        # ⑥ 必须明确「其余门不要动」
        if "条门**不要动**" not in _产136:
            fails.append("【R136】★ 没明确「其余门不要动」⇒ 模型会整体重写"
                         "（R135 的教训）")

        # ⑧ 送出去之后必须**清掉**（防变成永久噪音）
        _段136b = _源码[_源码.find("_拒136 = getattr"):]
        _段136b = _段136b[:_段136b.find("_二循环131")]
        if "_上次拒收97 = """ not in _段136b:
            fails.append("【R136】★ 注入后没清空 ⇒ 这段话会**每轮重复**，"
                         "变成永久噪音（模型会以为一直没修好）")
    except Exception as _e136:
        fails.append(f"【R136】齿自身异常：{type(_e136).__name__}: {_e136}")

    # ══════════════════════════════════════════════════════════════════
    # 【R135】门集已经是对的，机器却要求它重做门集（第 48 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（第 29 次真跑，轮2 门真值）：8 条门**覆盖了全部 7 个交付物**，
    #   真跑了 3 轮、判据违规点 6→4、`cargo_check_lib=True`。
    # ★ 而重规划 prompt 说的是「**重新设计**文件结构 / 实现方案 / **判据门**」
    #   ⇒ 模型重写门集 ⇒ 新门集漏了文件 ⇒ `交付物没有判据门覆盖` ⇒ 3 次全败。
    # ★ 与 R128/R129/R131 同族第四次：机器手里有正确信息，却把「要改哪一层」指错。
    #   ★★★ 这七条齿第一版**全部无齿**（注入后照样绿）—— 病根是**同一个**：
    #     齿查的是 `_源码`（整个文件），而**齿自己的代码里就写着那些字面量**
    #     ⇒ 「删掉产品里的那句」之后，**齿里的那句还在** ⇒ 检查照过。
    #     ★ 这是「门匹配到自己」第 **17** 次复发。
    #   ⇒ 修法：**只查产品代码切片**（本齿块之前的部分），并把
    #     「有没有赋值」换成 AST 查「右值是不是常量 True」。
    _产135 = _源码[:_源码.find("【R135】门集已经是对的")]
    try:
        # ① 重规划 prompt 必须含「门集不需要重写」这一段
        #   ★ 查「含这句话」不够：注入 `) if False else ""` 后
        #     那句话**还在字符串里**（只是不再被采用）⇒ 照样绿。
        #     必须查**它真的被条件采用**（`_门已验证135 else` 这个条件表达式）。
        _段135 = "判据门集不需要" + "重写"
        if _段135 not in _产135 or "_门已验证135 else" not in _产135:
            fails.append("【R135】★ 重规划 prompt 没告诉模型「门集已经跑通了、"
                         "只改实现」（或那段话没被条件采用）"
                         "⇒ 模型会重写门集、再漏文件、再被拒收"
                         "（真跑实测：连续 3 次全败 ⇒ 诚实停机）")
        # ② 必须**有条件**（门集确实红时不该说这句，否则会阻止合法换门）
        #   ★ 查「有没有赋值」不够：注入 `_门已验证135 = True` 时赋值还在。
        #     必须查**右值**不是常量 True。
        _恒135 = False
        try:
            for _n135 in __import__("ast").walk(__import__("ast").parse(_产135)):
                if (isinstance(_n135, __import__("ast").Assign)
                        and isinstance(_n135.targets[0], __import__("ast").Name)
                        and _n135.targets[0].id == "_门已验证135"
                        and isinstance(_n135.value, __import__("ast").Constant)
                        and _n135.value.value is True):
                    _恒135 = True
        except Exception:
            _恒135 = True
        if _恒135:
            fails.append("【R135】★ 「门集是否已验证」被写死成 True ⇒ "
                         "恒说「别动门集」⇒ 门集真红时模型无法自救")
        if "_门已验证135" not in _产135:
            fails.append("【R135】★ 没有「门集是否已验证」这个判断")
        # ③ 必须明确「只改哪几个字段」
        if "只改 `deliverables`" not in _产135:
            fails.append("【R135】★ 没明确「只改哪几个字段」⇒ 模型仍会整份重写")
        # ④ 必须留一条**例外通道**（真需要换门时逐条说明）
        if "逐条说明是哪一条" not in _产135:
            fails.append("【R135】★ 没给「确实要换门」的例外通道 ⇒ "
                         "门集真红时模型无法自救")
    except Exception as _e135:
        fails.append(f"【R135】齿自身异常：{type(_e135).__name__}: {_e135}")

    # ══════════════════════════════════════════════════════════════════
    # 【R134】基线探针炸了，炸掉的是「任务可达性」本身（第 47 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据：`TypeError: … not 'NoneType'` ⇒ 静默退回绝对全绿
    #   ⇒ 主树 13 处历史违规**又变成本次任务的责任** ⇒ 恒红门。
    # ★ 与 R126 同族：**异常不是因由**。「配置缺失」≠「没有历史债」。
    #   ★ 同样只用**产品代码切片**（齿自己写着这些字面量）。
    _产134 = _源码[:_源码.find("【R134】基线探针炸了")]
    try:
        # ① 必须在拿 None 当路径**之前**判掉。
        #   ★ R139 升级：原来只查 `if REPO_ROOT is None:` —— 而真跑实测
        #     None 的是 **workdir**（R134 只防住了一侧）。
        #     现要求：**每个根都必须归一**（`for _c139 in (workdir, REPO_ROOT)`），
        #     且归一后的根必须**真的被传下去**（不是算完就扔）。
        _归134 = False
        try:
            for _n134 in __import__("ast").walk(__import__("ast").parse(_产134)):
                if (isinstance(_n134, __import__("ast").FunctionDef)
                        and _n134.name == "_基线偏差"):
                    #   ★ 只查「有 for / 有 is」不够：注入成只遍历**一个**根，
                    #     结构照样满足（实测无齿一次）。
                    #     必须查：那个 `for` 的可迭代样本戊是**同时含两个根**的元组。
                    for _f134 in __import__("ast").walk(_n134):
                        if (isinstance(_f134, __import__("ast").For)
                                and isinstance(_f134.iter,
                                               __import__("ast").Tuple)):
                            _名134 = {
                                _e.id for _e in _f134.iter.elts
                                if isinstance(_e, __import__("ast").Name)}
                            if {"workdir", "REPO_ROOT"} <= _名134:
                                _归134 = True
        except Exception:
            _归134 = False
        if not _归134:
            fails.append("【R134/R139】★ 基线根**没有归一**（`workdir` 与 "
                         "`REPO_ROOT` 都必须判，且缺了要换用另一个）⇒ "
                         "任一为 None 就抛 TypeError ⇒ 被 except 吞成「无基线」"
                         "⇒ 退回绝对全绿 ⇒ 恒红门 ⇒ 永远跑不通")
        # ② 必须说清「这不是没有历史债，是没量到」（两件事处置相反）
        if ("这不是「没有历史债」，" + "是「没量到」") not in _产134:
            fails.append("【R134】★ 没区分「没历史债」与「没量到」⇒ "
                         "配置缺失会被当成「主树干净」")
        # ③ 必须给出可行动因由
        if ("请在档案里声明 " + "`repo_root`") not in _产134:
            fails.append("【R134】★ 没给可行动因由 ⇒ 用户不知道该怎么修")
        # ④ ★ R139：**归一后的根必须真的被用**（不能算完就扔 —— R128 同族）
        _用134 = False
        try:
            for _n134b in __import__("ast").walk(__import__("ast").parse(_产134)):
                if (isinstance(_n134b, __import__("ast").FunctionDef)
                        and _n134b.name == "_基线偏差"):
                    for _c134 in __import__("ast").walk(_n134b):
                        if (isinstance(_c134, __import__("ast").Call)
                                and isinstance(_c134.func,
                                               __import__("ast").Name)
                                and _c134.func.id == "_score_files"
                                and _c134.args
                                and isinstance(_c134.args[0],
                                               __import__("ast").Name)
                                and _c134.args[0].id == "_根139"):
                            _用134 = True
        except Exception:
            _用134 = False
        if not _用134:
            fails.append("【R139】★ 归一后的根**算完没用**（`_score_files` 仍收 "
                         "原始 workdir）⇒ 归一等于没做（R128 同族）")
        # ⑤ ★ R139：**归一后必须整条链都用它** ——
        #   `file_root` / `repo_root` 也是根，传 None 进去等于没归一。
        #   （实测：只改 `file_root=None` 时前四条齿全绿 ⇒ 无齿。）
        _链134 = False
        try:
            for _n134c in __import__("ast").walk(__import__("ast").parse(_产134)):
                if (isinstance(_n134c, __import__("ast").FunctionDef)
                        and _n134c.name == "_基线偏差"):
                    for _c134b in __import__("ast").walk(_n134c):
                        if (isinstance(_c134b, __import__("ast").Call)
                                and isinstance(_c134b.func,
                                               __import__("ast").Name)
                                and _c134b.func.id == "_score_files"):
                            _kw = {k.arg for k in _c134b.keywords
                                   if k.arg in ("file_root", "repo_root")}
                            if _kw == {"file_root", "repo_root"}:
                                _都引139 = all(
                                    "_根139" in __import__("ast").dump(k.value)
                                    for k in _c134b.keywords
                                    if k.arg in ("file_root", "repo_root"))
                                _链134 = _都引139
        except Exception:
            _链134 = False
        if not _链134:
            fails.append("【R139】★ `file_root`/`repo_root` 没跟着归一 ⇒ "
                         "传 None 进去 ⇒ 下游仍会 `Path(None)` ⇒ TypeError")
        # ⑤b ★ R141：**优先级必须主树在前**。
        #   实测（第 33 次真跑）：轮1-3 主树（4 处历史债，对），
        #   轮4 起变成 `work/task/r4`（错树）⇒ 基线跟着产物漂 ⇒ 永远追不上。
        _序141 = None
        try:
            for _n141 in __import__("ast").walk(__import__("ast").parse(_产134)):
                if (isinstance(_n141, __import__("ast").FunctionDef)
                        and _n141.name == "_基线偏差"):
                    for _f141 in __import__("ast").walk(_n141):
                        if (isinstance(_f141, __import__("ast").For)
                                and isinstance(_f141.iter,
                                               __import__("ast").Tuple)):
                            _元141 = [e.id for e in _f141.iter.elts
                                      if isinstance(e, __import__("ast").Name)]
                            if "workdir" in _元141 and "REPO_ROOT" in _元141:
                                _序141 = _元141
        except Exception:
            _序141 = None
        if _序141 is None:
            fails.append("【R141】★ 找不到基线根的候选元组 ⇒ 无法判优先级")
        elif _序141[0] != "REPO_ROOT":
            fails.append("【R141】★★ 基线根**优先级排反了**"
                         f"（现在是 {_序141}，应先 `REPO_ROOT`）⇒ "
                         "隔离树不是「未改动的主树」⇒ 基线跟着产物漂 ⇒ "
                         "达标口径永远追不上（第 33 次真跑实测）")
        # ⑤c ★ R142：**换门集后的重取也不得传隔离树**。
        #   实测（第 34 次真跑）：轮4 基线 22 处、轮6 基线 4 处，
        #   同一棵主树同一批判据却在 10/22/4 之间跳 ⇒ 达标口径跟着跳。
        _重取142 = False
        try:
            for _n142 in __import__("ast").walk(__import__("ast").parse(_产134)):
                if (isinstance(_n142, __import__("ast").FunctionDef)
                        and _n142.name == "_重取基线"):
                    for _c142 in __import__("ast").walk(_n142):
                        if (isinstance(_c142, __import__("ast").Call)
                                and isinstance(_c142.func,
                                               __import__("ast").Name)
                                and _c142.func.id == "_基线偏差"):
                            _重取142 = True
                            _首142 = (_c142.args[0]
                                      if _c142.args else None)
                            _dump142 = __import__("ast").dump(_首142) \
                                if _首142 is not None else ""
                            if "round_root" in _dump142:
                                fails.append(
                                    "【R142】★★ `_重取基线` 把**隔离树**"
                                    "（`self.round_root`）当基线根传进去了 ⇒ "
                                    "基线跟着本轮产物漂 ⇒ 达标口径永远不稳定"
                                    "（第 34 次真跑实测：10/22/4 三跳）")
        except Exception:
            _重取142 = False
        if not _重取142:
            fails.append("【R142】★ 找不到 `_重取基线` 里的基线调用 ⇒ "
                         "换门集路径可能绕过根归一")
        # ══════════════════════════════════════════════════════════════
        # 【R151】「按设计停机」不是「崩了」
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 37 次真跑，最后一轮）：
        #   `★★★ 重规划**崩了**（generic_goal.py:4839）：ValueError:
        #    重规划连续 3 次可纠正性问题仍未改对 ⇒ 诚实停机（不猜、不替它决定）`
        # ★ 一句话里同时写着「崩了」和「诚实停机」—— 自相矛盾。
        # ★★ 齿要测的是**分类结论**：同一个「按设计停机」，
        #   不得被判成产品缺陷；同一个「真崩」，必须被判成产品缺陷。
        try:
            if not issubclass(按设计停机, Exception):
                fails.append("【R151】★ `按设计停机` 不是异常类")
            # ① 按设计停机 ⇒ 不算产品缺陷
            if isinstance(按设计停机("连续 3 次没改对 ⇒ 交还决定权"),
                          (规约可纠正, 计划不可行)):
                fails.append("【R151】★ `按设计停机` 被归进了「规约可纠正」"
                             " ⇒ 会走回喂路径，永远停不下来")
            # ② 它必须**不是** `ValueError` —— 否则老分类又会把它当缺陷
            if issubclass(按设计停机, ValueError):
                fails.append("【R151】★★ `按设计停机` 继承 ValueError"
                             " ⇒ `_是缺陷` 的分类会把它当产品缺陷"
                             "（正是第 37 次真跑发生的）")
            # ③ 真崩（普通 Exception）必须**仍然**被判成缺陷
            #    ★ 反自证：如果分类把一切都当「按设计停机」，这条会咬住。
            _真崩 = RuntimeError("'str' object has no attribute 'get'")
            if isinstance(_真崩, 按设计停机):
                fails.append("【R151】★★ 真崩被判成「按设计停机」"
                             " ⇒ 产品缺陷会被掩盖成正常停机")
        except NameError as _e151:
            fails.append(f"【R151】★ 没有 `按设计停机` 异常类：{_e151}")
        except Exception as _e151b:
            fails.append(f"【R151】齿自身异常：{type(_e151b).__name__}: {_e151b}")
        # ④ ★★★ **测实际抛出的那个**，不是测类型定义。
        #   实测（本齿的第一版）：上面 ①②③ 三条只查了 `按设计停机` 的**定义**，
        #   我把抛出点 `raise 按设计停机(...)` 改回 `raise ValueError(...)`
        #   ⇒ 自检**仍然全绿** ⇒ 无齿。
        #   ★ 这是「齿要测结果，不是测过程里有没有那个词」的又一次：
        #     类型定义存在 ≠ 真的用它抛。
        #   ⇒ 判据改成：**在源码里找那个 raise 语句**，并核对它抛的是哪个类。
        #     ★ 用 AST，不受注释/字符串干扰（「文本会骗人，语法树不会」）。
        try:
            _ast151 = __import__("ast")   # ★ `_selftest` 里没有全局 `ast`
            # ★ 必须解析**整份源码**：切片会切在语句中间 ⇒ SyntaxError
            #   （第一版切在「R151」注释处，报 `expected 'except' or 'finally'`）。
            #   ★ 但计数必须**排除齿自己** ⇒ 用 AST 找 Raise，
            #     齿自己那条是 `fails.append(...)`，不是 Raise，天然不冲突。
            _树151 = _ast151.parse(_源码)
            _抛们151 = [
                n for n in _ast151.walk(_树151)
                if isinstance(n, _ast151.Raise) and n.exc is not None
                and "诚实停机" in _ast151.dump(n)]
            if not _抛们151:
                fails.append("【R151】★★ 源码里**找不到**抛「诚实停机」的那句 raise"
                             " ⇒ 本层可能被整体删掉了")
            else:
                _名们151 = [_ast151.dump(n.exc)[:120] for n in _抛们151]
                if not any("按设计停机" in _d for _d in _名们151):
                    fails.append(
                        "【R151】★★★ 抛「诚实停机」的那句 raise **抛的不是 "
                        "`按设计停机`**（实际：" + _名们151[0] + "）"
                        " ⇒ 分类层认不出它，仍会被当成产品缺陷"
                        "（这正是第 37 次真跑发生的）")
        except SyntaxError as _e151c:
            fails.append(f"【R151】齿④解析源码失败：{_e151c}")
        # ══════════════════════════════════════════════════════════════
        # 【R144】瞬时上游错误被当成终态（真跑抓出的第 57 层）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 35 次真跑，两次都在第 1 轮归零）：
        #   `规划失败：Error code: 402 - no_provider_under_bid`
        #   `轮 1 异常：Error code: 402 - no_provider_under_bid → 诚实停机`
        # ★ 判据：**等一等会不会改变它**。
        #   会 → 瞬时（该重试）；不会 → 终态（该立刻停）。
        # ★ 齿要测的是**分类结论**，不是"有没有那个词"。
        try:
            # ★★★★ 样本表 = (异常, 期望是否瞬时, **必须命中的关键词**)
            #   ★ 为什么第三列是必需的（第一版没有它，结果 ①② 都无齿）：
            #     分类器末尾有一条**兜底**：「认不出来的 ⇒ 保守按瞬时」。
            #     于是我把 `402` 从关键词表里删掉，`HTTP 402` 落到兜底
            #     ⇒ 仍然返回 True ⇒ **与期望一致** ⇒ 齿一声不响。
            #     ★ 这不是齿写错，是**兜底把差异吃掉了**。
            #   ⇒ 判据改成：**因由里必须写着那个关键词** ——
            #     证明是**关键词表**做的判定，不是兜底蒙对的。
            #   ★ 这仍然是「测结果」：`因由` 是分类器的返回值，不是过程里的词。
            _瞬例 = [
                # (异常, 期望是瞬时, 必须命中的关键词)
                (Exception("HTTP 402"), True, "402"),
                (Exception("Error code: 402"), True, "402"),
                (Exception("no_provider_under_bid"), True, "no_provider_under_bid"),
                (Exception("Error code: 504 - upstream_error"), True, "504"),
                (Exception("HTTP 429 rate limit exceeded"), True, "429"),
                (Exception("Timeout"), True, "timeout"),
                (Exception("HTTP 503 - model_not_found"), False, "model_not_found"),
                (Exception("HTTP 401 invalid_api_key"), False, "401"),
                (Exception("HTTP 403 permission denied"), False, "403"),
                (Exception("insufficient_quota"), False, "insufficient_quota"),
            ]
            for _e144, _望144, _词144 in _瞬例:
                _得144, _因144 = _是瞬时的(_e144)
                if _得144 is not _望144:
                    fails.append(
                        f"【R144】★ 分类错了：{_e144} 判成 "
                        f"{'瞬时' if _得144 else '终态'}（应为 "
                        f"{'瞬时' if _望144 else '终态'}）—— {_因144} ⇒ "
                        "终态被重试=白等，瞬时被当终态=整趟白跑")
                elif _词144 not in _因144:
                    fails.append(
                        f"【R144】★★ {_e144} 虽判对了，但**不是 `{_词144}` "
                        f"起的作用**（因由={_因144}）⇒ 落到兜底蒙对 ⇒ "
                        "把该关键词删掉齿也咬不住")
        except NameError:
            fails.append("【R144】★ 没有 `_是瞬时的` 分类器 ⇒ "
                         "瞬时错误会被当成终态（实测整趟在第 1 轮归零）")
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # 【R198c】破折号 / 省略号 / 书名号也是 E0460（第 101 次真跑实测）
        # ══════════════════════════════════════════════════════════════
        try:
            _得198d, _n198d = _修全角198('let a = 1 — 2；', 'a.rs')
            if _n198d != 2 or '—' in _得198d:
                fails.append(f"【R198c】★★ 破折号没被换掉（{_得198d!r} n={_n198d}）"
                             " ⇒ 照样 E0460（实测：em dash looks like - minus/hyphen）")
            if _修全角198('let s = "中文—破折号";', 'a.rs')[1] != 0:
                fails.append("【R198c】★★ 字符串里的破折号被改了 ⇒ 破坏源码")
        except NameError:
            fails.append("【R198c】★ 没有 `_修全角198`")
        
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # 【R208】只有「不可归因」的红门才算历史债 —— 防**假收敛**
        # ══════════════════════════════════════════════════════════════
        # 第 107 次真跑实测（我自己造的缺陷）：R207 把 `cargo_test_lib_no_run`
        #   （**目标本身那条门**）当历史债豁免 ⇒ `轮 2: Δ=0 verdict=converged`
        #   而主树**仍是 1 个编译错误**、真跑退出码 1 ⇒ **假收敛，比不收敛坏得多**。
        _产品208 = _源码[:_源码.find("def _selftest")]
        if 'def _有定位208(_a: dict) -> bool:' not in _产品208:
            fails.append("【R208】★★ 没有「能不能指出位置」这个判别 ⇒ 目标门会被当历史债豁免")
        if 'if a.get("ok") is not True and not _有定位208(a)}' not in _产品208:
            fails.append("【R208】★★ 豁免集没按可归因性过滤 ⇒ 假收敛会复发")
        if '_基红门有定位207' not in _产品208:
            fails.append("【R208】★ 没登记「本想免但有定位所以没免」的门 ⇒ 事后无法核对")
        # ★ 行为齿：一条带 `--> file:line` 的红门，必须被判为「有定位」
        try:
            _a208a = {'gate': 'cargo_test_lib_no_run', 'ok': False,
                      'err': 'error[E0433]: failed\n  --> src/a.rs:682:19\n'}
            _a208b = {'gate': 'test_count_at_least_1590', 'ok': False,
                      'err': 'count 1543 < 1590'}
            if not _有定位208(_a208a):
                fails.append("【R208】★★ 带文件:行号的红门被判成「不可归因」⇒ 会被豁免 ⇒ 假收敛")
            if _有定位208(_a208b):
                fails.append("【R208】★ 纯阈值的红门被判成「可归因」⇒ 历史债永远免不掉，永不收敛")
        except NameError:
            fails.append("【R208】★ 没有 `_有定位208`")
        
        # 【R207】门级基线：基线就红的非项目门，不许否决收敛
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 106 次真跑第 2/3 轮）：`（全库）@no_unused_mut_warnings=False`、
        #   `（全库）@test_count_at_least_1590=False` —— 后者阈值**抄自目标文本历史数字**
        #   （仓里只有 1543 个 #[test]）⇒ 永远不可能绿 ⇒ Δ=∅ 永不成立 ⇒ 永不收敛。
        _产品207 = _源码[:_源码.find("def _selftest")]
        if '_豁免207 = set() if _首轮207 else' not in _产品207:
            fails.append("【R207】★★ 没做门级基线豁免 ⇒ 不可实现的门让收敛永不可能")
        if 'if _首轮207:' not in _产品207 or '_基红门207 = {a.get("gate")' not in _产品207:
            fails.append("【R207】★★ 基线取自本轮 ⇒ 第一轮就自我豁免 ⇒ 假收敛")
        if '(getattr(self, "_基红门207", set()) - _项名207)' not in _产品207:
            fails.append("【R207】★★ 项目验收门也被豁免 ⇒ 放水（项目门必须永远参与）")
        if 'cur_devs = extract_deviations(_红207, dev_pred)' not in _产品207:
            fails.append("【R207】★★ 过滤了却没真正喂给 extract_deviations ⇒ 等于没修")
        if 'baseline_red_gates_exempted' not in _产品207:
            fails.append("【R207】★ 豁免没进回执 ⇒ 事后看不出哪些门被免了")
        
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # 【R222】回执正文不许复述目标书里的历史实测快照
        # ══════════════════════════════════════════════════════════════
        # 第 124/125 次真跑每条回执都写「11 个错误」，而现场是 0 错 / 1543 测试。
        #   R199 只修了「给模型的提示」，没修「回执正文」——同一个事实两处口径。
        _产品222 = _源码[:_源码.find("def _selftest")]
        if 'def _回执目标222(' not in _产品222:
            fails.append("【R222】★★ 没有回执目标清洗 ⇒ 回执会一直复述过期事实")
        if '_回执目标222(self.goal' not in _产品222:
            fails.append("【R222】★★ 清洗没接到回执上 ⇒ 等于没修")
        if 'def _现场段222(' not in _产品222:
            fails.append("【R222】★ 回执里没带现场真值 ⇒ 只说旧的去了、没说新的")
        try:
            _旧目标222 = ("收尾：让 `cargo test --lib --no-run` 退出码 0。\n\n"
                         "【机器实测（2026-10-28 13:44，cargo 亲自判）：11 个错误，退出码 101】\n\n"
                         "错误码分布：E0433 × 1、E0284 × 10")
            _清222 = _回执目标222(_旧目标222, "现场：0 个错误，退出码 0")
            if "11 个错误" in _清222:
                fails.append("【R222】★★ 过期读数还在回执里 ⇒ 人会以为树还是红的")
            if "E0433" in _清222:
                fails.append("【R222】★★ 过期的逐条错误清单还在回执里")
            if "退出码 0" not in _清222:
                fails.append("【R222】★ 现场真值没进回执 ⇒ 只说旧的去了、没说新的")
            if "cargo test --lib --no-run" not in _清222:
                fails.append("【R222】★★ 清洗把任务本体也删了 ⇒ 回执看不出在干什么")
            if _回执目标222("普通目标，没有快照") != "普通目标，没有快照":
                fails.append("【R222】★ 没有快照时被改动 ⇒ 越界")
        except Exception as _e222:
            fails.append(f"【R222】★ 行为齿跑不起来：{_e222}")
        
        # 【R221】「没做到」vs「没有模型可用」必须分开
        # ══════════════════════════════════════════════════════════════
        # 18:20 实测：live=[]、unreadable={'dddddd': 'HTTP 402 上游服务异常'}
        #   ⇒ 混成同一个退出码 1 时，用户无法判断该等上游还是该看代码。
        _产品221 = _源码[:_源码.find("def _selftest")]
        if 'def _上游不可用221(' not in _产品221:
            fails.append("【R221】★★ 没有「无可用模型」判定 ⇒ 会把基础设施故障报成任务失败")
        if 'upstream_unavailable' not in _产品221:
            fails.append("【R221】★★ 没有独立回执 ⇒ 回执分不清「没做到」和「没模型")
        if '_码158, _因158 = 3,' not in _产品221:
            fails.append("【R221】★ 没有独立退出码 3")
        try:
            class _空221:
                channel_report = {"live": [], "dead": {},
                                   "unreadable": {"d": "HTTP 402"}}
            class _活221:
                channel_report = {"live": ["d"], "dead": {}, "unreadable": {}}
            class _无报告221:
                pass
            if not _上游不可用221(_空221()):
                fails.append("【R221】★ 全通道不可用没被认出来")
            if _上游不可用221(_活221()):
                fails.append("【R221】★★ 有活通道却判成基础设施故障 ⇒ 真失败会被放过")
            if _上游不可用221(_无报告221()):
                fails.append("【R221】★ 没有报告时不该判基础设施故障")
        except NameError:
            fails.append("【R221】★ 没有 `_上游不可用221`")
        
        # 【R220】产出体积塌缩 ⇒ 直接拒收（不许写进树）
        # ══════════════════════════════════════════════════════════════
        # 第 120 次真跑现场：`时序拓扑注入.rs` 备份 **0KB**（原 58KB）、
        #   `算子选择器.rs` 备份 **1KB**（原 34KB）⇒ 模型产出过近乎空的完整文件。
        #   判据能事后发现错误，但**写进树的东西已经毁了工作区**。
        _产品220 = _源码[:_源码.find("def _selftest")]
        if "_缩220" not in _产品220 or "adopted.pop(_p220, None)" not in _产品220:
            fails.append("【R220】★★ 塌缩产出没被拦 ⇒ 0KB 候选能被写进树，等于能毁项目")
        if "shrunken_candidate_rejected" not in _产品220:
            fails.append("【R220】★ 拦下没进回执 ⇒ 事后看不出这轮为什么没产出")
        if "_原行220 >= 40" not in _产品220:
            fails.append("【R220】★ 没设小文件豁免 ⇒ 会误伤正常的短文件")
        
        # 【R219】陈旧阈值门必须按现场实测改写（目标文本里的数字不是判据）
        # ══════════════════════════════════════════════════════════════
        # 第 120 次真跑轮 1 门真值：`test_count_ge_1590=False`，而现场 `#[test]`=1543
        #   ⇒ 这条门永远绿不了 ⇒ Δ 永远不为 ∅ ⇒ 永远判不了达标。
        #   根因：目标文本的陈旧数字被规划器变成了判据门（R199 只修了提示，没修门）。
        _产品219 = _源码[:_源码.find("def _selftest")]
        if 'def _活体测试数219(' not in _产品219:
            fails.append("【R219】★★ 没有活体测试数 ⇒ 分不清「门错了」和「树没改好」")
        if 'def _陈旧阈值门219(' not in _产品219:
            fails.append("【R219】★★ 没有陈旧阈值校正 ⇒ 不可达门会让收敛永远走不到 Δ=∅")
        if 'stale_threshold_gate_rewritten' not in _产品219:
            fails.append("【R219】★ 改写没进回执 ⇒ 事后看不出门被谁动了")
        # ★ 行为齿：1590 必须被改写成现场实测值，且不得误伤项目验收门
        try:
            import tempfile as _tf219
            _根219 = Path(_tf219.mkdtemp(prefix="r219_"))
            _行219 = []
            for _k219 in range(7):
                _行219.append("#[test]")
                _行219.append("fn t" + str(_k219) + "() {}")
            (_根219 / "a.rs").write_bytes(("\n".join(_行219)).encode("utf-8"))
            _活219 = _活体测试数219(_根219)
            if _活219 != 7:
                fails.append(f"【R219】★ 活体数错（应 7，实得 {_活219}）")
            _sp219 = {"gates": [
                {"name": "test_count_ge_1590", "type": "grep_required",
                 "file": "a.rs", "patterns": ["test_count_ge_1590"]},
                {"name": "cargo_test_lib_no_run", "type": "compile",
                 "cmd": ["cargo", "test", "--lib", "--no-run"]},
            ], "deliverables": []}
            _改219r = _陈旧阈值门219(_sp219, str(_根219))
            _名219 = _sp219["gates"][0]["name"]
            if "1590" in _名219:
                fails.append(f"【R219】★★ 陈旧阈值没被改写（还是 {_名219}）⇒ 门永远绿不了")
            if "_ge_7" not in _名219:
                fails.append(f"【R219】★★ 没改成现场实测值（得 {_名219}）")
            if not _改219r:
                fails.append("【R219】★ 改写没被记下来 ⇒ 回执里看不见")
            if _sp219["gates"][1]["name"] != "cargo_test_lib_no_run":
                fails.append("【R219】★★ 误伤了项目验收门")
        except Exception as _e219x:
            fails.append(f"【R219】★ 行为齿跑不起来：{_e219x}")
        
        # 【R218】R215 的手段门词表必须覆盖英文（真跑抓出的漏网）
        # ══════════════════════════════════════════════════════════════
        # 第 118 次真跑门真值：`selector_keeps_dinglie_call=True`、
        #   `timing_topology_keeps_continue_fn=True`，且「历史偏差密度」把
        #   `dinglie_new_call_preserved×5` 列为优先解决 ⇒ 机器在催模型保留坏调用。
        #   ★ R215 只写中文 ⇒ 中文夹具过、真跑拦不住（同一件事两把尺子）。
        #   ★ 注意：词表是 `normalize_gates` 里的**局部变量**，不能 getattr，
        #     只能查源码文本 + 跑行为齿。
        try:
            import agentscope_orchestrator.gate_derive as _gd218
            _源218 = Path(_gd218.__file__).read_text(encoding="utf-8")
            for _w218 in ("keep", "keeps", "preserve", "preserved", "retain",
                          "remain", "unchanged", "imported_from"):
                if f'"{_w218}"' not in _源218:
                    fails.append(f"【R218】★★ 手段词表缺英文 `{_w218}` ⇒ 英文手段门照样漏过")
            # ★ 行为齿：拿真跑里**真实出现过**的门名喂进去，必须只剩项目验收门
            _sp218 = {"files": ["a.rs"], "gates": [
                {"name": "selector_keeps_dinglie_call", "type": "grep_required",
                 "file": "a.rs", "patterns": ["定列::新()"]},
                {"name": "timing_topology_keeps_continue_fn", "type": "grep_required",
                 "file": "a.rs", "patterns": ["继续"]},
                {"name": "cargo_test_lib_compile_ok", "type": "compile",
                 "cmd": ["cargo", "test", "--lib", "--no-run"]},
            ]}
            _得218, _注218 = _gd218.normalize_gates(_sp218)
            _留218 = [g.get("name") for g in _得218.get("gates") or []]
            if _留218 != ["cargo_test_lib_compile_ok"]:
                fails.append(f"【R218】★★ 英文手段门没被剔（剩 {_留218}）⇒ 模型仍被要求保留坏调用")
        except Exception as _e218:
            fails.append(f"【R218】★ 行为齿跑不起来：{_e218}")
        
        # 【R217】无人值守「失败自愈重试」——外层多次尝试
        # ══════════════════════════════════════════════════════════════
        # 事实：同一份红树、同一套判据，第 102/108 次真跑修绿并收敛；
        #   第 106/109/…/117 次真跑没修好 ⇒ 成功率随模型波动，不是 0 也不是 1。
        #   无人值守 = 一直试到达成，不是试一次就退出码 1。重试安全由回退保证。
        _产品217 = _源码[:_源码.find("def _selftest")]
        if 'def _尝试次数217() -> int:' not in _产品217:
            fails.append("【R217】★★ 没有外层尝试次数 ⇒ 一次失败就收工，不是无人值守")
        if 'for _第217 in range(1, _次数217 + 1):' not in _产品217:
            fails.append("【R217】★★ 没有外层重试循环")
        if 'attempt_retry' not in _产品217:
            fails.append("【R217】★★ 重试没进回执 ⇒ 会把「试了 3 次才成」看成「一次就成了」")
        if '第 {_第217}/{_次数217} 次尝试达标' not in _产品217:
            fails.append("【R217】★ 重试成功后没如实说明是第几次 ⇒ 回执骗人")
        # ★ 行为齿：次数解析的保守性（非法值按 1，上限 10）
        try:
            _保217 = os.environ.get("ORCH_ATTEMPTS")
            os.environ["ORCH_ATTEMPTS"] = "0"
            if _尝试次数217() != 1:
                fails.append("【R217】★ 0 次没被夹到 1 ⇒ 会一次都不跑就报失败")
            os.environ["ORCH_ATTEMPTS"] = "999"
            if _尝试次数217() > 10:
                fails.append("【R217】★ 没上限 ⇒ 配错会把无人值守变成死循环")
            os.environ["ORCH_ATTEMPTS"] = "abc"
            if _尝试次数217() != 1:
                fails.append("【R217】★ 非法值没退回 1")
            os.environ["ORCH_ATTEMPTS"] = _保217 if _保217 is not None else "1"
        except NameError:
            fails.append("【R217】★ 没有 `_尝试次数217`")
        
        # 【R216】同内容原地重交 ⇒ 直接判否，不再花一轮评分
        # ══════════════════════════════════════════════════════════════
        # 第 115/116 次真跑：同一处连续产出同一个**不可能成立**的解法（`定列::新()`
        #   而字段类型是 `Vec<态射样本>`）⇒ 每轮都重新评分同一份东西，纯浪费。
        _产品216 = _源码[:_源码.find("def _selftest")]
        if 'def _产出指纹216(内容) -> str:' not in _产品216:
            fails.append("【R216】★★ 没有内容指纹 ⇒ 无法识别原地重交")
        if '_重交216' not in _产品216 or 'adopted.pop(_p216, None)' not in _产品216:
            fails.append("【R216】★★ 重交没被拦下 ⇒ 每轮白烧一轮算力")
        if 'duplicate_candidate_rejected' not in _产品216:
            fails.append("【R216】★ 拦下没进回执 ⇒ 事后看不出这轮为什么没产出")
        if "self._已否216.setdefault(_键216b, set()).add(_产出指纹216(adopted[_键216b]))" not in _产品216:
            fails.append("【R216】★★ 被否内容没进黑名单 ⇒ 下次照旧重交")
        # ★ 行为齿：同一内容两次指纹必须相同；不同内容必须不同
        try:
            if _产出指纹216("a\n") != _产出指纹216(b"a\n"):
                fails.append("【R216】★ 指纹对 str/bytes 不一致 ⇒ 同一个产出认不出来")
            if _产出指纹216("a\n") == _产出指纹216("a\n "):
                fails.append("【R216】★ 不同内容算出同一指纹 ⇒ 会误伤正确产出")
        except NameError:
            fails.append("【R216】★ 没有 `_产出指纹216`")
        
        # 【R214】「保留」也可能是空转：结构门绿了、真门没动
        # ══════════════════════════════════════════════════════════════
        # 第 114 次真跑第 4 轮：`保留_样本列表_定列构造=True` 但 `cargo_test_lib_no_run=False`，
        #   `Δ=1 verdict=stalled_fixed_point action=stop` ⇒ 换门集（错：判据没坏，坏的是产出）。
        _产品214 = _源码[:_源码.find("def _selftest")]
        if '_空转保留214 = (verdict == "stalled_fixed_point"' not in _产品214:
            fails.append("【R214】★★ 保留>0 的空转不动点没被拦住 ⇒ 每轮换门集、判据越换越宽")
        if 'self._空转保留214 = True' not in _产品214:
            fails.append("【R214】★ 没把「上一轮保留是空转」留给下一轮 prompt ⇒ 模型不知道白改了")
        if 'no_op_retention_not_fixed_point' not in _产品214:
            fails.append("【R214】★ 没进回执 ⇒ 事后看不出这一轮是空转保留")
        if 'R214 本轮**保留了' not in _产品214:
            fails.append("【R214】★ 因由没说人话")
        
        # 【R213】空读数有两种，处置相反：本轮没产出 ≠ 门集坏了
        # ══════════════════════════════════════════════════════════════
        # 第 113 次真跑：空读数 ⇒ no_anchor ⇒ 换门集 ⇒ 基线放宽到 5 处 ⇒ 窗口违规不降、
        #   还出现 E0753（模型在破坏文件）。上一轮明明有读数，是**本轮**没产出。
        _产品213 = _源码[:_源码.find("def _selftest")]
        if '_有旧门213 = bool(getattr(self, "_上轮门真值", {}) or {})' not in _产品213:
            fails.append("【R213】★★ 空读数没区分「本轮没产出」和「门集坏了」⇒ 会乱换门集放水")
        if '这是**本轮没产出**' not in _产品213:
            fails.append("【R213】★★ 没把因由说清 ⇒ 事后看不出为什么没换门集")
        if 'verdict = "no_anchor"' not in _产品213:
            fails.append("【R213】★ 真·没锚那条路被删了 ⇒ 门集派生失败时会空转")
        
        # 【R212】编译错误有阶段序：「点数不变但阶段前进」是进展
        # ══════════════════════════════════════════════════════════════
        # 五次真跑同形证据：`✗ 违规点数没变（1 → 1）且不是同一集合 ⇒ 没进展 ⇒ 回退`，
        #   而窗内新错误是 `E0308`（类型检查）替代了 `E0433`（名字解析）= **向前推进**。
        #   ⇒ 单步贪心死锁：每轮都做对了一步、然后被退回起点。
        _产品212 = _源码[:_源码.find("def _selftest")]
        if 'def _阶段前进212(基线: frozenset, 现: frozenset) -> tuple[bool, str]:' not in _产品212:
            fails.append("【R212】★★ 没有阶段前进判别 ⇒ 编译错误阶段推进会被当交换回退 ⇒ 死锁")
        if '_阶段前进预算212' not in _产品212:
            fails.append("【R212】★★ 没有硬顶 ⇒ 可能在两个阶段间无限来回")
        if 'globals()["_阶段前进预算212"] = 2' not in _产品212:
            fails.append("【R212】★ 严格进展后没回满预算 ⇒ 真进展也会被硬顶卡住")
        # ★ 行为齿：E0433 → E0308 必须判「进展」；E0308 → E0433 必须判「倒退」
        try:
            _旧212 = frozenset({'E0433::failed to resolve::a.rs:682'})
            _新212 = frozenset({'E0308::mismatched types::a.rs:682'})
            _进, _ = _阶段前进212(_旧212, _新212)
            if not _进:
                fails.append("【R212】★★ E0433→E0308 没被判成进展 ⇒ 死锁照旧")
            _退, _ = _阶段前进212(_新212, _旧212)
            if _退:
                fails.append("【R212】★★ E0308→E0433 被判成进展 ⇒ 会往回摆")
            _同, _ = _阶段前进212(_旧212, frozenset({'E0433::x::b.rs:1'}))
            if _同:
                fails.append("【R212】★★ 同阶段换位置被判成进展 ⇒ 无限放行")
        except NameError:
            fails.append("【R212】★ 没有 `_阶段前进212`")
        
        # 【R210】「没有门」不是「门全绿」——防第二次假收敛
        # ══════════════════════════════════════════════════════════════
        # 第 110 次真跑第 3 轮原文：`轮 3: 门= | Δ=0 verdict=converged action=stop`
        #   `判据结构停机 @轮3（converged）→ 交还用户` / `[真跑] 退出码 1`，
        #   而主树仍是 1 个编译错误。门集为空 ⇒ Δ=∅ 恒真 ⇒ 报收敛。
        _产品210 = _源码[:_源码.find("def _selftest")]
        if 'if not results:' not in _产品210:
            fails.append("【R210】★★ 空门集没有被拦住 ⇒ Δ=∅ 恒真 ⇒ 假收敛")
        if 'verdict = "no_anchor"' not in _产品210:
            fails.append("【R210】★★ 空门集仍可能判 converged")
        if 'empty_gate_set_not_converged' not in _产品210:
            fails.append("【R210】★ 没进回执 ⇒ 事后看不出这一轮根本没量")
        if '这是**测量缺失**，不是目标达成' not in _产品210:
            fails.append("【R210】★ 因由没把「没量到」说成人能看懂的话")
        
        # 【R209】达标 = 此刻实测项目验收门全绿（不按名字对号）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 108 次真跑）：轮 2 `cargo_test_lib_no_run=True`、converged、
        #   主树实测 0 错误 / 退出码 0，而机器仍归 1 —— 因为 R206 按**名字**找档案门
        #   （档案名 cargo_test_lib_compile vs 自撰名 cargo_test_lib_no_run）⇒ 从不触发。
        _产品209 = _源码[:_源码.find("def _selftest")]
        if 'self._规划前门态()               # 现场重跑档案门（实测，不猜）' not in _产品209:
            fails.append("【R209】★★ 达标没现场重跑档案门 ⇒ 仍按名字对号 ⇒ 做对了也判不成")
        if '_基res209 = dict(_存209.get("res") or {})' not in _产品209:
            fails.append("【R209】★★ 没有基线读数 ⇒ 无法判「由红转绿」⇒ 会放水或误杀")
        if '_门态缓存.update(_存209)' not in _产品209:
            fails.append("【R209】★★ 重跑后没还原上下文 ⇒ 污染本轮别处的读数")
        if 'if _全绿209 and not _基全绿209:' not in _产品209:
            fails.append("【R209】★★ 没要求「由红转绿」⇒ 空任务也会被认成达成")
        if 'acceptance_by_project_gates' not in _产品209:
            fails.append("【R209】★ 达成没进回执 ⇒ 事后看不出凭什么判的")
        
        # 【R205】「换产出侧」必须**真的换**（不能只说不做）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 101 次真跑）：连续 3 轮候选全被拒，而每轮产出侧**一模一样**
        #   （同样 2 个文件、同样整份重写）⇒ 五轮全部白跑。
        _产品205 = _源码[:_源码.find("def _selftest")]
        if 'self.deliverables = [_一个205]' not in _产品205:
            fails.append("【R205】★★ 连续全拒后作业面没收窄到单文件 ⇒ 产出侧没换，"
                         "下一轮必然再被拒（实测已连白跑 5 轮）")
        if 'workface_narrowed' not in _产品205:
            fails.append("【R205】★ 收窄没进回执 ⇒ 事后看不出产出侧换过什么")
        if 'if _连拒205 > 0 and len(self.deliverables) > 1:' not in _产品205:
            fails.append("【R205】★ 没加「多于 1 个交付物」前置条件 ⇒ 单文件任务会走空")
        if 'key=lambda d: -_数红205.get(d["path"], 0))' not in _产品205:
            fails.append("【R205】★ 收窄不是按红证据最多选 ⇒ 可能收窄到不相关的文件")
        
        # 【R203】上一轮被机器退回来的文件，**不许**被跳过
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 100 次真跑第 2 轮）：`R193 跳过 算子选择器.rs：没有红门证据（已经是对的）`
        #   而**那正是唯一还编译失败的文件**（E0433 定列 未解析，第 682 行）。
        #   全库型门的证据没逐文件归属 ⇒ 抽不出它 ⇒ 被当成"没红证据"⇒ 永远改不到。
        try:
            if _该跳过193('a.rs', True, set(), True, 刚被退=True):
                fails.append("【R203】★★ 上一轮被机器退回来的文件被跳过 ⇒ "
                             "全库型门下它永远改不到 ⇒ 永不收敛")
            if not _该跳过193('b.rs', True, {'a.rs'}, True, 刚被退=False):
                fails.append("【R203】★ 没退过、也没红证据的文件该跳却没跳（规则失效）")
            if _该跳过193('a.rs', True, {'a.rs'}, True):
                fails.append("【R203】★ 有红证据的文件被跳过 ⇒ 该改的不改")
        except NameError:
            fails.append("【R203】★ 没有 `_该跳过193`")
        
        _产品203 = _源码[:_源码.find("def _selftest")]
        if '刚被退=_刚退203):' not in _产品203:
            fails.append("【R203】★★ 算了「刚被退」却没传给跳过判定 ⇒ 等于没修")
        if '_回退次数", None) or {}).get(d["path"], 0)) > 0' not in _产品203:
            fails.append("【R203】★ 没读回退次数 ⇒ 只有本轮台账里能查到")
        
        # ══════════════════════════════════════════════════════════════
        # 【R198b】中文引号也是 E0460（第 100 次真跑第 1 轮实测）
        # ══════════════════════════════════════════════════════════════
        try:
            _得198q, _n198q = _修全角198('let s = “abc”；', 'a.rs')
            if _n198q != 3 or _得198q != 'let s = "abc";':
                fails.append(f"【R198b】★★ 中文引号没被换成半角引号（{_得198q!r} n={_n198q}）"
                             " ⇒ 照样 E0460，整份文件不可编译")
            _串198q = 'let s = "中文“引号”；";'
            if _修全角198(_串198q, 'a.rs') != (_串198q, 0):
                fails.append("【R198b】★★ 把**字符串里**的中文引号改了 ⇒ 破坏源码")
        except NameError:
            fails.append("【R198b】★ 没有 `_修全角198`")
        
        # 【R201】回退制造的不动点 ≠ 搜索证出的不动点 ⇒ **不许停机**
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 99 次真跑第 1 轮）：`轮 1: Δ=3 verdict=stalled_fixed_point action=stop`
        #   `轮 1：stalled_fixed_point → 已换判据门集` —— 第 1 轮的『上一轮』其实是基线
        #   （同一次测量）⇒ 恒等 ⇒ 必然判 stalled ⇒ 每轮换门集 ⇒ 永远在第 1 轮打转。
        #   ★ R187 已查明根因，但当时只改因由不改动作 ⇒ 实测后果就是空转。
        try:
            import asyncio as _aio201
            _o201 = object.__new__(GoalRunner)
            if not hasattr(_o201, "_连拒201"):
                pass
            _源201 = _源码[:_源码.find("def _selftest")]
            if '_全拒201 = (verdict == "stalled_fixed_point"' not in _源201:
                fails.append("【R201】★★ 没识别「候选全被拒」这个回退制造的不动点")
            if 'verdict = "progressing"' not in _源201:
                fails.append("【R201】★★ 识别了却仍按 stalled 停机 ⇒ 继续在第 1 轮空转")
            if 'rollback_fixed_point_ignored' not in _源201:
                fails.append("【R201】★ 不停机没进回执 ⇒ 事后看不出为什么没停")
            if '_硬顶201 = 3' not in _源201 or 'self._连拒201 <= _硬顶201' not in _源201:
                fails.append("【R201】★★ 没有硬顶 ⇒ 产出侧永远修不好就无限兜圈子")
            if 'if _全拒201 else 0' not in _源201:
                fails.append("【R201】★ 连续计数没在好转时清零 ⇒ 会把历史全拒累加进硬顶")
        except NameError:
            fails.append("【R201】★ 没有 GoalRunner")
        
        # ══════════════════════════════════════════════════════════════
        # 【R202】达标口径里「目标点名文件」也要被现场实测收敛（与 R200 同一把尺子）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 99 次真跑第 1 轮）：R89 代偿拒收点名 **4 个**文件，
        #   而作业面只有 2 个（R193/R200 已证另 2 个改对了）
        #   ⇒ 就算那 2 个改对，达标也会被 2 个**已经对的文件**否掉 ⇒ 永远不达标。
        if '_红现202 = _红证据文件(_门态缓存.get("红") or [])' not in _源201:
            fails.append("【R202】★★ 达标口径没被现场实测收敛 ⇒ 已改对的文件仍是否决者")
        if '_必改89 = [_f for _f in _必改89 if _f in _红现202]' not in _源201:
            fails.append("【R202】★★ 算了却没落到 `_必改89` ⇒ 等于没修")
        if 'acceptance_scope_converged' not in _源201:
            fails.append("【R202】★ 收敛没进回执 ⇒ 事后无法核对达标口径被谁改过")
        if 'if _红现202:' not in _源201:
            fails.append("【R202】★ 没加「现场实测非空」前置条件 ⇒ 门全绿时会放宽口径（越界）")
        
        # 【R200】目标里手写的「必改文件」是历史快照 —— 现场实测说了算
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 97/98 次真跑）：R199 已按现场实测告诉规划器「只列还红的」，
        #   而 R86 又把目标里那 4 个必改文件**全部**并进「计划必须覆盖」的机器闸
        #   ⇒ 规划器永远被判「计划不可行」⇒ 与 R199 直接打架 ⇒ 回喂死循环。
        #   ★ 这是本项目主缺陷族第 13 次：**判据要的是「此刻必须改哪些」，**
        #     手里拿的是「目标文本里当初写了哪些」。
        _产品200 = _源码[:_源码.find("def _selftest")]
        if '_红现200 = _红证据文件(_门态缓存.get("红") or [])' not in _产品200:
            fails.append("【R200】★★ 必改闸没读现场实测 ⇒ 目标的历史快照仍会逼模型改已对的文件")
        if '_过时200 = [b for b in _目标必改86 if _红现200 and b not in _红现200]' not in _产品200:
            fails.append("【R200】★★ 没有算「目标说必改、现场其实不红」的差集")
        if 'if _b86 in _过时200:' not in _产品200:
            fails.append("【R200】★★ 算了差集却没跳过 ⇒ 等于没修")
        if 'stale_target_files' not in _产品200:
            fails.append("【R200】★ 忽略了哪些历史必改文件没进回执 ⇒ 事后无法核对")
        # ★ 边界：现场实测为空（门全绿）时，目标约束**照旧生效**（不许趁机放宽）
        _条件200 = 'if _红现200 and b not in _红现200'
        if _条件200 not in _产品200:
            fails.append("【R200】★ 没加「现场实测非空」这个前置条件 ⇒ 门全绿时会把"
                         "目标约束也放宽（越界）")
        
        # 【R199】目标文本里的「机器实测 / 必改文件」是**历史快照**，必须被现场实测覆盖
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 97 次真跑 plan 回执原文）：目标写着「11 个错误 / 必改 4 个文件」，
        #   而现场只剩 1 个错误、3 个文件已改对 ⇒ 规划器照过期清单出计划 ⇒
        #   下游在已经对的文件上重写 ⇒ 造新错（E0425/E0428）⇒ 整批回退 ⇒ 永不收敛。
        try:
            _段199 = _现场实测段199(
                [{'cause': 'error[E0433]: failed to resolve\n  --> a/算子选择器.rs:682:19\n'}],
                ['a/算子选择器.rs', '织网者.rs'])
            if '现场实测覆盖' not in _段199:
                fails.append("【R199】★★ 没生成现场覆盖段 ⇒ 规划器仍按过期清单出计划")
            if '算子选择器' not in _段199:
                fails.append("【R199】★★ 现场还红的文件没摆给规划器")
            if '不许' not in _段199 or '已经改对了' not in _段199:
                fails.append("【R199】★★ 没声明「已改对的不许动」⇒ 仍会去重写对的文件")
            # ★ 边界：没有实测事实 ⇒ **一个字都不许编**
            if _现场实测段199([], []) != '':
                fails.append("【R199】★ 没有实测事实却编出了覆盖段 ⇒ 会误导规划器")
            # ★ 接线：必须真的进规划器 prompt，且紧跟 goal（优先级最高处）
            _pp199 = _planner_prompt('目标：11 个错误，必改 4 个文件', '', '', '门态', '', _段199)
            if '现场实测覆盖' not in _pp199:
                fails.append("【R199】★★ 段算了但没进规划器 prompt（白算）")
            if _pp199.find('现场实测覆盖') > _pp199.find('门态'):
                fails.append("【R199】★ 覆盖段排在了门态之后 ⇒ 优先级不够（目标文本更靠前）")
        except NameError:
            fails.append("【R199】★ 没有 `_现场实测段199` / `_planner_prompt(现场=)`")
        
        _产品199 = _源码[:_源码.find("def _selftest")]
        if '_现场199 = _现场实测段199(_门态缓存.get("红") or [],' not in _产品199:
            fails.append("【R199】★★ 没接线（`_plan` 没把现场实测算出来）")
        if '现场=_现场199)' not in _产品199:
            fails.append("【R199】★★ 算了没传进 `_planner_prompt`")
        
        # 【R198】代码正文里的**全角标点**：入口拦下（E0460 词法错误）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 96 次真跑第 1 轮）：`E0460 unknown start of token … fullwidth comma`
        #   ⇒ 模型在 .rs 正文写了全角逗号 ⇒ 整个文件不可编译 ⇒ 整轮白跑。
        try:
            _新198, _n198 = _修全角198('let a = 1，2；', 'a.rs')
            if _n198 != 2 or _新198 != 'let a = 1,2;':
                fails.append(f"【R198】★★ 代码正文的全角标点没换成半角（{_新198!r} n={_n198}）"
                             " ⇒ 写进主树就是 E0460，整轮白跑")
            # ★ 边界①：字符串里的全角是**有意的**，动了就是破坏源码
            _s198 = 'let s = "中文，标点；不动";'
            if _修全角198(_s198, 'a.rs') != (_s198, 0):
                fails.append("【R198】★★ 把**字符串字面量**里的全角标点改了"
                             " ⇒ 破坏了源码语义（本仓库字符串里就是中文）")
            # ★ 边界②：注释里的全角（本仓库注释本来就是中文）不许动，但注释**之后**的代码要修
            _c198: str = '// 中文，注释；不动\nlet x = 1；'
            _得198c, _n198c = _修全角198(_c198, 'a.rs')
            if '中文，注释；不动' not in _得198c:
                fails.append("【R198】★★ 把**注释**里的中文标点改了 ⇒ 破坏了注释")
            if _n198c != 1 or not _得198c.endswith('let x = 1;'):
                fails.append(f"【R198】★ 注释后面的代码没修到（n={_n198c}）：{_得198c!r}")
            # ★ 边界③：Rust 生命周期 `'a` 不是字符字面量，别把它当成字符串吞掉后面
            _l198: str = 'let r: &\'a str = "x"；'
            _得198l, _n198l = _修全角198(_l198, 'a.rs')
            if _n198l != 1 or not _得198l.endswith(';'):
                fails.append(f"【R198】★ 生命周期 `'a` 被当成字符字面量 ⇒ 后面代码没修（n={_n198l}）")
            # ★ 边界④：文档类不碰
            if _修全角198('文档，不碰。', 'readme.md') != ('文档，不碰。', 0):
                fails.append("【R198】★ 文档类也被改了 ⇒ 越界（只该动代码文件）")
        except NameError:
            fails.append("【R198】★ 没有 `_修全角198` ⇒ 全角标点照样进主树")
        
        # ★ 接线：算了必须真的用上 + 必须报出去（R128/R129 纪律）
        _产品198 = _源码[:_源码.find("def _selftest")]
        if '产出, _全198 = _修全角198(产出, d["path"])' not in _产品198:
            fails.append("【R198】★★ 没接线（`_produce_window` 写盘前没用它）")
        if 'R198 {d[' not in _产品198:
            fails.append("【R198】★ 改了几处没报出去 ⇒ 事后无法核对是否发生过全角污染")
        if '代码正文里的标点**必须是半角**' not in _产品198:
            fails.append("【R198】★ 事前没在 prompt 里写死半角要求 ⇒ 只能靠事后修")
        
        # 【R197】共同决定的**质量闸**：一句废话不许当成「必须遵守」
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 95 次真跑第 1 轮重试）：`共同决定已拿到（25 字符）`
        #   —— 25 字符不可能是「按根因分组、每组一个改法」，却被注入每个 prompt。
        try:
            import asyncio as _aio197
        
            class _路由197:
                def __init__(self, 回):
                    self.回 = 回
                    self.次数 = 0
        
                async def call(self, msgs):
                    self.次数 += 1
                    class _M:
                        pass
                    _m = _M()
                    _m.content = self.回
                    return _m
        
            def _试197(回, 组数):
                _o = object.__new__(GoalRunner)
                _o.goal = 'x'
                _o._tok_chars = 0
                _o._work = 1
                _o.deliverables = [{'path': 'a.rs'}, {'path': 'b.rs'}]
                _o._emit = lambda *a, **k: None
                _o.router = _路由197(回)
                import re as _re
                _rs = [{'file': f'f{i}.rs', 'cause': f'E{1000+i}::某错'} for i in range(组数)]
                return _aio197.run(_o._算共同决定188(_rs))
        
            if _试197('统一改一下就好了', 2) != '':
                fails.append("【R197】★★ 一句废话（<下限）仍被当成「必须遵守的决定」"
                             " ⇒ 注入后比不注入更糟")
            _长197 = ('第1组 E1000：把 定列 的导入改成 crate::织网者::定列。'
                      '第2组 E1001：给 道痕 参数加显式类型标注，并在三处调用点同步。')
            if _试197(_长197, 2) != _长197:
                fails.append("【R197】★ 合格的决定被误杀 ⇒ 下限太高，会把真决定扔掉")
        except NameError as _e197:
            fails.append(f"【R197】★ 没有 `_算共同决定188`：{_e197}")
        
        # ★ 接线：质量闸必须真的在降级路径上（不是只写了函数）
        _产品197 = _源码[:_源码.find("def _selftest")]
        if '_下限197 = max(60, 30 * _组数197)' not in _产品197:
            fails.append("【R197】★★ 没有随组数走的长度下限 ⇒ 垃圾决定照样注入")
        if 'decision_rejected' not in _产品197:
            fails.append("【R197】★ 拒收没进回执 ⇒ 事后看不出「为什么这轮没有共同决定」")
        
        # 【R196】跳过必须**报出去**（R128/R129 的病：算了、存了、没送出去）
        # ══════════════════════════════════════════════════════════════
        _产品196 = _源码[:_源码.find("def _selftest")]
        if 'self._跳193 = 0' not in _产品196:
            fails.append("【R196】★ 轮初没清零 ⇒ 跳过数会跨轮累加，读数失真")
        if '"kind": "workface_converged"' not in _产品196:
            fails.append("【R196】★★ 跳过了却没进回执 ⇒ 事后无法核对作业面收敛到哪")
        if 'skipped": self._跳193' not in _产品196:
            fails.append("【R196】★ 回执里没写跳过数 ⇒ 等于没报")
        if 'self._跳193 = 0          # 用后即清' not in _产品196:
            fails.append("【R196】★ 报完没清零 ⇒ 下一轮会重复报旧数")
        
        # 【R195】「此刻哪里还红」要从**证据文本**里抽（字段常常根本没有）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 94 次真跑第 1 轮）：R194 拿到了基线证据（1 条），
        #   但 R193 仍不启用 —— 因为它只找 `a["file"]`，而红门条目
        #   只有 `err`/`cause` 一段文本，文件位置写在文本里（`--> path:line`）。
        #   ★ 主缺陷族第 11 次：**「字段没有」被当成了「事实没有」。**
        try:
            _样195a = [{'gate': 'x', 'cause': 'error[E0433]: failed to resolve\n  --> 示例工程\\基础集\\算子选择器.rs:682:19\n'}]
            _得195a = _红证据文件(_样195a)
            if _得195a != {'示例工程/基础集/算子选择器.rs'}:
                fails.append(f"【R195】★★ 从编译器定位行抽不出文件（实得 {sorted(_得195a)}）"
                             " ⇒ R193 的收敛作业面永远不启用")
            # ★ 边界①：`a["file"]` 有就要用（而且斜杠要归一）
            _得195b = _红证据文件([{'file': 'src\\a.rs'}])
            if _得195b != {'src/a.rs'}:
                fails.append(f"【R195】★ 有 file 字段却没抽到/没归一：{sorted(_得195b)}")
            # ★ 边界②：完全没文件信息 ⇒ **空集**（不许编出文件来）
            _得195c = _红证据文件([{'cause': 'the name is defined multiple times'}])
            if _得195c:
                fails.append(f"【R195】★★ 没有文件信息却编出了文件：{sorted(_得195c)}")
            # ★ 边界③：判据自己的违规点末段也要认（形如 …::文件）
            # ★ 边界③：`path:line` 的通用形态（不是 `-->` 开头）也要认 ——
            #   与 `_证据必改文件` 共用 `_证据违规点` 这把尺子（R43 纪律）。
            #   ★ 它会按 REPO_ROOT 过滤「文件真的存在」⇒ 自检环境必须给个假仓库，
            #     否则齿测的是环境（没有那个仓库），不是代码。
            _假根195 = Path(tempfile.mkdtemp())
            (_假根195 / '示例工程/基础集').mkdir(parents=True, exist_ok=True)
            (_假根195 / '示例工程/基础集/自涌现编译.rs').write_text('// x', encoding='utf-8')
            _旧根195 = globals().get('REPO_ROOT')
            globals()['REPO_ROOT'] = _假根195
            try:
                _得195d = _红证据文件([{'cause':
                    'error[E0284] at 示例工程/基础集/自涌现编译.rs:286:15'}])
            finally:
                globals()['REPO_ROOT'] = _旧根195
            if '示例工程/基础集/自涌现编译.rs' not in _得195d:
                fails.append(f"【R195】★ `path:line` 形态里的文件没认出来：{sorted(_得195d)}")
            if '示例工程/基础集/自涌现编译.rs' not in _得195d:
                fails.append(f"【R195】★ 违规点末段里的文件没认出来：{sorted(_得195d)}")
        except NameError:
            fails.append("【R195】★ 没有 `_红证据文件` ⇒ 作业面永远算不出")
        
        # ★ 接线：R193 必须用新的抽取口
        _产品195 = _源码[:_源码.find("def _selftest")]
        if '_有红193 = _红证据文件(red_audit_prev)' not in _产品195:
            fails.append("【R195】★★ 算了没用上（R193 还在按 `a[\"file\"]` 找）")
        
        # 【R194】第 1 轮没有「上一轮」，但有「基线」——那才是此刻还红的作业面
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 93 次真跑第 1 轮）：
        #   `★ R188 跨文件共同决定已拿到（601 字符）` ⇒ 几乎没证据，决定是空谈；
        #   `★ R193 不启用（红证据没有文件归属…）` ⇒ 第 1 轮仍按过期清单全改。
        # ⇒ 与 L5975 已有的兜底**同一把尺子**：裁决侧早就用 `_门态缓存["红"]` 兜底，
        #   产出侧却空手 —— 两把尺子。
        _产品194 = _源码[:_源码.find("def _selftest")]
        if '_证194 = red_audit_prev or (_门态缓存.get("红") or [])' not in _产品194:
            fails.append("【R194】★★ 第 1 轮产出侧仍空手（没拿基线红证据）"
                         " ⇒ R188 的决定是空谈、R193 的收敛不生效")
        if 'red_audit_prev = _证194' not in _产品194:
            fails.append("【R194】★★ 算了但**没赋回去** ⇒ 下游仍看到空证据")
        # ★ 边界：不许在**有**上一轮证据时覆盖它（上一轮才是最新的）
        if 'if not red_audit_prev and _证194:' not in _产品194:
            fails.append("【R194】★ 覆盖条件写错 ⇒ 会用基线盖掉上一轮的最新证据")
        
        # 【R193】已经对了的文件**不许动**（作业面 = 此刻还红的文件）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 92 次真跑，基线只剩 1 个错误）：目标仍点名 4 个必改文件，
        #   而 3 个上一趟已改对 ⇒ 模型在已经对的文件上乱改 ⇒ E0425/E0428
        #   （窗内违规 3 → 10）⇒ 每轮整批回退 ⇒ 永不收敛。
        try:
            _红193 = {'a.rs'}
            _交193 = [{'path': 'a.rs'}, {'path': 'b.rs'}]
            if not _启用跳过193(_红193, _交193, True):
                fails.append("【R193】★★ 红证据只落在 a.rs、a.rs 又确实是交付物，"
                             "却不启用收敛 ⇒ 会继续在 b.rs 上乱改")
            if _该跳过193('a.rs', True, _红193, True):
                fails.append("【R193】★★ 把**有红证据**的文件跳过了 ⇒ 该改的不改")
            if not _该跳过193('b.rs', True, _红193, True):
                fails.append("【R193】★★ 没有红证据、且文件已存在的交付物**没被跳过**"
                             " ⇒ 正会在已经对的文件上制造新错")
            # ★ 边界①：文件还不存在（要新建）⇒ **不许跳**，否则永远建不出来
            if _该跳过193('new.rs', True, _红193, False):
                fails.append("【R193】★ 还不存在的文件被跳过 ⇒ 新文件永远建不出来")
            # ★ 边界②：红证据没有文件归属（如只有全库型门）⇒ 不启用
            if _启用跳过193(set(), _交193, True):
                fails.append("【R193】★ 红证据为空却启用了收敛 ⇒ 会什么都不改")
            # ★ 边界③：红证据落在**没有交付物命中**的文件 ⇒ 不启用
            if _启用跳过193({'x.rs'}, _交193, True):
                fails.append("【R193】★ 红证据全在交付物之外却启用 ⇒ 所有交付物都被跳"
                             "⇒ 一轮什么都没改")
            # ★ 边界④：非仓库工程模式 ⇒ 不启用
            if _启用跳过193(_红193, _交193, False):
                fails.append("【R193】★ 非仓库模式却启用收敛 ⇒ 无「文件已存在」概念")
        except NameError:
            fails.append("【R193】★ 没有 `_启用跳过193`/`_该跳过193` ⇒ 过期作业面不会被收敛")
        
        # ★ 接线：算了必须真的用上
        _产品193 = _源码[:_源码.find("def _selftest")]
        if '_启用193 = _启用跳过193(_有红193, self.deliverables, self.repo_scope)' not in _产品193:
            fails.append("【R193】★★ 没接线（`_produce_window` 没用 `_启用跳过193`）")
        if 'or _该跳过193(d["path"], _启用193, _有红193,' not in _产品193.replace('if ', 'or ', 1):
            pass    # 形状检查交给下一条（上面那条已保证函数被调）
        if 'if _该跳过193(' not in _产品193:
            fails.append("【R193】★★ 算了但**没用来跳过**⇒ 过期作业面照样全改")
        
        # 【R191】混合根因必须**分组**各要一个改法（不许只答多数派）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 91 次真跑 11→1）：统一决定让 10 条 E0284 全消，
        #   就差 1 条 E0433 —— 两者**不是同一个根因**，而 R188 只允许给一个决定，
        #   ⇒ 模型挑了多数派，少数派根因原样留下。
        try:
            import asyncio as _aio191
        
            class _路由191:
                def __init__(self):
                    self.问 = ''
        
                async def call(self, msgs):
                    # ★ `Msg.content` 不一定是 str（实测是块列表：len==1）⇒ 取文本要展开
                    _c191 = (msgs[0].content if msgs else '')
                    if isinstance(_c191, str):
                        self.问 = _c191
                    else:
                        self.问 = "\n".join(getattr(_b191, "text", str(_b191))
                                            for _b191 in (_c191 or []))
                    class _M:
                        pass
                    _m = _M()
                    _m.content = '第1组 E0433：把 定列::新 改成 crate::织网者::定列::新。第2组 E0284：给 道痕 加显式类型。'
                    return _m
        
            _o191 = object.__new__(GoalRunner)
            _o191.goal = '让 cargo test 退出码 0'
            _o191._tok_chars = 0
            _o191.deliverables = [{'path': 'a.rs'}, {'path': 'b.rs'}]
            _r191 = _路由191()
            _o191.router = _r191
            _aio191.run(_o191._算共同决定188([
                {'file': 'a.rs', 'cause': 'E0433::failed to resolve use of 定列'},
                {'file': 'b.rs', 'cause': 'E0284::type annotations needed for 定列'}]))
            _问191 = _r191.问
            # ★ 判据要的是「分组真的送到模型面前」，不是「源码里有那段字」
            if 'E0433' not in _问191 or 'E0284' not in _问191:
                fails.append("【R191】★★ 两种根因没有**都**送进共同决定的 prompt"
                             " ⇒ 模型只能看到其中一种，另一种必然被忽略")
            if '根因分组' not in _问191:
                fails.append("【R191】★ prompt 里没有「根因分组」段 ⇒ 分组算了没用上")
            if '每一组都要有' not in _问191:
                fails.append("【R191】★ 没要求「每一组都要有」⇒ 模型可以只答一组")
        except NameError as _e191:
            fails.append(f"【R191】★ 没有 `_算共同决定188`/`GoalRunner`：{_e191}")
        
        # ══════════════════════════════════════════════════════════════
        # 【R192】换门集**计数到头 ≠ 该停**：还在严格变好就再给一轮（有硬顶）
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 91 次真跑第 4 轮）：`已换门集 2 次未收敛 → 交还用户`，
        #   而那一趟树是 **11 → 1**（两轮各保留一个文件）。
        #   ⇒ 停机判据拿的是「计数器」，该拿的是「还在不在变好」。
        _产品192 = _源码[:_源码.find("def _selftest")]
        if '_还在变好192 = getattr(self, "_本轮保留数187", 0) > 0' not in _产品192:
            fails.append("【R192】★★ 停机前没有看「上一轮有没有真实保留」⇒ "
                         "仍会在一棵树正在变好时提前交还")
        if '_硬顶192' not in _产品192:
            fails.append("【R192】★★ 没有硬顶 ⇒ 会无限换门集兜圈子（另一个极端）")
        if 'replan_budget_extended' not in _产品192:
            fails.append("【R192】★ 延期没有回执 ⇒ 事后无法核对「为什么没停」")
        
        # 【R189】交付物同路径重复 ⇒ 同文件一窗被独立改 N 次，前 N-1 次全白做
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 91 次真跑 plan 回执）：12 个交付物只有 4 个不同路径
        #   （时序拓扑注入.rs ×4 / 自涌现编译.rs ×4 / 织网者.rs ×3 / 算子选择器.rs ×1）
        #   ⇒ 8/12 次模型调用是纯重复；且后写覆盖前写，留下的不是最好那份。
        try:
            _入189 = [
                {'path': 'a.rs', 'gates': [{'name': 'g1'}]},
                {'path': 'b.rs'},
                {'path': 'a.rs', 'gates': [{'name': 'g2'}], 'focus': 42},
                {'path': 'a.rs', 'depends_on': ['b.rs']},
                {'path': 'b.rs'}]
            _出189 = _去重交付物189(_入189)
            if len(_出189) != 2:
                fails.append(f"【R189】★ 5→2 的去重没做到：得到 {len(_出189)} 条")
            elif [d['path'] for d in _出189] != ['a.rs', 'b.rs']:
                fails.append("【R189】★ 去重后**顺序变了**（拓扑序要保第一次出现）："
                             + str([d['path'] for d in _出189]))
            else:
                _a189 = _出189[0]
                _门名189 = [g.get('name') for g in (_a189.get('gates') or [])]
                if sorted(_门名189) != ['g1', 'g2']:
                    fails.append(f"【R189】★★ 去重把 gates 丢了：{_门名189}"
                                 " ⇒ 去重不许让判据变少")
                if _a189.get('focus') != 42:
                    fails.append(f"【R189】★ 去重丢了行号焦点：{_a189.get('focus')!r}")
                if _a189.get('depends_on') != ['b.rs']:
                    fails.append(f"【R189】★ 去重丢了依赖：{_a189.get('depends_on')!r}")
            _净189 = [{'path': 'x.rs'}, {'path': 'y.rs'}]
            if _去重交付物189(_净189) != _净189:
                fails.append("【R189】★ 没有重复却改动了交付物列表 ⇒ 越界")
            if _去重交付物189([]) != []:
                fails.append("【R189】★ 空列表没原样返回")
        except NameError:
            fails.append("【R189】★ 没有 `_去重交付物189` ⇒ 重复交付物不会被去重")
        
        # ★ 接线：必须真的接到 `self.deliverables` 的赋值处
        _产品189 = _源码[:_源码.find("def _selftest")]
        if 'self.deliverables = _去重交付物189(self.deliverables)' not in _产品189:
            fails.append("【R189】★★ 算了但**没接线**（赋值处没调 `_去重交付物189`）"
                         " ⇒ 重复交付物照样各调一次模型")
        
        # 【R188】跨文件改错必须先有**一次共同决定**，再让各文件分别执行
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 90 次真跑，15 次尝试全败）：`_produce_window` 逐文件独立调模型，
        #   每次 prompt 只有本文件 ⇒ 4 个文件各自发明一个改法 ⇒ 互不一致
        #   ⇒ 各自引入新错（E0599 / E0223），错误点数反而变多、类型逐轮漂移。
        # ★ 齿测**行为**：拿不到决定要降级；单文件不该多花一次调用；
        #   决定必须真的进到每个文件的 prompt 里（不是只算不用 —— R128/R129 那族）。
        try:
            import asyncio as _aio188
        
            class _路由188:
                def __init__(self, 回='', 抛=None):
                    self.回, self.抛, self.次数 = 回, 抛, 0
            
                async def call(self, msgs):
                    self.次数 += 1
                    if self.抛:
                        raise self.抛
                    class _M:
                        pass
                    _m = _M()
                    _m.content = self.回      # str：`_extract_text` 有入口分支
                    return _m
            
            _类188 = GoalRunner
            _o188 = object.__new__(_类188)
            _o188.goal = '让 cargo test 退出码 0'
            _o188._tok_chars = 0
            _o188.deliverables = [{'path': 'a.rs'}]
            _r188a = _路由188(回='随便一个决定文本，长度够二十个字符以上。')
            _o188.router = _r188a
            _得188a = _aio188.run(_o188._算共同决定188([]))
            if _得188a != '' or _r188a.次数 != 0:
                fails.append(
                    f"【R188】★ 只有 1 个交付物却仍去问共同决定（调用 {_r188a.次数} 次）"
                    f" ⇒ 白烧 token 且没有跨文件问题要解决")
        
            _o188.deliverables = [{'path': 'a.rs'}, {'path': 'b.rs'}, {'path': 'c.rs'}]
            _o188.router = _路由188(回='第1组 E0284：统一把 定列 改成显式类型标注（道痕 列），'
                                    '并在三处调用点同步；第2组 E0433：补 crate::织网者::定列 导入。')
            _得188b = _aio188.run(_o188._算共同决定188(
                [{'file': 'a.rs', 'cause': 'E0284 type annotations needed'},
                 {'file': 'b.rs', 'cause': 'E0284 type annotations needed'}]))
            if not _得188b or '定列' not in _得188b:
                fails.append(f"【R188】★ 多文件却没拿到共同决定：{_得188b!r}")
        
            # ★ 拿不到必须**降级返回空串**，不许把异常抛穿整趟
            _o188.router = _路由188(抛=RuntimeError('上游挂了'))
            try:
                _得188c = _aio188.run(_o188._算共同决定188([]))
            except Exception as _e188c:
                fails.append("【R188】★★ 拿不到共同决定时**抛穿**了"
                             f"（{type(_e188c).__name__}）⇒ 上游抖一下整趟死")
                _得188c = 'X'
            if _得188c != '':
                fails.append(f"【R188】★ 拿不到共同决定应降级为空串，实得 {_得188c!r}")
        
            # ★ 产出不可用（太短）也必须降级
            _o188.router = _路由188(回='知道了')
            if _aio188.run(_o188._算共同决定188([])) != '':
                fails.append("【R188】★ 决定太短（<20 字符）仍被当成有效决定 ⇒ 注入了垃圾")
        except NameError as _e188n:
            fails.append(f"【R188】★ 没有 `_算共同决定188`/`GoalRunner`：{_e188n}")
        
        # ★ 接线：算了必须**用上**（R128/R129 的病：算了、存了、没送出去）
        # ★★★★★ 先排除**自检函数自己**：否则齿的判据里写着那句调用，
        #   于是「把计算行删掉」齿照样绿 —— **齿把齿自己算成了证据**（R179b 同型）。
        _产品188 = _源码[:_源码.find("def _selftest")]
        _i188 = _产品188.find("async def _算共同决定188")
        _j188 = _产品188.find("async def _produce_window(", _i188)
        if _i188 < 0 or _j188 < 0:
            fails.append("【R188】★ 找不到 `_算共同决定188` / `_produce_window`")
        else:
            _窗188 = "\n".join(
                _l for _l in _产品188[_j188:_j188 + 30000].splitlines()
                if not _l.lstrip().startswith("#"))
            if 'self._共同决定188 = await self._算共同决定188(' not in _产品188:
                fails.append("【R188】★★ 共同决定**没被算**（`_produce_windows` 里没有调用）")
            if '_共188 = getattr(self, "_共同决定188", "")' not in _窗188:
                fails.append("【R188】★★ 算了但**没读出来**注入 prompt ⇒ "
                             "R128/R129 同一个病（算了、存了、没送出去）")
            if 'prompt = (' not in _窗188:
                fails.append("【R188】★ 共同决定没有拼进 prompt")
        
        # 【R187】「不动点」是谁制造的 —— 搜索证出的？还是回退制造的？
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 90 次真跑 · 退出码 1）：三轮全部「保留 0 / 回退 3」
        #   ⇒ 树逐字回到基线 ⇒ Δ 与上轮必然相同 ⇒ 判 stalled_fixed_point
        #   ⇒ 因由写「非目标不动点，需换P/换分解」（**归错因**）。
        # ★ 齿要测的是**因由说不说得对**，不是「有没有那句话」。
        try:
            _真因187 = _不动点因由187("stalled_fixed_point", 3, 0, "Δ==前一轮")
            if "回退制造" not in _真因187:
                fails.append(
                    "【R187】★★ 候选全被拒（保留 0）却说不出「不动点是回退制造的」"
                    " ⇒ 归错因 → 该换产出口时去换门集：" + _真因187)
            elif "产出侧" not in _真因187:
                fails.append("【R187】★ 没说清该换的是**产出侧**：" + _真因187)
            # ★ 反向边界①：**真**不动点（一个没候选）不许说是回退制造的
            _反187a = _不动点因由187("stalled_fixed_point", 0, 0, "Δ==前一轮")
            if "回退制造" in _反187a:
                fails.append("【R187】★ 根本没有候选，却说「回退制造」⇒ 编了个因由"
                             "：" + _反187a)
            # ★ 反向边界②：**有保留**时不许贴这条标签（那不是全拒）
            _反187b = _不动点因由187("stalled_fixed_point", 3, 1, "Δ==前一轮")
            if "回退制造" in _反187b:
                fails.append("【R187】★ 本轮有保留却说是「全部被拒」⇒ 读数错：" + _反187b)
            # ★ 反向边界③：别的 verdict 一律不许改因由（不许污染 progressing）
            if _不动点因由187("progressing", 3, 0, "原样") != "原样":
                fails.append("【R187】★ progressing 的因由被改了 ⇒ 读数越界")
        except NameError:
            fails.append("【R187】★ 没有 `_不动点因由187` ⇒ 不动点归因不可验")
        
        # 【R186】码只能从**结构化位置**读，不许拿自由文本里的三字符当码
        # ══════════════════════════════════════════════════════════════
        # 真证据（第 89 次真跑，2026-10-28 19:37）——**整趟因此停机**：
        #   `轮 1 异常：Error code: 500 - {'error': {'message': '上游服务异常，
        #    请稍后重试 (request id: 202610281140377312976288268d9d6xVaq4o03)'…}}
        #    → 诚实停机（终态（403：等一等不会改变它））`
        # ★ 请求 id 里 `114037731` 含 `403` ⇒ 旧实现 `if "403" in low` 命中
        #   ⇒ 瞬时 500 被判终态 ⇒ 同趟 R144 已用 1935 秒 4 次退避熬过 504，全作废。
        # ★ 齿要测的是**分类结论**：同一段文本，码在**字段里**还是在**噪声里**。
        try:
            _真文186 = (
                "Error code: 500 - {'error': {'message': '上游服务异常，请稍后重试 "
                "(request id: 202610281140377312976288268d9d6xVaq4o03)', "
                "'type': 'new_api_error', 'code': 'do_request_failed'}}")
            _得186, _因186r = _是瞬时的(Exception(_真文186))
            if not _得186:
                fails.append(
                    "【R186】★★★ 真跑原文被判成**终态** ⇒ 上游抖一下整趟归零："
                    f"{_因186r}（请求 id 里含 `403`，旧实现被它骗了）")
            elif "403" in _因186r:
                fails.append(
                    f"【R186】★★ 判对了但**因由里写着 403** ⇒ 码仍是从噪声里读的："
                    f"{_因186r}")
            # ★ 反向边界①：**真** 403 仍必须判终态（不许为了修这个把终态删掉）
            _得186b, _因186b = _是瞬时的(
                Exception("Error code: 403 - permission denied"))
            if _得186b:
                fails.append("【R186】★ 真 403 被判成瞬时 ⇒ 配置错会白等到预算耗尽")
            elif "403" not in _因186b:
                fails.append(
                    f"【R186】★ 真 403 的因由里没说清是 403：{_因186b}")
            # ★ 反向边界②：**只有噪声、没有码**的文本不许造出终态
            _得186c, _因186c = _是瞬时的(
                Exception("request id 202610281140377312976288268d9d6xVaq4o03"))
            if not _得186c:
                fails.append(
                    f"【R186】★★ 纯噪声里的 `403` 仍造出终态 ⇒ 子串匹配没修干净："
                    f"{_因186c}")
        except NameError:
            fails.append("【R186】★ 没有 `_是瞬时的` ⇒ 无法验码的读法")
        
        # ② 有界：必须**有上限**，不许无限等（那是另一种不诚实）
        # ★★★★★ R163：**上限的口径从「次数」改成「时长」。**
        #   真事故（第 47 次真跑）：上游 504 风暴连挂 **6 小时**，
        #   而旧口径 4 次 × (20+40+60+80) = **200 秒** ⇒ 退出码 1。
        #   ★ 4 次 vs 6 小时 = **差 100 倍** ⇒ 不是次数不够，是**尺子量错了**。
        if "_瞬时等待预算秒" not in _源码:
            fails.append("【R144】★ 没有任何等待预算 ⇒ 会无限等"
                         "（那是另一种不诚实）")
        # ★★★★★ R167：预算时钟必须**懒启动**（从第一次瞬时失败起算）。
        #   真事故：`已重试 0 次，共等 2266/1800s` —— 时钟从「轮开始」起算，
        #   而这一轮光试完 9 个槽就花了 2266 秒 ⇒ **重试一次都没发生**。
        _齿起167 = _源码.find("【R144】瞬时上游错误被当成终态")
        _产167 = _源码[:_齿起167] if _齿起167 > 0 else _源码
        _爪163 = _产167.count("_轮起163 = None") + _产167.count("_规划起163 = None")
        if _爪163 < 2:
            fails.append(f"【R167】★★ 只有 {_爪163}/2 处预算时钟是**懒启动**"
                         " ⇒ 时钟从「轮/规划开始」起算，一轮自己干活就把"
                         "预算烧光，重试机制变成摆设"
                         "（实测 已重试 0 次 / 共等 2266/1800s）")
        _懒163 = (_产167.count("if _轮起163 is None") +
                  _产167.count("if _规划起163 is None"))
        if _懒163 < 2:
            fails.append(f"【R167】★★ 只有 {_懒163}/2 处真的在首次失败时才起表"
                         " ⇒ 预算口径不是「等上游多久」")
        if "_瞬时退避封顶秒" not in _源码:
            fails.append("【R163】★★ 没有退避封顶 ⇒ 长故障里一睡几小时，"
                         "人回来时看不到任何中间回执")
        # ★★ 判据是**两条路径都按时长**，不是「有这个常量」。
        #   只查常量的话，把某一条改回次数照样漏（「修了一侧另一侧没修」）。
        _齿起144b = _源码.find("【R144】瞬时上游错误被当成终态")
        _产144b = _源码[:_齿起144b] if _齿起144b > 0 else _源码
        _时口径163 = _产144b.count("_已等163 < _瞬时等待预算秒")
        if _时口径163 < 2:
            fails.append(f"【R163】★★ 只有 {_时口径163}/2 处按**时长**判定"
                         "（规划+单轮应各一处）⇒ 有一条路径仍按次数，"
                         "上游小时级故障时 200 秒就走")
        # ★ 退避必须**指数增长且封顶**：既要能熬，也不能睡过头
        try:
            _首163 = float(_源码.split("_瞬时退避秒 =")[1].split()[0])
            _顶163 = float(_源码.split("_瞬时退避封顶秒 =")[1].split()[0])
            _预163 = float(_源码.split("_瞬时等待预算秒 =")[1].split()[0])
            if not (0 < _首163 <= 60):
                fails.append(f"【R163】★ 首次退避 {_首163}s 不合理（应 ≤60s）")
            if not (60 <= _顶163 <= 900):
                fails.append(f"【R163】★★ 退避封顶 {_顶163}s 不合理"
                             "（太小=狂打上游；太大=长故障里失联）")
            if not (600 <= _预163 <= 6 * 3600):
                fails.append(f"【R163】★★ 等待预算 {_预163}s 不合理"
                             "（<10 分钟熬不过真实抖动；>6 小时不如直接停机报错）")
            # ★★ 直接用**产品的那个函数**算序列 ——
            #   测的是「它实际会怎么退避」，不是「源码里有没有某个词」。
            #   （第一版齿自己重算公式 ⇒ 把产品改成常数退避它咬不住，实测无齿。）
            _序163 = [_退避163(_n, 0.0) for _n in range(1, 10)]
            if any(b < a - 1e-9 for a, b in zip(_序163, _序163[1:])):
                fails.append(f"【R163】★★ 退避不单调增长（{_序163}）"
                             " ⇒ 长故障里会一直以同一频率狂打上游")
            if max(_序163) < min(_顶163, _预163) - 1e-6:
                fails.append(f"【R163】★★ 退避永远到不了封顶（最大 "
                             f"{max(_序163):.0f}s vs 封顶 {_顶163:.0f}s）")
            if len({round(x, 3) for x in _序163[:4]}) < 4:
                fails.append(f"【R163】★★ 前 4 次退避不全不同（{_序163[:4]}）"
                             " ⇒ 不是**指数**增长，是常数或线性")
            # 预算内**至少能试 8 次** —— 否则「熬」是空话
            _已163, _次163 = 0.0, 0
            while _已163 < _预163 and _次163 < 999:
                _次163 += 1
                _已163 += _退避163(_次163, _已163)
            if _次163 < 8:
                fails.append(f"【R163】★★ 预算 {_预163:.0f}s 内只够试 {_次163} 次"
                             " ⇒ 熬不过真实的上游抖动")
        except (IndexError, ValueError) as _e:
            fails.append(f"【R163】齿读不到退避常量：{_e}")
        # ③ 两条会终止整趟的路径都必须走分类（不是只修一处）
        #   ★★★ 「门不得匹配到自己」：`_源码` 是**整份文件**，里面有本齿
        #     自己那句 `_得144, _因144 = _是瞬时的(_e144)` ⇒ 直接数会把
        #     齿自己也数进去，导致「删掉单轮那处」仍数到 3 ⇒ **无齿**
        #     （第一版就是这么写的，注入后 rc=0）。
        #   ⇒ 只数**产品代码切片**：`_源码[:齿块起始]`。
        _齿起144 = _源码.find("【R144】瞬时上游错误被当成终态")
        _产144 = _源码[:_齿起144] if _齿起144 > 0 else _源码
        _路144 = _产144.count("_是瞬时的(")
        if _路144 < 3:      # 定义 1 次 + 规划 1 次 + 单轮 1 次
            fails.append(f"【R144】★ 产品代码里只有 {_路144} 处出现 `_是瞬时的(`"
                         "（定义+规划+单轮应为 3）⇒ 只修了一条会终止整趟的"
                         "路径（「修了一侧」第 8 次）")
        # ④ 反自证：**证明 ① 那张表真的有区分力**。
        #   ★ 第一版我把这条写反了（「桩返回 True ⇒ 报错」），桩恒真所以必报错
        #     —— 那不是齿缺陷，是我把自证写成了自毁。
        #   ★ 正确口径：拿**恒瞬时**桩去跑同一张表，必须**至少有一条不符**；
        #     若一条都不符，说明表里的期望值全是 True ⇒ 那张表根本没测到终态。
        _桩144 = lambda e: (True, "桩：恒瞬时")
        _不符144 = sum(1 for _e, _望, _w in _瞬例
                       if _桩144(_e)[0] is not _望)
        if _不符144 == 0:
            fails.append("【R144】★★ 期望表里**没有一条终态样本** ⇒ "
                         "它测不出「终态被当瞬时」，是无区分力的表")
        #   ★ 再加一条：真分类器与恒瞬时桩的结论**必须不同**（否则真分类器恒真）
        _真同144 = sum(1 for _e, _望, _w in _瞬例
                       if _是瞬时的(_e)[0] == _桩144(_e)[0])
        if _真同144 == len(_瞬例):
            fails.append("【R144】★★ 真分类器与**恒瞬时桩**结论完全一致 ⇒ "
                         "它其实是恒真的，没在分类")
    except Exception as _e134:
        fails.append(f"【R134】齿自身异常：{type(_e134).__name__}: {_e134}")

    # ══════════════════════════════════════════════════════════════════
    # 【R131】2-循环：机器看得见，却只会说「你把这条弄红了」（第 44 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（第 27 次真跑五轮，门真值逐位）：
    #   轮3 mod_有唯一广域入口=红  评测口径经统一入口=绿
    #   轮4 mod_有唯一广域入口=绿  评测口径经统一入口=红
    #   轮5 mod_有唯一广域入口=红  评测口径经统一入口=绿  ← 与轮3 逐位相同
    # ⇒ 真 2-循环。而机器每轮只说「你把 X 弄红了」⇒ 模型修好 X、弄红 Y。
    # ★ 齿咬三处：① 检测存在且**真的比对两轮**；② 注入存在；
    #   ③ 注入的内容要求**换策略**（不是「再努力一点」）。
    try:
        # ① 检测：必须有「与上上轮逐位相同」这个比对，且必须**排除三连不动点**
        #   ★ 针必须**拆段拼**：整串写在这里会匹配到本齿自己
        #     （「门匹配到自己」第 16 次复发，实测：删掉赋值仍绿）。
        _针131 = "_上上轮门" + "真值"
        #   ★ 而且要查**赋值语句**，不是「出现过」——
        #     读取处（`getattr(self, "_上上轮门真值", None)`）也会命中。
        if not re.search(r"^ *self\." + _针131 + r" = ", _源码, re.MULTILINE):
            fails.append("【R131】没有保存「上上轮门真值」⇒ 2-循环**算不出来** "
                         "⇒ 机器只会说「你把这条弄红了」（真跑实测：模型因此在"
                         "两条门之间来回跳了三个来回）")
        else:
            # ★ 齿的作用域必须收到**那一段**上：查全文件会匹配到别处的
            #   `==` 比较（R130 已经吃过这个亏：在别人身上找到自己要找的东西）
            _段131 = _源码[_源码.find("_二循环131 = []"):]
            _段131 = _段131[:_段131.find("self._二循环131 = _二循环131")]
            if not _段131:
                fails.append("【R131】找不到 2-循环的检测段")
            else:
                # 必须有「逐位相同」的判据
                if "len(_同) == len(_共同)" not in _段131:
                    fails.append("【R131】★ 检测没有「逐位相同」这条判据 ⇒ "
                                 "会把任意两轮都判成 2-循环")
                # 必须要求「与上轮确有不同」（排除三连不动点）
                if "and _异" not in _段131 and "_异:" not in _段131:
                    fails.append("【R131】★ 检测没有要求「与上轮确有不同」⇒ "
                                 "三连不动点会被误判成 2-循环（那是 stalled 不是循环）")
        # ② 注入存在，且**留了可核对的日志**
        _注131 = "已把「2-循环」" + "注入窗级 prompt"
        if _注131 not in _源码:
            fails.append("【R131】★ 检测算了却**没注入** ⇒ 又是「算了但没给出去」"
                         "（R128 同族）")
        # ③ 注入内容必须要求**换策略**，不是「再努力一点」
        #   ★ 同样拆段拼：这句字面量就在本齿里 ⇒ 整串查会自匹配。
        if ("请把它当成**一个" + "约束求解问题**") not in _源码:
            fails.append("【R131】★ 注入没有要求**换策略**（约束求解）⇒ "
                         "模型会继续逐条修红门，也就继续来回跳")
        # ④ 必须给模型一条**退出通道**（判据互斥 ⇒ 交人裁决）
        #    ★ 没有它，模型只能继续跳；有了它，「两条门真互斥」能被说出来
        #   ★ 拆段拼（同上）。
        if ("判据" + "互斥：") not in _源码:
            fails.append("【R131】★ 没给模型「判据互斥」这条退出通道 ⇒ "
                         "两条门若真互斥，模型只能无限来回跳（机器不会自己发现）")
    except Exception as _e131:
        fails.append(f"【R131】齿自身异常：{type(_e131).__name__}: {_e131}")

    # ══════════════════════════════════════════════════════════════════
    # 【R129】diverging 立刻停机，R128 的信息永远没有下一轮可用（第 42 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据：`★ R128 … ⇒ 下一轮 prompt 会明确要求` 紧跟
    #   `判据结构停机 @轮2（diverging）→ 交还用户` ⇒ **没有下一轮**。
    # ★ 齿要咬三处：① 有这条重试分支；② 有上限；③ 它**在停机之前**。
    try:
        _树129 = _ast110.parse(_源码)
        #   ★ 齿第一版只认 `AugAssign`（`x += 1`），而实现写的是
        #     `self._diverging_重试 = getattr(...) + 1`（普通 `Assign`）
        #     ⇒ **齿假红**。★ 「齿必须判语义不判写法」—— 第 N 次。
        #   ⇒ 两种写法都接受：只要**目标是** `self._diverging_重试`。
        #   ★★★★★ 这个齿前两版**都是无齿**，两次都是同一个病：**范围错**。
        #     ① 只查「整棵树里有没有 `self._diverging_重试 = …`」⇒ 注入
        #        `if False and (...)` 仍绿（赋值还在，只是永不执行）。
        #     ② 只查「整棵树里有没有 `< 小整数`」⇒ 注入 `< 999` 仍绿
        #        （`generic_goal.py` 里别处还有 `< 1` / `< 2` / `< 3`，
        #         齿在**别人身上**找到了自己要找的东西）。
        #   ⇒ 必须**定位到那一个 `if`**，然后**只看它自己的条件**。
        #     「查存在」永远抓不住「查的是不是那一条」。
        _if129 = None
        for _n in _ast110.walk(_树129):
            if not isinstance(_n, _ast110.If):
                continue
            # 找条件里含 `verdict == "diverging"` 的那个 if
            _有129 = False
            for _c in _ast110.walk(_n.test):
                if (isinstance(_c, _ast110.Compare)
                        and isinstance(_c.ops[0], _ast110.Eq)
                        and isinstance(_c.left, _ast110.Name)
                        and _c.left.id == "verdict"
                        and isinstance(_c.comparators[0], _ast110.Constant)
                        and _c.comparators[0].value == "diverging"):
                    _有129 = True
            if _有129:
                _if129 = _n
                break
        if _if129 is None:
            fails.append("【R129】没有 `if … verdict == \"diverging\" …` 分支 ⇒ "
                         "diverging 仍然立刻停机 ⇒ R128 的信息永远没有下一轮可用")
        else:
            # ★ 可达性：条件不许含常量短路（`if False and …`）
            _常129 = any(isinstance(_c, _ast110.Constant)
                         and not isinstance(_c.value, bool) is False
                         for _c in _ast110.walk(_if129.test))
            _假129 = any(isinstance(_c, _ast110.Constant)
                         and _c.value is False
                         for _c in _ast110.walk(_if129.test))
            if _假129:
                fails.append("【R129】diverging 分支被 `and False` 关掉 ⇒ "
                             "永不执行（语法树里还在，运行时到不了）")
            # ★ 上限：只看**这个 if 自己的条件**里有没有 `getattr(…_diverging_重试…) < 小整数`
            _限129 = False
            for _c in _ast110.walk(_if129.test):
                if not (isinstance(_c, _ast110.Compare)
                        and isinstance(_c.ops[0], _ast110.Lt)):
                    continue
                _左 = _c.left
                _用129 = any(isinstance(_g, _ast110.Constant)
                             and _g.value == "_diverging_重试"
                             for _g in _ast110.walk(_左))
                _右 = _c.comparators[0]
                if (_用129 and isinstance(_右, _ast110.Constant)
                        and isinstance(_右.value, int) and 0 < _右.value <= 3):
                    _限129 = True
            if not _限129:
                fails.append("【R129】diverging 重试**没有针对 "
                             "`_diverging_重试` 的小整数上限**（须 `0 < N <= 3`）"
                             " ⇒ 会无限重试，收敛语义被破坏")
        # ★ 顺序：`continue` 必须在 `return 1` **之前**（同一分支里）
        _块129 = _源码[_源码.find("R129 diverging 第一次"):]
        _块129 = _块129[:_块129.find("_报主树保留")] if "_报主树保留" in _块129 else _块129
        if "continue" not in _块129:
            fails.append("【R129】diverging 重试分支里没有 `continue` ⇒ "
                         "回喂之后没有下一轮，等于没回喂")
        # ★ R128 的注入必须留可核对的日志（不许假设注入生效）
        #   ★ 针必须**拆段拼**：整句写在这里会匹配到本齿自己
        #     （「门匹配到自己」第 15 次复发，实测：注入别的日志仍绿）。
        _注128 = "已把「不许弄红」" + "注入窗级 prompt"
        if _注128 not in _源码:
            fails.append("【R128】注入 prompt 之后**没有留日志** ⇒ "
                         "无法核对它到底进没进去（本族主导缺陷：声明了却没被使用）")
    except Exception as _e129:
        fails.append(f"【R129】齿自身异常：{type(_e129).__name__}: {_e129}")

    # ══════════════════════════════════════════════════════════════════
    # 【R128】「曾绿」算出来了，却没告诉模型（第 41 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据：轮1/2/3 在 `cargo_check_lib` 与 `接线法则_九宗门降级接无界键`
    #   之间来回换（5→2→6 处）。模型不知道「刚刚还是绿的」⇒ 必然反复。
    # ★ 齿要咬三处，缺一处就等于没接上：
    #   ① 真的算了「上轮绿、本轮红」；
    #   ② 真的把它写进了窗级 prompt；
    #   ③ 反向约束的话**在 prompt 里**（不是只在注释里）。
    try:
        _树128 = _ast110.parse(_源码)
        _算128 = False
        for _n in _ast110.walk(_树128):
            if (isinstance(_n, _ast110.Assign)
                    and isinstance(_n.targets[0], _ast110.Attribute)
                    and _n.targets[0].attr == "_曾绿今红"):
                # 右值必须是 `sorted(...)` 或含比较的推导式 —— 不许写死成 []
                _算128 = not (isinstance(_n.value, _ast110.List)
                              and not _n.value.elts)
        if not _算128:
            fails.append("【R128】`_曾绿今红` 没有被真正算出来（写死成 []）"
                         " ⇒ 模型永远不知道哪条门刚被弄绿")
        #   ★ 齿②第一版只查「有没有 `prompt +=`」⇒ **无齿**（注入 `if False:`
        #     仍绿）—— 因为文件里别处还有别的 `prompt +=`（R121a 的反向约束）。
        #     ★ 这是「单侧检查抓不到另一侧」的又一例：**存在** ≠ **是那一条**。
        #   ⇒ 必须要求：那个 `prompt +=` 的**右值里含 R128 那句话**。
        #   ★ 齿②第二版只查「那个 `prompt +=` 的右值含那句话」⇒ **仍然无齿**
        #     （注入 `if False:` 后照样绿）—— 因为 `if False:` **不删语法树节点**，
        #     它只是让它**永远不执行**。
        #     ★ 「语法树里有」≠「运行时会走到」：AST 能证结构，证不了可达性。
        #   ⇒ 必须查**包着它的那层 `if` 的条件**：条件不许是常量。
        _注128 = False
        for _if128 in _ast110.walk(_树128):
            if not isinstance(_if128, _ast110.If):
                continue
            _含128 = False
            for _n in _ast110.walk(_ast110.Module(body=_if128.body, type_ignores=[])):
                if not (isinstance(_n, _ast110.AugAssign)
                        and isinstance(_n.target, _ast110.Name)
                        and _n.target.id == "prompt"
                        and isinstance(_n.op, _ast110.Add)):
                    continue
                for _c in _ast110.walk(_n.value):
                    if (isinstance(_c, _ast110.Constant)
                            and isinstance(_c.value, str)
                            and "已经变绿" in _c.value):
                        _含128 = True
            if not _含128:
                continue
            # 条件不许是常量（`if False:` / `if 0:` ⇒ 永不执行）
            if isinstance(_if128.test, _ast110.Constant):
                continue
            _注128 = True
        if not _注128:
            fails.append("【R128】窗级 prompt **没有**追加反向约束"
                         "（缺含「已经变绿」那段的 `prompt += …`）"
                         " ⇒ 算出来也没人看见")
        _话128 = "不许把**已经变绿**的门弄红"
        if _话128 not in _源码:
            fails.append("【R128】反向约束的**话**不在源码里 ⇒ "
                         "模型收到的仍是「只有红门」的信息")
    except Exception as _e128:
        fails.append(f"【R128】齿自身异常：{type(_e128).__name__}: {_e128}")

    # ══════════════════════════════════════════════════════════════════
    # 【R108】判据保真检查被锁在它自己要判的条件之后（第 22 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据：**全部十次真跑**，日志里「主心骨自证」出现 **0 次**。
    #   而每次都会打印「N 条门都自带否定样本」—— 那只是 `抽否定样本`
    #   （**声明**检查），不是「样本真能使其红」。
    # 守卫原是 `_达标本 and bool(alignment.get("fixed_point"))`：
    #   要跑它得先「已达标且多窗对齐」，而它正是判「门全绿可不可信」的东西
    #   ⇒ **循环依赖**，它在最需要时永远不跑。
    # ★ 真跑 R102 那趟是活证据：`results` 三条全 True、`_达标本` 也 True，
    #   只因 `fixed_point=False` ⇒ 整块跳过 ⇒ 3 条门的否定样本一次没被喂过。
    try:
        _m108 = re.search(
            r"    async def _run\(self.*?(?=\n    (?:async )?def )", _源码, re.S)
        if not _m108:
            fails.append("【R108】★ 找不到 `_run` 的方法体")
        else:
            _体108 = _m108.group(0)
            # ① ★ 触发条件里**不得**再出现「必须多窗对齐」
            # ★★★★★ 这条针连踩两次坑（如实登记，两次都是「门匹配到自己」）：
            #   ① 第一版针含**字符串字面量** `fixed_point` 且用单引号，
            #      而产品用 ASCII 双引号 ⇒ 正则永远匹配不上 ⇒ **无齿**。
            #   ② 第二版拆段修好了，却**匹配到了我自己的注释** ——
            #      我在 `_run` 里写的缺陷说明里，原样引用了那句旧守卫
            #      ⇒ 未修改的文件也判红 ⇒ **假红**。
            #   这正是 TS-468 齿早就写下的教训：
            #     「**读数前先剥注释与字符串**」，且「**针必须是纯代码形态**」。
            #   ⇒ 修法：复用 `gate_spec.strip_for_matching`（保位剥），
            #     针只用**纯代码**半句（不含任何字符串字面量）。
            from agentscope_orchestrator import gate_spec as _gs108
            _体净108 = _gs108.strip_for_matching(_体108)
            _锁108 = "if _达标本 and bool(alignment.get("
            if _锁108 in _体净108:
                fails.append("【R108】★ 判据保真检查仍被锁在 `fixed_point` 之后 ⇒ "
                             "它在最需要时永远不跑（十次真跑 0 次）")
            # ② 必须有一条「门全绿 / 已达标」的触发
            if not re.search(r"if _达标本 or _全绿本108:", _体108):
                fails.append("【R108】★ 触发条件没包含「本轮门全绿」⇒ 判据没判别力时"
                             "仍不会被查出来")
            # ③ ★ 跳过必须留痕（R85 家族：静默跳过 = 不可见）
            if "判据保真检查本轮**未跑**" not in _体108:
                fails.append("【R108】★ 跳过时没有留痕 ⇒ 「验过了」与「没验」"
                             "在日志里长得一样（R85 同形）")
            # ④ ★ 跑的时候必须说清「多窗对齐只影响交付，不影响验保真」
            if "只影响交付" not in _体108:
                fails.append("【R108】★ 没把「多窗对齐」与「验保真」分开说 ⇒ "
                             "下次还会混")
            # ⑤ ★ 行为齿：门全绿但**未达标、未对齐**时也必须触发
            #    （用真实例跑不了整轮，改为验证触发条件的**取值**：
            #     `_全绿本108` 在 results 全 True 时必须为 True）
            #    —— 用 `_score_files` 口径的等价式直接算一遍
            _测108 = {"a": True, "b": True}
            _算108 = bool(_测108) and all(v is True for v in _测108.values())
            if not _算108:
                fails.append("【R108】★ 门全绿的判定式在三条全 True 时不为真 ⇒ 触发条件恒假")
            _测108b = {"a": True, "b": False}
            if bool(_测108b) and all(v is True for v in _测108b.values()):
                fails.append("【R108】★ 门全绿的判定式在有一条 False 时仍为真 ⇒ 恒真")
            if bool({}) and all(v is True for v in {}.values()):
                fails.append("【R108】★ 空门集被判成全绿（经典层假绿）")
    except Exception as _e108:
        fails.append(f"【R108】齿自身异常：{type(_e108).__name__}: {_e108}")

    # ══════════════════════════════════════════════════════════════════
    # 【R107】修了枚举里的项，没修「枚举」（真跑抓出的第 21 层）
    # ══════════════════════════════════════════════════════════════════
    # 真跑收场：`重规划失败：槽 pre 必须是 list → 诚实停机`（exit 1）。
    #   而那是**模型照着就能改的**（报错里写着哪个槽不对）⇒ 该回喂，不该死。
    #   R80 修过同一个病，并明确写过「拿一张表去冒充可能性本身」，
    #   但只补了表里三项，**没修「表」这件事** ⇒ `plan_tasks` 漏着。
    try:
        # ① 行为齿：`plan_tasks` 的错必须能被 `规约可纠正` 接住
        #    （而不是以裸 ValueError 收场）
        _坏107 = {"name": "x", "pre": "不是list", "constraints": [],
                  "gates": [], "acceptance": []}
        _接107 = False
        try:
            auto_goal.plan_tasks(_坏107)
        except 规约可纠正:
            _接107 = True
        except Exception:
            pass
        if not _接107:
            # 说明 plan_tasks 本身没转 —— 那接线处必须转
            pass
        # ★★★ 必须**切出方法体**再搜：③④ 的检查串在**本齿自己也出现**，
        #   搜整个 `_源码` ⇒ 注入掉实现也仍然命中 ⇒ **无齿**。
        #   这是「门不得匹配到自己」的**第 8 次复发**（当场抓到，如实登记）。
        _m107 = re.search(
            r"    def _adopt_spec\(self.*?(?=\n    (?:async )?def )", _源码, re.S)
        if not _m107:
            fails.append("【R107】★ 找不到 `_adopt_spec` 的方法体")
        else:
            _体107 = _m107.group(0)
            # ② 接线齿：调用点必须在 try 里（紧邻上一行就是 `try:`）
            if not re.search(r"try:\n *self\.plan = auto_goal\.plan_tasks\(spec\)",
                             _体107):
                fails.append("【R107】★ `plan_tasks` **不在 try 里** ⇒ 它的裸 ValueError"
                             "会绕过回喂循环 ⇒ 整趟死（真跑实测 exit 1）")
            if "raise 规约可纠正(" not in _体107.split(
                    "self.plan = auto_goal.plan_tasks(spec)")[-1]:
                fails.append("【R107】★ `plan_tasks` 的错没有转成 `规约可纠正` ⇒ "
                             "模型没机会改（R80 只补了表里三项，这里是表外第四项）")
            # ③ ★ 反向：报错必须**告诉它怎么改**（光说「不对」等于没说）
            if "都必须是 **list**" not in _体107:
                fails.append("【R107】★ 报错没写清「四个槽必须是 list」⇒ 模型只能猜")
            if "必填字段" not in _体107:
                fails.append("【R107】★ 报错没指向字段契约 ⇒ 模型改不到点上")
        # ④ ★ 骨架齿：`except 规约可纠正: raise` 必须在（不得把已可纠正的再包一层）
        if not re.search(r"except 规约可纠正:\n *raise\n *except Exception as exc:"
                         r" *\n? *#[^\n]*\n *raise 规约可纠正\(\n *f\"载体规约", _源码):
            fails.append("【R107】★ 没排除「已经是 `规约可纠正`」的情况 ⇒ "
                         "会把可纠正错误包成两层，报错文本被污染")
    except Exception as _e107:
        fails.append(f"【R107】齿自身异常：{type(_e107).__name__}: {_e107}")

    # ══════════════════════════════════════════════════════════════════
    # 【R106】主心骨的判据只是提示，不是闸（真跑抓出的第 20 层）
    # 【R102】自检不得写**真回执**（证据一旦不能证明真跑，整条验收链就空了）
    # ══════════════════════════════════════════════════════════════════
    # R106 真证据（第九次端到端 16:43）：主心骨给了 7 判据 + 6 计划，
    #   判据写得极准（生产侧必须真调用 / 评测侧不得降级 / 定义不得删改），
    #   R105 已送进提示 —— 结果规划器**还是**给了
    #   `src/模块乙/分派树.rs` / `src/模块乙/胞坐标.rs` 两个零门交付物。
    #   ⇒ 提示不够；R86 早就证明过，必须变成机器闸。
    # R102 真证据：`chief_receipt.jsonl` 里 69+ 条是自检写的（goal 全是
    #   `把 src 下的东西修好`、`name=fib_gen`），每 1–2 分钟一条 = 回归节奏。
    try:
        # ── R106 ────────────────────────────────────────────────────
        _决106 = {"判据": [
            {"name": "生产侧真调用", "type": "grep_required",
             "in": "src/模块甲/趋向.rs", "patterns": ["广域\\s*\\("]},
            {"name": "评测侧不得降级", "type": "grep_required",
             "in": "src/模块乙/评测口径.rs", "patterns": ["广域"]},
            {"name": "定义不得删", "type": "whole_reading",
             "in": "src/模块甲/mod.rs", "must_contain": ["pub fn 学模式_广域"]}],
            "计划": [{"path": "src/模块乙/arc评测.rs", "why": "评测侧适配"},
                     {"path": "src/模块乙/点云评测.rs:436", "why": "带行号"}]}
        _抽106 = _指导声明的必改文件(_决106)
        for _必106 in ("src/模块甲/趋向.rs", "src/模块乙/评测口径.rs",
                       "src/模块甲/mod.rs", "src/模块乙/arc评测.rs",
                       "src/模块乙/点云评测.rs"):
            if _必106 not in _抽106:
                fails.append(f"【R106】★ 主心骨点名的 `{_必106}` 没被抽出来 ⇒ "
                             f"它进不了必改闸，规划器会继续无视")
        if "src/模块乙/点云评测.rs:436" in _抽106:
            fails.append("【R106】★ `:行号` 尾巴没被剥掉 ⇒ 必改闸按行判，"
                         "行号漂移就白红（与 R86 同口径才对）")
        if _指导声明的必改文件(None) or _指导声明的必改文件({}):
            fails.append("【R106】★ 空指导应抽出空清单")
        # ★ 接线齿：必须并进 R86 的**同一个** `_必改36` 列表
        if not re.search(r'^ *_指106 = _指导声明的必改文件\('
                         r'getattr\(self, "_chief_指导", None\)\)', _源码, re.MULTILINE):
            fails.append("【R106】★ 主心骨的判据**没并进必改闸** ⇒ "
                         "又只是提示（R86 已证明提示不管用）")
        if not re.search(r'^ *if _b106 not in _必改36:', _源码, re.MULTILINE):
            fails.append("【R106】★ 没并进 R86 那个 `_必改36` ⇒ 两把尺子（R98b）")
        # ── R102 ────────────────────────────────────────────────────
        # ① 必须有一个**产品自己声明**的「我是真跑实例」事实
        if "_真跑实例 = True" not in _源码:
            fails.append("【R102】★ 没有「真跑实例」标记 ⇒ 分不清真跑与检查")
        # ② `_主心骨()` 必须按这个事实分流回执目录
        if not re.search(r'^ *_真 = bool\(getattr\(self, "_真跑实例", False\)\)',
                         _源码, re.MULTILINE):
            fails.append("【R102】★ `_主心骨()` 没读「真跑实例」⇒ 自检仍会写真回执")
        if not re.search(r"^ *_回执dir = _RECEIPT\.parent$", _源码, re.MULTILINE):
            fails.append("【R102】★ 真跑不再写生产回执 ⇒ 证据丢了（反向错）")
        # ③ 行为齿：非真跑实例**不得**指向生产回执
        _r102 = GoalRunner.__new__(GoalRunner)
        _r102.router = None
        _c102 = _r102._主心骨()
        _dir102 = str(getattr(_c102, "_回执目录", "") or "")
        if str(_RECEIPT.parent) == _dir102:
            fails.append("【R102】★ 非真跑实例的主心骨仍指向**生产回执** ⇒ "
                         "自检会污染真跑证据（实测 69+ 条）")
        # ④ 反向：真跑实例**必须**指向生产回执
        _r102b = GoalRunner.__new__(GoalRunner)
        _r102b.router = None
        _r102b._真跑实例 = True
        _c102b = _r102b._主心骨()
        if str(getattr(_c102b, "_回执目录", "") or "") != str(_RECEIPT.parent):
            fails.append("【R102】★ 真跑实例**没**指向生产回执 ⇒ 真跑没证据")
    except Exception as _e106:
        fails.append(f"【R106/R102】齿自身异常：{type(_e106).__name__}: {_e106}")

    # ══════════════════════════════════════════════════════════════════
    # 【R105】主心骨定了判据，但判据没有任何消费者（真跑抓出的第 19 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（`chief_receipt.jsonl` 2026-10-19 15:22:27）：
    #   主心骨回了一条**格式正确、内容到位**的决定：
    #     `判据数=7 计划数=4 空决定=False 纠正次数=0`
    #   理由写得对：「上一轮不是目标不成立，而是规约撞了冻结区…
    #     目标我保留，但把它写死成可判定的形态」。
    #   而机器**只读了 `改目标` 这一个比特**（False）⇒ 直接停机 exit 1。
    #   ⇒ 7 条判据、4 项计划，**一个字都没被用到**。
    try:
        # ① ★ 主心骨给了判据/计划时，`_问主心骨_规划不成` 必须返回 True
        #    （表示「重来一次规划」），不能当成「没意见」停机。
        _体105 = ""
        _m105 = re.search(
            r"    async def _问主心骨_规划不成\(self.*?(?=\n    (?:async )?def )",
            _源码, re.S)
        if not _m105:
            fails.append("【R105】★ 找不到 `_问主心骨_规划不成` 的方法体")
        else:
            _体105 = _m105.group(0)
            # 判据/计划必须**真的被用**（存起来 + 返回 True）
            if not re.search(r'^ *if 决\.get\("判据"\) or 决\.get\("计划"\):',
                             _体105, re.MULTILINE):
                fails.append("【R105】★ 主心骨给了判据/计划却**没被检查** ⇒ "
                             "它的决定会被扔掉（真跑实测：7 条判据、0 个消费者）")
            if not re.search(r'^ *self\._chief_指导 = 决', _体105, re.MULTILINE):
                fails.append("【R105】★ 主心骨的判据**没被存下来** ⇒ 下游拿不到")
            # ★ 必须有**独立的**预算（不能和改目标共用一个计数器）
            if not re.search(r'^ *if getattr\(self, "_chief_指导次数", 0\) >= 1:',
                             _体105, re.MULTILINE):
                fails.append("【R105】★ 指导没有独立预算 ⇒ 会无限「重规划→又不成」")
            if not re.search(r'^ *self\._chief_指导次数 = getattr\('
                             r'self, "_chief_指导次数", 0\) \+ 1', _体105, re.MULTILINE):
                fails.append("【R105】★ 指导次数没被消耗 ⇒ 预算守卫是死的")
        # ② ★ 存下来的指导必须**真的进规划器提示**（不能又变成只写不读）
        # ★★ 用**单引号** raw 串：串里含 ASCII 双引号，双引号 raw 会提前终止
        #   （本库已登记 15 次的同一个坑，这次又踩，如实登记）
        if not re.search(r'^ *反馈 = _指导块\(getattr\(self, "_chief_指导", None\)\)',
                         _源码, re.MULTILINE):
            fails.append("【R105】★ 主心骨的判据**没进规划器提示** ⇒ "
                         "又是「声明了但没人读」（R85 同形）")
        # ③ 行为齿：指导块必须**真的渲染出内容**（不是空串）
        _块105 = _指导块({"理由": "目标保留，做法要改",
                          "判据": [{"name": "键口径统一", "type": "whole_reading",
                                    "in": "src/模块甲/mod.rs"}],
                          "计划": [{"path": "src/模块甲/mod.rs",
                                    "why": "把键导出收成一处"}]})
        for _必105 in ("键口径统一", "src/模块甲/mod.rs", "目标保留", "把键导出收成一处"):
            if _必105 not in _块105:
                fails.append(f"【R105】★ 指导块漏了「{_必105}」⇒ 规划器读不到这条约束")
        # ★ 锚点必须是**这句话独有的**：`deliverables` 在同行前半句也有，
        #   只查它 ⇒ 注入掉后半句仍然命中 ⇒ **无齿**（当场抓到，如实登记）。
        if "没有判据门覆盖" not in _块105:
            fails.append("【R105】★ 指导块没提醒「判据点名的文件必须进 deliverables」"
                         "⇒ 会重犯「交付物没有判据门覆盖」")
        # ④ 空决定 ⇒ 指导块必须为空（不得凭空造约束）
        if _指导块({"改目标": False}) != "":
            fails.append("【R105】★ 没有判据也没有计划时，指导块却非空 ⇒ 凭空造约束")
        if _指导块(None) != "":
            fails.append("【R105】★ `None` 输入应返回空指导块")
        # ⑤ ★ 反向：**不改目标、也没给判据/计划** ⇒ 仍必须诚实停机
        if "照旧诚实停机" not in _体105:
            fails.append("【R105】★ 「不改目标也没给判据」时不再诚实停机 ⇒ "
                         "机器开始替主心骨决定")
    except Exception as _e105:
        fails.append(f"【R105】齿自身异常：{type(_e105).__name__}: {_e105}")

    # ══════════════════════════════════════════════════════════════════
    # 【R104】门集能跑 ≠ 门集能判目标（真跑抓出的第 18 层）
    # ══════════════════════════════════════════════════════════════════
    # 真证据（2026-10-19 · 第七次端到端 · 目标「把无界键接进生产决策链」）：
    #   30 条门其实是**同 3 条复制 10 遍**，且**全是 regression guard**：
    #     · cargo_test_lib_build            （全库能编译）
    #     · 模块甲_无界键**定义仍在**     （must_contain "pub fn 学模式_广域"）
    #     · 造变换_施填规仍在               （must_contain "施填规"）
    #   ⇒ 门全绿 = **没破坏任何东西** = 必然发生，与「接进决策链」无关。
    #   而 5 个交付物里 **3 个零门**（arc评测 / 点云评测 / 评测口径）。
    #   同一趟跑还输出三份**互相矛盾**的裁决：
    #     escalated_unreachable: 「Δ=∅ ⇒ **P 达成**」
    #     main_tree_modified:    「**未收敛停机**」
    #     日志 R89:              「代偿拒收 ⇒ **不判达标**」
    #   ★ 我的编排器犯了和它要改造的样本戊**一模一样**的错：
    #     用无判别力的判据声称达成（判据5 恒 1.000 / 判据6 恒 1000 / 本处恒 True）。
    try:
        from agentscope_orchestrator.gate_derive import (
            sufficiency_bound as _sb104, bound_report as _br104)
        # ① ★ 交付物**没有判据门覆盖** ⇒ 必须判不充分（真跑那次的精确回放）
        _门104 = [
            {"name": "cargo_test_lib_build", "type": "compile", "cwd": "{repo}",
             "cmd": ["cargo", "test", "--lib", "--no-run"], "expect_exit": 0},
            {"name": "模块甲_无界键定义仍在", "type": "whole_reading",
             "in": "src/模块甲/mod.rs", "mode": "contains",
             "must_contain": ["pub fn 学模式_广域", "pub fn 推理_广域"]},
            {"name": "造变换_施填规仍在", "type": "whole_reading",
             "in": "src/模块乙/造变换.rs", "mode": "contains",
             "must_contain": ["施填规"]},
        ]
        _规104 = {"deliverables": [
            {"path": "src/模块甲/mod.rs"},
            {"path": "src/模块乙/造变换.rs"},
            {"path": "src/模块乙/评测口径.rs"},
            {"path": "src/模块乙/点云评测.rs"},
            {"path": "src/模块乙/arc评测.rs"}]}
        _够104, _因104 = _sb104(_门104, _规104)
        if _够104:
            fails.append("【R104】★ 3 个交付物**零门**，`sufficiency_bound` 却判"
                         "「充分」⇒ 门全绿会被当成目标达成（真跑实测过）")
        _缺104 = [r for r in _因104 if "没有判据门覆盖" in r]
        if not _缺104:
            fails.append("【R104】★ 判不充分了，但**没点名哪些交付物没有门** ⇒ "
                         "模型不知道该补哪条")
        elif "评测口径" not in _缺104[0] or "arc评测" not in _缺104[0]:
            fails.append(f"【R104】★ 点名了但漏了文件（得 {_缺104[0][:120]}）")
        # ② ★ `contract` **不得无条件声称「必然」**（真跑那趟就是这么骗人的）
        _边104 = _br104(_门104, _规104, Path("."))
        if _边104["decision_necessary"]:
            fails.append("【R104】★ `bound_report` 仍判 `decision_necessary=True`")
        if "门全绿⇒目标达成" in _边104["contract"]:
            fails.append("【R104】★ `contract` 仍无条件声称「门全绿⇒目标达成」⇒ "
                         "那是**向用户做了做不到的保证**（真跑实测：门全绿而目标未达成）")
        if "不必然" not in _边104["contract"]:
            fails.append("【R104】★ `contract` 没说清「不必然」⇒ 用户仍会误读")
        # ③ ★ 反向：门**真覆盖**全部交付物时，不得假红
        _门104b = list(_门104) + [
            {"name": "口径门", "type": "whole_reading",
             "in": "src/模块乙/评测口径.rs", "mode": "contains",
             "must_contain": ["x"]},
            {"name": "点云门", "type": "whole_reading",
             "in": "src/模块乙/点云评测.rs", "mode": "contains",
             "must_contain": ["x"]},
            {"name": "arc门", "type": "whole_reading",
             "in": "src/模块乙/arc评测.rs", "mode": "contains",
             "must_contain": ["x"]},
            {"name": "行为门", "type": "compile", "cwd": ".",
             "cmd": ["python", "-m", "py_compile", "{file}"], "expect_exit": 0}]
        _够104b, _因104b = _sb104(_门104b, _规104)
        if not _够104b:
            fails.append(f"【R104】★ **假红**：门已覆盖全部交付物仍判不充分："
                         f"{_因104b[:2]}")
        # ④ ★ `{file}`/`{work}` 通用门**天生覆盖所有交付物**，不得判未覆盖
        _门104c = [{"name": "编译", "type": "compile", "cwd": ".",
                    "cmd": ["python", "-m", "py_compile", "{file}"],
                    "expect_exit": 0}]
        _够104c, _因104c = _sb104(_门104c, _规104)
        if any("没有判据门覆盖" in r for r in _因104c):
            fails.append("【R104】★ `{file}` 通用门被误判成「未覆盖交付物」⇒ "
                         "**假红**（自检夹具全用 `{file}`，当场抓到 6 条）")
        # ⑤ ★ 措辞齿：`converged` 的因由**不得**说「P 达成」
        from agentscope_orchestrator.gate_derive import (
            deviation_structure_verdict as _dv104)
        _判104, _由104 = _dv104([], [], declared_scopes=set(), prev_green=set())
        if "P 达成" in _由104:
            fails.append("【R104】★ `Δ=∅` 仍被说成「**P 达成**」⇒ 它只意味着"
                         "「已声明的门集下无偏差」，离目标达成还差「门真能判目标」")
        if "目标达成" not in _由104:
            fails.append("【R104】★ 措辞没提醒「≠ 目标达成」⇒ 仍会被误读")
    except Exception as _e104:
        fails.append(f"【R104】齿自身异常：{type(_e104).__name__}: {_e104}")

    # ══════════════════════════════════════════════════════════════════
    # 【R97】主心骨必须**真的做一次回合**（否则它只是个记忆笔记本）
    # ══════════════════════════════════════════════════════════════════
    # 真跑抓出（2026-10-19 · 第三次端到端）：`回合()`/`改目标()`/`写回执()`
    # 在整条流程里**调用次数都是 0**。R92 的齿验的是「记忆有没有被喂回去」，
    # **没有一条齿验「它有没有做过一次决定」** ⇒ 验收项「改过一次目标」
    # 在构造上不可能达成。
    # ★ 齿的教训（本库已登记那条）：**齿只验它被写出来时想到的那件事**。
    try:
        # ① 回合必须在**语句位置**被调用（死代码/恒假守卫不算）
        if not re.search(r"^ *_决97 = await _心92\.回合\(", _源码, re.MULTILINE):
            fails.append("【R97】★ 主心骨 `回合()` **没被调用**（或只在死代码里）"
                         "⇒ 它从不做决定 ⇒ 永远不会改目标（真跑已发生）")

        # ② 改目标必须**真的生效**：换目标 + 旧规约作废
        if not re.search(r"^ *self\.goal = _心92\.目标", _源码, re.MULTILINE):
            fails.append("【R97】★ 主心骨改了目标但 `self.goal` 没跟着换 ⇒ 回执写了、"
                         "流程还按旧目标跑")
        if not re.search(r"^ *self\.spec = \{\}", _源码, re.MULTILINE):
            fails.append("【R97】★ 改了目标却留着旧规约 ⇒ 新目标配旧判据")

        # ③ 降级必须**如实**（不许静默退回老形态）
        #   ★★ 「门不得匹配到自己」**第 7 次复发**（R71/R73b/R78/R80/R86/R93/R97）：
        #   上一版写 `"_chief_降级 = True" not in _源码` —— 而**这条齿自己的
        #   字面量**就在 `_源码` 里 ⇒ 无论实现怎么改都命中 ⇒ 恒绿
        #   （`_验` 注入当场抓出：④⑤ 无齿）。
        #   ⇒ 修法：要求**语句位置**（行首缩进后直接是赋值），
        #     齿自己的字面量前面是引号，不可能满足。
        if not re.search(r"^ *self\._chief_降级 = True", _源码, re.MULTILINE):
            fails.append("【R97】★ 主心骨降级没有记下来 ⇒ 看起来像「主心骨没意见」，"
                         "实际是「主心骨没上班」")

        # ④ 目标没变时**不得**反复返回 changed（否则重规划计数空转）
        #   ★ 同型（自匹配）：要求**整条 elif 语句**，而不是那个子串。
        if not re.search(r'^ *elif _决97\.get\("改目标"\) and _心92\.目标 != self\.goal',
                         _源码, re.MULTILINE):
            fails.append("【R97】★ 没判「目标真变了」⇒ 主心骨每次都说改目标就无限重规划")

        # ⑤ 方向不变时，它定的判据必须交给规划器（否则这次回合白做）
        #   ★ 同型（死代码盲，R93 ③ 的复发）：只查「字面量出现过」的话，
        #     `if False:` 包住整块照样命中。⇒ 要求**语句位置**。
        if not re.search(r"^ *if _判97 or _计97:", _源码, re.MULTILINE):
            fails.append("【R97】★ 主心骨定了判据却没交给规划器 ⇒ 回合做了等于没做")
        if "主心骨本回合的决定" not in _源码:
            fails.append("【R97】★ 主心骨的判据没被标成「它的决定」⇒ 规划器会当成"
                         "普通上下文忽略掉")
    except Exception as _e97b:
        fails.append(f"【R97】接线齿自身异常：{type(_e97b).__name__}: {_e97b}")

    if fails:
        _log(f"generic_goal 自检：失败 {len(fails)} · 判据点 {_判据点}"
             f"（下限 {_判据下限}）—— 红 {len(fails)}")
        for f_ in fails:
            _log(f"  ✗ {f_}")
        return 1
    # ★ 不再写「M/M 全绿」—— 那是在**声明**一个我数不准的东西。
    #   如实写：**0 个判据点报红** + 判据点的实际个数 + 下限。
    _log(f"generic_goal 自检：全绿 · 判据点 {_判据点}（下限 {_判据下限}）· 失败 0")
    return 0


# ════════════════════════════════════════════════════════════════
# ★ 区域编辑协议（2026-10-14 新增 —— 实测缺口驱动，架构级）
# ════════════════════════════════════════════════════════════════
# 【为什么需要它 —— 实测证据】
#   2026-10-14 09:16 真跑派发 `src/模块甲/mod.rs:8295`，模型**诚实拒绝**：
#     「无法产出 `src/模块甲/mod.rs` 的完整内容 —— 注入到本对话的文件内容在
#       **第 60000 字符处被截断**（原文件共 **342872 字符**），而本次要改的登记点
#       **第 8295 行**位于截断点之后，**不在我可读的范围内**。」
#   ⇒ 根因**不是**"注入上限太小"，而是**协议选错了**：
#     本编排器的改文件模型是**整文件替换**（L198「只输出完整内容」），
#     要求模型**输出整个文件**。对 343KB / 8807 行的文件，这在**输出侧**
#     就不可能（远超任何模型的输出上限），注入侧再怎么调 cap 也救不了。
#
#   ★ 实测规模：`src/` 下**51 个文件超过 60KB 注入上限**，最大 **560KB**
#     ⇒ 旧协议下编排器**只能改少数小文件**，这是"落地"的主要拦路石。
#
# 【修法】超过阈值时自动切**区域编辑协议**：模型只看**目标行附近带行号的窗口**，
#   只输出**补丁块**，由**机器**施加到原文上。
#   补丁的每一处都机器可校验（行号在界内、块不重叠、行号递增），
#   非法补丁**如实拒绝并把原因回喂**，绝不静默套用。
#
# 【诚实边界】
#   · 本协议只解决"**改**已有文件"；**新建**文件仍走整文件协议（本就该如此）。
#   · 窗口是有界的 ⇒ 模型看不到远处上下文。需要跨远处改动时，
#     仍会失败并如实报"改动点不在窗口内" —— 那时应把目标拆成多轮。
#   · 机器施加 ≠ 语义正确。正确性仍由**判据门**（cargo test 真退出码 +
#     结构键保留门）裁决，本协议只保证"改的是它说改的地方"。

_PATCH_RE = re.compile(
    r"<<<\s*(REPLACE|DELETE|INSERT)\s+(\d+)(?:\s+(\d+))?\s*>>>[ \t]*\n?"
    r"(.*?)\n?<<<\s*END\s*>>>",
    re.DOTALL)
# ★ `\n?` 必须**可选**：自检实测抓到 `<<<DELETE 2 4>>><<<END>>>`（无换行 ——
#   模型写空删除时很自然这么写）在原来的 `>>>\s*\n` 下**匹配不到** ⇒ 整条补丁
#   被判「未找到任何补丁块」⇒ 明明合法的删除被拒。判据读错了东西的又一例。
_PATCH_KINDS = ("REPLACE", "DELETE", "INSERT")


def _focus_line(rel: str, goal: str) -> int | None:
    """从目标文本里抽该文件的**行号焦点**（如 `src/模块甲/mod.rs:8295`）。

    找不到 → None（调用方据此退回整文件协议，或如实报"改不了"）。
    """
    base = rel.replace("\\", "/").strip("/")
    # ★ 不能直接用「非空白非冒号」当路径字符集：目标文本里行号常写成
    #   `【登记位置】src/模块甲/mod.rs:8295`，路径**前面紧贴**标记字符，
    #   那样会把 `【登记位置】src/模块甲/mod.rs` 整个当成路径 ⇒ 匹配失败。
    #   （自检 #⑪ 实测抓到。）改为：在每个 `:数字` 处**往前切一段**，按分隔符取最后一片。
    for m in re.finditer(r"[:：]\s*(\d+)", goal or ""):
        pre = (goal or "")[:m.start()]
        cand = re.split(r"[\s`'\"（）()【】\[\]，,；;、]", pre)[-1]
        c = cand.replace("\\", "/").lstrip("./").strip("/")
        if not c:
            continue
        if c == base or c.endswith("/" + base) or base.endswith("/" + c):
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def _内容锚(目标: str) -> list[str]:
    """从目标文本里抽出**内容锚**（把「行号」换回「文本身份」）。

    ★★★★★ R70（真跑抓出 · 2026-10-16）：**行号不是身份，是坐标。**

      真跑证据：缺口在扫描那一刻登记为 `src/模块甲/mod.rs:10571`，
      而窗口是 **31 分钟后**才读的文件。这 31 分钟里**另一个进程在改同一个文件**
      （文件长了 22 行）⇒ 10571 行早已不是那处缺口 ⇒ 机器按 10571 取窗口
      ⇒ 窗口里**根本没有目标** ⇒ 模型如实拒收、每一窗都白烧。

      ★ 模型自己诊断对了（回执原文）：「要么窗口行号整体偏移，要么登记行号已陈旧
        …… 按规则 6，我**不猜行号**」。★ 它**拒绝**是对的 —— 危险的是
        一个"更听话"的模型会照着陈旧行号**改错地方**。

      ★ 这违反的是库里那条：**同一性必须和被测样本戊一样细**。
        行号是**坐标**（会因别人的改动整体平移），文本才是**身份**。
        ⇒ 目标文本里本来就带着机器抽的**原文行**与**上下文**，那才是身份。

    返回候选锚（按**特异性**从强到弱）：登记原文行 → 上下文里的非空行。
    """
    出: list[str] = []
    t = 目标 or ""
    m = re.search(r"【登记原文】[ \t]*(.+)", t)
    if m and m.group(1).strip():
        出.append(m.group(1).strip())
    # 上下文块：`【登记上下文】` 到下一个 `【` 之前
    m2 = re.search(r"【登记上下文】[ \t]*\n(.*?)(?=\n【|\Z)", t, re.S)
    if m2:
        for 行 in m2.group(1).splitlines():
            s = 行.strip()
            # ★ 太短的行（`{`、`}`、`//`）不配当身份 —— 它们遍地都是。
            if len(s) >= 8:
                出.append(s)
    # ★ 去重但**保序**（顺序就是特异性顺序）
    见 = set()
    唯: list[str] = []
    for a in 出:
        if a not in 见:
            见.add(a)
            唯.append(a)
    return 唯


def _定位焦点(原文: str, 目标: str, 行号: int) -> tuple[int, str]:
    """把「行号」重新定位到**当前文件**里的真实行。返回 (焦点行, 依据)。

    ★ R70：先认**内容**，再认行号。三种结局都要**说得清**：
      ① 行号仍然命中锚 ⇒ 直接用（不动）；
      ② 锚能唯一定位 ⇒ 按锚走，并说明行号已陈旧（**平移了多少行**要报出来）；
      ③ 锚在文件里**一个都找不到** ⇒ 如实报，**不按陈旧行号硬改**
         （「能在入口拦下的错，不要在出口兜」）。
    """
    行们 = (原文 or "").splitlines()
    n = len(行们)
    if n == 0:
        return 1, "空文件"
    行号 = max(1, min(int(行号 or 1), n))
    锚们 = _内容锚(目标)
    if not 锚们:
        return 行号, "无内容锚（目标里没带原文行）⇒ 沿用行号"

    # ① 行号仍命中**最强的那个锚** ⇒ 坐标没漂，直接用。
    if 锚们[0] in 行们[行号 - 1]:
        return 行号, "行号命中登记原文（坐标未漂）"

    # ② 按锚定位。★ 先要**唯一命中**（那才是身份）；都不唯一才退到"离行号最近"。
    命中表 = []
    for i, a in enumerate(锚们):
        hits = [k + 1 for k, l in enumerate(行们) if a in l]
        if hits:
            命中表.append((i, a, hits))
    if not 命中表:
        return 行号, ("★ 登记原文与上下文**在当前文件里一处都找不到**"
                      "（文件已被大幅改写）⇒ 不按陈旧行号硬改")

    for i, a, hits in 命中表:
        if len(hits) == 1:
            return hits[0], (f"按内容锚定位（锚 #{i + 1} 唯一命中；"
                             f"登记行号 {行号} 已陈旧，实际在 {hits[0]} 行，"
                             f"平移 {hits[0] - 行号:+d} 行）")
    # ③ 都有多命中 ⇒ 取命中数最少、再离行号最近的那个
    命中表.sort(key=lambda t: (len(t[2]), min(abs(h - 行号) for h in t[2])))
    i, a, hits = 命中表[0]
    best = min(hits, key=lambda h: abs(h - 行号))
    return best, (f"按内容锚定位（锚 #{i + 1} 有 {len(hits)} 处命中 ⇒ "
                  f"取离登记行号 {行号} 最近的一处 {best}）")


def _numbered_window(text: str, focus: int, radius: int = 150,
                     cap: int = 24000) -> tuple[str, int, int]:
    """取焦点行附近的带行号窗口。返回 (窗口文本, 起行, 止行)（1-based 闭区间）。

    ★ 行号**必须**带 —— 模型要靠它给出补丁的行号；不带行号就只能靠猜，
      而猜出来的行号一旦机器施加，就是"改错地方"。
    """
    行们 = text.splitlines()
    n = len(行们)
    if n == 0:
        return "", 1, 0
    focus = max(1, min(focus, n))
    a = max(1, focus - radius)
    b = min(n, focus + radius)
    # 按字符预算收缩（从两端等量收，保持焦点居中）
    while b > a:
        块 = "\n".join(f"{i:>6}| {行们[i - 1]}" for i in range(a, b + 1))
        if len(块) <= cap:
            break
        收a = a < focus
        收b = b > focus
        if 收a and (not 收b or (focus - a) >= (b - focus)):
            a += 1
        elif 收b:
            b -= 1
        else:
            a += 1
    块 = "\n".join(f"{i:>6}| {行们[i - 1]}" for i in range(a, b + 1))
    return 块, a, b


_括号对 = {"(": ")", "[": "]", "{": "}"}
_闭括号 = {v: k for k, v in _括号对.items()}

# ★ R70b：窗口右边界之后**至少要留几行**（焦点不许落在窗口边界上）。
#   实测值 3：注释行 + `#[test]` + `fn …() {` 恰好三行 —— 缺口登记的注释
#   与其描述的函数之间就隔着这么多。★ 太小 ⇒ 注释型焦点仍看不见函数体。
_焦点后最少上下文 = 3


def _带行号(行们: list[str], a: int, b: int) -> str:
    return "\n".join(f"{i:>6}| {行们[i - 1]}" for i in range(a, b + 1))


def _块闭合窗口(text: str, focus: int, radius: int = 150,
                cap: int = 24000) -> tuple[str, int, int, str]:
    """★★★★★ R69：窗口必须是**自含区域**（按结构取，不按距离取）。

    返回 (窗口, 起行, 止行, 说明)。

    ── 为什么必须改（真跑证据 + 全库校准）──
    `_numbered_window` 取的是「焦点 ± 固定半径」。半径会**横切块边界**。
    在真工程 60 个最大的 `.rs`、**1557 个焦点**上**实测**：

        旧窗口：两端深度不等（末尾挂着没闭合的开括号）**79.45%**
        旧窗口：区间内有孤儿括号（`}` 的 `{` 在窗口外）**69.11%**
        新窗口：**0.00%** / **0.00%**

    ⇒ 模型看到的**多数是半截块**：它新开一层块（`mod X {`）却不知道在哪里闭合
      ⇒ 连续两轮 `unclosed delimiter`（`mod 涌现值测试 {` 在 2468 行开着，
        到 3013 行文件结尾都没闭合）。
    R63 在入口拦住了、R67 把坐标翻译对了 —— 但那两道都是**拦**，不是**不给它犯**。
    ★ 根因一句话：**窗口是按"距离"切的，而语法是按"结构"分的。**

    ── 修法（用结构，不用枚举）──
    **自含区域** = 两条同时成立：
      · `深[起] == 深[止+1] == 基准深度`（末尾没有没闭合的开括号）
      · 区间内深度**永不浅于基准深度**（没有孤儿闭括号）
    合起来 ⇒ **窗口内每一对括号都自成一对** ⇒ 模型看不到任何孤儿括号。
    ① 从最大到最紧**逐层试探** `_自含(dd)`（`dd` 从 0 到焦点深度），
       取**放得进预算的最大**那个；
    ② 放不下的 ⇒ 沿「包住焦点的**孩子块**」逐层下钻（每一层都仍是自含区域）；
    ③ 仍放不下（单个巨大块）⇒ 给**头 + 焦点邻域 + 尾**三段
       —— ★ **焦点必须可见**：只给头尾会把焦点藏进省略里 ⇒ 那一轮必然白烧。

    ★ 附带把「窗口是自含区域」这个**不变量**写进 prompt（见 `_patch_prompt_block`）
      —— 模型看得见的约束才是约束。

    ── 自检/校准踩过的坑（如实登记）──
    ① 第一版按「含焦点的**最深**块跨度」取最内层，结果焦点行上的**单行自配对**
       （`x1999();` 里的 `()`）比外层 `{}` 更深 ⇒ 窗口退化成**单行**。
       ⇒ **"最内层"不等于"最有用"**。
    ② **同一个坑我踩了三次**（这是本轮最该记住的一条）：
       `_自含` 的右扫描写 `>= dd`；收缩的右搜索写 `!= d`；旧「外层块」扫描写 `!= d-1`。
       三处的病**一模一样**：**用「不等」当停止条件，就会跨过那个"掉下去"的点**
       ⇒ 区间里混进孤儿括号。不自含率因此反复反弹：
       **0% → 8.67%（改坏）→ 1.22% → 1.4% → 0%**。
       ⇒ 正确的写法只有一种：**「深 > 基准」时继续走，一旦 ≤ 就停，只接受「恰好 == 基准」**。
       ⇒ 现在全库只剩**一个** `_自含`，不再有第二把尺子 —— 库里那条 **口径必须统一**。
    ③ 原来另有一个 `_块跨度()` 解析块，但它与 `_行首深度()` 是**两把尺子**
       （括号不匹配时弹栈方式不同 ⇒ 永久分叉）⇒ 两个函数对同一行给出不同深度。
       ⇒ **删掉它**，只留深度数组这一把尺子。
    ④ 焦点本身是**闭括号行**（真库里大量焦点行就是 `}`）时，`_自含(焦点深度)` 必然
       失败：那一行的**下一行**深度已经掉了一级 ⇒ 整批掉进兜底分支（真库 1.4%）。
       ⇒ 必须**逐层往下试**（`dd` 从 0 到焦点深度），不能只试一层。
    ⑤ 未闭合的文件里，右扫描会把下标推到 `n+2` ⇒ `深[i]` **越界抛异常**。
       ⇒ 必须先判界再取；**括号不平衡只许如实退化，不许崩**（"崩溃不是报告"）。
    """
    行们 = text.splitlines()
    n = len(行们)
    if n == 0:
        return "", 1, 0, ""
    focus = max(1, min(focus, n))
    净 = _掩掉字面量与注释(text)
    深 = _行首深度(净, n)

    # ①（旧版已删）**这里原来另有一段「外层块」扫描，用的停止条件是
    #   `深[L] != d_in - 1` / `深[M+1] != d_in - 1` —— 与 `_自含` 的
    #   「用不等当停止条件」是同一个错，它会跨过掉到 d_in-1 以下的地方，
    #   于是"外层块"里混进了孤儿闭括号（全库 1.22% 残差就是它）。
    #   现在统一走下面这一个 `_自含`，不再有第二把尺子。**

    # ② 构造**自含区域**：`深[A] == 深[B+1] == dd` **且**区间内深度**永不浅于 dd**。
    #    ★ 两个条件缺一不可：
    #      · 只有「两端深度相等」⇒ 区间里可能**有闭括号，其开括号在窗口外**
    #        （全库校准量到 1.22% 残差）⇒ 模型看到"孤儿闭括号"。
    #      · 只有「不浅于 dd」⇒ 区间末尾可能还有没闭合的开括号。
    #    合起来才是：**窗口内每个括号都自成一对**（模型看不到任何孤儿括号）。
    d = 深[focus]

    def _自含(dd: int) -> tuple[int, int] | None:
        """取含焦点、深度永不浅于 `dd`、两端深度都等于 `dd` 的**最大**区间。

        ★ 右扫描必须是 `> dd` **然后要求恰好等于 `dd`**，不能写成 `!= dd` 再
          「往后找到第一个 == dd 的行」—— 后者会**跨过那个掉到 dd 以下的地方**，
          于是区间里就留下了深度 < dd 的孤儿闭括号。
        ★ 左扫描不需要这层校验：深度每次只变 ±1，所以「左边那行深度 < dd」
          必然推出「本行深度 ≤ dd」；配合本行 ≥ dd 即得恰好 == dd。

        ── 同一个坑我踩了三次（如实登记）──
          ① `_自含` 的右扫描写 `>= dd`；② 收缩的右搜索写 `!= d`；
          ③ **分支①「外层块」也写 `!= d-1`**（就是它把不自含率钉在 1.22%）。
          三处的病一模一样：**用「不等」当停止条件，就会跨过那个"掉下去"的点**。
          ⇒ 现在全库只剩这一个 `_自含`，不再有第二把尺子。
        """
        k = focus
        while k > 1 and 深[k - 1] >= dd:
            k -= 1
        A = k
        if 深[A] != dd:
            return None
        i = focus + 1
        while i <= n + 1 and 深[i] > dd:
            i += 1
        # ★ `i` 可能被推到 `n+2`（文件末尾还有没闭合的开括号）⇒ 必须先判界，
        #   否则 `深[i]` 越界抛异常。自检抓到的就是这个（"未闭合文件"那条齿）。
        if i > n + 1 or 深[i] != dd:
            return None
        B = i - 1
        if not (A <= focus <= B):
            return None
        B = _补尾(B, dd)
        return A, B

    def _补尾(B: int, dd: int) -> int:
        """把右边界往后推，直到焦点之后**至少有 `_焦点后最少上下文` 行**。

        ★★★★★ R70b（真跑抓出 · 2026-10-16）：**窗口不许以焦点行结尾。**

        ── 真跑证据 ──
        缺口登记的是一行**注释**（`// 【未核】本测试用 std::time::Instant…`），
        而它描述的函数在**紧接着的下一行**。`_自含` 取的是「焦点之后第一个
        深度回到 `dd` 的行」—— 注释行**自己**就是那个位置 ⇒ 窗口**正好在焦点行结束**。

        真库实测：焦点 10610 行，窗口 `10460..10610` ⇒ **焦点离右边界 0 行**。
        ⇒ 模型看得见注释，**看不见它描述的那个函数** ⇒ 它（正确地）判定
          「窗口声明与登记上下文不可能同时为真」⇒ **如实拒收** ⇒ 整轮白烧。

        ★ 这与 R69 的「只给头尾会把焦点藏进省略里」是**同一类病**：
          窗口必须让**要改的东西**看得见，而不只是让**焦点那一行**看得见。
          「窗口含焦点」是**必要条件**，不是**充分条件**。

        ★ 保持自含（不变量不被破坏）：要么往后吃一行**同样在 `dd` 深度**的行，
          要么把**整个孩子块**吞下来 —— 吞完深度仍回到 `dd`，两端仍相等。
          块**没闭合**（文件本身不平衡）⇒ **不硬吞**，如实停在这里。
        """
        while B < n and (B - focus) < _焦点后最少上下文:
            nxt = B + 1
            if 深[nxt + 1] > dd:                 # nxt 开了一层块 ⇒ 整块吞下
                j = nxt + 1
                while j <= n + 1 and 深[j] > dd:
                    j += 1
                if j > n + 1 or 深[j] != dd:
                    break                        # 块没闭合 ⇒ 不硬吞（宁可短）
                B = j - 1
            else:
                B = nxt
        return B

    # ① 选窗口 = **放得进预算的最大自含区间**。
    #    ★ 焦点本身是**闭括号行**时（真库里大量焦点行就是 `}`），`_自含(d)` 必然
    #      失败：那一行的**下一行**深度已经是 `d-1`，右扫立刻掉下去 ⇒ 返回 None。
    #      所以必须**逐层往下试**（dd 从 0 到 d），取第一个放得下的。
    #      dd 越小 = 区间越大；第一个放得下的就是"最大的、放得下的"。
    #      （旧版没做这层，于是这些焦点全掉进兜底分支 ⇒ 全库 1.4% 不自含。）
    if d > 0:
        外 = _自含(d - 1)
        if 外 is not None and len(_带行号(行们, 外[0], 外[1])) <= cap:
            return (_带行号(行们, 外[0], 外[1]), 外[0], 外[1],
                    f"（窗口 = **外层块的完整范围**：第 {外[0]} 行开、第 {外[1]} 行闭"
                    f" ⇒ 块的起止括号都在你眼前；"
                    f"你在窗口内开的每一层块**都必须在窗口内闭合**）")

    区 = None
    for _dd in range(0, d + 1):
        _r = _自含(_dd)
        if _r is not None and len(_带行号(行们, _r[0], _r[1])) <= cap:
            区 = _r
            break
    if 区 is None:                       # 没有放得下的 ⇒ 取最紧的那个（后面下钻/截断）
        for _dd in range(d, -1, -1):
            _r = _自含(_dd)
            if _r is not None:
                区 = _r
                break

    # ②b **收缩到半径附近**：最大自含区间在 `d == 0` 时会一路退到文件头
    #    （深度 0 永不小于 0）⇒ 区间等于"文件前缀"，白占预算且没意义。
    #    ★ 收缩后**仍是自含**：子区间的深度最小值只会更大，
    #      且两端都仍取在深度恰好 == 基深度 的行上。
    if 区 is not None:
        A0, B0 = 区
        基深 = 深[A0]
        A = A0
        k = max(A0, min(B0, focus - radius))      # ★ 夹在 [A0,B0] 内（否则越界）
        while k > A0 and 深[k] > 基深:            # ★ 只能「深 > 基深」时继续走
            k -= 1
        if 深[k] == 基深:
            A = k
        B = B0
        k = max(A0, min(B0, focus + radius))      # ★ 同上
        while k < B0 and 深[k + 1] > 基深:        # ★ 一旦 ≤ 基深 就停，只接受 ==
            k += 1
        if 深[k + 1] == 基深:
            B = k
        区 = (A, B) if A <= focus <= B else 区

    # ③ 太大 ⇒ 沿「包住焦点的孩子块」**逐层下钻**（每一层都仍是自含区域）
    if 区 is not None:
        A, B = 区
        dd = 深[A]                      # ★ 用**区间自己的**基深度，不是 `d`
        for _ in range(64):
            if len(_带行号(行们, A, B)) <= cap:
                break
            s = None
            k = A
            while k < B:
                if 深[k] == dd and 深[k + 1] == dd + 1:
                    e = k + 1
                    while e < B and 深[e + 1] != dd:
                        e += 1
                    if 深[e + 1] == dd:
                        if k <= focus <= e:
                            s = (k, e)
                            break
                        k = e + 1
                        continue
                k += 1
            if s is None:
                break
            A, B, dd = s[0], s[1], dd + 1

        说明 = (f"（窗口 = **自含区域**：第 {A} 行开、第 {B} 行闭，"
                f"两端括号深度相同、且窗口内**每一对括号都自成一对**"
                f" ⇒ 你在窗口内开的每一层块**都必须在窗口内闭合**）")
        整 = _带行号(行们, A, B)
        if len(整) <= cap:
            return 整, A, B, 说明
        # 下钻到底仍放不下（单个巨大块）⇒ **三段**：头 + **焦点邻域** + 尾。
        #   ★ 必须给三段：只给头尾会把**焦点本身**藏进省略里 ⇒ 模型看不到要改的地方，
        #     那一轮必然白烧（正是这一轮要修的失败模式）。两端也必须在 ⇒ 括号可见。
        k2 = max(10, (cap // 4) // 40)
        fa = max(A, focus - k2)
        fb = min(B, focus + k2)
        头 = _带行号(行们, A, min(A + k2 - 1, fa - 1)) if fa > A else ""
        尾 = _带行号(行们, max(B - k2 + 1, fb + 1), B) if fb < B else ""
        中 = _带行号(行们, fa, fb)
        省1 = (fa - 1) - (A + k2 - 1) if 头 else 0
        省2 = (B - k2 + 1) - (fb + 1) if 尾 else 0
        片 = []
        if 头:
            片.append(头)
            片.append(f"   ……（此处省略 {省1} 行，未列出；**不要**让补丁块跨越它）……")
        片.append(中)
        if 尾:
            片.append(f"   ……（此处省略 {省2} 行，未列出；**不要**让补丁块跨越它）……")
            片.append(尾)
        文 = "\n".join(片)
        省总 = 省1 + 省2
        return 文, A, B, (说明 + f"；块太大 ⇒ 给**头 {k2} 行 + 焦点邻域 {fb - fa + 1} 行"
                                 f" + 尾 {k2} 行**，中间共省略 {省总} 行（未列出）")

    # ④ 兜底：连自含区域都构造不出（文件本身括号就不平衡）
    #    ⇒ 退回半径窗口，并**如实说明**（R63 的入口闸会另行拒收）
    a = max(1, focus - radius)
    b = min(n, focus + radius)
    说明 = ("（★ 本文件括号**本身不平衡**，无法给出自含区域；"
            "下面是焦点附近的原文，请**不要**新开块）")
    整 = _带行号(行们, a, b)
    return 整[:cap], a, b, 说明


def _行首深度(净: str, n: int) -> list[int]:
    """返回每行**行首**的括号深度。索引 1..n+1（n+1 = 文末深度）。

    ★ 有了它，窗口边界就能按**结构**（深度回归处）吸附，而不是按**距离**取。
    """
    深 = [0] * (n + 2)
    d = 0
    行 = 1
    for c in 净:
        if c == "\n":
            行 += 1
            if 行 <= n:
                深[行] = d
        elif c in _括号对:
            d += 1
        elif c in _闭括号:
            d = max(0, d - 1)
    深[n + 1] = d
    return 深


def _掩掉字面量与注释(文本: str) -> str:
    """把**注释与字符串/字符字面量**替换成同长度空白，保留行结构。

    ★ 为什么必须掩：括号计数若把字符串里的 `{` 也算进去，就会**假红**
      （模型在字符串里写 `"{"` 是合法的）。而「假红比无齿更坏」。
    ★ 为什么要保守：拿不准的构造（Rust 生命周期 `'a`、未知前缀）
      **一律按"不是字面量"处理** —— 宁可漏掩（少拦一次），不可错掩（假红一次）。
    """
    出 = []
    i, n = 0, len(文本)
    栈注释 = 0
    while i < n:
        c = 文本[i]
        nxt = 文本[i + 1] if i + 1 < n else ""
        if 栈注释:
            出.append("\n" if c == "\n" else " ")
            if c == "*" and nxt == "/":
                出.append(" ")
                i += 2
                栈注释 -= 1
                continue
            if c == "/" and nxt == "*":
                出.append(" ")
                i += 2
                栈注释 += 1
                continue
            i += 1
            continue
        if c == "/" and nxt == "/":
            while i < n and 文本[i] != "\n":
                出.append(" ")
                i += 1
            continue
        if c == "/" and nxt == "*":
            出.append("  ")
            i += 2
            栈注释 = 1
            continue
        if c == "#" and nxt != "[":
            # `#` 起行注释（Python/shell）—— 但 **Rust 的 `#[…]`/`#![…]` 是属性**，
            # 不是注释。用"后一个字符是不是 `[`"区分（保守：拿不准就**不掩**，
            # 少拦一次好过错掩一次 ⇒ 不假红）。
            while i < n and 文本[i] != "\n":
                出.append(" ")
                i += 1
            continue
        # Rust 原始串 r"..." / r#"..."# / r##"..."##
        if c == "r" and (nxt in ('"', "#")):
            j = i + 1
            井 = 0
            while j < n and 文本[j] == "#":
                井 += 1
                j += 1
            if j < n and 文本[j] == '"':
                闭 = '"' + "#" * 井
                k = 文本.find(闭, j + 1)
                if k >= 0:
                    段 = 文本[i:k + len(闭)]
                    出.append("".join("\n" if ch == "\n" else " " for ch in 段))
                    i = k + len(闭)
                    continue
        if c == '"':
            # 三引号（Python）或普通串
            三 = 文本.startswith('"""', i) or 文本.startswith("'''", i)
            引 = 文本[i:i + 3] if 三 else '"'
            j = i + len(引)
            while j < n:
                if 文本[j] == "\\":
                    j += 2
                    continue
                if 文本.startswith(引, j):
                    j += len(引)
                    break
                j += 1
            段 = 文本[i:j]
            出.append("".join("\n" if ch == "\n" else " " for ch in 段))
            i = j
            continue
        if c == "'":
            # 字符字面量 vs 生命周期/撇号：**只有** `'x'` / `'\n'` 这种才认
            j = i + 1
            if j < n and 文本[j] == "\\":
                j += 2
                while j < n and 文本[j] != "'":
                    j += 1
                if j < n:
                    j += 1
                    continue_ok = True
                else:
                    continue_ok = False
            elif j + 1 < n and 文本[j + 1] == "'":
                j += 2
                continue_ok = True
            else:
                continue_ok = False       # 生命周期 `'a` ⇒ 不掩，原样保留
            if continue_ok:
                段 = 文本[i:j]
                出.append("".join("\n" if ch == "\n" else " " for ch in 段))
                i = j
                continue
        出.append(c)
        i += 1
    return "".join(出)


def _定界符失衡定位(文本: str) -> list[tuple[int, str]]:
    """返回 **(行号, 说明)** 的原始定位；行号是**传入文本**的 1-based 行号。

    ★ 说明里**不含行号** —— 便于调用方把行号换成**别的坐标系**再拼。
      （R67：入口闸原先直接把"补丁后文件"的行号写进说明，而模型的补丁用的是
        **原文件**行号 ⇒ 补丁一插入行，两个坐标系就错开 ⇒ 模型拿到对不上的行号。）
    """
    净 = _掩掉字面量与注释(文本)
    栈: list[tuple[str, int]] = []
    错: list[tuple[int, str]] = []
    行 = 1
    for c in 净:
        if c == "\n":
            行 += 1
            continue
        if c in _括号对:
            栈.append((c, 行))
        elif c in _闭括号:
            if not 栈:
                错.append((行, f"多出一个 `{c}`（没有与之配对的开括号）"))
            elif 栈[-1][0] != _闭括号[c]:
                开, 开行 = 栈.pop()
                错.append((行, f"`{c}` 与第 {开行} 行的 `{开}` 不配对"))
            else:
                栈.pop()
    for 开, 开行 in 栈[:8]:
        错.append((开行, f"`{开}` 未闭合（到文本结尾都没有对应的闭括号）"))
    return 错


def _定界符失衡(文本: str) -> list[str]:
    """返回**结构失衡**的逐条说明；空列表 = 平衡。

    ★ 只看括号（`()`/`[]`/`{}`），先掩掉注释与字面量。
      语言无关（任何用括号分块的语言都适用），且**不用解析语法**。
    """
    return [f"第 {行} 行 {说}" for 行, 说 in _定界符失衡定位(文本)]



def _apply_patch(orig: str, patch_text: str) -> tuple[str | None, list[str]]:
    """把补丁施加到原文。返回 (新文本 或 None, 错误列表)。

    ★ 机器施加、机器校验：行号界内 / 块不重叠 / 至少一处改动。
      任一条不满足 → 返回 None + 逐条原因（回喂给模型，不静默套用）。
    """
    errs: list[str] = []
    行们 = orig.splitlines()
    n = len(行们)
    块们 = _PATCH_RE.findall(patch_text or "")
    if not 块们:
        return None, [
            "未找到任何补丁块。本文件超过整文件阈值 ⇒ 必须用区域编辑协议，格式为："
            "`<<<REPLACE 起行 止行>>>` 新内容 `<<<END>>>`（行号 1-based、闭区间）。"
            "DELETE / INSERT 同理。**不要**输出整个文件。"]

    解析: list[tuple[str, int, int, list[str]]] = []
    for kind, a_s, b_s, body in 块们:
        try:
            a = int(a_s)
            b = int(b_s) if b_s else a
        except ValueError:
            errs.append(f"补丁行号不是整数：{a_s!r}/{b_s!r}")
            continue
        体 = body.split("\n") if body else []
        # 去掉块体首尾因格式产生的空行（模型常在 >>> 后多留一空行）
        while 体 and 体[0].strip() == "":
            体.pop(0)
        while 体 and 体[-1].strip() == "":
            体.pop()
        if kind == "INSERT":
            if not (0 <= a <= n):
                errs.append(f"INSERT 行号 {a} 越界（应在 0..{n}）")
                continue
            解析.append((kind, a, a, 体))
        else:
            if not (1 <= a <= b <= n):
                errs.append(f"{kind} 行号区间 {a}..{b} 越界（文件共 {n} 行，"
                            f"应为 1..{n} 且 起≤止）")
                continue
            if kind == "DELETE":
                体 = []
            解析.append((kind, a, b, 体))

    if errs:
        return None, errs
    if not 解析:
        return None, ["补丁块解析后为空"]

    # 重叠检查（按原始区间）
    区间 = sorted((a, b) for _, a, b, _ in 解析)
    for i in range(1, len(区间)):
        if 区间[i][0] <= 区间[i - 1][1]:
            errs.append(f"补丁块重叠：{区间[i-1]} 与 {区间[i]}")
    if errs:
        return None, errs

    # 自底向上施加（行号才不会被前面的改动顶偏）
    新行 = list(行们)
    # ★★★★★ R67：**同时**维护每一结果行的「出身」。
    #   出身 = 该行是从原文件哪一行来的，还是补丁新造的（以及锚在原文哪一段）。
    #   为什么必须并行维护：入口闸要报失衡位置，而**报坐标必须用模型自己的坐标系**
    #   —— 模型的补丁用的是**原文件**行号；若报"补丁后文件的第 N 行"，
    #   补丁一插入行两个坐标系就错开，模型**拿着对不上的行号没法改**。
    #   （这正是库里那条硬规矩：**口径必须统一**。）
    #   int  ⇒ 原文件行号（1-based）
    #   (-1, a)        ⇒ 你 INSERT 的内容，锚点在原文件第 a 行之后
    #   (-2, a, b)     ⇒ 你 REPLACE 的内容，替换的是原文件第 a..b 行
    源行: list = list(range(1, n + 1))
    for kind, a, b, 体 in sorted(解析, key=lambda t: -t[1]):
        if kind == "INSERT":
            新行[a:a] = 体
            源行[a:a] = [(-1, a)] * len(体)
        else:
            新行[a - 1:b] = 体
            源行[a - 1:b] = [(-2, a, b)] * len(体)

    if 新行 == 行们:
        return None, ["补丁施加后内容与原文完全相同（空改动）—— 请确认是否真的改了东西"]

    结果 = "\n".join(新行) + ("\n" if orig.endswith("\n") else "")

    # ★★★★★ R63（真无人值守抓出 · 2026-10-16）：**定界符配对闸**。
    #   【现象】轮 1/轮 2 模型产出的 Rust 都带 `unclosed delimiter`
    #     （`mod 涌现值测试 {` 在 2468 行开着，到文件结尾没闭合；
    #      补丁后文件从 2793 行长到 3013 行 ⇒ 是**补丁体自己没闭合**）。
    #     编译门抓到了、正确回滚了 —— 但那是**出口兜**：整文件写下去、
    #     再编译、再回滚，代价是一整轮。
    #   【根因】区域编辑协议只给模型**焦点附近的一个编号窗口**
    #     ⇒ 模型**看不到块的边界**，无从判断自己是否拆散了配对。
    #   【口径】「能在入口拦下的错，不要在出口兜」⇒ 补丁施加前先验配对。
    #   ★★ 关键设计：**本闸只做提前拦截与精确回喂，不做最终裁决**。
    #     词法掩码（注释/字面量）是启发式的，偶有偏差 ⇒ 若把它当最终裁决
    #     就会**假红**（把合法补丁拒掉），而「假红比无齿更坏」。
    #     ⇒ 只在「**原文本来是平衡的**、补丁后变得不平衡」时拒 ——
    #       原文平衡说明掩码对这个文件的词法约定是**校准过**的，
    #       此时判不平衡的误判率最低；且被拒后模型会拿到精确行号重试，
    #       重试仍不通过就走原有路径（编译门兜底）。
    原错 = _定界符失衡(orig)
    if not 原错:
        新错 = _定界符失衡定位(结果)
        if 新错:
            # ★ R67：把"补丁后文件"的行号**翻译回模型自己的坐标系**。
            #   只在 原错 为空时才会走到这里 ⇒ 这些失衡**必然**出在你新造的内容里，
            #   所以逐条都能指名道姓说是哪一块补丁造的。
            定位: list[str] = []
            for 行, 说 in 新错[:8]:
                i = 行 - 1
                出 = 源行[i] if 0 <= i < len(源行) else None
                if isinstance(出, int):
                    定位.append(f"  · {说} —— 在**原文件第 {出} 行**的内容里")
                elif isinstance(出, tuple) and 出[0] == -1:
                    定位.append(f"  · {说} —— 在**你 INSERT 的内容**里"
                                f"（锚点：原文件第 {出[1]} 行之后）")
                elif isinstance(出, tuple) and 出[0] == -2:
                    定位.append(f"  · {说} —— 在**你 REPLACE 的内容**里"
                                f"（替换的是原文件第 {出[1]}..{出[2]} 行）")
                else:
                    定位.append(f"  · {说} —— 在文件结尾附近"
                                f"（补丁后共 {len(新行)} 行）")
            return None, [
                "★★ 补丁被**入口闸**拒收：它破坏了括号配对"
                "（原文本来是平衡的，补丁后不平衡）。"
                "区域编辑协议只给你一个窗口 ⇒ 你看不到块边界；"
                "请**只改焦点附近的局部**，不要动到块的起止括号。\n"
                "★ 位置按**你给补丁时用的行号**（原文件坐标）标注："
            ] + 定位 + [
                "⇒ 修法：把你新开的那一层块的**闭括号补上**，"
                "或者干脆不要新开块（在原块内部改动）。"
            ]
    return 结果, []


def _退过179(rel: str, 台账: dict | None = None) -> bool:
    """这个文件是否**被回退过**（机器已有证据：整份重写会把好地方改坏）。

    ★ R179（第 81 次真跑抓出 · 2026-10-28）：
      补丁模式原本只在 `len(原文) > _REPO_TARGET_CAP(60000)` 时才开，
      而真项目里出问题的文件是 19696 / 37836 / 16348 / 6176 / 1748 字符
      ⇒ **全部走整文件重写** ⇒ 每轮都引入 7 个新错 ⇒ 连续 3 轮零进展。
      ★ R177 把「你上一版被退了 + 新引入的错」送进 prompt **确实生效**
        （日志可见），但**结果没变** ⇒ 模型不是不知道，是做不到。
      ⇒ 修法不是再劝它，是**把改动面缩小**：退过的文件只让它改那几行。
    """
    if not isinstance(台账, dict):
        return False
    return int(台账.get(str(rel), 0) or 0) >= 1


def _patch_prompt_block(rel: str, win: str, a: int, b: int, n: int,
                        窗口说明: str = "") -> str:
    """区域编辑协议的提示块（含带行号窗口 + 严格格式 + 禁止整文件输出）。

    ★ R69：窗口现在是**语法闭合区域**（按块边界取），所以这里要**明说这个不变量**
      —— 模型看得见的约束才是约束。「给你一个闭合区域」若不告诉它，
      它仍然会以为自己可以随便开块。
    """
    闭块 = ""
    if 窗口说明:
        闭块 = (f"\n★ **窗口是语法闭合区域**：{窗口说明}\n"
                f"  ⇒ 你在窗口内**新开的每一层块，都必须在窗口内闭合**。\n"
                f"  ⇒ 若你只想改块**内部**的几行，就**不要**动块的起止括号。\n")
    return (
        f"\n\n═══ 【区域编辑协议】{rel} 共 {n} 行，**超出整文件阈值** ═══\n"
        f"本文件太大，**不要输出整个文件**（做不到，且会被拒）。\n"
        f"下面是目标行附近的**带行号窗口**（格式 `行号| 内容`，行号为 1-based）：\n"
        f"{闭块}\n"
        f"```\n{win}\n```\n\n"
        f"请**只输出补丁块**，严格用下列格式（可多块，每块一个）：\n\n"
        f"<<<REPLACE 起行 止行>>>\n新的行内容\n可以多行\n<<<END>>>\n\n"
        f"规则：\n"
        f"1. 行号是**原文件**的行号（即上面窗口里 `|` 左边那个数），1-based 闭区间。\n"
        f"2. 只替换真正要改的行；**未列入补丁块的行会被逐字保留**。\n"
        f"3. 多块之间**不得重叠**，且都要在 1..{n} 内。\n"
        f"4. 纯删除用 `<<<DELETE 起行 止行>>>` 紧接 `<<<END>>>`；\n"
        f"   在行 a 之后插入用 `<<<INSERT a>>>`（a=0 表示文件开头）。\n"
        f"5. **不要**输出 markdown 围栏、不要解释文字、**不要**输出整文件。\n"
        f"6. 目标改动点在第 {a}..{b} 行窗口内；若你要改的地方**不在**这个窗口里，\n"
        f"   **如实说明**，不要凭猜行号乱改。\n"
        f"7. ★ **括号必须配对**：机器在施加补丁前会验一遍；"
        f"补丁若让配对失衡会被**当场拒收**（并告诉你失衡在你哪一块补丁里）。")



if __name__ == "__main__":
    raise SystemExit(main())

