set -u
export ORCH_PROFILE=/mnt/e/基础设施/离散时空认知物理基础研究/太虚元道/orchestrator.profile.yml
cd /mnt/e/基础设施/离散时空认知物理基础研究/太虚元道/多智能体编排/agentscope_orchestrator/src
$HOME/.orch-venv2/bin/python -m agentscope_orchestrator.maturity 2>&1 | tail -n 3
