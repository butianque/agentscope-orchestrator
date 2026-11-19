"""R284：**多文件可重复性**——把"可重复"从一次性观察变成机器可判的事实。

★ 为什么（真读数，2026-11-06）：
  「可重复性」是既定目标里点名的一项，而至今只在 case02 上**偶然**验过一次：
  无人值守跑通 1 次，重复 4 次失败（1/4）。判十五只在 **单文件**（`src/util.rs`）
  上连跑 3 次，那证明不了多文件。

★ 本模块测的是**多文件批量的可重复性**，不依赖模型：
  用本机真工具链（cargo），仓里同时存在**两处**独立编译错误，
  每轮都从「全红基线」出发，要求：
    ① 改动面**同时**定位到两个文件（缺一个 = 少做了）；
    ② 每轮结论**逐字节一致**（面、保留数、复跑 rc）；
    ③ 连跑 N 轮全一致才算可重复。

★ 三条边界（写进返回，不许含糊）：
  · 没有真工具链 ⇒ **没测到**，不是通过；
  · 中途一轮结论不同 ⇒ **不可重复**，并指出是哪一轮不同；
  · 这个测试**不调模型**：它证明的是"机器侧（定位→应用→复跑）可重复"，
    不证明"模型侧可重复"——后者要上游活着才测得到。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _造仓(cargo: str, 目录: Path) -> None:
    """一个跨两文件的 Rust 仓：`lib.rs` 引用两处未定义符号，分别落在两个模块里。"""
    (目录 / "src").mkdir(parents=True, exist_ok=True)
    (目录 / "Cargo.toml").write_text(
        '[package]\nname="demo"\nversion="0.1.0"\nedition="2021"\n\n[workspace]\n',
        encoding="utf-8")
    (目录 / "src" / "lib.rs").write_text("pub mod alpha;\npub mod beta;\n",
                                          encoding="utf-8")
    (目录 / "src" / "alpha.rs").write_text(
        "pub fn a() -> i32 { undefined_alpha }\n", encoding="utf-8")
    (目录 / "src" / "beta.rs").write_text(
        "pub fn b() -> i32 { undefined_beta }\n", encoding="utf-8")


def _一轮(cargo: str, 标: str) -> dict:
    """从全红基线跑一轮：测量 → 定位改动面 → 逐文件应用 → 独立复跑。"""
    from agentscope_orchestrator import generic_goal as _G, gate_spec as _gs

    根 = Path(tempfile.mkdtemp(prefix=f"r284_{标}_")) / "repo"
    _造仓(cargo, 根)
    门 = [{"name": "cargo check", "type": "compile",
           "cmd": [cargo, "check"], "cwd": str(根), "expect_exit": 0}]
    起, 起详 = _gs.evaluate(门, 根, 根)
    if 起.get("cargo check"):
        return {"坏": "起始态就绿 ⇒ 这条齿测不到东西"}

    面 = _G._失败栈改动面236(起详, 根, [根 / "src"])
    面 = sorted(x.replace("\\", "/") for x in (面 or []))
    # 修复内容：把两个模块里的未定义符号换成定义
    修 = {}
    for rel in 面:
        p = 根 / rel
        if not p.is_file():
            continue
        t = p.read_text(encoding="utf-8")
        修[rel] = t.replace("undefined_alpha", "1").replace("undefined_beta", "2")
    # ★ R284b（自查）：第一版这里两个分支都 append ⇒ **等于没判断**，
    #   剩下的只是"写了两个文件"。而"逐文件红段数严格减少才留"是 R278 定下的纪律，
    #   这里必须真执行：每个文件单独试写、试完看**编译错误条数**有没有减少，
    #   减少了才留，否则回退这一个。
    def _错数() -> int:
        _r, _d = _gs.evaluate(门, 根, 根)
        if _r.get("cargo check"):
            return 0
        # 用真 cargo 的错误条数（`error[E….]:` 出现次数）作为"红了几处"
        _o = subprocess.run([cargo, "check"], cwd=str(根),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
        return max(1, _o.stderr.count("error["))

    _前 = _错数()
    留下 = []
    for rel in 面:
        if rel not in 修:
            continue
        p = 根 / rel
        原 = p.read_text(encoding="utf-8")
        p.write_text(修[rel], encoding="utf-8")
        _后 = _错数()
        if _后 < _前:
            留下.append(rel)
            _前 = _后
        else:
            p.write_text(原, encoding="utf-8")     # 没变好 ⇒ 回退这一个
    # 独立复跑（另起 cargo 进程，不复用任何缓存状态）
    复 = subprocess.run([cargo, "check"], cwd=str(根),
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace")
    return {"面": tuple(面), "保留": len(留下), "rc": 复.returncode,
            "绿": bool(_gs.evaluate(门, 根, 根)[0].get("cargo check"))}


def 多文件可重复(*, 轮数: int = 3, 语言: str = "rust") -> dict:
    """连跑 N 轮，判"多文件批量"是否可重复。"""
    if 语言 != "rust":
        return {"ok": False, "测了没": False,
                "结论": f"本机没验过 {语言} 的多文件可重复（只做 rust）"}
    cargo = shutil.which("cargo")
    if not cargo:
        return {"ok": False, "测了没": False,
                "结论": "本机没有 cargo ⇒ **没测到**（不算通过）",
                "诚实边界": "没测到不能当通过了。"}

    结果 = []
    for i in range(max(1, int(轮数))):
        r = _一轮(cargo, str(i))
        if "坏" in r:
            return {"ok": False, "测了没": False, "结论": r["坏"],
                    "诚实边界": "起始态就绿 ⇒ 测不到东西，不许算通过。"}
        结果.append(r)
        if not r.get("绿"):
            return {"ok": False, "测了没": True,
                    "结论": f"第 {i + 1} 轮复跑仍红（改动没让目标仓自己的门变绿）",
                    "轮次": 结果,
                    "诚实边界": "这才是真正要看的：**改动落主树且能被独立复跑验证**。"}

    指纹 = [(x["面"], x["保留"], x["rc"]) for x in 结果]
    一致 = len(set(指纹)) == 1
    每轮两面 = all(len(x["面"]) == 2 for x in 结果)
    return {
        "ok": bool(一致 and 每轮两面),
        "测了没": True,
        "结论": ("连跑 %d 轮：改动面 %s、保留 %d、复跑 rc %s —— **可重复**"
                % (len(结果), 结果[0]["面"], 结果[0]["保留"], 结果[0]["rc"]))
                if (一致 and 每轮两面) else
                (f"**不可重复**：{len(set(指纹))} 种不同结论 {指纹}"
                 if not 一致 else
                 f"**定位不全**：每轮只找出一部分文件 {指纹[0][0]}（应为 2 个）"),
        "轮次": 结果,
        "诚实边界": ("本测试**不调模型**：证明的是机器侧（定位→应用→复跑）可重复，"
                  "**不**证明模型侧可重复——后者要上游活着才测得到。"),
    }
