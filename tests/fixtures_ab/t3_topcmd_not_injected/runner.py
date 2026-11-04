# -*- coding: utf-8 -*-
"""门表的执行前校验与执行。

门型约定：
  · `cmd_exit_ok`        —— 一条命令，退出码 0 即过；
  · `regression_parity`  —— **两条**命令：`baseline_cmd`（基线）与 `cmd`（当前），
                            比对两者的结果是否一致。
"""
from __future__ import annotations

import subprocess

需要两条命令 = ("regression_parity",)


def 校验门(门: dict) -> None:
    """一条门能不能上执行台。缺命令的直接报错，不让空命令的门静默通过。"""
    型 = 门.get("type")
    if 型 in 需要两条命令:
        if not 门.get("baseline_cmd") or not 门.get("cmd"):
            raise ValueError(f"未知|{型} 需要 baseline_cmd 与 cmd 两条命令")
    elif not 门.get("cmd"):
        raise ValueError(f"未知|{型} 需要 cmd")


def 跑(门表: list[dict], cwd: str | None = None) -> list[int]:
    """逐条校验并执行（不判结果，只回收退出码）。"""
    码: list[int] = []
    for 门 in 门表:
        校验门(门)
        码.append(subprocess.run(
            [str(x) for x in 门["cmd"]], cwd=cwd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode)
    return 码
