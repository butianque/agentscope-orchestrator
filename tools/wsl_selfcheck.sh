set -u
SRC="/mnt/e/基础设施/离散时空认知物理基础研究/太虚元道/多智能体编排/agentscope_orchestrator/src"
CFG="/mnt/e/基础设施/离散时空认知物理基础研究/太虚元道/多智能体编排/agentscope_orchestrator/config"
VENV="$HOME/.orch-venv"
PY="$VENV/bin/python"
echo "== 平台 =="; . /etc/os-release; echo "  $PRETTY_NAME · $(uname -r) · $($PY -V 2>&1)"
echo "== 1) 工具面自检 =="
cd "$SRC" || exit 5
ORCH_CONFIG_DIR="$CFG" PYTHONPATH="$SRC" "$PY" -m agentscope_orchestrator.taixu_mcp --selftest 2>&1 | tail -n 2
echo "== 2) 真跑一次任务 =="
ORCH_CONFIG_DIR="$CFG" PYTHONPATH="$SRC" "$PY" - <<'PY'
import asyncio, json, os, tempfile
os.environ.setdefault("TAIXU_DB", os.path.join(tempfile.mkdtemp(prefix="wsl_orch_"), "wsl.db"))
from agentscope_orchestrator import taixu_mcp as m
async def main():
    r = json.loads(await m.orch_run(repo=tempfile.mkdtemp(prefix="wsl_repo_"),
                                   cmd=["python3", "-c", "print('linux ok')"],
                                   wait=True, timeout_s=120))
    print("  fenlei=", r.get("分类"), "| gate_ok=", [g.get("通过") for g in (r.get("门") or [])],
          "| auditor=", (r.get("审计官") or {}).get("结论"),
          "| payload=", (r.get("成本") or {}).get("载荷字节"))
asyncio.run(main())
PY
echo "== 3) guide 工具（Linux 侧单次自举） =="
ORCH_CONFIG_DIR="$CFG" PYTHONPATH="$SRC" "$PY" - <<'PY'
import asyncio
from agentscope_orchestrator import taixu_mcp as m
async def main():
    文 = await m.taixu_guide()
    print("  guide bytes=", len(文.encode()), "| has_instances_mode=", "instances_mode" in 文)
asyncio.run(main())
PY
echo "== 4) 跨语言记忆（TS+Py） =="
ORCH_CONFIG_DIR="$CFG" PYTHONPATH="$SRC" "$PY" - <<'PY'
import tempfile
from pathlib import Path
from agentscope_orchestrator import mem_symbols as ms
根 = Path(tempfile.mkdtemp(prefix="wsl_xlang_"))
(根/"a.ts").write_text("export function f(x: number) { return x }\nexport class C {}\n", encoding="utf-8")
(根/"b.py").write_text("def g(y):\n    return y\n", encoding="utf-8")
表 = ms.符号表(); 表.同步(根, 后缀=(".py", ".ts"))
print("  xlang f=", bool(表.查("f")), " g=", bool(表.查("g")), " scale=", 表.规模())
PY