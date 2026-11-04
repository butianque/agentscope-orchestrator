"""结构记忆·按需增量观测干测：记忆检索联动 + 数据膨胀不重扫。

断言档：
  (a) 首次观测：全部实读(scan=1)，无记忆复用。
  (b) 同内容再观测：记忆命中复用(reuse>=1)，实读不增；全称判定结果一致(仍 clean)。
  (c) 源变更(注入机制违反)：实读刷新，不漏变更——观测检测出违反。
纯内存 temp，不触真 cargo / 不触 DB。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.index import UnitIndex
from agentscope_orchestrator import observation as obs

PASS = 0


def check(cond, msg):
    global PASS
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    PASS += 1


def build_tmp_index(tmp: Path, text: str, name: str = "a.rs") -> UnitIndex:
    idx = UnitIndex()
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    idx.register(f"src.{name}", p)
    return idx


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        compliant = "pub fn f(){}\n"
        mem = obs.StructureMemory(tmp / "mem.json")
        mem_path = tmp / "mem.json"

        # (a) 首次：全部实读
        v1 = obs.observe_rust(build_tmp_index(tmp, compliant), tmp, memory=mem)
        s1 = mem.stats()
        check(v1.total_units == 1 and v1.is_clean, "首次观测应 1 单元且 clean")
        check(s1["scanned_changed"] == 1 and s1["reused_unchanged"] == 0,
              f"首次应实读1、复用0，实得 {s1}")
        check(mem_path.exists(), "记忆应已落盘")

        # (b) 同内容再观测：记忆复用、实读不增、判定一致
        mem2 = obs.StructureMemory(mem_path)      # 载入持久记忆
        v2 = obs.observe_rust(build_tmp_index(tmp, compliant), tmp, memory=mem2)
        s2 = mem2.stats()
        check(v2.is_clean and v2.total_units == 1,
              "再观测判定应与首次一致(clean)")
        check(s2["scanned_changed"] == 0 and s2["reused_unchanged"] == 1,
              f"再观测应实读0、复用1(完全记忆命中)，实得 {s2}")

        # (c) 源变更注入违反：实读刷新，不漏变更。
        # 结构证据语义：常量须被用作圈界（for..MAX/..=MAX）才构成违反；
        # 仅声明未引用（如规律常量）不报。故坏样本 = 常量 + 真实 `for _ in 0..MAX_ROUNDS` 引用。
        bad = ("const MAX_ROUNDS: u64 = 120;\n"
               "pub fn g(){ for _ in 0..MAX_ROUNDS {} }\n")
        mem3 = obs.StructureMemory(mem_path)
        v3 = obs.observe_rust(build_tmp_index(tmp, bad, "b.rs"), tmp, memory=mem3)
        s3 = mem3.stats()
        check(not v3.is_clean and v3.units_with_violations == 1,
              "变更(含圈界引用)应被观测到违反(机制真值False)")
        check(s3["scanned_changed"] == 1 and s3["memory_units"] == 2,
              f"本轮仅实读变更文件、记忆累积2单元，实得 {s3}")

    print(f"[structure_memory] 全绿（PASS={PASS}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())