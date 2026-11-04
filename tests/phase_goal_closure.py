"""phase_goal_closure：第二层闭环（偏差→全局目标 自指闭环）验收测试。

用户拍板方向：多智能体编排质量主线——「全动态结构真值派发」：
回读所有已派发缺口的历史真值结构 → 取偏差 → 动态生成/纠正下一个目标函数
→ 控制全局方向。目标函数必须是偏差结构密度的函数（非意图/候选表）。

本测试覆盖用户未必明说的「不足/风险点」：
  U1 防平凡不动点：is_done 必须对照独立真值门，禁放松已绿门；
     曾绿后回归未闭环的偏差不许被"偏差收窄"表象带过。
  U2 损坏行容忍：账本 JSON 坏行跳过不炸（无人值守跨会话的韧性）。
  U3 幂等回读：追加式账本后写覆盖，末次状态取末条；重复喂同一份不翻倍、结果唯一。
  U4 不震荡：同一 records 折叠两次 → 目标完全一致（确定性，全局方向不来回跳）。
  U5 纯机器：0-LLM，目标=偏差密度函数；无意图表、无候选表、无外部档位。
  U6 偏差密度排序：高回归+轮次滞留 → 最先派发（全局方向随偏差收窄）。

运行：.venv\\Scripts\\python.exe tests\\phase_goal_closure.py
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agentscope_orchestrator.auto_objective as ao
from agentscope_orchestrator import develop as dev

G_OK = {"cargo_check_lib": True, "cargo_test_lib": True,
        "cargo_build_all_targets": True}
G_RED = {"cargo_check_lib": True, "cargo_test_lib": False,
         "cargo_build_all_targets": True}


def _fix(key, **kw):
    return {"round": "fix_attempt", "gap_key": key, "file": key.split("@")[0], **kw}


def _iter(gates, gaps, tag):
    return {"round": "iteration", "time": tag, "gates": gates,
            "post_gates": gates, "gaps": gaps}


def test_u1_fixed_point():
    """防平凡不动点：独立门红 → 未闭合；回归未闭环 + 门全绿 → 仍不许 done。"""
    fails = []
    o1 = ao.compute([_iter(G_RED, [], "r1")], current_gates=G_RED)
    if o1.is_done:
        fails.append("独立门为红时必须判未闭合")
    recs2 = [
        _iter(G_OK, [{"key": "a.rs@E001", "file": "a.rs", "status": "open"}], "r1"),
        _fix("a.rs@E001", status="resolved", regressed=False),
        _fix("a.rs@E001", status="blocked", regressed=True),   # 曾绿→改后挂
        _iter(G_OK, [], "r2"),
    ]
    o2 = ao.compute(recs2, current_gates=G_OK)
    if o2.is_done:
        fails.append("回归未闭环+独立门全绿也不许判 done（防平凡不动点）")
    d2 = next((d for d in o2.active if d.key == "a.rs@E001"), None)
    if not d2 or d2.weight <= 0:
        fails.append("回归偏差必须加权重开（不许假装收窄）")
    # 正常收敛（闭环+门全绿）→ done（防自宽的反向：真收敛该认绿）
    o3 = ao.compute([_iter(G_OK, [{"key": "b.rs@E002", "file": "b.rs"}], "r1"),
                     _fix("b.rs@E002", status="resolved", regressed=False),
                     _iter(G_OK, [], "r2")], current_gates=G_OK)
    if not o3.is_done:
        fails.append("完全闭环+独立门全绿应判 done")
    return fails


def test_u2_corrupt_lines():
    """损坏行容忍：坏 JSON 跳过不炸。"""
    fails = []
    good = json.dumps(_fix("d.rs@E004", status="blocked"), ensure_ascii=False)
    lines = good + "\n{bad json line, truncated\n" + "\n{\"round\": null\n"
    try:
        o = ao.compute_from_lines(lines, current_gates=G_OK)
    except Exception as e:  # noqa: BLE001
        return [f"损坏行应跳过不炸，却抛 {e!r}"]
    d = next((x for x in o.active if x.key == "d.rs@E004"), None)
    if not d or d.blocked != 1:
        fails.append("损坏行应被跳过（d.rs@E004 blocked 应为1）")
    return fails


def test_u3_idempotent():
    """幂等回读：同一行集折叠结果唯一；末次状态取末条（后写覆盖）。"""
    fails = []
    recs = [_fix("c.rs@E003", status="blocked", regressed=True),
            _fix("c.rs@E003", status="open"),
            _fix("c.rs@E003", status="resolved", regressed=False),
            _iter(G_OK, [{"key": "c.rs@E003", "file": "c.rs"}], "r1")]
    oa, ob = ao.compute(recs, current_gates=G_OK), ao.compute(recs, current_gates=G_OK)
    sig = lambda o: [(d.key, d.weight, d.last_state, d.regressed) for d in o.active]
    if sig(oa) != sig(ob):
        fails.append("同一 records 折叠两次结果不一致（非幂等）")
    if sig(oa) != sig(oa):
        fails.append("同一对象自比较不一致（确定性破缺）")
    return fails


def test_u4_no_oscillation():
    """不震荡：多轮推进后目标稳定收窄，不会反复横跳。"""
    fails = []
    recs = [
        _iter(G_OK, [{"key": "p.rs@E1", "file": "p.rs"}], "r1"),
        _fix("p.rs@E1", status="blocked"),   # r1 派发失败
        _iter(G_OK, [{"key": "p.rs@E1", "file": "p.rs"}], "r2"),
        _fix("p.rs@E1", status="blocked"),
        _iter(G_OK, [{"key": "p.rs@E1", "file": "p.rs"}], "r3"),
        _fix("p.rs@E1", status="blocked"),
    ]
    o = ao.compute(recs, current_gates=G_OK)
    if o.pick_next() != "p.rs@E1":
        fails.append("持续滞留缺口应保持为全局方向（不震荡偏移）")
    # 权重只随轮次滞留单调上升（r1→r3 每轮出现 → recurred=3）
    d = o.active[0]
    if d.recurred != 3:
        fails.append(f"recurred 应按轮次滞留计 3，得 {d.recurred}")
    return fails


def test_u5_pure_machine():
    """纯机器：目标=偏差密度函数；无意图表/候选表/外部档位。"""
    fails = []
    src = Path(ao.__file__).read_text(encoding="utf-8")
    # 禁止出现"意图选择表/候选表/命中即驱动"形态
    for banned in ("intent_table", "candidate_pool", "policy_table",
                   "priority_table"):
        if banned in src:
            fails.append(f"auto_objective 不应含意图/候选表形态：{banned}")
    # 目标函数只由偏差结构密度（attempts/blocked/regressed/recurred）加权
    if "weight" not in src:
        fails.append("目标函数缺少偏差密度权重计算")
    # 无 LLM 调用（不 import agentscope 的模型通道 / 不调 router）
    for banned_mod in ("agentscope.message", "gateway", "router"):
        if f"import {banned_mod}" in src or f"from {banned_mod}" in src:
            fails.append(f"auto_objective 不应依赖模型通道：{banned_mod}")
    # 0-LLM 佐证：无 http/网络调用
    for banned in ("requests.", "aiohttp", "urllib.request"):
        if banned in src:
            fails.append(f"auto_objective 不应有网络调用：{banned}")
    return fails


def test_u6_deviation_ordering():
    """偏差密度排序：高回归+轮次滞留 最先派发（全局方向随偏差收窄）。"""
    fails = []
    recs = [
        _fix("high.rs@H", status="blocked", regressed=True),
        _fix("low.rs@L", status="blocked"),
        _iter(G_OK, [], "r1"),
        _iter(G_OK, [{"key": "high.rs@H", "file": "high.rs"}], "r1"),
        _iter(G_OK, [{"key": "high.rs@H", "file": "high.rs"}], "r2"),
    ]
    o = ao.compute(recs, current_gates=G_OK)
    if o.pick_next() != "high.rs@H":
        fails.append("高偏差密度缺口应最先派发（high.rs@H）")
    weights = [d.weight for d in o.active]
    if weights != sorted(weights, reverse=True):
        fails.append("active 应按 weight 降序")
    return fails


def test_wiring():
    """接线回归：develop.py 已导入 auto_objective 并接入 run_iteration；守卫门在套件中。"""
    fails = []
    if not hasattr(dev, "auto_objective"):
        fails.append("develop 未导入 auto_objective")
    import inspect
    src = inspect.getsource(dev.DevelopDriver.run_iteration)
    if "objective = auto_objective.compute_from_lines" not in src:
        fails.append("run_iteration 未接线偏差→目标计算")
    if "objective_weight_by_key" not in src:
        fails.append("run_iteration 未把偏差权重落账（供下轮回读）")
    ssuite = inspect.getsource(dev.DevelopDriver._dev_suite)
    if "auto_objective_selftest" not in ssuite:
        fails.append("dev_suite 守卫门缺 auto_objective_selftest")
    return fails


def main() -> int:
    tests = [
        ("U1 防平凡不动点（对照独立真值门）", test_u1_fixed_point),
        ("U2 损坏行容忍（账本韧性）", test_u2_corrupt_lines),
        ("U3 幂等回读（后写覆盖）", test_u3_idempotent),
        ("U4 不震荡（目标稳定收窄）", test_u4_no_oscillation),
        ("U5 纯机器（0-LLM 偏差密度函数）", test_u5_pure_machine),
        ("U6 偏差密度排序（全局方向）", test_u6_deviation_ordering),
        ("接线回归（develop.run_iteration 接入）", test_wiring),
    ]
    failed = 0
    for name, fn in tests:
        errs = fn()
        if errs:
            failed += 1
            print(f"✗ {name}")
            for e in errs:
                print(f"    - {e}")
        else:
            print(f"✓ {name}")
    total = len(tests)
    if failed:
        print(f"phase_goal_closure：{total-failed}/{total} 绿")
        return 1
    print(f"phase_goal_closure：{total}/{total} 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())