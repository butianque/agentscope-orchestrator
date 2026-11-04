# -*- coding: utf-8 -*-
"""判据：带超时地跑**子进程树** —— `subproc_safe.跑()`。

## 它钉住的是哪一件真事故

2026-11-04，CI 被 `tests/lean_spec.py` 挂死 **26 分钟**。
那里写着「有超时、有出口」：

    subprocess.run(["lake","env","lean",…], timeout=900, capture_output=True)
    except subprocess.TimeoutExpired: ...

把超时从 900 改成 180 之后**单跑仍超过 600 秒没返回** —— 说明
**那条出口在真的被超时的进程树里根本走不到**：

    python ─> lake.exe ─> lean.exe     ← 孙进程继承并持有 stdout 管道
    超时到了只杀 lake；`communicate()` 收输出时读不到 EOF ⇒ 一直等。

⇒ 口径：**「有出口」不能只看代码里有没有 `except TimeoutExpired`**，
   得看那条出口在真进程树里走不走得到。

## 本判据怎么量（每条都能红）

① **老写法真的挂死**（不是"我觉得"）：把老写法放进一个**被观察的子进程**里，
   等 8 秒 —— 它还活着。这一条如果不成立（比如夹具没继承到管道），
   ②③④ 就都成了空话，所以它必须有。
② **新写法按时返回**：`跑(timeout=2)` ⇒ ≤5 秒返回、`超时 is True`、`返回码 is None`。
③ **超时是"没验成"，不是"它坏了"**：`超时=True` 与 `返回码=None` 分得开
   —— 结论里这两件事不许混。
④ **超时后整棵树真的死了**：夹具把孙进程 pid 写出来，超时后**按 pid 查不到它**
   （只杀直接子进程会留孤儿 `lean.exe` 继续占着 Lean 的锁）。
⑤ **快命令不受影响**：输出**完整**（用临时文件收输出，不许丢）、rc 对、中文不坏。
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

这里 = Path(__file__).resolve().parent
仓 = 这里.parent
sys.path.insert(0, str(仓))

from agentscope_orchestrator import subproc_safe as SS         # noqa: E402

夹具 = 这里 / "fixtures_proc" / "孙进程持管道.py"
PY = sys.executable

失败: list[str] = []


def 断言(条件: bool, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}")
    else:
        print(f"  FAIL {说明}")
        失败.append(说明)


def 查得到(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, timeout=30).stdout or ""
            return str(pid) in r
        os.kill(pid, 0)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def 杀树(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        else:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:                                    # noqa: BLE001
        pass


print("subproc_safe：带超时地跑子进程树（因为孙进程持有管道，subprocess 的超时管不到它）")

# ── ① 老写法真的挂死（这一条不成立，下面几条就没有意义）────────────────
with tempfile.TemporaryDirectory() as td:
    pid文件 = Path(td) / "gr.pid"
    孙pid_老 = 0
    老 = (
        "import subprocess, sys\n"
        "p = sys.argv[1]\n"
        "try:\n"
        "    subprocess.run([sys.executable, p, sys.argv[2]], capture_output=True,\n"
        "                   text=True, timeout=2)\n"
        "    print('RETURNED')\n"
        "except subprocess.TimeoutExpired:\n"
        "    print('TIMEOUT-RAISED')\n"
    )
    w = subprocess.Popen([PY, "-c", 老, str(夹具), str(pid文件)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 等到夹具把孙进程 pid 写出来（最多 10 秒）
        for _ in range(100):
            if pid文件.is_file() and pid文件.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        孙pid_老 = int(pid文件.read_text(encoding="utf-8").strip())
        time.sleep(8.0)                                  # 老写法 timeout=2，8 秒早该返回了
        断言(w.poll() is None,
             "① 老写法（subprocess.run + timeout=2）**8 秒后还活着** "
             "—— 超时到了但 `communicate()` 收输出时被孙进程的管道拖住，"
             "这就是那次 CI 挂 26 分钟的原因")
    finally:
        杀树(w.pid)
        if 孙pid_老:
            杀树(孙pid_老)
        time.sleep(0.5)

# ── ② 新写法：按时返回 ──────────────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    pid文件 = Path(td) / "gr2.pid"
    t0 = time.time()
    果 = SS.跑([PY, str(夹具), str(pid文件)], timeout=2.0)
    秒 = time.time() - t0
    断言(秒 <= 6.0,
         f"② 新写法 **{秒:.1f} 秒就返回了**（秒表实测；老写法此刻还挂着）")
    断言(果.超时 and 果.返回码 is None,
         f"② 超时如实标出：超时={果.超时}，返回码={果.返回码}（{果.行()}）")

    # ── ③ 超时 ≠ 坏了（两种结局在**渲染上**也不许长得一样）────────────
    超时行 = SS.结果(返回码=None, 输出="", 超时=True, 秒=1.0).行()
    失败行 = SS.结果(返回码=1, 输出="", 超时=False, 秒=1.0).行()
    断言("超时" in 超时行 and "rc=1" not in 超时行 and "超时" not in 失败行,
         f"③ 「没验成（超时）」与「它坏了（非零返回）」在读数上分得开："
         f"{超时行!r} vs {失败行!r} —— 一个含糊的 None 会把这两种结论合掉")

    # ── ④ 整棵树真的死了 ────────────────────────────────────────────
    time.sleep(0.5)
    try:
        孙pid = int(pid文件.read_text(encoding="utf-8").strip())
    except Exception:                                    # noqa: BLE001
        孙pid = 0
    if 孙pid:
        断言(not 查得到(孙pid),
             f"④ 超时后**整棵树**都没了（孙进程 pid={孙pid} 已查不到）"
             "—— 只杀直接子进程会留下孤儿 lean.exe 继续占着锁")
    else:
        断言(False, "④ 夹具没写出孙进程 pid，这一条没法核（不许当通过）")
        杀树(孙pid)

# ── ⑤ 快命令：输出完整、rc 对、中文不坏 ──────────────────────────────
果 = SS.跑([PY, "-c", "import sys; print('中文 OK'); sys.exit(0)"], timeout=30)
断言(not 果.超时 and 果.返回码 == 0 and "中文 OK" in 果.输出,
     f"⑤ 快命令照常：rc={果.返回码}、输出完整（{果.输出.strip()[:20]!r}）"
     "—— 换成临时文件收输出**不许丢输出**")
果 = SS.跑([PY, "-c", "import sys; print('bad'); sys.exit(7)"], timeout=30)
断言(not 果.超时 and 果.返回码 == 7,
     f"⑤ 非零退出码如实透出（rc={果.返回码}）—— 真失败不许被当成超时")

print()
if 失败:
    print(f"subproc_safe：红 {len(失败)} 项 —— {失败}")
    sys.exit(1)
print("subproc_safe：绿 —— 孙进程持管道时**老写法真的挂死、新写法按时返回且整棵树被杀**，"
      "「没验成」与「它坏了」分得开，快命令输出不丢、rc 如实")
