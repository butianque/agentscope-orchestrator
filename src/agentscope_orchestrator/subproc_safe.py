# -*- coding: utf-8 -*-
"""带超时地跑**子进程树** —— 不许因为孙进程持有管道而把等待变成挂死。

## 为什么需要这个模块（真事故，2026-11-04）

`tests/lean_spec.py` 里写的是：

```python
唤 = subprocess.run(["lake", "env", "lean", 模块], timeout=900, capture_output=True)
except subprocess.TimeoutExpired: ...
```

看起来有超时、有出口。**实际它挂死了整条 CI 26 分钟**（改成 180 秒之后
单跑仍超过 600 秒没返回）。原因不是超时值太大，是 `subprocess.run` 的超时
**管不到孙进程**：

    python (判据)
      └── lake.exe          ← subprocess 直接杀这个
            └── lean.exe    ← **孙进程，继承并持有 stdout/stderr 管道**

超时到了，`run()` 杀掉 `lake`；但 `lean` 还开着那两个管道的写端 ⇒
`communicate()` 读不到 EOF ⇒ **一直等**。于是"有超时"变成了"永远不返回"。

口径：**「有出口」不能只看代码里有没有 `except TimeoutExpired`** ——
得看那条出口在**真的**被超时的进程树里走不走得到。

## 修法两条（都必要）

1. **输出走临时文件，不走管道**：孙进程持有的是文件句柄，
   文件不会因为"写端没关"而阻塞父进程的读。
2. **超时杀整棵树**：Windows 用 `taskkill /F /T`，POSIX 用 `killpg`。
   只杀直接子进程会留下孤儿 `lean.exe` 继续占着 Lean 的锁。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass


@dataclass(frozen=True)
class 结果:
    返回码: int | None          # None = 超时被杀
    输出: str
    超时: bool
    秒: float

    def 行(self) -> str:
        圈 = "超时" if self.超时 else f"rc={self.返回码}"
        return f"{圈} · {self.秒:.1f}s · {len(self.输出)} 字符"


def _杀树(pid: int) -> None:
    """杀**整棵**进程树（含孙进程）。"""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        else:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:                                    # noqa: BLE001
        pass


def 跑(cmd: list[str], *, cwd: str | None = None, timeout: float = 180.0,
       env: dict | None = None) -> 结果:
    """跑一条命令，**保证**在 `timeout` 秒后返回（哪怕它起了孙进程）。

    返回 `结果(返回码=None, 超时=True)` 表示"没验成"，**不是**"它坏了" ——
    这两件事在结论里必须分得开。
    """
    import time as _t
    t0 = _t.time()
    # ★ 输出走**文件**而不是管道（见模块头第 1 条）。
    with tempfile.TemporaryFile() as 出f, tempfile.TemporaryFile() as 错f:
        唤 = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=出f, stderr=错f, stdin=subprocess.DEVNULL,
            start_new_session=(sys.platform != "win32"),
        )
        超时 = False
        try:
            rc = 唤.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            超时 = True
            rc = None
            _杀树(唤.pid)                                  # ★ 杀整棵树
            try:
                唤.wait(timeout=30)                         # 真把它收掉，别留僵尸
            except subprocess.TimeoutExpired:
                pass
        秒 = _t.time() - t0
        出f.seek(0)
        错f.seek(0)
        文 = (出f.read().decode("utf-8", "replace")
              + 错f.read().decode("utf-8", "replace"))
    return 结果(返回码=rc, 输出=文, 超时=超时, 秒=秒)