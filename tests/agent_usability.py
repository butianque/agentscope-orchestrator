#!/usr/bin/env python
"""面向**智能体消费者**的可用性标定（tests/agent_usability.py）

存在的理由：交付对象不再是人类，而是"装进智能体里的 AI"。所以验收标准必须换成
**它能测到的东西**，而不是文档写得漂不漂亮：

① 一次往返拿结论（`orch_run wait=True`）
② 非达标回执带**可直接执行**的修法
③ 责任归属可读（调用方侧 / 执行侧 / 环境）
④ 载荷经济（决策摘要 ≤1KB，细节另存）
⑤ 三态判决（证明/反例/**未知**，未知绝不判绿）
⑥ 怀疑接口（orch_object）：敏感判据抓得住、钝判据如实说抓不住、双验入册与留痕
⑦ 症状路由（guide 里"我遇到 X 该调谁"能查到）

★ 门槛写在文件里，绿是跑出来的，不是我说的。
"""

from __future__ import annotations

import asyncio
import json
import os
import atexit
import shutil
import sys
import tempfile
import time
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(根 / "src"))

桶 = Path(tempfile.mkdtemp(prefix="au_"))
atexit.register(shutil.rmtree, 桶, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
os.environ["ORCH_STATE_DIR"] = str(桶 / "state")

from agentscope_orchestrator import agentface  # noqa: E402
from agentscope_orchestrator import gate_spec as gs  # noqa: E402

通过: list[str] = []
失败: list[str] = []


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}" + (f"：{说明}" if 说明 else ""))


