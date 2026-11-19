"""R296：**项目知识图谱的自动构建**——给一个文件夹，自动抽出整个项目的知识结构。

★ 为什么要有这个模块（用户当面指出的根本缺陷，2026-11-06）：
  「一个数据库有功能，但是不能存任何数据、不能查任何数据，有什么意义？」
  「没有自动构建的过程，就等于数据从来不入系统内部。」
  「换一个项目就要重新手写？那有啥意义？」

  实测事实（改之前）：
    · 编排器的索引根 `DOCS_DIR = products/docs` —— **只索引它自己产出的回执**；
    · 项目本体（`形式化验证/` 下 22,839 个 .lean、224 MB）**从未入索引**；
    · `search_docs("密码")` → 0 条；`search_docs("theorem")` → 0 条；
      而索引里明明有 4 万条形式化单元（`auto_scan` 数得出来）。
  ⇒ **「索引里有」不等于「查得到」；「数得出来」不等于「进得了系统」。**

★ 本模块的口径（三条，都是被真事故逼出来的）：
  ① **机械优先、绝不漏**：能用正则/语法判定的（theorem/lemma/def/structure、
     文档标题、代码签名）**绝不调模型**。模型只在"语义"上补：
     「这个定理讲什么主题」「这两个术语是否同义」。
  ② **语义部分可降级**：模型/api 不可用时，构建**照样完成**，
     只是把语义字段标成 `未测到`。**不许因为调不到模型就不建图。**
  ③ **覆盖度必须报数**：扫到多少、解析成功多少、跳过多少及原因。
     「不许漏」不能是一句口号 —— 它得是一个可以读出数字的判据。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

#: 每个文件里可抽取的"知识单元"种类（机械可判）
单元种类 = ("theorem", "lemma", "def", "structure", "axiom", "instance",
            "class", "abbrev", "doc", "func", "class_py", "todo")

#: 按扩展名分派解析器
_后缀类 = {
    ".lean": "lean", ".rs": "rust", ".py": "python",
    ".md": "markdown", ".txt": "text", ".json": "json", ".yml": "yaml",
    ".yaml": "yaml", ".toml": "toml",
    # ★ R297b：门1 报红后查出**真不认识的类型**里有值得索引的 556 个文件，
    #   其中 .ps1(68) .sh(56) .tsx(34) .js(10) .ts(4) .html(3) .dsl(8) .sty(8)
    #   都是**知识载体**，不是垃圾。
    #   ⇒ 补解析器（这是分诊里「补解析器」那一档该做的），**不是问模型**。
    ".ps1": "shell", ".sh": "shell", ".bat": "shell", ".cmd": "shell",
    ".ts": "web", ".tsx": "web", ".js": "web", ".jsx": "web", ".mjs": "web",
    ".html": "markup", ".htm": "markup", ".css": "markup",
    ".dsl": "text", ".sty": "text", ".tex": "markup",
    ".jsonl": "json", ".sub": "text",
}

#: 默认跳过的目录（**必须显式列出，不许靠"扫不到"来跳过**）
_默认跳过目录 = {".git", "__pycache__", "node_modules", ".venv", "venv",
                ".mypy_cache", ".pytest_cache", "target", "dist", "build",
                ".idea", ".vscode", "_run_all_state"}

# ★★★ 2026-11-07：Lean 声明正则**全包唯一一份**，在 `mem_symbols._定义正则["lean"]`。
#   本模块原来自带一份，与它**各缺一样**：
#       mem_symbols    不认修饰词(noncomputable/private/…) · 无 `abbrev` · 有 `inductive`
#       本模块(旧)     全认修饰词 · 有 `abbrev` · **无 `inductive`**
#   ⇒ 同一个语言两套抽取器，同一个文件两个答案。实测代价：
#     `BSDWalk.lean` 3 个 `noncomputable def` 在 mem_symbols 那边全抽不出；
#     `_verify_ts495.lean` 3 个 `abbrev` 同样抽不出。
#   现在的口径：**并集**只有一份，本模块 import 它。
try:
    from .mem_symbols import _定义正则 as _符号正则
    _LEAN声明 = _符号正则["lean"]
except Exception:                                # noqa: BLE001
    # 装成单文件跑时不硬崩；这一份**不再自己维护**，只作最后兜底。
    _LEAN声明 = re.compile(
        r"^[ \t]*(?:@\[[^\]]*\][ \t]*\n[ \t]*)*"
        r"(?:(?:private|protected|noncomputable|partial|unsafe|scoped|local)\s+)*"
        r"(theorem|lemma|def|abbrev|structure|axiom|instance|class|inductive)\s+"
        r"([^\s:({\[]+)", re.M)
_LEAN_IMPORT = re.compile(r"^[ \t]*(?:public\s+)?import\s+([^\s]+)", re.M)
_RUST声明 = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?"
    r"(fn|struct|enum|trait|impl|mod|type|const|static)\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)", re.M)
_RUST_USE = re.compile(r"^[ \t]*(?:pub\s+)?use\s+([^;]+);", re.M)
_PY声明 = re.compile(r"^[ \t]*(?:async\s+)?(def|class)\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)", re.M)
_PY_IMPORT = re.compile(r"^[ \t]*(?:from\s+([.\w]+)\s+import|import\s+([.\w]+))", re.M)
_MD标题 = re.compile(r"^(#{1,4})\s+(.+)$", re.M)
_SORRY = re.compile(r"\bsorry\b")
#: 待办标记（让"还没做"也成为图上的东西）
_TODO = re.compile(r"(?:TODO|FIXME|未闭合|待证|待办|尚未实现|NotImplementedError)", re.I)
#: 中文标题（Lean 的 `/-! # 标题 -/`、Markdown 的 `# 标题`、Rust/Py 的 `//!` `"""`）
_中文标题行 = re.compile(
    r"^[ \t]*(?:/-!|-/|//!|///|#+|\"\"\"|\'\'\'|--)[ \t]*#*[ \t]*([^\n]{2,80})$", re.M)
#: 判断"是不是中文标题"：含中文且不是纯符号
_有中文 = re.compile(r"[\u4e00-\u9fff]")


def _文件标题(文本: str, 相对: str) -> str:
    """抽出文件级中文标题 —— **这是"关键词关联"的关键**。

    ★ R296c：`_AxiomsCognition.lean` 里定理名全是英文
      (`step_none`/`frozen_of_no_defect`)，而「自主认知」「三要素」「必需第四条」
      这些**决定语义的中文全写在文件头的注释块里**。
      只在单元自身找中文 ⇒ 查「自主认知」得 0 条，**但库里明明就是那个主题**。
      ⇒ 口径：**文件级标题必须挂到该文件每个单元上**，
        这样"按主题找"才能命中"按英文命名的定理"。
    """
    标题们: list[str] = []
    for m in _中文标题行.finditer(文本[:4000]):
        行 = m.group(1).strip()
        if _有中文.search(行) and len(行) >= 2:
            # 去掉 Lean 注释块的收尾符
            行 = re.sub(r"\s*-/\s*$", "", 行).strip()
            if 行 and 行 not in 标题们:
                标题们.append(行)
        if len(标题们) >= 3:
            break
    return " · ".join(标题们[:2]) if 标题们 else ""


#: 备份/历史快照的识别：后缀里带 bak / backup / pre_ / 纯日期
_备份标记 = re.compile(r"(?:^|\.)(?:bak|backup|pre_?[a-z0-9]+|old|orig)(?:[_.\-]|$)"
                       r"|^\.[0-9]{8}", re.I)


def _是备份(相对: str, 后缀名: str) -> bool:
    """判断这是不是一个**旧版本快照**（`.lean.bak_xxx` / `.lean.pre_r23b` / `.backup.20260427`）。"""
    名 = Path(相对).name.lower()
    if 后缀名 in (".bak", ".orig"):
        return True
    # 形如 xxx.lean.bak_wire212 / xxx.lean.pre_r23b / xxx.backup.20260427
    尾巴 = 名.rsplit(".", 1)[-1] if "." in 名 else ""
    if any(k in 尾巴 for k in ("bak", "backup", "pre_", "old", "orig")):
        return True
    if "".join(filter(str.isdigit, 尾巴)) == 尾巴 and len(尾巴) >= 6:
        return True           # 纯时间戳尾巴
    return bool(_备份标记.search(名.rsplit(".", 1)[0] if "." in 名 else 名))


def _读文本(p: Path, 上限=8 * 1024 * 1024) -> str | None:
    """读文本。读不了返回 None（**并记账**，不许静默跳过）。"""
    try:
        if p.stat().st_size > 上限:
            return None
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _哈希(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "ignore")).hexdigest()[:16]


def _抽lean(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    单元, 依赖 = [], [m.group(1) for m in _LEAN_IMPORT.finditer(文本)]
    # ★ R296c（真事故，第二次跑才看出来）：摘要第一版用 `rfind("\n\n")` 取**前文**，
    #   结果 `_AxiomsCognition.lean` 抽出的 41 个单元**摘要全是空的**
    #   —— 因为 Lean 的 `/-- -/` 文档注释与声明之间只隔**一个**换行，
    #   两个换行出现在更早处或被注释块吞掉。
    #   ⇒ 病根：**「摘要」取的应当是「声明本身 + 紧跟的正文」**，
    #     不是"声明前面的东西"。查"自主认知"得 0 条，就是这个 bug 造成的。
    行起 = [0] + [i + 1 for i, ch in enumerate(文本) if ch == "\n"]

    def _行到偏移(行号: int) -> int:
        return 行起[行号] if 0 <= 行号 < len(行起) else len(文本)

    for m in _LEAN声明.finditer(文本):
        类, 名 = m.group(1), m.group(2)
        # 摘要 = 声明前最多 6 行（含 /-- 文档注释）+ 声明后最多 12 行正文
        起行 = max(0, 文本[:m.start()].count("\n") - 6)
        尾 = 文本.find("\n\n", m.end())
        止 = 尾 if 尾 > 0 else len(文本)
        止 = min(止, _行到偏移(文本[:m.start()].count("\n") + 13))
        段 = 文本[_行到偏移(起行):止]
        单元.append({
            "id": f"{相对}::{类}::{名}",
            "kind": "theorem" if 类 in ("theorem", "lemma") else 类,
            "name": 名, "file": 相对, "line": 文本[:m.start()].count("\n") + 1,
            "摘要": 段[:900],
            "有sorry": bool(_SORRY.search(段)),
        })
    return 单元, 依赖


def _抽rust(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    单元, 依赖 = [], []
    for m in _RUST_USE.finditer(文本):
        for p in m.group(1).split("::"):
            if p and p not in ("self", "super", "crate"):
                依赖.append(p.strip())
    for m in _RUST声明.finditer(文本):
        类, 名 = m.group(1), m.group(2)
        行 = 文本[:m.start()].count("\n") + 1
        尾 = 文本.find("\n}\n", m.end())
        段 = 文本[m.start():(尾 + 3 if 尾 > 0 else m.end() + 400)]
        单元.append({
            "id": f"{相对}::{类}::{名}", "kind": "func", "name": 名,
            "file": 相对, "line": 行, "类型": 类, "摘要": 段[:600],
            "有todo": bool(_TODO.search(段)),
        })
    return 单元, 依赖


def _抽python(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    单元, 依赖 = [], []
    for m in _PY_IMPORT.finditer(文本):
        依赖.extend(x for x in (m.group(1), m.group(2)) if x)
    for m in _PY声明.finditer(文本):
        类, 名 = m.group(1), m.group(2)
        行 = 文本[:m.start()].count("\n") + 1
        单元.append({
            "id": f"{相对}::{类}::{名}",
            "kind": "class_py" if 类 == "class" else "func",
            "name": 名, "file": 相对, "line": 行,
            "摘要": 文本[m.start():m.start() + 600],
            "有todo": bool(_TODO.search(文本[m.start():m.start() + 600])),
        })
    return 单元, 依赖


def _抽markdown(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    """文档按**标题**切分（比按文件名可靠 —— 文件名常常不含内容）。"""
    单元 = [{"id": f"{相对}::doc::全文", "kind": "doc", "name": Path(相对).stem,
             "file": 相对, "line": 1, "摘要": 文本[:800],
             "字符数": len(文本), "有todo": bool(_TODO.search(文本[:4000]))}]
    标 = [(m.start(), len(m.group(1)), m.group(2).strip()) for m in _MD标题.finditer(文本)]
    for i, (起, 级, 题) in enumerate(标):
        止 = 标[i + 1][0] if i + 1 < len(标) else len(文本)
        段 = 文本[起:止]
        单元.append({
            "id": f"{相对}::h{级}::{题}", "kind": "doc", "name": 题,
            "file": 相对, "line": 文本[:起].count("\n") + 1,
            "级别": 级, "摘要": 段[:800], "字符数": len(段),
            "有todo": bool(_TODO.search(段)),
        })
    return 单元, []


def _抽文本(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    return [{"id": f"{相对}::doc::全文", "kind": "doc", "name": Path(相对).stem,
             "file": 相对, "line": 1, "摘要": 文本[:800],
             "字符数": len(文本), "有todo": bool(_TODO.search(文本[:4000]))}], []


def _抽json(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    """结构化数据：**不问模型**，机械拆键。锁文件/清单/配置都走这里。"""
    import json as _j
    try:
        值 = _j.loads(文本)
    except Exception:                              # noqa: BLE001
        # 解析不了就当一个文档（至少能按内容搜到），**不崩**
        return _抽文本(文本, 相对)
    单元 = [{"id": f"{相对}::json::根", "kind": "data", "name": Path(相对).stem,
             "file": 相对, "line": 1, "摘要": 文本[:600],
             "顶层键": list(值)[:40] if isinstance(值, dict) else [],
             "字符数": len(文本)}]
    if isinstance(值, dict):
        for k, v in list(值.items())[:200]:
            单元.append({"id": f"{相对}::key::{k}", "kind": "field", "name": str(k),
                         "file": 相对, "line": 1,
                         "摘要": f"{k} = {str(v)[:400]}"})
    return 单元, []


def _抽toml(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    """TOML 用正则拆表头与键 —— **不引新依赖**（本仓 py3.12 有 tomllib，但保留正则兜底）。"""
    单元 = [{"id": f"{相对}::toml::根", "kind": "data", "name": Path(相对).stem,
             "file": 相对, "line": 1, "摘要": 文本[:600]}]
    for m in re.finditer(r"^[ \t]*\[+([^\]]+)\]+", 文本, re.M):
        单元.append({"id": f"{相对}::td::{m.group(1)}", "kind": "field",
                     "name": m.group(1).strip(), "file": 相对,
                     "line": 文本[:m.start()].count("\n") + 1,
                     "摘要": m.group(0)[:300]})
    for m in re.finditer(r"^[ \t]*([A-Za-z0-9_\-\.]+)[ \t]*=", 文本, re.M):
        单元.append({"id": f"{相对}::kv::{m.group(1)}", "kind": "field",
                     "name": m.group(1), "file": 相对,
                     "line": 文本[:m.start()].count("\n") + 1, "摘要": m.group(0)[:300]})
    return 单元[:400], []


def _抽yaml(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    """YAML 同样机械拆键，不依赖 pyyaml（它可能没装）。"""
    单元 = [{"id": f"{相对}::yaml::根", "kind": "data", "name": Path(相对).stem,
             "file": 相对, "line": 1, "摘要": 文本[:600]}]
    for m in re.finditer(r"^[ \t]*([A-Za-z0-9_\-\.\u4e00-\u9fff]+)[ \t]*:", 文本, re.M):
        单元.append({"id": f"{相对}::k::{m.group(1)}", "kind": "field",
                     "name": m.group(1), "file": 相对,
                     "line": 文本[:m.start()].count("\n") + 1,
                     "摘要": 文本[m.start():m.start() + 200]})
    return 单元[:400], []


def _抽shell(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    """Shell/PowerShell：抽函数、脚本级命令、注释里的中文说明。"""
    单元 = [{"id": f"{相对}::script::全文", "kind": "func", "name": Path(相对).stem,
             "file": 相对, "line": 1, "摘要": 文本[:600],
             "有todo": bool(_TODO.search(文本[:3000]))}]
    for m in re.finditer(r"^[ \t]*(?:function\s+|def\s+)?([A-Za-z_][\w\-]*)\s*\(\s*\)\s*\{?",
                         文本, re.M):
        单元.append({"id": f"{相对}::fn::{m.group(1)}", "kind": "func",
                     "name": m.group(1), "file": 相对,
                     "line": 文本[:m.start()].count("\n") + 1,
                     "摘要": 文本[m.start():m.start() + 400]})
    return 单元, []


def _抽web(文本: str, 相对: str) -> tuple[list[dict], list[str]]:
    """TS/JS：抽函数、类、导出、导入。"""
    单元 = [{"id": f"{相对}::mod::全文", "kind": "func", "name": Path(相对).stem,
             "file": 相对, "line": 1, "摘要": 文本[:600]}]
    依赖 = [m.group(1) for m in re.finditer(
        r"(?:from|require)\s*\(?\s*['\"]([^'\"]+)['\"]", 文本)]
    for m in re.finditer(
            r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
            r"(function|class|const|interface|type|enum)\s+([A-Za-z_$][\w$]*)", 文本, re.M):
        单元.append({"id": f"{相对}::{m.group(1)}::{m.group(2)}", "kind": "func",
                     "name": m.group(2), "file": 相对,
                     "line": 文本[:m.start()].count("\n") + 1,
                     "摘要": 文本[m.start():m.start() + 400]})
    return 单元, 依赖


_解析器 = {"lean": _抽lean, "rust": _抽rust, "python": _抽python,
          "markdown": _抽markdown, "text": _抽文本,
          # ★ R297：结构化数据补上解析器 —— 它们原来全落进"没有对应解析器"，
          #   而**问模型是错的**（锁文件不需要语义），补解析器才是对的。
          "json": _抽json, "toml": _抽toml, "yaml": _抽yaml,
          # ★ R297e：`.html`/`.css` 映射到 `markup`，但这里**漏注册**了
          #   ⇒ 3 个 .html 落进"没有对应解析器"。
          #   「映射表里有 ≠ 解析器接了」—— 两张表必须对齐，否则就是假覆盖。
          "shell": _抽shell, "web": _抽web, "markup": _抽markdown}


def 扫描(根: Path, *, 跳过目录: set[str] | None = None,
         后缀: set[str] | None = None, 上限文件数: int = 0) -> dict:
    """① 遍历：**如实记账每一个文件的结果**（不许静默跳过）。

    ★ R296b（真事故，第一次跑就中）：`上限文件数` 原来是"扫到 N 个就停"。
      实测扫 `形式化验证/`（22,839 个 .lean）给上限 120 ⇒
      结果 `{markdown: 96, python: 18, text: 6}`，**`.lean` 一个都没有**
      —— 因为 `os.walk` 先走到的目录里全是 .md，配额在碰到 .lean 之前就用完了。
      ⇒ 这不是"抽样"，是**系统性漏掉一整类文件**。
      口径：**上限必须按类型均摊**，否则"覆盖率"这个数字会骗人。

    返回 {文件: [...], 覆盖: {...}}
    """
    根 = Path(根)
    跳过 = set(跳过目录 or _默认跳过目录)
    候选: list[dict] = []
    跳过明细: list[dict] = []
    未读明细: list[dict] = []

    # ★★★ 2026-11-07：剪枝口径**改成全包一份**（`mem_prune`），不再自带一张精确名单。
    #
    #   实测（真仓，21,410 目录 / 114,896 文件）：
    #     本模块原来 `d not in 跳过`（**精确名**）只跳过 **13** 个名字；
    #     而 `mem_prune` 的前缀/类名表会跳 **148** 个本模块照样走进去的目录 ——
    #       out×45 · site-packages×33 · .lake×8（里面有 mathlib 的 11 万文件）·
    #       .cargo×6 · work×6 · products×5 · target2 / target_auto / target_clean /
    #       target_g / target_p029 / target_sn91 / target_ts341 … · build_script_build-* · tmp*
    #
    #   为什么必须只留一份：这两个模块回答的是**同一个问题**（「这个目录该不该进去看」），
    #   各写一份的结果就是**两个答案**：`mem_partition` 说 `.lake` 剪掉、
    #   本模块却走进去，于是「候选数」「覆盖率」这些数字互相矛盾，
    #   而**看的人无法知道该信哪个**。
    #   ⇒ 单一来源（`mem_prune.通用跳` 是「通用不可关」的，不允许被调用方关掉）。
    try:
        from . import mem_prune as _mp
    except Exception:                            # noqa: BLE001
        _mp = None                               # 装成单文件跑时不硬崩，退回自带名单
    _档案跳 = frozenset(跳过)

    for dirpath, dirnames, filenames in os.walk(根):
        if _mp is not None:
            dirnames[:] = [d for d in dirnames
                           if d not in 跳过 and not _mp.该跳(d, _档案跳)]
        else:
            dirnames[:] = [d for d in dirnames if d not in 跳过]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                相对 = str(p.relative_to(根))
            except ValueError:
                continue
            后缀名 = p.suffix.lower()
            if 后缀 and 后缀名 not in 后缀:
                跳过明细.append({"文件": 相对, "原因": "后缀不在范围内"})
                continue
            类 = _后缀类.get(后缀名)
            if not 类:
                # ★ R297：不再一律写"不认识的类型" —— 那样门1 永远红，
                #   而红的原因是**我的口径含糊**，不是项目有问题。
                #   已归类的写归类，真不认识的才写不认识。
                if 后缀名 in _非知识后缀:
                    原因 = f"非知识载体（{_不切分后缀.get(后缀名, '工具状态/产物')}）"
                elif _是备份(相对, 后缀名):
                    # ★ R297c：最后 10 个未归类全是 `.lean.bak_xxx` / `.lean.pre_r23b`
                    #   / `.backup.20260427` 这类**旧版本快照**。
                    #   它们不该进知识图谱（同一份内容会有 N 个版本，图会被旧版污染），
                    #   但**必须说得清**，不能含糊成"不认识"。
                    原因 = "非知识载体（备份/历史快照：同一内容的旧版本，入图会污染）"
                elif 相对.endswith("lean-toolchain"):
                    原因 = "非知识载体（工具链版本声明，不是知识）"
                elif 后缀名 in (".bib", ".hash", ".barrel"):
                    原因 = f"非知识载体（{后缀名}：依赖/构建辅助文件）"
                else:
                    原因 = "不认识的类型"
                跳过明细.append({"文件": 相对, "原因": 原因})
                continue
            # 先只登记"存在"，不读内容 —— 读在下面按配额做
            候选.append({"文件": 相对, "类": 类, "路径": p})

    # ★ 按类型均摊配额：每一类都拿到名额，不许某一类把上限吃光
    文件表: list[dict] = []
    if 上限文件数 and len(候选) > 上限文件数:
        按类: dict[str, list[dict]] = {}
        for c in 候选:
            按类.setdefault(c["类"], []).append(c)
        类数 = max(1, len(按类))
        每类 = max(1, 上限文件数 // 类数)
        选了: list[dict] = []
        for 类, 组 in 按类.items():
            选了.extend(组[:每类])
        # 还没到上限 ⇒ 把余量补给文件最多的类
        余 = 上限文件数 - len(选了)
        if 余 > 0:
            已选 = {c["文件"] for c in 选了}
            补 = [c for c in 候选 if c["文件"] not in 已选][:余]
            选了.extend(补)
        候选 = 选了[:上限文件数]

    for c in 候选:
        文本 = _读文本(c["路径"])
        if 文本 is None:
            未读明细.append({"文件": c["文件"], "原因": "读不了或超过 8 MB"})
            continue
        文件表.append({"文件": c["文件"], "类": c["类"],
                       "字节": c["路径"].stat().st_size, "文本": 文本})

    应扫 = len(文件表) + len(跳过明细) + len(未读明细)
    return {
        "文件": 文件表,
        "覆盖": {
            "发现文件数": 应扫,
            "已解析": len(文件表),
            "按后缀跳过": len(跳过明细),
            "读不了": len(未读明细),
            "按类": _计数([{"kind": f["类"]} for f in 文件表]),
            "跳过明细_前20": 跳过明细[:20],
            "未读明细_前20": 未读明细[:20],
            "覆盖率": (len(文件表) / 应扫) if 应扫 else 1.0,
            "被上限截断": bool(上限文件数),
        },
    }


def 构建(根: str | Path, *, 语义=None, 上限文件数: int = 0) -> dict:
    """② 构建：扫描 → 抽取 → 关联 → 产出图谱。**全程不需要模型。**

    参数:
       语义: 可选的可调用 `(问题, 待选项) -> str`。给了就补语义字段，
              **没给或调用失败 ⇒ 语义字段标 `未测到`，构建照样完成。**
    """
    根 = Path(根).resolve()
    if not 根.is_dir():
        return {"ok": False, "错": f"不是一个目录：{根}", "图谱": None}
    t0 = time.time()
    扫 = 扫描(根, 上限文件数=上限文件数)

    单元: list[dict] = []
    依赖边: list[dict] = []
    按类计数: dict[str, int] = {}
    解析失败: list[dict] = []

    for f in 扫["文件"]:
        解析 = _解析器.get(f["类"])
        if not 解析:
            解析失败.append({"文件": f["文件"], "原因": "没有对应解析器"})
            continue
        try:
            元, 依 = 解析(f["文本"], f["文件"])
        except Exception as e:                       # noqa: BLE001
            解析失败.append({"文件": f["文件"], "原因": f"{type(e).__name__}: {e}"})
            continue
        for u in 元:
            u["来源哈希"] = _哈希(f["文本"])
            # ★ 把文件级中文标题挂到每个单元上（"按主题找英文定理"靠这个）
            标 = _文件标题(f["文本"], f["文件"])
            if 标:
                u["文件标题"] = 标
            单元.append(u)
            按类计数[u["kind"]] = 按类计数.get(u["kind"], 0) + 1
        for d in 依:
            依赖边.append({"从": f["文件"], "到": d, "类": "import"})

    # 名字 → 单元（建"被谁用了"的反向索引；这是图能回答问题的关键）
    名字表: dict[str, list[str]] = {}
    for u in 单元:
        名字表.setdefault(u["name"], []).append(u["id"])

    # 机械关联：单元摘要里出现别的单元的名字 ⇒ 引用边（不算语义，纯字符串）
    引用边: list[dict] = []
    for u in 单元:
        hay = u.get("摘要") or ""
        for 名, ids in 名字表.items():
            if 名 and 名 != u["name"] and len(名) >= 3 and 名 in hay:
                for i in ids:
                    引用边.append({"从": u["id"], "到": i, "类": "提到"})
        if len(引用边) > 200_000:
            break

    # 语义（可选，可降级）
    语义态 = {"调用": bool(语义), "成功": 0, "失败": 0, "状态": "未请求"}
    if 语义:
        try:
            问 = ("用一句话说出这个项目的主题（不超过 20 字）" if not 单元
                 else f"这些单元属于什么主题（不超过 10 字）："
                      f"{'、'.join(u['name'] for u in 单元[:30])}")
            语 = 语义(问, [])
            语义态.update({"成功": 1, "状态": f"ok: {str(语)[:80]}"})
        except Exception as e:                       # noqa: BLE001
            语义态.update({"失败": 1, "状态": f"未测到（{type(e).__name__}）"})

    return {
        "ok": True,
        "根": str(根),
        "耗时秒": round(time.time() - t0, 2),
        "图谱": {
            "单元": 单元,
            "名字表": 名字表,
            "依赖边": 依赖边,
            "引用边": 引用边,
        },
        "读数": {
            "文件数_已解析": 扫["覆盖"]["已解析"],
            "单元总数": len(单元),
            "按类": 按类计数,
            "依赖边数": len(依赖边),
            "引用边数": len(引用边),
            "解析失败数": len(解析失败),
            "解析失败明细_前10": 解析失败[:10],
            "有sorry的定理数": sum(1 for u in 单元 if u.get("有sorry")),
            "有todo的单元数": sum(1 for u in 单元 if u.get("有todo")),
        },
        "覆盖": 扫["覆盖"],
        "语义": 语义态,
    }


def 存(图谱构建结果: dict, 路径: str | Path) -> dict:
    """③ 落盘（构建一次，之后查索引，不再遍历目录）。"""
    p = Path(路径)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(图谱构建结果, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "路径": str(p), "字节": p.stat().st_size}


def 载(路径: str | Path) -> dict | None:
    try:
        return json.loads(Path(路径).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ────────────────────── 查询（这才是"有用"的部分）──────────────────────

def _命中(单元: dict, 词: str) -> bool:
    词l = 词.lower()
    hay = " ".join(str(单元.get(k, "")) for k in
                   ("name", "摘要", "file", "kind", "文件标题", "所属主题")).lower()
    return 词l in hay


def 查(图谱构建结果: dict, 关键词: str, *, 种类: str = "",
       上限: int = 20) -> dict:
    """在已建好的图上查。**不再遍历目录。**

    支持多词：`"密码 加密"` ⇒ 任一词命中即算（OR），并按命中词数排序。
    """
    词们 = [w for w in re.split(r"[\s,，、+]+", 关键词 or "") if w]
    单元 = (图谱构建结果 or {}).get("图谱", {}).get("单元") or []
    if not 词们:
        return {"ok": True, "命中数": 0, "结果": [], "说": "没给关键词"}

    hits: list[tuple[int, dict]] = []
    for u in 单元:
        if 种类 and u.get("kind") != 种类:
            continue
        n = sum(1 for w in 词们 if _命中(u, w))
        if n:
            hits.append((n, u))
    hits.sort(key=lambda x: (-x[0], x[1].get("file", ""), x[1].get("line", 0)))

    结果 = [{"命中词数": n, **{k: u.get(k) for k in
             ("id", "kind", "name", "file", "line", "摘要", "有sorry", "有todo")}}
            for n, u in hits[:上限]]
    return {
        "ok": True, "关键词": 词们, "命中数": len(hits),
        "按类": _计数([u for _, u in hits]),
        "结果": 结果,
        # ★ 诚实：0 命中必须能区分「真没有」与「我没索引到」
        "说": ("0 命中 —— 先看 `覆盖`：若覆盖率 < 1 或有「读不了」的，"
               "这可能是**我没索引到**，不是**项目里没有**。"
               if not hits else f"共 {len(hits)} 条，按命中词数排序"),
    }


def _计数(单元: list[dict]) -> dict:
    out: dict[str, int] = {}
    for u in 单元:
        out[u.get("kind", "?")] = out.get(u.get("kind", "?"), 0) + 1
    return out


def 反查(图谱构建结果: dict, 名字: str) -> dict:
    """谁用了这个名字 / 这个名字用了谁（图的价值在这里）。"""
    g = (图谱构建结果 or {}).get("图谱") or {}
    ids = (g.get("名字表") or {}).get(名字) or []
    被用 = [e for e in (g.get("引用边") or []) if e.get("到") in ids][:50]
    被依赖 = [e for e in (g.get("依赖边") or []) if e.get("到") == 名字][:50]
    return {"ok": True, "名字": 名字, "定义处": ids,
            "被这些单元提到": 被用, "被这些文件依赖": 被依赖}


# ────────────────── 「全局构建完毕」的标准（R297）──────────────────
#
# ★ 用户问（2026-11-06）：「知识图谱怎么算全局构建完毕？有没有标准？」
#
# 原来的输出只有 `覆盖率: 0.9649` 一个数字 —— **它说明不了"建完了没有"**：
#   · 覆盖率 96.49% 听起来高，但那 3.51% 里有没有整类文件？不知道；
#   · 251 个"解析失败"里，是**不需要索引的锁文件**，还是**我解析器漏了重要类型**？
#     分不出来 ⇒ 这个数字不能用来判"完毕"。
#
# ⇒ 定四道门。**四道全绿才算"这个项目的图谱构建完毕"**：
#   门1 无遗漏解释：每一个没进图谱的文件，都必须有一个**可归类的原因**
#       （跳过原因 ∈ 已知类，且不留"没对应解析器"这种含糊口径）；
#   门2 无解析崩溃：解析器崩了必须为 0 —— 崩了说明是我写错，不是文件的问题；
#   门3 关键类型齐备：项目里**实际存在**的语言/文件类型，每一类都必须抽出了单元；
#   门4 可回答：给定项目的主主题词，必须查得到东西（**图建了但查不到 = 没建**）。
#
# 这四道门分别对应四种"看起来建完了其实没有"：
#   ① 覆盖率数字骗人 ② 用崩溃当判据 ③ 整类文件被系统性漏掉 ④ 索引里有但查不到

#: 允许的"没进图谱"原因（**每一条都要能说清是什么东西**）
_可归类跳过 = {
    "后缀不在范围内": "调用方显式限定了范围（不是漏）",
    "不认识的类型": "未知后缀 ⇒ **必须由分诊处理**（见 `失败分诊`），不许含糊放过",
    "读不了": "超过大小上限或权限不足（可单独复查）",
}


def _原因可归类(原因: str) -> bool:
    """门1 的判据：这个"没进图谱"的原因，说得清是什么吗？"""
    r = str(原因 or "")
    if not r:
        return False
    if r in ("后缀不在范围内", "读不了"):
        return True
    if r.startswith("非知识载体"):
        return True
    if r.startswith("不认识的类型"):
        return False
    # "不认识的类型" = 含糊，门1 必须红
    return False

#: 明确**不需要**索引的后缀（它们不是知识载体，是工具状态/产物）
_非知识后缀 = {
    # 工具状态与产物
    ".lock", ".log", ".tmp", ".bak", ".orig", ".pyc", ".pyo", ".cache",
    # 二进制与打包
    ".so", ".dll", ".exe", ".zip", ".gz", ".tar", ".whl", ".db", ".sqlite",
    ".bin", ".class", ".o", ".a", ".lib", ".pdb", ".ilk", ".exp", ".dat",
    ".idx", ".pack", ".sample", ".olean", ".ilean", ".trace",
    # 图像与文档（内容不可机械切分成本仓的"知识单元"）
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf", ".webp", ".bmp",
    # ★ 表格：**不是"不认识的类型"，是"有结构但当前不切分"**
    #   用户口径（2026-11-06）：「解析失败的为什么不会调用模型查？」
    #   ⇒ 表格不需要模型：它是结构化的，切分是机械活。
    #     但把每一行都变成知识单元会让图被"数据行"淹没 ⇒ 显式归为"不切分"，
    #     并且**说明理由**，而不是含糊地说"不认识"。
    ".csv", ".tsv",
    # ★ R297b：工具残留（不是知识，也不是"不认识"）
    ".disabled", ".deploying_old", ".pre_r23", ".bak2", ".bak_mirror",
    ".tail", ".out", ".dot", ".aux", ".toc", ".fls", ".fdb_latexmk",
}

#: 明确归类但**不切分**的后缀（结构已知，只是不入图）
_不切分后缀 = {
    ".csv": "表格数据：结构已知但按行切分会让图被数据行淹没",
    ".tsv": "表格数据：同上",
    ".log": "运行日志：是过程痕迹，不是知识",
}


def 构建完毕判定(图谱构建结果: dict, *, 主主题词: list[str] | None = None) -> dict:
    """**「这个项目的知识图谱建完了没有」——四道门，机器判定。**

    返回 {完毕: bool, 门: [...], 不满足的: [...], 说明}
    任何一道门红 ⇒ 完毕=False，并给出**具体缺什么**（不是一个百分比）。
    """
    果 = 图谱构建结果 or {}
    覆 = 果.get("覆盖") or {}
    读 = 果.get("读数") or {}
    门: list[dict] = []
    不满足: list[str] = []

    # 门1：每一个没进图谱的文件都要有可归类的原因
    跳过 = (覆.get("跳过明细_前20") or [])
    含糊 = [x for x in 跳过 if not _原因可归类(x.get("原因", ""))]
    含糊 += [x for x in (覆.get("未读明细_前20") or [])
            if not _原因可归类(x.get("原因", ""))]
    门1红 = bool(含糊)
    门.append({"门": "门1 无遗漏解释", "过": not 门1红,
               "读数": f"跳过 {覆.get('按后缀跳过', 0)} + 读不了 {覆.get('读不了', 0)}",
               "不过的原因": [x.get("原因") for x in 含糊][:5] if 门1红 else ""})
    if 门1红:
        不满足.append(f"有 {len(含糊)} 个文件没进图谱且原因不可归类")

    # 门2：解析器崩溃必须为 0（崩了是我写错）
    #   ★ 注意区分两种"解析失败"：
    #     · "没有对应解析器" = **没写**这个解析器（属于门1/分诊，不是崩溃）；
    #     · 其它异常 = **写了但崩了**（这是真崩，必须为 0）。
    #   第一版我把两者混在一起判，会得出"251 个崩溃"的错误结论。
    崩 = [x for x in (读.get("解析失败明细_前10") or [])
         if "没有对应解析器" not in str(x.get("原因", ""))]
    没解析器 = 读.get("解析失败数", 0) - len(崩)
    门2红 = bool(崩)
    门.append({"门": "门2 无解析崩溃", "过": not 门2红,
               "读数": f"崩溃 {len(崩)} 个；没写解析器的 {max(0, 没解析器)} 个（后者归门1 分诊）",
               "不过的原因": [f"{x['文件']}：{x['原因']}" for x in 崩][:3] if 门2红 else ""})
    if 门2红:
        不满足.append(f"{len(崩)} 个文件让解析器崩了（是我写错，不是文件的问题）")

    # 门3：项目里实际存在的语言类，每一类都必须抽出了单元
    有文件的类 = {str(k) for k, v in (覆.get("按类") or {}).items() if v}
    抽到东西的类: set[str] = set()
    for u in (果.get("图谱") or {}).get("单元") or []:
        抽到东西的类.add(str(u.get("kind")))
    # 把"文件类型"映射到"应产出的单元种类"
    应产出 = {"lean": {"theorem", "def", "structure", "instance", "axiom", "class", "abbrev"},
             "rust": {"func"}, "python": {"func", "class_py"},
             "markdown": {"doc"}, "text": {"doc"}}
    漏的类 = []
    for 类 in 有文件的类:
        期 = 应产出.get(类)
        if 期 and not (期 & 抽到东西的类):
            漏的类.append(类)
    门3红 = bool(漏的类)
    门.append({"门": "门3 关键类型齐备", "过": not 门3红,
               "读数": f"有文件的类 {sorted(有文件的类)}；抽到单元的类 {sorted(抽到东西的类)}",
               "不过的原因": f"这些类一个单元都没抽到：{漏的类}" if 门3红 else ""})
    if 门3红:
        不满足.append(f"整类文件没抽出单元：{漏的类}")

    # 门4：给定主主题词必须查得到（图建了但查不到 = 没建）
    词们 = [w for w in (主主题词 or []) if w]
    if 词们:
        空 = [w for w in 词们 if 查(果, w, 上限=1)["命中数"] == 0]
        门4红 = bool(空)
        门.append({"门": "门4 可回答", "过": not 门4红,
                   "读数": f"试了 {词们}",
                   "不过的原因": f"这些主题词查不到：{空}" if 门4红 else ""})
        if 门4红:
            不满足.append(f"主题词查不到（图建了但没用）：{空}")
    else:
        门.append({"门": "门4 可回答", "过": None,
                   "读数": "没给主主题词 ⇒ **没测到**（不等于通过）",
                   "不过的原因": ""})

    完毕 = all(g["过"] is not False for g in 门) and all(g["过"] for g in 门 if g["过"] is not None)
    return {
        "完毕": bool(完毕),
        "门": 门,
        "不满足的": 不满足,
        "说明": ("四道门全过 ⇒ **这个项目的知识图谱算建完**；"
                "任何一门红或未测到 ⇒ 不算，且上面已写明缺什么"
                if 完毕 else
                "**不算建完** —— 见 `不满足的`"),
        # ★ 诚实：覆盖率单独给，但它**不是**判定标准
        "覆盖率_仅供参考不用于判定": 覆.get("覆盖率"),
    }


def 失败分诊(图谱构建结果: dict, 根: str | Path) -> dict:
    """**没进图谱的文件，分诊成三类：不需要索引 / 我该补解析器 / 该问模型。**

    ★ 用户问（2026-11-06）：「解析失败的为什么不会调用模型查？」
      ⇒ 因为**大部分失败根本不该问模型**：
        · `.lock.json` / `lakefile.toml` —— 不是知识载体，模型也只会说"这是锁文件"；
        · 解析器漏了一个语言 —— 补解析器，问模型是浪费；
        · **真需要语义的**（比如一段没有标签的散文、未知格式的自定义 DSL）才该问。

      所以本函数把每一个失败**先分诊、再决定用哪种手段**：
        `不需要索引` / `补解析器` / `问模型` / `人工看`
    """
    根 = Path(根)
    覆 = (图谱构建结果 or {}).get("覆盖") or {}
    读 = (图谱构建结果 or {}).get("读数") or {}
    明细 = list(覆.get("跳过明细_前20") or []) + list(覆.get("未读明细_前20") or [])
    明细 += list(读.get("解析失败明细_前10") or [])

    分诊: dict[str, list[dict]] = {"不需要索引": [], "补解析器": [], "问模型": [], "人工看": []}
    for x in 明细:
        文件 = str(x.get("文件", ""))
        原因 = str(x.get("原因", ""))
        后 = Path(文件).suffix.lower()
        双重 = [s.lower() for s in Path(文件).suffixes]  # 例如 .json.lock / .lean.bak726
        if 原因 == "读不了":
            分诊["人工看"].append({"文件": 文件, "原因": 原因})
            continue
        # ★ R297d（真读数，改之前分诊与门判定打架）：
        #   · `.lean.bak726` 被分进「问模型」10 个 —— 它是**备份**，
        #     问模型看一份旧版快照讲了什么，纯属浪费，而且入图会污染。
        #   · `.html` 被分进「补解析器」3 个 —— 但扫描器把 .html 判成
        #     "非知识载体"（因为标记文件不在 _后缀类 里），两边口径不一致。
        #   ⇒ 口径统一：**先判备份，再判非知识载体，再看有没有解析器。**
        if _是备份(文件, 后) or any(_是备份(文件, s) for s in 双重):
            分诊["不需要索引"].append({"文件": 文件, "原因": "备份/历史快照（旧版本，入图会污染）"})
            continue
        任一非知识 = any(s in _非知识后缀 for s in 双重) or 后 in _非知识后缀
        if 任一非知识:
            分诊["不需要索引"].append({"文件": 文件, "原因": f"非知识载体（{后 or '无后缀'}）"})
            continue
        if 后 in _后缀类:
            # 已知类型却没解析出来 ⇒ 解析器有洞，该补
            分诊["补解析器"].append({"文件": 文件, "原因": 原因})
            continue
        if 后 in (".json", ".toml", ".yaml", ".yml", ".xml", ".csv",
                  ".html", ".htm", ".tsx", ".ts", ".js", ".ps1", ".sh"):
            # 结构已知但没接进 _后缀类 ⇒ 是我没接，不是语义问题
            分诊["补解析器"].append({"文件": 文件, "原因": f"结构已知但没接解析器（{后}）"})
            continue
        # 剩下的：未知格式。这才是**真该问模型**的那一档
        分诊["问模型"].append({"文件": 文件, "原因": f"未知格式 {后 or '无后缀'}，需要判断它讲了什么"})

    return {
        "ok": True,
        "分诊": {k: {"数": len(v), "明细": v[:10]} for k, v in 分诊.items()},
        "口径": ("**先分诊再决定手段**：不需要索引的不问模型；"
                "解析器有洞的补解析器（问模型是浪费且结果不可复用）；"
                "只有未知格式才该问模型。"),
        "诚实边界": ("分诊按后缀与原因文本机械判定，可能误判；"
                    "`问模型` 这一档在模型不可用时会留空 —— 那是**没测到**，不是没有。"),
    }
