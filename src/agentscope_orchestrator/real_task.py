"""真实任务闭环（项目无关）：N 窗并发生成产物 → 机器判据门 → 收敛裁决 → 写回。

一切项目专有内容（产物库路径、模板、提示词、判据门、命名空间）全部来自档案
（profile）。本模块**不含任何项目名 / 路径 / 语言 / 命令**。

判据门（全部机器可算，由档案声明）：
  例：shell_ok / no_forbidden / statement_intact / no_sorry_axiom。
  评估器 gate_spec.evaluate 语言无关；换项目只换档案里的 gates 数据。

收敛：通过窗数 ≥ min_pass 则采纳首个通过窗产物；否则任务判失败。

用法：
    python -m agentscope_orchestrator.real_task --n 3 [--channel aaa] [--promote]
    ... --profile path/to/orchestrator.profile.yml
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from .msgs import UserMsg

# ★ R157：通道一律走 `build_adaptive_router` ⇒ 这两个不再使用。
#   （`Router` 仍作类型注解用，保留。）
from agentscope_orchestrator.gate_spec import (evaluate, evaluate_async,
                                               validate as gate_spec_validate)
from agentscope_orchestrator.router import Router
from agentscope_orchestrator import math_model
from agentscope_orchestrator import profile as prof_mod
from agentscope_orchestrator import failure_feedback

logging.basicConfig(level=logging.WARNING)

# 失败反馈环最大轮数：每轮都真调模型 + 真跑门，故设硬上限防无限烧。
_FEEDBACK_MAX_ROUNDS = 2

# ── 日志抽象：CLI 用 print；MCP stdio 侧覆盖为 stderr logger（stdout 是协议通道） ──
_log_fn = print


def _log(*args) -> None:
    _log_fn(*args)


def _p() -> prof_mod.Profile:
    return prof_mod.get_profile()


# ── 目录（全部由档案决定；此处仅为便捷访问器，非硬编码） ────────
class _Dirs:
    """档案派生目录的惰性访问器（每次读当前档案，支持运行期换档案）。"""

    @property
    def ORCH(self) -> Path:
        return prof_mod.TOOL_ROOT

    @property
    def ARTIFACT(self) -> Path | None:
        return _p().artifact.root

    @property
    def WORKDIR(self) -> Path:
        d = _p().work_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def PRODUCTS(self) -> Path:
        d = _p().products_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def AUDIT(self) -> Path:
        d = _p().audit_dir
        d.mkdir(parents=True, exist_ok=True)
        return d


DIRS = _Dirs()

# 兼容旧名（外部代码可能引用 rt.ORCH / rt.PRODUCTS）
ORCH = prof_mod.TOOL_ROOT


def __getattr__(name: str):
    """模块级兼容层：把档案里的路径暴露为模块属性。"""
    if name == "ARTIFACT_DIR":
        return _p().artifact.root
    if name == "PRODUCTS":
        return DIRS.PRODUCTS
    if name == "WORKDIR":
        return DIRS.WORKDIR
    if name == "AUDIT":
        return DIRS.AUDIT
    if name == "FORMAL_PROOF":
        root = _p().artifact.root
        return root.parent if root else None
    # ★★★★★ 修复 R9（**真缺陷**）：补上 `REPO_ROOT`。
    #   `taixu_mcp` 的 MCP 入口调 `check_cmd_safety(..., repo_root=rt.REPO_ROOT)`
    #   来放开 `{repo}` 主树工程任务的 cwd —— 但本兼容层**从来没暴露过**这个名字
    #   ⇒ `run_errand` / `submit_task` **每次调用都 AttributeError**，
    #     MCP 返回 "Error executing tool 示例·任意任务: REPO_ROOT"。
    #   ★ 与本模块自身的设计承诺一致（"把档案里的路径暴露为模块属性"）：
    #     `repo_root` 就是档案里的一个路径，本就该在这里暴露。
    #   ★ 为什么静态门没抓到：本模块的门扫的是**全局名**（`LOAD_GLOBAL`），
    #     而这是**属性读**（`rt.REPO_ROOT`）—— 同一类缺陷深了一层。
    #     已把这一类也做成门（`static_audit.missing_module_attrs`），
    #     并且**端到端协议验证**（真起服务、真调工具）才是抓它的根本手段：
    #     这一类只有"真的走一遍"才现形。
    if name == "REPO_ROOT":
        return _p().repo_root
    raise AttributeError(name)


@dataclass
class TaskSpec:
    id: str
    unit_id: str
    module: str
    theorem: str
    statement_marker: str
    target_rel: str          # 相对产物库根的产物路径
    template: str = ""
    min_pass: int = 2
    n_windows: int = 3
    gates: list[dict] | None = None   # 判据门 GateSpec；None=档案默认门
    prompt: str = ""                  # 项目提示词；空=档案 default_task.prompt
    namespace: str = ""               # 生成命名空间；空=档案默认


def _task_source(task: TaskSpec) -> Path | None:
    """把 task.unit_id 解析为**真实存在的源码文件**（供失败反馈抽真实符号）。

    unit_id 形如 `NS_Millennium_Stages.RealQuotient` 或 `src.queue`：
    按档案源码根逐个拼相对路径试后缀（.lean/.rs/...），命中即返回。
    解析不到 ⇒ None（反馈环退化为只回喂编译器原文，仍可用）。
    """
    prof = _p()
    rid = (task.unit_id or "").replace("\\", "/").strip("/")
    if not rid:
        return None
    roots = []
    try:
        for r in (prof.source.roots or ()):
            roots.append(Path(r) if Path(r).is_absolute() else prof.repo_root / r)
    except Exception:  # noqa: BLE001 档案缺 source 段 ⇒ 退化
        roots = []
    if prof.artifact and prof.artifact.root:
        roots.append(Path(prof.artifact.root))
    if not roots:
        roots = [prof.repo_root]
    # 后缀候选：档案 glob 决定（默认 .lean）
    后缀 = [".lean", ".rs", ".py"]
    try:
        g = str((prof.artifact.glob if prof.artifact else "") or "").strip()
        if g:
            s = Path(g).suffix
            if s and s in 后缀:
                后缀.insert(0, 后缀.pop(后缀.index(s)))
    except Exception:  # noqa: BLE001
        pass
    parts = rid.split(".")
    for root in roots:
        for suf in 后缀:
            # ① 整条点分键当路径：a/b/c.ext
            cand = root / (("/".join(parts)) + suf)
            if cand.is_file():
                return cand
            # ② 末段当文件名（多数单元键以文件名结尾）
            if parts:
                cand2 = root / (parts[-1] + suf)
                if cand2.is_file():
                    return cand2
            # ③ 末段可能带路径：a.b/c
            if len(parts) >= 2:
                cand3 = root / parts[-2] / (parts[-1] + suf)
                if cand3.is_file():
                    return cand3
    return None


def _default_task() -> TaskSpec:
    """从档案读默认任务（无档案 ⇒ 空任务，调用方须显式给全字段）。"""
    d = dict(_p().default_task or {})
    return TaskSpec(
        id=str(d.get("id") or "t001"),
        unit_id=str(d.get("unit_id") or ""),
        module=str(d.get("module") or ""),
        theorem=str(d.get("theorem") or ""),
        statement_marker=str(d.get("statement_marker") or ""),
        target_rel=str(d.get("target_rel") or ""),
        template=str(d.get("template") or ""),
        prompt=str(d.get("prompt") or ""),
        min_pass=int(d.get("min_pass") or 2),
        n_windows=int(d.get("n_windows") or 3),
    )


# 兼容旧名 rt.TASK（惰性：首次访问时按当前档案构造）
class _LazyTask:
    _cache: TaskSpec | None = None

    def _get(self) -> TaskSpec:
        if self._cache is None:
            self._cache = _default_task()
        return self._cache

    def __getattr__(self, k):
        return getattr(self._get(), k)


TASK = _LazyTask()


def lean_native_gates(task: TaskSpec) -> list[dict]:
    """档案声明的默认判据门（数据来自档案；评估器一行不改）。

    占位符替换分两层：
      · 任务级 {theorem}/{marker}/{module} —— 本函数填
      · 路径级 {artifact_parent}/{repo}/{artifact} —— 本函数也填（评估器只认
        {file}/{work}/{src}，故档案里的路径占位符必须在交给评估器前消解）
    """
    raw = (_p().default_task or {}).get("lean_gates") or []
    if not raw:
        return []
    art = _p().artifact.root
    mapping = {
        "theorem": task.theorem,
        "marker": task.statement_marker,
        "module": task.module,
        "artifact_parent": str(art.parent) if art else str(DIRS.WORKDIR),
        "artifact": str(art) if art else "",
        "repo": str(_p().repo_root),
    }

    def sub(o):
        if isinstance(o, str):
            for k, v in mapping.items():
                o = o.replace("{" + k + "}", v)
            return o
        if isinstance(o, list):
            return [sub(x) for x in o]
        if isinstance(o, dict):
            return {k: sub(v) for k, v in o.items()}
        return o

    return sub([dict(g) for g in raw])


def _strip_fence(text: str) -> str:
    """去掉模型输出常见的 markdown 围栏与前后空白。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```\w*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_text(content) -> str:
    parts: list[str] = []
    for block in content:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _build_prompt(task: TaskSpec, 反馈: str = "") -> str:
    """任务提示词 = 任务自带（优先）或档案 default_task.prompt，注入模板与机制基线。

    反馈：上一轮失败时由 `failure_feedback` 生成的**机器原文**段，追加在末尾。
    为空 ⇒ 首轮，提示词与原先逐字一致（不改变既有行为）。
    """
    tmpl = task.prompt or str((_p().default_task or {}).get("prompt") or "")
    if not tmpl:
        # 无提示词模板：退化为最小可用提示（不猜项目语义）
        tmpl = ("完成下面的产物文件，把 `sorry` 替换为真实实现。\n"
                "只输出完整文件内容，前后不要解释文字、不要代码块围栏。\n\n"
                "```\n{TEMPLATE}\n```")
    base = tmpl.format(TEMPLATE=task.template,
                       MECH=math_model.get_charter())
    return base + 反馈 if 反馈 else base


async def _window(router: Router, idx: int, task: TaskSpec, dry: bool,
                  dry_body: str = "", 反馈: str = "") -> dict:
    """单窗：模型产出完整模块 → 落盘 → 判据门。dry 模式写 dry_body。

    反馈：非空 ⇒ 本窗是**改进重试**，提示词带上上一轮的机器原文报错。
    """
    # ★ 烧字计量（2026-10-13 补）：此前**完全没有**计量 ⇒ 无人值守护栏的
    #   "成本失控 ⇒ 停机"那条判据**永远不触发**（拿到的一直是 0，是死判据）。
    #   实测发现：`task_status` 不返回 tokens_chars，而 real_task 也不记 ⇒
    #   护栏里那条是**装饰**。现按"提示词 + 回复"字符数如实计量（与
    #   `generic_goal._tok_chars` 同口径），供 `guard.护栏.记烧字` 用。
    chars = 0
    if dry:
        body = dry_body or "  sorry"
        text = task.template.replace("  sorry\n", body + "\n")
    else:
        prompt = _build_prompt(task, 反馈)
        resp = await router.call([UserMsg(name="user", content=prompt)])
        text = _strip_fence(_extract_text(resp.content))
        chars = len(prompt) + len(str(resp.content))

    cand = DIRS.WORKDIR / f"{task.id}_w{idx}.out"
    cand.write_text(text, encoding="utf-8")
    spec = task.gates or lean_native_gates(task)
    # ★ 用 evaluate_async：门是同步 subprocess（可能跑数分钟），在协程里直接调会冻住
    #   整个事件循环，令同进程的 MCP 服务器收不到/发不出任何消息。
    art_parent = DIRS.ARTIFACT.parent if DIRS.ARTIFACT else DIRS.WORKDIR
    results, audit = await evaluate_async(spec, cand, DIRS.WORKDIR, art_parent)
    # ★★★★★ R14：走**数术层格裁决**（TS-468 §6）。取值与 `all` 一致（无回退），
    #   但把**因由**带回给修复环 —— `all` 只回一个 Bool，模型因此不知道
    #   "是哪个门红的、为什么红"，只能盲改。
    from agentscope_orchestrator import shushu_logic as _sl
    _值, _因由, _注 = _sl.从审计(results, audit)
    passed = bool(_值)
    return {"idx": idx, "passed": passed, "gates": results, "audit": audit,
            "裁决因由": _因由, "裁决注": _注,
            "text": text, "chars": chars}


def 可及数(passed: list[dict]) -> tuple[int, list[str]]:
    """**可及性计数**：只认**内容相异**的通过窗（取代裸计数 `len(passed)`）。

    ★★★★★ 为什么（TS-468 §4 · TS-429 的裁定）：
      「增信」是错的框架，「**可及性**」才对。裸计数把「同一个解被找到两次」
      也算成两份证据 —— 但两份**逐字相同**的产物不是两条独立路径，
      是**一条路径数了两遍**。可及性只认**内容相异**的通过窗：
      逐字重复的窗**没有**「独立路径」这个**结构见证** ⇒ 不增可及。

    ★ 用 `shushu_logic.增信格` 记录：每个相异内容让**一位**上升（带见证），
      并保证**沿格单调**（不得下降）；重复窗**不上升**。
    ★ 行为等价：所有窗内容互不相同时 `可及数 == len(passed)` ⇒ 与旧判据一致。

    返回 `(可及数, 重复说明)`。
    """
    from agentscope_orchestrator import shushu_logic as _sl
    n = max(len(passed), 1)
    g = _sl.增信格(n=n)
    见: dict = {}
    重复: list[str] = []
    for r in passed:
        文 = str(r.get("text") or "")
        if 文 in 见:
            重复.append(f"w{r.get('idx')} 与 w{见[文]} 产物逐字相同 ⇒ 不增可及"
                        "（同一条路径数了两遍，不是两条独立路径）")
            continue
        见[文] = r.get("idx")
        态 = [_sl.TRUE] * len(见) + [_sl.FALSE] * (n - len(见))
        坏 = g.上升(tuple(态), {"窗": r.get("idx"),
                                "见证": "产物内容与其它通过窗相异"})
        if 坏:
            重复.append(f"w{r.get('idx')} 可及上升被拒：{坏}")
    return g.见证数(), 重复


async def _window_list(router: Router, indices: list[int], task: TaskSpec,
                       dry: bool, dry_body: str = "",
                       反馈: str = "") -> list[dict]:
    """并行跑指定窗（空列表直接返回，供续跑时省去整段 gather）。"""
    if not indices:
        return []
    return await asyncio.gather(*(_window(router, i, task, dry, dry_body, 反馈)
                                  for i in indices))


async def run_task(task: TaskSpec, n_windows: int, channel_name: str,
                   promote: bool, dry: bool, status: dict | None = None,
                   resume_windows: dict[int, dict] | None = None) -> int:
    """运行 N 窗管线。resume_windows: {idx: {passed,text,gates,audit}} 中断点续跑时
    已通过窗的子任务幂等复用——只重跑未跑/失败窗，不重调模型、不重跑该窗门。"""
    prof = _p()
    if status is not None:
        status.update({"id": task.id, "unit_id": task.unit_id,
                       "state": "running", "module": task.module,
                       "theorem": task.theorem, "promote": promote,
                       "profile": prof.name,
                       "resumed": bool(resume_windows)})
    # ★★★★★ R157：**改走统一的自适应通道入口**（见 `gateway.build_adaptive_router`）。
    #   原来这里是静态的 `select_channels` + 每通道只取 `models[0]`
    #   ⇒ 死通道会被交给 Router，同通道的其余模型永远用不到。
    from agentscope_orchestrator.gateway import build_adaptive_router
    router, _槽们157, _报告157 = build_adaptive_router(
        channel_name, probe=not dry)

    t0 = time.time()
    _log(f"[real_task] {task.id} unit={task.unit_id} "
         f"target={task.module}.{task.theorem} windows={n_windows} dry={dry} "
         f"channels={router.channel_names()} "
         f"resume={sorted((resume_windows or {}).keys())}")

    # 断点续跑：已通过窗直接复用，只补跑缺省窗
    resume = resume_windows or {}
    to_run = [i for i in range(n_windows) if i not in resume]
    dry_body = str((prof.default_task or {}).get("dry_body") or "")
    fresh = [dict(r) for r in await _window_list(router, to_run, task, dry,
                                                 dry_body)]
    results = [resume[i] for i in range(n_windows) if i in resume] + fresh
    results = [r for r in sorted(results, key=lambda x: x["idx"])]
    passed = [r for r in results if r["passed"]]
    # ★★★★★ R15：判据从**裸计数**换成**可及数**（TS-468 §4 / TS-429 的裁定）。
    #   「增信」是错的框架，「可及性」才对：只认**内容相异**的通过窗 ——
    #   逐字重复的窗不是两条独立路径，是一条数了两遍。
    可及, 重复窗 = 可及数(passed)
    if 重复窗:
        for _x in 重复窗:
            _log(f"[real_task] {_x}")

    # ══════════════════════════════════════════════════════════════
    # ★ 失败反馈环（G6 缺口修复）
    #   窗口不足 min_pass ⇒ **带着机器原文报错**改进重试，而不是原样重跑。
    #   实测缺陷（2026-10-13）：原实现直接 return 1，resume 复用旧窗后
    #   模型收到一模一样的提示 ⇒ realMul_nonneg 连跑 3 次三次同错。
    #   判据仍全机器可算：回喂的是编译器原文 + 源码真实符号，非模型评价。
    # ══════════════════════════════════════════════════════════════
    反馈轮次 = 0
    反馈历史: list[dict] = []
    # ★ 烧字累加器（**含被丢弃的窗**）：反馈环会用新窗**替换**旧窗（L400），
    #   若只对最终 results 求和，则被替换掉的窗烧的字**全部漏计** ⇒
    #   成本护栏会低估。成本要按**真实花掉的**算，不按"留下来的"算。
    总烧字 = sum(int(r.get("chars") or 0) for r in results)
    while not dry and 可及 < task.min_pass:
        if 反馈轮次 >= _FEEDBACK_MAX_ROUNDS:
            _log(f"[real_task] 失败反馈环已达上限 {_FEEDBACK_MAX_ROUNDS} 轮，停止")
            break
        # 取本轮最差窗的信号（门最少的那个）作为改进依据
        最差 = min(results, key=lambda r: sum(1 for v in r["gates"].values() if v))
        信号 = failure_feedback.抽取错误信号(最差.get("audit"))
        if failure_feedback.信号是否为空(信号):
            # 门红了但没给出可操作信号 ⇒ 诚实停止，不假装能改进
            _log(f"[real_task] 失败反馈环：w{最差['idx']} 门红但无机器可操作信号，停止")
            break
        # 源码真实符号：供模型替换幻觉名（防再犯同一个错）
        符号 = failure_feedback.可用符号(_task_source(task))
        if not 符号:
            符号 = failure_feedback.可用符号(prof.artifact.root)
        反馈 = failure_feedback.构造反馈提示(信号, 符号, 最差.get("gates"))
        新提示 = _build_prompt(task, 反馈)
        旧提示 = _build_prompt(task, "")
        if not failure_feedback.反馈是否有效(旧提示, 新提示):
            _log("[real_task] 失败反馈环：新提示与旧提示相同 ⇒ 反馈无效，停止")
            break
        反馈轮次 += 1
        _log(f"[real_task] 失败反馈环 第{反馈轮次}轮："
             f"未知名={信号['未知名'][:4]} 编译报错={len(信号['编译报错'])}条 "
             f"符号表={len(符号)}个")
        反馈历史.append({"round": 反馈轮次, "failed_window": 最差["idx"],
                         "信号": 信号, "符号数": len(符号)})
        # 重跑**全部未过窗**（带反馈）；已过窗保持复用（幂等，不浪费模型调用）
        重跑 = [i for i in range(n_windows) if i not in resume]
        if not 重跑:
            重跑 = [最差["idx"]]
        新窗 = [dict(r) for r in await _window_list(router, 重跑, task, dry,
                                                     dry_body, 反馈)]
        总烧字 += sum(int(r.get("chars") or 0) for r in 新窗)   # 累加（含被替换的）
        按序 = {r["idx"]: r for r in 新窗}
        results = [按序.get(r["idx"], r) for r in results]
        results = [r for r in sorted(results, key=lambda x: x["idx"])]
        passed = [r for r in results if r["passed"]]
        # ★ R15：每轮重算可及数（判据是**可及性**，不是计数）
        可及, _新重复 = 可及数(passed)
        if _新重复:
            for _x in _新重复:
                _log(f"[real_task] {_x}")

    _log(f"[real_task] windows={len(results)} passed={len(passed)} "
         f"可及={可及} failed={len(results) - len(passed)} "
         f"elapsed={time.time() - t0:.1f}s")
    for r in results:
        g = "  ".join(f"{k}={v}" for k, v in r["gates"].items())
        _log(f"[real_task]   w{r['idx']}: {'PASS' if r['passed'] else 'FAIL'} | {g}")
    if status is not None:
        # ★ 烧字如实计量：**含反馈环被替换掉的窗**（按真实花掉的算）
        _烧 = 总烧字
        status.update({"state": "windows_done", "passed": len(passed),
                       "可及": 可及, "重复窗": 重复窗,
                       "failed": len(results) - len(passed),
                       "tokens_chars": _烧,
                       "windows": [{"idx": r["idx"], "passed": r["passed"],
                                    "text": r["text"]} for r in results],
                       "gates": [{"idx": r["idx"], "passed": r["passed"],
                                  "gates": r["gates"]} for r in results],
                       "audit": [{"idx": r["idx"], "audit": r["audit"]}
                                 for r in results],
                       "feedback": 反馈历史})
        _log(f"[real_task] 烧字计量：{_烧} 字符（{len(results)} 窗）")

    # 收敛裁决：通过窗 ≥ min_pass 采纳首个通过窗
    if 可及 < task.min_pass:
        _log(f"[real_task] CONVERGENCE_FAILED min_pass={task.min_pass} "
             f"passed={len(passed)} feedback_rounds={反馈轮次}")
        if status is not None:
            status.update({"state": "convergence_failed", "min_pass": task.min_pass,
                           "feedback_rounds": 反馈轮次})
        return 1

    product_text = passed[0]["text"]
    if promote:
        art = prof.artifact.root
        if art is None:
            _log("[real_task] ERROR 档案未配置产物库（artifact.root）⇒ 拒绝 promote")
            if status is not None:
                status.update({"state": "error",
                               "error": "档案未配置产物库，拒绝写回"})
            return 2
        if not art.is_dir():
            _log(f"[real_task] ERROR 产物库目录不存在: {art}")
            if status is not None:
                status.update({"state": "error",
                               "error": f"产物库目录不存在: {art}"})
            return 2

        # ★ 闸：target_rel 可能由模型/外部给出，必须做包含性校验，
        #   否则 `../../` 或绝对路径可写到库外任意位置。
        target = prof.artifact_path(task.target_rel)
        if target is None:
            _log(f"[real_task] ERROR target_rel 越出产物库根：{task.target_rel}")
            if status is not None:
                status.update({"state": "error",
                               "error": f"target_rel 越界（拒绝写库外）：{task.target_rel}"})
            return 4

        # ★ 事务化写回：备份 → 原子替换 → 复验 → 失败回滚。
        backup = target.with_suffix(target.suffix + ".pre_promote.bak")
        had_old = target.is_file()
        if had_old:
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        tmp = target.with_suffix(target.suffix + ".promote_tmp")
        try:
            tmp.write_text(product_text, encoding="utf-8")
            os.replace(tmp, target)          # 原子替换：读者要么见旧、要么见新
            # 对写回产物复跑全部门
            spec = task.gates or lean_native_gates(task)
            art_parent = art.parent
            final, _ = await evaluate_async(spec, target, DIRS.WORKDIR, art_parent)
            _log(f"[real_task] PROMOTED -> {target}")
            _log(f"[real_task]   final gates: "
                 + "  ".join(f"{k}={v}" for k, v in final.items()))
            if not all(final.values()):
                # 回滚：绝不让未过门的产物留在库里
                if had_old:
                    os.replace(backup, target)
                    _log("[real_task] PROMOTED file failed final gates → 已回滚原文件")
                else:
                    target.unlink(missing_ok=True)
                    _log("[real_task] PROMOTED file failed final gates → 已删除新文件")
                if status is not None:
                    status.update({"state": "error",
                                   "error": "promoted file failed final gates（已回滚）",
                                   "final_gates": final, "rolled_back": True})
                return 3
            if had_old:
                backup.unlink(missing_ok=True)
        finally:
            tmp.unlink(missing_ok=True)
        if status is not None:
            status.update({"state": "promoted", "product": str(target),
                           "adopted_window": passed[0]["idx"],
                           "final_gates": final})
    else:
        # 未 promote：产物落 products/ 暂存，供人工复核
        p = DIRS.PRODUCTS / task.target_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(product_text, encoding="utf-8")
        _log(f"[real_task] product staged -> {p} (not promoted, use --promote)")
        if status is not None:
            status.update({"state": "staged", "product": str(p),
                           "adopted_window": passed[0]["idx"]})

    _log(f"[real_task] CONVERGED: adopted w{passed[0]['idx']} "
         f"({len(passed)}/{n_windows} windows passed)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="真实任务闭环（项目无关）")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--channel", default="",
                    help="锁定频道，支持逗号分隔多个（空=全通道）")
    ap.add_argument("--promote", action="store_true", help="产物写回档案产物库")
    ap.add_argument("--dry", action="store_true", help="不真对话")
    ap.add_argument("--profile", default=None, help="项目档案路径")
    a = ap.parse_args()
    prof_mod.reset_profile(a.profile)
    task = _default_task()
    n = a.n or task.n_windows
    return asyncio.run(run_task(task, n, a.channel, a.promote, a.dry))


if __name__ == "__main__":
    raise SystemExit(main())
