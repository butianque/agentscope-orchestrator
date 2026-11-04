"""G7 全自动引擎：全库扫描建记忆索引 → 自动选题 → 模板生成 → 无人值守循环。

三段职责（机械机制，非"模型审定模型"）：
  scan_all()         三种源各建一份记忆索引：
                     - formal  形式化产物库   *.lean（复用 UnitIndex.build_artifact 点式键）
                     - code    代码             *.py（排除依赖/工具/审计脏目录）
                     - task    任务书           *.md（排除脏目录 + 语义收窄）
                     kind -> key -> path。
  select_unclosed()  从 formal 索引选"未闭合声明"：块正则提 theorem|lemma，
                     块内含 `sorry`/`admit` 或 `:= by` 后无本体 => 候选。
                     按依赖缺口（引用库内其他定理多者优先）排序。
  gen_lean_candidate() 把选中声明隔离成新模板（新 namespace + import 原模块 +
                     `open` 原命名空间 + 原陈述逐字保留 + `by\n  sorry` +
                     Lean 原生四条 GateSpec）。

循环：MCP 的 auto_* 工具把候选转 TaskSpec 提交进 _jobs/_schedule
（priority/depends_on/gates 已支持），判据门 + 收敛兜底，过门才写回。

诚实边界：只证"能扫/能选/能生成可编译模板"，不承诺自动产物无 bug；
判据门 + 收敛把守，未过门不写回。
"""

from __future__ import annotations

import os
from fnmatch import fnmatch

import re
from dataclasses import dataclass, field
from pathlib import Path

from agentscope_orchestrator.index import UnitIndex

_NOISE_PARTS = (
    ".venv", ".tools", ".audit_trail", ".lake", ".git", "node_modules",
    ".cargo", ".cargo_home", ".semsearch", ".semsearch_index",
    "_archive", "_backup", ".backups", "_lean_scratch", ".bak",
    ".trae", "jobs", "products", "work", "config",
)
_TASK_KEYS = ("任务", "需证", "未闭合", "待办", "todo", "formalize", "证明")

_BLOCK_RE = re.compile(
    r"(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_']*)\b(.*?)(?=^\s*(?:theorem|lemma)\b|^\s*end\b|\Z)",
    re.MULTILINE | re.DOTALL)
_MARKER_RE = re.compile(r"^(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_']*)\b(.*)",
                        re.MULTILINE | re.DOTALL)


def _陈述_of(块: str, 名: str) -> str | None:
    """从定理块里抽出**完整陈述**（签名 + 可能跨行的返回类型），去掉证明体。

    ★ 为什么需要它（2026-10-13 实测缺陷）：
      原实现直接用 `_MARKER_RE` 的 group(3)，而该组 `re.MULTILINE` 下 `(.*)`
      **止于第一个换行** ⇒ 陈述若跨行，返回类型被整段截掉。实测铁证
      （`NS_Millennium_Stages/RealQuotient.lean :: realMul_nonneg`）：

          源文件:  theorem realMul_nonneg {a b : Real} (ha : …) (hb : …) :
                       realLE realZero (realMul a b) := by
          抓到:    ' {a b : Real} (ha : …) (hb : …) :'      ← 返回类型没了

      后果：① 喂给模型的模板陈述残缺 ⇒ 生成的证明对不上；
            ② `statement_intact` 门守的是**残缺陈述** ⇒ 门形同虚设。

    做法：取签名到**首个证明起始**（`:=` / `where`）之间的全部内容，
          再把换行合并为空格（Lean 陈述允许跨行，合并后语义等价）。
    """
    # ★ 注意组号：`_MARKER_RE` = (kind)(name)(rest) ⇒ 签名是 group(3)，不是 group(2)。
    m = _MARKER_RE.search(块)
    if not m or m.group(2) != 名:
        # 块首不匹配时退回：在块内按名字找
        m = re.search(rf"^(theorem|lemma)\s+{re.escape(名)}\b(.*)",
                      _剥注释(块), re.MULTILINE | re.DOTALL)
        if not m:
            return None
    sig = m.group(3) if m.re is _MARKER_RE else m.group(2)
    # 截到证明体之前：`:=` 或 `where`（取最先出现者）
    切点 = len(sig)
    for 标记 in (" := ", ":=", " where", "\nwhere"):
        i = sig.find(标记)
        if i != -1:
            切点 = min(切点, i)
    sig = sig[:切点]
    # 换行合一（Lean 陈述跨行合法，合并后等价），压缩多余空白
    return re.sub(r"\s+", " ", sig).strip()


