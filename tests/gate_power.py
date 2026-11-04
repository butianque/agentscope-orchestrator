"""门区分力度量回归（2026-11-01：给"过门 ≠ 改得对"一个**数字**）。

★ 为什么值得一条门：`orch_fix` 的全部结论建立在"门绿"上，而门绿只证明"这一条判据没意见"。
  0.12.5 真事故：模型把 `_RUST_ROOT` 改成 `parents[2]`，门（只要求"已定义 + 真目录"）绿了。
  这类事**不可彻底消灭**（语义等价性不可判），但可以**度量**：
  往产物里注机械性错误，看门抓住几种 ⇒ 存活清单就是"这个门的洞在哪"。

★ 本测试**用一个刻意弱门和一个够强的门**验证度量本身有没有区分力：
  ① 弱门（只要退出码 0）⇒ 应有变异存活、区分力 < 1，并点名存活项；
  ② 强门（`unittest` 真跑断言）⇒ 变异应被抓住，区分力 = 1；
  ③ 基线红时不硬测（"先让门在正确产物上变绿"）；
  ④ 不动调用方的仓（跑完仓内容逐字节不变）。

跑法：python tests/gate_power.py      退出码 0 = 全绿
"""
from __future__ import annotations

import asyncio
import hashlib
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))

from agentscope_orchestrator import gate_power as gp  # noqa: E402

失败: list[str] = []
条数 = 0


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    global 条数
    条数 += 1
    if not 条件:
        失败.append(f"{名}：{说明}")


def _建(p: Path) -> None:
    """一个"实现 + 测试"的最小工程：实现真被测试约束。"""
    (p / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\n"
        "def is_positive(n):\n    return n > 0\n", encoding="utf-8")
    (p / "test_calc.py").write_text(
        "import unittest\n\nimport calc\n\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n\n"
        "    def test_pos(self):\n        self.assertTrue(calc.is_positive(1))\n"
        "        self.assertFalse(calc.is_positive(0))\n",
        encoding="utf-8")
    # ★ 夹具用 `unittest.TestCase`，不是裸 `test_*()` 函数 —— unittest 只收集 TestCase，
    #   裸函数会让"跑 0 个测试"以退出码 5 报红（实测 `Ran 0 tests` / `NO TESTS RAN`）。


def _指纹目录(p: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(p)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def main() -> int:
    顶 = Path(tempfile.mkdtemp(prefix="gpw_"))
    atexit.register(shutil.rmtree, 顶, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    try:
        _建(顶)
        前 = _指纹目录(顶)

        # ① 弱门：`python -c pass` 永远绿 ⇒ 所有变异都存活
        弱 = asyncio.run(gp.测区分力(顶, [sys.executable, "-c", "pass"], 变异数=3))
        判("① 弱门测得出来", 弱.get("ok") is True, f"返回={弱.get('error')}")
        判("① 弱门区分力 < 1", (弱.get("区分力") or 0) < 1, f"区分力={弱.get('区分力')}")
        判("① 弱门点名存活项", len(弱.get("存活") or []) >= 1, f"存活={弱.get('存活')}")
        判("① 弱门注入数正确", 弱.get("注入") == 3, f"注入={弱.get('注入')}")

        # ② 强门：unittest 真跑断言 ⇒ 变异应被抓住
        强 = asyncio.run(gp.测区分力(
            顶, [sys.executable, "-m", "unittest", "-q", "test_calc"], 变异数=3))
        判("② 强门测得出来", 强.get("ok") is True, f"返回={强.get('error')}")
        判("② 强门区分力 = 1", 强.get("区分力") == 1, f"明细={强.get('变异明细')}")
        判("② 强门无存活", (强.get("存活") or []) == [], f"存活={强.get('存活')}")

        # ③ 基线红 ⇒ 不硬测，直接给"先让门变绿"
        坏 = asyncio.run(gp.测区分力(顶, [sys.executable, "-c", "raise SystemExit(3)"],
                                    变异数=1))
        判("③ 基线红被拦下", 坏.get("ok") is False and "基线" in str(坏.get("error")),
          f"返回={坏}")
        判("③ 基线红归因=参数前提", 坏.get("分类") == "参数/前提不成立", f"分类={坏.get('分类')}")

        # ⑤ 规模护栏：仓太大时**快速拒**并给准确做法（不许慢慢挂住）
        import os as _os
        旧上限 = _os.environ.get("ORCH_GATE_POWER_MAX_FILES")
        _os.environ["ORCH_GATE_POWER_MAX_FILES"] = "1"
        try:
            大 = asyncio.run(gp.测区分力(顶, [sys.executable, "-c", "pass"], 变异数=1))
        finally:
            if 旧上限 is None:
                _os.environ.pop("ORCH_GATE_POWER_MAX_FILES", None)
            else:
                _os.environ["ORCH_GATE_POWER_MAX_FILES"] = 旧上限
        判("⑤ 大仓快速拒", 大.get("ok") is False and "仓太大" in str(大.get("error")),
          f"返回={大.get('error')}")
        判("⑤ 拒绝时给子工程做法", "子工程" in str((大.get("修法") or {}).get("参数")),
          f"修法={大.get('修法')}")
        判("⑤ 拒绝归因=参数前提", 大.get("分类") == "参数/前提不成立", f"分类={大.get('分类')}")

        # ⑥ 副本必须放在**仓外**（自查事故：临时目录落在仓里 ⇒ 递归自我复制 14 层）
        临 = (强.get("临时目录") or {})
        判("⑥ 返回临时目录来源", bool(临.get("基")), f"临时目录={临}")
        判("⑥ 临时目录不在仓内",
          bool(临.get("基")) and not Path(临["基"]).resolve().is_relative_to(顶.resolve()),
          f"临时目录={临.get('基')} 仓={顶}")

        # ④ 不动调用方的仓
        判("④ 仓内容逐字节未变", _指纹目录(顶) == 前, "度量过程改动了调用方的仓")
        判("④ 返回带边界声明", "≠ 改得对" in str(强.get("边界") or ""), f"边界={强.get('边界')}")
    finally:
        shutil.rmtree(顶, ignore_errors=True)

    if 失败:
        print(f"gate_power：{len(失败)}/{条数} 红")
        for f in 失败:
            print(f"  [FAIL] {f}")
        return 1
    print(f"gate_power：{条数}/{条数} 全绿（弱门有洞 / 强门全抓 / 基线红不硬测 / 不动调用方的仓）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


