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
            # ★ 顺手落"项目烂大街词"侧车：自查闸只读这个小文件（毫秒级），
            #   不必为了查几个词的 df 去载入 43MB 主索引（实测那是套件变慢的真因）。
            _写常见词侧车(缓存, self._倒排)
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
        模型解释 = ""
        模型档: dict[str, float] = {}
        # ★ 语义那一跳：**必须在打分之前**。第一版插到了打分之后 ——
        #   术语报对了（`固定点`）、文件一个没变（还是碎片的那些），白跑。
        # ★ 触发条件用**问句形态**，不用命中数：实测自然语也"命中 12 个"，但全是
        #   「直到再作」这类无信息碎片 ⇒ 用命中数判会漏。长句 = 人话，走语义抽词。
        if len(命中) < 3 or len(问句.strip()) >= 15:
            抽 = 模型抽词(问句)
            合: dict[str, float] = {t: w for t, w in 命中}
            # ★ 「够专」闸：概念词必须是**少见**的。模型会给出「代码」「证明」「文件」
            #   这种满仓都是的词，它们一进倒排就把整仓文件全拉出来（实测就是这么退化的）。
            #   判据用文档频率，不用屏蔽词表 —— 换项目照样成立。
            够专 = max(8, int(self._N * 0.06))

            def _够专(t: str) -> bool:
                ids = self._倒排.get(t) or []
                return bool(ids) and len(ids) <= 够专

            for t in 抽:
                if t in self._倒排 and not _够专(t):
                    continue                     # 太常见 ⇒ 不是概念，是背景词
                if t in self._倒排:
                    模型档[t] = self._idf.get(t, 1.0) + 5.0
                    continue
                # ★ 不再做前缀扩展：实测它把「形式化三」「证明一字」这类**罕见垃圾 n-gram**
                #   当成概念词（它们文档频率低，够专闸拦不住），一批就压住真术语。
                #   同义说法由模型直接给（提示已要求），比我们猜扩展可靠得多。
            合.update(模型档)
            # ★ 人话问句里词面碎片是**噪声**，留一个都是污染（实测：加权后噪声仍压住真术语）。
            #   所以长句一旦模型抽出术语，就**只用模型那批**，把碎片整批丢掉。
            if 模型档:
                命中 = sorted(模型档.items(), key=lambda kv: -kv[1])[:10]
                模型解释 = f"；模型抽词 {len(抽)} 个 → 采用 {len(命中)} 个专术语"
        # 模型已判过「够专」的 2 字术语不该再被强证据闸丢掉
        强 = [(t, w) for t, w in 命中 if len(t) >= 3 or t.isascii() or t in 模型档]
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
        # ★★★★★ 2026-11-05：**模型词单靠自己不许"凑答案"**（判据漂移逼出来的）
        #   真事故：套件里负对照「哞哞咩咩吽吽噗呲」偶发回 20 个文件，而单跑是 0 ——
        #   因为模型那一次把拟声词抽成了通用词（「声音」「拟声」），每个都能撞上一批文件。
        #   ⇒ 这类回答正是「能凑出几个最像的」= **假能力**。

        #   规则（确定性，不依赖模型那次抽得好不好）：
        #     问句**自身**的强术语一个都没命中时（全靠模型档），**且这批模型词全是常见词**
        #     （每个的文档频率都 > 3%，即"够专"闸的一半）⇒ 只认**同一文件共现 ≥2 个**的证据。
        #   为什么要"全是常见词"这个前提：实测（同日两次翻车）
        #     · 只加共现闸 ⇒ **真能力被打死**：自然语召回 1.00 → 0.05。
        #       模型给的是 `不动点`（罕见）+ `迭代/收敛`（常见），真 GT 文件里常见词往往只出现一个，
        #       共现 ≥2 根本凑不齐 ⇒ 合法答案被闸掉。
        #     · 只加"单词必须罕见"闸 ⇒ 也打死真能力（1.00 → 0.00）：`固定点` 本身文档频率高。
        #   ⇒ 闸只该对**"整批都是满仓通用词"**这种形态生效（那正是拟声词被抽成
        #     「声音」「拟声」的样子），不许对任何一种"含罕见词"的正常抽取生效。
        自身强 = [(t, w) for t, w in 命中 if t not in 模型档]
        仅模型说明 = ""
        if 模型档 and not 自身强 and len(命中) >= 2:
            常见阈 = max(8, int(self._N * 0.03))
            全常见 = all(len(self._倒排.get(t) or []) > 常见阈 for t, _ in 命中)
            if 全常见:
                共现 = [0] * len(self._文件们)
                for t, _w in 命中:
                    for i in self._倒排[t]:
                        共现[i] += 1
                前 = len(排序)
                排序 = [i for i in 排序 if 共现[i] >= 2]
                仅模型说明 = (f"；问句自身无强术语且模型词全是常见词 ⇒ "
                              f"只认 **≥2 个模型术语共现** 的文件（{前} → {len(排序)}）")
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
            "说明": f"术语命中 {len(命中)} 个；文件 {len(排序)} 个（代码/证明/文档分栏）"
                    + 扩解释 + 模型解释 + 仅模型说明,
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


