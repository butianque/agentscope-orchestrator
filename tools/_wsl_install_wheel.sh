set -u
VENV="$HOME/.orch-venv2"
M="https://pypi.tuna.tsinghua.edu.cn/simple"
python3 -m venv "$VENV" 2>/dev/null || true
"$VENV/bin/python" -m pip install -q --disable-pip-version-check --default-timeout 90 -i "$M" "/mnt/e/基础设施/离散时空认知物理基础研究/太虚元道/多智能体编排/agentscope_orchestrator/dist/orchestrator-0.2.0/wheels/agentscope_orchestrator-0.2.0-py3-none-any.whl" 2>&1 | tail -n 4
echo "== 装完的版本与依赖 =="
"$VENV/bin/python" -c "from importlib.metadata import version as v; print('  pkg:', v('agentscope-orchestrator'))"
"$VENV/bin/python" -c "import mcp, importlib.metadata as md; print('  mcp:', md.version('mcp'))"
"$VENV/bin/python" -c "from mcp.server.fastmcp import FastMCP; print('  FastMCP: ok')" 2>&1 | tail -n 2