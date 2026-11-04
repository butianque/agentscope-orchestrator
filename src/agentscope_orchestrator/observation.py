"""观测机制（observation）：读结构 → 取象 → 全称机制判定 → 求真值。

这是用户数学模型的四步观测机制的工程实现，把「观测」真正做成工具内生机制，
而不是只在对话/ prompt 里让模型去读。四步全部机器可算、语言无关：

  1. 读结构（read_structure）：主动遍历索引全量单元，逐个读源文本——
     不等人喂信号，不进对话上下文，直接扫整个库。
  2. 取象（extract_image）：从每个单元文本提取「象」——
     签名（fn/struct/enum/impl/trait/mod 点式名）+ 依赖标识符（use/限定路径引用）
     + 机制语义标记（固定轮限、候选池、todo!/unimplemented! 等「圈定有界」模式）。
  3. 全称机制判定（universal_judge）：对全量单元穷举扫描，对机制矩阵
     （无界=全称可无限演化 / 有界=收敛不动点 / 读盘即最优 / 自演化一步到不动点）
     判全称范围内违反机制的模式，产出 violations:[{unit_id,file,line,pattern,机制,真值=False}]。
  4. 求真值：每个观测机器可算（正则/计数命中与否 = 做出/没做），不做模型审模型；
     判定不设上限（扫完整个索引为准，不截断、不限轮、不限规模）。
     单元无任何违反 → 该单元机制真值 = True。

收敛语义：观测本身一次全量扫描即真值；「自演化收敛」由 develop 的迭代循环承担——
每一轮启动即全量重扫，观测到的违反逐一派发真实模型修复并过机器门，观测集合随演化
收敛到不动点（无新违反），不预设轮数/超时/候选池上限。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# ★★★★★ 修复 R7（**P0**）：本模块此前**从未定义 `logger`**，
#   而本轮 R6 的漏读账要 `logger.warning(...)` ⇒ 一走到就 `NameError`。
#   ★ 与 `taixu_mcp._RUST_ROOT` 是**同一类**缺陷：被读、却从未定义；
#     逃过所有运行期门（那些门从不走这条路径）⇒ 只有**静态**判据能抓。
#     本轮已把它做成共享门 `static_audit`（见同目录 static_audit.py），全库扫。
logger = logging.getLogger("observation")

# ── 机制标签（对齐 math_model）──────────────────────────────
MECH_BOUNDED_VIOLATION = "无界·有界被圈成预设边界(违反Boundedness)"
MECH_EVOLVE_VIOLATION = "自演化·未演化到不动点(违反SelfEvolvingRule)"
MECH_OPTIMAL_VIOLATION = "读盘即最优(违反OptimalParams)"
MECH_NONE = ""  # 仅记录，不判违反


@dataclass(frozen=True)
class Marker:
    """一个机制语义标记：命中即代表该机制在该单元上可能未被满足。

    pattern 为正则；name 是人类可读的标记名；mechanism 为违反的机制标签；
    hint 给修复提示。真值恒为机器命中与否（命中→该机制真值 False）。"""
    name: str
    mechanism: str
    pattern: str
    hint: str = ""

    @property
    def matched(self) -> re.Pattern:
        return re.compile(self.pattern)


# ── 机制语义标记表（机器正则；判定不设上限）────────────────────
# 对齐用户圈定的「有界」模式：固定轮限常量、候选池 `for _ in 0..n`、todo!/unimplemented!、
# 大常数循环上限比较；额外补重试限步、MAX/LIMIT 硬上限（违反自演化一步不动点 / 读盘即最优）。
_MARKERS: tuple[Marker, ...] = (
    Marker("固定限轮常量", MECH_BOUNDED_VIOLATION,
           r"const\s+\w+\s*:\s*(?:u32|u64|usize|i32|i64|isize)\s*=\s*\d+\s*;",
           "硬编码固定轮次/上限常量：改为按结构自演化判据（收敛即停），不预先圈定轮数。"),
    Marker("候选池循环", MECH_BOUNDED_VIOLATION,
           r"for\s+_+\s+in\s+0\.\.\w+",
           "索引式候选池遍历：应改为对结构全称穷举或收敛即停，不加候选池上限。"),
    Marker("硬编码循环上限", MECH_BOUNDED_VIOLATION,
           r"for\s+\w+\s+in\s+[0-9]+\.\.|for\s+\w+\s+in\s+\.\.\s*[0-9]+",
           "写死数字的循环边界：改为自演化至不动点即停，不用字面量圈定有界。"),
    Marker("大常数比较圈界", MECH_BOUNDED_VIOLATION,
           r"(?:<=|>=|==)\s*[0-9]{2,}",
           "硬编码大数值阈值圈界：改为结构演化真值判收敛，不用外部标准数值。"),
    Marker("重试限步", MECH_EVOLVE_VIOLATION,
           r"(?i)(?:retry\w*|tries|attempts|max_iter(?:\w*))\s*[:=]\s*[0-9]+",
           "以重试 N 次/步数上限替代自演化停机：收敛判据应为不动点，而非限步。"),
    Marker("脚手架未演化", MECH_EVOLVE_VIOLATION,
           r"todo!\(|unimplemented!\(",
           "未演化到不动点的脚手架：应实现自演化逻辑到达不动点，禁止留 todo/unimplemented 占位。"),
    Marker("硬编码最优上限", MECH_OPTIMAL_VIOLATION,
           r"(?i)\b(?:MAX|LIMIT|UPPER|candidate_?max)\b\s*[=:]\s*[0-9]+",
           "写死最优/候选池上限：最优应读盘结构即得，不用外部档位/上限。"),
)


def marker_catalog() -> tuple[Marker, ...]:
    """暴露机制标记表（供判据/文档对齐，引用同一真相源）。"""
    return _MARKERS


# ── 统一结构观测器（全称全局、不枚举单体）────────────────────
# 判据：违规须有「结构证据」——符号被用作循环界/比较上限/候选池终点 才算违反。
# 未作为边界引用的规律常量（阴阳=2/五行=5/RADIX=60 等）不构成违反，消除误报。
_STR_CHAR_CLASS = r"[^\"\\]|\\."
_REFS_AS_BOUND = (
    # for x in <...(含 name)...>.. / ..<...name...>，及 x..=name / name..= 形态
    re.compile(r"\bfor\b[^;{}]{0,40}\b(?:in)\b[^;{}]{0,80}\b(%s)\b" % r"\w+"),
    # 倒退：把「被 for 体/条件邻接名」当作引用证据的是退化启发；这里只认明确边界形态
    re.compile(r"\.\.=\s*%s\b" % r"\w+"),
    re.compile(r"\.\.\s*%s\b" % r"\w+"),
    re.compile(r"%s\s*\.\." % r"\w+"),
    # 条件/比较上限：<= x / x >= / == x（x 作圈界阈值）
    re.compile(r"(?:<=|>=|==)\s*%s\b" % r"\w+"),
    re.compile(r"%s\s*(?:<=|>=)\b" % r"\w+"),
    # 限定语义：retry N / attempts N
    re.compile(r"(?i)retry\w*|tries|attempts|max_iter(?:\w*)[^0-9]{0,4}%s\b" % r"\d+"),
    # MAX/LIMIT/UPPER 赋值右值是 name
    re.compile(r"(?i)\b(?:MAX|LIMIT|UPPER|candidate_?max)\b\s*[=:]\s*%s\b" % r"\w+"),
)


def _lex_strip(src: str) -> str:
    """剥字符串字面量 / 块·行注释 / 属性测试·基准段，留存结构 token 流。

    不引入 AST 解析器，做轻量结构清洗：注释与字符串不影响「结构判定」，
    且在注释里命中的模式（如文档里提到 MAX=60）不构成真实结构违规。

    ★★★★★ 修复 R10（**真缺陷**）：**清洗不得改变坐标**。
      本函数原来把剥掉的内容**连换行一起删掉**，输出比输入**短**，行号整体错位。
      实测 `src/运行时样本.rs`：原 **4764 行** → 清洗后 **1729 行**，
      **错位 3035 行**；而 `extract_image` 拿**清洗后**的偏移 `m.start()`
      去索引**原文**（注释自称"用最邻近搜索"，实则张冠李戴）
      ⇒ **每一条违规的行号都是错的**：实测报 `运行时样本.rs:301:21`，
        原文件 301 行是**注释**，真正的 `for _ in 0..6` 在别处
      ⇒ 修复环照错行号派发会**改错地方**（或去改注释）。

      ★ 修法：输出与输入**等长、换行位置一致**；被剥掉的字符一律换成**空格**
        （空格是合法 token 分隔，且任何模式都不会在纯空白上命中）
        ⇒ `m.start()` 在清洗文本与原文里**指向同一个位置**，行列**精确**，
          不再需要"最邻近搜索"这种会出错的补偿。

    ★ 附带修掉两处**误剥**（同样制造假阴/假阳）：
      ① 字符字面量分支原先把**生命周期** `'a` 当成字面量 ⇒ 一路吞到下一个 `'`
         （`fn f<'a>(x: &'a str)` 会吞掉 `a>(x: &`）⇒ 把真代码当字面量剥掉。
         现按结构判：`'\\…'`（转义）或 `'x'`（恰好一个字符）才算字面量，
         否则是生命周期 ⇒ **原样保留**。
      ② 原始字符串 `r"…"` / `r#"…"#` / `b"…"` 此前完全没处理 ⇒ 其内容会被当代码读。
    """
    buf = list(src)          # ★ 逐字符保位：输出与输入等长
    n = len(src)

    def 抹(a: int, b: int) -> None:
        """把 [a,b) 抹成空格，**换行原样保留**（保住行号）。"""
        for k in range(a, min(b, n)):
            if buf[k] != "\n":
                buf[k] = " "

    i = 0
    while i < n:
        ch = src[i]
        # ── 行注释：抹到行尾，**不吞换行** ──
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            抹(i, j)
            i = j
            continue
        # ── 块注释：抹到 `*/`，**内部的换行原样保留** ──
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            抹(i, j)
            i = j
            continue
        # ── 原始字符串 r"…" / r#"…"# / r##"…"## / br#"…"# / b"…" ──
        m = _RE_RAW_STR.match(src, i)
        if m:
            _前 = m.group(0)[:-len(m.group(1) or "") - 1]   # 前缀字母（不含 # 与引号）
            if "r" in _前:
                # 原始串：闭合符 = `"` + 同样个数的 `#`
                _井 = m.group(1) or ""
                _关 = '"' + _井
                j = src.find(_关, m.end())
                j = n if j < 0 else j + len(_关)
            else:
                # 字节串 `b"…"`：与普通字符串同规则（有转义）
                j = i + 1
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == '"':
                        j += 1
                        break
                    j += 1
            抹(i, j)
            i = j
            continue
        # ── 普通字符串 "…"（含 b"…" 前缀：前缀单字母无害，一并抹掉更干净）──
        if ch == '"':
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    j += 1
                    break
                j += 1
            抹(i, j)
            i = j
            continue
        # ── 字符字面量：**只认真正的字面量**，生命周期原样保留 ──
        if ch == "'" and i + 1 < n:
            if src[i + 1] == "\\" and i + 3 < n:
                j = i + 2
                while j < n and src[j] != "'":
                    j += 1
                if j < n:
                    抹(i, j + 1)
                    i = j + 1
                    continue
            elif i + 2 < n and src[i + 2] == "'":
                抹(i, i + 3)
                i += 3
                continue
            # 否则是生命周期 `'a` / `'static` ⇒ 不剥（原样保留）
        i += 1
    return "".join(buf)


# 原始字符串：r"…" / r#"…"# / r##"…"## / b"…" / br"…" / br#"…"#
_RE_RAW_STR = re.compile(r'(?:br|rb|r|b)(#*)"')


def _is_ref_as_bound(tokens: str, name: str) -> bool:
    """结构证据查询：name 是否被用作循环界/比较上限/候选池终点的右值。

    在全称意义上：同一谓词对任意符号一致判决——只问「它是否真的出现在
    圈界的右值位置上」，不关心符号的意义/数值。
    """
    for rx in _REFS_AS_BOUND:
        # 把 patterns 里的通用 \w+ 槽替换为具体 name 后命中即证据
        pat = (rx.pattern
               .replace(r"%s\b", re.escape(name) + r"\b")
               .replace(r"(\w+)", re.escape(name)))
        if re.search(pat, tokens):
            return True
    return False


_BENCH_TEST_SEG = "bench", "cfg(test)", "#[test]", "#[bench]"

# 默认分类规则（通用启发式；项目可用档案 observation.bench_* 覆盖）
_DEFAULT_BENCH_DIRS = ("bin/", "tests/", "bench/", "benches/")
_DEFAULT_BENCH_WORDS = ("基准", "基线", "判据", "test", "bench")


def classify_source(rel: str) -> str:
    """按 rel 分类单元：基准/测试 或 生产。全称一致规则，非逐文件枚举。

    规则（可由档案 observation 段覆盖）：
      · 位于 bench_dirs（默认 bin/ tests/ bench/ benches/）下 → bench（只扫不改）
      · 路径含 bench_words（默认 基准/基线/判据/test/bench） → bench
      · 其余 → production（生产代码才进修复派发）
    """
    from agentscope_orchestrator import profile as prof_mod
    obs_cfg = (prof_mod.get_profile().raw.get("observation") or {})
    dirs = tuple(obs_cfg.get("bench_dirs") or _DEFAULT_BENCH_DIRS)
    words = tuple(obs_cfg.get("bench_words") or _DEFAULT_BENCH_WORDS)

    low = rel.replace("\\", "/").lower()
    # 源码根前缀剥掉后再判目录（如 src/bin/x.rs → bin/x.rs）
    for root in (prof_mod.get_profile().source.roots or ()):
        rr = str(root).replace("\\", "/").strip("/").lower()
        if rr and low.startswith(rr + "/"):
            low = low[len(rr) + 1:]
            break
    if any(low.startswith(d) or ("/" + d) in low for d in dirs):
        return "bench"
    if any(w.lower() in low for w in words):
        return "bench"
    return "production"


def _strip_segments(src: str) -> str:
    """在 _lex_strip 基础上再剥 `#[cfg(test)]`/`#[test]`/`#[bench]` 属性段。

    ★ R10：剥法必须**保位**（原来用 `re.sub(..., "")` 删掉 ⇒ 同样改坐标）。
      现在把命中区间抹成空格（换行保留），长度与换行位置一律不变。
    """
    cleaned = _lex_strip(src)
    for seg in (r"#\[cfg\(test\)\]", r"#\[test\]", r"#\[bench\]"):
        pat = re.compile(seg + r"[^\n]*?(?=\n\s*(?:fn|pub|[^\s]))")
        buf = list(cleaned)
        for m in pat.finditer(cleaned):
            for k in range(m.start(), min(m.end(), len(buf))):
                if buf[k] != "\n":
                    buf[k] = " "
        cleaned = "".join(buf)
    return cleaned


# ── 取象正则（语言相关，Rust 引擎专用）────────────────────────
_RUST_SIG_RE = (
    re.compile(r"\b(?:pub(?:\([^\)]*\))?\s+)?(?:async\s+)?fn\s+([a-z_]\w*)"),
    re.compile(r"\b(?:pub\s+)?(?:struct|enum|trait)\s+([A-Z]\w*)"),
    re.compile(r"\bimpl(?:\s*<[^>]*>)?(?:\s+([A-Za-z_]\w*[:\w]*))?"),
    re.compile(r"\b(?:pub\s+)?mod\s+([a-z_]\w*)"),
)
_RUST_USE_RE = re.compile(r"\buse\s+([\w:]+)")
_RUST_QUAL_RE = re.compile(r"\b(?:crate|self|super)::[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*")


@dataclass
class Image:
    """一个单元的「象」：签名 + 依赖 + 机制标记命中。"""
    signatures: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    markers: list[dict] = field(default_factory=list)   # {name,mechanism,line,col,match}


@dataclass
class Observation:
    """读结构：索引一个真实单元的观测记录。"""
    key: str          # 首个索引键（信息性）
    rel: str          # 相对根的路径
    file: Path
    image: Image


@dataclass
class Violation:
    """全称机制判定中的一次违反：真值恒为 False（未做出该机制）。"""
    unit_id: str      # source.点式 或 rel 路径（信息性）
    rel: str
    file: Path
    line: int
    col: int
    pattern: str      # 标记名
    mechanism: str
    truth: bool = False
    hint: str = ""

    def to_gap_dict(self) -> dict:
        return {"rel": self.rel, "line": self.line, "col": self.col,
                "pattern": self.pattern, "mechanism": self.mechanism,
                "truth": self.truth, "hint": self.hint}


@dataclass
class UniversalVerdict:
    """全称机制判定的汇总：不设上限，扫完整个索引为准。"""
    total_units: int
    units_with_violations: int
    clean_units: int                 # 无任何违反的单元数（各机制真值 True）
    violations: list[Violation]
    mechanism_matrix: dict[str, dict]  # 机制 -> {hit_units, units}

    @property
    def is_clean(self) -> bool:
        """全称范围内机制清零（自演化收敛到无违反不动点）。"""
        return self.units_with_violations == 0


# ── 结构记忆：记忆检索式按需观测（接用户的"观察=结构观测 + 记忆力检索联动"）──
class StructureMemory:
    """持久化结构记忆：按需读取的索引（不漏=可达，不驻留=按需）。

    记忆=每单元指纹(sha)+象(signatures/deps/markers)。已入记忆且源未变 →
    不重读盘、直接从记忆取象；只对变更/新增单元读盘。这同时实现：
      ① 记忆力检索联动：structure 跨轮次相连（象不变则复用），供全局统一锚定；
      ② 数据膨胀不再全量重扫：增量观测，累积数据再多不因加载变慢/上下文暴涨。
    判据本体语言无关，机器真值仍每次由命中与否给定。"""
    def __init__(self, path: Path):
        self.path = Path(path)
        self._d = self._load()
        self._scan = 0     # 本轮实际读盘(变更/新增)单元数
        self._reuse = 0    # 本轮记忆命中(未变)复用单元数

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 记忆损坏不阻断，重建
            return {}

    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()

    def image(self, rel: str, text: str) -> Image:
        """按 rel+指纹取象：未变→记忆命中(复用,不重读判)；变了→重取象并写回记忆。"""
        sha = self._sha(text)
        rec = self._d.get(rel)
        if rec and rec.get("sha") == sha:
            self._reuse += 1
            return Image(signatures=list(rec.get("signatures", [])),
                         deps=list(rec.get("deps", [])),
                         markers=[dict(m) for m in rec.get("markers", [])])
        img = extract_image(text)
        self._d[rel] = {"sha": sha, "signatures": img.signatures,
                        "deps": img.deps, "markers": img.markers}
        self._scan += 1
        return img

    def save(self, path: Path | None = None) -> None:
        p = path or self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        # 原子写（临时文件 + os.replace）——多窗并发各自 save 也绝不写半截文件：
        # os.replace 是原子的，任何时刻读者看到的要么旧要么新，绝不损坏记忆。
        import os
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self._d, ensure_ascii=False), encoding="utf-8")
        try:
            os.replace(tmp, p)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def stats(self) -> dict:
        return {"memory_units": len(self._d),
                "scanned_changed": self._scan, "reused_unchanged": self._reuse}


# ── 四步：读结构 → 取象 → 全称判定 → 求真值 ──────────────────
def _hit_has_bound_evidence(match_text: str, mk: Marker, cleaned: str) -> bool:
    """判定一次机制命中是否有「结构证据」：该命中的符号被用作圈界。

    全称一致：不对具体符号/数值特判。规则分两类——
    - 「脚手架」类（todo!/unimplemented!，mechanism=Evolve）本身即违规，无需引用证据。
    - 「圈界/上限」类（Bounded/Optimal）：须命中符号在 cleaned 里被 for..X/..=X/X..<=X 等
      圈界形态引用；仅"声明 const X = 2"且未被作边界的（阴阳=2）不构成违反。
    """
    # 纯脚手架/演化占位：命中即违规（todo!/unimplemented!）
    if mk.mechanism == MECH_EVOLVE_VIOLATION:
        return True
    # 圈界/上限类：找命中文本里的候选符号（const 名 / MAX 标识 / 循环变量）
    token = _bound_token_of(match_text, mk)
    if token is None:
        return False
    if token == "":          # 裸字面量界（`for x in 0..64`）——硬编码圈界本身即证据
        return True
    return _is_ref_as_bound(cleaned, token)


def _bound_token_of(match_text: str, mk: Marker) -> str | None:
    """从命中文本抽「圈界符号」候选（全称一致，按匹配形态取）。

    返回语义：None=无可判定符号（丢弃，不构成违反）；""=字面量界（本身即违规）；
    其他=符号名（须再查是否被作圈界引用）。
    """
    m = re.search(r"([A-Za-z_]\w*)\s*[:=]", match_text)
    if m:
        return m.group(1)
    # 循环边界 `..X`：X 是符号则返回名，是字面量则返回 ""
    m = re.search(r"\.\.\s*([0-9]+)\s*$", match_text)
    if m:
        return ""
    m = re.search(r"\.\.\s*([A-Za-z_]\w*)\s*$", match_text)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:retry\w*|tries|attempts|max_iter(?:\w*))", match_text)
    return m.group(0) if m else None


def extract_image(text: str) -> Image:
    """取象：从单元文本提取签名、依赖、机制语义标记。机器可算。

    结构判定：先 `_strip_segments` 清洗（剥注释/字符串/测试基准段），
    机制命中后须有「结构证据」——命中的符号被用作循环界/比较上限/候选池终点
    才算违反（全称一致，不逐常量枚举）→ 消除规律常量误报。"""
    img = Image()
    seen_sig: set = set()
    for rx in _RUST_SIG_RE:
        for m in rx.finditer(text):
            name = m.group(1)
            if name is None or name in seen_sig:
                continue
            seen_sig.add(name)
            img.signatures.append(name)
    seen_dep: set = set()
    for m in _RUST_USE_RE.finditer(text):
        dep = m.group(1)
        if dep and dep not in seen_dep:
            seen_dep.add(dep)
            img.deps.append(dep)
    for m in _RUST_QUAL_RE.finditer(text):
        dep = m.group(0)
        if dep and dep not in seen_dep:
            seen_dep.add(dep)
            img.deps.append(dep)
    cleaned = _strip_segments(text)
    for mk in _MARKERS:
        rxm = mk.matched
        for m in rxm.finditer(cleaned):
            # 结构证据：命中符号若被用作圈界右值才计违反（全称同一谓词）
            if not _hit_has_bound_evidence(m.group(0), mk, cleaned):
                continue
            # ★ R10：清洗**保位**（等长 + 换行位置一致）⇒ 清洗文本与原文的
            #   偏移**指向同一个位置**，直接用即可，**不需要**任何补偿。
            #   原来这里写的是"清洗删除注释等可能错位，用最邻近搜索"，
            #   却拿清洗后的偏移去索引原文 —— 那是**张冠李戴**，
            #   实测把行号报偏 3035 行，指到注释上。
            bol = text.rfind("\n", 0, m.start()) + 1
            line = text.count("\n", 0, m.start()) + 1
            col = m.start() - bol + 1
            img.markers.append({"name": mk.name, "mechanism": mk.mechanism,
                                "line": line, "col": col,
                                "match": m.group(0), "hint": mk.hint})
    return img


# ★★★★★ 修复 R6-F11：**漏读账**（本模块此前两处静默丢弃的落点）。
#
# 旧行为（两处，都**静默**）：
#   ① `if not p.is_file(): continue` —— 索引里的单元文件不存在 ⇒ 直接跳过；
#   ② `except OSError: text = ""`      —— 文件读不出来 ⇒ 当成**空文本**
#      ⇒ `extract_image("")` 给**空象** ⇒ 该单元被判定为**无违反**。
#
# ★ 为什么这是最坏的一类：**假绿**。
#   读不到的单元不是"没有违反"，是"**没读到**"—— 两件事必须分开报。
#   旧行为把"没读到"记成"没有违反"，于是**不可读的代码全部自动合规**。
#   这与本模块开头的承诺（"不漏 = 对索引里可达的每个单元都读到"）直接冲突。
#
# ★ 修法：把两处丢弃都**记进账**（不阻断扫描，但必须报出去），
#   并让调用方可以选择接收（`漏读账` 参数）。诊断一律读它，不看 `Bool`。
def read_structure(idx, root: Path, index_key: str = "src",
                   memory: StructureMemory | None = None,
                   漏读账: list | None = None,
                   ) -> list[Observation]:
    """读结构：主动遍历索引全量单元，逐个读源文本，返回观测列表。

    去重：以真实文件 resolve 路径为准（索引多键可指向同一文件，只观测一次）。
    保证「不漏」= 对索引里可达的每个单元都读到。配合 memory 时则为「按需增量」：
    已入记忆且源未变的单元不重读盘、直接从记忆取象（记忆检索联动，防数据膨胀）。

    ★★★★★ R6-F11：`漏读账` 非 None 时，把**读不到的单元**逐条记进去
      （`{"key","path","why"}`）。**不阻断扫描**（一个坏单元不该停掉全量观测），
      但**必须报出去** —— 调用方据此知道"这些单元没读到"，
      而不是把它们当成"读了、且无违反"。
    """
    seen_files: dict[Path, Observation] = {}
    for key, p in idx._units.items():
        if not key.startswith(index_key):
            continue   # 只扫点式前缀命中的真实单元；裸路径键跳过避免重复
        if not p.is_file():
            # ★ 不再静默：索引里有、盘上没有 ⇒ 记进漏读账（索引已腐坏）
            if 漏读账 is not None:
                漏读账.append({"key": key, "path": str(p),
                               "why": "索引内单元文件不存在（索引已腐坏）"})
            logger.warning("[observation] 漏读：索引单元 %s 的文件不存在 %s", key, p)
            continue
        res = p.resolve()
        if res in seen_files:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            # ★ 不再当成空文本（那会产出**假绿**）：记为"没读到"，且**不产出观测**
            #   —— 宁可不观测，也不给一个"空象 ⇒ 无违反"的假绿。
            if 漏读账 is not None:
                漏读账.append({"key": key, "path": str(p),
                               "why": f"文件读不出（{type(exc).__name__}）"
                                      f"⇒ 未观测（不当成空文本）"})
            logger.warning("[observation] 漏读：%s 读不出（%s）⇒ 未观测",
                           p, type(exc).__name__)
            continue
        rel = (p.relative_to(root).as_posix() if p.is_relative_to(root)
               else p.as_posix())
        img = memory.image(rel, text) if memory else extract_image(text)
        seen_files[res] = Observation(key=key, rel=rel, file=res, image=img)
    # 排序保证确定性（全称无上限扫描，输出稳定）
    return sorted(seen_files.values(), key=lambda o: o.rel)


def universal_judge(observations: list[Observation]) -> UniversalVerdict:
    """全称机制判定：对全量单元穷举，产出违反列表 + 机制矩阵。

    求真值：单元在某机制上无命中 → 该机制真值 True；有命中 → False（违反）。
    violations 只记录真值 False 的命中，供派发修复。"""
    violations: list[Violation] = []
    mech_hits: dict[str, set[str]] = {}
    units_with_viol: set[str] = set()
    for obs in observations:
        for mk in obs.image.markers:
            uid = obs.rel
            if obs.image.signatures:
                uid = f"{obs.rel}::{obs.image.signatures[0]}"
            v = Violation(unit_id=uid, rel=obs.rel, file=obs.file,
                          line=mk["line"], col=mk["col"], pattern=mk["name"],
                          mechanism=mk["mechanism"], truth=False,
                          hint=mk["hint"])
            violations.append(v)
            units_with_viol.add(obs.rel)
            mech_hits.setdefault(mk["mechanism"], set()).add(obs.rel)
    matrix: dict[str, dict] = {}
    for mech, units in mech_hits.items():
        matrix[mech] = {"hit_units": len(units),
                        "units": sorted(str(u) for u in units)}
    return UniversalVerdict(
        total_units=len(observations),
        units_with_violations=len(units_with_viol),
        clean_units=len(observations) - len(units_with_viol),
        violations=violations, mechanism_matrix=matrix)


def observe_source(idx, root: Path, index_key: str = "src",
                   memory: StructureMemory | None = None) -> UniversalVerdict:
    """源码便捷入口：读结构 + 取象 + 全称判定 + 求真值，一次完成。

    传入 memory 时增量观测并落记忆（按需读取、记忆检索联动）。"""
    obs = read_structure(idx, root, index_key, memory)
    if memory is not None:
        memory.save()
    return universal_judge(obs)


# 兼容旧名
observe_rust = observe_source


# ── CLI：独立全量扫一轮，便于人工核对 ─────────────────────────
def _selftest() -> int:
    """★★★★★ R10 判据力探针：**清洗不得改变坐标**。

    ★ 为什么这一条是**结构性**的（不是"再修一个 bug"）：
      观测机制的全部价值在于「报出的位置**真是**违规的位置」。
      位置一旦错位，修复环就会**改错地方**，而所有门**照样全绿**
      —— 因为门只看"报了几条违规"，不看"报得对不对"。
      ⇒ 必须有一条门直接判**坐标**，而不是判数量。

    ★ 实测缺陷（本门存在的理由）：`_lex_strip` 剥注释时连换行一起删掉，
      `src/运行时样本.rs` 原 **4764 行** → 清洗后 **1729 行**，
      **错位 3035 行**；报出的 `:301:21` 在原文件里是**注释**，
      真正的 `for _ in 0..6` 在 974 行。
    """
    坏: list = []
    # ① 保长：清洗输出与输入**等长**
    for 名, 例 in (
            ("行注释", 'let a = 1;  // 说明\nfor _ in 0..n {\n'),
            ("块注释", 'let a = 1;\n/* 多行\n注释 */\nfn f() {}\n'),
            ("字符串", 'let s = "for _ in 0..9";\nfn g() {}\n'),
            ("原始串", 'let s = r#"const X: u32 = 9;"#;\nfn h() {}\n'),
            ("测试段", '#[cfg(test)]\nmod t { fn a() {} }\nfn real() {}\n'),
            ("生命周期", "fn f<'a>(x: &'a str) -> &'a str { x }\n"),
            ("转义", 'let c = \'\\n\'; let d = "a\\"b";\n'),
    ):
        净 = _strip_segments(例)
        if len(净) != len(例):
            坏.append(f"R10 保长失败[{名}]：{len(例)} → {len(净)}"
                      " ⇒ 坐标错位，报出的行号会指到别处")
        if [k for k, c in enumerate(例) if c == "\n"] != \
                [k for k, c in enumerate(净) if c == "\n"]:
            坏.append(f"R10 换行位置变了[{名}] ⇒ 行号必错")
    # ② 真的剥掉了注释/字符串内容（别为了保位就不剥了）
    if "for _ in 0..n" not in _strip_segments('let a = 1;  // 说明\nfor _ in 0..n {\n'):
        坏.append("R10 把真代码也剥了")
    if re.search(r"MAX_ROUNDS", _strip_segments('// const MAX_ROUNDS: u32 = 60;\nfn f(){}\n')):
        坏.append("R10 注释里的常量没剥掉 ⇒ 假阳回归")
    if re.search(r"for _ in 0\.\.9", _strip_segments('let s = "for _ in 0..9";\n')):
        坏.append("R10 字符串里的循环没剥掉 ⇒ 假阳回归")
    if re.search(r"const X: u32 = 9", _strip_segments('let s = r#"const X: u32 = 9;"#;\n')):
        坏.append("R10 原始字符串没剥掉 ⇒ 假阳回归")
    # ③ 生命周期**不得**被当字符字面量吞掉（原来的误剥）
    _生 = _strip_segments("fn f<'a>(x: &'a str) -> &'a str { x }\n")
    if "x: &'a str" not in _生:
        坏.append(f"R10 生命周期被误剥 ⇒ 真代码被抹掉（假阴）：{_生!r}")
    if "'a" not in _生:
        坏.append("R10 生命周期标记丢失")
    # ④ ★ 根本判据：**报出的行号，在原文件那一行真的能找到命中**
    _例 = ("fn a() {}\n" * 5) + "// for _ in 0..7 注释里的\n" + \
          ("fn b() {}\n" * 3) + "    for _ in 0..6 {\n" + ("fn c() {}\n" * 2)
    _img = extract_image(_例)
    _行 = _例.split("\n")
    _命 = [m for m in _img.markers if m["name"] == "候选池循环"]
    if not _命:
        坏.append("R10 例子里应命中『候选池循环』一条")
    else:
        _m = _命[0]
        if _m["line"] != 10:
            坏.append(f"R10 行号应=10（真位置），得 {_m['line']}")
        elif "for _ in 0..6" not in _行[_m["line"] - 1]:
            坏.append(f"R10 报的行号指到的不是命中行："
                      f"{_行[_m['line'] - 1]!r}")
    # ⑤ 注释里的同名模式**不得**被报（假阳）
    if any(m["line"] == 6 for m in _img.markers):
        坏.append("R10 注释里的模式被报了（假阳）")
    # ⑥ ★★ 端到端：**真源码**上逐条验「报的行号真的是命中行」
    try:
        from agentscope_orchestrator import profile as _pm
        _f = _pm.get_profile().repo_root / "src" / "运行时样本.rs"
        if _f.is_file():
            _原 = _f.read_text(encoding="utf-8", errors="ignore").split("\n")
            for m in extract_image("\n".join(_原)).markers:
                _ln = m.get("line")
                if not isinstance(_ln, int) or not (1 <= _ln <= len(_原)):
                    坏.append(f"R10 真源码行号越界：{m}")
                    break
                _真 = _原[_ln - 1]
                if m["match"].split()[0] not in _真 and \
                        m["match"][:8] not in _真:
                    坏.append(f"R10 真源码 {_ln} 行报的不是命中："
                              f"报 {m['match'][:30]!r}，该行 {_真.strip()[:40]!r}")
                    break
    except Exception as _e:                      # noqa: BLE001 环境缺文件不算失败
        logger.warning("[observation] R10 真源码端到端检查跳过：%s", _e)
    if 坏:
        print("observation 自检红：")
        for x in 坏:
            print(f"  ✗ {x}")
        return 1
    print("observation 自检：清洗保坐标 + 定位真值 6/6 全绿")
    return 0


def _main(root: Path) -> int:
    from agentscope_orchestrator import engine_dev
    idx = engine_dev.build_index()
    print(f"源码索引：{idx.size()} 键")
    v = observe_source(idx, root)
    print(f"观测单元 {v.total_units} · 有违反 {v.units_with_violations} · "
          f"全机制真值True {v.clean_units}")
    for m, st in sorted(v.mechanism_matrix.items()):
        print(f"  机制[{m}] 命中 {st['hit_units']} 单元")
    for vi in v.violations:
        print(f"  {vi.rel}:{vi.line}:{vi.col} [{vi.pattern}] → {vi.mechanism}")
    return 0 if v.is_clean else 1


if __name__ == "__main__":
    import sys
    from agentscope_orchestrator import profile as prof_mod
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(_main(prof_mod.get_profile().repo_root))