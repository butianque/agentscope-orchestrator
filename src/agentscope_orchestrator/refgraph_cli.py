"""建引用图（索引期用的命令行）。

    python -m agentscope_orchestrator.refgraph_cli [--刷新]

为什么单独一条命令：建图要读 7000+ 文件、约 145 秒，**属于索引期**；
问答期只许读缓存（`refgraph.只读缓存`）。把它做成显式动作，
是因为"什么时候花 145 秒"必须由人/CI 决定，不能藏在一次问答背后。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from . import refgraph as RG
    from .profile import get_profile

    参数 = list(sys.argv[1:] if argv is None else argv)
    档案 = get_profile()
    根 = Path(档案.repo_root)
    t = time.time()
    图 = RG.项目图(刷新=True) if "--刷新" in 参数 or "-f" in 参数 else RG.项目图()
    秒 = time.time() - t
    缓存 = RG.缓存路径(根)
    print(f"[refgraph] {秒:.1f}s · 节点 {len(图.节点)} · 边 {len(图.边)} · "
          f"死引用 {len(图.死引用)} · 歧义 {len(图.歧义)} · 缓存 {缓存}"
          f"{'（' + str(round(缓存.stat().st_size / 1048576, 1)) + ' MB）' if 缓存.is_file() else ''}")
    统计 = 图.统计
    print(f"[refgraph] 边分类：" + " · ".join(f"{k} {v}" for k, v in 统计.items() if k.startswith("边")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())