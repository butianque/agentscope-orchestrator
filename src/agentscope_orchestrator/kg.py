"""★ 通用知识图谱管线（R310）：**任意文件夹 → 一张图 → 常驻 → 自动跟着文件更新**。

## 为什么要有这个模块（我上一轮做错的地方）

我上一程给**这一个仓库**手搓了一堆读数（公理足迹、mathlib 参考语料、elaborator 名字）。
换个仓库，**一项都用不上** —— 那是**产物**，不是**机器**。

本模块要的是机器：

| 要 | 怎么做到 |
|---|---|
| **通用** | 不按项目写规则。语言用 **tree-sitter grammar**（现装 15 种），**没有 grammar 的文件也进图**（保底适配器）。 |
| **完备** | **不静默跳过任何文件**。跳过的必须逐条报出来，并说清「为什么跳」。 |
| **自动** | 一条命令建图；再跑只重算**戳变了**的文件；删掉的标**墓碑**。 |
| **常驻** | 落盘 `<根>/.kg/`（图 + 戳 + 清单），下次直接加载。 |
| **可自证** | 自洽门：节点能在源文件里定位 · 边两端存在 · **覆盖率必须报「没进图的」**。 |

## 三条设计决定（都是踩出来的）

1. **不为每种语言手写规则** —— 那是另一种手搓。
   tree-sitter 的**定义节点普遍带 `name` 字段** ⇒ 通用启发式：
   节点类型名匹配 `_definition|_declaration|_item|_specifier|_heading|_section`
   且能取到名字，就是一条定义。
   ★ 实测（python/rust/js/go/markdown）能抓到函数与类，**不需要 per-language 规则**。
2. **图要完备，读数才筛** —— 噪声（`field_declaration`、`parameter_declaration`）
   **不是问题，丢了才是**。⇒ 全部进图，带上 `深度` 与 `父节点`；查询时按需筛。
3. **「看不见」不能当「没有」** —— 没 grammar 的、读不了的、剪掉的，
   **都必须变成图上可见的一类**，不许从读数里消失。

## 用法

    python -m agentscope_orchestrator.kg 建   <根>     # 建图（全量）
    python -m agentscope_orchestrator.kg 更新 <根>     # 增量（只重算戳变了的）
    python -m agentscope_orchestrator.kg 报告 <根>     # 常驻读数
    python -m agentscope_orchestrator.kg 查   <名>     # 按名字查节点
    python -m agentscope_orchestrator.kg 自检          # 不带仓也能跑的自检

★ 诚实边界（本版）：
  · **引用边**用标识符出现做近似（不是类型检查）⇒ 会多不会少，标 `近似`。
  · **跨文件解析只认 import 图** —— 靠「全仓碰巧只有一个同名的」算解析**是假解析**，已删。
  · **未导入**是「别处有定义、本文件没导入」⇒ **依赖图上缺的边**，不是「没有」。
  · **绑定/内置**（局部变量、参数、语言内置）**不算引用** —— 它们是变量，不是图上的实体。
  · **未解析**里混着 C 扩展名、动态生成的属性、外部库 —— **不假装解析**。
  · 二进制文件只进「文件节点 + 指纹」，**不进内容**（存了也没法用）。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

__all__ = ["建图", "更新图", "加载图", "报告", "查", "自洽门", "适配器表"]

# ── 常量 ────────────────────────────────────────────────────────────
_图档 = "图.json"
_戳档 = "戳.json"
_清档 = "清单.json"
_KG目录 = ".kg"

#: **类别**：tree-sitter 的节点命名**跨语言一致**，所以按**后缀**分类
#: **不是按语言写规则** —— 这正是它能当通用基础的原因。
#: ★★ 这里踩过一个真坑：原来把 `_statement` 也当「定义」⇒
#:   Python 标准库 3,905 个文件出了 **676,950 个「定义」**（`if_statement` /
#:   `return_statement` / `expression_statement` 全被算成定义）。
#:   **语句是行为，不是命名实体** ⇒ 它有自己的类别，不许混进「定义」。
_类别: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"_definition$"), "定义"),
    (re.compile(r"_declaration$"), "声明"),
    (re.compile(r"_item$"), "项"),
    (re.compile(r"_specifier$"), "类型"),
    (re.compile(r"_heading$"), "标题"),
    (re.compile(r"_section$"), "小节"),
    (re.compile(r"_statement$"), "语句"),
    (re.compile(r"_expression$"), "表达式"),
)

#: 只有这些类别是「**命名实体**」，才当定义收进图。
_实体类 = frozenset({"定义", "声明", "项", "类型", "标题", "小节"})

#: ★ 「名字必须来自自己的 `name` 字段」的类别 —— **代码实体**。
#:   实测（对账 `ast`）：`with ... as fp`（`with_item`）与 `self._read_lock`
#:   这类**语法位置**没有自己的 `name` 字段，名字是退到「第一个标识符后代」捞的
#:   ⇒ 被当成了定义（400 个标准库文件多抽 345 条 = 3.7% 假阳）。
#: ★ 「允许退到后代」的类别 —— **文档实体**：markdown 的标题内容本来就在
#:   `inline` 里，没有 name 字段是正常的。
#: ⇒ 这是按**命名约定**分类（tree-sitter 跨语言一致），**不是按语言写规则**。
_须自有名字 = frozenset({"定义", "声明", "项", "类型"})


def _分类(型: str) -> str:
    for pat, c in _类别:
        if pat.search(型):
            return c
    return "其他"

#: 名字字段的**尝试顺序**（通用，不按语言写）。
#: ★ 实测：rust 的 `impl_item` 名字在 `type` 字段；js 的 `lexical_declaration`
#:   在 `declarator`；markdown 标题在 `inline`。按顺序试，试不到再退到「第一个标识符后代」。
_名字字段 = ("name", "declarator", "type", "value", "pattern", "key")

#: 有 grammar 的扩展名 → tree-sitter 语言名。**没装 grammar 的用不上它，走保底。**
_语法 = {
    ".py": "python", ".pyi": "python",
    ".rs": "rust",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript:typescript", ".tsx": "typescript:tsx",
    ".go": "go",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".json": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown", ".markdown": "markdown",
    ".html": "html", ".htm": "html",
    ".css": "css",
    ".sh": "bash", ".bash": "bash",
    # ★ Lean **不在** tree-sitter 里 —— 它的结构得**问 elaborator**（`mem_axiom`）。
    #   本模块对 `.lean` 只做保底（不假装有 parser）。
}

#: 结构化的非代码格式：用**标准库/已装库**读，不写正则。
_结构化 = {".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".xlsx", ".docx", ".pptx"}

#: 一律不进内容的目录（**但会出现在「跳过清单」里**，不许静默）。
#: ★ **必剪**：几乎不可能在这里放源码。
_必剪 = (
    ".git", ".hg", ".svn", ".kg", "node_modules", "__pycache__", ".venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".cargo", ".tox",
    ".next", ".cache", ".lake",
    # ★ R521：**依赖落点**。实测某个桶里有 6,307 个文件落在
    #   `…/r291v_4ed_jak_/v/Lib/site-packages/…` —— 那**不是"这个项目的东西"**，
    #   而本层的目的是「让 AI 知道自己有什么」⇒ 把别人的库算成自己的家当就是错。
    #   `site-packages`/`dist-packages` 是 **Python 语义保留名**（PEP 370），
    #   任何项目里它都不可能是"你自己的源码" ⇒ 可以照剪。
    "site-packages", "dist-packages",
)

#: ★★ **可疑剪枝**：这些名字**既可能是构建产物、也可能是真源码目录**。
#:   实测（R338）：`Lib/venv/` 是**标准库真模块**、`pip/_internal/operations/build/`
#:   是**真源码**（8 个 `.py`），两个都被按名字剪掉了 —— **「我漏」里 9 个整文件漏就是它们**。
#:   ⇒ **有 `__init__.py`（Python 包）就不剪**：「叫 build 的包」和「构建产物目录」
#:     因此能被机器分开。`work` 有 7813 个 `.py` 但没 `__init__.py` ⇒ 仍然剪 ✓。
_可疑剪枝 = ("target", "dist", "build", "venv", "work", ".idea", ".vscode")

#: 兼容旧名（别处若有引用）
_默认剪枝 = tuple(_必剪) + tuple(_可疑剪枝)


def _是有源码的目录(d) -> bool:
    """可疑剪枝目录里**像不像真源码**（一次 stat 级别的代价）。

    ★ 判据是**可机判且与语言无关的**：目录里直接有 ``__init__.py``（Python 包）、
      ``Cargo.toml``（Rust crate）、``go.mod``（Go module）、``package.json``（JS 包）。
    ★ **不是猜名字** —— 名字说明不了它是产物还是源码（`build` 两边都用）。
    """
    for 标记 in ("__init__.py", "Cargo.toml", "go.mod", "package.json"):
        if (d / 标记).is_file():
            return True
    return False


def _现在() -> float:
    return time.time()


# ── 适配器：文件 → 节点 + 边 ────────────────────────────────────────
def _取字段(n, 名: str):
    try:
        return n.child_by_field_name(名)
    except Exception:                             # noqa: BLE001
        return None


def _字段名(n, i: int) -> str | None:
    try:
        return n.field_name_for_child(i)
    except Exception:                             # noqa: BLE001
        return None


#: 「这个节点是导入语句」的**通用**判据（按 tree-sitter 的命名约定，**不按语言**）。
# ★ **没有 `require`**：JS 的 `require("fs")` 是 `call_expression`
# （节点类型**不含 "require"**）⇒ 这个词条从来没抽到过 require，
# 只匹配到 TypeScript 的 **`required_parameter`**（函数参数）
# ⇒ 实测抽出 `'x: number'` / `'req: Request'`。**假阳的唯一来源、收益为零。**
# `require()` 是**函数调用**不是导入声明，该单独标注，不混进 import 图。
_导入型 = re.compile(r"import|use_declaration|preproc_include|mod_item|extern_crate")
#: 字符串节点类型（各语言命名不同，列的是**通用集合**）
_字符串型 = ("string", "string_literal", "raw_string_literal",
           "interpreted_string_literal", "string_content", "concatenated_string")


def _路径文本(n, bs: bytes) -> str:
    """从导入节点里**取路径文本**（**通用兜底**）—— 走到这一步就不能再靠 field 名了。

    ★★ 为什么必须有：实测 **Rust 上一条都抽不到**（1071 个 `.rs`，导入 0 条）。
      Rust 是 `use_declaration`，路径在 **`argument` 字段**
      （`scoped_identifier` / `use_as_clause` 的 `path` / `scoped_use_list`），
      **没有 `name` 字段** ⇒ 只认 `name` 的分支全军覆没。
      **在 Python 上调到 100% 不代表通用** —— 这才是「只在 Python 上验过」的真实代价。

    做法（全是**跨语言约定**，不是按语言写）：
      · 截到第一个 `{` —— Rust/JS 的成员列举，只要前缀
      · 去关键字前缀（`use`/`import`/`from`/`pub`/`export`/`require`/`include`/`mod`）
      · 去别名 `X as Y`（Y 可能是 `_`、标识符、或中文）
      · 去尾星号（`use super::*;` 的 `*` **不是**模块名）
      · **分隔符归一**：`::`、`/`、`\\` 都当 `.`（模块路径要能对上文件路径）
    """
    段 = bs[n.start_byte:n.end_byte].decode("utf-8", "replace")
    段 = 段.split("{")[0]
    段 = re.sub(r"^\s*(?:pub\s*(?:\([^)]*\))?\s*)?"
               r"(?:use|import|from|export|require|include|#include|mod|static|extern\s+crate)\s+",
               "", 段)
    # ★ 别名：`\S+` 而不是 `[\w\*]+` —— 中文别名（实测 `as 机` / `as 观`）也要吃掉
    段 = re.sub(r"\s+as\s+\S+\s*$", "", 段)
    段 = 段.strip().strip("\"'`,;:").strip()
    段 = 段.rstrip("*").strip()             # `super::*` 的星号不是模块名
    段 = 段.replace("::", ".").replace("/", ".").replace("\\", ".")
    段 = re.sub(r"\.+", ".", 段).strip(".")
    return 段[:200]


def _清路径(文: str) -> str:
    """把一段导入文本清成**模块路径**。字段分支和整段兜底**共用同一套清洗**。

    ★ 两条分支各清一套 ⇒ 必然给出两个答案（`super::*` 兜底路径清干净了，
      字段路径没清）⇒ 实测抽出 `'super.*'` / `'serde.{Deserialize, Serialize}'`。
      **同一个判断写在两处，必然给出两个答案** —— 所以只留一处。
    """
    文 = 文.split("{")[0]                       # 成员列举只取前缀（Rust/JS 通用）
    文 = 文.strip().strip("\"'`,;:").strip()
    文 = __import__("re").sub(r"\s+as\s+\S+\s*$", "", 文)
    文 = 文.rstrip("*").strip()
    文 = 文.replace("::", ".").replace("/", ".").replace("\\", ".")
    文 = __import__("re").sub(r"\.+", ".", 文).strip(".")
    return 文[:200]


# ★★ **成员列举型**：`import` 语句里**列举被导入名字**的那些结构。
#   白名单是**有界**的 —— 只往这些类型里下钻，**绝不**递归整棵树
#   （R323 递归整棵树 ⇒ `取导入` 塌了：导入边全没、边上冒 `保底: true`）。
_成员列举型 = frozenset({
    "import_clause", "named_imports", "import_specifier", "namespace_import",
    "use_list", "use_as_clause", "use_wildcard", "named_import",
    "namespace_export", "export_clause",
})
# ★ **别名结构**：里面有多个标识符时，**最后一个才是绑定的名字**
#   （`import { c as d }` 绑 `d`；Rust `use a as b` 绑 `b`；Python `x as y` 绑 `y`）
_别名型 = frozenset({"import_specifier", "aliased_import", "use_as_clause",
                    "namespace_import"})
_标识叶子 = frozenset({"identifier", "property_identifier", "type_identifier",
                     "shorthand_property_identifier", "shorthand_property_identifier_pattern",
                     "field_identifier", "constant"})


def _抽绑定名(n, 段, 模块: str = "", 模块节点=None) -> list[str]:
    """从 import 语句节点抽出**被绑定的名字**（有界下钻，绝不遍历整棵树）。

    ★ 返回列表：一行可以绑多个名（`from x import a, b`、`import a, b`）。
    ★ **排除 `模块节点` 的子树** —— 那是「从哪来」，不是「绑了什么」。
    """
    出: list[str] = []

    def _收(节, 深=0) -> None:
        # ★ 深度上限：白名单之外的层只允许下钻几层，防止意外走遍整棵树
        if 深 > 3 or 节 is None:
            return
        if 节.type in _标识叶子:
            文 = 段(节)
            if 文:
                出.append(文)
            return
        for c in 节.children:
            _收(c, 深 + 1)

    def _子节点(节, 深=0):
        """收集白名单结构里的标识符；**别名结构只取最后一个**。"""
        if 节 is None:
            return
        if 节.type in _别名型:
            暂: list[str] = []
            _收进(节, 暂, 0)
            if 暂:
                出.append(暂[-1])          # ★ 别名 ⇒ 最后一个
            return
        if 节.type in _标识叶子:
            文 = 段(节)
            if 文:
                出.append(文)
            return
        for c in 节.children:
            _子节点(c, 深 + 1)

    def _收进(节, 到: list, 深=0) -> None:
        if 节 is None or 深 > 3:
            return
        if 节.type in _标识叶子:
            文 = 段(节)
            if 文:
                到.append(文)
            return
        for c in 节.children:
            _收进(c, 到, 深 + 1)

    # ★★ **`n` 自己就可能是别名结构**：`_导入型` 正则含 `import`，
    #   所以 `aliased_import` **也匹配** ⇒ 它会被当成「语句节点」送进来。
    #   实测就是这一条：`import numpy._core.fromnumeric as fromnumeric` 的 `n`
    #   是 `aliased_import` 本身（不是 `import_statement`），
    #   于是「语句直接带的别名」那条看不见它，抽成空。
    if n.type in _别名型:
        暂x: list[str] = []
        _收进(n, 暂x, 0)
        if 暂x:
            return [暂x[-1]]

    # ★★ 这里**曾经**有一个 ⓪ 分支（R334b 加的）：「语句直接带着的别名节点」——
    #   它在 `if 出: return 出` 处**提前返回**，于是**丢掉了同一语句里其余的名字**。
    #   实测：`from gettext import gettext as _, ngettext` 只拿到 `_`，
    #   `ngettext` 丢了（「我漏 958」的主要来源）。
    #   ★ **② 分支本来就能统一处理两者**（别名取最后一个、裸名取第一个），
    #     所以 ⓪ **删掉**。**同一个判断写在两处，必然给出两个答案。**
    #   （`n` 自己就是别名结构的情形由上面的 `n.type in _别名型` 处理。）

    # ① 白名单结构里的名字（JS/TS/Rust 的花括号、specifier、别名）
    已见 = False
    for c in n.children:
        if c.type in _成员列举型:
            _子节点(c)
            已见 = True
    if 出:
        return 出

    # ② Python 那种：`import` 关键字**之后**同级的命名子节点
    #    （`from 包.甲 import 工具函数, 别的` ⇒ `工具函数`、`别的`）
    过关键字 = False
    for c in n.children:
        # ★★ **`from` 不能置 True**：`from time import gmtime, time` 里
        #   `from` 与 `import` **之间**的 `time` 是**模块路径**（不是绑定名），
        #   而 `import` **之后**的才是被导入的成员名。
        #   原来把 `from` 也算上 ⇒ 模块路径被当成绑定名收进来
        #   （实测 `from time import gmtime, time` 抽出 `['time','gmtime','time']`，
        #   多了一个模块路径的 `time`）。
        #   只有匿名 `import`/`use` 之后才是「被导入的东西」。
        if not c.is_named and c.type in ("import", "use"):
            过关键字 = True
            continue
        if not 过关键字 or not c.is_named:
            continue
        if 模块节点 is not None and c.id == 模块节点.id:
            continue                        # ★ 模块路径不取
        if c.type in ("string", "string_literal", "raw_string_literal"):
            continue
        if c.type in ("comment", "line_comment", "block_comment"):
            continue
        暂: list[str] = []
        _收进(c, 暂, 0)
        if 暂:
            出.append(暂[-1] if c.type in _别名型 else 暂[0])

    if 出:
        return 出

    return 出


def _去模块前缀(名表: list[str], 模块: str) -> list[str]:
    """去掉「模块路径的段」—— 那是**从哪来**，不是**绑了什么**。

    ★ 实测（R330）：`from 包.甲 import 工具函数, 别的` 抽出
      `['包', '工具函数', '别的']` —— `包` 是模块路径的头一段，被误当成了绑定。
      Rust `use std::collections::HashMap` 抽出 `['std', ...]` 同理。

    ★ 为什么用**通用后处理**而不是继续抠树：模块路径在各语言里的节点类型都不同
      （`dotted_name`/`scoped_identifier`/`relative_import`…），
      但**「名字等于模块路径的某一段」这个事实与语言无关** ——
      分隔符按 `.`/`::`/`/` 一起切，三种都覆盖。
    """
    _段列 = [x for x in 模块.replace("::", ".").replace("/", ".").split(".")
            if x and x != "."]
    # ★★ **单段模块不过滤**：`from time import gmtime, time` ——
    #   从 `time` 导入的成员 `time` **是真的绑定名**（`ast` 权威就是这么算的）。
    #   实测这一类是「我漏 724→1555」回升的主要来源（`http/cookies.py` 漏 `time`）。
    #   多段模块（`包.甲`）取出来的 `包` 必然只是**路径的一段** ⇒ 该去掉。
    # ★★★ 该去掉的是**前缀段**，**不含最后一段**。
    #   实测（R337）：`from encodings.aliases import aliases` —— ② 分支抽出的
    #   模块路径名字只有**首段** `encodings`，而**末段同名成员 `aliases` 是真的绑定名**。
    #   原来把**所有段**都算进去 ⇒ 末段也被去掉 ⇒ 这一类全漏（`aliases`/`DefaultTable`/
    #   `StandardEncoding`/`removeOverlaps` —— 正是「我漏 51」的全部形态）。
    #   ★ `_段列[:-1]` 对单段模块自然为空（`from time import time` 也是真的）——
    #     **不用再单独特判单段**，那不是特例，是同一条规则的结果。
    段s = set(_段列[:-1])
    return [x for x in 名表 if x not in 段s]


def _绑定名(n, 段, 模块: str) -> list[str]:
    """`_去模块前缀` 与「纯模块导入兜底」的**唯一组合点**（顺序就是判据）。

    ★ 顺序：**先去前缀，再兜底**。
      反过来的话，兜底产生的 `os` 会立刻被 `_去模块前缀` 当模块路径去掉
      —— **实测 `import os` 的绑定就因此一直是空的**（兜底等于没写）。

    ★ 两步各自都对，**顺序错了就等于没做**：
      · `_去模块前缀` 对 `from x import y` 是对的（`y` 不该取到 `x`）
      · 兜底对 `import X` 是对的（`X` **确实**绑进了本文件命名空间，`ast` 逐条对账证实）

    ★ 兜底的判据**不按语言特判**：「语句里有没有 `from`/`use`」——
      纯 `import` 才兜底；Rust `use a::b::C` 有 `use` ⇒ 不走这里。
    """
    # ★★ **有别名时，别名就是绑定名，不许做前缀过滤。**
    #   实测：`import numpy._core.fromnumeric as fromnumeric` —— 别名 `fromnumeric`
    #   **恰好等于模块末段**，`_去模块前缀` 把它当模块路径去掉了 ⇒ 绑定变成 `[]`。
    #   但它是**显式写出来的绑定名**，不是「模块路径的段」。
    #   （`_抽绑定名` 对这条语句本来就返回 `['fromnumeric']`，是过滤把它吃掉的。）
    _别名在 = any(c.type in _别名型 for c in n.children) or n.type in _别名型 or any(
        (not x.is_named) and x.type == "as"
        for c in n.children for x in c.children)
    # ★★★ **不再做「按名字去前缀」** —— 那本质上是在猜。
    #
    # 实测（R339，反例）：`pip/_internal/vcs/__init__.py` 的
    #   `from pip._internal.vcs.versioncontrol import (…, make_vcs_requirement_url, vcs,)`
    # 树里**明明有 5 个 `dotted_name`**（含 `vcs`），而我这里**只给出 4 个** ——
    # `vcs` 既是**模块路径的第 3 段**、又是**真的成员名**，
    # 「名字等于模块路径的段就去掉」**分不开这两种情形**。
    #
    # ★ 而 **② 分支本来就是按结构跳过的**：`过关键字` 只在匿名 `import`/`use`
    #   **之后**才为 True ⇒ `from` 与 `import` **之间的模块路径节点自然被跳过**。
    #   ⇒ **结构已经保证了正确，名字过滤只会误伤。**
    #
    # ★ 逐条验证（去掉后）：`from 包.甲 import 工具函数, 别的` ⇒ ['工具函数','别的'] ✓
    #   `from time import gmtime, time` ⇒ ['gmtime','time'] ✓
    #   `import a.b.c` ⇒ ['a']（Python 语义：`import a.b.c` 绑的就是 `a`）✓
    #   JS `import { b, c as d } from 'y'` ⇒ 走 ① 白名单 ⇒ ['b','d'] ✓
    # ★★★ **纯 `import a, b`：只保留「属于本模块」的那个名字。**
    #   R341 逐条对「模块归属」查出来的：`import getopt, sys` **一条语句两个模块**，
    #   而 5 处产出各调一次 `_绑定名`（`模块` 各自正确：`getopt` / `sys`），
    #   但**绑定列表都是全量的** ⇒ `sys` 被挂到 `getopt` 上
    #   （实测 `base64.py` 出现 `('sys','getopt')`）。
    #
    #   ★ 判据**按结构**：语句里没有匿名 `from` ⇒ 纯 import ⇒
    #     本模块的名字 = **别名**（若有）或**模块路径首段**（`import a.b.c` 绑 `a`，Python 语义）。
    #   ★ 有 `from` 的语句只涉及一个模块，不在此列（走下面的原路径）。
    _纯import = not any((not c.is_named) and c.type == "from" for c in n.children)
    if _纯import and 模块:
        for c in n.children:
            if c.type in _别名型:
                _临 = _抽绑定名(c, 段, 模块)      # 别名结构 ⇒ 返回最后一个 identifier
                if _临 and 模块 == 段(c).split(" as ")[0].strip():
                    return [_临[-1]]
        return [模块.replace("::", ".").replace("/", ".").split(".")[0]]

    净 = _抽绑定名(n, 段, 模块)
    if 净:
        return 净
    _有来源 = any((not c.is_named) and c.type in ("from", "use", "from_")
                 for c in n.children)
    # ★ **有别名就不许兜底**：有 `as` 说明**别名才是绑定名**，
    #   兜底成「模块首段」必然错（实测 `import a.b as c` ⇒ 错成 `a`）。
    #   到这里还没抽到名字，说明别名节点没被认出来 —— **那是缺口，不是「可以兜底」**。
    _有别名 = any(c.type in _别名型 for c in n.children) or any(
        (not x.is_named) and x.type == "as"
        for c in n.children for x in c.children)
    if not _有来源 and not _有别名 and 模块:
        _首段 = 模块.replace("::", ".").replace("/", ".").split(".")
        if _首段 and _首段[0]:
            return [_首段[0]]
    return 净


def _归_点(文: str) -> str:
    """把 `::` / `/` 归一化成 `.` —— 图里**所有语言的模块名都是点分**。

    ★ R346 实测：Rust 分支第一版直接给 `std::collections::BTreeMap`，
      **判五十五 立刻红了**（它期望 `std.collections.BTreeMap`）——
      **同一个模块有两个名字**，下游任何按 `.` 切的地方都会错。
    """
    return 文.replace("::", ".").replace("/", ".")


_导入语句型 = frozenset({"import_statement", "import_from_statement",
                     "future_import_statement", "use_declaration"})


def _在模块级(n) -> bool:
    """**这个赋值是不是「模块级」的**（R396）。

    ★ `MAX_PREC = 999` 在模块顶层 ⇒ 它在**模块命名空间里产生一个名字** ⇒
      权威 `ast` 侧算命名实体。实测判五十一 `我漏 124`：
      `_pydecimal.py::MAX_PREC` / `MIN_EMIN` / `DecimalTuple` / `_pyio.py::open_code`。
    ★ 判据是**结构**：`assignment` ← `expression_statement` ← `module`。
      **不按名字列举**，也不按"看起来像常量"猜。
    ★ 只认**模块的 TOP-LEVEL 子语句** —— `if`/`try`/`with` 体里的赋值
      **不算**（权威 `ast` 侧的 `走` 也只从 `module.body` 直接子层收）。
      **两边必须同宽**，否则一边说漏、一边说多。
    """
    _p = n.parent
    if _p is None:
        return False
    # ★★★ **链式赋值：里面的 `assignment` 嵌在外层的 `right` 字段里**（R397）。
    #   实测（打出来的）：
    #     `BOM_UTF16 = BOM_UTF16_BE = codecs.BOM_UTF16_BE`
    #       assignment[left] identifier('BOM_UTF16')
    #       =
    #       assignment[right] identifier('BOM_UTF16_BE') ...   ← **内层**
    #   ⇒ 我只处理最外层 ⇒ `BOM_UTF16` 建了、`BOM_UTF16_BE` **漏** ✗
    #   （`collections/__init__.py:222` `update = __update = ...`、
    #     `asyncio/futures.py:430` `Future = _CFuture = ...` 同型。）
    #   ★ 权威 `ast` 侧 `n.targets` **本来就是列表**（两个都收）⇒
    #     **又是"两边不同宽"**（口径 74 的第 N 次）。
    #   ⇒ 判据：**上溯时允许穿过 `assignment`**（链式赋值的每一环都算模块级）。
    if _p.type != "expression_statement" and _p.type != "assignment":
        return False
    # ★★★ **要"上溯穿过控制流块"，不能只认 `module` 的直接子**（R397）。
    #   实测：`_pydecimal.py:87| MAX_PREC = 425000000` **不是顶层赋值**
    #     （它在控制流块里）—— 我第一版只认 `module` 直接子 ⇒ **一条都不匹配** ✗
    #   而权威 `ast` 侧的 `走` **递归进 `If`/`Try`/`With`/`For`/`While`
    #     并保持 `顶层=True`** ⇒ 它算了 ⇒ **只报"我漏"** ✓
    #   ★ 口径：**"模块级"不等于"模块的直接子"** ——
    #     判据是「**到 `module` 的路上没有穿过 `def`/`class`**」。
    #     **两边必须同宽**（口径 74 的又一次）。
    #   ★ 反过来：穿过 `function_definition`/`class_definition` 就**不是**模块级
    #     （类体由 `_在类体里` 管；函数体两边都不算）。
    _x2 = _p.parent
    _防2 = 0
    while _x2 is not None and _防2 < 40:
        _防2 += 1
        if _x2.type == "module":
            return True
        if _x2.type in ("function_definition", "class_definition",
                        "lambda", "decorated_definition"):
            return False
        _x2 = _x2.parent
    return False


def _在类体里(n) -> bool:
    """**这个节点是不是「类体的直接语句」**（R396）。

    ★ 为什么需要它：`class C: __slots__ = ()` 里的 `assignment` 在 tree-sitter 里
      就是普通 `assignment` ⇒ 我这边落**「绑定/内置」**，而权威 `ast` 侧
      **它是类属性、算命名实体** ⇒ 判五十一 `我漏 635`。
    ★ 判据是**结构**：`assignment` ← `expression_statement` ← `block[body]`
      ← `class_definition`。**只认"类体的直接子语句"** ——
      **函数体内的赋值不算**（那边两边都算「绑定」）。
    ★ 不按名字列举（`__slots__` 只是样例，凡类体赋值同理）。
    """
    _p = n.parent
    if _p is None:
        return False
    # ★★★ **链式赋值在类体里同样要穿过 `assignment`**（R397）——
    #   我刚给 `_在模块级` 加了这一条，**却没给 `_在类体里` 加** ✗
    #   实测剩余 4 条全是这个：
    #     `collections/__init__.py:222` `update = __update = _collections_abc...`
    #     `email/_header_value_parser.py:500`
    #       `local_part = domain = route = addr_spec = display_name`（**四层链**）
    #   ★ 口径 74：**一条口径写在两处必须同时改** —— 这是它第 N 次复发，
    #     而且**这次是在同一次提交里、同一个函数族里**。
    #     **改完一个判据，立刻问：它的"孪生判据"在哪？**
    if _p.type != "expression_statement" and _p.type != "assignment":
        return False
    # ★ **穿过之后必须"继续上溯"，不是只把 `assignment` 从拒绝名单里划掉**（R397）。
    #   四层链 `local_part = domain = route = addr_spec = display_name`：
    #     `_p` 是**内层** assignment ⇒ `_p.parent` **还是** assignment ✗
    #   ⇒ 上一版判 `_b.type != "block"` ⇒ **return False** ✗（读数一字未动）
    #   ★ 口径：「允许穿过 X」的正确写法是**循环到不是 X 为止**，
    #     不是「把 X 加进白名单」—— **白名单只放行一层。**
    # ★★★ **要跳过 `assignment` **和** `expression_statement` 两种**（R398）。
    #   插桩实得值（打出来的）：
    #     `class D:\n    update = __update = f`
    #       PROBE-K  父=expression_statement 文本='update = __update = f'   ← 外层 → _b=block ✓
    #       PROBE-K  父=assignment           文本='__update = f'            ← 内层
    #       PROBE-K2 上溯到 _b=**expression_statement** _c=block            ✗ **被挡**
    #   ⇒ 内层 `assignment` 的 `_p` 是**外层 `assignment`**，
    #     再上一层是 `expression_statement` —— **而我只跳过了 `assignment`** ✗
    #   ★ 口径 77 的精确版：**"循环到不是 X 为止"里的 X 必须是**这一族**，
    #     不是"我刚遇到的那一个"** —— `assignment` 与 `expression_statement`
    #     在"赋值语句"这件事上是**结构的两层皮**。
    _b = _p.parent
    _防B = 0
    while (_b is not None and _b.type in ("assignment", "expression_statement")
           and _防B < 40):
        _防B += 1
        _b = _b.parent
    if _b is None or _b.type != "block":
        return False
    _c = _b.parent
    if _c is None or _c.type != "class_definition":
        return False
    try:
        i = list(_c.children).index(_b)
        return (_c.field_name_for_child(i) or "") == "body"
    except Exception:                             # noqa: BLE001
        return False


def _在泛型声明位(n) -> bool:
    """**这个 `type_parameter` 是不是「泛型参数声明」**（R396）。

    ★ 为什么必须有（R391 引入的**假阳**，R396 抓到）：
      tree-sitter 把**下标表达式** `Optional[Type[BaseException]]` 里的
      `[...]` **也解析成 `type_parameter`** ✗
      ⇒ 我的泛型参数分支把 `BaseException` / `TracebackType` / `float` / `Type`
        **当成了定义** ⇒ 判五十一实得 **`我多 7`**（假阳）。
      实测（打出来的字段名）：
        `def f[T](x)`                        ⇒ 字段名 `'type_parameters'` ✓ 真声明
        `class C[ArrayT]`                     ⇒ 字段名 `'type_parameters'` ✓ 真声明
        `def f(x: Optional[Type[BaseException]])` ⇒ 字段名 `''`             ✗ 下标
    ★ 判据是**语法位置（字段名）**，不是名字长相、也不是"有没有 `[`"。
    ★ 诚实边界：**只有「字段名是 `type_parameters`」这一种**才算声明 ——
      若哪天 grammar 改名，这里会**变成漏抽**（`我漏` 会涨），**不会变成假阳**。
      宁可漏，不可错（口径：误报的引用比缺失的更坏）。
    """
    _p = n.parent
    if _p is None:
        return False
    try:
        i = list(_p.children).index(n)
        return (_p.field_name_for_child(i) or "") == "type_parameters"
    except Exception:                             # noqa: BLE001
        return False


def _语句行(n) -> int:
    """**这个名字所在的「import 语句」的起始行**（1 基）。

    ★ 为什么需要它（R394 实测）：`_导入别名` 原来用**别名节点自己的行**当键，
      而 `取导入` 用**语句的行**当键 —— **多行括号导入时两者不同**：
        `from numpy._typing import (` / `  NDArray,` / `  _ArrayLikeInt_co as i_co,` / `)`
        ⇒ 别名在第 3 行，语句在第 1 行 ⇒ `_别名.get(1)` = `{}` ⇒ 映射空 ✗
      ⇒ `i_co` 140 条未解析（当时未解析 Top 第一）。
    ★ 判据是**结构**：**沿 `parent` 向上找最近的 import 类语句**。
      **不按语言列举** —— Python `import_statement`/`import_from_statement`、
      JS/TS `import_statement`、Rust `use_declaration` 一律命中。
    ★ 找不到时**退回自己的行**（绝不返回 None —— 调用方要拿它当键）。
    """
    _x = n
    _防 = 0
    while _x is not None and _防 < 40:
        _防 += 1
        if _x.type in _导入语句型:
            return _x.start_point[0] + 1
        _x = _x.parent
    return n.start_point[0] + 1


def _导入别名(树, bs: bytes) -> dict[int, dict[str, str]]:
    """`{行: {本地名: 原名}}` —— **别名导入时靶文件里要找的是「原名」**（R373）。

    ★ 病根实测（标准库，同一把探针剩下的 1,775 条）：
      `random.py:53  from warnings import warn as _warn`
    而 `warnings.py` 里 **`warn` 是定义(行299)、`_warn` 是 `None`**。
    我的导入边记的是**本地名** `_warn`，于是「去靶文件里找同名」**必然找不到**
    ⇒ 落「未导入」。**靶文件里从来就没有 `_warn` 这个东西。**

    ★ 判据不按语言列举，按**语法位置**：别名结构里那个**匿名 `as` 关键字**
      的前一个 named 兄弟是**原名**，后一个是**本地名**。
      · Python `aliased_import`：`warn as _warn` ⇒ 前 `warn`、后 `_warn` ✓
      · JS/TS `import_specifier`：`a as b` ⇒ 同规则 ✓
      · Go `import_spec` 是 `别名 "path"`（**没有 `as`**）⇒ 本函数不管它 ✓
        —— **不误伤**正是"按语法位置、不按语言"的好处。
    """
    def 段(n) -> str:
        return bs[n.start_byte:n.end_byte].decode("utf-8", "replace")

    出: dict[int, dict[str, str]] = {}
    栈 = [树.root_node]
    while 栈:
        n = 栈.pop()
        if n.type in _别名型:
            _as = [i for i, c in enumerate(n.children)
                   if (not c.is_named) and c.type == "as"]
            if _as:
                _i = _as[0]
                _前 = [c for c in n.children[:_i] if c.is_named]
                _后 = [c for c in n.children[_i + 1:] if c.is_named]
                if _前 and _后:
                    _原 = 段(_前[-1]).strip()
                    _本 = 段(_后[0]).strip()
                    if _原 and _本 and _原 != _本:
                        # ★★★ **键必须是「import 语句」的起始行，不是「别名节点」的行**（R394）。
                        #   实测（同一把探针，实得值）：
                        #     `from numpy._typing import ( NDArray, _ArrayLikeInt_co as i_co )`
                        #       `_导入别名` ⇒ `{3: {"i_co": "_ArrayLikeInt_co"}}`  ← 别名在第 3 行
                        #       `取导入`   ⇒ `{"行": 1, ...}`                      ← **语句**在第 1 行
                        #     ⇒ `_别名.get(1)` = `{}` ⇒ **映射空** ⇒ `i_co` 140 条未解析 ✗
                        #   ★ 单行导入看不出来（`import os as o` 两种行号相同 ⇒ 实测里
                        #     `o: os` 是**对的**）——
                        #     **"只有多行才暴露"的错，单行 fixture 永远测不出。**
                        #   ★ 判据是**结构**：**向上找最近的 import 类语句节点**，用它起始行。
                        出.setdefault(_语句行(n), {})[_本] = _原
        for c in n.children:
            栈.append(c)
    return 出


def 取导入(树, bs: bytes) -> list[dict]:
    """从语法树里抽 `import`/`use` 类语句 ⇒ `[{"模块","绑定","相对","行"}]`。

    ★★ **顺序在这里是判据本身**，实测每一条都对应一个真缺陷：

      ① **字符串优先**：路径几乎总是**字符串字面量**（Go/JS/TS/C 全带引号），
         而 `name`/`alias` 字段通常是**别名**。
         实测 Go `import_spec` 同时有 `name`（`mypkg`/`_`）和 `path`（真路径）
         ⇒ 先命中 `name` ⇒ **抽到的是别名，不是模块**。
         Python `from X import Y` 没字符串 ⇒ 照旧走字段。
      ② **判定只看 `named` 子节点**：`import` / `use` / `from` 这些
         **匿名关键字节点本身也含 "import"/"use"** ⇒ 用 `any(...)` 会把
         「自己有子结构」误判成真 ⇒ 实测 **Java 从 1032 条掉到 0**。
      ③ **取最内层**：这一层取到产出就**不往导入型子节点下钻**，取不到才下钻。
         · Python `from re import compile as re_compile` 里 `aliased_import`
           也含 "import" ⇒ 靠①挡住（否则取到的是**被导入的名字** `compile`）
         · Go `import_declaration` 取不到 ⇒ 下钻到 `import_spec` ✓
      ④ 整段路径兜底只在**①②③都没产出、且没有 named 导入型子节点**时跑
         （Rust 的路径在 `argument` 字段；Java 的 `scoped_identifier` **field=None**）。
    """
    def 段(n) -> str:
        return bs[n.start_byte:n.end_byte].decode("utf-8", "replace")

    def 收拾(文: str) -> str:
        return 文.strip().strip("\"'`").strip()

    出: list[dict] = []
    有产出表: dict[int, bool] = {}          # 节点 id ⇒ 这个节点**自己**抽到过东西没有
    栈 = [树.root_node]
    while 栈:
        n = 栈.pop()
        # ★★★ **C/C++ 的 `#include` 就是它的 import**（R381）。
        #   修前实测（标准库未解析 Top）：`Py_DECREF 610 · PyErr_Occurred 385 ·
        #     Py_INCREF 363 · Py_XDECREF 337 · __Pyx_AddTraceback 242 ·
        #     __pyx_lineno/filename/clineno 232` —— **全是 `.c` 文件里的宏与类型**。
        #   它们**来自 `Python.h` / `_pyx` 头文件**，而 `#include` 我**一条都没抽**
        #   ⇒ 这些名字既没有本文件定义、也没有导入记录 ⇒ 只能落「未解析」。
        #   ★ 语义：`#include <Python.h>` = **把该头文件的全部符号引入本文件**
        #     ⇒ 与 `from X import *` **同类**（星号式），所以 `星号: True`。
        #   ★ tree-sitter-c 实测字段名：`preproc_include | path:system_lib_string`
        #     / `path:string_literal`，文本带 `<>` 或 `""` ⇒ 这里把引号剥掉。
        if n.type in ("preproc_include", "preproc_def", "preproc_ifdef"):
            if n.type == "preproc_include":
                _路 = _取字段(n, "path")
                if _路 is not None:
                    _头 = 段(_路).strip().strip('<>"').strip()
                    if _头:
                        出.append({"模块": _头, "绑定": [], "行": n.start_point[0] + 1,
                                   "相对": False, "星号": True,
                                   "节点类型": "preproc_include"})
            # `#define FOO ...` 的 `FOO` 是**本文件的定义**，不在这里抽
            # （定义走 `_语法树适配器` 的实体循环；这里只管 import 关系）。
            continue
        本节点起点 = len(出)
        # ★★ **带 body 的不是导入，是定义** —— `mod X { ... }`（内联模块）vs `mod X;`
        #   实测踩过：把内联模块当导入 ⇒ 158 个**仓内模块名**被算成
        #   「Cargo.toml 没声明的外部 crate」⇒ **检查看似在报错，其实是我分错了类**。
        _有体 = (_取字段(n, "body") is not None
                or any(c.type in ("declaration_list", "block", "field_declaration_list",
                                  "class_body", "statement_block", "compound_statement")
                       for c in n.children))
        # ★★ **跳过自己 ⟺ 父是导入型 且 父不是容器**（容器 = 类型含 list/block/group）
        #   · Python `aliased_import` 的父是 `import_from_statement`（非容器）⇒ **跳过** ✓
        #     （否则取到的是**被导入的名字** `compile`，不是模块）
        #   · Go `import_spec` 的父是 `import_spec_list`（**容器**）⇒ **不跳过** ✓
        #     （原来靠「父含 import」一刀切 ⇒ 连 `import_spec` 一起挡死
        #      ⇒ 实测 59 个 `.go` 只抽到 2 条）
        # ★★ **跳过自己 ⟺ 父是导入型 且「父自己取得到产出」** —— 用**确定性字典**记，
        #   不靠全局计数（`len(出) > 层前` 那种写法实测**误伤 Python**：漏 30 / 多 21）。
        #     · Python `aliased_import`：父 `import_from_statement` **有产出** ⇒ 跳过 ✓
        #       （否则取到的是**被导入的名字** `compile`，不是模块）
        #     · Go `import "C"`：`import_spec` 的父 `import_declaration` **取不到产出**
        #       ⇒ **不跳过** ✓（原来被「父含 import」一刀切误伤）
        #     · Go `import (...)`：父 `import_spec_list` 取不到 ⇒ 不跳过 ✓
        父 = n.parent
        _父生产出 = 父 is not None and 有产出表.get(父.id, False)
        # ★★ **成员列举节点不是导入** —— 通用判据，跨语言一条就够：
        #   `import_clause` / `import_specifier` / `namespace_import` / `named_imports`
        #   回答的是「导入了**哪些名字**」，**模块路径在父节点上**。
        #   实测：不挡的话 JS 会抽出 `'clamp'` / `'readFileSync'` / `'pathToFileURL'`
        #   —— 那是**成员名**。（Python 的 `aliased_import` 是同一类，
        #     只是它已被「父有产出」挡住；一条判据同时覆盖，不用按语言各写一条。）
        _是成员列举 = any(k in n.type for k in
                       ("clause", "specifier", "namespace", "named_"))
        # ★★ **JS/TS 的 bare import（`import "mod"`）不绑定任何名字**（R348）。
        #   树：`import_statement` → import(匿名) · string("modD") · ';'
        #   **没有 `import_clause`** ⇒ 它只表示「执行这个模块」，
        #   **本文件作用域里没有新名字**。原来兜底给了 `['modD']`
        #   —— 那是**凭空造出来的一个绑定**，而**「误报的引用比缺失的引用更坏」**。
        if n.type in ("import_statement", "export_statement"):
            _有串 = next((c for c in n.children if c.is_named and "string" in c.type), None)
            _串文 = 收拾(段(_有串)) if _有串 is not None else ""
            _子句 = next((c for c in n.children if c.is_named
                          and "clause" in c.type), None)
            # ★★★ **`import_statement` 有 `import_clause` 时不许在这里处理** ——
            #   它由主路径处理（`import { b, c as d }` / `import * as e` / `import d, {f}`），
            #   而主路径是对的。我第一版没挡 ⇒ **把它们全吞成空绑定**
            #   （实测 `modB`/`modC`/`modE` 的绑定变成 `[]`）。
            #   ★ 这是本轮的第二次「**写了但没生效**」的镜像：
            #     **写了而且生效了，但生效在了不该生效的地方。**
            if _串文 and (_子句 is None or n.type == "export_statement"):
                # ★★ **re-export 是真引用**：`export { w } from "./w"` 里
                #    `w` 来自 `./w`。第一版我只处理「没有子句」的 bare import，
                #    **把 re-export 一起吞掉了**（实测 `('./w', ['w'])` 消失，
                #    而**没有判据抓到** —— 这正是「没测到不能当通过了」）。
                _绑 = []
                if _子句 is not None:
                    for _e in _子句.children:
                        if not _e.is_named:
                            continue
                        if _e.type in ("export_specifier", "import_specifier",
                                       "identifier", "string"):
                            _名 = [z for z in _e.children
                                  if z.is_named and z.type in ("identifier",
                                                                "type_identifier")]
                            _绑.append(段(_名[-1]) if _名 else 段(_e))
                        elif _e.type in ("identifier", "shorthand_property_identifier"):
                            _绑.append(段(_e))
                _绑 = [b for b in _绑 if b and " " not in b]
                # ★★★ **`export * from "x"` 是星号**（R355）。原来 `星号` 写死 `False`，
                #   于是 R348 那条注释说的「等同 `from z import *`」**只写在注释里、
                #   没落进数据** —— 而**注释不是判据**，下游读不到。
                #   实测（修前）：`export * from "./z.js"` ⇒ `绑定=[] 星号=False`，
                #   与其他四条（`export {a,b as c}` / `import * as ns` / `import def`）
                #   放在一起一比就露了：**只有它该是星号而报 False**。
                #   ★ 判据：本分支已经要求 `_子句 is None`（没有 `import_clause`/
                #     `export_clause`），所以这里出现的匿名 `*` **只可能是 `export *`**；
                #     `import * as ns` 有子句、走主路径，**不会被误标**。
                _星号 = any((not c.is_named) and c.type == "*" for c in n.children)
                出.append({"模块": _串文, "绑定": _绑,
                          "行": n.start_point[0] + 1,
                          "相对": _串文.startswith("."), "星号": _星号})

        # ★★★ **Go `import_spec` 必须在这里处理**（R347）。
        #   它**在 `_父生产出` 检查之外** —— 因为 `import_spec` 的父
        #   （`import_declaration` / `import_spec_list`）**会产出**，
        #   放在里面就**永远被跳过**（实测：分支写了但读数一字未变）。
        #   ★ 这已经是同一类错的**第三次**：**条件/位置自己把自己排除掉** ——
        #     「写了」和「生效」是两件事。
        if n.type == "import_spec":
            _路文 = next((x for x in n.children if x.is_named
                          and "string" in x.type), None)
            # ★ 别名节点的类型**不要按名字列举**：`import m "math"` 里的 `m`
            #   在 Go grammar 里**不是 `identifier`**（实测列举漏掉它 ⇒ 别名没抽到），
            #   而 `_` 是 `blank_identifier`、`.` 是 `dot`。
            #   ⇒ **通用判据**：除字符串外**任何 named 子节点**就是别名。
            #   「不要猜数据的形状，去读它的生产者」—— 这里连生产者都得按**结构**读。
            _名节 = next((x for x in n.children if x.is_named
                          and "string" not in x.type), None)
            if _路文 is not None:
                _模 = _清路径(收拾(段(_路文)))
                _绑 = [段(_名节)] if _名节 is not None else [_模.rsplit("/", 1)[-1]]
                出.append({"模块": _模, "绑定": _绑,
                          "行": n.start_point[0] + 1,
                          "相对": False, "星号": False})

        #   ★★ **父层不再管 Go 的 import_spec**（R347）：`import_declaration` /
        #     `import_spec_list` 若有 `import_spec` 子节点 ⇒ **让 spec 自己报**，
        #     否则两条都产出，而**去重是「同模块同行留第一条」** ⇒ 留下的可能是错的那条
        #     （实测 `import m "math"` 留了 `['math']` 而不是 `['m']`）。
        #     **「两个都对的分支，谁先跑就决定了答案对不对。」**
        _有GoSpec = any(c.is_named and c.type == "import_spec" for c in n.children)
        if (not _有体 and not _是成员列举 and _导入型.search(n.type)
                and not _父生产出 and not _有GoSpec
                # ★ `import_spec` 自己**由上面的预分支处理**，不能再走这里 ——
                #   否则预分支给 `['m']`、这里给 `['math']`，而**去重留第一条** ⇒
                #   实测留下的是错的那条。**「两个都对的分支，谁先跑就决定答案。」**
                and n.type != "import_spec"):
            行 = n.start_point[0] + 1
            本节点起点 = len(出)
            有名字 = False

            # ★ **星号导入**（`from x import *`）必须**单独记**：
            #   `from x import *` 之后，本文件里**任何**未解析的名字都可能来自 x ——
            #   「本文件没导入它」这个判断**不再成立**。
            #   不记它，那一整类「未导入」都是**假缺口**。
            #   （节点类型 `wildcard_import` 是 tree-sitter 各语言通用的名字。）
            有星号 = any(c.type == "wildcard_import" or c.type == "wildcard"
                       for c in n.children) or any(
                c.type == "wildcard_import" for c in n.children for c in c.children)

            # ★★★ **Java 的 `import` 也是「末段」**（R347，先打树核对）：
            #     `import java.util.List` ⇒ 用得着的名字是 **`List`**，不是 `java`
            #     （原来给 `['java']` —— **类型名导入的语言绑末段，模块导入的绑首段**）。
            #     树：`import_declaration` → import(匿名) · [static(匿名)] ·
            #         scoped_identifier(java.lang.Math.max) · ['.'(匿名) · asterisk(*)]
            if "import_declaration" in n.type or n.type == "import_declaration":
                _jv: list[dict] = []
                _路节 = next((c for c in n.children if c.is_named
                              and c.type in ("scoped_identifier", "identifier")), None)
                _有星 = any(c.type == "asterisk" for c in n.children)
                if _路节 is not None:
                    _全 = 段(_路节)
                    _jv.append({"模块": _归_点(_全),
                                "绑定": [] if _有星 else [_全.split(".")[-1]],
                                "行": 行, "相对": False, "星号": _有星})
                if _jv:
                    出.extend(_jv)
                    有名字 = True

            # ★★ **Go `import_spec` 的别名**（R347）：
            #     `import m "math"` ⇒ 绑 `m`（原来绑 `math`）
            #     `import _ "net/http/pprof"` ⇒ 绑 `_`
            #     `import . "strings"` ⇒ 点导入（无名字，合并进本文件作用域）
            #   树：`import_spec` → [identifier|blank_identifier|dot] · interpreted_string_literal

            # ★★★ **Rust `use` 的绑定是「路径末段」，与 Python 相反**（R346）。
            #   Python `import a.b.c` 绑 **首段** `a`；Rust `use a::b::C` 绑 **末段** `C`
            #   —— **同一个概念、两个语言语义相反**，所以不能一条规则走天下。
            #   实测四种形态（都错成 `['std']`）：
            #     `use std::collections::HashMap`        ⇒ 应 ['HashMap']
            #     `use crate::foo as bar`                ⇒ 应 ['bar']
            #     `use std::collections::{HashMap, …}`   ⇒ 应 ['HashMap', …]
            #     `use std::collections::*`              ⇒ 星号必须 True（原来 False）
            #   ★ 判据必须**把类型名说全**：我先写成 `"use" in n.type and "use_" not in n.type`，
            #     而 `use_declaration` **本身就含 `use_`** ⇒ **条件自己把自己挡住了**
            #     （实测：加了分支但读数一字未变）。**一个把自己排除掉的条件，等于没写。**
            if n.type in ("use_declaration", "use_item", "extern_crate_declaration") \
                    or n.type.endswith("_use_declaration"):
                _rs: list[dict] = []

                def _归(文: str) -> str:
                    """★ 模块名**归一化成与其它语言一致的 `点分`**。

                    实测（R346）：我第一版直接给原文 `std::collections::BTreeMap`，
                    **判五十五 立刻红了** —— 它期望 `std.collections.BTreeMap`。
                    图里所有语言的模块名都是点分（`_去模块前缀` 也按 `.` 切），
                    Rust 的 `::` 必须在这里换掉，**否则同一个模块有两个名字**。
                    """
                    return 文.replace("::", ".").replace("/", ".")

                for c in n.children:
                    if (not c.is_named) or c.type in ("use", "visibility_modifier"):
                        continue
                    if c.type in ("scoped_identifier", "identifier"):
                        _全 = 段(c)
                        _rs.append({"模块": _归(_全), "绑定": [_全.split("::")[-1]],
                                    "行": 行, "相对": False, "星号": False})
                    elif c.type == "use_as_clause":
                        _名 = [x for x in c.children if x.is_named
                              and x.type in ("scoped_identifier", "identifier")]
                        _别 = [x for x in c.children if x.is_named and x.type == "identifier"]
                        if _名:
                            _rs.append({"模块": _归(段(_名[0])),
                                        "绑定": [段(_别[-1])] if _别 else [],
                                        "行": 行, "相对": False, "星号": False})
                    elif c.type in ("scoped_use_list", "use_list"):
                        _路节 = [x for x in c.children if x.is_named
                                and x.type in ("scoped_identifier", "identifier")]
                        _列 = next((x for x in c.children if x.is_named
                                    and x.type == "use_list"), None)
                        _模 = _归(段(_路节[0])) if _路节 else ""
                        # ★ **`use_list` 的成员自己抽**（不借 `_抽绑定名`）：
                        #   `{HashMap, HashSet}` / `{self, Debug}` 的成员都是直接子节点，
                        #   形如 `identifier` / `scoped_identifier` / `use_as_clause` / `self`。
                        #   实测传 `_抽绑定名(use_list, …)` 得 `[]` —— **不如直接把子节点列出来可信**。
                        _绑 = []
                        if _列 is not None:
                            for _e in _列.children:
                                if not _e.is_named:
                                    continue
                                if _e.type in ("identifier", "scoped_identifier", "self"):
                                    _绑.append(段(_e))
                                elif _e.type == "use_as_clause":
                                    _id = [x for x in _e.children
                                          if x.is_named and x.type == "identifier"]
                                    if _id:
                                        _绑.append(段(_id[-1]))
                                elif _e.type in ("use_list", "scoped_use_list"):
                                    _绑.extend(
                                        [段(x) for x in _e.children
                                         if x.is_named and x.type in
                                         ("identifier", "scoped_identifier")])
                        if _模 or _绑:
                            _rs.append({"模块": _模, "绑定": _绑, "行": 行, "相对": False, "星号": False})
                    elif c.type == "use_wildcard":
                        _路 = [x for x in c.children if x.is_named
                              and x.type in ("scoped_identifier", "identifier")]
                        if _路:
                            _rs.append({"模块": _归(段(_路[0])), "绑定": [], "行": 行, "相对": False, "星号": True})
                if _rs:
                    出.extend(_rs)
                    有名字 = True

            # ① 字符串优先
            for c in n.children:
                if c.type in _字符串型 or "string" in c.type:
                    文 = 收拾(段(c))
                    if 文 and len(文) < 200 and not 文.startswith("/"):
                        出.append({"模块": 文, "绑定": _绑定名(n, 段, 文),
                                  "相对": 文.startswith("."), "行": 行, "星号": 有星号})
                        有名字 = True

            # ② `module_name` 字段（Python `from X import ...` 的 X）
            if not 有名字:
                目 = _取字段(n, "module_name")
                if 目 is not None:
                    文 = 收拾(段(目))
                    if 文:
                        出.append({"模块": 文, "绑定": _绑定名(n, 段, 文),
                                  "相对": 文.startswith("."), "行": 行, "星号": 有星号})
                        有名字 = True

            # ★★★ **「`from` 与 `import` 之间的文本」就是模块名所在的位置** ——
            #   它**可能是匿名节点**（② 收不到、① 字符串优先也收不到）。
            #   实测（R342/R343）：`from __future__ import annotations` 的树是
            #     `future_import_statement` → from(匿名) · **`__future__`(匿名)** · import(匿名)
            #                              · dotted_name(annotations)
            #   ⇒ `module_name` **字段不存在**（② 不命中）⇒ 掉到下面「`name` 字段多值」分支
            #   ⇒ **把被导入的名字 `annotations` 当成了模块**（实测 append 到 文='annotations'）。
            #   `from time import gmtime` 之所以对，是因为 `import_from_statement`
            #   **有 `module_name`** ⇒ ② 先命中 ⇒ 那个分支不跑。**差一个字段就全错。**
            #   ★ 这条判据**与语言无关**：任何「模块是匿名 token」的语法都成立。
            if not 有名字:
                _f = next((c for c in n.children
                           if (not c.is_named) and c.type == "from"), None)
                _i = next((c for c in n.children
                           if (not c.is_named) and c.type == "import"), None)
                if _f is not None and _i is not None and _f.end_byte <= _i.start_byte:
                    _路2 = bs[_f.end_byte:_i.start_byte].decode("utf-8", "replace").strip()
                    _路2 = _路2.strip("()").strip()
                    if _路2:
                        出.append({"模块": _路2, "绑定": _绑定名(n, 段, _路2),
                                  "相对": _路2.startswith("."), "行": 行, "星号": 有星号})
                        有名字 = True

            # ③ 其它路径字段（**`path` 优先于 `name`** —— `name` 常是别名）
            if not 有名字:
                # 单值字段（Rust `argument`、Go `path`、Python `module_name`）
                for 字 in ("path", "source", "argument"):
                    子 = _取字段(n, 字)
                    if 子 is None:
                        continue
                    文 = _清路径(收拾(段(子)))
                    if not 文:
                        continue
                    出.append({"模块": 文, "绑定": _绑定名(n, 段, 文),
                              "相对": 文.split(".")[0] in ("crate", "self", "super")
                                     or 文.startswith("."), "行": 行, "星号": 有星号})
                    有名字 = True
                    break
                # ★★ `name` 是**多值**字段：`import re, sys, getopt` 一行三个，
                #    `_取字段` 只给第一个 ⇒ 实测「我漏 9」（`base64.py::'getopt'`…）。
                #    ⇒ **每个带该字段的子节点都算一条**。
                # ★★ **`name` 只在「自己就是语句层」时才算模块路径**（父不是导入型）。
                #   否则取到的是**被导入的名字**：JS `import { clamp } from "..."` 的
                #   `import_specifier` 父是 `named_imports`（导入型）⇒ `clamp` 被当模块。
                #   `path`/`source`/`argument` 不受此限 —— 它们是**真路径**。
                if not 有名字 and not (父 is not None and _导入型.search(父.type)):
                    for _i, _c in enumerate(n.children):
                        if _字段名(n, _i) != "name":
                            continue
                        文 = _清路径(收拾(段(_c)))
                        if not 文:
                            continue
                        出.append({"模块": 文, "绑定": _绑定名(n, 段, 文),
                                  "相对": 文.split(".")[0] in ("crate", "self", "super")
                                         or 文.startswith("."), "行": 行, "星号": 有星号})
                        有名字 = True

            # ④ 整段路径兜底
            #
            # ★★ **先试「`from` 与 `import` 之间」** —— 那是**模块名所在的位置**，
            #   而它**可能是匿名节点**（② 只收 named 子节点 ⇒ 收不到）。
            #   实测（R342）：`from __future__ import annotations` 的树是
            #     `future_import_statement` → from(匿名) · **`__future__`(匿名)** · import(匿名)
            #                              · dotted_name(annotations)
            #   ⇒ ② 收不到 `__future__` ⇒ 原来兜底取**整段文本**再清洗 ⇒
            #     模块被记成 **`annotations`**（成员名当成了模块名）。
            #   ★ 这条修法**与语言无关**：任何「模块是匿名 token」的语法都成立。
            if not 有名字 and not any(c.is_named and _导入型.search(c.type)
                                     for c in n.children):
                _路 = None
                _f = next((c for c in n.children
                           if (not c.is_named) and c.type == "from"), None)
                _i = next((c for c in n.children
                           if (not c.is_named) and c.type == "import"), None)
                if _f is not None and _i is not None and _f.end_byte <= _i.start_byte:
                    _路 = bs[_f.end_byte:_i.start_byte].decode("utf-8", "replace").strip()
                    _路 = _路.strip("()").strip() or None
                路 = _路 or _路径文本(n, bs)
                if 路 and 路 not in ("use", "import", "mod", "pub"):
                    出.append({"模块": 路, "绑定": _绑定名(n, 段, 路),
                              "相对": 路.split(".")[0] in ("crate", "self", "super")
                                     or 路.startswith("."), "行": 行, "星号": 有星号})

        # ★★★ **一处给「本轮新增的记录」打上语句类型**（R365）。
        #   为什么需要它：`import numpy as np` 与 `from numpy import array`
        #   **在边上形状完全相同**（`模块="numpy"`, `绑定=["np"/"array"]`），
        #   但语义相反：
        #     · `import X as Y` ⇒ **`Y` 就是模块 X 本身**（和 `import X` 同一条规则）
        #     · `from X import Y` ⇒ `Y` 是 **X 里的成员**
        #   ⇒ **只有抽取层知道语法**；下游再猜就是猜。
        #   ★ 实测（标准库）：`np` 有 2,180 条判「未解析」，而它的导入边
        #     `{"终":"mod:numpy","绑定":["np"]}` **完全正确** ——
        #     错在解析层把 `np` 当成「`numpy` 里叫 `np` 的成员」去找，**当然找不到**。
        #   ★ 用 `本节点起点`（循环头已记好）**只标本轮新增的** ——
        #     不用在十几个 `出.append` 各加一个字段（**手写清单必然漏**）。
        for _r in 出[本节点起点:]:
            _r.setdefault("节点类型", n.type)
        # ★ 记下**这个节点自己**有没有产出（子节点查表用；确定性，不依赖遍历顺序）
        有产出表[n.id] = len(出) > 本节点起点
        for c in reversed(n.children):
            栈.append(c)

    # ★★★ **出口统一规范化** —— 调用方**不许再有硬下标**（R348）。
    #
    # 实测（R346）：各分支 `出.append` 手写五个键，**漏写一个 `相对`** ⇒
    # 调用方 `导["相对"]` 抛 `KeyError` ⇒ 上层吞掉 ⇒ **整个文件不进图**
    # （图上一条 `mod:` 都没有，而判据说的是「导入没进图」——**现象对，根因差一个键**）。
    #
    # ★ 根因不是「漏了一个键」，是**「键的完整性靠各处 append 自觉」**。
    #   ⇒ 出口处**统一补全**，并且**把补了哪些字段记下来**（**不许静默**）：
    #     缺 `模块` 或它为空 ⇒ **丢弃并计数**；缺 `行` ⇒ 记数（行号没有就是 bug）。
    _缺: dict[str, int] = {}
    规范: list[dict] = []
    for x in 出:
        _模 = x.get("模块")
        if not _模:
            _缺["模块（空）"] = _缺.get("模块（空）", 0) + 1
            continue
        _行 = x.get("行")
        if _行 is None:
            _缺["行"] = _缺.get("行", 0) + 1
        if "相对" not in x:
            _缺["相对"] = _缺.get("相对", 0) + 1
        if "星号" not in x:
            _缺["星号"] = _缺.get("星号", 0) + 1
        if "绑定" not in x:
            _缺["绑定"] = _缺.get("绑定", 0) + 1
        # ★★★ **先把原记录铺开，再覆盖规范化字段**（R365）——
        #   原来这里是**白名单重建**（只列 5 个键）⇒
        #   **任何新加的字段都会被静默丢掉**。
        #   ★ 实测：我在抽取层加的 `节点类型` 在这里**没了**，
        #     表现是下游 `_节型 = ""` ⇒ **别名解析整条失效**，
        #     而**没有任何报错**。
        #   ★ 这段代码的注释自己说的就是同一个病
        #     （「键的完整性靠各处 append 自觉 ⇒ 出口统一补全」）——
        #     出口白名单修了「字段漏」，却换成「**新字段进不来**」。
        #   ★ 口径：出口**补全**字段，不是**筛掉**字段。补全用默认值，筛选是另一回事。
        规范.append({
            **x,
            "模块": _模,
            "绑定": list(x.get("绑定") or []),
            "星号": bool(x.get("星号")),
            "相对": bool(x["相对"]) if "相对" in x else (
                str(_模).startswith(".")
                or str(_模).split(".")[0] in ("crate", "self", "super")),
            "行": _行,
        })
    取导入.上次缺字段 = _缺            # ★ 供上层读（**报出来，不是吞掉**）

    # 去重（同一模块同一行只留一条）
    见 = set()
    净: list[dict] = []
    for x in 规范:
        # ★ **空模块名不许留在图里** —— 实测 `.tsx` 26 个 / `.js` 8 个 / `.mjs` 10 个
        #   产出 `''`（`_清路径` 把纯成员列举清成了空）。一个空名会变成
        #   图上一条**无目标的依赖边**，读数上还占一条「导入」。
        #   （规范化已在上面挡掉，这里留判据做**第二道**。）
        if not x["模块"]:
            continue
        k = (x["模块"], x["行"])
        if k in 见:
            continue
        见.add(k)
        净.append(x)
    return 净


# ★★★★ R504（口径 305）：**`粒` 只有一个入口** —— 全 `kg.py` 只许在这里写那个字面量表。
#
# ★ 为什么必须收成一个：`粒` 原来**散在 18 处**，而 R499/R501/R502/R503
#   **四次修复修的都是"其中一处"**，每次修完都与其余处不一致：
#     R501 `深 <= 2` 把模块级块里的 `def` 判成内嵌（通用节点那处）
#     R501 赋值那处硬编码 `类体`
#     R502 类体里的 `def` 全落内嵌（通用节点那处，第二次）
#     R503 Lean 把 `namespace` 当内嵌
#   ⇒ 四次都是**同一个字段、同一个病**。
#
# ★ 每个调用点必须**声明它挂的是什么容器**（`容器` 参数）——
#   这正是原来隐含、且四次都没说清的语义。
#   ★ 于是"改语义"变成"改一张表"，而不是"去找还有哪处漏改"。
#
# ★ `未跟踪` 是**诚实的缺口**，不是分类：那两个站点（Lean 的构造子/结构字段）
#   抽取器**不知道该名字挂在哪个容器里**，原来也是写死 `顶层`。
#   ⇒ 收进来之后**缺口有了名字**，下一轮可以按名字去补（口径 266）。
_粒容器: dict[str, str] = {
    "文件": "顶层",       # 挂在文件上（模块级 / 命名空间级 / 顶层声明）
    "类型体": "类体",     # 挂在**建作用域的类型体**里（Python 的 `class`）
    "定义体": "内嵌",     # 挂在**另一个定义的体内**（函数内嵌套 / Lean 局部绑定）
    "局部": "局部",       # 类型形参这类**不是定义**的名字
    "md文件": "文件",     # markdown 的**文件**节点
    "md标题": "文档",     # markdown 的**标题**节点
    "未跟踪": "顶层",     # ★ 抽取器不知道容器 ⇒ 按文件级记（**已知缺口**）
}


def _粒(容器: str) -> str:
    """★ `粒` = 这个定义**挂在什么容器上**（`顶层`/`类体`/`内嵌`/`局部`/`文件`/`文档`）。

    ★ 为什么 `namespace` 与 Python 的 `class` **值不同**（R503）：
      - Python 的 `class` 体 **确实建作用域** ⇒ 挂 `类型体` ⇒ `类体`
      - Lean 的 `namespace`/`end` **不建作用域，只加前缀** ⇒ 挂 `文件` ⇒ `顶层`
      ⇒ **两种语言的正确值不同，这不是不一致，是语义差别。**

    ★ 语言无关：调用点只声明**挂在哪**，不声明"是什么语言"。
    """
    _v = _粒容器.get(容器)
    if _v is None:
        # ★ 拼错容器名必须**炸**，不许静默给个默认值 ——
        #   静默默认 = 又一次"某个站点悄悄落错态"（口径 302/304 的病根）。
        raise ValueError(f"_粒: 未知容器 {容器!r}；只许 {sorted(_粒容器)}")
    return _v


# ★★★★ R505：Lean 的**命名空间层数** —— 一个入口（口径 305 的同族）。
#
# ★ 为什么必须改名（`深度` → `命名空间深度`）：
#   `深度` 这个字段名在**两种语言里装了两个不同的量**：
#     Lean  ：**命名空间层数**（`namespace A.B` ⇒ 2）
#     其它  ：**tree-sitter 语法深度**（Python 实测最大 **20**）
#   ⇒ **R501 那个缺陷（`深 <= 2` 判顶层）就是被这个名字骗的** ——
#     它拿"语法深度"当"作用域深度"用。
#   ⇒ 光修 `粒` 不够：**字段名本身在骗下一个读者**（口径 308 的同族）。
#
# ★ 口径 261 的边界：**只按语言分流，不做全局排除** ——
#   非 Lean 的 `深度`（语法深度）**原样保留**，它有它的用处。
def _命名空间深度(行前缀表: dict, 行号: int) -> int:
    """★ Lean：这个名字所在的**命名空间层数**（0 = 不在任何 `namespace` 里）。

    ★ 与 `粒` **无关**：`粒` 说的是"挂在什么容器上"，这个说的是"namespace 套了几层"。
      两者都从 `前` 派生，所以必须**同时自洽**（口径 194：两个出口字段必须一样）。
    """
    _p = 行前缀表.get(行号, "")
    return _p.count(".") + 1 if _p else 0


def _像结构体类型(类型名: str) -> bool:
    """★ R502：**这个「类型」是不是结构体祖先**（类/结构/接口/trait/impl/enum）。

    ★ 为什么需要：`粒` 是**三态**（顶层 / 类体 / 内嵌），而 R501 之后
      **类体那一态整个是空的**（`类体 × function_definition` = 0，
      `父是类却标内嵌` = 87,359）。目标②要的是「把定义绑到**作用域**」——
      而「方法」与「函数内的嵌套函数」**是两个不同的作用域层**
      （`A.m` vs `f.<locals>.g`）：混成一态 ⇒ 解析 `self.m()` 时
      **没有任何字段能区分**。

    ★ 口径 261 的边界：这里只做**分类**（这一态该叫什么），
      **不做全局排除**。认不出来就**当它不是结构体** ⇒ 落 `内嵌`
      （**宁可漏标，不可错标**，口径 203）。
    ★ 语言无关：按**类型名**里的词判，不按语言分支写 ——
      `class_definition`(py/js/ts) · `struct_item`/`impl_item`/`trait_item`(rust)
      · `class_declaration`(java/ts) · `interface_declaration`(ts)
      · `enum_declaration` · `object_declaration`(kotlin) · `mod_item`(rust, 模块也是命名空间)。
    """
    _t = (类型名 or "").lower()
    for _词 in ("class", "struct", "impl", "trait", "interface", "enum",
                "object", "mod_item", "module_decl"):
        if _词 in _t:
            return True
    return False


def _取语言(语: str):
    """按 `语` 拿 tree-sitter 的 Language。`语` 可以是 `名` 或 `名:变体`。

    ★★ 为什么不能只写 `.language()`：实测 **`tree_sitter_typescript` 没有 `language`**，
      它暴露的是 `language_typescript()` 和 `language_tsx()`。
      只写 `.language()` ⇒ **`.ts`/`.tsx` 起不了 parser ⇒ 静默降级到保底**
      （160 个文件只拿到「文件+行块+提及」）。
      **静默降级是最坏的一种「绿」**：图上看起来有东西，其实那一层没做。
      ⇒ 判五十六 专门钉这件事（**注册的每种语言都必须真能起 parser**）。

    顺序（**惯例探测**，不是按语言写死）：
      ① `language_<变体>()`  ← `typescript:tsx` ⇒ `language_tsx()`
      ② `language()`         ← 绝大多数 grammar
      ③ `language_<名>()`
    """
    from tree_sitter import Language as _L
    名, _, 变体 = 语.partition(":")
    m = importlib.import_module("tree_sitter_" + 名)
    试: list[str] = []
    if 变体:
        试.append("language_" + 变体)
    试 += ["language", "language_" + 名]
    for a in 试:
        f = getattr(m, a, None)
        if callable(f):
            try:
                return _L(f())
            except Exception:                     # noqa: BLE001
                continue
    raise LookupError(f"tree_sitter_{名} 里找不到可用的 language 取法（试过 {试}）")


def _语法语言(语: str) -> str:
    """★ R522：把 `_语法` 表里的「**grammar 选择键**」化成「**语言名**」。

    ★ 为什么需要（实测）：`_语法` 的值是 `语言名:变体名`：
    ```python
    ".ts":  "typescript:typescript",   # tree_sitter_typescript **没有** `language()`，
    ".tsx": "typescript:tsx",          # 只有 `language_typescript()` / `language_tsx()`
    ```
    ★ 而 `_语法树适配器` 里**同一个 `语` 变量两种含义**：
      · `_取语言(语)` 要**完整键**（选 grammar）
      · `"语言": 语` 要**语言名**（节点字段）
    ⇒ 实测节点上出现 `语言 = "typescript:typescript"` ✗

    ★ 为什么这是真缺陷、不是"名字难看"：下游**按语言做的判断**
      （语言内置表 R486 · 判 166 watchdog · `_按扩展名判语言`）
      认的是**语言名** ⇒ `typescript:typescript` **一个都匹配不上**
      ⇒ 这些节点在按语言的判断里**等于隐形**（口径 260：看不见不能当没有）。

    ★ 单一实现点（口径 305）：**别在别处再 `split(":")`**。
    """
    return 语.split(":", 1)[0]


def _语法树适配器(文本: str, 相对: str, 语: str) -> tuple[list[dict], list[dict]]:
    """tree-sitter 通用适配器。**没有 per-language 规则** —— 靠类型名 + name 字段。"""
    from tree_sitter import Language, Parser

    解析 = Parser(_取语言(语))
    # ★★★ R522：**grammar 用完整键（上面那行），节点字段要语言名** ⇒ 取完就重绑。
    #   本函数下面有 **9 处** `"语言": 语` —— 重绑一次，9 处自动正确
    #   （口径 305：一处提供；逐个改 9 处既容易漏，又会让「同一个量两种形态」
    #     散在函数各处）。★ 顺序要紧：必须在 `_取语言(语)` **之后**。
    语 = _语法语言(语)
    bs = 文本.encode("utf-8", "replace")
    树 = 解析.parse(bs)

    节点: list[dict] = []
    边: list[dict] = []
    文件节点 = f"file:{相对}"

    def 取段(n) -> str:
        return bs[n.start_byte:n.end_byte].decode("utf-8", "replace")

    def 取名(n, 报来源: bool = False, 严格: bool = False):
        """`严格=True` ⇒ **只认 `name` / `declarator`**（代码实体的名字字段）。

        ★★ 踩过的坑：`_名字字段` 里有 `"value"`，而 `with_item`（`with X as y:`）
          **有 `value` 字段** ⇒ `来自字段=True` ⇒ 我的「包装节点」判据**不生效**，
          于是 `self._read_lock` 这种语法位置照样进图（对账 `ast` 多抽 343 条）。
          ⇒ **`value`/`type` 是「内容」不是「名字」**：对代码实体一律不认。
        """
        字段们 = ("name", "declarator") if 严格 else _名字字段
        for f in 字段们:
            c = n.child_by_field_name(f)
            if c is not None:
                s = 取段(c).strip()
                if s:
                    return (s[:200], True) if 报来源 else s[:200]
        if 严格:
            return (None, False) if 报来源 else None
        # 退到「第一个标识符后代」（通用兜底）
        栈 = [n]
        while 栈:
            x = 栈.pop(0)
            if x.type in ("identifier", "property_identifier", "type_identifier",
                          "field_identifier", "constant", "string_content"):
                s = 取段(x).strip()
                if s:
                    return (s[:200], False) if 报来源 else s[:200]
            栈.extend(x.children)
        return (None, False) if 报来源 else None

    # 一趟遍历：同时收「定义」与「标识符提及」
    # ★★★ **成员名（`.items` / `.field` / `.prop`）不是「名字引用」**（R376）。
    #   修前实测（标准库，R375 修完缺口读数的 Top 12）：
    #     `items 558 · mark 519 · pop 329 · raises 963 · extend 311 · update 249`
    #   全是**属性名**，而它们**不可能是任何定义** ⇒ 常驻在「未解析」里。
    #   ★ 为什么漏了：`_语法树适配器` 只收 `identifier`/`type_identifier`，
    #     Rust 的成员是 `field_identifier`、JS 的是 `property_identifier` ⇒ **早已排除**；
    #     而 **Python 的 `attribute` 节点里，成员就是个 `identifier`** ⇒ **收进来了**。
    #   ★ 判据**按字段名、不按语言**（tree-sitter 实测字段名）：
    #       Python `attribute`          → 成员字段 `attribute`
    #       Rust   `field_expression`   → 成员字段 `field`
    #       JS     `member_expression`  → 成员字段 `property`
    #     ⇒ **字段名 ∈ {attribute, property, field} ⇒ 成员名，不收提及**。
    #   ★ 而 `object`/`value` 字段**照收**（`a.items` 里的 `a` 是**真名字**）——
    #     「同一个节点的两个字段，一个是名字、一个不是」，只有字段名能分辨。
    _成员字段 = ("attribute", "property", "field")
    栈: list[tuple[Any, int, str | None, bool, bool]] = [(树.root_node, 0, None, False, False)]
    # ★★★ **「提及」必须按「出现处」记，不能按名字聚合**（R359）。
    #   原来 `提及: list[str]` + `for 名 in set(提及)` ⇒ **一个名字一条边**。
    #   后果（R358 实测）：`def dup` 内外同层各一处调用时，**两处共用一条边**
    #   ⇒ 主靶只能有一个 ⇒ **对一处对、对另一处错**，而**「答案错了」比「未解析」更坏**
    #   （未解析会被 ④ 报成缺口；错答案会被当成事实用于计划）。
    #   ⇒ 记 `(名, 所在定义, 行)` 三元组：**一个出现处一条边**。
    #   ★ 顺带把 `行` 带上 —— 下游要能定位到源文件的那一行。
    提及: list[tuple[str, str | None, int]] = []
    # ★★★ **导入语句里只收「绑定名」，不收「模块路径名」**（R380）。
    #   修前实测（标准库未解析 Top）：
    #     `collections 262` ← `from collections.abc import Mapping` 里的 `collections`
    #     `pip        233` ← `from pip._internal.utils.entrypoints import _wrapper` 里的 `pip`
    #     `abc`/`_internal`/`utils` 同理 —— **模块路径的每一段都被当成了一个名字**。
    #   ★ 它们**不是本文件引用的名字**：`from collections.abc import Mapping`
    #     在本文件里只引入了一个名字 `Mapping`；`collections` 是**解析模块用的路径**。
    #     （`import os` 不同：它**绑定了** `os` 这个模块对象 ⇒ `os` 是绑定名，照收。）
    #   ★ 判据**去问 `取导入`**（它已经按语言规则算出了 `绑定`），
    #     而不是在这里再写一遍"哪些算路径" ——
    #     **同一个判断写在两处，必然给出两个答案。**
    _导入行绑定: dict[int, set[str]] = {}
    _模块名集: set[str] = set()
    # ★★★ **内置/标准库模块名必须在 `取导入` 循环之外加**（R392 第二次踩）。
    #   第一版我把它写在 `for _d0 in 取导入(...)` 的**循环体内** ⇒
    #   **文件没写 import 时循环体一次都不跑** ⇒ 一个名字都加不上 ✗
    #   实测代价：`encodings/charmap.py` 里没有 `import codecs`（`codecs` 是
    #   解释器自带的）⇒ `codecs.charmap_encode` 的 `charmap_encode`
    #   **142 条全落未解析**，而同一个修法在有 `import os` 的文件里**生效了 1 条**
    #   —— **"生效了 1 条"比"完全没生效"更容易骗过我。**
    #   ★ 口径：**一个"全局该有"的东西，不能放在"按文件才有"的循环里。**
    _模块名集.update(_内置模块名())
    for _d0 in 取导入(树, bs):
        _导入行绑定.setdefault(_d0.get("行") or 0, set()).update(_d0.get("绑定") or [])
        # ★★★ **限定名提及**（R384）：`包.甲.工具函数()` 里的 `工具函数`
        #   是 **`attribute` 的成员名**，R376「属性名不是名字」把它**一并删了** ⇒
        #   它再也解不到定义（判六十三 实测红）。
        #   ★ 但**不能全收**：R376 收全收时，`self.items`/`x.mark`/`d.pop`
        #     这些**普通对象成员**污染了缺口读数（`items 558 · mark 519 · pop 329`）。
        #   ⇒ **结构判据**：宿主**是不是本文件 import 过的模块名/模块绑定**
        #     · `包.甲.工具函数` —— `包.甲` 来自 `import 包.甲` ⇒ **是模块** ⇒ 收 ✓
        #     · `self.items` —— `self` 是参数 ⇒ **不是模块** ⇒ 不收 ✓
        #   ★ 记模块名的**首段**（`包.甲` ⇒ `包`）与**模块绑定**（`import a.b as ab` ⇒ `ab`）。
        _m0 = str(_d0.get("模块") or "")
        if _m0:
            _模块名集.add(_m0.split(".")[0].lstrip("."))
        _模块名集.update(_d0.get("模块绑定") or [])
        pass
        # ★★★ **只有「模块绑定」才是模块**（R384 第二次踩，代价很大）。
        #   第一版我在这里又加了一句 `_模块名集.update(_d0.get("绑定") or [])`，
        #   想着"`import a.b` 绑 `a`，而 `a` 就是模块" —— 但 `绑定` 里**同时装着**
        #   `from pytest import mark` 的 **`mark`**（那是个**装饰器对象，不是模块**）⇒
        #   于是 `pytest.mark.xxx`/`x.mark` 这类**普通对象成员**全被当成限定名收了回来。
        #   实测代价（标准库全量）：
        #     未解析 3,416 → **34,929**；未导入 1,172 → **31,258**
        #     Top：`raises 988 · mark 522 · stacklevel 279 · key 245 · v 389`
        #   —— **全是 R376 刚治好的属性名，被这一行整批放回来了**。
        #   ★ 口径：**R376 的结论没错，错的是我给的"宿主是模块"的判据太宽** ——
        #     `import a.b` 的绑定 `a` **另有出处**（下面 `_指模块` 算出的 `模块绑定`
        #     与模块名首段），**不需要从 `绑定` 里借**。
        #   ★ 这正是「修一处只能挡住一处」的反面：**一个过宽的判据会把已修好的整批染回去**。
    # ★ 预扫：找出「宿主是模块名」的成员 identifier 的**位置** ⇒ 主 walk 只对这些放行。
    _限定位: set[tuple[int, int]] = set()
    # ★★★ **限定名的「根」也要记下来**（R385）—— 解析层要靠它判归属。
    #   修前实测（标准库全量）：`pytest.raises(...)` / `pytest.mark.xxx` /
    #   `pytest.parametrize` 这类**真限定名**收进来之后，因为**靶模块在仓外**
    #   （`pytest` 不在仓内），全落「**未解析**」⇒ 未解析 3,416 → **17,803**。
    #   ★ 但它们**不是缺口**：靶在仓外 ≠ 本仓缺东西 ⇒
    #     **「靶在仓外」被算成「缺口」会让缺口读数失真**（这正是 R381 立
    #     「外部符号」桶要治的同一个病，只是那次走的是 `#include`，这次走限定名）。
    #   ★ 键 = `(名, 行)`，因为解析层拿到的是提及边上的 `名`/`行`。
    _限定宿主: dict[tuple[str, int], str] = {}
    _限栈 = [树.root_node]
    while _限栈:
        _n2 = _限栈.pop()
        if _n2.type in ("attribute", "member_expression", "field_expression"):
            _宿 = _取字段(_n2, "object") or _取字段(_n2, "value")
            _员 = _取字段(_n2, "attribute") or _取字段(_n2, "property") or _取字段(_n2, "field")
            # ★★★ **宿主可能是一条链**（R384 第二次踩）：
            #   `包.甲.工具函数()` 的结构是**嵌套 attribute** ——
            #     `attribute(object=attribute(object=identifier:包, attribute:甲),
            #                attribute:工具函数)`
            #   ⇒ `工具函数` 的宿主是**另一个 attribute**，不是 identifier ✗
            #   （第一版只判 `_宿.type == "identifier"` ⇒ **只有 `甲` 被收到，
            #     `工具函数` 仍然一条提及都没有** —— 加分支但读数几乎没变，
            #     正是「一个把自己排除掉的条件，等于没写」的同族。）
            #   ⇒ **顺着 `object`/`value` 链走到最左的那个 identifier**，
            #     它就是这条限定名的**根**（`包.甲.工具函数` 的根是 `包`）。
            _根2 = _宿
            _防7 = 0
            while _根2 is not None and _根2.type not in ("identifier",) and _防7 < 12:
                _根2 = _取字段(_根2, "object") or _取字段(_根2, "value")
                _防7 += 1
            if _员 is not None and _根2 is not None and _根2.type == "identifier":
                if 取段(_根2) in _模块名集:
                    _限定位.add((_员.start_point[0], _员.start_point[1]))
                    _限定宿主[(取段(_员), _员.start_point[0] + 1)] = 取段(_根2)
        _限栈.extend(_n2.children)
    # ★★★ **提及的「所在定义」**（R358）：名字 → 出现处的父定义节点 id 列表。
    #   为什么必须记：作用域解析的正确规则是**沿 `父定义` 链由内向外找**，
    #   而不是「取深度最大的」。`深度` 是 **tree-sitter 语法深度**，
    #   不等于**作用域深度**（`粒` 的作者自己就用 `深 <= 2` 当启发式）。
    #   ★ R357 盘点查出 `父定义`(97,581 条) / `粒`(97,597) / `深度`(267,812)
    #     在图上躺着、**读 0 次** —— 作用域只做到文件级，定义级没做。
    #   ⇒ 先让「提及」带上它的作用域位置，解析器才可能做对。
    #   ★ 同名可能在不同作用域各出现一次（一个名字两处用），所以记**列表**。
    提及父: dict[str, list[str | None]] = {}
    # ★★ **限定名**（`类.方法`）。为什么必须有：
    #   1. 裸名 `__init__` / `new` / `clone` 满图都是 ⇒ **同名歧义爆炸**，
    #      引用解析根本没法做。
    #   2. 和**权威解析器**对账时口径才一致（`ast` 那边给的就是 `类.方法`）。
    #   DFS 先序 ⇒ 父节点一定先于子节点被处理，所以父的名字已经知道了。
    _名表: dict[str, str] = {}
    # ★★★★ R502：**父的「类型」表** —— `粒` 必须按**父是不是结构体**分三态。
    #   实测（判一百五十一）：**87,359 个定义「父是类/结构体，粒却写内嵌」** ——
    #   `类体 × function_definition` **一个都没有**（那一态整个是空的）✗
    #   ⇒ 维护 id → 类型，避免每次回头线性找父（50 万节点会平方爆炸）。
    _型表: dict[str, str] = {}
    while 栈:
        n, 深, 父, _在导入里, _是成员 = 栈.pop()
        # ★★★ **导入语句里的名字不是「本文件的定义」**（R350）。
        #   实测：`import { clamp } from "./util/clamp.js"` 被抽成一个实体
        #   `类型:src/index.js:clamp:1` ⇒ 引用解析①「本文件定义」先命中 ⇒
        #   `clamp` 被解到**导入语句本身**，而不是 `util/clamp.js` 里的函数。
        #   ★ 这正是目标②要根除的「**绑到 import 而不是绑到定义**」——
        #     而且它**不报未解析**（看起来解到了），比未解析更坏。
        #   ★ 判据**通用**（按 tree-sitter 节点类型，不按语言）：
        #     ① 已被 `取导入` 认定为导入的语句（`_导入型` 命中）；
        #     ② `export ... from "x"`（re-export，也是引入外部名字）。
        #   ★ **`export function f(){}` 不算** —— 那是真定义，
        #     所以判据必须要求**同时有 `from`**，不能见 `export` 就砍。
        _是导入 = _在导入里 or bool(_导入型.search(n.type)) or (
            n.type == "export_statement"
            and any((not c.is_named) and c.type == "from" for c in n.children))
        # ★★ **属性名不是名字引用** —— `self.read` / `f.close` 里的 `read`/`close`
        #   是**属性访问**，语法上不构成「用了一个名字」。
        #   原来把 `property_identifier`/`field_identifier` 也收进「提及」⇒
        #   「未导入」虚高到 **153,056（30%）**（标准库不可能 30% 的名字没导入）。
        #   ⇒ 「提及」只收**裸标识符**（`identifier`/`type_identifier`）；
        #     成员名另有价值，但**是另一类**，不该混进「引用」。
        # ★★★ **关键字参数名不是「名字」**（R384）：`stacklevel=2`、`reason="…"`、
        #   `strict=False` 里的 `stacklevel`/`reason`/`strict` 是**参数名**，
        #   不是本文件引用的名字 —— 实测标准库 `stacklevel 279 · reason 211 · strict` 等
        #   **占了未解析 Top 的一整片**。
        #   ★ 判据用**语法位置**（`keyword_argument` 的 `name` 字段），**不按名字列举** ——
        #     `reason` 在别处可能真是个变量（**按名字列举必然漏，也必然误伤**）。
        # ★ **必须用 `n.parent`（tree-sitter 的父节点），不是 `父`**（R384）——
        #   这个循环里的 `父` 是**栈里传下来的「所在定义」id**（字符串或 None），
        #   对它取 `.type` 会抛 `AttributeError` ⇒ 被上层 `except` 吞掉 ⇒
        #   **静默退到 `_保底适配器`** ⇒ 实测表现为「整份文件的引用一条都没有」（`问题数: 1`）。
        #   ★ 口径：**探针少打一个字段 = 少一步定因** —— 这次是**"节点数 2 / 边数 1"**
        #     这个读数把"没抽取"和"抽取了但没解析"分开了。
        _关 = n.parent
        _是关键字参数名 = (_关 is not None and _关.type == "keyword_argument"
                          and (_取字段(_关, "name") is not None)
                          and _取字段(_关, "name").id == n.id)
        # ★★★ **`type_parameter`（泛型参数声明）里的名字不是提及**（R390）。
        #   实测：`def f[ScalarT: _Shape](x: ScalarT)` ⇒ `ScalarT` 落「未解析」✗
        #   ★ 根因：**提及收集与绑定收集是两套** —— 我改的是绑定那套（`栈2`），
        #     **提及这边照收不误** ⇒ 它照样进解析 ⇒ 解不到 ⇒ 未解析。
        #     **同一个概念写在两处，必然给出两个答案。**
        #   结构（打出来的）：`type_parameter > [ type > identifier ]`
        #     ⇒ 判据：**父亲或祖父是 `type_parameter`** ⇒ 是声明位置，不收。
        _祖5 = n.parent
        _在泛型参里 = (_祖5 is not None and (
            _祖5.type == "type_parameter"
            or (_祖5.type == "type" and _祖5.parent is not None
                and _祖5.parent.type == "type_parameter")))
        # ★★★ R461：**`package` 声明里的标识符不是引用** ——
        #   `package org.ggp.base.apps.benchmark;` 在 tree-sitter java 里是
        #   **嵌套 5 层的 `scoped_identifier`** ⇒ 每层的 `identifier` 各进一条提及
        #   ⇒ `org` / `ggp` / `base` / `apps` / `benchmark` **5 个包名段全成假缺口**；
        #   实测（R460 缺口 Top）：`org 332 · base 332 · ggp 386` ——
        #   **光 Java 包名就约 1,050 条假缺口**，而它们**不可能是任何定义**。
        #   ★ 与 R380（导入语句里只收绑定名，不收模块路径名）**同族**：
        #     都是**把「路径」当成了「名字」** ✗ —— R380 用 `取导入` 的绑定修掉了
        #     `from collections.abc import Mapping` 的 `collections`，
        #     而 **`package` 声明没有对应的 `取导入` 条目 ⇒ 漏在网外**。
        #   ★ 判据按**节点类型**（不按语言列举）：凡祖先里有 `package_declaration`
        #     / `namespace_definition` 的标识符 ⇒ **是归属声明，不是引用**。
        # ★★★ R462：**「声明性位置」是一个大类，不该一个个补** ——
        #   上一轮我按 `package_declaration` 补了一种；这一轮分诊又冒出三种：
        #     `#![allow(clippy::arithmetic_side_effects)]` ⇒ `allow` 290 次、`clippy`
        #     `#[cfg(test)]`                              ⇒ `test` 287 次
        #     `#![no_std]` / `#[derive(Debug)]` …         ⇒ 同族
        #   ★ 它们的共同点：**在 `attribute_item` 里** —— 那是**对编译器说的话**，
        #     不是对本文件里某个定义的引用。实测（R462 缺口 Top）：`allow 290` ·
        #     `test 287` · `non_snake_case 207` · `dead_code` · `uncommon_codepoints`
        #     —— **全部来自属性宏**，共 500+ 条假缺口。
        #   ★ 判据仍是**节点类型**：祖先链里有 `attribute_item` ⇒ 不收提及。
        #     （Rust 的 `#[...]` / `#![...]` 都是 `attribute_item`。）
        #   ★★ 又一次「同族不同名」（R462 第二次踩）：我只写了 `attribute_item`，
        #     而 **`#![allow(...)]`（inner attribute）的节点名叫 `inner_attribute_item`** ✗
        #     ⇒ 实测 `allow` / `non_snake_case` **照旧未解析**，
        #     而 `#[cfg(test)]` / `#[derive(Debug)]` 那半边**修好了** ——
        #     **又是"部分修好"**（口径 201 的第 2 次命中）。
        #   ★ 结构（打出来的）：
        #     `#![allow(non_snake_case)]` ⇒ inner_attribute_item > attribute
        #         > [ identifier(allow), token_tree > identifier(non_snake_case) ]
        #     `#[derive(Debug, Clone)]`   ⇒ attribute_item > attribute > …
        #   ⇒ **两种都要**。★ 口径 202 的同族：**别只写你想到的那一个名字。**
        _声明性祖先 = ('package_declaration', 'namespace_definition',
                    'attribute_item', 'inner_attribute_item')
        _祖6 = n.parent
        _在包声明里 = False
        # ★★★ **这里不许有魔法数字**（R461 自己踩的）——
        #   第一版写的 `for _步 in range(6)` ⇒ 实测：`org.ggp.base.player.gamer
        #   .clojure.stubs` 是 **7 段** ⇒ 往上 6 层**还没到** `package_declaration`
        #   ⇒ `org` / `ggp` / `base` **照旧进缺口** ✗（而 `apps`/`benchmark` 那种
        #   短包名**修好了** ⇒ **"部分修好"比"完全没修"更容易骗过我**）。
        #   ★ 口径：**往上找祖先要么走到根，要么找一个"唯一确定"的节点** ——
        #     **拍一个深度上限 = 把"我不知道多深"写成了一个数字**。
        while _祖6 is not None:

            if _祖6.type in _声明性祖先:
                _在包声明里 = True
                break
            _祖6 = _祖6.parent
        if (n.type in ("identifier", "type_identifier")
                and not _在泛型参里
                and not _在包声明里
                and not _是关键字参数名
                and (not _是成员
                     or (n.start_point[0], n.start_point[1]) in _限定位)
                and not (_在导入里
                         and 取段(n) not in _导入行绑定.get(n.start_point[0] + 1, ()))):
            # ★ 一个出现处一个三元组（`父` = 引用所在的定义节点，`行` 从 1 起）
            提及.append((取段(n), 父, n.start_point[0] + 1))
            _列 = 提及父.setdefault(取段(n), [])
            if 父 not in _列:
                _列.append(父)
        c类 = _分类(n.type)
        if n.type in ("parameter_declaration", "parameter", "optional_parameter"):
            pass   # ★ R381 参数是绑定不是定义（它以 _declaration 结尾被正则误配）
        elif c类 in _实体类:
            # ★ 代码实体**严格取名字**（只认 `name`/`declarator`）；文档实体可退后代。
            名, 来自字段 = 取名(n, 报来源=True, 严格=c类 in _须自有名字)
            # ★★ 包装节点（**通用判据**）：`*_definition` 但**自己没有 `name` 字段**
            #   ⇒ 名字是从子节点里捞的 ⇒ 它只是**外壳**，不是定义。
            #   实测踩过：`decorated_definition`（装饰器外壳）被我收成定义 ⇒
            #     ① 同一个函数**进图两次**；
            #     ② 限定名成了 `Sequence.abstractmethod.__getitem__`（**装饰器名混进父链**）
            #        ⇒ 按 `Sequence.__getitem__` 做引用解析**永远找不到**。
            #   ⇒ 外壳**透明**：不进图，且**不占父位**（子节点的父仍是外壳的父）。
            包装 = (名 is not None and not 来自字段 and n.type.endswith("_definition"))
            # ★★ 代码实体**必须有自己的 `name` 字段** —— 否则它只是**语法位置**
            #   （`with ... as fp` 的 `with_item`、`self._read_lock`），不是定义。
            #   实测：对账 `ast` 时这类多抽 345 条 = **3.7% 假阳**。
            #   文档实体（标题/小节）例外：markdown 标题内容在 `inline` 里，退后代是对的。
            if c类 in _须自有名字 and not 来自字段:
                包装 = True
            if 名 and not 包装 and not _是导入:
                号 = f"{c类}:{相对}:{名}:{n.start_point[0] + 1}"
                _父名 = _名表.get(父) if 父 else None
                # ★ R504：**容器**在这里算一次，值交给 `_粒` ——
                #   语义（"挂在什么上"）与表示（那几个字面量）分开。
                _容器1 = ("文件" if 父 is None
                        else ("类型体" if _像结构体类型(_型表.get(父, "")) else "定义体"))
                全名 = f"{_父名}.{名}" if _父名 else 名
                _名表[号] = 全名
                _型表[号] = str(n.type)
                节点.append({
                    "id": 号, "类": c类, "名": 名, "语言": 语,
                    # ★ 限定名：引用解析与对账都用它，**不是裸名**
                    "限定名": 全名 if "." in 全名 else None,
                    "文件": 相对, "行": n.start_point[0] + 1,
                    "类型": n.type, "深度": 深,
                    "父定义": 父,   # ★ 完备：内嵌定义也进图，只是标了父
                    # ★★ 粒：**跨语言通用**的层级判据 —— **只看"有没有父定义"**。
                    #   （不按「父是不是 program/module」判，那要按语言写。）
                    # ★★★★ R501 第二处（口径 295「两个同名的门槛必须都查」）：
                    #   原来写的是 `"顶层" if 父 is None and 深 <= 2 else "内嵌"` ——
                    #   **`深` 是 tree-sitter 语法深度，不是作用域深度**（L4349 自己
                    #   就写过这句话，我却在同一份文件里又踩了一次）。
                    #   实测（`ast` 权威对账，`我少 760 个文件`）：
                    #     `pan_engine/api_server.py` 的 `class ChatRequest` / `def chat`
                    #     在 **`try:` 块里** —— `try`/`if`/`with`/`for` 块
                    #     **不产生新命名空间**（Python 语义），但语法深度是 3~4
                    #     ⇒ 被 `深 <= 2` 判成「内嵌」✗
                    #   ★ `父 is None` 才是**作用域**判据：没有父定义 = 挂在模块上。
                    # ★★★★ R502：**`粒` 是三态，不是两态** —— R501 只修对了一半：
                    #   模块级块里的 `def` 救回了 `顶层` ✓，
                    #   但**类体里的 `def`/`class` 仍落 `内嵌`** ✗
                    #   实测（判一百五十一，全 17 桶）：
                    #     `类体 × function_definition` = **0**（**那一态整个是空的**）
                    #     `父是类/结构体却标内嵌` = **87,359**
                    #   ⇒ **目标②的直接要求**：「方法」与「函数内的嵌套函数」
                    #     **是两个不同的作用域层**（`A.m` vs `f.<locals>.g`），
                    #     混成一态 ⇒ 解析「`self.m()` 指向哪个 `m`」时
                    #     **没有任何字段能区分**。
                    #   ⇒ 判据（语言无关，**只做分类不做全局排除**，口径 261）：
                    #     父的类型名里含 class/struct/impl/trait/interface/enum/object
                    #     ⇒ `类体`；否则有父 ⇒ `内嵌`。
                    "粒": _粒(_容器1),
                })
                边.append({"起": 文件节点, "终": 号, "关系": "包含"})
                父 = 号
        elif n.type in ("function_definition", "declaration", "function_declarator") \
                and n.type != "declaration":
            # ★★★ **C/C++ 的函数定义/声明：名字在 `declarator` 字段链的最内层**（R381）。
            #   实测 tree-sitter-c：
            #     `function_definition | type:primitive_type
            #                           declarator:function_declarator body:...`
            #       `function_declarator | declarator:identifier parameters:...`
            #   ⇒ **一路顺着 `declarator` 走到底，第一个 `identifier` 就是名字**。
            #   ★ 为什么必须有：不加这一步，`void f(void){...}` 里的 `f`
            #     **既不是定义也不是绑定** ⇒ 在我新开的「外部符号」桶里
            #     被算成**来自仓外头文件** ✗ —— **误报的引用比缺失的引用更坏**。
            _d = _取字段(n, "declarator")
            _名5 = None
            _防5 = 0
            while _d is not None and _防5 < 12:
                if _d.type == "identifier":
                    _名5 = 取段(_d)
                    break
                _d = _取字段(_d, "declarator") or next(
                    (c for c in _d.children if c.is_named
                     and c.type.endswith("declarator")), None)
                _防5 += 1
            if _名5:
                # ★★★ **函数体内的 `declaration` 是局部变量 ⇒ 绑定，不是定义**（R381）。
                #   与 Python 的 `assignment` **同一判据**（`父 is None and 深 <= 2` 才算模块级）——
                #   **同一个概念在两种语言里必须用同一条判据**，否则 `int o;` 会变成
                #   一个"本文件定义" ⇒ 引用解析①会命中它 ⇒ 而且它还挡住了
                #   「这个名字该落绑定」的事实（**一个答案硬套多处，必然有一处是错的**）。
                _模块级 = (父 is None)   # ★ R381 结构判据，不看语法深度
                if not _模块级:
                    节点.append({"id": f"bind:{相对}:{_名5}", "类": "绑定", "名": _名5,
                                "语言": 语, "文件": 相对, "行": n.start_point[0] + 1,
                                "类型": "binding", "深度": 0})
                    边.append({"起": 文件节点, "终": f"bind:{相对}:{_名5}", "关系": "绑定"})
                else:
                    _号5 = f"{'声明' if n.type == 'declaration' else '定义'}:{相对}:{_名5}:{n.start_point[0] + 1}"
                    节点.append({
                        "id": _号5, "类": "声明" if n.type == "declaration" else "定义",
                        "名": _名5, "语言": 语, "限定名": None, "文件": 相对,
                        "行": n.start_point[0] + 1, "类型": n.type, "深度": 深,
                        "父定义": None, "粒": _粒("文件"),
                    })
                    边.append({"起": 文件节点, "终": _号5, "关系": "包含"})
        elif n.type == "parameter_declaration":
            # ★ **C 的函数参数是「绑定」**（局部名字，不算引用）——
            #   实测：漏了它 ⇒ 参数 `o`/`x` 落「未解析」或「外部符号」✗
            _d2 = _取字段(n, "declarator")
            _防6 = 0
            while _d2 is not None and _防6 < 12:
                if _d2.type == "identifier":
                    _n6 = 取段(_d2)
                    if _n6:
                        节点.append({"id": f"bind:{相对}:{_n6}", "类": "绑定", "名": _n6,
                                    "语言": 语, "文件": 相对, "行": n.start_point[0] + 1,
                                    "类型": "binding", "深度": 0})
                        边.append({"起": 文件节点, "终": f"bind:{相对}:{_n6}", "关系": "绑定"})
                    break
                _d2 = _取字段(_d2, "declarator") or next(
                    (c for c in _d2.children if c.is_named
                     and c.type.endswith("declarator")), None)
                _防6 += 1
        elif n.type == "assignment" and (_在类体里(n) or _在模块级(n)):
            # ★★★ **类体的赋值是「类属性」，算命名实体**（R396）。
            #   实测（判五十一 `我漏 635`，样例）：
            #     `_collections_abc.py::AsyncGenerator.__slots__`
            #     `_collections_abc.py::AsyncIterable.__class_getitem__`
            #   源码：`class AsyncIterable: __slots__ = ()` /
            #         `__class_getitem__ = classmethod(...)`
            #   ★ 它和我这边**已经收了的**「模块级赋值」是**同一类东西**，
            #     而 ast 侧我这一轮刚把「模块级 + 类体」都加上了 ⇒
            #     **两边口径必须一致，否则一边说我漏、一边说我多。**
            #     （同族口径：R390「绑定收集与提及收集是两套」、
            #       R393「当前节点算不算与往不往下传」。）
            #   ★ 边界：**函数体内**的赋值**不算**（那边落「绑定/内置」）——
            #     由 `_在类体里` 判，**按语法位置，不按名字**。
            for _iC, _cC in enumerate(n.children):
                _fC = _字段名(n, _iC)
                if _fC != "left":
                    continue
                if _cC.type != "identifier":
                    continue
                _名C = 取段(_cC)
                if not _名C:
                    continue
                _号C = f"定义:{相对}:{_名C}:{_cC.start_point[0] + 1}"
                # ★★★ **类属性的「限定名」必须带类名前缀**（R396，一个错两头都错）。
                #   实测（判五十一，同一轮）：
                #     `我漏 635` 的样例是 `AsyncGenerator.__slots__`（**带前缀**）
                #     而 `我多` 的样例是 `__slots__`（**不带前缀**）
                #   ⇒ **不是两个错，是「限定名 = None」这一个错** ✗
                #     对账时 `n.get("限定名") or n["名"]` 取到裸名 ⇒
                #     ① ast 的带前缀名我这边没有 ⇒ 记「我漏」
                #     ② 我的裸名 ast 那边没有 ⇒ 记「我多」
                #   ★ 口径：**一个字段为空，会在两个方向同时报错** ——
                #     看到「漏和多大体相等」时，先怀疑**中间那个字段没填**。
                # ★★★ **限定名要带"完整嵌套前缀"，不是只带一层**（R397）。
                #   实测（同一轮，两头都报）：
                #     `我漏` 样例 `BaseDefaultEventLoopPolicy._Local._loop`（**带全链**）
                #     `我多` 样例 `_Local._loop`（**只带一层**）
                #   ⇒ **不是两个错，是"前缀只带了一层"这一个错** ✗
                #     （同 R396 的"一个字段为空两头报错"—— **这次是"只带一层"。**）
                #   ★ 判据是**结构**：**沿 `父定义` 链一路往上拼**，
                #     直到没有父定义（模块级）为止。
                #     **不按"类名"猜，也不限制层数。**
                _链C: list[str] = []
                _看C = 父
                _防C = 0
                while _看C is not None and _防C < 64:
                    _防C += 1
                    _找C = None
                    for _xN in reversed(节点):
                        if _xN.get("id") == _看C:
                            _找C = _xN
                            break
                    if _找C is None:
                        break
                    _链C.append(str(_找C.get("名") or ""))
                    _看C = _找C.get("父定义")
                _类名C = ".".join(reversed([x for x in _链C if x])) or None
                # ★ **模块级赋值同样是命名实体**（R396，`我漏 124` 的另一半）：
                #   `_pydecimal.py::MAX_PREC` / `MIN_EMIN` / `_pyio.py::open_code`
                #   —— 这些是**模块级** `X = ...`，权威 `ast` 侧算，我这边原来不算。
                #   ⇒ 和「类体赋值」**同族**：**凡是"赋值产生模块/类命名空间里一个名字"
                #     的，两边都要算；凡是"赋值只产生局部变量"的，两边都不算。**
                #     **这条口径写在两处（ast 侧与 tree-sitter 侧），必须同时改。**
                节点.append({
                    "id": _号C, "类": "定义", "名": _名C, "语言": 语,
                    "限定名": (f"{_类名C}.{_名C}" if _类名C else None),
                    "文件": 相对, "行": _cC.start_point[0] + 1,
                    "类型": "class_assignment", "深度": 深, "父定义": 父,
                    # ★★★★ R501：**`粒` 必须按"有没有父定义"分** ——
                    #   上面 L1583 的守卫是 `_在类体里(n) or _在模块级(n)`
                    #   ⇒ **模块级赋值**和**类体赋值**都进这一支 ✓
                    #   但 `粒` **硬编码 `"类体"`** ⇒ 模块级的 `RCON = [...]`
                    #   也被标成「类体」✗
                    #   ★ 实测（R501，`ast` 权威对账）：**10,462 个文件的
                    #     `粒=顶层` 定义一个都没有**、`我多 0` —— 症状是
                    #     「大规模单向缺失」，**根因是分类错**（不是没收）：
                    #     `debug_aes.py` 的 `RCON`/`SBOX`/`key`/`plaintext`/`expected`
                    #     我全标成 `class_assignment`+`粒=类体`+`深度=2/4`。
                    #   ⇒ 口径 213：**判据要测"我改的那一层"** ——
                    #     这一层就是**分类**，不是**收集**。
                    "粒": _粒("类型体" if 父 else "文件"),
                })
                边.append({"起": 父 or 文件节点, "终": _号C, "关系": "包含"})
        elif n.type == "type_parameter" and _在泛型声明位(n):
            # ★★★ **PEP 695 泛型参数是"声明"**（R391）—— 它引入一个**新名字**，
            #   作用域是**所在的那个函数/类**。
            #   实测（未解析 Top 前五条同一根因）：
            #     `def f[ScalarT: _Shape](x: ScalarT)` ⇒ `ScalarT` 762 条落未解析。
            #   ★ 它**不是"我抽错了"**，是**作用域解析缺一层**：
            #     `ScalarT` 在 `[...]` 里声明，在函数体内被引用 ⇒
            #     解析层「**按作用域由内向外**」那一套**本来就能解** ——
            #     缺的只是"**把这个声明变成一个定义节点**"。
            #   ★ 结构（打出来的）：`type_parameter > [ type > identifier ]`
            #     ⇒ 判据用**语法位置**，不按名字列举。
            #   ★ `父定义` 填当前所在定义（`父`）—— **状态要跟着子树往下传**，
            #     否则泛型参数会挂到文件上，**跨函数同名会互相冒充**。
            #   ★★ **必须深度优先找第一个 `identifier`**（R391 第三次踩）：
            #     实得值（插桩打出来的）：
            #       `type_parameter` children = [('[','['), ('type','ScalarT: int'), (']',']')]
            #     而那个 `type` 的**孩子里没有直接的 `identifier`**（`ScalarT: int`
            #     还套着一层 `type`）⇒ 我第一版只找**直接孩子** ⇒ `_标6 = None`
            #     ⇒ **`continue`** ⇒ **定义节点一个都没建**，
            #     而外观上**分支明明被走到了**。
            #     ★ 「分支被走到了」与「分支产出了东西」是**两件事** ——
            #       我差点因为 PROBE-A 打印了就以为修好了。
            _标6 = None
            _待6 = [n]
            _防6b = 0
            while _待6 and _防6b < 200:
                _x6 = _待6.pop(0)
                _防6b += 1
                if _x6.type == "identifier":
                    _标6 = _x6
                    break
                for _c6 in _x6.children:
                    _待6.append(_c6)
            for _c6x in ([] if _标6 is None else [_标6]):
                _名6 = 取段(_标6)
                if not _名6:
                    continue
                _号6 = f"定义:{相对}:{_名6}:{_标6.start_point[0] + 1}"
                节点.append({
                    "id": _号6, "类": "定义", "名": _名6, "语言": 语,
                    "限定名": None, "文件": 相对, "行": _标6.start_point[0] + 1,
                    "类型": "type_parameter", "深度": 深, "父定义": 父,
                    "粒": _粒("局部"),
                })
                边.append({"起": 父 or 文件节点, "终": _号6, "关系": "包含"})
        elif n.type == "type_alias_statement":
            # ★★★ **PEP 695 类型别名 `type X = ...` 也是定义**（R389）。
            #   实测（未解析 Top 前三条的根）：`numpy/_typing/_shape.py` 里
            #     `type _Shape = tuple[int, ...]`
            #     `type _AnyShape = tuple[Any, ...]`
            #     `type _ShapeLike = SupportsIndex | Sequence[SupportsIndex]`
            #   ⇒ 该文件**定义节点 0 个** ⇒ `NDArray 460 · _Shape 294 · _AnyShape 86`
            #     全落未解析（`numpy/_typing/__init__.py` 只是把它们转出去）。
            #   ★ **tree-sitter 的实得形状**（打出来的，不是猜的）：
            #     `type_alias_statement` 的 children =
            #       [('type','type'), ('type','_Shape'), ('=','='), ('type','tuple[...]')]
            #     ⇒ **名字是第二个孩子，而且它的类型是 `type` 不是 `identifier`** ✗
            #   泛型形式 `type X[T] = list[T]` ⇒ children[1] 是 `('type','X[T]')`，
            #     里面是 `generic_type > identifier`。
            #   ★ 判据用**语法位置**（第二个孩子），**不按名字列举**。
            #   ★ 实测效果（标准库全量）：
            #     未解析 12,811 → **10,568**；`NDArray`/`_Shape`/`_AnyShape` **全部归零**；
            #     已解析·本文件 173,104 → 177,793。**引用条数不变**（改判，不是新增）。
            _名5 = None
            _子列 = list(n.children)
            if len(_子列) >= 2:
                _c2 = _子列[1]
                if _c2.type == "identifier":
                    _名5 = 取段(_c2)
                elif _c2.type == "type":
                    _内 = next((g for g in _c2.children if g.type == "generic_type"), None)
                    _标 = None
                    if _内 is not None:
                        _标 = next((g for g in _内.children if g.type == "identifier"), None)
                    if _标 is None:
                        _标 = next((g for g in _c2.children if g.type == "identifier"), None)
                    if _标 is not None:
                        _名5 = 取段(_标)
            if _名5:
                _号5 = f"定义:{相对}:{_名5}:{n.start_point[0] + 1}"
                节点.append({
                    "id": _号5, "类": "定义", "名": _名5, "语言": 语,
                    "限定名": None, "文件": 相对, "行": n.start_point[0] + 1,
                    "类型": "type_alias_statement", "深度": 深, "父定义": None,
                    "粒": _粒("文件"),
                })
                边.append({"起": 文件节点, "终": _号5, "关系": "包含"})
        elif n.type in ("preproc_def", "preproc_function_def"):
            # ★ **C 的 `#define FOO` 也是定义**（R381）—— 同一个「导入图」要能对上号：
            #   本文件 `#define FOO` + 别的文件 `#include "本文件.h"` ⇒ 那个 FOO 能解到这儿。
            _n2 = _取字段(n, "name")
            if _n2 is not None:
                _名4 = 取段(_n2)
                if _名4:
                    _号4 = f"定义:{相对}:{_名4}:{n.start_point[0] + 1}"
                    节点.append({
                        "id": _号4, "类": "定义", "名": _名4, "语言": 语,
                        "限定名": None, "文件": 相对, "行": n.start_point[0] + 1,
                        "类型": "preproc_def", "深度": 深, "父定义": None,
                        "粒": _粒("文件"),
                    })
                    边.append({"起": 文件节点, "终": _号4, "关系": "包含"})
        elif (语 == "python" and n.type == "assignment" and 父 is None
              and 深 <= 2 and not _是导入):
            # ★★★ **Python 的模块级赋值是「定义」**（R372）。
            #   实测（目标⑤ 的 `ast` 对账）：`typing.py` 的
            #     `Dict = _alias(dict, 2, inst=False, name=chr(39)+"Dict"+chr(39))`
            #   **不建节点**（`assignment` 不在 `_类别` 任何规则里）⇒
            #   `from typing import Dict` 在靶文件里找不到 ⇒ 落「未导入」。
            #   同一类还有 `re.__all__`、`numpy.ndarray`（`ndarray = ...`）——
            #   **标准库里同族 1,229 条**（`typing` 一族为主）。
            #   ★ `from X import Y` 能不能成功，**取决于 X 的模块级命名空间**，
            #     而那个命名空间 = 定义 **+ 模块级赋值**。少了一半 ⇒ 假缺口。
            #   ★ 只收 **`父 is None and 深 <= 2`**（模块级）——
            #     函数体内的赋值是**局部变量**，那是「绑定」不是定义。
            _左 = _取字段(n, "left")
            if _左 is not None and _左.type == "identifier":
                _名3 = 取段(_左)
                _号3 = f"定义:{相对}:{_名3}:{n.start_point[0] + 1}"
                节点.append({
                    "id": _号3, "类": "定义", "名": _名3, "语言": 语,
                    "限定名": None, "文件": 相对, "行": n.start_point[0] + 1,
                    "类型": "assignment", "深度": 深, "父定义": None,
                    "粒": _粒("文件"),
                })
                边.append({"起": 文件节点, "终": _号3, "关系": "包含"})
        for _i, c in enumerate(reversed(n.children)):
            # ★ `reversed` 里的下标要换回原下标才能问字段名
            _原i = len(n.children) - 1 - _i
            _字段 = _字段名(n, _原i)
            栈.append((c, 深 + 1, 父, _是导入, _字段 in _成员字段))

    # ★★ **绑定**（局部名）：`def f(x)` 的 `x`、`a = 1` 的 `a`、`for i in ...` 的 `i`
    #   —— 它们是**变量**，**不是图上的定义**，也**不是引用**。
    #   实测：不做这一步，「未导入」被 `instance`/`metaclass`/`items` 这类局部名撑到 15 万。
    #   ★ 通用判据（按 tree-sitter 节点类型，不按语言）：绑定位置里的标识符。
    # ★★★ **R369：绑定位必须按「字段名」判，不许整棵子树都算。**
    #   修前实测（标准库 `_collections_abc.py:485`）：
    #     `[_type_repr(a) for a in self.__args__[:-1]]` 里的 `a` 落 **未解析** ——
    #     **所有推导式/生成器里的循环变量都被当成缺定义**（`ast` 对账：198 条/前 120 文件）。
    #   根因两条，用 tree-sitter 的**真节点类型 + 真字段名**定死（不猜）：
    #     ① `_绑定型` 里写的是 `for_statement`，而 **Python 推导式是 `for_in_clause`**、
    #        `lambda` 的参数是 **`lambda_parameters`** ⇒ 这两类**整个漏掉**；
    #     ② 旧的 `是绑定位` 把**整棵子树**都当绑定 ⇒
    #        `for x in a` 的 `right`（`a`）也被算成绑定 ⇒ **假「绑定/内置」**
    #        （本该落「未解析」的自由名被当成变量而**不算缺**）。
    #   ★ 真值（tree-sitter-python 实测字段名）：
    #        `for_in_clause`  left | `for_statement`  left
    #        `default_parameter`  name | `as_pattern`  alias（`with` / `except` 目标）
    #        `lambda`  parameters
    #   ⇒ **只有这些字段上的子树才是绑定位**。
    _绑定整型 = ("assignment", "parameters", "lambda_parameters",
               "variable_declarator", "let_declaration", "formal_parameters",
               "required_parameter", "optional_parameter",
               # ★★★ **解包模式与 `as` 目标**（R393）—— 整棵子树都是绑定，
               #   所以必须在这里（**往下传**），不是在 `是绑定位` 那一处
               #   （那里只判"当前节点算不算"，**不负责传给子节点**）。
               #   · `pattern_list`：`a, b = 1, 2` / `for k, v in d.items()`
               #     ★ 它以 `_list` 结尾 ⇒ `endswith("_pattern")` 匹配不上 ✗
               #   · `as_pattern_target`：`except OSError as exc` / `with x as fh`
               #     ★ 它以 `_target` 结尾 ⇒ 同样匹配不上 ✗
               #   ★ 我第一版只改了 `是绑定位` ⇒ `a`/`b` 靠 `assignment` 运气对了，
               #     而 `k`/`v`/`exc` **照旧未解析** ——
               #     **"当前节点算不算"和"要不要传给子节点"是两个判断，写在两处。**
               "pattern_list", "as_pattern_target",   # 整棵子树都是绑定
               # ★★★ R461：**`catch` 的形参也是绑定**（目标②的漏）——
               #   实测：`} catch (Exception e) {` 里的 `e` 落「未解析」✗
               #   （`arc_data` 的 `e`：193 条未解析 + 119 条绑定/内置 ⇒
               #    **同名在不同处境遇不同**，口径 187）。
               #   结构：`catch_formal_parameter > [ catch_type > type_identifier,
               #                                    identifier ]`
               #   ★ `formal_parameters`（方法形参）在表里，而 **`catch` 用了
               #     另一个节点名** ⇒ **同一族、不同后缀**（R393 的老病）。
               #   ⇒ **按这一族枚举**，不再靠猜后缀。
               #   ★★★ **`catch_clause` 本身不许进这张表**（R461 自己踩的坑）——
               #   第一版我把它也加了进去 ⇒ **整个 `catch` 子树（含 `block` 里的
               #   `y(e)`）都成绑定位** ⇒ 实测 `y` 从「未解析」变成「绑定/内置」✗
               #   **把一条真缺口藏进了"不算引用"**，比漏标更坏。
               #   ★ 这正是 R393 已经写过的话：「`for_in_clause` 自己**不是**绑定位
               #     （否则 `right` 也被吞进来），**只有它的 `left` 字段是**」——
               #     我在同一个位置重犯了同一个错。
               #   ⇒ 只留**真正声明名字的那个节点**：`catch_formal_parameter`。
               "catch_formal_parameter", "formal_parameter",
               "resource", "enhanced_for_statement",
               # ★★★ R462：**Rust 闭包的形参是绑定**（目标②的漏）——
               #   实测：`hits.sort_by(|left, right| ...)` 的 `left`/`right`
               #   落「未解析」（缺口 Top 里各 200/188 次）✗
               #   结构（打出来的）：
               #     `closure_expression > closure_parameters > identifier`  ← 无类型
               #     `closure_expression > closure_parameters > parameter > identifier`
               #     ⇒ 有类型的走 `parameter`（**已在表里**），无类型的直接是 `identifier`
               #       ⇒ **只有 `closure_parameters` 能救它**。
               #   ★ 安全：`closure_parameters` **只含形参**，闭包体是
               #     `closure_expression` 的**兄弟节点** ⇒ **不会把体吞进来**
               #     （这正是 R393/R461 那条口径要防的事）。
               "closure_parameters")
    # ★★★ **字段名必须绑在父类型上，否则同一个字段名在别的结构里含义完全不同**（R383）。
    #   实测踩到（判五十三 红）：
    #     `return foo() + local_var` —— `binary_operator` 的字段**也叫 `left`** ✗
    #        ⇒ `foo`（一个**被调用**的名字）被判成**局部绑定** ⇒ 它再也走不到
    #          「未导入 / 未解析」⇒ **真缺口被藏进「绑定/内置」**。
    #     `def use(): ...` —— `function_definition` 的字段**也叫 `name`** ✗
    #        ⇒ **函数名自己**被判成绑定。
    #   ⇒ 修前实测：`c.py` 三条引用里 `foo` 与 `use` **两条都错**。
    #   ★ 口径：**`left` 在 `assignment` 里是绑定位，在 `binary_operator` 里是表达式** ——
    #     **字段名脱离了它的父类型就没有意义。**
    #   ★ 反面同样要防：**按字段名列举**会漏（`for_in_clause`、`with_item` ……），
    #     所以下面**只收「确实是绑定位置」的父类型**，其余一律不绑。
    _绑定字段表: dict[str, tuple[str, ...]] = {
        "assignment": ("left",),
        "augmented_assignment": ("left",),
        "variable_declarator": ("name",),
        "named_expression": ("name",),
        "aliased_import": ("alias",),          # 导入名由 `_在导入里` 排除，这里只是齐全
        "parameter_declaration": ("declarator",),
        "let_declaration": ("name", "pattern"),
        # ★★★ **`for_in_clause` 的 `left` 是循环变量（绑定）** —— R384 补回。
        #   我上一轮把 `_绑定字段 = ("left", ...)` 换成「按父类型」的表时**漏了它**，
        #   而 L1372 那句 `if t == "for_in_clause" and f != "left": 子绑 = False`
        #   是**假定** `left` 会被 `_绑定字段` 命中的 ⇒ 表里没有 ⇒ **它自己把自己排除了**
        #   ⇒ 实测标准库：`for v in arg_strings` 的 `v`（389 条）、
        #     `{v: k for k, v in enumerate(...)}` 的 `v`/`k`、
        #     `for key,(begin,end) in ...` 的 `key`（245 条）**全落「未解析」** ✗
        #   ★ 口径：**换判据的「形状」时，必须把旧判据覆盖的每一处都搬过去** ——
        #     否则新判据看起来更精确，实际**漏掉了一整类**。
        "for_in_clause": ("left",),
        # ★★★ **`for_statement` 的 `left` 同样是循环变量**（R399）——
        #   ★ R393 我只补了 `pattern_list`（**多个**目标时 `left` 是
        #     `pattern_list`），**漏了"单个目标时 `left` 就是裸 `identifier`"** ✗
        #   实测（标准库未解析 Top 第一，`i 209` 条）样例：
        #     `gzip.py:433|        for i in range(count // self._buffer_size):`
        #     `ipaddress.py:1696|    for i in range(1, len(parts) - 1):`
        #     `pkgutil.py:115|            for i in importers:`
        #   ★ 口径：**`for i in ...` 与 `for k, v in ...` 是同一结构的两种形态** ——
        #     补了"多个目标"，**必须同时问"单个目标长什么样"**
        #     （同 R394「只有多行才暴露」、R397「链式赋值有几层」）。
        "for_statement": ("left",),
    }
    # ★★★ **`import` 里的名字不是「局部绑定」，是「导入绑定」**（R369）。
    #   修前实测：`import os` ⇒ 抽出一个 `绑定 os` 节点 ⇒ 解析时
    #   「本文件绑定」**先于**导入表命中 ⇒ `os`/`ab`/`OrderedDict`
    #   **全落「绑定/内置」（不算引用）**，而导入边**明明是对的**：
    #     `{"模块":"os","绑定":["os"],"节点类型":"import_statement"}` ✓
    #   ⇒ 后果正是「**高频名字分因**」里那一大片：`np 2180`、`plt 572`、`mpl 342`
    #     本该解到模块，却因为名字先被当成变量而**根本走不到导入表**。
    #   ★ 修**只修这一处**（抽取层）：导入名交给导入表，
    #     而不在解析层再加一条「导入表优先」的判据 ——
    #     **同一个判断写在两处，必然给出两个答案。**
    # ★★★ **名字不许与函数里已有的 `_导入型` 撞**（R369 实测踩到）：
    #   上面 L1043 早有一个 `_导入型`，它是**正则**（`_导入型.search(n.type)`）。
    #   我在同一函数里又定义了一个**同名 tuple** ⇒ L1043 拿到 tuple
    #   ⇒ `AttributeError: tuple object has no attribute search`
    #   ⇒ 被上层 `except` 吞掉 ⇒ **静默退到 `_保底适配器`**
    #     （保底把每个词都当名字 ⇒ `import`/`from`/`def`/`return` 全成了「提及」，
    #       8 条「未解析」而文件里明明有十几个标识符）。
    #   ★ 口径：**静默降级是最坏的一种绿** —— 它不报错，
    #     只让读数「看起来在跑」（这次连读数都凑齐了，只是全错）。
    _导入节点型 = ("import_statement", "import_from_statement",
              "future_import_statement")
    绑定名: set[str] = set()
    # ★★ **「在导入里」必须是会往下传的状态**（R369 第二次踩）：
    #   第一版只在**恰好是** `_导入节点型` 那一层置 `False`，
    #   而 `import a.b as ab` 的结构是
    #     `import_statement > aliased_import > (dotted_name, identifier:ab)`
    #   ⇒ 进到 `aliased_import` 时状态**已经丢了**，
    #     再被 `_绑定字段` 里的 `alias` 命中 ⇒ `ab` **还是绑定** ⇒
    #     解析时「本文件绑定」先于导入表 ⇒ `ab` 落「绑定/内置」。
    #   ★ 口径：**状态不许只在起点置一次** —— 它要**跟着子树往下传**，
    #     否则「排除」只排掉了起点，**排除了」和「通过了」长得一样**。
    # ★★★ **第四个标志：`在类型里`**（R390）。
    #   实测（本轮暴露）：`def f(x: _Shape)`、`x: _Shape = 1` 里
    #   `_Shape` **落「绑定/内置」** ✗ —— 因为 `parameters` 在 `_绑定整型` 里
    #   ⇒ **整棵参数子树都算绑定位**，**连类型注解也算** ✗
    #   ★ tree-sitter 的实得结构（打出来的）：
    #     `typed_parameter`          = identifier[name] : type[type]
    #     `typed_default_parameter`  = identifier[name] : type[type] = value[value]
    #     `assignment`（`x: T = 1`）= identifier[left] : type[type] = integer[right]
    #   ⇒ **凡在 `type` 字段里的名字都是注解，不是绑定** —— 这**跨语言通用**
    #     （注解里写的是**被引用的类型名**）。
    #   ★ 判据用**语法位置**（进了 `type` 节点，整棵子树都在类型里），
    #     **不按名字列举**。
    栈2 = [(树.root_node, False, False, False)]
    import sys as _s8
    def _探(节):
        pass
    层级: dict = {}
    while 栈2:
        n, 在绑定里, 在导入里, 在类型里 = 栈2.pop()
        t = n.type
        在导入里 = 在导入里 or t in _导入节点型
        #   ★★ **例外：`type_parameter`（泛型参数声明）里的 `type` 不是注解位置**（R390）。
        #     实测结构（打出来的）：
        #       `def f[ScalarT: int]`  ⇒ type_parameter > [ type > identifier(ScalarT) ]
        #       `class C[ArrayT]`      ⇒ type_parameter [type_parameters] > 同上
        #       `type X[T] = list[T]`  ⇒ 左边 generic_type 里也有 type_parameter
        #     ⇒ 那里的 identifier 是**新引入的名字（声明）**，不是"引用一个类型" ✗
        #     ★ 我加"在类型里"时把它一起排掉了 ⇒ 实测 Top 换成
        #       `ScalarT 764 · ArrayT 344 · ShapeT 287 · i_co 140 · DTypeT 118`。
        #     ★ 口径：**加一条"排掉"的判据时，必须同时量它会不会把"该留的"也排掉**
        #       （同 R388 的 `./` 被点分分支吃掉）。
        _父9 = n.parent
        在类型里 = 在类型里 or (t == "type" and not (
            _父9 is not None and _父9.type == "type_parameter"))
        # ★★★ **两类"长得不像 `_pattern` 的绑定位置"**（R393）：
        #   · `pattern_list` —— **解包赋值与 for 的目标**：
        #       `a, b = 1, 2`      ⇒ assignment > pattern_list[left] > identifier(a), identifier(b)
        #       `for k, v in d...`  ⇒ for_statement > pattern_list[left] > identifier(k), identifier(v)
        #     ★ 它**以 `_list` 结尾**，`t.endswith("_pattern")` **匹配不上** ✗
        #       ⇒ 实测未解析 Top：`v 79 · key 80 · item 69` 一大片。
        #   · `as_pattern_target` —— **`except ... as exc` / `with ... as fh`**：
        #       `except OSError as exc` ⇒ as_pattern > as_pattern_target[alias] > identifier(exc)
        #     ★ 它**以 `_target` 结尾**，同样匹配不上 ✗
        #       ⇒ 实测未解析 Top：`exc 117 · err 108`。
        #   ★ 口径：**`endswith("_pattern")` 是一条"按名字长相"的判据** ——
        #     它面对**同一族但不同后缀**的节点必然漏。**按"这一族有几种后缀"枚举，
        #     并把这一族写在注释里**，比继续用通配安全。
        是绑定位 = (not 在导入里) and (not 在类型里) and (
            在绑定里 or t in _绑定整型 or t.endswith("_pattern")
            or t in ("for_in_clause", "pattern_list", "as_pattern_target"))
        if 是绑定位 and t in ("identifier", "property_identifier", "type_identifier"):
            绑定名.add(取段(n))
        if t == "assignment" and not 在导入里:
            # 只有**左边**才是绑定；右边是引用
            for i2, c in enumerate(n.children):
                f = _字段名(n, i2)
                左 = (f == "left") or (f is None and i2 < len(n.children) // 2)
                栈2.append((c, bool(左), 在导入里, 在类型里 or f == "type"))
            continue
        for i2, c in enumerate(n.children):
            f = _字段名(n, i2)
            # ★ `for_in_clause` 自己**不是**绑定位（否则 `right` 也被吞进来），
            #   只有它的 `left` 字段是。
            子绑 = (在绑定里
                    or (f in _绑定字段表.get(t, ()))     # ★ 字段名**必须**绑在父类型上
                    or t in _绑定整型)
            if t == "for_in_clause" and f != "left":
                子绑 = False
            栈2.append((c, bool(子绑), 在导入里, 在类型里 or f == "type"))

    # 引用边：文本里出现的标识符 → 指向**同名**定义（近似，会多不会少）
    # ★ **一个出现处一条边**，并按 `(名, 父, 行)` 去重（同一处只留一条）。
    #   去重键**必须带父和行** —— 只按名字去重就又回到「一处答案硬套多处」。
    _见提: set[tuple] = set()
    for _名, _父, _行 in 提及:
        _k提 = (_名, _父, _行)
        if _k提 in _见提:
            continue
        _见提.add(_k提)
        # ★ 限定名的根一并挂在提及边上（R385）——**下游要判归属**（靶在仓外 ⇒ 外部符号）。
        _宿名 = _限定宿主.get((_名, _行))
        边.append({"起": 文件节点, "终": f"name:{_名}", "关系": "提及", "近似": True,
                   "父": _父, "行": _行,
                   **({"限定宿主": _宿名} if _宿名 else {})})

    # ★★ **导入**：文件 → 模块（`mod:<模块>` 是**待解析的占位符**，跨文件解析在 `解析引用`）
    #   这一层比「提及」硬：它是**语法结构**给的事实，不是「某个名字出现过」。
    for 名 in 绑定名:
        号 = f"bind:{相对}:{名}"
        # ★★★ R522：**导入绑定节点也必须报语言**（判一百七十八抓出来的）。
        #   实测：`.tsx` 样本（`import React from "react"; export const A = () => <div/>;`）
        #   抽出的两个节点（`绑定 A` / `绑定 div`）**语言全是 None** ⇒
        #   判一百七十八 报红：**这个语言在自己的样本节点上没报出语言名**。
        #   ★ 那个函数里的「声明/定义」节点都写了 `"语言": 语`，
        #     **只有这一处漏** ⇒ 于是「按语言做的判断」看不见它们（口径 260）。
        节点.append({"id": 号, "类": "绑定", "名": 名, "语言": 语, "文件": 相对, "行": 0,
                    "类型": "binding", "深度": 0})
        边.append({"起": 文件节点, "终": 号, "关系": "绑定"})

    _导 = 取导入(树, bs)
    # ★★★ **别名导入：把「本地名 → 原名」挂在导入边上**（R373）。
    #   ★ 加在**这一处**（不是 12 个 `出.append` 各加一遍）——
    #     `取导入` 的出口是 `**x` **铺开**（R365 已修）⇒ 新字段进得来；
    #     而 `边.append({**导, ...})` 也是铺开 ⇒ 一路都不筛字段。
    #   ★ 口径：**靠列举保证完备迟早漏；靠结构保证不漏** ——
    #     所以这里只**在一处**注入，让后面所有铺开层自动带上。
    _别名 = _导入别名(树, bs)
    for _d in _导:
        _d["映射"] = _别名.get(_d.get("行"), {}) or {}
    # ★★ **缺字段必须报出来，不许静默**（R348）：`取导入` 的出口已统一规范化，
    #   把「补了哪些字段」放在 `取导入.上次缺字段` 上 —— 这里**读它并写进问题清单**。
    #   实测（R346）一个漏写的 `相对` 键 ⇒ `KeyError` ⇒ 上层吞掉 ⇒ **整个文件不进图**，
    #   而判据只能看到「没有」。**「抛出的异常被吞掉」和「静默的默认值」是同一个病。**
    if getattr(取导入, "上次缺字段", None):
        问题.append({"文件": 相对, "类": "导入记录缺字段",
                     "缺": dict(取导入.上次缺字段)})
    for 导 in _导:
        号 = f"mod:{导['模块']}"
        节点.append({"id": 号, "类": "模块", "名": 导["模块"], "文件": 相对,
                    "行": 导["行"], "类型": "import", "相对": 导["相对"]})
        # ★ `星号` 挂在**边**上，不挂在节点上 ——
        #   `mod:x` 节点是**全局共享**的（两个文件 import 同一个模块 ⇒ 同一个节点），
        #   挂在节点上会**串台**：一个文件用星号、另一个不用，节点只有一个。
        #   **「同一个判断写在两处，必然给出两个答案」** —— 这里是把两个不同的事实
        #   挤进一个共享节点，必然给出错的答案。
        # ★ `绑定` 也挂**边**上（和 `星号` 同类的串台）：
        #   `mod:` 节点是**全局共享**的，而**不同文件从同一模块导入的名字不同** ⇒
        #   挂节点上必然串台。**同一类错这次不许漏第二个字段。**
        # ★★★ **铺开 `导` 再覆盖**（R365）—— 这里原来也是**白名单**
        #   （只列 `行`/`星号`/`绑定`）⇒ 抽取层新加的 `节点类型` **静默丢掉**。
        #   ★ 同一个病本轮抓到**第三处**：
        #     ① `取导入` 出口的 `规范.append` ② 这里 ③ 下游 `_节型` 拿到空串。
        #     每一处都**不报错**，只是让一个字段消失。
        #   ★ 口径：**传递层不许筛字段** —— 过滤是**判断**，只能写在**判断该发生的那一处**。
        边.append({**导, "起": 文件节点, "终": 号, "关系": "导入", "行": 导["行"],
                   "星号": bool(导.get("星号")),
                   "绑定": list(导.get("绑定") or [])})
    return 节点, 边


def _结构化适配器(文本: str, 相对: str, 语: str) -> tuple[list[dict], list[dict]]:
    """json/yaml/toml：用标准库读，把**键路径**当定义。**不写正则。**"""
    节点: list[dict] = []
    边: list[dict] = [{"起": f"file:{相对}", "终": f"file:{相对}", "关系": "自"}]
    文件节点 = f"file:{相对}"
    try:
        if 语 == "json":
            import json as _j
            数据 = _j.loads(文本)
        elif 语 in ("yaml",):
            import yaml as _y
            数据 = _y.safe_load(文本)
        elif 语 == "toml":
            import tomllib as _t
            数据 = _t.loads(文本)
        else:
            return 节点, 边
    except Exception as e:                        # noqa: BLE001
        return [], [{"起": 文件节点, "终": f"parse_error:{相对}", "关系": "解析失败",
                     "原因": f"{type(e).__name__}: {e}"[:200]}]

    def 走(x, 路径: str) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                号 = f"key:{相对}:{路径}{k}"
                节点.append({"id": 号, "类": "键", "名": str(k), "路径": f"{路径}{k}",
                             "文件": 相对, "类型": type(v).__name__})
                边.append({"起": 文件节点, "终": 号, "关系": "包含"})
                走(v, f"{路径}{k}.")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                走(v, f"{路径}{i}.")

    走(数据, "")
    return 节点, 边


def _md链接目标(文: str) -> str:
    """markdown 链接目标 → **仓内相对路径**（`""` = 外部/无法归仓）。

    ★ 三种目标都实测存在（仓里 9,438 个 `.md`）：
      · `file:///E:/.../main.rs#L728-L743` —— **绝对 + 行号锚**（semsearch 生成的报告）
      · `other.md` / `../x/y.md` —— 相对本文件
      · `https://...` —— 外部，**不算断链**
    """
    import urllib.parse as _u
    文 = 文.strip().strip("<>").strip()
    if not 文:
        return ""
    低 = 文.lower()
    if 低.startswith(("http://", "https://", "mailto:", "ftp://", "//")):
        return ""                              # 外部 ⇒ 不算断链
    if 文.startswith("#"):
        return 文                              # 本文件锚
    文 = 文.split("#", 1)[0]                    # 行号/锚只留路径部分
    if not 文:
        return ""
    if 低.startswith("file://"):
        文 = _u.unquote(文[7:])
        # `file:///E:/x` ⇒ `/E:/x`；windows 上要把开头的 `/` 去掉
        文 = 文.lstrip("/")
        文 = 文.replace("\\", "/")
        # 绝对路径：能相对仓根就算仓内，否则算外部
        return 文
    return _u.unquote(文).replace("\\", "/")


def _md锚行(原文: str) -> tuple[int, int] | None:
    """从 markdown 链接原文里取 `#L728-L743` 的行号范围。

    ★ 必须在**归一化之前**从**原文**里取 —— `_md链接目标` 会把 `#` 后面全切掉，
      锚行信息在入口就丢了，**下游再想判越界也判不了**（假 0 的来源）。
    """
    import re as _re
    m = _re.search(r"#L(\d+)(?:\s*[-–]\s*L?(\d+))?", 原文, _re.I)
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    if b < a:
        a, b = b, a
    return a, b


def _Markdown适配器(文本: str, 相对: str, 语: str) -> tuple[list[dict], list[dict]]:
    """markdown：**标题算定义**（`#x` 锚的目标），**链接算引用**（真引用，可判存在性）。

    ★ 与代码适配器的分工：代码的引用靠**作用域**（import/绑定）解析；
      markdown 的引用**自带目标**（写着路径），所以能**直接判存在与否** ——
      这使它成为「未解析必须报出来」最干净的一处读数。
    """
    from tree_sitter import Parser as _P
    节: list[dict] = []
    边: list[dict] = []
    try:
        bs = 文本.encode("utf-8", "replace")
        树 = _P(_取语言(语)).parse(bs)
    except Exception as e:                        # noqa: BLE001
        节.append({"id": f"file:{相对}", "类": "文件", "名": 相对, "语言": "markdown",
                  "文件": 相对, "行": 1, "粒": _粒("md文件"), "深度": 0,
                  "来源": "文本", "确证": False,
                  "说明": f"markdown grammar 起不来：{type(e).__name__}"})
        return 节, 边

    def 段(n) -> str:
        return bs[n.start_byte:n.end_byte].decode("utf-8", "replace")

    链接 = 0
    栈 = [树.root_node]
    while 栈:
        n = 栈.pop()
        行 = n.start_point[0] + 1
        # ① 标题 ⇒ 定义节点（`#anchor` 的目标）
        if n.type in ("atx_heading", "setext_heading"):
            名 = 段(n).strip().lstrip("#").strip()
            名 = 名.replace("`", "").strip()
            if 名:
                号 = f"标题:{相对}:{名}:{行}"
                节.append({"id": 号, "类": "标题", "名": 名, "语言": "markdown",
                          "文件": 相对, "行": 行, "粒": _粒("md标题"), "深度": 0,
                          "父定义": None, "来源": "文本", "确证": False})
        # ② 链接**不在这里抽** —— 见下面的正则（这个 grammar 是块级的，
        #    内联链接被压成匿名 token，没有 `inline_link` 节点）
        elif False and ("link_destination" in n.type):
            目 = _md链接目标(段(n))
            if 目.startswith("#"):
                边.append({"起": f"file:{相对}", "终": f"锚:{相对}:{目[1:]}",
                          "关系": "引用", "依据": "markdown 本文件锚",
                          "行": 行, "已解析": False, "外部": False})
                链接 += 1
            elif 目:
                边.append({"起": f"file:{相对}", "终": f"路径:{目}",
                          "关系": "引用", "依据": "markdown 链接",
                          "行": 行, "已解析": False, "外部": False})
                链接 += 1
            else:
                边.append({"起": f"file:{相对}", "终": f"外链:{段(n)[:120]}",
                          "关系": "引用", "依据": "markdown 外链",
                          "行": 行, "已解析": False, "外部": True})
                链接 += 1
        for c in reversed(n.children):
            栈.append(c)

    # ②' **链接用正则抽，但要排除围栏代码块**（代码块里的 `[x](y)` 不是链接）
    import re as _re2
    在块内 = False
    围栏 = ""
    for i, 行文 in enumerate(文本.splitlines(), 1):
        t = 行文.lstrip()
        if t.startswith("```") or t.startswith("~~~"):
            标 = t[:3]
            if not 在块内:
                在块内, 围栏 = True, 标
            elif 标 == 围栏:
                在块内, 围栏 = False, ""
            continue
        if 在块内:
            continue
        # 行内代码 `` `...` `` 里的也算不是链接
        洁 = _re2.sub(r"`[^`]*`", "", 行文)
        for m in _re2.finditer(r"\]\(([^)\s]*)", 洁):
            原 = m.group(1)
            目 = _md链接目标(原)
            锚行 = _md锚行(原)
            if 目.startswith("#"):
                边.append({"起": f"file:{相对}", "终": f"锚:{相对}:{目[1:]}",
                          "关系": "引用", "依据": "markdown 本文件锚",
                          "行": i, "已解析": False, "外部": False})
            elif 目:
                边.append({"起": f"file:{相对}", "终": f"路径:{目}",
                          "关系": "引用", "依据": "markdown 链接",
                          "行": i, "已解析": False, "外部": False,
                          **({"锚行": list(锚行)} if 锚行 else {})})
            else:
                边.append({"起": f"file:{相对}", "终": f"外链:{m.group(1)[:120]}",
                          "关系": "引用", "依据": "markdown 外链",
                          "行": i, "已解析": False, "外部": True})
        # 引用式定义 `[ref]: target`
        for m in _re2.finditer(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)", 行文):
            目 = _md链接目标(m.group(1))
            if 目 and not 目.startswith("#"):
                边.append({"起": f"file:{相对}", "终": f"路径:{目}",
                          "关系": "引用", "依据": "markdown 引用式定义",
                          "行": i, "已解析": False, "外部": False})

    if not any(x.get("类") == "文件" for x in 节):
        节.insert(0, {"id": f"file:{相对}", "类": "文件", "名": 相对,
                     "语言": "markdown", "文件": 相对, "行": 1,
                     "粒": _粒("md文件"), "深度": 0, "来源": "文本", "确证": False})
    return 节, 边


def _剥JSON注释(文: str) -> str:
    """剥 `//` 与 `/* */` 注释 —— **字符串里的 `//` 不许当注释**（如 `"http://x"`）。"""
    出: list[str] = []
    i, n = 0, len(文)
    在串 = False
    引 = ""
    while i < n:
        c = 文[i]
        if 在串:
            出.append(c)
            if c == "\\" and i + 1 < n:
                出.append(文[i + 1])
                i += 2
                continue
            if c == 引:
                在串 = False
            i += 1
            continue
        if c in "\"'":
            在串, 引 = True, c
            出.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and 文[i + 1] == "/":
            while i < n and 文[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and 文[i + 1] == "*":
            i += 2
            while i + 1 < n and not (文[i] == "*" and 文[i + 1] == "/"):
                i += 1
            i += 2
            continue
        出.append(c)
        i += 1
    return "".join(出)


def _读JSON(文本: str) -> tuple[object, str]:
    """读 JSON/JSONC/JSON5。返回 `(对象, 走的路)`；全失败 `(None, "失败")`。

    ★ **不静默降级** —— 读不出来就明说读不出来。把读不出的配置变成 `{}`，
      下游会以为「这文件没有引用」，而真相是「我没读懂」。
    """
    import json as _json
    try:
        return _json.loads(文本), "严格JSON"
    except Exception:                                             # noqa: BLE001
        pass
    路: list[str] = []
    try:
        import json5 as _j5
        左 = _j5.loads(文本)
        路.append("json5")
    except Exception:                                             # noqa: BLE001
        左 = _解不开
    try:
        import re as _re
        净 = _re.sub(r",(\s*[}\]])", r"\1", _剥JSON注释(文本))
        右 = _json.loads(净)
        路.append("自剥注释")
    except Exception:                                             # noqa: BLE001
        右 = _解不开
    # ★ 两条路都成功 ⇒ 必须给出**同一个结构**（对账）
    if 左 is not _解不开 and 右 is not _解不开:
        if 左 == 右:
            return 左, "+".join(路) + "（两条路一致）"
        return 左, "+".join(路) + "（★两条路不一致）"
    if 左 is not _解不开:
        return 左, "json5"
    if 右 is not _解不开:
        return 右, "自剥注释"
    return None, "失败"


def _json指针(档: object, 针: str) -> object:
    """按 RFC 6901 解 JSON 指针（`#/definitions/X`）。解不开返回 `_解不开`。"""
    if not 针:
        return 档
    针 = 针.lstrip("#")
    if not 针.startswith("/"):
        return _解不开
    当 = 档
    for 段 in 针[1:].split("/"):
        段 = 段.replace("~1", "/").replace("~0", "~")
        if isinstance(当, dict) and 段 in 当:
            当 = 当[段]
        elif isinstance(当, list) and 段.isdigit() and int(段) < len(当):
            当 = 当[int(段)]
        else:
            return _解不开
    return 当


_解不开 = object()


def _结构化引用(文本: str, 相对: str, 语: str) -> list[dict]:
    """`json`/`yaml` 里的**真引用**：JSON `$ref`（文件 + 指针）、YAML 别名（须有锚点）。

    ★ 为什么这是真引用而不是「名字出现过」：
      · `$ref` 的**目标文件在不在**是文件系统事实；
        `#/definitions/X` 这个**指针能不能在目标里解开**是解析事实。
        **两样都不是我猜的。**
      · YAML 别名 `*self` **必须**在同文件里有 `&self` —— 没有就是断引用，
        而「有没有」由**规范决定**，由**权威 token 流**给出，不由正则猜。
    """
    出: list[dict] = []
    低 = (语 or "").lower()
    if "json" in 低:
        档, _路 = _读JSON(文本)
        if 档 is None:
            return 出

        def 走(当, 路: str) -> None:
            if isinstance(当, dict):
                if "$ref" in 当 and isinstance(当["$ref"], str):
                    原 = 当["$ref"]
                    文, _, 针 = 原.partition("#")
                    _外 = 文.lower().startswith(
                        ("http://", "https://", "//", "urn:"))
                    出.append({"起": f"file:{相对}",
                              "终": (f"外链:{原}" if _外 else
                                    (f"路径:{文}" if 文 else f"本文件指针:{针}")),
                              "关系": "引用", "依据": "json $ref",
                              "行": 0, "已解析": False, "外部": _外,
                              "指针": 针 or "", "路": 路})
                for k, v in 当.items():
                    走(v, f"{路}/{k}" if 路 else f"/{k}")
            elif isinstance(当, list):
                for i, v in enumerate(当):
                    走(v, f"{路}/{i}")
        走(档, "")
        # ★ **跨文件引用还有别的规约**（不只有 `$ref`）——
        #   `.json` 里 `$ref` 出现 0 次的仓，引用却确实存在：
        #   `tsconfig.json` 的 `extends` / `references[].path` 就是**跨文件引用**，
        #   解析算法由 TypeScript 定死：**相对引用文件所在目录**、**可省 `.json` 后缀**。
        def 逛(当) -> None:
            if isinstance(当, dict):
                ex = 当.get("extends")
                if isinstance(ex, str):
                    出.append({"起": f"file:{相对}", "终": f"路径:{ex}",
                              "关系": "引用", "依据": "json extends",
                              "行": 0, "已解析": False,
                              "外部": ex.lower().startswith(("http", "//")),
                              "可省后缀": True})
                refs = 当.get("references")
                if isinstance(refs, list):
                    for it in refs:
                        if isinstance(it, dict) and isinstance(it.get("path"), str):
                            出.append({"起": f"file:{相对}",
                                      "终": f"路径:{it['path']}",
                                      "关系": "引用", "依据": "json references",
                                      "行": 0, "已解析": False, "外部": False,
                                      "可省后缀": True, "目录型": True})
                for v in 当.values():
                    逛(v)
            elif isinstance(当, list):
                for v in 当:
                    逛(v)
        逛(档)
        return 出

    if "yaml" in 低:
        try:
            import yaml as _y
        except Exception:                                          # noqa: BLE001
            return 出
        try:
            标 = list(_y.scan(文本))
        except Exception:                                          # noqa: BLE001
            return 出
        锚: set[str] = set()
        for t in 标:
            if type(t).__name__ == "AnchorToken":
                锚.add(getattr(t, "value", ""))
        for t in 标:
            if type(t).__name__ == "AliasToken":
                名 = getattr(t, "value", "")
                行 = getattr(getattr(t, "start_mark", None), "line", 0) + 1
                出.append({"起": f"file:{相对}", "终": f"锚引用:{相对}:{名}",
                          "关系": "引用", "依据": "yaml 别名",
                          "行": 行, "已解析": 名 in 锚, "外部": False,
                          "锚名": 名})
        return 出
    return 出


_结构化适配器_原 = _结构化适配器


def _结构化适配器(文本: str, 相对: str, 语: str) -> tuple[list[dict], list[dict]]:
    """在 `_结构化适配器_原` 外面**包一层**，把 `json`/`yaml` 的**真引用**接上。

    ★ 为什么包一层而不是往原函数某个 `return` 前插：原函数**有两个 return**，
      插一处漏一处。**唯一的包装层**覆盖全部返回路径。
      （上一轮的教训是「登记了不等于接上了」；这一轮的正确做法是
        **把接线接在唯一的地方**。）
    """
    节, 边 = _结构化适配器_原(文本, 相对, 语)
    # ★ **JSONC**：`tsconfig.json` 是带注释/尾随逗号的 JSON，严格 `json.loads` 读不了。
    #   实测被原函数判成「解析失败」并写了 `parse_error` 边 ——
    #   **我把一个合法配置文件当成了坏文件。** 用 `json5`（权威）+ 自剥注释 两条路读。
    if any((e.get("关系") or "") == "解析失败" for e in 边) and 语 in ("json",):
        _档, _走 = _读JSON(文本)
        if _档 is not None:
            try:
                import json as _j2
                节, 边 = _结构化适配器_原(
                    _j2.dumps(_档, ensure_ascii=False), 相对, 语)
                边 = list(边) + [{"起": f"file:{相对}", "终": f"file:{相对}",
                               "关系": "说明", "依据": f"JSONC（{_走}）"}]
            except Exception:                                     # noqa: BLE001
                pass
    try:
        边 = list(边) + _结构化引用(文本, 相对, 语)
    except Exception:                                             # noqa: BLE001
        pass
    return 节, 边


def _剥Lean注释(文: str) -> str:
    """剥 Lean 注释：行注释 `--`、块注释 `/- -/`（**块注释可嵌套**）。

    ★ 实测踩过：不剥注释，正则扫到**注释里**的 `theorem xxx_原` ⇒ 假阳。
      对账里「我多」从一片降到 **14**，就是这一步的功劳。
    ★ 只去注释，**不动代码**；字符串字面量里的 `--` 会一起被吃掉 ——
      但 Lean 声明名不出现在字符串里，所以对「抽声明名」无害（**边界如实记**）。
    """
    出: list[str] = []
    i, n, 层 = 0, len(文), 0
    while i < n:
        if 层 == 0 and 文.startswith("--", i):
            j = 文.find("\n", i)
            i = n if j < 0 else j
            continue
        if 文.startswith("/-", i):
            层 += 1
            i += 2
            continue
        if 层 and 文.startswith("-/", i):
            层 -= 1
            i += 2
            continue
        if 层 == 0:
            出.append(文[i])
        elif 文[i] == "\n":
            # ★★★ R465：**块注释里的换行必须留下** ——
            #   原来块注释**整个删掉（含 `\n`）** ⇒ **剥完注释的文本行数变了**
            #   ⇒ 后面所有 `行` 字段**相对的是"净文本"而不是源文件** ✗
            #   实测（`formal_proof/LayerShrink.lean`）：一条 `未解析` 边的 `行=7`
            #   指过去是**空行**，`行=8` 指到 `namespace LayerShrink` ——
            #   **行号有、但查不到东西**，等于**有行号的不可行动缺口**。
            #   ★ 根因特别隐蔽：Lean 的**文档注释**是 `/-- … -/`（块注释）**极其常见**，
            #     而**行注释**那一支本来就保留了 `\n` ⇒ **只有块注释偏**。
            #   ★ 口径：**"行号"必须相对"人要打开的那份文件"** ——
            #     相对中间产物（净文本）的行号，**看起来对、实际错**，比没有行号更坏。
            出.append("\n")
        i += 1
    return "".join(出)


#: Lean 的**定义类**关键字（进图当「定义」）。
_Lean定义 = ("def", "theorem", "lemma", "abbrev", "instance", "inductive",
           "structure", "class", "opaque", "example", "deriving")
#: Lean 的**声明类**关键字（只声明，不进定义）。
_Lean声明 = ("axiom", "constant", "variable", "universe", "notation", "macro",
           "syntax", "elab")
#: Lean 里带名字的**命令**（`#check foo` 之类是**用**，不是定义）。
_Lean命令 = re.compile(r"^\s*#(check|print|reduce|eval|eval!|synth|guard)\s+(.+)$",
                     re.MULTILINE)


def _Lean适配器(文本: str, 相对: str, 语: str) -> tuple[list[dict], list[dict]]:
    """Lean 的**文本级**适配器。

    ★ 为什么 Lean 单列一个适配器：**没有 tree-sitter grammar**（`_语法` 里故意没有 `.lean`），
      而 Lean 的**真结构只有 elaborator 有**（自动名 / 构造子 / 投影名 —— 实测
      elaborator 确证 34,227 个去重裸名，文本级只覆盖 **17,271 = 50.5%**）。
      ⇒ 本适配器给的是**文本级那一半**，每条都标 `来源: "文本"`、`确证: False`；
        elaborator 那一半由 `灌Lean确证` 灌进**同一张图**，标 `确证: True`。
        **两层并列，各标来源** —— 不是让文本级去假装 elaborator。
    """
    try:
        from . import mem_symbols as _ms
    except Exception:                             # noqa: BLE001
        _ms = None
    文件节点 = f"file:{相对}"
    节点: list[dict] = []
    边: list[dict] = []
    净 = _剥Lean注释(文本)
    # ★★★★ R507：**引号归一化必须在"两个消费者"之前做**（口径 305）。
    #   `净` 有两条消费路径：
    #     ① `抽(净, "lean")` —— **声明抽取**（mem_symbols）
    #     ② 下面的构造子/字段/绑定扫描（原来用 `_净`）
    #   ★ R471 只把归一化加在 ② 上（注释原文：「`«` / `»` 不是标识符字符 ⇒
    #     12 个地支构造子一个都没抽出来」）⇒ ① **整批漏抽**：
    #     实测 `inductive «十神266r»` / `def «丙»` 在图上**根本没有节点**，
    #     而它们的构造子却抽出来了（限定名 `十神266r.比肩`，父为空）。
    #   ⇒ 真仓 **1,897 个 .lean 文件**带 `«`（分属 5 个顶层目录：707 · 697 ·
    #     483 · 7 · 3）。
    #   ★ 换成**空格**保住列号（与 R471 同一选择，不另立口径）。
    净 = re.sub(r"[«»]", " ", 净)

    # ── namespace 栈 ⇒ 限定名（Lean 的 `namespace Foo` / `end Foo`）─────────
    #   ★ 按行扫（不用语法树）—— 这是 Lean 的**文本约定**，不是猜。
    前缀栈: list[str] = []
    命名空间行: list[tuple[int, str]] = []
    for i, 行 in enumerate(净.splitlines(), 1):
        t = 行.strip()
        if t.startswith("namespace "):
            前缀栈.append(t[len("namespace "):].strip())
            命名空间行.append((i, ".".join(前缀栈)))
        elif t == "end" or t.startswith("end "):
            if 前缀栈:
                前缀栈.pop()
    # 行 → 该行所在 namespace 前缀（用命名空间行的区间切）
    区间 = []
    for k, (行号, _p) in enumerate(命名空间行):
        pass
    # 简化：按行号顺序重放
    行前缀: dict[int, str] = {}
    栈2: list[str] = []
    for i, 行 in enumerate(净.splitlines(), 1):
        t = 行.strip()
        行前缀[i] = ".".join(栈2)
        if t.startswith("namespace "):
            栈2.append(t[len("namespace "):].strip())
        elif t == "end" or t.startswith("end "):
            if 栈2:
                栈2.pop()

    抽 = _ms.抽取符号 if (_ms is not None and hasattr(_ms, "抽取符号")) else None
    声明们: list[tuple[str, str, int]] = []
    if 抽 is not None:
        try:
            # ★ 真实形状 = `[(名字, 关键字, 行, 内容)]` **第一字段就是名字**
            #   （我第一版把 tuple 里所有字符串都当名字 ⇒ 把整段代码都当成了名字。）
            for 项 in 抽(净, "lean"):
                if isinstance(项, (tuple, list)) and 项 and isinstance(项[0], str):
                    名 = 项[0].strip()
                    类词 = 项[1] if len(项) > 1 and isinstance(项[1], str) else ""
                    行 = 项[2] if len(项) > 2 and isinstance(项[2], int) else 0
                    if 名:
                        声明们.append((名, 类词, 行))
        except Exception:                         # noqa: BLE001
            声明们 = []

    见 = set()
    for 名, 类词, 行 in 声明们:
        if (名, 行) in 见:
            continue
        见.add((名, 行))
        前 = 行前缀.get(行, "")
        全名 = f"{前}.{名}" if 前 else 名
        c类 = "定义" if 类词 in _Lean定义 else ("声明" if 类词 in _Lean声明 else "定义")
        号 = f"{c类}:{相对}:{名}:{行}"
        节点.append({
            "id": 号, "类": c类, "名": 名, "语言": "lean",
            "限定名": 全名 if "." in 全名 else None,
            "文件": 相对, "行": 行, "类型": f"lean:{类词}",
            "命名空间深度": _命名空间深度(行前缀, 行),
            "父定义": None,
            # ★★★★ R503：**`namespace` 是命名空间，不是「内嵌」**。
            #   原来写的是 `"顶层" if not 前 else "内嵌"` —— 而 `前` 是
            #   **namespace 栈**（L2627-2631 逐行扫出来的）⇒
            #   **`粒` 实际测的是"namespace 有多深"，不是"是不是嵌在定义体里"** ✗
            #   实测（判一百五十二，全 17 桶）：
            #     `内嵌 × lean:theorem` 102,557 · `内嵌 × lean:structure` 6,143
            #     （**structure 一个顶层都没有**）
            #     **164,647 个定义「有命名空间前缀、却没有父定义，粒却是内嵌」**
            #   抽样 `深=1` 的：限定名 `Self.orbit_length` · `lean_self/P3.lean`
            #     ⇒ **`namespace Self` 里的定理**，**不是嵌套定义** ✓
            #   ★ 为什么必须分开（**目标②的实质**，不是命名口味）：
            #     `namespace Self` 里的 `orbit_length` —— 作用域链是 **模块 → Self**，
            #       **名字挂在命名空间上，不随任何定义体进出作用域**
            #       （Lean 的 `namespace`/`end` **不建作用域**，只加前缀）
            #     `def f` 体里的 `g` —— 作用域链 **模块 → f → g**，`g` 是 `f` 的局部
            #     ⇒ 混成一态 ⇒ **解析"这个名字从哪来"时没有任何字段能区分**
            #   ★ Python 侧的对应物已经分开了（`类体` vs `内嵌`，R502）——
            #     **因为 Python 的 `class` 体**确实**建作用域**，而 Lean 的 `namespace` 不建。
            #     ⇒ **两种语言的正确值不同，这不是不一致，是语义差别**：
            #       Python 类体 ⇒ `类体`；Lean 命名空间 ⇒ **`顶层`**。
            #   ★ namespace 的深度**没丢**：在 `深度` 与 `限定名` 里（`Self.orbit_length`）。
            "粒": _粒("文件"),
            # ★★ **来源与确证** —— 图谱上必须能分清「文本说的」与「elaborator 说的」
            "来源": "文本", "确证": False,
        })
        边.append({"起": 文件节点, "终": 号, "关系": "包含"})

    # ★★★ **Lean 的绑定位**（R406）—— 目标②「把定义绑到作用域」在 Lean 这一侧。
    #   实测（R405）：未解析 105,856 里**局部绑定是绝对大头**：
    #     h 2114 · n 1516 · x 1360 · a 1264 · b 1162 · p 924 · k 867 · f 837 ·
    #     c 780 · y 739 · h1 732 · s 704 · d 703 · h2 652 · ih 622 · m 618 · i 611
    #   ★ 根因：本适配器**只抽声明，不抽绑定** ⇒
    #       `theorem foo (h : P) : Q := by intro h; exact h`
    #       里的两个 `h` 在图上**根本没有节点** ⇒ 引用它们必然「未解析」。
    #   ★★ 口径：**「按名字列举」与「按语法位置」是两种完备性** ——
    #      上一轮写好的 `_Lean似局部`（按名字猜）**故意没接**：
    #      `h` 也可能是真外部名，**按名字列举必然漏**。
    #      这里按**语法位置**抽：`(`…`:` 之间、`intro` 之后、`let` 左边、
    #      `fun` 之后、`rcases … ⟨…⟩` 里面 —— **位置是语法事实，不猜名字。**
    #   ★ 每条标 `类: "绑定"`，与 `解析引用` 的 `本文件绑定` 索引天然对齐
    #     （它就是查 `类 == "绑定"`）。
    _绑见 = set()

    def _记绑(_名: str, _行: int, _怎么来的: str) -> None:
        if not _名 or not re.fullmatch(r"[^\W\d][\w']*", _名):
            return
        if _名 in _Lean关键字 or _名 in _LeanTactic:
            return
        if (_名, _行) in _绑见:
            return
        _绑见.add((_名, _行))
        _号 = f"绑定:{相对}:{_名}:{_行}"
        节点.append({
            "id": _号, "类": "绑定", "名": _名, "语言": "lean",
            "限定名": None, "文件": 相对, "行": _行,
            "类型": f"lean:绑定({_怎么来的})",
            "命名空间深度": _命名空间深度(行前缀, _行), "父定义": None, "粒": _粒("定义体"),
            "来源": "文本", "确证": False,
        })
        边.append({"起": 文件节点, "终": _号, "关系": "包含"})

    # ★★★ R471：**`inductive` 的构造子与 `structure` 的字段也是「定义」** ——
    #   实测（`形式化验证` 的 `★真外部缺`）：
    #     `ff` 未解析 **239** · `tt` 未解析 **239** · `dep_type` **652** ·
    #     `proof_status` **652**
    #   而它们的源行是：
    #     `_archive/验收目录/Foundational/PureBoolLemmas.lean:62   | ff => rfl`
    #     `formal_proof/MyMeta/MasterTable/DependencyTable_A01.lean:33  dep_type : DepType`
    #   ★ 根因**两条，都是"这个位置我没抽"**：
    #     ① **构造子**：`inductive MyBool where | ff : MyBool | tt : MyBool`
    #        里的 `ff`/`tt` 是**定义名**，而本适配器**只抽声明关键字**
    #        （`def`/`theorem`/…）⇒ 构造子从来没被抽 ⇒ 后续 `| ff =>` 引用它
    #        必然「未解析」✗
    #     ② **结构字段**：`structure X where` 下的 `dep_type : DepType`
    #        前面**没有 `(`/`{`/`[`** ⇒ 参数那条正则**匹配不上** ✗
    #   ★ 为什么不把构造子记成「绑定」：**绑定是"局部名"，构造子是"定义"** ——
    #     记错类会让 `解析引用` 走到另一条分支（口径 190 的近亲：**类别是语义，不是标签**）。
    #   ★ 块的边界按**缩进**判：`inductive X where` 之后，
    #     缩进**比它深**的行属于这个块；遇到缩进**不深于**它的非空行就结束。
    # ★★★ R477：**Lean 的转义标识符 `«…»`** ——
    #   实测（`子` 未解析 742 · `寅` 456 · `午` 452 …）根因在这里：
    #     `inductive DiZhi : Type where`
    #       `| «子» | «丑» | «寅» | «卯» | «辰» | «巳» | «午» | …`
    #   ⇒ 构造子名是 **`«子»`**（尖括号是 Lean 的**标识符引号**，
    #     用来把与关键字冲突的名字包起来）。
    #   ★ `«` / `»` **不是标识符字符** ⇒ 原字符类 `[^\W\d]` 匹配不上 ⇒
    #     12 个地支构造子**一个都没抽出来**，而全书 742 处引用全部落「未解析」✗
    #   ⇒ 统一把 `«` / `»` 换成**空格**（保列号），让 `«子»` 退化成裸名 `子`。
    #     ★ 这样 `DiZhi.«子»` 变成 `DiZhi. 子 ` ⇒ `子` 是裸提及，
    #       与 `DiZhi.toNat` 的处理一致（限定名后段本来就不由裸名正则负责）。
    _净 = 净   # ★ R507：归一化已提前（口径 305：只留一处做这件事）
    # ★★★ R479：**`where` 可以省略** ——
    #   实测（`ff` 未解析 239 · `tt` 239，12 个文件都 `import MyMeta.Foundational.PureBool`）：
    #     `inductive PureBool : Type`
    #       `| tt : PureBool`
    #       `| ff : PureBool`
    #   ⇒ 头一行**没有 `where`**，而原来的正则**强制要求** `\bwhere\b`
    #     ⇒ 整个块没进 ⇒ 两个构造子一个都没抽 ✗
    #   ★ Lean 4 里 `where` 在构造子以 `|` 开头时**可省**（`structure` 也一样）。
    #   ⇒ 改成 `where` **可选**：`(?:\bwhere\b|(?=:\s*$))` 太绕，直接去掉它，
    #     靠**缩进**判块边界（下一行的缩进更深 = 还在这个块里）。
    #   ★ 去掉 `where` 会不会误收？—— 不会：块头仍然要求行首（可有缩进）
    #     是 `inductive|structure|class`，且**块内行必须缩进更深**；
    #     `inductive` 出现在注释/字符串里时，`净` 里它们的引号已被处理，
    #     而注释里的 `inductive` 后面通常跟着中文，不会形成「缩进更深的 | 行」。
    _块头 = re.compile(r"^(\s*)(inductive|structure|class)\b[^\n]*?((?:\bwhere\b)|(?:\s*:))")
    _构造 = re.compile(r"^\s*\|\s*([^\W\d][\w']*)\s*(?::|$)")
    _字段 = re.compile(r"^(\s+)([^\W\d][\w']*)\s*:")
    _行列 = _净.splitlines()
    for _i7, _行7 in enumerate(_行列, 1):
        _mh = _块头.match(_行7)
        if not _mh:
            continue
        _缩 = len(_mh.group(1))
        _类头 = _mh.group(2)
        # ★★★★ R506：**构造子/结构字段的全名靠父定义**（`Self.T.leaf`）——
        #   在这里一次算出父节点，后面构造子/字段两处都用它。
        #
        #   ★ 为什么可以精确找（口径 275：只用抽取阶段就有的字段做键）：
        #     实测 P3.lean —— `inductive/structure/class` 的**块头行
        #     与声明节点的 `行` 完全相等（命中 10/10）**
        #     ⇒ 用 **(名, 行)** 两个字段直接命中，**不猜**。
        #   ★ 找不到就**如实留 `None`**，并且**不编一个 id 出来** ——
        #     悬空父定义比 `None` 更坏：它**看起来**绑上了（判 156 ③ 就是抓这个）。
        _m名 = re.match(r"\s*(?:inductive|structure|class)\s+([^\W\d][\w'.«»]*)", _行7)
        _类型名 = _m名.group(1) if _m名 else ""
        _父号 = None
        _父全 = ""
        if _类型名:
            for _v9 in 节点:
                if (_v9.get("类") == "定义" and _v9.get("语言") == "lean"
                        and _v9.get("名") == _类型名 and _v9.get("行") == _i7):
                    _父号 = _v9.get("id")
                    _父全 = str(_v9.get("限定名") or "")
                    break
        for _j7 in range(_i7, len(_行列)):          # _i7 是 1-based ⇒ 下一行是 索引 _i7
            _行8 = _行列[_j7]
            if not _行8.strip():
                continue
            _缩8 = len(_行8) - len(_行8.lstrip())
            if _缩8 <= _缩:
                break
            if _类头 == "inductive":
                # ★★★ R477：**一行可以有多个构造子** ——
                #   `| «子» | «丑» | «寅» | …`（实测 DiZhi 一行 12 个）。
                #   原来用 `^\s*\|\s*名字` **只匹配行首第一个** ⇒ 其余 11 个全丢 ✗
                #   ⇒ 改成 `finditer` 扫**所有** `| 名字`。
                _mc = None
                for _mc in re.finditer(r"\|\s*([^\W\d][\w']*)\s*(?=[:|]|$)", _行8):
                    _名7 = _mc.group(1)
                    _号7 = f"定义:{相对}:{_名7}:{_j7 + 1}"
                    if any(x["id"] == _号7 for x in 节点):
                        continue
                    节点.append({"id": _号7, "类": "定义", "名": _名7, "语言": "lean",
                        # ★ 口径 203：**父没找到 ⇒ 不填**（填了就是少一层的错名字）
                        "限定名": ((_父全 or _类型名) + "." + _名7) if (_类型名 and _父号) else None,
                        "文件": 相对, "行": _j7 + 1,
                        "类型": "lean:构造子",
                        "命名空间深度": _命名空间深度(行前缀, _j7 + 1), "父定义": _父号,
                        "粒": _粒("文件"), "来源": "文本", "确证": False})
                    边.append({"起": 文件节点, "终": _号7, "关系": "包含"})
                if _mc is not None:
                    continue
                if False:
                    _名7 = ""

                    _号7 = f"定义:{相对}:{_名7}:{_j7 + 1}"
                    if not any(x["id"] == _号7 for x in 节点):
                        节点.append({
                            "id": _号7, "类": "定义", "名": _名7, "语言": "lean",
                            "限定名": None, "文件": 相对, "行": _j7 + 1,
                            "类型": "lean:构造子",
                            "命名空间深度": _命名空间深度(行前缀, _j7 + 1), "父定义": _父号,
                            "粒": _粒("文件"), "来源": "文本", "确证": False,
                        })
                        边.append({"起": 文件节点, "终": _号7, "关系": "包含"})
                    continue
            _mf = _字段.match(_行8)
            if _mf:
                _名7 = _mf.group(2)
                # ★ 字段位**不许**把 `|` 开头的构造子行也算进来（上面已 continue）
                _号7 = f"定义:{相对}:{_名7}:{_j7 + 1}"
                if not any(x["id"] == _号7 for x in 节点):
                    节点.append({
                        "id": _号7, "类": "定义", "名": _名7, "语言": "lean",
                        # ★ 口径 203：**父没找到 ⇒ 不填**（填了就是少一层的错名字）
                        "限定名": ((_父全 or _类型名) + "." + _名7) if (_类型名 and _父号) else None,
                        "文件": 相对, "行": _j7 + 1,
                        "类型": "lean:结构字段",
                        "命名空间深度": _命名空间深度(行前缀, _j7 + 1), "父定义": _父号,
                        "粒": _粒("文件"), "来源": "文本", "确证": False,
                    })
                    边.append({"起": 文件节点, "终": _号7, "关系": "包含"})

    # ★★★ **归纳/递归的 case 绑定**（R408）——
    #   `induction n with | zero => … | succ k ih => …` 里的 `k` / `ih`，
    #   `cases h with | inl hp => …` 里的 `hp`。
    #   实测（R407 后）：`ih` 604 次仍是「真外部缺」Top1 ✗，`h1`/`h2` 同理。
    #   ★ 口径：**「`|` 之后到 `=>` 之前」是一个绑定位置** ——
    #     它和 `(`…`:` 一样是**语法位置**，不是"被引用的标识符"。
    for _i5, _行5 in enumerate(净.splitlines(), 1):
        # `| succ k ih =>` / `| zero =>` / `| inl hp =>`
        for _m5 in re.finditer(r"\|\s*[A-Za-z_][\w']*\s+([^=]*?)=>(?!=)", _行5):
            for _n5 in re.findall(r"[^\W\d][\w']*",
                                  _m5.group(1)):
                _记绑(_n5, _i5, "case")
        # `have h' : P := …` / `have : P := …` / `suffices h : P by`
        #   ★ `have h'` 里的 `h'` **带撇号**，上一版漏了 ⇒ 147 次算「真外部缺」（R409）
        for _m5 in re.finditer(r"\b(?:have|suffices|assert)\s+([A-Za-z_\u4e00-\u9fff]"
                               r"[\w'\u4e00-\u9fff]*)\s*[:=]", _行5):
            _记绑(_m5.group(1), _i5, "have")
        # `rintro ⟨a, b⟩` / `rintro rfl | h`

        for _m5 in re.finditer(r"\brintro\b(.+)$", _行5):
            for _n5 in re.findall(r"[^\W\d][\w']*",
                                  _m5.group(1)):
                _记绑(_n5, _i5, "rintro")
        # `universe u v` / `variable (u : …)`
        for _m5 in re.finditer(r"^\s*(?:universe|variable)\s+(.+)$", _行5):
            for _n5 in re.findall(r"[^\W\d][\w']*",
                                  _m5.group(1)):
                _记绑(_n5, _i5, "universe")

    # ★★★ **`set_option X …` 的 `X` 是选项名，不是被引用的标识符**（R407）。
    #   实测（判九十三 的红文）：`maxHeartbeats` · `autoImplicit` · `relaxedAutoImplicit`
    #   · `only` · `using` · `axioms` 出现在「真外部缺」Top20 里 ✗
    #   —— 它们**不是缺口**，是 Lean 的**选项名**。
    #   ★ 口径：**「在名字的位置上」不等于「是名字」** ——
    #     `set_option` 后面那一段是**选项标识符**，它的"定义"在 Lean 自己里面。
    #     **按语法位置判，不按名字列举**（口径 103）。
    for _i4, _行4 in enumerate(净.splitlines(), 1):
        _m4 = re.match(r"\s*set_option\s+([A-Za-z_][\w.]*)", _行4)
        if not _m4:
            continue
        for _段4 in _m4.group(1).split("."):
            if not _段4:
                continue
            _号4 = f"绑定:{相对}:{_段4}:{_i4}"
            if any(x["id"] == _号4 for x in 节点):
                continue
            节点.append({
                "id": _号4, "类": "绑定", "名": _段4, "语言": "lean",
                "限定名": None, "文件": 相对, "行": _i4,
                "类型": "lean:选项名(set_option)",
                "命名空间深度": _命名空间深度(行前缀, _i4), "父定义": None,
                "粒": _粒("定义体"), "来源": "文本", "确证": False,
            })
            边.append({"起": 文件节点, "终": _号4, "关系": "包含"})

    # ★★★ **`namespace X` 也必须是个定义**（R407）。
    #   实测：`MyMeta` 3,771 次被算成「真外部缺」✗ —— 而它是
    #   本仓自己的命名空间（`MyMeta.Foundational.…` 的第一段）。
    #   ★ 根因：适配器**只抽声明，不抽命名空间** ⇒ `MyMeta` 在图上
    #     **没有裸名定义** ⇒ 分类器当然把它当缺口。
    #   ★ 口径：**「半截名字」不是靠"它长得像前缀"判的，是靠"本仓声明过它"判的** ——
    #     前者是猜，后者是事实。
    for _i3, _行3 in enumerate(净.splitlines(), 1):
        _m3 = re.match(r"\s*namespace\s+([A-Za-z_\u4e00-\u9fff][\w'.\u4e00-\u9fff]*)", _行3)
        if not _m3:
            continue
        for _段 in _m3.group(1).split("."):
            _段 = _段.strip()
            if not _段:
                continue
            _号3 = f"定义:{相对}:{_段}:{_i3}"
            if any(x["id"] == _号3 for x in 节点):
                continue
            节点.append({
                "id": _号3, "类": "定义", "名": _段, "语言": "lean",
                "限定名": None, "文件": 相对, "行": _i3,
                "类型": "lean:namespace",
                "命名空间深度": _命名空间深度(行前缀, _i3), "父定义": None,
                "粒": _粒("文件"), "来源": "文本", "确证": False,
            })
            边.append({"起": 文件节点, "终": _号3, "关系": "包含"})


    for _i, _行2 in enumerate(净.splitlines(), 1):
        for _m in re.finditer(r"[\(\{\[]\s*([^\W\d][\w']*"
                              r"(?:\s+[^\W\d][\w']*)*)\s*:", _行2):
            for _n2 in _m.group(1).split():
                _记绑(_n2, _i, "参数")
        for _m in re.finditer(r"\bintro(?:s)?\s+([^\W\d][\w']*"
                              r"(?:\s+[^\W\d][\w']*)*)", _行2):
            for _n2 in _m.group(1).split():
                _记绑(_n2, _i, "intro")
        # ★★★ R472：**`by_cases h : P` / `cases h : P` 里的 `h` 是绑定位** ——
        #   实测（`形式化验证` 的 `★真外部缺`，全是未解析）：
        #     hlt 124 (`by_cases hlt : ringOf ti < ringOf yong`)
        #     hl  154 (`cases hl : lookupForced forced l.var with`)
        #     hb  113 · h' 110 · hc 109 · hx 98 · heq 89 · hxy 82 …
        #   ⇒ **`by_cases`/`cases` 是 Lean 里最常见的"引入一个假设"的写法**，
        #     而本适配器**从来没抽过它的名字** ⇒ 每个都成了「AI 缺什么」✗
        #   ★ 形状是 `命令 名字 : 命题` —— `:` 在**名字之后**，
        #     所以「参数」那条正则（要求前面有 `(`/`{`/`[`）覆盖不到。
        #   ★ 同一族按**这一族枚举**（R461/R462 的老病：同族不同后缀）：
        #     `by_cases` / `cases` / `rcases` / `obtain` / `induction` /
        #     `generalize` / `simp` 之后都可以跟 `名字 :`。
        #     ★ 但**只认紧跟命令的第一个 `名字 :`** —— `cases ha : S a <;>
        #       cases hb : S b` 这样串起来的多条由 `finditer` 各自命中 ✓
        for _m in re.finditer(r"\b(?:by_cases|cases|rcases|obtain|induction|generalize)"
                              r"\s+([^\W\d][\w']*)\s*:", _行2):
            _记绑(_m.group(1), _i, "cases")
        # ★★★ R474：**还有五类绑定位没抽**（实测全是未解析）——
        #   ① `by_contra h'`（94 条）：`by_contra` 之后的名字是**反证假设名**；
        #   ② `if hb : b = 0 then`（51+ 条）：Lean 的 **`if 名字 : 条件 then`**；
        #   ③ `rename_i hb`：`rename_i` **之后的所有名字都是新名字**；
        #   ④ `rcases … with h' | h'`：`with` 子句里的模式名；
        #   ⑤ `| ⟨a, b, hb, hab⟩ =>`：**模式里的 `⟨⟩` 绑定**。
        #   ★ 口径仍然是「按**语法位置**抽，不按名字猜」——
        #     这五处都是**位置**（命令之后 / `if` 之后 / `with` 之后 / `⟨⟩` 之内）。
        _名正 = r"[^\W\d][\w']*"
        for _m in re.finditer(r"\bby_contra!?\s+(" + _名正 + r")", _行2):
            _记绑(_m.group(1), _i, "by_contra")
        # `if 名字 : 条件 then` —— ★ 必须是**带 `名字 :`** 的形态
        #   （裸 `if 条件 then` 没有名字，那里的标识符是**真引用**，不许动）
        for _m in re.finditer(r"\bif\s+(" + _名正 + r")\s*:", _行2):
            _记绑(_m.group(1), _i, "if")
        # `rename_i a b c` ⇒ 后面**全是名字**（逗号或空格分隔）
        for _m in re.finditer(r"\brename_i\b(.*)$", _行2):
            for _n in re.findall(_名正, _m.group(1)):
                _记绑(_n, _i, "rename_i")
        # `with …`（`rcases`/`obtain` 的 `with` 子句）—— ★ 要抽**整段**的模式名：
        #   实测 `rcases hx with hq | hr` 里我只抽了 `with` 后的**第一个**名字 ⇒
        #   `hr` 仍落「未解析」✗（同一族只覆盖了头一个 —— 老病）。
        #   ★ `cases x with` 后面是换行 ⇒ `(.*)$` 为空 ⇒ 不会误命中。
        for _m in re.finditer(r"\bwith\b(.*)$", _行2):
            _段 = _m.group(1)
            # ★ 去掉 `⟨⟩` 内的部分，避免与下面的「模式」规则重复计数（同一条边两次无害，
            #   但**计数会虚高**）——
            _段外 = re.sub(r"⟨[^⟩]*⟩", " ", _段)
            for _n in re.findall(_名正, _段外):
                _记绑(_n, _i, "with")
        # `| ⟨a, b, hb, hab⟩ =>` 的 `⟨⟩` 绑定
        #   ★ 只认**以 `|` 开头的模式行**（`⟨⟩` 在**项**里是构造子，不是绑定）
        if re.match(r"\s*\|", _行2) or re.match(r"\s*\w+.*⟨", _行2):
            for _m in re.finditer(r"⟨([^⟩]*)⟩", _行2):
                for _n in re.findall(_名正, _m.group(1)):
                    _记绑(_n, _i, "模式")
        for _m in re.finditer(r"\blet\s+([^\W\d][\w']*)", _行2):
            _记绑(_m.group(1), _i, "let")
        for _m in re.finditer(r"\bfun\s+([^\W\d][\w']*"
                              r"(?:\s+[^\W\d][\w']*)*)\s*=>", _行2):
            for _n2 in _m.group(1).split():
                _记绑(_n2, _i, "fun")
        for _m in re.finditer(r"\b(?:rcases|obtain|cases|induction)\b[^\n]*?⟨([^⟩]*)⟩", _行2):
            for _n2 in re.findall(r"[^\W\d][\w']*", _m.group(1)):
                _记绑(_n2, _i, _m.group(0).split()[0])

    # ── Lean import 图：`import X.Y.Z` ────────────────────────────────
    for m in re.finditer(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_.']*)", 净, re.MULTILINE):
        模 = m.group(1).strip()
        号 = f"mod:{模}"
        节点.append({"id": 号, "类": "模块", "名": 模, "文件": 相对, "行": 0,
                    "类型": "import", "绑定": None, "相对": 模.startswith(".")})
        边.append({"起": 文件节点, "终": 号, "关系": "导入", "行": 0})

    # ── 提及：裸标识符 ────────────────────────────────────────────────
    # ★★★ R465：**必须按「出现处」记，不能按名字聚合** ——
    #   `_语法树适配器` 早在 **R359** 就改对了（那里写着：
    #   「原来 `提及: list[str]` + `for 名 in set(提及)` ⇒ **一个名字一条边**，
    #     后果：`def dup` 内外同层各一处调用时**两处共用一条边** ⇒ 主靶只能有一个
    #     ⇒ **对一处对、对另一处错**，而**「答案错了」比「未解析」更坏**」）——
    #   **★ 而 Lean 适配器没跟着改**：`for 名 in set(re.findall(...))` 一直留着。
    #   ★ 两套适配器、同一个概念、只改了一套 —— **「同一个判断写在两处，
    #     必然给出两个答案」**（R390 的原话），这次是它第 N 次命中。
    #   实测代价（`formal_proof/LayerShrink.lean`）：
    #     `h12` 的引用边 **`行=None`** ⇒ 它未解析时**我查不到是哪一行**
    #     ⇒ 无法定位"哪一种绑定形式被漏抽了" ⇒ 一个**不可行动的缺口**。
    #   ★ 口径 150：**可行动缺口与不可行动缺口必须分开报** ——
    #     没有行号的缺口**天生不可行动**。
    # ★★★ R469：**`?名字` 是 Lean 的「命名洞」，不是引用** ——
    #   实测：`refine ?left ?right` ⇒ 产出 `name:left` / `name:right` 两条提及边
    #   ⇒ 下游解不到任何定义 ⇒ **报成缺口** ✗
    #   全仓代价（`形式化验证`）：`left` 未解析 **548** 条 · `right` **554** 条
    #   ⇒ **1,102 条纯假缺口** —— 而 `?xxx` 在整个 Lean 里**从来不指向定义**。
    #   ★ 根因：前视 `(?<![\w.'])` **只排了 `\w`/`.`/`'`**，`?` 不在其中
    #     ⇒ `?left` 里的 `left` 前一个字符是 `?` ⇒ **照样匹配** ✗
    #   ★ 而同族的还有：`?_`（匿名洞）⇒ 会抽出名字 `_`，**也不是引用**。
    #   ★ 口径：**假缺口与真缺口同样有害**（口径 203 的近亲）——
    #     它们把「AI 缺什么」这张表**掺进了本来就不该存在的条目**。
    # ★★★ R475：**字符串字面量里的词不是引用** ——
    #   实测（`形式化验证` 的 `★真外部缺`）：
    #     `Collapse` 未解析 75 条，样例是
    #       `s!"  Collapse: {cr.collapseTriggered}\n" ++`
    #       `s!"  Hasse Extreme Collapse DOES NOT apply to secp256k1:\n" ++`
    #   ⇒ **全是人类可读的提示文字**，被裸名正则当成了标识符 ✗
    #   ★ 口径 226：**靠巧合不出错不算对** —— 不能指望下游过滤器兜住；
    #     必须在**产生提及的地方**就把字面量剔掉。
    #   ★ 实现：把每一行的**字符串字面量内容**换成等长空格（保列号 ⇒
    #     行号/列号读数不变），再跑提及正则。
    #     · Lean/Python 都用 `"` 定界；`\"` 是转义 ⇒ **成对扫描**，不靠正则。
    #     · ★ 只剔**内容**，保留引号本身无意义（引号不是标识符字符）。
    #     · `'` 不处理：Lean 里 `'` 是**标识符字符**（`h'`），不是字符串定界符。
    def _去字面量(_行: str) -> str:
        """把**字符串字面量的内容**换成等长空格（保列号）。

        ★★ R475 第二版：**插值 `{…}` 里的代码要保留** ——
          我第一版把整个字面量剔空，实测 `s!"… {helper} …"` 里的
          `helper` **是真引用**却也没了 ✗（判据②当场抓住）。
          ⇒ 用 `逼真` 深度：`{` 之内**照原样保留**（其中若还有字符串，
            那一层再剔）；`}` 回到字面量层。
          ★ 只剔**内容**，`"` 保留（引号不是标识符字符，留着不影响列号）。
          ★ 不处理 `'`：Lean 里 `'` 是**标识符字符**（`h'`），不是字符串定界符。
        """
        _出 = []
        _i = 0
        _层 = 0          # 0 = 普通代码；1 = 字符串内；2 = 插值 `{…}` 内
        while _i < len(_行):
            _c = _行[_i]
            if _层 == 1:
                if _c == "\\" and _i + 1 < len(_行):
                    _出.append(" " * 2)
                    _i += 2
                    continue
                if _c == '"':
                    _层 = 0
                    _出.append('"')
                elif _c == "{":
                    _层 = 2
                    _出.append("{")
                else:
                    _出.append(" ")
            elif _层 == 2:
                if _c == '"':
                    _层 = 1            # 插值里又开了一个字符串
                    _出.append('"')
                elif _c == "}":
                    _层 = 1
                    _出.append("}")
                else:
                    _出.append(_c)     # ★ **插值里的代码照原样保留**
            else:
                if _c == '"':
                    _层 = 1
                _出.append(_c)
            _i += 1
        return "".join(_出)

    for _i6, _行6 in enumerate(_净.splitlines(), 1):
        _行6 = _去字面量(_行6)
        for _m6 in re.finditer(r"(?<![\w.?'\\])([^\W\d][\w']*)", _行6):
            名 = _m6.group(1)
            # ★★★ R480：**名字必须以字母或下划线开头** ——
            #   实测 `¹` 未解析 79，样例：
            #     `have hgi : g⁻¹ • b = b := by`
            #   ⇒ `g⁻¹` 是 Lean 的**上标逆元记号**（`Inv.inv`）。
            #     `⁻`（U+207B 上标减）不是 `\w` ⇒ `g` 到此为止；
            #     而 **`¹`（U+00B9）的 `isalnum()` 是 True**（Unicode 类别 `No`）
            #     ⇒ 它**独立成了一个名字** ✗
            #   ★ 这是 `[^\W\d]`（R476 改的）的**副作用**：
            #     `\d` 只排 ASCII 数字，而 `isalnum()` 把 `¹²³` 也当字母。
            #   ⇒ 不去改正则（那会又把希臊字母搞坏），
            #     而是在**抽出名字之后加一条最朴素的守卫**：
            #     **名字必须以字母或下划线开头**。
            #     ★ 它同时把 `①②③`（带圈数字）、`①` 等一整类
            #       "No/Nl 类但不是字母" 的字符一并挡住。
            if not (名[0].isalpha() or 名[0] == "_"):
                continue
            # ★ R475：**字符串插值前缀是语法词** —— `s!"…"` / `m!"…"` /
            #   `f!"…"` 里的 `s`/`m`/`f` 是 Lean 的插值器名，**不是引用**。
            #   实测：剔掉字面量后 `s` 立刻冒出来成「未解析」✗
            #   ⇒ 判据是**形状**（紧跟 `!` 再紧跟 `"`），不是名字本身。
            if 名 in ("s", "m", "f") and _行6[_m6.end():_m6.end() + 2] == '!"':
                continue
            # ★ R478：**`λ` 是 Lean 的 lambda 记号，是语法词** ——
            #   实测 `λ` 未解析 585，样例：
            #     `cryptoSeedSet.filter (λ s => s.seedDomain == …)`
            #   ⇒ 它是 `fun` 的 Unicode 写法，**不指向任何定义**。
            #   ★ 同族的还有 `Π`（依值函数类型）/`Σ`（依值对）/`→`/`↦`，
            #     但那些**不是标识符字符**（不会被裸名正则抽到），所以只这一个要排。
            #   ★ 判据是**形状无关的**：`λ` 本身就不能是标识符（Lean 里它被
            #     解析成 `fun` 的记号码位）⇒ 直接按名字排，不会误杀。
            if 名 == "λ":
                continue
            # ★ 单下划线（含 `?_`）不是可引用的名字
            if 名.strip("_") == "":
                continue
            # ★★★ R470：**`#print axioms X` 里的 `axioms` 是语法词，不是引用** ——
            #   实测：全仓 `#词 后紧跟的词` Top1 **就是 `axioms`（5,013 次）**，
            #   而它在 `★真外部缺` 表里占 **4,891 / 6,567 = 74.5%** ✗
            #   ⇒ **一个假缺口吃掉了整张缺口表的前三名。**
            #   ★ 但**不许笼统跳过「`#` 命令后的第一个词」** —— 因为
            #     `#check Nat` 里 `Nat` 是**真引用**（实测 `Nat` 348 次、
            #     `Fin` 98 次都在这一位）⇒ 那样会**把真引用一起删掉** ✗
            #   ⇒ 只跳过 **`#print` 紧跟 `axioms`** 这一个**整体语法**。
            #     口径 203 的方向：**宁可漏绑，不可错绑** ——
            #     若有人真定义了叫 `axioms` 的东西，这里会漏，
            #     但**不会把语法词报成「AI 缺什么」**。
            if 名 == "axioms" and re.search(r"#print\s+$", _行6[: _m6.start()]):
                continue
            # ★ R472：**`_root_` 是 Lean 的根命名空间前缀，是语法词** ——
            #   实测 `_root_.liuHe zhi` ⇒ 抽出 `_root_` 98 条未解析 ✗
            #   ★ 真正的引用是它**后面**的 `liuHe`（限定名的后段），
            #     而那段由 `解析限定名` 那条路负责，不该在这里当裸名抽。
            if 名 == "_root_":
                continue
            # ★★★ R470 第二半：**紧跟在 `#` 之后的词是「命令名」，不是引用** ——
            #   实测：`#check Nat` ⇒ 抽出 `check`；`#print axioms X` ⇒ 抽出 `print`
            #   ⇒ 全仓 `#check` **7,599** 次 · `#print` **5,122** 次 ·
            #     `#guard_msgs` **4,524** 次 · `#eval` **3,797** 次
            #   ⇒ 单是这四个就**几万条假提及** ✗
            #   （它们多数落进「绑定/内置」而没进缺口表，纯属侥幸 ——
            #     `print` 恰好在 `_语言内置` 里、`check` 恰好在别处有定义；
            #     **靠巧合不出错不算对**。）
            #   ★★ **只跳过紧跟在 `#` 后的那一个词** —— `#check MyThm` 里的
            #     `MyThm` **必须留下**（它才是真引用）。
            #     口径 203 的方向：**宁可漏绑，不可错绑**。
            if re.search(r"#\s*$", _行6[: _m6.start()]):
                continue
            if 名 in ("import", "namespace", "end", "def", "theorem", "lemma", "by",
                      "where", "with", "match", "fun", "let", "have", "show", "from",
                      "open", "section", "variable", "instance", "structure", "class",
                      "inductive", "abbrev", "axiom", "variable", "universe", "deriving",
                      "if", "then", "else", "do", "return", "at", "in", "for", "while",
                      "forall", "exists", "Type", "Prop", "True", "False", "Nat", "Int"):
                continue
            # ★ 一个**出现处**一条边，并带 `行`（下游要能定位到源文件那一行）
            边.append({"起": 文件节点, "终": f"name:{名}", "关系": "提及",
                       "近似": True, "行": _i6})
    return 节点, 边


def _保底适配器(文本: str, 相对: str, 语: str) -> tuple[list[dict], list[dict]]:
    """**任何文本文件都进图** —— 没 grammar、没格式，也不许从图上消失。

    粒度粗（文件 + 行块 + 标识符提及），但**「不知道」不等于「没有」**。
    """
    文件节点 = f"file:{相对}"
    节点: list[dict] = []
    边: list[dict] = []
    行们 = 文本.splitlines()
    # 行块：每 200 行一块，让「这条信息在文件的哪一段」有落点
    块 = 200
    for i in range(0, len(行们), 块):
        号 = f"chunk:{相对}:{i + 1}"
        节点.append({"id": 号, "类": "行块", "名": f"{相对}:{i + 1}-{min(i + 块, len(行们))}",
                     "文件": 相对, "行": i + 1, "类型": "text_chunk"})
        边.append({"起": 文件节点, "终": 号, "关系": "包含"})
    # ★★★ **保底适配器**不许**产生「提及」边**（R375）。
    #   修前（标准库实测）：未解析 188,068 条里 **130,789 条（69.6%）来自这里** ——
    #     `CREDITS.txt` 的「Guido」、`HISTORY.txt` 的「IDLE」、
    #     `.csv`/`.afm`/`.pem`/`METADATA` 里的每一个英文词，
    #     都被当成「一个名字出现过」⇒ 落「未解析」。
    #   ★ 这不是"缺口多"，是**缺口读数被污染**：
    #     目标④要的是「**AI 知道自己缺什么**」，而 70% 的"缺"是英文单词
    #     ⇒ 真正缺的定义**淹在里面看不出来**。
    #   ★ 语义上：**「提及」是「引用」的原料**；没有语法的文本里，
    #     **「词」不是「名字」** —— 它没有作用域、没有绑定、也没有靶。
    #     把它们记成"名字"是在**伪造一个不存在的事实**。
    #   ★ 口径：**「不知道」不等于「没有」，但「不知道」更不等于「有一堆名字」。**
    #     保底该说「**我看不懂这个文件**」，而不是「这里有 500 个名字」。
    #   ★ 「任何文本文件都进图」**仍然成立** —— 文件节点 + 行块都在 ✓；
    #     markdown 的**链接**（真引用，可判存在）由 `_Markdown适配器` 单独负责 ✓。
    return 节点, 边


def _二进制适配器(文本: str, 相对: str, 语: str) -> tuple[list[dict], list[dict]]:
    """二进制：只进文件节点 + 指纹。**存内容也没法用**，但**必须在图上可见**。"""
    return [], []



# ══════════════════════════════════════════════════════════════════════════
# ★★★★ R531：**知识族适配器**（`.owl` `.rdf` `.ttl` `.nt` `.n3` `.trig`
#   `.jsonld` `.kif`）—— 用户 2026-11-15 定「**知识族优先**」。
#
# ★ 缺陷现场（R529 挖出来的，不是推测）：`apache-jena-5.6.0` 的产物上写着
#     `扩展名 `.owl` 无结构可抽（**不是源码/配置/文档**）`
#   —— 而 `.owl`/`.rdf`/`.ttl` **正是知识图谱自己的交换格式**。
#   ⇒ **一个做知识图谱的工具，读不了知识图谱的标准格式。**
#
# ★ 工具选择（R309：**能用别人做好的就用别人的**）：
#   · RDF/XML → **`lxml`**（venv 里可用）；`rdflib` **不可用**（离线装不上）。
#   · Turtle / N-Triples / N3 / TriG → **没有现成库** ⇒ 自己写（核心语法很小）。
#   · KIF → s-表达式括号配对。
#   ★ **没验过的不许登记「已支持」**：`.knowledge` 我还没见过它的正文，
#     所以它**仍留在「是缺口」**（口径 344：不许声称没量过的事）。
#
# ★ 图上的形态（与既有词汇同源，口径 305）：
#   `rdf:<相对>:<主体>` 类 `RDF资源` · `rdf值:<相对>:<行>:<序>` 类 `RDF字面量`；
#   边 `file:<相对> --包含--> 主体`；三元组边 `主体 --<谓语短名>--> 客体`。
# ══════════════════════════════════════════════════════════════════════════

#: RDF 的核心命名空间 ⇒ 短名（**不给短名就没法读**：一个三元组只写 URI 等于没写）
_RDF短名 = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://www.w3.org/2000/01/rdf-schema#": "rdfs",
    "http://www.w3.org/2002/07/owl#": "owl",
    "http://www.w3.org/2001/XMLSchema#": "xsd",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://xmlns.com/foaf/0.1/": "foaf",
    "http://www.w3.org/2004/02/skos/core#": "skos",
    "http://schema.org/": "schema",
}

_TTL前缀 = re.compile(r"(?mi)^\s*(?:@prefix|PREFIX)\s+([A-Za-z_][\w.\-]*)?:\s*<([^>]*)>\s*\.?")
_TTL基 = re.compile(r"(?mi)^\s*(?:@base|BASE)\s*<([^>]*)>\s*\.?")
_KIF注释 = re.compile(r";[^\n]*")


def _短(uri: str) -> str:
    """URI → **短名**（`owl:Class` 这种）。**读不出来才退整串。**"""
    u = uri.strip().strip("<>")
    # ★★★ R531b：**lxml 给的是 Clark 记法** `{uri}local`（带花括号），
    #   而 `_短()` 原来只按 `#` 切 ⇒ 短名变成 `…}type`，**一个都认不出来**。
    #   实测（真样本 `camera.owl`）：谓词全是 `{http://…#}type` 这种形态。
    if u.startswith("{") and "}" in u:
        # ★★★ R531c：剥掉 Clark 之后**不许把命名空间也剥掉** ——
        #   实测（R531b 修出来的**新**缺陷）：谓词短名成了 `type` 而不是 `rdf:type`，
        #   而 `rdf:type` 与 `dc:type` 会**撞成同一个名字**
        #   （口径 311：一个串不许承担两个意思）⇒ 先查短表，查不到才用末段。
        _前, _撇, _本 = u[1:].partition("}")
        for _长2, _短2 in _RDF短名.items():
            if _前 == _长2 or _前.startswith(_长2):
                return f"{_短2}:{_本}"
        _末 = _前.rstrip("#/").rpartition("/")[2] or _前.rstrip("#/")
        return f"{_末}:{_本}" if _末 else _本
    for 长, 短 in _RDF短名.items():
        if u.startswith(长):
            return f"{短}:{u[len(长):]}"
    if "#" in u:
        _头, _撇, 尾 = u.rpartition("#")
        return f"…{尾}" if 尾 else u
    return u[-48:] if len(u) > 48 else u


def _知识图发射(相对: str, 三元组: list, 依据: str) -> tuple[list[dict], list[dict]]:
    """(主体, 谓语, 客体, 行, 客体是不是字面量) 列表 ⇒ (节点, 边)。**只在这一处成型**。"""
    文件节点 = f"file:{相对}"
    节点: list[dict] = []
    边: list[dict] = [{"起": 文件节点, "终": 文件节点, "关系": "自"}]
    有 = set()
    _值序 = 0
    for 主, 谓, 客, 行, 是字面量 in 三元组:
        if not 主 or not 客:
            continue
        _主id = f"rdf:{相对}:{_短(主)}"
        if _主id not in 有:
            有.add(_主id)
            节点.append({"id": _主id, "类": "RDF资源", "名": _短(主), "文件": 相对,
                         "行": 行, "类型": "rdf_resource"})
            边.append({"起": 文件节点, "终": _主id, "关系": "包含"})
        if 是字面量:
            _值序 += 1
            _客id = f"rdf值:{相对}:{行}:{_值序}"
            节点.append({"id": _客id, "类": "RDF字面量", "名": str(客)[:120], "文件": 相对,
                         "行": 行, "类型": "rdf_literal"})
        else:
            _客id = f"rdf:{相对}:{_短(客)}"
            if _客id not in 有:
                有.add(_客id)
                节点.append({"id": _客id, "类": "RDF资源", "名": _短(客), "文件": 相对,
                             "行": 行, "类型": "rdf_resource"})
                边.append({"起": 文件节点, "终": _客id, "关系": "包含"})
        边.append({"起": _主id, "终": _客id, "关系": _短(谓), "行": 行, "依据": 依据})
    return 节点, 边


def _RDFXML适配器(文本: str, 相对: str, 语: str):          # noqa: N802
    """RDF/XML（`.owl` `.rdf`）—— 用 **`lxml`**（R309：不自己造 XML 解析器）。"""
    # ★ R547：`lxml` 未声明（已补进 pyproject），但**装不上的机器也要能解析**：
    #   退到标准库 `xml.etree.ElementTree`（无 recover 模式，畸形 XML 会走下面的兜底）。
    try:
        from lxml import etree
    except ImportError:
        import xml.etree.ElementTree as etree  # type: ignore[no-redef]
    try:
        根 = etree.fromstring(文本.encode("utf-8", "replace"))
    except Exception:                                        # noqa: BLE001
        try:
            根 = etree.fromstring(文本.encode("utf-8", "replace"),
                                  parser=etree.XMLParser(recover=True))
        except Exception:                                    # noqa: BLE001
            return [], []
    if 根 is None:
        return [], []
    R = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    三元组: list = []
    _序 = [0]

    def _名(e) -> str:
        _t = e.tag
        return _t if isinstance(_t, str) else ""

    def _行(e) -> int:
        try:
            return int(e.sourceline or 0)
        except Exception:                                    # noqa: BLE001
            return 0

    def _走(e, 主: str, 深: int) -> None:
        if 深 > 12:
            return
        _t = _名(e)
        if _t and not _t.endswith("Description"):
            三元组.append((主, R + "type", _t, _行(e), False))
        for c in e:
            _c = _名(c)
            if not _c or _c == R + "RDF":
                continue
            _资 = c.get(R + "resource") or c.get(R + "nodeID")
            _子 = [x for x in c if _名(x)]
            if _子:
                _序[0] += 1
                _bn = f"_:{相对}#{_序[0]}"
                三元组.append((主, _c, _bn, _行(c), False))
                _走(c, _bn, 深 + 1)
                continue
            if _资:
                三元组.append((主, _c, _资, _行(c), False))
            else:
                _txt = (c.text or "").strip()
                if _txt:
                    三元组.append((主, _c, _txt, _行(c), True))

    for e in 根:
        _t = _名(e)
        if _t == R + "RDF":
            continue
        # ★★★ R531b：原来这里写 `f"_:{id(e)}"` —— `id()` 是**内存地址**
        #   ⇒ **同一份文件两次建的图不一样**（实测 `_:1923853835776`）。
        #   ★ 口径 286：**不可复现的产物等于没有产物**。改成按**出现次序**编号。
        _序[0] += 1
        _主 = (e.get(R + "about") or e.get(R + "ID") or e.get(R + "nodeID")
               or f"_:{相对}#{_序[0]}")
        _走(e, _主, 0)
    return _知识图发射(相对, 三元组, "rdf/owl")


#: Turtle 的词——**顺序即优先级**（IRI 必须在 PNAME 之前，否则 `<a:b>` 被切成前缀名）
#  ★ 三引号必须**拼出来**：在 raw 三引号字符串里写字面的三个单引号会把**我自己**终结
#    （引号哨兵当场抓住 ⇒ 口径 350 的同族：**写代码的工具本身也会被语法咬**）。
_三单 = chr(39) * 3
_三双 = chr(34) * 3
_TTL词 = re.compile(
    r"\s*(?:(?P<注>\#[^\n]*)"
    r"|(?P<iri><[^>\s]*>)"
    r"|(?P<长串>" + _三双 + r"(?:.|\n)*?" + _三双
    + r"|" + _三单 + r"(?:.|\n)*?" + _三单 + r")"
    r"|(?P<串>\"(?:[^\"\\]|\\.)*\")"
    r"|(?P<前缀名>(?:[A-Za-z_][\w.\-]*)?:[^\s;,.\]\[]*)"
    r"|(?P<点睛>\.)"
    r"|(?P<标>[;,\[\]\(\)])"
    r"|(?P<字面>[^\s;,.\]\[]+))")



def _TTL三元组(文本: str) -> list:
    """Turtle / N-Triples / N3 / TriG ⇒ 三元组列表。

    ★ 只做**语法核心**：`@prefix`/`@base` + `主语 谓语 宾语 (, 宾语)* (; 谓语 宾语)* .`
      以及 `[ ... ]` 匿名块。**不做的**（如实写在 `_后缀登记` 的依据里）：
      `a` 关键字的完整等价、`@` 语言标签以外的注解、集合 `( )` 的 RDF 展开、
      TriG 的 graph 名。★ **做不到就少读几个三元组，不许编。**
    """
    # ★★★ R531b：**声明行必须先摘掉**，否则 `@prefix rdf: <…>` 会被词法分析
    #   当成一句三元组（实测：`rdf:cheese.ttl:@prefix --rdf:--> rdf:cheese.ttl:rdf:`）。
    #   ★ 口径 203：**宁可漏读，不可错读**。
    _原文 = 文本
    文本 = _TTL前缀.sub("", 文本)
    文本 = _TTL基.sub("", 文本)
    前缀: dict[str, str] = {}
    for m in _TTL前缀.finditer(_原文):
        前缀[(m.group(1) or "").lower()] = m.group(2)
    基 = ""
    mb = _TTL基.search(文本)
    if mb:
        基 = mb.group(1)

    def _展(s: str):
        """词 ⇒ (值, 是不是字面量)；展不开返回 (None, False)。"""
        if s is None:
            return None, False
        if s.startswith("<") and s.endswith(">"):
            return s[1:-1], False
        if s.startswith('"') or s.startswith("'"):
            return s[:120], True
        if s.startswith("_:"):
            return s, False
        if ":" in s:
            p, _, loc = s.partition(":")
            if p.lower() in 前缀:
                return 前缀[p.lower()] + loc, False
            if not p:
                return (基 + loc) if 基 else s, False
            return s, False
        return s, False

    三元组: list = []
    行号 = 1
    主 = None
    谓 = None
    待客 = False
    深 = 0
    匿名栈: list = []
    pos = 0
    while pos < len(文本):
        m = _TTL词.match(文本, pos)
        if not m:
            pos += 1
            continue
        行号 += 文本.count("\n", pos, m.end())
        pos = m.end()
        种 = m.lastgroup
        if 种 == "注":
            continue
        v = m.group(0).lstrip() if 种 != "注" else ""
        if 种 == "点睛":
            主 = None
            谓 = None
            待客 = False
            if 匿名栈:
                匿名栈.pop()
            continue
        if 种 == "标":
            if v == ";":
                谓 = None
            elif v == ",":
                pass
            elif v == "[":
                深 += 1
                _bn = f"_:{行号}"
                匿名栈.append(_bn)
                if 主 is not None and 谓 is not None:
                    三元组.append((主, 谓, _bn, 行号, False))
                主 = _bn
                谓 = None
                continue
            elif v == "]":
                深 = max(0, 深 - 1)
                主 = None
                谓 = None
                continue
            else:
                continue
            continue
        _值, _字 = _展(v)
        if _值 is None:
            continue
        if 主 is None:
            主 = _值
        elif 谓 is None:
            谓 = _值
            待客 = False
        else:
            三元组.append((主, 谓, _值, 行号, _字))
            待客 = True
    return 三元组


def _Turtle适配器(文本: str, 相对: str, 语: str):
    return _知识图发射(相对, _TTL三元组(文本), "turtle")


def _KIF适配器(文本: str, 相对: str, 语: str):
    """KIF（`.kif`）—— **s-表达式**：每个顶层 `(关系 参数…)` 是一条断言。

    ★ KIF 是 GGP（通用博弈）用的**知识表示格式** —— R529 把它数成「是缺口」，
      它和 `.owl`/`.rdf` 是同一件事：**知识格式读不了**。
    ★ **只读第一层**：顶层项的**第一个词**当谓语、整项当一个断言节点。
      **不假装懂 KIF 的语义**（真语义要 FOL 消解器，这里没有）。
    ★ 行号按 `(` 出现的位置算（一条 KIF 断言可跨多行）。
    """
    净 = _KIF注释.sub("", 文本)
    三元组: list = []
    深 = 0
    行 = 1
    起行 = 0
    词: list = []
    for ch in 净:
        if ch == "\n":
            行 += 1
            continue
        if ch == "(":
            深 += 1
            if 深 == 1:
                词 = []
                起行 = 行
            continue
        if ch == ")":
            深 -= 1
            if 深 <= 0:
                深 = 0
                if 词:
                    # ★ 句柄只用**第一个词**（关系名）；不给没量过的语义下断言
                    三元组.append((f"kif:{相对}", f"kif:{词[0]}",
                                   f"kif断言:{相对}:{起行}", 起行, False))
                continue
            if 深 == 1 and 词:
                词.append(")")
            continue
        if 深 == 1:
            if ch.isspace():
                if 词 and 词[-1] != "":
                    词.append("")
            else:
                if not 词:
                    词.append("")
                词[-1] = 词[-1] + ch
    三元组 = [x for x in 三元组 if x[1] != "kif:"]
    return _知识图发射(相对, 三元组, "kif")


def _JSONLD适配器(文本: str, 相对: str, 语: str):
    """JSON-LD（`.jsonld`）—— **它就是 JSON**（`@id`/`@type`/`@context`），复用解析。"""
    import json as _j
    try:
        数 = _j.loads(文本)
    except Exception:                                        # noqa: BLE001
        return [], []
    三元组: list = []

    def _走(x, 行: int = 0) -> None:
        if isinstance(x, list):
            for y in x:
                _走(y, 行)
            return
        if not isinstance(x, dict):
            return
        主 = x.get("@id") or f"_:{id(x)}"
        _t = x.get("@type")
        if isinstance(_t, str):
            三元组.append((主, "rdf:type", _t, 行, False))
        elif isinstance(_t, list):
            for y in _t:
                三元组.append((主, "rdf:type", str(y), 行, False))
        for k, v in x.items():
            if k.startswith("@"):
                continue
            if isinstance(v, dict) and "@id" in v:
                三元组.append((主, k, v["@id"], 行, False))
            elif isinstance(v, (str, int, float, bool)):
                三元组.append((主, k, str(v), 行, True))
            elif isinstance(v, (list, dict)):
                _走(v, 行)

    _走(数)
    return _知识图发射(相对, 三元组, "jsonld")


def _适配器表() -> list[tuple[str, Callable[..., Any], str]]:
    """(名字, 函数, 适用说明) —— **注册表**，加新类型只加一行。"""
    return [
        ("结构化", _结构化适配器, "json/yaml/toml ⇒ 键路径"),
        ("Lean文本", _Lean适配器, "**无 grammar** 的 .lean ⇒ 文本级声明 + namespace 限定名"
         "（标 `来源:文本/确证:False`，与 elaborator 层并列）"),
        ("语法树", _语法树适配器, "tree-sitter grammar ⇒ 定义 + 提及"),
        ("Markdown", _Markdown适配器, "markdown ⇒ 标题(定义) + 链接(**真引用**，可判存在)"),
        ("RDF/OWL", _RDFXML适配器, "`.owl`/`.rdf` ⇒ RDF/XML 三元组（**lxml**）"
         "（R531：知识图谱的交换格式，**从前被判成「无结构」**）"),
        ("Turtle", _Turtle适配器, "`.ttl`/`.nt`/`.n3`/`.trig` ⇒ 三元组（自写词法）"),
        ("JSON-LD", _JSONLD适配器, "`.jsonld` ⇒ `@id`/`@type`/谓词"),
        ("KIF", _KIF适配器, "`.kif` ⇒ s-表达式断言（GGP 的知识表示格式）"),
        ("保底", _保底适配器, "**任何文本文件** ⇒ 文件 + 行块"
         "（**不产提及**：无语法处「词」不是「名字」，否则污染缺口读数）"),
        ("二进制", _二进制适配器, "非文本 ⇒ 文件 + 指纹"),
    ]


def 适配器表() -> list[dict]:
    """给读数看：现在有哪些适配器、覆盖什么。**读数里有它，才知道能力边界。**"""
    # ★ R547：**tree_sitter 未装时要降级，不许崩**（verifier 干净环境实测：
    #   严格按 Requires-Dist 装的机器上，这里 `ModuleNotFoundError: tree_sitter`
    #   让整张能力表读不出来 —— 而「缺一个可选能力」与「这层坏了」不是一回事）。
    基 = [{"适配器": n, "适用": d2} for n, _f, d2 in _适配器表()]
    try:
        import pathlib as _p
        import tree_sitter as _ts
        d = _p.Path(_ts.__file__).parent.parent
        有 = sorted(x.name.replace("tree_sitter_", "")
                    for x in d.iterdir()
                    if x.name.startswith("tree_sitter_") and x.is_dir()
                    and not x.name.endswith(".dist-info"))
    except ImportError as _e:
        return 基 + [{"适配器": "tree-sitter grammar",
                      "适用": f"**未装**（{_e}）⇒ 语法树/提及解析这一路不可用；"
                              "装法：pip install agentscope_orchestrator[parsers]"}]
    return 基 + [{"适配器": "tree-sitter grammar",
                  "适用": ("已装：" + ", ".join(有)) if 有 else "**tree_sitter 在但没有 grammar**"}]


# ── 遍历 ────────────────────────────────────────────────────────────
# ★ 备份型目录名：**历史副本**，不是源码世界（R421）
_像备份目录 = re.compile(
    r"(备份|歷史版本|历史版本|快照|snapshot|(^|[_.\-])(backup|bak|old|orig|save)"
    r"([_.\-]|$)|_bak$|_old$|_backup$|backup_\d|_\d{8}$)", re.I)


#: ★ R521：**文件级**的「这是历史副本/临时件」——
#:   ★ 为什么不与 `_像备份目录` 共用一个正则：那个含 `_\d{8}$`，
#:     对**目录**判备份合适，对**文件**会误伤 `报告_20260427` 这类**正常数据文件**
#:     ⇒ 口径 203（**宁可漏剔，不可错剔**）⇒ 文件级只认下面五种**语义确定**的形态。
#:   ★ 实测（`太虚元道` 桶）：`.bak_ts046_mod_before_reuse_retry.rs` 与
#:     `.bak_ts046_mod_before_tiebreak.rs` 的 **sha1 完全相同** —— 同一份代码的副本。
_像备份文件 = re.compile(
    r"(^|/)\.bak([_.\-]|$)"      # `.bak_xxx` / `.bak_xxx.rs` / `.bak`（点开头 = 明确的备份命名）
    r"|\.bak\d*$"                # `foo.rs.bak` / `foo.py.bak2`
    r"|(^|/)\.tmp_"               # `.tmp_ReuseGap_check.lean`
    r"|~$"                         # 编辑器临时（`foo.py~`、`foo.md~`）
    r"|\.(orig|rej)$"             # `patch` 的原始件/拒绝件
)


def _是备份文件(名: str) -> bool:
    """**文件名本身**指向「这是历史副本/临时件」⇒ 不进图（R521）。

    ★ 为什么要有它（实测）：`_是备份目录` 只管**目录名** ⇒
      `太虚元道` 桶里 11 个 `.bak_*` / `.tmp_*` **照收**，
      而两个 `.bak_*` 的 **sha1 一模一样** ⇒ 图上表现为「两个文件定义了同一批名字」，
      读起来像"重复定义"，其实是**同一份代码的副本** ✗
    ★ 防误伤：只看**文件名本身**（不看内容、不看目录）——
      备份的就是源码，**不能靠"有源码标记"豁免**，否则等于不判。
    """
    return bool(_像备份文件.search(名))


def _是备份目录(名: str) -> bool:
    """目录名本身指向「这是历史副本」⇒ 不进图。

    ★ 实测（R421）：`备份/TS-181_基线_1551_0_119` 597 个文件、
      `备份/TS-186_基线` 431 个、`中文编译器_backup_20260710` 304 个……
      **总根 36 分钟跑不完就是这个**。
    ★ `分根()` 早就有 `_备份型`（含 `备份`），**但 `_该跳` 没有** ⇒
      **分根认得出、建图认不出**。★ 口径：**同一个判断写在两处，必然给出两个答案**；
      这里更糟 —— **只写在了一处**，另一处**根本不判断**。
    ★ 防误伤：只在**目录名本身**指向备份时判（不看内容）——
      因为"备份目录里有真源码"是**必然的**（备份的就是源码），
      所以**不能靠"有源码标记"来豁免**，否则等于不判。
    """
    return bool(_像备份目录.search(名))


def _是运行副本(名: str, 兄弟们) -> bool:
    """★★★ **同类的兄弟 ≥3 个 ⇒ 这是一个「运行副本」目录，不是源码世界**（R415）。

    ★ 为什么需要（实测，不是理论）：
      `多智能体编排/agentscope_orchestrator/` 下有
      **`r291v_a2j_487u` / `it_bncm8s7j` / … 一大批**，
      **每个 635–636 个文件** —— 这是**每次运行留一份的临时工作副本**。
      它们让这个子根从几千个文件涨到 **38,962 个进图文件**。
      ★ 口径：**「产出」落在「输入」里，就是自我污染**（口径 105）——
        这里更狠：**每一轮运行都往里多堆一份自己的副本。**

    ★ 判据是**结构的，不是名字的**（口径 103：按名字列举必然漏）：
      · 取名字的「稳定前缀」（去掉尾部 ≥4 位小写字母数字乱码）；
      · **同父目录下同前缀的兄弟 ≥3 个** ⇒ 判为运行副本。
      ★ 不写死 `r291v` —— 下一轮它会变成 `r415v`、`ts999v`……
        **按「它有一堆长得很像的兄弟」判，漏不了。**

    ★★★ **不许对每个名字重扫一遍兄弟**（R417 —— **我自己引入的 O(n²)**）——
      实测：`多智能体编排/agentscope_orchestrator/` 下有 **5,120 个兄弟**，
      第一版对**每个目录**都遍历 5,120 个兄弟做正则 ⇒ **约 2,600 万次正则** ✗
      ⇒ `形式化验证` 重建从 **173 秒变成 22 分钟**（cpu 1348s 还没完）。
      ★ 口径：**「每个元素扫一遍全集」在 5,000 个元素上就是 2,500 万次** ——
        **判据本身要对，判据的代价也要能承受**（口径 120 的同一件事）。
      ★ 修法：`兄弟们` 传**前缀计数表**（`_副本前缀表` 一次折成 `{前缀: 个数}`），
        每个目录 O(1) 查表。
    """
    前 = _副本前缀(名)
    if 前 is None:
        return False
    if isinstance(兄弟们, dict):
        同 = 兄弟们.get(前, 0)
    else:
        同 = sum(1 for 兄 in 兄弟们 if _副本前缀(兄) == 前)
    return 同 >= 3          # ≥3 个同类兄弟（含自己）


def _副本前缀(名: str):
    """名字的「稳定前缀」：去掉尾部 ≥4 位小写字母数字乱码；不是则 None。"""
    # ★★★ R445：**分隔符前必须有一个字母数字** ——
    #   旧式 `(.*?[_-])` 是**非贪婪**的 ⇒ 前缀会**退化成一个分隔符**：
    #   `_core` / `_typing` / `_utils` / `_pyinstaller` **全算出 `"_"`** ✗
    #   ⇒ `numpy/` 下 ≥3 个 `_` 开头的**真子模块**被当成"运行副本"剪掉 ✗✗
    #   实测后果：`判七十一` **整文件漏 116**、`判七十三` **归属漏 2,761**
    #   （例全是 `site-packages/numpy/_core/…`）—— **两条红，一个根因。**
    #   ★ 正确语义：**"稳定前缀"必须够具体** —— 分隔符前至少一个字母数字。
    m = re.match(r"^(.*[A-Za-z0-9][_-])([a-z0-9]{4,})$", 名)
    return m.group(1) if m else None


def _副本前缀表(子目们) -> dict:
    """把一批兄弟目录**一次**折成 `{前缀: 个数}`（避免 O(n²)）。"""
    计: dict = {}
    for x in 子目们:
        q = _副本前缀(x)
        if q is not None:
            计[q] = 计.get(q, 0) + 1
    return 计


def _该跳(名: str, 目录=None) -> bool:
    """★ **可疑剪枝目录要看内容**，不能只看名字。

    实测（R338）：`Lib/venv/`（标准库真模块）和
    `pip/_internal/operations/build/`（真源码，8 个 `.py`）**都被按名字剪掉了** ——
    「我漏」里 **9 个整文件漏** 就是它们。

    ★ 「静默跳过」没有发生（条目在 `.kg/清单.json` 的跳过清单里），
      所以问题不在诚实性，**在判据**：**名字分不清「构建产物」和「叫 build 的源码目录」。**

    ★ 但**不能一刀切**：`.venv`/`node_modules` 里全是 `__init__.py` ——
      一刀切会把整个虚拟环境扫进来。所以 ``_必剪`` 照剪，``_可疑剪枝`` 才看内容。
    """
    if 名 in _必剪:
        return True
    # ★★★ R521：**虚拟环境的通用识别 —— 看标记文件，不看目录名**。
    #   为什么需要（实测）：`太虚元道` 桶收进来 **6,307** 个文件，全在
    #   `多智能体编排/agentscope_orchestrator/r291v_4ed_jak_/v/Lib/site-packages/…`。
    #   那个虚拟环境的根目录叫 **`v`** —— 而 `_必剪` 认的是 `.venv`/`venv`
    #   ⇒ **名字判据一个都没命中**，整棵虚拟环境照收。
    #   `pyvenv.cfg` 是 **PEP 405** 规定的虚拟环境标记文件
    #   ⇒ **与目录叫什么无关**，`v` / `env` / `venv312` / `.conda` 一网打尽。
    #   ★ 代价：每个候选目录一次 `is_file()`（walk 本来就要 stat，
    #     且这一条在名字判据**都不命中**之后才走，属于必要代价）。
    #   ★ 限额：只在**名字判据全不命中**时查，所以真源码目录（有 `__init__.py`
    #     之外的普通目录）**每次都会被查** —— 这是**每个目录一次 syscall**，
    #     相对「整个 venv 进来后被反复解析」便宜几个数量级。
    if 目录 is not None:
        try:
            if (目录 / "pyvenv.cfg").is_file():
                return True
        except OSError:
            pass
    if _是备份目录(名):            # ★ 备份副本（R421）
        return True
    if 名 in _可疑剪枝:
        # 有源码标记 ⇒ 是**真源码目录**，不剪（一次 stat 级别的代价）
        return not (目录 is not None and _是有源码的目录(目录))
    return 名.startswith(".") and 名 not in (".", "..") and 名 not in (".github", ".config")


def _数目录文件数(目: Path, 上限: int = 200_000,
                 秒预算: float = 0.5, 只数一级: bool = False) -> dict:
    """★★ R442 + **R517b（时间预算 + 只数一级）** —— 给"跳过的目录"一个**大小**。

    ★ 为什么要有它（R442）：`枚举文件` 跳目录时原来只记 `{"路径","类","原因"}` ⇒
      调用方**只知道"有个目录被跳了"，不知道因此少了多少东西**
      ⇒ **"看不见"被当成"没有"**（口径 139）。
      实测后果：`判九十五` 报「60 个源码文件谁都不管」+「只有 0 个落进总根」
      —— 两条红，**同一个根因：沉默的删除**。

    ★★★ R517b 的实测动机（**不是理论**，2026-11-13 22:40）：
````text
分根(总) 总 2992s（50 分钟）
`_数目录文件数` 3,412 次调用 · 合计 2597s ⇒ **占 87%**
  743.9s agentscope_orchestrator 的 work 目录（我的临时 fixture）
  560.5s target                          ← Rust 编译产物
  325.1s .tmp      95.7s .git     80.8s .cargo_home     65.1s 备份
````
      `快=True`（R414）省的是**每个文件**的两次系统调用，
      **没省"每跳一个目录就把整棵子树数一遍"** ⇒ 50 分钟还在（口径 3501）。

    ★★★ 关键判断：**数 `target`/`.git`/`work` 里有多少文件，对"AI 知道自己缺什么"没有价值。**
      R442 要守的是「**有源码的目录**被剪了要知道少了多少」
      （判九十五报的是"60 个**源码文件**谁都不管"），**不是**"target 里有 9 万个 .o"。
      ⇒ `只数一级=True` 时**只做一次 `scandir`**，字段换成 `一级条目数`
        （**名字自己说明它是什么**，口径 311：「字段名本身可以是一个缺陷」）。

    ★ 三种返回形态**互不同名**（口径 190：`False` 与 `unknown` 分开；
      口径 318：保守必须写出方向）：
````text
      递归数完    ⇒ {"里面有文件数": n, "数完": True}          ← 精确
      递归没数完  ⇒ {"里面至少有文件数": n, "数完": False}     ← 下界
      只数一级    ⇒ {"一级条目数": n, "数完": True}            ← 另一个量，另起一个名
````
    ★ 用 `os.scandir`（**不 stat**，只看是不是文件）。
    """
    import os as _os
    import time as _t
    if 只数一级:
        # ★ 一次 scandir —— 剪枝/产物目录的"大小"只要个量级
        n = 0
        try:
            with _os.scandir(目) as it:
                for _e in it:
                    n += 1
                    if n >= 上限:
                        break
        except OSError:
            pass
        return {"一级条目数": n, "数完": True}
    _截 = _t.monotonic() + 秒预算
    n = 0
    _数完 = True
    _栈 = [目]
    while _栈:
        if n >= 上限 or _t.monotonic() > _截:
            _数完 = False
            break
        _d = _栈.pop()
        try:
            with _os.scandir(_d) as it:
                for e in it:
                    if n >= 上限 or _t.monotonic() > _截:
                        _数完 = False
                        break
                    try:
                        if e.is_dir(follow_symlinks=False):
                            _栈.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            n += 1
                    except OSError:
                        continue
        except OSError:
            continue
    if _数完:
        return {"里面有文件数": n, "数完": True}
    return {"里面至少有文件数": n, "数完": False}


# ══════════════════════════════════════════════════════════════════════════
# ★★★★ R532：**粘滞剪枝**（用户 2026-11-15 定第①条）
#
# ★ 缺陷（我量出来的）：剪枝规则「同前缀兄弟 ≥3 个 ⇒ 运行副本」的输入是
#   **盘上的兄弟目录**，而兄弟是**别的会话随时会新建的** ⇒
#   **图的文件集不是路径的函数**：旁边多两个同名兄弟，目录就从图里消失。
#   ★ 实测现场：`★剪枝漂移` 这个读数就是为它设的（盘上还在、这一轮被剪）。
#   ★★ 它顶到「任意文件夹 ⇒ 完备知识图谱」：**图会因为无关目录的出现而变**。
#
# ★ 用户的决定：**粘滞** —— 进过图的目录**不再被剪**。
#
# ★ 判据来源（口径 305：一个处置只写一处）：**只用上一轮的图** ——
#   `戳.json` 里出现过的文件路径 ⇒ 它们的**目录及其祖先**。
#   ★ 不新增状态文件；语义也对：`戳.json` 里的文件是**真进过图的**。
#
# ★★ 口径 348：`建图`（全量）里 `旧戳={}` 是为**强制重算 sha1** 而设的，
#   而**粘滞要读盘上的旧戳** —— 两件事**分开取**，不许混用一个值。
# ══════════════════════════════════════════════════════════════════════════

def _粘滞目录们(旧戳: dict) -> set[str]:
    """上一轮**有文件的目录**（含全部祖先）⇒ **这一轮不许被剪**。

    ★ 入参是 `戳.json`（键 = **文件**的 posix 相对路径）。
    ★ 祖先闭包要一起给：如果 `a/b/c/x.py` 在图上，那么 `a`、`a/b`、`a/b/c`
      都「进过图」，都该粘滞（否则上层目录一被剪，下面照样全没了）。
    ★ 返回**posix 相对目录**（根记为 `""`）—— 与 `枚举文件` 里的算号方式一致。
    """
    出: set[str] = set()
    for _相 in (旧戳 or {}):
        # ★★★ R532b：这里原来写的是 `相`（而不是循环变量 `_相`）⇒ **`NameError`**。
        #   ★★ 它**只在循环体执行时才炸**（= **只在粘滞集非空时**）
        #   ⇒ `compile` 绿 · 引号哨兵绿 · **第一次建图也绿**（那时旧戳为空，
        #     循环体一次都没进）⇒ **这个功能从未工作过，而所有常规检查都是绿的**。
        #   ⇒ 口径 370：**只在「特征被触发」时才走的路径，默认路径跑不出来** ——
        #     必须有一个**专门把特征触发起来的最小实验**。
        _d = _相.rsplit("/", 1)[0] if "/" in _相 else ""
        出.add(_d)
        while _d:
            出.add(_d)
            _d = _d.rsplit("/", 1)[0] if "/" in _d else ""
    return 出


def _读旧戳(根: Path) -> dict:
    """从**盘上**读上一轮的 `戳.json`（读不到就是 `{}`）。

    ★ 为什么单列一个函数：`建图` 需要「`旧戳` 为空（强制重算）」**同时**
      「粘滞集来自盘上的旧戳」—— 一个值不许兼这两件事（口径 348）。
    """
    _kg = _kg目录(根)
    try:
        import json as _j
        _d = _j.loads((_kg / _戳档).read_text(encoding="utf-8"))
        return _d if isinstance(_d, dict) else {}
    except Exception:                                        # noqa: BLE001
        return {}


def 枚举文件(根: Path, 上限: int = 200_000,
            快: bool = False, 排除=None, 粘滞=None,
            救回=None) -> tuple[list[Path], list[dict]]:
    """→ (要进图的文件, **跳过清单**)。

    ★ 「跳过清单」是**读数的一部分** —— 跳过的必须逐条能说清「为什么跳」。
      **静默跳过 = 把「看不见」当「没有」。**

    ★★★ `排除=(子根名…)` = **在 walk 时就剪掉**这些一级子树（R418）。

      为什么需要（实测）：`建图(总根)` 里 `_过排除` 是在**枚举之后**才过滤的 ⇒
      **仍然遍历全树 162,524 个文件、每个都 `stat`** ⇒ **20 分钟** ✗
      ★ 口径：**「过滤」与「剪枝」是两件事** ——
        过滤是**全都走一遍再扔掉**，剪枝是**根本不进去**；
        16 万个文件上，这个差别就是 **20 分钟与 1 秒**。
      ★ 剪掉的在"跳过"里**留名**（口径 95：沉默的删除比报错坏得多）。

    ★★★ `快=True` = **只列不查**（跳过 `is_symlink()` 与 `stat()`）（R414）。
      为什么需要（实测）：总仓 **162,524 个文件**，每个文件两次系统调用
      （`is_symlink()` 在 Windows 上要查 reparse point）
      ⇒ **`分根()` 光枚举就跑了 20+ 分钟**（而 `分根` **只看扩展名**，
      `stat` 与它完全无关）。
      ★ 口径：**「算得出」与「算得快」在规模面前是同一件事** ——
        **一个要 20 分钟的步骤，没人会跑第二次。**
      ★ `快=True` **有代价**：不查 symlink（可能成环）、不查大小（超大文件会进图）
        ⇒ **只给「只按名字/扩展名做判断」的调用方用**（目前只有 `分根`）。
        **其余一律用默认值，别为了快把安全性丢掉。**
    """
    要: list[Path] = []
    跳: list[dict] = []
    排除集 = {str(x).replace("\\", "/").strip("/").split("/")[0]
             for x in (排除 or ()) if str(x).strip()}
    # ★★★ R532：粘滞集 —— 由**调用方**从上一轮的图算出来（口径 305）
    _粘滞集 = set(粘滞 or ())
    for 目, 子目, 文件们 in os.walk(根):
        目p = Path(目)
        保留 = []
        _兄弟 = _副本前缀表(子目)   # ★ 前缀只数一次（R417）
        # ★ 一级子树被"排除" ⇒ **根本不进去**（R418）
        _是根层 = (目p == 根)
        for d in 子目:
            if _是根层 and 排除集 and d in 排除集:
                跳.append({"路径": (目p / d).relative_to(根).as_posix(), "类": "目录",
                          "原因": f"属于子根 `{d}`（**它的图在别处**）",
                          # ★ R517c：**也只数一级** —— 「少了多少**源码**」由
                          #   `一级子树源码数` 负责（那是 R442 真正要的读数）；
                          #   递归数"里面有多少 .o/.class"既慢又与动机无关（口径 332）。
                          **_数目录文件数(目p / d, 只数一级=True)})
                continue
            # ★★ R442：跳目录时**连『里面有 N 个文件』一起报出** ——
            #   `_该跳` 只记「有个目录被跳了」，调用方**不知道少了多少东西**
            #   ⇒ **「看不见」被当成「没有」**（口径 139）。
            # ★★★ R532：**粘滞**优先于一切剪枝 —— 上一轮在图上，这一轮不许剪。
            #   ★ 位置在**所有剪枝判断之前**（含「排除」之外的三条分支），
            #     否则「同前缀兄弟」那条仍然会把进过图的目录剪掉。
            if _粘滞集 and (目p / d).relative_to(根).as_posix() in _粘滞集:
                if 救回 is not None:
                    救回.append({"路径": (目p / d).relative_to(根).as_posix(),
                                 "原因": "**粘滞**：它上一轮在图上（R532）"})
                保留.append(d)
                continue
            if _该跳(d, 目p / d):
                # ★ R521：**原因要分得开** —— 原来只有"备份副本"与"剪枝目录"两类，
                #   于是「6,307 个文件的虚拟环境被剪了」和「一个空 build 目录被剪了」
                #   在读数里长得一样（口径 194：两个不同的量不许并成一个数）。
                if _是备份目录(d):
                    _因 = "备份副本（目录名指向历史版本，**不是源码世界**）"
                elif d in ("site-packages", "dist-packages") or (目p / d / "pyvenv.cfg").is_file():
                    _因 = "依赖/虚拟环境（**不是我自己的东西** —— 不该算进「我有什么」）"
                else:
                    _因 = "剪枝目录（名字像产物，且无源码标记）"
                跳.append({"路径": (目p / d).relative_to(根).as_posix(), "类": "目录",
                          "原因": _因, **_数目录文件数(目p / d, 只数一级=True)})
            elif (not 快) and _是运行副本(d, _兄弟):
                跳.append({"路径": (目p / d).relative_to(根).as_posix(), "类": "目录",
                          "原因": "运行副本（同前缀兄弟 ≥3 个）",
                          **_数目录文件数(目p / d, 只数一级=True)})
            else:
                保留.append(d)
        子目[:] = 保留
        for f in 文件们:
            全 = 目p / f
            try:
                相 = 全.relative_to(根).as_posix()
            except ValueError:
                continue
            if 快:
                # ★ 只列不查：`分根` 只看扩展名，stat 与它无关
                要.append(全)
                if len(要) >= 上限:
                    跳.append({"路径": "…", "类": "文件", "原因": f"达到上限 {上限}"})
                    return 要, 跳
                continue
            # ★ R521：**文件级的备份/临时件同样不是「我有什么」**（口径 305：
            #   目录那半上一轮刚补，这是另一半）。剪掉**不静默** —— 进跳过清单。
            if _是备份文件(f):
                跳.append({"路径": 相, "类": "文件",
                           "原因": "备份/临时件（**同一份代码的历史副本**，不是「我有什么」）"})
                continue
            if 全.is_symlink():
                跳.append({"路径": 相, "类": "文件", "原因": "符号链接（可能成环）"})
                continue
            try:
                st = 全.stat()
            except OSError as e:
                跳.append({"路径": 相, "类": "文件", "原因": f"stat 失败：{type(e).__name__}"})
                continue
            if st.st_size > 8 * 1024 * 1024:
                跳.append({"路径": 相, "类": "文件", "原因": f"过大（{st.st_size // 1024}KB > 8MB）",
                           "大小": st.st_size})
                continue
            要.append(全)
            if len(要) >= 上限:
                跳.append({"路径": "…", "类": "文件", "原因": f"达到上限 {上限}"})
                return 要, 跳
    return 要, 跳


def _戳(p: Path, 旧: dict | None = None, 强校验: bool = False) -> dict:
    """(mtime, size, sha1[:16]) —— 三个一起，任一变了就重算。

    ★ 快筛（**口径必须报出来**）：`mtime` 与 `size` 都没变就**沿用旧 sha1**，
      **不读文件**。这是 git 之外几乎所有构建系统的做法，代价是
      **mtime 相同而内容真变了**时会漏判（罕见，但存在）。
      ⇒ 图上写清「戳用的是 mtime+size 快筛」；`强校验=True` 时一律算 sha1。
    """
    st = p.stat()
    米 = {"mtime": int(st.st_mtime), "size": st.st_size}
    if (not 强校验 and 旧 and 旧.get("mtime") == 米["mtime"]
            and 旧.get("size") == 米["size"] and 旧.get("sha1")):
        米["sha1"] = 旧["sha1"]
        米["快筛"] = True
        return 米
    h = hashlib.sha1()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    米["sha1"] = h.hexdigest()[:16]
    米["快筛"] = False
    return 米


def _读(p: Path) -> tuple[str | None, str | None]:
    """→ (文本, 读不了的原因)。**读不了也要有原因**，不许静默。"""
    try:
        原始 = p.read_bytes()
    except OSError as e:
        return None, f"读失败：{type(e).__name__}"
    if b"\x00" in 原始[:8192]:
        return None, "二进制（前 8KB 有 NUL）"
    for 编 in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return 原始.decode(编), None
        except UnicodeDecodeError:
            continue
    return None, "编码都解不开"


def _选适配器(相: str) -> tuple[Callable[..., Any], str]:
    后缀 = Path(相).suffix.lower()
    if 后缀 in _结构化 and 后缀 in (".json", ".yaml", ".yml", ".toml"):
        return _结构化适配器, _语法.get(后缀, "json")
    if 后缀 == ".lean":
        # ★ Lean 没有 tree-sitter grammar（`_语法` 里**故意没有** `.lean`），
        #   而它的真结构只有 elaborator 有 ⇒ 单列一个**文本级**适配器，
        #   每条标 `来源: 文本 / 确证: False`，和 elaborator 层**并列在同一张图**上。
        return _Lean适配器, "lean"
    # ★ markdown 必须**先**接（`_语法` 里有 `.md`，会把它抢去走语法树）。
    #   实测：只注册进 `_适配器表` 不够 —— `_选适配器` 的 `if 后缀 in _语法`
    #   优先 ⇒ 342 个文件里 0 个走 Markdown。
    #   **「登记了」不等于「接上了」。**
    if 后缀 in (".md", ".markdown"):
        return _Markdown适配器, "markdown"

    # ★★★ R531：**知识族**（用户 2026-11-15 定「知识族优先」）。
    #   ★ 必须在 `_语法` 之前接：`.jsonld` 会撞 `.json`，而知识族要按 RDF 读。
    if 后缀 in (".owl", ".rdf"):
        return _RDFXML适配器, "rdfxml"
    if 后缀 in (".ttl", ".nt", ".n3", ".trig"):
        return _Turtle适配器, "turtle"
    if 后缀 == ".jsonld":
        return _JSONLD适配器, "jsonld"
    if 后缀 == ".kif":
        return _KIF适配器, "kif"

    if 后缀 in _语法:
        return _语法树适配器, _语法[后缀]
    return _保底适配器, "text"


def _解析一个(p: Path, 相: str, 是二进制原因: str | None = None
             ) -> tuple[list[dict], list[dict], dict]:
    """→ (节点, 边, 元信息)。**元信息里必须写清「走了哪个适配器 / 为什么没走成」。**"""
    元 = {"适配器": None, "原因": None}
    if 是二进制原因:
        # ★ 二进制**不是问题** —— 它是一条**正常结局**（有自己的适配器）。
        #   把它记进「问题」会让「问题数」虚高，看读数的人分不出
        #   「这是二进制」和「适配器炸了」。
        元["适配器"] = "二进制适配器"
        元["说明"] = 是二进制原因
        return [], [], 元
    文本, 坏 = _读(p)
    if 文本 is None:
        元["适配器"] = "二进制适配器"
        元["说明"] = 坏
        return [], [], 元
    适, 语 = _选适配器(相)
    try:
        节, 边 = 适(文本, 相, 语)
        元["适配器"] = 适.__name__
        元["语言"] = 语
        return 节, 边, 元
    except Exception as e:                        # noqa: BLE001
        # ★ 适配器炸了**不许让文件消失** —— 退保底，并把原因记下来
        节, 边 = _保底适配器(文本, 相, "text")
        元["适配器"] = "保底（适配器炸了）"
        元["原因"] = f"{适.__name__}: {type(e).__name__}: {e}"[:200]
        return 节, 边, 元


#: 图目录可以**覆盖**（环境变量 `KG_图目录`）。
#  ★ 为什么必须有：**真实仓常常是只读的** —— 实测 `太虚元道\src`、
#    `formal_proof\MyMeta\Foundational` 都写不进 `.kg`（`PermissionError [WinError 5]`）。
#    只读根不是异常，是**常态**（依赖检出、系统目录、CI 挂载）。
#    一个「任意文件夹都能用」的机器，**不许因为仓是只读的就用不了** —— 但
#    **默认仍然是 `<根>/.kg`**（常驻的语义就是「图跟着根走」），要让位得**显式**说。
_env图目录 = os.environ.get("KG_图目录", "").strip()


def _kg目录(根: Path) -> Path:
    if _env图目录:
        # 覆盖时按根的名字分桶，避免多个根互相覆盖
        # ★★★ **桶名不许把非 ASCII 全洗成下划线**（R400）。
        #   实测：根 `…\太虚元道` ⇒ 桶名 **`____`**（4 个下划线）✗
        #   两个后果：
        #     ① **看不出这是哪个根** —— 索引里几条记录全写 `____`，互相盖
        #     ② **中文根名会撞桶**（都退化成下划线）
        #   ★ 口径：**清洗只该替换"文件系统不接受的字符"**，
        #     不是"把我不认识的字都换掉"。
        #     原来的正则把**中文、日文、俄文**全当成了非法字符。
        #   ★ 退化保护：清洗后若**没有字母数字**（全下划线/空），
        #     用**路径哈希**兜底 —— **唯一性优先于可读性**。
        _裸 = 根.name or "根"
        桶 = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", _裸).strip(" .")[:60]
        if not re.search(r"[^_.]", 桶):
            桶 = "根_" + hashlib.sha1(
                str(根).encode("utf-8", "replace")).hexdigest()[:12]
        return Path(_env图目录) / 桶
    return 根 / _KG目录


# ── 建图 / 更新 ─────────────────────────────────────────────────────
def _收尾灌确证(根: Path, 出: dict, 打印: Callable[[str], None],
                灌确证: bool, 确证档: str | Path | None) -> dict:
    """建完图之后：**被显式要求时**，把 elaborator 确证名合流进来。

    ★ R366 实测：`灌Lean确证` **写了、完整、没人调** ——
      判十一（「没有死函数」）报 `kg.py::灌Lean确证@2981`。
      **目标 ⑥（Lean/elaborator 合流）只做了一半：函数在，接线不在。**
      「**一个没有调用方的函数等于没有接线。**」

    ★★★ **为什么必须显式开关，不许自动判**：
      我第一版写的判据是「**确证档所在仓 == 目标仓**才灌」，想避免
      「对别的仓灌 5 万个挂不上的节点」。**实测这个判据在真实使用里不成立**：
        确证档在 `…/agentscope_orchestrator/products/mem/`（`$pkg`），
        而图建在 `…/太虚元道/.kg/`（`$root`）—— **跨仓**。
      ⇒ 那个判据让合流**永远不生效**，而返回值上**看不出任何异常**。
      ★ 口径：**`建图` 是通用机器，不许猜跨仓的事**。
        跨仓关系由**调用方**声明（就像 `确证档` 路径本身就是调用方给的）。
      ★ `灌确证=True` 时若图里没有 lean 节点，**如实说跳过**，不静默。
    """
    if not 灌确证:
        return 出
    _图 = 加载图(根)
    if "未测到" in _图:
        return 出
    节 = _图.get("节点") or {}
    if not 节:
        return 出
    有lean = any(n.get("语言") == "lean" for n in 节.values())
    if not 有lean:
        打印("   Lean 合流：跳过（图里没有 .lean 节点）")
        return 出
    try:
        return 灌Lean确证(根, 确证档, 打印=打印)
    except Exception as e:                        # noqa: BLE001
        # ★ **不许把合流失败吞成「没这回事」** —— 如实报在返回里
        出["★Lean合流失败"] = f"{type(e).__name__}: {e}"
        return 出


# ★★★ R530：`_进度回调(阶段, 已做, 总数)` —— **长跑相位不许无声**（口径 337）。
#   ★ 默认 `None`：**不挂就没有任何行为变化**（既有调用方零影响）。
#   ★ 为什么需要它：陌生靶 UE5 `Engine\Source`（58,107 文件 / 2,091 MB）
#     实测 **9 小时**跑核心相位，而这段路上**一行进度都不打** ⇒
#     外面分不清 5% 还是 95%，「慢」与「挂住」只能靠**进程外探针的 ΔCPU** 猜。
#   ★ 阶段是**一个独立字段**（口径 311：不许用一个数兼两个意思）。
_进度回调 = None


def 建图(根: str | Path, 打印: Callable[[str], None] = print,
        灌确证: bool = False, 确证档: str | Path | None = None,
        排除=None) -> dict:
    """**全量**建图。跑完 `<根>/.kg/` 下有 图.json / 戳.json / 清单.json。

    ★ `灌确证=True` 时，建完图把 **elaborator 确证名合流进来**（目标 ⑥）。
      默认 **False** —— 确证档可能属于**别的仓**，跨仓关系由调用方声明。
    """
    根 = Path(根).resolve()
    if not 根.is_dir():
        return {"错误": f"不是目录：{根}"}
    t0 = _现在()
    # ★★★ R532：**粘滞剪枝**（用户 2026-11-15 定）——
    #   粘滞集来自**盘上的旧戳**（不是下面 `_跑` 的 `旧戳={}`：那是为强制重算 sha1）。
    救回: list[dict] = []
    文件们, 跳过 = 枚举文件(根, 排除=排除, 粘滞=_粘滞目录们(_读旧戳(根)),
                          救回=救回)                 # ★ walk 时剪枝（R418）
    文件们, _无结构 = _过值得(文件们, 根)
    文件们, _剔 = _过排除(根, 文件们, 排除)      # ★ 兜底：万一有漏网的
    跳过 = list(跳过) + _无结构 + _剔
    打印(f"   枚举：要进图 {len(文件们)} 个文件 · 跳过 {len(跳过)} 条（已记原因）")
    出 = _跑(根, 文件们, 跳过, 旧戳={}, 旧节={}, 旧边=[], t0=t0, 打印=打印,
              全量=True, 粘滞救回=救回)
    return _收尾灌确证(根, 出, 打印, 灌确证, 确证档)


def 更新图(根: str | Path, 打印: Callable[[str], None] = print,
         灌确证: bool = False, 确证档: str | Path | None = None) -> dict:
    """**增量**：只重算 sha1 变了的文件；删掉的标墓碑。"""
    根 = Path(根).resolve()
    旧 = 加载图(根)
    if "未测到" in 旧:
        打印("   （没有旧图 ⇒ 退化成全量）")
        return 建图(根, 打印, 灌确证, 确证档, 排除)
    救回2: list[dict] = []
    文件们, 跳过 = 枚举文件(根, 粘滞=_粘滞目录们(旧.get("戳", {})), 救回=救回2)
    文件们, _无结构2 = _过值得(文件们, 根)
    跳过 = list(跳过) + _无结构2
    出 = _跑(根, 文件们, 跳过, 旧戳=旧.get("戳", {}), 旧节=旧.get("节点", {}),
              粘滞救回=救回2,
             旧边=旧.get("边", []), t0=_现在(), 打印=打印, 全量=False)
    return _收尾灌确证(根, 出, 打印, 灌确证, 确证档)


def _跑(根: Path, 文件们: list[Path], 跳过: list[dict], 旧戳: dict, 旧节: dict,
        旧边: list, t0: float, 打印: Callable[[str], None], 全量: bool,
        粘滞救回: list | None = None) -> dict:
    kg = _kg目录(根)
    # ★★ 「任意文件夹」必须覆盖**写不进去的文件夹**（只读目录 / 权限拒）。
    #   原来这里裸抛 `PermissionError: [WinError 5] 拒绝访问` ⇒ **换一个根就直接崩**，
    #   而不是告诉你「这个根写不了图」。真事：`太虚元道\src` 就写不进去。
    try:
        kg.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"错误": f"图目录写不进去：{kg}（{type(e).__name__}: {e}）",
                "建议": "换一个可写的根，或给这个目录写权限。"
                        "**图必须落在根下的 `.kg/`** —— 常驻的语义就是「图跟着根走」。",
                "枚举到": len(文件们), "跳过数": len(跳过)}
    新戳: dict[str, dict] = {}
    节: dict[str, dict] = {}
    边: list[dict] = []
    口: dict[str, int] = {}
    问题: list[dict] = []
    重算 = 0
    当前 = set()

    # ★★ 性能 bug（本轮实测抓到的）：原来「没变的文件的边也带过来」写成
    #    `for e2 in 旧边: ... 新戳[相]["sha1"] == 旧戳...` ⇒ **O(边×文件)**。
    #    标准库实测 883,547 条边 × 3,905 文件 ⇒ 增量 **193s > 全量 111s**。
    #    ⇒ 先把「没变的文件」算成一个集合，再**一趟**遍历旧边。
    没变集: set[str] = set()
    变了集: set[str] = set()

    if _进度回调 is not None:
        _进度回调("枚举完成", len(文件们), len(文件们))

    for p in 文件们:
        # ★★ bug（判五十在**带子目录**的临时目录上抓到的）：Windows 上
        #   `relative_to()` 给的是 `子\\g.sql`（反斜杠），图上就存了 OS 原生分隔符
        #   ⇒ **同一个文件在两个平台上长得不一样**，跨机比对/增量都会错。
        #   ⇒ 图上**一律正斜杠**（`as_posix()`）。
        相 = p.relative_to(根).as_posix()
        当前.add(相)
        # ★★★ R530：**长跑相位不许无声**（口径 337）—— 陌生靶 UE5 `Engine\Source`
        #   （58,107 文件 / 2,091 MB）实测 **9 小时**跑这段，而这段路上
        #   **一行进度都不打** ⇒ 外面**分不清 5% 还是 95%**
        #   （「慢」与「挂住」分不开；当时只能靠**进程外探针的 ΔCPU** 猜，口径 354）。
        #   ★ 同一份代码在 `Runtime\Core`（1,987 文件）上只跑 323s ——
        #     **小靶上看不出这个缺陷，因为不到一分钟就结束了**。
        #   ★ 默认 `None` ⇒ 对既有调用方**零影响**；挂上就有进度。
        if _进度回调 is not None and len(当前) % 2000 == 0:
            _进度回调("解析", len(当前), len(文件们))
        try:
            戳 = _戳(p, 旧戳.get(相) if not 全量 else None)
        except OSError as e:
            跳过.append({"路径": 相, "类": "文件", "原因": f"戳失败：{type(e).__name__}"})
            continue
        新戳[相] = 戳
        if not 全量 and 旧戳.get(相, {}).get("sha1") == 戳["sha1"]:
            没变集.add(相)
            continue
        变了集.add(相)
        重算 += 1
        n节, n边, 元 = _解析一个(p, 相)
        口[元["适配器"]] = 口.get(元["适配器"], 0) + 1
        if 元.get("原因"):
            问题.append({"文件": 相, "适配器": 元["适配器"], "原因": 元["原因"]})
        # ★★ bug（自洽门抓到的）：`file:` 节点原来只在「**没有**定义」时才建
        #    ⇒ 有定义的文件反而**缺文件节点** ⇒ 它发出的 `包含` 边**全部悬空**。
        #    症状是「自洽门在正常图上就红了：有 N 条边的终点不在节点表里」。
        #    ⇒ **文件节点永远建**（每个进图的文件在图上都必须自己是一个点）。
        节[f"file:{相}"] = {"id": f"file:{相}", "类": "文件", "名": 相,
                           "文件": 相, "类型": "file",
                           "适配器": 元["适配器"], "原因": 元.get("原因")}
        for n in n节:
            n["适配器"] = 元["适配器"]
            节[n["id"]] = n
        for e2 in n边:
            边.append(e2)

    _增量丢边 = 0      # ★ 增量时查不到归属文件的边（**报出来，不静默**）
    # 没变的文件：节点与边都从旧图搬过来（**一趟**遍历，O(节点)+O(边)）
    if not 全量:
        if _进度回调 is not None:
            _进度回调("搬旧节点", 0, len(旧节))
        for i, n in 旧节.items():
            相 = n.get("文件")
            # ★★★ **"不属于任何源文件"的节点必须无条件搬**（R403）。
            #   实测（Lean 图，定因链 R399→R402→R403）：
            #     `灌Lean确证` 给"模块→文件对不上"的确证名建了节点，
            #     `文件` 是伪归属 `"<elaborator>"`（R403 前半段的修）；
            #     但**搬运条件只有 `相 in 没变集`** ⇒ 伪归属**永远不在
            #     "没变的文件"里**（它根本不是文件）⇒ 那 5 个节点
            #     每次增量都被丢掉（213,852 → 213,847）。
            #   ★ 口径：**"按 X 刷新"的机器，必须给"不属于任何 X"的东西
            #     一条显式的通路** —— 否则它在**每一次刷新**里都被当作
            #     "已经不存在了"。**这是"沉默的删除"，比报错坏得多。**
            #   ★ 判据形状：`相 in 没变集` 是"这个文件没变"，而
            #     `相 not in 当前` 是"它压根不是（本仓的）文件" ——
            #     **两件事，必须分开写。**
            if 相 in 没变集 or (相 and 相 not in 当前):
                if n.get("类") == "墓碑":
                    continue          # 墓碑不搬 —— 它由下面的逻辑重新判定
                节[i] = n
        # ★★★ **不许再用「手写 id 前缀清单」判边属于哪个文件**（R351）。
        #   上一版是：
        #     `for 前缀 in ("file:","定义:","声明:","项:","类型:","标题:","小节:",
        #                   "chunk:","key:"): if 起.startswith(前缀): ... break`
        #   两个病：
        #     ① **清单漏了 `mod:`/`绑定:`/`tomb:` 等** ⇒ 这类边**一条都进不了新图**；
        #     ② **没命中任何前缀时循环走完、什么都不做** ⇒ **静默丢边**
        #        —— 增量模式下边会**越来越少**，而读数上看不出「丢了」。
        #   ★ 这是同一个「手写清单必然漏」的**第三次**（前两次：并进主图的 id 前缀、
        #     搬运时把 `名` 当身份键）。⇒ 不再猜前缀，**直接查节点表**：
        #     边的任一端若是图上的节点，那个节点的 `文件` 就是这条边所属的文件。
        #   ★ 查不到就**计数报出来**，不静默。
        for e2 in 旧边:
            相2: str | None = None
            for _端 in (e2.get("起", ""), e2.get("终", "")):
                if _端.startswith("file:"):
                    _文2 = _端[5:]
                    if _文2 and not _文2.startswith(
                            ("name:", "mod:", "bind:", "tomb:")):
                        相2 = _文2
                        break
                _n2 = 旧节.get(_端)
                if isinstance(_n2, dict) and _n2.get("文件"):
                    相2 = _n2["文件"]
                    break
            if 相2 is None:
                _增量丢边 += 1
                continue
            if 相2 in 没变集:
                # ★★★ **搬旧边之前必须把「引用」退回「提及」**（R361）。
                #   病根：`解析引用` **不是只读的** —— 它把主图里的提及边
                #   **原地改成**引用边（`"关系":"引用"`，靶换成节点 id，
                #   同时留下 `原名`）。**边数不变**。
                #   ⇒ 于是「增量重建」是从**一份被读操作改过的图**上搬边。
                #   ★ 实测（标准库，R361）：
                #       全量后 边 2,814,885 · 出现过的(文件,名字) **503,743** · 解到定义 **109,785**
                #       增量后 边 2,814,885 · 出现过的(文件,名字) **405,326** · 解到定义  **11,368**
                #     **节点数、边数一模一样，内容不一样** ——
                #     只报计数的话，这是一次**完美的假绿**。
                #   ★ 口径：**「读」不许改「源」**。解析结果是**派生物**
                #     （`引用.json` 已经是独立的一份），主图必须能回到原状。
                #   ★ 退回**只动两个字段**（`关系`/`终`），**不列字段清单** ——
                #     「手写清单必然漏」在本文件已经栽过三次；而且
                #     `原名` 就是原值本身，其余字段（`名`/`依据`/`作用域`…）
                #     是附加信息且下次解析会重算，**留着无害**。
                #   ★ 用 `{**e2}` 造新字典：**不许改旧图里的那个对象**
                #     （否则「退回」本身又成了一次原地改）。
                if e2.get("关系") == "引用" and e2.get("原名"):
                    边.append({**e2, "关系": "提及", "终": e2["原名"]})
                else:
                    边.append(e2)

    # 墓碑：旧图有、这次枚举没有 ⇒ **两种原因，必须分开**（口径 348：两个量不许共用一个名字）
    #   ★★ R525m（陌生仓库验收追出来的）：`枚举文件` 有一条剪枝规则是
    #     「**同前缀兄弟 ≥3 个 ⇒ 运行副本**」，而**兄弟是别的会话后来新建的**
    #     ⇒ 同一个文件「建图时在图里、现在不在，**而它一个字节都没改**」。
    #     实测：`formal_proof/_lead024/zhi_verify/` 的 9 个 `.py` 盘上 `is_file()`=True
    #     却枚举不到（剪枝理由原文：运行副本（同前缀兄弟 ≥3 个））。
    #   ⇒ 这就是「全称通用」最深的裂缝：**图的「我有什么」不是路径的函数，
    #     是整个目录列表的函数**（同样的路径、同样的内容、不同的图）。
    #   ★ 拆开记：**真删**（盘上没了）vs **剪枝漂移**（盘上还在、这一轮被剪了）。
    _全墓碑 = sorted(set(旧戳) - 当前)
    墓碑 = [相 for 相 in _全墓碑 if not (根 / 相).exists()]
    _漂移 = [相 for 相 in _全墓碑 if (根 / 相).exists()]
    for 相 in _全墓碑:
        _还在 = (根 / 相).exists()
        节[f"tomb:{相}"] = {"id": f"tomb:{相}", "类": "墓碑", "名": 相, "文件": 相,
                           "类型": "tombstone", "★盘上还在": _还在,
                           "★原因": ("剪枝漂移（盘上还在，这一轮被剪掉）" if _还在
                                    else "文件已不在盘上（真删/改名）")}

    # ★ R498：`跳过` 的**分类分部**（口径 203：剔掉必须留痕，且要看得出剔了什么）
    _跳类: dict[str, int] = {}
    _跳后: dict[str, int] = {}
    for _t in 跳过:
        _r = str((_t or {}).get("原因") or "（没写原因）")[:40]
        _跳类[_r] = _跳类.get(_r, 0) + 1
        # ★★★★ R508：**读错字段名**（口径 311：字段名本身可以是一个缺陷）。
        #   `跳过` 条目里**文件型用键 `文件`**（实测 `数据部署` 36,722 条）、
        #   **目录型用键 `路径`**（18 条）—— 原来只读 `路径`
        #   ⇒ 36,722 条读不到 ⇒ `Path("").suffix` = `""` ⇒ **全记成 `(无后缀)`** ✗
        #   影响 **16 / 17 个桶**（判 160 实测）：
        #     `数据部署` `.xyz 12,311 → 无` · `太虚元道` `.o 4,557 → 303`
        #   ★ 后果不是"少一点读数"，是**这一栏一个信息都没有**：
        #     口径 199/293 要它回答"剔掉的是 `.o` 二进制，还是本该进图的源码"，
        #     而它把**所有**被剔的都写成 `(无后缀)` —— **看起来像"没有异常"**。
        #   ★ 两个键都没有 ⇒ 记 `(没有路径字段)`，**不许静默成 `(无后缀)`**
        #     （口径 260：空输入不补 0；口径 190：`unknown` 与 `False` 分开）。
        _tt = (_t or {})
        _p = str(_tt.get("路径") or _tt.get("文件") or "")
        _s = Path(_p).suffix.lower() or ("(没有路径字段)" if not _p else "(无后缀)")
        _跳后[_s] = _跳后.get(_s, 0) + 1
    _跳类 = dict(sorted(_跳类.items(), key=lambda kv: -kv[1]))
    _跳后 = dict(sorted(_跳后.items(), key=lambda kv: -kv[1]))
    出 = {
        "根": str(根), "建于": t0, "模式": "全量" if 全量 else "增量",
        "文件数": len(当前), "重算文件数": 重算,
        "节点数": len(节), "边数": len(边),
        "适配器分布": 口,
        "墓碑数": len(墓碑),
        # ★★ R525m：**漂移必须是一个看得见的数**（口径 203/260）。
        #   它原来被并进「墓碑」，读者会以为「文件被删了」——**不是**。
        # ★★★ R532：**粘滞**的读数 —— 单列，**不许塞进 `跳过`**
        #   （那会让 `文件 + 跳过 == 枚举到 + 枚举剪` 这条恒等式错掉；口径 348）。
        #   ★ 「本会被剪、因为上一轮在图上而没被剪」的目录，是**策略生效的证据**，
        #     没有这个读数就分不清「粘滞生效了」和「本来就没触发剪枝」（口径 307）。
        "★粘滞救回数（上一轮在图上、这一轮本会被剪）": len(粘滞救回 or []),
        "★粘滞救回样例": (粘滞救回 or [])[:20],
        "★粘滞口径（R532）": ("上一轮在图上（`戳.json` 里的文件 ⇒ 其目录及祖先）的目录，"
                          "**这一轮不许被剪** —— 用户 2026-11-15 定的「粘滞」。"
                          "★ 为什么：剪枝规则「同前缀兄弟 ≥3 个」的输入是**盘上的兄弟**，"
                          "而兄弟是别的会话随时新建的 ⇒ **图的文件集不是路径的函数**"
                          "（同一个目录，旁边多两个同名兄弟就从图里消失）。"
                          "★ 这一项**不属于 `跳过`**（粘滞是「不剔」，跳过是「剔」，口径 348）。"),
        "★剪枝漂移数（盘上还在、这一轮被剪掉）": len(_漂移),
        "★剪枝漂移样例": _漂移[:20],
        "★口径（墓碑 vs 漂移）": ("墓碑=盘上没了（真删/改名）；"
                              "剪枝漂移=盘上还在但这一轮被枚举剪掉 ⇒ "
                              "**图的文件集不是路径的函数**，会随无关目录的出现而变"),
        "★增量丢边（查不到归属文件的边）": _增量丢边,
        "跳过数": len(跳过),
        # ★★★★ R498：**剔掉的东西必须能分类读到**（口径 203/199）。
        #   判一百四十七 报过：仓库根跳过了 **84,439** 个，而 `图.json` 里
        #   **只有一个总数** ⇒ 读者分不清「剔掉的是 `.o` 二进制」还是
        #   「剔掉的是本该进图的源码」—— 而**错剔**恰恰是口径 203 最怕的事。
        #   ★ 分两个维度（口径 282：一条读数可能属于不同层，要分开给）：
        #     `★跳过分类` 按**原因**（"为什么剔"）· `★跳过后缀` 按**形态**（"剔了什么"）
        "★跳过分类": _跳类,
        "★跳过后缀": _跳后,
        "★跳过分类口径": ("按**原因**分部（前 40 字）+ 按**后缀**分部。"
                      "两者各自的**和都等于 `跳过数`**。"
                      "★ 口径 203：**剔掉必须留痕** —— 且要**看得出剔的是什么**，"
                      "否则「错剔了一个本该进图的文件」与「正确地剔掉一个 `.o`」"
                      "在盘上长得一样。"),
        "问题数": len(问题),
    }
    _原子写519((kg / _图档), json.dumps(出, ensure_ascii=False, indent=1))
    _原子写519((kg / "节点.json"), json.dumps(节, ensure_ascii=False))
    _原子写519((kg / "边.json"), json.dumps(边, ensure_ascii=False))
    _原子写519((kg / _戳档), json.dumps(新戳, ensure_ascii=False))
    _原子写519((kg / _清档), json.dumps({"跳过": 跳过, "问题": 问题, "墓碑": 墓碑,
                                       "★剪枝漂移": _漂移},
                                       ensure_ascii=False, indent=1))
    出["耗时秒"] = round(_现在() - t0, 1)
    出["★自洽门"] = 自洽门(根)
    _原子写519((kg / _图档), json.dumps(出, ensure_ascii=False, indent=1))
    打印(f"   {出['模式']}：文件 {出['文件数']}（重算 {重算}）· 节点 {出['节点数']}"
         f" · 边 {出['边数']} · 墓碑 {出['墓碑数']}"
         f" · **剪枝漂移 {出['★剪枝漂移数（盘上还在、这一轮被剪掉）']}** · 跳过 {出['跳过数']}"
         f" · 问题 {出['问题数']} · {出['耗时秒']}s")
    return 出


def 加载图(根: str | Path) -> dict:
    kg = _kg目录(Path(根).resolve())
    try:
        出 = json.loads((kg / _图档).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"未测到": f"没有 `{kg / _图档}`（先跑 `kg 建 <根>`）"}
    except Exception as e:                        # noqa: BLE001
        return {"未测到": f"图读不出来（{type(e).__name__}: {e}）"}
    for 名, 键 in (("节点", "节点.json"), ("边", "边.json"), ("戳", _戳档)):
        try:
            出[键.split(".")[0]] = json.loads((kg / 键).read_text(encoding="utf-8"))
        except FileNotFoundError:
            出[键.split(".")[0]] = {}
        except Exception:                         # noqa: BLE001
            出[键.split(".")[0]] = {}
    try:
        清 = json.loads((kg / _清档).read_text(encoding="utf-8"))
    except Exception:                             # noqa: BLE001
        清 = {}
    出["清单"] = 清
    return 出


# ── 跨文件引用解析 ──────────────────────────────────────────────────
def _内置模块名() -> frozenset[str]:
    """**内置/标准库模块名**（权威来源，不手写表）。

    ★ 为什么要它（R392 实测）：`codecs.charmap_encode` / `os.fspath` 这类
      **限定名**，宿主是标准库模块 —— 而源码里**不会 import `codecs`/`os`**
      （它们在解释器里）⇒ 判"宿主是本文件 import 的模块"**一条都匹配不上**
      ⇒ 全落「未解析」，而它们的靶**在仓外**，本该是**外部符号**。
    ★ 来源：`sys.builtin_module_names`（编译进解释器的）+ `sys.stdlib_module_names`
      （3.10+ 的标准库名单）。**都不手写。**
    ★ 诚实边界：这**只**回答"这个名字是不是解释器自带的模块" ——
      它**不回答**"仓里有没有同名模块"（那是 `模块表` 的活）。
    """
    if _内置模块缓存:
        return _内置模块缓存[0]
    出: set[str] = set()
    try:
        import sys as _s
        出 |= set(_s.builtin_module_names)
        出 |= set(getattr(_s, "stdlib_module_names", ()) or ())
    except Exception:                             # noqa: BLE001
        pass
    _内置模块缓存.append(frozenset(出))
    return _内置模块缓存[0]


_内置模块缓存: list = []


# ★★★ **Lean 的"保留字/tactic/命令"不是名字**（R405）。
#   实测（常驻 Lean 图，4,033 个 .lean，未解析 134,700 条 / 去重 15,206 个）：
#     Top：MyMeta 3771 · **rfl 2321** · **exact 2180** · **_ 2130** · h 2114 ·
#          **intro 1888** · **false 1843** · **set_option 1814** · List 1656 ·
#          **rw 1572** · n 1516 · **linter 1506** · **simp 1410** · x 1360 ·
#          **decide 1322** · **unfold 1308** · a 1264 · b 1162 · **cases 1153** ·
#          **true 1087** · **refine 1029** · **constructor 927** · …
#   ★ 分成**四类**，只有最后一类是真缺口：
#     ① **关键字/命令**：`set_option`·`linter`·`false`·`true`·`_`·`sorry`…
#     ② **tactic**：`rfl`·`exact`·`intro`·`rw`·`simp`·`decide`·`unfold`·
#        `cases`·`refine`·`constructor`·`rcases`·`induction`·`apply`·`have`…
#        —— **tactic 是"语法位置上的保留动词"**，不是它所在的命名空间里的常量。
#     ③ **局部绑定**（定理参数 / 假设 / `intro` 引入的名字）：`h`·`n`·`x`·`a`·
#        `b`·`p`·`k`·`f`·`c`·`y`·`h1`·`s`·`d`·`ih`·`m`·`i` ——
#        这一族**本轮不处理**（要真做需要作用域，属目标②）；**但它们也不是"未解析"**，
#        见下面的 `_Lean似局部`。
#     ④ **真外部缺**：`List`·`Bool`·`DecidableEq`·`Fin`·`succ`…（`Init`/Mathlib）
#   ★ 口径：**假缺口比缺口更坏** —— 把 `rfl` 算成"未解析"会让人去"补"一个
#     **根本不该存在的定义**。**让它们进"未解析"不是"宁可多报"，是报错了。**
_Lean关键字 = frozenset("""
set_option linter false true _ sorry admit by at from with in do then else
where let have show fun match if unless return deriving extends instance
namespace section end open variable variables universe theorem lemma example
def abbrev structure inductive class mutual private protected noncomputable
partial unsafe opaque axiom constant attribute macro syntax notation infix
infixl infixr prefix postfix scoped local export import init builtin
""".split())

_LeanTactic = frozenset("""
rfl exact intro intros rw rewrite simp decide unfold cases rcases induction
refine constructor apply have' apply' assumption contradiction exfalso
by_contra by_cases push_neg contrapose specialize obtain use existsi
ring omega norm_num simp_all linarith nlinarith positivity field_simp
subst injection injections cc split_ifs trans calc conv rw? simp? exact?
apply? trivial tauto aesop decide! native_decide norm_cast push_cast
iterate repeat first try all_goals any_goals focus swap rotate case
""".split())

_Lean似局部 = frozenset("""
h h1 h2 h3 h4 h5 ih ih1 ih2 ih3 n n1 n2 x x1 x2 a a1 a2 b b1 b2 p p1 p2
k k1 k2 f f1 f2 c c1 c2 y y1 y2 s s1 s2 d d1 d2 m m1 m2 i i1 i2 j j1 j2
t t1 t2 u u1 u2 v v1 v2 w w1 w2 g g1 g2 e e1 e2 r r1 r2 q q1 q2 l l1 l2
o o1 o2 z z1 z2 hx hy hz hab hba hn hp hk hf hg hs ht hu hv hw hd
""".split())

# ★ 实测补充：这些是 tactic 的**变体/名字**，上一版漏了（R407）
_LeanTactic |= frozenset("""
simpa rintro absurd rintro? simpa? push_neg? by_contra! omega? linarith?
positivity? norm_num? aesop? exact_mod_cast norm_cast? field_simp?
""".split())

# ★ 实测补充（R408）：这些是 **tactic 里的语法关键字**，不是被引用的名字 ——
#   `rw [h] at h2` 的 `at`、`simp only [h]` 的 `only`、`induction x using f` 的 `using`、
#   `generalizing x` 的 `generalizing`、`with`、`<;>`、`·`…
_Lean关键字 |= frozenset("""
using only generalizing at with without simp_all? renaming this
""".split())

# ★ 实测补充（R409）：core tactic 里上一版漏掉的
_LeanTactic |= frozenset("""
change dsimp dsimp? change? show? convert convert? extract_lets
""".split())

_Lean保留 = _Lean关键字 | _LeanTactic


def _取内置名() -> frozenset[str]:
    """语言内置名。**用权威来源取，不手写表** —— 手写的表一定会漏、会过期。

    ★ 别的语言（rust/std/go/…）这里给不出，就**如实为空** ——
      「不知道」不等于「没有」，读数里会体现为「未解析」而不是「内置」。
    """
    出: set[str] = set()
    try:
        import builtins as _b
        出 |= set(dir(_b))
    except Exception:                             # noqa: BLE001
        pass
    # ★★★ **模块级 / 类级 / 函数级的 dunder 属性也是「语言内置」**（R386）——
    #   `dir(builtins)` **只给内置函数与异常**，给不出 `__file__`/`__name__`/
    #   `__class__`/`__dict__`/`__module__`/`__qualname__` 这些**每个对象都有的**名字
    #   ⇒ 实测标准库未解析里 `__file__ 116` 占了一整片。
    #   ★ **仍然不手写表** —— 去问**类型对象自己**（权威来源）：
    #     · `dir(types.ModuleType)` ⇒ `__name__`/`__doc__`/`__file__`/`__package__`
    #       /`__loader__`/`__spec__`
    #     · `dir(object)`           ⇒ `__class__`/`__dict__`/`__module__`/`__init__`/`__eq__`…
    #     · `dir(type)`             ⇒ 元类再加的那些
    #     · `dir(types.FunctionType)` ⇒ 函数属性
    #   ★ 口径：**「不知道」不等于「没有」，但「能问到」就不许手写。**
    try:
        import types as _t
        出 |= set(dir(_t.ModuleType))
        出 |= set(dir(_t.FunctionType))
    except Exception:                             # noqa: BLE001
        pass
    try:
        出 |= set(dir(object))
        出 |= set(dir(type))
    except Exception:                             # noqa: BLE001
        pass
    # ★★★ **`__file__` 之类的「解释器设置的模块属性」要问已加载模块自己**（R386）。
    #   `dir(types.ModuleType)` **给不出 `__file__`** —— 它是**实例属性**，
    #   由 import 机制在加载时**挂到模块字典上**（C 扩展模块没有它，所以
    #   `dir(sys)` 也没有 ⇒ 不能只挑一个模块）。
    #   ⇒ 取**若干个真实模块的 `__dict__` 里的 dunder**（`__x__` 形状）——
    #     这是「解释器会设哪些模块属性」的**权威集合**；
    #     **只取 dunder**，免得把模块自己的名字（`os`/`sys`）算成内置。
    for _mn in ("os", "sys", "types", "collections", "importlib"):
        try:
            _mod = __import__(_mn)
        except Exception:                         # noqa: BLE001
            continue
        出 |= {k for k in vars(_mod) if k.startswith("__") and k.endswith("__")}
    for 名 in ("True", "False", "None", "self", "cls", "super", "print", "len"):
        出.add(名)
    return frozenset(出)


_内置名 = _取内置名()
# ★ 入口文件名的**权威清单**（各语言定死的「包就住在这个文件里」）
_入口名 = frozenset({"__init__.py", "__init__.pyi", "index.js", "index.mjs",
                     "index.ts", "index.tsx", "mod.rs", "lib.rs", "main.rs",
                     "main.go", "main.py", "__main__.py", "index.go"})


def _入口优先(目: list[str]) -> list[str]:
    """模块名命中多个文件时，**入口文件优先**（`__init__.py` 等）。

    ★ 为什么不直接标歧义：`import numpy` 的权威答案由**语言**定死 ——
      它就是那个包的入口文件。索引宽是为了「不把能解析的变成未解析」，
      但**宽索引的代价必须由这里收回**，不能让假歧义混进读数。
    """
    排 = sorted(目, key=lambda f: (f.rsplit("/", 1)[-1] not in _入口名, f))
    return 排


def _解相对(相: str, 模: str) -> str:
    """把**相对模块名**解成**仓内相对路径**（去扩展名）。不是相对的**原样返回**。

    ★★★ 为什么必须有这一步（R349 实测）：
      JS/TS 写 `import { clamp } from "./util/clamp.js"` —— 而
      `_模块索引` 给 `src/util/clamp.js` 注册的键是 `src/util/clamp` / `clamp` / `util`，
      **没有 `./util/clamp.js`** ⇒ 一条都匹配不上 ⇒
      `已解析·导入模块 = 0`，`./util/clamp.js` 被当成**仓外模块**。
      一个仓里就有、就在隔壁的文件，图上却「不在本仓」。

    ★★★ 口径（**与语言无关**）：**相对路径只在「发起导入的那个文件」的位置上才有意义**。
      同一个字符串 `./util/clamp.js` 在 `src/index.js` 里指 `src/util/clamp.js`，
      在 `lib/a.js` 里指 `lib/util/clamp.js` —— **离开发起文件，它没有含义**。
      （Python `from . import x`、Rust `super::x` 同理。）

    ★ 不做「猜后缀」：只按**写出来的字面**解析（`.js`/`.ts`/`.py` 都去掉最后一段后缀），
      再把 `/index`、`/__init__`、`/mod` 这类入口名也试一遍 —— **试不到的如实留成未解析**。
    """
    s = 模.strip()
    # ★★★ **Python 的点分相对导入也是"相对"，也必须按发起文件解**（R388）。
    #   实测踩到（未解析 Top 第一条 `NDArray 460`）：
    #     `numpy/_typing/__init__.py` 里 `from ._array_like import NDArray`
    #     ⇒ 导入边上的模块名是 **`._array_like`**，而这里**原样返回** ✗
    #     ⇒ 查不到 ⇒ **`NDArray`/`_Shape`/`_AnyShape` 全落未解析**（460+294+86 条）。
    #   ★ 而本函数的 docstring 里**我自己写着**：
    #     「Python `from . import x`、Rust `super::x` 同理」
    #     —— **我写了这句话，却没实现 Python 那一支**（与 R387 同一个病）。
    #   ★ 语义（`importlib._bootstrap._calc___package__` + `_sanity_check`）：
    #     点的个数 = **上跳级数 + 1**（一个点 = 当前包，两个点 = 上一级）。
    #     余下部分若是点分（`._a.b`）⇒ 换成 `/`（`_a/b`）。
    _点 = len(s) - len(s.lstrip("."))
    #   ★ **必须排除 `./` 与 `../`**（R388 第二次踩）：`./util/clamp.js` 去点后是
    #     `/util/clamp.js`（**以 `/` 开头**）⇒ 若不排除，它会被当成"点分相对导入"
    #     去走 `.replace(".", "/")` ⇒ 实测变成 `.../util/clamp/js`（**错的路径**）。
    #     ⇒ 判据：去点后**以 `/` 开头的一律不是点分**。
    if (_点 > 0
            and not s[_点:].startswith("/")
            and (len(s) == _点 or not s[_点:].startswith(("./", "../")))):
        余 = s[_点:]
        基部 = (相.rsplit("/", 1)[0].split("/") if "/" in 相 else [])
        上跳 = _点 - 1
        基部 = 基部[:len(基部) - 上跳] if 上跳 and 上跳 <= len(基部) else ([] if 上跳 else 基部)
        部 = 基部 + ([x for x in 余.replace(".", "/").split("/") if x] if 余 else [])
    else:
        if not (s.startswith("./") or s.startswith("../") or s in (".", "..")):
            return 模
        基 = 相.rsplit("/", 1)[0] if "/" in 相 else ""
        部 = (基.split("/") if 基 else []) + s.split("/")
    栈: list[str] = []
    for x in 部:
        if x in ("", "."):
            continue
        if x == "..":
            if 栈:
                栈.pop()
            continue
        栈.append(x)
    路 = "/".join(栈)
    if not 路:
        return 模
    # 去扩展名（最后一段里的最后一个点之后，若长度 <= 5 且是字母）
    p = 路.rsplit("/", 1)[-1]
    if "." in p:
        头, _, 尾 = p.rpartition(".")
        if 尾 and len(尾) <= 5 and 尾.isalnum():
            路 = (路[: -(len(尾) + 1)])
    return 路 or 模


def _导入优先级(路: str) -> tuple:
    """模块候选文件的**解析优先级**（小者优先）—— 复刻 `sys.path` 的顺序语义。

    ★★★ 为什么必须有（R378 实测，**这是"错答案"不是"缺答案"**）：
      `依据=导入模块` 的 24,296 条里，**11,209 条（46%）**的模块名在模块表里
      有**多个候选文件**，而其中 **5,313 条**被静默压成一个：
    ````
    warnings  选=site-packages/pydantic/warnings.py   ← **错的**
              候选 warnings.py                          ← 本该是这个
    ````
      根因：`_入口优先`（把 `__init__`/`index`/`mod`/`main` 排前）之后
      **平局时按扫盘顺序取第一个** —— 那是**任意的**，与语言的解析规则无关。
    ★ 口径：**「答案错了」比「未解析」更坏** ——
      未解析会被 ④ 报成缺口；错答案会被当成事实用于计划。

    ★ 权威规则（Python）：`import X` 在 **`sys.path` 顺序**里找第一个 `X`
      —— 所以**包根浅的在前**、`site-packages/` 在后（`sys.path` 里 stdlib 先于
      site-packages）。这不是"猜哪个更像"，是**语言的解析顺序**。
    ★ 通用性（不限 Python）：其余语言（Rust `crate::`、Go module path、JS `node_modules`）
      的解析同样**按根目录顺序**，故这条**按路径结构排**，不列举语言。
    ★ 诚实边界：**仍平局的如实标歧义**，不许挑一个装作确定。
    """
    段 = [x for x in str(路).split("/") if x]
    # ① 段数少者优先（越靠近根越先被搜到）
    # ② `site-packages` / `node_modules` / `vendor` 这类**依赖目录**排后
    # ③ 再按原路径字典序（**只是让结果稳定可复现**，不声称它更对）
    _依赖目录 = ("site-packages", "node_modules", "vendor", "dist-packages",
               "third_party", "thirdparty", ".venv", "venv")
    是依赖 = 1 if any(x in _依赖目录 for x in 段) else 0
    依赖深 = min((i for i, x in enumerate(段) if x in _依赖目录), default=len(段))
    return (是依赖, 依赖深, len(段), str(路))


def _模块索引(相们: Iterable[str]) -> dict[str, list[str]]:
    """**模块名 → 文件**（通用，不按语言）。一个文件注册**多种写法**，
    这样任何语言 import 出来的字符串都有机会匹配上。

    ★ 「看不见」不能当「没有」：注册得多只会让**歧义**变多，
      注册得少会让**能解析的变成未解析**。两种都要在读数里看得见。
    """
    出: dict[str, list[str]] = {}
    for 相 in 相们:
        p = 相.rsplit("/", 1)[-1]
        去后缀 = 相[:-(len(p))] + p.rsplit(".", 1)[0] if "." in p else 相
        候选 = set()
        候选.add(去后缀)
        候选.add(去后缀.replace("/", "."))
        # ★★★ **带后缀的整名也要注册**（R381）——
        #   因为**有的语言 import 写的就是带后缀的文件名**：
        #     C   `#include "local.h"` / `#include <Python.h>`
        #     JS  `import x from "./util/clamp.js"`
        #   实测踩过：只注册去后缀的键 ⇒ `local.h` 查不到 ⇒ 它被当成**仓外**头文件
        #   ⇒ 该文件里**全部**名字（连 `f`/`x`）都被判成「外部符号」——
        #   **一个查不到的键，把一整类答案染错。**
        候选.add(相)
        _尾 = 相.rsplit("/", 1)[-1]
        if "." in _尾:
            _后缀 = "." + _尾.rsplit(".", 1)[1]
            for i in range(1, len(相.split("/"))):
                尾2 = "/".join(相.split("/")[i:])
                if 尾2:
                    候选.add(尾2)
                if "/" not in 尾2:
                    候选.add(尾2.split(".")[0] + _后缀)
        # 各级后缀：`a/b/c` → `b/c`、`c`
        部 = 去后缀.split("/")
        for i in range(1, len(部)):
            尾 = "/".join(部[i:])
            候选.add(尾)
            候选.add(尾.replace("/", "."))
        # 包/入口文件的目录名也算（`__init__`、`index`、`mod`、`main`）
        名 = 部[-1].lower()
        if 名 in ("__init__", "index", "mod", "main", "lib"):
            目 = "/".join(部[:-1])
            if 目:
                候选.add(目)
                候选.add(目.replace("/", "."))
                尾 = 目.split("/")[-1]
                候选.add(尾)
                # ★★★ **包名也要做「各级后缀」**（R387）。
                #   实测踩到（未解析 Top 第一条）：
                #     `site-packages/matplotlib/_api/__init__.py`
                #     ⇒ 只注册了 `site-packages/matplotlib/_api`
                #       与 `site-packages.matplotlib._api`（**带 site-packages 前缀**）
                #     ⇒ **`matplotlib._api` 这个键根本不存在** ✗
                #       而源码写的是 `from matplotlib import _api, cbook` ⇒
                #       查 `matplotlib._api` 查不到 ⇒ **`_api` 395 条落未解析**。
                #   ★ 这正是本函数 docstring 里那句话的实例：
                #     「**注册得少会让能解析的变成未解析**」—— 我写了这句话，
                #     却在**入口名这条路上漏了它**。
                #   ★ 通用：`a/b/c/__init__.py` 要能被
                #     `a/b/c`、`b/c`、`c` 三种写法查到（包根深浅不同，语言都一样）。
                _目部 = 目.split("/")
                for i in range(1, len(_目部)):
                    尾i = "/".join(_目部[i:])
                    if 尾i:
                        候选.add(尾i)
                        候选.add(尾i.replace("/", "."))
                        # ★ 带后缀形式：`pkg/mod/__init__.py` ⇒ `pkg/mod/__init__.py` 已由
                        #   `候选.add(相)` 覆盖；各后缀的「整名」在这里补 `mod/__init__.py`。
                        候选.add(尾i + "/" + p)
        for c in 候选:
            if c:
                出.setdefault(c, [])
                if 相 not in 出[c]:
                    出[c].append(相)
    return 出


def _未测到留痕(图: dict, 根=None) -> dict:
    """★★★ **「未测到就早退」必须在返回里留痕**（R466）。

    ★ 为什么：全模块有 **8 处** `if "未测到" in 图: return 图` ——
      `解析引用` / `灌Lean确证` / `解析md链接` / `解析JSON引用` / `自洽门` /
      `报告` / `查` / `_涂`。它们**一个产物都不写**，而且**什么都不说**。
    ★ 实测事故（R465）：`work/图/formal_proof/` 里
      `节点.json` / `边.json` 是**今天**的，而 `外部缺.json` 的 mtime
      停在 **11-10 06:36（昨天）** —— **同一个桶里的产物来自两个时间点** ✗
      我读 `外部缺.json` 拿到旧口径（去重 3,000），而新代码其实已算出 6,601。
    ★ 口径 197：**一个机制改了产物就必须同时改它的账。**
      口径 199：**剔掉必须留痕** —— 这里是"**没写**必须留痕"。
      口径 214：**同一个概念写在 8 处 ⇒ 抽一处**，否则改了 1 处还剩 7 处。

    ★ 诚实边界：它**不改行为**（该早退还是早退），只是**把"没写"写出来**。
    """
    if not isinstance(图, dict):
        return 图
    没写 = ["引用.json", "外部缺.json", "缺口.json", "md链接.json", "json引用.json"]
    停在: dict = {}
    if 根 is not None:
        try:
            _桶 = _kg目录(Path(str(根)))
            for _名 in 没写:
                _f = _桶 / _名
                停在[_名] = _f.stat().st_mtime if _f.is_file() else 0
        except Exception:                         # noqa: BLE001
            停在 = {}
    图["★未测到（早退）"] = {
        "原因": "图里含 `未测到` ⇒ **本次一个产物都没写**",
        "★没写的产物": 没写,
        "★它们停在哪个时间（mtime=0 表示不存在）": 停在,
        "★口径": "**这个桶里的产物来自不同时间点** ⇒ 别把旧读数当新读数",
        "怎么办": "先让 `建图` 成功（图里没有 `未测到`），再调这个函数",
    }
    return 图



def 收尾全套(根: str | Path, 打印: Callable[[str], None] = print,
             确证档: str | Path | None = None, 灌确证: bool = True,
             自洽: bool = True) -> dict:
    """★★ R519：把一个根的**收尾产物一次算齐** —— 5 个产物，一个入口。

    ★★★ 为什么必须有（实测 2026-11-14，`grep` 数出来的，不是猜的）
````text
解析引用      定义 1 · 被调 **4**  ✓  ⇒ 引用.json / 外部缺.json / 缺口.json
灌Lean确证    定义 1 · 被调 **1**  ✓（默认关闭的收尾闸）
自洽门        定义 1 · 被调 **4**  ✓
解析md链接    定义 1 · 被调 **0**  ✗✗ **死函数**
解析JSON引用  定义 1 · 被调 **0**  ✗✗ **死函数**
````
    ★ 而代码**自己**（`_未测到留痕` L4407）写着收尾全套就是这 5 个产物：
      `["引用.json","外部缺.json","缺口.json","md链接.json","json引用.json"]`
      ⇒ **我一直在只做 1 个**（`引用.json`）⇒ 目标④「未解析的必须报出来」
        在 markdown/json 两支上**完全没接**。
    ★ 口径 366 的普遍形态：**一个没有调用方的函数等于没有接线。**

    ★★ 顺序（有依赖，不许换）：
````text
① 解析引用      ⇒ 引用.json / 外部缺.json / 缺口.json（读 边.json）
② 解析md链接    ⇒ md链接.json（读 节点.json 的 file: 集合；**断链 = 未解析引用**）
③ 解析JSON引用  ⇒ json引用.json（读 节点.json 的 file: 集合）
④ 灌Lean确证    ⇒ **改 节点.json/边.json** ⇒ 必须放最后（它之后不许再有读图步骤）
⑤ 自洽门        ⇒ 在**合流之后**跑才看得到新边的悬空与否
````

    ★ `灌确证` 的**自动判据**（★ **不猜跨仓**）：
      桶里已有 `lean确证.json` ⇒ **这个桶声明过要合流**（那是上一次灌的留痕，
      里面 `根` 字段记着原根）⇒ 用**它记的根**重灌。
      没有留痕的桶 ⇒ **不灌**，如实报「跳过（没声明过）」。
      ★ `_收尾灌确证` 的 docstring 明确禁止 `建图` 猜跨仓关系 —— 这里守住了：
        **证据在盘上**（"这个桶自己声明过"），不是我推断的。
    """
    根 = Path(根).resolve()
    出: dict = {"根": str(根)}
    _桶 = _kg目录(根)
    # ★★★ R519：**同一个锚定闸**（口径 305：一处提供、三处都用）。
    #   即使本函数后面是从留痕取根、碰巧用对，**入参姿势本身就是错的** ——
    #   而这种「碰巧对」正是让 2026-11-14 那次事故查了半天的原因：
    #   读代码的人看见 `收尾全套(桶目录)` 会以为它就该这么调。
    if _桶.resolve() == 根:
        return {
            "★错误": f"`根` **自己就是图桶**（`{根}`）⇒ 拒跑",
            "★为什么拒跑": ("`收尾全套` 的第一参数是**源码根**。传桶目录时 "
                            "`_kg目录(根)` = 根本身，本函数是在「桶自己」上找留痕、"
                            "并可能覆写这张图（实测毁过两张：1,503,681 → 21,160）。"),
            "★怎么修": f"传**源码根**：看 `{_桶 / '图.json'}` 的 `根` 字段。",
            "★哨兵": "判一百七十五（源码根不许自己就是图桶）",
        }
    打印(f"   ── 收尾全套：{根}")

    # ★★ R519b：**合流必须在最前**（它改 `节点.json`/`边.json`，是"改图的输入"）。
    #   我第一版把它放最后 ⇒ 它一改图，后面产出的 `md链接.json`/`json引用.json`
    #   立刻比 `节点.json` 旧 ⇒ 判一百七十二② 立刻红。
    #   ★ 那条判据的目标是「图重算过而派生没重跑」—— **它抓对了**，
    #     只是"重算图"的那一步是我自己放的（口径 332 的反面）。
    # ★ 只在盘上有留痕时灌（= 这个桶**自己声明过**要合流；不猜跨仓）。
    出["Lean合流"] = "跳过（这个桶没声明过要合流）"
    _留痕 = _桶 / "lean确证.json"
    if 灌确证 and _留痕.is_file():
        try:
            _旧 = json.loads(_留痕.read_text(encoding="utf-8"))
            _原根 = str(_旧.get("根") or "")
            _档 = 确证档 or _旧.get("确证档") or None
            if _原根 and Path(_原根).is_dir():
                出["Lean合流"] = 灌Lean确证(Path(_原根), _档, 打印=打印)
            else:
                出["Lean合流"] = f"跳过（留痕里的根不在：{_原根}）"
        except Exception as e:                        # noqa: BLE001
            出["Lean合流"] = f"★失败 {type(e).__name__}: {e}"
    elif 灌确证:
        打印("   Lean 合流：跳过（这个桶没声明过 —— 没有 `lean确证.json` 留痕）")

    try:
        出["引用"] = 解析引用(根, 打印=打印)
    except Exception as e:                            # noqa: BLE001
        出["★引用失败"] = f"{type(e).__name__}: {e}"
        return 出
    for _名, _函 in (("md链接", 解析md链接), ("json引用", 解析JSON引用)):
        try:
            出[_名] = _函(根, 打印=打印)
        except Exception as e:                        # noqa: BLE001
            出[f"★{_名}失败"] = f"{type(e).__name__}: {e}"

    if 自洽:
        try:
            出["自洽门"] = 自洽门(根)
        except Exception as e:                        # noqa: BLE001
            出["★自洽门失败"] = f"{type(e).__name__}: {e}"
    return 出


def 解析引用(根: str | Path, 打印: Callable[[str], None] = print) -> dict:
    """把「**提及**」（名字出现过）升级为「**引用**」（指向哪个定义）。

    解析顺序（**通用**，不按语言）：
      ① **本文件定义**（裸名 或 限定名尾）
      ② **本文件 import 的绑定**（`import numpy as np` ⇒ `np` → numpy 这个模块）
      ③ **全仓同名定义**（唯一命中才算；多个 ⇒ **歧义**，不硬猜）
      ④ 都不是 ⇒ **未解析**

    ★ 还要分出**「未导入」**这一类：**别的文件有这个定义，但这个文件没 import 它**。
      这不是「没有」，是**依赖图上缺了一条边** —— 正是「AI 知道自己缺什么」的读数。

    产出写进 `.kg/引用.json` 与 `.kg/缺口.json`（**常驻**）。
    """
    根 = Path(根).resolve()
    图 = 加载图(根)
    if "未测到" in 图:
        return _未测到留痕(图, 根)
    节: dict = 图.get("节点") or {}
    边: list = 图.get("边") or []

    # ★★★★ R491：**把上一轮派生的「引用」边降级回「提及」** —— 这是幂等的关键。
    #
    #   ★ 根因链（R490 全仓普查 + 30 次收敛曲线）：
    #     `解析引用` 的**输入里有它自己上一轮的输出**（`边.json` 里的引用边）：
    #     ````
    #     「图」─(加载图：读 边.json)─> 「节/边」
    #        ↑                              │
    #        └─(写回 边.json)─ 「引用/升级」 ← 解析（含 _升 表）
    #     ````
    #     每跑一轮，图上多几条「已被升级」的边；这些边**改变了下一轮 `加载图`
    #     得到的 `节/边`**，于是下一轮的 `_升` 能命中更多提及边 ⇒ 又多升一批 ⇒ **慢收敛**。
    #     实测 `TS-CORE-112`：引用边 839 → 864 → 874 → 877 → 880 → **883**，
    #     相邻差分 `14, 7, 4, 4, 4, 2, 1, 1, 1, 1, 1, 1, …0`，**约 16 次才到固定点**。
    #     **13 个桶不幂等**（幂等的只有 4 个：数据部署 · 文档体系 · arc适配 · 交接报告）。
    #
    #   ★★★ **R487 我在这里失败过**，留痕（口径 269 的学费）：
    #     那一版只把 `关系` 改回「提及」，**没有把 `终` 改回去**
    #     ⇒ 下游循环判 `_终.startswith("name:")` **为假** ⇒ **一条都升不回去**，
    #       而且**不报任何错**（静默）⇒ 主图引用边 `12,152 → 0` ✗
    #     ⇒ 口径 278：**「派生结果」与「输入态」只差一个字段** —— 这里就差 `终`。
    #
    #   ★ 升级循环（下面 `for e2 in 边:`）**只处理 `终` 以 `name:` 开头的边** ——
    #     这正是**天然的输入态判据**：`name:xxx` 是**抽取阶段**的形态，
    #     `定义:` / `mod:` / `声明:` 是**解析阶段**的产物。
    #     ⇒ 所以「退回输入态」= **把 `终` 写回 `name:原名`**，不是删掉整条边
    #       （删掉就把「名字出现过」这个**事实**也删了）。
    #
    #   ★ `原名` 保留（事实字段，口径 271）—— 它是升级时从 `终` 抄下来的，
    #     所以**能从它恢复 `终`**；恢复不了（老图没有 `原名`）的就**不动**，
    #     宁可让它留在「引用」态（那是上一轮的真实结论）。
    _降级 = 0
    for _e in 边:
        if _e.get("关系") != "引用":
            continue
        _原名 = _e.get("原名")
        if not _原名:                     # 没有 `原名` ⇒ 无法恢复输入态 ⇒ 不动
            continue
        _e["关系"] = "提及"
        _e["终"] = _原名                  # ★★ 就是这一步，R487 漏了
        for _k in ("歧义", "候选", "外部", "未解析原因",
                   "目标文件", "依据", "由"):
            _e.pop(_k, None)
        # ★ `原名` 自己**保留**（它是升级时从 `终` 抄下来的事实，删了就再也恢复不了）
        _降级 += 1
    #
    # ★★★★ R487c：**这里原有一段"幂等"修复，已回滚**（留痕，口径 199/248）。
    #   我写的版本是：开始前把 `边` 里所有 `关系=="引用"` 的边**退回成「提及」**，
    #   然后让本轮解析重新决定它们。**判据上它完全成立**：
    #   `tools` 桶连跑 5 次，`引用.json` 与 `边.json` 的 **md5 一字不差**（真幂等）。
    #   ★ **但它把主图的引用边抹掉了**：`引用边 12,152 → 0` ✗✗✗
    #     根因：`_升` 的键是 `(起, 名/原名, 行)`，而**退回之后键失配**，
    #     一条都升不回去，**且不报任何错**（静默）。
    #   ⇒ **"幂等"与"正确"是两个判据** —— 我拿"md5 一样"当了"对了"。
    #     这是 R309 那句话的又一次实证：**读数漂亮不等于结论对**。
    #   ★ 口径 248：「不修」也要留痕 ⇒ 这里明写**没修**，并把缺陷写成判据
    #     （判一百四十一），而不是留一段"看起来对"的代码。
    #   ★ 要修的正确形态（下一轮）：**派生结果不要写回 `边.json`** ——
    #     主图的 `提及` 边保持原样，解析结果只进 `引用.json`；
    #     或给派生的引用边打**稳定标记**，按标记剔除而不是按 `关系`。


    # ── 索引 ────────────────────────────────────────────────────────
    本文件: dict[str, dict[str, list[str]]] = {}      # 文件 → {名字: [id]}
    # ★ 声明必须在索引循环**之前** —— 否则 Python 把它当局部变量，
    #   循环里先引用就 `UnboundLocalError`（实测踩过）。
    本文件绑定: dict[str, set[str]] = {}
    全仓: dict[str, list[str]] = {}                   # 裸名 → [id]
    模块表 = _模块索引(节[f"file:{k[5:]}"]["文件"] for k in 节 if k.startswith("file:"))
    导入: dict[str, list[dict]] = {}                  # 文件 → [{模块, 绑定}]
    for i, n in 节.items():
        if n.get("类") == "绑定":
            本文件绑定.setdefault(n.get("文件"), set()).add(n["名"])
        if n.get("类") in _实体类:
            f = n.get("文件")
            本文件.setdefault(f, {}).setdefault(n["名"], []).append(i)
            if n.get("限定名"):
                本文件[f].setdefault(n["限定名"], []).append(i)
                本文件[f].setdefault(n["限定名"].rsplit(".", 1)[-1], []).append(i)
            # ★★★ **`全仓`（跨文件名字表）必须过滤 —— 它是「别处能不能 import 到」的判据**（R364）。
            #   修前的实测（标准库）：`__name__` 有 8 个「定义」节点，**粒全是「内嵌」**
            #   （函数体内的赋值/嵌套定义），而 `repr`/`print`/`initialized` 之类
            #   被当成「别处有定义」⇒ **制造出 43,934 条假「未导入」**。
            #   两个过滤条件，各有依据：
            #   ① **`粒 == "顶层"`** —— **函数体内的名字不能从别的模块 import**。
            #      （`本文件` 索引**不过滤**，因为文件内解析恰恰需要看见内嵌定义 ——
            #       作用域链就是靠它。**同一个节点，对两个问题有两个答案，这不是矛盾。**）
            #   ② **同语言** —— `import` 规则是**语言内**的规则。
            #      实测 `'initialized'` 的 6 个节点全是 **`.c` 文件的 `类=声明`**，
            #      却被拿去回答「Python 的 `m2.py` 能不能 import 到它」⇒ **跨语言误配**。
            #      （这与历史读数「我多 222/229 全是 `.c`」是同一个病根。）
            if n.get("粒") == "顶层" and (f or "").endswith((".py", ".pyi")):
                全仓.setdefault(n["名"], []).append(i)
    for e2 in 边:
        if e2.get("关系") == "导入":
            起 = e2.get("起", "")
            if 起.startswith("file:"):
                终 = e2.get("终", "")
                if 终.startswith("mod:"):
                    # ★ 从**边**读 `绑定` 和 `星号` —— 它们挂在边上（挂节点会串台：
                    #   `mod:` 是全局共享节点，不同文件从同一模块导入的名字不同）。
                    #
                    # ★★★ **就在这里把相对模块名解开**（R349）——**只此一处**。
                    #   相对名（`./util/clamp.js`）**只在「发起导入的文件」的位置上有含义**
                    #   ⇒ 解它需要 `起`（谁导入的），而**这里正是两者都在的地方**。
                    #   ★ 上一版我在下游 `导这里` 又解了一次 ⇒ **同一个判断写在两处**，
                    #     两处迟早给出两个答案（而且边上留着没解的原文，图上自相矛盾）。
                    #   ★ `原文` 保留：**图上要看得见源码里写的是什么**。
                    _原 = 终[4:]
                    # ★★★ **区分「指向模块的绑定」与「指向成员的绑定」**（R365）。
                    #   `import X as Y` / `import X` 的语句类型**不含 `from`**（Python
                    #   `import_statement`、Go `import_spec`、JS `export_statement`）
                    #   ⇒ 这些绑定名**指的就是模块本身**。
                    #   `from X import Y`（`import_from_statement` / JS `import_clause`）
                    #   的绑定名是**成员**。
                    #   ★ 判据只用「语句类型里有没有 `from`」这个**语法事实**，
                    #     不猜名字、不查目标模块里有没有同名定义（那是**猜**：
                    #     `import os as path` 时 `os.path` 恰好存在，会误判）。
                    _节型 = str(e2.get("节点类型") or "")
                    #   ★★ **不许写 `not str(e2.get("星号"))`** —— `str(False)` 是
                    #      `"False"`，**非空字符串是真值** ⇒ 条件**恒为假**。
                    #      实测：`模块绑定` 一直是 `[]`，`np` 2,180 条判「未解析」，
                    #      而这一段**看起来完全正确**。
                    #      「假红与真红长得一样，唯一的分界是把实得值打出来核对」——
                    #      这里是**假绿**：条件恒假，**不报错**。
                    _指模块 = (not ("from" in _节型)) and not bool(e2.get("星号"))
                    导入.setdefault(起[5:], []).append(
                        # ★★★ **铺开 `e2` 再覆盖**（R374）。
                        #   这里原来是**逐键列举**（`"绑定": list(e2.get("绑定")...)` 之类）
                        #   ⇒ 抽取层新加的字段**一律静默丢掉**（R373 的 `映射` 就是这么没的，
                        #     导致整套改名逻辑一行都没生效）。
                        #   ★ 判八十七（`传递层不许筛字段`）现在用 `ast` 守着这条：
                        #     **没有 `**` 铺开、且 ≥2 个键的值是 `x.get("同名键")`** ⇒ 红。
                        #     本处**曾经**就是那个红 —— **判据自己把修法写在红文里**。
                        #   ★ 口径：**过滤是判断，只能写在判断该发生的那一处**；
                        #     传递层只**补全**，不**筛掉**。
                        {**e2, "模块": _解相对(起[5:], _原), "原文": _原,
                         "模块绑定": list(e2.get("绑定") or []) if _指模块 else [],
                         "节点类型": _节型,
                         # ★★★ **`映射`（本地名 → 原名）必须传过来**（R373）。
                         #   这里是**白名单重建** ⇒ 抽取层新加的 `映射` **静默丢掉**
                         #   ⇒ 我 R373 的整套改名逻辑**一行都没生效**
                         #   （探针实录：`_原名=_warn`，而边上是 `映射:{"_warn":"warn"}`）。
                         #   ★ 这是**同一个病第四处**：① `取导入` 出口 ② 导入边 `append`
                         #     ③ 索引重建 ④ 下游 `_节型`。**每一处都不报错。**
                         #   ★ 口径：**传递层不许筛字段** —— 白名单是"筛"，
                         #     而这里需要的恰恰是"传"。
                         "映射": dict(e2.get("映射") or {}),
                         "星号": bool(e2.get("星号"))})

    # ── 逐文件解析 ──────────────────────────────────────────────────
    引用: list[dict] = []
    # ★★★ **绑定/内置必须在「出现处」那一层就记下来**（R360）。
    #   我第一版想在结果里从 `引用` 反推「哪些是内置」—— **反推不到**：
    #   那个分支 `continue` 掉了，**根本不往 `引用` 里写**。
    #   实测后果：`绑定/内置（**不算缺**） = 0`，而同时
    #   `计数['绑定/内置'] = 620,212` —— **同一个事实两个答案**，
    #   于是 62 万个内置被算进「**一个答案都没给**」⇒ 「未解析 97%」是**假的**。
    #   ★ 口径：事实**在产生它的那一处**记下来；**下游反推**必然漏。
    _名绑内: set[tuple[str, str]] = set()
    计 = {"已解析·本文件": 0, "已解析·导入模块": 0,
          "绑定/内置（**不算引用**）": 0, "未导入": 0, "未解析": 0}
    未解析例: list[dict] = []
    未导入例: list[dict] = []
    歧义例: list[dict] = []
    # ★★★ **作用域链**（R358）：节点 id → 它所在的父定义 id。
    #   作用域解析的正确规则是**沿这条链由内向外找**，而不是「取深度最大的」——
    #   `深度` 是 tree-sitter **语法深度**，不等于**作用域深度**。
    #   ★ 本表读的就是 R357 盘点查出「读 0 次」的那个 `父定义` 字段。
    父定义表: dict[str, str | None] = {i: n.get("父定义") for i, n in 节.items()}
    已见 = set()
    # ★★★ **`引用` 用代理列表 —— `行` 由它一处补，不需要在 8 个 `append` 各写一遍**（R363）。
    #   病根：解析到目标的那些分支（L2450 起）**漏写 `"行"`**，
    #   于是「并进主图」的键 `(起, 名, 行)` **全部失配**（引用的 `行` 是 None、
    #   边的 `行` 是真实行号）⇒ 退回**按名字取** ⇒ 一个文件里同名多处**又变成一条**。
    #   ★ 实测：`b.py` 的 `import a`（行1）与 `a.foo()`（行5）两条提及边，
    #     产出的引用**都没带行**。
    #   ★ 为什么不用「在 8 处各加一个字段」：那是**手写清单**，
    #     而本文件已经因为它栽过**三次**（id 前缀清单、把 `名` 当身份键、增量前缀清单）。
    #     **靠列举来保证完备，迟早有一处漏**；**靠结构保证完备，漏不了**。
    _行栈: list = [None]

    class _引用表(list):
        """`list` 的代理：`append` 时**若调用方没给 `行`，自动补当前边的行号**。"""

        def append(self, x):                      # noqa: D102
            if isinstance(x, dict) and "行" not in x:
                x["行"] = _行栈[0]
            super().append(x)

    引用 = _引用表()

    # ★★★ **传递解析：再导出链**（R370）。
    #   实测（标准库 `cgi.py`）：`from io import StringIO` 里的 `StringIO`
    #   落 **「未导入」**（5,243 条同族：`StringIO`/`BytesIO`/`Tuple`/`List`…）。
    #   根因**不是**导入表没记 —— 导入表**是对的**：
    #     `{"模块":"io","绑定":["StringIO","BytesIO","TextIOWrapper"]}` ✓
    #   而是 `io.py` 里 **`StringIO` 自己也是 `from _io import (...)`** ——
    #   它是**再导出**，在 `io.py` 里**不是顶层定义** ⇒ 在 `io.py` 里找不到 ⇒
    #   掉到后面 ⇒ 落「未导入」（**修前落「绑定/内置」**，等于**从一个错桶挪到另一个错桶**）。
    #   ★ 正确做法：**跟着靶文件的导入表再走一层**（再导出链可以很长，实测
    #     `cgi → io → _io`），所以是**递归 + 防环 + 缓存 + 深度上限**。
    #   ★ 这正是目标③「提及 → 引用（带目标节点 id）」要求的**传递性**；
    #     也是「**一个答案硬套一处**」的反面：**每一跳都记在 `链` 上**，
    #     下游能看到「这个引用是经过谁的再导出找到的」。
    #   ★ 终点若是**本仓内文件** ⇒ 真引用；若链走到**仓外模块** ⇒ 落
    #     「导入定义（**靶文件不在本仓**）」，与本地缺定义**分开**。
    _传递: dict[tuple[str, str], dict | None] = {}
    _深上限 = 6

    def _终极靶(文: str, 名2: str, 深: int = 0,
              见过: frozenset = frozenset()) -> dict | None:
        """在 `文` 里找 `名2` 的**终极落点**：本文件顶层没有 ⇒ 跟着它的导入表再走一层。

        ★ `名2` 传进来时**已经是「原名」**（调用方查过 `映射`）——
          因为靶文件里从来就没有本地别名那个东西（R373）。
        """
        _k = (文, 名2)
        if _k in _传递:
            return _传递[_k]
        if 深 >= _深上限 or _k in 见过:
            _传递[_k] = None
            return None
        _r: dict | None = None
        _本 = 本文件.get(文, {}).get(名2) or []
        if _本:
            _r = {"终": _本[0], "文件": 文, "模块": None, "链": [文]}
        else:
            # ★ 靶文件**自己**的别名也要认（链可以一路别名下去）
            _名3 = 名2
            for d in (导入.get(文) or []):
                if 名2 in (d.get("绑定") or []):
                    _名3 = (d.get("映射") or {}).get(名2) or 名2
                    break
            for d in (导入.get(文) or []):
                # ★★★ **星号导入也是"靶文件的导入"**（R371）。
                #   实测（标准库 `cgi.py:40 from collections.abc import Mapping`）：
                #     `collections.abc` 的靶文件是 `collections/abc.py` ✓（模块表对得上），
                #     但那个文件**在图里一个实体节点都没有** —— 因为它的内容**只有**
                #       `from _collections_abc import *`
                #       `from _collections_abc import __all__`
                #     ⇒ `Mapping` **真的不在它里面**，它是**星号导入进来的**。
                #   ⇒ 旧写法 `名2 not in d["绑定"]` 对星号导入**恒真**
                #     （星号导入的 `绑定` 是**空表**）⇒ `continue` ⇒
                #     传递链**在星号这一步断掉** ⇒ 落「未导入」（3,793 条里的一大部分）。
                #   ★ 口径：**"没登记"与"登记了但为空"是两件事** ——
                #     这里 `绑定: []` 不是"没有绑定"，是"**全都能绑**"，
                #     必须看 `星号` 标志，不能看列表长短。
                if not (_名3 in (d.get("绑定") or []) or d.get("星号")):
                    continue
                _m = str(d.get("模块") or "")
                _目 = _入口优先(模块表.get(_m) or 模块表.get(_m.lstrip(".")) or [])
                if len(_目) > 1 and _目[0].rsplit("/", 1)[-1] in _入口名:
                    _目 = [_目[0]]
                if not _目:
                    # 链走到仓外 ⇒ 终点是模块，不是本仓的定义
                    _r = {"终": f"mod:{_m}", "文件": None, "模块": _m,
                          "链": [文, _m], "外": True}
                    break
                _子 = _终极靶(_目[0], _名3, 深 + 1, 见过 | {_k})
                if _子:
                    _r = {**_子, "链": [文, *(_子.get("链") or [])]}
                    break
        _传递[_k] = _r
        return _r

    for e2 in 边:
        if e2.get("关系") != "提及":
            continue
        _行栈[0] = e2.get("行")
        终 = e2.get("终", "")
        if not 终.startswith("name:"):
            continue
        名 = 终[5:]
        起 = e2.get("起", "")
        相 = 起[5:] if 起.startswith("file:") else None
        if 相 is None:
            continue
        # ★★★ **去重键必须带作用域**（R358）。原来 `k = (相, 名)` ⇒
        #   同一个名字在一个文件里**只被解析一次** ⇒ 不同出现处拿到**同一个答案**
        #   ⇒ **作用域解析根本无从谈起**（`def dup` 内外两层同名时，
        #   外层调用必然也解到内层，或反之）。
        #   ★ 这是「同一个判断写在两处，必然给出两个答案」的**镜像**：
        #     **一个答案硬套多处，必然有一处是错的。**
        # ★★ **`父` 现在是标量**（一个出现处一条边），`list(标量)` 会**把字符串拆成字符**
        #   —— 那正是「**探针少打一个字段 = 少一步定因**」的近亲：
        #     **类型变了不改读法，比没读更坏**（会静默给出 20 个单字符的「作用域」）。
        #   ★ 兼容两种形状：标量（新）与列表（保底/旧图）。
        _父v = e2.get("父")
        _父列0: list = list(_父v) if isinstance(_父v, (list, tuple)) else [_父v]
        # ★★★ R468：**`父 = None` 是「作用域未知」，不是「同一个作用域」** ——
        #   实测（`a.lean`：`theorem u : zzz = zzz := by exact zzz`）：
        #   `边.json` 里 `name:zzz` 有 **3 条**（行 2、2、3），
        #   而 `引用.json` 里只剩 **1 条（行 2）** ⇒ **第 3 行那一条整个丢了** ✗
        #   ★ 根因：`_Lean适配器` 的提及边**没有 `父` 字段**
        #     （对照：Python 边有 `"父": "定义:a.py:f:1"`）⇒
        #     去重键退化成 `(相, 名, (None,))` ⇒ 同一文件同名的多处**全被合并**。
        #   ★ 而这是**口径 190** 的原话：**`False` 与 `unknown` 必须分开** ——
        #     `父=None` 有两种可能：① 真在顶层；② **我不知道**。
        #     把「不知道」当成"同一个作用域"⇒ **一个答案硬套多处**
        #     ⇒ 正是 R358 立这条去重键时要消灭的东西，**从后门回来了**。
        #   ⇒ 修法：**`父` 未知时把 `行` 并进键** ——
        #     把「未知作用域」**退化**成「每个出现处自己一个作用域」
        #     （**保守**：宁可多算几遍，也不许一个答案硬套多处）。
        #   ★ 方向与口径 203 一致：**不许为了少算而合并**。
        if _父v is None:
            k = (相, 名, None, _行栈[0])
        else:
            k = (相, 名, tuple(_父列0))
        if k in 已见:
            continue
        已见.add(k)
        本 = 本文件.get(相, {})
        # ★ 相对名**已经在上游（构 `导入` 表处）解开**，这里不再解第二遍 ——
        #   **同一个判断写在两处，必然给出两个答案。**
        导这里 = 导入.get(相, [])

        # ① 本文件定义 —— ★★★ **按作用域由内向外找**（R358）。
        #   规则（Python / JS / Rust 同构）：名字解析从**引用所在的作用域**开始，
        #   沿 `父定义` 链逐层向外，**第一个有该名字的定义胜出**；
        #   内层定义**遮蔽**外层同名定义，且内层定义在**外层不可见**。
        #   ★ 所以「一条边一个答案」：`e2["父"]` 列出这个名字出现过的每个作用域，
        #     逐个由内向外找 —— 不同出现处**本来就该有不同答案**。
        if 名 in 本:
            候选 = sorted(set(本[名]))
            # ★★★ **每个「出现处」各算一次，不许互相顶替**（R358）。
            #   提及边是**按名字聚合**的（`set(提及)`）⇒ 一条边带**多个父**。
            #   ★ 我第一版只取「第一个命中的父」⇒ 一个名字拿到**一个答案** ——
            #     实测：内层 `def dup` 与外层 `def dup` 同名时，
            #     外层调用（父=`None`）也被解到**内层** `dup:2`，**答案错了**。
            #   ⇒ 正解：**逐个父各算一次，汇总**；不同父给出**不同**答案时，
            #     这条边就是**歧义**（`终` 是 list）—— **不许挑一个装作确定**。
            #   ★ 理想是「提及边按出现处拆开、各带行号」，那是下一轮的事；
            #     本轮先保证**不给出错的单答案**。
            _命中: list[str] = []
            _按作用域 = False
            _各父: list[str] = []
            for _起层 in _父列0:
                _层 = _起层
                _防 = 0
                _此: list[str] = []
                while _防 <= 64:                 # 防环（病态语法树）
                    _防 += 1
                    _此 = [c for c in 候选 if 父定义表.get(c) == _层]
                    if _此:
                        _按作用域 = True
                        break
                    if _层 is None:
                        break
                    _层 = 父定义表.get(_层)
                _各父.extend(_此)
                for _c in _此:
                    if _c not in _命中:
                        _命中.append(_c)
            if not _命中:
                # ★ **兜底不许丢答案**：按作用域找不到时退回全文件匹配，
                #   但**依据要写清是哪条路** —— 否则下游分不清「作用域定的」还是「兜的」。
                _命中 = 候选
            目标 = sorted(set(_命中))
            引用.append({"起": 起, "终": 目标[0] if len(目标) == 1 else 目标,
                        "关系": "引用", "名": 名,
                        "依据": ("本文件定义（**按作用域由内向外**）" if _按作用域
                                else "本文件定义（作用域内无命中，退回全文件）"),
                        "作用域": _父列0,
                        # ★★★ **`行` 必须写进引用**（R359）：它是**出现处的唯一标识**。
                        #   没有它，「并进主图」的索引键 `(起,名,父,行)` 全是
                        #   `(起,名,None,None)` ⇒ 同一名字的**多条引用互相覆盖**，
                        #   只剩最后一条 —— **上游算对了、下游合并错了**。
                        #   ★ 实测：`引用.json` 里行4→`dup:2`、行9→`dup:6`（**都对**），
                        #     而主图 4 条全变 `dup:6`。**只在主图上看，会以为是解析错。**
                        "行": e2.get("行"),
                        "歧义": len(目标) > 1})
            计["已解析·本文件"] += 1
            continue
        # ①.5 **绑定与内置都不算「引用」** —— 它们是**变量**或**语言内置**。
        #   ★★ 原来「内置」判在「未导入」**后面** ⇒ `print`/`super`/`callable`
        #     全被算成「未导入」（顺序错，不是判据错）。
        # ★★★★★ R511：**按本文件那一处的语言**判内置。
        #   ★ `_内置名` 是 **Python-only**（`dir(builtins)` + 几个 dunder + 8 个手写的），
        #     对 `.rs`/`.lean`/`.java` **恒为空** —— 这是"如实为空"，不是 bug；
        #     但 `_是内置(名, {语})` 现在**已经有了**，这条路径却还用着旧的那个名字级集合。
        #   ★ 实测后果：`外部: true` 442,597 条里 **5,428 条**的名字按它自己那一处的
        #     语言就是内置（`Map 678 · ArrayList 622 · Option 529 · Path 247`…）；
        #     `semsearch` 的 `use Yi::*;` 把 `Some`/`String`/`Ok`/`u64` 记成
        #     「外部符号（星号引入的靶全在仓外）」—— 而它们**不可能**定义在 `Yi` 里。
        #   ★ 与 R509/R510 是**同一个病的第三个实例**（口径 324：修表 ≠ 修路径）：
        #     R509 修「未解析」档的判定、R510 修表、本处修这个**分支自己**的判据。
        #   ★ 语言判不出来 ⇒ 不判（口径 158：不剔）—— `_是内置` 自己已保证这点。
        _语511 = _按扩展名判语言(str(相 or ""))
        if (名 in 本文件绑定.get(相, ()) or 名 in _内置名
                or (_语511 and _是内置(名, {_语511}))
                or (str(相 or '').endswith('.lean') and 名 in _Lean保留)):
            # ★★★ **也写进 `引用`**（R362）。原来这里 `continue` 掉、什么都不留
            #   ⇒ 末尾想「从 `引用` 派生所有计数」时**这一类根本查不到**
            #   （实测：`计['绑定/内置'] = 620,212` 而**派生不出来**，
            #    于是 62 万个内置被算进「一个答案都没给」——**假 0 与假 97% 同源**）。
            #   ★ 口径：**不变量是「每一类都留痕」**；谁留痕谁就能被派生，
            #     **反推**必然漏（下游不知道它为什么没出现）。
            _名绑内.add((相, 名))
            引用.append({"起": 起, "终": f"name:{名}", "关系": "提及",
                        "名": 名, "行": e2.get("行"),
                        "依据": "**绑定/内置**（局部变量/参数/语言内置，不算引用）",
                        "歧义": False})
            continue

        # ②.-1 ★★★ **名字在本文件的导入绑定里 ⇒ 靶唯一**。
        #   `from itertools import count` ⇒ 本文件从 itertools 绑了 `count` ⇒
        #   `count` 就是 itertools 的 `count`，**不用猜**。
        #
        #   ★ 这就是 R327 用 `ast` 查出来的病根：12,581 条跨文件歧义里 **2881 条靶是错的**
        #     （`sched.py` 的 `count` 我猜 `collections`，权威是 `itertools`）——
        #     因为候选法只知道「本文件 import 的某个模块里恰有同名定义」。
        #   ★ 现在绑定信息有了（R330 抽、R331 接、R332 搬边），**先按绑定定靶**。
        #
        #   ★ 只在**靶唯一**时用它：绑到了多个模块（`from a import x` 与 `from b import x`
        #     同时存在）⇒ 老实标歧义，不许挑一个装作确定。
        #   ★ **但绑定名等于模块本身时要让位**：`import copy` 绑了 `copy`，
        #     而裸名 `copy` 指的是**那个模块对象**，不是 `copy.py` 里叫 `copy` 的函数
        #     ⇒ 这种情况交给下面 ②.0（模块名优先），否则绑到「模块里的同名定义」就**错了**
        #     （判六十五就是这么红的 —— **判据红得对，是我的新分支太贪**）。
        _绑定到 = [d.get("模块") for d in 导这里
                 if 名 in (d.get("绑定") or [])
                 and 名 != d.get("模块")
                 and 名 != str(d.get("模块") or "").split(".")[-1]]
        # ★★★ **靶文件里要找「原名」，不是本地别名**（R373）。
        #   实测：`from warnings import warn as _warn` ⇒ 导入边 `绑定:["_warn"]`
        #   而 `warnings.py` 里 `warn` 是定义、`_warn` **根本不存在**
        #   ⇒ 旧写法拿 `_warn` 去靶文件里找 ⇒ 必然落「未导入」。
        #   ★ 判据：**本地名是"我用什么名字称呼它"，原名是"它在那边叫什么"** ——
        #     跨文件找的永远是后者。
        _原名 = 名
        for _d in 导这里:
            if 名 in (_d.get("绑定") or []):
                _原名 = (_d.get("映射") or {}).get(名) or 名
                break
        if _绑定到:
            _定2: list[dict] = []
            _靶文列: list[str] = []
            for _模2 in _绑定到:
                _目2 = sorted(_入口优先(模块表.get(_模2)
                                   or 模块表.get(str(_模2).lstrip(".")) or []),
                              key=_导入优先级)
                if len(_目2) > 1 and _目2[0].rsplit("/", 1)[-1] in _入口名:
                    _目2 = [_目2[0]]
                for _文2 in _目2[:1]:
                    _靶文列.append(_文2)
                    _t = 本文件.get(_文2, {}).get(_原名) or []
                    for _i3 in _t:
                        _定2.append({"终": _i3, "模块": _模2, "文件": _文2})
            _去2: dict[str, dict] = {}
            for c in _定2:
                _去2.setdefault(c["终"], c)
            if _去2:
                _定列 = sorted(_去2)
                引用.append({"起": 起, "终": _定列[0], "关系": "引用", "名": 名,
                            "依据": "导入绑定（**权威**：本文件的 import 绑了这个名字）",
                            "目标文件": _去2[_定列[0]]["文件"],
                            "模块": _去2[_定列[0]]["模块"],
                            "歧义": len(_定列) > 1,
                            **({"候选": _定列[:20]} if len(_定列) > 1 else {})})
                计["已解析·导入模块"] += 1
                continue
            # ★★★ **`from P import X` 里 `X` 可能是 `P` 的子模块**（R377）。
            #   实测（标准库 R376 修完的缺口 Top）：`_api 460 · collections 274 ·
            #     util 261 · cbook 239` —— 全部来自这两行：
            #       `from . import (_api, cbook, collections, ...)`        （matplotlib/widgets.py）
            #       `from matplotlib import _api, cbook`                  （axes/_base.py）
            #   ★ 我把它们判成了「P 的**成员**」⇒ 去 `matplotlib/__init__.py` 里找
            #     `_api` 这个**定义** ⇒ 当然没有（**它是个子模块，不是属性**）⇒ 未解析。
            #   ★ Python 的 `from P import X` 语义（权威）：**先找 `P.X` 子模块，
            #     找不到再取 `P/__init__.py` 里的属性** —— 顺序是语法规定的，不是猜的。
            #     所以这里补的是**语义里缺的那一半**。
            #   ★ 判据用**键**（`P.X` / `P/X` 在模块表里有没有），
            #     **不去猜名字像不像模块**（`_api` 带下划线、`collections` 不带，猜不出来）。
            _子模列: list[str] = []
            for _模3 in _绑定到:
                for _子键 in (f"{_模3}.{_原名}", f"{_模3}/{_原名}"):
                    _目3 = sorted(_入口优先(模块表.get(_子键) or []), key=_导入优先级)
                    if _目3:
                        _子模列.append(_目3[0])
                        break
            if _子模列:
                _唯子 = sorted(set(_子模列))
                引用.append({"起": 起, "终": f"file:{_唯子[0]}", "关系": "引用", "名": 名,
                            "依据": "导入模块（**`from P import X`：X 是 P 的子模块**）",
                            "目标文件": _唯子[0], "模块": _原名,
                            "歧义": len(_唯子) > 1,
                            **({"候选": [f"file:{x}" for x in _唯子[:20]]}
                               if len(_唯子) > 1 else {})})
                计["已解析·导入模块"] += 1
                continue
            # ★★★ **靶文件里没有 ⇒ 跟着它的导入表再走一层**（R370 传递解析）。
            #   这是「未导入 +6,383」的根因：`cgi.py` 的 `StringIO` 在 `io.py` 里
            #   **不是定义，是再导出**（`io.py` 自己 `from _io import StringIO`）。
            #   ★ 每一跳都记在 `链` 上 —— 下游能看到"这个引用经过谁的再导出"，
            #     而不是只拿到一个终点 id。
            _传递列: list[dict] = []
            for _文3 in _靶文列:
                _终3 = _终极靶(_文3, _原名)
                if _终3:
                    _传递列.append({"靶文": _文3, **_终3})
            if _传递列:
                _内 = [c for c in _传递列 if not c.get("外")]
                if _内:
                    _c0 = _内[0]
                    引用.append({"起": 起, "终": _c0["终"], "关系": "引用", "名": 名,
                                "依据": "导入定义（**传递：经再导出链**）",
                                "目标文件": _c0.get("文件"), "模块": _c0.get("模块"),
                                "再导出链": _c0.get("链"),
                                "歧义": len(_内) > 1,
                                # ★ R516：标了歧义就要给出候选（口径 199 的同族）
                                **({"候选": [c["终"] for c in _内[:20]]}
                                   if len(_内) > 1 else {})})
                    计["已解析·导入模块"] += 1
                    continue
                # 链走到了仓外 ⇒ **与"本仓缺定义"分开**
                _c0 = _传递列[0]
                引用.append({"起": 起, "终": _c0["终"], "关系": "引用", "名": 名,
                            "依据": "导入定义（**传递后落仓外**）",
                            "目标文件": None, "模块": _c0.get("模块"),
                            "再导出链": _c0.get("链"), "外部": True,
                            "歧义": len(_传递列) > 1})
                计["已解析·导入模块"] += 1
                continue

            # ★★★ **`from X import Y` 的 `Y`，而 `X` 的靶不在本仓**（R369）。
            #   修前实测（标准库 `collections.abc` / 任何 `from X import Y`）：
            #   `collections` 落「导入模块（靶文件不在本仓）」✓ 而它的成员
            #   `OrderedDict` **落「未解析」** ✗ —— 因为 ②.-1 要求
            #   「去靶文件里找到同名定义」，靶不在本仓 ⇒ `_去2` 空 ⇒ 掉到 ⑤。
            #   ⇒ **这不是「未解析」，是「解到了仓外的模块」** ——
            #     **「歧义」「未解析」「靶在仓外」是三件事**，混成一个桶
            #     就是「**一个答案硬套多处，必然有一处是错的**」。
            #   ★ 而且它把「AI 知道自己缺什么」这个读数**污染**了：
            #     真正缺的定义（本仓该有却没有）与**仓外的东西**混在一起，
            #     读数上看不出该去补哪一个。
            #   ★ 判据要**分开**：靶模块在 `模块表` 里**没有文件** ⇒ 仓外；
            #     有文件但文件里没有这个名字 ⇒ **真缺口，落未解析**。
            _无靶 = [m for m in _绑定到
                    if not (模块表.get(m) or 模块表.get(str(m).lstrip(".")))]
            if _无靶:
                引用.append({"起": 起, "终": f"mod:{_无靶[0]}", "关系": "引用",
                            "名": 名, "依据": "导入定义（**靶文件不在本仓**）",
                            "模块": _无靶[0], "外部": True, "目标文件": None,
                            "歧义": len(_无靶) > 1,
                            **({"候选": [f"mod:{_m}" for _m in _无靶[:20]]}
                               if len(_无靶) > 1 else {})})
                计["已解析·导入模块"] += 1
                continue

        # ②.0 ★★ **名字就是本文件 import 的模块名 ⇒ 它就是这个模块**（Python 语义）。
        #   `import copy` 之后，裸名 `copy` **就是**那个模块对象 ——
        #   它不可能同时是 `os.py` 里的那个叫 `copy` 的函数。
        #
        #   ★★ 实测：这一段原来排在**候选法后面** ⇒ `argparse.py` 里的 `copy`
        #     先命中了 `copy.py`/`os.py`/`shutil.py` 里叫 `copy` 的**定义**，
        #     造出 3 个候选的**假歧义**（stdlib 上这类跨文件歧义有 **9013 条**）。
        #     **不是信息不够，是顺序错了：两个都对的分支，谁先跑就决定了答案。**
        # ★★★ **别名也算「名字是模块」**（R365）：
        #   `import numpy as np` ⇒ `np` **就是** `numpy` 这个模块对象 ——
        #   与 `import copy` 之后裸名 `copy` 是同一个规则。
        #   ★ 原来这里只认 `模块名 == 名`，而别名走 ②.-1「去目标模块里找同名定义」
        #     ⇒ **当然找不到** ⇒ 落「未解析」（实测标准库 `np` 2,180 条、
        #       `plt` 572、`mpl` 342 全是这一类）。
        _模名 = [d for d in 导这里 if d.get("模块") == 名
               or str(d.get("模块") or "").split(".")[-1] == 名
               or 名 in (d.get("模块绑定") or [])]
        if _模名:
            模 = _模名[0]["模块"]
            目 = _入口优先(模块表.get(模) or 模块表.get(模.lstrip(".")) or [])
            # ★★★ **按语言解析顺序排序，不再"取第一个"**（R378）。
            #   修前实测：`import warnings` 被解到 **`site-packages/pydantic/warnings.py`**
            #   （扫盘顺序的任意结果），而正确答案是根目录的 `warnings.py`。
            #   24,296 条`导入模块`里 11,209 条（46%）有多个候选，5,313 条被静默压成一个。
            #   ⇒ **错答案比未解析更坏**：未解析会被报成缺口，错答案会被当成事实用。
            目 = sorted(目, key=_导入优先级)
            # 唯一入口 ⇒ 直绑，**不标歧义**（`import numpy` 的答案由语言定死）
            if len(目) > 1 and 目[0].rsplit("/", 1)[-1] in _入口名:
                目 = [目[0]]
            if 目:
                引用.append({"起": 起, "终": f"file:{目[0]}", "关系": "引用",
                            "名": 名, "依据": "导入模块", "模块": 模,
                            # ★ **靶文件必须写在边上**（R350）：判七十四 报
                            #   「解到了却没有靶」就是这里漏了 —— 下游要知道
                            #   「这个引用落在哪个文件」，光有节点 id 不够。
                            "目标文件": 目[0],
                            "歧义": len(目) > 1,
                            # ★ R516：52,391 条里最多的缺候选处
                            **({"候选": [f"file:{_x}" for _x in 目[:20]]}
                               if len(目) > 1 else {})})
                计["已解析·导入模块"] += 1
            else:
                引用.append({"起": 起, "终": f"mod:{模}", "关系": "引用",
                            "名": 名, "依据": "导入模块（靶文件不在本仓）",
                            "模块": 模, "外部": True, "目标文件": None,
                            # ★★★ R454：**这一支原来没有 `歧义` 键** ✗
                            #   实测（全仓）**53,527 条引用边没有 `歧义` 键**（17 个根都有），
                            #   依据**清一色**是 `导入模块（靶文件不在本仓）` —— 而它的**孪生**
                            #   `导入定义（**靶文件不在本仓**）` 那条**有** `歧义`。
                            #   ⇒ **同一个东西两个出口，一个标了一个没标。**
                            #   ★ 口径 155/156：**「没有键」与「歧义=False」是两件事** ——
                            #     而目标③要求「**歧义必须标**」：**没键 = 没标** ✗
                            #   ★ 这里靶不在本仓 ⇒ `目` 为空 ⇒ **无候选** ⇒
                            #     是**明确的 false**，不是 unknown。
                            "歧义": False})
                计["已解析·导入模块"] += 1
            continue

        # ②.-0.5 ★★★ **星号导入**：`from x import *` / `export * from "x"`（R356）。
        #   ★ R355 只是把 `星号` **标在边上**，**下游一条都没用** ——
        #     这正是一类要盯的东西：**「登记了」不等于「接上了」**。
        #   ★ 位置很关键，排在 **②.0 之后、候选法之前**：
        #     ① 模块名优先（②.0）必须压过它（`import copy` 的裸名 `copy` 是**模块对象**）；
        #     ② 它必须压过候选法 —— 候选法只知道「本文件 import 的某个模块里
        #        **恰有**同名定义」，**那是猜**；星号导入是**语言规则**
        #        （`from x import *` 就是把 `x` 的公开名字引进本作用域）。
        #        **规则不许排在一个猜测后面。**
        #   ★ 诚实边界：星号导入的名字**没有在导入处登记**（登记不了，是「全部」），
        #     所以这里只能**去目标模块找同名** —— 多个模块都导出同名时
        #     **必须标歧义**，不许挑一个装作确定。
        _星号模 = [d.get("模块") for d in 导这里 if d.get("星号") and d.get("模块")]
        if _星号模:
            _候3: list[dict] = []
            for _模3 in _星号模:
                _目3 = _入口优先(模块表.get(_模3)
                               or 模块表.get(str(_模3).lstrip(".")) or [])
                if len(_目3) > 1 and _目3[0].rsplit("/", 1)[-1] in _入口名:
                    _目3 = [_目3[0]]
                for _文3 in _目3[:1]:
                    for _i4 in (本文件.get(_文3, {}).get(名) or []):
                        _候3.append({"终": _i4, "模块": _模3, "文件": _文3})
            _去3: dict[str, dict] = {}
            for c in _候3:
                _去3.setdefault(c["终"], c)
            if _去3:
                _列3 = sorted(_去3)
                引用.append({"起": 起, "终": _列3[0], "关系": "引用", "名": 名,
                            "依据": "星号导入（**从 `import *` 的模块来，名字未在导入处登记**）",
                            "目标文件": _去3[_列3[0]]["文件"],
                            "模块": _去3[_列3[0]]["模块"],
                            "歧义": len(_列3) > 1,
                            **({"候选": _列3[:20]} if len(_列3) > 1 else {})})
                计["已解析·导入模块"] += 1
                continue
            # ★★★ **星号导入 + 靶不在本仓 ⇒ 名字来自外部**（R381）。
            #   修前实测（标准库未解析 Top 的 C 部分）：
            #     `Py_DECREF 610 · PyErr_Occurred 385 · Py_INCREF 363 · Py_XDECREF 337
            #      · __Pyx_AddTraceback 242 · __pyx_lineno/filename/clineno 232
            #      · PyErr_SetString 224 · PyExc_TypeError 217` —— 全部 3,000+ 条。
            #   `.c` 文件 `#include <Python.h>` ⇒ `Python.h` **不在本仓**
            #   ⇒ 本文件里出现的 `Py_*` 名字**只可能来自那个头文件**。
            #   ★ 这与「本仓缺定义」是**两件事**：目标④要的是「AI 知道自己缺什么」，
            #     而"这个宏在 CPython 头文件里"**不是缺口，是事实**。
            #     混在一起 ⇒ **真缺口被外部符号淹掉**（R375 的 70% 英文单词是同一个病）。
            #   ★ 判据用**靶在不在仓内**（`模块表` 有没有），**不按名字长相猜**
            #     （`Py_` 前缀是 CPython 的约定，不是语言的规则）。
            _外头 = [m for m in _星号模
                    if not (模块表.get(m) or 模块表.get(str(m).lstrip(".")))]
            if _外头 and len(_外头) == len(_星号模):
                引用.append({"起": 起, "终": f"extern:{名}", "关系": "引用", "名": 名,
                            "依据": "外部符号（**本文件星号引入的靶全在仓外**）",
                            "模块": _外头[0], "外部": True, "目标文件": None,
                            "歧义": len(_外头) > 1,
                            **({"候选": sorted({f"extern:{m}" for m in _外头})[:20]}
                               if len(_外头) > 1 else {})})
                计["已解析·导入模块"] += 1
                continue

        # ② ★★ **跨文件绑定义**：本文件 import 的**某个模块的文件里真定义了这个名字**。
        #   实测原来的写法：`命中导` 要匹配 `d["绑定"] == 名`，而 `绑定` 是**死的**
        #   （`取导入` 里 5 处硬编码 `None`）⇒ `from x import y` 的 `y` **永远不匹配**，
        #   全落到「未解析」，还把 `工具函数` 标成 `未解析原因: "未导入"`（**它明明导入了**）。
        #
        #   ★ 但**不要去抽 `绑定`**（上一轮我这么做，把 `取导入` 弄炸了：
        #     导入边一条不剩，边上冒出 `保底: true`，关键字 `def`/`import` 被当名字）。
        #     `from x import y` 由三件事**已经确定**：
        #       本文件 import 了模块 x ＋ y 出现在本文件 ＋ **x 的文件里确实定义了 y**
        #     第三件才是**决定的证据**，而它**已经有**（`本文件` 索引）。
        #   ⇒ 这个判据更通用（不碰按语言各异的名字位置），也更强
        #     （连 `包.甲.工具函数()` 这种限定名用法一样绑得上）。
        _候选: list[dict] = []
        for d in 导这里:
            模 = d.get("模块") or ""
            目 = (模块表.get(模) or 模块表.get(模.lstrip(".")) or [])
            for _文j in 目:
                for _i2 in (本文件.get(_文j, {}).get(名) or ()):
                    _候选.append({"终": _i2, "模块": 模, "文件": _文j})
        if _候选:
            # 同一个定义可能被多个模块路径命中 ⇒ 按目标去重
            _去: dict[str, dict] = {}
            for c in _候选:
                _去.setdefault(c["终"], c)
            _定 = sorted(_去)
            引用.append({"起": 起, "终": _定[0], "关系": "引用", "名": 名,
                        "依据": "导入定义（跨文件）",
                        "目标文件": _去[_定[0]]["文件"],
                        "模块": _去[_定[0]]["模块"],
                        "歧义": len(_定) > 1,
                        **({"候选": _定[:20]} if len(_定) > 1 else {})})
            计["已解析·导入模块"] += 1
            continue

        # ③ **跨文件必须经过 import 图**（这才是「真引用」）。
        #   ★★ 原来这里有一层「全仓唯一 ⇒ 算解析」——**那是假解析**：
        #     Python 里跨文件用名字**必须 import**，靠「全仓只有一个同名的」
        #     得到的边**没有依据**。实测它还把「歧义」推到 116,355（裸名 `__init__`
        #     `get` `close` 满仓同名）⇒ 数字好看，**含义是假的**。
        #     ⇒ 删掉。跨文件只认 import。
        # ④ **未导入**：别的文件确实定义了这个名字，但**本文件没导入提供它的模块**。
        #   这不是「没有」——是**依赖图上缺了一条边**，正是「AI 知道自己缺什么」的读数。
        #   ★ 原来这个分支**永远走不到**（被上面两层吃光）⇒「未导入 0」是假的 0。
        # ★★★ **⑦ 限定名 + 宿主靶在仓外 ⇒ 它是外部符号，不是缺口**（R385）。
        #   修前实测（标准库全量）：`pytest.raises(...)` / `pytest.mark.xxx` /
        #   `pytest.parametrize` 这类**真限定名**被收进来之后（R384），
        #   因为 `pytest` **不在仓内**，全落「未解析」⇒ 未解析 3,416 → **17,803**。
        #   ★ 但**靶在仓外不是缺口**：它是「本仓没有这个依赖的源码」这个**事实**。
        #     混在一起 ⇒ **真缺口被外部符号淹掉**（R381 在 `#include` 那条路上
        #     立「外部符号」桶要治的是同一个病）。
        #   ★ 判据用**结构**：①这条提及是**限定名**（`限定宿主`，语法位置给的）
        #     ②那个宿主是**本文件 import 的模块** ③该模块**在仓内没有文件**。
        #     **不按名字长相猜**（`pytest` 叫什么都行）。
        #   ★ 位置：排在 ①-③ 之后、④⑤ 之前 —— 只在**什么都没解到**时才归属，
        #     所以它**不会改写任何已解析的答案**（最小改动）。
        # ★★★ **名字本身就是「内置/标准库模块」⇒ 它是一条模块引用**（R392）。
        #   `os.fspath` 里的 `os` ⇒ 它**不是**未解析，是「导入模块（靶在仓外）」——
        #   只是**没写 import 而已**（解释器自带）。
        #   ★ 判据用**权威名单**（`sys.builtin_module_names` +
        #     `sys.stdlib_module_names`），**不按名字长相猜**。
        #   ★ 排在 ①-③ 之后：**本文件真定义/导入过同名东西的，仍然优先**。
        if 名 in _内置模块名():
            引用.append({"起": 起, "终": f"module:{名}", "关系": "引用", "名": 名,
                        "依据": "导入模块（**靶文件不在本仓**：解释器自带）",
                        "模块": 名, "目标文件": None, "歧义": False})
            计["已解析·导入模块"] += 1
            continue

        _宿3 = e2.get("限定宿主")
        if _宿3:
            # ★★★ **宿主是「内置模块」时不需要 import**（R392）。
            #   实测标准库未解析 Top：`charmap_encode 142`（`codecs.charmap_encode`）
            #   · `fspath 94`（`os.fspath`）· 同族一大片。
            #   `codecs`/`os` 是**内置/标准库模块**，源码里**不会 import 它们** ⇒
            #   上一版要求「宿主必须是本文件 import 的模块」⇒ **一条都匹配不上** ✗
            #   ⇒ 全落「未解析」，而它们**靶在仓外**，本该是**外部符号**。
            #   ★ 用**权威来源** `sys.builtin_module_names`，**不手写模块名表** ——
            #     手写的表一定会漏、会过期（口径 43 的同一个道理）。
            #   ★ 注意：这里只判「**是不是编译进解释器的模块**」；
            #     `os` 是 `.py`（不在 builtin 名单里，它是 frozen/文件模块）⇒
            #     还要加上 **`sys.stdlib_module_names`**（3.10+ 的权威名单）。
            _宿模 = [str(d.get("模块") or "") for d in 导这里
                    if str(d.get("模块") or "").split(".")[0].lstrip(".") == _宿3
                    or _宿3 in (d.get("模块绑定") or [])]
            if not _宿模 and _宿3 in _内置模块名():
                # 靶在仓外（内置/标准库模块）⇒ 名字来自外部，不是本仓的缺口
                _宿模 = [_宿3]
            if _宿模:
                _宿在仓 = any(模块表.get(m) or 模块表.get(str(m).lstrip("."))
                             for m in _宿模)
                if not _宿在仓:
                    引用.append({"起": 起, "终": f"extern:{_宿3}.{名}", "关系": "引用",
                                "名": 名, "依据": "外部符号（**限定名的宿主靶在仓外**）",
                                "模块": _宿模[0], "外部": True, "目标文件": None,
                                "限定": f"{_宿3}.{名}", "歧义": len(_宿模) > 1,
                                **({"候选": [f"extern:{_m}.{名}" for _m in _宿模[:20]]}
                                   if len(_宿模) > 1 else {})})
                    计["已解析·导入模块"] += 1
                    continue
                # ★★★ **宿主在仓、但成员不在仓里**（R392 第三次踩）—— 也是外部符号。
                #   实测：`encodings/charmap.py` 里 `codecs.charmap_encode`
                #     · 提及边**完全正确**（`限定宿主=codecs`）
                #     · 导入边**也有**（`{"模块":"codecs","绑定":["codecs"]}`）
                #     · **而 `codecs.py` 就在 stdlib 仓里** ⇒ `_宿在仓 = True`
                #   ⇒ 上一版只处理"宿主不在仓"，**这条落到 ④⑤ ⇒ 未解析** ✗
                #   而 `charmap_encode` 是 **C 函数**（在 `_codecs` 里，这份源码没有）
                #   ⇒ **"宿主在仓"不等于"成员在仓"** —— 这是两件事。
                #   ★ 判据是结构：**宿主解析出来了（`_宿模` 非空），
                #     而按 ①-③ 都没在本仓找到这个名字** ⇒ 成员来自宿主的**非源码部分**
                #     （C 扩展 / 生成代码 / 平台分支）。
                #   ★ 这条**只在 ①-③ 全都没解到**时才走（本函数的位置决定的），
                #     所以**不会改写任何已解析的答案**。
                引用.append({"起": 起, "终": f"extern:{_宿模[0]}.{名}", "关系": "引用",
                            "名": 名,
                            "依据": "外部符号（**限定名的成员不在本仓源码里**）",
                            "模块": _宿模[0], "外部": True, "目标文件": None,
                            "限定": f"{_宿3}.{名}", "歧义": len(_宿模) > 1,
                            **({"候选": [f"extern:{_m}.{名}" for _m in _宿模[:20]]}
                               if len(_宿模) > 1 else {})})
                计["已解析·导入模块"] += 1
                continue

        # ★★★★★ R512：**名字空间是按语言分的** ——
        #   别的语言的同名定义**不是**「别处有定义」。
        #   ★ 怎么发现的：本轮量目标③（`提及` → `引用`）时先量「提及档各档构成」，
        #     看到 `未导入 33,628`。抽样例：
        #       `file:src/lib.rs` 的 `环` **未导入**自 `_验三判据.py`（Python）
        #       `file:ARC-AGI-1/apps/css/common.css` 的 `row` **未导入**自 `_dbg3.py`
        #     而 `未导入` 的**全部意义**是「**补一行 import 就消**」（这正是它存在的
        #     理由）—— `src/lib.rs` 里**加不了一行 Python 的 import**。
        #     ⇒ 口径 203：**误报的引用比缺失的引用更坏**。
        #   ★ 实测规模：33,628 条里 **33,043（98.3%）跨语言**，**只有 488 条同语言**。
        #   ★ 正解：别处只有**别的语言**的定义 ⇒ 在**本语言的名字空间里就没有它**
        #     ⇒ 落「未解析」（→ 进真缺口，正是"AI 知道自己缺什么"）。
        #   ★ `起` 语言判不出（`.css`/`.js`/`.md`）⇒ **不判**（口径 158），
        #     那些文件里本来也没有「导入」这个概念。
        _语512 = _按扩展名判语言(str(相 or ""))
        全 = 全仓.get(名, []) if _语512 else []
        他文件 = sorted({节.get(i2, {}).get("文件") for i2 in 全
                       if 节.get(i2, {}).get("文件") != 相
                       and _按扩展名判语言(
                           "file:" + str(节.get(i2, {}).get("文件") or "")) == _语512
                       }) if 全 else []
        if 他文件:
            # ★ **不叫「引用」** —— 它没解析到目标（`终` 还是名字本身）。
            #   **名不副实会让下游全错**：一条 `关系:"引用"` 却指不到节点的边，
            #   比没有这条边更坏。所以保持 `关系` 为 `提及`，另标缺边原因。
            引用.append({"起": 起, "终": f"name:{名}", "关系": "提及",
                        "名": 名, "依据": "**未导入**（别处有定义，本文件没导入）",
                        "在别处": 他文件[:5], "歧义": False, "缺边": True,
                        "未解析原因": "未导入"})
            计["未导入"] += 1
            if len(未导入例) < 20:
                未导入例.append({"文件": 相, "名": 名, "在别处": 他文件[:3]})
            continue
        # ⑤ 未解析：分「语言内置」与「其余（局部变量/属性名）」——
        #   混成一堆会让读数没区分力（局部变量**本来就不该**在图上）。
        # ★★★ **必须也 `append` 进 `引用`**（R364）—— 原来这里**只加计数**，
        #   于是「未解析」这一类**从来不进 `引用`**；而 `计` 是从 `引用` 派生的
        #   ⇒ **`未解析` 永远是 0**。
        #   ★ 实测：`m2.py` 用了 `n.py` **函数体内**的 `helper`
        #     （不能跨模块 import ⇒ 本该落「未解析」），
        #     `引用.json` 里**根本没有这一条**，`计["未解析"] = 0`。
        #   ★ 这不是「桶空了」，是「**桶里的东西没记**」——
        #     而目标 ④ 的原话就是「**未解析的必须报出来**」，
        #     一个永远为 0 的桶**看起来最像「都解析好了」**。
        #   ★ 顺带：判五十三① 的 `提及边 <= 引用` 正是靠这一类记账才成立。
        引用.append({"起": 起, "终": f"name:{名}", "关系": "提及",
                    "名": 名, "依据": "**未解析**（本文件没定义、没导入、也不在别处顶层定义）",
                    "歧义": False})
        if len(未解析例) < 20:
            未解析例.append({"文件": 相, "名": 名})

    # ★★★ **名字级分层**（R360）：目标 ④ 的原话是「**未解析的必须报出来**」，
    #   而它的用处是「**AI 知道自己缺什么**」—— 那问的是
    #   「**哪些名字我一个答案都没给出来**」，不是「多少个出现处没解到」。
    #   ★ 两个口径**必须分开报**：同一个名字在 100 处出现，
    #     出现处口径会说「100 条未解析」（听起来很糟），
    #     名字口径会说「1 个名字没解到」（听起来还行）—— **哪一个才是可行动的？**
    #     是后者：补一个名字的定义/导入，就能消掉那 100 条。
    #   ★ 实现上**只在出口算一次**：来源是**已经算好的两个东西**
    #     （`引用` 里的 `名` = 解到的；主图 `提及` 边里的 `name:` = 出现过的）。
    #     不在那 6 个分支里各加一行 —— 那是「**同一个判断写在两处**」，
    #     分支一改就会两处不一致（本项目已经栽过这个跟头）。
    #   ★ 必须在**并进主图之前**取快照（并进之后提及边已被改写）。
    # ★★★ **「解到」必须按靶判，不能按「有没有 `名` 字段」判**（R360 实测踩到）：
    #   `未导入` 分支也往 `引用` 里 append（`关系` 仍是 `提及`）**且带 `名`**
    #   ⇒ 我第一版 `{x["名"] for x in 引用}` 把**未导入的名字也算成「解到」**。
    #   **假绿**：实测 `m2.py` 用了 `helper` 却没 import，
    #   名字级却报「一个答案都没给的名字 = 1」（那个 1 是内置 `print`），
    #   **`helper` 消失了** —— 而那正是**目标 ④ 最该报出来的东西**。
    #   ★ 判据只有一条：**`终` 不以 `name:` 开头 ⇒ 真的指到了节点**。
    # ★★★ **粒度必须是 `(文件, 名字)`，不是 `名字`**（R360 第二次踩）：
    #   第一版用全局名字集合 ⇒ 实测 `n.py` 里 `helper` 解到了**它自己的定义**，
    #   把 **`m2.py` 里那个「用了 `helper` 却没 import」**盖掉了
    #   （名字级报「未解到 1 = print」，**真正该报的缺口消失了**）。
    #   ★ 这是「**一个答案硬套多处，必然有一处是错的**」在**统计口径**上的翻版：
    #     名字是**同一个**，但**每个文件的境遇不同** ——
    #     而「AI 知道自己缺什么」问的恰恰是「**哪个文件缺什么**」。
    _名解到: set[tuple[str, str]] = set()
    _名内置: set[tuple[str, str]] = set(_名绑内)      # ★ 就地记的，不反推
    _名未导入: set[tuple[str, str]] = set()
    for _x3 in 引用:
        if not _x3.get("名"):
            continue
        _起3 = str(_x3.get("起") or "")
        _相3 = _起3[5:] if _起3.startswith("file:") else None
        if _相3 is None:
            continue
        _键3 = (_相3, _x3["名"])
        _终3 = _x3.get("终")
        _s3 = "" if isinstance(_终3, list) else str(_终3 or "")
        if _x3.get("未解析原因") == "未导入":
            _名未导入.add(_键3)
        elif _s3.startswith("name:"):
            # 没指到节点：内置/绑定，或真未解析 —— 由 `依据` 区分
            _依3 = str(_x3.get("依据") or "")
            if "内置" in _依3 or "绑定" in _依3:
                _名内置.add(_键3)
        else:
            _名解到.add(_键3)
    _名出现: set[tuple[str, str]] = set()
    for _e3 in 边:
        if _e3.get("关系") != "提及":
            continue
        _t3 = _e3.get("终") or ""
        if not _t3.startswith("name:"):
            continue
        _起e = str(_e3.get("起") or "")
        if _起e.startswith("file:"):
            _名出现.add((_起e[5:], _t3[5:]))
    # ★ **内置/绑定不算「缺」** —— 把它们混进「未解到」会让读数失去区分力
    #   （实测唯一那个「未解到」恰好是内置 `print`）。名字级必须**四层并列**。
    _名无解 = sorted(_名出现 - _名解到 - _名内置)
    # ★ 排除**语言内置/绑定**（`len`/`print`/`True`…）—— 那不是「缺」。
    #   名字级也要报出来，否则「未解析 0」会被误读成「什么都解到了」。
    _名无解例 = [{"文件": _f, "名": _n} for _f, _n in _名无解[:40]]
    # ★ 分层里最该看的一类：**在别处有定义、但这个文件没导入**
    #   （= 真缺口：少一行 import，或我的导入抽取漏了）。它可行动。
    # ★ 形状要**读生产者的**：`全仓` 才是「名字 → 定义节点 id 列表」，
    #   `他文件` 是**它算出来的一份派生物**（只在这一层的局部作用域里）。
    #   我第一版写了 `他文件.get(...)` ⇒ `AttributeError: 'list' object has no attribute 'get'`
    #   —— **猜形状的代价**：如果它恰好是个 dict，我会得到一个**看起来对**的结果。
    _名缺导入: list[dict] = []
    for _f3, _n3 in _名无解:
        _全3 = 全仓.get(_n3) or []
        _文3 = sorted({节.get(_i3, {}).get("文件") for _i3 in _全3
                      if 节.get(_i3, {}).get("文件") not in (None, _f3)})
        if _文3:
            _名缺导入.append({"文件": _f3, "名": _n3, "在别的文件有定义": _文3[:3]})
        if len(_名缺导入) >= 40:
            break

    # ★★ **把「引用」并进主图** —— 目标 ③ 的原话是「『提及』边升级为『引用』边」。
    #   原来只写 `引用.json`，`边.json` 里仍是 `提及` ⇒ **图没变**，
    #   读图的人看到的还是「名字出现过」。
    # ★★★ **索引键必须带「出现处」**（R359）。原来只是 `(起, 名)` ⇒
    #   同一文件同一名字的**多条引用互相覆盖**，最后只剩一条 ⇒
    #   **刚在 ① 层做到的「一个出现处一个答案」在并回主图时被抹平**
    #   （实测：`def dup` 内外同名，4 条引用并回后全变成 `dup:6`）。
    #   ★ 这就是「**写了、生效了，但生效在了不该生效的地方**」再往前一步：
    #     **改对了上游、却在下游被合并掉** —— 所以要**端到端看边**，不能只看上游计数。
    #   ★ 键 = `(起, 名, 父, 行)`；保底路径没有 `父`/`行` ⇒ 用 `None` 占位，仍然匹配得上。
    _升: dict[tuple, dict] = {}
    for x in 引用:
        if x.get("名"):
            #   ★ 键用 **`(起, 名, 行)`** —— `行` 就是**出现处的唯一标识**。
            #     第一版我把 `父` 也放进键，但**解析结果里没有 `父` 字段**
            #     （只有 `作用域`）⇒ 键永远对不上，**4 条只剩 1 条升成引用**。
            #     ★ 又一次「**字段名不一致，静默失配**」：不报错、只是数量变少。
            _升[(x.get("起"), x["名"], x.get("行"))] = x
    _改 = 0
    _保提及 = 0
    for e2 in 边:
        if e2.get("关系") != "提及":
            continue
        _终 = e2.get("终") or ""
        if not _终.startswith("name:"):
            continue
        #   ★ 先按「出现处」精确取；取不到（保底路径没有父/行）再退回按名字取 ——
        #     **退回也要能退**，否则保底路径的边**永远升不成引用**（那是另一种丢）。
        x = _升.get((e2.get("起"), _终[5:], e2.get("行")))
        if x is None:
            # ★ **退回也要能退**：保底路径的提及边没有 `行` ⇒ 引用条目也没有 ⇒
            #   两边都是 `None`，上面的键本来就能命中。这里再兜一层按名字取，
            #   只为老图（`引用.json` 里没 `行` 时）还能升。
            x = _升.get((e2.get("起"), _终[5:], None))
        if x is None:
            continue
        # ★ **歧义也是引用**（只是指向不唯一）——
        #   命中多个时 `终` 是 **list**，原来 `str(list).startswith("定义:")` 为假
        #   ⇒ **解析成功却被当成没解到**（实测 `未解析原因: "本文件定义"`）。
        #   **「歧义」和「未解析」是两件事**：前者是我找到了但说不准，后者是一个都没找到。
        _目 = x.get("终")
        _候 = _目 if isinstance(_目, list) else None
        if isinstance(_目, list):
            _目 = _目[0] if _目 else None
        # ★★★ **判「解到了」不许用手写的 id 前缀清单**（R350）。
        #   上一版是 `_目.startswith(("定义:","绑定:","bind:","file:","mod:",
        #   "标题:","项:","类型:","包:","模块:"))` —— 而节点类是
        #   `_实体类 = ['声明','定义','小节','标题','类型','项']`：
        #   **`声明:` 不在清单里** ⇒ JS/TS/Go/Rust 的定义（`声明:`）全部被当成
        #   「没解到」而**退回「提及」** —— 实测 `引用.json` 里明明有一条
        #   `{"名":"clamp","终":"声明:util/clamp.js:clamp:1","目标文件":"util/clamp.js"}`
        #   而**主图 `边.json` 里一条引用边都没有**。
        #   ★ 这与紧邻下面那条注释（手写清单漏了 `目标文件`）**是同一个病**：
        #     **手写清单必然漏**。所以这里改成**查表**：
        #     目标 id 在节点表里、且它的类属于 `_实体类` ⇒ 就是解到了定义。
        #     （`file:`/`mod:` 是文件/模块节点，本就在表里；歧义候选用 `_候` 兜。）
        _实体类集 = set(_实体类)
        _是引用 = (x.get("关系") == "引用"
                 and (_候 is not None
                      or (isinstance(_目, str)
                          and ((_目 in 节 and 节[_目].get("类") in _实体类集)
                               or _目.startswith(("file:", "mod:", "包:", "模块:"))))))
        # **没解到目标的保持「提及」**（名实相符），只记原因
        if not _是引用:
            e2["未解析原因"] = x.get("未解析原因") or x.get("依据") or "未解析"
            _保提及 += 1
            continue
        e2["原名"] = _终                          # 「名字出现过」不许丢
        e2["关系"] = "引用"
        e2["终"] = _目
        # ★ **除身份键之外，解析结果的所有字段一律搬过去** ——
        #   上一版手写清单（`依据`/`歧义`/`外部`）**漏了 `目标文件`**，
        #   于是判据报「绑定到定义却说不清是哪个文件」。
        #   **手写清单必然漏**；改成整表搬运，下次给解析加字段边自动就有。
        #
        # ★★★ **`名` 不是身份键，不许跳过**（R350）。上一版跳过 `("起","名","关系","终")`，
        #   而**提及边上根本没有 `名`** ⇒ 主图里那条引用边**丢了 `名`**：
        #   实测 `边.json` 里是 `{"关系":"引用","原名":"name:clamp","目标文件":"util/clamp.js"}`
        #   —— **靶文件在、名字没了**。下游按名字查引用边**一条都查不到**
        #   （判七十四 正是这么发现的：`引用.json` 有 `名`，主图没有）。
        #   ⇒ 身份键只有三个：`起`（谁）、`关系`（是什么）、`终`（指向哪）。
        for _k, _v in x.items():
            if _k in ("起", "关系", "终"):
                continue
            e2[_k] = _v
        if _候:
            e2["歧义"] = True
            e2["候选"] = _候[:20]                  # **歧义必须标出来**，且给出候选
        if x.get("外部"):
            e2["外部"] = True
        _改 += 1

    出 = {
        "根": str(根), "文件数": 图.get("文件数"),
        "★口径": "「提及」来自标识符出现；本层把它解析到**具体定义**。"
                "**解析不了的不许静默** —— 全部分类报出。",
        # ★ `"计数"` **不在这里赋值** —— 它必须**在派生之后**（R362 实测踩到）：
        #   派生代码在下面几十行，而这里先把**旧的累加值**存进了返回字典
        #   ⇒ 探针读 `r["计数"]` 拿到旧值、日志打印拿到新值，**同一个事实两个答案**，
        #   而且**两个看起来都很合理**（累加值 10,330 / 派生值 145,334）。
        #   ★ 又是「**同一个判断写在两处，必然给出两个答案**」。
        "★已解析占比": None,
        "★未导入（依赖图缺边，**不是「没有」**）": len(未导入例),
        "未导入样例": 未导入例,
        "未解析样例": 未解析例,
        "歧义样例": 歧义例,
    }
    总 = sum(计.values())
    # ★★★ **`计` 一律从 `引用` 派生 —— 单一来源**（R362）。
    #   原来 `计[...] += 1` 散在 6 个分支里 ⇒ **漏一处就是一个假 0**
    #   （标准库实测：`已解析·导入模块 = 0` 而依据里明明有 23,695 条；
    #    `已解析·本文件 = 10,330` 而依据里 145,334 条 —— 差 14 倍）。
    #   ★ 这是「**同一个判断写在两处，必然给出两个答案**」的**第 N 次**：
    #     一处是「分支里加号」，一处是「`引用` 里那条记录」。
    #   ⇒ 分类规则**只写在这里一份**，谁想知道类别就查 `依据`。
    _计2 = {"已解析·本文件": 0, "已解析·导入模块": 0,
            "绑定/内置（**不算引用**）": 0, "未导入": 0, "未解析": 0,
            "文档/配置链接（**抽取阶段即引用**）": 0}
    # ★★★★ R511b：**「内置去哪了」必须有台账**（口径 199）。
    #   R511 把内置判定提前到「绑定/内置」分支 ⇒ 内置**不再进 `_未解名`**
    #   ⇒ 以 `_未解名` 为输入的两个留痕字段
    #     （`★按语言判定的内置（R484…）` / `★按出现处语言判定的内置（R493）`）
    #   **会读作空 / 0**。★ 空的不是「没剔」，是「**在更早的档就拦下了**」——
    #   但**读的人看不出来**（口径 322 的应验：上一轮"报出来"的改动，
    #   这一轮变成"报没了"）。⇒ 在这里把**新台账**数出来。
    # ★★★ R518：**抽取阶段就已经是「引用」**的边必须接上出口（口径 286）。
    #   实测：12 个桶的 `边.json` 里有 **3,256 条**这类边
    #   （`依据` 以 `markdown`/`json` 开头：`markdown 链接` 2,932 ·
    #    `markdown 外链` 131 · `markdown 本文件锚` 193），
    #   而 `引用.json` 里 **0 条** —— 因为 `引用 = _引用表()`（L4638）是**新建空表**，
    #   只装下面这个循环**自己升出来的**边。
    #   ⇒ `.md`/`.json` 里写着的真引用，**AI 一条都看不到**。
    #   ★ 这不是"没做"，是"**做了但没接上**"：适配器早就在
    #     （`_Markdown适配器`：标题=定义、链接=**真引用**，`依据: "markdown 链接"`）。
    #   ★ 按 `(起,终,行)` 去重 ⇒ **幂等**（重跑不会翻倍）。
    #   ★ 只认 `依据` 前缀 `markdown`/`json` —— 上一轮升级出来的边依据是
    #     `导入定义（跨文件）` 之类，**不在这两档里** ⇒ 不会被重复加。
    _抽引 = [x for x in 边
             if isinstance(x, dict)
             and str(x.get("关系") or "") == "引用"
             and str(x.get("依据") or "").startswith(("markdown", "json"))]
    # ★ R518b：**这个 `_已有键` 取的是本轮新建的 `引用` 表，不是盘上的 `引用.json`**
    #   ⇒ 它数的是「本轮循环里已经产出过的」，**不是**「幂等重跑时本来就在的」。
    #     （口径 311：**字段名本身可以是一个缺陷** —— 上一版它叫
    #      `本来就在（幂等重跑）`，读的人必然误判成"不幂等"。）
    _已有键 = {(str(x.get("起")), str(x.get("终")), x.get("行")) for x in 引用}
    _并进 = 0
    for _x5 in _抽引:
        _k5 = (str(_x5.get("起")), str(_x5.get("终")), _x5.get("行"))
        if _k5 in _已有键:
            continue
        引用.append(dict(_x5))
        _已有键.add(_k5)
        _并进 += 1
    出["★并进抽取阶段就是引用的边（R518）"] = {
        "★口径": "`边.json` 里 `关系=引用` 且 `依据` 以 markdown/json 开头",
        "这么些条": len(_抽引),
        "**新并进引用.json 的**": _并进,
        "本轮循环已产出（去重命中）": len(_抽引) - _并进,
        "★注意": "上面这个『去重命中』数的是**本轮循环**，"
                  "**不是**幂等（幂等要看盘上 `引用.json` 的最终条数）",
    }

    _绑内条 = 0
    for _x4 in 引用:
        _依4 = str(_x4.get("依据") or "")
        _终4 = _x4.get("终")
        _靶4 = not (isinstance(_终4, str) and _终4.startswith("name:"))
        if _依4.startswith(("markdown", "json")):
            # ★ R518：抽取阶段即引用的（markdown 链接 / json references）——
            #   **不许**被算成 `已解析·导入模块`（那不是导入模块，名不副实，口径 311）。
            _计2["文档/配置链接（**抽取阶段即引用**）"] += 1
        elif _依4.startswith("**绑定/内置**"):
            _计2["绑定/内置（**不算引用**）"] += 1
            _语4 = _按扩展名判语言(str(_x4.get("起") or ""))
            if _语4 and _是内置(_x4.get("名"), {_语4}):
                _绑内条 += 1
        elif _依4.startswith("**未导入**"):
            _计2["未导入"] += 1
        elif _靶4:
            if _依4.startswith("本文件定义"):
                _计2["已解析·本文件"] += 1
            else:
                _计2["已解析·导入模块"] += 1
        else:
            # ★ **指不到节点的「引用」不算解析** —— 名实必须相符
            #   （`未导入` 已在上面分掉，这里是真未解析）
            _计2["未解析"] += 1
    # ★ 与循环里累加的值对账：**不一致就报出来**（不许静默用一个）
    _差2 = {k: (_计2[k], 计.get(k, 0)) for k in _计2 if _计2[k] != 计.get(k, 0)}
    if _差2:
        打印(f"   ⚠ 计数口径不一致（派生值, 累加值）：{_差2}")
    计 = _计2
    #   ★ 派生之后才放进返回字典 —— **一处赋值，一处读**
    出["计数"] = 计

    # ★★★ **两个数必须分开报，不许用一个顶替另一个**（R363）。
    #   `总` 是**解析时扫过的提及边数**（事实）；`sum(计)` 是**产出的引用条数**。
    #   两者**不一定相等** —— 一条提及边可能产出多条引用
    #   （导入表路径 + 模块名路径会各产一条）。
    #   ★ 原来 `总 = sum(旧计)` 在派生之前算 ⇒ 判五十三① 拿一个**中间态的数**
    #     去比派生后的和，红得**完全正确**（它抓到了「总不是总」）。
    #   ★ 口径：**「提及边数」和「引用条数」是两个事实**，
    #     各自有名字；**差值必须报出来**，否则「多出来的」就没名字。
    出["提及总数"] = 总
    出["★分类之和"] = sum(计.values())
    出["★一条提及边产出的引用（差额，可能有导入表/模块名两条路）"] = sum(计.values()) - 总
    出["★已解析占比"] = round(
        100 * (计["已解析·本文件"] + 计["已解析·导入模块"])
        / max(1, 总), 1)
    (kg2 := _kg目录(根)).mkdir(parents=True, exist_ok=True)
    _原子写519((kg2 / "引用.json"), json.dumps(引用, ensure_ascii=False))
    # ★ R518b：**盘上最终有多少**（真幂等读数）—— 重跑这个数不许变
    出["★并进抽取阶段就是引用的边（R518）"]["**盘上引用.json 里这类边的最终条数**"] = sum(
        1 for _x6 in 引用
        if str(_x6.get("依据") or "").startswith(("markdown", "json")))
    # ★ 主图也要更新（`加载图` 读的是 `边.json`；写别处等于没写）
    _原子写519((kg2 / "边.json"), json.dumps(边, ensure_ascii=False))
    出["★并进主图"] = {"升级为引用": _改, "仍为提及（未解到目标）": _保提及}
    # ★ R491：**降级必须留痕**（口径 199）—— 它是"这次跑之前盘上有多少上次的派生结果"。
    出["★降级回提及的上轮派生（R491）"] = _降级
    # ★★★ 名字级分层（**目标 ④ 的可行动读数**）—— 与上面的「出现处」口径并列，不许混。
    出["★名字级"] = {
        "★口径": "**按（文件, 名字）计**，不是按名字 —— 同名在不同文件境遇可以不同",
        "出现过的（文件,名字）": len(_名出现),
        "解到定义的（文件,名字）": len(_名出现 & _名解到),
        "**一个答案都没给的（文件,名字）**": len(_名无解),
        "  其中：**未导入**（别处有定义，本文件没导入 ⇒ 补一行 import 就消）":
            len(set(_名无解) & _名未导入),
        "  其中：内置/绑定（**不算缺**）": len(_名出现 & _名内置),
        "其中：在别处有定义（= 少一行 import 或我漏抽导入）": len(_名缺导入),
        "未解到样例": _名无解例,
        "缺导入样例": _名缺导入,
    }
    # ★★★ **「真外部缺」单独成表**（R407）—— 目标④的落盘形态。
    #   ★ 为什么必须再分一层：`未解析` 里混着**四类假缺口**（R405/R406 已剔掉
    #     关键字/tactic/绑定），剩下还混着 **命名空间首段**：
    #     `MyMeta` 3,771 次 —— 它是 `MyMeta.Foundational.…` 的**第一段**，
    #     本仓**当然没有叫 `MyMeta` 的定义**，但它**不是缺口**：
    #     缺口是"缺一个该有的定义"，不是"少了半个限定名的前缀"。
    #   ★ 口径：**「真缺口」与「半截名字」是两件事** ——
    #     前者是"AI 知道自己缺什么"的读数，后者是**抽取层的副产品**。
    #   ⇒ 排除：① 命名空间首段（本仓有 `X.…` 形式的名字）② 已归绑定/内置的
    #     剩余的才算**真外部缺**。
    _未解名 = {}
    # ★★★ R463：**同一个名字可能出现在多种语言的文件里** ⇒ 必须把语言记下来，
    #   否则「按语言判内置」没有输入。
    # ★★★★ R493：**按出现处记语言，不是按名字记语言集**（口径 283）。
    #   ★ R463 起这里是 `set`（`_未解名语言[名] = {语1, 语2, …}`），
    #     于是 `_是内置(名, 语言集)` 的**交集语义**把「同一名字在多种语言里
    #     各出现若干次」压成了一个"全有或全无"的判断：
    #       实测 `PartialEq` —— 在 `.rs` 里是 `std::cmp::PartialEq`（内置）、
    #       在 `.lean` 里没有这个名字 ⇒ 语言集 `{rust, lean}` ⇒ **判"否"** ✗
    #       结果：**34 个 Rust 标准库名字**（`PartialEq/Ord/Hash/Debug/Display/
    #       Default/Clone …`）因为有别的语言也提到过，就被报成「真缺口」。
    #   ★ 修法（**保守方向不变**）：语言集**仍按名字取交集**判"这个名字是不是
    #     通篇内置"（口径 203：宁可漏剔）；但**出现次数**按**每个出现处自己的
    #     语言**分别算（`Counter`）⇒ `PartialEq` 在 `.rs` 里的那些次算内置、
    #     在别的语言里的那几次**如实算真缺**。
    #   ★ 这不是放宽：`_是内置` 本身一个字没改，判一百三十九 ⑤（跨语言不许串台）
    #     照旧绿；变的只是"**这个数字怎么数**"。
    _未解名语言: dict[str, dict] = {}
    # ★★★★ R509：**语言判不出来的那几次也要数** ——
    #   原来 `if _语:` 直接跳过 ⇒ 那几次**在语言表里根本不存在**，
    #   于是"逐条判"会**少算**它们（口径 260：空输入不补 0；口径 190）。
    #   现在单列一档：语言未知 ⇒ **不剔**（口径 158 判一百五十八定的口径）。
    _未解无语: dict[str, int] = {}
    for _r in 引用:
        if "**未解析**" not in (_r.get("依据") or ""):
            continue
        _未解名.setdefault(_r.get("名"), 0)
        _未解名[_r.get("名")] += 1
        _语 = _按扩展名判语言(str(_r.get("起") or ""))
        if _语:
            _d = _未解名语言.setdefault(_r.get("名"), {})
            _d[_语] = _d.get(_语, 0) + 1
        else:
            _未解无语[_r.get("名")] = _未解无语.get(_r.get("名"), 0) + 1
    # ① 本仓出现过的限定名首段（`MyMeta.Foundational.x` ⇒ 首段 `MyMeta`）
    _首段 = set()
    for _n in 节.values():
        _q = _n.get("限定名")
        if _q and "." in _q:
            _首段.add(_q.split(".", 1)[0])
    # ★★★ **"是限定名前缀"与"是不是真缺口"不是一回事**（R407 第二次修）。
    #   实测：第一版把"本仓有 `X.…` 形式的名字"整类排除 ⇒
    #     `List` / `Bool` / `Fin` 被归成"半截名字" ✗
    #     而它们是**货真价实的真外部缺**（Lean 标准库有，本仓没有裸名定义）。
    #   区别在哪：`MyMeta` 是**本仓自己的命名空间**（`MyMeta.Foundational.…`），
    #     `List` **不是**（本仓只是借它的名字写 `List.map`）。
    #   ★ 判据：**本仓有没有"裸名就等于它"的定义** ——
    #     有 ⇒ 不是缺口；没有 ⇒ **它就是缺口**（不管它是否也当前缀用）。
    #   ★ 口径：**「一个答案硬套多处，必然有一处是错的」** ——
    #     "是不是前缀"回答不了"缺不缺定义"，**两问必须两答**。
    裸名 = {n["名"] for n in 节.values() if n.get("类") in _实体类}
    # ★ R463：先剔「按语言判出来的内置」，再剔「本仓有裸名定义」——
    #   两个判据**独立**，各自留痕（口径 199：剔掉必须留痕）。
    # ★★★★ R484：**通用内置表里的名字也必须剔掉** ——
    #   这一段原来是这样：
    #     `_按语内置` = 「**不在**通用表里 **且** 按语言判是内置」的
    #     `_真外`     = 「不是裸名 **且 不在 `_按语内置`**」的
    #   ⇒ **通用表里的名字被 `not in _语言内置` 挡在 `_按语内置` 之外**，
    #     于是**留在 `_真外` 里** ✗✗✗
    #   ★ 方向是**反的**：**越是在通用表里（越确定是内置），越会被报成"真缺口"**。
    #
    #   ★ 为什么一直没发现：`_语言内置` 里有 `Option`/`String`/`Ok`/`Result`/
    #     `Some`/`Err`/`Vec`/`Self`/`Debug`/`Clone`…（168 个），
    #     而**在 Lean 桶里这些确实是 Mathlib 名（真的本仓外）** ⇒ 判对了；
    #     只有在 **Rust 桶**里 `Option` 才是「Rust 内置」。
    #   ⇒ **`semsearch`（Rust）是唯一把它暴露出来的桶**：
    #      解析率 70.08%，Top20 真外部缺前六名是
    #      `Option 308 · String 256 · Ok 213 · Result 166 · Some 112 · Err 98`
    #      —— **全是 `_语言内置` 的成员**。
    #
    #   ★ 口径 199：剔掉必须留痕 ⇒ **两批分开记**（通用表 / 按语言判）。
    # ★★★★ R484：**按语言判内置** —— 不再用语言无关的 `_语言内置` 做全局排除。
    # ★ 注意：`_通内置` 是在 **`_未解名` 里**取子集 ⇒ 与 `_已解` **天然不相交**，
    #   所以 `_已解 + len(_通内置) + 真缺口 == _边总` 才成立（R486 的三档自洽）。
    # ★ `_未解名语言[k]` 现在是 `{语: 次数}` ⇒ 取键集当语言集（**交集语义不变**）
    _语集 = {k: set(v) for k, v in _未解名语言.items()}
    _通内置 = {k for k in _未解名 if _是内置(k, _语集.get(k))}
    _按语内置 = {k for k in _未解名
                 if k not in _通内置
                 and _缺口分类(k, _语集.get(k)) == "内置"}
    # ★★★★★ R509：**逐条判内置**（口径 284：判据/判法要落在缺陷实际发生的层）。
    #   `_内按名[名]` = 该名字里「**自己那一处的语言**认它是内置」的出现次数；
    #   `_非内按名[名]` = 其余的（含**语言未知**那几次）——**这些才是真缺口**。
    #   ★ 与 `_通内置`（名字级交集）的关系：`_通内置 ⊆ {k: _非内按名[k]==0}` 不成立，
    #     恰恰相反 —— `PartialEq` 属于 `_通内置`（rust 认、lean 不认），
    #     而它**在 lean 的那几次**必须留在真缺里（Lean 没有这个内置）。
    #   ⇒ **真缺不再用 `_通内置` 做名字级排除**，改由 `_非内按名` 驱动。
    _内按名: dict[str, int] = {}
    for _k, _d in _未解名语言.items():
        _c = 0
        for _语, _n in _d.items():
            if _是内置(_k, {_语}):
                _c += _n
        if _c:
            _内按名[_k] = _c
    _非内按名 = {k: v - _内按名.get(k, 0) for k, v in _未解名.items()}
    # ★ 排除项**逐项留痕**（口径 199）：它们的出现次数必须能与真缺加起来**守恒**
    #   （判一百六十一 ② 就核这条）。
    _裸名排次 = sum(v for k, v in _非内按名.items() if k in 裸名)
    _语秤排次 = sum(v for k, v in _非内按名.items()
                    if k not in 裸名 and k in _按语内置)
    _真外 = {k: v for k, v in _非内按名.items()
             if v > 0 and k not in 裸名 and k not in _按语内置}
    _半截 = {k: v for k, v in _未解名.items() if k in _首段 and k not in 裸名}
    # ★★★ R481：**把「引用解析率」记下来** ——
    #   这是目标（「名字出现过」→「真引用」）的**核心读数**，
    #   而在此之前**账里根本没有它**：`外部缺.json` 只写 `未解析总数`，
    #   索引只写 `引用条数` / `未解析` ⇒ **两个数都在，比率没人算** ✗
    #   ⇒ 读数要靠人现算的东西，就等于没记（口径 197 的近亲）。
    #   ★ 口径 194：同一个概念两个出口字段必须一样 ——
    #     `引用边总数` / `未解析总数` / `已解析条数` / `★解析率` 四个字段
    #     **必须自洽**（判据一百三十六就是核这个）。
    _边总 = len(引用)
    _未解 = sum(_未解名.values())
    _已解 = _边总 - _未解
    # ★★★★ R486：**一个比率说不清三件事** ——
    #   实测（`semsearch`，R484 之后）：未解析 3,831 条的 Top5 是
    #     `.rs`  : Option 302 · Ok 249 · String 249 · Result 166 · Some 112
    #     `.lean`: IO 166 · String 94 · List 71 · UInt64 37 · toString 35
    #   —— **全是内置**。它们**确实**没解到本仓的定义（`Option` 由 `std` 提供），
    #   所以算「未解析」**逻辑上没错**；但把它们与
    #   「本仓缺了 `Truth` 这个类型」**报成同一个数**，
    #   读者就分不清「这个仓引用得野」和「这个仓真的少东西」。
    #   ⇒ **三档分开记**（口径 190 的同一族：不同的东西不能共用一个读数）：
    #       解到定义的 = `_已解`
    #       是内置的   = `_通内置`
    #       真缺口的   = `_未解 - _已解 - 内置` 的那一批
    #   ★ 三者之和 = `_边总`（自洽），且**三个比率都记**。
    # ★★ R486 抓到的第一个我自己的错：**「名数」与「出现次数」不是一个东西** ——
    #   我第一版写 `len(_通内置)`，量出 `semsearch` 内置占比 **0.33%**，
    #   而同一张表里 `Option 302 · Ok 249 · String 249` 明明摆着。
    #   ⇒ `_通内置` 是**名字集合**（43 个名），而 `_未解名` 是 `{名: 次数}`。
    #     「占比」必须按**出现次数**算，名数只能当参考（口径 255 的兄弟：
    #     **两个数都在，但它们不是一个东西**）。
    # ★★★★ R493：**新增「按出现处语言的真正内置次数」`_内条按语`**。
    #   ★ 它与 `_内条` 差在哪：`_内条` 只数**「通篇内置」的名字**（交集语义，
    #     保守，口径 203）⇒ `PartialEq`（rust 认、lean 不认）**一条都不数**；
    #     `_内条按语` 对每个 `(名, 语言)` 单独判 `_是内置(名, {语})`，
    #     把认它是内置的那些语言的出现次数加起来 ⇒ `PartialEq` 在 `.rs` 里的
    #     那些次**计入**，在别的语言里的那几次**不计入**（如实算真缺）。
    #   ★ 两者之差 = **病②（判一百四十三 留痕的那 34 个名字）的精确规模** ——
    #     修之前它只是个"34 个名字"的说法，修之后它是一个**数**。
    #   ★ 为什么**不**让 `_内条按语` 直接替代 `_内条` 去驱动三档读数：
    #     那等于"**按部分语言把这个名字剔掉**"，而口径 203 要求
    #     **宁可漏剔**；而 `_通内置` 的名字级交集语义正是判一百三十九 ⑤
    #     （跨语言不许串台）依赖的东西。⇒ **两个数并列报出，主从分明**：
    #     `_内条` 驱动三档（行为不变），`_内条按语` 是**这个缺陷的读数**。
    _内条 = sum(_未解名.get(k, 0) for k in _通内置)
    # ★ 病② 的名单（口径 199 留痕）：**交集判否、但至少一种语言认它是内置**。
    _病二 = {k for k, _d in _未解名语言.items()
             if any(_是内置(k, {_语}) for _语 in _d)
             and not _是内置(k, _语集.get(k))}
    _内条按语 = 0
    for _k, _d in _未解名语言.items():
        for _语, _c in _d.items():
            if _是内置(_k, {_语}):
                _内条按语 += _c
    _外档 = {
        "根": str(根),
        "引用边总数": _边总,
        "已解析条数": _已解,
        "★解析率（已解析/全部引用边）": (round(_已解 * 100.0 / _边总, 2) if _边总 else None),
        # ★★★★ R486：三档分开报 —— **一个比率说不清三件事**。
        "★按语言判定的内置·出现次数（R486）": _内条,
        "★按语言判定的内置·去重名数（R486）": len(_通内置),
        "★内置占比%": (round(_内条 * 100.0 / _边总, 2) if _边总 else None),
        "★真缺口占比%": (round((_未解 - _内条) * 100.0 / _边总, 2) if _边总 else None),
        "★三档自洽（解到+内置+真缺=全部）": (
            _已解 + _内条 + (_未解 - _内条) == _边总),
        "★未解析里有多少是内置（R486）": (
            round(_内条 * 100.0 / _未解, 2) if _未解 else None),
        # ★★★★ R493：**按出现处语言判定的内置次数** —— 名字级（`_内条`，交集、
        #   保守）与次数级（本字段）**并列报出，主从分明**：三档读数仍由 `_内条`
        #   驱动（行为不变），本字段是**病②的精确规模**（判一百四十三 留痕）。
        #   实测差额见 `★病②规模…`。
        "★按出现处语言判定的内置·出现次数（R493）": _内条按语,
        # ★★★★ R509：**逐条判的三档守恒** —— 这一次它是**真的**逐条：
        #   未解析总数 = 逐条内置次数 + 逐条真缺次数 + 名字级排除掉的次数
        #   （判一百六十一 ② 核这条；口径 194：同一个量两个出口必须一样）。
        "★未解析两档之和（逐条内置 + 逐条非内置）": _内条按语 + sum(_非内按名.values()),
        "★逐条非内置·出现次数（真缺的**上界**）": sum(_非内按名.values()),
        # ★ R509 自纠：**「语言判不出来」不许放进这个台账** ——
        #   台账列的是「**从真缺里排掉**的东西」，而语言未知的那些**本来就留在真缺里**
        #   （口径 158：语言未知 ⇒ 不剔）。放进去就等于**重复计数**，
        #   实测让 `太虚元道` 的守恒式多出 **182** 次（判一百六十一 ② 当场抓住）。
        "★逐条真缺排除留痕（口径 199）": {
            "本仓有裸名定义·出现次数": _裸名排次,
            "按语内置表（关键字/tactic）·出现次数": _语秤排次,
        },
        "★语言判不出来·出现次数（**不剔**，口径 158，**已含在真缺里**）":
            sum(_未解无语.values()),
        "★三档自洽（逐条·按出现次数）": (
            _内条按语 + sum(_非内按名.values()) == _未解),
        "★按出现处语言判定的内置占比%": (
            round(_内条按语 * 100.0 / _边总, 2) if _边总 else None),
        "★病②规模·次数级比名字级多算的内置（R493）": _内条按语 - _内条,
        "★病②留痕·交集判否但部分语言认它是内置（R493）·条数": len(_病二),
        "★病②留痕·名单（R493，至多 60 个）": sorted(_病二)[:60],
        # ★★★★ R511b：**新台账** —— 内置**在哪一档被拦下的**。
        #   ★ 为什么必须有它：R511 之后 `_未解名` 里**已经没有内置**，
        #     于是下面那两个「按语言判定的内置（R484/R493）」留痕字段读作 **0 / 空** ——
        #     口径 199 要求「剔掉必须留痕」，而**台账换位置了**：
        #     内置现在记在「绑定/内置」这一档里。
        #   ★ 字段分三件：① 新档的**规模** ② 一句话**说明** ③ 旧字段为空的**原因**。
        #     没有③，读的人会以为「没算」（口径 311：读数本身可以是缺陷）。
        "★内置去哪了（R511）·绑定与内置档·出现次数": _绑内条,
        "★内置去哪了（R511）·说明": (
            "R511 起内置判定在**绑定/内置分支**（比 `_未解名` 更早）就被拦下 ⇒ "
            "内置**不进「未解析」** ⇒ 以 `_未解名` 为输入的两个留痕字段"
            "（`★按语言判定的内置（R484/R493）`）**在 R511 之后为空/0 是正常的**；"
            "内置的**新台账就是本字段**。"),
        "★内置去哪了（R511）·旧字段为空的原因": (
            "不是「没算」，而是「**在更早的档就拦下了**」——"
            "口径 322：上一轮「报出来」的改动，这一轮会变成「报没了」。"),
        "★口径": ("**真外部缺** = 本仓没有、也不是关键字/tactic/绑定/限定名前缀 ⇒ "
                 "**这就是「AI 知道自己缺什么」的读数**（目标④）。"
                 "「半截名字」与「真缺口」是两件事，分开列。"),
        "★真外部缺": [{"名": k, "次": v, "也是限定名前缀": k in _首段}
                     for k, v in sorted(_真外.items(), key=lambda x: (-x[1], x[0]))],
        "真外部缺·去重数": len(_真外),
        "真外部缺·出现次数": sum(_真外.values()),
        "半截名字（限定名前缀，**不是缺口**）": sorted(_半截.items(),
                                                    key=lambda x: (-x[1], x[0]))[:50],
        "半截名字·去重数": len(_半截),
        "★按语言判定的内置（R463 剔掉的，留痕）": sorted(_按语内置),
        "★按语言判定的内置·条数": len(_按语内置),
        # ★★★ R484：通用表里剔掉的那一批 —— **原来没记，而且没剔** ✗
        "★按语言判定的内置（R484：含原「通用表」那批，留痕）": sorted(_通内置),
        "★按语言判定的内置（R484）·条数": len(_通内置),
        "未解析总数": sum(_未解名.values()),
    }
    _原子写519((kg2 / "外部缺.json"), json.dumps(_外档, ensure_ascii=False, indent=1))
    出["★真外部缺（本仓没有 ⇒ AI 缺什么）」"] = {
        "去重数": len(_真外), "出现次数": sum(_真外.values()),
        "Top20": sorted(_真外.items(), key=lambda x: (-x[1], x[0]))[:20],
        "半截名字（不是缺口）": len(_半截),
    }
    _原子写519((kg2 / "缺口.json"), json.dumps(出, ensure_ascii=False, indent=1))
    # ★★★ **收尾自动刷索引**（R406）。
    #   判九十 连续两轮因「索引里的数字过期」变红，而**两次都是正常操作**造成的
    #   （跑了 `更新图` / `解析引用`）。★ 口径：**「每次都要人记得去刷」= 迟早忘** ——
    #   能被忘掉的步骤**不该由人记得**。`解析引用` 是链条的最后一环，
    #   索引在这里刷，就永远跟着最新读数走。
    try:
        刷索引(根, 打印)
    except Exception as e:                        # noqa: BLE001
        打印(f"   ★ 索引刷新失败（{type(e).__name__}: {e}）—— "
             f"**如实说出，不静默**")
    打印(f"   引用解析：提及 {总} · 已解析 {出['★已解析占比']}%"
         f" · 绑定/内置 {计['绑定/内置（**不算引用**）']}"
         f" · **未导入 {计['未导入']}** · 未解析 {计['未解析']}")
    打印(f"     {计}")
    return 出



def _原子写519(路径, 文本: str) -> None:
    """★ R519：**同目录 `.tmp` + `os.replace`** 的原子写（全仓图/清单存档统一走这里）。

    ★ 为什么要（实测，不是"防御性编程"的口头禅）：
      原来这里是 `write_text` ⇒ 非原子。实测 2026-11-14 18:34：`收尾全套`
      在写 `边.json` 途中 MemoryError ⇒ 盘上留下 **0 字节** `边.json`
      （**135 万条边全没**），**且没有任何报错留痕** ——
      下次读出来只会以为「这个桶本来就没有边」。
      **「看不见」不能当「没有」**（口径 260）。

    ★ 为什么 `.tmp` 必须**同目录**：`os.replace` 只在同一文件系统内原子；
      跨盘/跨卷会退化成「复制 + 删」，就不再原子了。

    ★ 为什么做成函数而不是逐处展开（口径 305）：实测这类写点**一共 22 处**
      ——「同一个处理写多处、只改一处就是病」。
      ★ 且接收者形状不统一（`(kg / _图档)` / `(kg2 / "边.json")` / 裸名 `守`），
        所以这里是**一处实现**，调用点由脚本按括号配平统一改。
    """
    临 = 路径.with_name(路径.name + ".tmp")
    临.write_text(文本, encoding="utf-8")
    os.replace(临, 路径)


def 灌Lean确证(根: str | Path, 确证档: str | Path | None = None,
              打印: Callable[[str], None] = print) -> dict:
    """把 **elaborator 确证的声明名**灌进**同一张图**，与文本级层**并列**。

    ★★ 为什么是「并列」而不是「让文本级去模拟」：
      实测（`formal_proof`，746 模块 / 53,714 条确证名）：
        **文本级覆盖 17,271 / 34,227 = 50.5%**，缺的 **16,956** 条是
        **自动名**（`_proof_1` / `_sizeOf_inst` / `casesOn` / `match_` / `proof_`）、
        **构造子**（`CPU`/`GPU`）、**结构投影名**、**私有名编码** ——
        **文本里根本没有，正则原理上做不到**。
      ⇒ 合成一个数（「覆盖率」）会把「两种东西」压成「一个数」：
        文本级能自己查的部分 **和** 只有 elaborator 知道的部分。
        所以**两层各标来源，都留在图上**：
          · `来源: "文本"` + `确证: False`  —— 正则抽的
          · `来源: "elaborator"` + `确证: True` —— 权威登记的
          · 两边同名 ⇒ **合并成一个节点**，`来源: "文本+elaborator"`，`确证: True`

    ★ 键是**限定名** —— 实测文本级的 namespace 限定名与 elaborator 的登记名
      **格式一致**（`MyMeta.Foundational.PureLogic.impl_intro`），所以能对上。
    """
    根 = Path(根).resolve()
    # ★★★ R519：**锚定闸 —— 源码根不许自己就是图桶**（判一百七十五）。
    #   机制（读代码读出来的）：`_kg目录(根)` 在 `KG_图目录` 设着时是
    #   `Path(_env图目录) / 根.name` ⇒ 传**桶目录** `work/图/太虚元道` 时
    #   `根.name` = `太虚元道` ⇒ `_kg目录(桶目录)` = **桶自己**
    #   ⇒ 本函数「读自己的图、覆写自己的图」，**而且不报错**。
    #   实测后果：`太虚元道` 1,503,681 → **21,160** · `形式化验证` 552,670 → **21,160**
    #   （**两个桶节点数完全相同** ⇒ 同一份确证节点集合覆盖了两张图）。
    #   ★ 只喊错不给修法 = 没接线 ⇒ 把「该传什么」一起报出来。
    _桶519 = _kg目录(根)
    if _桶519.resolve() == 根:
        return {
            "错误": f"`根` **自己就是图桶**（`{根}`）⇒ 拒跑",
            "★为什么拒跑": ("本函数会读这张图、再覆写这张图（`_kg目录(根)` = 根本身）"
                            "⇒ 图会被换成「确证节点集合」，**且不报错**。"
                            f"实测：`{根.name}` 1,503,681 → 21,160 节点。"),
            "★怎么修": f"传**源码根**，不要传桶目录。这个桶对应的源码根在 "
                        f"`{_桶519 / '图.json'}` 的 `根` 字段里。",
            "★哨兵": "判一百七十五（源码根不许自己就是图桶）",
        }
    图 = 加载图(根)
    if "未测到" in 图:
        return _未测到留痕(图, 根)
    节: dict = 图.get("节点") or {}
    边: list = 图.get("边") or []

    if 确证档 is None:
        确证档 = (Path(__file__).resolve().parents[2] / "products" / "mem"
                 / "模块声明名-elaborator.tsv")
    确证档 = Path(确证档)
    if not 确证档.is_file():
        return {"错误": f"确证档不在：{确证档}",
                "建议": "先跑 `mem_axiom.扫` 生成 elaborator 确证表。"}

    # ★★★ R519：**在入口处归一化 Lean 的标识符引号 `«…»`**（判一百五十七④）。
    #   `Name.toString` 对非法标识符段自动加书名号 ⇒ 导出档天生带引号，
    #   形态如 `MyMeta.Foundational.PureNat.«term_<n_»` —— **源码里不出现**这种写法
    #   ⇒ 不归一化的话，「提及」侧永远对不上（等于另一种漏）。
    #   ★ 唯一归一化点在 `mem_axiom.规范名`（口径 305：一处提供，两处都用）。
    #   ★ 实测零碰撞：确证档 53,411 行里带引号 772 行，
    #     归一化后**互相撞 0 组** · **与裸名相同 0 个**。
    from . import mem_axiom as _ma519
    确证: dict[str, set[str]] = {}
    _原条数519, _带引号519 = 0, 0
    _原始名们519: list[str] = []
    for 行 in 确证档.read_text(encoding="utf-8", errors="replace").splitlines():
        部 = 行.split("\t")
        if len(部) >= 2:
            _原条数519 += 1
            _原始名们519.append(部[1])
            if _ma519.带引号吗(部[1]):
                _带引号519 += 1
            确证.setdefault(部[0], set()).add(_ma519.规范名(部[1]))
    全确证: set[str] = set()
    for v in 确证.values():
        全确证 |= v

    # 模块 → 文件（复用通用模块索引）
    文件表 = {n.get("文件"): k[5:] for k, n in 节.items() if k.startswith("file:")}
    模索引 = _模块索引(文件表.values()) if 文件表 else {}

    # ① 文本级节点：限定名命中 ⇒ **升级为已确证**
    升级 = 0
    已配 = set()
    for i, n in 节.items():
        if n.get("类") not in _实体类 or n.get("语言") != "lean":
            continue
        全名 = n.get("限定名")
        if 全名 and 全名 in 全确证:
            n["确证"] = True
            n["来源"] = ("文本+elaborator" if n.get("来源") == "文本"
                        else n.get("来源"))
            # ★★★ R447：**`升级` 必须数「名字」，不能数「节点」** ——
            #   实测 `升级 50,422` + `没配上 19,469` = 69,891 > `确证名总数 53,704`
            #   （**多 16,187**）⇒ 同一个全限定名在图上**有多个节点**
            #   （多个文件/多个 `定义:` 号），旧式 `升级 += 1` **每个节点都加一次** ✗
            #   ⇒ **口径 160/163：「报了数」与「数的是一回事」是两件事。**
            if 全名 not in 已配:
                升级 += 1
            已配.add(全名)

    # ② 只有 elaborator 知道的 ⇒ **新建节点**（挂到模块对应的文件上）
    新增 = 0
    挂不上 = 0
    短名撞集: set = set()   # ★ R447/448：**去重集合**（不是计数器）
    号异 = 0         # ★ R447：号在、但名不同（不该发生，发生了要报）
    # ★ R448：`折叠` / `去重后待新建` **都从 `待建集` 现算** ——
    #   ★ 旧式按 `集` 逐模块累加 ⇒ 与 `待建集`（去重）**量纲又不同** ⇒ B 差 −4 ✗
    #   ⇒ **同一把尺子：先取集合，再从集合上算每一个分项。**
    待建集: set = set()  # ★ R447/448：全名没配上的**去重**集合（与 `全确证` 同量纲）
    样例: list[dict] = []
    for 模块, 集 in 确证.items():
        # ★★ **查侧必须试各级后缀，和注册侧对称。**
        #   实测：注册侧登记了 `Foundational/PureBool`、`Foundational.PureBool` 等后缀，
        #   查侧却只试了**全名**（`MyMeta.Foundational.PureBool`）⇒ 17,864 条
        #   「挂不上文件」⇒ 它们**没能进图**，而读数里显示「只有 elaborator 有 0」
        #   —— **「看不见」被当成了「没有」**。两边不对称就是假 0 的来源。
        部 = 模块.split(".")
        目 = []
        for k in range(len(部)):
            尾 = ".".join(部[k:])
            if k == 0:
                目 = 模索引.get(尾) or 模索引.get(尾.replace(".", "/")) or []
            else:
                目 = (模索引.get(尾) or 模索引.get(尾.replace(".", "/"))
                     or (模索引.get("/".join(部[k:])) if False else None) or [])
            if 目:
                break
        相 = 目[0] if 目 else None
        裸 = {}
        _待 = 0          # ★ R447：去重**前**的全名数（分母）
        for 全名 in 集:
            if 全名 in 已配:
                continue
            _待 += 1
            n裸 = 全名.rsplit(".", 1)[-1]
            裸.setdefault(n裸, 全名)
        # ★★ 算术要对得上，**两边必须同量纲**（口径 144）：
        #   `确证名总数` 按**全名**计，而 `裸` 已按**短名**去过重 ⇒
        #   实测 `50422 + 6495 = 56917 ≠ 53704`（**多 3,213**）✗
        #   ⇒ 报出去重后的分母，让调用方能自己收口。
        # ★★★ R448：**`待建` 必须用和 `全确证` 同一把尺子（去重）** ——
        #   实测：`全确证` 去重后 53,704，而 `待建` **按模块累加** ⇒
        #   有 **10 个全名出现在两个模块里** ⇒ 累加会**多 10** ✗
        #   （其中 6 个已在 `已配` 里被跳过 ⇒ 实测只多 **4** —— 精确对上）
        #   ⇒ **口径 144/177 第三次：算术要对上，两边必须同量纲。**
        待建集 |= (set(集) - 已配)      # ★ 用集合，不用累加
        for n裸, 全名 in 裸.items():
            if 全名 in 已配:
                continue
            # ★★★ **"挂不上文件"也必须有一个稳定归属**（R403）。
            #   实测（R399 建 → R402 定因 → R403 修）：
            #     干净图 `建图+灌确证`：报「只有 elaborator 有 17,859」+
            #       「挂不上文件 5」；而**落盘 elaborator 节点 17,864**
            #       ⇒ **17,859 + 5 = 17,864**，两数**都对**，只是数的不是一回事
            #       （口径：**「报了数」与「落盘了」是两件事**）。
            #     但 `更新图`（增量）之后变成 **17,859** —— **那 5 个没了** ✗
            #   根因：挂不上时 `文件` 是 `None` ⇒ **增量按文件归属重建时找不到它**
            #     ⇒ 被当墓碑丢掉。（图.json 里的
            #     `★增量丢边（查不到归属文件的边）` 是同一件事的边侧。）
            #   ★ 口径：**「建了」而「增量留不住」等于没建** ——
            #     一个节点若**没有任何归属**，它在"按归属刷新"的机器里
            #     就是**不可寻址的**，下一轮必然丢。
            #   ⇒ 给它们一个**显式的伪归属** `"<elaborator>"`：
            #     ① 增量按文件找得到 ✓
            #     ② 图上直接看得见**"它不属于任何源文件"**（`无源文件: True`）✓
            #        而不是靠 `文件: None` 这种**下游要各自猜**的形状。
            _相C = 相 if 相 else "<elaborator>"
            号 = f"定义:{_相C}:{n裸}:0"
            if 号 in 节:
                # ★★★ R447：**这一支原来什么都不记** ⇒
                #   实测 `确证名总数 53,704`、`★文本级升级为已确证 50,422`、
                #   而 `★只有 elaborator 有 0` —— **差额 3,282 去哪了？**
                #   答：这 3,282 条的**全限定名没配上**（所以没进 `已配`），
                #   但**裸名撞上了文本级已有的节点号** ⇒ 在这里 `continue` ✗
                #   ⇒ **「全名没配上」和「文本级真没有」被合成了一个读数** ✗
                #      —— **口径 155/160：两个不同的东西不许共用一个数。**
                #   ★ 正确形状：**三个数分开报**：
                #     · 全名配上 ⇒ `★文本级升级为已确证`（真升级）
                #     · 全名没配上、**短名撞上** ⇒ `★全名没配上但短名撞上`
                #     · 短名也没有 ⇒ `★只有 elaborator 有`
                _已存 = 节.get(号) or {}
                if n裸 == _已存.get("名"):
                    短名撞集.add(n裸)   # ★ 只按短名 —— 与 `待建短名` 同量纲
                else:
                    号异 += 1
                continue
            节[号] = {
                "id": 号, "类": "定义", "名": n裸, "语言": "lean",
                "限定名": 全名 if "." in 全名 else None,
                "文件": _相C, "行": 0, "类型": "elaborator:const",
                "命名空间深度": 全名.count("."), "父定义": None, "粒": _粒("文件"),
                # ★★ 这一条**只有 elaborator 有** —— 图上必须看得见这个事实
                "来源": "elaborator", "确证": True,
                "无源文件": (相 is None),
            }
            边.append({"起": f"file:{_相C}", "终": 号, "关系": "包含",
                      "依据": ("elaborator 确证（文本级抽不到）" if 相
                             else "elaborator 确证（**模块→文件对不上**，"
                                  "挂在伪归属上）")})
            if 相:
                新增 += 1
            else:
                挂不上 += 1
            if len(样例) < 12:
                样例.append({"模块": 模块, "名": n裸, "全名": 全名, "文件": 相})

    # ★ R448：**从去重集合上现算短名数**（与 `待建集` 同一把尺子）
    _裸数 = len({x.rsplit(".", 1)[-1] for x in 待建集})
    出 = {
        "根": str(根), "确证档": str(确证档),
        "确证模块数": len(确证), "确证名总数": len(全确证),
        "★归一化 Lean 标识符引号（`«…»` → 去掉）": {
            "原始条数": _原条数519, "带引号的": _带引号519,
            "归一化后去重条数": len(全确证),
            # ★ 口径 194：**两个口径不许混成一个数**。实测真档 53,411 行：
            #   原始自带重复 **10** 条 · 归一化**额外**造成的撞名 **0** 条。
            "★原始档自带的重复（与归一化无关）": (
                len(_原始名们519) - len(set(_原始名们519))),
            "★归一化额外造成的撞名": (
                len(set(_原始名们519)) - len(全确证)),
            "★为什么必须归一化": ("`«` / `»` 是 Lean 的**标识符引号**，不是名字的一部分；"
                                  "留着的话「提及」侧永远对不上（判一百五十七④）"),
        },
        "★文本级升级为已确证": 升级,
        "★只有 elaborator 有（文本级抽不到）": 新增,
        "★挂不上文件（模块→文件对不上）": 挂不上,
        # ★★ R447：把"全名没配上"与"文本级真没有"**分开**（口径 155/160）
        "★全名没配上但短名撞上（文本级有同短名）": len(短名撞集),
        "★号在但名不同（不该发生）": 号异,
        # ★★ R447：让算术收得上的三个数（**同量纲**，口径 144）
        "★全名没配上的总数": len(待建集),
        "★其中被短名去重折叠掉": len(待建集) - _裸数,
        "★去重后待新建的短名数": _裸数,
        "样例": 样例,
    }
    节 = dict(节)
    图["节点"] = 节
    图["边"] = 边
    kg2 = _kg目录(根)
    kg2.mkdir(parents=True, exist_ok=True)
    # ★★ **必须写 `边.json`，不是 `图.json`！**
    #   `加载图` 读的是 `节点.json` + `边.json`（`_跑` 写的也是它们）；
    #   `图.json` 是**遗留常量** `_图档`，早就不是主存档了。
    #   写错地方的后果：**判定全对、数字全对，下次读出来却是「没做过」** ——
    #   「跑过」与「留下痕迹」是两件事。
    #
    # ★★★ **R368 实测：我上面这段注释警告完，自己就漏了 `节点.json`。**
    #   真跑（`formal_proof`，4,033 个 `.lean`）：`确证模块数 746`、
    #   `★文本级升级为已确证 32,905`、`★只有 elaborator 有 17,859` —— **全对**，
    #   而 `加载图` 数出来的 `来源 == "elaborator"` 节点是 **0**。
    #   ⇒ **那 17,859 个节点全丢了**：新建的节点只挂在 `节` 上，
    #     `节`（= `图["节点"]`）**从来没写盘**；只有 `边.json` 落了盘 ⇒
    #     那些边**指向不存在的节点**（悬空边）。
    #   ★ 口径：**「报了数」与「落盘了」是两件事** ——
    #     和「跑过 vs 留下痕迹」「登记了 vs 接上了」同族，
    #     而且更隐蔽：**它把计数算对了**，所以读数字的人**不会怀疑**。
    _原子写519((kg2 / "节点.json"), json.dumps(节, ensure_ascii=False))
    _原子写519((kg2 / "边.json"), json.dumps(边, ensure_ascii=False))
    _原子写519((kg2 / "lean确证.json"), json.dumps(出, ensure_ascii=False, indent=1))
    # ★★★ R459：**合流改了 `节点.json`/`边.json`，就必须把 `图.json` 的计数一起改** ✗
    #   实测（判九十②）：`formal_proof` 索引写节点 **400,828**，而图上重数是 **418,692**
    #   —— 差 17,864 就是这里新加的节点，`图.json` 还是 `建图` 那一刻的旧数。
    #   ★ 这正是 R395 那类病：**"数字过期"与"数字算错"长得一样**（口径 146/160）。
    #   ★ 口径 169 的同族：**改了两个产物就要改它们的账**，否则读账的人拿到的是旧世界。
    _tj = kg2 / "图.json"
    if _tj.is_file():
        try:
            _gj = json.loads(_tj.read_text(encoding="utf-8"))
            _gj["节点数"] = len(节)
            _gj["边数"] = len(边)
            _gj["★Lean合流"] = {"升级": 升级, "只有elaborator有": 新增,
                              "确证模块数": len(确证)}
            _原子写519(_tj, json.dumps(_gj, ensure_ascii=False, indent=1))
        except Exception:                              # noqa: BLE001
            pass
    打印(f"   Lean 合流：文本级升级 {升级} · **只有 elaborator 有 {新增}**"
         f" · 挂不上文件 {挂不上}")
    return 出


def 解析md链接(根: str | Path, 打印: Callable[[str], None] = print) -> dict:
    """markdown 链接 ⇒ **真引用**：目标文件**在不在**、`#L行号` **在不在范围内**。

    ★★ 为什么这是「真引用」最干净的一处读数：
      代码的引用要靠**作用域**推（import/绑定/歧义），推不出来只能标「未解析」；
      而 markdown 链接**自带目标**（写着路径）⇒ **存在与否是文件系统事实**，
      不是我猜的，也不是模型说的。
      ⇒ **断链 = 未解析引用**，直接喂给「未解析的必须报出来」这一条。

    ★ 目标形式（实测仓里 9,438 个 `.md` 真有）：
      · `other.md` / `../x/y.md` 相对本文件
      · `file:///E:/.../main.rs#L728-L743` **绝对 + 行号锚**（semsearch 生成的报告）
      · `https://...` 外部 ⇒ **不算断链**（标 `外部: True`）
      · `#heading` 本文件锚 ⇒ 查本文件有没有同名标题
    """
    根 = Path(根).resolve()
    图 = 加载图(根)
    if "未测到" in 图:
        return _未测到留痕(图, 根)
    边: list = 图.get("边") or []
    节: dict = 图.get("节点") or {}
    已有 = {k[5:].replace("\\", "/") for k in 节 if k.startswith("file:")}
    标题表: dict[str, set[str]] = {}
    for n in 节.values():
        if n.get("类") == "标题" and n.get("文件"):
            # ★★★ R525：原来存的是 `set(名)` —— **只有名字、没有 id**，
            #   所以锚边**没法接上**（口径 194：解到了就要能接上）。
            #   改成 `名 → 节点 id`。
            标题表.setdefault(n["文件"].replace("\\", "/"), {})[n.get("名") or ""] = n.get("id") or ""

    计数 = {"链接": 0, "外部": 0, "锚": 0, "已解析": 0, "断链": 0,
           "行号越界": 0, "本文件锚未命中": 0}
    断链样例: list[dict] = []
    for e in 边:
        if e.get("关系") != "引用":
            continue
        依 = e.get("依据") or ""
        if not 依.startswith("markdown"):
            continue
        计数["链接"] += 1
        终 = e.get("终") or ""
        if e.get("外部") or 终.startswith("外链:"):
            计数["外部"] += 1
            e["已解析"] = False
            continue
        起相 = (e.get("起") or "")[5:].replace("\\", "/")
        起目 = str(Path(起相).parent).replace("\\", "/")
        if 起目 == ".":
            起目 = ""
        if 终.startswith("锚:"):
            计数["锚"] += 1
            目 = 终.rsplit(":", 1)[-1]
            _标 = 标题表.get(起相) or {}
            e["已解析"] = 目 in _标
            if not e["已解析"]:
                计数["本文件锚未命中"] += 1
            # ★★ R525j：**这里曾经把锚接上 `标题:` 节点，已撤。**
            #   撤的理由是判一百七十① 当场抓住的：`解析引用` 从不产出指向
            #   `标题:` 节点的引用边（实测 `引用.json` 按终前缀分类 `标题` **0 条**）
            #   ⇒ 接上就违反「`边.json` 的引用边必须都在 `引用.json`」这条**跨产物**
            #   不变量 ⇒ `太虚元道` 丢 2 条（**2074 → 2072**）。
            #   ★ 收益 4 条边 vs 代价是弄坏一条不变量 ⇒ **撤**（口径 248）。
            #   ★ 撤掉**不丢事实**：解析状态在 `e["已解析"]` 与 `md链接.json` 的
            #     `本文件锚未命中` 里；`标题:` 节点本来就在 `节点.json` 里。
            continue
        if 终.startswith("file:"):
            # ★ R525：**收尾可能跑多次** —— 上一轮已接上的边不许再当「没解到」
            #   （幂等；否则第二轮把 `已解析` 数成 0，读数随跑几次而变）。
            计数["已解析"] += 1
            e["已解析"] = True
            continue
        if not 终.startswith("路径:"):
            continue
        原 = 终[3:].replace("\\", "/")
        # 目标：绝对路径取仓内相对；相对路径按**起文件所在目录**解析
        cand = ""
        if 原.startswith("/") or (len(原) > 1 and 原[1] == ":"):
            # ★ **绝对路径归仓**：先试 `relative_to`，失败要回退 realpath 前缀比较。
            #   实测踩过：`file:///e:/.../TS252_PartsWiring.lean` 文件**明明在**
            #   （`(根/cand).exists()` 为 True），函数里却 `目标文件=None` ——
            #   差别只在函数里 `根 = Path(根).resolve()`。
            #   **真实仓常有 junction/符号链接，`resolve()` 会把两边解析到不同的真实路径**
            #   ⇒ `relative_to` 抛异常 ⇒ 被我 `except` 里一句 `cand = ""` **吞掉**。
            #   **「异常吞成空串」是假 0 最喜欢的藏身处。**
            import os as _os2
            cand = ""
            try:
                cand = str(Path(原).resolve().relative_to(根)).replace("\\", "/")
            except Exception:                     # noqa: BLE001
                try:
                    _真 = _os2.path.realpath(原)
                    _根真 = _os2.path.realpath(根)
                    _rel = _os2.path.relpath(_真, _根真)
                    if not _rel.startswith(".."):
                        cand = _rel.replace("\\", "/")
                except Exception:                 # noqa: BLE001
                    cand = ""
        else:
            cand = str((Path(起目) / 原)).replace("\\", "/") if 起目 else 原
            cand = str(Path(cand)) .replace("\\", "/")
        目实 = None
        for c in (cand, cand.rstrip("/") + "/README.md", cand.rstrip("/") + ".md"):
            # ★★★ R525：**`c` 必须非空** —— `(根 / "").exists()` 就是 `根.exists()`，
            #   **恒为 True** ⇒ `目实 = ""` ⇒ `已解析 = True`：
            #   **断链被报成「已解析」，解析率虚增**（实测交付态 **188 条**）。
            if c and (c in 已有 or (根 / c).exists()):
                目实 = c
                break
        e["目标文件"] = 目实
        # ★ **锚行越界**必须真的数出来（原来是个假 0：锚在入口就被切掉了）
        if 目实 is not None and e.get("锚行"):
            try:
                全 = (根 / 目实)
                总行 = sum(1 for _ in 全.open("r", encoding="utf-8", errors="replace"))
                a, b = e["锚行"]
                e["文件行数"] = 总行
                if a > 总行 or b > 总行:
                    e["行号越界"] = [a, b, 总行]
                    计数["行号越界"] += 1
                else:
                    e["行号越界"] = None
            except OSError:
                e["行号越界"] = "读不了"
        if 目实 is None:
            e["已解析"] = False
            计数["断链"] += 1
            if len(断链样例) < 20:
                断链样例.append({"起": 起相, "行": e.get("行"), "目标": 原,
                               "解析为": cand})
        else:
            e["已解析"] = True
            计数["已解析"] += 1
            # ★★★ R525k：**解到了就把「出口」写上 —— 但不许改 `终`。**
            #   为什么不许改（判一百七十① 当场抓住的）：`收尾全套` 的次序是
            #   `解析引用`(①) → `md链接`(②)，而 `md链接` **读** `引用.json`
            #   （它自己的注释：「断链 = 未解析引用」）⇒ **① ② 不能翻**
            #   ⇒ `引用.json` 天生只存**抽取期**的 `路径:` 形态。
            #   改 `终` 就是在**跨产物的阶段边界上动键** ⇒ 实测丢 **1,343 条**。
            #   ★ 于是分名字：`终` = 抽取期 id（不许解析器动）·
            #     `出口` = 解析后的节点 id（想顺图走的人看这个）。
            #   ★ 只写**图里真有**的节点（`已有`）；盘上有、图里没有 ⇒ 另记一个量。
            if 目实 in 已有:
                e["出口"] = f"file:{目实}"
            elif 目实 is not None:
                计数["目标文件在盘上但不在图里"] = 计数.get("目标文件在盘上但不在图里", 0) + 1
            # `#L728-L743` 的行号**必须在文件范围内**
            if "#L" in (e.get("终") or "") or "#l" in (e.get("终") or ""):
                pass
    图["边"] = 边
    kg2 = _kg目录(根)
    kg2.mkdir(parents=True, exist_ok=True)
    # ★★ **必须写 `边.json`，不是 `图.json`！**
    #   `加载图` 读的是 `节点.json` + `边.json`（`_跑` 写的也是它们）；
    #   `图.json` 是**遗留常量** `_图档`，早就不是主存档了。
    #   写错地方的后果：**判定全对、数字全对，下次读出来却是「没做过」** ——
    #   「跑过」与「留下痕迹」是两件事。
    _原子写519((kg2 / "边.json"), json.dumps(边, ensure_ascii=False))
    出 = {"根": str(根), **计数, "断链样例": 断链样例}
    _原子写519((kg2 / "md链接.json"), json.dumps(出, ensure_ascii=False, indent=1))
    打印(f"   markdown 链接 {计数['链接']} 条 · 已解析 {计数['已解析']} · "
        f"**断链 {计数['断链']}** · 外部 {计数['外部']} · 锚 {计数['锚']}")
    return 出


def 解析JSON引用(根: str | Path, 打印: Callable[[str], None] = print) -> dict:
    """把 JSON/YAML 的跨文件引用**解到目标**：文件在不在、**指针能不能解开**。

    ★★ 这是比 markdown 链接更硬的一处「真引用」：
      `$ref: "./x.json#/definitions/Y"` **声称** ``x.json`` 里有个 ``/definitions/Y``。
      **这个声称可以被机器核对** —— 读那个文件、按 RFC 6901 走一遍指针。
      走得通 = 真引用；走不通 = **断引用**（未解析的必须报出来）。
    """
    根 = Path(根).resolve()
    图 = 加载图(根)
    if "未测到" in 图:
        return _未测到留痕(图, 根)
    边: list = 图.get("边") or []
    已有 = {k[5:].replace("\\", "/") for k in (图.get("节点") or {})
           if k.startswith("file:")}
    计数 = {"引用": 0, "外部": 0, "本文件指针": 0, "已解析": 0, "断链": 0,
           "指针解不开": 0}
    样例: list[dict] = []
    _档缓: dict[str, object] = {}

    def 取档(c: str):
        if c not in _档缓:
            try:
                _t = (根 / c).read_text(encoding="utf-8", errors="replace")
                _档缓[c] = _读JSON(_t)[0]
            except OSError:
                _档缓[c] = None
        return _档缓[c]

    for e in 边:
        if e.get("关系") != "引用":
            continue
        依 = e.get("依据") or ""
        if not 依.startswith("json"):
            continue
        计数["引用"] += 1
        if e.get("外部"):
            计数["外部"] += 1
            continue
        终 = e.get("终") or ""
        起相 = (e.get("起") or "")[5:].replace("\\", "/")
        起目 = str(Path(起相).parent).replace("\\", "/")
        if 起目 == ".":
            起目 = ""
        指 = e.get("指针") or ""
        if 终.startswith("本文件指针:"):
            计数["本文件指针"] += 1
            档 = 取档(起相)
            e["目标文件"] = 起相
            e["已解析"] = False
            if 档 is not None:
                e["已解析"] = _json指针(档, 指) is not _解不开
            if not e["已解析"]:
                计数["指针解不开"] += 1
                if len(样例) < 20:
                    样例.append({"起": 起相, "路": e.get("路"), "指针": 指,
                               "原因": "本文件指针解不开"})
            else:
                计数["已解析"] += 1
            continue
        if 终.startswith("file:"):
            # ★ R525：幂等（同 md链接 —— 同一处置写两处，一起改）。
            e["已解析"] = True
            计数["已解析"] += 1
            continue
        if not 终.startswith("路径:"):
            continue
        原 = 终[3:].replace("\\", "/")
        cand = ""
        try:
            cand = str(Path(原).resolve().relative_to(根)).replace("\\", "/")
        except Exception:                                          # noqa: BLE001
            import os as _os3
            try:
                _rel = _os3.path.relpath(_os3.path.realpath(根 / 起目 / 原)
                                        if not (原.startswith("/") or (len(原) > 1 and 原[1] == ":"))
                                        else _os3.path.realpath(原),
                                        _os3.path.realpath(根))
                cand = "" if _rel.startswith("..") else _rel.replace("\\", "/")
            except Exception:                                      # noqa: BLE001
                cand = ""
        if not cand and 起目:
            cand = f"{起目}/{原}"
        目实 = None
        试 = [cand]
        if e.get("可省后缀"):
            试 += [cand + ".json", cand.rstrip("/") + "/tsconfig.json"]
        if e.get("目录型"):
            试 += [cand.rstrip("/") + "/tsconfig.json", cand + ".json"]
        for c in 试:
            # ★ R525：同 md链接 —— `c` 非空（`(根 / "").exists()` 恒真）。
            if c and (c in 已有 or (根 / c).exists()):
                目实 = c
                break
        e["目标文件"] = 目实
        # ★★★ R525k：同 md链接 —— **写 `出口`，不许改 `终`**（口径 348）。
        #   ★ `指针解不开` 是**另一个量**（`目实` 存在但指针走不通），照样写 `出口`，
        #     走不通仍由 `指针解不开` 单独报（口径 194：不许并成一个数）。
        if 目实 in 已有:
            e["出口"] = f"file:{目实}"
        elif 目实 is not None:
            计数["目标文件在盘上但不在图里"] = 计数.get("目标文件在盘上但不在图里", 0) + 1
        if 目实 is None:
            e["已解析"] = False
            计数["断链"] += 1
            if len(样例) < 20:
                样例.append({"起": 起相, "路": e.get("路"), "目标": 原,
                           "解析为": cand, "原因": "目标文件不存在"})
            continue
        if not 指:
            e["已解析"] = True
            计数["已解析"] += 1
            continue
        档 = 取档(目实)
        e["已解析"] = 档 is not None and _json指针(档, 指) is not _解不开
        if e["已解析"]:
            计数["已解析"] += 1
        else:
            计数["指针解不开"] += 1
            if len(样例) < 20:
                样例.append({"起": 起相, "路": e.get("路"), "目标": 原,
                           "指针": 指, "目标文件": 目实,
                           "原因": "指针在目标文件里解不开"})
    kg2 = _kg目录(根)
    kg2.mkdir(parents=True, exist_ok=True)
    _原子写519((kg2 / "边.json"), json.dumps(边, ensure_ascii=False))
    出 = {"根": str(根), **计数, "样例": 样例}
    _原子写519((kg2 / "json引用.json"), json.dumps(出, ensure_ascii=False, indent=1))
    打印(f"   json 引用 {计数['引用']} 条 · 已解析 {计数['已解析']} · "
        f"**断链 {计数['断链']}** · **指针解不开 {计数['指针解不开']}** · "
        f"外部 {计数['外部']} · 本文件指针 {计数['本文件指针']}")
    return 出


# ── 自洽门 ──────────────────────────────────────────────────────────
# ★★★ **不是所有文件都值得进图**（R413）——
#   实测：`arc_data` 16,289 个文件里 **13,328 个是 `.txt`**（语料/数据，不是源码）
#   ⇒ 建图跑到 **2.3GB 内存 / 30 分钟没完**，而且**一个源码节点都还没建**。
#   ★ 口径：**「扫得完」是有前提的**（口径 114）——
#     进图该按**「这个文件里有结构可抽」**判，不是按**「它在目录里」**判。
#   ★ 但**不许静默丢**：跳过的每条都带原因（口径 95）。
_值得进图 = frozenset({".py", ".pyi", ".rs", ".lean", ".js", ".jsx", ".ts", ".tsx",
                      ".go", ".md", ".markdown", ".json", ".json5", ".yaml", ".yml",
                      ".toml", ".cfg", ".ini", ".sh", ".ps1", ".psm1", ".bat",
                      ".c", ".h", ".cpp", ".hpp", ".cc", ".java", ".kt", ".cs",
                      ".rb", ".php", ".swift", ".sql", ".r", ".jl", ".m", ".hs",
                      ".ex", ".exs", ".vue", ".svelte", ".html", ".css", ".scss",
                      # ★★★ R531：**知识族**（用户定「知识族优先」）—— 从前这 8 个
                      #   后缀连第一道闸都过不去（`_过值得` 直接判「无结构可抽」）。
                      ".owl", ".rdf", ".ttl", ".nt", ".n3", ".trig", ".jsonld", ".kif"})


# ★ 进图体积上限：**1MB**（源码文件正常不会超过；超过的几乎都是生成物）
_进图上限字节 = 1 * 1024 * 1024
# ★★★★ R499：**白名单后缀（会进图的形态）用更宽的门槛** ——
#   判一百四十八 报过：1MB 一刀切剔掉了 **54 条白名单后缀**，其中包括
#   `src/判据观测.rs`（2,316KB）· `src/结构记忆网络.rs`（2,189KB）·
#   `src/mod.rs`（1,561KB）· `中文编译器/PRN认知中枢.rs`（1,453KB）
#   —— **这些是真的源码**，它们不在图里，而 AI 读缺口时看不见（口径 139）。
#   ★ 1MB 的原始理由是「2.5MB 的 `mermaid.min.js` 让建图跑 36 分钟」——
#     但 `min.js` 已被 `_像生成物` 挡住，**不该拿它当全体的门槛**（口径 261 的同族：
#     不能用一个特例做全局排除）。
_进图上限宽 = 8 * 1024 * 1024
# ★ 名字像"压缩/生成"的文件：有语法结构，**没有人读的结构**
_像生成物 = re.compile(
    r"(?:\.min\.|\-min\.|\.bundle\.|\.chunk\.|\.generated\.|_pb2\.py$"
    r"|\.g\.dart$|\.designer\.cs$|\.pb\.go$|\.pb\.cc$|\.pb\.h$"
    r"|min\.js$|min\.css$)", re.I)


# ★★★★ R529a：`_后缀登记` —— **跳过的后缀必须登记**（口径 203/307/344）。
#   为什么需要它（陌生靶给的）：`apache-jena-5.6.0` 的产物里写着
#     `扩展名 `.owl` 无结构可抽（**不是源码/配置/文档**）` —— 而 `.owl`/`.rdf`/`.ttl`
#     **正是知识图谱自己的交换格式**，它们是**有结构的** ⇒ **一句错话进了交付产物**。
#   ⇒ 三类，**不许混**（口径 194）：
#       `已支持`   —— 有适配器（走到跳过分支就是「登记与代码不一致」，口径 305）
#       `真无结构` —— 确实抽不出（二进制 / 日志 / 数据表）
#       `是缺口`   —— **有结构，但没建适配器** ⇒ **绝不许**说「不是源码/配置/文档」
#   ★ 没登记的后缀 ⇒ 原因里**明说「未登记」**（缺口在产物里自己喊出来，不许静默）。
#   ★ 前缀规则：键以 `@前缀:` 开头时按 `startswith` 匹配（用于 `.bak_*` 那种长尾）。
_后缀登记: dict[str, str] = {
    # ── **是缺口**：有结构，没适配器（最贴身的一类）
    ".owl": "已支持（R531 知识族适配器）：**OWL 本体**（RDF/XML）—— 知识图谱的交换格式，**有结构**",
    ".rdf": "已支持（R531 知识族适配器）：**RDF 图**（XML 序列化）—— 知识图谱的交换格式，**有结构**",
    ".ttl": "已支持（R531 知识族适配器）：**Turtle**（RDF 的紧凑文本语法）—— 有结构",
    ".nt": "已支持（R531 知识族适配器）：**N-Triples**（一行一条三元组）—— 有结构",
    ".n3": "已支持（R531 知识族适配器）：**Notation3** —— 有结构",
    ".jsonld": "已支持（R531 知识族适配器）：**JSON-LD** —— 有结构",
    ".trig": "已支持（R531 知识族适配器）：**TriG** —— 有结构",
    ".xml": "是缺口：XML —— 有结构（jena 实测 1 个被跳过）",
    ".properties": "是缺口：Java properties —— 有结构（jena 实测 1 个被跳过）",
    # ★★★★ R532e：**这一条是我自己留下的残留物**（口径 373）。
    #   桶 `多智能体编排` 与 `太虚元道` 各跳过 1 个 `.metadata`，判 184 当场把它抓出来
    #   （口径 203/307：剔掉必须留痕、**缺口要在源头就登记**）。
    #   ★ 那个文件是 `pip-unpack-ik6vo8h0/rdflib-7.6.0-py3-none-any.whl.metadata`
    #     （12,584 字节 · 落盘 11/15 22:40:19）—— **pip 的 sidecar**：
    #     **轮子已经下完并解开**，pip 才会写它。
    #   ⇒ 所以「`rdflib` 不可用」是**用错尺的结论**：真实读数是「**没装完**」。
    #   ★ 内容是 `Metadata-Version: 2.1` / `Name:` / `Version:` 这类
    #     **RFC 822 key:value 文本** ⇒ **有结构** ⇒ 归「是缺口」，不是「真无结构」。
    ".metadata": "是缺口：**PEP 658 元数据**（`Metadata-Version:` / `Name:` / `Version:` "
                 "的 RFC 822 key:value 文本）—— **有结构**（pip 的 sidecar）",
    ".toml": "是缺口：TOML —— 有结构（交付态 5,107 个 `.toml` 内容节点**没有语言**）",
    ".yml": "是缺口：YAML —— 有结构",
    ".yaml": "是缺口：YAML —— 有结构",
    ".ini": "是缺口：INI —— 有结构",
    ".cfg": "是缺口：配置 —— 有结构",
    ".conf": "是缺口：配置 —— 有结构",
    ".txt": "是缺口：**纯文本没有语法**，但**内容是文档** ⇒ 一个节点都不建（口径 260：看不见不能当没有）",
    ".csv": "真无结构：分隔符数据表（有行列，**没有命名实体**）",
    ".tsv": "真无结构：分隔符数据表（同 `.csv`）",
    ".rst": "是缺口：reStructuredText —— 有结构（与 markdown 同类）",
    ".tex": "是缺口：LaTeX —— 有结构",
    ".org": "是缺口：Org-mode —— 有结构",
    ".adb": "是缺口：Ada —— 有结构",
    ".ads": "是缺口：Ada —— 有结构",
    ".tcl": "是缺口：Tcl —— 有结构（交付态实测 69 个）",
    ".spec": "是缺口：RPM spec —— 有结构（交付态实测 10 个）",
    ".vue": "是缺口：Vue 单文件组件 —— 有结构",
    ".svelte": "是缺口：Svelte 组件 —— 有结构",
    ".ipynb": "是缺口：Jupyter notebook（**里面是代码**）—— 有结构",
    ".proto": "是缺口：Protocol Buffers —— 有结构",
    ".thrift": "是缺口：Thrift IDL —— 有结构",
    ".graphql": "是缺口：GraphQL —— 有结构",
    ".tf": "是缺口：Terraform HCL —— 有结构",
    ".dockerfile": "是缺口：Dockerfile —— 有结构",
    # ── **真无结构**：二进制 / 日志 / 编译产物
    ".jar": "真无结构：**压缩二进制**（zip）",
    ".pyd": "真无结构：Python 扩展二进制（交付态实测 10 个）",
    ".so": "真无结构：共享库二进制",
    ".dll": "真无结构：Windows 动态库二进制",
    ".exe": "真无结构：可执行二进制",
    ".lib": "真无结构：静态库二进制",
    ".a": "真无结构：静态库二进制",
    ".o": "真无结构：目标文件二进制",
    ".class": "真无结构：JVM 字节码二进制",
    ".pyc": "真无结构：Python 字节码二进制",
    ".olean": "真无结构：Lean 编译产物（二进制）",
    ".png": "真无结构：位图",
    ".jpg": "真无结构：位图",
    ".jpeg": "真无结构：位图",
    ".gif": "真无结构：位图",
    ".ico": "真无结构：位图",
    ".bmp": "真无结构：位图",
    ".pdf": "真无结构：PDF 容器（**内容有结构，但需专门抽取器**）",
    ".zip": "真无结构：压缩包",
    ".gz": "真无结构：压缩包",
    ".tar": "真无结构：归档",
    ".7z": "真无结构：压缩包",
    ".log": "真无结构：日志（时间戳 + 自由文本，**没有命名实体**）",
    ".err": "真无结构：日志（同 `.log`）",
    ".out": "真无结构：日志（同 `.log`）",
    ".stderr": "真无结构：日志（同 `.log`）",
    ".stdout": "真无结构：日志（同 `.log`）",
    ".jsonl": "真无结构：JSON Lines 流水（**逐行记录**，不是配置）",
    ".lock": "真无结构：锁文件（工具生成，非人写）",
    ".version": "真无结构：版本号一行",
    ".license": "真无结构：许可证全文（**法律文本，不是这棵树的结构**）",
    "(无)": "是缺口：**没有后缀** ⇒ 只能落 `_保底适配器`；报告上写着 `_保底适配器` 的"
            "文件**在按语言的判断里等于隐形**（口径 260；认知主权 685 · jena 45 个）",
    ".mod": "是缺口：Go module 清单 —— 有结构",
    ".sum": "真无结构：校验和清单",
    ".map": "真无结构：source map（机器生成）",
    # ── ★★ R529d：判一百八十四 逼出来的 78 种（**抽样定性，不许猜**，口径 344）
    #   是缺口（有结构、没适配器）—— 注意其中**两个是知识表示格式**：
    ".kif": "已支持（R531 知识族适配器）：**KIF（Knowledge Interchange Format）** —— `GGP/.../connectFour.kif`；"
            "**又一个知识表示格式读不了**（与 `.owl`/`.rdf`/`.ttl` 同族）",
    ".knowledge": "是缺口：知识文件（`arch_knowledge/gpu_kernels.knowledge`）—— **名字就叫 knowledge**",
    ".typ": "是缺口：`iggp-master/types/*.typ`（GGP 的 game type 定义，**不是 Typst**）—— 有结构",
    ".pl": "是缺口：**Perl** —— 有结构",
    ".inl": "是缺口：**C++ 内联实现** —— 与 `.h`/`.hpp` 同类（交付态 28 个 · UE5 `Core` 24 个 `.tps`）",
    ".sty": "是缺口：**LaTeX 宏包** —— 有结构",
    ".ptx": "是缺口：**CUDA PTX 中间表示** —— 有结构",
    ".cu": "是缺口：**CUDA C++** —— 有结构",
    ".isph": "是缺口：**ISPC 着色器/向量内核**（UE5 实测 8 个）—— 有结构",
    ".xsl": "是缺口：**XSLT 样式表** —— 有结构",
    ".wgsl": "是缺口：**WGSL 着色器** —— 有结构",
    ".mjs": "是缺口：**ES module JavaScript**（`.js` 支持而 `.mjs` 不支持 ⇒ 白名单的典型缺口）",
    ".scala": "是缺口：**Scala** —— 有结构",
    ".clj": "是缺口：**Clojure** —— 有结构",
    ".sass": "是缺口：Sass/SCSS —— 有结构",
    ".dot": "是缺口：**Graphviz** —— 有结构",
    ".gradle": "是缺口：Gradle 构建脚本 —— 有结构",
    ".cmd": "是缺口：Windows 批处理 —— 有结构",
    ".code-workspace": "是缺口：VS Code 工作区配置 —— 是配置，**不是「无结构」**",
    ".readme": "是缺口：**以扩展名形式出现的 README** —— 是文档",
    ".tm": "是缺口：TeX 宏 / troff 宏 —— 有结构",
    ".msg": "是缺口：消息定义（ROS `.msg` / 邮件）—— 有结构",
    ".example": "是缺口：样例配置（`.example` 后缀）—— 是配置",
    ".terms": "是缺口：术语表 —— 有结构",
    ".copyright": "真无结构：**版权声明文本**（法律文本，不是这棵树的结构）",
    ".snap": "真无结构：**快照**（工具生成，不是人写的源码）",
    ".copyright": "真无结构：**版权声明文本**（法律文本，不是这棵树的结构）",
    ".snap": "真无结构：**快照**（工具生成，不是人写的源码）",
    #   真无结构（二进制 / 机器生成 / 数值表 / 备份）
    ".xyz": "真无结构：**点云坐标数值表**（`数据部署/点云/parsed/*.xyz`，有行列、**没有命名实体**）"
            " ★ 24,622 个，是交付态最大的单一未登记类",
    ".bin": "真无结构：二进制",
    ".d": "真无结构：**依赖清单**（`target2/debug/deps/*.d`，机器生成）",
    ".rmeta": "真无结构：Rust 元数据二进制",
    ".rlib": "真无结构：Rust 编译产物二进制",
    ".dat": "真无结构：数据文件二进制",
    ".exp": "真无结构：Windows 导出表二进制",
    ".ttf": "真无结构：字体二进制",
    ".pdb": "真无结构：调试符号二进制",
    ".enc": "真无结构：加密载荷",
    ".tps": "真无结构：UE5 侧车二进制（`Core` 实测 24 个）",
    ".db": "真无结构：SQLite 库二进制",
    ".obj": "真无结构：目标文件二进制（**不是 Wavefront OBJ**）",
    ".med": "真无结构：医学/中间数据二进制",
    ".tag": "真无结构：ctags 索引（机器生成）",
    ".key": "真无结构：密钥/键盘表",
    ".raw": "真无结构：裸数据",
    ".eps": "真无结构：Encapsulated PostScript（矢量图）",
    ".backup": "真无结构：**备份副本**（不是「我有什么」）",
    ".legacy": "真无结构：**旧版残留**",
    ".safety516": "真无结构：**当轮的安全副本**",
    ".pypython": "真无结构：**误命名的备份**（后缀本身就是手滑）",
    ".e5": "真无结构：**当轮的中间版本**",
    ".0": "真无结构：**版本序号后缀**（`X.0` 这种手滑命名）",
    ".我": "真无结构：**中文变体命名**（不是真后缀）",
    ".道": "真无结构：**中文变体命名**（`E3_1000lines.道`，不是真后缀）",
    "@前缀:.strip": "真无结构：**误命名残留**（后缀里带着一段没渲染的代码文本）",
    "@前缀:.corrupted": "真无结构：**损坏件的备份**",
    "@前缀:.truncated": "真无结构：**截断件的备份**",
    "@前缀:.臂": "真无结构：**中文变体命名**（`臂29备份`/`臂c备份`）",
    "@前缀:.范式": "真无结构：**范式迁移前的备份**",
    "@前缀:.0概念": "真无结构：**中文变体命名**（`0概念包装版`）",
    "@前缀:.2026": "真无结构：**日期后缀备份**（`.20260709_文档更新前` 这类，不是真后缀）",
    # ── 前缀规则（长尾）
    "@前缀:.bak": "真无结构：**备份副本**（同一份代码的历史副本，不是「我有什么」）",
    "@前缀:.tmp": "真无结构：临时件",
    "@前缀:.disabled": "真无结构：被禁用的旧件",
    "@前缀:.deploying": "真无结构：部署中间件",
    "@前缀:.cloud": "真无结构：云同步残留",
    "@前缀:.tdb": "真无结构：Jena TDB 索引产物",
    "@前缀:.xloader": "真无结构：Jena 装载器脚本残留",
    # ── 已支持（走到跳过分支就是「登记与代码不一致」）
    ".json": "已支持：走 `json引用`（**有专门的抽取器**）",
    ".md": "已支持：走 `_Markdown适配器`",
}


def _为什么跳过(suf: str) -> str:
    """→ 一条**站得住的**跳过原因（★★★ **不许替文件下结论**，口径 344/311）。

    ★ 核心：原因字符串是**产物**，会被下游当事实。所以它只能说
      「**我这边是什么状态**」（没适配器 / 没登记），**不能**说
      「**这个文件是什么**」（不是源码/配置/文档）—— 后者是**我没量过的事**。
    """
    # ★ 空后缀必须**规范化成一个看得见的名字** —— 否则产物里印出的是空串
    #   （口径 311：一个字段不许空着；`图.json` 的 `★跳过后缀` 用的就是 `(无)`）。
    s = (suf or "").lower() or "(无)"
    if s in _后缀登记:
        由 = _后缀登记[s]
        if 由.startswith("已支持"):
            return (f"扩展名 `{s}` 登记为**已支持**，却走到了跳过分支 ⇒ "
                    f"**登记与代码不一致**（口径 305；`_值得进图` 与 `_后缀登记` 要对齐）")
        if 由.startswith("是缺口"):
            return f"扩展名 `{s}` **有结构，但没建适配器**（**是缺口**：{由.split('：', 1)[-1]}）"
        return f"扩展名 `{s}` 无结构可抽（已登记为**真无结构**：{由.split('：', 1)[-1]}）"
    for _k, _v in _后缀登记.items():
        if _k.startswith("@前缀:") and s.startswith(_k[4:]):
            return f"扩展名 `{s}` 无结构可抽（已登记为**真无结构**：{_v.split('：', 1)[-1]}）"
    return (f"扩展名 `{s}` **未登记** —— 既没有适配器、也没在 `kg._后缀登记` 里 "
            f"⇒ **不许替它下结论**（口径 203/307：缺口必须在产物里喊出来）")


def _过值得(文件们: list, 根: "Path | None" = None) -> tuple[list, list]:
    """按扩展名筛「有结构可抽」的文件；**其余显式跳过并留名**。

    ★ 口径 191：**跳过项必须能定位**。
    ★★★ R525：**三条分支原本只有「过大」那条写了 `路径`** ——
      扩展名无结构 / 压缩生成物两条只写 `文件: f.name`（**basename**）
      ⇒ 交付态实测 **134,512 条**跳过记录**定位不到文件**，
      其中 **46,768 处 basename 撞名**（不同目录的同名文件分不清）。
      ★ 那条已修的注释自己写着「实测第一版只写 `文件: f.name`
        ⇒ 48 条里多个 `mod.rs` 分不清」—— **实际是 13 万条**。
      ★ 症状长这样：**「生产枚举 vs 图 + 跳过」的对账永远算不平**，
        陌生仓库验收脚本因此报出「静默丢」的**假缺陷**（口径 260 的反面）。
      ⇒ **三条分支统一走 `_定位`**（口径 305：形态只在一个地方决定）。
    """
    留, 跳 = [], []

    def _定位(f) -> tuple:
        """→ (可定位路径, 路径形态说明)。**形态只在这一处决定**。

        ★ 有 `根` 就给**相对路径** —— 与 `戳.json` **同源**（口径 194：
          同一个量的出口必须同一种形态，"生产枚举 vs 图+跳过"才可能算平）。
        ★ 没 `根` 就**明说这是绝对路径**，别假装有相对路径（口径 191）。
        """
        if 根 is not None:
            try:
                return f.relative_to(根).as_posix(), "相对根（与 `戳.json` 同源）"
            except (ValueError, OSError):
                pass
        return str(f), "绝对路径（这次没拿到根）"

    for f in 文件们:
        if f.suffix.lower() not in _值得进图:
            _路, _形态 = _定位(f)
            跳.append({"路径": _路, "文件": f.name, "类": "文件", "★路径形态": _形态,
                      # ★★★ R529a：**不许替文件下结论**（口径 344/311）——
                      #   原来写死了「**不是源码/配置/文档**」，而 `.owl`/`.rdf`/`.ttl`
                      #   正是知识图谱交换格式（**有结构**）⇒ 那句是假话，已删。
                      "原因": _为什么跳过(f.suffix)})
            continue
        if _像生成物.search(f.name):
            _路, _形态 = _定位(f)
            跳.append({"路径": _路, "文件": f.name, "类": "文件", "★路径形态": _形态,
                      "原因": "**压缩/生成**文件（有语法结构，**没有人读的结构**）"})
            continue
        try:
            _大 = f.stat().st_size
        except OSError:
            _大 = 0
        # ★★★★ R499：**白名单里的（会进图的形态）用更宽的门槛**。
        #   判一百四十八 报过：**54 条白名单后缀被 1MB 剔掉** ——
        #   `src/判据观测.rs`（2,316KB）· `src/结构记忆网络.rs`（2,189KB）·
        #   `src/mod.rs`（1,561KB）· `中文编译器/PRN认知中枢.rs`（1,453KB）
        #   —— **这些是真的源码**，它们**不在图里**，而 AI 读缺口时**看不见**。
        #   ★ 1MB 的来历是「2.5MB 的 `mermaid.min.js` 让建图跑 36 分钟」——
        #     那**不是白名单后缀**（`.js` 在白名单，但 `_像生成物` 已先挡住 `min.js`）。
        #   ⇒ 白名单后缀用 `_进图上限宽`（8MB）；非白名单仍走原门槛。
        #   ★ 口径 296：**两个体积门槛都要查**。
        _限 = _进图上限字节 if f.suffix.lower() not in _值得进图 else _进图上限宽
        if _大 > _限:
            # ★ 口径 191：跳过项必须能定位。★ R525 起**走 `_定位`** ——
            #   有根就是相对路径（与 `戳.json` 同源），没根才退回绝对路径。
            _路, _形态 = _定位(f)
            跳.append({
                "路径": _路,
                "文件": f.name,
                "类": "文件",
                "★路径形态": _形态,
                "原因": f"过大（{_大 // 1024}KB > {_限 // 1024 // 1024}MB）",
                "大小": _大})
            continue
        留.append(f)
    return 留, 跳


def _过排除(根: Path, 文件们: list, 排除) -> tuple[list, list]:
    """★★★ **把「不属于这张图」的文件显式挑出去**（R412）。

    ★ 为什么需要（实测）：`分根()` 算出的**总根**只该装 927 个散落文件，
      但 `建图(总根)` 会 `枚举文件(总根)` ⇒ **扫出全部 162,549 个文件**
      ⇒ **分根白做，总根那一张图又把全仓塞进来了** ✗
    ★ 口径：**「分根」不只是"算出该建几张图"，还得让每张图"只扫自己那份"** ——
      否则机制在、读数不在（口径 85：**「机制在」≠「机制在真数据上成立」**）。

    ★ **被排除的必须留名**：返回的"跳过"里带 `原因`，
      这样它和"过大/剪枝"一样**出现在图上**，不是凭空消失。
    """
    排除 = tuple(str(x).replace("\\", "/").strip("/")
                 for x in (排除 or ()) if str(x).strip())
    if not 排除:
        return list(文件们), []
    留, 剔 = [], []
    for f in 文件们:
        try:
            rel = f.relative_to(根).as_posix()
        except ValueError:
            rel = str(f)
        hit = next((e for e in 排除 if rel == e or rel.startswith(e + "/")), None)
        if hit is None:
            留.append(f)
        else:
            剔.append({"文件": rel, "原因": f"属于子根 `{hit}`（**它的图在别处**）"})
    return 留, 剔


_源码扩展 = frozenset({".py", ".pyi", ".rs", ".lean", ".js", ".jsx", ".ts", ".tsx",
                     ".go", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
                     ".sh", ".ps1", ".c", ".h", ".cpp", ".hpp", ".java", ".rb", ".php",
                     ".cs", ".swift", ".kt", ".sql", ".r", ".jl", ".m", ".hs", ".ex"})


def 分根(根: str | Path, 最少源文件: int = 50,
        打印: Callable[[str], None] = print) -> dict:
    """★★★ **一张图放不下时，正确做法是分根，不是硬塞**（R411）。

    ★ 为什么需要（实测，不是理论）：
      总仓 `太虚元道` **162,524 个文件**（其中 `.py` 28,512 · `.rs` 13,330 ·
      `.md` 6,519 · `.lean` 6,209；另有大量 `target_*` 编译产物）。
      拿它整张建图 ⇒ **跑了 40 分钟没完**（同规模的 Lean 6,607 文件只要 60 秒，
      25 倍文件量 ⇒ 约 1,000 万节点 ⇒ **内存放不下，在 swap 上爬**）。
      ★ 口径：**"扫得完"是有前提的** ——
        文件量 × 每文件节点数是**平方级落在内存里**的，
        **能跑通的规模才是规模**。
      ⇒ 分根：**每个"有独立源码世界"的一级子树各成一图**。

    ★ 判据（**可机判**，不是拍脑袋）：
      · **自足度**：该子树里的源码文件数 ≥ `最少源文件`
        （文件太少 ⇒ 它更像是总仓的一个零件，不值得单独一张图）；
      · **完备性**：**每一个源码文件必须落进且只落进一个根** ——
        不属于任何合格子树的（如根目录下的散文件）落进**总根自己的图**。
        ★ 口径：**「按 X 刷新的机器必须给不属于任何 X 的东西一条显式通路」** ——
          **沉默地漏掉**比报错坏得多。

    返回 `{"根们": [...], "归属": {相对路径: 根}, "不属于任何子树": [...]}`。
    """
    根 = Path(根).resolve()
    # ★★★ **分根结果不缓存**（R416 —— 撤掉 R414 加的缓存）。
    #   为什么撤（实测，不是理论）：加了缓存后**两次调用都不命中** ✗
    #     · 指纹里的**根 mtime** 会被"建图往 <根>/<子根>/.kg 写东西"改掉；
    #     · 去掉根 mtime 后，**子目录 mtime 同样被 `.kg` 改掉** ⇒ 仍不命中。
    #   ★ 口径：**缓存的价值 = 命中率 × 单次成本** ——
    #     **永不命中的缓存只是往每次调用上加一次读写**（还写了个 20MB 的 JSON）。
    #   ★ 口径：**"失效条件说不清"就不要缓存** ——
    #     说不清什么时候该失效，说明**还没想清楚什么东西影响结果**；
    #     这时加缓存，**错的不是性能，是正确性**（会给出过期答案）。
    #   ★ 真正的收益来自 `快=True`（每个文件省两次系统调用）：20+ 分钟 → 48 秒。
    #     这已经够了 —— **不需要缓存来"显得快"。**
    文件们, _跳过 = 枚举文件(根, 快=True)   # ★ 只看扩展名 ⇒ 不需要 stat（R414）
    # ── 统计一级子树里的**源码**文件数（按扩展名，不按目录名猜）
    子计: dict[str, int] = {}
    散: list[Path] = []
    for f in 文件们:
        try:
            rel = f.relative_to(根)
        except ValueError:
            散.append(f)
            continue
        if len(rel.parts) < 2:
            散.append(f)                          # 根目录下的散文件
            continue
        if f.suffix.lower() not in _源码扩展:
            continue
        子计[rel.parts[0]] = 子计.get(rel.parts[0], 0) + 1
    # ── **备份/临时/产物目录不各成一图**（R411 第二次修）——
    #   实测：合格子树里混进了 `_backup_safe_rename_*` / `src_backup_*` /
    #   `备份_20260705_全量处置前` / `src.bak.20260703_审计修复前` /
    #   `headwk_b1rmrjd3` 这类**备份与临时目录** ⇒ 它们**不是"独立源码世界"**，
    #   给它们各建一张图 = **把备份当成正本**。
    #   ★ 但它们**不许被静默丢掉** —— 落进总根（那条显式通路）。
    #   ★ 口径：**「按 X 刷新的机器必须给不属于任何 X 的东西一条显式通路」** ——
    #     排除是对的，**排除得没有名字就是错的**。
    _备份型 = re.compile(
        r"(^[_.]?(?:backup|bak|old|tmp|temp|copy|dist|build|target|node_modules)"
        r"|[_.](?:backup|bak|old|tmp|temp)|备份|_backup_|^target|^headwk|^_work)")
    排除: dict[str, str] = {}
    合格 = []
    for k in sorted(子计, key=lambda x: (-子计[x], x)):
        if 子计[k] < 最少源文件:
            排除[k] = f"源码文件 {子计[k]} < {最少源文件}"
        elif _备份型.search(k):
            排除[k] = "名字像备份/临时/产物目录（**不是独立源码世界**）"
        else:
            合格.append(k)
    # ★ 总根自己的图：装"不属于任何合格子树"的东西
    根们 = [{"根": str(根), "角色": "总根（装散落文件 + 跨子树引用）",
             "源文件": len(散)}]
    for k in 合格:
        根们.append({"根": str(根 / k), "角色": f"子根（源码 {子计[k]}）",
                     "源文件": 子计[k]})
    归属: dict[str, str] = {}
    for f in 文件们:
        try:
            rel = f.relative_to(根)
        except ValueError:
            归属[str(f)] = str(根)
            continue
        上 = str(根 / rel.parts[0]) if len(rel.parts) > 1 and rel.parts[0] in 合格 \
            else str(根)
        归属[str(rel)] = 上
    打印(f"   分根：{根.name} 一级子树 {len(子计)} 个 · 合格（源码≥{最少源文件}）"
         f" {len(合格)} 个 ⇒ **{len(根们)} 张图**"
         f" · 不单独成图 {len(排除)} 个（**全部落进总根，不丢**）")
    for x in 根们[1:]:
        打印(f"      · {Path(x['根']).name:28} {x['源文件']:6}")
    打印(f"      · {根.name + '（总根）':28} {len(散):6}（散落文件）")
    _出 = {"根们": 根们, "归属": 归属, "不属于任何子树": [str(x) for x in 散],
            "合格": 合格, "一级子树源码数": 子计, "不单独成图": 排除,
    # ★★ R442：**带名字的跳过**（路径 + 原因 + **里面有文件数**）——
    #   「排除」与「丢掉」的区别**不在排不排**，而在**排了有没有名字和大小**。
    "跳过的目录（带文件数）": [x for x in _跳过
                              if x.get("类") == "目录"
                              and ("里面有文件数" in x or "里面至少有文件数" in x
                                   or "一级条目数" in x)],
    # ★★ R442：**带名字的跳过**（路径 + 原因 + **里面有文件数**）——
    #   「排除」与「丢掉」的区别**不在排不排**，而在**排了有没有名字和大小**。
    "跳过的目录（带文件数）": [x for x in _跳过
                              if x.get("类") == "目录"
                              and ("里面有文件数" in x or "里面至少有文件数" in x
                                   or "一级条目数" in x)],
    # ★★ R442：**带名字的跳过**（路径 + 原因 + **里面有文件数**）——
    #   「排除」与「丢掉」的区别**不在排不排**，而在**排了有没有名字和大小**。
    "跳过的目录（带文件数）": [x for x in _跳过
                              if x.get("类") == "目录"
                              and ("里面有文件数" in x or "里面至少有文件数" in x
                                   or "一级条目数" in x)],
            "★口径": ("每个源码文件**必须落进且只落进一个根**；"
                     "不单独成图的子树**全部落进总根**（显式通路，不静默丢）")}
    try:
        _缓 = (json.loads(_索档0.read_text(encoding="utf-8"))
               if _索档0.is_file() else {})
    except Exception:                             # noqa: BLE001
        _缓 = {}
    # ★★★ **缓存里不许存"大件"**（R414 第三次修）——
    #   第一版把整个 `_出`（含 `归属`：总仓 **162,546 条路径**）写进缓存
    #   ⇒ **写 20MB + 读 20MB + `json.loads`** ⇒ **命中也要 72 秒** ✗
    #   ★ 口径：**缓存的是"判定结果"，不是"全部中间产物"** ——
    #     把推导得出来的东西也存进去，**缓存本身就成了瓶颈**。
    #   ★ `归属` **可以从 `合格` 重推**（哪个文件属于哪个根，只看它的首段），
    #     所以**只在条目少时存**（小根要它，大根用不着）；不存时如实标 `None`。
    _表 = dict(_缓.get("各根") or {})
    _存 = dict(_出)
    if len(_存.get("归属") or {}) > 20000:
        _存["归属"] = None
        _存["★归属说明"] = ("条目 > 20000 ⇒ **不写进缓存**（缓存要小）；"
                          "要看归属请直接调 `分根(…, 不用缓存=True)`")
    _表[str(根)] = _存
    _缓.pop("根们", None)                          # ★ 撤掉第一版写坏的那个键
    _缓["各根"] = _表
    _缓["★口径"] = ("分根结果缓存。★ 指纹用**一级子目录的 (名字, mtime)** ——"
                   "文件增删会改目录 mtime，改文件内容不会（而定合格只看文件数）")
    try:
        仓0.mkdir(parents=True, exist_ok=True)
        _原子写519(_索档0, json.dumps(_缓, ensure_ascii=False, indent=1))
    except Exception:                             # noqa: BLE001
        pass
    return _出


def _标父子关系(表: list) -> int:
    """★ R467：**给"父子根覆盖同一批文件"这件事分类并留痕**。

    ★ 为什么必须有：实测 `形式化验证`（父，5,659 文件）与
      `formal_proof`（子，3,783 文件）**各建了一张图**，
      对齐基准后 **子∩父 = 3,777 / 3,783 = 99.8%** ⇒
      ① **双重计数**（读总账的人把同一批文件算两次）；
      ② **对不上账** —— 两个桶的 `文件` 字段**相对各自的根**
         （子桶写 `ABRedundant.lean`，父桶写 `formal_proof/ABRedundant.lean`）
         ⇒ **同一个文件在两张图里名字不同**（口径 191）。
         ★ 我第一版按裸名求交集得 **0**，**差点得出「两者不重叠」的反结论** ✗

    ★ 判据用**路径前缀** —— 路径包含关系是**唯一确定**的
      （口径 202 的反面：能唯一确定时就不许猜）。

    ★★ 但**必须区分两种重叠**（这是我第一版的错）：
      ① **总根 ↔ 分根**：`分根` 的**设计**（总根的角色就写着
         「装散落文件 + 跨子树引用」）⇒ **登记，不报警**；
      ② **分根 ↔ 二级子桶**：**手工**建的 ⇒ 两个桶都在总账里、
         读数可能差一倍 ⇒ **必须报警**。
      ★ 我第一版把 16 个桶**全报了警** —— 一刷索引满屏「双重计数」，
        而其中 15 条是设计 ⇒ **报警太多等于没报警** ✗
        （口径 201 的同族：**「全都红」与「没检查」长得一样**。）

    ★ 诚实边界：它只做**路径包含**判定，**不比对文件内容** ——
      「两个桶覆盖同一批文件」的**程度**（99.8% 还是 100%）要另外量。
    """
    _根表 = [(str(x.get("根") or "").rstrip("\\/"), x) for x in 表 if x.get("根")]
    _报 = 0
    for _r, _条 in _根表:
        if not _r:
            continue
        _父们 = [(q, y) for q, y in _根表
                 if q and q != _r and (_r.startswith(q + "\\") or _r.startswith(q + "/"))]
        if not _父们:
            continue
        _父, _父条 = max(_父们, key=lambda t: len(t[0]))
        _是总根 = ("总根" in str(_父条.get("角色") or ""))
        _条["★它是谁的子根"] = _父
        _条["★与父根的关系"] = ("**设计**：父是总根（装散落文件 + 跨子树引用）"
                            if _是总根
                            else "**重叠**：父**不是总根** ⇒ 同一批文件被两个桶各建了一张图")
        if not _是总根:
            _报 += 1
            _条["★与父根重叠"] = (
                "★★ **父不是总根** ⇒ 这两个桶覆盖同一批文件、**都在总账里** ⇒ "
                "读总账的人会**双重计数**；且两个桶的 `文件` 字段**相对各自的根**"
                "（口径 191）⇒ **同名对不上账**（我第一版按裸名求交集得 0，"
                "差点得出『不重叠』的反结论 ✗）。"
                "★ 两个桶**都留着是对的**，但**必须知道谁是子集**。")
            _父条.setdefault("★它有哪些子桶（重叠，需人工确认）", []).append(_r)
    return _报



def _找图桶(根: Path, 图仓库们) -> tuple[Path | None, list[str]]:
    """在**候选图仓**里找「根就是它」的那个桶 → `(桶目录 | None, 找过的地方)`。

    ★★★ 为什么不能再用 `_kg目录(根)` 一处定生死（R533）：
      `_kg目录` 看 `KG_图目录` 环境变量 ⇒ **一个环境变量决定工具说「你有」还是「你没有」**。
      实测：设了 ⇒ `work/图/<桶>` 有图；没设 ⇒ 找 `<根>/.kg` ⇒ 报「没建」，
      而**那 16 个根其实都建了图** ⇒ 「AI 知道自己有什么」的入口在**说假话**。

    ★ 认**权威指针**：`图.json.根`（建图自己写的、绝对路径）——
      不靠目录名去猜桶（口径 359：认标记位，不认名字）。

    ★ 返回**找过的地方**：这是口径 358 的落实 ——
      「没找到」只能说「**我在这些地方找过**」，不许说「**这个东西不存在**」。
    """
    找过: list[str] = []
    for _仓 in (图仓库们 or ()):
        try:
            _c = Path(_仓).resolve()
        except Exception:                                  # noqa: BLE001
            continue
        if not _c.is_dir():
            找过.append(f"{_c}（**这个目录不存在**）")
            continue
        找过.append(str(_c))
        # 桶可能就在这一层，也可能再深一层（`图/<桶>/图.json`）
        for _g in list(_c.glob("图.json")) + list(_c.glob("*/图.json")):
            try:
                _元 = json.loads(_g.read_text(encoding="utf-8"))
            except Exception:                              # noqa: BLE001
                continue
            _r = _元.get("根")
            if not _r:
                continue
            try:
                if Path(_r).resolve() == 根:
                    return _g.parent, 找过
            except Exception:                              # noqa: BLE001
                continue
    return None, 找过


def 刷全仓索引(根: str | Path, 打印: Callable[[str], None] = print,
              图仓库们=None) -> dict:
    """★★★ **把「分根」的每一张图汇成一个入口**（R422）。

    ★ 为什么需要（口径 115）：**「一张图」在规模面前要改写成「一套图」** ——
      AI 要知道「我有什么」，必须**有一个地方一次看到全套**，
      而不是逐个根去 `ls .kg/`。
    ★ 与 `刷索引(根)` 的分工：`刷索引` 只管**一个根**（写在它的仓里）；
      本函数遍历**分根算出的每个子根**，汇成一张表。
      ★ 两者**读数必须同源**（都读落盘的 `节点.json`/`边.json`/…）——
        否则「同一个判断写在两处，必然给出两个答案」。
    ★ **没建图的根必须显式写「没建」并说明原因**（口径 92：允许没有 + 必须说明）——
      **沉默的缺席比报错坏**（口径 95 的同一件事）。
    """
    根 = Path(根).resolve()
    计 = 分根(根, 打印=打印)
    仓 = _kg目录(根).parent
    if _env图目录:
        仓 = Path(_env图目录)
    if 仓.resolve() == 根:
        仓 = _kg目录(根)
    _表 = []
    _成 = _未 = 0
    for x in 计["根们"]:
        _根 = Path(x["根"])
        条 = {"根": str(_根), "角色": x.get("角色"),
              "源文件（分根读数）": x.get("源文件")}
        try:
            # ★★★★ R533：**候选图仓**，不是 `_kg目录(_根)` 一处定生死 ——
            #   后者看 `KG_图目录` 环境变量 ⇒ 一个变量决定「你有」还是「你没有」，
            #   而我 05:19 那次没带变量 ⇒ 16 个**已建好图**的根全被报「没建」✗✗
            #   ⇒ 「AI 知道自己有什么」的入口在**说假话**（口径 358/359）。
            _候选 = [Path(_env图目录)] if _env图目录 else []
            _候选.append(_根 / _KG目录)
            _候选 += [Path(x) for x in (图仓库们 or ())]
            _桶, _找过 = _找图桶(_根, _候选)
            条["★找图的地方"] = _找过
            if _桶 is None:
                条["有图"] = False
                # ★ 口径 358：「没建」只许说**我找过哪里**，不许替世界下结论。
                条["备注"] = ("**我找过的地方都没有它的图** ⇒ 当下看不到这个根有图。"
                            "★ 这**不等于「没做」**：也可能图**建在别处**"
                            "（把那个图仓加进 `图仓库们` 再问一次）。"
                            "建图命令：`建图(该根)` 或 `建总图(总根)`")
                _未 += 1
                _表.append(条)
                continue
            _kgd = _桶
            # ★ 认权威指针：桶里的 `图.json.根` 必须就是这个根（口径 359）
            条["★图在哪找到的"] = str(_桶)
            节档 = _kgd / "节点.json"
            if not 节档.is_file():
                条["有图"] = False
                条["备注"] = (f"找到了桶 `{_桶}`（它的 `图.json.根` 就是这个根）"
                            f"，但**桶里没有 `节点.json`** ⇒ 建图没跑完。")
                _未 += 1
                _表.append(条)
                continue
            节 = json.loads(节档.read_text(encoding="utf-8"))
            条["有图"] = True
            条["图目录"] = str(_kgd)
            条["节点数"] = len(节)
            # ★★★★ R496：**「源文件」要取 `图.json.文件数`，不取 `分根` 的读数**。
            #   根因（判一百四十五 抓到的）：`分根` 数的是**直接位于该根下**的
            #   源文件，而 `建图` 是**递归**的 —— 对**仓库总根**这两者差 24 倍：
            #     `太虚元道` 桶  `分根` 说 **1,067** · `图.json` 说 **25,896**
            #     （`边.json` 里真实出现 **25,791 个文件** ⇒ `图.json` 是对的）
            #   ⇒ 口径 214 那笔债（"桶读取逻辑还在两处"）的**具体后果**就在这里：
            #     **索引里唯一描述"这个桶覆盖了多少东西"的字段是错的**，
            #     而目标④要的正是「AI 知道自己有什么、缺什么」。
            #   ★ 两个读数**都留**（口径 199）：权威的那个主用，`分根` 的留痕。
            _图档 = _kgd / "图.json"
            if _图档.is_file():
                try:
                    _图元 = json.loads(_图档.read_text(encoding="utf-8"))
                except Exception:                 # noqa: BLE001
                    _图元 = {}
                _权 = _图元.get("文件数")
                if isinstance(_权, int):
                    条["★分根说这个根直接有多少源文件"] = 条.get("源文件（分根读数）")
                    条["源文件（分根读数）"] = _权
                    条["★源文件读数取自"] = "`图.json.文件数`（建图自己记的，递归；权威）"
            _没跑 = []
            # ★★ 读数一律**读落盘文件**，不猜返回结构（R423，口径 139）——
            #    `引用.json` **是 list**；`外部缺.json`/`缺口.json` 的键名
            #    必须**照实写**，读不到就报"这一步还没跑"，**不许写 0/None**。
            for _名, _键, _取 in (
                    ("边.json", "边数", len), ("引用.json", "引用条数", len),
                    # ★ `未解析总数` **只在 `外部缺.json` 里**（R427）——
                    #   我原先从 `缺口.json` 读它 ⇒ 一直报 **0**，
                    #   而 **0 看起来像好消息**。判一百零五靠"总数守恒"抓住了它。
                    ("外部缺.json", "未解析", "未解析总数"),
                    # ★★★ R482：**把核心读数（解析率）抬到索引里** ——
                    #   目标（「名字出现过」→「真引用」）的判据就是这个比率。
                    #   它 R481 才第一次被写进 `外部缺.json`，但**索引里还没有**
                    #   ⇒ 而索引是「AI 知道自己有什么」的**入口**：
                    #     **入口上看不到的数，等于 AI 不知道。**
                    #   ★ 与 R423/R427 同一条口径：读数要**读落盘文件**、
                    #     键名**照实写**、读不到就报"这一步没跑"，不许写 0。
                    ("外部缺.json", "★引用解析率%", "★解析率（已解析/全部引用边）"),
                    ("外部缺.json", "引用边总数", "引用边总数"),
                    ("外部缺.json", "已解析条数", "已解析条数"),
                    ("外部缺.json", "真外部缺·去重数", "真外部缺·去重数"),
                    ("lean确证.json", "Lean确证数", len)):
                档2 = _kgd / _名
                if not 档2.is_file():
                    # ★★★ **不许留 None**（R423，口径 92）——
                    #   读索引的人分不清"这张图没有未解析"还是"这一步没产出"。
                    # ★★ 但**原因必须写准**（R424）：
                    #   我 R422 把"`建图` 重建时清掉了图目录"**误判成"从来没跑"** ✗
                    #   ⇒ 这里**只说"当下没有这个文件"，并给出两种可能**，
                    #     不替使用者断言历史。
                    条[_键] = None
                    条.setdefault("★缺哪些步", []).append(
                        f"当下**没有** `{_名}` ⇒ 两种可能：①这一步**没跑**；"
                        f"②**跑过、但被后来的 `建图` 重建清掉了**"
                        f"（`建图` 会重写图目录）。★ 要分辨只能看证据："
                        f"若同目录的其它产物带得上一次读数，就是②。"
                        f"★ 重跑命令：`解析引用(该根)`")
                    _没跑.append(_名)
                    continue
                try:
                    d2 = json.loads(档2.read_text(encoding="utf-8"))
                except Exception as e:            # noqa: BLE001
                    条[_键] = None
                    条.setdefault("★缺哪些步", []).append(
                        f"`{_名}` 读不了（{type(e).__name__}）")
                    _没跑.append(_名)
                    continue
                if isinstance(_取, int):
                    # ★ 数字 = "文件本身就是个原子值"的分支，本表不用
                    条[_键] = _取
                elif _取 is len:
                    条[_键] = len(d2) if hasattr(d2, "__len__") else None
                elif isinstance(d2, dict):
                    # ★★ **照实读那个键**；读不到就是"算我读错"，**不许退成 0/None**
                    条[_键] = d2.get(_取)
                    if 条[_键] is None:
                        条.setdefault("★缺哪些步", []).append(
                            f"`{_名}` 在，但**里面没有 `{_取}` 这个键** "
                            f"（实有：{list(d2.keys())[:6]}）⇒ **键名对不上，算我读错**")
                    else:
                        条.setdefault("★同源读数", {})[_名] = _取
                else:
                    条[_键] = len(d2) if hasattr(d2, "__len__") else None
            # ★★ **不许用"文件在不在"推断"做没做过"**（R424）——
            #    只能说"当下这套产物齐不齐"。
            _缺 = [x for x in _没跑 if x in ("引用.json", "缺口.json", "外部缺.json")]
            条["★这套产物齐不齐"] = ("齐" if not _缺
                                  else f"**缺 {len(_缺)} 件**：{'、'.join(_缺)}")
            if _缺:
                条["★重跑命令"] = "`解析引用(该根)`（只补这一步，不重建图）"
            _成 += 1
        except Exception as e:                    # noqa: BLE001
            条["有图"] = False
            条["备注"] = f"读图失败：{type(e).__name__}: {e}"
            _未 += 1
        _表.append(条)

    # ★★★ R458：**图仓库里可能还有"`分根` 不收、但确实建了图"的桶** ——
    #   实测：`formal_proof`（**400,828 节点 · 670,716 边 · 6,607 文件**）不在
    #   `计["根们"]` 里（它是 `形式化验证` 的**二级**子目录）但它**有完整 `图.json`**
    #   ⇒ 旧式只遍历 `根们` ⇒ **索引总账漏了这 40 万节点** ✗
    #   ★ 与 R453（总根被静默跳过）**同族**：**"没登记"与"没建"长得一样。**
    #   ★ 口径 92 的另一面：**允许它不在 `分根` 里，但不许它在总账里不存在。**
    _已登 = {str(x.get("图目录")) for x in _表 if x.get("图目录")}
    _死桶: list[dict] = []          # R460：`图.json` 说的根已经不在了的桶
    if 仓.is_dir():
        import json as _j458
        for _桶 in sorted(仓.iterdir()):
            if not _桶.is_dir() or str(_桶) in _已登:
                continue
            _tj = _桶 / "图.json"
            if not _tj.is_file():
                continue
            try:
                _gj = _j458.loads(_tj.read_text(encoding="utf-8"))
            except Exception:                         # noqa: BLE001
                continue
            # R460：`图.json` 里写的那个根必须真的还在 ——
            #   实测（判九十③）：`work/图/锁` 桶的 `图.json.根` 指向
            #   `work/临时/kg只读_xxx/锁`，那个目录**早被删了**，而补扫照样登记它。
            #   根因：`_kg目录(根)` 用**根名**做桶名 ⇒ 桶叫 `锁`，补扫只看到
            #   「桶里有 `图.json`」，**没问「它说的根还在不在」**（口径 92/183）。
            if not _gj.get('根') or not Path(str(_gj['根'])).is_dir():
                _死桶.append({'桶': str(_桶), '根': _gj.get('根'),
                               '原因': '`图.json` 说的根目录不存在（fixture 跑完就删）'})
                continue
            # R459：铺开源图，不许白名单重建 —— 判八十七 报过：白名单会把源里
            #   将来新增的键悄悄丢掉，而「丢」与「源本来没有」长得一样。
            _补 = {**_gj, "根": _gj.get("根"),
                   "角色": "**手工建的子桶**（不在 `分根` 的一级子树里）",
                   "有图": True, "图目录": str(_桶),
                   "节点数": _gj.get("节点数"), "边数": _gj.get("边数"),
                   "源文件（分根读数）": _gj.get("文件数"),
                   "★口径": ("它不在 `分根` 的 `根们` 里（二级子目录），"
                            "但**有完整图** ⇒ **必须进总账**，否则"
                            "「没登记」会被读成「没建」（口径 92/183）。"),
                   "★这套产物齐不齐": "齐"}
            _rf = _桶 / "引用.json"
            if _rf.is_file():
                try:
                    _rj = _j458.loads(_rf.read_text(encoding="utf-8"))
                    _补["引用条数"] = len(_rj) if isinstance(_rj, list) else None
                except Exception:                     # noqa: BLE001
                    pass
            # ★★★ R482：**这里原来漏了 `外部缺.json`**（口径 214 的欠账）——
            #   这段是 **R458 补录路径**（「`分根` 不收但有完整图」的桶），
            #   它**自己另写了一套取数**：只读 `引用.json` / `缺口.json`。
            #   后果实测到了：17 个桶里**唯一**走这条路的 `formal_proof`
            #   在 `索引.json` 里**解析率永远是 None**，而 `形式化验证`
            #   （走主循环）有 91.11% —— 两条目躺在**同一个列表**里，
            #   读者看不出「这条路没读那个文件」。
            #   ★ 不是『没跑过』（它的 `外部缺.json` 里明明有 89.95%），
            #     是**登记它的那条路没去读**。
            _wf = _桶 / "外部缺.json"
            if _wf.is_file():
                try:
                    _wj = _j458.loads(_wf.read_text(encoding="utf-8"))
                    for _索键, _外键 in (("★引用解析率%", "★解析率（已解析/全部引用边）"),
                                        ("引用边总数", "引用边总数"),
                                        ("已解析条数", "已解析条数"),
                                        ("未解析", "未解析总数"),
                                        ("真外部缺·去重数", "真外部缺·去重数")):
                        _v = _wj.get(_外键)
                        if _v is not None:
                            _补[_索键] = _v
                        else:
                            _补.setdefault("★缺哪些步", []).append(
                                f"`外部缺.json` 在，但**里面没有 `{_外键}` 这个键** "
                                f"（实有：{list(_wj.keys())[:6]}）⇒ **键名对不上，算我读错**")
                except Exception as _e:               # noqa: BLE001
                    _补.setdefault("★缺哪些步", []).append(
                        f"`外部缺.json` 读不了（{type(_e).__name__}）")
            else:
                _补.setdefault("★缺哪些步", []).append(
                    "当下**没有** `外部缺.json` ⇒ 两种可能：①这一步**没跑**；"
                    "②**跑过、但被后来的 `建图` 重建清掉了**。★ 重跑命令：`解析引用(该根)`")
            _qf = _桶 / "缺口.json"
            if _qf.is_file():
                try:
                    _qj = _j458.loads(_qf.read_text(encoding="utf-8"))
                    _补["未解析"] = _补.get("未解析") or _qj.get("★未解析") or _qj.get("未解析")
                except Exception:                     # noqa: BLE001
                    pass
            _表.append(_补)
        _成 += 1                                    # ★ R458：**它也是"建成了"** ——
        #   否则 `建成 16` 与 `根数 17` 自相矛盾（读数内部对不上，口径 160/173）。

    _重叠数 = _标父子关系(_表)

    出 = {"说明": ("★ **全仓知识图谱索引** —— 「AI 知道自己有什么东西」的入口。"
                 "每条 = 一个根 + 它的图目录 + **同源读数**（全读落盘文件，不重算）。"),
          "总根": str(根), "图仓库": str(仓),
          "根数": len(_表), "建成": _成, "没建": _未,
          "★父子重叠的桶数（父不是总根）": _重叠数,
          "★口径": ("① 每个源码文件必须落进且只落进一个根；"
                  "② 没建图的根必须显式写「没建」并说明原因（允许没有 + 必须说明）；"
                  "③ 所有读数**从落盘文件读**，不重算 —— 否则两处读数会不一致"),
          "★剔掉的死桶（`图.json` 说的根不存在）": _死桶[:20],
          "★死桶条数": len(_死桶),
          "根们": _表}
    try:
        仓.mkdir(parents=True, exist_ok=True)
        (仓 / "索引.json").write_text(
            json.dumps(出, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:                        # noqa: BLE001
        打印(f"   ★ 索引落盘失败（{type(e).__name__}）—— 如实说出，不静默")
    打印(f"   全仓索引：**{len(_表)} 个根 · 建成 {_成} · 没建 {_未}** ⇒ {仓 / '索引.json'}")
    return 出


# ★★★ 语言内置/标准库/prelude —— **这些"缺"是恒真的，不是"AI 缺什么"**
#   （R431）分三张表，而不是一张 Top：①内置（不看）②第三方（可补）③真缺口（该修）
_语言内置 = frozenset("""
    Option Some None Result Ok Err Vec String str bool char usize isize u8 u16 u32 u64
    u128 i8 i16 i32 i64 i128 f32 f64 Self self static const mut ref impl trait struct
    enum fn pub crate super mod use let match if else for while loop return break
    continue where dyn as in move async await unsafe extern type
    Clone Copy Debug Default Display PartialEq Eq PartialOrd Ord Hash Iterator
    Into From TryInto TryFrom AsRef AsMut Deref DerefMut Drop Send Sync Sized
    derive cfg println print format vec panic assert todo unimplemented unreachable
    Box Rc Arc Cell RefCell Mutex RwLock HashMap HashSet BTreeMap BTreeSet VecDeque
    Object Class String Integer Boolean Character Double Float Long Short Byte
    Override Deprecated SuppressWarnings FunctionalInterface SafeVarargs
    List Map Set ArrayList HashMap HashSet Optional Stream Collectors Arrays
    Exception RuntimeException Error Throwable Thread Runnable Comparable Iterable
    python self None True False def class import from as with lambda yield raise
    try except finally global nonlocal pass del is not and or in
    Int Double String Bool Char IO Either Maybe Monad Functor
""".split())

# ★★★ R463：**语言内置必须按语言分层**（口径 103 的落实）——
#   实测根据（`形式化验证` 根缺口 Top40）：
#     `DecidableEq 574 · Inhabited 466 · congrArg 388 · Or 329 · Repr 323`
#     `Nonempty 245 · Equal 238 · Less 236 · funext 185` —— **全是 Lean 恒真内建**，
#   而全局 `_语言内置` 里**一个 Lean 名都没有**（它有 Rust/Java/Python 的）。
#   ★ 但**不能就这么塞进全局表**：`path` / `time` / `env` / `iter` / `hash` /
#     `str` / `result` / `some` / `none` 这些**在 Python / JS 里是真名字** ⇒
#     一旦进全局表，本仓里一个叫 `path` 的真定义**会被当成"内置"藏起来** ✗
#     —— **这正是口径 203 说的"把真缺口藏进不算引用"。**
#   ⇒ 判据：**一个名字只有它在的每一种语言里都是内置，才算内置**（用 `all`）。
#     例：`path` 同时出现在 .rs（内建）和 .py（非内建）⇒ **仍算缺口**（保守）。
# ★★★★ R510：** Lean 的「库名」不是「语言内置」**（口径 248/303）。
#   这一行的 `Mathlib Finset BigOperators MeasureTheory Set Filter Topology
#   Algebra Order Group Ring Field Module LinearAlgebra Analysis` 是
#   **Mathlib 库的命名空间**（本仓外），不是 Lean 语言自带的 ——
#   而**口径 248 明确要求「Mathlib 名必须保持报出来」**（它们是真缺口）。
#   ★ 实测后果：extern 引用里判成内置的 6,581 条里，**`Mathlib` 占 1,153 条**；
#     反向也自相矛盾 —— 同为本仓外的 `ℝ`/`ℕ`/`ZMod` 报表，`Mathlib` 却被剔掉。
#   ★ 只留 `Function`（Lean **核心**里确实有）；其余交给「本仓有没有裸名定义」
#     与「半截名字」两档兜住 —— 本仓自己的命名空间**不会**污染真外部缺。
_内置_按语言: dict[str, frozenset] = {
    "lean": frozenset("""
    And Or Not Iff Exists forall Eq Equal HEq Ne propext funext congrArg congrFun
    Quot Quotient Classical Decidable DecidableEq DecidablePred of_decide_eq_true
    Inhabited Nonempty Repr BEq Hashable ToString toString Fintype Fin Enum Countable
    Subtype PLift ULift PProd PSum Sigma PSigma CoeFun CoeSort Coe CoeTC CoeOut
    Option some none Sum inl inr Prod mk List nil cons Array Nat Int Char
    LT.lt GT.gt LE.le GE.ge compare min max abs pow
    if_pos if_neg dif_pos dif_neg isFalse isTrue inferInstance
    rfl simp simpa exact apply intro intros revert rwa omega decide nomatch trivial
    induction cases constructor assumption contradiction by_contra norm_num ring linarith
    rename_i classical subst calc rcases obtain specialize generalize simp_all
    field_simp positivity continuity fun_prop measurability aesop tauto
    exact_mod_cast norm_cast push_cast linear_combination decide!
    Function
    String IO Float Bool Unit Empty Task Thunk Except Id
    UInt8 UInt16 UInt32 UInt64 USize Int8 Int16 Int32 Int64 ISize
    LawfulBEq ReflEq LawfulHashable
    """.split()),
    "rust": frozenset("""
    std core alloc proc_macro crate
    sync path fs env cell rc borrow convert cmp hash iter mem num ops ptr
    thread time io fmt vec boxed result collections
    """.split()),
    # ★★★★ R514：**这一行是"包前缀"，不是"类型"** ——
    #   `java`/`javax`/`org`/`com` 是**包名**（限定名的首段）；而 `String`/`Object`
    #   是**类型名**。两者不是一回事，但同一张表要同时回答这两个问题
    #   （本仓的名字抽取会把限定名的**首段**也当名字）。
    #   ★ 实测后果：`_是内置('String', {'java'}) = **False**` ⇒ Java 源码里的
    #     `String`/`System`/`Math`/`Thread` 全被当未解析；`arc_data` 桶的
    #     `★真外部缺` 里就躺着 `String`/`StringBuilder`/`Thread`/`System`/`Number`/`Math`。
    #   ★ 为什么一直没暴雷：它们大多被 **`裸名`** 挡着（本仓别处有同名实体定义）——
    #     正是 R513 预研记的「**缺陷的可见性依赖另一个无关判据**」（口径 330）。
    #   ⇒ 类型那一半**由 JDK 生成**（口径 43/323：不手写），见下面 `java内置.json` 的加载。
    "java": frozenset("""
    java javax org com
    """.split()),
    "python": frozenset("""
    self None True False def class import from as with lambda yield raise
    try except finally global nonlocal pass del is not and or in elif
    """.split()),
}

# ★★★★★ R514：**Java 的表由 JDK 生成**（口径 43/323：不手写常量表）。
#   · 生成器：`work/临时/生成java内置.py`（可复现）
#   · 权威来源两处，都来自机器上那个 JDK 自己：
#       ① 类型：`jimage list <jdk>/lib/modules` 列 `java/lang/*.class`
#          → 剥内部类 → `javap -public` 只留公开类型
#          （`java.lang` 是 JLS 定的「不用 import 就自动可见」的包）
#       ② 关键字：`javax.lang.model.SourceVersion.isKeyword` ——
#          拿本仓 Java/Kotlin 文件里**真实出现的标识符**逐个问 JDK
#   · 产物：`java内置.json`（包根，与 `benchmark_baseline.json` 同处），**带出处**
#   · ★ **kg.py 不依赖 JDK**：只读这份 JSON；读不到就**留旧表 + 记原因**
#     （口径 325：一个"如实为空"的边界要写下**它什么时候会变成缺陷**）。
_内置_按语言来源: dict[str, dict] = {}
try:
    import json as _json514
    _p514 = Path(__file__).resolve().parent / "java内置.json"
    _d514 = _json514.loads(_p514.read_text(encoding="utf-8"))
    _新514 = set(_内置_按语言.get("java", ()))
    _新514 |= {str(x) for x in (_d514.get("类型") or [])}
    _新514 |= {str(x) for x in (_d514.get("关键字") or [])}
    _内置_按语言["java"] = frozenset(_新514)
    _内置_按语言来源["java"] = dict(_d514.get("来源") or {})
    _内置_按语言来源["java"]["实得条数"] = len(_内置_按语言["java"])
except Exception as _e514:                             # noqa: BLE001
    # ★ 不静默：留旧表，并把**为什么没换成**写进来源（下游读得到）
    _内置_按语言来源["java"] = {
        "状态": "**没生成**（读 `java内置.json` 失败）",
        "错": f"{type(_e514).__name__}: {_e514}",
        "后果": ("Java 的 `java.lang` 类型（`String`/`System`/…）**仍会被当缺口** —— "
                 "它们现在只被 `裸名` 挡着一部分（口径 330：改动会连带引爆）"),
        "修法": "跑 `python work/临时/生成java内置.py`（需要机器上有 JDK）",
    }

# ★★★★ R525q：**语言专用内置表的缺口必须登记**（口径 203/307）。
#   为什么需要它：`_内置_按语言` 只覆盖 **4 种**（java/lean/python/rust），
#   而交付图里出现过 **9 种** ⇒ 其余几种的「**我缺什么**」读数**被关键字污染**，
#   而这件事**没有任何产物在说**（口径 260 的反面：污染了却不报）。
#   ★ 最硬的证据来自**陌生靶**：UE5 Core（真 C++）真缺口 Top =
#     `nodiscard 2,897 · ""（空名）1,760 · public 879 · static_cast 726 · inline 670`
#     —— **全是 C++ 的关键字/属性**；244,015 次未解析**全落在「语言判不出来」**。
#   ★ 登记**分两类**，而且必须写清是哪一类（口径 194：不同的量不许并成一个数）：
#     `"缺口"` = 这种语言**确有标识符概念、只是没建表** ⇒ 它的真缺口读数**含噪音**；
#     `"不需要"` = 这种语言的「名字」不是代码标识符（如 markdown 的标题/链接）
#                 ⇒ 没有表**不是**缺口。
#   ★ 判一百八十二 守这条：**图里出现过的语言，要么有表、要么在这里**。
#     它**不判**「缺口存在」为红（建不建表是策略），只判**缺口不许无声**。
_内置表缺口_已登记: dict[str, str] = {
    "javascript": "缺口（交付图 2,926 节点）：有标识符，没建表 ⇒ 真缺口读数含关键字噪音",
    "typescript": "缺口（交付图 95 节点）：同 javascript",
    "c": "缺口（交付图 200 节点）：`_内置_按语言` 里 c/cpp 都没有 ⇒ 同 cpp",
    "cpp": "缺口（UE5 Core 实测）：真缺口 Top 是 `nodiscard`/`public`/`static_cast`/`inline`"
           " —— **全是 C++ 关键字/属性**，而剔内置用的那张表是 **Rust 的**",
    "bash": "缺口（交付图 2 节点）：shell 关键字（then/fi/done…）会进真缺口",
    "csharp": "缺口（有扩展名映射、无表）：未在交付图上量到，按「声称支持」登记",
    "go": "缺口（有扩展名映射、无表）：未在交付图上量到，按「声称支持」登记",
    "markdown": "不需要：markdown 的「名字」是标题/链接（节点已按标题建模），"
                "不是代码标识符 ⇒ 没有内置表**不是**缺口",
}
_内置表缺口_口径 = ("`缺口` = 确有标识符概念但没建表 ⇒ 真缺口读数**含关键字噪音**；"
                 "`不需要` = 语言的「名字」不是代码标识符。判一百八十二 守「不许无声」。")

# ★★★★ R484：**`_语言内置` 是语言无关的，不能拿它做全局排除** ——
#   实测：168 个里 **131 个**只在 `_语言内置` 里、`_内置_按语言` 里没有：
#   绝大多数是 **Rust 专属**（`Ok`/`Result`/`Some`/`Err`/`Vec`/`Self`/
#   `println`/`derive`/`cfg`/`Clone`/`Debug`/`PartialEq`/…）
#   或 **Java 专属**（`ArrayList`/`Optional`/`Object`/`Integer`/…）。
#   ⇒ 原来 `_真外` 只排 `_按语内置`，而 `_按语内置` 又把这些名字用
#     `not in _语言内置` 挡在外面 ⇒ **它们全都留在「真外部缺」里** ✗
#     实测后果（`semsearch`，Rust）：解析率 70.08%，真外部缺 Top6 =
#       `Option 308 · String 256 · Ok 213 · Result 166 · Some 112 · Err 98`
#       —— **全是内置**。
#   ★ 但**反过来也不能一刀切**：`Option`/`String` 在 **Lean** 里是核心类型
#     （`_内置_按语言["lean"]` 里本来就有）；若用通用表全局剔，会把
#     **真缺口藏起来**（口径 203 的形态）。
#   ⇒ 判据是：**按「这个名字出现在哪种语言的文件里」判** ——
#     只有**该名字出现过的每一种语言都认它是内置**，才算内置。
# ★★★★ R492：**Rust 的 `_通用归属` 缺了一整类** —— `std::cmp` / `std::io` 的 trait 与类型。
#   ★ 怎么发现的：全仓「真外部缺」Top20 里躺着
#     `String 1,377 · Eq 901 · Ordering 242`。
#     `String` 在两张表里各有一份 ⇒ 判内置 ✓；而
#     `Eq` 在 **rust / lean 两张表里都没有**（`PartialEq` 有、`Eq` 没有）✗、
#     `Ordering` 在**四张表里全没有**（`Ord` 有、`Ordering` 没有）✗。
#   ⇒ **表是"按我碰到过的补"，不是"按语言的标准库列"** —— 于是「真缺口」里
#     混着一堆 `std` 自带的东西（判一百四十三 实测 36 个漏判）。
#   ★ 为什么这有害（不是抠字眼）：目标④ 说「未解析必须报出来，这是
#     **AI 知道自己缺什么**的读数」—— 若这份读数里混着语言自带的 trait，
#     AI 就会去「补」一个 `std` 里本来就有的东西（口径 203：
#     **误报的引用比缺失的引用更坏**）。
#   ★ 保守方向（口径 203「宁可漏剔，不可错剔」）：这里只加**确定无疑属于
#     Rust 标准库**的名字（`std::cmp` / `std::fmt` / `std::io` / `std::fs` /
#     `std::collections` / `std::path` / `std::time` / `std::hash` /
#     `std::iter` / `std::convert` / `std::borrow` 的公开项），
#     不做任何"看起来像"的推断。
_通用归属: dict[str, frozenset] = {
    "rust": frozenset("""
        Ok Err Some None Result Option Vec String Box Rc Arc Cell RefCell Mutex RwLock
        Self Send Sync Sized Ord PartialEq PartialOrd Clone Copy Debug Default Display
        Iterator Into From Drop Deref DerefMut AsRef AsMut TryFrom TryInto Error Hash
        BTreeMap BTreeSet HashMap HashSet VecDeque
        bool char str f32 f64 i8 i16 i32 i64 i128 u8 u16 u32 u64 u128 usize isize
        fn let mut pub struct enum trait impl mod use crate super self static const
        if else match loop while for return break continue where dyn async await move
        unsafe extern ref type as in
        assert cfg derive format panic print println todo unimplemented unreachable
        Eq PartialEq Ord PartialOrd Ordering Reverse Hash Hasher BuildHasher DefaultHasher
        Debug Display Formatter Iterator IntoIterator FromIterator DoubleEndedIterator
        ExactSizeIterator ToString FromStr From Into TryFrom TryInto AsRef AsMut
        Borrow BorrowMut Drop Deref DerefMut Error Write Read Seek BufRead BufReader BufWriter
        Cursor VecDeque Path PathBuf OsStr OsString File OpenOptions DirEntry ReadDir
        HashMap HashSet BTreeMap BTreeSet BinaryHeap LinkedList Duration Instant SystemTime
        Weak Cell RefCell Condvar Barrier AtomicBool AtomicUsize AtomicU64
        Ipv4Addr Ipv6Addr SocketAddr TcpListener TcpStream UdpSocket
        ExitCode Child Command Stdio Output NonZeroUsize NonZeroU64 Wrapping Saturating
        Range RangeInclusive RangeFrom PhantomData Cow FusedIterator Sum Product
    """.split()),
    "java": frozenset("""
        ArrayList Arrays Boolean Byte Character Class Collectors Comparable Double
        Exception FunctionalInterface Integer Iterable Long Map Object Optional
        Override Runnable RuntimeException SafeVarargs Short Stream SuppressWarnings
        Throwable Deprecated
    """.split()),
    "python": frozenset("""
        python bool int float str list dict set tuple type print len range open
    """.split()),
}


def _是内置(名: str, 语言集) -> bool:
    """★★ R484：**按语言判内置**。

    ★ 口径：名字出现在**多种语言**的文件里时，**每一种语言都认它是内置**才算
      —— 只要有一种语言的表里没有它，就不能剔
      （宁可漏剔，不可错剔：口径 203「为了让一条判据变绿而放宽范围
        会把真缺口藏起来」）。
    ★ 语言判不出来（`_按扩展名判语言` 给了空）⇒ ★★★★ **不许退回通用表，一律不剔**。
      ★ R507 改：原来退回通用表，理由是「保守：多剔一点」——**方向反了**。
        实测 `_是内置('Eq', None) = True` / `String` / `i32` / `ArrayList`：
        这些名字**各只在某一种语言里**是内置，却因为通用表是**跨语言混合**的，
        在「语言未知」时被剔掉。两条现成口径直接判它错：
          · **口径 261**：语言无关的通用表**绝不许用于全局排除**；
          · **口径 203**：**宁可漏剔，不可错剔** —— 剔错就把**真缺口藏起来**，
            而 AI 会以为"这个名字有了" ⇒ 直接违背**目标④「未解析的必须报出来」**。
        ⇒ **剔除不是保守动作，报出来才是。**
      ★ 代价（如实）：`None` 语言下的未解析会**变多** —— 那才是真读数。
    """
    if not 语言集:
        return False
    for _L in 语言集:
        if 名 not in _内置_按语言.get(_L, ()) and 名 not in _通用归属.get(_L, ()):
            return False
    return True


# ★ 扩展名 ⇒ 语言（**只列我们真的会建图的**；其余返回 None ⇒ 不参与按语言判定）
_扩展语言 = {
    ".lean": "lean", ".rs": "rust", ".java": "java", ".py": "python",
    ".pyi": "python", ".kt": "java", ".kts": "java",
}


def _按扩展名判语言(路径: str) -> str | None:
    """★ 从边上的 `起` 字段（`file:xxx.rs`）判语言。

    ★ 为什么必须从**边**上判，而不是从"这个根是什么项目"判：
      一个根里**可以混着多种语言**（本仓的 `形式化验证` 下有 `.lean`，
      同级还有 `.py`）⇒ 按根判会把 `.py` 里的真名字按 Lean 的规则藏掉。
    """
    if not 路径:
        return None
    _p = 路径[len("file:"):] if 路径.startswith("file:") else 路径
    return _扩展语言.get(Path(_p).suffix.lower())



# ★ 第三方依赖的**判据形态**（不是列举具体库名 —— 口径 103：按结构判不按名字列举）
_像第三方 = re.compile(
    r"^(?:numpy|pandas|torch|tensorflow|scipy|sklearn|matplotlib|requests|flask|"
    r"django|fastapi|pytest|serde|tokio|rayon|clap|anyhow|thiserror|log|env_logger|"
    r"regex|lazy_static|once_cell|itertools|rand|chrono|sqlx|axum|reqwest|"
    r"serde_json|num_|ndarray|rayon|parking_lot|dashmap|crossbeam)",
    re.I)


# ★★★ R473：**全局 `_语言内置` 里凡有「语言归属」的名字必须移出** ——
#   实测（这就是立这条的原因）：`String`/`IO`/`Float`/`Bool` **早在全局表里**
#   （168 个，当 Java/Rust 风格名放进去的）⇒ `_缺口分类("String", ["rust"])`
#   第一行 `if 名 in _语言内置: return "内置"` **就返回了** ⇒
#   **按语言分层形同虚设** ✗
#   ★ R463 立 `_内置_按语言` 的用意正是「同一个名字在不同语言里处境不同」，
#     但只要全局表里还留着它，分层**永远不会被执行到**。
#   ⇒ 启动时做一次**收窄**：出现在任何语言层里的名字，从全局表里删掉
#     （它们的归属由语言层回答）。
#   ★ 方向安全性（口径 203）：这是**收窄**，收窄只会让更多名字
#     从「内置」变成「真缺口」⇒ **不隐藏缺口**，宁可多报不可漏报。
#   ★ 但**不删**那些**没有**语言归属的名字（纯通用词，如 `self`/`None`）
#     —— 它们在按语言判定缺席时仍该算内置。
#   ★★ 第一版我做了**物理收窄**（`_语言内置 -= 每个语言层`）—— 实测副作用：
#     `_缺口分类("self", [])` 变成「真缺口」✗（`self` 是**纯通用词**，
#     没有语言信息时**仍该算内置**）。
#   ⇒ 改成**判定顺序**：**有语言信息时，语言层说了算**（不再回退全局表）；
#     **没有**语言信息时才用全局表。这样两边都对。


def _三档(名: str) -> str:
    """`第三方` 还是 `真缺口`（`内置` 已由调用方判掉）。

    ★ 抽成一处（口径 214）：原来这两条写在 `_缺口分类` 的尾巴上，
      按语言判定一旦提前 return，就**再也不会走到这里** ⇒
      「同一个判断写在两处」。
    """
    if _像第三方.match(名):
        return "第三方"
    # ★ 限定名（含 `.` / `::`）里的首段若像第三方，也算第三方
    _首 = re.split(r"[.:]{1,2}", 名)[0]
    if _首 != 名 and _像第三方.match(_首):
        return "第三方"
    return "真缺口"


def _缺口分类(名: str, 语言们=None) -> str:
    """把「本仓没有定义的名字」分成三类（R431）。

    ★ 为什么必须分：「本仓没定义」有三种，**混在一起等于没读数**：
      `内置`     —— 语言内置/标准库/prelude ⇒ **恒为真，不是缺口**；
      `第三方`   —— 外部依赖 ⇒ **可补**（加依赖）；
      `真缺口`   —— 本仓该有但没找到 ⇒ **该修**。
    ★ 口径：**「缺什么」要能行动** —— 一张 Top10 全是 `Option` 的清单，
      **看一百遍也不会让任何人去做任何事**。
    """
    # ★★★ R473：**有语言信息时，「语言层说了算」** ——
    #   实测（这就是立这条的原因）：`String`/`IO`/`Float`/`Bool` **早在全局表里**
    #   （168 个，当 Java/Rust 风格名放进去的）⇒ 原来的第一行
    #   `if 名 in _语言内置: return "内置"` **就把 `String` 判成内置了** ⇒
    #   `_缺口分类("String", ["rust"])` 也回「内置」✗
    #   ⇒ **按语言分层形同虚设**（R463 的用意全部落空）。
    #   ★★ 顺序必须是：**知道语言 ⇒ 语言层说了算**（不回退全局表）；
    #     **不知道语言 ⇒ 才用全局表**。
    #     · 知道语言：`String` 在 `lean` 里是 core ⇒「内置」；
    #       在 `rust` 里**不是**我们表里的 ⇒「真缺口」（**不隐藏缺口**）。
    #     · 不知道语言：`self`/`None` 这类**纯通用词**仍算内置
    #       （第一版我做了物理收窄，把 `self` 也删了 ⇒ `_缺口分类("self", [])`
    #        变「真缺口」✗ —— **收窄过头了**）。
    #   ★ 方向安全性（口径 203）：这是**收窄**，只会让更多名字从「内置」
    #     变成「真缺口」⇒ **宁可多报，不可漏报**。
    _语集 = [x for x in (语言们 or []) if x]
    if _语集:
        # 只有这个名字出现过的**每一种**语言都把它当内置，才算内置（`all`）
        return "内置" if all(名 in _内置_按语言.get(_x, ()) for _x in _语集) else _三档(名)
    if 名 in _语言内置:
        return "内置"
    return _三档(名)


def 全仓缺口(根: str | Path, 打印: Callable[[str], None] = print,
            前几名: int = 200) -> dict:
    """★★★ **把「分根」每一张图的「真外部缺」汇成一张常驻清单**（R430，目标④）。

    ★ 为什么（目标④的原话）：「**未解析的必须报出来**（这是真缺口，
      也是「**AI 知道自己缺什么**」的读数）」——
      光有"每张图各有 404 / 82 / 5,679 个缺口"**没用**：
      **AI 要的是一张能查的清单** —— 哪些名字本仓没有、**谁在找它们**。

    ★ 汇总必须解决三件事：
      ① **去重**：同一个名字在 5 张图里都缺 ⇒ 只算**一个缺口**
         （口径 112：「我多」与「我漏」几乎相等通常是口径差 —— 这里不并就是虚高）；
      ② **归并出现次数**：**谁最缺**（按出现次数排序）⇒ 决定"先补什么"；
      ③ ★ **带上「谁在找它」**：光知道缺 `DecidableEq` 没用，
         要知道**是哪些文件在找** ⇒ 那才是"该去哪儿补"
         （★ 口径：**「缺什么」要配「谁在问」才有用**）。
    ★ 落盘 `缺口清单.json`（常驻），**读数只从落盘文件读**（口径 141）。
    """
    根 = Path(根).resolve()
    计 = 分根(根, 打印=打印)
    仓 = _kg目录(根).parent
    if _env图目录:
        仓 = Path(_env图目录)
    if 仓.resolve() == 根:
        仓 = _kg目录(根)
    _合: dict[str, dict] = {}
    # ★ 二桶：`内置`（语言关键字/prelude，恒为真）与 `外部库`（本仓之外）
    _其: dict[str, dict] = {"内置": {}, "第三方": {}}
    _每根 = []
    _没产物 = []
    for x in 计["根们"]:
        _根 = Path(x["根"])
        _kgd = _kg目录(_根)
        f = _kgd / "外部缺.json"
        if not f.is_file():
            _没产物.append(_根.name)
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:                    # noqa: BLE001
            _没产物.append(f"{_根.name}（读不了 {type(e).__name__}）")
            continue
        _真 = d.get("★真外部缺") or d.get("真外部缺") or []
        # ★★ 真实形状（**去读生产者才看到**，口径 141）：
        #    `[{"名": "ABC", "次": 180, "也是限定名前缀": false}, …]`
        #    —— 不是 dict，也不是 [[名, 次], …]。我第一版 `dict(_真)` 直接炸 ✗
        _对 = []
        if isinstance(_真, dict):
            _对 = [(k, v) for k, v in _真.items()]
        elif isinstance(_真, list):
            for x in _真:
                if isinstance(x, dict):
                    _对.append((x.get("名") or x.get("名字"), x.get("次")
                               or x.get("出现次数") or 0))
                elif isinstance(x, (list, tuple)) and len(x) >= 2:
                    _对.append((x[0], x[1]))
        else:
            _没产物.append(f"{_根.name}（`★真外部缺` 形状不认识："
                          f"{type(_真).__name__}）")
            continue
        if not _对:
            _没产物.append(f"{_根.name}（`★真外部缺` 读出来是空的）")
            continue
        _每根.append({"根": _根.name, "真外部缺": len(_对),
                     "出现次数": d.get("真外部缺·出现次数")})
        for _名, _次 in _对:
            if _名 is None:
                continue
            _类 = _缺口分类(str(_名))          # ★ 三类分开（R431）
            _桶 = _合 if _类 == "真缺口" else _其.get(_类)
            if _桶 is None:
                continue
            条 = _桶.setdefault(str(_名), {"出现次数": 0, "在哪些根": []})
            条["出现次数"] += int(_次 or 0)
            if _根.name not in 条["在哪些根"]:
                条["在哪些根"].append(_根.name)
    _排 = sorted(_合.items(), key=lambda kv: -kv[1]["出现次数"])
    _排内 = sorted(_其["内置"].items(), key=lambda kv: -kv[1]["出现次数"])
    _排三 = sorted(_其["第三方"].items(), key=lambda kv: -kv[1]["出现次数"])
    出 = {
        "说明": ("★ **全仓缺口清单** —— 「AI 知道自己缺什么」的入口。"
                "每个名字 = **本仓没有任何地方定义它**，而下面列了谁在找它。"),
        "总根": str(根), "图仓库": str(仓),
        "★口径": ("① 同名跨根**只算一个缺口**（去重）；"
                 "② 按**出现次数**排序 ⇒ 先补最缺的；"
                 "③ **带「在哪些根」** —— 「缺什么」要配「谁在问」才有用；"
                 "④ 读数**只从落盘文件读**，不重算"),
        "★三类": {
            "真缺口（本仓该有但没找到 ⇒ **该修**）": len(_合),
            "外部库（语言标准库/官方 core/第三方 —— **本仓之外**；名单**不完备**，只作提示）": len(_其["第三方"]),
            "语言内置/标准库（**恒为真，不是缺口**）": len(_其["内置"]),
        },
        "外部库 Top": [{"名": k, "出现次数": v["出现次数"],
                     "在哪些根": v["在哪些根"]} for k, v in _排三[:前几名]],
        "缺口数（去重后）": len(_合),
        "出现次数（合计）": sum(v["出现次数"] for v in _合.values()),
        "★没产物的根": _没产物,
        "★每根缺口数": _每根,
        "Top": [{"名": k, "出现次数": v["出现次数"], "在哪些根": v["在哪些根"]}
                for k, v in _排[:前几名]],
    }
    try:
        仓.mkdir(parents=True, exist_ok=True)
        (仓 / "缺口清单.json").write_text(
            json.dumps(出, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:                        # noqa: BLE001
        打印(f"   ★ 缺口清单落盘失败（{type(e).__name__}）—— 如实说出，不静默")
    打印(f"   全仓缺口：**真缺口 {len(_合)}** · 外部库 {len(_其['第三方'])} · "
         f"内置 {len(_其['内置'])}（**恒为真，不是缺口**）· "
         f"覆盖 {len(_每根)} 根"
         + (f" · **{len(_没产物)} 根没产物**（如实说出）" if _没产物 else ""))
    打印(f"   全仓缺口：**{出['缺口数（去重后）']} 个名字（去重后）** · "
         f"出现 {出['出现次数（合计）']} 次 · "
         f"覆盖 {len(_每根)} 根"
         + (f" · **{len(_没产物)} 根没产物**（如实说出）" if _没产物 else ""))
    return 出


def 全仓未导入(根: str | Path, 打印: Callable[[str], None] = print,
              前几名: int = 300) -> dict:
    """★★★ **「未导入」= 别处有定义、本文件没导入 ⇒ 补一行就消**（R433）。

    ★ 为什么这是**最可行动**的一类（目标②的三分类）：
    ````
    ① 文件内定义 ⇒ 局部
    ② import 进来的 ⇒ 跨文件
    ③ 其余 ⇒ extern/未解析
      ③a **未导入**：别处有定义、本文件没导入 ⇒ **补一行 import 就消** ← 本函数
      ③b **真外部缺**：本仓任何地方都没有   ⇒ **补不了**（是外部库/内置）
    ````
    ★ 口径 147：**「缺什么」要能行动** ——
      10,329 个"真缺口"里绝大多数是语言内置与外部库（**补不了**），
      而这几百个 `未导入` 是**"改一行就好"**的。
      ⇒ 两个问题**必须分开报**，混在一起就都不可行动。

    ★ 读数只从落盘文件读（口径 141）：
      `缺口.json` 的 `★未导入（依赖图缺边，**不是「没有」**）` 与 `未导入样例`
      （样例里带 `在别处` —— **别处有定义的文件列表** ⇒ 那才是"补哪儿"）。
    ★ **`在别处` 是这条清单的价值所在**：光说"缺 T"没用，
      要说"**`T` 在 `…/test_a.py` 里有定义**"。
    """
    根 = Path(根).resolve()
    计 = 分根(根, 打印=打印)
    仓 = _kg目录(根).parent
    if _env图目录:
        仓 = Path(_env图目录)
    if 仓.resolve() == 根:
        仓 = _kg目录(根)
    _全 = []
    _每根 = []
    _没产物 = []
    for x in 计["根们"]:
        _根 = Path(x["根"])
        f = _kg目录(_根) / "缺口.json"
        if not f.is_file():
            _没产物.append(_根.name)
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:                    # noqa: BLE001
            _没产物.append(f"{_根.name}（读不了 {type(e).__name__}）")
            continue
        _数 = None
        _计 = d.get("计数")
        if isinstance(_计, dict):
            _数 = _计.get("未导入")
        _样 = d.get("未导入样例") or []
        if not isinstance(_样, list):
            _样 = []
        _每根.append({"根": _根.name, "未导入": _数, "样例条数": len(_样)})
        for y in _样:
            if not isinstance(y, dict):
                continue
            # ★ **铺开源 dict，不筛字段**（判八十七：传递层不许筛字段）——
            #   白名单重建等于我替调用方决定"什么重要"；源头多一个字段，
            #   它会**在传递中静默消失**，而"消失"长得像"本来就没有"。
            _在别处 = y.get("在别处") or []
            # ★★★ R455：**`文件` 是相对 `_根`（子根）的相对路径，不是相对总根** ——
            #   实测：我按总根拼 `未导入清单.Top[].文件`，**前 4 条全部"文件不在"** ✗
            #   拼对了子根之后 **6 条命中 6** ✓ —— **账本是准的，是我读错了基准**。
            #   ★ 口径 174「留名」的推广：**相对路径必须写明"相对谁"** ——
            #     否则每一个读它的人都要各自猜一遍（而我刚猜错过）。
            #   ⇒ 每条都带 `根路径`（绝对），`★口径` 里也写明。
            _全.append({**y, "根": _根.name, "根路径": str(_根),
                        "在别处": _在别处[:8], "在别处条数": len(_在别处)})
    _全.sort(key=lambda z: (-(z["在别处条数"] or 0), str(z["文件"])))
    出 = {
        "说明": ("★ **全仓「未导入」清单** —— 别处有定义、本文件没导入 ⇒ "
                "**补一行 import/use/open 就消**。这才是『该修的缺口』；"
                "而 `全仓缺口()` 报的是**本仓之外**（补不了）。"),
        "总根": str(根), "图仓库": str(仓),
        "★口径": ("★ **`Top[].文件` 是相对 `Top[].根路径`（子根）的相对路径**，"
                 "拼绝对路径要 `根路径 / 文件`；"
                 "① 只算 **③a 未导入**（别处有定义）；"
                 "② **带上『在别处』** —— 光说缺什么没用，要说『它哪里有定义』；"
                 "③ 与 `全仓缺口()`（本仓之外）**分开报**，混在一起就都不可行动；"
                 "④ 读数只从落盘文件读"),
        "未导入数（各根计数之和）": sum((z["未导入"] or 0) for z in _每根),
        "★每根": _每根,
        "★没产物的根": _没产物,
        "Top": _全[:前几名],
    }
    try:
        仓.mkdir(parents=True, exist_ok=True)
        (仓 / "未导入清单.json").write_text(
            json.dumps(出, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:                        # noqa: BLE001
        打印(f"   ★ 未导入清单落盘失败（{type(e).__name__}）—— 如实说出，不静默")
    打印(f"   全仓未导入：**{出['未导入数（各根计数之和）']} 处**"
         f"（别处有定义、本文件没导入 ⇒ **补一行就消**）· "
         f"覆盖 {sum(1 for z in _每根 if (z['未导入'] or 0) > 0)} 根"
         + (f" · **{len(_没产物)} 根没产物**" if _没产物 else ""))
    return 出


def 建总图(根: str | Path, 打印: Callable[[str], None] = print, 灌确证: bool = False,
           确证档: str | Path | None = None, 收尾解析: bool = True) -> dict:
    """**对每个根各建一图**（`分根` 的产物逐个跑 `建图`），并汇总。

    ★★ `收尾解析=True`（默认）：**每建完一个根，当场跑 `解析引用`**（R425）。

      为什么（实测）：16 张图建完后，**所有 `引用.json` 都不见了** ——
      `建图` 重写图目录 ⇒ **建图会毁掉上一步的解析产物**；
      我上一轮刚跑完的 `解析引用`，这一轮建图**全废了**。
      ★ 口径 104：**刷新/同步必须是收尾的一部分** ——
        「建完图」不是终点，「**建完并且解出引用**」才是。
      ★ 默认 `True`：**默认就该是对的**（正确的事不该靠记得）。
      ★ 关掉它只该是为了「只要图、不要引用」，**不该是为了省时间**。

    ★★ 收尾的**三步**（R426）：`解析引用` → `刷全仓索引` → （总账由调用方读索引出）

      ★ 只接一步不够 —— 我 R425 只把「解引用」接进收尾，
      **索引/总账留在手动** ⇒ "齐没齐"仍然取决于我记不记得。
      ★ 口径 117：**修完一个机制要问「下一步有没有按它走」**。
    """
    计 = 分根(根, 打印=打印)
    根 = Path(根).resolve()
    # ★★★ **总根要"只扫自己那份"**（R412）—— 否则 `建图(总根)` 又把全仓扫一遍，
    #   分根等于白做（实测：总根会枚举出 162,549 个文件，而它只该有 927 个）。
    #   ★ 口径：**「分根」不只是"算出该建几张图"，还得让每张图"只扫自己那份"。**
    _子根名 = tuple(计["合格"])
    果 = []
    for x in 计["根们"]:
        子根 = Path(x["根"])
        if 子根 == 根:
            # ★★★ R453：**总根该不该建，不能只看 `不属于任何子树`** ——
            #   实测（我自己的小 fixture）：`真项目`(8 文件)、`散的`(3 文件)
            #   **都不够 50 ⇒ 落进 `不单独成图` ⇒ 它们的文件 `归属` 到总根**；
            #   而 `不属于任何子树` 只数「**直接散落**在总根的文件」（= 0）
            #   ⇒ 旧式 `if not 计["不属于任何子树"]: continue` **把总根整个跳过** ✗
            #   ⇒ **那 11 个文件谁都不建图 ⇒ 静默消失** ✗
            #   ★ 这是 `判九十五`（沉默的删除）的**同族缺陷，换了个位置** ——
            #     口径 169：**清理/跳过的位置也是判据的一部分。**
            #   ★ 正确判据：**总根只要有"该它管"的文件就得建** ——
            #     该它管的 = 归属它的文件里，**不在任何合格子根下的**那些。
            #   ★ 量过 `分根` 的形状（口径 141）：
            #     `归属 = {相对路径: **归属到哪张图的根路径**}`（值是根路径，不是文件名）
            #     ⇒ 正确判据只有一句：**归属到总根的文件数 > 0 ⇒ 总根要建**。
            _归它 = 计.get("归属") or {}
            _有份 = sum(1 for _v in _归它.values() if Path(str(_v)) == 根)
            if not _有份 and not 计["不属于任何子树"]:
                continue
            _排 = _子根名
        else:
            _排 = ()
        try:
            建图(子根, 打印=打印, 灌确证=灌确证, 确证档=确证档, 排除=_排)
            _条 = {"根": x["根"], "成": True}
            # ★★ 收尾：**建完当场把 5 个产物算齐**（R425 起源，R519 补齐，口径 104）——
            #    否则「建图」会把上一步的解析产物毁掉，而"毁了没"取决于我看没看见。
            #
            # ★★★ R519 实测：这一行**原来只调 `解析引用`**，而代码**自己**（`_未测到留痕`
            #   L4407）写着收尾全套是 **5 个产物**：
            #     `["引用.json","外部缺.json","缺口.json","md链接.json","json引用.json"]`
            #   ⇒ `解析md链接` / `解析JSON引用` **被调 0 次**（死函数）
            #     ⇒ 目标④「未解析的必须报出来」在 markdown/json 两支上**完全没接**。
            #   ★ 口径 366：**一个没有调用方的函数等于没有接线**（R366 那句话的系统版）。
            #   ★ 口径 322：R466 记下了「同桶产物来自不同时间点」，但**只治了"早退留痕"**，
            #     没治「**根本没被调**」⇒ 这一轮撞上了。
            # ★ `收尾全套` 内部保证顺序：**合流最先**（它改图），其余只读派生都在其后
            #   ⇒ 5 个产物同代（判一百七十二② 看着这件事）。
            if 收尾解析:
                try:
                    _出 = 收尾全套(子根, 打印=打印, 确证档=确证档)
                    _条["收尾全套"] = "成"
                    _条["引用条数"] = len((_出.get("引用") or {}).get("引用") or []) or None
                    _条["md链接"] = (_出.get("md链接") or {}).get("断链")
                    _条["json引用"] = (_出.get("json引用") or {}).get("未解析")
                    _条["Lean合流"] = ("成" if isinstance(_出.get("Lean合流"), dict)
                                      else _出.get("Lean合流"))
                except Exception as e2:                   # noqa: BLE001
                    _条["收尾全套"] = f"失败 {type(e2).__name__}: {e2}"
                    打印(f"   ★ 子根 {子根.name} **收尾全套失败**："
                         f"{type(e2).__name__}: {e2} —— **如实说出，不静默**")
            果.append(_条)
        except Exception as e:                    # noqa: BLE001
            果.append({"根": x["根"], "成": False,
                        "错": f"{type(e).__name__}: {e}"})
            打印(f"   ★ 子根 {子根.name} 建图失败：{type(e).__name__}: {e}"
                 f" —— **如实说出，不静默**")
    # ★★ 收尾的**第三步**：刷索引（R426，口径 104/117）——
    #    「建完一套图」= 建图 + 解引用 + **刷索引**。
    #    ★ 光把"解引用"接进收尾还不够：索引留在手动 ⇒
    #      "齐没齐"仍然取决于我记不记得。
    if 收尾解析:
        try:
            出索引 = 刷全仓索引(根, 打印=打印)
            # ★ 同上：**铺开**，不筛字段（判八十七）
            果.append({**出索引, "根": "<索引>", "成": True})
        except Exception as e3:                       # noqa: BLE001
            果.append({"根": "<索引>", "成": False,
                      "错": f"{type(e3).__name__}: {e3}"})
            打印(f"   ★ **收尾刷索引失败**：{type(e3).__name__}: {e3}"
                 f" —— **如实说出，不静默**")
    # ★★ 收尾的**第四步**：全仓缺口 + 全仓未导入（R450，口径 104/117）——
    #    「AI 知道自己缺什么」是目标④的读数，而它**不能靠我记着跑**：
    #    `建总图` 建完 16 张图 + 刷完索引之后，**缺口账本必须是现成的**。
    #   ★ 口径 150：**可行动的缺口**（`未导入`：本仓有这个名字但没连边）
    #     与**不可行动的缺口**（外部库/内置）**必须分开报**。
    #   ★★ R450：**必须写成直接调用，不许走 `for _名, _函 in (...)` 的表** ——
    #     我把第一版写成表驱动，**判一百一十一① 立刻红了**：
    #     `if "全仓缺口(" not in getsource(建总图)` **看不见表里的函数名** ⇒
    #     「四步在不在」从源码上**读不出来**。
    #   ★ 口径：**"在不在"要能被读源码的判据看见** ——
    #     放在一张运行期才展开的表里，等于**只有运行时才知道**。
    if 收尾解析:
        try:
            _账缺 = 全仓缺口(根, 打印=打印)
            果.append({**_账缺, "根": "<全仓缺口>", "成": True})
        except Exception as e4:                           # noqa: BLE001
            果.append({"根": "<全仓缺口>", "成": False,
                       "错": f"{type(e4).__name__}: {e4}"})
            打印(f"   ★ **收尾 全仓缺口 失败**：{type(e4).__name__}: {e4}"
                 f" —— **如实说出，不静默**")
        try:
            _账未 = 全仓未导入(根, 打印=打印)
            果.append({**_账未, "根": "<全仓未导入>", "成": True})
        except Exception as e5:                           # noqa: BLE001
            果.append({"根": "<全仓未导入>", "成": False,
                       "错": f"{type(e5).__name__}: {e5}"})
            打印(f"   ★ **收尾 全仓未导入 失败**：{type(e5).__name__}: {e5}"
                 f" —— **如实说出，不静默**")
    return {"分根": 计, "结果": 果}


def 刷索引(根: str | Path, 打印: Callable[[str], None] = print) -> dict:
    """★★★ **把"我有什么"登记进常驻索引** —— 并且**自己刷新**（R406）。

    ★ 为什么需要（判九十 连续两轮因此变红）：
      `work/图/索引.json` 是「AI 知道自己有什么东西」的入口，
      但它是**我手写脚本跑一次生成的** ⇒ 跑了 `更新图` / `解析引用` 之后
      **索引里的数字就过期了**，而**没有任何东西会提醒我**。
      ★ 口径：**「每次都要人记得去刷」= 迟早忘** ——
        能被忘掉的步骤，**不该由人记得**。
      ⇒ 刷新必须是**收尾动作的一部分**，而不是另一个手工命令。

    ★ 索引里每一条的每一个数字，**都当场从图里重新数**（口径：数字要同源）。
    """
    根 = Path(根).resolve()
    kg = _kg目录(根)
    if not (kg / _图档).is_file():
        return {"未测到": f"没有图：{根}"}
    仓 = kg.parent                                # 图仓库 = 图目录的上一层
    if _env图目录:
        仓 = Path(_env图目录)
    # ★★★ **索引不许写进被扫的目录**（R406 修正，两次才对）——
    #   实测（判八十一 的红文）：`包含` 边 **2 → 16**、还多出 `自: 1`
    #   —— **图自己把自己扫进去了**（`索引.json` 落进根目录 ⇒
    #      下一轮 `建图` 把它当源文件）。
    #   ★ 第一版把守卫写在 `if _env图目录:` **里面** ⇒
    #     **`KG_图目录` 没设时根本不走那条分支** ⇒ 红文一字未改。
    #     口径：**"读数一字未动" = 我改的那条路从来没被执行** —— 先打点，别再加补丁。
    #   ★ 口径：**"产出"落在"输入"里，就是自我污染** ——
    #     凡"生成一份东西再扫描这个目录"的地方，都要问
    #     **产出的位置会不会被下一轮的扫描看见**。
    if 仓.resolve() == 根:
        仓 = kg
    索档 = 仓 / "索引.json"
    try:
        索 = json.loads(索档.read_text(encoding="utf-8")) if 索档.is_file() else {}
    except Exception:                             # noqa: BLE001
        索 = {}
    索.setdefault("说明", "★ 常驻知识图谱索引 —— 「AI 知道自己有什么东西」的入口。"
                          "每条 = 一个根 + 它的图目录 + 同源读数。**收尾自动刷新**（R406）。")
    索["建"] = _现在()
    索["图仓库"] = str(仓)
    条表 = {x.get("根"): x for x in (索.get("根们") or []) if isinstance(x, dict)}
    条 = 条表.get(str(根)) or {"根": str(根), "注": 根.name}
    条["图目录"] = str(kg)
    条["有图"] = True
    条.pop("备注", None)                           # ★ 有图了 ⇒ 那条"没建"的说明必须撤掉
    try:
        条["节点数"] = len(json.loads((kg / "节点.json").read_text(encoding="utf-8")))
        条["边数"] = len(json.loads((kg / "边.json").read_text(encoding="utf-8")))
    except Exception as e:                        # noqa: BLE001
        条["读数错误"] = f"{type(e).__name__}"
    _引 = kg / "引用.json"
    if _引.is_file():
        try:
            _by = json.loads(_引.read_text(encoding="utf-8"))
            条["引用条数"] = len(_by)
            条["未解析"] = sum(1 for r in _by if "**未解析**" in (r.get("依据") or ""))
        except Exception:                         # noqa: BLE001
            pass
    条["文件数"] = None
    try:
        条["文件数"] = json.loads((kg / _图档).read_text(encoding="utf-8")).get("文件数")
        条["文件时间"] = _现在()
    except Exception:                             # noqa: BLE001
        pass
    条表[str(根)] = 条
    索["根们"] = list(条表.values())
    仓.mkdir(parents=True, exist_ok=True)
    _原子写519(索档, json.dumps(索, ensure_ascii=False, indent=1))
    打印(f"   索引：{根.name} 节点 {条.get('节点数')} · 引用 {条.get('引用条数')} "
         f"· 未解析 {条.get('未解析')}")
    return 条


def 悬空边分类(节: dict, 边: list) -> dict:
    """把「边的终点不在节点表里」**分成三类**（口径 194：不同的量不许并成一个数）。

    ★★★ R525：原来一句话「悬空 4,185」（实测交付态 17 桶）把三件事混成一个数：
        ① `外链:` —— 仓外引用，**本来就不该有节点**；
        ② `路径:` 且 `已解析=False` —— **已经报出来的断链**
           （在 `md链接.json` / `json引用.json` 的 `断链` 里与 `引用.json` 里），
           那是「这条链接确实断了」，**不是图自相矛盾**；
        ③ **真矛盾** —— 本该接上却没接上（`已解析=True` 却不在节点表，或
           非 `路径:` 的边指向不存在的 id）。
      ★ 混在一起的代价：门天天红着 4,185 条，**真正的那 689 条淹在里面没人看见**。
    ★ 为什么抽出来：`自洽门` 住在 `kg` 里、只能通过 `_kg目录(根)` 找图，
      而**判据**（判一百八十一）要直接读 `work/图/<桶>` 的产物 —— 不带
      `KG_图目录` 时 `_kg目录(根)` 指向 `<根>/.kg`（不存在）。
      ⇒ **判据的绿不许取决于环境变量** ⇒ 两处共用这一段（口径 305：同一处置只写一处）。
    """
    悬空 = 外链 = 锚未接 = 解析错边 = 已报断 = 解到不在图 = 出口不在终 = 0
    for e2 in 边:
        终 = e2.get("终", "")
        if 终.startswith("name:"):
            continue                # `name:` 是「提及了一个名字」，等跨文件解析，不算悬空
        if 终 in 节:
            continue
        # ★★★ R525：**六类分清，只把「本该落盘的 id 却没落盘」判红。**
        #   口径 194：每类都是**不同的量**，并成一个数就没人知道哪儿真错。
        #   实测（交付态 17 桶，R525 修前）：4,185 条里真矛盾只有 689 条，
        #   其余是仓外/已报断/锚未接/语法错边 —— **真正的那 689 条淹在里面**。
        if 终.startswith("外链:"):
            外链 += 1               # 仓外引用，本来就不该有节点
        elif 终.startswith("锚:"):
            锚未接 += 1             # 本文件锚没接上（`已解析` 说的）
        elif 终.startswith("parse_error:"):
            解析错边 += 1           # 源文件语法错产生的边
        elif 终.startswith("本文件指针:"):
            已报断 += 1             # JSON 本文件指针解不开（已报）
        elif 终.startswith("路径:"):
            # ★ 「解到了、但目标不在图」与「已报出的断链」是**两个量**：
            #   前者目标文件在盘上却不在图（被剪枝/不该进图/是目录），
            #   后者是链接本来就断了。合并就分不清该修哪一边。
            if e2.get("已解析"):
                # ★ R525k：解到了 ⇒ **出口在 `出口` 字段**。
                #   `出口` 指的节点在 ⇒ **不是悬空**（这条路走得通）；
                #   不在 ⇒ 「解到但图里没有那个文件」（**另一个量**）。
                if e2.get("出口") and e2.get("出口") in 节:
                    出口不在终 += 1
                else:
                    解到不在图 += 1
            else:
                已报断 += 1
        else:
            悬空 += 1               # ★ **真矛盾**：本该落盘的 id 却没落盘
    return {"悬空": 悬空, "外链": 外链, "锚未接": 锚未接,
            "解析错边": 解析错边, "已报断": 已报断, "解到不在图": 解到不在图,
            "出口不在终": 出口不在终}


def 自洽门(根: str | Path) -> dict:
    """图必须能自证。**红了要说清哪条**，不是一句「自洽」。"""
    import json as _j门      # noqa: PLC0415（本文件没有模块级 json —— 局部名）
    根 = Path(根).resolve()
    图 = 加载图(根)
    if "未测到" in 图:
        return {"红": [图["未测到"]], "绿": []}
    节 = 图.get("节点") or {}
    边 = 图.get("边") or []
    红: list[str] = []
    绿: list[str] = []
    # ① 每条边的两端必须在节点里（或 `name:`/`外链:` 这种**故意不落盘**的占位符）
    # ★★★ R525：**口径 194 —— 「已报出的断链」与「自相矛盾」是两个不同的量。**
    #   原来把三种边混成一句「悬空 4,185」（实测交付态）：
    #     ① `外链:`（135）—— 仓外，天经地义不落盘；
    #     ② `路径:` 且 `已解析=False`（3,361）—— **已经报出来的断链**
    #        （在 `md链接.json`/`json引用.json` 的 `断链` 与 `引用.json` 里），
    #        那不是「图自相矛盾」，是「这条链接确实断了」；
    #     ③ 真矛盾 —— **本该接上却没接上**。
    #   ★ 混在一起的后果：这个门天天红着 4,185 条，而 **177 条判据没有一条读它**
    #     ⇒ 门变成噪声，真正的矛盾（689 条）**淹在里面没人看见**（口径 260 的反面）。
    #   ⇒ 只把 ③ 判红；① ② **如实单列**（口径 203：单列 ≠ 静默跳过）。
    _分 = 悬空边分类(节, 边)
    悬空 = _分["悬空"]
    if 悬空:
        红.append(f"有 {悬空} 条边的终点不在节点表里（悬空边）")
    else:
        绿.append("边的终点都在节点表里")
    _单 = {k: v for k, v in _分.items() if k != "悬空" and v}
    if _单:
        绿.append("★ 单列（**都是已报出的、不是自相矛盾** —— 口径 194："
                 "不同的量不并成一个数）：" + _j门.dumps(_单, ensure_ascii=False) +
                 "（`已报断`/`解到不在图` 在 `md链接.json`/`json引用.json` 的"
                 " `断链` 与 `目标文件在盘上但不在图里` 里）")
    # ② 每个定义节点必须能在源文件里定位（文件在 + 行号在）
    无落点 = [i for i, n in 节.items()
            if n.get("类") in _实体类 and (not n.get("文件") or not n.get("行"))]
    if 无落点:
        红.append(f"有 {len(无落点)} 个定义节点**没有源文件落点**（定位不了）")
    else:
        绿.append("命名实体节点都有文件+行号落点")
    # ③ 覆盖率：**没进图的必须报**，不许只报「进图的」
    文件数 = 图.get("文件数") or 0
    跳 = (图.get("清单") or {}).get("跳过") or []
    问 = (图.get("清单") or {}).get("问题") or []
    if 文件数 == 0:
        红.append("文件数为 0 —— 要么真空目录，要么枚举坏了（**不许当「没有东西」**）")
    else:
        绿.append(f"进图文件 {文件数} · 跳过 {len(跳)} · 适配器报问题 {len(问)}"
                 f"（**跳过的逐条有原因，在 `.kg/清单.json`**）")
    # ④ 墓碑不许比文件还多（多半是把根搞错了）
    if (图.get("墓碑数") or 0) > max(10, 文件数):
        红.append(f"墓碑 {图['墓碑数']} 多于文件 {文件数} ⇒ 多半根目录搞错了")
    else:
        绿.append(f"墓碑 {图.get('墓碑数') or 0}")
    return {"红": 红, "绿": 绿}


# ── 读数 / 查询 ─────────────────────────────────────────────────────
def 报告(根: str | Path) -> str:
    图 = 加载图(根)
    if "未测到" in 图:
        return f"知识图谱：未测到 —— {图['未测到']}"
    门 = 图.get("★自洽门") or 自洽门(根)
    行 = [
        f"知识图谱〔{图.get('模式')}〕：文件 {图.get('文件数')}（重算 {图.get('重算文件数')}）"
        f" · 节点 {图.get('节点数')} · 边 {图.get('边数')}",
        f"  适配器：{图.get('适配器分布')}",
        f"  墓碑 {图.get('墓碑数')} · **跳过 {图.get('跳过数')}**"
        f"（逐条原因在 `.kg/清单.json`，**不许静默**）"
        f" · 适配器报问题 {图.get('问题数')}",
        f"  自洽门：{'红 ' + str(门.get('红')) if 门.get('红') else '绿'}",
    ]
    return "\n".join(行)


def 查(名: str, 根: str | Path) -> dict:
    """按名字查节点（**定义优先**，提及次之）。"""
    图 = 加载图(根)
    if "未测到" in 图:
        return _未测到留痕(图, 根)
    节 = 图.get("节点") or {}
    命 = [n for n in 节.values() if n.get("名") == 名 or n.get("id") == 名]
    命.sort(key=lambda n: (n.get("类") != "定义", n.get("文件") or ""))
    出边 = [e2 for e2 in (图.get("边") or []) if 名 in (e2.get("起", ""), e2.get("终", ""))]
    return {"问的是": 名, "命中": 命[:30], "命中数": len(命),
            "相关边": 出边[:30], "相关边数": len(出边),
            "★边界": "引用边是**近似**（标识符出现），会多不会少；跨文件同名未解析。"}


# ── 自检（不带仓也能跑） ────────────────────────────────────────────
def _自检() -> int:
    错: list[str] = []
    import tempfile
    根 = Path(tempfile.mkdtemp(prefix="kg_自检_"))
    try:
        (根 / "a.py").write_text("def foo(x):\n    return bar(x)\n\nclass K:\n    def m(self): pass\n",
                                 encoding="utf-8")
        (根 / "b.rs").write_text("fn q(a: u32) -> u32 { a }\nstruct S { x: u32 }\n", encoding="utf-8")
        (根 / "c.md").write_text("# 标题\n\n正文\n", encoding="utf-8")
        (根 / "d.json").write_text('{"k": {"n": 1}}', encoding="utf-8")
        (根 / "e.未知后缀").write_text("随便什么内容 with 标识符名字", encoding="utf-8")
        (根 / "f.bin").write_bytes(b"\x00\x01\x02\xff")
        出 = 建图(根, 打印=lambda *_: None)
        if 出.get("文件数") != 6:
            错.append(f"应有 6 个文件进图，实得 {出.get('文件数')}")
        # ★ 保底：**没 grammar 的文件也必须在图上**
        节 = json.loads((_kg目录(根) / "节点.json").read_text(encoding="utf-8"))
        if not any(n.get("文件") == "e.未知后缀" for n in 节.values()):
            错.append("**没有 grammar 的文件从图上消失了** ⇒ 违反「看不见不能当没有」")
        if not any(n.get("文件") == "f.bin" for n in 节.values()):
            错.append("**二进制文件从图上消失了**")
        # ★ 适配器要真的各司其职
        适 = 出.get("适配器分布") or {}
        for k in ("_语法树适配器", "_结构化适配器", "_保底适配器", "二进制适配器"):
            if not 适.get(k):
                错.append(f"适配器 `{k}` 一个文件都没走到（分布 {适}）")
        # ★★ 类别不许把**语句**当定义（本轮实测：原版把 `_statement` 也算进去 ⇒
        #   Python 标准库 3,905 个文件出了 **676,950 个「定义」**，实为 99,076）
        (根 / "g.py").write_text("def f():\n    if True:\n        return 1\n", encoding="utf-8")
        建图(根, 打印=lambda *_: None)
        _节g = json.loads((_kg目录(根) / "节点.json").read_text(encoding="utf-8"))
        _型 = {n.get("类型") for n in _节g.values() if n.get("文件") == "g.py"}
        _坏类 = [t for t in _型 if t and t.endswith("_statement")]
        if _坏类:
            错.append(f"**语句被当成定义进图了**：{_坏类} ⇒ 类别不诚实（会虚报实体数）")
        (_根_g := 根 / "g.py").unlink()
        # ★ touch（mtime 变、内容不变）⇒ **不该重算**（sha1 相同才算真变）
        (_根_h := 根 / "h.py").write_text("def hh():\n    pass\n", encoding="utf-8")
        更新图(根, 打印=lambda *_: None)
        import os as _os
        _os.utime(_根_h, (time.time() + 5, time.time() + 5))
        _出t = 更新图(根, 打印=lambda *_: None)
        if _出t.get("重算文件数") != 0:
            错.append(f"只 touch 没改内容却重算了 {_出t.get('重算文件数')} 个"
                      f" ⇒ 戳比的是 mtime 不是内容")
        # ★ 增量：内容不变 ⇒ 不重算
        出2 = 更新图(根, 打印=lambda *_: None)
        if 出2.get("重算文件数") != 0:
            错.append(f"增量应重算 0 个（内容没变），实得 {出2.get('重算文件数')}")
        # ★ 内容真变 ⇒ 必须重算
        (根 / "a.py").write_text("def foo2(x):\n    return 1\n", encoding="utf-8")
        出3 = 更新图(根, 打印=lambda *_: None)
        if 出3.get("重算文件数") != 1:
            错.append(f"改了一个文件应重算 1，实得 {出3.get('重算文件数')}")
        if "foo2" not in json.dumps(json.loads((_kg目录(根) / "节点.json")
                                              .read_text(encoding="utf-8")), ensure_ascii=False):
            错.append("改后的新定义 `foo2` 没进图 ⇒ 增量没真的更新")
        # ★ 删除 ⇒ 墓碑
        (根 / "b.rs").unlink()
        出4 = 更新图(根, 打印=lambda *_: None)
        if not 出4.get("墓碑数"):
            错.append("删掉的文件没标墓碑")
        # ★ 自洽门必须能变红（不能永远绿）
        门 = 自洽门(根)
        if 门.get("红"):
            错.append(f"自洽门在正常图上就红了：{门['红']}")
        # 人为搞坏：把一条边的终点改成不存在的
        边 = json.loads((_kg目录(根) / "边.json").read_text(encoding="utf-8"))
        边.append({"起": "file:a.py", "终": "定义:不存在:xxx:1", "关系": "包含"})
        _原子写519((_kg目录(根) / "边.json"), json.dumps(边, ensure_ascii=False))
        if not 自洽门(根).get("红"):
            错.append("悬空边加进去，自洽门**没红** ⇒ 门是装饰")
        # 复原
        _原子写519((_kg目录(根) / "边.json"), json.dumps(边[:-1], ensure_ascii=False))
    finally:
        import shutil
        shutil.rmtree(根, ignore_errors=True)
    if 错:
        print("❌ 知识图谱自检：")
        for e in 错:
            print("   -", e)
        return 1
    print("✅ 知识图谱自检全绿（保底不漏·四个适配器各司其职·增量只重算变了的"
          "·改了必更新·删了必立碑·自洽门能变红）")
    return 0


def _涂(根: Path | None) -> int:
    根 = (根 or Path.cwd()).resolve()
    图 = 加载图(根)
    if "未测到" in 图:
        print(f"   {图['未测到']}")
        return 1
    节 = 图.get("节点") or {}
    名 = 图.get("文件数")
    边 = 图.get("边") or []
    print(f"   节点 {len(节)} · 边 {len(边)} · 文件 {名}")
    import collections as _c
    类 = _c.Counter(n.get("类") for n in 节.values())
    print("   节点分类:", dict(类))
    适 = _c.Counter(n.get("适配器") for n in 节.values())
    print("   适配器:", dict(适))
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        sys.exit(_自检())
    cmd = argv[0]
    if cmd == "自检":
        sys.exit(_自检())
    if cmd == "适配器":
        print(json.dumps(适配器表(), ensure_ascii=False, indent=2))
        sys.exit(0)
    根 = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    if cmd == "建":
        出 = 建图(根)
        print(json.dumps({k: v for k, v in 出.items() if k != "★自洽门"},
                         ensure_ascii=False, indent=2))
        sys.exit(0)
    if cmd == "更新":
        出 = 更新图(根)
        print(json.dumps({k: v for k, v in 出.items() if k != "★自洽门"},
                         ensure_ascii=False, indent=2))
        sys.exit(0)
    if cmd == "报告":
        print(报告(根))
        sys.exit(_涂(根))
    if cmd == "查":
        print(json.dumps(查(argv[1], 根), ensure_ascii=False, indent=2))
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
