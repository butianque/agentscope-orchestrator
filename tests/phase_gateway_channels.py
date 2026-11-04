"""编排器频道解析回归（G18）：`select_channels` 必须正确。

背景：`--channel` 逗号列表不展开导致 Router([]) -> `no healthy channel`，
把配置错误伪装成模型通道故障（千条假 blocked 元凶之一）。本测试把修复钉死。
纯本地读 config/channels.yml，不触网络/模型/引擎。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.gateway import select_channels, load_channels

PASS = "PASS"
FAIL = "FAIL"


def main() -> int:
    fails: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(("  ok   " if cond else "  FAIL ") + name)
        if not cond:
            fails.append(name)

    avail = [c.name for c in load_channels()]
    print(f"可用频道={avail}")

    # 空串 / 仅空白 ⇒ 全通道
    check("空串->全通道", [c.name for c in select_channels("")] == avail)
    check("空白->全通道", [c.name for c in select_channels("  ")] == avail)
    # 单选
    check("单选精确匹配", [c.name for c in select_channels("ggg")] == ["ggg"])
    # 逗号多选（旧缺陷路径）
    if "aaa" in avail and "dddddd" in avail:
        check("逗号多选展开", [c.name for c in select_channels("aaa,dddddd")] == ["aaa", "dddddd"])
    # 未知频道必须抛清晰错误（不静默成 [])
    for bad in ("zzz", "nope1,nope2"):
        try:
            select_channels(bad)
            check(f"未知频道 {bad} 抛错", False)
        except ValueError:
            check(f"未知频道 {bad} 抛错", True)

    if fails:
        print(f"phase_gateway_channels: {len(fails)} 项失败 -> {fails}")
        return 1
    print("phase_gateway_channels: 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())