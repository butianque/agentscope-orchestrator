"""批号分配 + 人类可读回执渲染（项目无关）。

机器底料仍是引擎各自的 JSON/.jsonl（判据门/续跑/回放要它）；本模块在其*之上*
每轮渲染一份 markdown 回执，头部字段（批号/窗口/性质/执行时间锚/基线提交）与
项目既有回执习惯一致，正文按引擎结果生成。

批号前缀与计数目录全部来自档案（receipt_prefix / audit_dir），工具不写死任何
项目的号段习惯。批号文件 <audit>/<prefix>_seq.json（{"last":N}），递增领号。

⚠ 兼容：旧调用方传的是**工程根 Path**（而非 Profile）。为不破坏既有调用，
   `claim_batch` / `render` 同时接受 Path 与 Profile：传 Path 时按该根下的
   `.orchestrator` 目录与默认前缀 `ORCH` 处理（通用兜底，非任何具体项目）。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


def _可写(d: Path) -> bool:
    """真写一个探针文件再删掉 —— 不靠 `os.access` 猜。

    ★ 事故来源（2026-11-01）：本机 `<项目根>\\.audit_trail` 对 **python 进程** 是
      `PermissionError [Errno 13]`，而同一 token 的 powershell 能写（按进程映像拦的，
      不是 ACL）。`os.access` 在这台机器上会**说能写** ⇒ 只有真写一次才是判据。
    """
    try:
        d.mkdir(parents=True, exist_ok=True)
        探 = d / f".orch_write_{os.getpid()}"
        探.write_text("x", encoding="utf-8")
        探.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _回退目录() -> Path | None:
    """审计目录不可写时，回执/批号落这里：状态根下的 `receipts/`（`STATE_ROOT` 由档案定）。"""
    try:
        from . import profile as _prof          # 延迟导入，避免与 taixu_mcp 形成循环
        return Path(_prof.STATE_ROOT) / "receipts"
    except Exception:                            # noqa: BLE001
        return None


def _落点(target) -> tuple[Path, str, str | None, Path | None]:
    """把目标解析成 (实际落点, 前缀, 降级原因, 审计目录)。

    ★ 2026-11-01 修的是**静默丢数据**：原实现直接往审计目录 `write_text`，
      写不进就整份回执丢失（连 .md 都没有），只剩一行 `回执渲染失败：Permission denied`
      挂在日志里 —— 看起来像"回执不重要"，实际是**每一条回执都没了**。
      现在：审计目录不可写 ⇒ 落回状态根并**如实标降级原因**（.md 里会写明），
      不再把"没写"和"写好了"混在一起。
    """
    audit: Path | None = None
    prefix = getattr(target, "receipt_prefix", None) or "ORCH"
    raw = getattr(target, "audit_dir", None)
    if raw is not None:
        audit = Path(raw)
    else:
        audit = Path(target) / ".orchestrator"     # 兼容旧签名：Path 视为工程根
        prefix = "ORCH"
    if _可写(audit):
        return audit, prefix, None, audit
    回退 = _回退目录()
    if 回退 is not None and _可写(回退):
        return 回退, prefix, f"审计目录不可写（{audit}），已降级到状态根", audit
    return audit, prefix, f"审计目录与状态根都不可写（{audit}）", audit


def _coerce(target) -> tuple[Path, str]:
    """把 Profile 或 Path 统一成 (落点目录, prefix)。"""
    落点, prefix, 原因, _审计 = _落点(target)
    if 原因:
        # 如实说，不静默：调用方（回执渲染器）会把原因写进 .md 头部。
        最近降级原因[0] = 原因
    return 落点, prefix


# 最近一次降级的**原因**（None = 正常落盘）。状态放在模块级而不是返回值里，
# 是因为 `claim_batch` 的返回类型被旧调用方钉成 int（改签名会破兼容）。
最近降级原因: list[str | None] = [None]




def claim_batch(target) -> int:
    """领取下一个批号。target 可为 Profile 或工程根 Path。

    ★ 2026-11-01：计数文件写不进时**不再抛**（原来会让整份回执渲染失败）。
      改为：退回**时钟批号**并把原因记进 `最近降级原因` —— 批号不再单调递增，
      但"回执丢了"比"批号不连续"严重得多，且原因会写在 .md 里，不被静默。
    """
    audit, prefix = _coerce(target)
    seqf = audit / f"{prefix}_seq.json"
    last = 0
    if seqf.exists():
        try:
            last = int(json.loads(seqf.read_text(encoding="utf-8"))["last"])
        except Exception:  # noqa: BLE001 计数文件损坏则回退
            last = 0
    nxt = last + 1
    try:
        seqf.write_text(json.dumps({"last": nxt}), encoding="utf-8")
    except OSError as e:
        最近降级原因[0] = (f"批号计数文件写不进（{seqf.name}: {e.__class__.__name__}），"
                          f"改用时钟批号")
        return int(time.strftime("%m%d%H%M")) % 9000 + 1000
    return nxt


def short_head(repo: Path) -> str:
    """尽力取基线提交短哈希；失败返回 —。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        return out.stdout.strip() or "—"
    except Exception:  # noqa: BLE001
        return "—"


