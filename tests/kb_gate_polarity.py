#!/usr/bin/env python
"""⑦ 知识库转出来的门，**方向必须是人的意思**（tests/kb_gate_polarity.py）

## 为什么单开一支判据

这不是「多修了一个 bug」。它是**同一支判据里"症状"和"要求"被机器换了个位置** ——
而这一次换的是**整个知识库的方向**。

真库 16 条规则，改之前实测：

```
门数 98 · 极性分布 Counter({'grep_required': 97, 'grep_forbidden': 1})
```

**97/98 都是「产物里必须出现」** —— 而知识库写的几乎全是禁令。
最荒唐的一条：规则 `口径-通用工具源码不许出现专有名词`（名字里就写着"不许出现"）
产出了一道 `grep_required` 门，**要求产物里出现它自己的症状**
`工具在无档案环境下导入失败`。

⇒ 后果分两层，一层比一层坏：

· **门是反的**：带病产物**恰好满足**它，修好的产物反而红。
  这比没有门坏得多 —— 它会把"唯一正确的写法"拦住。
· 它**同时解释了第224轮那个悬案**：「门集 10 条里 8 条无关门
  ⇒ **任何产物都必然红**」—— 根因就是这些靠猜归堆的模式。

## 三个真因（各修一处，各钉一条）

| # | 真因 | 实测证据 | 修法 |
|:--:|:--|:--|:--|
| ① | **没有祈使词时按条目级极性猜** | 36 个片断无祈使词，全是症状/证据（`PermissionError` / `WinError 5` / `want staged, got error` / `分类=None` / `precondition`） | 判不了 ⇒ **不产门**，记进 `未转门()` |
| ② | **「就近」没有距离上限** | 隔了 3 个句号的 `必须` 仍然管到一个症状片断（`跑一个` / `print`） | 祈使词**只管自己那一句**（`。；！？` 换行为界，`，` 不算） |
| ③ | **`grep_required` 铺到整个 glob** | 真仓 55/97 道必红，**全部**来自 4 条"范围铺开"的规则；单文件门全绿 | 铺到 >1 个文件 ⇒ **不产门**，记进 `未转门()` |

修完实测：**98 道门 → 37 道**，极性 `97:1` → `36:1`（36 禁 + 1 必须），
真仓上 **55 红 → 6 红**，而 6 红 **6/6 是裸词误报**（见下）。

## 还有一层：裸词禁令**只标不丢**

剩下那 6 道红门，逐条查过，**没有一条是真违规**：

```
规则正文 `**永不用 `setdefault`**` ⇒ 片断只有裸词 `setdefault`
  命中 globals().setdefault(…) / acc.setdefault(gk, {…}) / os.environ.setdefault("TAIXU_DB", …)
       —— dict 的 setdefault / 另一个环境变量，与"给子进程钉编码"无关
规则正文 `PYTHONIOENCODING` ⇒ 命中 env = dict(os.environ, PYTHONIOENCODING="utf-8", …)
       —— **那恰恰是规则要求的合规写法**，只是"提到了"这个名字
```

★ 口径：**一个裸标识符当禁令，等于"这个词不许出现"** ——
  而"不出现"和"不做那件事"是两回事。

★ 为什么**只标不丢**：丢掉就把这条禁令的真价值也丢了（确实存在"用 setdefault
  钉编码"这种错写法）。而"该写具体"这件事**机器判不了** —— 只有人知道那句
  禁令的上下文。⇒ 如实报进 `未转门()`。

★ 与"极性判不了就不猜"的分界：那里是**判不了所以不产门**；
  这里是**能判、但判得比人想说的宽** ⇒ 产物照出，把偏差标出来。

## 判据守的是什么

**不是"门变少了"，是"方向对了"** —— 所以下面第 ⑥ 段是**反向断言**：
一条写得好的规则**必须仍然产出 `grep_required`**。
只测"症状不再变要求"的话，把整个机制砍成产 0 道门也能通过。
"""
import os
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

桶 = Path(tempfile.mkdtemp(prefix="kbgp_"))
# ★ 兜底：判据中途失败/被掐也要收掉，不然它会留在包根（实测攒过 85 个）
atexit.register(shutil.rmtree, 桶, ignore_errors=True)
os.environ["ORCH_STATE_DIR"] = str(桶 / "state")
os.environ["ORCH_KB_DIR"] = str(桶 / "kb")          # ★ 覆盖，不是 setdefault

