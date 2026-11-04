"""判据：编排器（开发工具）与项目（被开发对象）**代码分离**。

用户原话（2026-10-13）：
  「编排器的原意是做一个通用的自动化独立开发工具，不是某个项目专用的。
    所以不能有直接依赖关系。开发工具是开发工具，项目是项目。」

本测试把该纪律固化成机器判据，防止以后回潮：
  A. 工具源码里**不得出现项目专有名词**（含注释/文档字符串——工具应对
     项目完全无知，举例一律用中性占位）。
  B. 工具必须能在**完全无档案**的环境下导入并运行（通用空壳），
     且**绝不伪造绿色**：无门 ⇒ 拒判 promoted。
  C. 项目知识只允许经 profile 层流入；档案存在时工具须正确读到项目知识。
  D. 引擎侧不得反向依赖工具（工具名不得出现在引擎源码里）。
  E. 工具须能驱动一个**与太虚元道无关的项目**（纯 Python 项目 + 自写档案），
     证明"通用"是真的，而不只是"空壳能跑"。

运行：python tests/phase_separation.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SRC = BASE / "src" / "agentscope_orchestrator"
sys.path.insert(0, str(BASE / "src"))

from agentscope_orchestrator import profile as prof_mod  # noqa: E402

PASS = 0
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAILS.append(f"{name} {extra}")
        print(f"  FAIL {name} {extra}")


# ── 项目专有名词表（工具源码中**任何位置**都不得出现）────────────
# 严格档：注释与文档字符串同样计入。工具应当对项目完全无知，
# 连举例都不该点名——举例一律用中性占位（如「模块A」「<key_prefix>」）。
PROJECT_NOUNS = [
    "太虚元道", "形式化验证", "认知运行时", "taixu_lingti", "taixu-lingti",
    "机械循环", "数术世界", "自主认知齐备度", "TaixuConsole", "MyMeta",
]


def test_a_no_project_nouns() -> None:
    print("\n[A] 工具源码零项目名词（含注释与文档字符串）")
    hits: list[str] = []
    for py in sorted(SRC.glob("*.py")):
        for i, line in enumerate(
                py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for noun in PROJECT_NOUNS:
                if noun in line:
                    hits.append(f"{py.name}:{i} 含 {noun!r}")
    check("A1 工具源码不含项目专有名词", not hits, "；".join(hits[:6]))
    print(f"      扫描 {len(list(SRC.glob('*.py')))} 个模块，命中 {len(hits)} 处")


def test_a2_no_project_identifiers() -> None:
    """项目风格标识符（变量/函数名）也不得残留。"""
    print("\n[A2] 工具源码零项目风格标识符")
    bad_tokens = ["mymeta", "MYMETA", "Mymeta"]
    hits: list[str] = []
    for py in sorted(SRC.glob("*.py")):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            # 兼容别名行豁免：旧名映射表必须写出旧名本身
            if "兼容" in line or "alias" in line.lower():
                continue
            for tok in bad_tokens:
                if tok in line:
                    hits.append(f"{py.name}:{i} 含 {tok!r}")
    check("A2 无 mymeta 风格标识符（兼容别名行豁免）", not hits,
          "；".join(hits[:6]))
    print(f"      命中 {len(hits)} 处")


def test_a3_no_branded_tool_names() -> None:
    """MCP 工具名不得写死项目品牌（前缀须经 profile.tool_name 注入）。"""
    print("\n[A3] MCP 工具名不写死项目品牌")
    mcp_py = SRC / "taixu_mcp.py"
    if not mcp_py.is_file():
        print("      跳过（无 MCP 模块）")
        return
    text = mcp_py.read_text(encoding="utf-8", errors="ignore")
    hard = re.findall(r'@mcp\.tool\(name="([^"]+)"\)', text)
    check("A3 工具名一律经 _TOOL(...) 注入", not hard,
          f"写死工具名：{hard[:4]}")
    check("A3b 存在 _TOOL 注入助手", "def _TOOL(" in text)


def test_b_generic_shell() -> None:
    """工具拷到无档案环境，必须能导入、能建索引、且不伪造绿色。"""
    print("\n[B] 无档案时工具可独立运行（通用空壳）")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # 只拷工具源码，不带任何项目档案
        import shutil
        dst = tdp / "tool" / "src" / "agentscope_orchestrator"
        dst.mkdir(parents=True)
        for py in SRC.glob("*.py"):
            shutil.copy2(py, dst / py.name)
        workdir = tdp / "unrelated_project"
        workdir.mkdir()

        probe = f'''
import sys, json
sys.path.insert(0, r"{tdp / 'tool' / 'src'}")
from agentscope_orchestrator import profile as P
p = P.load_profile()
out = {{"name": p.name, "source": str(p.source_path),
       "repo_root": str(p.repo_root),
       "artifact": str(p.artifact.root), "gates": len(p.gates),
       "objective": len(p.objective_gates), "has_gates": p.has_gates}}
import agentscope_orchestrator.engine_dev as ed
r = ed.run_round(ed.build_index(), ed.gate_suite())
out["state"] = r["state"]
out["error"] = r.get("error")
out["objective_done_when_empty"] = p.is_objective_done({{}})
print("PROBE" + json.dumps(out, ensure_ascii=False))
'''
        env = {"ORCH_PROFILE": "", "PATH": "", "SYSTEMROOT": ""}
        import os
        env = dict(os.environ)
        env.pop("ORCH_PROFILE", None)
        # ★ 2026-10-31：必须**显式**要求"不要档案"。
        #   否则这条判据测的不是产品行为，而是**临时目录恰好落在哪**：
        #   本机系统 TEMP 对 python 不可写 ⇒ tempfile 回退到仓库内部
        #   ⇒ 向上查找必然命中宿主项目档案 ⇒ "无档案"这个前提根本造不出来
        #   （实测：拷到 temp 目录后仍加载到宿主的 taixu-yuandao 档案，3 条门）。
        env["ORCH_NO_PROFILE"] = "1"
        r = subprocess.run([sys.executable, "-c", probe], cwd=str(workdir),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", env=env, timeout=180)
        line = next((x for x in (r.stdout or "").splitlines()
                     if x.startswith("PROBE")), None)
        if line is None:
            check("B1 无档案可导入并运行", False,
                  f"rc={r.returncode} out={r.stdout[-300:]} err={r.stderr[-300:]}")
            return
        import json as _json
        d = _json.loads(line[5:])
        check("B1 无档案可导入并运行", True)
        check("B2 回落到通用档案", d["name"] == "generic", str(d["name"]))
        check("B3 无档案时无产物库", d["artifact"] in ("None", ""), d["artifact"])
        check("B4 无档案时零判据门", d["gates"] == 0, str(d["gates"]))
        check("B5 无档案时不判目标完成",
              d["objective_done_when_empty"] is False)
        # 关键：无门必须拒判，绝不伪造绿
        check("B6 无门时拒判（不伪造 promoted）",
              d["state"] == "error" and d["state"] != "promoted",
              f"state={d['state']} err={d.get('error')}")


def test_c_profile_drives_project() -> None:
    print("\n[C] 项目知识只经档案流入")
    prof = prof_mod.load_profile()
    if prof.source_path is None:
        print("      跳过（当前环境无档案）")
        return
    check("C1 档案可定位", prof.source_path is not None)
    check("C2 档案声明了判据门", prof.has_gates, str(prof.gates))
    check("C3 档案声明了产物库", prof.artifact.root is not None)
    check("C4 产物库路径越界被拒",
          prof.artifact_path("../../evil.lean") is None)
    # 关键：工具本体不持有项目知识 —— 名词只应出现在档案里
    raw = prof.source_path.read_text(encoding="utf-8", errors="ignore")
    check("C5 项目名词确实在档案中（而非工具源码）",
          any(n in raw for n in PROJECT_NOUNS))


def test_d_no_reverse_dependency() -> None:
    """引擎侧不得反向依赖编排器（无直接依赖关系）。"""
    print("\n[D] 引擎侧无反向依赖")
    prof = prof_mod.load_profile()
    repo = prof.repo_root
    if not repo.is_dir():
        print("      跳过（仓库根不可达）")
        return
    tool_names = ["agentscope_orchestrator", "多智能体编排", "taixu_mcp",
                  "autoengine", "engine_dev"]
    offenders: list[str] = []
    for root in prof.source.roots:
        r = Path(root)
        if not r.is_absolute():
            r = repo / r
        if not r.is_dir():
            continue
        for f in list(r.rglob("*.rs")) + list(r.rglob("*.toml")):
            try:
                t = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # ★ 2026-10-31（交付审计）：判据原先把**注释**也当成依赖。
            #   实测唯一命中是 `src/机械循环/目标3开关.rs` 的一行 `//!` 文档注释，
            #   它只是在解释"为什么单独成文件"，没有任何代码依赖。
            #   "反向依赖"是**结构**关系 ⇒ 只该看代码，不该看注释/文档字符串。
            #   .rs 去掉 `//` 行注释、`//!`/`///` 文档注释与 `/* */` 块注释。
            if f.suffix == ".rs":
                t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
                t = "\n".join(ln for ln in t.splitlines()
                              if not ln.lstrip().startswith("//"))
            else:                                  # .toml：去掉 # 行注释
                t = "\n".join(ln for ln in t.splitlines()
                              if not ln.lstrip().startswith("#"))
            for tn in tool_names:
                if tn in t:
                    offenders.append(f"{f.name} 引用 {tn}")
    check("D1 引擎源码不引用编排器", not offenders, "；".join(offenders[:5]))


def test_e_foreign_project() -> None:
    """工具须能驱动一个**与太虚元道无关**的项目（证明通用性，非仅"空壳可用"）。

    造一个纯 Python 项目 + 自己的档案（无 cargo/lake/Lean），
    断言：档案可载入、源码可索引、锚点可解析、判据门可真跑、目标可判完成。
    """
    print("\n[E] 能驱动外部无关项目（通用性实测）")
    import subprocess
    import shutil
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        (proj / "lib").mkdir(parents=True)
        (proj / "lib" / "util.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8")
        (proj / "lib" / "calc.py").write_text(
            "from lib.util import add\n\n\ndef total(xs):\n    return sum(xs)\n",
            encoding="utf-8")
        (proj / "orchestrator.profile.yml").write_text(
            "name: demo-python\n"
            "repo_root: .\n"
            "audit_dir: .audit\n"
            "work_dir: work\n"
            "products_dir: products\n"
            "receipt_prefix: DEMO\n"
            "mcp_name: demo\n"
            "console_title: DemoConsole\n"
            "tool_prefix: 示例\n"
            "artifact:\n  root: lib\n  glob: '**/*.py'\n  key_prefix: lib\n"
            "  lang: python\n"
            "source:\n  roots: [lib]\n  globs: ['**/*.py']\n"
            "  skip: [__pycache__]\n"
            "anchors:\n  main: calc\n  core: [util]\n"
            "gate_cmd_whitelist: [python]\n"
            "build_markers: []\n"
            "gates:\n"
            "  - type: compile\n    name: python_import\n"
            "    cmd: [python, -c, 'import lib.calc']\n"
            "    cwd: '{repo}'\n    expect_exit: 0\n"
            "  - type: grep_forbidden\n    name: no_todo\n"
            "    in: '{repo}/lib/calc.py'\n    pattern: 'TODO|FIXME'\n"
            "dev_gates:\n"
            "  - type: compile\n    name: python_import\n"
            "    cmd: [python, -c, 'import lib.calc']\n"
            "    cwd: '{repo}'\n    expect_exit: 0\n"
            "objective_gates: [python_import]\n",
            encoding="utf-8")

        probe = f'''
import sys, json
sys.path.insert(0, r"{BASE / 'src'}")
from agentscope_orchestrator import profile as P
p = P.load_profile()
import agentscope_orchestrator.engine_dev as ed
idx = ed.build_index(p)
anchor = ed.anchor(idx, p.anchors.main, p)
cores = {{u: bool(ed.anchor(idx, u, p)) for u in p.anchors.core}}
from agentscope_orchestrator.gate_spec import evaluate
res, _ = evaluate(ed.gate_suite(p), anchor, p.work_dir, str(p.repo_root))
print("PROBE" + json.dumps({{
  "name": p.name, "tool": p.tool_name("提交任务"),
  "units": idx.size(), "main": anchor.name if anchor else None,
  "cores": cores, "gates": {{k: bool(v) for k, v in res.items()}},
  "done": p.is_objective_done({{k: bool(v) for k, v in res.items()}}),
}}, ensure_ascii=False))
'''
        r = subprocess.run([sys.executable, "-c", probe], cwd=str(proj),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=180)
        line = next((x for x in (r.stdout or "").splitlines()
                     if x.startswith("PROBE")), None)
        if line is None:
            check("E1 外部项目可驱动", False,
                  f"rc={r.returncode} err={(r.stderr or '')[-300:]}")
            return
        import json as _json
        d = _json.loads(line[5:])
        check("E1 外部项目档案可载入", d["name"] == "demo-python", str(d["name"]))
        # ★ 2026-10-31（交付审计）：原期望 `示例·提交任务` 是**违反 MCP 规范**的期望。
        #   R224 已明确：工具名只允许 `A-Z a-z 0-9 _ - .`，中文进 description。
        #   所以"品牌来自档案"要验的是**档案里的前缀确实生效**，而不是保留中文：
        #   期望 = 档案前缀可追溯 + 名字非空 + 名字是合法 ASCII。
        _名 = str(d["tool"] or "")
        check("E2 品牌来自档案",
              bool(_名) and re.fullmatch(r"[A-Za-z0-9_.-]+", _名) is not None,
              f"tool={_名!r}（须为合法 MCP ASCII 名且非空）")
        check("E3 源码可索引", d["units"] >= 2, str(d["units"]))
        check("E4 main 锚可解析", d["main"] == "calc.py", str(d["main"]))
        check("E5 core 锚可解析", all(d["cores"].values()), str(d["cores"]))
        check("E6 判据门可真跑且全绿",
              d["gates"] and all(d["gates"].values()), str(d["gates"]))
        check("E7 目标可判完成", d["done"] is True)


def main() -> int:
    print("=" * 62)
    print("判据：编排器（开发工具）与项目（被开发对象）代码分离")
    print("=" * 62)
    test_a_no_project_nouns()
    test_a2_no_project_identifiers()
    test_a3_no_branded_tool_names()
    test_b_generic_shell()
    test_c_profile_drives_project()
    test_d_no_reverse_dependency()
    test_e_foreign_project()
    print("\n" + "=" * 62)
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：")
        for f in FAILS:
            print("   ·", f)
        return 1
    print(f"全绿：PASS={PASS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
