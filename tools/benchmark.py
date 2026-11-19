"""薄包装：真正实现在包内 `agentscope_orchestrator.benchmark`（随 wheel 交付）。

为什么留这个文件：仓库里习惯用 `tools/xxx.py` 当入口；但**能力本体必须在包里**，
否则装完之后调不到（第一版就吃了这个亏：Linux 上记分卡 L 判红）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentscope_orchestrator.benchmark import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