def _剥注释(源: str) -> str:
    """去掉行注释与块注释，防注释里的 `:=` 干扰陈述切分。"""
    src = re.sub(r"/-.*?-/", " ", 源, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", src)
_SORRY_RE = re.compile(r"\bsorry\b|\badmit\b")

# ★★★ 「证明体为空」判据（2026-10-13 修 · **候选表被灌满 99.3% 的根因**）。
#
# 原实现：`re.search(r"\)?\s*:=\s*by\s*$", block, re.MULTILINE)`
#   ★ `re.MULTILINE` 让 `$` 匹配**行尾**而不是**串尾** ⇒ 于是**任何**写成
#     ```lean
#     theorem t : P := by
#       <证明>
#     ```
#     的**已证明**定理，第一行都以 `:= by` 结尾 ⇒ 全被判"未闭合"。
#
# 实测（活库 13035 个定理块）：**8063 个**被这条命中；
#   候选表实际规模 **7921** 个，其中**真有 `sorry`/`admit` 的只有 58 个**，
#   其余 **7863 个（99.3%）是假候选**（已证明完的）。
#   ⇒ 编排器**一直在重新证明已经证明好的东西**：
#     实测同一定理 staged **8 次**、另一个 failed **3 次**，**永不收敛**。
#
# 修法：要判的是"**证明体为空**"（模板没填），即定理块**以 `:= by` 结束**、
#   后面什么都没有 ⇒ 用 `\Z`（串尾），不是 `$`（行尾）。**不再传 MULTILINE**。
_EMPTY_BODY_RE = re.compile(r":=\s*by\s*\Z")


def _去注释(文本: str) -> str:
    """剥掉 Lean 注释，只留**代码**（读代码，不读散文）。

    ★★★ 为什么需要（2026-10-13 实测缺陷 · **重做永动机的根因**）：

      `_SORRY_RE` 直接扫定理块文本，而 `_BLOCK_RE` 对**文件里最后一个定理**
      会把文件余下全部吞进块里 —— 实测 `TS375.global_ledger375` 的块长
      **17830 字符**（该定理本身只有 7 行）。于是文档注释里**用散文提到**
      `sorry`（原文：「T8 初稿在单射侧写了 **`sorry`**」）被当成**代码里的
      sorry** ⇒ `unclosed = True` ⇒ **已闭合的定理被永远当"未闭合"重选**。

      实测后果：`siZhuInfinite_ne_empty_of_pos` 被 **staged 8 次**、
      `global_ledger375` **2 次** —— 同一个定理反复重做，**永远不收敛**。

      ⇒ 这是「**把散文当代码读**」—— 与判据门被注释骗过（`statement_intact`
        读文本）是**同一个病**，修法也**同一条**：**换读法**（TS-430 重定位）。
        门那边搬到"读内核类型"，这边搬到"读代码而非散文"。

    ★ 诚实边界：本剥离器是**语法层**的（不解析 Lean 词法）。
      已知不处理的：字符串字面量里的 `--`（Lean 里 `"--"` 会被误当行注释起点）。
      该情形在定理块里极罕见，且**方向是保守的**（多剥 ⇒ 可能漏判 unclosed
      ⇒ 宁可漏报"未闭合"，不会误报"已闭合"）。
    """
    out: list[str] = []
    i, n = 0, len(文本)
    深度 = 0
    while i < n:
        # 块注释 `/- ... -/`（Lean 可嵌套）
        if 文本.startswith("/-", i):
            深度 += 1
            i += 2
            continue
        if 深度 > 0:
            if 文本.startswith("-/", i):
                深度 -= 1
                i += 2
                continue
            # 块注释内保留换行，行号/结构不丢
            if 文本[i] == "\n":
                out.append("\n")
            i += 1
            continue
        # 行注释 `-- ...`
        if 文本.startswith("--", i):
            while i < n and 文本[i] != "\n":
                i += 1
            continue
        out.append(文本[i])
        i += 1
    return "".join(out)
_OPEN_RE = re.compile(r"^\s*(?:open|namespace)\s+([A-Za-z_][A-Za-z0-9_.'<>: ]*)",
                      re.MULTILINE)


def _is_noise(path: Path) -> bool:
    for part in path.parts:
        if part in _NOISE_PARTS:
            return True
        if part.startswith("._") or part.startswith("_archive"):
            return True
    return False


class MemoryIndex:
    def __init__(self) -> None:
        self._kinds: dict[str, UnitIndex] = {}

    def _ensure(self, kind: str) -> UnitIndex:
        if kind not in self._kinds:
            self._kinds[kind] = UnitIndex()
        return self._kinds[kind]

    def register(self, kind: str, key: str, path: Path) -> None:
        try:
            self._ensure(kind).register(key, path)
        except ValueError:
            pass

    def index(self, kind: str) -> UnitIndex:
        return self._ensure(kind)

    def kinds(self) -> list[str]:
        return sorted(self._kinds)

    def count(self, kind: str) -> int:
        return self._ensure(kind).size()

    def sizes(self) -> dict[str, int]:
        return {k: self.count(k) for k in self.kinds()}


def scan_code(mem: MemoryIndex, roots: list[Path], glob: str = "**/*.py") -> int:
    n = 0
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob(glob)):
            if not p.is_file() or _is_noise(p):
                continue
            dotted = ".".join(p.relative_to(root).with_suffix("").parts)
            mem.register("code", str(p), p)
            mem.register("code", dotted, p)
            n += 1
    return n


