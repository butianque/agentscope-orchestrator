"""跨进程并发槽位（global_gate）：让「并发上限」在**所有服务器进程之间**也算数。

═══════════════════════════════════════════════════════════════════════
原状与代价（这是`guide`里"做不到"那一行说的东西，2026-11-01 起不再是缺陷）
═══════════════════════════════════════════════════════════════════════
`_CONCURRENCY = 4` 此前只是**每进程**的 `asyncio.Semaphore`：起 3 个 MCP 服务器进程
就是 12 路并发（cargo/编译器一起上，足以把机器打死），而**每个进程自己看着都守规矩**。

为什么不用「pid + 时间戳」的土办法
---------------------------------
Windows 上"这个 pid 还活着吗"不可靠（pid 复用、权限）、进程被强杀会留**永久死锁**的文件。
⇒ 用**操作系统文件锁**：进程怎么死，锁都由内核释放，不存在僵尸槽。

机制
----
    槽位文件  <状态根>/slots/slot-0 .. slot-(上限-1)
    抢槽     非阻塞独占锁（Windows `msvcrt.locking` / POSIX `flock`），**抢到哪个用哪个**
    抢不到   退避重试（150~300 ms 抖动），过了等待上限就**如实超时**（不假装拿到）

★ 目录不可写 ⇒ **降级为进程内上限**，并把这件事实写进返回的 `降级/原因`
  （静默降级 = 让调用方以为全局生效，那是另一种撒谎）。
"""
from __future__ import annotations

import asyncio
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path

_锁模块 = None
if os.name == "nt":                       # pragma: no cover - 平台分支
    try:
        import msvcrt as _锁模块
    except Exception:                     # noqa: BLE001
        _锁模块 = None
else:                                     # pragma: no cover
    try:
        import fcntl as _锁模块
    except Exception:                     # noqa: BLE001
        _锁模块 = None


def _槽目录() -> Path:
    from . import profile as _p
    return Path(_p.STATE_ROOT) / "slots"


def _试锁(p: Path):
    """试着独占一个槽位文件。返回文件对象（成功）或 None（被别人占着）。"""
    if _锁模块 is None:
        return None
    try:
        fh = open(p, "a+b")
    except OSError:
        raise
    try:
        if os.name == "nt":
            fh.seek(0)
            _锁模块.locking(fh.fileno(), _锁模块.LK_NBLCK, 1)
        else:                              # pragma: no cover
            _锁模块.flock(fh.fileno(), _锁模块.LOCK_EX | _锁模块.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def _放锁(fh) -> None:
    try:
        if os.name == "nt":
            fh.seek(0)
            _锁模块.locking(fh.fileno(), _锁模块.LK_UNLCK, 1)
        else:                              # pragma: no cover
            _锁模块.flock(fh.fileno(), _锁模块.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            fh.close()
        except OSError:
            pass


@asynccontextmanager
async def 抢槽(上限: int, 目录: Path | None = None, 等待上限: float | None = None):
    """异步上下文：拿到一个跨进程槽位才继续。

    yield `{"槽": int|None, "降级": bool, "原因": str}`。
    · 降级=True（目录不可写 / 平台无锁） ⇒ 调用方应退回**进程内**上限，并把这个原因交出去；
    · 等待超时 ⇒ 抛 `TimeoutError`（由调用方决定如实报"排队超时"还是继续等）。
    """
    上限 = max(1, int(上限))
    顶 = 目录 or _槽目录()
    try:
        顶.mkdir(parents=True, exist_ok=True)
        # 真试着建个文件：`os.access` 在本机某些路径上说谎（见 profile._可写 的教训）
        探 = 顶 / f".探针.{os.getpid()}"
        探.write_text("ok", encoding="utf-8")
        探.unlink()
    except OSError as e:
        yield {"槽": None, "降级": True,
               "原因": f"槽目录不可写（{顶}）：{type(e).__name__}: {e}"}
        return
    if _锁模块 is None:
        yield {"槽": None, "降级": True, "原因": "本平台没有可用的文件锁模块"}
        return

    t0 = asyncio.get_running_loop().time()
    已告警 = False
    while True:
        for i in range(上限):
            try:
                fh = _试锁(顶 / f"slot-{i}")
            except OSError as e:
                yield {"槽": None, "降级": True,
                       "原因": f"槽位文件打不开：{type(e).__name__}: {e}"}
                return
            if fh is not None:
                try:
                    yield {"槽": i, "降级": False, "原因": ""}
                finally:
                    _放锁(fh)
                return
        if 等待上限 is not None and (asyncio.get_running_loop().time() - t0) >= 等待上限:
            raise TimeoutError(f"跨进程并发槽位 {上限} 个全被占用，等了 {等待上限:.0f}s")
        await asyncio.sleep(0.15 + random.random() * 0.15)


async def 探活(上限: int = 4) -> dict:
    """只读探针：报出"现在有几个槽位被别的进程占着"，给判据/doctor 用。"""
    顶 = _槽目录()
    占 = []
    for i in range(max(1, int(上限))):
        p = 顶 / f"slot-{i}"
        if not p.exists():
            continue
        try:
            fh = _试锁(p)
        except OSError:
            continue
        if fh is None:
            占.append(i)
        else:
            _放锁(fh)
    return {"槽目录": str(顶), "上限": int(上限), "占用": 占, "占用数": len(占)}
