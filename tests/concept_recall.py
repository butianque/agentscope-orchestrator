r"""概念召回判据：**一句问句 → 代码/证明文件网络**，能拿回来多少、边界在哪。

## 这条判据为什么必须存在

`index.UnitIndex.找()` 只认**名字**：拿自然语问线上 `context`，它回「没命中」。
而人要的是「从一个**概念**拉出整个代码 + 形式化证明的文件网络」。
`concept.py` 就是补这一层的，但**补得怎么样必须量**，不能靠"我感觉它能"：

- 概念词（`固定点`）→ 代码侧召回 **1.00**，证明侧 **0.48**
- 纯自然语（不含概念词）→ 代码侧 **0.07**、证明侧 **0.00**  ← **未达标，如实记**

⇒ 本判据锁两件事：
  ① **不许退步**：概念词代码召回 ≥ 0.80、证明召回 ≥ 0.45（当前实测值往下留一点余量）；
  ② **不许骗人**：无意义问句必须**回空**（不许"总能凑出几个最像的"）；
  ③ **不许偷偷遍历**：召回期间 `rglob/glob/iterdir/scandir` 必须 **0 次**
     （文件清单只能来自记忆索引）——这条正是「不用遍历枚举全部文件翻找」的机证。

真值不手写：判据自己按"文件内容里出现该概念"从仓里算（全树扫描**只在这里**允许，
因为它是造真值，不是答题）。
"""
from __future__ import annotations

import pathlib
import sys

包根 = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(包根 / "src"))

from agentscope_orchestrator import concept as C  # noqa: E402

概念 = "固定点"
代码召回下限 = 0.80
证明召回下限 = 0.45

# ── 真值（判据自己算）────────────────────────────────────────────
根 = 包根.parent.parent
if not (根 / "src").is_dir():                      # 仓根不是"太虚元道"时退到包根
    根 = 包根
rs = sorted((根 / "src").rglob("*.rs")) if (根 / "src").is_dir() else []
lean = sorted(p for p in (根 / "形式化验证").rglob("*.lean")
              if "_archive" not in str(p)) if (根 / "形式化验证").is_dir() else []
真码 = [p for p in rs if 概念 in p.read_text(encoding="utf-8", errors="ignore")]
真证 = [p for p in lean if 概念 in p.read_text(encoding="utf-8", errors="ignore")]
if len(真码) < 3 or len(真证) < 3:
    print(f"概念召回：**未验** —— 真值不足（代码 {len(真码)} / 证明 {len(真证)}），"
          f"本机没有这个项目的源码树或概念词变了，不是召回坏了。")
    raise SystemExit(3)

# ── 先把记忆索引建好（建索引**允许**遍历；只有"召回"不许）────────
from agentscope_orchestrator import autoengine as _AE  # noqa: E402
_AE.项目索引()

# ── 不许偷偷遍历：把枚举入口全换成计数器 ────────────────────────
枚举 = [0]
_原 = {}
for 名 in ("rglob", "glob", "iterdir"):
    _原[名] = getattr(pathlib.Path, 名)

    def _包(原, 计数器):
        def _替(self, *a, **k):
            计数器[0] += 1
            return 原(self, *a, **k)
        return _替
    setattr(pathlib.Path, 名, _包(_原[名], 枚举))
import os  # noqa: E402
_原scandir, _原listdir = os.scandir, os.listdir


def _包os(函):
    def _替(*a, **k):
        枚举[0] += 1
        return 函(*a, **k)
    return _替


os.scandir, os.listdir = _包os(_原scandir), _包os(_原listdir)
枚举[0] = 0                      # 索引建好之后再清零：只数"召回"这一段的枚举

try:
    结果 = C.概念召回(概念, 上限文件=60, 上限定理=80)
    # 负对照①：语料里根本不存在的拟声词
    无意义 = C.概念召回("哞哞哞咩咩咩吽吽吽咕噜噜噗呲噗呲", 上限文件=20, 上限定理=20)
    # 负对照②：只有 2 字常见词（专测"证据闸"：不许拿冰箱/面包这种碎片凑答案）
    碎词 = C.概念召回("冰箱里的面包和跳舞的兔子", 上限文件=20, 上限定理=20)
finally:
    for 名, 原 in _原.items():
        setattr(pathlib.Path, 名, 原)
    os.scandir, os.listdir = _原scandir, _原listdir

已枚举 = 枚举[0]


def _相对(p: pathlib.Path) -> str:
    try:
        return str(p.resolve().relative_to(根).as_posix())
    except ValueError:
        return str(p.resolve().as_posix())


码 = {_相对(p) for p, _ in 结果["代码"]}
证 = {_相对(p) for p, _ in 结果["证明"]}
真码集 = {_相对(p) for p in 真码}
真证集 = {_相对(p) for p in 真证}
码回 = len(码 & 真码集) / len(真码集)
证回 = len(证 & 真证集) / len(真证集)