def scan_tasks(mem: MemoryIndex, roots: list[Path], glob: str = "**/*.md") -> int:
    n = 0
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob(glob)):
            if not p.is_file() or _is_noise(p):
                continue
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
            except OSError:
                continue
            if any(k in head for k in _TASK_KEYS):
                mem.register("task", str(p), p)
                mem.register("task", p.stem, p)
                n += 1
    return n


def scan_lean(mem: MemoryIndex, root: Path, profile=None) -> int:
    """扫产物库 .lean（键前缀/跳过规则由档案 artifact 段决定）。"""
    from agentscope_orchestrator import profile as prof_mod
    art = (profile or prof_mod.get_profile()).artifact
    idx = mem.index("formal")
    _, disc = idx.build_artifact(root, key_prefix=art.key_prefix,
                                 disabled_marker=art.disabled_marker,
                                 glob=art.glob)
    return disc


def scan_all(artifact_root: Path,
             code_roots: list[Path] | None = None,
             task_roots: list[Path] | None = None,
             profile=None) -> MemoryIndex:
    mem = MemoryIndex()
    scan_lean(mem, artifact_root, profile)
    for r in (code_roots or []):
        scan_code(mem, [r])
    for r in (task_roots or []):
        scan_tasks(mem, [r])
    return mem


# ── ★ R230：按**项目自己的声明**扫（档案 `memory:` 段）───────────────
#
# 为什么要有它：原来的代码根取自 `TOOL_ROOT`（**工具安装位置**），
# 装成包跑时那个目录不存在 ⇒ 索引里只剩产物库，本体源码/文档一份没有。
# 真流程实测：`auto_scan` 只回 `{"formal": 38238}`。
#
# ★ 两条纪律写进实现里：
#   ① **自己剪枝**，不用 `rglob` 扫全树 —— 本仓从根扫 `**/*.md`
#      会走进 20 万个文件（含 17 万 MB 构建产物）。剪枝是**功能**，不是优化。
#   ② **声明了但不存在的根要如实报出来** —— 「声明了却没扫到」正是这次的病灶，
#      再静默一次就等于没修。
_默认跳过 = (
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".lake",
    ".tmp", "dist", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
    "_run_all_state", ".cargo_home", ".tools",
    "target", "备份", "backup", "arc_data", "数据部署", "全局梳理",
)


