"""只读任务控制台（编排器自持）：分页浏览 任务/逐窗门/审计/报告。

薄 DSH 架构：DSH 只做 提交 + 轻量轮询 list_tasks/task_status；
大载荷（read_report 全文 / audit_log 全审计 / 各窗文本）只在本控制台按需查看，
按页返回，浏览器永不一次性载入大 blob。

权限边界（不破）：只读。不暴露 改判据 / 改库 / 改产物 / 发任务。

用法：
    venv\\Scripts\\python.exe src\\agentscope_orchestrator\\taixu_console.py [--port 8088]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentscope_orchestrator import profile as prof_mod

ORCH = prof_mod.TOOL_ROOT
STATE = prof_mod.STATE_ROOT
_prof = prof_mod.get_profile()
FORMAL_PROOF = (_prof.artifact.root.parent if _prof.artifact.root else None)
ARTIFACT_DIR = _prof.artifact.root
PRODUCTS = _prof.products_dir
# ★ 2026-10-31：库在 STATE（与 taixu_mcp 同一落点）；此前读 TOOL_ROOT/jobs ⇒ 装成 wheel
#   后读到的是 `<venv>/Lib/jobs` 那个空库,控制台永远显示 0 条。
DB_FILE = STATE / "jobs" / "jobs.db"

_PORT = 8088
_PAGE_DEFAULT = 25


def _console_base() -> str:
    return f"http://127.0.0.1:{_PORT}"


def _load_jobs() -> list[tuple[str, dict]]:
    """只读打开 jobs.db，返回全部 (id, job)。文件不存在返回空。"""
    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
    try:
        rows = []
        for rid, rdata in conn.execute("SELECT id, data FROM jobs"):
            try:
                rows.append((rid, json.loads(rdata)))
            except Exception:
                continue
        return rows
    finally:
        conn.close()


def _window_text_len(job: dict) -> int:
    s = job.get("status") or {}
    return sum(len((w.get("text") or "")) for w in s.get("windows", []))


def _safe_product(job: dict) -> Path | None:
    """产物路径白名单校验（只允许产物库/ 与 products/ 内），越界返回 None。"""
    product = (job.get("status") or {}).get("product")
    if not product:
        return None
    p = Path(product).resolve()
    allowed = [str(PRODUCTS.resolve())]
    if ARTIFACT_DIR:
        allowed.append(str(ARTIFACT_DIR.resolve()))
    if p.is_file() and any(str(p).startswith(a) for a in allowed):
        return p
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = f"{_prof.console_title}/1.0"

    # ── 路由 ────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            q = parse_qs(parsed.query)
            if parsed.path in ("/", "/tasks"):
                self._task_list(q)
            elif parsed.path.startswith("/task/"):
                self._task_detail(parsed.path, q)
            elif parsed.path.startswith("/report/"):
                self._report(parsed.path)
            else:
                self._json(404, {"ok": False, "error": "not found"})
        except Exception as exc:  # 诚实边界：服务端异常照实回
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    # ── / 任务列表（分页，只取元信息，不载 window 文本） ──
    def _task_list(self, q):  # type: ignore[no-untyped-def]
        page = max(1, int((q.get("page") or ["1"])[0]))
        per = min(int((q.get("per_page") or [str(_PAGE_DEFAULT)])[0]), 100)
        jobs = sorted(_load_jobs(), key=lambda r: r[0])
        total = len(jobs)
        start, end = (page - 1) * per, page * per
        items = []
        for _rid, j in jobs[start:end]:
            s = j.get("status") or {}
            p = _safe_product(j)
            items.append({
                "id": j.get("id"), "state": j.get("state"),
                "unit_id": j.get("unit_id"), "module": j.get("module"),
                "theorem": j.get("theorem"),
                "passed": s.get("passed"), "failed": s.get("failed"),
                "promote": j.get("promote"), "dry": j.get("dry"),
                "product": str(p) if p else None,
                "error": j.get("error"),
                "windows_text_bytes": _window_text_len(j),
                "url": _console_base() + f"/task/{j.get('id')}",
            })
        self._json(200, {"ok": True, "total": total, "page": page,
                         "per_page": per, "pages": (total + per - 1) // per,
                         "items": items})

    # ── /task/<id> 单任务：逐窗门 + 审计（可回放） ──
    def _task_detail(self, path: str, q):  # type: ignore[no-untyped-def]
        task_id = path.split("/task/", 1)[1]
        jobs = dict(_load_jobs())
        j = jobs.get(task_id)
        if j is None:
            return self._json(404, {"ok": False, "error": f"task {task_id} 不存在"})
        s = j.get("status") or {}
        page = max(1, int((q.get("page") or ["1"])[0]))
        per = min(int((q.get("per_page") or [str(_PAGE_DEFAULT)])[0]), 100)
        windows = s.get("windows", [])
        start, end = (page - 1) * per, page * per
        body = {
            "ok": True,
            "id": task_id, "unit_id": j.get("unit_id"),
            "module": j.get("module"), "theorem": j.get("theorem"),
            "state": j.get("state"), "rc": j.get("rc"), "error": j.get("error"),
            "finished_at": j.get("finished_at"), "channel": j.get("channel"),
            "promote": j.get("promote"), "n_windows": j.get("n_windows"),
            "min_pass": j.get("min_pass"),
            "product": s.get("product"), "adopted_window": s.get("adopted_window"),
            "summary": {"state": s.get("state"), "passed": s.get("passed"),
                        "failed": s.get("failed"),
                        "resumed": s.get("resumed"),
                        "final_gates": s.get("final_gates")},
            "window_page": {"page": page, "per_page": per,
                            "total": len(windows),
                            "windows": windows[start:end]},
            "gates": s.get("gates", []),
            "phrase": self._phrase(s),
        }
        jid_sort = sorted(s.get("audit", []), key=lambda a: a.get("idx", 0))
        body["audit_total"] = len(jid_sort)
        self._json(200, body)

    # ── /report/<id> 读产物（服务端截断，防大 blob） ──
    def _report(self, path: str) -> None:  # noqa: D102
        task_id = path.split("/report/", 1)[1]
        jobs = dict(_load_jobs())
        j = jobs.get(task_id)
        if j is None:
            return self._json(404, {"ok": False, "error": f"task {task_id} 不存在"})
        p = _safe_product(j)
        if p is None:
            return self._json(404, {"ok": False, "error": "该任务无产物或路径越界",
                                    "state": j.get("state")})
        data = p.read_text(encoding="utf-8")
        limit = 200_000
        truncated = len(data) > limit
        self._json(200, {"ok": True, "task_id": task_id, "path": str(p),
                         "byte_len": len(data.encode("utf-8")),
                         "truncated": truncated, "content": data[:limit]})

    # ── 辅助 ────────────────────────────────────────────────
    def _phrase(self, s: dict):  # type: ignore[no-untyped-def]
        st = s.get("state")
        if st == "staged":
            return "已收敛，产物暂存 products/（未写回产物库，可 --promote）"
        if st == "promoted":
            return "已收敛并写回产物库，全部门复跑通过"
        if st == "convergence_failed":
            return f"未收敛：通过窗 {s.get('passed')}<min_pass={s.get('min_pass')}"
        return st or ""

    def _json(self, code: int, obj):  # type: ignore[no-untyped-def]
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[console] " + (fmt % args) + "\n")


def main() -> int:
    global _PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8088)
    a = ap.parse_args()
    _PORT = a.port
    if not DB_FILE.exists():
        sys.stderr.write(f"[console] WARN jobs.db 不存在: {DB_FILE}（可能尚无任务）\n")
    print(f"[console] 只读任务控制台 http://127.0.0.1:{_PORT}/  "
          f"(DB={DB_FILE})", file=sys.stderr, flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", _PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())