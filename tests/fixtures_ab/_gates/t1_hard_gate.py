# -*- coding: utf-8 -*-
"""t1 仓外门（**不随仓交给模型**，因此既能防篡改、也不泄题）。

用法：python _gates/t1_hard_gate.py <t1 仓绝对路径>

四条**全部**成立才算绿：
  ① judge.py 在最小环境下自跑 ⇒ 退出码 0（判据自己认为 PASS）；
  ② judge.py 源码里出现 PYTHONIOENCODING（子进程的编码口径必须**显式**给出）；
  ③ 毒化宿主口径（PYTHONIOENCODING=cp1252 + PYTHONUTF8=0）后 judge.py 仍 ⇒ 退出码 0
     —— 证明它是**显式改写子进程环境**，而不是「读端按宿主 locale 解码」之类的糊法；
  ④ 仓内 CI 入口 run_ci.py ⇒ 退出码 0。

为什么要有 ③：只查字面量，模型可以「把 PYTHONIOENCODING 写进注释 + 读端按 locale 解码」骗过；
 ③ 是行为判据，注释骗不过去。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_留 = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT",
       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "COMSPEC"}


def 干净(覆盖: dict | None = None) -> dict:
    环境 = {k: v for k, v in os.environ.items() if k.upper() in _留}
    if 覆盖:
        环境.update(覆盖)
    return 环境


def 跑(脚本: Path, 环境: dict, cwd: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(脚本)], cwd=str(cwd), env=环境,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main(仓: Path) -> int:
    红: list[str] = []
    判据, ci = 仓 / "judge.py", 仓 / "run_ci.py"

    rc, 出 = 跑(判据, 干净(), 仓)
    if rc != 0:
        红.append(f"① 判据未通过 rc={rc} {出.strip().splitlines()[-1:] or ''}")

    if "PYTHONIOENCODING" not in 判据.read_text(encoding="utf-8", errors="replace"):
        红.append("② 编码口径不符")

    rc3, 出3 = 跑(判据, 干净({"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}), 仓)
    if rc3 != 0:
        红.append(f"③ 宿主口径一变就崩：rc={rc3}")

    rc4, 出4 = 跑(ci, 干净(), 仓)
    if rc4 != 0:
        红.append(f"④ CI 未通过 rc={rc4} {出4.strip().splitlines()[-1:] or ''}")

    if 红:
        print(json.dumps({"结果": "红", "理由": 红}, ensure_ascii=False))
        return 1
    print(json.dumps({"结果": "绿"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1
                          else Path(__file__).resolve().parents[1] / "t1_subprocess_encoding"))
