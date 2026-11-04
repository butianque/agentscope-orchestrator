"""纠偏判据 —— 证明「AI 被项目事实纠正」，而不是只证明「工具能跑」。

## 这个判据要证的那件事（整套东西的落脚点）

用户的原话：

> 随着项目时间越做越久，外部资料知识越来越全，**AI 对自身训练数据依赖被门强行纠正**。
> 不然怎么做都是 AI 本身的数据，结论也会越来越倾向原来的训练数据。

⇒ 所以这里**不测"工具返回了字符串"**（那种判据谁都能过），
   测的是三件事**连起来的因果链**：

```
① 项目有一条**与训练数据相反**的口径
② 这条口径**必然出现在喂给模型的提示里**（不靠模型自觉去调工具）
③ 模型若照训练惯性写 ⇒ **判据门变红**（"纠正"必须是有牙齿的，不能只是提示）
```

★ 三段缺一不可：
  - 缺 ① ⇒ 没有可纠的东西，测的是空气；
  - 缺 ② ⇒ 口径只躺在库里，模型看不见（等于没有）；
  - 缺 ③ ⇒ "纠正"只是个说法 —— 模型照样能用训练先验写，而且**看不出来**。

## 为什么特意挑「与训练数据相反」的口径

如果口径是"要写测试"这种，训练数据里本来就有 ⇒ 分不清是口径起作用还是模型本来就懂。
必须挑一个**通用做法明确是另一个样**的：

```
通用做法（训练数据里全是）：  刻度按数量级自动切 Hz/kHz/MHz
本项目口径：                 刻度**一律 kHz**，不许切
```

★ 这条口径不是我编的 —— 它是本仓真实事故的沉淀
  （0.13.10 实测：冷组模型自造 `if abs(x)>=1: kHz else Hz`）。
  拿真事故当判据素材，比编一个好。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

仓 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(仓))

_桶 = Path(__file__).resolve().parent / "_run_all_state" / "correction"
shutil.rmtree(_桶, ignore_errors=True)
_桶.mkdir(parents=True, exist_ok=True)
os.environ["ORCH_STATE_DIR"] = str(_桶)
os.environ.pop("ORCH_KB_DIR", None)

from agentscope_orchestrator import project_kb as KB      # noqa: E402
from agentscope_orchestrator import context_view as CV    # noqa: E402
from agentscope_orchestrator import dispatch as DP        # noqa: E402
from agentscope_orchestrator import blind_spot as BS      # noqa: E402

失败: list[str] = []


def 断言(条件, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}")
    else:
        print(f"  FAIL {说明}")
        失败.append(说明)


KB.播种()

# ══════════════════════════════════════════════════════════════════════════
print("① 造一条**与训练数据相反**的项目口径")
# ★ 通用做法（训练数据里全是）：按数量级自动切单位。本项目相反：一律 kHz。
口径正文 = ("刻度一律用 `kHz`，**禁止**按数量级自动切换 Hz/MHz。"
          "通用做法是 `if abs(x)>=1: kHz else Hz` —— 本项目**明令禁止**那种写法，"
          "因为它让同一张图上的刻度单位不一致，读数要靠猜。")
try:
    KB.写({"id": "口径-刻度一律kHz禁切换", "类别": "规则", "强度": "禁止",
           "正文": 口径正文, "范围": ["plot/*.py"]})
except Exception:                                          # noqa: BLE001
    pass
有, 说, _ = KB.命中("口径-刻度一律kHz禁切换", 最低分=0.25, 最少词元=2)
断言(有, f"口径写进库且能命中（{说[:60]}）")
断言("禁止" in 口径正文 and "kHz" in 口径正文, "这条口径本身是「与通用做法相反」的")

# ══════════════════════════════════════════════════════════════════════════
print("② 这条口径**必然出现在喂给模型的提示里**（不靠模型自觉调用）")
# 走真实的分工线：一条没有判据、知识库不该命中的证据
# —— 看分工线交给 LLM 时，提示里有没有带上这条口径。
证据 = "plot/kHz.py 第 30 行：刻度输出用了自动切换单位的分支"

# 先单独验语义入口取得到
单 = CV.看(证据, 上限=1500)
断言("kHz" in 单["正文"], f"语义入口把这条口径取出来了（{len(单['正文'])} 字）")
断言("禁止" in 单["正文"] or "一律" in 单["正文"],
     "取出来的是**正文口径**，不是「知识库里有一条」这种统计串")

# 再验分工线交给 LLM 的那一份里带没带
裁 = DP.裁(命令=["develop", "plot/kHz.py"], 证据=证据,
            判据已命中=False, 判据说明="没有判据覆盖刻度单位",
            项目根=str(_桶))
断言(裁.该谁 in ("LLM", "台账", "知识"),
     f"分工线给出了裁定（该谁={裁.该谁}，来源={裁.来源}）")
交代 = DP.给LLM的交代(裁)
断言(bool(交代.strip()), f"交代文本非空（{len(交代)} 字）")
# ★★ 这一条是**整个判据的核心断言**
带上了 = ("kHz" in 交代) or ("kHz" in (裁.依据 or ""))
断言(带上了,
     "**交给 LLM 的交代里带上了项目口径**（这是「必然被喂到」的证明；"
     "若只在模型自觉调用工具时才有，这条为红）")
if not 带上了:
    print(f"       交代前 200 字：{交代[:200]!r}")
    print(f"       依据前 200 字：{(裁.依据 or '')[:200]!r}")

# ══════════════════════════════════════════════════════════════════════════
print("③ 照训练惯性写 ⇒ **判据门变红**（纠正必须有牙齿）")
#
# ★★★ 2026-11-03 改：这里第一版是**我手写的两条 if 检查**，不是真机制。
#   那只能证明"这段话在逻辑上能被判"，**证明不了"真判据门机制能判它"**。
#   ⇒ 现在改成走**真的 `gate_spec.evaluate`**（合法门类型表里那 14 种之一），
#     这才是"真机验收"该有的样子。
#
#   门怎么造：两条门 ——
#     · `grep_forbidden` 禁出现按数量级切换单位的写法
#     · `grep_required`  必须有 kHz 常量
#   用**同一条门集**判两份产物，看它是不是"惯性红 / 合规绿"。
from agentscope_orchestrator import gate_spec as GS        # noqa: E402

真仓 = _桶 / "载荷仓"
shutil.rmtree(真仓, ignore_errors=True)
真仓.mkdir(parents=True)
# 门用 path 指相对文件，所以产物就放在仓里
产物 = 真仓 / "plot_刻度.py"

门集 = [
    {"name": "禁自动切单位", "type": "grep_forbidden",
     "pattern": r"if\s+abs\(", "path": "plot_刻度.py"},
    {"name": "必须有kHz", "type": "grep_required",
     "pattern": r"kHz", "path": "plot_刻度.py"},
]

# 惯性产物：训练数据里那种"按数量级自动切单位"
惯性产物 = ("def 刻度(x):\n"
        "    return f'{x/1000:.1f} kHz' if abs(x) >= 1 else f'{x:.0f} Hz'\n")
# 按项目口径写：一律 kHz
本分产物 = ("def 刻度(x):\n"
        "    return f'{x/1000:.1f} kHz'\n")


def 跑真门(源: str) -> tuple[bool, list]:
    产物.write_text(源, encoding="utf-8")
    res, audit = GS.evaluate(门集, 产物, 真仓, cwd_default=str(真仓),
                             file_root=真仓)
    return bool(res) and all(res.values()), [a for a in audit]


绿1, 审1 = 跑真门(惯性产物)
绿2, 审2 = 跑真门(本分产物)
断言(not 绿1, f"**照着训练惯性写**（自动切单位）⇒ 真判据门**变红**："
              f"{[(a.get('gate'), a.get('ok')) for a in 审1]}")
断言(绿2, f"**按项目口径写**（一律 kHz）⇒ 真判据门绿："
          f"{[(a.get('gate'), a.get('ok')) for a in 审2]}")
断言(set(门集[0].keys()) >= {"name", "type", "pattern", "path"},
     "用的是**合法门类型表里的真门**（不是自造门型）")
断言(GS.合法门类型() >= {"grep_required", "grep_forbidden"}
     if hasattr(GS, "合法门类型") else True,
     "门型取自机制自己的合法表")

# ══════════════════════════════════════════════════════════════════════════
print("③·补 ★★★ 知识库规则 **一键变门** 后必须仍然有区分力")
#
# ## 这一条是**真缺陷**逼出来的（2026-11-03），必须钉死
#
# `KB.门()` 把人的自然语言片断塞进 `patterns`，而 `gate_spec` 拿它当**正则**跑。
# 两边对不上的后果是**静默假绿**：
#
#     规则：禁止 `if abs(x)>=1` 这种写法
#     ⇒ 模式：`if abs(x)>=1`
#     ⇒ 当正则跑：`(x)` 成了**分组** ⇒ 实际在找 `if absx>=1` 之类
#     ⇒ 产物里明明白白写着 `if abs(x)>=1`，门却**永远绿**
#
# 实测（改之前）：惯性产物与合规产物**都过门** ⇒ 判据零区分力，
# 而门表里那条"禁止自动切单位"看起来**管得严严的**。
#
# ★ 这是"门墙"最隐蔽的一种失效：**它不报错、不空缺、门数也对，就是不管事。**
#
# ⇒ 判据必须验的是**区分力**（惯性红 + 合规绿），不是"门转出来了没有"。
function_ok = False
KB仓 = _桶 / "kb门仓"
shutil.rmtree(KB仓, ignore_errors=True)
(KB仓 / "plot").mkdir(parents=True)
KB产物 = KB仓 / "plot" / "刻度.py"
KB产物.write_text("placeholder\n", encoding="utf-8")

try:
    KB.写({"id": "禁则-刻度不许自动切单位", "类别": "禁则", "强度": "禁止",
           "正文": "禁止 `if abs(x)>=1` 这种按数量级自动切换单位的写法",
           "范围": ["plot/*.py"]})
except Exception:                                          # noqa: BLE001
    pass       # 已存在就跳过（判据要能重复跑）
try:
    KB.写({"id": "规则-刻度必须kHz", "类别": "规则", "强度": "必须",
           "正文": "刻度一律写 `kHz`", "范围": ["plot/*.py"]})
except Exception:                                          # noqa: BLE001
    pass

KB门 = KB.门(仓=str(KB仓), 文件="plot/刻度.py")
断言(len(KB门) >= 2,
     f"两条规则转成门（实得 {len(KB门)} 条）——若为 0，多半是范围/文件过滤写错了")
断言(KB门 and not any("__未转门__" in g for g in KB门),
     "**返回列表里不许混进说明性字典**（那样 `gate_spec` 会整批拒掉合法门）")
# ★ 元信息走独立通道（`未转门()`），不走返回列表
断言(isinstance(KB.未转门(), dict), f"未转门信息走独立通道（{list(KB.未转门())[:3]}）")

# 带括号/点的模式必须被**转义**，否则它当正则跑时匹配不到
模式们 = [p for g in KB门 for p in (g.get("patterns") or [])]
断言(any("\\(" in p for p in 模式们),
     f"含括号的模式**被转义了**（{模式们}）—— 不转义就会静默假绿")

KB产物.write_text("def f(x):\n    return 'kHz' if abs(x)>=1 else 'Hz'\n", encoding="utf-8")
_kb1, _kba1 = GS.evaluate(KB门, KB产物, KB仓, cwd_default=str(KB仓), file_root=KB仓)
KB产物.write_text("def f(x):\n    return 'kHz'\n", encoding="utf-8")
_kb2, _kba2 = GS.evaluate(KB门, KB产物, KB仓, cwd_default=str(KB仓), file_root=KB仓)
断言(_kb1 != _kb2,
     f"**KB 转出的门有区分力**：惯性 {_kb1} vs 合规 {_kb2}")
断言(not all(_kb1.values()) and all(_kb2.values()),
     f"惯性 ⇒ 红、合规 ⇒ 绿（惯性因由：{[x.get('cause') for x in _kba1]}）")

# ══════════════════════════════════════════════════════════════════════════
print("④ 三段因果链合起来：口径 → 提示 → 门")
# 这一条把前三段串起来：**只要模型不听口径，门就一定拦得住**；
# **只要门在，模型就必须按口径改**。两句都是机器可判的。
断言(带上了 and not 绿1 and 绿2,
     "口径进了提示 **且** 惯性写法被门拦、合规写法过门 ⇒ "
     "「AI 被项目事实纠正」这条因果链**闭合**")
断言(带上了, "（其中「口径进提示」这一环不依赖模型自觉去调用工具）")

# ══════════════════════════════════════════════════════════════════════════
print("⑤ 反面：口径**不在库**时，不许假装被纠正了")
os.environ["ORCH_STATE_DIR"] = str(_桶 / "空根")
try:
    KB.播种()
except Exception:                                          # noqa: BLE001
    pass
空 = CV.看("刻度一律kHz", 要什么="口径", 上限=600)
断言("优先于通用做法" not in 空["正文"],
     f"空库里**不装命中**（{空['正文'][:50] or '（空）'}）")
空裁 = DP.裁(命令=["develop", "x.py"], 证据="刻度输出用了自动切换单位",
              判据已命中=False, 判据说明="无", 项目根=str(_桶 / "空根"))
空交 = DP.给LLM的交代(空裁)
断言("kHz" not in 空交 or "没有" in 空交.lower(),
     "空库时交代里**不会凭空冒出项目口径**（假纠正比不纠正更坏）")
os.environ["ORCH_STATE_DIR"] = str(_桶)

# ══════════════════════════════════════════════════════════════════════════
print("⑥ 同一条口径第二次遇到时，盲区台账不该重复记（别把已知的当新的）")
前 = BS.统计().get("条数", 0)
DP.记这次(证据=证据, 上下文="plot/kHz.py:30", 来源="判据:纠偏测试")
DP.记这次(证据=证据, 上下文="plot/kHz.py:31", 来源="判据:纠偏测试")
后 = BS.统计().get("条数", 0)
断言(后 - 前 <= 1,
     f"同一症状两次 ⇒ 台账最多加 1 条（前 {前} 后 {后}）—— 归一化生效")

print()
if 失败:
    print(f"纠偏判据：红 {len(失败)} 项 —— {失败[0]}")
    sys.exit(1)
print("纠偏判据：绿 —— 口径与训练相反、必然进提示、惯性写法被门拦、空库不装、盲区不重复记")
