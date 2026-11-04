"""装机自检（`python -m agentscope_orchestrator.selfcheck`，或控制台命令 `orch-check`）。

**为什么存在**：装完那个包的人（尤其是智能体自己）必须能在**一条命令**里知道
"这层到底能不能干活"，而不是去看文档、也不是去问作者。所以它检查的是**能力**，
不是"文件在不在"：

  ① 导入与版本           —— 装的是哪一版（别拿旧 wheel 当真）
  ② 依赖                 —— `mcp < 2.0.0`（钉住的唯一硬依赖）
  ③ 工具面               —— 24 个工具里，**关键入口**是否都在（orch_run/orch_fix/
                            orch_object/guide/orch_history/resume_task…）
  ④ 状态目录可写         —— 任务库要能落盘，否则一切都是空转
  ⑤ 判据引擎             —— 编译门 + **三态门**（证明 / 未知）现跑一次
  ⑥ 活体任务             —— 真起一个临时仓，调 `orch_run`，必须拿到 `达标`，
                            且回执里 `预算` 与 `留痕`（检查点）都在
  ⑦ 契约                 —— `guide` 能给出症状路由 / 历史查询 / orch_object

**边界（如实）**：这是**冒烟**，不是全量回归。它不证明语义正确（那要靠判据门），
也不调真模型（所以 `orch_fix` 的模型链只验"能导入、有预算参数"）。
状态目录默认走**临时目录**，不会污染你正在用的任务库。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_关键工具 = ("orch_run", "orch_fix", "orch_object", "guide", "orch_history",
             "resume_task", "list_tasks", "gate_power")

# 自检产生的临时目录 —— 结束一律删除（自检是客人，不许在你仓里留下家具）
_临时们: list[Path] = []


def _临时目录(前缀: str) -> Path:
    r"""按候选基目录**逐个试**拿一个可写临时目录；全部不可写才抛错。

    这里刻意**不用** `tempfile`：实测这台机器
      · `TEMP`（`E:\Temp`）与 `%LOCALAPPDATA%\Temp` 对子进程**拒绝访问**（WinError 5）；
      · 而 `tempfile.mkdtemp` 遇到 PermissionError 会在上千个随机名上**重试**
        （还夹着 `isdir` 探测）⇒ 表现为**几分钟无输出**，看着像挂死。
    交付物不许因为"环境怪"就挂/崩，只能**快速失败并说清降级到哪儿**：
    每个候选基目录只试 3 个名字，失败立刻换下一个，全失败抛错（上层给红 + 修法）。
    """
    基们: list[Path] = []
    if os.environ.get("ORCH_STATE_DIR"):
        基们.append(Path(os.environ["ORCH_STATE_DIR"]).parent)
    基们.append(Path.cwd())
    基们.append(Path.home())
    基们.append(Path(__file__).resolve().parent)   # 包所在目录（装在哪就在哪，必然存在）
    最后: Exception | None = None
    秒 = int(__import__("time").time() * 1000)
    for 基 in 基们:
        for 序 in range(3):
            候选 = 基 / f"{前缀}{os.getpid()}-{秒}-{序}"
            try:
                候选.mkdir()
                _临时们.append(候选)
                return 候选
            except Exception as e:          # noqa: BLE001
                最后 = e
    raise RuntimeError(f"没有任何可写的临时目录（最后一错：{最后}）")


def _记(结果: list, 名: str, 绿: bool, 证据: str = "", 修法: dict | None = None) -> None:
    结果.append({"检查": 名, "通过": bool(绿), "证据": 证据[:400],
                 **({"修法": 修法} if (not 绿 and 修法) else {})})


def run() -> dict:
    t0 = time.time()
    结果: list[dict] = []

    # ── ① 导入与版本 ──
    try:
        from importlib.metadata import version as _v
        装版 = _v("agentscope-orchestrator")
    except Exception:                       # noqa: BLE001
        装版 = ""
    版本 = "未取到"          # 抬头印**代码版**：metadata 可能残留旧 dist-info（实测报 0.1.0）
    try:
        import agentscope_orchestrator as 包
        代码版 = getattr(包, "__version__", "未知")
        版本 = 代码版
        位置 = Path(包.__file__).parent
        # ★ ① 的真问题不是"能不能导入"，而是**装的是不是你现在这版**。
        #   实测过：源码目录里跑，metadata 报 0.1.0（旧的 dist-info 残留），
        #   若只判"导入成功"就会绿 —— 那正是"拿旧 wheel 当真"的情形。
        装在哪 = any(位置.parent.glob("agentscope_orchestrator-*.dist-info"))
        if 装在哪 and 装版 and 装版 != 代码版:
            _记(结果, "① 导入与版本", False,
                f"**装的 wheel 是 {装版}，代码是 {代码版}** · {位置}",
                {"做": "pip install --force-reinstall --no-deps "
                       f"<最新的 agentscope_orchestrator-{代码版}-py3-none-any.whl>"})
        else:
            _记(结果, "① 导入与版本", True,
                f"代码版={代码版} · 装版={装版 or '未装（源码运行）'} · "
                f"位置={'安装（有 dist-info）' if 装在哪 else '源码树（无 dist-info）'} · {位置}")
    except Exception as e:                  # noqa: BLE001
        _记(结果, "① 导入与版本", False, f"{type(e).__name__}: {e}",
            {"做": "重装 wheel：pip install --force-reinstall <wheel>"})


    # ── ② 依赖 ──
    try:
        import mcp
        from importlib.metadata import version as _v
        mcp版 = _v("mcp")
        绿 = mcp版.split(".")[0] == "1"          # 钉的是 mcp<2.0.0
        _记(结果, "② 依赖 mcp<2.0.0", 绿, f"mcp={mcp版}",
            None if 绿 else {"做": "pip install 'mcp<2.0.0'"})
    except Exception as e:                  # noqa: BLE001
        _记(结果, "② 依赖 mcp<2.0.0", False, f"{type(e).__name__}: {e}",
            {"做": "pip install 'mcp<2.0.0'"})
        return _汇总(结果, t0, 版本)

    # 自检不污染生产任务库；这个临时目录同样登记，结束时删掉
    if not os.environ.get("ORCH_STATE_DIR"):
        临时 = _临时目录("orch-selfcheck-state-")
        os.environ["ORCH_STATE_DIR"] = str(临时)

    # ── ③ 工具面 ──
    try:
        from agentscope_orchestrator import taixu_mcp as M
        工具 = sorted(t.name for t in M.mcp._tool_manager.list_tools())
        缺 = [k for k in _关键工具 if not any(k in t for t in 工具)]
        _记(结果, "③ 工具面（关键入口）", not 缺,
            f"共 {len(工具)} 个；缺={缺 or '无'}",
            None if not 缺 else {"做": "版本不匹配：装最新 wheel", "缺": 缺})
    except Exception as e:                  # noqa: BLE001
        _记(结果, "③ 工具面（关键入口）", False, f"{type(e).__name__}: {e}",
            {"做": "设 ORCH_STATE_DIR 指向可写目录后重试"})
        return _汇总(结果, t0, 版本)

    # ── ④ 状态目录可写 ──
    try:
        状态 = Path(os.environ["ORCH_STATE_DIR"])
        状态.mkdir(parents=True, exist_ok=True)
        探 = 状态 / f".probe.{os.getpid()}"
        探.write_text("ok", encoding="utf-8")
        探.unlink()
        _记(结果, "④ 状态目录可写", True, str(状态))
    except Exception as e:                  # noqa: BLE001
        _记(结果, "④ 状态目录可写", False, f"{type(e).__name__}: {e}",
            {"做": "设 ORCH_STATE_DIR 到可写目录后再自检"})

    # ── ⑤ 判据引擎：编译门 + 三态门 ──
    try:
        from agentscope_orchestrator import gate_spec as gs
        仓 = _临时目录("orch-judge-")
        (仓 / "ok.py").write_text("print('ok')\n", encoding="utf-8")
        产 = 仓 / "_out.txt"
        产.write_text("x", encoding="utf-8")
        r1, _a1 = gs.evaluate(
            [{"name": "c", "type": "compile", "cmd": ["python", "ok.py"], "cwd": str(仓)}],
            产, 仓, str(仓), 仓)
        r2, a2 = gs.evaluate(
            [{"name": "t", "type": "oracle", "cmd": ["python", "-c", "print('proved')"]}],
            产, 仓, str(仓), 仓)
        r3, a3 = gs.evaluate(
            [{"name": "u", "type": "oracle",
              "cmd": ["python", "-c", "import sys; print('dunno'); sys.exit(1)"]}],
            产, 仓, str(仓), 仓)
        绿 = (r1.get("c") is True and r2.get("t") is True
              and r3.get("u") is False
              and str(a3[0].get("cause", "")).startswith("未知|"))
        _记(结果, "⑤ 判据引擎（编译门 + 三态门）", 绿,
            f"编译门={r1.get('c')} · 证明={a2[0].get('三态')} · 未知={a3[0].get('三态')}"
            f"（未知判红={r3.get('u') is False}）",
            None if 绿 else {"做": "门引擎异常：报 issue 附本行证据"})
    except Exception as e:                  # noqa: BLE001
        _记(结果, "⑤ 判据引擎（编译门 + 三态门）", False, f"{type(e).__name__}: {e}",
            {"做": "报 issue 附完整堆栈"})

    # ── ⑥ 活体任务（1 次往返，必须拿到「达标」+ 预算 + 检查点） ──
    try:
        仓2 = _临时目录("orch-live-")
        (仓2 / "pkg").mkdir()
        (仓2 / "pkg" / "mod.py").write_text("def f():\n    return 42\n",
                                            encoding="utf-8")
        # ★ 自检绝不许挂：外层硬上限（内层 120s + 30s 余量）
        # ★ 必须是**子进程**：`wait_for` 只放弃等待，任务线程会让进程退不掉（实测）。
        子代码 = (
            "import asyncio, json, sys\n"
            "from agentscope_orchestrator import taixu_mcp as M\n"
            "async def m():\n"
            "    return await M.orch_run(sys.argv[1], [sys.executable, '-c',\n"
            "        \"import sys; sys.path.insert(0,'.'); from pkg.mod import f;\"\n"
            "        \" assert f() == 42; print('ok')\"], timeout_s=120)\n"
            "print(json.dumps(json.loads(asyncio.run(m())), ensure_ascii=False))\n")
        子 = __import__("subprocess").run(
            [sys.executable, "-c", 子代码, str(仓2)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150,
            env={**os.environ,
                  # ★★★★★ 2026-11-03 根因修复：**孙进程的输出编码必须显式固定**。
                  #
                  #   真事故（本项在 `tools/ci.py` 里稳定红、手动跑稳定绿；我做了
                  #   6 轮二分全复现不出，最后靠"让判据自己吐证据"才定位）：
                  #     · `ci.py` 用 `Popen(stdout=<二进制文件>)` 起 selfcheck ⇒
                  #       本进程 stdout 不是终端；
                  #     · 此时 Windows 上 Python 的 stdout 编码**退回活动代码页(GBK)**，
                  #       除非 `PYTHONIOENCODING`/`PYTHONUTF8` 兜住；
                  #     · 而 `ci.py` **没设**这两个（我手动跑时 shell 里有 ⇒ 所以绿）；
                  #     · 孙进程于是用 GBK 输出那份 JSON，父进程却按 `encoding="utf-8"`
                  #       解码 ⇒ 中文键 `"分类"` 变成替换字符 ⇒ `回.get("分类")` 得 None
                  #       ⇒ 判据报 `分类=None`，**看起来像编排器坏了**。
                  #   一句话：**判据依赖调用方恰好设了编码环境变量** ——
                  #   这就是「环境前提不成立，冒充产物不合格」。前提必须由判据自己兜住。
                  #   修法：在这一层把两个变量钉死，孙进程无论怎么被起都用 UTF-8。
                  "PYTHONIOENCODING": "utf-8",
                  "PYTHONUTF8": "1",
                  "PYTHONPATH": os.pathsep.join(
                p for p in [str(Path(__file__).resolve().parent.parent), os.environ.get("PYTHONPATH", "")]
                if p)})
        _行 = [x for x in 子.stdout.strip().splitlines() if x.startswith("{")]
        assert _行, f"子进程没给回执：{(子.stderr or 子.stdout).strip()[-200:]}"
        回 = json.loads(_行[-1])
        预 = (回.get("预算") or {}).get("秒") or {}
        留 = 回.get("留痕") or ""
        绿 = (回.get("分类") == "达标" and bool(预.get("上限")) and bool(留)
              and Path(留).is_file())
        # ★★★★★ 2026-11-03：**失败时留下原始证据**。
        #
        #   为什么（真实教训）：本项在 `tools/ci.py` 里稳定红、手动跑稳定绿，
        #   而它给出的说明只有 `分类=None · rc=0 · 预算上限=None` ——
        #   **恰好不含任何能定位的信息**：没有子进程 stdout、没有错误原文、
        #   没有它实际取到的那行 JSON。我为此做了 6 轮二分（换 cwd / 换 env /
        #   仿 Popen 参数 / 关重定向…）全部复现不出。
        #
        #   ⇒ 这就是「假故障」的第三种形态：**判据红了，却不肯说它看到了什么**。
        #     红本身不是问题，"无法从红里学到东西"才是。
        #   ⇒ 修法不是"再猜得更准"，而是**让判据自己吐证据**：
        #     失败时把子进程 stdout/stderr 与候选 JSON 行落到 receipts/ 下的文件，
        #     并在说明里带上路径。这样下一次红是**可读的**。
        if not 绿:
            try:
                证 = Path(os.environ.get("ORCH_STATE_DIR") or ".") / "receipts"
                证.mkdir(parents=True, exist_ok=True)
                证文 = 证 / "selfcheck-⑥-证据.txt"
                证文.write_text(
                    f"selfcheck ⑥ 子进程返回 rc={子.returncode}\n"
                    f"取到的 JSON 行数={len(_行)}（用的是最后一行）\n"
                    f"最后一行前 800 字：\n{(_行[-1] if _行 else '<无>')[:800]}\n\n"
                    f"=== stdout 全文 ===\n{子.stdout}\n\n"
                    f"=== stderr 全文 ===\n{子.stderr}\n",
                    encoding="utf-8")
                证路 = str(证文)
            except Exception:                       # noqa: BLE001
                证路 = "（证据落盘失败）"
        else:
            证路 = ""
        _记(结果, "⑥ 活体任务（orch_run 一次往返）", 绿,
            f"分类={回.get('分类')} · rc={回.get('rc')} · 预算上限={预.get('上限')} "
            f"已用={预.get('已用')} · 检查点={'有' if Path(留).is_file() else '无'}"
            + (f" · 原始证据={证路}" if 证路 else ""),
            None if 绿 else dict(回.get("修法") or {"做": "看回执 error 原文"}))
    except __import__("subprocess").TimeoutExpired:
        _记(结果, "⑥ 活体任务（orch_run 一次往返）", False,
            "150s 内没拿到结论（最可能是通道探活卡住，客户端 0 CPU）——子进程已杀，自检不会挂",
            {"做": "先手动跑一次 orch_run；若也卡住 ⇒ 换有可用通道 key 的环境，"
                   "或把状态目录指到本地盘后重试"})
    except Exception as e:                  # noqa: BLE001
        _记(结果, "⑥ 活体任务（orch_run 一次往返）", False, f"{type(e).__name__}: {e}",
            {"做": "看回执 error 原文；状态目录不可写是最常见原因"})

    # ── ⑦ 契约 ──
    try:
        from agentscope_orchestrator import taixu_mcp as M2
        契约 = asyncio.run(M2.taixu_guide())
        文 = 契约 if isinstance(契约, str) else json.dumps(契约, ensure_ascii=False)
        绿 = all(k in 文 for k in ("症状路由", "历史查询", "orch_object"))
        _记(结果, "⑦ 契约（guide 可机读）", 绿,
            f"{len(文.encode())} 字节 · 症状路由/历史查询/orch_object 齐备={绿}")
    except Exception as e:                  # noqa: BLE001
        _记(结果, "⑦ 契约（guide 可机读）", False, f"{type(e).__name__}: {e}",
            {"做": "报 issue 附完整堆栈"})

    # ── ⑧ 真模型链（有通道才判绿；无通道记未验）──
    # ★ 位置很关键：**必须是最后一项**。首次落地时被插到 ① 的 except 后面，
    #   于是 run() 提前 return，②~⑦ 全被跳过 —— 自检只跑 2 项还报绿。
    #   这是"改对了单点、改坏了整体"的典型；改自检自身结构后必须看**检查项数**。
    try:
        绿, 证据, 修法 = _真模型链()
        _记(结果, "⑧ 真模型链（orch_fix 改一个坏仓 → 门复跑）", bool(绿), 证据, 修法)
        if 绿 is None:
            结果[-1]["未验"] = True
    except Exception as e:                  # noqa: BLE001
        _记(结果, "⑧ 真模型链（orch_fix 改一个坏仓 → 门复跑）", False,
            f"{type(e).__name__}: {e}", {"做": "报 issue 附完整堆栈"})

    return _汇总(结果, t0, 版本)


def _真模型链() -> tuple[bool | None, str, dict | None]:
    """⑧ 真模型链：派一个真模型改一个坏仓，再用判据门复跑判绿 ⇒ 关掉"未调真模型"这条边界。

    ★ 为什么是"能修 + 门能复判"，不是"修得对"：语义正确性不可判（gate_power 只是代理）。
      这一步能证明的是：**模型链是通的、门能对真模型的产物下判决、卡住会被杀**。
    返回 (True=绿, False=红, None=未验)。
    """
    if os.environ.get("ORCH_SELFCHECK_MODEL", "1") == "0":
        return None, "跳过：ORCH_SELFCHECK_MODEL=0（只想跑快检时用）", None
    子代码 = r"""
