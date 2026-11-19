# -*- coding: utf-8 -*-
"""平台拒绝探针（N2 · 判据 #30）。

判三件事（缺一不可，都是"可判"的）：
  ① 模拟 `sys.platform='linux'` ⇒ `doctor.main()` **恰好返回 67**（不是 0，也不是随便非 0：
     要和 64 python / 65 profile / 66 channel 分开，否则下游分不清是哪类阻塞）；
  ② 输出里**必须说清原因**（含"未验"/"不承诺"/平台名），不许只给个码；
  ③ **正控**：本机真实平台（win32）跑 `doctor.main()` 走正常路径，**不许返回 67**
     —— 没有正控的负控等于"我把功能关掉了"。

输出末尾固定 `判定=OK` / `判定=回归`。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
包根 = pathlib.Path(__file__).resolve().parents[1]
源路径 = str(包根 / "src")

失败: list[str] = []


def 判(条件: bool, 说明: str) -> None:
    print(("   ok  " if 条件 else "   ✗   ") + 说明)
    if not 条件:
        失败.append(说明)


def 跑(平台: str | None) -> tuple[int, str]:
    码 = (
        "import sys\n"
        f"sys.path.insert(0, {源路径!r})\n"
        + (f"sys.platform = {平台!r}\n" if 平台 else "")
        + "from agentscope_orchestrator import doctor\n"
        "sys.exit(doctor.main())\n"
    )
    cp = subprocess.run([sys.executable, "-B", "-c", 码], capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=900)
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def main() -> int:
    print(f"   本机 platform = {sys.platform!r}")

    # ① ② 模拟 linux
    码, 出 = 跑("linux")
    判(码 == 67, f"模拟 linux ⇒ 恰好 67（实得 {码}）")
    判(("未验" in 出) or ("不承诺" in 出), "输出说清「本平台未验」")
    判("linux" in 出, "输出里带上了平台名（可定位）")
    判("静默" not in 出.replace("不做静默降级", ""),
       "没有'静默往下走'（真拒绝，不是打个招呼继续）")

    # ③ 正控：真实平台不许 67
    码2, 出2 = 跑(None)
    判(码2 != 67, f"正控：本机真实平台**不许**返回 67（实得 {码2}）")

    print(f"判定={'OK' if not 失败 else '回归'}")
    for x in 失败:
        print(f"   ⇒ {x}")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(main())