# ★ 声明式扫描只剔「**肯定不是内容**」的东西。
#   不许用 `_is_noise` —— 它里面有 `work` / `products` / `config` / `.audit_trail`
#   这类**项目特有的**排除项（旧路径按"工具自己的仓"定的），
#   拿来扫**项目声明的**范围时会把项目最值钱的东西丢掉：
#   本项目 `.audit_trail` 下有 1,784 份回执/裁定，正是「项目事实」的主来源。
#   ⇒ **项目声明了，就照声明走；工具不替项目猜"哪个目录不算知识"。**
_内容噪声 = ("__pycache__", ".git", ".venv", "venv", "node_modules",
             ".lake", ".mypy_cache", ".pytest_cache", ".ipynb_checkpoints")


def _是内容噪声(p: Path) -> bool:
    if any(part in _内容噪声 for part in p.parts):
        return True
    return p.suffix in (".pyc", ".pyo", ".lock")


def _该跳(name: str, skip: tuple[str, ...]) -> bool:
    """目录名该不该剪掉。`skip` 按**前缀**匹配（`target` 能盖住 `target_基线`）。"""
    if name in _默认跳过:
        return True
    for s in skip:
        if s and name.startswith(s):
            return True
    return bool(name.startswith("_archive") or name.startswith("._"))


def scan_declared(mem: MemoryIndex, entries, repo_root: Path) -> dict:
    """按档案 `memory:` 段扫，返回 {计数, 声明了却没扫到, 逐条, declared}。"""
    计数: dict[str, int] = {}
    缺根: list[str] = []
    明细: list[dict] = []

    for 项 in (entries or []):
        kind = str(项.get("kind") or "misc")
        roots = [str(x) for x in (项.get("roots") or ["."])]
        globs = [str(x) for x in (项.get("globs") or ["*"])]
        skip = tuple(str(x) for x in (项.get("skip") or []))
        require = tuple(str(x) for x in (项.get("require") or []))
        n = 0
        for 相对 in roots:
            根 = (repo_root / 相对).resolve()
            if not 根.is_dir():
                缺根.append(f"{相对}（kind={kind}）")
                continue
            for 图样 in globs:
                递归 = "**" in 图样
                # ★ 取 glob 的**最后一段**当文件名匹配式。
                #   原来写的是 `图样.lstrip("*/")` —— `str.lstrip` 会把它**所有**
                #   以 `*` 或 `/` 开头的字符一口气啃掉：`"**/*.rs"` ⇒ `".rs"`，
                #   于是 `fnmatch("a.rs", ".rs")` 恒假、**五种声明全部命中 0**。
                #   （2026-11-04 真跑取证。改法是取最后一段，不是再修 lstrip。）
                尾巴 = 图样.split("/")[-1] or "*"
                for 目录, 子目录s, 文件s in os.walk(根):
                    子目录s[:] = sorted(d for d in 子目录s if not _该跳(d, skip))
                    for 名 in sorted(文件s):
                        if not fnmatch(名, 尾巴):
                            continue
                        p = Path(目录) / 名
                        if _是内容噪声(p):
                            continue
                        if require:
                            try:
                                head = p.read_text(encoding="utf-8",
                                                   errors="ignore")[:4000]
                            except OSError:
                                continue
                            if not any(k in head for k in require):
                                continue
                        try:
                            键 = p.resolve().relative_to(repo_root).as_posix()
                        except ValueError:
                            键 = str(p)
                        mem.register(kind, 键, p)
                        mem.register(kind, p.stem, p)
                        n += 1
                    if not 递归:
                        break
        计数[kind] = 计数.get(kind, 0) + n
        # ★ 把 `skip` / `require` 一起报出来 —— 否则「命中比真盘少」这件事
        #   在返回值里看不出原因（本次真跑：task 类 759 vs 真盘 2151，
        #   差的就是关键词收窄；数字必须能自己解释自己）。
        明细.append({"kind": kind, "roots": roots, "globs": globs,
                     "skip": list(skip), "require": list(require), "命中": n})

    return {"计数": 计数, "声明了却没扫到": 缺根, "逐条": 明细,
            "declared": bool(entries)}


