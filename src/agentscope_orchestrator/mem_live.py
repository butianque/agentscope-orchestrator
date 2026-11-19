"""常驻活符号表 —— 让编排器**自己**知道这个仓里有什么（R303）。

## 为什么要有这个模块

用户 2026-11-07 当面指出的问题（原话）：

> 「启动编排器，这个编排器**自己就要主动扫**这个文件夹下有什么，是这个编排器告诉你的。
>   不是你自己去看的，也不是你告诉我的。是你在每轮对话里就**必须知道的**
>   和你训练数据那样的**本来就知道**的。」
> 「要实现局部任意召回相关内容不丢，全局遍历知识图谱一次知道完整全局状态不丢信息，
>   **不用遍历整个仓库**的效果。」

改之前的真读数（本轮实测）：

· `符号表`（`mem_symbols.py`）**本来就有全部能力**：增量 `同步`、`调用者`、`引用名`、
  反向索引、墓碑；`枚举源文件` 的剪枝还是**前缀匹配**（`target_` 这种写法本来就考虑了）。
· 但它在 **6 个模块里各自 `符号表(); 表.同步(根)`**（`benchmark` / `fixloop` /
  `generic_goal` / `maturity` / `mem_context` / `mem_symbols` 自检）
  ⇒ **每次用都全新建表**，增量设计**全部作废**：`_文哈希` 不跨调用存活
  ⇒ 每次同步仍然把每个文件读进来算 sha256。
· `global` / `_单例` / `pickle` / 落盘 / 载入 —— 在 `mem_symbols.py` 里**各 0 次**。

⇒ 所以缺的**不是能力，是「常驻 + 落盘 + 自动 + 每轮注入」**这四件外壳。本模块就是这四件。

## 四件事分别在哪

| # | 要求 | 实现 |
|---|---|---|
| ① | **常驻** | `现存()` —— 进程内单例，所有调用方共用一张表 |
| ② | **跨进程存活** | `落盘()` / `_载盘()` —— pickle 出**含 `_文戳`** 的全状态；不带戳，下次起来戳筛全 miss ⇒ 又要读全仓 |
| ③ | **主动自动** | `自动同步()` —— 按档案 `memory:` 段**自己决定扫什么**，不需要调用方传根 |
| ④ | **每轮本来就知道** | `简报()` —— 紧凑全局状态（精确到目录 + 缺口），给注入用 |

## 为什么扫什么由档案定，不由本模块定

换项目 = 换 `orchestrator.profile.yml`。本模块**不写死任何项目路径**
（全文 `太虚元道` 0 次），只读 `profile.memory` 与 `profile.source.skip`。
档案没声明 ⇒ 退化为空表并告警，**不静默假装知道**。

## 关于「没调用的就抽不出」—— 那是错的，要纠正

`符号表.调用者(名)` 返回空 **不是抽取失败，是结论本身**：
**这个名字没有任何文件引用它**。所以「写了但没人接线」是**正面可判的**
（`无调用者()`），不是抽不出来。本轮实测反证：
`引用边里含「记起」的 = 0` 是 `project_graph.py` 那条路径的现象
（它的引用边只从 `.md` 文档抽「提到」），**不是 `符号表` 的现象** ——
`符号表` 的 `_引` 是按标识符建的，`调用者("记起")` 有真读数。

## 诚实边界

· `简报()` 的「无调用者」**不等于「没用」**：入口（`main` / `#[test]` / 被外部 crate 用的
  `pub`）天然无仓内调用者。它是**线索**，不是判决 —— 判决要门。
· 戳筛把「mtime_ns 与 size 都没变」当「内容没变」（同 make/cargo）。要绝对保险用 `全量=True`。
· 本模块**不改仓、不调模型**，只读文件与写自己的 pickle。
"""
from __future__ import annotations

import fnmatch
import json
import os
import pickle
import sys
import time
from pathlib import Path

from . import mem_symbols as ms

_包根 = Path(__file__).resolve().parents[2]
_状态路径 = _包根 / "products" / "mem" / "活符号表.pkl"
# ★ 注入用的**小文件**（2026-11-07 R303）。
#   为什么不能直接让注入去读 `_状态路径`：那个 pickle 有 87 万引用名，
#   `pickle.load` 要好几秒（实测第二次同步 9.1s 里大半是它）。
#   而 `_build_prompt` **每轮都跑** —— 每轮载几秒是不可接受的。
#   ⇒ 同步时**预算**好一段简报写成小文本，注入只读它（微秒级）。
#   口径：**重状态给查询，轻简报给注入**，两者用途不同，不该共用一份产物。
_简报路径 = _包根 / "products" / "mem" / "仓况.txt"
# 分区结果的小档（同 `_简报路径` 的道理：重活给同步，轻读给注入）
_分区路径 = _包根 / "products" / "mem" / "分区.json"
_分区: dict | None = None
# 缺口结果的小档 —— 算一次实测 2.45s，而 `简报()` **每轮都跑**，不能现算
_缺口路径 = _包根 / "products" / "mem" / "缺口.json"
_缺口: dict | None = None
# 锚定边小档 —— 实测 3.6s
_锚定路径 = _包根 / "products" / "mem" / "锚定.json"
_锚定: dict | None = None
# ★ 缺口**历史**（章程 §3.5：「缺口的数量变化就是演化进度」；§3.6 的判法就是
#   「缺口数的历史曲线」）。没有历史，缺口就只是个**静态读数**，
#   报「今天有 34,718 个没扫到」既说不出是变好还是变坏，也**没法验收**。
_缺口史路径 = _包根 / "products" / "mem" / "缺口史.jsonl"

_表: ms.符号表 | None = None
_已载 = False
_同步读数 = None
_告警: list[str] = []


# ════════════════════════════════════════════════════════════
# ① 常驻
# ════════════════════════════════════════════════════════════

def 现存() -> ms.符号表:
    """进程内单例。**所有调用方共用这一张表** —— 这是「常驻」的全部含义。

    ★ 改之前 6 个模块各建各的表，于是每个人都要从头读一遍全仓。
    """
    global _表
    if _表 is None:
        _表 = ms.符号表()
    return _表


def 规模() -> dict[str, int]:
    return 现存().规模()


def 上次同步():
    return _同步读数


def 告警() -> tuple[str, ...]:
    return tuple(_告警)


# ════════════════════════════════════════════════════════════
# ② 跨进程存活（落盘/载盘）
# ════════════════════════════════════════════════════════════

def 落盘(路径: Path | None = None) -> Path:
    路径 = Path(路径 or _状态路径)
    路径.parent.mkdir(parents=True, exist_ok=True)
    临时 = 路径.with_suffix(".tmp")
    with 临时.open("wb") as f:
        pickle.dump({"版本": 1, "状态": 现存().导出()}, f, protocol=pickle.HIGHEST_PROTOCOL)
    临时.replace(路径)                       # 原子替换：写坏也不毁旧档
    return 路径


def 载盘(路径: Path | None = None) -> bool:
    """载入上次的状态。**载不到不是错**（第一次跑），返回 False 并继续。"""
    路径 = Path(路径 or _状态路径)
    if not 路径.is_file():
        return False
    try:
        with 路径.open("rb") as f:
            包 = pickle.load(f)
    except Exception as e:                   # 版本不符/损坏 ⇒ 当作没有，重扫
        _告警.append(f"状态档读不了（{type(e).__name__}），按首次处理：{路径.name}")
        return False
    if not isinstance(包, dict) or 包.get("版本") != 1:
        _告警.append("状态档版本不符，按首次处理")
        return False
    现存().载入(包.get("状态") or {})
    return True


def 确保载入() -> bool:
    """显式载入落盘状态 —— **只给显式查询用，不要放进每轮的路径**。

    ★ 为什么要有这个区分（2026-11-07 实测踩到）：
      `repo_state` / `repo_symbol` 是**用户主动问**，载一次盘几秒可以接受；
      而 `仓况段` **每轮都跑**，它要是也载盘，每轮建提示词就多几秒。
      所以：**注入只读小简报（`_简报路径`），查询才载大状态**。
    """
    global _已载
    if 现存().规模().get("文件"):
        return True
    if _已载:
        return False
    好 = 载盘()
    _已载 = True
    return 好


# ════════════════════════════════════════════════════════════
# ③ 主动自动：扫什么由**档案**定
# ════════════════════════════════════════════════════════════

# 通用剪枝在 `mem_prune` 里 —— **全项目唯一一份**。
# ★ 2026-11-07 真事故：我先在这里写了一份、又在 `mem_partition` 写了一份，
#   两份立刻不一致 ⇒ `mem_partition` 没剪 `.git`/`.lake`/`.cargo_home`，
#   扫出 **2,011 个「项目」**（绝大多数是依赖包）。
#   **「同一个判断写在两处，必然给出两个答案」** ⇒ 抽出去，两边 import。
from .mem_prune import 通用跳, 该跳 as _剪枝该跳  # noqa: F401  （对外仍导出 通用跳）


