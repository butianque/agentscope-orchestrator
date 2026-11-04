r"""项目知识库的判据（`project_kb.py`）。

锁的是"**人写的外挂知识**"这条链上会悄悄坏掉的六处：
  ① 写进去能查出来（人是 Markdown，机器是索引，两边必须一致）；
  ② **改了就生效**：版本自增 + 变更日志留痕（否则"随时改"变成"改了不知道改成啥"）；
  ③ **删掉索引不丢知识**：索引是缓存，从人写的 md 能全量重建；
  ④ **冲突不许静默**：同 id 重复 / "必须 X"与"禁止 X"同文 ⇒ 报冲突；
  ⑤ **范围 glob 生效**：只在 `*.py` 生效的规则，不该注入到别的路径；
  ⑥ **可机判门**：禁则/必须 能转成 grep 门（注入只是提示，门才是硬约束）。
"""
from __future__ import annotations

import os
import shutil
import atexit
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))
from agentscope_orchestrator import project_kb as kb  # noqa: E402

失败: list[str] = []


def 断言(条件, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}", flush=True)
    else:
        失败.append(说明)
        print(f"  FAIL {说明}", flush=True)


def main() -> int:
    临时 = Path(tempfile.mkdtemp(prefix="kb-test-", dir=str(Path(__file__).parent)))
    atexit.register(shutil.rmtree, 临时, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    旧 = os.environ.get("ORCH_KB_DIR")
    os.environ["ORCH_KB_DIR"] = str(临时)
    try:
        # ① 写入可查
        kb.写({"id": "R-单位-kHz", "类别": "规则", "强度": "必须", "来源": "验收人-2026-11-02",
               "等级": "SOLID", "范围": ["*.py"], "正文": "绘图刻度一律 kHz，禁止出现 `Hz` 字样的裸标签"})
        断言(len(kb.查(关键词="kHz")) == 1, "写进去能按关键词查出来")
        断言(Path(kb._文路径("R-单位-kHz")).is_file(), "落盘为**人可读的 Markdown**")

        # 同 id 再写必须拒绝（避免静默覆盖）
        拒 = False
        try:
            kb.写({"id": "R-单位-kHz", "类别": "规则", "正文": "覆盖掉它"})
        except FileExistsError:
            拒 = True
        断言(拒, "同 id 重复写被拒（要改必须走 改()）")

        # ② 改了就生效 + 版本/日志
        新 = kb.改("R-单位-kHz", 正文="刻度一律 kHz；标签格式 `{v:.1f} kHz`；禁止裸 `Hz`")
        断言(新["版本"] == 2 and len(新.get("变更") or []) == 1, "改后版本自增且留变更日志")
        断言("2.1 kHz" in (kb.查(关键词="kHz")[0].get("正文") or "") or "1f" in
             (kb.查(关键词="kHz")[0].get("正文") or ""), "改后的正文立刻能查到")

        # ③ 删索引不丢知识
        kb._索引路径().unlink(missing_ok=True)
        断言(len(kb.查(关键词="kHz")) == 1, "删掉索引后仍能查（从 md 自动重建）")

        # ④ 冲突不静默
        kb.写({"id": "X-禁numpy", "类别": "禁则", "强度": "禁止", "范围": ["*.py"],
               "正文": "禁止 import numpy"})
        kb.写({"id": "X-禁numpy-2", "类别": "规则", "强度": "必须", "范围": ["*.py"],
               "正文": "禁止 import numpy"})
        冲 = kb.冲突()
        断言(any(c["型"] == "必须与禁止同文" for c in 冲), f"必须/禁止同文报冲突（实得 {冲}）")

        # ⑤ 范围 glob
        断言(kb.范围命中({"范围": ["*.py"]}, "a/b/c.py") and not kb.范围命中({"范围": ["*.py"]}, "a/b.md"),
             "范围 glob 命中/不命中正确")

        # ⑥ 可机判门
        门们 = kb.门()
        断言(any(g["type"] == "grep_forbidden" and any("numpy" in p for p in g["patterns"]) for g in 门们),
             f"禁则转成 grep_forbidden 门（实得 {len(门们)} 条）")

        # 注入块带优先级声明，且不超预算
        块 = kb.注入(上限字=300)
        断言("优先于你的训练先验" in 块, "注入块带优先级声明")
        断言(len(块) <= 300 + 80, f"注入块守预算（实得 {len(块)} 字）")

        # ⑦ 交付标准：**装进智能体里 AI 知道怎么用** —— MCP 面必须有这个工具，且能真调通。
        #    只测源码里有字符串不算："AI 知道怎么用"必须能被调用方**真的调用**。
        try:
            import json as _json
            from agentscope_orchestrator import taixu_mcp as _mcp
            工 = getattr(_mcp, "kb", None)
            断言(callable(工), "MCP 面暴露 kb 工具")
            if callable(工):
                出 = _json.loads(工(action="统计"))
                断言(出.get("ok") and 出.get("条数", 0) >= 1, f"kb(action='统计') 真能调通（{出}）")
                门出 = _json.loads(工(action="门", repo=str(临时)))
                断言(isinstance(门出.get("门"), list), "kb(action='门') 返回门列表")
        except Exception as exc:                 # noqa: BLE001
            断言(False, f"MCP kb 工具不可用：{type(exc).__name__}: {exc}")
    finally:
        if 旧 is None:
            os.environ.pop("ORCH_KB_DIR", None)
        else:
            os.environ["ORCH_KB_DIR"] = 旧
        shutil.rmtree(临时, ignore_errors=True)

    if 失败:
        print(f"\nproject_kb：红 {len(失败)} 项 —— " + "；".join(失败))
        return 1
    print("\nproject_kb：绿 —— 可写可改可删索引、冲突不静默、规则可转门")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
