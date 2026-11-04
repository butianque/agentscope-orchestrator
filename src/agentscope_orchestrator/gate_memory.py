"""跨任务学习：已确证的门**沉淀成对象**，供同结构任务复用（TS-378 路径）。

## 为什么需要它

编排器此前**跑一次就是一次**：任务 A 判出恒真门、派生出了有区分力的替换门，
任务 B 遇到同类恒真门时**从零再派生一遍**。这是"持续开发"的硬缺口 ——
工具不会因为干得多而变强。

## 数学依据（活库已机证，`#check` 核验过）

`TS378.disc_factors_through_constraint`（`TS378_AdaptiveUniversal.lean:157`）：

    theorem disc_factors_through_constraint (G : α → Prop) (f : α → β)
      (hf : ∀ x y, ConstraintOfGoal G x y → f x = f y) :
      ∃ F : Quot (ConstraintOfGoal G) → β, ∀ x, F (Quot.mk _ x) = f x

  ⇒ 尊重约束的判别式**必唯一地穿过约束的商**。门不是独立定义的，是**派生的**。

`TS378.constraint_bounds_discrimination`（:165）：
  约束归为一类的两点，**任何**由该约束派生的判别式都分不开
  ⇒ 判别力上限**完全由目标决定**。

## ★★ 关键：为什么复用**必须**过约束机检

`TS378.goal_change_changes_constraint`（:174）机证：

    (∀ x y, ConstraintOfGoal (fun _ => True) x y)          -- G≡True：全不可区分
    ∧ (¬ ConstraintOfGoal (fun n => n = 0) 0 1)            -- G:=(·=0)：可区分

  ⇒ **约束随目标变**。故：**为任务 A 派生的门，对任务 B 可能过细** ——
    它在 A 的约束下合法，在 B 的约束下就**越界**（假红）。

这就是本模块**不**做成"存下来直接用"的原因。复用必须满足两条：
  ① **真跑**在新任务产物上验过（造门 ≠ 门在新任务上有用）
  ② 过 `constraint.机检门集尊重约束`（TS-378）—— 目标变了就得重验

## 诚实边界（务必守住）

· 复用是**候选提议**，**不是**免检通行证。它省的是"重新派生"这一步，
  **不省**任何判据门 —— 门照样真跑。
· 结构指纹是**人工列的关键字表**（Lean/Rust/Python 声明关键字），
  不是从库里自动提取的。它保证的是"同语言同形态"，**不保证语义同类**。
· 命中计数是**经验统计**，不是证明。库里没有"复用有效性"的定理。
· 只沉淀**结构门**（`_structural`）。钉原文的过细门（`_over_specific`）
  **一律不沉淀** —— 它们本身就是假红生成器，沉淀等于把缺陷传下去。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from agentscope_orchestrator import profile as prof_mod

# ── 账本位置（随档案走，换工程不串味）────────────────────────────
_prof = prof_mod.get_profile()
AUDIT = _prof.audit_dir
AUDIT.mkdir(parents=True, exist_ok=True)
GATE_MEMORY = AUDIT / "gate_memory.jsonl"

# 复用门槛：同一结构对象被**独立确证**至少这么多次才允许复用。
#   ★ 依据：`develop.py` 的 `MIN_DENSITY = 2` 同口径 —— 一次命中可能是巧合，
#     两次才是结构。这是**经验门槛**，不是定理（库里无对应件，如实标注）。
最小命中 = 2


def _结构指纹(文本: str, 后缀: str = "") -> str:
    """产物的**结构指纹**（语言 + 声明关键字集），用作复用的 key。

    ★ 用结构而**不是**原文：原文指纹会让"换个写法"就命不中，
      那样沉淀毫无意义（本模块存在的理由就是跨写法复用）。
    """
    from agentscope_orchestrator.discriminant import _结构关键字
    关键字 = sorted(_结构关键字(文本))
    语言 = {".lean": "lean", ".rs": "rust", ".py": "python"}.get(
        后缀.lower(), "other")
    return f"{语言}|{'+'.join(关键字) if 关键字 else '无声明'}"


def _门指纹(门: dict) -> str:
    """门的**结构指纹**（类型 + 目标模式），不含具体路径与原文。"""
    类型 = 门.get("type", "?")
    if 类型 == "whole_reading":
        模式 = "+".join(门.get("must_contain") or [])
        return f"whole_reading:{门.get('mode','contains')}:{模式}"
    if 类型 == "grep_required":
        return "grep_required:" + "+".join(门.get("patterns") or [])
    if 类型 == "grep_forbidden":
        return f"grep_forbidden:{门.get('pattern','')}"
    if 类型 == "compile":
        return "compile:" + " ".join(门.get("cmd") or [])
    return 类型


def 沉淀(门: dict, 产物文本: str, 后缀: str, 目标指纹: str = "",
        证伪次数: int = 0) -> bool:
    """把**已被独立确证**的结构门沉淀成对象。返回是否沉淀。

    只收结构门（`_structural`）；过细门（`_over_specific`）一律拒收 ——
    它们是假红生成器，沉淀等于把缺陷传给下一个任务。
    """
    if not isinstance(门, dict):
        return False
    if 门.get("_over_specific"):
        return False                      # ★ 拒收假红生成器
    if not 门.get("_structural"):
        return False                      # 只收结构门（换写法仍有效）
    if not 门.get("_derived_from"):
        return False                      # 只收"派生出来的"（有溯源）
    结构 = _结构指纹(产物文本, 后缀)
    门纹 = _门指纹(门)
    key = f"{结构}::{门纹}"
    已有 = {r["key"]: r for r in 读账本()}
    rec = {"key": key, "结构": 结构, "门纹": 门纹,
           "门": {k: v for k, v in 门.items() if not k.startswith("_")},
           "目标指纹": 目标指纹,
           "命中": (已有[key].get("命中", 0) + 1) if key in 已有 else 1,
           "证伪": 证伪次数,
           "ts": time.time(), "时间": time.strftime("%Y-%m-%d %H:%M:%S")}
    with GATE_MEMORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def 读账本() -> list[dict]:
    """读沉淀账本（追加式，坏行跳过）。"""
    if not GATE_MEMORY.is_file():
        return []
    出: list[dict] = []
    for line in GATE_MEMORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            出.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return 出


def 折叠() -> dict[str, dict]:
    """按 key 折叠（后写覆盖前条，命中数取末次 —— 与 develop 账本同口径）。"""
    表: dict[str, dict] = {}
    for r in 读账本():
        k = r.get("key")
        if k:
            表[k] = r
    return 表


def 召回(产物: Path, 上限: int = 8) -> list[dict]:
    """按产物**结构指纹**召回可复用的门（**候选**，非免检）。

    ★★ 诚实：返回的是**候选提议**。调用方**必须**
      ① 真跑在新产物上验过  ② 过 `constraint.机检门集尊重约束`。
      理由：`goal_change_changes_constraint` 机证约束随目标变 ⇒
      为旧目标派生的门对新目标可能过细（假红）。
    """
    if not 产物.is_file():
        return []
    try:
        文本 = 产物.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    结构 = _结构指纹(文本, 产物.suffix)
    表 = 折叠()
    候选: list[dict] = []
    for rec in 表.values():
        if rec.get("结构") != 结构:
            continue
        if rec.get("命中", 0) < 最小命中:
            continue                      # 未达门槛：一次命中可能是巧合
        门 = dict(rec.get("门") or {})
        if not 门:
            continue
        门["_recalled"] = rec["key"]
        门["_hits"] = rec.get("命中", 0)
        # ★ 门必须跟随**被评判的产物**，不钉召回时的旧路径
        if "in" in 门:
            门["in"] = "{file}"
        候选.append(门)
        if len(候选) >= 上限:
            break
    return 候选


def 机检复用是否安全(候选门: list[dict], 产物: Path, work_dir: Path,
                     达标: dict | None = None, cwd_default: str = ".") -> dict:
    """★★ 复用前的**双重机检**：真跑有区分力 + 过约束（TS-378）。

    返回 {"安全": [...], "拒收": [...], "报告": [...]}。

    两条判据都**机器可算**，不靠模型：
      ① **真跑**：门在新产物上必须判**绿**（不能一上来就把合法产物判红）
      ② **约束**：过 `constraint.机检门集尊重约束`（目标变了得重验）
    """
    from agentscope_orchestrator import gate_spec
    安全: list[dict] = []
    拒收: list[dict] = []
    报告: list[dict] = []
    if not 产物.is_file():
        return {"安全": [], "拒收": [], "报告": []}
    for g in 候选门:
        跑 = {k: v for k, v in g.items() if not k.startswith("_")}
        名 = 跑.get("name") or 跑.get("type")
        try:
            结果, _ = gate_spec.evaluate([跑], 产物, work_dir, cwd_default)
        except Exception as exc:          # noqa: BLE001
            拒收.append(g)
            报告.append({"门": 名, "判": "拒收", "因": f"真跑异常：{exc}"})
            continue
        if not 结果.get(名):
            拒收.append(g)
            报告.append({"门": 名, "判": "拒收",
                         "因": "在新产物上判红 ⇒ 可能过细（假红）"})
            continue
        安全.append(g)
        报告.append({"门": 名, "判": "安全", "因": "新产物上判绿"})
    # ② 约束机检（有独立达标判据时才做；否则如实跳过）
    if 安全 and 达标:
        try:
            from agentscope_orchestrator import constraint as _cs
            样本 = [(str(产物), 产物)]
            rep = _cs.机检门集尊重约束(安全, 样本, work_dir, 达标, cwd_default)
            if rep.get("违反"):
                违反集 = set(rep["违反"])
                安全 = [g for g in 安全
                        if (g.get("name") or g.get("type")) not in 违反集]
                拒收 += [g for g in 候选门
                         if (g.get("name") or g.get("type")) in 违反集]
                报告.append({"门": "约束机检", "判": "拒收",
                             "因": f"违反目标约束（TS-378 假红）：{sorted(违反集)}"})
        except Exception as exc:          # noqa: BLE001
            报告.append({"门": "约束机检", "判": "跳过", "因": f"异常：{exc}"})
    return {"安全": 安全, "拒收": 拒收, "报告": 报告}


def _selftest() -> int:
    """自检：全部机器可算。"""
    import tempfile as _tf
    ok = total = 0

    def chk(name: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  ✗ {name}")

    global GATE_MEMORY
    原账本 = GATE_MEMORY
    d = Path(_tf.mkdtemp(prefix="gm_st_"))
    try:
        GATE_MEMORY = d / "gate_memory.jsonl"

        # ① 结构指纹：同语言同形态 ⇒ 同指纹（跨写法）
        a = "theorem f (n : Nat) : n = n := by\n  rfl\n"
        b = "theorem 另写 (m : Nat) : m = m := by\n  rfl\n"
        chk("结构指纹跨写法一致",
            _结构指纹(a, ".lean") == _结构指纹(b, ".lean"))
        chk("结构指纹区分语言",
            _结构指纹(a, ".lean") != _结构指纹(a, ".rs"))
        chk("无声明文本有独立指纹",
            "无声明" in _结构指纹("随便写点什么", ".lean"))

        # ② 门指纹不含路径与原文
        g1 = {"name": "x", "type": "whole_reading", "mode": "contains",
              "must_contain": ["theorem "], "in": "C:/a/b.lean"}
        g2 = dict(g1)
        g2.update({"name": "y", "in": "D:/完全不同/路径.lean"})
        chk("门指纹不含路径", _门指纹(g1) == _门指纹(g2))

        # ③ 沉淀只收结构门
        chk("拒收过细门",
            not 沉淀(dict(g1, _over_specific=True, _derived_from="z",
                          _structural=True), a, ".lean"))
        chk("拒收非结构门",
            not 沉淀(dict(g1, _derived_from="z"), a, ".lean"))
        chk("拒收无溯源门",
            not 沉淀(dict(g1, _structural=True), a, ".lean"))
        chk("接收结构门",
            沉淀(dict(g1, _derived_from="z", _structural=True), a, ".lean"))
        chk("沉淀后账本非空", len(读账本()) == 1)

        # ④ 命中门槛：只沉淀 1 次 ⇒ 未达 MIN_DENSITY=2 ⇒ 不召回
        p = d / "Artifact.lean"
        p.write_text(a, encoding="utf-8")
        chk("未达门槛不召回", len(召回(p)) == 0)

        # ⑤ 再沉淀一次（同 key）⇒ 命中=2 ⇒ 可召回
        沉淀(dict(g1, _derived_from="z", _structural=True), a, ".lean")
        chk("达门槛可召回", len(召回(p)) == 1)
        候选 = 召回(p)
        chk("召回门跟随被评判产物", 候选 and 候选[0].get("in") == "{file}")
        chk("召回门带命中数", 候选 and 候选[0].get("_hits") == 2)
        chk("召回门带溯源 key", 候选 and 候选[0].get("_recalled"))

        # ⑥ 折叠：同 key 后写覆盖（命中数取末次）
        表 = 折叠()
        chk("折叠同 key 为一条", len(表) == 1)
        chk("折叠取末次命中数", list(表.values())[0]["命中"] == 2)

        # ⑦ ★★ 双重机检：真跑判绿才安全
        rep = 机检复用是否安全(候选, p, d, None, ".")
        chk("机检：真跑绿 ⇒ 安全", len(rep["安全"]) == 1 and not rep["拒收"])
        chk("机检：有报告", len(rep["报告"]) == 1)

        # ⑧ ★★ 机检拦截：产物里没有结构关键字 ⇒ 门判红 ⇒ 拒收
        p空 = d / "空.lean"
        p空.write_text("", encoding="utf-8")
        rep2 = 机检复用是否安全(候选, p空, d, None, ".")
        chk("机检：新产物无关键字 ⇒ 拒收", len(rep2["拒收"]) == 1)
        chk("机检：拒收原因写明", "假红" in rep2["报告"][0]["因"])

        # ⑨ ★★ 约束机检拦截（TS-378）：目标变了门可能过细
        #    构造一个"对同类达标产物判不同档"的场景
        p另 = d / "另.lean"
        p另.write_text("def 另写 : Nat := 1\n", encoding="utf-8")
        过细候选 = [dict(g1, _derived_from="z", _structural=True,
                         name="must_theorem")]
        rep3 = 机检复用是否安全(过细候选, p另, d, None, ".")
        chk("机检：无关键字产物上判红被拒", len(rep3["拒收"]) == 1)

        # ⑩ 不同结构指纹不互相召回
        pRs = d / "x.rs"
        pRs.write_text("fn main() {}\n", encoding="utf-8")
        chk("不同语言不串味召回", len(召回(pRs)) == 0)
    finally:
        GATE_MEMORY = 原账本
        import shutil as _sh
        _sh.rmtree(d, ignore_errors=True)

    print(f"  跨任务门沉淀·召回 自检：{ok}/{total} 通过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(f"账本：{GATE_MEMORY}")
    print(f"对象数：{len(折叠())}")