import asyncio, json, sys, tempfile
from pathlib import Path

repo = Path(tempfile.mkdtemp(prefix="orch-selfcheck-model-", dir=sys.argv[1]))
(repo / "pkg").mkdir()
(repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
(repo / "pkg" / "mod.py").write_text("def f():\n    return 41\n", encoding="utf-8")
cmd = [sys.executable, "-c",
       "import sys; sys.path.insert(0,'.'); from pkg.mod import f; assert f()==42, f()"]

from agentscope_orchestrator import fixloop
回 = asyncio.run(fixloop.修复(str(repo), cmd, 轮数=1))
rc = __import__("subprocess").run(cmd, cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace")
门们 = 回.get("门")
if isinstance(门们, list):                      # 门表是**列表**（每条带 门/通过），不是字典
    门摘要 = {str(g.get("门")): bool(g.get("通过")) for g in 门们 if isinstance(g, dict)}
else:
    门摘要 = 门们 if isinstance(门们, dict) else {}
print(json.dumps({"分类": 回.get("分类"),
                  "错误": (回.get("error") or "")[:300],
                  "门": 门摘要,
                  "仲裁rc": rc.returncode,
                  "仲裁输出": ((rc.stdout or "") + (rc.stderr or "")).strip()[-300:]},
                 ensure_ascii=False))
"""
    import subprocess
    工作 = _临时目录("orch-selfcheck-model-")
    try:
        子 = subprocess.run(
            [sys.executable, "-c", 子代码, str(工作)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=420,
            env={**os.environ,
                  # ★★★★★ 2026-11-03 根因修复：**孙进程的输出编码必须显式固定**。
                  #
                  #   真事故（本项在 `tools/ci.py` 里稳定红、手动跑稳定绿；我做了
                  #   6 轮二分全复现不出，最后靠"让判据自己吐证据"才定位）：
                  #     · `ci.py` 用 `Popen(stdout=<二进制文件>)` 起 selfcheck ⇒
                  #       本进程 stdout 不是终端；
                  #     · 此时 Windows 上 Python 的 stdout 编码**退回活动代码页(GBK)**，
                  #       除非 `PYTHONIOENCODING`/`PYTHONUTF8` 兜住；
                  #     · 而 `ci.py` **没设**这两个（我手动跑时 shell 里有 ⇒ 所以绿）；
                  #     · 孙进程于是用 GBK 输出那份 JSON，父进程却按 `encoding="utf-8"`
                  #       解码 ⇒ 中文键 `"分类"` 变成替换字符 ⇒ `回.get("分类")` 得 None
                  #       ⇒ 判据报 `分类=None`，**看起来像编排器坏了**。
                  #   一句话：**判据依赖调用方恰好设了编码环境变量** ——
                  #   这就是「环境前提不成立，冒充产物不合格」。前提必须由判据自己兜住。
                  #   修法：在这一层把两个变量钉死，孙进程无论怎么被起都用 UTF-8。
                  "PYTHONIOENCODING": "utf-8",
                  "PYTHONUTF8": "1",
                  "PYTHONPATH": os.pathsep.join(
                p for p in [str(Path(__file__).resolve().parent.parent),
                            os.environ.get("PYTHONPATH", "")] if p)})
        行 = [x for x in 子.stdout.strip().splitlines() if x.startswith("{")]
        if not 行:
            return (False, f"子进程没给回执：{(子.stderr or 子.stdout).strip()[-200:]}",
                    {"做": "先手动跑一次 orch_fix；仍失败就看通道与 key"})
        回 = json.loads(行[-1])
        分类 = 回.get("分类")
        if 分类 == "基础设施不可用":
            return (None, f"未验（环境侧）：{回.get('错误') or 分类}",
                    {"做": "配一个可用通道 key 后重跑；或 ORCH_SELFCHECK_MODEL=0 明确跳过"})
        绿 = 回.get("仲裁rc") == 0
        return (绿, (f"分类={分类} · 头门={回.get('门')} · 仲裁rc={回.get('仲裁rc')} "
                     f"· 输出={(回.get('仲裁输出') or '')[:80]}"),
                None if 绿 else
                {"做": "看门表与仲裁输出；这是**真模型**没改对，不是环境问题"})
    except subprocess.TimeoutExpired:
        return (False, "420s 内没拿到回执（模型链卡住）——子进程已杀，自检不会挂",
                {"做": "先手动跑一次 orch_fix 看单轮耗时；必要时换通道"})
    except FileNotFoundError:
        return None, "未装到可运行的解释器（跳过）", None


def _汇总(结果: list[dict], t0: float, 版本: str) -> dict:
    # 清理自检自己造的临时目录（失败也要清：留着就是污染调用方的仓）
    import shutil as _sh
    while _临时们:
        d = _临时们.pop()
        _sh.rmtree(d, ignore_errors=True)
    未验 = [x for x in 结果 if x.get("未验")]
    红 = [x for x in 结果 if not x["通过"] and not x.get("未验")]
    return {"ok": not 红, "版本": 版本,
            "平台": f"{sys.platform} · python {sys.version.split()[0]}",
            "耗时_s": round(time.time() - t0, 1),
            "检查": 结果,
            "未验": [x["检查"] for x in 未验],
            "结论": ("全绿：这层能干活" if not 红 and not 未验 else
                    (f"{len(红)} 项红 —— " + "；".join(x["检查"] for x in 红) if 红
                     else f"绿但 {len(未验)} 项未验 —— " + "；".join(x["检查"] for x in 未验))),
            "边界": ("冒烟：不证明语义正确（那靠判据门）；真模型链只在有可用通道时验一轮，"
                     "验的是\"能修 + 门能复判\"，不是\"修得对\"")}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    机器读 = "--json" in argv
    try:
        if 机器读:
            # ★ stdout 只能有 JSON：日志/print 全赶去 stderr（实测过噪声会让解析失败）
            import contextlib
            with contextlib.redirect_stdout(sys.stderr):
                出 = run()
        else:
            出 = run()
    except Exception as e:                   # noqa: BLE001
        出 = {"ok": False, "版本": "未取到", "平台": f"{sys.platform}",
              "耗时_s": 0.0, "检查": [{"检查": "自检自身", "通过": False,
                                       "证据": f"{type(e).__name__}: {e}",
                                       "修法": {"做": "把 ORCH_STATE_DIR 指到可写目录后重试；"
                                                      "仍崩就报 issue 附本行"}}],
              "结论": f"自检自己崩了：{type(e).__name__}",
              "边界": "崩溃 ≠ 产品判定；这说明自检本身在这台机器上跑不起来"}
    if 机器读:
        print(json.dumps(出, ensure_ascii=False, indent=1))
    else:
        print(f"orch 自检 · 版本 {出['版本']} · {出['平台']} · {出['耗时_s']}s")
        for x in 出["检查"]:
            # ★ 三态打印：未验既不是 PASS 也不是 FAIL（印成 FAIL 会让人以为产品坏了）
            标 = "未验" if x.get("未验") else ("PASS" if x["通过"] else "FAIL")
            print(f"  [{标}] {x['检查']}：{x['证据']}")
            if not x["通过"] and x.get("修法"):
                print(f"         修法：{json.dumps(x['修法'], ensure_ascii=False)}")
        print(f"\n结论：{出['结论']}")
        print(f"边界：{出['边界']}")
    return 0 if 出["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

