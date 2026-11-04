# -*- coding: utf-8 -*-
"""把顶层命令和调用方的门装配成一份判据门表。"""
from __future__ import annotations

from typing import Iterable


def 装配(顶层cmd: Iterable[str], 门们: Iterable[dict]) -> list[dict]:
    """顶层命令编成一条 `cmd_exit_ok` 门，调用方的门补齐缺省项后并入。

    返回的表里，第 0 条永远是顶层命令编出来的那条门；其余按传入顺序排列。
    """
    顶层 = list(顶层cmd)
    表: list[dict] = [{"type": "cmd_exit_ok", "cmd": 顶层}]
    for g in 门们:
        门 = dict(g)
        if 门.get("type") == "regression_parity" and not 门.get("baseline_cmd"):
            门["baseline_cmd"] = list(顶层)
        表.append(门)
    return 表
