"""auto_objective：全动态结构真值派发 —— 偏差→全局目标 自指闭环（编排层机器工具位）。

本模块实现第二层闭环（用户主线：多智能体编排质量）：
   回读所有已派发缺口的**历史真值结构** → 取偏差 → 动态生成/纠正下一个目标函数
   → 控制全局方向随偏差收窄。

与既有层次的分工（不重复、不打架）：
  - 判据演化 `develop.sediment_evolved`：单点 OLD/NEW 对象沉淀复用（机构变对象）。
  - 本模块 `auto_objective`：**整条缺口历史 → 偏差密度排序 → 下一轮派发优先级**，
    是"偏差→全局目标"的闭环，天然依赖以上各轮真值，但目标函数本身 0-LLM、纯机器。

铁律落点：
  ① 目标函数必须是偏差结构密度的函数，不是"意图/候选表"（G7：判断力来自真值密度）。
  ② 防平凡不动点：is_done **必须对照独立真值门**（current_gates），禁止放松已绿门——
     任何既定真值门此刻为红、或曾绿后回归（regressed）未闭环，都不得判目标完成，
     绝不因"偏差整体收窄/目标看起来变小"就自行收敛。
  ③ 幂等回读：账本追加式，本模块读多行时**后写覆盖前条**（latest 状态取末次），
     损坏行跳过。同一份记录集合折叠结果唯一（确定性 → 不震荡）。

偏差向量结构（每 `file@code` 累加自历史 fix_attempt/iteration 记录）：
  - attempts   累计尝试次数         —— 反复改却不过，偏差升高
  - blocked    累计阻塞次数          —— 长期派而不得解，偏差升高
  - regressed  曾绿→改后挂的回归次数  —— 破坏已绿门，偏差陡升（防放松已绿门）
  - resolved   累计确证绿（已闭环）    —— 闭合不升偏差
  - recurred   该缺口在几轮 iteration 中反复出现 —— 真值滞留/open 反复，偏差升高
  - last_state 末次状态（resolved/blocked/regressed/open）

权重（偏差密度，纯近似不查表）：
    weight = recurred + 3·regressed + 2·blocked + 0.25·clamped(attempts)
  已闭环（末态 resolved 且无回归滞留）且权重趋零 → 出 active（被真值闭合，非被放宽）。
  active 按 weight 降序 = 全局方向（先打最大偏差），随偏差收窄 active 越窄越集中。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# 默认偏差密度系数（尽量忙学派生的稳定近似；纯机器可替换，不构成"意图表"）
_W_REGRESS = 3.0
_W_BLOCK = 2.0
_W_ATTEMPTS = 0.25

# window_conflict / observation_dry：非"真未解"的 blocked，不计入偏差（避免把
# 多窗互斥/占位 dry 当短板放大）。
_NON_DEVIATION_BLOCK_SOURCES = {"window_conflict", "observation_dry"}


@dataclass
class Deviation:
    key: str                          # file@code
    file: str
    code: str
    attempts: int = 0
    blocked: int = 0
    progressed: int = 0
    regressed: int = 0
    resolved: int = 0
    recurred: int = 0
    last_state: str = "open"
    weight: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Objective:
    """目标函数：偏差结构密度 → 全局方向（active 排序）+ 独立门闭合判定。"""
    active: list[Deviation] = field(default_factory=list)  # 待打缺口，weight 降序
    is_done: bool = False
    gate_truth: dict = field(default_factory=dict)         # 本轮独立真值门（判谁绿）
    note: str = ""

    def to_dict(self) -> dict:
        return {"active": [d.to_dict() for d in self.active],
                "is_done": self.is_done, "gate_truth": self.gate_truth,
                "note": self.note}

    def pick_next(self) -> str | None:
        """全局方向 = 当前偏差密度最高的缺口 key（0-LLM 计算，非查表）。"""
        return self.active[0].key if self.active else None


def _iter_fold(records) -> list:
    """追加式账本折叠：逐条 yield，容忍非 dict 项（类型错）。"""
    for rec in records:
        if isinstance(rec, dict):
            yield rec


def compute(records, current_gates: dict | None = None,
            p2_green: set | None = None) -> Objective:
    """从账本 records 聚合偏差结构，产出目标函数。

    records:        账本记录的 list[dict]（iteration + fix_attempt 混合，可含损坏项）。
    current_gates:  本轮最新独立真值门 {gate_name: bool}；为 None 时用账本最后一次
                    post_gates 兜底（仍是对照门真值，不放松）；两者皆无 → 无法确认绿，
                    保守 is_done=False。
    p2_green:       曾绿门身份单点映射 set（C5：每门一位，不折叠历史向量）。为 None 时
                    regressed 只用记录里的显式标记（向后兼容旧账本）。

    返回 Objective。纯函数：同一 records → 同一结果（幂等、不震荡）。
    """
    if records is None:
        records = []

    # ── 1. 逐条折叠：按 gap_key 累加偏差结构 ────────────────────
    acc: dict[str, dict] = {}                 # key -> accumulator
    round_seen: dict[str, set] = {}           # key -> {round_tag}
    last_post_gates: dict | None = None

    for rec in _iter_fold(records):
        # fix_attempt 记录：逐缺口真值
        gk = rec.get("gap_key")
        if gk:
            a = acc.setdefault(gk, {"file": rec.get("file") or gk.split("@")[0],
                                    "code": gk.split("@")[1] if "@" in gk else "",
                                    "attempts": 0, "blocked": 0, "progressed": 0,
                                    "regressed": 0,
                                    "resolved": 0, "last_state": "open"})
            a["attempts"] += int(rec.get("attempt") or 0)
            status = rec.get("status")
            source = rec.get("source")
            if status == "resolved":
                a["resolved"] += 1
                a["last_state"] = "resolved"
            elif status == "progressed":
                # ★★★【TS-442】新增第三态：**同门仍红、但红的因由已变** ⇒ 有进展。
                #   旧版把它并进 `blocked` ⇒ `attempts` 累积 ⇒ 判 stalled_fixed_point
                #   ⇒ 换门集/停机，而真实进展刚刚发生（"任务永不收敛"的独立成因）。
                #   ⇒ **不计入 blocked**（那才是"真停滞"），单独计数。
                a["progressed"] += 1
                a["last_state"] = "progressed"
            elif status == "blocked":
                if source not in _NON_DEVIATION_BLOCK_SOURCES:
                    a["blocked"] += 1
                a["last_state"] = "blocked" if source not in _NON_DEVIATION_BLOCK_SOURCES \
                    else a["last_state"]
            elif status == "open":
                a["last_state"] = "open"
            # C5：regressed（曾绿→改后挂）锚定「曾绿身份单点映射」，不折叠历史向量。
            # p2_green 为曾绿门身份集合（每门一位）；其身份仍红 且 该门曾绿 → 回归。
            if p2_green is not None:
                regr = bool(rec.get("regressed")
                            or (status == "blocked" and gk in p2_green))
            else:
                regr = bool(rec.get("regressed"))
            if regr:
                a["regressed"] += 1
                a["last_state"] = "regressed"   # 曾绿→改后挂：滞留为最高信号

        # iteration 记录：真值门 + 缺口轮次滞留（recurred）
        rec_gates = rec.get("post_gates") or rec.get("gates")
        if isinstance(rec_gates, dict) and rec_gates:
            last_post_gates = rec_gates      # 后写覆盖前条 → 幂等回读语义
        gaps = rec.get("gaps")
        if isinstance(gaps, list):
            rtag = rec.get("time") or str(rec.get("ts"))
            for g in gaps:
                if not isinstance(g, dict):
                    continue
                gk2 = g.get("key")
                if not gk2:
                    continue
                a = acc.setdefault(gk2, {"file": g.get("file") or gk2.split("@")[0],
                                         "code": gk2.split("@")[1] if "@" in gk2 else "",
                                         "attempts": 0, "blocked": 0, "regressed": 0,
                                         "resolved": 0, "last_state": "open"})
                round_seen.setdefault(gk2, set()).add(rtag)

    # ── 2. 偏差真值 = 独立门（外部优先，否则账本末次）────────────
    gate_truth = dict(current_gates) if current_gates else (
        dict(last_post_gates) if last_post_gates else {})

    # ── 3. 目标函数：偏差密度 → weight → active（weight 降序）────
    deviations: list[Deviation] = []
    for key, a in acc.items():
        recurred = len(round_seen.get(key, set()))
        w = (recurred
             + _W_REGRESS * a["regressed"]
             + _W_BLOCK * a["blocked"]
             + _W_ATTEMPTS * min(a["attempts"], 20))
        deviations.append(Deviation(
            key=key, file=a["file"], code=a["code"], attempts=a["attempts"],
            blocked=a["blocked"], regressed=a["regressed"], resolved=a["resolved"],
            recurred=recurred, last_state=a["last_state"],
            weight=round(w, 3)))

    # 已闭环出列：末态 resolved 且无回归滞留 且权重≈0（真值闭合，非放宽）
    active = [d for d in deviations
              if not (d.resolved > 0 and d.regressed == 0
                      and d.blocked == 0 and d.weight <= 0.0)]
    active.sort(key=lambda d: (-d.weight, -d.attempts, d.key))

    # ── 4. 防平凡不动点：is_done 必须对照独立真值门，禁止放松已绿门 ──
    gate_ok = bool(gate_truth) and all(v is True for v in gate_truth.values())
    # 回归未闭环 = 存在曾绿→改后挂、且末态不是 resolved 的偏差 —— 破坏已绿门不许装没看见
    unclosed_regress = any(d.regressed > 0 and d.last_state != "resolved"
                           for d in deviations)
    is_done = bool(gate_truth) and gate_ok and not unclosed_regress

    note = ""
    if not gate_truth:
        note = "无独立真值门可对照 → 保守判未闭合（防自宽）。"
    elif not gate_ok:
        red = [k for k, v in gate_truth.items() if v is not True]
        note = f"独立门未全绿（{red}）→ 目标未闭合，恰是该对照不放松已绿门。"
    elif unclosed_regress:
        note = "存在已绿后回归未闭环的偏差 → 目标不得判完成（防平凡不动点）。"

    return Objective(active=active, is_done=is_done,
                     gate_truth=gate_truth, note=note)


def compute_from_lines(lines, current_gates: dict | None = None,
                       p2_green: set | None = None) -> Objective:
    """从账本原始行文本计算（损坏 JSON 行跳过，幂等回读入口）。

    幂等：同一组 lines 折叠结果唯一；追加式后写覆盖 —— 末次状态/gate_truth 取
    集内最后一条。重复喂同一份 lines 不累积翻倍。
    """
    records: list = []
    if lines:
        for line in lines.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue        # 损坏行容忍：跳过不炸
    return compute(records, current_gates, p2_green)


# ── 自检（工程等价物：把 Lean 已证"收敛判定=对照结构真值非预设档"移植成机器断言）──
def _selftest() -> int:
    fails: list[str] = []

    def rec_fix(key, **kw):
        r = {"round": "fix_attempt", "gap_key": key, "file": key.split("@")[0],
             **kw}
        return r

    def rec_iter(gates, gaps, tag):
        return {"round": "iteration", "time": tag, "gates": gates,
                "post_gates": gates, "gaps": gaps}

    g_ok = {"cargo_check_lib": True, "cargo_test_lib": True,
            "cargo_build_all_targets": True}
    g_red = {"cargo_check_lib": True, "cargo_test_lib": False}

    # ① 防平凡不动点：独立门当前为红 → is_done False（禁止放松已绿门）
    recs = [rec_iter(g_red, [], "r1")]
    obj = compute(recs, current_gates=g_red)
    if obj.is_done:
        fails.append("独立门为红时必须判未闭合")
    if obj.note and "未全绿" not in obj.note:
        fails.append("note 应指明红门")

    # ② 回归未闭环：曾绿→改后挂, 独立门已绿 → 仍不许判 done（防平凡不动点核心）
    recs2 = [rec_iter(g_ok, [{"key": "a.rs@E001", "file": "a.rs", "status": "open"}], "r1"),
             rec_fix("a.rs@E001", status="resolved", resolved=True, regressed=False),
             rec_fix("a.rs@E001", status="blocked", regressed=True),  # 改后挂了
             rec_iter(g_ok, [], "r2")]
    obj2 = compute(recs2, current_gates=g_ok)
    if obj2.is_done:
        fails.append("有回归未闭环外加独立门全绿也不许判 done")
    d2 = next((d for d in obj2.active if d.key == "a.rs@E001"), None)
    if not d2 or d2.weight <= 0:
        fails.append("回归偏差必须加权重开")

    # ③ 正常收敛：闭环且独立门全绿 → done
    recs3 = [rec_iter(g_ok, [{"key": "b.rs@E002", "file": "b.rs", "status": "open"}], "r1"),
             rec_fix("b.rs@E002", status="resolved", regressed=False),
             rec_iter(g_ok, [], "r2")]
    obj3 = compute(recs3, current_gates=g_ok)
    if not obj3.is_done:
        fails.append("完全闭环+独立门全绿应判 done")

    # ④ 不震荡：同一 records 折叠两次结果一致（确定性）
    recs4 = [rec_fix("c.rs@E003", status="blocked", regressed=True),
             rec_fix("c.rs@E003", status="open"),
             rec_iter(g_ok, [{"key": "c.rs@E003", "file": "c.rs"}], "r1")]
    o4a = compute(recs4, current_gates=g_ok)
    o4b = compute(recs4, current_gates=g_ok)
    if [ (d.key, d.weight) for d in o4a.active ] != \
       [ (d.key, d.weight) for d in o4b.active ]:
        fails.append("同账本折叠应确定性一致（不震荡）")

    # ⑤ 损坏行容忍 + 幂等/确定性回读：坏 JSON 行跳过不炸；同一行集折叠结果唯一
    good_line = json.dumps(rec_fix("d.rs@E004", status="blocked"), ensure_ascii=False)
    lines = good_line + "\n{bad json line, truncat"
    o5a = compute_from_lines(lines, current_gates=g_ok)
    o5b = compute_from_lines(lines, current_gates=g_ok)
    if [ (d.key, d.weight, d.blocked) for d in o5a.active ] != \
       [ (d.key, d.weight, d.blocked) for d in o5b.active ]:
        fails.append("同一行集折叠应确定性一致（幂等）")
    d5 = next((d for d in o5a.active if d.key == "d.rs@E004"), None)
    if not d5 or d5.blocked != 1:
        fails.append("损坏行应被跳过（d.rs@E004 blocked 应为1）")
    # blocked(未回归)缺口：不把已绿门判红（非防自宽），但仍留在 active 供下一轮派发
    if not o5a.is_done:
        fails.append("blocked 非回归缺口不该把『独立门全绿』判成未闭合（防自宽的反向）")
    if o5a.pick_next() != "d.rs@E004":
        fails.append("blocked 缺口应留在全局方向待派发")

    # ⑥ 偏差密度排序：高回归+滞留 优先于 低偏差
    recs6 = [rec_fix("high.rs@H", status="blocked", regressed=True),
             rec_fix("low.rs@L", status="blocked"),
             rec_iter(g_ok, [], "r1"),
             rec_iter(g_ok, [{"key": "high.rs@H", "file": "high.rs"}], "r1"),
             rec_iter(g_ok, [{"key": "high.rs@H", "file": "high.rs"}], "r2")]
    obj6 = compute(recs6, current_gates=g_ok)
    if obj6.pick_next() != "high.rs@H":
        fails.append("高偏差密度缺口应最先派发（high.rs@H）")

    if fails:
        print(f"auto_objective 自检：{12-len(fails)}/{12} 绿")
        for f_ in fails:
            print(f"  ✗ {f_}")
        return 1
    print("auto_objective 自检：12/12 全绿")
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="偏差→目标 自指闭环（纯机器）")
    ap.add_argument("--selftest", action="store_true", help="跑自检门")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))