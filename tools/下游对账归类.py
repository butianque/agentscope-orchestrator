# -*- coding: utf-8 -*-
"""下游对账归类（P5 · 判据 #28）—— **先修尺子，再量**。

★ P5 的原话：「先修尺子再量」（有先例：`判九十四` 曾报"我漏 465 我多 467"，
  最后是"我多 0 我漏 0"）。所以这个工具**不信任何单个数**，只认三件事：
    ① **守恒**：`文件数 == 一致 + 不一致 + 两边都空 + 语法错`
       （原报表 2276 vs 2200+0+8 = 2208 ⇒ **68 个文件不在任何计数里** —— 那就是尺子漏了一桶）
    ② **归类 100%**：每个"不一致"条目都带 `类`（口径 R525：跳过项必须可定位）
    ③ **真差 = 0**：去掉"口径差/尺子错/源文件错"之后，真正对不上的必须是 0

★ 边界（如实）：本工具**只判报表**，不重新跑对账（跑一次 ≈ 数分钟）。
  新鲜度由调用方负责：`全仓ast对账.py` 先跑，本工具读它的落盘产物。
  报表比源码旧 ⇒ 记**未验**，不记绿。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
包根 = pathlib.Path(__file__).resolve().parents[1]
报表 = 包根 / "work" / "图" / "ast对账.json"


def main() -> int:
    if not 报表.is_file():
        print(f"⚠ 报表不存在：{报表}")
        print("判定=未验（先跑 `python tools\\全仓ast对账.py`）")
        return 2
    果 = json.loads(报表.read_text(encoding="utf-8"))
    if "守恒" not in 果:
        print("⚠ 报表是**旧版**（没有守恒字段）⇒ 尺子还没修，读数不可用")
        print("判定=未验（先跑修好的 `全仓ast对账.py` 重出一份报表）")
        return 2

    文件数 = int(果.get("文件数") or 0)
    一致 = int(果.get("一致") or 0)
    不一致 = int(果.get("不一致") or 0)
    两边都空 = int(果.get("两边都空") or 0)
    语法错 = len(果.get("ast语法错") or [])
    合计 = int(果.get("合计") or 0)
    守恒 = bool(果.get("守恒")) and 合计 == 文件数

    明细 = 果.get("不一致明细") or []
    归类 = collections.Counter()
    无类 = []
    真差 = []
    for x in 明细:
        类 = str(x.get("类") or "").strip()
        if not 类:
            无类.append(x.get("文件"))
            continue
        归类[类] += 1
        if 类.startswith("真差"):
            真差.append(x)

    print(f"文件数={文件数} · 一致={一致} · 不一致={不一致} · 两边都空={两边都空} · 语法错={语法错}")
    print(f"  守恒：{一致}+{不一致}+{两边都空}+{语法错} = {一致 + 不一致 + 两边都空 + 语法错} "
          f"vs 文件数 {文件数} ⇒ {'平' if 守恒 else '**不平**'}")
    print(f"  归类覆盖：{len(明细) - len(无类)}/{len(明细)} 条有类"
          + ("（不一致为 0 ⇒ **覆盖不适用**，别把 0/0 读成 100%）" if not 明细 else "")
          + ("" if not 无类 else f"（**无类 {len(无类)}**：{无类[:3]}）"))
    for 类, n in 归类.most_common():
        print(f"    {n:4d}  {类}")
    print(f"  真差 = {len(真差)}" + ("" if not 真差 else f"（前 3：{[x.get('文件') for x in 真差[:3]]}）"))
    # ★ 明细被截断时不许当"全都看过了"：报表里的 不一致 数必须与明细条数一致
    完整 = (len(明细) == 不一致)
    if not 完整:
        print(f"  ⚠ 明细只有 {len(明细)} 条、而报表说 {不一致} 条 ⇒ **不完整**，归类覆盖算不出来")

    # ── 抽样复核「两边都空」这一桶：**用 ast 独立验**它真的没有导入 ──
    空文件们 = 果.get("两边都空文件") or []
    违约 = []
    if 空文件们:
        import random as _r
        样 = _r.Random(20261118).sample(空文件们, min(10, len(空文件们)))
        for 相对 in 样:
            p = 包根.parents[1] / 相对
            try:
                源 = p.read_text(encoding="utf-8", errors="replace")
                树 = ast.parse(源)
            except (OSError, SyntaxError):
                违约.append((相对, "读/解析不了"))
                continue
            有导 = any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(树))
            if 有导:
                违约.append((相对, "**其实有导入**"))
        print(f"  抽样复核「两边都空」：{len(样) - len(违约)}/{len(样)} 确实没有导入")
        for x in 违约[:5]:
            print(f"    ✗ {x[0]} — {x[1]}")
    else:
        print("  ⚠ 「两边都空」没有清单 ⇒ 这一桶**不可复核**（计数不带清单就是不可核）")

    ok = (守恒 and 完整 and not 无类 and not 真差 and 文件数 > 0
          and not 违约 and bool(空文件们))
    print(f"判定={'OK' if ok else '不达标'}")
    if not 守恒:
        print("   ⇒ 守恒不平：报表有一桶没交代（尺子的事，不是仓的事）")
    if not 完整:
        print("   ⇒ 明细被截断：归类覆盖率不可算（不许拿部分当全部）")
    if 无类:
        print("   ⇒ 有条目没有类：归类覆盖率不是 100%")
    if 真差:
        print("   ⇒ 有真差：适配器的绑定名确实对不上（这才是我的 bug）")
    if not 文件数:
        print("   ⇒ 文件数 0：报表其实是空跑（口径 375：别拿两个空集比）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