# ── 语义那一跳：让模型把「人话」抽成「项目术语」────────────────────
# 为什么要它：术语倒排只能撞"同一个说法"。实测人问「反复作用到不再变」时，
# 词面全撞不上（自然语召回 0.06~0.25），而模型 4~10 秒就能抽出 `不动点` 这种真术语。
# 这一步**只在术语命中不足时**才发生，所以平时问答不花模型调用。
_抽词缓存: dict[str, list[str]] = {}


def _同步跑(协程):
    """在**独立线程的事件循环**里跑协程 —— MCP 服务本身是 async，直接 asyncio.run 会炸。"""
    import asyncio, concurrent.futures
    def 跑():
        return asyncio.run(协程)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as 池:
        return 池.submit(跑).result()


def 模型抽词(问句: str) -> list[str]:
    """模型把自然语问句抽成**领域概念词**（失败一律返回 []，绝不抛：这是增强不是前提）。"""
    键 = 问句.strip()
    if 键 in _抽词缓存:
        return _抽词缓存[键]
    词们: list[str] = []
    try:
        from . import gateway
        通道, 报 = gateway.select_live_channels("")
        对 = gateway.live_model_pairs(通道, 报)
        if 对:
            客户 = gateway.build_client(对[0][1], 对[0][2])
            提示 = ("你是中文技术术语抽取器。把这句话里的领域概念抽成术语，"
                  "**同一概念的不同说法都要列出**（例如「不动点」也要列出「固定点」），"
                  "只输出 JSON 数组（每个元素 2-4 个字，最多 8 个），不要解释。\n问题：" + 键)
            回 = _同步跑(客户([{"role": "user", "content": 提示}]))
            # ★ 实测：`回.content` 是**文本块列表**（`[文本块(text=..., type=...)]`），
            #   不是字符串 —— 直接 str() 会让正则/JSON 解析全废（第一版就是这么死的）。
            内 = getattr(回, "content", 回)
            if isinstance(内, str):
                文本 = 内
            elif isinstance(内, (list, tuple)):
                段 = []
                for b in 内:
                    t = getattr(b, "text", None)
                    if t is None and isinstance(b, dict):
                        t = b.get("text")
                    if t:
                        段.append(str(t))
                文本 = "\n".join(段)
            else:
                文本 = str(内)
            m = re.search(r"\[[^\]]*\]", 文本)
            原 = json.loads(m.group(0)) if m else []
            词们 = [str(t).strip() for t in 原 if isinstance(t, (str, int)) and 2 <= len(str(t).strip()) <= 6]
    except Exception:                                   # noqa: BLE001 没通道/超时/格式坏 ⇒ 当没这一步
        词们 = []
    _抽词缓存[键] = 词们
    return 词们