def _该跳(名: str, 跳过: set[str]) -> bool:
    return _剪枝该跳(名, 跳过)


def _枚举声明(仓库根: Path, root: str, glob: str, 跳过: set[str]):
    """按一条 `memory:` 声明枚举，**可剪枝**。

    ★ 不用 `根.glob(glob阶)`：`枚举源文件` 在 `glob阶 != "**/*"` 时走 `根.glob()`，
      那会**先把整棵树物化**再过滤 —— 仓根往下就是 20 万+ 文件（构建产物）。
      这里自己走 `os.walk` 并剪枝，被跳过的目录**根本不进**枚举。
    """
    基 = 仓库根 / root if root not in ("", ".") else 仓库根
    if not 基.is_dir():
        return
    递归 = "**" in glob
    模式 = glob.split("/")[-1]
    for dp, dn, fn in os.walk(基):
        dn[:] = sorted(d for d in dn if not _该跳(d, 跳过))
        for f in sorted(fn):
            if fnmatch.fnmatch(f, 模式):
                p = Path(dp) / f
                try:
                    相对 = str(p.relative_to(仓库根)).replace("\\", "/")
                except ValueError:
                    continue
                yield p, 相对
        if not 递归:
            dn[:] = []                       # `*.md` 只扫这一层（档案口径）


def _命中要求(p: Path, 要求: tuple[str, ...], 头字节: int = 4096) -> bool:
    """`require:` 这层：**文件名或文件头部**命中任一关键词即算命中。

    ★ 为什么把文件名也算进来：档案的原话是「**文件头部**必须命中的关键词」，
      但一个叫 `任务书-XXX.md` 的文件，名字本身就已经说明了它是任务书；
      只看正文会把这类判掉，而「判掉该要的」比「多要一个」贵得多
      （多要的会在读数里显形，判掉的不会）。这条口径写在这里，不写在档案注释里 ——
      **注释不参与执行**。

    ★ 读不了的文件**算命中**（保留），并把计数单独报出来：
      读不了 ⇒ 无法判断 ⇒ 滤掉就是**拿「看不见」当「没有」**。
    """
    名 = p.name
    if any(k in 名 for k in 要求):
        return True
    try:
        with p.open("rb") as f:
            头 = f.read(头字节)
    except OSError:
        return True                              # 读不了 ⇒ 不筛掉，另行计数
    try:
        文 = 头.decode("utf-8", errors="replace")
    except Exception:                            # noqa: BLE001
        return True
    return any(k in 文 for k in 要求)


def 自动同步(*, 全量: bool = False, 落: bool = True, 分区: bool = True,
             上限缺口: int = 25) -> dict:
    """按档案 `memory:` 段扫，**调用方不需要传任何根**。

    返回 `{ok, 扫描, 变更, 戳筛跳过, 读盘, 新增墓碑, 规模, 声明数, 告警}`。

    `分区=True`（默认）：同时扫**整个仓根**分区（项目边界/角色/剪枝）+
    算**三类缺口**。实测 +8.5s（分区）+2.5s（缺口）。
    这两块是「分得清什么东西是什么 / 哪里不完整」那层的代价，
    **只在同步时付一次** —— 简报每轮读的是它们落的小档。
    """
    global _同步读数, _已载
    from . import profile as prof_mod

    _告警.clear()
    表 = 现存()
    # ★ 只在**本进程第一次**同步时载盘。原来每次都载 —— 那会把刚同步好的内存状态
    #   用盘上的旧档盖掉（同一进程内多调几次就白同步了）。
    载过 = False
    if not 全量 and not _已载:
        载过 = 载盘()
    _已载 = True
    档案 = prof_mod.get_profile()
    仓库根 = Path(档案.repo_root)
    跳过 = set(档案.source.skip or ())
    声明们 = list(档案.memory or ())

    if not 声明们:
        _告警.append("档案没有 `memory:` 段 ⇒ 扫不到任何东西（不静默假装知道）")

    候选: list[tuple[Path, str]] = []
    已见: set[str] = set()
    要求滤掉 = 0
    for 声明 in 声明们:
        # ★★★ 2026-11-07：`require:` 以前**声明了但没人执行**。
        #
        #   `profile.py:254` 写着「`require:` 可选，文件头部必须命中的关键词（任务书一类用）」，
        #   档案里 `kind: task` 也真的用了：
        #       roots: [多智能体编排/agentscope_orchestrator]
        #       globs: ["**/*.md"]
        #       require: [任务, 需证, 未闭合, 待办, todo, formalize, 证明]
        #   而本函数**根本没有 `require` 参数**，声明被一路丢掉 ⇒
        #   `**/*.md` 是递归的，于是**这个包下每一个 `.md`**（全部回执、章程、
        #   交付说明、诊断报告、CHANGES…）都被当成「任务书」进了常驻表。
        #
        #   ⇒「一段驱动不了的接线，等于没有接线」。
        #     而且是**最坏的一种**：档案里写着有过滤，读数里看不出没有 ——
        #     「看不见」不能当「没有」，这里反过来：**看起来有，其实没有**。
        要求 = tuple(str(x) for x in (声明.get("require") or ()))
        for root in (声明.get("roots") or ["."]):
            for glob in (声明.get("globs") or ["**/*"]):
                for p, 相对 in _枚举声明(仓库根, str(root), str(glob), 跳过):
                    if 要求 and not _命中要求(p, 要求):
                        要求滤掉 += 1
                        continue
                    if 相对 in 已见:
                        continue
                    已见.add(相对)
                    候选.append((p, 相对))

    # ★ 一次算完：墓碑逻辑要求 `现存` 是**全部声明合起来**的集合
    读 = 表.同步(仓库根, 候选=候选, 全量=全量)
    _同步读数 = 读
    分区果 = None
    if 分区:
        try:
            分区果 = 分区同步()          # 扫整个仓根（慢的那一步，只在同步时做）
        except Exception as e:           # noqa: BLE001
            _告警.append(f"分区失败：{type(e).__name__}: {e}")
    缺口果 = None
    if 分区:                             # ①要跟分区对账，所以跟着分区一起算
        try:
            缺口果 = 缺口同步(上限=上限缺口)
        except Exception as e:           # noqa: BLE001
            _告警.append(f"缺口失败：{type(e).__name__}: {e}")
    锚定果 = None
    if 分区:
        try:
            锚定果 = 锚定同步()
        except Exception as e:           # noqa: BLE001
            _告警.append(f"锚定失败：{type(e).__name__}: {e}")
    if 落:
        try:
            落盘()
        except OSError as e:
            _告警.append(f"状态落盘失败：{e}")
        try:
            _写简报()                            # 小文件，给注入用
        except OSError as e:
            _告警.append(f"简报落盘失败：{e}")
    # ★ 顺序要紧：**先读上一次的差，再写这一次**。写反了差值恒为 0。
    史段 = 史一行()
    记史(读数取(缺口果) if 缺口果 else None,
         锚定读数取(锚定果) if 锚定果 else None)
    return {"ok": True, "载过状态档": 载过, "声明数": len(声明们),
            "扫描": 读.扫描文件, "变更": 读.变更文件, "戳筛跳过": 读.未变跳过,
            "读盘": 读.读盘次数, "新增墓碑": 读.新增墓碑,
            # ★ `require:` 滤掉多少**必须报出来** —— 过滤器的力度看不见，
            #   就没人知道它是在干活还是又一根没接上的线。
            "require滤掉": 要求滤掉,
            "规模": 表.规模(),
            "分区": None if 分区果 is None else {
                "项目数": 分区果["项目数"], "工位数": 分区果["工位数"],
                "文件数": 分区果["文件数"], "目录数": 分区果["目录数"],
            },
            "缺口": None if 缺口果 is None else 缺口一行(缺口果),
            "锚定": 锚定一行(锚定果) or None,
            "缺口史": 史段,
            "告警": list(_告警)}


# ════════════════════════════════════════════════════════════
# ④ 每轮本来就知道：简报
# ════════════════════════════════════════════════════════════

def 顶层目录树(上限: int = 0) -> list[dict]:
    """按**目录**汇总（精确到具体文件夹）—— 用户要的「分得清什么东西在哪个文件夹」。

    `上限=0` 不限条数。返回按文件数降序：
    `[{目录, 文件, 符号, 条}]`
    """
    表 = 现存()
    agg: dict[str, dict] = {}
    for 相对, 名们 in 表._文符号.items():
        顶 = 相对.split("/")[0] if "/" in 相对 else "(仓根文件)"
        d = agg.setdefault(顶, {"目录": 顶, "文件": 0, "符号": 0, "条": 0})
        d["文件"] += 1
        d["符号"] += len(名们)
        d["条"] += sum(1 for 名 in 名们 for s in 表._活.get(名, ()) if s.文件 == 相对)
    出 = sorted(agg.values(), key=lambda x: -x["文件"])
    return 出[:上限] if 上限 else 出


