"""中性入口：MCP stdio 服务（项目无关）。

实现见 `taixu_mcp`（历史文件名，保留以兼容既有 DSH 注册与测试）。
本模块只是入口别名，**不含任何逻辑**：

  · 作为模块导入时 —— 直接把本模块对象替换为 `taixu_mcp`，
    因此 `_jobs` / `_schedule` 等私有属性同样可达（与直接导入等价）。
  · 作为脚本运行/`-m` 运行时 —— 转调 `taixu_mcp.main()`。

用法：
    python -m agentscope_orchestrator.mcp_server
    python src/agentscope_orchestrator/mcp_server.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentscope_orchestrator import taixu_mcp as _impl

if __name__ == "__main__":
    raise SystemExit(_impl.main())

# 导入路径：整体替换为实现模块，保证属性（含下划线私有名）完全一致
sys.modules[__name__] = _impl