def _取索引(mem=None) -> tuple["概念索引 | None", list[Path]]:
    """取（必要时建）**项目**概念索引 —— 内存缓存 → 磁盘缓存 → 重建，三级。"""
    from . import real_task as _rt
    文件们 = 文件清单(mem)
    键 = hashlib.sha256("|".join(str(p) for p in 文件们).encode()).hexdigest()[:16]
    if 键 in _内存:
        return _内存[键], 文件们
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
    return 索, 文件们


def 术语文频(词们: list[str], mem=None) -> dict[str, int]:
    """这些术语**在项目语料里出现在多少个文件**（0 = 项目里根本没有这个词）。

    ★ 为什么需要它（2026-11-05）：`selfrecall`（自查）要知道"这个词是不是工具专有的"。
      实测两次污染都是同一个形状：我在注释里写了一句话解释某个项目问句，
      于是那个**项目专名**（如「关系式族差」）在工具代码里出现了 2 次，
      自查索引就把它当工具术语，项目问句一进来就显示「编排器自身」段。
      ⇒ 判据：**项目里很常见的词，不是工具术语**（不管它在工具注释里出现过几次）。

    ★★ 但**别在热路径上调它**：概念索引文件 43MB，整块载入一次几秒 + 几百 MB 内存，
      实测把整套判据从 1062s 拖到 1871s，`g3_sqlite` 被判超时。
      ⇒ 需要"是不是烂大街词"的地方请用 `项目常见词()`（毫秒级侧车查表）。
    """
    索, _文件们 = _取索引(mem)
    if 索 is None:
        return {}
    表 = getattr(索, "_倒排", {}) or {}
    return {t: len(表.get(t, [])) for t in 词们}


# ── 「项目烂大街词」侧车（小文件）：给自查闸用，避免在热路径载入 43MB 索引 ──
_常见词侧车 = "concept_common_terms.json"
_常见词缓存: dict[str, frozenset[str]] = {}
_常见阈 = 80          # 出现在 80 个以上项目文件里的词 = 项目通用词


