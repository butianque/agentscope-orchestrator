# -*- coding: utf-8 -*-
"""`齿单测`：**把能单独跑的齿秒级跑一遍**，并对它做**秒级变异咬合**。

## 为什么（R546 的教训，很贵）

R540 的齿我改了 3 轮才不红，每轮验证都要跑 `--selftest`：
真读数 **2392 s / 2764 s**（40~46 分钟）。而那条齿本身只需要**毫秒**。
于是每次改一行都要等 40 分钟 —— 人就会开始"看着差不多就过"，
**那正是假绿的温床**（本项目已经因为假绿栽过多次）。

⇒ 口径：**一条齿必须能单独跑、也要能单独被咬**。

## 两种模式

    python tools/齿单测.py            # 只跑齿（应该全绿）
    python tools/齿单测.py --变异      # 再对每条齿注入变异，**每条都必须变红**

`--变异` 是**判「这齿是不是真齿」的机判**：把被测函数换成坏版本，
齿必须报红；报绿 ⇒ **齿是装饰**。全程秒级。

## 边界（如实）

只覆盖**已提出来**的齿（模块级 `_齿_Rxxx(fails)`）。埋在 `--selftest` 大函数里的
齿仍然只能整包跑 —— 这个工具的作用是**推动**「提出来」，不是替代整包回归。
"""
from __future__ import annotations

import importlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _变异表(g) -> list:
    """(齿名, 变异说明, 注入, 还原)。

    注入/还原都是**对模块属性的赋值**（monkeypatch），跑完立刻还原。
    """
    原_scope = g._detect_repo_scope
    原_档门 = g._档案声明了主树门
    原_score = g._score_gates
    原增量 = g._增量应用
    return [
        # ★ R547 之后 `_detect_repo_scope` 有 4 个参数（交付物也要传）——
        #   变异必须跟着签名走，否则**变异自己 TypeError**，会被误读成「齿崩了」。
        #   真读数：上一版只接 3 个参数 ⇒ 齿报 `TypeError: lambda() takes …`
        #   而工具正确判成「齿自己崩了（不是咬住）」—— 工具是对的，变异是坏的。
        ("R540", "接线：调用时不传档案门",
         lambda: setattr(g, "_detect_repo_scope",
                         lambda s, a, m=None, d=None: 原_scope(s, a, None, d)),
         lambda: setattr(g, "_detect_repo_scope", 原_scope)),
        ("R540", "接线：调用时不传交付物（R547 的收窄没生效）",
         lambda: setattr(g, "_detect_repo_scope",
                         lambda s, a, m=None, d=None: 原_scope(s, a, m, None)),
         lambda: setattr(g, "_detect_repo_scope", 原_scope)),
        ("R540", "行为：档案门不参与主树判定（= 回到只看模型自撰门）",
         lambda: setattr(g, "_档案声明了主树门", lambda m: False),
         lambda: setattr(g, "_档案声明了主树门", 原_档门)),
        ("R540", "行为：档案门恒算（= 把所有目标都拖进主树模式）",
         lambda: setattr(g, "_档案声明了主树门", lambda m: True),
         lambda: setattr(g, "_档案声明了主树门", 原_档门)),
        # ★ R548 的变异：把 `{repo}` 的展开**退回事故现场的行为**（用 cwd_根 而不是 repo_root）。
        #   做法：包一层 `_score_gates`，把 repo_root 丢掉、只用 cwd_根 兜底 ——
        #   这正是 `cwd_根` 分支**优先于** `repo_root` 时的可观测后果（门在副本上跑）。
        ("R548", "行为：`{repo}` 展开成轮次副本（= 回到 R548 事故现场）",
         lambda: setattr(g, "_score_gates",
                         lambda wd, f, gates, repo_root=None, cwd_根=None:
                         原_score(wd, f, gates, repo_root=None, cwd_根=wd)),
         lambda: setattr(g, "_score_gates", 原_score)),
        # ★ R549 的变异：把 `主树=` 丢掉 ⇒ 退回「备份副本、改副本、判副本」的事故现场。
        ("R549", "行为：增量裁决忽略 `主树=`（= 退回判轮次副本）",
         lambda: setattr(g, "_增量应用",
                         lambda a, b, rr, dl, 回退详情=None, 主树=None:
                         原增量(a, b, rr, dl, 回退详情)),
         lambda: setattr(g, "_增量应用", 原增量)),
    ]