# ── 进程内缓存的项目索引 ───────────────────────────────────────────
#
# 为什么要缓存：全量建索引实测 **4.8 秒**（4,354 个文件 + 38,274 个形式化单元）。
# `context` 是交互式入口，每次调用都重建是不能接受的；但**也不许**缓存到
# 跨进程的磁盘上假装"自动更新" —— 那正是"读数悄悄过期"的经典形态。
# ⇒ 口径：**进程内缓存 + 显式刷新入口（`auto_scan` / `刷新项目索引()`）
#   + 段里报出建库时间与文件数**，让"新不新"这件事是**看得见**的。
_索引缓存: MemoryIndex | None = None
_索引时间: float = 0.0
_索引次数: int = 0

# ★ 索引的**年龄预算**（秒）。过了就自动重建。
#
#   为什么必须有它：MCP 服务是**长驻进程**，一个会话能挂几小时，
#   而工程在这期间一直在前进（新文件、新回执、改过的代码）。
#   如果索引只在进程启动时建一次，后面全程都在用**越来越旧的读数** ——
#   这正是本仓一直在打的那支病（读数悄悄过期，且没人知道）。
#   ⇒ 口径：**过了预算就自己重建，并把"重建过几次 / 这次多久了"报出来。**
#     预算不是越长越好（会过期），也不是越短越好（每次重建实测 ~5 秒）。
索引最长年龄: float = 300.0


def 项目索引(刷新: bool = False, 最长年龄: float | None = None) -> MemoryIndex:
    """拿进程内的项目索引。

    · `刷新=True`        强制重建
    · `最长年龄=None`    用模块默认预算（`索引最长年龄`）
    · `最长年龄=-1`      **永不**因年龄自动重建（给需要稳定快照的判据用）
    """
    global _索引缓存, _索引时间, _索引次数
    预算 = 索引最长年龄 if 最长年龄 is None else 最长年龄
    龄 = 索引年龄()
    该建 = (刷新 or _索引缓存 is None
            or (预算 >= 0 and 龄 > 预算))
    if 该建:
        import time as _t
        from agentscope_orchestrator import profile as prof_mod
        prof = prof_mod.get_profile()
        m = MemoryIndex()
        覆盖 = scan_project(m, prof.artifact.root, prof.repo_root)
        m._覆盖 = 覆盖            # 挂在索引上，供"看得到覆盖面"用
        _索引缓存 = m
        _索引时间 = _t.time()
        _索引次数 += 1
    return _索引缓存


def 索引年龄() -> float:
    """索引建好到现在过了多少秒（没建过 = -1）。"""
    if _索引缓存 is None:
        return -1.0
    import time as _t
    return _t.time() - _索引时间


def 索引重建次数() -> int:
    """这个进程里索引被建过几次 —— 「有没有随工程进度更新」的**读数**。"""
    return _索引次数


def scan_project(mem: MemoryIndex, artifact_root: Path, repo_root: Path,
                 profile=None) -> dict:
    """产物库 + **项目声明的**记忆范围。真流程该走的那条路。"""
    from agentscope_orchestrator import profile as prof_mod
    prof = profile or prof_mod.get_profile()
    scan_lean(mem, artifact_root, prof)
    声明 = tuple(getattr(prof, "memory", ()) or ())
    出 = scan_declared(mem, 声明, repo_root)
    if not 声明:
        出["warn"] = ("档案没有声明 `memory:` 段 ⇒ 只扫了产物库。"
                      "「只有 formal」**不等于**「全都盖了」—— "
                      "要盖项目本体的源码/文档，请在档案里声明 memory 段。")
    return 出


@dataclass
class Candidate:
    unit_id: str
    module: str
    theorem: str
    statement_marker: str
    source_path: Path
    body: str
    deps: list[str] = field(default_factory=list)