def 无调用者(上限: int = 0, 只报多文件: bool = True) -> list[dict]:
    """**没有任何仓内文件引用**的符号 —— 「写了但没人接线」的正面判据。

    ★ 这是线索不是判决：入口（`main`/`#[test]`/`pub` 外部 API）天然无仓内调用者。
    `只报多文件=True` 时跳过只在一个文件里出现的名字（多半是局部变量/字段误抽）。
    """
    表 = 现存()
    出: list[dict] = []
    for 名, 符们 in 表._活.items():
        文件们 = {s.文件 for s in 符们}
        if 表.调用者(名):
            continue
        if 只报多文件 and len(符们) == 1 and 符们[0].种类 in ("const", "static", "type", "field"):
            continue
        出.append({"名": 名, "种": 符们[0].种类 if 符们 else "",
                   "定义在": sorted(文件们)[:3], "定义处数": len(符们)})
    出.sort(key=lambda x: x["名"])
    return 出[:上限] if 上限 else 出


def 取分区() -> dict:
    """分区结果（常驻）。**进程内有就用内存；否则读小档；都没有就空。不重扫。**"""
    global _分区
    if _分区 is not None:
        return _分区
    try:
        if _分区路径.is_file():
            _分区 = json.loads(_分区路径.read_text(encoding="utf-8"))
    except Exception:                            # noqa: BLE001 —— 坏档当没有，不阻塞
        _分区 = None
    return _分区 or {}


def 分区同步() -> dict:
    """扫**整个仓根**并落小档 —— 这是慢的那一步（实测 ~8.5s），**只在同步时做**。

    ★ 为什么单独一步、且落成小档：`简报()` **每轮都跑**，绝不能在里面扫仓根。
      口径同 `_简报路径`：**重活给同步，轻读给注入。**
    """
    global _分区
    from . import mem_partition as _mp
    from . import profile as _prof
    p = _prof.get_profile()
    果 = _mp.扫(p.repo_root, 跳过=set(p.source.skip or ()))
    _分区 = 果
    try:
        _分区路径.parent.mkdir(parents=True, exist_ok=True)
        临时 = _分区路径.with_suffix(".tmp")
        # ★ `_全仓lean名` 是个 set，**不落盘**：JSON 存不了 set，而且它是给
        #   同进程的锚定层用的瞬态读数（省掉第二遍全仓 walk）。落盘只会白占 200 KB。
        落 = {k: v for k, v in 果.items() if not k.startswith("_")}
        临时.write_text(json.dumps(落, ensure_ascii=False), encoding="utf-8")
        临时.replace(_分区路径)
    except OSError as e:
        _告警.append(f"分区落档失败：{e}")
    return 果


# ════════════════════════════════════════════════════════════
# 缺口：**图谱不完整是常态，但「哪里不完整」必须是一等公民**
# ════════════════════════════════════════════════════════════

# 不是符号：语言关键字 / 内建名 / 高频通用词。
# ★ 为什么要这么长：`符号表` 的引用索引收**文本里所有标识符**（宁多不漏），
#   所以 `have`/`Prop`/`Type` 这些 Lean 关键字会全部进来。实测（2026-11-07）：
#   不过滤时悬空榜前两名是 `have`(2,281 文件)、`Prop`(2,241) —— **纯噪声**。
# ★ 诚实边界：这是**手写清单**，必然漏（新关键字、新库名）也必然误（真有人把变量叫 `data`）。
#   所以：① 返回里**报出滤掉了多少**，让过滤器的力度可见；
#        ② 返回**前 N 个**让调用方能一眼看出还剩多少噪声；
#        ③ 措辞一律是「线索」，不是「缺了这么多定义」。
_不是符号 = frozenset("""
if else elif for while return def class import from as with try except finally raise
pass break continue lambda yield global nonlocal assert del in is not and or
None True False self cls
print len range str int float bool list dict set tuple type object super bytes
let mut fn impl struct enum use mod pub crate match loop where unsafe trait const static
true false nil end then do begin function local require
theorem lemma axiom abbrev instance structure namespace open section variable
have Prop Type Sort Or And Not Iff Eq Nat Int Bool List Option
some none pure bind map filter fold deriving extends noncomputable classical
simp rw exact apply intro cases induction unfold rewrite sorry
hmul hadd ofnat coe functor monad decidable beq repr inhabited empty nonempty
subtype sigma psigma ulift plift quot prod sum unit punit fin array vector
string char uint8 uint16 uint32 uint64 usize isize f32 f64
the and for with this that from are all any use can has its our out
one two set get add new old key val name size data path file test main
args index value count total result item node edge find push pop max min
sum abs open read write close run start stop init done error ok null
first last next prev head tail body text line word byte bit buf tmp cur ret res msg
""".split())


