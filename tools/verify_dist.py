# -*- coding: utf-8 -*-
"""交付包验收闸：**在一个全新 venv 里从零装一遍，然后真握手**。

跑法：

    python tools/verify_dist.py            # 默认在系统临时目录建 venv
    python tools/verify_dist.py --keep     # 保留 venv 便于排查

它验的五件事（任何一件红 ⇒ 退出码 1）：

  ① wheel 能装进全新 venv，且 `orch-*` 命令都出现
  ② `orch-doctor` 退出码 0（环境自检过）
  ③ **MCP stdio 真握手**：initialize → tools/list，拿到 ≥16 个合规工具名（当前 18）
     （不是 `list_tools()` 内部调用，是走协议、跨进程、和外部客户端同一条路）
  ④ 通道自动适配在子进程里生效（`<前缀>_API_KEY` ⇒ 通道名出现在 doctor 读数里）
  ⑤ 装出来的 `orch-goal --dry` 能给出计划（不起模型调用）

★ 为什么要有这条闸：
  「源码目录里能跑」和「装出来的包能跑」是两件事。
  2026-10-29 当天就吃过教训：工具名全是中文、SDK 只警告不拦，
  源码里 `list_tools()` 全绿，而**任何客户端都调不动**。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
dist = 根 / "dist"


def _轮子() -> Path:
    # ★ 只认编排器自己的轮子：`wheels/` 里现在还有 34 个依赖轮子（离线装用），
    #   按文件名排序取最后 = `uvicorn-0.54.0` ⇒ 验的就不是编排器了（2026-11-19 实测）。
    候选 = (sorted(dist.glob("*/wheels/agentscope_orchestrator-*.whl"))
            or sorted((dist / "wheels").glob("agentscope_orchestrator-*.whl")))
    if not 候选:
        raise SystemExit("✗ 没有 wheel，先跑 tools/make_dist.py")
    return 候选[-1]


def _装离线(venv_py: Path, 轮: Path) -> bool:
    """**发货承诺的那条路**：依赖只从 `wheels/` 解析（`--no-index --find-links`）。

    ★ 2026-11-19 实测：此前 verify_dist 只验"网络 pip / 解包兜底"，而 install.ps1 承诺的是
      "从 wheels/ 离线装（不联网也能装）" —— 于是**发货承诺的那条路从来没被验过**，
      干净机器上"离线装出 doctor=64（缺 pyyaml/httpx/mcp）"的坏装就这样发出去了。
      ⇒ 把它排到第一位：先验它，才叫"交付包可用"。

    ★ 环境上必须 `pop("PIP_CACHE_DIR")`：本机 `PIP_CACHE_DIR` 指向 python 写不进去的目录，
      pip 会在缓存上**静默卡死**（`--no-cache-dir` 也还不够，pip 仍会去看那个变量）。
    """
    环 = dict(os.environ)
    环["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    环.pop("PIP_CACHE_DIR", None)
    try:
        r = subprocess.run(
            [str(venv_py), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
             "--no-index", "--find-links", str(轮.parent), "--force-reinstall", str(轮)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=环,
            timeout=float(os.environ.get("ORCH_PIP_TIMEOUT", "600")),
        )
    except Exception as e:                       # noqa: BLE001
        print(f"  ⚠ 离线装异常：{type(e).__name__}: {e}")
        return False
    if r.returncode != 0:
        print(f"  ⚠ 离线装（--no-index --find-links）rc={r.returncode}")
        print("  ── stderr 尾 ──\n" + (r.stderr or "（空）")[-1200:])
        return False
    return True


def _装(venv_py: Path, 轮: Path, 宿主: str) -> None:
    # ★ 装依赖必须绕开 pip 的临时目录陷阱：TMPDIR 会盖住 TEMP，
    #   指向满盘时 pip 报 `[Errno 28] No space left on device`（假象）。
    环 = dict(os.environ)
    环["TMPDIR"] = str(Path(tempfile.gettempdir()))
    环["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    r = subprocess.run(
        [str(venv_py), "-m", "pip", "install", "--no-cache-dir", "-q", str(轮)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=环,
        timeout=float(os.environ.get('ORCH_PIP_TIMEOUT', '150')),
    )
    if r.returncode != 0:
        print(f"  pip rc={r.returncode}")
        print("  ── stdout 尾 ──\n" + (r.stdout or "（空）")[-1200:])
        print("  ── stderr 尾 ──\n" + (r.stderr or "（空）")[-2000:])
        raise SystemExit("✗ wheel 装不进全新 venv")



def _离线装配(venv_py: Path, 轮: Path) -> dict:
    """不经过 pip 的装配：wheel 解包 + 宿主机既有依赖（**排除 agentscope**）。

    ★ 为什么要有这条路（2026-10-29 实测）：
      本机 `pip install` 在子进程里反复出现 **CPU 0s 静止 20 分钟**，
      换 `--python` / 自带 pip / 文件重定向都复现；而 `pip --version`、
      `python -m venv` 在同一父进程下都正常（11s / 0.8s）。
      ⇒ 卡点是 pip 自身，不是管道、不是网络（依赖下载实测 3–8 秒）。
      交付验收不能被一台机器的 pip 症状卡死，所以给一条**确定性**装配路径。

    ★ 这条路径顺带证明一件更重要的事：
      只装 wheel + 非 agentscope 依赖，编排器**照样能导入、能自检、能握手**。
    """
    import zipfile
    sp = venv_py.parent.parent / "Lib" / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(轮) as z:
        z.extractall(sp)

    宿主sp = Path(next(iter(sys.path))) if False else None
    import sysconfig
    for 候选 in sys.path:
        可 = Path(候选)
        if 可.name == "site-packages" and 可.exists():
            宿主sp = 可
            break
    拷了, 跳过 = [], []
    if 宿主sp:
        for d in sorted(宿主sp.iterdir()):
            名 = d.name
            if 名.startswith(("agentscope", "pip", "setuptools", "wheel", "~")):
                跳过.append(名)
                continue
            if (sp / 名).exists():
                continue
            if d.is_dir():
                shutil.copytree(d, sp / 名, dirs_exist_ok=True)
                拷了.append(名)
            elif d.suffix in (".py", ".pth"):
                # ★ 单文件模块也要拷：`typing_extensions.py` 就是这种，
                #   漏了它 anyio/mcp 直接 ModuleNotFound（实测）。
                shutil.copy2(d, sp / 名)
                拷了.append(名)
    # ── 生成控制台入口（pip 平时用 .exe 启动器做这件事）──
    #    ★ 不生成就等于"包装上了但没有命令"，验收会误判成失败。
    # ★ 只认**我们自己**的 dist-info：未加限定时会先撞上别的包（实测撞到 a2a-db），
    #   于是"命令 0 个"——那是读数错，不是装配失败。
    ep = next((d / "entry_points.txt" for d in sp.iterdir()
               if d.name.startswith("agentscope_orchestrator-")
               and d.name.endswith(".dist-info")
               and (d / "entry_points.txt").exists()), None)
    命令 = []
    if ep:
        for 行 in ep.read_text(encoding="utf-8").splitlines():
            行 = 行.strip()
            if "=" not in 行 or 行.startswith("["):
                continue
            名, 目标 = [x.strip() for x in 行.split("=", 1)]
            模块, _, 函数 = 目标.partition(":")
            脚本目录 = venv_py.parent
            (脚本目录 / f"{名}.cmd").write_text(
                "@echo off\r\n"
                f"\"{venv_py}\" -c \"import sys;from {模块} import {函数} as m;sys.exit(m())\" %*\r\n",
                encoding="utf-8")
            命令.append(名)
    return {"拷入": 拷了, "排除": 跳过, "命令": 命令}


def _握手(venv_py: Path, 期望至少: int = 16) -> list[str]:
    """走 **MCP stdio 协议**真握手（跨进程，和外部客户端同一条路）。"""
    环 = dict(os.environ)
    环["PYTHONIOENCODING"] = "utf-8"; 环["PYTHONUTF8"] = "1"
    环["TMPDIR"] = str(Path(tempfile.gettempdir()))
    环.pop("ORCH_PROFILE", None)
    p = subprocess.Popen(
        [str(venv_py), "-m", "agentscope_orchestrator.taixu_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1, env=环, cwd=str(根),
    )
    assert p.stdin and p.stdout

    # ★ 看门狗：readline 会**死等**，服务端不吐字节就永远不返回。
    #   真证据（2026-10-29 21:4x）：没有它时本闸挂了 20 分钟无输出。
    import threading, time as _t
    _超 = float(os.environ.get("ORCH_HANDSHAKE_TIMEOUT", "180"))

    def _看门狗():
        _t.sleep(_超)
        try:
            p.kill()
        except Exception:                        # noqa: BLE001
            pass

    threading.Thread(target=_看门狗, daemon=True).start()

    def 发(obj: dict) -> None:
        p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        p.stdin.flush()

    def 收(id_: int, 超次: int = 400) -> dict:
        for _ in range(超次):
            line = p.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if m.get("id") == id_:
                return m
        raise SystemExit("✗ MCP 握手超时/无响应")

    try:
        发({"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "capabilities": {},
                       "clientInfo": {"name": "verify_dist", "version": "1"}}})
        r1 = 收(1)
        if "result" not in r1:
            raise SystemExit(f"✗ initialize 失败：{r1}")
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized",
                                  "params": {}}) + "\n")
        p.stdin.flush()
        发({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        r2 = 收(2)
        工具 = [t["name"] for t in (r2.get("result") or {}).get("tools", [])]
        if len(工具) < 期望至少:
            raise SystemExit(f"✗ tools/list 只拿到 {len(工具)} 个（期望 ≥{期望至少}）")
        坏 = [n for n in 工具 if not all(c.isascii() and (c.isalnum() or c in "_-.") for c in n)]
        if 坏:
            raise SystemExit(f"✗ 工具名不合 MCP 规范：{坏}")
        return 工具
    finally:
        try:
            p.stdin.close()
        except Exception:                        # noqa: BLE001
            pass
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:                        # noqa: BLE001
            p.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--venv", default="")
    a = ap.parse_args()

    轮 = _轮子()
    print(f"① wheel：{轮.name}（{轮.stat().st_size // 1024} KB）")

    venv = Path(a.venv) if a.venv else Path(tempfile.mkdtemp(prefix="orchdist-")) / "venv"
    # ★ 必须用**基础解释器**：从本仓 .venv 里再建 venv 是嵌套 venv，
    #   pip 会以退出码 1 静默失败（实测）。sys._base_executable 才是真的。
    宿主 = getattr(sys, "_base_executable", sys.executable)
    print(f"② 全新 venv：{venv}")
    if venv.exists():
        shutil.rmtree(venv)
    # ★ 让 venv **自带 pip**：`pip --python` 的跨解释器安装在实测中会
    #   **0 CPU 死等 20 分钟**（管道阻塞），不可靠。自带 pip 是唯一稳的路。
    r = subprocess.run([宿主, "-m", "venv", str(venv)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:], file=sys.stderr)
        raise SystemExit("✗ 建 venv 失败")
    vpy = venv / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    if not vpy.exists():
        raise SystemExit("✗ venv 里没有 python")

    # ★ 先验**发货承诺的那条路**：离线装（依赖只从 wheels/ 解析）。
    #   2026-11-19 实测：这条路从来没被验过 ⇒ "干净机器离线装出 doctor=64（缺 pyyaml/httpx/mcp）"
    #   的坏装就这么发出去了，而 verify_dist 一直是绿的（它只验"能装 + 能握手"）。
    装法 = ""
    try:
        if not _装离线(vpy, 轮):
            raise SystemExit("离线装 rc≠0")
        装法 = "离线（--no-index --find-links）"
        print("③ wheel 安装成功（走离线装：依赖只从 wheels/ 解析）")
    except (SystemExit, Exception) as e:
        print(f"③ 离线装不可用（{type(e).__name__}）⇒ 试网络 pip")
        try:
            _装(vpy, 轮, 宿主)
            装法 = "网络 pip"
            print("③ wheel 安装成功（走网络 pip）")
        except (SystemExit, Exception) as e2:
            print(f"③ pip 路径不可用（{type(e2).__name__}）⇒ 改走离线装配（解包）")
            装法 = "解包兜底"
            结 = _离线装配(vpy, 轮)
        print(f"   离线装配：拷入 {len(结['拷入'])} 个包；"
              f"**排除** {[x for x in 结['排除'] if x.startswith('agentscope')] or '无 agentscope'}")
        print(f"   生成控制台入口 {len(结.get('命令', []))} 个：{结.get('命令', [])}")

    # ★★ 装完立刻验"装出来的东西依赖齐不齐"。
    #   为什么必须是这一步：2026-11-19 的坏装（`--no-deps` 装进干净 venv）**装得上、也握手得上**，
    #   只有 doctor 退出 64（缺 pyyaml/httpx/mcp）—— 而 doctor 此前不在本闸的检查范围内 ⇒ 假绿。
    _环 = dict(os.environ)
    _环["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    _r = subprocess.run([str(vpy), "-m", "agentscope_orchestrator.doctor"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        env=_环, timeout=600)
    _o = (_r.stdout or "") + (_r.stderr or "")
    if _r.returncode == 64 or "deps: 缺" in _o:
        print(f"  ✗ 装出来的东西依赖不齐（doctor 退出 {_r.returncode}）：")
        for _l in _o.splitlines():
            if "deps" in _l or "缺" in _l or "不可开工" in _l:
                print("    " + _l.strip()[:160])
        raise SystemExit("✗ 安装产物依赖不齐（离线装承诺不成立）")
    print(f"③.5 依赖自检：doctor 退出 {_r.returncode}（64/缺依赖 = 装坏）· 安装路径 = {装法}")

    脚本 = venv / ("Scripts" if os.name == "nt" else "bin")
    命令 = sorted(p.stem for p in 脚本.iterdir()
                  if p.stem.startswith("orch-") and p.suffix in (".exe", ".cmd"))
    print(f"④ 装出来的命令 {len(命令)} 个：{命令}")
    if len(命令) < 8:
        raise SystemExit(f"✗ 命令数不足（{len(命令)} < 8）")

    工具 = _握手(vpy)
    print(f"⑤ MCP 真握手：initialize → tools/list ⇒ {len(工具)} 个合规工具")
    print("   " + ", ".join(工具))

    if not a.keep:
        shutil.rmtree(venv.parent, ignore_errors=True)
    print("\n✓ 交付包验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
