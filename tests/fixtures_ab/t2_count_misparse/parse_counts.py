# -*- coding: utf-8 -*-
"""从一段测试输出里解析 passed / failed / skipped 三项计数。"""
from __future__ import annotations

import re

# 「数字 + 指标名」，例：`10 passed` / `30 failed`
_词形 = re.compile(r"(\d+)\s+(passed|failed|skipped)")

# 「指标名 + 数字」，例：`passed=10` / `failed: 3`
_键形 = [
    ("passed", re.compile(r"passed\s*=\s*(\d+)")),
    ("failed", re.compile(r"failed\s*=\s*(\d+)")),
    ("skipped", re.compile(r"skipped\s*=\s*(\d+)")),
]


def 计数(文: str) -> dict:
    """返回 {'passed': int, 'failed': int, 'skipped': int}；没出现的项记 0。"""
    出 = {"passed": 0, "failed": 0, "skipped": 0}

    命中 = _词形.findall(文 or "")
    if 命中:
        for 数, 键 in 命中:
            出[键] = int(数)
        return 出

    for 键, 模式 in _键形:
        m = 模式.search(文 or "")
        if m:
            出[键] = int(m.group(1))
    return 出
