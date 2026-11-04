"""观测机制（observation）干测：读结构→取象→全称机制判定→求真值。

假库三源：
  ① 合规单元 —— 无任何机制违反标记 → 机制真值 True。
  ② 限轮/候选池单元 —— `const MAX_ROUNDS: u32 = 64;` 固定限轮常量 + `for _ in 0..MAX_ROUNDS`
     候选池循环 → 违反无界(Boundedness)。
  ③ 脚手架单元 —— `todo!()` 未演化 → 违反自演化(SelfEvolvingRule)。

断言：全称判定 = 3 单元、2 违反单元、1 clean；各违反真值 False；
机制矩阵含〈无界〉与〈自演化〉两类。取象能提取签名+依赖。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.index import UnitIndex
from agentscope_orchestrator import observation as O


PASS = 0


def check(cond, msg):
    global PASS
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    PASS += 1


FAKE = {
    "src/compliant.rs": """// 合规单元：显式自演化至不动点即停，无穷举上限
mod shape;
pub fn evolve_fixed_point(graph: &Hash) {
    // 结构演化收敛：不设轮数，不动点即停
    let next = Self::structure_next(graph);
    let _ = next;
}
use crate::core::shape;
""",
    "src/bounded.rs": """// 违反无界：固定限轮常量 + 候选池遍历上限
const MAX_ROUNDS: u32 = 64;
pub fn search(sample: &Sample) {
    for _ in 0..MAX_ROUNDS {
        // 候选池被圈定，未全称演化
    }
}
""",
    "src/scaffold.rs": """// 违反自演化：脚手架未演化到不动点
pub fn derive() {
    todo!()
}
impl Builder for Context {
    fn build(&self) {}
}
""",
}


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for rel, text in FAKE.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

        idx = UnitIndex()
        for rel in FAKE:
            p = root / rel
            idx.register(f"src.{Path(rel).stem}", p)   # 点式键（startswith 'src'）
            idx.register(str(p), p)

        # ── 读结构 → 取象 ─────────────────────────────
        obs = O.read_structure(idx, root, index_key="src")
        check(len(obs) == 3, f"读结构应含 3 单元，实得 {len(obs)}")

        by_name = {Path(o.rel).stem: o for o in obs}
        comp = by_name["compliant"]
        check("evolve_fixed_point" in comp.image.signatures,
              f"取象应提取签名 evolve_fixed_point，实得 {comp.image.signatures}")
        _has_dep = any("crate" in dep or "shape" in dep for dep in comp.image.deps)
        check(_has_dep, f"取象应提取依赖(crate/use)，实得 {comp.image.deps}")
        check(len(comp.image.markers) == 0,
              f"合规单元不应有机制标记，实得 {comp.image.markers}")

        bound = by_name["bounded"]
        mark_names = {m["name"] for m in bound.image.markers}
        check("固定限轮常量" in mark_names and "候选池循环" in mark_names,
              f"bounded 单元应命中固定限轮+候选池，实得 {mark_names}")

        scaff = by_name["scaffold"]
        check("脚手架未演化" in {m["name"] for m in scaff.image.markers},
              "scaffold 单元应命中脚手架未演化")

        # ── 全称机制判定 → 求真值 ──────────────────────
        v = O.universal_judge(obs)
        check(v.total_units == 3, f"total_units=3，实得 {v.total_units}")
        check(v.units_with_violations == 2, f"违反单元=2，实得 {v.units_with_violations}")
        check(v.clean_units == 1, f"clean=1，实得 {v.clean_units}")
        check(not v.is_clean, "含违反时 is_clean 应为 False")

        check(all(vi.truth is False for vi in v.violations),
              "所有违反真值必须为 False（未做出机制）")
        mechs = {vi.mechanism for vi in v.violations}
        check(O.MECH_BOUNDED_VIOLATION in mechs and O.MECH_EVOLVE_VIOLATION in mechs,
              f"机制矩阵应含〈无界〉与〈自演化〉，实得 {mechs}")
        check(len(v.violations) >= 3,
              f"违反数>=3（bounded×2+scaffold×1），实得 {len(v.violations)}")

        # 标记表是同一真相源
        check(len(O.marker_catalog()) == len(O._MARKERS),
              "marker_catalog 与内部标记表一致")

        # 纯合规库应 clean
        pure = O.universal_judge([by_name["compliant"]])
        check(pure.is_clean and pure.clean_units == 1,
              "纯合规单元应机制全 True（is_clean）")

    print(f"[observation] 全绿（PASS={PASS}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())