def _写常见词侧车(缓存: Path, 倒排: dict) -> None:
    try:
        常见 = sorted(t for t, ids in 倒排.items() if len(ids) > _常见阈)
        (缓存.parent / _常见词侧车).write_text(
            json.dumps({"阈": _常见阈, "词": 常见}, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError):
        pass


def 项目常见词(词们: list[str]) -> set[str]:
    """这些词里**哪些是项目通用词**（项目语料里出现 >80 个文件）。

    读**侧车**（建索引时顺手落盘的小 JSON），不载入 43MB 主索引；
    侧车不存在时退回主索引算一次（只在索引刚建、还没存侧车时发生）。
    """
    try:
        from . import real_task as _rt
        缓存 = Path(_rt.DIRS.WORKDIR) / _缓存名
        侧 = 缓存.parent / _常见词侧车
        键 = str(侧)
        if 键 not in _常见词缓存:
            if 侧.is_file():
                原 = json.loads(侧.read_text(encoding="utf-8"))
                _常见词缓存[键] = frozenset(原.get("词") or [])
            else:
                频 = 术语文频(词们)
                return {t for t, df in 频.items() if df > _常见阈}
        return {t for t in 词们 if t in _常见词缓存[键]}
    except Exception:                                  # noqa: BLE001 取不到就不筛
        return set()


def _本块缺口(文件们: list[Path], 上限: int = 12) -> dict:
    """这一批召回出来的文件**点到了哪些点不到的名字** —— 局部召回缺的那半。

    ★★★ 2026-11-07（R303）：章程 §3.5 那条「缺口必须是**节点**，不是「没查到」」
      在**常驻读数**里已经落地（`mem_live.缺口() ④图上死引用`）。
      但**局部召回**这一路还是只拉「**有的**」东西 ——
      问一句自然语，它回「代码/证明/文档/定理」，**一个字不提这块缺什么**。
      而「演化进度」正是靠缺口数变化的：**召回时不报缺，就等于把进度藏起来。**

    ★ 成本：只读 `refgraph` 缓存（热）+ `mem_live` 侧车（2,228 个名字）。
      **不走全仓** —— 问答路径上不能走全仓（实测 17.4 s，上一次把建图放进
      问答路径被判据当场抓住，实测 209 s）。

    ★ 三分类**必须分开报**（本工程最贵的一课）：真死 / 被剪枝但存在。
      只报「点不到」而不说其中多少只是被剪枝，就是把「在别的根里」报成「悬空」。
    """
    try:
        from . import refgraph as _rg
        from . import mem_live as _ml
    except Exception as e:                       # noqa: BLE001
        return {"未测到": f"导不进模块（{type(e).__name__}: {e}）"}

    try:
        from .runtime import ORCH as _ORCH       # type: ignore[attr-defined]
    except Exception:                            # noqa: BLE001
        try:
            from pathlib import Path as _P
            _ORCH = _P(__file__).resolve().parents[2]
        except Exception:                        # noqa: BLE001
            return {"未测到": "拿不到仓库根"}
    仓根 = _ORCH.parents[1]

    图 = None
    try:
        图 = _rg.只读缓存(仓根)                   # ★ 只读，不建图
    except Exception:                            # noqa: BLE001
        pass
    if 图 is None:
        return {"未测到": "refgraph 没有缓存档（建图是索引期的事，不在问答路径上做）"}

    本批 = {str(p).replace("\\", "/").lower() for p in 文件们}
    真死名 = _ml.真死名字()
    命真死, 命剪枝 = [], []
    for 源, 目标 in 图.死引用:
        if str(源).replace("\\", "/").lower() not in 本批:
            continue
        尾 = 目标.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if 尾 in 真死名:
            命真死.append([Path(str(源)).name, 目标])
        else:
            命剪枝.append([Path(str(源)).name, 目标])
    出 = {
        "★这一块缺什么": "本批召回文件**点名但点不到**的东西（缺口进局部召回，不只进全局读数）",
        "①真死引用（全仓都没有）": len(命真死),
        "②被剪枝但存在（**不是缺口**）": len(命剪枝),
        "①例": 命真死[:上限],
        "②例": 命剪枝[:min(上限, 4)],
        "算法": ("取本批文件 → 在 refgraph 缓存的 `死引用` 里筛源文件命中的 → "
                  "拿 `mem_live.真死名字()` 分①/②"),
    }
    if not 真死名:
        出["未测到"] = ("`mem_live` 侧车不在 ⇒ ①/② 分不开；"
                        "**这不算「0 条」**（「看不见」不能当「没有」）")
    return 出


def 概念召回(问句: str, mem=None, 上限文件: int = 40, 上限定理: int = 40) -> dict:
    """一句自然语 → 代码/证明/文档文件网络 + 定理清单 + **这一块缺什么**。

    ★ 加 `缺口` 段的理由：召回只拉「有的」东西，等于把演化进度藏起来 ——
      章程 §3.5「缺口必须是**节点**，不是「没查到」」在局部召回这一路也要成立。
    """
    索, _文件们 = _取索引(mem)
    if 索 is None:
        return {"代码": [], "证明": [], "定理": [], "缺口": {"未测到": "索引不在"}}
    果 = 索.召回(问句, 上限文件, 上限定理)
    try:
        文件们 = [p for 栏 in ("代码", "证明", "文档") for p, _w in (果.get(栏) or [])]
        果["缺口"] = _本块缺口(文件们)
    except Exception as e:                       # noqa: BLE001
        果["缺口"] = {"未测到": f"{type(e).__name__}: {str(e)[:80]}"}
    return 果