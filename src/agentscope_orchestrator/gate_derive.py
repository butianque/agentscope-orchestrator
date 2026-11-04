"""判定层必然性机验（定理A 的工程兑现）—— 不经过任何模型。

双定理边界（模块契约，编排器在判定层唯一可信的正确性来源）：
  定理A（必要）：编排器的「认可目标达成」只发生在判定层：门 G 由规约按载体律
      机器推导，且满足充分性机验（G 非平凡、有真实机器行为门、覆盖验收叙述、
      沙箱安全），则 G 全绿 ⇒ 目标达成是机器可断言的必然，判定不经过模型。
      这是必然：正确性完全不依赖 LLM 是否聪明。
  定理B（生成）：LLM 只是候选生产器，其产出正确性只有概率保证。本模块不冒充
      证明 "P(达成)=1"。它只保证把随机性开除出判定层——对未满足规约的候选，
      机器门必然拦截（虚警→0）。生产效能在门强度上单调逼近，受机器控制。

本模块只做两件机器判定：
  1) sufficiency_bound(gates, spec) -> (decision_necessary: bool, reasons)
       判该门集是否足以支撑「判定层必然性」。拒绝平凡/软/空洞门（防"通关却目标
       未达成"的假完成，对软判一律红）。
  2) check_cmd_safety(gates, root) -> (ok, violations)
       沙箱：命令白名单 + cwd 不越出工作区根 + 禁跳越。防模型写的判据门被当成
       任意代码执行通道。
两者都是纯机器断言，置换/补偿不经过模型。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentscope_orchestrator.gate_spec import validate as gate_validate
from agentscope_orchestrator import profile as prof_mod

# 可执行门的顶层命令白名单（沙箱）：anchor 到解释器/运行器，不放开任意命令。
# 基础集与项目无关；项目可在档案 gate_cmd_whitelist 追加自己的构建工具。
_CMD_TOP_WHITELIST_BASE = {
    "python", "python3", "py", "node", "true", "false",
    # ★★★★★ R183（第 88 次真跑 · 2026-10-28 18:05）：**Windows 上的 `bash` 是
    #   个摆设，`powershell` 才是真跑得起来的那个 shell。**
    #   真证据：规划器给出的 7 条门全是 `powershell -Command ...`，被沙箱整批拒：
    #     `沙箱:门#1/#8/#15/#22/#29 … 顶层命令不被白名单允许：'powershell'`
    #   ⇒ 白白多烧**一整个规划轮**（回喂重来）。
    #   ★ 白名单自己的口径写得很清楚：收的是「**解释器/运行器**」。
    #     `bash` / `sh` 就是 shell，`powershell` 也是 shell —— 同类不同命，
    #     这不是「保守」，这是**在原地长（Linux 写法）上做判据**：
    #     项目在 Windows 上，模型给的 Windows 写法被判成「不可信」。
    #   ★★ 沙箱强度不降：收的都是 shell/解释器，本来就能跑任意命令；
    #     白名单挡的是「模型塞个任意**可执行名**」，不是「shell 不给用」。
    "bash", "sh", "powershell", "pwsh", "pytest",   # 运行器
    "git",                    # 版本控制
}
_CMD_TOP_WHITELIST = _CMD_TOP_WHITELIST_BASE

# ★★★ 工程自带构建工具的**自动识别**（2026-10-13 补 · 修"通用工程跑不起来"）。
#
# ── 实测缺口 ──
#   在一个**独立 Rust 小工程**上真跑通用目标，规划出的判据门是
#   `cargo build` / `cargo test`，结果被沙箱拒：
#       沙箱:门#0 顶层命令不被白名单允许：'cargo'
#   ⇒ **通用工程开发根本跑不起来**，除非用户手工在档案里写
#     `gate_cmd_whitelist: [cargo]`。
#
# ── 为什么这不是"放开沙箱" ──
#   白名单的**目的**是"模型不能塞任意可执行"。而这里的判据是
#   **工程自己声明的构建工具** —— 由**仓库里的清单文件**决定，
#   不是模型说了算。⇒ 识别的是"这个工程用什么建"，
#   与"模型想跑什么"**无关**，沙箱强度不降。
#
# ── 识别规则（清单文件 → 顶层命令）──
#   只认**仓库根真实存在**的清单文件；不认识的一律不加（保守）。
_MANIFEST_TOOLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Cargo.toml", ("cargo",)),
    ("package.json", ("npm", "npx", "yarn", "pnpm", "node")),
    ("pyproject.toml", ("python", "python3", "pytest", "uv", "poetry")),
    ("setup.py", ("python", "python3", "pytest")),
    ("requirements.txt", ("python", "python3", "pytest", "pip")),
    ("go.mod", ("go",)),
    ("pom.xml", ("mvn",)),
    ("build.gradle", ("gradle", "./gradlew")),
    ("build.gradle.kts", ("gradle", "./gradlew")),
    ("Makefile", ("make",)),
    ("CMakeLists.txt", ("cmake", "ctest", "make")),
    ("tsconfig.json", ("npx", "node", "tsc")),
    ("Gemfile", ("bundle", "rake", "ruby")),
    ("composer.json", ("composer", "php")),
    ("mix.exs", ("mix", "elixir")),
    ("pubspec.yaml", ("flutter", "dart")),
    # ★★★★★ 2026-11-01 真缺陷（本工程自证时抓到）：
    #   本库**自带** Lean 原生判据门（`kernel_statement` / `axiom_footprint`，
    #   `tests/lean_native.py` 16/16），但白名单的自动识别表里**没有 Lean** ——
    #   于是在一个真 Lean 工程上跑自己的门，被自己的沙箱拒：
    #       `命令安全闸拒绝：门#0.cmd 顶层命令不被白名单允许：'lake'`
    #   ⇒ **产品测不了自己的招牌能力**。与当年 `cargo` 那次（下方 53 行注释）同型。
    #   收的是"解释器/编译器/运行器"这一类，不放开任意可执行：强度不降。
    ("lakefile.lean", ("lake", "lean", "elan")),
    ("lakefile.toml", ("lake", "lean", "elan")),
    ("lean-toolchain", ("lean", "lake", "elan")),
)


def _detected_build_tools(根们: tuple[Path, ...] = ()) -> set:
    """扫**被判定工程的根**（及档案根）的清单文件 → 该工程自带的构建工具。

    ★ 这是**读工程的事实**，不是读模型的意图 ⇒ 不削弱沙箱。
    ★ 失败/未识别一律返回空集（保守：宁可不加，也不放开）。

    ★★★★★ 2026-11-01 真缺陷（第二处）：原来**只**扫档案根 `profile.repo_root`。
    于是 `orch_run(repo=<另一个工程>)` 时，识别的是**档案那个工程**的清单 ——
    被判定工程是 Lean（`lakefile.lean`）、档案工程是 Python（`pyproject.toml`），
    结果 `lake` 永远识别不到。判据读错了对象，比没有判据更坏。
    现在：先扫**调用方给的根**（工作区根 / 被判定仓根），再兜底扫档案根。
    """
    候选: list[Path] = [Path(p) for p in 根们 if p is not None]
    try:
        prof = prof_mod.get_profile()
        候选.append(Path(prof.repo_root))
    except Exception:      # noqa: BLE001 档案异常时不猜（但用户给的根照样扫）
        pass
    命中: set = set()
    见过: set = set()
    for root in 候选:
        try:
            rp = root.resolve()
        except OSError:
            continue
        if rp in 见过 or not rp.is_dir():
            continue
        见过.add(rp)
        for 清单, 工具 in _MANIFEST_TOOLS:
            try:
                if (rp / 清单).is_file():
                    命中 |= set(工具)
            except OSError:
                continue
    return 命中


def _cmd_whitelist(根们: tuple[Path, ...] = ()) -> set:
    """顶层命令白名单 = 基础集 ∪ **工程自带构建工具**（自动识别）∪ 档案追加。

    ★ 三段来源，全部与"模型想跑什么"无关：
      ① 基础集 —— 语言无关的解释器/运行器；
      ② **自动识别** —— 由仓库清单文件决定（`Cargo.toml` ⇒ `cargo`）；
      ③ 档案 `gate_cmd_whitelist` —— 用户显式追加（仍保留，优先级最高）。
    """
    extra = prof_mod.get_profile().raw.get("gate_cmd_whitelist") or []
    return (set(_CMD_TOP_WHITELIST_BASE)
            | _detected_build_tools(根们)
            | {str(x) for x in extra})


# ★★ 2026-10-14 修（实测根因，非推测）：
#   原值 600s **使编译/测试类门在本工程里恒不可满足**。实测证据：
#     · 本工程 `cargo test --lib` 在**原始树上**实测跑 **>40 分钟**（未跑完）；
#     · 真跑三个候选窗的门耗时 = **11.0 / 10.7 / 10.4 分钟** —— 每个窗都**精确
#       撞在 600s 上限**上（600s + 开销）；
#     · ⇒ `cargo_test_lib` 每一轮都 False，任务**构造上不可能收敛**。
#   这是本库统一诊断的又一例：**判据读错了东西** —— 它读的是"命令有没有在
#   600 秒内跑完"，而不是"测试有没有过"。
#   修法不是简单抬大（那样真挂起会白等一小时），而是**两条一起**：
#     ① 上限抬到能容纳真实构建/测试时长（1 小时，仍有界）；
#     ② 编译类门缺省加 **stall 检测**（见 `_DEFAULT_COMPILE_STALL`）：
#        长时间**无任何输出**才判挂起 —— 跑得久但有进展的命令不再被误杀。
_MAX_TIMEOUT = 3600         # 每个门默认/上限超时（秒）
# 编译/测试类门缺省「无输出即挂起」阈值（秒）。判据是**有没有进展**，不是总时长。
_DEFAULT_COMPILE_STALL = 900
_MAX_GATES = 40             # 单目标门集上限，防模型塞满跑死（四域长线目标常 30~40 门；
                            # 超限由 normalize_gates 语义等价合并+关键门保护裁剪消化）


def _gate_patterns(g: dict) -> list:
    """提取 grep 类门的匹配 pattern 列表（兼容 patterns 数组与 pattern 单串）。"""
    p = g.get("patterns")
    if isinstance(p, list) and p:
        return [x for x in p if isinstance(x, str) and str(x).strip()]
    if "pattern" in g and str(g.get("pattern") or "").strip():
        return [g["pattern"]]
    return []


def normalize_gates(spec: dict, cap: int = _MAX_GATES) -> tuple[dict, dict]:
    """门集归一化（纯机器、保判据语义）：模型塞门超配额时机器消化，不整体拒死。

    两级纯机器操作，判定层必然性仍由「保留门全绿」承担，不放松判据：
      1) 同文件同型 grep 门**语义等价合并**：grep_forbidden 取并（任一命中即红）、
         grep_required 取全含（全部字面必须出现）——与逐门分别跑完全等价；
      2) 仍超配额 → 逐文件公平裁剪冗余门（保留行为门 compile/scan_output 优先，
         再 grep_required，最后裁 grep_forbidden；独立门优先，顶层默认门兜底）。
    返回 (规约深拷贝, notes)；notes 记 merged/dropped 明细，供回执透明披露。
    极端（文件过多仍超配额且无法再裁）不强压到假完成：留给 sufficiency_bound 保守拒。
    """
    import copy
    spec = copy.deepcopy(spec)
    notes: dict = {"merged": 0, "dropped": 0, "conflict_dropped": 0, "detail": []}
    # ★★★★★ R215：**手段门不许当判据。**
    #   真证据（第 115 次真跑第 4 轮，门真值原文）：
    #     `算子选择器.rs@算子选择器_已引入定列且保留样本列表构造=True`
    #     `（全库）@cargo_test_lib_no_run_exit0=False`
    #     `（全库）@构建输出不含定列解析错误与类型推断错误=False`
    #   ★ 模型为了过自己写的这条门，去「给 `定列` 补导入 + 保留 `定列::新()`」，
    #     而该字段类型是 `Vec<态射样本>` ⇒ 补了导入也永远编译不过（E0308）。
    #     ⇒ **自撰的门把错误的解法方向固化了**：门绿、真门永远红。
    #   ★ 这是 Goodhart 的教科书形态：模型优化的是**它自己写的判据**，
    #     而判据要求的是「**保留某个写法**」—— 把手段当成了目的。
    #   ⇒ 口径：门里出现「保留 / 维持 / 已引入 / 仍在 / 不许删 / 保持不变」
    #     这类**以现状为达标条件**的门 ⇒ 它不是验收判据，**剔掉**（回执披露）。
    #     保留门永远不许把门集剔空（至少留一条）。
    # ★★★★★ R218：**R215 只认中文，真跑里漏了英文。**
    #   真证据（第 118 次真跑，门真值原文）：
    #     `示例工程/基础集/算子选择器.rs@selector_keeps_dinglie_call=True`
    #     `示例工程/基础集/时序拓扑注入.rs@timing_topology_keeps_continue_fn=True`
    #   而「历史偏差密度」还把 `dinglie_new_call_preserved×5` 列为**优先解决**的缺口
    #   ⇒ 机器在**催模型去保留那个坏调用**（那个调用正是 E0433 的源头）。
    #   ★ 这是 R215 的同一个病：**手段门当判据**。只不过规划器用英文写门名，
    #     而我的词表只有中文 ⇒ 过滤形同虚设（中文跑得过、真跑拦不住）。
    #   ⇒ 词表必须**中英双语**，且 `keep/preserve/retain/remain/unchanged` 全覆盖。
    _手段词215 = ("保留", "维持", "已引入", "仍在", "不许删", "不要删",
                  "不得删除", "保持不变", "仍然存在",
                  "keep", "keeps", "keeping", "preserve", "preserved",
                  "preserves", "retain", "retained", "remain", "remains",
                  "unchanged", "not_removed", "notremoved", "still_present",
                  "imported_from")
    _top215 = spec.get("gates")
    if isinstance(_top215, list) and len(_top215) > 1:
        def _门文215(_g) -> str:
            if not isinstance(_g, dict):
                return str(_g)
            _段 = []
            for _k in ("name", "desc", "title", "id", "gate", "note"):
                if _g.get(_k):
                    _段.append(str(_g[_k]))
            return " ".join(_段)
        _留215, _剔215 = [], []
        for _g in _top215:
            _文 = _门文215(_g)
            if any(_w in _文 for _w in _手段词215) and len(_留215) + 1 < len(_top215):
                _剔215.append(_文[:60])
            else:
                _留215.append(_g)
        if _剔215:
            spec["gates"] = _留215
            notes["procedural_dropped"] = len(_剔215)
            notes.setdefault("detail", []).append(
                {"procedural_dropped": _剔215[:6]})

    def _pats(g: dict) -> list:
        p = g.get("patterns")
        if isinstance(p, list) and p:
            return p
        if "pattern" in g and str(g.get("pattern") or "").strip():
            return [g["pattern"]]
        return []

    def _resolve_conflicts(gates):
        """③ 门集自洽消解（纯机器）：同一门集内 grep_required 与 grep_forbidden 若把
        同一字面 pattern 同时当"必须含"与"禁止含" → 不可能同时满足（执行永远差一门红）。
        删除等价于必需标记的禁词 pattern（保留必需标记），返回 (门集, 删禁词数)。"""
        required = set()
        for g in gates:
            if g.get("type") == "grep_required":
                required |= set(_pats(g))
        if not required:
            return gates, 0
        dropped = 0
        out: list[dict] = []
        for g in gates:
            if g.get("type") != "grep_forbidden":
                out.append(g)
                continue
            pats = _pats(g)
            keep = [p for p in pats if p not in required]
            if len(keep) == len(pats):
                out.append(g)
            elif keep:
                g = dict(g)
                if "patterns" in g:
                    g["patterns"] = keep
                else:
                    g["pattern"] = keep[0]
                out.append(g)
                dropped += len(pats) - len(keep)
            else:
                dropped += len(pats)
        return out, dropped

    def _reduce_text_scans(gates):
        """④ 门集收敛归约（可复用规则）：把『同一被测文本的偏扫描必需标记』折叠为
        单一 whole_reading 定位实例（读整体一次判 O(1)）。机制不变、换域只换输入字节：
        判定层仍是机器硬判定，只是把逐 pattern 偏扫描并成一次读 + 一次稳定归一化比对。
        归约**保持判定语义**：定位键在 whole_reading 内同样历经稳定规范化（去空白/NFKC），
        故"结构键是否存在"对格式免疫——与逐字面排版敏感的偏扫描相比，判定层不变、更稳。"""
        out: list[dict] = []
        for g in gates:
            if g.get("type") != "grep_required":
                out.append(g)
                continue
            pats = [p for p in _pats(g) if isinstance(p, str) and p]
            if not pats:
                out.append(g)          # 无必需标记 → 保原门，不强行改写
                continue
            g = dict(g)
            g["type"] = "whole_reading"
            g["mode"] = "contains"
            g["must_contain"] = pats
            g.pop("patterns", None)
            g.pop("pattern", None)
            out.append(g)
        return out

    def _只留字典(seq, 层: str) -> list:
        """★★★★★ R149（真跑抓出的第 62 层 · 2026-10-24 · 第三十六次端到端）：
        **模型给错了格式，不该让产品崩在 `AttributeError` 上。**

        真证据（第 36 次真跑，轮 4，整趟死在这里）：
          `AttributeError: 'str' object has no attribute 'get'`
          `  gate_derive.py:228 in _merge → key = (g.get("type"), …)`
        ★ 主心骨给的 `gates` 里混进了**字符串**（本该是 dict）。
          产品没有校验，直接 `.get()` ⇒ 崩 ⇒ 重规划路径炸 ⇒ 整趟 exit 1。

        ★★ 这是「声明了与被使用之间断了」的第 N 次：
          模型**声明**了门，产品**使用**时才发现它不是门。
          中间没有一道「它到底是不是门」的检查。

        ★★★ 处置口径（三条同时成立，缺一条就是另一种不诚实）：
          ① **不能崩** —— 一条坏门不得杀掉整趟无人值守；
          ② **不能静默丢掉** —— 丢了却不说 = 主心骨以为有这条判据，
             实际没有 ⇒ 门全绿也不代表它被验过（R146 的同一个病）；
          ③ **要计入 notes** —— 让报表和主心骨都看得见「有几条不是门」。
        """
        好, 坏 = [], 0
        for x in (seq or []):
            if isinstance(x, dict):
                好.append(x)
            else:
                坏 += 1
        if 坏:
            notes["dropped_bad_gate"] = notes.get("dropped_bad_gate", 0) + 坏
            notes["detail"].append(
                f"★ 丢弃 {坏} 条**不是 dict 的门**（{层}）"
                f"⇒ 它们无法被判据门执行器读取；"
                f"这不是「没有这条判据」，是「这条判据写坏了」")
        return 好

    def _merge(gates: list[dict]) -> list[dict]:
        gates = _只留字典(gates, "门集")
        out: list[dict] = []
        buckets: dict[tuple, list[dict]] = {}
        for g in gates:
            key = (g.get("type"), str(g.get("in") or ""))
            if g.get("type") in ("grep_forbidden", "grep_required"):
                buckets.setdefault(key, []).append(g)
            else:
                out.append(g)
        for _key, gs in buckets.items():
            if len(gs) == 1:
                out.append(gs[0])
                continue
            merged = dict(gs[0])
            merged["patterns"] = [p for g in gs for p in _pats(g)]
            merged["name"] = f"{gs[0].get('name', gs[0]['type'])}_merge{len(gs)}"
            merged.pop("pattern", None)
            out.append(merged)
            notes["merged"] += len(gs) - 1
        return out

    def _trim(gates: list[dict], budget: int) -> list[dict]:
        if len(gates) <= budget:
            return gates
        # 关键判据门保护（随预算收紧）：优先保编译行为门（判定层必然），
        # 再保禁词门（质量护栏）、必需标记门（验收标记）——已保代表不裁
        order = {"compile": 0, "grep_forbidden": 1, "grep_required": 2,
                 "scan_output": 3}
        forced: list[dict] = []
        for t in ("compile", "grep_forbidden", "grep_required"):
            if len(forced) >= budget:
                break
            for g in gates:
                if g.get("type") == t:
                    forced.append(g)
                    break
        rest = [g for g in gates if g not in forced]
        budget_rest = max(0, budget - len(forced))
        rest = sorted(rest, key=lambda g: (order.get(g.get("type"), 4),
                                           g.get("name", "")))
        dropped = rest[budget_rest:]
        notes["dropped"] += len(dropped)
        notes["detail"].append(
            "裁" + str(len(dropped)) + "门: " + "、".join(
                g.get("name", g["type"]) for g in dropped))
        return forced + rest[:budget_rest]

    # ① 顶层默认门 + 各文件独立门分别做同型语义等价合并
    notes.setdefault("dropped_bad_gate", 0)
    gates = spec.get("gates")
    if isinstance(gates, list) and gates:
        spec["gates"] = _merge(gates)
        spec["gates"], d_cl = _resolve_conflicts(spec["gates"])
        notes["conflict_dropped"] += d_cl
        spec["gates"] = _reduce_text_scans(spec["gates"])
    for d in spec.get("deliverables") or []:
        g = d.get("gates")
        if isinstance(g, list) and g:
            d["gates"] = _merge(g)
            d["gates"], d_cl = _resolve_conflicts(d["gates"])
            notes["conflict_dropped"] += d_cl
            d["gates"] = _reduce_text_scans(d["gates"])
    if notes["conflict_dropped"]:
        notes["detail"].append(
            "消解门集自洽冲突删" + str(notes["conflict_dropped"]) + "条禁词pattern")

    # ② 总门数（与 _adopt_spec 的 all_gates 同口径）超配额 → 公平裁剪
    files = spec.get("deliverables") or []
    if not files:
        total = len(spec.get("gates") or [])
        if total > cap:
            spec["gates"] = _trim(spec["gates"], cap)
        return spec, notes
    total = sum(len(d.get("gates") or spec["gates"]) for d in files)
    if total <= cap:
        return spec, notes
    it = 0
    while total > cap and it < 10:
        it += 1
        indep = [d for d in files if d.get("gates")]
        if indep:
            weight = sum(len(d["gates"]) for d in indep)
            cut = False
            for d in indep:
                budget = max(1, int(cap * len(d["gates"]) / weight))
                if len(d["gates"]) > budget:
                    d["gates"] = _trim(d["gates"], budget)
                    cut = True
            if not cut:
                break   # 独立门已到最小，无法再按比例削
        else:
            spec["gates"] = _trim(spec["gates"], cap)
            break
        total = sum(len(d.get("gates") or spec["gates"]) for d in files)
    return spec, notes


def gates_consistent(gates) -> tuple[bool, list[str]]:
    """机器判门集自洽（非破坏）：同一门集内 grep_required 的必需标记若被同门集内
    grep_forbidden 同时禁止 → 任何实现都不可能同时满足（执行永远差一门红）。

    判定层必需的自洽条件：若自相矛盾，门全绿不可达，编排器须在采纳层消解或拒规约。"""
    required = set()
    for g in gates:
        if g.get("type") == "grep_required":
            required |= set(_gate_patterns(g))
    if not required:
        return True, []
    reasons: list[str] = []
    for i, g in enumerate(gates):
        if g.get("type") != "grep_forbidden":
            continue
        for p in _gate_patterns(g):
            if p in required:
                reasons.append(
                    f"门#{i}({g.get('name', g.get('type'))}) 禁词 pattern 与必需标记等价，"
                    f"不可能同时满足：{p[:40]!r}")
    return not reasons, reasons


def _trivial_cmd(g: dict) -> bool:
    """门是否"没有真实执行内容"。

    ★ 澄清（2026-10-12）：本判据对**文本门**（grep_forbidden/grep_required/
    whole_reading）返回 True —— 这不是说文本门没用，而是说**文本门不演练产物行为**。
    一个只读文本的门集能证明"文件里出现了某段字"，不能证明"程序真的跑对了"。
    故它只能作旁证，不能单独支撑"判定层必然性"（见 sufficiency_bound 的 behavioral 要求）。

    ⚠ 注意：此前 `_normalize_spec` 会给文本门硬塞 `cmd: ["true"]`，于是文本门在此处
    被当作"平凡 compile 门"，报错信息误诊为"自嗨绿"，掩盖了真实原因（缺行为门）。
    该注入已撤除，现文本门如实按"无 cmd"归类。"""
    cmd = g.get("cmd") or []
    c = [str(x).strip().lower() for x in cmd if str(x).strip()]
    return c == ["true"] or not c


def 去重门(gates) -> list:
    """按**结构身份**去重门集（同一判据只算一条）。

    ★★★★★ R35（真跑抓出的缺口 · 2026-10-15 · Rust 工程 `example-project`）：

      规划器看见机器探针报出的 **18 处违规散在 7 个文件**，于是**按文件**列了
      7 条交付物，每条都挂上同一组共享门（`_parse_deliverables` 的设计：
      每文件必有门可判）。于是 `all_gates` 是**笛卡尔积** ——
      7 文件 × 6 门 = **42** ⇒ 撞上 `_MAX_GATES = 40` ⇒
          `判定层必然性不成立：('门集超限（>40）：防模型塞满跑死',)`
      ⇒ **规划失败，整趟没跑起来**。

      ★ 而 `normalize_gates`（门集归一化）读的是 `spec["gates"]`，
        也就是**去重后的 6 条**，回执里如实报 `merged: 0, dropped: 0`
        —— 同一趟跑里，**同一件事有两个不同的尺子**：
        归一化说"6 条，没问题"，必然性判定说"42 条，超限"。
        ⇒ 这正是本库反复踩的病：**口径分叉**（同一问题两处各算一遍）。

      ★ 那条上限的**本意**写得很清楚：「防模型**塞满**跑死」
        —— 它防的是模型**刷一堆不同的门**，不是"一个任务有很多文件"。
        ⇒ 修法是**统一尺子**（按结构身份去重），**不是放宽上限**：
          真刷 50 条**不同**门的模型照样撞限（齿探针钉住这条）。

    ★ 与 R31 同一条根：**"多文件"是通用工程开发的常态**，
      而本工具此前只在单文件样例上跑通 ⇒ 多文件路上的断路没被发现。
    """
    见: dict[str, dict] = {}
    for g in (gates or ()):
        if not isinstance(g, dict):
            continue
        try:
            # 结构身份：只取**判据语义**，不含 cwd/timeout 这类执行参数
            # （同一条判据换个超时不算两条）。
            键 = json.dumps(
                {k: g.get(k) for k in
                 ("type", "name", "cmd", "in", "pattern", "patterns",
                  "must_contain", "must_not", "mode", "expect_exit", "file")},
                ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            键 = repr(g)
        if 键 not in 见:
            见[键] = dict(g)
    return list(见.values())


def sufficiency_bound(gates, spec: dict,
                      逐件已覆盖: bool = False) -> tuple[bool, list[str]]:
    """判门集是否足以支撑判定层必然性。

    必要条件（全满足才判 decision_necessary=True）：
      - 门集非空、数量 ≤ 上限（防塞满/跑死）
      - 至少有 1 个非平凡门（cmd 不是 ["true"]/空——否则纯自嗨，全绿无意义）
      - 至少有 1 个真实机器[行为]门（compile/scan_output 且 cmd 引用 {file} 或
        以模块执行/断言——门切实演练产物，而不是只有文本 grep）
      - 每个验收叙述都要有“它可被机器检验”的落点：至少存在一个 compile 门
        提供真值（行为断言/编译），软验收不能单独兜底
    全部满足 → 判定层必然性成立；否则给 reason 并保守判 decision_necessary=False
    （编排器据此拒绝把完成当必然，对软/空/含糊一律红）。"""
    reasons: list[str] = []
    # ★★★★★ R35：**统一尺子** —— 上限数的是**不同判据**，不是
    #   (文件 × 门) 的笛卡尔积。`normalize_gates` 早就用去重口径，
    #   这里必须同口径，否则同一趟跑里两个尺子各说一套（口径分叉）。
    #   ★ 这不放松判据：真刷 50 条**不同**门的模型照样撞限。
    gates = 去重门(gates)
    if gate_validate(gates):
        reasons.append("存在非法判据门（gate_spec.validate 拒绝）")
        return False, reasons
    if not gates:
        reasons.append("门集为空：无任何机器判据，必红")
        return False, reasons
    if len(gates) > _MAX_GATES:
        reasons.append(f"门集超限（>{_MAX_GATES}）：防模型塞满跑死")
        return False, reasons

    non_trivial = [g for g in gates if not _trivial_cmd(g)]
    if not non_trivial:
        reasons.append("全部门为平凡/空 cmd（自嗨绿）：无真实机器检查，必红")

    consistent, c_reasons = gates_consistent(gates)
    if not consistent:
        reasons.append("门集自相矛盾（必需标记与禁词冲突，全绿不可达）：")
        reasons += c_reasons

    def _behavioral(g: dict) -> bool:
        if g.get("type") not in ("compile", "scan_output"):
            return False   # grep 类不演练产物行为
        blob = json.dumps(g, ensure_ascii=False)
        if "{file}" in blob or "{work}" in blob:
            return True    # 直接引用待判产物
        cmd = g.get("cmd") or []
        return len([c for c in cmd if str(c).strip()]) >= 2  # -c/-m 实调用非裸解释器

    behavioral = [g for g in gates if _behavioral(g)]
    if not behavioral:
        reasons.append("缺少真实机器行为门：验收须由编译/行为断言门兜底（非仅文本）")
        # ★★★★★ R133（真跑抓出的第 46 层 · 2026-10-22 · 第二十八次端到端）：
        #   **机器说了「不成立」，却没说「哪一条不成立」。**
        #
        #   真证据：重规划连续 3 次被拒，最后一次原文
        #     `判定层必然性不成立：门绿不必然等于目标达成`
        #   而回喂的话是「★ 请把软/空/含糊的门换成**有判别力**的门
        #     （修复前为假、修复后为真）」——
        #   ★ 模型手里有 9 条门，**不知道是哪一条不合格**，只能**整体重写**，
        #     于是下一次又踩另一条 ⇒ 3 次全败 ⇒ 诚实停机。
        #
        #   ★ 这与 R130（门类型不给清单）是同一族的下一步：
        #     R130 是「合法集合没说出来」，R133 是「**哪一条不合格没说出来**」。
        #     一个工程师会说：「第 4 条门 `xxx` 是 grep，它证明不了行为；
        #     把它换成跑 `{file}` 的断言门。」机器现在说不出这句话。
        #
        #   ★ 修法：**逐门列出它为什么不行为**（不是笼统的"缺行为门"）。
        #     ★ 只陈述事实（类型是什么、有没有引用产物），**不给它改**。
        _非行133 = []
        for _g133 in gates:
            if _behavioral(_g133):
                continue
            _t133 = _g133.get("type")
            _n133 = str(_g133.get("name") or _t133)[:40]
            _b133 = json.dumps(_g133, ensure_ascii=False)
            if _t133 not in ("compile", "scan_output"):
                _为何133 = f"类型是 `{_t133}`（grep/文本门只读源码，不演练产物行为）"
            elif "{file}" not in _b133 and "{work}" not in _b133:
                _为何133 = ("是 compile/scan_output 但**没引用** `{file}`/`{work}` "
                            "⇒ 它跑的是固定命令，与待判产物无关")
            else:
                _cmd133 = _g133.get("cmd") or []
                _为何133 = (f"cmd 只有 {len([c for c in _cmd133 if str(c).strip()])} "
                            f"个非空元素（裸解释器不算实调用）")
            _非行133.append(f"  · `{_n133}`：{_为何133}")
        if _非行133:
            reasons.append(
                "**逐门诊断（机器实测，这些门证明不了「做到了」）**：\n"
                + "\n".join(_非行133[:10])
                + "\n  ★ 改法：至少留一条**跑产物本身**的 compile/scan_output 门"
                  "（cmd 里带 `{file}` 或 `{work}`），"
                  "让它「修复前为假、修复后为真」。"
                  "**不要**把全部门重写 —— 只换掉上面列出的这些。")

    # ★★★★★ R104（真跑抓出的第 18 层 · 2026-10-19 · 第七次端到端）：
    #   **门集能跑 ≠ 门集能判目标。**
    #
    #   真跑证据（目标「把无界键接进生产决策链」，5 个交付物）：
    #     30 条门其实是**同 3 条复制 10 遍**：
    #       · cargo_test_lib_build             （全库能编译）
    #       · 模块甲_无界键**定义仍在**      （must_contain: "pub fn 学模式_广域"）
    #       · 造变换_施填规仍在                （must_contain: "施填规"）
    #     ★ 三条**全是 regression guard**（判「没破坏」），
    #       而目标是「把无界键**接进**决策链」—— 要的是**调用**，不是定义。
    #     ★ 而且 5 个交付物里 **3 个零门**（arc评测 / 点云评测 / 评测口径）。
    #
    #   于是：门全绿 = 没破坏任何东西 = **必然发生**，与目标无关。
    #   而 `bound` 照样声称「**门全绿 ⇒ 目标达成**」，`Δ=∅` 照样判「**P 达成**」。
    #   ⇒ 我的编排器犯了和它要改造的样本戊**一模一样**的错：
    #     用无判别力的判据声称达成（判据5 恒 1.000 / 判据6 恒 1000 / 本处恒 True）。
    #
    #   ★ 本函数能判的那一半：**每个交付物至少要被一条门提到**。
    #     判不了的那一半（门是否判「做到」而非「没破坏」）只能由主心骨判 ——
    #     所以提示里必须逼它自问（见 `chief._默认提示`）。
    _交104 = []
    for _d in (spec.get("deliverables") or []):
        _pp = _d.get("path") if isinstance(_d, dict) else _d
        if _pp:
            _交104.append(re.sub(r"[：:]\d+(?:[：:]\d+)?\s*$", "",
                                 str(_pp).strip()))
    # ★★ 第一版漏了一条：`{file}` / `{work}` 门**天生覆盖所有交付物**
    #   （它就是「拿待判产物本身去跑」）。字面搜路径会把它们判成未覆盖 ⇒ **假红**。
    #   ★ 自检当场抓到 6 条红（R36/R37/dry-machine 的夹具都用 `{file}`）。
    #   ⇒ 有通用门时**不施加**覆盖要求。
    _通用104 = any(("{file}" in json.dumps(g, ensure_ascii=False)
                    or "{work}" in json.dumps(g, ensure_ascii=False))
                   for g in gates)
    # ★★★★★ R170（2026-10-27）：**机器逐件门也算覆盖。**（第 82 层）
    #
    # 【真事故 —— 第 52 次真跑，连续 3 次重规划后按设计停机】
    #   回喂文本原话：
    #     `交付物**没有判据门覆盖**：['bin/TS827_结构自生成.rs',
    #       'src/模块甲/持久化入口.rs'] ⇒ 这些文件绿不绿都无所谓`
    #   ⇒ 3 次全撞同一条 ⇒ `重规划连续 3 次仍可纠正性问题未改对 ⇒ 诚实停机`。
    #
    # 【为什么这是误判（而不是模型没听话）】
    #   编排器**本来就会**对每个交付物**单独跑逐件门**——
    #   同一趟日志里明明写着：
    #     `✓ src/模块甲/持久化入口.rs：该文件主体的门 2 → 0（严格变小）⇒ 保留`
    #     `✗ src/模块甲/mod.rs：违规点变多（0 → 1）⇒ 只回退这一个文件`
    #   ⇒ **文件绿不绿完全有人管**，而且管它的正是判据门。
    #   ★ 本函数只看 `spec` 里**主心骨给的门集**，看不见「机器自己的逐件门」
    #     ⇒ 「手里明明有覆盖信息，却判成没有」。
    #     这是那一族（第 76/79/80 层同型）：**手里那个信息不是判据要用的那个信息**。
    #
    # 【口径】
    #   · 调用方显式声明 `逐件已覆盖=True`（= 它确实会对每个交付物跑逐件门）时，
    #     「计划门没提到该文件」**不再构成拒收理由**；
    #   · 默认仍是 `False` ⇒ **老判据原样保留**（R104/R105 的齿照样咬得住），
    #     所以这不是「把检查删掉」，是「知道机器有逐件门时才不重复要求」。
    #   · 通道唯一：要不要逐件，由调用方**显式**说，不由本函数猜。
    if _交104 and not _通用104 and not 逐件已覆盖:
        _门串104 = json.dumps(gates, ensure_ascii=False)
        _未覆104 = [x for x in dict.fromkeys(_交104) if x and x not in _门串104]
        if _未覆104:
            reasons.append(
                f"交付物**没有判据门覆盖**：{_未覆104} ⇒ "
                f"这些文件绿不绿**都无所谓**，门全绿不蕴含它们被做到")

    if not non_trivial:
        return False, reasons            # 硬拒：连一个真检查都没有

    enough = bool(non_trivial) and bool(behavioral) and not reasons[:2]
    if not enough:
        reasons.append("判定层必然性不成立：门绿不必然等于目标达成")
    return enough, reasons


def check_cmd_safety(gates, root: Path, repo_root: Path | None = None) -> tuple[bool, list[str]]:
    """沙箱：命令白名单 + cwd 不越出允许根（工作区/主树）+ 禁跳越。

    用列表(subprocess，无 shell)执行，天然防注入；但仍限定：
      - 每个 compile/scan_output 门 cmd[0] 必须在白名单（模型不能塞任意可执行）
      - 每个门命中的 cwd（含 {work}/{repo} 解析后）必须落在允许根内
        （root 工作区根 + repo_root 主树根，主树工程任务 {repo} 用）
      - 参数/路径含 ".." 跳越 → 违规；引用 {repo} 但未给 repo_root → 违规
    违规任何一个 → 该门集不可用，编排器必须拒绝（不把不可信通道当判据门）。"""
    violations: list[str] = []
    root = Path(root).resolve()
    allowed = [root]
    repo_str: str | None = None
    if repo_root is not None:
        rp = Path(repo_root).resolve()
        allowed.append(rp)
        repo_str = str(rp)
    # ★ 白名单按**被判定工程的根**算（而不是只按档案根），且整次检查只算一次：
    #   每个门重扫一遍目录树既慢又让"用哪个根"这件事散落在多处。
    _白 = {str(w).lower() for w in _cmd_whitelist(tuple(Path(a) for a in allowed))}
    # ★ 自证可信根：编排器自己的解释器 + 安装根。
    #   理由：这两样是"我们装的、我们起的"，不是模型塞进来的；而 `_run` 本来就会把
    #   裸 `python` 自动换成 `sys.executable` ⇒ 判据必须承认这件事，否则
    #   「最正当的写法」被判成越界（无效拒绝，实测 7.7% 的误伤正来自这里）。
    _自证: list[Path] = []
    try:
        _自证.append(Path(__import__("sys").executable).resolve().parent)
    except OSError:
        pass
    try:
        _自证.append(Path(prof_mod.TOOL_ROOT).resolve())
    except Exception:      # noqa: BLE001 拿不到安装根就算了（不许因此改判据）
        pass
    for i, g in enumerate(gates):
        strs: list[str] = []
        for k in ("cmd", "in", "src"):
            v = g.get(k)
            if isinstance(v, str):
                strs.append(v)
            elif isinstance(v, list):
                strs += [x for x in v if isinstance(x, str)]
        for fld in ("pattern", "patterns", "must_not", "must_contain",
                    "expect", "expect_sig"):
            v = g.get(fld)
            if isinstance(v, str):
                strs.append(v)
            elif isinstance(v, list):
                strs += [x for x in v if isinstance(x, str)]
        for s in strs:
            if "{repo}" in s and repo_str is None:
                violations.append(f"门#{i} 引用 {{repo}} 但未给 repo_root")
            if ".." in s and "{work}" not in s and "{repo}" not in s:
                violations.append(f"门#{i} 参数含裸跳越：{s[:40]}")
        # ★ 2026-11-01：命令白名单**跟着门型走**。新增的三类门（`test_counts` /
        #   `differential` / `property`）同样会**真跑命令**，此前不在此列 ⇒ 它们可以
        #   绕过顶层命令白名单：那是"新门型开出一个旧沙箱没覆盖的口子"。
        if g.get("type") not in ("compile", "scan_output", "test_counts",
                                 "differential", "property"):
            continue
        # `differential` 有两处命令入口（被测 + 参照），两处都要过白名单。
        命令们 = [("cmd", g.get("cmd") or []), ("ref", g.get("ref") or [])]
        for _键, cmd in 命令们:
            head = str(cmd[0]).strip() if cmd else ""
            if not head:
                continue
            if head not in _白:
                # ★★★★★ 修复 R9（**门比合法用法更窄**）：允许**带路径**的白名单程序，
                #   但必须落在允许根内。
                #
                #   ★ 原实现只认裸名（`python`），传绝对路径一律拒 ⇒
                #     **跑本工程自己的 venv 解释器**（最常用、最正当的用法）
                #     被无理由拒绝：实测
                #     `顶层命令不被白名单允许：'…\\agentscope_orchestrator\\.venv\\Scripts\\python.exe'`。
                #     后果与"空门集被拒"同型：**逼调用方绕路或写假门**。
                #
                #   ★ 安全性质**不降低**，判的是两件事（缺一不可）：
                #     ① 程序名（去 `.exe` 去目录）在白名单里 —— 不能塞任意可执行；
                #     ② 若给了路径，解析后必须落在允许根内
                #        —— 不能指向允许根外的同名的恶意程序。
                _名 = Path(head).name
                _干 = Path(_名).stem.lower()
                if _干 not in _白:
                    violations.append(f"门#{i}.{_键} 顶层命令不被白名单允许：{head!r}")
                else:
                    _hp = Path(head)
                    if _hp.is_absolute():
                        try:
                            _根们 = list(allowed) + _自证
                            if not any(_hp.resolve().is_relative_to(r) for r in _根们):
                                violations.append(
                                    f"门#{i}.{_键} 可执行越出允许根：{_hp}")
                        except OSError:
                            violations.append(f"门#{i}.{_键} 可执行路径解析失败：{_hp}")
        for key in ("cwd",):
            v = g.get(key)
            if not v:
                continue
            try:
                s = str(v).replace("{work}", str(root)).replace("{file}", "x")
                if "{repo}" in s:
                    if repo_str is None:
                        violations.append(f"门#{i} cwd 引用 {{repo}} 但未给 repo_root：{v!r}")
                        continue
                    s = s.replace("{repo}", repo_str)
                p = Path(s)
            except Exception:  # noqa: BLE001 - 占位符解析失败算违规
                violations.append(f"门#{i} cwd 解析失败：{v!r}")
                continue
            if p.is_absolute() and not any(
                    p.resolve().is_relative_to(r) for r in allowed):
                violations.append(f"门#{i} cwd 越出允许根：{p}")
            if ".." in p.parts:
                violations.append(f"门#{i} cwd 含跳越：{p}")
    return not violations, violations


def bound_report(gates, spec: dict, root: Path, repo_root: Path | None = None,
                 逐件已覆盖: bool = False) -> dict:
    """给编排器一张「边界卡」：哪里是必然、哪里只是概率，运行时可查。

    ★ R170：`逐件已覆盖=True` ⇒ 调用方会对**每个交付物**单独跑逐件门，
      此时不再因为「计划门文本里没提到该交付物」而拒收（详见 sufficiency_bound）。
    """
    dec_necessary, d_reasons = sufficiency_bound(gates, spec, 逐件已覆盖=逐件已覆盖)
    safe, s_vio = check_cmd_safety(gates, root, repo_root=repo_root)
    return {
        "decision_necessary": bool(dec_necessary and safe),
        "sufficient": dec_necessary,
        "safe": safe,
        "reasons": (*d_reasons, *(f"沙箱:{v}" for v in s_vio)),
        # ★★★★★ R104：`contract` **不得无条件声称「必然」**。
        #   真跑抓出：`sufficient` 是 False 的时候，这句照样输出
        #   「判定层必然（门全绿⇒目标达成）」—— 那是**向用户做了做不到的保证**。
        #   ⇒ 文本必须跟着 `sufficient` 走。声称什么，取决于真判出了什么。
        "contract": (
            "判定层必然（门全绿⇒目标达成，不经模型）；"
            "生产层仅概率（P(达成)<1，机器门压制虚警→0）。"
            if (dec_necessary and safe) else
            "★ 判定层**不必然**：" + ("；".join(d_reasons[:3]) or "充分性未成立")
            + " ⇒ **门全绿不等于目标达成**，只等于「这些门没红」。"
            "生产层仅概率。"
        ),
    }


# ════════════════════════════════════════════════════════════════
# 目标驱动偏差判定层（全自动工程执行器判据层）
# ── 门从 P 闭式推导、来源唯一；偏差纯结构身份；结构停机判据 ──
# 与数学模型对齐：
#   · C1 卡点＝「当前已声明门集下无锚」的存在性判断（可判定），
#        不判「所有分解下无锚」（∀ 否定，0 Classical 不可判）→ 无搜索。
#   · C2 偏差身份＝纯结构（类型+消息canonical+符号名），不含行号/偏移，
#        行号漂移不改键 ⇒ Δ⊂ 是结构包含非位置包含，防假发散。
#   · C3 偏差选择＝闭式（身份 canonical 序最小、有锚门），O(1)、确定性。
#   · C5 曾绿＝门身份→真值单点映射（每门一位，不折叠、不周期覆盖，TS376）。
#   · C6 发散＝已消偏差重现；回归＝P₂ 门变红，判据分开。
#   · 停机⟺不动点：Δ_n==Δ_{n-1}≠∅ ⇒ 非目标不动点 ⇒ 停滞交还用户。
#   全程无跨轮历史向量折叠、无数值次数上限。
# ════════════════════════════════════════════════════════════════

# 结构化 P 的检查种类 → gate_spec 门类型（闭式、确定性，不分发门漂移）
_CHECK_KIND_TO_GATE = {
    "whole_reading": "whole_reading",
    "scan_output": "scan_output",
    "compile": "compile",
}


def _canonical_msg(s: str) -> str:
    """偏差消息 canonical 化（C2）：行号/列/偏移位数→#，标点/空白归一，小写。
    行号漂移不改变身份键 → 结构身份，非位置身份。"""
    s = re.sub(r"\d+", "#", str(s))
    s = re.sub(r"[\s{}():;,<>\[\]'\"`=]", " ", s)
    return " ".join(s.split()).lower()


def deviation_identity(dev: dict) -> str:
    """纯结构偏差身份键（C2）：错误类型 + 消息 canonical + 涉及符号名(排序) + **文件**。
    不含行号/字节偏移。同一语义错误换位置 → 同一键。

    ★★★【TS-442】2026-10-14 补 **文件** 一维（**这是停机不收敛的直接成因**）：
      `anchored(dev_identity, declared_scopes)` 判锚用的是
      `any(scope in dev_identity for scope in declared_scopes)`，
      而 `declared_scopes` = 声明文件路径 ∪ 结构键标记。
      ⇒ 旧身份键**不含任何文件路径** ⇒ `anchored` 对**编译类偏差恒为 False**
        ⇒ `deviation_structure_verdict` 落到 `no_anchor`
        ⇒ **交还用户/停机**（实测真跑 #4 正是以 `no_anchor`/`stop` 收场）。
      ⇒ 这是"判据读的东西**不含判据需要的那一维**"：锚住在**文件**里，
        而身份读法把文件**丢了**。按 TS-442 `read_eq_reflects`，
        读必须保留判定所需的信息；否则"读相同"不蕴含"态相同"，
        而"读里没有锚"也不蕴含"真的无锚"。
      ★ 仍**不含行号** ⇒ C2「行号漂移不改键」不被破坏。
      ★ 无 `file` 字段的老账本记录身份键**不变**（向后兼容，不制造历史断裂）。
    """
    etype = str(dev.get("type") or "?")
    msg = _canonical_msg(dev.get("message") or "")
    syms = dev.get("symbols") or []
    base = f"{etype}::{msg}::{('|'.join(sorted(str(s) for s in syms)))}"
    f = str(dev.get("file") or "").strip().replace("\\", "/")
    return f"{base}::{f}" if f else base


def _err_path(body: str) -> str:
    """从编译错块抽**出错文件路径**（`--> path:line:col`）。

    ★【TS-442】这是"锚"住在哪里：`anchored` 用声明作用域（文件路径/结构键）
      在身份键里找锚 ⇒ 身份键必须**带上这个路径**，否则编译类偏差恒无锚。
      归一为 `/` 分隔、去掉前导 `./`，使跨平台与相对/绝对写法不制造假差异。
    """
    m = re.search(r"^\s*-->\s*(\S+?):\d+:\d+", body or "", re.MULTILINE)
    if not m:
        return ""
    p = str(m.group(1)).strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


_COMPILE_ERR_RE = re.compile(
    r"error(?:\[(?P<code>[A-Za-z]?\d{3,4})\])?\s*:\s*(?P<msg>.*?)(?:\s+--|→|\n)",
    re.IGNORECASE)

# ★★★【TS-442 / TS-430 relocate】偏差身份的**无损读**（2026-10-14 修，账本层塌缩）
#
# ── 缺口（实测）──
#   `extract_deviations` 对编译错**只读 error 行的第一句**：
#     `error[E0308]: mismatched types` → message = `"mismatched types"`
#   而**真正区分两个 E0308 的信息在后续行**：
#     `expected \`u8\`, found \`bool\``  /  `expected \`usize\`, found \`u64\``
#   ⇒ 同一文件里两个**完全不同的**类型错，身份键**逐字相同**
#     （`E0308::mismatched types::…`）⇒ `cur_ids == prev_ids`
#     ⇒ `deviation_structure_verdict` 判 **`stalled_fixed_point`**（停滞交还用户）
#     ⇒ 而**真实进展刚刚发生**（修好一个、暴露另一个）。
#   ⇒ 这就是 `Defect384` 在**账本层**的复现：相异因由压成一档。
#
# ── 修法（TS-430 `relocate`：把读搬到**有区分力的那个位置**）──
#   · 读**整个错误块**（error 行 + 其后若干行），不只第一行；
#   · 把块里**有区分力的子句**（`expected … found …` / `help:` / `cannot find …` 等）
#     并进 message；
#   · 符号名也从**整块**抽，不再只从第一句抽。
#   ★ 仍**不含行号**（`_canonical_msg` 照旧把数字归一）⇒ C2「行号漂移不改键」
#     **不被破坏** —— 本修只增加**语义**区分力，不引入**位置**区分力。
_ERR_DETAIL_RE = re.compile(
    r"(?:^|[|;\s])(expected\b[^\n|]{0,160}?found\b[^\n|]{0,80}"
    r"|cannot find\b[^\n|]{0,120}"
    r"|unresolved import\b[^\n|]{0,120}"
    r"|no method named\b[^\n|]{0,120}"
    r"|no function or associated item named\b[^\n|]{0,120}"
    r"|borrow of moved value\b[^\n|]{0,120}"
    r"|mismatched closing delimiter\b[^\n|]{0,120}"
    r"|this function takes\b[^\n|]{0,120}"
    r"|arguments to this\b[^\n|]{0,120}"
    r"|help:\s*[^\n|]{0,160})",
    re.IGNORECASE)


_ERR_LINE_RE = re.compile(
    r"^\s*error(?:\[(?P<code>[A-Za-z]?\d{3,4})\])?\s*:\s*(?P<msg>.*)$",
    re.IGNORECASE)


def _err_blocks(body: str, span: int = 14) -> list[tuple[str, str, str, str]]:
    """把编译输出切成**每个错误一块**，返回 `[(code, msg, block, path), …]`。

    ★★★★★ R125（真跑抓出的第 39 层 · 2026-10-21 · 第二十二次端到端）：
      **一条门报 19 个编译错，账本只记 1 个。**

      真证据（11:00→11:30 那一跑，两次回执）：
        · 轮1 的 `compile_tests_ok` 红因里 **19 个 `error@`**；
        · 轮2 **16 个**，而且两次的**文件完全不同**
          （轮1 是 `A线拼装.rs`，轮2 是 `呼吸基准.rs` / `T线判据收口.rs` / `洞图口径AB.rs`…）。
      而 `Δ` 两轮**都是 1**。日志原话：
        `Δ=1 … 等势轮换但**有消且基数未涨**：已消 1 个、新增 1 个（1→1）`

      ★ 原因：旧实现用 `_COMPILE_ERR_RE.search(body)` —— **只抽第一个错误**。
        于是「修好第 1 个、第 2 个才显现」这个真实进展，
        在账本上表现成「1→1 等势轮换」⇒ 永远判 `progressing` 但基数永不下降
        ⇒ 永远到不了 `cur ⊊ 基线` ⇒ **永远交付不了**。
      ★ 这是 **TS-430 `Defect384` 在账本层的第三次复现**
        （第一次：message 只读第一句；第二次：身份键不含文件；
         这次：**一条门的输出只抽一个偏差**）。
      ★ 一句话：**「一条门一个偏差」这个前提，从来没有被验证过。**

    ★ 无 `-->` 的块（`error: could not compile …` 这类**汇总行**）不算偏差；
      若整段输出里**一个** `-->` 都没有（格式异常）⇒ 退回只取第一块（老行为）。
    """
    行们 = (body or "").splitlines()
    起点 = [i for i, ln in enumerate(行们) if _ERR_LINE_RE.match(ln)]
    if not 起点:
        return []
    出: list[tuple[str, str, str, str]] = []
    for k, s in enumerate(起点):
        e = 起点[k + 1] if k + 1 < len(起点) else len(行们)
        块 = "\n".join(行们[s:min(e, s + span)])
        m = _ERR_LINE_RE.match(行们[s])
        code = str(m.group("code") or "E0460") if m else "E0460"
        msg = (str(m.group("msg") or "")).strip() if m else ""
        出.append((code, msg, 块, _err_path(块)))
    with_path = [x for x in 出 if x[3]]
    return with_path or 出[:1]


def _err_block(body: str, span: int = 14) -> str:
    """取编译错**整块**（error 行 + 其后 `span` 行）—— 有区分力的信息在这里。"""
    行们 = (body or "").splitlines()
    出: list[str] = []
    在块 = False
    for ln in 行们:
        if _COMPILE_ERR_RE.search(ln):
            在块 = True
            出.append(ln)
            continue
        if 在块:
            if len(出) >= span:
                break
            出.append(ln)
    return "\n".join(出) if 出 else (body or "")


def _err_detail(block: str) -> str:
    """从整块里抽**有区分力的子句**（多个则按出现序拼接，去重）。"""
    细节: list[str] = []
    for m in _ERR_DETAIL_RE.finditer(block or ""):
        s = " ".join(str(m.group(1) or "").split())
        if s and s not in 细节:
            细节.append(s)
    return " | ".join(细节[:3])
_STOP_SYMS = {"error", "errors", "the", "a", "an", "of", "in", "found", "expected",
              "type", "value", "literal", "string", "note", "warning", "warnings",
              "aborting", "could", "not", "only", "because", "this", "help", "more",
              "rustc", "fn", "mut", "const", "let", "line", "column", "at"}


def _extract_symbols(text: str, cap: int = 8) -> list:
    """从文本抽鲁棒符号名（同一语义错误符号稳定 → C2 身份键稳定）。"""
    toks = re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_])", text or "")
    return [t for t in dict.fromkeys(toks) if t.lower() not in _STOP_SYMS][:cap]


def pred_declared_scopes(pred: dict) -> set:
    """P 声明的作用域锚（C1 declare_scopes）：目标文件路径 ∪ must_contain 必需结构键。
    存在性判定（枚举当前 P 声明），非全称否定。compile 错误行 `--> src/a.rs`、whole_missing
    的标识符都命中其一。"""
    files, markers = set(), set()
    for c in pred.get("checks") or []:
        f = c.get("file")
        if f:
            files.add(str(f))
        for m in (c.get("must_contain") or []) or (c.get("patterns") or []):
            if isinstance(m, str) and str(m).strip():
                markers.add(str(m))
    return files | markers


def extract_deviations(red_audit: list, pred: dict) -> list[dict]:
    """Δ(P)：把单轮红门审计（{gate,file,ok,err/missing/must_not_hits}）映射成结构化偏差
    {type, message, symbols}。纯机器重述 P 的可判定检查的逆向（P 不通过 ⟺ 偏差非空），
    不含人工"必要性豁免"规则。"""
    devs: list[dict] = []
    for a in red_audit or []:
        if a.get("ok") is True:
            continue
        info = dict(a)
        err = info.pop("err", None)
        if isinstance(err, dict):
            info.update(err)     # compile 的 {"rc","err"} 摊平
        elif isinstance(err, str) and (err.strip() or ""):
            info["err_text"] = err.strip()
        # ★★★★★ R123（真跑抓出的第 37 层 · 2026-10-21 · 第二十次端到端）：
        #   **TS-442 只给 `compile` 那一支补了 `file`，这两支漏了。**
        #
        #   真证据（07:28→08:30 那一跑）：三轮 `verdict=no_anchor` ⇒ 停机，
        #   日志原话：`当前声明门集下无锚：whole_missing`。
        #   `whole_missing` 来自 `whole_reading` 门缺必需键 —— 而它的偏差
        #   **没有 `file` 字段** ⇒ 身份键里**不含任何文件路径** ⇒
        #   `anchored()` 只能靠 `symbols` 命中 `declared_scopes` 里的 markers，
        #   而 markers 只在 `pred` 存在时才有 ⇒ 无锚 ⇒ 停机。
        #
        #   ★ TS-442 的 docstring 自己写着：「旧身份键**不含任何文件路径**
        #     ⇒ `anchored` 对**编译类偏差恒为 False**」—— 它修了 compile，
        #     `whole_missing` / `scan_hit` 照旧裸着。
        #   ★ 这是 **R118 的原样第 3 次复发**：
        #     R1-F5 只修 `grep_forbidden`（漏 grep_required/whole_reading/scan_output）
        #     → R118 补齐；**TS-442 只修 compile（漏 whole_missing/scan_hit）→ 本条**。
        #     **修了枚举里的项，没修「枚举」。**
        if info.get("missing"):                       # whole_reading contains 缺必需键
            for m in info["missing"]:
                devs.append({"type": "whole_missing", "message": "缺必需结构键",
                             # ★ R123：带上门**真读的那个文件**（锚住在这里）
                             "file": str(a.get("file") or ""),
                             "symbols": _extract_symbols(str(m))})
            continue
        if info.get("must_not_hits"):                 # whole_reading forbidden / scan_output
            for h in info["must_not_hits"]:
                devs.append({"type": "scan_hit", "message": str(h)[:120],
                             # ★ R123：同上
                             "file": str(a.get("file") or ""),
                             "symbols": _extract_symbols(str(h))})
            continue
        body = info.get("err_text") or ""
        if body:                                       # compile 红：error[E..]: msg --> path
            # ★ R125：**一条门可能报 N 个错** ⇒ 逐个记成偏差（不再只取第一个）
            _块们125 = _err_blocks(body)
            if _块们125:
                for _code125, _msg125, _blk125, _pth125 in _块们125:
                    _det125 = _err_detail(_blk125)
                    _full125 = (f"{_msg125} | {_det125}" if _det125 else _msg125)
                    devs.append({"type": _code125,
                                 "message": _full125,
                                 "file": _pth125 or str(a.get("file") or ""),
                                 "symbols": _extract_symbols(_full125)
                                 or _extract_symbols(_blk125)})
                continue
            m = _COMPILE_ERR_RE.search(body)
            if m:
                msg = (str(m.group("msg") or "")).strip()
                # ★【TS-442 / TS-430 relocate】并进**整块**里有区分力的子句 +
                #   从**整块**抽符号。旧版只读 error 行第一句
                #   （`mismatched types`）⇒ 同一文件里两个**完全不同**的 E0308
                #   身份键逐字相同 ⇒ `cur_ids == prev_ids` ⇒ 判
                #   `stalled_fixed_point`（停滞交还用户），而**真实进展刚发生**。
                #   ⇒ 这是 `Defect384` 在**账本层**的复现。
                #   ★ 仍不含行号（`_canonical_msg` 照旧归一数字）⇒ C2
                #     「行号漂移不改键」**不被破坏**：只加语义区分力，不加位置区分力。
                blk = _err_block(body)
                det = _err_detail(blk)
                full = f"{msg} | {det}" if det else msg
                # ★【TS-442】带上**出错文件**（锚住在这里）。优先用 `-->` 行里的真路径
                #   （权威），退化用审计里的 file。
                devs.append({"type": str(m.group("code") or "E0460"),
                             "message": full,
                             "file": _err_path(body) or str(a.get("file") or ""),
                             "symbols": _extract_symbols(full)
                             or _extract_symbols(blk)})
                continue
            first = body.splitlines()[0][:120] if body else (a.get("gate") or "?")
            devs.append({"type": "runtime", "message": first,
                         "file": _err_path(body) or str(a.get("file") or ""),
                         "symbols": _extract_symbols(first)})
        else:
            gname = a.get("gate") or a.get("type") or "?"
            devs.append({"type": "gate_red", "message": gname,
                         "file": str(a.get("file") or ""), "symbols": []})
    return devs


def derive_gates(pred: dict, repo_root: str) -> list[dict]:
    """从结构化 P（pred）闭式确定性推导门集（C 来源唯一：LLM 不参与）。
    pred={"checks":[{kind,name,file/scope,...},...]}；每个 check 声明其命中时
    【恰好消除】一个偏差类（anchor 语义），供卡点存在性判定。门序按 name 确定。
    换检查种类＝换 P＝用户重定义；本函数不含任何搜索/候选/上限。"""
    gates: list[dict] = []
    checks = pred.get("checks") or []
    for i, c in enumerate(checks):
        kind = _CHECK_KIND_TO_GATE.get(str(c.get("kind") or ""))
        if kind is None:
            continue
        name = str(c.get("name") or f"derive_{i}")
        # ★★★★★ R95：**否定样本必须从 check 搬到门**。
        #   真跑抓出的形态（2026-10-18 · 主心骨第一次端到端）：
        #     R92 要求「每条判据自带一个它必须拒收的样本」，模型照着交了 ——
        #     但**门集唯一来源是 `derive_gates(P)`**，而本函数是**逐键白名单**
        #     （只搬 name/type/in/mode/must_contain/cmd/...），
        #     `否定样本` **不在白名单里 ⇒ 被静默丢掉**。
        #   ⇒ 症状：主心骨如实报「5/5 条门没有否定样本」——
        #     **它说的是真话，而真话的原因是"我们把它扔了"**。
        #   ★ 这是本库反复出现的那条形状的第 8 层：
        #     **「声明了」与「被使用」之间断了**（R85/R86/R87/R88/R89/R91 同型）。
        #   ★ 修法不是"在别处再补一份"，而是**让这条唯一的搬运通道别丢东西**：
        #     凡是与判据契约相关的键，一律原样搬过去。
        _契约 = {k: c[k] for k in ("否定样本", "否定") if k in c}
        if kind == "whole_reading":
            gates.append({
                "name": name, "type": kind, "in": c.get("file"),
                "mode": c.get("mode", "contains"),
                "must_contain": c.get("must_contain") or [], "derive": True,
                **_契约})
        elif kind == "compile":
            # 未给 cmd 时的兜底：用档案声明的默认构建命令（工具不假定语言）
            default_cmd = (prof_mod.get_profile().raw.get("default_build_cmd")
                           or ["true"])
            gates.append({
                "name": name, "type": "compile", "cwd": c.get("cwd") or "{repo}",
                "cmd": list(c.get("cmd") or default_cmd),
                "expect_exit": 0, "derive": True,
                **_契约})
        else:  # scan_output
            gates.append({
                "name": name, "type": "scan_output", "cwd": c.get("cwd") or "{repo}",
                "cmd": list(c.get("cmd") or []), "expect_exit": c.get("expect_exit", 0),
                "derive": True,
                **_契约})
    return gates


def assert_derived_gates(gates: list[dict], pred: dict) -> list[str]:
    """门集来源唯一断言（降级后的 reject：纯断言/自检，不作清洗路径）。
    返回 violation 列表；非空 ⇒ 门集混入非 Δ(P) 派生门 ⇒ 硬失败停机。
    不再「先造守恒门再剔除」——门只能来自 derive_gates(P)。"""
    derived_names = {g.get("name") for g in derive_gates(pred, repo_root="")}
    viol: list[str] = []
    for g in gates:
        if not g.get("derive"):
            viol.append(f"门 {g.get('name', g.get('type'))} 非常规闭式推导（非 derive_gates(P) 产物）")
            continue
        if g.get("name") not in derived_names:
            viol.append(f"门 {g.get('name')} 不在 derive_gates(P) 派生集内")
    return viol


def precheck_pred_decidable(pred: dict, repo_root: str,
                            pending_files: set | None = None,
                            ) -> tuple[bool, list[str]]:
    """入口 P 可判定性预检（C4）：P 的检查种类在当前仓库不可跑 ⇒ 入口拒绝交还用户。
    非降级门——不因 compile 不可用自动换成 scan_output（换门＝换P）。

    ★ 修复（2026-10-12）：**允许判据作用于本轮拟新建的文件**。
      原实现对 whole_reading / scan_output **只认已存在的文件**，文件不存在即拒。
      这使「从零创建一个新文件」——最常见的开发任务——**在入口就被拒死**，
      编排器实际只能改已有文件（端到端实测：目标"创建 calc.py + test_calc.py"
      被预检拒绝，规划器产出的门完全正确却被判 P 不可判定）。
      正确语义：判据指向的文件若**在本轮拟改/拟建文件集内**，则"现在不存在"是
      预期状态（正要去创建它），不构成不可判定；只有**既不存在、也不在待建集内**
      的目标才是真悬空，才拒绝。
      `pending_files` 为本轮拟改/拟建文件的相对路径集合（可为 None=旧行为）。"""
    import os
    root = str(repo_root or "")
    pend = {str(x).replace("\\", "/") for x in (pending_files or set())}
    problems: list[str] = []
    # 构建清单文件（档案 build_markers 声明；未声明 ⇒ 只要求目录存在，不假定语言）
    markers = list(prof_mod.get_profile().raw.get("build_markers") or [])
    for c in pred.get("checks") or []:
        kind = str(c.get("kind") or "")
        if kind == "compile":
            if markers:
                if not any(os.path.isfile(os.path.join(root, m)) for m in markers):
                    problems.append(
                        f"检查 {c.get('name')}（compile）在仓库根不可跑："
                        f"无 {'/'.join(str(m) for m in markers)}")
            elif not os.path.isdir(root):
                problems.append(
                    f"检查 {c.get('name')}（compile）仓库根不可达：{root}")
        elif kind in ("whole_reading", "scan_output"):
            f = str(c.get("file") or "")
            f_norm = f.replace("\\", "/").lstrip("./")
            path = os.path.join(root, f)
            if not os.path.exists(path):
                # 待建文件：现在不存在是预期状态，放行（由产出阶段创建）
                if f_norm and any(f_norm == p or p.endswith("/" + f_norm)
                                  or f_norm.endswith("/" + p) for p in pend):
                    continue
                problems.append(f"检查 {c.get('name')}（{kind}）目标不存在：{f}")
    return (not problems), problems


def anchored(dev_identity: str, declared_scopes: set) -> bool:
    """C1 存在性锚：dev_identity 是否被【当前已声明门集】的某一 scope 恰好框定。
    可判定（枚举当前声明 scope）；不判『所有分解下无锚』（∀ 否定不可判）。"""
    return any(scope in dev_identity for scope in declared_scopes)


def pick_anchored_deviation(cur: list[dict]) -> dict | None:
    """C3 闭式选择：Δ 中身份 canonical 序最小且被已声明 scope 锚定的偏差。
    O(1)（min over 当前 Δ）、确定性、不用搜索。返回候选偏差或 None。"""
    if not cur:
        return None
    best: dict | None = None
    for d in cur:
        if d.get("_skip_anchor") is True:
            continue
        if best is None or deviation_identity(d) < deviation_identity(best):
            best = d
    return best


def deviation_structure_verdict(prev: list[dict], cur: list[dict],
                                declared_scopes: set,
                                prev_green: set,
                                ignore_ids: frozenset = frozenset()
                                ) -> tuple[str, str]:
    """结构停机判据（C1/C6 + SelfEvolvingRule 停机⟺不动点）。无跨轮折叠、无数值上限。

    verdict ∈ {converged, progressing, stalled_fixed_point, no_anchor, diverging}：
      - converged        ：Δ=∅ ⇒ P 达成 ⇒ 交付。
      - progressing      ：Δ_{n+1} ⊂ Δ_n（纯结构身份真子集）⇒ 推进。
      - stalled_fixed_point：Δ_{n+1} == Δ_n ≠ ∅ ⇒ 非目标不动点 ⇒ 停滞交还用户。
      - no_anchor        ：∃ u∈Δ 当前声明门集下无锚 ⇒ 交还（补门/换P），非全称否定。
      - diverging        ：已消偏差重现（C6，u 曾绿现在红）⇒ 回滚+交还。
    prev=None 表示首轮（无前一窗口）；prev_green 为曾绿偏差身份单点映射（C5）。"""
    cur_ids = {deviation_identity(d) for d in cur}
    if not cur_ids:
        # ★★★★★ R104：**`Δ=∅` 不等于「P 达成」。**
        #   真跑抓出：3 条门全是 regression guard（「定义仍在」「规仍在」「能编译」），
        #   5 个交付物里 3 个零门 ⇒ 门全绿是**必然的**、与目标无关
        #   ⇒ `Δ=∅` ⇒ 回执写「**P 达成**」，而同一时刻另一条回执写「未收敛停机」。
        #   ★ `Δ=∅` 的全部含义只是：**在已声明的门集下**，没有偏差。
        #     它离「目标达成」还差一个前提：**这些门真能判目标**。
        #     那个前提由 `sufficiency_bound` 判（R104），**不归本函数判**。
        #   ⇒ 措辞必须诚实，不能替它做保证。
        return "converged", ("Δ=∅ ⇒ **已声明的门集下**无偏差"
                             "（≠ 目标达成；门集是否充分见 bound）")
    if prev is None:
        if any(i in prev_green for i in cur_ids):
            return "diverging", "已消偏差重现（C6）"
        return "progressing", "首轮：待推进（无前一窗可比较）"
    prev_ids = {deviation_identity(d) for d in prev}
    if cur_ids == prev_ids:
        if any(i in prev_green for i in cur_ids):
            return "diverging", "已消偏差重现（C6）"
        return "stalled_fixed_point", "Δ==前一轮≠∅ ⇒ 非目标不动点，需换P/换分解"
    if cur_ids < prev_ids:
        if any(i in prev_green for i in cur_ids):
            return "diverging", "已消偏差重现（C6）"
        return "progressing", "Δ 严格递减（纯结构真子集）"
    # ★★★★★ R122（真跑抓出的第 36 层 · 2026-10-21 · 第十九次端到端）：
    #   **`no_anchor` 是「达标判定」之外的第二把卡尺，而 R119b 只修了第一把。**
    #
    #   真证据（05:25→06:24 那一跑）：三轮 `verdict=no_anchor` ⇒ 停机。
    #   同时刻 `compile_ok=False`（全库编译红），它报的错在
    #   `运行时事件采集.rs` / `运行时健康诊断.rs` —— **都不在交付物里**。
    #   ⇒ `anchored(身份, declared_scopes)` 必然 False（scope 里没有那个文件）
    #   ⇒ `un` 非空 ⇒ `no_anchor` ⇒ `action=stop` ⇒ 停机交还。
    #
    #   ★ R119b 已经论证过：**作业面碰不到的债不是本次任务的责任**
    #     （编排器改不了那些文件 ⇒ 要求它消除是无解）。
    #     `_达标` 那一侧已经照此修了；
    #     但「有没有锚」这一侧**仍然拿全部 Δ 去查** ⇒ 外债必然无锚 ⇒ 必然停机。
    #   ★ 这是同一族的第 3 次复发（R108 / R116齿③ / R119a 都是「修了一侧」）：
    #     一个判据被**两处**读，修一处就等于没修。
    #
    #   ★ 修法：`ignore_ids` 只从**基线**取（与 R119b 同一条纪律：
    #     豁免集合不得随本轮长大，否则新债会被豁免掉）。
    #   ★ 不放松：作业面**内**的偏差无锚 ⇒ 仍判 `no_anchor`（那才是真该补门）。
    un = [d for d in cur
          if deviation_identity(d) not in ignore_ids
          and not anchored(deviation_identity(d), declared_scopes)]
    if un:
        return "no_anchor", f"当前声明门集下无锚：{un[0].get('type')}"
    if any(i in prev_green for i in cur_ids):
        return "diverging", "已消偏差重现（C6）"
    # ★★★【TS-442】2026-10-14：**"等势轮换"里有真进展，不得一律判停滞**。
    #
    # ── 旧口径 ──
    #   走到这里就返回 `stalled_fixed_point`（"等势轮换/未严格递减…交还用户"）。
    #   而实测常见形态是：**上一轮的偏差已消（被修好了），同时暴露出一个新偏差**
    #   （修好一个编译错、下一个才显现）—— 两者身份不同、基数相同。
    #   ⇒ 旧口径把这种"**有消有增**"判成停滞 ⇒ 停机，而**真实进展刚刚发生**。
    #
    # ── 新口径（保守：必须"确实消掉了东西"**且**"偏差数没涨"）──
    #   · `cur_ids == prev_ids` ⇒ **一个都没变** ⇒ 真不动点 ⇒ `stalled_fixed_point`（不变）；
    #   · 有消（`prev_ids - cur_ids ≠ ∅`）**且** 基数未涨（`|cur| ≤ |prev|`）
    #     ⇒ 判 `progressing`（本轮确实解决过偏差，且没有把盘子做大）；
    #   · 其余（纯增、或消了但盘子变大）⇒ 仍判 `stalled_fixed_point`（不放宽）。
    #
    # ★ 为什么加"基数未涨"这一条：单看"有消有增"会把 **A→B→C→D 无重复空转**
    #   也判成推进（`diverging` 只在**同一**偏差重现时触发，抓不到不重复的空转）。
    #   加上基数非增 ⇒ 该判据成为一根**单调不增的势函数**，
    #   真正的"修一个坏一个、盘子越修越大"仍会被判停滞。
    #
    # ★ 诚实边界（**这是一处语义判断，不只是读数修正**，用户可否决）：
    #   本改动会**放宽**"等势轮换即停机"。防抖动责任移交两处**真实**信号：
    #   ① `diverging`（已消偏差**重现**，C6，上面那一条）；
    #   ② 基数单调 + 资源护栏（`MAX_WORK` / 预算）。
    #   理由：用"基数恰好相等"当停机信号是**用形状代替事实**；而"消了没有、
    #   盘子有没有变大"才是**事实**。故让停机由事实触发，不由计数巧合触发。
    _消 = prev_ids - cur_ids
    _增 = cur_ids - prev_ids
    if _消 and len(cur_ids) <= len(prev_ids):
        return "progressing", (f"等势轮换但**有消且基数未涨**：已消 {len(_消)} 个、"
                               f"新增 {len(_增)} 个（{len(prev_ids)}→{len(cur_ids)}）"
                               f" ⇒ 本轮确实解决过偏差（不判停滞）")
    # ★★★★★ R185（诊断读数 · 目标①）：**判「停滞」必须能说出集合关系。**
    #   第 87 次真跑只留下一行 `Δ=6 verdict=stalled_fixed_point`，
    #   而「13 处基线 → 6 处」到底是**真停滞**还是**两把尺子对不上**，
    #   日志里读不出来 ⇒ 只能回去猜代码。
    #   ★ 这是本项目反复的形态：「**手里的读数不是判据需要的那条读数**」——
    #     判据要「Δ 是不是真子集/有没有消」，手里只有一个基数。
    #   ⇒ 把 prev / cur / 消 / 增 四个数都写进因由（不改判定，只改读数）。
    return "stalled_fixed_point", (
        f"等势轮换/未严格递减且非重合不动点 ⇒ 交还用户（不判收敛）"
        f"［诊断：prev={len(prev_ids)} cur={len(cur_ids)}"
        f" 消={len(_消)} 增={len(_增)}"
        f" 子集={cur_ids <= prev_ids}"
        f"］")


# ── 自检：把判据翻译成机器断言 ────────────────────────────────────
def _goal_selftest() -> tuple[int, list]:
    """目标驱动偏差判定层自检（C1/C2/C3/C4/C5/C6）。返回 (total, fails)。"""
    fails: list = []

    # ════════════════════════════════════════════════════════════════════
    # 【R133】机器说了「不成立」，却没说「哪一条不成立」（第 46 层）
    # ════════════════════════════════════════════════════════════════════
    # 真证据（第 28 次真跑）：重规划连续 3 次被拒，原文
    #   `判定层必然性不成立：门绿不必然等于目标达成`
    # 而回喂的话只有「请把软/空/含糊的门换成有判别力的门」——
    # ★ 模型手里有 9 条门，**不知道是哪一条不合格**，只能整体重写 ⇒ 3 次全败。
    # ★ 与 R130 同族：R130 是「合法集合没说出来」，这里是「哪一条不合格没说出来」。
    _spec133 = {"deliverables": ["src/x.rs"], "acceptance": ["做到"]}
    _门133 = [
        {"type": "grep_required", "in": "src/x.rs", "patterns": ["a"],
         "name": "甲门"},
        {"type": "whole_reading", "in": "src/x.rs", "must_contain": ["b"],
         "name": "乙门"},
    ]
    _ok133, _因133 = sufficiency_bound(_门133, _spec133)
    if _ok133:
        fails.append("R133 ★ 全文本门集被判成「判定层必然性成立」⇒ 判据被放松")
    # ★ 噪音齿**必须放在最前**并用自己的夹具：
    #   否则别处先红时，它的 `✗` 会被后面更早的 append 挤掉/盖住
    #   （实测两次都是「红了但没点名」）。
    _串133 = "\n".join(_因133)
    # ① 必须**逐门点名**（不能只说"缺少行为门"）
    for _名133 in ("甲门", "乙门"):
        if _名133 not in _串133:
            fails.append(f"R133 ★ 逐门诊断里**没有点名** `{_名133}` ⇒ "
                         f"模型不知道是哪一条不合格，只能整体重写（真跑实测 3 次全败）")
    # ② 必须说**为什么**（类型 / 没引用 {file} / cmd 太短）
    if "grep/文本门只读源码" not in _串133:
        fails.append("R133 ★ 逐门诊断没给出**原因**（只说「不行」等于让模型猜）")
    # ③ 必须给**可执行改法**，且明确「不要整体重写」
    if "{file}" not in _串133 or "不要" not in _串133:
        fails.append("R133 ★ 逐门诊断没给**可执行改法**（模型会整体重写，再踩另一条）")


    # ★ R133 噪音齿：**有行为门时不该报逐门诊断**。
    #   ★ 为什么必须放在最前：实测注入「`if True:` 让缺少行为门恒报」后，
    #     别处的夹具（三件套 / 边界卡）先红，把输出占满 ⇒
    #     这条齿的 `✗` **根本没机会被看见** ⇒ 我读输出时以为它没咬。
    #   ★ 齿必须让它要测的那件事成为**唯一的差异**，且**排在最前**。
    _spec133z = {"deliverables": ["src/x.rs"], "acceptance": ["做到"]}
    _ok133z, _因133z = sufficiency_bound(
        [{"type": "compile", "cmd": ["cargo", "test", "{file}"], "name": "行为门"}],
        _spec133z)
    if "逐门诊断" in "\n".join(_因133z):
        fails.append("R133 ★ 有行为门时仍报逐门诊断 ⇒ 噪音，会淹没真因由")
    if not _ok133z:
        fails.append(f"R133 ★ 单条行为门仍被判不足：{_因133z} ⇒ 判据过严")
    #   ★ 诚实登记：这两条**共用同一个夹具**，注入「缺少行为门恒报」时
    #     只会触发第二条（`_ok133z` 变 False），第一条**测不到** ——
    #     因为那个注入不制造噪音，只制造"过严"。
    #     ⇒ 要测「噪音」，需要**另一个**注入：让 `_behavioral` 恒 False
    #       但 `behavioral` 非空（即"有行为门却仍报逐门诊断"）。
    #     ★ 实测确认：注入「缺少行为门恒报」时，输出里
    #       `R133 ★ 有行为门时仍报逐门诊断` **不出现**（只出现"判据过严"）。
    #       这不是无齿，是**这两件事确实是两个方向**。
    #     ⇒ 补一条真正测噪音的：让 behavioral 非空但逐门诊断照报。
    _树133n = __import__("ast").parse(
        __import__("pathlib").Path(__file__).read_text(encoding="utf-8"))
    _噪133 = False
    for _n133n in __import__("ast").walk(_树133n):
        if (isinstance(_n133n, __import__("ast").If)
                and isinstance(_n133n.test, __import__("ast").Name)
                and _n133n.test.id == "_非行133"):
            _噪133 = True
    if not _噪133:
        fails.append("R133 ★ 逐门诊断**没挂在「非行为门非空」的条件上** ⇒ "
                     "要么恒报（噪音），要么恒不报（无诊断）")

    # C2 纯结构身份：行号漂移不改键；符号不同则改键
    d1 = {"type": "E0432", "message": "unresolved import gpu at src/a.rs:12:5",
          "symbols": ["gpu"]}
    d2 = {"type": "E0432", "message": "unresolved import gpu at src/a.rs:220:18",
          "symbols": ["gpu"]}
    if deviation_identity(d1) != deviation_identity(d2):
        fails.append("C2 fail: 行号漂移不应改偏差身份键")
    d3 = {"type": "E0432", "message": "unresolved import gpu at src/a.rs:12:5",
          "symbols": ["cpu"]}
    if deviation_identity(d1) == deviation_identity(d3):
        fails.append("C2 fail: 符号不同应改键")

    # ════════════════════════════════════════════════════════════════════
    # 【TS-442 / TS-430】账本层不塌缩：两个**不同**的 E0308 必须身份不同
    # ════════════════════════════════════════════════════════════════════
    # 旧版 `extract_deviations` 只读 `error[E0308]: mismatched types` 的**第一句**
    # ⇒ 同一文件里两个完全不同的类型错身份键**逐字相同** ⇒ `cur_ids == prev_ids`
    # ⇒ `deviation_structure_verdict` 判 `stalled_fixed_point`（停滞交还用户），
    # 而**真实进展刚刚发生**（修好一个、暴露另一个）。这是 Defect384 在账本层。
    try:
        _pred = {"checks": [{"kind": "compile", "name": "c", "file": "src/a.rs"}]}
        _e0308_u8 = ("error[E0308]: mismatched types\n"
                     " --> src/a.rs:2:17\n"
                     "  |\n"
                     "2 |     let x: u8 = true;\n"
                     "  |            --   ^^^^ expected `u8`, found `bool`\n"
                     "error: aborting due to 1 previous error\n")
        _e0308_usize = ("error[E0308]: mismatched types\n"
                        " --> src/a.rs:88:9\n"
                        "  |\n"
                        "88 |     let y: usize = 真;\n"
                        "  |            -----   ^^ expected `usize`, found `u64`\n"
                        "error: aborting due to 1 previous error\n")
        _da = extract_deviations(
            [{"gate": "c", "file": "src/a.rs", "ok": False, "err": _e0308_u8}], _pred)
        _db = extract_deviations(
            [{"gate": "c", "file": "src/a.rs", "ok": False, "err": _e0308_usize}], _pred)
        if not _da or not _db:
            fails.append("【TS-442】编译错应抽出偏差")
        else:
            # ① ★★★ 两个不同的 E0308 ⇒ 身份**必须**不同（不塌缩）
            if deviation_identity(_da[0]) == deviation_identity(_db[0]):
                fails.append("【TS-442】两个不同 E0308（u8/bool vs usize/u64）"
                             "身份键不得相同（账本层塌缩）")
            # ② 区分信息确实进了 message（不是靠别的偶然）
            if "u8" not in str(_da[0].get("message")) or \
               "usize" not in str(_db[0].get("message")):
                fails.append("【TS-442】区分子句（expected/found）应进 message")
            # ③ C2 不得回退：**同一错误**换行号 ⇒ 身份**仍相同**
            _e0308_u8_移 = _e0308_u8.replace("src/a.rs:2:17", "src/a.rs:220:18") \
                                     .replace("\n2 |", "\n220 |")
            _dc = extract_deviations(
                [{"gate": "c", "file": "src/a.rs", "ok": False, "err": _e0308_u8_移}], _pred)
            if _dc and deviation_identity(_dc[0]) != deviation_identity(_da[0]):
                fails.append("C2 回退：同一错误仅行号漂移不得改身份键")
            # ④ 不同错误码仍不同（原有区分力不得丢）
            _e0432 = ("error[E0432]: unresolved import `gpu`\n"
                      " --> src/a.rs:12:5\n")
            _dd = extract_deviations(
                [{"gate": "c", "file": "src/a.rs", "ok": False, "err": _e0432}], _pred)
            if _dd and deviation_identity(_dd[0]) == deviation_identity(_da[0]):
                fails.append("【TS-442】不同错误码身份键不得相同")
    except Exception as _e442d:      # noqa: BLE001
        fails.append(f"【TS-442】账本身份自检自身异常：{type(_e442d).__name__}: {_e442d}")

    # C4 入口 P 可判定性预检：无构建清单的 compile ⇒ 拒绝；缺文件 ⇒ 拒绝
    # （本自检用档案 build_markers；未声明时退回"仓库根不可达"判据）
    ok1, p1 = precheck_pred_decidable(
        {"checks": [{"kind": "compile", "name": "cb",
                     "cmd": ["python", "-c", "pass"]}]}, "/no/such/repo")
    if ok1:
        fails.append("C4 fail: compile 在不可达仓库根应入口拒绝")
    ok2, p2 = precheck_pred_decidable(
        {"checks": [{"kind": "whole_reading", "name": "w", "file": "a.rs",
                     "mode": "contains", "must_contain": ["x"]}]}, "/no/such/repo")
    if ok2:
        fails.append("C4 fail: whole_reading 目标文件不存在应入口拒绝")

    # derive_gates 闭式：门序确定、含 derive 标记；来源唯一断言
    pred = {"checks": [
        {"kind": "whole_reading", "name": "has_solve", "file": "a.rs",
         "mode": "contains", "must_contain": ["fn solve"]},
        {"kind": "compile", "name": "cb", "cmd": ["python", "-c", "pass"]}]}
    gates = derive_gates(pred, "")
    names = [g["name"] for g in gates]
    if names != ["has_solve", "cb"]:
        fails.append(f"derive_gates fail: 门序应确定且齐全，得 {names}")
    if not all(g.get("derive") for g in gates):
        fails.append("derive_gates fail: 门应全带 derive 标记（来源唯一）")
    if assert_derived_gates(gates, pred):
        fails.append("assert_derived_gates fail: 合法派生门不得报违规")
    bad = dict(gates[0]); bad["derive"] = False
    if not assert_derived_gates([bad], pred):
        fails.append("assert_derived_gates fail: 非派生门应被断言拦截（硬失败）")

    # C1 存在性锚：声明 scope 命中 ⇒ 锚；全称否定不判（仅当前门集）
    scopes = {"fn solve", "unresolved import"}
    dA = {"type": "E", "message": "unresolved import xyz", "symbols": []}
    if not anchored(deviation_identity(dA), scopes):
        fails.append("C1 fail: 声明 scope 命中应判有锚")
    dB = {"type": "E", "message": "some other marker", "symbols": []}
    if anchored(deviation_identity(dB), scopes):
        fails.append("C1 fail: 声明 scope 未命中应判无锚（存在性、可判定）")

    # C3 闭式选择：身份 canonical 序最小
    cur = [dA, dB]
    picked = pick_anchored_deviation(cur)
    if not picked or deviation_identity(picked) not in {deviation_identity(dA),
                                                        deviation_identity(dB)}:
        fails.append("C3 fail: 应选出当前 Δ 中身份序最小偏差")

    # C5/C6 + 停机：converged / progressing(真子集) / stalled_fixed_point / diverging
    v_conv, _ = deviation_structure_verdict([dA], [], scopes, set())
    if v_conv != "converged":
        fails.append("verdict fail: Δ=∅ 应 converged")
    # 构造 真子集 与 非真子集 情况
    dP = {"type": "E", "message": "fix me", "symbols": ["z"]}
    prevX = [dA, dP]
    curX = [dA]
    v_prog2, _ = deviation_structure_verdict(prevX, curX, scopes, set())
    if v_prog2 != "progressing":
        fails.append("verdict fail: 真子集应 progressing")
    # stalled: 等势轮换/不减少 ⇒ 不是 converged/progressing
    curOSC = [{"type": "E", "message": "b", "symbols": []}]
    prevOSC = [{"type": "E", "message": "a", "symbols": []}]
    v_stall, _ = deviation_structure_verdict(prevOSC, curOSC, set(), set())
    if v_stall not in ("stalled_fixed_point", "no_anchor"):
        fails.append("verdict fail: 等势轮换不等式应不停留在推进/收敛")
    # diverging: 已消偏差重现（曾绿现在红）
    prevG = [dA]
    v_div, _ = deviation_structure_verdict(prevG, [dA], scopes, {deviation_identity(dA)})
    if v_div != "diverging":
        fails.append("verdict fail: 已消偏差重现应 diverging")

    # ════════════════════════════════════════════════════════════════════
    # 【TS-442】2026-10-14 三处修正的机器断言（锚维度 / 有消有增 / 真停滞不变）
    # ════════════════════════════════════════════════════════════════════
    # ① 身份键必须**带文件** —— 否则编译类偏差恒无锚 ⇒ 判 no_anchor ⇒ 停机。
    #    实测：真跑 #4 正是以 no_anchor/stop 收场。
    sc_path = {"src/a.rs"}
    dP1 = {"type": "E0308", "message": "mismatched types | expected u8, found bool",
           "file": "src/a.rs", "symbols": ["u8"]}
    if "src/a.rs" not in deviation_identity(dP1):
        fails.append("【TS-442】身份键应含文件路径（否则 anchored 恒 False）")
    if not anchored(deviation_identity(dP1), sc_path):
        fails.append("【TS-442】带文件的偏差在声明文件作用域下应判有锚")
    d_no_file = {"type": "E0308", "message": "mismatched types", "symbols": []}
    if deviation_identity(d_no_file) != "E0308::mismatched types::":
        fails.append("【TS-442】无 file 的老账本记录身份键应保持不变（向后兼容）")
    # ② 有消有增且基数未涨 ⇒ progressing（旧版判 stalled，把真进展当停滞）
    dQ = {"type": "E0308", "message": "mismatched types | expected usize, found u64",
          "file": "src/a.rs", "symbols": ["usize"]}
    v_turn, w_turn = deviation_structure_verdict([dP1], [dQ], sc_path, set())
    if v_turn != "progressing":
        fails.append(f"【TS-442】有消有增且基数未涨应 progressing，实得 {v_turn}")
    # ③ 基数**涨了** ⇒ 仍判停滞（不放宽：修一个坏两个是退步）
    #    ★ 注意：消息里不得只差数字（`_canonical_msg` 会把数字归一 ⇒ 两偏差同身份）。
    dR1 = {"type": "E", "message": "alpha", "file": "src/a.rs", "symbols": []}
    dR2 = {"type": "E", "message": "beta", "file": "src/a.rs", "symbols": []}
    if deviation_identity(dR1) == deviation_identity(dR2):
        fails.append("【自检夹具】alpha/beta 应身份不同（否则本条断言无效）")
    # ★ R125a：一条门报 N 个编译错 ⇒ 必须抽 N 个偏差（不许只取第一个）
    _多 = "\n".join(
        [f"error[E0308]: mismatched types\n --> src/m{i}.rs:1:1\n  |\n"
         f"1 | let x: u8 = true;\n  |   ^^^^ expected `u8`, found `bool`\n"
         for i in range(5)])
    # ★ 夹具必须与**真实审计记录**同形：`err` 是**字符串**
    #   （`_run_one` 返回 `{"rc": rc, "err": evidence}`，`**info` 摊平后
    #    `a["err"]` 是 str）。第一版我写成 `{"err": {"err": ...}}` ⇒
    #   `info["err"]` 是 dict ⇒ `err_text` 从未被设置 ⇒ body 为空
    #   ⇒ 走 `gate_red` 分支 ⇒ 抽出 1 个 ⇒ **齿假红**。
    #   ★ 「齿的夹具与真实数据不同形」= 齿在测一个不存在的场景。
    _d多 = extract_deviations([{"gate": "cargo_check_lib", "file": "（全库）",
                                "ok": False, "err": _多}], {})
    if len(_d多) != 5:
        fails.append(f"R125a ★ 一条门报 5 个编译错，只抽出 {len(_d多)} 个偏差 ⇒ "
                     f"账本塌缩 ⇒ Δ 永远 1 ⇒ 永远「1→1 等势轮换」⇒ 交付不了"
                     f"（真跑实测：19 个错压成 1 个）")
    elif len({deviation_identity(d) for d in _d多}) != 5:
        fails.append("R125a ★ 5 个偏差身份键有重复 ⇒ 抽了但不可区分")
    # ★ 无 `-->` 的汇总行不算偏差（`error: could not compile …`）
    _汇总 = ("error[E0308]: mismatched types\n --> src/a.rs:1:1\n"
             "error: could not compile `x` due to 2 previous errors\n")
    _d汇 = extract_deviations([{"gate": "g", "file": "（全库）", "ok": False,
                                "err": _汇总}], {})
    if len(_d汇) != 1:
        fails.append(f"R125a ★ 汇总行被当成偏差 ⇒ 抽出 {len(_d汇)} 个（应为 1）")
    # ★ R123：`whole_missing` / `scan_hit` 也必须带 `file`（TS-442 的漏项）
    for _kind, _k, _payload in (("whole_missing", "missing", ["必需结构键甲"]),
                                ("scan_hit", "must_not_hits", ["禁词乙"])):
        _r = [{"gate": "g", "file": "src/甲.rs", "ok": False, _k: _payload}]
        _d = extract_deviations(_r, {})
        _hit = [x for x in _d if x.get("type") == _kind]
        if not _hit:
            fails.append(f"R123 夹具失效：{_kind} 没抽出来")
        elif not _hit[0].get("file"):
            fails.append(f"R123 ★ `{_kind}` 偏差**没有 file** ⇒ 身份键不含文件路径 "
                         f"⇒ `anchored` 恒 False ⇒ `no_anchor` 停机"
                         f"（真跑实测：三轮 no_anchor 全是 whole_missing）")
        elif "src/甲.rs" not in deviation_identity(_hit[0]):
            fails.append(f"R123 ★ `{_kind}` 的 file 没进身份键 ⇒ 仍然锚不到")
        elif not anchored(deviation_identity(_hit[0]), {"src/甲.rs"}):
            fails.append(f"R123 ★ `{_kind}` 带了 file 却仍锚不到 ⇒ 修得不对")
    # ★ R122：`no_anchor` 必须能被 `ignore_ids` 豁免（作业面外的历史债）
    dW = {"type": "E0308", "message": "mismatch",
          "file": "src/测试支撑/运行时事件采集.rs", "symbols": ["w"]}
    idW = deviation_identity(dW)
    v_un, _ = deviation_structure_verdict([dP1], [dP1, dW], sc_path, set())
    if v_un != "no_anchor":
        fails.append(f"R122 夹具失效：作业面外偏差本应判 no_anchor，实得 {v_un}")
    v_ig, _ = deviation_structure_verdict([dP1], [dP1, dW], sc_path, set(),
                                          ignore_ids=frozenset({idW}))
    if v_ig == "no_anchor":
        fails.append("R122 ★ 作业面外的历史债仍判 no_anchor ⇒ "
                   "R119b 只修了达标判定那一侧，锚定这一侧照旧死锁")
    # ★ 反向：作业面**内**的偏差无锚 ⇒ 仍必须判 no_anchor（不放松）
    dN = {"type": "E0308", "message": "mismatch",
          "file": "src/别处/内坏.rs", "symbols": ["n"]}
    v_in, _ = deviation_structure_verdict([dP1], [dP1, dN], sc_path, set(),
                                          ignore_ids=frozenset({idW}))
    if v_in != "no_anchor":
        fails.append(f"R122 ★ 作业面**内**的偏差无锚却判 {v_in} ⇒ 口径被放松")
    v_grow, _ = deviation_structure_verdict([dP1], [dR1, dR2], sc_path, set())
    if v_grow == "progressing":
        fails.append("【TS-442】偏差基数上涨不得判 progressing（防无限空转）")
    # ④ 一个都没变 ⇒ 真不动点（原语义不得回退）
    v_same, _ = deviation_structure_verdict([dP1], [dict(dP1)], sc_path, set())
    if v_same != "stalled_fixed_point":
        fails.append("【TS-442】Δ 逐字不变仍应判 stalled_fixed_point（原语义不得回退）")
    # ⑤ C2 不回退：同一错误仅**行号**漂移（`-->` 行里的 line:col）不得改身份键
    _eA = ("error[E0308]: mismatched types\n --> src/a.rs:2:17\n  |\n"
           "2 |     let x: u8 = true;\n"
           "  |            --   ^^^^ expected `u8`, found `bool`\n")
    _eB = ("error[E0308]: mismatched types\n --> src/a.rs:220:18\n  |\n"
           "220 |     let x: u8 = true;\n"
           "  |            --   ^^^^ expected `u8`, found `bool`\n")
    _predC2 = {"checks": [{"kind": "compile", "name": "c", "file": "src/a.rs"}]}
    _dA2 = extract_deviations(
        [{"gate": "c", "file": "src/a.rs", "ok": False, "err": _eA}], _predC2)
    _dB2 = extract_deviations(
        [{"gate": "c", "file": "src/a.rs", "ok": False, "err": _eB}], _predC2)
    if not _dA2 or not _dB2:
        fails.append("【TS-442】编译错应抽出偏差（C2 断言夹具）")
    elif deviation_identity(_dA2[0]) != deviation_identity(_dB2[0]):
        fails.append("C2 回退：同一错误仅行号漂移不得改偏差身份键")

    # ★★★★★ R133b（同一趟真跑的第三层）：**分母硬编码，45 条判据点报成 15。**
    #   ★ 这是 R130c 的同一缺陷在**另一个模块**复发（第 5 次）：
    #     R68 / R70c / R72 / R130c 之后，这是第 5 次。
    #   ★ 后果不是"少报数字"，是**报表在骗人**：
    #     我加完 R133 的齿，自检照打 `55/55 全绿` —— 分母没动，
    #     而实际判据点从 55 变成了 60。**"全绿"这两个字因此不再可信。**
    #   ⇒ 分母运行时从源码数（同 gateway/router/generic_goal/auto_goal 的修法）。
    try:
        _源133 = open(__file__, encoding="utf-8").read()
        _起133 = _源133.index("def _goal_selftest")
        _止133 = _源133.index("def _selftest() -> int:")
        _n133 = _源133[_起133:_止133].count("fails.append")
    except Exception:
        _n133 = 0
    #   ★ 分母不许是**常量**：常量分母会让「齿加了、分母没动」，
    #     报表照样打「55/55 全绿」而实际有 86 条齿。
    #     R68 / R70c / R72 / R130c 之后，这是第 5 次。
    _树133 = __import__("ast").parse(
        __import__("pathlib").Path(__file__).read_text(encoding="utf-8"))
    #   ★ 第一版查**整棵树**里有没有 `_n133 = 常量` ⇒ **假红**：
    #     兜底分支 `except Exception: _n133 = 0` 是**正常写法**，
    #     却被判成「分母是常量」。
    #     ⇒ 只认**函数体顶层**的赋值（`try:` 里的兜底不算）。
    #     ★ 又是「查存在 vs 查是那一条」—— 第 N 次。
    #   ★★ 第二版查「有没有 `_n133 = 常量` 的赋值」⇒ **仍然无齿**：
    #     注入 `return 15` 时 `_n133 = ...` 那一行**还在**（只是不再被返回）
    #     ⇒ 查赋值的齿照样绿。**实测：注入后输出「55/55 全绿」，一点没变。**
    #   ⇒ 必须查**返回语句本身**：`return _n133, fails` 还是 `return 15, fails`。
    #     ★ 这与 R130 的教训完全一致：**要测的是「结果」，不是「过程里有没有那个词」。**
    _常133 = False
    for _f133 in __import__("ast").walk(_树133):
        if not (isinstance(_f133, __import__("ast").FunctionDef)
                and _f133.name == "_goal_selftest"):
            continue
        for _s133 in _f133.body:            # ★ 只看顶层语句
            if not isinstance(_s133, __import__("ast").Return):
                continue
            _v133 = _s133.value
            if isinstance(_v133, __import__("ast").Constant):
                _常133 = True
            elif (isinstance(_v133, __import__("ast").Tuple)
                    and _v133.elts
                    and isinstance(_v133.elts[0], __import__("ast").Constant)):
                _常133 = True
    if _常133:
        fails.append("R133 ★ `_goal_selftest` 的分母 `_n133` 是**常量** ⇒ "
                     "齿加了分母不动，报表会打「N/N 全绿」而齿更多")
    if _n133 < 15:
        fails.append(f"R133 ★ `_goal_selftest` 判据点只有 {_n133} 个（下限 15）"
                     f"—— 齿被成批删掉而报表照样绿")
    return _n133, fails


# ── 自检：把判据翻译成机器断言 ────────────────────────────────────
def _selftest() -> int:
    import tempfile
    # ★ 稳健导入（2026-10-14）：原为裸 `from generic_goal import _DRY_SPEC`，
    #   只在 `sys.path[0]` 恰为包目录时成立 ⇒ 用 `python -m
    #   agentscope_orchestrator.gate_derive --selftest` 跑会
    #   `ModuleNotFoundError: No module named 'generic_goal'`，
    #   于是**回归跑不起来**（跑不起来 = 没有回归 = 坏了不知道）。
    #   同目录模块改用**包内相对导入**，两种调用方式都成立。
    from agentscope_orchestrator.generic_goal import _DRY_SPEC
    from agentscope_orchestrator.gate_spec import (evaluate as g_eval,
                                                   _canon, _sig)
    fails: list = []

    # ★★★★★ R149 齿**必须排在最前**。
    #   实测：我先把它放在末尾，注入「过滤过头」时，
    #   一条**旧测试**（`rr["gates"][0]`）先崩 IndexError
    #   ⇒ 整个自检带栈退出，`if fails:` 那段根本没执行
    #   ⇒ 红是红了，但**没有点名 R149** ⇒ 我不知道是哪个缺陷。
    #   ★ 这是「齿要排在最前」的又一次实证：
    #     齿排在别人后面，别人的崩溃会把它的结论吃掉。
    # ══════════════════════════════════════════════════════════════
    # 【R149】模型给错了格式，产品不得崩在 AttributeError 上
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 36 次真跑，轮 4，整趟死在这里）：
    #   `AttributeError: 'str' object has no attribute 'get'`
    #   `  gate_derive.py:228 in _merge`
    # ★ 齿要测**三件事**，少一件就是另一种不诚实：
    #   ① 不崩（有坏门也要返回规约）
    #   ② 好门**一条不少**（不能因为过滤把好的也丢了）
    #   ③ 坏门**被说出来**（`notes["dropped_bad_gate"]` > 0）
    try:
        _坏规149 = {
            "deliverables": [{"path": "src/x.rs",
                              "gates": [{"type": "compile_ok", "name": "好门"},
                                        "这是一条字符串，不是门",
                                        {"type": "grep_required",
                                         "name": "另一好门",
                                         "patterns": ["ABC"]}]}],
            "gates": [{"type": "compile_ok", "name": "顶层好门"}, 42],
        }
        _规149, _注149 = normalize_gates(_坏规149)
        _门149 = _规149["deliverables"][0]["gates"]
        _名149 = {g.get("name") for g in _门149 if isinstance(g, dict)}
        if "好门" not in _名149 or "另一好门" not in _名149:
            fails.append(
                f"【R149】★ 过滤坏门时**把好门也丢了**（剩 {sorted(_名149)}）"
                " ⇒ 判据被静默削减，门全绿不再蕴含目标达成")
        if not _规149.get("gates"):
            fails.append("【R149】★ 顶层门被清空了 ⇒ 过滤过头")
        if int(_注149.get("dropped_bad_gate") or 0) < 2:
            fails.append(
                f"【R149】★★ 丢弃了坏门却**没说出来**"
                f"（dropped_bad_gate={_注149.get('dropped_bad_gate')}）"
                " ⇒ 主心骨以为有这条判据，实际没有（R146 同一个病）")
    except AttributeError as _e149:
        fails.append(f"【R149】★★ 有坏门时仍然**崩了**：{_e149} "
                     "⇒ 一条坏门杀掉整趟无人值守")
    except Exception as _e149b:
        fails.append(f"【R149】齿自身异常：{type(_e149b).__name__}: {_e149b}")

    # ★★★★★ **立刻打出来**，不等末尾的 `if fails:`。
    #   实测：注入「过滤过头」时，本齿正确记了一条，
    #   但**后面**一条旧测试（`rr["gates"][0]`）先崩 IndexError
    #   ⇒ 整个自检带栈退出，末尾那段 `if fails:` 根本没执行
    #   ⇒ 红是红了，却**没有点名 R149** —— 我看到的是一句无关的 IndexError。
    #   ★ 这是「齿要排在最前」的加强版：
    #     **排在最前还不够，结论还必须在别人崩之前落地。**
    #     ⇒ 本齿一发现就 flush 打出来，不把结论押在「流程能走到末尾」上。
    for _f149 in [x for x in fails if "【R149】" in str(x)]:
        print(f"  ✗ gate_derive {_f149}", flush=True)

    good = json.loads(json.dumps(_DRY_SPEC))   # compile + 行为断言 + grep 三件套
    dec, reasons = sufficiency_bound(good["gates"], good)
    if not dec:
        fails.append(f"三件套门应判判定层必然，得 reasons={reasons}")

    vacuous = [{"name": "a", "type": "compile", "cmd": ["true"],
                "cwd": ".", "expect_exit": 0},
               {"name": "b", "type": "compile", "cmd": ["true"],
                "cwd": ".", "expect_exit": 0}]
    d2, _ = sufficiency_bound(vacuous, good)
    if d2:
        fails.append("全平凡门(自嗨绿)不得判必然")

    if sufficiency_bound([], good)[0]:
        fails.append("空门集不得判必然")

    soft = [{"name": "c", "type": "compile", "cmd": ["python"],
             "cwd": ".", "expect_exit": 0},
            {"name": "d", "type": "grep_required", "in": "{file}",
             "patterns": ["ok"]}]
    d3, _ = sufficiency_bound(soft, good)
    if d3:
        fails.append("无引用产物的门不得判必然")

    # 沙箱：白名单外命令拒；cwd 越界拒；跳越拒
    evil = [{"name": "x", "type": "compile",
             "cmd": ["rm", "-rf", "/"], "cwd": ".", "expect_exit": 0}]
    safe, vio = check_cmd_safety(evil, Path("."))
    if safe:
        fails.append("白名单外命令应违规")

    esc = [{"name": "e", "type": "compile",
            "cmd": ["python", "-c", "1"], "cwd": "C:/Windows", "expect_exit": 0}]
    safe2, vio2 = check_cmd_safety(esc, Path("."))
    if safe2:
        fails.append("cwd 越界应违规")

    trav = [{"name": "f", "type": "grep_required", "in": "{file}",
             "patterns": ["../etc/passwd"]}]
    safe3, _ = check_cmd_safety(trav, Path("."))
    if safe3:
        fails.append("含裸跳越应违规")

    # 工程判据门：白名单命令 + {repo} 主树根（用与项目无关的 python 演示）
    eng = [{"name": "g", "type": "compile",
            "cmd": ["python", "-c", "pass"], "cwd": "{repo}", "expect_exit": 0}]
    safe4, vio4 = check_cmd_safety(eng, Path("."), repo_root=Path("C:/repo"))
    if not safe4:
        fails.append(f"白名单 compile 门 + {{repo}} 应安全，得 {vio4}")
    safe5, vio5 = check_cmd_safety(eng, Path("."))
    if safe5:
        fails.append("未给 repo_root 时 {repo} 应违规")

    # 非白名单命令应被拦截（不假定项目构建工具）
    bad_g = [{"name": "h", "type": "scan_output",
              "cmd": ["definitely_not_whitelisted", "-x"], "cwd": ".",
              "expect_exit": 0}]
    safe6, vio6 = check_cmd_safety(bad_g, Path("."))
    if safe6:
        fails.append(f"非白名单命令应违规，得 {vio6}")

    # ══════════════════════════════════════════════════════════════
    # ★★★★★ R183：**Windows 上的 shell 也得在白名单里**
    # ══════════════════════════════════════════════════════════════
    # 真证据（第 88 次真跑 · 18:05 one-shot）：规划器给的 7 条门全是
    #   `powershell -Command ...` ⇒ 被整批拒：
    #   `沙箱:门#1/#8/#15/#22/#29 … 顶层命令不被白名单允许：'powershell'`
    #   ⇒ 白白多烧**一整个规划轮**（回喂重来）。
    # ★ 白名单自己的口径是「收**解释器/运行器**」，`bash`/`sh` 在、
    #   `powershell` 不在 ⇒ 这是**在 Linux 写法上做判据**，项目却在 Windows。
    # ★ 沙箱强度不降：收的都是 shell/解释器；挡的是「塞任意可执行名」。
    _ps183 = [{"name": "p", "type": "compile",
               "cmd": ["powershell", "-NoProfile", "-Command", "exit 0"],
               "cwd": ".", "expect_exit": 0}]
    _ok183, _vio183 = check_cmd_safety(_ps183, Path("."))
    if not _ok183:
        fails.append(f"★ R183：powershell 门应过沙箱（Windows 上它就是 shell），"
                     f"得 {_vio183} ⇒ 白名单把 Windows 写法判成不可信、白烧规划轮")
    # ★ 反向边界：**任意可执行名仍必须拒**（不许把白名单放宽成"什么都行"）
    _bad183 = [{"name": "q", "type": "compile",
                "cmd": ["powershell_x", "-c", "1"], "cwd": ".",
                "expect_exit": 0}]
    _ok2_183, _ = check_cmd_safety(_bad183, Path("."))
    if _ok2_183:
        fails.append("★ R183：近似名 `powershell_x` 必须被拒（白名单不许放宽成任意可执行）")


    # ★★★ 工程自带构建工具**自动识别**（2026-10-13 补 · 修"通用工程跑不起来"）
    #   实测缺口：独立 Rust 工程的判据门用 `cargo build`，被沙箱拒
    #   （'cargo' 不在白名单）⇒ **通用工程开发根本跑不起来**。
    import tempfile as _tf
    _d = Path(_tf.mkdtemp(prefix="gd_manifest_"))
    _n_manifest = 0        # ★ 本段**真计数**（此前 total 是硬编码，装饰性的）

    def _mchk(名: str, 条件: bool) -> None:
        nonlocal _n_manifest
        _n_manifest += 1
        if not 条件:
            fails.append(名)

    try:
        import dataclasses as _dc
        from agentscope_orchestrator import profile as _pm
        _old = _pm.get_profile()

        def _替(root: Path, raw: dict):
            """以当前档案为底，只改 repo_root/raw（不构造新 Profile 的全字段）。"""
            return _dc.replace(_old, repo_root=root, raw=raw)

        # ① Cargo.toml ⇒ cargo 进白名单
        (_d / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
        _pm.set_profile(_替(_d, {}))
        _mchk("★ Cargo.toml 存在时 cargo 应在白名单",
              "cargo" in _cmd_whitelist())
        # ② cargo 门应通过沙箱
        _cg = [{"name": "b", "type": "compile", "cmd": ["cargo", "build"],
                "cwd": ".", "expect_exit": 0}]
        _s, _v = check_cmd_safety(_cg, _d)
        _mchk(f"★ 自动识别后 cargo 门应安全（得 {_v}）", _s)
        # ③ ★ 沙箱强度不降：未声明清单时 cargo 仍被拒
        (_d / "Cargo.toml").unlink()
        _pm.set_profile(_替(_d, {}))
        _mchk("★ 无清单文件时不应自动放开 cargo",
              "cargo" not in _cmd_whitelist())
        _s2, _ = check_cmd_safety(_cg, _d)
        _mchk("★ 无清单文件时 cargo 门仍应被拒（沙箱不降）", not _s2)
        # ④ 其它语言清单
        (_d / "go.mod").write_text("module x\n", encoding="utf-8")
        _pm.set_profile(_替(_d, {}))
        _mchk("go.mod 存在时 go 应在白名单", "go" in _cmd_whitelist())
        # ⑤ 档案显式追加仍生效
        (_d / "go.mod").unlink()
        _pm.set_profile(_替(_d, {"gate_cmd_whitelist": ["mytool"]}))
        _mchk("档案 gate_cmd_whitelist 追加应生效",
              "mytool" in _cmd_whitelist())
        # ⑥ 任意可执行仍被拒（模型不能靠清单文件蒙混）
        _mchk("★ 任意可执行不应进白名单",
              "rm" not in _cmd_whitelist()
              and "definitely_not_whitelisted" not in _cmd_whitelist())
        # ⑦ 真实工程（本仓）应自动识别出自己的构建工具
        _pm.set_profile(_old)
        _真 = _cmd_whitelist()
        _mchk("★ 真实档案的构建工具被自动识别（本仓有 Cargo.toml ⇒ cargo）",
              "cargo" in _真)
    finally:
        import shutil as _sh
        _sh.rmtree(_d, ignore_errors=True)

    # 边界卡落位
    rpt = bound_report(good["gates"], good, Path("."))
    if not rpt["decision_necessary"] or not rpt["safe"]:
        fails.append(f"边界卡应为 decision_necessary+safe，得 {rpt}")

    # ★★★★★ R9 判据力：**带路径的白名单程序**必须放行，路径越界的必须拒。
    #   为什么需要这条门：原实现只认裸名 ⇒ 跑本工程自己的 venv 解释器
    #   （最常用、最正当的用法）被无理由拒绝，**逼调用方绕路或写假门**。
    #   安全性质不降低：判的是「程序名在白名单」**且**「路径落在允许根内」。
    #   ★ 计数必须**真计**（`_n_safety`），否则新门加了却不出现在总数里 ——
    #     总数是给人看的"覆盖了多少条"，少算就是**对自己谎报覆盖面**。
    _n_safety = 0

    def _schk(名: str, 条件: bool) -> None:
        nonlocal _n_safety
        _n_safety += 1
        if not 条件:
            fails.append(名)

    with tempfile.TemporaryDirectory() as _td9:
        _根 = Path(_td9)
        _内 = _根 / "venv" / "Scripts"
        _内.mkdir(parents=True)
        _解 = _内 / "python.exe"
        _解.write_text("", encoding="utf-8")
        _外 = _根.parent / "_允许根外_python.exe"
        _放 = [{"name": "venv", "type": "compile",
                "cmd": [str(_解), "-c", "pass"], "cwd": str(_根)}]
        _s1, _v1 = check_cmd_safety(_放, _根, repo_root=_根)
        _schk("★ 允许根内的白名单程序（venv 解释器）应放行", _s1)
        _放2 = [{"name": "外", "type": "compile",
                 "cmd": [str(_外), "-c", "pass"], "cwd": str(_根)}]
        _s2, _v2 = check_cmd_safety(_放2, _根, repo_root=_根)
        _schk("★ 允许根外的同名程序必须拒（安全性质不得降低）", not _s2)
        # 非白名单程序，即使给了路径也必须拒
        _放3 = [{"name": "恶", "type": "compile",
                 "cmd": [str(_根 / "rm.exe"), "-rf", "/"], "cwd": str(_根)}]
        _s3, _v3 = check_cmd_safety(_放3, _根, repo_root=_根)
        _schk("★ 带路径的非白名单程序必须拒", not _s3)
        # 裸名回归：原来能用的不能坏
        _放4 = [{"name": "裸", "type": "compile",
                 "cmd": ["python", "-c", "pass"], "cwd": str(_根)}]
        _s4, _v4 = check_cmd_safety(_放4, _根, repo_root=_根)
        _schk("裸名白名单程序回归了", _s4)

    # 主树工程边界卡（repo 根）落位
    rpt2 = bound_report(eng, good, Path("."), repo_root=Path("C:/repo"))
    if not rpt2["safe"]:
        fails.append(f"主树工程边界卡应为 safe，得 {rpt2}")

    # 门集自洽：必需标记与禁词冲突 → 必不一致 / 归一化消解
    conflict = [{"name": "keep", "type": "grep_required", "in": "{file}",
                 "patterns": ["fn solve"]},
                {"name": "no_keep", "type": "grep_forbidden", "in": "{file}",
                 "patterns": ["fn solve", "bad"]}]
    cok, cwhy = gates_consistent(conflict)
    if cok:
        fails.append("必需标记与禁词等价应判门集不自洽")
    cc, dcn = normalize_gates({"gates": conflict, "name": "t"}, cap=10)
    still = gates_consistent(cc["gates"])
    if not still[0] or dcn["conflict_dropped"] == 0:
        fails.append("归一化应消解必需/禁词等价冲突")
    okc = [g for g in cc["gates"] if g.get("type") == "grep_forbidden"]
    if okc and "bad" not in (okc[0].get("patterns") or []):
        fails.append("冲突消解应去掉等价禁词保留其他禁词")

    # ㉕ A) whole_reading 原语（读整体一次判 O(1)）：标准/绑定格式不敏感、多键一次比对
    with tempfile.TemporaryDirectory() as td:
        w = Path(td)
        (w / "t.rs").write_text("fn main() {\n    let x = 1;\n}\n",
                                encoding="utf-8")
        whole = {"name": "w", "type": "whole_reading", "in": "{file}",
                 "mode": "contains", "must_contain": ["fn main()", "let"]}
        res, aud = g_eval(whole, w / "t.rs", w)
        if not all(res.values()):
            fails.append("whole_reading contains 多键一次比对应全绿")
        (w / "t2.rs").write_text("fn helper() { no_hit }\n", encoding="utf-8")
        res2, _ = g_eval(whole, w / "t2.rs", w)
        if res2.get("w") is not False:
            fails.append("whole_reading contains 缺必需键应红")
        eq = {"name": "e", "type": "whole_reading", "in": "{file}",
              "mode": "equal", "expect_text": "fn main(){\nlet x=1;\n}"}
        res3, _ = g_eval(eq, w / "t.rs", w)
        # 空白/换行在归一化下不影响整体签名（紧致极值判），故应绿
        res4, _ = g_eval(eq, w / "t2.rs", w)
        if not res3.get("e") or res4.get("e") is not False:
            fails.append("whole_reading equal 应空白不变判相等（紧致签名）")
        fb = {"name": "f", "type": "whole_reading", "in": "{file}",
              "mode": "forbidden", "must_not": ["no_hit"]}
        res5, _ = g_eval(fb, w / "t2.rs", w)
        if res5.get("f") is not False:
            fails.append("whole_reading forbidden 命中应红")
    if not (_sig(_canon("a  b")) == _sig(_canon("ab\t\n"))):
        fails.append("canon/sig 应为空白不变紧致签名")

    # ㉖ C) 门集收敛归约：偏扫描多 pattern grep_required → 单一 whole_reading contains
    red_spec = {"name": "t", "gates": [
        {"name": "a", "type": "grep_required", "in": "{file}",
         "patterns": ["fn main()", "let", "build"]}]}
    rr, rn = normalize_gates(red_spec, cap=10)
    wr = [g for g in rr["gates"] if g.get("type") == "whole_reading"]
    if rr["gates"][0].get("type") != "whole_reading" or not wr \
            or wr[0].get("must_contain") != ["fn main()", "let", "build"]:
        fails.append("多 pattern grep_required 应归约为单一 whole_reading contains")
    # 空白/跨行定位键同样归约（whole_reading 内 marker 一并规范化，格式免疫判存在性）
    ws_spec = {"name": "t", "gates": [
        {"name": "a", "type": "grep_required", "in": "{file}",
         "patterns": ["use std", "fn solve"]}]}
    rr2, _ = normalize_gates(ws_spec, cap=10)
    if any(g.get("type") != "whole_reading" for g in rr2["gates"]):
        fails.append("含空白定位键也应归约为 whole_reading（读整体一次判格式免疫）")
    # 归约后判定语义不变：整化内容缺必需键仍红（无虚放）
    with tempfile.TemporaryDirectory() as td2:
        w2 = Path(td2)
        (w2 / "a.py").write_text("def solve(): return 1\n", encoding="utf-8")
        res_r, _ = g_eval(ws_spec["gates"][0], w2 / "a.py", w2)
        # "use std" 在 canon 中不存在 → missing → 红
        if res_r.get("a") is not False:
            fails.append("归约 whole_reading 缺必需键应红（不虚放）")

    goal_total, goal_fails = _goal_selftest()   # 目标驱动偏差判定层（C1–C6）自检
    fails += goal_fails

    # ════════════════════════════════════════════════════════════════
    # ★★★★★ R35：门集上限必须数**不同判据**，不是 (文件 × 门) 的笛卡尔积（5 条）
    # ════════════════════════════════════════════════════════════════
    # 【真跑抓出 · 2026-10-15 · Rust 工程 `example-project`】
    #   机器探针报出 18 处违规散在 7 个文件 ⇒ 规划器按文件列了 7 条交付物，
    #   每条挂同一组共享门 ⇒ `all_gates` = 7 × 6 = **42** > `_MAX_GATES = 40`
    #   ⇒ 规划失败、整趟没跑起来。而 `normalize_gates` 读的是去重后的 6 条、
    #   如实报 `merged: 0, dropped: 0` —— **同一件事两个尺子**（口径分叉）。
    try:
        _一 = {"type": "compile", "name": "g1", "cmd": ["python", "-m", "x"],
              "expect_exit": 0}
        # ① 同一判据重复 100 次 ⇒ 去重后 1 条，且**不撞上限**（多文件是常态）
        _多 = [json.loads(json.dumps(_一)) for _ in range(100)]
        if len(去重门(_多)) != 1:
            fails.append(f"【R35】同一判据重复 100 次去重后应为 1，得 {len(去重门(_多))}")
        _dec多, _r多 = sufficiency_bound(_多, good)
        if any("门集超限" in str(x) for x in _r多):
            fails.append("【R35】★ 多文件任务（同一判据按文件重复）被判「门集超限」"
                         " ⇒ 通用工程开发的常态跑不起来")
        # ② ★★★★★ 齿：**真刷不同门**的模型**照样**撞上限（不是放宽）
        _异 = [{"type": "grep_forbidden", "name": f"n{i}", "in": "a.py",
                "pattern": f"pat{i}"} for i in range(_MAX_GATES + 5)]
        _dec异, _r异 = sufficiency_bound(_异, good)
        if not any("门集超限" in str(x) for x in _r异):
            fails.append(f"【R35】★★ 门无齿：真刷 {len(_异)} 条**不同**门却未判超限"
                         " ⇒ 上限被放宽了（本该防模型塞满跑死）")
        # ③ 去重**不得**改判据语义：`cwd`/`timeout` 差异不算两条（同一条判据）
        _参 = [dict(_一), {**_一, "cwd": "{repo}", "timeout": 900}]
        if len(去重门(_参)) != 1:
            fails.append("【R35】同一条判据仅执行参数不同被当成两条 ⇒ 去重口径过窄")
        # ④ 但**判据语义**不同必须算两条（防去重去过头，把真门吃掉）
        _义 = [dict(_一), {**_一, "cmd": ["python", "-m", "y"]}]
        if len(去重门(_义)) != 2:
            fails.append("【R35】★ 判据语义不同的两条门被去重成一条 ⇒ 真门被吃掉")
        # ⑤ 空/非 dict 输入不得崩（检查必须报告，不能自己崩）
        try:
            if 去重门(None) or 去重门([]) or 去重门(["不是dict", 1, None]):
                fails.append("【R35】空/非法输入未返回空列表")
        except Exception as _e35:      # noqa: BLE001
            fails.append(f"【R35】空/非法输入把去重弄崩了："
                         f"{type(_e35).__name__}: {_e35}")
    except Exception as _e35b:
        fails.append("【R35】门集上限口径门自身异常：%s: %s"
                     % (type(_e35b).__name__, _e35b))

    # ══════════════════════════════════════════════════════════════════
    # 【R95】否定样本必须从 check 搬到门
    # ══════════════════════════════════════════════════════════════════
    # 真跑形态（2026-10-18 · 主心骨第一次端到端）：R92 要求「每条判据自带
    # 一个它必须拒收的样本」，模型照交了 —— 但**门集唯一来源是本文件的
    # `derive_gates(P)`**，而它是**逐键白名单**（只搬 name/type/in/mode/...），
    # `否定样本` 不在白名单里 ⇒ **被静默丢掉** ⇒ 主心骨如实报
    # 「5/5 条门没有否定样本」——**它说的是真话，原因是「我们把它扔了」**。
    # ★ 这是「声明了」与「被使用」之间断了的第 8 层
    #   （R85/R86/R87/R88/R89/R91 同型）。
    try:
        _样本 = [{"旧": "def fib", "新": "def nope", "说明": "删掉定义"}]
        for _kind, _c in (
            ("whole_reading", {"kind": "whole_reading", "name": "w",
                               "file": "f.py", "must_contain": ["def fib"],
                               "否定样本": _样本}),
            ("compile", {"kind": "compile", "name": "c",
                         "cmd": ["true"], "否定样本": _样本}),
            ("scan_output", {"kind": "scan_output", "name": "s",
                             "cmd": ["true"], "否定样本": _样本}),
        ):
            _g = derive_gates({"checks": [_c]}, "")
            if not _g:
                fails.append(f"【R95】{_kind} 没派生出门（夹具本身有问题）")
                continue
            if not _g[0].get("否定样本"):
                fails.append(f"【R95】★ {_kind} 的 `否定样本` **被派生时丢掉了** ⇒ "
                             f"主心骨交了样本也到不了门（真跑已发生）")

        # 反向：没有样本的 check **不得**凭空长出样本（零回退）
        _g2 = derive_gates({"checks": [{"kind": "compile", "name": "c2",
                                        "cmd": ["true"]}]}, "")
        if _g2 and _g2[0].get("否定样本"):
            fails.append("【R95】★ 没声明样本却长出了样本 ⇒ 凭空造契约")

        # 不得把**别的**键也一股脑搬过去（保持白名单语义，只加契约键）
        _g3 = derive_gates({"checks": [{"kind": "compile", "name": "c3",
                                        "cmd": ["true"], "垃圾键": 1}]}, "")
        if _g3 and "垃圾键" in _g3[0]:
            fails.append("【R95】★ 任意键都被搬进门 ⇒ 白名单语义破了（会混入未校验字段）")
    except Exception as _e95:
        fails.append("【R95】否定样本齿自身异常：%s: %s"
                     % (type(_e95).__name__, _e95))

    # ══════════════════════════════════════════════════════════════
    # 【R170】机器有逐件门，就不得再判「交付物没有判据门覆盖」
    # ══════════════════════════════════════════════════════════════
    # 真事故（第 52 次真跑，2026-10-27，连续 3 次重规划后按设计停机）：
    #   `交付物**没有判据门覆盖**：['bin/TS827_结构自生成.rs',
    #     'src/模块甲/持久化入口.rs']`
    #   而同一趟日志里机器自己写着：
    #     `✓ src/模块甲/持久化入口.rs：该文件主体的门 2 → 0 ⇒ 保留`
    # ★ 齿要测**两件事**，少一件就是另一种不诚实：
    #   ① 声明 `逐件已覆盖=True` ⇒ **不得**再因覆盖拒收（否则死锁照旧）；
    #   ② 不声明（默认）⇒ **必须**照旧拒收（否则等于把 R104 删了）。
    # ★ 夹具必须**不含** `{file}`/`{work}`——否则 `_通用104` 直接跳过覆盖检查，
    #   齿会因为夹具自己把被测逻辑短路而永远绿（那是假绿）。
    _n170 = 3
    try:
        _门170 = [{"name": "cargo_check_lib", "type": "compile",
                  "cwd": "{repo}", "cmd": ["cargo", "check", "--lib"],
                  "expect_exit": 0}]
        _规170 = {"deliverables": [{"path": "src/a.rs"}]}
        _因170a = sufficiency_bound(_门170, _规170, 逐件已覆盖=True)[1]
        if any("没有判据门覆盖" in str(r) for r in _因170a):
            fails.append("【R170】★ 声明了逐件门仍判「没有判据门覆盖」"
                         " ⇒ 死锁照旧（第 52 次真跑的停机原因）")
        _因170b = sufficiency_bound(_门170, _规170)[1]
        if not any("没有判据门覆盖" in str(r) for r in _因170b):
            fails.append("【R170】★ 默认档也不判覆盖缺口了 ⇒ 等于把 R104 删掉")
        _根170 = Path(tempfile.mkdtemp())
        _报170 = bound_report(_门170, _规170, _根170, 逐件已覆盖=True)
        if any("没有判据门覆盖" in str(r) for r in _报170["reasons"]):
            fails.append("【R170】★ `bound_report` 没把 `逐件已覆盖` 透传下去")
    except Exception as _e170:
        fails.append("【R170】齿自身异常：%s: %s"
                     % (type(_e170).__name__, _e170))

    if fails:
        print("gate_derive 自检红：")
        for f_ in fails:
            print(f"  ✗ {f_}")
        return 1
    # ★ R170：分母必须显式加 `_n170`，不许写成算不出来的常数
    total = 23 + goal_total + _n_manifest + _n_safety + 5 + 3 + _n170   # +3 = R149 三齿 / +_n170 = R170 三齿
    print(f"gate_derive 自检：{total}/{total} 全绿")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_selftest())