def _module_theorem_names(text: str) -> set[str]:
    # ★ 只认**代码**里的声明（剥注释），不认散文里引用的声明名
    return {m.group(2) for m in _BLOCK_RE.finditer(_去注释(text))}


def select_unclosed(mem: MemoryIndex, artifact_root: Path,
                    limit: int = 20, profile=None) -> list[Candidate]:
    """选未闭合声明。键前缀/语言后缀由档案 artifact 段决定。"""
    from agentscope_orchestrator import profile as prof_mod
    art = (profile or prof_mod.get_profile()).artifact
    prefix = (art.key_prefix + ".") if art.key_prefix else ""
    ext = Path(art.glob).suffix or ".lean"

    idx = mem.index("formal")
    # 全库定理名参照（依赖缺口度量）
    all_names: set[str] = set()
    seen_files = set()
    for key, path in idx._units.items():
        if not str(key).endswith(ext) or path in seen_files:
            continue
        seen_files.add(path)
        try:
            t = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        all_names |= _module_theorem_names(t)

    found: list[Candidate] = []
    done = set()
    for key, path in idx._units.items():
        if not str(key).endswith(ext) or path in done:
            continue
        done.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # ★★★ 先剥注释**再切块**（2026-10-13 修 · 同一类病第四次出现）。
        #   原实现在**原始文本**上切块 ⇒ 注释里**引用的**声明也被当成真声明。
        #   实测后果：`HeightInfoEnergyConservation.lean` 的注释块是
        #   「被删除声明的完整清单」，里面写着
        #       `theorem height_reconstruct_conservation (gen : Generator) ...`
        #   ⇒ 被抽成候选 ⇒ 生成模板 ⇒ 模型写 `exact True.intro` ⇒
        #   **四门全绿** —— 编排器"证明"了一个**人特意删除的空真定理**
        #   （该文件明写「删除理由：**本意为假**」）。
        #   ★ 这正是库里 TS-392→TS-410 记的**空真形态**：判据全绿而零信息。
        #   ⇒ 剥注释后再切块：**只认代码里的真声明**。
        for m in _BLOCK_RE.finditer(_去注释(text)):
            name = m.group(2)
            block = m.group(0)
            # ★★★ 判"未闭合"必须读**代码**，不读**散文**（2026-10-13 修）。
            #   原实现直接 `_SORRY_RE.search(block)` —— 而 block 对文件里
            #   **最后一个定理**会把余下全文吞进来（实测 17830 字符），
            #   文档注释里散文提到的 `sorry` 被当成代码 ⇒ **已闭合的定理
            #   被永远重选**（实测同一定理 staged 8 次，永不收敛）。
            #   ⇒ 先剥注释再判，是**换读法**（同门那边读内核的道理）。
            代码 = _去注释(block)
            # ★★ 「证明体为空」用 `\Z`（**串尾**）判，不用 `$`（行尾）——
            #    原实现传了 `re.MULTILINE`，`$` 变成行尾 ⇒ **任何**
            #    `theorem t : P := by` + 换行的**已证明**定理都被误判
            #    （实测活库 8063/13035 命中，候选表 99.3% 是假候选）。
            unclosed = bool(_SORRY_RE.search(代码)) or \
                bool(_EMPTY_BODY_RE.search(代码))
            stmt_m = _MARKER_RE.search(block)
            if not unclosed or not stmt_m:
                continue
            # ★ 用完整陈述（跨行返回类型不丢），不再直接取 group(3)
            完整陈述 = _陈述_of(block, name)
            if not 完整陈述:
                continue
            # 依赖缺口：块内标识符 token 与库内全部定理名求交集（O(n)，免逐名正则）
            tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_']*", block))
            deps = sorted(t for t in tokens if t in all_names and t != name)
            rel = path.relative_to(artifact_root).with_suffix("")
            dotted = ".".join(rel.parts)
            found.append(Candidate(
                unit_id=f"{prefix}{dotted}",
                module=f"{prefix}{dotted}",
                theorem=name, statement_marker=完整陈述,
                source_path=path, body=block, deps=deps))
    found.sort(key=lambda c: (-len(c.deps), c.unit_id, c.theorem))
    return found[:limit]


