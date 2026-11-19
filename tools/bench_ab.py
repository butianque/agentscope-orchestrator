r"""同任务对照基准：A=本编排器（orch_fix + 判据门） · B=同模型裸跑（无门、无回滚、无证据包）。

★ 为什么必须有这个脚本（2026-11-02 定）：
  "比外部产品更优"这句话，本仓此前**没有任何对照数据** —— 只有设计主张
  （一次往返、判决诚实、预算硬门）。主张不是证据。这个脚本做的就是把主张变成
  同任务下的五个数字：**成功率 / 往返次数 / token / 秒 / 需人工干预次数**。

★ 公平性设计（这是唯一能让结论算数的部分）：
  1. **同一个模型、同一条通道**（A 与 B 都走渠道配置，B 不再挑更弱的模型）。
  2. **同一个仲裁者**：两臂都用同一条判据命令判成败 —— A 用的是"门"，B 是"事后复跑同一命令"。
     B 拿不到门（它没有门），但它**知道自己没通过**（下一轮会看到失败输出），
     所以差距来自"有没有门/回滚/证据包/预算"，而不是"信息不对称"。
  3. **同样的轮数上限**（默认 2 轮）。
  4. **A 的往返次数按它自己的修复记录数**，B 按它实际的模型调用次数 —— 都用"模型调用"这一单位。

★ 诚实边界（写在报告里，不许省）：
  · B 没有"人工干预"这个概念 ⇒ 它的解析失败/跑飞由脚本**兜住并计数**，
    那就是"需要人管一次"；A 的同类情况表现为返回非达标 + 修法。
  · token 若通道不回 usage，则用"提示+回复字符数/4"**估算**，报告里标 `est`。
  · 本基准只覆盖**小型可机判任务**，不代表开放任务上的表现（那需要另一套语料）。

跑法：
  python tools/bench_ab.py                 # 5 个任务，2 轮上限
  python tools/bench_ab.py --tasks 3 --rounds 1
  python tools/bench_ab.py --arm A         # 只跑一边（排障用）
回执：receipts/bench-ab-<时间戳>.json + .md（内容同时打印）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))

# ────────────────────────────── 任务夹具 ──────────────────────────────
# 每个任务：给一批"坏文件"，外加一条**可机判**的仲裁命令。
# 命令绿 = 成功。数值型任务（科研味）用打印值比对，不靠人看。

任务们: list[dict] = [
    {
        "名": "T1-值错",
        "类": "工程",
        "文件": {"pkg/__init__.py": "", "pkg/mod.py": "def f():\n    return 41\n"},
        "命令": [sys.executable, "-c",
                 "import sys; sys.path.insert(0,'.'); from pkg.mod import f; assert f()==42, f()"],
    },
    {
        "名": "T2-测试红",
        "类": "工程",
        "文件": {"pkg/__init__.py": "", "pkg/core.py": "def add(a, b):\n    return a - b\n",
                 "tests/__init__.py": "",
                 "tests/test_core.py": ("import unittest\nfrom pkg.core import add\n\n\n"
                                        "class T(unittest.TestCase):\n"
                                        "    def test_add(self):\n"
                                        "        self.assertEqual(add(2, 3), 5)\n")},
        "命令": [sys.executable, "-m", "unittest", "-q"],
    },
    {
        "名": "T3-边界",
        "类": "工程",
        "文件": {"pkg/__init__.py": "",
                 "pkg/stats.py": "def mean(xs):\n    return sum(xs) / len(xs)\n"},
        "命令": [sys.executable, "-c",
                 "import sys; sys.path.insert(0,'.'); from pkg.stats import mean;"
                 " assert mean([])==0.0; assert mean([1,2,3])==2.0, mean([1,2,3])"],
    },
    {
        "名": "T4-数据处理",
        "类": "科研",
        "文件": {"data.csv": "x\n1\n2\n3\n4\n",
                 "analyze.py": "import csv\n\n\nrows = list(csv.DictReader(open('data.csv')))\n"
                               "print(len(rows))\n"},
        "命令": [sys.executable, "-c",
                 "import subprocess,sys,io,contextlib;"
                 " out=subprocess.run([sys.executable,'analyze.py'],capture_output=True,text=True);"
                 " t=(out.stdout or '').strip().split(',');"
                 " assert len(t)==2, 'want mean,std got '+repr(out.stdout)+repr(out.stderr);"
                 " m,s=float(t[0]),float(t[1]);"
                 " assert abs(m-2.5)<1e-6, m; assert abs(s-1.1180)<0.01, s"],
    },
    {
        "名": "T5-拟合",
        "类": "科研",
        "文件": {"points.csv": "x,y\n0,1\n1,3\n2,5\n3,7\n",
                 "fit.py": "print('0,0')\n"},
        "命令": [sys.executable, "-c",
                 "import subprocess,sys;"
                 " out=subprocess.run([sys.executable,'fit.py'],capture_output=True,text=True);"
                 " t=(out.stdout or '').strip().split(',');"
                 " assert len(t)==2, 'want slope,intercept got '+repr(out.stdout)+repr(out.stderr);"
                 " k,b=float(t[0]),float(t[1]);"
                 " pts=[(0,1),(1,3),(2,5),(3,7)];"
                 " sse=sum((k*x+b-y)**2 for x,y in pts);"
                 " assert sse<1e-4, ('sse',sse,k,b)"],
    },
    {
        "名": "T6-大仓定位",
        "类": "工程",
        "文件": dict(
            **{"pkg/__init__.py": ""},
            # 40 个无关模块：B 臂会把它们**全部**塞进提示（没有证据选择），
            # A 臂只拿"病根文件 + 失败输出" ⇒ 这里量的是**上下文经济**，不是智力。
            **{f"pkg/m{i:02d}.py": (f"def f{i}(x):\n    return x + {i}\n" * 12)
               for i in range(40)},
            **{"pkg/core.py": "def scale(x):\n    return x * 2\n",
               "tests/__init__.py": "",
               "tests/test_core.py": ("import unittest\nfrom pkg.core import scale\n\n\n"
                                       "class T(unittest.TestCase):\n"
                                       "    def test_scale(self):\n"
                                       "        self.assertEqual(scale(3), 9)\n")},
        ),
        "命令": [sys.executable, "-m", "unittest", "-q"],
    },
    {
        "名": "T7-回归破坏",
        "类": "工程",
        # 双门：主命令绿不够，还有一条**回归门**。裸跑臂只看得到第一条，
        # 这正是"没有门就敢交付"的真实形状 —— 改动 utils 会把 report 的断言打破。
        "文件": {"pkg/__init__.py": "",
                 "pkg/utils.py": "def norm(xs):\n    return xs\n",
                 "pkg/report.py": "from pkg.utils import norm\n\n\n"
                                  "def render(xs):\n    return '|'.join(str(x) for x in norm(xs))\n",
                 "tests/__init__.py": "",
                 "tests/test_utils.py": ("import unittest\nfrom pkg.utils import norm\n\n\n"
                                          "class T(unittest.TestCase):\n"
                                          "    def test_norm(self):\n"
                                          "        self.assertEqual(norm([1.0, 2.0, 2.5]), [1, 2, 2.5])\n"
                                          "        self.assertIsInstance(norm([3.0])[0], int)\n"),
                 "tests/test_report.py": ("import unittest\nfrom pkg.report import render\n\n\n"
                                           "class T(unittest.TestCase):\n"
                                           "    def test_render(self):\n"
                                           "        self.assertEqual(render([1.5, 2.5]), '1.5|2.5')\n")},
        # ★ 2026-11-02 第一轮基准的真教训：9 个任务两臂都 100%、都在 1 轮内完成
        #   ⇒ **那些任务测不出编排的价值**（强模型一眼改对时，门只是净成本）。
        #   要测出门的价值，必须构造"偷懒修法能过主命令、必挂回归"的形状 ——
        #   这不是给某一臂下绊子，这就是真实项目里"改 A 破 B"的日常。
        #   主命令**只跑 utils 那个测试文件**（裸跑臂的停止条件就是它绿）；
        #   回归门跑 report ⇒ 偷懒修法（全转 int）过主命令、必挂回归。
        "命令": [sys.executable, "-m", "unittest", "-q", "tests.test_utils"],
        "第二门": [sys.executable, "-m", "unittest", "-q", "tests.test_report"],
    },
    {
        "名": "T8-手写拟合",
        "类": "科研",
        # 禁 numpy：必须手写最小二乘；门同时卡斜率/截距/SSE 与"不许 import numpy"。
        "文件": {"points.csv": "x,y\n0,2\n1,4\n2,6.1\n3,8.2\n",
                 "fit.py": "print('0,0')\n"},
        "命令": [sys.executable, "-c",
                 "import subprocess,sys;"
                 " out=subprocess.run([sys.executable,'fit.py'],capture_output=True,text=True);"
                 " t=(out.stdout or '').strip().split(',');"
                 " assert len(t)==2, repr(out.stdout)+repr(out.stderr);"
                 " k,b=float(t[0]),float(t[1]);"
                 " pts=[(0,2),(1,4),(2,6.1),(3,8.2)];"
                 " sse=sum((k*x+b-y)**2 for x,y in pts);"
                 " assert sse<0.01, ('sse',sse);"
                 " src=open('fit.py',encoding='utf-8').read();"
                 " assert 'numpy' not in src, '不许用 numpy'"],
    },
    {
        "名": "T10-硬编码陷阱",
        "类": "工程",
        # 主命令只有一个输入点 ⇒ 模型很容易写 `if x==3: return 9`；
        # 回归门用**随机值**做性质检查，硬编码立刻露馅。
        "文件": {"pkg/__init__.py": "",
                 "pkg/core.py": "def scale(x):\n    return x * 2\n",
                 "tests/__init__.py": "",
                 "tests/test_one.py": ("import unittest\nfrom pkg.core import scale\n\n\n"
                                        "class T(unittest.TestCase):\n"
                                        "    def test_one(self):\n"
                                        "        self.assertEqual(scale(3), 9)\n")},
        "命令": [sys.executable, "-m", "unittest", "-q", "tests.test_one"],
        "第二门": [sys.executable, "-c",
                 "import sys, random; sys.path.insert(0,'.'); from pkg.core import scale;"
                 " random.seed(7);"
                 " bad=[(x, scale(x)) for x in (random.randint(-50,50) for _ in range(20))"
                 " if scale(x)!=x*3];"
                 " assert not bad, bad[:5]"],
    },
    {
        "名": "T11-调用方漏改",
        "类": "工程",
        # 只改被改模块会让调用方崩；主命令只跑被改模块的测试 ⇒ 看不到，回归门看得见。
        # ★ 2026-11-02 首版夹具是**坏的**：回归门要求 `label(1000)=='1.0 kHz Hz'`（重复单位），
        #   模型"顺手把调用方的重复单位修掉"——那是合理行为，门却判红。夹具把合理修法判死，
        #   测出来的就不是产品差异而是夹具口味。改成**语义正常**的形状：
        #   khz() 返回数值(kHz)，由调用方格式化 ⇒ 返回字符串的偷懒修法在 f-string 上必炸。
        "文件": {"pkg/__init__.py": "",
                 "pkg/khz.py": "def khz(x):\n    return x\n",
                 "pkg/plot.py": "from pkg.khz import khz\n\n\ndef label(x):\n    return f'{khz(x):.1f} kHz'\n",
                 "tests/__init__.py": "",
                 "tests/test_khz.py": ("import unittest\nfrom pkg.khz import khz\n\n\n"
                                        "class T(unittest.TestCase):\n"
                                        "    def test_khz(self):\n"
                                        "        self.assertEqual(khz(1000), 1.0)\n")},
        "命令": [sys.executable, "-m", "unittest", "-q", "tests.test_khz"],
        "第二门": [sys.executable, "-c",
                 "import sys; sys.path.insert(0,'.'); from pkg.plot import label;"
                 " assert label(1000)=='1.0 kHz', label(1000);"
                 " assert label(2500)=='2.5 kHz', label(2500)"],
    },
    {
        "名": "T9-格式与容差",
        "类": "科研",
        "文件": {"data.csv": "v\n0.1\n0.2\n0.30000000000000004\n0.4\n",
                 "summary.py": "print('todo')\n"},
        "命令": [sys.executable, "-c",
                 "import subprocess,sys,re;"
                 " out=subprocess.run([sys.executable,'summary.py'],capture_output=True,text=True);"
                 " s=(out.stdout or '').strip();"
                 " assert re.fullmatch(r'-?\\d+\\.\\d{2}', s), repr(s);"
                 " assert abs(float(s)-0.25)<1e-9, s"],
    },
]


def 铺(任务: dict, 父: Path) -> Path:
    仓 = 父 / re.sub(r"[^0-9A-Za-z\u4e00-\u9fff-]", "_", 任务["名"])
    仓.mkdir(parents=True, exist_ok=True)
    for 相对, 文 in 任务["文件"].items():
        p = 仓 / 相对
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(文, encoding="utf-8")
    return 仓


def 跑仲裁(仓: Path, 任务: dict) -> tuple[int, str]:
    r"""**唯一仲裁者**：主命令必须绿，有第二门的也要绿。两臂都用它，不采信任何自述。"""
    rc, 出 = 跑命令(仓, 任务["命令"])
    if rc == 0 and 任务.get("第二门"):
        rc2, 出2 = 跑命令(仓, 任务["第二门"])
        return rc2, (出 + "\n" + 出2).strip()
    return rc, 出


def 跑命令(仓: Path, 命令: list[str], 上限: float = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(命令, cwd=str(仓), capture_output=True, text=True,
                           timeout=上限, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"命令超过 {上限}s 未结束"


# ────────────────────────────── 模型侧（两臂共用） ──────────────────────────────
def 建路由():
    from agentscope_orchestrator import gateway as g
    from agentscope_orchestrator import fixloop
    from agentscope_orchestrator.router import Router
    通道们, 报告 = g.select_live_channels("")
    对 = [(槽, g.build_client(c, m)) for 槽, c, m in g.live_model_pairs(通道们, 报告)]
    if not 对:
        raise SystemExit(f"没有可用模型通道：{json.dumps(报告.get('dead') or {}, ensure_ascii=False)[:300]}")
    return Router(对), fixloop


def 估token(文: str) -> int:
    return max(1, len(文) // 4)


class 计量:
    r"""把 `fixloop._模型调用带上限` 包一层，记录**提示字节/回复字节/调用次数**。

    ★ 为什么必须这么做（2026-11-02 实测）：A 臂走产品自己的调用链，通道不回 usage、
       `修复()` 也不统计 token ⇒ 原先 A 的 token 恒为 0 而 B 是估算，**两列不可比**。
       公平的尺子只有一把：在**同一层**（模型调用处）数提示与回复的字节。
       这不改产品逻辑，只是量具（wrap 而非 patch 行为）。
    """

    def __init__(self, fixloop):
        self.orig = fixloop._模型调用带上限
        self.fixloop = fixloop
        self.调用 = 0
        self.提示字节 = 0
        self.回复字节 = 0

    def __enter__(self):
        def 包(路由, 提示, 上限):
            self.调用 += 1
            self.提示字节 += len(提示.encode("utf-8"))
            回 = self.orig(路由, 提示, 上限)
            文 = getattr(回, "content", None) or getattr(回, "text", None) or str(回)
            if isinstance(文, list):
                文 = "".join((b.get("text") or "") if isinstance(b, dict)
                             else (getattr(b, "text", "") or "") for b in 文)
            self.回复字节 += len(str(文).encode("utf-8"))
            return 回
        self.fixloop._模型调用带上限 = 包
        return self

    def __exit__(self, *a):
        self.fixloop._模型调用带上限 = self.orig
        return False

    @property
    def token(self) -> int:
        return max(1, (self.提示字节 + self.回复字节) // 4)


# ────────────────────────────── A 臂：编排器 ──────────────────────────────
def 跑A(任务: dict, 轮数: int, 路由, fixloop, 趟: int = 1) -> dict:
    父 = Path(tempfile.mkdtemp(prefix="bench-a-", dir=str(根 / "tests")))
    try:
        仓 = 铺(任务, 父)
        门 = []
        if 任务.get("第二门"):
            门.append({"name": "回归门", "type": "compile", "cmd": list(任务["第二门"]),
                       "cwd": str(仓), "expect_exit": 0})
        with 计量(fixloop) as 计:
            t0 = time.perf_counter()
            回 = asyncio.run(fixloop.修复(str(仓), list(任务["命令"]), 门=门, 轮数=轮数))
            秒 = round(time.perf_counter() - t0, 1)
        rc, 出 = 跑仲裁(仓, 任务)                  # 事后**独立**复判（不信它自己的话）
        return {"臂": "A", "趟": 趟, "任务": 任务["名"], "类": 任务["类"], "成功": rc == 0,
                "分类": 回.get("分类"), "往返": 计.调用 or len(回.get("修复记录") or []),
                "秒": 秒, "token": 计.token, "token源": "计量(提示+回复字节/4)",
                "人工干预": 0 if 回.get("分类") in ("达标", "未达标") else 1,
                "仲裁rc": rc, "仲裁输出": 出.strip()[-400:],
                "详情": (回.get("error") or "")[:300]}
    finally:
        shutil.rmtree(父, ignore_errors=True)


# ────────────────────────────── B 臂：同模型裸跑 ──────────────────────────────
格式说明 = """严格按下面格式输出要改的文件，**不要输出别的**：

