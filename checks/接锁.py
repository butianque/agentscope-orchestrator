# -*- coding: utf-8 -*-
"""把 `_互斥.占住` 注入所有「会改源码文件」的探针（R65 全称修法）。

★ 为什么用脚本而不是手改 10 个文件：
  手改 10 处 = **枚举式修法**，漏一处就重现 R65。
  本脚本从**探针源码里机器读出**它要改哪些文件，据此生成锁行
  ⇒ 新增探针只要保持同一写法，跑一次本脚本就自动接上。

★ 两种写法都认（实测都存在）：
  ① `原字节 = 目标文件.read_bytes()`
  ② `原文 = {GG: GG.read_bytes(), TS: TS.read_bytes()}`   ← 改**多个**文件

★ **多文件必须全局排序加锁**：否则 A 探针按 {GG, TS} 锁、B 探针按 {TS, GG} 锁
  就可能**死锁**（各持一把、互相等对方那把）。
  按解析后的路径**统一升序**取锁 ⇒ 不存在环。

★ 本脚本**只加锁、不改语义**：插在「读原文」之前，且幂等（已加过就跳过）。
"""
import re
import sys
from pathlib import Path

检查目录 = Path(__file__).resolve().parent
跳过 = {"_互斥.py", "全量回归.py", "接锁.py", "_看匹配.py"}
探针们 = sorted(p for p in 检查目录.glob("*.py") if p.name not in 跳过)

单文件 = re.compile(r"^\s*(\w+)\s*=\s*(\w+)\.read_bytes\(\)", re.M)
多文件 = re.compile(r"^\s*(\w+)(?:\s*:\s*[^=]+)?\s*=\s*\{([^}]*)\}", re.M)
读项 = re.compile(r"(\w+)\s*:\s*(\w+)\.read_bytes\(\)")

标记 = "# ★★ R65 互斥："

接了 = 跳了 = 无改 = 还原 = 0
for p in 探针们:
    文 = p.read_text(encoding="utf-8")
    if 标记 in 文:
        跳了 += 1
        print(f"  跳过（已接）  {p.name}")
        continue

    文件们: list[str] = []
    插入点 = None

    # ② 先试多文件（更具体）
    for m in 多文件.finditer(文):
        项 = 读项.findall(m.group(2))
        if 项:
            文件们 = [b for _a, b in 项]
            插入点 = m.start()
            break
    # ① 再试单文件
    if not 文件们:
        m1 = 单文件.search(文)
        if m1:
            文件们 = [m1.group(2)]
            插入点 = m1.start()

    if not 文件们 or 插入点 is None:
        无改 += 1
        print(f"  无源码改动    {p.name}")
        continue

    # ★ 去重 + 全局排序（防多文件探针死锁）
    文件们 = sorted(set(文件们))

    行 = [标记 + "两个进程同时改同一个源文件会**互相还原对方的改动**（实测各 7 红）。",
          "#   锁的键是**被改的文件**，不是探针名 ⇒ 新增探针自动共享同一把锁。",
          "#   ★ 多文件按**同一顺序**取锁（防两探针各持一把互相等 ⇒ 死锁）。",
          "sys.path.insert(0, str(Path(__file__).resolve().parent))",
          "from _互斥 import 占住 as _占住          # noqa: E402"]
    for i, f in enumerate(文件们):
        行.append(f"_锁{i} = _占住({f})   # 等不到就抛 占用错（绝不静默放行）")
    块 = "\n".join(行) + "\n"

    新 = 文[:插入点] + 块 + 文[插入点:]
    # ★★★ R65 安全网：**注入之后必须能解析**。插错位置（比如插进函数体里，
    #   缩进不对）会产生 `IndentationError` ⇒ 一个**语法坏的探针**留在源码里。
    #   实测踩过：第一次接锁把锁插进了 `冻结区齿探针.py` 的函数体 ⇒ 探针全废。
    #   ⇒ 写之前先 `ast.parse` 验；不过 ⇒ **还原原文**并计入 `还原`（不静默留下坏的）。
    import ast
    try:
        ast.parse(新)
    except SyntaxError as e:
        还原 += 1
        print(f"  ★ 还原        {p.name}  注入会造成语法错（{e.msg} @ 行 {e.lineno}）"
              f" ⇒ 已放弃注入、保留原文")
        continue
    p.write_text(新, encoding="utf-8", newline="")
    接了 += 1
    print(f"  已接          {p.name}  → 锁 {文件们}")

print("=" * 60)
print(f"接锁：已接 {接了} · 已跳过 {跳了} · 无源码改动 {无改} · 还原 {还原}")
# ★ R69b：`checks/` 是**产品目录** —— 里面的每个文件都必须能**自证**
#   （回归对 checks/ 的判绿口径与 src 模块一致：退出码 0 只是必要条件，
#    还必须有自证；否则"非探针"会静默混进绿里 ⇒ **无齿看起来像绿**）。
#   `接锁.py` 是"动作型"工具（它改探针源码），自证的内容是：
#   **没有留下语法坏的探针**（还原数 == 0）。
print(f"接锁 自检：{'全绿' if 还原 == 0 else '红'} "
      f"（已接 {接了} · 已跳过 {跳了} · 无源码改动 {无改} · 还原 {还原}）")
raise SystemExit(0 if 还原 == 0 else 1)