def _target_imports(source: Path, source_module: str) -> str:
    imp = f"import {source_module}\n"
    opens: list[str] = []
    try:
        t = source.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        t = ""
    seen = set()
    for m in _OPEN_RE.finditer(t):
        token = m.group(1).strip()
        if token and token not in seen:
            seen.add(token)
            opens.append(token)
    if opens:
        imp += "open " + " ".join(opens) + "\n"
    return imp


def gen_lean_candidate(cand: Candidate, artifact_root: Path,
                       profile=None) -> dict:
    """生成隔离候选模板 + 档案声明的判据门（工具不持有命名空间/门）。"""
    from agentscope_orchestrator import profile as prof_mod
    prof = profile or prof_mod.get_profile()
    ns = str(prof.default_task.get("gen_namespace") or "Orchestrated_auto")
    stmt = cand.statement_marker
    # 陈述与定理名之间须留空格（抽取时可能紧贴，`f{a b : Real}` 不是合法 Lean）
    if stmt and not stmt.startswith(" "):
        stmt = " " + stmt
    imports = _target_imports(cand.source_path, cand.unit_id)
    template = f"""{imports}
namespace {ns}

/-- 自动收口：证明 {cand.theorem}（陈述自源模块抽取，逐字保留）。 -/
theorem {cand.theorem}{stmt}
    := by
  sorry

end {ns}
"""
    # 判据门：优先用档案 default_task.lean_gates（占位符按本候选填充）
    raw = list(prof.default_task.get("lean_gates") or [])
    art = prof.artifact.root
    # ★ `marker` 是 `statement_intact` 门用来核对「陈述未被改动」的锚点。
    #   原实现 `split(":")[0]` 只取到**第一个冒号之前**的形参列表，
    #   返回类型部分完全不查 ⇒ 该门守不住陈述的后半段（实测缺陷，2026-10-13）。
    #   现改为取**完整陈述**（已把跨行合并为空格），并同时给出参数段作兼容锚。
    全陈述 = cand.statement_marker.strip()
    参段 = 全陈述.split(":")[0].strip() if ":" in 全陈述 else 全陈述
    mapping = {"theorem": cand.theorem,
               "marker": 全陈述,
               "marker_args": 参段,
               "module": ns,
               "artifact_parent": str(art.parent) if art else str(artifact_root),
               "artifact": str(art) if art else "",
               "repo": str(prof.repo_root)}

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

    gates = sub([dict(g) for g in raw]) if raw else []
    return {
        "unit_id": cand.unit_id, "module": ns,
        "theorem": cand.theorem, "statement_marker": cand.statement_marker,
        "target_rel": f"Orchestrated_auto_{cand.theorem}.lean",
        "template": template, "gates": gates, "deps": cand.deps,
        "source_path": str(cand.source_path),
    }


