"""外挂知识库的 A/B 对照跑手：**同一批题、同一个模型、只差「有没有外挂知识」**。

## 为什么要这个工具（而不是我手工跑几次）
用户的诉求是"外挂知识有没有用"要**变成证据**，不是说法。手工跑无法保证两臂同题、
同轮数、同门、同通道，也无法把"注入块到底命中哪条知识"留下来。所以做成可重跑的工具。

## 两臂只差什么（这是全部结论的地基，必须写死）
    · 暖臂：`ORCH_KB_DIR=<包内 kb_seed>`（受版本控制的真知识，随 wheel 发布）
    · 冷臂：`ORCH_KB_DIR=<空目录>`
    · 外加 `ORCH_SEMSEARCH` 同步开关（暖=1 / 冷=0）
    **其他一律相同**：同一题、同一门、同一轮数、同一通道、同一起始状态（每次从纯净副本重开）。
    ★ 诚实边界：`learning`（跨任务经验环）与 `memory8`（八维记忆）**两臂都开着**，
      它们没有环境开关。所以本实验量的是**知识库的边际贡献**，
      不是"完全无经验的裸模型"。这一点写进回执，不许含糊。

## 判卷
一律用**仓外门**（`_gates/*.py`，不随仓交给模型 ⇒ 既防篡改也不泄题）：
    存在性优先：`_gates/{前缀}*gate*.py`；没有就退回 `ab.json.门命令`（仓内门，可能泄题）。
判绿 = 门退出码 0（由 `修复` 的机器门裁决，不是我看着觉得对）。

## 用法
    python tools/kb_ab.py                    # 全部夹具 × 两臂
    python tools/kb_ab.py -k t1              # 只跑名字含 t1 的
    python tools/kb_ab.py --臂 冷            # 只跑一臂（调试用）
    python tools/kb_ab.py --干跑             # 只验夹具"起手必红"，不调模型
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

仓 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(仓 / "src"))

夹具根 = 仓 / "tests" / "fixtures_ab"
门根 = 夹具根 / "_gates"
跑根 = 夹具根 / "_run"          # ★ 必须在产品树内：deploy 解释器只有产品树可写
解释器 = Path(os.environ.get("KB_AB_PYTHON")
              or r"E:\基础设施\orch-deploy\.venv\Scripts\python.exe")

# ── 知识库：暖臂指真知识，冷臂指一个空目录 ──────────────────────────────────
_真知识 = 仓 / "src" / "agentscope_orchestrator" / "kb_seed"


def 找门(夹具名: str, 工作: Path, 清单: dict) -> list[str]:
    r"""仓外门优先（防篡改 + 防泄题）；没有就退回 `ab.json` 里给的命令。

    ★★★ 2026-11-03 我在这里犯过一个**会出假结果**的错，留作路标：
      `找门` 原来只有一个路径参数，既用它**取前缀选门**、又用它**当 argv 里的仓路径**。
      而我传进来的是工作副本（名字形如 `_起手_t2_count_misparse`）⇒
      `split("_")[0]` 得到**空前缀** ⇒ glob `*gate*.py` 匹配到排序第一的
      `t1_hard_gate.py` ⇒ **拿 t1 的门去跑 t2**。
      症状是 t2/t3 报 `FileNotFoundError: ...\t2_...\judge.py`
      —— `judge.py` 是 t1 的文件。**这是"错因的红"：门红了，但红的不是被测的缺陷。**
      ⇒ 教训：**选门要用的身份**（夹具名）与**命令里的路径**（工作副本）是两件事，
        必须分开传參。这也是"起手必红"这步干跑存在的意义 ——
        它不检查"红不红"，它检查**红得对不对**（红的原因必须来自缺陷本身）。
    """
    预 = 夹具名.split("_")[0]
    外 = sorted(门根.glob(f"{预}*gate*.py")) if 门根.is_dir() else []
    if 外:
        return [str(解释器), str(外[0]), str(工作)]
    return [str(x) for x in 清单["门命令"]]


def 复制纯净(夹具: Path, 目标: Path) -> None:
    """从**纯净夹具**复制一份工作副本，并删掉 `FIXME.md`。

    ★ 删 `FIXME.md` 是**必须**的：它是给实验方看的（病根 + 正确修法），
      留着就等于给冷臂也发答案 ⇒ 实验直接作废。这条最容易被漏掉，
      而且漏掉之后**结果会看起来"两臂都好"**，非常像"知识没用"，其实是我泄了题。
    """
    if 目标.exists():
        shutil.rmtree(目标, ignore_errors=True)
    目标.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(夹具, 目标, ignore=shutil.ignore_patterns(
        "FIXME.md", "__pycache__", "*.pyc", "_run"))


def 起手验红(夹具: Path) -> dict:
    """纯净态必须是红的 —— 否则这道题**没有区分力**（冷臂天然绿，测不出东西）。"""
    临时 = 跑根 / f"_起手_{夹具.name}"
    复制纯净(夹具, 临时)
    import subprocess as _sp
    命令 = 找门(夹具.name, 临时, json.loads((夹具 / "ab.json").read_text(encoding="utf-8")))
    try:
        r = _sp.run(命令, cwd=str(临时), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=180)
        rc, 尾 = r.returncode, ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    except Exception as e:                        # noqa: BLE001
        rc, 尾 = -99, [f"{type(e).__name__}: {e}"]
    finally:
        shutil.rmtree(临时, ignore_errors=True)
    return {"rc": rc, "红": rc != 0, "尾": 尾[-1] if 尾 else ""}


def 跑一臂(夹具: Path, 清单: dict, 臂: str, 轮数: int, 通道: str,
          标记: dict) -> dict:
    """跑一个 (夹具, 臂)。**在同一个进程内切环境** —— `项目知识库.库根()`
    与 `fixloop` 的 `ORCH_SEMSEARCH` 都是**调用时**读 `os.environ`，所以两臂可在
    同进程内换（不必起两个 MCP，也不必改产品 API）。"""
    工作 = 跑根 / f"{夹具.name}__{臂}"
    复制纯净(夹具, 工作)
    命令 = 找门(夹具.name, 工作, 清单)

    旧kb, 旧ss = os.environ.get("ORCH_KB_DIR"), os.environ.get("ORCH_SEMSEARCH")
    旧profile = os.environ.get("ORCH_PROFILE")
    空 = 跑根 / "_空知识库"
    空.mkdir(parents=True, exist_ok=True)
    os.environ["ORCH_KB_DIR"] = str(_真知识 if 臂 == "暖" else 空)
    os.environ["ORCH_SEMSEARCH"] = "1" if 臂 == "暖" else "0"

    # ★★★ 把**跨任务经验环**对两臂同样清空（否则实验测的根本不是知识库）。
    #
    #   为什么必须做：本机 `learning` 里已经攒了 **196 条**经验（历次真跑的产物），
    #   而 `learning.取()` 是按命令指纹 + 证据相似度检索的（阈值 0.55）。
    #   196 条里很可能就有与夹具症状相似的 ⇒ 冷臂也能拿到"别人怎么修好的"，
    #   于是**两臂都会绿**，而我会误报成"知识库没用"。这是本实验最容易踩的假结论。
    #
    #   怎么做（干净、可证）：`learning.经验文件()` = `Path(ORCH_PROFILE).parent/.orchestrator/experience.jsonl`。
    #   把 profile **原样复制**到跑手自己的目录、把 `ORCH_PROFILE` 指过去 ⇒
    #   profile 内容**逐字不变**（门/档案行为不变），只是经验文件落在一个**空目录** ⇒ 经验环为空。
    #   ★ 两臂同样处理 ⇒ "只差外挂知识"这个前提仍然成立。
    #
    #   ★★★★★ 第一版**做错了，而且错得很隐蔽**（2026-11-03 同一轮内自查发现）：
    #     我把 profile 副本放在**所有运行共用**的一个目录里。问题是 `learning.记()`
    #     在**门变绿时会往经验文件写一条** ⇒ 先跑的那次把经验写进去，后跑的那次读到了它
    #     ⇒ **两臂通过经验文件互相污染**，而我的"冷臂"根本不是冷的。
    #     症状是回执里 `经验环非空` 读数**不一致**（t1 冷 0/1、t1 暖 1/1、t3 冷 0/1），
    #     而且 t3 冷臂是第 5 个跑的 —— 它那个"绿"很可能来自 t1 写的经验，不是它自己修出来的。
    #     ⇒ 修法：**每次运行一个全新 profile 目录**，并在复制前先删掉它的 `.orchestrator`。
    #     ⇒ 教训：凡是"两臂只差一个变量"的实验，**共享目录里的任何自动写入都是作弊通道**；
    #       必须为每次运行开独立的可写落点，并且把"落点确实是空的"**读出来验**（下面回执里的
    #       `经验环非空次数` 就是这个验的读数）。
    # ★★ 2026-11-19 实测事故的修法：落点名字带**纳秒时间戳** ⇒ 目录不可复用。
    #   原写法按「夹具+臂」固定命名 + `rmtree(ignore_errors=True)` 吞掉删除失败，
    #   于是一旦有一轮没删干净，**后面所有同名运行都继承它的旧经验**
    #   （实测形状：t1冷 0/1，其余 5 次全是 1/1）。
    经验落空 = 跑根 / f"_profile_{夹具.name}_{臂}_{time.time_ns()}"
    if 经验落空.exists():
        shutil.rmtree(经验落空)          # 不吞异常：删不掉就让它炸，不许带着旧经验跑
    经验落空.mkdir(parents=True, exist_ok=True)
    _真profile = Path(旧profile) if 旧profile else None
    if _真profile and _真profile.is_file():
        shutil.copy2(_真profile, 经验落空 / _真profile.name)
        os.environ["ORCH_PROFILE"] = str(经验落空 / _真profile.name)

    # ★ 抓注入块：区分"检索没命中"与"命中了但模型没用"——这是本实验最关键的归因信息
    from agentscope_orchestrator import fixloop as FL
    from agentscope_orchestrator import learning as _L
    from agentscope_orchestrator import project_kb as KB

    # ★★ 跑之前**读出来验**：经验文件必须不存在或 0 条。
    #   为什么要断言而不是只打印：判据看见了自己不满意的东西却仍把比较算成结论，
    #   那是假绿（口径 311）。这里直接 `AssertionError` 拒跑，宁可这一轮跑挂也不产假读数。
    _验文 = _L.经验文件()
    _验条 = (len([x for x in _验文.read_text(encoding="utf-8").splitlines() if x.strip()])
             if _验文.is_file() else 0)
    assert _验条 == 0, (
        f"经验环隔离失败：{_验文} 里有 {_验条} 条经验 ⇒ 两臂不再『只差外挂知识』"
        f"（拒跑：这次比较不构成一次可检验的对照实验）")
    抓: list[dict] = []
    _原 = KB.注入

    def _包(*a, **k):
        块 = _原(*a, **k)
        抓.append({"关键词尾": str(k.get("关键词") or "")[-160:],
                   "块长": len(块), "块":块[:400]})
        return 块

    _原取 = _L.取
    经验抓: list[str] = []

    def _包取(命令, 证据, *a, **k):
        文 = _原取(命令, 证据, *a, **k)
        经验抓.append(文 or "")
        return 文

    KB.注入 = _包
    _L.取 = _包取
    t0 = time.time()
    try:
        回 = asyncio.run(FL.修复(工作, 命令, 门=[], 轮数=轮数, 通道=通道))
        错 = None
    except Exception as e:                        # noqa: BLE001
        回, 错 = {}, f"{type(e).__name__}: {e}"
    finally:
        KB.注入 = _原
        _L.取 = _原取
        耗时 = round(time.time() - t0, 1)
        for 键, 值 in (("ORCH_KB_DIR", 旧kb), ("ORCH_SEMSEARCH", 旧ss),
                      ("ORCH_PROFILE", 旧profile)):
            if 值 is None:
                os.environ.pop(键, None)
            else:
                os.environ[键] = 值

    门们 = 回.get("门") or []
    绿 = bool(门们) and all(bool(x.get("通过")) for x in 门们)
    命 = [t for t in 抓 if t["块长"] > 0]
    return {
        "夹具": 夹具.name, "臂": 臂, "命令": 命令, "工作": str(工作),
        "分类": 回.get("分类"), "绿": 绿, "error": 错 or 回.get("error"),
        "门": [{"门": x.get("门"), "通过": x.get("通过"),
               "证据": str(x.get("证据") or "")[:300]} for x in 门们],
        "轮数上限": 轮数, "耗时_s": 耗时,
        "注入调用次数": len(抓), "注入非空次数": len(命),
        "注入命中": [t["块"][:200] for t in 命],
        # ★ 证据：两臂的跨任务经验环都必须为空（否则"只差外挂知识"这个前提不成立）
        "经验环调用次数": len(经验抓),
        "经验环非空次数": sum(1 for x in 经验抓 if x.strip()),
        "修复记录条数": len(回.get("修复记录") or 回.get("记录") or []),
        "原始键": sorted(回.keys()),  # 便于以后发现"能用但没读"的字段（如 token）
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑名字含该串的夹具")
    ap.add_argument("--臂", default="冷,暖")
    ap.add_argument("--轮数", type=int, default=0, help="0=用 ab.json 里的")
    ap.add_argument("--通道", default="")
    ap.add_argument("--干跑", action="store_true", help="只验起手必红，不调模型")
    a = ap.parse_args()

    # ★★ 仪器前提：**必须**给 ORCH_PROFILE。没给的话 `learning.经验文件()` 会回落到
    #   `cwd/.orchestrator/experience.jsonl`（历次真跑攒了 413 条），而 `learning.记()`
    #   在门变绿时会往里写 ⇒ 后跑的臂读到先跑的那次写的经验 ⇒ **两臂不独立**，
    #   这次比较就不构成一次干净的两臂对照。实测形状：第一个 0/1、其余全 1/1。
    #   按 R545 的纪律：**前提由仪器自己兜住**，不许靠调用方恰好设对。
    _档 = os.environ.get("ORCH_PROFILE") or ""
    if not _档 or not Path(_档).is_file():
        print("✗ 拒跑：没给 ORCH_PROFILE（或文件不存在）⇒ 经验环会落到 cwd 共享目录，两臂不独立。")
        print("  跑法：先 `$env:ORCH_PROFILE='<项目档案>.yml'`（见 DEPLOY.md），再重跑本命令。")
        print(f"  当前 ORCH_PROFILE = {_档!r}")
        return 3

    臂们 = [x.strip() for x in a.臂.split(",") if x.strip()]
    夹具们 = sorted(p for p in 夹具根.glob("*")
                   if p.is_dir() and (p / "ab.json").is_file()
                   and (a.k in p.name))
    if not 夹具们:
        print(f"没找到夹具（{夹具根}）")
        return 1

    print(f"夹具 {len(夹具们)} 个 · 臂 {臂们} · 解释器 {解释器}")
    print(f"暖知识库 = {_真知识}")
    结果: list[dict] = []
    起手: list[dict] = []

    print("\n══ 起手验红（纯净态必须红，否则该题无区分力）══")
    for f in 夹具们:
        清 = json.loads((f / "ab.json").read_text(encoding="utf-8"))
        r = 起手验红(f)
        r["夹具"] = f.name
        起手.append(r)
        print(f"  {'ok  ' if r['红'] else 'WARN'} {f.name}: rc={r['rc']} {r['尾'][:80]}")
        if not r["红"]:
            print(f"       ★ 该题起手就是绿的 ⇒ 没有区分力，A/B 测不出东西")
        if a.干跑:
            continue
        for 臂 in 臂们:
            轮 = a.轮数 or int(清.get("轮数") or 2)
            print(f"\n══ {f.name} · {臂}臂（轮数 {轮}）══")
            z = 跑一臂(f, 清, 臂, 轮, a.通道, {})
            结果.append(z)
            print(f"  分类={z['分类']} 绿={z['绿']} 耗时={z['耗时_s']}s "
                  f"注入非空 {z['注入非空次数']}/{z['注入调用次数']}")
            if z["error"]:
                print(f"  error={str(z['error'])[:200]}")

    出 = 仓 / "receipts" / f"kb-ab-{time.strftime('%Y%m%d-%H%M%S')}.json"
    出.write_text(json.dumps({"起手": 起手, "结果": 结果, "暖知识库": str(_真知识),
                             "解释器": str(解释器)}, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    print(f"\n原始记录 → {出}")

    if not 结果:
        return 0
    # 汇总
    print("\n══ 汇总（绿=机器门全过）══")
    无效 = [x for x in 结果 if x["经验环非空次数"] > 0]
    for f in 夹具们:
        for 臂 in 臂们:
            z = [x for x in 结果 if x["夹具"] == f.name and x["臂"] == 臂]
            if z:
                print(f"  {f.name:28s} {臂}臂: {'绿' if z[0]['绿'] else '红'}"
                      f"  注入非空 {z[0]['注入非空次数']}/{z[0]['注入调用次数']}"
                      f"  经验环非空 {z[0]['经验环非空次数']}/{z[0]['经验环调用次数']}")
    if 无效:
        print(f"\n  ★★ 作废：{len(无效)} 次运行的经验环**非空**（"
              + "、".join(f"{x['夹具']}/{x['臂']}" for x in 无效)
              + "）⇒ 两臂不是'只差外挂知识'，这批数字不能用，必须修隔离后重跑。")
    暖绿 = sum(1 for x in 结果 if x["臂"] == "暖" and x["绿"])
    冷绿 = sum(1 for x in 结果 if x["臂"] == "冷" and x["绿"])
    print(f"\n  暖臂绿 {暖绿} · 冷臂绿 {冷绿}"
          + ("（**作废数字**，见上）" if 无效 else ""))
    if 无效:
        return 2
    for f in 夹具们:
        暖 = [x for x in 结果 if x["夹具"] == f.name and x["臂"] == "暖"]
        冷 = [x for x in 结果 if x["夹具"] == f.name and x["臂"] == "冷"]
        if 暖 and 冷:
            判 = ("知识库有边际贡献" if 暖[0]["绿"] and not 冷[0]["绿"]
                  else "两臂同绿 ⇒ **该题无边际贡献**" if 暖[0]["绿"] and 冷[0]["绿"]
                  else "两臂同红 ⇒ 都没修出来" if not 暖[0]["绿"] and not 冷[0]["绿"]
                  else "★ 冷绿暖红 ⇒ 反常，需查（随机性/额度/超时）")
            print(f"    {f.name:28s} {判}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
