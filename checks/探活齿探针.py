# -*- coding: utf-8 -*-
"""R18 判据力探针（齿）：把探活退回**两桶**（慢=死），门必须变红。

★ 为什么必须做：一条门如果"改回去也全绿"，它就是**无齿门**。
★ 本探针**不碰网络**：直接验判据本身，以及"慢通道是否被判死"这条不变量。
★ 前几次教训复用：读用文本模式（归一 `\\n`）、写用 `newline="\\n"`、
  还原用**字节级**、路径从 `__file__` 推导。
"""
import os
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "src" / "agentscope_orchestrator"
根 = PKG.parents[1]
PY = 根 / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)
F = PKG / "gateway.py"
# ★★ R65 互斥：两个进程同时改同一个源文件会**互相还原对方的改动**（实测各 7 红）。
#   锁的键是**被改的文件**，不是探针名 ⇒ 新增探针自动共享同一把锁。
#   ★ 多文件按**同一顺序**取锁（防两探针各持一把互相等 ⇒ 死锁）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _互斥 import 占住 as _占住, 原子写   #  noqa: E402
_锁0 = _占住(F)   # 等不到就抛 占用错（绝不静默放行）

原文 = F.read_bytes()
源 = F.read_text(encoding="utf-8")

env = dict(os.environ)
env["PYTHONPATH"] = str(PKG.parent)
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"


def 跑(内容: str) -> tuple[int, str]:
    with open(F, "w", encoding="utf-8", newline="\n") as f:
        f.write(内容)
    try:
        cp = subprocess.run([str(PY), str(F), "--selftest"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="ignore", env=env, cwd=str(根), timeout=600)
        return cp.returncode, (cp.stdout or "") + (cp.stderr or "")
    finally:
        原子写(F, 原文)


结果: list[tuple[str, bool]] = []
try:
    # ★ 回退①：把「读不到」当成「死」（旧的两桶口径）
    坏1 = 源.replace(
        '    if _classify_failure(None, "TimeoutError: read operation timed out")'
        ' != 读不到:',
        '    if _classify_failure(None, "TimeoutError") != 硬死:', 1)
    assert 坏1 != 源, "回退①注入失败"
    rc1, 出1 = 跑(坏1)
    ok1 = rc1 != 0
    结果.append(("① 齿：超时判「死」⇒ 门变红", ok1))
    print(f"① 退出码={rc1}")

    # ★ 回退②：只排除 live（即"读不到"不再交给 Router 试）⇒ 全慢时拒跑
    坏2 = 源.replace(
        '    试 = set(rep["live"]) | set(rep.get("unreadable") or {})',
        '    试 = set(rep["live"])', 1)
    assert 坏2 != 源, "回退②注入失败"
    rc2, 出2 = 跑(坏2)
    ok2 = rc2 != 0
    结果.append(("② 齿：慢通道不再交给 Router ⇒ 门变红（全慢时拒跑）", ok2))
    print(f"② 退出码={rc2}")
finally:
    原子写(F, 原文)

还 = F.read_bytes() == 原文
结果.append(("③ 还原：字节级一致", 还))
print(f"③ 还原字节级一致={还}")
print()
for 名, ok in 结果:
    print(("PASS " if ok else "FAIL ") + 名)
sys.exit(0 if all(ok for _, ok in 结果) else 1)
