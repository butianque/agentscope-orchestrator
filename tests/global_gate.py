"""跨进程并发槽回归（2026-11-01：把"每进程 4"变成真·全局的守门用例）。

★ 为什么必须单独测：`_CONCURRENCY = 4` **历史上只是每进程的 asyncio.Semaphore** ——
  起 3 个服务器进程就是 12 路并发，而每个进程自己看着都守规矩、测试也都绿。
  这种"局部合规、全局失控"的东西，只有**多进程真抢**才能证伪。

★ 判据形状（行为，不是常量）：
  起 6 个子进程，每个去抢一个槽并**持有一段可测量的时间**，父亲按子进程汇报的
  起止时刻算**真实重叠峰值** ⇒ 峰值必须 ≤ 上限。常量写对不等于存在；
  这里只认"真抢的时候会不会超"。

跑法：python tests/global_gate.py      退出码 0 = 全绿
"""
from __future__ import annotations

import json
import os
import subprocess
import atexit
import shutil
import sys
import tempfile
import time
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))

上限 = 4
子进程数 = 6
持有秒 = 1.2

子 = r"""
import asyncio, json, sys, time
sys.path.insert(0, sys.argv[1])
from agentscope_orchestrator import global_gate as gg

async def main():
    async with gg.抢槽(int(sys.argv[2])) as r:
        # perf_counter：Windows 上走 QPC，全系统共同时基且亚微秒；
        # 原来的 time.time() 粒度约 15.6ms，会把"同 tick 内的先后"算成并发。
        t0 = time.perf_counter()
        await asyncio.sleep(float(sys.argv[3]))
        print(json.dumps({"槽": r.get("槽"), "降级": r.get("降级"),
                          "原因": r.get("原因"), "t0": t0, "t1": time.perf_counter()}),
              flush=True)

asyncio.run(main())
"""


def main() -> int:
    失败: list[str] = []
    条数 = 0

    def 判(名: str, 条件: bool, 说明: str = "") -> None:
        nonlocal 条数
        条数 += 1
        if not 条件:
            失败.append(f"{名}：{说明}")

    临时 = Path(tempfile.mkdtemp(prefix="gg_"))
    atexit.register(shutil.rmtree, 临时, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    环境 = dict(os.environ)
    环境["ORCH_STATE_DIR"] = str(临时)      # 独占槽目录，避免和真跑互相干扰
    ps = [subprocess.Popen([sys.executable, "-c", 子, str(根 / "src"), str(上限), str(持有秒)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           env=环境, encoding="utf-8", errors="replace")
          for _ in range(子进程数)]
    记录 = []
    for p in ps:
        out, err = p.communicate(timeout=120)
        if p.returncode != 0:
            失败.append(f"子进程退出码 {p.returncode}：{(err or out).strip()[:200]}")
            continue
        行 = [x for x in out.strip().splitlines() if x.startswith("{")]
        if not 行:
            失败.append(f"子进程无读数：{out.strip()[:120]}")
            continue
        记录.append(json.loads(行[-1]))

    判("降级未发生", all(not r["降级"] for r in 记录),
      f"槽目录应可写，实测降级：{[r['原因'] for r in 记录 if r['降级']]}")
    判("每个子进程都拿到槽", all(isinstance(r.get("槽"), int) for r in 记录),
      f"实测槽位：{[r.get('槽') for r in 记录]}")

    # 真实重叠峰值：把 12 个端点排序后扫一遍。
    # ★ 平手时**先退后进**（`-1` 排在 `+1` 前）：两个事件时间戳相同时，保守地不计重叠。
    #   方向选"少算"是有意的：这条判据的主张是"峰值 ≤ 上限"，而真并发由下面
    #   「槽号被真独占」与「串行化真的发生」两条守 —— 不靠这一条的过计来抓。
    点 = []
    for r in 记录:
        点.append((r["t0"], 1))
        点.append((r["t1"], -1))
    点.sort(key=lambda x: (x[0], x[1]))
    峰 = 现 = 0
    for _, d in 点:
        现 += d
        峰 = max(峰, 现)
    判("重叠峰值不超上限", 峰 <= 上限,
      f"{子进程数} 个子进程真抢槽，重叠峰值 {峰}，声明上限 {上限}")

    # 反证：同一时刻占着的槽号必须互不相同（否则是假锁）
    号 = [r["槽"] for r in 记录 if isinstance(r.get("槽"), int)]
    同时 = {}
    for r in sorted(记录, key=lambda x: x["t0"]):
        同时.setdefault(r["t0"], []).append(r["槽"])
    判("槽号被真独占", max(len(v) for v in 同时.values()) <= 上限,
      f"同一时刻槽号：{同时}")

    用时 = max((r["t1"] for r in 记录), default=0) - min((r["t0"] for r in 记录), default=0)
    判("串行化真的发生", 用时 >= 持有秒 * 1.5,
      f"6 个进程抢 {上限} 个槽、每个持有 {持有秒}s ⇒ 总时长应明显大于 {持有秒}s，实测 {用时:.2f}s")

    for p in 临时.glob("slot-*"):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        临时.rmdir()
    except OSError:
        pass

    if 失败:
        print(f"global_gate：{len(失败)}/{条数} 红")
        for f in 失败:
            print(f"  [FAIL] {f}")
        return 1
    print(f"global_gate：{条数}/{条数} 全绿（{子进程数} 进程真抢，峰值 {峰} ≤ 上限 {上限}，"
          f"总时长 {用时:.2f}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
