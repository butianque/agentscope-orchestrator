"""G6 判据：通用 GateSpec 评估器确为语言/项目无关机制（非 Lean 专用）。

用一份"Python 语言"的判据门规格来验证——机制本体若能驱动一个非 Lean 目标，
就证明它不是专用机制：差异只在数据(spec)，评估器对任何语言都可现场组装闭环。

覆盖原语：
  compile        用 python 命令当"编译/构建/测试"，断言退出码
  grep_forbidden 文本不得含正则 token（仿 sorry/unsafe/TODO）
  grep_required  文本必须含字面子串（仿"函数签名必须存在"）
  scan_output    临时件+跑命令扫 stdout/stderr 不得含 must_not
以及 validate 校验 + 审计结构 + 占位符 {file}/{work} 替换。
"""
from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.gate_spec import evaluate, validate

PY = sys.executable


def _mk(name, text) -> Path:
    p = Path(tempfile.mkdtemp()) / name
    atexit.register(shutil.rmtree, p, ignore_errors=True)  # 跑完不留临时目录（包根会脏）
    p.write_text(text, encoding="utf-8")
    return p


def main() -> int:
    py = _mk("mod.py", "def solve(x): return x + 1\n")   # 干净模块
    bad = _mk("bad.py", "def solve(x):\n    # TODO: not implemented\n    pass\n")

    # 1) compile：用 python 当构建工具（python mod.py 解码成功=退出码0）
    spec = [{"name": "ok_compiles", "type": "compile",
             "cmd": [PY, "{file}"], "cwd": ".", "expect_exit": 0}]
    res, audit = evaluate(spec, py, py.parent, ".")
    assert res == {"ok_compiles": True} and audit[0]["ok"] is True

    # 2) grep_forbidden：不得含 TODO/unsafe（正则）
    spec = [{"name": "no_forbidden", "type": "grep_forbidden",
             "patterns": [r"\bTODO\b", r"\bunsafe\b"], "in": "{file}"}]
    res, _ = evaluate(spec, py, py.parent, ".")
    assert res["no_forbidden"] is True
    res, _ = evaluate(spec, bad, bad.parent, ".")
    assert res["no_forbidden"] is False, "含 TODO 应判不过"

    # 3) grep_required：必须含函数签名（字面子串）
    spec = [{"name": "has_def", "type": "grep_required",
             "patterns": ["def solve"], "in": "{file}"}]
    res, _ = evaluate(spec, py, py.parent, ".")
    assert res["has_def"] is True
    res, _ = evaluate(spec, _mk("x.py", "def other(y): pass\n"), Path(tempfile.mkdtemp()), ".")
    assert res["has_def"] is False, "缺函数签名应判不过"

    # 4) scan_output：临时件+跑命令，扫输出不得含 must_not
    spec = [{"name": "no_leak", "type": "scan_output",
             "src": "{file}",
             "cmd": [PY, "-c", "print('leaked_secret')"],
             "cwd": ".", "must_not": ["leaked_secret"], "expect_exit": 0}]
    res, _ = evaluate(spec, py, py.parent, ".")
    assert res["no_leak"] is False, "输出含 must_not 应判不过"
    spec[0]["cmd"] = [PY, "-c", "print('clean')"]
    res, _ = evaluate(spec, py, py.parent, ".")
    assert res["no_leak"] is True

    # 5) validate：非法 type 拒绝，合法通过，None 通过
    assert validate(None) is None
    assert validate([{"name": "a", "type": "compile", "cmd": [], "cwd": "."}]) is None
    assert validate([{"name": "a", "type": "nonsense"}]) is not None

    # 6) 审计结构：gate/file/ok 可回放，与既有状态审计一致
    spec = [{"name": "g1", "type": "grep_required", "patterns": ["def"], "in": "{file}"}]
    _, audit = evaluate(spec, py, py.parent, ".")
    assert audit[0]["gate"] == "g1" and audit[0]["file"] == "mod.py" and audit[0]["ok"]

    print("[g6] PASS: 通用判据门机制确与语言无关（以 Python 规格驱动验证）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())