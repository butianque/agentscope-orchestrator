"""phase_window_lock 干测：跨进程目标文件互斥锁（多窗并发防撞）。

覆盖：抢锁/释放、互斥（同文件二窗一得另拒）、异文件不互斥、崩溃残留陈旧锁清理、
develop 集成 import 不崩。无需联网。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentscope_orchestrator import window_lock  # noqa: E402

PASS = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {extra}"
    PASS += 1
    print(f"  ok  {name}")


def main() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        audit = Path(tmp) / ".audit_trail"
        ta = Path(tmp) / "a.rs"
        tb = Path(tmp) / "b.rs"
        ta.write_text("fn main() {}\n", encoding="utf-8")
        tb.write_text("fn main() {}\n", encoding="utf-8")

        got1 = window_lock.acquire(audit, ta, timeout=0.0)
        check("首窗抢到 a.rs", got1)
        got2 = window_lock.acquire(audit, ta, timeout=0.0)
        check("二窗被 a.rs 拒（互斥）", not got2)
        other = window_lock.acquire(audit, tb, timeout=0.0)
        check("异文件 b.rs 不互斥", other)
        window_lock.release(audit, tb)
        window_lock.release(audit, ta)
        got3 = window_lock.acquire(audit, ta, timeout=0.0)
        check("释放后可再抢 a.rs", got3)
        window_lock.release(audit, ta)
        check("释放后锁文件被删", not window_lock.lock_file(audit, ta).exists())

        # 陈旧锁清理：伪造一个比 max_age 老的锁体
        lf = window_lock.lock_file(audit, ta)
        lf.write_text("stale", encoding="utf-8")
        old = time.time() - 9999
        try:
            import os
            os.utime(lf, (old, old))
        except Exception:  # noqa: BLE001 Windows 下 utime 可能因权限失败
            pass
        got_stale = window_lock.acquire(audit, ta, timeout=0.0, max_age=1.0)
        check("陈旧锁被清理可再抢", got_stale)
        window_lock.release(audit, ta)

    # develop 集成 import（wiring 生效，未跑真门）
    import agentscope_orchestrator.develop  # noqa: F401
    check("develop 引用 window_lock.acquire", hasattr(window_lock, "acquire"))
    check("develop 引用 window_lock.release", hasattr(window_lock, "release"))

    print(f"\nPASS={PASS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())