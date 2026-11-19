"""R275：**按目标树清单无人值守跑完并汇总裁决**（不调模型，纯机器裁判）。

★ 为什么需要它（真读数，2026-11-05）：
  - `auto_goal` 已经能给出 `submissions`（可提交清单，带 tid/kind/gates/依赖）；
  - 但全仓**没有任何东西按它执行**：`develop.py` 是"从 audit/observation 抽 Gap 再派模型修"
    的修坏链，它把 `auto_goal.py` 当**自检目标**，完全不认识 topo/submissions；
  - R274 的真跑驱动是我**手写的一次性脚本**，跑完就删了 ——
    「一段驱动不了的接线等于没有接线」，同理 **一次性的驱动脚本也不等于产品**。

⇒ 本模块把那份脚本变成可复用的执行器：
  输入 = 计划 + 仓根；输出 = 逐段裁决 + 父目标是否达成 + 未达成时点名。
  它**不派模型**：目标树里每段该写什么由调用方（模型 / 人工）产出，
  本执行器只回答"这些段现在到底绿没绿、整体达成没达成"。

★ 诚实边界：本执行器只做**裁决**，不做**改码**。改码仍走 generic_goal 的
  逐文件/整批应用 + 判据门复跑那条链。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import gate_spec as _gs


def run_plan(plan: dict, 仓根: Path | str) -> dict:
    """按 `plan['submissions']` 的 topo 序逐段裁决，返回汇总。

    返回：
      {
        "ok": bool,                      # 父目标是否达成（全部会跑的段都绿 + 验收门全绿）
        "段": [{"tid","kind","状态","门","理由"}],
        "达成": bool,
        "未达成的原因": [str],
        "耗时秒": float,
        "诚实边界": str,
      }

    ★ 语义（对齐 R269/R271/R272/R274b）：
      · `will_run=False` 的汇总点**不裁决**（它没有自己的门），只做记账；
      · 会跑的段用**它自己的门**裁决，全绿才算这一段落地；
      · 验收段（acceptance）**另算**：它的门是全部达成判据的合取；
      · **任一段红 ⇒ 父目标未达成**，并点名是哪一段、哪条门、为什么。
    """
    起 = time.time()
    根 = Path(仓根)
    # ★★★★★ R275b（真读数抓的）：**执行器不许依赖"某一层好心加上的字段"**。
    #   真证据：`plan_tasks()` 本身**不产生** `submissions` —— 那是 MCP 工具
    #   `auto_goal` 在回执里补上的。执行器拿 `plan_tasks()` 的结果直接跑 ⇒
    #   「计划里没有 submissions ⇒ 无法执行」⇒ 三档测试全部 False。
    #   ⇒ 缺了就**自己从 tasks 翻**（同一个 plan_to_submissions，不许两把尺子）。
    from . import auto_goal as _ag
    subs = [s for s in (plan.get("submissions") or []) if isinstance(s, dict)]
    if not subs and isinstance(plan, dict) and plan.get("tasks"):
        subs = [s for s in _ag.plan_to_submissions(plan) if isinstance(s, dict)]
    if not subs:
        return {"ok": False, "段": [], "达成": False,
                "未达成的原因": ["计划里既没有 submissions 也没有 tasks ⇒ 无法执行"],
                "耗时秒": round(time.time() - 起, 3),
                "诚实边界": "没有清单就无从执行，不许当达成"}

    段: list[dict] = []
    原因: list[str] = []
    已落地: set[str] = set()
    for s in subs:
        tid = str(s.get("tid") or "")
        kind = str(s.get("kind") or "")
        deps = list(s.get("depends_on_plan") or [])
        缺 = [d for d in deps if d not in 已落地]
        if 缺:
            段.append({"tid": tid, "kind": kind, "状态": "跳过",
                       "门": {}, "理由": f"上游未落地：{缺}"})
            原因.append(f"{tid} 跳过：上游 {缺} 未落地")
            continue
        if not s.get("will_run"):
            段.append({"tid": tid, "kind": kind, "状态": "汇总点",
                       "门": {}, "理由": "不单独起执行体（只等依赖）"})
            continue
        门 = [g for g in (s.get("gates") or []) if isinstance(g, dict)]
        if not 门:
            # ★★★★★ R276b（真读数抓的）：**R269 的修法造出了新死锁。**
            #   真证据（真仓跑 close_loop）：`constraints:["不许改 tests/"]` 但没写
            #   `constraint` 关联门 ⇒ R269 正确地"不拿实现判据冒充约束判据" ⇒ con_0 无门；
            #   而 con_0 被 task_0 依赖 ⇒ 它永远判"拒跑" ⇒ **下游全部级联跳过**，
            #   整棵树静默停摆，而且**没有任何一段是红的**（所以连改动面都算不出来）。
            #   ⇒ 语义要分开：
            #     · `constraint_check` 无门 = **该约束没有可机判形态**（如实记账，不算红，
            #       也不能当"达成证据"）⇒ 放行下游，但在回执里明写"未机判"；
            #     · `errand` 无门 = 真的无裁决依据 ⇒ **拒跑**（这条不变）。
            #   口径：**"没有约束门"和"没有实现门"是两件事**，混在一档就会
            #     要么假绿（把无门当通过）要么死锁（把无门当拒跑）。
            if kind == "constraint_check":
                段.append({"tid": tid, "kind": kind, "状态": "无门",
                           "门": {}, "理由": "该约束在档案里没有可机判的门 ⇒ 未机判"
                                             "（不算绿、也不算红）"})
                已落地.add(tid)
                continue
            段.append({"tid": tid, "kind": kind, "状态": "拒跑",
                       "门": {}, "理由": "该段没有判据门 ⇒ 无裁决依据（空判据不许当绿）"})
            原因.append(f"{tid} 拒跑：没有判据门，不能算达成")
            continue
        try:
            果, 明细 = _gs.evaluate(门, 根, 根)
        except Exception as e:                      # noqa: BLE001
            段.append({"tid": tid, "kind": kind, "状态": "异常",
                       "门": {}, "理由": f"{type(e).__name__}: {e}"})
            原因.append(f"{tid} 判据门求值异常：{type(e).__name__}: {e}")
            continue
        绿 = all(bool(v) for v in 果.values())
        if 绿:
            已落地.add(tid)
        段.append({"tid": tid, "kind": kind,
                   "状态": "绿" if 绿 else "红", "门": 果,
                   # ★ R276c：把**门规格原文**回传，下游才能从中取出
                   #   门自己声明的目标文件（`in`/`file`/`path`）。
                   #   只回 `{门名: bool}` 就丢了"该改哪个文件"这个**声明**。
                   "_门规格": 门,
                   "理由": "" if 绿 else "有门未通过"})
        if not 绿:
            红门 = [k for k, v in 果.items() if not v]
            原因.append(f"{tid}（{kind}）红：{红门}")

    会跑 = [x for x in 段 if x["状态"] in ("绿", "红", "拒跑", "异常")]
    达成 = bool(会跑) and all(x["状态"] == "绿" for x in 会跑)
    if not 会跑:
        原因.append("一个会跑的段都没有 ⇒ 没有可判的实现 ⇒ 不许当达成")
    return {"ok": 达成, "段": 段, "达成": 达成,
            "未达成的原因": [] if 达成 else 原因,
            "耗时秒": round(time.time() - 起, 3),
            "诚实边界": ("本执行器只裁决不改码；段内容由调用方产出。"
                        "达成 = 全部会跑的段都绿（汇总点不裁决）。")}
