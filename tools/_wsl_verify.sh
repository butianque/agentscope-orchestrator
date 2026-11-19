set -u
VENV="$HOME/.orch-venv2"
PY="$VENV/bin/python"
CFG="/mnt/e/基础设施/离散时空认知物理基础研究/太虚元道/多智能体编排/agentscope_orchestrator/config"
export ORCH_CONFIG_DIR="$CFG"
. /etc/os-release; echo "== $PRETTY_NAME · $($PY -V 2>&1) =="
echo "== 1) 自检 =="
"$PY" -m agentscope_orchestrator.taixu_mcp --selftest 2>&1 | tail -n 2
echo "== 2) 真跑任务 =="
"$PY" - <<'PY'
import asyncio, json, os, tempfile
os.environ.setdefault("TAIXU_DB", os.path.join(tempfile.mkdtemp(prefix="linux_orch_"), "t.db"))
from agentscope_orchestrator import taixu_mcp as m
async def main():
    r = json.loads(await m.orch_run(repo=tempfile.mkdtemp(prefix="linux_repo_"),
                                   cmd=["python3", "-c", "print('linux ok')"], wait=True, timeout_s=120))
    print("  分类=", r.get("分类"), "| 门过=", [g.get("通过") for g in (r.get("门") or [])],
          "| 审计=", (r.get("审计官") or {}).get("结论"), "| 载荷=", (r.get("成本") or {}).get("载荷字节"))
asyncio.run(main())
PY
echo "== 3) guide 自举 =="
"$PY" - <<'PY'
import asyncio
from agentscope_orchestrator import taixu_mcp as m
async def main():
    文 = await m.taixu_guide()
    print("  字节=", len(文.encode()), "| 含 instances_mode=", "instances_mode" in 文, "| 含 9 类分类=", "任务书驳回" in 文)
asyncio.run(main())
PY
echo "== 4) 完备性记分卡 =="
"$PY" -m agentscope_orchestrator.maturity --repo "$(mktemp -d)" 2>&1 | tail -n 4