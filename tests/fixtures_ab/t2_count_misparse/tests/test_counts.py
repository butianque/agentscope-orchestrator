# -*- coding: utf-8 -*-
"""parse_counts.计数 的断言：覆盖几种真实测试输出格式。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parse_counts import 计数  # noqa: E402


def test_键值形式_三项齐全():
    assert 计数("passed=10 failed=3 skipped=0") == {"passed": 10, "failed": 3, "skipped": 0}


def test_pytest_汇总行():
    assert 计数("10 passed, 3 failed") == {"passed": 10, "failed": 3, "skipped": 0}


def test_cargo_式_result_line():
    assert 计数("test result: ok. 121 passed; 30 failed; 0 ignored") == {
        "passed": 121, "failed": 30, "skipped": 0}


def test_点号前缀汇总():
    assert 计数("Tests: 5 passed, 2 failed") == {"passed": 5, "failed": 2, "skipped": 0}


def test_分号分隔():
    assert 计数("5 passed; 3 failed; 0 ignored") == {"passed": 5, "failed": 3, "skipped": 0}


def test_键值在前后混排():
    assert 计数("passed=10, 3 failed") == {"passed": 10, "failed": 3, "skipped": 0}


def test_键值倒序():
    assert 计数("failed=3 passed=10") == {"passed": 10, "failed": 3, "skipped": 0}


def test_键值与词形交错():
    assert 计数("failed=3, skipped=1, 8 passed") == {"passed": 8, "failed": 3, "skipped": 1}


def test_pytest_真汇总行():
    assert 计数("1 failed, 7 passed in 0.42s") == {"passed": 7, "failed": 1, "skipped": 0}


def test_零值也算出现():
    assert 计数("== 2 failed, 5 passed, 1 skipped in 0.12s ==") == {
        "passed": 5, "failed": 2, "skipped": 1}
