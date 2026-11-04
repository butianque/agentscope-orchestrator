# -*- coding: utf-8 -*-
"""本仓的 CI 入口：跑 judge.py，判定本仓的编码口径是否自洽。

判据两条：
  ① judge.py 跑完退出码为 0（它自己的 JSON 结论 ok=true）；
  ② judge.py 里的子进程编码口径是显式的。

退出码：0 = 全过；非 0 = 红（每条都打一行中性理由）。
子进程 stdout 落到仓内的临时文件，避免依赖宿主的临时目录口径。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

根 = Path(__file__).resolve().parent
判据 = 根 / "judge.py"
_落盘 = 根 / "_ci_judge.out"

# CI 只给子进程一份最小环境：门的结果不该随本机环境漂移。
_留 = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT",
       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "COMSPEC"}
干净环境 = {k: v for k, v in os.environ.items() if k.upper() in _留}


def 跑判据() -> tuple[int, str]:
    """按 CI 约定起 judge.py：二进制 stdout 落盘，stdin 断开，独立进程组。"""
    try:
        with _落盘.open("wb") as f:
            p = subprocess.Popen(
                [sys.executable, str(判据)],
                stdout=f, stdin=subprocess.DEVNULL, env=干净环境,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            rc = p.wait(timeout=120)
        return rc, _落盘.read_bytes().decode("utf-8", "replace")
    finally:
        _落盘.unlink(missing_ok=True)


def 末行结论(文: str) -> dict:
    for 行 in reversed([x for x in 文.strip().splitlines() if x.strip()]):
        try:
            数据 = json.loads(行)
        except Exception:
            continue
        if isinstance(数据, dict):
            return 数据
    return {}


def main() -> int:
    rc, 文 = 跑判据()
    结论 = 末行结论(文)
    if rc != 0 or not 结论.get("ok"):
        print("[FAIL] 判据未通过 rc=%d 结论=%s" % (rc, json.dumps(结论, ensure_ascii=True)))
        return 1

    源 = 判据.read_text(encoding="utf-8", errors="replace")
    if "PYTHONIOENCODING" not in 源:
        print("[FAIL] 编码口径不符")
        return 1

    print("[OK] 判据通过，编码口径自洽")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
