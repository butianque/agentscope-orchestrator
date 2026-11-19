# -*- coding: utf-8 -*-
"""部署一致性（#48）：**部署目录**必须与仓库同版本、同内容 —— 不许悄悄落后几版。

## 为什么必须有这条（本轮实测到的真实状态）

`E:\\基础设施\\orch-deploy` 是本机的**独立部署目录**（`DEPLOY.md` 定的），DSH 智能体把它当
MCP 工具调用。2026-11-19 实测：它里面装的是 **0.13.11**（11-03），而仓库已到 **0.13.17** ——
**差 6 个版本**，中间修的所有真缺陷（抽取器不认 `«»`、缓存键缺版本、打包判据写死 …）
一个都不在部署里。而仓库这边 **39 条验收判据全绿**。

⇒ 这不是"少做了一步"，这是**判据的覆盖盲区**：判据只测源码树和构建产物，
**没人测"部署目录里到底是哪一版"**。用户问"能直接部署使用吗"，此前**没有任何机器读数能回答**。

## 判据（全机判，不需要人去点点点）

1. 部署根存在，且含 `VERSION.txt` / `wheels/*.whl` / `SHA256SUMS` / `.venv`。
2. **版本三方一致**：`VERSION.txt` == 仓库 `pyproject.toml` == `SHA256SUMS` 里那只 wheel 的版本。
   （三方不一致 ⇒ 你说不出装的是哪一版，这正是 `__init__.py` 开头那段教训。）
3. **部署 venv 里装的包，逐文件 sha256 == 部署包里那只 wheel 里的内容**。
   不一致 ⇒ 装的和发的不是同一份（"重打包后忘了重装"就是这种）。
4. **SHA256SUMS 与文件**逐个校验一致（发布物自相矛盾 ⇒ 发出去的东西不可信）。
5. 部署包里那只 wheel 的 `.py` 内容 == 仓库 `src/**/*.py`（现算比对）⇒ 部署包不是旧代码。

## 三态

- 通过 ⇒ `判定=OK`，退出码 0
- 漂移/自相矛盾 ⇒ `判定=不达标`，退出码 1，逐条说哪不一致、差在哪
- 部署根不存在（这台机器没部署）⇒ `判定=未验`，退出码 3（口径 545：环境问题记未验，不记红）

## 诚实边界

· 只比 `.py`（库的实际可执行内容）+ 部署包元数据；`tools\\` 不随 wheel 发布。
· 不验"能不能真跑起来"——那是 `tools\\verify_dist.py`（干净 venv 真握手）的职责，两条判据互补。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tomllib
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_包根 = pathlib.Path(__file__).resolve().parents[1]
_默认部署根 = pathlib.Path(r"E:\基础设施\orch-deploy")


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _版本(仓库根: pathlib.Path) -> str:
    with (仓库根 / "pyproject.toml").open("rb") as f:
        return str(tomllib.load(f)["project"]["version"])


def main() -> int:
    部署 = pathlib.Path(os.environ.get("ORCH_DEPLOY_ROOT") or _默认部署根)
    print(f"部署根 = {部署}")
    if not 部署.is_dir():
        print(f"⚠ 未验：部署根不存在（{部署}）—— 这台机器没部署，不是产品红（口径 545）")
        print("判定=未验")
        return 3

    版本 = _版本(_包根)
    print(f"仓库版本 = {版本}")

    缺 = [n for n in ("VERSION.txt", "SHA256SUMS") if not (部署 / n).is_file()]
    if not (部署 / ".venv").is_dir():
        缺.append(".venv")
    # ★ 只认编排器自己的轮子：部署根 wheels/ 里现在还有 34 个**依赖轮子**（离线装用），
    #   按文件名排序取最后 = `uvicorn-0.54.0` ⇒ 会把"装的是哪个版本"判成 uvicorn 的版本
    #   （2026-11-19 实测：#48 因此报"三方版本不一致 wheel=0.54.0 / 包里缺 111"）。
    wheels = sorted((部署 / "wheels").glob("agentscope_orchestrator-*.whl"))
    if not wheels:
        缺.append("wheels/agentscope_orchestrator-*.whl")
    if 缺:
        print(f"✗ 部署根不完整，缺：{缺}")
        print("判定=不达标")
        return 1

    # ── 判据 2：版本三方一致 ──
    装机版本 = (部署 / "VERSION.txt").read_text(encoding="utf-8").strip()
    wheel = wheels[-1]
    m = re.search(r"-(\d+\.\d+\.\d+)-", wheel.name)
    包版本 = m.group(1) if m else "?"
    坏: list[str] = []
    print(f"VERSION.txt = {装机版本} · wheel = {包版本}")
    if not (装机版本 == 包版本 == 版本):
        坏.append(f"版本三方不一致：VERSION.txt={装机版本} · wheel={包版本} · 仓库={版本}")

    # ── 判据 3/5：wheel 内容 vs 部署 venv 已装 vs 仓库源码 ──
    with zipfile.ZipFile(wheel) as z:
        包内 = {n: z.read(n) for n in z.namelist()
                if n.startswith("agentscope_orchestrator/") and n.endswith(".py")}
    print(f"wheel 内 .py = {len(包内)} 个")

    站 = 部署 / ".venv" / "Lib" / "site-packages" / "agentscope_orchestrator"
    装缺, 装差 = 0, 0
    for n, 字节 in 包内.items():
        f = 站 / n.split("agentscope_orchestrator/", 1)[1]
        if not f.is_file():
            装缺 += 1
            continue
        if _h(f.read_bytes()) != _h(字节):
            装差 += 1
    print(f"部署 venv 已装：缺 {装缺} · 内容不一致 {装差}")
    if 装缺 or 装差:
        坏.append(f"部署 venv 里装的和 wheel 不是同一份（缺 {装缺} · 不一致 {装差}）")

    源码 = {p.name: p.read_bytes() for p in (_包根 / "src" / "agentscope_orchestrator").glob("*.py")}
    包名集 = {n.split("agentscope_orchestrator/", 1)[1] for n in 包内}
    源缺 = sorted(set(源码) - 包名集)
    源差 = sorted(n for n in 包名集 & set(源码)
                  if _h(包内[f"agentscope_orchestrator/{n}"]) != _h(源码[n]))
    print(f"部署包 vs 仓库源码：源码 {len(源码)} · 包里缺 {len(源缺)} · 内容不同 {len(源差)}")
    if 源缺 or 源差:
        坏.append(f"部署包是旧代码（缺 {源缺[:5]} · 不同 {源差[:5]}）")

    # ── 判据 4：SHA256SUMS 与文件逐个一致 ──
    和文件 = 部署 / "SHA256SUMS"
    核 = 0
    环: list[str] = []
    for 行 in 和文件.read_text(encoding="utf-8").splitlines():
        if not 行.strip():
            continue
        记, _, 名 = 行.partition("  ")
        目 = 部署 / 名.strip()
        if not 目.is_file():
            环.append(f"缺文件 {名.strip()}")
            continue
        核 += 1
        if _h(目.read_bytes()) != 记.strip().lower():
            环.append(f"哈希不符 {名.strip()}")
    if wheel.name not in 和文件.read_text(encoding="utf-8"):
        环.append(f"SHA256SUMS 里没有这只 wheel（{wheel.name}）")
    print(f"SHA256SUMS 校验 {核} 条 · 不一致 {len(环)}")
    if 环:
        坏.append(f"发布物自相矛盾：{环[:4]}")

    if 坏:
        for x in 坏:
            print(f"✗ {x}")
        print("  修法：python tools\\make_dist.py → 把新 wheel 放进部署根 wheels/ → "
              "在部署根跑 install.ps1 → 更新 VERSION.txt 与 SHA256SUMS")
        print("判定=不达标")
        return 1
    print("判定=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())