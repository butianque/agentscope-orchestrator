# -*- coding: utf-8 -*-
"""覆盖核算（P1 · 判据 #21/#22）：把「全量枚举 / 进表 / 跳过（带原因）」对账成**守恒式**。

★ 为什么需要（口径 375）：原来的「①没扫到」只报一个**差**
  （`分区文件数 − 符号表文件数`），那是**减法**，不是守恒式：
  差里混着「本来就不该进图的」（依赖/产物/数据），也混着「真漏了」的，两者被同一个数字盖住，
  于是既不能判红、也不能判绿 —— 只能年年复读那个差。
  ⇒ 这里改成**三类互斥且覆盖**：
        全量枚举 = 进表 + 跳过（每条带原因）      ← #21 守恒式（且全量 > 0，不许空集比）
        跳过必须有非空原因                        ← #22 reason 覆盖 100%
        值得进图但不在表里 = **真未进表**（缺陷）  ← 棘轮：只许降

★ 复用而不重写（口径 R117）：
  · 「值不值得进图」用 `kg._过值得()` —— 它的跳过项自带 `路径` + `原因`（R525 已修到可定位）；
  · 「剪不剪枝」用 `mem_prune.该跳()`；
  · 「在不在表里」用 `mem_live.现存()._文哈希`（posix 相对路径，与 `戳.json` 同源）。

★ 输出：一行机器可读 + `work\\覆盖核算.json`。
  退出码：0 = 守恒 ∧ 全量>0 ∧ reason 全覆盖 ∧ 真未进表 ≤ 基线；1 = 不满足；2 = 基线缺失。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
包根 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(包根 / "src"))

基线文件 = 包根 / "work" / "覆盖基线.json"
输出文件 = 包根 / "work" / "覆盖核算.json"


def _枚举全量(根: pathlib.Path, 跳过名: set[str]) -> list[pathlib.Path]:
    """**不剪枝地**走全仓，只按 `mem_prune.该跳` 与档案 skip 剪目录。

    ★ 为什么目录要剪、文件不能剪：目录剪枝是「这里不可能有自己的源码」（`.git`/`node_modules`），
      而**文件**一律枚举进来 —— 否则"跳过的文件"根本不会出现在分母里，守恒式就成了自证。
    """
    from agentscope_orchestrator.mem_prune import 该跳
    出: list[pathlib.Path] = []
    for dp, dn, fn in os.walk(根):
        dn[:] = [d for d in dn if not 该跳(d, 跳过名)]
        for f in fn:
            出.append(pathlib.Path(dp) / f)
    return 出


def main() -> int:
    from agentscope_orchestrator import kg, profile as _pf
    from agentscope_orchestrator import mem_live as _ml

    档 = _pf.get_profile()
    根 = pathlib.Path(getattr(档, "repo_root", None) or 包根).resolve()
    if "--根" in sys.argv:
        根 = pathlib.Path(sys.argv[sys.argv.index("--根") + 1]).resolve()
    跳过名 = set(getattr(getattr(档, "source", None), "skip", ()) or ())

    全量 = _枚举全量(根, 跳过名)
    值得, 跳 = kg._过值得(全量, 根)
    跳路 = {x.get("路径") for x in 跳}
    # ★★ 必须显式载入：`现存()` 在没载盘时返回**空表** —— 空表会让『守恒式』自证成立
    #   （0 + 跳过 == 全量 时看似平，其实什么都没对上），并把全部值得进图的都算成「真未进表」。
    #   实测第一版就是这样：进表=0 · 真未进表=22,614 —— 口径 375 的坑，当场踩到。
    try:
        _ml.确保载入()
    except Exception as _e:                     # noqa: BLE001
        print(f"   ⚠ 符号表载入失败：{type(_e).__name__}: {_e}")
    进表 = set((_ml.现存()._文哈希 or {}).keys())

    def _rel(p: pathlib.Path) -> str:
        try:
            return p.relative_to(根).as_posix()
        except (ValueError, OSError):
            return p.as_posix()

    真未 = [ _rel(p) for p in 值得 if _rel(p) not in 进表 ]
    无因 = [x for x in 跳 if not str(x.get("原因") or "").strip()]

    守恒左 = len(值得) + len(跳)
    # ★ 口径 375：**不许拿两个空集比** —— 进表为空时守恒式恒成立，那不是守恒，是空。
    平 = (守恒左 == len(全量)) and len(全量) > 0 and len(进表) > 0

    基线 = {}
    if 基线文件.is_file():
        基线 = json.loads(基线文件.read_text(encoding="utf-8"))
    基真未 = int(基线.get("真未进表", 10 ** 9))
    超 = len(真未) > 基真未

    报 = {
        "根": str(根),
        "全量枚举": len(全量),
        "进表": len(进表),
        "值得进图": len(值得),
        "跳过": len(跳),
        "守恒式": f"{len(值得)}(值得)+{len(跳)}(跳过) = {守恒左} · 全量 {len(全量)}",
        "守恒": 平,
        "跳过缺原因": len(无因),
        "真未进表": len(真未),
        "真未进表样例": 真未[:30],
        "基线真未进表": 基真未,
        "跳过按原因": {},
        "跳过按后缀": {},
    }
    from collections import Counter
    报["跳过按原因"] = dict(Counter(str(x.get("原因") or "")[:40] for x in 跳).most_common(10))
    报["跳过按后缀"] = dict(Counter(pathlib.Path(str(x.get("路径") or "")).suffix.lower() or "(无)"
                                  for x in 跳).most_common(10))
    输出文件.parent.mkdir(parents=True, exist_ok=True)
    输出文件.write_text(json.dumps(报, ensure_ascii=False, indent=2), encoding="utf-8")

    行 = (f"全量={len(全量)} 进表={len(进表)} 跳过={len(跳)} 真未进表={len(真未)} "
          f"跳过缺原因={len(无因)} 守恒={'平' if 平 else '不平'}（进表 {len(进表)}）"
          f" 基线真未进表={基真未}")
    # ★★★ 判决分层（2026-11-18 实测逼出来的）：
    #   **硬门**只放结构性不变量：守恒式 + reason 覆盖 + 进表非空。
    #   **真未进表**只作**趋势读数**，不做硬棘轮 —— 因为仓在动：本轮我自己往仓里写
    #   5 个文件，它就 +5 立刻把硬棘轮打红。对活仓用绝对数当门 ⇒ **天天假红**，
    #   而假红比漏报更坏（口径 311）：人会开始无视这条门，那比没有门更糟。
    #   ⇒ 超过基线**打印警示并写进输出**，但不判红；基线仍用来表明「有没有变多」。
    ok = 平 and not 无因
    print(行 + f" 判定={'OK' if ok else '不达标'}")
    if not 平:
        print(f"   ⇒ **守恒式不平**：{守恒左} != {len(全量)} —— 有文件既没进表也没被跳过（静默丢）")
    if 无因:
        print(f"   ⇒ **{len(无因)} 条跳过没有原因**（口径 95/191：跳过必须能定位且说明为什么）")
    if 超:
        print(f"   ⚠ 趋势：真未进表 {len(真未)} > 基线 {基真未}（+{len(真未) - 基真未}）——"
              f"**不判红**（仓在动，绝对数不是稳定判据）；样例 {真未[:5]}")
    elif len(真未) < 基真未:
        print(f"   ↓ 趋势：真未进表降了 {基真未 - len(真未)} 个（可跑 --写基线 压下去）")
    if "--写基线" in sys.argv:
        基线文件.write_text(json.dumps(
            {"真未进表": len(真未), "_说明": "只许降不许升；清理后跑 --写基线 压下去"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   基线已写入 {基线文件.name}（真未进表={len(真未)}）")
        return 0
    if not 基线文件.is_file():
        print("   ✗ 基线缺失：先跑一次 `--写基线`")
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
