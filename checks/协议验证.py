# -*- coding: utf-8 -*-
"""终局验证：**真起 MCP 服务 + 真过 stdio 协议调工具**。

★ 前面的验证都是"直接调函数"。这一件是**过协议**：
  起真的 MCP 服务器进程 → 真握手 → 真列工具 → 真调工具 → 真拿结果。
  ⇒ 证明的不只是"函数对"，而是"**你从对话里一调就能跑**"。

★ 全部走 dry/安全路径（不连模型 API、不改主树），故可反复跑。
★ `list_tasks` 在任务多时会把载荷**外置成文档**（`ds.thin`）⇒ 本件两种形态都处理。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# ★ 产品件：路径**从 __file__ 推导**；解释器用**当前解释器**（门就是用venv跑的）
_PKG = Path(__file__).resolve().parents[1]        # …\agentscope_orchestrator
PY = Path(sys.executable)
SERVER = _PKG / "src" / "agentscope_orchestrator" / "taixu_mcp.py"
真单元 = "src.bin.O1基准"          # 真在 Rust 引擎索引里的单元（实测）

from mcp import ClientSession, StdioServerParameters        # noqa: E402
from mcp.client.stdio import stdio_client                   # noqa: E402

通过: list = []
失败: list = []


def chk(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}" + (f"：{说明}" if 说明 else ""))


def 解析(文: str):
    """`ds.thin` 可能把载荷外置成文档指针 ⇒ 两种形态都还原成对象。"""
    try:
        return json.loads(文)
    except json.JSONDecodeError:
        m = re.search(r"\[doc:([^\s·]+)", 文)
        if not m:
            return None
        from agentscope_orchestrator import doc_store as ds
        try:
            d = ds.read_doc(m.group(1), max_chars=200000)
        except Exception:                              # noqa: BLE001
            return None
        try:
            return json.loads(d)
        except (json.JSONDecodeError, TypeError):
            return None


async def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PKG / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    参数 = StdioServerParameters(command=str(PY), args=[str(SERVER)], env=env)

    async with stdio_client(参数) as (读, 写):
        async with ClientSession(读, 写) as 会话:
            print("═" * 70)
            print("① 起真服务 + 握手")
            print("═" * 70)
            await 会话.initialize()
            chk("MCP 握手成功", True, "")

            print()
            print("═" * 70)
            print("② 真列工具")
            print("═" * 70)
            工具 = await 会话.list_tools()
            名 = [t.name for t in 工具.tools]
            chk("工具已注册", len(名) >= 10, f"{len(名)} 个")
            print("      " + " / ".join(名))

            def 找(尾: str):
                return next((t.name for t in 工具.tools if 尾 in t.name), None)

            名计划, 名列表 = 找("目标计划"), 找("任务列表")
            名任意, 名文档 = 找("任意任务"), 找("读文档")
            chk("找到『目标计划』", 名计划 is not None, f"{名计划}")
            chk("找到『任意任务』", 名任意 is not None, f"{名任意}")
            chk("找到『任务列表』", 名列表 is not None, f"{名列表}")

            async def 调(工具名, 参数d):
                r = await 会话.call_tool(工具名, 参数d)
                return "".join(getattr(c, "text", "") for c in r.content)

            print()
            print("═" * 70)
            print("③ 真过协议调『目标计划』（0-LLM 分解）")
            print("═" * 70)
            规约 = json.dumps({"name": "协议验证", "pre": ["a"], "constraints": ["b"],
                               "gates": [{"type": "compile", "cmd": ["x"],
                                          "cwd": ".", "expect_exit": 0}],
                               "acceptance": ["c"]}, ensure_ascii=False)
            计划 = 解析(await 调(名计划, {"goal_json": 规约})) or {}
            chk("计划返回成功", bool(计划.get("tasks")),
                f"{len(计划.get('tasks', []))} 任务")
            chk("计划带可执行拓扑序", bool(计划.get("topo")), f"topo={计划.get('topo')}")
            chk("计划带执行契约", "执行契约" in 计划, "")
            位 = {t: i for i, t in enumerate(计划.get("topo") or [])}
            chk("拓扑序满足依赖先后",
                all(位[d] < 位[t["tid"]] for t in 计划["tasks"]
                    for d in (t.get("depends_on") or [])), "")

            print()
            print("═" * 70)
            print("④ 真过协议调『任意任务』——**真跑一条命令**")
            print("═" * 70)
            td = Path(tempfile.mkdtemp())
            印 = td / "ran.txt"
            回2 = 解析(await 调(名任意, {
                "unit_id": 真单元,
                "cmd": [str(PY), "-c",
                        f"open(r'{印}','w',encoding='utf-8').write('ran')"],
                "cwd": str(_PKG), "gates": []})) or {}
            chk("提交被受理", bool(回2.get("ok")), f"{回2}")
            tid = 回2.get("task_id")
            终 = None
            for _ in range(80):
                await asyncio.sleep(0.5)
                表 = 解析(await 调(名列表, {})) or []
                行 = next((x for x in 表 if x.get("id") == tid), None)
                if 行 and 行.get("state") not in ("queued", "running"):
                    终 = 行
                    break
            chk("任务真跑到终态", 终 is not None,
                f"state={终.get('state') if 终 else '超时未终态'}")
            chk("★ 命令**真的执行了**（过协议）", 印.is_file(),
                f"{印.name} 存在={印.is_file()}")
            chk("任务判绿（promoted）", (终 or {}).get("state") == "promoted",
                f"state={(终 or {}).get('state')}")

            print()
            print("═" * 70)
            print("⑤ 真过协议调『任意任务』——**危险命令必须被拒**")
            print("═" * 70)
            回4 = 解析(await 调(名任意, {"unit_id": 真单元, "cmd": ["rm", "-rf", "/"],
                                        "cwd": str(_PKG), "gates": []})) or {}
            chk("危险命令被安全闸拒",
                回4.get("ok") is False and "安全闸" in str(回4.get("error", "")),
                f"{回4.get('error')}")
            回5 = 解析(await 调(名任意, {"unit_id": "不存在.的单元",
                                        "cmd": [str(PY), "-c", "pass"],
                                        "cwd": str(_PKG), "gates": []})) or {}
            chk("锚定不到 ⇒ 拒跑（防派到不存在的文件）",
                回5.get("ok") is False and "锚定" in str(回5.get("error", "")),
                f"{回5.get('error')}")

            print()
            print("═" * 70)
            print("⑥ 真过协议调『任务列表』")
            print("═" * 70)
            表 = 解析(await 调(名列表, {})) or []
            chk("任务列表可读", len(表) >= 1, f"{len(表)} 条")
            chk("列表里有刚跑完的任务",
                any(x.get("id") == tid for x in 表), f"tid={tid}")

    print()
    print("═" * 70)
    print(f"结论：通过 {len(通过)} / 失败 {len(失败)}")
    for x in 失败:
        print("   FAIL:", x)
    print("═" * 70)
    return 1 if 失败 else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
