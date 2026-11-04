"""自动生产线后台派发器（脱离 DSH/MCP 独立运行）。

复用 G7 引擎三段机制 + real_task 真跑管线，构成无人值守循环：
  建库扫描 -> 选题(未闭合+依赖缺口) -> 生成模板(GateSpec) -> 真跑(n窗+判据门+收敛)
  -> 回执写盘 -> 下一题；无新未闭合候选即停机。

回执/进度实时写 .audit_trail：
  <audit>/autodispatch_receipt.jsonl   追加式事件流（每行一条事件，可 tail/follow 实时看）
  <audit>/autodispatch_live.json       当前状态快照
  <audit>/autodispatch_done.json       已收口/已放弃的定理（进程重启续跑去重）
  <audit>/autodispatch_run.log         STDOUT 运行日志

参数：默认真跑 n=2 min_pass=1，产物 staged 落 products/（不 promote，不改库）。
用法：
  venv\\Scripts\\python.exe src\\agentscope_orchestrator\\autodispatch.py [--limit N] [--dry]
诚实边界：只证"能扫/能选/能真跑过门/回执可查"，不承诺自动产物无 bug或"满分/跨域通用"；
收敛兜底——未过门不写回。停机标准（ARC/点云/GGP 满分）作为阶段愿景，由人工+判据门逐步逼近。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import agentscope_orchestrator.real_task as rt
from agentscope_orchestrator.autoengine import MemoryIndex, scan_all
from agentscope_orchestrator import autoengine as ae
from agentscope_orchestrator import ts_receipt
from agentscope_orchestrator import profile as prof_mod

logging.basicConfig(level=logging.WARNING)

ORCH = rt.ORCH
ARTIFACT_DIR = rt.ARTIFACT_DIR
PRODUCTS = rt.PRODUCTS
AUDIT = rt.AUDIT

_RECEIPT = AUDIT / "autodispatch_receipt.jsonl"
_LIVE = AUDIT / "autodispatch_live.json"
_DONE = AUDIT / "autodispatch_done.json"


def _load_done() -> list[str]:
    try:
        return json.loads(_DONE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_done(done: list[str]) -> None:
    _DONE.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")


def _emit(event: dict) -> None:
    event = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **event}
    line = json.dumps(event, ensure_ascii=False)
    with _RECEIPT.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    _LIVE.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[receipt] {line}", flush=True)


async def _run_one(cand: ae.Candidate, dry: bool, seq: int) -> dict:
    gen = ae.gen_lean_candidate(cand, ARTIFACT_DIR)
    task = rt.TaskSpec(id=f"ad{seq:03d}", unit_id=gen["unit_id"],
                       module=gen["module"], theorem=gen["theorem"],
                       statement_marker=gen["statement_marker"],
                       target_rel=gen["target_rel"], template=gen["template"],
                       min_pass=1, n_windows=2, gates=gen["gates"])
    task.promote = False  # 产物 staged 落 products/，不改产物库
    status: dict = {}
    _emit({"kind": "dispatch", "seq": seq, "task": task.id,
           "unit_id": task.unit_id, "theorem": task.theorem,
           "deps": len(gen["deps"]), "dry": dry})
    try:
        rc = await rt.run_task(task, task.n_windows, "", task.promote,
                               dry, status=status)
    except Exception as exc:
        status["state"] = "error"
        status["error"] = f"{type(exc).__name__}: {exc}"
        rc = -1
    s = status.get("state")
    _emit({"kind": "settled", "seq": seq, "task": task.id,
           "theorem": task.theorem, "state": s, "rc": rc,
           "passed": status.get("passed"), "failed": status.get("failed"),
           "product": status.get("product"), "error": status.get("error"),
           "final_gates": status.get("final_gates")})
    # 每题渲染一份人类可读 .md 回执（遵循 TS-XXX 模板）；机器底料仍是上方 JSON
    try:
        batch = ts_receipt.claim_batch(prof_mod.get_profile())
        ts_receipt.render(
            repo_root=prof_mod.get_profile(), batch=batch, window="autodispatch",
            nature="证明回执（Lean 收口自动循环）",
            topic=f"autodispatch_{task.theorem}_{time.strftime('%m%d%H%M%S')}",
            summary=(f"**state={s}**；theorem=`{task.theorem}`；"
                     f"unit=`{task.unit_id}`；passed={status.get('passed')}"
                     f"/failed={status.get('failed')}。"),
            gates=status.get("final_gates"),
            product=status.get("product"),
            notes=([] if s == "staged"
                   else [f"未过收敛：state={s}",
                         f"err={status.get('error')}"]))
    except Exception as exc:  # noqa: BLE001 回执渲染失败不阻断证明循环
        _emit({"kind": "md_render_error",
               "err": f"{type(exc).__name__}: {exc}"})
    return {"task": task, "state": s, "status": status}


async def dispatch(limit: int, dry: bool) -> int:
    done = _load_done()
    mem = scan_all(ARTIFACT_DIR, [ORCH / "src", ORCH / "tests"])
    _emit({"kind": "start", "dry": dry, "limit": limit,
           "index": mem.sizes()})
    seq = 0
    emitted = 0
    while True:
        cands = ae.select_unclosed(mem, ARTIFACT_DIR, limit=50)
        cands = [c for c in cands
                 if f"{c.unit_id}::{c.theorem}" not in done]
        if not cands:
            _emit({"kind": "no_candidates", "dispatched": seq,
                   "emitted": emitted})
            break
        if limit and emitted >= limit:
            _emit({"kind": "limit_reached", "dispatched": seq,
                   "emitted": emitted, "limit": limit})
            break
        cand = cands[0]
        seq += 1
        res = await _run_one(cand, dry, seq)
        done.append(f"{cand.unit_id}::{cand.theorem}")
        _save_done(done)
        if res["state"] == "staged":
            emitted += 1
        # 无论过/败继续下一题（优胜劣汰循环），直到无候选或达限
    _emit({"kind": "halt", "dispatched": seq, "emitted": emitted})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="最多产出台数（0=不限，跑到无候选）")
    ap.add_argument("--dry", action="store_true", help="dry 不真对话（写已知正确证明）")
    a = ap.parse_args()
    return asyncio.run(dispatch(a.limit, a.dry))


if __name__ == "__main__":
    raise SystemExit(main())