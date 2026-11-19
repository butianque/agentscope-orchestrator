# -*- coding: utf-8 -*-
"""MCP 工具面契约（N10 · 判据 #29）—— 让外部 agent **不用读源码**就能自动接入。

★ N10 的原话：不做 IDE 插件、不做云，**只做一件**：把 MCP 工具面（41 个）的
  契约**可机读化**，让外部 agent 能自动接入。判据：契约 JSON 可被**独立脚本**解析
  且覆盖**全部 41 个**工具。

★ 契约从**活的注册表**现算（`mcp._tool_manager.list_tools()`），不是从源码猜：
  源码里的 `@mcp.tool()` 装饰器可以被条件跳过、名字可以被 `_TOOL()` 前缀改装，
  只有注册表里的才是**外部 agent 真正会看到的东西**（口径 344：不许替文件下结论）。

★ 判四件事：
  ① JSON 能被**另一个进程**读出来并解析（不是"我刚写完所以我知道"）；
  ② 覆盖：契约里的工具名集合 == 注册表现算集合（并按名字排序逐一比对）；
  ③ 每个工具都有 `name` + `描述` + `inputSchema`（缺一即不达标 —— 没 schema 的
     工具，外部 agent 只能瞎填参数）；
  ④ schema 形状自检：`type=object` 且有 `properties`；`required` 里的键必须在
     `properties` 里（否则是**自相矛盾的契约**）。

★ 边界（如实）：这是**语法层契约**（参数名/类型/必填），**不**包含
  "这个工具什么时候该用"的语义判断 —— 那部分在 `guide` 的文字里。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
包根 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(包根 / "src"))
契约 = 包根 / "work" / "工具面契约.json"

失败: list[str] = []


def 判(条件: bool, 说明: str) -> None:
    print(("   ok  " if 条件 else "   ✗   ") + 说明)
    if not 条件:
        失败.append(说明)


def 生成() -> dict:
    from agentscope_orchestrator import taixu_mcp as M

    源 = pathlib.Path(M.__file__)
    工具们 = []
    for t in M.mcp._tool_manager.list_tools():
        参 = t.parameters if isinstance(t.parameters, dict) else {}
        工具们.append({
            "name": t.name,
            "描述": (t.description or "").strip(),
            "inputSchema": 参,
            "required": list(参.get("required") or []),
            "参数数": len(参.get("properties") or {}),
        })
    工具们.sort(key=lambda x: x["name"])
    return {
        "包": "agentscope_orchestrator",
        "源文件": 源.name,
        "源sha256_12": hashlib.sha256(源.read_bytes()).hexdigest()[:12],
        "工具数": len(工具们),
        "工具": 工具们,
        "★口径": ("从活的 FastMCP 注册表现算（不是读源码猜）；"
                  "只含**语法层**契约（名字/描述/参数 schema），不含'何时该用'的语义。"),
    }


def main() -> int:
    # ★ 2026-11-19 #51 抓到洞：原来「生成 → 写盘 → 与内存里的果比覆盖」是**恒真**，
    #   契约文件被改成旧名字/过期 sha 也照样绿。契约是**交付物**：外部 agent 读的是
    #   仓库里那份文件 ⇒ 必须先读**已落盘**那份再比。
    旧 = None
    if 契约.is_file():
        try:
            旧 = json.loads(契约.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            旧 = None
    果 = 生成()
    契约.parent.mkdir(parents=True, exist_ok=True)
    契约.write_text(json.dumps(果, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"   已写 {契约.name}：工具数 = {果['工具数']} · 源 = {果['源文件']}"
          f"@{果['源sha256_12']}")

    # ① 独立进程解析（**不**经过本进程的对象）
    码 = ("import json,sys,pathlib;"
          f"d=json.loads(pathlib.Path({str(契约)!r}).read_text(encoding='utf-8'));"
          "print(len(d['工具']))")
    cp = subprocess.run([sys.executable, "-B", "-c", 码], capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=300)
    独立数 = int((cp.stdout or "0").strip() or 0)
    判(独立数 == 果["工具数"] and 独立数 > 0,
       f"① 独立进程解析成功且条数一致（{独立数} vs {果['工具数']}）")

    # ② 覆盖：与注册表现算集合逐一比对
    from agentscope_orchestrator import taixu_mcp as M
    活名 = sorted(t.name for t in M.mcp._tool_manager.list_tools())
    契约名 = [t["name"] for t in 果["工具"]]
    判(契约名 == 活名, f"② 覆盖全部工具（契约 {len(契约名)} · 注册表 {len(活名)} · 逐一相等）")
    if 契约名 != 活名:
        缺 = set(活名) - set(契约名)
        多 = set(契约名) - set(活名)
        print(f"      缺 {sorted(缺)[:5]} · 多 {sorted(多)[:5]}")

    # ②′ **已落盘**契约 == 注册表（防「发出去的那份是旧的/被改过的」）
    if 旧 is None:
        判(False, "②′ 契约文件缺失或不可解析（交付物本身不合格）")
    else:
        旧名 = sorted(str(t.get("name")) for t in (旧.get("工具") or []))
        判(旧名 == 活名, f"②′ 已落盘契约 == 注册表（文件 {len(旧名)} · 活 {len(活名)}）")
        判(str(旧.get("源sha256_12") or "") == str(果["源sha256_12"]),
           f"②″ 契约里的源 sha == 当前源码（旧 {旧.get('源sha256_12')} · 现 {果['源sha256_12']}）")

    # ③ 每个工具三件套齐全
    缺三 = [t["name"] for t in 果["工具"]
            if not t["name"] or not t["描述"] or not isinstance(t["inputSchema"], dict)]
    判(not 缺三, f"③ 每个工具都有 名字/描述/inputSchema（缺 {len(缺三)} 个{缺三[:3]}）")

    # ④ schema 自洽
    坏: list[str] = []
    for t in 果["工具"]:
        s = t["inputSchema"]
        if s.get("type") != "object":
            坏.append(f"{t['name']}: type≠object")
            continue
        props = set((s.get("properties") or {}).keys())
        req = set(s.get("required") or [])
        if not props and req:
            坏.append(f"{t['name']}: 没有 properties 却要求必填 {sorted(req)[:2]}")
        elif req - props:
            坏.append(f"{t['name']}: required 里有 properties 没有的键 {sorted(req - props)[:2]}")
    判(not 坏, f"④ schema 自洽（坏 {len(坏)} 个{坏[:3]}）")

    print(f"   工具数 {果['工具数']} · 平均参数 {sum(t['参数数'] for t in 果['工具']) / max(1, 果['工具数']):.1f}")
    print(f"判定={'OK' if not 失败 else '不达标'}")
    for x in 失败:
        print(f"   ⇒ {x}")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(main())
