# -*- coding: utf-8 -*-
"""**独立缺陷数**：从判据之间的调用图算出"真正独立的红有几条"（R441，口径 160）。

★ 为什么（实测）：
````
判三十四（交付说明数字过期）→ **传染 25 条**
判十一  （死函数）           → **传染 17 条**
⇒ 我第一次看到"红 40 条"，**以为坏了 40 处**；
  修掉判三十四之后变成"红 24 条"，**缺陷一个没修** ✗
````
★ 病因：**全局条件被写在几十条判据的 body 里** —— 它们一红，整片变红。
⇒ 口径 160：**报告红数时必须同时报「独立缺陷数」**。

★ 怎么算「独立」：**靠依赖图，不靠数。**
````
把"每条判据的函数体里调用了哪些判据"建成图；
一个红的判据，若它能由**别的红判据**到达 ⇒ 它是**被传染的**（不算独立）；
只有**入边全是绿/无入边**的红，才是**独立缺陷**。
````
★ 这里用**保守**口径：**只把"红的判据"之间**的调用算传染
  （绿判据里调红判据是不可能的，因为那就红了）——
  所以传染边只可能在"红 → 红"之间。**不做传递闭包猜测，只按直接调用。**
"""
import io
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

仓 = pathlib.Path(__file__).resolve().parent
P = 仓 / "orch_product_chain.py"
if not P.is_file():
    P = pathlib.Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator\tests\orch_product_chain.py")


def 判据名们(src: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^def (判[一二三四五六七八九十百零][\w]*)", src, re.M)]


def 体(src: str, 名: str) -> str:
    i = src.find(f"def {名}(")
    if i < 0:
        return ""
    j = len(src)
    for m in re.finditer(r"^def ", src[i + 10:], re.M):
        j = i + 10 + m.start()
        break
    return src[i:j]


def main(结果文件: str | None = None) -> int:
    src = P.read_text(encoding="utf-8", errors="replace")
    名们 = 判据名们(src)
    体们 = {n: 体(src, n) for n in 名们}
    # ── 调用图：判据 A 的体里出现 "判X(" ⇒ A 依赖 X
    图: dict[str, set[str]] = {}
    for n in 名们:
        图[n] = {m.group(1) for m in re.finditer(r"\b(判[一二三四五六七八九十百零][\w]*)\s*\(", 体们[n])
                 if m.group(1) in 体们 and m.group(1) != n}
    # ── 红名单
    红: set[str] = set()
    if 结果文件 and pathlib.Path(结果文件).is_file():
        for 行 in pathlib.Path(结果文件).read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"红\|\s*(判[一二三四五六七八九十百零][\w]*)", 行)
            if m and m.group(1) in 体们:
                红.add(m.group(1))
    if not 红:
        print("   ⚠ 没拿到红名单 —— 用 `逐条跑判据.py > 结果.txt` 的产物，"
              "再 `独立缺陷数.py 结果.txt`")
        # ★ 仍把"依赖图里入度最大的"报出来（那是最容易造成传染的）
        _入 = {n: 0 for n in 名们}
        for a, 依 in 图.items():
            for b in 依:
                _入[b] = _入.get(b, 0) + 1
        _排 = sorted(_入.items(), key=lambda kv: -kv[1])[:8]
        print("   ★ **被最多判据调用的**（= 一旦红就传染最广）:")
        for n, k in _排:
            if k:
                print(f"      {k:3} 条依赖它 · {n}")
        return 0

    被传 = set()
    for a in 红:
        for b in 图.get(a, ()):
            if b in 红:
                被传.add(b)
    独立 = 红 - 被传
    print(f"   ★ 红 **{len(红)}** 条 ⇒ **独立缺陷 {len(独立)}** 条 "
          f"（被传染 {len(被传)} 条）")
    print(f"   ★ **独立缺陷**（入边无红）：")
    for n in sorted(独立):
        print(f"      · {n}")
    if 被传:
        print(f"   ★ **被传染的**（它们的红来自上面的某条）：")
        for n in sorted(被传):
            _源 = [a for a in 红 if n in 图.get(a, ())]
            print(f"      · {n}  ←  {('、'.join(sorted(_源)))[:80]}")
    print()
    print("   ★ **口径**：**『数到几条红』取决于判据之间的依赖图，不取决于缺陷数**。"
          "报告红数时必须同时报独立缺陷数（口径 160）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
