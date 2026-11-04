# -*- coding: utf-8 -*-
"""运行总账 —— 每次「无人值守跑」一条记录，落盘可查。

═══════════════════════════════════════════════════════════════════════
为什么要它（实测缺口）
═══════════════════════════════════════════════════════════════════════
产品原有的落盘有两层：
  · `.audit_trail/generic_receipt.jsonl` —— **引擎内部**逐轮/逐窗的底料；
  · 批号 markdown 回执（`ts_receipt`）—— 单次任务的渲染件。

★ 但**「无人值守」这一层没有账**：跑完一次，没有一条能一眼查到的总记录。
  实测（2026-10-16）：想知道"昨天那次跑成没成、改了哪些文件、有没有回灌"
  只能去翻几千行 jsonl 或看控制台 —— 而无人值守的价值恰恰是**跑一夜后
  第二天能查**。

⇒ 本模块补这一层：`<audit>/orch_runs.jsonl`，**每次跑一行**。
  与既有两层**不重复**：那两层是引擎底料，这一层是**跑的总账**。

★ 口径（与库内一致）：
  · **报表与真实状态不一致，比没有报表更坏** ⇒ 只记**实测得到**的字段，
    拿不到就记 `null` 并说明，**不填默认值冒充**。
  · **崩溃不是报告** ⇒ 查询遇到坏行**跳过并计数**，不让整个历史读不出来。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _账本(档案=None) -> Path:
    from agentscope_orchestrator import profile as _pf
    档 = _pf.load_profile(档案) if 档案 else _pf.get_profile()
    d = Path(档.audit_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / "orch_runs.jsonl"


def 新号(模式: str = "goal") -> str:
    return f"run_{time.strftime('%m%d_%H%M%S')}_{模式}_{os.getpid() % 10000:04d}"


def 记(记录: dict, 档案=None) -> Path:
    """追加一条。**只写实测字段**；缺的留 None，不编。"""
    p = _账本(档案)
    行 = dict(记录)
    行.setdefault("记录时间", time.strftime("%Y-%m-%dT%H:%M:%S"))
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(行, ensure_ascii=False) + "\n")
    return p


def 查(档案=None, 裁决: str | None = None, 含目标: str | None = None,
      条数: int = 20) -> dict:
    """查历史。返回 {"记录": [...], "坏行": n, "总行": n}。

    ★ 坏行**跳过并计数**：一行坏了不该让整本账读不出来（崩溃不是报告）。
    """
    p = _账本(档案)
    if not p.is_file():
        return {"记录": [], "坏行": 0, "总行": 0}
    好: list[dict] = []
    坏 = 总 = 0
    for 行 in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not 行.strip():
            continue
        总 += 1
        try:
            好.append(json.loads(行))
        except json.JSONDecodeError:
            坏 += 1
    if 裁决:
        好 = [r for r in 好 if r.get("裁决") == 裁决]
    if 含目标:
        好 = [r for r in 好 if 含目标 in (r.get("目标") or "")]
    return {"记录": 好[-条数:], "坏行": 坏, "总行": 总}


def 渲染(记录) -> str:
    """把记录渲染成人读的多行文本。**单条与多条都收**。

    ★ R64（2026-10-16）：原签名是 `记录: list[dict]`，而**最自然的误用**
      就是传一条记录（`渲染(查(...)["记录"][0])`）。字典**是可迭代的**
      ⇒ 它会被当成"一串 key"逐个 `.get` ⇒ 在深处炸出
      `AttributeError: 'str' object has no attribute 'get'`。
      ★ 一个在**最自然用法**上以**最难懂方式**失败的接口，是产品缺陷 ——
        调用方看到的是"str 没有 get"，而不是"你该传列表"。
      ⇒ 两种形态都收，且**显式判型**（不靠"能不能迭代"这种巧合）。
    """
    if isinstance(记录, dict):
        记录 = [记录]
    if not 记录:
        return "（无记录）"
    出: list[str] = []
    for r in 记录:
        if not isinstance(r, dict):
            出.append(f"（坏记录，跳过：{type(r).__name__}）")
            continue
        改 = r.get("改动") or {}
        灌 = r.get("回灌") or {}
        出.append(
            f"{r.get('id', '?')}  [{r.get('裁决', '?')}]  "
            f"{r.get('秒', '?')}s  退出码={r.get('退出码', '?')}\n"
            f"    目标：{(r.get('目标') or '')[:110]}\n"
            f"    隔离树：{r.get('隔离树') or '（未隔离，直接改主树）'}\n"
            f"    净改动：改{改.get('改', 0)}/增{改.get('增', 0)}/删{改.get('删', 0)}"
            f"    回灌：落{灌.get('落', 0)}/让开{灌.get('让开', 0)}")
        if 灌.get("让开文件"):
            出.append(f"    ★ 让开（别人动过，没覆盖）：{灌['让开文件'][:6]}")
        if r.get("失败因"):
            出.append(f"    ✗ {r['失败因']}")
    return "\n".join(出)


def _自检() -> int:
    import tempfile
    fails: list[str] = []
    N = 0

    def 断言(c: bool, s: str) -> None:
        nonlocal N
        N += 1
        if not c:
            fails.append(s)

    根 = Path(tempfile.mkdtemp(prefix="orch_log_"))
    旧 = os.environ.get("ORCH_PROFILE")
    try:
        档 = 根 / "orch.yml"
        档.write_text("name: t\nrepo_root: .\naudit_dir: .audit\n", encoding="utf-8")
        os.environ["ORCH_PROFILE"] = str(档)
        from agentscope_orchestrator import profile as _pf
        _pf.reset_profile(str(档))

        记({"id": "r1", "裁决": "green", "目标": "甲任务", "秒": 10}, 档案=档)
        记({"id": "r2", "裁决": "red", "目标": "乙任务", "秒": 20,
            "回灌": {"落": 0, "让开": 2, "让开文件": ["x.rs", "y.rs"]}}, 档案=档)
        # ★ 坏行必须被跳过而不是炸掉整本账
        with _账本(档).open("a", encoding="utf-8") as f:
            f.write("{这不是合法 json\n")
        记({"id": "r3", "裁决": "green", "目标": "丙任务", "秒": 30}, 档案=档)

        全 = 查(档案=档, 条数=50)
        断言(全["总行"] == 4, f"查：总行不对 {全['总行']}")
        断言(全["坏行"] == 1, f"查：坏行没被计数 {全['坏行']}")
        断言(len(全["记录"]) == 3, f"查：好行数不对 {len(全['记录'])}")
        断言([r["id"] for r in 全["记录"]] == ["r1", "r2", "r3"],
             "查：顺序不对（应是时间正序）")

        绿 = 查(档案=档, 裁决="green")
        断言([r["id"] for r in 绿["记录"]] == ["r1", "r3"], "查：按裁决过滤不对")
        乙 = 查(档案=档, 含目标="乙")
        断言([r["id"] for r in 乙["记录"]] == ["r2"], "查：按目标过滤不对")
        限 = 查(档案=档, 条数=1)
        断言([r["id"] for r in 限["记录"]] == ["r3"], "查：条数上限不对")

        文 = 渲染(全["记录"])
        断言("r2" in 文 and "让开" in 文, "渲染：让开信息没出来")
        断言("x.rs" in 文, "渲染：让开文件没点名")
        断言("隔离树" in 文, "渲染：缺隔离树字段")

        空 = 查(档案=根 / "没有这个.yml")
        断言(空["记录"] == [] and 空["总行"] == 0, "查：不存在档案应返回空")

        # ── ★ R64 齿：`渲染` 收单条与多条都行 ──
        #   最自然的误用就是传**一条记录**（`渲染(查(...)["记录"][0])`）。
        #   字典**可迭代** ⇒ 旧写法会把它当"一串 key"逐个 `.get`，
        #   在深处炸出 `'str' object has no attribute 'get'`。
        #   ★ 一个在最自然用法上以最难懂方式失败的接口，是产品缺陷。
        单 = 渲染(全["记录"][0])
        断言("r1" in 单, "R64：渲染单条记录必须可用（传 dict 不该炸）")
        断言(渲染([]) == "（无记录）", "R64：渲染空列表")
        断言(渲染(None) == "（无记录）", "R64：渲染 None")
        # 坏元素不许炸整本渲染（崩溃不是报告）
        断言("坏记录" in 渲染([全["记录"][0], "这不是记录"]),
             "R64：坏元素必须跳过并说明，不许炸")
    finally:
        if 旧 is None:
            os.environ.pop("ORCH_PROFILE", None)
        else:
            os.environ["ORCH_PROFILE"] = 旧
        import shutil
        shutil.rmtree(根, ignore_errors=True)

    if fails:
        print(f"runlog 自检：{N - len(fails)}/{N} —— 红 {len(fails)}")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print(f"runlog 自检：{N}/{N} 全绿")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="运行总账（无人值守跑的历史）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--profile")
    ap.add_argument("--verdict", choices=["green", "red", "error"],
                    help="只看某个裁决")
    ap.add_argument("--grep", help="只看目标里含该子串的")
    ap.add_argument("-n", "--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    a = ap.parse_args(argv)
    if a.selftest:
        return _自检()
    r = 查(档案=a.profile, 裁决=a.verdict, 含目标=a.grep, 条数=a.limit)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"总账：{r['总行']} 行（坏行 {r['坏行']}）· 显示最近 {len(r['记录'])} 条")
        print(渲染(r["记录"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