from agentscope_orchestrator import project_kb as KB  # noqa: E402

失败: list[str] = []
无区分力: list[str] = []


def 断言(条件, 说明: str) -> None:
    print(f"   {'ok  ' if 条件 else 'FAIL'} {说明}")
    if not 条件:
        失败.append(说明)


def 无区分力注(说明: str) -> None:
    """★ 机制"没走到"和"走错了"是两件事 —— 前者要如实标出来，**不许静默当通过**。"""
    print(f"   [未验] {说明}")
    无区分力.append(说明)


def 净() -> None:
    """清空库，让每段只受自己写进去的规则影响。"""
    for r in KB.查():
        try:
            KB.删(r["id"])
        except Exception:
            pass


def 单门(正文: str, *, 类别: str = "规则", 强度: str = "必须",
        范围=None, 仓=None, 文件: str = "") -> list[dict]:
    return KB.门([{"id": "试", "类别": 类别, "强度": 强度, "正文": 正文,
                  "范围": 范围 if 范围 is not None else ["src/**/*.py"]}],
                仓=仓, 文件=文件)


print("① ★ 判不了极性 ⇒ **不产门**（不许按条目级极性猜）")
净()
# 条目强度=必须，正文却**只是描述症状**（无祈使词）——
# 旧算法会把它当成"必须出现 PermissionError"，正好把病当药。
门s = 单门("症状：临时目录不可写，抛 `PermissionError`、`WinError 5`")
断言(门s == [], f"无祈使词的正文 ⇒ 一道门都不产（实得 {len(门s)} 道）")
未 = KB.未转门().get("极性判不了") or []
断言(any("PermissionError" in str(x) for x in 未),
     f"并**如实报出丢掉了什么**（{len(未)} 条：{str(未)[:70]}）—— "
     f"只报产出，看的人会以为'知识库就这么多门'")
断言(not any("grep_required" == g.get("type") and "PermissionError" in str(g.get("patterns"))
             for g in 门s),
     "**绝不能**把症状当成'必须出现'（那等于要求产物里出现 bug）")

print("② ★ 祈使词**只管自己那一句**（不许跨句）")
净()
门s = 单门("判据的运行环境必须由判据自己决定。另外：工具自己 `print` 时先钉编码。"
          "三轮实测：① `tools/x.py` 崩掉；② 套件齐红，根因是 `跑一个` 用了它")
断言(门s == [], f"隔了句号的 `必须` **不许**管到后面的片断（实得 {len(门s)} 道）—— "
                f"'就近'没有距离上限时，3 个句号之前的祈使词也能管到症状")
未 = KB.未转门().get("极性判不了") or []
断言(any("print" in str(x) or "跑一个" in str(x) for x in 未),
     f"这些片断被如实记为'判不了'（{str(未)[:70]}）")

print("③ ★ 症状区 vs 修法区：**机制自己落在正确的边界上**")
净()
门s = 单门("症状：报 `分类=None`、`门数=0`。修法：一律给 `门s=[] if 门s is None else 门s`")
该类型 = {str(g.get("patterns")[0]): g.get("type") for g in 门s}
断言("分类\\=None" not in str(该类型) and "门数\\=0" not in str(该类型),
     f"症状里的 `分类=None` / `门数=0` **没进任何门**（实得 {list(该类型)[:3]}）")
断言(any("grep_required" == t for t in 该类型.values()),
     f"而修法区（带「一律」）**照常产出 grep_required**（{list(该类型.values())}）—— "
     f"不需要去猜小标题，祈使词自己分开了这两区")

print("④ ★ `grep_required` 铺到多个文件 ⇒ 不产门（量词错了）")
净()
仓 = Path(tempfile.mkdtemp(prefix="kbgp_r_"))
# ★ 兜底：判据中途失败/被掐也要收掉，不然它会留在包根（实测攒过 85 个）
atexit.register(shutil.rmtree, 仓, ignore_errors=True)
(仓 / "src").mkdir(parents=True, exist_ok=True)
for 名 in ("a.py", "b.py", "c.py"):
    (仓 / "src" / 名).write_text("x = 1\n", encoding="utf-8", newline="")
门s = 单门("一律用它 `门s=[] if 门s is None else 门s`", 仓=仓)
断言(门s == [], f"'必须出现'铺到 3 个文件 ⇒ **不产门**（实得 {len(门s)} 道）—— "
                f"'这 3 个文件每个都必须含它'几乎从来不是人想说的那句话")
