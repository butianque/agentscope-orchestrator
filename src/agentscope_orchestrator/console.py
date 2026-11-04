"""中性入口：只读任务控制台（项目无关）。

实现见 `taixu_console`（历史文件名，保留以兼容既有启动脚本与文档）。
本模块只是入口别名，**不含任何逻辑**：

  · 作为模块导入时 —— 直接把本模块对象替换为 `taixu_console`。
  · 作为脚本运行/`-m` 运行时 —— 转调 `taixu_console.main()`。

用法：
    python -m agentscope_orchestrator.console
    python src/agentscope_orchestrator/console.py --port 8088
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentscope_orchestrator import taixu_console as _impl

if __name__ == "__main__":
    raise SystemExit(_impl.main())

sys.modules[__name__] = _impl