def 缺口(上限: int = 25, 表=None) -> dict:
    """三类缺口 —— **每类都报数，并报出「拿什么算的」**（不许只说「有缺口」）。

    | # | 缺口 | 怎么算（机器判据） |
    |---|---|---|
    | ① | **没扫到** | 分区扫到的**全部文件** vs 符号表**收录**的文件 —— 差就是档案没声明的 |
    | ② | **抽到了连不上** | 被 **≥2 个文件**引用、但**全表没有任何定义**的名字（悬空） |
    | ③ | **连上了没语义** | 收录了、却**一个符号都抽不出**的文件（按后缀分组） |

    ★ 为什么要 `≥2 个文件` 这一条：`符号表` 的引用索引收的是**文本里所有标识符**
      （宁多不漏），所以局部变量、关键字都在里面。只被 1 个文件提到的名字
      大多是本文件内的局部量；**被多个文件提到却哪儿都没定义**，才是真缺口。

    ★ 诚实边界（必须跟着数字一起给）：
      · ② 里**混着外部库**（`torch`/`numpy` 这种本来就该在表外）——
        所以返回同时给「引用文件数最多的前 N 个」，让调用方能看出偏多少，
        并**显式标注这不是「缺了 N 个定义」**；
      · ① 的差**不等于错**：`memory:` 只声明该进表的根，仓里本来就有不该进表的
        （产物、依赖包、数据）。所以①同时给分区里的**剪枝按类**做参照；
      · ③ 只说明「抽不出符号」，**不等于这个文件没内容**（散文、数据、配置本来就无符号）。
    """
    表 = 表 if 表 is not None else 现存()
    规 = 表.规模()
    出: dict = {}

    # ── ② 抽到了连不上 ──
    #    ★ 分两层报，因为这两层的**证据强度完全不同**（2026-11-07 实测教训）：
    #      我先只按「被 ≥2 文件引用 ∧ 全表无定义」算，实得 **236,088 个** ——
    #      看着像「缺了 23 万个定义」，其实是**局部变量**：引用索引收的是文本里
    #      **所有标识符**，`total`/`count` 这种局部量天然出现在多个文件里。
    #      筛子（≥2 文件）**不具选择性** ⇒ 不能拿它当缺口数。
    定义名 = set(表._活)
    阈值 = (2, 5, 10, 50)
    各阈值 = {k: 0 for k in 阈值}
    最热: list[tuple[str, int]] = []
    滤掉 = 0
    for 名, 文件们 in 表._引.items():
        if 名 in 定义名 or len(名) < 3 or 名.startswith("_"):
            continue
        if 名 in _不是符号:
            滤掉 += 1
            continue
        c = len(文件们)
        for k in 阈值:
            if c >= k:
                各阈值[k] += 1
        if c >= 2:
            最热.append((名, c))
    最热.sort(key=lambda kv: -kv[1])

    # ★ 硬读数：**符号级**连通率 —— 每一个定义都是真的节点，分母没有噪声。
    #   「抽到了连不上」在这一层才有判决力：
    #   定义出来了、却没有任何**别的文件**引用它 ⇒ 这个节点在图上是孤立的。
    无调 = 无调用者()
    有跨文件引用者 = len(定义名) - len(无调)
    出["②抽到了连不上"] = {
        "硬读数（符号级，分母无噪声）": {
            "定义符号名": len(定义名),
            "其中被**别的文件**引用过": 有跨文件引用者,
            "无仓内调用者（图上孤立）": len(无调),
            "连通率": (f"{有跨文件引用者 / len(定义名):.1%}" if 定义名 else "n/a"),
            "★这不是判决": ("入口/main/导出 API 天然无调用者 ⇒ 孤立是**线索**；"
                            "真判据要按语言分别排除入口（见 `无调用者`）。"),
        },
        "线索（标识符级，**会淹**）": {
            "≥2 文件提到且全表无定义": 各阈值[2],
            "≥5": 各阈值[5], "≥10": 各阈值[10], "≥50": 各阈值[50],
            "滤掉的关键字/内建/通用词": 滤掉,
            "最多提到的前若干": [{"名": n, "被几个文件引用": c} for n, c in 最热[:上限]],
            "★这不是「缺了这么多定义」": (
                "该层**没做作用域分析**，局部变量会被算成悬空 —— 实测 ≥2 这一档有 "
                f"{各阈值[2]:,} 个，绝大多数是局部量。**看趋势用这层，判缺口用上面那层。**"),
        },
        "算法": ("硬读数＝`定义名 − 无调用者()`（跨文件引用索引判定）；"
                 "线索＝被 ≥K 个文件提到的标识符里，全表找不到定义的"),
    }

    # ── ③ 无符号文件：收录了但一个符号都抽不出 ──
    from collections import Counter as _C
    无符后缀: _C = _C()
    无符文件 = 0
    for 相对 in 表._文哈希:
        if not 表._文符号.get(相对):
            无符文件 += 1
            无符后缀[Path(相对).suffix.lower() or "(无后缀)"] += 1
    出["③连上了没语义"] = {
        "收录但抽不出符号的文件数": 无符文件,
        "收录文件数": 规["文件"],
        "按后缀": dict(无符后缀.most_common(上限)),
        "算法": "文件在表内 ∧ `_文符号[该文件]` 为空",
        "★不等于没内容": "散文/数据/配置本来就无符号；本条只说明**抽不出结构**",
    }

    # ── ① 没扫到：分区扫到的全部文件 vs 表收录的 ──
    分 = 取分区()
    if 分:
        出["①没扫到"] = {
            "分区扫到的文件数": 分.get("文件数"),
            "符号表收录的文件数": 规["文件"],
            "差（未进表）": (分.get("文件数") or 0) - 规["文件"],
            "剪枝按类（参照：差里有多少本来就是产物/依赖）": 分.get("剪枝按类"),
            "算法": "分区（扫整个仓根）文件数 − 符号表（按档案 memory:）收录文件数",
            "★差≠错": ("`memory:` 只声明该进表的根；仓里本来就有不该进表的。"
                       "要看的是**这个差有没有解释**，不是把差清零"),
        }
    else:
        出["①没扫到"] = {"未测到": "没有分区结果（未同步过分区）"}

    # ── ④ 图上死引用（★ 2026-11-07 新加：章程 §3.5「缺口必须是节点」那一半）──
    #    为什么要有这一类：`refgraph` 早就在建一张**有边**的图，而且
    #    `死引用` 是一等记录（它的 docstring：「不许静默丢掉，那是可报告的缺陷」）。
    #    但 `refgraph` 的读数和**常驻缺口读数/简报/缺口史**从来没接上过 ——
    #    一条 3,655 条的真缺口，在每轮注入里**一个字都不出现**。
    try:
        出["④图上死引用"] = 图上死引用()
    except Exception as e:                       # noqa: BLE001
        出["④图上死引用"] = {"未测到": f"{type(e).__name__}: {str(e)[:80]}"}

    # ── ⑤ 公理足迹（★ 2026-11-07 新加：**问 Lean 内核**，不是查文本）──
    #    为什么必须有这一类：本仓全仓 `sorry` **文本命中 0** ——
    #    按「查 sorry」会得出「证明是干净的」。而 `#print axioms`（`collectAxioms`）
    #    扫出 **19 条声明站在 `Lean.trustCompiler` 上**（`native_decide`：
    #    证明是**跑编译代码跑出来的**，不是内核逐步验算的），
    #    全部落在音律/节气这一域 —— 正是工程的核心主张。
    #    ★ 这些名字**在源代码里一个字都不出现**（洞藏在被调用的引理里）。
    #    ⇒ 这不是「更大的图」，是**维度**：grep 数不出这个数。
    #    ★ 它是**索引期**产物（扫一遍 53,411 条约 10 分钟），这里只读档。
    try:
        from . import mem_axiom as _ma
        出["⑤公理足迹"] = _ma.缺口()
    except Exception as e:                       # noqa: BLE001
        出["⑤公理足迹"] = {"未测到": f"{type(e).__name__}: {str(e)[:80]}"}
    return 出


def 取缺口() -> dict:
    """缺口结果（常驻）。**不现算**（现算 2.45s，简报每轮都跑）。"""
    global _缺口
    if _缺口 is not None:
        return _缺口
    try:
        if _缺口路径.is_file():
            _缺口 = json.loads(_缺口路径.read_text(encoding="utf-8"))
    except Exception:                            # noqa: BLE001
        _缺口 = None
    return _缺口 or {}


# ── ④ 图上死引用：把 `refgraph` 的缺口节点接进常驻读数 ──────────────
_死引用档 = _包根 / "products" / "mem" / "死引用分类.json"


