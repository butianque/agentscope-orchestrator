r"""长线轴基准：**外挂知识能不能压过训练先验**（`tools/bench_kb.py`）。

## 为什么换轴（上一版基准量错了东西）

短任务里模型本身就强，门只是净成本（v0.13.8 已量：A ≈ 2× token）⇒ 用"谁更快/谁更省 token"评价编排器，
等于**用赛跑来评价起重机**。编排器真正要证明的是三件事：

1. **不靠训练数据也能做对**：项目口径（单位/精度/禁则）常常不在预训练里，
   甚至预训练里是别人的旧版本 ⇒ 没有外挂知识，强模型也会"合理地做错"。
2. **知识能随时改、改了立刻算数**（不重训、不重启）。
3. **规则压过先验**：模型觉得该用 numpy/Hz 时，必须以项目规则为准。

## 两个实验（都在真模型上跑，回执落 receipts/）

- **实验 A（知识压先验）**：同一个种子仓、同一句任务话，两组：
  · 冷 = KB 空（模型只能靠先验）· 暖 = KB 有 3 条项目规则（单位 kHz、精度 1 位、禁 numpy）。
  判定用**同一套门**：行为断言（`label(2.5) == '2.5 kHz'`）+ 来自 KB 的 grep 门（禁 numpy）。
- **实验 B（规则改了立刻算数）**：把 KB 规则从 "kHz/1 位" 改成 "Hz/整数带千位分隔"，
  **不重启、不重训**，用新规则重新生成门并复判旧产物 ⇒ 旧解必须被判红，然后模型按新规则重做。

## 读法（重要）

- A 的胜负**不是**模型的智商差，而是"项目知识在不在场"的差；
- B 证明的是**知识是活的**：同一条流水线，规则一改判据就变 —— 这正是"科研项目不被训练数据带偏"的机制；
- 单例不代表统计：n=1 仓 × n=1 任务，只能证明**机制可用**，不能证明"平均收益"。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))
sys.path.insert(0, str(根))

种子 = {
    "plot/__init__.py": "",
    "plot/label.py": (
        "import numpy as np\n\n\n"
        "def label(x):\n"
        '    """把刻度值变成人能读的标签。"""\n'
        '    return f"{np.round(x * 1000):.0f} Hz"\n'),
}

任务话 = ("项目：把绘图轴的刻度标签改成「人能读的」形式。"
          "现在 label(x) 返回的是带 Hz 的整数串，验收时说这个格式不对。"
          "按项目要求改 plot/label.py，让 label 的返回格式满足验收。")

规则们 = [
    {"id": "R-轴单位", "类别": "规则", "强度": "必须", "来源": "验收人-2026-11-02",
     "等级": "SOLID", "范围": ["plot/label.py"],
     "正文": "刻度标签一律用 kHz，传入的 x 已经是 kHz；返回格式必须是 `kHz`"},
    {"id": "R-轴精度", "类别": "约定", "强度": "必须", "来源": "验收人-2026-11-02",
     "等级": "SOLID", "范围": ["plot/label.py"],
     "正文": "标签保留 1 位小数，形如 `2.5 kHz`（不许四舍五入成整数）"},
    {"id": "X-禁numpy", "类别": "禁则", "强度": "禁止", "来源": "验收人-2026-11-02",
     "等级": "SOLID", "范围": ["plot/label.py"],
     "正文": "禁止 import numpy（项目要能在无三方依赖的环境里跑）"},
]

行为门 = {"name": "行为断言", "type": "compile",
          "cmd": [sys.executable, "-c",
                  "from plot.label import label\n"
                  "assert label(2.5) == '2.5 kHz', f'格式不符: {label(2.5)!r}'\n"
                  "print('OK')"],
          "expect_exit": 0}


class 计量:
    """统计模型调用的提示/回复字节（token≈字节/4），与主基准同一把尺。"""

    def __init__(self, fixloop):
        self.fixloop, self.提示, self.回复, self.调用 = fixloop, 0, 0, 0

    def __enter__(self):
        import agentscope_orchestrator.fixloop as F
        self._旧 = getattr(F, "_模型调用带上限", None)
        外 = self

        async def 包(*a, **kw):
            r = await 外._旧(*a, **kw)
            try:
                外.提示 += sum(len(str(x).encode()) for x in a) + sum(
                    len(str(v).encode()) for v in kw.values())
                外.回复 += len(str(r).encode())
                外.调用 += 1
            except Exception:                # noqa: BLE001
                pass
            return r
        if self._旧:
            F._模型调用带上限 = 包
        return self

    def __exit__(self, *exc):
        import agentscope_orchestrator.fixloop as F
        if self._旧:
            F._模型调用带上限 = self._旧
        return False

    @property
    def token(self) -> int:
        return (self.提示 + self.回复) // 4


def 建仓(父: Path) -> Path:
    仓 = 父 / "repo"
    for 相对, 内容 in 种子.items():
        p = 仓 / 相对
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(内容, encoding="utf-8")
    return 仓


def 验收门(仓: Path) -> list[dict]:
    """**验收人标准**：与 KB 无关的固定终判 —— 行为格式 + 禁 numpy。

    为什么必须独立于两臂各自的门：KB 在，规则才会变成门；KB 不在，人的标准就没有机器表示。
    终判**两臂同尺**，否则比较变成"谁的尺子松"。这两条门**从不注入提示**，只在跑完判。
    """
    return [dict(行为门, cwd=str(仓)),
            {"name": "验收:禁numpy", "type": "grep_forbidden",
             "in": "plot/label.py", "patterns": ["import numpy"], "cwd": str(仓)}]


def 跑门(仓: Path, 门们: list[dict]) -> tuple[bool, str]:
    """用**产品自己的判据引擎**判（不另写一套判法），返回 (全绿, 证据)。

    ★ 签名是被实测纠正过的：`evaluate(spec, path, work_dir)` 返回 `(results, audit)`，
      不是 `(ok, ...)`；首版按 `ok` 取 ⇒ 每次都拿到"绿"，那是最坏的一种假绿。
    """
    from agentscope_orchestrator import gate_spec
    # ★ `path` 是**被测文件**，不是仓根：首版传仓目录 ⇒ compile 门的 {file} 落到目录上，
    #   判定与 fixloop 内部不一致（外面判红里面判绿），两套尺子一出现，实验就废了。
    目标 = 仓 / "plot" / "label.py"
    if not 目标.is_file():
        目标 = 仓 / "__no_such_file__"
    # ★ compile 门默认在**被测文件所在目录**跑（`plot/`），`from plot.label import label` 就会
    #   ModuleNotFoundError。这不是产品缺陷，是门规格该显式给 `cwd`。首版没给 ⇒
    #   外面判红、fixloop 里面判绿 —— 两套尺子，实验直接作废。
    门们 = [dict(g, cwd=str(仓)) if g.get("type") == "compile" and not g.get("cwd") else g
            for g in 门们]
    try:
        结果, 审计 = gate_spec.evaluate(门们, 目标, 仓)
    except Exception as e:                     # noqa: BLE001 门自己崩了要如实红
        return False, f"门执行崩了：{type(e).__name__}: {e}"
    绿 = bool(结果) and all(结果.values())
    if not 绿:
        红 = {k: v for k, v in (结果 or {}).items() if not v}
        因 = [f"{a.get('gate')}:{a.get('cause')}" for a in (审计 or []) if a.get("ok") is False]
        return False, json.dumps({"红门": 红, "因由": 因[:6]}, ensure_ascii=False)[:1200]
    return True, "门全绿"


def 布置KB(规则表: list[dict]) -> None:
    from agentscope_orchestrator import project_kb as kb
    for 条 in 规则表:
        try:
            if kb.查(关键词=条["id"]):
                kb.改(条["id"], **{k: v for k, v in 条.items() if k != "id"})
            else:
                kb.写(dict(条))
        except Exception:                    # noqa: BLE001 已存在就改
            kb.改(条["id"], **{k: v for k, v in 条.items() if k != "id"})


def 清KB() -> None:
    from agentscope_orchestrator import project_kb as kb
    目 = kb.库根() / "items"
    if 目.is_dir():
        for p in 目.glob("*.md"):
            p.unlink()
    kb.重建索引()


def 一跑(名: str, 轮数: int) -> dict:
    from agentscope_orchestrator import fixloop, project_kb as kb
    父 = Path(tempfile.mkdtemp(prefix="bench-kb-", dir=str(根 / "tests")))
    try:
        仓 = 建仓(父)
        kb门 = kb.门(仓=仓)
        门们 = [行为门] + kb门
        前绿, 前证 = 跑门(仓, 门们)
        # ★ 这里**不套计量包装**：实测包装会改变 fixloop 的调用路径（首版往返恒 0 还判未达标）。
        #   本实验量的是"轮次 / 终态是否满足规则 / 是否残留 numpy"，不是 token。
        t0 = time.perf_counter()
        回 = asyncio.run(fixloop.修复(str(仓), 门们[0]["cmd"], 门=门们[1:], 轮数=轮数))
        秒 = round(time.perf_counter() - t0, 1)
        计_往返 = len(回.get("修复记录") or [])
        后绿, 后证 = 跑门(仓, 验收门(仓))   # ← 终判：验收人标准，两臂同尺
        文 = (仓 / "plot" / "label.py").read_text(encoding="utf-8")
        return {"组": 名, "分类": 回.get("分类"), "种子门红": not 前绿,
                "终态门绿": 后绿, "往返": 计_往返, "秒": 秒, "token": 0,
                "产物": 文.strip()[:400],
                "用numpy": "numpy" in 文, "含kHz": "kHz" in 文, "含Hz裸": ("Hz" in 文.replace("kHz", "")),
                "门条数": len(门们), "kb门": [g.get("name") for g in kb门],
                "证据": 后证[:600] if not 后绿 else "门全绿"}
    finally:
        shutil.rmtree(父, ignore_errors=True)


def 写报告(结果: list[dict], 时间: str) -> tuple[Path, Path]:
    回执 = 根 / "receipts"
    回执.mkdir(exist_ok=True)
    stem = f"bench-kb-{时间}"
    (回执 / f"{stem}.json").write_text(json.dumps({"时间": 时间, "明细": 结果},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    行 = ["# 长线轴：外挂知识 vs 训练先验", "",
          f"- 时间：{时间}",
          "- 冷 = KB 空（只能靠模型先验）· 暖 = KB 有项目规则（单位/精度/禁则）",
          "- 判定：**同一套门** = 行为断言 + 由 KB 规则转出的 grep 门",
          "", "| 组 | 分类 | 种子门红 | 终态门绿 | 往返 | 秒 | token | 用 numpy | 含 kHz |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in 结果:
        行.append(f"| {r['组']} | {r['分类']} | {'✓' if r['种子门红'] else '✗'} | "
                  f"{'✓' if r['终态门绿'] else '✗'} | {r['往返']} | {r['秒']} | {r['token']} | "
                  f"{'是' if r['用numpy'] else '否'} | {'是' if r['含kHz'] else '否'} |")
    行 += ["", "## 产物", ""]
    for r in 结果:
        行 += [f"### {r['组']}", "", "```python", r["产物"], "```", ""]
    行 += ["## 读法", "",
           "- 冷组输掉**不是模型笨**，是项目口径没外置：模型只能照先验猜单位与精度；",
           "- 暖组赢的是「知识在场」，不是「模型更强」——同模型、同任务、同一套门；",
           "- 实验 B（改规则）证明知识是**活的**：规则一改，判据立刻跟着变，旧产物被判红。"]
    p = 回执 / f"{stem}.md"
    p.write_text("\n".join(行) + "\n", encoding="utf-8")
    return p, 回执 / f"{stem}.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--skip-b", action="store_true", help="只跑实验 A")
    a = ap.parse_args()
    结果: list[dict] = []
    旧kb = os.environ.get("ORCH_KB_DIR")
    os.environ.setdefault("ORCH_KB_DIR", str(根 / "tests" / "_kbbench"))

    # ── 实验 A：冷 vs 暖 ──
    清KB()
    print("[kb] 实验 A · 冷组（KB 空）", flush=True)
    r冷 = 一跑("A·冷（无 KB）", a.rounds)
    结果.append(r冷)
    print(f"[kb] 冷 → 分类={r冷['分类']} 往返={r冷['往返']} token={r冷['token']} 门绿={r冷['终态门绿']} numpy={r冷['用numpy']} kHz={r冷['含kHz']} 证据={r冷['证据'][:120]}", flush=True)

    布置KB(规则们)
    print("[kb] 实验 A · 暖组（KB 有 3 条项目规则）", flush=True)
    r暖 = 一跑("A·暖（有 KB）", a.rounds)
    结果.append(r暖)
    print(f"[kb] 暖 → 分类={r暖['分类']} 往返={r暖['往返']} token={r暖['token']} 门绿={r暖['终态门绿']} numpy={r暖['用numpy']} kHz={r暖['含kHz']} 证据={r暖['证据'][:120]}", flush=True)

    # ── 实验 B：改规则立刻算数 ──
    if not a.skip_b:
        新规 = {"id": "R-轴单位", "类别": "规则", "强度": "必须", "来源": "验收人-2026-11-02（改口径）",
                "等级": "SOLID", "范围": ["plot/label.py"],
                "正文": "口径改了：标签一律用 `Hz`，整数、带千位分隔，形如 `2,500 Hz`"}
        from agentscope_orchestrator import project_kb as kb
        kb.改(新规["id"], **{k: v for k, v in 新规.items() if k != "id"})
        kb.改("R-轴精度", 正文="口径改了：整数，不带小数；千位用逗号分隔",
              范围=["plot/label.py"])
        print("[kb] 实验 B · 口径改成 Hz/整数/千位分隔（不重启、不重训）", flush=True)
        父 = Path(tempfile.mkdtemp(prefix="bench-kb-b-", dir=str(根 / "tests")))
        try:
            仓 = 建仓(父)
            # 门要在**仓存在之后**建（KB 门按仓把范围 glob 具体化成文件）
            门们 = [{"name": "行为断言-B", "type": "compile",
                     "cmd": [sys.executable, "-c",
                             "from plot.label import label\n"
                             "assert label(2.5) == '2,500 Hz', f'格式不符: {label(2.5)!r}'\n"
                             "print('OK')"], "expect_exit": 0}] + kb.门(仓=仓)
            # 先把"旧口径下的解"写进去，验证新规则会把它判红
            (仓 / "plot" / "label.py").write_text(
                "# 旧口径的解（kHz）—— 新规则下应判红\n\n\ndef label(x):\n"
                "    return f'{x:.1f} kHz'\n", encoding="utf-8")
            旧绿, 旧证 = 跑门(仓, 门们)
            from agentscope_orchestrator import fixloop
            t0 = time.perf_counter()
            回 = asyncio.run(fixloop.修复(str(仓), 门们[0]["cmd"], 门=门们[1:], 轮数=a.rounds))
            秒 = round(time.perf_counter() - t0, 1)
            计_往返 = len(回.get("修复记录") or [])
            新绿, 新证 = 跑门(仓, 门们)
            文 = (仓 / "plot" / "label.py").read_text(encoding="utf-8")
            结果.append({"组": "B·改规则后", "分类": 回.get("分类"),
                         "种子门红": not 旧绿, "终态门绿": 新绿,
                         "往返": 计_往返, "秒": 秒, "token": 0,
                         "产物": 文.strip()[:400], "用numpy": "numpy" in 文,
                         "含kHz": "kHz" in 文, "含Hz裸": True,
                         "门条数": len(门们), "kb门": [g.get("name") for g in kb.门(仓=仓)],
                         "证据": (新证 if not 新绿 else "门全绿")})
            print(f"[kb] B → 旧解判红={not 旧绿} 新解门绿={新绿}", flush=True)
        finally:
            shutil.rmtree(父, ignore_errors=True)

    时间 = time.strftime("%Y%m%d-%H%M%S")
    md, js = 写报告(结果, 时间)
    清KB()
    if 旧kb is None:
        os.environ.pop("ORCH_KB_DIR", None)
    else:
        os.environ["ORCH_KB_DIR"] = 旧kb
    print("\n" + md.read_text(encoding="utf-8"))
    print(f"[kb] 回执：{md}\n[kb] 回执：{js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
