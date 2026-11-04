# -*- coding: utf-8 -*-
"""重算 _gates/pristine.json（仓外门用来判「仓内判据文件有没有被改」）。

用法：python _gates/make_pristine.py
改了被钉的文件（run_tests.py / tests/*.py / runner.py）之后才需要重跑。
**不要把 FIXME.md 加进来** —— 实验时要删它。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
被钉 = [
    "t2_count_misparse/run_tests.py",
    "t2_count_misparse/tests/test_counts.py",
    "t3_topcmd_not_injected/run_tests.py",
    "t3_topcmd_not_injected/runner.py",
    "t3_topcmd_not_injected/tests/test_assemble.py",
]


def main() -> int:
    表 = {相对: hashlib.sha256((根 / 相对).read_bytes()).hexdigest() for 相对 in 被钉}
    出 = Path(__file__).resolve().parent / "pristine.json"
    出.write_text(json.dumps(表, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(出)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
