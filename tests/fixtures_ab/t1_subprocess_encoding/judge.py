# -*- coding: utf-8 -*-
"""判据：跑一条被测命令，检查它输出的 JSON 里有没有 `分类` 字段。

约定：被测命令把一行 JSON 写到 stdout；判据按 utf-8 读回来，字段齐 ⇒ ok=True。
判据自己的输出是纯 ASCII 的 JSON，方便上一层按任意编码读。
"""
from __future__ import annotations

import json
import subprocess
import sys

# 被测命令：把一行 JSON 打到 stdout（含中文键，非 ASCII 转义）。
被测代码 = "import json;print(json.dumps({'分类': '达标'}, ensure_ascii=False))"


def 取被测输出() -> tuple[int, str]:
    """起被测子进程，按 utf-8 读它的 stdout（读不动的位置替换掉，不抛异常）。"""
    r = subprocess.run(
        [sys.executable, "-c", 被测代码],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.returncode, (r.stdout or "")


def 判() -> dict:
    rc, 文 = 取被测输出()
    数据: object = None
    for 行 in [x for x in 文.strip().splitlines() if x.strip()]:
        try:
            数据 = json.loads(行)
        except Exception:
            数据 = None
        else:
            break
    值 = 数据.get("分类") if isinstance(数据, dict) else None
    return {"ok": isinstance(值, str) and bool(值), "field": 值,
            "child_rc": rc, "child_bytes": len(文)}


def main() -> int:
    结果 = 判()
    print(json.dumps(结果, ensure_ascii=True))
    return 0 if 结果["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
