"""监督/重连层（Phase 1 提供最小重试，Phase 5 扩展 token 预警/频道 failover/异步队列）。

call_with_retry: 对模型调用做指数退避重试，捕获瞬态异常(由调度层注入
TransientError 或网络异常)。不耦合主路径——调用方 await 单次，重试在此内部发生。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("supervisor")

MAX_ATTEMPTS = 4
BASE_DELAY = 1.0


class TransientError(Exception):
    """临时故障标记：429 / 5xx / 截断 / 断线，可重试。"""


async def call_with_retry(
    async_call,
    max_attempts: int = MAX_ATTEMPTS,
    base_delay: float = BASE_DELAY,
):
    """指数退避重试 async_call()，直到成功或耗尽次数。"""
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await async_call()
        except TransientError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001 - 网络/HTTP 层异常统一按瞬态处理
            last_err = e
        delay = base_delay * (2 ** (attempt - 1))
        logger.warning(
            "retry attempt=%d delay=%.1fs err=%r", attempt, delay, last_err
        )
        if attempt < max_attempts:
            await asyncio.sleep(delay)
    raise last_err or RuntimeError("call failed")