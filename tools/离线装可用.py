# -*- coding: utf-8 -*-
"""判据入口（#49）：**发货承诺的"离线装"到底能不能装出可用的东西**。

为什么单列一条：`install.ps1` 承诺「从 wheels/ 里离线装（不联网也能装）」，而 2026-11-19
实测发现 `wheels/` 里只有编排器自己的轮子 ⇒ 干净机器上"离线装"装出 `doctor` 退出 64（缺
pyyaml/httpx/mcp）。这条判据把那个承诺变成机器可判的五件事：

  ① 全新 venv（工作区内，可写）
  ② `pip install --no-index --find-links wheels/` ⇒ rc=0（**完全离线**，只认本地轮子）
  ③ 11 个 console 入口都生成
  ④ 关键模块与依赖都导入得起来（yaml/httpx/mcp + 编排器五个入口模块）
  ⑤ MCP 协议真握手 ≥ 40 个工具

★ 边界：这验的是**Windows cp312** 的离线装（`packaging/deps_wheels/` 按目标平台取轮子）；
  换平台要换依赖轮子集。Linux 的 install.sh 也是同一条 `--no-index --find-links` 逻辑。

退出码：0 = 全过；1 = 有项不过；3 = 未验（交付包不在）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

包根 = Path(__file__).resolve().parent.parent


def _最新轮(包根: Path) -> tuple[Path | None, Path | None]:
    包们 = sorted((包根 / "dist").glob("orchestrator-*"),
                 key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split("-")[-1].split(".")])
    for 包 in reversed(包们):
        轮们 = sorted((包 / "wheels").glob("agentscope_orchestrator-*.whl"))
        if 轮们:
            return 包, 轮们[-1]
    return None, None


def main() -> int:
    包, 轮 = _最新轮(包根)
    if not 轮:
        print("   ③ 未验：dist/ 里没有交付包（先跑 tools/make_dist.py）")
        return 3
    轮们 = sorted((包 / "wheels").glob("*.whl"))
    依赖数 = len([w for w in 轮们 if not w.name.startswith("agentscope_orchestrator-")])
    print(f"   交付包 = {包.name} · wheel = {轮.name} · wheels/ 共 {len(轮们)} 个（依赖 {依赖数}）")

    根 = 包根 / "work" / "_离线装验"
    shutil.rmtree(根, ignore_errors=True)
    根.mkdir(parents=True)
    vdir = 根 / "venv"
    venv.create(vdir, with_pip=True)
    vpy = vdir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")

    环 = dict(os.environ)
    环.pop("PIP_CACHE_DIR", None)          # 坏缓存目录会让 pip 静默卡死（2026-11-19 实测）
    环["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    t0 = time.time()
    r = subprocess.run(
        [str(vpy), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
         "--no-index", "--find-links", str(包 / "wheels"), "--force-reinstall", str(轮)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=环, timeout=900)
    耗时 = round(time.time() - t0, 1)
    if r.returncode != 0:
        print(f"   ✗ 离线装失败 rc={r.returncode}（{耗时}s）")
        print("     " + ((r.stderr or r.stdout or "")[-800:]).replace("\n", "\n     "))
        return 1
    print(f"   ① 离线装 rc=0（{耗时}s，--no-index：只认本地轮子）")

    脚本 = vdir / ("Scripts" if os.name == "nt" else "bin")
    命令 = sorted(p.stem for p in 脚本.iterdir()
                  if p.stem.startswith("orch-") and p.suffix in (".exe", ".cmd", ""))
    命令 = sorted(set(命令))
    print(f"   ② 装出来的入口 {len(命令)} 个：{命令}")
    if len(命令) < 11:
        print(f"   ✗ 入口少于 11 个")
        return 1

    导入 = subprocess.run(
        [str(vpy), "-c", "import yaml, httpx, mcp\n"
                          "import agentscope_orchestrator.taixu_mcp\n"
                          "import agentscope_orchestrator.unattended\n"
                          "import agentscope_orchestrator.selfcheck\n"
                          "import agentscope_orchestrator.doctor\n"
                          "print('IMPORT-OK')"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=环, timeout=300)
    if "IMPORT-OK" not in (导入.stdout or ""):
        print(f"   ✗ 依赖/模块导入失败：{(导入.stderr or 导入.stdout or '')[-600:]}")
        return 1
    print("   ③ yaml/httpx/mcp + 五个入口模块导入 OK")

    sys.path.insert(0, str(包根 / "tools"))
    import verify_dist as V
    try:
        工具们 = V._握手(vpy, 期望至少=40)
    except Exception as e:                      # noqa: BLE001
        print(f"   ✗ MCP 握手失败：{type(e).__name__}: {e}")
        return 1
    print(f"   ④ MCP 协议真握手 {len(工具们)} 个工具")

    判定 = "OK" if len(命令) >= 11 and len(工具们) >= 40 else "不达标"
    print(f"   判定={判定}")
    (包根 / "work" / "离线装.json").write_text(json.dumps(
        {"包": 包.name, "wheel": 轮.name, "依赖轮子": 依赖数, "耗时_s": 耗时,
         "入口": 命令, "工具数": len(工具们), "判定": 判定},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if 判定 == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
