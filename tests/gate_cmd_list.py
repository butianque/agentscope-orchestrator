"""判据：`cmd` 写成字符串时，必须报**门规格非法**，不许报成「命令无法启动」。

钉的真事故（2026-11-05，由"自述检索"翻出来的）：
拿 `gate_spec.evaluate` 跑三条 compile 门：

```
列表形式      ok=True  rc=0  cause=绿
字符串形式    ok=False rc=-1 cause=命令无法启动|字符串形式
裸命令字符串  ok=False rc=-1 cause=命令无法启动|裸命令字符串
```

第二条是**门规格写错**（`[str(c) for c in cmd]` 把字符串按字符拆开），
却被报成「命令无法启动」—— 与"这台机器没装 echo"长得一模一样。
口径：**规格错与环境错不许共用一个因由**（诊断方向会从"查规格"偏到"查 PATH"）。

本判据四条：
  ① 字符串 cmd ⇒ ok=False，因由以「门规格非法」开头，且**明确说 cmd 要是列表**；
  ② 且**不许**出现「命令无法启动」（那是环境缺件那一档，不许混用）；
  ③ 列表 cmd 照常绿（加闸不许把正常路径弄红）；
  ④ 真·环境缺件（列表里写一个不存在的命令）仍报「命令无法启动」——
     这一条是**反例保护**：不许为了修①把环境缺件也改道成"规格非法"。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentscope_orchestrator import gate_spec as GS  # noqa: E402

坏: list[str] = []


def main() -> int:
    d = Path(tempfile.mkdtemp(prefix="cmd_list_"))
    try:
        return _跑(d)
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)   # 判据不许在包根留临时目录


def _跑(d: Path) -> int:
    目标 = d / "x.py"
    目标.write_text("print('hi')\n", encoding="utf-8")
    py = sys.executable
    规格 = [
        {"name": "列表", "type": "compile", "cwd": str(d),
         "cmd": [py, "-c", "print('hi')"]},
        {"name": "字符串", "type": "compile", "cwd": str(d),
         "cmd": [f'"{py}" -c "print(1)"'][0]},
        {"name": "缺件", "type": "compile", "cwd": str(d),
         "cmd": ["这个命令在整台机器上都不存在_zzz"]},
    ]
    ok, 详情 = GS.evaluate(规格, 目标, d)
    按名 = {x["gate"]: x for x in 详情}
    print("  逐门读数：")
    for x in 详情:
        print(f"    {x['gate']:6s} ok={x['ok']} rc={x['rc']} cause={x['cause'][:70]}")

    列 = 按名["列表"]
    if not (列["ok"] and 列["rc"] == 0):
        坏.append(f"列表 cmd 没绿（ok={列['ok']} rc={列['rc']}）⇒ 加闸弄红了正常路径")

    串 = 按名["字符串"]
    因 = str(串["cause"])
    if not 因.startswith("门规格非法"):
        坏.append(f"字符串 cmd 的因由不是「门规格非法」（实得 {因[:60]}）")
    if "命令无法启动" in 因:
        坏.append("字符串 cmd 仍报「命令无法启动」⇒ 规格错与环境缺件混用")
    if "列表" not in 因:
        坏.append(f"因由没说清楚该怎么写（应提「必须是列表」，实得 {因[:60]}）")

    缺 = 按名["缺件"]
    if not str(缺["cause"]).startswith("命令无法启动"):
        坏.append(f"真·环境缺件被改道了（实得 {缺['cause'][:60]}）⇒ 反例保护失效")

    if 坏:
        print("门规格因由：红 —— " + "；".join(坏))
        return 1
    print("门规格因由：绿 —— **字符串 cmd 报「门规格非法」并指出要列表 · "
          "正常列表 cmd 照常绿 · 真·环境缺件仍是「命令无法启动」**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())