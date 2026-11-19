# -*- coding: utf-8 -*-
"""全仓知识图谱总账 —— **一切读数从落盘文件读**（口径 141）。

★ 与 `刷全仓索引` 的区别：那个写 `索引.json`（给 AI 的**入口**）；
  这个是**打给人看的表**（含排名、Top 缺口、跨根汇总）。
★ 两者必须同源：都读 `节点.json`/`边.json`/`引用.json`/`缺口.json`/`外部缺.json`。
"""
import sys
import pathlib
import json

sys.path.insert(0, r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agentscope_orchestrator import kg as _kg  # noqa: E402


def 读(p, 名, 取=None):
    f = p / 名
    if not f.is_file():
        return None
    try:
        j = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if 取 is None:
        return j
    if isinstance(j, dict):
        return j.get(取)
    return len(j) if hasattr(j, "__len__") else None


总 = pathlib.Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道")
计 = _kg.分根(总, 打印=lambda *_: None)
行 = []
for x in 计["根们"]:
    p = pathlib.Path(x["根"])
    d = _kg._kg目录(p)
    if not (d / "节点.json").is_file():
        行.append((p.name, None, None, None, None, None, "没建图"))
        continue
    节 = 读(d, "节点.json")
    _引 = 读(d, "引用.json")
    _引 = len(_引) if hasattr(_引, "__len__") else _引
    _边 = 读(d, "边.json")
    _边 = len(_边) if hasattr(_边, "__len__") else _边
    条 = {
        "节点": len(节) if 节 else None,
        "边": _边,
        "引用": _引,
        "未解析": 读(d, "外部缺.json", "未解析总数"),
        "真外部缺": 读(d, "外部缺.json", "真外部缺·去重数"),
        "已解析占比": 读(d, "缺口.json", "★已解析占比"),
        "半截": 读(d, "外部缺.json", "半截名字·去重数"),
    }
    行.append((p.name, 条["节点"], 条["引用"], 条["未解析"], 条["真外部缺"],
              条["已解析占比"], "齐" if 条["引用"] is not None else "缺"))

print()
print("   ═══ 全仓知识图谱总账（一切读数从落盘文件读）═══")
print(f"   {'根':22} {'节点':>9} {'引用':>9} {'未解析':>7} {'真外部缺':>8} {'解析率':>7}  产物")
_合 = {"节点": 0, "引用": 0, "未解析": 0, "真外": 0}
for 名, 节, 引, 未, 外, 率, 态 in 行:
    if 节 is None:
        print(f"   {名:22} {'—':>9} {'—':>9} {'—':>7} {'—':>8} {'—':>7}  ⏳ {态}")
        continue
    _合["节点"] += 节 or 0
    _合["引用"] += 引 or 0
    _合["未解析"] += 未 or 0
    _合["真外"] += 外 or 0
    print(f"   {名:22} {节 or 0:>9,} {引 or 0:>9,} {未 or 0:>7,} {外 or 0:>8,} "
          f"{率 if 率 is not None else 0:>6.1f}%  {'✅' if 态 == '齐' else '⚠ 缺'}")
print(f"   {'合计':22} {_合['节点']:>9,} {_合['引用']:>9,} {_合['未解析']:>7,} {_合['真外']:>8,}")
print()
建成 = [x for x in 行 if x[1] is not None]
齐 = [x for x in 建成 if x[6] == "齐"]
print(f"   ★ 根 {len(行)} 个 · 建成 {len(建成)} · 产物齐 {len(齐)} · 没建 {len(行) - len(建成)}")
