"""窗口级互斥锁（window_lock）：跨进程目标文件锁，防多窗并发撞同一文件。

多智能体多窗口并行自主开发时，两个窗口可能同时盯上同一个 Rust 目标文件
（备份 → 应用方案 → 跑 crate 门 → 绿保/红滚）→ 互相覆盖、备份错乱、回滚互相
踩踏。本模块按**目标文件**加锁：谁先抢到谁修完，后到者见锁即跳过（不重复修、
不冲突），根治「LLM 执行多窗口联动的毛病」。

实现：在 `.audit_trail/window_locks/` 下对目标文件绝对路径取 sha1 作为锁文件名，
用 `os.open(O_CREAT|O_EXCL)` **独占创建**抢锁。无需第三方依赖、跨进程可见。
进程崩溃残留的陈旧锁体（超过 max_age）由 acquire 顺带清理，避免死锁。

诚实边界：锁只防「同时改同一文件」的写写冲突，不改变判据门语义、不覆盖
结构记忆单写者语义。
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path


def locks_dir(audit: Path) -> Path:
    d = Path(audit) / "window_locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_file(audit: Path, file_abs: Path) -> Path:
    h = hashlib.sha1(str(file_abs.resolve()).encode("utf-8", "ignore")).hexdigest()
    return locks_dir(audit) / f"{h}.lock"


def acquire(audit: Path, file_abs: Path, timeout: float = 0.0,
            max_age: float = 3600.0) -> bool:
    """抢目标文件的独占锁。

    timeout=0：一次尝试即可（多窗开发更愿意让后到者跳过而非阻塞）；
    timeout>0：轮询等待直到超时。陈旧锁（超过 max_age 秒）由抢锁者清理。
    返回 True=拿到锁（调用方必须随后 release）。
    """
    lf = lock_file(audit, file_abs)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lf, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            now = time.time()
            try:
                age = now - lf.stat().st_mtime
            except OSError:   # 锁文件被并发删除，重试
                age = 0
            if age > max_age:
                try:
                    lf.unlink()
                except OSError:
                    pass
                continue
            if timeout <= 0 or now >= deadline:
                return False   # 另一窗在修，跳过（不阻塞不冲突）
            time.sleep(0.2)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            import json
            f.write(json.dumps({"file": str(file_abs.resolve()),
                                "pid": os.getpid(), "ts": time.time()}))
        return True


def release(audit: Path, file_abs: Path) -> None:
    lf = lock_file(audit, file_abs)
    try:
        lf.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass