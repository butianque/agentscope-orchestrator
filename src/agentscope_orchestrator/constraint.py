"""约束由目标派生 · 判别式必须穿过约束的商（TS-378 路径的工程落形）。

## 数学依据（活库已机证，`#check` 逐条核验过）

`TS378.ConstraintOfGoal`（`TS378_AdaptiveUniversal.lean:141`）：

    def ConstraintOfGoal {α : Type} (G : α → Prop) (x y : α) : Prop := G x ↔ G y

`TS378.constraintOfGoal_equivalence`（:145）：
    约束**自动**是等价关系（三条全由 `Iff` 的群结构给出）—— 不需人工声明。

`TS378.disc_factors_through_constraint`（:157）★ 核心：

    theorem disc_factors_through_constraint {α β : Type} (G : α → Prop) (f : α → β)
        (hf : ∀ x y : α, ConstraintOfGoal G x y → f x = f y) :
        ∃ F : Quot (ConstraintOfGoal G) → β, ∀ x : α, F (Quot.mk _ x) = f x

    ⇒ **任何"尊重约束"的判别式，必唯一地穿过约束的商。**
    ⇒ 约束唯一决定判别式 —— 判别式不是独立定义的，是**派生的**。

`TS378.constraint_bounds_discrimination`（:165）：
    约束归为一类的两点，**任何**由该约束派生的判别式都分不开。
    ⇒ **判别力的上限完全由目标决定，不由编码技巧决定。**

`TS378.goal_change_changes_constraint`（:174）：
    目标变 ⇒ 约束变（自适应性来源），且是**同一个构造子**产出的。

## 本模块的映射

| 数学对象 | 工程对应物 |
|---|---|
| 目标 `G : α → Prop` | 规约里的 `pred`（结构化 P）—— 一个产物**是否满足目标** |
| 约束 `ConstraintOfGoal G x y`（`G x ↔ G y`） | 两个产物**在目标眼里是否同类**（都达标 / 都不达标） |
| 判别式 `f : α → β` | 判据门（把产物映到 绿/红） |
| `hf : 约束同类 ⇒ f 同类` | **门必须尊重目标**：目标分不开的两点，门也不许分开 |
| `disc_factors_through_constraint` | `判别式尊重约束()`：机检门是否真穿过约束的商 |
| `constraint_bounds_discrimination` | 违反者 ⇒ **假红生成器**（判得比目标还细 ⇒ 拒掉合格产出） |
| `goal_change_changes_constraint` | `目标变约束变()`：换目标 ⇒ 约束与门集随之变 |

## 为什么这是真缺口

编排器原状：门集由 `derive_gates(pred)` 派生 —— **方向是对的**（门来自目标，不由模型造）。
但**没有任何检查**保证门真的"尊重"目标。于是可以出现：

  · **假红**（门比目标细）：两个产物目标眼里同类（都达标），门却判不同档
    ⇒ 合格产出被拒 ⇒ 编排器反复返工烧资源（实测烧过 31 万字符）。
    这正是 `constraint_bounds_discrimination` 说的"判别力超出目标给的上限"。
  · **假绿**（门比目标粗）：目标能分的两点，门判同一档 ⇒ 门分不开 ⇒ 缺陷
    （这一半由 `discriminant.py` 的缺陷对派生处理）。

本模块补的是**假红**那一半 —— 用 TS-378 的商判据机检。

## 诚实边界

· 约束的判定需要**能判 `G x`**（该产物是否达标）。本模块不发明这个判定：
  它由 `pred` 的 `checks` 在**真实产物**上跑出来（`gate_spec.evaluate`），
  与生产判门口径一致。判不出就如实标 `unknown`，不猜。
· 抽样是**有限**的：只检查给定的样本对，不是全称证明。本模块只把
  「已发现的违反」挡在门外，不声称"已验证门完全尊重约束"。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from agentscope_orchestrator import gate_spec


# ── 约束：由目标派生（TS378 `ConstraintOfGoal`）────────────────────
def 约束(达标A: bool, 达标B: bool) -> bool:
    """`ConstraintOfGoal G A B := G A ↔ G B`（TS378:141 的工程落形）。

    两个产物**在目标眼里同类** ⟺ 它们达标与否**相同**。
    """
    return bool(达标A) == bool(达标B)


def 约束是等价关系(样本: list[bool]) -> tuple[bool, list[str]]:
    """机检约束的自反/对称/传递（TS378:145 `constraintOfGoal_equivalence`）。

    ★ 数学上这三条**自动**成立（由 `Iff` 的群结构给出），不需人工声明。
      本函数是**工程自检**：确认实现没写错（而非"发现"新性质）。
    """
    问题: list[str] = []
    for a in 样本:
        if not 约束(a, a):
            问题.append(f"自反失败：{a}")
    for a in 样本:
        for b in 样本:
            if 约束(a, b) != 约束(b, a):
                问题.append(f"对称失败：{a},{b}")
    for a in 样本:
        for b in 样本:
            for c in 样本:
                if 约束(a, b) and 约束(b, c) and not 约束(a, c):
                    问题.append(f"传递失败：{a},{b},{c}")
    return (not 问题), 问题


# ── 判别式尊重约束（TS378 `disc_factors_through_constraint`）────────
def 判别式尊重约束(门: dict, 样本: list[tuple[str, Path]],
                   work_dir: Path, cwd_default: str = ".",
                   达标: dict[str, bool] | None = None,
                   ) -> tuple[str, list[str]]:
    """机检一个门是否**穿过约束的商**（TS378:157）。

    判据（严格对应 `hf : ∀ x y, ConstraintOfGoal G x y → f x = f y`）：
        约束同类（`G x ↔ G y`）⇒ 门判同一档（`f x = f y`）。

    违反 ⇒ 该门**判别力超出目标给的上限**（`constraint_bounds_discrimination`）
        ⇒ **假红生成器**：目标眼里同类的两点被门分开 ⇒ 合格产出被拒。

    参数
    ----
    门     : 判据门（dict，gate_spec 格式）
    样本   : [(标签, 产物路径)]，**已按 `达标` 分好类**的样本
    work_dir / cwd_default : 门执行上下文
    达标   : {标签: 该产物是否达标}。**必须由外部真实判定给出**
             （通常来自 `pred` 的 checks 在真产物上跑的结果）。
             为 None ⇒ 无法判约束 ⇒ 如实返回 "unknown"。

    返回 (verdict, 说明)；verdict ∈ {"尊重", "违反", "unknown"}。
    """
    if 达标 is None or len(样本) < 2:
        return "unknown", ["缺目标达标判定或样本不足，无法判约束（如实标 unknown）"]

    名 = 门.get("name", 门.get("type", "gate"))
    违反: list[str] = []
    检查数 = 0
    for i in range(len(样本)):
        for j in range(i + 1, len(样本)):
            标i, 路i = 样本[i]
            标j, 路j = 样本[j]
            if 标i not in 达标 or 标j not in 达标:
                continue
            if not 约束(达标[标i], 达标[标j]):
                continue        # 目标能分开 ⇒ 门分开是**对的**，不检查
            检查数 += 1
            # 约束同类 ⇒ 门必须判同一档
            ri, _ = gate_spec.evaluate([dict(门)], 路i, work_dir, cwd_default)
            rj, _ = gate_spec.evaluate([dict(门)], 路j, work_dir, cwd_default)
            vi, vj = ri.get(名), rj.get(名)
            if vi != vj:
                违反.append(
                    f"约束同类（达标={达标[标i]}）但门判不同档："
                    f"{标i}={vi} vs {标j}={vj} ⇒ 假红（判别力超出目标上限）")

    if 检查数 == 0:
        return "unknown", ["样本里没有『目标同类』的对，无法检查（如实标 unknown）"]
    if 违反:
        return "违反", 违反
    return "尊重", [f"检查 {检查数} 对同类样本，门判一致 ⇒ 门穿过约束的商"]


def 机检门集尊重约束(门集: list[dict], 样本: list[tuple[str, Path]],
                     work_dir: Path, 达标: dict[str, bool],
                     cwd_default: str = ".") -> dict:
    """对整份门集做「尊重约束」机检（TS378 路径的批量落地）。

    返回 {"尊重": [...], "违反": [...], "unknown": [...], "ok": bool, "报告": [...]}。
    `ok` 为真 ⟺ **没有**门违反约束（unknown 不阻断，但如实列出）。
    """
    尊重, 违反, 未知, 报告 = [], [], [], []
    for g in 门集:
        v, 说明 = 判别式尊重约束(g, 样本, work_dir, cwd_default, 达标)
        名 = g.get("name", g.get("type", "gate"))
        报告.append({"gate": 名, "verdict": v, "detail": 说明})
        if v == "尊重":
            尊重.append(名)
        elif v == "违反":
            违反.append(名)
        else:
            未知.append(名)
    return {"尊重": 尊重, "违反": 违反, "unknown": 未知,
            "ok": not 违反, "报告": 报告}


def 目标变约束变(目标A_达标: dict[str, bool],
                 目标B_达标: dict[str, bool],
                 标签: list[str]) -> tuple[bool, str]:
    """机检「目标变 ⇒ 约束变」（TS378:174 `goal_change_changes_constraint`）。

    两个不同目标给出的约束若**不同**，则确认约束确实是目标的函数
    （不是人工固定的一堆等价类）。
    """
    if not 标签:
        return False, "无标签，无法比较"
    不同 = False
    for i in range(len(标签)):
        for j in range(i + 1, len(标签)):
            a, b = 标签[i], 标签[j]
            if a not in 目标A_达标 or b not in 目标A_达标:
                continue
            if a not in 目标B_达标 or b not in 目标B_达标:
                continue
            if 约束(目标A_达标[a], 目标A_达标[b]) != \
               约束(目标B_达标[a], 目标B_达标[b]):
                不同 = True
    return 不同, ("约束随目标变化 ⇒ 约束是目标的函数（内生）" if 不同
                  else "两个目标给出同一约束 ⇒ 无法确认内生（样本不足或目标太近）")


def _selftest() -> int:
    ok = total = 0

    def chk(name: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  ✗ {name}")

    # ① 约束 = G A ↔ G B
    chk("同类：都达标", 约束(True, True))
    chk("同类：都不达标", 约束(False, False))
    chk("异类：一达标一不达标", not 约束(True, False))

    # ② 约束自动是等价关系（TS378:145）
    e_ok, e_prob = 约束是等价关系([True, False])
    chk("约束是等价关系", e_ok)
    chk("等价关系无问题项", not e_prob)

    # ③ 门尊重约束 ⇒ 判「尊重」
    d = Path(tempfile.mkdtemp(prefix="cons_st_"))
    try:
        a, b = d / "a.py", d / "b.py"
        a.write_text("def f():\n    return 1\n", encoding="utf-8")
        b.write_text("def g():\n    return 2\n", encoding="utf-8")
        # 门只要求含 "def " —— a、b 都含 ⇒ 判同一档
        门 = {"name": "hasdef", "type": "grep_required", "patterns": ["def "]}
        样本 = [("a", a), ("b", b)]
        达标 = {"a": True, "b": True}      # 目标眼里同类
        v, _ = 判别式尊重约束(门, 样本, d, ".", 达标)
        chk("同类样本判一致 ⇒ 尊重", v == "尊重")

        # ④ 门违反约束 ⇒ 判「违反」（假红生成器）
        #    a 含 "return 1"、b 不含 ⇒ 门判不同档，但目标说二者同类
        门2 = {"name": "has1", "type": "grep_required", "patterns": ["return 1"]}
        v2, 说明2 = 判别式尊重约束(门2, 样本, d, ".", 达标)
        chk("同类样本判不同 ⇒ 违反", v2 == "违反")
        chk("违反说明含假红字样", any("假红" in s for s in 说明2))

        # ⑤ 目标能分开的两点，门分开是**对的** ⇒ 不判违反
        达标2 = {"a": True, "b": False}
        v3, _ = 判别式尊重约束(门2, 样本, d, ".", 达标2)
        chk("目标异类时门分开不算违反", v3 == "unknown" or v3 == "尊重")

        # ⑥ 缺达标判定 ⇒ unknown（不猜）
        v4, _ = 判别式尊重约束(门, 样本, d, ".", None)
        chk("缺达标判定给 unknown", v4 == "unknown")

        # ⑦ 批量机检：ok 反映有无违反
        r = 机检门集尊重约束([门], 样本, d, 达标, ".")
        chk("批量机检 ok=True（无违反）", r["ok"] is True)
        r2 = 机检门集尊重约束([门, 门2], 样本, d, 达标, ".")
        chk("批量机检 ok=False（有违反）", r2["ok"] is False)
        chk("批量机检列出违反门", "has1" in r2["违反"])

        # ⑧ 目标变约束变（TS378:174）
        变, _ = 目标变约束变({"a": True, "b": True}, {"a": True, "b": False},
                             ["a", "b"])
        chk("目标变约束变", 变)
        未变, _ = 目标变约束变({"a": True, "b": True}, {"a": True, "b": True},
                              ["a", "b"])
        chk("同目标约束不变", not 未变)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(f"  约束派生·判别式穿过商 自检：{ok}/{total} 通过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
