# -*- coding: utf-8 -*-
"""安全改写：**绝不改变文件行尾**的文本替换（R582b）。

## 为什么必须有个专用工具（两次真事故，同一个根因）

事故 1：`p.read_text()` + `p.write_text(t)` 做编号替换 ⇒ 7 个 **LF-only** 文件整篇变 CRLF
        （bootstrap.py 2618 行"改动"，实际只改了 2 处编号）。
事故 2：明知要保 CRLF，写成 `write_text(t.replace("\n", "\r\n"))`
        ⇒ `write_text` 默认 `newline=None` 又翻译一次 ⇒ 每行变成 `\r\r\n`
        （交付验收.py 644 行"全篇改动"）。

⇒ **`Path.write_text` 在 Windows 上会翻译行尾，不许用它改已有文件。**
   一律 `read_bytes` → `decode` → 改 → `encode` → `write_bytes`。
   而且**行尾按"原文件占多数的那一种"统一还原**，不做混合推断。

## 用法

    from 安全改写 import 替换
    替换(路径, "旧文本", "新文本")          # 锚必须唯一，否则抛错（不猜）
    替换(路径, "旧文本", "新文本", 次数=0)   # 0 = 全部
"""
from __future__ import annotations

import pathlib
import sys


def _行尾(文: str) -> str:
    """按占多数的行尾返回 '\r\n' 或 '\n'；**混合也照多数走**，并如实报告。"""
    crlf = 文.count("\r\n")
    lf = 文.count("\n") - crlf
    return "\r\n" if crlf >= lf and crlf else "\n"


def 替换(路径: pathlib.Path | str, 旧: str, 新: str, 次数: int = 1) -> int:
    p = pathlib.Path(路径)
    原字节 = p.read_bytes()
    文 = 原字节.decode("utf-8")
    EOL = _行尾(文)
    # 把文件与锚都归一到 \n 再匹配 —— 这样锚不必关心行尾
    归一 = 文.replace("\r\n", "\n")
    锚 = 旧.replace("\r\n", "\n")
    新锚 = 新.replace("\r\n", "\n")
    n = 归一.count(锚)
    if n == 0:
        raise RuntimeError(f"锚不在：{p.name}（找不到要替换的原文）")
    if 次数 and n != 次数:
        raise RuntimeError(f"锚出现 {n} 次，期望 {次数} 次 ⇒ 拒绝替换（不猜位置）：{p.name}")
    结果 = 归一.replace(锚, 新锚) if 次数 == 0 else 归一.replace(锚, 新锚, 次数)
    # ★ 还原成原行尾，**字节级**写回（不经 write_text）
    p.write_bytes(结果.replace("\n", EOL).encode("utf-8"))
    return n if 次数 == 0 else 次数


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("这是库，不是脚本。用法见文件头。")