未 = KB.未转门().get("必须出现却铺到多文件") or []
断言(bool(未), f"并如实报出（{str(未)[:70]}）")
# ★ 反向：指名一个文件 ⇒ 照常出（人已经把它收敛到一个产物上了）
门s2 = 单门("一律用它 `门s=[] if 门s is None else 门s`", 仓=仓, 文件="src/a.py")
断言(len(门s2) >= 1,
     f"**给了 `文件=` 就照常出**（实得 {len(门s2)} 道）—— "
     f"判据要有区分力，不能把这条口径做成'凡是 required 全砍'")
# ★ 反面：`grep_forbidden` 铺到多个文件**天然成立**（"全都别做"是对的）
门s3 = 单门("禁止 `setdefault`", 强度="禁止", 仓=仓)
断言(len(门s3) >= 1,
     f"而**禁令**铺到多个文件照常出（实得 {len(门s3)} 道）—— "
     f"两种极性的量词语义本来就不一样，别一起砍")

print("⑤ ★ 裸词禁令**标出来**（不丢，只标）")
净()
门s = 单门("禁止 `setdefault`", 强度="禁止")
裸 = KB.未转门().get("裸词禁令（过宽，建议写具体）") or []
断言(bool(裸), f"裸标识符当禁令 ⇒ 如实报'过宽'（{str(裸)[:70]}）—— "
              f"实测真仓 6 道红门 6/6 是裸词误报，其中一道命中的还是**合规写法**")
断言(len(门s) >= 1, f"但门**照出**（实得 {len(门s)} 道）—— "
                   f"丢掉就把这条禁令的真价值也丢了，而'该写具体'机器判不了")
# ★ 反向：带上下文的禁令**不许**被标成裸词（否则标注自己就是噪声）
净()
单门("禁止 `环境.setdefault(\"PYTHONIOENCODING\",\"utf-8\")`", 强度="禁止")
裸2 = KB.未转门().get("裸词禁令（过宽，建议写具体）") or []
断言(not 裸2, f"带上下文的禁令**不被标**（实得 {裸2}）—— 标注自己不许是噪声")

print("⑥ ★ 反向：写得好的规则**必须仍然产门**（不然'砍成 0 道'也能过）")
净()
门s = 单门("刻度一律用 `kHz`，禁止按数量级自动切换 `Hz/MHz`", 范围=["plot/*.py"])
类 = [(str(g.get("patterns")[0]), g.get("type")) for g in 门s]
断言(len(门s) == 2, f"一条'必须 A 且禁止 B'的规则产出**两道门**（实得 {len(门s)}）")
断言(any("required" in t for _, t in 类) and any("forbidden" in t for _, t in 类),
     f"且极性**一正一反、各管各的**（{类}）")

print("⑦ ★ 真种子库：门少了，但**方向对了**、且不产症状门")
净()
KB.播种()
门s = KB.门()
真 = [(str(g.get("patterns")[0]), g.get("type")) for g in 门s]
断言(len(门s) >= 1, f"随包种子仍产出门（{len(门s)} 道）—— 修法是'纠正方向'，不是'清空'")
症状词 = ("PermissionError", "WinError", "跑一个", "分类=None", "precondition",
          "regressionparity", "want staged")
撞 = [p for p, _ in 真 if any(w in p for w in 症状词)]
断言(not 撞, f"**没有一道门要求产物里出现症状串**（撞上的：{撞[:3]}）—— "
             f"改之前这正是 97/98 道门的形态")
种类 = {t for _, t in 真}
断言(len(种类) >= 2,
     f"两种极性**都还在**（{sorted(种类)}）—— 只留一种说明砍过头了")
print(f"   真种子库门数 {len(门s)}（改之前 98 道、其中 97 道是 grep_required）")

净()
if 无区分力:
    print(f"\n★ 没有区分力的项：{len(无区分力)} —— {无区分力[0]}")
if 失败:
    print(f"\n知识库极性判据：红 {len(失败)} 项 —— {失败[0]}")
    sys.exit(1)
print("\n知识库极性判据：绿 —— 极性判不了就不猜、祈使词不跨句、症状不当要求、"
      "必须出现不铺多文件、裸词禁令如实标出、好规则照常产门")
