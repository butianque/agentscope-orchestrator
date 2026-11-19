"""符号表 —— O(1) 寻址 + 墓碑 + 指纹（结构寻址的"准"那一半）。

★ 它管什么、不管什么（与模型层的边界）
  管：**结构上必然相关**的东西 —— 这个名字定义在哪、谁引用了它、
      它现在的结构指纹是什么、哪些符号已经死了（墓碑）。
  不管：**哪些东西"有关"**（相关性、语义）—— 那需要常识与代码知识，交给模型（API）。
  本层零常识、零依赖、零阈值。

★ 三条不变量（都写成了自检）
  ① 查在 O(1)：建表后查询不读盘（自检用读盘计数证明，不用计时）；
  ② 成本 ∝ Δ：第二次同步无改动 ⇒ 重算 0、重扫描 0；
  ③ 墓碑不进活集：删掉的文件/符号标墓碑，查活集不得返回它。

★ 口径来源
  「字表只增」「墓碑」「出生或复用」的口径来自 示例工程 数据感知记忆网络
  （`结构记忆网络.rs` / 交接_总览 的五条目标 ③与⑤），此处按其判据重写，不复制源码。
"""

from __future__ import annotations
from .proj_ident import lean根们 as _proj_ident_lean根们

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .mem_code import 代码结构

# ★ R537c：`跳过` 为空时用的**保守默认跳过集**。
#   只收录「公认不是项目源码」的目录：产物、依赖、缓存、版本库内部。
#   ★ 刻意**不**收录 `.audit_trail`/`.orchestrator` 这类目录 ——
#     档案的 `memory:` 段完全可能把它们声明成知识源（本项目就是这么声明的），
#     默认跳掉它们等于"悄悄少索引一整个源"（口径 92）。
_默认跳过: tuple[str, ...] = (
    ".git", ".hg", ".svn",
    "target", "node_modules", "__pycache__",
    ".venv", "venv", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cargo_home",
)


def _枚举缓存秒() -> float:
    """枚举缓存的有效秒数（`ORCH_ENUM_CACHE_S`，默认 120；0 = 关掉缓存）。

    ★ 0 表示**每次重新枚举**（回到 R537d 之前的行为）——留给"我要绝对新鲜"
      的调用方，不必改代码。
    """
    try:
        return max(0.0, float(__import__("os").environ.get("ORCH_ENUM_CACHE_S", "120")))
    except Exception:                      # noqa: BLE001 环境值非法 ⇒ 用默认，不炸
        return 120.0


def 枚举源文件(根: str | Path, 后缀: tuple[str, ...] = (".py", ".rs", ".lean"),
               跳过: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
               glob阶: str = "**/*"):
    """按后缀枚举源文件，**可剪枝**。

    ★ 为什么需要（2026-10-31 交付审计）：`同步()` 原先只有 `sorted(根.glob(glob阶))`
      —— 它先把**整棵树**物化再按后缀过滤。在真实大仓上（`target/`、备份目录、
      `.tmp/` 各含上万文件）光枚举就几分钟，`maturity` 的 D/F 两条判据因此直接超时。
      剪枝走 `os.walk`，被跳过的目录**根本不进**枚举。
    跳过的匹配：目录名 == 条目，或目录名以条目开头（项目档案里 `target_` 就是前缀式写法）。
    """
    根 = Path(根)
    跳 = {str(s).replace("\\", "/").strip("/") for s in (跳过 or ()) if str(s).strip()}
    # ★★★★★ R537c：**跳过表为空时不能退回「物化整棵树」**。
    #   实测（2026-11-17，栈转储钉死）：`_结构包文本` 调 `表.同步(根)` 用的是
    #   默认 `跳过=frozenset()` ⇒ 命中下面那条 `sorted(根.glob("**/*"))` 分支
    #   ⇒ 对整个太虚元道（含 `.venv`、`target`）物化 + 排序，
    #   而该函数被上百个判据各调一次 ⇒ **CPU 烧几千秒、输出 20 分钟不增长**。
    #   ★ 荒唐处：这个函数**就是**为修这个病而写的（见本函数 docstring 第 35 行），
    #     而「跳过表为空」是它**最常见**的调用形态 —— 修好的那条路根本没被走到。
    #   ⇒ 空表时用这套**无歧义**的默认跳过（只跳公认的产物/依赖目录，
    #     不跳 `.audit_trail` 之类可能真被档案声明为源的隐藏目录）。
    if not 跳:
        跳 = set(_默认跳过)
    跳后缀 = {s if s.startswith(".") else "." + s for s in 后缀}
    # ★ ② 凡 `**/<单段模式>` 都能用剪枝 walk 复现 ⇒ 不再退回 glob。
    #   原条件 `glob阶 != "**/*"` 把 `**/*.rs` 也打回了全树 glob —— 同样慢。
    _模 = ""
    if glob阶.startswith("**/") and "/" not in glob阶[3:]:
        _模 = glob阶[3:]
    if not _模:
        for p in sorted(根.glob(glob阶)):
            if p.is_file() and p.suffix in 跳后缀:
                yield p
        return

    import fnmatch as _fn

    def _该跳(名: str) -> bool:
        return any(名 == s or 名.startswith(s) for s in 跳)

    for dirpath, dirnames, filenames in os.walk(根):
        dirnames[:] = sorted(d for d in dirnames if not _该跳(d))
        for fn in sorted(filenames):
            if Path(fn).suffix in 跳后缀 and (_模 == "*" or _fn.fnmatch(fn, _模)):
                yield Path(dirpath) / fn

