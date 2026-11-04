"""Phase 2 机证判据：hard gates 拒绝含糊/正确接受 + 审计日志。exit 0 通过。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.judge import audit_log, judge

# 1) exact_int：空/错 → False；对 → True
assert judge({"pred": "exact_int", "expected": 42}, "") is False, "空输出必须被拒"
assert judge({"pred": "exact_int", "expected": 42}, "答案是 43") is False
assert judge({"pred": "exact_int", "expected": 42}, "答案：42") is True

# 2) 审计日志记录了每次调用
n_before = len(audit_log())
judge({"pred": "non_empty"}, "x")
assert len(audit_log()) == n_before + 1, "审计日志必须记录判据调用"

# 3) grep_unmatched：0-sorry 检查
clean = Path(__file__).resolve().parent / "_clean_tmp.txt"
clean.write_text("prelude\nlemma foo := by trivial\n", encoding="utf-8")
assert judge({"pred": "grep_unmatched", "path": str(clean), "token": "sorryAx"}) is True
clean.write_text("lemma bar := by sorryAx\n", encoding="utf-8")
assert judge({"pred": "grep_unmatched", "path": str(clean), "token": "sorryAx"}) is False
clean.unlink(missing_ok=True)

# 4) shell_ok：成功命令 True / 失败命令 False
assert judge({"pred": "shell_ok", "cmd": "python -c \"print()\""}) is True
assert judge({"pred": "shell_ok", "cmd": "python -c \"import sys; sys.exit(3)\""}) is False

print("[phase2] PASS: hard gates 拒绝含糊 + 接受正确 + 审计齐全")
sys.exit(0)