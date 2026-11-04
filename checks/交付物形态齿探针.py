# -*- coding: utf-8 -*-
"""交付物形态判据力探针：把 R31 的每一处逐条退回 ⇒ 自检必须变红且点名。

═══════════════════════════════════════════════════════════════════
R31 的来历（**真跑抓出**，不是读出来的）
═══════════════════════════════════════════════════════════════════
在真 Rust 工程 `taixu-lingti`（`src/` 下 112 个目录、单文件最大 629KB）上
派一个中长线任务（修项目自己的判据质量门）。规划器把交付物写成**目录** `src/`
—— 这是"改 src 下若干文件"的**自然表述**。编排器把它当**文件**落盘：

    PermissionError: [Errno 13] Permission denied:
        .../work/task/r1/w0/src

⇒ 整轮**崩在写窗副本上**：不是按判据收场，也不是如实报错。

★★ 为什么这是**主路**而不是边角：
   · "改多个文件"是**通用工程开发**的常态（只改一个文件是特例）；
   · 之前只在**单文件**样例（`work/rust工程样例`，只有一个 `src/lib.rs`）上
     跑通过 ⇒ **单文件跑通掩盖了多文件的断路**。
   ★ 与 R21/R22/R23/R25 同源：**只有真工程、真规模才暴露得出来**。

═══════════════════════════════════════════════════════════════════
本探针钉住的 5 处（每一处都必须**有齿**）
═══════════════════════════════════════════════════════════════════
  ② `_交付物形态问题` 的目录识别   —— 退回 ⇒ 目录又会被当文件
  ③ `_落盘` 的防御                 —— 退回 ⇒ 用户看到裸 PermissionError（指不出根因）
  ④ `_adopt_spec` 的形态预检       —— 退回 ⇒ 形态错不再被拦
  ⑤ `_plan` 的**回喂重规划**       —— 退回 ⇒ 规划器没有改正机会，一次就死
  ⑥ `_extract_text` 的裸 str 防御  —— 退回 ⇒ 逐字符遍历 ⇒ JSON 报 Invalid control character

★ 纪律：判据写**全**（看真跑结果）；从 `__file__` 推路径；
  **字节级还原**并断言 sha1；锚点**必须唯一**；行尾**不假设**（读文件自己的）；
  检查必须**报告**，不能自己崩；**超时算红**（跑到挂死绝不能算通过）。
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

工具 = Path(__file__).resolve().parent.parent
包 = 工具 / "src" / "agentscope_orchestrator"
目标文件 = 包 / "generic_goal.py"
sys.path.insert(0, str(包.parent))

失败: list[str] = []
# ★★ R65 互斥：两个进程同时改同一个源文件会**互相还原对方的改动**（实测各 7 红）。
#   锁的键是**被改的文件**，不是探针名 ⇒ 新增探针自动共享同一把锁。
#   ★ 多文件按**同一顺序**取锁（防两探针各持一把互相等 ⇒ 死锁）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _互斥 import 占住 as _占住, 原子写   #  noqa: E402
_锁0 = _占住(目标文件)   # 等不到就抛 占用错（绝不静默放行）
原字节 = 目标文件.read_bytes()
原_sha = hashlib.sha1(原字节).hexdigest()


def 跑自检(超时: int = 600) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(包.parent)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        cp = subprocess.run([sys.executable, str(目标文件), "--selftest"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=超时, env=env)
    except subprocess.TimeoutExpired:
        return 1, f"（自检超时 >{超时}s ⇒ 判红：跑到挂死绝不能算通过）"
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def 退回(替换表: list[tuple[str, str]]) -> None:
    """按字节替换。锚点用文本模板，**行尾从文件自己的字节读出**（不假设）。"""
    行尾 = "\r\n" if b"\r\n" in 原字节 else "\n"
    b = 原字节
    for 旧文本, 新文本 in 替换表:
        旧 = 旧文本.replace("\n", 行尾).encode("utf-8")
        新 = 新文本.replace("\n", 行尾).encode("utf-8")
        n = b.count(旧)
        if n != 1:
            raise AssertionError(f"锚点出现 {n} 次（要求唯一）：{旧文本[:80]!r}")
        b = b.replace(旧, 新)
    原子写(目标文件, b)


def 场景(名: str, 替换表: list[tuple[str, str]], 要点: str) -> None:
    try:
        退回(替换表)
        rc, 出 = 跑自检()
        if rc == 0:
            失败.append(f"{名} ★ 门无齿：退回后自检**仍全绿**")
        elif 要点 not in 出:
            失败.append(f"{名} 自检红了但**没点名 {要点}**（红错地方）："
                        f"{出.strip()[-260:]}")
        else:
            print(f"PASS {名} 退回后自检红且点名 {要点}")
    except AssertionError as e:
        失败.append(f"{名} 探针锚点失效（实现改过了？）：{e}")
    finally:
        原子写(目标文件, 原字节)


# ── ① 反自证 ──────────────────────────────────────────────────
rc, 出 = 跑自检()
if rc != 0:
    失败.append(f"① 未改时自检竟然红（探针自身有问题）：{出.strip()[-300:]}")
else:
    print("PASS ① 未改时自检全绿（反自证）")

# ── ② 目录识别退回 ────────────────────────────────────────────
场景("②", [('        if full.is_dir():\n'
            '            问题.append(f"`{raw}` 在仓库里**是一个目录** ⇒ 不是文件")',
            '        if False:\n'
            '            问题.append(f"`{raw}` 在仓库里**是一个目录** ⇒ 不是文件")')],
     "【R31】")

# ── ③ 落盘防御退回 ────────────────────────────────────────────
场景("③", [('    if p.is_dir():\n'
            '        raise 交付物形态错(',
            '    if False:\n'
            '        raise 交付物形态错(')],
     "【R31】落盘到目录")

# ── ④ `_adopt_spec` 形态预检退回 ──────────────────────────────
场景("④", [('        if _形态问题:\n'
            '            raise 交付物形态错(_交付物形态提示(_形态问题))',
            '        if False:\n'
            '            raise 交付物形态错(_交付物形态提示(_形态问题))')],
     "【R31】`_adopt_spec` 未拒目录")

# ── ⑤ 回喂重规划退回（上限改成 1 ⇒ 一次就死，模型没有改正机会）──
# ★★ 锚点随 R80b 改过（2026-10-17）：`for 第次 in range(3):` 现在出现**两次**
#   （首次规划 `_plan` 一条、重规划 `_replan` 一条 —— 这正是 R80b 的目的：
#    同一个可纠正的错，走两条路要有**同一种命运**）。
#   ⇒ 锚点必须**带上下文**才唯一。用重规划那条（它带 `base_prompt`）。
#   ★ 顺带说明：本探针只钉 `_plan` 那条路；`_replan` 那条由
#     `generic_goal` 自检的【R80】齿按路验。两处口径不重复。
场景("⑤", [("            for 第次 in range(3):\n                prompt = _planner_prompt(self.goal, snapshot, 反馈, 门态,",
            "            for 第次 in range(1):\n                prompt = _planner_prompt(self.goal, snapshot, 反馈, 门态,")],
     "【R31】回喂次数不对")

# ── ⑥ `_extract_text` 的裸 str 防御退回 ───────────────────────
场景("⑥", [("    if isinstance(content, str):\n        return content",
            "    if False:\n        return content")],
     "【R31】`_extract_text` 对裸 str")

# ── ⑦ 还原 ────────────────────────────────────────────────────
if hashlib.sha1(目标文件.read_bytes()).hexdigest() != 原_sha:
    失败.append("⑦ 未还原干净（generic_goal.py 与开跑前不字节级一致）")
elif 跑自检()[0] != 0:
    失败.append("⑦ 还原后自检不绿")
else:
    print("PASS ⑦ 还原：generic_goal.py 与探针开跑前**字节级一致**且自检绿")

print()
if 失败:
    print("FAIL")
    for x in 失败:
        print("  ✗", x)
    raise SystemExit(1)
print("交付物形态齿探针：全部 PASS（目录识别 · 落盘说清根因 · 预检拦截 · "
      "回喂重规划 · 裸 str 防御 · 字节级还原）")
