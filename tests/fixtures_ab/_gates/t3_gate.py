# -*- coding: utf-8 -*-
"""t3 仓外门（**不随仓交给模型**）：独立验装配结果，不依赖仓内测试文件。

用法：python _gates/t3_gate.py <t3 仓绝对路径>

三条**全部**成立才算绿：
  ① 仓内判据文件未被改（sha256 对齐 _gates/pristine.json）；
  ② 仓内入口 run_tests.py ⇒ 退出码 0；
  ③ **本门自带**的断言逐条对齐：顶层命令必须进到 regression_parity 门里，
     且调用方显式给的值不许被覆盖、其他门型不许被牵连。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

基准 = json.loads((Path(__file__).resolve().parent / "pristine.json").read_text(encoding="utf-8"))
_留 = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC",
       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
被钉 = ("run_tests.py", "runner.py", "tests/test_assemble.py")

顶层 = ["python", "-c", "print('ok')"]


def main(仓: Path) -> int:
    红: list[str] = []
    for 相对 in 被钉:
        应有 = 基准.get(f"t3_topcmd_not_injected/{相对}")
        if 应有 and hashlib.sha256((仓 / 相对).read_bytes()).hexdigest() != 应有:
            红.append(f"① 判据文件被改：{相对}")

    r = subprocess.run([sys.executable, str(仓 / "run_tests.py")], cwd=str(仓),
                       env={k: v for k, v in os.environ.items() if k.upper() in _留},
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    if r.returncode != 0:
        尾 = [x for x in ((r.stdout or "") + (r.stderr or "")).strip().splitlines() if x.strip()]
        红.append(f"② 仓内入口未通过 rc={r.returncode} {尾[-1][:80] if 尾 else ''}")

    sys.path.insert(0, str(仓))
    try:
        from assemble import 装配               # noqa: PLC0415
        from runner import 校验门                # noqa: PLC0415
    except Exception as e:                       # noqa: BLE001
        红.append(f"③ 模块导不进来：{type(e).__name__}")
        print(json.dumps({"结果": "红", "理由": 红[:6]}, ensure_ascii=False))
        return 1

    只给基线的门 = {"type": "regression_parity", "baseline_cmd": ["python", "-m", "pytest"], "cwd": "."}
    表 = 装配(顶层, [只给基线的门])
    门 = [x for x in 表 if x.get("type") == "regression_parity"]
    if not 门:
        红.append("③ 装配后找不到 regression_parity 门")
    else:
        if 门[0].get("cmd") != 顶层:
            红.append(f"③ 该门没读到顶层命令：cmd={门[0].get('cmd')!r}")
        if 门[0].get("baseline_cmd") != ["python", "-m", "pytest"]:
            红.append("③ 调用方给的 baseline_cmd 被覆盖了")
        try:
            校验门(门[0])
        except Exception as e:                   # noqa: BLE001
            红.append(f"③ 执行前校验不过：{e}")

    显式门 = {"type": "regression_parity", "cmd": ["echo", "x"], "baseline_cmd": ["echo", "y"]}
    表2 = 装配(顶层, [显式门])
    门2 = [x for x in 表2 if x.get("type") == "regression_parity"][0]
    if 门2.get("cmd") != ["echo", "x"]:
        红.append(f"③ 调用方显式给的 cmd 被覆盖了：{门2.get('cmd')!r}")

    if 表[0] != {"type": "cmd_exit_ok", "cmd": 顶层}:
        红.append("③ 顶层命令那条门被改了")

    if 红:
        print(json.dumps({"结果": "红", "理由": 红[:6]}, ensure_ascii=False))
        return 1
    print(json.dumps({"结果": "绿"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1
                          else Path(__file__).resolve().parents[1] / "t3_topcmd_not_injected"))