def 图上死引用(*, 重算: bool = False) -> dict:
    """`refgraph.死引用` → **三分类**，供常驻缺口读数用。

    ★★★ 为什么必须是三分类，而不是直接报 6,198
      实测（2026-11-07 真仓）：`refgraph` 缓存里 `死亡引用 = 6,198`。
      但把它逐条拿去**全仓文件名集**（按 `mem_prune` 剪枝走一遍）里核对：

          ① 真死引用（**全仓都没有**这个文件名）  **3,655** 条
          ② 被剪枝但存在（仓里有，只是不在 `memory:` 声明的根里）**2,543** 条

      ⇒ **41% 是假红。** 直接报 6,198 就是把「在别的根里」当成「悬空」——
        A 级锚定第一版正是这么错的（`mem_anchor.py` 文件头记着那次）。
        **假绿比无齿更坏，假红同样是坏账**：它让人去修不存在的问题。

    ★ 为什么要有缓存档：全仓文件名那一遍实测 ~200s，不能进每轮路径。
      这里按 `refgraph` 缓存的指纹复用；`重算=True` 才重走。
      读不到缓存时**如实报「未测到」**，不许拿 0 冒充「没有缺口」。

    ★ 边界（如实）：② 只说明「仓里有同名文件」，**不保证就是被点名那一个**
      （同名不同路径很常见）。所以②只用来**从缺口里减掉**，不用来证明引用是对的。
    """
    try:
        from . import refgraph as _rg
        from . import mem_prune as _mp
    except Exception as e:                       # noqa: BLE001
        return {"未测到": f"导不进 refgraph（{type(e).__name__}: {e}）"}

    仓根 = _包根.parents[1]
    try:
        图 = _rg.只读缓存(仓根)
    except Exception as e:                       # noqa: BLE001
        return {"未测到": f"refgraph 缓存读不了（{type(e).__name__}: {e}）"}
    if 图 is None:
        return {"未测到": "refgraph 没有缓存档（要先 `refgraph.项目图()` 建一次）"}

    死 = list(getattr(图, "死引用", ()) or ())
    指纹 = f"{len(死)}|{(图.统计 or {}).get('边')}|{getattr(图, '统计', {}).get('文件')}"
    if not 重算 and _死引用档.is_file():
        try:
            原 = json.loads(_死引用档.read_text(encoding="utf-8"))
            # ★★★ 指纹相同**不够**：侧车还得带 `真死名`。
            #   第一版这里只比指纹 —— 侧车是**旧格式**（没有 `真死名`）时照样命中，
            #   于是 `真死名字()` 返回空集，`_本块缺口` 分不开①/②。
            #   实测抓法：局部召回报「真死 0 条」——**看起来像好消息**。
            #   ⇒ 缓存有效性必须把「**下游要用的字段在不在**」算进去，
            #     不能只比「是不是同一次算的」。**「看不见」不能当「没有」。**
            if 原.get("指纹") == 指纹 and "真死名" in 原:
                return 原["果"]
        except Exception:                        # noqa: BLE001
            pass

    # 全仓文件名集：**不剪枝**（只排 `.git`）。
    #
    # ★★★ 2026-11-07 同一个回合里，本仓两个方向各自踩了同一个坑，必须记下来：
    #   第一版我用 `mem_prune.该跳(d, ())` 剪枝走这一遍 —— **错的**。
    #   `档案 skip` 与 `mem_prune.通用跳` 都是**前缀匹配**：
    #     `中文编译器` 会连 `中文编译器_backup_20260710/**` 一起剪掉，
    #     `通用跳` 会剪掉 `.cargo_home`。
    #   ⇒ 只在剪枝目录里存在的名字，在剪枝后的集合里**找不到** ⇒
    #     被判成「①真死引用」。**我的 3,655 因此偏大。**
    #   队友在反方向（`.lean → .rs`）量到同一件事：用剪枝口径，三类从
    #     21 / 7 / 47 变成 **21 / 48 / 6** —— 41 条**真存在**的被误报成「悬空」。
    #   ⇒ **判「存在性」的口径必须是不剪枝的全仓**；剪枝口径只能用来判「该不该进表」。
    #     「看不见」不能当「没有」——剪枝就是主动选择看不见。
    #
    #   ★ 代价：不剪枝会看到 `.lake`、`.venv`、`products` 那些大目录。
    #     ★ 实测本机这一遍 **~25–60s**（R4xx 的读数）——★ **这个读数会随树长大而失效**：
    #        2026-11-14 复测 = 133,924 目录 · 329,090 文件 · **143.4s**（只排 `.git`）
    #        ⇒ 原来走两遍 ~287s ⇒ 判四十六 撞上限 ⇒ 套件卡死。
    #        ⇒ **R519 起只走一遍**（两个口径在一次遍历里同时收），并在下面加秒预算留痕。
    #     不剪枝、但**只排 `.git`**（实测 `.git` 里没有这些后缀的文件）。
    import os as _os
    from . import mem_prune as _mp2

    # ★★★ R519：**一遍走完，两个口径同时收**。
    #   ★ 为什么原来走两遍是**可避免的**：剪枝只改变**后续递归**，
    #     不改变**已经看到的条目** ⇒ 一次「只排 `.git`」的完整遍历里，
    #     对每个目录按 `该跳` 判一次它**在剪枝口径下会不会被跳过**，
    #     就得到与第二遍**完全相同**的 `剪枝名`。
    #   ★ 实测（2026-11-14，只排 `.git`）：一遍 = 133,924 目录 · 329,090 文件 · **143.4s**
    #     ⇒ 两遍 ~287s ⇒ 判四十六 撞 600s 上限 ⇒ 套件永久卡在 [46/168]（口径 337）。
    #   ★ 口径 341 同族教训：**省下来的必须是"同一份读数"** ——
    #     这里两个集合确实来自同一份目录快照，所以合并**不是**偷懒。
    全仓名: set[str] = set()
    剪枝名: set[str] = set()
    # `被剪[(相对目录)]` —— 该目录在剪枝口径下会不会被跳过（自顶向下传递）
    _被剪: dict[str, bool] = {"": False}
    for dp, dn, fn in _os.walk(仓根):
        if ".git" in dn:
            dn.remove(".git")
        全仓名.update(fn)
        _相 = _os.path.relpath(dp, 仓根)
        _相 = "" if _相 == "." else _相
        _父 = _被剪.get(_os.path.dirname(_相) if _相 else "", False)
        _本剪 = _父 or (bool(_相) and _mp2.该跳(_os.path.basename(_相), ()))
        _被剪[_相] = _本剪
        if not _本剪:
            剪枝名.update(fn)

    真死, 剪枝但有 = 0, 0
    例真死, 例剪枝 = [], []
    真死名: set[str] = set()
    # ★ 同时算一遍**剪枝口径**下的判法，把「用错口径会误报多少」量出来
    误报若剪枝 = 0
    for a, b in 死:
        尾 = b.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if 尾 in 全仓名:
            剪枝但有 += 1
            if len(例剪枝) < 6:
                例剪枝.append([str(a)[:80], str(b)[:60]])
        else:
            真死 += 1
            真死名.add(尾)
            if len(例真死) < 10:
                例真死.append([str(a)[:80], str(b)[:60]])
        if 尾 not in 剪枝名:
            误报若剪枝 += 1

    果 = {
        "★缺口节点来自": "refgraph（有边的图，非读数）",
        "图上死引用总数": len(死),
        "①真死引用（全仓都没有）": 真死,
        "②被剪枝但存在（**不是缺口**）": 剪枝但有,
        "歧义（多个同名目标）": len(getattr(图, "歧义", ()) or ()),
        "连接边数": (图.统计 or {}).get("边"),
        "图内文件数": (图.统计 or {}).get("文件"),
        "①例": 例真死,
        "②例": 例剪枝,
        "★③如果拿剪枝口径判存在性会误报多少": 误报若剪枝,
        "★存在性口径（不许改）": ("**不剪枝的全仓**（只排 `.git`）；"
                                    "`档案 skip` 与 `mem_prune.通用跳` 都是**前缀匹配**，"
                                    "拿它们判存在性会把「在剪枝目录里」报成「没有」"),
        "★两个口径的名数": {"不剪枝": len(全仓名), "剪枝": len(剪枝名)},
        "★②的边界": ("只说明「仓里有同名文件」，**不保证就是被点名那一个**；"
                      "所以②只用来从缺口里减掉，不用来证明引用是对的"),
        "★为什么不能只报总数": (
            f"实测 **{剪枝但有 / max(1, len(死)) * 100:.0f}% 是假红**"
            f"（{剪枝但有:,}/{len(死):,} 的目标在仓里存在）——"
            "**把「在别的根里」报成「悬空」正是 A 级锚定踩过的坑**"),
        "★我第一版错在哪（留档）": (
            "第一版用 `mem_prune` 剪枝走全仓名集，得 ①=3,655。"
            "不剪枝后 ①=**2,228** —— **多报了 1,427 条（39%）**。"
            "`档案 skip`/`通用跳` 是**前缀匹配**（`中文编译器` 吃掉 `中文编译器_backup_*`），"
            "所以只在剪枝目录里存在的名字被判成「全仓都没有」。"
            "★ 队友在反方向（`.lean→.rs`）同回合量到同一件事：剪枝口径把三类从 "
            "21/7/47 变成 21/48/6。**判存在性必须用不剪枝的全仓。**"),
        "算法": ("读 refgraph 缓存 → 逐条死引用取 basename → "
                  "在「**不剪枝**走一遍全仓」得到的文件名集里找"),
    }
    try:
        _死引用档.parent.mkdir(parents=True, exist_ok=True)
        临 = _死引用档.with_suffix(".tmp")
        # ★ `真死名` 单独存：**局部召回要用它分类**（问「这个概念牵连的文件里
        #   有哪些点名点不到」），而局部召回在**问答路径**上 ——
        #   不能每问一次就走一遍全仓。2,228 个短名字 ≈ 60KB，存得起。
        #   ★ 它**不进** `果`：`果` 要进 `缺口.json` 和简报，那是每轮都读的东西。
        临.write_text(json.dumps({"指纹": 指纹, "果": 果,
                                  "真死名": sorted(真死名)}, ensure_ascii=False),
                      encoding="utf-8")
        临.replace(_死引用档)
    except OSError:
        pass
    return 果


def 真死名字() -> set[str]:
    """`图上死引用` 算出的**真死引用名字集**（读侧车，**不走全仓**）。

    ★ 为什么要有这个函数：局部召回（`concept.概念召回`）需要知道
      「这一批文件里点到的名字哪些是真死的」——
      那是**问答路径**，不能每问一次走一遍全仓（实测 17.4 s）。
    ★ 侧车不在 ⇒ 返回**空集**，而不是假装「没有死引用」。
      调用方拿到空集时应当报「未测到」，不许报「0 条」——
      这是本包的老规矩：**「看不见」不能当「没有」**。
    """
    try:
        原 = json.loads(_死引用档.read_text(encoding="utf-8"))
        return set(原.get("真死名") or ())
    except Exception:                            # noqa: BLE001
        return set()


def 缺口同步(上限: int = 25) -> dict:
    """算三类缺口并落小档。**只在同步时调。**"""
    global _缺口
    果 = 缺口(上限=上限)
    _缺口 = 果
    try:
        _缺口路径.parent.mkdir(parents=True, exist_ok=True)
        临时 = _缺口路径.with_suffix(".tmp")
        临时.write_text(json.dumps(果, ensure_ascii=False), encoding="utf-8")
        临时.replace(_缺口路径)
    except OSError as e:
        _告警.append(f"缺口落档失败：{e}")
    return 果


