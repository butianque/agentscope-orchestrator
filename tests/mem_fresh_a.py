#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：记忆索引**随工程进度自动更新** —— 盘变了，重扫的读数必须跟着变。

## 为什么要有它

`auto_scan` 的语义是「建立/刷新统一记忆索引」：它**换一个新索引实例**
（`_MEM = MemoryIndex()`）再按档案 `memory:` 段重扫。
「刷新」一旦不成立 —— 比如某天有人把重扫换成读上一轮的数字 ——
索引就会**拿着旧数当真**，而返回值照样 `{"ok": true}`
（本仓已命名过这一类病：**一个旧数和一个当前数在返回值上长得一样**）。

⇒ 这支判据只做一件事：**改动工程里一个受控文件（新建/删除）之后，
重新扫描的读数必须跟着变：新键必须可查、删掉的键必须消失。**

## 四段

| 段 | 验什么 |
|:--|:--|
| ① | 真项目：**本轮新建的这份判据文件自己**必须在索引里（上一轮建好的旧读数里不可能有它）|
| ② | 真项目：受控探针（根目录一个 `.txt`，档案声明的「读数」类）新建/删除，键跟着有/没有 |
| ③ | 受控仓（**真档案的真声明逐字照抄**，树是我们自己的）：新建 ⇒ 命中**精确 +1**、删除 ⇒ **精确 -1** |
| ④ | 生产入口 `auto_scan` 走的确实是同一个函数（`MemoryIndex()` + `scan_project`）|

## 区分力（不是「有没有这段代码」）

- ★ 反例①：**不重扫**就取不到刚写的文件 —— 读数不是自己变的，是重扫读出来的；
- ★ 反例②：**删掉**之后重扫，命中必须**减少**、键必须消失（不是只增不减）；
- ★ 同一棵树连扫两次逐条相等 —— 「盘不动 ⇒ 读数不动」。

真项目上只断言**方向**与**键的存在性**，不断言精确 ±1：这个工程里同时可能有
别的智能体在写盘，把并发噪声读成「本判据红了」是不诚实的。精确算术放在受控仓那段。

跑法（Windows PowerShell）：
    $env:ORCH_STATE_DIR="<pkg>/tests/_run_all_state/mem_fresh_a"
    & "<pkg>/.venv/Scripts/python.exe" -u tests/mem_fresh_a.py
