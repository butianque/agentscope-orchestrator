"""小工具：快重试 + 通道降级 + 单通道告警（R243）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def 通道读数() -> dict:
    """当前活通道/死通道（**实测**，不是读配置）。"""
    from agentscope_orchestrator import gateway
    try:
        r = gateway.select_live_channels("")[-1]
        return {"live": list(r.get("live") or []),
                "dead": {k: str(v)[:120] for k, v in (r.get("dead") or {}).items()}}
    except Exception as e:                       # noqa: BLE001
        return {"live": [], "dead": {}, "err": f"{type(e).__name__}: {e}"}


def 快重试策略(预算秒: float = 1800.0, 快试次数: int = 3, 快试窗秒: float = 30.0):
    """★ R243：把"等上游好"拆成两段 —— **先快试，再长退避**。

    为什么（真读数）：case02 复跑 A4 在一次上游抖动上等满 1800 秒、
    规划阶段连续失败 9 次才停机。而**多数抖动是秒级**（502/504/连接reset），
    20→40→80→160→300 的退避曲线等于"睡着了等"。⇒ 前 3 次间隔 2/4/6 秒试完，
    仍然失败才进长退避。总预算不变（有界），只是**把钱花在更有希望的地方**。
    """
    def 下一次(第几次: int, 已等: float) -> float:
        _n = max(1, int(第几次))
        if _n <= 快试次数:
            return min(2.0 * _n, max(0.0, 快试窗秒 - max(0.0, 已等)))
        _长 = min(20.0 * (2 ** min(_n - 快试次数 - 1, 4)), 300.0)
        return min(_长, max(1.0, 预算秒 - max(0.0, 已等)))
    return 下一次


def 单通道告警(读数: dict | None = None) -> str:
    """活通道 ≤1 时，**启动就说**（而不是藏在 router 的 WARNING 里）。

    ★ 为什么必须报：无人值守的全部筹码压在一个通道上时，
      "跑不起来"看起来像"模型不行/判据太严"，实际是**上游单点**。
      把这件事放在**启动第一屏**，人和事后复盘都不会误判。
    """
    r = 读数 or 通道读数()
    live, dead = r.get("live") or [], r.get("dead") or {}
    if len(live) >= 2:
        return ""
    if not live:
        return ("★★ 【通道】**当前没有任何活通道** ⇒ 无人值守一定跑不起来。"
                "死因：" + "；".join(f"{k}={v[:60]}" for k, v in list(dead.items())[:3])
                + "（缺 key 是配置错，403 是分组无权 —— 两类要分开修）")
    return (f"★★ 【通道】**只有 1 条活通道**（{live[0]}）⇒ 它一抖整趟就停。"
            f"其它 {len(dead)} 条都死了："
            + "；".join(f"{k}={(v[:50])}" for k, v in list(dead.items())[:3])
            + "｜修法：补 key（env XXX_API_KEY）或换有权的分组/模型")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = 通道读数()
    print(json.dumps(r, ensure_ascii=False, indent=1)[:1200])
    w = 单通道告警(r)
    print(w or "通道数 ≥2，无需告警")
