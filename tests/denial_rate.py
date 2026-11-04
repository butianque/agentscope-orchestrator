#!/usr/bin/env python
"""常驻指标：**无效拒绝率** 与 **漏放率**（tests/denial_rate.py）

这两个数决定「沙箱烦不烦」，而不是「有没有沙箱」。

无效拒绝率 = 本该允许却被拒 / 合法请求总数
  · 它高的产品，AI 就会学会一句万能借口「沙箱环境限制」。本产品真摔过两次：
    ① `powershell -Command …`（Windows 上唯一能跑的 shell）被判非白名单；
    ② `lake env lean …`（Lean 官方入口）被判非白名单 —— 而本库**自带** Lean 判据门。
    两次都是 100%。

漏放率 = 本该拒绝却被放行 / 非法请求总数
  · 它高的产品，围栏等于装饰。把它与上面**同时**报，才防"为降误伤而松到底"。

门槛：无效拒绝率 ≤ 5% 且 漏放率 = 0。任一不达标就红（红在测试里，不红在文档里）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.gate_derive import (  # noqa: E402
    _cmd_whitelist, check_cmd_safety)

无效拒绝门槛 = 0.05
漏放门槛 = 0.0

通过: list[str] = []
失败: list[str] = []


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}" + (f"：{说明}" if 说明 else ""))


def 合法样例(仓: Path, 工作: Path, lean: Path) -> list[tuple[str, dict, Path, Path]]:
    """(说明, 门, 工作区根, 主树根)。每一条都是**真实会用到**的写法。"""
    exe = Path(sys.executable).resolve()
    return [
        ("python -c（最小）",
         {"type": "compile", "cmd": ["python", "-c", "print(1)"]}, 仓, 仓),
        ("绝对路径的 venv 解释器（最常用、最正当）",
         {"type": "compile", "cmd": [str(exe), "-m", "pytest", "-q"]}, 仓, 仓),
        ("python -m unittest",
         {"type": "compile", "cmd": ["python", "-m", "unittest"]}, 仓, 仓),
        ("pytest 直呼",
         {"type": "compile", "cmd": ["pytest", "-q"]}, 仓, 仓),
        ("cargo test --lib（Rust 工程自带工具）",
         {"type": "compile", "cmd": ["cargo", "test", "--lib"]}, 仓, 仓),
        ("node 脚本",
         {"type": "compile", "cmd": ["node", "-e", "1"]}, 仓, 仓),
        ("pwsh -Command（Windows 上的真 shell）",
         {"type": "compile", "cmd": ["pwsh", "-Command", "exit 0"]}, 仓, 仓),
        ("powershell 写法（历史事故回归）",
         {"type": "compile", "cmd": ["powershell", "-Command", "exit 0"]}, 仓, 仓),
        ("lake env lean（历史事故回归：Lean 工程自带工具）",
         {"type": "compile", "cmd": ["lake", "env", "lean", "x.lean"]}, lean, lean),
        ("git status",
         {"type": "compile", "cmd": ["git", "status"]}, 仓, 仓),
        ("门 in 带子目录（相对路径）",
         {"type": "grep_required", "in": "src/a/b.py", "pattern": "x"}, 仓, 仓),
        ("门 cwd 用 {repo} 占位",
         {"type": "compile", "cmd": ["git", "status"], "cwd": "{repo}"}, 仓, 仓),
        ("门 cwd 用 {work} 占位",
         {"type": "compile", "cmd": ["git", "status"], "cwd": "{work}"}, 工作, 仓),
    ]


def 非法样例(仓: Path) -> list[tuple[str, dict, bool]]:
    """(说明, 门, 是否"只因程序名不在白名单"才非法)。

    ★ 第三个字段是关键：把"程序名"与"路径跳越/越界"两类混在一起过滤，会把
      **路径类**非法样例误当成"档案放行的程序"跳过 —— 自检第一版就这么漏掉
      `cwd: ..` 这一条（它 cmd[0] 是 python，在白名单里，但违规原因是跳越）。
      判据读错了要判的东西，就是这个库反复摔的那一跤。
    """
    return [
        ("任意可执行名", {"type": "compile", "cmd": ["definitely_not_allowed", "x"]}, True),
        ("rm -rf（未在白名单）", {"type": "compile", "cmd": ["rm", "-rf", "/"]}, True),
        ("curl 外联（未在白名单）", {"type": "compile", "cmd": ["curl", "http://x"]}, True),
        ("裸跳越 ..", {"type": "compile", "cmd": ["python", "-c", "1"], "cwd": ".."}, False),
        ("in 含裸跳越", {"type": "grep_required", "in": "../../etc/passwd",
                        "pattern": "root"}, False),
        ("cwd 指到允许根之外", {"type": "compile", "cmd": ["python", "-c", "1"],
                              "cwd": "C:/Windows/System32"}, False),
    ]


def main() -> int:
    print("denial_rate：无效拒绝率 + 漏放率（两个数一起看才有意义）")
    with tempfile.TemporaryDirectory(prefix="dr_") as 桶:
        基 = Path(桶)
        仓, 工作, lean = 基 / "repo", 基 / "work", 基 / "leanproj"
        for d in (仓, 工作, lean):
            d.mkdir()
        (仓 / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
        (仓 / "package.json").write_text("{}", encoding="utf-8")
        (仓 / "src" / "a").mkdir(parents=True, exist_ok=True)
        (仓 / "src" / "a" / "b.py").write_text("x = 1\n", encoding="utf-8")
        (lean / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")

        合法 = 合法样例(仓, 工作, lean)
        拒: list[str] = []
        for 说明, 门, 工作根, 主树根 in 合法:
            ok, 违 = check_cmd_safety([门], 工作根, repo_root=主树根)
            if not ok:
                拒.append(f"{说明} ⇒ {违}")
        率 = len(拒) / max(1, len(合法))
        判(f"无效拒绝率 ≤ {无效拒绝门槛:.0%}",
           率 <= 无效拒绝门槛,
           f"实得 {率:.1%}（{len(拒)}/{len(合法)}）" + (f"；被拒：{拒}" if 拒 else ""))

        非法 = 非法样例(仓)
        # ★ 只拿**当前白名单里确实没有**的当"非法"样例。
        #   真事：`curl` 本机在档案 `gate_cmd_whitelist` 里显式登记过（用户声明要它），
        #   那是**已声明的策略**，不是漏放。把策略当缺陷会导致"为凑指标而收紧策略"。
        白 = _cmd_whitelist((仓,))
        跳过 = [说明 for 说明, 门, 按名 in 非法
                if 按名 and str(门.get("cmd", [""])[0]).lower() in 白]
        非法 = [(说明, 门) for 说明, 门, 按名 in 非法
                if not (按名 and str(门.get("cmd", [""])[0]).lower() in 白)]
        if 跳过:
            print(f"  [INFO] 这些「非法」样例实际由档案显式放行，不计入漏放：{跳过}")
        漏: list[str] = []
        for 说明, 门 in 非法:
            ok, _违 = check_cmd_safety([门], 仓, repo_root=仓)
            if ok:
                漏.append(说明)
        率2 = len(漏) / max(1, len(非法))
        判(f"漏放率 = {漏放门槛:.0%}（围栏没被为降误伤而放松）",
           率2 <= 漏放门槛,
           f"实得 {率2:.1%}（{len(漏)}/{len(非法)}）" + (f"；被放行：{漏}" if 漏 else ""))

        # 历史事故的**单点回归**：这三条红过一次，必须不再红
        for 名, 门, 根 in (
            ("历史事故①powershell", {"type": "compile", "cmd": ["powershell", "-Command", "exit 0"]}, 仓),
            ("历史事故②lake", {"type": "compile", "cmd": ["lake", "env", "lean", "x.lean"]}, lean),
        ):
            ok, 违 = check_cmd_safety([门], 根, repo_root=根)
            判(f"{名} 不再被误拒", ok, f"违规={违}")

        # 判据显式声明能识别：把 lakefile 删掉 ⇒ lake 应当不再被认（识别跟工程事实走）
        (lean / "lakefile.lean").unlink()
        ok2, 违2 = check_cmd_safety(
            [{"type": "compile", "cmd": ["lake", "env", "lean", "x.lean"]}], lean, repo_root=lean)
        判("工程里没有 lakefile 时 lake 不被识别（识别有依据，不是默认放开）",
           (not ok2) and bool(违2), f"违规={违2}")

    print()
    if 失败:
        print(f"denial_rate：{len(失败)} 项红 —— {失败}")
        return 1
    print(f"denial_rate：{len(通过)}/{len(通过)} 全绿（误伤可见 · 漏放为零 · 事故回归）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

