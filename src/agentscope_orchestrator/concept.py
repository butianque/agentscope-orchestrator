r"""概念召回：**一句自然语 → 代码/证明文件网络**。

## 为什么需要它（本轮实测出来的真缺口）

`index.UnitIndex.找()` 是**按名**检索：问句里的词必须"本来就是已索引的名字"才命中。
拿一句自然语问线上 `context`：

    场景："让一个变换反复作用到自己身上，直到再作用一次也不变，那个不动的东西在
          引擎代码和形式化证明里分别落在哪些文件"

它老实回**「没命中」**（并说明"这些词都不是已索引的名字"）。这不是坏消息掩盖——
它如实说了。但人问的是**概念**，不是名字：

    「让变换反复作用到不再变」 ──应拉出──▶ `src/bin/固定点诊断.rs`
                                              + 活跃树里 27 份含该概念的 Lean
                                              + 46 条陈述里出现该概念的定理

⇒ 缺的是一层**概念召回**：从项目自己的文件内容里切出术语，建**术语→文件**倒排表；
   问句切 n-gram 去撞术语，按 IDF 打分；命中后按"代码侧 / 证明侧"分栏，
   并把证明侧里**陈述含该术语**的定理名一起给出来。

## 关键约束（都用判据钉住，`tests/concept_recall.py`）

1. **术语从项目自身产出**：词表不是手写的，是从声明文件内容里切的 n-gram
   （效果：换项目 = 换索引，不用改代码）。
2. **不遍历**：文件清单来自记忆索引（`项目索引()` 已扫过的那些），
   本模块**不 rglob、不 listdir**——判据里用计数器钉死"0 次目录枚举"。
3. **没命中就明说没命中**，绝不"凑几个最像的"当答案。
4. 同一条问句里**不带原术语**时召回应显著下降（这是能力边界，判据里如实记，
   不许把它说成"自然语言理解"）。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

# 中文术语：连续汉字串（至少 2 字）
_汉串 = re.compile(r"[\u4e00-\u9fff]{2,}")
# ASCII 标识符（>=3 字符，避免 a/b/i 这类噪声）
_标识 = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# 定理/引理声明
_定理 = re.compile(r"^(?:theorem|lemma)\s+([A-Za-z0-9_'.]+)", re.M)
# 跨侧引用：Rust 注释里点 Lean 文件 / Lean 定理名（这是**代码↔证明**之间唯一的机器可读桥）
_引文件 = re.compile(r"([A-Za-z0-9_\u4e00-\u9fff]+\.lean)")
_引定理 = re.compile(r"([a-zA-Z_][A-Za-z0-9_']*)\s*(?::|（|\()?\s*(?:TS\d+|定理|theorem|lemma)?")


def _注释里(文: str) -> str:
    """只取注释部分（`//`、`///`、`/* */`、`#`）——引用关系写在注释里，代码里没有。"""
    出: list[str] = []
    for 行 in 文.splitlines():
        s = 行.strip()
        if s.startswith(("//", "///", "//!", "*", "/*", "#", "--")):
            出.append(s)
    return "\n".join(出)

_MIN_N, _MAX_N = 2, 4          # n-gram 长度范围
_最少文件数 = 2                 # 只保留"至少出现在 2 个文件"的术语（过滤一次性噪声）
# 虚词/功能字：切成短语块时用（**只切查询侧**，索引侧也用同一条，保证口径一致）。
# ★ 2026-11-04 第二版修正：**「不 / 无 / 未 / 到 / 一」不能当虚词切**——
#   实测第一版把「不动点」切成「动点」，于是真正的概念词在索引里根本不存在
#   （`不动`/`不动点` 都查不到，只剩碎片 `作用一次`/`再作用一` 顶在第一屏）。
#   **拿停用词表切中文，切掉的可能正是术语的构词字。**
_虚词 = re.compile(r"[的了着过是在和与及或把被让给对从向往就都也还很更最"
                   r"个些那这其之而为以此所且但却并则若如虽然即使已经将]")
_停用术语 = frozenset({
    "这个", "那个", "一个", "我们", "你们", "他们", "自己", "什么", "怎么", "为什么",
    "可以", "就是", "不是", "还是", "以及", "因此", "所以", "但是", "如果", "因为",
    "已经", "现在", "这里", "那里", "这些", "那些", "一下", "一直", "反复", "不断",
    "作用", "东西", "事情", "问题", "部分", "情况", "时候", "地方", "文件", "代码",
    "证明", "形式化", "引擎", "哪些", "分别", "落在", "拿到", "看看", "能不能",
})


def _切术语(文: str) -> set[str]:
    """从一段文字里切出候选术语（汉字 n-gram + ASCII 标识符）。

    ★ 2026-11-04 实测修正：**不许跨虚词切**。第一版对整个汉字串取 n-gram，
      于是问句「那个不动的东西」切出 `那个不动`/`个不动的`/`不动的东西` 这类
      **跨词边界碎片** ⇒ 它们恰好也在文件里出现（注释里同款说法），IDF 还高
      ⇒ 能把噪声顶到真概念前面（实测首屏全是 `concept.py`、`结构见证_认知行为.rs`）。
      ⇒ 先按**停用虚词**把句子切成短语块，只在块内取 n-gram。
    """
    出: set[str] = set()
    for 串 in _汉串.findall(文):
        for 块 in _虚词.split(串):
            if len(块) < _MIN_N:
                continue
            n = len(块)
            for i in range(n):
                for L in range(_MIN_N, _MAX_N + 1):
                    if i + L <= n:
                        g = 块[i:i + L]
                        if g not in _停用术语:
                            出.add(g)
    for 符 in _标识.findall(文):
        出.add(符)
    return 出


def _指纹(文件们: list[Path]) -> str:
    h = hashlib.sha256()
    for p in 文件们:
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(str(p).encode())
        h.update(str(st.st_mtime_ns).encode())
        h.update(str(st.st_size).encode())
    return h.hexdigest()


def _沿图扩(档: dict) -> str:
    """沿项目级引用图缓存扩一跳，把邻居补进 代码/证明 两栏。**不建图、不枚举**。

    分类不写死扩展名：拿本函数**已经分好栏的那批文件**的 kind 当模板 ——
    新节点的 kind 属于"证明栏里的 kind"就进证明栏，属于"代码栏里的 kind"就进代码栏。
    """
    try:
        from . import refgraph as _rg
        from .profile import get_profile
        try:
            根 = Path(get_profile().repo_root)      # ★ 项目根（`_rt.ORCH` 是**工具根**，不是项目根）
        except Exception:                          # noqa: BLE001
            来自 = None
            return ""
        图 = _rg.只读缓存(根)
        if 图 is None or not 图.边:
            return ""
        证明类 = {图.节点.get(str(p)) for p, _ in 档["证明"]} - {None}
        代码类 = {图.节点.get(str(p)) for p, _ in 档["代码"]} - {None}
        已见 = {str(p) for p, _ in 档["证明"]} | {str(p) for p, _ in 档["代码"]}
        种子 = [str(p) for p, _ in 档["代码"][:12] + 档["证明"][:12]]
        加证 = 加码 = 0
        for q in 图.网络(种子, 最大跳=1):
            if q in 已见 or not Path(q).is_file():
                continue
            k = 图.节点.get(q)
            # ★ 只能**追加**、且限额：第一版插到最前面，把术语命中的那批挤出了前 N 个，
            #   判据立刻显示证明召回 0.48 → 0.00（"我加的东西把原来的能力顶掉了"）。
            if k in 证明类 and 加证 < 24:
                档["证明"].append((Path(q), 60.0))
                已见.add(q)
                加证 += 1
            elif k in 代码类 and 加码 < 24:
                档["代码"].append((Path(q), 60.0))
                已见.add(q)
                加码 += 1
        if 加证 or 加码:
            return f"；沿引用图扩到 {加码} 代码 + {加证} 证明（邻居，非遍历）"
    except Exception:                              # noqa: BLE001 没图就不扩，不影响主路
        pass
    return ""


class 概念索引:
    """术语 → 文件 的倒排表（术语从文件内容切出，IDF 权重）。"""

    def __init__(self, 倒排: dict[str, list[int]], 文件们: list[Path], 总文件数: int) -> None:
        self._倒排 = 倒排
        self._文件们 = 文件们
        self._N = 总文件数
        self._idf = {t: math.log((1 + self._N) / (1 + len(ids))) + 1.0
                     for t, ids in 倒排.items()}

    # ── 建/存/取 ────────────────────────────────────────────────
    @classmethod
    def 建(cls, 文件们: list[Path]) -> "概念索引":
        倒排: dict[str, list[int]] = {}
        自身 = Path(__file__).resolve()
        # ★ 只收**项目知识**，不收编排器自身实现（实测两次栽在这上面）：
        #   ① 本模块 docstring 里写了示范问句 ⇒ 它自己排第一；
        #   ② 判据 `tests/concept_recall.py`、`context_view.py` 里也有同款句子 ⇒ 又混进首屏。
        #   工具代码不是"项目知识"，它的句子更不是证据。口径：概念索引 = rs/lean/文档。
        工具根 = 自身.parent.parent.parent          # …/agentscope_orchestrator
        跳过 = (str(工具根 / "src"), str(工具根 / "tests"), str(工具根 / "tools"))
        for i, p in enumerate(文件们):
            try:
                s = str(p.resolve())
                if s == str(自身) or s.startswith(跳过):
                    continue
                文 = p.read_text(encoding="utf-8", errors="ignore")
            except (OSError, RuntimeError):
                continue
            for t in _切术语(文):
                倒排.setdefault(t, []).append(i)
        倒排 = {t: ids for t, ids in 倒排.items() if len(ids) >= _最少文件数}
        return cls(倒排, 文件们, max(1, len(文件们)))

    def 存(self, 缓存: Path) -> None:
        try:
            缓存.parent.mkdir(parents=True, exist_ok=True)
            缓存.write_text(json.dumps({
                "指纹": _指纹(self._文件们),
                "文件们": [str(p) for p in self._文件们],
                "倒排": self._倒排,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    @classmethod
    def 取(cls, 缓存: Path, 文件们: list[Path]) -> "概念索引 | None":
        try:
            原 = json.loads(缓存.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if 原.get("指纹") != _指纹(文件们):
            return None
        try:
            return cls({k: list(v) for k, v in 原["倒排"].items()}, 文件们, len(文件们))
        except (KeyError, TypeError):
            return None

    # ── 查询 ────────────────────────────────────────────────────
    def 术语(self, 问句: str, 上限: int = 12) -> list[tuple[str, float]]:
        """问句里**真在项目中出现过**的术语，按 IDF×长度 排序。

        ★ 第三版修正（实测驱动）：光按 IDF 排，会把 `作用一次`/`再作用一` 这类
          **同一句注释的碎片**顶到真概念（`不动点`）前面 —— 因为碎片在项目里更"稀有"。
          修法两条：
          ① **短词向长术语扩展**：`不动` 命中时，把索引里以它开头的长术语
             （`不动点`/`不动点收敛`）一并纳入并加权 —— 术语由项目自己产出，不是同义词表。
          ② 排序改为**命中术语长度之和**优先（长术语更像"就是这个概念"），IDF 次之。
        """
        打分: dict[str, float] = {}
        for g in _切术语(问句):
            if g in self._倒排:
                打分[g] = self._idf[g] * (1.0 + 0.35 * (len(g) - _MIN_N))
        # ① 短词→长术语扩展（前缀只查一次，用排序后的术语表做二分）
        if not hasattr(self, "_术语表"):
            self._术语表 = sorted(self._倒排)
        import bisect
        for g in list(打分):
            if len(g) >= 3:
                continue
            i = bisect.bisect_left(self._术语表, g)
            扩: list[str] = []
            while i < len(self._术语表) and self._术语表[i].startswith(g):
                t = self._术语表[i]
                if len(t) <= len(g) + 2:
                    扩.append(t)
                i += 1
            for t in 扩[:6]:
                打分.setdefault(t, 0.5 * self._idf.get(t, 1.0) * (1.0 + 0.35 * (len(t) - _MIN_N)))
        return sorted(打分.items(),
                      key=lambda kv: (-sum(1 for _ in kv[0]), -len(kv[0]), -kv[1]))[:max(1, 上限)]

    def 召回(self, 问句: str, 上限文件: int = 40, 上限定理: int = 40) -> dict:
        命中 = self.术语(问句)
        # ★ 证据闸（判据 `tests/concept_recall.py` 抓出来的真缺陷）：
        #   第一版对「紫色大象在冰箱里烤面包」也回 20 个文件 —— 因为 2 字碎片
        #   （`冰箱`/`面包`）在语料里总能撞上。**能凑出几个最像的，不是能力，是假能力。**
        #   ⇒ 只认长度 ≥3 的术语或 ASCII 标识符；凑不出强证据就明说没命中。
        强 = [(t, w) for t, w in 命中 if len(t) >= 3 or t.isascii()]
        if not 强:
            return {"概念术语": [], "代码": [], "证明": [], "文档": [], "定理": [],
                    "说明": "没命中 —— 问句里没有任何**强术语**（≥3 字或标识符）"
                            "出现在本项目的文件内容里（不是「这一类没进索引」，"
                            "是这些词项目里真没有）"}
        命中 = 强
        if not 命中:
            return {"概念术语": [], "代码": [], "证明": [], "文档": [], "定理": [],
                    "说明": "没命中 —— 问句里没有任何术语出现在本项目的文件内容里"
                            "（不是「这一类没进索引」，是这些词项目里真没有）"}
        文件分 = [0.0] * len(self._文件们)
        for t, w in 命中:
            for i in self._倒排[t]:
                文件分[i] += w
        排序 = sorted((i for i, s in enumerate(文件分) if s > 0),
                      key=lambda i: -文件分[i])
        档 = {"代码": [], "证明": [], "文档": []}
        for i in 排序:
            p = self._文件们[i]
            名 = p.name.lower()
            if 名.endswith((".rs", ".py", ".toml")):
                档["代码"].append((p, round(文件分[i], 3)))
            elif 名.endswith(".lean"):
                档["证明"].append((p, round(文件分[i], 3)))
            else:
                档["文档"].append((p, round(文件分[i], 3)))
        # 证明侧：把"陈述里含命中术语"的定理名一起取出（这是网络里的节点，不是文件列表）
        术语集 = {t for t, _ in 命中}
        定理: list[tuple[Path, str]] = []
        # ① 跨侧网络：沿**项目级引用图缓存**扩一跳（**只读，不建图**）
        #
        #   为什么：术语倒排只在"同语言同说法"里有效 —— 代码说中文、证明写英文，
        #   术语撞不上；但代码注释里点名了证明文件，那是项目自己长出来的边。
        #   ★ 上一版这里写死了 `.lean`，换项目就废；现在调通用机制 `refgraph`。
        #   ★ 只读缓存（`refgraph.只读缓存`）：建图要 145 秒、要目录枚举 ——
        #     **建图是索引期的事**，放进问答路径会被判据当场抓住（实测 209 秒 / 5.5 万次枚举）。
        扩解释 = _沿图扩(档)
        for p, _w in 档["证明"][:上限文件]:
            try:
                文 = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in _定理.finditer(文):
                块 = 文[m.end():m.end() + 800]
                if any(t in 块 for t in 术语集):
                    定理.append((p, m.group(1)))
                    if len(定理) >= 上限定理:
                        break
            if len(定理) >= 上限定理:
                break
        return {
            "概念术语": [[t, round(w, 2)] for t, w in 命中],
            "代码": 档["代码"][:上限文件],
            "证明": 档["证明"][:上限文件],
            "文档": 档["文档"][:上限文件],
            "定理": 定理,
            "说明": f"术语命中 {len(命中)} 个；文件 {len(排序)} 个（代码/证明/文档分栏）" + 扩解释,
        }


# ── 对外入口（走记忆索引的文件清单，不自己遍历目录）──────────────────
_缓存名 = "concept_index.json"
_内存: dict[str, 概念索引] = {}


def 文件清单(mem=None) -> list[Path]:
    """从记忆索引取**去重后的文件清单** —— 本模块因此不需要自己遍历目录。"""
    if mem is None:
        from .autoengine import 项目索引
        mem = 项目索引()
    seen: dict[str, Path] = {}
    for kind in mem.kinds():
        idx = mem.index(kind)
        for k in idx.keys():
            p = idx.query(k)
            if p is None or not p.is_file():
                continue
            seen.setdefault(str(p), p)
    return sorted(seen.values(), key=lambda p: str(p))


def 概念召回(问句: str, mem=None, 上限文件: int = 40, 上限定理: int = 40) -> dict:
    """一句自然语 → 代码/证明/文档文件网络 + 定理清单。"""
    from . import real_task as _rt
    文件们 = 文件清单(mem)
    键 = hashlib.sha256("|".join(str(p) for p in 文件们).encode()).hexdigest()[:16]
    if 键 in _内存:
        return _内存[键].召回(问句, 上限文件, 上限定理)
    try:
        缓存 = Path(_rt.DIRS.WORKDIR) / _缓存名      # 运行期缓存：工作目录（已被 .gitignore 挡住）
    except Exception:                                          # noqa: BLE001
        缓存 = None
    索 = None
    if 缓存 is not None:
        索 = 概念索引.取(缓存, 文件们)
    if 索 is None:
        索 = 概念索引.建(文件们)
        if 缓存 is not None:
            索.存(缓存)
    _内存[键] = 索
    return 索.召回(问句, 上限文件, 上限定理)