def main(argv: list[str]) -> int:
    from agentscope_orchestrator import generic_goal as g

    齿表 = [
        ("R540", "主树判定 + 红门不许叫交付（假成功）", "_齿_R540"),
        ("R548", "`{repo}` 只展开成主树根（门不许在轮次副本上跑）", "_齿_R548"),
        ("R549", "增量裁决必须在主树上跑（不是轮次副本）", "_齿_R549"),
    ]
    做变异 = "--变异" in argv

    总红 = 0
    存活: list[str] = []
    齿坏: list[str] = []
    t0 = time.time()

    print(f"══ 齿单测：{len(齿表)} 条（秒级，不跑整包自检）"
          f"{' · 含变异咬合' if 做变异 else ''} ══")
    for 名, 说明, 函 in 齿表:
        f = getattr(g, 函, None)
        if f is None:
            print(f"  ✗ {名}：`{函}` 不存在（没提出来，或改名了）")
            齿坏.append(名)
            总红 += 1
            continue
        # ── ① 干净跑：必须全绿 ──
        fails: list = []
        t1 = time.time()
        try:
            f(fails)
        except Exception as e:                  # noqa: BLE001
            fails.append(f"【{名}】齿自己抛异常：{type(e).__name__}: {e}")
        耗 = time.time() - t1
        if fails:
            print(f"  ✗ {名}（{耗:.2f}s）：{说明}")
            for x in fails[:6]:
                print(f"      {x}")
            总红 += len(fails)
            # ★ 基线不绿就不许咬 —— 恒红的齿会让每条变异都"咬住"（R545 的教训）
            continue
        print(f"  ✓ {名}（{耗:.2f}s）：{说明}")

        if not 做变异:
            continue
        # ── ② 变异咬合：每条变异都必须让齿变红 ──
        for 说 in _变异表(g):
            if 说[0] != 名:
                continue
            _, 描述, 注入, 还原 = 说
            注入()
            try:
                vf: list = []
                t2 = time.time()
                try:
                    f(vf)
                except Exception as e:          # noqa: BLE001
                    vf.append(f"齿抛异常：{type(e).__name__}: {e}")
                耗2 = time.time() - t2
            finally:
                还原()
            红 = [x for x in vf if "自身异常" not in x and "抛异常" not in x]
            if 红:
                print(f"      ✓ 咬住（{耗2:.2f}s）{描述}")
            elif vf:
                print(f"      ✗ **齿自己崩了**（不是咬住！）{描述}：{vf[0][:90]}")
                齿坏.append(f"{名}/{描述}")
            else:
                print(f"      ✗ **没咬**（缺陷版本上齿仍绿）{描述}")
                存活.append(f"{名}/{描述}")

    总耗 = time.time() - t0
    未达标 = list(存活) + list(齿坏)
    print(f"\n══ 合计：红 {总红} · 墙钟 {总耗:.2f}s ══")
    if 未达标:
        if 存活:
            print("✗ 存活（齿是装饰）=", 存活)
        if 齿坏:
            print("✗ 齿自己崩了 =", 齿坏)
        return 1
    # ★★★★★ R548c：**成功那句话只能在 `总红 == 0` 时打。**
    #   原来这里只判 `未达标`（存活/齿坏）就打了「✓ 全部齿干净通过」——
    #   真读数：`红 1 · 墙钟 15.94s` 之后紧接着 `✓ 全部齿干净通过 ⇒ 是真齿`。
    #   这和 R545d 是**同一形态**：**最后一句才是人读的那句**，
    #   所以任何「逐条失败、汇总清零」的写法都是假绿。
    if 总红:
        print(f"\n✗ 有 {总红} 条齿在**干净源码上**就红 ⇒ 不许说「全部通过」")
        return 1
    if 做变异:
        print("✓ 全部齿干净通过，且每条变异都被咬住 ⇒ 是真齿")
    print("  边界：只覆盖**已提出来**的齿；埋在大函数里的仍要整包 `--selftest`。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
