"""工程开发派发器（engine_dev）：无人值守跑【档案声明】的工程级机器判据门。

分工与纪律：
  - 项目无关：本模块不含任何项目名 / 路径 / 语言 / 命令。一切来自 profile。
  - 锚定档案声明的入口单元（源码索引）→ 依次跑档案声明的判据门
    （G6 语言无关 GateSpec evaluate，纯机器退出码判定）→ 全过 promoted /
    任一失败 convergence_failed → 实时回执写档案声明的 audit_dir。
  - 只做 build/test 机器门，不改一行源码：判据门不亲自读工程逻辑。
    诚实边界：机制只报"能编译/自身测试过否"的真值，不承诺产物无缺。
  - 档案未声明门 ⇒ 不跑门、不假装通过（显式 error，绝不用恒真占位门冒充）。

用法：
  python -m agentscope_orchestrator.engine_dev                  # 跑一次
  ... engine_dev.py --loop 600 --gates <名1>,<名2>              # 每600s一轮
  ... engine_dev.py --profile path/to/orchestrator.profile.yml
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from agentscope_orchestrator.index import UnitIndex
from agentscope_orchestrator.gate_spec import evaluate
from agentscope_orchestrator import ts_receipt
from agentscope_orchestrator import profile as prof_mod

logger = logging.getLogger("engine_dev")
logger.addHandler(logging.StreamHandler(sys.stderr))
logger.setLevel(logging.INFO)


def _p() -> prof_mod.Profile:
    return prof_mod.get_profile()


def build_rust_index(prof: prof_mod.Profile | None = None) -> UnitIndex:
    """按档案 source 规格建源码索引（语言无关：后缀由档案 globs 决定）。"""
    prof = prof or _p()
    idx = UnitIndex()
    skip = prof.source.skip
    globs = prof.source.globs or ("**/*.rs",)
    for rel in prof.source.roots:
        root = Path(rel)
        if not root.is_absolute():
            root = prof.repo_root / root
        if not root.is_dir():
            logger.warning("[engine_dev] 源码根不存在：%s", root)
            continue
        for g in globs:
            for p in sorted(root.rglob(g)):
                if not p.is_file():
                    continue
                if skip and any(s in p.parts for s in skip):
                    continue
                try:
                    rel_p = p.relative_to(prof.repo_root).with_suffix("")
                    idx.register(".".join(rel_p.parts), p)
                except ValueError:
                    idx.register(p.stem, p)
                idx.register(str(p), p)
    return idx


# 兼容旧名（外部仍可能引用）
build_index = build_rust_index


def anchor(idx: UnitIndex, unit_id: str,
           prof: prof_mod.Profile | None = None) -> Path | None:
    """锚定单元：支持「源码根前缀式」点分键与「裸名」两种写法。

    前缀来自档案 source.roots（如 src → src.某模块），工具不假定任何具体根名。
    """
    if not unit_id:
        return None
    prof = prof or _p()
    # 源码根 → 前缀形态（统一分隔符，去掉首尾斜杠）
    prefixes = [f"{str(r).replace(chr(92), '/').strip('/')}."
                for r in (prof.source.roots or ())]
    cands = [unit_id]
    for pre in prefixes:
        if unit_id.startswith(pre):
            cands.append(unit_id[len(pre):])
        else:
            cands.append(pre + unit_id)
    for k in cands:
        p = idx.query(k)
        if p and p.is_file():
            return p.resolve()
    return None


def gate_suite(prof: prof_mod.Profile | None = None) -> list[dict]:
    """工程级判据门 = 档案声明（工具不持有任何具体命令）。"""
    prof = prof or _p()
    return [dict(g) for g in prof.gates]


def run_round(idx: UnitIndex, gates: list[dict],
              prof: prof_mod.Profile | None = None) -> dict:
    prof = prof or _p()
    anchor_main = anchor(idx, prof.anchors.main)
    anchors = {u: (str(anchor(idx, u)) if anchor(idx, u) else None)
               for u in prof.anchors.core}
    result = {"ts": time.time(),
              "round": time.strftime("%Y-%m-%d %H:%M:%S"),
              "profile": prof.name,
              "anchor": str(anchor_main) if anchor_main else None,
              "core_anchors": anchors,
              "details": []}
    if anchor_main is None:
        result["state"] = "error"
        result["error"] = (f"锚定失败：`{prof.anchors.main}` 未入源码索引"
                           f"（档案 source.roots={list(prof.source.roots)}），拒跑门")
        return result
    if not gates:
        # 无门 ≠ 通过。显式 error，避免"恒真占位门"式假绿。
        result["state"] = "error"
        result["error"] = (f"档案 `{prof.name}` 未声明任何判据门（gates）"
                           " ⇒ 无可裁决，拒判 promoted")
        result["gates"] = {}
        return result
    results, audit = evaluate(gates, anchor_main, prof.work_dir,
                              str(prof.repo_root))
    result["gates"] = results
    result["audit"] = audit
    # ★★★★★ R14：顶层裁决走**数术层格裁决**（TS-468 §6），不走 `all(Bool)`。
    #   · 在 `Fin 2^n` 的**积序**上取下确界 —— 与 `all` 取值一致（无语义回退）；
    #   · 但**带全部因由**（是哪个门红的、为什么红）—— `all` 会把它压平掉；
    #   · 空集**不判绿**（`all({})` 恒 True）；读不到与违反**分开列**。
    from agentscope_orchestrator import shushu_logic as _sl
    _值, _因由, _注 = _sl.从审计(results, audit)
    result["裁决值"] = int(_值)
    result["裁决因由"] = _因由
    result["裁决注"] = _注
    result["state"] = "promoted" if _值 else "convergence_failed"
    return result


def write_receipts(result: dict, prof: prof_mod.Profile | None = None) -> None:
    prof = prof or _p()
    prof.audit_dir.mkdir(parents=True, exist_ok=True)
    receipt = prof.audit_dir / "engine_dev_receipt.jsonl"
    live = prof.audit_dir / "engine_dev_live.json"
    with receipt.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    live.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    # 人类可读 .md 回执；机器底料仍是上方 JSON
    try:
        batch = ts_receipt.claim_batch(prof)
        red = [k for k, v in result.get("gates", {}).items() if v is not True]
        ts_receipt.render(
            repo_root=prof,
            batch=batch, window="engine_dev",
            nature="执行回执（工程级判据门机器审计）",
            topic=f"engine_dev_判据门_{time.strftime('%m%d%H%M%S')}",
            summary=(f"**state={result['state']}**；档案=`{prof.name}`；锚定 "
                     f"{result.get('anchor')}；门套件={result.get('gates')}。"),
            gates=result.get("gates"),
            notes=([f"红门待修：{red}"] if red
                   else ["全部门通过。",
                         "只证能编译/自身测试过，不承诺产物无缺。"]),
            extra_sections={"核心锚点（信息性）":
                            "\n".join(f"- {k}: {v}"
                                      for k, v in result.get("core_anchors", {}).items())})
    except Exception as e:  # noqa: BLE001 回执渲染失败不阻断机器门
        logger.warning("[engine_dev] 回执渲染失败：%s", e)


def main() -> int:
    ap = argparse.ArgumentParser(description="工程级机器判据门派发器（项目无关）")
    ap.add_argument("--loop", type=int, default=0,
                    help="轮询间隔秒数；0=只跑一轮")
    ap.add_argument("--gates", default="all",
                    help="逗号分隔门名子集；all=档案全部")
    ap.add_argument("--profile", default=None,
                    help="项目档案路径（默认自动查找 orchestrator.profile.yml）")
    args = ap.parse_args()

    prof = prof_mod.reset_profile(args.profile)
    logger.info("档案：%s（%s）", prof.name, prof.source_path or "通用空档案")
    logger.info("仓库根：%s", prof.repo_root)

    idx = build_rust_index(prof)
    logger.info("源码索引就绪：%d 单元", idx.size())
    suite = gate_suite(prof)
    if args.gates != "all":
        subset = set(x.strip() for x in args.gates.split(",") if x.strip())
        suite = [g for g in suite if g.get("name") in subset]
    logger.info("本轮机器门：%s", [g.get("name") for g in suite])

    first = True
    while first or args.loop > 0:
        first = False
        result = run_round(idx, suite, prof)
        write_receipts(result, prof)
        line = (f"[{result['round']}] state={result['state']} "
                f"gates={result.get('gates')}")
        logger.info(line)
        print(line)
        if args.loop <= 0:
            return 0 if result["state"] == "promoted" else 1
        time.sleep(args.loop)


def _compat(name: str):
    """旧名兼容层：把 engine_dev._RUST_ROOT 等映射到当前档案。

    这些名字此前是模块级硬编码常量；改为按档案动态解析，使工具不含项目知识，
    同时不破坏既有外部引用（develop / observation / repo_index 等）。
    """
    p = _p()
    if name in ("_RUST_ROOT", "ROOT"):
        return p.repo_root
    if name == "_WORK":
        p.work_dir.mkdir(parents=True, exist_ok=True)
        return p.work_dir
    if name == "AUDIT":
        p.audit_dir.mkdir(parents=True, exist_ok=True)
        return p.audit_dir
    if name == "_ANCHOR_UNIT":
        return p.anchors.main
    if name == "_CORE_UNITS":
        return list(p.anchors.core)
    if name == "_SKIP":
        return set(p.source.skip)
    raise AttributeError(name)


def __getattr__(name: str):
    return _compat(name)


if __name__ == "__main__":
    sys.exit(main())
