"""测试共用：**选一个真可用通道**，别再写 `channels[0]`。

★ 病根（2026-11-01 实测，三份测试同病）：
  `smoke.py:44` 写的是 `ch = channels[0]`。配置里第一个通道是 `aaa`，本机没有 key
  ⇒ 直接 SKIP 返回 3。而**同一时刻** maturity 的 A 判据实测 `dddddd:可达(9)`。
  ⇒ 墙上的字是「未验」，真实原因是**选通道的方式没看健康状况**：
    机器明明有可用通道，验收却永远 exit 3。三类"未验"里有两类（g3/mcp_smoke）
  卡在同一处，只因它们把不带 channel 的提交交给服务器、服务器又按配置顺序取第一个。

★ 口径（与 run_all 一致）：
  · 有可用通道 ⇒ 返回它的名字，测试真跑；
  · 全无可用（每个都硬死：凭据不可读/模型不存在）⇒ 返回空名，调用方记 3（未验），
    **不冒充通过、也不冒充缺陷**；"读不到"（超时/5xx）仍算可用候选 —— 真调用才是判据。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _强制不走模型() -> bool:
    """`ORCH_TESTS_NO_MODEL=1` ⇒ 直接按"未验"走。

    ★ 为什么需要这个开关：真跑一段要**分钟级**（一次 dry 提交要真调模型产窗）。
      这不是缺陷，是"真跑"的定义。但没有开关时，"快速冒烟"和"完整验收"只能用
      同一档成本 —— 开发中反复跑全套会被真跑段拖到几十分钟。
      ⇒ 默认**真跑**（有通道就跑，这是判据），要快就显式关掉，
        并接受这一段回到 `3`（未验，不算通过）。
    """
    return (os.environ.get("ORCH_TESTS_NO_MODEL") or "").strip().lower() in (
        "1", "true", "yes", "on")


def 选可用通道(超时: float = 12.0, 说: bool = True):
    """返回 `(通道名, Channel|None, 探活报告)`；全不可用时通道名为 `""`。

    只用公开入口（`load_channels` + `select_live_channels`），不读内部断言。
    """
    from agentscope_orchestrator.gateway import load_channels, select_live_channels

    if _强制不走模型():
        if 说:
            print("[通道] ORCH_TESTS_NO_MODEL=1 ⇒ 本段真跑跳过（记未验 3）", flush=True)
        return "", None, {"why": "ORCH_TESTS_NO_MODEL=1（调用方显式跳过真跑）"}

    chans = load_channels()
    if not chans:
        return "", None, {"why": "channels.yml 为空或读不到"}
    候选, 报告 = select_live_channels("", probe=True, timeout=超时)
    if 候选:
        c = 候选[0]
        if 说:
            其余 = [x.name for x in chans if x.name != c.name]
            print(f"[通道] 选中 {c.name}（探活通过）｜其余候选 {其余}", flush=True)
        return c.name, c, 报告
    if 说:
        print("[通道] 无可用通道（逐条原因）：", flush=True)
        for 桶 in ("dead", "unreadable"):
            for k, v in (报告.get(桶) or {}).items():
                print(f"  · {k} [{桶}] {str(v)[:90]}", flush=True)
    return "", None, 报告
