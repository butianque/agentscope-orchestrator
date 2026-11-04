#!/usr/bin/env python
"""门白名单的"识别谁"回归测（tests/lean_whitelist.py）

两处真缺陷（2026-11-01，本工程在真 Lean 工程上自证时抓到）：

① 自动识别表里**没有 Lean**：本库自带 Lean 原生判据门（`kernel_statement` /
   `axiom_footprint`），却在自己的招牌场景上被自己的沙箱拒 ——
   `命令安全闸拒绝：门#0.cmd 顶层命令不被白名单允许：'lake'`。
② 识别**只看档案根**：`orch_run(repo=<另一个工程>)` 时扫的是档案那个工程 ——
   判据读错了对象。上面的 ① 之所以一直没人发现，是因为 ② 让它在本工程里
   恰好用不上（本工程是 Python 工程）。

这个文件把两件事都钉住：Lean 清单要能认出来；识别必须跟着**被判定工程的根**走。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator import gate_derive as gd  # noqa: E402

通过: list[str] = []
失败: list[str] = []


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}" + (f"：{说明}" if 说明 else ""))


def main() -> int:
    print("lean_whitelist：Lean 清单识别 + 识别跟着被判定根走")

    with tempfile.TemporaryDirectory(prefix="leanwl_") as 桶:
        项 = Path(桶) / "proj"
        项.mkdir()
        (项 / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
        (项 / "lean-toolchain").write_text("leanprover/lean4:v4.27.0\n", encoding="utf-8")

        工具 = gd._detected_build_tools((项,))
        判("① Lean 工程认出 lake", "lake" in 工具, f"识别={sorted(工具)}")
        判("① Lean 工程认出 lean/elan",
          {"lean", "elan"} <= 工具, f"识别={sorted(工具)}")
        判("① 白名单含 lake", "lake" in gd._cmd_whitelist((项,)))

        # 沙箱真的放行（这才是"能不能跑自己的门"的判据）
        门 = [{"name": "lean", "type": "compile", "cmd": ["lake", "env", "lean", "x.lean"],
              "cwd": str(项)}]
        ok, 违 = gd.check_cmd_safety(门, 项, 项)
        判("② 沙箱放行 lake 门", ok, f"违规={违}")

        # 反向：不在白名单的可执行照样被拒（强度没降）
        坏 = [{"name": "bad", "type": "compile", "cmd": ["definitely_not_allowed", "x"],
              "cwd": str(项)}]
        ok2, 违2 = gd.check_cmd_safety(坏, 项, 项)
        判("② 非白名单可执行仍被拒", (not ok2) and bool(违2), f"违规={违2}")

        # ③ 识别跟着**被判定根**走：造一个只有被判定根才有的清单
        原表 = gd._MANIFEST_TOOLS
        try:
            gd._MANIFEST_TOOLS = tuple(原表) + (("MyFake.MANIFEST", ("mytool_zzz",)),)
            (项 / "MyFake.MANIFEST").write_text("x\n", encoding="utf-8")
            命中 = gd._detected_build_tools((项,))
            判("③ 只存在于被判定根的清单被认出来", "mytool_zzz" in 命中,
              f"识别={sorted(命中)}")
            牙 = gd._detected_build_tools(())      # 不给根 ⇒ 只扫档案根
            判("③ 不给根时不会凭空认出该工具", "mytool_zzz" not in 牙,
              "（若档案根恰有同名清单，这条会红——那是环境巧合，不是缺陷）")
        finally:
            gd._MANIFEST_TOOLS = 原表

    print()
    if 失败:
        print(f"lean_whitelist：{len(失败)} 项红 —— {失败}")
        return 1
    print(f"lean_whitelist：{len(通过)}/{len(通过)} 全绿（Lean 可判 · 沙箱未放松 · 识别跟被判定根）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