def 缺口一行(缺: dict | None = None) -> str:
    """缺口压成**一行**给简报用 —— 四个数，各带一句边界。"""
    缺 = 缺 if 缺 is not None else 取缺口()
    if not 缺:
        return ""
    try:
        一 = 缺.get("①没扫到") or {}
        二 = (缺.get("②抽到了连不上") or {}).get("硬读数（符号级，分母无噪声）") or {}
        三 = 缺.get("③连上了没语义") or {}
        四 = 缺.get("④图上死引用") or {}
        五 = 缺.get("⑤公理足迹")
        if not 五:
            # ★ 踩过一次：`缺口一行` 走的是 `取缺口()`（**缓存** `products/mem/缺口.json`）。
            #   缓存是在 ⑤ 之前写的 ⇒ `缺.get("⑤公理足迹")` 是 None ⇒ 下面
            #   全走 `.get(..., '?')` ⇒ **新加的缺口类别在简报里静默显示成 `?`**。
            #   两处判断给两个答案。⇒ 缺就**现取**（`mem_axiom.缺口()` 只读档，不跑 Lean）。
            try:
                from . import mem_axiom as _ma
                五 = _ma.缺口()
            except Exception:                    # noqa: BLE001
                五 = {}
        五 = 五 or {}
        # ★ ④ 那一截：**只报「真死引用」**，并把它从总数里减掉了多少**同时报出来** ——
        #   不然读的人会以为「图上死引用」就是那个大数。41% 是假红，必须让减掉的动作可见。
        if 四.get("未测到"):
            四段 = f" · ④**图上死引用** 未测到（{四['未测到']}）"
        else:
            四段 = (f" · ④**图上死引用** {四.get('①真死引用（全仓都没有）', '?')} 条真死"
                    f"（总数 {四.get('图上死引用总数', '?')} 里减掉了 "
                    f"{四.get('②被剪枝但存在（**不是缺口**）', '?')} 条「存在但被剪枝」的假红）")
        # ★ ⑤ 那一截：**语义级**，文本查不出来。
        #   为什么单列「信任编译器」：它和 `sorryAx` 是**两种不同的洞**
        #   （一个是明说的，一个是「相信编译器」），而且**都不出现在源码文本里**。
        #   ★ 覆盖率必须一起报 —— 拿 43% 覆盖率的扫描结果当全工程结论，
        #     就是「把看不见当没有」。
        if 五.get("未测到"):
            五段 = f" · ⑤**公理足迹** 未测到（{五['未测到']}）"
        else:
            五段 = (f" · ⑤**公理足迹**（内核给的，非文本）洞 {五.get('①洞（sorryAx）', '?')}"
                    f"· **★信任编译器 {五.get('★②信任编译器', '?')}**"
                    f"（`native_decide`：证明是跑编译代码跑出来的，不是内核验的）"
                    f"· 标准公理 {五.get('③标准公理（正常）', '?')}"
                    f"· 纯内核 {五.get('⑤无公理（真干净）', '?')}")
            if 五.get("★覆盖率"):
                五段 += f" · 覆盖 {五['★覆盖率']}"
        return (f"缺口：①**没扫到** {一.get('差（未进表）', '?')} 个文件未进表"
                f"（差≠错，见边界）· "
                f"②**连不上** 连通率 {二.get('连通率', '?')}"
                f"（图上孤立 {二.get('无仓内调用者（图上孤立）', '?')} 个，线索非判决）· "
                f"③**没语义** {三.get('收录但抽不出符号的文件数', '?')} 个文件抽不出符号"
                f"（不等于没内容）" + 四段 + 五段)
    except Exception:                            # noqa: BLE001
        return ""


def 锚定根们() -> tuple[list[str], list[str]]:
    """从档案 `memory:` 段取 (rust 根, lean 根) —— **不写死，换项目就换档案**。

    ★ 用户原话：「**换一个项目就要重新手写？那有啥意义？**」
      ⇒ 根一律从档案读；档案缺 `lean` 声明时返回空，**不猜目录名**。
    """
    from . import profile as _prof
    p = _prof.get_profile()
    rs: list[str] = []
    lean: list[str] = []
    for d in (p.memory or []):
        k = str(d.get("kind", "")).lower()
        if k in ("rs", "rust"):
            rs += [x for x in (d.get("roots") or ()) if x not in rs]
        elif k == "lean":
            lean += [x for x in (d.get("roots") or ()) if x not in lean]
    if not rs:
        rs = list(p.source.roots or ())
    return rs, lean


def 取锚定() -> dict:
    """锚定边结果（常驻）。**不现算**（现算 3.6s）。"""
    global _锚定
    if _锚定 is not None:
        return _锚定
    try:
        if _锚定路径.is_file():
            _锚定 = json.loads(_锚定路径.read_text(encoding="utf-8"))
    except Exception:                            # noqa: BLE001
        _锚定 = None
    return _锚定 or {}


def 锚定同步(*, rust根们=None, lean根们=None, 样例: int = 8) -> dict:
    """算 A/B/C 三级锚定边并落小档。**只在同步时调。**

    ★ B/C 级**复用常驻符号表**（`.lean` theorem 53,778 条、`.rs` fn 19,511 条）——
      绝不在这里再解析一遍源码。「同一个判断写在两处，必然给出两个答案」。
    """
    global _锚定
    from . import mem_anchor as _ma
    from . import profile as _prof
    if rust根们 is None or lean根们 is None:
        rs, lean = 锚定根们()
        rust根们 = rust根们 if rust根们 is not None else rs
        lean根们 = lean根们 if lean根们 is not None else lean
    if not rust根们 or not lean根们:
        _告警.append(f"锚定跳过：档案没声明 rust 根或 lean 根（rs={rust根们} lean={lean根们}）")
        return {}
    p = _prof.get_profile()
    表 = 现存()
    if not 表.规模().get("文件"):
        _告警.append("锚定：符号表是空的 ⇒ 只算 A 级（B/C 需要符号表，不假装算过）")
        表 = None
    果 = _ma.扫锚定(p.repo_root, rust根们, lean根们,
                    跳过=set(p.source.skip or ()), 样例=样例, 表=表,
                    全仓lean名=(取分区() or {}).get("_全仓lean名"))
    _锚定 = 果
    try:
        _锚定路径.parent.mkdir(parents=True, exist_ok=True)
        临时 = _锚定路径.with_suffix(".tmp")
        临时.write_text(json.dumps(果, ensure_ascii=False), encoding="utf-8")
        临时.replace(_锚定路径)
    except OSError as e:
        _告警.append(f"锚定落档失败：{e}")
    return 果


def 锚定一行(果: dict | None = None) -> str:
    """锚定压成一行给简报用。"""
    from . import mem_anchor as _ma
    果 = 果 if 果 is not None else 取锚定()
    return _ma.锚定一行(果)


def 读数取(果: dict) -> dict:
    """从缺口/锚定结果里抽出**可比的一行数** —— 只取能跨轮对比的标量。

    ★ 只取标量、不取样例：历史要能存几千轮，存样例会让文件迅速爆掉。
    ★ 键名固定 —— 改键名等于把历史**断代**，所以键名一旦定下就不许改。
    """
    没 = (果.get("①没扫到") or {})
    连 = (果.get("②抽到了连不上") or {})
    硬 = (连.get("硬读数（符号级，分母无噪声）") or {})
    语 = (果.get("③连上了没语义") or {})
    死 = (果.get("④图上死引用") or {})
    return {
        "没扫到未进表": 没.get("差（未进表）"),
        "定义符号名": 硬.get("定义符号名"),
        "图上孤立": 硬.get("无仓内调用者（图上孤立）"),
        "连通率": 硬.get("连通率"),
        "抽不出符号的文件数": 语.get("收录但抽不出符号的文件数"),
        # ★ ④ 只有**真死引用**进历史 —— 总数里 41% 是假红（被剪枝但存在），
        #   把总数写进趋势曲线等于拿假红当演化进度。
        #   ★ 扁平化在**这一处**做（不在 `记史` 里）：`读数取` 才是那个唯一定义处，
        #     写两处就会给出两个答案（本包为此专门立过一条规矩）。
        "图上真死引用": 死.get("①真死引用（全仓都没有）"),
    }


def 锚定读数取(果: dict) -> dict:
    """锚定结果里能跨轮对比的标量（与 `读数取` 同规矩：键名一旦定下不许改）。"""
    if not 果:
        return {}
    return {
        "A级真锚定": (果.get("A级真锚定") or {}).get("边数"),
        "悬空锚定": (果.get("悬空锚定（点了一个不存在的.lean）") or {}).get("条数"),
        "在别的根里": (果.get("在别的根里（仓里有，但不在声明的 lean 根内）") or {}).get("条数"),
        "未锚定": (果.get("未锚定（有证明但没有任何代码点名）") or {}).get("文件数"),
    }


def 记史(缺口读数: dict | None = None, 锚定读数: dict | None = None,
         路径: Path | None = None) -> dict:
    """往缺口史追加**一行**（jsonl）。返回刚写的那一行。

    ★ 追加是**原子追加**（`open(..., "a")`）—— 历史不许被覆盖，覆盖就等于
      把「变好还是变坏」的证据删掉。写失败进 `_告警`，**不抛**（历史不该拖垮同步）。
    """
    路径 = 路径 or _缺口史路径
    行 = {"时刻": time.strftime("%Y-%m-%d %H:%M:%S")}
    if 缺口读数:
        行.update(缺口读数)
    if 锚定读数:
        行.update(锚定读数)
    try:
        路径.parent.mkdir(parents=True, exist_ok=True)
        with 路径.open("a", encoding="utf-8") as f:
            f.write(json.dumps(行, ensure_ascii=False) + "\n")
    except OSError as e:
        _告警.append(f"缺口史落档失败：{e}")
    return 行