def main() -> int:
    print("agent_usability：AI 消费方的六个读数")
    仓 = 桶 / "repo"
    仓.mkdir()
    (仓 / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef big(s):\n"
        "    if len(s) > 3:\n        return True\n    return False\n", encoding="utf-8")
    验 = ["python", "-c",
         "import sys; sys.path.insert(0,'.'); import calc; assert calc.add(1,2)==3"]
    产物 = 仓 / "_cand.out"
    产物.write_text("x", encoding="utf-8")

    # ── ⑤ 三态：证明 / 反例 / 未知，且"未知"绝不判绿 ──
    证 = [{"name": "o", "type": "oracle", "cmd": ["python", "-c", "print('ok')"]}]
    反 = [{"name": "o", "type": "oracle", "cmd": ["python", "-c",
          "import sys; print('counterexample: a=1'); sys.exit(1)"],
          "witness_re": r"counterexample: .*"}]
    未 = [{"name": "o", "type": "oracle", "cmd": ["python", "-c",
          "import sys; print('nope'); sys.exit(1)"]}]
    r1, a1 = gs.evaluate(证, 产物, 仓, str(仓), 仓)
    r2, a2 = gs.evaluate(反, 产物, 仓, str(仓), 仓)
    r3, a3 = gs.evaluate(未, 产物, 仓, str(仓), 仓)
    判("⑤ 证明 → 绿", r1["o"] is True and a1[0].get("三态") == "证明")
    判("⑤ 反例 → 红且因由带反例原文",
       r2["o"] is False and str(a2[0].get("cause", "")).startswith("反例|"),
       f"cause={a2[0].get('cause')}")
    判("⑤ 未知 → 红（**未知不等于绿**）且因由可区分",
       r3["o"] is False and str(a3[0].get("cause", "")).startswith("未知|"),
       f"cause={a3[0].get('cause')}")

    # ── ⑥ 怀疑接口：敏感判据抓住 / 钝判据如实说抓不住 ──
    t0 = time.time()
    r敏 = asyncio.run(agentface.实现(str(仓), "怀疑这个绿是假的", 验))
    敏 = r敏.get("区分力") or {}
    判("⑥ 敏感判据：抓住机械变异并点名文件",
       isinstance(敏, dict) and bool(敏.get("抓住")) and 敏.get("文件") == "calc.py",
       f"抓住={敏.get('抓住')} 文件={敏.get('文件')}")
    r钝 = asyncio.run(agentface.实现(str(仓), "怀疑这条判据太弱",
                                  ["python", "-c", "print('always green')"]))
    钝 = r钝.get("区分力") or {}
    判("⑥ 钝判据：如实说抓不住（不许谎报区分力）",
       isinstance(钝, dict) and not 钝.get("抓住") and int(钝.get("试过变异数", 0)) > 0,
       f"试过={钝.get('试过变异数')}")
    判("⑥ 双验通过入册（判据册.jsonl 有行）",
       (桶 / "state" / "判据册.jsonl").exists())
    判("⑥ 回执留痕成文件（跨会话可回读）",
       len(list((桶 / "state" / "receipts").glob("*.json"))) >= 2,
       f"文件数={len(list((桶 / 'state' / 'receipts').glob('*.json')))}")
    r红 = asyncio.run(agentface.实现(str(仓), "应该已经坏了",
                                  ["python", "-c", "import sys; sys.exit(3)"]))
    判("⑥ 现状红 → 分类=未达标 + 责任归属=执行侧 + 修法可执行",
       r红.get("分类") == "未达标" and r红.get("责任归属") == "执行侧"
       and (r红.get("修法") or {}).get("工具") == "orch_fix")
    总秒 = round(time.time() - t0, 1)
    判("⑥ 一次怀疑的往返代价 ≤ 30 s（AI 等得起）", 总秒 <= 30, f"{总秒}s")

    # ── ⑥b 候选源必须按**相关性**挑，不许被"最新文件"带偏 ──
    #   真事故（活体）：按 mtime 倒序 ⇒ 挑中测试残留的探针（`tmpXXXX/x.py`，0 行逻辑）
    #   ⇒ 变异一个都挂不上 ⇒ 回执说"判据不敏感"，而真正被判断的模块**根本没被试过**。
    垃圾 = 仓 / "tmp_probe_dir"
    垃圾.mkdir(exist_ok=True)
    (垃圾 / "x.py").write_text("z = 1\n" * 500, encoding="utf-8")
    (仓 / "_t_fresh.py").write_text("def f():\n    return True\n" * 200, encoding="utf-8")
    候选 = agentface._候选源(仓, 验)
    名 = [str(p.relative_to(仓)).replace("\\", "/") for p in 候选]
    判("⑥b 候选源按相关性挑（点名模块进前列）",
       bool(名) and 名[0].endswith("calc.py"), f"前二={名[:2]}")
    判("⑥b 候选源剔除临时文件（tmp*/_t_*）",
       not any("tmp_probe_dir" in x or x.startswith("_t_") for x in 名), f"{名}")
    (垃圾 / "x.py").unlink()
    垃圾.rmdir()
    (仓 / "_t_fresh.py").unlink()

    # ── ④ 载荷经济：决策摘要 ≤1KB ──
    尺寸 = [len(json.dumps(x["决策摘要"], ensure_ascii=False).encode())
            for x in (r敏, r钝, r红)]
    判("④ 决策摘要 ≤1024 字节（AI 上下文友好）", max(尺寸) <= 1024, f"三种回执={尺寸}")
    判("④ 摘要含可执行下一步",
       all(x["决策摘要"].get("下一步") for x in (r敏, r钝, r红)))

    # ── ⑦ 症状路由（guide）──
    from agentscope_orchestrator import taixu_mcp as M
    契约 = asyncio.run(M.taixu_guide())
    文 = 契约 if isinstance(契约, str) else json.dumps(契约, ensure_ascii=False)
    判("⑦ guide 含症状路由表", "症状路由" in 文)
    判("⑦ guide 含历史查询入口（会话断了能接上）",
       "历史查询" in 文 and "receipts" in 文)
    判("⑦ guide 已登记 orch_object", "orch_object" in 文)
    判("⑦ guide 载荷 < 12KB（一次往返给契约，别贪）",
       len(文.encode()) < 12 * 1024, f"{len(文.encode())} 字节")

    print()
    if 失败:
        print(f"agent_usability：{len(失败)} 项红 —— {失败}")
        return 1
    print(f"agent_usability：{len(通过)}/{len(通过)} 全绿"
          "（三态 · 怀疑接口 · 载荷 · 留痕 · 症状路由）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
