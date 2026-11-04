# -*- coding: utf-8 -*-
"""R16 判据力探针（齿）：把顶层判定**退回经典层**，门必须变红。

★ 为什么必须做（库的纪律）：一条门如果"改回去也全绿"，它就是**无齿门**。

★ 探针自身的两轮教训（如实登记）：
  ① 第一版把包**复制到临时目录**再改 —— 行不通：包内按 `__file__` 解析
     `config/channels.yml` 等路径，副本里全找不到 ⇒ 抛 FileNotFoundError
     ⇒ 探针判"FAIL（无齿门）"，其实是**探针错**（假红）。
  ② 改为**原位回退 + `finally` 保证还原**。窗口约 10 秒。
     ★ 还原写在 `finally`，且用**字节级**保存/回写，异常也不丢原文。
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

原文 = F.read_bytes()                      # ★ 字节级原文，用于 finally 还原
# ★★ 探针自身的第三轮教训（如实登记）：源文件是 **CRLF**。
#   `read_bytes().decode()` 保留 `\r\n`，而 `write_text` 在 Windows 上
#   又把每个 `\n` 转成 `\r\n` ⇒ 已有的 `\r\n` 变成 `\r\r\n` ⇒ **语法崩**
#   （报成 `IndentationError`，位置还落在文档字符串里 ⇒ 极难定位）。
#   修法：**读用文本模式归一成 `\n`，写用 `newline="\n"` 固定**。
源 = F.read_text(encoding="utf-8")         # 归一成 \n

新块首 = "            from agentscope_orchestrator import shushu_logic as _sl16"
i = 源.index(新块首)
# ★★★ 探针自身的第四轮教训（如实登记）：回退范围原来止于 `if done_ok:`，
#   而 R20 在 `_sl16` 块与 `if done_ok:` 之间插入了**真值空间**（TS-469）代码
#   ⇒ 回退把 R20 的代码也删了 ⇒ 变红的是 **R20 的齿**，不是 TS-468 的齿
#   ⇒ 判据（`"经典层合取" in 出 or "接线断了" in 出`）不成立 ⇒ 探针判 FAIL
#   （**假红**：门其实有齿，是探针回退过头）。
#   修法：回退**止于 R20 块之前**，只回退 TS-468 那一块。
j = 源.index("            _向量20 = _TS20_向量(", i)
旧块 = ("            done_ok = (verdict == \"converged\" and "
        "bool(alignment.get(\"fixed_point\"))\n"
        "                       and witness_ok)\n")
回退 = 源[:i] + 旧块 + 源[j:]

env = dict(os.environ)
env["PYTHONPATH"] = str(PKG.parent)
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"

try:
    with open(F, "w", encoding="utf-8", newline="\n") as _f:
        _f.write(回退)
    cp = subprocess.run([str(PY), str(F), "--selftest"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="ignore", env=env, cwd=str(根), timeout=1800)
    出 = (cp.stdout or "") + (cp.stderr or "")
finally:
    原子写(F, 原文)                    # ★ 无条件还原
    print("已还原原文：", F.read_bytes() == 原文)

print(f"回退后退出码 = {cp.returncode}")
print(f"stdout {len(cp.stdout or '')} 字符 / stderr {len(cp.stderr or '')} 字符")
for line in 出.splitlines():
    if "自检" in line or "✗" in line:
        print("  ", line.strip()[:170])
print()
# ★ 判据：门必须**变红**，且因由指向经典层形态（两条因由任一即可 ——
#   回退同时触发「退回经典层合取」与「未走格裁决」，后者是更本质的那条）。
抓到 = cp.returncode != 0 and ("经典层合取" in 出 or "接线断了" in 出)
print("⇒", "PASS（门有齿：回退到经典层立即变红）" if 抓到
      else "FAIL（**无齿门**：回退后仍全绿）")
sys.exit(0 if 抓到 else 1)