def 缺口史(条: int = 30) -> list[dict]:
    """读最近 `条` 行历史（老 -> 新）。"""
    try:
        if not _缺口史路径.is_file():
            return []
        行们 = _缺口史路径.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    出: list[dict] = []
    for s in 行们[-条:]:
        try:
            出.append(json.loads(s))
        except ValueError:
            continue
    return 出


# 越大越好的键：算差值时要反号，否则「涨了」会被念成「退步」
_越大越好 = frozenset({"A级真锚定", "定义符号名", "连通率"})
# 这张表就是**信息优先级**：先报最要命的三类缺口，再报锚定
# ★ 2026-11-07（R303）加 `图上真死引用` —— ④ 那条真缺口（实测 3,655 条）
#   以前在每轮注入里**一个字都不出现**，现在进趋势对比。
_史键序 = ("没扫到未进表", "图上孤立", "连通率", "抽不出符号的文件数",
           "图上真死引用", "A级真锚定", "悬空锚定", "在别的根里", "未锚定")


def 史一行(最近: list[dict] | None = None) -> str:
    """把**最近这次与上一次的差**压成一行。**没有两条以上历史就如实说没有。**"""
    最近 = 最近 if 最近 is not None else 缺口史(2)
    if len(最近) < 2:
        return ("缺口史：**只有 %d 条记录，还看不出趋势**（章程 §3.5 要的是"
                "「数量变化」，一次读数是静态的）" % len(最近))
    新, 旧 = 最近[-1], 最近[-2]
    段 = []
    for k in _史键序:
        a, b = 旧.get(k), 新.get(k)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        d = b - a
        if abs(d) < 1e-9:
            continue
        好 = (d > 0) if k in _越大越好 else (d < 0)
        段.append(f"{k} {a:,.0f}→{b:,.0f}（{'+' if d > 0 else ''}{d:,.0f}{'✅' if 好 else '⚠'}）")
    if not 段:
        return f"缺口史（{len(最近)} 条）：**较上次无变化**"
    return f"缺口史（{len(最近)} 条）较上次：" + " · ".join(段)


def 简报(预算字: int = 1200, 目录数: int = 12) -> str:
    """给「每轮注入」用的紧凑全局状态。**只用表内数据，不读盘、不调模型。**"""
    表 = 现存()
    规 = 表.规模()
    读 = _同步读数
    行 = ["【仓况·常驻】"]
    if 读 is not None:
        行.append(f"上次同步：扫描 {读.扫描文件} · 变更 {读.变更文件} · "
                  f"戳筛跳过 {读.未变跳过} · 读盘 {读.读盘次数} · 墓碑 {读.新增墓碑}")
    行.append(f"符号表规模：文件 {规['文件']} · 符号名 {规['符号名']} · 符号条 {规['符号条']} · "
              f"引用名 {规['引用名']} · 墓碑 {规['墓碑']}")
    # ★ 拼接顺序 = **信息优先级**：摘要 ≥ 缺口 ≥ 明细。
    #   实测教训（2026-11-07，连栽两次）：缺口行原本在最后，被 `_按行截断` 优先截掉
    #   ⇒ **每轮注入里永远看不到缺口**，而缺口正是最该常驻的东西。
    #   「谁先被保下来」就是「谁更重要」—— 这个顺序不能随手写。
    有分区 = False
    明细分 = ""
    try:
        from . import mem_partition as _mp
        摘要, 明细分 = _mp.简报分节(取分区())
        if 摘要:
            # ★ 必须**拆成行**再放进去 —— `简报分节` 返回的是多行字符串。
            #   实测 bug（2026-11-07）：整段当一个元素 append ⇒ `_按行截断` 把它
            #   当**一行**算长度，立刻超预算 ⇒ 落盘只剩 269 B，而内存里正文 1,900 字。
            #   症状是「注入的仓况比打印出来的少一大截」，很隐蔽。
            行.extend(摘要.split("\n"))
            有分区 = True
    except Exception as e:                       # noqa: BLE001
        _告警.append(f"分区段取不到：{type(e).__name__}: {e}")
    一 = 缺口一行()
    if 一:
        行.append(一)
    锚 = 锚定一行()
    if 锚:
        行.append(锚)
    史 = 史一行()
    if 史:
        行.append(史)          # ★ 紧跟缺口/锚定 —— 「变好还是变坏」比「明细」要紧
    无 = 无调用者()
    行.append(f"无仓内调用者的符号：{len(无)} 个（**线索，非判决**：入口天然无调用者）")
    if 明细分:
        行.extend(明细分.split("\n"))
    # ★ 只有**没有分区**时才退回去报顶层目录树 —— 分区的明细已经带了「按顶层（文件数）」，
    #   两段都留会在预算里互相挤，实测把末尾切成半句。
    if not 有分区:
        树 = 顶层目录树(上限=目录数)
        if 树:
            行.append("顶层目录（精确到文件夹，含符号数）：")
            for d in 树:
                行.append(f"  {d['目录']} — 文件 {d['文件']} · 符号 {d['符号']}")
    return _按行截断(行, 预算字)


def _按行截断(行: list[str], 预算字: int) -> str:
    """★ **按整行**截断 —— 不许切在半句。

    实测（2026-11-07）：原来 `文[:预算字-20] + "…（截断）"` 把最后一行切成
    「顶层目」这种半句，读的人会以为信息就这样。
    **截断必须让人看得出「这里少了一整块」**，不是让人猜。
    """
    出: list[str] = []
    长 = 0
    for l in 行:
        if 长 + len(l) + 1 > 预算字:
            出.append(f"…（按预算 {预算字} 字截断，还有 {len(行) - len(出)} 行未显示）")
            break
        出.append(l)
        长 += len(l) + 1
    return "\n".join(出)


# ════════════════════════════════════════════════════════════
# 自检：增量性必须是**机验**的，不是声称的
# ════════════════════════════════════════════════════════════

def _写简报(预算字: int = 1200, 路径: Path | None = None) -> Path:
    """把当前仓况预算成**小文本**落盘 —— 供跨进程、每轮注入读取。

    `路径` 可覆盖，**只给自检用**（自检不许盖掉真档案）。
    """
    目标 = Path(路径 or _简报路径)
    目标.parent.mkdir(parents=True, exist_ok=True)
    临时 = 目标.with_suffix(".tmp")
    临时.write_text(简报(预算字=预算字), encoding="utf-8")
    临时.replace(目标)                            # 原子替换
    return 目标


def 仓况段(预算字: int = 1200) -> str:
    """给提示词注入的段 —— **这是「每轮本来就知道」的落点**。

    与 `math_model.get_charter()` 分工：charter 是档案里的**静态**机制基线，
    本段是**动态**的那个对应物（随每次同步变）。

    ★ 读的是 `_简报路径` 那个**小文本**，不载大 pickle —— 因为本函数**每轮都跑**。
    ★ 空/缺 ⇒ 返回**空串**，不编造、不占位。取不到仓况时调用方少一段背景，
      而不是拿到一段假背景。**降级要看得见** —— 所以调用方应把空串当信号。
    """
    # 同进程内已有常驻表 ⇒ 直接用（比读文件还准，刚同步过的）
    表 = 现存()
    if 表.规模().get("文件"):
        return "\n\n" + 简报(预算字=预算字)
    if not _简报路径.is_file():
        return ""
    try:
        文 = _简报路径.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not 文.strip():
        return ""
    return "\n\n" + 文[:预算字]


