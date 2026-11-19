set -u
PY="$HOME/.orch-venv2/bin/python"
export ORCH_CONFIG_DIR="/mnt/e/基础设施/离散时空认知物理基础研究/太虚元道/多智能体编排/agentscope_orchestrator/config"
echo "== Linux 装完即自证（0.3.1） =="
"$PY" -m agentscope_orchestrator.maturity --repo "/mnt/e/基础设施/_ab_zhcompiler" 2>&1 | grep -E "^  [✓✗?]|合计|证据：语义|证据：本机" | head -n 18