# 定义抽取：行首声明（语言无关写法，各自一段）
_定义正则: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^[ \t]*(?:async[ \t]+)?(?P<kind>def|class)[ \t]+(?P<name>\w+)", re.M),
    "rust": re.compile(
        r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?(?:unsafe[ \t]+)?"
        r"(?P<kind>fn|struct|enum|trait|mod|const|static|type)[ \t]+(?P<name>\w+)",
        re.M),
    # ★★★ 2026-11-07：Lean 声明正则**全包唯一一份**（`project_graph` 从这里取）。
    #
    #   为什么会发现它不对：③「收录了却抽不出符号」3,969 个文件里，
    #   **761 个是 `.lean`**（13.6%）。摊开看：723 个是散文/记号（无符号是对的）、
    #   23 个只有 import、11 个空 —— **真缺陷只有 4 个**，而它们暴露了**两个**漏认：
    #
    #     ① `abbrev` 不在关键词表里
    #        （`_verify_ts495.lean` 3 个 abbrev · `sl_00_names.lean` 4 个 → 全抽不出）
    #     ② **修饰词一个都不认**：`noncomputable def bsdBazi …` 匹配不上
    #        （`BSDWalk.lean` 3 个 noncomputable def → 全抽不出）
    #
    #   ★ 而 `project_graph._LEAN声明` **恰好相反**：它认修饰词、有 `abbrev`，
    #     却**没有 `inductive`**。同一个语言、两套抽取器、**各缺一样**：
    #
    #         修饰词(private/protected/noncomputable/partial/unsafe/scoped/local)  abbrev  inductive
    #       mem_symbols    ❌ 一个不认                                                  ❌      ✅
    #       project_graph  ✅ 全认                                                      ✅      ❌
    #
    #     ⇒「同一个判断写在两处，必然给出两个答案」。这里把**并集**定为唯一一份，
    #       `project_graph` 改为 import 本项 —— 两边从此不可能再分叉。
    "lean": re.compile(
        r"^[ \t]*(?:@\[[^\]]*\][ \t]*)?"
        r"(?:(?:private|protected|noncomputable|partial|unsafe|scoped|local)[ \t]+)*"
        r"(?P<kind>theorem|lemma|def|abbrev|structure|class|inductive|axiom|instance)"
        # ★ R568：`«…»` 是 **Lean 的标识符引号**（口径 305），正则必须认它 ——
        #   否则 `theorem «含_eq_true_iff»` 这类声明**一个都抽不出来**（实测 U1b/U1c 全空）。
        r"[ \t]+(?P<name>«[^»\n]+»|[\w\u4e00-\u9fff'.]+)",
        re.M),
}
_标识符 = re.compile(r"[^\W\d]\w*", re.U)

