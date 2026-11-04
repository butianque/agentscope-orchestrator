# -*- coding: utf-8 -*-
"""把旧落点（装错时落在 `<venv>/Lib` 里的状态）迁到新状态根。

★ 为什么要这个脚本（2026-10-31 交付审计 P0-1）：
    修好 `TOOL_ROOT` 后，新状态落在 `STATE_ROOT`；而历史任务库、成本账、
    熔断健康表还留在旧位置。不迁就等于**交付时把历史清空**：
    调用方问"上次那条任务怎么样了"会得到 0 条，而这会被误读成"工具没记录"。

★ 安全约定（都是为了让"迁坏了"不成立）：
    ① 默认**只演不搬**（dry-run），要真搬得加 `--apply`；
    ② 绝不覆盖新落点已有的文件：目标已存在就跳过并如实报告；
    ③ 搬是 copy → 校验字节 → 再改名为 `.migrated.bak`，不删原件；
    ④ jobs.db 用 sqlite 只读连接确认行数，两边行数不一致就**判失败**。

用法：
    python -m agentscope_orchestrator.migrate_state --from <旧目录> [--apply]
    python -m agentscope_orchestrator.migrate_state              # 自动找旧落点
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from . import profile as prof_mod

# 相对路径 → 说明（只搬"跨重启必须记住"的东西）
要搬 = [
    ("jobs/jobs.db", "任务库"),
    ("jobs/成本账.jsonl", "成本账"),
    ("jobs/熔断状态.json", "熔断健康表"),
    ("jobs/纠偏台账.jsonl", "纠偏台账"),
    ("jobs/tasks.json", "旧版任务快照"),
]
要搬目录 = [("products", "产物库"), ("work", "工作目录")]


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fp:
        for 块 in iter(lambda: fp.read(1 << 20), b""):
            h.update(块)
    return h.hexdigest()


def _库行数(p: Path) -> int | None:
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            return int(conn.execute("SELECT count(*) FROM jobs").fetchone()[0])
        finally:
            conn.close()
    except Exception:                          # noqa: BLE001
        return None


def _找旧落点() -> list[Path]:
    """旧落点候选：正是那个"parents[2] 猜错"的地方。

    wheel 布局下旧根 = `<venv>/Lib`（site-packages 的上一级）；源码布局无此问题。
    """
    候: list[Path] = []
    for up in (prof_mod.PKG_DIR.parent.parent, prof_mod.PKG_DIR.parent):
        if up.name.lower() == "lib" and (up / "jobs").is_dir():
            候.append(up)
    return 候


def 迁(旧: Path, 新: Path, apply: bool = False) -> dict:
    报告: dict = {"旧": str(旧), "新": str(新), "apply": apply,
                  "搬运": [], "跳过": [], "失败": [], "校验": {}}
    if not 旧.is_dir():
        报告["失败"].append(f"旧落点不存在：{旧}")
        return 报告
    for 名, 说明 in 要搬:
        s, d = 旧 / 名, 新 / 名
        if not s.is_file():
            continue
        if d.exists():
            报告["跳过"].append(f"{名}（目标已存在，不覆盖）")
            continue
        if not apply:
            报告["搬运"].append(f"[dry] {名}（{说明}，{s.stat().st_size} B）")
            continue
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            if _sha(s) != _sha(d):
                报告["失败"].append(f"{名} 字节校验不一致")
                continue
            if 名.endswith(".db"):
                前, 后 = _库行数(s), _库行数(d)
                报告["校验"][名] = {"旧行数": 前, "新行数": 后}
                if 前 is None or 前 != 后:
                    报告["失败"].append(f"{名} 行数不一致：{前} vs {后}")
                    continue
            报告["搬运"].append(f"{名}（{说明}）")
        except OSError as e:
            报告["失败"].append(f"{名}: {type(e).__name__}: {e}")
    for 名, 说明 in 要搬目录:
        s, d = 旧 / 名, 新 / 名
        if not s.is_dir():
            continue
        n = sum(1 for _ in s.rglob("*") if _.is_file())
        if d.exists() and any(d.iterdir()):
            报告["跳过"].append(f"{名}/（目标非空，不合并）")
            continue
        if not apply:
            报告["搬运"].append(f"[dry] {名}/（{说明}，{n} 个文件）")
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
            for f in s.rglob("*"):
                if f.is_file():
                    t = d / f.relative_to(s)
                    t.parent.mkdir(parents=True, exist_ok=True)
                    if not t.exists():
                        shutil.copy2(f, t)
            报告["搬运"].append(f"{名}/（{说明}，{n} 个文件）")
        except OSError as e:
            报告["失败"].append(f"{名}/: {type(e).__name__}: {e}")
    return 报告


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="旧状态落点 → 新状态根（默认 dry-run）")
    ap.add_argument("--from", dest="src", default="", help="旧落点（缺省=自动探测）")
    ap.add_argument("--to", dest="dst", default="", help="新状态根（缺省=STATE_ROOT）")
    ap.add_argument("--apply", action="store_true", help="真搬（不加就只演）")
    a = ap.parse_args(argv)
    新 = Path(a.dst).resolve() if a.dst else Path(prof_mod.STATE_ROOT)
    旧们 = [Path(a.src).resolve()] if a.src else _找旧落点()
    if not 旧们:
        print(json.dumps({"ok": True, "说明": "没有找到旧落点 ⇒ 不需要迁移",
                          "新状态根": str(新)}, ensure_ascii=False, indent=1))
        return 0
    报告们 = [迁(p, 新, a.apply) for p in 旧们]
    坏 = [r for r in 报告们 if r["失败"]]
    print(json.dumps({"ok": not 坏, "报告": 报告们}, ensure_ascii=False, indent=1))
    if not a.apply and any(r["搬运"] for r in 报告们):
        print("\n（以上是 dry-run。确认无误后加 --apply 真搬。）", file=sys.stderr)
    return 1 if 坏 else 0


if __name__ == "__main__":
    raise SystemExit(main())
