# -*- coding: utf-8 -*-
"""⑧ 的区分力标定（N9）：往「真模型链」的**回执**里注入变异，看 ⑧ 的判决抓不抓得住。

## N9 原来是"未做"，卡在哪

`orch-check` ⑧ 只证明过「真模型能改对」——那是**一次成功**，不是**判据的区分力**。
而区分力只能这样量：**故意把回执做成假的，看判据会不会点头**。
原来量不了的原因是 ⑧ 把「起子进程调模型」和「判回执」写在一起，
想测判决就得再花一次真模型调用（几十秒~几分钟一次，还得有活通道），于是永远没人测。

⇒ 本轮先把判决本体抽成 `selfcheck._判真模型回执(回)`（一次纯函数），
  本工具拿**合成回执**反复喂它。**不调模型、不花钱、可回放**。

## 判据

- **基线**：一个真做对的回执（`仲裁rc=0` + 输出含 `ORCH-OK-42`）必须判**绿**。
  基线不绿 ⇒ 判据只会拒绝，测不出区分力（先修基线再谈注入）。
- **注入**（每一个都**不许判绿**）：
  1. `仲裁rc` 0→1：门没复跑过
  2. 删掉成功标记（只退 0）：**R552 那个击穿** —— 产物顶层 `sys.exit(0)`，assert 没跑，退出码门照样绿
  3. `仲裁rc=1` 但输出含标记：自相矛盾证据
  4. 缺 `仲裁输出` 字段：信息缺失
  5. `仲裁rc=None`：拿不到读数
  6. 空回执 `{}`：整个回执丢了
- **未验不被记成绿**：`分类=基础设施不可用` ⇒ 必须返回 `None`（未验），
  既不许记绿（那是假绿），也不许记红（口径 311：假红比漏报更坏）。

## 诚实边界

· 这里量的是**判决逻辑**对**回执变异**的敏感度，不是"真模型改得对不对"（那一般不可判）。
· 变异是机械的：区分力高只说明"挡得住这类机械假绿"，**不**说明判据能识破精心构造的伪造回执。
· 读数写 `work\\八号区分力.json`（口径 191：可定位、可回放）。
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_包根 = pathlib.Path(__file__).resolve().parents[1]
# ★ 量的是**源码树里现在这份判决**（编辑中的代码），不是装出来的旧包。
sys.path.insert(0, str(_包根 / "src"))

_好回执 = {
    "分类": "达标",
    "错误": "",
    "门": {"compile": True, "whole_reading": True},
    "仲裁rc": 0,
    "仲裁输出": "ORCH-OK-42",
}


def _变异们(好: dict) -> list[tuple[str, dict, str]]:
    """(名, 变异回执, 为什么这算假绿) —— 每一条都必须**不判绿**。"""
    出: list[tuple[str, dict, str]] = []
    m = copy.deepcopy(好); m["仲裁rc"] = 1
    出.append(("仲裁rc 0→1", m, "门没复跑过就说改好了"))
    m = copy.deepcopy(好); m["仲裁输出"] = ""
    出.append(("删成功标记（只退 0）", m, "R552：sys.exit(0) 击穿退出码门，assert 根本没跑"))
    m = copy.deepcopy(好); m["仲裁rc"] = 1
    出.append(("有标记但 rc=1", m, "自相矛盾证据：说成功却退非零"))
    m = copy.deepcopy(好); m.pop("仲裁输出", None)
    出.append(("缺 仲裁输出 字段", m, "信息缺失还判绿 = 替文件下结论（口径 344）"))
    m = copy.deepcopy(好); m["仲裁rc"] = None
    出.append(("仲裁rc=None", m, "拿不到读数"))
    出.append(("空回执 {}", {}, "整个回执丢了"))
    return 出


def main() -> int:
    from agentscope_orchestrator import selfcheck as S  # noqa: PLC0415

    if not hasattr(S, "_判真模型回执"):
        print("✗ selfcheck 里没有 _判真模型回执 ⇒ 判决本体还没分离（N9 前置未做）")
        print("判定=不达标")
        return 1
    模块 = pathlib.Path(S.__file__)
    print(f"判决本体 = {模块}")

    # ── 基线：真做对的回执必须绿 ──
    基绿, 基证, _ = S._判真模型回执(copy.deepcopy(_好回执))
    print(f"基线（真做对）⇒ {'绿' if 基绿 else ('未验' if 基绿 is None else '红')} · {基证[:90]}")
    if 基绿 is not True:
        print("✗ 基线不绿 ⇒ 判据只会拒绝、测不出区分力（先修基线再谈注入）")
        print("判定=不达标")
        return 1

    # ── 注入：逐条量 ──
    抓 = 0
    明细 = []
    for 名, 回, 为什么 in _变异们(_好回执):
        判, 证据, _修 = S._判真模型回执(回)
        抓住 = 判 is not True
        抓 += int(抓住)
        明细.append({"变异": 名, "判决": ("绿" if 判 is True else ("未验" if 判 is None else "红")),
                     "抓住": 抓住, "为什么这算假绿": 为什么})
        print(f"  {'✓' if 抓住 else '✗ 存活'} {名:20s} ⇒ {'绿（没抓住）' if 判 is True else ('未验' if 判 is None else '红')}")

    # ── 未验不许被记成绿 ──
    未判, 未证, _ = S._判真模型回执({"分类": "基础设施不可用", "错误": "CIRCUIT_OPEN"})
    print(f"  环境不可用 ⇒ {'未验' if 未判 is None else ('绿' if 未判 else '红')}（必须=未验）· {未证[:70]}")

    总 = len(明细)
    print(f"区分力 = {抓}/{总} · 未验口径 = {'对' if 未判 is None else '错'}")

    读数 = {
        "模块": str(模块),
        "基线": {"判": "绿" if 基绿 else "非绿", "证据": 基证},
        "区分力": f"{抓}/{总}", "抓住": 抓, "注入": 总, "明细": 明细,
        "未验口径": "对" if 未判 is None else "错",
        "边界": "只量判决对**机械变异回执**的敏感度；不判真模型改得对不对，也不防精心伪造的回执",
    }
    出 = _包根 / "work" / "八号区分力.json"
    出.parent.mkdir(parents=True, exist_ok=True)
    出.write_text(json.dumps(读数, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"读数 → {出}")

    if 抓 != 总:
        存活 = [x["变异"] for x in 明细 if not x["抓住"]]
        print(f"✗ ⑧ 的判决对 {len(存活)} 个变异**不敏感** ⇒ 假绿入口：{存活}")
        print("判定=不达标")
        return 1
    if 未判 is not None:
        print("✗ 环境不可用没有被记成「未验」⇒ 会把环境问题报成产品红（口径 311）")
        print("判定=不达标")
        return 1
    print("判定=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())