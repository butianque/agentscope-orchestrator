# -*- coding: utf-8 -*-
"""装配结果的断言：顶层命令必须能被每一条需要它的门读到。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assemble import 装配  # noqa: E402
from runner import 校验门  # noqa: E402

顶层 = [sys.executable, "-c", "print('ok')"]


def test_顶层命令进入_regression_parity门():
    表 = 装配(顶层, [{"type": "regression_parity", "baseline_cmd": None, "cwd": "."}])
    门 = [x for x in 表 if x.get("type") == "regression_parity"][0]
    assert 门.get("cmd") == 顶层


def test_装配后的门表能过执行前校验():
    表 = 装配(顶层, [{"type": "regression_parity", "cwd": "."}])
    for 门 in 表:
        校验门(门)


def test_调用方显式给的_cmd_不被覆盖():
    表 = 装配(顶层, [{"type": "regression_parity",
                     "cmd": ["echo", "x"], "baseline_cmd": ["echo", "y"]}])
    assert 表[1]["cmd"] == ["echo", "x"]


def test_其他门型不受影响():
    表 = 装配(顶层, [{"type": "cmd_exit_ok", "cmd": ["echo", "hi"]}])
    assert 表[1]["cmd"] == ["echo", "hi"]


def test_顶层命令本身仍编成第一条门():
    表 = 装配(顶层, [])
    assert 表[0] == {"type": "cmd_exit_ok", "cmd": 顶层}