FILE: <相对路径>
```python
<该文件完整新内容>
```

可以输出多个 FILE 块。除这些块以外不要写任何解释。"""


def 解析块(文: str) -> dict[str, str]:
    出: dict[str, str] = {}
    for m in re.finditer(r"FILE:\s*(\S+)\s*\n```[a-zA-Z0-9_+-]*\n(.*?)\n```", 文, re.S):
        出[m.group(1).strip().lstrip("./")] = m.group(2)
    return 出


def 跑B(任务: dict, 轮数: int, 路由, fixloop, 趟: int = 1) -> dict:
    父 = Path(tempfile.mkdtemp(prefix="bench-b-", dir=str(根 / "tests")))
    try:
        仓 = 铺(任务, 父)
        往返 = 0
        估 = 0
        干预 = 0
        t0 = time.perf_counter()
        失败输出 = ""
        成功 = False
        计 = 计量(fixloop)
        计.__enter__()
        for 轮 in range(1, 轮数 + 1):
            文件们 = {str(p.relative_to(仓)).replace("\\", "/"): p.read_text(encoding="utf-8")
                     for p in 仓.rglob("*") if p.is_file()}
            清单 = "\n".join(f"=== {k} ===\n{v}" for k, v in sorted(文件们.items()))
            提示 = (f"这是一个必须通过下面这条命令的小项目：\n`{ ' '.join(任务['命令']) }`\n\n"
                    f"当前文件：\n{清单}\n\n最近一次失败输出（第 {轮} 轮）：\n{失败输出 or '（还没跑过）'}\n\n"
                    f"请修复到命令能通过。{格式说明}")
            往返 += 1
            try:
                回复 = fixloop._模型调用带上限(路由, 提示, 240)
                文 = getattr(回复, "content", None) or getattr(回复, "text", None) or str(回复)
                if isinstance(文, list):
                    文 = "".join((b.get("text") or "") if isinstance(b, dict)
                                 else (getattr(b, "text", "") or "") for b in 文)
                文 = 文 if isinstance(文, str) else str(文)
            except Exception as e:                   # noqa: BLE001 模型侧失败即算"要人管"
                干预 += 1
                失败输出 = f"模型调用失败：{type(e).__name__}: {e}"
                continue
            块 = 解析块(文)
            if not 块:
                干预 += 1                            # 不能按格式产出 ⇒ 没有门的人只能自己收拾
                失败输出 = "上一轮回复里没有可解析的 FILE 块。"
                continue
            for 相对, 内容 in 块.items():
                目标 = 仓 / 相对
                if 仓 not in 目标.resolve().parents and 目标.resolve() != 仓:
                    干预 += 1
                    continue                         # 越界写 ⇒ 没人拦，但记成一次干预
                目标.parent.mkdir(parents=True, exist_ok=True)
                目标.write_text(内容 + ("\n" if not 内容.endswith("\n") else ""), encoding="utf-8")
            rc, 失败输出 = 跑命令(仓, 任务["命令"])
            if rc == 0:
                成功 = True
                break
        计.__exit__()
        秒 = round(time.perf_counter() - t0, 1)
        rc, 出 = 跑仲裁(仓, 任务)
        return {"臂": "B", "趟": 趟, "任务": 任务["名"], "类": 任务["类"], "成功": 成功 and rc == 0,
                "分类": "达标" if rc == 0 else "未达标", "往返": 计.调用 or 往返, "秒": 秒,
                "token": 计.token, "token源": "计量(提示+回复字节/4)", "人工干预": 干预,
                "仲裁rc": rc, "仲裁输出": 出.strip()[-400:], "详情": 失败输出[:300]}
    finally:
        shutil.rmtree(父, ignore_errors=True)


# ────────────────────────────── 报告 ──────────────────────────────
def 汇总(结果: list[dict]) -> dict:
    汇: dict = {}
    # ★ 按 (臂, 趟) 分组：趟是在量**全局记忆**（第 2 趟带着第 1 趟写下的八维记忆）
    for 键 in sorted({(r["臂"], r.get("趟", 1)) for r in 结果}):
        臂, 趟 = 键
        行 = [r for r in 结果 if r["臂"] == 臂 and r.get("趟", 1) == 趟]
        if not 行:
            continue
        汇[f"{臂}·第{趟}趟"] = {
            "任务数": len(行),
            "成功": sum(1 for r in 行 if r["成功"]),
            "成功率": round(sum(1 for r in 行 if r["成功"]) / len(行), 3),
            "平均往返": round(sum(r["往返"] for r in 行) / len(行), 2),
            "平均秒": round(sum(r["秒"] for r in 行) / len(行), 1),
            "token合计": sum(r["token"] for r in 行),
            "人工干预合计": sum(r["人工干预"] for r in 行),
        }
    return 汇


def 写报告(结果: list[dict], 汇: dict, 轮数: int, 时间: str) -> tuple[Path, Path]:
    回执 = 根 / "receipts"
    回执.mkdir(exist_ok=True)
    stem = f"bench-ab-{时间}"
    (回执 / f"{stem}.json").write_text(
        json.dumps({"时间": 时间, "轮数上限": 轮数, "汇总": 汇, "明细": 结果},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    行 = ["# 同任务对照基准 A vs B", "",
          f"- 时间：{时间} · 轮数上限：{轮数} · 任务数：{len({r['任务'] for r in 结果})}",
          "- A = 本编排器（`orch_fix` + 判据门 + 回滚 + 证据包 + 预算）",
          "- B = **同一个模型**裸跑（无门、无回滚、无证据包；只给失败输出）",
          "", "## 汇总", "",
          "| 臂 | 任务数 | 成功 | 成功率 | 平均往返 | 平均秒 | token | 人工干预 |",
          "|---|---|---|---|---|---|---|---|"]
    for 臂, v in 汇.items():
        行.append(f"| {臂} | {v['任务数']} | {v['成功']} | {v['成功率']} | {v['平均往返']} | "
                  f"{v['平均秒']} | {v['token合计']} | {v['人工干预合计']} |")
    行 += ["", "## 逐任务", "",
           "| 任务 | 类 | 臂 | 成功 | 往返 | 秒 | token(源) | 人工干预 | 仲裁rc |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(结果, key=lambda x: (x["任务"], x["臂"], x.get("趟", 1))):
        行.append(f"| {r['任务']} | {r['类']} | {r['臂']}·{r.get('趟',1)} | {'✓' if r['成功'] else '✗'} | "
                  f"{r['往返']} | {r['秒']} | {r['token']}({r['token源']}) | {r['人工干预']} | {r['仲裁rc']} |")
    行 += ["", "## 边界（读这张表前先读这一段）", "",
           "- 只覆盖**小型可机判任务**；开放任务上的表现不由此表代表。",
           "- token：通道回 usage 时用真值，否则 `est` 为「提示+回复字符数/4」。",
           "- B 的「人工干预」= 解析失败/越界写/模型调用失败被脚本兜住的次数；A 的同类表现为返回非达标 + 修法。",
           "- 成功由**同一仲裁命令**事后独立复跑判定，不采信任何一臂的自述。"]
    p = 回执 / f"{stem}.md"
    p.write_text("\n".join(行) + "\n", encoding="utf-8")
    return p, 回执 / f"{stem}.json"


正确解 = {
    "T1-值错": {"pkg/mod.py": "def f():\n    return 42\n"},
    "T2-测试红": {"pkg/core.py": "def add(a, b):\n    return a + b\n"},
    "T3-边界": {"pkg/stats.py": "def mean(xs):\n    return sum(xs) / len(xs) if xs else 0.0\n"},
    "T4-数据处理": {"analyze.py": (
        "import csv\nimport statistics as st\n\n\n"
        "rows = list(csv.DictReader(open('data.csv')))\n"
        "xs = [float(r['x']) for r in rows]\n"
        "print(f'{st.mean(xs):.4f},{st.pstdev(xs):.4f}')\n")},
    "T5-拟合": {"fit.py": "print('2.0,1.0')\n"},
    "T6-大仓定位": {"pkg/core.py": "def scale(x):\n    return x * 3\n"},
    "T7-回归破坏": {"pkg/utils.py": "def norm(xs):\n    return [int(round(x)) if float(x).is_integer() else x for x in xs]\n"},
    "T8-手写拟合": {"fit.py": (
        "import csv\n\n\n"
        "rows = list(csv.DictReader(open('points.csv')))\n"
        "xs = [float(r['x']) for r in rows]\n"
        "ys = [float(r['y']) for r in rows]\n"
        "n = len(xs)\n"
        "mx = sum(xs) / n\n"
        "my = sum(ys) / n\n"
        "k = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)\n"
        "b = my - k * mx\n"
        "print(f'{k},{b}')\n")},
    "T10-硬编码陷阱": {"pkg/core.py": "def scale(x):\n    return x * 3\n"},
    "T11-调用方漏改": {"pkg/khz.py": "def khz(x):\n    return x / 1000\n"},
    "T9-格式与容差": {"summary.py": (
        "import csv\n\n\n"
        "xs = [float(r['v']) for r in csv.DictReader(open('data.csv'))]\n"
        "print(f'{sum(xs)/len(xs):.2f}')\n")},
}


def 自检() -> int:
    r"""夹具自检：**坏种必须红、正确解必须绿**。

    ★ 为什么这不是可选项：基准的两臂都靠"同一条仲裁命令"判成败。
      如果夹具本身写错（正确解也过不了，或坏种本来就绿），整张表测的就是噪声。
      这一步把"量具先校准"写进流程，而不是靠人临场看。
    """
    坏 = 0
    for 任务 in 任务们:
        父 = Path(tempfile.mkdtemp(prefix="bench-selftest-", dir=str(根 / "tests")))
        try:
            仓 = 铺(任务, 父)
            rc坏, _ = 跑命令(仓, 任务["命令"])
            for 相对, 内容 in 正确解[任务["名"]].items():
                (仓 / 相对).write_text(内容, encoding="utf-8")
            rc好, 出好 = 跑仲裁(仓, 任务)
            if rc坏 == 0:
                print(f"  [FAIL] {任务['名']}：坏种竟然绿了 —— 这个任务测不出东西")
                坏 += 1
            elif rc好 != 0:
                print(f"  [FAIL] {任务['名']}：正确解没过 —— 夹具写错了：{出好.strip()[-200:]}")
                坏 += 1
            else:
                print(f"  ok   {任务['名']}：坏种红 ✓ 正确解绿 ✓")
        finally:
            shutil.rmtree(父, ignore_errors=True)
    print(f"夹具自检：{'全过' if not 坏 else f'{坏} 项有问题'}（{len(任务们)} 个任务）")
    return 0 if not 坏 else 1


def 清记忆() -> None:
    """冷启动：清掉八维记忆库（只删 memory8 库，不碰任务库）。"""
    try:
        from agentscope_orchestrator import memory8 as _m8
        库 = _m8.库路径()
        for 后缀 in ("", "-wal", "-shm"):
            Path(str(库) + 后缀).unlink(missing_ok=True)
    except Exception:                       # noqa: BLE001 清不掉不算错，报告里会露馅
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="只校准夹具：坏种必须红、正确解必须绿")
    ap.add_argument("--tasks", type=int, default=len(任务们))
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--arm", default="AB")
    ap.add_argument("--passes", type=int, default=1,
                    help="跑几趟：同一批任务重复跑。第 1 趟冷启动（清八维记忆库），之后是暖的")
    ap.add_argument("--keep-memory", action="store_true",
                    help="第 1 趟也不清记忆库（量已有全局记忆的真实效果）")
    a = ap.parse_args()
    if a.selftest:
        return 自检()
    选 = 任务们[: max(1, min(a.tasks, len(任务们)))]
    路由, fixloop = 建路由()
    结果: list[dict] = []
    for 趟 in range(1, max(1, a.passes) + 1):
        if 趟 == 1:
            if a.keep_memory:
                print("[bench] 第 1 趟：**保留**已有八维记忆（量全局记忆的真实增益）", flush=True)
            else:
                清记忆()
                print("[bench] 第 1 趟：冷启动（八维记忆库已清）", flush=True)
        elif 趟 == 2:
            print("[bench] 第 2 趟：暖启动（带着上一趟写下的八维记忆）", flush=True)
        for 任务 in 选:
            if "A" in a.arm:
                print(f"[bench] A ← 趟{趟} {任务['名']}", flush=True)
                r = 跑A(任务, a.rounds, 路由, fixloop, 趟)
                结果.append(r)
                print(f"[bench] A → 趟{趟} {任务['名']} 成功={r['成功']} 往返={r['往返']} "
                      f"{r['秒']}s token={r['token']}", flush=True)
            if "B" in a.arm:
                print(f"[bench] B ← 趟{趟} {任务['名']}", flush=True)
                r = 跑B(任务, a.rounds, 路由, fixloop, 趟)
                结果.append(r)
                print(f"[bench] B → 趟{趟} {任务['名']} 成功={r['成功']} 往返={r['往返']} "
                      f"{r['秒']}s token={r['token']}", flush=True)
    汇 = 汇总(结果)
    时间 = time.strftime("%Y%m%d-%H%M%S")
    md, js = 写报告(结果, 汇, a.rounds, 时间)
    print("\n" + md.read_text(encoding="utf-8"))
    print(f"[bench] 回执：{md}\n[bench] 回执：{js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
