"""判据：**同一构建目录里的门命令必须串行**（跨目录不许互相拖慢）。

这条判据钉的是一次真事故（2026-11-05）：
`tests/g3_sqlite.py` 与 `tests/mcp_smoke.py` 长期"未验"，一开真通道就变红，
红在 `shell_ok`：两个窗**并发**跑 `lake env lean`，第二个在
`<pkg>/.lake/config/[anonymous]/lakefile.olean.lock` 上拿到
`error: permission denied (error code: 13)`。
**手工单跑同一条命令 4~10 秒 exit 0** ⇒ 不是产物坏了，是并发抢共享构建锁。

本判据只验机制，不验 Lean（换任何"在共享目录落缓存"的构建系统都该成立）：
  ① 同 cwd 的两个门命令**不重叠**（时间轴上 start/end 成对）；
  ② **不同 cwd** 的仍可并行（证明锁是按目录、不是全局串行）；
  ③ 锁文件落在系统临时目录，**不往被测仓里写东西**（判据的仓里不许出现判据自己写出来的东西）。
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentscope_orchestrator import gate_spec as GS  # noqa: E402

坏: list[str] = []


def _脚本(标记: str, 日志: Path, 睡: float) -> str:
    """子进程：写 start → 睡 → 写 end（append 模式，天然能看出重叠）。"""
    return (
        "import sys, time\n"
        f"log = {str(日志)!r}\n"
        f"with open(log, 'a', encoding='utf-8') as f: f.write('start {标记}\\n')\n"
        f"time.sleep({睡})\n"
        f"with open(log, 'a', encoding='utf-8') as f: f.write('end {标记}\\n')\n"
    )


def _跑(标记: str, 日志: Path, cwd: Path, 睡: float, 结果: dict) -> None:
    结果[标记] = GS._run(["python", "-c", _脚本(标记, 日志, 睡)], cwd=cwd, timeout=120)


def 查同目录串行() -> float:
    """两个门命令同 cwd：日志必须 start/end 成对，不许交叉。"""
    根 = Path(tempfile.mkdtemp(prefix="lock_same_"))
    日志 = 根 / "trace.log"
    结果: dict[str, tuple] = {}
    起 = time.time()
    ts = [threading.Thread(target=_跑, args=(t, 日志, 根, 0.8, 结果)) for t in ("A", "B")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    耗 = time.time() - 起
    行 = [l.strip() for l in 日志.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"    同 cwd：耗时 {耗:.1f}s · 轨迹 {行}")
    if len(行) != 4:
        坏.append(f"同 cwd 轨迹条数 {len(行)} != 4：{行}")
        return 耗
    if 行 != ["start A", "end A", "start B", "end B"] and 行 != ["start B", "end B", "start A", "end A"]:
        坏.append(f"同 cwd 轨迹**交叉**（并发抢锁没被挡住）：{行}")
    if 耗 < 1.4:
        坏.append(f"同 cwd 耗时 {耗:.1f}s 太短 ⇒ 根本没串行")
    for t, (rc, _o, e) in 结果.items():
        if rc != 0:
            坏.append(f"同 cwd 子进程 {t} rc={rc}：{e[:120]}")
    return 耗


def 查跨目录并行() -> float:
    """两个门命令不同 cwd：必须能并行（否则我们把整套判据拖慢了）。"""
    根1 = Path(tempfile.mkdtemp(prefix="lock_x1_"))
    根2 = Path(tempfile.mkdtemp(prefix="lock_x2_"))
    日志 = Path(tempfile.mkdtemp(prefix="lock_xlog_")) / "trace.log"
    结果: dict[str, tuple] = {}
    起 = time.time()
    ts = [threading.Thread(target=_跑, args=(t, 日志, c, 1.2, 结果))
          for t, c in (("A", 根1), ("B", 根2))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    耗 = time.time() - 起
    行 = [l.strip() for l in 日志.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"    跨 cwd：耗时 {耗:.1f}s · 轨迹 {行}")
    if 耗 > 2.2:
        坏.append(f"跨 cwd 耗时 {耗:.1f}s ⇒ 锁是**全局**的，把无关目录也串行拖慢了")
    if 行[:2] == ["start A", "end A"] and 行[2:] == ["start B", "end B"]:
        坏.append(f"跨 cwd 也串行了：{行}")
    return 耗


def 查锁文件不落仓() -> None:
    """锁文件必须在临时目录，不许写进被测仓（那会污染『仓内容逐字节未变』类判据）。"""
    根 = Path(tempfile.mkdtemp(prefix="lock_pure_"))
    前 = {p.name for p in 根.iterdir()}
    结果: dict[str, tuple] = {}
    _跑("A", 根 / "trace.log", 根, 0.2, 结果)
    后 = {p.name for p in 根.iterdir()}
    print(f"    仓内新增文件：{sorted(后 - 前)}")
    多余 = (后 - 前) - {"trace.log"}
    if 多余:
        坏.append(f"门命令往构建目录里写了锁文件：{sorted(多余)}")
    import glob as _g
    if not _g.glob(str(Path(tempfile.gettempdir()) / "orch_gate_*.lock")):
        坏.append("临时目录里没有看到 orch_gate_*.lock ⇒ 串行锁可能没生效")


def main() -> int:
    print("门命令串行判据：同目录不重叠 · 跨目录仍并行 · 锁不落仓")
    查同目录串行()
    查跨目录并行()
    查锁文件不落仓()
    if 坏:
        print("门命令串行：红 —— " + "；".join(坏))
        return 1
    print("门命令串行：绿 —— **同构建目录的门命令已被串行（并发抢锁不再被读成「产物坏了」）**，"
          "跨目录仍并行，锁文件只在系统临时目录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())