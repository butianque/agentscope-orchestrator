"""Phase 3 机证判据：覆盖不变量(registered==discovered) + 可达性不漏 + 按需取回。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.index import UnitIndex

# ★★ 2026-10-31（交付审计）：原来这里写死 `parents[1] / "src"` —— **只对源码树成立**。
#   本文件现在随 wheel 进 `site-packages/agentscope_orchestrator/tests/`，
#   那里 `parents[1]` 是包目录、下面根本没有 `src/` ⇒ 扫到 0 个单元
#   ⇒ 断言报 "scan must discover units"，看着像产品缺陷，实际是**脚本假定错了布局**。
#   ⇒ 显式区分两种布局：有源码树就扫源码树；装成包就扫**已安装的包目录本身**
#     （它同样是一堆 .py，覆盖性/可达性/按需取回这三条不变量照样成立）。
_此处 = Path(__file__).resolve()
_源码树 = _此处.parents[1] / "src"
ROOT = _源码树 if _源码树.is_dir() else _此处.parents[1]
print(f"[phase3] 扫描根 = {ROOT}（{'源码树' if _源码树.is_dir() else '安装布局'}）")

idx = UnitIndex()
reg, disc = idx.build(ROOT, glob="**/*.py")

# 1) 覆盖不变量：registered 集合覆盖全部 discovered 单元，大小非零
assert disc > 0, "scan must discover units"
assert reg > 0, "index must be non-empty"
assert idx.coverage_holds(), "coverage invariant must hold"

# 2) 可达性不漏：每个 discovered 文件都能经索引 query 定位到
for p in ROOT.rglob("**/*.py"):
    if p.is_file():
        assert idx.query(p.stem) is not None, f"filename unreachable: {p.stem}"
        assert idx.query(str(p)) is not None, f"fullpath unreachable: {p}"

# 3) 按需取回：query 返回路径，fetch 才读盘拿到内容
p = idx.query("gateway")
assert p and p.is_file()
content = idx.fetch("gateway")
assert content and "OpenAIChatModel" in content

# 4) 未知 key 返回 None（不漏≠万能，诚实边界）
assert idx.query("__no_such_unit__") is None

print(f"[phase3] PASS: registered={reg} discovered={disc} coverage_holds={idx.coverage_holds()}")
sys.exit(0)