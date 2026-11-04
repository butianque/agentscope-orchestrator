# -*- coding: utf-8 -*-
"""R19 判据力探针（齿）：把 `_验证可分开` 退回**只查分开、不查方向**，门必须变红。

★ 这是真跑抓出的严重缺陷（派生门反向 ⇒ 只让坏产物过 ⇒ 烧 17895 字符不收敛）。
★ 探针自身的教训一律复用：读用文本模式、写用 `newline="\\n"`、
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
F = PKG / "discriminant.py"
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
                            errors="ignore", env=env, cwd=str(根), timeout=900)
        return cp.returncode, (cp.stdout or "") + (cp.stderr or "")
    finally:
        原子写(F, 原文)


结果: list[tuple[str, bool]] = []
try:
    # ★ 回退：方向判据退回「只查分开」（旧实现）
    针 = "        return (v好 is True) and (v坏 is False)"
    assert 源.count(针) == 1, "找不到 R19 方向判据"
    坏 = 源.replace(针, "        return v坏 != v好", 1)
    rc, 出 = 跑(坏)
    ok = rc != 0
    结果.append(("齿：退回「只查分开不查方向」⇒ 门变红", ok))
    print(f"退出码={rc}")
    for line in 出.splitlines():
        if "✗" in line:
            print("   ", line.strip()[:150])
finally:
    原子写(F, 原文)

还 = F.read_bytes() == 原文
结果.append(("还原：字节级一致", 还))
print(f"还原字节级一致={还}")
print()
for 名, ok in 结果:
    print(("PASS " if ok else "FAIL ") + 名)
sys.exit(0 if all(ok for _, ok in 结果) else 1)
