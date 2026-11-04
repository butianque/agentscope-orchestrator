"""★ 标定语料：用**真值已知**的构造案例量"判决准不准、修法有没有、载荷多大"。

为什么要有这个（2026-10-30）：我此前所有"判得准/省轮次"的说法都只有**单点读数**，
没有带真值的语料 ⇒ 那只是主张。这个模块把主张变成可复算的数字：

  真值 = 我构造案例时就知道它属于哪一类（不是因为分类器说了什么）；
  混淆矩阵 = 真值 × 判决；
  危险错误单独计：**假绿**（非达标被判达标）与**基设↔产物互认错**。

口径（避免自欺）：
  * 每个案例只用**公开接口** `taixu_mcp.orch_run` 跑，不绕过它调分类器；
  * 抖动/分歧这类"环境侧"案例的真值按**设计意图**标（混窗=抖动，异构不一致=分歧），
    若判决给的是"未达标"且差分说明了红绿混合，我记为**部分相符**并单列，不硬算对错；
  * 语料 n 小，**只报计数与逐类命中**，不报置信区间——n 小的时候报小数点后两位是骗人。

用法：
    python -m agentscope_orchestrator.calib            # 全量
    python -m agentscope_orchestrator.calib --limit 8  # 抽前 8 条
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

案例们: list[dict] = []


def _案例(编号: str, 真值: str, 说明: str, **跑参):
    案例们.append({"id": 编号, "真值": 真值, "说明": 说明, "跑参": 跑参})


def _仓(基: Path, 名: str) -> Path:
    p = 基 / 名
    shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _建语料(基: Path) -> None:
    """构造案例。每条都写清"为什么它的真值是这个"，便于别人复核。"""
    空 = _仓(基, "空仓")
    内容门仓 = _仓(基, "内容门仓")
    (内容门仓 / "out.txt").write_text("BUILD OK\n", encoding="utf-8")
    假绿仓 = _仓(基, "假绿仓")
    (假绿仓 / "target").mkdir(exist_ok=True)
    (假绿仓 / "target" / "ok.flag").write_text("built", encoding="utf-8")

    # ── 达标 ──────────────────────────────────────────────
    _案例("G1", "达标", "命令退出码 0",
          repo=str(空), cmd=["python", "-c", "print('ok')"])
    _案例("G2", "达标", "退出码 0 且文件内容门匹配（grep_required 读 in 文件）",
          repo=str(内容门仓), cmd=["python", "-c", "print('done')"],
          gates=[{"name": "must", "type": "grep_required", "in": str(内容门仓 / "out.txt"),
                  "pattern": "BUILD OK", "cwd": str(内容门仓)}])
    _案例("G3", "达标", "稍慢任务（1s）仍然等到终态",
          repo=str(空), cmd=["python", "-c", "import time; time.sleep(1); print('ok')"])

    # ── 未达标（产物问题） ─────────────────────────────────
    _案例("F1", "未达标", "断言失败（典型产物错）",
          repo=str(空), cmd=["python", "-c", "raise AssertionError('expected 5 got 4')"])
    _案例("F2", "未达标", "Rust 编译器错误文本 + 非零退出",
          repo=str(空), cmd=["python", "-c",
                              "print('error[E0308]: mismatched types'); import sys; sys.exit(1)"])
    _案例("F3", "未达标", "退出码 0 但文件内容门不满足（弱门陷阱）",
          repo=str(内容门仓), cmd=["python", "-c", "print('done')"],
          gates=[{"name": "must", "type": "grep_required", "in": str(内容门仓 / "out.txt"),
                  "pattern": "all tests passed", "cwd": str(内容门仓)}])
    _案例("F4", "未达标", "同时含 503 与产物词 ⇒ 产物词否决，不许记成基设",
          repo=str(空), cmd=["python", "-c",
                              "print('503 Service Unavailable'); print('error[E0308]: bad type'); "
                              "import sys; sys.exit(1)"])
    _案例("F5", "未达标", "Python 语法错误",
          repo=str(空), cmd=["python", "-c", "print('SyntaxError: invalid syntax'); import sys; sys.exit(1)"])

    # ── 参数/前提不成立（调用方错） ────────────────────────
    _案例("P1", "参数/前提不成立", "repo 路径不存在",
          repo=str(基 / "根本不存在"), cmd=["python", "-c", "print(1)"])
    _案例("P2", "参数/前提不成立", "cmd 为空",
          repo=str(空), cmd=[])
    _案例("P3", "参数/前提不成立", "unit_id 不在源码索引内",
          repo=str(空), cmd=["python", "-c", "print(1)"], unit_id="根本不存在.的.单元")
    _案例("P4", "任务书驳回", "门规格非法（未知类型）⇒ 任务书侧问题，不是产物错",
          repo=str(空), cmd=["python", "-c", "print(1)"],
          gates=[{"name": "x", "type": "根本不存在的门类型"}])

    # ── 基础设施不可用（环境侧） ───────────────────────────
    _案例("I1", "基础设施不可用", "连不上上游端口（Connection refused）",
          repo=str(空), cmd=["python", "-c",
                              "import urllib.request as u; u.urlopen('http://127.0.0.1:1/', timeout=2)"])
    _案例("I2", "基础设施不可用", "只有 503，没有产物词",
          repo=str(空), cmd=["python", "-c",
                              "print('503 Service Unavailable from upstream'); import sys; sys.exit(1)"])
    _案例("I3", "基础设施不可用", "只有 Timeout，没有产物词",
          repo=str(空), cmd=["python", "-c",
                              "print('Timeout: upstream did not respond'); import sys; sys.exit(1)"])

    # ── 实例分歧（环境耦合：同一门集在异构实例里结论不同） ──
    # D1 删除：`hash()` 奇偶不能保证两实例必不同（构造本身不确定），改由 D2 覆盖
    _案例("D2", "实例分歧", "按实例注入的 PYTHONHASHSEED 奇偶分叉（两实例必不同）",
          repo=str(空), cmd=["python", "-c",
                              "import os,sys; sys.exit(0 if int(os.environ.get('PYTHONHASHSEED','0'))%2==0 else 1)"],
          instances=2)

    # ── 抖动（同一实例多窗，时绿时红） ─────────────────────
    # 确定性投窗：第 1、2 窗红，第 3~5 窗绿（计数器落盘，同 cwd 顺序执行）
    def _投窗(仓: Path, 名: str) -> None:
        (仓 / f"c_{名}.txt").write_text("0", encoding="utf-8")

    投窗仓 = _仓(基, "投窗仓")
    _投窗(投窗仓, "a")
    投窗仓2 = _仓(基, "投窗仓2")
    _投窗(投窗仓2, "b")
    _投命令 = ["python", "-c",
             "import pathlib,sys; p=pathlib.Path('c_a.txt'); n=int(p.read_text() or 0)+1; "
             "p.write_text(str(n)); sys.exit(0 if n>2 else 1)"]
    _投命令2 = ["python", "-c",
              "import pathlib,sys; p=pathlib.Path('c_b.txt'); n=int(p.read_text() or 0)+1; "
              "p.write_text(str(n)); sys.exit(0 if n>2 else 1)"]
    _案例("J1", "达标", "确定性：5 窗中 3 窗绿，须过=3 ⇒ 按票数接受（抖动被票数吸收）",
          repo=str(投窗仓), cmd=_投命令, windows=5, min_pass=3)
    _案例("J2", "未达标", "确定性：5 窗中 3 窗绿，须过=5 ⇒ 必须判未达标",
          repo=str(投窗仓2), cmd=_投命令2, windows=5, min_pass=5)

    # ── skip 是否算绿（2026-10-31 的假绿洞） ─────────────
    跳仓 = _仓(基, "全跳仓")
    (跳仓 / "test_a.py").write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    @unittest.skip('先跳过')\n    def test_x(self):\n        self.assertEqual(1, 2)\n",
        encoding="utf-8")
    过仓 = _仓(基, "真过仓")
    (过仓 / "test_a.py").write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    def test_x(self):\n        self.assertEqual(2, 2)\n", encoding="utf-8")
    _案例("S1", "未达标", "测试被全部 skip（退出码 0）⇒ 计数门必须判红",
          repo=str(跳仓), cmd=["python", "-m", "unittest", "-q"])
    _案例("S2", "达标", "测试真跑真过 ⇒ 计数门判绿",
          repo=str(过仓), cmd=["python", "-m", "unittest", "-q"])

    # ── 假绿风险（copy 模式：绿只因仓里已有构建产物） ──────
    _案例("H1", "实例分歧", "单实例看 target/ok.flag 而绿，copy 模式无该产物 ⇒ 实例结论不同",
          repo=str(假绿仓),
          cmd=["python", "-c",
               "import pathlib,sys; sys.exit(0 if pathlib.Path('target/ok.flag').exists() else 1)"],
          instances=2, instances_mode="copy")

    # ── 非终态 / 取消 ──────────────────────────────────────
    _案例("R1", "进行中", "wait=False，1.5s 的任务仍在跑",
          repo=str(空), cmd=["python", "-c", "import time; time.sleep(1.5); print('ok')"],
          wait=False)
    _案例("R2", "取消", "wait=False 后立刻取消",
          repo=str(空), cmd=["python", "-c", "import time; time.sleep(30); print('ok')"],
          wait=False, _取消=True)


async def _跑一条(m, 案例: dict) -> dict:
    参 = dict(案例["跑参"])
    取消 = 参.pop("_取消", False)
    t0 = time.time()
    记录 = {"id": 案例["id"], "真值": 案例["真值"], "说明": 案例["说明"],
            "判决": None, "载荷字节": 0, "有修法": None, "差分": None, "错": None}
    try:
        文 = await m.orch_run(timeout_s=180, **参)
        载 = json.loads(文)
        记录["载荷字节"] = len(文.encode("utf-8"))
        if 取消:
            tid = 载.get("task_id")
            if tid:
                m.cancel_task(tid)          # 同步函数（真跑踩过：await 会 TypeError）
                await asyncio.sleep(0.6)
                载2 = json.loads(await m.orch_wait(tid, timeout_s=10, max_chars=2000))
                记录["判决"] = 载2.get("分类")
                记录["载荷字节"] = len(json.dumps(载2, ensure_ascii=False).encode("utf-8"))
        if 记录["判决"] is None:
            记录["判决"] = 载.get("分类")
            记录["有修法"] = 载.get("修法") is not None
            记录["差分"] = (载.get("差分") or {}).get("说明")
            记录["证据字节"] = sum(len(str(g.get("证据") or "").encode())
                                 for g in (载.get("门") or []))
    except Exception as ex:                       # noqa: BLE001
        记录["错"] = f"{type(ex).__name__}: {ex}"
    记录["耗时_s"] = round(time.time() - t0, 2)
    return 记录


async def 跑语料(limit: int | None = None, 并发: int = 3) -> dict:
    from agentscope_orchestrator import taixu_mcp as m
    基 = Path(tempfile.mkdtemp(prefix="calib_"))
    os.environ.setdefault("TAIXU_DB", str(基 / "calib.db"))
    _建语料(基)
    选 = 案例们[: limit] if limit else 案例们
    闸 = asyncio.Semaphore(并发)

    async def 包(c):
        async with 闸:
            return await _跑一条(m, c)

    行 = await asyncio.gather(*[包(c) for c in 选])
    行 = list(行)

    # ── 计分 ──
    类别 = ["达标", "未达标", "参数/前提不成立", "基础设施不可用", "实例分歧",
            "任务书驳回", "进行中", "取消", "未到终态"]
    混淆 = {真: {判: 0 for 判 in 类别} for 真 in 类别}
    对 = 危 = 0
    for r in 行:
        真, 判 = r["真值"], (r["判决"] or "（异常）")
        if 判 in 混淆[真]:
            混淆[真][判] += 1
        if 判 == 真:
            对 += 1
        elif 真 != "达标" and 判 == "达标":
            危 += 1
        基设, 产物 = {"基础设施不可用"}, {"未达标"}
        if (真 in 基设 and 判 in 产物) or (真 in 产物 and 判 in 基设):
            危 += 1
    非绿 = [r for r in 行 if r["真值"] != "达标"]
    有修法 = sum(1 for r in 非绿 if r["有修法"])
    载荷 = [r["载荷字节"] for r in 行 if r["载荷字节"]]
    证据 = [r.get("证据字节", 0) for r in 行]
    比 = [p / e for p, e in zip(载荷, 证据) if e]
    return {
        "n": len(行), "对": 对, "准确率": round(对 / len(行), 3) if 行 else 0,
        "危险错误": 危, "混淆": 混淆, "行": 行,
        "修法覆盖率": round(有修法 / len(非绿), 3) if 非绿 else None,
        "载荷中位字节": int(statistics.median(载荷)) if 载荷 else 0,
        "证据中位字节": int(statistics.median([e for e in 证据 if e])) if any(证据) else 0,
        "载荷/证据比中位": round(statistics.median(比), 2) if 比 else None,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    出 = asyncio.run(跑语料(limit))
    print(f"══ 标定语料 n={出['n']} · 准确率={出['准确率']} · 危险错误={出['危险错误']} ══")
    print(f"{'id':<4}{'真值':<12}{'判决':<12}{'耗时':<7}{'载荷':<7}说明")
    for r in 出["行"]:
        print(f"{r['id']:<4}{r['真值']:<12}{str(r['判决']):<12}{r['耗时_s']:<7}"
              f"{r['载荷字节']:<7}{r['说明'][:34]}")
    print(f"\n修法覆盖率(非绿)={出['修法覆盖率']} · 载荷中位={出['载荷中位字节']}B · "
          f"证据中位={出['证据中位字节']}B · 载荷/证据中位={出['载荷/证据比中位']}")
    print(json.dumps({k: v for k, v in 出.items() if k != "行"}, ensure_ascii=False))
    return 0 if 出["危险错误"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
