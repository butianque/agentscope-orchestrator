"""R278：**迭代闭环**——给一个目标，跑到达成或明确抛锚，并带预算护栏。

★ 为什么（真读数，2026-11-06）：
  `close_loop` 只跑**一轮**（一次应用 + 一次复验）。真实工程目标很少一轮到位：
  第①轮改对两段、第②轮才发现第三段还红。只跑一轮 ⇒ 调用方还得自己套循环，
  而"调用方自己套循环"就是 R274/R275/R277 反复出现的那个老病：
  **需要调用方补的步骤，等于没做。**

★ 本模块的三条硬约束（都是前几轮用真事故换来的）：
  1. **预算护栏**：最多 `max_rounds` 轮；每轮都记账。不许无限转。
  2. **不许空转**：某一轮的改动面与上一轮**完全相同**且裁决完全相同 ⇒
     判 `stalled`（原地打转），立刻停。这条防的是"每轮都重试同一个失败"。
  3. **每轮失败必须整批回滚**（复用 close_loop 的行为）；
     仓在归还时必须与进入本轮前一致 —— 不许越改越乱。

★ 诚实边界（最重要）：
  `改码来源` 是**可插拔回调**。没有模型/没有产出时，迭代**必须如实报
  `blocked_on_model` 并停在第 1 轮**，不许"跑满 max_rounds 再说到不了"——
  那会让人以为"试了很多轮"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import close_loop as _cl
from . import goal_runner as _gr

#: 改码回调签名： (轮次, 计划, 仓根, 上轮结果) -> {相对路径: 新全文} | None
改码器 = Callable[[int, dict, Path, dict], "dict[str, str] | None"]


def iterate(plan: dict, 仓根: Path | str, *,
            改码: 改码器 | None = None,
            max_rounds: int = 3,
            源码根: "list[str] | None" = None) -> dict:
    """迭代跑闭环，直到达成 / 抛锚 / 预算用尽。

    返回：
      {
        "ok": bool,                      # 是否达成
        "停因": "达成"|"阻塞"|"打转"|"预算用尽"|"一直没改码",
        "轮次": [{轮, 阶段, ok, 改动面, 应用, 未达成原因}],
        "用了几轮": int,
        "改动面轨迹": [[...], ...],       # 每轮算出的改动面（供查"是不是原地打转"）
        "blocked_on_model": bool,
        "诚实边界": str,
      }
    """
    根 = Path(仓根).resolve()
    n = max(1, int(max_rounds))
    轮次: list[dict] = []
    轨迹: list[list[str]] = []
    上_面: list[str] | None = None
    上_裁决: str | None = None
    停因 = ""
    阻塞 = False

    for i in range(1, n + 1):
        # 每轮：先让调用方产改码（没有就停，不许空转）
        产出 = None
        if 改码 is not None:
            try:
                产出 = 改码(i, plan, 根, {"轮次": 轮次, "上轮改动面": 上_面})
            except Exception as e:                  # noqa: BLE001
                轮次.append({"轮": i, "阶段": "改码器异常", "ok": False,
                             "原因": f"{type(e).__name__}: {e}"})
                停因 = "阻塞"
                阻塞 = True
                break
            if not isinstance(产出, dict):
                产出 = None

        果 = _cl.close_loop(plan, 根, adopted=产出, 源码根=源码根)
        面 = list((果.get("改动面") or {}).get("文件") or [])
        轨迹.append(面)
        本轮 = {"轮": i, "阶段": 果.get("阶段"), "ok": bool(果.get("ok")),
                "改动面": 面, "应用": 果.get("应用"),
                "未达成原因": [str(x)[:160] for x in
                              ((果.get("复验") or {}).get("未达成的原因") or [])]}
        轮次.append(本轮)

        if 果.get("ok"):
            停因 = "达成"
            break

        # 没改码输入 ⇒ 只可能是阻塞这一档；**不许"跑满轮数再说"**
        if 果.get("blocked_on_model"):
            停因 = "阻塞"
            阻塞 = True
            本轮["原因"] = ("没有改码输入 ⇒ 迭代无法继续。"
                          "**这不是'试了 %d 轮都没成'**，是第 1 轮就没有输入。" % i)
            break

        # 打转检测：改动面与裁决都跟上轮一样 ⇒ 原地打转，立刻停
        裁决纹 = "|".join(本轮["未达成原因"])
        if 上_面 is not None and 面 == 上_面 and 裁决纹 == (上_裁决 or ""):
            停因 = "打转"
            本轮["原因"] = ("本轮改动面与裁决与上轮**完全相同** ⇒ 原地打转，"
                          "继续跑只是重复同一个失败")
            break
        上_面, 上_裁决 = 面, 裁决纹
    else:
        停因 = "预算用尽"

    达成 = bool(轮次) and 轮次[-1]["ok"]
    return {
        "ok": 达成,
        "停因": 停因,
        "轮次": 轮次,
        "用了几轮": len(轮次),
        "改动面轨迹": 轨迹,
        "blocked_on_model": 阻塞,
        "诚实边界": (
            "达成：迭代到全绿。" if 达成 else
            "阻塞：没有改码输入 ⇒ 只证明裁决/定位是通的，**不证明目标能达成**；"
            "别把它当'试了多轮都没成'。" if 阻塞 and 停因 == "阻塞" else
            f"未达成（{停因}）：见 轮次[-1].未达成原因。每一轮失败都已整批回滚。"
        ),
    }
