# -*- coding: utf-8 -*-
"""悬空锚定归零（P2 · 判据 #25/#26）。

★ 背景（原账）：「悬空 91」= 代码里点名的 `.lean` 全仓都找不到。但**这个数不能直接当缺陷数**：
  正则匹配的是**任何** `*.lean` 子串 ⇒ 注释、文档、格式示例里的文件名也算一条边。
  实测样例里就有 `MyMeta.MetaTheory.X.lean`、`A/B/C.lean` 这种**明显的占位符**。
  ⇒ 判据要的是**分类后仍无线索的那些**（真缺），且每条必须能**定位**（rs 文件 + 行号）。

判据：
  #25 ① **守恒**：归类总数 == 悬空条数（不许丢条目）；
      ② 每条都有归类名；
      ③ **真缺 = 0** 或每条都在 `work\\悬空锚定登记.json` 里显式登记（带 rs + 行 + 原因）。
  #26 抽 40 条 A 级真锚定边，逐条验「文件真的存在 ∧ 点名发生在**活代码**（不在注释里）」，
      准确率 **≥ 95%**。★ 这是**可机算的下界**：模板/注释边会被抓住，误连则抓不到（如实写）。

退出码：0 = 三条都过；1 = 不过。末行固定 `判定=OK` / `判定=不达标`。
"""
from __future__ import annotations

import json
import pathlib
import random
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
包根 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(包根 / "src"))

登记文件 = 包根 / "work" / "悬空锚定登记.json"
输出文件 = 包根 / "work" / "悬空锚定归类.json"

# 占位符/模板的特征（都是**可判**的字符串形态，不是"看着像"）
占位模式 = re.compile(
    r"(^[A-Z]\.lean$)"                       # X.lean / A.lean
    r"|(^\.\.\.)|(<[^>]*>)|(\{)|(\*)"        # …/…、<name>、{x}、通配
    r"|(^[A-Z](/[A-Z])+\.lean$)"             # A/B/C.lean
    r"|(^X\.)|(\.X\.)|(^foo\.)|(^bar\.)|(^example)|(^示例)"
    r"|(^某某)|(^某\.lean$)|(^name\.lean$)|(^test\.lean$)"
    r"|(^A\.B\.C\.lean$)|(^MyMeta\.MetaTheory\.X\.lean$)",
    re.I,
)


def _全仓lean名(根: pathlib.Path) -> dict[str, list[str]]:
    """basename(小写) → 全仓相对路径们。只用于**存在性**判断（不剪枝，只排 .git）。"""
    表: dict[str, list[str]] = {}
    for dp, dn, fn in __import__("os").walk(根):
        dn[:] = [d for d in dn if d != ".git"]
        for f in fn:
            if f.lower().endswith(".lean"):
                p = pathlib.Path(dp) / f
                try:
                    相对 = p.relative_to(根).as_posix()
                except ValueError:
                    continue
                表.setdefault(f.lower(), []).append(相对)
    return 表


def _行们(rs文件: pathlib.Path, 名: str) -> list[tuple[int, bool]]:
    """返回 [(行号, 是否在注释里)]。注释判定：`//` 出现在命中位置之前。"""
    出: list[tuple[int, bool]] = []
    try:
        文 = rs文件.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 出
    for i, 行 in enumerate(文.splitlines(), 1):
        j = 行.find(名)
        if j < 0:
            continue
        注 = 行.find("//")
        出.append((i, 0 <= 注 < j))
    return 出


