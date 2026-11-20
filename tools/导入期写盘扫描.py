# -*- coding: utf-8 -*-
"""全包扫「**导入期就写盘/建目录**」的模块级语句（R543 的同族病，按类别扫干净）。

## 为什么要扫全包而不是只修 `generic_goal`

R543 的教训：`generic_goal` 里 4 处模块级裸 `mkdir`，我第一轮只修了 3 处，
漏掉的 `BACKUP_DIR` 让整个包**导入失败**。而「部署目录不可写」是**任何**模块都可能踩的
同一条线 ⇒ 必须**按类别**扫一遍，不能只修报错的那一处。

## 判据（机判，不靠眼看）

对每个 `src/agentscope_orchestrator/*.py`：
  ① 解析 AST，只走**模块级**（不进 `FunctionDef` / `AsyncFunctionDef` / `ClassDef`）；
  ② 找出其中 `mkdir` / `write_text` / `write_bytes` / `touch` / `unlink` / `rmdir` 调用；
  ③ 再看它是否被 `try/except` 包住（模块级 `Try` 的 body 里）—— 没包住的列出来。
输出：每个文件的「模块级写盘点」，以及**未受保护**的条数。退出码 = 未受保护条数 > 0 ? 1 : 0。

★ 边界（如实）：这是**静态**判据，不执行任何模块；`import` 触发的间接写盘（比如
  某个被导入模块的模块级代码）不在本次范围内 —— 那要靠真导入验（`orch-check` 已覆盖）。
"""
from __future__ import annotations

import ast
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

包 = pathlib.Path(__file__).resolve().parents[1] / "src" / "agentscope_orchestrator"
写盘名 = {"mkdir", "write_text", "write_bytes", "touch", "unlink", "rmdir", "rename", "replace"}


def 模块级节点(树):
    """只走模块级（不进函数/类体）。"""
    def 递(节点):
        for 子 in ast.iter_child_nodes(节点):
            if isinstance(子, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            yield 子
            yield from 递(子)
    return 递(树)


def 受保护的节点(树) -> set[int]:
    """模块级 `try:` 的 body 里（含更深层）出现的节点行号 —— 那些算「有兜底」。"""
    保护: set[int] = set()
    for n in 模块级节点(树):
        if isinstance(n, ast.Try):
            for b in n.body:
                for m in ast.walk(b):
                    if hasattr(m, "lineno"):
                        保护.add(m.lineno)
    return 保护


def main() -> int:
    文件们 = sorted(包.glob("*.py"))
    print(f"══ 扫 {len(文件们)} 个模块（只看**模块级**，不进函数/类体）══")
    总 = 0
    裸: list[str] = []
    for f in 文件们:
        try:
            树 = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            print(f"  ✗ {f.name}: 语法错（跳过）：{e}")
            continue
        保护 = 受保护的节点(树)
        本 = 0
        for n in 模块级节点(树):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in 写盘名):
                本 += 1
                总 += 1
                if n.lineno not in 保护:
                    裸.append(f"{f.name}:{n.lineno}  {ast.unparse(n)[:70]}")
        if 本:
            print(f"  · {f.name}: 模块级写盘 {本} 处"
                  f"（未兜底 {sum(1 for x in 裸 if x.startswith(f.name + ':'))}）")
    print(f"\n══ 合计模块级写盘 {总} 处 · **未兜底 {len(裸)} 处** ══")
    for x in 裸:
        print(f"  ✗ {x}")
    if 裸:
        print("  口径：导入期**不许**因为不可写就崩 —— 包一层 try 并 warning，"
              "把失败推迟到真正落盘（R543）。")
        return 1
    print("  判定=OK（所有模块级写盘都在 try 里）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
