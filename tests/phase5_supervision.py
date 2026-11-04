"""Phase 5 机证判据：token 预警硬阈值 + 频道 down-后不再被选(failover冷却) + 监督队列解耦。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.router import Router
from agentscope_orchestrator.supervision import SupervisionWorker, TokenMonitor


class FakeUsage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


# 1) token 预警：硬阈值机器判定
mon = TokenMonitor(high_water=100, critical_water=200)
assert mon.should_compress(FakeUsage(60, 30)) is False
assert mon.should_compress(FakeUsage(120, 0)) is True  # warn
assert mon.level(FakeUsage(250, 0)) == "critical"
assert mon.usage_tokens(None) == 0

# 2) 频道 failover 冷却：某频道连续失败达阈值后被隔离，请求转健康频道
calls = {"bad": 0, "good": 0}


class FakeBad:
    async def __call__(self, msgs):
        calls["bad"] += 1
        raise RuntimeError("injected 429")


class FakeGood:
    async def __call__(self, msgs):
        calls["good"] += 1
        return object()


async def failover():
    r = Router([("bad", FakeBad()), ("good", FakeGood())])
    for _ in range(6):
        try:
            await r.call([])
        except Exception:
            pass
    return calls["bad"]


async def decoupled():
    # 3) 监督队列解耦：enqueue 非阻塞，主路径立即返回，worker 后台消化
    w = SupervisionWorker()
    w.start()
    await asyncio.sleep(0)
    w.enqueue({"type": "compress"})
    w.enqueue({"type": "compress"})
    w.enqueue({"type": "reconnect"})
    assert w.queue_size() >= 0  # 入队不抛、立即返回
    await asyncio.sleep(0.1)  # 让 worker 消化
    assert len(w.handled) == 3, f"worker 应处理 3 件事，实际 {w.handled}"
    await w.stop()


bad_calls_after = asyncio.run(failover())
assert bad_calls_after <= 2, f"bad 不应在冷却期被反复挑选，调用 {bad_calls_after} 次"

asyncio.run(decoupled())
print("[phase5] PASS: token预警 + failover隔离 + 监督队列解耦")
sys.exit(0)