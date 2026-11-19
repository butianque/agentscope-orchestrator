"""MCP stdio server：把真实任务闭环包成工具，直连 DSH 聊天可见进度。

服务名/产物库/判据门等**项目知识全部来自档案**（orchestrator.profile.yml），
本模块只做协议适配，不持有任何项目专有内容。

工具面（防跑偏五道机器闸的 MCP 侧落形）：
  - submit_task  提交任务（必须锚定产物库内真实单元；锚不到→拒跑；后台并发跑）
  - list_tasks   列出全部任务及状态
  - task_status  单任务详细状态（阶段/各窗判据门/收敛）
  - judge_result 判据门详情（各门真值）
  - read_report  读产物/报告（限产物库/ 与 products/ 内，只读）
  - audit_log    全量审计（每窗每门，可回放）
  - cancel_task  取消运行中任务
  - resume_task  断点续跑（error/convergence_failed/cancelled/孤儿 running；
                复用已过窗=子任务幂等，只补跑失败窗）

权限边界（不暴露）：改判据 / 改库 / 改产物。promote 由 submit_task 可选开启，
但产物写回仍过全部门 + 最终门复跑（机器闸把关），聊天层无软干预通道。

用法：venv\\Scripts\\python.exe src\\agentscope_orchestrator\\taixu_mcp.py
"""
from __future__ import annotations

import re

import asyncio
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mcp.server.fastmcp import FastMCP

import agentscope_orchestrator.real_task as rt
from agentscope_orchestrator.autoengine import (MemoryIndex, scan_all,
                                                  scan_project)
from agentscope_orchestrator import autoengine as ae
from agentscope_orchestrator import auto_goal as ag
from agentscope_orchestrator import goal_runner as gr  # 按目标树清单无人值守裁决
from agentscope_orchestrator import close_loop as cl  # 规划→裁决→定位→改码→复验 一次调用
from agentscope_orchestrator import goal_iterate as gi  # 迭代闭环（预算护栏/打转检测）
from agentscope_orchestrator import model_coder as mc  # 模型产改码（机器门裁决，模型不判自己）
from agentscope_orchestrator import channel_doctor as cd  # 开工前通道体检（逐对真探活）
from agentscope_orchestrator import repeatability as rpt  # 多文件可重复性（真工具链）
from agentscope_orchestrator import failure_cause as fc  # 工程失败归因（分清责任层）
from agentscope_orchestrator import field_contract as fcl  # 跨模块字段契约表（防猜错）
from agentscope_orchestrator import audit as adt  # 整体交付对账（已证明/没测到）
from agentscope_orchestrator import ts_receipt  # MCP 任务每轮渲染人类可读 .md 回执
from agentscope_orchestrator.index import UnitIndex
from agentscope_orchestrator import doc_store as ds  # 文档外置+自建索引+按需查看（防会话卡死）
from agentscope_orchestrator import profile as prof_mod
# ★ R2-F4：MCP 入口的命令安全闸（此前只在 gate_derive 内部被调）
from agentscope_orchestrator.gate_derive import check_cmd_safety

# ── 日志：MCP stdio 的 stdout 是协议通道，所有业务日志走 stderr ──
_logger = logging.getLogger("orchestrator")
_logger.addHandler(logging.StreamHandler(sys.stderr))
_logger.setLevel(logging.INFO)
rt._log_fn = lambda *a: _logger.info(" ".join(str(x) for x in a))

ORCH = rt.ORCH
# ★ 2026-10-31（交付审计 P0）：**可变状态一律走 STATE_ROOT，绝不走 ORCH/TOOL_ROOT**。
#   事故形态：wheel 装完后 `rt.ORCH` 解析到 `<venv>/Lib`，于是任务库落在解释器目录里；
#   该目录对服务进程不可写 ⇒ 所有写工具（orch_run/orch_fix/run_errand/submit_task）
#   每次 `attempt to write a readonly database`，而只读工具全绿。
STATE = prof_mod.STATE_ROOT
ARTIFACT_DIR = rt.ARTIFACT_DIR
PRODUCTS = rt.PRODUCTS
JOBS_FILE = STATE / "jobs" / "tasks.json"
# ★★ 2026-10-31（交付审计）：这里原来只有一行 `JOBS_FILE.parent.mkdir(...)`。
#   实机事故：本机所有候选状态根都写不进去（`%LOCALAPPDATA%` / 家目录 / `E:\Temp`
#   全部 `WinError 5 拒绝访问`），候选逻辑于是退到最后一个把父目录直接拼出来，
#   紧接着这一行抛 `FileNotFoundError: ...\agentscope-orchestrator\jobs` →
#   换个异常又是 `PermissionError [WinError 5]`。用户看到的是**一段 mkdir 堆栈**，
#   既不知道是"状态目录不可写"，也不知道该怎么办。
#   承诺过"全失败就响亮报错"，那就得是**人能读懂、且给出下一步动作**的报错。
#   ⇒ 在这里显式兜住，并指名修法（设 ORCH_STATE_DIR）。不再让裸堆栈冒出去。
try:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
except OSError as _e:
    _候选 = "\n".join(f"    · {p}" for p in getattr(prof_mod, "_候选状态根", lambda: [])())
    raise SystemExit(
        "✗ 状态目录不可写，编排器无法启动。\n"
        f"  期望位置：{STATE}\n"
        f"  系统报错：{type(_e).__name__}: {_e}\n"
        "  试过的候选：\n" + (_候选 or "    （无）") + "\n"
        "  修法：显式指定一个**可写**目录后再启动，例如\n"
        "    PowerShell:  $env:ORCH_STATE_DIR = 'D:\\orch-state'\n"
        "    bash:        export ORCH_STATE_DIR=/var/lib/orch-state\n"
        "  说明：状态目录放任务库/成本账/审计回放，必须**长期可写**，且不要放在安装树里。"
    ) from None


def _台账文件() -> str | None:
    """工作台账的**真实**位置；没有就是 None（不返回一个永远打不开的路径）。

    ★ 修的是死字段（2026-10-31 审计）：原写法
      `str(_ORCH_DIR / "工作台账.md") if "_ORCH_DIR" in globals() else None`
      里 `_ORCH_DIR` 从来没定义过 ⇒ `orch_history` 的 `台账` **永远是 null** ——
      一个看起来在工作、实际永远为空的功能点，比没有更坏。
    ★ 搜法要去重两种布局：源码树的台账在工具仓根；装成 wheel 后工具根是包目录，
      台账在**档案所指的项目**里。所以除了两个根，还看项目根与档案所在目录。
    """
    基们 = [prof_mod.STATE_ROOT, prof_mod.TOOL_ROOT]
    # ★ 2026-11-01：再往上**各看一级**（bounded，只一级，不在磁盘上乱走）。
    #   实机布局：部署在 `E:\...\orch-deploy\.venv\...`，状态根被钉在
    #   `<项目根>\.orchestrator-state`，而台账在
    #   `<项目根>\多智能体编排\agentscope_orchestrator\工作台账.md`。
    #   原来四个候选（状态根/工具根/项目根/档案目录）一个都命不中 ⇒ 部署态
    #   `台账` 恒为 null，而源码树里正常 —— 又一个"只在开发机成立"的读数。
    for _r in (prof_mod.STATE_ROOT, prof_mod.TOOL_ROOT):
        try:
            基们.append(Path(_r).parent)
        except Exception:                      # noqa: BLE001
            pass
    _显式 = (os.environ.get("ORCH_LEDGER") or "").strip()
    if _显式:
        return _显式 if Path(_显式).is_file() else None
    try:
        prof = prof_mod.get_profile()
        for p in (getattr(prof, "repo_root", None),
                  Path(prof.source_path).parent if getattr(prof, "source_path", None) else None):
            if p:
                基们.append(Path(p))
    except Exception:                          # noqa: BLE001
        pass
    见: set[str] = set()
    for base in 基们:
        for cand in (Path(base) / "工作台账.md",
                     Path(base) / "多智能体编排" / "agentscope_orchestrator" / "工作台账.md"):
            s = str(cand)
            if s in 见:
                continue
            见.add(s)
            if cand.is_file():
                return s
    return None


mcp = FastMCP(prof_mod.get_profile().mcp_name)


def _TOOL(short: str) -> str:
    """工具名前缀来自档案（项目品牌不写死在工具源码里）。"""
    return prof_mod.get_profile().tool_name(short)


def _工具总数() -> int:
    """已注册工具数（从 FastMCP 管理器现算）。

    ★ 契约文字里的"全部有 N 个"曾两次写错（0.9.2、0.12.3），第三次是自检写死 18。
      治法是**不再手写数字**：谁能算谁算。
    """
    try:
        return len(getattr(getattr(mcp, "_tool_manager", None), "_tools", {}) or {})
    except Exception:                      # noqa: BLE001
        return 0

# 只读控制台地址（薄 DSH：大载荷只看控制台，不走 MCP 大载荷返给聊天层）
_CONSOLE = "http://127.0.0.1:8088"

# ★★★★★ 修复 R3（**P0**）：`_RUST_ROOT` 此前**从未定义**，而 `run_errand` 里
#   引用了它两次 ⇒ 「任意任务」工具**一调就 `NameError`**，完全不可用。
#   实测证据：`"_RUST_ROOT" in vars(taixu_mcp)` 为 `False`，而
#   `inspect.getsource(run_errand).count("_RUST_ROOT") == 2`。
#   ★ 之所以能活到今天：**没有任何一条判据门检查过「模块里的全局名是否都存在」**。
#     本次同时补 `_未定义全局名()` 静态门（见文件末尾自检），让**这一类**再也出不去。
#   语义 = 工程任务默认工作目录 = 档案声明的**主树根**（项目无关，从档案派生）。
_RUST_ROOT = prof_mod.get_profile().repo_root

# ── G7 全自动引擎：统一记忆索引（懒构建，不阻塞 MCP 启动） ──
_MEM: MemoryIndex | None = None
_CODE_ROOTS = ([prof_mod.TOOL_ROOT / "src", prof_mod.TOOL_ROOT / "tests"]
               if (prof_mod.TOOL_ROOT / "src").is_dir() else [prof_mod.PKG_DIR])
_TASK_ROOTS: list = []  # 任务书源默认不扫；需要时经 auto_scan(scan_tasks=True) 开启


def _ensure_memory(scan_tasks: bool = False) -> MemoryIndex:
    """懒构建记忆索引 —— **按项目档案的 `memory:` 段扫**（见 `auto_scan`）。"""
    global _MEM
    if _MEM is None:
        _MEM = MemoryIndex()
        scan_project(_MEM, rt.ARTIFACT_DIR, prof_mod.get_profile().repo_root)
    return _MEM

# ── 任务存储（G3：SQLite 持久化 + 断点恢复；启动时迁移旧 tasks.json） ──
_jobs: dict[str, dict] = {}
_runners: dict[str, asyncio.Task] = {}
_seq = 0

# ★ 兜底事件循环（2026-10-13）：让编排器**脱离 MCP 服务器**也能跑。
#
# 缺口（实测）：`_start_job` 用 `asyncio.create_task` 派发，一旦当前线程没有运行中的
#   事件循环（命令行直接调用就是如此），create_task 抛
#   `RuntimeError: no running event loop` ⇒ 任务被拒：
#     「派发失败（no running event loop）：当前上下文无事件循环，任务未启动。
#       请通过 MCP 服务器（异步工具）提交。」
#   ⇒ 编排器**只能在 MCP 服务器进程里跑**，没法挂后台跑一夜（无人值守的硬前提）。
#
# 修法：没有运行循环时，起一个**本进程的后台事件循环线程**接管派发。
#   这与 MCP 路径**同语义**（同一套 coroutine、同一套门、同一套落库），
#   只是换了个循环宿主 —— 不是另起一套执行机制。
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOOP_LOCK = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """取得一个**可用的事件循环**：优先当前运行的；否则起后台循环线程。

    诚实边界：后台循环线程在本进程存活期间常驻；进程退出即随之结束
    （无人值守场景由外层 `run_forever` 持有，不提前退）。
    """
    global _LOOP, _LOOP_THREAD
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    with _LOOP_LOCK:
        if _LOOP is not None and not _LOOP.is_closed():
            return _LOOP
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever,
                             name="taixu-orchestrator-loop", daemon=True)
        t.start()
        _LOOP, _LOOP_THREAD = loop, t
        return loop


def _submit_coro(coro) -> asyncio.Task:
    """把协程派发到可用循环，返回 Task（跨线程安全）。"""
    loop = _ensure_loop()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if loop is running and running is not None:
        return loop.create_task(coro)          # 同循环内直接建
    return asyncio.run_coroutine_threadsafe(coro, loop)   # 跨线程派发


# ★ 任务库位置：默认生产库；可用环境变量 TAIXU_DB 指向独立库。
#   为什么需要这个开关（2026-10-13 实测事故）：
#     测试脚本曾直接 `unlink()` 生产 jobs.db 再重建，而**常驻 MCP 服务器**
#     （另一进程）持有自己的内存 `_jobs`/`_seq` 快照、从不重读磁盘
#     ⇒ 两边各自 `INSERT OR REPLACE` 互相覆盖 ⇒ 任务历史丢失、ID 计数退回。
#     测试必须指向独立库，生产库只由唯一一个服务器进程写。
_DB_FILE = Path(os.environ.get("TAIXU_DB") or (STATE / "jobs" / "jobs.db"))
_DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def _db_可写断言() -> None:
    """写库前先证一次「状态目录可写」，失败就给**可执行的归因**。

    ★ 为什么需要（2026-10-31 交付审计 P0）：真事故里调用方只看到
      `attempt to write a readonly database` —— 这句话既不说哪个目录、也不说该改什么，
      于是人只会去改代码。实测根因是**状态目录落在不可写处**（当时在 venv/Lib）。
      这里把它换成人能直接执行的修法。
    """
    try:
        _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        _探 = _DB_FILE.parent / f".写探针.{os.getpid()}"
        _探.write_text("ok", encoding="utf-8")
        _探.unlink()
    except OSError as e:
        raise RuntimeError(
            f"状态目录不可写：{_DB_FILE.parent}（{type(e).__name__}: {e}）。"
            f"任务库必须落在可写处 —— 设环境变量 ORCH_STATE_DIR（或 ORCH_HOME）指向"
            f"一个可写目录后重启服务器；`ORCH_STATE_DIR` 当前={os.environ.get('ORCH_STATE_DIR')!r}。"
            f"本轮未做任何写操作，仓保持原状。") from e



