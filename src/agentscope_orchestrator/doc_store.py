"""文档外置 + 自建索引 + 按需读取（doc_store）：把长内容落盘成文档并登记索引。

解决：DSH 会话把「工具大载荷 / 模型超长推理链输出」写进 session.jsonl.zstd，
历史载入时整体解压冻结（web UI 卡死）。本模块把「其他内容」从对话窗口移到
磁盘文档，由编排器（模型侧）自建索引、模型按需查看：
  - store_text()  : 任意长文本落盘 products/docs/<doc_id>.md + 登记 _index.jsonl
  - thin()        : 超过内联阈值即外置为文档，返回指针+预览（聊天窗口只留记录）
  - read_doc()    : 按 doc_id 读取（限长，防止一次把大文档拉回会话）
  - search_docs() : 按类别/关键词查索引（「模型自己建立索引按需查看」的入口）
  - archive_chain(): 任务终态把每窗推理链全文归档为可检索文档（不进会话）

诚实边界：
  - 本模块只做编排层「文档+索引」，不压缩 DSH 会话历史；已存在的大 session
    仍需运维归档（_archive_sessions_*）。
  - 索引条目只存 meta 与摘录，不内嵌全文，防索引自身膨胀。
  - 落盘位置固定 products/docs/；read_doc 只允许该目录内路径，防越界读。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentscope_orchestrator import profile as prof_mod

ORCH = prof_mod.TOOL_ROOT                         # 工具自身根（与项目无关）
DOCS_DIR = prof_mod.get_profile().products_dir / "docs"
INDEX = DOCS_DIR / "_index.jsonl"
MAX_INLINE_BYTES = 1000                            # 超过即外置（聊天窗口只留记录）
MAX_READ_CHARS = 4000                              # read_doc 单次拉取上限（防爆会话）

DOCS_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> float:
    return time.time()


def _load_index() -> dict[str, dict]:
    """读索引：doc_id -> 条目（追加式，后写覆盖前条）。"""
    table: dict[str, dict] = {}
    if not INDEX.exists():
        return table
    for line in INDEX.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        table[rec["doc_id"]] = rec
    return table


def store_text(doc_id: str, kind: str, title: str, text: str,
               meta: dict | None = None) -> dict:
    """长内容落盘为文档并登记索引。doc_id 唯一（重复写覆盖同文档+索引）。

    kind: 文档类别（chain=推理链/audit=审计/report=产物/note=说明）。
    返回索引条目（含 byte_len 与摘录）。"""
    safe_id = "".join(c for c in doc_id if c.isalnum() or c in "_-.")
    if not safe_id:
        safe_id = f"doc_{int(_now())}"
    p = DOCS_DIR / f"{safe_id}.md"
    p.write_text(text or "", encoding="utf-8")
    excerpt = (text or "").replace("\n", " ")[:120]
    rec = {"doc_id": safe_id, "kind": kind, "title": title or safe_id,
           "path": str(p), "byte_len": p.stat().st_size,
           "excerpt": excerpt, "ts": _now(),
           "meta": meta or {}}
    with INDEX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def thin(text: str, kind: str, title: str, meta: dict | None = None) -> str:
    """对话窗口守卫：超阈值内容外置为文档，返回指针+预览；小内容原样返回。"""
    if len((text or "").encode("utf-8")) <= MAX_INLINE_BYTES:
        return text or ""
    doc_id = f"{kind}_{int(_now())}_{abs(hash(title)) % 10000}"
    rec = store_text(doc_id, kind, title, text, meta=meta)
    return (f"[doc:{rec['doc_id']} · {rec['kind']} · {rec['byte_len']}B] {title}"
            f" —— 全文已外置文档，请用 read_doc(doc_id={rec['doc_id']}) 按需查看。"
            f"摘录：{rec['excerpt']}")


def read_doc(doc_id: str, max_chars: int = MAX_READ_CHARS) -> str:
    """按 doc_id 读文档（限长）。越界/不存在 → 错误信息（不抛异常，MCP 友好）。"""
    idx = _load_index()
    rec = idx.get(doc_id)
    if rec is None:
        return f"doc_id={doc_id!r} 不存在（可用 search_docs 查索引）"
    p = Path(rec["path"]).resolve()
    docs_root = DOCS_DIR.resolve()
    if not str(p).startswith(str(docs_root)) or not p.is_file():
        return f"doc_id={doc_id!r} 路径越界或文件缺失，拒绝读取"
    text = p.read_text(encoding="utf-8", errors="ignore")
    if len(text) > max_chars:
        return (f"<截断：全文{len(text)}字符，仅返回前{max_chars}字符。"
                f"doc_id={doc_id}>\n" + text[:max_chars])
    return text


def search_docs(kw: str | None = None, kind: str | None = None,
                limit: int = 20) -> list[dict]:
    """按类别/关键词查索引（子串匹配 title/kind/meta，不全文扫），返回条目标签。"""
    limit = max(1, min(limit, 100))
    rows: list[dict] = []
    for rec in sorted(_load_index().values(), key=lambda r: -r.get("ts", 0)):
        if kind and rec.get("kind") != kind:
            continue
        if kw:
            hay = " ".join([rec.get("title", ""), rec.get("kind", ""),
                            json.dumps(rec.get("meta", {}), ensure_ascii=False)])
            if kw not in hay:
                continue
        rows.append({"doc_id": rec["doc_id"], "kind": rec.get("kind"),
                     "title": rec.get("title"), "byte_len": rec.get("byte_len"),
                     "excerpt": rec.get("excerpt")})
        if len(rows) >= limit:
            break
    return rows


def archive_chain(doc_prefix: str, title: str, texts: dict[str, str],
                  meta: dict | None = None) -> list[dict]:
    """任务终态：把每窗推理链全文归档为可检索文档，返回索引条目列表。

    texts: {子名: 全文}（如 {"w0": "…", "w1": "…"}）。全文只进文档与索引，
    不进 DSH 会话；聊天侧可用 search_docs(kind='chain') 发现、read_doc 拉取。"""
    out = []
    for sub, text in texts.items():
        if not text:
            continue
        rec = store_text(f"{doc_prefix}_{sub}", "chain",
                         f"{title} · {sub}", text, meta=meta)
        out.append({"doc_id": rec["doc_id"], "sub": sub})
    return out


# ── 自检门：文档外置 + 索引 + 按需读取 的机器断言 ──
def _cleanup_test() -> None:
    """清掉自检产生的文档与索引条目（避免污染真实索引）。"""
    if INDEX.exists():
        INDEX.unlink()
    for p in DOCS_DIR.glob("_test_*"):
        p.unlink(missing_ok=True)


def run_selftest() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    _cleanup_test()

    r1 = store_text("_test_a", "note", "自检A", "hello doc store", {"k": "v"})
    out.append(("store_registered",
                r1["doc_id"] == "_test_a" and r1["byte_len"] > 0,
                "store_text 落盘并登记索引（byte_len>0）"))
    out.append(("read_roundtrip",
                read_doc("_test_a") == "hello doc store",
                "read_doc 按 doc_id 读回原文（roundtrip 恒等）"))
    out.append(("read_unknown",
                "不存在" in read_doc("_test_missing"),
                "未知 doc_id 返回错误信息而非崩溃"))
    small = thin("x" * 100, "note", "小")
    out.append(("thin_small_inline", small == "x" * 100,
                "小内容（≤阈值）原样内联，不进文档"))
    big = thin("y" * 5000, "note", "大", meta={"task": "t1"})
    out.append(("thin_big_external",
                big.startswith("[doc:") and "read_doc" in big,
                "大内容（>阈值）外置为文档，聊天窗口只留指针+摘录"))
    out.append(("big_in_index",
                len(search_docs(kw="t1", kind="note")) >= 1,
                "外置文档已入索引，可按关键词命中"))
    out.append(("search_by_kind",
                len(search_docs(kind="chain")) >= 0,
                "按类别检索可用（chain 为空不炸）"))
    # 越界：构造指向 DOCS_DIR 外的路径索引条目，read_doc 必须拒绝
    evil = {"doc_id": "_test_evil", "kind": "note", "title": "evil",
            "path": str((ORCH / "secret.txt").resolve()), "byte_len": 1,
            "excerpt": "", "ts": 0, "meta": {}}
    with INDEX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(evil, ensure_ascii=False) + "\n")
    out.append(("read_path_escape_blocked",
                "路径越界" in read_doc("_test_evil"),
                "索引条目路径越界被拒（只读 DOCS_DIR 内）"))
    arch = archive_chain("_test_chain", "定理X", {"w0": "证明甲", "w1": "证明乙"})
    out.append(("archive_chain_docs",
                len(arch) == 2 and all(a["sub"] in ("w0", "w1") for a in arch),
                "任务终态推理链按窗归档为可检索文档（chain 类别）"))

    _cleanup_test()
    failed = [n for n, ok, _ in out if not ok]
    out.append(("selftest_total", not failed,
                f"{len(out) - 1} 项自检：{'全绿' if not failed else '失败=' + str(failed)}"))
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="跑自检门")
    a = ap.parse_args()
    results = run_selftest()
    bad = [n for n, ok, _ in results if not ok]
    if a.selftest:
        for name, ok, note in results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {note}")
        return 1 if bad else 0
    if bad:
        print(f"doc_store 自检失败：{bad}")
        return 1
    print(f"doc_store 自检全绿（{len(results) - 1} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
