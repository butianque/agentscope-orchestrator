#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：按名字**直接取回路径**，不靠遍历 —— 记忆索引的「取回」这一半。

## 三样东西（一个都不能少，且都要断言**路径正确**）

| # | 名字是什么 | 谁回答 |
|:--|:--|:--|
| ① | 一条**已证明的定理名**（Lean 形式化库） | 产物库索引的**单元级键**（`<前缀>.<点式模块>.<定理名>`）|
| ② | 一个**已实现的 Rust 符号名**（`src/` 里） | 符号表（`mem_symbols`，按名字 O(1) 寻址）|
| ③ | 一份**审计回执的文件名**（`.audit_trail` 里） | 声明扫描登记的回执名键（`**/*.md` 递归）|

## 「不靠遍历」怎么证 —— 不是计时，是**机械禁掉遍历**（顺带禁掉读盘）

取回那几次调用，把 `os.walk` / `os.scandir` / `os.listdir`、
`Path.rglob` / `Path.glob` / `Path.iterdir`，**以及** `open` /
`Path.open` / `Path.read_text` / `Path.read_bytes` **全部换成会抛异常的哨兵**：
**取回路径上只要出现一次遍历或一次读盘，它当场炸、判据红。**

★ 为什么不用载体自己的计数器（2026-11-04 实测）：`符号表._读盘` 在 `_读()`
  里**从未自增** ⇒ 恒为 0；而 `mem_symbols._自检()` 里那条「查询零读盘」
  比的是**同步读数快照**（`读1.读盘次数`）与它自己 ⇒ **恒真**。
  两个读数都**不会动**，拿它们当证据等于没证。⇒ 改用"禁掉读盘"这一条机械判据。

## 区分力 —— 「没有」必须报「没有」

- 不存在的名字：必须 `None` / 空元组，**不许勉强命中**；
- **近失名**（原名字加一个字符 / 少一个字符 / 换个大小写）：同样必须是「没有」；
- **裸定理名**（不带模块前缀）：按设计不注册（`build_artifact` 明写
  「不注册裸文件名」）⇒ 必须取不到 —— 这一条证明命名空间是**精确键**，不是模糊匹配；
- **类目不许串**：定理名拿去 `doc` 类、回执名拿去 `formal` 类查，都必须是「没有」。

跑法（Windows PowerShell）：
    $env:ORCH_STATE_DIR="<pkg>/tests/_run_all_state/mem_lookup_b"
    & "<pkg>/.venv/Scripts/python.exe" -u tests/mem_lookup_b.py