# ★★★ R569：**抽取器版本**。抽取规则一改就 +1 —— 它进缓存键。
#   没有它的时候：「抽取器改了、文件内容没变」⇒ 同步按内容哈希跳过 ⇒ 修好的抽取器**永远不生效**。
#   实测（2026-11-18）：修好 `«…»` 引号标识符后整表重扫两轮，`U1b` 的 29 个符号一个都没进表，
#   而判据绿着 —— 因为"表里没有"被解释成"抽不出"（假绿的温床）。
抽取器版本 = 2
_语言后缀 = {".py": "python", ".rs": "rust", ".lean": "lean",
             ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript",
             ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript"}


@dataclass(frozen=True)
class 符号:
    名: str
    种类: str
    文件: str
    起: int                 # 1 起
    结构指纹: str           # mem_code 口径（改名不变、改结构变）

    def 身份(self) -> str:
        """身份 = 名 + 文件 + 结构指纹（不是行号：行号会随编辑漂移）。"""
        return f"{self.文件}::{self.名}::{self.结构指纹[:16]}"


@dataclass
class 同步读数:
    扫描文件: int = 0
    变更文件: int = 0
    重算符号: int = 0
    读盘次数: int = 0
    未变跳过: int = 0
    新增墓碑: int = 0

    def 行(self) -> str:
        return (f"扫描 {self.扫描文件} · 变更 {self.变更文件} · "
                f"重算符号 {self.重算符号} · 读盘 {self.读盘次数} · "
                f"戳筛跳过 {self.未变跳过} · 新增墓碑 {self.新增墓碑}")


def 抽取符号带口径(文本: str, 语言: str, 模块: str | None = None
                   ) -> tuple[list[tuple[str, str, int, str]], dict]:
    """④ **接线版**：`(符号们, 口径)`。

    ★ 两层各司其职，**不是互相替代**：
      · **elaborator 决定「哪些名字算数」**（`moduleData.constNames`）
      · **正则提供「行号与体文」**（elaborator 不给源码跨度，没 InfoTree）
    ★ 三件必须一起报：
      · `确证`  —— 正则也抽到、elaborator 也确证的
      · `正则独有` —— 源文本里有、elaborator 没确证 ⇒ **假阳候选，不许当确证**
      · `★确证但正则抽不到` —— **`_proof_`/`_private` 这类自动名**，
        源码里不以那个形式出现 ⇒ 起行 0、体文空、种类 `elaborator-only`。
        **这一格为空，就说明 elaborator 那条路其实没接上。**
    """
    符号们 = _正则抽取(文本, 语言)
    口径: dict = {"语言": 语言, "模块": 模块}
    if 语言 != "lean" or not 模块:
        口径["★来源"] = "**正则兜底**"
        口径["★为什么兜底"] = ("非 lean，或**没给模块名** ⇒ 归属对不上，"
                              "elaborator 那条路用不了")
        return 符号们, 口径
    try:
        from . import mem_axiom as _ma
        确 = _ma.模块确证名(模块)
    except Exception as _e:                      # noqa: BLE001
        确 = None
        口径["★导入出错"] = f"{type(_e).__name__}: {_e}"
    if not 确:
        口径["★来源"] = "**正则兜底**"
        口径["★为什么兜底"] = (f"elaborator 档里没有模块 `{模块}`"
                              f"（本仓聚合入口只覆盖 790/1,832）")
        return 符号们, 口径
    正则名 = {x[0] for x in 符号们}
    确证名 = {n.rsplit(".", 1)[-1] for n in 确}
    口径["★来源"] = "**elaborator**（`moduleData.constNames`）"
    口径["确证条数"] = len(确)
    口径["正则抽到的"] = len(正则名)
    口径["正则独有（**假阳候选，不许当确证**）"] = sorted(正则名 - 确证名)[:20]
    缺 = sorted(确证名 - 正则名)
    口径["★确证但正则抽不到"] = len(缺)
    口径["★确证但正则抽不到的样例"] = 缺[:12]
    出 = list(符号们)
    for n in 缺:
        出.append((n, "elaborator-only", 0, ""))
    口径["★若这一格为 0"] = ("说明 elaborator 那条路**其实没接上**"
                            "（正则也能抽到全部名字）")
    return 出, 口径


def 抽取符号(文本: str, 语言: str, 模块: str | None = None
            ) -> list[tuple[str, str, int, str]]:
    """原接口（向后兼容）—— 现在**默认就走在接线上**。

    ★ 不给 `模块` 时是**正则兜底**：`_lean` 的归属对不上，elaborator 用不了。
      要真拿到 elaborator 那一层，调用方得把**模块名**传进来。
    ★ 这个名字是我自己弄丢过一次的：改函数时把 `抽取符号` 整个换掉了，
      结果模块里**没有这个名字** ⇒ 判四十九 报
      `has no attribute '抽取符号'`。**改函数不许把旧名字换没了。**
    """
    return 抽取符号带口径(文本, 语言, 模块)[0]


def _正则抽取(文本: str, 语言: str) -> list[tuple[str, str, int, str]]:
    """纯正则抽取（**兜底**，也是 `抽取符号` 的实体）。"""

    """→ [(名, 种类, 起行 1 起, 体文)]。

    ★ 体文 = 本声明起、到**下一个声明**之间（含定义行、含函数体）。
      实测教训：早先只对"定义行"取指纹 ⇒ **改函数体测不出来**（形状仍判"未变"），
      跨轮记忆就变成了摆设。改体文后，改函数体必然判「形状已变」。
    ★ v1 限制：嵌套定义会被按"下一个声明"切开，方法体的切片偏粗 —— 已登记。
    """
    规则 = _定义正则.get(语言)
    if 规则 is None:
        return []
    行们 = 文本.splitlines(keepends=True)
    点: list[tuple[int, str, str]] = []
    for m in 规则.finditer(文本):
        # 引号只是引号 ⇒ 入库前剥掉（消费侧 `mem_axiom.规范名` 也剥，这里先给出干净名）
        名 = m.group("name").strip("«»").split(".")[0]
        if not 名:
            continue
        点.append((文本.count("\n", 0, m.start("name")) + 1, 名, m.group("kind")))
    出: list[tuple[str, str, int, str]] = []
    for i, (起行, 名, 种类) in enumerate(点):
        下一 = 点[i + 1][0] if i + 1 < len(点) else len(行们) + 1
        出.append((名, 种类, 起行, "".join(行们[起行 - 1:下一 - 1])))
    return 出


# ── ④ 接线：仓相对路径 → Lean 模块名 ────────────────────────────
# ★ R566：lean 根从 profile.memory 现算（R117 唯一取名点），不写死项目目录名。
#   取不到 ⇒ 空元组（调用方按"没有 lean 根"处理，**不许**退回写死值）。
_LIB_根 = tuple(_proj_ident_lean根们())


def 模块名(相对: str) -> str | None:
    """`.lean` 的仓相对路径 → Lean 模块名；不是 Lean 或在编不出来 ⇒ `None`。

    ★ 为什么要它：`抽取符号` 原来**按文件文本**抽，而 elaborator 索引
      **按模块**给 ⇒ **归属对不上，接线接不上**。
      `符号表._重算` 手里正好有仓相对路径，从这里换成模块名。

    ★ 编不出来就返回 `None` —— 调用方**必须**因此走「正则兜底」并且**明说**。
      **不许把编不出来的当成确证。**
    """
    p = 相对.replace("\\", "/")
    if not p.endswith(".lean"):
        return None
    部 = p.split("/")
    # ★★ 踩过一次：我假设 `相对` 是**仓相对**（含 `formal_proof` 那一段），
    #    但 `符号表.同步(根)` 给的是**相对那个根**的路径 ⇒ 路径里根本没有
    #    `formal_proof` ⇒ 全返回 `None` ⇒ 12,658 个文件**全走兜底**，
    #    `elaborator 文件数 = 0`。**又是「不读生产者就猜形状」。**
    #    实测两种形状都要认：仓相对（含 `formal_proof`）和根相对（就是模块路径）。
    起 = next((k for k, seg in enumerate(部) if seg == "formal_proof"), None)
    if 起 is not None:
        模块部 = 部[起 + 1:-1] + [部[-1][:-5]]
    else:
        # 根相对：整条路径就是模块路径（如 `MyMeta/Foundational/PureLogic.lean`）。
        # 认它的条件：**第一段得像 Lean 模块段**（字母/下划线开头），否则宁可不认
        #   —— 「编不出来」必须返回 `None`，**不许把编不出来的当确证**。
        首 = 部[0] if 部 else ""
        if not 首 or not (首[0].isalpha() or 首[0] == "_"):
            return None
        模块部 = 部[:-1] + [部[-1][:-5]]
    if not 模块部:
        return None
    # Lean 的引号段：以数字开头、或含非 ASCII ⇒ 模块名里要用 `«…»`
    #   （实测：`MyMeta.05_IntelligentLife.*` / `对外认证包.*` 必须加引号，
    #    否则 `import` 报 `unexpected character` / `expected token`）
    出 = []
    for seg in 模块部:
        if not seg or (seg[0].isdigit() or not seg.isascii()):
            出.append("«" + seg + "»")
        else:
            出.append(seg)
    return ".".join(出)


class 符号表:
    """活符号 + 墓碑 + 引用索引；全部增量维护。"""

    def __init__(self) -> None:
        self._活: dict[str, list[符号]] = {}          # 名 → 符号们（可能重名）
        self._引: dict[str, set[str]] = {}            # 名 → 引用它的文件们
        self._文件名: dict[str, set[str]] = {}        # 文件 → 它引用的名字们（反向索引）
        self._文哈希: dict[str, str] = {}             # 文件 → sha256
        self._抽取器版本: int = 0                      # 0 = 旧状态（未知）
        self._文戳: dict[str, tuple[int, int]] = {}   # 文件 → (mtime_ns, size)（快筛）
        self._文符号: dict[str, list[str]] = {}       # 文件 → 它定义的符号名
        self._墓碑: set[str] = set()                  # 文件（已删/不可读）
        self._读盘 = 0
        self.根: Path | None = None
        # ★ R537d：枚举缓存（(时刻, 键, 候选列表)）；见 `同步` 的说明。
        self._枚举缓存: tuple[float, tuple, list] | None = None

    # ── 同步（增量：只碰哈希变了的文件）───────────────────────
    def 同步(self, 根: str | Path, 后缀: tuple[str, ...] = (".py", ".rs", ".lean"),
             glob阶: str = "**/*", 跳过: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
             候选=None, 全量: bool = False, 用缓存: bool = True
             ) -> 同步读数:
        """★ 2026-11-07（R303）：两条改动，都是为了**常驻 + 不遍历整仓**。

        ① **`候选` 可显式给** —— 用于**多根一次同步**。
           档案 `memory:` 段声明了多个根（`src` / `.audit_trail` / `receipts` …），
           而下面的墓碑逻辑要求 `现存` 是**全部声明合起来**的集合：
           分几次调 `同步` 的话，第二次会把第一次的文件**全标成墓碑**。
           ⇒ 必须一次算完。`候选` 收 `(路径, 相对名)` 对，相对名由调用方定
           （这样多根可以共用一个命名空间，路径仍精确到具体目录）。

        ② **戳筛**：先用 `(mtime_ns, size)` 挡一道，戳没变就**不读盘**。
           原来每个候选文件都要 `_读()` 进来算 sha256 ⇒ 大仓上「增量」
           只省了**重算**，**没省读盘**，于是每轮仍要遍历整仓。
           戳筛之后未变文件**一次盘都不读**。

        ★ 诚实边界：戳筛把「mtime_ns 与 size 都没变」当作「内容没变」——
          能骗过它的只有**刻意回填 mtime 且字节数恰好相同**的改写
          （构建工具如 make/cargo 用的就是这个判据）。
          要绝对保险就 `全量=True`，那就回到逐文件读盘算 sha256。

        ★★★ R562（2026-11-18）**新增 `用缓存=False`** —— 起因是一次实测：
          同一个表对同一个根同步两次，中间新建 `src/新来的.rs`，**第二次没补上**
          （改内容看得见，**新建看不见**）。这就是 R537d 枚举缓存的有界取舍，
          但它比注释里写的更严重：**一切"表里没有"的判断都可能是假的**
          （`mem_anchor._自检` 就是因此红的：表名 7 个，缺了后建的 3 个）。
          ⇒ 判**存在性/完整性**的调用方（自检、验收工具）必须 `用缓存=False`：
            只多走一次 `枚举源文件`，**不**读盘算 sha256，比 `全量=True` 便宜得多。
        """
        根 = Path(根)
        self.根 = 根
        读 = 同步读数()
        # ★★★ R569：**抽取器版本闸**。抽取规则改了而文件内容没变时，内容哈希不变 ⇒
        #   同步会全部跳过 ⇒ **修好的抽取器永远不生效**（实测：`U1b` 的 29 个符号两轮重扫都没进表）。
        #   版本不符 ⇒ 清空哈希/戳、强制全量（绕过枚举缓存）。
        if getattr(self, "_抽取器版本", 0) != 抽取器版本:
            self._文哈希.clear()
            if hasattr(self, "_文戳"):
                self._文戳.clear()
            self._枚举缓存 = None
            self._抽取器版本 = 抽取器版本
            全量 = True
        现存: set[str] = set()
        if 候选 is None:
            # ★★★★★ R537d：**同根短时间内不重复走全盘**。
            #   实测（2026-11-17）：`枚举源文件(太虚元道)` 走一遍 **33 秒 / 19.9 万文件**
            #   （R537c 之前是 180 秒还没走完）。而 `_produce_window` **每个判据都调
            #   一次** `表.同步(根)` ⇒ 光是枚举就把自检拖成几小时（口径 390）。
            #   ⇒ 缓存键 = (根, 后缀, glob阶, 跳过的冻结集)；过了 `ORCH_ENUM_CACHE_S`
            #     秒就重新枚举。
            #   ★ 诚实边界：缓存期内**新增**的文件看不见（**改动**的文件仍靠戳筛发现）
            #     —— 这是"新鲜度 vs 可用性"的有界取舍；要绝对新鲜用 `全量=True`
            #     （那条路绕过缓存）。
            _键 = (str(根), tuple(后缀), glob阶,
                   tuple(sorted(str(s) for s in (跳过 or ()))))
            _现 = __import__("time").monotonic()
            _缓 = self._枚举缓存
            if (not 全量 and 用缓存 and _缓 is not None and _缓[1] == _键
                    and _现 - _缓[0] < _枚举缓存秒()):
                候选 = _缓[2]
            else:
                候选 = [(路径, str(路径.relative_to(根)).replace("\\", "/"))
                        for 路径 in 枚举源文件(根, 后缀, 跳过, glob阶)]
                self._枚举缓存 = (_现, _键, 候选)
        for 路径, 相对 in 候选:
            读.扫描文件 += 1
            # ★ R569e：**全量也要取戳**。原来 `全量=True` 时戳恒为 None ⇒
            #   重抽完 `_文戳` 是空的 ⇒ 下一次同步又把全部文件当成「变了」（实测 481s/次）。
            #   取戳是 stat（便宜），哈希才是读盘（贵）；跳过判断仍只在不全量时生效。
            戳 = self._取戳(路径)
            # ★★★★★ R537e：**「现存」只收真正在盘上的文件**。
            #   为什么必须把 `现存.add` 挪到这儿：R537d 给枚举加了缓存之后，
            #   缓存期内的候选列表会**仍然列着已删掉的文件** ——
            #   原来无条件 `现存.add(相对)` ⇒ 已删文件被当成"还在" ⇒
            #   **墓碑永远标不出来**（`mem_symbols._自检` 当场抓到：
            #   「删文件记墓碑：墓碑 ()」「墓碑符号不在活集：实得 (符号(名='丙'…))」）。
            #   ⇒ 取不到戳 = stat 失败 = 多半已删 ⇒ 不进「现存」，
            #     下面那段墓碑逻辑自然会摘掉它。这同时让缓存与墓碑两全。
            if not 全量 and 戳 is None:
                continue
            现存.add(相对)
            if 戳 is not None and self._文戳.get(相对) == 戳:
                读.未变跳过 += 1
                continue                                    # ② 戳没变 ⇒ **不读盘**
            文本 = self._读(路径)
            if 文本 is None:
                现存.discard(相对)                          # 读不到 ⇒ 同样不算现存
                continue
            读.读盘次数 += 1
            哈 = hashlib.sha256(文本.encode("utf-8", "replace")).hexdigest()
            if self._文哈希.get(相对) == 哈:
                if 戳 is not None:
                    self._文戳[相对] = 戳
                continue                                    # ③ 内容未变 ⇒ 不重算
            读.变更文件 += 1
            self._文哈希[相对] = 哈
            if 戳 is not None:
                self._文戳[相对] = 戳
            self._墓碑.discard(相对)
            self._重算(相对, 文本)
        # 墓碑：文件消失 ⇒ 标墓碑，并从活集摘除
        for 相对 in list(self._文哈希):
            if 相对 not in 现存:
                读.新增墓碑 += 1
                self._标墓碑(相对)
        return 读

    def _取戳(self, 路径: Path) -> tuple[int, int] | None:
        try:
            st = 路径.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _读(self, 路径: Path) -> str | None:
        # ★ 唯一的读盘点，也是**唯一**该自增计数器的地方。
        #   原来这个计数器建了却从没自增 ⇒ 「查询零读盘」那条判据恒真
        #   （拿一个没人碰的快照比自己）。计数器是死的时候，
        #   任何"零变化"的断言都不证明任何事。
        self._读盘 += 1
        try:
            return 路径.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def 读盘次数(self) -> int:
        """本表自建立以来**真的**读了几次盘 —— 给"查询零读盘"当读数用。"""
        return self._读盘

    # ★ ④ 的真读数：**有多少文件真的走了 elaborator**、多了多少名字。
    #   「接线」必须能在这个数上看见 —— 看不见就等于没接。
    _elab文件: int = 0
    _elab独有: int = 0
    _兜底文件: int = 0

    def 口径(self) -> dict:
        """④ 的读数：`.lean` 里多少走了 elaborator、多少只能兜底。"""
        return {"elaborator 文件数": self._elab文件,
                "正则兜底 文件数": self._兜底文件,
                "★elaborator 独有符号数": self._elab独有,
                "★读法": "若 `elaborator 文件数` 为 0，说明接线**没接上**"
                        "（产品主流仍在走正则兜底）。"}

    def _重算(self, 相对: str, 文本: str) -> None:
        for 名 in self._文符号.get(相对, []):
            self._活[名] = [s for s in self._活.get(名, []) if s.文件 != 相对]
            if not self._活[名]:
                del self._活[名]
        语言 = _语言后缀.get(Path(相对).suffix, "generic")
        名们: list[str] = []
        # ★ ④：.lean 走**接线版**（elaborator 定「哪些名字算数」）
        模 = 模块名(相对) if 语言 == "lean" else None
        if 语言 == "lean":
            符们, 口 = 抽取符号带口径(文本, 语言, 模)
            if "elaborator" in str(口.get("★来源", "")):
                self._elab文件 += 1
                self._elab独有 += int(口.get("★确证但正则抽不到") or 0)
            else:
                self._兜底文件 += 1
        else:
            符们 = 抽取符号(文本, 语言)
        for 名, 种类, 起行, 体 in 符们:
            符 = 符号(名, 种类, 相对, 起行, 代码结构(体, 语言).指纹())
            self._活.setdefault(名, []).append(符)
            名们.append(名)
        self._文符号[相对] = 名们
        # 引用索引：文件里出现过的所有标识符（除它自己的定义行不做区分，宁多不漏）
        # ★ 2026-11-07（R303）修 —— 这里原来是**平方级**：
        #      for 名 in list(self._引):          # 遍历**全表**所有引用名
        #          if 相对 in self._引[名]: ...
        #   每重算一个文件就扫一遍全表的引用名 ⇒ O(总引用名 × 文件数)。
        #   实测症状：真仓上 `自动同步` 20 分钟不返回（`.venv` 38k 文件那条已剪枝后仍然如此）。
        #   改法：**旧名字集合自己就记着这个文件贡献过哪些名字**（`_文件名[相对]`），
        #   按它删即可 ⇒ O(本文件的名字数)。**必须先用旧值再覆盖** ——
        #   原来把 `_文件名[相对]` 先赋新值，旧值就丢了，所以只能退回去扫全表。
        旧名 = self._文件名.get(相对, set())
        for 名 in 旧名:
            s = self._引.get(名)
            if s is not None:
                s.discard(相对)
                if not s:
                    del self._引[名]
        新名 = set(_标识符.findall(文本))
        self._文件名[相对] = 新名
        for 名 in 新名:
            self._引.setdefault(名, set()).add(相对)

    def _标墓碑(self, 相对: str) -> None:
        for 名 in self._文符号.pop(相对, []):
            活 = [s for s in self._活.get(名, []) if s.文件 != 相对]
            if 活:
                self._活[名] = 活
            else:
                self._活.pop(名, None)
        self._文哈希.pop(相对, None)
        self._文戳.pop(相对, None)
        # ★ 2026-11-07（R303）修 —— 同 `_重算` 的平方级毛病：
        #   原来 `self._文件名.pop(相对)` 之后又 `for 名 in list(self._引)` 扫全表。
        #   改成**先拿旧名字集合**再删，逐个精确摘除 ⇒ O(本文件的名字数)。
        旧名 = self._文件名.pop(相对, set())
        for 名 in 旧名:
            s = self._引.get(名)
            if s is not None:
                s.discard(相对)
                if not s:
                    del self._引[名]
        self._墓碑.add(相对)

    # ── 查询（① 不读盘）────────────────────────────────────
    def 查(self, 名: str) -> tuple[符号, ...]:
        return tuple(self._活.get(名, ()))

    def 查_文件(self, 文件: str) -> tuple[符号, ...]:
        """某文件里定义的活符号（墓碑文件返回空）。"""
        return tuple(s for 名 in self._文符号.get(文件, ())
                     for s in self._活.get(名, ()) if s.文件 == 文件)

    def 调用者(self, 名: str, 排除自身: bool = True) -> tuple[str, ...]:
        定义文件 = {s.文件 for s in self.查(名)}
        引用 = set(self._引.get(名, set()))
        if 排除自身:
            引用 -= 定义文件
        return tuple(sorted(引用))

    def 墓碑们(self) -> tuple[str, ...]:
        return tuple(sorted(self._墓碑))

    def 引用名(self, 文件: str) -> tuple[str, ...]:
        """该文件里出现过的标识符（反向索引；供"被调用者"通道用）。"""
        return tuple(sorted(self._文件名.get(文件, ())))

    def 规模(self) -> dict[str, int]:
        return {"符号名": len(self._活),
                "符号条": sum(len(v) for v in self._活.values()),
                "引用名": len(self._引),
                "文件": len(self._文哈希),
                "戳": len(self._文戳),
                "墓碑": len(self._墓碑)}

    # ── 常驻化：状态出/入（供 mem_live 落盘，让增量跨进程存活）──────
    def 导出(self) -> dict:
        """把**全部**内部状态取出来（含 `_文戳`）。

        ★ 为什么必须含 `_文戳`：不带它落盘，下次进程起来戳筛必然全miss
          ⇒ 又要读全仓 ⇒ 「常驻」只省了重算，等于没省。
        """
        return {"_活": self._活, "_引": self._引, "_文件名": self._文件名,
                "_文哈希": self._文哈希, "_文戳": self._文戳, "_抽取器版本": self._抽取器版本,
                "_文符号": self._文符号, "_墓碑": self._墓碑, "_读盘": self._读盘}

    def 载入(self, 状: dict) -> None:
        self._活 = 状.get("_活", {})
        self._引 = 状.get("_引", {})
        self._文件名 = 状.get("_文件名", {})
        self._文哈希 = 状.get("_文哈希", {})
        self._抽取器版本 = int(状.get("_抽取器版本", 0) or 0)
        self._文戳 = 状.get("_文戳", {})
        self._文符号 = 状.get("_文符号", {})
        self._墓碑 = set(状.get("_墓碑", ()))
        self._读盘 = int(状.get("_读盘", 0))


# ════════════════════════════════════════════════════════════
# 自检：三条不变量，全部结构性（无计时断言）
# ════════════════════════════════════════════════════════════

def _自检() -> int:
    import tempfile

    失败: list[str] = []

    def 判(名: str, 条件: bool, 说明: str = "") -> None:
        if not 条件:
            失败.append(f"{名}{('：' + 说明) if 说明 else ''}")

    with tempfile.TemporaryDirectory() as td:
        根 = Path(td)
        (根 / "a.py").write_text("def 甲(x):\n    return x + 1\n\ndef 乙(x):\n    return 甲(x)\n",
                                encoding="utf-8")
        (根 / "b.py").write_text("from a import 甲\n\ndef 丙(y):\n    return 甲(y)\n",
                                encoding="utf-8")
        表 = 符号表()
        读1 = 表.同步(根)
        判("首次同步有变更", 读1.变更文件 == 2, 读1.行())
        判("能查到符号", bool(表.查("甲")), f"规模 {表.规模()}")
        判("跨文件调用者", set(表.调用者("甲")) == {"b.py"}, f"实得 {表.调用者('甲')}")

        # ① O(1)：查询不读盘（读盘计数是**表级**结构量，不是计时）
        #
        # ★ 这里原来是一条**恒真门**：`前 = 读1.读盘次数` 之后拿
        #   `读1.读盘次数 == 前` 当判据 —— 而 `读1` 是同步返回的快照，
        #   中间没人碰它 ⇒ 查询读不读盘它都不会变，**永远绿**。
        #   现在改成读**表自己的**计数器、取查询前后的差；并且紧跟一条
        #   **正控**：跑一次同步，读数必须变大 —— 不然计数器是死的，
        #   那条 0 差就什么都证明不了。
        前 = 表.读盘次数()
        表.查("甲")
        表.调用者("甲")
        判("查询零读盘", 表.读盘次数() == 前,
           f"{前} → {表.读盘次数()}（查询把盘读了，O(1) 不再成立）")
        # ★ 2026-11-07（R303）：正控拆成**两条互补的**判据。
        #   原来只有一条「跑一次同步读数必须变大」，触发条件用的是「无改动再同步」。
        #   加了戳筛之后**无改动本来就该 0 读盘**（这正是新功能），
        #   于是这条正控的**触发**过时了 —— 但它的**意图**必须留着：
        #   计数器是死的时候，「查询零读盘」恒真、什么都不证明。
        #   ⇒ 改成：无改动那条证明**戳筛生效**，`全量=True` 那条证明**计数器是活的**。
        表.同步(根)
        判("无改动走戳筛（不读盘）", 表.读盘次数() == 前,
           f"无改动却读了盘：{前} → {表.读盘次数()}（戳筛没接上）")
        表.同步(根, 全量=True)
        判("读盘计数是活的（正控）", 表.读盘次数() > 前,
           f"`全量=True` 后仍是 {表.读盘次数()}（= 查询前 {前}）⇒ 计数器没接线，"
           f"上面那条「查询零读盘」等于没判")

        # ② 成本 ∝ Δ：无改动 ⇒ 变更 0、重算 0
        读2 = 表.同步(根)
        判("无改动零重算", 读2.变更文件 == 0 and 读2.重算符号 == 0, 读2.行())

        # ②′ 只改一个文件 ⇒ 变更恰为 1
        (根 / "b.py").write_text("from a import 甲\n\ndef 丙(y):\n    return 甲(y) + 1\n",
                                 encoding="utf-8")
        读3 = 表.同步(根)
        判("改一个文件只动一个", 读3.变更文件 == 1, 读3.行())

        # ③ 墓碑：删文件 ⇒ 它的独占符号进墓碑，不进活集
        (根 / "b.py").unlink()
        读4 = 表.同步(根)
        判("删文件记墓碑", "b.py" in 表.墓碑们(), f"墓碑 {表.墓碑们()}")
        判("墓碑符号不在活集", not 表.查("丙"), f"实得 {表.查('丙')}")
        判("他文件的符号仍在", bool(表.查("甲")))

        # 改名 vs 结构：形状同、名不同 ⇒ 指纹同
        from .mem_code import 代码结构
        判("同名形状指纹同",
           代码结构("def 甲(x):\n    return x + 1", "python").指纹()
           == 代码结构("def 乙(x):\n    return x + 1", "python").指纹())

    for f in 失败:
        print(f"  ✗ {f}")
    if not 失败:
        print("  ✓ 符号表自检全绿（O(1) 查询 · 增量 ∝ Δ · 墓碑不进活集）")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(_自检())


# ★ 跨语言结构记忆（阶段 5）：TypeScript / JavaScript 抽取规则。
#   必须放在 `_定义正则` 定义之后——我第一次写成 `_定`（不存在的名字）⇒ 运行时 NameError。
#   教训：改别人文件前先确认**真实标识符**，别按记忆里的缩写写。
_定义正则["typescript"] = re.compile(
    r"^[ \t]*(?:export[ \t]+)?(?:default[ \t]+)?(?:declare[ \t]+)?"
    r"(?P<kind>abstract[ \t]+class|function|class|interface|type|const|let|var|enum|namespace)"
    r"[ \t]+(?P<name>[A-Za-z_$][\w$]*)", re.M)
_定义正则["javascript"] = _定义正则["typescript"]
