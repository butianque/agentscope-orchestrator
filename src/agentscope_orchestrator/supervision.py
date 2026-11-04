"""后台监督/重连层（Phase 5）。

- TokenMonitor: 机器判 token 用量，超高水位触发压缩信号（硬阈值，非模型判）。
- SupervisionWorker: 异步消费监督事件队列；enqueue 非阻塞，主路径不等待 worker，
  从而实现监督与主路径解耦。429/5xx 退避见 supervisor.call_with_retry，
  频道 failover/冷却见 router.Router。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("supervision")

# token 硬阈值：超过即预警压缩
DEFAULT_HIGH_WATER = 60_000
DEFAULT_CRITICAL_WATER = 90_000


class TokenMonitor:
    """机器 token 监视：输入+输出 > 高水位 → 需要压缩。"""

    def __init__(
        self,
        high_water: int = DEFAULT_HIGH_WATER,
        critical_water: int = DEFAULT_CRITICAL_WATER,
    ) -> None:
        self.high_water = high_water
        self.critical_water = critical_water

    def usage_tokens(self, usage) -> int:
        """从 ChatResponse.usage 提取可计 token 总数；无 usage 返回 0。"""
        if not usage:
            return 0
        return (
            int(getattr(usage, "prompt_tokens", None) or 0)
            + int(getattr(usage, "completion_tokens", None) or 0)
        )

    def level(self, usage) -> str:
        """返回 'ok' | 'warn' | 'critical'。"""
        n = self.usage_tokens(usage)
        if n >= self.critical_water:
            return "critical"
        if n >= self.high_water:
            return "warn"
        return "ok"

    def should_compress(self, usage) -> bool:
        return self.level(usage) in ("warn", "critical")


class SupervisionWorker:
    """后台监督 worker：异步消费事件队列；enqueue 不阻塞主路径。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.handled: list[str] = []

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def enqueue(self, event: dict) -> None:
        """非阻塞入队；主路径立即返回，不等待 worker。"""
        self._queue.put_nowait(event)

    def queue_size(self) -> int:
        return self._queue.qsize()

    async def _run(self) -> None:
        while True:
            try:
                event = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._handle(event)
            except Exception as e:  # noqa: BLE001 - 监督异常不外泄到主路径
                logger.warning("supervision event failed: %r", e)
            finally:
                self._queue.task_done()

    async def _handle(self, event: dict) -> None:
        # 语义：token 超阈值事件 → 置压缩标志（实际压缩接入框架 Context compaction）
        if event.get("type") == "compress":
            self.handled.append("compress")
        elif event.get("type") == "reconnect":
            self.handled.append("reconnect")
        else:
            self.handled.append(event.get("type", "unknown"))