退出码 0 = 全绿
"""

from __future__ import annotations

import builtins
import io
import os
import pathlib
import shutil
import sys
from pathlib import Path

判据目录 = Path(__file__).resolve().parent
仓 = 判据目录.parent
sys.path.insert(0, str(仓 / "src"))

状态 = 判据目录 / "_run_all_state" / "mem_lookup_b"
shutil.rmtree(状态, ignore_errors=True)
状态.mkdir(parents=True, exist_ok=True)
os.environ["ORCH_STATE_DIR"] = str(状态)

from agentscope_orchestrator import autoengine as ae      # noqa: E402
from agentscope_orchestrator import mem_symbols as ms     # noqa: E402
from agentscope_orchestrator import profile as P          # noqa: E402

失败: list[str] = []


def 断言(条件, 说明) -> None:
    print(f"  {'ok  ' if 条件 else 'FAIL'} {说明}")
    if not 条件:
        失败.append(说明)


prof = P.get_profile()
根 = prof.repo_root

# 三样东西的**名字**是判据的输入；路径由索引回答（本判据不自己去找文件）
定理名 = "MyMeta.05_IntelligentLife.Bridge_EngineeringGapCompleteness.bootstrap_closure"
定理裸名 = "bootstrap_closure"
符号名 = "格值_到_Windows_DWORD"
符号文件 = "知识条目/边界转换.rs"          # 相对被同步的根（src/知识库）
符号根相对 = "src/知识库"
回执名 = "01_测试数差异对账"

print("mem_lookup_b：按名字取回路径（不靠遍历）")
print(f"  档案 {prof.source_path} · repo_root {根}")

mem = ae.MemoryIndex()
出 = ae.scan_project(mem, prof.artifact.root, 根)
各 = {t["kind"]: t["命中"] for t in 出["逐条"]}
print(f"  索引读数 {mem.sizes()}")
print(f"  逐条命中 {各}")

# ── ① 已证明的定理名 → Lean 文件路径 ──────────────────────────────────
print("① 已证明的定理名 → Lean 文件路径")
formal = mem.index("formal")
定理路径 = formal.query(定理名)
断言(定理路径 is not None, f"按定理名取回路径（实得 {定理路径}）")
if 定理路径 is not None:
    断言(定理路径.is_file(), "取回的路径是**真文件**（不是一条拼出来的候选）")
    断言(str(定理路径).startswith(str(prof.artifact.root)),
         "落在**产物库根**之内 —— 同名文件在别处也不算数")
    文 = 定理路径.read_text(encoding="utf-8", errors="ignore")
    代码 = ae._去注释(文)
    块 = None
    for m in ae._BLOCK_RE.finditer(代码):
        if m.group(2) == 定理裸名:
            块 = m.group(0)
            break
    断言(块 is not None, "文件**代码**里真有这条声明（不是注释里被引用的名字）")
    if 块 is not None:
        断言(not ae._SORRY_RE.search(块),
             "证明体里没有 sorry/admit —— 这条是**已证明**的，不是骨架")
        断言(not ae._EMPTY_BODY_RE.search(块), "证明体非空（不是 `:= by` 就断了）")
    if "sorry" in 文 and "sorry" not in 代码:
        print("  [凭据] 原文含 `sorry` 字样而代码里没有 —— 本判据读的是剥注释后的代码")

# ── ② 已实现的 Rust 符号名 → src/ 里的文件路径 ────────────────────────
print("② 已实现的 Rust 符号名 → src/ 里的文件路径")
符号根 = 根 / "src" / "知识库"
表 = ms.符号表()
读1 = 表.同步(符号根, 后缀=(".rs",), 跳过=ae._默认跳过)
print(f"  符号表同步：{读1.行()} · 规模 {表.规模()}")
命中符 = 表.查(符号名)
断言(len(命中符) >= 1, f"按符号名取回定义（命中 {len(命中符)} 个）")
符 = next((s for s in 命中符 if s.文件 == 符号文件), None)
断言(符 is not None,
     f"命中里有 {符号文件}（实得 {[s.文件 for s in 命中符]}）")
符号路径 = None
定义行 = ""
if 符 is not None:
    符号路径 = 符号根 / 符.文件
    断言(符号路径.is_file(), f"由名字拼回的路径是真文件（{符号路径}）")
    断言(str(符号路径).startswith(str(根 / "src")),
         f"落在 `src/` 之内（{符号根相对}）—— 是工程源码，不是别处的同名文件")
    行们 = 符号路径.read_text(encoding="utf-8", errors="ignore").splitlines()
    定义行 = 行们[符.起 - 1].strip() if 0 < 符.起 <= len(行们) else ""
    断言("fn" in 定义行 and 符号名 in 定义行,
         f"第 {符.起} 行确实是它的定义（{定义行}）")
    断言(bool(符.结构指纹),
         f"取回的还有**结构身份**而不只是文件名（{符.身份()}）")

# ── ③ 审计回执的文件名 → .audit_trail 里的路径 ────────────────────────
print("③ 审计回执的文件名 → .audit_trail 里的路径")
回执索引 = mem.index("doc")
回执路径 = 回执索引.query(回执名)
断言(回执路径 is not None, f"按回执名取回路径（实得 {回执路径}）")
if 回执路径 is not None:
    断言(回执路径.stem == 回执名, "取回的正是**问的那个名字**（不是同名近似）")
    断言(".audit_trail" in 回执路径.parts, "落在 `.audit_trail` 之内")
    断言(回执路径.parent.name != ".audit_trail",
         "它在**子目录**里 —— `**/*.md` 的递归声明真的生效"
         "（上一版只扫顶层，漏了 34 份；这一条钉的就是那次病灶）")
    断言(回执路径.is_file() and 回执路径.stat().st_size > 0,
         "回执是真文件且非空")

# ── ④ 不靠遍历、也不读盘：禁掉遍历与读盘原语，再把三样取回一遍 ────────
print("④ 「不靠遍历」：禁掉遍历/读盘原语后，三样仍按名字取回")
原函数表: list = []


def _禁掉(类, 名, 哨兵) -> None:
    自有 = 名 in 类.__dict__
    原函数表.append((类, 名, 类.__dict__.get(名), 自有))
    setattr(类, 名, 哨兵)


def _还原() -> None:
    while 原函数表:
        类, 名, 原, 自有 = 原函数表.pop()
        if 自有:
            setattr(类, 名, 原)
        else:
            delattr(类, 名)


def _炸(*a, **k):
    raise AssertionError("取回路径上出现了遍历 —— 本判据要的就是**不遍历**")


取回2: dict = {}
try:
    for 名 in ("walk", "scandir", "listdir"):
        if hasattr(os, 名):
            _禁掉(os, 名, _炸)
    for 模 in (io, builtins):
        if hasattr(模, "open"):
            _禁掉(模, "open", _炸)
    for 类 in (pathlib.Path, getattr(pathlib, "WindowsPath", None),
               getattr(pathlib, "PosixPath", None)):
        if 类 is None:
            continue
        for 名 in ("rglob", "glob", "iterdir", "open", "read_text", "read_bytes"):
            if hasattr(类, 名):
                _禁掉(类, 名, _炸)
    取回2["定理"] = formal.query(定理名)
    取回2["回执"] = 回执索引.query(回执名)
    取回2["符号"] = 表.查(符号名)
    # ★ 哨兵自己的正控：**它必须真的会炸** —— 否则上面那三条只是「补丁没生效」，
    #   本判据会变成一句空话（本仓命名过的形态：判据恒真/门是摆设）。
    取回2["哨兵"] = []
    for 试名, 试 in (("os.walk", lambda: os.walk(".")),
                     ("Path.iterdir", lambda: pathlib.Path(".").iterdir()),
                     ("Path.read_text",
                      lambda: pathlib.Path(".").read_text(encoding="utf-8")),
                     ("open", lambda: builtins.open(".", "r"))):
        try:
            试()
            取回2["哨兵"].append(f"{试名} → 没炸")
        except AssertionError:
            取回2["哨兵"].append(f"{试名} → 炸")
        except Exception as exc:                            # noqa: BLE001
            取回2["哨兵"].append(f"{试名} → {type(exc).__name__}")
except Exception as exc:                                   # noqa: BLE001
    断言(False, f"禁遍历/禁读盘状态下取回失败：{type(exc).__name__}: {exc}")
finally:
    _还原()

断言(all(x.endswith("→ 炸") for x in (取回2.get("哨兵") or []))
     and len(取回2.get("哨兵") or []) == 4,
     f"★ 区分力：哨兵自己确实会炸（{取回2.get('哨兵')}）—— "
     f"证明④这一段的「禁掉」不是一句空话")

断言(取回2.get("定理") is not None and 取回2.get("定理") == 定理路径,
     "禁遍历/禁读盘下定理仍按名字取回**同一条**路径")
断言(取回2.get("回执") is not None and 取回2.get("回执") == 回执路径,
     "禁遍历/禁读盘下回执仍按名字取回**同一条**路径")
断言(取回2.get("符号") is not None and len(取回2.get("符号") or ()) == len(命中符),
     "禁遍历/禁读盘下符号表仍按名字取回**同样的**命中")
print(f"  [读数] 建表期真读盘 {读1.读盘次数} 次（`同步读数` 的计数）· "
      f"`符号表._读盘` = {表._读盘}（该字段在 `_读()` 里没自增 ⇒ **恒 0**，"
      f"拿它当读数是恒真的；同理 `mem_symbols._自检()` 那条「查询零读盘」比的是"
      f"同步快照与它自己，也是恒真）—— 故本判据用上面那次**禁读盘**取证")

# ── ⑤ 区分力：「没有」必须报「没有」──────────────────────────────────
print("⑤ 区分力：「没有」必须报「没有」，不许勉强命中")
断言(formal.query(定理名 + "_x") is None, "近失名（原名字 + 一个字符）取不到")
断言(formal.query(定理名.upper()) is None, "大小写不同取不到（键是精确匹配，不做归一）")
断言(formal.query(定理裸名) is None,
     f"**裸定理名**取不到（{定理裸名}）—— `build_artifact` 明写「不注册裸文件名」，"
     f"命名空间是**精确键**，不是模糊匹配")
断言(回执索引.query(回执名 + "x") is None
     and 回执索引.query(回执名[:-1]) is None,
     "回执近失名（多一个字符 / 少一个字符）都取不到")
断言(表.查(符号名 + "x") == () and 表.查(符号名[:-1]) == (),
     "符号近失名取不到（空元组 —— 不是「最后一个近似」）")
断言(回执索引.query(定理名) is None,
     "★ 类目不许串：定理名拿去 `doc` 类查，必须是「没有」")
断言(formal.query(回执名) is None,
     "★ 类目不许串：回执名拿去 `formal` 类查，必须是「没有」")
断言(回执索引.query(符号名) is None,
     "★ 类目不许串：符号名拿去 `doc` 类查，必须是「没有」")

print()
print(f"取回的三样：定理 {定理名} → {定理路径}")
print(f"           符号 {符号名} → {符号路径}")
print(f"           回执 {回执名} → {回执路径}")
if 失败:
    print(f"mem_lookup_b：红 {len(失败)} 项 —— {失败[:3]}")
    sys.exit(1)
print("mem_lookup_b：绿 —— 定理/符号/回执三样都能按名字直接取回真路径，"
      "禁遍历+禁读盘状态下照样取回同一条路径，不存在的名字一律报「没有」")