def _db_init() -> None:
    _db_可写断言()
    conn = sqlite3.connect(_DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS jobs ("
                 "id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    conn.commit()
    conn.close()


def _db_load() -> dict[str, dict]:
    out: dict[str, dict] = {}
    conn = sqlite3.connect(_DB_FILE)
    try:
        for row_id, row_data in conn.execute("SELECT id, data FROM jobs"):
            try:
                out[row_id] = json.loads(row_data)
            except Exception:
                _logger.warning("job %s 数据损坏，跳过", row_id)
    finally:
        conn.close()
    return out


def _db_save_one(job_id: str, job: dict) -> None:
    _db_可写断言()
    conn = sqlite3.connect(_DB_FILE)
    try:
        conn.execute("INSERT OR REPLACE INTO jobs (id, data) VALUES (?, ?)",
                     (job_id, json.dumps(job, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def _db_sync_in() -> int:
    """把磁盘上**本进程不知道**的行并进内存（多写者/重启后追赶）。

    为什么需要：服务器只在启动时 `_db_load()` 一次，此后全靠内存。
    若有别的写者（测试进程、另一实例、手工补跑）往库里写了新行，
    本进程既看不见它们、又会在下次 `_db_save_one` 时按自己的旧内存
    去覆盖 —— 任务历史就这样丢。这里主动并表，只增不改本进程已知的行。
    """
    n = 0
    try:
        disk = _db_load()
    except Exception:
        return 0
    for jid, job in disk.items():
        if jid not in _jobs:
            _jobs[jid] = job
            n += 1
    return n


def _migrate_legacy() -> None:
    """迁移旧 JSON 落盘数据（存在则并入 SQLite 后归档原文件）。"""
    if not JOBS_FILE.exists():
        return
    try:
        legacy = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    conn = sqlite3.connect(_DB_FILE)
    try:
        for jid, job in legacy.items():
            conn.execute("INSERT OR IGNORE INTO jobs (id, data) VALUES (?, ?)",
                         (jid, json.dumps(job, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    archived = JOBS_FILE.with_suffix(".json.legacy")
    JOBS_FILE.replace(archived)


_db_init()
_migrate_legacy()
_jobs = _db_load()
_seq = max((int(jid.rsplit("_", 1)[-1]) for jid in _jobs
            if "_" in jid and jid.rsplit("_", 1)[-1].isdigit()), default=0)


def _next_id() -> str:
    """生成新任务 id。

    ★ 修复（2026-10-13 实测事故）：原实现只在**进程启动时**算一次 `_seq`，
       之后永远自增内存计数器。一旦库里有**别的写者**插入过更新的行
       （测试进程、另一个服务器实例、手工补跑），本进程的计数器就落后，
       下一个 id 会**撞上已存在的行**，而落库用的是 `INSERT OR REPLACE`
       ⇒ 静默**覆盖别人的任务**（实机已发生：磁盘有 t1013_006，
          常驻进程 _seq=5，下一单就是 t1013_006 → 覆盖）。
       现改为：每次取 id 前**重读磁盘**取真实最大序号，双保险再查一遍占用。
    """
    global _seq
    day = time.strftime("%m%d")
    best = _seq
    # ① 内存里已有的
    for jid in _jobs:
        tail = jid.rsplit("_", 1)[-1]
        if "_" in jid and tail.isdigit():
            best = max(best, int(tail))
    # ② 磁盘上已有的（防别的写者领先）
    try:
        conn = sqlite3.connect(_DB_FILE)
        try:
            for (jid,) in conn.execute("SELECT id FROM jobs"):
                tail = jid.rsplit("_", 1)[-1]
                if "_" in jid and tail.isdigit():
                    best = max(best, int(tail))
        finally:
            conn.close()
    except Exception:
        pass
    # ③ 占用即跳过，绝不覆盖已存在的任务
    while True:
        best += 1
        cand = f"t{day}_{best:03d}"
        if cand not in _jobs and not _db_has(cand):
            _seq = best
            return cand


def _db_has(job_id: str) -> bool:
    """该 id 是否已在库（含别的进程写入的行）。"""
    try:
        conn = sqlite3.connect(_DB_FILE)
        try:
            return conn.execute("SELECT 1 FROM jobs WHERE id = ?",
                                (job_id,)).fetchone() is not None
        finally:
            conn.close()
    except Exception:
        return False


# ── 防跑偏闸 ①：方向锚定（单元必须真实存在） ──
# 锚定走档案声明的产物库 UnitIndex（点式/前缀/theorem 级均登记），
# 停用目录不入索引 → 不会锚定到归档代码。
_ARTIFACT_INDEX = UnitIndex()
# ★ R546：**构建推迟到真要用的时候**。原来在这里 `build_artifact(...)`，
#   而 `unattended.py` 在模块级 `import taixu_mcp` ⇒ `orch-run --help` 也要付这笔钱。
#   见本文件下方 `_确保索引544()`。


_索引已建544 = False


def _确保索引544() -> None:
    """★ R546：两套索引**按需**构建（幂等，失败不致命）。

    ★ 为什么必须惰性：`orch-run` 在模块级 import 本模块，模块级构建就意味着
      **任何** `--help` / `--json` 探测都要先扫全仓（实测 11 秒级）。
      与 R543 同一个病，同一个治法：把「第一次要用」留在「第一次要用」。
    """
    global _索引已建544
    if _索引已建544:
        return
    _索引已建544 = True                      # ★ 先置位：构建过程可能回调本模块
    try:
        if ARTIFACT_DIR:
            _art = prof_mod.get_profile().artifact
            _ARTIFACT_INDEX.build_artifact(ARTIFACT_DIR,
                                       key_prefix=_art.key_prefix,
                                       disabled_marker=_art.disabled_marker,
                                       glob=_art.glob)
            _logger.info("artifact index built")
    except Exception as _iexc:                # noqa: BLE001
        # 索引构建失败不致命：锚定退回字符串直解，判据门仍守真编译
        _logger.warning("artifact index build failed: %s", _iexc)
    try:
        _build_rust_index()
        _logger.info("engine source index built: %d units", _RUST_INDEX.size())
    except Exception as _iexc:                # noqa: BLE001
        _logger.warning("engine source index build failed: %s", _iexc)


def _anchor_unit(unit_id: str) -> Path | None:
    """unit_id 点分路径 → 产物库下真实模块文件；索引命中或真实存在则返回。"""
    _确保索引544()
    p = _ARTIFACT_INDEX.query(unit_id)
    if p and p.is_file():
        return p.resolve()
    if not ARTIFACT_DIR:
        return None
    # 退回字符串直解（兼容索引未覆盖但物理存在的单元）
    art = prof_mod.get_profile().artifact
    parts = unit_id.split(".")
    if parts and art.key_prefix and parts[0] == art.key_prefix:
        parts = parts[1:]
    if not parts:
        return None
    suffix = Path(art.glob).suffix or ".lean"
    rel = "/".join(parts) + suffix
    p = (ARTIFACT_DIR / rel).resolve()
    if p.exists() and p.is_file():
        return p
    return None


# ── 工程执行型：源码索引（供多智能体提交 build/test 等命令到机器判据门）──
_RUST_INDEX = UnitIndex()


def _build_rust_index() -> None:
    """按档案 source 规格把源码单元记为点式键（排除备份/目标/工具层）。"""
    prof = prof_mod.get_profile()
    skip = prof.source.skip
    globs = prof.source.globs or ("**/*.rs",)
    seen: set = set()
    for r in prof.source.roots:
        root = Path(r)
        if not root.is_absolute():
            root = prof.repo_root / root
        if not root.is_dir():
            continue
        for g in globs:
            for p in sorted(root.rglob(g)):
                if p in seen or not p.is_file():
                    continue
                seen.add(p)
                if skip and any(s in p.relative_to(root).parts for s in skip):
                    continue
                rel = p.relative_to(prof.repo_root).with_suffix("")
                dotted = ".".join(rel.parts)
                _RUST_INDEX.register(dotted, p)
                _RUST_INDEX.register(str(p), p)


def _anchor_engine(unit_id: str) -> Path | None:
    """锚定源码单元（工程执行型任务的目标单元）。
    unit_id 取源码根相对点分键（如 <root>.某模块 或 裸模块名）。
    仅索引内真实存在的文件视为可锚定，防多智能体把任务派到不存在的假设文件。"""
    prof = prof_mod.get_profile()
    prefixes = [f"{str(r).replace(chr(92), '/').strip('/')}."
                for r in (prof.source.roots or ())]
    prefixes = prefixes or ["src."]
    cands = [unit_id]
    for pre in prefixes:
        if unit_id.startswith(pre):
            cands.append(unit_id[len(pre):])
        else:
            cands.append(pre + unit_id)
    for k in cands:
        _确保索引544()
        p = _RUST_INDEX.query(k)
        if p and p.is_file():
            return p.resolve()
    return None


# ★ R546：源码索引的构建也**推迟**（原为「函数已定义，模块级执行一次」）。
#   代价实测：`orch-run --help` 11.11s、`orch-mcp --help` 11.67s，且 `--help` 有副作用。


def rebuild_indexes(path: str | Path | None = None) -> dict:
    """★ 换工程：**在进程内**重建档案与全部索引（不再需要重启进程）。

    缺口（实测）：索引在**模块导入时**构建一次（上面的模块级 `_build_rust_index()`），
    档案单例也只在首次 `get_profile()` 时加载 ⇒ 同一进程内改档案/换工程，
    锚定仍按**旧索引**查 ⇒ 必失败。此前结论是"换工程必须重启进程"。

    修法（本函数）：显式按新档案重建三样 ——
      ① 档案单例（`prof_mod.reset_profile(path)`）
      ② 工程源码索引（`_RUST_INDEX`，锚定 `unit_id` 用）
      ③ 产物库索引（`_ARTIFACT_INDEX`，锚定形式化库单元用）
      ④ 记忆索引（`_MEM`，未闭合候选用；惰性，下次取用时自然重建）

    诚实边界：**正在跑的任务不受影响**（它们持有自己那份任务规约），
    但**尚未派发**的任务会按新档案锚定。故换工程应在无在跑任务时做。
    """
    旧档案 = prof_mod.get_profile().name
    新档案 = prof_mod.reset_profile(path)
    # ① 源码索引：清空重建（UnitIndex 无 clear ⇒ 换新实例后重灌）
    global _RUST_INDEX, _ARTIFACT_INDEX, _MEM
    _RUST_INDEX = UnitIndex()
    _build_rust_index()
    # ② 产物库索引
    #   ★ 注意：`ARTIFACT_DIR` 是**模块导入时**由档案解析出的常量，换档案后它
    #     仍指旧库（实测：切到实验场后产物库单元数仍是旧库的 14756）。
    #     故这里**按新档案重新解析**产物库根，而不是沿用模块常量。
    _ARTIFACT_INDEX = UnitIndex()
    try:
        新产物根 = 新档案.artifact.root
        if 新产物根:
            _art = 新档案.artifact
            _ARTIFACT_INDEX.build_artifact(Path(新产物根),
                                           key_prefix=_art.key_prefix,
                                           disabled_marker=_art.disabled_marker,
                                           glob=_art.glob)
    except Exception as exc:      # noqa: BLE001 重建失败如实报，不静默
        _logger.warning("[orchestrator] 产物库索引重建失败：%s", exc)
    # ③ 记忆索引置空 ⇒ 下次取用时按新档案重建（惰性，避免白扫）
    _MEM = None
    info = {"旧档案": 旧档案, "新档案": 新档案.name,
            "源码单元数": _RUST_INDEX.size(),
            "产物库单元数": _ARTIFACT_INDEX.size(),
            "repo_root": str(新档案.repo_root)}
    _logger.info("[orchestrator] 索引已按新档案重建：%s", info)
    return info


# ── 防跑偏闸 ②：模板完整性（定理名 + 占位 sorry 必须存在） ──
def _validate_template(task: rt.TaskSpec) -> str | None:
    if f"theorem {task.theorem}" not in task.template:
        return "template 缺少定理声明 theorem {theorem}"
    if "sorry" not in task.template:
        return "template 缺少证明占位 `by sorry`（模型须替换的占位）"
    if task.statement_marker not in task.template:
        return "template 缺少陈述标记（防模型篡改陈述）"
    return None


# ── G5 调度：队列 / 优先级 / 依赖链 ──
# 同窗已并行，任务级再用并发上限收敛，让「优先级」真实生效：仅最高优先级的
# 就绪任务先运行，其余排队。依赖只允许引用已存在任务 → 天然 DAG，无环。
# 2026-10-11 调至 4：i7-6800K 12 线程 + 64G，配合现有负载（develop cargo + lean 单元）
# 的安全最高档；任务主体是异步 API 窗 + lake 单核，内存余量充足，CPU 时间片挤压但不死机。
_CONCURRENCY = 4
# ★★ 2026-10-30 实测：`_CONCURRENCY` 只管**派发**出去的任务；`orch_run(wait=True)`
#   在 orch_run 里**就地 await 执行体**（见下方注释），完全绕过上限 —— 实测 6 个
#   wait=True 长任务峰值并发 **6**（声明上限 4）。6 个 cargo 同时编译足以打死机器。
#   ⇒ 就地路径也过同一道闸；并把"排队等了多久"如实交出去（排队不可见 = 调用方盲等）。
_并发闸 = None


def _取闸() -> "asyncio.Semaphore":
    """懒建（必须绑定当前事件循环，导入期建会绑错循环）。"""
    global _并发闸
    if _并发闸 is None:
        _并发闸 = asyncio.Semaphore(max(1, int(_CONCURRENCY)))
    return _并发闸


async def _有槽开跑(job_id: str, coro):
    """★★ 2026-11-01：跨进程并发槽（把"每进程 4"变成真·全局）。

    ★ 此前 `guide` 的"做不到"里如实写着「并发上限是**每进程 4**，不是全局配额」：
      起 3 个服务器进程就是 12 路并发，cargo 一起上足以打死机器 —— 而每个进程都觉得自己守规矩。
    ⇒ `global_gate.抢槽` 用**操作系统文件锁**占槽（进程怎么死锁都由内核释放，没有僵尸槽）。
    ★ 降级如实记：槽目录不可写 / 平台无锁 ⇒ 退回进程内上限，并把这个事实写进 job
      （静默降级会让人以为全局生效，那是另一种撒谎）。
    """
    try:
        from . import global_gate as _gg
    except Exception as e:                     # noqa: BLE001
        j = _jobs.get(job_id)
        if j is not None:
            j["并发降级"] = f"global_gate 导入失败：{type(e).__name__}: {e}"
        await coro
        return
    _t0 = time.time()
    try:
        async with _gg.抢槽(_CONCURRENCY) as 槽:
            j = _jobs.get(job_id)
            if j is not None:
                if 槽.get("降级"):
                    j["并发降级"] = str(槽.get("原因") or "槽目录不可用")
                else:
                    j["全局槽"] = 槽.get("槽")
                    _等 = time.time() - _t0
                    if _等 > 0.2:
                        j["排队"] = {"等全局槽_s": round(_等, 2),
                                     "说明": "跨进程并发上限被其它进程占满 ⇒ 先排队"}
            await coro
    finally:
        j = _jobs.get(job_id)
        if j is not None:
            j.pop("全局槽", None)


def _dep_state(job_id: str) -> tuple[str, str]:
    """依赖判定：返回 ("ready"|"waiting"|"failed", 原因)。

    ★★★★★ 修复 R2-F3（**真缺陷**）：依赖满足**只认 `promoted`**。

    原实现把 `staged` 也算成功 —— 那是错的。按 `real_task.py:501-509`：
      · `promoted` = 产物已**写回主树**（过全部门 + 最终门复跑）；
      · `staged`   = 产物只落 `products/` **暂存**，主树**没变**。

    ⇒ 依赖是 `staged` 时放行下游 = 下游在**旧树**上开跑，
      它以为前提已就位，其实主树里根本没有那个产物 ⇒ **产出基于不存在的前提**。
      这正是"跑任务会出问题"的典型形态，而且**不报错**（静默错误产出）。

    ★ 诚实边界：`staged` 不是失败，是**未落地**。所以判 `failed` 并把原因写清，
      而不是让下游静默跑 —— 调用方看到原因后可选择带 `promote` 重跑依赖。
    """
    deps = _jobs[job_id].get("depends_on") or []
    for d in deps:
        dj = _jobs.get(d)
        if dj is None:
            return ("failed", f"依赖任务 {d} 不存在")
        st = dj.get("state")
        if st == "promoted":
            continue
        if st == "staged":
            return ("failed",
                    f"依赖任务 {d} 只**暂存**（`staged`：产物落 products/ 未进主树）"
                    f" ⇒ 下游不能在旧树上开跑；请以 promote 重跑该依赖")
        if st in ("running", "queued"):
            return ("waiting", f"等待依赖任务 {d} 完成")
        return ("failed", f"依赖任务 {d} 终态 {st}")
    return ("ready", "")


def accept_closed(planned: dict) -> tuple[bool, str]:
    """★ R256（2026-11-05 · 目标树纪律接进执行层）：**父目标不许假完成**。

    ★ 为什么必须接在**执行侧**而不只在计划器：计划器多返回一个 `closed` 字段，
      如果没人读它，那只是"打印了一份真相"—— 无人值守照旧会报绿。
      真形状（本机可复现）：一份 3 个实现任务的计划，若 acceptance 只依赖 1 个，
      另 2 个做不做都判"目标完成"。

    ★ 零回退：本函数是**新增**的，老调用方一个都不改；
      谁想用"父目标闭合"这条纪律，就在提交前调它一次。
      返回 `(能否接受, 原因)`；`planned` 就是 `auto_goal.plan_tasks()` 的返回值。
    """
    if not isinstance(planned, dict):
        return (False, f"计划不是 dict：{type(planned).__name__}")
    if not planned.get("conserved"):
        return (False, "计划未通过接线守恒（conserved 非真）⇒ 拒发")
    if "closed" not in planned:
        return (False, "计划没有 `closed` 字段（旧版计划器）⇒ 无法证明父目标闭合，拒发")
    if not planned.get("closed"):
        _缺 = planned.get("uncovered") or []
        # ★★ R259b（真读数抓的）：`uncovered` 为空时旧文案照样写
        #   「**0 个**实现任务不在闭包里 ⇒ 拒发」—— **红得没法修**，
        #   而且它把真正的原因（没有闭合点 / 没有实现任务）藏起来了。
        #   口径：「红得没法修」本身就是缺陷（同 R254 齿里那条"没点名"）。
        if _缺:
            return (False, "父目标不闭合："
                           f"{len(_缺)} 个实现任务不在 acceptance 的依赖闭包里"
                           f"（{'、'.join(map(str, _缺[:6]))}）"
                           " ⇒ 它们做不做都判「目标完成」，拒发")
        # ★★ R260（2026-11-05）：**先问"有没有实现任务"，再问"有没有清单"**。
        #   真读数（刚量的）：`plan_tasks({name, pre, gates: []})` 返回
        #     tasks=1（只有 pre_emit）、errand=0、acceptance=0、closed=False、uncovered=[]
        #   ⇒ 旧分支顺序把这一档报成"没给出未覆盖清单"，而**真相是压根没有判据门**：
        #     档案写了 goal 却没写 gates ⇒ 计划器编不出任何实现任务。
        #   口径：报错要报**最上游那条原因**，否则读的人会去修一个没坏的东西。
        if not planned.get("tasks"):
            return (False, "父目标不闭合：计划里**一个任务都没有** ⇒ 没有可执行实现，"
                           "拒发（空计划不等于目标已达成）")
        _实现 = [t for t in (planned.get("tasks") or [])
                 if isinstance(t, dict) and t.get("kind") == "errand"]
        if not _实现:
            return (False, "父目标不闭合：计划里**没有任何实现任务（errand）** ⇒ "
                           "档案声明了 goal 却没有可执行的判据门（gates 空），"
                           "计划器编不出实现 ⇒ 拒发。请在档案里写 gates（目标仓自己的验收命令）")
        return (False, "父目标不闭合，但计划器**没给出未覆盖清单**（uncovered 为空）"
                       " ⇒ 无法定位缺口；请检查 plan_tasks 是否带了 closed/uncovered 字段")
    return (True, "")


def _父目标闭合闸(cwd: Path | str) -> str:
    """从目标仓档案里读 `goal` / `plan`，机验父目标闭合；返回拒因（空串 = 放行）。

    ★★★ R259（2026-11-05）：**优先级从"档案里有写"这个偶然事实，改成"档案自己声明的口径"。**

    上一版的漏洞（我立刻发现的）：它只要看到 `plan:` 就要求闭合，
    **而没写 `plan` 的档案一律放行** ⇒ "没声明"变成了一条**绕过纪律的后门**。
    真实档案（如 `_crossrepo_case02/orchestrator.profile.yml`）根本没有 `plan` 字段。

    ⇒ 现在三档，按档案**自己**写的来：
      · 档案写了 `plan`（结构化计划）⇒ 直接用；
      · 否则档案有 `goal` + `acceptance` ⇒ **现场用 `auto_goal.plan_tasks` 机编计划**再验闭合；
      · 两者都没有 ⇒ 档案**没声明目标**（不是"声明了又没闭合"）⇒ 放行，老路径零回退。

    口径：**只要档案声明了"目标要达成什么"，就必须证明实现任务全被验收覆盖**；
      什么都没声明才放行 —— 放行的是"没有目标"，不是"目标没闭合"。
    """
    try:
        根 = Path(cwd)
        for 名 in ("orchestrator.profile.yml", "orchestrator.profile.yaml",
                   ".orchestrator/profile.yml"):
            f = 根 / 名
            if not f.is_file():
                continue
            txt = f.read_text(encoding="utf-8", errors="replace")
            需要 = ("plan:" in txt) or ("goal:" in txt)
            if not 需要:
                return ""
            try:
                import yaml as _y
            except Exception:                      # noqa: BLE001
                return "档案声明了目标/计划，但本机无 yaml 解析器 ⇒ 无法机验闭合，拒跑（不冒充验过）"
            档 = _y.safe_load(txt) or {}
            plan = 档.get("plan")
            if plan is None:
                # ★ 从 `goal` + 档案的 `gates`/`acceptance` 现场编计划（机器计划器，不调模型）
                _g = 档.get("goal")
                if not _g:
                    return ""
                规 = {"name": str(_g)[:80],
                      "pre": list(档.get("pre") or ["工程仓已就位"]),
                      "constraints": list(档.get("constraints") or []),
                      "gates": [dict(x) for x in (档.get("gates") or [])
                                if isinstance(x, dict) and x.get("type")],
                      "acceptance": list(档.get("acceptance") or ["档案声明的判据门全绿"])}
                try:
                    from agentscope_orchestrator import auto_goal as _ag
                    plan = _ag.plan_tasks(规)
                except Exception as _e2:           # noqa: BLE001
                    return (f"档案声明了 goal，但机器计划器编不出合法计划，拒跑"
                            f"（**不冒充验过**）：{type(_e2).__name__}: {_e2}")
            好, 因 = accept_closed(plan if isinstance(plan, dict) else {})
            return "" if 好 else 因
        return ""
    except Exception as _e:                        # noqa: BLE001
        # 闸自己坏了：**拒跑**（宁可不动，也不要在"不知道闭不闭合"的树上开跑）
        return f"父目标闭合闸自身异常，拒跑：{type(_e).__name__}: {_e}"


def _active_count() -> int:
    """在跑任务数 = 仍有存活 runner 的。孤儿 running（死进程遗留）不计。"""
    return sum(1 for r in _runners.values() if not r.done())


def _start_job(job_id: str, resume: dict[int, dict] | None = None) -> None:
    """按 job 记录派发后台执行（提交/续跑/调度共用）。
    kind=engine → 通用任意程序任务（_run_errand，本协程原文保留）；否则 Lean 证明任务。

    ★ 修复（2026-10-12）：**先建任务、成功后才落 running**。
      原实现先 `j["state"]="running"` 落库、再 `asyncio.create_task`；一旦当前线程没有
      运行中的事件循环（FastMCP 把同步工具丢线程池执行时就是如此），create_task 抛
      `RuntimeError: no running event loop`，而 `running` 已经落库 ⇒ 留下**永不回收的
      孤儿 running**：`_active_count()` 只数本进程 _runners、`_queue_order()` 只挑 queued，
      于是它既不被调度也不被回收，下游依赖永久阻塞（实机 jobs.db 已出现多条）。
      现改为 try 建任务 → 成功后才置 running；失败则回滚为 error 并把原因落库。"""
    j = _jobs[job_id]

    def _make_coro():
        # ★ R551：**所有协程必须惰性建**。
        #   原来这里直接 `_有槽开跑(job_id, _run_errand(job_id))`：实参 `_run_errand(...)`
        #   在本行被求值时就建好了。而 `_submit_coro` → `_ensure_loop()` 一旦起不来，
        #   这个内层协程就没人 await ⇒ Python 只留一行
        #       RuntimeWarning: coroutine '_run_errand' was never awaited
        #   **退出码仍是 0、门也抓不住**（本项目最忌的那类：没做成被降级成一行没人看的警告）。
        #   ⇒ 先返回一层空壳，真协程等空壳**开始运行**时才建；空壳不被 await 就什么都不存在。
        async def _惰性(工厂):
            return await 工厂()
        if j.get("kind") == "engine":
            return _惰性(lambda: _有槽开跑(job_id, _run_errand(job_id)))
        task = rt.TaskSpec(id=job_id, unit_id=j["unit_id"], module=j["module"],
                           theorem=j["theorem"],
                           statement_marker=j["statement_marker"],
                           target_rel=j["target_rel"], template=j["template"],
                           min_pass=j["min_pass"], n_windows=j["n_windows"],
                           gates=j.get("gates"))
        task.promote = j.get("promote", False)
        return _惰性(lambda: _有槽开跑(job_id, _run_job(job_id, task, j.get("channel", ""),
                              j.get("dry", False), resume=resume)))

    # ★ R551（calib 复核时抓到）：`_submit_coro` 里 `_ensure_loop()` 一旦起不来，
    #   `_make_coro()` 造出来的协程就**没人消费** ⇒ Python 只报一行
    #       RuntimeWarning: coroutine '_run_errand' was never awaited
    #   而**退出码仍是 0、门也抓不住**。这正是本项目一直在打的那一类：
    #   「没做成」被降级成「一行没人看的警告」。
    #   ⇒ 失败路径显式 close()：把「没跑」变成可见的（下面还有回滚 + 落库 + error 日志）。
    #   ★ 交付后已 consume 的协程不要 close（交给 Task/threadsafe 管）。
    _待跑协程 = None
    try:
        _待跑协程 = _make_coro()
        runner = _submit_coro(_待跑协程)
        _待跑协程 = None                      # 已被 consume，交给 _runners 管
    except RuntimeError as exc:
        if _待跑协程 is not None:
            try:
                _待跑协程.close()
            except Exception:                 # noqa: BLE001 关不掉也不许影响回滚
                pass
        # 连兜底循环都起不来：拒绝派发，回滚状态，绝不留孤儿 running
        j["state"] = "error"
        j["error"] = (f"派发失败（{exc}）：当前上下文无事件循环，任务未启动。"
                      f"请通过 MCP 服务器（异步工具）提交。")
        j.pop("finished_at", None)
        _db_save_one(job_id, j)
        _logger.error("[orchestrator] %s 派发失败：%s", job_id, exc)
        return

    j["state"] = "running"
    j.pop("error", None)
    j.pop("rc", None)
    j.pop("finished_at", None)
    _db_save_one(job_id, j)
    _runners[job_id] = runner


def _reap_orphans() -> int:
    """启动回收：把「本进程无对应 runner 却处于 running」的孤儿任务判为 error。

    ★ 修复（2026-10-12）：死进程遗留的 running 既不被调度也不被回收，会让
      `submit_task` 的依赖门永久等待（依赖判 waiting 而非 failed）。
      启动时统一回收为 error，并在回执里说明，让依赖链能继续推进。"""
    reaped = 0
    for jid, j in _jobs.items():
        if j.get("state") != "running":
            continue
        if jid in _runners:
            continue
        j["state"] = "error"
        j["error"] = "孤儿 running：进程重启后无对应执行体，已回收为 error（防依赖永久阻塞）"
        j["finished_at"] = time.time()
        _db_save_one(jid, j)
        reaped += 1
    if reaped:
        _logger.warning("[orchestrator] 启动回收孤儿 running 任务 %d 条", reaped)
    return reaped


def _queue_order() -> list[str]:
    """当前就绪排队任务，按 (priority 降序, created 升序) 排序的 id 列表。"""
    ready = [jid for jid, j in _jobs.items()
             if j.get("state") == "queued" and _dep_state(jid)[0] == "ready"]
    ready.sort(key=lambda jid: (-int(_jobs[jid].get("priority", 0)),
                                _jobs[jid].get("created", 0)))
    return ready


def _schedule() -> None:
    """每次提交/完成后的调度：把就绪任务按优先级派发，受并发上限约束。"""
    for jid in list(_jobs.keys()):
        j = _jobs[jid]
        if j.get("state") != "queued":
            continue
        st, why = _dep_state(jid)
        if st == "failed":
            j["state"] = "error"
            j["error"] = why
            _db_save_one(jid, j)
    active = _active_count()
    for jid in _queue_order():
        if active >= _CONCURRENCY:
            break
        _start_job(jid)
        active += 1


# ── 工程执行型任务：通用任意程序任务（无"定理/模板/窗口"概念）──
# 任务 = 任意命令(cmd) + 工作目录(cwd) + 任意判据门 GateSpec(gates)。
# 命令本身编成一条 compile 门跑真退出码；额外判据任意加。评估器统一 evaluate。
_跳过目录 = {".git", "target", "node_modules", "__pycache__", ".venv", "dist", "build",
             ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "venv", ".orchestrator"}


def _仓指纹(根: Path, 上限文件: int = 20000) -> dict:
    """仓的**内容指纹**（路径 + 大小 + mtime），用于回答"这次任务有没有动仓"。

    ★ 为什么这比容器更先做（2026-10-30 用户的质问：判据错了提交不了，要容器干嘛）：
      门只判它被要求判的东西。`cargo fix` / 格式化 / 生成脚本把源码改掉，门照样绿——
      **绿得没错，但仓已不是交出去的那个仓**。容器防的是"跑坏了多大范围"，
      而这件事要的是"**有没有偷偷改正在判的东西**"，一条前后比对就够，不需要沙箱。
      容器解决不了判据对错；它也不该被当成判据的一部分。
    """
    项: dict[str, tuple[int, int]] = {}
    try:
        for p in 根.rglob("*"):
            if len(项) >= 上限文件:
                break
            try:
                if not p.is_file() or any(part in _跳过目录
                                          for part in p.relative_to(根).parts):
                    continue
                st = p.stat()
                项[str(p.relative_to(根)).replace("\\", "/")] = (int(st.st_size), int(st.st_mtime))
            except OSError:
                continue
    except OSError:
        return {"可用": False}
    return {"可用": True, "项": 项, "文件数": len(项)}


def _仓改动(前: dict, 后: dict, 样例上限: int = 5) -> dict | None:
    """→ {新增, 修改, 删除, 样例[]}；不可用或零改动返回 None（不制造噪声）。"""
    if not (前 or {}).get("可用") or not (后 or {}).get("可用"):
        return None
    a, b = 前["项"], 后["项"]
    新 = [k for k in b if k not in a]
    删 = [k for k in a if k not in b]
    改 = [k for k in b if k in a and a[k] != b[k]]
    if not (新 or 删 or 改):
        return None
    样例 = ([f"+ {k}" for k in 新[:样例上限]] + [f"- {k}" for k in 删[:样例上限]]
            + [f"~ {k}" for k in 改[:样例上限]])[:样例上限]
    return {"新增": len(新), "修改": len(改), "删除": len(删), "样例": 样例,
            "说明": "任务执行期间仓内容发生了变化。若这是构建产物之外的文件改动，"
                    "说明命令**改了它正在被判的东西** —— 那个绿可能已不代表原始输入。"}


async def _run_errand(job_id: str) -> None:
    """后台执行通用程序任务，结果回写 _jobs[job_id]。"""
    job = _jobs[job_id]
    status: dict = job.get("status") or {}
    job["status"] = status
    cwd = Path(job["cwd"]).resolve()
    try:
        _db_save_one(job_id, job)
        work = rt.WORKDIR
        # ★ 修复（2026-10-12）：空门集/缺门集必须落到默认行为门，绝不 `all({})` 恒真。
        #   原实现直接 evaluate(job["gates"], ...)：若 gates=[]，evaluate 返回 ({}, [])，
        #   `all({}.values())` 恒 True ⇒ **零判据被判 promoted**（"没有判据"冒充"判据全过"）。
        # ★★★★★ 修复 R2-F2（**真缺陷**）：**cmd 永远编成一条真行为门**。
        #   原实现只在 `not spec` 时才把 cmd 编成门 ⇒ 一旦调用方给了**任何**门
        #   （哪怕只是一条 `grep_forbidden`），任务的**本体命令根本没跑**，
        #   只跑了几条旁证门 ⇒ 任务报绿，但**它该做的事一件没做**。
        #   这是最危险的形态：**静默的假绿**。
        #   现改为：cmd 门**无条件**进 spec（去重：已有跑同一 cmd 的 compile 门则不重复跑），
        #   于是"任务本体真的跑了"是**结构保证**，不依赖调用方是否给了门。
        spec = list(job.get("gates") or [])
        _cmd = [str(x) for x in job["cmd"]]
        # ★★★★★ 2026-11-03：把**顶层 `cmd`** 补给"需要一个'当前版命令'却没写"的门。
        #
        #   真事故（**只有经 MCP 通道才暴露**，进程内判据抓不到）：
        #     调用方自然会把主命令写在**顶层** `cmd`（这就是契约），
        #     门里只写它**额外**需要的东西（如 `regression_parity` 的
        #     `baseline_cmd`）。但 `regression_parity` 的实现读的是 `g["cmd"]`
        #     ⇒ 真跑时报：
        #       `未知|regression_parity 需要 baseline_cmd 与 cmd 两条命令`
        #     —— 调用方明明给了主命令，门却说他没给。**契约与实现不对齐。**
        #
        #   为什么在**这里**补而不是让门自己去全局找：
        #     装配层同时看得见"顶层 cmd"和"门列表"，是天然的对齐点；
        #     门自己去猜（翻门集、翻全局）会引入隐式耦合，且越权。
        #   ⇒ 与"顶层 cmd 永远被编成一条 cmd_exit_ok 门"是同一处职责：
        #     **装配层负责让门拿到它该拿到的东西。**
        #
        #   只补**缺省**（`setdefault` 语义）：调用方显式给了 `cmd` 就用它自己的
        #   —— 允许"当前版命令 ≠ 顶层命令"（如只对某一路做对照）。
        for _g in spec:
            if (isinstance(_g, dict)
                    and str(_g.get("type")) == "regression_parity"
                    and not _g.get("cmd")):
                _g["cmd"] = list(job["cmd"])
        # ★ 2026-10-31：`test_counts` 同样跑的是这条 cmd（还自带退出码校验）⇒
        #   也算"已含"，否则测试套件会被**跑两遍**（一遍计数门、一遍 cmd_exit_ok）。
        _已含 = any(str(g.get("type")) in ("compile", "test_counts")
                    and [str(x) for x in (g.get("cmd") or [])] == _cmd
                    for g in spec)
        if not _已含:
            spec = [{"name": "cmd_exit_ok", "type": "compile",
                     "cmd": list(job["cmd"]), "cwd": str(cwd), "expect_exit": 0}] + spec
        job["gates"] = spec
        job["cmd_ran"] = True
        job["cmd_gate"] = str((spec[0] or {}).get("name") or "cmd_exit_ok")
        _改前 = _仓指纹(cwd)
        results, audit = await rt.evaluate_async(spec, cwd, work, str(cwd), file_root=cwd)
        try:
            _动 = _仓改动(_改前, _仓指纹(cwd))
            if _动:
                job["仓改动"] = _动
        except Exception:                # noqa: BLE001 比对失败绝不影响判决
            pass
        # rc：compile 门真退出码（若有），否则 -1
        #
        # ★★★★★ 2026-11-03 真事故（**只有端到端经 MCP 才抓得到**）：
        #   原实现 `a.get("rc", -1) for a in audit if "rc" in a` 有两个坑叠在一起：
        #     · `if "rc" in a` 只判**键在不在**，不判值；
        #     · `a.get("rc", -1)` 的默认值**只在键缺失时生效** ——
        #       键在而值是 `None` 时它照样返回 `None`；
        #   ⇒ `max((None, ...))` 抛
        #     `TypeError: '>' not supported between instances of 'NoneType' and 'int'`
        #   ⇒ 整个 `orch_run` 以 `error` 收场、`门数.实得=0`，
        #     **看起来像新门根本没跑**，实际是聚合层把合法的 `rc=None` 当数字用。
        #
        #   为什么进程内判据没抓到这个：判据直接调 `gate_spec._run_one`，
        #   **不经过本层聚合**。教训已在 `gate_premises.py` 落成一条硬要求：
        #   新门必须**经 MCP 通道**端到端跑一次，进程内绿不算数。
        #
        #   语义（钉死）：`rc=None` 是**合法**的 —— 它表示"这个门不跑命令"
        #   （`precondition` / `instance_agreement` / `regression_parity` 都可能这样）。
        #   所以修的是**聚合层容忍它**，而不是逼门去编一个假退出码。
        rc = max((a["rc"] for a in audit if isinstance(a.get("rc"), int)), default=-1)
        job["rc"] = rc
        # ★★★★★ R14：走**数术层格裁决**（TS-468 §6）。取值与 `all` 一致（无回退），
        #   但把**因由**带回回执 —— 调用方与模型因此知道"是哪个门红的、为什么红"，
        #   而不是只拿到一个 Bool 盲改。
        from agentscope_orchestrator import shushu_logic as _sl
        _值, _因由, _注 = _sl.从审计(results, audit)
        ok = bool(_值)
        status.update({"state": "promoted" if ok else "convergence_failed",
                       "passed": 1 if ok else 0, "failed": 0 if ok else 1,
                       "gates": results, "audit": audit,
                       "裁决因由": _因由, "裁决注": _注,
                       "final_gates": results})
        # ★★★ 差分验收（实例级隔离）：**绿要交集**
        #   只在主实例绿时才做 —— 红已经有真证据，再乘实例数只是烧钱；
        #   而"绿"恰恰是最容易假的那一侧（本会话两次假绿都是环境造成的）。
        #   结论不同 ⇒ state=instance_diverged（**不是** convergence_failed），
        #   分类由 _分类 读 `差分分歧` 得到「实例分歧」，绝不折成"未达标"。
        _实例数 = int(job.get("instances") or 1)
        # ★ windows>1 时**必须**补采：只看第一次的成败，一次抖动就判死，
        #   多窗就成了摆设。抖动用票数吸收，这条前提是"票要采够"。
        if (ok and _实例数 > 1) or int(job.get("windows") or 1) > 1:
            差分 = await _差分验收(spec, cwd, work, _实例数, ok,
                                   模式=str(job.get("instances_mode") or "env"),
                                   窗数=int(job.get("windows") or 1),
                                   须过=int(job.get("min_pass") or 1))
            job["差分"] = 差分
            if 差分.get("分歧"):
                job["差分分歧"] = True
                job["state"] = "instance_diverged"
                status["state"] = "instance_diverged"
                status["裁决注"] = "实例分歧：主实例绿、异构实例红 ⇒ 环境耦合，不是任务失败"
            elif 差分.get("全红"):
                job["state"] = "convergence_failed"
                status["state"] = "convergence_failed"
                status["裁决注"] = "所有实例都红 ⇒ 判据一致，判红"
        # ★ 双向纠偏：红的时候先问一句"这红是任务书造成的吗"
        if not ok:
            驳 = _判驳回(status)
            if 驳:
                job["驳回"] = _纠偏留痕(job, 驳)
                status["state"] = "task_rejected"
                status["裁决注"] = f"{驳['COR']}：任务书缺陷，非产物缺陷（{驳['说明']}）"
        job["state"] = status.get("state") or job["state"]
        job["finished_at"] = time.time()
    except asyncio.CancelledError:
        job["state"] = "cancelled"
        job["error"] = "cancelled by user"
    except Exception as exc:
        job["state"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _db_save_one(job_id, job)
        _schedule()


def _archive_windows(job_id: str, status: dict) -> list[dict]:
    """任务终态：把各窗推理链（模型产稿全文）归档为可检索文档，只留索引不进会话。

    解决 DSH 卡死：超长推理链输出不再靠聊天自动加载；聊天侧用
    search_docs(kind='chain') 发现、read_doc(doc_id) 按需拉取。"""
    windows = status.get("windows") or []
    texts = {f"w{w['idx']}": w.get("text", "") for w in windows}
    if not texts:
        return []
    return ds.archive_chain(f"{job_id}_chain",
                            f"{job_id} · 各窗推理链", texts,
                            meta={"task_id": job_id})


async def _run_job(job_id: str, task: rt.TaskSpec, channel: str, dry: bool,
                   resume: dict[int, dict] | None = None) -> None:
    """后台执行管线，结果回写 _jobs[job_id]。
    resume 为断点续跑复用窗（子任务幂等）。"""
    job = _jobs[job_id]
    old_status = job.get("status") or {}
    status: dict = dict(old_status)
    job["status"] = status
    try:
        _db_save_one(job_id, job)
        rc = await rt.run_task(task, task.n_windows, channel,
                               task.promote, dry, status=status,
                               resume_windows=resume)
        if resume:
            # 合并续跑窗的旧判据门/审计，保证全量可回放
            gates = {g["idx"]: g for g in old_status.get("gates", [])}
            gates.update({g["idx"]: g for g in status.get("gates", [])})
            audit = {a["idx"]: a for a in old_status.get("audit", [])}
            audit.update({a["idx"]: a for a in status.get("audit", [])})
            status["gates"] = [gates[i] for i in sorted(gates)]
            status["audit"] = [audit[i] for i in sorted(audit)]
        # 推理链文档化：模型产稿全文落盘+索引（不进 DSH 会话，防卡死）
        chain_docs = _archive_windows(job_id, status)
        if chain_docs:
            status["doc_ids"] = chain_docs
            _logger.info("[orchestrator] %s 归档推理链文档 %d 篇（doc_store）",
                         job_id, len(chain_docs))
        job["rc"] = rc
        job["state"] = status.get("state", job.get("state"))
        job["finished_at"] = time.time()
    except asyncio.CancelledError:
        job["state"] = "cancelled"
        job["error"] = "cancelled by user"
    except Exception as exc:  # 诚实边界：机制只保证结构性；运行异常照实报
        job["state"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _render_job_receipt(job_id, status)   # MCP 任务终态渲染人类可读 .md 回执
        _db_save_one(job_id, job)
        _schedule()  # G5：任务结束即触发一次调度，放行/改判排队依赖任务


def _render_job_receipt(job_id: str, status: dict) -> None:
    """MCP 主导任务终态渲染 TS-XXX_回执_*.md 到 .audit_trail/（人类可读进度）。

    此前 MCP 任务只写 jobs.db（仅控制台可查），DSH 聊天侧看不到 .md 回执；
    现按 ts_receipt 模板渲染，与 engine_dev/develop 引擎回执同一批号源，供人类直接
    打开看进度/判据/产物。渲染失败不阻断任务落库（诚实边界）。"""
    try:
        prof = prof_mod.get_profile()
        state = status.get("state", "unknown")
        batch = ts_receipt.claim_batch(prof)
        gates = status.get("gates") or []
        passed = [g["idx"] for g in gates if g.get("passed")]
        summary = (
            f"任务 {job_id} 终态 [{state}]；通过窗 {len(passed)}/{len(gates)}；"
            f"min_pass={status.get('min_pass', '?')}"
            if gates else f"任务 {job_id} 终态 [{state}]（无窗记录）")
        product = status.get("product")
        ts_receipt.render(
            repo_root=prof, batch=batch,
            window=job_id, nature="执行回执（MCP 多智能体任务 · 判据门机器审计）",
            topic=f"{job_id}", summary=summary,
            gates={f"w{g['idx']} 通过": bool(g.get("passed")) for g in gates},
            product=product or status.get("target_rel", ""),
            notes=[f"audit 全量见只读控制台 {_CONSOLE}/task/{job_id}",
                   f"推理链文档：{status.get('doc_ids', [])}"] if status.get("doc_ids") else [
                       f"audit 全量见只读控制台 {_CONSOLE}/task/{job_id}"])
    except Exception as exc:  # noqa: BLE001 回执渲染失败不阻断任务落库
        _logger.warning("[orchestrator] 任务 %s 回执渲染失败：%s", job_id, exc)


@mcp.tool(name=_TOOL("run_errand"))
def run_errand(unit_id: str, cmd: list[str], cwd: str, gates: list[dict],
               name: str = "engine", priority: int = 0,
               depends_on: list[str] | None = None) -> str:
    """提交一个通用任意程序任务（工程执行型，无"定理/模板/窗口"概念）。

    unit_id 锚定档案声明的真实源码单元（源码根相对点分键，如 <root>.某模块 或裸模块名），
    锚不到 → 拒跑，防多智能体把任务派到不存在的假设文件。
    cmd 是任意命令（如 cargo build / cargo test / python 脚本）；cwd 是命令工作目录
    （默认锚定工具根）。

    ★ **测试类命令自动挂 `test_counts` 门**（不是退出码门）：解析 passed/failed/skipped/
      ignored，`passed < min_passed(1)` 或 `skipped > max_skipped(0)` 即红 ⇒
      **`@skip`/`OK (skipped=1)`/`[ignore]` 都不能算绿**。认出 `pytest/py.test/unittest/
      cargo test/go test/jest/vitest/mocha/npm test/mvn test/gradle test/dotnet test/
      ctest/rspec/phpunit/tox/nox/mix test/swift test` 等；其余命令仍挂退出码门。
      要放宽自己传一条 `{"type":"test_counts","min_passed":0,"max_skipped":99}`。

    ★ gates **可以为空**：`cmd` 自己**永远**被编成一条 `cmd_exit_ok` compile 门
      （跑真退出码），所以「跑这条命令，告诉我它成不成」是最省事的用法，
      `gates` 传 `[]` 即可 —— **不需要为了过闸写一条多余的门**。
      要更强的判据再额外加：grep_forbidden / grep_required / scan_output /
      whole_reading / axiom_footprint / kernel_statement / 再一条 compile。
    全部用 G6 语言无关评估器 evaluate 现场执行，机制本体与语言无关。

    G5：priority + depends_on 同 submit_task。**依赖只认 `promoted`**：
    上游只 `staged`（产物落 products/ 未进主树）时**不放行下游**，
    因为下游会在旧树上开跑（非存在前提）。
    产物按全部门通过才 promoted，回执进 .audit_trail / 只读控制台。"""
    global _seq
    if not cmd:
        return json.dumps({"ok": False, "error": "cmd 为空，拒跑"}, ensure_ascii=False)
    unit = _anchor_engine(unit_id)
    if unit is None:
        return json.dumps({"ok": False,
                           "error": f"工程锚定失败：unit_id={unit_id} 在 Rust 引擎索引内不存在，拒跑"},
                          ensure_ascii=False)
    cwd_res = Path(cwd or _RUST_ROOT)
    if not cwd_res.is_dir():
        cwd_res = _RUST_ROOT
    # ★★★★★ 修复 R9（**判据集错位**）：校验对象必须是**合成后**的门集，不是调用方传的。
    #
    #   原实现：`gate_spec_validate(gates)` —— 只看调用方传的门。
    #   `gates` 为空 ⇒ 直接拒跑（"门集为空：零个机器判据 ⇒ 无裁决依据"）。
    #
    #   ★ 为什么这是错的：本工具**永远**会把 `cmd` 自己编成一条 `cmd_exit_ok`
    #     compile 门（下面 `_全门`，且 `_run_errand` 里还会再兜一层）
    #     ⇒ **门集从来不空**，裁决依据一直存在（命令的真退出码）。
    #   ★ 后果：最常用的通用工程任务 ——「**跑这条命令，告诉我它成不成**」
    #     （cargo build / cargo test / 任意脚本）—— 被**无理由拒绝**，
    #     逼调用方为了过闸**再写一条多余的假门**。这是**逼人造假门**。
    #   ★ 修法：先合成 `_全门`（cmd 门 + 调用方门），**校验合成后的集合**。
    #     "空门集不得判全绿"这条不变量**照旧成立**（合成后恒 ≥1 条真门），
    #     只是不再拿"调用方没写额外门"当成"没有裁决依据"。
    _全门 = [{"name": "cmd_exit_ok", "type": "compile", "cmd": list(cmd),
              "cwd": str(cwd_res), "expect_exit": 0}] + list(gates or [])
    # ★ 2026-11-03：与 `orch_run` 同一处职责 —— 把**顶层 cmd** 补给
    #   "需要一个'当前版命令'却没写"的门（目前是 `regression_parity`）。
    #   不补的话真跑时报 `未知|regression_parity 需要 baseline_cmd 与 cmd 两条命令`，
    #   而调用方明明给了主命令 —— 契约与实现不对齐，且只有端到端才暴露。
    for _g in _全门:
        if (isinstance(_g, dict)
                and str(_g.get("type")) == "regression_parity"
                and not _g.get("cmd")):
            _g["cmd"] = list(cmd)
    bad_gates = rt.gate_spec_validate(_全门)
    if bad_gates:
        return json.dumps({"ok": False, "error": bad_gates}, ensure_ascii=False)
    # ★★★★★ 修复 R2-F4：**MCP 入口的命令安全闸**。
    #   此前 `check_cmd_safety` 只在 `gate_derive` 内部被调 ⇒ 从这里进来的
    #   任意可执行 / 越界 cwd **完全不过闸**。现在本任务的 `cmd` 自己也编成
    #   一条门，**一起过闸**（不能只查 gates 而放过 cmd）。
    #   允许根 = 工具工作区 + 主树根（`{repo}` 工程任务用）。
    _safe, _vio = check_cmd_safety(_全门, rt.WORKDIR, repo_root=rt.REPO_ROOT)
    if not _safe:
        return json.dumps({"ok": False,
                           "error": "命令安全闸拒绝：" + "；".join(_vio[:4]),
                           "violations": _vio}, ensure_ascii=False)
    # ★★★★★ R257（2026-11-05 · 父目标闭合闸**真正接进提交路径**）：
    #   上一轮我加了 `accept_closed()`，但**没有调用点** —— 那仍然只是"打印了一份真相"。
    #   现在接在**提交这一刻**：`orchestrator.profile.yml`（或档案里的目标规约）若声明了
    #   `plan`（= auto_goal.plan_tasks 的结果），本函数**先机验父目标闭合**，不闭合直接拒跑。
    #   ★ 零回退：没声明 `plan` 的目标**完全不受影响**（老路径照旧），
    #     但一旦声明了，就**必须**闭合 —— 声明了纪律却允许绕过，等于没有纪律。
    _闭拒 = _父目标闭合闸(cwd_res)
    if _闭拒:
        return json.dumps({"ok": False, "error": _闭拒, "refuse": "parent_goal_not_closed"},
                          ensure_ascii=False)
    prio = max(0, int(priority) if isinstance(priority, int) else 0)
    deps = list(depends_on or [])
    for d in deps:
        if d not in _jobs:
            return json.dumps({"ok": False,
                               "error": f"依赖任务 {d} 不存在，拒跑"},
                              ensure_ascii=False)
    job_id = _next_id()
    _jobs[job_id] = {"id": job_id, "created": time.time(), "kind": "engine",
                     "unit_id": unit_id, "target_rel": name, "cmd": cmd,
                     "cwd": str(cwd_res), "gates": _全门,
                     "priority": prio, "depends_on": deps,
                     "state": "queued", "dry": False}
    _db_save_one(job_id, _jobs[job_id])
    _schedule()
    if _jobs[job_id].get("state") == "error":
        return json.dumps({"ok": True, "task_id": job_id, "unit": str(unit),
                           "state": "error", "error": _jobs[job_id].get("error")},
                          ensure_ascii=False)
    if _jobs[job_id].get("state") == "queued":
        return json.dumps({"ok": True, "task_id": job_id, "unit": str(unit),
                           "state": "queued", "queued": True},
                          ensure_ascii=False)
    return json.dumps({"ok": True, "task_id": job_id, "unit": str(unit),
                       "state": "running"}, ensure_ascii=False)


@mcp.tool(name=_TOOL("submit_task"))
def submit_task(unit_id: str, module: str, theorem: str,
                statement_marker: str, target_rel: str,
                template: str, n_windows: int = 3, min_pass: int = 2,
                promote: bool = False, channel: str = "",
                dry: bool = False, priority: int = 0,
                depends_on: list[str] | None = None,
                gates: list[dict] | None = None) -> str:
    """提交一个形式化收口任务：N 窗并发生成收口件 → 机器判据门 → 收敛裁决。

    必锚定产物库内真实单元（unit_id，如 <key_prefix>.A.B.C），
    锚不到 → 拒跑。template 是含 `by sorry` 占位的模块模板（陈述已定死）。

    G5：priority + depends_on（依赖全成功放行，失败则判 error）。

    ★ `min_pass` 数的是**可及数**（互不相同的产物），**不是窗数**：产物逐字相同的窗
      只算一条路径（TS-468 定的口径，重复窗如实记在回报的 `重复窗` 里）。
      实测后果：3 窗全绿而其中两窗产物逐字相同 ⇒ `可及=2`，`min_pass=3` 下判
      `convergence_failed`。**要 3 条独立路径就设 min_pass=3 且给模型留出差异空间；
      只是"跑够几窗"则应设 min_pass ≤ 预期相异产物数。**

    【通用判据门】gates：可选，声明式 GateSpec。不传 = Lean 原生四条；
    传了 = 按本任务规格现场生成专属闭环（机制本体语言无关）。"""
    global _seq
    # 闸 ① 方向锚定
    unit = _anchor_unit(unit_id)
    if unit is None:
        return json.dumps({"ok": False,
                           "error": f"方向锚定失败：unit_id={unit_id} 在产物库内不存在，拒跑"}, ensure_ascii=False)
    # 闸 ② 模板完整性
    task = rt.TaskSpec(id=_next_id(), unit_id=unit_id, module=module,
                       theorem=theorem, statement_marker=statement_marker,
                       target_rel=target_rel, template=template,
                       min_pass=min_pass, n_windows=n_windows)
    task.promote = promote  # 运行时参数
    if not (1 <= n_windows <= 5):
        return json.dumps({"ok": False, "error": "n_windows 须在 1..5"}, ensure_ascii=False)
    if not (1 <= min_pass <= n_windows):
        return json.dumps({"ok": False, "error": "min_pass 须在 1..n_windows"}, ensure_ascii=False)
    bad = _validate_template(task)
    if bad:
        return json.dumps({"ok": False, "error": bad}, ensure_ascii=False)
    bad_gates = rt.gate_spec_validate(gates)
    if bad_gates:
        return json.dumps({"ok": False, "error": bad_gates}, ensure_ascii=False)
    # ★ 修复 R2-F4：显式传入的判据门同样必须过命令安全闸。
    if gates:
        _safe, _vio = check_cmd_safety(list(gates), rt.WORKDIR,
                                       repo_root=rt.REPO_ROOT)
        if not _safe:
            return json.dumps({"ok": False,
                               "error": "命令安全闸拒绝：" + "；".join(_vio[:4]),
                               "violations": _vio}, ensure_ascii=False)
    prio = max(0, int(priority) if isinstance(priority, int) else 0)
    deps = list(depends_on or [])
    # G5 依赖门：依赖任务必须已知存在
    for d in deps:
        if d not in _jobs:
            return json.dumps({"ok": False,
                               "error": f"依赖任务 {d} 不存在，无法建立依赖链，拒跑"},
                              ensure_ascii=False)

    job_id = task.id
    _jobs[job_id] = {"id": job_id, "created": time.time(), "unit_id": unit_id,
                     "module": module, "theorem": theorem,
                     "statement_marker": statement_marker,
                     "target_rel": target_rel, "template": template,
                     "n_windows": n_windows, "min_pass": min_pass,
                     "promote": promote, "priority": prio,
                     "depends_on": deps,
                     "gates": list(gates) if gates else None,
                     "state": "queued", "channel": channel, "dry": dry}
    _db_save_one(job_id, _jobs[job_id])
    _schedule()  # 提交即调度：就绪则按并发上限+优先级放行，否则排队
    st, why = _dep_state(job_id)
    if _jobs[job_id].get("state") == "error":
        return json.dumps({"ok": True, "task_id": job_id, "unit": str(unit),
                           "state": "error", "error": why}, ensure_ascii=False)
    if _jobs[job_id].get("state") == "queued":
        return json.dumps({"ok": True, "task_id": job_id, "unit": str(unit),
                           "state": "queued", "queued": True,
                           "reason": why or "排队中（可能受并发上限）"},
                          ensure_ascii=False)
    return json.dumps({"ok": True, "task_id": job_id, "unit": str(unit),
                       "state": "running"}, ensure_ascii=False)


@mcp.tool(name=_TOOL("orch_fix"))
async def orch_fix(repo: str, cmd: list[str], gates: list[dict] | None = None,
                   rounds: int = 2, channel: str = "",
                   timeout_s: int = 1200, max_chars: int = 200_000) -> str:
    """★★【会改代码的入口】红了就让模型改，**机器门复跑裁决**，绿保红回滚。

    和 `orch_run` 的分工：`orch_run` 只判（跑命令、给判决）；`orch_fix` **判 + 修**。
    两者共用同一套判据门 —— 修出来的东西必须过同一道门才算数，模型不给自己发合格证。

    结构（这是"谁判"的关键，不是实现细节）：
      跑门 → 红 ⇒ 打包红门证据 + 相关文件 → 派**真模型**产完整文件内容
      → 备份 → 原子写 → **机器门复跑** → 绿=保留 / 红=**整批回滚** → 下一轮。

    repo/cmd/gates: 同 `orch_run`（`cmd` 永远是一条真行为门）。
    rounds: 最多修几轮（默认 2）。每轮改动全失败就回滚，**仓保持原状**。
    channel: 通道名；空=全通道轮转（单通道故障可降级）。

    返回：分类 + 门（每轮复跑结果）+ 修复记录（模型说明/改了哪些文件/是否保留/备份路径）
    + 成本。**修不动也如实说**：`未达标` 会明确写"每轮改动均已回滚，仓保持原状"。

    ★ 能做什么：**新建文件/目录、改写、删除、重命名**——新项目和重构都支持。
      回滚不靠「禁止操作」，靠**全仓快照 + 红则全量还原**（新建的删掉、
      删除的恢复、改名/改写的还原）。
    ★ 硬边界（如实）：不出仓、不碰 .git、不许改**判据环境文件**
      （`sitecustomize.py`/`usercustomize.py`/`*.pth`——实测中模型真的试图
      用它们骗过门）；改 `conftest.py`/`pytest.ini` 会记进 `门环境改动` 警示，
      因为那种绿可能是「把测试跳过」换来的。
    ★ 无沙箱：模型仍可能写出**语义错但能过门**的代码 —— 那时问题是「门太弱」。
    """
    from agentscope_orchestrator import fixloop as _fl
    # ★ 预算闸（烧钱的主因是"修不动还一直修"）：
    #   `max_chars` = 提示+回复的**累计字符**上限。超了就在**轮边界**停下并如实报
    #   `停止原因=字符预算用尽`，而不是跑满轮数再告诉调用方"花了这么多"。
    #   为了能在轮间检查，修复按**一轮一调**执行（门每轮本来就要复跑，代价可接受）。
    async def _带预算() -> dict:
        记录: list[dict] = []
        已用 = 0
        已跑轮 = 0
        末: dict = {}
        while 已跑轮 < max(1, int(rounds or 1)):
            if max_chars and 已用 >= max_chars:
                break
            单 = await _fl.修复(repo, cmd, gates, 1, channel)
            已跑轮 += 1
            for r in (单.get("修复记录") or []):
                已用 += int(r.get("提示字节") or 0) + int(r.get("回复字节") or 0)
            记录.extend(单.get("修复记录") or [])
            末 = 单
            if 单.get("分类") in ("达标", "基础设施不可用", "参数/前提不成立",
                                 "任务书驳回", "取消"):
                break
        出 = dict(末 or {"分类": "未达标"})
        出["修复记录"] = 记录 or (末.get("修复记录") or [])
        出["预算"] = {"字符上限": int(max_chars or 0), "已用字符": 已用,
                     "轮数上限": max(1, int(rounds or 1)), "已跑轮数": 已跑轮}
        if max_chars and 已用 >= max_chars and 出.get("分类") != "达标":
            出["预算"]["停止原因"] = "字符预算用尽（不是修不动，是预算到顶）"
        return 出
    try:
        _结果 = await asyncio.wait_for(_带预算(), timeout=timeout_s + 60)
        try:                                   # 检查点：断线后可回读"走到哪一步"
            from agentscope_orchestrator import agentface as _af
            _结果["留痕"] = _af.落检查点(
                f"fix_{int(__import__('time').time())}", {
                    "分类": _结果.get("分类"), "预算": _结果.get("预算"),
                    "门": _结果.get("门"),
                    "下一步": "按『修法』执行；预算到顶就先 orch_run 只判不修",
                    "续跑": {"工具": "orch_fix",
                            "参数": {"repo": repo, "cmd": list(cmd),
                                    "rounds": max(1, int(rounds or 1))}}})
        except Exception:                      # noqa: BLE001
            pass
        return json.dumps(_结果, ensure_ascii=False, default=str)
    except asyncio.TimeoutError:
        return json.dumps({"ok": False, "分类": "进行中",
                           "error": f"超过 {timeout_s}s 仍在修；任务未杀，用 orch_wait 或提高 timeout_s",
                           "修法": {"工具": "orch_fix",
                                    "参数": {"repo": repo, "cmd": list(cmd),
                                             "timeout_s": timeout_s * 2}}},
                          ensure_ascii=False)
    except Exception as ex:                      # noqa: BLE001
        return json.dumps({"ok": False, "分类": "基础设施不可用",
                           "error": f"{type(ex).__name__}: {ex}",
                           "修法": {"工具": "run_errand",
                                    "参数": {"unit_id": "any", "cmd": ["python", "-c", "print(1)"],
                                             "cwd": repo, "gates": []}}},
                          ensure_ascii=False)


@mcp.tool(name=_TOOL("context"))
async def taixu_context(场景: str = "", 引用: str = "", 关键词: str = "",
                        要什么: str = "", 上限: int = 2000) -> str:
    """★【动手前先问一句】**一次调用**拿到这个项目里跟当前这件事**有关联的全部事实**
    —— 项目口径、历史坐标、四方联动、已知盲区、机器步数。

    why: 长线工程里最容易出的事不是"不会写"，是**按训练数据里的通用做法写**，
    而**这个项目本来不是那么办的**（单位口径、禁则、术语、已否决的方案…）。
    这些事实散在六个地方，一个一个查要 5 次调用、拿到 5 块割裂的返回，
    AI 索性就不查了 —— 于是每次都从训练惯性出发。

    ⇒ 所以做成**一次给全、按关联排好**：口径排最前（它决定"该怎么做"），
      历史/引用其次（以前怎么做的），盲区带上「该加门」的预警（别重犯）。
      **返回正文有上限**（默认 2000 字，可按 `上限` 调），超了会明说"不是全部"。

    when:
      · 拿到一个改动点、还没动手时 —— 先看这个项目本来怎么办
      · 遇到"门判不了"的模糊处 —— 看盲区台账里有没有同类
      · 想知道这事牵动了谁 —— 看引用与历史

    参数：
      · `场景`  一句话说你在干什么（如"要改 plot 里的刻度输出"）——**这个最常用**
      · `引用`  具体文件/条款（有就给，能查到历史与引用关系）
      · `关键词` 单独指定检索词（不填从 `场景` 抽）
      · `要什么` 只要某几段（逗号分隔：`口径,引用,盲区,事件,外部`）——
                **省上下文的第二个手段**：只想知道口径就只问口径
      · `上限`  返回正文的字数上限

    ★ 诚实边界：它**只做关联与裁剪，不产生事实** —— 每条都能在原模块里查到；
      若与源头不一致，错的是它。「沾边」与「命中」是分开标的，
      **沾边不会被装成结论**。`要什么` 里的段名写错时**那一段静默不给**（不报错）。
    """
    from agentscope_orchestrator import context_view as _CV
    try:
        体 = _CV.看(场景, 引用=引用, 关键词=关键词, 要什么=要什么,
                    上限=max(200, min(8000, int(上限 or 2000))))
    except Exception as e:                       # noqa: BLE001 工具绝不因自身抛给调用方
        return f"（语义入口自身出错：{type(e).__name__}: {e}）⇒ 这一步请按项目文档自行确认"
    if not 体.get("正文"):
        return ("这个项目里**没有查到与它相关的已积累事实**。\n"
                "⇒ 那就按你的判断做，但**请说清你是按哪条项目事实下的结论**；"
                "如果一条都举不出来，说明这是**项目知识的一个空缺**（该记进盲区台账）。")
    return 体["正文"]


@mcp.tool(name=_TOOL("guide"))
async def taixu_guide() -> str:
    """★【新智能体第一步】一次调用拿到**使用契约**：什么时候用哪个工具、分类怎么读、
    失败怎么自救、哪些事它做不到。**不知道从哪开始时先调它**（载荷 < 5KB，1 次往返）。

    存在的理由：工具说明散在 18 个函数的 docstring 里，新智能体要花十几轮才摸清；
    而"能不能减少对话轮次"正是这层的验收标准。所以把契约做成**可机读的一次调用**。
    """
    try:
        from importlib.metadata import version as _v
        版本 = _v("agentscope-orchestrator")
    except Exception:                        # noqa: BLE001
        版本 = "0.1.0"
    返回 = {
        "版本": 版本,
        "定位": "AI 可用（不是人类可用）的工程任务编排层：1 次调用交任务、拿判决、拿修法；"
                "`orch_fix` 还能让模型改代码并由机器门裁决（绿保红滚）。"
                "没有给人看的仪表盘——调用方就是智能体本身。",
        "首选": {"工具": "orch_run",
                 "何时": "要让某个命令在某个仓里跑并**立刻知道成不成**（构建/测试/校验/脚本）",
                 "最小参数": {"repo": "<仓绝对路径>", "cmd": ["<命令>", "<参数>"]},
                 "要点": "wait=True 默认等终态；cmd 自己永远被编成一条 cmd_exit_ok 门"},
        "工具表": [
            {"工具": "orch_run", "何时": "只判：跑命令 → 判决（默认入口，1 次往返）"},
            {"工具": "orch_fix", "何时": "判 + 修：红了让模型改、机器门复跑、绿保红回滚"},
            {"工具": "orch_wait", "何时": "已提交但没等到终态（返回『进行中』『未到终态』时）"},
            {"工具": "run_errand", "何时": "需要异步/自定义门集，不阻塞调用方"},
            {"工具": "submit_task", "何时": "形式化收口（Lean 定理，N 窗并发生成）"},
            {"工具": "judge_result", "何时": "要看逐窗逐门证据的细节"},
            {"工具": "read_report", "何时": "要产物/报告全文（大载荷不塞进对话）"},
            {"工具": "list_tasks", "何时": "盘清当前所有任务及状态"},
            {"工具": "resume_task", "何时": "任务已终止（error/收敛失败/取消）要断点续跑"},
            {"工具": "cancel_task", "何时": "不要再跑了"},
            {"工具": "orch_calibrate", "何时": "自证判决准不准（真值语料，不调模型）"},
            {"工具": "orch_object", "何时": "**怀疑绿是假的**时：现状跑一遍 + 破坏副本再跑一遍，"
                                        "给你「这条判据抓不抓得住单点破坏」的双读数（入册留痕）"},
            {"工具": "orch_history", "何时": "查历史：这台编排器做过什么、结论是什么、学到什么（不调模型）"},
            {"工具": "kb", "何时": "**项目口径要外挂**（单位/精度/禁则/术语）：人写一条、机器判一条；"
                                    "`kb(action='门', repo=...)` 的门塞进 orch_run 的 gates 才算硬约束"},
            {"工具": "gate_power", "何时": "量**门的区分力**：注机械变异看门抓住几种（区分力低就该加门族）"},
            {"工具": "orch_run（测试命令时）", "何时": "自动挂 test_counts 门：测试被 skip 光 = 红，不是绿"},
            {"工具": "guide", "何时": "就是它自己：忘了契约时随时再调"},
        ],
        "症状路由": {
            "命令跑完不知道成不成": "orch_run（cmd 自己就是一条退出码门）",
            "红了但不知道谁的责任": "读回执『责任归属』：调用方侧=参数错 / 执行侧=产物错 / 环境=通道或依赖",
            "绿了但我不信": "orch_object（破坏副本测区分力）",
            "门给不出结论（未知）": "未知 ≠ 未达标：换 oracle 判据或缩范围，不要拿去『修』",
            "测试跳光了还绿": "默认门已挂 test_counts（max_skipped=0）；可再传自定义",
            "同一个任务跑两次结论不同": "instances=2 + instances_mode=copy（抓伪绿/环境耦合）",
            "会话断了要接上": "resume_task（断点续跑，已过窗不重跑）；回执里有『留痕』路径可回读",
            "想知道我做过什么": "orch_history（不调模型）或读 ORCH_STATE_DIR/receipts/*.json",
            "项目口径（单位/精度/禁则）老被模型先验带偏": "kb：写一条规则 → kb(action='门') → 塞进 orch_run 的 gates",
            "验收人改了口径，怎么让它立刻算数": "kb(action='改', id=...) —— 版本自增、变更留痕，门跟着变，不重训不重启",
            "外部知识改了但不确定机器认得": "kb(action='冲突') 看有没有『必须 X 与禁止 X 同文』；kb(action='统计') 看可机判门数",
            "工具太多不知从哪开始": "就是 guide 自己",
        },
        "历史查询": {
            "回执留痕": "<ORCH_STATE_DIR>/receipts/*.json（每次 orch_object 等都会落盘）",
            "判据册": "<ORCH_STATE_DIR>/判据册.jsonl（双验通过后的判据累积，跨会话可读）",
            "任务历史": "orch_history / list_tasks",
        },
        "分类字典": {
            "达标": "成了，用产物，别复查",
            "未达标": "产物不合格：按『修法』改后复跑（不要重试同一条命令）",
            "参数/前提不成立": "调用方写错了（repo/cmd/unit_id/gates），看 error 改参数",
            "基础设施不可用": "环境问题（熔断/端点死/无 key）：**别改产物**，等冷却或换通道",
            "实例分歧": "环境耦合或伪绿：看 差分.实例[].模式（copy ⇒ 多半是绿依赖既有产物）",
            "任务书驳回": "任务书/门规格不可机判，改任务书不是改产物",
            "进行中": "未到终态**不是失败**：orch_wait 继续等",
            "取消": "被显式取消，不判成败",
            "未到终态": "wait 超时但仍在跑：orch_wait 再等，别重复提交",
        },
        "自纠契约": "任何非达标返回都带 修法={工具,参数}：按它调下一个工具即可，无需读源码。"
                    "修法=null 才是需要人判断的。",
        "全量验收": "TAIXU_GUIDE_TEST=1 时附带。实际能力自检请跑："
                    "`python -m agentscope_orchestrator.maturity --repo <仓>`，"
                    "退出码 0=全绿 / 1=有红 / 3=只有未知（条数以该命令自己的记分卡为准）。",
        "工具表说明": (f"上表只列**常用**（`orch_run` 有两条：通用与测试命令）；"
                       f"全部工具有 **{_工具总数()} 个**（现算，不手写），完整清单与参数用 "
                       "`list_tools` 查（形式化轨道 submit_task/read_report/audit_log、"
                       "无人值守 auto_scan/auto_candidates/auto_submit/auto_develop/auto_goal、"
                       "知识 search_docs/read_doc 等）——别因为表里没有就以为没有"),
        "做不到（别指望）": [
            "无容器隔离：只有命令白名单 + cwd 约束，不防恶意命令",
            "并发上限 4 现在是**跨进程**的（`global_gate` 用操作系统文件锁占槽；"
            "槽目录不可写时降级为进程内上限，并把它写进 job 的 `并发降级`）",
            "WSL2 验过判据门/符号记忆/上下文包核心层；MCP 服务端本身未在 Linux 装过",
            "没有公开 benchmark ⇒ 不主张「比谁快」",
            "orch_fix 能新建/删除/重命名，但不做出仓动作、不碰 .git、不改判据环境文件",
            "orch_fix 的改动由**门**裁决，门弱则可能放过语义错的改动 —— 这条**不可彻底消灭**（等价于自动程序验证，一般不可判），但可以量：用 `gate_power` 注机械变异，看门抓住几种；区分力低就是该加门族的证据",
        ],
        "示例": [
            {"要什么": "跑测试并拿结论",
             "调": {"工具": "orch_run", "参数": {"repo": "E:\\proj", "cmd": ["python", "-m", "pytest", "-q"]}}},
            {"要什么": "抓「绿只因有构建产物」的伪绿",
             "调": {"工具": "orch_run", "参数": {"repo": "E:\\proj", "cmd": ["cargo", "check"],
                                                "instances": 2, "instances_mode": "copy"}}},
            {"要什么": "怀疑抖动（时绿时红）",
             "调": {"工具": "orch_run", "参数": {"repo": "E:\\proj", "cmd": ["pytest", "-q"],
                                                "windows": 5, "min_pass": 3}}},
        ],
    }
    return json.dumps(返回, ensure_ascii=False)


@mcp.tool(name=_TOOL("orch_object"))
async def taixu_object(repo: str, 怀疑: str, cmd: list[str],
                       timeout_s: int = 300, max_files: int = 12000) -> dict:
    """★★【AI 的怀疑接口】你说"这个绿可能是假的"，我给你**可机验的两个读数**。

    外部产品在这里是空白：模型怀疑自己或怀疑门时，只能重跑或瞎猜。本工具把它变成动作：
      ① 在**当前产物**上跑 `cmd`（应绿）
      ② 在**破坏副本**里再跑（机械变异：返回置空/比较反号/真值翻转/删断言）
      ③ 两边读数都成立 ⇒ 这条判据**确实抓得住单点破坏**（入册 `判据册.jsonl`）
         变异后仍绿 ⇒ 如实告诉你"这条判据对破坏不敏感，别把结论寄托在它上面"

    什么时候用：任务绿了但你不放心 / 门只判退出码 / 你要把"我怀疑"落成一条永久判据。
    什么时候别用：现状已红（直接 `orch_fix`）；仓 > 3000 源文件（用 `gate_power` 量门族）。

    参数：repo=仓绝对路径 · 怀疑=一句话（会写进判据册做留痕）· cmd=要验的那条命令。
    返回 ≤2KB：分类 / 现状 / 区分力 / 已入册 / 决策摘要 / 留痕（回执文件路径，会话断了可回读）。
    """
    from agentscope_orchestrator import agentface
    try:
        return await agentface.实现(repo, 怀疑, list(cmd or []), int(timeout_s),
                                    int(max_files))
    except Exception as e:                        # noqa: BLE001 工具不许把异常抛给调用方
        return {"分类": "参数/前提不成立", "责任归属": "调用方侧",
                "error": f"{type(e).__name__}: {e}",
                "决策摘要": {"分类": "参数/前提不成立", "下一步": [{"做": "看 error 原文改参数"}]}}


@mcp.tool(name=_TOOL("list_tasks"))
def list_tasks() -> str:
    """列出全部任务及状态（id/单元/定理/阶段/优先级/依赖/通过窗数）。"""
    _db_sync_in()      # 并进别的写者新增的行（长驻进程不再只看启动快照）
    order = {jid: i for i, jid in enumerate(_queue_order())}
    rows = []
    for jid, j in sorted(_jobs.items()):
        s = j.get("status", {})
        queue = None
        if j.get("state") == "queued":
            if jid in order:
                queue = {"queued": True, "priority": j.get("priority", 0),
                         "queue_pos": order[jid] + 1}
            else:
                queue = {"queued": True, "priority": j.get("priority", 0),
                         "waiting_on": j.get("depends_on", [])}
        rows.append({"id": jid, "state": j.get("state"),
                     "unit_id": j.get("unit_id"), "module": j.get("module"),
                     "theorem": j.get("theorem"),
                     "priority": j.get("priority", 0),
                     "depends_on": j.get("depends_on", []),
                     "queue": queue,
                     "passed": s.get("passed"), "failed": s.get("failed"),
                     "product": s.get("product"),
                     "error": j.get("error")})
    # 任务列表可能随任务量膨胀：超过内联阈值即外置为文档，聊天窗口只留指针
    return ds.thin(json.dumps(rows, ensure_ascii=False, indent=2),
                   "tasks", f"任务列表({len(rows)}条)")


@mcp.tool(name=_TOOL("task_status"))
def task_status(task_id: str) -> str:
    """单任务摘要状态：阶段、通过/失败窗数、产物路径、控制台链接。
    （不返回各窗候选全文——大载荷请到只读控制台 /task/<id> 按页查看。）"""
    j = _jobs.get(task_id)
    if j is None:
        _db_sync_in()                 # 可能是别的写者刚写的行，先追赶再判
        j = _jobs.get(task_id)
    if j is None:
        return json.dumps({"ok": False, "error": f"task {task_id} 不存在"}, ensure_ascii=False)
    s = j.get("status") or {}
    _成本累(task_id, "task_status")
    windows = [{"idx": w["idx"], "passed": w.get("passed")}
               for w in s.get("windows", [])]
    order = _queue_order()
    queue = None
    if j.get("state") == "queued":
        if task_id in order:
            queue = {"queued": True, "queue_pos": order.index(task_id) + 1}
        else:
            queue = {"queued": True, "waiting_on": j.get("depends_on", [])}
    return json.dumps({"ok": True, "state": j.get("state"),
                       "rc": j.get("rc"), "error": j.get("error"),
                       "finished_at": j.get("finished_at"),
                       "priority": j.get("priority", 0),
                       "depends_on": j.get("depends_on", []),
                       "queue": queue,
                       "summary": {"state": s.get("state"),
                                   "passed": s.get("passed"),
                                   "failed": s.get("failed"),
                                   "product": s.get("product"),
                                   "adopted_window": s.get("adopted_window"),
                                   "min_pass": s.get("min_pass"),
                                   # ★ 烧字（2026-10-13 补）：此前**不返回** ⇒
                                   #   无人值守护栏的"成本失控 ⇒ 停机"判据
                                   #   拿到的永远是 0 ⇒ 是**死判据**（装饰）。
                                   #   现由 real_task 如实计量并透出。
                                   "tokens_chars": s.get("tokens_chars"),
                                   "final_gates": s.get("final_gates")},
                       "windows": windows,
                       "doc_ids": s.get("doc_ids"),   # 已归档的各窗推理链文档
                       # ★ agent-first：加"能不能收工"的三个字段（分类/证据/修法）+ 成本
                       "分类": _分类(j),
                       "门": _门们(s),
                       "失败证据": [g for g in _门们(s) if g["通过"] is False],
                       "修法": _修法(j, _分类(j)),
                       "cost": _成本读(task_id, j),
                       "console": _CONSOLE + f"/task/{task_id}"},
                      ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("judge_result"))
def judge_result(task_id: str) -> str:
    """判据门详情：每窗 4 门（shell_ok/no_forbidden/statement_intact/no_sorry_axiom）。"""
    j = _jobs.get(task_id)
    if j is None:
        return json.dumps({"ok": False, "error": f"task {task_id} 不存在"}, ensure_ascii=False)
    gates = (j.get("status") or {}).get("gates", []) or []
    _成本累(task_id, "judge_result")
    _门 = _门们(j.get("status") or {})
    return json.dumps({"ok": True, "gates": gates,
                       "门摘要": _门,
                       "失败证据": [g for g in _门 if g["通过"] is False],
                       "分类": _分类(j), "修法": _修法(j, _分类(j)),
                       "cost": _成本读(task_id, j)},
                      ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("audit_log"))
def audit_log(task_id: str) -> str:
    """审计回放入口：仅回审计条数 + 控制台链接。
    （全量逐条审计请在只读控制台 /task/<id> 分页回放。）"""
    j = _jobs.get(task_id)
    if j is None:
        return json.dumps({"ok": False, "error": f"task {task_id} 不存在"}, ensure_ascii=False)
    audit = (j.get("status") or {}).get("audit", []) or []
    # ★ agent-first：审计**内联**（尾部 50 条，每条截 500 字符）。
    #   以前只回条数 + 控制台链接 ⇒ 模型看不到任何一条 = 信息黑洞。
    _尾 = audit[-50:]
    行们 = []
    for it in _尾:
        文 = json.dumps(it, ensure_ascii=False) if not isinstance(it, str) else it
        行们.append(文 if len(文) <= 500 else 文[:500] + f"…(截断 {len(文)})")
    _成本累(task_id, "audit_log")
    return json.dumps({"ok": True, "audit_total": len(audit),
                       "内联条数": len(行们),
                       "省略条数": max(0, len(audit) - len(行们)),
                       "audit": 行们,
                       "cost": _成本读(task_id, j),
                       "console": _CONSOLE + f"/task/{task_id}"},
                      ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("read_report"))
def read_report(task_id: str, max_chars: int = 8000) -> str:
    """读产物/报告（只读）：**内容内联**（默认 8000 字符，超出给 truncated 标记）+ 路径 + 成本。"""
    j = _jobs.get(task_id)
    if j is None:
        return json.dumps({"ok": False, "error": f"task {task_id} 不存在"}, ensure_ascii=False)
    product = (j.get("status") or {}).get("product")
    if not product:
        return json.dumps({"ok": False, "error": "该任务尚无产物（未收敛或未写回）",
                           "state": j.get("state")}, ensure_ascii=False)
    p = Path(product).resolve()
    allowed = (str(ARTIFACT_DIR.resolve()), str(PRODUCTS.resolve()))
    if not p.is_file() or not str(p).startswith(allowed):
        return json.dumps({"ok": False, "error": "路径越界，拒绝读取"}, ensure_ascii=False)
    # ★ agent-first：产物**内联**（默认 8000 字符，可调），路径与链接仅为附加字段。
    文 = p.read_text(encoding="utf-8", errors="replace")
    截 = 文[:max_chars] if len(文) > max_chars else 文
    _成本累(task_id, "read_report", len(截))
    return json.dumps({"ok": True, "path": str(p),
                       "byte_len": p.stat().st_size,
                       "truncated": len(文) > max_chars,
                       "total_chars": len(文),
                       "content": 截,
                       "cost": _成本读(task_id, j),
                       "console": _CONSOLE + f"/report/{task_id}"},
                      ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("cancel_task"))
def cancel_task(task_id: str) -> str:
    """取消运行中的任务。"""
    j = _jobs.get(task_id)
    if j is None:
        return json.dumps({"ok": False, "error": f"task {task_id} 不存在"}, ensure_ascii=False)
    r = _runners.get(task_id)
    # ★ 粘性取消（2026-10-30 真跑）：`r.cancel()` 在跨线程派发下不落地——
    #   实测 cancel 后 0.8s 再查，state 仍是 running，任务跑完后还会被写成成功。
    #   ⇒ 记一个**粘性标记**：一旦请求取消，判决就是「取消」，执行体跑完也不翻案。
    j["_cancel"] = True
    j["state"] = "cancelled"
    j["error"] = "cancelled by user"
    if r is None or r.done():
        return json.dumps({"ok": True, "task_id": task_id,
                           "action": "marked cancelled（执行体已结束）"}, ensure_ascii=False)
    r.cancel()
    return json.dumps({"ok": True, "task_id": task_id,
                       "action": "cancel requested"}, ensure_ascii=False)


@mcp.tool(name=_TOOL("resume_task"))
def resume_task(task_id: str) -> str:
    """断点续跑：仅允许对已终止任务（error / convergence_failed /
    cancelled / 孤儿 running）。复用已通过窗（子任务幂等，不重调模型、
    不重跑该窗门），只补跑失败/未跑窗。"""
    j = _jobs.get(task_id)
    if j is None:
        return json.dumps({"ok": False, "error": f"task {task_id} 不存在"},
                          ensure_ascii=False)
    r = _runners.get(task_id)
    if r is not None and not r.done():
        return json.dumps({"ok": False, "error": "任务仍在运行，无需续跑"},
                          ensure_ascii=False)
    if j.get("state") not in ("error", "convergence_failed", "cancelled", "running"):
        return json.dumps({"ok": False,
                           "error": f"state={j.get('state')} 不可续跑（仅 error/"
                                    f"convergence_failed/cancelled/孤儿 running）"},
                          ensure_ascii=False)
    if "template" not in j or "statement_marker" not in j:
        return json.dumps({"ok": False,
                           "error": "旧任务缺 template/statement_marker 参数，"
                                    "无法重建任务（请重新 submit）"},
                          ensure_ascii=False)
    task = rt.TaskSpec(id=task_id, unit_id=j["unit_id"], module=j["module"],
                       theorem=j["theorem"], statement_marker=j["statement_marker"],
                       target_rel=j["target_rel"], template=j["template"],
                       min_pass=j["min_pass"], n_windows=j["n_windows"],
                       gates=j.get("gates"))
    task.promote = j.get("promote", False)
    resume: dict[int, dict] = {}
    old_status = j.get("status") or {}
    old_gates = {g["idx"]: g.get("gates", {}) for g in old_status.get("gates", [])}
    old_audit = {a["idx"]: a.get("audit", []) for a in old_status.get("audit", [])}
    for w in old_status.get("windows", []):
        if w.get("passed"):
            idx = w["idx"]
            resume[idx] = {"idx": idx, "passed": True, "text": w["text"],
                           "gates": old_gates.get(idx, {}),
                           "audit": old_audit.get(idx, [])}
    j["state"] = "running"
    j["error"] = None
    j.pop("rc", None)
    j.pop("finished_at", None)
    _db_save_one(task_id, j)
    _runners[task_id] = _submit_coro(
        _run_job(task_id, task, j.get("channel", ""), j.get("dry", False),
                 resume=resume))
    return json.dumps({"ok": True, "task_id": task_id, "state": "running",
                       "resumed_windows": sorted(resume.keys()),
                       "rerun_windows": [i for i in range(task.n_windows)
                                         if i not in resume]},
                      ensure_ascii=False)


@mcp.tool(name=_TOOL("auto_scan"))
def auto_scan(scan_tasks: bool = False) -> str:
    """G7：建立/刷新统一记忆索引 —— **按项目档案的 `memory:` 段扫**。

    ★ R230 改：原来代码根取自 `TOOL_ROOT`（**工具安装位置**），装成包跑时
      那个目录不存在 ⇒ 索引里只剩产物库（真流程实测只回 `{"formal": 38238}`），
      项目本体的源码/文档一份没进。现在扫什么由**项目自己声明**决定 ——
      换项目 = 换档案。
    `scan_tasks` 为兼容保留：档案声明了就照声明走，它不再决定扫哪些根。
    """
    global _MEM
    _MEM = MemoryIndex()
    _覆盖 = scan_project(_MEM, rt.ARTIFACT_DIR,
                         prof_mod.get_profile().repo_root)
    sizes = _MEM.sizes()
    cands = ae.select_unclosed(_MEM, rt.ARTIFACT_DIR, limit=100)
    出 = {"ok": True, "kinds": sizes,
          "declared": _覆盖.get("declared"),
          "逐条": _覆盖.get("逐条"),
          "unclosed_candidates": len(cands),
          "console": _CONSOLE}
    if _覆盖.get("warn"):
        出["warn"] = _覆盖["warn"]
    if _覆盖.get("声明了却没扫到"):
        出["声明了却没扫到"] = _覆盖["声明了却没扫到"]
    return json.dumps(出, ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("auto_candidates"))
def auto_candidates(limit: int = 20) -> str:
    """G7：列出未闭合声明候选（含依赖缺口排序），供选题洞察。
    只读：不提交、不改库。完整陈述/正文到只读控制台查看。"""
    mem = _ensure_memory()
    cands = ae.select_unclosed(mem, rt.ARTIFACT_DIR, limit=max(1, min(limit, 100)))
    rows = [{"unit_id": c.unit_id, "theorem": c.theorem,
             "deps": c.deps, "source": str(c.source_path)}
            for c in cands]
    return json.dumps({"ok": True, "count": len(rows), "candidates": rows,
                       "console": _CONSOLE},
                      ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("auto_submit"))
def auto_submit(limit: int = 1, min_pass: int = 2, n_windows: int = 3,
                promote: bool = False, dry: bool = False) -> str:
    """G7：无人值守——自动选未闭合声明、生成模板、提交真跑（判据门+收敛兜底）。

    每次调用收口至多 limit(1..5) 个未闭合定理。锚定闸仍生效：unit_id 取索引内
    真实单元；不该写的绝不写——产物写回仍过全部门 + 最终门复跑。
    返回各任务 id + 状态。运行进度用 task_status/只读控制台查看。"""
    mem = _ensure_memory()
    cands = ae.select_unclosed(mem, rt.ARTIFACT_DIR, limit=max(1, min(limit, 5)))
    if not cands:
        return json.dumps({"ok": False, "error": "库内已无未闭合声明（全闭合）或索引为空"},
                          ensure_ascii=False)
    submitted = []
    for cand in cands:
        task = ae.gen_lean_candidate(cand, rt.ARTIFACT_DIR)
        # 复用既有提交路径（锚定/模板/判据门校验 + 入队 + 调度）
        res = submit_task(unit_id=task["unit_id"], module=task["module"],
                          theorem=task["theorem"],
                          statement_marker=task["statement_marker"],
                          target_rel=task["target_rel"], template=task["template"],
                          gates=task["gates"], n_windows=n_windows,
                          min_pass=min_pass, promote=promote, dry=dry)
        try:
            r = json.loads(res)
        except Exception:
            r = {"ok": False, "error": res[:200]}
        submitted.append({"theorem": task["theorem"], "resp": r})
    return json.dumps({"ok": True, "submitted": submitted,
                       "console": _CONSOLE},
                      ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("search_docs"))
def search_docs(kw: str = "", kind: str = "", limit: int = 20) -> str:
    """查外置文档索引（「模型自己建立索引、按需查看」的入口）。

    kw 子串匹配标题/类别/meta/**摘录**；元数据没命中时**兜底扫正文**（实测 13.6 MB 全扫 0.42 秒）⇒ 按主题也查得到。
    kind 过滤（chain=推理链/note=说明/tasks=任务列表）。
    只返回条目标签（doc_id/字节/摘录），全文用 read_doc 按需拉取——
    聊天窗口只留记录、不载大载荷，防 DSH 会话卡死。"""
    rows = ds.search_docs(kw=kw or None, kind=kind or None, limit=limit)
    return json.dumps({"ok": True, "count": len(rows), "rows": rows,
                       "console": _CONSOLE}, ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("read_doc"))
def read_doc(doc_id: str, max_chars: int = 4000) -> str:
    """按 doc_id 读取外置文档（各窗推理链/说明/任务列表全文）。限长返回
    （max_chars 200..20000），防止一次拉爆 DSH 会话。doc_id 来源：search_docs
    结果或工具返回里的 [doc:...] 指针。"""
    return ds.read_doc(doc_id, max_chars=max(200, min(max_chars, 20000)))


@mcp.tool(name=_TOOL("auto_develop"))
def auto_develop(iterations: int = 2, channel: str = "",
                 unit_id: str = "",
                 name: str = "develop", priority: int = 0,
                 depends_on: list[str] | None = None) -> str:
    """一键「给目标自动完成全线开发」：后台跑 develop 无人值守修复环。

    develop 环 = 扫描引擎红门 → 缺口证据打包派真实模型产 OLD/NEW（机器演化
    工具位简报注入） → 备份/原子应用 → 机器判据门裁决（绿保/红滚） → 账本
    续跑幂等。本次提交即入队（G5 优先级/依赖链），完成看 task_status。

    unit_id: 锚定真实源码单元（空=用档案 anchors.main；锚不到 → 拒跑）。
    iterations: 自动修复轮数（≥1）；channel: 真模型通道名。

    ★【TS-381】channel 默认由写死的 `"aaa"` 改为 `""`（=全通道）。
      原默认值把整条产品线钉在**某一个**通道上：该通道一旦下线/改名，
      工具**每次都失败**，而调用方看不出是「通道死了」还是「任务真失败」。
      实测（2026-10-13）：`aaa` 的 `deepseek-v4.1-flash` 已下线
      （上游 503 `model_not_found`），而 `dddddd`/`ggg` 仍可用。
      空串走 `select_channels("")` → 全通道 + Router 轮转 ⇒ 单通道故障可降级。

    诚实边界：develop 只改由机器门裁决的工程源码；驱动本身不 Edit/Write
    引擎源码。"""
    if not unit_id:
        unit_id = prof_mod.get_profile().anchors.main
    unit = _anchor_engine(unit_id)
    if unit is None:
        return json.dumps({"ok": False,
                           "error": f"引擎锚定失败：{unit_id} 不在源码索引内，拒跑"},
                          ensure_ascii=False)
    iters = max(1, min(iterations, 10))
    # ★ 2026-10-31：原为 `TOOL_ROOT/"src"/…/develop.py` —— 只在源码树成立，装成 wheel 后
    #   该路径不存在（根也从 `<venv>/Lib` 起算），auto_develop 一调就跑不起来。
    #   develop.py 本体在**两种布局下都在包目录里**，直接用 PKG_DIR 定位；cwd 用可写状态根。
    cmd = [sys.executable, str(prof_mod.PKG_DIR / "develop.py"),
           "--iterations", str(iters)]
    if channel:
        cmd += ["--channel", channel]
    prof = prof_mod.get_profile()
    if prof.source_path:
        cmd += ["--profile", str(prof.source_path)]
    gates = [{"type": "compile", "name": "develop_ok",
              "cmd": cmd, "cwd": str(prof_mod.STATE_ROOT),
              "expect_exit": 0, "timeout": 600}]
    # 复用 run_errand 提交路径（任意程序任务 + G5 调度 + 判据门）
    return run_errand(unit_id=unit_id, cmd=cmd, cwd=str(prof_mod.STATE_ROOT),
                      gates=gates, name=name, priority=priority,
                      depends_on=depends_on)


@mcp.tool(name=_TOOL("auto_goal"))
def auto_goal(goal_json: str) -> str:
    """目标→任务序列 机器计划器（编排层工具位⑦，0-LLM 分解）。

    输入真模型产出的结构化规约 JSON（自然语言目标→载体的语义映射由真模型做，
    本工具不猜语义、不审模型）：
      {"name":"目标名", "pre":["前提槽",...], "constraints":["约束槽",...],
       "gates":[GateSpec,...], "acceptance":["验收判据",...]}
    GateSpec 每项 = {"type","cmd","cwd","expect_exit"(可省),"timeout"(可省)}。

    返回机器计划：tasks + **topo（可执行拓扑序）** + conserved（全列守恒机验，
    违例拒发）+ rule_space（规则升阶 RA-03）+ rules。

    ★★★★★ R8-F14：`topo` 是**执行序**，不再是展示品。
      调用方**照 `topo` 顺序**逐个提交（run_errand / auto_develop），
      并把前序任务的**真 job id** 写进后序任务的 `depends_on`
      ⇒ 顺序由计划定、依赖由编排器机器闸守（`_dep_state` 只认 promoted）。
      `topo` 里的 tid 是**计划内 id**（`task_0`…），不是 job id；
      提交时用返回的 `task_id` 回填依赖。
      成环 / 依赖指向不存在的任务 ⇒ 计划器**拒发**（不返回跑不起来的计划）。

    计划器绝不 Edit/Write 引擎源码；各任务语义内容（cmd）由真模型提供。"""
    try:
        plan = ag.plan_tasks(json.loads(goal_json))
        # ★ 把执行契约显式写进回执：调用方不必猜怎么用这份计划。
        plan["执行契约"] = {
            "顺序": "照 topo 逐个提交；前序的真 job id 回填到后序 depends_on",
            "依赖闸": "编排器 _dep_state 只认 promoted（staged=未落地，不放行下游）",
            "失败语义": "任一段红 ⇒ 该段不 promoted ⇒ 下游自动判 error（不放行）",
        }
        # ★★★★★ R274（2026-11-05 真读数）：**"照 topo 顺序提交"这句话，此前没有人能执行。**
        #   真读数：`plan_to_submissions` 上一轮加完，调用方只有它自己的自检——
        #     全仓 grep 只有 `def plan_to_submissions` 和一处 `_s273 = ...`（齿里）。
        #   而本工具的文档写着"调用方照 topo 顺序逐个提交"，
        #   **却只给出计划内 tid、不给可提交清单** ⇒ 调用方得自己再实现一遍翻译，
        #   而"自己再实现一遍"就是 R273 里 `tid`/`kind` 丢失的那条老路。
        #   ⇒ 计划器**直接给出可提交清单**，把 tid→kind→gates→依赖 全带上，
        #     调用方只剩一件事：把 `depends_on_plan` 换成真 job id 然后提交。
        #   ★ 这是**加法**：原有字段一个不动，老调用方零回退。
        plan["submissions"] = ag.plan_to_submissions(plan)
        plan["可执行性"] = {
            "可运行项": [s["tid"] for s in plan["submissions"] if s.get("will_run")],
            "汇总点": [s["tid"] for s in plan["submissions"] if not s.get("will_run")],
            "说明": ("`submissions` 已按 topo 序排好；`will_run=True` 的才需要真提交，"
                    "汇总点只等依赖。`depends_on_plan` 是计划内 tid，"
                    "提交时换成真 job id 填进 depends_on。"),
        }
        return json.dumps(plan, ensure_ascii=False, indent=2)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(name=_TOOL("run_goal"))
def run_goal(goal_json: str, cwd: str) -> str:
    """按目标树清单**无人值守裁决**：逐段跑目标仓自己的判据门，汇总父目标是否达成。

    ★★★★★ R275：为什么必须有这个工具（真读数，2026-11-05）：
      · `auto_goal` 能给出 `submissions`（可提交清单，带 tid/kind/gates/依赖）；
      · 但全仓**没有任何东西按它执行**：`develop.py` 是从 audit/observation
        抽 Gap 再派模型修的"修坏链"，它把 `auto_goal.py` 当**自检目标**，
        完全不认识 topo/submissions；
      · R274 的真跑驱动是我**手写的一次性脚本**，跑完就删了。
      ⇒ 「一段驱动不了的接线等于没有接线」；同理 **一次性的驱动脚本也不等于产品**。

    本工具做的事：`goal_json` → 机器计划 → 按 topo 序逐段求值目标仓自己的门 →
    逐段裁决 + 父目标是否达成 + 未达成时**点名到段和门**。

    ★ 它**不派模型、不改码**：段的内容由调用方（模型/人）产出；
      它只回答"这些段现在到底绿没绿、整体达成没达成"。
    ★ 级联语义：某段红 ⇒ 依赖它的段**跳过**（不假装绿）；
      约束段红 ⇒ 下游全停（边界被踩了就不要再往前做）。
    ★ 空判据不许当绿：会跑的段如果一条门都没有 ⇒ 判「拒跑」。
    """
    try:
        规 = json.loads(goal_json)
    except ValueError as e:
        return json.dumps({"ok": False, "error": f"goal_json 不是合法 JSON：{e}"},
                          ensure_ascii=False)
    根 = Path(cwd or ".").resolve()
    if not 根.is_dir():
        return json.dumps({"ok": False, "error": f"cwd 不是目录：{cwd}"}, ensure_ascii=False)
    try:
        计划 = ag.plan_tasks(规)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    好, 因 = accept_closed(计划)
    if not 好:
        return json.dumps({"ok": False, "error": 因, "refuse": "parent_goal_not_closed"},
                          ensure_ascii=False)
    果 = gr.run_plan(计划, 根)
    果["计划摘要"] = {"closed": 计划.get("closed"), "topo": 计划.get("topo"),
                      "可运行项": [s["tid"] for s in ag.plan_to_submissions(计划)
                                   if s.get("will_run")]}
    果["cwd"] = str(根)
    return json.dumps(果, ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("close_goal"))
def close_goal(goal_json: str, cwd: str, adopted_json: str = "",
               源码根: str = "") -> str:
    """**闭环一次调用**：规划 → 裁决 → 定位改动面 → （有改码输入才）应用 → 复验。

    ★★★★★ R277：为什么必须有这个工具（真读数，2026-11-06）：
      `close_loop.close_loop()` 上一轮做完了，但**调用方只有它自己的注释** ——
      全仓 grep 只有 `def close_loop` 和它内部的自我引用。
      按本项目自己的口径「一段驱动不了的接线等于没有接线」，
      一个没有调用方的函数**同样等于没有接线**。

    和 `run_goal` 的分工：
      · `run_goal`  只**裁决**（跑目标仓自己的门，回答"现在达成没达成"）；
      · `close_goal` 裁决 + **定位改动面** + 可选**应用改码** + **复验**。

    参数：
      goal_json    : 同 `auto_goal`（name/pre/constraints/gates/acceptance）
      cwd          : 目标仓根
      adopted_json : `{"相对路径": "新全文", ...}` —— **模型产出的改码**。
                     留空 ⇒ 只跑到"定位"并报 `blocked_on_model`
                     （**这不是失败，是"还没有改码输入"**）
      源码根       : 逗号分隔的改动面搜索根（默认自动探测 src/lib/app/pkg）

    ★ 诚实边界：
      · 留空 `adopted_json` 时，回执**只证明裁决与定位是通的**，不证明目标能达成；
      · 会写盘：改码落在 `cwd` 指向的仓里，且**只改改动面内的文件**；
        复验不绿 ⇒ **整批回滚**（有备份），回执里 `应用.回退` 会列出来；
      · 本工具**不派模型**。改码内容必须由调用方提供。
    """
    try:
        规 = json.loads(goal_json)
    except ValueError as e:
        return json.dumps({"ok": False, "error": f"goal_json 不是合法 JSON：{e}"},
                          ensure_ascii=False)
    改码 = {}
    if adopted_json.strip():
        try:
            改码 = json.loads(adopted_json)
        except ValueError as e:
            return json.dumps({"ok": False,
                               "error": f"adopted_json 不是合法 JSON：{e}"},
                              ensure_ascii=False)
        if not isinstance(改码, dict):
            return json.dumps({"ok": False,
                               "error": "adopted_json 必须是 {相对路径: 新全文} 对象"},
                              ensure_ascii=False)
    根 = Path(cwd or ".").resolve()
    if not 根.is_dir():
        return json.dumps({"ok": False, "error": f"cwd 不是目录：{cwd}"},
                          ensure_ascii=False)
    try:
        计划 = ag.plan_tasks(规)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    好, 因 = accept_closed(计划)
    if not 好:
        return json.dumps({"ok": False, "error": 因, "refuse": "parent_goal_not_closed"},
                          ensure_ascii=False)
    _根们 = [x.strip() for x in 源码根.split(",") if x.strip()] or None
    try:
        果 = cl.close_loop(计划, 根, adopted=改码 or None, 源码根=_根们)
    except Exception as e:                          # noqa: BLE001
        return json.dumps({"ok": False,
                           "error": f"闭环自身异常：{type(e).__name__}: {e}"},
                          ensure_ascii=False)
    果["cwd"] = str(根)
    果["有没有改码输入"] = bool(改码)
    # ★★★★★ R283（真读数，2026-11-07）：**未达成时不给出口，调用方就只能猜。**
    #   实测：只给 `a.py` 的改码时，本工具如实返回
    #     ok=False · 应用.保留=['src/a.py'] · 阶段=复验
    #   —— 账是准的（没把半成品当成功）。但调用方看到 `ok=False` 会卡住：
    #     ① 是"该再给一批改码"？② 还是"我这条路过时了，该换 drive_goal"？
    #     ③ 还是"根本没有可改的了"？
    #   三种情形**下一步完全不同**，不给出口等于把判断甩给调用方。
    #   ⇒ 本工具**只跑一轮**（`close_loop` 不迭代），这是它和 `drive_goal` 的分工：
    #     · `close_goal` = 你给改码，我应用+复验（**一轮**，你可自己套循环）
    #     · `drive_goal` = 我调模型产改码，**迭代到达成或抛锚**
    #   所以 `未达成` 时的出口只有两条，工具必须说清是哪一条。
    if not 果.get("ok"):
        # ★ R283b（真读数）：**又读错了层、又猜错了键名。**
        #   我第一版写 `果.get("段")` —— 而 `close_loop` 返回的顶层**没有** `段`，
        #   真正的段在 `果["复验"]["段"]` 里（复验才是 run_plan 的结果）。
        #   于是 `_剩` 恒为空 ⇒ 走进"未达成但没有红段"的兜底 ⇒ **报了假警报**。
        #   顺带键名也猜错了：段的标识是 `tid` 不是 `id`（`run_plan` 文档写的是
        #   `{"tid","kind","状态","门","理由"}`）—— **不要猜数据的形状，去读它的生产者。**
        #   ★ 这已经是**同一类错第 4 次**（R280e 读错层、R281c 猜常量值、
        #     R282b/c 判据少读一个字段，现在是读错层+猜键名）。
        #   ⇒ 一律**先问生产者**：段在 `复验` 里，键是 `tid`/`状态`。
        _复 = 果.get("复验") or {}
        _剩 = [x for x in (_复.get("段") or [])
                if x.get("状态") in ("红", "拒跑", "异常")]
        if 果.get("blocked_on_model"):
            果["下一步"] = ("没有改码输入 ⇒ 给 `adopted_json` 再来一次，"
                          "或直接用 `drive_goal`（它会自己调模型产改码并迭代）。")
        elif _剩:
            果["未达成的段"] = [{"tid": x.get("tid"), "状态": x.get("状态")}
                              for x in _剩]
            果["下一步"] = (
                f"**只跑了一轮**，还剩 {len(_剩)} 段没绿"
                f"（{[x.get('tid') for x in _剩]}）。两条路："
                "①再调 `close_goal` 并给上批**未达成段**的新改码（自己套循环）；"
                "②改用 `drive_goal`（它迭代到底，你只管给目标和预算）。"
                "★ 已保留的改动**已在主树里**，不必重做。")
        else:
            果["下一步"] = "未达成但没有红段（门没过却没报红）——把回执原文发我看，这本身是个缺陷。"
    果["入口分工"] = ("close_goal=你给改码，我只跑一轮（应用+复验）；"
                    "drive_goal=我调模型产改码并迭代。两者都会真落主树。")
    return json.dumps(果, ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("drive_goal"))
def drive_goal(goal_json: str, cwd: str, max_rounds: int = 3,
               channel: str = "", 源码根: str = "", force: bool = False) -> str:
    """**无人值守驱动一个工程目标**：迭代「模型产改码 → 机器判据门裁决」直到达成或抛锚。

    ★★★★★ R279：这是整条链的收口工具（真读数，2026-11-06）：
      前几轮把各段都做出来了，但"产出改码"这一半**仍然是人**——
      `goal_iterate.iterate(改码=...)` 的改码是个回调占位，我只能用测试脚本里
      手写的函数喂它。⇒ 本工具把这一半接上**真模型**，形成闭环：
        `auto_goal`（规划）→ `goal_iterate`（迭代）→ `model_coder`（模型产整文件）
        → `close_loop` + `gate_spec`（**机器门裁决**，绿保/回退）→ 下一轮

    ★ 三条硬纪律（都是真事故换来的，写进这里免得以后又被绕过）：
      1. **模型不给自己发合格证**：模型只产 `{相对路径: 完整新全文}`，
         落不落盘、留不留，全由机器判据门决定；
      2. **只许改改动面内的文件**：改动面由机器算出（失败栈 + 门声明），
         模型返回越界文件（如 `tests/`）一律**拒收并如实列出**；
      3. **拿不到可用产物就如实说**：通道全死 / 返回不是完整文件 ⇒ 报
         `blocked_on_model`，**不许把"没拿到"说成"改完了"**。

    参数：
      max_rounds : 预算护栏（1..8）。打转会**提前**停，不跑满。
      channel    : 通道名；空 = 全通道轮转（单通道故障可降级）。
      源码根     : 逗号分隔，改动面搜索根（默认自动探测）。
      force      : ★ R282：默认 False。开工前会**自体检**；若「没有一个确认活的通道、
                   只剩读不到的槽」，默认**不开工**（写 20 个文件也大概率一个都拿不到），
                   直接回 `拒跑=no_live_channel_before_start` 并给出该谁动手。
                   只有你明确知道"那个槽只是慢、其实能用"时才 force=True。
                   全硬死（缺 key / 无权访问）时 force 也不许 —— 那重试毫无意义。

    ★ 会写盘：改动落在 `cwd` 指向的仓里；每轮失败的改动**逐文件回退**
      （使红段数减少的保留，没变好的回滚）。最终 `ok` 严格由「目标仓自己的门全绿」决定。
    """
    try:
        规 = json.loads(goal_json)
    except ValueError as e:
        return json.dumps({"ok": False, "error": f"goal_json 不是合法 JSON：{e}"},
                          ensure_ascii=False)
    根 = Path(cwd or ".").resolve()
    if not 根.is_dir():
        return json.dumps({"ok": False, "error": f"cwd 不是目录：{cwd}"},
                          ensure_ascii=False)
    try:
        计划 = ag.plan_tasks(规)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    好, 因 = accept_closed(计划)
    if not 好:
        return json.dumps({"ok": False, "error": 因, "refuse": "parent_goal_not_closed"},
                          ensure_ascii=False)
    # ★★★★★ R282（真读数，2026-11-07）：**需要调用方记得做的步骤，等于没做。**
    #   上一轮做出了 `doctor_channels`，但它是**另一个工具** —— 调用方很容易
    #   忘了先体检就直接 `drive_goal`，然后**跑到一半**才没通道：
    #   前几个文件白改，回执里只有一句"阻塞"。
    #   ⇒ 开工前自己先体检，**没通道就别开工**。而且不许只说"没通道"：
    #     要把「哪种死法、责任在谁、该做什么」一起给出（这才是能动手的信息）。
    #   ★ 边界（诚实）：这**不保证**模型改得对；它只保证"有通道可派、且理由说清"。
    体检 = None
    try:
        体检 = cd.体检(channel or "")
    except Exception as e:                          # noqa: BLE001
        体检 = {"可开工": False, "档": "体检失败",
                "结论": f"体检自身异常：{type(e).__name__}: {e}",
                "可交给router": [], "汇总": {}, "逐通道": []}
    # ★ R282b（我自己抓的）：**判据选错了依据。**
    #   第一版用 `体检.get("能开工")` 做闸 —— 而实测本机是
    #     `能开工=True`（qww 是 unreadable，按 R18 仍交给 Router 试）、
    #     `可开工=False`（没有一个确认活的通道）
    #   ⇒ 闸**形同虚设**，照样往下派模型、照样跑到超时。
    #   ⇒ 这是「手里那个量不是判据需要的那个量」的第 N 次现场：
    #     "能开工"回答的是"还有没有槽可以试"，而这里要问的是
    #     **"这一趟值不值得开工"**。
    #   口径（三档，各自对应不同动作）：
    #     · `可开工` 有确认活的通道      → 正常开工
    #     · `仅剩未确认的槽`              → **默认不开工**（写了 20 个文件也大概率
    #        一个都拿不到），但调用方可以显式 `force=True` 逼它试一次
    #     · `无通道可用`（全硬死）        → 直接拒跑，force 也不许
    # ★ R282c（真读数）：**闸要按"能不能派"判断，不是按档名字符串。**
    #   实测 `channel="不存在的通道__r282"` 时，`体检` 返回
    #     {"档": None, "能开工": False, "error": "ValueError: 未知频道 …"}
    #   而我的闸只比 `_档 == "无通道可用"` ⇒ None 不等于它 ⇒ **放行** ⇒
    #   照样往下派模型跑到超时。
    #   ⇒ 同一个病（判据只看自己认识的那个值）：**答案在 `能开工` 上，我却去比档名。**
    #   口径：`能开工` 是硬否决（连配置都读不出来、或全硬死），
    #         `仅剩未确认的槽` 是软否决（可 force 绕过）。
    _档 = 体检.get("档")
    _能开工 = bool(体检.get("能开工"))
    if (not _能开工) or (_档 == "仅剩未确认的槽" and not force):
        return json.dumps({
            "ok": False,
            "停因": "开工前体检查出无通道可用",
            "blocked_on_model": True,
            "用了几轮": 0,
            "体检": {"档": 体检.get("档"), "结论": 体检.get("结论"),
                     "汇总": 体检.get("汇总"), "摘要": cd.体检摘要(体检)},
            "拒跑": ("no_channel_before_start"
                    if (_档 == "无通道可用" or not _能开工)
                    else "no_live_channel_before_start"),
            "诚实边界": ("**一个文件都没改，仓保持原状。**"
                      "这不是'试过了没成'，是'开工前就知道派不出模型'。"
                      "按「该做什么」那一栏动手后再调。"),
        }, ensure_ascii=False, indent=2)

    日志: list[str] = []
    改码 = mc.模型改码器(计划, 通道=channel,
                       日志=lambda m: 日志.append(str(m)))
    _根们 = [x.strip() for x in 源码根.split(",") if x.strip()] or None
    try:
        果 = gi.iterate(计划, 根, 改码=改码,
                        max_rounds=max(1, min(int(max_rounds), 8)), 源码根=_根们)
    except Exception as e:                          # noqa: BLE001
        return json.dumps({"ok": False,
                           "error": f"驱动自身异常：{type(e).__name__}: {e}"},
                          ensure_ascii=False)
    果["cwd"] = str(根)
    果["通道"] = channel or "（全通道轮转）"
    果["模型日志"] = 日志
    果["开工前体检"] = {"档": 体检.get("档"), "摘要": cd.体检摘要(体检)}
    # ★★★★★ R285（真读数，2026-11-07）：**达成时应当自带证据，而不是让调用方再验一遍。**
    #   前几轮把可重复性做成了机器可判的（repeatability，真 cargo 连跑 3 轮），
    #   但它当时**只有我手调过**——调用方拿到 ok=True 仍然不知道
    #   "这台机器侧到底可不可重复"。
    #   ⇒ 达成时顺手跑一次机器侧自证，把结论直接放进回执。
    #   ★ 边界（诚实，必须写）：这个自证用的是**本仓内置的 rust 小样本**，
    #     证明的是**机器侧链路（定位→应用→复跑）可重复**，
    #     **不**证明"你这个目标被重复执行也会一致"——那要重跑你的目标才行，
    #     而重跑你的目标可能很贵，所以这里不擅自做。
    if 果.get("ok"):
        try:
            _证 = rpt.多文件可重复(轮数=3)
            果["机器侧自证"] = {
                "ok": _证.get("ok"), "测了没": _证.get("测了没"),
                "结论": _证.get("结论"),
                "适用边界": ("证明**机器侧链路**（定位→应用→独立复跑）可重复；"
                          "**不**证明你这个目标重复执行也一致。"
                          "要后者请重跑本工具并比对回执。"),
            }
        except Exception as e:                       # noqa: BLE001
            果["机器侧自证"] = {"ok": False, "测了没": False,
                              "结论": f"自证自身异常：{type(e).__name__}: {e}",
                              "适用边界": "异常 ≠ 不可重复；这是没测到。"}
    return json.dumps(果, ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("build_project_graph"))
def build_project_graph(repo: str, 上限文件数: int = 0, 存到: str = "") -> str:
    """**给一个文件夹，自动建这个项目的知识图谱**（定理/引理/定义/文档/函数）。

    ★★★★★ R296：为什么必须有它（用户当面指出的根本缺陷，2026-11-06）：
      「一个数据库有功能，但是不能存任何数据、不能查任何数据，有什么意义？」
      「没有自动构建的过程，就等于数据从来不入系统内部。」
      「换一个项目就要重新手写？那有啥意义？」

      改之前的真读数：
        · 编排器的索引根是 `products/docs` —— **只索引它自己产出的回执**；
        · 项目本体（`形式化验证/` 下 22,839 个 .lean、224 MB）**从未入索引**；
        · `search_docs("密码")` → 0 条、`search_docs("theorem")` → 0 条，
          而 `auto_scan` 明明数得出 4 万条形式化单元。
      ⇒ **「数得出来」不等于「进得了系统」；「索引里有」不等于「查得到」。**

    ★ 口径三条：
      ① **机械优先、绝不漏**：theorem/lemma/def/structure、文档标题、代码签名
         全用正则抽，**不调模型**；
      ② **语义可降级**：模型不可用时构建照样完成，语义字段标 `未测到`；
      ③ **覆盖度必须报数**：发现/已解析/跳过/读不了各多少，逐条可查。

    repo: 项目根（绝对路径）。0 上限 = 全量。
    真读数（`形式化验证/`，本机）：25,755 文件 → **535,478 单元**
      theorem 385,921 / def 94,909 / instance 22,466 / doc 10,889 /
      structure 7,881 / abbrev 7,304 / class 3,804 / func 1,604 / axiom 544
      覆盖率 96.49%（读不了 0，解析失败 251）
    """
    import json as _json
    try:
        from . import project_graph as _pg296
    except Exception as e:                      # noqa: BLE001
        return _json.dumps({"ok": False, "错": f"模块不可用：{e}"}, ensure_ascii=False)
    果 = _pg296.构建(repo, 上限文件数=int(上限文件数 or 0))
    if not 果.get("ok"):
        return _json.dumps(果, ensure_ascii=False)
    # ★ R297：「建完了没有」必须有标准，不能只给一个覆盖率数字
    判 = _pg296.构建完毕判定(果)
    果["建完判定"] = 判
    到 = 存到 or str(__import__("pathlib").Path(repo).resolve().parent
                     / "_project_graph.json")
    存 = _pg296.存(果, 到)
    return _json.dumps({
        "ok": True, "根": 果["根"], "耗时秒": 果["耗时秒"],
        "读数": 果["读数"], "覆盖": 果["覆盖"], "语义": 果["语义"],
        "建完判定": 判,
        "失败分诊": _pg296.失败分诊(果, repo),
        "已存": 存,
        "下一步": f"用 query_project_graph(图谱={到}, 关键词=...) 查",
    }, ensure_ascii=False)


@mcp.tool(name=_TOOL("graph_completeness"))
def graph_completeness(图谱: str, 主主题词: str = "") -> str:
    """**「这个项目的知识图谱建完了没有」—— 四道门，机器判定。**

    ★★★★★ R297：用户问（2026-11-06）：「知识图谱怎么算全局构建完毕？有没有标准？」

      改之前只有一个 `覆盖率: 0.9649` —— **它说明不了"建完了没有"**：
        · 96.49% 听起来高，但那 3.51% 里有没有整类文件？看不出；
        · 251 个"解析失败"里，是**不需要索引的锁文件**，还是**我解析器漏了**？分不出。

      ⇒ 定四道门，**全过才算建完**：
        门1 无遗漏解释 —— 每个没进图的文件都要有可归类原因（"不认识的类型"不算）
        门2 无解析崩溃 —— 解析器崩了必须 0（崩了是我写错，不是文件的问题）
        门3 关键类型齐备 —— 项目里实际存在的语言类，每类都必须抽出了单元
        门4 可回答 —— 给定主主题词必须查得到（图建了但查不到 = 没建）

      真读数（`形式化验证/`，本机）：四道门全过，**完毕=True**，
      解析失败 **0**、分诊 `{'不需要索引': 20, '补解析器': 0, '问模型': 0, '人工看': 0}`。

    ★ 覆盖率**不是**判定标准，只作参考 —— 结果里明写这一条。
    主主题词: 逗号分隔，如 "密码学,自主认知,结构本能"（给了才测门4）
    """
    import json as _json
    try:
        from . import project_graph as _pg296
    except Exception as e:                      # noqa: BLE001
        return _json.dumps({"ok": False, "错": f"模块不可用：{e}"}, ensure_ascii=False)
    图 = _pg296.载(图谱)
    if not 图:
        return _json.dumps({"ok": False, "错": f"图谱载不进来：{图谱}"},
                           ensure_ascii=False)
    词 = [w.strip() for w in (主主题词 or "").replace("，", ",").split(",") if w.strip()]
    return _json.dumps(_pg296.构建完毕判定(图, 主主题词=词 or None),
                       ensure_ascii=False)


@mcp.tool(name=_TOOL("graph_triage"))
def graph_triage(图谱: str, repo: str) -> str:
    """**没进图谱的文件要分诊**：不需要索引 / 补解析器 / 问模型 / 人工看。

    ★ R297：用户问「解析失败的为什么不会调用模型查？」

      答：**因为大部分失败根本不该问模型。**
        · `.lock.json` / `lakefile.toml` —— 不是知识载体，模型也只会说"这是锁文件"；
        · 解析器漏了一个语言 —— **补解析器**，问模型是浪费且结果不可复用；
        · 只有**未知格式**才该问模型。

      这条口径的来历是真事故：改之前分诊把 `.lean.bak726`（备份）判进"问模型"10 个
      —— 问模型看一份旧版快照讲了什么，纯属浪费，入图还会污染。

    真读数（`形式化验证/`）：`{'不需要索引': 20, '补解析器': 0, '问模型': 0, '人工看': 0}`
    即 **本地不需要任何模型介入**（"问模型 0" 是因为没有真需要语义的文件，
    不是"不需要模型"这个能力）。
    """
    import json as _json
    try:
        from . import project_graph as _pg296
    except Exception as e:                      # noqa: BLE001
        return _json.dumps({"ok": False, "错": f"模块不可用：{e}"}, ensure_ascii=False)
    图 = _pg296.载(图谱)
    if not 图:
        return _json.dumps({"ok": False, "错": f"图谱载不进来：{图谱}"},
                           ensure_ascii=False)
    return _json.dumps(_pg296.失败分诊(图, repo), ensure_ascii=False)


@mcp.tool(name=_TOOL("query_project_graph"))
def query_project_graph(图谱: str, 关键词: str, 种类: str = "", 上限: int = 20) -> str:
    """**在已建好的项目知识图谱上查**（不再遍历目录）。

    ★ R296：这是"这个产品有没有用"的那个出口。
      真读数（本机，`形式化验证/` 全量图谱）：
        · 查「密码学」→ **373 条定理**（`rsa_factorization`、`cryptanalysis_instant`…）
        · 查「自主认知」→ **184 条定理**（`const_rule_no_reuse`…，其中带确认结论）
        · 查「结构本能」→ **42 条定理**
        · 多词 OR：「记忆 网络 遗忘」→ 1,352 条
      ★ 而改之前这些查询**全部返回 0**。

    ★ 0 命中必须先分清「真没有」与「我没索引到」：
      结果里带 `覆盖`；覆盖率 < 1 或有「读不了」时，0 命中可能是后一种。

    图谱: `build_project_graph` 返回的 `已存.路径`。
    种类: theorem / def / doc / func / structure / instance / axiom…（空=全部）
    """
    import json as _json
    try:
        from . import project_graph as _pg296
    except Exception as e:                      # noqa: BLE001
        return _json.dumps({"ok": False, "错": f"模块不可用：{e}"}, ensure_ascii=False)
    图 = _pg296.载(图谱)
    if not 图:
        return _json.dumps({"ok": False,
                            "错": f"图谱载不进来：{图谱}（先跑 build_project_graph）"},
                           ensure_ascii=False)
    果 = _pg296.查(图, 关键词, 种类=种类, 上限=int(上限))
    果["图谱根"] = 图.get("根")
    果["覆盖"] = 图.get("覆盖")
    return _json.dumps(果, ensure_ascii=False)


@mcp.tool(name=_TOOL("reverse_lookup"))
def reverse_lookup(图谱: str, 名字: str) -> str:
    """**反查**：这个名字定义在哪、被谁用到过。

    ★ R296：这是"图"相对于"文件搜索"的独占价值 ——
      文件系统只能回答"哪里出现这个词"，图能回答"谁依赖它"。
    """
    import json as _json
    try:
        from . import project_graph as _pg296
    except Exception as e:                      # noqa: BLE001
        return _json.dumps({"ok": False, "错": f"模块不可用：{e}"}, ensure_ascii=False)
    图 = _pg296.载(图谱)
    if not 图:
        return _json.dumps({"ok": False, "错": f"图谱载不进来：{图谱}"},
                           ensure_ascii=False)
    return _json.dumps(_pg296.反查(图, 名字), ensure_ascii=False)


@mcp.tool(name=_TOOL("audit_delivery"))
def audit_delivery() -> str:
    """**整体交付对账**：把「已证明什么 / 还没测到什么」一次说清，机器可复跑。

    ★★★★★ R289：为什么必须有它（真读数，2026-11-06）：
      这个项目的目标是「能无人值守地在任意真实仓上完成多文件/跨文件工程目标」。
      做了 36 轮之后，**没有任何一个地方能回答**："现在到底证明了哪几条？还差哪几条？"
      每轮回执只讲那一轮的一件事，读三份回执也拼不出全貌。
      而这个产品最怕的正是**把"没测到"当成"做成了"**。

    ⇒ 本工具逐条**真跑一遍**，把每条钉死在 `已证明 / 没测到 / 不成立` 三档之一。

    ★ 真读数（本机，2026-11-06，通道已修通）：
        合计: **{'已证明': 8, '没测到': 0, '不成立': 0}**
        已证明：任务切分 / 真落主树+可独立复跑 / 失败归因 / 通道降级 /
                目标树 / 可重复性·机器侧 / **真模型无人值守把多文件改对** /
                装完就能跑（wheel 形态）

    ★★★ R298：最后那条「真模型无人值守把多文件改对」之前**永远报"没测到"**，
      因为判它的人写的是 `_档(None if not 上游活 else None, ...)`
      —— **两个分支都是 None**，条件变了也不重测。
      那是让作者心安的标签，不是判据。
      现在：上游活 ⇒ **真跑一次**（派模型改真文件，机器门判对错）。
      真读数：`通道 ['qww:deepseek-v4.1-flash']；真跑机器门 rc=0；改对了`。
      ⇒ 口径：**「缺条件」这个结论必须有到期条件。**

    ★ 我第一版这里**会骗人**：把"可重复性"写成一条，依据文字里写着"模型侧没测到"，
      但那一档是按**机器侧**判的 ⇒ 合计报 `没测到: 0`。
      **一条只能回答一个问题**；否则"已证明"会把"没测到"盖住 ——
      而这正是本产品最怕的那种骗。现已拆成两条。

    ★ 诚实边界：
      · 对账**只报它真跑过的**。要上游活着才能测的，就写 `没测到`，
        **绝不**因为"代码写完了"就算证明；
      · `没测到`（缺条件）与 `不成立`（测了没过）是**两件事**，不许混。
    """
    try:
        return json.dumps(adt.对账(), ensure_ascii=False, indent=2)
    except Exception as e:                          # noqa: BLE001
        return json.dumps({"ok": False,
                           "error": f"对账自身异常：{type(e).__name__}: {e}",
                           "诚实边界": "异常 ≠ 各项都对；这是没测到。"},
                          ensure_ascii=False)


@mcp.tool(name=_TOOL("field_contract"))
def field_contract_check() -> str:
    """**跨模块字段契约**：核对"我以为的形状"和"生产者真产出的形状"是否一致。

    ★★★★★ R287：为什么值得专门做个工具（真读数，五轮里栽了 **4 次**）：
      这几轮的错**全是同一句**「不要猜数据的形状，去读它的生产者」：
        · R280e 读错层：`client.timeout`（真的在它的上一层）
        · R281c 猜常量值：`硬死` 其实是 `'dead'`（我以为值就是中文）
        · R283b 读错层 + 猜键名：`复验.段` 不在顶层，键是 `tid` 不是 `id`
        · R286b 猜字段含义：证据里的 `file` 是 `"repo"`（**仓目录名**），不是文件路径
        · R286c 猜容器类型：`门` 是 `{"grep_required": false}`（dict），不是列表
      五次全是我**在别人的产出上瞎猜**，而且每次代码都能跑、不报错、看着合理，
      **只是判断全都反了**。这是本项目最贵的一类错。

    ⇒ 本工具**不查文档、不读注解**，只**真跑一遍生产者**，逐条核对表里写的形状。
      形状一变，这里就红 —— 而不是某个下游悄悄判反、还报绿。

    ★ 它自己抓到过我自己写错的契约（真读数）：契约第一版写「`复验` 是 dict」，
      实测 `adopted=None` 时 `复验`/`应用` 是 **`None`**（不是空 dict）。
      下游若 `果["复验"]["段"]` 会 TypeError；更坏的是用 `or {}` 静默吞掉
      ⇒ 报"没有失败"（R286c 就报了假绿）。

    ★ 诚实边界：本表**只覆盖列出的字段**，不是全部协议。它降低"又猜错"的概率，
      **不消灭**它 —— 新增跨模块读取时，必须同时往表里加一行。
    """
    try:
        _r = fcl.校验()
        return json.dumps({
            "ok": _r["ok"], "过": _r["过"], "错": _r["错"],
            "契约": fcl.表(),
            "诚实边界": ("只覆盖表里列出的字段，不是全部协议。"
                      "新增跨模块读取时必须同时往表里加一行，否则这条齿管不到。"),
        }, ensure_ascii=False, indent=2)
    except Exception as e:                          # noqa: BLE001
        return json.dumps({"ok": False,
                           "error": f"契约核对自身异常：{type(e).__name__}: {e}",
                           "诚实边界": "异常 ≠ 形状对；这是没测到。"},
                          ensure_ascii=False)


@mcp.tool(name=_TOOL("explain_failure"))
def explain_failure(goal_json: str, cwd: str) -> str:
    """**失败归因**：目标没做成时，分清是"谁的错"——因为下一步动作完全不同。

    ★★★★★ R286：为什么必须有它（真读数，2026-11-06）：
      既定目标点名了「失败归因」，而此前只有**通道级**的判定
      （`_classify_failure` 判"硬死 / 读不到"）。**工程失败本身**完全没有归因：
      一次目标没做成，回执里长得都一样，只有一句"未达成"。
      而实测四种失败**下一步动作完全互斥**：

      | 档 | 责任 | 该做什么 |
      |---|---|---|
      | 无法启动 / 环境缺工具 | 环境 | 装工具（**改代码和改判据都没用**） |
      | 判据写错（规格非法） | 判据 | 改判据（**改代码没用**） |
      | 代码没改对 | 代码 | **这才是该改代码的时候** |
      | 未知 | 未知 | 别猜，把原文贴出来 |

    ★ 我第一版把这件事**全判错了**：我拿证据里的 `file` 字段当"文件路径"，
      而它实际是 `"repo"`（**仓目录名，表示被测对象**）⇒ 后三种**全被归成"判据写错"**。
      最坏的是"真代码错 → 判据写错"：调用方会去改一个**好好的判据**，
      而真正的错在代码里，怎么改都不会绿。
      ⇒ 判据自己已经把话说清了，**去读 `cause`**：
        `门规格非法|读不到|in=…（不存在或不是文件）` 才是判据写错的凭据；
        `必需缺失|X` 是代码没改对。

    ★ 诚实边界：归因是**从证据里读出来的**（cause/err/rc/文件是否存在），
      **不是**推断"模型哪里想错了"。认不出的一律归 `未知` ——
      **一个错的归因比没有归因更坏**，它会让人去改一个没坏的地方。

    ★ 这是只读的：**不调模型、不改仓**。它跑目标仓自己的门然后归因。
    """
    try:
        规 = json.loads(goal_json)
    except ValueError as e:
        return json.dumps({"ok": False, "error": f"goal_json 不是合法 JSON：{e}"},
                          ensure_ascii=False)
    根 = Path(cwd or ".").resolve()
    if not 根.is_dir():
        return json.dumps({"ok": False, "error": f"cwd 不是目录：{cwd}"},
                          ensure_ascii=False)
    try:
        计划 = ag.plan_tasks(规)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    # 逐段跑它自己的门，收集证据再归因（复用 goal_runner，不另起一把尺子）
    try:
        from . import generic_goal as _G  # noqa: F401
        判 = gr.run_plan(计划, 根)
    except Exception as e:                          # noqa: BLE001
        return json.dumps({"ok": False,
                           "error": f"归因自身异常：{type(e).__name__}: {e}"},
                          ensure_ascii=False)

    # ★★★★★ R286c（真读数，2026-11-06）：**这里我第一版报了假绿。**
    #   我写的是 `for _g in (_段.get("门") or [])` —— 以为 `门` 是"证据字典的列表"。
    #   实测 `run_plan` 返回的是：
    #     `"门": {"grep_required": false}`   ← **一个 dict，值是布尔结论**
    #   遍历一个 dict 拿到的是 **键名（字符串）**，`isinstance(_g, dict)` 恒假
    #   ⇒ `_证据` 恒空 ⇒ `归因一批([])` ⇒ `ok=True` 而 `目标达成吗=False`
    #   ⇒ **工具对外报"没有失败"，而目标其实红着。** 这比归因错误更糟：
    #     它让调用方以为一切正常。
    #   ⇒ 两个修法一起上（缺一个都不够）：
    #     ① `门` 是 dict，按 dict 读；
    #     ② 但 `run_plan` **只给布尔结论，不给 cause/err** ——
    #        而归因的**全部原料**都在 cause 里 ⇒ 必须**重跑** `gate_spec.evaluate`
    #        拿真实明细。这正是「一个错的归因比没有归因更坏」的反面：
    #        **没有原料就不要归因，宁可说"没归因到"。**
    _证据: list = []
    from . import gate_spec as _gs
    from . import goal_runner as _gr2
    for _段 in (判.get("段") or []):
        if _段.get("状态") == "绿":
            continue
        _规格 = _段.get("_门规格") or []
        if not _规格:
            continue
        try:
            _r2, _d2 = _gs.evaluate(_规格, 根, 根)
        except Exception:                            # noqa: BLE001
            continue
        for _e in (_d2 or []):
            if isinstance(_e, dict) and not _e.get("ok"):
                _证据.append(_e)
    归 = fc.归因一批(_证据, 根)
    # ★ 兜底（不许再报假绿）：有红段却一条证据都没拿到 ⇒ 明说"没归因到"
    _有红 = any((x.get("状态") in ("红", "拒跑", "异常"))
                for x in (判.get("段") or []))
    if _有红 and not 归["逐条"]:
        归 = {**归, "逐条": [],
              "汇总": {"未知": ["（一条证据都没取到）"]},
              "最该先做的": ("**没归因到**：有段是红的，但没取到门的明细。"
                          "这是归因链自己断了，不是'没有失败'。"),
              "诚实边界": "归因链断了 ⇒ 必须如实说，不许报'没有失败'。"}
    return json.dumps({
        "ok": not 归["逐条"],
        "目标达成吗": 判.get("ok"),
        "未达成的原因": 判.get("未达成的原因"),
        "归因": 归,
        "拒跑": None,
    }, ensure_ascii=False, indent=2)


@mcp.tool(name=_TOOL("check_repeatable"))
def check_repeatable(rounds: int = 3, lang: str = "rust") -> str:
    """**机器侧可重复性自证**：同一目标从全红基线连跑 N 轮，看结论是否逐轮一致。

    ★★★★★ R285：为什么它必须是**可调用的工具**而不是我手跑一次：
      「可重复性」是既定目标里点名的一项，而此前它只在 case02 上**偶然**验过一次
      （无人值守跑通 1 次、重复 4 次失败 = **1/4**）。判十五只在**单文件**上连跑，
      那证明不了多文件。
      ⇒ 现在换成：本机真工具链，仓里同时有**两处独立编译错误**，每轮从全红基线出发，
        要求①改动面**同时**定位到两个文件 ②每轮面/保留数/独立复跑 rc 逐轮一致。

    ★ 真读数（本机，2026-11-07）：
      `连跑 3 轮：改动面 ('src/alpha.rs','src/beta.rs')、保留 2、复跑 rc 0 —— 可重复`
      反向验证：把定位截断成只取第 1 个文件 ⇒
      `ok=False · 第 1 轮复跑仍红（改动没让目标仓自己的门变绿）`
      ⇒ 这条齿**真的能抓到"改了一半"**，而"少做"最容易伪装成"做成了"。

    ★ 诚实边界（写在返回值里，不许含糊）：
      本工具**不调模型**。它证明的是**机器侧**（定位→应用→独立复跑）可重复，
      **不**证明**模型侧**可重复——后者要上游活着才测得到。
      本机没有 cargo ⇒ 报 `测了没=False`（**没测到**），**不算通过**。

    参数：rounds=连跑几轮（建议 ≥3）；lang 目前只支持 rust（其余如实说"没验过"）。
    """
    try:
        return json.dumps(rpt.多文件可重复(轮数=max(1, min(int(rounds), 10)),
                                         语言=str(lang or "rust")),
                          ensure_ascii=False, indent=2)
    except Exception as e:                          # noqa: BLE001
        return json.dumps({"ok": False, "测了没": False,
                           "error": f"自证自身异常：{type(e).__name__}: {e}",
                           "诚实边界": "异常 ≠ 不可重复；这是没测到。"},
                          ensure_ascii=False)


@mcp.tool(name=_TOOL("doctor_channels"))
def doctor_channels(channel: str = "", timeout: float = 25.0) -> str:
    """**开工前通道体检**：逐 (通道, 模型) 对真探活，给出"为什么不能用、该谁动手"。

    ★★★★★ R281：为什么非要它（真读数，2026-11-07）：
      上一轮把探活判准了（`HTTP 200 + {"error":…}` 不再算活），但那份结论
      **只活在一次调用的异常信息里**。真正无人值守时第一次失败往往发生在
      **跑到一半**——模型改到第 3 个文件才没通道，前面全白做。
      ⇒ 开工前必须有一张**结构化体检表**，把"哪种死法、责任在谁、该做什么"讲清。

    ★ 三种死法的**责任方完全不同**，混成一档会让人做错决定：
      · `缺key`     → 你：设 `*_API_KEY`（本机 3 个通道是这个）
      · `无权访问`  → 你：换有该分组的 key（本机 `dddddd` 是 403 无权访问分组）
      · `上游异常`  → 上游：只能等，本地不重试（与 R166 一致：慢错重试拿不到新信息）

    ★ 与 `doctor.py` 安装自检的分工：那个**不联网**（装完就能跑），本工具**真探活**。

    ★ 诚实边界（写在返回值里）：探活是**启发式**，Router 有换槽与冷却，
      **真调用才是判据**。`未探测` 档是「没测到」，不等于「测过不行」。
      `可开工` 与 `能开工` 是**两件事**：前者=有通道被确认是活的，
      后者=还有槽可试（兜底，不是保证）。
    """
    try:
        表 = cd.体检(channel or "", timeout=float(timeout))
    except Exception as e:                          # noqa: BLE001
        return json.dumps({"ok": False,
                           "error": f"体检自身异常：{type(e).__name__}: {e}"},
                          ensure_ascii=False)
    表["摘要"] = cd.体检摘要(表)
    return json.dumps(表, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════
# 自检：MCP 入口的**结构性**守卫（不连网、不起服务）
# ══════════════════════════════════════════════════════════════════════
def _未定义全局名(mod) -> list:
    """★★★★★ 静态门：找出模块里**被读却从未定义**的全局名。

    ★ 为什么需要它（这是本轮 R3 的**根因**）：
      `_RUST_ROOT` 从未定义，却被 `run_errand` 引用两次 ⇒ 工具一调就 `NameError`。
      这类缺陷**逃过了所有运行期判据门** —— 因为那些门从不调用这个工具。
      ⇒ 只有**静态**判据能抓住它。

    ★ 判法（语言无关的「读」）：扫字节码，取 `LOAD_GLOBAL` 的名字，
      扣掉模块全局、内建名、以及 `from x import *` 的可见名 ⇒ 剩下的就是未定义。
      这**不是**启发式：它就是解释器执行时查表的**同一个集合**。
    """
    # ★ R7：复用共享静态门（同一判据只有一处实现，避免"一处修了三处没修"）
    from agentscope_orchestrator import static_audit as _sa
    return _sa.undefined_globals(mod)


def _selftest() -> int:
    # ══════════════════════════════════════════════════════════════
    """MCP 入口自检：结构性守卫，全部机器可算、不连网、不起服务。"""
    fails: list[str] = []

    # ══════════════════════════════════════════════════════════════
    # 【R224】MCP 工具名必须合规（ASCII）
    # ══════════════════════════════════════════════════════════════
    # 真证据（2026-10-29 实跑 list_tools）：16 个工具名曾全是 `示例·任意任务`，
    #   SDK 原文「may cause compatibility issues」⇒ 任意标准客户端都调不动。
    try:
        _合规224 = re.compile(r"^[A-Za-z0-9_.-]+$")
        _源224 = Path(__file__).read_text(encoding="utf-8")
        _短224 = re.findall(r'@mcp\.tool\(name=_TOOL\("([^"]+)"\)\)', _源224)
        for _s224 in _短224:
            if not _合规224.match(_s224):
                fails.append(f"【R224】★★ 工具短名不合规（{_s224}）⇒ 客户端调不动")
        # ★ 2026-10-31（交付审计 P0-3）：这里原来写死 `!= 18`。
        #   工具加到 22 个之后，**自检自己先红了** —— 而这条命令正是交付说明里的第一条自证。
        #   同类"计数漂移"台账已记过两次（0.9.2、0.12.3），这是第三次 ⇒ 治根，不治数：
        #   ① 装饰点数量必须等于**真正注册进 FastMCP 管理器**的数量（两边都从代码算）；
        #   ② 再留一条**下限**，防"注册点被整段删掉"时两边一起缩水而没人发现。
        _注册224 = set(getattr(getattr(mcp, "_tool_manager", None), "_tools", {}) or {})
        chk_holder = []          # chk 定义在下面，这里先攒着，随后统一判
        chk_holder.append(("tool_registry_matches_decorators", len(_注册224) == len(_短224),
                           f"装饰点 {len(_短224)} 个，注册进 manager 的 {len(_注册224)} 个"
                           f" ⇒ 有工具写了却没暴露给客户端"))
        chk_holder.append(("tool_count_floor", len(_短224) >= 20,
                           f"工具数骤降到 {len(_短224)}（曾 22）⇒ 有注册点被误删"))
        _未注册224 = [s for s in _短224
                      if not any(k == s or k.endswith("__" + s) or k.endswith("_" + s)
                                 for k in _注册224)]
        chk_holder.append(("every_decorator_registered", not _未注册224,
                           f"这些短名没有对应注册名：{_未注册224}"))
    except Exception as _e224:
        fails.append(f"【R224】★ 齿跑不起来：{_e224}")
        chk_holder = []
    n = 0

    def chk(name: str, cond: bool, why: str = "") -> None:
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{name}：{why}")

    # ★ R224 的三条判据（在 chk 定义前就算好了，这里统一记账）
    for _名224, _cond224, _why224 in chk_holder:
        chk(_名224, _cond224, _why224)

    # ★★★★★ 门①（**本轮 P0 的判据力探针**）：模块里不得有未定义的全局名。
    #   这条门若早存在，`_RUST_ROOT` 根本进不了主干。
    _缺 = _未定义全局名(sys.modules[__name__])
    chk("no_undefined_global", not _缺,
        f"存在被读却未定义的全局名 {_缺} ⇒ 相关工具一调就 NameError")

    # ★ 门②：这条门**自己必须有效** —— 造一个真含未定义名的模块，必须被抓出。
    #   （否则「零未定义名」可能只是门没在工作 —— 恒真的门比没有门更坏。）
    import textwrap
    import types as _types
    _假 = _types.ModuleType("_假模块")
    exec(compile(textwrap.dedent("""
        def f():
            return _这个名从未定义 + 1
    """), "<假>", "exec"), _假.__dict__)
    _假.__name__ = "_假模块"
    chk("undefined_global_gate_has_teeth", bool(_未定义全局名(_假)),
        "静态门对含未定义名的模块返回空 ⇒ 门失效（恒真）")

    # ★ 门③：`_RUST_ROOT` 必须是**真存在的目录**（否则默认 cwd 不成立）。
    #   ★★ 写法要点：必须用 `globals().get(...)`，**不能直接写 `_RUST_ROOT`** ——
    #      否则一旦它真的未定义，自检自己先 `NameError` 崩掉，
    #      **那条 FAIL 永远打不出来**。自检必须对它所查的缺陷**免疫**。
    #      （本探针第一次跑正是如此：门①抓到了，门③先崩，FAIL 一条没打出来。）
    _rr = globals().get("_RUST_ROOT")
    chk("rust_root_is_a_real_dir",
        isinstance(_rr, (str, Path)) and Path(_rr).is_dir(),
        f"_RUST_ROOT={_rr!r} 不是目录 ⇒ run_errand 默认 cwd 不成立")

    # ★ 门④：`run_errand` 的源码里，`_RUST_ROOT` 必须已在模块层定义。
    import inspect
    _src = inspect.getsource(run_errand)
    chk("run_errand_rust_root_defined",
        "_RUST_ROOT" not in _src or "_RUST_ROOT" in vars(sys.modules[__name__]),
        "run_errand 引用了未定义的 _RUST_ROOT")

    # ★ 门⑤：命令安全闸必须**真的接在 MCP 入口上**（R2-F4）。
    for _fn, _nm in ((run_errand, "run_errand"), (submit_task, "submit_task")):
        chk(f"cmd_safety_wired_{_nm}", "check_cmd_safety" in inspect.getsource(_fn),
            f"{_nm} 未接命令安全闸 ⇒ 可提交任意可执行/越界 cwd")

    # ★ 门⑥：`_dep_state` 必须**不把 staged 当成功**（R2-F3）。
    _d = inspect.getsource(_dep_state)
    chk("staged_is_not_dependency_success",
        "st == \"promoted\"" in _d and 'in ("staged", "promoted")' not in _d,
        "依赖判定仍把 staged 当成功 ⇒ 下游在旧树上开跑")

    # ★ 门⑦：`_run_errand` 必须**无条件**把 cmd 编成门（R2-F2）。
    _e = inspect.getsource(_run_errand)
    chk("errand_always_runs_cmd",
        "if not 已含" in _e.replace("_已含", "已含")
        or ("cmd_exit_ok" in _e and "if not spec" not in _e),
        "gates 非空时 cmd 不跑 ⇒ 任务报绿但本体命令没执行")

    # ★★★★★ 门⑧（R8-F14）：计划必须带**可执行拓扑序** + 执行契约。
    #   这条门存在的意义：DAG 从"展示品"变成"执行序"。
    _plan = json.loads(auto_goal(json.dumps({
        "name": "计划门", "pre": ["a", "b"], "constraints": ["c"],
        "gates": [{"type": "compile", "cmd": ["x"], "cwd": ".", "expect_exit": 0},
                  {"type": "compile", "cmd": ["y"], "cwd": ".", "expect_exit": 0}],
        "acceptance": ["z"]}, ensure_ascii=False)))
    _t = _plan.get("topo") or []
    _位 = {tid: i for i, tid in enumerate(_t)}
    chk("plan_has_executable_topo",
        len(_t) == len(_plan["tasks"])
        and all(_位.get(d, 10 ** 9) < _位[t["tid"]]
                for t in _plan["tasks"] for d in (t.get("depends_on") or [])),
        f"计划缺可执行拓扑序或序不对：topo={_t}")
    chk("plan_has_execution_contract", "执行契约" in _plan,
        "计划未给出执行契约 ⇒ 调用方仍需自己猜顺序")

    # ★★★★★ 门⑨（R8-F13）：**单窗不得自称收敛**（如实标注）。
    #   收敛判据是「多窗一致对齐」（`_align_windows`），单窗没有交叉验证
    #   ⇒ 回执必须带 `cross_validated`，且单窗时 verdict 改标
    #     `converged_single_window`，不许拿"一条路走通"冒充"已收敛"。
    from agentscope_orchestrator import generic_goal as _gg
    _src_emit = inspect.getsource(_gg.GoalRunner._emit)
    chk("single_window_not_called_converged",
        "converged_single_window" in _src_emit and "cross_validated" in _src_emit,
        "单窗仍自称 converged ⇒ 拿'一条路走通'冒充'已收敛'")
    # ★ 门⑩：这条标注**必须真生效**（不是只写了字符串）—— 真跑一次 _emit 看回执。
    import tempfile
    with tempfile.TemporaryDirectory() as _td:
        _旧 = _gg._RECEIPT
        _gg._RECEIPT = Path(_td) / "r.jsonl"
        try:
            _g = object.__new__(_gg.GoalRunner)
            _g.goal = "自检"
            _g.n_windows = 1
            _g._emit({"kind": "iteration", "verdict": "converged"})
            _rec = json.loads(_gg._RECEIPT.read_text(encoding="utf-8").splitlines()[-1])
            chk("single_window_relabel_is_effective",
                _rec.get("verdict") == "converged_single_window"
                and _rec.get("cross_validated") is False,
                f"单窗标注未生效：verdict={_rec.get('verdict')} "
                f"cross_validated={_rec.get('cross_validated')}")
            _g.n_windows = 2
            _g._emit({"kind": "iteration", "verdict": "converged"})
            _rec2 = json.loads(_gg._RECEIPT.read_text(encoding="utf-8").splitlines()[-1])
            chk("multi_window_keeps_converged",
                _rec2.get("verdict") == "converged"
                and _rec2.get("cross_validated") is True,
                f"多窗被误改标：verdict={_rec2.get('verdict')}")
        finally:
            _gg._RECEIPT = _旧

    if fails:
        print("taixu_mcp 自检：失败")
        for f in fails:
            print("  [FAIL]", f)
        return 1
    print(f"taixu_mcp 自检：{n}/{n} 全绿")
    return 0


# ════════════════════════════════════════════════════════════════
# ★★★★★ 智能体可用层（agent-first）2026-10-30
# ════════════════════════════════════════════════════════════════
# 【为什么要有这一层 —— 判据换了主体】
#   以前的接口是**给人用的 CLI 形状**：提交 → 人去看控制台 → 人再决定。
#   但真正的消费者是**模型**（我自己）。对人友好的东西，对我常常是**信息黑洞**：
#     `read_report` 返回 "请看 http://127.0.0.1:8088/report/x" —— 我看不了网页；
#     `audit_log` 返回 "共 N 条，去控制台翻" —— 我看不到那 N 条。
#   于是每一条"请看控制台"都变成**一次额外往返**，或者干脆是**拿不到**。
#
# 【判据（智能体可用性，替代人类可用性）】
#   1 决策充分性：一次返回必须足以决定下一步；出现"请看控制台"即判不合格。
#   2 轮次成本  ：提交→结论的 MCP 调用次数，理想 = 2（orch_run + orch_wait）
#                 或 1（orch_run(wait=True)）。
#   3 自纠能力  ：失败必须带**可直接执行**的修法（工具名 + 参数），不是解释。
#   4 成本可读  ：每任务的调用数/载荷字节/烧字要能读数，否则"省了多少"是空话。
#
# 【本层不删旧工具】16 个细粒度手柄保留（我需要时按需索），
#   但**默认路径**必须是"一次提交 + 一次拿结论"。
# ════════════════════════════════════════════════════════════════

_成本账: dict[str, dict] = {}
# ★ 成本账原先只在**进程内存** ⇒ 服务一重启"省了多少"就归零 ⇒ 成本是装饰。
#   现在每次往返追加一行 jsonl，重启后可按任务聚合回来（文件名带 _DB_FILE 前缀，互不干扰）。
_成本文件 = Path(_DB_FILE).parent / "成本账.jsonl"
# ★ 熔断健康表与成本账同层：同一个"跨重启要记住的事"就放在同一个目录。
#   不设的话 router 拿不到落点 ⇒ 重启清零（已实测代价：第一个任务重付全部重试）。
# ★★★★★ R548（verifier 用机器对照抓出的真根因，R547 只修到了皮）：
#   这里原本是 `os.environ.setdefault("ORCH_STATE_DIR", str(Path(_DB_FILE).parent))`
#   —— 在**模块顶层**改全局环境变量。后果实测：
#       BEFORE: env=None          条数=16  库=<STATE>\kb
#       AFTER : env=…\jobs        条数=0   库=…\jobs\kb（不存在）
#   因为 `project_kb.库根()` 认 env ⇒ 只要 import 本模块，库根就被拐走。
#   于是 doctor 同一屏出现「知识库可机判 6/16 条」与「知识库 **0 条**」
#   自相矛盾（前者在被改之前算、后者在之后算），并且照样打「可以开工」。
#   ⇒ 违反口径 387：**库模块不许在 import 期改全局状态**。
#   修法：这里不设；在**进程入口** `main()` 设一次，值取**规范状态根** `STATE`
#   （不是 `jobs` 子目录 —— 子目录只是 jobs.db 的落点，不是状态根）。
_状态根提示548 = str(STATE)


def _成本落盘(job_id: str, 工具: str, 字节: int, 计入调用: bool = True) -> None:
    """★ `计入调用=False` 用于"只补载荷"的行：否则重启回放会把补记行数算成调用数
    （真事故：t1030_013 两次调用落成 4 行，回放会报"用了 4 次"）。"""
    try:
        with _成本文件.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps({"t": round(time.time(), 3), "task": job_id,
                                 "tool": 工具, "bytes": int(字节 or 0),
                                 "call": 1 if 计入调用 else 0,
                                 "pid": os.getpid()}, ensure_ascii=False) + "\n")
    except OSError:                       # 记账失败不许影响任务本身
        pass


def _成本回放(job_id: str) -> dict:
    """重启后从 jsonl 聚合某任务的调用数/载荷（按 pid 无关，纯求和）。"""
    调用 = 载荷 = 0
    工具们: list[str] = []
    try:
        with _成本文件.open("r", encoding="utf-8", errors="replace") as fp:
            for ln in fp:
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if d.get("task") == job_id:
                    载荷 += int(d.get("bytes") or 0)
                    # "（载荷补记）"行永远不是一次调用；旧行无 call 字段也要按工具名判
                    # （真事故：补记行被算成调用 ⇒ t1030_013 的 2 次调用被报成 6 次）
                    if str(d.get("tool", "")).startswith("（"):
                        continue
                    if int(d.get("call", 1)):
                        调用 += 1
                        工具们.append(str(d.get("tool")))
    except OSError:
        pass
    return {"调用": 调用, "载荷字节": 载荷, "工具序列": 工具们[-20:]}

# 终态判定：执行器写入的任何非 queued/running 状态都算终态。
# ★ 不用"枚举成功态"：枚举会随新增裁决态（stalled_fixed_point / diverging …）
#   静默漏判，漏判的后果是 orch_wait 永远等下去 —— 那是最坏的一种等待。
_非终态 = {"queued", "running"}

_基设词 = ("CIRCUIT_OPEN", "no healthy channel", "所有通道处于熔断","Connection", "Timeout", "timeout", "503", "502", "429", "model_not_found",
           "APIError", "不可用", "连接", "超时", "No space", "Errno 28")
_参数词 = ("锚定", "不存在", "拒跑", "越界", "安全闸", "参数", "为空", "缺少", "不合法")


def _成本累(job_id: str, 工具: str, 载荷字节: int = 0) -> None:
    """记一次 MCP 往返（这是"轮次成本"的唯一真值来源）。"""
    if not job_id:
        return
    c = _成本账.setdefault(job_id, {"调用": 0, "载荷字节": 0, "工具序列": []})
    c["调用"] += 1
    c["载荷字节"] += max(0, int(载荷字节 or 0))
    c["工具序列"].append(工具)
    _成本落盘(job_id, 工具, 载荷字节)


def _成本补(job_id: str, 载荷字节: int) -> None:
    """只记载荷、不增调用数（用于"先把返回拼好、量一下多大"）。"""
    if not job_id:
        return
    c = _成本账.setdefault(job_id, {"调用": 0, "载荷字节": 0, "工具序列": []})
    c["载荷字节"] += max(0, int(载荷字节 or 0))
    _成本落盘(job_id, "（载荷补记）", 载荷字节, 计入调用=False)


def _成本读(job_id: str, j: dict | None = None) -> dict:
    回放 = _成本回放(job_id)
    c = 回放 if 回放.get("调用") else (_成本账.get(job_id)
                                      or {"调用": 0, "载荷字节": 0, "工具序列": []})
    s = ((j or {}).get("status") or {})
    return {"MCP调用数": c["调用"], "载荷字节": c["载荷字节"],
            "工具序列": c["工具序列"],
            "模型token字符": s.get("tokens_chars")}


def _是终态(j: dict) -> bool:
    return (j.get("state") or "") not in _非终态


_成功态 = {"promoted", "staged", "converged", "converged_single_window"}


def _证据文(a) -> str:
    """把一条 audit 记录压成可读证据（orch 的 audit 是真字段：gate/ok/cause/rc/err）。"""
    if isinstance(a, str):
        return a
    if not isinstance(a, dict):
        return str(a)
    片 = []
    for 键 in ("cause", "rc", "err", "detail", "output", "msg", "message"):
        v = a.get(键)
        if v not in (None, ""):
            片.append(f"{键}={v}")
    return " · ".join(片) or json.dumps(a, ensure_ascii=False)


def _自动门(cmd: list, 根: Path, gates: list | None) -> list:
    """默认门表：测试类命令挂 `test_counts`（跳过不算绿），其余挂退出码门。

    为什么默认挂（2026-10-31）：`pytest` 的 `@skip` / `unittest` 的 `OK (skipped=1)`
    退出码都是 0 ⇒ 只判退出码时**把测试跳光就是绿**。默认门必须堵住这个洞。
    调用方若已显式给 `test_counts` 门，则不重复添加；想放宽可自己传
    `{"type":"test_counts","max_skipped":99}` 或 `{"type":"compile",...}` 覆盖。
    """
    from agentscope_orchestrator.gate_spec import 像测试命令
    给 = list(gates or [])
    # ★★ 2026-11-01：本体命令门**默认带写量配额**。
    #   理由不是"防坏人"，是**防自己人**：`gate_power` 真把临时目录落在被测仓内，
    #   自我复制 14 层、**126,673 个条目**、仓被搞脏、`git add` fatal。
    #   容器能防；一条"新增文件数/字节数"配额同样能防，且不改变任何执行语义。
    #   默认值刻意宽松（2 万文件 / 500 MB：构建产物不算数），只抓"疯了"那一档。
    #   调用方可用 `limits: {"off": true}` 关掉，或自定义上限。
    _限 = {"max_new_files": 20000, "max_new_bytes": 500 * 1024 * 1024}
    if any(str((x or {}).get("type")) == "test_counts" for x in 给):
        return [{"name": "cmd_exit_ok", "type": "compile", "cmd": list(cmd),
                 "cwd": str(根), "expect_exit": 0, "limits": _限}] + 给
    if 像测试命令(cmd):
        # ★ 只挂 test_counts（它自己也验退出码）：否则测试套件会被跑**两遍**
        return [{"name": "test_counts", "type": "test_counts", "cmd": list(cmd),
                 "cwd": str(根), "min_passed": 1, "max_skipped": 0,
                 "limits": _限}] + 给
    return [{"name": "cmd_exit_ok", "type": "compile", "cmd": list(cmd),
             "cwd": str(根), "expect_exit": 0, "limits": _限}] + 给


def _头尾(文: str, 上限: int) -> str:
    """超长证据保留**头 + 尾**。

    真证据（2026-10-30 标定语料 I1）：urllib 的 traceback 决定性一行
    `Connection refused` 在**末尾**；只留头部 ⇒ 强基设特征被截掉 ⇒
    「基础设施不可用」被误判成「产物未达标」——让执行侧替环境背锅。
    """
    if len(文) <= 上限:
        return 文
    尾 = max(200, 上限 // 3)
    头 = 上限 - 尾
    return 文[:头] + "\n…（中略 " + str(len(文) - 上限) + " 字符）…\n" + 文[-尾:]


def _门们(s: dict, 上限: int = 1500) -> list[dict]:
    """把 status 里的门记录归一成 [{门, 通过, 证据}]。

    ★★★★★ 真证据（2026-10-30 实跑 t1030_144）：两种形态都真实存在 ——
      工程任务：`gates = {"cmd_exit_ok": true}`（**dict**），证据在
                `audit = [{"gate","ok","cause","rc","err"}]`；
      形式化任务：`gates` 才是 list。
      早先只按 list 写 ⇒ 一次真跑就崩；修成"非表 ⇒ 未达标"更糟 ——
      **把 state=promoted 的成功任务报成未达标**。崩溃看得见，错判看不见。
    """
    出: list[dict] = []
    g = (s or {}).get("gates")
    if isinstance(g, dict):
        证映射 = {}
        for a in ((s or {}).get("audit") or []):
            if isinstance(a, dict):
                证映射[a.get("gate") or a.get("门") or ""] = a
        for 名, 过 in g.items():
            a = 证映射.get(名)
            文 = _证据文(a) if a is not None else ""
            if len(文) > 上限:
                文 = _头尾(文, 上限)
            出.append({"门": str(名), "通过": bool(过), "证据": 文})
        return 出
    for x in (g or []):
        if isinstance(x, dict):
            名 = (x.get("name") or x.get("门") or
                  (f"w{x.get('idx')}" if x.get("idx") is not None else "门"))
            文 = _证据文(x)
            if len(文) > 上限:
                文 = _头尾(文, 上限)
            出.append({"门": str(名), "通过": bool(x.get("passed")), "证据": 文})
        else:
            文 = str(x)
            if len(文) > 上限:
                文 = _头尾(文, 上限)
            出.append({"门": "（无结构门记录）", "通过": None, "证据": 文})
    return 出


# ★★ 基础设施故障可能只出现在**门证据**里（真事故：cmd 输出
#   `upstream 503 model_not_found` 且 rc=1，`error` 字段空 ⇒ 我先判成"未达标/执行侧"，
#   把上游故障记到产物头上）。这里用**强词 + 产物词否决**：
#   强词命中不算数，只要同时出现产物级证据（编译错误/断言失败/panic）就归产物。
_强基设词 = ("CIRCUIT_OPEN", "no healthy channel", "model_not_found",
             "503", "502", "429", "Connection refused", "Max retries exceeded",
             "Could not resolve host", "Connection reset",
             "Timeout:", "timed out", "Temporary failure in name resolution")
_产物词 = ("assert", "FAILED", "error[E", "panicked at", "SyntaxError",
           "IndentationError", "TypeError", "NameError", "does not compile")


_非法门词 = ("门规格非法", "非法判据门规格项", "缺 pattern", "无 pattern",
             "type 须属于", "不支持的判据门类型")


def _非法门规格(s: dict) -> str | None:
    """门规格本身写错（type 不认识/缺 pattern/`in` 非法）⇒ 红的不是产物，是任务书。

    证据（2026-10-30 标定语料 P4）：给一条未知 type 的门，判决落到「未达标」，
    等于让执行侧替**任务书写错**背锅。这类必须走「任务书驳回」。
    """
    材 = [str(s.get("error") or "")]
    for x in (_门们(s, 4000) or []):
        if isinstance(x, dict):
            材.append(str(x.get("err") or "") + " " + str(x.get("cause") or ""))
    for a in (s.get("audit") or []):
        if isinstance(a, dict):
            材.append(str(a.get("err") or "") + " " + str(a.get("cause") or ""))
    文 = " ".join(材)
    for 词 in _非法门词:
        if 词 in 文:
            return 词
    return None


def _证据里是基设(s: dict) -> bool:
    """红门的证据里有没有**强基础设施特征**（且没有产物级证据）。"""
    文 = " ".join(str(x.get("证据") or "") + " " + str(x.get("err") or "") + " "
                  + str(x.get("cause") or "")
                  for x in (_门们(s, 4000) or []) if isinstance(x, dict))
    for a in (s.get("audit") or []):
        if isinstance(a, dict):
            文 += " " + str(a.get("err") or "") + " " + str(a.get("cause") or "")
    低 = 文.lower()
    if any(w.lower() in 低 for w in _产物词):
        return False
    return any(w.lower() in 低 for w in _强基设词)


def _分类(j: dict) -> str:
    """失败分类必须互斥且能推出下一步。

    ★ 逐门记录优先；无逐门记录时用执行器裁决态（promoted/staged = 过完门并写回）。
      只有这样"promoted 的任务"才不可能被报成未达标。
    """
    st = (j.get("state") or "")
    # ★ 形状加固（2026-10-30 实测踩到）：`_分类` 读顶层 `state`/`error`，但 `status`
    #   里也可能各有一份。两者不一致时若只看顶层，会把"基础设施不可用"读成空串
    #   ⇒ 落到逐门兜底 ⇒ 判成「未达标 / 执行侧」= 基础设施故障背到执行侧头上。
    #   这一条不是为我的探针打的补丁：真实路径里 `status.error` 与 `job.error`
    #   由不同分支写入，形状不同就翻转裁决，比崩溃更坏。
    err = str(j.get("error") or (j.get("status") or {}).get("error") or "")
    if st == "cancelled" or j.get("_cancel"):
        return "取消"
    if st == "error" or err:
        _态 = dict(j.get("status") or {})
        if not _态.get("error"):
            _态["error"] = err
        if _非法门规格(_态):
            return "任务书驳回"
        if any(k in err for k in _基设词) or _证据里是基设(j.get("status") or {}):
            return "基础设施不可用"
        if any(k in err for k in _参数词):
            return "参数/前提不成立"
        return "未达标"
    if st in _非终态:                 # 还在跑 ⇒ 不是"未达标"，是"没有结论"
        return "进行中"
    s = (j.get("status") or {})
    if _非法门规格(s):
        return "任务书驳回"
    if j.get("驳回"):
        # 红的不是产物，是任务书 ⇒ 单独一档。折进"未达标"就是让执行侧背锅。
        return "任务书驳回"
    if j.get("差分分歧"):
        # ★ 一绿一红：**不是任务失败**，是"这只环境的结果不可信"。
        #   折成"未达标"是错的方向（等于替环境背锅）；折成"达标"更糟（假绿）。
        return "实例分歧"
    if _证据里是基设(s):
        # 门红了，但红的原因是上游不可用 ⇒ 不许记成产物的错。
        # ★ 必须先于失败态判断（2026-10-30 实测：顺序反了 ⇒ 3 条基设故障全变未达标）
        return "基础设施不可用"
    差 = j.get("差分") or {}
    if isinstance(差, dict) and 差:
        # ★ 差分是权威（2026-10-30 J1/J2 双向踩出的结论）：窗口/实例数 > 1 时，
        #   只有差分会把「票数吸收抖动」和「跨实例交集」算对；`state` 是**单窗**产物，
        #   用它裁决会两头出错：
        #     信 state=convergence_failed ⇒ 3/5 已过 须过 的抖动任务被判红；
        #     信 status.gates（最后一窗的绿）⇒ 全红的判成**达标**（假绿）。
        if 差.get("分歧"):
            return "实例分歧"
        return "达标" if 差.get("全绿") else "未达标"
    if st in ("convergence_failed", "rejected", "failed"):
        return "未达标"
    g = s.get("gates")
    门判 = None
    if isinstance(g, dict) and g:
        门判 = all(bool(v) for v in g.values())
    elif isinstance(g, list) and g and all(isinstance(x, dict) for x in g):
        门判 = all(bool(x.get("passed")) for x in g)
    if 门判 is not None:
        return "达标" if 门判 else "未达标"
    return "达标" if st in _成功态 else "未达标"


def _修法(j: dict, 分类: str) -> dict | None:
    """可执行的下一步（工具名 + 参数）—— 自纠能力的机械形态。"""
    jid = (j.get("id") or "")
    if 分类 == "达标":
        return None
    if 分类 == "未达标":
        # ★ 未达标的下一步是**直接修**，不是让人看证据（2026-10-30 交付自检发现）：
        #   回一个可执行的 orch_fix 调用，调用方不需要再问"要不要我修" —— 这才是省轮次。
        _仓 = j.get("repo") or j.get("仓") or "<同 repo>"
        _命令 = j.get("cmd") or j.get("命令") or "<同 cmd>"
        return {"工具": "orch_fix",
                "参数": {"repo": _仓, "cmd": _命令, "rounds": 2},
                "说明": "让模型按红门证据改代码 → 机器门复跑 → 绿保红回滚；"
                        "只想看逐门证据用 judge_result，只想重跑用 orch_run",
                "责任归属": "执行侧"}
    if 分类 == "基础设施不可用":
        return {"工具": "auto_scan", "参数": {},
                "说明": "先验基础设施（通道/磁盘），不要重跑同一命令；"
                        "本仓曾出现模型名过期 ⇒ 全 503，被误读成「任务失败」"}
    if 分类 == "参数/前提不成立":
        return {"工具": "list_tasks", "参数": {},
                "说明": "unit_id 必须在源码索引内；任意工程用 unit_id='any' + repo=工程根"}
    if 分类 == "任务书驳回":
        驳 = j.get("驳回") or {}
        if not 驳:
            坏 = _非法门规格(j.get("status") or {}) or "门规格/任务书不可机判"
            return {"工具": "orch_run", "参数": {"repo": "<仓>", "cmd": ["<命令>"], "gates": []},
                    "说明": f"任务书侧问题（{坏}）：改 gates 或去掉 gates（cmd 本身就是一条门）",
                    "责任归属": "调用方侧"}
        return {"工具": "orch_run", "参数": {"cmd": "[按说明改 cmd 后重跑]"},
                "说明": f"{驳.get('COR')}：{驳.get('说明')}（命中文档原文：{驳.get('命中')}）",
                "责任归属": 驳.get("责任归属"),
                "重复次数": 驳.get("重复次数"), "升级": 驳.get("升级"),
                "升级说明": 驳.get("升级说明")}
    if 分类 == "实例分歧":
        差分 = j.get("差分") or {}
        return {"工具": "judge_result", "参数": {"task_id": jid},
                "说明": "同一门集在刻意异构的实例里结论不同 ⇒ **环境耦合**，"
                        "不是判据错也不是产物错；按分歧实例的环境向量复现后再定",
                # 只给序号：环境向量与证据已在顶层 `差分.实例` 里，贴第二遍就是重复
                "分歧实例": [x["idx"] for x in (差分.get("实例") or []) if not x.get("ok")]}
    if 分类 == "进行中":
        return {"工具": "orch_wait", "参数": {"task_id": jid, "timeout_s": 600},
                "说明": "仍在跑：再等一次（服务端等待），不要用 task_status 轮询"}
    if 分类 == "取消":
        return {"工具": "resume_task", "参数": {"task_id": jid},
                "说明": "复用已通过窗，只补跑失败/未跑窗"}
    return {"工具": "judge_result", "参数": {"task_id": jid},
            "说明": "逐门证据已内联在『失败证据』；改产物或门后调 resume_task 复跑"}


# 不确定词（移植《AI指挥协作契约》COR-04）：判定基准里出现这些 ⇒ 判定基准不确定
_不确定词 = ("可能", "应该", "大概", "估计", "似乎", "试试", "待定", "TBD", "看情况", "差不多")
_引文件门 = ("grep_required", "grep_forbidden", "whole_reading", "kernel_statement",
             "axiom_footprint")
_内容门 = ("grep_required", "grep_forbidden", "scan_output", "whole_reading",
           "kernel_statement", "axiom_footprint")


def _近似文件(根: Path, 目标: str) -> list[str]:
    """给悬空引用找最近似的真实路径（移植《审计官》「列出最近似的已有条款」）。"""
    import difflib
    全 = []
    try:
        for p in 根.rglob("*"):
            if p.is_file():
                全.append(str(p.relative_to(根)).replace("\\", "/"))
            if len(全) >= 400:
                break
    except OSError:
        return []
    return difflib.get_close_matches(str(目标).replace("\\", "/"), 全, n=3, cutoff=0.4)


def _审任务书(根: Path, cmd: list, 门们: list | None) -> dict:
    """★ 移植《AI指挥官/审计官》体系的**结构**：审计对象是任务书本身。

    我原先只在**产物侧**设门，任务书侧没有门 —— 结果就是《审计官》说的
    「指令颗粒度不足」：执行者只能靠猜补全，猜出来的绿/红都可能是假的。
    这一层把三类真实缺陷机检出来，并给出**责任归属**（那两份文档最值钱的地方：
    判决不只说「不行」，还说「该谁动」）。

    等级沿用 G0~G3 命名，但**按机器语境重新绑定**（不冒充原定义）：
      G0 可直接机判（判据齐、引用可锚定、无不确定词）
      G1 能跑，但绿的含义弱（只判退出码）
      G2 判定基准不确定（含不确定词）⇒ 照跑，但回执里必须摊开「需补什么」
      G3 结构性不可满足（引用悬空）⇒ **硬驳回**，不浪费一次真跑

    四策略驳回的机器版：G3→硬驳回（拒跑）；G2→转写驳回（回执带待补信息）；
    G1→放行附提示；G0→放行。责任归属四值：调用方侧/执行侧/环境侧/编排器侧。
    """
    发现: list[dict] = []
    等级 = "G0"

    def 记(lv: str, 责: str, 说明: str, 修法: dict | None = None, 证据: str = "") -> None:
        nonlocal 等级
        发现.append({"等级": lv, "责任归属": 责, "说明": 说明, "证据": 证据, "修法": 修法})
        if lv > 等级:
            等级 = lv

    门们 = 门们 or []
    文 = " ".join(str(x) for x in (cmd or []))
    for g in 门们:
        if isinstance(g, dict):
            文 += " " + json.dumps(g, ensure_ascii=False)
    命中 = sorted({w for w in _不确定词 if w in 文})
    if 命中:
        记("G2", "调用方侧",
            f"判定基准含不确定词 {命中} ⇒ 机器无法判定「算不算过」，只能猜",
            {"工具": "orch_run",
             "参数": {"gates": "[把期望写成可机验判据：expect_exit / grep_required / scan_output]"},
             "说明": "把「大概对了」改成可机判：期望退出码、必须出现的子串、必须不出现的串"})

    for g in 门们:
        if not isinstance(g, dict) or g.get("type") not in _引文件门:
            continue
        目 = g.get("in") or g.get("file") or g.get("path")
        if not isinstance(目, str) or not 目.strip():
            continue
        if not (根 / 目).exists():
            近似 = _近似文件(根, 目)
            记("G3", "调用方侧",
                f"门[{g.get('name') or g.get('type')}]引用不存在的路径：{目} ⇒ 该门永远不可能满足",
                {"工具": "orch_run",
                 "参数": {"gates": f"[把 {目} 改成真实路径]" + (f"，最近似：{近似}" if 近似 else "")},
                 "说明": "悬空引用不是产物错，是任务书写错了；改门再跑，别让执行侧背锅"},
                证据=f"最近似：{近似}" if 近似 else "")

    # ④ 构建文件存在性（把 COR-05 从"事后驳回"提前到"跑之前"）
    #   真事故：调用方对无 Cargo.toml 的仓提 cargo check，我先花了 0.9 秒真跑 cargo,
    #   才在红证据里认出 COR-05。既然"仓里有没有 Cargo.toml"是零成本事实，
    #   就该在**跑之前**判 —— 否则每个错任务书都要烧一次真跑。
    _首 = str((cmd or [""])[0]).lower() if cmd else ""
    if _首 == "cargo" and not any(str(x).startswith("--manifest-path") for x in (cmd or [])):
        _p = 根
        _有 = False
        for _ in range(12):                      # cargo 会向上找 Cargo.toml
            if (_p / "Cargo.toml").exists():
                _有 = True
                break
            if _p.parent == _p:
                break
            _p = _p.parent
        if not _有:
            记("G3", "调用方侧",
                f"仓里（含上级目录）没有 Cargo.toml ⇒ cargo 门不可能过；"
                f"这是任务书与仓不符，不是代码问题",
                {"工具": "orch_run",
                 "参数": {"repo": "[指向含 Cargo.toml 的目录]", "cmd": ["cargo", "check", "--lib"]},
                 "说明": "COR-05：先确认仓里有构建文件，再提 cargo 门"})

    # ★ 立法修正（2026-10-30 当场自己抓的）：这条原本记成 G1 + 责任归属=调用方侧，
    #   错了。`expect_exit:0` 是**编排器自己**注入的默认期望，"只判退出码"是编排器的
    #   默认行为，不是调用方的缺陷 —— 对"跑一下看行不行"这种合法诉求判它不合格，
    #   就是又一次「兜底错方向」。⇒ 降为**提示**（不改等级、不判责任），如实告诉
    #   调用方"这次的绿有多强"。
    有内容门 = any(isinstance(g, dict) and g.get("type") in _内容门 for g in 门们)
    强度 = "内容/产物" if 有内容门 else "仅退出码"
    提示 = ("绿的含义：命令退出码 0（不判输出/产物）。要判内容加 grep_required / "
            "scan_output / whole_reading" if not 有内容门 else
            "绿的含义：命令退出码 0 + 你声明的内容/产物判据")
    return {"等级": 等级, "判据强度": 强度, "提示": 提示, "发现": 发现}


# ★★ 双向纠偏（移植《契约》§9.2「本地AI→AI指挥官纠偏」的机器版）
#   原来只有单向：执行侧红了，我把"未达标"甩回去。但如果**任务书本身**与仓不符
#   （门要求的目标不存在 / 测试门没有对象 / 命令参数不被接受），红的是任务书，
#   不是产物 —— 执行侧做什么都不可能过。这时候必须由执行侧**向上驳回**。
_驳回规则: tuple = (
    ("COR-05", ("no targets found", "could not find `Cargo.toml`", "could not find Cargo.toml",
                "does not contain a package", "no library targets found"),
     "调用方侧", "门要求的构建目标在仓里不存在 ⇒ 任务书与仓不符，不是产物错",
     "把 cmd 改成仓里真实存在的目标（先 ls 出 Cargo.toml / pyproject.toml 再写 cmd）"),
    ("COR-05", ("no tests ran", "no test files", "collected 0 items", "Ran 0 tests"),
     "调用方侧", "测试门没有可跑的对象 ⇒ 判据悬空，红不代表代码坏",
     "先确认测试文件存在；没有测试就用 cargo check / python -m compileall 这类有对象的门"),
    ("COR-07", ("unexpected argument", "unrecognized subcommand", "error: Found argument",
                "unknown command", "no such option", "invalid subcommand"),
     "调用方侧", "命令形态不被工具接受 ⇒ 任务书写错，不是代码错",
     "对照 `--help` 改参数；不确定就先跑 `<工具> --help` 拿真参数再写进 cmd"),
)


def _判驳回(status: dict) -> dict | None:
    """红门是不是「任务书缺陷」造成的？是则返回驳回项（含 COR 编号与责任归属）。

    只在**红**时跑：绿不需要驳回。命中即说明"执行侧做什么都不可能过"。
    """
    文 = " ".join(str(a.get("err") or "") + " " + str(a.get("cause") or "")
                  for a in (status.get("audit") or []) if isinstance(a, dict))
    文 += " " + " ".join(str(记.get("证据") or "") for 记 in (_门们(status, 2000) or [])
                          if isinstance(记, dict))
    低 = 文.lower()
    for cor, 词们, 责, 说明, 修 in _驳回规则:
        for w in 词们:
            if w.lower() in 低:
                return {"COR": cor, "责任归属": 责, "说明": 说明, "命中": w,
                        "改法": 修}
    return None


def _纠偏留痕(job: dict, 驳: dict) -> dict:
    """驳回落持久台账（移植《契约》§9.3 纠偏升级：同一问题连续 3 轮未修正要留痕）。

    计数键 = (repo, cmd, COR)：同一个错在不同任务上重复出现，才算"未修正"。
    """
    签 = json.dumps({"repo": job.get("repo") or job.get("cwd"),
                     "cmd": job.get("cmd"), "cor": 驳["COR"]},
                    ensure_ascii=False, sort_keys=True)
    文件 = Path(_DB_FILE).parent / "纠偏台账.jsonl"
    次数 = 0
    try:
        if 文件.exists():
            with 文件.open("r", encoding="utf-8", errors="replace") as fp:
                for ln in fp:
                    try:
                        if json.loads(ln).get("签") == 签:
                            次数 += 1
                    except Exception:
                        continue
        次数 += 1
        with 文件.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps({"t": round(time.time(), 3), "签": 签,
                                 "task": job.get("id"), "cor": 驳["COR"],
                                 "命中的任务书": job.get("cmd")}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return {**驳, "重复次数": 次数, "升级": 次数 >= 3,
            "升级说明": (f"⚠️ 升级：{驳['COR']} 同一任务书已连续第 {次数} 次被驳回而未被修正"
                        if 次数 >= 3 else None)}


def _审计官(j: dict, 分类: str) -> dict:
    """★ 三角色里的**审计官**独立成一步（三阶段模型，移植《审计官 v5.3》）。

    ★★ 为什么这版**不调用任何既有 helper**（`_门们`/`_证据文`/`_责任归属`）：
      上一版这么调，真调用直接 `RecursionError` 爆栈（而自检仍绿 ⇒ 自检没覆盖这条路）。
      我没能定位到环，于是**从结构上消掉成环的可能**：只读 `j` 里的原始字段。
      这比"赌它这次不爆"可靠——审计官给出的是「这个结论可不可信」，它必须最不可能崩。

      阶段一（机械）：任务书能否机判（G0~G3）
      阶段二（信息流动）：结论是否**自包含**——失败有没有证据原文。没证据 = **信息缺失**
                          ⇒ 索要驳回（拿不到证据不该下判决，而不是判死）
      阶段三（交叉校验）：差分/实例结论与主结论是否一致
    结论四值：放行 / 硬驳回 / 转写驳回 / 索要驳回。
    """
    st = j.get("status") or {}
    审计 = j.get("任务书审计") or {}
    等级 = str(审计.get("等级") or "G0")
    差分 = j.get("差分") or {}

    失败门: list = []
    g = st.get("gates")
    if isinstance(g, dict):
        失败门 = [k for k, v in g.items() if not bool(v)]
    elif isinstance(g, list):
        失败门 = [x.get("gate") or x.get("门") for x in g
                  if isinstance(x, dict) and not bool(x.get("passed"))]
    有证据 = True
    if 失败门:
        证 = ""
        for a in (st.get("audit") or []):
            if isinstance(a, dict):
                证 += str(a.get("err") or "") + str(a.get("cause") or "") + str(a.get("证据") or "")
        有证据 = bool(证.strip())

    阶段一 = "通过" if 等级 in ("G0", "G1", "G2") else "命中：G3 任务书结构性不可满足"
    if 分类 == "达标":
        阶段二 = "通过：绿无待证事实"
    elif 失败门 and not 有证据:
        阶段二 = "信息缺失：失败门无证据原文"
    else:
        阶段二 = "通过：失败证据可回放"
    if not 差分:
        阶段三 = "未启用（单实例单窗）"
    elif 差分.get("全绿") or 差分.get("全红"):
        阶段三 = "通过：实例结论一致"
    else:
        阶段三 = "不一致：实例分歧，不能放行"

    if 分类 == "达标" and not str(阶段三).startswith("不一致"):
        结论 = "放行"
    elif 分类 in ("基础设施不可用", "进行中", "取消"):
        结论 = "放行（非产物问题，不记不合格）"
    elif str(阶段二).startswith("信息缺失"):
        结论 = "索要驳回"
    elif 分类 in ("参数/前提不成立", "任务书驳回"):
        结论 = "转写驳回"
    else:
        结论 = "硬驳回"

    return {"审计对象": "回执", "阶段一_机械": 阶段一, "阶段二_信息流动": 阶段二,
            "阶段三_交叉校验": 阶段三, "结论": 结论,
            "恢复协议": None if str(结论).startswith("放行") else "按『修法』执行后重跑"}


def _责任归属(j: dict, 分类: str) -> str | None:
    """谁该动：判决不带责任归属，就只是抱怨（《审计官》四策略驳回的要点）。

    未达标默认责任在执行侧，但证据里出现 CMD_NOT_FOUND / 超时 / 挂起 时真实责任是
    **环境侧** —— 执行侧什么都没做错，改它没用。
    """
    if 分类 == "任务书驳回":
        return (j.get("驳回") or {}).get("责任归属")
    if 分类 == "实例分歧":
        return "环境侧"
    if 分类 in ("达标", "进行中"):
        return None
    证据 = " ".join(str(a.get("证据")) for a in (_门们(j.get("status") or {}, 800) or [])
                    if isinstance(a, dict))
    if 分类 == "未达标":
        if any(k in 证据 for k in ("CMD_NOT_FOUND", "SUBPROCESS_TIMEOUT", "HANG_STALL")):
            return "环境侧"
        return "执行侧"
    if 分类 == "基础设施不可用":
        return "环境侧"
    if 分类 in ("参数/前提不成立", "取消"):
        return "调用方侧"
    if 分类 == "未到终态":
        return "编排器侧"
    return None


async def _差分验收(spec: list, cwd, work, n: int, 基线绿: bool,
                     模式: str = "env", 窗数: int = 1, 须过: int = 1) -> dict:
    """★ 实例级差分验收：**绿要交集，红要并集取证，分歧单独一档**。

    为什么需要（本会话的真教训）：我修过的最严重的几个缺陷——门子进程被 MCP 协议管道
    卡死、PYTHONPYCACHEPREFIX 指向 0 字节盘、`_分类` 把 promoted 判成未达标——
    **单实例单环境永远看不见**，它们全是"换个环境/换个实例就变"的耦合型缺陷。
    ⇒ "绿"不能只在一只环境里绿：同一门集要在刻意异构的实例里各跑一次。

    异构维度（只挑语义无关、却能暴露耦合的）：PYTHONHASHSEED（顺序不确定性）、
    独立 TMP/TEMP/TMPDIR（临时件隔离）、PYTHONDONTWRITEBYTECODE。
    刻意**不动** TZ/LANG：那会改语义，用来判达标就是错判据。

    ★ 聚合规则为什么**不对等**（设计要点，不是偷懒）：
      · 绿 = 交集（所有实例都绿才算绿），否则假绿照样过关；
      · 红 = 不走交集：主实例红就直接红（红已有真证据，再乘实例数只是烧钱）；
      · 混绿混红 = **既不是绿也不是红**，单列「实例分歧」——把它折进任一边，
        不是在替环境背锅，就是在放行假绿。这正是我这几轮反复打的那个病。
    """
    from agentscope_orchestrator import gate_spec as _gs
    from agentscope_orchestrator import shushu_logic as _sl
    基线 = dict(getattr(_gs, "实例环境", {}) or {})
    实例们: list[dict] = []
    # ★ instances × n_windows 的联动语义（先立法后写码，见回执 175）：
    #   · 同一实例内多次采样 ⇒ **抖动用票数吸收**（该实例过 = 通过窗数 ≥ 须过）
    #   · 跨实例 ⇒ **耦合用交集暴露**（每个实例都要过；混绿混红 = 分歧）
    #   绝不把两者乘成一个平均数 —— 那会同时看不见抖动和耦合。
    窗数 = max(1, int(窗数 or 1))
    须过 = min(窗数, max(1, int(须过 or 1)))
    基线票 = [bool(基线绿)]
    if 窗数 > 1:
        try:
            _gs0 = __import__("agentscope_orchestrator.gate_spec", fromlist=["evaluate_async"])
            for _k in range(窗数 - 1):
                res0, aud0 = await rt.evaluate_async(spec, cwd, work, str(cwd), file_root=cwd)
                基线票.append(bool(_sl.从审计(res0, aud0)[0]))
        except Exception:            # noqa: BLE001 补采失败就按已有票数，不假装满票
            pass
    绿们: list[bool] = [sum(基线票) >= 须过]
    临时们: list[Path] = []
    try:
        for k in range(1, max(1, int(n))):
            临时 = Path(__import__("tempfile").mkdtemp(prefix=f"orch_inst{k}_"))
            临时们.append(临时)
            覆盖 = {"PYTHONHASHSEED": str(k), "TMP": str(临时), "TEMP": str(临时),
                    "TMPDIR": str(临时), "PYTHONDONTWRITEBYTECODE": "1"}
            _跑cwd = cwd
            if str(模式) == "copy":
                # ★ 环境异构抓不到「**仓状态污染**」：同一 cwd 的实例共享文件系统，
                #   主实例绿可能只是因为上一次编译留下的 target/、缓存、生成文件。
                #   迁移到独立副本 = 干净状态 ⇒ 若主实例绿而副本红，那是**伪绿**，
                #   是单实例永远看不见的真发现。
                副本 = 临时 / "repo"
                try:
                    __import__("shutil").copytree(
                        Path(cwd), 副本, dirs_exist_ok=True,
                        ignore=__import__("shutil").ignore_patterns(
                            ".git", "target", "node_modules", "__pycache__", ".venv",
                            "dist", "build", ".pytest_cache", "*.pyc"))
                    _跑cwd = 副本
                    if os.environ.get("ORCH_TRACE_COPY"):
                        import sys as _sys
                        print(f"[trace-copy] 源cwd={Path(cwd)} work={work}\n"
                              f"[trace-copy] 副本={副本}\n"
                              f"[trace-copy] 副本里 flag 存在={ (副本/'target/ok.flag').exists() }\n"
                              f"[trace-copy] 模式={模式!r} 探=k{k}", file=_sys.stderr, flush=True)
                except OSError as exc:
                    _gs.实例环境 = {**基线, **覆盖}
                    实例们.append({"idx": k, "模式": ("copy" if str(模式) == "copy" else "env"), "ok": False, "环境": "copy",
                                   "证据": f"副本创建失败：{type(exc).__name__}: {exc}"})
                    绿们.append(False)
                    continue
            try:
                _gs.实例环境 = {**基线, **覆盖}
                # ★ 根因（本轮实测）：门规格里**自带 cwd**，它会盖掉传下去的
                #   `cwd_default`。所以"换个 cwd_default 参数"根本改不了执行目录：
                #   副本建好了（trace 里 flag 确实不在），门还是回原仓跑 ⇒ 假绿。
                #   ⇒ 必须把门规格里的 cwd 一并改写到副本。
                _副本spec = [{**g, "cwd": str(_跑cwd)} if isinstance(g, dict) and "cwd" in g
                            else g for g in spec]
                res, aud = await rt.evaluate_async(_副本spec, _跑cwd, work, str(_跑cwd), file_root=_跑cwd)
                值, _因, _注 = _sl.从审计(res, aud)
                _票 = [bool(值)]
                for _j in range(窗数 - 1):
                    try:
                        _r2, _a2 = await rt.evaluate_async(_副本spec, _跑cwd, work, str(_跑cwd), file_root=_跑cwd)
                        _票.append(bool(_sl.从审计(_r2, _a2)[0]))
                    except Exception:    # noqa: BLE001
                        break
                ok = sum(_票) >= 须过
                证据 = "；".join(
                    f"{a.get('gate')}={a.get('cause') or a.get('rc')}"
                    for a in (aud or []) if isinstance(a, dict))[:300]
            except Exception as exc:       # noqa: BLE001 实例崩了也是一种观测
                ok = False
                证据 = f"实例崩了：{type(exc).__name__}: {exc}"[:300]
            finally:
                _gs.实例环境 = 基线
            绿们.append(ok)
            # 载荷减肥（自指缺陷：一个教人省往返的工具，自己把同一份环境贴了三遍）
            #   TMP 三个键指向同一个临时目录 ⇒ 摘要成"独立临时目录"，不贴路径。
            实例们.append({"idx": k, "模式": ("copy" if str(模式) == "copy" else "env"),
                           "ok": ok, "环境": {
                "PYTHONHASHSEED": 覆盖["PYTHONHASHSEED"],
                "TMP": "独立临时目录",
                "PYTHONDONTWRITEBYTECODE": "1"}, "证据": 证据})
    finally:
        _gs.实例环境 = 基线
        for t in 临时们:
            __import__("shutil").rmtree(t, ignore_errors=True)
    全绿 = all(绿们)
    全红 = not any(绿们)
    return {"实例数": len(绿们), "窗数": 窗数, "须过": 须过, "基线绿": bool(基线绿),
            "全绿": 全绿, "全红": 全红,
            "分歧": (not 全绿) and (not 全红), "实例": 实例们,
            "模式": ("copy" if str(模式) == "copy" else "env"),
            "说明": ("全部实例都绿 ⇒ 绿（交集成立）" if 全绿 else
                    "全部实例都红 ⇒ 红（判据一致）" if 全红 else
                    # ★ 分歧的成因**随模式不同**，文案必须跟着说：
                    #   env  = 环境耦合（同一份文件，不同环境结论不同）
                    #   copy = **伪绿**（原仓有构建产物/缓存，干净副本里没有）
                    #   给错结论比不给更坏：调用方会照错的去复现。
                    "主实例在**有既有产物/缓存的原仓**里绿，干净副本（无 target/缓存）里红 ⇒ "
                    "**伪绿**：绿依赖既有状态，不是代码本身" if str(模式) == "copy" else
                    "主实例与异构实例结论不同 ⇒ **环境耦合**，既不能判绿也不能判未达标")}


def _payload(job_id: str, max_chars: int = 20000) -> dict:
    """终态载荷：分类 + 逐门证据 + 修法 + 成本 + 产物内容（内联，不给指针）。"""
    j = _jobs.get(job_id) or {}
    分类 = _分类(j)
    s = (j.get("status") or {})
    门 = _门们(s, max(400, min(2000, int(max_chars // max(1, len((s.get("gates") or {})) or 1)))))
    # ★ ⑤ 预算闸：**上限 / 已用 / 剩余**三件套 + 检查点。
    #   理由：AI 会来回调十几次而不自知。只在超时后才说"超时了"没有用 ——
    #   必须在每次回执里告诉它"还剩多少"，它才能自己决定继续还是收手。
    预算 = {"秒": {"上限": int(j.get("timeout_s") or 0),
                  "已用": round(max(0.0, time.time() - float(j.get("created") or time.time())), 1)},
           "门数": {"上限": int(j.get("max_gates") or 0), "实得": len(门)}}
    if 预算["秒"]["上限"]:
        预算["秒"]["剩余"] = round(max(0.0, 预算["秒"]["上限"] - 预算["秒"]["已用"]), 1)
    # ★ 检查点：**必须回答"下一步调什么工具、带什么参数"**，否则断线=任务白做。
    try:
        from agentscope_orchestrator import agentface as _af
        检查点 = _af.落检查点(job_id, {
            "分类": 分类, "责任归属": _责任归属(j, 分类), "阶段": j.get("state"),
            "预算": 预算,
            "门": [{"门": g.get("门"), "通过": g.get("通过"),
                    "证据": str(g.get("证据") or "")[:200]} for g in 门],
            "下一步": (_修法(j, 分类) or {}).get("说明") or
                     ("用产物，别复查" if 分类 == "达标" else "按『修法』执行"),
            "续跑": {"工具": "orch_wait" if not _是终态(j) else "read_report",
                    "参数": {"task_id": job_id}},
        })
    except Exception:                             # noqa: BLE001 检查点失败不许影响回执
        检查点 = ""

    # ★ 显式的"先读这个"：AI 上下文有限，摘要 ≤1KB，细节照旧在后面。
    try:
        from agentscope_orchestrator.agentface import 决策摘要 as _摘
        摘 = _摘(分类, _责任归属(j, 分类), 门, _修法(j, 分类),
                {"rc": j.get("rc"), "state": j.get("state"),
                 "剩余预算_s": ((j.get("timeout_s") or 0) - round(
                     (__import__("time").time() - (j.get("t0") or __import__("time").time())), 1))
                 if j.get("timeout_s") else None})
    except Exception:                             # noqa: BLE001 摘要失败不许影响回执
        摘 = None
    out = {"ok": True, "task_id": job_id, "分类": 分类,
           **({"决策摘要": 摘} if 摘 else {}),
           "预算": 预算,
           **({"留痕": 检查点} if 检查点 else {}),
           "state": j.get("state"), "rc": j.get("rc"), "error": j.get("error"),
           "门": 门,
           "失败证据": [{"门": g["门"], "证据": g["证据"]} for g in 门 if g["通过"] is False],
           "修法": _修法(j, 分类),
           "责任归属": _责任归属(j, 分类),
           "审计官": _审计官(j, 分类),
           "仓改动": j.get("仓改动"),
           **({"驳回": j["驳回"]} if j.get("驳回") else {}),
           **({"任务书审计": j["任务书审计"]} if j.get("任务书审计") else {}),
           **({"差分": j["差分"]} if j.get("差分") else {}),
           "成本": _成本读(job_id, j),
           "产物": None,
           "console": _CONSOLE + f"/task/{job_id}"}   # 附加字段，不再是唯一信息通道
    product = s.get("product")
    if product:
        try:
            p = Path(product).resolve()
            产物 = {"path": str(p)}
            if p.is_file():
                产物["byte_len"] = p.stat().st_size
                文 = p.read_text(encoding="utf-8", errors="replace")
                if len(文) <= max_chars:
                    产物["内容"] = 文
                else:
                    产物["内容"] = 文[:max_chars] + f"\n…（截断：全文 {len(文)} 字符）"
                    产物["truncated"] = True
            out["产物"] = 产物
        except OSError as exc:
            产物 = {"path": str(product), "读取失败": str(exc)}
            out["产物"] = 产物
    return out


async def _等终态_a(job_id: str, timeout_s: float, 步长: float = 0.4) -> dict:
    """异步等待：**不得**在事件循环线程上阻塞（真实事故见下）。

    ★★★★★ 2026-10-30 真事故（这条是拿真调用撞出来的）
      通过 MCP 调用 `orch_run(wait=True)`：任务落库成 running 之后**再也不动**，
      我等了 300 秒拿到 `state=running`，而且分类还报了"未达标"（门为空）。
      根因：FastMCP 把同步工具放在事件循环线程上执行，而 `_等终态` 用
      `time.sleep` 阻塞 ⇒ **循环被饿死** ⇒ 派发出去的执行协程永远没机会跑。
      同一个调用在普通线程里跑（无运行中的循环）反而是好的 —— 所以只在
      真 MCP 通道上才复现，本地单测看不见。**这是"接口形态"逼出来的缺陷。**
      修法：等待改成异步（await asyncio.sleep），循环始终有空转。
    """
    t0 = time.time()
    while True:
        j = _jobs.get(job_id)
        if j is None:
            _db_sync_in()
            j = _jobs.get(job_id)
        if j is not None and _是终态(j):
            return {"等到了": True, "耗时_s": round(time.time() - t0, 2),
                    "最终state": j.get("state")}
        if time.time() - t0 >= timeout_s:
            j = _jobs.get(job_id) or {}
            return {"等到了": False, "耗时_s": round(time.time() - t0, 2),
                    "最终state": j.get("state"),
                    "提示": "未到终态；用 orch_wait 再等，不要反复 task_status 轮询"}
        await asyncio.sleep(步长)


@mcp.tool(name=_TOOL("orch_run"))
async def orch_run(repo: str, cmd: list[str], gates: list[dict] | None = None,
             unit_id: str = "any", name: str = "engine", wait: bool = True,
             timeout_s: int = 1800, priority: int = 0,
             depends_on: list[str] | None = None, instances: int = 1,
             instances_mode: str = "env", windows: int = 1, min_pass: int = 1,
             max_gates: int = 40) -> str:
    """★★★【智能体默认入口】一次提交，直接拿结论。

    repo: 工程根（绝对路径）。**任意工程**，不限 Rust —— `unit_id="any"` 时不锚定源码单元，
          只要求 repo 真实存在（存在前提的机器判据从"文件存在"换成"仓存在"）。
    cmd : 要跑的命令（如 ["cargo","check","--lib"]、["python","-m","pytest","-q"]）。
    gates: 可选附加门；**可为空**——`cmd` 自己永远被编成一条 cmd_exit_ok 门。
    wait: True（默认）= 服务端等到终态再返回。**这就是省轮次的关键**：
          调用方只需 1 次往返，而不是"提交 → 轮询 → 再拉详情"。
    timeout_s: 等待上限（超时不杀任务，只返回未到终态 + 修法）。
    instances: >1 时做**差分验收**：同一门集在刻意异构的实例里各跑一次，**绿要交集**；
               结论不同 ⇒ 分类「实例分歧」而不是未达标（那是耦合，不是任务失败）。默认 1。
    instances_mode: **"env"（默认）** = 只异构环境（PYTHONHASHSEED/独立 TMP）；
               **"copy"** = 每个实例跑在**独立仓副本**里（排除 target/.git/node_modules 等）。
               copy 的独立价值：抓**伪绿**——主实例绿只因上次编译留下了 target/，干净副本里其实红。
               代价：仓越大副本越贵（每个实例复制一次源码）。
    windows  : 同一实例内**重复采样次数**（默认 1）。用来区分**抖动**与**真失败**。
    min_pass : 一个实例算"过"需要几窗通过（默认 1；建议 windows=5 时配 3）。
               合起来：**抖动靠票数吸收（windows/min_pass），耦合靠交集暴露（instances）**。
               确定性命令不必开多窗——5 窗和 1 窗结论一样，只是贵 5 倍。
               ★ 同名不同义（2026-11-01 补）：这里的 `min_pass` 是**票数**；
                 `submit_task` 的 `min_pass` 是**可及数**（产物逐字相同的窗只算一条路径）。
                 两个工具读同一个词却数不同的东西 ⇒ 必须看工具名，别按直觉套。

    返回（终态）：逐门证据（内联原文）、**可执行修法**{工具,参数}、成本（MCP调用数/载荷/token）、
    产物、控制台链接；带差分/审计官时另有 差分 与 审计官 两段。

    ★ 分类只有这 9 个值，**不要自己发明**；每个值对应完全不同的下一步动作：
      达标            → 用产物；结束，别复查。
      未达标          → 按『修法』改产物或门，再 resume_task/orch_run 复跑；不要重试同一条命令。
      参数/前提不成立  → 责任在**调用方**：repo/cmd/unit_id/gates 写错了，看 error 原文改参数。
      基础设施不可用   → 责任在**环境**：通道熔断/端点死/无 key。**不要改产物**；
                        修法是等冷却或用其它通道（错误里带 `共试 N 次`/`CIRCUIT_OPEN` 就是这档）。
      实例分歧        → 责任在**环境耦合或伪绿**：看 差分.实例[].模式 与 证据；
                        模式=copy 时多半是"绿依赖既有产物"⇒ 清 target 再跑，别信那个绿。
      任务书驳回      → 任务书本身不可机判（G3）或门规格非法，改任务书/门，不是改产物。
      进行中          → **未到终态**，不是失败：用 orch_wait 继续等，或 timeout_s 调大。
      取消            → 被显式取消，不判成败。
      未到终态        → wait 超时但任务还在跑：orch_wait 再等，别重复提交。

    ★ 排队：若返回里有 `排队` 段，说明并发闸被占满（**跨进程**上限 4，用操作系统文件锁
      占槽；多进程共用一个配额），本次先排队再执行——**这不是任务慢**。里面给排队秒数与上限。

    ★ 自纠契约（AI 可用的核心）：任何非达标返回都带 `修法={工具,参数}`，
      按它调下一个工具即可，**不需要回读我的源码**。修法为 null 时才是"得人来判断"。
    """
    global _seq
    根 = Path(repo or ".").resolve()
    if not 根.is_dir():
        return json.dumps({"ok": False, "分类": "参数/前提不成立",
                           "error": f"repo 不是目录：{根}",
                           "修法": {"工具": "auto_scan", "参数": {},
                                    "说明": "先确认工程根存在（本层只认真实存在的仓）"}},
                          ensure_ascii=False)
    if not cmd:
        return json.dumps({"ok": False, "分类": "参数/前提不成立", "error": "cmd 为空，拒跑",
                           "修法": {"工具": "orch_run",
                                    "参数": {"repo": str(根), "cmd": ["<你的命令>"]}}},
                          ensure_ascii=False)
    锚 = None
    if unit_id not in ("", "any", "*"):
        锚 = _anchor_engine(unit_id)
        if 锚 is None:
            return json.dumps({"ok": False, "分类": "参数/前提不成立",
                               "error": f"unit_id={unit_id} 不在源码索引内，拒跑"
                                        f"（任意工程请用 unit_id='any'）",
                               "修法": {"工具": "orch_run",
                                        "参数": {"repo": str(根), "cmd": list(cmd),
                                                 "unit_id": "any"}}},
                              ensure_ascii=False)
    _全门 = _自动门(cmd, 根, gates)
    bad = rt.gate_spec_validate(_全门)
    if bad:
        return json.dumps({"ok": False, "分类": "任务书驳回", "error": bad,
                           "修法": {"工具": "orch_run",
                                    "参数": {"repo": str(根), "cmd": list(cmd), "gates": []},
                                    "说明": "门规格不合法；先只留 cmd_exit_ok"}},
                          ensure_ascii=False)
    _safe, _vio = check_cmd_safety(_全门, rt.WORKDIR, repo_root=根)
    if not _safe:
        return json.dumps({"ok": False, "分类": "参数/前提不成立",
                           "error": "命令安全闸拒绝：" + "；".join(_vio[:4]),
                           "violations": _vio,
                           "修法": {"工具": "orch_run",
                                    "参数": {"repo": str(根), "cmd": ["<白名单内命令>"]},
                                    "说明": "顶层命令必须在门白名单内（cargo/python/node/pwsh…）"}},
                          ensure_ascii=False)
    deps = list(depends_on or [])
    for d in deps:
        if d not in _jobs:
            return json.dumps({"ok": False, "分类": "参数/前提不成立",
                               "error": f"依赖任务 {d} 不存在，拒跑",
                               "修法": {"工具": "list_tasks", "参数": {}}},
                              ensure_ascii=False)
    job_id = _next_id()
    # ★ 预算闸先于任务书审计：门数超预算属**调用方参数问题**，
    #   不该被"任务书驳回"抢先盖掉（实测：6 条合法门被 G3 先拦，分类误导）。
    if int(max_gates or 0) and len(_全门) > int(max_gates):
        _成本累("（未建任务）", "orch_run(门数超额)", 0)
        return json.dumps({"ok": False, "分类": "参数/前提不成立", "责任归属": "调用方侧",
                           "error": f"门数 {len(_全门)} 超过本次预算 {int(max_gates)}",
                           "预算": {"门数": {"上限": int(max_gates), "实得": len(_全门)}},
                           "修法": {"工具": "orch_run",
                                   "参数": {"repo": str(根), "cmd": list(cmd),
                                            "max_gates": len(_全门)},
                                   "说明": "判据多不是错，但要显式抬预算（默认 40）"}},
                          ensure_ascii=False)
    审计 = _审任务书(根, cmd, _全门)
    if 审计.get("等级") == "G3":
        _成本累("（未建任务）", "orch_run(硬驳回)", 0)
        return json.dumps({"ok": False, "分类": "参数/前提不成立",
                           "责任归属": "调用方侧",
                           "error": "任务书结构性不可满足（G3）：执行侧做什么都不可能过",
                           "发现": 审计.get("发现"),
                           "修法": (审计.get("发现") or [{}])[-1].get("修法")
                                   or {"工具": "orch_run", "参数": {}}},
                          ensure_ascii=False)
    _jobs[job_id] = {"id": job_id, "created": time.time(), "kind": "engine",
                     "timeout_s": int(timeout_s or 0), "max_gates": int(max_gates or 0),
                     "unit_id": (str(锚) if 锚 else f"any:{根.name}"),
                     "anchored": bool(锚), "repo": str(根), "target_rel": name,
                     "cmd": list(cmd), "cwd": str(根), "gates": _全门,
                     "priority": max(0, int(priority or 0)), "depends_on": deps,
                     "instances": max(1, min(4, int(instances or 1))),
                     "instances_mode": ("copy" if str(instances_mode) == "copy" else "env"),
                     "windows": max(1, min(9, int(windows or 1))),
                     "min_pass": max(1, min(9, int(min_pass or 1))),
                     "任务书审计": 审计,
                     "state": "queued", "dry": False}
    _db_save_one(job_id, _jobs[job_id])
    _成本累(job_id, "orch_run", 0)
    if not wait:
        _schedule()                      # 后台跑（与 run_errand 同路）
        return json.dumps({"ok": True, "task_id": job_id,
                           "分类": "进行中",
                           "state": _jobs[job_id].get("state"),
                           "门数": len(_全门),
                           "修法": {"工具": "orch_wait",
                                    "参数": {"task_id": job_id, "timeout_s": timeout_s}}},
                          ensure_ascii=False)
    # ★★★★★ 2026-10-30 第二次真事故：**跨线程派发在 MCP 里不落地**
    #   真证据（部署库）：`run_errand` 派发的任务能跑到 promoted；而同一条命令经
    #   `orch_run(wait=True)` 提交后永远停在 running，两次真调用分别卡 300s / 120s，
    #   重启被 `_reap_orphans` 回收成"孤儿 running"。
    #   ⇒ 根因是 `_submit_coro` 的跨线程/跨循环分支在 FastMCP 宿主里拿不到落地循环。
    #   ⇒ 修法不再修派发器，而是**wait=True 时直接就地 await 执行体**：
    #     同一条 `_run_errand`、同一套门、同一份审计，只是不经过派发。
    #     代价：这一个调用期间占用事件循环（对"提交即等结论"的调用方正是想要的）。
    _jobs[job_id]["state"] = "running"
    _db_save_one(job_id, _jobs[job_id])
    _排队 = 0.0
    try:
        _闸 = _取闸()
        if _闸.locked():
            _t0 = time.time()
            _jobs[job_id]["排队中"] = True
            async with _闸:
                _jobs[job_id]["排队中"] = False
                _排队 = time.time() - _t0
                await _有槽开跑(job_id, _run_errand(job_id))
        else:
            async with _闸:
                await _有槽开跑(job_id, _run_errand(job_id))
    except Exception as exc:             # noqa: BLE001 就地执行也必须落终态
        _jobs[job_id]["state"] = "error"
        _jobs[job_id]["error"] = f"{type(exc).__name__}: {exc}"
        _db_save_one(job_id, _jobs[job_id])
    终 = await _等终态_a(job_id, float(timeout_s), 步长=0.05)
    out = _payload(job_id, 20000)
    out["等待"] = 终
    if _排队 > 0.05:
        out["排队"] = {"并发上限": int(_CONCURRENCY), "排队等待_s": round(_排队, 2),
                       "说明": "有同批任务占满并发闸 ⇒ 本次先排队再执行；这不是任务慢"}
    文 = json.dumps(out, ensure_ascii=False, indent=1)
    _成本补(job_id, len(文))
    out["成本"] = _成本读(job_id, _jobs.get(job_id))      # 第二趟：让载荷字节真进字段
    文 = json.dumps(out, ensure_ascii=False, indent=1)
    _成本补(job_id, len(文) - out["成本"]["载荷字节"])
    return 文


@mcp.tool(name=_TOOL("orch_history"))
def orch_history(关键词: str = "", 条数: int = 20) -> str:
    """历史查询：这台编排器**做过什么、结论是什么、学到了什么**（不调模型、不跑命令）。

    why: 没有历史，每次调用都从零开始，"经验"只存在于人的记忆里。
    本工具把三样东西一次交出来：
      · 最近任务的判决与命令（可按关键词过滤 repo/cmd）；
      · 经验环（`learning.py`）里**已过门验证**的修法条数与失败门分布；
      · 每条经验的症状/根因摘要（供调用方判断"这类我修过没有"）。

    返回：{任务:[...], 经验:{条数,失败门分布,语言分布,最近几条}, 提示}
    """
    _db_sync_in()
    行 = []
    for jid, j in sorted(_jobs.items(), reverse=True):
        if len(行) >= max(1, min(200, int(条数 or 20))):
            break
        命令 = " ".join(str(x) for x in (j.get("cmd") or []))
        仓 = str(j.get("repo") or j.get("仓") or "")
        if 关键词 and (关键词 not in 命令) and (关键词 not in 仓) and (关键词 not in jid):
            continue
        行.append({"task_id": jid, "分类": _分类(j), "state": j.get("state"),
                   "repo": 仓, "cmd": 命令[:120],
                   "差分": (j.get("差分") or {}).get("说明") if j.get("差分") else None})
    经验 = {}
    try:
        from agentscope_orchestrator import learning as _学
        经验 = _学.统计()
        if 关键词:
            条们 = [x for x in _学._读()
                    if 关键词 in json.dumps(x, ensure_ascii=False)]
            经验["命中关键词条数"] = len(条们)
            经验["最近几条"] = [{"症状": x.get("症状"), "根因": x.get("根因"),
                                "做法": x.get("做法"), "验证": x.get("验证"),
                                "时间": x.get("时间")} for x in 条们[-5:]]
        else:
            经验["最近几条"] = [{"症状": x.get("症状"), "根因": x.get("根因"),
                                "验证": x.get("验证"), "时间": x.get("时间")}
                               for x in _学._读()[-5:]]
    except Exception as exc:                    # noqa: BLE001
        经验 = {"error": f"{type(exc).__name__}: {exc}"}
    return json.dumps({"ok": True, "任务数": len(行), "任务": 行, "经验": 经验,
                       "台账": _台账文件()},
                      ensure_ascii=False)


@mcp.tool(name=_TOOL("kb"))
def kb(action: str = "查", id: str = "", 关键词: str = "", 类别: str = "",
       正文: str = "", 强度: str = "", 等级: str = "SOLID", 范围: list[str] | None = None,
       repo: str = "", 上限字: int = 700) -> str:
    """项目知识库：**不改模型训练数据**也能积累项目经验的唯一入口（人可改、机器可判）。

    why: 长线项目里"单位/精度/禁则/术语"往往不在预训练中，或预训练里是别人的旧版本。
    没有外挂知识，强模型也会**合理地做错**，而门照样绿 —— 0.13.10 实测：
    冷组（无 KB）模型自造 `if abs(x)>=1: kHz else Hz`，残留 `import numpy`，fixloop 判达标、
    验收人判红。所以知识必须**外挂**（可查可改）、**算数**（能变判据门）。

    action:
      · `查`   按 关键词/类别 列条目（人写的是 Markdown，索引只是缓存）
      · `写`   新增一条（同 id 已存在会报错：要改走 `改`，这样版本与变更日志不丢）
      · `改`   改正文/强度/范围等，版本自增、变更留痕
      · `删`   删一条
      · `冲突` 报冲突（同 id 重复、"必须 X"与"禁止 X"同文），不静默覆盖
      · `门`   把规则转成**判据门**（给了 repo 就把范围 glob 具体化成文件）
      · `注入` 生成带「优先于你的训练先验」声明的提示块（≤上限字）
      · `统计` 条数/按类别/冲突数/可机判门数

    用法示例：项目验收人说"刻度一律 kHz、别用 numpy" ⇒
      kb(action="写", id="R-轴单位", 类别="规则", 强度="必须", 正文="刻度用 `kHz`", 范围=["plot/*.py"])
      kb(action="写", id="X-禁numpy", 类别="禁则", 强度="禁止", 正文="禁止 import numpy", 范围=["plot/*.py"])
      然后把 kb(action="门", repo=...) 返回的门加进 orch_run 的 gates ⇒ 从此**机器判**，不靠提示。

    ★ 诚实边界：`注入` 只是提示（模型可能不听）；**`门` 才是硬约束**。
      规则写得含糊（没反引号、没具体范围）就转不出门，本工具会明说转不出。
    """
    from agentscope_orchestrator import project_kb as _kb
    try:
        if action == "写":
            条 = {"id": id, "类别": 类别 or "规则", "强度": 强度 or "必须",
                  "等级": 等级 or "SOLID", "正文": 正文}
            if 范围:
                条["范围"] = list(范围)
            return json.dumps({"ok": True, "条": _kb.写(条)}, ensure_ascii=False)
        if action == "改":
            字段 = {k: v for k, v in
                    {"正文": 正文 or None, "强度": 强度 or None, "类别": 类别 or None,
                     "等级": 等级 or None, "范围": list(范围) if 范围 else None}.items() if v}
            return json.dumps({"ok": True, "条": _kb.改(id, **字段)}, ensure_ascii=False)
        if action == "删":
            return json.dumps({"ok": _kb.删(id)}, ensure_ascii=False)
        if action == "冲突":
            return json.dumps({"ok": True, "冲突": _kb.冲突()}, ensure_ascii=False)
        if action == "门":
            return json.dumps({"ok": True, "门": _kb.门(仓=repo or None),
                               "说明": "把这里的每条门原样塞进 orch_run 的 gates"},
                              ensure_ascii=False)
        if action == "注入":
            return json.dumps({"ok": True, "块": _kb.注入(关键词=关键词, 范围="", 上限字=上限字)},
                              ensure_ascii=False)
        if action == "统计":
            return json.dumps({"ok": True, **_kb.统计()}, ensure_ascii=False)
        return json.dumps({"ok": True, "条": _kb.查(关键词=关键词, 类别=类别)}, ensure_ascii=False)
    except Exception as exc:                    # noqa: BLE001 知识库坏了要能看见原因
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


@mcp.tool(name=_TOOL("orch_calibrate"))
async def orch_calibrate(limit: int = 0) -> str:
    """自证：跑**真值已知**的标定语料，量「判决准不准」（不测速度，不调模型）。

    why: 没有真值的"判得准"只是说法。这套语料用**公开接口** `orch_run` 造 21 类情形
    （达标/产物错/调用方错/基设故障/环境耦合/伪绿/抖动/取消/非终态），
    真值由构造决定 ⇒ 能算出混淆矩阵与**危险错误**（假绿、基设↔产物互认错）。

    返回：准确率 + 危险错误数 + 混淆矩阵 + 修法覆盖率 + 载荷/证据字节比。
    limit: 0=全量；>0=只跑前 N 条（快速自检）。

    ★ 诚实边界：语料是**我构造的**，n=21；1.0 只说明"我造的案例我都判对了"，
      不说明外部真实任务上的准确率。它抓到过 7 个真缺陷（见台账），那才是它的价值。
    """
    from agentscope_orchestrator import calib as _cb
    try:
        出 = await asyncio.wait_for(_cb.跑语料(limit or None), timeout=1800)
    except asyncio.TimeoutError:
        return json.dumps({"分类": "进行中", "说明": "标定超时（语料太重）；调小 limit 重试",
                           "修法": {"工具": "orch_calibrate", "参数": {"limit": 6}}},
                          ensure_ascii=False)
    except Exception as exc:                    # noqa: BLE001
        return json.dumps({"分类": "基础设施不可用",
                           "error": f"{type(exc).__name__}: {exc}",
                           "修法": {"工具": "auto_scan", "参数": {}}}, ensure_ascii=False)
    摘 = {k: v for k, v in 出.items() if k != "行"}
    摘["行"] = [{k: r.get(k) for k in ("id", "真值", "判决", "耗时_s", "载荷字节")}
                for r in 出.get("行") or []]
    摘["达标"] = 出.get("危险错误") == 0
    return json.dumps(摘, ensure_ascii=False)


@mcp.tool(name=_TOOL("orch_wait"))
async def orch_wait(task_id: str, timeout_s: int = 1800, max_chars: int = 20000) -> str:
    """等某任务到终态并返回**完整可决策载荷**（分类/证据/修法/成本/产物内容）。

    与 orch_run(wait=True) 的区别：本工具用于"已经提交过的任务"。
    两个工具合起来把"提交→结论"压到 **1~2 次 MCP 往返**。
    超时不杀任务，返回未到终态 + 修法（再等一次，别轮询 task_status）。
    """
    终 = await _等终态_a(task_id, float(timeout_s))
    if task_id not in _jobs:
        return json.dumps({"ok": False, "error": f"task {task_id} 不存在",
                           "修法": {"工具": "list_tasks", "参数": {}}},
                          ensure_ascii=False)
    _成本累(task_id, "orch_wait")
    out = _payload(task_id, max(2000, int(max_chars)))
    out["等待"] = 终
    if not 终.get("等到了"):
        out["修法"] = {"工具": "orch_wait",
                       "参数": {"task_id": task_id, "timeout_s": timeout_s},
                       "说明": "仍在跑；再等一次即可，不要用 task_status 轮询"}
    文 = json.dumps(out, ensure_ascii=False, indent=1)
    _成本补(task_id, len(文))
    out["成本"] = _成本读(task_id, _jobs.get(task_id))
    文 = json.dumps(out, ensure_ascii=False, indent=1)
    _成本补(task_id, len(文) - out["成本"]["载荷字节"])
    return 文



_常驻启动读数: dict = {}


def _启动常驻同步() -> "threading.Thread":
    """编排器**自己主动**扫这个仓（R303）—— 用户原话：

    > 「启动编排器，这个编排器**自己就要主动扫**这个文件夹下有什么，
    >   是这个编排器告诉你的。不是你自己去看的，也不是你告诉我的。」

    ★ 为什么放**后台线程**：首次冷同步实测 **205.9s**（含 Lean 5,595 文件）；
      放在 `mcp.run` 之前就是「编排器三分多钟不响应」，比不做还坏。
      第二次只有 **9.1s、读盘 0**（戳筛），所以这是**一次性的**代价。
    ★ 失败**不阻塞服务器**，但把原因留在 `_常驻启动读数["错"]` 里 ——
      「降级不是失败，但谎报没降级是最坏的失败」。
    """
    def 跑() -> None:
        try:
            from agentscope_orchestrator import mem_live as _ml
            _常驻启动读数.update(_ml.自动同步())
        except Exception as e:                       # noqa: BLE001
            _常驻启动读数["错"] = f"{type(e).__name__}: {e}"
    t = threading.Thread(target=跑, name="常驻仓况同步", daemon=True)
    t.start()
    return t


@mcp.tool(name=_TOOL("repo_state"))
def 仓况(同步: bool = False, 预算字: int = 1200) -> str:
    """**这个仓里现在有什么** —— 常驻仓况（精确到文件夹/文件/符号）。

    为什么有这个工具（用户 2026-11-07 当面指出）：
      「怎么知道这个文件夹下有什么东西？哪些有用哪些没用？怎么真的知道有什么没什么？」
      「要实现局部任意召回相关内容不丢，全局遍历知识图谱一次知道完整全局状态
        **不丢信息，不用遍历整个仓库**的效果。」

    它读的是**常驻符号表**（`mem_live`），不是现扫：
      · 增量靠 `(mtime_ns, size)` 戳筛 —— 未变文件**一次盘都不读**（实测第 2 次读盘 **0**）；
      · 状态落盘 `products/mem/活符号表.pkl`，**跨进程存活** ⇒ 「不用遍历整个仓库」不是口号；
      · 扫什么由**档案 `memory:` 段**决定（换项目 = 换档案），本工具不写死任何项目路径。

    参数：
      同步    True ⇒ 先增量同步一次再报（默认 False：只读现有常驻表，最快）。
      预算字  概要截断长度。

    返回：仓况简报 + 上次同步读数 + 目录级分布 + **无仓内调用者的符号数**。
      ★ 「无调用者」是**线索不是判决**：入口（main / #[test] / pub 外部 API）
        天然没有仓内调用者。
    """
    from agentscope_orchestrator import mem_live as _ml
    同步读数 = None
    if 同步:
        同步读数 = _ml.自动同步()
    else:
        _ml.确保载入()          # ★ 显式查询 ⇒ 可以载盘（几秒）；注入路径不载
    文 = _ml.简报(预算字=预算字)
    if not _ml.规模().get("文件"):
        文 = ("【仓况·空】常驻表是空的。可能原因：① 启动同步还在跑（首次冷同步实测 205.9s）"
              "；② 档案没有 `memory:` 段。**这不是「仓里没东西」** —— "
              "空表与空仓必须分得开，否则就是「看不见当没有」。")
    出 = {"常驻": 文, "本次同步": 同步读数, "启动同步": dict(_常驻启动读数),
          "规模": _ml.规模(), "上次同步": None if _ml.上次同步() is None
          else _ml.上次同步().行(), "告警": list(_ml.告警())}
    # ★ 分区摘要（不吐 411 KB 全量档）：回答「这个仓里有哪些项目、各是什么」
    分 = _ml.取分区()
    if 分:
        一方 = [p for p in (分.get("项目们") or []) if str(p.get("类型", "")).startswith("一方")]
        出["分区"] = {
            "目录数": 分.get("目录数"), "被剪枝目录数": 分.get("被跳目录数"),
            "文件数": 分.get("文件数"), "项目数": 分.get("项目数"),
            "工位残留": 分.get("工位数"),
            "剪枝按类": 分.get("剪枝按类"),
            "一方项目": [{"根": p["根"], "清单": p["清单"], "语言": p["语言"],
                          "文件": p["文件"], "按角色": p["按角色"]} for p in 一方[:40]],
            "口径": 分.get("口径"), "剪枝边界": 分.get("剪枝边界"),
            "工位判据": "`<X>/repo` 自己是一个项目根 ⇒ X 是一次性工位（结构性，可复核）",
        }
    return json.dumps(出, ensure_ascii=False, indent=1)


@mcp.tool(name=_TOOL("repo_symbol"))
def 仓内符号(名: str = "", 文件: str = "", 上限: int = 30) -> str:
    """**局部任意召回**：按符号名或文件路径，把这个仓里相关的东西一次拿全。

    与 `仓况` 的分工：`仓况` 是**全局状态**（一眼知道有什么），
    本工具是**局部召回**（问某个名字/某个文件）。两者读的是**同一张常驻表**。

    参数：
      名   符号名（精确）。给了就回：定义处、体指纹、**谁调用它**。
      文件 仓内**相对路径**（如 `机械循环/结构记忆网络.rs`）。给了就回它定义的符号、
            以及它引用到的名字。
    两者都给 ⇒ 都回。都不给 ⇒ 回面积的错，**不猜**。
    """
    from agentscope_orchestrator import mem_live as _ml
    _ml.确保载入()              # ★ 显式查询 ⇒ 载盘
    表 = _ml.现存()
    if not 名 and not 文件:
        return json.dumps({"ok": False, "错": "`名` 与 `文件` 至少要给一个（不猜用户想问什么）"},
                          ensure_ascii=False)
    出: dict = {"ok": True}
    if 名:
        符们 = 表.查(名)
        出["名"] = {
            "定义处": [{"文件": s.文件, "行": s.起, "种类": s.种类,
                        "结构指纹": s.结构指纹[:16]}
                       for s in 符们[:上限]],
            "定义处数": len(符们),
            "调用它的文件": list(表.调用者(名))[:上限],
            "被引文件数": len(表.调用者(名)),
        }
        if not 符们:
            出["名"]["说明"] = "表里没有这个名字 —— **先确认它是否真在这张表的作用域里**，再谈「不存在」"
    if 文件:
        相对 = 文件.replace("\\", "/").lstrip("./")
        # ★ 兜底：表里的路径是**仓相对**；用户/模型常按记忆写 `机械循环/x.rs`
        #   而不是 `src/机械循环/x.rs`。**「路径不对」和「东西不在表里」必须分开** ——
        #   否则一次笔误会读成「这个仓里没有它」，正是「看不见当没有」。
        候选 = []
        if 相对 not in 表._文哈希 and 相对 not in 表.墓碑们():
            候选 = sorted(p for p in 表._文哈希
                          if p == 相对 or p.endswith("/" + 相对) or p.endswith(相对))
            if len(候选) == 1:
                相对 = 候选[0]
        符们 = 表.查_文件(相对)
        出["文件"] = {"路径": 相对, "在表里": 相对 in 表._文哈希,
                      "定义的符号数": len(符们),
                      "定义的符号": [{"名": s.名, "行": s.起, "种类": s.种类}
                                     for s in 符们[:上限]],
                      "引用的名字数": len(表.引用名(相对)),
                      "它是墓碑": 相对 in 表.墓碑们()}
        if 相对 not in 表._文哈希 and 相对 not in 表.墓碑们():
            if 候选:
                出["文件"]["说明"] = (f"给的路径直接查不到，但有 {len(候选)} 个后缀匹配 ⇒ "
                                     "**无法唯一确定**，请给仓相对全路径")
                出["文件"]["后缀匹配样例"] = 候选[:5]
            else:
                出["文件"]["说明"] = ("该路径不在常驻表里 —— 可能是：① 档案 `memory:` 没声明它所在的根；"
                                      "② 被 `skip:` 剪掉了；③ 后缀不在声明内。"
                                      "**「表里没有」不等于「仓里没有」**")
    return json.dumps(出, ensure_ascii=False, indent=1)


def main() -> int:
    from ._winutf8 import 确保UTF8输出 as _u8; _u8()
    # ★ R546：`--help` 必须**立刻返回**。不认它时，`orch-mcp --help` 会被当成
    #   「起 stdio 服务」：stdin 立刻 EOF ⇒ **一个字节都不打印、退出码 0**。
    #   ⇒ 用户看到「跑完了但没输出」，这是最坏的一类失败：**它看起来成功了**。
    if any(x in sys.argv[1:] for x in ("-h", "--help", "帮助")):
        print("用法：orch-mcp [-h|--help]\n"
              "\n"
              "  以 MCP stdio 服务方式启动（给 AI 客户端连的入口，不是给人敲的）。\n"
              "  工具面由档案决定；这里不重复列出（用 guide 工具查）。\n"
              "\n"
              "  -h/--help  本帮助（立刻返回，不起服务、不建索引）\n"
              "\n"
              "  ★ 边界：本进程讲 MCP 协议（stdin/stdout），直接敲它不会有人话输出 ——\n"
              "    那是**协议**，不是缺陷。要体检请用 orch-check / orch-doctor。")
        return 0
    # ★ R548：状态根只在**进程入口**设一次（见本文件里 R548 那段注释）。
    os.environ.setdefault("ORCH_STATE_DIR", _状态根提示548)
    _确保索引544()          # ★ 起服务前建一次（之后就幂等）
    # ★ 启动回收孤儿 running（死进程遗留），防依赖链永久阻塞
    _reap_orphans()
    # ★ 2026-11-07（R303）：**编排器自己主动扫这个仓** —— 后台，不阻塞 stdio
    _启动常驻同步()
    mcp.run(transport="stdio")
    return 0
    return 0


@mcp.tool(name=_TOOL("gate_power"))
async def gate_power(repo: str, cmd: list[str], mutations: int = 6) -> str:
    """★★★【量门的区分力】往产物里注机械性错误，看这个门**能抓住几种**。

    为什么需要它（这是本层最该被问的问题）：`orch_fix` 的全部结论建立在"门绿"上，
    而门绿只证明**这一条判据**没意见。0.12.5 真事故：模型把 `_RUST_ROOT` 改成
    `Path(__file__).resolve().parents[2]`，门（只要求"已定义 + 是真目录"）**绿了**，
    语义却与原意不同。这类事**不可彻底消灭**（"改得对不对"等价于自动程序验证，一般不可判），
    但它可以被**度量** —— 度量结果就是"该加什么门"的依据。

    怎么判：基线先绿（否则不测，直接告诉你先让门在正确产物上变绿）→ 逐个注入变异
    （返回值置空 / 删断言 / `==`→`!=` / `!=`→`==` / `True`→`False` / 文件尾 `sys.exit(0)`）
    → 每注一次跑一次门 → **门仍绿 = 这个变异存活 = 门有洞**。

    返回：{区分力(抓住/注入), 注入, 抓住, 存活[{文件,变异}], 变异明细, 边界, 建议}
    区分力低 ⇒ 按『建议』加门族（grep_required / whole_reading / test_counts /
    `instances=2 + instances_mode="copy"` 抓伪绿）。

    ★ 边界（如实）：变异是**机械的**，区分力高只说明"挡得住这类机械错误"，
      **不**说明"改得对"；只支持 Python 源；在**临时副本**上做，不动你的仓。
    """
    from agentscope_orchestrator import gate_power as _gp
    果 = await _gp.测区分力(repo, list(cmd or []), mutations)
    return json.dumps(果, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())


