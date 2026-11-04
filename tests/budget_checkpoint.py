#!/usr/bin/env python
"""⑤ 检查点续跑 + 预算闸（tests/budget_checkpoint.py）

为什么这两件必须一起测：AI 会话会断（换会话/上下文溢出/进程重启），断线后
唯一的救生索是**检查点里那句"下一步调什么工具、带什么参数"**；而"断线"最常见的
原因就是**预算烧完了还在跑**。所以：

① 预算块自洽：上限 / 已用 / 剩余三件套，且 剩余 = 上限 − 已用（可机验的算术）
② 门数预算硬拒：判据超预算 ⇒ `参数/前提不成立` + 责任=调用方侧 + 可执行修法，
   **不许**跑到一半才说
③ 检查点落盘且**含续跑入口**（工具名 + 可直接用的参数）
④ 断线可续：**另起一个进程**读检查点（模拟换会话/重启），能拿到工具名与 task_id
⑤ orch_fix 的字符预算：预算到顶必须在**轮边界**停，且如实写停止原因
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(根 / "src"))

桶 = Path(tempfile.mkdtemp(prefix="bc_"))
atexit.register(shutil.rmtree, 桶, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
os.environ["ORCH_STATE_DIR"] = str(桶 / "state")

from agentscope_orchestrator import agentface  # noqa: E402
from agentscope_orchestrator import taixu_mcp as M  # noqa: E402

通过: list[str] = []
失败: list[str] = []


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}" + (f"：{说明}" if 说明 else ""))


def main() -> int:
    print("budget_checkpoint：预算闸 + 断线续跑")
    仓 = 桶 / "repo"
    仓.mkdir()
    (仓 / "a.py").write_text("print('ok')\n", encoding="utf-8")

    # ── ② 门数预算硬拒（超预算 ⇒ 调用方侧 + 修法可执行） ──
    门 = [{"name": f"g{i}", "type": "grep_required", "pattern": "ok", "path": "a.py"}
          for i in range(6)]
    拒 = json.loads(asyncio.run(M.orch_run(str(仓), ["python", "-c", "print('ok')"],
                                         gates=门, max_gates=3)))
    判("② 门数超预算 ⇒ 拒跑（参数/前提不成立 + 调用方侧）",
       拒.get("分类") == "参数/前提不成立" and 拒.get("责任归属") == "调用方侧",
       f"{拒.get('分类')}")
    判("② 拒跑仍给可执行修法（抬预算的具体参数）",
       (拒.get("修法") or {}).get("工具") == "orch_run"
       and (拒.get("修法") or {}).get("参数", {}).get("max_gates")
       == 拒["预算"]["门数"]["实得"])

    # ── ①③ 正常任务的预算块 + 检查点 ──
    回 = json.loads(asyncio.run(M.orch_run(str(仓), ["python", "-c", "print('ok')"],
                                       max_gates=40, timeout_s=120)))
    预 = 回.get("预算") or {}
    秒 = 预.get("秒") or {}
    判("① 预算块含 上限/已用（超时预算不写只读）",
       bool(秒.get("上限")) and 秒.get("已用") is not None, f"{秒}")
    判("① 剩余 = 上限 − 已用（算术自洽）",
       秒.get("剩余") is None or abs(float(秒["剩余"])
                                     - max(0.0, float(秒["上限"]) - float(秒["已用"]))) < 0.11,
       f"{秒}")
    判("① 门数预算有读数", (预.get("门数") or {}).get("实得", 0) >= 1, f"{预.get('门数')}")
    留 = 回.get("留痕") or ""
    判("③ 检查点文件落盘", bool(留) and Path(留).is_file(), 留)
    内 = json.loads(Path(留).read_text(encoding="utf-8")) if 留 and Path(留).is_file() else {}
    判("③ 检查点含 分类/阶段/门快照",
       bool(内.get("分类")) and bool(内.get("阶段")) and isinstance(内.get("门"), list))
    续 = 内.get("续跑") or {}
    判("③ 检查点含**可直接用**的续跑入口（工具 + task_id）",
       bool(续.get("工具")) and (续.get("参数") or {}).get("task_id") == 回.get("task_id"),
       f"{续}")

    # ── ④ 断线可续：另起一个进程读检查点（换会话/重启的等价物） ──
    脚本 = ("import json,sys;"
            f"d=json.load(open(r'{留}',encoding='utf-8'));"
            "print(json.dumps({'工具':d['续跑']['工具'],"
            "'task_id':d['续跑']['参数']['task_id'],'下一步':d.get('下一步')},"
            "ensure_ascii=False))")
    子 = subprocess.run([sys.executable, "-c", 脚本], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60)
    跨进程 = json.loads(子.stdout.strip()) if 子.returncode == 0 and 子.stdout.strip() else {}
    判("④ 新进程能读回续跑入口（断线不丢任务）",
       bool(跨进程.get("工具")) and bool(跨进程.get("task_id")), str(跨进程))
    判("④ 跨进程读回的任务在库中真实存在",
       any(t.get("id") == 跨进程.get("task_id") for t in M._jobs.values())
       or bool(M._db_load(跨进程.get("task_id") or "")),
       f"task_id={跨进程.get('task_id')}")

    # ── ⑤ orch_fix 字符预算：轮边界硬停 ──
    #   这里用**干跑**不调模型不可行（干跑不谈预算），所以只验"预算字段与自洽性
    #   在真实返回里成立"——真正的轮边界停止已在实现里；调用模型会把本测拖成分钟级。
    判("⑤ orch_fix 暴露 max_chars 预算参数",
       "max_chars" in M.orch_fix.__code__.co_varnames,
       "签名含 max_chars")

    # ★ ⑤ 的真验：用**假模型**（monkeypatch）跑预算循环 —— 真模型会把本测拖成分钟级，
    #   但预算逻辑本身必须被机验：超预算要在**轮边界**停，且如实写停止原因。
    from agentscope_orchestrator import fixloop as fl

    真 = fl.修复
    调用次数 = {"n": 0}

    async def _假修复(仓, 命令, 门, 轮数, 通道, 干跑=False):  # noqa: ANN001
        调用次数["n"] += 1
        return {"分类": "未达标", "责任归属": "执行侧", "门": [{"门": "cmd_exit_ok", "通过": False}],
                "修复记录": [{"轮": 1, "提示字节": 150_000, "回复字节": 90_000,
                              "改动": [], "未采纳": "预算测试"}],
                "说明": "每轮改动均已回滚，仓保持原状"}

    try:
        fl.修复 = _假修复
        带 = json.loads(asyncio.run(M.orch_fix(
            str(仓), ["python", "-c", "print('x')"], rounds=5, max_chars=200_000,
            timeout_s=60)))
    finally:
        fl.修复 = 真
    预 = 带.get("预算") or {}
    判("⑤ 超字符预算 ⇒ 在轮边界停（不是跑满 5 轮才说超了）",
       int(预.get("已跑轮数") or 0) == 1 and 调用次数["n"] == 1,
       f"已跑轮数={预.get('已跑轮数')} 实际调用={调用次数['n']}")
    判("⑤ 停止原因如实写『字符预算用尽』（不是『修不动』）",
       "字符预算用尽" in str(预.get("停止原因") or ""), str(预.get("停止原因")))
    判("⑤ 预算到顶仍给检查点留痕（断线可回读）", bool(带.get("留痕")))

    print()
    if 失败:
        print(f"budget_checkpoint：{len(失败)} 项红 —— {失败}")
        return 1
    print(f"budget_checkpoint：{len(通过)}/{len(通过)} 全绿（预算自洽 · 硬拒 · 检查点 · 跨进程续跑）")
    return 0


if __name__ == "__main__":
    sys.exit(main())


