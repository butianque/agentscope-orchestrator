# -*- coding: utf-8 -*-
"""本仓的判据入口：跑 tests/ 下的断言函数，全过才退 0。

等价于 `python -m pytest -q tests/test_counts.py`，但不依赖第三方包，
任何解释器都能当门跑。输出沿用 pytest 的汇总行（`N passed` / `M failed`）。
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

根 = Path(__file__).resolve().parent
sys.path.insert(0, str(根))
try:
    sys.stdout.reconfigure(errors="backslashreplace")
except Exception:                     # noqa: BLE001 老解释器没有 reconfigure
    pass


def 载入(文件: Path):
    规格 = importlib.util.spec_from_file_location(文件.stem, 文件)
    模块 = importlib.util.module_from_spec(规格)
    sys.modules[文件.stem] = 模块
    规格.loader.exec_module(模块)
    return 模块


def main() -> int:
    全 = 0
    失败: list[str] = []
    for 文件 in sorted((根 / "tests").glob("test_*.py")):
        模块 = 载入(文件)
        for 名 in sorted(n for n in dir(模块) if n.startswith("test_")):
            全 += 1
            try:
                getattr(模块, 名)()
            except Exception:         # noqa: BLE001 断言失败算红，继续跑下一个
                失败.append(f"tests/{文件.name}::{名}")
                print(f"FAILED tests/{文件.name}::{名}")
                print(traceback.format_exc())
    if 失败:
        print(f"{len(失败)} failed, {全 - len(失败)} passed in 0.0s")
        return 1
    print(f"{全} passed in 0.0s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
