"""逐个跑 tests/*.py，带每文件超时，定位"卡住的是哪个"。

用途（一次性诊断，也留着以后用）：`run_all` 只有总超时，卡住时看不出是哪一支。
本脚本按文件跑，超过 `--cap` 秒就杀掉并标 `卡住`，最后给一张时间表。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
跳过 = {"run_all.py", "stress.py", "_通道.py", "per_file_timing.py"}
cap = 90
if "--cap" in sys.argv:
    cap = int(sys.argv[sys.argv.index("--cap") + 1])

文件们 = sorted(p for p in (根 / "tests").glob("*.py") if p.name not in 跳过)
print(f"逐文件跑 {len(文件们)} 支，单支上限 {cap}s")
慢: list[tuple[str, float, str]] = []
坏: list[tuple[str, str]] = []
for p in 文件们:
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, "-u", str(p)], cwd=str(根),
                           capture_output=True, text=True, timeout=cap)
        秒 = round(time.time() - t0, 1)
        状态 = "PASS" if r.returncode == 0 else f"FAIL({r.returncode})"
        if r.returncode != 0:
            坏.append((p.name, (r.stdout + r.stderr)[-600:]))
    except subprocess.TimeoutExpired:
        秒 = round(time.time() - t0, 1)
        状态 = "卡住"
        坏.append((p.name, f"超过 {cap}s 未结束"))
    print(f"  {状态:10s} {p.name:28s} {秒:6.1f}s", flush=True)
    慢.append((p.name, 秒, 状态))

print("\n按耗时排序（前 8）：")
for 名, 秒, 状态 in sorted(慢, key=lambda x: -x[1])[:8]:
    print(f"  {秒:7.1f}s {状态:10s} {名}")
if 坏:
    print(f"\n{len(坏)} 支有问题：")
    for 名, 文 in 坏:
        print(f"  --- {名} ---\n{文}")
    sys.exit(1)
print(f"\n全部 {len(文件们)} 支通过")
