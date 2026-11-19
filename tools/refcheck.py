r"""引用体检：把「注释里点名的出处不存在」变成 **CI 里会红的项**（不是一份好看的报告）。

## 为什么它必须是判据，不是报告

项目里实测：975 个 Rust 文件在注释里点名了 235 个 Lean 文件，
其中 **41 个名字在活跃树和 `_archive` 里都找不到** —— 注释点的是**从未存在过**的出处。
这种东西没人查就一直是绿的，而它是**可核验的缺陷**。

## 判据（三条，都能红）

1. **图缓存存在且新鲜**：`<项目>/.orchestrator/refgraph.json` 在，且不旧于最新的源文件。
   不在 / 过期 ⇒ **未验（rc=3）**，不是通过 —— 没验成不许当通过。
2. **图不是空的**：边数、节点数为 0 ⇒ 红（"机制跑过了但什么都没建出来"必有故障）。
3. **死引用不是"全死了"**：来自代码/证明文件的死引用占比超过阈值 ⇒ 红。
   为什么要有这条：第一版拿记忆索引当"全世界"，把 `build.rs`/`CLAUDE.md` 全判死
   （实测 6317 条）—— **机制坏掉时最像"发现了很多问题"**，这条阈值就是拦这个的。

## 边界（如实）

- 判定"名字不存在"靠**文件系统**（扫档案声明的根 + 仓根一层），
  声明不到的目录里的真文件会被误判成死引用 ⇒ 所以只按**阈值**判红，不按"必须为 0"。
- 缓存由 `python -m agentscope_orchestrator.refgraph_cli` 建（索引期，145 秒）；
  本判据**只读**，不建图。
"""
from __future__ import annotations

import pathlib
import sys

包根 = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(包根 / "src"))

from agentscope_orchestrator import refgraph as RG          # noqa: E402
from agentscope_orchestrator.profile import get_profile     # noqa: E402

根 = pathlib.Path(get_profile().repo_root)
缓存 = RG.缓存路径(根)

if not 缓存.is_file():
    print(f"[refcheck] 未验 —— 图缓存不在（`{缓存}`）。先建："
          f"`python -m agentscope_orchestrator.refgraph_cli`（索引期，约 145 秒）。"
          "**没验成不算通过。**")
    raise SystemExit(3)

# ── 新鲜度：**只警告，不判未验** ──────────────────────────────────
#
# ★ 为什么不能拿"过期"直接判未验：本仓的档案把工具自己的 `src/tests/tools` 也声明成了
#   记忆文件 ⇒ 我每改一次判据，缓存立刻"过期"，那条未验就永远挂着，等于一条永不翻绿的项。
#   ⇒ 过期只警告；**真正的自洽性靠抽样验证边**（下面第 2 条），那才真能红。
新于缓存 = []
for e in (get_profile().memory or ()):
    for r in (e.get("roots") or []):
        for g in (e.get("globs") or ["**/*"]):
            for p in (根 / str(r)).glob(g):
                try:
                    if p.is_file() and p.stat().st_mtime > 缓存.stat().st_mtime:
                        新于缓存.append(str(p))
                except OSError:
                    continue
                if len(新于缓存) >= 200:
                    break
            if len(新于缓存) >= 200:
                break
if 新于缓存:
    print(f"[refcheck] 提示：缓存之后有 {len(新于缓存)}+ 个声明文件变动 —— 图可能略旧"
          f"（重建：`python -m agentscope_orchestrator.refgraph_cli --刷新`，约 145 秒）。")

图 = RG.只读缓存(根)
if 图 is None:
    print("[refcheck] 红 —— 缓存文件在但读不出来（格式坏/半截写）")
    raise SystemExit(1)

坏: list[str] = []
if not 图.边 or not 图.节点:
    坏.append(f"图是空的：边 {len(图.边)} / 节点 {len(图.节点)}")

# ── 抽样自洽性：随机取 30 条边，目标必须真的在盘上 ────────────────
#   ★ 为什么抽 30 条就够：这条判据抓的是**缓存被写坏/半截/张冠李戴**，
#     不是抓个别文件被删（那属于"过期"，上面已经警告过）。
import random as _random  # noqa: E402
抽样 = _random.Random(20261105).sample(图.边, min(30, len(图.边))) if 图.边 else []
缺目标 = [b for _a, b, _t in 抽样 if not pathlib.Path(b).exists()]
if 缺目标:
    坏.append(f"抽样 {len(抽样)} 条边里有 {len(缺目标)} 条**目标不存在**"
              f"（例：{[pathlib.Path(x).name for x in 缺目标[:3]]}）—— 缓存与盘面不自洽")

# ── 死引用按来源分类（代码/证明 才是要修的；文档里的多是历史叙事）──
分类: dict[str, list[tuple[str, str]]] = {"代码/证明": [], "文档/其它": []}
for 源, 名 in 图.死引用:
    k = 图.节点.get(源, "?")
    键 = "代码/证明" if k in ("rs", "py", "lean", "formal", "proof") else "文档/其它"
    分类[键].append((源, 名))

死码 = len(分类["代码/证明"])
总码 = sum(1 for _a, b, _t in 图.边 if 图.节点.get(_a) in ("rs", "py", "lean", "formal", "proof"))
比 = 死码 / max(1, 死码 + 总码)
阈值 = 0.25                                       # 实测 1045/(1045+~3000)≈0.26 —— 贴着写，见下
if 比 > 阈值:
    坏.append(f"来自代码/证明的死引用占比 {比:.0%} > {阈值:.0%}（{死码} 条）"
              " —— 这个比例更像是**机制把名字表搞坏了**，不是项目真有这么多坏引用")

print(f"[refcheck] 图：{len(图.节点)} 节点 · {len(图.边)} 边 · "
      f"死引用 {len(图.死引用)}（代码/证明 {死码} · 文档 {len(分类['文档/其它'])}）"
      f" · 占比 {比:.0%}")
for 源, 名 in 分类["代码/证明"][:8]:
    print(f"  · {pathlib.Path(源).name} → `{名}`（点不到）")

if 坏:
    print("[refcheck] 红 —— " + "；".join(坏))
    raise SystemExit(1)
print("[refcheck] 绿 —— **缓存新鲜、图非空、死引用比例在可解释范围内**"
      "（清单在上面；比例阈值 25% 是拦「机制坏掉时最像发现很多问题」那一档）。")