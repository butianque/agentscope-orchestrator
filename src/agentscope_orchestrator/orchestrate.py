"""Phase 1 最小可跑闭环：调度器 + N 个并发 worker 窗，各自跑不同任务，
判据门过滤，收敛裁决。支持 --inject-429 故障注入验证自动重连。

用法：
    python orchestrate.py --demo
    python orchestrate.py --demo --inject-429
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from .msgs import UserMsg

from agentscope_orchestrator.gateway import build_client, load_channels
from agentscope_orchestrator.judge import judge
from agentscope_orchestrator.supervisor import TransientError, call_with_retry

logging.basicConfig(level=logging.WARNING)

# 演示任务：三窗不同算式，判据均为"整数值==42"，机器可算。
_DEMO_TASKS = [
    {"id": "w0", "prompt": "请只输出一个整数：17 加 25 等于多少？", "expected": 42},
    {"id": "w1", "prompt": "请只输出一个整数：50 减去 8 等于多少？", "expected": 42},
    {"id": "w2", "prompt": "请只输出一个整数：7 乘以 6 等于多少？", "expected": 42},
]


def _extract_text(content) -> str:
    parts: list[str] = []
    for block in content:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


async def _worker(task, model, fail_on_id: str | None) -> dict:
    """单个 worker 窗：带故障注入支持 + 监督重试 + 判据门。"""
    state = {"n": 0}

    async def call():
        if fail_on_id == task["id"] and state["n"] == 0:
            state["n"] += 1
            raise TransientError("injected 429 for " + task["id"])
        resp = await model([UserMsg(name="user", content=task["prompt"])])
        return _extract_text(resp.content)

    text = await call_with_retry(call)
    criterion = {"pred": "exact_int", "expected": task["expected"]}
    passed = judge(criterion, text)
    return {"id": task["id"], "text": text, "passed": passed, "retried": state["n"] > 0}


async def run_demo(n_windows: int, channel_name: str, fail_on: str | None) -> int:
    # ★★★★★ R157（2026-10-25）：**这是第三个自己拼通道的地方**（齿抓出来的）。
    #   原来：`load_channels()` → 按名取一个 → `ch.models[0]`
    #   ⇒ ① 完全不探活（死通道也拿来用）；② 每通道只取第一个模型。
    #   ★ 我是**先写了「只有一处入口」这条齿**，它才把这个文件点出来的
    #     —— 手工枚举三遍会漏，齿一次就抓到。
    #     「能不枚举就别枚举」在这里的落地就是：**让机器去数**。
    from agentscope_orchestrator.gateway import build_adaptive_router
    router, _槽们157, _报告157 = build_adaptive_router(
        channel_name, probe=True)
    _首槽 = _槽们157[0]
    print(f"[demo] 可用模型槽：{'；'.join(_槽们157)} ⇒ 用 {_首槽}")
    model = dict(router.槽对).get(_首槽)
    if model is None:
        raise RuntimeError(f"自适应 Router 里取不到槽 {_首槽}")
    tasks = _DEMO_TASKS[:n_windows]

    results = await asyncio.gather(*(_worker(t, model, fail_on) for t in tasks))

    print(f"[demo] slot={_首槽} windows={len(tasks)}")
    for r in results:
        print(
            f"[demo]   {r['id']}: passed={r['passed']} retried={r['retried']} "
            f">>{r['text'].strip()}<<"
        )
    all_pass = all(r["passed"] for r in results)
    print("[demo] ALL_PASS" if all_pass else "[demo] SOME_FAILED")
    return 0 if all_pass else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="跑最小闭环 demo")
    ap.add_argument("--n", type=int, default=3, help="worker 窗数（默认3）")
    ap.add_argument("--channel", default="", help="频道名（空=全通道，单通道故障可降级）")
    ap.add_argument("--inject-429", dest="fail_on", default=None,
                    const="w0", nargs="?", help="某窗首次调用注入瞬态故障")
    args = ap.parse_args()
    if not args.demo:
        print("run with --demo")
        return 2
    return asyncio.run(run_demo(args.n, args.channel, args.fail_on))


if __name__ == "__main__":
    raise SystemExit(main())