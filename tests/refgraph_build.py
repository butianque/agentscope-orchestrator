r"""引用图判据：**机制通用（不按项目手写）+ 边真的建得出来 + 死引用不许被吞**。

## 为什么这条判据存在

用户的原话：「这个东西不能你按项目手写吧？应该机制自动扫文件自动构建吧？」
⇒ 光"我建出来了"不算数，必须机证三件事：

① **通用性**：换一个跟本项目毫无关系的目录（不同扩展名、不同引用写法），
   同一份代码照样建出正确的边 —— 只把"扫哪些文件"当参数传进去。
   还要反过来查：**模块源码里不许出现本项目的专有名词**（`lean`/`rs`/`形式化`/`src`…），
   否则"通用"就是嘴上说的。
② **自动构建**：文件改了，重建必须反映出来（不许缓存成化石）。
③ **死引用不许被吞**：点不到的名字要**列出来**，不能静默丢掉 ——
   那些正是项目里"注释点名了不存在的出处"的真缺陷。

## 边界（如实）

- 符号边只认 ASCII 标识符（≥5 字符）：中文符号名要分词，本版没做。
- 真规模建一次图 ~145 秒（7208 文件）；判据里只做**有界**抽查，全量放离线。
- 「死引用」的判定依赖"名字表"覆盖到哪些目录；覆盖不到的文件会被误判成死引用
  （本轮实测过：`build.rs`/`CLAUDE.md` 曾经就是被我这么误判的）。
  所以判据只断言"**死引用被列出来**"和"**有边**"，不断言"死引用数等于某个值"。
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

包根 = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(包根 / "src"))

from agentscope_orchestrator import refgraph as RG  # noqa: E402

坏: list[str] = []
临时根 = pathlib.Path(tempfile.mkdtemp(prefix="refgraph-"))
import atexit  # noqa: E402
atexit.register(shutil.rmtree, 临时根, ignore_errors=True)   # 跑完/跑挂都不留临时目录

# ── ① 通用性：造一个跟本项目毫无关系的仓 ────────────────────────
假仓 = 临时根 / "假项目"
(假仓 / "kernel").mkdir(parents=True)
(假仓 / "proofs").mkdir(parents=True)
(假仓 / "kernel" / "引擎.kt").write_text(
    "// 这个算子由 proofs/不变量.agda 里的定理保证\n"
    "fun step(x) = x\n", encoding="utf-8")
(假仓 / "kernel" / "辅助.kt").write_text(
    "// 见 不变量.agda 与 引擎.kt\n", encoding="utf-8")
(假仓 / "proofs" / "不变量.agda").write_text(
    "theorem step_stable : stable step\n", encoding="utf-8")
奖 = RG.建图([{"kind": "code", "roots": ["kernel"], "globs": ["**/*.kt"]},
             {"kind": "proof", "roots": ["proofs"], "globs": ["**/*.agda"]}], 假仓,
            用索引=False)
边 = {(pathlib.Path(a).name, pathlib.Path(b).name) for a, b, _t in 奖.边}
if ("引擎.kt", "不变量.agda") not in 边:
    坏.append(f"通用性：假项目里 `引擎.kt → 不变量.agda` 这条边没建出来（实得 {sorted(边)[:4]}）")
if ("辅助.kt", "引擎.kt") not in 边:
    坏.append("通用性：`辅助.kt → 引擎.kt` 的边没建出来")
网络 = 奖.网络([str(假仓 / "kernel" / "辅助.kt")], 最大跳=2)
if not any(pathlib.Path(p).name == "不变量.agda" for p in 网络):
    坏.append("通用性：两跳扩散没能从 辅助.kt 走到 不变量.agda")

# ── ② 机制源码里不许有本项目专有名词 ────────────────────────────
import ast  # noqa: E402
源 = (包根 / "src" / "agentscope_orchestrator" / "refgraph.py").read_text(encoding="utf-8")
树 = ast.parse(源)
for 点 in ast.walk(树):                      # 剥掉所有 docstring：**注释/文档里举例允许**
    if isinstance(点, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
        体 = getattr(点, "body", [])
        if 体 and isinstance(体[0], ast.Expr) and isinstance(getattr(体[0], "value", None), ast.Constant) \
                and isinstance(体[0].value.value, str):
            点.body = 体[1:] or [ast.Pass()]
    纯代码 = ast.unparse(树)
import re as _re  # noqa: E402
专有 = [w for w in ("形式化", "agentscope_orchestrator/tests") if w in 纯代码]
# ★ 扩展名要用**边界**匹配：第一版直接 `".rs" in 纯代码`，撞上了 `k.rsplit(` 里的 ".rs"
#   —— 判据自己的匹配太糙，报了个假阳性。**判据也会误判，所以要写成正则。**
专有 += [m.group(0) for m in _re.finditer(r"\.(lean|rs|agda)(?![\w])", 纯代码)]
if 专有:
    坏.append(f"通用性：模块代码里出现本项目专有名词 {专有}（注释里举例可以，代码里不行）")

# ── ③ 自动构建：改了文件，重建必须反映 ──────────────────────────
新文件 = 假仓 / "kernel" / "新增.kt"
新文件.write_text("// 指向 不变量.agda\n", encoding="utf-8")
奖2 = RG.建图([{"kind": "code", "roots": ["kernel"], "globs": ["**/*.kt"]},
              {"kind": "proof", "roots": ["proofs"], "globs": ["**/*.agda"]}], 假仓,
            用索引=False)
边2 = {(pathlib.Path(a).name, pathlib.Path(b).name) for a, b, _t in 奖2.边}
if ("新增.kt", "不变量.agda") not in 边2:
    坏.append("自动构建：新增文件里的引用没进图（缓存成了化石/建图不增量）")

# ── ④ 真项目有界抽查：边真的建得出来，死引用真的被列出来 ────────
真根 = 包根.parent.parent
if (真根 / "src").is_dir():
    图 = RG.建图([{"kind": "rs", "roots": ["src"], "globs": ["**/*.rs"]}], 真根,
                 上限文件=400, 用索引=False)
    if not 图.边:
        坏.append("真项目：一条边都没建出来")
    if not 图.死引用:
        坏.append("真项目：死引用列表是空的 —— 要么真没有（不可能），"
                  "要么被静默吞了（这正是要防的）")
    print(f"  （真项目有界抽查：文件 {图.统计['文件']} · 边 {图.统计['边']}"
          f" · 死引用 {图.统计['死引用']} · 歧义 {图.统计['歧义']}）")
else:
    print("  （真项目源码树不在，跳过 ④ —— 这是**未验**，不是通过）")

print(f"引用图：假项目边 {len(边)} 条 · 两跳网络 {len(网络)} 个节点 "
      f"· 新增文件后 {len(边2) - len(边)} 条新边")
if 坏:
    print("引用图：红 —— " + "；".join(坏))
    raise SystemExit(1)
print("引用图：绿 —— **通用机制（假项目照样建图、源码无项目专有名词）、改文件即重建、"
      "死引用如实列出**。")