def main() -> int:
    from agentscope_orchestrator import mem_anchor as _ma
    from agentscope_orchestrator import mem_live as _ml
    from agentscope_orchestrator import profile as _pf

    档 = _pf.get_profile()
    根 = pathlib.Path(getattr(档, "repo_root", None) or 包根).resolve()
    _ml.确保载入()
    表 = _ml.现存()
    lean名 = _全仓lean名(根)

    果 = _ma.扫锚定(根, ["src"], ["形式化验证"], 跳过={".git", ".venv", "target", "node_modules"},
                   样例=10 ** 6, 表=表, 全仓lean名=set(lean名))
    悬 = 果.get("悬空锚定（点了一个不存在的.lean）", {}).get("样例") or []
    悬挂条数 = int(果.get("悬空锚定（点了一个不存在的.lean）", {}).get("条数") or 0)

    归类: dict[str, list[dict]] = {}
    for x in 悬:
        写到 = str(x.get("写到") or "").replace("\\", "/")
        名 = 写到.split("/")[-1]
        rs = str(x.get("rs") or "")
        行们 = _行们(根 / rs, 名) if rs else []
        活着 = [(n, c) for n, c in 行们 if not c]
        if 占位模式.search(名):
            类 = "占位符/模板（示例字符串，不是真引用）"
        elif 名.lower() in lean名 and 名 not in lean名:
            类 = "大小写不同（仓里有同名文件）"
        elif not 行们:
            类 = "rs 里找不到该名（计数与扫描不一致）"
        elif not 活着:
            类 = "只在注释/文档里提到（不是活代码点名）"
        elif 名.lower() in lean名:
            类 = "路径基准错（全仓有该名，点名的相对路径不对）"
        else:
            类 = "★真缺（活代码点名、全仓无此文件）"
        归类.setdefault(类, []).append({"rs": rs, "写到": 写到, "行": [n for n, _ in 行们][:5]})

    登记 = {}
    if 登记文件.is_file():
        try:
            登记 = json.loads(登记文件.read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            登记 = {}
    已登记键 = {(x.get("rs"), x.get("写到")) for x in (登记.get("条目") or [])}
    真缺 = 归类.get("★真缺（活代码点名、全仓无此文件）", [])
    未登记 = [x for x in 真缺 if (x["rs"], x["写到"]) not in 已登记键]

    总数 = sum(len(v) for v in 归类.values())
    守恒 = (总数 == 悬挂条数)
    print(f"悬空锚定={悬挂条数} · 归类总数={总数} · 守恒={'平' if 守恒 else '不平'}")
    for k, v in sorted(归类.items(), key=lambda kv: -len(kv[1])):
        print(f"   {len(v):3d}  {k}")
    print(f"  ★真缺={len(真缺)} · 已登记={len(真缺) - len(未登记)} · **未登记={len(未登记)}**")
    for x in 未登记[:8]:
        print(f"     · {x['rs']} → {x['写到']}（行 {x['行']}）")

    # ── #26 A 级边的抽样准确率 ──
    真边 = (果.get("A级真锚定") or {}).get("样例") or []
    随 = random.Random(20261118)
    抽 = 随.sample(真边, min(40, len(真边))) if 真边 else []
    过 = 0
    坏: list[dict] = []
    for e in 抽:
        写到 = str(e.get("写到") or "").replace("\\", "/")
        名 = 写到.split("/")[-1]
        rs = str(e.get("rs") or "")
        # ① 文件存在（按相对路径或 basename 在全仓能定位）
        存在 = (根 / 写到).is_file() or bool(lean名.get(名.lower()))
        # ② 点名在活代码里（不在注释里）
        行们 = _行们(根 / rs, 名) if rs else []
        活 = any(not c for _, c in 行们)
        if 存在 and 活:
            过 += 1
        else:
            坏.append({"rs": rs, "写到": 写到, "存在": 存在, "活代码": 活})
    准 = (过 / len(抽)) if 抽 else 0.0
    print(f"A级边抽样={len(抽)} · 存在且活代码={过} · 准确率={准:.1%}（阈值 ≥95%）")
    for x in 坏[:6]:
        print(f"     ✗ {x}")

    输出文件.write_text(json.dumps(
        {"悬空条数": 悬挂条数, "归类": {k: v for k, v in 归类.items()},
         "真缺": 真缺, "未登记": 未登记,
         "A级抽样": {"抽": len(抽), "过": 过, "准确率": round(准, 4), "坏": 坏}},
        ensure_ascii=False, indent=2), encoding="utf-8")

    ok = 守恒 and not 未登记 and len(抽) >= 20 and 准 >= 0.95
    print(f"判定={'OK' if ok else '不达标'}")
    if not 守恒:
        print("   ⇒ 归类数 != 悬空数：有条目没被归类（静默丢）")
    if 未登记:
        print(f"   ⇒ {len(未登记)} 条真缺**没有登记**（要么修，要么写进 work\\悬空锚定登记.json）")
    if len(抽) < 20:
        print("   ⇒ A 级边抽样不足 20 条，判据不成立")
    if 准 < 0.95:
        print(f"   ⇒ A 级边准确率 {准:.1%} < 95%：边里混着注释/模板（正则过宽）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
