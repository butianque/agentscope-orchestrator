"""G5 判据：任务队列 / 优先级 / 依赖链（调度层确定性单测，不触真实 lake build）。

做法：导入 taixu_mcp，用合成 jobs 字典驱动调度函数，并打桩 _start_job/_active_count/
_db_save_one 使 _schedule 只做"判定+排队+放行"而不真派发任务。

覆盖（机器可算，无模型）：
  依赖未满足 → queued（waiting）；依赖失败/不存在 → error；依赖达成 → 放行；
  高优先级就绪任务先于低优先级；并发上限约束在跑数。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agentscope_orchestrator.taixu_mcp as tm

saved = {
    "jobs": tm._jobs,
    "start": tm._start_job,
    "active": tm._active_count,
    "save": tm._db_save_one,
}


def _mk(state, prio=0, deps=None, created=0):
    return {"state": state, "priority": prio, "depends_on": list(deps or []),
            "created": created}


def main() -> int:
    started = []
    tm._db_save_one = lambda *a, **k: None          # 不污染真实 jobs.db
    tm._active_count = lambda: 0                    # 并发计数归零，便于断言

    def fake_start(jid, resume=None):
        started.append(jid)
        tm._jobs[jid]["state"] = "running"          # 与真 _start_job 一致
    tm._start_job = fake_start
    tm._CONCURRENCY = 2

    # ── 场景 1：依赖未满足排队 / 依赖失败改判 / 高优先级优先 / 并发上限 ──
    tm._jobs = {
        "A": _mk("error", prio=0, created=1),          # 依赖已失败
        "B": _mk("queued", prio=0, deps=["A"], created=2),   # 依赖失败 → error
        "C": _mk("queued", prio=5, created=4),         # 就绪，优先级最高
        "D": _mk("queued", prio=1, created=3),         # 就绪，次高
        "E": _mk("queued", prio=0, deps=["Z"], created=5),   # 依赖不存在 → error
        "F": _mk("queued", prio=0, created=6),         # 就绪，被并发上限挡住
    }

    # 调度前：依赖未满足（A 是 error）→ B 判定 failed
    assert tm._dep_state("B")[0] == "failed"
    assert tm._dep_state("E")[0] == "failed"
    assert tm._dep_state("C")[0] == "ready"
    assert tm._dep_state("D")[0] == "ready"

    # 排队位次（仅就绪入队，优先级降序）：C(5) → D(1) → F(0)；B/E 依赖失败不入队
    assert tm._queue_order() == ["C", "D", "F"]
    assert tm._queue_order()[0] == "C"

    tm._schedule()
    # 并发上限 2：只派发就绪中优先级最高的 C、D
    assert set(started) == {"C", "D"}
    # 依赖失败/不存在的排队任务 → 改判 error
    assert tm._jobs["B"]["state"] == "error"
    assert tm._jobs["E"]["state"] == "error"
    assert tm._jobs["F"]["state"] == "queued"          # 仍排队等并发空位

    # ── 场景 2：依赖达成后放行（排队 → 满足 → 运行）──
    # ★ 2026-10-31（交付审计）：原写 "staged" 是**过时口径**，产品随后刻意改成
    #   「依赖只认 promoted」（taixu_mcp._dep_state 的 R2-F3）：staged = 产物只在
    #   products/ 暂存、主树没变 ⇒ 放行下游 = 下游在**旧树**上开跑（静默用不存在的前提）。
    #   所以这里要用 promoted 才能表达"依赖真的满足了"；用 staged 是在断言错误行为。
    tm._jobs["A"]["state"] = "promoted"                # 原依赖 A 由失败改为**真落地**
    tm._jobs["B"] = _mk("queued", prio=0, deps=["A"], created=7)
    tm._CONCURRENCY = 3                                # 放宽并发，让 B 可入
    started.clear()
    tm._schedule()
    assert "B" in started, "依赖满足后应放行排队任务"
    assert tm._jobs["B"]["state"] == "running"

    # 恢复现场
    tm._start_job = saved["start"]
    tm._active_count = saved["active"]
    tm._db_save_one = saved["save"]
    tm._jobs = saved["jobs"]
    print("[g5] PASS: queuing / deps-chain / priority-order / concurrency-cap OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())