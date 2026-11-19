# -*- coding: utf-8 -*-
"""跨机续跑探针（#35）—— 状态 bundle 的**核对**是否真有牙齿。

判据（N1 / #20）：
  ① 导出→导入后，B 里每个文件的 **sha256 与 A 逐字节一致**，且 jobs 行数一致；
  ② 导入后 `jobs/续跑来源.json` 存在且 `zip_sha256` 与实际 zip 一致
     （日后能区分「本机跑出来的」与「搬过来的」）；
  ③ **二次导入不许覆盖**：目标已存在 ⇒ 全部跳过、原文件 sha256 不变；
  ④ **篡改过的包必须整包拒收**：把包内 jobs.db 改一个字节 ⇒ 导入必须 `拒绝` 非空，
     且**一个字节都不落盘**（不认"部分导入"）。
     ★ 这条是整套东西的牙齿 —— 没有它，"核对"只是打印了一行 ok。

退出码：0 = 全过；1 = 有不过。末行固定 `判定=OK` / `判定=回归`。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator import state_bundle as SB  # noqa: E402
from agentscope_orchestrator.migrate_state import _库行数, _sha  # noqa: E402

失败: list[str] = []


def 判(条件: bool, 说明: str) -> None:
    print(("   ok  " if 条件 else "   ✗   ") + 说明)
    if not 条件:
        失败.append(说明)


def _造状态(根: pathlib.Path) -> None:
    (根 / "jobs").mkdir(parents=True, exist_ok=True)
    (根 / "products").mkdir(parents=True, exist_ok=True)
    (根 / "work").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(根 / "jobs" / "jobs.db"))
    try:
        conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, json TEXT)")
        conn.executemany("INSERT INTO jobs VALUES (?,?)",
                         [("j1", '{"state":"done"}'), ("j2", '{"state":"done"}')])
        conn.commit()
    finally:
        conn.close()
    (根 / "jobs" / "成本账.jsonl").write_text(
        '{"任务":"j1","token":123}\n{"任务":"j2","token":456}\n', encoding="utf-8")
    (根 / "products" / "产物.txt").write_text("这是 A 机跑出来的产物\n", encoding="utf-8")
    (根 / "work" / "笔记.txt").write_text("工作目录里的东西\n", encoding="utf-8")


def main() -> int:
    基 = pathlib.Path(tempfile.mkdtemp(prefix="跨机续跑-"))
    A, B, C = 基 / "A", 基 / "B", 基 / "C"
    _造状态(A)
    B.mkdir()
    C.mkdir()
    zip_A = 基 / "状态A.zip"

    r = SB.导出(zip_A, A)
    期望 = r["文件数"]
    判(期望 >= 4, f"导出成功（文件数={期望} · 总字节={r['总字节']}）")
    # ★ 期望值**从导出结果取**，不硬编码：清单由 `migrate_state.要搬` 声明，
    #   这里再写一个数字就是「同一事实两处写」（第一版就因此假红了一次）。

    r2 = SB.导入(zip_A, B)
    判(not r2["拒绝"], f"正常包导入未被拒（拒绝={r2['拒绝']}）")
    判(not r2["失败"], f"无落盘失败（失败={r2['失败']}）")
    同 = 0
    for x in r2["写入"]:
        if (B / x["相对"]).is_file() and _sha(B / x["相对"]) == x["sha256"]:
            同 += 1
    判(len(r2["写入"]) == 期望 and 同 == 期望,
       f"B 与 A **逐字节一致**（写入 {len(r2['写入'])}/{期望} · sha256 相同 {同}/{期望}）")
    判(_库行数(A / "jobs" / "jobs.db") == _库行数(B / "jobs" / "jobs.db") == 2,
       f"jobs 行数一致（A={_库行数(A / 'jobs' / 'jobs.db')} · B={_库行数(B / 'jobs' / 'jobs.db')}）")

    来 = B / "jobs" / "续跑来源.json"
    判(来.is_file(), "写了 jobs/续跑来源.json")
    if 来.is_file():
        标 = json.loads(来.read_text(encoding="utf-8"))
        判(标.get("zip_sha256") == r["sha256"],
           f"续跑来源记的是**这个** zip 的 sha256（{str(标.get('zip_sha256'))[:12]}…）")
        判(标.get("来源") == "导入", f"来源标记 = {标.get('来源')!r}")

    # ③ 二次导入不许覆盖
    旧 = _sha(B / "jobs" / "jobs.db")
    r3 = SB.导入(zip_A, B)
    判(len(r3["跳过"]) == 期望 and not r3["写入"],
       f"二次导入**全部跳过**（跳过 {len(r3['跳过'])}/{期望} · 写入 {len(r3['写入'])}）")
    判(_sha(B / "jobs" / "jobs.db") == 旧, "二次导入后 jobs.db **没被改动**")

    # ④ 篡改包必须整包拒收
    坏 = 基 / "坏包.zip"
    with zipfile.ZipFile(zip_A) as zin, zipfile.ZipFile(坏, "w", zipfile.ZIP_DEFLATED) as zout:
        for 名 in zin.namelist():
            数据 = zin.read(名)
            if 名.endswith("成本账.jsonl"):
                数据 = 数据 + b'{"tampered":true}\n'      # 改一个字节级的内容
            zout.writestr(名, 数据)
    r4 = SB.导入(坏, C)
    判(bool(r4["拒绝"]), f"篡改包**被拒收**（拒绝={r4['拒绝']}）")
    落 = [p for p in C.rglob("*") if p.is_file() and p.name != "续跑来源.json"]
    判(len(落) == 0, f"拒收后**一个字节都没落盘**（C 里文件数={len(落)}）")

    shutil.rmtree(基, ignore_errors=True)
    print(f"判定={'OK' if not 失败 else '回归'}")
    if 失败:
        print("   ⇒ 不过的项：" + "；".join(失败))
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(main())
