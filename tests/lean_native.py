"""Lean 形式化路径回归（2026-11-01 补：此前"本轮未回归"如实写在交付说明里）。

★ 为什么值得单独一条：编排器的主线是"模型产收口件 → 机器判据门 → 收敛裁决"，
  Lean 那四条原生门（shell_ok / no_forbidden / statement_intact / no_sorry_axiom）
  就是这条线上的**唯一裁决者**。它们不动，别的都白说；它们退化，一条也发现不了 ——
  因为退化形态是"**绿**"（假绿比红危险得多）。

★ 判据形状（只认行为）：
  ① 真证明 ⇒ 四门全绿；
  ② `by sorry` ⇒ no_forbidden 红 **且** no_sorry_axiom 红（两条独立读法都要抓住）；
  ③ 把陈述写在**注释**里、正文写恒真命题 ⇒ statement_intact 红
     —— 这一条专门守 2026-10-13 那个"注释版骗过文本门"的坑：
     它证明门读的是**内核展开后的类型**，不是文件里的字符。

环境：需要 `lean` + `lake`（本机 elan 已装 v4.27.0）。
  没有 lean 的机器上会**如实判红并说明缺什么**（不静默跳过 —— 跳过 = 假绿）。
  只想跳过：`ORCH_TESTS_NO_LEAN=1`（终归要有人真跑过一次才算数）。

跑法：python tests/lean_native.py      退出码 0 = 全绿
"""
from __future__ import annotations

import os
import shutil
import subprocess
import atexit
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))

from agentscope_orchestrator import gate_spec as gs  # noqa: E402

失败: list[str] = []
条数 = 0


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    global 条数
    条数 += 1
    if not 条件:
        失败.append(f"{名}：{说明}")


def _有工具() -> bool:
    return bool(shutil.which("lean") and shutil.which("lake"))


def _建工程(顶: Path) -> None:
    (顶 / "lean-toolchain").write_text("leanprover/lean4:v4.27.0", encoding="utf-8")
    (顶 / "lakefile.toml").write_text('name = "probe"\ndefaultTargets = ["Probe"]\n',
                                     encoding="utf-8")
    (顶 / "Probe").mkdir()


def _四门(模块: str, 定理: str, 陈述: str, 工作目录: Path,
         限定名: bool = False) -> list[dict]:
    """与 `orchestrator.profile.yml` 的 lean_gates 同形。

    ★ 注意 `cwd` 不用 `{work}` 占位：评估器只替换 `cmd`/`in`/`src` 里的占位符
      （`_sub`），**不替换 `cwd`** —— 实测把 `cwd` 写成 `"{work}"` 会得到
      `命令无法启动`（目录不存在），那是写门的人踩的坑，不是门的缺陷。

    ★ `限定名=True` 用于复现 2026-11-01 抓到的**真缺陷**：探针写 `{module}.{theorem}`
      在 Lean 4 下必然 `unknownIdentifier`（模块名不是命名空间），
      旧模板就是这么写的 ⇒ 内核陈述门对**一切**真实产物恒红。
    """
    工 = str(工作目录)
    名 = f"{模块}.{定理}" if 限定名 else 定理
    return [
        {"name": "shell_ok", "type": "compile", "cmd": ["lake", "env", "lean", "{file}"],
         "cwd": 工, "expect_exit": 0, "timeout": 180},
        {"name": "no_forbidden", "type": "grep_forbidden", "pattern": r"\b(sorry|admit)\b",
         "in": "{file}"},
        {"name": "statement_intact", "type": "kernel_statement", "src": "{file}",
         "append": f"\n#check {名}\n", "cmd": ["lake", "env", "lean", "{file}"],
         "cwd": 工, "expect_exit": 0, "timeout": 180, "must_contain": [陈述]},
        {"name": "no_sorry_axiom", "type": "axiom_footprint", "src": "{file}",
         "append": f"\n#print axioms {名}\n",
         "cmd": ["lake", "env", "lean", "{file}"], "cwd": 工,
         "expect_exit": 0, "timeout": 180},
    ]


