"""★ 可复现 benchmark（**包内模块**，随 wheel 一起交付）。

为什么必须随包交付（2026-10-30 的教训）：第一版放在仓库 `tools/benchmark.py`，
装到 Linux 的 venv 里跑完备性记分卡 → **L 双平台真跑直接红**
（`缺 benchmark 脚本：…/site-packages/tools/benchmark.py`）。
交付物里的能力，必须在**装完之后**还能被调用，否则那是仓库里的能力，不是产品的能力。

纪律同前：只用标准库 + 本包源码（不依赖网络/模型通道），场景自建自清，
末行打印单行 JSON 供抓取；**只报实测读数，不报"谁更快"**。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import gate_spec as gs
from . import mem_context as mc
from . import mem_symbols as ms

PY = sys.executable or "python3"


def _场景_门绿(仓: Path) -> tuple[str, dict]:
    t = time.perf_counter()
    res, aud = gs.evaluate(
        [{"name": "compile", "type": "compile", "cmd": [PY, "-c", "print('ok')"],
          "expect_exit": 0, "cwd": str(仓)}], 仓, 仓, str(仓))
    秒 = time.perf_counter() - t
    return "门-单命令绿", {"秒": round(秒, 3), "通过": bool(res.get("compile")),
                          "审计字节": len(json.dumps(aud, ensure_ascii=False).encode())}


def _场景_门红(仓: Path) -> tuple[str, dict]:
    t = time.perf_counter()
    res, _ = gs.evaluate(
        [{"name": "compile", "type": "compile",
          "cmd": [PY, "-c", "import sys; sys.exit(1)"],
          "expect_exit": 0, "cwd": str(仓)}], 仓, 仓, str(仓))
    秒 = time.perf_counter() - t
    return "门-单命令红", {"秒": round(秒, 3), "通过": bool(res.get("compile"))}


def _场景_索引(仓: Path, 文件数: int = 60) -> tuple[str, dict]:
    for i in range(文件数):
        (仓 / f"m{i}.py").write_text(
            f"def f{i}(x):\n    return x\n\nclass C{i}:\n    def g(self):\n        return {i}\n",
            encoding="utf-8")
    t = time.perf_counter()
    表 = ms.符号表()
    读数 = 表.同步(仓)
    秒 = time.perf_counter() - t
    return f"索引-{文件数}文件", {"秒": round(秒, 3), "规模": 表.规模(),
                             "扫描": 读数.扫描文件, "重算": 读数.重算符号}


def _场景_上下文(仓: Path, 预算: int = 800) -> tuple[str, dict]:
    表 = ms.符号表()
    表.同步(仓)
    t = time.perf_counter()
    包 = mc.上下文包(表, [mc.洞("m0.py", 1, "E0425")])
    文, 报 = mc.渲染_预算(包, 预算)
    秒 = time.perf_counter() - t
    return f"上下文-预算{预算}", {"秒": round(秒, 3), "条": len(包),
                              "字节": len(文.encode("utf-8")),
                              "未超预算": len(文.encode("utf-8")) <= 预算,
                              "截断": (报 or {}).get("丢条")}


def 跑(重复: int = 1) -> dict:
    """跑全部场景，返回 {环境, 重复, 读数}。调用方自己决定怎么比。"""
    临时 = Path(tempfile.mkdtemp(prefix="orch_bench_"))
    读数: list[dict] = []
    try:
        for k in range(max(1, 重复)):
            空 = 临时 / f"空{k}"
            空.mkdir(parents=True, exist_ok=True)
            for 场景 in (_场景_门绿, _场景_门红):
                名, 数 = 场景(空)
                读数.append({"场景": 名, **数})
            仓2 = 临时 / f"索{k}"
            仓2.mkdir(parents=True, exist_ok=True)
            for 场景, 参 in ((_场景_索引, (仓2,)), (_场景_上下文, (仓2,))):
                名, 数 = 场景(*参)
                读数.append({"场景": 名, **数})
    finally:
        shutil.rmtree(临时, ignore_errors=True)
    return {"环境": {"平台": platform.platform(), "python": platform.python_version(),
                    "实现": platform.python_implementation(), "CPU核": os.cpu_count()},
            "重复": max(1, 重复), "读数": 读数}


def 语义指纹(出: dict | None = None) -> dict:
    """只取**跨平台必须一致**的语义量：门结论、符号规模、审计字节、上下文包字节/条数。

    ★ 刻意**不含秒数**：秒数依赖机器负载，把它写进基线就等于把"快慢"当成正确性。
      这个指纹的用途是"换个平台跑，结论是否相同"，不是"谁更快"。
    """
    出 = 出 if 出 is not None else 跑(1)
    读 = 出.get("读数") or []
    门 = [x.get("通过") for x in 读 if str(x.get("场景", "")).startswith("门-")]
    规 = next((x.get("规模") for x in 读 if str(x.get("场景", "")).startswith("索引-")), None)
    审 = next((x.get("审计字节") for x in 读 if x.get("审计字节") is not None), None)
    上 = next((x for x in 读 if str(x.get("场景", "")).startswith("上下文-")), {})
    return {"门结论": 门, "符号规模": 规, "审计字节": 审,
            "上下文字节": 上.get("字节"), "上下文条": 上.get("条")}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    重复 = 1
    if "--repeat" in argv:
        重复 = max(1, int(argv[argv.index("--repeat") + 1]))
    出 = 跑(重复)
    环境 = 出["环境"]
    print(f"══ benchmark（{环境['平台']} · py{环境['python']} · {环境['CPU核']} 核）══")
    for r in 出["读数"]:
        细 = " ".join(f"{k}={v}" for k, v in r.items() if k != "场景")
        print(f"  {r['场景']}: {细}")
    print(json.dumps(出, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
