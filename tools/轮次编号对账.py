# -*- coding: utf-8 -*-
"""轮次编号对账（判据 #55）—— **一个新编号只能有一个含义，且必须登记**。

## 为什么需要它（真读数，2026-11-20 17:20，本轮自己踩的）

本仓用 `R<三位数>` 当**轮次标识**，全仓 416 个编号、上百处代码注释与文档靠它回查
「当时为什么这么改」。本轮我连写 10 个新编号 `R547…R556`，收尾一查才发现**全被用过了**：

| 我这次的意思 | 预存的意思（2026-11-18 那一轮） |
|---|---|
| R547 `_交付物落在主树` | R547 状态根真可写吗（`doctor.py`） |
| R548 `{repo}` 展开成主树根 | R548 verifier 抓出的真根因（`taixu_mcp.py`） |
| R549 增量裁决必须在主树 | R549 收口到唯一取法（`blind_spot.py`/`memory8.py`…） |
| R550 元判据自指死锁 | R550 收口到 `profile.state_dir()`（`agentface.py`） |
| R551 并发跑抢产物 | R551 所有协程必须惰性建（`taixu_mcp.py`） |
| R552 无清单文件的语言兜底 | R552 门加成功标记 `ORCH-OK-42`（`selfcheck.py`） |
| R553 配置与代码分离 | R553 semsearch 旧二进制 |
| R554 安装脚本被 SyntaxWarning 打断 | R554（已存在） |
| R555 未验必须带归因 | R555 元层目录按规则排除（`mem_anchor.py`） |
| R556 `dist/wheels` 是暂存区 | R556 删不掉必须说出来（`workspace.py`） |

⇒ 查 `R552` 会得到**两个完全不同的答案**。这是本项目最贵的一类错：
  **标识符重复不报错、不崩溃，只是让回查失效** —— 而回查是这个仓唯一的经验载体。
  本轮真正的编号从 **R572** 起（R571 是此前最大号）。

## 判据（四条，全机器可判）

① **新编号必须登记**：≥ `免登记上限+1` 的每个 `R<三位数>` 都必须在包根 `轮次编号.json` 的 `登记` 里有条目。
   （历史编号由 `免登记上限` 一次性祖父条款豁免 —— 416 个老编号不重写，但**新的一律登记**。）
② **别名唯一**：同一个编号不得被**两个登记项**同时列为 `别名` ⇒ 否则就是一号两义。
③ **已知冲突必须带可数的到期条件**，且**到期后必须清理**：
   · 没写 `到期条件` ⇒ 红（R298：「缺条件」这个结论必须有到期条件）；
   · 到期条件已满足（= 旧标记文件里已无该编号）而登记还在 ⇒ **也红** ——
     否则这条登记会变成永久的"已知问题"挡箭牌，挡住新出现的真冲突。
④ **格式**：编号必须 `R` + 三位数字；主题非空。

## 边界（如实）

· 它判的是**编号层面的唯一性**，判不了"主题描述得对不对"（那要人看）；
· 它读**文本里出现的编号**，不是语义 —— 一个编号写在注释里说别的事，它看不出来；
· `到期条件` 用**可数规则**表达（`旧标记文件` 里还有没有该编号），不是自然语言承诺。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

包根 = pathlib.Path(__file__).resolve().parents[1]
# ★ R584：登记表是**判据的常驻输入**，必须随仓走 ⇒ 放包根，**不许**放 work/（那目录被忽略）
登记表路径 = 包根 / "轮次编号.json"
扫的目录 = ("src", "tools", "tests")
扫的文档 = ("*.md", "work/*.md")
编号正则 = re.compile(r"(?<![A-Za-z0-9_])R(\d{3})b?(?![0-9])")


def _扫到的编号() -> dict[str, list[tuple[str, int, str]]]:
    """{编号: [(相对路径, 行号, 该行原文)]}"""
    出: dict[str, list[tuple[str, int, str]]] = {}
    文件: list[pathlib.Path] = []
    for d in 扫的目录:
        p = 包根 / d
        if p.is_dir():
            文件 += list(p.rglob("*.py"))
    for g in 扫的文档:
        文件 += [x for x in 包根.glob(g) if x.is_file()]
    for f in 文件:
        if ".venv" in f.parts or "__pycache__" in f.parts:
            continue
        try:
            文 = f.read_text(encoding="utf-8")
        except Exception:                                       # noqa: BLE001
            continue
        for i, 行 in enumerate(文.splitlines(), 1):
            for m in 编号正则.finditer(行):
                出.setdefault("R" + m.group(1), []).append(
                    (str(f.relative_to(包根)).replace("\\", "/"), i, 行.strip()[:90]))
    return 出


def main() -> int:
    try:
        if callable(getattr(sys.stdout, "reconfigure", None)):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

    print("═══ 轮次编号对账（判据 #55）═══")
    if not 登记表路径.is_file():
        print(f"✗ 登记表不在：{登记表路径}")
        print("  修法：建 work/轮次编号.json（格式见 tools/轮次编号对账.py 文件头）")
        return 2
    try:
        登记 = json.loads(登记表路径.read_text(encoding="utf-8"))
    except Exception as e:                                      # noqa: BLE001
        print(f"✗ 登记表读不出来：{type(e).__name__}: {e}")
        return 2

    项们 = 登记.get("登记") or []
    已知冲突 = 登记.get("已知冲突") or []
    免登记上限 = str(登记.get("免登记上限") or "R571")
    if not re.fullmatch(r"R\d{3}", 免登记上限):
        print(f"✗ `免登记上限` 不合法：{免登记上限!r}")
        return 2
    上限号 = int(免登记上限[1:])

    失败: list[str] = []

    # ── ④ 登记项自身合法 + ① 编号唯一 ──────────────────────
    print(f"\n① 登记表自身（{len(项们)} 项 · 已知冲突 {len(已知冲突)} 条 · 免登记上限 {免登记上限}）")
    主题集: dict[str, str] = {}
    for 项 in 项们:
        编 = str(项.get("编号") or "")
        if not re.fullmatch(r"R\d{3}", 编):
            失败.append(f"登记项编号不合法：{编!r}")
            continue
        if int(编[1:]) <= 上限号:
            失败.append(f"{编} ≤ 免登记上限 {免登记上限} ⇒ 新登记不该占用老号段")
        题 = str(项.get("主题") or "").strip()
        if not 题:
            失败.append(f"{编}：主题为空")
        if 编 in 主题集:
            失败.append(f"{编}：**重复登记**（已有「{主题集[编]}」，又来「{题}」）")
        主题集[编] = 题
        for 别 in (项.get("别名") or []):
            if not re.fullmatch(r"R\d{3}", str(别)):
                失败.append(f"{编}：别名不合法 {别!r}")
    print(f"   · 合法新编号 {len(主题集)} 个（{', '.join(sorted(主题集)) or '无'}）")

    # ── ② 别名唯一 ────────────────────────────────────────
    别名归属: dict[str, list[str]] = {}
    for 项 in 项们:
        for 别 in (项.get("别名") or []):
            别名归属.setdefault(str(别), []).append(str(项.get("编号")))
    冲突 = {k: sorted(set(v)) for k, v in 别名归属.items() if len(set(v)) > 1}
    if 冲突:
        print(f"\n② 别名唯一：**{len(冲突)} 个编号被两处认领**")
        for k, v in sorted(冲突.items()):
            失败.append(f"{k} 被 {'、'.join(v)} 同时列为别名 ⇒ 一号两义")
            print(f"   ✗ {k} ← {'、'.join(v)}")
    else:
        print(f"\n② 别名唯一：✓ {len(别名归属)} 个别名没有冲突")

    # ── ③ 已知冲突：带可数到期条件，到期必须清理 ────────────
    扫到 = _扫到的编号()
    if 已知冲突:
        print(f"\n③ 已知冲突（{len(已知冲突)} 条，逐条核到期条件）")
        for 条 in 已知冲突:
            编 = str(条.get("编号") or "")
            旧标记文件 = list(条.get("旧标记文件") or [])
            余 = len([x for x in 扫到.get(编, []) if x[0] in 旧标记文件])
            if not str(条.get("到期条件") or "").strip():
                失败.append(f"{编}：已知冲突**没写到期条件**（R298：缺条件的结论必须有到期条件）")
                print(f"   ✗ {编}：没写到期条件")
                continue
            if 余 == 0:
                失败.append(f"{编}：已知冲突**到期条件已满足**（旧标记 0 处）⇒ 必须删掉这条登记，"
                            f"否则它会挡住新出现的真冲突")
                print(f"   ✗ {编}：到期条件已满足 ⇒ **该删这条登记**")
            else:
                print(f"   · {编}：旧标记仍存 {余} 处 ⇒ 未到期（{条['到期条件']}）")
    else:
        print("\n③ 已知冲突：无")

    # ── ① 覆盖：新号段的每个编号都必须登记 ──────────────────
    新号段 = {n: v for n, v in 扫到.items() if int(n[1:]) > 上限号}
    未登记 = sorted(set(新号段) - set(主题集))
    print(f"\n④ 覆盖：新号段（> {免登记上限}）出现 {len(新号段)} 个编号")
    if 未登记:
        print(f"   ✗ 未登记 {len(未登记)} 个：{', '.join(未登记)}")
        for n in 未登记[:6]:
            a = 新号段[n][0]
            print(f"      {n}：{a[0]}:{a[1]} | {a[2]}")
            失败.append(f"{n}：未登记（首次出现在 {a[0]}:{a[1]}）")
    else:
        print("   ✓ 全部已登记")

    # ── 收口 ──────────────────────────────────────────────
    print()
    if 失败:
        print(f"判定=不达标（{len(失败)} 条）")
        for x in 失败[:12]:
            print(f"  ✗ {x}")
        return 1
    print(f"判定=OK；新编号 {len(主题集)} 个 · 别名冲突 0 · 未登记 0 · "
          f"已知冲突 {len(已知冲突)} 条（到期条件均未满足）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
