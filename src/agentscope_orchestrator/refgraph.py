r"""引用图：**从项目文件自己长出"边"**（通用机制，不含任何项目专有名词）。

## 为什么需要它（上一轮量出来的缺口）

记忆网络只有**点**（单元 → 文件），没有**边**。于是「从一个概念拉出整个代码 + 证明网络」
只能给出一张**文件清单**，给不出"这些文件之间怎么连"。

而边其实**就在项目文件里**，只是没人解析：

    实测（本项目仓，975 个引擎源文件 + 12398 个活跃证明文件）：
      · 128 个 Rust 文件在注释里点名了 Lean 文件名，共 **426 条点边**，
        涉及 235 个不同 Lean 名 —— 但**一条都没进过图**；
      · 其中 **41 个名字是真死引用**（活跃树和 `_archive` 都没有）。
        ⇒ 人手写的引用会烂，而没人查就一直是绿的。

## 为什么必须是机制，不是按项目手写

按项目手写（"扫 src/*.rs 里的 .lean"）会**换项目就废**，违反「换项目 = 换档案」。
所以这里**一个字都不写死**：

| 需要知道的事 | 从哪来 |
|---|---|
| 扫哪些文件、分哪几类 | 项目档案的 `memory:` 段（`entries`：kind / roots / globs） |
| 哪些扩展名算"文件引用" | **从项目实际拥有的扩展名集合里统计**（不写死 `.lean`/`.rs`） |
| 哪些标识符算"符号引用" | 记忆索引里**别的文件已声明的单元键**（点层现成的东西） |
| 边的类型（代码→证明 等） | 两端的 `kind`（同样是档案给的） |

⇒ 换成任何项目、任何语言，只要它在档案里声明了自己的文件，这个机制照样建图。

## 两类边（都语言无关）

1. **文件引用边**：文件 A 的文本里出现 `某名字.某扩展名`，且该扩展名是**本项目拥有的**，
   且能按文件名解析到项目内某个真实文件 ⇒ A ──引用──▶ B。
   解不出的**单独记**成 `死引用`（**不许静默丢掉**，那是可报告的缺陷）。
2. **符号引用边**：文件 A 的文本里出现一个标识符，而这个标识符是**文件 B 里声明的单元键
   的末段** ⇒ A ──用到──▶ B。键来自索引，所以不需要任何语言知识。

## 边界（如实）

- 符号边目前只认 **ASCII 标识符（≥4 字符）**：中文符号名（本项目里真有，
  如 `量化可行性_有限步达不动点`）需要中文分词才能可靠切出，本版**没做**。
- 同名文件（多个 `mod.rs`）按"路径最短 + 记录歧义"处理，不猜。
- 图是**静态快照**：跑一次按指纹缓存；文件变了指纹变 ⇒ 自动重建（判据里钉住这条）。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

# 形如 `名字.扩展名`（扩展名稍后按"项目实际拥有的扩展名集合"过滤，不在此写死）
# ★ 不许含空格：实测第一版允许空格，把散文「写 _A线_体检读数.txt」也当引用（死引用 4996 条里
#   大半是这种），噪声把真引用埋了。**引用是"一个名字"，不是"一句话的尾巴"。**
_文件名引用 = re.compile(r"(?<![\w.])([\w\u4e00-\u9fff][\w\u4e00-\u9fff.\-]{0,60}?"
                     r"\.([A-Za-z][A-Za-z0-9]{0,5}))(?![\w])")
# ASCII 标识符（符号边的候选）
_标识 = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_缓存名 = "refgraph.json"
_缓存子目录 = ".orchestrator"          # 放**项目级**（跟运行状态根无关），问答期才能读到
_内存: dict[str, "引用图"] = {}


class 引用图:
    def __init__(self) -> None:
        self.边: list[tuple[str, str, str]] = []      # (源, 目标, 类型)
        self.死引用: list[tuple[str, str]] = []        # (源, 点不到的名字)
        self.歧义: list[tuple[str, str, int]] = []     # (源, 名字, 候选数)
        self.节点: dict[str, str] = {}                 # 路径 -> kind
        self.统计: dict[str, int] = {}

    def 邻居(self, 路径: str) -> list[tuple[str, str]]:
        """一跳邻居：[(目标路径, 类型)]（有向出边）。"""
        return [(b, t) for a, b, t in self.边 if a == 路径]

    def 网络(self, 起点们: list[str], 最大跳: int = 2) -> list[str]:
        """从若干起点沿边扩散（默认 2 跳）—— 这就是"从一个概念拉出网络"。"""
        见 = set(起点们)
        层 = list(起点们)
        for _ in range(max(1, 最大跳)):
            下: list[str] = []
            for p in 层:
                for q, _t in self.邻居(p):
                    if q not in 见:
                        见.add(q)
                        下.append(q)
            层 = 下
            if not 层:
                break
        return sorted(见 - set(起点们))

    def 存(self, 缓存: Path) -> None:
        try:
            缓存.parent.mkdir(parents=True, exist_ok=True)
            缓存.write_text(json.dumps({
                "边": [list(t) for t in self.边],
                "死引用": [list(t) for t in self.死引用],
                "歧义": [list(t) for t in self.歧义],
                "节点": self.节点, "统计": self.统计,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _指纹(路径们: list[Path]) -> str:
    h = hashlib.sha256()
    for p in 路径们:
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p}|{st.st_mtime_ns}|{st.st_size}".encode())
    return h.hexdigest()


def _收文件(entries: list[dict], 根: Path) -> dict[str, list[Path]]:
    """按**档案声明**收文件：{kind: [路径...]}。不写死任何目录名。"""
    出: dict[str, list[Path]] = defaultdict(list)
    for e in entries:
        kind = str(e.get("kind") or "file")
        for r in e.get("roots") or []:
            根路 = (根 / r).resolve()
            if not 根路.is_dir():
                continue
            for g in e.get("globs") or ["**/*"]:
                for p in 根路.glob(g):
                    if p.is_file():
                        出[kind].append(p.resolve())
    # 去重（一个文件可能被多条声明命中）
    return {k: sorted(set(v)) for k, v in 出.items()}


# 常规垃圾目录：跨项目通用，不是"这个项目的名词"
_垃圾目录 = {".git", ".venv", "node_modules", "target", "__pycache__", "wheels",
             ".mypy_cache", ".pytest_cache", ".tmp", "dist"}


def _名字表(根: Path, entries: list[dict],
           索引路径们: list[Path]) -> dict[str, list[Path]]:
    """建 {文件名: [路径]} —— 三个**都不靠猜**的来源：

    ① 声明文件：按档案的 roots × globs 取（**不 walk**，glob 自己决定递归与否）；
    ② 记忆索引里的单元路径（形式化单元常常没在 `memory:` 段声明，但索引里有）；
    ③ 仓根那一层（`build.rs` / `Cargo.toml` 这类入口文件）。

    ★ 为什么必须问文件系统而不只信索引（本轮实测）：第一版拿索引当"全世界"，
      `CLAUDE.md`（在 src/）、`build.rs`（在仓根）、`A线拼装.rs`（在 src/bin/）
      全被判成「死引用」—— **它们都在仓里，只是没进索引**。又一次「看不见 ≠ 没有」。
    ★ 为什么又不能 walk 整仓（同轮实测）：`doc` 的根是 `.`（不递归），我按"根"去递归扫，
      等于又把 20 万个构建产物走了一遍，一次建图 **433 秒**。⇒ **尊重 glob 的递归语义**。
    """
    出: dict[str, list[Path]] = defaultdict(list)
    for kind, 列 in _收文件(entries, 根).items():
        for p in 列:
            出[p.name.lower()].append(p)
    for p in 索引路径们:
        出[p.name.lower()].append(p)
    for p in 根.glob("*"):
        if p.is_file():
            出[p.name.lower()].append(p)
    return {k: sorted({str(x): x for x in v}.values()) for k, v in 出.items()}


def 建图(entries: list[dict], 根: Path, *, 上限文件: int = 20000,
         用索引: bool = True) -> 引用图:
    """从项目档案声明的文件里建引用图（文件引用边 + 符号引用边）。"""
    图 = 引用图()
    分类 = _收文件(entries, 根)
    文件们: list[Path] = []
    for kind, 列 in 分类.items():
        for p in 列:
            图.节点[str(p)] = kind
            文件们.append(p)
    # ★ 再并上**记忆索引里的全部单元路径**：形式化单元（如 Lean）可能不在档案的 memory 段里
    #   声明，但索引已经收了 4 万多个 —— 图必须看得见它们，否则"代码↔证明"的边永远建不出来。
    try:
        if not 用索引:
            raise RuntimeError("判据要求不读索引")
        from .autoengine import 项目索引 as _索
        _mem = _索()
        for _kind in _mem.kinds():
            _idx = _mem.index(_kind)
            for _k in _idx.keys():
                _p = _idx.query(_k)
                if _p is not None and _p.is_file():
                    _p = _p.resolve()
                    # ★ 只收**本次给定根之下**的：否则拿同一份机制去建"别的项目"的图时，
                    #   它会把当前项目的索引也读进来（实测：假项目建出 39318 条真项目的边，
                    #   通用性判据当场变红）。**机制不许有"隐式全局状态"。**
                    try:
                        _p.relative_to(根.resolve())
                    except ValueError:
                        continue
                    if str(_p) not in 图.节点:
                        图.节点[str(_p)] = _kind
                        文件们.append(_p)
    except Exception:                                # noqa: BLE001
        pass
    文件们 = sorted({str(p): p for p in 文件们}.values())
    文件们 = 文件们[:上限文件]
    # ① 扩展名集合：**从项目实际拥有的文件里统计**（不写死）
    扩展 = {p.suffix.lower().lstrip(".") for p in 文件们}
    扩展 = {e for e in 扩展 if e and len(e) <= 6 and e.isalnum()}
    # ② 文件名表（同名记多处 ⇒ 解析时按路径最短，并记歧义）
    名表 = _名字表(根, entries, 文件们)   # ★ 解析用**文件系统 + 索引**，不靠猜、也不全仓 walk
    # ③ 符号表：**别的文件已声明的单元键末段**（键来自记忆索引，不含语言知识）
    符号: dict[str, list[Path]] = defaultdict(list)
    try:
        if not 用索引:
            raise RuntimeError("判据要求不读索引")
        from .autoengine import 项目索引
        mem = 项目索引()
        for kind in mem.kinds():
            idx = mem.index(kind)
            for k in idx.keys():
                p = idx.query(k)
                if p is None or not p.is_file():
                    continue
                末 = str(k).rsplit(".", 1)[-1]
                if len(末) >= 4 and 末.isascii():
                    符号[末].append(p.resolve())
    except Exception:                                # noqa: BLE001 没有索引就只建文件边
        符号 = defaultdict(list)
    # ④ 逐文件抽引用
    死: set[tuple[str, str]] = set()
    for p in 文件们:
        try:
            文 = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        源 = str(p)
        已加: set[tuple[str, str, str]] = set()
        已歧: set[tuple[str, str, int]] = set()
        for m in _文件名引用.finditer(文):
            名, 扩 = m.group(1), m.group(2).lower()
            if 扩 not in 扩展:
                continue
            候选 = 名表.get(名.lower()) or []
            候选 = [q for q in 候选 if q != p]
            if not 候选:
                死.add((源, 名))
                continue
            if len(候选) > 1 and (源, 名, len(候选)) not in 已歧:
                已歧.add((源, 名, len(候选)))
                图.歧义.append((源, 名, len(候选)))
            目标 = min(候选, key=lambda q: (len(str(q)), str(q)))
            目标 = 目标.resolve()
            if str(目标) not in 图.节点:
                图.节点[str(目标)] = "索引外"     # 仓里有、索引没收 —— 照建边，但标出来
            边 = (源, str(目标), "引用" + 扩)
            if 边 not in 已加:
                已加.add(边)
        for 符 in {s for s in _标识.findall(文) if len(s) >= 5}:
            for q in 符号.get(符, [])[:3]:
                if str(q) == 源:
                    continue
                边 = (源, str(q), "用到")
                if 边 not in 已加:
                    已加.add(边)
        图.边.extend(sorted(已加))
    # ★ 只有"**全仓都没有**"才算死引用；仓里有但没进索引的，已经按「索引外」建边了。
    图.死引用 = sorted(死)
    图.统计 = {
        "文件": len(文件们),
        "扩展名": len(扩展),
        "边": len(图.边),
        "边-引用": sum(1 for _a, _b, t in 图.边 if t.startswith("引用")),
        "边-用到": sum(1 for _a, _b, t in 图.边 if t == "用到"),
        "死引用": len(图.死引用),
        "歧义": len(图.歧义),
    }
    return 图


def 缓存路径(根: Path) -> Path:
    """图缓存的**项目级**位置。

    ★ 为什么不能放 `DIRS.WORKDIR`（本轮踩过的坑）：那玩意是按**运行状态根**走的
      （每个测试/每个会话一个），问答时缓存必然不命中 ⇒ 在问答路径里重建整张图
      ⇒ 一次问答 209 秒 + 5.5 万次目录枚举，判据当场判红。
      ⇒ **建图是索引期的事；问答期只许读缓存。** 缓存必须跟**项目**绑，不是跟会话绑。
    """
    return Path(根) / _缓存子目录 / _缓存名


def 只读缓存(根: Path) -> "引用图 | None":
    """只读项目级缓存，**绝不建图**（问答路径专用）。没有就返回 None，不装。"""
    p = 缓存路径(根)
    if not p.is_file():
        return None
    try:
        原 = json.loads(p.read_text(encoding="utf-8"))
        图 = 引用图()
        图.边 = [tuple(t) for t in 原["边"]]
        图.死引用 = [tuple(t) for t in 原["死引用"]]
        图.歧义 = [tuple(t) for t in 原["歧义"]]
        图.节点 = dict(原["节点"])
        图.统计 = dict(原["统计"])
        return 图
    except (OSError, ValueError, KeyError):
        return None


def 项目图(刷新: bool = False) -> 引用图:
    """按**当前项目档案**建图（戒指纹缓存：文件变了自动重建）。"""
    from . import real_task as _rt
    from .profile import get_profile
    try:
        档案 = get_profile()
        entries = [dict(e) for e in (getattr(档案, "memory", None) or ())]
        根 = Path(getattr(档案, "repo_root", None) or _rt.ORCH)
    except Exception:                                # noqa: BLE001
        entries, 根 = [], Path(_rt.ORCH)
    if not entries:
        return 引用图()
    键源 = f"{根}|" + json.dumps(entries, ensure_ascii=False, sort_keys=True)
    键 = hashlib.sha256(键源.encode()).hexdigest()[:16]
    if not 刷新 and 键 in _内存:
        return _内存[键]
    文件们 = [p for 列 in _收文件(entries, 根).values() for p in 列]
    缓存 = 缓存路径(根)
    指纹 = _指纹(文件们)
    if not 刷新 and 缓存.is_file():
        try:
            原 = json.loads(缓存.read_text(encoding="utf-8"))
            if 原.get("指纹") == 指纹:
                图 = 引用图()
                图.边 = [tuple(t) for t in 原["边"]]
                图.死引用 = [tuple(t) for t in 原["死引用"]]
                图.歧义 = [tuple(t) for t in 原["歧义"]]
                图.节点 = dict(原["节点"])
                图.统计 = dict(原["统计"])
                _内存[键] = 图
                return 图
        except (OSError, ValueError, KeyError):
            pass
    图 = 建图(entries, 根)
    try:
        缓存.parent.mkdir(parents=True, exist_ok=True)
        原 = {"指纹": 指纹, "边": [list(t) for t in 图.边],
              "死引用": [list(t) for t in 图.死引用],
              "歧义": [list(t) for t in 图.歧义],
              "节点": 图.节点, "统计": 图.统计}
        缓存.write_text(json.dumps(原, ensure_ascii=False), encoding="utf-8")
    except Exception:                                # noqa: BLE001 缓存失败不影响结果
        pass
    _内存[键] = 图
    return 图