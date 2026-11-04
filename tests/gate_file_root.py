#!/usr/bin/env python
"""门读文件按哪个根解析（tests/gate_file_root.py）

真缺陷（2026-11-01 自证时抓到）：同一条门集里**两把尺子** ——
`cmd` 按被判定工程跑（`cwd_default`），而 `in`/`src` 相对路径**一律**按
编排器自己的 `work_dir` 解析。于是：

    orch_run(repo=<Lean 工程>, gates=[{"in": "MyMeta/OrchJudge.lean", ...}])
    ⇒ 读 `<包>/work/MyMeta/OrchJudge.lean` ⇒ 读不到 ⇒ 门红

而它**没有症状的那一面**更危险：如果 `work_dir` 里恰好有同名文件，
门就会**静默读错的那一份**并给出绿 —— 判据读错了东西，比判据缺失更坏。

这个文件钉三件事：
① 给了文件根，相对 `in` 按文件根解析；
② 不给文件根，行为与旧版一致（按 `work_dir`，不偷偷多试一个根）；
③ 两个根下都有同名文件时，**读的是文件根那一份**（不许静默串味）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.gate_spec import evaluate  # noqa: E402

通过: list[str] = []
失败: list[str] = []


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}" + (f"：{说明}" if 说明 else ""))


def main() -> int:
    print("gate_file_root：门读文件按被判定工程解析（一门一尺）")
    with tempfile.TemporaryDirectory(prefix="fr_work_") as 工桶, \
         tempfile.TemporaryDirectory(prefix="fr_repo_") as 仓桶:
        工, 仓 = Path(工桶), Path(仓桶)
        (仓 / "MyMeta").mkdir()
        (仓 / "MyMeta" / "Spec.lean").write_text("theorem ok : True := trivial\n",
                                                 encoding="utf-8")
        # work_dir 下放一个**同名但含禁词**的文件：用来抓"静默读错一份"
        (工 / "MyMeta").mkdir()
        (工 / "MyMeta" / "Spec.lean").write_text("theorem bad : True := by sorry\n",
                                                 encoding="utf-8")

        门 = [{"name": "no_sorry", "type": "grep_forbidden", "in": "MyMeta/Spec.lean",
              "pattern": r"\bsorry\b"}]
        产物 = 工 / "_cand.out"
        产物.write_text("", encoding="utf-8")

        # ① 有文件根 ⇒ 读仓里那份（干净）⇒ 绿
        r1, a1 = evaluate(门, 产物, 工, str(仓), 仓)
        判("① 给文件根时按仓解析（仓内无 sorry ⇒ 绿）", r1.get("no_sorry") is True,
          f"审计={a1}")

        # ② 不给文件根 ⇒ 旧行为：读 work_dir 那份（含 sorry）⇒ 红（且不偷偷回退到仓）
        r2, a2 = evaluate(门, 产物, 工, str(仓))
        判("② 不给文件根时按 work_dir 解析（不偷偷换根）", r2.get("no_sorry") is False,
          f"因由={[x.get('cause') for x in a2]}")

        # ③ 两个根都有同名文件时，文件根必须压倒 work_dir（不许串味）
        (仓 / "MyMeta" / "Spec2.lean").write_text("theorem ok2 : True := trivial\n",
                                                  encoding="utf-8")
        门2 = [{"name": "req", "type": "grep_required", "in": "MyMeta/Spec2.lean",
               "pattern": "theorem ok2"}]
        r3, _ = evaluate(门2, 产物, 工, str(仓), 仓)
        判("③ 仓里独有的文件在文件根下可读", r3.get("req") is True)

        # ④ 绝对路径不受文件根影响（调用方显式指定就是显式指定）
        绝 = 仓 / "MyMeta" / "Spec.lean"
        门3 = [{"name": "abs", "type": "grep_forbidden", "in": str(绝),
               "pattern": r"\bsorry\b"}]
        r4, _ = evaluate(门3, 产物, 工, str(仓), 工)   # 故意把文件根指到 work_dir
        判("④ 绝对路径不被文件根改写", r4.get("abs") is True)

    print()
    if 失败:
        print(f"gate_file_root：{len(失败)} 项红 —— {失败}")
        return 1
    print(f"gate_file_root：{len(通过)}/{len(通过)} 全绿（文件根生效 · 旧行为不变 · 不串味）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
