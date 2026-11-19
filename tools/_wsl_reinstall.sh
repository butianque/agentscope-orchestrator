set -u
PY="$HOME/.orch-venv2/bin/python"
$PY -m pip install -q --force-reinstall --no-deps --disable-pip-version-check "/mnt/e/基础设施/离散时空认知物理基础研究/太虚元道/多智能体编排/agentscope_orchestrator/dist/orchestrator-0.12.3/wheels/agentscope_orchestrator-0.12.3-py3-none-any.whl" 2>&1 | tail -n 1
$PY -c "from importlib.metadata import version as v; print('  WSL:', v('agentscope-orchestrator'))"
