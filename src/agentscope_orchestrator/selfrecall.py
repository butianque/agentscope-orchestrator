"""编排器**自己的代码**检索（自述）—— 让「关于编排器本身的问题」也有证据可引。

## 为什么单独一个模块（而不是把工具代码塞进 `concept.py` 的概念索引）

`concept.py` 有一条**明文口径**：「工具代码不是项目知识」（它把 tool 的 `src/tests/tools`
排除在概念索引之外）。那条口径是真金白银换来的：工具 docstring 里的示范问句会把
**项目**召回压住（实测两次栽在这上面）。

但那条口径留下另一个洞（2026-11-05 用户当面指出）：
问「本仓判据门评估器里 `cmd` 是列表时走 shell 还是 argv」时，**项目检索给的全是无关文档** ——
文件段按名命中 `warnings_cmd.txt`、概念段给 Rust 文件，
**编排器答不出关于自己的问题**。这不是"项目知识"，这是"自查能力"。

⇒ 本模块：不破那条口径，工具代码走**独立索引 + 独立段**，只回答"关于编排器"的问题。

## 口径（四条，都是判据钉住的）

1. 文件清单来自**记忆索引**（`py` 类），**不自己遍历目录** —— 问答期枚举目录会被
   `tests/tool_self_recall.py` 当场数出来。
2. 只收 `工具根/{src,tests,tools}` 下的 `.py`。
3. 术语按**行**建倒排：只给文件名等于没证据，要能给出**行号 + 那一行原文**。
4. 建索引可以读文件、可以慢；**问答期只读缓存**（缓存不在就按指纹重建，仍不枚举目录）。

## 与项目检索的关系

互不污染：项目问句在工具语料里几乎没有命中（工具段为空即不显示）；
工具问句在项目语料里也没有有效命中（这正是本模块存在的理由）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

_缓存名 = "self_index.json"
# ★ 缓存要带**版本号**：指纹只认"文件清单/大小/时间"，改的是**建索引的逻辑**时指纹不变 ⇒
#   旧缓存被继续用（实测两次：剥引号规则改了、df 闸改了，都还在用旧索引，污染照旧）。
#   光靠人工 +1 不可靠（这轮忘了两次）⇒ 再带一个**实现指纹**：本模块源码一变就重建。
_索引版本 = 4
_实现指纹 = hashlib.sha256(
    Path(__file__).read_bytes()).hexdigest()[:12] if Path(__file__).is_file() else ""
_内存: dict[str, "自述索引"] = {}

# ★ 引号里的话**不算实现**（2026-11-05 实测三次污染都是这一条）：
#   我在注释/判据里写过「问『判据门评估器 cmd 是列表时走 shell 还是 argv』时…」，
#   于是**引用这句问句的那一行**把真正的实现（`judge.py`）挤下去，甚至让
#   拟声词负对照问句命中 `tests/concept_evidence.py`。
#   口径：**引述不是知识** —— 建索引前把引号内内容整段剥掉再切词。
_引号内 = re.compile(r"「[^」]{0,120}」|\"[^\"]{0,120}\"|`[^`]{0,120}`|“[^”]{0,120}”|'[^']{0,120}'")

# 代码里满仓都是、但不构成"关于它的知识"的词（ASCII 与常见中文骨架）
_代码停用 = frozenset({
    "self", "return", "import", "from", "class", "def", "None", "True", "False",
    "str", "int", "float", "bool", "list", "dict", "set", "tuple", "len", "range",
    "Path", "print", "raise", "except", "finally", "with", "for", "while", "if",
    "else", "elif", "try", "pass", "continue", "break", "yield", "async", "await",
    "json", "text", "utf", "args", "kwargs", "value", "values", "items", "keys",
    "我们", "这个", "那个", "可以", "如果", "就是", "不是", "没有", "已经",
    "然后", "因为", "所以", "但是", "而且", "这里", "一个", "这个",
})


def 工具根() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def 自述文件(mem=None) -> list[Path]:
    """工具自己的 py 文件清单（**从记忆索引取，不遍历目录**）。

    ★ 回执 md **不进这个索引**（2026-11-05 试过，退回）：回执里既有事实，也有
      「问句→回答」对照表和负对照记录（`拟声词「哞哞…」⇒ 回空`）。混进代码索引后，
      拟声词问句能命中 2 条回执、项目问句也被问句表拉高分 —— **两类证据必须分段**，
      不能靠"更聪明的闸"分辨。回执正文检索留给独立的「回执证据」段（见第 246 轮回执第 6 节）。
    """
    try:
        if mem is None:
            from .autoengine import 项目索引
            mem = 项目索引()
    except Exception:                                # noqa: BLE001
        return []
    根 = 工具根()
    允许 = []
    for d in ("src", "tests", "tools"):
        try:
            允许.append(str((根 / d).resolve()))
        except OSError:
            continue
    见: dict[str, Path] = {}
    # ★ 只排除**判据**（它的夹具里摆着期望答案字符串，进了索引就是自问自答）。
    #   检索器自己**要留在索引里**：第一版把 `selfrecall.py` 也排除，理由是它的
    #   docstring 引着示范问句 —— 现在引号内已经被剥掉了（见 `_引号内`），
    #   污染没了，但留下一个更蠢的洞：问「自述检索的窗口共现权重是多少」查不到，
    #   因为**唯一的答案就在被排除的那个文件里**（实测 2026-11-05，A/B 第 5 题）。
    _自身 = Path(__file__).resolve()
    _跳过名 = {"tool_self_recall.py"}
    try:
        类们 = list(mem.kinds())
    except Exception:                                # noqa: BLE001
        return []
    for kind in 类们:
        try:
            idx = mem.index(kind)
            键们 = list(idx.keys())
        except Exception:                            # noqa: BLE001
            continue
        for k in 键们:
            try:
                p = idx.query(k)
            except Exception:                        # noqa: BLE001
                continue
            if p is None or getattr(p, "suffix", "") != ".py":
                continue
            if p.name in _跳过名:
                continue
            try:
                s = str(p.resolve())
            except OSError:
                continue
            if any(s.startswith(x) for x in 允许):
                见.setdefault(s, p)
    return sorted(见.values(), key=lambda p: str(p))


def _指纹(文件们: list[Path]) -> str:
    件 = []
    for p in 文件们:
        try:
            st = p.stat()
            件.append(f"{p}:{st.st_size}:{int(st.st_mtime)}")
        except OSError:
            continue
    return hashlib.sha256("|".join(件).encode()).hexdigest()[:16]


class 自述索引:
    """术语 → {相对路径: [行号…]}；行级，因为证据必须能指到一行。"""

    def __init__(self, 倒排: dict[str, dict[str, list[int]]], 文件们: list[Path],
                 根: Path, 指纹: str) -> None:
        self._倒排 = 倒排
        self._文件们 = 文件们
        self._根 = 根
        self._指纹 = 指纹
        self._N = len(文件们)

    # ── 建 / 存 / 取 ───────────────────────────────────────────
    @classmethod
    def 建(cls, 文件们: list[Path], 根: Path | None = None) -> "自述索引":
        from .concept import _切术语
        根 = Path(根) if 根 is not None else 工具根()
        倒排: dict[str, dict[str, list[int]]] = {}
        for p in 文件们:
            try:
                s = str(p.resolve())
                rel = str(Path(s).relative_to(根)).replace("\\", "/")
                全 = p.read_text(encoding="utf-8", errors="ignore")
            except (OSError, RuntimeError, ValueError):
                continue
            # ★ 剥引号必须**跨行**做：实测那条污染注释是
            #   L508 `…口径「工具代码不是项目知识」，所以问「本仓判据门评估器里`
            #   L509 `cmd 是列表时走 shell 还是 argv」时…`
            #   开引号在 508、闭引号在 509，逐行剥根本剥不掉。
            净文 = _引号内.sub(lambda m: "\n" * m.group(0).count("\n") + " ", 全)
            for no, 行 in enumerate(净文.splitlines(), 1):
                行 = 行.strip()
                if not 行 or 行.startswith(("import ", "from ")) or 行.startswith("#!"):
                    continue
                for t in _切术语(行):
                    if t in _代码停用:
                        continue
                    倒排.setdefault(t, {}).setdefault(rel, []).append(no)
        # 只留"不超过 35% 文件"的术语（丢 self/Path 这类骨架词）。
        #   ★ 下限**不设 2**（2026-11-05 实测）：那条"至少 2 个文件都有"的闸是从
        #     `concept.py` 搬来的，它适合 4000 文件的**项目**语料；工具语料只有 185 个文件，
        #     而**辨识工具内部细节的词往往只有一处**（`窗口共现` 全仓只出现在
        #     selfrecall.py 一处）。设了下限 2 ⇒ 问「窗口共现权重是多少」直接查不到，
        #     而这正是"回答关于自己"该答的那类问题。
        _文件数 = len(文件们)
        上限 = max(2, int(_文件数 * 0.35))
        倒排 = {t: 图 for t, 图 in 倒排.items() if len(图) <= 上限}
        for t, 图 in 倒排.items():
            for rel, 号们 in 图.items():
                图[rel] = sorted(set(号们))[:40]       # 一个术语在一个文件里留 40 行为够
        return cls(倒排, 文件们, 根, _指纹(文件们))

    def 存(self, 缓存: Path) -> None:
        try:
            缓存.parent.mkdir(parents=True, exist_ok=True)
            缓存.write_text(json.dumps({
                "版本": _索引版本,
                "实现": _实现指纹,
                "指纹": self._指纹,
                "文件": [str(p) for p in self._文件们],
                "倒排": self._倒排,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def 取(cls, 缓存: Path, 文件们: list[Path]) -> "自述索引 | None":
        try:
            原 = json.loads(缓存.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if 原.get("版本") != _索引版本:
            return None
        if 原.get("实现") != _实现指纹:
            return None
        if 原.get("指纹") != _指纹(文件们):
            return None
        图 = 原.get("倒排")
        if not isinstance(图, dict):
            return None
        return cls(图, 文件们, 工具根(), str(原.get("指纹") or ""))

    # ── 检索 ───────────────────────────────────────────────────
    def 召回(self, 问句: str, 上限文件: int = 4, 上限行: int = 3) -> dict:
        """先按字面打一次；**字面一个词都撞不上**时才用模型抽的术语再打一次。

        ★ 为什么第二跳要**收窄**（2026-11-05 CI 实测）：
          第一版把"字面分弱"也算进第二跳 ⇒ 每个弱命中问句都多一次模型调用（10~20s），
          套件从 1062s 涨到 1679s，`g3_sqlite` 因等窗口超时被判红。
          实测那次第二跳**也没把分数提高**（7.66 → 7.66）：真问题是显示闸太严，
          不是缺模型语义。
          ⇒ 口径：**问答热路径上不许随手加模型调用**；语义那跳只在字面全落空时发生。
        """
        字 = self._切词(问句)
        r = self._搜(字, 上限文件, 上限行)
        if 字:
            return r
        try:
            from .concept import 模型抽词
            补 = [t for t in 模型抽词(问句) if t in self._倒排 and t not in 字]
        except Exception:                             # noqa: BLE001 增强不是前提
            补 = []
        # ★ 补进来的词也要过**对比词频闸**：实测模型会把拟声词抽成「声音」「拟声」
        #   这类泛词，它们在工具代码里确实出现过 ⇒ 不筛就会让无意义问句"命中 3 个文件"
        #   （判据 ⑤ 当场抓到）。闸只认"项目语料里不烂大街"的词。
        if 补:
            try:
                from .concept import 项目常见词
                常见 = 项目常见词(补)
                补 = [t for t in 补 if t not in 常见]
            except Exception:                         # noqa: BLE001
                pass
        if not 补:
            return r
        r2 = self._搜(字 + 补, 上限文件, 上限行)
        if r2["命中"]:
            r2["说明"] += "（字面无命中 ⇒ 已用模型抽词补搜一次）"
            return r2
        return r

    def _切词(self, 问句: str) -> list[str]:
        from .concept import _切术语
        原词 = {t for t in _切术语(问句) if t not in _代码停用}
        # ★ 丢**子串碎片**：`_切术语` 会同时吐 `判据门` 和 `据门`（n-gram），
        #   后者只是前者的一部分，留着只会让"到处都命中"的文件刷分。
        原词 = {t for t in 原词
                if not any(u != t and t in u and len(u) > len(t) for u in 原词)}
        词 = sorted(t for t in 原词 if t in self._倒排)
        # ★★ **对比词频闸**（2026-11-05 第三次污染逼出来的）：
        #   我在 `context_view.py` 注释里解释"项目问句实测命中…关系式族差…"，
        #   于是那个**项目专名**在工具代码里出现了 2 次，自查索引就当它是工具术语，
        #   项目问句一进来就显示「编排器自身」段（实测首条 14.95 分，压过真项目证据）。
        #   ⇒ 口径：**项目语料里烂大街的词，不是工具术语**，不管它在工具注释里出现几次。
        #   线画在 80 个文件（按实测：`判据门`239 · `否证`319 · `读数`1702 · `载体`1083
        #   是项目烂大街词；`cmd`66 · `shell`10 · `argv`6 · `isinstance`4 · `窗口共现`0
        #   是工具真词，全部保留）。第一次画在 40 时把 `cmd` 也挡掉了 ⇒ `judge.py`
        #   那条 `isinstance(cmd,(list,tuple))` 反而落到第二名之后（判据当场抓到）。
        #   （只在项目索引可得时生效；取不到索引就不筛，绝不因此报错。）
        try:
            from .concept import 项目常见词
            常见 = 项目常见词(词)
            词 = [t for t in 词 if t not in 常见]
        except Exception:                             # noqa: BLE001 筛不了就按原样查
            pass
        return 词

    def _搜(self, 词: list[str], 上限文件: int, 上限行: int) -> dict:
        if not 词:
            return {"命中": [], "术语": [], "说明": "没命中 —— 问句里没有出现在"
                                                    "**编排器自身代码**里的术语（换说法再问一次）"}
        idf = {t: math.log((1 + self._N) / (1 + len(self._倒排[t]))) + 1.0 for t in 词}
        分: dict[str, float] = {}
        证: dict[str, list[tuple[float, str, int]]] = {}
        for t in 词:
            for rel, 号们 in self._倒排[t].items():
                分[rel] = 分.get(rel, 0.0) + idf[t]
                证.setdefault(rel, [])
                if 号们:
                    证[rel].append((idf[t], t, 号们[0]))
        # ★★ **窗口共现**（2026-11-05 实测逼出来的）：只看"整文件命中几个词"，
        #   答案所在的 `gate_spec.py` 排到第 6 —— 因为 `generic_goal.py`（一万多行）
        #   随随便便就能凑齐六七个词。真正说明"这里讲的就是这件事"的信号是：
        #   **不同术语挤在连续几行里**。⇒ 分 = 术语分 + 最佳窗口共现词数 × 2.5，
        #   再按文件规模轻度归一（大文件不许靠体量取胜）。
        候选 = sorted(分, key=lambda r: -分[r])[:40]
        最佳窗: dict[str, tuple[int, int, list[tuple[int, str]]]] = {}
        for rel in 候选:
            行们 = self._读行(rel)
            点 = []
            for t in 词:
                for no in self._倒排[t].get(rel) or []:
                    点.append((no, t))
            点.sort()
            好 = (0, 0, [])
            for i, (no, _t) in enumerate(点):
                窗 = []
                for no2, t2 in 点[i:]:
                    if no2 - no > 4:
                        break
                    窗.append((no2, t2))
                distinct = len({t2 for _n, t2 in 窗})
                if distinct > 好[0]:
                    好 = (distinct, no, 窗)
            最佳窗[rel] = 好
            分[rel] = (分[rel] + 2.5 * 好[0]) / (1.0 + 0.15 * math.log10(1.0 + len(行们) / 300.0))
        排 = sorted(分, key=lambda r: -分[r])[:上限文件]
        命中 = []
        for rel in 排:
            行们 = self._读行(rel)
            证据 = []
            用于 = set()
            # 证据优先取**最佳窗口**里那几行（答案就在那儿），窗口不足再补别处
            for no, t in (最佳窗.get(rel) or (0, 0, []))[2]:
                if len(证据) >= 上限行:
                    break
                if any(e["行"] == no for e in 证据):
                    continue
                文 = 行们[no - 1].strip() if 0 < no <= len(行们) else ""
                if not 文:
                    continue
                证据.append({"行": no, "术语": t, "摘录": 文[:150]})
            for w, t, no in sorted(证.get(rel) or [], key=lambda x: -x[0]):
                if len(证据) >= 上限行:
                    break
                if any(e["行"] == no for e in 证据):
                    continue
                文 = 行们[no - 1].strip() if 0 < no <= len(行们) else ""
                if not 文:
                    continue
                证据.append({"行": no, "术语": t, "摘录": 文[:150]})
            命中.append({"文件": rel, "分": round(分[rel], 3), "证据": 证据})
        return {"命中": 命中, "术语": sorted(词)[:12],
                "说明": f"术语命中 {len(词)} 个；文件 {len(命中)} 个（行级证据）"}

    def _读行(self, rel: str) -> list[str]:
        try:
            return (self._根 / rel).read_text(encoding="utf-8", errors="ignore").splitlines()
        except (OSError, ValueError):
            return []


def _缓存路径() -> Path | None:
    try:
        from . import real_task as _rt
        return Path(_rt.DIRS.WORKDIR) / _缓存名
    except Exception:                                # noqa: BLE001
        return None


def 自述召回(问句: str, mem=None, 上限文件: int = 4, 上限行: int = 3) -> dict:
    """一句问句 → 编排器自身代码的**行级证据**（文件、行号、那一行原文）。"""
    文件们 = 自述文件(mem)
    if not 文件们:
        return {"命中": [], "术语": [], "说明": "读不到工具自身文件清单（记忆索引没建）"}
    键 = hashlib.sha256("|".join(str(p) for p in 文件们).encode()).hexdigest()[:16]
    索 = _内存.get(键)
    if 索 is None:
        缓存 = _缓存路径()
        if 缓存 is not None:
            索 = 自述索引.取(缓存, 文件们)
        if 索 is None:
            索 = 自述索引.建(文件们)
            if 缓存 is not None:
                索.存(缓存)
        _内存[键] = 索
    return 索.召回(问句, 上限文件, 上限行)