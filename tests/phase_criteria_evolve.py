"""判据演化（meta-loop）干测：真值→对象→同模式机械复用，受独立真值锚约束。

三断言档：
  (a) 沉淀：机制缺口被门确证的修正(O→N)记入账本，同 key 复用命中 hits 递增。
  (b) 复用：对象在账本且主线此刻逐字唯一命中 → evolved_reuse 返回新全文。
  (c) 独立真值锚：①主线已变/OLD 不再唯一 → 拒绝复用（不放松门）；
                  ②非 observation 缺口不沉淀（防机制对象被无关修正污染）。
纯内存临时账本，不触真 cargo / 不触 DB。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agentscope_orchestrator.develop as D

PASS = 0


def check(cond, msg):
    global PASS
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    PASS += 1


def mk_gap(tmp, rel, code, kind="observation", line=3):
    return D.Gap(file=Path(rel), abs_path=tmp / rel, line=line, col=1,
                 code=code, message="机制违反", kind=kind)


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        D.EVOLVED = tmp / "evolved.jsonl"   # 重定向账本到临时目录，不污染真实审计
        (tmp / "a.rs").write_text("const MAX_R: u32 = 8;\npub fn a(){}\n",
                                  encoding="utf-8")

        # (a) 沉淀新对象
        stored_old = "const MAX_R: u32 = 8;"
        stored_new = "const MAX_R: u32 = 8; // replaced-by-evolution"
        gap = mk_gap(tmp, "a.rs", "固定限轮常量")
        D.sediment_evolved(gap, [(stored_old, stored_new)])
        table = D.load_evolved()
        check(gap.key in table and table[gap.key]["hits"] == 1,
              f"沉淀后应存在 key={gap.key} hits=1，实得 {list(table)}")

        # (阈) 密度未达不涌现：单次确证(hits=1)应拒复用（意图非单次规则，需高密度）
        (tmp / "a.rs").write_text(stored_old + "\npub fn a(){}\n", encoding="utf-8")
        _, hit0 = D.evolved_reuse(gap)
        check(not hit0, f"hits=1<MIN_DENSITY 时应拒复用（意图未涌现），实得 hit0={hit0}")

        # (b) 密度达标后涌现实：结构一致≥MIN_DENSITY → 复用命中
        D.sediment_evolved(gap, [])                 # hits→2
        newtxt, hit = D.evolved_reuse(gap)
        check(hit and stored_new in newtxt, f"密度达标后应复用命中，实得 hit={hit}")
        check(D.load_evolved()[gap.key]["hits"] == 2, "已呈现一致密度 2，时机才到")

        # (b2) 复用成功后 hits 递增（密度继续上升=更稳固），且复用本身再沉淀
        _ = D.evolved_reuse(gap)
        D.sediment_evolved(gap, [])
        check(D.load_evolved()[gap.key]["hits"] == 3,
              "复用命中 hits 应递增到 3")

        # (c) 独立真值锚①：OLD 不再唯一 → 拒绝复用（不冒然改多义处）
        (tmp / "a.rs").write_text(stored_old + "\n" + stored_old + "\n",
                                  encoding="utf-8")
        _, hit2 = D.evolved_reuse(gap)
        check(not hit2, "OLD 重复出现时应拒绝复用（真值锚不接受不唯一改写）")

        # (c) 独立真值锚②：非 observation 缺口不沉淀（防污染机制对象表）
        cgap = mk_gap(tmp, "a.rs", "E0433", kind="compile")
        before = len(D.load_evolved())
        D.sediment_evolved(cgap, [(stored_old, stored_new)])
        check(len(D.load_evolved()) == before,
              "compile 缺口不应沉淀进判据演化账本")

    print(f"[criteria_evolve] 全绿（PASS={PASS}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())