退出码 0 = 全绿
"""

from __future__ import annotations

import ast
import os
import shutil
import sys
from pathlib import Path

判据目录 = Path(__file__).resolve().parent
仓 = 判据目录.parent
sys.path.insert(0, str(仓 / "src"))

状态 = 判据目录 / "_run_all_state" / "mem_fresh_a"
shutil.rmtree(状态, ignore_errors=True)
状态.mkdir(parents=True, exist_ok=True)
os.environ["ORCH_STATE_DIR"] = str(状态)

from agentscope_orchestrator import autoengine as ae      # noqa: E402
from agentscope_orchestrator import profile as P          # noqa: E402

失败: list[str] = []


def 断言(条件, 说明) -> None:
    print(f"  {'ok  ' if 条件 else 'FAIL'} {说明}")
    if not 条件:
        失败.append(说明)


prof = P.get_profile()
声明 = tuple(getattr(prof, "memory", ()) or ())
根 = prof.repo_root

print("mem_fresh_a：记忆索引随工程进度自动更新")
print(f"  档案 {prof.source_path}")
print(f"  repo_root {根} · 产物库 {prof.artifact.root}")
断言(bool(声明), f"档案声明了 `memory:` 段（{len(声明)} 条）—— 没声明就没有可扫的范围")

# 受控探针：根目录一个 `.txt`，真档案里对应的声明是 `kind: 读数 / roots: [.] / *.txt`
#   （顶层不递归 ⇒ 扫它最便宜，且它不属于任何"事实来源"类，写删最不打扰别人）
探针 = 根 / "_probe_mem_fresh_a.txt"
探针名 = 探针.name
探针.unlink(missing_ok=True)          # 扫掉上一次可能留下的残留


def 扫项目():
    """一次**真重扫**（与 `auto_scan` 里那一步同一个函数），返回 (索引, 逐条命中)。"""
    mem = ae.MemoryIndex()
    出 = ae.scan_project(mem, prof.artifact.root, 根)
    数 = {t["kind"]: t["命中"] for t in 出["逐条"]}
    return mem, 出, 数


# ── ① 真项目：本轮新建的受控文件（本判据自己）必须在索引里 ────────────
print("① 真项目：**本轮新建**的受控文件必须在索引里（旧读数不可能有它）")
本判据 = Path(__file__).resolve()
mem0, 出0, 数0 = 扫项目()
print(f"  重扫读数：{mem0.sizes()}")

试键: list[str] = [本判据.stem]
for 基 in (根, 根.resolve()):
    try:
        试键.append(本判据.relative_to(基).as_posix())
    except ValueError:
        continue
py索引 = mem0.index("py")
命中本判据 = None
for k in 试键:
    q = py索引.query(k)
    if q is not None:
        命中本判据 = q
        break
断言(命中本判据 is not None and 命中本判据.resolve() == 本判据,
     f"按名字能取回本轮新建的这份判据（试过 {试键}，实得 {命中本判据}）—— "
     f"它是刚才才落到盘上的，旧读数里不可能有它")

# ── ② 真项目：受控探针 新建 ⇒ 有 / 删除 ⇒ 没有 ────────────────────────
print("② 真项目：受控探针（根目录一个 .txt）新建/删除，索引里的键必须跟着有/没有")
读数索引 = mem0.index("读数")
断言(读数索引.query(探针名) is None,
     f"探针不在盘上时索引里也没有它（`{探针名}` 对 `读数` 类取不到）")
try:
    探针.write_text("mem_fresh_a 受控探针\n", encoding="utf-8", newline="")
    断言(读数索引.query(探针名) is None,
         "★ 反例①：**不重扫**就取不到刚写的探针 —— 旧读数不会自己更新")
    mem1, 出1, 数1 = 扫项目()
    有 = mem1.index("读数").query(探针名)
    断言(有 is not None and 有.resolve() == 探针.resolve(),
         f"重扫后探针键出现且指回它自己（实得 {有}）")
    断言(数1.get("读数", 0) >= 数0.get("读数", 0) + 1,
         f"「读数」类命中 ≥ 基线 +1（{数0.get('读数')} → {数1.get('读数')}）")
finally:
    探针.unlink(missing_ok=True)
mem2, 出2, 数2 = 扫项目()
断言(mem2.index("读数").query(探针名) is None,
     "删掉探针并重扫后，这个键必须消失")
断言(数2.get("读数", 0) < 数1.get("读数", 0),
     f"★ 反例②：删掉之后命中必须**减少**（{数1.get('读数')} → {数2.get('读数')}）"
     f" —— 读数不是只增不减")
断言(not 探针.exists(), "受控探针已清干净（本判据不留残留）")

# ── ③ 受控仓：真档案的真声明逐字照抄，做精确算术 ─────────────────────
print("③ 受控仓：把真档案的 `memory:` 段**逐字**拿来扫我们自己的树，做精确算术")
仓根 = 状态 / "受控仓"
shutil.rmtree(仓根, ignore_errors=True)
for 条 in 声明:
    for r in (条.get("roots") or ["."]):
        (仓根 / r).mkdir(parents=True, exist_ok=True)

种子 = {
    "src/既有.rs": "pub fn 既有函数() -> u8 { 0 }\n",
    "多智能体编排/agentscope_orchestrator/src/既有.py": "x = 1\n",
    "多智能体编排/agentscope_orchestrator/tools/既有工具.py": "y = 2\n",
    "种子.md": "# 种子\n",
    ".audit_trail/既有回执.md": "# 回执\n",
    "多智能体编排/agentscope_orchestrator/receipts/既有回执.md": "# 回执二\n",
    "读数_既有.txt": "0.1\n",
    "多智能体编排/agentscope_orchestrator/任务书_未闭合.md": "待办：未闭合\n",
}
for 相对, 文 in 种子.items():
    p = 仓根 / 相对
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(文, encoding="utf-8", newline="")


def 扫仓():
    mem = ae.MemoryIndex()
    出 = ae.scan_declared(mem, 声明, 仓根)
    数 = {t["kind"]: t["命中"] for t in 出["逐条"]}
    return mem, 出, 数


memA, 出A, 数A = 扫仓()
memB, 出B, 数B = 扫仓()
断言(出A["声明了却没扫到"] == [],
     f"受控仓里每一条声明的根都在（缺根 {出A['声明了却没扫到']}）")
断言(出A["declared"] and all(v > 0 for v in 数A.values()),
     f"**每一种**声明形状都命中了（{数A}）—— 换项目=换声明，这里把声明的形状全跑了一遍")
断言(数A == 数B,
     f"同一棵树连扫两次逐条相等（{数A}）—— 盘不动 ⇒ 读数不动")

新增 = 仓根 / "src" / "新增_受控.rs"
新增键 = "src/新增_受控.rs"
新增.write_text("pub fn 新增函数() -> u8 { 1 }\n", encoding="utf-8", newline="")
断言(memA.index("rs").query(新增键) is None,
     "★ 反例①：新建之后**不重扫**，旧索引里取不到它")
memC, 出C, 数C = 扫仓()
新键路径 = memC.index("rs").query(新增键)
断言(新键路径 is not None and 新键路径.resolve() == 新增.resolve(),
     f"重扫之后新键出现且指回它自己（实得 {新键路径}）")
断言(数C.get("rs", 0) == 数A.get("rs", 0) + 1,
     f"新建 ⇒ `rs` 命中**精确 +1**（{数A.get('rs')} → {数C.get('rs')}）")
新增.unlink()
memD, 出D, 数D = 扫仓()
断言(memD.index("rs").query(新增键) is None,
     "★ 反例②：删掉之后重扫，键必须消失")
断言(数D.get("rs", 0) == 数A.get("rs", 0),
     f"删除 ⇒ `rs` 命中**精确回到基线**（{数C.get('rs')} → {数D.get('rs')}）")

# ── ④ 生产入口走的是同一个函数 ────────────────────────────────────────
print("④ 生产入口：`auto_scan` 体内调的就是 `MemoryIndex()` + `scan_project(...)`")
_mcp = 仓 / "src" / "agentscope_orchestrator" / "taixu_mcp.py"
树 = ast.parse(_mcp.read_text(encoding="utf-8"))
函数 = [n for n in ast.walk(树)
        if isinstance(n, ast.FunctionDef) and n.name == "auto_scan"]
体内名 = {n.id for f in 函数 for n in ast.walk(f) if isinstance(n, ast.Name)}
断言(bool(函数) and "MemoryIndex" in 体内名 and "scan_project" in 体内名,
     f"`auto_scan` 体内真出现 MemoryIndex/scan_project（找到 {len(函数)} 个同名函数，"
     f"体内名 {sorted(体内名 & {'MemoryIndex', 'scan_project'})}）—— "
     f"本判据测的就是生产入口走的那条路")

print()
if 失败:
    print(f"mem_fresh_a：红 {len(失败)} 项 —— {失败[:3]}")
    sys.exit(1)
print("mem_fresh_a：绿 —— 真项目上本轮新建的文件能按名字取回、受控探针建/删后"
      "键与命中跟着变（不重扫取不到 / 删掉必减少），受控仓里精确 ±1、同树两扫相等，"
      "生产入口确认走同一函数")
