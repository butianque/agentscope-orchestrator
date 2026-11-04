"""G7 全自动引擎干测：扫描建索引 / 自动选题 / 模板生成 三要素（不烧真模型）。

覆盖：
  (a) 用临时假 MyMeta + 假代码 + 假任务书建统一记忆索引，验证三源计数与 key 可达
  (b) select_unclosed 检出含 sorry/admit 的定理 + 依赖缺口排序
  (c) gen_lean_candidate 生成模板：陈述逐字保留 + by sorry 占位 + 四条 GateSpec
  (d) 真实 MyMeta 上干跑不崩（不真正编译）
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator import autoengine as ae

FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global FAIL
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)
    if not cond:
        FAIL = 1


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        froot = root / "formal" / "MyMeta"
        (froot / "Engine").mkdir(parents=True)
        (froot / "Engine" / "Delta.lean").write_text("""
namespace Engine

theorem alpha_pos {n : Nat} : 0 < n := by
  sorry

theorem beta_closed {n : Nat} : n = n := rfl

lemma gamma_open (k : Nat) : k + 1 = k + 1 := by
  sorry

end Engine
""", encoding="utf-8")
        croot = root / "code"
        (croot / "worker").mkdir(parents=True)
        (croot / "worker" / "util.py").write_text("def f():\n    pass\n", encoding="utf-8")
        troot = root / "tasks"
        troot.mkdir(parents=True)
        (troot / "task_a.md").write_text("# 需证 extent 收敛", encoding="utf-8")

        mem = ae.scan_all(froot, [croot], [troot])
        sizes = mem.sizes()
        check("scan: 三源计数", sizes.get("formal", 0) >= 1
              and sizes.get("code", 0) >= 1 and sizes.get("task", 0) >= 1, str(sizes))
        check("scan: formal key 可达",
              mem.index("formal").query("MyMeta.Engine.Delta") is not None)
        check("scan: code key 可达",
              mem.index("code").query("worker.util") is not None)
        check("scan: task key 可达",
              mem.index("task").query("task_a") is not None)

        cands = ae.select_unclosed(mem, froot)
        names = [c.theorem for c in cands]
        check("select: 检出 sorry 定理", "alpha_pos" in names and "gamma_open" in names)
        check("select: 跳过已闭合", "beta_closed" not in names, str(names))
        check("select: 依赖缺口排序不崩", len(cands) >= 2)

        c0 = next(c for c in cands if c.theorem == "alpha_pos")
        gen = ae.gen_lean_candidate(c0, froot)
        tpl = gen["template"]
        check("gen: 陈述逐字保留", "theorem alpha_pos {n : Nat} : 0 < n" in tpl)
        check("gen: 含 by sorry 占位", "sorry" in tpl)
        check("gen: import 引线生成",
              "import MyMeta.Engine.Delta" in tpl, tpl[:80])
        check("gen: 四条 GateSpec", len(gen["gates"]) == 4)
        check("gen: statement_marker 非空", bool(gen["statement_marker"]))

    # (d) 真实产物库干跑不崩（只扫不编译）；库路径来自档案，工具不持有项目知识
    rt = __import__("agentscope_orchestrator.real_task", fromlist=["ARTIFACT_DIR"])
    _art = rt.ARTIFACT_DIR
    if _art is None:
        print("[info] 档案未声明 artifact.root，跳过真实库干跑", flush=True)
    else:
        mem2 = ae.scan_all(_art, [Path(__file__).resolve().parents[1] / "src"])
        sizes2 = mem2.sizes()
        check("real artifact: 扫描不崩", sizes2.get("formal", 0) > 0, str(sizes2))
        cands2 = ae.select_unclosed(mem2, _art, limit=10)
        print(f"[info] 真实库未闭合候选 {len(cands2)} 个", flush=True)
        for c in cands2[:5]:
            print(f"[info]   {c.unit_id} :: {c.theorem} deps={len(c.deps)}",
                  flush=True)

    print("[g7_engine] ALL PASS" if FAIL == 0 else "[g7_engine] FAILED", flush=True)
    return FAIL


if __name__ == "__main__":
    raise SystemExit(main())