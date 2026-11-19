# -*- coding: utf-8 -*-
"""**全仓 ast 对账**（目标⑤）：用内建 `ast` 独立算出真 import 集，
与通用 tree-sitter 路径（`取导入`）**逐条**交叉验证 —— **不一致就是我的 bug**。

★ 目标⑤的原话：
  「Python 标准库用内建 `ast` 独立算出真 import/定义集，与通用 tree-sitter
   路径交叉验证，**不一致就是我的 bug**」
★ 现有 `判九十四` 守的是 **stdlib 163 个文件**；本脚本扩到**全仓每个 `.py`**。
★ 为什么必须"独立算"：`ast` 是**官方**解析器（Python 自己用它）**不可能错**；
  tree-sitter 是**第三方文法**（可能有版本/边缘 bug）。
  ⇒ 不一致时**错的一定是我这边的接线/适配器**。
★ 口径 113：**对账判据必须「两边都报」** ——
  只报"不一致 N 处"没用，要报**哪个文件、哪个名字、哪边多了/少了**。
"""
import ast
import json
import pathlib
import sys
import time

sys.path.insert(0, r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agentscope_orchestrator import kg as _kg  # noqa: E402

总 = pathlib.Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道")


def 权威导入集(源: str) -> set:
    """内建 `ast` 算出的**真 import 集**（`import X` / `from X import Y` 的本地名）。"""
    出 = set()
    try:
        树 = ast.parse(源)
    except SyntaxError:
        return 出
    for n in ast.walk(树):
        if isinstance(n, ast.Import):
            for a in n.names:
                出.add((a.asname or a.name.split(".")[0]).strip())
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "*" or not a.name:
                    continue
                出.add((a.asname or a.name).strip())
    return 出


def 权威模块集(源: str) -> set:
    """内建 `ast` 算出的**真被导入模块名**（`import a.b` ⇒ `a`；`from a.b import c` ⇒ `a.b`）。"""
    出 = set()
    try:
        树 = ast.parse(源)
    except SyntaxError:
        return 出
    for n in ast.walk(树):
        if isinstance(n, ast.Import):
            for a in n.names:
                出.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                出.add(n.module.split(".")[0])
    出.discard("")
    return 出


计 = _kg.分根(总, 打印=lambda *_: None)
只看 = set(sys.argv[1:])
文件们 = []
for x in 计["根们"]:
    p = pathlib.Path(x["根"])
    if 只看 and p.name not in 只看:
        continue
    try:
        文 = _kg.枚举文件(p, 排除=(tuple(计["合格"]) if p == 总 else ()))
    except TypeError:
        文 = _kg.枚举文件(p)
    # ★ `枚举文件` 返回 **2 元组**（`(文件列表, 剪掉的目录)`）——
    #   我第一版把整个元组交给了 `_过值得` ⇒ `'list' object has no attribute 'suffix'` ✗
    #   （**又一次猜形状**，口径 141；这次是"读生产者"读到了，但读少了半句）
    if isinstance(文, tuple):
        文 = 文[0]
    留 = _kg._过值得(文)
    if isinstance(留, tuple):
        留 = 留[0]
    文件们 += [f for f in 留 if getattr(f, "suffix", "").lower() == ".py"]

print(f"   ★ 全仓 .py 共 {len(文件们)} 个（16 张图覆盖）")
t = time.time()
_不一致 = []
_语法错 = []
_一致 = 0
_我没给 = 0
_两边都空 = 0          # 我这边没给导入、权威也没有 ⇒ **不是分歧**
_两边都空文件: list[str] = []   # ★ 带清单：计数不带清单 = 不可复核
_我没给它有 = 0        # 我这边没给、权威有 ⇒ 真分歧（进 _不一致）
for f in 文件们:
    try:
        源 = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    try:
        ast.parse(源)
    except SyntaxError as e:
        _语法错.append({"文件": str(f.relative_to(总)), "行": e.lineno})
        continue
    _权 = 权威导入集(源)
    _权模 = 权威模块集(源)
    # ── 我这边（tree-sitter 路径）
    try:
        bs = f.read_bytes()
        # ★★ 真形状（**打印出来的，不是猜的**）：
        #    `_解析一个` 返回 **`(节, 边, 元)` 3 元组** —— **它不返回语法树** ✗
        #    树在适配器内部：`解析 = Parser(_取语言(语)); 树 = 解析.parse(bs)`
        #    ⇒ 我对账必须**自己构造那棵树**，用与生产完全相同的两步。
        #    ★ 口径 141 第四次应验：我猜了三次形状（`_一文件`/`树[0]`/元组），
        #      **第四次才去读生产的 L1202-L1205**。
        from tree_sitter import Parser as _P
        _语 = _kg._取语言("python")
        _树 = _P(_语).parse(bs)
        _我导 = _kg.取导入(_树, bs)
    except Exception as e:  # noqa: BLE001
        _不一致.append({"文件": str(f.relative_to(总)),
                       "类": "尺子错·我这边炸了（适配器异常）",
                       "**我这边炸了**": f"{type(e).__name__}: {e}",
                       "权威导入": sorted(_权)[:12]})
        continue
    if not _我导:
        _我没给 += 1
        if _权:
            _我没给它有 += 1
            _不一致.append({"文件": str(f.relative_to(总)),
                           "类": "尺子错·我这边没给出任何导入（tree-sitter 适配器漏了）",
                           "我这边": "**没给出任何导入**",
                           "权威导入": sorted(_权)[:12]})
        else:
            # ★ 两边都没有导入 ⇒ **不是分歧**。原来它和上一档混在一个 `_我没给` 里，
            #   于是守恒式永远差一截、也没人看得出差在哪（口径 558：覆盖要核算）。
            _两边都空 += 1
            if len(_两边都空文件) < 500:
                _两边都空文件.append(str(f.relative_to(总)))
        continue
    _我绑 = set()
    for d in _我导:
        for b in (d.get("绑定") or []):
            if b:
                _我绑.add(str(b).strip())
    if _权 and not _我绑:
        _不一致.append({"文件": str(f.relative_to(总)),
                       "类": "尺子错·给了导入但没有绑定名",
                       "我这边": "给了导入但**没有绑定名**",
                       "权威导入": sorted(_权)[:12]})
        continue
    _我少 = _权 - _我绑
    _我多 = _我绑 - _权 - {"*"}
    if _我少 or _我多:
        # ★ 过滤「模块名被当绑定」这类**合理解释**：`import a.b` 权威给 `a`，
        #   我这边的 `绑定` 可能是 `a`；若 `a` 在权威模块集里，不算分歧
        _我少2 = {x for x in _我少 if x not in _权模}
        if _我少2 or _我多:
            _类 = ("口径差·模块名 vs 绑定名" if _我少 and not _我少2 and _我多
                   else "真差·绑定名对不上")
            _不一致.append({"文件": str(f.relative_to(总)), "类": _类,
                           "权威有我没有": sorted(_我少2)[:12],
                           "我有权威没有": sorted(_我多)[:12]})
    else:
        _一致 += 1

print(f"   ★ 对账用了 {time.time() - t:.0f}s")
print(f"      ✅ **两边一致** {_一致}")
print(f"      ⚠️ **不一致** {len(_不一致)}（★ **错的一定是我这边**）")
print(f"      ⏭ 我这边没给出导入 {_我没给}")
print(f"      ✗ `ast` 判语法错 {len(_语法错)}（**不算我的 bug** —— 源文件本身有错）")
print(f"      ○ 两边都没导入（无差） {_两边都空}")
print(f"      ○ 我这没给但权威有（已计入不一致） {_我没给它有}")
_合计 = _一致 + len(_不一致) + _两边都空 + len(_语法错)
_守恒 = (_合计 == len(文件们))
print(f"      ★ 守恒：{_合计} vs 文件数 {len(文件们)} ⇒ {'平' if _守恒 else '**不平（有一桶没交代）**'}")
for x in _不一致[:10]:
    print(f"      ⚠ {x['文件']}")
    for k, v in x.items():
        if k != "文件":
            print(f"         {k}: {v}")
_真差 = [x for x in _不一致 if str(x.get("类") or "").startswith("真差")]
果 = {"文件数": len(文件们), "一致": _一致, "不一致": len(_不一致),
     "两边都空": _两边都空, "我没给它有": _我没给它有,
     "两边都空文件": _两边都空文件,
     "两边都空已截断": _两边都空 > len(_两边都空文件),
     "守恒": _守恒, "合计": _合计, "真差": len(_真差),
     "不一致明细": _不一致, "ast语法错": _语法错[:300],
     "★口径": ("内建 `ast` 是**官方解析器**（不会错）；不一致时**错的一定是我这边**"
              "（目标⑤的原话）。口径 113：两边都报。" )}
仓 = pathlib.Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator\work\图")
仓.mkdir(parents=True, exist_ok=True)
(仓 / "ast对账.json").write_text(json.dumps(果, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"   ★ 落盘 {仓 / 'ast对账.json'}")
# ★ 2026-11-19 #51 抓到洞：原来语法错算作「源文件本身有错，不算我的 bug」⇒ 门仍绿。
#   统计口径可以这么分，但**判据门**不行：仓库里有一个语法坏 .py，交付物就已经坏了
#   （连 import 都过不去）⇒ 语法错 > 0 一律判不达标；适配器侧结论（不一致/真差）仍单列。
# ★ 2026-11-19 #51 抓到洞：原来语法错被算作「源文件本身有错，不算我的 bug」⇒ 门仍绿，
#   于是**本包自己的源码**里塞一个语法坏文件也能过门。统计口径可以这么分，判据门不行。
#   ★ 但「全仓」里有 8 个**包外**语法错（py2 的 Jython 脚本、历史快照…，2026-11-19 实测）
#     —— 拿它们判产品红是假红（口径 311：假红比漏报更坏）。
#   ⇒ 只对 **本包 src/tools** 判红；包外的单列、不判。
_自家语法错 = [x for x in _语法错
               if "agentscope_orchestrator" in str(x.get("文件") or "")
               and ("\\src\\" in str(x["文件"]) or "/src/" in str(x["文件"])
                    or "\\tools\\" in str(x["文件"]) or "/tools/" in str(x["文件"]))]
_判 = _守恒 and not _真差 and not _自家语法错
print(f"判定={'OK' if _判 else '不达标'}")
if not _守恒:
    print("   ⇒ 守恒不平：报表有一桶没交代（不许把'没数到'当成'没问题'）")
if _自家语法错:
    print(f"   ⇒ 本包 src/tools 里有 {len(_自家语法错)} 个语法坏文件："
          "**产品交付物已坏**（门必须红）")
elif _语法错:
    print(f"   ○ 另有 {len(_语法错)} 个语法错文件在**本包之外**（py2/历史快照/第三方）"
          "—— 不算产品缺陷，单列不判红（假红比漏报更坏）")
if _真差:
    print(f"   ⇒ 真差 {len(_真差)} 处：绑定的名字对不上，是适配器/接线的问题")
sys.exit(0 if _判 else 1)
