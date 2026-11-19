# -*- coding: utf-8 -*-
"""**Lean 三方对账**（目标⑥）：正则（文本级） × elaborator（确证级） × 我的图。

★ 这笔账是**以前欠的**（`工程声明名-elaborator.json` 里我自己写着）：
````
「本轮想拿 `mem_symbols` 的 `lean` 正则在同一批文件上对账，结果
 `★正则抽出 = 9` 条，全是 `['abbrev','axiom','class','def']` ——
 **是我取 `group(1)` 取到了关键字而不是名字**。
 ⇒ **对账不成立，那些数一个都不许用。**
 要重做得先读正则的分组结构（「不要猜数据的形状，去读它的生产者」）。」
````
★ 而真相只要读一次 `groupindex`：
````
`_m._定义正则["lean"].groupindex == {'kind': 1, 'name': 2}`
⇒ **名字在 `group(2)`，我取的是 `group(1)`（= `kind`）** ✗
````
★★ 口径 157：**能自动打印的东西，不要靠记** —— 这就是一例。

★ 三方各自是什么：
````
① **正则**（`mem_symbols._正则抽取`）—— **文本级**，只看源文本的形状
② **elaborator**（`工程声明名-elaborator.txt`）—— **确证级**，Lean 自己登记的
   （53,411 行全限定名；`★作用域`: `import MyMeta` 只覆盖 **790/1,832 模块**）
③ **我的图**（`kg.灌Lean确证` 灌进去的 `lean确证.json`）—— 目标⑥要合流的那张
````
★ 对账要回答的问题（口径 113：**两边都报**）：
````
· 正则有、elaborator 没有 ⇒ **假阳候选，不许当确证**
· elaborator 有、正则没有 ⇒ `_proof_`/`_private` 这类**自动名**（正则做不到的）
· 两者都有、我的图没有 ⇒ **合流漏了**（目标⑥的 bug）
````
"""
import pathlib
import re
import sys

sys.path.insert(0, r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import agentscope_orchestrator.mem_symbols as _m  # noqa: E402

根 = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else r"E:\基础设施\离散时空认知物理基础研究\太虚元道\形式化验证")
确证档 = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else
    r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator\products\mem\工程声明名-elaborator.txt")

# ── ① 先读正则的分组结构（★ 这就是那笔账的正确做法）
规则 = _m._定义正则.get("lean")
print(f"   ★ 正则分组结构: groupindex={规则.groupindex} · groups={规则.groups}")
名字组 = 规则.groupindex.get("name")
if 名字组 is None:
    print("   ✗ **这个正则没有具名 `name` 组** ⇒ 不许猜组号，停下")
    raise SystemExit(1)
print(f"   ★ **名字在 group({名字组})** —— 我上次取的是 group(1)（= "
      f"`{list(规则.groupindex)[0]}`）⇒ 抽到的是关键字 ✗（这笔账的真相）")

# ── ② elaborator 确证名
确证名 = set()
if 确证档.is_file():
    for 行 in 确证档.read_text(encoding="utf-8", errors="replace").splitlines():
        s = 行.strip()
        if s:
            确证名.add(s)
    print(f"   ★ elaborator 确证名: **{len(确证名)}** 个")
else:
    print(f"   ✗ 确证档不在: {确证档}")

# ── ③ 正则抽（**用正确的组号**）
文件们 = sorted(根.rglob("*.lean")) if 根.is_dir() else []
print(f"   ★ `.lean` 文件 {len(文件们)} 个（根 {根.name}）")
正则名 = set()
_命中 = 0
for f in 文件们[:4000]:
    try:
        文本 = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for mm in 规则.finditer(文本):
        n = mm.group(名字组)
        if n:
            正则名.add(n.strip())
            _命中 += 1
print(f"   ★ 正则抽出: **{len(正则名)}** 个名字（命中 {_命中} 处）")
print(f"      样例: {sorted(正则名)[:6]}")

# ── ④ 三方对账（口径 113：两边都报）
_假阳 = sorted(正则名 - 确证名) if 确证名 else []
_缺 = sorted(确证名 - 正则名) if 确证名 else []
# 确证名是全限定；正则名是短名 ⇒ 用**末段**比对（★ 这是口径问题，必须说明）
_确证末 = {x.rsplit(".", 1)[-1] for x in 确证名}
_假阳末 = sorted(正则名 - _确证末)
_缺末 = sorted(_确证末 - 正则名)
print()
print(f"   ★ 按**全限定名**比（口径严）：正则独有 {len(_假阳)} · 确证独有 {len(_缺)}")
print(f"   ★ 按**末段**比（口径宽）：正则独有 {len(_假阳末)} · 确证独有 {len(_缺末)}")
print(f"   ★ 口径说明：正则只能抽到**短名**，elaborator 是**全限定名** ⇒")
print(f"      两个口径必须**一起报**，只报一个就是假读数（口径 155）")
if _假阳末:
    print(f"      正则独有样例（**假阳候选，不许当确证**）: {_假阳末[:8]}")
if _缺末:
    print(f"      确证独有样例（**正则做不到的自动名**）: {_缺末[:8]}")

# ── ⑤ 我的图里的 lean 确证
import json  # noqa: E402
仓 = pathlib.Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator\work\图")
print()
for d in sorted(仓.glob("*/lean确证.json")):
    try:
        j = json.loads(d.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠ {d.parent.name}/lean确证.json 读不了（{type(e).__name__}）")
        continue
    print(f"   ★ 我的图 {d.parent.name}/lean确证.json: keys={list(j)[:6]}")
    for k, v in list(j.items())[:8]:
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        print(f"      {k}: {s[:130]}")