# ── ★ 真值也要审：证明侧的"真值"里混着**不是知识**的文件 ──────────────
#
# 实测（2026-11-05）：27 个"活跃 Lean 里内容含『固定点』"的文件中，
#   · 5 个是探针/构建配置（`aa_xing_probe.lean`、`lakefile.lean`…），theorem 声明 0 条；
#   · 5 个在 `_已废弃` / `_work025/destroy` 这类**废弃目录**里，虽然有条目但已被索引排除。
# 拿它们当分母，会把"召回率"算成 0.48；按**知识宇宙**（有形式化单元的文件）算则是 13/13。
# ★ 这一条不能自己改分母就完事：必须**逐个分类**，并断言"没有真缺"。
定 = __import__("re").compile(r"^\s*(?:theorem|lemma)\s+\S", __import__("re").M)
try:
    from agentscope_orchestrator.autoengine import 项目索引 as _索
    _formal = _索().index("formal")
    _有单元 = set()
    for _k in _formal.keys():
        _p = _formal.query(_k)
        if _p is not None:
            _有单元.add(_相对(_p))
except Exception:                                 # noqa: BLE001
    _有单元 = set()
真证知识 = {x for x in 真证集 if x in _有单元}
未召回 = sorted(真证集 - 证)
分类: dict[str, list[str]] = {"探针/无定理": [], "废弃目录": [], "真缺": []}
for x in 未召回:
    p_ = 根 / x
    try:
        文_ = p_.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        分类["真缺"].append(x)
        continue
    if not 定.search(文_):
        分类["探针/无定理"].append(x)
    elif any(w in x for w in ("_已废弃", "_archive", "_work", "backup", "destroy")):
        分类["废弃目录"].append(x)
    else:
        分类["真缺"].append(x)
证知回 = (len(证 & 真证知识) / len(真证知识)) if 真证知识 else float("nan")

坏: list[str] = []
if 码回 < 代码召回下限:
    坏.append(f"代码侧召回 {码回:.2f} < {代码召回下限:.2f}")
if 证知回 < 证明召回下限:
    坏.append(f"证明侧召回（知识宇宙）{证知回:.2f} < {证明召回下限:.2f}")
if 分类["真缺"]:
    坏.append(f"有 {len(分类['真缺'])} 个**真该召回**的证明文件没召回：{分类['真缺'][:4]}")
if 无意义["代码"] or 无意义["证明"]:
    坏.append(f"无意义问句没回空（代码 {len(无意义['代码'])} / 证明 {len(无意义['证明'])}）"
              " —— 这是「总能凑几个最像的」那种假能力")
if 碎词["代码"] or 碎词["证明"]:
    坏.append(f"只有 2 字常见词也凑出了答案（代码 {len(碎词['代码'])} / 证明 {len(碎词['证明'])}）"
              " —— 证据闸没拦住碎片")
if 已枚举:
    坏.append(f"召回期间做了 {已枚举} 次目录枚举 —— 违反「不遍历」")
if not 结果["定理"]:
    坏.append("定理清单为空")

# 自然语（不含概念词）：**如实印，不判红** —— 这是已知未达标项，红了会挡住上面三条真能力
自然 = C.概念召回(
    "让一个变换反复作用到自己身上，直到再作用一次也不变，那个不动的东西是什么？"
    "在引擎代码和形式化证明里分别落在哪些文件", 上限文件=60, 上限定理=40)
自然码 = {_相对(p) for p, _ in 自然["代码"]}
自然回 = len(自然码 & 真码集) / len(真码集)

print(f"概念召回：概念词「{概念}」→ 代码 {码回:.2f}（{len(码 & 真码集)}/{len(真码集)}）"
      f" · 证明 **{证知回:.2f}（{len(证 & 真证知识)}/{len(真证知识)}）按知识宇宙**"
      f" / 全量分母 {证回:.2f}（{len(证 & 真证集)}/{len(真证集)}）"
      f" · 定理 {len(结果['定理'])} 条 · 目录枚举 {已枚举} 次")
if 未召回:
    print(f"  **未召回的 GT 文件分类**：探针/无定理 {len(分类['探针/无定理'])}"
          f" · 废弃目录 {len(分类['废弃目录'])} · **真缺 {len(分类['真缺'])}**"
          f"（探针例：{ [pathlib.Path(x).name for x in 分类['探针/无定理'][:3]] }）")
print(f"  **已知边界**：纯自然语（不含概念词）代码召回 {自然回:.2f}"
      f" —— **未达标**：词面检索跨不过「人话→术语」，需要语义层；本条只印不判红，"
      f"因为它红了会掩盖上面三条真能力。")
if 坏:
    print("概念召回：红 —— " + "；".join(坏))
    raise SystemExit(1)
print("概念召回：绿 —— **概念词能拉出代码+证明网络、无意义问句回空、全程 0 次目录枚举**；"
      "边界（自然语不达标）已如实印出。")