def main() -> int:
    if not _有工具():
        if os.environ.get("ORCH_TESTS_NO_LEAN", "").strip().lower() in ("1", "true", "yes", "on"):
            print("lean_native：跳过（ORCH_TESTS_NO_LEAN=1，环境无 lean/lake）")
            return 0
        print("lean_native：红 —— 环境里没有 lean/lake，Lean 判据门**无法实测**"
              "（要跳过请显式设 ORCH_TESTS_NO_LEAN=1）")
        return 1

    顶 = Path(tempfile.mkdtemp(prefix="leanprobe_"))
    atexit.register(shutil.rmtree, 顶, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    try:
        _建工程(顶)
        产物 = 顶 / "Probe" / "Basic.lean"
        # 先让 lake 建好 manifest（首跑要写清单，计时的不是判据）
        subprocess.run(["lake", "env", "lean", "--version"], cwd=str(顶),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)

        # ① 真证明 ⇒ 四门全绿
        产物.write_text(
            "theorem add_comm_probe (a b : Nat) : a + b = b + a := Nat.add_comm a b\n",
            encoding="utf-8")
        因由, 结果, _ = gs.evaluate_lossless(
            _四门("Probe.Basic", "add_comm_probe", "a + b = b + a", 顶), 产物, 顶, str(顶))
        判("① 真证明四门全绿", all(结果.values()), f"因由={因由}")
        判("① 陈述读到内核类型", 结果.get("statement_intact") is True, f"因由={因由}")
        判("① 公理足迹在白名单内", 结果.get("no_sorry_axiom") is True, f"因由={因由}")

        # ② by sorry ⇒ 两条独立读法都红
        产物.write_text("theorem add_comm_probe (a b : Nat) : a + b = b + a := by sorry\n",
                       encoding="utf-8")
        因由, 结果, _ = gs.evaluate_lossless(
            _四门("Probe.Basic", "add_comm_probe", "a + b = b + a", 顶), 产物, 顶, str(顶))
        判("② sorry 被文本门抓", 结果.get("no_forbidden") is False, f"因由={因由}")
        判("② sorry 被公理门抓", 结果.get("no_sorry_axiom") is False, f"因由={因由}")
        判("② 公理因由点名 sorryAx",
          "sorryAx" in str(因由.get("no_sorry_axiom", "")), f"因由={因由.get('no_sorry_axiom')}")

        # ③ 陈述写注释、正文恒真 ⇒ 内核门必须红（文本门看不出来）
        产物.write_text(
            "-- theorem add_comm_probe (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
            "theorem add_comm_probe (a b : Nat) : True := by trivial\n",
            encoding="utf-8")
        因由, 结果, _ = gs.evaluate_lossless(
            _四门("Probe.Basic", "add_comm_probe", "a + b = b + a", 顶), 产物, 顶, str(顶))
        判("③ 注释版过编译", 结果.get("shell_ok") is True, f"因由={因由}")
        判("③ 注释版过禁词门", 结果.get("no_forbidden") is True, f"因由={因由}")
        判("③ 内核陈述门判红", 结果.get("statement_intact") is False, f"因由={因由}")
        判("③ 因由=内核陈述缺失",
          str(因由.get("statement_intact", "")).startswith("内核陈述缺失|"),
          f"因由={因由.get('statement_intact')}")

        # ④ 探针用**限定名**、产物**没声明 namespace** ⇒ 门必须**自动退回**后判绿
        #    （2026-11-01 真跑抓到的形态：限定名只在产物自己 `namespace` 时解析得了；
        #      两种写法各覆盖一半情形 ⇒ 门必须自己退回，否则"完全正确的产物"恒红）
        产物.write_text(
            "theorem add_comm_probe (a b : Nat) : a + b = b + a := Nat.add_comm a b\n",
            encoding="utf-8")
        因由, 结果, 审计 = gs.evaluate_lossless(
            _四门("Probe.Basic", "add_comm_probe", "a + b = b + a", 顶, 限定名=True),
            产物, 顶, str(顶))
        判("④ 限定名在裸产物上也能判绿", 结果.get("statement_intact") is True, f"因由={因由}")
        判("④ 退回被记进审计",
          any(a.get("gate") == "statement_intact" and a.get("probe_fallback") is True
              for a in (审计 or [])), f"审计={审计}")

        # ⑤ 产物自己声明 namespace ⇒ 限定名**一次就中**（不该退回，也不该红）
        产物.write_text(
            "namespace Probe.Basic\n"
            "theorem add_comm_probe (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
            "end Probe.Basic\n",
            encoding="utf-8")
        因由, 结果, 审计 = gs.evaluate_lossless(
            _四门("Probe.Basic", "add_comm_probe", "a + b = b + a", 顶, 限定名=True),
            产物, 顶, str(顶))
        判("⑤ 带 namespace 的产物判绿", 结果.get("statement_intact") is True, f"因由={因由}")
        判("⑤ 带 namespace 时不需退回",
          all(a.get("probe_fallback") in (False, None)
              for a in (审计 or []) if a.get("gate") in ("statement_intact", "no_sorry_axiom")),
          f"审计={审计}")

        # ⑥ 声明根本不存在 ⇒ 两种写法都解析不了 ⇒ 仍须判红（退回不是放水）
        产物.write_text(
            "theorem other_name (a b : Nat) : a + b = b + a := Nat.add_comm a b\n",
            encoding="utf-8")
        因由, 结果, 审计 = gs.evaluate_lossless(
            _四门("Probe.Basic", "add_comm_probe", "a + b = b + a", 顶, 限定名=True),
            产物, 顶, str(顶))
        判("⑥ 声明不存在仍判红", 结果.get("statement_intact") is False, f"因由={因由}")
        判("⑥ 因由=读法无法执行",
          str(因由.get("statement_intact", "")).startswith("内核读法无法执行|"),
          f"因由={因由.get('statement_intact')}")
    finally:
        shutil.rmtree(顶, ignore_errors=True)

    if 失败:
        print(f"lean_native：{len(失败)}/{条数} 红")
        for f in 失败:
            print(f"  [FAIL] {f}")
        return 1
    print(f"lean_native：{条数}/{条数} 全绿（真证明 / sorry / 注释版骗门 / 限定名退回 / namespace 直中 / 声明缺失 六档）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



