r"""夹具校准判据：`tools/bench_ab.py --selftest` 必须全过。

★ 为什么它必须是套件里的一支（2026-11-02 被自己抓到过）：
  基准的两臂都靠"同一条仲裁命令"判成败。夹具一旦写错（正确解也过不了、
  或坏种本来就绿），整张 A/B 表测的就是噪声 —— 而那种表**看起来特别像证据**。
  实测抓到两个真夹具缺陷：
    · T7 的坏种其实绿着（Python 里 `[1.0,2.0]==[1,2]` 成立，断言测不出东西）
    · T7 的主命令把回归测试也跑了 ⇒ 裸跑臂"看得见"回归，陷阱失效
  ⇒ 夹具校准进套件，改夹具改歪了就红。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
出 = subprocess.run([sys.executable, "tools/bench_ab.py", "--selftest"],
                    cwd=str(根), capture_output=True, text=True, timeout=300,
                    encoding="utf-8", errors="replace")
文 = (出.stdout or "") + (出.stderr or "")
print(文.strip()[-1500:])

坏 = []
if 出.returncode != 0:
    坏.append(f"夹具自检 rc={出.returncode}")
if "全过" not in 文:
    坏.append("没有出现「全过」结论")
if "[FAIL]" in 文:
    坏.append("有夹具判定为坏种绿/正确解红")
# 任务数量：新加任务必须同步给"正确解"，否则自检会 KeyError
if "（11 个任务）" not in 文:
    坏.append("任务数与预期不符（当前应为 11）")

if 坏:
    print("\nbench_fixtures：红 —— " + "；".join(坏))
    raise SystemExit(1)
print("\nbench_fixtures：绿 —— 11 个任务的坏种全红、正确解全绿（基准量具已校准）")
