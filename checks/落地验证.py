# -*- coding: utf-8 -*-
"""编排器**落地验证**：走**真代码路径**（taixu_mcp 的真函数），不是 mock。

★ 为什么必须有这一件：
  模块自检全绿只能说明「每条判据自己成立」，
  不能说明「把任务交给编排器，它真能把任务跑对」。
  ⇒ 本件端到端真跑：真提交、真调度、真执行、真判据、真落库，逐条验收。

★ 每条都对着一个**本轮修掉的真缺陷**，且**反向验证**（把缺陷放回去必须红）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

# ★ 产品件：路径**从 __file__ 推导**，不写死本机绝对路径（换机即可跑）
_PKG = Path(__file__).resolve().parents[1]        # …\agentscope_orchestrator
sys.path.insert(0, str(_PKG / "src"))

import agentscope_orchestrator.taixu_mcp as m              # noqa: E402
import agentscope_orchestrator.auto_goal as ag             # noqa: E402
from agentscope_orchestrator.gate_derive import check_cmd_safety  # noqa: E402

通过: list = []
失败: list = []


def chk(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}"
          + (f"：{说明}" if 说明 else ""))


def 真跑(任务: dict, 超时: float = 120.0) -> dict:
    """把一个 job 直接喂进 `_run_errand` 真跑（不起 MCP 服务、不连网）。

    ★ `_run_errand` 结尾会调 `_schedule()` —— 它会看到 `_jobs` 里**全部** queued 任务。
      故这里**隔离**：跑之前把 `_jobs` 清成只剩本任务，跑完恢复。
      （不清的话，本件前面造的假依赖任务会被调度，报 KeyError ——
       那是**脚手架**问题，不是产品问题。）
    """
    jid = 任务["id"]
    _保存 = dict(m._jobs)
    try:
        m._jobs.clear()
        m._jobs[jid] = 任务
        asyncio.run(m._run_errand(jid))
        return m._jobs[jid]
    finally:
        _结果 = dict(m._jobs.get(jid) or {})
        m._jobs.clear()
        m._jobs.update(_保存)
        if _结果:
            m._jobs[jid] = _结果


def 造任务(jid: str, cmd: list, cwd: Path, gates: list | None = None,
           deps: list | None = None) -> dict:
    return {"id": jid, "created": time.time(), "kind": "engine",
            "unit_id": "selfcheck", "target_rel": jid, "cmd": cmd,
            "cwd": str(cwd), "gates": list(gates or []), "priority": 0,
            "depends_on": list(deps or []), "state": "queued", "dry": False}


print("═" * 70)
print("① R2-F2：**任务的本体命令必须真跑**（gates 非空时也必须跑）")
print("═" * 70)
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    印 = d / "ran.txt"           # 命令跑过就会创建这个文件
    # 关键：**显式给了一条旁证门** —— 旧实现此时**根本不跑 cmd**
    任务 = 造任务("t_cmd", ["python", "-c",
                            f"open(r'{印}', 'w').write('ran')"],
                  d, gates=[{"name": "旁证", "type": "grep_required",
                             "patterns": ["x"], "in": "{file}"}])
    (d / "dummy.py").write_text("x\n", encoding="utf-8")
    任务["gates"][0]["in"] = str(d / "dummy.py")
    j = 真跑(任务)
    chk("cmd_真的执行了", 印.is_file() and 印.read_text() == "ran",
        f"印文件存在={印.is_file()}（旧实现此时 cmd 从不执行）")
    chk("cmd 门被自动编入", j.get("cmd_gate") == "cmd_exit_ok",
        f"cmd_gate={j.get('cmd_gate')}")
    chk("旁证门与 cmd 门都在审计里",
        len(j["status"]["audit"]) >= 2,
        f"审计条数={len(j['status']['audit'])}")

print()
print("═" * 70)
print("② R2-F2 反向：**cmd 真失败必须判红**（不许被旁证门盖过去）")
print("═" * 70)
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d / "ok.txt").write_text("OK\n", encoding="utf-8")
    任务 = 造任务("t_cmdfail", ["python", "-c", "raise SystemExit(3)"], d,
                  gates=[{"name": "旁证", "type": "grep_required",
                          "patterns": ["OK"], "in": str(d / "ok.txt")}])
    j = 真跑(任务)
    chk("cmd 退出码 3 ⇒ 任务不 promoted",
        j["state"] == "convergence_failed",
        f"state={j['state']}（旁证门是绿的，旧实现会误判绿）")
    chk("因由读得出退出码不符",
        "退出码不符" in json.dumps(j["status"]["audit"], ensure_ascii=False),
        "audit 里应有退出码不符的因由码")

print()
print("═" * 70)
print("③ R2-F3：**依赖只暂存（staged）不得放行下游**")
print("═" * 70)
m._jobs["dep_staged"] = {"id": "dep_staged", "state": "staged"}
m._jobs["dep_promoted"] = {"id": "dep_promoted", "state": "promoted"}
m._jobs["dep_err"] = {"id": "dep_err", "state": "error"}
m._jobs["down1"] = {"id": "down1", "state": "queued", "depends_on": ["dep_staged"]}
m._jobs["down2"] = {"id": "down2", "state": "queued", "depends_on": ["dep_promoted"]}
m._jobs["down3"] = {"id": "down3", "state": "queued", "depends_on": ["dep_err"]}
s1, w1 = m._dep_state("down1")
s2, _ = m._dep_state("down2")
s3, _ = m._dep_state("down3")
chk("staged 依赖 ⇒ 不放行（判 failed 并说明）", s1 == "failed" and "暂存" in w1,
    f"({s1}, {w1[:40]}…)")
chk("promoted 依赖 ⇒ 放行", s2 == "ready", f"state={s2}")
chk("error 依赖 ⇒ 不放行", s3 == "failed", f"state={s3}")

print()
print("═" * 70)
print("④ R2-F4：**MCP 入口的命令安全闸**（任意可执行 / 越界 cwd 必须拒）")
print("═" * 70)
_恶 = [{"name": "x", "type": "compile", "cmd": ["rm", "-rf", "/"], "cwd": "."}]
_越 = [{"name": "y", "type": "compile", "cmd": ["python", "-c", "pass"],
        "cwd": "../../../etc"}]
_好 = [{"name": "z", "type": "compile", "cmd": ["python", "-c", "pass"],
        "cwd": "."}]
s恶, v恶 = check_cmd_safety(_恶, _PKG / "work", repo_root=_PKG)
s越, v越 = check_cmd_safety(_越, _PKG / "work", repo_root=_PKG)
s好, _ = check_cmd_safety(_好, _PKG / "work", repo_root=_PKG)
chk("非白名单可执行被拒", not s恶, f"违规={v恶[:1]}")
chk("越界 cwd 被拒", not s越, f"违规={v越[:1]}")
chk("合规命令放行", s好, "")
import inspect                                            # noqa: E402
chk("安全闸真接在 run_errand 上",
    "check_cmd_safety" in inspect.getsource(m.run_errand), "")
chk("安全闸真接在 submit_task 上",
    "check_cmd_safety" in inspect.getsource(m.submit_task), "")

print()
print("═" * 70)
print("⑤ R1-F1：**索引覆盖不变量必须真**（旧实现恒真）")
print("═" * 70)
from agentscope_orchestrator.index import UnitIndex        # noqa: E402
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d / "a.py").write_text("x=1\n", encoding="utf-8")
    (d / "b.py").write_text("y=2\n", encoding="utf-8")
    ix = UnitIndex()
    ix.build(d, "**/*.py")
    chk("真建库 ⇒ 覆盖成立", ix.coverage_holds(), f"gap={ix.coverage_gap()}")
    # 人为把一条 discovered 键抹掉 ⇒ 必须**判不成立**（旧实现照样说成立）
    _k = ix._discovered_keys[0]
    ix._units.pop(_k, None)
    chk("抹掉一条可达键 ⇒ 覆盖**不**成立", not ix.coverage_holds(),
        f"gap={ix.coverage_gap()}")
    chk("覆盖缺口读得出是哪一条", ix.coverage_gap() == [_k], f"{ix.coverage_gap()}")

print()
print("═" * 70)
print("⑥ R1-F5/F6：**坏门规格不得把整个任务带崩**")
print("═" * 70)
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    任务 = 造任务("t_badgate",
                  ["python", "-c", "pass"], d,
                  gates=[{"name": "读不到", "type": "grep_forbidden",
                          "patterns": ["x"], "in": str(d / "根本没有.py")}])
    j = 真跑(任务)
    chk("缺文件的门 ⇒ 任务**不崩**（有终态）",
        j.get("state") in ("convergence_failed", "promoted"),
        f"state={j.get('state')} err={j.get('error')}")
    chk("因由点名『读不到被测文件』",
        "读不到被测文件" in json.dumps(j["status"]["audit"], ensure_ascii=False),
        "")
    任务2 = 造任务("t_badre", ["python", "-c", "pass"], d,
                   gates=[{"name": "坏正则", "type": "grep_forbidden",
                           "patterns": ["(unclosed"], "in": str(d)}])
    (d / "z.py").write_text("x\n", encoding="utf-8")
    任务2["gates"][0]["in"] = str(d / "z.py")
    j2 = 真跑(任务2)
    chk("坏正则的门 ⇒ 任务**不崩**", j2.get("state") in ("convergence_failed", "promoted"),
        f"state={j2.get('state')} err={j2.get('error')}")
    chk("因由点名『正则非法』",
        "正则非法" in json.dumps(j2["status"]["audit"], ensure_ascii=False), "")

print()
print("═" * 70)
print("⑦ R4-F9：**计划是可执行的**（拓扑序 + 成环拒发）")
print("═" * 70)
plan = ag.plan_tasks({"name": "端到端", "pre": ["a", "b"], "constraints": ["c"],
                      "gates": [{"type": "compile", "cmd": ["x"], "cwd": ".",
                                 "expect_exit": 0},
                                {"type": "compile", "cmd": ["y"], "cwd": ".",
                                 "expect_exit": 0}],
                      "acceptance": ["z"]})
位 = {t: i for i, t in enumerate(plan["topo"])}
chk("计划带拓扑序", len(plan["topo"]) == len(plan["tasks"]),
    f"topo={plan['topo']}")
chk("拓扑序真满足依赖先后",
    all(位[d] < 位[t["tid"]] for t in plan["tasks"]
        for d in (t.get("depends_on") or [])), "")
try:
    ag.topo_order([ag.Task(tid="a", name="A", kind="errand", gates=[],
                           depends_on=["b"]),
                   ag.Task(tid="b", name="B", kind="errand", gates=[],
                           depends_on=["a"])])
    chk("成环 ⇒ 拒发", False, "成环却通过了")
except ValueError:
    chk("成环 ⇒ 拒发", True, "")

print()
print("═" * 70)
print("⑧ R6-F11：**读不到的单元不得被当成'无违反'（假绿）**")
print("═" * 70)
from agentscope_orchestrator import observation as obs      # noqa: E402
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d / "好.py").write_text("def f(): pass\n", encoding="utf-8")
    ix = UnitIndex()
    ix.build(d, "**/*.py")
    # 造一条指向**不存在文件**的索引项
    ix.register("src.幽灵", d / "幽灵.py")
    漏: list = []
    _o = obs.read_structure(ix, d, index_key="", 漏读账=漏)
    chk("漏读被记账（不再静默）", any("幽灵" in x["key"] for x in 漏),
        f"漏读账={漏}")
    chk("漏读的单元**不产出观测**（不当成空文本）",
        all("幽灵" not in o.rel for o in _o),
        f"观测={[o.rel for o in _o]}")

print()
print("═" * 70)
print("⑨ R3/R7：**全库静态门**（未定义全局名 = 0）")
print("═" * 70)
from agentscope_orchestrator import static_audit as sa      # noqa: E402
坏 = sa.scan_dir(_PKG / "src" / "agentscope_orchestrator")
chk("全库无未定义全局名", not 坏, f"{坏}")
_q = Path(tempfile.mkdtemp()) / "q.py"
_q.write_text("def g():\n    return _没有\n", encoding="utf-8")
chk("静态门有力（真能报）", bool(sa.undefined_globals_in_file(_q)), "")
_q2 = Path(tempfile.mkdtemp()) / "q2.py"
_q2.write_text("import os\n\ndef g():\n    return os.getcwd()\n", encoding="utf-8")
chk("静态门不误报（干净模块 ⇒ 空）",
    not sa.undefined_globals_in_file(_q2), "")
# ★ 读不到的文件必须**如实报**，不是崩
chk("静态门对读不到的文件如实报（不崩）",
    bool(sa.undefined_globals_in_file(Path(tempfile.mkdtemp()) / "没有.py")), "")

print()
print("═" * 70)
print(f"结论：通过 {len(通过)} / 失败 {len(失败)}")
if 失败:
    for x in 失败:
        print("   FAIL:", x)
print("═" * 70)
sys.exit(1 if 失败 else 0)
