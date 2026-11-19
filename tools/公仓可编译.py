# -*- coding: utf-8 -*-
"""公仓可编译可导入（判据 #50）—— **发布出去的东西**必须真能装上、真能 import。

## 为什么要有这条（2026-11-19 真事故）
公共仓 `butianque/agentscope-orchestrator` 已推到 0.13.20，本地 43 条判据全绿，
**但公仓 HEAD 里有一份未完成的 WIP 编辑**（`bootstrap._扫后缀分布` 第 574 行未闭合、
字符串里还嵌了双引号）⇒ 从公仓 clone 下来 `import agentscope_orchestrator.bootstrap`
**直接 SyntaxError**。43 条判据一条都没抓到：它们测的是**本机工作树**，
不是**发布出去的那份字节**。

⇒ 这条判据只回答一个问题：**把公仓 clone 下来，能不能编译、能不能 import。**

判定（四条，全过才 OK）：
① `git clone` 成功；② `compileall src/` 无 Error compiling；
③ 逐个 import 全部模块，失败 **0**；④ `__version__` 与 `pyproject.toml` 一致。

## 诚实边界
· 它测的是**公仓**（`ORCH_PUB`，默认 `E:\\基础设施\\orch-share\\agentscope-orchestrator`）；
  公仓不存在 / 无 git ⇒ **退出码 3（未验）**，不假装通过（口径 375）。
· 网络不需要：本地克隆；公仓目录不存在时才算未验。
· 它不测"代码对不对"，只测"发布件能不能用"——语义仍由其余判据负责。
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

包根 = pathlib.Path(__file__).resolve().parents[1]
公仓 = pathlib.Path(os.environ.get("ORCH_PUB", r"E:\基础设施\orch-share\agentscope-orchestrator"))
输出 = 包根 / "work" / "公仓可编译.json"


def _跑(argv: list[str], *, cwd: pathlib.Path | None = None, env: dict | None = None,
        timeout: int = 600) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    # ★ 本机 `PYTHONPYCACHEPREFIX` 指向一个**不可写**的缓存目录 ⇒ compileall 会报
    #   PermissionError（看着像坏码，其实是环境）；判据里必须摘掉，否则永远是假红。
    e.pop("PYTHONPYCACHEPREFIX", None)
    e.pop("PYTHONDONTWRITEBYTECODE", None)
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, env=e)


def _版本(p: pathlib.Path) -> str:
    for 行 in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = 行.strip()
        if s.startswith("version"):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    if not (公仓 / ".git").exists():
        print(f"○ 未验：公仓不在 `{公仓}`（设 ORCH_PUB 指向它）—— 环境没就绪，不是产品缺陷")
        return 3
    临时 = pathlib.Path(tempfile.mkdtemp(prefix="orch-公仓-"))
    克隆 = 临时 / "repo"
    try:
        进 = _跑(["git", "-c", "http.version=HTTP/1.1", "clone", "-q", str(公仓), str(克隆)])
        assert 进.returncode == 0, f"git clone 失败：{进.stderr[:300]!r}"
        print(f"   ① clone OK → {克隆}")

        源 = 克隆 / "src"
        assert (源 / "agentscope_orchestrator").is_dir(), "克隆件里没有 src/agentscope_orchestrator"
        编 = _跑([sys.executable, "-m", "compileall", "-q", str(源)])
        坏行 = [x for x in 编.stdout.splitlines() + 编.stderr.splitlines() if "Error compiling" in x]
        assert 编.returncode == 0, f"compileall 退出 {编.returncode}：{(编.stderr or 编.stdout)[:300]!r}"
        assert not 坏行, f"compileall 报 {len(坏行)} 个文件编不过：{坏行[:3]}"
        print("   ② compileall OK（0 个 Error compiling）")

        # ③ 逐个 import（独立进程，避免本进程已缓存模块导致假绿）
        探针 = (
            "import sys, pathlib, importlib, logging\n"
            "sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
            "logging.disable(logging.CRITICAL)\n"
            f"根 = pathlib.Path(r'{源}')\n"
            "sys.path.insert(0, str(根))\n"
            "import agentscope_orchestrator as 包\n"
            "名字 = [p.stem for p in (根/'agentscope_orchestrator').glob('*.py') if p.stem != '__init__']\n"
            "坏 = []\n"
            "for n in 名字:\n"
            "    try: importlib.import_module('agentscope_orchestrator.' + n)\n"
            "    except Exception as e: 坏.append((n, type(e).__name__, str(e)[:120]))\n"
            "print('版本=' + 包.__version__)\n"
            "print('模块数=' + str(len(名字)))\n"
            "print('失败=' + str(len(坏)))\n"
            "for n, t, m in 坏[:10]: print('坏:' + n + '|' + t + '|' + m)\n"
        )
        导 = _跑([sys.executable, "-B", "-c", 探针], env={"PYTHONPATH": str(源)})
        行 = 导.stdout.splitlines()
        assert 导.returncode == 0, f"import 探针退出 {导.returncode}：{(导.stderr or 导.stdout)[:300]!r}"
        版本 = next((x.split("=", 1)[1] for x in 行 if x.startswith("版本=")), "")
        模块数 = int(next((x.split("=", 1)[1] for x in 行 if x.startswith("模块数=")), "0"))
        失败 = int(next((x.split("=", 1)[1] for x in 行 if x.startswith("失败=")), "0"))
        assert 模块数 > 0, "一个模块都没 import 到（路径不对）"
        assert 失败 == 0, f"{失败} 个模块 import 失败：{[x for x in 行 if x.startswith('坏:')][:5]}"
        print(f"   ③ import OK（模块数 = {模块数} · 导入失败 = 0）")

        # ④ 版本一致（clone 件的 __version__ 必须 == 它的 pyproject）
        期 = _版本(克隆 / "pyproject.toml")
        assert 期 and 版本 == 期, f"版本不一致：__version__={版本!r} · pyproject={期!r}"
        print(f"   ④ 版本一致 = {版本}")

        输出.parent.mkdir(parents=True, exist_ok=True)
        输出.write_text(json.dumps({
            "公仓": str(公仓), "提交": _跑(["git", "-C", str(克隆), "rev-parse", "HEAD"]).stdout.strip(),
            "版本": 版本, "模块数": 模块数, "导入失败": 失败,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("   判定=OK")
        return 0
    except AssertionError as e:
        print(f"   判定=回归 · {e}")
        return 1
    finally:
        shutil.rmtree(临时, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())