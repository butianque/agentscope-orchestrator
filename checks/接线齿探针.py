# -*- coding: utf-8 -*-
"""R17 判据力探针（齿）+ 剥注释机制的反假红验证。

三条：
  ① **齿**：把 `bool(results) and all(...)` 放回**代码**里 ⇒ 门必须变红；
  ② **不假红**：只在**注释**里写那个串 ⇒ 门必须**仍绿**
     （这正是 R12 保位剥注释机制存在的理由，第三次自匹配假红换来的）；
  ③ **还原**：跑完原文必须字节级不变。

★ 教训复用：源文件是 **CRLF** —— 读用文本模式（归一 `\n`），
  写用 `newline="\n"`；还原用**字节级**。
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
F = PKG / "generic_goal.py"
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
                            errors="ignore", env=env, cwd=str(根), timeout=1800)
        return cp.returncode, (cp.stdout or "") + (cp.stderr or "")
    finally:
        原子写(F, 原文)


结果: list[tuple[str, bool]] = []
try:
    # ① 齿：把布尔合取放回**代码**（改一处生产判定）
    针 = "            ok = bool(_sl17.从门结果(results)[0])"
    assert 源.count(针) >= 1, "找不到 R17 判定点"
    坏 = 源.replace(针, "            ok = bool(results) and "
                        "all(v is True for v in results.values())", 1)
    rc, 出 = 跑(坏)
    ok1 = rc != 0 and "因由仍被压平" in 出
    结果.append(("① 齿：代码里回退 ⇒ 门变红", ok1))
    print(f"① 退出码={rc}；抓到「因由仍被压平」={'因由仍被压平' in 出}")

    # ② 不假红：只在**注释**里写那个串
    好 = 源.replace("def _align_windows(",
                    "# 这里故意写一句注释：bool(results) and all(...) 是旧写法\n"
                    "def _align_windows(", 1)
    assert 好 != 源, "注释注入失败"
    rc2, 出2 = 跑(好)
    ok2 = rc2 == 0 and "全绿" in 出2
    结果.append(("② 不假红：注释里写了 ⇒ 门仍绿", ok2))
    print(f"② 退出码={rc2}；全绿={'全绿' in 出2}")
finally:
    原子写(F, 原文)

还 = F.read_bytes() == 原文
结果.append(("③ 还原：字节级一致", 还))
print(f"③ 还原字节级一致={还}")
print()
for 名, ok in 结果:
    print(("PASS " if ok else "FAIL ") + 名)
sys.exit(0 if all(ok for _, ok in 结果) else 1)