def render(*, repo_root, batch: int, window: str, nature: str,
           topic: str, summary: str, gates: dict | None = None,
           gaps: list[dict] | None = None, product: str | None = None,
           notes: list[str] | None = None,
           extra_sections: dict[str, str] | None = None) -> Path:
    """渲染一份 <prefix>-<batch>_回执_<topic>.md 到 audit_dir，返回路径。

    repo_root 可为 Profile（推荐）或工程根 Path（兼容旧调用）。
    """
    audit, prefix = _coerce(repo_root)
    # git 基线：Profile 用其 repo_root；Path 用自身
    git_root = getattr(repo_root, "repo_root", None) or Path(repo_root)

    fn = f"{prefix}-{batch}_回执_{topic}.md"
    out = audit / fn
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    L: list[str] = []
    A = L.append
    A(f"# {prefix}-{batch}_回执_{topic}")
    A("")
    A(f"**批号**: {prefix}-{batch}")
    A(f"**窗口**: {window}")
    A(f"**性质**: {nature}")
    A(f"**执行时间锚**: {ts}（本机墙钟）")
    A(f"**基线提交**: `{short_head(git_root)}`")
    A("")
    if 最近降级原因[0]:
        # ★ 如实标降级：读的人必须知道这份回执**没落在它该在的地方**，
        #   否则"在 .audit_trail 里找不到回执"会被读成"任务没跑完"。
        A(f"> ⚠ 落点降级：{最近降级原因[0]}")
        A(f"> 实际位置：`{audit}`")
        A("")
    A("---")
    A("")
    A("## 〇、一句话结论")
    A("")
    A(summary.strip())
    A("")
    if gates:
        A("---")
        A("")
        A("## 判据门（机器可算，门名: 真值）")
        A("")
        A("| 判据门 | 结果 |")
        A("|:--|:-:|")
        for k, v in gates.items():
            A(f"| {k} | {'✅ True' if v is True else ('⚠️ False' if v is False else str(v))} |")
        A("")
    if gaps:
        A("---")
        A("")
        A("## 捕获缺口（编译证据 `file:line` + error 码）")
        A("")
        for g in gaps:
            f = g.get("file", "")
            ln = g.get("line", "")
            cd = g.get("code") or ""
            msg = (g.get("message") or "")[:180]
            A(f"- `{f}:{ln}` {f'[{cd}] ' if cd else ''}{msg}")
        A("")
    if product:
        A("---")
        A("")
        A("## 产物")
        A("")
        A(f"- `{product}`")
        A("")
    if extra_sections:
        for hdr, body in extra_sections.items():
            A("---")
            A("")
            A(f"## {hdr}")
            A("")
            A(body.strip())
            A("")
    if notes:
        A("---")
        A("")
        A("## 未核项 / 诚实边界")
        A("")
        for n in notes:
            A(f"- {n}")
        A("")
    try:
        out.write_text("\n".join(L), encoding="utf-8")
    except OSError as e:
        raise OSError(
            f"回执无处可写：{out}（{e.__class__.__name__}: {e}）。"
            f"审计目录与状态根都不可写 ⇒ 回执不会静默丢弃，这里是响亮的失败。"
            f"修法：把状态根指到可写处（ORCH_STATE_DIR=<可写目录>），"
            f"或让审计目录对**本进程**可写。") from None
    # ★ 2026-11-03：回执落地 ⇒ **自动落一个坐标**，把这次回执挂到全局链上。
    #
    #   为什么必须在这里做（而不是"以后加个命令"）：
    #     链的价值全在**自动**。只要有一环需要人记得去调，长线上就一定会漏 ——
    #     本项目已经反复吃过这个亏（知识库接在 fixloop 里、却永远注入空串）。
    #     回执是"这一轮做过什么"的**唯一权威产物**，它落地的瞬间就是最合适的挂链点。
    #
    #   为什么不让挂链失败拖垮回执：
    #     回执本身是本轮的事实凭据，链是可重建的缓存（见 coord_chain.重建）。
    #     优先级：**回执先保住**。挂链失败要**如实说出来**（写进 .md 的诚实边界段），
    #     但不许把已经写好的回执变成"没写"。
    try:
        from . import coord_chain as _链
        _链.追加("回执", out.name)
        挂链 = None
    except Exception as e:                       # noqa: BLE001
        挂链 = f"{type(e).__name__}: {e}"
    if 挂链:
        # 降级要可见：读回执的人必须知道"这份回执没进链"，否则链上缺一格会被
        # 误读成"这轮没做" —— 那正是坐标链要消灭的那类误判。
        try:
            with out.open("a", encoding="utf-8") as f:
                f.write(f"\n---\n\n> ⚠ 未挂入坐标链：{挂链}\n"
                        f"> 链是可重建的缓存（`coord_chain.重建`），回执本体不受影响。\n")
        except OSError:
            pass
    return out
