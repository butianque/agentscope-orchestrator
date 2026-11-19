"""难任务集（可复算）—— 用来测「省轮次」，不是测判决准确率（那用 calib.py）。

★ 为什么单独一份
  `calib.py` 的 23 个案例是**判决**语料：真值已知、多数首轮即绿，适合验分类；
  但"经验环/修复环能省几轮"需要**难任务**：冷启动必须 ≥2 轮，省轮才有空间。

★ 本文件现有内容与**实测结论**（2026-10-31）
  ① 合成集 `合成任务()`：5 类跨文件缺陷（单位换算常量 / 配置键不一致 / 切片差一 /
     可变默认参数 / 排序键取错字段）+ 1 个**链式**任务（修 A 才暴露 B）。
     实测：deepseek-v4.1-flash **全部首轮即绿**（冷 5/5 达标、中位 1 轮）。
     ⇒ 合成小仓**构造不出**难任务：模型会把 3~5 个小文件一次看全并一起修掉。
  ② 真实仓模式 `真实仓(路径)`：375 个 .rs、含 `#[test]` 的大仓才是难任务来源。
     注意编译成本：首次 `cargo test` 可能数分钟/轮，测前先用 `--no-run` 探时间。

用法：
    python -m tools.hard_cases              # 跑合成集（约 1 分钟，需模型通道）
    python -m tools.hard_cases --real <仓>  # 跑真实仓模式
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import sys
import time
from pathlib import Path


def _仓(基: Path, 名: str, 文们: dict) -> Path:
    p = 基 / 名
    shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True)
    for k, v in 文们.items():
        (p / k).write_text(v, encoding="utf-8")
    return p


def 合成任务() -> dict[str, dict]:
    """5 类跨文件缺陷：症状在 test_*.py，病根在另一个模块。"""
    return {
        "单位换算": {"conv.py": "KB = 1024\nMB = 1000 * KB\n\n\ndef to_mb(n):\n    return n / MB\n",
                 "test_a.py": "import unittest\nimport conv\n\n\nclass T(unittest.TestCase):\n"
                              "    def test_one(self):\n"
                              "        self.assertAlmostEqual(conv.to_mb(1024 * 1024), 1.0, places=6)\n"},
        "配置键": {"config.py": "def load():\n    return {'ttl': 60, 'mode': 'fast'}\n",
                "auth.py": "import config\n\n\ndef ttl():\n    return config.load().get('ttl_s')\n",
                "test_a.py": "import unittest\nimport auth\n\n\nclass T(unittest.TestCase):\n"
                             "    def test_ttl(self):\n        self.assertEqual(auth.ttl(), 60)\n"},
        "分页切片": {"pager.py": "def page(items, n):\n    return items[0:n - 1]\n",
                  "test_a.py": "import unittest\nimport pager\n\n\nclass T(unittest.TestCase):\n"
                               "    def test_page(self):\n"
                               "        self.assertEqual(len(pager.page(list(range(10)), 3)), 3)\n"},
        "可变默认值": {"tags.py": "def add(tag, bucket=[]):\n    bucket.append(tag)\n    return bucket\n",
                    "test_a.py": "import unittest\nimport tags\n\n\nclass T(unittest.TestCase):\n"
                                 "    def test_isolated(self):\n        tags.add('a')\n"
                                 "        self.assertEqual(tags.add('b'), ['b'])\n"},
        # ★ 真值必须独立校验：这一例第一版期望值恰好等于错误实现的输出（按 age 升序 = young），
        #   结果"轮=0 直接绿"——真值造错，比缺案例更坏。现改为按 score 取最大。
        "排序键": {"rank.py": "def best(rows):\n"
                          "    return sorted(rows, key=lambda r: r['age'])[0]['name']\n",
                "test_a.py": "import unittest\nimport rank\n\n\nclass T(unittest.TestCase):\n"
                             "    def test_best(self):\n        rows = [{'name': 'old', 'age': 40, 'score': 1},\n"
                             "                {'name': 'young', 'age': 20, 'score': 9}]\n"
                             "        self.assertEqual(rank.best(rows), 'old')\n"},
    }


def 链式任务() -> dict[str, dict]:
    """修好 A 才暴露 B、修好 B 才暴露 C（强制多轮的结构）。"""
    return {"parse.py": "def split(s):\n    # 缺陷1：丢首段\n    return s.split('-')[1:]\n",
            "transform.py": "def upper(xs):\n    # 缺陷2：丢尾段\n"
                            "    return [x.upper() for x in xs[:len(xs) - 1]]\n",
            "render.py": "def join(xs):\n    # 缺陷3：分隔符错\n    return ','.join(xs)\n",
            "pipeline.py": "import parse\nimport render\nimport transform\n\n\n"
                           "def run(s):\n    return render.join(transform.upper(parse.split(s)))\n",
            "test_a.py": "import unittest\nimport pipeline\n\n\nclass T(unittest.TestCase):\n"
                         "    def test_run(self):\n"
                         "        self.assertEqual(pipeline.run('a-b-c'), 'A|B|C')\n"}


async def 跑(名: str, 仓: Path, 轮数: int = 3, 通道: str = "dddddd") -> dict:
    from agentscope_orchestrator import taixu_mcp as m
    t0 = time.time()
    r = json.loads(await m.orch_fix(repo=str(仓), cmd=["python", "-m", "unittest", "-q"],
                                    rounds=轮数, channel=通道))
    return {"案": 名, "分类": r.get("分类"), "轮": r.get("修复轮数"),
            "耗时": round(time.time() - t0, 1),
            "经验命中": sum(1 for x in (r.get("修复记录") or []) if x.get("经验命中"))}


async def 跑合成(基: Path, 通道: str) -> dict:
    from agentscope_orchestrator import learning as L
    冷 = []
    for 名, 文们 in 合成任务().items():
        冷.append(await 跑(名, _仓(基, f"冷_{名}", 文们), 3, 通道))
    print(f"\n学习后经验条数 = {L.统计()['经验条数']}")
    热 = []
    for 名, 文们 in 合成任务().items():
        热.append(await 跑(名, _仓(基, f"热_{名}", 文们), 3, 通道))
    选 = await 跑("链式", _仓(基, "链式", 链式任务()), 4, 通道)
    for 组, 行们 in (("冷启动", 冷), ("带经验", 热)):
        轮们 = [x["轮"] or 0 for x in 行们]
        print(f"{组}: 达标 {sum(1 for x in 行们 if x['分类']=='达标')}/{len(行们)} "
              f"中位轮 {statistics.median(轮们)} 总轮 {sum(轮们)} "
              f"中位耗时 {statistics.median(x['耗时'] for x in 行们)}s")
    print(f"链式: 分类={选['分类']} 轮={选['轮']} 耗时={选['耗时']}s")
    可测 = [x for x in 冷 if (x["轮"] or 0) >= 2]
    print(f"\n⇒ 需要 ≥2 轮的案例：{len(可测)}/{len(冷)}"
          f"{'（该集合可用于测省轮）' if 可测 else '（**该集合测不出省轮**：全首轮即绿，须换真实大仓）'}")
    return {"冷": 冷, "热": 热, "链式": 选}


async def 跑真实(路径: Path, 通道: str, 轮数: int = 2) -> dict:
    """真实仓模式：**不复制、不改**，只在副本上跑（调用方自己给副本路径）。"""
    from agentscope_orchestrator import taixu_mcp as m
    t0 = time.time()
    r = json.loads(await m.orch_fix(repo=str(路径), cmd=["cargo", "test", "--lib"],
                                    rounds=轮数, channel=通道))
    print(f"真实仓: 分类={r.get('分类')} 轮={r.get('修复轮数')} "
          f"耗时={round(time.time()-t0,1)}s")
    return r


def main(argv=None) -> int:
    牙 = argparse.ArgumentParser()
    牙.add_argument("--real", default="", help="真实仓路径（副本！）")
    牙.add_argument("--channel", default="dddddd")
    牙.add_argument("--base", default="", help="工作目录（默认系统临时目录）")
    参 = 牙.parse_args(argv)
    基 = Path(参.base) if 参.base else Path(__import__("tempfile").mkdtemp(prefix="hard_cases_"))
    基.mkdir(parents=True, exist_ok=True)
    if 参.real:
        asyncio.run(跑真实(Path(参.real), 参.channel))
    else:
        asyncio.run(跑合成(基, 参.channel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
