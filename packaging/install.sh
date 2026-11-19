#!/usr/bin/env bash
# 多智能体编排器 · Linux / macOS 一键安装
#
#   bash install.sh
#
# ① 找 Python ≥3.12  ② 建 .venv  ③ 从 wheels/ 离线装  ④ 跑 orch-doctor  ⑤ 打印常用命令
set -euo pipefail
cd "$(dirname "$0")"

echo "═══ 多智能体编排器 安装 ═══"

PY=""
for c in python3.13 python3.12 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    v="$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0.0)"
    if [ "$(printf '%s\n3.12\n' "$v" | sort -V | head -1)" = "3.12" ]; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "✗ 找不到 Python ≥3.12"; exit 1
fi
echo "✓ Python: $PY ($($PY -c 'import sys;print(sys.version.split()[0])'))"

if [ ! -d .venv ]; then
  echo "· 建虚拟环境 .venv"
  "$PY" -m venv .venv
else
  echo "· 复用已有 .venv"
fi
VPY=".venv/bin/python"
[ -x "$VPY" ] || { echo "✗ venv 建失败"; exit 1; }

# ★ 只挑编排器自己的轮子：依赖轮子也在 wheels/ 里（如 pydantic_core-2.50.4 版本号更高），
#   按名字 sort|tail -1 会挑中依赖包 —— 与 install.ps1 同一条坑（2026-11-19 实测）。
WHL="$(ls -1 wheels/agentscope_orchestrator-*.whl 2>/dev/null | sort | tail -1 || true)"
[ -n "$WHL" ] || { echo "✗ wheels/ 里没有 agentscope_orchestrator-*.whl"; exit 1; }
echo "· 安装 $(basename "$WHL")（wheels/ 共 $(ls -1 wheels/*.whl 2>/dev/null | wc -l) 个）"
# ★ 不用 pip 缓存：有机器把 cache-dir 配在写不进去/已断开的盘上，pip 会**静默卡死**
#   （2026-11-19 本机实测 pip 升级卡 >10 分钟；加 --no-cache-dir 后 3 秒完）。
"$VPY" -m pip install --no-cache-dir --disable-pip-version-check --upgrade pip -q || true
# ★ 依赖只从本地 wheels/ 解析 ⇒ **真离线装**。旧写法缺依赖时会装出 doctor=64 的坏装
#   （install.sh 原本没写 --no-deps，是靠联网补依赖；离线机器上就装坏了）。
"$VPY" -m pip install --no-cache-dir --disable-pip-version-check --no-index --find-links wheels --force-reinstall "$WHL"

echo
set +e
"$VPY" -m agentscope_orchestrator.doctor
DOC=$?
set -e

echo
echo "═══ 装完了 ═══"
echo "  激活：      source .venv/bin/activate"
echo "  自检：      .venv/bin/orch-doctor"
echo "  跑目标：    .venv/bin/orch-goal --goal \"<你的目标>\" --max-work 4"
echo "  当插件用：  .venv/bin/orch-mcp"
echo "  上手文档：  QUICKSTART.md"
[ "$DOC" -eq 0 ] || echo "注意：doctor 报了硬阻塞（见上），先解决再跑任务。"
exit "$DOC"