def _selftest() -> int:
    """自检：全部机器可算。

    ★ 重点测 `_去注释` —— 它是「**读代码不读散文**」那条修法的实现，
      坏了不会有症状：候选表会悄悄塞满**已经证明完的**定理，
      编排器就**反复重做同一件事**（实测发生过：同一定理 staged 8 次）。
    """
    ok = total = 0

    def chk(name: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  ✗ {name}")

    # ① 行注释里的 sorry 不算代码
    chk("行注释 sorry 被剥",
        not _SORRY_RE.search(_去注释("theorem t : True := by\n  -- 没有 sorry\n  trivial\n")))
    # ② 块注释
    chk("块注释 sorry 被剥",
        not _SORRY_RE.search(_去注释("theorem t : True := by\n  /- 没写 sorry -/\n  trivial\n")))
    # ③ ★ 嵌套块注释（Lean 特有：/- 可嵌套）
    chk("★ 嵌套块注释被剥",
        not _SORRY_RE.search(_去注释("theorem t : True := by\n  /- 外 /- 内 sorry -/ 还是注释 -/\n  trivial\n")))
    # ④ 文档注释
    chk("文档注释 sorry 被剥",
        not _SORRY_RE.search(_去注释("/-- 历史上用过 sorry -/\ntheorem t : True := by\n  trivial\n")))
    # ⑤ 真 sorry 必须留下（不能剥过头）
    chk("真 sorry 留下", bool(_SORRY_RE.search(_去注释("theorem t : True := by\n  sorry\n"))))
    chk("真 admit 留下", bool(_SORRY_RE.search(_去注释("theorem t : True := by\n  admit\n"))))
    # ⑥ 注释+真 sorry ⇒ 仍判未闭合（不能因为剥注释把真错放过）
    chk("注释+真 sorry ⇒ 仍未闭合",
        bool(_SORRY_RE.search(_去注释("theorem t : True := by\n  -- 注释\n  sorry\n"))))
    # ⑦ 剥注释保留换行（行号/结构不丢）
    chk("剥注释保留换行",
        _去注释("a\n/- x\ny -/\nb\n").count("\n") >= 3)
    # ⑧ 无注释文本原样
    chk("无注释文本不变", _去注释("theorem t : True := by trivial") == "theorem t : True := by trivial")
    # ⑨ 未闭合的第二种形态：证明体**为空**（模板没填）
    chk("空证明体（:= by 结尾）判未闭合",
        bool(_EMPTY_BODY_RE.search(_去注释("theorem t : True := by\n"))))
    # ⑩ ★★★ 正常写法**不得**误判（这是候选表被灌满 99.3% 的根因）
    chk("★ 正常写法 := by + 换行 + 证明 ⇒ 判已闭合",
        not _EMPTY_BODY_RE.search(
            _去注释("theorem t : True := by\n  trivial\n")))
    chk("★ 单行 := by trivial ⇒ 已闭合",
        not _EMPTY_BODY_RE.search(_去注释("theorem t : True := by trivial\n")))
    chk("★ := 换行 + ⟨⟩ 证明 ⇒ 已闭合",
        not _EMPTY_BODY_RE.search(_去注释("theorem t : A ∧ B :=\n  ⟨ha, hb⟩\n")))
    # ⑩b ★ 关键：`$`（行尾）与 `\Z`（串尾）的差别必须体现出来
    chk("★ 行尾 := by 不算空体（MULTILINE 陷阱）",
        not re.search(r":=\s*by\s*$",
                      _去注释("theorem t : True := by\n  trivial\n")))
    # ⑩c 注释里的 := by 不算
    chk("注释里的 := by 不算",
        not _EMPTY_BODY_RE.search(
            _去注释("theorem t : True := by\n  trivial\n-- 曾经 := by\n")))
    # ⑪ 真实形态：散文里提 sorry 的已闭合定理 ⇒ 判已闭合
    真形态 = ("theorem g (n : Nat) : n = n :=\n  rfl\n\n"
             "/-! ★★ 如实记录自纠：初稿在这里写了 **`sorry`** —— 后来改了。 -/\n")
    chk("★ 散文提 sorry 的已闭合定理判已闭合",
        not _SORRY_RE.search(_去注释(真形态)))
    # ⑫ 综合判据（与生产同一口径）
    def 判未闭合(文: str) -> bool:
        c = _去注释(文)
        return bool(_SORRY_RE.search(c)) or bool(_EMPTY_BODY_RE.search(c))

    chk("综合·真 sorry ⇒ 未闭合", 判未闭合("theorem t : True := by\n  sorry\n"))
    chk("综合·正常证明 ⇒ 已闭合", not 判未闭合("theorem t : True := by\n  trivial\n"))
    chk("综合·空体 ⇒ 未闭合", 判未闭合("theorem t : True := by\n"))
    chk("综合·注释 sorry ⇒ 已闭合",
        not 判未闭合("theorem t : True := by\n  -- 不需要 sorry\n  trivial\n"))

    print(f"  autoengine 自检：{ok}/{total} 通过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("自动引擎模块（--selftest 跑自检）")