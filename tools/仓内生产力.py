# -*- coding: utf-8 -*-
"""仓内生产力指标（判据 #52）—— N3 的可机判替代，**明确不答"比外部强"**。

## 为什么必须做这个（N3 的边界）
N3「开放任务通用生产力」= 产出/投入，需要一个**分母**（人类基线或外部基准的真值）。
本机没有 docker、没有 SWE-bench 类数据集、没有人类工时记录 ⇒ 那条**永远 UNVERIFIABLE**。
但「不可判」不等于「不记账」。所以把它降成**仓内可机判、可复跑的趋势指标**：

| 指标 | 来源（可机读，不是我说） |
|---|---|
| 判据规模 | `work/交付验收_最近.json` 的条数（最近一趟全量）|
| 最近一趟结果 | 同上：PASS/FAIL/未验 + 总墙钟 + 平均每条 + 最慢 3 条 |
| 门区分力 | `work/门区分力.json`：抓住/注入成功 + 门具数 |
| 人工交互点 | 扫 `src/**/*.py` + `tools/*.py` 里的 `input(` / `getpass` / `sys.stdin` ⇒ 应为 **0**（无人值守是设计，不是口号）|
| 真事故发现数 | `work/进度回执.md` 里「真事故」「真洞」标记的条数（**弱证据**：文本计数，附行号）|

## ★ 它回答什么、不回答什么
· 回答：**交付机器有没有退化** —— 判据数、绿率、墙钟、区分力、交互点，每次全量跑完重跑一次，趋势可比；
· **不**回答：与人类/外部智能体孰优（N3 仍 UNVERIFIABLE，不许拿这份指标冒充生产力结论）。

## 三态
· 数据源齐 + 交互点 0 ⇒ `判定=OK`；
· 缺 `交付验收_最近.json`（还没跑过全量）⇒ `判定=未验`，退出 3；
· 出现交互点（`input(`）⇒ `判定=回归`，退出 1 —— 无人值守被破坏是硬伤。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

包根 = pathlib.Path(__file__).resolve().parents[1]
最近 = 包根 / "work" / "交付验收_最近.json"
区分 = 包根 / "work" / "门区分力.json"
回执 = 包根 / "work" / "进度回执.md"
输出 = 包根 / "work" / "仓内生产力.json"


def _扫交互点() -> tuple[int, list[str], int]:
    """返回 (交互点数, 命中行, 顺带看到的 stdin 引用数)。

    无人值守的定义：**没有任何"等人打字"的点** ⇒ 只算 `input(...)` / `getpass(...)` **调用**。
    ★ 三个误判陷阱（2026-11-19 全踩过）：
      ① 按行文本搜 ⇒ 把本工具 docstring 里的 `input(` 字样算成交互点（散文不是代码）；
      ② 按 token 搜 `stdin` ⇒ `subprocess.run(..., stdin=DEVNULL)` 被算成交互点 ——
         那恰恰是**防交互**（不继承 stdin，子进程不会卡在等人输入）；
      ③ `sys.stdin.read()` 是**管道**用法（喂数据），不是交互提示。
    ⇒ 用 AST 只认 Call：func 是 Name `input`/`getpass`。
    """
    import ast

    命中: list[str] = []
    stdin引 = 0
    文件们 = list((包根 / "src" / "agentscope_orchestrator").glob("*.py")) + \
        [p for p in (包根 / "tools").glob("*.py")]
    for p in 文件们:
        文 = p.read_text(encoding="utf-8", errors="replace")
        try:
            树 = ast.parse(文)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(树):
            if isinstance(node, ast.Attribute) and node.attr == "stdin":
                stdin引 += 1
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name) and f.id in ("input", "getpass"):
                命中.append(f"{p.name}:{node.lineno}: {f.id}(...)")
    return len(命中), 命中, stdin引


def _真事故() -> list[str]:
    if not 回执.is_file():
        return []
    命中: list[str] = []
    for i, 行 in enumerate(回执.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if ("真事故" in 行) or ("真洞" in 行):
            命中.append(f"回执:{i}")
    return 命中


def main() -> int:
    if not 最近.is_file():
        print("   ③ 未验：还没有一次**全量**交付验收读数（先跑 python tools/交付验收.py）")
        print("判定=未验")
        return 3
    读 = json.loads(最近.read_text(encoding="utf-8"))
    条数 = int(读.get("条数") or 0)
    过 = int(读.get("PASS") or 0)
    败 = int(读.get("FAIL") or 0)
    未 = int(读.get("未验") or 0)
    墙钟 = float(读.get("总墙钟秒") or 0)
    平均 = (墙钟 / 条数) if 条数 else 0.0
    明细 = sorted([x for x in (读.get("明细") or []) if x.get("用时")],
                  key=lambda x: float(x["用时"]), reverse=True)[:3]

    区 = {}
    if 区分.is_file():
        区 = json.loads(区分.read_text(encoding="utf-8"))

    交互数, 交互行, stdin引 = _扫交互点()
    事故 = _真事故()

    print(f"   ① 判据规模 = {条数} 条（最近一趟 {读.get('时间')}）")
    print(f"   ② 最近一趟 = PASS {过} · FAIL {败} · 未验 {未} · 总墙钟 {墙钟:.0f}s · 平均 {平均:.1f}s/条")
    for x in 明细:
        print(f"      · 最慢：{x['id']} {x['名'][:26]} = {float(x['用时']):.0f}s")
    if 区:
        print(f"   ③ 门区分力 = 抓住 {区.get('抓住')}/{区.get('注入成功')}"
              f"（门具 {len(区.get('门具') or [])} 个 · 无从注入 {区.get('无从注入')}）")
    else:
        print("   ③ 门区分力 = 未验（还没有 work/门区分力.json）")
    print(f"   ④ 人工交互点 = {交互数} 处（无人值守的定义：没有任何「等人打字」的点）")
    print(f"      另：stdin 引用 {stdin引} 处（管道 / DEVNULL 等，**不算**交互点 —— 见函数注释）")
    for x in 交互行[:5]:
        print(f"      ✗ {x}")
    print(f"   ⑤ 台账里的真事故/真洞标记 = {len(事故)} 处（弱证据：文本计数）{事故[:6]}")

    if 交互数:
        print("   判定=回归（出现 stdin 交互点 ⇒ 无人值守被破坏）")
        return 1
    if not 区:
        print("   判定=未验（缺门区分力读数）")
        return 3
    输出.parent.mkdir(parents=True, exist_ok=True)
    输出.write_text(json.dumps(dict(
        时间=读.get("时间"), 判据条数=条数, PASS=过, FAIL=败, 未验=未, 总墙钟秒=墙钟,
        平均每条秒=round(平均, 2), 门区分力=f"{区.get('抓住')}/{区.get('注入成功')}",
        门具数=len(区.get("门具") or []), 人工交互点=交互数, 真事故标记=len(事故),
        边界="无外部效度：不构成通用生产力结论（N3 仍 UNVERIFIABLE）；只作交付趋势指标",
    ), ensure_ascii=False, indent=2), encoding="utf-8")
    print("   判定=OK")
    print("   边界=无外部效度：以上是**仓内趋势指标**，不构成「比人类/外部智能体更强」的结论"
          "（N3 仍 UNVERIFIABLE）")
    return 0


if __name__ == "__main__":
    sys.exit(main())