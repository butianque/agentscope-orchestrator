# -*- coding: utf-8 -*-
"""**盘「写了没人读」的字段** —— 「登记了」不等于「接上了」。

为什么需要它（而不是靠人记）：
  · R331：`绑定` 抽出来了、判据绿了，但**没有调用方**；
  · R356：`星号` 标在边上、三条判据守着**格式**，**下游一条都没读**；
  · R357：`父定义`（97,581 节点）与 `粒` **读 0 次** ——
    而注释里明写「★ 粒：跨语言通用的层级判据」。**注释不是判据。**

⇒ 把「谁在读这个字段」变成**可重复的机器读数**，而不是靠记忆。

用法：
    python tests\\kg_字段接线盘点.py            # 盘 kg.py
    python tests\\kg_字段接线盘点.py <文件>      # 盘别的文件
    python tests\\kg_字段接线盘点.py --语料       # 顺带数一数图上真有多少条带它

口径（**说清「读」怎么算**，否则数字没有意义）：
  · 写 = 源码里出现 `"字段":`（字典字面量的键）
  · 读 = 出现 `get("字段")` 或 `["字段"]`
  · 两者都算的写法（如 `x["字段"] = v`）会被**同时**计入 —— 所以
    **读>0 只是必要条件，不是充分条件**：真正的「接上了」要看**调用链**，
    这个脚本只能**把可疑对象缩到一小撮**，不能替你判定。
  · ★ **诚实边界**：这是**静态文本统计**，不是数据流分析。
    它会把「写了又自己读一遍」算成读过 ⇒ **零读才是有力的信号**，
    非零读仍需人（或判据）确认。
"""
import collections
import pathlib
import re
import sys


def 盘(源: pathlib.Path) -> tuple[list[tuple[str, int, int]], int]:
    文 = 源.read_text(encoding="utf-8")
    写 = collections.Counter(re.findall(r'"([^"\n]{1,24})"\s*:', 文))
    读 = collections.Counter(re.findall(r'(?:get\(|\[)"([^"\n]{1,24})"', 文))
    行 = [(k, 读.get(k, 0), v) for k, v in 写.items()]
    行.sort(key=lambda x: (x[1], -x[2]))
    return 行, len(文.splitlines())


def main() -> int:
    参 = [a for a in sys.argv[1:] if not a.startswith("--")]
    默认 = pathlib.Path(__file__).resolve().parents[1] / "src" / "agentscope_orchestrator" / "kg.py"
    源 = pathlib.Path(参[0]) if 参 else 默认
    if not 源.exists():
        print(f"✗ 找不到 {源}")
        return 2
    行, 行数 = 盘(源)
    零 = [x for x in 行 if x[1] == 0 and x[2] >= 3]
    print(f"   {源.name}（{行数} 行）· 字典键 {len(行)} 个")
    print(f"   ★ **写了 ≥3 次、读 0 次**（最可疑的一撮，共 {len(零)} 个）：")
    if not 零:
        print("      （无）")
    for k, _r, w in 零:
        print(f"      写 {w:>3} 次 · 读 0 次    {k}")
    if "--语料" in sys.argv:
        语料 = pathlib.Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib")
        import json
        for 名 in ("节点", "边"):
            p = 语料 / ".kg" / f"{名}.json"
            if not p.exists():
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            值 = list(d.values()) if isinstance(d, dict) else d
            c = collections.Counter(k for n in 值 for k in n)
            print(f"   ★ 图上 `{名}` 里带这些字段的条数：")
            for k, _r, _w in 零:
                if c.get(k):
                    print(f"      {k:14} {c[k]:>9} 条")
    print("   ★ 口径：**零读是有力信号，非零读仍需确认**（静态统计，非数据流分析）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())