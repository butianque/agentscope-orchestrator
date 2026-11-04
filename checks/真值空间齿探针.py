# -*- coding: utf-8 -*-
"""R20 判据力探针（齿）：把真值空间接线逐条退回，门**必须**变红。

★ 为什么必须做：一条门如果"改回去也全绿"，它就是**无齿门**。
★ 本探针覆盖三类**不同**的齿（每类退回的**是**真缺陷，不是打字错）：
    ① 接线齿：判定层退回「六值字符串当判据」（不回真值空间）；
    ② 穷尽齿：真值空间门口放开 ⇒ **第三值**（六值字符串）溜进来；
    ③ 载体消去齿：`读()` 依赖键序（载体）⇒ 同真值不同位置。
★ 探针自身的教训一律复用（R13/R17 四次踩坑）：
    读用文本模式（归一 `\\n`）、写用 `newline="\\n"`、还原用**字节级**、
    路径从 `__file__` 推导（移到 `checks/` 后仍可移植）。
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
GG = PKG / "generic_goal.py"
TS = PKG / "truth_space.py"

env = dict(os.environ)
env["PYTHONPATH"] = str(PKG.parent)
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
# ★★ R65 互斥：两个进程同时改同一个源文件会**互相还原对方的改动**（实测各 7 红）。
#   锁的键是**被改的文件**，不是探针名 ⇒ 新增探针自动共享同一把锁。
#   ★ 多文件按**同一顺序**取锁（防两探针各持一把互相等 ⇒ 死锁）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _互斥 import 占住 as _占住, 原子写   #  noqa: E402
_锁0 = _占住(GG)   # 等不到就抛 占用错（绝不静默放行）
_锁1 = _占住(TS)   # 等不到就抛 占用错（绝不静默放行）

原文: dict[Path, bytes] = {GG: GG.read_bytes(), TS: TS.read_bytes()}
源: dict[Path, str] = {p: b.decode("utf-8") for p, b in 原文.items()}


def 跑(改动: dict[Path, str]) -> tuple[int, list[str]]:
    """按 `改动` 临时写文件、跑 generic_goal 自检、**无条件**字节级还原。"""
    try:
        for p, 内容 in 改动.items():
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(内容)
        cp = subprocess.run([str(PY), str(GG), "--selftest"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="ignore", env=env, cwd=str(根), timeout=1800)
        出 = (cp.stdout or "") + (cp.stderr or "")
        红 = [ln.strip() for ln in 出.splitlines() if "✗" in ln]
        return cp.returncode, 红
    finally:
        for p, b in 原文.items():
            原子写(p, b)


结果: list[tuple[str, bool]] = []

# ── ① 接线齿：删掉 Stuck 分支（判定层退回字符串猜测）──
针1 = "            elif _判20.已判定 and _判20.值 is False:"
assert 源[GG].count(针1) == 1, "① 锚点找不到"
坏1 = 源[GG].replace(针1, "            elif False:  # 退回：不看真值空间", 1)
rc, 红 = 跑({GG: 坏1})
结果.append((f"① 齿：删 Stuck 分支（退回字符串猜测）⇒ 门变红（退出码={rc}）",
             rc != 0 and any("TS-469" in x for x in 红)))
print(f"① 退出码={rc}  红门数={len(红)}")
for x in 红[:3]:
    print("    ", x[:130])

# ── ② 接线齿：删掉鸽笼违反检测器 ──
针2 = "            if work > _TS20_鸽笼界 and not _判20.已判定:"
assert 源[GG].count(针2) == 1, "② 锚点找不到"
坏2 = 源[GG].replace(针2, "            if False:  # 退回：无鸽笼检测器", 1)
rc, 红 = 跑({GG: 坏2})
结果.append((f"② 齿：删鸽笼违反检测器 ⇒ 门变红（退出码={rc}）",
             rc != 0 and any("TS-469" in x for x in 红)))
print(f"② 退出码={rc}  红门数={len(红)}")

# ── ③ 接线齿：鸽笼界退回**拍脑袋常数**（不从真值空间基数算）──
针3 = "            _鸽笼20 = _ts20.载体基数(_TS20_真值空间位数)"
assert 源[GG].count(针3) == 1, "③ 锚点找不到"
坏3 = 源[GG].replace(针3, "            _鸽笼20 = 40  # 退回：拍脑袋常数", 1)
rc, 红 = 跑({GG: 坏3})
结果.append((f"③ 齿：鸽笼界退回常数 ⇒ 门变红（退出码={rc}）",
             rc != 0 and any("TS-469" in x for x in 红)))
print(f"③ 退出码={rc}  红门数={len(红)}")

# ── ④ 穷尽齿：真值空间门口放开（`bool()` 强转）⇒ 六值字符串溜进来 ──
针4 = """    if v is True or v == 1:
        return True
    if v is False or v == 0:
        return False
    raise TypeError("""
assert 源[TS].count(针4) == 1, "④ 锚点找不到"
坏4 = 源[TS].replace(针4, """    if v is True or v == 1:
        return True
    if v is False or v == 0:
        return False
    return bool(v)          # 退回：强转 ⇒ 第三值（字符串）溜进真值空间
    raise TypeError(""", 1)
rc, 红 = 跑({TS: 坏4})
结果.append((f"④ 齿：门口放开（强转）⇒ 六值字符串溜进 ⇒ 门变红（退出码={rc}）",
             rc != 0 and any("第三值" in x or "穷尽" in x for x in 红)))
print(f"④ 退出码={rc}  红门数={len(红)}")
for x in 红[:3]:
    print("    ", x[:130])

# ── ⑤ 载体消去齿：`读()` 依赖键序（载体）──
针5 = "    return tuple(sorted(str(k) for k in 结果))"
assert 源[TS].count(针5) == 1, "⑤ 锚点找不到"
坏5 = 源[TS].replace(
    针5, "    return tuple(str(k) for k in 结果)   # 退回：不排序 ⇒ 读载体（键序）", 1)
rc, 红 = 跑({TS: 坏5})
结果.append((f"⑤ 齿：读() 依赖键序（载体）⇒ 门变红（退出码={rc}）",
             rc != 0 and any("载体" in x for x in 红)))
print(f"⑤ 退出码={rc}  红门数={len(红)}")

# ── ⑥ 还原：字节级一致 ──
还 = all(p.read_bytes() == b for p, b in 原文.items())
结果.append(("⑥ 还原：两文件字节级一致", 还))
print(f"⑥ 还原字节级一致={还}")

# ── ⑦ 齿自己不得恒真：**未改**时必须全绿 ──
rc, 红 = 跑({})
结果.append((f"⑦ 未改时门必须全绿（防齿自己恒红/恒真）退出码={rc}", rc == 0))
print(f"⑦ 未改 退出码={rc}")

print()
for 名, ok in 结果:
    print(("PASS " if ok else "FAIL ") + 名)
sys.exit(0 if all(ok for _, ok in 结果) else 1)
