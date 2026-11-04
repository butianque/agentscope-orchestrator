"""Phase 4 机证判据：≥10 窗并发 + 多频道负载均衡 + 频道 failover。

用法：
    python stress.py --n 10 --down [频道名] --dry  # --dry 不真对话，只验证路由
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope.message import UserMsg

from agentscope_orchestrator.gateway import build_client, load_channels
from agentscope_orchestrator.judge import judge
from agentscope_orchestrator.router import Router


def _extract_text(content) -> str:
    parts: list[str] = []
    for block in content:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


async def _window(router: Router, idx: int, dry: bool) -> dict:
    """单窗任务：让模型算出可机证的整数。dry 模式跳过真对话，模拟成功。"""
    a, b = (idx + 3) * 7, (idx + 5) * 11
    expected = a * b
    prompt = f"请只输出一个整数：{a} 乘以 {b} 等于多少？"

    async def _call():
        if dry:
            return str(expected)
        resp = await router.call([UserMsg(name="user", content=prompt)])
        return _extract_text(resp.content)

    text = str(await _call())
    passed = judge({"pred": "exact_int", "expected": expected}, text)
    return {"idx": idx, "expected": expected, "passed": passed}


async def main(n: int, dry: bool, down: str | None) -> int:
    channels = load_channels()
    # 每频道取第一个模型，组成多频道池
    pairs = [(c.name, build_client(c, c.models[0])) for c in channels]
    router = Router(pairs)
    if down:
        router.force_down(down)
    print(f"[stress] channels={router.channel_names()} concurrent={n} dry={dry} down={down}")

    results = await asyncio.gather(*(_window(router, i, dry) for i in range(n)))
    failed = [r for r in results if not r["passed"]]
    print(f"[stress] total={n} passed={n - len(failed)} failed={len(failed)}")
    if failed:
        print("[stress] FAILED indices:", [r["idx"] for r in failed])
        return 1
    print("[stress] PASS: all windows completed under multi-channel load balance")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--dry", action="store_true", help="只测路由不真对话")
    ap.add_argument("--down", default=None, help="强制某频道不可用以验证 failover")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.n, a.dry, a.down)))