def _自检() -> int:
    """五条判据，全部**报出样本数**（口径：任何「通过」必须同时报出样本数 > 0）。"""
    import tempfile

    失败: list[str] = []
    读数: list[str] = []

    def 判(名: str, 条件: bool, 说明: str = "") -> None:
        if not 条件:
            失败.append(f"{名}{('：' + 说明) if 说明 else ''}")

    with tempfile.TemporaryDirectory() as td:
        根 = Path(td)
        for i in range(6):
            (根 / f"m{i}.py").write_text(
                f"def f{i}():\n    return g{i}\n\ndef g{i}():\n    return {i}\n",
                encoding="utf-8")

        表 = ms.符号表()
        读1 = 表.同步(根, 后缀=(".py",))
        判("①首次全读", 读1.读盘次数 == 6 and 读1.扫描文件 == 6 and 读1.变更文件 == 6,
           f"读盘 {读1.读盘次数} 扫描 {读1.扫描文件} 变更 {读1.变更文件}（样本 6）")
        读数.append(f"①首次：读盘 {读1.读盘次数}/{读1.扫描文件}（应 6/6）")

        # ② 无改动再同步 ⇒ 一次盘都不读（这是「不用遍历整个仓库」的**机器证据**）
        读2 = 表.同步(根, 后缀=(".py",))
        判("②增量不读盘", 读2.读盘次数 == 0 and 读2.未变跳过 == 6 and 读2.变更文件 == 0,
           f"读盘 {读2.读盘次数} 戳筛跳过 {读2.未变跳过} 变更 {读2.变更文件}")
        读数.append(f"②无改动：读盘 {读2.读盘次数} · 戳筛跳过 {读2.未变跳过}（应 0 · 6）")

        # ③ 改一个 ⇒ 只重算它
        (根 / "m3.py").write_text("def f3():\n    return 999\n", encoding="utf-8")
        读3 = 表.同步(根, 后缀=(".py",))
        判("③只碰变了的", 读3.变更文件 == 1 and 读3.未变跳过 == 5 and 读3.读盘次数 == 1,
           f"变更 {读3.变更文件} 跳过 {读3.未变跳过} 读盘 {读3.读盘次数}")
        读数.append(f"③改 1 个：变更 {读3.变更文件} · 戳筛跳过 {读3.未变跳过}（应 1 · 5）")

        # ④ 删一个 ⇒ 墓碑 +1
        (根 / "m5.py").unlink()
        读4 = 表.同步(根, 后缀=(".py",))
        判("④删了有墓碑", 读4.新增墓碑 == 1 and "m5.py" in 表.墓碑们(),
           f"新增墓碑 {读4.新增墓碑} 墓碑们 {len(表.墓碑们())}")
        读数.append(f"④删 1 个：新增墓碑 {读4.新增墓碑}（应 1）")

        # ⑤ 导出→载入：增量能力必须跨进程存活（不带 _文戳 这条必红）
        状 = 表.导出()
        表2 = ms.符号表()
        表2.载入(状)
        读5 = 表2.同步(根, 后缀=(".py",))
        判("⑤戳跨载入存活", 读5.读盘次数 == 0 and 读5.未变跳过 == 5,
           f"载入后读盘 {读5.读盘次数} 戳筛跳过 {读5.未变跳过}")
        读数.append(f"⑤载入后：读盘 {读5.读盘次数} · 戳筛跳过 {读5.未变跳过}（应 0 · 5）")

        # ⑥ 调用者判据在样本上真的会动（不是恒真）
        #    ★ 必须用**跨文件**样本：`调用者` 默认 `排除自身=True`，
        #      同文件内的引用会被滤掉 ⇒ 拿同文件样本测，红绿都看不出分辨力。
        (根 / "用0.py").write_text("def 用0():\n    return f0()\n", encoding="utf-8")
        表.同步(根, 后缀=(".py",))
        正 = 表.调用者("f0")          # 被「用0.py」引用 ⇒ 非空
        负 = 表.调用者("g0")          # g0 定义在 m0.py、只被 m0.py 自己引用 ⇒ 空
        判("⑥调用者有分辨力", 正 == ("用0.py",) and 负 == (),
           f"调用者(f0)={正}（应 ('用0.py',)）· 调用者(g0)={负}（应 ()）")
        读数.append(f"⑥调用者(f0)={正} · 调用者(g0)={负}（应 ('用0.py',) · ()）")

        # ⑦ 落盘简报必须与内存简报**逐字一致**（不许落成半截）
        #    ★ 这条是**被真 bug 逼出来的**（2026-11-07）：
        #      `简报段` 返回**多行字符串**，被当**一行** append ⇒ `_按行截断`
        #      按那一行的长度算，立刻超预算 ⇒ 落盘只剩 **269 B**，而内存正文 1,900 字。
        #      症状：**注入给模型的仓况比打印出来的少一大截** —— 不报错、不显眼、纯丢信息。
        #      「注入的内容」与「我看到的内容」不一致，是最难发现的一类缺陷，
        #      所以必须有机器判据盯着，不能靠人对比。
        #    ★ 小档必须写到**夹具树之外** —— `表.同步` 是**递归**扫 `根` 的，
        #      写进 `根` 的**任何子目录**都会被下一次同步收成样本（实测：先下移一层
        #      仍被收走，`.txt` 数成 2 而不是 1）。
        #      **测试之间通过文件系统互相污染**是很隐蔽的一类假红/假绿。
        内存 = 简报(预算字=4000)
        with tempfile.TemporaryDirectory() as td2:
            盘 = _写简报(预算字=4000,
                         路径=Path(td2) / "仓况.txt").read_text(encoding="utf-8")
        判("⑦落盘与内存逐字一致", 盘 == 内存,
           f"内存 {len(内存)} 字 vs 落盘 {len(盘)} 字"
           + ("（落盘被截短了）" if len(盘) < len(内存) else ""))
        读数.append(f"⑦落盘/内存：{len(盘)}/{len(内存)} 字（应相同）")

        # ⑦′ 截断必须**按整行**，且明说少了几行（不许切半句）
        节 = _按行截断(["甲" * 10, "乙" * 10, "丙" * 10], 25)
        判("⑦′截断按整行且报出行数",
           节.count("\n") >= 1 and "未显示" in 节 and "丙" not in 节,
           repr(节))
        读数.append(f"⑦′截断：{节.splitlines()[-1][:40]}")

        # ⑧ 三类缺口**在样本上真的会动**（不是恒 0、也不是恒真）
        #    ★ 必须造出「有符号」和「无符号」**两侧**样本 —— 只给一侧的判别器必过触发。
        (根 / "散文.txt").write_text("这是一段没有符号的散文。\n", encoding="utf-8")
        表.同步(根, 后缀=(".py", ".txt"))
        缺 = 缺口(上限=5, 表=表)
        三 = 缺["③连上了没语义"]
        二硬 = 缺["②抽到了连不上"]["硬读数（符号级，分母无噪声）"]
        判("⑧缺口③认得出无符号文件（正控）",
           三["收录但抽不出符号的文件数"] == 1
           and 三["按后缀"].get(".txt") == 1 and 三["收录文件数"] == 7,
           f"无符 {三['收录但抽不出符号的文件数']} 按后缀 {三['按后缀']} 收录 {三['收录文件数']}")
        判("⑧缺口②的硬读数与 `无调用者()` 对得上",
           二硬["定义符号名"] == 二硬["其中被**别的文件**引用过"]
           + 二硬["无仓内调用者（图上孤立）"],
           f"{二硬}")
        判("⑧缺口②带边界声明（不许把它当判决）",
           "线索" in 二硬["★这不是判决"]
           and "★这不是「缺了这么多定义」" in 缺["②抽到了连不上"]["线索（标识符级，**会淹**）"],
           "边界声明缺失")
        读数.append(f"⑧缺口：③无符 {三['收录但抽不出符号的文件数']}/{三['收录文件数']}（应 1/6）· "
                    f"②定义 {二硬['定义符号名']} · 有引用 {二硬['其中被**别的文件**引用过']} · "
                    f"孤立 {二硬['无仓内调用者（图上孤立）']}")

        # ⑨ 缺口**史**：章程 §3.5「缺口的数量变化就是演化进度」。
        #    ★ 只给一条历史必过触发（永远「看不出趋势」），所以**两侧都要造**：
        #      空史 ⇒ 「看不出趋势」；两条相反方向 ⇒ 必须一个 ✅ 一个 ⚠。
        with tempfile.TemporaryDirectory() as td史:
            史p = Path(td史) / "缺口史.jsonl"
            判("⑨空史时**如实说看不出趋势**（不许编一个 0）",
               "看不出趋势" in 史一行([]), "空史被判成有趋势")
            记史({"图上孤立": 100, "A级真锚定": 10}, 路径=史p)
            记史({"图上孤立": 80, "A级真锚定": 15}, 路径=史p)
            两 = [json.loads(s) for s in 史p.read_text(encoding="utf-8").splitlines()]
            判("⑨记史是**追加**（两行都在，不许覆盖）", len(两) == 2, f"实得 {len(两)} 行")
            一史 = 史一行(两)
            判("⑨孤立变少念 ✅、锚定变多念 ✅（方向对了才叫趋势）",
               "图上孤立 100→80（-20✅）" in 一史 and "A级真锚定 10→15（+5✅）" in 一史, 一史)
            记史({"图上孤立": 200}, 路径=史p)
            三史 = [json.loads(s) for s in 史p.read_text(encoding="utf-8").splitlines()]
            判("⑨孤立涨回去要念 ⚠（反方向也得会念）",
               "⚠" in 史一行(三史), 史一行(三史))
            判("⑨无变化的键**不出现**（不刷无意义的 0）",
               "在别的根里" not in 史一行(三史), 史一行(三史))
            读数.append(f"⑨缺口史：{史一行(两)}")

    print("  === mem_live 自检 ===")
    for r in 读数:
        print("   ", r)
    if 失败:
        print("  ❌ 失败：")
        for f in 失败:
            print("    ", f)
        return 1
    print(f"  ✅ 12/12 通过（样本 6 文件 + 1 散文 · 判据 ①–⑨）")
    return 0


if __name__ == "__main__":
    sys.exit(_自检())
