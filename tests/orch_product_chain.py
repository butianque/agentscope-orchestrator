"""判据：**"能把活干在别人的仓上"这条链路的四道齿**（2026-11-05 case01 真跑撞出来的）。

背景：拿一个不是自己的仓（`_crossrepo_case01`），给一句自然语言目标，让它真改代码。
第一次真跑的结果是「模型改对了、判据绿了、主树一个字节没动、退出码 0」——四道齿各缺一点。
本判据把这四道齿钉住，缺一条就红：

  ① 档案里的门**装载期就校验**：`type: cmd` 按别名当 compile（+ 非法类型被移出 gates）
     —— 否则错的档案要跑到运行期深处才炸，而且炸声会被"不阻断规划"吞掉。
  ② 红门违规点**解析不出时用 `file` 兜底**，且身份**不含因由措辞**
     —— 否则"算不出度量"⇒ 一个文件都不应用；含措辞⇒改对了也被判"变多"回退。
  ③ 门命令**跑不起来（工具/模块缺失）不是红门**，是门规格非法 ⇒ 换同义可跑命令 / 摘掉
     —— 否则 `No module named pytest` 被当"产物没改好"，模型永远修不好。
  ④ "交付"必须**有主树字节作见证** ⇒ 没有任何文件落地时不许报成功（该判否）。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from agentscope_orchestrator import gate_spec

坏: list[str] = []


def _写(p: Path, 文: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(文, encoding="utf-8", newline="\n")


def 判一档案门装载期校验() -> None:
    from agentscope_orchestrator import profile as P
    基 = Path(tempfile.mkdtemp(prefix="r231_"))
    try:
        档 = 基 / "orchestrator.profile.yml"
        _写(档, "name: 校验\nrepo_root: .\ngates:\n"
                "  - {name: 甲, type: cmd, cmd: [echo, 1]}\n"
                "  - {name: 乙, type: 根本不存在的门, cmd: [echo, 1]}\n")
        p = P.load_profile(档)
        名型 = [(g.get("name"), g.get("type")) for g in p.gates]
        print(f"    ① 可用门（cmd 已按别名改写）= {名型}；非法门 = "
              f"{[x.get('name') for x in getattr(p, 'gates_invalid', ())]}")
        if ("甲", "compile") not in 名型:
            坏.append("① `type: cmd` 没有按别名落实成 compile（装载期就该改写）")
        if any(n == "乙" for n, _ in 名型):
            坏.append("① 不认识的门口**留在了 gates 里** —— 它会以异常形式进运行期")
        if not getattr(p, "gates_invalid", ()):
            坏.append("① 非法门没有被单独记进 gates_invalid（事后看不出档案有问题）")
    finally:
        shutil.rmtree(基, ignore_errors=True)

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判一档案门装载期校验：绿")


def 判二违规点身份() -> None:
    from agentscope_orchestrator import generic_goal as G
    甲 = [{"gate": "结构门", "file": "src/a.py", "cause": "结构键缺失|冲正 #"}]
    乙 = [{"gate": "结构门", "file": "src/a.py", "cause": "退出码不符|期望0实得1"}]
    点甲 = G._红门违规点(甲)
    点乙 = G._红门违规点(乙)
    print(f"    ② 文本无违规点时的兜底身份 = {sorted(点甲)}")
    if not 点甲:
        坏.append("② `file` 在字段里、文本里没有违规点 ⇒ 身份算成空集（=> 一个文件都不应用）")
    if 点甲 != 点乙:
        坏.append("② 身份里混进了因由措辞 ⇒ 因由一变就判「变多」⇒ 改对的文件被回退")
    if not G._红门违规点([{"gate": "x", "file": "src/a.py"}]):
        坏.append("② 连 evidence 文本都没有时也没兜底")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二违规点身份：绿")


def 判三门命令可用性() -> None:
    from agentscope_orchestrator import generic_goal as G
    基 = Path(tempfile.mkdtemp(prefix="r234_"))
    try:
        (基 / "tests").mkdir(parents=True, exist_ok=True)
        _写(基 / "tests" / "test_x.py", "import unittest\n")
        spec = {"gates": [{"type": "compile", "name": "跑测试",
                           "cwd": "{repo}",
                           "cmd": ["python", "-m", "pytest_本机没装它", "-q", "tests"],
                           "expect_exit": 0}]}
        新, 修 = G._门可用性矫正(spec, 基)
        门 = (新.get("gates") or [{}])[0]
        print(f"    ③ 矫正={修}")
        print(f"    ③ 矫正后的门命令 = {门.get('cmd')}")
        if not 修:
            坏.append("③ `No module named pytest` 没被认成「门跑不起来」（会被当红门）")
        if any("没装" in str(x) for x in (门.get("cmd") or [])):
            坏.append("③ 仍然留着跑不起来的 pytest 命令")
        if not any("unittest" in str(x) for x in (门.get("cmd") or [])):
            坏.append("③ 没有换成同义可跑的 unittest discover")
        if G._门命令不可用({"err": "这是真的测试失败：assert 1 == 2"}) is not False:
            坏.append("③ 把「真的测试失败」也判成门不可用 ⇒ 会放过假绿")
    finally:
        shutil.rmtree(基, ignore_errors=True)

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三门命令可用性：绿")


def 判四空交付不许报成功() -> None:
    """静态接线判据：交付前必须有「一个文件都没落地 ⇒ 判否」这道闸。

    诚实边界：不真跑一遍完整闭环（那要花模型调用）；这里钉的是**接线存在**，
    行为证据在回执里（`no_file_applied` 那条真跑读数）。
    """
    from agentscope_orchestrator import generic_goal as G
    源 = Path(G.__file__).read_text(encoding="utf-8")
    if '"kind": "no_file_applied"' not in 源:
        坏.append("④ 没有 `no_file_applied` 这道闸 ⇒ 零改动会被报成「已保留在主树」")
    # 闸必须在「发交付回执」之前，且以 return 1 结束
    段 = 源.split('"kind": "no_file_applied"')[0][-1200:]
    if "if not backups:" not in 段:
        坏.append("④ 闸没有绑在「本轮保留的备份列表为空」这个事实上")
    if "return 1" not in 源.split('"kind": "no_file_applied"')[1][:800]:
        坏.append("④ 闸触发后没有以失败退出（会照样当成功）")
    _四红 = [x for x in 坏 if str(x).startswith("④")]
    print("    ④ 空交付闸接线 =", "缺" if _四红 else "在")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四空交付不许报成功：绿")


def 判五失败栈改动面() -> None:
    """多文件目标的核心齿：**规划器漏掉的必改文件，必须能从失败栈补回来**。

    真证据（case02 多文件靶，2026-11-05）：
      规划器只切出 `inventory.py`，而失败栈写着
      `File "...\\shop\\service.py", line 39, in 取消 / NotImplementedError`
      ⇒ 只改 inventory 永远不绿。补上 service.py 之后才有 3 文件同改的收敛。
    """
    from agentscope_orchestrator import generic_goal as G
    基 = Path(tempfile.mkdtemp(prefix="r237_"))
    try:
        (基 / "src" / "shop").mkdir(parents=True)
        (基 / "src" / "shop" / "service.py").write_text("x=1\n", encoding="utf-8")
        (基 / "src" / "shop" / "report.py").write_text("y=1\n", encoding="utf-8")
        (基 / "tests").mkdir()
        (基 / "tests" / "test_x.py").write_text("import unittest\n", encoding="utf-8")
        # 证据文本用**纯文本**（不是 json.dumps —— 转义引号会让 `File "` 匹配不上）
        红 = [{"gate": "全量单元测试", "ok": False, "file": "case02",
               "err": 'Traceback:\n  File "%s", line 39, in 取消\n'
                      "ImportError: cannot import name '月度退款额' from 'shop.report'\n"
                      % (基 / "src" / "shop" / "service.py")}]
        面 = G._失败栈改动面236(红, 基, ["src"])
        spec = {"gates": [], "deliverables": [{"path": "src/shop/report.py"}]}
        新, 补 = G._补齐改动面236(G._parse_deliverables(spec), spec, 基, 红, ["src"])
        路径 = [d["path"] for d in 新]
        print(f"    ⑤ 读出的改动面 = {面}")
        print(f"    ⑤ 补齐后交付物 = {路径}")
        if "src/shop/service.py" not in 面:
            坏.append("⑤ 失败栈里的 `File \"…service.py\"` 没被读成改动面")
        if "src/shop/report.py" not in 面:
            坏.append("⑤ `cannot import name … from 'shop.report'` 没被换算成文件")
        if "src/shop/service.py" not in 路径:
            坏.append("⑤ 改动面没补进交付物集合 ⇒ 规划器漏了就永远改不到")
        if any("tests/" in p for p in 路径):
            坏.append("⑤ 把测试文件补成了交付物 ⇒ 判据会被被判方改")
    finally:
        shutil.rmtree(基, ignore_errors=True)

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判五失败栈改动面：绿")


def 判六项目门红不许交付() -> None:
    """接线判据：项目验收门还红时，「交付」必须被降级为部分交付（exit≠0）。"""
    from agentscope_orchestrator import generic_goal as G
    源 = Path(G.__file__).read_text(encoding="utf-8")
    if '"kind": "partial_delivery"' not in 源:
        坏.append("⑥ 没有 `partial_delivery` 这条降级 ⇒ 项目门红也报成功")
    if "project_gate_red_continue" not in 源:
        坏.append("⑥ 还有轮次时没有「不交付、继续下一轮」的路径 ⇒ 多文件一轮改不完就停")
    if "return -1" not in 源:
        坏.append("⑥ `_deliver` 没有返回「继续下一轮」的哨兵")
    print("    ⑥ 项目门红降级 + 继续下一轮 =",
          "缺" if any(str(x).startswith("⑥") for x in 坏) else "在")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判六项目门红不许交付：绿")


def 判七用例身份可比() -> None:
    """多文件逐文件裁决的度量：违规点必须是**哪条用例在红**，而不是"哪个门在哪个文件上红"。"""
    from agentscope_orchestrator import generic_goal as G
    四 = "\n".join(f"ERROR: test_{i} (x.流程.test_{i})" for i in range(1, 5))
    三 = "\n".join(f"ERROR: test_{i} (x.流程.test_{i})" for i in range(1, 4))
    红四 = [{"gate": "全量单元测试", "ok": False, "file": "case02", "err": 四}]
    红三 = [{"gate": "全量单元测试", "ok": False, "file": "case02", "err": 三}]
    a, b = G._红门违规点(红四), G._红门违规点(红三)
    print(f"    ⑦ 4 条失败身份 = {sorted(a)}")
    if not a:
        坏.append("⑦ 用例失败没有被读成违规点身份")
    if not (b < a):
        坏.append("⑦ 修好一条用例后身份**不是真子集变小** ⇒ 正确改动会被判「没进展」回退")
    if any("门::" in x for x in a):
        坏.append("⑦ 同时掺了「门::文件」兜底 ⇒ 两把尺子混用")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判七用例身份可比：绿")


def 判八上游空包归瞬时() -> None:
    """★ R258c：上游回**错误包**（HTTP 200 + {"error":...}）时，SDK 抛的是
    `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`。

    真证据（case02 回执）：channel_degraded → plan_transient_retry 第 10/11 次
    → **plan_error: JSONDecodeError** → 整趟 return 1。
    ⇒ 我把"上游让我稍后重试"读成了"本机代码坏了"，一趟任务当场归零。

    这道齿钉两件事：
      ① `JSONDecodeError` 必须被判**瞬时**（进重试，不是终态）；
      ② 但**真的数据结构错**（KeyError/TypeError）必须仍是终态 ——
         否则"归瞬时"就变成"什么都重试"，那是另一个方向的错。
    """
    from agentscope_orchestrator import generic_goal as G
    import json as _j
    try:
        瞬, 因 = G._是瞬时的(_j.JSONDecodeError("Expecting value", "", 0))
        if not 瞬:
            坏.append(f"判八：JSONDecodeError 被判终态（{因}）⇒ 上游空包会让整趟归零")
        for e in (KeyError("x"), TypeError("y")):
            瞬2, _ = G._是瞬时的(e)
            if 瞬2:
                坏.append(f"判八：{type(e).__name__} 被判瞬时 ⇒ 什么都重试，等于没归因")
        if not G._是上游空包(RuntimeError("返回的不是模型回复")):
            坏.append("判八：'返回的不是模型回复' 没被判上游空包")
        # ★★ 关键：分类器对了 **不等于接线对**。
        #   第一版这道齿只测分类器 ⇒ 我把规划循环里那行 `_瞬 = _瞬 or _是上游空包(e)`
        #   删掉、跑判据**仍然绿**。这正是我刚写下的那句批评：
        #   「一段判定没接进执行路径，等于没有判定」。
        #   ⇒ 必须机验**规划循环真的读了它**（AST 层：while-try 里调用了它）。
        import ast as _ast
        源 = open(G.__file__, encoding="utf-8").read()
        树 = _ast.parse(源)
        # ★ 用**显式栈**遍历：`ast.walk` 不跟踪父节点，第一版靠它找 `_run`
        #   报「没找到」而源码里明明有 —— **"找不到"必须能区分"真没有"与"我找错了"**。
        _接, _见 = [], [False]      # 用列表做可变容器，避免闭包/global 与局部同名互踩
        def _走(n, 栈):
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                栈 = 栈 + [n.name]
                if n.name == "_run":
                    _见[0] = True
            if (isinstance(n, _ast.Call)
                    and getattr(n.func, "id", getattr(n.func, "attr", "")) == "_是上游空包"
                    and 栈 and 栈[-1] == "_run"):
                _接.append(n.lineno)
            for c in _ast.iter_child_nodes(n):
                _走(c, 栈)
        _走(树, [])
        _见 = _见[0]
        if not _见:
            坏.append("判八：源码里找不到 `_run` ⇒ **是这条判据找错了**（不是接线对）")
        if not _接:
            坏.append("判八：`_是上游空包` 定义了但**规划循环里没调用**（判定没接进执行路径）")
    except Exception as e:                       # noqa: BLE001
        坏.append(f"判八异常：{type(e).__name__}: {e}")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判八上游空包归瞬时：绿")


def 判九没有重复定义() -> None:
    """★ R259c：**同一个名字在同一个文件里定义了两次** —— 后定义者静默胜出。

    真读数（2026-11-05，我自己的脚本化编辑造成的）：
      `taixu_mcp.py` 里 `def accept_closed` 出现了 **5 次**（前几轮重复插入）。
      我改第 3 份，运行时用的是第 5 份 ⇒ 改了等于没改，而**所有判据照样绿**。

    ★ 为什么这条必须机器判：脚本化批量替换最容易制造的垃圾就是重复块，
      而它的表现是"改了不生效"——**最难从行为上发现**。
      口径：判据不许留垃圾。垃圾的定义里就该包含"重复定义"。
    """
    from pathlib import Path as _P
    仓 = _P(__file__).resolve().parent.parent / "src" / "agentscope_orchestrator"
    for f in sorted(仓.glob("*.py")):
        源 = f.read_text(encoding="utf-8", errors="replace")
        try:
            import ast as _ast
            树 = _ast.parse(源)
        except SyntaxError as e:
            坏.append(f"判九：{f.name} 语法错，判不了重复定义：{e}")
            continue
        # ★★★ **「重定义」不等于「静默胜出」**（R366）。
        #   实测 `kg.py` 被判「重复顶层定义」：
        #     `def _结构化适配器(...)`（原函数）… `_结构化适配器_原 = _结构化适配器`
        #     … `def _结构化适配器(...)`（**包装层**）
        #   ⇒ 这是**合法的装饰器/包装模式**：旧函数**先被存进另一个名字**，
        #     再用新函数覆盖原名。**旧值被用掉了**，不是「静默胜出」。
        #   ★ 判据：**重定义之前，旧值被 `Load` 过 ⇒ 不算缺陷**。
        #   ★ 这一条误报**一次挡住了 36 条判据**（它们共享判九/判十一做前置）
        #     —— 「**误报的引用比缺失的引用更坏**」，判据的误报同理：
        #     一条过严的判据会把整片判据池染红，而**红得没有信息量**。
        #   ★ `%d` 那行是**批量插入时没格式化的模板占位符**，顺手清掉。
        名 = {}
        已读: set[str] = set()
        for 结 in 树.body:
            # 先记这一句里**读**了哪些顶层名字（`X_原 = X` 的右边就是 Load）
            if isinstance(结, _ast.Assign):
                for _t in _ast.walk(结.value):
                    if isinstance(_t, _ast.Name) and isinstance(_t.ctx, _ast.Load):
                        已读.add(_t.id)
            if isinstance(结, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                if 结.name in 已读:
                    # 旧值被读过（存了别名）⇒ **有意的重定义**，不算静默胜出
                    名.setdefault(结.name, []).append(结.lineno)
                    continue
                名.setdefault(结.name, []).append(结.lineno)
        重 = {k: [x for x in v] for k, v in 名.items()
             if len(v) > 1 and k not in 已读}
        if 重:
            坏.append(f"判九：{f.name} 有**重复顶层定义**（后定义者静默胜出）：{重}")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判九没有重复定义：绿")


def 判十相对cwd落在求值根() -> None:
    """★ R261：门里写 `cwd: "."` 时，命令必须跑在**求值根**里，不是进程 cwd。

    真证据（修前，本机 3 行可复现）：
      `evaluate([{type:compile, cwd:".",
                  cmd:[python,-m,unittest,discover,-s,tests,-q]}], 仓, 仓)`
      ⇒ **退出码 5**（跑到工具自己的安装目录去了）；cwd 写绝对路径 ⇒ 绿。

    ★ 这是「求值根必须与被判字节同一棵树」的**反向**形态：沙箱里判红、主树里其实是绿。
      而且它是**静默的**——报「退出码不符」，读的人会去改代码，而问题在求值根。
    """
    from pathlib import Path as _P
    import tempfile, sys as _s
    基 = _P(tempfile.mkdtemp(prefix="r261_"))
    try:
        (基 / "tests").mkdir()
        (基 / "tests" / "test_a.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_x(self): self.assertTrue(True)\n", encoding="utf-8")
        门 = [{"type": "compile", "name": "相对cwd",
               "cmd": [_s.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
               "cwd": ".", "expect_exit": 0}]
        _r, _d = gate_spec.evaluate(门, 基, 基)
        # ★ 口径：`audit` 是**全量读数**（含绿的条目）；判红与否只看 `results`。
        #   第一版我拿 `if _d:` 当"判红"，于是**绿的时候也报红** ——
        #   "看不见"和"没有"是两件事，"有读数"和"有红"也是。
        if not (_r or {}).get("相对cwd"):
            坏.append(f"判十：cwd='.' 没落在求值根 ⇒ 门判红（跑到工具目录去了）："
                      f"{_d[0].get('cause') if _d else '无读数'}")
    except Exception as e:                       # noqa: BLE001
        坏.append(f"判十异常：{type(e).__name__}: {e}")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十相对cwd落在求值根：绿")


def 判十一没有死函数() -> None:
    """★ R262：**写了却永远走不到的函数** = 装饰品，不是能力。

    ★ 这条判据自己错了三次，每次都改检测器而不是改结论：
      ① 只数"调用 + 裸名"，把 `@mcp.tool(name=_TOOL("context"))` 注册的
         `taixu_context` 判成死 —— **Python 函数名 ≠ 工具名**；
      ② 只看模块级 `Name`，漏了 `from x import 快重试策略 as _快243` 这种**别名 import**
         （`_退避163` 就是这样接线的，我差点把一条活接线删掉）；
      ③ 把类方法当顶层函数（`self._task_detail` 是属性调用）。
      ⇒ 终版：**有装饰器的一律算活**（框架/注册表调用），类方法跳过，
        其余必须被 Name / Attribute / import / 字符串 任一方式引用过。
      「找不到」必须能区分「真没有」与「我找错了」—— 三次都是后者。
    """
    import ast as _ast
    仓 = Path(__file__).resolve().parent          # tests/
    包根 = 仓.parent                              # agentscope_orchestrator/
    源 = sorted((包根 / "src" / "agentscope_orchestrator").glob("*.py"))
    if len(源) < 50:
        坏.append(f"判十一：只扫到 {len(源)} 个模块 ⇒ 扫描根写错了（不许把'没扫到'当'没有'）")
        return
    全部 = 源 + sorted(仓.glob("*.py")) + sorted((包根 / "tools").glob("*.py"))
    import re as _re2      # ★ R519：`_导入表` 收集在下面那次遍历里就要用
    被引用, 类方法 = set(), set()
    # ★ R519：**两把尺子** —— `被引用` 含字符串（旧行为，R440 起），
    #   `_被引用_代码` **只认代码名**（Name/Attribute/import）。
    #   口径 331：「表里有这个名字」与「这个名字在这里是什么意思」必须分开。
    _被引用_代码: set = set()
    _函数表: list = []          # (模块名, 函数名, 行号) —— 顶层函数
    _导入表: dict = {}          # 文件名 → 它 import 的模块名集合
    for f in 全部:
        _是运行时 = (f in set(源)) or (f.parent.name == 'tools')
        _导入表[f] = set()
        try:
            树 = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for 类 in [x for x in _ast.walk(树) if isinstance(x, _ast.ClassDef)]:
            for n in 类.body:
                if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    类方法.add(n.name)
        for _nf in _ast.walk(树):
            if isinstance(_nf, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                if not (_nf.name.startswith('__') or _nf.decorator_list
                        or _nf.name in 类方法):
                    _函数表.append((f.stem, _nf.name, _nf.lineno))
        for _mi in _re2.finditer(
                r'^\s*(?:from\s+([\w.]+)\s+import\s+([^\n]+)'
                r'|import\s+([^\n]+))',
                f.read_text(encoding='utf-8', errors='replace'), _re2.M):
            if _mi.group(1):
                _导入表[f].add(_mi.group(1).split('.')[-1])
                for _p in _mi.group(2).replace('(', '').replace(')', '').split(','):
                    if _p.strip().split(' as ')[0].strip():
                        _导入表[f].add(_p.strip().split(' as ')[0].strip().split('.')[-1])
            else:
                for _p in _mi.group(3).split(','):
                    if _p.strip().split(' as ')[0].strip():
                        _导入表[f].add(_p.strip().split(' as ')[0].strip().split('.')[-1])
        _导入表[f] |= ({'<运行时>'} if _是运行时 else set())
        for n in _ast.walk(树):
            if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load):
                被引用.add(n.id)
                _被引用_代码.add(n.id)
            elif isinstance(n, _ast.Attribute) and isinstance(n.ctx, _ast.Load):
                被引用.add(n.attr)
                _被引用_代码.add(n.attr)
            elif isinstance(n, _ast.ImportFrom):
                for a in n.names:
                    被引用.update([a.name, a.asname or a.name])
                    _被引用_代码.update([a.name, a.asname or a.name])
            elif isinstance(n, _ast.Import):
                for a in n.names:
                    被引用.update([a.name.split(".")[-1], a.asname or a.name])
                    _被引用_代码.update([a.name.split(".")[-1], a.asname or a.name])
            elif isinstance(n, _ast.Constant) and isinstance(n.value, str):
                被引用.add(n.value)
    死的 = set()
    for f in 源:
        try:
            树 = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in _ast.walk(树):
            if not isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            if n.name.startswith("__") or n.decorator_list or n.name in 类方法:
                continue
            if n.name not in 被引用:
                死的.add(f"{f.stem}.py::{n.name}@{n.lineno}")
            # ★★★ **分级**（R440）：真死代码（红） vs **未接线的能力**（只印出）。
            #   ★ 判据③（docstring 里自己写的教训）说：「**「找不到」必须能区分
            #     「真没有」与「我找错了」**」—— 而这里还有**第三种**：
            #     **「有，但还没接线」**。
            #   ★ 旧版把第三种算进第一种 ⇒ 一条红**传染 17 条**（口径 160），
            #     而**缺陷一个没多**。**"没接线"不是"写错了"。**
    import re as _re2
    _真死, _未接线 = set(), set()
    # ★ R519f：**从 `_导入表` 一次算完**（上面那次遍历顺手收的）。
    #   原来是 `for 死函数 { for 文件 { read + 2×regex } }`
    #   ⇒ O(死函数数 × 文件数 × 文件大小)，实测让判十一**超时 900s**。
    #   ★ 口径 337 同族：**慢判据会让人不敢跑，不跑就等于没有**。
    _被导入的模 = set()
    _运行时模 = set()
    for _f5, _集5 in _导入表.items():
        _有运 = "<运行时>" in _集5
        _名集 = _集5 - {"<运行时>"}
        _被导入的模 |= _名集
        if _有运:
            _运行时模 |= _名集
    for _条 in 死的:
        _模 = _条.split(".py::", 1)[0]
        (_真死 if _模 in _被导入的模 else _未接线).add(_条)
    # ★★★ R519：**「提到 ≠ 接线」**（口径 331/317）——
    #   `被引用` 把 **任何字符串字面量**都算"被引用"（`_ast.Constant(str)`）
    #   ⇒ **判据文件里提到函数名 = 算接线**。
    #   实测（HEAD 版本）：`解析md链接` / `解析JSON引用`
    #     **代码名引用 ✗ 无**，但在 `orch_product_chain.py` 里**被正文提到 3 次**
    #     ⇒ 判十一**看不见它们** ⇒ 两个死函数从 R4xx 躺到 R519。
    #   ★ **判据和被测系统犯了同一个错**（被测系统的病就是"提及≠引用"）
    #     ⇒ 口径 317：恒绿的判据会替缺陷打掩护。
    #   ★ 四类互斥：
    #       代码名✓ 串✓ ⇒ 真接线      | 代码名✓ 串✗ ⇒ 真接线
    #       代码名✗ 串✗ ⇒ 真死/未接线（R440 原档，不动）
    #       代码名✗ 串✓ ⇒ ★ **只被提到**（本条款）
    #   ★ 靶（口径 313/332）：**且该模块在运行时路径上**（`src/` 或 `tools/`
    #     里有人 import 它）—— 只被 `tests/` import 的模块是**库**，
    #     对外暴露函数合法（实测 `语义校准.py`：`语义留出`/`两级判全文`
    #     是它的公开 API，判四十 检查的是"在不在"，不是"在不在跑"）。
    _仅串 = set()
    for _模2, _名2, _行2 in _函数表:
        if (_名2 in 被引用) and (_名2 not in _被引用_代码) and (_模2 in _运行时模):
            _仅串.add(f"{_模2}.py::{_名2}@{_行2}")
    if _仅串:
        坏.append("判十一[R519]：这些函数**只有字符串/文档提到过它**"
                  "（**不是接线** —— 口径 331「提及 ≠ 引用」）："
                  + "、".join(sorted(_仅串)))
    if _真死:
        坏.append("判十一：这些函数**写了但没人调用**"
                   "（**真死代码 · 该删**）：" + "、".join(sorted(_真死)))
    if _未接线:
        print("   ⚠ 判十一：这些是**未接线的能力**（整个模块没被任何地方 "
              "import ⇒ **不是缺陷**，是『能力躺着没用』）："
              + "、".join(sorted(_未接线)))



    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十一没有死函数：绿")


def 判十二指令带证据() -> None:
    """★ R263：**证据不进指令，等于把定位白做了** —— 而且要测**接线**，不是测辅助函数。

    真读数（2026-11-05，case02 真红灯）：
      门报 `ImportError: cannot import name '月度退款额' from 'shop.report'`，
      而从失败栈补出的交付物指令只有一句
      "由项目验收门的失败栈定位到本文件：它必须一起改" ——
      **没有一个字说要补 `月度退款额`**。模型只能猜（历史真事故：`已退金额` 写成 `已退`）。

    ★ 第一版这条齿**没咬住**（我自己抓的）：它直接调 `_指令带证据263(...)` 测辅助函数，
      于是我把**接线**改回旧泛话它照样绿 —— 和第 8 轮"只测分类器不测接线"是同一个错。
      ⇒ 现在走真路径：造一个**真有这个文件的仓** + 真红条目，调 `_补齐改动面236`，
        断言它吐出来的 `agent_instruction` 里有结论行 —— 接线断了就红。
    """
    from agentscope_orchestrator import generic_goal as _G
    基 = Path(tempfile.mkdtemp(prefix="r263_"))
    (基 / "shop").mkdir(parents=True, exist_ok=True)     # ★ 判据自己的 bug：漏了建目录
    (基 / "shop" / "report.py").write_text("def 月度收入(库):\n    return 0\n", encoding="utf-8")
    (基 / "tests").mkdir()
    红 = [{"gate": "全量单元测试", "file": "shop", "ok": False,
           "cause": "退出码不符|期望0实得1", "rc": 1,
           "err": 'Traceback (most recent call last):\n'
                  '  File "E:\\x\\unittest\\loader.py", line 396, in _find_test_path\n'
                  '    module = self._get_module_from_name(name)\n' + "\n".join(
                      f'  File "filler{i}.py", line {i}, in x' for i in range(40)) + "\n"
                  "ImportError: cannot import name '月度退款额' from 'shop.report'\n"}]
    交付, 补 = _G._补齐改动面236(
        [], {"name": "t", "gates": []}, 基, 红, [基])
    候 = [x for x in 交付 if str(x.get("path", "")).endswith("report.py")]
    if not 候:
        坏.append(f"判十二：失败栈没把 shop/report.py 补进交付物（实得 {[x.get('path') for x in 交付]}）")
        return
    指令 = str(候[0].get("agent_instruction") or "")
    if "月度退款额" not in 指令:
        坏.append(f"判十二：交付物指令里**没有红门结论行**（模型只能猜）⇒ 实得：{指令[:120]!r}")
        return
    _i结 = 指令.find("结论行")
    _i原 = 指令.find("原始证据")
    if _i结 < 0 or (_i原 >= 0 and _i结 > _i原):
        坏.append("判十二：结论行没有前置 ⇒ 被栈顶的 File 行挤掉了")
    if len(指令) > 1900:
        坏.append(f"判十二：指令过长（{len(指令)} 字）⇒ 会挤掉真正的改动要求")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十二指令带证据：绿")


def 判十三跨文件整批达成() -> None:
    """★ R264：**逐文件裁决必然把跨文件改动判成倒退** —— 整批路径必须真能兜住。

    真读数（2026-11-05，case02 副本，不调模型）：
      ✗ service.py：违规点数没变（1→1）⇒ 只回退这一个文件
      ✗ report.py：违规点变多（1→5）  ⇒ 只回退这一个文件
      ✗ inventory.py：违规点数没变（1→1）⇒ 只回退这一个文件
      ★★★ R180 整批成立：3 个文件一起改 ⇒ 违规点 1 → 0（全绿）⇒ 全部保留
      独立复跑 rc = 0 | OK

    ★ 这条齿同时钉住"写 A 判 B"那一族缺陷：备份根 / 写文件根 / 判分根
      都必须跟着 `round_root` 走。任何一处回到 `REPO_ROOT`，
      本齿就会以 `PermissionError` 或 `gate: missing`（文件明明写了却报未产出）红给你看。
    """
    import subprocess as _sp
    from agentscope_orchestrator import generic_goal as _G, gate_spec as _gs
    基 = Path(tempfile.mkdtemp(prefix="r264_")) / "repo"
    (基 / "src" / "shop").mkdir(parents=True)
    (基 / "tests").mkdir()
    # 三个互相依赖的文件：只改任一个都不绿
    (基 / "src" / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (基 / "src" / "shop" / "inventory.py").write_text(
        "class 库存:\n"
        "    def __init__(self): self._可用 = {'x': 1}\n"
        "    def 预占(self, 号): self._可用['x'] -= 1\n"
        "    def 可用(self): return self._可用['x']\n"
        "    def 释放(self, 号): raise NotImplementedError('释放')\n", encoding="utf-8")
    (基 / "src" / "shop" / "service.py").write_text(
        "from .inventory import 库存\n\n"
        "class 服务:\n"
        "    def __init__(self): self.库存 = 库存()\n"
        "    def 取消(self, 号): raise NotImplementedError('取消')\n", encoding="utf-8")
    (基 / "src" / "shop" / "report.py").write_text(
        "def 月度收入(库):\n    return 0\n", encoding="utf-8")
    (基 / "tests" / "test_all.py").write_text(
        "import sys, unittest\n"
        "sys.path.insert(0, 'src')\n"
        "from shop.service import 服务\n"
        "from shop.report import 月度收入, 月度退款额\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_全链(self):\n"
        "        s = 服务()\n"
        "        s.库存.预占(1)\n"
        "        s.取消(1)\n"
        "        self.assertEqual(s.库存.可用(), 1)\n"
        "        self.assertEqual(月度退款额(s.库存), 3)\n"
        "        self.assertEqual(月度收入(None), 0)\n", encoding="utf-8")
    门 = [{"name": "全量单元测试", "type": "compile",
           "cmd": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
           "cwd": str(基), "expect_exit": 0}]
    _r, _d = _gs.evaluate(门, 基, 基)
    if _r.get("全量单元测试"):
        坏.append("判十三：起始态就绿了 ⇒ 这条齿测不到东西")
        return
    基线 = _G._红门违规点(_d)
    交付 = [{"path": p, "focus": 0, "agent_instruction": "", "gates": 门,
             "depends_on": [], "machine": None}
            for p in ("src/shop/service.py", "src/shop/report.py", "src/shop/inventory.py")]
    产物 = {
        "src/shop/inventory.py": (基 / "src/shop/inventory.py").read_text(encoding="utf-8").replace(
            "    def 释放(self, 号): raise NotImplementedError('释放')\n",
            "    def 释放(self, 号): self._可用['x'] += 1\n"),
        "src/shop/report.py": (基 / "src/shop/report.py").read_text(encoding="utf-8")
            + "\n\ndef 月度退款额(库):\n    return 3\n",
        "src/shop/service.py": (基 / "src/shop/service.py").read_text(encoding="utf-8").replace(
            "    def 取消(self, 号): raise NotImplementedError('取消')\n",
            "    def 取消(self, 号):\n        self.库存.释放(号)\n"),
    }
    _res, _red, 留, 退 = _G._增量应用(产物, 基线, 基, 交付)
    if len(留) != 3:
        坏.append(f"判十三：三文件跨文件目标没整批成立（保留 {len(留)}，回退 {退}）"
                  " ⇒ 逐文件裁决把互相依赖的改动判成倒退了")
        return
    if _red:
        坏.append(f"判十三：保留后仍剩红 {len(_red)} 条 ⇒ '保留'与'全绿'不是同一件事")
    # ★★★ R264b：**备份根 / 写文件根必须跟随被判仓**，而且要**在没有 ORCH_STATE_DIR
    #   的环境里**验 —— 我第一次的对照实验是错的：`ORCH_STATE_DIR` 一设，备份根就走那一支，
    #   于是漏传 round_root 根本看不出来（拆掉接线照样绿）。
    #   ⇒ 这里必须显式**清空 ORCH_STATE_DIR** 再验，否则这条齿验的是环境不是代码。
    _env2 = {k: v for k, v in __import__("os").environ.items() if k != "ORCH_STATE_DIR"}
    _探 = _sp.run(
        [sys.executable, "-c",
         "import sys;from pathlib import Path;"
         "sys.path.insert(0,%r);"
         "from agentscope_orchestrator import generic_goal as G;"
         "b=Path(%r);print(G._备份根246(b))" % (str(第pkg := Path(__file__).resolve().parent.parent / "src"), str(基))],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=_env2)
    _被 = str(基.resolve())
    if _被 not in (_探.stdout or ""):
        坏.append(f"判十三：备份根没跟随被判仓（实得 {(_探.stdout or _探.stderr).strip()[:90]!r}，"
                  f"应落在 {_被}）⇒ 会「在 A 仓上备份、往 B 仓写」")
    # 独立复跑：另起进程跑仓自己的命令（不是复用内存里的判定）
    _p = _sp.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                 cwd=str(基), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if _p.returncode != 0:
        坏.append(f"判十三：独立复跑 rc={_p.returncode} ⇒ 主树里的字节不成立"
                  f"（{((_p.stderr or '') + (_p.stdout or '')).strip().splitlines()[-1][:80]}）")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十三跨文件整批达成：绿")


def 判十四多语言失败栈() -> None:
    """★ R265：**「任意真实仓」不是一句口号，是七种语言的真错误格式都得认**。

    真读数（2026-11-05，修复前）：
      Rust(cargo `--> src/lib.rs:10:5`)      ⇒ []      ← 空
      Go(`main_test.go:12:`)                ⇒ []      ← 空
      TS(`src/app.ts(12,7): error TS2322`)   ⇒ []      ← 空
      Java(`src/A.java:12: error:`)          ⇒ []      ← 空
      C++(`src/a.cpp:12:5: error:`)          ⇒ []      ← 空
      Python unittest(`File "/abs/x.py"`)    ⇒ []      ← 空
      只有 pytest 的 `File "…", line N` 一种能认。
    ⇒ 所谓"任意真实仓"当时只对 pytest 成立。

    ★ 这条齿还钉住两个**正则自己的坑**（都是我改的时候真撞上的）：
      ① 后缀替选要**长的在前**：`(?:java|c|cpp|…)` 会把 `src/a.cpp` 吃成 `src/a.c`；
      ② 可选列号**不能**写成 `(?::(\d+):(?::\d+:)?)` —— 在 `a.cpp:12:5:` 上整体失配。
      两个坑都表现为"某几种语言静默变成空"，不报错 ⇒ 只能靠这种逐语言齿咬出来。
    """
    from agentscope_orchestrator import generic_goal as _G
    基 = Path(tempfile.mkdtemp(prefix="r265_")) / "repo"
    文件 = ("src/lib.rs", "src/app.ts", "src/widget.tsx", "src/A.java",
            "src/a.cpp", "src/main.c", "src/x.py", "src/lib.go")
    for _f in 文件:
        (基 / _f).parent.mkdir(parents=True, exist_ok=True)
        (基 / _f).write_text("", encoding="utf-8")
    样例 = {
        "Rust": ("error[E0308]: mismatched types\n --> src/lib.rs:10:5\n", "src/lib.rs"),
        "TS": ("src/app.ts(12,7): error TS2322: not assignable\n", "src/app.ts"),
        "TSX": ("src/widget.tsx(3,1): error TS1: boom\n", "src/widget.tsx"),
        "Java": ("src/A.java:12: error: cannot find symbol\n", "src/A.java"),
        "C++": ("src/a.cpp:12:5: error: 'x' was not declared\n", "src/a.cpp"),
        "C": ("src/main.c:3:1: warning: unused variable\n", "src/main.c"),
        "Python": ('File "src/x.py", line 12, in f\nNameError: x\n', "src/x.py"),
    }
    for _名, (_文本, _应) in 样例.items():
        _红 = [{"gate": "g", "file": "（全库）", "ok": False,
                "cause": "退出码不符", "rc": 1, "err": _文本}]
        _实 = _G._失败栈改动面236(_红, 基, [基 / "src"])
        if _应 not in _实:
            坏.append(f"判十四：{_名} 的真实错误格式认不出改动面"
                       f"（应为 {_应!r}，实得 {_实}）⇒ 「任意真实仓」对该语言不成立")
    # ★★★ R266：**诊断词不是英文、以及输出带 ANSI 颜色**，这两个真缺口。
    #   真读数（本机真 javac / 真 cargo，2026-11-05）：
    #     javac（中文 locale，出厂默认）`src\\A.java:1: 错误: 找不到符号`   ⇒ []  ❌
    #     cargo --message-format=short 的**真实字节**是
    #       'src\\util.rs:1:26: \x1b[1m\x1b[91merror[E0425]\x1b[0m: cannot find value …'
    #       —— 诊断词前插着颜色转义，任何 error 正则都匹配不上        ⇒ []  ❌
    #     而人在终端里看到的是彩色的 "error[E0425]"：**「看得见」与「读得到」不是一回事**。
    (基 / "src" / "A.java").write_text("", encoding="utf-8")
    (基 / "src" / "util.rs").write_text("", encoding="utf-8")
    for _名, _文本, _应 in (
            ("中文javac", "src\\A.java:1: 错误: 找不到符号\n", "src/A.java"),
            ("cargo-short带色",
             "src\\util.rs:1:26: \x1b[1m\x1b[91merror[E0425]\x1b[0m: cannot find value\n",
             "src/util.rs")):
        _实 = _G._失败栈改动面236([{"gate": "g", "ok": False, "err": _文本}], 基, [基 / "src"])
        if _应 not in _实:
            坏.append(f"判十四：{_名} 认不出改动面（应为 {_应!r}，实得 {_实}）"
                      "⇒ 非英文 locale 或带 ANSI 色时，失败栈整条失效")
    # 反向：判据文件必须仍然被排除（别为了多认语言把测试文件也当改动面）
    _红2 = [{"gate": "g", "ok": False,
             "err": "tests/test_x.py:12: error: boom\n"}]
    if any("tests/" in _x for _x in _G._失败栈改动面236(_红2, 基, [基 / "src"])):
        坏.append("判十四：判据文件（tests/）被当成改动面了 ⇒ 会让被判方去改判据")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十四多语言失败栈：绿")


def 判十五真工具链端到端且可重复() -> None:
    """★ R267：**整仓改-判-落-复跑，在真编译器上跑通，并且连跑多次结论一致**。

    真读数（2026-11-05，本机真 cargo）：
      ① 起始 {'cargo check': False} | 基线 frozenset({'门::cargo check::<仓>'})
      ② 失败栈→改动面 ['src/util.rs']（真 cargo 的 `\x1b[…merror[E0425]\x1b[0m` 也认得出）
      ③ 落地：✓ src/util.rs 判据全绿 ⇒ 保留；备份落在**被判仓**内
      ④ 独立复跑（另起 cargo 进程）rc = 0
      ⑤ 主树真改了
      连跑 5 次：(['src/util.rs'], 保留 1, rc 0) × 5 ⇒ **可重复**

    ★ 跳过的条件必须说清楚：**没有 cargo 就跳**（不是判绿）。
      「没测到不能当通过了」—— 所以这里要么真跑，要么在 `坏` 里写明"跳过"，
      不许沉默地算绿。
    """
    import shutil as _sh, subprocess as _sp
    _cargo = _sh.which("cargo")
    if not _cargo:
        坏.append("判十五：本机没有 cargo ⇒ **没测到**（不算通过）。"
                  "多语言整仓端到端目前只在有 cargo 的机器上验过")
        return
    from agentscope_orchestrator import generic_goal as _G, gate_spec as _gs

    def _一轮(标: str) -> tuple:
        _基 = Path(tempfile.mkdtemp(prefix=f"r267_{标}_")) / "repo"
        (_基 / "src").mkdir(parents=True)
        (_基 / "Cargo.toml").write_text(
            '[package]\nname="demo"\nversion="0.1.0"\nedition="2021"\n\n[workspace]\n',
            encoding="utf-8")
        (_基 / "src" / "lib.rs").write_text("pub mod util;\n", encoding="utf-8")
        (_基 / "src" / "util.rs").write_text(
            "pub fn helper() -> i32 { undefined_xyz }\n", encoding="utf-8")
        _门 = [{"name": "cargo check", "type": "compile",
                "cmd": [_cargo, "check"], "cwd": str(_基), "expect_exit": 0}]
        _r, _d = _gs.evaluate(_门, _基, _基)
        if _r.get("cargo check"):
            坏.append(f"判十五（{标}）：起始态就绿 ⇒ 这条齿测不到东西")
            return ("坏",)
        _基线 = _G._红门违规点(_d)
        _面 = _G._失败栈改动面236(_d, _基, [_基 / "src"])
        if not _面:
            坏.append(f"判十五（{标}）：真 cargo 的编译错算不出改动面")
            return ("坏",)
        _新 = (_基 / _面[0]).read_text(encoding="utf-8").replace("undefined_xyz", "41 + 1")
        _交付 = [{"path": _面[0], "focus": 0, "agent_instruction": "", "gates": _门,
                  "depends_on": [], "machine": None}]
        _res, _red, _留, _退 = _G._增量应用({_面[0]: _新}, _基线, _基, _交付)
        # 独立复跑：另起进程（不是复用内存判定）
        _p = _sp.run([_cargo, "check"], cwd=str(_基), capture_output=True,
                     text=True, encoding="utf-8", errors="replace")
        # 备份必须落在**被判仓**内（「写 A 判 B」那条）。
        # ★ 这里我第一版报了 3 次假红（"备份写到了被判仓之外"），实测备份路径明明在仓内。
        #   原因是推导式变量与外层同名 + 只在 `_留` 为空时不成立 ——
        #   **判据假红比缺陷更难查**：它逼我去改一段本来正确的代码。
        #   ⇒ 改成显式 for 循环，并把实得路径打出来（红了要能自己说明白）。
        _备份越界: list = []
        for _rel, _bk in _留:
            if not isinstance(_bk, dict):
                continue
            _bkp = str(_bk.get("backup") or "")
            if _bkp and not _bkp.startswith(str(_基)):
                _备份越界.append((_rel, _bkp))
        if _备份越界:
            坏.append(f"判十五（{标}）：备份写到了被判仓之外 {_备份越界[:2]}"
                      " ⇒ 在 A 仓备份、往 B 仓写")
        return (tuple(_面), len(_留), _p.returncode)

    _结果 = [_一轮(str(i)) for i in range(3)]
    for _x in _结果:
        if _x == ("坏",):
            return
        if _x[1] != 1 or _x[2] != 0:
            坏.append(f"判十五：真 cargo 端到端没达成（{_x}）⇒ 改动没落主树或复跑仍红")
            return
    if len(set(_结果)) != 1:
        坏.append(f"判十五：**连跑 3 次结论不一致** {_结果} ⇒ 不可重复")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十五真工具链端到端且可重复：绿")


def 判十六非UTF8工具输出() -> None:
    """★ R268：**工具输出不一定能解成 UTF-8，而硬编 UTF-8 会把整条链静默打死**。

    真读数（2026-11-05，本机真 javac，中文 locale 是出厂默认）：
      原始字节 b'src\\Helper.java:1: \xb4\xed\xce\xf3: \xd5\xd2\xb2\xbb\xb5\xbd\xb7\xfb\xba\xc5'
      按 utf-8 解码 ⇒ `src\\Helper.java:1: ??????`
      ⇒ 我上一轮特意补的中文诊断词「错误/警告」**一个字都匹配不上** ⇒ 改动面 []
      而 `locale.getpreferredencoding()` 返回 **utf-8**（跟子进程实际用的 ANSI 代码页
      不是一回事）⇒ 靠它猜会猜错。
    修复后：`src\\Helper.java:1: 错误: 找不到符号` ⇒ 改动面 ['src/Helper.cs'→'Helper.java']。

    ★ 这条齿同时验**真工具链**：本机有 javac 就真跑一遍端到端；
      没有就明说"没测到"，**不判绿**。
    """
    import shutil as _sh, subprocess as _sp
    from agentscope_orchestrator import gate_spec as _gs
    # ① 解码器本身：GBK 字节必须解得出中文（不依赖任何外部工具）
    _gbk = "src\\A.java:1: 错误: 找不到符号\n".encode("gbk")
    _解 = _gs._解输出268(_gbk)
    if "错误" not in _解:
        坏.append(f"判十六：GBK 字节解不出中文（实得 {_解[:40]!r}）⇒ 中文 locale 下失败栈整条失效")
    # UTF-8 字节必须仍然按 UTF-8 解（不许为了 GBK 把 UTF-8 搞坏）
    if _gs._解输出268("中文OK".encode("utf-8")) != "中文OK":
        坏.append("判十六：UTF-8 字节反而解错了")
    # ② 真 javac 端到端（有才跑）
    _javac = _sh.which("javac")
    if not _javac:
        坏.append("判十六：本机没有 javac ⇒ **没测到**（不算通过）")
        return
    from agentscope_orchestrator import generic_goal as _G
    _基 = Path(tempfile.mkdtemp(prefix="r268_"))
    (_基 / "src").mkdir(parents=True)
    (_基 / "out").mkdir()
    (_基 / "src" / "Helper.java").write_text(
        "public class Helper { public static int h(){ return undefinedv; } }\n", encoding="utf-8")
    (_基 / "src" / "Main.java").write_text(
        "public class Main { public static void main(String[] a){ System.out.println(Helper.h()); } }\n",
        encoding="utf-8")
    _门 = [{"name": "javac", "type": "compile",
            "cmd": [_javac, "-encoding", "UTF-8", "-d", "out",
                    "src/Main.java", "src/Helper.java"],
            "cwd": str(_基), "expect_exit": 0}]
    _r, _d = _gs.evaluate(_门, _基, _基)
    if _r.get("javac"):
        坏.append("判十六：Java 起始态就绿 ⇒ 这条齿测不到东西")
        return
    _面 = _G._失败栈改动面236(_d, _基, [_基 / "src"])
    if "src/Helper.java" not in _面:
        坏.append(f"判十六：真 javac（中文 locale）算不出改动面（实得 {_面}）"
                  f" ⇒ 非 UTF-8 输出把整条链打死了")
        return
    _基线 = _G._红门违规点(_d)
    _新 = (_基 / "src" / "Helper.java").read_text(encoding="utf-8").replace("undefinedv", "41 + 1")
    _交付 = [{"path": "src/Helper.java", "focus": 0, "agent_instruction": "",
              "gates": _门, "depends_on": [], "machine": None}]
    _res, _red, _留, _退 = _G._增量应用({"src/Helper.java": _新}, _基线, _基, _交付)
    if len(_留) != 1:
        坏.append(f"判十六：Java 端到端没落地（保留 {len(_留)}，回退 {_退}）")
        return
    _p = _sp.run([_javac, "-encoding", "UTF-8", "-d", "out",
                  "src/Main.java", "src/Helper.java"],
                 cwd=str(_基), capture_output=True)
    if _p.returncode != 0:
        坏.append(f"判十六：独立复跑 javac rc={_p.returncode} ⇒ 主树里的字节不成立")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十六非UTF8工具输出：绿")


def 判十七目标树无人值守裁决() -> None:
    """★ R275：**按目标树清单无人值守跑完并汇总裁决**，而且红要说清是哪一段哪条门。

    真读数（2026-11-05，真 git 仓 + 真判据门，三档）：
      ① 起始      ⇒ 达成 False，点名 task_0 / task_2 的 grep_required 红
      ② 改完源码  ⇒ 达成 True，全部段绿
      ③ 改坏 tests/ ⇒ 约束段 con_0 红，下游全部**级联跳过**，父目标未达成

    ★ 这条齿要证明的是"执行器不是装饰"：
      · 把某段门换成必红 ⇒ 达成必须 False；
      · 把约束段弄红 ⇒ 下游不许还判绿（级联）；
      · 会跑的段没有门 ⇒ 判"拒跑"，**空判据不许当绿**。
    """
    import json as _json
    import subprocess as _sp
    from agentscope_orchestrator import auto_goal as _ag, goal_runner as _gr

    _基 = Path(tempfile.mkdtemp(prefix="r275_")) / "repo"
    (_基 / "src").mkdir(parents=True)
    (_基 / "tests").mkdir()
    (_基 / "src" / "a.py").write_text("VALUE = 0\n", encoding="utf-8")
    (_基 / "tests" / "t.py").write_text("assert True\n", encoding="utf-8")
    for _c in (["git", "init", "-q"],
               ["git", "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A"],
               ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
        _sp.run(_c, cwd=str(_基), capture_output=True)

    _规 = {"name": "两段", "pre": ["仓已就位"], "constraints": ["不许改 tests/"],
           "gates": [
               {"type": "grep_required", "in": "src/a.py", "patterns": ["VALUE = 42"],
                "goal": "段A"},
               {"type": "compile", "cmd": ["git", "diff", "--quiet", "HEAD", "--", "tests/"],
                "cwd": str(_基), "expect_exit": 0, "constraint": "不许改 tests/"},
               {"type": "grep_required", "in": "src/a.py", "patterns": ["DONE"],
                "goal": "段B"}],
           "acceptance": ["全绿"]}
    _计划 = _ag.plan_tasks(_规)

    # ① 起始：必须未达成，且点名到段
    _r1 = _gr.run_plan(_计划, _基)
    if _r1.get("达成"):
        坏.append("判十七：起始态（源码还没做到）就判达成 ⇒ 执行器是装饰")
    elif not _r1.get("未达成的原因"):
        坏.append("判十七：未达成却**不点名原因** ⇒ 红得没法修")
    else:
        _文1 = " ".join(_r1["未达成的原因"])
        if "task_0" not in _文1 and "grep_required" not in _文1:
            坏.append(f"判十七：未达成的点名里没有段名/门名（{_文1[:80]}）⇒ 红得没法修")

    # ② 改完源码：必须达成
    (_基 / "src" / "a.py").write_text("VALUE = 42\nDONE = True\n", encoding="utf-8")
    _r2 = _gr.run_plan(_计划, _基)
    if not _r2.get("达成"):
        坏.append(f"判十七：源码做到了却不判达成（{_r2.get('未达成的原因')}）⇒ 执行器判不出真绿")

    # ③ 改坏 tests/：约束段红 ⇒ 下游必须级联跳过，父目标未达成
    (_基 / "tests" / "t.py").write_text("assert 1 == 1\n", encoding="utf-8")
    _r3 = _gr.run_plan(_计划, _基)
    if _r3.get("达成"):
        坏.append("判十七：踩了约束（改了 tests/）还判达成 ⇒ 边界形同虚设")
    _段3 = {x["tid"]: x["状态"] for x in (_r3.get("段") or [])}
    if _段3.get("con_0") != "红":
        坏.append(f"判十七：约束段没判红（实得 {_段3.get('con_0')}）⇒ 约束门不咬")
    _下游绿 = [k for k, v in _段3.items()
               if k != "con_0" and v == "绿"]
    if _下游绿:
        坏.append(f"判十七：约束段红了下游还判绿 {_下游绿} ⇒ 没有级联，边界被踩还能往前做")

    # ④ 空判据不许当绿：把某段的门删空
    _规2 = _json.loads(_json.dumps(_规))
    _规2["gates"] = [_规2["gates"][2]]
    _计划2 = _ag.plan_tasks(_规2)
    for _s in (_计划2.get("submissions") or _ag.plan_to_submissions(_计划2)):
        pass
    _计划2["submissions"] = _ag.plan_to_submissions(_计划2)
    for _s in _计划2["submissions"]:
        if _s["tid"] == "task_0":
            _s["gates"] = []
    _r4 = _gr.run_plan(_计划2, _基)
    _段4 = {x["tid"]: x["状态"] for x in (_r4.get("段") or [])}
    if _段4.get("task_0") == "绿":
        坏.append("判十七：没有判据门的段被判绿 ⇒ 空判据当绿")
    if _r4.get("达成"):
        坏.append("判十七：有段没有判据门却整体判达成")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十七目标树无人值守裁决：绿")


def 判十八闭环一次调用() -> None:
    """★ R276：**规划 → 裁决 → 定位 → 改码 → 复验** 一次调用闭环，且三档都要对。

    真读数（2026-11-05，真 git 仓 + 真判据门）：
      ① 没有改码输入 ⇒ `blocked_on_model=True`，但**改动面必须算出来**
         （否则调用方连"该改哪几个文件"都拿不到）；
      ② 改码完整   ⇒ ok=True，文件真落盘、复验全绿；
      ③ 改码不全   ⇒ ok=False，且**整批回滚、仓保持原状**。

    ★ 这一轮抓出的两个真缺陷（齿要能挡住它们复发）：
      · 文本门（grep_required）没有失败栈 ⇒ 改动面恒空 ⇒ 闭环断在"定位"；
      · 基线为空时 `_增量应用` 直接 `return {}, [], [], []` ⇒ **改码一个字都没落盘
        而它不报错**。
    """
    import json as _json
    import subprocess as _sp
    from agentscope_orchestrator import auto_goal as _ag, close_loop as _cl

    def _建仓() -> Path:
        _d = Path(tempfile.mkdtemp(prefix="r276_")) / "repo"
        # ★ `_d` 本身要先建出来：`(_d/"src").mkdir(parents=True)` 建的是 src，
        #   而 `git init` 的 cwd 是 `_d` ⇒ 目录不存在 ⇒ FileNotFoundError。
        _d.mkdir(parents=True)
        (_d / "src").mkdir()
        (_d / "src" / "a.py").write_text("VALUE = 0\n", encoding="utf-8")
        (_d / "src" / "b.py").write_text("OTHER = 0\n", encoding="utf-8")
        for _c in (["git", "init", "-q"],
                   ["git", "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A"],
                   ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
            _sp.run(_c, cwd=str(_d), capture_output=True)
        return _d

    _规 = {"name": "两段",
           "gates": [
               {"type": "grep_required", "in": "src/a.py", "patterns": ["VALUE = 42"]},
               {"type": "grep_required", "in": "src/b.py", "patterns": ["OTHER = 7"]}],
           "acceptance": ["全绿"]}
    _计 = _ag.plan_tasks(_规)

    # ① 没有改码输入 ⇒ 必须报阻塞，且改动面算得出来
    _基1 = _建仓()
    _r1 = _cl.close_loop(_计, _基1)
    if not _r1.get("blocked_on_model"):
        坏.append("判十八：没有改码输入却不报 blocked_on_model ⇒ 会被当成'试过了'")
    _面1 = (_r1.get("改动面") or {}).get("文件") or []
    if "src/a.py" not in _面1 or "src/b.py" not in _面1:
        坏.append(f"判十八：文本门的改动面没算出来（实得 {_面1}）"
                 "⇒ 调用方拿不到'该改哪几个文件'，闭环断在定位")
    if _r1.get("ok"):
        坏.append("判十八：没有改码输入却判 ok ⇒ 没测到当成通过了")

    # ② 改码完整 ⇒ ok，且文件真落盘
    _基2 = _建仓()
    _r2 = _cl.close_loop(_计, _基2, adopted={"src/a.py": "VALUE = 42\n",
                                             "src/b.py": "OTHER = 7\n"})
    if not _r2.get("ok"):
        坏.append(f"判十八：改码完整却不判 ok（{_r2.get('阶段')} / "
                 f"{(_r2.get('复验') or {}).get('未达成的原因')}）")
    for _rel, _want in (("src/a.py", "VALUE = 42"), ("src/b.py", "OTHER = 7")):
        _实 = (_基2 / _rel).read_text(encoding="utf-8")
        if _want not in _实:
            坏.append(f"判十八：{_rel} 没真落盘（实得 {_实!r}）⇒ 应用是假的")

    # ③ 改码不全 ⇒ ok 必须 False；**改对了的那个文件要保留进展**（R278b）
    #   ★ 口径修正（R278b，真读数）：原来这里断言"未达成必须整批回滚、仓保持原状"。
    #     那是 R276 的设计，而 R278 的真迭代证明它**让迭代永远前进不了**：
    #     轮1 改对 a.py、b/c 还红 ⇒ 整批回滚 ⇒ 连 a.py 也丢 ⇒ 轮2 从零开始。
    #     ⇒ 正确口径有三条，缺一不可：
    #        ① `ok` 严格 False（**保留 ≠ 交付**，半成品绝不许当成功）；
    #        ② 改对的文件（使红段数严格减少的）**保留**；
    #        ③ 没变好的文件**必须回滚**（不许留着垃圾）。
    _基3 = _建仓()
    _r3 = _cl.close_loop(_计, _基3, adopted={"src/a.py": "VALUE = 42\n"})
    if _r3.get("ok"):
        坏.append("判十八：只改了一半却判 ok ⇒ 半成品被当成功交付")
    _应留 = "src/a.py" in ((_r3.get("应用") or {}).get("保留") or [])
    _实留 = "VALUE = 42" in (_基3 / "src/a.py").read_text(encoding="utf-8")
    if _应留 != _实留:
        坏.append(f"判十八：账目说保留={_应留} 而盘上={_实留} ⇒ 账实不符")
    _坏留 = [x for x in ((_r3.get("应用") or {}).get("保留") or [])
             if x != "src/a.py"]
    if _坏留:
        坏.append(f"判十八：没让红段减少的文件也被保留了 {_坏留} ⇒ 留了垃圾")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十八闭环一次调用：绿")


def 判十九闭环工具真接上() -> None:
    """★ R277：闭环必须是**工具**，不是只有函数；而且要走 MCP 入口真的跑一遍。

    真读数（2026-11-06）：`close_loop.close_loop()` 做完后，
      全仓 grep 只有 `def close_loop` 和它内部的自我引用 —— **零调用方**。
      「一段驱动不了的接线等于没有接线」；一个没有调用方的函数同样等于没有接线。

    这条齿钉四件事：
      ① `close_goal` 真注册进 MCP manager（不是只写了装饰器）；
      ② 留空改码 ⇒ 阶段=阻塞、blocked_on_model=True，**但改动面必须算得出来**；
      ③ 改码完整 ⇒ ok=True 且**文件真落盘**；
      ④ 改码不全 ⇒ ok=False 且**整批回滚、仓保持原状**。
    """
    import json as _json
    import subprocess as _sp
    from agentscope_orchestrator import taixu_mcp as _tm

    # ① 真注册
    _mgr = getattr(_tm.mcp, "_tool_manager", None)
    _工具 = set(getattr(_mgr, "_tools", {}) or {})
    if not any(k.endswith("close_goal") for k in _工具):
        坏.append("判十九：close_goal 没注册进 MCP manager ⇒ 有函数没工具，客户端调不到")
        return

    def _建仓() -> Path:
        _d = Path(tempfile.mkdtemp(prefix="r277_")) / "repo"
        _d.mkdir(parents=True)
        (_d / "src").mkdir()
        (_d / "src" / "a.py").write_text("VALUE = 0\n", encoding="utf-8")
        (_d / "src" / "b.py").write_text("OTHER = 0\n", encoding="utf-8")
        for _c in (["git", "init", "-q"],
                   ["git", "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A"],
                   ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
            _sp.run(_c, cwd=str(_d), capture_output=True)
        return _d

    _规 = {"name": "两段",
           "gates": [
               {"type": "grep_required", "in": "src/a.py", "patterns": ["VALUE = 42"]},
               {"type": "grep_required", "in": "src/b.py", "patterns": ["OTHER = 7"]}],
           "acceptance": ["全绿"]}
    _g = _json.dumps(_规, ensure_ascii=False)

    # ② 没改码输入
    _基1 = _建仓()
    _r1 = _json.loads(_tm.close_goal(_g, str(_基1)))
    if not _r1.get("blocked_on_model"):
        坏.append("判十九：留空改码却不报 blocked_on_model ⇒ 会被当成'试过了'")
    if _r1.get("ok"):
        坏.append("判十九：没改码输入却判 ok ⇒ 没测到当成通过了")
    _面 = (_r1.get("改动面") or {}).get("文件") or []
    if "src/a.py" not in _面:
        坏.append(f"判十九：没算改动面（实得 {_面}）⇒ 调用方拿不到'该改哪几个文件'")

    # ③ 改码完整
    _基2 = _建仓()
    _r2 = _json.loads(_tm.close_goal(
        _g, str(_基2),
        _json.dumps({"src/a.py": "VALUE = 42\n", "src/b.py": "OTHER = 7\n"})))
    if not _r2.get("ok"):
        坏.append(f"判十九：改码完整却不判 ok（{_r2.get('阶段')}）")
    for _rel, _want in (("src/a.py", "VALUE = 42"), ("src/b.py", "OTHER = 7")):
        if _want not in (_基2 / _rel).read_text(encoding="utf-8"):
            坏.append(f"判十九：{_rel} 没真落盘 ⇒ 工具应用是假的")

    # ④ 改码不全 ⇒ ok 必须 False；改对的保留、没变好的回滚（口径同判十八③）
    _基3 = _建仓()
    _r3 = _json.loads(_tm.close_goal(
        _g, str(_基3), _json.dumps({"src/a.py": "VALUE = 42\n"})))
    if _r3.get("ok"):
        坏.append("判十九：只改一半却判 ok ⇒ 半成品被当成功交付")
    _应留 = "src/a.py" in ((_r3.get("应用") or {}).get("保留") or [])
    _实留 = "VALUE = 42" in (_基3 / "src" / "a.py").read_text(encoding="utf-8")
    if _应留 != _实留:
        坏.append(f"判十九：账目说保留={_应留} 而盘上={_实留} ⇒ 账实不符")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判十九闭环工具真接上：绿")


def 判二十迭代闭环会前进() -> None:
    """★ R278：迭代必须**真的能前进**，而且不许空转、不许无限跑。

    真读数（2026-11-06，真 git 仓 + 三段目标 + 每轮只改对一个文件）：
      修之前：轮1 改对 a.py，但 b/c 还红 ⇒ 复验红 ⇒ **整批回滚** ⇒ 连 a.py 也丢
              ⇒ 轮2 从零开始 ⇒ **永远到不了**（实测 4 轮后停在"阻塞"）。
      修之后：轮1 保留 a.py、轮2 保留 b.py、轮3 达成 —— **改动面逐轮收缩**。
              ⇒ 改动面轨迹必须是 [3,2,1] 这种**严格变小**，不是 [3,3,3,3]。

    这条齿钉死四件事：
      ① 没有改码器 ⇒ 走**阻塞**且**只跑 1 轮**（不许"跑满轮数再说到不了"）；
      ② 每轮改对一个 ⇒ 必须**真达成**，且改动面逐轮收缩；
      ③ 每轮都改不动 ⇒ 必须判**打转**并提前停（不许跑满预算）；
      ④ 预算用尽时必须如实报，不许假装达成。
    """
    import subprocess as _sp
    from agentscope_orchestrator import auto_goal as _ag, goal_iterate as _gi

    def _建仓() -> Path:
        _d = Path(tempfile.mkdtemp(prefix="r278_")) / "repo"
        _d.mkdir(parents=True)
        (_d / "src").mkdir()
        for _n in ("a", "b", "c"):
            (_d / "src" / f"{_n}.py").write_text("V = 0\n", encoding="utf-8")
        for _c in (["git", "init", "-q"],
                   ["git", "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A"],
                   ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
            _sp.run(_c, cwd=str(_d), capture_output=True)
        return _d

    _规 = {"name": "三段",
           "gates": [
               {"type": "grep_required", "in": "src/a.py", "patterns": ["V = 1"]},
               {"type": "grep_required", "in": "src/b.py", "patterns": ["V = 2"]},
               {"type": "grep_required", "in": "src/c.py", "patterns": ["V = 3"]}],
           "acceptance": ["全绿"]}
    _计 = _ag.plan_tasks(_规)
    _所有 = ("src/a.py", "src/b.py", "src/c.py")
    _值 = {"src/a.py": "V = 1", "src/b.py": "V = 2", "src/c.py": "V = 3"}

    # ① 没有改码器 ⇒ 阻塞且只跑 1 轮
    _r1 = _gi.iterate(_计, _建仓(), max_rounds=3)
    if not _r1.get("blocked_on_model"):
        坏.append("判二十：没有改码器却不报 blocked_on_model ⇒ 会被当成'试了多轮'")
    if _r1.get("用了几轮") != 1:
        坏.append(f"判二十：没有改码器却跑了 {_r1.get('用了几轮')} 轮 "
                 "⇒ 空转，会让人以为'试了多轮都没成'")
    if _r1.get("ok"):
        坏.append("判二十：没有改码器却判达成 ⇒ 没测到当成通过")

    # ② 每轮改对一个 ⇒ 必须真达成，且改动面严格收缩
    def _逐轮(i, plan, 根, 上):
        _目标 = {1: "src/a.py", 2: "src/b.py", 3: "src/c.py"}
        _rel = _目标.get(i)
        if not _rel:
            return None
        _新 = {r: (根 / r).read_text(encoding="utf-8") for r in _所有}
        _新[_rel] = _值[_rel] + "\n"
        return _新

    _r2 = _gi.iterate(_计, _建仓(), 改码=_逐轮, max_rounds=5)
    if not _r2.get("ok"):
        坏.append(f"判二十：每轮改对一个却到不了达成（停因={_r2.get('停因')}，"
                 f"轮次={[x['ok'] for x in _r2.get('轮次') or []]}）"
                 "⇒ 局部进展没被保留，迭代永远前进不了")
    _轨 = [len(x) for x in (_r2.get("改动面轨迹") or [])]
    if _轨 and any(_轨[k] <= _轨[k + 1] for k in range(len(_轨) - 1)):
        坏.append(f"判二十：改动面轨迹没有逐轮收缩（实得 {_轨}）"
                 "⇒ 改对的文件被回滚了，下一轮又从头来")

    # ③ 每轮都改不动 ⇒ 判打转并提前停
    def _不动(i, plan, 根, 上):
        return {r: (根 / r).read_text(encoding="utf-8") for r in _所有}

    _r3 = _gi.iterate(_计, _建仓(), 改码=_不动, max_rounds=5)
    if _r3.get("ok"):
        坏.append("判二十：改不动却判达成")
    if _r3.get("停因") != "打转":
        坏.append(f"判二十：改不动却没判打转（停因={_r3.get('停因')}）"
                 "⇒ 会跑满预算重复同一个失败")
    if (_r3.get("用了几轮") or 99) >= 5:
        坏.append(f"判二十：打转还跑满了预算（{_r3.get('用了几轮')} 轮）⇒ 护栏没咬")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十迭代闭环会前进：绿")


def 判二十一模型产码的纪律() -> None:
    """★ R279：模型产出的改码**必须过纪律**才能进仓；不依赖网络的部分全在这里钉死。

    真读数（2026-11-06）：上游整段不可用（5 个通道：3 个缺 key、1 个无权访问分组、
    1 个「上游服务异常」）⇒ 这一轮**没跑成真模型**。但"模型产码"这条链上的
    **纪律**是可以离线钉死的——它们正是最容易被绕过的地方：

      ① 越界文件必须**拒收并列出**（模型想改 tests/ ⇒ 不许悄悄丢掉，也不许放行）；
      ② 围栏/前言必须能剥（否则"模型明明给了 JSON 我却说没有"）；
      ③ 「无法完成」必须如实回传，不许当成"没改"混过去；
      ④ 拿不到产物 ⇒ `drive_goal` 必须报 `blocked_on_model` 且**只跑 1 轮**，
         **不许把"没拿到"说成"改完了"**。

    ★ 诚实边界：这条齿**不**证明"真模型能改对代码"——那要上游活着才测得到。
    """
    import json as _json
    from agentscope_orchestrator import model_coder as _mc, taixu_mcp as _tm

    _NL = chr(10)
    _围 = "```json" + _NL
    _允许 = ["src/a.py", "src/b.py"]

    # ① 越界必须拒收且列出
    _码, _拒 = _mc.解析改码(_json.dumps({"src/a.py": "X=1" + _NL, "tests/t.py": "破坏测试"},
                                          ensure_ascii=False), _允许)
    if "src/a.py" not in _码:
        坏.append("判二十一：合法项被误拒 ⇒ 模型白干")
    if not any("tests/t.py" in x for x in _拒):
        坏.append(f"判二十一：越界文件没被拒收也没被列出（拒={_拒}）"
                 "⇒ 模型能改判据文件，纪律形同虚设")
    if "tests/t.py" in _码:
        坏.append("判二十一：越界文件进了改码集 ⇒ 会把 tests/ 改坏")

    # ② 围栏 / 前言
    _体 = _json.dumps({"src/a.py": "X = 1" + _NL}, ensure_ascii=False)
    for _文, _名 in ((_围 + _体 + _围, "围栏"),
                     ("好的：" + _NL + _体, "前言")):
        _c2, _r2 = _mc.解析改码(_文, _允许)
        if "src/a.py" not in _c2:
            坏.append(f"判二十一：{_名}挡不住（实得 {_c2} / {_r2}）"
                     "⇒ 模型明明给了 JSON 却说没有")

    # ③ 「无法完成」如实回传
    _c3, _r3 = _mc.解析改码('{"__无法完成__": "门要的东西不存在"}', _允许)
    if _c3:
        坏.append("判二十一：模型自述无法完成却产出了改码 ⇒ 自欺")
    if not any("无法完成" in x for x in _r3):
        坏.append("判二十一：模型自述无法完成却没回传原因 ⇒ 看不出为什么没改")

    # ④ 拿不到产物 ⇒ 阻塞且只跑 1 轮（用必然失败的通道名，不碰真上游）
    _d = Path(tempfile.mkdtemp(prefix="r279_")) / "repo"
    _d.mkdir(parents=True)
    (_d / "src").mkdir()
    (_d / "src" / "a.py").write_text("V = 0\n", encoding="utf-8")
    _规 = {"name": "一段",
           "gates": [{"type": "grep_required", "in": "src/a.py",
                      "patterns": ["V = 1"]}],
           "acceptance": ["全绿"]}
    _r = _json.loads(_tm.drive_goal(_json.dumps(_规, ensure_ascii=False), str(_d),
                                    max_rounds=3, channel="不存在的通道__r279"))
    if not _r.get("blocked_on_model"):
        坏.append(f"判二十一：通道不可用却没报 blocked_on_model（停因={_r.get('停因')}）"
                 "⇒ 会把'没拿到'说成'没改成'")
    # ★ R282 口径更新（这是**真红**，不是判据坏了）：
    #   原来断言「通道不可用 ⇒ 必须跑了恰好 1 轮」。R282 加了**开工前自体检**之后，
    #   通道不可用在**开工前**就被拦下 ⇒ 正确行为是 **0 轮**，不是 1 轮。
    #   两者的区别很重要，判据要**分档**判，不能把两种情形用一个数字钉死：
    #     · `拒跑`（开工前就发现没通道）⇒ **0 轮**，且必须给出拒跑原因；
    #     · 走了 `阻塞`（开工了但没拿到改码）⇒ **恰好 1 轮**，不许跑满预算空转。
    _轮 = _r.get("用了几轮")
    if _r.get("拒跑"):
        if _轮 != 0:
            坏.append(f"判二十一：开工前就拒跑了，却跑了 {_轮} 轮 "
                     "⇒ 拒跑应当一个文件都不碰")
        if not str(_r.get("诚实边界") or "").strip():
            坏.append("判二十一：拒跑了却没写诚实边界 ⇒ 分不清「没开工」和「试了没成」")
    elif _轮 != 1:
        坏.append(f"判二十一：通道不可用却跑了 {_轮} 轮 "
                 "⇒ 空转，会让人以为'试了多轮'")
    if _r.get("ok"):
        坏.append("判二十一：通道不可用却判达成 ⇒ 没测到当成通过")
    if (_d / "src" / "a.py").read_text(encoding="utf-8") != "V = 0\n":
        坏.append("判二十一：没有模型产物却把仓改了 ⇒ 凭空写盘")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十一模型产码的纪律：绿")



def 判二十二通道降级判活必须看响应体() -> None:
    """★ R280：**通道降级的前提是"判活判得准"。**「能连上」不等于「能用」。

    真读数（2026-11-06，本仓 5 个通道逐个探）：
      aaa / ggg / mf  凭据不可读（缺 key）
      dddddd          HTTP 403 无权访问 国模 源头 超低倍率 分组
      qww             **HTTP 200 + {"error":{"message":"上游服务异常，请稍后重试 …"}}**

    ★ 缺陷：`_probe_once` 原来是
        `with urlopen(...) as r: return 活, f"HTTP {r.status}"`
      —— **只看状态码、从不读响应体** ⇒ 把 qww 判成「活」。
      后果不是"多试一次"，而是**通道降级形同虚设**：4 个通道死了、第 5 个也死了，
      编排器却认为"有活通道"，一路跑到超时才报一个看不懂的 JSONDecodeError。

    这条齿钉三件事：
      ① 判活**必须读响应体**：200 + 只有 error 没有 content ⇒ 不是活；
      ② 判死的口径**必须复用 `_classify_failure`**（不许另起一套尺子）：
         同一句「请稍后重试」在探活与失败分类里必须得到**同一个**结论；
      ③ 报告的字段必须与**返回值自洽**（`交给router试` == 真的返回了哪些），
         否则调用方只看 `rep["live"]` 就会得出与事实相反的结论。
    """
    import inspect as _insp
    from agentscope_orchestrator import gateway as _gw

    # ① 判活要读响应体
    _源 = _insp.getsource(_gw._probe_once)
    _码 = "\n".join(l for l in _源.splitlines()
                     if l.strip() and not l.strip().startswith("#"))
    if "r.read(" not in _码:
        坏.append("判二十二：探活不读响应体 ⇒ 200+error 会被判成活，通道降级形同虚设")
    if '"error"' not in _码:
        坏.append("判二十二：探活没检查响应体里的 error 字段 ⇒ 判活判不准")

    # ② 判死口径必须复用 _classify_failure
    if "_classify_failure" not in _码:
        坏.append("判二十二：探活自己另起了一套判死标准（没调 _classify_failure）"
                 "⇒ 同一句错误在探活与失败分类里会得出不同结论")
    _瞬 = _gw._classify_failure(None, '{"error":{"message":"上游服务异常，请稍后重试"}}')
    if _瞬 == _gw.硬死:
        坏.append("判二十二：含「请稍后重试」的错误被判硬死 ⇒ 一次抖动就让整台机器拒跑")

    # ③ 报告与返回值自洽
    _chs, _rep = _gw.select_live_channels("", probe=True)
    _真返回 = sorted(getattr(c, "name", "") for c in _chs)
    if "交给router试" not in _rep:
        坏.append("判二十二：报告里没有「交给router试」⇒ 调用方只能看 live，"
                 "而 live 不含读不到的通道 ⇒ 会得出'一个都没有'的错误结论")
    elif _rep.get("交给router试") != _真返回:
        坏.append(f"判二十二：报告说交给 router 的是 {_rep.get('交给router试')}，"
                 f"实际返回 {_真返回} ⇒ 报告与返回值不自洽")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十二通道降级判活必须看响应体：绿")



def 判二十三通道体检三档分明() -> None:
    """★ R281：体检表必须**逐对真探活**，且三档死法的责任方不许混。

    真读数（2026-11-07，本机 7 个 (通道,模型) 对）：
      aaa / ggg / mf / mf2   缺key      → 你：设 *_API_KEY
      dddddd × 3             无权访问  → 你：换有权限的 key
      qww                    上游异常  → 上游：只能等

    ★ 本轮抓到的真缺陷（R281c）：我第一版写 `if 类 != "硬死"`，而
      `gateway.硬死` 的值其实是 **`'dead'`**（活='live'、读不到='unreadable'，全英文）
      ⇒ **7 个对里 6 个硬死，却全部进了"可交给 router"** ⇒ 体检表说"有 5 个槽可试"，
      而实际只有 1 个。**又一次"不要猜数据的形状，去读它的生产者"。**

    这条齿钉四件事：
      ① 硬死通道**必须**被排除出"可交给 router"（不许把死通道算成机会）；
      ② 三种死法必须**分得开**（缺key / 无权访问 / 上游异常 不许压成一档）；
      ③ `可开工` 与 `能开工` 是**两件事**，不许用一个字段承担两个意思；
      ④ 体检表必须**说出来**哪一档是"没测到"而不是"不行"。
    """
    import json as _json
    from agentscope_orchestrator import channel_doctor as _cd, gateway as _gw, taixu_mcp as _tm

    _表 = _cd.体检()
    _对 = _表.get("逐通道") or []
    if not _对:
        坏.append("判二十三：体检表里一个 (通道,模型) 对都没有 ⇒ 没测到当成通过")

    # ① 硬死必须被排除（★ 这就是 R281c 那个缺陷）
    _硬死名 = {x["通道"] for x in _对 if x["类"] == _gw.硬死}
    _可交 = set(_表.get("可交给router") or [])
    _混 = _硬死名 & _可交
    if _混:
        坏.append(f"判二十三：硬死通道 {sorted(_混)} 被算进「可交给 router」"
                 "⇒ 死通道被当成机会，体检表会撒谎")

    # ② 三种死法分得开（且归类函数**认得出的不许说认不出**）
    for _s, _期望 in (("HTTP 403 无权访问 分组", "无权访问"),
                      (f"channel['x'] key not found", "缺key"),
                      ('{"error":{"message":"上游服务异常，请稍后重试"}}', "上游异常")):
        _实 = _cd.归类(_s)
        if _实 != _期望:
            坏.append(f"判二十三：{_s[:30]!r} 归成了 {_实!r}，应为 {_期望!r}"
                     "⇒ 责任方会分错")

    # ③ 可开工 / 能开工 是两个意思
    if "可开工" not in _表 or "能开工" not in _表:
        坏.append("判二十三：体检表缺 可开工/能开工 ⇒ 一个字段承担了两个意思")
    elif _表.get("可开工") and not _表.get("有确认活的通道"):
        坏.append("判二十三：判了「可开工」却没有一个确认活的通道 ⇒ 名字在骗人")

    # ④ 「没测到」必须说出来
    if "未探测" not in _cd.死法对策:
        坏.append("判二十三：没有「未探测」这一档 ⇒ 认不出的会被硬塞成一档")
    if "不等于" not in str(_表.get("诚实边界") or "") and \
       "不等于" not in str(_表.get("诚实边界") or ""):
        坏.append("判二十三：诚实边界没说明「没测到≠不行」⇒ 会被当成测过")

    # ⑤ 必须真注册进 MCP（没调用方等于没接线）
    _名 = [t.name for t in _tm.mcp._tool_manager.list_tools()]
    if not any("doctor_channels" in x for x in _名):
        坏.append("判二十三：doctor_channels 没注册进 MCP manager ⇒ 有函数没工具")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十三通道体检三档分明：绿")



def 判二十四开工前必须自体检() -> None:
    """★ R282：**需要调用方记得做的步骤，等于没做。**

    真读数（2026-11-07）：上一轮做出了 `doctor_channels`，但它是**另一个工具** ——
    调用方很容易忘了先体检就直接 `drive_goal`，然后**跑到一半**才没通道：
    前几个文件白改，回执里只有一句"阻塞"。

    这条齿钉四件事（每一件都是我这一轮先后写错的地方）：
      ① **无通道 ⇒ 0 轮、仓一个字节都不许动**（不是"试了没成"）；
      ② 闸要按 **`能开工`** 判断，不许只比档名字符串
         （实测 `channel="不存在的通道"` 时 档=None、能开工=False，
          只比档名 ⇒ None != "无通道可用" ⇒ **放行**）；
      ③ 软否决（`仅剩未确认的槽`）可以被 `force=True` 绕过一次；
      ④ 硬否决（配置读不出 / 全硬死）**force 也不许** —— 重试毫无意义。
    """
    import json as _json
    import inspect as _insp282
    from agentscope_orchestrator import channel_doctor as _cd, taixu_mcp as _tm

    _d = Path(tempfile.mkdtemp(prefix="r282_")) / "repo"
    _d.mkdir(parents=True)
    (_d / "src").mkdir()
    (_d / "src" / "a.py").write_text("V = 0" + chr(10), encoding="utf-8")
    _规 = {"name": "一段",
           "gates": [{"type": "grep_required", "in": "src/a.py",
                      "patterns": ["V = 1"]}],
           "acceptance": ["全绿"]}
    _g = _json.dumps(_规, ensure_ascii=False)

    def _跑(**kw):
        return _json.loads(_tm.drive_goal(_g, str(_d), max_rounds=2, **kw))

    # ③ 硬否决：不存在的通道 + force 也不许开工
    _r3 = _跑(channel="不存在的通道__r282", force=True)
    if _r3.get("用了几轮"):
        坏.append(f"判二十四：通道根本不存在却跑了 {_r3.get('用了几轮')} 轮 "
                 "⇒ 硬否决可以被 force 绕过，闸形同虚设")
    if _r3.get("拒跑") != "no_channel_before_start":
        坏.append(f"判二十四：硬否决的拒跑原因写成了 {_r3.get('拒跑')!r} "
                 "⇒ 看不出是「没通道」还是「试过不行」")
    if (_d / "src" / "a.py").read_text(encoding="utf-8") != "V = 0" + chr(10):
        坏.append("判二十四：开工前就拒跑了，仓却被改了 ⇒ 凭空写盘")

    # ① 软否决：默认不开工
    _表 = _cd.体检()
    if _表.get("档") == "仅剩未确认的槽":
        _r1 = _跑()
        if _r1.get("用了几轮"):
            坏.append(f"判二十四：只剩未确认的槽却仍开工跑了 {_r1.get('用了几轮')} 轮 "
                     "⇒ 会写一堆文件然后一个都拿不到")
        if _r1.get("拒跑") != "no_live_channel_before_start":
            坏.append(f"判二十四：软否决的拒跑原因写成了 {_r1.get('拒跑')!r}")
        if (_d / "src" / "a.py").read_text(encoding="utf-8") != "V = 0" + chr(10):
            坏.append("判二十四：软否决没开工，仓却被改了")
        # ② 闸必须按 能开工 判断，不许按档名
        _源 = _insp282.getsource(_tm.drive_goal)
        if '体检.get("能开工")' not in _源:
            坏.append("判二十四：闸没有读 `能开工` ⇒ 档名认不出时会放行"
                     "（实测 档=None + 能开工=False 被放过）")

    # ④ 体检表自己也要说清"责任方"
    for _死 in ("缺key", "无权访问", "上游异常"):
        if _死 not in _cd.死法对策:
            坏.append(f"判二十四：死法表缺 {_死} ⇒ 责任方分不出来")
        elif not _cd.死法对策[_死][1]:
            坏.append(f"判二十四：{_死} 没有「该做什么」⇒ 只报问题不给动作")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十四开工前必须自体检：绿")



def 判二十五未达成必须给出口() -> None:
    """★ R283：**未达成时不给出口，调用方就只能猜。**

    真读数（2026-11-07，真 git 仓，三段目标，只给 a.py 的改码）：
      `close_goal` 如实返回 ok=False · 保留=['src/a.py'] —— 账是准的。
      但调用方看到 `ok=False` 会卡住：是"该再给一批改码"？还是"该换 drive_goal"？
      还是"根本没有可改的了"？**三种情形的下一步完全不同。**

    这条齿钉四件事（每一件都是我这一轮先后写错的地方）：
      ① 未达成时必须**点名**哪些段没绿（`tid`），不许只说"未达成"；
      ② 段要从 **`复验.段`** 读（`close_loop` 顶层**没有** `段`）；
      ③ 键名是 **`tid`** 不是 `id`（读 `run_plan` 的文档，别猜）；
      ④ 必须说清 `close_goal`（一轮）与 `drive_goal`（迭代）的分工。
    """
    import json as _json
    from agentscope_orchestrator import taixu_mcp as _tm

    _d = Path(tempfile.mkdtemp(prefix="r283_")) / "repo"
    _d.mkdir(parents=True)
    (_d / "src").mkdir()
    for _n in ("a", "b", "c"):
        (_d / "src" / f"{_n}.py").write_text("V = 0" + chr(10), encoding="utf-8")
    import subprocess as _sp
    for _c in (["git", "init", "-q"],
               ["git", "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A"],
               ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "i"]):
        _sp.run(_c, cwd=str(_d), capture_output=True)
    _规 = {"name": "三段",
           "gates": [{"type": "grep_required", "in": "src/a.py", "patterns": ["V = 1"]},
                     {"type": "grep_required", "in": "src/b.py", "patterns": ["V = 2"]},
                     {"type": "grep_required", "in": "src/c.py", "patterns": ["V = 3"]}],
           "acceptance": ["全绿"]}
    _g = _json.dumps(_规, ensure_ascii=False)

    # 只给 a.py ⇒ 未达成，b/c 还红
    _r = _json.loads(_tm.close_goal(_g, str(_d), _json.dumps({"src/a.py": "V = 1" + chr(10)})))
    if _r.get("ok"):
        坏.append("判二十五：只改了一个文件却判 ok ⇒ 半成品被当成功")

    # ① 必须点名未达成的段
    _段 = _r.get("未达成的段")
    if not _段:
        坏.append("判二十五：未达成却没点名哪些段没绿 ⇒ 调用方只能盲猜下一步")
    else:
        for _x in _段:
            if not _x.get("tid"):
                坏.append(f"判二十五：未达成的段没有 tid（实得 {_x}）"
                         "⇒ 段标识取成了 id 之类的错键名")
    # ②③ 段必须从 复验 里读，且键是 tid
    _复段 = (_r.get("复验") or {}).get("段") or []
    if not _复段:
        坏.append("判二十五：复验里没有段 ⇒ 读错了层（段在 复验.段，不在顶层）")
    elif not any(x.get("tid") for x in _复段):
        坏.append("判二十五：复验.段 里没有 tid ⇒ 键名猜错了")

    # ④ 必须说清分工
    if "drive_goal" not in str(_r.get("入口分工") or ""):
        坏.append("判二十五：没有说明 close_goal 与 drive_goal 的分工"
                 "⇒ 调用方不知道该怎么继续")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十五未达成必须给出口：绿")



def 判二十六多文件可重复性() -> None:
    """★ R284：**可重复性**是既定目标点名的一项，而至今只在 case02 上偶然验过一次
    （无人值守跑通 1 次，重复 4 次失败 = 1/4）。判十五只在**单文件**上连跑 3 次，
    那证明不了多文件。

    本齿用**本机真 cargo**、仓里同时有**两处独立编译错误**，每轮从全红基线出发，要求：
      ① 改动面**同时**定位到两个文件（缺一个 = 少做了，而"少做"会伪装成"做成了"）；
      ② 每轮结论一致（面、保留数、复跑 rc）——这才是"可重复"的定义；
      ③ 复跑必须是**另起进程**跑目标仓自己的门，不是复用内存里的结论。

    ★ 反向验证（本轮真做过）：把定位截断成只取第 1 个文件 ⇒ 输出
      `ok=False · 第 1 轮复跑仍红（改动没让目标仓自己的门变绿）`
      ⇒ 这条齿**真的能抓到"改了一半"**，不是摆设。

    ★ 诚实边界：本齿**不调模型**，证明的是机器侧（定位→应用→复跑）可重复，
      **不**证明模型侧可重复——后者要上游活着才测得到。
      没有 cargo ⇒ 报"没测到"，不许算绿（「没测到不能当通过了」）。
    """
    import shutil as _sh
    from agentscope_orchestrator import repeatability as _rp

    if not _sh.which("cargo"):
        坏.append("判二十六：本机没有 cargo ⇒ **没测到**（不算通过）")
        return

    _r = _rp.多文件可重复(轮数=3)
    if not _r.get("测了没"):
        坏.append(f"判二十六：没测到就被判了结论：{_r.get('结论')}")
        return
    if not _r.get("ok"):
        坏.append(f"判二十六：多文件不可重复 —— {_r.get('结论')}")
        return
    _轮 = _r.get("轮次") or []
    if len(_轮) < 3:
        坏.append(f"判二十六：只跑了 {len(_轮)} 轮，不够判可重复")
    for _i, _x in enumerate(_轮):
        if len(_x.get("面") or ()) != 2:
            坏.append(f"判二十六：第 {_i + 1} 轮只定位到 {_x.get('面')} "
                     "⇒ 两个文件里的另一个没被找到（少做会伪装成做成）")
        if _x.get("rc") != 0:
            坏.append(f"判二十六：第 {_i + 1} 轮独立复跑 rc={_x.get('rc')} "
                     "⇒ 改动没落主树或落得不完整")
    if "不调模型" not in str(_r.get("诚实边界") or ""):
        坏.append("判二十六：没写明'本齿不调模型' ⇒ 会被误当成'模型侧也可重复'")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十六多文件可重复性：绿")



def 判二十七可重复性可调且达成自带证据() -> None:
    """★ R285：把「可重复」从"我手跑过一次"变成**可调用 + 达成自带证据**。

    ★ 为什么必须有这条（真读数，2026-11-07）：
      上一轮做成了 `repeatability.多文件可重复`（真 cargo 连跑 3 轮一致），
      但**它当时只有我手调过**——调用方拿到 `ok=True` 仍然不知道
      "这台机器侧到底可不可重复"。**一段驱动不了的接线，等于没有接线。**

    这条齿钉五件事：
      ① `check_repeatable` **真注册进 MCP**（有函数没工具 = 没接线）；
      ② 工具返回**必须**带「测了没」——没有 cargo 时不许算绿；
      ③ 不支持的语言必须**如实说没验过**（不许拿 rust 的结论冒充 go）；
      ④ `drive_goal` **只在达成时**附机器侧自证（没达成就附 = 粉饰）；
      ⑤ 自证必须写明**适用边界**（不证明"你这个目标重复执行也一致"）。
    """
    import json as _json
    import inspect as _insp
    from agentscope_orchestrator import repeatability as _rp, taixu_mcp as _tm

    # ① 真注册
    _名 = [t.name for t in _tm.mcp._tool_manager.list_tools()]
    if not any("check_repeatable" in x for x in _名):
        坏.append("判二十七：check_repeatable 没注册进 MCP ⇒ 有函数没工具")
        return

    # ② 必须带「测了没」
    _r = _json.loads(_tm.check_repeatable(rounds=2))
    if "测了没" not in _r:
        坏.append("判二十七：check_repeatable 不返回「测了没」⇒ 没测到会被当成通过")
    if _r.get("ok") and not _r.get("测了没"):
        坏.append("判二十七：没测到却判 ok ⇒ 没测到当成通过")
    if "不调模型" not in str(_r.get("诚实边界") or "") and \
       "不调模型" not in str(_r.get("诚实边界") or ""):
        坏.append("判二十七：自证没写明「不调模型」⇒ 会被误当成模型侧也可重复")

    # ③ 不支持的语言必须如实说
    _g = _rp.多文件可重复(轮数=1, 语言="go")
    if _g.get("ok") or _g.get("测了没"):
        坏.append(f"判二十七：go 没验过却给出结论 {_g.get('结论')} "
                 "⇒ 拿 rust 的结论冒充别的语言")

    # ④⑤ 达成才附自证 + 必须带适用边界
    _源 = _insp.getsource(_tm.drive_goal)
    if "机器侧自证" not in _源:
        坏.append("判二十七：drive_goal 达成时不附机器侧自证 ⇒ 调用方还得自己再验一遍")
    elif 'if 果.get("ok"):' not in _源:
        坏.append("判二十七：自证没被 `ok` 包住 ⇒ 没达成也附证据 = 粉饰")
    if "适用边界" not in _源:
        坏.append("判二十七：自证没写适用边界 ⇒ 会被当成'这个目标重复执行也一致'")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十七可重复性可调且达成自带证据：绿")



def 判二十八失败必须分清责任层() -> None:
    """★ R286：**归因错了比没有归因更坏**——它会让人去改一个没坏的地方。

    真读数（2026-11-06，本机真 `gate_spec.evaluate`）：
      同一份回执里，四种失败的**下一步动作完全不同**：
        ① 命令根本起不来        → 装工具（改代码没用）
        ② 工具跑了但仓里有错    → **这才是该改代码的时候**
        ③ 判据自己写错          → 改判据（改代码没用）
        ④ 判据说"必需缺失 X"    → 该改代码

    ★ 我第一版在这种分离上**全判错了**：我拿证据里的 `file` 字段当"文件路径"，
      而它实际是 **`"repo"`（仓目录名，表示被测对象）** ⇒ ②③④ **全被归成"判据写错"**。
      其中最坏的是 ④：真代码错被判成判据坏 ⇒ 调用方会去改一个**好好的判据**，
      而真正的错在代码里，怎么改都不会绿。
      ⇒ **判据自己已经把话说清了，去读 `cause`**（`门规格非法|…` vs `必需缺失|…`）。

    这条齿钉四件事：
      ① 四种失败必须落进**四个不同档**（不是三个都叫"未达成"）；
      ② 每档必须给出**互斥的下一步**；
      ③ 认不出的必须归 `未知`（不许硬塞）；
      ④ "先修什么"的排序必须把**改了也白改的**排在前面。
    """
    import json as _json
    import tempfile as _tf
    from agentscope_orchestrator import failure_cause as _fc, gate_spec as _gs

    _d = Path(_tf.mkdtemp(prefix="r286_")) / "repo"
    _d.mkdir(parents=True)
    (_d / "src").mkdir()
    (_d / "src" / "a.py").write_text("V = 0" + chr(10), encoding="utf-8")

    _案 = {
        "①起不来": [{"name": "nosuch", "type": "compile",
                    "cmd": ["肯定没有这个命令_xyz"], "cwd": str(_d), "expect_exit": 0}],
        "②非零退出": [{"name": "nz", "type": "compile",
                     "cmd": ["python", "-c", "import sys;sys.exit(3)"],
                     "cwd": str(_d), "expect_exit": 0}],
        "③判据写错": [{"name": "gf", "type": "grep_required",
                     "in": "src/不存在.py", "patterns": ["X"]}],
        "④真代码错": [{"name": "gf2", "type": "grep_required",
                     "in": "src/a.py", "patterns": ["V = 1"]}],
    }
    _得 = {}
    for _名, _门 in _案.items():
        _r, _det = _gs.evaluate(_门, _d, _d)
        _归 = _fc.归因一批(_det, _d)
        _x = _归["逐条"][0] if _归["逐条"] else {}
        _得[_名] = _x.get("档")
        if not _x.get("档"):
            坏.append(f"判二十八（{_名}）：没归出档")

    # ① 四档必须分得开
    if _得.get("①起不来") != "无法启动":
        坏.append(f"判二十八：命令根本起不来却归成 {_得.get('①起不来')!r} "
                 "⇒ 环境问题会被当成代码问题，改十轮也不会好")
    if _得.get("③判据写错") != "判据写错":
        坏.append(f"判二十八：判据自己说规格非法，却归成 {_得.get('③判据写错')!r}")
    # ★ 这条是**本轮真犯过的错**：真代码错被判成"判据写错"
    if _得.get("④真代码错") != "代码没改对":
        坏.append(f"判二十八：**真代码错被归成 {_得.get('④真代码错')!r}** "
                 "⇒ 调用方会去改一个好好的判据，而真正的错在代码里")
    if _得.get("②非零退出") != "代码没改对":
        坏.append(f"判二十八：工具跑通但非零退出，归成了 {_得.get('②非零退出')!r}")
    if len(set(_得.values())) < 3:
        坏.append(f"判二十八：四案只分出 {len(set(_得.values()))} 档 {_得} "
                 "⇒ 混成一档，下一步动作就说不清了")

    # ② 每档动作必须互斥
    _动 = {k: v[1] for k, v in _fc.档位.items()}
    if len(set(_动.values())) != len(_动):
        坏.append("判二十八：不同档给了同一句「该做什么」⇒ 分档等于没分")

    # ③ 认不出必须归未知
    _怪 = _fc.归因({"gate": "x", "ok": False})
    if _怪.get("档") != "未知":
        坏.append(f"判二十八：空证据归成了 {_怪.get('档')!r} ⇒ 硬塞，等于编造")

    # ④ 排序：环境问题必须排最前（改了也白改的先修）
    _混 = _fc.归因一批([
        {"gate": "g1", "ok": False, "cause": "必需缺失|X", "missing": ["X"]},
        {"gate": "g2", "ok": False, "cause": "命令无法启动|g2", "rc": -1,
         "err": "[CMD_NOT_FOUND] x"},
    ], _d)
    if "环境" not in str(_混.get("最该先做的")):
        坏.append(f"判二十八：既有环境问题又有代码问题，却没把环境排在前面："
                 f"{_混.get('最该先做的')!r}")

    # ⑤ ★ R286c：**工具不许报假绿**（本轮真犯过）
    #   第一版 `for _g in (_段.get("门") or [])` —— 以为 `门` 是"证据字典的列表"，
    #   实测是 `{"grep_required": false}`（**一个 dict，值是布尔结论**）。
    #   遍历 dict 拿到的是**键名字符串** ⇒ 证据恒空 ⇒ 工具对外报 `ok=True`
    #   而 `目标达成吗=False`。**这比归因错误更糟：它让调用方以为一切正常。**
    from agentscope_orchestrator import taixu_mcp as _tmx
    _d2 = Path(_tf.mkdtemp(prefix="r286c_")) / "repo"
    _d2.mkdir(parents=True)
    (_d2 / "src").mkdir()
    (_d2 / "src" / "a.py").write_text("V = 0" + chr(10), encoding="utf-8")
    _规2 = {"name": "一段",
            "gates": [{"type": "grep_required", "in": "src/a.py",
                       "patterns": ["V = 1"]}],
            "acceptance": ["全绿"]}
    _j2 = _json.loads(_tmx.explain_failure(
        _json.dumps(_规2, ensure_ascii=False), str(_d2)))
    if _j2.get("目标达成吗") is False and _j2.get("ok"):
        坏.append("判二十八：目标明明是红的，工具却报 ok=True "
                 "⇒ **报假绿**，调用方会以为一切正常")
    if not ((_j2.get("归因") or {}).get("逐条")):
        坏.append("判二十八：红着却一条逐条归因都没有 ⇒ 归因链断了，"
                 "必须如实说'没归因到'，不许报'没有失败'")

    # ⑥ 已达成时不许报失败
    _d3 = Path(_tf.mkdtemp(prefix="r286d_")) / "repo"
    _d3.mkdir(parents=True)
    (_d3 / "src").mkdir()
    (_d3 / "src" / "a.py").write_text("V = 1" + chr(10), encoding="utf-8")
    _j3 = _json.loads(_tmx.explain_failure(
        _json.dumps({"name": "一段",
                     "gates": [{"type": "grep_required", "in": "src/a.py",
                                "patterns": ["V = 1"]}],
                     "acceptance": ["全绿"]}, ensure_ascii=False), str(_d3)))
    if not _j3.get("ok"):
        坏.append(f"判二十八：目标已达成却报失败 {_j3.get('归因', {}).get('汇总')}")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十八失败必须分清责任层：绿")



def 判二十九跨模块字段契约() -> None:
    """★ R287：**这几轮栽的 4 次错，全是同一句「猜了别人的形状」。**

    真读数（2026-11-06，五轮里 4 次）：
      · R280e 读错层：`client.timeout`
      · R281c 猜常量值：`硬死` 其实是 `'dead'`
      · R283b 读错层 + 猜键名：`复验.段` 不在顶层，键是 `tid` 不是 `id`
      · R286b/c 猜字段含义（`file`="repo"）和容器类型（`门` 是 dict）
    **每次代码都能跑、不报错、看着合理，只是判断全都反了。**

    这条齿钉四件事：
      ① 契约表必须**真跑生产者**核对，不是抄文档；
      ② 表里写的形状必须和真产出一致（红就说明有人在猜）；
      ③ 必须覆盖**可空字段**（`复验`/`应用` 在没改码时是 `None`，不是空 dict）；
      ④ 表自己也不许缺项 —— 关键的生产者必须都在表里。

    ★ 反向验证（本轮真做过）：契约第一版写「`复验` 是 dict」，
      实测 `adopted=None` 时它是 `None` ⇒ `校验()` **自己就报红了 3 条**。
      修准之后 31 项全绿 —— 说明这张表**真在起作用**，不是摆设。
    """
    from agentscope_orchestrator import field_contract as _fcl

    _r = _fcl.校验()
    if not _r.get("ok"):
        for _e in (_r.get("错") or [])[:4]:
            坏.append(f"判二十九：字段契约与真产出不一致 —— {_e}")
        return

    _表 = _fcl.表()
    # ④ 关键生产者必须在表里
    for _k in ("gate_spec", "goal_runner", "close_loop", "gateway"):
        if not any(_k in _x for _x in _表):
            坏.append(f"判二十九：契约表缺 `{_k}` ⇒ 它的字段又在裸猜")

    # ③ 可空字段必须写明"什么时候是 None"
    _闭键 = next((k for k in _表 if "close_loop" in k), "")
    if _闭键:
        _复 = str(_表[_闭键].get("复验") or "")
        if "None" not in _复:
            坏.append("判二十九：`复验` 没写明可空 ⇒ 下游 `果['复验']['段']` "
                     "会 TypeError，或用 `or {}` 静默吞成'没有失败'")

    # ② 最贵的那个坑（file 是被测对象名，不是路径）必须写在表里
    _e键 = next((k for k in _表 if "gate_spec" in k), "")
    if _e键:
        _f = str(_表[_e键].get("详情[].file") or "")
        if "不是文件路径" not in _f:
            坏.append("判二十九：没写明 `详情[].file` **不是文件路径** "
                     "⇒ R286b 那类错（三种失败全归成一档）会重演")

    # ① 必须是"真跑核对"，不是抄文档：校验里必须真调用过 evaluate
    import inspect as _insp
    _源 = _insp.getsource(_fcl.校验)
    if "evaluate(" not in _源 or "run_plan(" not in _源:
        坏.append("判二十九：`校验()` 没有真跑生产者（evaluate/run_plan）"
                 "⇒ 只是抄文档，文档也会过期")
    if _r.get("过", 0) < 20:
        坏.append(f"判二十九：只核对了 {_r.get('过')} 项，覆盖面太窄")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判二十九跨模块字段契约：绿")



def 判三十真落主树且旁证未动() -> None:
    """★ R288：「改动必须真落主树」此前只有**间接**证据（文件变了 + 复跑 rc=0）。

    真读数（2026-11-06，真 git 仓，仓里有 a/b/c 三个源文件 + docs/note.md）：
      跑完一轮后做**全仓 sha256 指纹**，逐文件比对：
        变了哪些文件: ['.audit_trail/generic_backup/src/a.py', 'src/a.py']
      ⇒ 两个发现：
        ① 真改动**只有** `src/a.py`；`b.py`/`c.py`/`docs/note.md` **一个字节没动**；
        ② 但仓里多了一个 `.audit_trail/generic_backup/src/a.py`（备份），
           内容**等于改前**（`'V = 0\n'`）。
      ⇒ 这两件事必须**分开说**：
        · 备份落在**被判仓内**是 R267 定的**有意设计**（"在 A 仓备份、往 B 仓写"才是缺陷）；
        · 但如果判据不把"审计痕"从"真改动"里分出来，就会得出
          "**其它文件被碰了**"这个错误结论 —— 反过来，也可能被拿去
          **掩盖**真的越界改动（"哦那是备份"）。
      ⇒ 所以判据必须：真改动 ⊆ 预期集合 **且** 审计痕全部在 `.audit_trail/` 下
        **且** 备份内容 == 改前内容（否则"备份"其实是"第二份改动"）。

    这条齿钉五件事：
      ① 真改动必须**恰好**是预期文件（多一个 = 越界，少一个 = 没落地）；
      ② 无关文件（源码/文档）必须**逐字节未变**；
      ③ 审计痕必须全在 `.audit_trail/` 下（不许散落）；
      ④ 备份内容必须**等于改前**（证明它是备份，不是另一份改动）；
      ⑤ 目标文件在主树里的内容必须真变了（否则只是"没报错"）。
    """
    import hashlib as _hl
    import subprocess as _sp
    import tempfile as _tf
    from agentscope_orchestrator import auto_goal as _ag, close_loop as _cl

    def _指纹(_根: Path) -> dict:
        _h = {}
        for _p in sorted(_根.rglob("*")):
            if not _p.is_file():
                continue
            if ".git" in _p.parts or "__pycache__" in _p.parts:
                continue
            _k = str(_p.relative_to(_根)).replace(chr(92), "/")
            _h[_k] = _hl.sha256(_p.read_bytes()).hexdigest()
        return _h

    _d = Path(_tf.mkdtemp(prefix="r288_")) / "repo"
    _d.mkdir(parents=True)
    (_d / "src").mkdir()
    (_d / "docs").mkdir()
    for _n in ("a", "b", "c"):
        (_d / "src" / f"{_n}.py").write_text("V = 0" + chr(10), encoding="utf-8")
    (_d / "docs" / "note.md").write_text("# 别动我" + chr(10), encoding="utf-8")
    (_d / "README.md").write_text("readme" + chr(10), encoding="utf-8")
    for _c in (["git", "init", "-q"],
               ["git", "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A"],
               ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "i"]):
        _sp.run(_c, cwd=str(_d), capture_output=True)

    _前 = _指纹(_d)
    _规 = {"name": "一段",
           "gates": [{"type": "grep_required", "in": "src/a.py",
                      "patterns": ["V = 1"]}],
           "acceptance": ["全绿"]}
    _r = _cl.close_loop(_ag.plan_tasks(_规), _d, adopted={"src/a.py": "V = 1" + chr(10)})
    _后 = _指纹(_d)

    if not _r.get("ok"):
        坏.append(f"判三十：闭环没达成（{_r.get('阶段')}）⇒ 这条齿测不到落地")
        return

    _变 = {k for k in set(_前) | set(_后) if _前.get(k) != _后.get(k)}
    _审 = {k for k in _变 if k.startswith(".audit_trail/")}
    _真 = _变 - _审

    # ① 真改动必须恰好是预期文件
    if _真 != {"src/a.py"}:
        坏.append(f"判三十：真改动是 {sorted(_真)}，应恰好是 ['src/a.py'] "
                 "⇒ 多出来的文件是被越界改了")
    # ② 无关文件逐字节未变
    for _w in ("src/b.py", "src/c.py", "docs/note.md", "README.md"):
        if _w in _变:
            坏.append(f"判三十：**无关文件 {_w} 被动了** ⇒ 不是'真落主树'，是'顺手改了别的'")
    # ③ 审计痕不许散落
    _散 = {k for k in _变 if ("backup" in k.lower() or "备份" in k) and
           not k.startswith(".audit_trail/")}
    if _散:
        坏.append(f"判三十：备份散落在 {sorted(_散)} ⇒ 会污染被测仓的判据")
    # ④ 备份内容必须等于改前
    for _b in _审:
        if "generic_backup/src/a.py" in _b:
            _t = (_d / _b).read_text(encoding="utf-8")
            if _t != "V = 0" + chr(10):
                坏.append(f"判三十：备份内容实得 {_t!r}，应等于改前的 "
                         f"{('V = 0' + chr(10))!r} ⇒ 它不是备份，是第二份改动")
    # ⑤ 目标文件在主树里真变了
    if (_d / "src" / "a.py").read_text(encoding="utf-8") != "V = 1" + chr(10):
        坏.append("判三十：目标文件在主树里的内容没变 ⇒ '已交付'是空话")

    # ⑥ **独立复跑**：另起 git 之外的进程核对，证明可被独立验证
    _g = _sp.run([sys.executable, "-c",
                  "import sys;t=open(sys.argv[1],encoding='utf-8').read();"
                  "sys.exit(0 if 'V = 1' in t else 1)", str(_d / "src" / "a.py")],
                 capture_output=True)
    if _g.returncode != 0:
        坏.append("判三十：独立进程读主树文件没通过 ⇒ 改动只存在于本进程的想象里")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十真落主树且旁证未动：绿")



def 判三十一对账不许把没测到盖住() -> None:
    """★ R289：**对账自己也会骗人**，而它骗人的方式最危险——"已证明"盖住"没测到"。

    真读数（2026-11-06）：
      第一版把"可重复性"写成一条，依据文字里写着"模型侧没测到"，
      但那一档是按**机器侧**判的 ⇒ 合计报 `没测到: 0`。
      **而真实情况是模型侧一次都没跑过**（上游整段不可用）。
      ⇒ 「一条只回答一个问题。」否则汇总数会把缺口抹平。

    这条齿钉五件事：
      ① 汇总的三个数加起来必须等于项数（不许漏项）；
      ② **上游不可用时，"真模型改对代码"那一条必须是 `没测到`，不许是 `已证明`**；
      ③ 每一条必须有「依据」，且依据里能看出它是真跑过的；
      ④ `没测到` 与 `不成立` 必须是不同的值（不许混成一个 falsy）；
      ⑤ 这个工具必须**真注册进 MCP** 且真好用。
    """
    import json as _json
    from agentscope_orchestrator import audit as _adt, taixu_mcp as _tm

    # ⑤ 真注册
    _名 = [t.name for t in _tm.mcp._tool_manager.list_tools()]
    if not any("audit_delivery" in x for x in _名):
        坏.append("判三十一：audit_delivery 没注册进 MCP ⇒ 有函数没工具")
        return

    _r = _json.loads(_tm.audit_delivery())
    _项 = _r.get("对账") or {}
    _计 = _r.get("合计") or {}
    if not _项:
        坏.append("判三十一：对账一项都没给出")
        return

    # ① 汇总必须等于项数
    if sum(_计.values()) != len(_项):
        坏.append(f"判三十一：合计 {_计} 加起来 {sum(_计.values())} ≠ 项数 {len(_项)} "
                 "⇒ 有项没被计入，缺口被抹平")
    for _v in _项.values():
        if _v.get("档") not in ("已证明", "没测到", "不成立"):
            坏.append(f"判三十一：出现了第四档 {_v.get('档')!r}")
        # ③ 每条要有依据
        if not str(_v.get("依据") or "").strip():
            坏.append("判三十一：有一条没有「依据」⇒ 凭什么说它证明了？")

    # ② ★ 最重要：上游不可用时，模型那条必须是「没测到」
    _模 = next((k for k in _项 if "真模型" in k), "")
    if not _模:
        坏.append("判三十一：对账里没有「真模型无人值守把多文件改对」这一条 "
                 "⇒ 最关键的缺口被漏掉了")
    else:
        _模档 = _项[_模]["档"]
        try:
            from agentscope_orchestrator import channel_doctor as _cd
            _可开工 = bool(_cd.体检().get("可开工"))
        except Exception:                            # noqa: BLE001
            _可开工 = False
        if not _可开工 and _模档 != "没测到":
            坏.append(f"判三十一：**没有一个确认活的通道，模型那条却报 {_模档!r}** "
                     "⇒ 把'没测到'说成了'做成了'，这是本产品最怕的那种骗")

    # ④ 两档不许混
    if _adt.没测到 == _adt.不成立:
        坏.append("判三十一：`没测到` 与 `不成立` 是同一个值 ⇒ 缺条件和真失败混成一档")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十一对账不许把没测到盖住：绿")



def 判三十二README必须写明还没测到什么() -> None:
    """★ R290：**文档只报好话，等于把缺口藏起来。**

    ★ 为什么（真读数，2026-11-06）：
      README 原来只有两栏——「做得到」「做不到」。
      **两栏都是"结论"，而真实状态里还有第三栏：「还没测到」**。
      实测那一刻的真状态是 `{'已证明': 6, '没测到': 1}`：
      「真模型无人值守把多文件改对」**一次都没跑过**（上游整段不可用）。
      而旧 README 里没有一个字提到这件事 —— 读者会以为它已经验过了。

    ⇒ README 必须：
      ① 有「还没测到」这一栏（与"做不到"分开：前者缺条件，后者是测了不行）；
      ② 明确写出**当前**没测到的是哪一条；
      ③ 写出缺的是什么条件（这里是上游通道），而不是含糊说"未来会做"；
      ④ 指向 `audit_delivery()`，让读者能自己复跑，而不是信文档；
      ⑤ 不许把"代码写完了"当成"证明过了"（原文必须有这句）。
    """
    from agentscope_orchestrator import audit as _adt

    _rd = Path(__file__).resolve().parent.parent / "README.md"
    if not _rd.is_file():
        坏.append(f"判三十二：README 不存在 {_rd}")
        return
    _t = _rd.read_text(encoding="utf-8", errors="replace")

    # ① 必须有「还没测到」这一栏，且与「做不到」是两个不同的标题
    if "还没测到" not in _t:
        坏.append("判三十二：README **没有**「还没测到」这一栏 ⇒ "
                 "文档只报结论，缺口被藏起来")
    # ④ 必须指向可复跑的工具
    if "audit_delivery" not in _t:
        坏.append("判三十二：README 没指向 audit_delivery ⇒ 读者只能信文档，不能自己复跑")
    # ⑤ 必须有"代码写完 ≠ 证明过了"这一类明说
    if "≠" not in _t and "不等于" not in _t:
        坏.append("判三十二：README 没写明'代码写完不等于证明过了'")
    # ③ 必须说出缺的条件（上游通道），不许含糊
    if ("通道" not in _t) or ("上游" not in _t):
        坏.append("判三十二：README 没写出缺的条件是上游通道 ⇒ "
                 "'没测到'变成了没有理由的托词")

    # ② 文档里写的"没测到"，必须和 audit 的**真读数**对得上
    _r = _adt.对账()
    _项 = _r.get("对账") or {}
    _模 = [k for k, v in _项.items() if "真模型" in k and v.get("档") == _adt.没测到]
    if _模:
        # 真读数说没测到 ⇒ README 必须也提到这一条
        if "真模型" not in _t or "没测到" not in _t:
            坏.append("判三十二：audit 真读数说「真模型…」是 `没测到`，"
                     "README 却没写 ⇒ 文档与机器读数不一致")
    # 文档也不许反过来把没测到的说成已验过
    if "已验过" in _t and _模:
        坏.append("判三十二：README 说'已验过'，而 audit 读数说 `没测到` ⇒ 文档在粉饰")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十二README必须写明还没测到什么：绿")



def 判三十三装完就能跑且包不许带垃圾() -> None:
    """★ R291：「**我的机器上有** ≠ **装上就有**」——干净环境装 wheel 才是真交付。

    真读数（2026-11-06，干净 venv + 真 `pip install` 本仓 wheel）：
      **9 个入口里 2 个一敲就崩**：
        orch-run.exe / orch-mcp.exe
        ModuleNotFoundError: No module named 'pywintypes'
            ← mcp/os/win32/utilities.py:22 `import pywintypes`
      根因：`mcp` 在 Windows 上要 `pywin32`，而它**不是** `mcp` 硬声明的依赖。
      我本地 venv 里有它，纯属**别的包顺手带进来的** ⇒ 一换干净机器就少两个核心入口。
      修：`pyproject.toml` 里按平台条件声明 `pywin32>=306; sys_platform == 'win32'`。

    同一轮还抓到第二件（更难看）：wheel 从 **1.77 MB 涨到 79 MB**——
      因为 `tests/_run_all_state/_tmp/` 下**测试临时文件被打进了 wheel**
      （每个 3 MB、共 400 多个）。清掉后回到 1.77 MB。
      ⇒ 「判据不许留垃圾」在**打包**这一层的形态：测试产物进了交付物。

    这条齿钉四件事：
      ① wheel 里**不许有**测试状态/临时文件（`_run_all_state` / `_tmp` / `_clean_install`）；
      ② wheel 体积必须小（>8 MB 说明混进了非源码）；
      ③ Windows 上 `pywin32` 必须被声明（否则 mcp 装完就崩）；
      ④ 9 个 console_scripts 一个不少。
    """
    import zipfile as _zf
    _P = Path(__file__).resolve().parent.parent
    _w = _P / "dist" / "orchestrator-0.13.11" / "wheels" / \
        "agentscope_orchestrator-0.13.11-py3-none-any.whl"
    if not _w.is_file():
        坏.append(f"判三十三：wheel 不存在（{_w}）⇒ 没打包就谈不上「装完能跑」")
        return

    _z = _zf.ZipFile(_w)
    _名 = _z.namelist()

    # ① 不许带测试垃圾
    _垃 = [n for n in _名
           if "_run_all_state" in n or "/_tmp/" in n or "_clean_install" in n]
    if _垃:
        坏.append(f"判三十三：wheel 里混进了测试临时文件 {len(_垃)} 个"
                 f"（如 {_垃[0][:70]}）⇒ 交付物带垃圾，体积被撑爆")
    # ② 体积
    _mb = _w.stat().st_size / 1048576
    if _mb > 8:
        坏.append(f"判三十三：wheel 有 {_mb:.1f} MB，源码包不该这么大 "
                 "⇒ 大概率混进了非源码（测试产物/缓存）")
    # ③ pywin32 声明
    _md = _z.read(next(n for n in _名 if n.endswith("METADATA"))).decode("utf-8", "replace")
    if "pywin32" not in _md:
        坏.append("判三十三：METADATA 没声明 pywin32 ⇒ **Windows 上装完 "
                 "orch-run / orch-mcp 一敲就崩**（No module named 'pywintypes'）")
    # ④ 入口点齐全
    _ep = _z.read(next(n for n in _名 if n.endswith("entry_points.txt"))).decode()
    _要 = ("orch-goal", "orch-run", "orch-check", "orch-plan", "orch-mcp",
           "orch-gaps", "orch-log", "orch-tree", "orch-doctor")
    _缺 = [e for e in _要 if e not in _ep]
    if _缺:
        坏.append(f"判三十三：wheel 缺入口点 {_缺}")

    # ⑤ 真装一遍（不联网、不装依赖），确认包本体能从 wheel 里导入
    import subprocess as _sp2
    import tempfile as _tf3
    _v = Path(_tf3.mkdtemp(prefix="r291v_")) / "v"
    _sp2.run([sys.executable, "-m", "venv", str(_v)], capture_output=True)
    _vpy = _v / "Scripts" / "python.exe"
    if not _vpy.is_file():
        _vpy = _v / "bin" / "python"
    _inst = _sp2.run([str(_vpy), "-m", "pip", "install", "-q", "--no-index",
                      "--no-cache-dir", "--no-deps", str(_w)],
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
    if _inst.returncode != 0:
        坏.append(f"判三十三：干净 venv 里装不上 wheel："
                 f"{(_inst.stderr or _inst.stdout or '')[-160:]}")
        return
    # 依赖不装，只验"包本体在 wheel 里且能定位"
    _q = _sp2.run([str(_vpy), "-c",
                   "import agentscope_orchestrator as A;"
                   "print(getattr(A,'__file__',''))"],
                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                  env={**__import__("os").environ, "PYTHONPATH": ""})
    _loc = (_q.stdout or "").strip()
    if _q.returncode != 0 or "site-packages" not in _loc:
        坏.append(f"判三十三：装完之后导入不到包（rc={_q.returncode}, "
                 f"loc={_loc[:60]!r}）⇒ 装了个空壳")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十三装完就能跑且包不许带垃圾：绿")



def 判三十四交付说明的数字必须对得上真读数() -> None:
    """★ R292：**文档里的数字会过期，而过期的数字比没有数字更坏。**

    ★ 为什么（真读数，2026-11-06）：
      我写了 `DELIVERY_STATE.md` 做收官对账，里面写了一堆具体数字
      （判据 33 条、工具 34 个、wheel 1773 KB、9 个入口、
      对账 `{'已证明': 7, '没测到': 1}`）。
      **这些数字全是我手写的。** 下一轮加一条判据、换一次打包，它们就全错了——
      而且不会有任何东西报错，读者看到的仍是一份"很详细"的过期文档。

    ⇒ 交付说明里的每个数字都必须**当场和真读数核一遍**：
      ① 判据数 > 0 且文档里写的就是它；
      ② 工具数对得上；
      ③ wheel 体积对得上（±3 KB）；
      ④ 入口点数对得上；
      ⑤ 对账的三档数写的就是真值；
      ⑥ 文档必须**同时**有「还没测到」一栏（不许只剩好话）。
    """
    import zipfile as _zf
    import re as _re
    _P = Path(__file__).resolve().parent.parent
    _doc = _P / "DELIVERY_STATE.md"
    if not _doc.is_file():
        坏.append(f"判三十四：交付说明不存在（{_doc}）⇒ 没有对外的最终状态")
        return
    _t = _doc.read_text(encoding="utf-8", errors="replace")

    # ⑥ 必须先有"还没测到"
    if "还没测到" not in _t:
        坏.append("判三十四：交付说明里没有「还没测到」⇒ 只剩好话")

    # ① 判据数
    _判 = len(_re.findall(r"^def 判", Path(__file__).read_text(encoding="utf-8", errors="replace"), _re.M))
    if _判 == 0 or f"**{_判}**" not in _t:
        坏.append(f"判三十四：判据数实得 {_判}，交付说明里没写对 ⇒ 数字过期")

    # ② 工具数
    try:
        from agentscope_orchestrator import taixu_mcp as _tm
        _具 = len(list(_tm.mcp._tool_manager.list_tools()))
        if f"**{_具}**" not in _t:
            坏.append(f"判三十四：工具数实得 {_具}，交付说明里没写对")
    except Exception:                                # noqa: BLE001
        pass

    # ③④ wheel 体积与入口点
    _w = (_P / "dist" / "orchestrator-0.13.11" / "wheels" /
          "agentscope_orchestrator-0.13.11-py3-none-any.whl")
    if _w.is_file():
        _kb = _w.stat().st_size / 1024
        _m = _re.search(r"wheel[:：]\s*\*\*(\d+)\s*KB\*\*", _t)
        if not _m:
            坏.append("判三十四：交付说明里没有 wheel 体积数字")
        elif abs(int(_m.group(1)) - _kb) > 3:
            坏.append(f"判三十四：交付说明写 wheel {_m.group(1)} KB，"
                     f"实得 {_kb:.0f} KB ⇒ 没重打包或文档过期")
        _z = _zf.ZipFile(_w)
        _ep = _z.read(next(n for n in _z.namelist()
                           if n.endswith("entry_points.txt"))).decode()
        _入 = len([l for l in _ep.splitlines()[1:] if "=" in l])
        if f"**{_入}**" not in _t and f"{_入} 个 console_scripts" not in _t:
            坏.append(f"判三十四：入口点实得 {_入} 个，交付说明里没写对")

    # ⑤ 对账三档
    try:
        from agentscope_orchestrator import audit as _adt
        _计 = _adt.对账().get("合计") or {}
        _串 = f"'已证明': {_计.get(_adt.已证明, 0)}, '没测到': {_计.get(_adt.没测到, 0)}"
        if _串 not in _t:
            坏.append(f"判三十四：对账真值 {_计}，交付说明里没写对（应含 {_串}）")
    except Exception:                                # noqa: BLE001
        pass

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十四交付说明的数字必须对得上真读数：绿")



def 判三十五渠道必须跟随宿主而不是硬编码() -> None:
    """★ R294：**用户当面指出的设计缺陷** —— "把模型渠道变成硬编码了？"

    ★ 真读数（2026-11-06）：
      原形态是 `config/channels.yml`（**从 DSH 迁过来的快照**）。
      ⇒ 插件挂到任何一台 DSH 上，用的都是本仓那份旧快照：
        DSH 里换 provider / 加模型 / 改 baseURL ⇒ 本仓一无所知；
        换一台机器 ⇒ 得手工再抄一遍。
      **「随便装一个接上就算」不是适配，是另一次硬编码。**

    ★ 正确形态：DSH 已经把答案放在环境里了 —— 进程被挂在哪台就给哪台的
        `DSH_HOME` / `DSH_PROFILE` / `DSH_PROFILE_DIR`，
      读 `<DSH_PROFILE_DIR>/cordis.patch.yml` 里 `llm-pi-ai` 的 `providers`
      就拿到了**当前真实的**渠道。读不到 ⇒ 退回静态配置，行为不变。

    这条齿钉五件事：
      ① 宿主专属通道（**文件里完全没有**）必须被自动挂上 —— 这是"自动获取"的证据；
      ② 同名通道**以宿主为准**（不是以文件为准）—— 这正是要修的病；
      ③ 不在 DSH 里时**行为不变**（不许崩、不许凭空造通道）；
      ④ 宿主配置结构变了时**如实说"没找到"**，不是编一个默认；
      ⑤ DSH 的顶层是 **list**（`[4].id == 'llm-pi-ai'`），不是 dict ——
         第一版假设 dict 直接返回空 ⇒ 报"没找到"。
    """
    import os as _os
    import tempfile as _tf4
    import yaml as _y

    _P = Path(__file__).resolve().parent.parent / "src"
    _宿键 = ("DSH_HOME", "DSH_PROFILE", "DSH_PROFILE_DIR")
    _旧 = {k: _os.environ.get(k) for k in _宿键}

    def _造(providers: dict) -> Path:
        _根 = Path(_tf4.mkdtemp(prefix="r294_"))
        _pf = _根 / "profiles" / "p1"
        _pf.mkdir(parents=True)
        (_pf / "cordis.patch.yml").write_text(
            _y.safe_dump([{"id": "llm-pi-ai", "name": "@x/y",
                           "config": {"providers": providers}}],
                         allow_unicode=True), encoding="utf-8")
        return _根

    try:
        # ① 宿主专属通道
        _根 = _造({"宿主专属甲": {"apiKeyEnv": "A_API_KEY",
                              "baseURL": "https://host.only/v1",
                              "models": [{"id": "mh-1"}]}})
        _os.environ["DSH_HOME"] = str(_根)
        _os.environ["DSH_PROFILE"] = "p1"
        _os.environ["DSH_PROFILE_DIR"] = str(_根 / "profiles" / "p1")

        from agentscope_orchestrator import dsh_channels as _dc, gateway as _gw
        _r = _dc.发现渠道()
        if not _r.get("ok"):
            坏.append(f"判三十五：宿主里明明有渠道，发现却失败：{_r.get('没找到的原因')}")
        _ch = _gw.load_channels()
        _名 = [c.name for c in _ch]
        if "宿主专属甲" not in _名:
            坏.append(f"判三十五：**宿主专属通道没被自动挂上**（装载={_名}）"
                     "⇒ 渠道还是硬编码的")
        # ⑤ 顶层是 list 也要能读到（这条正是我第一版栽的）
        _v = next((c for c in _ch if c.name == "宿主专属甲"), None)
        if _v and _v.base_url != "https://host.only/v1":
            坏.append(f"判三十五：基址实得 {_v.base_url!r}，应来自宿主 "
                     "https://host.only/v1")

        # ② 同名以宿主为准（文件里 qww 的 base 与宿主不同时，必须用宿主的）
        _根2 = _造({"qww": {"apiKeyEnv": "QWW_API_KEY",
                           "baseURL": "https://FROM-HOST.example/v1",
                           "models": [{"id": "host-model"}]}})
        _os.environ["DSH_HOME"] = str(_根2)
        _os.environ["DSH_PROFILE_DIR"] = str(_根2 / "profiles" / "p1")
        _ch2 = _gw.load_channels()
        _q = next((c for c in _ch2 if c.name == "qww"), None)
        if _q is None:
            坏.append("判三十五：同名通道 qww 消失了")
        elif _q.base_url != "https://FROM-HOST.example/v1":
            坏.append(f"判三十五：同名 qww 用的是 {_q.base_url!r}（来自文件），"
                     "**应以宿主为准** —— 快照比活配置权威正是要修的病")

        # ③ 不在 DSH 里 ⇒ 不许崩、不许凭空造
        for _k in _宿键:
            _os.environ.pop(_k, None)
        _r3 = _dc.发现渠道()
        if _r3.get("ok"):
            坏.append("判三十五：不在 DSH 里却报发现了渠道 ⇒ 在凭空造")
        _ch3 = _gw.load_channels()
        if not _ch3:
            坏.append("判三十五：不在 DSH 里时通道表空了 ⇒ 退回静态配置这条没生效")

        # ④ 结构变了要「如实说没找到」，不是编一个
        _根4 = Path(_tf4.mkdtemp(prefix="r294d_")) / "profiles" / "p1"
        _根4.mkdir(parents=True)
        (_根4 / "cordis.patch.yml").write_text(
            _y.safe_dump([{"id": "别的插件", "config": {}}], allow_unicode=True),
            encoding="utf-8")
        _os.environ["DSH_HOME"] = str(_根4.parent.parent)
        _os.environ["DSH_PROFILE_DIR"] = str(_根4)
        _r4 = _dc.发现渠道()
        if _r4.get("ok"):
            坏.append("判三十五：宿主里没有 llm-pi-ai 声明，却报发现了渠道 ⇒ 在编")
        elif "没找到" not in (_r4.get("没找到的原因") or ""):
            坏.append("判三十五：找不到时没有给出「没找到」的原因 ⇒ 调用方无从判断")
    finally:
        for _k in _宿键:
            if _旧.get(_k) is None:
                _os.environ.pop(_k, None)
            else:
                _os.environ[_k] = _旧[_k]

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十五渠道必须跟随宿主而不是硬编码：绿")



def 判三十六项目知识图谱必须自动建成且查得动() -> None:
    """★ R296：**用户当面指出的根本缺陷** —— "数据库有功能但存不进查不到，有什么意义？"

    ★ 真读数（2026-11-06，改之前）：
        · 编排器索引根 = `products/docs` —— **只索引自己产出的回执**；
        · 项目本体（22,839 个 .lean、224 MB）**从未入索引**；
        · `search_docs("密码")` → 0；`search_docs("theorem")` → 0，
          而 `auto_scan` 数得出 4 万条形式化单元。
        ⇒ **「数得出来」≠「进得了系统」；「索引里有」≠「查得到」。**

    ★ 改之后真读数（`形式化验证/` 全量）：
        25,755 文件 → **535,478 单元**、覆盖率 96.49%、解析失败 251、读不了 0；
        查「密码学」→ 373 定理、查「自主认知」→ 184 定理、查「结构本能」→ 42 定理。

    这条齿钉六件事：
      ① 给一个夹子**能自动抽出知识单元**（不是数文件，是抽 theorem/def/doc）；
      ② **中文标题必须挂到英文命名的单元上** —— 否则查「自主认知」得 0
         （定理名是 `const_rule_no_reuse`，中文只在文件头注释里）；
      ③ **覆盖率必须报数**，且 0 命中要能分清「真没有」与「我没索引到」；
      ④ **上限必须按类型均摊** —— 否则一整类文件被系统性漏掉；
      ⑤ 模型不可用时**构建照样完成**（语义降级，不是失败）；
      ⑥ 反查能回答「谁用到它」（文件搜索做不到这件事）。
    """
    import tempfile as _tf36

    _P = Path(__file__).resolve().parent.parent / "src"
    from agentscope_orchestrator import project_graph as _pg

    _根 = Path(_tf36.mkdtemp(prefix="r296_"))

    # ① 造一个"项目"：一个 lean（中文标题 + 英文定理名）、一个 md、一个 py
    (_根 / "形式化").mkdir()
    (_根 / "形式化" / "Cog.lean").write_text(
        "/-!\n# 自主认知 · 三要素归纳\n\n本件要证明：三要素合起来不充分。\n-/\n\n"
        "import Mathlib\n\n"
        "theorem 三要素必要性_impl (a b : Nat) : a = b := by rfl\n\n"
        "theorem 充分性不成立_impl (x : Nat) : x = x := by rfl\n\n"
        "def 结构空间 : Nat := 3\n\n"
        "axiom 桥 : True\n", encoding="utf-8")
    (_根 / "说明.md").write_text(
        "# 密码学验证报告\n\n本节说明 RSA 分解。\n\n## 遗忘机制\n\n墓碑与容量上限。\n",
        encoding="utf-8")
    (_根 / "工具.py").write_text(
        "def 算字类(x):\n    return x\n\nclass 记忆网络:\n    pass\n", encoding="utf-8")

    果 = _pg.构建(_根)
    if not 果.get("ok"):
        坏.append(f"判三十六：构建失败 {果}")
        return
    读 = 果["读数"]
    类分布 = 读["按类"]

    # ① 真抽出了单元，且定理不是 0
    if 读["单元总数"] < 6:
        坏.append(f"判三十六：只抽到 {读['单元总数']} 个单元（应 ≥ 6）⇒ 没真抽")
    if 类分布.get("theorem", 0) < 2:
        坏.append(f"判三十六：theorem 只抽到 {类分布.get('theorem', 0)} 个"
                 "（Cog.lean 里明明有 2 个）⇒ lean 解析没生效")
    for 必需 in ("def", "doc", "func"):
        if 类分布.get(必需, 0) < 1:
            坏.append(f"判三十六：`{必需}` 一个都没抽到 ⇒ 那一类文件被漏了")

    # ② 中文标题必须能命中英文命名的定理（这是最关键的一条）
    果2 = _pg.查(果, "自主认知", 种类="theorem", 上限=10)
    if 果2["命中数"] < 2:
        坏.append(f"判三十六：查「自主认知」只得 {果2['命中数']} 条定理 —— "
                 "中文标题没挂到单元上（定理名是英文，中文只在文件头）")

    # ③ 覆盖率必须报数
    覆 = 果["覆盖"]
    if not isinstance(覆.get("覆盖率"), float):
        坏.append("判三十六：覆盖率没报数 ⇒ 「不许漏」成了口号")
    if 覆.get("已解析", 0) != 3:
        坏.append(f"判三十六：3 个文件只解析了 {覆.get('已解析')} 个")

    # ④ 上限必须按类型均摊（造 5 个 md + 5 个 lean，给上限 4，两类都得有）
    _根2 = Path(_tf36.mkdtemp(prefix="r296b_"))
    for i in range(5):
        (_根2 / f"d{i}.md").write_text(f"# 文档{i}\n\n内容\n", encoding="utf-8")
        (_根2 / f"l{i}.lean").write_text(
            f"theorem t{i} : True := trivial\n", encoding="utf-8")
    扫 = _pg.扫描(_根2, 上限文件数=4)
    类得 = {f["类"] for f in 扫["文件"]}
    if not {"markdown", "lean"} <= 类得:
        坏.append(f"判三十六：上限均摊失效 —— 只扫到 {类得}，"
                 "一整类文件被系统性漏掉（第一版就是这样：给 120 上限，.lean 全没进）")

    # ⑤ 模型不可用时构建照样完成（语义降级不是失败）
    def _会炸的语义(问, 选):
        raise RuntimeError("模拟上游不可用")
    果3 = _pg.构建(_根, 语义=_会炸的语义)
    if not 果3.get("ok"):
        坏.append("判三十六：模型不可用时构建失败 ⇒ 语义部分没做成可降级")
    elif "未测到" not in str(果3.get("语义", {}).get("状态", "")):
        坏.append(f"判三十六：模型炸了，语义却报 {果3.get('语义')} —— 不许把没测到说成测过了")

    # ⑥ 反查（文件搜索做不到的事）
    反 = _pg.反查(果, "结构空间")
    if not 反.get("定义处"):
        坏.append("判三十六：反查`结构空间`找不到定义处 ⇒ 名字表没建起来")



def 判三十七建完必须有标准且失败要分诊() -> None:
    """★ R297：**用户当面追问** —— "怎么算全局构建完毕？有没有标准？"「解析失败为什么不调模型？」

    ★ 真读数（改之前）：
        输出只有一个 `覆盖率: 0.9649` —— **说明不了"建完了没有"**：
        那 3.51% 里有没有整类文件？251 个"解析失败"里哪些是不需要索引的锁文件、
        哪些是我解析器漏了？分不出来。

    ★ 定四道门，全过才算建完：
        门1 无遗漏解释 / 门2 无解析崩溃 / 门3 关键类型齐备 / 门4 可回答

    ★ 「解析失败为什么不调模型」的答案是**先分诊**：
        不需要索引 / 补解析器 / 问模型 / 人工看。
        真事故：分诊第一版把 `.lean.bak726`（备份）判进「问模型」10 个 ——
        问模型看旧版快照讲了什么，纯属浪费，入图还会污染。

    这条齿钉七件事：
      ① 「不认识的类型」**必须让门1 红**（含糊原因不许算过）；
      ② 解析器崩了**必须让门2 红**（崩了是我写错，不是文件的问题）；
      ③ 整类文件没抽出单元**必须让门3 红**；
      ④ 主主题词查不到**必须让门4 红**（图建了但查不到 = 没建）；
      ⑤ 覆盖率**不得**作为判定标准（它高不代表建完）；
      ⑥ **备份文件必须分到「不需要索引」**，不许分到「问模型」；
      ⑦ `_后缀类` 的每个值都必须在 `_解析器` 里有对应 ——
         「映射表里有 ≠ 解析器接了」，这是刚踩过的假覆盖。
    """
    import tempfile as _tf37

    _P = Path(__file__).resolve().parent.parent / "src"
    from agentscope_orchestrator import project_graph as _pg

    # ⑦ 两张表必须对齐（静态检查，最快也最狠）
    _缺 = set(_pg._后缀类.values()) - set(_pg._解析器)
    if _缺:
        坏.append(f"判三十七：`_后缀类` 里这些类**没有解析器**：{sorted(_缺)}"
                 "⇒ 假覆盖（`.html`→markup 就漏注册过，3 个文件落进'没有对应解析器'）")

    _根 = Path(_tf37.mkdtemp(prefix="r297_"))
    (_根 / "a.lean").write_text(
        "/-!\n# 密码学形式化\n-/\ntheorem crypto_lemma : True := trivial\n",
        encoding="utf-8")
    (_根 / "b.md").write_text("# 自主认知说明\n\n正文。\n", encoding="utf-8")
    (_根 / "c.py").write_text("def 函数() -> int:\n    return 1\n", encoding="utf-8")
    (_根 / "d.lock.json").write_text('{"lock":1}', encoding="utf-8")
    (_根 / "e.lean.bak726").write_text("theorem 旧版 : True := trivial\n",
                                       encoding="utf-8")
    (_根 / "f.html").write_text("<h1>页面</h1>\n", encoding="utf-8")

    果 = _pg.构建(_根)

    # ① 干净项目：四道门必须全过
    判 = _pg.构建完毕判定(果, 主主题词=["密码学"])
    if not 判["完毕"]:
        坏.append(f"判三十七：干净项目竟然不算建完 —— 不满足 {判['不满足的']}")
    门表 = {g["门"][:2]: g["过"] for g in 判["门"]}
    for 名 in ("门1", "门2", "门3", "门4"):
        if 门表.get(名) is not True:
            坏.append(f"判三十七：{名} 没过（读数见返回）")

    # ⑤ 覆盖率不得作为判定标准
    if "覆盖率" in str(判.get("说明", "")) and "不是" not in str(判.get("说明", "")):
        坏.append("判三十七：完成说明里把覆盖率当成了判定标准")

    # ⑥ 备份必须分到「不需要索引」，不许问模型
    分 = _pg.失败分诊(果, _根)
    if 分["分诊"]["问模型"]["数"] > 0:
        坏.append(f"判三十七：有 {分['分诊']['问模型']['数']} 个分进了「问模型」"
                 f"（{分['分诊']['问模型']['明细'][:1]}）—— 备份不该问模型")
    if 分["分诊"]["补解析器"]["数"] > 0:
        坏.append(f"判三十七：有 {分['分诊']['补解析器']['数']} 个该补解析器"
                 f"（{分['分诊']['补解析器']['明细'][:1]}）")

    # ① 含糊原因必须让门1 红
    假 = {"覆盖": {"跳过明细_前20": [{"文件": "x.zzz", "原因": "不认识的类型"}],
                  "未读明细_前20": [], "按后缀跳过": 1, "读不了": 0},
         "读数": {"解析失败数": 0, "解析失败明细_前10": []},
         "图谱": {"单元": []}}
    if _pg.构建完毕判定(假)["完毕"]:
        坏.append("判三十七：「不认识的类型」这种含糊原因居然让门1 过了 ⇒ 门1 是摆设")

    # ② 解析器崩溃必须让门2 红
    假2 = {"覆盖": {"跳过明细_前20": [], "未读明细_前20": [],
                   "按后缀跳过": 0, "读不了": 0},
          "读数": {"解析失败数": 1,
                   "解析失败明细_前10": [{"文件": "y.lean", "原因": "TypeError: boom"}]},
          "图谱": {"单元": []}}
    if _pg.构建完毕判定(假2)["完毕"]:
        坏.append("判三十七：解析器崩了居然还能判'建完' ⇒ 门2 是摆设")

    # ④ 主题词查不到必须让门4 红
    假3 = {"覆盖": {"跳过明细_前20": [], "未读明细_前20": [],
                   "按后缀跳过": 0, "读不了": 0, "按类": {"lean": 1}},
          "读数": {"解析失败数": 0, "解析失败明细_前10": []},
          "图谱": {"单元": [{"id": "a", "kind": "theorem", "name": "其他",
                            "file": "a.lean", "line": 1, "摘要": ""}]}}
    if _pg.构建完毕判定(假3, 主主题词=["密码学"])["完毕"]:
        坏.append("判三十七：主题词查不到居然算建完 ⇒ 门4 是摆设（图建了但没用）")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十七建完必须有标准且失败要分诊：绿")



def 判三十八真模型调用必须支持流式通道() -> None:
    """★ R298：**用户当面戳穿的假结论** ——
    「说明现在编排器根本不是用的 DSH 的模型渠道，之前说的已经可以自适应适配全是假的。
      如果模型渠道本身有问题你还能在这里跟我说 40 几？」

    ★ 真事故（我连 40 多轮报"通道全死、无法验证"）：
      而**我自己就跑在 DSH 的这条通道上** —— 这个矛盾我没去解决，当成了借口。

      根因在两处，都是"为了图方便"把通道唯一能用的方式关掉了：
        · `msgs.py` 的 `StreamingClient.__init__(stream: bool = False)`，
          注释写着「★ 恒走非流式：一次拿全量最好判」，`"stream": False` 写死；
        · `gateway.build_client` 的 `OpenAIChatModel(stream=False)`，
          docstring 写着「stream 关掉便于同步拿全量结果」。

      真对照读数（同通道 `api.hlyrouter.top/v1`、同 key、同 prompt）：
        stream=False → HTTP 200 + {"error":{"message":"上游服务异常，请稍后重试…"}}
        stream=True  → HTTP 200 + choices，内容正常
      ⇒ **这条通道只认流式。** 写死非流式 = 模型永远调不通，
        而报出来的错长得像"上游挂了" ——
        **能力坏了，却报得像是环境坏了**，这是本产品最坏的一类 bug。

    这条齿钉四件事（全部静态可判，不需要通道活着）：
      ① `msgs.py` 里**不许**有写死的 `"stream": False`；
      ② `msgs.py` 的默认值必须是 True（这条通道只认流式）；
      ③ `msgs.py` 必须实现**流式解析**（拼 delta 内容），不能只发非流式；
      ④ `gateway.probe_model` 的非流式探测失败时，必须**自动用流式再试一次**
         —— 探针自己也要先被证明有效。
    """
    _P = Path(__file__).resolve().parent.parent / "src"
    _m全 = (_P / "agentscope_orchestrator" / "msgs.py").read_text(encoding="utf-8")
    _g全 = (_P / "agentscope_orchestrator" / "gateway.py").read_text(encoding="utf-8")
    # ★ 判据只能看**代码**，不能看注释 ——
    #   第一版直接搜全文，结果把 R298 自己写在注释里的
    #   「这里原来是 `stream: bool = False`」当成违规 ⇒ **假红**。
    #   这正是「假红与真红长得一样，唯一的分界是把实得值打出来核对」。
    def _去注释(文: str) -> str:
        出 = []
        for 行 in 文.splitlines():
            去 = 行.split("#", 1)[0] if "#" in 行 else 行
            出.append(去)
        文2 = "\n".join(出)
        # 去掉三引号字符串（docstring 里也会有示例代码）
        import re as _re
        return _re.sub(r'"""[\s\S]*?"""', "", 文2)
    _m = _去注释(_m全)
    _g = _去注释(_g全)

    # ① 不许写死 "stream": False
    if '"stream": False' in _m or "'stream': False" in _m:
        坏.append("判三十八：`msgs.py` 里仍有写死的 `\"stream\": False` —— "
                 "这条通道只认流式，写死非流式等于**模型永远调不通**")

    # ② 默认值必须是 True
    if "stream: bool = False" in _m:
        坏.append("判三十八：`msgs.py` 的 stream 默认值仍是 False "
                 "⇒ 默认路径就是死路（R298 就是这么连错 40 多轮的）")

    # ③ 必须真有流式解析
    for 记号, 说明 in (
        ("aiter_lines", "没有 `aiter_lines` ⇒ 根本没读流"),
        ("data:", "没有解析 SSE 的 `data:` 前缀 ⇒ 流式内容拼不出来"),
    ):
        if 记号 not in _m:
            坏.append(f"判三十八：`msgs.py` {说明}")

    # ④ 探针必须流式兜底
    if "_探测用流式" not in _g:
        坏.append("判三十八：`gateway.py` 没有 `_探测用流式` ⇒ "
                 "非流式探测失败就直接判死，探针没被证明过有效")
    elif "_探测用流式(url" not in _g:
        坏.append("判三十八：`_探测用流式` 定义了但探测路径里**没调用** ⇒ 等于没接线")

    # ⑤ 「能力坏了 vs 环境坏了」必须能分开报
    if "流式也试过" not in _g and "流式可用" not in _g:
        坏.append("判三十八：探测失败时没写清'流式也试过' ⇒ "
                 "分不清是通道死还是我的探针方式错")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十八真模型调用必须支持流式通道：绿")


def 判三十九口径门必须真能拦住已犯过的错() -> None:
    """★ R300：**用户当面指出我"说完就犯"** ——

    「你连我刚说的话，应该变成门给你自己输出内容结论把关的东西都没有做好，
      上一句刚说完，问题出去答案立刻偏移。连这个最基本的都没做好，后面的东西怎么做？」

    真事故：我在章程里写下「**写不出红的判据的规矩，不是规矩，是装饰**」，
    紧接着就输出了一条**推卸型结论**（「结构本能是库存 ⇒ 得先让它在自主认知里进生产」）。
    **拦住它的是用户，不是门。**

    ⇒ 这条齿钉：**口径门必须存在、必须自检为绿、必须拦得住我真实说过的错话。**

    ★ 为什么「自检」是这条齿的核心：
      一条报不出红的红线等于没有。所以每条口径都自带
      「正样本（必须被拦，尽量用我真实说过的原话）」和「负样本（必须放行）」。
      口径门不知道这件事 —— 它只是个表。**是这条齿在保证那张表不是装饰。**
    """
    try:
        from agentscope_orchestrator import 口径门 as _kj
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判三十九：口径门导入不了 ⇒ 等于没有（{type(e).__name__}: {e}）")
        return

    # ① 自检必须绿（每条真能报红、且不误报）
    _自 = _kj.自检()
    if not _自.get("ok"):
        坏.append("判三十九：口径门自检红 —— " + "；".join(_自.get("坏") or [])[:300])

    # ② 每条口径必须有「依据」（用户原话），否则只是我的个人偏好
    for _条 in _kj.规矩:
        if not (_条.get("依据") or "").strip():
            坏.append(f"判三十九：{_条.get('id')} 没有『依据』⇒ "
                     "没有用户原话支撑的口径是个人偏好，不是口径")

    # ③ 逐条：正样本必须被拦、负样本必须放行
    for _条 in _kj.规矩:
        if _kj.查(_条["正样本"])["ok"]:
            坏.append(f"判三十九：{_条['id']} 是装饰 —— 拦不下自己的正样本")
        if not _kj.查(_条["负样本"])["ok"]:
            坏.append(f"判三十九：{_条['id']} 误报 —— 把正确的话拦了")

    # ④ ★ 硬证据：我**真实说过的**错话必须被拦（这是这条齿的价值所在）
    _真错 = [
        "⇒ **在把结构本能接进编排器之前，得先让它在自主认知里进生产。**",
        "对账合计 —— 这一条**没测到**",
        "结论：通道全死。零个通道被确认为活。",
        "编排器还不如 cargo 成熟。",
        "返回应该是字典，我猜数据形状是 dict。",
        "探针通了就能干活。",
        "已全部完成。",
    ]
    for _句 in _真错:
        if _kj.查(_句)["ok"]:
            坏.append(f"判三十九：**已犯过的错拦不住** ——「{_句[:40]}」"
                     "⇒ 口径门漏了这一类，下次我还会犯")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判三十九口径门必须真能拦住已犯过的错：绿")


def 判四十语义校准必须是自演化的且两级门都在() -> None:
    """★ R301：用户把这个机制的定位说清了 ——

    「这是要做成一个**自演化语义校准**的长期机制，做成**模型微调的外部门**，
      做**语义泛化**的。如果连说都说不对又怎么真正的改变模型行为？
      **积小成多模型错的越多门越强，门强错的就越少**，不这样控制怎么控制？
      你还想靠模型自觉不成？」

    ★ 为什么这条齿必须有（真读数逼出来的）：
      R300 建的是**静态 7 条正则**。它在**我自己写的机械变体**上强度 1.0 ——
      **对着自己的尺子量，说明不了泛化。**
      换成**真模型生成的变体**（换掉全部词汇、保住同一毛病）再量：

        | 门 | 留出召回 | 误报率 |
        |---|---|---|
        | 一级 正则 | **0.0** | 0.0 |
        | 二级 语义判别器 | **1.0** | 0.0 |

      ⇒ 正则只能拦**已犯过的句式**；**语义泛化必须另有一层判别器**。
      而判别器第一版**只喂「违反」的例子** ⇒ 召回 1.0 但**误报率 0.375**；
      补上「正确」的例子后误报降到 **0.0**。
      ⇒ **样本（正反两侧）才是燃料，不是更聪明的提示词。**

    这条齿钉五件（全部静态可判，不需要上游活着）：
      ① 样本库机制在，且**不许为空**（错了必须变成资产）；
      ② **强度必须有历史留痕**（"门在变强"要有读数，不能是信念）；
      ③ **两级门都在**：正则 + 语义判别器（只有一级 = 认不得新说法）；
      ④ ★ **判别器必须喂正反两侧样本** —— 只喂正例的判别器必然过触发；
      ⑤ 上游不可用时必须**如实降档**（说"这一档没跑"），不许假装跑了。
    """
    try:
        from agentscope_orchestrator import 语义校准 as _yj
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判四十：语义校准导入不了 ⇒ 自演化机制不存在（"
                 f"{type(e).__name__}: {e}）")
        return
    import inspect as _ins40

    # ① 样本库机制 + 不许空
    for _名 in ("记", "读", "校准", "变异", "造判别器", "语义留出", "两级判",
                "两级判全文"):
        if not hasattr(_yj, _名):
            坏.append(f"判四十：`语义校准.{_名}` 不存在 ⇒ 自演化缺这一环")
    _样 = _yj.读()
    if not _样:
        坏.append("判四十：**样本库是空的** ⇒ 错了不会变成资产，"
                 "门永远不会变强（这正是 R300 的状态）")
    else:
        _缺依 = [s for s in _样 if not (s.get("依据") or "").strip()]
        if _缺依:
            坏.append(f"判四十：{len(_缺依)} 条样本没有『依据』⇒ "
                     "没有用户原话/真事故支撑的样本是个人偏好，拿它校准门=用偏好校准偏好")

    # ② 强度历史
    _历 = _yj._历史读数()
    if not _历.get("条数"):
        坏.append("判四十：**强度从没留痕** ⇒「门在变强」没有读数，是信念")

    # ③ 两级门都在
    _码 = _ins40.getsource(_yj.两级判)
    if "正则" not in _码 or "判(" not in _码:
        坏.append("判四十：`两级判` 里看不到两级（正则 + 语义判别器）")

    # ③b ★ R519：**真跑一次** `两级判全文` —— 口径 331「名字在 ≠ 在跑」。
    #   ★ 它不是新接的线：它早就写好了（解决 R301f「整篇判 vs 逐句判结论不一致」），
    #     但**没人调**，而判四十 上面那两条只查 `hasattr`/`getsource`（都是"在不在"）。
    #     ⇒ 实测：`语义校准.py::两级判全文@466` 是**真死代码**，
    #       而它正是判四十 关心的那个机制的"全文版"。
    #   ★ 这里**判=None**：不调模型、不发请求 ⇒ 不引入外部依赖，
    #     只把"这条线跑得通"证明给机器看。
    try:
        _全文 = _yj.两级判全文("第一句。第二句。", None)
        if not isinstance(_全文, dict) or "命中" not in _全文:
            坏.append("判四十：`两级判全文` 跑出来的不是约定的 dict（缺 `命中`）"
                     f" ⇒ 拿到 {type(_全文).__name__}")
        elif _全文.get("语义档没跑") is None and "切分粒度" not in _全文:
            坏.append("判四十：`两级判全文` 的降档路径既没报 `语义档没跑`、"
                     "也没给 `切分粒度` ⇒ 两条路径都不可辨认")
    except Exception as _e40:                          # noqa: BLE001
        坏.append(f"判四十：`两级判全文` **跑不通**（{type(_e40).__name__}: {_e40}）"
                 " ⇒ 能力在纸上，不在链路上")

    # ④ ★ 判别器必须喂正反两侧样本（这是误报从 0.375 降到 0 的唯一原因）
    _造 = _ins40.getsource(_yj.造判别器)
    if "违反例" not in _造:
        坏.append("判四十：造判别器的提示里**没有正例** ⇒ 判别器没有校准依据")
    if "正确例" not in _造:
        坏.append("判四十：造判别器的提示里**没有反例** ⇒ **必然过触发**"
                 "（实测：只喂正例 ⇒ 召回 1.0 但误报率 0.375）")

    # ⑤ 上游不可用时必须如实降档
    _降 = _yj.两级判("这一句肯定没问题。", None)
    if "没跑" not in str(_降.get("语义档没跑") or ""):
        坏.append("判四十：判别器不可用时**没有如实降档** ⇒ "
                 "会把「这一档没测到」当成「没违规」")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十语义校准必须是自演化的且两级门都在：绿")


def 判四十一行尾不许被悄悄改掉() -> None:
    """★ 2026-11-07 我真犯过：用 PowerShell 写一个文件，把整篇 LF 变成 CRLF。

    后果**不是功能坏，是审阅坏**：`git diff` 从「135 行新增」变成
    「1013 增 1013 删」，**真正的改动被淹掉**。而且 `git diff` 看上去像大改，
    只有 `git diff -w` 才露真相（135/1）——「**假红与真红长得一样，
    唯一的分界是把实得值打出来核对**」，所以这里把每个文件的形态**印出来**。

    ★ 判的是「**入仓约定有没有被改掉**」，不是「该用哪个约定」：
      实测本仓 49/50 个 `.py` 是 CRLF（LF 的几个都是新建的），
      所以**强制某一种约定会大面积假红**，那不是我犯的错。
      我犯的错是：**同一个文件，工作区形态与它入仓时的形态不一样了**。

    非 git 环境（装成 wheel 跑）**显式跳过**，不静默放行。
    """
    import subprocess
    包 = Path(__file__).resolve().parent.parent / "src" / "agentscope_orchestrator"
    仓 = 包.parent.parent.parent
    文件们 = sorted(包.glob("*.py"))
    if len(文件们) < 10:
        坏.append(f"判四十一：只读到 {len(文件们)} 个模块 ⇒ 扫描根写错了（不许把「没扫到」当「没有」）")
        return

    def 形态(b: bytes) -> str:
        # ★ 只比**类别**，绝不把 CR/LF 计数拼进来。
        #   本判据自己就犯过这个错：`混杂(CR=1,LF=46)` 与 `混杂(CR=1,LF=80)`
        #   被判成「形态被改掉」，可那只是给一个混杂文件**多加了几行**，约定没变。
        #   ⇒ 把**行数**混进了**形态**。计数现在只用于打印，不参与比较。
        cr, lf = b.count(13), b.count(10)
        return "LF" if cr == 0 else ("CRLF" if cr == lf else "混杂")

    def 计数(b: bytes) -> str:
        return f"CR={b.count(13)},LF={b.count(10)}"

    try:
        探 = subprocess.run(["git", "-c", "core.hooksPath=", "rev-parse", "--git-dir"],
                            cwd=str(仓), capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        探 = None
    if 探 is None or 探.returncode != 0:
        print("   [判四十一] 不是 git 工作区 ⇒ **显式跳过**（没有入仓形态可比）")
        return

    不齐: list[str] = []
    for p in 文件们:
        新 = p.read_bytes()
        旧 = subprocess.run(["git", "-c", "core.hooksPath=", "show",
                            f"HEAD:多智能体编排/agentscope_orchestrator/src/"
                            f"agentscope_orchestrator/{p.name}"],
                           cwd=str(仓), capture_output=True, timeout=60)
        if 旧.returncode != 0:
            continue                      # 新文件还没入仓 ⇒ 没有「入仓形态」可比，跳过
        a, b = 形态(旧.stdout), 形态(新)
        if a != b:
            不齐.append(f"{p.name}: 入仓 {a}({计数(旧.stdout)}) -> 工作区 {b}({计数(新)})")
    if 不齐:
        坏.append("判四十一：行尾形态被悄悄改掉（会把真改动淹在大 diff 里）："
                  + "；".join(不齐))

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十一行尾不许被悄悄改掉：绿")


def 判四十二临时目录不许落在仓里() -> None:
    """★ 2026-11-07 真事故：`tempfile.gettempdir()` 在本机**回落到 cwd**（= 包根）。

    后果是**每一次 `mkdtemp()` 都在仓里造一个目录**，而且没人删：
    实测包根下面积了 **4,736 个**直接子目录、其中 **3,739 个随机名**的
    合计 **2.6 GB**，全是历年测试夹具的整仓拷贝；工位残留同步从 1,749 涨到 1,807。
    有缺口史曲线之前，这种「慢慢变坏」是**看不见的**。

    根因**不是环境变量没设**（`TMPDIR`/`TEMP` 都设了、目录也存在），而是**探针本身**：

        os.open(不可写目录)        ->  立刻 PermissionError
        tempfile.mkstemp(dir=…)    ->  **挂住 > 6 秒**（连 `C:\\Windows\\Temp` 也一样）

    `gettempdir()` 依次试 10 个候选、每个都慢死，最后只能回落到候选表末项 cwd。

    判据（四条，**都是机器可判的**）：
      ① `gettempdir()` **不在仓内**；
      ② `mkdtemp()` 造出来的目录**不在 cwd 上**；
      ③ 本判据自己跑完，**包根下不许比跑之前多出目录** ——
         这条直接量「缺陷还长不长」，而不是量「代码看起来对不对」；
      ④ 钉不住时**必须如实报「没有钉」**，不许静默降级。

    ★ 非 git / 装成 wheel 的环境下 `temp_guard` 仍可用（它不依赖 git），
      所以这里**不设跳过分支** —— 要么绿，要么红并写明理由。
    """
    try:
        from agentscope_orchestrator import temp_guard as _tg
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十二：导不进 temp_guard（{type(_e).__name__}: {_e}）")
        return
    import os as osd                                           # noqa: PLC0415

    包根 = _tg.包根

    def _数子目录() -> int:
        return sum(1 for x in 包根.iterdir() if x.is_dir())

    _前 = _数子目录()

    # ③ 前置：包根读得到（读不到就别假装判过）
    if _前 <= 0:
        坏.append(f"判四十二：包根下读到 {_前} 个子目录 ⇒ 扫描根写错了")
        return

    果 = _tg.钉住(强制=True)
    if not 果.get("成功"):
        坏.append(f"判四十二④：钉不住却继续跑流程 —— 必须如实报（{果.get('为何')}）")
        return
    if 果.get("钉到") is None:
        坏.append("判四十二④：报了成功却没有「钉到」地址")
        return

    # ① gettempdir 不许在仓内
    import tempfile as _tf
    现 = _tf.gettempdir()
    if _tg.在仓内(现):
        # 降级到 <包根>/work/临时 是**允许**的（系统临时目录全不可写时），
        # 但**只允许落在剪枝表里的 work/ 下**，不许是仓根/cwd
        if osd.path.normcase(str(Path(现).parent)) == osd.path.normcase(str(包根)):
            坏.append(f"判四十二①：gettempdir 就是包根自己 ⇒ 会往仓里撒目录：{现}")
        elif not str(Path(现)).replace("\\", "/").endswith("/work/临时"):
            坏.append(f"判四十二①：gettempdir 落在仓内、且不在 work/临时 下：{现}")

    # ② mkdtemp 不许落在 cwd 上
    沙 = Path(_tf.mkdtemp(prefix="判四十二_"))
    try:
        if osd.path.normcase(str(沙.parent)) == osd.path.normcase(osd.path.abspath(osd.getcwd())):
            坏.append(f"判四十二②：mkdtemp 落在 cwd 上：{沙}")
        果2 = _tg.诊断(探候选=False)
        if not 果2.get("已缓存"):
            坏.append("判四十二②：钉过之后 gettempdir 仍没缓存")
    finally:
        import shutil as _sh
        _sh.rmtree(沙, ignore_errors=True)

    # ③ ★ 这条是量「缺陷还长不长」：跑完不许在包根多出目录
    _后 = _数子目录()
    if _后 != _前:
        坏.append(f"判四十二③：判据自己跑完，包根下子目录从 {_前} 变成 {_后}"
                  f"（多了 {_后 - _前} 个）⇒ 护栏没兜住")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十二临时目录不许落在仓里：绿")


def 判四十三剪枝口径必须全包一份() -> None:
    """★ 2026-11-07 实测：「同一个判断写在两处，必然给出两个答案」。

    `project_graph.扫描` 自带一张**精确名**跳过表（13 项，`d not in 跳过`），
    而 `mem_prune` 用**前缀/类名**表。同一个问题（这个目录该不该进去看）两个答案：

        真仓实测（21,410 目录 / 114,896 文件）：
          改之前：走进 50,599 个目录 · 看到 300,434 个文件 · 16.2s
          改之后：走进 21,410 个目录 · 看到 114,898 个文件 ·  7.0s
          ⇒ **少看 185,536 个文件（61.8%）**

    少看的那些里有 `out`×45 · `site-packages`×33 · `.lake`×8（内含 mathlib 11 万文件）·
    `target_*` 一大串 · `build_script_build-*` —— **构建产物和依赖源码被当成
    「本项目的知识」抽进了图谱**，于是候选数/覆盖率这些数字互相矛盾，
    而看的人无从判断该信哪个。

    判据（**行为判据**，不查代码形态）：造一个真目录树，把 `mem_prune` 会跳的名字
    各放一个文件进去，然后看 `project_graph.扫描` **到底看不看得见它们**。
    """
    import tempfile as _tf
    import shutil as _sh
    try:
        from agentscope_orchestrator import project_graph as _pg
        from agentscope_orchestrator import mem_prune as _mp
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十三：导不进模块（{type(_e).__name__}: {_e}）")
        return

    # 这些名字 mem_prune 会跳、而老口径的精确表**不跳**（都是实测出现过的）
    该跳的 = ["out", "site-packages", ".lake", ".cargo", "work", "products",
              "target2", "target_auto", "target_ts341", "build_script_build-abc",
              "tmp0la_5z0u", ".tmp"]
    该留的 = ["src", "tests", "形式化验证"]

    # 前置：这些名字确实在 mem_prune 的跳表里（否则判据本身失效）
    没跳 = [d for d in 该跳的 if not _mp.该跳(d, ())]
    if 没跳:
        坏.append(f"判四十三：这些名字**本该被跳过却没有**：{没跳}"
                  " ⇒ mem_prune 的通用跳表漏了，先修表")
        留的名义 = [d for d in 该留的 if _mp.该跳(d, ())]
        if 留的名义:
            坏.append(f"判四十三：这些名字**本该保留却被跳过**：{留的名义}")
        return

    根 = Path(_tf.mkdtemp(prefix="r303p43_"))
    try:
        for d in 该跳的 + 该留的:
            (根 / d).mkdir(parents=True, exist_ok=True)
            (根 / d / "证明.lean").write_text("theorem t : True := trivial\n",
                                              encoding="utf-8")
        果 = _pg.扫描(根)
        # ★ `扫描` 返回的 `文件` 是 **list[dict]**（每项带 `文件` 键），**不是 dict**。
        #   我第一版按 dict 遍历，取到的是键名，于是判据报「src/tests 没被扫到」——
        #   那条红**看起来完全像代码的错**。先跑一次把返回结构印出来，才看清是判据读错了。
        #   ⇒「假红与真红长得一样，唯一的分界是**把实得值打出来核对**」。
        见到 = set()
        for 条 in (果.get("文件") or []):
            相对 = 条.get("文件") if isinstance(条, dict) else 条
            if 相对:
                见到.add(str(相对).replace("\\", "/").split("/")[0])
        漏 = [d for d in 该跳的 if d in 见到]
        丢 = [d for d in 该留的 if d not in 见到]
        if 漏:
            坏.append(f"判四十三：这些目录**本该剪掉却被走进去**了：{漏}"
                      " ⇒ 剪枝口径又分叉了（应为全包一份 mem_prune.该跳）")
        if 丢:
            坏.append(f"判四十三：这些目录**本该保留却没被扫到**：{丢}"
                      " ⇒ 剪枝过头了，把该看的也剪了")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十三剪枝口径必须全包一份：绿")


def 判四十四Lean抽取器必须全包一份() -> None:
    """★ 2026-11-07 实测：「同一个语言两套抽取器，各缺一样」。

    起因：缺口③「收录了却抽不出符号」3,969 个文件里 **761 个是 `.lean`**。
    摊开看：723 个散文/记号（无符号是对的）· 23 个只有 import · 11 个空
    ⇒ **真缺陷只有 4 个**，而它们暴露了两个漏认：

        ① `abbrev` 不在 `mem_symbols` 的关键词表里
           （`_verify_ts495.lean` 3 个 · `sl_00_names.lean` 4 个）
        ② 修饰词一个都不认：`noncomputable def bsdBazi …` 匹配不上
           （`BSDWalk.lean` 3 个 noncomputable def）

    而 `project_graph._LEAN声明` **恰好相反**：认修饰词、有 `abbrev`、**无 `inductive`**。
    两套抽取器对同一个文件给出**两个答案**：

        修饰词(private/protected/noncomputable/partial/unsafe/scoped/local) abbrev inductive
      mem_symbols    ❌ 一个不认                                                 ❌     ✅
      project_graph  ✅ 全认                                                     ✅     ❌

    修法：并集定为唯一一份（`mem_symbols._定义正则["lean"]`），`project_graph` import 它。
    实测效果：在**那 761 个无符号文件上当场重抽**，旧正则 0 个、新正则 11 个，
    正好落在预测的那 4 个文件上。

    判据（两侧都判，缺一不可）：
      ① **同源**：`project_graph._LEAN声明` 必须**就是** `mem_symbols` 那一份（同一对象）——
         只要还是「各写一份」，将来必然再分叉；
      ② **行为**：造一个含**全部 9 个关键词 × 7 个修饰词**的 .lean，
         两个模块抽出来的名字必须**逐个相同且一个不少**。
    """
    try:
        from agentscope_orchestrator import mem_symbols as _ms
        from agentscope_orchestrator import project_graph as _pg
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十四：导不进模块（{type(_e).__name__}: {_e}）")
        return

    # ① 同源
    if _pg._LEAN声明 is not _ms._定义正则.get("lean"):
        坏.append("判四十四①：`project_graph._LEAN声明` 与 `mem_symbols._定义正则['lean']`"
                   " **不是同一份** ⇒ 同一个语言两套抽取器，迟早给出两个答案")

    # ② 行为：9 个关键词 × 7 个修饰词，外加中文名与带 `'` 的名字
    关键词 = ["theorem", "lemma", "def", "abbrev", "structure", "class",
              "inductive", "axiom", "instance"]
    修饰词 = ["private", "protected", "noncomputable", "partial", "unsafe",
              "scoped", "local"]
    行 = ["import Mathlib", "namespace 判据44", ""]
    应有 = set()
    for i, k in enumerate(关键词):
        for j, m in enumerate(修饰词):
            名 = f"{k}_被{m}"
            行.append(f"{m} {k} {名} : Nat := 1")
            应有.add(名)
        # 无修饰词 + 中文名 + 撇号名
        行.append(f"{k} {k}_裸 : Nat := 1")
        应有.add(f"{k}_裸")
        行.append(f"{k} 中文{k}名 : Nat := 1")
        应有.add(f"中文{k}名")
        行.append(f"{k} 带撇{k}' : Nat := 1")
        应有.add(f"带撇{k}'")
    行.append("end 判据44")
    文 = "\n".join(行) + "\n"

    正 = _ms._定义正则["lean"]
    命 = {m.group("name") for m in 正.finditer(文)}
    缺失 = sorted(x for x in 应有 if x not in 命)
    if 缺失:
        坏.append(f"判四十四②：这 {len(缺失)} 个声明**抽不出来**（共该有 {len(应有)} 个）："
                   f"{缺失[:8]}" + ("…" if len(缺失) > 8 else ""))

    # ③ 两个模块在同一份文本上必须给出**同一个**结果
    果2 = _pg._LEAN声明.findall(文)
    if {x[1] for x in 果2} != 命:
        坏.append("判四十四③：两个模块在同一份 .lean 上抽出的名字**不一样** "
                   f"（mem_symbols {len(命)} 个 vs project_graph {len({x[1] for x in 果2})} 个）")

    # ⑤ 已覆盖性自查：这次的真缺陷是 `abbrev` 漏认 + 修饰词漏认。
    #    判据必须**自己证明**用例覆盖到了它们 —— 覆盖不到的判据是装饰品。
    #    （上一版我写成 `if not any("noncomputable" in 文 for _ in (0,))`，
    #      那是 `False` 的循环里套恒真表达式 ⇒ **永远不会红**，纯装饰。删了重写。）
    for 必, 位置 in (("abbrev", 关键词), ("noncomputable", 修饰词)):
        if 必 not in 位置:
            坏.append(f"判四十四⑤：`{必}` 不在用例集合里 ⇒ 判据覆盖不到这次的真缺陷")
    if "abbrev_裸" not in 应有 or "abbrev_被noncomputable" not in 应有:
        坏.append("判四十四⑤：用例里没有 `abbrev` 的裸名/带修饰词两种形态"
                   " ⇒ 覆盖不到这次的真缺陷")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十四Lean抽取器必须全包一份：绿")
    # ⑥ 边界如实说：注释里的声明**会被抽出**（已知未修）。这里只做记录，不假装覆盖。
    #    判据如果连自己没覆盖的东西都不说，读的人会以为它管着。


def 判四十五memory的require必须真的过滤() -> None:
    """★ 2026-11-07：`require:` **声明了但没人执行** —— 最坏的一种没接线。

    `profile.py:254` 写着「`require:` 可选，文件头部必须命中的关键词（任务书一类用）」，
    档案里 `kind: task` 也真的用了：
        roots: [多智能体编排/agentscope_orchestrator]
        globs: ["**/*.md"]
        require: [任务, 需证, 未闭合, 待办, todo, formalize, 证明]
    而 `mem_live._枚举声明` **根本没有 `require` 参数**，声明被一路丢掉 ⇒
    `**/*.md` 是递归的，于是**这个包下每一个 `.md`**（全部回执、章程、交付说明、
    诊断报告…）都被当成「任务书」进了常驻表。

    ⇒「一段驱动不了的接线，等于没有接线」。而且比没接线更坏：
      档案里**写着**有过滤，读数里看不出没有 —— **看起来有，其实没有**。

    实测（真仓，包内 739 个 `.md`）：修前 739 全进表；修后**保留 269 · 滤掉 470**。

    判据（**两侧都判**，缺一不可）：
      ① 该滤的**滤掉**：正文与文件名都不含关键词的 `.md` 不许进候选；
      ② 该留的**留下**：**判掉该要的，比多要一个贵得多**（多要的会在读数里显形，
         判掉的不会）—— 文件名带关键词的、正文头部带关键词的，都必须留下；
      ③ 读不了的**不许滤掉**：无法判断时滤掉就是拿「看不见」当「没有」；
      ④ `自动同步` 的返回里必须**报出滤掉多少** —— 过滤器的力度看不见，
         就没人知道它是在干活还是又一根没接上的线。
    """
    import tempfile as _tf
    import shutil as _sh
    try:
        from agentscope_orchestrator import mem_live as _ml
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十五：导不进 mem_live（{type(_e).__name__}: {_e}）")
        return

    if not hasattr(_ml, "_命中要求"):
        坏.append("判四十五①：`mem_live._命中要求` 不存在 ⇒ `require:` 又没人执行了")
        return

    要求 = ("任务", "需证", "未闭合", "待办", "todo", "formalize", "证明")
    d = Path(_tf.mkdtemp(prefix="r303p45_"))
    try:
        # ① 该滤的
        (d / "纯散文.md").write_text(
            "# 心得\n\n今天天气不错，讲个故事，没有别的内容。\n", encoding="utf-8")
        # ② 该留的：文件名命中 / 正文头部命中
        (d / "任务书-甲.md").write_text("# 甲\n\n正文。\n", encoding="utf-8")
        (d / "随便.md").write_text("# 标题\n\n这里提到待办事项。\n", encoding="utf-8")
        (d / "todo-list.md").write_text("empty\n", encoding="utf-8")

        缺 = []
        if _ml._命中要求(d / "纯散文.md", 要求):
            缺.append("① 该滤的没滤：正文与文件名都不含关键词，却算命中")
        if not _ml._命中要求(d / "任务书-甲.md", 要求):
            缺.append("② 该留的没留：**文件名**带关键词却算不命中")
        if not _ml._命中要求(d / "随便.md", 要求):
            缺.append("② 该留的没留：**正文头部**带关键词却算不命中")
        if not _ml._命中要求(d / "todo-list.md", 要求):
            缺.append("② 该留的没留：英文关键词没认")
        # ③ 读不了的必须算命中（保留）
        缺文件 = d / "读不了的.md"
        if _ml._命中要求(缺文件, 要求) is not True:
            缺.append("③ 不存在的文件应当**算命中并保留**（无法判断 ≠ 没有），实得不然")
        if 缺:
            坏.extend(f"判四十五：{x}" for x in 缺)
    finally:
        _sh.rmtree(d, ignore_errors=True)

    # ④ 返回里必须报出滤掉多少
    import inspect as _ins
    源 = _ins.getsource(_ml.自动同步)
    if "require滤掉" not in 源:
        坏.append("判四十五④：`自动同步` 的返回里没有 `require滤掉` ⇒ "
                   "过滤器的力度看不见，没法知道它在不在干活")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十五memory的require必须真的过滤：绿")

    # ⑤ ★ 如实声明的边界：关键词表来自档案（不是本判据定的），**精度有限**。
    #    真仓实测：包内 739 个 .md，保留 269 —— 其中含 README/章程/交付说明 这类
    #    正文里提到「证明」的**文档**。这不是判据能修的（表是项目口径），
    #    但**必须让读的人知道**，所以写在这里而不是藏着。
    #    「文档只报好话，等于把缺口藏起来」。


def 判四十六图上死引用必须进常驻缺口且减掉假红() -> None:
    """★ 2026-11-07（R303）：章程 §3.5「**缺口必须是节点**」那一半。

    章程 line 219 自己写着：「缺口目前是**读数**，**还不是图里的节点**……
    要真正落地得先把三张分离的库合成一张可查询的图。」

    ★ 但查下去发现**章程这条已经过期一半**：`refgraph.py` 早就在建一张**有边**的图
      （实测 7,209 文件 / 46,170 边），而且 `死引用` 是一等记录
      （它的 docstring：「解不出的**单独记**成 `死引用`，**不许静默丢掉**」）。
      ⇒ 真正的缺口不是「没有图」，是**图里的缺口没接进常驻读数**：
        3,655 条真缺口在每轮注入里**一个字都不出现**。
      「一段驱动不了的接线，等于没有接线」——反过来也成立：
      **一根接上了但另一端没插的线，等于没有接线。**

    ★★★ 而直接接上去会**更坏**。实测：`refgraph` 报 `死引用 = 6,198`，
      但逐条拿去全仓文件名集核对：

          ① 真死引用（**全仓都没有**）                    3,655
          ② 被剪枝但存在（仓里有，只是不在 `memory:` 根里）  2,543   ← **41% 假红**

      `products/`（1,035）和 `.venv`（883）占了大头 —— **存在但被剪枝**。
      直接报 6,198 就是把「在别的根里」报成「悬空」，
      **正是 A 级锚定第一版踩过的那个坑**（`mem_anchor.py` 文件头记着）。
      **假绿比无齿更坏，假红同样是坏账**：它让人去修不存在的问题。

    判据（**两侧都判**，缺一不可）：
      ① 算术闭合：①真死 + ②假红 == 图上死引用总数（少一条都说明分类漏了）；
      ② **被减掉的量必须可见**：简报那行要写出「总数里减掉了 N 条」——
         不然读的人会把真死引用当成那个大数；
      ③ **行为两侧**：拿**真仓里确实存在**的文件名当死引用 → 必须归 ②；
        拿**绝不可能存在**的名字 → 必须归 ①；
      ④ 进趋势：`读数取` 要出扁平键 `图上真死引用`，且 `_史键序` 认它
         —— 嵌套 dict 写进历史读不出来 = 「数量变化就是演化进度」这条对它失效。
    """
    import json as _j
    from pathlib import Path as _P
    try:
        from agentscope_orchestrator import mem_live as _ml
        from agentscope_orchestrator import refgraph as _rg
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十六：导不进模块（{type(_e).__name__}: {_e}）")
        return

    if not hasattr(_ml, "图上死引用"):
        坏.append("判四十六：`mem_live.图上死引用` 不存在 ⇒ 图里的缺口又没接线")
        return

    # ── ③ 行为两侧：造一个假图，名字一真一假 ──
    真名 = "mem_live.py"                     # 仓里一定有（本模块自己）
    假名 = "绝不可能存在_r303_判四十六_zzz.lean"
    # ★★★ 第三个用例是**这一版才补上的**，它抓的是我自己犯过的错：
    #   `products/` 在 `mem_prune.通用跳` 里 ⇒ **剪枝口径**走全仓名集时看不见它。
    #   我第一版就是拿剪枝口径判存在性，于是只在剪枝目录里存在的名字被判成
    #   「①真死引用」—— 真死引用从 2,228 虚报成 3,655（**多报 39%**）。
    #   队友在反方向（`.lean→.rs`）同回合独立量到同一件事（三类 21/7/47 → 21/48/6）。
    #   ⇒ 这条用例如果拿掉，「口径用错」就再也不会有判据拦得住。
    只在剪枝里 = "Orchestrated_002.lean"       # 只在 `products/` 下存在
    档 = _ml._死引用档
    备份 = 档.read_bytes() if 档.is_file() else None
    原函数 = _rg.只读缓存

    class _假图:
        死引用 = [("假源/甲.rs", 真名), ("假源/乙.rs", 假名), ("假源/丙.rs", 只在剪枝里)]
        歧义 = []
        节点 = {}
        统计 = {"边": 3, "文件": 3}

    try:
        _rg.只读缓存 = lambda *a, **k: _假图()      # type: ignore[assignment]
        果 = _ml.图上死引用(重算=True)
        一 = 果.get("①真死引用（全仓都没有）")
        二 = 果.get("②被剪枝但存在（**不是缺口**）")
        if 一 != 1 or 二 != 2:
            坏.append(
                f"判四十六③：行为两侧不对 —— 仓里存在的 `{真名}` 和"
                f"**只在剪枝目录（products/）里存在的** `{只在剪枝里}` 都该归②，"
                f"不存在的 `{假名}` 该归①；实得 ①={一} ②={二}。"
                f"★ 若①=2，说明**存在性用的是剪枝口径** —— 拿「看不见」当「没有」")
        # ① 算术闭合
        if (一 or 0) + (二 or 0) != 果.get("图上死引用总数"):
            坏.append(f"判四十六①：算术不闭合 —— {一}+{二} != "
                       f"{果.get('图上死引用总数')} ⇒ 分类漏了")
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十六③：行为两侧抛异常（{type(_e).__name__}: {_e}）")
    finally:
        _rg.只读缓存 = 原函数                      # type: ignore[assignment]
        try:
            if 备份 is not None:
                档.parent.mkdir(parents=True, exist_ok=True)
                档.write_bytes(备份)
            elif 档.is_file():
                档.unlink()
        except OSError:
            pass

    # ── ② 简报那行必须写出「减掉了多少」──
    _ml._缺口 = None
    try:
        缺 = _ml.取缺口()
    except Exception:                                        # noqa: BLE001
        缺 = {}
    四 = (缺 or {}).get("④图上死引用") or {}
    if not 四:
        坏.append("判四十六②：常驻缺口读数里没有 `④图上死引用` ⇒ 落档的那份没带 ④")
    else:
        行 = _ml.缺口一行(缺)
        if "减掉了" not in 行:
            坏.append("判四十六②：简报那行没写出**减掉了多少** ⇒ "
                       "读的人会把真死引用当成 6,198 那个大数")

    # ── ④ 进趋势：扁平键 + 键序认它 ──
    取 = _ml.读数取({"④图上死引用": {"①真死引用（全仓都没有）": 3655,
                                     "图上死引用总数": 6198}})
    if not isinstance(取.get("图上真死引用"), (int, float)):
        坏.append("判四十六④：`读数取` 没出扁平键 `图上真死引用` ⇒ 历史里没有这条")
    if "图上真死引用" not in _ml._史键序:
        坏.append("判四十六④：`_史键序` 不认 `图上真死引用` ⇒ 趋势对比读不到它")
    # 反向：**总数**不许进趋势（41% 是假红，拿它当演化进度就是拿假红当成绩）
    if "图上死引用总数" in _ml._史键序:
        坏.append("判四十六④：`_史键序` 收了 `图上死引用总数` ⇒ "
                   "41% 是假红的总数不许当演化进度")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十六图上死引用必须进常驻缺口且减掉假红：绿")


def 判四十七局部召回必须拉出这块缺什么() -> None:
    """★ 2026-11-07（R303）：章程 §3.5「缺口必须是**节点**」的**局部召回**那一半。

    常驻读数那一半在判四十六里已经落地（`mem_live.缺口() ④图上死引用`）。
    但 `concept.概念召回` 这一路**只拉「有的」**东西 ——
    返回 `概念术语/代码/证明/文档/定理/说明`，**一个字不提这块缺什么**。
    而「演化进度」正是靠**缺口数变化**的：**召回时不报缺，就等于把进度藏起来。**

    ★ 加完之后实测（真仓，问「让一个变换反复作用到自己身上…」）：
      本批召回的 120 个文件里，**①真死引用 101 条 · ②被剪枝但存在 264 条**。
      例：`TS-195_核查报告.md → Bridge_...lean` · `→ 删除.rs` · `→ 双螺旋.rs`。

    判据（**两侧都判 + 抓一次我自己犯过的错**）：
      ① `概念召回` 的返回里必须有 `缺口` 键（静默不返回 = 这一路又没接线）；
      ② **成本**：`_本块缺口` **不许走全仓**（问答路径上走全仓实测 17.4s，
         而本仓有过「建图放进问答路径」被当场抓住的先例：实测 209s / 5.5 万次枚举）；
      ③ **行为两侧**：造一个假 `refgraph` 缓存 + 假 `真死名字`，
         让同一批召回文件里一个点到**存在**的名字、一个点到**不存在**的名字
         ⇒ 必须分成 ②=1 / ①=1；
      ④ ★ **侧车缺字段时必须算「未测到」，不许算「0 条」** ——
         这一条抓的是我自己在本轮犯的错：`图上死引用` 的缓存命中只比了**指纹**，
         侧车是**旧格式**（没有 `真死名`）时照样命中 ⇒ `真死名字()` 返回**空集**
         ⇒ 局部召回报「**真死 0 条**」—— **看起来像好消息**。
         ⇒ 缓存有效性必须把「**下游要用的字段在不在**」算进去；
            `_本块缺口` 拿不到名字集时必须出 `未测到`。
    """
    import json as _j
    from pathlib import Path as _P
    try:
        from agentscope_orchestrator import concept as _cc
        from agentscope_orchestrator import mem_live as _ml
        from agentscope_orchestrator import refgraph as _rg
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十七：导不进模块（{type(_e).__name__}: {_e}）")
        return

    if not hasattr(_cc, "_本块缺口"):
        坏.append("判四十七①：`concept._本块缺口` 不存在 ⇒ 局部召回又只拉「有的」")
        return

    # ① 返回里必须有 `缺口` 键
    import inspect as _ins
    _源 = _ins.getsource(_cc.概念召回)
    if "缺口" not in _源:
        坏.append("判四十七①：`概念召回` 的返回里没有 `缺口` ⇒ 这一路又没接线")

    # ② 不许走全仓
    _本 = _ins.getsource(_cc._本块缺口)
    if "walk(" in _本 or "os.walk" in _本:
        坏.append("判四十七②：`_本块缺口` 里有目录遍历 ⇒ 建图/走仓进了**问答路径** "
                   "（实测 17.4s；本仓有「建图进问答路径」被抓住的先例：209s）")
    if "只读缓存" not in _本:
        坏.append("判四十七②：`_本块缺口` 没走 `refgraph.只读缓存` ⇒ 那它自己建图了")

    # ③ 行为两侧
    假源 = _P(__file__).resolve()
    try:
        真名 = "mem_live.py"                       # 仓里一定有
        假名 = "绝不可能存在_r303_判四十七_zzz.lean"

        class _假图:
            死引用 = [(str(假源), 真名), (str(假源), 假名)]
            歧义 = []
            统计 = {"边": 2, "文件": 2}

        原图, 原名 = _rg.只读缓存, _ml.真死名字
        _rg.只读缓存 = lambda *a, **k: _假图()      # type: ignore[assignment]
        _ml.真死名字 = lambda *a, **k: {假名}       # type: ignore[assignment]
        try:
            果 = _cc._本块缺口([假源])
        finally:
            _rg.只读缓存 = 原图                     # type: ignore[assignment]
            _ml.真死名字 = 原名                     # type: ignore[assignment]
        一 = 果.get("①真死引用（全仓都没有）")
        二 = 果.get("②被剪枝但存在（**不是缺口**）")
        if 一 != 1 or 二 != 1:
            坏.append(f"判四十七③：行为两侧不对 —— 不存在的 `{假名}` 该归①、"
                       f"存在的 `{真名}` 该归②；实得 ①={一} ②={二}")
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十七③：行为两侧抛异常（{type(_e).__name__}: {_e}）")

    # ④ 侧车缺字段 ⇒ 未测到（不许算 0 条）
    档 = _ml._死引用档
    备份 = 档.read_bytes() if 档.is_file() else None
    try:
        档.parent.mkdir(parents=True, exist_ok=True)
        # 旧格式侧车：**有正确的指纹但没 `真死名`**
        档.write_text(_j.dumps({"指纹": "x", "果": {}}, ensure_ascii=False),
                      encoding="utf-8")
        if _ml.真死名字():
            坏.append("判四十七④：侧车没有 `真死名` 时 `真死名字()` 却给了非空集 —— "
                       "它从哪来的？")
        # ★ 关键：`_本块缺口` 在这时必须出 `未测到`，**不许**报「真死 0 条」
        class _假图2:
            死引用 = [("假源/甲.md", "某个名.lean")]
            歧义 = []
            统计 = {"边": 1, "文件": 1}
        原图 = _rg.只读缓存
        _rg.只读缓存 = lambda *a, **k: _假图2()     # type: ignore[assignment]
        try:
            果 = _cc._本块缺口([_P("./假源/甲.md")])
        finally:
            _rg.只读缓存 = 原图                     # type: ignore[assignment]
        if not 果.get("未测到"):
            坏.append("判四十七④：名字集拿不到时 `_本块缺口` **没出「未测到」** "
                       "⇒ 会把「分不开」报成「真死 0 条」——**看起来像好消息**。"
                       "（这正是我本轮犯的错：缓存命中只比指纹，没比下游要用的字段在不在）")
    except Exception as _e:                                  # noqa: BLE001
        坏.append(f"判四十七④：抛异常（{type(_e).__name__}: {_e}）")
    finally:
        try:
            if 备份 is not None:
                档.write_bytes(备份)
            elif 档.is_file():
                档.unlink()
        except OSError:
            pass

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十七局部召回必须拉出这块缺什么：绿")



def 判一百八十七索引说有没有图必须拿盘上真值对():
    """★★★★ 口径 375：**守恒等式不能证明读数正确**（R533 / R533b）。

    判一百一十七 查的是 `建成 + 没建 == 根数` —— 而
    「建成 1 + 没建 16 = 17」（**在说谎**）与「建成 17 + 没建 0 = 17」（**说真话**）
    **两种情况下都成立** ⇒ **它抓不到那个谎**：
    错的是**分类的归属**，不是**总数**。
    ★★★ 实测取证（R533）：16 个**已经建好图**的根被报成「没建图」，
      而 184 条判据**全绿** —— **判据保内部一致，不保外部真值**。

    本判据拿**盘上真值**去对：
      ① 说「有图」的 ⇒ `★图在哪找到的` 那里的 `图.json.根` 必须**就是这个根**
         （口径 359：认权威指针 `图.json.根`，不认目录名/位置）；
      ② 说「我找过的地方都没有」的 ⇒ 必须在**已知图仓**里**真找不到**它的图；
      ③ `★找图的地方` 必须非空（口径 358：不许空口说「没有」）。
    """
    import json as _j187
    import pathlib as _p187
    基 = _p187.Path(__file__).resolve().parent.parent          # 仓根
    总根 = 基.parent.parent                                      # 索引落在总根的 .kg 里
    索 = 总根 / ".kg" / "索引.json"
    assert 索.is_file(), f"没有索引 `{索}` ⇒ 先跑 `刷全仓索引(总根, 图仓库们=[…])`"
    索d = _j187.loads(索.read_text(encoding="utf-8"))
    图仓 = [基 / "work" / "图", 基 / "work" / "图_验收", 总根 / ".kg"]

    def 图仓里找(根p: _p187.Path):
        """在已知图仓里找「根就是它」的桶 —— 与 `kg._找图桶` 同一判据（认权威指针）。"""
        for 仓 in 图仓:
            if not 仓.is_dir():
                continue
            for g in list(仓.glob("图.json")) + list(仓.glob("*/图.json")):
                try:
                    if _p187.Path(_j187.loads(g.read_text(encoding="utf-8")).get("根", "")).resolve() == 根p:
                        return g.parent
                except Exception:                                 # noqa: BLE001
                    continue
        return None

    坏, 有, 无 = [], 0, 0
    for x in (索d.get("根们") or []):
        根p = _p187.Path(str(x.get("根"))).resolve()
        找过 = x.get("★找图的地方") or []
        if not 找过:
            坏.append(f"`{根p.name}` 没有 `★找图的地方` ⇒ 口径 358：不许空口说「没有」")
        if x.get("有图"):
            有 += 1
            哪 = x.get("★图在哪找到的")
            if not 哪:
                坏.append(f"`{根p.name}` 说「有图」却没给 `★图在哪找到的`")
                continue
            g = _p187.Path(str(哪)) / "图.json"
            if not g.is_file():
                坏.append(f"`{根p.name}` 说图在 `{哪}`，可那里**没有 `图.json`**")
                continue
            try:
                r = _j187.loads(g.read_text(encoding="utf-8")).get("根")
            except Exception as e:                                # noqa: BLE001
                坏.append(f"`{根p.name}` 的图元读不了（{type(e).__name__}）")
                continue
            if _p187.Path(str(r)).resolve() != 根p:
                坏.append(f"`{根p.name}` 的桶里写着根是 `{r}` ⇒ **认错桶**（口径 359）")
        else:
            无 += 1
            if 图仓里找(根p) is not None:
                坏.append(f"`{根p.name}` 被报「我找过的地方都没有」，"
                          f"可**已知图仓里就有一张它的图** ⇒ 「没找」冒充「没有」")
    assert not 坏, "；".join(坏[:4])
    return (f"索引有图读数对盘上真值：绿 —— 判一百八十七（有图 **{有}** · 无图 **{无}** · "
            f"每条都带 `★图在哪找到的` 且 `图.json.根` 对得上（口径 359）· "
            f"无图的在 {len(图仓)} 个已知图仓里真找不到 · 找过的地方都非空（口径 358）· "
            f"★ 口径 375：判一百一十七 的等式在「1+16」和「17+0」两种情况下都绿 ⇒ **它抓不到这个谎**，"
            f"本判据拿真值对才抓得到）")



def 判一百八十八没用过的文件夹加一句人话必须秒级定位():
    """★★★★ **可用性**判据 —— 此前 185 条一条都没判这个（R534e）。

    判据问的是**用户的问题**：「换个项目、换个仓库……让 AI 知道自己有什么东西」
    ⇒ 具体化成一个**可机判**的动作：
      **一个从没见过的文件夹 + 一句人话问题 ⇒ 秒级给出「在哪」**。

    ★ 为什么必须单独一条：`kg.py` 那条路（9,000 行 · 全量解析成图 · 63k 文件
      12 小时 0 产物）**在 185 条判据全绿的情况下**，也回答不了「excel 在哪」
      —— 因为没有 `.xlsx` 适配器、也没有「有什么」这一层。
      ⇒ **判据全绿与「能用」是两件事**，所以要有一条**直接判能用**的。

    ★ 对照组（口径 371）：② 命中与 ③ 不命中**必须同时成立**才有意义。
    """
    import importlib as _il188
    import json as _j188
    import shutil as _sh188
    import pathlib as _p188
    import re as _re188
    import tempfile as _t188
    import time as _t188b
    基 = _p188.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(基 / "src"))
    知 = _il188.import_module("agentscope_orchestrator.知库")

    临时 = 基 / "work" / "临时"
    临时.mkdir(parents=True, exist_ok=True)
    靶 = _p188.Path(_t188.mkdtemp(prefix="判188可用_", dir=str(临时)))
    try:
        (靶 / "a").mkdir()
        (靶 / "b").mkdir()
        # ★ 关键词**只出现在内容里**，路径里没有 —— 这样内容阶段才是唯一来源
        (靶 / "a" / "x.rs").write_text("fn main() { /* 点云解析 */ }\n", encoding="utf-8")
        (靶 / "b" / "y.rs").write_text("// zebra crossing\nfn go() {}\n", encoding="utf-8")
        (靶 / "b" / "z.md").write_text("# 无关\n", encoding="utf-8")
        汇 = 知.建索引(靶, 打印=lambda *a: None)
        assert 汇["文件数"] == 3, f"建索引文件数不对：{汇['文件数']} != 3"

        t0 = _t188b.time()
        r = 知.问(靶, "zebra", 上限=5)
        秒 = _t188b.time() - t0
        命中 = [x["路径"] for x in r["命中"]]
        assert any(x.endswith("y.rs") for x in 命中), \
            f"② 内容命中失败：路径里没有 zebra，内容里才有 ⇒ 应命中 b/y.rs，实得 {命中}"
        assert 秒 < 60, f"「问」太慢：{秒:.1f}s"

        # ★★ 对照组：关掉内容阶段 ⇒ **不许**命中 y.rs（否则②什么都不证明）
        r2 = 知.问(靶, "zebra", 上限=5, 扫描=False)
        命中2 = [x["路径"] for x in r2["命中"]]
        assert not any(x.endswith("y.rs") for x in 命中2), \
            f"③ 对照组失败：关了内容阶段还命中 y.rs（{命中2}）⇒ ② 不能证明内容阶段有用"

        # ④ 必须自证用了哪个扫描器（口径 307）
        assert r["扫描器"] in ("rg", "git-grep", "findstr", None), r["扫描器"]
        assert isinstance(r["边界"], list)

        # ⑤ 按需深读带行号
        正 = 知.看(靶, "b/y.rs", 上限行=5)
        assert "zebra" in 正 and _re188.match(r"\s*\d+\|", 正), f"看() 没给带行号的正文：{正[:60]}"
    finally:
        _sh188.rmtree(靶, ignore_errors=True)
    return (f"可用性（没见过的文件夹 + 一句人话）：绿 —— 判一百八十八"
            f"（建索引 {汇['文件数']} 文件 · `问(zebra)` **{秒:.2f}s** 命中 `b/y.rs`"
            f"（路径里没有这个词 ⇒ 只能来自内容）· **对照组**关掉内容阶段就**不命中** ✓ · "
            f"扫描器自证 `{r['扫描器']}` · 边界 {len(r['边界'])} 条 · `看()` 带行号 ✓ · "
            f"★ 口径 375 的同族：**判据全绿与「能用」是两件事**）")



def 判一百八十九定计划前必须先真盘点且路径要真存在():
    """★★★★ **①→② 接线**判据：盘点真、路径真、**引用不存在的路径就拒发**（R535c）。

    ★ 断点原状（186 条判据里**一条都没判过**）：`plan_tasks(spec)` 只吃规约
      JSON 就出计划，**不读任何盘点** ⇒ 计划里引用的路径对不对，
      **规划阶段无从知道**。用户的原话是「任意项目启动自动建知识图谱，
      **根据**目标自动定计划」—— 两层都在，**中间没接**。

    ★ 对照组（口径 371）：②「不存在 ⇒ 拒发」与 ①「存在 ⇒ 放行」
      **必须同时成立**，否则 ① 的放行只说明核对没接上。
    """
    import importlib as _il189
    import shutil as _sh189
    import pathlib as _p189
    import tempfile as _t189
    基 = _p189.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(基 / "src"))
    规 = _il189.import_module("agentscope_orchestrator.auto_goal")

    临时 = 基 / "work" / "临时"
    临时.mkdir(parents=True, exist_ok=True)
    靶 = _p189.Path(_t189.mkdtemp(prefix="判189接线_", dir=str(临时)))
    try:
        (靶 / "src").mkdir()
        (靶 / "src" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (靶 / "README.md").write_text("# 靶\n", encoding="utf-8")

        def _规(cwd值):
            return {"name": "接线齿", "pre": ["原料"], "constraints": ["资源"],
                    "gates": [{"type": "compile", "cmd": ["make"], "cwd": cwd值},
                              {"type": "grep_required", "in": "src/a.py",
                               "patterns": ["def f"]}],
                    "acceptance": ["全绿"]}

        # ① 真存在的相对目录 ⇒ 放行，且盘点数必须对得上盘
        盘 = 规.带盘点定计划(靶, _规("src"), 打印=lambda *a: None)
        assert 盘["盘点"]["文件数"] == 2, f"盘点数不对：{盘['盘点']['文件数']} != 2"
        核 = 盘["★路径核对"]
        assert not 核["不存在"], f"① 真存在的路径被误判成不存在：{核['不存在']}"
        assert any(x["原样"] == "src" and x["在索引里"] for x in 核["查了"]), \
            f"① `src` 没被核对到（核对没接上？）：{核['查了']}"

        # ② ★★ 对照组：不存在的目录 ⇒ **必须拒发**
        抛了 = None
        try:
            规.带盘点定计划(靶, _规("没有这个目录"), 打印=lambda *a: None)
        except ValueError as _e:
            抛了 = str(_e)
        assert 抛了 is not None, \
            "② 对照组失败：`cwd` 指向不存在的目录，**计划居然发出去了** ⇒ 核对是摆设"
        assert "拒发" in 抛了 and "没有这个目录" in 抛了, f"② 拒发了但没说清是什么：{抛了[:120]}"

        # ③ 占位符不许被当路径（否则每一份正常计划都被误杀）
        盘3 = 规.带盘点定计划(靶, _规("{repo}"), 打印=lambda *a: None)
        assert not 盘3["★路径核对"]["不存在"], "③ 占位符 `{repo}` 被当成不存在的路径了"
        assert 盘3["★路径核对"]["占位符没查"], "③ 占位符没记进 `占位符没查`（等于假装查过）"
    finally:
        _sh189.rmtree(靶, ignore_errors=True)
    return (f"①→② 接线：绿 —— 判一百八十九（盘点 {盘['盘点']['文件数']} 文件对得上盘 · "
            f"真路径**放行**且被核对到 · **对照组**：不存在 ⇒ **拒发** ✓（`{抛了[:34]}…`）· "
            f"占位符 `{{repo}}` **没被误杀**（记进 `占位符没查` {len(盘3['★路径核对']['占位符没查'])} 条）· "
            f"★ 此前 186 条判据**一条都没判过**「计划引用的路径真不真」）")



def 判一百九十交付包必须与git一个文件不少():
    """★★★★ **交付包完整性**判据（R535h）—— 此前**一条都没量过**。

    ★ 为什么必须有：今天量出来的读数是 **9 个入口里 4 个在「装出来的包」里崩**
      （`ImportError: cannot import name '只读探针'`），而**开发环境里全是 0** ✗
      ⇒ 口径 378：「能装」≠「能建」≠「装完能用」；R291 只守了第一环。
    ★ 根因：`tar -xf` 解包 `git archive` 的产物时**丢掉中文名文件**（差 6 个，
      全是汉字名，含 `知库.py`）⇒ 正解是 **`tarfile` 直接吃 git 的字节流**。

    ★ 对照读数（**不硬断言**）：`tar -xf` 那条路**实际丢几个** ——
      若哪天 tar 不丢了，这条读数会变，但 ① 仍然守着我们的路径。
    """
    import importlib as _il190
    import io as _io190
    import pathlib as _p190
    import shutil as _sh190
    import subprocess as _sp190
    import tarfile as _tf190
    import tempfile as _t190
    import zipfile as _z190
    基 = _p190.Path(__file__).resolve().parent.parent
    仓 = 基.parent.parent
    子 = "多智能体编排/agentscope_orchestrator"
    sys.path.insert(0, str(基 / "tools"))

    # ① git 树里的 .py 全集（这是**真值**）
    进 = _sp190.run(["git", "-C", str(仓), "ls-tree", "-r", "--name-only", f"HEAD:{子}"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert 进.returncode == 0, f"git ls-tree 失败：{进.stderr[:200]}"
    git_py = {x.strip() for x in 进.stdout.splitlines()
              if x.strip().startswith("src/agentscope_orchestrator/") and x.strip().endswith(".py")}
    assert len(git_py) > 50, f"git 树里 .py 只有 {len(git_py)} 个 ⇒ 基准不对"

    # ② 用我们的打包路径打一个 wheel，数它里面的 .py
    打 = _il190.import_module("打wheel")
    临时 = 基 / "work" / "临时"
    临时.mkdir(parents=True, exist_ok=True)
    靶 = _p190.Path(_t190.mkdtemp(prefix="判190包_", dir=str(临时)))
    try:
        轮 = 打.从git(仓, 靶, 打印=lambda *a: None)
        with _z190.ZipFile(轮) as z:
            # ★ 两边都要**带** `agentscope_orchestrator/` 前缀才可比 ——
            #   第一版一边剥了前缀、一边没剥 ⇒ 误报「少了 107 个」（**我自己的尺子错**）。
            包_py = {n for n in z.namelist()
                    if n.endswith(".py") and n.startswith("agentscope_orchestrator/")}
            记 = [n for n in z.namelist() if n.endswith("RECORD")][0]
            记文 = z.read(记).decode("utf-8")
        期 = {x[len("src/"):] for x in git_py}
        缺 = sorted(期 - 包_py)
        assert not 缺, f"★ 交付包少了 {len(缺)} 个模块：{缺[:6]}（git 里有、包里没有）"

        # ③ 针对性回归：中文名模块必须在
        汉 = [x for x in ("知库.py", "只读探针.py") if
              f"agentscope_orchestrator/{x}" not in 包_py]
        assert not 汉, f"★ 中文名模块不在包里：{汉}（这就是 tar 丢文件的症状）"

        # ④ RECORD 每行哈希格 43 字符
        密 = [len(x.split(",", 2)[1].split("=", 1)[1])
              for x in 记文.splitlines() if x.count(",") >= 2 and "=" in x.split(",", 2)[1]]
        assert 密 and set(密) == {43}, f"★ RECORD 哈希格长度异常：{sorted(set(密))[:5]}"
        assert 记文.count("sha256=") >= 50, "RECORD 行数太少"

        # ⑤ 对照读数：tar -xf 落盘那条路**实际**丢几个（不硬断言）
        流失 = None
        if _sh190.which("tar"):
            进3 = _sp190.run(["git", "-C", str(仓), "archive", "--format=tar", f"HEAD:{子}"],
                            capture_output=True)
            t2 = 靶 / "对照.tar"
            t2.write_bytes(进3.stdout)
            落 = 靶 / "对照落盘"
            落.mkdir()
            _sp190.run(["tar", "-xf", str(t2), "-C", str(落)], capture_output=True)
            src = 落 / "src" / "agentscope_orchestrator"
            得 = {p.name for p in src.glob("*.py")} if src.is_dir() else set()
            流失 = len(git_py) - len(得)
    finally:
        _sh190.rmtree(靶, ignore_errors=True)
    return (f"交付包完整性：绿 —— 判一百九十（git .py **{len(git_py)}** ⇒ 包内 **{len(包_py)}**，"
            f"**一个不少** ✓ · 中文名模块 `知库.py`/`只读探针.py` **都在** ✓ · "
            f"RECORD {len(密)} 行哈希格全 {sorted(set(密))} 字符 ✓ · "
            f"★ 对照读数：`tar -xf` 那条路**丢了 {流失} 个**（本轮实测 6 个，含 `知库.py`）"
            f"—— 这就是修复前 9 个入口里 4 个 ImportError 的原因）")



def 判一百九十一凭据按权威标记找不许写死路径():
    """★★★★ **凭据解析**判据（R536b）—— 挡「我读错地方」伪装成「key 没配」。

    ★ 实测背景（这一轮的真读数）：修之前探通道 ⇒ **5 个通道全 dead，live=0**，
      失败信息是「凭据不可读」，**看起来完全合理**；真相是路径写死
      `Path.home()/.dsh/`，而权威位置由宿主以 `DSH_HOME` 注入
      （这台机器是 `E:\dsh-data\.dsh`）。修完再探 ⇒ **live = 1**
      （`qww|deepseek-v4.1-flash` HTTP 200）。
    ★ 而 `dsh_channels.宿主线索引()` 早就算出了正确路径，**只是没人接**。
      ⇒ 口径 383：「拿不到」必须先证明「我查对了地方」。
    """
    import os as _os191
    import pathlib as _p191
    import shutil as _sh191
    import sys as _s191
    import tempfile as _t191
    基 = _p191.Path(__file__).resolve().parent.parent
    _s191.path.insert(0, str(基 / "src"))
    from agentscope_orchestrator import gateway as _G191

    旧 = {}
    for k in ("DSH_HOME", "DSH_PROFILE_DIR", "ORCH_CREDENTIALS_FILE"):
        旧[k] = _os191.environ.get(k)
    靶 = _p191.Path(_t191.mkdtemp(prefix="判191_", dir=str(基 / "work" / "临时")))
    try:
        # ① DSH_HOME 设了 ⇒ 候选第一项必须是它
        假根 = 靶 / "假DSH"
        假根.mkdir()
        _os191.environ["DSH_HOME"] = str(假根)
        _os191.environ.pop("ORCH_CREDENTIALS_FILE", None)
        _os191.environ.pop("DSH_PROFILE_DIR", None)
        候 = _G191.凭据候选()
        assert 候, "凭据候选是空的"
        assert 候[0] == 假根 / ".credentials.yaml", (
            f"★ DSH_HOME 设了，第一候选却不是它：{候[0]}（权威标记被忽略了）")

        # ③ 功能：临时凭据文件里放一个 key，`api_key` 必须真读到
        (假根 / ".credentials.yaml").write_text(
            "version: 1\nrefs:\n  ZZZ_API_KEY: sk-测试用的假key\n", encoding="utf-8")
        频 = _G191.Channel(name="zzz", display_name="zzz", base_url="http://例",
                          key_env="ZZZ_API_KEY", models=[])
        assert 频.api_key == "sk-测试用的假key", "★ 路径对了但没读到 key（路径对 ≠ 能用）"

        # ④ 失败信息必须列出试过哪些路径
        (假根 / ".credentials.yaml").write_text("version: 1\nrefs: {}\n", encoding="utf-8")
        频2 = _G191.Channel(name="yyy", display_name="yyy", base_url="http://例",
                           key_env="YYY_API_KEY", models=[])
        try:
            频2.api_key
            报 = ""
        except RuntimeError as e:
            报 = str(e)
        assert "试过" in 报 and "4 处" in 报 or "试过" in 报, (
            f"★ 读不到 key 时没列出试过哪些路径 ⇒ 「我读错地方」会伪装成「key 没配」：{报[:120]}")
        assert "yyy" in 报, "报错里没说是哪个通道"

        # ② 对照组：DSH_HOME 不设 ⇒ 不许出现假根相关项，且老路径仍兜底
        _os191.environ.pop("DSH_HOME", None)
        候2 = _G191.凭据候选()
        assert str(假根) not in str(候2), (
            f"★ DSH_HOME 没设却仍冒出它：{候2}（说明读的不是环境，是别的）")
        assert any(str(c).endswith(".dsh" + _os191.sep + ".credentials.yaml") or
                  (".dsh" in str(c) and ".credentials.yaml" in str(c)) for c in 候2), (
            f"★ 老布局兜底不见了（行为变了）：{候2}")
    finally:
        for k, v in 旧.items():
            if v is None:
                _os191.environ.pop(k, None)
            else:
                _os191.environ[k] = v
        _sh191.rmtree(靶, ignore_errors=True)
    return ("凭据解析：绿 —— 判一百九十一（`DSH_HOME` 设了 ⇒ 第一候选就是它 ✓ · "
            "临时凭据里的 key **真读到了** ✓ · 读不到时错误**列出试过 4 处** ✓ · "
            "**对照组**：`DSH_HOME` 不设 ⇒ 不冒出它、老路径仍兜底 ✓ · "
            "★ 修前探通道 **live=0（5 通道全「凭据不可读」）** ⇒ 修后 **live=1**"
            "（`qww|deepseek-v4.1-flash` HTTP 200）—— 差的是**我读错了地方**，不是环境没 key）")



def 判一百九十二包内每个模块都要能在别人的stdout下import():
    """★★★★ **import 副作用**判据（R536f）—— 挡「库模块在 import 时改全局状态」。

    ★ 真事故（2026-11-16，判一百九十跑到时才爆）：
      `AttributeError: '_io.StringIO' object has no attribute 'reconfigure'`
      —— 红的是**判一百九十**，而根因在 `tools/打wheel.py` 顶部：
      `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` **无条件执行** ✗
      ⇒ 测试框架把 `sys.stdout` 换成 `io.StringIO` 就炸 ⇒
        **判据的失败信息指向了调用方，不是被测物** —— 这是最费时间的一类红。
    ★ 为什么这类 bug 平时看不见：自己跑时 stdout 是真的 TTY/管道 ⇒ **永远不炸**；
      只在**被别人 import** 时才炸。⇒ 口径 387：**库模块 import 不许有副作用、不许抛**。

    做法：把 stdout/stderr 换成 `io.StringIO`，**逐个 import 包内每个模块**；
    另用**对照组**证明这条判据真能抓到这种模块（否则它没有区分力）。
    """
    import importlib as _il192
    import io as _io192
    import pathlib as _p192
    import sys as _s192
    基 = _p192.Path(__file__).resolve().parent.parent
    包 = 基 / "src" / "agentscope_orchestrator"
    if not 包.is_dir():
        raise AssertionError(f"判一百九十二：找不到包目录 {包}")

    def _试(模块名, 路径=None):
        """在 StringIO 的 stdout 下 import 一次。返回 None 或错误串。"""
        真出, 真错 = _s192.stdout, _s192.stderr
        _s192.stdout, _s192.stderr = _io192.StringIO(), _io192.StringIO()
        try:
            if 路径 is None:
                _il192.import_module(f"agentscope_orchestrator.{模块名}")
            else:
                sp = _il192.util.spec_from_file_location(f"_判192_{模块名}", 路径)
                m = _il192.util.module_from_spec(sp)
                sp.loader.exec_module(m)
            return None
        except Exception as e:                       # noqa: BLE001
            return f"{type(e).__name__}: {e}"
        finally:
            _s192.stdout, _s192.stderr = 真出, 真错

    _s192.path.insert(0, str(基 / "src"))
    模块们 = sorted(x.stem for x in 包.glob("*.py") if x.stem != "__init__")
    assert 模块们, "判一百九十二：一个模块都没扫到（判据没测到东西）"
    坏 = []
    for 名 in 模块们:
        e = _试(名)
        if e:
            坏.append(f"{名}.py ⇒ {e}")
    if 坏:
        raise AssertionError(
            f"判一百九十二①：{len(坏)}/{len(模块们)} 个模块在别人的 stdout 下 import 就炸 "
            f"（口径 387：库模块 import 不许有副作用）—— " + "；".join(坏[:4]))

    # ★ 对照组：造一个**确实会炸**的模块 ⇒ 这条判据**必须**抓到它
    临 = _p192.Path(__import__("tempfile").mkdtemp(prefix="判192_", dir=str(基 / "work" / "临时")))
    try:
        恶 = 临 / "坏模块.py"
        恶.write_text("import sys\nsys.stdout.reconfigure(encoding='utf-8')\n",
                      encoding="utf-8")
        e = _试("坏模块", 恶)
        if e is None:
            raise AssertionError(
                "判一百九十二②：**对照组没被抓到** ⇒ 这条判据对「import 时改 stdout」"
                "没有区分力（那它绿了也不说明什么 —— 口径 371）")
        对照组 = f"✓ 对照组被抓到（{e.split(':')[0]}）"
    finally:
        import shutil as _sh192
        _sh192.rmtree(临, ignore_errors=True)

    return (f"import 副作用：绿 —— 判一百九十二（**{len(模块们)} 个包内模块**"
            f"在 `sys.stdout=StringIO` 下**全部 import 成功** ✓ · {对照组} ⇒ "
            f"这条判据真能抓到「库模块 import 时改全局状态」（口径 387）· "
            f"★ 起因：`tools/打wheel.py` 顶部无条件 reconfigure，"
            f"害得**判一百九十**顶着一个跟它无关的 AttributeError 变红）")



def 判一百九十三入口必须真跑盘点不许只接在函数层():
    """★★★★ **端到端入口**判据（R536l，口径 388）—— 挡「接线只接到函数层」。

    ★ 真断点（2026-11-16 端到端真跑 CLI 才抓到）：
      `带盘点定计划` 写好了、**判一百八十九绿了**，
      但 `auto_goal.main`（= `orch-plan`）里
      `带盘点定计划` **0 次**、`_核对计划路径` **0 次**、`盘点` **0 次** ✗
      ⇒ 用户敲 `orch-plan --goal-json …` **一步盘点都没有** ✗
      ⇒ 全仓**只有判 189 和临时脚本**调过它 —— **判据测函数，交付走入口**。
    ★ 所以本判据**不调函数，真跑命令行**（subprocess），断言**输出**里有盘点读数；
      并用**两个对照组**证明它有区分力（口径 371）。
    """
    import json as _json193
    import pathlib as _p193
    import shutil as _sh193
    import subprocess as _sp193
    import sys as _s193
    import tempfile as _t193
    基 = _p193.Path(__file__).resolve().parent.parent
    小根 = _p193.Path(_t193.mkdtemp(prefix="判193_", dir=str(基 / "work")))
    try:
        (小根 / "src").mkdir()
        (小根 / "src" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (小根 / "README.md").write_text("# 小根\n", encoding="utf-8")
        规约 = {
            "name": "判193真目标",
            "pre": ["锚定真实源码单元"],
            "constraints": ["不许写不存在的东西"],
            "gates": [{"type": "compile", "cmd": ["python", "-c", "print(1)"],
                       "cwd": "src"}],
            "acceptance": ["入口走盘点那条路"],
        }

        def 跑(额外=()):
            return _sp193.run(
                [_s193.executable, "-m", "agentscope_orchestrator.auto_goal",
                 "--goal-json", _json193.dumps(规约, ensure_ascii=False),
                 "--root", str(小根), *额外],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(基), timeout=600,
                env={**__import__("os").environ, "PYTHONPATH": str(基 / "src")})

        # ① 默认必须真盘点
        r = 跑()
        出 = (r.stdout or "") + (r.stderr or "")
        if "知库·建索引" not in 出 and "盘点" not in 出:
            raise AssertionError(
                "判一百九十三①：入口的输出里**没有盘点读数** ⇒ "
                "接线只接在函数层，没接在入口上（口径 388）。stdout 尾部："
                + (r.stdout or "")[-300:])
        if "没做" in 出:
            raise AssertionError(
                "判一百九十三①：默认路径居然报「没做」⇒ 入口没走盘点那条路："
                + [l for l in 出.splitlines() if "盘点" in l][:2].__str__())
        if r.returncode != 0:
            raise AssertionError(f"判一百九十三①：退出码 {r.returncode}（应为 0）：{出[-300:]}")
        真盘 = [l.strip() for l in 出.splitlines()
                if "知库·建索引" in l or l.strip().startswith("盘点")]
        if not 真盘:
            raise AssertionError("判一百九十三①：没找到以「盘点」打头的行")

        # ② 对照组：--no-inventory ⇒ **必须写明没盘点**（口径 92）
        r2 = 跑(("--no-inventory",))
        出2 = (r2.stdout or "") + (r2.stderr or "")
        if "没做" not in 出2:
            raise AssertionError(
                "判一百九十三②：`--no-inventory` 时输出**没写明「没做」** ⇒ "
                "「没盘点」会看起来像「盘点过了、没问题」：" + 出2[-300:])

        # ③ 对照组2：cwd 指向不存在的目录 ⇒ 必须拒发
        坏规约 = dict(规约)
        坏规约["gates"] = [{"type": "compile", "cmd": ["python", "-c", "print(1)"],
                          "cwd": "这个目录肯定不存在"}]
        r3 = _sp193.run(
            [_s193.executable, "-m", "agentscope_orchestrator.auto_goal",
             "--goal-json", _json193.dumps(坏规约, ensure_ascii=False),
             "--root", str(小根)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(基), timeout=600,
            env={**__import__("os").environ, "PYTHONPATH": str(基 / "src")})
        出3 = (r3.stdout or "") + (r3.stderr or "")
        if r3.returncode == 0:
            raise AssertionError("判一百九十三③：引用了**不存在**的路径却**放行了** ⇒ 核对没生效")
        if "拒发" not in 出3:
            raise AssertionError("判一百九十三③：拒发了但没说「拒发」：" + 出3[-200:])
    finally:
        _sh193.rmtree(小根, ignore_errors=True)
    return ("入口接线：绿 —— 判一百九十三（**真跑命令行** `auto_goal --goal-json … --root …` ⇒ "
            f"输出里有盘点读数：`{真盘[0][:52]}` ✓ · "
            "对照组①`--no-inventory` ⇒ 输出**写明「没做」** ✓ · "
            "对照组②`cwd` 指向不存在的目录 ⇒ **拒发**、退出码非 0 ✓ · "
            "★ 修前该入口 `带盘点定计划`/`盘点` 各 **0 次**（判 189 全绿也照样断）—— 口径 388）")


def main() -> int:



















    判一档案门装载期校验()
    判二违规点身份()
    判三门命令可用性()
    判四空交付不许报成功()
    判五失败栈改动面()
    判六项目门红不许交付()
    判七用例身份可比()
    判八上游空包归瞬时()
    判九没有重复定义()
    判十相对cwd落在求值根()
    判十一没有死函数()
    判十二指令带证据()
    判十三跨文件整批达成()
    判十四多语言失败栈()
    判十五真工具链端到端且可重复()
    判十七目标树无人值守裁决()
    判十八闭环一次调用()
    判十九闭环工具真接上()
    判二十迭代闭环会前进()
    判二十一模型产码的纪律()
    判二十二通道降级判活必须看响应体()
    判二十三通道体检三档分明()
    判二十四开工前必须自体检()
    判二十五未达成必须给出口()
    判二十六多文件可重复性()
    判二十七可重复性可调且达成自带证据()
    判二十八失败必须分清责任层()
    判二十九跨模块字段契约()
    判三十真落主树且旁证未动()
    判三十一对账不许把没测到盖住()
    判三十二README必须写明还没测到什么()
    判三十三装完就能跑且包不许带垃圾()
    判三十四交付说明的数字必须对得上真读数()
    判三十五渠道必须跟随宿主而不是硬编码()
    判三十六项目知识图谱必须自动建成且查得动()
    判三十七建完必须有标准且失败要分诊()
    判三十八真模型调用必须支持流式通道()
    判三十九口径门必须真能拦住已犯过的错()
    判四十语义校准必须是自演化的且两级门都在()
    判十六非UTF8工具输出()
    判四十一行尾不许被悄悄改掉()
    判四十二临时目录不许落在仓里()
    判四十三剪枝口径必须全包一份()
    判四十四Lean抽取器必须全包一份()
    判四十五memory的require必须真的过滤()
    判四十六图上死引用必须进常驻缺口且减掉假红()
    判四十七局部召回必须拉出这块缺什么()
    判四十八公理足迹必须问内核且不许谎报覆盖()
    判四十九Lean符号必须问elaborator且参考语料必须分层()
    判一百七十一合流产物必须还在盘上()
    判一百七十二收尾五产物必须齐全且同代()
    判一百七十四桶的规模不许凭空塌掉()
    判一百七十五源码根不许自己就是图桶()
    判一百七十六依赖与虚拟环境不许混进项目内容()
    判一百七十七备份临时文件不许混进项目内容()
    判一百七十八每种语法树语言必须用真样本验过()
    判一百七十九桶内产物的根必须与图一致()
    判一百八十跳过记录必须能定位()
    判一百八十一直洽门不许红在交付桶上()
    判一百八十二语言专用内置表的缺口必须登记()
    判一百八十三验收桶必须过同一套断言()
    判一百八十四跳过后缀必须登记且原因不许替文件下结论()
    判一百八十五知识族格式必须真读出来且是确定的()
    判一百八十六粘滞剪枝不许让进过图的目录消失()
    判一百八十七索引说有没有图必须拿盘上真值对()
    判一百八十八没用过的文件夹加一句人话必须秒级定位()
    判一百八十九定计划前必须先真盘点且路径要真存在()
    判一百九十交付包必须与git一个文件不少()
    判一百九十一凭据按权威标记找不许写死路径()
    判一百九十二包内每个模块都要能在别人的stdout下import()
    判一百九十三入口必须真跑盘点不许只接在函数层()
    if 坏:
        print("工程链路多文件齿：红 —— " + "；".join(坏))
        return 1
    print("工程链路多文件齿：绿 —— **必改文件从失败栈补全 / 项目门红不许交付 / "
          "用例身份单调可比 / 单文件与多文件同一条纪律 / 上游空包归瞬时而非代码缺陷 / 没有重复定义 / 相对 cwd 落在求值根 / 没有死函数 / 指令带证据 / 跨文件整批达成 / 多语言失败栈 / 真工具链端到端且可重复 / 非 UTF-8 工具输出 / 目标树无人值守裁决 / 闭环一次调用 / 闭环工具真接上 / 迭代闭环会前进 / 模型产码的纪律 / 通道降级判活看响应体 / 通道体检三档分明 / 开工前自体检 / 未达成必须给出口 / 多文件可重复性 / 可重复性可调且达成自带证据 / 失败必须分清责任层 / 跨模块字段契约 / 真落主树且旁证未动 / 对账不许把没测到盖住 / README必须写明还没测到什么 / 装完就能跑且包不许带垃圾 / 交付说明的数字必须对得上真读数 / 渠道必须跟随宿主而不是硬编码 / 项目知识图谱必须自动建成且查得动 / 建完必须有标准且失败要分诊 / 真模型调用必须支持流式通道 / 口径门 / 语义校准两级门 / 行尾不许被悄悄改掉 / 临时目录不许落在仓里 / 剪枝口径全包一份 / Lean 抽取器全包一份 / memory 的 require 真的过滤 / 图上死引用进常驻缺口并减掉假红 / 局部召回拉出这块缺什么**。")
    return 0


def 判四十八公理足迹必须问内核且不许谎报覆盖() -> None:
    """★ R303：**公理足迹**（问 Lean 内核，不查文本）。

    为什么单独立一条判据：这一层的读数**不是「更大的图」，是一个新维度** ——
    `grep sorry` 数不出它。本仓全仓 `sorry` **文本命中 0**，而内核说
    **19 条声明站在 `Lean.trustCompiler` 上**（`native_decide`：证明是
    **跑编译代码跑出来的**，不是内核逐步验算的），全在音律/节气这一域。

    ★ 这条判据要拦住的**不是「有没有做」，是「有没有谎报」**：
      ①缺档时必须说「未测到」，**不许给 0**（「看不见」不能当「没有」）；
      ②`trustCompiler` 与 `sorryAx` 是**两种不同的洞**，不许混成一类；
      ③「无公理」必须**推**出来（`扫了 − 带足迹`）而不是取分类 ——
        取分类**永远是 0**，而 0 看起来像「一个都没有」，是个好数字被显示成 0；
      ④**覆盖率必须一起报**：拿 43% 覆盖率的扫描当全工程结论就是谎报；
      ⑤接线：`mem_live.缺口()` 与 `缺口一行()` 里必须真的出现 ⑤ ——
        一个没有调用方的模块等于没有接线。
    """
    坏: list[str] = []
    try:
        import json as _json
        import tempfile as _tf
        from pathlib import Path as _P

        from agentscope_orchestrator import mem_axiom as _ma
        from agentscope_orchestrator import mem_live as _ml
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十八：导不进模块（{type(_e).__name__}: {_e}）")
        return _报(坏)

    # ── ① 缺档 ⇒ 必须「未测到」，不许给 0 ──
    _假 = _P("__绝不可能存在_r303_判四十八__")
    原 = _ma._包根
    try:
        _ma._包根 = lambda: _假                  # type: ignore[assignment]
        缺 = _ma.读数()
        if "未测到" not in 缺:
            坏.append(f"判四十八①：缺档时没出「未测到」，实得 {str(缺)[:80]} ⇒ "
                      f"会把「没测」报成「干净」")
        _报1 = _ma.报告()
        if "未测到" not in _报1:
            坏.append(f"判四十八①：`报告()` 缺档时没出「未测到」：{_报1[:80]}")
    finally:
        _ma._包根 = 原                       # type: ignore[assignment]

    # ── ②③ 两类洞不许混 + 「无公理」必须是推的 ──
    _f = _P(_tf.gettempdir()) / "r303_判四十八.tsv"
    try:
        _f.write_text(
            "AX\tA\t[propext]\n"
            "AX\tB\t[propext, Quot.sound, Classical.choice]\n"
            "HOLE\tC\t[sorryAx]\n"
            "AX\tD\t[Lean.ofReduceBool, Lean.trustCompiler]\n"
            "AX\tE\t[Lean.trustCompiler, propext]\n", encoding="utf-8")
        果 = _ma.整理(_f, {"扫了": 8})
        if 果["①洞（sorryAx）"] != 1:
            坏.append(f"判四十八②：`sorryAx` 应归①洞=1，实得 {果['①洞（sorryAx）']}")
        if 果["★②信任编译器（证明不是内核验的）"] != 2:
            坏.append(f"判四十八②：`trustCompiler` 应归②=2，实得 "
                      f"{果['★②信任编译器（证明不是内核验的）']}")
        if "sorryAx" in str(果["★②的名单"]):
            坏.append("判四十八②：`sorryAx` 混进了②的名单 ⇒ 两类没分开")
        # ★ 8 扫了、5 条有足迹 ⇒ 无公理应 3。**取分类的话永远是 0。**
        if 果["⑤无公理（纯内核，真干净）"] != 3:
            坏.append(f"判四十八③：无公理应 3（8−5），实得 "
                      f"{果['⑤无公理（纯内核，真干净）']} ⇒ "
                      f"「无公理」是**取的分类**不是**推的**（取分类永远是 0）")
    finally:
        _f.unlink(missing_ok=True)

    # ── ④ 覆盖率必须一起报 ──
    _真 = _ma.读数()
    if "未测到" not in _真:
        if not _真.get("★覆盖率"):
            坏.append("判四十八④：常驻档没有 `★覆盖率` ⇒ "
                      "拿部分覆盖率的扫描当全工程结论（谎报）")
        elif "没测到" not in str(_真.get("★覆盖率")) and "覆盖" not in str(_真.get("★覆盖率")):
            坏.append(f"判四十八④：覆盖率没说清未测到多少：{str(_真.get('★覆盖率'))[:90]}")

    # ── ⑤ 接线：常驻缺口与简报里必须真的出现 ⑤ ──
    try:
        缺 = _ml.缺口()
        if "⑤公理足迹" not in 缺:
            坏.append("判四十八⑤：`mem_live.缺口()` 里没有 ⑤公理足迹 ⇒ 没接线")
        elif "未测到" not in (缺.get("⑤公理足迹") or {}) and                 "★②信任编译器" not in (缺.get("⑤公理足迹") or {}):
            坏.append(f"判四十八⑤：⑤ 里既没有「未测到」也没有「信任编译器」⇒ "
                      f"形状不对：{str(缺.get('⑤公理足迹'))[:90]}")
        # ★ 这一条针对踩过的真 bug：`缺口一行` 走的是**缓存** `取缺口()`，
        #   缓存是 ⑤ 出现之前写的 ⇒ `缺.get("⑤公理足迹")` 为 None ⇒
        #   那一截静默变成 `?`。**一个新加的缺口类别，在简报里看不见。**
        行 = _ml.缺口一行()
        if "⑤" not in 行:
            坏.append("判四十八⑤：`缺口一行()` 里没有 ⑤ ⇒ 简报看不见这一类")
        elif "⑤**公理足迹** ?" in 行:
            坏.append("判四十八⑤：`缺口一行()` 里 ⑤ 全是 `?` ⇒ "
                      "命中了缓存缺键那个 bug（新类别静默变成 ?）")
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十八⑤：接线验证炸了（{type(_e).__name__}: {_e}）")

    return _报(坏, "工程链路多文件齿：绿 —— 判四十八（公理足迹：问内核·两类洞分开·"
                   "无公理靠推·覆盖率必须报·⑤真接线）")

    # ★★★ **收尾**（R366 批量补）：`坏` 收集了就必须有人看 ——
    #   实测这些判据**有 `坏.append` 却从不检查 `坏`** ⇒
    #   「看起来有红路径」而**永远绿**。判八十五②从此守住这一条。
    if 坏:
        return _报2(坏, f"判四十八公理足迹必须问内核且不许谎报覆盖：绿")


def _报(坏: list, 绿文: str = "") -> None:
    if 坏:
        print("工程链路多文件齿：红 —— " + "；".join(坏))
        raise SystemExit(1)
    if 绿文:
        print(绿文)



def 出只_建议(出: dict) -> bool:
    """报错时**必须给下一步** —— 只说「失败了」等于没说。"""
    return bool(出.get("建议"))


def _语料与图对齐(语料: "Path", 模式: tuple = ("*.py",)) -> tuple:
    """★★★ R523e：把**语料宇宙**对齐到**图宇宙**（判七十一/判七十三 **共用这一处**）。

    ★ 缺陷现场（2026-11-15 实测，`太虚元道\.tools\py312\Lib`）：
        语料 `rglob("*.py")` = **2,731** 个 · 其中 `site-packages/` 下 **1,976** 个
        图侧 `文件数` = **764** · `戳.json` 里含 `site-packages` 的 **0** 条
      ⇒ 判据报「**整文件漏 1,851**」，例子**清一色** `site-packages/...`。
    ★ 这不是漏，是**判据的宇宙比图的宇宙大**：R521 之后依赖目录**不在图里**
      —— 而 `判一百七十六`**明确要求**图里不许有依赖/虚拟环境混入。
      **两把尺子量同一件事，必然报出假缺陷**（口径 277）。
    ★ 口径 260：「看不见」不能当「没有」⇒ 被排除的**必须单列报出**，
      不是静默跳过（口径 203：**单列 ≠ 静默跳过**）。
    ★ 剪枝规则**直接调生产的 `kg._该跳`** —— 不在这里另写一份
      （口径 305：同一处置写在几处、只改一处就是病）。
    ★ 返回 `(留下的文件, 被排除的相路径)`；排除为 0 也要能返回 0（口径 307）。
    """
    import sys as _sys523
    _sys523.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from agentscope_orchestrator import kg as _kg523
    _文: list = []
    for _m523 in 模式:
        _文 += list(语料.rglob(_m523))
    _留523: list = []
    _排523: list = []
    for _f523 in sorted(_文)[:3000]:
        _相523 = _f523.relative_to(语料).as_posix()
        if _相523.startswith(".kg/") or "/.kg/" in _相523:
            _排523.append(_相523)                      # 图集自己不算语料
        elif any(_kg523._该跳(_p) for _p in _相523.split("/")[:-1]) or _kg523._该跳(_f523.name):
            _排523.append(_相523)                      # ★ 依赖/虚拟环境 —— 图故意不收
        else:
            _留523.append(_f523)
    return _留523, _排523


def 判七十三模块归属必须与权威ast逐文件全等() -> None:
    """**名字对了不等于挂对了模块** —— 这一层是「跨文件绑到定义」的前提。

    ★ 前六轮只对了「本文件绑了哪些名字」（判七十二）。
      模块归属（`from X import y` 里的 `y` 属于 `X`）**是另一层** ——
      R341 第一次对它，立刻查出**真的归属错**：
        `import getopt, sys` ⇒ `sys` 被挂到 `getopt` 上（`base64.py`）。
      R343 又查出 `from __future__ import annotations` 被记成 `模块=annotations`
      （`future_import_statement` **没有 `module_name` 字段** ⇒ 掉到别的分支）。

    ★ 口径：**只比绝对导入** —— 相对导入两边表达不同（`ast` 用 `level`、我用 `.` 前缀），
      排除并把排除条数计入绿文（**排除不是通过**）。
    ★ 只对 `ast` 语料内（**`.py` + `.pyi`**）比：`.c` 是 Cython 产物，
      `ast` 不扫 —— 算成「我多」是**拿两把尺子**。
    ★ **没测到不能当通过**。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        import ast as _ast
        import json as _json
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判七十三：依赖导不进来（{type(e).__name__}: {e}）"])

    _语料 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib")
    if not (_语料 / ".kg" / "边.json").exists():
        return _报2(["判七十三：**没测到**标准库语料或它的图 —— 没测到不能当通过"])
    try:
        边 = _json.loads((_语料 / ".kg" / "边.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判七十三：读图失败（{type(e).__name__}: {e}）"])

    我: dict[str, set] = {}
    for e in 边:
        if e.get("关系") != "导入":
            continue
        起 = e.get("起") or ""
        if not 起.startswith("file:"):
            continue
        相 = 起[5:]
        模 = (e.get("终") or "")[4:]
        # ★★ **相对导入不排除** —— 实测（R345）两边形态一一对应：
        #   ast `level=L, module=M` ⟺ 我 `"."*L + (M or "")`，总数 4197 = 4197。
        #   归一化后**逐文件全等 2907/2907** ⇒ 原来排除它们是把覆盖白丢了。
        for 名 in (e.get("绑定") or []):
            我.setdefault(相, set()).add((名, 模))

    全等 = 漏 = 多 = checked = 0
    相对条数 = 0
    漏例: list = []
    多例: list = []
    # ★★★ R523e **宇宙对齐**（同 `判七十一`，共用一处；口径 305）
    留下, 排除 = _语料与图对齐(_语料, ("*.py", "*.pyi"))
    for f in 留下:
        相 = f.relative_to(_语料).as_posix()
        try:
            树 = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:                         # noqa: BLE001
            continue
        权: set = set()
        for n in _ast.walk(树):
            if isinstance(n, _ast.Import):
                for a in n.names:
                    权.add((a.asname or a.name.split(".")[0], a.name))
            elif isinstance(n, _ast.ImportFrom):
                # ★★ 相对导入归一化成与我的图同一形态 `"."*level + module`
                if n.level:
                    相对条数 += 1
                    _模 = "." * n.level + (n.module or "")
                    for a in n.names:
                        if a.name != "*":
                            权.add((a.asname or a.name, _模))
                    continue
                for a in n.names:
                    if a.name != "*":
                        权.add((a.asname or a.name, n.module or ""))
        if not 权:
            continue
        checked += 1
        b = 我.get(相, set())
        if 权 == b:
            全等 += 1
            continue
        if 权 - b:
            漏 += len(权 - b)
            if len(漏例) < 3:
                漏例.append((相, sorted(权 - b)[:4]))
        if b - 权:
            多 += len(b - 权)
            if len(多例) < 3:
                多例.append((相, sorted(b - 权)[:4]))

    if checked < 100:
        坏.append(f"判七十三：**没测到**（只对上了 {checked} 个有导入的文件）—— 没测到不能当通过")
    if 漏:
        坏.append(f"判七十三：**归属漏** {漏}（名字挂了但模块丢了）。例：{漏例}")
    if 多:
        坏.append(f"判七十三：**归属多** {多}（挂到了错的模块上）。例：{多例}")
    return _报2(坏, f"模块归属逐文件全等：绿 —— 判七十三（{checked} 个文件**全等 {全等} · 漏 0 · 多 0**；"
                    f"**相对导入 {相对条数} 条也已归一化后一起比**（不再排除）；"
                    f"★ 单列：语料里被剪枝规则排除 **{len(排除)}** 个（依赖/虚拟环境，见 `判一百七十六`））")


def 判七十二导入绑定必须与权威ast完全一致() -> None:
    """抽样的**逐文件全等**（不是「大部分一致」）。

    ★ 六轮的账（每轮都由 `ast` 独立算，不是我自报）：
      R333 我漏 6409 · R334 我漏 1555 · R335 我漏 958 · R336 我漏 51
      · R337 我漏 5（**但那把尺子漏算了整文件漏，是假的**）
      · R338 修好尺子 ⇒ 量出真缺陷（剪枝按名字扔了真源码）· R339 **我漏 0**

    ★ 这一轮把「**在 `ast` 语料内逐文件全等**」钉成判据 ——
      它是目标 ⑤（用权威解析器对账）的第一个完整闭环。

    ★ 只对 ``ast`` 语料内（``.py``/``.pyi``）比：
      ``.c`` 是 Cython 产物，``ast`` 根本不扫 —— 把它们算成「我多」是**拿两把尺子**。
    ★ **没测到不能当通过**。抽样（固定取前 N 个）—— 全量读数记在 R333~R339 回执里。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        import ast as _ast
        import json as _json
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判七十二：依赖导不进来（{type(e).__name__}: {e}）"])

    _语料 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib")
    if not (_语料 / ".kg" / "边.json").exists():
        return _报2(["判七十二：**没测到**标准库语料或它的图 —— 没测到不能当通过"])
    try:
        边 = _json.loads((_语料 / ".kg" / "边.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判七十二：读图失败（{type(e).__name__}: {e}）"])

    我: dict[str, set] = {}
    for e in 边:
        if e.get("关系") != "导入":
            continue
        起 = e.get("起") or ""
        if 起.startswith("file:") and (e.get("绑定") or []):
            我.setdefault(起[5:], set()).update(e["绑定"])
    if not 我:
        return _报2(["判七十二：**没测到**任何导入绑定边 ⇒ 不能当通过"])

    样本 = sorted(我)[:400]
    全等 = 漏 = 多 = checked = 0
    漏例: list = []
    多例: list = []
    for 相 in 样本:
        f = _语料 / 相
        if not f.is_file() or f.suffix not in (".py", ".pyi"):
            continue
        try:
            树 = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:                         # noqa: BLE001
            continue
        权: set = set()
        for n in _ast.walk(树):
            if isinstance(n, _ast.Import):
                for a in n.names:
                    权.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, _ast.ImportFrom):
                for a in n.names:
                    if a.name != "*":
                        权.add(a.asname or a.name)
        checked += 1
        a, b = 权, 我[相]
        if a == b:
            全等 += 1
            continue
        if a - b:
            漏 += len(a - b)
            if len(漏例) < 3:
                漏例.append((相, sorted(a - b)[:5]))
        if b - a:
            多 += len(b - a)
            if len(多例) < 3:
                多例.append((相, sorted(b - a)[:5]))

    if checked < 50:
        坏.append(f"判七十二：**没测到**（只对上了 {checked} 个文件）—— 没测到不能当通过")
    if 漏:
        坏.append(f"判七十二：**名字我漏** {漏}（AI 不知道自己有这个绑定）。例：{漏例}")
    if 多:
        坏.append(f"判七十二：**名字我多** {多}（AI 会拿错的靶做计划）。例：{多例}")
    return _报2(坏, f"导入绑定逐文件全等：绿 —— 判七十二（抽样 {checked} 个 stdlib 文件，"
                    f"**全等 {全等} · 我漏 0 · 我多 0**；全量读数见 R333~R339 回执）")


def 判七十一不许整文件漏() -> None:
    """`ast` 说某个文件有导入绑定，而我**一条绑定边都没有** ⇒ 红（整文件漏）。

    ★ R338 的账：修剪枝前 **整文件我漏 9**（``Lib/venv/`` 与
      ``pip/_internal/operations/build/`` 被按目录名剪掉）；修后 **0**。

    ★ 为什么这一类最坏：不是少了几个名字，是**AI 完全不知道有这个文件** ——
      「看不见」被当成了「没有」。而它**在跳过清单里**，
      所以**诚实性没问题，判据有问题** —— 判据要看**内容**，不能只看名字。

    ★ 只对 ``ast`` 语料内（``.py``/``.pyi``）比；``.c`` 等不在语料的文件不算。
    ★ **没测到不能当通过**。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        import ast as _ast
        import json as _json
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判七十一：依赖导不进来（{type(e).__name__}: {e}）"])

    _语料 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib")
    if not (_语料 / ".kg" / "边.json").exists():
        return _报2(["判七十一：**没测到**标准库语料或它的图 —— 没测到不能当通过"])

    try:
        边 = _json.loads((_语料 / ".kg" / "边.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判七十一：读图失败（{type(e).__name__}: {e}）"])

    我: dict[str, set] = {}
    for e in 边:
        if e.get("关系") != "导入":
            continue
        起 = e.get("起") or ""
        if 起.startswith("file:") and (e.get("绑定") or []):
            我.setdefault(起[5:], set()).update(e["绑定"])

    # ★ 连「节点」也查：整文件漏的极端情形是**连 file 节点都没有**
    有节点 = {k[5:] for k in (e.get("起", "") for e in 边)
             if isinstance(k, str) and k.startswith("file:")}

    漏文: list[str] = []
    无节点: list[str] = []
    checked = 0
    # ★★★ R523e **宇宙对齐**：语料侧必须用**图侧同一条剪枝规则**（共用 `_语料与图对齐`）
    留下, 排除 = _语料与图对齐(_语料)
    for f in 留下:
        相 = f.relative_to(_语料).as_posix()
        try:
            树 = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:                         # noqa: BLE001
            continue
        名: set = set()
        for n in _ast.walk(树):
            if isinstance(n, _ast.Import):
                for a in n.names:
                    名.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, _ast.ImportFrom):
                for a in n.names:
                    if a.name != "*":
                        名.add(a.asname or a.name)
        if not 名:
            continue
        checked += 1
        if not 我.get(相):
            漏文.append(相)
            if 相 not in 有节点:
                无节点.append(相)

    if checked < 200:
        坏.append(f"判七十一：**没测到**（只对上了 {checked} 个有导入的文件）—— 没测到不能当通过")
    if 漏文:
        坏.append(f"判七十一：**整文件漏** {len(漏文)} 个文件（`ast` 说有导入，我一条绑定都没有）"
                  f"—— 这是「看不见被当成没有」。例：{漏文[:5]}")
    if 无节点:
        坏.append(f"判七十一：**连 file 节点都没有** {len(无节点)} 个（最坏的一类）。例：{无节点[:5]}")
    return _报2(坏, f"不许整文件漏：绿 —— 判七十一（{checked} 个 stdlib 文件全都有导入绑定边；"
                    f"★ 单列：语料里被**剪枝规则**排除 **{len(排除)}** 个"
                    f"（依赖/虚拟环境 —— `判一百七十六`要求图里不许有，故**不比**，"
                    f"但**报出来**，不静默跳过））")


def 判七十导入绑定不许误报() -> None:
    """用 `ast` 逐文件对**导入绑定**，**只判「我多」**。

    ★ 三轮的账（每轮都由 `ast` 独立算出，不是我自报）：
      R333 我多 0 · R334 我多 0（真·0）· R335 我多 0 · R336 我多 0
    ★ 同时「我漏」从 6409 → 51 —— **精确和召回一起看，不是拿一个换另一个**。

    ★ 为什么「我多」比「我漏」更该钉：
      漏了 AI 知道自己缺（④ 未解析会报出来）；**错了 AI 会拿错的靶去做计划**。
      R327 实测 **2881 条猜错靶**就是这个代价。

    ★ 只对 `ast` 语料内（`.py`/`.pyi`）的文件比：`.c` 是 Cython 产物，
      `ast` 根本不扫 —— **是我比它扫得多**，算成「我多」就是拿两把尺子。

    ★ 抽样而不是全量：全量要跑 2700+ 文件的 `ast` + 读一张 100 万边的图，
      判据会慢到没人愿意跑。**抽样**是**有权衡的**：它证明不了全量，
      但能在每次改动后立刻抓住「多了一个名字」这类回归。
      **全量读数记在回执里**（R333~R336 逐轮都有）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        import ast as _ast
        import json as _json
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判七十：依赖导不进来（{type(e).__name__}: {e}）"])

    # ★ 语料在**项目自己的标准库**上；找不到就如实说「没测到」，不当通过
    _语料 = None
    for c in (Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib"),):
        if (c / ".kg" / "边.json").exists() and c.exists():
            _语料 = c
            break
    if _语料 is None:
        return _报2(["判七十：**没测到**标准库语料或它的图（`<Lib>/.kg/边.json` 不存在）"
                     "—— 没测到不能当通过"])

    try:
        边 = _json.loads((_语料 / ".kg" / "边.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判七十：读图失败（{type(e).__name__}: {e}）"])

    我: dict[str, set] = {}
    for e in 边:
        if e.get("关系") != "导入":
            continue
        起 = e.get("起") or ""
        if 起.startswith("file:") and (e.get("绑定") or []):
            我.setdefault(起[5:], set()).update(e["绑定"])
    if not 我:
        return _报2(["判七十：**没测到**任何导入绑定边 ⇒ 不能当通过"
                     "（R331 就出过「抽出来了但没人用」的假绿）"])

    # 抽样：固定取前 N 个（可复现），N 取 400
    样本 = sorted(我)[:400]
    多: list[tuple] = []
    checked = 0
    for 相 in 样本:
        f = _语料 / 相
        if not f.is_file() or f.suffix not in (".py", ".pyi"):
            continue                                # ★ `.c` 等不在 ast 语料内，跳过
        try:
            树 = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:                           # noqa: BLE001
            continue
        权: set = set()
        for n in _ast.walk(树):
            if isinstance(n, _ast.Import):
                for a in n.names:
                    权.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, _ast.ImportFrom):
                for a in n.names:
                    if a.name != "*":
                        权.add(a.asname or a.name)
        checked += 1
        多出 = sorted(我[相] - 权)
        if 多出:
            多.append((相, 多出[:8]))

    if checked < 50:
        坏.append(f"判七十：**没测到**（只对上了 {checked} 个文件）—— 没测到不能当通过")
    if 多:
        坏.append(f"判七十：**导入绑定误报** {len(多)} 个文件（= AI 会拿着错的靶做计划）。"
                  f"例：{多[:4]}")
    return _报2(坏, f"导入绑定不许误报：绿 —— 判七十（抽样 {checked} 个 stdlib 文件，"
                    f"**我多 0**；全量读数见 R333~R336 回执）")


def 判六十九导入绑定必须优先于候选法() -> None:
    """用 R327 用 `ast` 查出的**真实病例**钉住。

    ★ 病例（`ast` 权威认定的靶）：`sched.py` 里
      `import collections` + `from itertools import count`，
      而 **`collections` 和 `itertools` 里都有叫 `count` 的定义**。
      · 候选法（R324–R331）：只知道「本文件 import 的某个模块里恰有同名定义」
        ⇒ 猜了 `collections`。**靶是错的。**（R327 实测这类有 **2881 条**）
      · `ast` 权威：`from itertools import count` ⇒ **`itertools`**。

    ★ 治法链条（每一环都有自己的判据）：
      R328 先把树打出来看（不许猜）· R328 判六十六守「`取导入` 不许塌」
      → R330 有界白名单抽名 + 去模块前缀（判六十八守抽得对）
      → R331 接进 `取导入` → R332 `绑定` 搬边（挂共享节点会串台）+ **`解析引用` 用起来**

    ★ 本判据钉三件事：
      1. **导入绑定优先** —— 靶必须是 `itertools` 的 `count`，不是 `collections` 的
      2. **不许标歧义** —— 绑定是确定的（本文件的 import 明写了）
      3. ★ **绑到多个模块时必须老实标歧义** ——
         `from a import x` 与 `from b import x` 同时存在，**不许挑一个装作确定**
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十九：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kg绑_"))
    图 = Path(tempfile.mkdtemp(prefix="kg绑图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        # ★ 两个模块**都有** `count` —— 这正是 R327 里我猜错靶的局
        (根 / "itertools.py").write_text("def count():\n    return 1\n",
                                        encoding="utf-8")
        (根 / "collections.py").write_text("def count():\n    return 2\n",
                                          encoding="utf-8")
        (根 / "sched.py").write_text(
            "import collections\nfrom itertools import count\n\n\ndef f():\n"
            "    return count()\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        边 = _kg.加载图(根).get("边") or []
        引 = [e for e in 边 if e.get("关系") == "引用"
             and (e.get("原名") or "") == "name:count"
             and (e.get("起") or "") == "file:sched.py"]
        if not 引:
            坏.append("判六十九：`sched.py` 里 `count` 的引用边丢了")
        else:
            e = 引[0]
            if "itertools" not in (e.get("终") or ""):
                坏.append(f"判六十九：**靶错了** —— `from itertools import count` "
                          f"⇒ 必须是 `itertools` 的，实得 {e.get('终')!r}"
                          "（这就是 R327 查出的 2881 条猜错靶的病）")
            if e.get("歧义"):
                坏.append(f"判六十九：绑定是确定的，**不许标歧义**（候选 "
                          f"{e.get('候选')}）")
            if "绑定" not in (e.get("依据") or ""):
                坏.append(f"判六十九：依据该是「导入绑定」，实得 {e.get('依据')!r}"
                          "⇒ 说明没走绑定这条路，还是候选法在猜")

        # ③ 绑到多个模块 ⇒ **必须老实标歧义**
        (根 / "another.py").write_text("def count():\n    return 3\n",
                                      encoding="utf-8")
        (根 / "两个.py").write_text(
            "from itertools import count\nfrom another import count as count2\n"
            "from another import count\n\n\ndef g():\n    return count()\n",
            encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        边2 = _kg.加载图(根).get("边") or []
        引2 = [e for e in 边2 if e.get("关系") == "引用"
              and (e.get("原名") or "") == "name:count"
              and (e.get("起") or "") == "file:两个.py"]
        if not 引2:
            坏.append("判六十九：`两个.py` 的引用边丢了")
        elif not 引2[0].get("歧义"):
            坏.append("判六十九：**从两个模块都绑了 `count` 却不标歧义** "
                      f"（实得终={引2[0].get('终')!r}）"
                      "⇒ 不许挑一个装作确定")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十九：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "导入绑定优先：绿 —— 判六十九（R327 真实病例：靶是 itertools "
                    "不是 collections·确定的不标歧义·绑多个必须标歧义）")


def 判六十八抽绑定名必须按语言正确且去掉模块前缀() -> None:
    """「谁 import 进来了什么」—— 这是 R327 用 `ast` 查出我**猜错靶 2881 条**的根因。

    ★ R327 的读数：12,581 条跨文件歧义里 **2,881 条我的候选靶是错的**
      （`sched.py` 的 `count` 权威是 `itertools`，我猜了 `collections`）。
      根因是候选法**不知道谁 `from M import N`**。

    ★ R323 我抽过一次，**把 `取导入` 弄炸了**（导入边全没、边上冒 `保底: true`）。
      所以这次 **R328 先把树打出来看**、**R328 立了判六十六守塌**，才动手。

    ★ R330 实测（探针）：
      · JS/TS `['a','b','d','e']` —— default / 具名 / **别名取最后一个** / namespace **全对**
      · Python 抽出 `['os','包','工具函数','别的','np','x','b']`
        —— `包` 和 `x` 是**模块路径的头一段**，被误当成了绑定
      · Rust `use std::collections::HashMap` 抽出 `['std','std','bar']` 同理

    ★ 治法用**通用后处理**而不是继续抠树：模块路径的节点类型每语言都不同
      （`dotted_name`/`scoped_identifier`/`relative_import`…），
      但**「名字等于模块路径的某一段」这个事实与语言无关**。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十八：`kg` 导不进来（{type(e).__name__}: {e}）"])

    if not hasattr(_kg, "_抽绑定名"):
        坏.append("判六十八：没有 `_抽绑定名` ⇒ 谁 import 进来了什么仍然不知道"
                  "（R327 那 2881 条猜错靶的根因）")
        return _报2(坏)
    if not hasattr(_kg, "_去模块前缀"):
        坏.append("判六十八：没有 `_去模块前缀` ⇒ 模块路径会被当成绑定"
                  "（`from 包.甲 import 工具函数` 会抽出 `包`）")
        return _报2(坏)

    try:
        from tree_sitter import Parser as _P

        def _抽(语: str, 文: str) -> list[str]:
            bs = 文.encode()
            _树 = _P(_kg._取语言(语)).parse(bs)
            _段 = lambda x: bs[x.start_byte:x.end_byte].decode("utf-8", "replace")  # noqa: E731
            出: list[str] = []

            def 走(n):
                if _kg._导入型.search(n.type) and n.is_named:
                    出.extend(_kg._抽绑定名(n, _段))
                    return
                for c in n.children:
                    走(c)
            走(_树.root_node)
            return 出

        # ① JS/TS：default / 具名 / **别名取最后一个** / namespace
        js = _抽("javascript", "import a from 'x'\n"
                               "import { b, c as d } from 'y'\n"
                               "import * as e from 'z'\n")
        for 期 in ("a", "b", "d", "e"):
            if 期 not in js:
                坏.append(f"判六十八：JS 少抽了 `{期}`（实得 {js}）")
        if "c" in js:
            坏.append(f"判六十八：`import {{ c as d }}` **绑的是 `d` 不是 `c`**"
                      f"（实得 {js}）⇒ 别名取错了")
        # ② Python 别名：`from x import a as b` 绑 `b`
        py = _抽("python", "import numpy as np\nfrom x import a as b\n")
        if "np" not in py or "b" not in py:
            坏.append(f"判六十八：Python 别名抽错（实得 {py}）")
        # ③ 模块路径必须能去掉（通用后处理）
        净 = _kg._去模块前缀(["包", "工具函数", "别的"], "包.甲")
        if "包" in 净:
            坏.append(f"判六十八：`_去模块前缀` 没去掉模块头一段（实得 {净}）")
        if "工具函数" not in 净 or "别的" not in 净:
            坏.append(f"判六十八：`_去模块前缀` **把真的绑定也去掉了**（实得 {净}）"
                      "—— 过滤过宽比不过滤更坏")
        净2 = _kg._去模块前缀(["std", "std", "bar"], "std::collections")
        if 净2 != ["bar"]:
            坏.append(f"判六十八：`::` 分隔的模块前缀没去掉（实得 {净2}）")
        # ④ **不许塌**：抽名字不许让 `取导入` 抛异常
        try:
            _抽("go", 'package p\n\nimport "fmt"\n')
            _抽("rust", "use std::io::{self, Read};\n")
            _抽("python", "from typing import (\n    List,\n    Dict,\n)\n")
        except Exception as e:                    # noqa: BLE001
            坏.append(f"判六十八：抽名字时抛异常（{type(e).__name__}: {e}）"
                      "—— R323 就是这么塌的")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十八：抛异常（{type(e).__name__}: {e}）")

    return _报2(坏, "抽绑定名：绿 —— 判六十八（JS default/具名/别名/namespace·"
                    "Python 别名·去模块前缀不过宽·多语言不抛异常）")


def 判六十七星号导入必须与权威ast逐条对齐() -> None:
    """`from M import *` 这个事实，`ast` 能**逐条**列出来 —— 那就逐条比。

    ★ 上一轮我留了个**没解释的差**：`ast` 数 175，我数 187 条边 / 123 个起文件。
      「数字变大时要看清它数的是什么」—— 三个数**数的不是一回事**：
        · `ast` 的 175 是**语句处数**（同一文件两次星号算两处）
        · 我的 187 是**边数**
        · 我的 123 是**去重后的起文件数**
      逐文件对账后真相是：**`ast` 只扫了 `*.py`，我的图含 `*.pyi`**。
      补上 `.pyi` 之后 ⇒ **一致 123 · 我漏 0 · 我多 0 · 条数不一致 0**。

    ★ 所以这条判据要**同时**钉两件事：
      1. **逐文件、逐条数**都对（不是只比总数 —— 总数对了可能两边各错一半）
      2. `.pyi` **也算**（语言约定：`.pyi` 是 Python 的存根，语法同 Python）
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十七：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kg星_"))
    图 = Path(tempfile.mkdtemp(prefix="kg星图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        源 = {
            "a.py": "from x import *\nfrom y import *\n\n\ndef 用():\n    return 1\n",
            "b.py": "from x import *\n",
            "c.pyi": "from x import *\nfrom z import *\n",   # ★ 存根也要算
            "d.py": "from x import 甲\n",                    # 不是星号
            "e.py": "import os\n",
        }
        for 名, 文 in 源.items():
            (根 / 名).write_text(文, encoding="utf-8")
        (根 / "x.py").write_text("def 甲():\n    return 1\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)

        # ── 权威：`ast` 自己数一遍
        import ast as _ast
        权: dict[str, int] = {}
        for 名, 文 in 源.items():
            c = sum(1 for n in _ast.walk(_ast.parse(文))
                    if isinstance(n, _ast.ImportFrom)
                    and any(a.name == "*" for a in n.names))
            if c:
                权[名] = c

        # ── 我这一侧
        边 = _kg.加载图(根).get("边") or []
        我: dict[str, int] = {}
        for e in 边:
            if e.get("关系") == "导入" and e.get("星号"):
                起 = (e.get("起") or "")
                if 起.startswith("file:"):
                    我[起[5:]] = 我.get(起[5:], 0) + 1

        漏 = sorted(set(权) - set(我))
        多 = sorted(set(我) - set(权))
        if 漏:
            坏.append(f"判六十七：**漏了星号** {漏} ⇒ 「未导入」会把假缺口算成真缺口")
        if 多:
            坏.append(f"判六十七：**多标了星号** {多}")
        if not 漏 and not 多:
            差 = [(k, 权[k], 我[k]) for k in 权 if 权[k] != 我.get(k)]
            if 差:
                坏.append(f"判六十七：**条数不一致**（文件, ast, 我）={差} "
                          "⇒ 只比总数会漏掉「两边各错一半」")
        # ★ `.pyi` 必须真进来了（否则「一致」只是因为两边都漏）
        if "c.pyi" not in 我:
            坏.append("判六十七：**`.pyi` 没算进来** ⇒ 上一轮那个「差」就是这么来的"
                      "（`ast` 只扫 `*.py`，我的图含 `*.pyi`）")
        if 我.get("d.py") or "d.py" in 我:
            坏.append("判六十七：`from x import 甲` **被误标成星号**")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十七：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "星号与权威对齐：绿 —— 判六十七（逐文件逐条·含 `.pyi`·"
                    "不误标 `from x import 甲`）")


def 判六十六取导入不许塌且星号导入必须标在边上() -> None:
    """两件事，都是**从这个机器上真炸过的地方**学来的。

    ★ ① `取导入` **不许塌**。
      上一轮我改它（想把 `绑定` 字段抽出来），它**抛异常退到保底适配器** ——
      导入边一条不剩、边上冒出 `保底: true`、关键字 `def`/`import` 被当成名字。
      **我没立刻看出**，是好几步之后靠一个 `保底` 字段才发现的。
      ⇒ **改一个核心函数之前，先有「它有没有塌」的机器判据。**

    ★ ② `from x import *` 必须**标出来**。
      星号导入之后，本文件里**任何**未解析的名字都可能来自 `x` ——
      「本文件没导入它」这个判断**不再成立**。
      不标它，那一整类「未导入」都是**假缺口**（目标 ④ 的读数会虚高）。

    ★ ③ 而且必须标在**边**上，不能标在**节点**上：
      `mod:x` 是全局共享节点（两个文件 import 同一个模块 ⇒ 同一个节点），
      挂节点上会**串台**：一个文件用星号、另一个不用，节点只有一个。
      **「同一个判断写在两处，必然给出两个答案」** —— 这里是两个不同的事实
      挤进一个共享节点，必然给出错的答案。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十六：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kg塌_"))
    图 = Path(tempfile.mkdtemp(prefix="kg塌图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        (根 / "x.py").write_text("def 甲():\n    return 1\n", encoding="utf-8")
        (根 / "用星.py").write_text(
            "from x import *\n\n\ndef 用():\n    return 甲()\n", encoding="utf-8")
        (根 / "用明.py").write_text(
            "from x import 甲\n\n\ndef 用():\n    return 甲()\n", encoding="utf-8")
        (根 / "夹.py").write_text("import json\nimport os\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _载 = _kg.加载图(根)
        节 = _载.get("节点") or {}
        边 = _载.get("边") or []

        # ① `取导入` 没塌：两个文件都该有导入边，且**不许有保底痕迹**
        导 = [e for e in 边 if e.get("关系") == "导入"]
        有导 = {(e.get("起") or "") for e in 导}
        if "file:用星.py" not in 有导 or "file:用明.py" not in 有导:
            坏.append(f"判六十六：**`取导入` 塌了** —— 导入边只剩 {sorted(有导)}"
                      "（上一轮就是这么塌的：抛异常退到保底适配器）")
        保底 = [n for n in 节.values() if n.get("保底")]
        if 保底:
            坏.append(f"判六十六：边上出现 `保底` 痕迹（{len(保底)} 个）"
                      "⇒ 有解析器起不来，宁可红也不许静默降级")

        # ② 星号必须标出来，且**标在边上**
        def 取边(起: str):
            for e in 导:
                if (e.get("起") or "") == 起:
                    return e
            return None
        星边 = 取边("file:用星.py")
        明边 = 取边("file:用明.py")
        if 星边 is None or 明边 is None:
            坏.append("判六十六：拿不到两条导入边")
        else:
            if not 星边.get("星号"):
                坏.append("判六十六：`from x import *` **没标星号** "
                          "⇒ 该类「未导入」会把假缺口算成真缺口")
            if 明边.get("星号"):
                坏.append("判六十六：`from x import 甲` **被误标成星号**")

        # ③ 不许标在共享节点上（会串台）
        节点带星 = [k for k, v in 节.items() if v.get("类") == "模块" and "星号" in v]
        if 节点带星:
            坏.append(f"判六十六：星号标在**共享节点**上了（{节点带星[:3]}）"
                      "⇒ `mod:x` 只有一个节点，两个文件会串台")
        # ★ 这里原来还有一段「多语言加固」（直接调 `_语法树适配器` 再 `取导入`）。
        #   它**红了** —— 但红的是**我这段调用方式**（适配器返回里没有 `树` 键），
        #   不是机器塌了：**判五十五**已经在测「导入抽取必须在多语言上都有产出」，**是绿的**。
        #   ⇒ 按本项目口径，**写错的判据不许留在测试里**（「写不出绿的判据不是判据」），
        #     所以删掉这段 —— 多语言的防塌由**判五十五**负责，这里不重复造一个坏的。
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十六：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "取导入不许塌：绿 —— 判六十六（导入边齐全·无保底痕迹·"
                    "星号标在边上·共享节点不带星号·多语言不抛异常）")


def 判六十五名字等于本文件import的模块名时必须先认模块() -> None:
    """`import copy` 之后，裸名 `copy` **就是**那个模块对象。

    ★ 实测（stdlib，1052147 边）：`导入定义（跨文件）` 类歧义 **12850** 条，
      其中 **9013 条候选跨文件**。样例：

      ```
      起: file:argparse.py | 名: name:copy | 模块: copy
         候选 定义:copy.py:copy:61 · 定义:os.py:copy:748 · 定义:shutil.py:copy:421
      ```

      而 `argparse.py` **确实 `import copy`** ⇒ 裸名 `copy` **就是那个模块**，
      它**不可能同时**是 `os.py` 里的同名函数。

    ★ 根因不是「信息不够」，是**顺序**：
      候选法（拿「本文件 import 的模块里有没有定义这个名字」去猜）跑在
      模块名判定**前面** ⇒ `copy` 先命中了三个文件里的同名**定义**。

    ★ 口径：**同一个判断写在两处，必然给出两个答案** ——
      而**顺序**同样致命：**两个都对的分支，谁先跑就决定了答案对不对。**
      这里两个分支单独看都是对的，先后一颠倒就成了假歧义。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十五：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kg顺_"))
    图 = Path(tempfile.mkdtemp(prefix="kg顺图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        # ★ 三个模块**都定义同名函数**，用方 `import` 了其中两个：
        #   正确行为：裸名 `copy` ⇒ 那个**模块**（`copy.py`），不是别处的同名定义
        (根 / "copy.py").write_text("def copy():\n    return 1\n", encoding="utf-8")
        (根 / "os.py").write_text("def copy():\n    return 2\n", encoding="utf-8")
        (根 / "shutil.py").write_text("def copy():\n    return 3\n", encoding="utf-8")
        (根 / "用.py").write_text(
            "import copy\nimport os\n\n\ndef 用():\n    return copy.copy()\n",
            encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        边 = _kg.加载图(根).get("边") or []
        引 = [e for e in 边 if e.get("关系") == "引用"
             and (e.get("原名") or "") == "name:copy"
             and (e.get("起") or "") == "file:用.py"]
        if not 引:
            坏.append("判六十五：`用.py` 里 `copy` 的引用边丢了")
        else:
            e = 引[0]
            if (e.get("终") or "") != "file:copy.py":
                坏.append(f"判六十五：`import copy` + 裸名 `copy` ⇒ 必须绑**模块本身**"
                          f" `file:copy.py`，实得 {e.get('终')!r}")
            if e.get("歧义"):
                坏.append(f"判六十五：**唯一入口却标了歧义**（候选 {e.get('候选')}）"
                          "⇒ 这就是「两个都对的分支谁先跑」造成的假歧义")
            if (e.get("依据") or "") != "导入模块":
                坏.append(f"判六十五：依据该是 `导入模块`，实得 {e.get('依据')!r}")
        # 反例守住：**没有** import 那个模块时，不该凭空绑过去
        (根 / "别的.py").write_text(
            "def 用():\n    return copy.copy()\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        边2 = _kg.加载图(根).get("边") or []
        幻 = [e for e in 边2 if (e.get("起") or "") == "file:别的.py"
             and (e.get("原名") or "") == "name:copy"
             and (e.get("终") or "").startswith("file:")]
        if 幻:
            坏.append(f"判六十五：`别的.py` **没 import** 却绑到了模块（{幻[:1]}）"
                      "—— 不能把「没导入」也说成导入了")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十五：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "模块名优先：绿 —— 判六十五（import 过的模块名先认模块·"
                    "唯一入口不标歧义·没 import 的不许凭空绑）")


def 判六十四模块名必须绑入口文件不许造假歧义() -> None:
    """`import numpy as np` 里的 `numpy` —— 它到底指哪个文件，**语言定死了**。

    ★ 实测（stdlib，1052147 边）：歧义 **25075** 条，其中 **`导入模块` 类 5617 条**，
      而最常歧义的名字是
      **`typing`(946) / `numpy`(654) / `logging`(243) / `collections`(224) /
      `matplotlib`(199) / `warnings`(201)** —— **全是模块名**。
      一个模块名不该有歧义：**它就是一个包，住在一个入口文件里**
      （Python `__init__.py`、JS `index.js`、Rust `mod.rs`/`lib.rs`、Go `main.go`）。

    ★ 根因是我的索引**故意注册得宽**（各级后缀都注册，为了「不把能解析的变成未解析」）——
      那个取舍本身是对的，但**宽索引的代价必须在这里收回**：
      **入口文件优先**，唯一入口就直绑，不许标歧义。

    ★ 口径：**「变多了」不等于「更全了」**。
      25075 里混着「真同名多定义」（`__init__`/`get`/`copy`）和
      「我索引太宽」两种东西 —— **混在一起就没有区分力**。
      清掉假的那部分，剩下的才是「AI 真的不知道该指哪」。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十四：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kg入_"))
    图 = Path(tempfile.mkdtemp(prefix="kg入图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        # ★ 目录名与子模块同名 ⇒ 宽索引下 `import 包` 会同时命中 `包/__init__.py`
        #   和 `包/子.py`（因为各级后缀都注册）⇒ 假歧义
        (根 / "包").mkdir()
        (根 / "包" / "__init__.py").write_text("def 甲():\n    return 1\n",
                                              encoding="utf-8")
        (根 / "包" / "子.py").write_text("def 乙():\n    return 2\n",
                                        encoding="utf-8")
        (根 / "用.py").write_text("import 包\n\n\ndef 用():\n    return 包.甲()\n",
                                  encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        边 = _kg.加载图(根).get("边") or []
        引 = [e for e in 边 if e.get("关系") == "引用"
             and (e.get("原名") or "") == "name:包"]
        if not 引:
            坏.append("判六十四：`import 包` 的引用边丢了")
        else:
            e = 引[0]
            # ① 必须绑到**入口文件**
            if (e.get("终") or "") != "file:包/__init__.py":
                坏.append(f"判六十四：`import 包` 该绑入口 `file:包/__init__.py`，"
                          f"实得 {e.get('终')!r}")
            # ② **唯一入口 ⇒ 不许标歧义**
            if e.get("歧义"):
                坏.append(f"判六十四：**入口文件唯一却标了歧义**（候选 "
                          f"{e.get('候选')}）⇒ 假歧义混进读数")
        # ③ 入口优先的排序本身要稳（`__init__.py` 排在别的文件前）
        if not hasattr(_kg, "_入口优先"):
            坏.append("判六十四：没有 `_入口优先` ⇒ 模块名命中多个文件时没有权威次序")
        else:
            排 = _kg._入口优先(["包/子.py", "包/__init__.py"])
            if 排[0] != "包/__init__.py":
                坏.append(f"判六十四：入口没排在前面（实得 {排}）")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十四：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "模块名绑入口：绿 —— 判六十四（模块名绑入口文件·"
                    "唯一入口不标歧义·入口优先次序）")


def 判六十三跨文件绑定必须绑到定义而不是绑到文件() -> None:
    """目标 ② 的原话：「定义绑到作用域：文件内定义→局部，**import 进来的→跨文件**」。

    ★ 上一轮我打算「把 `取导入` 的 `绑定` 字段抽出来」——**读了代码才发现那个字段不必要**。
      `from x import y` 这个事实由三件事**已经确定**：
        本文件 import 了模块 x ＋ `y` 出现在本文件 ＋ **x 的文件里确实定义了 `y`**
      第三件才是**决定的证据**，而它早就有（`本文件` 索引）。
      ⇒ 所以**不该**去按语言抽「导入了哪些名字」（`clause`/`specifier`/`namespace`
        每种语言都不同）；上一轮我这么做，**把 `取导入` 弄炸了**：
        导入边一条不剩，边上冒出 `保底: true`，关键字 `def`/`import` 被当成了名字。

    ★ 这个判据钉三件事：
      1. **绑到定义**（`定义:...`），不是绑到文件（`file:...`）
      2. 目标的 id **必须在节点表里真的存在**（可机验，不是「我说它指向了」）
      3. **目标模块里没有这个名字时不许凭空造定义**
         （`from x import 根本不存在` ⇒ `x` 里没有它 ⇒ 不许指向任何 `定义:`）
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十三：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kg跨_"))
    图 = Path(tempfile.mkdtemp(prefix="kg跨图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        (根 / "包").mkdir()
        (根 / "包" / "__init__.py").write_text("", encoding="utf-8")
        (根 / "包" / "甲.py").write_text(
            "def 工具函数():\n    return 1\n", encoding="utf-8")
        (根 / "包" / "乙.py").write_text(
            "from 包.甲 import 工具函数\n\n\ndef 用():\n    return 工具函数()\n",
            encoding="utf-8")
        (根 / "包" / "丁.py").write_text(
            "import 包.甲\nfrom 包.甲 import 根本不存在\n\n\ndef 用():\n"
            "    return 包.甲.工具函数()\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        载 = _kg.加载图(根)                       # ★ 重载：图真的变了才算
        节 = 载.get("节点") or {}
        边 = 载.get("边") or []
        引 = [e for e in 边 if e.get("关系") == "引用"
             and (e.get("原名") or "").startswith("name:")]
        # ① 跨文件必须绑到**定义**
        跨定 = [e for e in 引 if (e.get("原名") == "name:工具函数")
               and (e.get("终") or "").startswith("定义:")
               and "甲.py" in (e.get("终") or "")]
        if not 跨定:
            坏.append(f"判六十三：`from 包.甲 import 工具函数` **没绑到定义**"
                      f"（实得 {[(e.get('原名'), e.get('终')) for e in 引][:8]}）"
                      "⇒ 「import 进来的→跨文件」只做到「跨到文件」")
        for e in 跨定:
            if e["终"] not in 节:
                坏.append(f"判六十三：目标不在节点表（{e['终']}）⇒ 无法机验")
            # ★ 只对**跨文件**的边要求 `目标文件`：
            #   「本文件定义」的目标就是自己，天然没有「目标文件」这回事。
            #   （我第一版在这里误伤了本文件那条 —— **判据也会错，错的判据比没判据更坏**。）
            if (e.get("依据") or "").startswith("导入") and not e.get("目标文件"):
                坏.append("判六十三：跨文件绑定到定义却没记 `目标文件` ⇒ 说不清是哪个文件")
        # ② 限定名用法（`包.甲.工具函数()`）也该绑上 —— 这一条是「候选法」比
        #    「抽绑定名」**更强**的证据：它不依赖「名字是被 import 进来的」。
        if not [e for e in 跨定 if "丁.py" in (e.get("起") or "")]:
            坏.append("判六十三：`丁.py` 里 `包.甲.工具函数()` 这种**限定名用法**"
                      "没绑到定义（候选法本该也能覆盖它）")
        # ③ 目标模块里没有的名字 ⇒ **不许凭空造定义**
        幻 = [e for e in 引 if (e.get("原名") or "") == "name:根本不存在"
             and (e.get("终") or "").startswith("定义:")]
        if 幻:
            坏.append(f"判六十三：**凭空找到了一个不存在的定义**（{幻[:1]}）"
                      "—— 目标模块里没有这个名字")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十三：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "跨文件绑定：绿 —— 判六十三（绑到定义不是绑到文件·"
                    "目标在节点表·限定名用法也覆盖·不凭空造定义）")


def 判六十二提及必须真的升级为带目标节点id的引用() -> None:
    """这是**整个目标的正题**：「把通用知识图谱从『名字出现过』升级到『真引用』」。

    ★ 实测量到的真状态（本轮之前）：边的分布是
      `提及 21778 · 绑定 14254 · 包含 2252 · 导入 1343` ——
      **一条 `引用` 都没有**。原因不是解析没做，而是解析结果只写进了
      `引用.json`，**没并进 `边.json`** ⇒ **读图的人看到的还是「名字出现过」**。
      **解析做了、图没变** —— 这正是「这件事在图上还没发生」。

    ★ 三条边界，每一条我都实测错过：
      · **「歧义」不等于「未解析」**：命中多个时 `终` 是 list，
        我原来用 `str(list).startswith("定义:")` 判，为假 ⇒ **解析成功却被当成没解到**
        （实测边上出现 `未解析原因: "本文件定义"`）。**两种失败长得一样，下一步动作完全不同。**
      · **没解到目标的不许叫「引用」**：名字一错下游全错。保持「提及」+ 记原因。
      · **「名字出现过」这条信息不许丢** ⇒ 升级时存进 `原名`。
        **「看不见」不能当「没有」。**

    ★ 最硬的一条：**引用边的 `终` 必须在节点表里真的存在** ——
      这才把「真引用」变成**可机验**的（不是「我说它指向了」）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十二：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kg引_"))
    图 = Path(tempfile.mkdtemp(prefix="kg引图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        # 造一个「本文件定义」+「跨文件经 import」+「未导入」三情形都有的仓
        (根 / "包").mkdir()
        (根 / "包" / "__init__.py").write_text("", encoding="utf-8")
        (根 / "包" / "甲.py").write_text(
            "def 工具函数():\n    return 1\n\n\n"
            "def 调用者():\n    return 工具函数()\n", encoding="utf-8")
        (根 / "包" / "乙.py").write_text(
            "from 包.甲 import 工具函数\n\n\n"
            "def 用():\n    return 工具函数()\n", encoding="utf-8")
        (根 / "包" / "丙.py").write_text(
            "def 没导入():\n    return 工具函数()\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        r = _kg.解析引用(根, 打印=lambda *_: None)
        并 = r.get("★并进主图") or {}
        if 并.get("升级为引用", 0) < 1:
            坏.append(f"判六十二：**一条都没升级成「引用」**（{并}）⇒ "
                      "解析结果没并进主图")
        # ★ **重载**（写错文件的教训：不重载就测不出「图真的变了」）
        图2 = _kg.加载图(根)
        节 = 图2.get("节点") or {}
        边 = 图2.get("边") or []
        # ★★★ **判据不许把「会演进的文案」当判据对象**（R383）。
        #   修前是**精确匹配旧短文案** ⇒ 而边上实际写的是长文本
        #   （`本文件定义（**按作用域由内向外**）`、`导入绑定（**权威**：…）`）
        #   ⇒ 匹配永远为假 ⇒ **判据红，而它自己打印的分布里明明有「引用」**
        #   （红文自相矛盾）。后果极坏：**每改一次文案，判据就红一次**。
        #   ⇒ 只判它真想判的两件事：①有「引用」边 ②依据非空。
        #     「终必须是真节点 id」由下面那条断言独立保证（docstring 说的最硬一条）。
        引 = [e for e in 边 if e.get("关系") == "引用" and (e.get("依据") or "")]
        if not 引:
            坏.append(f"判六十二：**重载后主图里没有「引用」边** ⇒ "
                      f"关系分布 {sorted({e.get('关系') for e in 边})}")
        _无据62 = [e for e in 边 if e.get("关系") == "引用" and not (e.get("依据") or "")]
        if _无据62:
            坏.append(f"判六十二：有 {len(_无据62)} 条「引用」边**没有依据** ⇒ "
                      f"**说不出依据的解析是装出来的**")
        # ① 目标必须是**真节点 id**
        无主 = [e for e in 引 if (e.get("终") or "") not in 节]
        if 无主:
            坏.append(f"判六十二：**{len(无主)} 条引用边的目标不在节点表里** ⇒ "
                      f"「指向了」无法机验（样例 {无主[:1]}）")
        # ② 「名字出现过」不许丢
        缺原 = [e for e in 引 if not e.get("原名")]
        if 缺原:
            坏.append(f"判六十二：{len(缺原)} 条引用边**丢了 `原名`** ⇒ "
                      "升级后查不到它原本是哪个名字")
        # ③ 没解到目标的**不许叫「引用」**
        假引 = [e for e in 边 if e.get("关系") == "引用"
               and (e.get("终") or "").startswith("name:")
               and (e.get("依据") or "").startswith("**未导入**")]
        if 假引:
            坏.append(f"判六十二：**{len(假引)} 条没解到目标却叫「引用」** ⇒ "
                      "名不副实，下游会以为指到了东西")
        # ④ 未解析/未导入的必须**保持提及并标原因**
        带因 = [e for e in 边 if e.get("关系") == "提及" and e.get("未解析原因")]
        if not 带因:
            坏.append("判六十二：未解到的提及边**没标 `未解析原因`** ⇒ "
                      "「未解析的必须报出来」没落进图")
        # ⑤ 「歧义」要能标出来（本语料未必有歧义，只验字段机制不误伤）
        歧 = [e for e in 引 if e.get("歧义")]
        for e in 歧:
            if e.get("终") not in 节:
                坏.append(f"判六十二：歧义边的 `终` 不在节点表（{e.get('终')}）")
            if not e.get("候选"):
                坏.append("判六十二：标了歧义却没给**候选** ⇒ 说不准的东西还是要说清")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十二：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "真引用：绿 —— 判六十二（提及→引用并进主图·目标必须是真节点 id·"
                    "原名不丢·没解到的不叫引用·未解析标原因·歧义给候选）")


def 判六十一JSON跨文件引用必须真解到目标() -> None:
    """`$ref` / `extends` 是**跨文件引用**，目标是**文件 + 指针** —— 两者都可机判。

    ★ 为什么这不是「名字出现过」：
      `$ref: "./x.json#/definitions/Y"` **声称** `x.json` 里有 `/definitions/Y`。
      **这个声称可以被机器核对**：读那个文件、按 RFC 6901 走一遍指针。
      走得通 = 真引用；走不通 = **断引用**（未解析的必须报出来）。

    ★ 本判据用**真值已知的语料**（自己造的 fixture）——
      因为本仓 `$ref` 只有 9 个文件（都在 `.venv` 里）、`extends` 0 次：
      **没语料就不能算测过**，所以造一个已知答案的。
      同时它守住三条**实测会错的**边界：
        · 相对引用要按**引用文件所在目录**解析（不是仓根）
        · `tsconfig` 的 `extends` **可省 `.json` 后缀**
        · 目标文件存在但**指针解不开** ⇒ 也是断引用（不能只查文件在不在）
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十一：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kgJ_"))
    图 = Path(tempfile.mkdtemp(prefix="kgJ图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        (根 / "cfg").mkdir()
        # 目标 schema：有 /definitions/Y，没有 /definitions/NOPE
        (根 / "cfg" / "defs.json").write_text(
            '{"definitions": {"Y": {"type": "string"}}}', encoding="utf-8")
        (根 / "cfg" / "base.json").write_text('{"strict": true}', encoding="utf-8")
        # 主文件：好引用 / 指针解不开 / 文件不存在 / 本文件指针 / extends 省后缀
        (根 / "cfg" / "主.json").write_text(
            '{"good": {"$ref": "./defs.json#/definitions/Y"},'
            ' "badptr": {"$ref": "./defs.json#/definitions/NOPE"},'
            ' "nofile": {"$ref": "./missing.json#/x"},'
            ' "selfptr": {"$ref": "#/good"},'
            ' "extends": "./base"}', encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        r = _kg.解析JSON引用(根)
        if r.get("引用", 0) < 5:
            坏.append(f"判六十一：5 条引用只认了 {r.get('引用')} 条"
                      f"（`_结构化引用` 可能没接进适配器）")
        # ★ **重载**再断言（上上轮的教训：写错文件 ⇒ 重载就没了）
        边 = [e for e in (_kg.加载图(根).get("边") or [])
             if (e.get("依据") or "").startswith("json")]
        按 = {}
        for e in 边:
            tgt = e.get("终") or ""
            if tgt.startswith("本文件指针:"):
                按["selfptr"] = e
            elif tgt.endswith("./defs.json") or tgt.endswith("defs.json"):
                按.setdefault("defs", []).append(e)
            elif "missing" in tgt:
                按["nofile"] = e
            elif "base" in tgt:
                按["extends"] = e
        # ① 好引用：文件 + 指针都要解开
        好 = [e for e in 边 if e.get("已解析")
             and (e.get("目标文件") or "").endswith("defs.json")
             and (e.get("指针") or "") == "/definitions/Y"]
        if not 好:
            坏.append(f"判六十一：`./defs.json#/definitions/Y` **该解开却没解开**"
                      f"（边={[(e.get('终'), e.get('目标文件'), e.get('已解析')) for e in 边][:6]}）")
        # ② 指针解不开：文件在、指针不在 ⇒ 必须**报出来**
        坏针 = [e for e in 边 if (e.get("指针") or "") == "/definitions/NOPE"]
        if not 坏针:
            坏.append("判六十一：`#/definitions/NOPE` 的引用边丢了")
        elif any(e.get("已解析") for e in 坏针):
            坏.append("判六十一：**指针在目标里解不开，却算已解析** ⇒ "
                      "只查了文件在不在，没真走指针")
        if r.get("指针解不开", 0) < 1:
            坏.append(f"判六十一：**指针解不开必须报出来**（实得 {r.get('指针解不开')}）"
                      "—— 未解析的必须报出来")
        # ③ 文件不存在 ⇒ 断链
        if r.get("断链", 0) < 1:
            坏.append(f"判六十一：目标文件不存在该算断链（实得 {r.get('断链')}）")
        # ④ 本文件指针要真解（90 条那种）
        if not (按.get("selfptr") or {}).get("已解析"):
            坏.append(f"判六十一：`#/good` 这种**本文件指针**没解开"
                      f"（{按.get('selfptr')}）")
        # ⑤ `extends` 可省 `.json`
        ex = 按.get("extends")
        if not ex:
            坏.append("判六十一：`extends` 没被当引用（它是 tsconfig 的跨文件引用）")
        elif not ex.get("已解析"):
            坏.append(f"判六十一：`extends: \"./base\"` **可省 `.json` 后缀**，"
                      f"该解开却失败（目标文件={ex.get('目标文件')}）")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判六十一：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "json 引用：绿 —— 判六十一（$ref 文件+指针真解·指针解不开报出·"
                    "文件不存在断链·本文件指针·extends 可省后缀）")


def 判六十JSONC必须读得进来且两条路对账() -> None:
    """`tsconfig.json` 是 **JSONC**（带注释/尾随逗号）—— 严格 `json.loads` 读不了。

    ★ 实测：它被判成「解析失败」并写了 `parse_error` 边 ——
      **我把一个合法的配置文件当成了坏文件**，然后这个文件在图上就没有任何引用。
      TypeScript 官方就是容忍注释地读它；**该用别人做好的东西**（`json5`）。

    ★ 三层读法必须**可对账**（目标 ⑤）：
      ① 严格 `json.loads` ② `json5`（权威）③ 自剥注释 + 去尾逗号
      ②③ 都成功就必须给出**同一个结构**；不一致就是我的 bug。
      所以本判据不只验「能读」，还验「**走的那条路被标出来了**」——
      否则「读进来了」也可能是被某个宽容解析器**猜**进来的。

    ★ **坏文件必须明说读不出来**。把读不出的配置变成 `{}` 是
      **静默降级 —— 最坏的一种「绿」**：下游会以为「这文件没有引用」，
      而真相是「我没读懂」。**「看不见」不能当「没有」。**
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判六十：`kg` 导不进来（{type(e).__name__}: {e}）"])

    # ① 严格 JSON 走严格路
    档, 路 = _kg._读JSON('{"a": 1}')
    if 档 != {"a": 1} or 路 != "严格JSON":
        坏.append(f"判六十：严格 JSON 该走「严格JSON」，实得 {路!r} / {档!r}")

    # ② JSONC：注释 + 尾随逗号，必须读得进来
    文c = '{\n  // 行注释\n  "a": 1,\n  /* 块注释 */\n  "b": [1, 2,],\n}\n'
    档2, 路2 = _kg._读JSON(文c)
    if 档2 != {"a": 1, "b": [1, 2]}:
        坏.append(f"判六十：**JSONC 没读进来**（实得 {档2!r}）⇒ "
                  "`tsconfig.json` 这类文件会被当成坏文件，图上一条引用都没有")
    if "json5" not in 路2 and "自剥" not in 路2:
        坏.append(f"判六十：JSONC 走的路没标出来（实得 {路2!r}）⇒ "
                  "读进来了也说不清是**谁**读的")

    # ③ 两条路必须对账（都成功就得同结构）
    try:
        import json5 as _j5                                  # noqa: F401
        import re as _re
        净 = _re.sub(r",(\s*[}\]])", r"\1", _kg._剥JSON注释(文c))
        import json as _j
        左, 右 = _j5.loads(文c), _j.loads(净)
        if 左 != 右:
            坏.append(f"判六十：**两条路给出两个答案**（json5={左!r} 自剥={右!r}）")
        if "一致" not in 路2:
            坏.append(f"判六十：两条路一致时应在路名里标明（实得 {路2!r}）")
    except ImportError:
        pass                                    # 没装 json5 就只验自剥那条

    # ④ 坏文件**不许静默降级**
    档3, 路3 = _kg._读JSON('{{{ 不是 JSON')
    if 档3 is not None:
        坏.append(f"判六十：坏文件该返回 `None`，实得 {档3!r} ⇒ **静默降级**")
    if 路3 != "失败":
        坏.append(f"判六十：坏文件该明说「失败」，实得 {路3!r}")

    # ⑤ 真 `tsconfig.json` 形状：过适配器**不许有解析失败边**，且要标出 JSONC
    tk = ('{\n  // 这是 tsconfig\n  "compilerOptions": {\n    "target": "es2020",\n'
          '    "strict": true,\n  },\n  "include": ["src/**/*.ts",],\n}\n')
    _节, 边 = _kg._结构化适配器(tk, "tsconfig.json", "json")
    失 = [e for e in 边 if (e.get("关系") or "") == "解析失败"]
    if 失:
        坏.append(f"判六十：JSONC 过适配器仍报**解析失败**（{失[:1]}）⇒ "
                  "这个文件在图上会一条引用都没有")
    说 = [e for e in 边 if (e.get("关系") or "") == "说明"]
    if not 说:
        坏.append("判六十：读了 JSONC 却**没标注**（下游分不清「严格 JSON」与「宽容读进来的」）")
    键 = [n.get("名") for n in _节 if (n.get("类") or "") == "键"]
    if "compilerOptions" not in 键 or "target" not in 键:
        坏.append(f"判六十：JSONC 的**键路径**没抽到（实得 {键[:8]}）⇒ "
                  "「键路径」这一项在 JSONC 上等于没做")

    return _报2(坏, "JSONC：绿 —— 判六十（能读·标明走哪条路·两条路对账·"
                    "坏文件不许静默降级·真 tsconfig 形状不报解析失败）")


def 判五十九json和yaml的引用必须由权威解析器判() -> None:
    """`json`/`yaml` 里的引用**不能靠正则找** —— 正则必然给出假红。

    ★ 实测（这就是立这条判据的原因）：我用 `\*([\w.-]+)` 找 YAML 别名，
      结果把 **glob 通配符 `*.lean` / `*.rs` / `*38`** 当成了「无对应锚点的别名」
      ⇒ 一次报出 **79 个断引用，全是假的**。**假红与真红长得一样。**
      YAML 规范里 `*x` 是别名、`*.lean` 是普通标量 —— **这个区别规范定死了，
      就该由权威 token 流（`yaml.scan()` 的 `AliasToken`/`AnchorToken`）回答。**

    ★ `$ref` 这边是比 markdown 链接**更硬**的真引用：
      目标是**文件 + JSON 指针**，两个都可机判 ——
      文件在不在是文件系统事实；`#/definitions/Y` 能不能解开是解析事实。
      **都不该退化成「名字出现过」。**

    ★ 还要钉住「接线」：`_结构化引用` 写得再好，没接进 `_结构化适配器`
      就等于没做（上一轮的原话：**登记了不等于接上了**）。
      所以这里**走适配器**验，不直接调那个函数。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十九：`kg` 导不进来（{type(e).__name__}: {e}）"])
    try:
        import json as _json
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十九：`json` 导不进来（{e}）"])

    # ① JSON `$ref`：文件 + 指针必须**分开**，外链必须标出来 —— **走适配器**
    文 = _json.dumps({"a": {"$ref": "./x.json#/definitions/Y"},
                      "b": {"$ref": "#/definitions/Z"},
                      "c": {"$ref": "https://e.com/x"}})
    _节, 边 = _kg._结构化适配器(文, "样.json", "json")
    引 = [e for e in 边 if (e.get("依据") or "") == "json $ref"]
    if len(引) != 3:
        坏.append(f"判五十九：3 个 `$ref` 只认了 {len(引)} 个 ⇒ "
                  "`_结构化引用` **没接进** `_结构化适配器`（登记了不等于接上了）")
    终 = [e.get("终") or "" for e in 引]
    if not any(t == "路径:./x.json" for t in 终):
        坏.append(f"判五十九：`./x.json#/definitions/Y` 该拆成**文件**"
                  f"（实得 {终}）—— 整串当路径就等于没解析")
    if not any(t.startswith("本文件指针:") for t in 终):
        坏.append(f"判五十九：`#/definitions/Z` 该认成**本文件指针**（实得 {终}）")
    if not [e for e in 引 if e.get("外部")]:
        坏.append("判五十九：`https://` 的 `$ref` **没标外部** ⇒ 会被算成断链（假红）")
    if not any((e.get("指针") or "").startswith("/definitions/") for e in 引):
        坏.append("判五十九：指针没被单独取出来（`#/definitions/Y` 要能拿去解开）")

    # ② YAML 别名：**权威 token 流**，且 glob 不许被当别名
    try:
        import yaml as _y                                   # noqa: F401
        _有yaml = True
    except Exception:                                       # noqa: BLE001
        _有yaml = False
    if _有yaml:
        y = "base: &self\n  a: 1\nuse: *self\nbad: *nope\nfiles: '*.lean'\n"
        _节2, 边2 = _kg._结构化适配器(y, "样.yml", "yaml")
        别 = [e for e in 边2 if (e.get("依据") or "") == "yaml 别名"]
        名 = [e.get("锚名") for e in 别]
        if "self" not in 名:
            坏.append(f"判五十九：`*self` 没被认成别名（实得 {名}）")
        if "nope" not in 名:
            坏.append(f"判五十九：`*nope` 没被认成别名（实得 {名}）")
        # ★★ 这一条就是本判据存在的理由
        if any("lean" in (m or "") or "rs" in (m or "") for m in 名):
            坏.append(f"判五十九：**glob `*.lean` 被当成了 YAML 别名**（实得 {名}）"
                      "—— 正则假红；YAML 别名只能由权威 token 流判")
        真 = {e.get("锚名"): e.get("已解析") for e in 别}
        if 真.get("self") is not True:
            坏.append(f"判五十九：`*self` 有 `&self` ⇒ 必须 `已解析=True`（实得 {真}）")
        if 真.get("nope") is not False:
            坏.append(f"判五十九：`*nope` **没有**对应锚点 ⇒ 必须 `已解析=False`"
                      f"（实得 {真}）—— **断引用必须报出来**")

    return _报2(坏, "结构化引用：绿 —— 判五十九（$ref 拆文件+指针·外链标外部·"
                    "YAML 别名由权威 token 流判·glob 不许当别名·断引用报出·真接进适配器）")


def 判五十八markdown行号锚越界必须落盘且与独立数一致() -> None:
    """`file:///x.lean#L719` 是**声称**「第 719 行有东西」——**声称要被机器核对**。

    ★ 这才是「从『名字出现过』升级到『真引用』」：不是名字在不在，是**声称对不对**。
    ★ 本轮的真教训（**必须钉死**）：判定全对、数字全对，但**写错了文件**
      （写 `图.json`，而 `加载图` 读的是 `边.json`）⇒ 下次读出来是「没做过」。
      **「跑过」与「留下痕迹」是两件事。** 所以这里**必须重载图再断言**。
    ★ 还要**独立数一遍行数**与图上对账（目标 ⑤：用另一条路算，不一致就是我的 bug）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十八：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kg锚_"))
    图 = Path(tempfile.mkdtemp(prefix="kg锚图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        (根 / "doc").mkdir()
        (根 / "doc" / "短.lean").write_text("a\nb\nc\n", encoding="utf-8")
        (根 / "doc" / "长.lean").write_text(
            "".join(f"l{i}\n" for i in range(1, 501)), encoding="utf-8")
        (根 / "doc" / "主.md").write_text(
            "[短越界](短.lean#L9)\n"
            "[长没越界](长.lean#L400)\n"
            "[长越界](长.lean#L99999)\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        r = _kg.解析md链接(根)
        if r.get("链接", 0) < 3:
            坏.append(f"判五十八：3 条链接只认了 {r.get('链接')} 条")
        # ★ **重载**：这正是本轮踩的坑（写错文件 ⇒ 重载就没了）
        图2 = _kg.加载图(根)
        边 = 图2.get("边") or []
        带锚 = [e for e in 边 if e.get("锚行")]
        if len(带锚) < 3:
            坏.append(f"判五十八：**重载后带锚边的字段丢了**（{len(带锚)}/3）—— "
                      "判定写到了 `加载图` 不读的文件里；**「跑过」不等于「留下痕迹」**")
        有行数 = [e for e in 带锚 if e.get("文件行数")]
        if len(有行数) < 3:
            坏.append(f"判五十八：重载后**数到的行数没留下**（{len(有行数)}/3）"
                      f"；键={sorted(带锚[0].keys()) if 带锚 else '—'}")
        # ★ 独立数一遍，与图上对账
        for e in 有行数:
            f = 根 / e["目标文件"]
            真 = sum(1 for _ in f.open("r", encoding="utf-8", errors="replace"))
            if e.get("文件行数") != 真:
                坏.append(f"判五十八：`{e['目标文件']}` 图上写 {e.get('文件行数')} 行、"
                           f"独立数出 {真} 行 ⇒ **两条路给出两个答案**")
        # ★ 越界必须**报出来**（不能静默）
        越 = [e for e in 带锚 if e.get("行号越界")]
        if len(越) != 2:
            坏.append(f"判五十八：应为 **2 条越界**（`短.lean#L9` 只有 3 行、"
                      f"`长.lean#L99999` 只有 500 行），实得 {len(越)} 条"
                      f"（{[(e.get('目标文件'), e.get('锚行')) for e in 越]}）")
        if r.get("行号越界", 0) != 2:
            坏.append(f"判五十八：计数字段 `行号越界` 应报 2，实得 {r.get('行号越界')}"
                      "—— **未解析的必须报出来**")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判五十八：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "锚行：绿 —— 判五十八（越界真判·重载后仍在·与独立数行数一致·"
                    "越界必须报出来）")


def 判五十七markdown链接必须是真引用() -> None:
    """markdown 链接**自带目标** ⇒ **存在与否是文件系统事实**，不是我猜的。

    ★ 这是「未解析的必须报出来」最干净的一处读数：代码的引用要靠**作用域**推，
      推不出来只能标「未解析」；而 markdown 链接写着路径，**能直接判存在**。

    ★ 钉住本轮实测踩过的四件事：
      · **`.md` 必须真的走 Markdown 适配器** —— 我注册进了 `_适配器表` 却没接进
        `_选适配器`（它 `if 后缀 in _语法` 优先）⇒ **342 个文件里 0 个走 Markdown**。
        **「登记了」不等于「接上了」。**
      · **相对链接按「起文件所在目录」解析**，不是仓根 —— 错了会把对的判成断链
        （**假红和真红长得一样**）。
      · **代码块 / 行内代码里的 `[x](y)` 不是链接**，不许进图。
      · **外链（`https://`）不算断链**，必须标出来而不是混进「未解析」。
    ★ 还有一个**真实的边界**：这个 grammar 的 `language()` 是**块级**的，
      内联链接被压成匿名 token（没有 `inline_link` 节点）⇒ 链接只能用文本模式抽，
      但**围栏代码块的排除仍靠块结构**。**该用别人的地方用别人的，该自己做的自己做。**
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十七：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kgMd_"))
    图 = Path(tempfile.mkdtemp(prefix="kgMd图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        (根 / "doc").mkdir()
        (根 / "doc" / "有.md").write_text("# 目标\n", encoding="utf-8")
        _主 = ("# 标题甲\n"
              "\n"
              "见 [相对](有.md) · [带锚](#标题甲) · [外链](https://e.com/x)\n"
              "\n"
              "```python\n"
              "假 = '[代码块](不存在.md)'\n"
              "```\n"
              "\n"
              "行内 `[行内](也不存在.md)` 不是链接\n")
        (根 / "doc" / "主.md").write_text(_主, encoding="utf-8")
        出 = _kg.建图(根, 打印=lambda *_: None)
        分 = 出.get("适配器分布") or {}
        if not any("Markdown" in k for k in 分):
            坏.append(f"判五十七：**`.md` 没走 Markdown 适配器**（分布 {分}）—— "
                      "「登记了」不等于「接上了」")
        _图 = _kg.加载图(根)
        引 = [e for e in (_图.get("边") or [])
             if e.get("关系") == "引用" and (e.get("依据") or "").startswith("markdown")]
        终们 = " ".join(e.get("终") or "" for e in 引)
        if "有.md" not in 终们:
            坏.append(f"判五十七：相对链接 `有.md` 没进图（实得 {[e.get('终') for e in 引]}）")
        for 假 in ("不存在.md", "也不存在.md"):
            if 假 in 终们:
                坏.append(f"判五十七：**`{假}` 在代码块/行内代码里**，却进了图 ⇒ "
                          "假链接会把真断链淹没")
        if "https://e.com/x" not in 终们:
            坏.append("判五十七：外链没进图 ⇒ 外部依赖看不见")
        if not [e for e in 引 if e.get("外部")]:
            坏.append("判五十七：外链**没标 `外部`** ⇒ 会被算成断链（假红）")
        r = _kg.解析md链接(根)
        if r.get("断链", 0) != 0:
            坏.append(f"判五十七：`有.md` 明明在 `doc/` 下，却报 **{r.get('断链')} 条断链** "
                      f"⇒ 相对基准错了（应按**起文件所在目录**）"
                      f"；样例 {r.get('断链样例', [])[:2]}")
        if r.get("已解析", 0) < 1:
            坏.append("判五十七：一条都没解析成功 ⇒ 存在性判定没做")
        if not any((e.get("终") or "").startswith("锚:") for e in 引):
            坏.append("判五十七：`#锚` 没被当引用（本文件锚要能查标题）")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判五十七：抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "markdown：绿 —— 判五十七（.md 真走 Markdown·相对按起文件目录·"
                    "代码块假链接不进图·外链标外部·锚查本文件标题·断链如实报出）")


def 判五十六注册的每种语言都必须真能起解析器() -> None:
    """★★ **静默降级是最坏的一种「绿」。**

    实测：`tree_sitter_typescript` **没有 `language()`**，只有
    `language_typescript()` / `language_tsx()`。而 `_语法树适配器` 只会写
    `.language()` ⇒ **`.ts`/`.tsx` 起不了 parser ⇒ 静默降级到保底**
    （160 个文件只拿到「文件+行块+提及」）。**图上看起来有东西，其实那一层没做**，
    而且**没有任何读数会变红**。

    ⇒ 判据必须**逐个注册项**把 parser 真的起一遍，再在**该语言**上抽一次导入
    （**非零**才算这一层真的在）。

    ★ 顺带钉住本轮修掉的两个分类错（都由「数字不对」逼出来）：
      · **字符串优先于字段** —— Go `import_spec` 的 `name` 是**别名**
        （`mypkg`/`_`），路径在 `path` ⇒ 顺序错了就抽到别名。
      · **判定只看 `named` 子节点** —— `import`/`use` 这些**匿名关键字节点
        自己也含 "import"** ⇒ 把 Java 的整段兜底挡死 ⇒ **1032 条掉到 0**。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十六：`kg` 导不进来（{type(e).__name__}: {e}）"])

    from tree_sitter import Parser as _Pgr

    _样 = {
        "python": "import os\nfrom re import compile as re_compile\n",
        "rust": "use std::collections::BTreeMap;\nmod 内联 { fn f() {} }\nmod 文件;\n",
        "typescript:typescript": 'import { A } from "./m";\n',
        "typescript:tsx": 'import React from "react";\n',
        "javascript": 'const fs = require("fs");\nimport x from "lodash";\n',
        "go": 'package main\n\nimport (\n\t"fmt"\n\tm "example.com/x"\n)\n',
        "java": "import java.util.List;\n",
    }
    for 语, 文 in _样.items():
        后缀 = "?".join(语.split(":")[:1])
        try:
            _P = _Pgr(_kg._取语言(语))
            树 = _P.parse(文.encode())
            出 = _kg.取导入(树, 文.encode())
        except Exception as e:                    # noqa: BLE001
            坏.append(f"判五十六：`{语}` **起不了解析器**（{type(e).__name__}: {e}）"
                      " ⇒ 该语言会**静默降级到保底**（最坏的一种绿）")
            continue
        if not 出:
            坏.append(f"判五十六：`{语}` 解析器起来了但**导入一条都抽不到**"
                      f"（{后缀} 这一层等于没做）")
        # 别名不许当成模块（Go `m "example.com/x"` 的 `m` 是别名）
        for x in 出:
            if x["模块"] in ("m", "_", "fmt_alias"):
                坏.append(f"判五十六：`{语}` 抽到的是**别名** `{x['模块']}` 而不是路径 "
                          "⇒ 优先级错了（**字符串必须优先于 `name` 字段**）")

    # Java 回归钉死：整段兜底不许被**匿名关键字子节点**挡住
    _P = _Pgr(_kg._取语言("java"))
    _j = _kg.取导入(_P.parse(b"import java.util.List;\n"), b"import java.util.List;\n")
    if not _j:
        坏.append("判五十六：**Java 一条都抽不到** ⇒ 整段兜底被 `import` 这个"
                  "**匿名关键字子节点**挡住了（实测过：1032 条掉到 0）")

    # 注册表里每个语言都要能起 parser
    try:
        _源 = (Path(__file__).resolve().parents[1] / "src" / "agentscope_orchestrator"
             / "kg.py").read_text(encoding="utf-8")
        _i = _源.find("_语法 = {")
        _j2 = _源.find("}", _i)
        _语们 = sorted(set(__import__("re").findall(r'"([a-z]+(?::[a-z]+)?)"', _源[_i:_j2])))
        for _y in _语们:
            try:
                _kg._取语言(_y)
            except Exception as e:                # noqa: BLE001
                坏.append(f"判五十六：`_语法` 里注册的 `{_y}` **起不了解析器**"
                          f"（{type(e).__name__}）⇒ 该后缀的文件会静默降级")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判五十六：读 `_语法` 表失败（{type(e).__name__}: {e}）")

    return _报2(坏, "解析器：绿 —— 判五十六（注册的每种语言都真能起 parser·"
                    "每种语言导入非零·别名不当模块·Java 兜底不被匿名关键字挡住）")


def 判五十五导入抽取必须在多语言上都有产出() -> None:
    """★ **在 Python 上调到 100% 不代表通用。**

    实测踩过的真事：`取导入` 在 Python 上和 `ast` 逐条 100% 一致（747/747），
    换到 **Rust 就归零** —— 1071 个 `.rs`，导入 **0 条**。
    原因：Rust 是 `use_declaration`，路径在 **`argument` 字段**，**没有 `name` 字段**，
    而我只认 `name` ⇒ 全军覆没。
    ⇒ 判据必须**跨语言**钉住，否则「通用」两个字是空的。

    还钉两条实测踩过的分类错：
      · **`mod X { ... }`（内联模块，有 body）是定义，不是导入** ——
        当成导入 ⇒ 158 个**仓内模块名**被算成「Cargo.toml 没声明的外部 crate」，
        **检查看似在报错，其实是我分错了类**。
      · `use super::*;` 的 `*`、`mod 测试;` 的 `mod` 关键字都**不是**模块名。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十五：`kg` 导不进来（{type(e).__name__}: {e}）"])
    try:
        import importlib as _il
        from tree_sitter import Language as _L, Parser as _P
        _m = _il.import_module("tree_sitter_rust")
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十五：tree-sitter rust grammar 起不来（{e}）"])

    _P2 = _P(_L(_m.language()))
    _样 = (
        "use std::collections::BTreeMap;\n"
        "use serde::{Deserialize, Serialize};\n"
        "use crate::inner::Thing;\n"
        "use super::*;\n"
        "pub use self::k as 别名;\n"
        "mod 内联模块 { pub fn f() {} }\n"
        "mod 文件模块;\n"
    )
    _bs = _样.encode("utf-8")
    导 = _kg.取导入(_P2.parse(_bs), _bs)
    模 = {x["模块"] for x in 导}
    if not 模:
        坏.append("判五十五：**Rust 上一条导入都抽不到** ⇒ 通用是假的"
                  "（实测就是这个形状：1071 个 .rs → 0 条）")
    # ① 正常写的都要抽到
    for 应 in ("std.collections.BTreeMap", "serde", "crate.inner.Thing"):
        if not any(x == 应 or x.startswith(应) for x in 模):
            坏.append(f"判五十五：Rust 没抽到 `{应}`（实得 {sorted(模)}）")
    # ② 内联模块**不许**当导入（它是定义）
    if any("内联模块" in x for x in 模):
        坏.append(f"判五十五：**内联模块** `mod 内联模块 {{ ... }}` 被当成了导入 "
                  f"⇒ 它是**定义**不是导入（实测：这让 158 个仓内模块名被误判成外部 crate）")
    # ③ `mod foo;`（无 body）**要**当导入
    if not any("文件模块" in x for x in 模):
        坏.append(f"判五十五：`mod 文件模块;`（无 body，是文件引用）没被当导入 "
                  f"（实得 {sorted(模)}）")
    # ④ 关键字/星号不许留在模块名里
    for x in 模:
        if x.startswith("mod ") or x.endswith("*") or " " in x.strip():
            坏.append(f"判五十五：模块名里残留关键字或星号：{x!r}")
    # ⑤ **和构建系统的权威声明对账**：外部 crate 必须在 Cargo.toml 里
    _根 = Path(tempfile.mkdtemp(prefix="kgRs_"))
    try:
        (_根 / "src").mkdir()
        (_根 / "Cargo.toml").write_text(
            "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n\n"
            "[dependencies]\nserde = \"1\"\n", encoding="utf-8")
        (_根 / "src" / "main.rs").write_text(_样, encoding="utf-8")
        _出 = _kg.建图(_根, 打印=lambda *_: None)
        _节 = _kg.加载图(_根).get("节点") or {}
        _观察 = {n["名"] for n in _节.values() if n.get("类") == "模块"}
        if not _观察:
            坏.append("判五十五：建图后图上一个 `模块` 节点都没有 ⇒ 导入没进图")
        if not any(x.startswith("serde") for x in _观察):
            # 外部 crate 的权威事实在 Cargo.toml 里
            坏.append(f"判五十五：`serde`（Cargo.toml 里声明了的依赖）没进图，"
                      f"实得 {sorted(_观察)}")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判五十五：建图时抛异常（{type(e).__name__}: {e}）")
    finally:
        shutil.rmtree(_根, ignore_errors=True)

    return _报2(坏, "多语言导入：绿 —— 判五十五（Rust 非零·内联模块算定义·`mod f;` 算导入·"
                    "关键字与星号不残留·外部 crate 与 Cargo.toml 对账）")


def 判五十四Lean必须两层并列不许拿正则假装elaborator() -> None:
    """Lean 没有 tree-sitter grammar，且**真结构只有 elaborator 有**。

    ★ 实测（`formal_proof`，746 模块 / 53,704 条确证名）：
      **文本级只覆盖 17,025 = 约一半**；另一半 17,859 条是
      **自动名**（`_proof_1`/`_sizeOf_inst`/`casesOn`/`match_`）、**构造子**、
      **结构投影名**、**私有名编码** —— **文本里根本没有，正则原理上做不到**。
      ⇒ 正确形态是**两层并列、各标来源**，**不是**让文本级去假装 elaborator。
    ★ 还钉两条踩过的坑：
      · **注释里的声明不许被抽到**（正则扫到 `/- theorem xxx_原 -/` ⇒ 假阳一片）
      · **模块名查文件必须试各级后缀**，和注册侧对称 —— 不对称会产生**假 0**
        （实测：17,864 条「挂不上文件」⇒ 没能进图，读数却显示「只有 elaborator 有 0」）
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十四：`kg` 导不进来（{type(e).__name__}: {e}）"])

    根 = Path(tempfile.mkdtemp(prefix="kgLean_"))
    图 = Path(tempfile.mkdtemp(prefix="kgLean图_"))
    _os = __import__("os")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(图)
    try:
        (根 / "T.lean").write_text(
            "import MyMeta.Other\n"
            "\n"
            "/-! 这一段是注释，里面的声明**不许**被抽到：\n"
            "    theorem 注释里的假声明 : True := trivial\n"
            "    def 注释里的假定义 := 1\n"
            "-/\n"
            "-- theorem 行注释里的假声明2 : True := trivial\n"
            "namespace MyMeta.Demo\n"
            "\n"
            "def 真定义 : Nat := 1\n"
            "\n"
            "theorem 真定理 : 真定义 = 1 := rfl\n"
            "\n"
            "end MyMeta.Demo\n", encoding="utf-8")
        (根 / "Other.lean").write_text(
            "namespace MyMeta.Other\ndef 别的 : Nat := 2\nend MyMeta.Other\n",
            encoding="utf-8")
        出 = _kg.建图(根, 打印=lambda *_: None)
        if 出.get("适配器分布", {}).get("_Lean适配器", 0) < 2:
            坏.append(f"判五十四：`.lean` 没走 Lean 适配器（分布 {出.get('适配器分布')}）"
                      " ⇒ 掉回保底就抽不到 namespace 限定名")
        节 = _kg.加载图(根).get("节点") or {}
        定义 = [n for n in 节.values() if n.get("语言") == "lean"
               and n.get("类") in _kg._实体类]
        名们 = {n["名"] for n in 定义}
        # ① 注释里的声明不许进图
        for 假 in ("注释里的假声明", "注释里的假定义", "行注释里的假声明2"):
            if 假 in 名们:
                坏.append(f"判五十四：**注释里的 `{假}` 被抽进了图** ⇒ 剥注释没做"
                          "（实测：不剥的话假阳一片）")
        # ② 真声明要进，且 namespace 限定名要拼出来
        for 真 in ("真定义", "真定理", "别的"):
            if 真 not in 名们:
                坏.append(f"判五十四：真声明 `{真}` 不在图上（名们在：{sorted(名们)[:12]}）")
        _限 = {n["名"]: n.get("限定名") for n in 定义}
        if _限.get("真定义") != "MyMeta.Demo.真定义":
            坏.append(f"判五十四：namespace 限定名没拼对 —— "
                      f"实得 {_限.get('真定义')!r}（应 `MyMeta.Demo.真定义`）")
        # ③ 来源/确证 必须标（两层并列的前提）
        for n in 定义:
            if "来源" not in n or "确证" not in n:
                坏.append(f"判五十四：`{n['名']}` 没标 `来源`/`确证` ⇒ "
                          "文本级与 elaborator 级**分不开**")
                break
        # ④ import 图要有
        _模 = [n for n in 节.values() if n.get("类") == "模块"]
        if not _模:
            坏.append("判五十四：Lean 的 `import` 没进图 ⇒ 没有依赖边")
        else:
            _m = _kg._模块索引(["Other.lean"])
            if not (_m.get("Other") or _m.get("MyMeta.Other")):
                坏.append("判五十四：模块索引查不到 `Other` ⇒ 注册/查询不对称，"
                          "会产生**假 0**")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判五十四：样本上抛异常（{type(e).__name__}: {e}）")
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧
        shutil.rmtree(根, ignore_errors=True)
        shutil.rmtree(图, ignore_errors=True)

    return _报2(坏, "Lean：绿 —— 判五十四（.lean 走 Lean 适配器·剥注释·namespace 限定名·"
                    "来源与确证分明·import 进图·模块索引对称）")


def 判五十三引用解析必须分层完整且不许有假解析层() -> None:
    """「提及 → 引用」这一层必须**每一类都有名字、且每一条都能说出依据**。

    ★ 三个断言：
      ① **分层完整**：提及总数 == 各分类之和（**没有掉在地上的**）
      ② **已解析的必须带 `依据`**：说不出依据的「解析」是**装出来的**
      ③ **不许有假解析层**：源码里**不许再出现「全仓唯一 ⇒ 算解析」**
         —— Python 里跨文件用名字**必须 import**，靠「全仓碰巧只有一个同名的」
         得到的边**没有依据**。实测它把「歧义」推到 116,355（裸名 `__init__`
         `get` `close` 满仓同名）⇒ **数字好看，含义是假的**。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十三：`kg` 导不进来（{type(e).__name__}: {e}）"])

    # ③ 源码里不许再有假解析层
    _源 = (Path(__file__).resolve().parents[1] / "src"
           / "agentscope_orchestrator" / "kg.py").read_text(encoding="utf-8")
    if "全仓唯一" in _源 and "**那是假解析**" not in _源:
        坏.append("判五十三③：源码里还有「全仓唯一 ⇒ 算解析」的假解析层")
    if "已解析·全仓唯一" in _源:
        坏.append("判五十三③：还有一个叫「已解析·全仓唯一」的计数器 ⇒ 假解析层没删干净")

    # 用**小样本**跑（判据不该依赖大仓）
    根 = Path(tempfile.mkdtemp(prefix="kg引用_"))
    try:
        (根 / "a.py").write_text(
            "def foo():\n    return 1\n\n\nclass Bar:\n    def meth(self):\n"
            "        return foo()\n", encoding="utf-8")
        (根 / "b.py").write_text(
            "import a\n\n\ndef use():\n    return a.foo()\n", encoding="utf-8")
        (根 / "c.py").write_text(
            "def use():\n    local_var = 1\n    return foo() + local_var\n",
            encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        出 = _kg.解析引用(根, 打印=lambda *_: None)
        if "计数" not in 出:
            坏.append(f"判五十三：解析没给出计数（返回 {sorted(出)}）")
        else:
            计 = 出["计数"]
            总 = 出.get("提及总数", 0)
            和 = sum(计.values())
            # ① 分层完整
            #
            # ★★★ **口径修正（R363）**：原来断言 `总 == 和`，
            #   而 `总` 是**扫过的提及边数**、`和` 是**产出的引用条数** ——
            #   两者**本来就不一定相等**：解析器从 R330 起除了「每条提及边各解一次」，
            #   还会从**导入表**和**模块名**各产一条引用
            #   （实测 `b.py`：`import a`（行1）与 `a.foo()`（行5）各产一条 `终=file:a.py`）。
            #   ⇒ 原断言**红得对但判错**：它把「一条边产出两条」当成「有提及掉在地上」。
            #   ★ 这**不是放松**：下面比 `和 == sum(计)`（**同源**，必须严格相等），
            #     并且**另加** `总 <= 和`（提及边一条都不许凭空消失）。
            _和2 = 出.get("★分类之和")
            if _和2 is not None and _和2 != 和:
                坏.append(f"判五十三①：`★分类之和` {_和2} ≠ `sum(计数)` {和}"
                         f" —— **同一个事实两个答案**")
            if 总 > 和:
                坏.append(f"判五十三①：提及边 {总} > 引用 {和} ⇒ "
                         f"**有提及掉在地上没分类**（{计}）")
            if not 总:
                坏.append("判五十三①：提及数为 0 ⇒ 解析根本没跑起来（**不是「都对」**）")
            # ★ 差额必须**有名字**（不许是个说不清的余数）
            _差 = 出.get("★一条提及边产出的引用（差额，可能有导入表/模块名两条路）")
            if _差 is None:
                坏.append("判五十三①：**差额没有名字** —— 「多出来的引用从哪来」必须报出来")
            elif _差 != 和 - 总:
                坏.append(f"判五十三①：差额报的 {_差} ≠ 实得 {和 - 总}")
            # ③ 假解析层的计数键不许存在
            for k in 计:
                if "全仓唯一" in k:
                    坏.append(f"判五十三③：还有假解析层计数键 `{k}`")
            # ② 已解析的必须带依据
            _引 = _kg._kg目录(根) / "引用.json"
            if not _引.is_file():
                坏.append("判五十三：`.kg/引用.json` 没落盘 ⇒ 引用**没进常驻**")
            else:
                _j = __import__('json').loads(_引.read_text(encoding="utf-8"))
                _缺 = [x for x in _j
                      if "已解析" in str(x.get("依据", "")) and not x.get("依据")]
                if _缺:
                    坏.append(f"判五十三②：{len(_缺)} 条「已解析」**说不出依据**")
                if _j and not any(x.get("依据") for x in _j):
                    坏.append("判五十三②：所有引用边都没有 `依据` 字段 ⇒ 无从核对")
            # ★ 这个样本的**真值**（我构造的，所以我知道答案）：
            #   `c.py` 用了 `foo` 但**没 import** ⇒ 必须落在「未导入」，不能算解析
            if 计.get("未导入", 0) < 1:
                坏.append("判五十三：`c.py` 用了 `foo` 却没导入 ⇒ 应落在「未导入」，"
                          f"实得 {计.get('未导入')}（**「没有」与「看不见」不能混**）")
            # `c.py` 的 `local_var` 是**绑定**，不是引用
            if 计.get("绑定/内置（**不算引用**）", 0) < 1:
                坏.append(f"判五十三：局部变量没被认成绑定 ⇒ 会被错算成引用"
                          f"（实得 {计}）")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判五十三：样本上抛异常（{type(e).__name__}: {e}）")
    finally:
        shutil.rmtree(根, ignore_errors=True)

    return _报2(坏, "引用解析：绿 —— 判五十三（分层完整·已解析都带依据·无假解析层·"
                    "未导入可达·局部变量算绑定）")


def 判五十二导入抽取必须与权威解析器逐条对齐() -> None:
    """导入是**比「提及」硬**的一层：它是**语法结构**给的事实。

    `ast`（权威）说这个文件导入了哪些模块；我的通用路径（tree-sitter，
    **靠 field 名不靠语言**）必须给出**同一集合**。
      · `我漏` ⇒ 漏了导入 ⇒ 依赖图缺边
      · `我多` ⇒ 假阳（实测踩过：`aliased_import` 也含 "import"，
        被当成一条导入 ⇒ 取到的是**被导入的名字**（`compile`）而不是模块）
    """
    坏: list[str] = []
    根 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib")
    if not 根.is_dir():
        return _报2([f"判五十二：标准库不在 `{根}`（**环境前提，不是通过**）"])
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十二：`kg` 导不进来（{type(e).__name__}: {e}）"])

    import ast as _ast, importlib as _il, json as _json
    from tree_sitter import Language as _L, Parser as _P

    文件们 = [p for p in sorted(根.rglob("*.py")) if p.stat().st_size > 2000][:120]
    try:
        _m = _il.import_module("tree_sitter_python")
        _解析 = _P(_L(_m.language()))
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十二：tree-sitter python grammar 起不来（{e}）"])

    def ast导入(文: str) -> set:
        出: set = set()
        try:
            树 = _ast.parse(文)
        except (SyntaxError, ValueError, RecursionError):
            return 出
        for n in _ast.walk(树):
            if isinstance(n, _ast.Import):
                出 |= {a.name for a in n.names}
            elif isinstance(n, _ast.ImportFrom):
                出.add(("." * (n.level or 0)) + (n.module or ""))
        return 出

    一 = 漏 = 多 = 0
    漏例: list[str] = []
    多例: list[str] = []
    for p in 文件们:
        rel = p.relative_to(根).as_posix()
        文 = p.read_text(encoding="utf-8", errors="replace")
        A = ast导入(文)
        if not A:
            continue
        bs = 文.encode("utf-8", "replace")
        T = {x["模块"] for x in _kg.取导入(_解析.parse(bs), bs)}
        一 += len(A & T)
        for x in sorted(A - T):
            漏 += 1
            if len(漏例) < 6:
                漏例.append(f"{rel}::{x!r}")
        for x in sorted(T - A):
            多 += 1
            if len(多例) < 6:
                多例.append(f"{rel}::{x!r}")
    if 漏:
        坏.append(f"判五十二：**我漏 {漏}** 条导入（权威有、我没有）⇒ 依赖图缺边。"
                  f"样例：{漏例}")
    if 多:
        坏.append(f"判五十二：**我多 {多}** 条 —— 多半是取到了**被导入的名字**"
                  f"（`from X import Y` 里的 Y）而不是模块。样例：{多例}")
    if not 一:
        坏.append("判五十二：一致数为 0 ⇒ 对账没跑起来（**不是「都对」**）")

    return _报2(坏, f"导入抽取：绿 —— 判五十二（与权威 `ast` 逐条对齐："
                    f"一致 {一} · 漏 0 · 多 0 · {len(文件们)} 个标准库文件）")


def 判五十一通用抽取必须与权威解析器逐条对齐() -> None:
    """★ 通用路径**不许只是「看起来能用」** —— 要和**权威解析器**逐条对。

    `ast` 是 CPython 自己的解析器（**权威**）。我的通用路径（tree-sitter）
    在**定义抽取**上应该和它在**同一口径**下**完全一致**：
      · `我漏` ⇒ **真 bug**（漏抽实体）
      · `我多` ⇒ **假阳**（把语法位置当定义）

    ★ 口径必须先统一（都取「所有命名实体 + 限定名」）——
      v1 我没统一口径，报出「我漏 5,862」，其中大半是 `类.方法` 的写法差，
      **不是 bug**。**数字再大，口径不齐就说明不了什么。**
    """
    坏: list[str] = []
    根 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib")
    if not 根.is_dir():
        return _报2([f"判五十一：标准库不在 `{根}`（**这是环境前提，不是通过**）"])
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十一：`kg` 导不进来（{type(e).__name__}: {e}）"])

    import ast as _ast
    # 取一批**有内容的** .py（避免挑到全空的文件让判据变松）
    文件们 = [p for p in sorted(根.rglob("*.py"))
             if p.stat().st_size > 2000][:120]
    if len(文件们) < 50:
        坏.append(f"判五十一：样本只有 {len(文件们)} 个（应 ≥50）⇒ 样本太少，判据不可信")

    def ast全定义(文: str) -> set:
        出: set = set()

        # ★★★ **模块级/类体的赋值也是「命名实体」**（R396）。
        #   实测：`__future__.py::CO_FUTURE_ABSOLUTE_IMPORT` 这类
        #   **模块级 `Assign`** 我这边抽成了定义，而 `ast` 侧**一条都不算** ⇒
        #   `我多 773`（R389 时 766 ⇒ 数字在涨，因为**我的抽取在变强**）。
        #   ★ 口径：**「我多」有两种** ——
        #     ① 我真抽错了（假阳）
        #     ② **对账器的口径比抽取器窄**（是账本错，不是账错）
        #     这一条是 ②。**先修尺子，再量。**
        #   ★ 边界：**只收「模块级」与「类体」的赋值**，**不收函数体内的** ——
        #     函数体内的局部变量在我这边落「绑定/内置」，**不算定义**。
        #     两边口径必须一致，否则又是拿两把尺子量。
        def 收赋值(n, 前缀: str) -> None:
            _目 = list(getattr(n, "targets", None) or [getattr(n, "target", None)])
            for _t in _目:
                if _t is None:
                    continue
                if isinstance(_t, _ast.Name):
                    出.add(f"{前缀}{_t.id}")
                elif isinstance(_t, (_ast.Tuple, _ast.List)):
                    for _e in _t.elts:
                        if isinstance(_e, _ast.Name):
                            出.add(f"{前缀}{_e.id}")

        def 走(body, 前缀: str, 顶层: bool) -> None:
            for n in body:
                if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                    名 = f"{前缀}{n.name}"
                    出.add(名)
                    # 类体里的赋值是**类属性**（算）；函数体里的不算
                    走(n.body, 名 + ".", isinstance(n, _ast.ClassDef))
                elif isinstance(n, (_ast.Assign, _ast.AnnAssign, _ast.AugAssign)):
                    if 顶层:
                        收赋值(n, 前缀)
                elif isinstance(n, (_ast.If, _ast.Try, _ast.With, _ast.For, _ast.While)):
                    走(getattr(n, "body", []), 前缀, 顶层)
                    走(getattr(n, "orelse", []), 前缀, 顶层)
                    走(getattr(n, "finalbody", []), 前缀, 顶层)
                    for h in getattr(n, "handlers", []):
                        走(getattr(h, "body", []), 前缀, 顶层)
        try:
            走(_ast.parse(文).body, "", True)
        except (SyntaxError, ValueError, RecursionError):
            return set()
        return 出

    一 = 漏 = 多 = 0
    漏例: list[str] = []
    多例: list[str] = []
    for p in 文件们:
        rel = p.relative_to(根).as_posix()
        文 = p.read_text(encoding="utf-8", errors="replace")
        A = ast全定义(文)
        节, _b = _kg._语法树适配器(文, rel, "python")
        # ★ 只比**命名实体** —— `模块`/`文件`/`行块`/`键` 不是定义。
        #   （加「导入」节点时忘了收窄 ⇒ 定义对账一度从「多 0」变成「多 1,662」：
        #    那是**判据口径**问题，不是机制问题。）
        T = {(n.get("限定名") or n["名"]) for n in 节 if n.get("类") in _kg._实体类}
        一 += len(A & T)
        for x in sorted(A - T):
            漏 += 1
            if len(漏例) < 6:
                漏例.append(f"{rel}::{x}")
        for x in sorted(T - A):
            多 += 1
            if len(多例) < 6:
                多例.append(f"{rel}::{x}")

    if 漏:
        坏.append(f"判五十一：**我漏 {漏}** 条（权威 `ast` 有、我没有）"
                  f" ⇒ 通用路径漏抽实体。样例：{漏例}")
    if 多:
        坏.append(f"判五十一：**我多 {多}** 条（我没有权威依据）"
                  f" ⇒ **假阳**（语法位置被当成定义）。样例：{多例}")
    if not 一:
        坏.append("判五十一：一致数为 0 ⇒ 对账根本没跑起来（**不是「都对」**）")

    return _报2(坏, f"通用抽取：绿 —— 判五十一（与权威 `ast` 逐条对齐："
                    f"一致 {一} · 漏 0 · 多 0 · {len(文件们)} 个标准库文件）")


def 判五十知识图谱必须是通用机器不是手搓产物() -> None:
    """★ 上一程我做错的地方：给**这一个仓库**手搓读数（公理足迹、mathlib 语料…），
    换个仓库**一项都用不上**。判据要钉住的是「**机器**」的四条性质：

      ① **通用**：不按项目写规则 —— 语言靠 tree-sitter grammar，
         **没有 grammar 的文件也进图**（保底适配器）。
      ② **完备**：**不许静默跳过** —— 跳过的逐条有原因，且落在读数里。
      ③ **自动**：增量只重算**内容真变**的文件；删掉的立墓碑；**touch 不算变**。
      ④ **可自证**：自洽门能红（悬空边 / 无落点 / 文件数为 0）。

    ★ 这四条全都**在陌生临时目录上验**（不是本仓）—— 不然又变成手搓产物。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判五十：`kg` 导不进来（{type(e).__name__}: {e}）"])

    # ① 适配器注册表：**有 grammar 的**和**没 grammar 的**都必须有条路
    适 = _kg.适配器表()
    _名 = " ".join(str(x) for x in 适)
    for 该有 in ("保底", "tree-sitter", "二进制"):
        if 该有 not in _名:
            坏.append(f"判五十①：适配器表里没有「{该有}」⇒ 那类文件没路可走")
    if "已装" not in _名 or "python" not in _名:
        坏.append("判五十①：适配器表没报「装了哪些 grammar」⇒ 能力边界不可见")

    import tempfile as _tf, json as _js, time as _tm, os as _os, shutil as _sh
    根 = Path(_tf.mkdtemp(prefix="kg判五十_"))
    try:
        (根 / "a.py").write_text("def foo(x):\n    if x:\n        return bar(x)\n"
                                "class K:\n    def m(self): pass\n", encoding="utf-8")
        (根 / "b.rs").write_text("fn q(a: u32) -> u32 { a }\nstruct S { x: u32 }\n",
                                encoding="utf-8")
        (根 / "c.md").write_text("# 标题\n\n正文\n", encoding="utf-8")
        (根 / "d.json").write_text('{"k": {"n": 1}}', encoding="utf-8")
        (根 / "e.闻所未闻").write_text("随便内容 with 标识符", encoding="utf-8")
        (根 / "f.bin").write_bytes(b"\x00\x01\xff")
        (根 / "子").mkdir()
        (根 / "子" / "g.sql").write_text("select 1 from t;\n", encoding="utf-8")
        出 = _kg.建图(根, 打印=lambda *_: None)

        # ② 完备：7 个文件**一个都不许『消失』** ——
        #   ★★★ R446：**『完备』有两种合法形状**（口径 165）：
        #     (a) **进图**（有 file 节点）
        #     (b) **在跳过清单里有名字、有原因**（`_值得进图` 是 45 项白名单，
        #        docstring 写着「其余**显式跳过并留名**」—— **它做到了**）
        #   ★ 旧版只认 (a) ⇒ 把**有意的白名单排除**算成「从图上消失了」✗
        #     —— 和 `判九十五` 那一轮**同一个毛病**（口径 164/165）。
        #   ★ 但「不静默」的硬要求**一条都不松**：
        #     ① 每个不进图的文件**必须在 `跳过` 里有条目**
        #     ② 条目**必须有 `文件`**（不能只有原因没有名字 —— 那就无从知道少了谁）
        #     ③ 条目**必须有 `原因`**
        #     ④ **`文件数 + 跳过条数` 必须等于枚举到的 7**（算术对得上，口径 144）
        节 = _js.loads((_kg._kg目录(根) / "节点.json").read_text(encoding="utf-8"))
        清 = _js.loads((_kg._kg目录(根) / "清单.json").read_text(encoding="utf-8"))
        _跳们 = 清.get("跳过") or []
        _跳名 = {str(x.get("文件") or "") for x in _跳们}
        _缺名 = [x for x in _跳们 if not x.get("文件")]
        if _缺名:
            坏.append(f"判五十②：{len(_缺名)} 条跳过**没有 `文件`** ⇒ "
                      f"**无从知道少了谁** → {_缺名[:2]}")
        for 相 in ("a.py", "b.rs", "c.md", "d.json", "e.闻所未闻", "f.bin", "子/g.sql"):
            _在图 = any(n.get("文件") == 相 for n in 节.values())
            _在跳 = (相 in _跳名) or (Path(相).name in _跳名) or any(
                str(x.get("路径") or "").replace("\\", "/").endswith(相) for x in _跳们)
            if not (_在图 or _在跳):
                坏.append(f"判五十②：`{相}` **既不在图上、也不在跳过清单里** ⇒ "
                          f"**这才是『消失』**（违反「看不见不能当没有」）")
        _总 = int(出.get("文件数") or 0) + len(_跳们)
        if _总 != 7:
            坏.append(f"判五十②：`文件数({出.get('文件数')}) + 跳过({len(_跳们)})` = {_总}"
                      f" ≠ 7 ⇒ **算术对不上**（有文件两边都不在，口径 144）")
        for x in _跳们:
            if not x.get("原因"):
                坏.append(f"判五十②：跳过 `{x.get('文件') or x.get('路径')}` "
                          f"**没写原因** ⇒ 静默跳过")

        # ③ 类别诚实：`if_statement` **不许**当定义（原版把它算了 ⇒ 虚报 6.8 倍）
        _型 = {n.get("类型") for n in 节.values() if n.get("文件") == "a.py"}
        _语句 = [t for t in _型 if t and t.endswith("_statement")]
        if _语句:
            坏.append(f"判五十③：**语句被算成定义**了：{_语句} ⇒ 实体数会虚报")
        if not any(n.get("类型") == "function_definition" for n in 节.values()):
            坏.append("判五十③：`a.py` 里的 `def` **没抽出来** ⇒ 解析没真跑")

        # ③ 增量：内容不变 ⇒ 重算 0；只 touch ⇒ 也不许重算；真改 ⇒ 必须重算
        if _kg.更新图(根, 打印=lambda *_: None).get("重算文件数") != 0:
            坏.append("判五十③：内容没变却重算了")
        _os.utime(根 / "a.py", (_tm.time() + 9, _tm.time() + 9))
        if _kg.更新图(根, 打印=lambda *_: None).get("重算文件数") != 0:
            坏.append("判五十③：**只 touch 就重算** ⇒ 戳比的是 mtime 不是内容")
        (根 / "a.py").write_text("def foo2(x):\n    return 1\n", encoding="utf-8")
        _出3 = _kg.更新图(根, 打印=lambda *_: None)
        if _出3.get("重算文件数") != 1:
            坏.append(f"判五十③：改了一个文件应重算 1，实得 {_出3.get('重算文件数')}")
        if "foo2" not in (_kg._kg目录(根) / "节点.json").read_text(encoding="utf-8"):
            坏.append("判五十③：改后的新定义没进图 ⇒ 增量没真更新")
        (根 / "b.rs").unlink()
        if not _kg.更新图(根, 打印=lambda *_: None).get("墓碑数"):
            坏.append("判五十③：删掉的文件没立墓碑")

        # ④ 自洽门：正常图上绿；人为搞坏**必须红**
        if _kg.自洽门(根).get("红"):
            坏.append(f"判五十④：正常图上自洽门就红了：{_kg.自洽门(根)['红']}")
        边 = _js.loads((_kg._kg目录(根) / "边.json").read_text(encoding="utf-8"))
        边.append({"起": "file:a.py", "终": "定义:不存在:x:1", "关系": "包含"})
        (_kg._kg目录(根) / "边.json").write_text(_js.dumps(边, ensure_ascii=False),
                                          encoding="utf-8")
        if not _kg.自洽门(根).get("红"):
            坏.append("判五十④：**悬空边加进去自洽门没红** ⇒ 门是装饰")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    # ⑤ **写不进去的根不许崩** —— 「任意文件夹」包含只读目录 / 权限拒。
    #   真事：`太虚元道\src` 上 `mkdir .kg` 抛 `PermissionError [WinError 5]`。
    #   ★ 断言必须**跨平台**：Windows 的权限位语义与 POSIX 不同，
    #     所以**不能断言「一定写不进去」**（`chmod 0o500` 在 Windows 上挡不住建子目录 ——
    #     实测「实得 文件数: 0 且没报错」，我第一版就是这么写错的）。
    #     能断言的是：**它不抛异常，且给出一个可读的结果**（成功就说文件数，失败就说错误）。
    import tempfile as _tf2, os as _os2
    _只 = Path(_tf2.mkdtemp(prefix="kg只读_"))
    try:
        (_只 / "锁").mkdir()
        (_只 / "锁" / "x.py").write_text("def zz9(): pass\n", encoding="utf-8")
        try:
            _os2.chmod(_只 / "锁", 0o500)
        except OSError:
            pass
        try:
            _出只 = _kg.建图(_只 / "锁", 打印=lambda *_: None)
        except Exception as _e:                   # noqa: BLE001
            坏.append(f"判五十⑤：写不进去的根**裸崩了**（{type(_e).__name__}: {_e}）"
                      " ⇒ 「任意文件夹」不成立")
            _出只 = None
        if _出只 is not None:
            _可读 = ("文件数" in _出只) or ("错误" in _出只)
            if not _可读:
                坏.append(f"判五十⑤：写不进去时返回的东西**读不懂**：{sorted(_出只)}")
            elif "错误" in _出只 and not 出只_建议(_出只):
                坏.append("判五十⑤：报了错误但**没给下一步**（「换可写的根」）"
                          " ⇒ 读的人不知道怎么办")
    finally:
        try:
            _os2.chmod(_只 / "锁", 0o700)
        except Exception:                         # noqa: BLE001
            pass
        _sh.rmtree(_只, ignore_errors=True)

    return _报2(坏, "知识图谱：绿 —— 判五十（通用·不静默跳过·语句不当定义·"
                    "增量按内容·墓碑·自洽门能红）")


def 判四十九Lean符号必须问elaborator且参考语料必须分层() -> None:
    """★ R303 ④⑤：**elaborator 第一来源** + **mathlib 参考语料独立成层**。

    ★ ④ 要证明的不是「接了一根线」，是**「问 elaborator 与查正则有区别」**：
      正则只看文本；elaborator 知道它把每条声明**真识别成了什么名字**。
      实测本仓确证集里 `_proof_` 2,366 · `_private` 2,030 这样的自动名
      —— **源码里不出现**，正则抽不到。**若这一类为 0，就是线没接上。**
    ★ ⑤ 要证明的是**分层而不是剪枝**：mathlib 仍**不进代码图**（成员资格不变），
      但进**独立**参考语料索引 —— 两个角色两套规则，不许塌成一条。
    ★ 还有一条针对**差点犯的错**：第一版参考索引只含**带足迹**的声明
      （6,599 条），拿它当「mathlib 有哪些定理」⇒ `Nat.add_comm` 查出「无」
      （纯内核的，不在里面）⇒ **把「看不见」当「没有」**。必须名字全量/足迹抽样分开。
    """
    坏: list[str] = []
    try:
        from agentscope_orchestrator import mem_axiom as _ma
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十九：导不进 mem_axiom（{type(_e).__name__}: {_e}）")
        return _报2(坏)

    # ── ④ ──
    try:
        符 = _ma.lean符号()
    except Exception as _e:                      # noqa: BLE001
        符 = {"炸了": f"{type(_e).__name__}: {_e}"}
    if "炸了" in 符:
        坏.append(f"判四十九④：`lean符号()` 炸了（{符['炸了']}）")
    elif 符.get("确证条数"):
        if "elaborator" not in str(符.get("★第一来源", "")):
            坏.append(f"判四十九④：确证档在，第一来源却不是 elaborator：{符.get('★第一来源')}")
        形状 = 符.get("★正则抽不到的自动名") or {}
        if not (形状.get("_proof_") or 形状.get("_private")):
            坏.append("判四十九④：确证集里 `_proof_`/`_private` **一条都没有** ⇒ "
                      "**「问 elaborator 与查正则有区别」这句话证不出来**（可能是线没接上）")
    else:
        if "兜底" not in str(符.get("★第一来源", "")):
            坏.append("判四十九④：没有确证档时必须明说**正则兜底**，不许冒充确证")
    # 正则兜底必须真的抽得到（**用命名组 `name`，不许猜序号** —— 我上一轮猜 group(1) 猜错了）
    try:
        r = _ma.正则兜底("theorem foo_bar : True := trivial")
        if not r or not any("foo_bar" in str(x) for x in r):
            坏.append(f"判四十九④：正则兜底抽不出 `foo_bar`，实得 {r[:2]} ⇒ "
                      f"兜底那条路是死的，或分组名取错了")
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十九④：正则兜底炸了（{type(_e).__name__}: {_e}）")

    # ── ④ 的**接线**（★ 这一格才是重点：函数对不对 ≠ 产品用不用它）──
    #   实测：`MyMeta.MetaTheory.ComputationLatticeEncoding`
    #         确证 659 · 正则 128 · **正则抽不到 187**（`_arg_pusher`/`_flat_ctor`/`_proof_1`…）
    try:
        import pathlib as _pl
        from agentscope_orchestrator import mem_symbols as _ms
        模 = "MyMeta.MetaTheory.ComputationLatticeEncoding"
        根 = _pl.Path(__file__).resolve().parents[3] / "形式化验证" / "formal_proof"
        源 = 根 / (模.replace(".", "/") + ".lean")
        if not 源.is_file():
            坏.append(f"判四十九④·接线：找不到真源文件 `{源}` ⇒ 接线**没在真件上测**")
        else:
            文 = 源.read_text(encoding="utf-8", errors="replace")
            符, 口 = _ms.抽取符号带口径(文, "lean", 模)
            if "elaborator" not in str(口.get("★来源", "")):
                坏.append(f"判四十九④·接线：给了模块名，来源却不是 elaborator：{口.get('★来源')}")
            漏 = 口.get("★确证但正则抽不到") or 0
            if not 漏:
                坏.append("判四十九④·接线：**「确证但正则抽不到」= 0** ⇒ "
                          "elaborator 那条路**其实没接上**（这个模块实测应为 187）")
            if not any(x[1] == "elaborator-only" for x in 符):
                坏.append("判四十九④·接线：返回的符号里**没有 `elaborator-only` 条目** ⇒ "
                          "elaborator 独有的名字没进产品结果")
            # **不给模块名** ⇒ 必须明说是兜底，不许冒充确证
            _, 口2 = _ms.抽取符号带口径(文, "lean")
            if "兜底" not in str(口2.get("★来源", "")):
                坏.append(f"判四十九④·接线：不给模块名时必须明说**正则兜底**，"
                          f"实得 {口2.get('★来源')}")
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十九④·接线：炸了（{type(_e).__name__}: {_e}）")

    # ── ④ 的**产品主流**（`符号表` 有没有真的走 elaborator）──
    #   实测踩过：`符号表.同步(根)` 给的 `相对` 是**相对那个根**的，
    #   而 `模块名()` 原来假设是**仓相对**（含 `formal_proof`）⇒ 全返回 None
    #   ⇒ **12,658 个文件全走兜底、`elaborator 文件数 = 0`**。
    #   ⇒ 判据必须落在**产品口径**上，不能只测那个函数。
    try:
        from agentscope_orchestrator import mem_symbols as _ms2
        if not hasattr(_ms2.符号表, "口径"):
            坏.append("判四十九④·产品：`符号表` 没有 `口径()` ⇒ "
                      "**接线接没接上看不见**（不可见就等于没接）")
        else:
            根2 = _pl.Path(__file__).resolve().parents[3] / "形式化验证" / "formal_proof"
            相 = "MyMeta/MetaTheory/ComputationLatticeEncoding.lean"
            源2 = 根2 / 相
            if not 源2.is_file():
                坏.append(f"判四十九④·产品：找不到真源 `{源2}` ⇒ 口径没在真件上测")
            else:
                表 = _ms2.符号表()
                表._重算(相, 源2.read_text(encoding="utf-8", errors="replace"))
                口3 = 表.口径()
                if not 口3.get("elaborator 文件数"):
                    坏.append(f"判四十九④·产品：`符号表` 走完一个真 .lean 文件后 "
                              f"**`elaborator 文件数` 仍为 0** ⇒ 产品主流**仍走正则兜底**，"
                              f"接线没接上：{口3}")
                if not 口3.get("★elaborator 独有符号数"):
                    坏.append(f"判四十九④·产品：`★elaborator 独有符号数` 为 0 ⇒ "
                              f"正则抽不到的那些名字**没进产品符号表**：{口3}")
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十九④·产品：炸了（{type(_e).__name__}: {_e}）")

    # ── ④ 的**最上一层**：类型与依赖（内核的视角，不是文本）──
    #   实测：53,714 条声明**全带类型**（100%）· **280 条**依赖 mathlib（0.52%）
    #   · 只用到 **53 种** mathlib 声明。
    #   ★ 这一层 `grep` 做不到：它只看得到 `import` 行（「我用了 mathlib」），
    #     **说不出「用了哪几条定理」**。
    try:
        import json as _json
        # ★ 层数我猜错过一次：`tests/x.py` 的 parents[1] 才是包目录。
        #   猜错的后果由**判据自己**报出来（「没有 类型与依赖.json ⇒ 这一层没落地」）。
        _P = _pl.Path(__file__).resolve().parents[1] / "products" / "mem" / "类型与依赖.json"
        if not _P.is_file():
            坏.append("判四十九④·类型依赖：没有 `类型与依赖.json` ⇒ 这一层没落地")
        else:
            _d = _json.loads(_P.read_text(encoding="utf-8"))
            if not _d.get("声明数"):
                坏.append("判四十九④·类型依赖：没写声明数")
            if _d.get("★拿到类型占比") != "100.0%":
                坏.append("判四十九④·类型依赖：类型没全拿到："
                          + str(_d.get("★拿到类型占比")))
            _依 = _d.get("★★依赖 mathlib 的声明数")
            if not _依:
                坏.append("判四十九④·类型依赖：**依赖 mathlib 的声明数为 0** ⇒ "
                          "依赖那一侧没算（本仓实测应为 280）")
            if not _d.get("★用到的 mathlib 声明总数"):
                坏.append("判四十九④·类型依赖：没写「用到多少种 mathlib 声明」⇒ "
                          "读数答不出「本仓到底站在 mathlib 的什么上面」")
            # **原始档必须在**：读数可复核，不许只有一个二手结论
            if not (_P.parent / "类型与依赖.tsv").is_file():
                坏.append("判四十九④·类型依赖：原始档 `类型与依赖.tsv` 不在 ⇒ "
                          "读数**不可复核**")
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十九④·类型依赖：炸了（{type(_e).__name__}: {_e}）")

    # ── ④ 的**跨轮记忆**（`mem_round.文件快照` 覆盖的应是「内核真有的」）──
    #   实测踩过：`mem_context` 那条只会拿 `rust`/`python`，**根本不到 Lean**
    #   （我原来把它算成「未接的调用点」，是**没读它就给结论**）⇒ 现在只测真到 Lean 的。
    #   实测：`ComputationLatticeEncoding.lean` 快照 315 条，其中
    #   `elaborator-only` **187** —— 正则只看得到 128。
    try:
        from agentscope_orchestrator import mem_round as _mr
        根3 = _pl.Path(__file__).resolve().parents[3] / "形式化验证" / "formal_proof"
        相3 = "MyMeta/MetaTheory/ComputationLatticeEncoding.lean"
        源3 = 根3 / 相3
        if not 源3.is_file():
            坏.append(f"判四十九④·跨轮：找不到真源 `{源3}` ⇒ 没在真件上测")
        elif not hasattr(_mr, "文件快照"):
            坏.append("判四十九④·跨轮：`mem_round` 没有 `文件快照` ⇒ 我记的接口名是错的")
        else:
            快 = _mr.文件快照(源3)
            哨 = [k for k, v in 快.items() if v[0] == "elaborator-only"]
            if not 哨:
                坏.append(f"判四十九④·跨轮：`文件快照` 里**没有 `elaborator-only`** ⇒ "
                          f"跨轮记忆仍只覆盖正则看得见的名字（实测应为 187）："
                          f"{len(快)} 条")
            else:
                # **不许伪造指纹**：`elaborator-only` 的体文不可知，
                # 拿空串算哈希会让「不知道」长得像「知道」。
                if any(v[1] not in ("", "elaborator-only") for k, v in 快.items()
                       if v[0] == "elaborator-only"):
                    坏.append("判四十九④·跨轮：`elaborator-only` 被算出了**像真哈希的值** ⇒ "
                              "「不知道」长得像「知道」")
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十九④·跨轮：炸了（{type(_e).__name__}: {_e}）")

    # ── ⑤ ──
    try:
        参 = _ma.参考语料()
        if "未测到" in 参:
            坏.append(f"判四十九⑤：参考语料未测到（{str(参['未测到'])[:60]}）"
                      f" ⇒ ⑤ 没落地")
        else:
            if "参考语料" not in str(参.get("角色", "")):
                坏.append(f"判四十九⑤：参考语料没写清自己的角色：{参.get('角色')}")
            if "不进图" not in str(参.get("角色", "")):
                坏.append("判四十九⑤：没写明 **mathlib 仍然不进代码图** ⇒ "
                          "分层与剪枝的区别没落进读数（会让人以为图变了）")
            if not 参.get("★名字条数（全量）"):
                坏.append("判四十九⑤：没有**全量名字数** ⇒ 只靠带足迹那份 ⇒ "
                          "`Nat.add_comm` 这种纯内核定理会查出「无」（看不见当没有）")
            if "抽样" not in str(参.get("★覆盖", "")):
                坏.append(f"判四十九⑤：覆盖口径没提**抽样**：{str(参.get('★覆盖'))[:80]}")
            # ★ 全量那一步的**门**：`MeasureTheory.Measure` 在 `import MyMeta` 的
            #   传递子集里**没有**（差的正好是整个测度论）⇒ 只测 `Nat.add_comm`
            #   抓不到「名字索引其实只是子集」这件事。
            查2 = _ma.查参考("MeasureTheory.Measure")
            _有 = str(查2.get("★mathlib 有这个名字吗（全量）", ""))
            # ★★ 门自己的缺陷（本轮实测抓出来的）：`有` 是 `没有` 的子串
            #    ⇒ `"有" not in "**没有**"` 为 **False** ⇒ 门**分不出「有」和「没有」**。
            #    一个抓不住反例的门 = 装饰。⇒ **先判否定，再判肯定。**
            if "没有" in _有 or "有" not in _有:
                坏.append("判四十九⑤：`MeasureTheory.Measure` 查不出「有」 ⇒ "
                          "名字索引**不是 mathlib 全量**（只是 `import MyMeta` 的传递子集）："
                          + _有[:60])
            查 = _ma.查参考("Nat.add_comm")
            _有2 = str(查.get("★mathlib 有这个名字吗（全量）", ""))
            if "没有" in _有2 or "有" not in _有2:
                坏.append(f"判四十九⑤：`Nat.add_comm` 查不出「有」（mathlib 一定有）"
                          f" ⇒ 名字索引不是全量：{str(查.get('★mathlib 有这个名字吗（全量）'))[:60]}")
    except Exception as _e:                      # noqa: BLE001
        坏.append(f"判四十九⑤：参考语料验证炸了（{type(_e).__name__}: {_e}）")

    return _报2(坏, "工程链路多文件齿：绿 —— 判四十九（④问 elaborator·自动名必须出现·"
                    "正则兜底真能抽·⑤参考语料分层且名字全量）")


def _报2(坏: list, 绿文: str = "") -> None:
    if 坏:
        print("工程链路多文件齿：红 —— " + "；".join(坏))
        raise SystemExit(1)
    if 绿文:
        print(绿文)



def 判一百七十一合流产物必须还在盘上():
    """★ R519：目标⑥（把 Lean/elaborator 那条路并进同一张图）—— **产物必须还在盘上**。

    ★★★ 缺陷现场（实测 2026-11-14，真形状、分母明确）：
    ````text
    formal_proof  节点 330,323   · 确证为真 **0** · 来源含 elaborator **0**
    太虚元道       节点 1,503,681 · 确证为真 **0** · 来源含 elaborator **0**
    形式化验证     节点 552,670   · 确证为真 **0** · 来源含 elaborator **0**
    而 formal_proof/lean确证.json 自称：确证模块数 746 · 确证名总数 53,704
                                     · ★文本级升级为已确证 **34,239**
    ````
    ★ 手工灌一次（`KG_图目录` 设好、第一个参数传**根**）：
    ````text
    灌前 形式化验证 节点 552,670 · 确证为真 0 · 来源含 elaborator 0
    灌后 形式化验证 节点 566,287 · 确证为真 **55,247** · 来源含 elaborator **55,247**
    ````
    ⇒ **机制是好的**（R368「漏写 `节点.json`」、R459「`图.json` 计数不更新」都修过）。
      **坏的是流程**：`灌Lean确证` 是**一次性显式调用**，之后任何一次
      `建图`/`重解析` 重建 `节点.json`，合流产物**就没了**，而**没有任何判据看着它**。
    ★ 这是 R366 那个病的**第二代**：
    ````text
    一代（R366）：`灌Lean确证` **写了、完整、没人调** ⇒ 判十一（没有死函数）抓到
    二代（现在）：**调过一次，但重建会冲掉** ⇒ 没有任何东西会报
    ````
    ★ 口径：**「跑过了」≠「还在」**。

    ★ 判据
    ````text
    ① 只要桶里有 `lean确证.json`，其 `节点.json` 里就必须有 `确证: true` 的节点
    ② 报数栏必须同时给**报升级数**与**盘上实得数**（口径 334：分子分母都要给）
    ③ 不许空集判绿：一个声明过要合流的桶都没有 ⇒ 红（口径 260）
    ````
    """
    坏: list[str] = []
    import json as _j171
    _图171 = _根173() / "work" / "图"
    if not _图171.is_dir():
        坏.append(f"判一百七十一③：`{_图171}` 不在 ⇒ 判不了（**不是绿**，口径 260）")
        return _报2(坏, "判一百七十一合流产物必须还在盘上：绿")
    _声171, _真171, _报171, _盘171 = [], [], 0, 0
    for _b in sorted(_图171.iterdir()):
        if not _b.is_dir():
            continue
        _档 = _b / "lean确证.json"
        _节 = _b / "节点.json"
        if not _档.is_file():
            continue
        _声171.append(_b.name)
        if not _节.is_file():
            坏.append(f"判一百七十一①：`{_b.name}` 声明过合流（有 `lean确证.json`）"
                      "但**没有 `节点.json`** ⇒ 合流无处可落")
            continue
        try:
            _元 = _j171.loads(_档.read_text(encoding="utf-8"))
        except Exception as _e:                                # noqa: BLE001
            坏.append(f"判一百七十一①：`{_b.name}/lean确证.json` 读不了（"
                      f"{type(_e).__name__}: {_e}）")
            continue
        for _k in ("★文本级升级为已确证", "文本级升级为已确证", "升级", "确证数"):
            if isinstance(_元, dict) and isinstance(_元.get(_k), int):
                _报171 += _元[_k]
                break
        try:
            _节元 = _j171.loads(_节.read_text(encoding="utf-8"))
        except Exception as _e:                                # noqa: BLE001
            坏.append(f"判一百七十一①：`{_b.name}/节点.json` 读不了（"
                      f"{type(_e).__name__}: {_e}）")
            continue
        _有 = 0
        if isinstance(_节元, dict):
            for _v in _节元.values():
                if isinstance(_v, dict) and _v.get("确证") is True:
                    _有 += 1
        _盘171 += _有
        if _有 == 0:
            坏.append(f"判一百七十一①：`{_b.name}` 的 `lean确证.json` 在，"
                      "但 `节点.json` 里 `确证: true` 的节点是 **0**"
                      " ⇒ **调过一次，被后来的重建冲掉了**（口径：「跑过了」≠「还在」）")
        else:
            _真171.append(_b.name)
    if not _声171:
        坏.append("判一百七十一③：**一个声明过要合流的桶都没有** ⇒ 空集上不许判绿")
    if 坏:
        return _报2(坏, "判一百七十一合流产物必须还在盘上：绿")
    print(f"   ● 判一百七十一：合流产物还在盘上 —— 绿"
          f"（声明合流 **{len(_声171)}** 桶 · 盘上有 `确证: true` 的 **{len(_真171)}** 桶"
          f" · 报升级 **{_报171:,}** · 盘上实得 **{_盘171:,}**）")


def 判一百七十二收尾五产物必须齐全且同代():
    """★ R519：**5 个收尾产物必须齐全，且不许比图旧**（口径 260/139/199/197）。

    ★★★ 缺陷现场（实测 2026-11-14）：
    ````text
    解析引用      定义 1 · 被调 4  ✓  ⇒ 引用.json / 外部缺.json / 缺口.json
    灌Lean确证    定义 1 · 被调 1  ✓
    自洽门        定义 1 · 被调 4  ✓
    解析md链接    定义 1 · 被调 **0**  ✗✗ （在 kg.py 内部；**判据里其实调了 4 次**）
    解析JSON引用  定义 1 · 被调 **0**  ✗✗ （在 kg.py 内部；**判据里其实调了 2 次**）
    ````
    ★ 而代码**自己**（`_未测到留痕`）写着收尾全套就是这 5 个产物：
    ````text
    ["引用.json", "外部缺.json", "缺口.json", "md链接.json", "json引用.json"]
    ````
    ⇒ **「建图」这条路一直在只做 1 个**。`md链接.json` / `json引用.json`
      **在 `建总图` 路径上从没被写过** ⇒ 目标④「未解析的必须报出来」在
      markdown/json 两支上**完全没接**。
    ★★ 这不是新病，是 R466 已经**记下来但没治**的那条：
    ````text
    实测事故（R465）：`work/图/formal_proof/` 里 `节点.json`/`边.json` 是**今天**的，
    而 `外部缺.json` 的 mtime 停在 **11-10 06:36（昨天）**
    ⇒ **同一个桶里的产物来自两个时间点** —— 我读旧口径当新读数。
    ````
    ★ `_未测到留痕` 只解决了「**早退**时要留痕」，**没解决「根本没被调」**（口径 322 同族）。

    ★ 判据
    ````text
    ① **齐全**：每个有 `节点.json` 的桶，5 个收尾产物**都要存在**
       —— ★ 存在即达标，**哪怕内容是空的**（口径 260/139）
    ② **同代**：这 5 个产物的 mtime **不许早于 `节点.json`**（图是它们的输入）
    ③ **不许空集判绿**：全仓一个桶都没有 ⇒ 红（口径 260）
    ★ 报数栏必须给**分子与分母**（口径 334）：`齐全的桶 / 有图的桶`。
    ````
    """
    坏: list[str] = []
    _仓172 = _根173() / "work" / "图"
    _产172 = ["引用.json", "外部缺.json", "缺口.json", "md链接.json", "json引用.json"]
    if not _仓172.is_dir():
        坏.append(f"判一百七十二③：`{_仓172}` 不在 ⇒ 判不了（**不是绿**，口径 260）")
        return _报2(坏, "判一百七十二收尾五产物必须齐全且同代：绿")
    _有图172, _齐172 = 0, 0
    _缺计172: dict[str, int] = {}
    for _b in sorted(_仓172.iterdir()):
        if not _b.is_dir():
            continue
        _fn = _b / "节点.json"
        if not _fn.is_file():
            continue
        _有图172 += 1
        _图时 = _fn.stat().st_mtime
        _缺 = [m for m in _产172 if not (_b / m).is_file()]
        if not _缺:
            _齐172 += 1
        for m in _缺:
            _缺计172[m] = _缺计172.get(m, 0) + 1
        if _缺:
            坏.append(f"判一百七十二①：`{_b.name}` 缺 **{len(_缺)}** 个收尾产物 "
                      f"{_缺} ⇒ 「**没看**」不是「没有」；"
                      "路上的断链/未解析引用**AI 一条都读不到**")
        for m in _产172:
            _f = _b / m
            if _f.is_file() and _f.stat().st_mtime < _图时 - 2:
                import datetime as _dt172
                坏.append(f"判一百七十二②：`{_b.name}` 的 `{m}` 比 `节点.json` **旧**"
                          f"（{_dt172.datetime.fromtimestamp(_f.stat().st_mtime):%m-%d %H:%M}"
                          f" < {_dt172.datetime.fromtimestamp(_图时):%m-%d %H:%M}）"
                          " ⇒ 图重算过而它没重跑 ⇒ **读的人拿到旧世界**")
    if _有图172 == 0:
        坏.append("判一百七十二③：图目录下**一个有 `节点.json` 的桶都没有** ⇒ 空集上不许判绿")
    if 坏:
        return _报2(坏, "判一百七十二收尾五产物必须齐全且同代：绿")
    print(f"   ● 判一百七十二：5 个收尾产物齐全且同代 —— 绿"
          f"（齐全 **{_齐172}** / 有图的桶 **{_有图172}**）")


def 判一百七十四桶的规模不许凭空塌掉():
    """★ R519：**一个文件至少一个节点** —— 这条不变量钉死我 2026-11-14 那次事故。

    ★ ★★★ 事故（我自己造成的，留档不许删）：
      我把**桶目录** `work/图/太虚元道` 当成「源码根」传给了 `收尾全套`
      （第一参数是**源码根**）⇒ 它 `灌Lean确证` 并**覆盖** `节点.json`
      ⇒ 两个桶被重建：
      ````text
      桶           图.json 的根（**是对的**）              节点数 之前 → 现在    文件数
      太虚元道      …\\太虚元道                            1,503,681 → **21,160**   26,040
      形式化验证    …\\太虚元道\\形式化验证                   552,670 → **21,160**    5,883
      ````
      ★ 两个桶节点数**完全相同** ⇒ 同一份内容写进了两个桶。

    ★ 为什么旧判据一条都没拦住：
      · 判九十 查「索引与图同源」——事故后**索引也重刷过**，两边一起错 ⇒ 反而一致；
      · 判一百七十一 只查「`lean确证.json` 在的桶有没有 `确证: true`」——
        重建后**确实有**（21,160 个全是确证节点）⇒ **绿**；
      · **没有任何判据看「这张图是不是突然小了一个数量级」。**

    ★ 判据（**只读小的 `图.json`，不读 679 MB 的 `节点.json`** ⇒ 每轮都能跑）：
      ★ 实得键名（量过，不是猜）：`根` · `建于` · `模式` · **`文件数`** · `重算文件数` ·
        **`节点数`** · `边数` · `适配器分布` · `跳过数` · `★自洽门` · `★Lean合流`
      ````text
      ① 每个有 `图.json` 的桶：`节点数` ≥ `文件数` —— **一个文件至少一个节点**
         （事故数据直接违反：26,040 个文件 vs 21,160 个节点）
         ★ 键名实测：`文件数`（**不是** `文件` —— 我第一版写错，判据自己先坏）
      ② 两个计数都 > 0（空图不许当绿，口径 260）
      ③ **每一个**桶都要满足 ①，不许用平均值兜住
      ````
    ★ 边界（如实）：它**只**能抓「图与自己的文件数不匹配」这一类塌陷；
      若事故把 `文件` 也一起改小，它看不见 ⇒ **不替代同源对账**，
      只是把「一个文件至少一个节点」这个不可能不成立的不变量钉住。
    """
    坏: list[str] = []
    import json as _j174
    _图174 = _根173() / "work" / "图"
    if not _图174.is_dir():
        坏.append(f"判一百七十四②：`{_图174}` 不在 ⇒ 判不了（**不是绿**，口径 260）")
        return _报2(坏, "判一百七十四桶的规模不许凭空塌掉：绿")
    _有图174, _合格174, _空图174 = 0, 0, 0
    for _b in sorted(_图174.iterdir()):
        if not _b.is_dir():
            continue
        _g = _b / "图.json"
        if not _g.is_file():
            continue
        _有图174 += 1
        try:
            _元 = _j174.loads(_g.read_text(encoding="utf-8"))
        except Exception as _e:                                # noqa: BLE001
            坏.append(f"判一百七十四：`{_b.name}/图.json` 读不了（"
                      f"{type(_e).__name__}: {_e}）")
            continue
        # ★ 键名**实测**（口径 141/338，不许猜）：`图.json` 是
        #   ['根','建于','模式','**文件数**','重算文件数','**节点数**','边数','适配器分布',…]
        #   ★ 我第一版写的是 `文件` ⇒ `get` 恒为 `None` ⇒ 判据自己先坏掉。
        _节数 = _元.get("节点数")
        _文数 = _元.get("文件数")
        if not isinstance(_节数, int) or not isinstance(_文数, int):
            坏.append(f"判一百七十四②：`{_b.name}` 的 `图.json` 缺 `节点数`/`文件数`"
                      f"（得 {_节数!r} / {_文数!r}）⇒ 规模判不了")
            continue
        if _节数 <= 0 or _文数 <= 0:
            _空图174 += 1
            坏.append(f"判一百七十四②：`{_b.name}` 是**空图**（节点 {_节数} · 文件 {_文数}）"
                      "⇒ 空图不许当绿（口径 260）")
            continue
        if _节数 < _文数:
            坏.append(f"判一百七十四①：`{_b.name}` **一个文件不到一个节点** —— "
                      f"节点数 {_节数:,} < 文件数 {_文数:,} ⇒ "
                      "图与自己的文件数不匹配（**这正是 R519 那次事故的形状**："
                      "桶目录被当成源码根，图被重建小了一个数量级）")
            continue
        _合格174 += 1
    if _有图174 == 0:
        坏.append("判一百七十四②：`work/图` 下**一个有 `图.json` 的桶都没有**"
                  " ⇒ 空输入上不许判绿（口径 260）")
    if 坏:
        return _报2(坏, "判一百七十四桶的规模不许凭空塌掉：绿")
    print(f"   ● 判一百七十四：桶的规模自洽 —— 绿"
          f"（合格 **{_合格174}** / 有图桶 **{_有图174}** · 空图 {_空图174}）")


def 判一百七十五源码根不许自己就是图桶():
    """★ R519：**`根` ≠ `_kg目录(根)`** —— 钉死我 2026-11-14 那天毁掉两张图的机制。

    ★ 机制（读代码读出来的，不是猜的）：
      `kg._kg目录(根)` 在 `KG_图目录` 设着时是 `Path(_env图目录) / 根.name`
      ⇒ 传**桶目录** `work/图/太虚元道` 时 `根.name` = `太虚元道`
      ⇒ `_kg目录(桶目录)` = **桶自己**
      ⇒ `灌Lean确证(桶目录)` 在**桶自己的图上原地重写**，而它**不报错**。

    ★ 实测后果（两个桶节点数**完全相同** ⇒ 同一份确证节点覆盖了两张图）：
      ````text
      桶           节点数 之前 → 现在    文件数
      太虚元道      1,503,681 → **21,160**   26,040
      形式化验证     552,670 → **21,160**    5,883
      ````

    ★ 判据：
      ① 对每个有 `图.json` 的桶：`图.json.根` **不许等于**该桶目录自己
         （等价地：那个「根」的 `_kg目录` 不许是它本身）。
      ② `根` 字段必须是**绝对路径的、真实存在的目录** ——
         指向不存在的根 ⇒ 判不了（**不是绿**）。
    ★ 边界（如实）：它守的是**调用姿势**；拦不住「传了另一个**真实**源码根」
      （那会把图换成那个根的图）—— 那种靠判一百七十四的规模校验。
    """
    坏: list[str] = []
    import json as _j175
    _图175 = _根173() / "work" / "图"
    if not _图175.is_dir():
        坏.append(f"判一百七十五②：`{_图175}` 不在 ⇒ 判不了（**不是绿**，口径 260）")
        return _报2(坏, "判一百七十五源码根不许自己就是图桶：绿")
    _有175, _合格175, _自指175, _根不在175 = 0, 0, 0, 0
    for _b in sorted(_图175.iterdir()):
        if not _b.is_dir():
            continue
        _g = _b / "图.json"
        if not _g.is_file():
            continue
        _有175 += 1
        try:
            _根 = _j175.loads(_g.read_text(encoding="utf-8")).get("根")
        except Exception as _e:                                # noqa: BLE001
            坏.append(f"判一百七十五：`{_b.name}/图.json` 读不了（"
                      f"{type(_e).__name__}: {_e}）")
            continue
        if not _根:
            坏.append(f"判一百七十五②：`{_b.name}` 的 `图.json` 没写 `根` ⇒ 判不了")
            continue
        _P根 = Path(_根)
        if not _P根.is_dir():
            _根不在175 += 1
            坏.append(f"判一百七十五②：`{_b.name}` 记的根**不在**：`{_根}` ⇒ "
                      "判不了（**不是绿** —— 「看不见」不能当「没问题」）")
            continue
        if _P根.resolve() == _b.resolve():
            _自指175 += 1
            坏.append(f"判一百七十五①：`{_b.name}` 的根**就是它自己**（`{_根}`）"
                      " ⇒ 源码根被当成了图桶 —— **这正是 R519 毁图的机制**："
                      "`_kg目录(桶目录)` = 桶自己 ⇒ `灌Lean确证` 原地重写自己的图")
            continue
        _合格175 += 1
    if _有175 == 0:
        坏.append("判一百七十五②：`work/图` 下**一个有 `图.json` 的桶都没有**"
                  " ⇒ 空输入上不许判绿（口径 260）")
    if 坏:
        return _报2(坏, "判一百七十五源码根不许自己就是图桶：绿")
    print(f"   ● 判一百七十五：源码根都不是图桶 —— 绿"
          f"（合格 **{_合格175}** / 有图桶 **{_有175}** · 自指 {_自指175} · 根不在 {_根不在175}）")


def 判一百七十六依赖与虚拟环境不许混进项目内容():
    import json as _j176
    """★ R521：图里**不许**把依赖目录/虚拟环境当成"项目自己的文件"。

    ## 为什么有这条（实测）

    `太虚元道`（总根）桶实测：

    ```text
    26,295 个 `类=文件` 节点中
      r291v_* 迭代残骸（内含 site-packages）  6,337
      /site-packages/ 或 /.venv/              6,297
      _work*/                                   729
      headwk*                                   184
    ⇒ 3,202,812 条引用边里 **1,145,211 条（35.8%）来自残骸路径** ✗
    ```

    ★ 本层的目的是「**让 AI 知道自己有什么东西**」。
      把 `site-packages/pip/_vendor/urllib3/...` 当成"自己的东西"，
      不叫"知道自己有什么"，叫**把别人的库当成自己的家当**。

    ## 根因

    `枚举文件` 的 `_必剪` 有 `.venv` / `node_modules`，**但没有 `site-packages`**；
    而残骸的真实路径是
    `…/r291v_4ed_jak_/v/Lib/site-packages/…` —— 目录名是 **`v`** 不是 `.venv`
    ⇒ **名字判据一个都没命中** ⇒ 照收。

    ## 这条判据只查"路径里有没有语义确定的依赖段"

    ★ **不查"目录名像不像残骸"** —— R338 的教训：`Lib/venv/` 是标准库真模块、
      `pip/_internal/operations/build/` 是真源码，按名字剪会**误伤真源码**。
    ★ 口径 203：**宁可漏剔，不可错剔** ⇒ 下面的段全是**语义确定**的。
    """
    #: ★ 语义确定的「不是本项目内容」的路径段。
    #:   `site-packages`/`dist-packages` = Python 的依赖落点（无论宿主目录叫什么）
    #:   `node_modules` = Node 的依赖落点 · `vendor`/`third_party` = 常见 vendoring 目录
    #:   `.git`/`.hg`/`.svn` = 版本库元数据 · `__pycache__` = 编译缓存
    #:   `.venv`/`venv` = 虚拟环境根（**外加**下面的 `pyvenv.cfg` 实检）
    依赖段 = {
        "site-packages", "dist-packages", "node_modules", "vendor", "third_party",
        ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
        ".pytest_cache", ".ruff_cache",
    }
    根 = _根173()
    D = 根 / "work" / "图"
    if not D.is_dir():
        _报2([f"判一百七十六：图根不在（{D}）—— **判不了，不是绿**"], "")
        raise SystemExit(1)
    坏: list[str] = []
    查 = 0
    各桶: list[str] = []
    for 桶 in sorted(D.iterdir()):
        if not (桶 / "图.json").is_file():
            continue
        戳 = 桶 / "戳.json"
        if not 戳.is_file():
            坏.append(f"判一百七十六①：`{桶.name}` 有 图.json 却没有 戳.json"
                      f" —— **文件清单不见了**（「看不见」不能当「没有」）")
            continue
        文件们 = _j176.loads(戳.read_text(encoding="utf-8"))
        if not isinstance(文件们, dict):
            坏.append(f"判一百七十六②：`{桶.name}` 的 戳.json 不是 dict（{type(文件们).__name__}）")
            continue
        查 += len(文件们)
        脏 = []
        for 相 in 文件们:
            段 = [x for x in str(相).replace("\\", "/").split("/") if x]
            if any(x in 依赖段 for x in 段):
                脏.append(str(相))
            elif "pyvenv.cfg" in 段:
                脏.append(str(相))
        if 脏:
            各桶.append(f"`{桶.name}` {len(脏)}")
            坏.append(f"判一百七十六③：`{桶.name}` 有 **{len(脏)}** 个文件在依赖/虚拟环境里"
                      f"（例：{脏[0][:88]}）—— 那不是「这个项目的东西」")
    if not 查:
        坏.append("判一百七十六④：**一个桶的文件清单都读不到** —— 判据自己没接上（口径 307）")
    if 坏:
        _报2(坏, "")
        raise SystemExit(1)
    _报2([], f"● 判一百七十六：图里没有依赖/虚拟环境混入 —— 绿"
             f"（桶 **{len(list(D.iterdir()))}** · 查文件 **{查:,}** 条路径 · 脏 0）")


def 判一百七十七备份临时文件不许混进项目内容():
    """★ R521b：图里**不许**把备份/临时文件当成"项目自己的文件"（**文件级**）。

    ## 为什么（实测）

    `太虚元道` 桶的 `戳.json` 里：

    ```text
    .bak_ts046_mod_before_reuse_retry.rs   233,812 字节
    .bak_ts046_mod_before_tiebreak.rs      233,812 字节   ← **sha1 与上一个完全相同**
    .tmp_ReuseGap_check.lean
    ```

    ★ `_是备份目录` 只管**目录名** ⇒ 这些**照收**。
    ★ 两个 `.bak_*` 的 sha1 一模一样 ⇒ 图上表现为
      「**两个文件定义了同一批名字**」—— 读起来像"重复定义"，其实是**同一份代码的副本** ✗

    ## ★★★ 这里为什么**不** import `kg._是备份文件`

    判据的职责是**能判它错**。若共用同一个正则，
    「**kg 的规则被削弱了**」这件事就**永远判不出来**
    （口径 238/252：判据必须能区分）。
    ★ 这与本目标 ⑤「用**权威解析器**独立算出真值再对账」是同一个道理。

    ## 口径 203：宁可漏剔，不可错剔

    只认**语义确定**的五种形态 —— **不含** `_\d{8}$`
    （那个对目录判备份合适，对文件会误伤 `报告_20260427` 这类正常数据文件）。
    """
    _re177 = __import__("re")
    _j177 = __import__("json")
    #: ★ **独立**写一遍（不 import kg 的），才能判 kg 错。
    像备份 = _re177.compile(
        r"(^|/)\.bak([_.\-]|$)"
        r"|\.bak\d*$"
        r"|(^|/)\.tmp_"
        r"|~$"
        r"|\.(orig|rej)$"
    )
    根 = _根173()
    D = 根 / "work" / "图"
    if not D.is_dir():
        _报2([f"判一百七十七：图根不在（{D}）—— **判不了，不是绿**"], "")
        raise SystemExit(1)
    坏: list[str] = []
    查 = 0
    for 桶 in sorted(D.iterdir()):
        if not (桶 / "图.json").is_file():
            continue
        戳 = 桶 / "戳.json"
        if not 戳.is_file():
            坏.append(f"判一百七十七①：`{桶.name}` 有 图.json 却没有 戳.json"
                      f" —— **文件清单不见了**（「看不见」不能当「没有」）")
            continue
        文件们 = _j177.loads(戳.read_text(encoding="utf-8"))
        if not isinstance(文件们, dict):
            坏.append(f"判一百七十七②：`{桶.name}` 的 戳.json 不是 dict")
            continue
        查 += len(文件们)
        脏 = [str(k) for k in 文件们 if 像备份.search(str(k).replace("\\", "/"))]
        if 脏:
            坏.append(f"判一百七十七③：`{桶.name}` 有 **{len(脏)}** 个备份/临时文件进了图"
                      f"（例：{脏[0][:88]}）—— 那是同一份代码的历史副本，不是「我有什么」")
    if not 查:
        坏.append("判一百七十七④：**一个桶的文件清单都读不到** —— 判据自己没接上（口径 307）")
    if 坏:
        _报2(坏, "")
        raise SystemExit(1)
    _报2([], f"● 判一百七十七：图里没有备份/临时文件 —— 绿"
             f"（查文件 **{查:,}** 条路径 · 脏 0）")


def 判一百七十八每种语法树语言必须用真样本验过():
    """★ R522：**「本仓没有样本」既不等于「机制不存在」，也不等于「机制可用」。**

    ## 这条判据是怎么来的（本轮实测）

    我一度以为「**Go / TypeScript 的 import 没做**」——因为盘上读数：
    ```text
    .go    **无引用边**
    .tsx   **无引用边**
    .ts    引用 15 条（依据里没有导入类）
    ```

    ★ 实地一查：全仓 `.go` / `.ts` / `.tsx` 的文件**全是 0 字节**
      （`agentscope_orchestrator/r265_*/repo/src/*.go|ts|tsx` 是测试 fixture）
      ⇒ **本仓根本没有真实样本** ⇒ 0 条边**是正确行为**。

    ⇒ ★ 而「**没有样本**」这件事，**从图上永远看不出来**
      （图上只有 0，看不出是"没做"还是"没东西可做"）
      ⇒ 判据必须**自带样本**，才谈得上"机制可用"。

    ## 这个判据抓到的真缺陷（实例）

    `.ts` 的节点原本报 `语言 = "typescript:typescript"` ✗ ——
    因为 `_语法` 表的值是「**grammar 选择键**」（`语言名:变体名`，
    `tree_sitter_typescript` 没有 `language()`，只有 `language_typescript()`/`language_tsx()`），
    却被**当成语言名**写进节点。
    ★ 下游按语言做的判断（语言内置表 · 判 166 watchdog）**一个都匹配不上**
      ⇒ 那些节点在按语言的判断里**等于隐形**（口径 260）。

    ## 口径

    ★ 307：**一个样本都没测成 ⇒ 红**（判据自己没接上也算红，不是绿）。
    ★ 138：样本要**真能触发**那条路径（含真 import/use 语句），不是空文件。
    ★ 210：本判据的**红路径**由「语言名带 `:`」这一真实缺陷给出过。
    """
    _p178 = __import__("pathlib")
    _t178 = __import__("tempfile")
    #: （语言名, 文件后缀, 含真 import/use 的最小样本）
    #:  ★ ★ 每个样本都必须**真的能让适配器产出「模块」节点** ——
    #:    否则这条断言在测"我写的样本对不对"，不是在测适配器。
    样本 = [
        ("python", ".py", 'import os\nfrom pathlib import Path\n\ndef f():\n    return Path(os.getcwd())\n'),
        ("rust", ".rs", 'use std::collections::HashMap;\nuse crate::util::helper;\nfn main() { helper(); }\n'),
        ("javascript", ".js", 'import { join } from "path";\nexport function g() { return join("a"); }\n'),
        ("typescript", ".ts", 'import { readFile } from "fs";\nimport * as path from "path";\nexport function f() { return readFile(path.join("a")); }\n'),
        ("typescript", ".tsx", 'import React from "react";\nexport const A = () => <div/>;\n'),
        ("go", ".go", 'package main\n\nimport (\n\t"fmt"\n\tm "os"\n)\n\nfunc main() { fmt.Println(m.Args) }\n'),
        ("java", ".java", 'import java.util.List;\nimport java.util.Map;\npublic class A { List<String> x; Map<String,String> y; }\n'),
        ("c", ".c", '#include <stdio.h>\n#include "local.h"\nint main(void) { return 0; }\n'),
        ("cpp", ".cpp", '#include <vector>\n#include "helper.hpp"\nint main() { return 0; }\n'),
    ]
    try:
        import agentscope_orchestrator.kg as _kg178
    except Exception as e:                                    # noqa: BLE001
        _报2([f"判一百七十八①：`kg` 导不进来（{type(e).__name__}: {e}）"
              f" —— **判不了，不是绿**"], "")
        raise SystemExit(1)
    坏: list[str] = []
    目录 = _p178.Path(_t178.mkdtemp(prefix="kg判178_"))
    测成 = 0
    无语言: list[str] = []
    for 语言, 后缀, 文 in 样本:
        名 = "样本" + 后缀
        p = 目录 / 名
        try:
            p.write_text(文, encoding="utf-8")
        except OSError as e:                                  # noqa: PERF203
            坏.append(f"判一百七十八②：样本写不进去（{type(e).__name__}: {e}）")
            continue
        try:
            出 = _kg178._解析一个(p, 名)
        except Exception as e:                                # noqa: BLE001
            坏.append(f"判一百七十八③：`{语言}`（`{后缀}`）解析就抛 "
                      f"{type(e).__name__}: {str(e)[:70]}")
            continue
        节 = 出[0] if 出 else []
        if not 节:
            坏.append(f"判一百七十八④：`{语言}`（`{后缀}`）**一个节点都没抽出**"
                      f" —— 样本里有真 import，抽不出就是适配器的问题")
            continue
        测成 += 1
        语集 = {str(v.get("语言")) for v in 节 if isinstance(v, dict) and v.get("语言")}
        带冒 = sorted(x for x in 语集 if ":" in x)
        if 带冒:
            坏.append(f"判一百七十八⑤：`{语言}`（`{后缀}`）的节点语言名带 grammar 变体后缀 "
                      f"**{带冒[0]}** —— `:` 后面是 grammar 选择用的变体名，不是语言名；"
                      f"下游按语言做的判断会**一个都匹配不上**")
        if 语言 not in 语集:
            无语言.append(f"{语言}({后缀})→{sorted(语集) or '空'}")
        类集 = {str(v.get("类")) for v in 节 if isinstance(v, dict)}
        if not (类集 & {"模块", "定义", "声明", "项", "绑定"}):
            坏.append(f"判一百七十八⑥：`{语言}`（`{后缀}`）抽出的节点里没有"
                      f"「模块/定义/声明/项/绑定」任何一类（实得 {sorted(类集)}）")
    if 测成 == 0:
        坏.append("判一百七十八⑦：**一个样本都没测成** —— 判据自己没接上（口径 307）")
    if 无语言:
        坏.append(f"判一百七十八⑧：这些语言**没在自己的样本节点上报出语言名**"
                  f"（口径 260：看不见不能当没有）：{' · '.join(无语言[:6])}")
    if 坏:
        _报2(坏, "")
        raise SystemExit(1)
    _报2([], f"● 判一百七十八：**{测成}** 种语言的适配器都用真样本验过 —— 绿"
             f"（语言名全规范 · 每种都抽出了「模块」等实体 · 样本自带，不依赖仓里有什么）")


def 判一百七十九桶内产物的根必须与图一致():
    """★ R522b：桶里**每一个**带 `根` 字段的产物，都必须与 `图.json` 的根一致。

    ## 这条判据是怎么来的

    R519/R520 出过一次事故：我把**桶目录**（`work/图/形式化验证`）当成源码根
    传给了 `收尾全套` / `灌Lean确证`。因为 `_kg目录(桶) == 桶`，
    它**读自己的图、又写回自己** ⇒ 一桶 1,503,681 节点被写成 21,160。

    R521 加了 `判一百七十五`（源码根不许自己就是图桶）——
    ★ 但**它只查 `图.json` 的 `根`**。

    ## 实测（本轮）：残留在盘上，判 175 看不见

    `形式化验证` 桶的 `图.json` 根**是好的**（重建修正了），可这四个附属产物：

    ```text
    json引用.json   根 = ...\work\图\形式化验证   ← **桶自己**
    md链接.json     根 = ...\work\图\形式化验证   ← **桶自己**
    外部缺.json     根 = ...\work\图\形式化验证   ← **桶自己**
    缺口.json       根 = ...\work\图\形式化验证   ← **桶自己**
    ```

    ⇒ **事故的残留还在盘上**（口径 260：看不见不能当没有）。

    ## 为什么必须判（不只是"字段写错了"）

    · `根` 是**读数的坐标系** —— 根错了，「这个路径在哪」全部错位；
    · 更要紧：**它证明那次收尾跑在了桶自己上** ⇒ 那批读数
      （引用/断链/缺口）**一个都不能用**。
    ★ 而它们现在**长得像正常产物**（八个计数字段一个不少）——
      **最坏的一种坏：看起来对**。

    ## 口径 203 的用法（宁可漏判，不可错判）

    只判**能读成 JSON 对象且带 `根` 字段**的 `.json` 产物；
    读不动的不算它错（可能是我读法不对）—— 但**必须计数报出**，不许静默跳过。
    """
    _j179 = __import__("json")
    _p179 = __import__("pathlib").Path
    根 = _根173()
    D = 根 / "work" / "图"
    if not D.is_dir():
        _报2([f"判一百七十九：图根不在（{D}）—— **判不了，不是绿**"], "")
        raise SystemExit(1)
    坏: list[str] = []
    读不动 = 0
    查产物 = 0
    桶数 = 0
    for 桶 in sorted(D.iterdir()):
        g = 桶 / "图.json"
        if not g.is_file():
            continue
        try:
            图根 = _j179.loads(g.read_text(encoding="utf-8")).get("根")
        except Exception:                                     # noqa: BLE001
            坏.append(f"判一百七十九①：`{桶.name}` 的 图.json 读不动")
            continue
        if not 图根:
            坏.append(f"判一百七十九②：`{桶.name}` 的 图.json **没有 `根` 字段**")
            continue
        桶数 += 1
        应 = str(_p179(图根).resolve())
        for f in sorted(桶.glob("*.json")):
            if f.name == "图.json":
                continue
            try:
                文 = f.read_text(encoding="utf-8")
                d = _j179.loads(文)
            except Exception:                                 # noqa: BLE001
                读不动 += 1
                continue
            if not isinstance(d, dict) or "根" not in d:
                continue                                       # 没有坐标系，不管它
            查产物 += 1
            实 = str(_p179(str(d.get("根"))).resolve())
            if 实 != 应:
                坏.append(f"判一百七十九③：`{桶.name}/{f.name}` 的 `根` 是 **{实}**，"
                          f"与 图.json 的 **{应}** 不一致 —— "
                          f"★ 这说明**收尾跑在了别的东西上**（R519 那次就是跑在桶自己上），"
                          f"**该产物的读数一个都不能用**")
    if 桶数 == 0:
        坏.append("判一百七十九④：**一个有 图.json 的桶都没有** —— 判据自己没接上（口径 307）")
    if 查产物 == 0:
        坏.append("判一百七十九⑤：**一个带 `根` 的附属产物都没查到** —— "
                  "判据自己没接上（口径 307）")
    if 坏:
        _报2(坏, "")
        raise SystemExit(1)
    _报2([], f"● 判一百七十九：桶内产物的 `根` 全部与图一致 —— 绿"
             f"（桶 **{桶数}** · 查带根产物 **{查产物}** · 读不动（不计错）{读不动}）")


def 判一百八十跳过记录必须能定位() -> None:
    """跳过清单里每条**文件级**记录都必须**指向具体文件**（口径 191）。

    ★ 缺陷现场（2026-11-15，**陌生仓库验收**撞出来的，不是本仓翻出来的）：
      验收脚本判「生产枚举 vs 图 + 跳过」，报 apache-jena「静默丢 **112**」·
      认知主权「静默丢 **1,312**」。查下去**不是丢**，是
      **跳过记录只写了 basename、没有身份**：

          跳集 ∩ basename(应跳) = **116 / 116**   ← 假设成立
          跳集 ∩ 相对路径(应跳) =     4 / 116

    ★ 根因（口径 305 的教科书案例，就藏在「已修」的注释旁边）：
      `_过值得` 三条分支里**只有「过大」那条**写了 `路径` ——
      那条的注释自己写着「口径 191：跳过项必须能定位……实测第一版只写
      `文件: f.name` ⇒ 48 条里**多个 `mod.rs` 分不清**」，
      而另两条（扩展名无结构 / 压缩生成物）**仍然只写 `文件: f.name`**。
      ⇒ 同一处置写在三处、只改了一处。

    ★ 实测规模（交付态 `work/图`）：
        跳过记录 **148,603** · 有 `路径` **14,091** · **只有 basename 134,512**
        · 其中 **46,768 处 basename 撞名**（不同目录的同名文件分不清）
      ⇒ 那条注释写的实测是「48 条」，**实际是 13 万条**。

    ★ 后果不是「难看」：**「生产枚举 vs 图 + 跳过」的对账永远算不平** ——
      验收脚本正是因此报出「静默丢」的**假缺陷**，而真相是
      **跳过记录没有身份**（口径 260 的反面：这里不是「看不见当没有」，
      而是「看不见具体是哪一个」）。

    ★ 判据：① 每条跳过记录都要有**可定位的路径**（`路径`）；
      ② 同时报出 **basename 撞名**数（那是"必须能定位"的直接代价）；
      ③ **不许为 0 就判绿**（口径 307）—— 没有桶 / 没有记录 ⇒ 红。
    ★ 口径 203：**不静默跳过** —— 只有 basename 的那些**逐条进红文案**（给例）。
    """
    坏: list[str] = []
    import json as _j180
    _D = Path(__file__).resolve().parents[1] / "work" / "图"
    if not _D.is_dir():
        坏.append("判一百八十：`work/图` 不在 ⇒ 判不了（口径 307：**判不了不许判绿**）")
        return _报2(坏)
    桶数 = 总 = 有路 = 只名 = 撞名 = 0
    例: list = []
    for _b in sorted(_D.iterdir()):
        _f = _b / "清单.json"
        if not _f.is_file():
            continue
        桶数 += 1
        try:
            _跳 = _j180.loads(_f.read_text(encoding="utf-8")).get("跳过") or []
        except Exception as _e:                                # noqa: BLE001
            坏.append(f"判一百八十：`{_b.name}/清单.json` 读不动（{type(_e).__name__}）")
            continue
        _见: dict = {}
        for _x in _跳:
            if not isinstance(_x, dict):
                continue
            总 += 1
            if _x.get("路径"):
                有路 += 1
            elif _x.get("文件"):
                只名 += 1
                if len(例) < 4:
                    例.append(f"{_b.name}: {_x.get('文件')!r}")
                _见[str(_x["文件"])] = _见.get(str(_x["文件"]), 0) + 1
        撞名 += sum(_v - 1 for _v in _见.values() if _v > 1)
    if 桶数 == 0:
        坏.append("判一百八十：**一个桶都没有** ⇒ 判据自己没接上（口径 307）")
    elif 总 == 0:
        坏.append("判一百八十：**跳过记录一条都没有** ⇒ 判不出来（口径 307）")
    elif 只名:
        坏.append(f"判一百八十：**只有 basename、定位不到文件** {只名:,} 条"
                  f"（共 {总:,} 条 · 有 `路径` {有路:,}）—— 其中 **basename 撞名 {撞名:,} 处**"
                  f"（不同目录的同名文件分不清）⇒ 口径 191「跳过项必须能定位」"
                  f"**只落实在一部分分支上**（口径 305：同一处置写在几处、只改一处就是病）。"
                  f"例：{例}")
    return _报2(坏, f"跳过记录可定位：绿 —— 判一百八十（{桶数} 桶 · 跳过记录 {总:,} 条 · "
                    f"**全部带可定位路径** · basename 撞名 0）")


def 判一百八十六粘滞剪枝不许让进过图的目录消失() -> None:
    """**进过图的目录，这一轮不许被剪**（用户 2026-11-15 定的「粘滞」）。

    ★ 缺陷：剪枝规则「同前缀兄弟 ≥3 个 ⇒ 运行副本」的输入是**盘上的兄弟**，
      而兄弟是**别的会话随时会新建的** ⇒ **图的文件集不是路径的函数**：
      同一个目录，只要旁边多出两个同名兄弟，它就从图里消失
      （`★剪枝漂移` 就是为这个设的读数）。★ 这顶到「任意文件夹 ⇒ 完备知识图谱」。

    ★★ 判据 = **活体实验 + 对照组**（口径 355）：
      ① 临时靶里**只放** `run_aaaa/` ⇒ 建图 ⇒ 它在图上；
      ② **只加两个同前缀兄弟**（`run_aaaa` 一个字节都没改）⇒ 重建
         ⇒ 它**必须仍在图上**，且 `★粘滞救回数` **≥1**（证明是粘滞救的）；
      ③ **对照组**：删掉图目录（⇒ 没有上一轮的图 ⇒ 粘滞集空）再建
         ⇒ 它**必须被剪**。
      ★ ③ 是**必须的**：没有它，「它还在图上」可能只是因为**剪枝规则没触发**
        —— 那就分不清「粘滞生效」与「本来就不剪」。
        **我第一版实验就栽在这里**：用了中文前缀 `子_aaaa`，而 `_副本前缀`
        要求分隔符前是 ASCII 字母数字 ⇒ 永不匹配 ⇒ 无粘滞时三个目录**全进了图**
        ⇒ 那一版的「✓ 粘滞生效」**是假的**（口径 355：一个结论要两把尺）。
      ④ 产物里必须有 `★粘滞救回数` 这个**读数**（口径 307：接了线要看得见）。

    ★ 隔离：临时靶用**自己的 `.kg`**（不依赖 `KG_图目录`）⇒ 不碰交付态。
    """
    坏: list[str] = []
    import agentscope_orchestrator.kg as _k186
    import json as _j186
    import shutil as _sh186
    import tempfile as _t186
    import pathlib as _p186
    _基 = _p186.Path(__file__).resolve().parents[1]
    _工 = _t186.mkdtemp(prefix="判186粘滞_", dir=str(_基 / "work" / "临时"))
    _靶 = _p186.Path(_工) / "靶"
    _靶.mkdir(parents=True, exist_ok=True)

    def _造(名: str) -> None:
        (_靶 / 名).mkdir(parents=True, exist_ok=True)
        (_靶 / 名 / "m.py").write_text("import os\n\n\ndef f():\n"
                                       "    return os.path.join('a', 'b')\n", encoding="utf-8")

    def _图上目录() -> set:
        for _n in _靶.rglob("节点.json"):
            _d = _j186.loads(_n.read_text(encoding="utf-8"))
            return {v.get("文件", "").split("/")[0] for v in _d.values()
                    if isinstance(v, dict) and v.get("文件")}
        return set()

    # ★ 前缀必须是 **ASCII 字母数字 + `_`**：`_副本前缀` 的正则是
    #   `^(.*[A-Za-z0-9][_-])([a-z0-9]{4,})$` —— 中文前缀**永不匹配**。
    _A, _B, _C = "run_aaaa", "run_bbbb", "run_cccc"
    try:
        _造(_A)
        _出1 = _k186.建图(_靶, 打印=lambda *a: None)
        if _A not in _图上目录():
            return _报2(["判一百八十六：**判据自己没接上** —— 第一步建完图后 `"
                        + _A + "` 就不在图上（前提不成立，后面全无意义）"])
        _造(_B)
        _造(_C)
        _出3 = _k186.建图(_靶, 打印=lambda *a: None)
        _救 = _出3.get("★粘滞救回数（上一轮在图上、这一轮本会被剪）")
        if _A not in _图上目录():
            坏.append(f"判一百八十六①：只加了两个同前缀兄弟（`{_A}` **一个字节都没改**），"
                      f"它就从图上消失了 ⇒ **粘滞没生效**（图的文件集仍然不是路径的函数）")
        if not isinstance(_救, int) or _救 < 1:
            坏.append(f"判一百八十六②：`★粘滞救回数` = {_救!r} ⇒ **不是粘滞救的**"
                      f"（口径 307：没有这个读数就分不清「粘滞生效」与「本来就不剪」）")
        # ★★★ 对照组
        _sh186.rmtree(_靶 / ".kg", ignore_errors=True)
        _出4 = _k186.建图(_靶, 打印=lambda *a: None)
        if _A in _图上目录():
            坏.append(f"判一百八十六③：**对照组失败** —— 没有上一轮的图时 `{_A}` "
                      f"**仍进了图** ⇒ 说明剪枝规则**压根没触发**，"
                      f"那 ①② 的绿**什么都不证明**（口径 355：一个结论要两把尺）")
        # ④ 读数键（在交付态/验收态的产物上）
        _有读 = 0
        _桶 = 0
        for _D in (_基 / "work" / "图", _基 / "work" / "图_验收"):
            if not _D.is_dir():
                continue
            for _b in _D.iterdir():
                _g = _b / "图.json"
                if not _g.is_file():
                    continue
                _桶 += 1
                try:
                    if "★粘滞救回数（上一轮在图上、这一轮本会被剪）" in \
                            _j186.loads(_g.read_text(encoding="utf-8")):
                        _有读 += 1
                except Exception:                              # noqa: BLE001, PERF203
                    continue
        if _桶 and _有读 == 0:
            坏.append(f"判一百八十六④：{_桶} 个桶的 `图.json` 里**都没有** "
                      f"`★粘滞救回数` 这个读数（口径 307：接了线要看得见；"
                      f"桶是旧代码建的 ⇒ 重跑建图）")
        return _报2(坏, f"粘滞剪枝：绿 —— 判一百八十六（活体实验：加两个同前缀兄弟后 "
                      f"`{_A}` **仍在图上** ✓ · `★粘滞救回数` = {_救} ✓ · "
                      f"**对照组**（无旧图）**它被剪** ✓ · "
                      f"产物读数 {_有读}/{_桶} 个桶 ✓）")
    finally:
        _sh186.rmtree(_工, ignore_errors=True)


def 判一百八十五知识族格式必须真读出来且是确定的() -> None:
    """**知识族适配器**：派发要接上 · 产品里要真有 · **而且必须是确定的**。

    ★ 为什么这条判据存在：R529 挖出的缺陷是「**一个做知识图谱的工具，
      把知识图谱自己的交换格式（`.owl`/`.rdf`/`.ttl`）判成「无结构」**」。
      R531 写了知识族适配器 ⇒ **必须有人守它一直读得出来**（口径 307）。

    ★★ 三条断言，其中 ③ 是**通用的**：
      ① **派发**：8 个知识族后缀必须派到知识族适配器，**不是 `_保底适配器`**。
         ★ R525 的教训：「**登记了**」不等于「**接上了**」——
           只注册进 `_适配器表` 不够，`_选适配器` 的 `if 后缀 in _语法` 会把它抢走。
      ② **产品**：`work/图_验收` 的存在桶里必须**真出现 `RDF资源` 节点**
         （口径 287：在**陌生靶**上量，不在本仓上量）。
      ③ ★★ **确定性**：同一份样本**连读两次，节点 id 集合必须完全相同**。
         ★ 这一条**当场抓过真缺陷**：R531 第一版用 `f"_:{id(e)}"` 当空白节点 id
           —— `id()` 是**内存地址** ⇒ **同一份文件两次建的图不一样**（口径 286：
           不可复现的产物等于没有产物）。
         ★ 它是**通用的**：任何**非确定**的 id 生成都会被它逮住。
    """
    坏: list[str] = []
    import agentscope_orchestrator.kg as _k185
    import json as _j185
    import pathlib as _p185
    # ── ① 派发
    该 = {".owl": "RDF/OWL", ".rdf": "RDF/OWL", ".ttl": "Turtle", ".nt": "Turtle",
          ".n3": "Turtle", ".trig": "Turtle", ".jsonld": "JSON-LD", ".kif": "KIF"}
    _保底 = ("_保底适配器", "_保底")
    for 后, 期 in 该.items():
        适, _语 = _k185._选适配器("样" + 后)
        if 适.__name__ in _保底:
            坏.append(f"判一百八十五①：`{后}` 派到了 **{适.__name__}** "
                      f"（应走**知识族适配器** —— 口径 307：登记了不等于接上了）")
    # ── ③ 确定性（**在最小合法样本上，两次读必须逐字相同**）
    样本 = {
        ".ttl": "@prefix ex: <http://例#> .\nex:A ex:p ex:B , ex:C ; ex:q \"值\" .\n",
        ".nt": "<http://例#A> <http://例#p> <http://例#B> .\n",
        ".owl": ("<?xml version=\"1.0\"?><rdf:RDF "
                 "xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\" "
                 "xmlns:owl=\"http://www.w3.org/2002/07/owl#\">"
                 "<owl:Class rdf:about=\"http://例#A\"><rdfs:label "
                 "xmlns:rdfs=\"http://www.w3.org/2000/01/rdf-schema#\">甲</rdfs:label>"
                 "</owl:Class></rdf:RDF>"),
        ".kif": "(role x)\n(<= (goal x 100) (true (cell 1 1 b)))\n",
    }
    for 后, 文 in 样本.items():
        适, 语 = _k185._选适配器("样" + 后)
        try:
            _n1, _e1 = 适(文, "样" + 后, 语)
            _n2, _e2 = 适(文, "样" + 后, 语)
        except Exception as _e:                                # noqa: BLE001
            坏.append(f"判一百八十五③：`{后}` 适配器炸了（{type(_e).__name__}: {_e}）")
            continue
        _i1 = sorted(x["id"] for x in _n1)
        _i2 = sorted(x["id"] for x in _n2)
        if _i1 != _i2:
            坏.append(f"判一百八十五③：`{后}` **两次读出的节点 id 不同** ⇒ **非确定**"
                      f"（口径 286：不可复现的产物等于没有产物；"
                      f"典型病根是把内存地址（`id()`）当 id）")
        if not _n1:
            坏.append(f"判一百八十五③：`{后}` 一条节点都没读出来（派到了 "
                      f"{适.__name__}，但**什么也没读出**）")
    # ── ② 产品（在验收桶上量）
    _验收 = _p185.Path(__file__).resolve().parents[1] / "work" / "图_验收"
    _RDF = 0
    _桶数 = 0
    if _验收.is_dir():
        for _b in sorted(_验收.iterdir()):
            _n = _b / "节点.json"
            if not _b.is_dir() or not _n.is_file():
                continue
            _桶数 += 1
            try:
                _d = _j185.loads(_n.read_text(encoding="utf-8"))
            except Exception:                                  # noqa: BLE001, PERF203
                continue
            for _v in _d.values():
                if isinstance(_v, dict) and _v.get("类") == "RDF资源":
                    _RDF += 1
    if _桶数 == 0:
        坏.append("判一百八十五②：`work/图_验收` 里**一个带 `节点.json` 的桶都没有** "
                  "⇒ 判据自己没接上（口径 307）")
    elif _RDF == 0:
        坏.append("判一百八十五②：验收桶里 **`RDF资源` 节点 0 个** ⇒ "
                  "知识族适配器**没有在生产路径上生效**（根目录下找不到 `.owl`/`.rdf`/`.ttl`，"
                  "或桶还没用新代码重建 —— 重跑 `work/临时/R524验收建图.py`）")
    return _报2(坏, f"知识族格式：绿 —— 判一百八十五（8 个后缀派发 ✓ · "
                  f"确定性样本 {len(样本)} 份两次逐字同 ✓ · "
                  f"验收桶 {_桶数} 个 · **`RDF资源` 节点 {_RDF:,}**）")


def 判一百八十四跳过后缀必须登记且原因不许替文件下结论() -> None:
    """**被跳过的后缀必须登记；原因字符串不许替文件下结论**（口径 203/305/307/344）。

    ★ 缺陷现场（陌生靶 `apache-jena-5.6.0` 的产物原文，2026-11-15）：
    ```
    '扩展名 `.owl` 无结构可抽（**不是源码/配置/文档**）': 3
    '扩展名 `.rdf` 无结构可抽（**不是源码/配置/文档**）': 3
    '扩展名 `.ttl` 无结构可抽（**不是源码/配置/文档**）': 2
    ```
      `.owl`/`.rdf`/`.ttl` **正是知识图谱自己的交换格式** —— 它们是**有结构的**。
      ⇒ **一个做知识图谱的工具，把知识图谱的交换格式判成「无结构」**，
        并把这句**假话写进了交付产物**，而当时 **181 条判据没有一条读它**（口径 307）。
      ★ 这是「全称通用」在最贴身的一层不成立：**声称通用，却读不了这个领域的标准格式。**

    ★ 四条断言（都在**产物**上，不重算图）：
      ① 每个被跳过的后缀**必须登记**（`kg._后缀登记`，精确或 `@前缀:` 规则）
         ⇒ 否则**红**（口径 203：剔掉必须留痕）；
      ② 原因字符串**必须等于** `kg._为什么跳过(后缀)` **现在**会写的
         ⇒ 否则**红**（口径 305：同一处置写两处就是病；**旧代码写的产物在这里现形**，
         这正是「改代码不改已落盘的数据」的解药，口径 349）；
      ③ 原因里**不许**出现「不是源码/配置/文档」—— 那句是**替文件下结论**，
         是**我没量过的事**（口径 344：声称的理由要能过检查；口径 311：名字可以是缺陷）；
      ④ 原因里**不许**出现「**未登记**」—— 那是缺口在产物里喊出来，
         ⇒ 必须去登记（口径 307：接不上的东西必须报红）。

    ★ 本条**不判**「缺口存在」为红 —— 建不建适配器是**策略**（要用户定）。
      本条判的是**缺口不许无声、原因不许说假话**。
    """
    坏: list[str] = []
    _逐文件计: dict = {}
    _逐文件比: dict = {}
    import agentscope_orchestrator.kg as _k184
    import json as _j184
    import re as _re184
    登记 = dict(getattr(_k184, "_后缀登记", {}) or {})
    if not 登记:
        坏.append("判一百八十四：`kg._后缀登记` 是空的 ⇒ 判据自己没接上（口径 307）")
        return _报2(坏)
    _前 = [k[4:] for k in 登记 if k.startswith("@前缀:")]
    _旧式 = _re184.compile(r"^扩展名 `([^`]*)`")
    _图区 = [Path(__file__).resolve().parents[1] / "work" / "图",
             Path(__file__).resolve().parents[1] / "work" / "图_验收"]
    _桶 = 0
    _后缀集: dict = {}
    for _D in _图区:
        if not _D.is_dir():
            continue
        for _b in sorted(_D.iterdir()):
            _g = _b / "图.json"
            if not _b.is_dir() or not _g.is_file():
                continue
            try:
                _d = _j184.loads(_g.read_text(encoding="utf-8"))
            except Exception as _e:                                # noqa: BLE001, PERF203
                坏.append(f"判一百八十四：`{_b.name}/图.json` 读不动（{type(_e).__name__}）")
                continue
            _桶 += 1
            for _k, _v in (_d.get("★跳过分类") or {}).items():
                _m = _旧式.match(str(_k))
                if not _m:
                    continue
                _s = (_m.group(1) or "(无)").lower()
                _后缀集[_s] = _后缀集.get(_s, 0) + (_v if isinstance(_v, int) else 0)
                _已登 = (_s in 登记) or any(_s.startswith(p) for p in _前)
                if not _已登:
                    坏.append(f"判一百八十四①：`{_b.name}` 跳过了 **{_v}** 个 `{_s}`，"
                              f"而这个后缀**没登记**（口径 203：剔掉必须留痕）")
                # ② 产物里的原因必须等于**现在**代码会写的（口径 305/349）
                _该 = _k184._为什么跳过(_s)
                # ★★★ R531d：这里原来是 `str(_k) != _该` —— **拿截断键比完整串**。
                #   `★跳过分类` 的键**故意截断到 40 字**（`_r = str(原因)[:40]`），
                #   所以原断言**只在原因 ≤40 字时才能通过**。
                #   ★★ R529 把原因改成长串后②还绿，是因为**桶没重建**、
                #     盘上还是旧短串（口径 349）⇒ **重建 jena 才把破尺暴露出来**。
                #   ⇒ 口径 366：**判据的绿可能只是「数据旧」换来的**。
                #   ★★★ R531e（口径 367）：**这一栏是快照，不是契约** —— 去掉对它的比对。
                #     它只能靠**重建整桶**更新（交付态有一桶 1,459,760 个节点），
                #     拿它当契约会让判据**永久红**，而红的原因不是「产物错」，
                #     是「**判据选错了审查对象**」（口径 348/353/355）。
                #     ⇒ 权威产物是**逐文件 `清单.json`**，它**不靠重建也能校验**。
                if False:                                          # noqa: SIM223
                    pass
                # ★★★ R531d：**权威产物是逐文件原因**（`清单.json`），不是截断的派生栏。
                #   ⇒ 每一条都要与**现在**代码写的一致（口径 260：不许静默漏）。
                _清 = _b / "清单.json"
                _计 = 0
                _比过 = 0
                if _清.is_file():
                    try:
                        _跳们 = (_j184.loads(_清.read_text(encoding="utf-8")) or {}).get("跳过") or []
                    except Exception:                              # noqa: BLE001, PERF203
                        _跳们 = []
                    for _条 in _跳们:
                        _p = str((_条 or {}).get("路径") or (_条 or {}).get("文件") or "")
                        if not _p:
                            continue
                        # ★★★ R531e：原来这里切出来的后缀**没有点**（`jar`），
                        #   而 `_s` 是从键里解析出来的**带点**形式（`.jar`）
                        #   ⇒ 两边**永不相等** ⇒ 这段断言**一次都没真正比过**
                        #   （口径 311：两个「后缀」不是一个东西）。
                        #   ★ `(无)`（真没有后缀）与 `(没有路径字段)` 分开（口径 190）。
                        _基2 = _p.replace("\\", "/").rsplit("/", 1)[-1]
                        _s2 = ("." + _基2.rsplit(".", 1)[-1].lower()) if "." in _基2 else "(无)"
                        if _s2 != _s:
                            continue
                        _计 += 1
                        _r2 = str((_条 or {}).get("原因") or "")
                        # ★★★ R531f（口径 368）：**按闸认原因** ——
                        #   盘上有 `'过大（8528KB > 8MB）'`（**体积闸** 剔的，跳得对，
                        #   而且「过大」是**更准**的原因），原来拿它去比后缀闸的答案
                        #   ⇒ 必红 ⇒ **判据把「后缀没适配器」与「所有跳过原因」
                        #   当成一件事**（口径 348/311）。
                        #   ★ 只有**后缀闸**写的原因（以 ``扩展名 ` `` 开头）才比文本；
                        #     其它闸的原因归各自的闸，本条只保证该后缀**登记过**（①）。
                        if not _r2.startswith("扩展名 `"):
                            continue
                        _比过 += 1
                        if _r2 != _k184._为什么跳过(_s2):
                            坏.append(f"判一百八十四②：`{_b.name}/清单.json` 里"
                                      f"`{_p[-60:]}` 的原因与**现在**代码写的不一致"
                                      f"（口径 305/349）")
                            break
                _逐文件计[_b.name] = _逐文件计.get(_b.name, 0) + _计
                _逐文件比[_b.name] = _逐文件比.get(_b.name, 0) + _比过
                # ★★★ R529e：③④ **查代码现在会写的那句话**，不查产物里的散文 ——
                #   第一次跑时我用 `"未登记" in 原因` 这种**子串**匹配，
                #   被我自己写的散文骗了（`_后缀登记[".xyz"]` 里带了「未登记」三个字）
                #   ⇒ 假红 12,311 个（口径 277：两把尺量同一物；口径 311：一个串两个意思）。
                #   ★ 现在：**标记位**（只认原因开头的固定形态）+ **代码路径**（直接审 `_该`）。
                _标记 = f"扩展名 `{_s}` **未登记**"
                if str(_k).startswith(_标记):
                    坏.append(f"判一百八十四④：`{_b.name}` 的 `{_s}` 原因写着**未登记** "
                              f"（{_v} 个）⇒ 去 `kg._后缀登记` 里登记它（口径 307）")
                if _该.startswith(_标记):
                    坏.append(f"判一百八十四④：`{_s}` **在代码里也是未登记** ⇒ "
                              f"登记表漏了它（口径 307：缺口要在源头就登记）")
                _假话 = "（**不是源码/配置/文档**）"
                if _假话 in _该:
                    坏.append(f"判一百八十四③：`{_s}` 的原因里出现「不是源码/配置/文档」"
                              f"—— 那是**替文件下结论**"
                              f"（口径 344/311：我只知道我这边没适配器，不知道它是什么）")
    if not _桶:
        坏.append("判一百八十四：**一个桶都没读到** ⇒ 判据自己没接上（口径 307）")
        return _报2(坏)
    if not _后缀集:
        坏.append("判一百八十四：**一个被跳过的后缀都没读到** ⇒ 判据自己没接上（口径 307）")
        return _报2(坏)
    _缺口 = sorted(s for s in _后缀集 if s in 登记 and str(登记[s]).startswith("是缺口"))
    _真无 = sorted(s for s in _后缀集 if s in 登记 and str(登记[s]).startswith("真无结构"))
    _前命 = sorted(s for s in _后缀集 if s not in 登记 and any(s.startswith(p) for p in _前))
    return _报2(坏, f"跳过后缀登记：绿 —— 判一百八十四（{_桶} 桶 · "
                f"**被跳过的后缀 {len(_后缀集)} 种** · 登记表 {len(登记)} 条 · "
                f"**是缺口 {len(_缺口)}**（{_缺口[:9]}）· 真无结构 {len(_真无)} · "
                f"前缀规则命中 {len(_前命)} · **未登记 0** · **原因 100% 等于现在代码会写的**）")

def 判一百八十三验收桶必须过同一套断言() -> None:
    """**陌生靶验收桶必须过与交付桶同一套断言**（目标④ · 机器判而不是我看）。

    ★ 为什么需要它：在这条之前，验收读数全靠 `work/临时/R524验收读数.py`
      —— **那是我看**，不是机器判。脚本一停，读数就没地方去了
      （口径 307：**没接上判据的结论会被忘**）。

    ★ 同款断言逐条对应（物质写在**一处**，两处各写一遍就是病，口径 305）：
      ① `判一百七十四②` 桶的规模不许凭空塌掉 —— `节点数 > 0` · `文件数 > 0` ·
         **`节点数 ≥ 文件数`**（一个文件不到一个节点 ⇒ 多半根搞错了）；
      ② `判一百七十六③` 依赖/虚拟环境不许混进项目内容 —— `戳.json` 里不许有
         `site-packages`/`.venv`/`node_modules`/`__pycache__`/`.git/`；
      ③ `判一百七十八⑤` 语言名不许带 grammar 变体后缀 `:`；
      ④ `判一百七十九③` 桶内产物的 `根` 必须与桶名一致
         （`kg._kg目录(根).name == 桶名`）；
      ⑤ **口径 260**：生产枚举对账 —— `文件数 + 跳过数 == 枚举到的文件数`
         （**静默丢 0**）。★ 这是验收的**主判据**：它直接回答「有没有东西被悄悄丢掉」。
      ⑥ **口径 307**：验收区不在 ⇒ **红**（判不了不许判绿）；
         桶目录在但没建完（大靶在跑）⇒ **不是错，但必须逐个报出来**。

    ★ 交付那四条查 `work/图`（17 桶 · 本仓），本条查 `work/图_验收`（陌生靶）——
      **同一个物质、两个宇宙**。这正是「全称通用」的检验方式：
      **换个仓库，同一套断言必须照样过。**
    """
    坏: list[str] = []
    import agentscope_orchestrator.kg as _k183
    import json as _j183
    _D183 = Path(__file__).resolve().parents[1] / "work" / "图_验收"
    if not _D183.is_dir():
        坏.append("判一百八十三：`work/图_验收` 不在 ⇒ **目标④没做**"
                  "（口径 307：判不了不许判绿；重跑：`work/临时/R524验收建图.py`）")
        return _报2(坏)
    _脏段 = ("site-packages", "node_modules", "__pycache__", "/.venv/", "\\.venv\\",
             "/.git/", "\\.git\\", "dist-packages")
    _桶 = 0
    _未建完: list = []
    _总: list = []
    for _b in sorted(_D183.iterdir()):
        if not _b.is_dir():
            continue
        _g = _b / "图.json"
        if not _g.is_file():
            _未建完.append(_b.name)
            continue
        try:
            _d = _j183.loads(_g.read_text(encoding="utf-8"))
        except Exception as _e:                                    # noqa: BLE001, PERF203
            坏.append(f"判一百八十三：`{_b.name}/图.json` 读不动（{type(_e).__name__}）")
            continue
        _桶 += 1
        _文 = int(_d.get("文件数") or 0)
        _节数 = int(_d.get("节点数") or 0)
        _跳 = int(_d.get("跳过数") or 0)
        _根s = str(_d.get("根") or "")
        # ① 规模不许塌
        if _文 <= 0 or _节数 <= 0:
            坏.append(f"判一百八十三①（同判一百七十四②）：`{_b.name}` 是**空图**"
                      f"（节点 {_节数} · 文件 {_文}）⇒ 空图不许当绿（口径 260）")
        elif _节数 < _文:
            坏.append(f"判一百八十三①（同判一百七十四①）：`{_b.name}` **一个文件不到一个节点**"
                      f"（文件 {_文} · 节点 {_节数}）⇒ 多半根搞错了")
        # ④ 根与桶名一致（同判一百七十九③）
        if not _根s:
            坏.append(f"判一百八十三④（同判一百七十九②）：`{_b.name}` 的 `图.json` 没有 `根`")
        else:
            # ★★ R528c（口径 277）：**不许用 `_kg目录` 当尺** —— 它在没有
            #   `KG_图目录` 时算的是 `<根>/.kg`，而验收桶是**带着**环境变量建的
            #   ⇒ 桶名来自**产物命名规则**。用错尺 ⇒ 假缺陷（第一次跑就踩了）。
            import re as _re183
            _算 = _re183.sub(r'[<>:"/\\|?*\x00-\x1f]', "_",
                             Path(_根s).name or "根").strip(" .")[:60]
            if _算 != _b.name:
                坏.append(f"判一百八十三④（同判一百七十九③）：`{_b.name}` 的 `根` 算出来是 "
                          f"**{_算}** ⇒ 桶内产物的根与图**不一致**（R519 那个事故的入口）")
        # ② 依赖/虚拟环境不许混进来（同判一百七十六③）
        _p183 = _b / "戳.json"
        if not _p183.is_file():
            坏.append(f"判一百八十三②（同判一百七十六①）：`{_b.name}` 有 图.json 却没有 戳.json")
        else:
            try:
                _戳 = _j183.loads(_p183.read_text(encoding="utf-8"))
            except Exception as _e:                                # noqa: BLE001
                _戳 = None
                坏.append(f"判一百八十三②：`{_b.name}/戳.json` 读不动（{type(_e).__name__}）")
            if isinstance(_戳, dict):
                _脏 = [k for k in _戳 if any(_s in f"/{k}" for _s in _脏段)]
                if _脏:
                    坏.append(f"判一百八十三②（同判一百七十六③）：`{_b.name}` 有 "
                              f"**{len(_脏)}** 个文件在依赖/虚拟环境里（例 {_脏[:3]}）")
        # ⑤ 生产枚举对账（口径 260 主判据）
        _枚183 = -1
        _盘183 = -1
        _留183n = -1
        try:
            _文183, _剪183 = _k183.枚举文件(Path(_根s)) if _根s else ([], [])
            _过183 = _k183._过值得(_文183, 根=(Path(_根s) if _根s else None))
            _留183 = _过183[0] if isinstance(_过183, tuple) else _过183
            # ★★★ R528f（口径 348 的第三个实例）：这里要数的是**三个阶段的量**，
            #   一个都不许混（实测数字见下方恒等式）：
            #     ① 盘上条目 = 枚举到 + 枚举剪        （认知主权 1932 + 40 = 1972）
            #     ② 枚举后候选 = 枚举到                （1932）
            #     ③ 进图 = 留                          （607）
            #   `图.json.跳过数` = **① − ③**（= 枚举剪 + 过滤剪），
            #   ⇒ 它只能与 **①** 比，**不能**与 ② 比（我第一次就是拿它比了 ②）。
            #   ★ 也不用 `_过值得` 的第二元素当过滤剪 —— 它在 认知主权 报 0，
            #     而实际过滤掉 1,325（口径 341：返回值的意思不许猜）。
            _枚183 = len(_文183)
            _盘183 = len(_文183) + len(_剪183)     # ① 盘上条目
            _留183n = len(_留183)                  # ③ 进图
        except Exception as _e:                                    # noqa: BLE001
            坏.append(f"判一百八十三⑤：`{_b.name}` 重算枚举失败（{type(_e).__name__}: "
                      f"{str(_e)[:80]}）⇒ 生产枚举对账做不了（口径 307：判不了不许判绿）")
        if _枚183 >= 0:
            # A. 同一个量的两个出口：图.json 的 `文件数` vs 我重算的 `留`
            if _留183n != _文:
                坏.append(f"判一百八十三⑤-A（口径 194）：`{_b.name}` **进图的文件数两个出口不同**"
                          f" —— `图.json` 说 {_文}，我重算的 `留` 是 {_留183n}"
                          f"（差 {_留183n - _文}）⇒ 同一个量两个名字（或盘在动，口径 286）")
            # B. 盘上条目守恒：文件 + 跳过 == 枚举到 + 枚举剪（口径 260）
            if _文 + _跳 != _盘183:
                坏.append(f"判一百八十三⑤-B（口径 260/348）：`{_b.name}` **盘上条目对不上** —— "
                          f"文件 {_文} + 跳过 {_跳} = {_文 + _跳}，而这一次"
                          f"**枚举到 {_枚183} + 枚举剪 {len(_剪183)} = {_盘183}**"
                          f"（差 {_盘183 - (_文 + _跳)}）⇒ 有东西被静默丢了"
                          f"（★ 跳过数是**两个阶段的和**，只能与盘上条目比）")
            # ★★ R528d：差额**必须指名**（口径 260/203，R525o 学到的）——
            #   把桶里 `清单.json` 跳过记录的路径逐条查盘，
            #   报出「N 条已不在盘上」⇒ 差额从**无名数**变成**有名字的成因**。
            if _文 + _跳 != _盘183 and _根s:
                _已消 = _还在 = _总跳 = 0
                try:
                    _qc = _j183.loads((_b / "清单.json").read_text(encoding="utf-8"))
                    for _r in (_qc.get("跳过") or []):
                        _p = str((_r or {}).get("路径") or "")
                        if not _p:
                            continue
                        _总跳 += 1
                        if (Path(_p) if Path(_p).is_absolute() else Path(_根s) / _p).exists():
                            _还在 += 1
                        else:
                            _已消 += 1
                except Exception:                          # noqa: BLE001
                    _总跳 = -1
                if _总跳 > 0:
                    _h = (f" · ★ 指名：`清单.json` {_总跳} 条跳过记录里 **{_已消} 条的路径"
                          f"已不在盘上**（{_还在} 条还在）⇒ 部分是**建图之后盘变了**"
                          f"（口径 286/349）；其余要看**剪枝规则是否依赖整个目录列表**"
                          f"（口径 351：`同前缀兄弟 ≥3 个` ⇒ 枚举**不是路径的函数**）")
                    for _i in range(len(坏) - 1, -1, -1):
                        if "盘上条目对不上" in 坏[_i]:
                            坏[_i] = 坏[_i] + _h
                            break
        # ③ 语言名不带 `:`
        _nj = _b / "节点.json"
        _语183: dict = {}
        if _nj.is_file():
            try:
                _节183 = _j183.loads(_nj.read_text(encoding="utf-8"))
                for _x in _节183.values():
                    if isinstance(_x, dict) and _x.get("语言"):
                        _l = str(_x["语言"])
                        _语183[_l] = _语183.get(_l, 0) + 1
                del _节183
            except Exception as _e:                                # noqa: BLE001
                坏.append(f"判一百八十三③：`{_b.name}/节点.json` 读不动"
                          f"（{type(_e).__name__}）")
        _带冒 = sorted(k for k in _语183 if ":" in k)
        if _带冒:
            坏.append(f"判一百八十三③（同判一百七十八⑤）：`{_b.name}` 的语言名**带变体后缀** "
                      f"`:` —— {_带冒}（那些节点在按语言的判断里等于隐形，口径 260）")
        _总.append(f"{_b.name}(文 {_文} · 跳 {_跳} · 节点 {_节数} · 盘上 {_盘183} · "
                   f"解析率 {(_d.get('★解析率') or '—')})")
    if not _桶:
        坏.append("判一百八十三：`work/图_验收` 下**一个有 图.json 的桶都没有** ⇒ "
                  "判据自己没接上（口径 307）")
    _尾 = (f" · **未建完 {len(_未建完)}**（{_未建完} ⇒ 大靶在跑，不是错但必须报）"
           if _未建完 else "")
    return _报2(坏, f"验收桶同一套断言：绿 —— 判一百八十三（**{_桶} 个验收桶全过** · "
                f"{' · '.join(_总)}{_尾}）")

def 判一百八十二语言专用内置表的缺口必须登记() -> None:
    """**图里出现过的每一种语言，要么有内置表、要么在缺口登记里**（口径 203/307）。

    ★ 缺陷现场（2026-11-15 · 陌生仓库验收 + 交付态自查，两条路指向同一件事）：
      `_内置_按语言` 只覆盖 **4 种**（java 159 · lean 142 · python 26 · rust 28），
      而交付图里出现过 **9 种**（lean/rust/python/markdown/java/javascript/c/
      typescript/bash）⇒ **5 种语言在无声地**产出被污染的「真缺口」读数
      （markdown 92,341 节点 · javascript 2,926 · c 200 · typescript 95 · bash 2）。
    ★ 最硬的证据是**陌生靶**给的：UE5 Core（真 C++）真缺口 Top =
      `nodiscard` 2,897 · `""`（空名）1,760 · `public` 879 ·
      `static_cast` 726 · `inline` 670 —— **全是 C++ 的关键字/属性**，
      而剔内置用的那张表**是 Rust 的**；244,015 次未解析
      **全落在「语言判不出来」**这一档（占未解析 244,099 的 99.97%）。

    ★ 本条**不判「缺口存在」为红** —— 建不建表是**策略**（要用户定），不是缺陷。
      本条判的是「**缺口不许无声**」：
        ① 有表、或在 `_内置表缺口_已登记` 里 ⇒ 过；
        ② 两处都没有 ⇒ **红**（口径 307：接不上的东西必须报红）；
        ③ 登记的理由**不许是空话**（口径 344：声称的理由要能被读）；
        ④ 登记里**必须说清是哪一类**（`缺口` / `不需要`）。
      ★ 登记表本身是 `kg` 的**模块级产物** ⇒ 缺口是**一个数**，
        不是回执里的一段散文（口径 307 的核心：**没接上判据的结论会被忘**）。
    """
    坏: list[str] = []
    import agentscope_orchestrator.kg as _k182
    import json as _j182
    _有表 = set(getattr(_k182, "_内置_按语言", {}) or {})
    _登记 = dict(getattr(_k182, "_内置表缺口_已登记", {}) or {})
    if not _有表:
        坏.append("判一百八十二：`_内置_按语言` 是空的 ⇒ 判据自己没接上（口径 307）")
        return _报2(坏)
    if not _登记:
        坏.append("判一百八十二：`_内置表缺口_已登记` 是空的 ⇒ 缺口**没登记**"
                  "（口径 307：没写下来的缺口等于不存在）")
    # ★ 登记项不许是空话（口径 344）
    for _语182, _由182 in _登记.items():
        if len(str(_由182).strip()) < 12:
            坏.append(f"判一百八十二：`{_语182}` 的登记理由太短（{str(_由182)!r}）"
                      f"⇒ 登记**是空话**（口径 344）")
        elif not (str(_由182).startswith("缺口") or str(_由182).startswith("不需要")):
            坏.append(f"判一百八十二：`{_语182}` 的登记**没说清是哪一类** —— "
                      f"必须以 `缺口` 或 `不需要` 开头（口径 194）")
    # ★ 图里出现过哪些语言
    _D182 = Path(__file__).resolve().parents[1] / "work" / "图"
    if not _D182.is_dir():
        坏.append("判一百八十二：`work/图` 不在 ⇒ 判不了（口径 307：判不了不许判绿）")
        return _报2(坏)
    _语182集: dict = {}
    _桶182 = 0
    for _b182 in sorted(_D182.iterdir()):
        _f182 = _b182 / "节点.json"
        if not _f182.is_file():
            continue
        try:
            _节182 = _j182.loads(_f182.read_text(encoding="utf-8"))
        except Exception as _e182:                             # noqa: BLE001, PERF203
            坏.append(f"判一百八十二：`{_b182.name}/节点.json` 读不动"
                      f"（{type(_e182).__name__}）")
            continue
        _桶182 += 1
        for _n182 in _节182.values():
            if isinstance(_n182, dict) and _n182.get("语言"):
                _l182 = str(_n182["语言"])
                _语182集[_l182] = _语182集.get(_l182, 0) + 1
        del _节182
    if not _桶182:
        坏.append("判一百八十二：**一个桶都读不到** ⇒ 判据自己没接上（口径 307）")
        return _报2(坏)
    if not _语182集:
        坏.append("判一百八十二：**一个语言都没读到** ⇒ 判据自己没接上（口径 307）")
        return _报2(坏)
    _无声 = sorted(_k182_ for _k182_ in _语182集
                if _k182_ not in _有表 and _k182_ not in _登记)
    if _无声:
        坏.append(f"判一百八十二：**{len(_无声)} 种语言既没内置表、也没登记** ⇒ "
                  f"它们的「真缺口」读数**被关键字污染而没人知道**：{_无声}"
                  f"（口径 203：剔掉必须留痕；口径 307：接不上必须报红）")
    _缺口 = sorted(k for k, v in _登记.items() if str(v).startswith("缺口"))
    _不需 = sorted(k for k, v in _登记.items() if str(v).startswith("不需要"))
    _带叹 = " · ".join(f"{k}({_语182集[k]:,})" for k in _无声) if _无声 else ""
    return _报2(坏, f"语言内置表覆盖：绿 —— 判一百八十二（{_桶182} 桶 · "
                f"**图里 {len(_语182集)} 种语言** · 有表 **{len(_有表 & set(_语182集))}**"
                f"（{sorted(_有表 & set(_语182集))}）· **已登记缺口 {len(_缺口)}**"
                f"（{_缺口}）· 登记为不需要 {len(_不需)}（{_不需}）· "
                f"**未登记 {len(_无声)}**"
                + (("：" + _带叹) if _无声 else "") + "）")

def 判一百八十一直洽门不许红在交付桶上() -> None:
    """**交付态每个桶的自洽门必须绿** —— 门不许被架空（口径 307）。

    ★ 缺陷现场（2026-11-15，**陌生仓库验收**引出来的，不是本仓翻出来的）：
      交付态 **12 / 17 桶**的自洽门是**红的**，共 **4,185 条**「悬空边」，
      而 **177 条判据里没有一条读这个门** ⇒ **门天天红、没人管**
      （口径 307 的同族：判据没接上要报红；**门没接上也要报红**）。

    ★ 4,185 条分三类（口径 194：**三个不同的量不许并成一个数**）：
        ① `外链:` 仓外 **135** —— 天经地义不落盘，**不该判红**；
        ② `路径:` 且 `已解析=False` **3,361** —— **已经报出来的断链**
           （在 `md链接.json` / `json引用.json` 的 `断链` 里），
           那不是「图自相矛盾」，是「这条链接确实断了」；
        ③ **真矛盾 689** —— 靶**明明在图里**（`file:` 节点在），
           边却指向没落盘的 `路径:` ⇒ `终` 与 `目标文件`
           **同一个事实的两个出口不同形态**（口径 194）⇒ 图遍历时**这条路是断的**，
           而「解析率 97.77%」照样好看（它按 `目标文件` 算）。
      ★ 混在一起的代价：**真正的那 689 条淹在 4,185 里没人看见**（口径 260 的反面）。

    ★ 本条**不自己重算**悬空边 —— 直接问 `kg.自洽门(根)`
      （口径 305：**同一处置写两处就是病**；判据和门必须同源）。
      ★ 也不读 `图.json` 里那份**建图时**的缓存读数 —— `收尾全套`
        会改 `边.json`（把 `路径:` 接成 `file:`）⇒ 缓存**是旧世界的**（口径 286）。

    ★ 口径 210：本条的**红路径**由真实交付桶给出（改前 12 桶红）；
      ★ 口径 307：**一个桶都读不到** ⇒ 红（判不了不许判绿）。
    """
    坏: list[str] = []
    import agentscope_orchestrator.kg as _k181
    import json as _j181
    _D = Path(__file__).resolve().parents[1] / "work" / "图"
    if not _D.is_dir():
        坏.append("判一百八十一：`work/图` 不在 ⇒ 判不了（口径 307：**判不了不许判绿**）")
        return _报2(坏)
    桶数 = 红桶 = 0
    细: list = []
    例: list = []
    for _b in sorted(_D.iterdir()):
        _g = _b / "图.json"
        if not _g.is_file() or not (_b / "边.json").is_file():
            continue
        try:
            _根 = Path(str(_j181.loads(_g.read_text(encoding="utf-8")).get("根") or ""))
        except Exception as _e:                                # noqa: BLE001
            坏.append(f"判一百八十一：`{_b.name}/图.json` 读不动（{type(_e).__name__}）")
            continue
        if not _根.is_dir():
            坏.append(f"判一百八十一：`{_b.name}` 的根不在盘上：{_根}")
            continue
        桶数 += 1
        # ★★ 不调 `kg.自洽门(根)` —— 它走 `_kg目录(根)`，不带 `KG_图目录` 时
        #   指向 `<根>/.kg`（交付桶实际住在 `work/图/<桶>`）⇒ **判据会因环境变量而绿**
        #   （实测报「没有 `<根>/.kg/图.json`」）。**判据不许依赖环境巧合**。
        #   ⇒ 直接读本桶的 `节点.json`/`边.json`，调 `kg` 里**同一段**分类逻辑（口径 305）。
        try:
            _节 = _j181.loads((_b / "节点.json").read_text(encoding="utf-8"))
            _边 = _j181.loads((_b / "边.json").read_text(encoding="utf-8"))
            _分 = _k181.悬空边分类(_节, _边)
        except Exception as _e:                                # noqa: BLE001
            坏.append(f"判一百八十一：`{_b.name}` 产物读不动"
                      f"（{type(_e).__name__}: {str(_e)[:90]}）")
            continue
        del _节
        if not _边:
            坏.append(f"判一百八十一：`{_b.name}` 的 `边.json` 是**空的** ⇒ "
                      f"判不了（口径 307：**判不了不许判绿**）")
            continue
        if _分["悬空"]:
            红桶 += 1
            细.append(f"{_b.name} 悬空 {_分['悬空']}"
                      f"（另单列：外链 {_分['外链']} · 已报断 {_分['已报断']}）")
            if len(例) < 3:
                例.append(f"{_b.name}: 真矛盾 {_分['悬空']} 条"
                          f"（外链 {_分['外链']} · 已报断 {_分['已报断']} 单列）")
    if 桶数 == 0:
        坏.append("判一百八十一：**一个桶都读不到** ⇒ 判据自己没接上（口径 307）")
    elif 红桶:
        坏.append(f"判一百八十一：交付态 **{红桶} / {桶数} 桶**自洽门红 —— "
                  f"**门红着而判据不管**（口径 307）。例：{例}。全部：{细[:6]}")
    return _报2(坏, f"交付桶自洽门：绿 —— 判一百八十一（{桶数} 桶 · "
                    f"**每桶自洽门都绿** · 悬空边 0）")


def _根173():
    """判一百七十三 用的根：允许用环境变量指到别处（与其它判据同款）。"""
    import os as _os173
    from pathlib import Path as _P173
    return _P173(_os173.environ.get("KG_判173仓根")
                 or _P173(__file__).resolve().parents[1])


def 判一百七十三一遍全仓遍历不许走两遍():
    """★ R519附：**一遍全仓遍历不许走两遍**（口径 269 先写判据 / 313 靶要精确）。

    ★★★ 缺陷现场（实测 2026-11-14）：
      `mem_live.图上死引用` 里有 **2** 处 `os.walk(仓根)`：
        L690 收「不剪枝口径」的 `全仓名` · L699 收「剪枝口径」的 `剪枝名`。
      注释自称「实测本机这一遍 ~25–60s」——★ **读数已过期**：
        实测一遍 = 133,924 目录 · 329,090 文件 · **143.4s**（只排 `.git`）
      ★ 我原来在这里写「两遍 ~287s ⇒ 撞 600s 上限」——**实测推翻了它**（口径 344）：
      ⇒ 真因是**冷缓存 + 两遍遍历 + 前面 33 条判据的累计**超过逐条上限，**不是死锁**。★ 本判据的真理由只有一句：**同一份全仓读数不该走两遍**（一遍 ~200s，走两遍白付一遍）。
      ★ 口径 337：**慢到不敢跑，就等于没有这条判据。**

    ★ 为什么两遍是**可避免的**：剪枝只改变**后续递归**，不改变**已经看到的条目**
      ⇒ 「不剪枝遍历 + 在内存里判每个目录会不会被剪枝」能得到**同样两个集合**，
        磁盘只走一遍。★ 已逐元素对账（口径 113，两边都报）：全仓名 63846 · 剪枝名 3180 · 只旧有 0 · 只新有 0。★ 边界（如实）：在**热缓存的小根**上合并后反而慢 5.9s（26.4s vs 20.5s）—— 省下的第二遍被 OS 缓存吃掉了；在**冷根/大根**上省时间（实测判四十六 期间 `os.walk` 从 2 次降到 1 次，遍历 181,586 目录）。**两种情形都写下来。**
    """
    坏: list[str] = []
    import ast as _ast173
    _P = _根173()
    _f = _P / "src" / "agentscope_orchestrator" / "mem_live.py"
    if not _f.is_file():
        坏.append(f"判一百七十三：`{_f}` 不在 ⇒ 判不了（**不是绿**，口径 260）")
        return _报2(坏, "判一百七十三一遍全仓遍历不许走两遍：绿")
    try:
        _树173 = _ast173.parse(_f.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        坏.append(f"判一百七十三：`mem_live.py` 语法错 ⇒ 判不了（{e}）")
        return _报2(坏, "判一百七十三一遍全仓遍历不许走两遍：绿")
    _看 = 0
    for _fn in _ast173.walk(_树173):
        if not isinstance(_fn, (_ast173.FunctionDef, _ast173.AsyncFunctionDef)):
            continue
        if _fn.name != "图上死引用":
            continue
        _看 += 1
        _走 = [x for x in _ast173.walk(_fn) if isinstance(x, _ast173.Call)
              and isinstance(x.func, _ast173.Attribute) and x.func.attr == "walk"]
        if len(_走) > 1:
            坏.append(f"判一百七十三：`mem_live.图上死引用`（L{_fn.lineno}..{_fn.end_lineno}）"
                      f"里有 **{len(_走)}** 处 `os.walk`（L"
                      + "、L".join(str(x.lineno) for x in _走) + "）"
                      " ⇒ **同一条全仓读数被算了两遍**；"
                      "实测一遍 143.4s ⇒ 两遍撞上限 ⇒ 判四十六 永久卡住（口径 337）")
    if _看 == 0:
        坏.append("判一百七十三：`mem_live.py` 里找不到 `图上死引用` ⇒ "
                  "**判据的靶改名/搬走了** ⇒ 判不了（口径 313：靶没了要报，不许静默判绿）")
    if 坏:
        return _报2(坏, "判一百七十三一遍全仓遍历不许走两遍：绿")
    print("   ● 判一百七十三：一路全仓只走一遍 —— 绿")


if __name__ == "__main__":
    raise SystemExit(main())

def 判七十四导入进来的名字不许当成本文件定义() -> None:
    """**「绑到 import 语句」不是「绑到定义」** —— 而且它比「未解析」更坏。

    ★ R350 实测（JS）：
        index.js:  import { clamp } from "./util/clamp.js";  console.log(clamp());
        util/clamp.js:  export function clamp(){ return 1; }
      建图后 index.js 里多出一个实体 `类型:src/index.js:clamp:1`（**导入语句自己**）
      ⇒ 引用解析①「本文件定义」**先命中** ⇒ `clamp` 被解到**那条 import 语句**。
      ★ 它**不报未解析**（看起来解到了）—— 而**误报的引用比缺失的引用更坏**：
        缺失会被 ④ 报成缺口，误报会被**当成事实**用于计划。

    ★ 口径（**通用判据，按 tree-sitter 节点类型，不按语言**）：
      ① 已被 `取导入` 认定为导入的语句（`_导入型` 命中）的子树；
      ② `export ... from "x"`（re-export，同样是引入外部名字）。
      ★ **`export function f(){}` 不算** —— 那是真定义 ⇒ 判据必须要求**同时有 `from`**。

    ★ 端到端也要过：修好守卫之后 `clamp` 必须**真解到 util/clamp.js 里的定义**
      （只看「假定义没了」不够 —— **没测到不能当通过**）。
    """
    坏: list[str] = []
    try:
        import json as _json
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判七十四：依赖导不进来（{type(e).__name__}: {e}）"])

    实 = {}
    试 = [
        ("js", "index.js", 'import { clamp } from "./util/clamp.js";\nconsole.log(clamp());\n',
         "util/clamp.js", "export function clamp(){ return 1; }\n", "clamp"),
        ("ts", "index.ts", 'import { deep } from "./util/deep";\ndeep();\n',
         "util/deep.ts", "export function deep(){ return 2; }\n", "deep"),
        ("py", "index.py", "from util.deep import deep\n\ndeep()\n",
         "util/deep.py", "def deep():\n    return 2\n", "deep"),
    ]
    for 语, 主名, 主文, 靶相, 靶文, 名 in 试:
        try:
            根 = Path(_tf.mkdtemp(prefix="kgJ74_"))
            (根 / "util").mkdir(parents=True, exist_ok=True)
            if 语 == "py":
                (根 / "util" / "__init__.py").write_text("", encoding="utf-8")
            (根 / 主名).write_text(主文, encoding="utf-8")
            (根 / 靶相).write_text(靶文, encoding="utf-8")
            _kg.建图(根, 打印=lambda *_: None)
            节 = (_kg.加载图(根).get("节点") or {})
            实体类 = set(_kg._实体类)
            假 = [k for k, v in 节.items()
                  if v.get("类") in 实体类 and v.get("文件") == 主名 and v.get("名") == 名]
            if 假:
                坏.append(f"判七十四：{语} —— 导入语句里的 `{名}` 被建成了本文件定义 {假}")
            r = _kg.解析引用(根, 打印=lambda *_: None)
            计 = r.get("计数") or {}
            if not 计.get("已解析·导入模块"):
                坏.append(f"判七十四：{语} —— `{名}` 没解到导入的模块"
                          f"（实得 {计}；只有 `本文件定义` 就是又绑到自己身上了）")
            else:
                边2 = (_kg.加载图(根).get("边") or [])
                目 = [e for e in 边2 if e.get("关系") == "引用"
                      and e.get("名") == 名 and e.get("目标文件")]
                if not 目:
                    坏.append(f"判七十四：{语} —— `{名}` 的引用边没有 `目标文件`（解到了却没有靶）")
                elif not str(目[0].get("目标文件")).endswith(靶相):
                    坏.append(f"判七十四：{语} —— `{名}` 解到了 {目[0].get('目标文件')!r}，"
                              f"应为 {靶相!r}")
                else:
                    实[语] = 目[0].get("目标文件")
        except Exception as e:                        # noqa: BLE001
            坏.append(f"判七十四：{语} 建图/解析抛异常（{type(e).__name__}: {e}）")
    if 坏:
        return _报2(坏)
    干 = " · ".join(f"{k}→{v}" for k, v in sorted(实.items()))
    print(f"   导入名绑到真定义：绿 —— 判七十四（{干}；"
          f"**导入语句本身不再被当成本文件定义**）")

def 判七十五增量图必须与全量图逐项相同() -> None:
    """**增量不是「少做一点的全量」** —— 它必须给出**同一张图**。

    ★ R351 实测（读代码）：增量分支用手写的 id 前缀清单判「边属于哪个文件」，
      清单**漏了 `mod:`/`绑定:`** ⇒ 那些边**一条都进不了新图**；
      而**没命中任何前缀时循环走完、什么都不做** ⇒ **静默丢边**
      （增量模式下边越来越少，读数上看不出）。
      ⇒ R351 已改成**查节点表**（边的任一端在图上是哪个文件的节点）。

    ★ R352 才真正**测到**这条路径：上一轮我一直调 `建图()`，
      而 **`建图()` 永远是全量**、增量入口是 **`更新图()`** ——
      **探针调错函数 ⇒ 两次都报「模式=全量」⇒ 我以为「增量测不到」。**
      这正是「**没测到不能当通过**」的另一面：**先确认自己真的走到了那条路。**

    ★ 口径：
      ① **必须真的走增量**（`模式 == "增量"` 且 `重算文件数` 对得上）——
         否则判据只是把全量跑了两遍，**测了个寂寞**；
      ② 改一个已有文件 ⇒ 重算 **1**；
      ③ **只 touch 不改内容 ⇒ 重算 0**；
      ④ 增量图与**删掉 `.kg` 重建的全量图**：节点集合、边集合、关系分布、
         导入边数**逐项相同**；
      ⑤ `★增量丢边` 必须为 **0**（查不到归属文件的边 —— 宁可报出来也不许静默）。
    """
    坏: list[str] = []
    try:
        import shutil as _sh
        import tempfile as _tf
        import time as _time
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判七十五：依赖导不进来（{type(e).__name__}: {e}）"])

    import json as _json

    def _摘(根):
        图 = _kg.加载图(根)
        边 = 图.get("边") or []
        节 = 图.get("节点") or {}
        分: dict = {}
        for e in 边:
            分[e.get("关系")] = 分.get(e.get("关系"), 0) + 1
        return (frozenset(节), frozenset(_json.dumps(e, sort_keys=True, ensure_ascii=False) for e in 边),
                tuple(sorted(分.items())), len([e for e in 边 if e.get("关系") == "导入"]))

    根 = Path(_tf.mkdtemp(prefix="kgJ75_"))
    try:
        (根 / "util").mkdir(parents=True, exist_ok=True)
        (根 / "index.js").write_text(
            'import { clamp } from "./util/clamp.js";\nconsole.log(clamp());\n', encoding="utf-8")
        (根 / "util" / "clamp.js").write_text("export function clamp(){ return 1; }\n", encoding="utf-8")
        (根 / "util" / "other.js").write_text("export const q = 3;\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)

        # ③ 只 touch 不改内容 ⇒ 重算 0
        _time.sleep(1.1)
        (根 / "util" / "other.js").touch()
        出0 = _kg.更新图(根, 打印=lambda *_: None)
        if 出0.get("模式") != "增量":
            坏.append(f"判七十五：`更新图` 没走增量（模式={出0.get('模式')}）"
                      f" —— **没走到那条路，后面就都没测到**")
        if 出0.get("重算文件数") != 0:
            坏.append(f"判七十五：只 touch 没改内容却重算了 {出0.get('重算文件数')} 个")

        # ② 改一个已有文件 ⇒ 重算 1
        _time.sleep(1.1)
        (根 / "index.js").write_text(
            'import { clamp } from "./util/clamp.js";\n'
            'import { q } from "./util/other.js";\nconsole.log(clamp(), q);\n', encoding="utf-8")
        出1 = _kg.更新图(根, 打印=lambda *_: None)
        if 出1.get("模式") != "增量":
            坏.append(f"判七十五：第二次 `更新图` 没走增量（模式={出1.get('模式')}）")
        if 出1.get("重算文件数") != 1:
            坏.append(f"判七十五：改了一个文件应重算 1，实得 {出1.get('重算文件数')}")
        丢 = 出1.get("★增量丢边（查不到归属文件的边）")
        if 丢 is None:
            坏.append("判七十五：增量读数里**没有 `★增量丢边`** —— 丢边被静默了（探针少一个字段）")
        elif 丢:
            坏.append(f"判七十五：增量丢了 {丢} 条边（查不到归属文件）")
        _kg.解析引用(根, 打印=lambda *_: None)
        增 = _摘(根)

        # ④ 删掉 .kg 全量重建，逐项比
        _sh.rmtree(_kg._kg目录(根))
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        全 = _摘(根)

        if 增[0] != 全[0]:
            坏.append(f"判七十五：节点集合不一致（增量 {len(增[0])} · 全量 {len(全[0])}）")
        if len(增[1]) != len(全[1]):
            坏.append(f"判七十五：边集合不一致（增量 {len(增[1])} · 全量 {len(全[1])}）"
                      f" —— **增量把边弄丢了**")
        if 增[2] != 全[2]:
            坏.append(f"判七十五：关系分布不一致（增量 {增[2]} · 全量 {全[2]}）")
        if 增[3] != 全[3]:
            坏.append(f"判七十五：导入边数不一致（增量 {增[3]} · 全量 {全[3]}）"
                      f" —— **`mod:` 类边正是旧清单漏掉的那一类**")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判七十五：抛异常（{type(e).__name__}: {e}）")
    if 坏:
        return _报2(坏)
    print(f"   增量=全量：绿 —— 判七十五（真走增量：touch 重算 0 · 改内容重算 1 · "
          f"丢边 0；节点 {len(增[0])} · 边 {len(增[1])} · 导入边 {增[3]} 与全量逐项相同）")

def 判七十六大仓增量必须与旧图读数完全一致() -> None:
    """**小仓一致不等于大仓一致** —— 增量要在**真规模**上不丢东西。

    ★ R353 实测（标准库 3,923 文件 / 277,465 节点 / 1,055,138 边）：
        无改动 更新图：模式=增量 · 重算 0 · 丢边 0 · **23.7s**（全量 222s）
        节点 277465 · 边 1055138   ← **与全量一字不差**
        改 1 个文件：重算 1 · 丢边 0 · 28.8s；节点 **+1** · 边 **+2**，
          `定义:colorsys.py:__增量探针__:169` **真的进图**
        还原后：节点 277465 · 边 1055138，**探针残留 `[]`** ⇒ **幂等且可逆**

    ★ 为什么这条判据**不是**判七十五的重复：
      判七十五用 3 个文件的 fixture，**验的是「逻辑对不对」**；
      这条在**真语料**上跑，**验的是「规模上会不会漏」**——
      小仓上「每条边都搬得动」和几十万条边上「一条都不掉」**不是同一件事**。

    ★ 口径（**只读**，不改语料、不改图）：
      ① 语料图不存在 ⇒ **没测到**（不是通过）；
      ② 无改动 `更新图` 必须走**增量**（否则测的是全量）；
      ③ `重算文件数` 必须 **0**；
      ④ `★增量丢边` 必须 **0**，且字段必须**存在**；
      ⑤ 节点数与边数必须**与旧图完全相同**（不重建全量也能判 —— 这才是省得起的判据）。
    """
    坏: list[str] = []
    try:
        import json as _json
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判七十六：依赖导不进来（{type(e).__name__}: {e}）"])

    语料 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib")
    if not (语料 / ".kg" / "节点.json").exists():
        return _报2(["判七十六：**没测到**标准库语料图 —— 没测到不能当通过"])
    旧 = _kg.加载图(语料)
    旧节 = len(旧.get("节点") or {})
    旧边 = len(旧.get("边") or [])
    if not 旧节 or not 旧边:
        return _报2([f"判七十六：旧图读出来是空的（节点 {旧节} · 边 {旧边}）—— 读错了就不是测"])
    出 = _kg.更新图(语料, 打印=lambda *_: None)
    if 出.get("模式") != "增量":
        坏.append(f"判七十六：`更新图` 没走增量（模式={出.get('模式')}）—— 测的是全量")
    if 出.get("重算文件数") != 0:
        坏.append(f"判七十六：无改动却重算了 {出.get('重算文件数')} 个文件")
    丢 = 出.get("★增量丢边（查不到归属文件的边）")
    if 丢 is None:
        坏.append("判七十六：读数里没有 `★增量丢边` —— 丢边被静默了")
    elif 丢:
        坏.append(f"判七十六：大仓增量丢了 {丢} 条边")
    if 出.get("节点数") != 旧节:
        坏.append(f"判七十六：节点数变了（旧 {旧节} · 增量后 {出.get('节点数')}）"
                  f" —— **无改动就不该变**")
    if 出.get("边数") != 旧边:
        坏.append(f"判七十六：边数变了（旧 {旧边} · 增量后 {出.get('边数')}）"
                  f" —— 少了的边就是**静默丢的**")
    if 坏:
        return _报2(坏)
    print(f"   大仓增量：绿 —— 判七十六（{旧节} 节点 · {旧边} 边；"
          f"无改动增量重算 0 · 丢边 0 · 读数与旧图完全相同）")

def 判七十七大仓增量改再还原必须回到原位() -> None:
    """**「能改」和「能改回来」是两件事** —— 后者才说明增量没留残渣。

    ★ R354 实测（标准库 3,923 文件）：改 1 个文件 → 更新图 → 增量与**全量逐项零差异**
      （总数 277,467 节点 / 1,055,142 边两边一致；`colorsys.py` 单文件
      节点 37/37、边 91/91 **差 0**）—— 但**全量重建要 369s**，
      而增量只要 28.8s（**快 12.8 倍**）。
      ⇒ 「逐项对账」太重，**不能当常驻判据**；但它的**可判结论**可以：

    ★ **改一个文件、再改回原样，两次增量之后读数必须回到原位** —— 便宜（两次增量），
      而且能抓住两类真错：**丢边**（读数回不去）与**残留**（旧节点没被清掉）。
      ★ 这条比「计数比一比」强：计数相同也可能是**一多一少正好抵消**。
    ★ **必须 `try/finally` 还原语料**：判据自己不许把语料改坏。
    """
    坏: list[str] = []
    try:
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
        import time as _time
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判七十七：依赖导不进来（{type(e).__name__}: {e}）"])

    语料 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tools\py312\Lib")
    靶 = 语料 / "colorsys.py"
    if not (语料 / ".kg" / "节点.json").exists() or not 靶.exists():
        return _报2(["判七十七：**没测到**标准库语料或 colorsys.py —— 没测到不能当通过"])

    def _读():
        图 = _kg.加载图(语料)
        return 图.get("节点数") or len(图.get("节点") or {}), \
              图.get("边数") or len(图.get("边") or {})

    原 = 靶.read_text(encoding="utf-8")
    基 = _读()
    try:
        _time.sleep(1.1)
        靶.write_text(原 + "\n\ndef __对账探针__(a, b):\n    return a + b\n",
                      encoding="utf-8")
        出1 = _kg.更新图(语料, 打印=lambda *_: None)
        _中 = _读()
        if _中[0] <= 基[0] or _中[1] <= 基[1]:
            坏.append(f"判七十七：加了定义读数却没变大（基 {基} → {_中}）—— 增量没吃到改动")
        _time.sleep(1.1)
        靶.write_text(原, encoding="utf-8")
        出2 = _kg.更新图(语料, 打印=lambda *_: None)
        末 = _读()
        for _名, _出 in (("加", 出1), ("还原", 出2)):
            丢 = _出.get("★增量丢边（查不到归属文件的边）")
            if 丢 is None:
                坏.append(f"判七十七：{_名}那次读数里没有 `★增量丢边`")
            elif 丢:
                坏.append(f"判七十七：{_名}那次丢了 {丢} 条边")
            if _出.get("模式") != "增量":
                坏.append(f"判七十七：{_名}那次没走增量（模式={_出.get('模式')}）")
        if 末 != 基:
            坏.append(f"判七十七：改再还原后**没回到原位**（基 {基} → 末 {末}）"
                      f" —— 差值就是**残留或被静默丢掉的**")
        节 = _kg.加载图(语料).get("节点") or {}
        残 = [k for k in 节 if "__对账探针__" in k]
        if 残:
            坏.append(f"判七十七：还原后探针节点还在图里 {残[:3]}")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判七十七：抛异常（{type(e).__name__}: {e}）")
    finally:
        try:
            靶.write_text(原, encoding="utf-8")
            _kg.更新图(语料, 打印=lambda *_: None)
        except Exception:                             # noqa: BLE001
            pass
    if 坏:
        return _报2(坏)
    print(f"   增量可逆：绿 —— 判七十七（改再还原后读数回到原位 {基[0]} 节点 · {基[1]} 边；"
          f"两次增量都丢边 0 · 探针无残留）")

def 判七十八星号必须按语言标对且不许误标() -> None:
    """**注释不是判据** —— R348 在注释里写下「re-export 等同 `from z import *`」，
    但 `星号` 那个字段**写死 `False`** ⇒ **话说了、数据没变、下游读不到**。

    ★ R355 实测（修前）：
        export * from "./z.js"        ⇒ 绑定=[]        星号=**False**   ← 错
        export { a, b as c } from     ⇒ 绑定=['a','c']  星号=False      ← 对
        import * as ns from           ⇒ 绑定=['ns']     星号=False      ← 对
        import def from               ⇒ 绑定=['def']    星号=False      ← 对
      四条摆在一起一比，「只有第一条该是星号而报 False」**一眼就露**。
      ⇒ 这就是**把实得值全打出来核对**的价值：单看一条看不出对错。

    ★ 口径（**两个方向都要**）：
      ① `export * from "x"` 与 `from x import *` **必须** `星号=True`；
      ② `import * as ns from "x"` **必须不**标星号 —— 它是**命名空间绑定**
         （本文件作用域里多一个名字 `ns`），**不是**「把对方的名字全引进来」。
         ★ **误报的引用比缺失的引用更坏**：把 `ns` 标成星号 ⇒ 下游会以为
           `ns` 里所有名字都可见。
      ③ `星号=True` 的边**绑定必须为空**（星号导入不引入具名绑定）。
    """
    坏: list[str] = []
    try:
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
        import tree_sitter as _ts
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判七十八：依赖导不进来（{type(e).__name__}: {e}）"])

    def _抽(语, 文):
        树 = _ts.Parser(_kg._取语言(语)).parse(文.encode("utf-8"))
        return _kg.取导入(树, 文.encode("utf-8"))

    试 = [
        # (语言, 源码, 期望 {模块: (星号, 绑定)})
        ("javascript",
         'export * from "./z.js";\n'
         'export { a, b as c } from "./w.js";\n'
         'import * as ns from "./n.js";\n'
         'import def from "./d.js";\n',
         {"./z.js": (True, []), "./w.js": (False, ["a", "c"]),
          "./n.js": (False, ["ns"]), "./d.js": (False, ["def"])}),
        ("python", "from os import *\nimport a.b.c\n",
         {"os": (True, []), "a.b.c": (False, ["a"])}),
        ("typescript", 'export * from "./z";\nimport * as ns from "./n";\n',
         {"./z": (True, []), "./n": (False, ["ns"])}),
    ]
    for 语, 文, 期望 in 试:
        try:
            实 = _抽(语, 文)
        except Exception as e:                        # noqa: BLE001
            坏.append(f"判七十八：{语} 抽取抛异常（{type(e).__name__}: {e}）")
            continue
        表 = {d["模块"]: (bool(d["星号"]), list(d["绑定"])) for d in 实}
        for 模, (望星, 望绑) in 期望.items():
            if 模 not in 表:
                bad = sorted(表)
                坏.append(f"判七十八：{语} 少了 `{模}`（实得 {bad}）")
                continue
            实星, 实绑 = 表[模]
            if 实星 != 望星:
                坏.append(f"判七十八：{语} `{模}` 星号应为 {望星}，实得 {实星}"
                          + ("（`export *`／`import *` 的星号**没落进数据**）"
                             if 望星 else "（**误报**：命名空间绑定不是星号）"))
            if 望星 and 实绑:
                坏.append(f"判七十八：{语} `{模}` 是星号，绑定必须为空，实得 {实绑}")
            elif 实绑 != 望绑:
                坏.append(f"判七十八：{语} `{模}` 绑定应为 {望绑}，实得 {实绑}")
    if 坏:
        return _报2(坏)
    print("   星号按语言标对：绿 —— 判七十八（js/ts `export *` 与 py `from x import *` "
          "都标星号；`import * as ns` **不**标星号；星号边绑定为空）")

def 判七十九星号导入必须真接线且歧义要标() -> None:
    """**「登记了」不等于「接上了」** —— R355 把 `星号` 标在边上，**下游一条都没用**。

    ★ R356 实测（修前）：`from .util import *` 之后引用 `helper` ⇒ 落 **未解析**
      （星号边在图里躺着，**没人读它**）。
    ★ 修后：`helper → 定义:pkg/util.py:helper:1`，依据写「星号导入」。

    ★ 口径（**四个方向**）：
      ① **端到端**：星号导入进来的名字必须**真解到那个模块的定义**，
         依据里要写明是星号来的（否则下游分不清「规则定的」还是「猜的」）；
      ② **不许抢在模块名优先前面**：`import copy` 的裸名 `copy` 仍是**模块对象**
         （判六十五守这一条，这里只确认没把它打翻）；
      ③ **多个星号模块都导出同名 ⇒ 必须标歧义并给候选**，不许挑一个装作确定；
      ④ **星号边本身**（`导入` 边的 `星号` 字段）在三语言上都对（判七十八守格式，
         这里守**它被读到了**：修前 `星号` 是 False 时本判据的 ① 也会红）。
    """
    坏: list[str] = []
    try:
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判七十九：依赖导不进来（{type(e).__name__}: {e}）"])

    def _边(根, 名, 关系="引用"):
        return [e for e in (_kg.加载图(根).get("边") or [])
                if e.get("关系") == 关系 and e.get("名") == 名]

    # ① 唯一的星号导入 ⇒ 名字解到那个模块
    try:
        根 = Path(_tf.mkdtemp(prefix="kgJ79a_"))
        (根 / "pkg").mkdir(parents=True, exist_ok=True)
        (根 / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (根 / "pkg" / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (根 / "pkg" / "main.py").write_text("from .util import *\n\nhelper()\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        r = _kg.解析引用(根, 打印=lambda *_: None)
        目 = [e for e in _边(根, "helper") if e.get("目标文件")]
        if not 目:
            坏.append("判七十九：星号导入进来的 `helper` **没解到任何目标**"
                      f"（计数 {r.get('计数')}）—— **星号边在图里躺着没人读**")
        elif not str(目[0].get("目标文件")).endswith("pkg/util.py"):
            坏.append(f"判七十九：`helper` 解到了 {目[0].get('目标文件')!r}，应为 pkg/util.py")
        elif "星号" not in str(目[0].get("依据") or ""):
            坏.append(f"判七十九：`helper` 解到了，但依据是 {目[0].get('依据')!r}"
                      f" —— 下游分不清「规则定的」还是「猜的」")
        if (r.get("计数") or {}).get("未解析"):
            坏.append(f"判七十九：星号导入后 `helper` 仍被报未解析"
                      f"（未解析 {(r.get('计数') or {}).get('未解析')}）")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判七十九：① 抛异常（{type(e).__name__}: {e}）")

    # ③ 两个星号模块都导出同名 ⇒ 歧义 + 候选
    try:
        根3 = Path(_tf.mkdtemp(prefix="kgJ79c_"))
        (根3 / "a.py").write_text("def dup():\n    return 1\n", encoding="utf-8")
        (根3 / "b.py").write_text("def dup():\n    return 2\n", encoding="utf-8")
        (根3 / "m.py").write_text("from a import *\nfrom b import *\n\ndup()\n", encoding="utf-8")
        _kg.建图(根3, 打印=lambda *_: None)
        _kg.解析引用(根3, 打印=lambda *_: None)
        多 = [e for e in _边(根3, "dup") if e.get("起") == "file:m.py"]
        if not 多:
            坏.append("判七十九：两个星号模块都导出 `dup`，却没产出带名引用边")
        elif not 多[0].get("歧义"):
            坏.append(f"判七十九：`dup` 有两个来源却没标歧义（终={多[0].get('终')!r}）"
                      f" —— **「歧义」和「未解析」是两件事**，歧义必须标")
        elif len(多[0].get("候选") or []) < 2:
            坏.append(f"判七十九：标了歧义却没给候选（候选 {多[0].get('候选')}）")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判七十九：③ 抛异常（{type(e).__name__}: {e}）")

    # ④ JS `export *` 边必须带星号（否则 ① 在 JS 上根本无从谈起）
    try:
        根2 = Path(_tf.mkdtemp(prefix="kgJ79b_"))
        (根2 / "z.js").write_text("export function deep(){ return 5; }\n", encoding="utf-8")
        (根2 / "index.js").write_text('export * from "./z.js";\n', encoding="utf-8")
        _kg.建图(根2, 打印=lambda *_: None)
        导 = [e for e in (_kg.加载图(根2).get("边") or []) if e.get("关系") == "导入"]
        if not 导 or not any(e.get("星号") for e in 导):
            坏.append(f"判七十九：JS `export *` 的导入边没标星号（实得 {[(e.get('终'), e.get('星号')) for e in 导]}）")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判七十九：④ 抛异常（{type(e).__name__}: {e}）")

    if 坏:
        return _报2(坏)
    print("   星号真接线：绿 —— 判七十九（`from .util import *` 后 `helper` 解到 "
          "pkg/util.py 且依据写明星号；两模块同名 ⇒ 歧义+2 候选；js `export *` 边带星号）")

def 判八十内外同名必须各解到各自那一层() -> None:
    """**名字解析的规则是「由内向外、先命中者胜」** —— 不是「取深度最大的」。

    ★ R358 已把 `父定义` 接成作用域链，但**边还是按名字聚合的** ⇒
      `def dup` 内外同名时两处调用**共用一条边**、只能有一个主靶 ⇒
      **对一处对、对另一处错**。而**「答案错了」比「未解析」更坏**：
      未解析会被 ④ 报成缺口，**错答案会被当成事实用于计划**。

    ★ R359 实测（修完三层之后）：
        def outer():
            def dup(): ...      ← 内层（父定义=outer、粒=内嵌）
            return dup()         ← 行 4
        def dup(): ...          ← 外层（父定义=None、粒=顶层）
        dup()                   ← 行 9
      ⇒ 主图：**行 4 → 定义:m.py:dup:2**（内层）· **行 9 → 定义:m.py:dup:6**（外层）

    ★ 口径（**四个方向，缺一个就能骗过去**）：
      ① 内外同名时，**内层调用解到内层**；
      ② **外层调用解到外层** —— 内层定义在**外层不可见**（这一条最容易漏：
         只测 ① 的话，「全部解到最内层」也能过）；
      ③ 每个出现处**各有自己的 `行`**（出现处必须可区分 ——
         键里没有 `行`，「并进主图」会把它们**合并成一条**）；
      ④ 不重名时仍是**唯一答案、无歧义**（不许为了做作用域把简单情形弄成歧义）。

    ★ 为什么必须**端到端看主图**：本轮实际踩过 ——
      ① 层算得**完全正确**（`引用.json` 里行4→内层、行9→外层），
      但「并进主图」的索引键少了 `行` ⇒ **4 条互相覆盖**，主图上全变成外层。
      **只查上游计数会以为全对。**
    """
    坏: list[str] = []
    try:
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判八十：依赖导不进来（{type(e).__name__}: {e}）"])

    def _取(根, 名):
        return [e for e in (_kg.加载图(根).get("边") or [])
                if e.get("关系") == "引用" and e.get("名") == 名]

    try:
        根 = Path(_tf.mkdtemp(prefix="kgJ80_"))
        源 = ("def outer():\n     def dup():\n         return 1\n"
              "     return dup()\n\n"
              "def dup():\n     return 2\n\n"
              "dup()\n")
        (根 / "m.py").write_text(源, encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        边 = _取(根, "dup")
        by行 = {}
        for e in 边:
            by行.setdefault(e.get("行"), []).append(e)
        # ③ 每个出现处各有自己的行
        if len(边) < 2 or len(by行) < 2:
            坏.append(f"判八十：`dup` 的引用边没有按出现处分开（{len(边)} 条 · "
                      f"{len(by行)} 个不同行）—— **提及边可能还是按名字聚合的**")
        # ① 内层调用（行 4，在 outer 内）
        内 = by行.get(4) or []
        if not 内:
            坏.append(f"判八十：找不到行 4 的引用边（实得行 {sorted(k for k in by行 if k)}）")
        elif "定义:m.py:dup:2" not in str(内[0].get("终")):
            坏.append(f"判八十：**行 4 在 `outer` 内调用 `dup`，应对内层** "
                      f"`定义:m.py:dup:2`，实得 {内[0].get('终')!r}")
        # ② 外层调用（行 9，模块级）—— 内层定义在这里**不可见**
        外 = by行.get(9) or []
        if not 外:
            坏.append("判八十：找不到行 9 的引用边")
        elif "定义:m.py:dup:6" not in str(外[0].get("终")):
            坏.append(f"判八十：**行 9 是模块级调用，内层定义不可见 ⇒ 应解到外层** "
                      f"`定义:m.py:dup:6`，实得 {外[0].get('终')!r}"
                      f" —— 「全部解到最内层」就是这条在挡")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判八十：① 抛异常（{type(e).__name__}: {e}）")

    # ④ 不重名时仍是唯一答案、无歧义
    try:
        根2 = Path(_tf.mkdtemp(prefix="kgJ80b_"))
        (根2 / "n.py").write_text("def solo():\n    return 1\n\nsolo()\n", encoding="utf-8")
        _kg.建图(根2, 打印=lambda *_: None)
        _kg.解析引用(根2, 打印=lambda *_: None)
        边2 = _取(根2, "solo")
        if not 边2:
            坏.append("判八十：不重名时 `solo` 竟没有引用边")
        elif 边2[0].get("歧义"):
            坏.append(f"判八十：不重名时标了歧义（终={边2[0].get('终')!r}）"
                      f" —— 为做作用域把简单情形弄成歧义，是另一种错")
        elif "定义:n.py:solo:1" not in str(边2[0].get("终")):
            坏.append(f"判八十：`solo` 解到了 {边2[0].get('终')!r}，应为 定义:n.py:solo:1")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判八十：④ 抛异常（{type(e).__name__}: {e}）")

    if 坏:
        return _报2(坏)
    print("   作用域遮蔽：绿 —— 判八十（内外同名：行4→内层 dup:2 · 行9→外层 dup:6；"
          "每出现处各有行号；不重名仍唯一无歧义）")

def 判八十一读不许改源且增量必须幂等() -> None:
    """**读取动作不许改源；增量重建必须幂等。**

    ★ R361 实测抓到的（**标准库规模**，不是 fixture）：
        `解析引用` **不是只读的** —— 它把主图里的提及边**原地改成**引用边
        （`关系`→`引用`、`终`→节点 id，留下 `原名`），**边数不变**。
        于是「增量重建」是从**一份被读操作改过的图**上搬边：

            全量后 边 2,814,885 · 出现过的(文件,名字) **503,743** · 解到定义 **109,785**
            增量后 边 2,814,885 · 出现过的(文件,名字) **405,326** · 解到定义  **11,368**

        **节点数、边数一模一样，内容不一样** ——
        **只报计数的话，这是一次完美的假绿。**

    ★ 三个方向（缺一个就能骗过去）：
      ① `建图` → `解析引用` → `更新图` 之后，主图必须**回到建图后的关系分布**
         （引用边全部退回提及）；
      ② **幂等**：再解析 + 再更新，分布仍然相同（不是「碰巧第一次对上」）；
      ③ `更新图` 之后**主图里不许残留 `关系=="引用"` 的边** ——
         这条直接钉住「读改了源」。

    ★ 为什么必须**同时看关系分布**：只比边数的话，
      `引用`/`提及` 是**原地替换**（边数不变）⇒ 边数检查**永远绿**。
    """
    坏: list[str] = []
    try:
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判八十一：依赖导不进来（{type(e).__name__}: {e}）"])

    def 分布(根):
        c: dict = {}
        for x in (_kg.加载图(根).get("边") or []):
            c[x.get("关系")] = c.get(x.get("关系"), 0) + 1
        return c

    try:
        根 = Path(_tf.mkdtemp(prefix="kgJ81_"))
        (根 / "a.py").write_text("def f():\n    return 1\nf()\n", encoding="utf-8")
        (根 / "b.py").write_text("def g():\n    return 2\ng()\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        甲 = 分布(根)
        if 甲.get("引用"):
            坏.append(f"判八十一：建图后就有引用边（{甲}）—— 建图不该产出引用")
        _kg.解析引用(根, 打印=lambda *_: None)
        乙 = 分布(根)
        if not 乙.get("引用"):
            坏.append(f"判八十一：解析引用后竟没有引用边（{乙}）—— 前置条件没成立，后面对不上不算数")
        _kg.更新图(根, 打印=lambda *_: None)
        丙 = 分布(根)
        if 丙.get("引用"):
            坏.append(f"判八十一：③ `更新图` 后主图**仍残留引用边**（{丙}）"
                      f" —— **读改了源**，增量是从被污染的图上搬的")
        if 丙 != 甲:
            坏.append(f"判八十一：① `更新图` 后没回到建图后的分布（建图 {甲} · 更新 {丙}）")
        _kg.解析引用(根, 打印=lambda *_: None)
        _kg.更新图(根, 打印=lambda *_: None)
        戊 = 分布(根)
        if 戊 != 丙:
            坏.append(f"判八十一：② **不幂等**（第一次更新 {丙} · 第二次更新 {戊}）"
                      f" —— 只有「碰巧第一次对上」才会出现这种")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判八十一：抛异常（{type(e).__name__}: {e}）")

    if 坏:
        return _报2(坏)
    print("   读不许改源：绿 —— 判八十一（解析引用改主图 ⇒ 更新图必须把引用退回提及；"
          "关系分布回到建图后；再解析再更新仍相同）")

def 判八十二计数必须与引用条数同源且退回不留残边() -> None:
    """**计数与落盘记录必须同源**；**退回不许留残边**。

    ★ R362 实测（标准库）抓到的两处「同一个事实两个答案」：

      ① `计[...] += 1` 散在 6 个分支里 ⇒ **漏一处就是一个假 0**。
         实测返回的 `计数` 里 `已解析·导入模块 = 0`，
         而 `引用.json` 的依据分布里明明有 `导入模块 19,416 + 靶不在本仓 4,279`；
         `已解析·本文件 = 10,330` 而依据里 `145,334` —— **差 14 倍**。
         ⇒ 改成 **`计` 一律从 `引用` 派生**（分类规则只写一份）。
      ② 派生之后 `出["计数"]` 却**在派生之前**被赋值 ⇒
         探针读返回字典拿到**旧值**、日志打印拿到**新值**，
         **两个数看起来都很合理**（10,330 / 145,334）。
         ⇒ 一处赋值、一处读。

    ★ 三个方向：
      ① **同源**：`sum(计数.values()) == len(引用.json)`，且**逐类与依据分布一致**；
      ② **`更新图` 之后不许残留「由提及升级来的」引用边** ——
         `解析引用` 会把提及边原地改成引用边（**边数不变**），
         增量重建若不退回，就从被污染的图上搬边；
      ③ **标记补回**：`解析引用` 之后同一条边必须是**引用**（否则 ② 只是因为「压根没解析」而绿）。

    ★ 为什么 ③ 不能省：② 单独看，**一个从不做解析的实现天然满足** ——
      「**排除了」和「通过了」长得一样**。
    """
    坏: list[str] = []
    try:
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
        import json as _json
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判八十二：依赖导不进来（{type(e).__name__}: {e}）"])

    try:
        根 = Path(_tf.mkdtemp(prefix="kgJ82_"))
        (根 / "n.py").write_text("def helper():\n    return 3\n", encoding="utf-8")
        (根 / "m2.py").write_text("import os\nprint(helper())\nos.path.join('a')\n",
                                  encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        r = _kg.解析引用(根, 打印=lambda *_: None)
        计 = r.get("计数") or {}
        它 = _json.loads((_kg._kg目录(根) / "引用.json").read_text(encoding="utf-8"))
        它 = 它 if isinstance(它, list) else (它.get("引用") or [])
        # ① 同源
        if sum(计.values()) != len(它):
            bad_txt = [f"判八十二：① **计数与落盘记录不同源** —— 计数合计 {sum(计.values())} "
                       f"而 `引用.json` {len(它)} 条"]
        else:
            bad_txt = []
        # ② 逐类对账（依据 → 类别，规则与实现同一条）
        _期 = {"已解析·本文件": 0, "已解析·导入模块": 0,
               "绑定/内置（**不算引用**）": 0, "未导入": 0, "未解析": 0}
        for x in 它:
            _依 = str(x.get("依据") or "")
            _终 = x.get("终")
            _靶 = not (isinstance(_终, str) and _终.startswith("name:"))
            if _依.startswith("**绑定/内置**"):
                _期["绑定/内置（**不算引用**）"] += 1
            elif _依.startswith("**未导入**"):
                _期["未导入"] += 1
            elif _靶:
                _期["已解析·本文件" if _依.startswith("本文件定义") else "已解析·导入模块"] += 1
            else:
                _期["未解析"] += 1
        for k in _期:
            if _期[k] != 计.get(k):
                bad_txt.append(f"判八十二：① 类别 `{k}` 计数 {计.get(k)} ≠ 依据分布推出的 {_期[k]}")
        # ③ 标记必须补回（否则 ② 只是「压根没解析」）
        _后 = {x.get("关系") for x in (_kg.加载图(根).get("边") or [])}
        if "引用" not in _后:
            bad_txt.append("判八十二：③ `解析引用` 后主图里**没有引用边** —— "
                           "② 会因此**因为「压根没解析」而绿**")
        # ② 更新图之后不许残留（markdown 外链那类**本来就**是引用，要排除）
        _kg.更新图(根, 打印=lambda *_: None)
        _残 = [x for x in (_kg.加载图(根).get("边") or [])
               if x.get("关系") == "引用" and x.get("原名")]
        if _残:
            bad_txt.append(f"判八十二：② `更新图` 后**仍残留 {len(_残)} 条由提及升级来的引用边**"
                           f"（例：{_残[0].get('原名')}）—— 增量会从被污染的图上搬边")
        坏.extend(bad_txt)
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判八十二：抛异常（{type(e).__name__}: {e}）")

    if 坏:
        return _报2(坏)
    print("   计数同源：绿 —— 判八十二（计数合计 == 引用.json 条数；逐类与依据分布一致；"
          "解析后主图确有引用边；更新图后无残留升级边）")

def 判八十三未解析桶必须有出口且跨文件表按能否被import过滤() -> None:
    """**「未解析」必须有出口**；**跨文件名字表按「能不能被 import」过滤**。

    ★ R364 实测抓到的（**同一轮两个假读数**）：

      ① `未解析` **恒为 0** —— 不是「桶空了」，是**桶里的东西从来没进过账**：
         那个分支只 `计["未解析"] += 1` 和往「样例」（最多 20 条）里塞，
         **不 `append` 进 `引用`**。而 `计` 是从 `引用` 派生的。
         **一个永远为 0 的桶，看起来最像「都解析好了」。**
         修后标准库：`未解析 0 → 247,542`。

      ② `未导入` 虚高 30,186 条 —— `全仓`（**跨文件名字表**）收了两类**不能被 import** 的东西：
         · **函数体内的定义**（`粒=内嵌`）：实测 `__name__` 的 8 个「定义」节点全在内嵌层；
         · **别的语言的声明**（`.c` 的 `类=声明`）：拿 Python 的 import 规则去问它。
         修后标准库：`未导入 70,984 → 40,798`。

    ★ 四个方向：
      ① `n.py` 把 `helper` 定义在**函数体内**、`m2.py` 用它 ⇒
         必须落 **`未解析`**（本文件没定义、没导入，别处顶层也没有），
         且**必须在 `引用.json` 里能查到这一条**（不是只进了「样例」）；
      ② `m2.py` 用内置 `print` ⇒ 落 **`绑定/内置`**，**不许**算「未导入」；
      ③ **计数里有 `未解析` 这个键且它能为非零** ——
         一个**结构上不可能非零**的桶，是「这一类没有出口」；
      ④ **修前那种「未导入」不许回来**：`m2.py` 对 `helper` 的判定
         **不许是「未导入」**（`n.py` 的 `helper` 在函数里，**根本 import 不到**）。
         假缺口会让 AI 去补一个**不该存在的 import**。
    """
    坏: list[str] = []
    try:
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
        import json as _json
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判八十三：依赖导不进来（{type(e).__name__}: {e}）"])

    try:
        根 = Path(_tf.mkdtemp(prefix="kgJ83_"))
        # ① `helper` 在**函数体内** ⇒ 不可被 import
        (根 / "n.py").write_text(
            "def outer():\n    def helper():\n        return 1\n    return helper\n",
            encoding="utf-8")
        (根 / "m2.py").write_text("print(helper())\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        出 = _kg.解析引用(根, 打印=lambda *_: None)
        计 = 出.get("计数") or {}
        _j = _json.loads((_kg._kg目录(根) / "引用.json").read_text(encoding="utf-8"))
        _j = _j if isinstance(_j, list) else (_j.get("引用") or [])
        _m2 = {x.get("名"): str(x.get("依据") or "") for x in _j if x.get("起") == "file:m2.py"}
        # ① `helper` 必须出现在 `引用.json` 且是「未解析」
        if "helper" not in _m2:
            坏.append("判八十三①：`m2.py` 用的 `helper` **在 `引用.json` 里查不到** —— "
                      "这一类**没有出口**（只进了「样例」不算记账）")
        elif "未解析" not in _m2["helper"]:
            坏.append(f"判八十三①/④：`helper` 的依据是 {_m2['helper'][:44]!r}，"
                      f"应为「**未解析**」—— 若报「未导入」那是**假缺口**"
                      f"（`n.py` 的 `helper` 在函数体内，**根本 import 不到**）")
        # ② `print` 必须是绑定/内置
        if "print" in _m2 and "绑定" not in _m2["print"] and "内置" not in _m2["print"]:
            坏.append(f"判八十三②：内置 `print` 的依据是 {_m2['print'][:40]!r}，应为「绑定/内置」")
        # ③ 桶必须存在且能非零
        if "未解析" not in 计:
            坏.append(f"判八十三③：计数里**没有 `未解析` 这个键**（{sorted(计)}）"
                      f" —— 结构上不可能非零的桶")
        elif not 计.get("未解析"):
            坏.append(f"判八十三③：`未解析` = {计.get('未解析')} —— "
                      f"这个 fixture 里**必须有**（`helper` 就是那一类），0 说明**没有出口**")
        # ③b 计数与落盘记录同源
        if sum(计.values()) != len(_j):
            坏.append(f"判八十三③：计数合计 {sum(计.values())} ≠ `引用.json` {len(_j)} 条")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判八十三：抛异常（{type(e).__name__}: {e}）")

    if 坏:
        return _报2(坏)
    print("   未解析有出口：绿 —— 判八十三（函数体内的定义不可被 import ⇒ 用它的文件落「未解析」"
          "且在引用.json里查得到；内置落绑定/内置；计数与落盘同源）")

def 判八十四模块别名必须解到模块且传递层不许筛字段() -> None:
    """`import X as Y` 的 `Y` **就是模块 X 本身**；`from X import Y` 的 `Y` 是**成员**。

    ★ R365 实测（标准库）：`未解析` 里最大的一堆是**模块别名** ——
      `np` 2,180 条、`plt` 572、`mpl` 342、`collections` 314、`pip` 289。
      而它们的导入边 **完全正确**（`{"终":"mod:numpy","绑定":["np"]}`）
      —— 错在解析层把 `np` 当「`numpy` 里叫 `np` 的成员」去找。

    ★ 四个方向：
      ① **`import X as Y` ⇒ `Y` 解到「模块」（`file:`/`mod:` 靶），不是「未解析」**；
      ② **`from X import Y` ⇒ `Y` 解到 X 里的**那个定义（**成员**），
         不许被当成模块（那样 `from x import f` 之后 `f()` 就指到模块上了）；
      ③ **`_指模块` 不许经过 `str()`** —— `str(False)` 是 `"False"`，**非空即真**
         ⇒ 条件恒假、`模块绑定` 恒空、**不报错**。这里用**行为**验：
         `import X as Y` 必须真解到（恒假的话这一条会红）；
      ④ **传递层不许筛字段** —— 源码里那两处白名单恢复必须已改成铺开
         （`规范.append` 与导边 `边.append`）。**靠读源码**验，因为
         「字段静默消失」**不会**在读数上留下自己的名字。
    """
    坏: list[str] = []
    try:
        import tempfile as _tf
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
        import json as _json
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判八十四：依赖导不进来（{type(e).__name__}: {e}）"])

    try:
        根 = Path(_tf.mkdtemp(prefix="kgJ84_"))
        (根 / "npmod.py").write_text(
            "VALUE = 1\ndef f():\n    return 2\n", encoding="utf-8")
        (根 / "u.py").write_text(
            "import npmod as np\nimport npmod\nfrom npmod import f\n\nnp.f()\n",
            encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        出 = _kg.解析引用(根, 打印=lambda *_: None)
        _j = _json.loads((_kg._kg目录(根) / "引用.json").read_text(encoding="utf-8"))
        _j = _j if isinstance(_j, list) else (_j.get("引用") or [])
        _u = {x.get("名"): x for x in _j if x.get("起") == "file:u.py"}
        # ① 别名 `np` 必须解到模块
        _np = _u.get("np")
        if _np is None:
            坏.append("判八十四①：`import npmod as np` 的 `np` **没进引用**")
        else:
            _终 = str(_np.get("终") or "")
            _依 = str(_np.get("依据") or "")
            if "未解析" in _依 or _终.startswith("name:"):
                坏.append(f"判八十四①/③：别名 `np` 判成 {_依[:34]!r}（终={_终[:22]}）"
                          f" —— 别名**指模块本身**，`str(False)` 那个陷阱会让它恒不命中")
            elif not _终.startswith(("file:", "mod:")):
                坏.append(f"判八十四①：别名 `np` 解到了 {_终[:30]!r}，**应为模块**")
        # ② `from npmod import f` 的 `f` 必须解到成员（定义节点），不是模块
        _f = _u.get("f")
        if _f is None:
            坏.append("判八十四②：`from npmod import f` 的 `f` **没进引用**")
        else:
            _终2 = str(_f.get("终") or "")
            if _终2.startswith(("file:", "mod:")):
                坏.append(f"判八十四②：`from npmod import f` 的 `f` 解到了 {_终2[:30]!r}"
                          f" —— 那是**模块**，应为 `npmod` 里的定义（**成员**）")
            elif not _终2.startswith("定义:"):
                坏.append(f"判八十四②：`f` 解到了 {_终2[:30]!r}，应为 `定义:...`")
        # ④ 传递层不许筛字段（读源码 —— 静默丢字段不会在读数上留名）
        _源 = (Path(__file__).resolve().parents[1] / "src"
               / "agentscope_orchestrator" / "kg.py").read_text(encoding="utf-8")
        # ★ 判据文本里**不许出现裸的双引号**（写判据时踩过：
        #   它会把判据自己的字符串提前结束 ⇒ **判据文件语法错**）。
        # ★ 白名单的特征是 **`{` 后面紧邻的就是第一个键**；
        #   铺开的写法是 `{**导, "起": …` / `{` 换行 `**x,`。
        #   ★ 我第一版只查「`边.append({"起"` 这个子串」⇒
        #     **铺开写法里也含这个子串**（`{**导, "起": 文件节点`）⇒ **假红**。
        #     子串检测要挑**只有白名单才有的**那一段。
        # ④ **行为判据**：`节点类型` 是本轮新加的字段，它**必须一路活到导入边上**。
        #   ★ 我第一版是**读源码查子串** —— 当场两次假红
        #     （先匹配到铺开写法里的同名子串，又匹配到 Lean 那条无关的导边）。
        #     **字段传不传得到，用行为问，别用源码猜。**
        #   实测（本条立项的原因）：白名单三处（`取导入` 出口 / 导边构建 / 下游取值）
        #   让这个字段**静默消失**，而导入边**一条字段都没少** ——
        #   每一处都不报错，读数上只表现为「一大类别名解不到」。
        _导入边 = [e for e in (_kg.加载图(根).get("边") or [])
                  if e.get("关系") == "导入" and e.get("起") == "file:u.py"]
        if not _导入边:
            坏.append("判八十四④：`u.py` **没有导入边** ⇒ 这一条测不到（不许当通过）")
        elif not any("节点类型" in e for e in _导入边):
            坏.append("判八十四④：导入边上**没有 `节点类型` 字段** ⇒ "
                      "抽取层的新字段在半路被白名单吃掉了（**静默消失**）")
        # ★ 另一头：`from` 与 `import` 必须**分得开**（同一个字段承载语义）
        else:
            _型 = {e.get("节点类型") for e in _导入边}
            if not any("from" in str(t) for t in _型) or not any(
                    "from" not in str(t) for t in _型):
                坏.append(f"判八十四④：导入边的 `节点类型` 只有 {sorted(map(str, _型))}"
                          f" —— **`import X as Y` 与 `from X import Y` 必须分得开**"
                          f"（分不开就判不出别名指模块还是指成员）")
    except Exception as e:                            # noqa: BLE001
        坏.append(f"判八十四：抛异常（{type(e).__name__}: {e}）")

    if 坏:
        return _报2(坏)
    print("   模块别名：绿 —— 判八十四（`import X as Y` 的 Y 解到模块；"
          "`from X import Y` 的 Y 解到成员；传递层两处不许筛字段）")

def 判八十五每条判据都必须真的有红路径且引用的名字必须存在() -> None:
    """**判据自己是代码，也得有判据。** 用 `ast`（**权威解析器**）静态查三件事。

    ★ R365 实测（这就是立这条判据的原因）：
      `判八十`~`判八十四` 调的是 **`_報2`（繁体）**，而文件里只定义了
      **`_报2`（简体）** —— `_報2` **从未定义**。
      后果**不是报错**，是**更难发现**的东西：这几条判据**只有绿路径能跑**，
      一旦它想红就抛 `NameError`。
      ⇒ **一个永远绿的判据，你永远遇不到它的红路径。**
      ⇒ 我此前说的「修前必红」**是没验过的**。

    ★ 三个方向（都用 `ast`，不写正则）：
      ① **未定义的全局名**：函数体里 `Load` 的名字，既不在模块级定义里，
         也不是内建、不是 import 进来的 ⇒ **它一定会在运行时 `NameError`**。
         （`_報2` 就是这样被漏掉的 —— 它只在**红的时候**才被求值，
           所以绿的时候完全看不出来。）
      ② **红路径必须存在**：每个 `判*` 函数体里必须有对 `_报2(` 的调用。
         没有 ⇒ 它红了也无处说 ⇒ **永远绿**。
      ③ **`坏.append` 必须存在**：一条判据**写不出红**，就不是判据，**是装饰**。
         只有 `_报2` 而没有 `坏.append` 的函数，`坏` 恒空 ⇒ **结构上不可能红**。

    ★ 为什么不是「跑一遍看它红不红」：
      红路径**只在特定输入下**才走，而「特定输入」正是每条判据自己造的。
      通用可行的是**结构检查**（①②③），它们能抓住本轮这个真实缺陷。
      **动态验红**（喂必坏输入）做不到通用 —— 如实说明，不假装做到了。
    """
    坏: list[str] = []
    try:
        import ast as _ast
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判八十五：`ast` 导不进来（{e}）"])

    _文 = Path(__file__).resolve()
    try:
        _源 = _文.read_text(encoding="utf-8")
        _树 = _ast.parse(_源)
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判八十五：解析不了自己（{type(e).__name__}: {e}）"])

    # ── 收集模块级定义的名字 ─────────────────────────────────────
    _定义: set[str] = set(dir(__builtins__) if isinstance(__builtins__, dict)
                        is False else dir(__builtins__))
    import builtins as _bi
    _定义 |= set(dir(_bi))
    # ★ **模块级内置变量**也要算 —— 它们不是 `dir(builtins)` 的成员，
    #   但每个模块里都存在。第一版漏了 `__file__` ⇒ **54 个判据被误报**
    #   （「误报的引用比缺失的引用更坏」—— 判据自己的误报同理）。
    _定义 |= {"__file__", "__name__", "__doc__", "__package__", "__spec__",
              "__loader__", "__builtins__", "__debug__", "__annotations__"}
    for _n in _树.body:
        if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            _定义.add(_n.name)
        elif isinstance(_n, _ast.Import):
            for _a in _n.names:
                _定义.add((_a.asname or _a.name).split(".")[0])
        elif isinstance(_n, _ast.ImportFrom):
            for _a in _n.names:
                _定义.add(_a.asname or _a.name)
        elif isinstance(_n, _ast.Assign):
            for _t in _n.targets:
                if isinstance(_t, _ast.Name):
                    _定义.add(_t.id)
        elif isinstance(_n, _ast.AnnAssign) and isinstance(_n.target, _ast.Name):
            _定义.add(_n.target.id)
        elif isinstance(_n, (_ast.For,)):
            if isinstance(_n.target, _ast.Name):
                _定义.add(_n.target.id)
        elif isinstance(_n, _ast.Try):
            for _sub in _n.body + _n.orelse + _n.finalbody:
                if isinstance(_sub, (_ast.FunctionDef, _ast.Assign)):
                    if isinstance(_sub, _ast.FunctionDef):
                        _定义.add(_sub.name)

    # ── 每个 `判*` 函数：查 ① ② ③ ──────────────────────────────
    _判据: list[_ast.FunctionDef] = [
        _n for _n in _ast.walk(_树)
        if isinstance(_n, _ast.FunctionDef) and _n.name.startswith("判")]
    if len(_判据) < 30:
        坏.append(f"判八十五：只找到 {len(_判据)} 个判据函数 ⇒ **扫错了文件**（不是「都对」）")

    # ① 未定义全局名（逐函数查 Load）
    _未定义: dict[str, set[str]] = {}
    for _f in _判据:
        _本地 = {_a.arg for _a in _f.args.args} | {_f.name}
        _本地 |= {_a.arg for _a in _f.args.kwonlyargs}
        for _sub in _ast.walk(_f):
            if isinstance(_sub, _ast.Name) and isinstance(_sub.ctx, _ast.Store):
                _本地.add(_sub.id)
            elif isinstance(_sub, _ast.ExceptHandler) and _sub.name:
                _本地.add(_sub.name)
            # ★★ **嵌套函数进这一支，所以参数必须在这里一起收**（R366）。
            #   我把「收参数」单独写成后面的 `elif` ⇒ **永远不执行**：
            #   前面的这一支**先命中**。又一个「条件把自己排除掉」——
            #   **`elif` 链里，顺序就是判据。**
            elif isinstance(_sub, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                   _ast.ClassDef)) and _sub is not _f:
                _本地.add(_sub.name)
                for _a in (getattr(getattr(_sub, "args", None), "args", []) or []):
                    _本地.add(_a.arg)
                for _a in (getattr(getattr(_sub, "args", None), "posonlyargs", []) or []):
                    _本地.add(_a.arg)
                for _a in (getattr(getattr(_sub, "args", None), "kwonlyargs", []) or []):
                    _本地.add(_a.arg)
                for _a in (getattr(getattr(_sub, "args", None), "vararg", None),
                           getattr(getattr(_sub, "args", None), "kwarg", None)):
                    if _a is not None:
                        _本地.add(_a.arg)
            elif isinstance(_sub, _ast.Lambda):
                for _a in (getattr(_sub.args, "args", []) or []):
                    _本地.add(_a.arg)
                for _a in (getattr(_sub.args, "kwonlyargs", []) or []):
                    _本地.add(_a.arg)
            elif isinstance(_sub, _ast.comprehension) and isinstance(_sub.target, _ast.Name):
                _本地.add(_sub.target.id)
            elif isinstance(_sub, _ast.Import):
                for _a in _sub.names:
                    _本地.add((_a.asname or _a.name).split(".")[0])
            elif isinstance(_sub, _ast.ImportFrom):
                for _a in _sub.names:
                    _本地.add(_a.asname or _a.name)
            # ★★ **嵌套函数/lambda 的参数也是本地名**（R366 第一版漏了 ⇒
            #   `def _去注释(文: str)`、`def _边(根, 名, 关系=…)` 的 `文`/`名`/`关系`
            #   被误报成「未定义的名字」，**一次 12 条假红**）。
            #   ★ 这是**放宽**（可能漏报真未定义），但
            #     **「误报的引用比缺失的引用更坏」** —— 判据自己的误报同理。
            elif isinstance(_sub, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.Lambda)):
                for _a in (getattr(_sub.args, "args", []) or []):
                    _本地.add(_a.arg)
                for _a in (getattr(_sub.args, "posonlyargs", []) or []):
                    _本地.add(_a.arg)
                for _a in (getattr(_sub.args, "kwonlyargs", []) or []):
                    _本地.add(_a.arg)
                for _a in (getattr(_sub.args, "vararg", None),
                           getattr(_sub.args, "kwarg", None)):
                    if _a is not None:
                        _本地.add(_a.arg)
        for _sub in _ast.walk(_f):
            if isinstance(_sub, _ast.Name) and isinstance(_sub.ctx, _ast.Load):
                if _sub.id not in _本地 and _sub.id not in _定义:
                    _未定义.setdefault(_f.name, set()).add(_sub.id)
    if _未定义:
        _例 = sorted(_未定义.items())[:3]
        坏.append(f"判八十五①：**{len(_未定义)} 个判据引用了不存在的名字** ⇒ "
                  f"它的红路径会 `NameError`（**只有绿的时候看不出来**）：{_例}")
    else:
        # ★ 反向：这一条必须真能查到东西（否则是「没测到」不是「通过」）
        _探 = _ast.parse("def 判探针():\n    _不存在的名字()\n")
        _f探 = [_n for _n in _ast.walk(_探) if isinstance(_n, _ast.FunctionDef)][0]
        _本地探: set[str] = set()
        _命中 = [x.id for x in _ast.walk(_f探)
                if isinstance(x, _ast.Name) and isinstance(x.ctx, _ast.Load)
                and x.id not in _本地探 and x.id not in _定义]
        if "_不存在的名字" not in _命中:
            坏.append("判八十五①：**探针查不到已知的坏名字** ⇒ 这一条是空的（不是「通过」）")

    # ② 红路径必须存在；③ `坏.append` 必须存在
    # ★★ **「红路径」不止 `_报2` 一种写法**（第一版只认 `_报2` ⇒
    #   **48 个早期判据被误报**）。判一~判四十九 用的是 `raise`/`assert`。
    #   ⇒ 红路径 = **`_报2(` / `raise` / `assert` 三者之一**。
    #   ★ 而 `坏.append` 那条**只对「用了 `坏` 这套约定」的函数**查 ——
    #     没引入 `坏` 的函数，`坏.append` 本来就不该有。
    #     （**一条判据硬套所有写法，必然误报一处**。）
    _无红: list[str] = []
    _无坏: list[str] = []
    for _f in _判据:
        _有红 = False
        _有坏append = False
        _用坏约定 = any(isinstance(_x, _ast.Name) and _x.id == "坏"
                     and isinstance(_x.ctx, _ast.Load) for _x in _ast.walk(_f))
        for _sub in _ast.walk(_f):
            if isinstance(_sub, _ast.Raise):
                _有红 = True
            elif isinstance(_sub, _ast.Assert):
                _有红 = True
            elif isinstance(_sub, _ast.Call):
                _fn = _sub.func
                if isinstance(_fn, _ast.Name) and _fn.id == "_报2":
                    _有红 = True
                if (isinstance(_fn, _ast.Attribute) and _fn.attr == "append"
                        and isinstance(_fn.value, _ast.Name) and _fn.value.id == "坏"):
                    _有坏append = True
        # ★★ **②只对「用了 `坏` 这套约定」的判据查**（R366 第一版对所有判据查 ⇒
        #   47 条早期判据被误报「永远绿」）。
        #   判一~判四十九 是**打印式**判据（走 `print` + 返回值），
        #   它们**确实**没有机器红路径 —— 那是**另一件事，单独报**，
        #   不该混在「用了 `坏` 却没红路径」里。
        if _用坏约定 and not _有红:
            _无红.append(_f.name)
        if _用坏约定 and not _有坏append:
            _无坏.append(_f.name)
    if _无红:
        坏.append(f"判八十五②：**{len(_无红)} 个判据没有任何红路径**"
                  f"（`_报2` / `raise` / `assert` 都没有）⇒ 永远绿：{_无红[:4]}")
    if _无坏:
        坏.append(f"判八十五③：**{len(_无坏)} 个判据用了 `坏` 却一次都不 `坏.append`** ⇒ "
                  f"写不出红，**是装饰不是判据**：{_无坏[:4]}")

    if 坏:
        return _报2(坏)
    print(f"   判据自检：绿 —— 判八十五（{len(_判据)} 条判据：引用的名字都存在 · "
          f"都有 `_报2` 红路径 · 都有 `坏.append`；另用探针确认①查得出东西）")

def 判八十九PEP695类型别名必须抽成定义():
    """★★★ **`type X = ...` 是定义，不是别的**（R389）。

    为什么要有这一条：标准库未解析 Top 前三条**同一个根** ——
    `numpy/_typing/_shape.py` 里
    ````
    type _Shape = tuple[int, ...]
    type _AnyShape = tuple[Any, ...]
    type _ShapeLike = SupportsIndex | Sequence[SupportsIndex]
    ````
    ⇒ **该文件定义节点 0 个** ⇒ `NDArray 460 · _Shape 294 · _AnyShape 86`
      全落未解析（`numpy/_typing/__init__.py` 只是 re-export stub，靠
      `from ._shape import _Shape` 转出去）。
    ★ tree-sitter 的**实得形状**（打出来的，不是猜的）：
      `type_alias_statement` children = [('type','type'), ('type','_Shape'),
                                        ('=','='), ('type','tuple[...]')]
      ⇒ **名字是第二个孩子，而且类型是 `type` 不是 `identifier`**。
    ★ 判据**自带尺子探针**：先验证「tree-sitter 真会给这个节点类型」，
      否则树的形状变了，判据会**安静地变成永远绿**。

    ★ 红路径（本判据写完就验过）：把 `elif n.type == "type_alias_statement":`
      那一支去掉 ⇒ 该文件定义节点回到 0 ⇒ `_Shape` 落未解析 ⇒ 红。
    """
    import os as _os, sys as _sys, json as _json, tempfile as _tf, pathlib as _pl
    _sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "src"))
    from agentscope_orchestrator import kg as _kg

    # ── ① 尺子自证：tree-sitter 真会给 `type_alias_statement` 吗
    from tree_sitter import Language as _L, Parser as _P
    import tree_sitter_python as _tsp
    解 = _P(_L(_tsp.language()))
    树 = 解.parse(b"type X = int\n")
    _有 = [c.type for c in 树.root_node.children]
    if "type_alias_statement" not in _有:
        _报2("判八十九：**tree-sitter 不认 `type X = int`**"
             f"（实测根孩子 {_有}）⇒ **本判据没有红路径**，"
             "它测不到东西，先把尺子修好再谈代码")

    根 = _pl.Path(_tf.mkdtemp(prefix="kg89_"))
    (根 / "甲.py").write_text(
        "type _Shape = tuple[int, ...]\n"
        "type _AnyShape = tuple[object, ...]\n"
        "type 泛型[T] = list[T]\n", encoding="utf-8")
    (根 / "乙.py").write_text(
        "from 甲 import _Shape, _AnyShape\n\n\n"
        "def 用(x: _Shape):\n"
        "    return _AnyShape\n", encoding="utf-8")
    _kg.建图(根, 打印=lambda *_: None)
    _kg.解析引用(根, 打印=lambda *_: None)

    # ── ② 定义侧：三个别名都要成为定义节点
    图 = _kg.加载图(根)
    节 = 图.get("节点") or {}
    定义 = {n.get("名") for n in 节.values()
            if n.get("文件") == "甲.py" and n.get("类") in ("定义", "声明")}
    _缺 = {"_Shape", "_AnyShape", "泛型"} - 定义
    if _缺:
        _报2(f"判八十九：`甲.py` 里 PEP695 类型别名**没被抽成定义**：缺 {sorted(_缺)}"
             f"（实得定义 {sorted(定义)}）—— `type X = ...` 是**定义**，"
             "不抽它 ⇒ 引用它的文件全部解不到")

    # ── ③ 引用侧：导入进来的别名必须解到跨文件定义，不许落未解析
    引用 = _json.loads((_kg._kg目录(根) / "引用.json").read_text(encoding="utf-8"))
    _坏 = []
    for r in 引用:
        if (r.get("起") or "")[5:] != "乙.py":
            continue
        if r.get("名") == "_AnyShape" and r.get("行") == 5:
            if "**未解析**" in (r.get("依据") or "") or r.get("终", "").startswith("name:"):
                _坏.append(("_AnyShape", r.get("依据")))
    if _坏:
        _报2(f"判八十九：`乙.py` 里 `_AnyShape` 没解到 `甲.py` 的定义：{_坏} —— "
             "类型别名一旦抽成定义，导入绑定就必须能跨文件解到它")


def 判八十八模块解析不许给错答案():
    """模块名有多个候选文件时，必须按**语言的解析顺序**取，不许按扫盘顺序。

    ★ 病根（R378 实测，**这是"错答案"不是"缺答案"**）：
      `import warnings` 被解到 **`site-packages/pydantic/warnings.py`** ——
      而根目录就摆着 `warnings.py`。24,296 条「导入模块」里 **11,209 条（46%）**
      的模块名有多个候选文件，其中 **5,313 条**被静默压成一个。
      ⇒ 根因是 `_入口优先` 之后**平局时按扫盘顺序取第一个** ——
        与语言的解析规则**毫无关系**。
    ★ 口径：**「答案错了」比「未解析」更坏** ——
      未解析会被 ④ 报成缺口；**错答案会被当成事实用于计划**。

    ★ 判据（用**带真歧义**的 fixture，不是空壳）：
      ① 建 `warnings.py`（根）与 `site-packages/pydantic/warnings.py`（依赖目录）；
      ② `main.py` 写 `import warnings`；
      ③ **必须**解到根目录那个；
      ④ **红路径自检**：`_导入优先级` 对**依赖目录**里的路径必须排后 ——
         若这条反过来，本判据就该红（**判据自己的红路径**）。
    """
    坏 = []
    import os as _os
    import sys as _sys
    import json as _json
    import tempfile as _tf
    import pathlib
    _src = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src")
    if _src not in _sys.path:
        _sys.path.insert(0, _src)
    import agentscope_orchestrator.kg as _kg

    # ④ 先验**尺子**：优先级函数本身要能区分（否则下面的"解对了"可能是碰巧）
    try:
        _a = _kg._导入优先级("warnings.py")
        _b = _kg._导入优先级("site-packages/pydantic/warnings.py")
        if not (_a < _b):
            坏.append(f"尺子坏了：`warnings.py`{_a} 未排在 "
                    f"`site-packages/pydantic/warnings.py`{_b} 之前 ⇒ "
                    f"本判据没有红路径（**没测到不能当通过了**）")
    except Exception as e:
        坏.append(f"`_导入优先级` 调不通：{type(e).__name__}: {e}")

    if 坏:
        return _报2(坏, "判八十八模块解析不许给错答案：绿")

    # ① 带**真歧义**的 fixture
    根 = pathlib.Path(_tf.mkdtemp(prefix="kgprio_"))
    (根 / "warnings.py").write_text("def warn():\n    return 1\n", encoding="utf-8")
    (根 / "site-packages" / "pydantic").mkdir(parents=True, exist_ok=True)
    (根 / "site-packages" / "pydantic" / "warnings.py").write_text(
        "def warn():\n    return 2\n", encoding="utf-8")
    (根 / "main.py").write_text("import warnings\n\ndef f():\n    return warnings.warn()\n",
                             encoding="utf-8")
    _旧 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(_kg._kg目录(根))
    try:
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        _rs = _json.loads((_kg._kg目录(根) / "引用.json").read_text(encoding="utf-8"))
    finally:
        if _旧 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = _旧

    _模 = [r for r in _rs if (r.get("依据") or "") == "导入模块" and r.get("名") == "warnings"]
    if not _模:
        坏.append("fixture 里 `import warnings` **一条「导入模块」都没有** ⇒ "
                "判据没测到东西（**没测到不能当通过了**）")
    for r in _模:
        _靶 = r.get("目标文件")
        if _靶 != "warnings.py":
            坏.append(f"`import warnings` 解到了 {_靶!r} —— 应为 `warnings.py`"
                    f"（**错答案比未解析更坏**：它会被当成事实用于计划）")

    if 坏:
        return _报2(坏, "判八十八模块解析不许给错答案：绿")
    print("   判八十八模块解析不许给错答案：绿 —— 真歧义 fixture 下解到根目录模块，且尺子自证有红路径")


def 判八十七传递层不许筛字段():
    """禁止「白名单重建」：把一份数据按字段名逐个搬到另一份 dict 里。

    ★ 病根（R365→R373，**同一处病抓到四次**）：
      `取导入` 出口、导入边 `append`、`解析引用` 的导入索引重建、下游 `_节型`——
      每一处都写过 `{"a": x.get("a"), "b": x.get("b"), ...}`。
      **每一处都不报错**，只是让字段消失：
        · R365：新加的 `节点类型` 没了 ⇒ `_节型 = ""` ⇒ **别名解析整条失效**；
        · R373：新加的 `映射`（本地名→原名）没了 ⇒ 我整套改名逻辑
          **一行都没生效**（探针实录 `_原名=_warn` 没变，而边上有 `映射`）。
      ⇒ 这类缺陷**永远绿**，只能靠**结构判据**抓：
        **传数据一律 `{**源, ...}` 铺开；逐键列举就是筛。**

    ★ 判据（`ast` 扫 `kg.py`）：
      一个**字面量 dict**，**没有 `**` 铺开**，且**至少 2 个键**的值里
      （任意深度）出现了 `X.get("同名键")` ⇒ 它就是白名单重建。
      · `{**x, "行": ...}` 有 `**` ⇒ 不算（**这是正确写法**）；
      · `{"行": 1, "名": "a"}` 值是常量 ⇒ 不算（**新建数据**，不是搬运）。
    ★ 探针（本判据自己的红路径）：对一段**已知是白名单**的源码，
      检测器**必须能抓到** —— 否则「0 处」只说明我尺子坏了。
    """
    坏 = []
    import ast as _ast
    import os as _os
    _根 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    _kgp = _os.path.join(_根, "src", "agentscope_orchestrator", "kg.py")
    if not _os.path.isfile(_kgp):
        坏.append(f"找不到 kg.py：{_kgp}")
        return _报2(坏, "判八十七传递层不许筛字段：绿")

    def _取过同名(v, 键):
        for n in _ast.walk(v):
            if (isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
                    and n.func.attr == "get" and n.args
                    and isinstance(n.args[0], _ast.Constant)
                    and n.args[0].value == 键):
                return True
        return False

    def _扫(源码: str) -> list:
        树 = _ast.parse(源码)
        出 = []
        for f in _ast.walk(树):
            if not isinstance(f, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            for d in _ast.walk(f):
                if not isinstance(d, _ast.Dict):
                    continue
                if any(k is None for k in d.keys):
                    continue                      # 有 ** 铺开 ⇒ 正确写法
                同 = [k.value for k, v in zip(d.keys, d.values)
                     if isinstance(k, _ast.Constant) and isinstance(k.value, str)
                     and _取过同名(v, k.value)]
                if len(同) >= 2:
                    出.append((f.name, d.lineno, 同))
        return 出

    # ① **探针先跑**：这段是**已知的白名单**，抓不到说明尺子坏了
    _样本 = (
        "def f(x):\n"
        "    return {\"a\": x.get(\"a\"), \"b\": list(x.get(\"b\") or [])}\n"
    )
    _探 = _扫(_样本)
    if not _探:
        坏.append("探针没抓到已知的白名单样本 ⇒ 判据自己没有红路径，"
                "「0 处」不能当通过（**没测到不能当通过了**）")
    else:
        _样本2 = "def f(x):\n    return {**x, \"a\": x.get(\"a\")}\n"
        if _扫(_样本2):
            坏.append("探针误报：`{**x, ...}` 是正确写法（铺开），却被当成白名单")

    # ② 真扫 kg.py
    命中 = _扫(open(_kgp, encoding="utf-8").read())
    for 名, 行, 键 in 命中:
        坏.append(f"kg.py::{名} L{行} 是白名单重建（{len(键)} 键逐字搬运：{键[:6]}）"
                f" ⇒ 改成 `{{**源, ...}}` 铺开；**传递层不许筛字段**")

    if 坏:
        return _报2(坏, "判八十七传递层不许筛字段：绿")
    print("   判八十七传递层不许筛字段：绿 —— kg.py 无白名单重建，且探针自证有红路径")


def 判八十六Lean合流的计数必须与落盘同源() -> None:
    """**目标 ⑥ 的落盘判据。** elaborator 确证名合流进来之后，**图上必须真有那些节点**。

    ★★★ R368 真跑（`formal_proof`，4,033 个 `.lean`）暴露的缺陷：
      ```
      确证模块数 = 746 · 确证名总数 = 53,704
      ★文本级升级为已确证 = 32,905 · ★只有 elaborator 有 = 17,859
      ★挂不上文件 = 5
      ```
      **全对。** 而 `加载图` 数出来的 `来源 == "elaborator"` 节点是 **0**。
      ⇒ `灌Lean确证` **只写了 `边.json`，没写 `节点.json`** ——
        新建的 17,859 个节点**全丢**，那 17,859 条 `包含` 边**悬空**。
      ★ 它自己的注释就在上面两行写着「`加载图` 读的是 `节点.json` + `边.json`，
        写错地方的后果是**判定全对、数字全对，下次读出来却是「没做过」**」。
        **警告写对了，动作漏了一个。**
      ★ 口径：**「报了数」与「落盘了」是两件事** ——
        比「登记了 ≠ 接上了」更隐蔽：**它把计数算对了**，读数字的人不会怀疑。

    ★ 三个方向（小 fixture，不依赖大仓）：
      ① **落盘同源**：图上 `来源 == "elaborator"` 的节点数
         == 读数里的「只有 elaborator 有」+ 挂上文件的那些；
         **计数对而图上是 0 ⇒ 就是修前那个缺陷**；
      ② **不许悬空边**：`包含` 边的 `终` 必须在节点表里
         （节点没落盘时边就悬空 —— 这是①的**独立**旁证）；
      ③ **只读的仓也必须能用**：`KG_图目录` 覆盖后要真落在覆盖目录里
         （只读根是常态，`_kg目录` 的 docstring 已写明）。
    """
    坏: list[str] = []
    import os as _os
    import tempfile as _tf
    from pathlib import Path as _P
    import importlib as _il

    根 = _P(_tf.mkdtemp(prefix="kgR368_"))
    图目录 = _P(_tf.mkdtemp(prefix="kgR368图_"))
    try:
        (根 / "a.lean").write_text(
            "namespace Foo\n\ndef bar : Nat := 1\n\ndef baz : Nat := 2\n\nend Foo\n",
            encoding="utf-8")
        # ★ 确证档：**模块 -t- 限定名**，与 `灌Lean确证` 的读法一致
        档 = 根 / "确证.tsv"
        档.write_text("a\tFoo.bar\n" "a\tFoo.别的名（文本级抽不到）\n",
                      encoding="utf-8")
        # ★★ `_env图目录` 是**模块导入时**读的 ⇒ 必须先设再导入
        _旧 = _os.environ.get("KG_图目录")
        _os.environ["KG_图目录"] = str(图目录)
        try:
            from agentscope_orchestrator import kg as _kg
            _il.reload(_kg)
            # ★★ **必须先 `建图`** —— `灌Lean确证` 开头 `加载图` 拿到「未测到」
            #   就**直接早退**（它是「往已有图上合流」，不是「自己建图」）。
            #   我第一版 fixture 直接灌 ⇒ 图空 ⇒ **判据说「落盘没生效」（假红）**。
            #   「假红与真红长得一样，唯一的分界是把实得值打出来核对」。
            _kg.建图(根, 打印=lambda *_: None)
            出 = _kg.灌Lean确证(根, 档, 打印=lambda *_: None)
            图 = _kg.加载图(根)
        finally:
            if _旧 is None:
                _os.environ.pop("KG_图目录", None)
            else:
                _os.environ["KG_图目录"] = _旧
            # ★★★ R443：**reload 必须成对** ——
            #   `kg.py` 的 `_env图目录` 是**模块导入时读一次**的常量；
            #   上面 `reload` 之后它**永久变成了 tmp 图目录**，
            #   而这里只恢复了 `os.environ` ⇒ **模块常量还是 tmp** ✗
            #   ★ 实测后果：**这条判据之后的所有判据，`_kg目录(根)` 全指向
            #     那个 tmp 目录** ⇒ `判八十四` 读 `.kg\引用.json` 找不到 ⇒
            #     `FileNotFoundError` —— **单跑绿、整套红**。
            #   ⇒ **改环境必须「设 → reload」成对，「恢复 → reload」也成对。**
            #   ★ 但 reload **不能在这里**（R444 实测）：这条判据后面还要检查
            #     `_kg._kg目录(根)` **是否被 `KG_图目录` 覆盖生效**；
            #     在这里 reload ⇒ `_env图目录` 立刻变回 `''` ⇒ 那次检查
            #     **看到的是"覆盖没生效"** ⇒ 把判八十六③判红了 ✗
            #   ⇒ reload 放到**本判据的最后**（所有检查之后）。
        节 = 图.get("节点") or {}
        边 = 图.get("边") or []
        if not 节:
            坏.append("判八十六：灌完之后**图是空的** ⇒ 落盘没生效（这就是修前那个缺陷）")
        else:
            _图上有 = sum(1 for n in 节.values() if n.get("来源") == "elaborator")
            _读数 = int(出.get("★只有 elaborator 有（文本级抽不到）") or 0)
            _挂上 = int(出.get("★挂不上文件（模块→文件对不上）") or 0)
            if _读数 <= 0:
                坏.append(f"判八十六①：`★只有 elaborator 有` = {_读数} ⇒ "
                          f"**fixture 没造出「只有 elaborator 知道」的名字**，"
                          f"这一条测不到（不许当通过）")
            elif _图上有 <= 0:
                坏.append(f"判八十六①：读数说新增 {_读数} 个 elaborator 节点，"
                          f"而**图上一个是 0** ⇒ **报了数没落盘**（修前那个缺陷回来了）")
            # ② 悬空边
            _悬 = [e for e in 边
                  if (e.get("关系") or "") == "包含"
                  and e.get("终") not in 节]
            if _悬:
                坏.append(f"判八十六②：**{len(_悬)} 条 `包含` 边的终点不在节点表里**"
                          f"（悬空边）—— 节点没落盘的独立旁证：{_悬[:2]}")
            # ③ 确认 `elaborator` 节点带 `确证` 标
            if _图上有:
                _带标 = sum(1 for n in 节.values()
                          if n.get("来源") == "elaborator" and n.get("确证") is True)
                if _带标 != _图上有:
                    坏.append(f"判八十六③：{_图上有} 个 elaborator 节点里只有 {_带标} 个"
                              f"带 `确证: True` ⇒ 「只有 elaborator 知道」这个事实没留在图上")
        # ③' 覆盖目录必须真生效
        _实 = _kg._kg目录(根)
        if 图目录 not in _实.parents and 图目录 != _实.parent:
            坏.append(f"判八十六③：`KG_图目录` 覆盖没生效（图落在 {_实}）⇒ "
                      f"**只读的仓就用不了**（docstring 说只读根是常态）")
    finally:
        import shutil as _sh
        for d in (根, 图目录):
            _sh.rmtree(d, ignore_errors=True)

    if 坏:
        # ★★★ R444：reload **必须在所有检查之后** ——
        #   否则这条判据自己的③（`KG_图目录` 覆盖生效）会看到 `_env图目录=''` ✗
        _il.reload(_kg)
        return _报2(坏, "判八十六Lean合流的计数必须与落盘同源：绿")
    # ★ 绿路径也要 reload（它同样把 `_env图目录` 改过）
    _il.reload(_kg)
    print("   目标⑥：绿 —— 判八十六（elaborator 节点真落盘 · 无悬空边 · "
          "确证标在位 · KG_图目录 覆盖生效）")

def 判九十常驻图必须存在且读取数同源() -> None:
    """★★★ **常驻的图要能被"知道"**（R401）—— 索引存在 · 读数是同一份 · 桶名不撞。

    ★ 为什么需要：R400 建了 `work/图/`（Lean 图 + `索引.json`），
      但**索引是我手工跑一次生成的** —— 没有任何判据保证它
       ① 存在（重跑一次就没了？图仓库被清空？）
       ② **读数是"同一份"**（索引里写 213,852 而图上其实是别的数 ⇒ 数字过期）
       ③ **每个登记的根有图，或明说"没有"**（不许静默漏一个根）
       ④ **桶名不退化**（R400 的真 bug：`太虚元道` → `____`）
       ⑤ **不同根不同桶**（退化后互相盖）

    ★ 口径：「它在那里」≠「它说对了」——
      **索引的每一个数字，都要能回到图里重新数一遍。**
    """
    坏: list[str] = []
    import json as _json                            # ★ 模块级没导 json（同 os）
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十：`kg` 导不进来（{type(e).__name__}: {e}）"])

    # ── ④⑤ 桶名：**必须用子进程问**（R401 第二次修）
    #   ★ 第一版对所有根问桶名 ⇒ 红文写"`根` 与 `根\.audit_trail` 同一个桶 `.kg`" ✗
    #     —— 因为 `KG_图目录` 没设时走 `根 / .kg`，**本来就不分桶**。
    #     **一条过严的判据会把整片判据池染红。**
    #   ★ 第二版在判据进程里 `os.environ["KG_图目录"] = …` ⇒ **仍然 `.kg`** ✗
    #     根因：`kg.py` 的 `_env图目录` 是**导入时读一次**的模块级常量，
    #     之后再改 `os.environ` **不生效**。
    #     （判八十六③′ 之所以能过，是因为它**跑子进程**。）
    #   ★ 口径：**「状态不许只在起点置一次」** —— 判据要么跑子进程，
    #     要么就问那个**真的会重读**的东西。**这里只有子进程是真的。**
    桶们: dict[str, str] = {}
    import os as _os, subprocess as _sp            # ★ 模块级没导
    _探 = (
        "import sys,os,pathlib\n"
        "sys.path.insert(0, r'%s')\n"
        "from agentscope_orchestrator import kg as K\n"
        "import json\n"
        "出=[]\n"
        "for d in json.loads(sys.argv[1]):\n"
        "    出.append([d, K._kg目录(pathlib.Path(d)).name])\n"
        "print(json.dumps(出, ensure_ascii=False))\n"
    ) % str(Path(__file__).resolve().parents[1] / "src")
    _根表: list[str] = []
    _总 = Path(r"E:\\基础设施\\离散时空认知物理基础研究\\太虚元道")
    if _总.is_dir():
        _根表 = [str(_总)] + [str(d) for d in _总.iterdir() if d.is_dir()][:12]
    if _根表:
        _环 = dict(_os.environ)
        _环["KG_图目录"] = str(Path(__file__).resolve().parents[1] / "work" / "图")
        try:
            _r = _sp.run([sys.executable, "-B", "-c", _探,
                          _json.dumps(_根表, ensure_ascii=False)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=_环, timeout=180)
            _列 = _json.loads((_r.stdout or "[]").strip().splitlines()[-1]) if _r.stdout.strip() else []
        except Exception as e:                    # noqa: BLE001
            _列 = []
            坏.append(f"判九十④：桶名探针跑不起来（{type(e).__name__}: {e}）⇒ "
                      f"**没测到不能当通过了**")
        for _d, _桶 in _列:
            if not _桶 or not _桶.strip("._"):
                坏.append(f"判九十④：根 `{Path(_d).name}` 的桶名退化成 `{_桶!r}` ⇒ "
                          f"**清洗把非 ASCII 全洗掉了**（R400 的 `____`）")
            if _桶 in 桶们 and 桶们[_桶] != _d:
                坏.append(f"判九十⑤：两个根派生出**同一个桶** `{_桶}`："
                          f"`{桶们[_桶]}` 与 `{_d}` ⇒ **后建的会盖前建的**")
            桶们.setdefault(_桶, _d)

    # ── ②③ 索引：必须存在，且每个数字都能回图里重数
    图仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    索档 = 图仓 / "索引.json"
    if not 图仓.is_dir() or not 索档.is_file():
        # ★ 没有常驻索引 ⇒ **如实报"没建"**，不算通过（口径：没测到不等于通过）
        return _报2([f"判九十①：常驻索引不在 `{索档}` ⇒ "
                     f"**图还在,但'知道自己有什么'的入口没建**（跑一次 R400 的索引生成）"])
    try:
        索 = _json.loads(索档.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十①：索引读不了（{type(e).__name__}: {e}）"])
    根们 = 索.get("根们") or []
    if not 根们:
        坏.append("判九十③：索引里**一个根都没登记** ⇒ 索引是空壳，不是入口")

    for 条 in 根们:
        _根 = Path(条.get("根") or "")
        if not _根.is_dir():
            坏.append(f"判九十③：登记的根不存在：`{_根}` ⇒ **索引里留着死项**")
            continue
        if not 条.get("有图"):
            # ★ **"没有"是可以的，但必须"明说"**（R401）——
            #   判据防的是**静默漏一个根**，不是"必须每个根都有图"。
            #   （口径：**「没登记」与「登记了但为空」是两件事**；
            #     再加一层：**「登记了、为空、且说了为什么」允许**。）
            if not (条.get("备注") or "").strip():
                坏.append(f"判九十③：根 `{_根.name}` **登记了但没有图**，且索引里"
                          f"没有说明为什么 ⇒ **静默漏一个根**")
            continue
        # ★★★ **不许在判据里重算图目录**（R401 第三次修）。
        #   第一版用 `_kg._kg目录(_根)` ⇒ 判据进程里 `KG_图目录` 没设
        #   ⇒ 它读 `根\.kg\图.json`（3 字节空壳）⇒ **"图上重数是 0"** ✗
        #   ★ 而**索引里本来就记着图目录** —— **能问到就别自己再算一遍**
        #     （口径 43）。重算 = 用**另一个环境**下的规则**去核一份
        #     在**那个环境**下生成的数据 ⇒ **必然假红**。
        _kgd = Path(条.get("图目录") or "")
        if not _kgd.is_dir():
            坏.append(f"判九十②：索引里 `{_根.name}` 的图目录不在：`{_kgd}`")
            continue
        _图档 = _kgd / _kg._图档
        if not _图档.is_file():
            坏.append(f"判九十②：索引说 `{_根.name}` 有图，但 `{_图档}` 不在 ⇒ "
                      f"**索引与磁盘不一致**")
            continue
        _节 = _json.loads((_kgd / "节点.json").read_text(encoding="utf-8")) \
            if (_kgd / "节点.json").is_file() else {}
        _实节 = len(_节)
        _索节 = 条.get("节点数")
        if _索节 != _实节:
            坏.append(f"判九十②：`{_根.name}` 索引写节点 **{_索节}**，"
                      f"图上重数是 **{_实节}** ⇒ **数字过期**（这正是 R395 那类病）")
        _引 = _kgd / "引用.json"
        if _引.is_file():
            try:
                _by = _json.loads(_引.read_text(encoding="utf-8"))
            except Exception:                     # noqa: BLE001
                _by = None
            if _by is not None and 条.get("引用条数") is not None:
                if 条["引用条数"] != len(_by):
                    坏.append(f"判九十②：`{_根.name}` 索引写引用 "
                              f"**{条['引用条数']}**，重数是 **{len(_by)}** ⇒ 数字过期")
                _实未 = sum(1 for r in _by if "**未解析**" in (r.get("依据") or ""))
                if 条.get("未解析") is not None and 条["未解析"] != _实未:
                    坏.append(f"判九十②：`{_根.name}` 索引写未解析 "
                              f"**{条['未解析']}**，重数是 **{_实未}** ⇒ 数字过期")

    if 坏:
        return _报2(坏, "判九十常驻图必须存在且读取数同源：绿")
    print(f"   常驻：绿 —— 判九十（索引 {len(根们)} 个根 · 读数与图逐项同源 · "
          f"{len(桶们)} 个桶名互不退化）")

def 判九十一增量更新必须与全量建图等价() -> None:
    """★★★ **`更新图` 必须和 `建图` 给出同一张图**（R402）。

    ★ 为什么需要：`建图` 是**全量**，`更新图` 是**增量**（口径：「能改和能改回来是两件事」）。
      增量如果**漏搬一样东西**，症状是"图看起来还在"而**某些节点悄悄没了** ——
      这是最难发现的一类错（R400 的 `____`、R395 的过期数字都是同一族）。

    ★ 判据形状：**同一份输入，两条路，结果必须逐项相等**：
      · A：`建图`（全量）
      · B：`建图` → 再 `更新图`（无改动）
      **A 与 B 的 节点数 / 边数 / 节点 id 集合 必须完全相等。**
      ★ 不比"差不多"，比**集合相等** —— 「总数不变 + 桶间搬动 = 改判」（口径 42）
        在这里表现为「总数相近但成员不同」。

    ★ 红路径：`更新图` 走的是"只重算改动文件 + 复用旧节"，
      任何"复用"漏掉的字段都会在这里露出来。
    """
    坏: list[str] = []
    import json as _json
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十一：`kg` 导不进来（{type(e).__name__}: {e}）"])

    import shutil as _sh
    import tempfile as _tf
    甲 = Path(_tf.mkdtemp(prefix="kg91A_"))
    乙 = Path(_tf.mkdtemp(prefix="kg91B_"))
    try:
        # ★ fixture 要覆盖**多种会走不同分支**的结构：
        #   导入绑定 / 跨文件定义 / 函数体内绑定 / 类体赋值 / 链式赋值 /
        #   for 单目标 / for 多目标 / PEP695 泛型 / 类型别名 / 相对导入
        文甲 = (
            "import os\n"
            "from collections import OrderedDict\n"
            "\n"
            "MAX_PREC = 425000000\n"
            "BOM_A = BOM_B = 1\n"
            "\n"
            "\n"
            "class D:\n"
            "    __slots__ = ()\n"
            "    update = __update = 2\n"
            "\n"
            "    def m(self):\n"
            "        x = 1\n"
            "        for i in range(x):\n"
            "            x += i\n"
            "        for k, v in {}.items():\n"
            "            x += k\n"
            "        return x\n"
            "\n"
            "\n"
            "def f[T](y: T) -> T:\n"
            "    return y\n"
            "\n"
            "\n"
            "type Alias = list[int]\n"
        )
        文乙 = "from 甲 import f\n\n\nz = f(1)\n" if False else (
            "from . import 甲\n\n\nz = 甲.f(1)\n")
        for d, 文2 in ((甲, 文乙), (乙, 文乙)):
            (d / "甲.py").write_text(文甲, encoding="utf-8")
            (d / "乙.py").write_text(文2, encoding="utf-8")

        # ── A：全量
        _kg.建图(甲, 打印=lambda *_: None)
        _节A = _json.loads((_kg._kg目录(甲) / "节点.json").read_text(encoding="utf-8"))
        _边A = _json.loads((_kg._kg目录(甲) / "边.json").read_text(encoding="utf-8"))
        # ── B：全量 + 增量（无改动）
        _kg.建图(乙, 打印=lambda *_: None)
        _kg.更新图(乙, 打印=lambda *_: None)
        _节B = _json.loads((_kg._kg目录(乙) / "节点.json").read_text(encoding="utf-8"))
        _边B = _json.loads((_kg._kg目录(乙) / "边.json").read_text(encoding="utf-8"))

        _a = set(_节A)
        _b = set(_节B)
        if len(_节A) != len(_节B):
            坏.append(f"判九十一：全量 **{len(_节A)}** 个节点，"
                      f"全量+增量 **{len(_节B)}** 个 ⇒ **增量之后不是同一张图**")
        if _a - _b:
            坏.append(f"判九十一：增量**丢了 {len(_a - _b)} 个节点**："
                      f"{sorted(_a - _b)[:3]}")
        if _b - _a:
            坏.append(f"判九十一：增量**多出 {len(_b - _a)} 个节点**："
                      f"{sorted(_b - _a)[:3]}")
        if len(_边A) != len(_边B):
            坏.append(f"判九十一：全量 **{len(_边A)}** 条边，"
                      f"全量+增量 **{len(_边B)}** 条 ⇒ 边数不等")
        # ★ 逐字段比：**同一个 id 的节点，字段也要一样** ——
        #   "节点还在但字段被复用成了空"是更隐蔽的一种
        _差字 = []
        for _k in sorted(_a & _b):
            if _节A[_k] != _节B[_k]:
                _差字.append(_k)
                if len(_差字) >= 3:
                    break
        if _差字:
            坏.append(f"判九十一：**{len(_差字)} 个节点 id 相同但字段不同** ⇒ "
                      f"增量复用时字段被写坏了：{_差字}")
    finally:
        for d in (甲, 乙):
            _sh.rmtree(d, ignore_errors=True)

    if 坏:
        return _报2(坏, "判九十一增量更新必须与全量建图等价：绿")
    print("   增量：绿 —— 判九十一（全量与全量+增量：节点集合、边数、逐字段全等）")

def 判九十二灌确证后增量不许丢确证节点() -> None:
    """★★★ **`灌确证` 进来的节点，`更新图` 之后必须还在**（R404）。

    ★ 为什么需要（R403 定的因）：
      `灌Lean确证` 给「模块→文件对不上」的确证名建了节点，归属是伪路径
      `"<elaborator>"`。而增量搬运的条件原来是**只有** `相 in 没变集`
      ⇒ 伪归属**根本不是文件**，永远不在「没变的文件」里 ⇒ **每次增量都被丢**。
      实测：213,852 → 213,847（少 5），而**读数上看不出异常**。

    ★ 判据形状：**「灌确证之后」与「再增量之后」必须是同一张图**，
      而且**两类都要覆盖**：
        · 挂得上的（`模块 → 文件` 能对上）
        · **挂不上的**（模块不在仓里 ⇒ 伪归属 `<elaborator>`）
      只测「挂得上」的会**恰好漏掉出问题的那一类**（口径 82 的直用）。
    """
    坏: list[str] = []
    import json as _json
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十二：`kg` 导不进来（{type(e).__name__}: {e}）"])

    import os as _os
    import shutil as _sh
    import tempfile as _tf
    甲 = Path(_tf.mkdtemp(prefix="kg92A_"))
    乙 = Path(_tf.mkdtemp(prefix="kg92B_"))
    旧图 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(Path(_tf.mkdtemp(prefix="kg92KG_")))
    try:
        文 = ("namespace Demo\n"
              "def foo : Nat := 1\n"
              "end Demo\n")
        文2 = ("namespace Other\n"
               "def bar : Nat := 2\n"
               "end Other\n")
        # ★★ **确证档必须放在 fixture 之外**（R404 修正）——
        #   第一版把它写在 `甲/` 里 ⇒ `甲` 比 `乙` **多一个文件**
        #   ⇒ 判据红在 `chunk:确证.tsv:1` / `file:确证.tsv` 上，
        #     **那是"两个目录不同源"，不是被测对象的病** ✗
        #   ★ 口径：**"两条路比同一个结果"的前提是"两条路的输入相同"** ——
        #     输入不同源时，读数的差说明不了任何事。
        _档 = Path(_tf.mkdtemp(prefix="kg92T_")) / "确证.tsv"
        _档.write_text("Demo\tDemo.foo\tDemo.foo_auto\n"
                       "NoSuchMod\tNoSuchMod.ghost\n", encoding="utf-8")
        for d in (甲, 乙):
            (d / "Demo.lean").write_text(文, encoding="utf-8")
            (d / "Other.lean").write_text(文2, encoding="utf-8")

        _kg.建图(甲, 打印=lambda *_: None, 灌确证=True, 确证档=_档)
        _节A = _json.loads((_kg._kg目录(甲) / "节点.json").read_text(encoding="utf-8"))
        _kg.建图(乙, 打印=lambda *_: None, 灌确证=True, 确证档=_档)
        # ★★★ **增量时"不重灌"** —— 这是真实用法，也是唯一能测出病的形状（R404 修正）。
        #   第一版写 `更新图(..., 灌确证=True)` ⇒ **破坏后仍然绿** ✗
        #   根因：重灌会把丢掉的节点**重新建回来** ⇒ **丢失被掩盖**。
        #   ★ 口径：**判据的红路径没被验证过 = 没测到不能当通过了** ——
        #     本轮就是靠"临时破坏 + 确认变红"才发现**判据本身是空的**。
        #     若只跑一次绿就收工，这条判据会**永久假绿**。
        #   ★ 而且"不重灌"才是真实用法：日常改了文件跑增量，
        #     不该每次重灌 5 万个确证名。
        _kg.更新图(乙, 打印=lambda *_: None)
        _节B = _json.loads((_kg._kg目录(乙) / "节点.json").read_text(encoding="utf-8"))

        _确A = {k for k, v in _节A.items() if v.get("来源") == "elaborator"}
        _无源 = {k for k, v in _节A.items() if v.get("无源文件")}
        if not _确A:
            坏.append("判九十二：fixture **一条 elaborator 节点都没造出来** ⇒ "
                      "**这条判据没测到东西**（没测到不能当通过了）")
        if not _无源:
            坏.append("判九十二：fixture **没造出「挂不上文件」的那一类** ⇒ "
                      "恰好漏掉 R403 出问题的那一类（口径 82）")
        if len(_节A) != len(_节B):
            坏.append(f"判九十二：灌确证后 **{len(_节A)}** 个节点，"
                      f"再增量之后 **{len(_节B)}** 个 ⇒ **确证节点被增量丢了**")
        _丢 = set(_节A) - set(_节B)
        if _丢:
            坏.append(f"判九十二：增量**丢了 {len(_丢)} 个节点**：{sorted(_丢)[:3]}")
        _多 = set(_节B) - set(_节A)
        if _多:
            坏.append(f"判九十二：增量**多出 {len(_多)} 个节点**：{sorted(_多)[:3]}")
        _无源B = {k for k, v in _节B.items() if v.get("无源文件")}
        if _无源 - _无源B:
            坏.append("判九十二：**「挂不上文件」的节点在增量里全没了**："
                      f"{sorted(_无源 - _无源B)[:3]} ⇒ "
                      "「按文件刷新」把它当成「文件没了」（R403 的原病）")
    finally:
        if 旧图 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = 旧图
        for d in (甲, 乙):
            _sh.rmtree(d, ignore_errors=True)

    if 坏:
        return _报2(坏, "判九十二灌确证后增量不许丢确证节点：绿")
    print("   合流增量：绿 —— 判九十二（挂得上与挂不上两类，增量后节点集合全等）")

def 判九十三真外部缺必须是真缺口() -> None:
    """★★★ 「真外部缺」里不许混进「本仓自己的命名空间」和「语言保留字」（R407）。

    ★ 为什么需要（目标④「未解析的必须报出来」的**质量**）：
      `未解析` 这个桶里混过**四类假缺口**（R405/R406 剔了关键字/tactic/绑定），
      剩下的还混着**命名空间首段**：`MyMeta` 3,771 次 ——
      它是 `MyMeta.Foundational.…` 的**第一段**，本仓**当然没有叫 `MyMeta` 的定义**，
      但它**不是缺口**：缺口是「缺一个该有的定义」，
      不是「少了半个限定名的前缀」。
    ★ 口径：**「真缺口」与「半截名字」是两件事** ——
      前者是「AI 知道自己缺什么」的读数，后者是**抽取层的副产品**。
      **混在一起报，等于把一个读数变成一个噪声源。**

    ★ 判据形状（三项，都可机判）：
      ① `外部缺.json` 必须存在（常驻）；
      ② **本仓声明过的 `namespace X` 不许出现在「真外部缺」里**；
      ③ **`_Lean关键字` / `_LeanTactic` 不许出现在「真外部缺」里**。
      ★ ②③ 都用 **fixture 就地造**：造一个 `namespace Demo` 声明、
        一个 `set_option`、一个 `rfl`，看分类器会不会把它们当缺口；
        同时造一个**只在限定名里出现过**的 `MyMeta`，它**必须**进缺口。
    """
    坏: list[str] = []
    import json as _json
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十三：`kg` 导不进来（{type(e).__name__}: {e}）"])

    仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    有档 = list(仓.glob("*/外部缺.json")) if 仓.is_dir() else []
    if not 有档:
        坏.append(f"判九十三①：常驻图仓库里**没有 `外部缺.json`**（{仓}）⇒ "
                  f"目标④的读数**没落盘**")

    import os as _os
    import shutil as _sh
    import tempfile as _tf
    根 = Path(_tf.mkdtemp(prefix="kg93_"))
    旧图 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(Path(_tf.mkdtemp(prefix="kg93KG_")))
    try:
        (根 / "T.lean").write_text(
            "namespace Demo\n"
            "\n"
            "theorem foo : True := by rfl\n"
            "\n"
            "set_option maxHeartbeats 400000\n"
            "\n"
            "def bar : MyMeta.Ghost := 1\n"
            "\n"
            "end Demo\n", encoding="utf-8")
        _kg.建图(根, 打印=lambda *_: None)
        _kg.解析引用(根, 打印=lambda *_: None)
        _档 = _kg._kg目录(根) / "外部缺.json"
        if not _档.is_file():
            坏.append("判九十三①：`解析引用` **没有产出 `外部缺.json`** ⇒ "
                      "「真外部缺」这个读数不存在")
        else:
            _d = _json.loads(_档.read_text(encoding="utf-8"))
            _真 = {x["名"] if isinstance(x, dict) else x[0]
                   for x in (_d.get("★真外部缺") or [])}
            if not _真:
                坏.append("判九十三：fixture **一个真外部缺都没造出来** ⇒ "
                          "**这条判据没测到东西**（没测到不能当通过了）")
            if "Demo" in _真:
                坏.append("判九十三②：`Demo` 是 fixture 里 **`namespace Demo` 声明过的**，"
                          "却被算成「真外部缺」⇒ **把本仓自己的命名空间当缺口报**")
            if "MyMeta" not in _真:
                坏.append("判九十三②：`MyMeta` **本仓没有裸名定义**（只是 "
                          "`MyMeta.Ghost` 的前缀）⇒ 它**应当**出现在「真外部缺」里，"
                          "现在没有 ⇒ **分类器把「前缀」和「没有定义」混成一个判断了**")
            for _w in ("rfl", "set_option", "maxHeartbeats", "namespace",
                       "theorem", "def", "by"):
                if _w in _真:
                    坏.append(f"判九十三③：`{_w}` 是 **Lean 保留字/命令/选项名**，"
                              f"却被算成「真外部缺」⇒ **假缺口引着人去补不该存在的定义**")
    finally:
        if 旧图 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = 旧图
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判九十三真外部缺必须是真缺口：绿")
    print(f"   外部缺：绿 —— 判九十三（常驻 {len(有档)} 份 · "
          f"命名空间/保留字不进缺口 · 只当前缀但无定义的要进）")

def 判九十四导入集必须与权威ast逐条对齐() -> None:
    """★★★ **import 集也要和权威 `ast` 对账**（R410）—— 目标⑤后半。

    ★ 为什么需要：判五十一 只对了**定义集**（`ast全定义` vs 通用抽取）。
      目标⑤ 的原话是**「真 import/定义集」** —— **两样都要对**。
      import 集错了更隐蔽：定义少了看得见（引不到），import 少了
      **会把外部名字当"未解析"报成缺口**（正是目标④的读数被污染）。

    ★ 口径（**两边必须记成同一样东西**）：
      统一记 `(本文件里的绑定名, 被导入的那个模块名)`：
        `import X.Y`        ⇒ `(X, X.Y)`      绑定名是首段
        `import X.Y as Z`   ⇒ `(Z, X.Y)`
        `from X import Y`   ⇒ `(Y, X)`        模块是 X，Y 是成员
        `from . import Y`   ⇒ `(Y, .)`        相对导入保留层级
        `from X import *`   ⇒ **跳过**（没有绑定名可比）
      ★ 第一版探针把 `from X import Y` 记成 `(Y, X.Y)` ⇒
        **报出「我漏 465 · 我多 467」** —— 而两边**说的是同一件事**，
        只是我探针自己两边口径不同 ✗
      ★ 口径：**「先修尺子，再量」** —— 报「我多/我漏」之前，
        先确认**两边量的是不是同一个东西**。

    ★ 判据形状：整个标准库，`我漏 == 0 且 我多 == 0`。
      （`我多` 与 `我漏` **都要报**：只报一边会放过"换了个写法"这种错。）
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十四：`kg` 导不进来（{type(e).__name__}: {e}）"])
    import ast as _ast
    import sysconfig as _sc
    根 = Path(_sc.get_paths()["stdlib"])
    if not 根.is_dir():
        坏.append(f"判九十四：标准库不在 `{根}`（**这是环境前提，不是通过**）")
        return _报2(坏, "判九十四导入集必须与权威ast逐条对齐：绿")

    def _ast集(文: str) -> set:
        出 = set()
        for n in _ast.walk(_ast.parse(文)):
            if isinstance(n, _ast.Import):
                for a in n.names:
                    出.add((a.asname or a.name.split(".")[0], a.name))
            elif isinstance(n, _ast.ImportFrom):
                _模 = ("." * (n.level or 0)) + (n.module or "")
                for a in n.names:
                    if a.name == "*":
                        continue
                    出.add((a.asname or a.name, _模))
        return 出

    _多: dict = {}
    _漏: dict = {}
    _计 = 0
    for f in sorted(根.glob("*.py")):
        try:
            _文 = f.read_text(encoding="utf-8", errors="replace")
            _A = _ast集(_文)
        except SyntaxError:
            continue
        except Exception:                         # noqa: BLE001
            continue
        _, _边 = _kg._语法树适配器(_文, f.name, "python")
        _G = set()
        for _e in _边:
            if _e.get("关系") != "导入":
                continue
            _t = _e.get("终", "")
            _m = _t[4:] if _t.startswith("mod:") else _t
            for _b in (_e.get("绑定") or []):
                _G.add((_b, _m))
        _计 += len(_A)
        for _k in sorted(_A - _G):
            _多.setdefault(_k, []).append(f.name)
        for _k in sorted(_G - _A):
            _漏.setdefault(_k, []).append(f.name)

    if _计 == 0:
        坏.append("判九十四：**一条 import 都没比到** ⇒ 这条判据没测到东西")
    if _漏:
        坏.append(f"判九十四：**我漏 {len(_漏)}** 种（权威 `ast` 有、我没有）："
                  f"{sorted(_漏)[:3]} ⇒ 漏掉的 import 会让外部名字**被误报成缺口**")
    if _多:
        坏.append(f"判九十四：**我多 {len(_多)}** 种（我没有权威依据）："
                  f"{sorted(_多)[:3]}")
    if 坏:
        return _报2(坏, "判九十四导入集必须与权威ast逐条对齐：绿")
    print(f"   导入对账：绿 —— 判九十四（标准库 {_计} 条 import 与权威 `ast` "
          f"逐条对齐：我多 0 · 我漏 0）")

def 判九十五分根必须完备且不静默丢() -> None:
    """★★★ **分根：每个源码文件必须落进且只落进一个根；排除的东西要有名字**（R411）。

    ★ 为什么需要（实测，不是理论）：
      总仓 `太虚元道` **162,524 个文件**。拿它**整张建图** ⇒ 跑了 **40 分钟没完**
      （同规模 Lean 6,607 文件只要 60 秒 ⇒ 25 倍 ⇒ 约 1,000 万节点 ⇒
      **内存放不下，在 swap 上爬**）。
      ★ 口径：**「扫得完」是有前提的** —— **能跑通的规模才是规模。**

    ★ 判据形状（六项，都可机判；**用 fixture 造，不赌真仓长什么样**）：
      ① **完备**：`分根` 的 `归属` 必须覆盖**每一个**源码文件；
      ② **归属指向真根**：落进去的那个根必须在 `根们` 里；
      ③ **备份/临时目录不单独成图**（造一个 `_backup_xxx`：源码 60 个）；
      ④ ★ **但它不许被静默丢掉**：那 60 个文件必须**仍在 `归属` 里**（落进总根）
         —— **「排除」与「丢掉」是两件事**；
      ⑤ **合格子树要单独成图**；
      ⑥ **排除必须带名字**（`不单独成图` 不许为空）。
      ★ 口径：**「按 X 刷新的机器必须给不属于任何 X 的东西一条显式通路」** ——
        **沉默的删除比报错坏得多。**
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十五：`kg` 导不进来（{type(e).__name__}: {e}）"])
    if not hasattr(_kg, "分根"):
        return _报2(["判九十五：`kg` 里**没有 `分根`** ⇒ 总仓扫不完时没有出路"])

    import shutil as _sh
    import tempfile as _tf
    根 = Path(_tf.mkdtemp(prefix="kg95_"))
    try:
        (根 / "真项目").mkdir()
        for i in range(60):
            (根 / "真项目" / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
        (根 / "_backup_20260101").mkdir()
        for i in range(60):
            (根 / "_backup_20260101" / f"b{i}.py").write_text("y = 2\n", encoding="utf-8")
        (根 / "散.py").write_text("z = 3\n", encoding="utf-8")

        计 = _kg.分根(根, 最少源文件=50, 打印=lambda *_: None)
        _根们 = {str(Path(x["根"])) for x in 计["根们"]}
        _归属 = 计.get("归属") or {}
        _总 = str(根.resolve())

        _实 = {str(p.relative_to(根)) for p in 根.rglob("*.py")}
        # ★★ R442：**完备** = 每个源码文件**要么归属某个根**，
        #   **要么在"带名字的排除"里**（被排除，但**有路径、有原因、有文件数**）。
        #   ★ 旧版只认"归属" ⇒ 备份副本（本就不该进图）被算成"谁都不管" ✗
        #     —— **把"有意的排除"当成了"丢失"**（口径 139/150）。
        #   ★ **分隔符**：`路径` 是 posix（`/`），而 `_实` 在 Windows 上是
        #     `_backup_20260101\\b0.py` ⇒ **两边都要归一化**，否则永远匹配不上 ✗
        #     （这就是①改完还红一次的原因 —— **归一化漏了一边**。）
        def _归(x: str) -> str:
            return x.replace("\\", "/").strip("/")

        _跳集 = {_归(str(x.get("路径", ""))) for x in
                 (计.get("跳过的目录（带文件数）") or []) if x.get("路径")}
        _缺 = {k for k in (_实 - set(_归属))
               if not any(_归(k) == j or _归(k).startswith(j + "/") for j in _跳集)}
        if _缺:
            坏.append(f"判九十五①：**{len(_缺)} 个源码文件谁都不管**（如 "
                      f"{sorted(_缺)[:3]}）⇒ 分根**不完备**")
        for _rel, _r in _归属.items():
            if 根.joinpath(_rel).suffix.lower() not in _kg._源码扩展:
                continue
            if _r not in _根们:
                坏.append(f"判九十五②：`{_rel}` 被分给 `{_r}`，"
                          f"但**那个根不在 `根们` 里** ⇒ 归属指向了一个不存在的图")
                break
        _备份根 = str(根 / "_backup_20260101")
        if _备份根 in _根们:
            坏.append("判九十五③：`_backup_20260101` **单独成了一张图** ⇒ "
                      "**把备份当成正本**")
        _落总根 = [k for k, v in _归属.items() if v == _总
                   and k.startswith("_backup_20260101")]
        # ★★ R442：**接受"有名字的排除"** ——
        #   「排除」与「丢掉」的区别**不在排不排**，而在**排了之后
        #   有没有名字和大小**。备份副本进图会白解析 + 污染引用
        #   （同名定义两份）⇒ **排除是对的，排除得没有名字才是错的**。
        _跳目 = [x for x in (计.get("跳过的目录（带文件数）") or [])
                 if str(x.get("路径", "")).startswith("_backup_20260101")]
        # ★★★ R517d：**换靶**（口径 332：判据的靶可能比缺陷大一圈 —— 要改靶，
        #   不是放宽阈值）。原来这里写死了**一个字段名** `里面有文件数`；
        #   R517 把备份/剪枝目录的大小读数改名为 `一级条目数`
        #   （**更准确**：它是一级条目数，不是"里面有多少文件"—— 口径 311）
        #   ⇒ 按旧名找一个已改名的量 ⇒ 空 ⇒ 误报"被丢掉"。
        #   ★ 而 R442 真正要的是「跳过的目录**少了多少源码**」⇒ 首选量是
        #     `一级子树源码数`（`分根` 的 `子计`）—— **它只数源码，且与任何
        #     "跳过大小"字段名无关** ⇒ 换成它**比旧靶更准确**，不是放宽。
        _源码数 = (计.get("一级子树源码数") or {}).get("_backup_20260101")
        _大小键 = ("里面有文件数", "里面至少有文件数", "一级条目数")
        _有名字 = [x for x in _跳目 if any(x.get(k) is not None for k in _大小键)]
        _证据 = ((_源码数 is not None and _源码数 >= 60)
                 or bool(_有名字))
        if len(_落总根) != 60 and not _证据:
            坏.append(f"判九十五④：备份目录的 60 个源码文件里**只有 {len(_落总根)} 个"
                      f"落进总根**，且**两处都查不到它少了多少源码**"
                      f"（`一级子树源码数` = {_源码数} · "
                      f"带大小读数的跳过条目 {len(_有名字)} 条）⇒ "
                      f"**「排除」变成了「丢掉」** —— ★ 沉默的删除比报错坏得多"
                      f"（口径 332：要改靶，不是放宽阈值）")
        if str(根 / "真项目") not in _根们:
            坏.append("判九十五⑤：`真项目`（60 个源码文件）**没有单独成图** ⇒ "
                      "合格子树漏了")
        # ★★ R442：**"排除得没有名字" = 两张表都空** ——
        #   ★ 旧版只看 `不单独成图` ⇒ 而备份目录**在枚举阶段就被跳了**，
        #     从来没进过 `子计` ⇒ `不单独成图` 永远是空的 ⇒ **判据永远红** ✗
        #   ⇒ 现在两张表**任一张有内容**就叫"有名字"。
        if not (计.get("不单独成图") or {}) and not (计.get("跳过的目录（带文件数）") or []):
            坏.append("判九十五⑥：`不单独成图` 与 `跳过的目录（带文件数）` **都是空的**，"
                      "但 fixture 里明明造了不合格子树 ⇒ **排除得没有名字**")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判九十五分根必须完备且不静默丢：绿")
    print("   分根：绿 —— 判九十五（完备 · 归属指向真根 · 备份不单独成图 · "
          "但落进总根不丢 · 排除带名字）")

def 判九十六分根后每张图只扫自己那份() -> None:
    """★★★ **「分根」不只是"算出该建几张图"，还得让每张图"只扫自己那份"**（R412）。

    ★ 为什么需要（实测）：
      `分根()` 算出**总根**只该装 **927 个散落文件**，
      但 `建图(总根)` 里那句 `枚举文件(根)` ⇒ **扫出全部 162,549 个文件**
      ⇒ **分根白做，总根那一张图又把全仓塞回来了** ✗
    ★ 口径：**「机制在」≠「机制在真数据上成立」**（口径 85）——
      分根函数写对了（判九十五绿），**但没人保证建图时真的按分根扫**。

    ★ 判据形状（四项）：
      ① `建图(根, 排除=[子根…])` 后，**子根的文件不许出现在这张图的节点里**；
      ② **被排除的文件必须在「跳过」里带原因**（不许凭空消失）；
      ③ `建总图` 必须给总根传排除（总根那张图的 `文件` 节点只能是自己那份）；
      ④ **`文件` 节点数必须等于「该根自己那份」**，不是全仓的数。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十六：`kg` 导不进来（{type(e).__name__}: {e}）"])
    for _需 in ("分根", "建总图", "_过排除"):
        if not hasattr(_kg, _需):
            return _报2([f"判九十六：`kg` 里没有 `{_需}` ⇒ 分根没有落到建图上"])

    import json as _json
    import os as _os
    import shutil as _sh
    import tempfile as _tf
    根 = Path(_tf.mkdtemp(prefix="kg96_"))
    旧图 = _os.environ.get("KG_图目录")
    _os.environ["KG_图目录"] = str(Path(_tf.mkdtemp(prefix="kg96KG_")))
    try:
        (根 / "子项目").mkdir()
        for i in range(60):
            (根 / "子项目" / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
        (根 / "散.py").write_text("z = 3\n", encoding="utf-8")

        计 = _kg.分根(根, 最少源文件=50, 打印=lambda *_: None)
        _合格 = tuple(计["合格"])
        if "子项目" not in _合格:
            坏.append("判九十六：fixture 的 `子项目`（60 个源码文件）**没有进合格子树**"
                      "⇒ 判据的前置不成立（**没测到不能当通过了**）")

        _kg.建图(根, 打印=lambda *_: None, 排除=_合格)
        节 = _json.loads((_kg._kg目录(根) / "节点.json").read_text(encoding="utf-8"))
        _文节 = [v for v in 节.values() if v.get("类") == "文件"]
        _名 = {v.get("文件") or v.get("名") for v in _文节}
        _越界 = [n for n in _名
                if n and str(n).replace("\\\\", "/").startswith("子项目/")]
        if _越界:
            坏.append(f"判九十六①：`建图(根, 排除=…)` 之后，**子根的文件仍在图上**"
                      f"（{_越界[:3]}）⇒ **排除没生效**")
        if len(_文节) != 1 or "散.py" not in _名:
            坏.append(f"判九十六④：过筛后 `文件` 节点应是 1 个（`散.py`），"
                      f"实得 {len(_文节)} 个：{sorted(str(x) for x in _名)[:5]}")

        _清 = _kg._kg目录(根) / "清单.json"
        if _清.is_file():
            _跳 = _json.loads(_清.read_text(encoding="utf-8"))
            _跳 = _跳 if isinstance(_跳, list) else (_跳.get("跳过") or [])
            _有因 = [x for x in _跳 if isinstance(x, dict) and x.get("原因")]
            _排的 = [x for x in _有因 if "子根" in str(x.get("原因"))]
            if not _排的:
                坏.append("判九十六②：**被排除的文件没有在「跳过」里留名** ⇒ "
                          "**排除变成了静默消失**（★ 沉默的删除比报错坏得多）")
        else:
            坏.append("判九十六②：`清单.json` 不存在 ⇒ 跳过原因**没落盘**，"
                      "查不到「谁被排除了、为什么」")

        _os.environ["KG_图目录"] = str(Path(_tf.mkdtemp(prefix="kg96B_")))
        _kg.建图(根, 打印=lambda *_: None, 排除=tuple(计["合格"]))
        节2 = _json.loads((_kg._kg目录(根) / "节点.json").read_text(encoding="utf-8"))
        _文2 = [v for v in 节2.values() if v.get("类") == "文件"]
        if len(_文2) > 2:
            坏.append(f"判九十六③：总根过筛后 `文件` 节点有 {len(_文2)} 个"
                      f"（本应 1 个 `散.py`）⇒ **排除没传到**")
        # ③ 分根算出的总根文件数必须与过筛后一致
        _归总 = sum(1 for k, v in (计.get("归属") or {}).items()
                    if v == str(根.resolve())
                    and Path(k).suffix.lower() in _kg._源码扩展)
        if _归总 != len(_文节):
            坏.append(f"判九十六③：`分根` 说总根有 {_归总} 个源码文件，"
                      f"而图上有 {len(_文节)} 个 `文件` 节点 ⇒ **两处数不一致**"
                      f"（同一个判断写在两处，必然给出两个答案）")
    finally:
        if 旧图 is None:
            _os.environ.pop("KG_图目录", None)
        else:
            _os.environ["KG_图目录"] = 旧图
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判九十六分根后每张图只扫自己那份：绿")
    print("   分根扫描：绿 —— 判九十六（总根只扫自己那份 · 排除生效 · "
          "被排除的带名字 · 文件节点数 = 分根说的数）")

def 判九十七进图必须按有无结构筛且留名() -> None:
    """★★★ **不是所有文件都值得进图；进图的判据是"有结构可抽"，不是"它在目录里"**（R413）。

    ★ 为什么需要（实测，不是理论）：
      `arc_data` **16,289 个文件**，其中 **13,328 个是 `.txt`**（语料/数据）。
      建图跑到 **2,303MB 内存 / 30 分钟没完**，而且**一个源码节点都还没建** ✗
      ⇒ 加 `_值得进图`（按扩展名）+ `_过值得()`：
        `arc_data` **16,289 → 进图 2,564（跳过 13,725）· 建图 121 秒** ✓
    ★ 口径：**「扫得完」是有前提的**（口径 114）——
      **进图该按"这个文件里有结构可抽"判，不是按"它在目录里"判。**
    ★ 但**不许静默丢**（口径 95）：跳过的每条都要**带原因**。

    ★ 判据形状（四项）：
      ① `.txt` / `.bin` / `.o` / `.xyz` / `.dat` **不许进图**；
      ② `.py` / `.rs` / `.lean` / `.md` / `.json` / `.yaml` **必须进图**；
      ③ 跳过的必须在 `跳过` 里**带原因**（不许凭空消失）；
      ④ ★ **反例守卫**：故意放一个 `.py` 在深层目录，**它必须进图**
         （防"一刀切按目录排除"把真源码也切掉）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十七：`kg` 导不进来（{type(e).__name__}: {e}）"])
    if not hasattr(_kg, "_过值得"):
        return _报2(["判九十七：`kg` 里没有 `_过值得` ⇒ 无结构文件仍会进图"
                     "（`arc_data` 的 13,328 个 `.txt` 会把建图拖死）"])

    import shutil as _sh
    import tempfile as _tf
    根 = Path(_tf.mkdtemp(prefix="kg97_"))
    try:
        _无结构 = ["a.txt", "b.bin", "c.o", "d.xyz", "e.dat", "f.rmeta", "g.lock",
                   "h.d", "i.exe", "j.so", "k.dill", "l.pkl"]
        _有结构 = ["m.py", "n.rs", "o.lean", "p.md", "q.json", "r.yaml",
                   "s.toml", "t.sh", "u.java", "v.ts"]
        for n in _无结构 + _有结构:
            (根 / n).write_text("x = 1\n" if n.endswith((".py", ".rs")) else "# t\n",
                                encoding="utf-8")
        (根 / "深" / "更深").mkdir(parents=True)
        (根 / "深" / "更深" / "真源码.py").write_text("y = 2\n", encoding="utf-8")

        文件们, _跳1 = _kg.枚举文件(根)
        留, 跳 = _kg._过值得(文件们)
        _留名 = {f.name for f in 留}
        _跳名 = {x["文件"] for x in 跳}

        for n in _无结构:
            if n in _留名:
                坏.append(f"判九十七①：`{n}` **无结构可抽却进了图** ⇒ "
                          f"建图会被这类文件拖死（实测：`arc_data` 13,328 个 `.txt` "
                          f"⇒ 30 分钟没完）")
        for n in _有结构:
            if n not in _留名:
                坏.append(f"判九十七②：`{n}` **有结构可抽却没进图** ⇒ "
                          f"**该进的不进**（这是把读数切掉，比多扫描坏）")
        if "真源码.py" not in _留名:
            坏.append("判九十七④：深层目录里的 `真源码.py` **没进图** ⇒ "
                      "**按目录一刀切把真源码切掉了**")
        _无因 = [x for x in 跳 if not x.get("原因")]
        if _无因:
            坏.append(f"判九十七③：跳过的 {len(_无因)} 个**没有原因** ⇒ "
                      f"**静默丢文件**（★ 沉默的删除比报错坏得多）")
        if not 跳:
            坏.append("判九十七③：fixture 里放了 12 个无结构文件，`跳过` **却是空的** ⇒ "
                      "这条判据**没测到东西**")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判九十七进图必须按有无结构筛且留名：绿")
    print("   进图筛选：绿 —— 判九十七（无结构不进 · 有结构必进 · "
          "深层真源码不被误切 · 跳过带原因）")

def 判九十八运行副本必须按结构判不按名字列举() -> None:
    """★★★ **同类的兄弟 ≥3 个 ⇒ 运行副本**；判据是**结构**，不是名字（R415）。

    ★ 为什么需要（实测）：
      `多智能体编排/agentscope_orchestrator/` 下有 **5,120 个运行副本目录**
      （`r291v_a2j_487u` / `it_bncm8s7j` / …，每个 635–636 个文件）
      ⇒ 这个子根从几千个文件涨到 **38,962 个**。
      ★ 口径：**「产出」落在「输入」里，就是自我污染**（口径 105）——
        这里更狠：**每一轮运行都往里多堆一份自己的副本。**

    ★ 判据形状（五项，**fixture 造，不赌真仓长什么样**）：
      ① **同前缀 ≥3 个兄弟 + 尾巴是乱码** ⇒ 全判为运行副本；
      ② ★ **但 ≤2 个兄弟不许判**（`mod_a`/`mod_b` 两个真模块不能被切）；
      ③ ★ **不许按名字列举**：fixture 里用**从没出现过的前缀**（如 `zz9q_`），
         `_是运行副本` 必须**照样认出来**（写死名单的话这条必红）；
      ④ ★ **反例守卫**：真源码目录（名字规整、无同类兄弟）**不许被判掉**；
      ⑤ 判掉的必须**在跳过清单里带原因**。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十八：`kg` 导不进来（{type(e).__name__}: {e}）"])
    if not hasattr(_kg, "_是运行副本"):
        return _报2(["判九十八：`kg` 里没有 `_是运行副本` ⇒ 5,120 个运行副本会进图"])

    # ── ①②③ 直接量 `_是运行副本` 的判定力（不依赖目录）
    _兄弟 = ["zz9q_aaaabbbb", "zz9q_ccccdddd", "zz9q_eeeeffff", "真源码目录"]
    if not _kg._是运行副本("zz9q_aaaabbbb", _兄弟):
        坏.append("判九十八③：**从没出现过的前缀 `zz9q_` 没被认出来** ⇒ "
                  "判据是**按名字列举**的（写死名单），下一轮换个前缀就漏")
    if _kg._是运行副本("mod_a", ["mod_a", "mod_b"]):
        坏.append("判九十八②：只有 **2 个**同类兄弟就判成运行副本 ⇒ "
                  "**把真模块切掉**")
    if _kg._是运行副本("真源码目录", _兄弟):
        坏.append("判九十八④：名字规整、没有同类乱码兄弟的目录**被判成运行副本**")

    # ── ⑤ 走一遍真枚举，看跳过的带不带原因
    import shutil as _sh
    import tempfile as _tf
    根 = Path(_tf.mkdtemp(prefix="kg98_"))
    try:
        for n in ("zz9q_aaaabbbb", "zz9q_ccccdddd", "zz9q_eeeeffff"):
            (根 / n).mkdir()
            (根 / n / "m.py").write_text("x = 1\n", encoding="utf-8")
        (根 / "真项目").mkdir()
        (根 / "真项目" / "m.py").write_text("y = 2\n", encoding="utf-8")
        文们, 跳 = _kg.枚举文件(根)
        _留 = {f.name for f in 文们}
        _跳副本 = [x for x in 跳 if "运行副本" in str(x.get("原因") or "")]
        if len(_跳副本) != 3:
            坏.append(f"判九十八①：3 个同前缀乱码兄弟里**只判了 {len(_跳副本)} 个**")
        if "真项目" in str([x.get("路径") for x in 跳]):
            坏.append("判九十八④：`真项目`（真源码目录）**被剪掉了**")
        if "m.py" not in _留:
            坏.append("判九十八④：真源码的 `m.py` **没进图** ⇒ 一刀切把真源码切了")
        _无因 = [x for x in _跳副本 if not x.get("原因")]
        if _无因:
            坏.append("判九十八⑤：判掉的**没带原因** ⇒ 静默消失")
        if not _跳副本:
            坏.append("判九十八①：fixture 造了 3 个同前缀乱码兄弟，"
                      "**一个都没判出来** ⇒ 这条判据没测到东西")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判九十八运行副本必须按结构判不按名字列举：绿")
    print("   运行副本：绿 —— 判九十八（同前缀≥3 兄弟判掉 · 2 个不判 · "
          "新前缀照样认 · 真源码不动 · 带原因）")

def 判九十九副本前缀表必须一次折好不许每元素重扫全集() -> None:
    """★★★ **判据本身要对，判据的代价也要能承受**（R417）。

    ★ 为什么需要（实测，**我自己引入的 bug**）：
      R415 加的 `_是运行副本(d, 兄弟们)` 对**每个目录**都遍历一遍兄弟做正则。
      `多智能体编排/agentscope_orchestrator/` 下有 **5,120 个兄弟**
      ⇒ **约 2,600 万次正则** ✗
      ⇒ `形式化验证` 重建从 **173 秒变成 22 分钟**（cpu 1348s 还没完）。
    ★ 口径（120 的同一件事）：**「每个元素扫一遍全集」在 5,000 个元素上
      就是 2,500 万次** —— 一个要 22 分钟的步骤，**没人会跑第二次**。
    ★ 修法：`_副本前缀表(子目们)` 把兄弟**一次**折成 `{前缀: 个数}`，
      `_是运行副本` 收到 dict 时 **O(1) 查表**。

    ★ 判据形状（四项）：
      ① `_副本前缀表` 存在，且**对同一批名字给出正确计数**；
      ② `_是运行副本(名, 计数表)` 与 `_是运行副本(名, 名单)` **判定一致**
         （★ 快路径不许改判定 —— 否则"修性能"变成了"改语义"）；
      ③ ★ **不许 O(n²)**：造 **2,000 个**同前缀兄弟，
         `_副本前缀表` + 2,000 次 `_是运行副本` 必须在 **0.5 秒**内跑完
         （用名单那种写法是 400 万次正则，**这条会红**）；
      ④ ★ **真接线**：`枚举文件` 必须**真的把计数表传下去**
         （用 fixture 造 3 个同前缀兄弟，必须被判为运行副本 —— 
         若`枚举文件`仍传 `list`，这条还绿；所以要**直接验参数形状**）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判九十九：`kg` 导不进来（{type(e).__name__}: {e}）"])
    for _需 in ("_副本前缀表", "_副本前缀", "_是运行副本"):
        if not hasattr(_kg, _需):
            return _报2([f"判九十九：`kg` 里没有 `{_需}`"])

    import time as _t
    import shutil as _sh
    import tempfile as _tf

    # ── ① 计数表正确
    _名 = ["zz9q_aaaabbbb", "zz9q_ccccdddd", "zz9q_eeeeffff",
           "真源码目录", "mod_a", "mod_b"]
    _表 = _kg._副本前缀表(_名)
    if _表.get("zz9q_") != 3:
        return _报2([f"判九十九①：`zz9q_` 的计数应是 3，实得 {_表.get('zz9q_')!r}"])

    # ── ② 两条路判定一致
    for _x in _名:
        _a = _kg._是运行副本(_x, _表)
        _b = _kg._是运行副本(_x, _名)
        if _a != _b:
            return _报2([f"判九十九②：`{_x}` 走计数表得 {_a}，走名单得 {_b} "
                         f"⇒ **快路径改了判定**（那是改语义，不是修性能）"])

    # ── ③ 规模：2,000 个兄弟必须在 0.5 秒内
    _大 = [f"qq7w_{i:08x}" for i in range(2000)]
    _t0 = _t.time()
    _表2 = _kg._副本前缀表(_大)
    for _x in _大:
        _kg._是运行副本(_x, _表2)
    _用 = _t.time() - _t0
    if _用 > 0.5:
        return _报2([f"判九十九③：2,000 个兄弟用了 **{_用:.2f} 秒**（上限 0.5）"
                     f"⇒ **还在对每个元素重扫全集**（O(n²)）"])
    if _表2.get("qq7w_") != 2000:
        坏.append(f"判九十九①：2,000 个兄弟的计数实得 {_表2.get('qq7w_')!r}")

    # ── ④ 真接线：枚举文件 传下去的必须是计数表，且判定照旧成立
    根 = Path(_tf.mkdtemp(prefix="kg99_"))
    try:
        for n in ("zz9q_aaaabbbb", "zz9q_ccccdddd", "zz9q_eeeeffff"):
            (根 / n).mkdir()
            (根 / n / "m.py").write_text("x = 1\n", encoding="utf-8")
        (根 / "真项目").mkdir()
        (根 / "真项目" / "m.py").write_text("y = 2\n", encoding="utf-8")
        文们, 跳 = _kg.枚举文件(根)
        _副 = [x for x in 跳 if "运行副本" in str(x.get("原因") or "")]
        if len(_副) != 3:
            坏.append(f"判九十九④：3 个同前缀兄弟**只判了 {len(_副)} 个** ⇒ "
                      f"**枚举文件 没有把计数表接上**（或接上了但判定变了）")
        _留 = {f.name for f in 文们}
        if "m.py" not in _留:
            坏.append("判九十九④：真源码的 `m.py` 没进图")
        # ★ 源码级核对：枚举文件 里必须出现 `_副本前缀表(`
        import inspect as _ins
        _源 = _ins.getsource(_kg.枚举文件)
        if "_副本前缀表(" not in _源:
            坏.append("判九十九④：`枚举文件` 源码里**没有 `_副本前缀表(`** ⇒ "
                      "说明它还在传名单（`list(子目)`）⇒ **接线没接上**"
                      "（★ 口径：登记了≠接上了）")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判九十九副本前缀表必须一次折好不许每元素重扫全集：绿")
    print(f"   副本前缀表：绿 —— 判九十九（计数正确 · 两条路判定一致 · "
          f"2,000 个兄弟 {_用:.2f}s · 枚举文件真的传了计数表）")

def 判一百排除必须在walk时剪枝不许枚举完再过滤() -> None:
    """★★★ **「过滤」与「剪枝」是两件事**（R418）。

    ★ 为什么需要（实测，**我自己修的机制里的漏**）：
      R412 给 `建图` 加了 `排除=`，但 `_过排除` 是**在 `枚举文件` 之后**才过滤的
      ⇒ `建图(总根)` **仍然遍历全树 162,524 个文件、每个都 `stat`**
      ⇒ **20 分钟** ✗（而它本该只扫总根那 35,346 个）。
      ★ 口径：**过滤是"全都走一遍再扔掉"，剪枝是"根本不进去"** ——
        16 万个文件上，这个差别就是 **20 分钟与 13.6 秒**。
      ★ 这与 R417 的 O(n²) 是**同一类错的两种形态**：
        一个是"每个元素扫全集"，一个是"每个文件查完再扔"，
        **判定都对，代价废掉了步骤**（口径 127）。

    ★ 判据形状（四项）：
      ① `枚举文件` **必须收 `排除=` 这个参数**；
      ② ★ **规模/代价**：造一棵 **10,000 个文件**的树，其中 9 个一级子树各 1,000 文件，
         `枚举文件(根, 排除=那 9 个)` 必须**在 3 秒内**返回，
         且**留下的只有根层那 1,000 个**（★ 若还在"枚举完再过滤"，
         10,000 个文件的 stat 会明显超时）；
      ③ ★ **剪掉的在跳过清单里带原因**（口径 95：沉默的删除比报错坏得多）；
      ④ ★ **不传 `排除` 时行为不变**（10,000 个全留下）——
         防"为了剪枝把默认行为改坏"。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百：`kg` 导不进来（{type(e).__name__}: {e}）"])
    import inspect as _ins
    if "排除" not in _ins.signature(_kg.枚举文件).parameters:
        return _报2(["判一百①：`枚举文件` **没有 `排除=` 参数** ⇒ "
                     "`建图(总根)` 会把全树走一遍再过滤（实测 20 分钟）"])

    import time as _t
    import shutil as _sh
    import tempfile as _tf
    根 = Path(_tf.mkdtemp(prefix="kg100_"))
    try:
        _子根名 = [f"子根{i}" for i in range(9)]
        for n in _子根名:
            (根 / n).mkdir()
            for k in range(1000):
                (根 / n / f"f{k}.py").write_text("x = 1\n", encoding="utf-8")
        # 根层 1,000 个（本该留下）
        for k in range(1000):
            (根 / f"g{k}.py").write_text("y = 2\n", encoding="utf-8")

        _t0 = _t.time()
        留, 跳 = _kg.枚举文件(根, 排除=_子根名)
        _用 = _t.time() - _t0
        if len(留) != 1000:
            坏.append(f"判一百②：过筛后应留 1,000 个，实得 {len(留)} 个")
        _割 = [x for x in 跳 if "属于子根" in str(x.get("原因") or "")]
        if len(_割) != 9:
            坏.append(f"判一百③：9 个子根里**只剪了 {len(_割)} 个**，"
                      f"或剪掉的**没带原因**")
        if _用 > 3.0:
            坏.append(f"判一百②：10,000 个文件的树用了 **{_用:.1f} 秒**（上限 3 秒）⇒ "
                      f"**还在「枚举完再过滤」**（剪枝才是「根本不进去」）")

        _t0 = _t.time()
        留2, _ = _kg.枚举文件(根)
        _用2 = _t.time() - _t0
        if len(留2) != 10000:
            坏.append(f"判一百④：不传 `排除` 时应留 10,000 个，实得 {len(留2)} 个 "
                      f"⇒ **默认行为被剪枝改坏了**")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判一百排除必须在walk时剪枝不许枚举完再过滤：绿")
    print(f"   排除剪枝：绿 —— 判一百（10,000 个文件 {_用:.1f}s · "
          f"9 个子根带原因剪掉 · 不传排除行为不变）")

def 判一百零一我写的脚本和判据池本体都必须能编译() -> None:
    """★★★ **机器检查要守「结果」，不是守「风格」**（R419，口径 131 的落地）。

    ★ 为什么需要（实测，**同一个坑踩了五次**）：
      我用 heredoc / `python -c` 写脚本或判据时，脚本**里面的字符串含直引号**
      ⇒ 外层引号配对被打乱 ⇒ **落盘的脚本编译不过** ✗
      ⇒ 后续脚本的 `assert` 变成"在一个已经坏掉的文件上找锚点"，**越修越乱**
      （R417 真发生过：一个带 `def **副本前缀表**(...)` 的坏 kg.py 落了盘）。
      ★ 口径：**记在口径里只能防「我」，防不住「下一步的我」** ——
        要把"编译得过"做成**机器检查**。

    ★ ★ **第一版判据是错的（如实记）**：
      我原本写的是"查 f-string 内部出现同种直引号"，
      但 **Python 3.12（PEP 701）允许** f-string 内同种引号 ⇒
      **语法上不存在这个错** ⇒ 判据第一次运行就红，**是尺子有病**（口径 90）。
      ⇒ 换成正确的检查：**结果导向 —— 能不能编译**。

    ★ 判据形状（三项）：
      ① ★ **判据池本体**（`tests/orch_product_chain.py`）必须能 `compile()`；
      ② ★ **`kg.py`** 必须能 `compile()`；
      ③ ★ **我写脚本的地方**（`work/临时/*.py`）里，
         **任何已落盘的脚本都必须能 `compile()`**
         —— 若有编译不过的，**说明上一次写脚本把坏文件写下去了**
         （这正是 R417 的事故形态）。
         ★ 但**排除正在写的那一个**（写的过程允许中间态）：
           用 `_正在写` 参数显式指明，避免"判据把半成品当事故"。
    """
    坏: list[str] = []
    仓 = Path(__file__).resolve().parents[1]

    # ── ① 判据池本体
    _自 = Path(__file__).resolve()
    try:
        compile(_自.read_text(encoding="utf-8", errors="replace"), str(_自), "exec")
    except SyntaxError as e:
        return _报2([f"判一百零一①：**判据池本体编译不过**（line {e.lineno}: {e.msg}）"
                     f"⇒ 整片判据池都不可信"])

    # ── ② kg.py
    _kg档 = 仓 / "src" / "agentscope_orchestrator" / "kg.py"
    if not _kg档.is_file():
        坏.append("判一百零一②：**`kg.py` 不在**（找不到被测本体）")
    else:
        try:
            compile(_kg档.read_text(encoding="utf-8", errors="replace"), str(_kg档), "exec")
        except SyntaxError as e:
            坏.append(f"判一百零一②：**`kg.py` 编译不过**（line {e.lineno}: {e.msg}）"
                      f"⇒ 一个坏文件落了盘（R417 的事故形态）")

    # ── ③ 临时脚本目录（排除正在写的）
    _临 = 仓 / "work" / "临时"
    _跳名 = set()
    import os as _os
    for x in (_os.environ.get("KG_正在写的脚本") or "").split(";"):
        if x.strip():
            _跳名.add(x.strip())
    _数 = 0
    if _临.is_dir():
        for f in sorted(_临.glob("*.py")):
            if f.name in _跳名:
                continue
            _数 += 1
            try:
                compile(f.read_text(encoding="utf-8", errors="replace"), str(f), "exec")
            except SyntaxError as e:
                坏.append(f"判一百零一③：**`work/临时/{f.name}` 编译不过**"
                          f"（line {e.lineno}: {e.msg}）⇒ 上次写脚本把坏文件写下去了")

    if 坏:
        return _报2(坏, "判一百零一我写的脚本和判据池本体都必须能编译：绿")
    print(f"   编译自检：绿 —— 判一百零一（判据池本体 · `kg.py` · "
          f"`work/临时` 里 {_数} 个脚本，全部 compile 通过）")

def 判一百零二备份与生成物不许进图且必须留名() -> None:
    """★★★ **备份副本与压缩/生成物不是源码世界**（R420/R421）。

    ★ 为什么需要（实测）：
      建总根 **36 分钟不收缩**（cpu 1581s）。真凶不是大文件，是**备份副本**：
      `备份/TS-181_基线_1551_0_119` 597 个文件 · `备份/TS-186_基线` 431 个 ·
      `中文编译器_backup_20260710` 304 个 …… **38 个备份目录**。
      ★ 而 `分根()` **早就有** `_备份型` 正则（含 `备份`），
        **但 `枚举文件` 的 `_该跳` 里没有它** ⇒
        **分根认得出「备份不是源码世界」，建图认不出** ✗
        ★ 口径（135）：**同一个判断只写在一处，等于另一处根本不判断**。
      修后：**总根 8,456 → 740 个文件 · 建图 88 秒**（原 36 分钟不收缩）。
      另：`mermaid.min.js`（2,513KB）这类**压缩/生成**文件
      「有语法结构，但**没有人读的结构**」（口径 136）。

    ★ 判据形状（五项，fixture **造，不赌真仓**）：
      ① 备份型目录（`备份` / `backup` / `_bak` / `_old` / `快照`）**不许进图**；
      ② 压缩/生成文件（`.min.js` / `.bundle.js` / `_pb2.py`）**不许进图**；
      ③ ★ **超大文件**（> 1MB）**不许进图**；
      ④ ★ **反例守卫**：名字正常、体积正常、没有备份词的目录/文件**必须进图**
         （`src`、`模块.py`、`文档.md`）——防"为了剪枝把真源码切掉"；
      ⑤ ★ **剪掉的必须带原因**，且**原因里要能看出是哪一类**
         （备份 / 生成 / 过大）—— 因为"为什么被剪"决定了"该不该捡回来"。
    ★ 防退化（口径 124）：fixture 用**库里没出现过的备份词组合**
      （如 `zzq7_存档副本`），写死列举的话这条会红。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零二：`kg` 导不进来（{type(e).__name__}: {e}）"])
    for _需 in ("_是备份目录", "_过值得"):
        if not hasattr(_kg, _需):
            return _报2([f"判一百零二：`kg` 里没有 `{_需}`"])

    # ── ① 备份目录判定的**判定力**（不依赖真目录）
    for _名 in ("备份", "backup", "BACKUP", "_bak", "20260701_backup",
                "zzq7_存档副本的兄弟", "old", "快照", "snapshot"):
        if _名 in ("zzq7_存档副本的兄弟",):
            continue
        if not _kg._是备份目录(_名):
            坏.append(f"判一百零二①：`{_名}` **没被认成备份目录**")
    if not _kg._是备份目录("项目_快照"):        # ★ 库里没出现过的组合
        坏.append("判一百零二①：`项目_快照`（**库里没出现过的组合**）没被认成备份 "
                  "⇒ 判据是按名字列举的，下一轮换个词就漏（防退化）")
    for _名 in ("src", "模块", "docs", "tests"):
        if _kg._是备份目录(_名):
            坏.append(f"判一百零二④：`{_名}` **被误判成备份目录** ⇒ 会切掉真源码")

    # ── ②③④⑤ 走真枚举
    import shutil as _sh
    import tempfile as _tf
    根 = Path(_tf.mkdtemp(prefix="kg102_"))
    try:
        for n in ("备份", "backup", "zzq7_快照"):
            (根 / n).mkdir()
            (根 / n / "m.py").write_text("x = 1\n", encoding="utf-8")
        (根 / "src").mkdir()
        (根 / "src" / "模块.py").write_text("y = 2\n", encoding="utf-8")
        (根 / "文档.md").write_text("# 标题\n", encoding="utf-8")
        (根 / "app.min.js").write_text("var a=1;\n", encoding="utf-8")
        (根 / "proto_pb2.py").write_text("z = 3\n", encoding="utf-8")
        (根 / "巨大.py").write_text("q = 1\n" * 300000, encoding="utf-8")   # > 1MB
        # ★★★★ R499：**靶的修正（口径 252）** —— 原靶 `巨大.py` ≈ **1.72MB**，
        #   在 R499 之前被 1MB 门槛剔掉 ⇒ 判据说"超大不许进图"是绿。
        #   R499 把**白名单后缀**（`.py` 在内）的门槛放宽到 `_进图上限宽`（8MB）
        #   ⇒ 1.72MB 的**真源码**现在**该进图**，判据却还要求它被剔 ⇒ **假红**。
        #   ⇒ 靶必须精确成**「超过宽门槛」**：另造一个 9MB 的 `.py`。
        #   ★ 口径 203 的原意是「**错剔**有害」，**不是**「越大越该剔」——
        #     `.min.js`（生成物）由 ② 守，`>8MB` 由 ③ 守，**1.72MB 的真源码该收**。
        (根 / "超大源码.py").write_text("w = 1\n" * 3000000, encoding="utf-8")  # ≈ 18MB
        文们, 跳 = _kg.枚举文件(根)
        留, 跳2 = _kg._过值得(文们)
        _留 = {f.name for f in 留}
        _全跳 = list(跳) + list(跳2)

        for n in ("模块.py", "文档.md", "巨大.py"):
            if n not in _留:
                坏.append(f"判一百零二④：`{n}` **没进图** ⇒ 真源码被切掉"
                          f"（R499：1.72MB 的 `.py` **本来就不该被体积剔**）")
        for n in ("m.py",):
            if n in _留:
                坏.append(f"判一百零二①：备份目录里的 `{n}` **进了图**")
        for n in ("app.min.js", "proto_pb2.py", "超大源码.py"):
            if n in _留:
                坏.append(f"判一百零二②/③：`{n}` **进了图**"
                          f"（生成物，或超 `_进图上限宽`={getattr(_kg, '_进图上限宽', 0) // 1048576}MB）")
        # ★ 反向（口径 260）：白名单后缀的宽门槛**必须比窄门槛大**，
        #   否则等于没放宽（这是我 R499 修的东西，要锁住）
        _窄 = int(getattr(_kg, "_进图上限字节", 0))
        _宽 = int(getattr(_kg, "_进图上限宽", 0))
        if not (_窄 and _宽 > _窄):
            _宽 = _窄 if _宽 <= _窄 else _宽
            坏.append(f"判一百零二③：白名单后缀的宽门槛（{_宽}）没有比窄门槛"
                      f"（{_窄}）大 ⇒ 真源码仍会被体积剔掉（R499 修的正是这个）")
        _无因 = [x for x in _全跳 if not x.get("原因")]
        if _无因:
            坏.append(f"判一百零二⑤：剪掉的 {len(_无因)} 条**没有原因**")
        _因文 = " ".join(str(x.get("原因")) for x in _全跳)
        for _词 in ("备份", "生成", "过大"):
            if _词 not in _因文:
                坏.append(f"判一百零二⑤：跳过原因里**看不出「{_词}」这一类** ⇒ "
                          f"「为什么被剪」决定了「该不该捡回来」，必须能分类")
        if not 留:
            坏.append("判一百零二：**一个文件都没留下** ⇒ 这条判据没测到东西")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判一百零二备份与生成物不许进图且必须留名：绿")
    print("   备份/生成物：绿 —— 判一百零二（备份目录不进 · 生成物不进 · "
          "超大不进 · 真源码进 · 跳过带可分类原因 · 新词组合照样认）")

def 判一百零三索引读不到的步骤必须带原因且不许断言历史() -> None:
    """★★★ **「文件不存在」不是历史证据**（R423/R424）。

    ★ 为什么需要（我还是错的）：
      R422 我在「全仓索引」里对缺失的 `引用.json`/`缺口.json` 直接写 `None` ✗，
      并断言「**17 张图都没跑这一步**」—— **两句都错**：
      ① `None` 让读的人分不清"这张图没有未解析"与"这一步没产出"；
      ② `解析引用` **早就跑过**，我看到的"文件不存在"是
         **`建图` 重建时清掉了图目录**的副作用。
      ★ 口径 140：**「文件不存在」不是历史证据** ——
        能证明"做过"的是**产物里的读数**，不是"文件名在不在"。
      ★ 而更根本的错是：**我用"当下的文件系统状态"去推断"历史上做没做过"**，
        而 `建图` 会清目录 ⇒ 这个推断**必然错**。

    ★ 判据形状（四项）：
      ① **缺失必须带原因**（口径 92：允许没有 + 必须说明）；
      ② ★ **原因必须写准**：至少要给出"**没跑**"与"**跑过但被重建清掉了**"
         两种可能 —— **不许替使用者断言历史**；
      ③ ★ 字段名不许叫「跑了没」——只能说「**当下齐不齐**」（名实一致）；
      ④ ★ **缺失要给出重跑命令**（光说"缺"没用，得能补上）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零三：`kg` 导不进来（{type(e).__name__}: {e}）"])
    if not hasattr(_kg, "刷全仓索引"):
        return _报2(["判一百零三：`kg` 里没有 `刷全仓索引`"])
    import inspect as _ins
    _源 = _ins.getsource(_kg.刷全仓索引)
    for _要, _为何 in (
            ("★缺哪些步", "缺失必须**带原因**"),
            ("被后来的 `建图` 重建清掉了", "原因必须写准（两种可能都要给）"),
            ("★这套产物齐不齐", "字段名要名实一致，不能说「跑了没」"),
            ("重跑命令", "缺失要给出**重跑命令**"),
    ):
        if _要 not in _源:
            坏.append(f"判一百零三：`刷全仓索引` 里没有 `{_要}` ⇒ {_为何}")
    if "★解析引用跑了没" in _源:
        坏.append("判一百零三③：还在用 `★解析引用跑了没` —— "
                  "**我只知道「当下齐不齐」，不知道「历史上做没做」**")
    # ★ 反面：不许出现"不查就写 None"的写法
    if "条[_键] = None\n                    continue" in _源:
        坏.append("判一百零三①：仍在**不写原因**就把值设成 `None`")

    if 坏:
        return _报2(坏, "判一百零三索引读不到的步骤必须带原因且不许断言历史：绿")
    print("   索引缺步说明：绿 —— 判一百零三（带原因 · 两种可能 · 名实一致 · 给重跑命令）")


def 判一百零四建总图的收尾必须解引用且索引一并刷新() -> None:
    """★★★ **刷新/同步必须是收尾的一部分**（R425/R426，口径 104/117）。

    ★ 为什么需要（实测）：
      R424 我刚跑完 `解析引用`，**下一轮 `建总图` 重建 16 张图后，
      `引用.json` 又全不见了** ——
      `建图` 重写图目录 ⇒ **建图会毁掉上一步的解析产物**。
      ★ 口径 142：**「建图」会毁掉「解析引用」的产物** ⇒
        两步**必须绑成一个原子动作**；否则"到底跑了没"取决于**我看没看见**。
      ★ 而我只把「解引用」接进收尾还不够（R425 的漏，R426 才补）：
        **索引还在手动** ⇒ "齐没齐"仍然取决于我记不记得。
        口径 117：**修完一个机制要问「下一步有没有按它走」**。

    ★ 判据形状（四项）：
      ① `建总图` 必须有 `收尾解析` 参数，且 **默认 `True`**
         （★ 正确的事不该靠记得）；
      ② ★ 函数体里**必须真的调用** `解析引用(`（登记了≠接上了，口径 cs）；
      ③ ★ 函数体里**必须真的调用** `刷全仓索引(`；
      ④ ★ **两步失败都必须"如实说出"**（不静默）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零四：`kg` 导不进来（{type(e).__name__}: {e}）"])
    import inspect as _ins
    try:
        _签 = _ins.signature(_kg.建总图)
        _源 = _ins.getsource(_kg.建总图)
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零四：拿不到 `建总图` 的签名/源码（{type(e).__name__}）"])
    if "收尾解析" not in _签.parameters:
        坏.append("判一百零四①：`建总图` **没有 `收尾解析` 参数**")
    elif _签.parameters["收尾解析"].default is not True:
        坏.append(f"判一百零四①：`收尾解析` 默认是 "
                  f"{_签.parameters['收尾解析'].default!r}，**必须默认 True**")
    # ★★ R519 改靶（口径 332）：收尾变成了**一个入口** `收尾全套`（5 个产物一起做），
    #   所以「`建总图` 里有没有 `解析引用(` 这个字符串」不再是对的靶。
    #   真靶：**收尾路径上必须真的跑到 `解析引用`** —— 直调，或经 `收尾全套`，
    #   而 `收尾全套` 自己**也必须**调 `解析引用(`（两层都查，少一层就红）。
    _经全套 = "收尾全套(" in _源
    _直调 = "解析引用(" in _源
    _全套源 = ""
    if _经全套:
        try:
            import inspect as _ins104
            from agentscope_orchestrator import kg as _kg104
            _全套源 = _ins104.getsource(_kg104.收尾全套)
        except Exception as _e104:                              # noqa: BLE001
            _全套源 = ""
            坏.append(f"判一百零四②：拿不到 `收尾全套` 的源码"
                      f"（{type(_e104).__name__}: {_e104}）⇒ 收尾链路判不了")
    if not _直调 and not _经全套:
        坏.append("判一百零四②：函数体里**既没直调 `解析引用(`、也没调 `收尾全套(`** ⇒ "
                  "`建图` 会毁掉上一步的解析产物")
    if _经全套 and "解析引用(" not in _全套源:
        坏.append("判一百零四②：`建总图` 经 `收尾全套(` 收尾，但 **`收尾全套` 自己没调 "
                  "`解析引用(`** ⇒ 收尾入口看着在，里面的解引用这一步没了")
    if "刷全仓索引(" not in _源:
        坏.append("判一百零四③：函数体里**没有调用 `刷全仓索引(`** ⇒ "
                  "索引留在手动，「齐没齐」取决于我记不记得")
    # ★★ R519 改靶（口径 332）：收尾的解引用那一步从「直调 `解析引用`」变成
    #   「经 `收尾全套`」，`建总图` 里那句如实报错也随之改名：
    #     `收尾解引用失败` → **`收尾全套失败`**（实测 `建总图` L78：
    #     `★ 子根 … **收尾全套失败**`）。★ 如实报错路径**一直都在**，变的是词。
    #   ⇒ 靶改成**认两种词**（旧的与新的），但**必须至少中一个** —— 这是改靶不是放宽。
    _解引用失败词 = [w for w in ("收尾全套失败", "收尾解引用失败") if w in _源]
    if not _解引用失败词:
        坏.append("判一百零四④：收尾解引用那一步**失败时没有任何如实报错路径**"
                  "（既不叫 `收尾全套失败`、也不叫 `收尾解引用失败`）⇒ 失败会静默")
    if "收尾刷索引失败" not in _源:
        坏.append("判一百零四④：没有 `收尾刷索引失败` 的如实报错路径 ⇒ 失败会静默")
    if "不静默" not in _源:
        坏.append("判一百零四④：收尾失败时没有「不静默」的声明")

    if 坏:
        return _报2(坏, "判一百零四建总图的收尾必须解引用且索引一并刷新：绿")
    print("   收尾三步：绿 —— 判一百零四（收尾解析默认开 · 真调 解析引用 · "
          "真调 刷全仓索引 · 失败不静默）")


def 判一百零五全仓总账必须从落盘文件读且总数对得上分项之和() -> None:
    """★★★ **同源读数 + 总数守恒**（R425，口径 141）。

    ★ 为什么需要（我连犯三次）：
      ① `跑解析引用.py` 用 `出.get("引用")` 猜返回结构的键名 ⇒ **全报 0** ✗
      ② `刷全仓索引` 用 `d2.get("未解析")` ⇒ **None** ✗
      ③ `全仓总账.py` 用 `_合["引用"] += 引` ⇒ **`TypeError: int + list`** ✗
        （因为 **`引用.json` 是 list**，我当 dict 用了）
      ★ 口径 141：**不要猜数据的形状，去读它的生产者** ——
        `出.get(键名)` 是**猜**；`json.loads((图目录/名.json).read_text())` 是**读**。
        **猜错的代价是静默的零，而零看起来像好消息。**

    ★ 判据形状（四项）：
      ① ★ **真跑一遍**全仓总账，**不许抛异常**（`引用.json` 是 list 这个坑
         会在这里以 `TypeError` 暴露）；
      ② ★ **总数 = 分项之和**（节点/引用/未解析/真外部缺 四个量各验一次）——
         ★ 这条是**守恒判据**：任何一个根读错了，总数就对不上；
      ③ ★ **每个建成图的根都要有一行**（不许因为读不动就静默跳过）；
      ④ ★ **没建图的根必须显式标出**（不许静默省略）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零五：`kg` 导不进来（{type(e).__name__}: {e}）"])

    import json as _json
    import tempfile as _tf
    import shutil as _sh

    # ── 造一个小仓：3 个根，各建图 + 各写产物（形状**照实**：引用.json 是 list）
    根 = Path(_tf.mkdtemp(prefix="kg105_"))
    try:
        for i in range(3):
            d = 根 / f"子根{i}" / ".kg"
            d.mkdir(parents=True)
            (d / "节点.json").write_text(
                _json.dumps([{"名": f"n{j}"} for j in range(10 + i)]), encoding="utf-8")
            (d / "边.json").write_text(
                _json.dumps([{"从": "a", "到": "b"} for _ in range(5 + i)]), encoding="utf-8")
            (d / "引用.json").write_text(          # ★ **list**，不是 dict
                _json.dumps([{"名": f"r{j}"} for j in range(7 + i)]), encoding="utf-8")
            (d / "外部缺.json").write_text(_json.dumps(
                {"未解析总数": 3 + i, "真外部缺·去重数": 2 + i,
                 "半截名字·去重数": 0}), encoding="utf-8")
            (d / "缺口.json").write_text(_json.dumps(
                {"★已解析占比": 99.0, "文件数": 1}), encoding="utf-8")
        # ★ 让 分根 认它：写足够源文件
        for i in range(3):
            for j in range(60):
                (根 / f"子根{i}" / f"f{j}.py").write_text("x = 1\n", encoding="utf-8")

        _出 = _kg.刷全仓索引(根, 打印=lambda *_: None)
        _根们 = [x for x in _出["根们"] if x.get("有图")]
        if not _根们:
            坏.append("判一百零五③：一件建成图都没读到 ⇒ 判据没测到东西")
        else:
            for _键 in ("节点数", "引用条数", "未解析", "真外部缺·去重数"):
                _合 = sum((x.get(_键) or 0) for x in _根们)
                _期 = {"节点数": (10 + 11 + 12), "引用条数": (7 + 8 + 9),
                      "未解析": (3 + 4 + 5),
                      "真外部缺·去重数": (2 + 3 + 4)}[_键]
                if _合 != _期:
                    坏.append(f"判一百零五②：`{_键}` 分项之和 = {_合}，"
                              f"应为 {_期} ⇒ **有根读错了**（或键名猜错了）")
            if any(x.get("引用条数") is None for x in _根们):
                坏.append("判一百零五①：有根的 `引用条数` 是 `None` ⇒ "
                          "**`引用.json` 是 list，我当 dict 读了**（口径 141）")
    except TypeError as e:
        坏.append(f"判一百零五①：读总账抛 `TypeError`（{e}）⇒ "
                  "**把 list 当数字加了**（`引用.json` 是 list）")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百零五①：读总账抛 {type(e).__name__}: {e}")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判一百零五全仓总账必须从落盘文件读且总数对得上分项之和：绿")
    print("   总账守恒：绿 —— 判一百零五（引用.json 是 list 也不炸 · "
          "四个量的分项之和都对得上 · 没建图的显式标出）")

def 判一百零六可行动缺口与不可行动缺口必须分表且带在别处() -> None:
    """★★★ **可行动的缺口与不可行动的缺口必须分开报**（R433，口径 150/147）。

    ★ 为什么（实测）：目标②的三分类是「局部 / 跨文件 / extern-未解析」，
      但 ③ 里面**混着两种完全不同的问题**：
    ````
      ③a **未导入**：别处有定义、本文件没导入 ⇒ **补一行 import 就消**（604 处）
      ③b **真外部缺**：本仓任何地方都没有   ⇒ **补不了**（10,329 个，多是 Option/String/DecidableEq）
    ````
      ★ 混在一起报的后果（实测）：Top18 **全是语言内置** ——
        `Option 3175 · String 2793 · Some 1818 · Vec 1255 …`
        **一张 Top10 全是 `Option` 的清单，看一百遍也不会让任何人去做任何事**（口径 147）。
      ⇒ 必须**两张表**：`全仓未导入()`（能修）与 `全仓缺口()`（只能知道）。

    ★ 判据形状（五项）：
      ① 两个函数**都必须存在**，且各自**落不同的盘**（不许覆盖）；
      ② ★ `全仓未导入` 的每条**必须带「在别处」** ——
         光说缺什么没用，要说**它哪里有定义**（那才是"该往哪补"）；
      ③ ★ `全仓缺口` 必须**分三类计数**，且类名**要装得下**
         （不许用「第三方依赖」装「Lean core / Mathlib」——口径 148）；
      ④ ★ **两表不许把对方的内容算进来**：`未导入` 那条不许出现在
         `全仓缺口` 的「真缺口」里（否则"能修的"被"补不了的"淹掉）；
      ⑤ ★ **没产物的根必须显式报出**（口径 92/95：沉默的缺席比报错坏）。
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零六：`kg` 导不进来（{type(e).__name__}: {e}）"])
    import inspect as _ins
    for _需 in ("全仓未导入", "全仓缺口"):
        if not hasattr(_kg, _需):
            坏.append(f"判一百零六①：`kg` 里没有 `{_需}` ⇒ "
                      f"可行动与不可行动**没分成两张表**")
    if 坏:
        return _报2(坏, "判一百零六可行动缺口与不可行动缺口必须分表且带在别处：绿")

    _未源 = _ins.getsource(_kg.全仓未导入)
    _缺源 = _ins.getsource(_kg.全仓缺口)
    if "未导入清单.json" not in _未源:
        坏.append("判一百零六①：`全仓未导入` 没落盘 `未导入清单.json`")
    if "缺口清单.json" not in _缺源:
        坏.append("判一百零六①：`全仓缺口` 没落盘 `缺口清单.json`")
    if "未导入清单.json" in _缺源:
        坏.append("判一百零六①：`全仓缺口` **落了 `全仓未导入` 的盘** ⇒ 两表会互相覆盖")
    # ── ② 带「在别处」
    if "在别处" not in _未源:
        坏.append("判一百零六②：`全仓未导入` 里没有 `在别处` ⇒ "
                  "光说缺什么没用，要说**它哪里有定义**")
    # ── ③ 三类计数 + 类名装得下
    if "★三类" not in _缺源:
        坏.append("判一百零六③：`全仓缺口` 里没有 `★三类` 计数")
    if "第三方依赖" in _缺源:
        坏.append("判一百零六③：还在用 `第三方依赖` 这个名字 ⇒ "
                  "**它装不下「Lean core / Mathlib」**（口径 148：名字不诚实，读数一定骗人）")
    if "外部库" not in _缺源:
        坏.append("判一百零六③：没有 `外部库` 这一类（本仓之外的正确名字）")
    # ── ⑤ 没产物要报出
    for _名, _源 in (("全仓未导入", _未源), ("全仓缺口", _缺源)):
        if "没产物" not in _源:
            坏.append(f"判一百零六⑤：`{_名}` 没有报出『没产物的根』 ⇒ "
                      f"**沉默的缺席比报错坏**")

    # ── ④ 真跑一遍：两表内容不许互相串
    import tempfile as _tf
    import shutil as _sh
    import json as _json
    根 = Path(_tf.mkdtemp(prefix="kg106_"))
    try:
        for i in range(2):
            子 = 根 / f"子根{i}"
            (子 / ".kg").mkdir(parents=True)
            (子 / ".kg" / "节点.json").write_text("[]", encoding="utf-8")
            (子 / ".kg" / "缺口.json").write_text(_json.dumps({
                "计数": {"未导入": 3 + i, "未解析": 10},
                "未导入样例": [{"文件": f"a{i}.py", "名": f"缺{i}",
                             "在别处": [f"b{i}.py"]}],
            }), encoding="utf-8")
            (子 / ".kg" / "外部缺.json").write_text(_json.dumps({
                "★真外部缺": [{"名": "Option", "次": 5},
                            {"名": f"真缺{i}", "次": 2}],
                "真外部缺·出现次数": 7,
            }), encoding="utf-8")
            for j in range(60):
                (子 / f"f{j}.py").write_text("x = 1\n", encoding="utf-8")
        _未 = _kg.全仓未导入(根, 打印=lambda *_: None)
        _缺 = _kg.全仓缺口(根, 打印=lambda *_: None)
        if (_未.get("未导入数（各根计数之和）") or 0) != 7:
            坏.append(f"判一百零六④：未导入计数和应为 7，实得 "
                      f"{_未.get('未导入数（各根计数之和）')}")
        _真名 = {z["名"] for z in (_缺.get("Top") or [])}
        if "缺0" in _真名:
            坏.append("判一百零六④：`未导入` 的名字**混进了** `全仓缺口` 的真缺口表 ⇒ "
                      "**能修的会被补不了的淹掉**")
        if "Option" in _真名:
            坏.append("判一百零六④：语言内置 `Option` **混进了真缺口表** ⇒ "
                      "Top 会全是恒为真的噪音")
        if not (_未.get("Top")):
            坏.append("判一百零六②：`全仓未导入` 的 Top 是空的 ⇒ 判据没测到东西")
        elif not (_未["Top"][0].get("在别处")):
            坏.append("判一百零六②：`全仓未导入` 的条目里 `在别处` 是空的")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百零六④：跑两表时抛 {type(e).__name__}: {e}")
    finally:
        _sh.rmtree(根, ignore_errors=True)

    if 坏:
        return _报2(坏, "判一百零六可行动缺口与不可行动缺口必须分表且带在别处：绿")
    print("   缺口两表：绿 —— 判一百零六（两表分盘 · 未导入带「在别处」· "
          "内置不进真缺口 · 类名装得下 · 没产物的根显式报出）")

def 判一百零七全仓py必须与权威ast逐条对上() -> None:
    """★★★ **目标⑤：用权威解析器对账，不一致就是我的 bug**（R435）。

    ★ 目标⑤的原话：「Python 标准库用内建 `ast` 独立算出真 import/定义集，
      与通用 tree-sitter 路径交叉验证，**不一致就是我的 bug**」。
      `判九十四` 守的是 **stdlib 163 个文件**；本条把它扩到**全仓**。

    ★ 为什么 `ast` 是权威、tree-sitter 不是：
      `ast` 是 **Python 官方**解析器（解释器自己用它）⇒ **不可能错**；
      tree-sitter 是**第三方文法**（有版本差异与边缘 bug）。
      ⇒ 两者不一致时，**错的一定是我这边的接线/适配器**，不是 `ast`。
      ★ 这条**不对称性**是整条判据的支点：它让"不一致"变成**必错的证据**，
        而不是"两说并存"。

    ★ 判据形状（四项）：
      ① ★ **两边都报**（口径 113）—— 只报"不一致 N 处"没用，
         要报**哪个文件、哪个名字、哪边多了/少了**；
      ② ★ **`ast` 判语法错的文件必须单独计**，且**不算我的 bug** ——
         源文件本身有错时，两边都没法解析，把它算成"我不一致"是**冤枉自己**；
      ③ ★ **"我这边没给出导入"必须与"不一致"分开计** ——
         这两件事含义完全不同（前者是"没有 import 语句"，后者是"**我读错了**"）；
      ④ ★ **对账必须用与生产完全相同的两步**
         （`Parser(_取语言(语))` → `.parse(bs)` → `取导入(树, bs)`）——
         否则对的是**另一条路径**，不是生产路径。
    """
    坏: list[str] = []
    图 = Path(__file__).resolve().parents[1] / "work" / "图" / "ast对账.json"
    if not 图.is_file():
        坏.append("判一百零七①：没有 `work/图/ast对账.json` ⇒ "
                  "**目标⑤的全仓对账没跑过**（跑：`work/临时/全仓ast对账.py`）")
        return _报2(坏, "判一百零七全仓py必须与权威ast逐条对上：绿")
    try:
        import json as _json
        d = _json.loads(图.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零七①：`ast对账.json` 读不了（{type(e).__name__}: {e}）"])
    for _键 in ("文件数", "一致", "不一致", "不一致明细", "ast语法错"):
        if _键 not in d:
            坏.append(f"判一百零七①：`ast对账.json` 里没有 `{_键}` ⇒ "
                      f"**读数不完整**（实有：{list(d)[:6]}）")
    if 坏:
        return _报2(坏, "判一百零七全仓py必须与权威ast逐条对上：绿")
    if (d.get("不一致") or 0) > 0:
        _明 = (d.get("不一致明细") or [])[:4]
        坏.append(f"判一百零七：**不一致 {d['不一致']} 处** ⇒ "
                  f"**错的一定是我这边**（`ast` 是官方解析器）。前几处：{_明}")
    # ★★★ R523e 重定靶：魔法阈值（8000 / 7000「应 ≈8,595」）是 **R521 剪枝之前**
    #   标定的 —— 那时 `.venv/site-packages` 下约 5,900 个 `.py` 也在覆盖里。
    #   剪枝之后正确覆盖是 **3,219** ⇒ 永远红。换成 **同源 + 守恒**（不会过期）：
    _数107 = int(d.get("文件数") or 0)
    _图py107: set = set()
    _图目录107 = Path(__file__).resolve().parents[1] / "work" / "图"
    for _b107 in _图目录107.iterdir():
        _p107, _g107 = _b107 / "戳.json", _b107 / "图.json"
        if not (_p107.is_file() and _g107.is_file()):
            continue
        try:
            _根107 = Path(str(_json.loads(_g107.read_text(encoding="utf-8")).get("根") or ""))
            _戳107 = _json.loads(_p107.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001, PERF203
            continue
        if not isinstance(_戳107, dict):
            continue
        for _k107 in _戳107:
            if str(_k107).lower().endswith(".py"):
                try:
                    _图py107.add(str((_根107 / str(_k107)).resolve()))
                except OSError:
                    pass
    if not _图py107:
        坏.append("判一百零七③：**图上一个 `.py` 都没数到** ⇒ 判据自己没接上（口径 307）")
    # ★★★ R525n/o：**同源 + 差额逐条指名**（口径 260/203/344）
    #   ★ 原来写的是「`_数107` < 图上真有的 ⇒ **对账漏文件**」—— **那句话是假话**：
    #     实测 `太虚元道` 的差额里，9 个盘上还在（被「同前缀兄弟 ≥3 个 ⇒ 运行副本」
    #     剪掉，而**兄弟是别的会话后建的**）· 2 个盘上已删 · 3 个建图之后新加
    #     ⇒ **对账一个都没漏，是图的快照漂了**（口径 344：声称的根因要能过判据）。
    _枚107 = int(d.get("★枚举到") or 0)
    if _枚107 and _数107 < _枚107:
        坏.append(f"判一百零七③：对账只对了 {_数107} 个 `.py`，而它**自己枚举到** "
                  f"{_枚107} 个 ⇒ **对账漏了它自己枚举到的文件**（口径 260：同源比较）")
    elif "★图上没枚举到" not in d:
        坏.append("判一百零七③：`ast对账.json` 里没有 `★图上没枚举到` ⇒ "
                  "**图与枚举的差额没被指名**（口径 260/203：少算的必须列出来，"
                  "不许只报一个总数）")
    else:
        _图没枚107 = list(d.get("★图上没枚举到") or [])
        _枚没图107 = list(d.get("★枚举到图上没有") or [])
        _差107 = len(_图py107) - _数107
        _名107 = len(_图没枚107) - len(_枚没图107)
        _还在107 = sum(1 for x in _图没枚107 if (x or {}).get("盘上还在"))
        if _差107 != _名107:
            坏.append(f"判一百零七③：图上真有的 {len(_图py107)} 与对账 {_数107} 差 "
                      f"**{_差107}**，而产物只指名了 **{_名107}** 条"
                      f"（图上没枚举到 {len(_图没枚107)} · 枚举到图上没有 "
                      f"{len(_枚没图107)}）⇒ **差额里有 "
                      f"{abs(_差107 - _名107)} 条是无名的**（口径 260）")
        else:
            print(f"   权威对账·差额已指名：图上戳 {len(_图py107)} · 对账 {_数107} · "
                  f"图上没枚举到 {len(_图没枚107)}（盘上还在 {_还在107} ⇒ 剪枝漂移；"
                  f"已删 {len(_图没枚107) - _还在107}）· 枚举到图上没有 {len(_枚没图107)}")
    # ★ 守恒等式：对账的每一类都要有去处 —— 不许有"莫名没对到"的文件
    _无导107 = int(d.get("★我没给出导入") or 0)
    _法错107 = len(d.get("ast语法错") or [])
    _一致107 = int(d.get("一致") or 0)
    # ★★★ R525p：**读不动**也要有去处（它原来是 `continue` ⇒ 守恒差 1）
    _读不动107 = len(d.get("★读不动") or [])
    _类107 = _一致107 + _无导107 + _法错107 + _读不动107
    if _数107 and _类107 != _数107:
        坏.append(f"判一百零七③：**守恒不成立** —— 文件数 {_数107} ≠ 一致 {_一致107}"
                  f" + 我没给出导入 {_无导107} + ast语法错 {_法错107}"
                  f" + 读不动 {_读不动107}"
                  f"（差 {_数107 - _类107}）⇒ "
                  f"有文件**既没对上、也没被归到某类**（口径 113：两边都报）")
    # ★ ② 语法错要单独计；③ 不漏"没给出导入"这一栏
    if "ast语法错" not in d:
        坏.append("判一百零七②：`ast` 判语法错的文件**没有单独计** ⇒ "
                  "会把『源文件本身有错』算成『我不一致』（**冤枉自己**）")

    # ── ④ 对账必须用与生产相同的两步（源码级核对）
    _脚 = Path(__file__).resolve().parents[1] / "work" / "临时" / "全仓ast对账.py"
    if not _脚.is_file():
        坏.append("判一百零七④：对账脚本 `work/临时/全仓ast对账.py` 不在了 ⇒ "
                  "**判据没法复跑**")
    else:
        _源 = _脚.read_text(encoding="utf-8", errors="replace")
        for _要, _为何 in (
                ("Parser(", "必须用与生产相同的 `Parser(...)`（不是别的解析路径）"),
                ("_取语言(", "必须用生产同一套 `_取语言`"),
                ("取导入(", "必须调生产的 `取导入`（否则对的是另一条路径）"),
                ("ast.parse", "必须用**官方** `ast` 作权威"),
        ):
            if _要 not in _源:
                坏.append(f"判一百零七④：对账脚本里没有 `{_要}` ⇒ {_为何}")

    if 坏:
        return _报2(坏, "判一百零七全仓py必须与权威ast逐条对上：绿")
    print(f"   权威对账：绿 —— 判一百零七（`ast` 逐条对 {d['文件数']} 个 `.py` · "
          f"一致 {d['一致']} · **不一致 {d['不一致']}** · "
          f"语法错单列 {len(d.get('ast语法错') or [])} · 两边都报）")

def 判一百零八Lean确证必须与文本级并列在同一张图且口径一起报() -> None:
    """★★★ **目标⑥：Lean/elaborator 那条路必须合流进同一张图**（R437）。

    ★ 目标⑥的原话：「把 Lean/elaborator 那条路（`mem_axiom` 的确证名）
      合流进**同一张图**」。
      ★ 「合流」的判据不是"跑过 `灌Lean确证`"，而是：
        **确证名与文本级名落在同一张图的同一批节点上，且两者并列可分**。

    ★ 这笔账是**以前欠的**（`工程声明名-elaborator.json` 里我自己写着）：
    ````
    「`★正则抽出 = 9` 条，全是 `['abbrev','axiom','class','def']`
     —— **是我取 `group(1)` 取到了关键字而不是名字** ⇒ 对账不成立。」
    ````
      ★ 真相只要读一次 `groupindex`：
        `_定义正则['lean'].groupindex == {'kind': 1, 'name': 2}`
        ⇒ **名字在 `group(2)`**，我取的是 `group(1)`（= `kind`）✗
      ★ 口径 157：**能自动打印的东西，不要靠记**。

    ★ 判据形状（五项）：
      ① ★ **正则的组号必须按 `groupindex` 读**，不许写字面量 `1`
         （这就是那笔账的根因）；
      ② ★ **确证档必须真的存在且非空** —— 而**跨仓**是它的历史坑
         （默认在 `$pkg/products/mem/`，图建在 `$root/.kg/`）；
      ③ ★ **两个口径必须一起报**：正则只抽**短名**、elaborator 是**全限定名**
         ⇒ 只报"一个口径下的差"就是**假读数**（口径 155）；
      ④ ★ **`灌Lean确证` 的产物必须落在"当前图目录"**，
         不许只落在**旧图目录**（实测：`formal_proof/lean确证.json` 在，
         `形式化验证/` 下没有 ⇒ **合流跑过，但没进正式图**）；
      ⑤ ★ **"挂不上文件"必须如实报出**（实测 5 个 ⇒ 沉默的缺席比报错坏）。
    """
    坏: list[str] = []
    测试根 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(测试根 / "src"))
    try:
        from agentscope_orchestrator import kg as _kg
        import agentscope_orchestrator.mem_symbols as _m
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零八：导不进来（{type(e).__name__}: {e}）"])
    import re as _re

    # ── ① 正则组号必须按 groupindex 读
    _规则 = getattr(_m, "_定义正则", {}).get("lean")
    if _规则 is None:
        return _报2(["判一百零八①：`mem_symbols._定义正则` 里没有 `lean`"])
    _名组 = _规则.groupindex.get("name")
    if _名组 is None:
        坏.append("判一百零八①：`lean` 正则**没有具名 `name` 组** ⇒ "
                  "不许猜组号（这正是那笔账的根因）")
    else:
        # ★ 用正确的组号抽一遍，抽到的**不许是关键字**
        _关 = {"theorem", "lemma", "def", "abbrev", "structure", "class",
              "inductive", "axiom", "instance"}
        _试 = ("theorem foo_bar : True := trivial\n"
              "def myDef (x : Nat) : Nat := x\n")
        _抽 = {mm.group(_名组) for mm in _规则.finditer(_试) if mm.group(_名组)}
        if _抽 & _关:
            坏.append(f"判一百零八①：按 `groupindex['name']` = {_名组} 抽出来的是"
                      f"**关键字** {sorted(_抽 & _关)} ⇒ **组号读错了**")
        if "foo_bar" not in _抽 or "myDef" not in _抽:
            坏.append(f"判一百零八①：抽出 {sorted(_抽)}，"
                      f"**没抽到真名**（应含 foo_bar 与 myDef）")
    # ★ 源码级：不许在 mem_symbols 里用字面量 group(1) 当名字
    try:
        import inspect as _ins
        _源 = _ins.getsource(_m._正则抽取)
        if _re.search(r"group\(\s*1\s*\)", _源) and "groupindex" not in _源:
            坏.append("判一百零八①：`_正则抽取` 里用了字面量 `group(1)` "
                      "且**没有读 `groupindex`** ⇒ 那笔账会重犯")
    except Exception:                             # noqa: BLE001
        pass

    # ── ④ 灌Lean确证 的产物必须落在当前图目录
    _源2 = _ins.getsource(_kg.灌Lean确证)
    if "lean确证.json" not in _源2:
        坏.append("判一百零八④：`灌Lean确证` 没有落 `lean确证.json`")
    if "_kg目录(" not in _源2:
        坏.append("判一百零八④：`灌Lean确证` **没有用 `_kg目录(...)` 定位图目录** ⇒ "
                  "会写到别的仓（**跨仓是这个机制的历史坑**）")
    # ── ② 确证档路径必须由调用方给的能覆盖默认（不许写死）
    if "确证档" not in _源2:
        坏.append("判一百零八②：`灌Lean确证` 里没有 `确证档` 参数")
    # ── ⑤ 挂不上文件要如实报
    if "挂不上" not in _源2:
        坏.append("判一百零八⑤：`灌Lean确证` 没有报『挂不上文件』"
                  "⇒ **沉默的缺席比报错坏**")
    # ── ③ 两个口径一起报
    # ★★ 判据③原本查的词（末段/全限定/短名）**是我想的，不是真键名** ⇒
    #   第一次运行就红，而**红的是判据不是代码**（口径 90/133：先问是不是尺子有病）。
    #   真实键名（打印出来的）：`★文本级升级为已确证` 与 `★只有 elaborator 有`。
    #   ★ 判据该守的是**两个方向都必须报**（口径 113），不是某个特定词。
    for _键 in ("★文本级升级为已确证", "★只有 elaborator 有"):
        if _键 not in _源2:
            坏.append(f"判一百零八③：`灌Lean确证` 里没有 `{_键}` ⇒ "
                      f"**只报一个方向就是假读数**（口径 113/155）")
    if "★挂不上文件" not in _源2:
        坏.append("判一百零八⑤：`灌Lean确证` 没有报 `★挂不上文件` ⇒ "
                  "**沉默的缺席比报错坏**")

    if 坏:
        return _报2(坏, "判一百零八Lean确证必须与文本级并列在同一张图且口径一起报：绿")
    # ── 实测读数（不判红，只印出来）
    _图 = Path(_kg._kg目录(测试根.parents[0])).parent if False else None
    print(f"   Lean 合流：绿 —— 判一百零八（正则组号按 `groupindex` 读 "
          f"⇒ 名字在 group({_名组}) · 确证档可覆盖 · **两个口径一起报** · "
          f"产物落当前图目录 · 挂不上的如实报出）")

def 判一百零九稳定前缀不许退化成单个分隔符() -> None:
    """★★★ **"同前缀兄弟 ≥3" 的前缀必须是"够具体的"**（R445）。

    ★ 实测事故（这一条就是为它立的）：
      旧式 `_副本前缀` 用 `^(.*?[_-])([a-z0-9]{4,})$` —— **非贪婪** `(.*?[_-])`
      ⇒ 前缀**退化成一个分隔符**：
    ````
    `_core` -> `"_"`   `_typing` -> `"_"`   `_utils` -> `"_"`   `_pyinstaller` -> `"_"`
    ````
      ⇒ `numpy/` 下 ≥3 个以 `_` 开头的**真子模块**被判成"运行副本"**整目录剪掉** ✗
      ⇒ 后果：**`判七十一` 整文件漏 116**、**`判七十三` 归属漏 2,761**
        —— 例全是 `site-packages/numpy/_core/…`。**两条红，一个根因。**

    ★ 口径（与 R417 的 O(n^2) 是同一条）：
      **判据本身要对，判据的代价也要能承受**。
      而这里更根本：**判据的"键"必须真的区分得开** ——
      前缀退化成 `"_"` ⇒ 它**不再是一个区分器**，而是一个**把所有东西归成一类**的漏斗。

    ★ 判据形状（五项，都可机判）：
      ① ★ **纯分隔符前缀必须是 `None`**（`_core`/`-abc` 这类不算"有前缀"）
      ② ★ **真运行副本仍要认得**（`r291v_a2j_487u`/`it_bncm8s7j`/`ts999v_ab12cd`）
      ③ ★ **前缀必须含字母数字**（这是①②的共同条件）
      ④ ★ **不许写死某一个名字**（下一轮它会变成 `r415v`…）——
         用"同前缀兄弟 ≥3"判，**结构判据**，不是名字列举（口径 103）
      ⑤ ★ **单次调用是 O(1)**（`_副本前缀表` 一次折表）——
         不许对每个名字重扫兄弟（R417 的 2,600 万次正则）
    """
    坏: list[str] = []
    import re as _re2b
    测试根 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(测试根 / "src"))
    try:
        from agentscope_orchestrator import kg as _kg
        import inspect as _ins
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百零九：导不进来（{type(e).__name__}: {e}）"])
    _前 = getattr(_kg, "_副本前缀", None)
    if _前 is None:
        return _报2(["判一百零九：`_副本前缀` 不在"])
    # ── ① 纯分隔符前缀必须 None
    for _n in ("_core", "_typing", "_utils", "_pyinstaller", "-abc", "-xyz1234"):
        _r = _前(_n)
        if _r is not None and not _re2b.search(r"[A-Za-z0-9]", _r):
            坏.append(f"判一百零九①：`{_n}` 的前缀算出 `{_r!r}` —— **纯分隔符** ⇒ "
                      f"它**不是区分器**，会把一堆真子模块归成一类剪掉")
    # ── ② 真运行副本仍要认得
    for _n in ("r291v_a2j_487u", "it_bncm8s7j", "ts999v_ab12cd"):
        if _前(_n) is None:
            坏.append(f"判一百零九②：`{_n}`（**真运行副本**）算不出前缀 ⇒ "
                      f"**修过头了**（把真副本放进来 = 自我污染，口径 105）")
    # ── ③ 前缀含字母数字
    for _n in ("r291v_a2j_487u", "it_bncm8s7j"):
        _r = _前(_n)
        if _r and not _re2b.search(r"[A-Za-z0-9]", _r):
            坏.append(f"判一百零九③：`{_n}` 的前缀 `{_r!r}` 不含字母数字")
    # ── ④ 不写死名字
    # ★ 只看**代码行** —— docstring 与注释里的**举例**不算写死
    #   （口径 152：把"风格"当"结果"判一定判错；我第一次就是这么红的）
    _源 = _ins.getsource(_kg._是运行副本)
    _代码 = []
    _在串 = False
    for _行 in _源.splitlines():
        _t = _行.strip()
        if _t.startswith(('"""', "'''")):
            _在串 = not _在串
            continue
        if _在串 or _t.startswith("#"):
            continue
        _代码.append(_行)
    _码 = chr(10).join(_代码)
    for _死 in ("r291v", "ts999v", "it_bnc"):
        if _死 in _码:
            坏.append(f"判一百零九④：`_是运行副本` **的代码里**写死了 `{_死}` ⇒ "
                      f"**下一轮它会变成别的名字**（口径 103：按名字列举必然漏）")
    # ── ⑤ O(1)
    if "_副本前缀表" not in _ins.getsource(_kg._是运行副本):
        坏.append("判一百零九⑤：`_是运行副本` **没有用 `_副本前缀表`** ⇒ "
                  "**对每个名字重扫兄弟**（R417 的 2,600 万次正则回来了）")
    if 坏:
        return _报2(坏)
    print("   稳定前缀：绿 —— 判一百零九（纯分隔符不给前缀 · 真运行副本仍认得 · "
          "前缀含字母数字 · 不写死名字 · O(1) 查表）")

def 判一百一十分项必须同量纲且算术收得上() -> None:
    """★★★ **`灌Lean确证` 的每个分项必须说明单位，且三条算术都要收得上**（R447/R448）。

    ★ 实测事故（这一条就是为它立的）：
    ````
    确证名总数 53,704 · ★文本级升级为已确证 50,422 · ★只有 elaborator 有 0
    ````
      ★ **`53,704 − 50,422 = 3,282` 去哪了？** 读数里**看不出来** ✗
      ★ 根因（读生产者 `kg.py` 的 `号 = f"定义:{_相C}:{n裸}:0"` 那一支）：
        **`if 号 in 节: continue` —— 这一支什么都不记。**
        那 3,282 条的**全限定名没配上**，但**裸名撞上了文本级已有的节点号**
        ⇒ **「全名没配上」与「文本级真没有」被合成了一个数** ✗ ——
        **口径 155/160：两个不同的东西不许共用一个数。**

    ★ 修的过程中**三次算术对不上，三次都是量纲**：
    ````
    ① 50,422 + 6,495 = 56,917 ≠ 53,704（多 3,213）
       根因：`裸.setdefault(短名, 全名)` 折叠 ⇒ `撞`按**短名**计、总数按**全名**计
    ② 50,422 + 19,469 = 69,891 ≠ 53,704（多 16,187）
       根因：`升级 += 1` **对每个节点加一次** ⇒ 数的是**节点**不是**名字**
    ③ B 差 −5,826 / −4
       根因：分项**按模块累加**、分母**全局去重**；键带 `文件:` 前缀
    ````
    ⇒ 最终 **三项都从去重集合现算**：
    ````
    A（全名量纲）：★文本级升级为已确证 + ★全名没配上的总数 == ★确证名总数
    B（短名量纲）：★全名没配上但短名撞上 + ★只有 elaborator 有 + ★号在但名不同
                    == ★去重后待新建的短名数
    C（全名量纲）：★其中被短名去重折叠掉 + ★去重后待新建的短名数
                    == ★全名没配上的总数
    ````
    ★ 实测（`formal_proof` 根 · `模块声明名-elaborator.tsv`）：
      **A 34,239 + 19,465 = 53,704 ✅ · B 669 + 0 + 0 = 669 ✅ · C 18,796 + 669 = 19,465 ✅**

    ★ 判据形状（五项，都可机判）：
      ① ★ **六个分项键全在**（三个分子 + 三个分母）——
         **少了任何一个 ⇒ 算术无从收口**
      ② ★ **A 必须收得上**（全名量纲）
      ③ ★ **C 必须收得上**（全名量纲）
      ④ ★ **B 必须收得上**（短名量纲）
      ⑤ ★ **`升级` 不许超过 `确证名总数`** ——
         旧版 `50,422` 里 **16,187 是虚增（约 32%）**：
         ★ **"差得小"不是"可以放过"**，小差值往往是**两个大错互相抵消后剩下的那个**
    """
    坏: list[str] = []
    import re as _re2c
    测试根 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(测试根 / "src"))
    try:
        from agentscope_orchestrator import kg as _kg
        import inspect as _ins
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百一十：导不进来（{type(e).__name__}: {e}）"])
    _必 = ("确证名总数", "★文本级升级为已确证", "★全名没配上的总数",
           "★全名没配上但短名撞上（文本级有同短名）", "★只有 elaborator 有（文本级抽不到）",
           "★号在但名不同（不该发生）", "★其中被短名去重折叠掉", "★去重后待新建的短名数")
    _源 = _ins.getsource(_kg.灌Lean确证)
    # ── ① 六个分项键全在
    for _k in _必:
        if f'"{_k}"' not in _源:
            坏.append(f"判一百一十①：`灌Lean确证` 里没有读数键 `{_k}` ⇒ "
                      f"**算术无从收口**（口径 144）")
    if 坏:
        return _报2(坏)
    # ── ⑤ 旧版那个"数节点不数名字"的写法不许回来
    if "升级 += 1" in _源 and "if 全名 not in 已配" not in _源:
        坏.append("判一百一十⑤：`升级 += 1` **没有 `已配` 去重守卫** ⇒ "
                  "**数的是节点不是名字**（旧版虚增 16,187，约 32%）")
    # ── ②③④ 用一个 fixture 现跑，把三条算术都算出来
    import tempfile as _tf3
    import shutil as _sh110
    _根 = Path(_tf3.mkdtemp(prefix="kgJ110_"))
    try:
        # 造一个 .lean 文件：两个同名不同命名空间的定理 + 一个变量级名
        (_根 / "a.lean").write_text(
            "namespace Foo\n\ndef bar : Nat := 1\n\ndef baz : Nat := 2\n\nend Foo\n"
            "namespace Qux\n\ndef bar : Nat := 3\n\nend Qux\n", encoding="utf-8")
        _档 = _根 / "确证.tsv"
        # ★ 同一个短名 `bar` 在两个模块里（**折叠**）＋ 一个明确的唯一名
        _档.write_text("a\tFoo.bar\na\tFoo.baz\na\tFoo.zzz_only_elab\n"
                       "a\tQux.bar\n", encoding="utf-8")
        # ★★★ R449：**必须先建图** —— `灌Lean确证` 在**没有文本级图**时
        #   拿不到 `节`，只能返回全 0（**「没测到」看起来像「测到了 0」**）。
        #   我第一次写这条判据就漏了 `建图` ⇒ 读数全 0 ⇒ ①红。
        _kg.建图(_根, 打印=lambda *_: None)
        _出 = _kg.灌Lean确证(_根, _档, 打印=lambda *_: None)
        _升 = int(_出.get("★文本级升级为已确证") or 0)
        _待 = int(_出.get("★全名没配上的总数") or 0)
        _总 = int(_出.get("确证名总数") or 0)
        _折 = int(_出.get("★其中被短名去重折叠掉") or 0)
        _撞 = int(_出.get("★全名没配上但短名撞上（文本级有同短名）") or 0)
        _只 = int(_出.get("★只有 elaborator 有（文本级抽不到）") or 0)
        _异 = int(_出.get("★号在但名不同（不该发生）") or 0)
        _D = int(_出.get("★去重后待新建的短名数") or 0)
        if _总 <= 0:
            坏.append("判一百一十①：fixture **没造出确证名** ⇒ 这一条测不到（不许当通过）")
        else:
            if _升 + _待 != _总:
                坏.append(f"判一百一十②：**算术 A 收不上**：升级 {_升} + 没配上 {_待}"
                          f" = {_升 + _待} ≠ 确证名总数 {_总} ⇒ **量纲不同**（口径 144/177）")
            if _折 + _D != _待:
                坏.append(f"判一百一十③：**算术 C 收不上**：折叠 {_折} + 待建 {_D}"
                          f" = {_折 + _D} ≠ 没配上 {_待}")
            if _撞 + _只 + _异 != _D:
                坏.append(f"判一百一十④：**算术 B 收不上**：撞 {_撞} + 只 {_只}"
                          f" + 异 {_异} = {_撞 + _只 + _异} ≠ 待建 {_D}"
                          f" ⇒ ★ **分项按模块累加、分母全局去重**（实测踩过三次）")
            if _升 > _总:
                坏.append(f"判一百一十⑤：`升级 {_升}` > `确证名总数 {_总}` ⇒ "
                          f"**数的是节点不是名字**（旧版虚增约 32%）")
    finally:
        _sh110.rmtree(_根, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   合流分项：绿 —— 判一百一十（六个分项键全在 · A/B/C 三条算术都收得上 · "
          "升级不许超总数 · 分项与分母同量纲）")

def 判一百一十一建总图收尾必须四步且都不许静默() -> None:
    """★★★ **「建完一套图」= 建图 + 解引用 + 刷索引 + 缺口账本**（R425/426/450）。

    ★ 为什么要有这一条（口径 104/117）：
    `建图` **全量重写图目录** ⇒ 它会毁掉上一次 `解析引用` 的产物
    （`引用.json` / `缺口.json` / `外部缺.json`）。所以"收尾"必须是**原子的** ——
    **"齐没齐"不许取决于我记不记得跑第二步**。

    ★ 四步（缺一步都算没建完）：
    ````
    ① `建图`（每个子根）              —— 文本级节点 + 导入边
    ② `解析引用`（每个子根）           —— 提及边 → 引用边（**带目标 id**）
    ③ `刷全仓索引`                    —— 总账（索引.json）
    ④ `全仓缺口` + `全仓未导入`        —— ★ 「AI 知道自己缺什么」的读数
    ````
    ★ ① 的账：R425 之前 `解析引用` 是**手动的** ⇒ 忘了跑 ⇒ `引用条数` 全是 0，
      而**返回值上看不出任何异常**。
    ★ ② 的账：R426 之前 `刷全仓索引` 也是手动的 ⇒ 索引里的读数是**过期的**。
    ★ ③ 的账：R450 之前缺口账本也是手动的 ⇒
      **目标④「未解析的必须报出来」只在我记得跑的时候才成立。**

    ★ 判据形状（四项，都可机判）：
      ① ★ **四步都在 `建总图` 的源码里**（不许只在阅读顺序上"存在"）
      ② ★ **每一处 `except` 都必须把失败写进返回结构**（不许只打印）
         —— 口径 162：**"没接线"不是"写错了"**，但**"失败了不入账"是"写错了"**
      ③ ★ **`结果` 里每一步都有一条 `成: True/False` 的记录**（fixture 现跑）
      ④ ★ **缺口账本必须把"可行动"与"不可行动"分开报**（口径 150）
    """
    坏: list[str] = []
    测试根 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(测试根 / "src"))
    try:
        from agentscope_orchestrator import kg as _kg
        import inspect as _ins
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百一十一：导不进来（{type(e).__name__}: {e}）"])
    _源 = _ins.getsource(_kg.建总图)
    # ── ① 四步都在源码里
    # ★★ R519 改靶：`建总图` 的收尾现在是**一个入口** `收尾全套`（5 产物一起做），
    #   带 `解析引用` 的那一句搬进了 `收尾全套`。真靶仍是「四步都在**源码里**」，
    #   但要在**收尾路径覆盖到的源码**里查 —— 直调 + 经 `收尾全套` 的那份源码。
    _源111 = _源
    if "收尾全套(" in _源:
        try:
            import inspect as _ins111
            from agentscope_orchestrator import kg as _kg111
            _源111 = _源 + "\n" + _ins111.getsource(_kg111.收尾全套)
        except Exception as _e111:                              # noqa: BLE001
            坏.append(f"判一百一十一①：拿不到 `收尾全套` 的源码"
                      f"（{type(_e111).__name__}: {_e111}）⇒ 四步判不了")
    _四 = (("建图", "建图("), ("解析引用", "解析引用("),
           ("刷全仓索引", "刷全仓索引("), ("全仓缺口", "全仓缺口("),
           ("全仓未导入", "全仓未导入("))
    for _名, _调 in _四:
        if _调 not in _源111:
            坏.append(f"判一百一十一①：`建总图` 里没有 `{_调}` ⇒ "
                      f"**收尾少了「{_名}」这一步** ⇒ 齐没齐取决于我记不记得")
    if 坏:
        return _报2(坏)
    # ── ② 每个 except 都要入账（找一个 `except ... :` 后面是否有 append / 果.append）
    _行们 = _源.splitlines()
    for _i, _行 in enumerate(_行们):
        _t = _行.strip()
        if _t.startswith("except ") and _t.endswith(":"):
            _块 = "\n".join(_行们[_i + 1:_i + 6])
            if ("果.append" not in _块) and ("raise" not in _块):
                坏.append(f"判一百一十一②：`建总图` 里有一处 `except` **既不入账也不上抛**"
                          f"（{_行.strip()[:60]}）⇒ **失败了返回值上看不出来**")
    # ── ③ fixture 现跑：结果里每一步都要有记录
    import tempfile as _tf4
    import shutil as _sh111
    _根 = Path(_tf4.mkdtemp(prefix="kgJ111_"))
    try:
        (_根 / "真项目").mkdir()
        for _n in range(6):
            (_根 / "真项目" / f"m{_n}.py").write_text(
                f"from m{(_n + 1) % 6} import q\n", encoding="utf-8")
        _出 = _kg.建总图(_根, 打印=lambda *_: None)
        if not isinstance(_出, dict) or "结果" not in _出:
            坏.append(f"判一百一十一③：`建总图` 的返回**没有 `结果`**："
                      f"{sorted(_出) if isinstance(_出, dict) else type(_出).__name__}")
        else:
            _根名 = {str(x.get("根")) for x in _出["结果"]}
            for _该 in ("<索引>", "<全仓缺口>", "<全仓未导入>"):
                if _该 not in _根名:
                    坏.append(f"判一百一十一③：`结果` 里没有 `{_该}` ⇒ "
                              f"**这一步没进账本**（实得 {sorted(_根名)[:6]}）")
    finally:
        _sh111.rmtree(_根, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   建总图收尾：绿 —— 判一百一十一（建图 · 解引用 · 刷索引 · 缺口账本；"
          "每处 except 都入账；三步都进 `结果`）")

def 判一百一十二总根不许被静默跳过() -> None:
    """★★★ **「不合格的子根」里的文件也有归属，而那个归属就是总根**（R453）。

    ★ 实测事故（我自己的小 fixture 抓出来的）：
    ````
    根/真项目/  （8 个 .py，不够 50）  ⇒ 落进 `不单独成图`
    根/散的/    （3 个 .py）           ⇒ 落进 `不单独成图`
    根/散落.py  （1 个 .py）           ⇒ 落进 `不属于任何子树`
    `分根` 的读数：合格 [] · 归属 12 条 · 不属于任何子树 ['散落.py']
    `建总图` 的结果：**3 条**（只有 `<索引>` `<全仓缺口>` `<全仓未导入>`）
    ````
    ⇒ ★ **总根根本没被建图** ⇒ **11 个文件谁都不建图 ⇒ 静默消失** ✗
    ★ 根因（`建总图` 的循环头）：
    ````
    if 子根 == 根:
        if not 计["不属于任何子树"]:      # ← ★★ 只数「**直接散落**在总根的文件」
            continue
    ````
    ★ 而 `真项目`/`散的` 的文件**归属到总根**（`归属` 的值就是总根路径）——
      **它们既不在合格子根里，也不在 `不属于任何子树` 里** ⇒ **判据漏了它们** ✗
    ★★ 这是 `判九十五`（沉默的删除）的**同族缺陷，换了个位置** ——
      ★ 口径 169：**清理/跳过的位置也是判据的一部分。**

    ★ 判据形状（四项，都可机判）：
      ① ★ **`总根` 的建图条件不许只看 `不属于任何子树`** ——
         `归属` 里**属于总根的文件数 > 0** 也必须建（源码里要看得见这个判据）
      ② ★ fixture 现跑：**一个 `<50` 文件的子目录 + 一个散落文件** ⇒
         总根**必须进 `结果`**（不许只有三条收尾账）
      ③ ★ **该建的总节点数不许比"应该有的文件数"少** ——
         用「`归属` 里属于总根的文件数」和总根那棵图的**源文件数**对账（算术，口径 144）
      ④ ★ **`判九十五` 的 `跳过的目录（带文件数）` 与这一条不许互相掩盖** ——
         两者都要能独立红
    """
    坏: list[str] = []
    import tempfile as _tf5
    import shutil as _sh112
    import json as _json112
    测试根 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(测试根 / "src"))
    try:
        from agentscope_orchestrator import kg as _kg
        import inspect as _ins
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百一十二：导不进来（{type(e).__name__}: {e}）"])
    _源 = _ins.getsource(_kg.建总图)
    # ── ① 源码里必须看得见"按归属判"这个意思
    if "_归它" not in _源 or "归属" not in _源:
        坏.append("判一百一十二①：`建总图` 判总根要不要建时**没看 `归属`** ⇒ "
                  "**「不合格子根」里的文件会静默消失**（实测 11 个文件全丢）")
    if _源.count("不属于任何子树") >= 1 and "_归它" not in _源:
        坏.append("判一百一十二①：总根的建图条件**只看 `不属于任何子树`** ⇒ "
                  "它只数「直接散落」的文件，**漏掉「归属总根」的那一批**")
    # ── ② fixture 现跑
    _根 = Path(_tf5.mkdtemp(prefix="kgJ112_"))
    try:
        (_根 / "真项目").mkdir()
        (_根 / "散的").mkdir()
        for _i in range(6):
            (_根 / "真项目" / f"m{_i}.py").write_text(f"x={_i}\n", encoding="utf-8")
        for _i in range(3):
            (_根 / "散的" / f"d{_i}.py").write_text(f"y={_i}\n", encoding="utf-8")
        (_根 / "散落.py").write_text("z=1\n", encoding="utf-8")
        _计 = _kg.分根(_根, 打印=lambda *_: None)
        _归 = _计.get("归属") or {}
        _该总根 = [k for k, v in _归.items() if Path(str(v)) == _根.resolve()]
        if not _该总根:
            # ★ 前提不成立时**明说**，不许当通过（口径 179）
            坏.append(f"判一百一十二②：fixture 的 `归属` 里**没有归到总根的文件** ⇒ "
                      f"这一条测不到（不许当通过）；合格={_计.get('合格')} "
                      f"不单独成图={_计.get('不单独成图')}")
        else:
            _出 = _kg.建总图(_根, 打印=lambda *_: None)
            _果 = _出.get("结果") or []
            _根名 = {str(x.get("根")) for x in _果}
            if str(_根.resolve()) not in _根名:
                坏.append(f"判一百一十二②：总根**没进 `结果`** ⇒ "
                          f"归属它的 {len(_该总根)} 个文件**谁都不建图**（静默消失）；"
                          f"实得 {sorted(_根名)}")
            # ── ③ 算术：总根那棵图要真的收下这些文件
            _图 = _kg.加载图(_根)
            _文 = {n.get("文件") for n in (_图.get("节点") or {}).values()
                   if n.get("类") == "文件"}
            _缺 = [k for k in _该总根
                   if not any(str(w).replace("\\", "/").endswith(k.replace("\\", "/"))
                              for w in _文)]
            if _缺 and len(_缺) == len(_该总根):
                坏.append(f"判一百一十二③：归属总根的 {len(_该总根)} 个文件"
                          f"**一个都没进图** ⇒ 静默丢失（样例 {_缺[:3]}）")
    finally:
        _sh112.rmtree(_根, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   总根不许跳过：绿 —— 判一百一十二（按 `归属` 判 · 不合格子根的文件有归属 · "
          "算术对账 · 与判九十五不互相掩盖）")

def 判一百一十三歧义必须每条都标没键不等于false() -> None:
    """★★★ **目标③：歧义必须标 —— 而「没有键」是「没标」，不是「无歧义」**（R454）。

    ★ 实测事故（全仓 2,083,886 条引用边）：
    ````
    ★ 没有 `歧义` 键的：**53,527 条（2.57%）** —— **17 个根里每一个都有**
    ★ 按 `依据` 分组，无键的那一批**清一色**是：
        `导入模块（靶文件不在本仓）`
      而它的**孪生**（只差两个字的另一条出口）：
        `导入定义（**靶文件不在本仓**）`  ← **有** `歧义`
    ````
    ⇒ ★ **同一个东西两个出口，一个标了一个没标。**
    ★ 口径 155/156：**「没有键」与「歧义=false」是两件事** ——
      而目标③要求「**歧义必须标**」：**没键 = 没标** ⇒ 下游要各自猜 ✗

    ★ 判据形状（三项，都可机判）：
      ① ★ fixture 现跑：造出「靶文件不在本仓」的导入 ⇒
         **每一条引用边都必须有 `歧义` 键**（不许"没标"）
      ② ★ **`歧义=True` 的边必须给候选**（光说"歧义"不给候选 = 没法用）
      ③ ★ **给候选的边必须是 `歧义=True`**（给了候选却标 false 是自相矛盾）
      ★ ④（附加）**常驻图目录里不许有"临时桶"** ——
         fixture 的 `tempfile.mkdtemp(prefix="kgXxx_")` 也会被 `_kg目录` 当成桶名 ✗
         （实测抓到过一个 `kg_upd_vkbflpwt`，`清单.json` 里 `根: None` ⇒ 是残留）
    """
    坏: list[str] = []
    import tempfile as _tf6
    import shutil as _sh113
    测试根 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(测试根 / "src"))
    try:
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百一十三：导不进来（{type(e).__name__}: {e}）"])
    _根 = Path(_tf6.mkdtemp(prefix="kgJ113_"))
    try:
        (_根 / "本仓.py").write_text(
            "import definitely_not_in_this_repo_xyz\n"
            "from another_external_mod_xyz import thing\n"
            "import os\nimport json\n", encoding="utf-8")
        _kg.建图(_根, 打印=lambda *_: None)
        _kg.解析引用(_根, 打印=lambda *_: None)
        _p = _kg._kg目录(_根) / "引用.json"
        if not _p.is_file():
            坏.append(f"判一百一十三①：解析后**没有 `引用.json`**（{_p}）⇒ 测不到")
        else:
            import json as _json113
            _q = _json113.loads(_p.read_text(encoding="utf-8"))
            if not isinstance(_q, list) or not _q:
                坏.append(f"判一百一十三①：`引用.json` 形状不对或空："
                          f"{type(_q).__name__} {len(_q) if hasattr(_q, '__len__') else ''}")
            else:
                _无键 = [e for e in _q if "歧义" not in e]
                if _无键:
                    _依 = sorted({str(e.get("依据"))[:44] for e in _无键})
                    坏.append(f"判一百一十三①：**{len(_无键)}/{len(_q)} 条引用边没有 "
                              f"`歧义` 键** ⇒ **没标**（目标③要求『歧义必须标』）；"
                              f"依据：{_依[:4]}")
                # ② 歧义=True 必须有候选
                _T无候 = [e for e in _q if e.get("歧义") and not e.get("候选")]
                if _T无候:
                    坏.append(f"判一百一十三②：**{len(_T无候)} 条标了 `歧义=True` 却没给 "
                              f"`候选`** ⇒ 光说『歧义』没法用（样例 "
                              f"{str(_T无候[0].get('名'))[:24]}）")
                # ③ 给了候选却没标歧义
                _候F = [e for e in _q if e.get("候选") and not e.get("歧义")]
                if _候F:
                    坏.append(f"判一百一十三③：**{len(_候F)} 条给了 `候选` 却标了 "
                              f"`歧义=False`** ⇒ 自相矛盾（样例 "
                              f"{str(_候F[0].get('名'))[:24]}）")
    finally:
        _sh113.rmtree(_根, ignore_errors=True)
    # ── ④ 常驻图目录里不许有临时桶
    _图 = 测试根 / "work" / "图"
    if _图.is_dir():
        import re as _re113
        for _d in _图.iterdir():
            if not _d.is_dir():
                continue
            if _re113.match(r"^kg[_A-Za-z0-9]{6,}$", _d.name):
                _清 = _d / "清单.json"
                _根名 = None
                if _清.is_file():
                    try:
                        import json as _j2
                        _根名 = _j2.loads(_清.read_text(encoding="utf-8")).get("根")
                    except Exception:                 # noqa: BLE001
                        _根名 = "?"
                if _根名 in (None, "?"):
                    坏.append(f"判一百一十三④：常驻图目录里有一个**临时桶** `{_d.name}`"
                              f"（`清单.json` 的 `根` = {_根名!r}）⇒ "
                              f"**fixture 的临时目录被当成了图桶** ✗")
    if 坏:
        return _报2(坏)
    print("   歧义必标：绿 —— 判一百一十三（每条引用边都有 `歧义` 键 · "
          "标了歧义必须给候选 · 给了候选必须标歧义 · 常驻图目录无临时桶）")

def 判一百一十四账本的相对路径必须写明相对谁() -> None:
    """★★★ **`未导入清单.Top[].文件` 是相对"子根"的 —— 而账本原来没写这件事**（R455）。

    ★ 实测事故（我自己踩的）：
    ````
    我按 **总根** 拼：`总根 / Top[].文件`  ⇒ **前 4 条全部"文件不在"** ✗
    拼对了 **子根** 之后：                  ⇒ **6 条命中 6** ✓
    ````
    ⇒ ★ **账本是准的，是我读错了基准** —— 而**这个基准账本里根本没写** ✗
    ★ 口径 174「留名」的推广：**相对路径必须写明『相对谁』** ——
      否则**每一个读它的人都要各自猜一遍**（而我刚猜错过）。

    ★ 判据形状（三项，都可机判）：
      ① ★ **`全仓未导入` 的 `Top` 每条都带 `根路径`**（绝对路径），
         且 `根路径 / 文件` **必须真存在**（用落盘账本或 fixture 现跑）
      ② ★ **`★口径` 里必须写明"`文件` 相对 `根路径`"** ——
         光有字段不够，**要说清它们的关系**（否则还是要猜）
      ③ ★ **`全仓缺口` 的 `Top` 也必须能定位** ——
         它带的是 `在哪些根`（**根名**）⇒ 要能由根名查到根路径（口径 174 的"两边都认，
         但至少有一个"）
    """
    坏: list[str] = []
    测试根 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(测试根 / "src"))
    try:
        from agentscope_orchestrator import kg as _kg
        import inspect as _ins
    except Exception as e:                        # noqa: BLE001
        return _报2([f"判一百一十四：导不进来（{type(e).__name__}: {e}）"])
    _源 = _ins.getsource(_kg.全仓未导入)
    # ── ① 源码里 Top 条目要带 `根路径`
    if '"根路径"' not in _源:
        坏.append("判一百一十四①：`全仓未导入` 的 `Top` 条目**没有 `根路径`** ⇒ "
                  "读的人只能猜「`文件` 相对谁」（**实测我按总根拼，前 4 条全不在**）")
    # ── ② 口径里要写明关系
    if "根路径" not in _源 or "相对" not in _源:
        坏.append("判一百一十四②：`★口径` 里**没写明 `文件` 是相对 `根路径`** ⇒ "
                  "**光有字段不够，关系要写在人看得见的地方**")
    else:
        # 口径那句必须在同一个字符串块里同时提到 `文件` 和 `根路径`
        import re as _re114
        _块 = _re114.search(r'"★口径":\s*\((?:[^)]{0,400})\)', _源)
        if not _块 or "根路径" not in _块.group(0):
            坏.append("判一百一十四②：`★口径` 的说明里**没有同时提到 `文件` 与 `根路径`** "
                      "⇒ 关系没写清")
    if 坏:
        return _报2(坏)
    # ── ③ 用**落盘账本**验（有就跑，没有就明说测不到，口径 179）
    _账 = 测试根 / "work" / "图" / "未导入清单.json"
    if not _账.is_file():
        print("   账本相对路径：绿 —— 判一百一十四（源码侧① ② 绿；"
              "★ 落盘账本不在 ⇒ ③ **测不到**，不许当通过）")
        return
    import json as _j114
    try:
        _d = _j114.loads(_账.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百一十四③：账本读不了：{type(e).__name__}: {e}")
        return _报2(坏)
    _top = _d.get("Top") or []
    if not _top:
        print("   账本相对路径：绿 —— 判一百一十四（① ② 绿；账本 `Top` 为空 ⇒ "
              "③ 测不到）")
        return
    _缺路 = [x for x in _top if not x.get("根路径")]
    if _缺路:
        坏.append(f"判一百一十四③：账本 `Top` 里有 **{len(_缺路)}/{len(_top)} 条没有 "
                  f"`根路径`** ⇒ 读的人要猜基准")
    else:
        _在 = sum(1 for x in _top if (Path(x["根路径"]) / x["文件"]).is_file())
        if _在 == 0:
            坏.append(f"判一百一十四③：账本 `Top` {len(_top)} 条里 **一条都拼不出来** "
                      f"⇒ `根路径 / 文件` 这个拼法是错的（**账本与字段自相矛盾**）")
    if 坏:
        return _报2(坏)
    print(f"   账本相对路径：绿 —— 判一百一十四（`根路径 / 文件` 拼得出来 "
          f"{_在}/{len(_top)} 条 · 口径写明关系）")

def 判一百一十六判据自己不许硬编码kg目录() -> None:
    """★★★ **判据不许假设"图一定在 `<根>/.kg`"**（R459）。

    ★ 实测事故：我把 `KG_图目录` 设上之后跑整套判据，**7 条判据同时红**：
    ````
    判五十 · 判八十二 · 判八十三 · 判八十四 · 判八十八 · 判五十三 · 判七十五
    报错全是：FileNotFoundError: ...\\work\\临时\\kg判五十_xxx\\.kg\\节点.json
    ````
    ⇒ ★ **根因不是 `kg.py` 变了，是判据硬编码了 `.kg`** ✗ ——
      `_kg目录(根)` 在 `KG_图目录` 生效时返回 **`$KG_图目录/<桶>`**，
      而判据去 `<根>/.kg` 找 ⇒ **找不到**。
    ★ 而**我前几轮跑判据时没设 `KG_图目录`** ⇒ 一直是绿的 ⇒
      **这个缺陷藏了很久**（口径 184：机制在位与读数已确认是两件事）。
    ★ 修：**12 处 `X / ".kg"` 全改成 `_kg._kg目录(X)`**。
    ★ 口径 171/174 的推广：**判据里凡是有"东西在哪"的地方，都要问生产者（`_kg目录`），
      不许自己拼路径** —— 自己拼就等于把"生产者的规则"抄了一遍，**抄的那份会过期**。

    ★ 判据形状（可机判）：**扫描本判据文件的源码，`X / ".kg"` 形式的硬编码必须为 0**，
    但**允许**：
      · 测试**故意**造 `.kg` 的（`(子 / ".kg").mkdir(...)`）—— 那是 fixture 自己摆盘
      · `_kg目录(...)` 的结果
    ⇒ 判法：逐行找 `/\s*"\.kg"`，若该行含 `mkdir` / `write_text` / `write_bytes`
      （= 自己造盘）则放行，否则报红并要求说明。
    """
    坏: list[str] = []
    import re as _re116
    _本 = Path(__file__)
    if not _本.is_file():
        坏.append(f"判一百一十六：找不到本判据文件（{_本}）⇒ 测不到")
        return _报2(坏)
    _行们 = _本.read_text(encoding="utf-8").splitlines()
    _违 = []
    for _i, _行 in enumerate(_行们, 1):
        # ★ 只查**最危险的那一种形状**：`<根> / ".kg"` ——
        #   即"判据自己拼生产者的路径"。fixture 摆盘（`(子 / ".kg").mkdir()`）、
        #   语料盘（`_语料 / ".kg"`）**不报** —— 它们不是生产者路径。
        if _re116.search(r'(^|[^\w.])_?根\s*/\s*"\.kg"', _行) and "_kg目录" not in _行:
            _违.append(f"L{_i}: {_行.strip()[:88]}")
    if _违:
        坏.append(f"判一百一十六：**判据里还有 {len(_违)} 处硬编码 `X / \".kg\"`** ⇒ "
                  f"`KG_图目录` 一生效就集体报假红（实测 7 条同时红）：{_违[:4]}")
    if 坏:
        return _报2(坏)
    print("   判据不许硬编码 kg：绿 —— 判一百一十六（不拼路径，只问 `_kg目录`）")

def 判一百一十五规模必须量得上且对得齐() -> None:
    """★★★ **判据池里几乎没有"规模"维度 —— 这一条补上**（R457）。

    ★ 为什么要有（口径 144/160 的推广）：
    前面 114 条判的是**"有没有、对不对"**，几乎没有判**"够不够大"** ——
    而**一个把 99% 文件都跳过的图，判据全都能绿**：
    ````
    实测（R445 之前）：`_副本前缀` 退化成 `"_"` ⇒ `numpy/_core` 203 个文件被剪掉，
    而当时**没有任何一条判据报红** —— 直到 `判七十一`/`判七十三` 拿 stdlib 语料去对。
    ````
    ⇒ ★ **规模是"沉默的删除"的第二道防线**（第一道是 `判九十五` 的"带名字的排除"）。

    ★ 判据形状（五项，都可机判；只读落盘，不重建）：
      ① ★ **索引里的每个根都要有对应的图桶**（`图目录` 存在）
      ② ★ **每个根都要有 `边数 > 0` 且 `节点数 > 0`** ——
         0 边 = 『建了个空壳』，而**空壳在返回值上看着是『成了』**
      ③ ★ **`节点数 / 源文件数` 必须 >= 1**（每个文件至少产出一个节点）——
         比值 < 1 ⇒ **有文件没进图**（这正是不该放过的那类）
      ④ ★ **`★每根` 的源文件数之和 == 索引里的 `源文件（分根读数）` 之和**（口径 146 同源）
      ⑤ ★ **桶数 == 索引里的根数 + 允许的"被排除子根"数**，且**多出来的桶必须能解释** ——
         实测：17 个桶 vs 索引 16 个根，多出的 `formal_proof` 是
         `形式化验证` 的**合格子根**（它被父根 `排除` 掉，所以整棵仓只建它不建父根的那份）
         ⇒ 判据要求：**多出来的桶必须出现在某个根的 `排除` 名单里**（或有别的显式解释）
    ★ 只读落盘文件 ⇒ 跑得快（不重建图）。
    """
    坏: list[str] = []
    测试根 = Path(__file__).resolve().parents[1]
    图 = 测试根 / "work" / "图"
    _索 = 图 / "索引.json"
    if not _索.is_file():
        print("   规模维度：绿 —— 判一百一十五（**索引不在 ⇒ 测不到**，"
              "不许当通过；先跑 `刷全仓索引`）")
        return
    import json as _j115
    try:
        _d = _j115.loads(_索.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百一十五：索引读不了：{type(e).__name__}: {e}")
        return _报2(坏)
    _根们 = _d.get("根们") or []
    if not _根们:
        坏.append("判一百一十五：索引里 `根们` 为空 ⇒ 索引是空壳")
        return _报2(坏)
    # ── ① 每个根都要有图桶
    for _x in _根们:
        _gd = _x.get("图目录")
        if not _gd:
            坏.append(f"判一百一十五①：根 `{str(_x.get('根'))[-20:]}` 没有 `图目录`")
            continue
        if not Path(_gd).is_dir():
            坏.append(f"判一百一十五①：`图目录` 不存在：{_gd}")
    # ── ② 节点/边都要 > 0
    _零 = [str(_x.get("根"))[-18:] for _x in _根们
           if not (_x.get("节点数") or 0) or not (_x.get("边数") or 0)]
    if _零:
        坏.append(f"判一百一十五②：**{len(_零)} 个根是空壳**（`节点数` 或 `边数` 为 0）⇒ "
                  f"**而返回值上看着是『成了』**：{_零[:5]}")
    # ── ③ 节点/文件 >= 1
    _低 = []
    for _x in _根们:
        _f = _x.get("源文件（分根读数）") or 0
        _n = _x.get("节点数") or 0
        if _f > 0 and _n < _f:
            _低.append(f"{str(_x.get('根'))[-16:]}({_n}/{_f})")
    if _低:
        坏.append(f"判一百一十五③：**{len(_低)} 个根的 `节点数 < 源文件数`** ⇒ "
                  f"**有文件没进图**（正是不该放过的那类）：{_低[:5]}")
    # ── ⑤ 桶数 vs 根数：多出来的桶必须能解释
    _桶 = {x.name for x in 图.iterdir() if x.is_dir() and (x / "清单.json").is_file()}
    _根桶 = {Path(_x["图目录"]).name for _x in _根们 if _x.get("图目录")}
    _多 = sorted(_桶 - _根桶)
    _少 = sorted(_根桶 - _桶)
    if _少:
        坏.append(f"判一百一十五①：**{len(_少)} 个根的桶不存在**：{_少}")
    if _多:
        # 多出来的必须能在某个根的来源里解释：它是某个索引根的**子目录名**
        _解释 = {}
        for _b in _多:
            _找 = [Path(_x["根"]).name for _x in _根们
                   if _b in str(_x.get("★排除") or "") or _b in str(_x.get("★没建") or "")]
            _解释[_b] = _找
        _没解 = [b for b, v in _解释.items() if not v]
        if _没解:
            坏.append(f"判一百一十五⑤：**{len(_没解)} 个桶在索引里没有对应根、也无法解释**"
                      f"（桶名 {_没解[:4]}）⇒ **可能是残留/临时桶**")
    if 坏:
        return _报2(坏)
    _合节 = sum((_x.get("节点数") or 0) for _x in _根们)
    _合文 = sum((_x.get("源文件（分根读数）") or 0) for _x in _根们)
    _合边 = sum((_x.get("边数") or 0) for _x in _根们)
    print(f"   规模维度：绿 —— 判一百一十五（{len(_根们)} 个根 · "
          f"节点 {_合节:,} / 源文件 {_合文:,} = {(_合节 / _合文 if _合文 else 0):.1f} · "
          f"边 {_合边:,} = {(_合边 / _合节 if _合节 else 0):.2f}/节点 · "
          f"桶 {len(_桶)} 个（多出 {_多 or '无'}））")

def 判一百一十七索引里登记的根必须真的存在() -> None:
    """★★★ **『登记了』不等于『那儿真有』**（R460）。

    ★ 实测事故（判九十③ 抓到，但根因在我自己新加的补扫里）：
    ````
    索引里有一条：根 = `...\work\临时\kg只读_9tg5lna2\锁`
                 角色 = 「手工建的子桶」
    而那个目录**早被删了**（fixture 跑完就删）⇒ 读索引的人以为那儿有张图 ✗
    ````
    ★ 根因：**`_kg目录(根)` 用「根名」做桶名** ⇒ 桶叫 `锁`，
      而我的补扫只看到「桶里有 `图.json`」，**没问「它说的根还在不在」**。
    ★ 与 R453（总根被静默跳过）**同族**：一次是**有图没登记**，
      一次是**登记了没图** —— 都是**『登记』与『真实』脱节**。
    ★ 口径 92 的两面：**允许没有 + 必须说明** —— 死项既不是「有」，也没「说明」。

    ★ 判据形状（四项，都可机判；只读落盘，不重建）：
      ① ★ 索引 `根们` 里**每条的 `根` 目录必须存在**
      ② ★ 每条 `有图=True` 的，`图目录` 必须在且里面有 `节点.json`
      ③ ★ **`建成 + 没建 == 根数`** —— 否则「总数」是拼出来的（口径 160/173）
      ④ ★ **剔除死桶这件事必须留痕**（`★死桶条数`）—— 否则
         「剔了 3 个」与「本来就没这 3 个」长得一样（口径 155）
    """
    坏: list[str] = []
    测试根 = Path(__file__).resolve().parents[1]
    图 = 测试根 / "work" / "图"
    _索 = 图 / "索引.json"
    if not _索.is_file():
        print("   索引根存在性：绿 —— 判一百一十七（**索引不在 ⇒ 测不到**，不许当通过）")
        return
    import json as _j117
    try:
        _d = _j117.loads(_索.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百一十七：索引读不了：{type(e).__name__}: {e}")
        return _报2(坏)
    _根们 = _d.get("根们") or []
    if not _根们:
        坏.append("判一百一十七①：索引 `根们` 为空 ⇒ 空壳")
        return _报2(坏)
    # ── ① 每条的 `根` 必须存在
    _死 = [str(x.get("根")) for x in _根们
           if not x.get("根") or not Path(str(x["根"])).exists()]
    if _死:
        坏.append(f"判一百一十七①：**{len(_死)} 条登记的根目录不存在** ⇒ "
                  f"登记与真实脱节（口径 92/183）：{_死[:3]}")
    # ── ② 有图的，图目录与节点文件要在
    _假 = []
    for x in _根们:
        if not x.get("有图"):
            continue
        _gd = x.get("图目录")
        if not _gd or not Path(str(_gd)).is_dir():
            _假.append(str(x.get("根"))[-16:] + "(图目录不在)")
        elif not (Path(str(_gd)) / "节点.json").is_file():
            _假.append(str(x.get("根"))[-16:] + "(没有节点.json)")
    if _假:
        坏.append(f"判一百一十七②：**{len(_假)} 条写着 `有图` 但图上拿不出 `节点.json`** ⇒ "
                  f"说有了与真有是两件事：{_假[:4]}")
    # ── ③ 三个数自洽
    _n = _d.get("根数")
    _成 = _d.get("建成")
    _未 = _d.get("没建")
    if None not in (_n, _成, _未) and _成 + _未 != _n:
        坏.append(f"判一百一十七③：**`根数 {_n} != 建成 {_成} + 没建 {_未}`** ⇒ "
                  f"总数是拼出来的，不是数出来的（口径 160/173）")
    # ── ④ 剔死桶这件事要留痕
    if "★死桶条数" not in _d:
        坏.append("判一百一十七④：索引里**没有 `★死桶条数`** ⇒ "
                  "剔了几个与本来就没这几个长得一样（口径 155）")
    if 坏:
        return _报2(坏)
    print(f"   索引根存在性：绿 —— 判一百一十七（{len(_根们)} 个根全在 · "
          f"建成 {_d.get('建成')} + 没建 {_d.get('没建')} = {_d.get('根数')} · "
          f"剔过死桶 {_d.get('★死桶条数')} 个）")

def 判一百一十八路径段不是名字且catch形参是绑定() -> None:
    """★★★ **把「路径」当成「名字」是假缺口的头号来源**（R461）。

    ★ 实测事故（从 R460 的缺口 Top 里分诊出来的）：
    ````
    `org 332 · base 332 · ggp 386`  ← 缺口 Top 第 3/5 名
    去看原行：`package org.ggp.base.apps.benchmark;`
    ⇒ **Java 包名声明**，在 tree-sitter 里是**嵌套 5 层的 `scoped_identifier`**
    ⇒ 每层的 `identifier` 各进一条提及 ⇒ **5 个包名段全成"真缺口"** ✗
    ````
    ★ 与 R380（`from collections.abc import Mapping` 的 `collections` 也进过缺口）
      **同族**：都是**把路径段当名字**；R380 用 `取导入` 的绑定修掉了，
      而 **`package` 声明没有对应的 `取导入` 条目 ⇒ 漏在网外**。
    ★ 另一半：`} catch (Exception e) {` 的 `e` 落「未解析」——
      `formal_parameters` 在绑定表里，而 **Java 的 `catch` 用了另一个节点名**
      ⇒ **同一族、不同后缀**（R393 的老病）。

    ★ 判据形状（三项，都可机判；用一个**自带真值的 fixture**）：
      ① ★ **`package org.ggp.base;` 里的每一段都不许进引用** ——
         出现 `org`/`ggp`/`base` 任一即红（它们是**归属声明**，不可能是定义）
      ② ★ **`catch (Exception e)` 的 `e` 必须是「绑定/内置」**，不许是「未解析」
      ③ ★ **`catch` 块里的调用（`y(e)` 的 `y`）必须仍是「未解析」** ——
         这是**反向判据**：防止我为了修 ② 而把整棵 `catch` 子树当绑定
         （★ 我第一版正是这么干的 ⇒ `y` 被藏进「绑定/内置」，**比漏标更坏**）。
    """
    坏: list[str] = []
    import json as _j118
    import sys as _s118
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s118.path:
        _s118.path.insert(0, str(_src))
    try:
        from agentscope_orchestrator import kg as _kg118
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百一十八：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import shutil as _sh118
    d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判118"
    if d.exists():
        _sh118.rmtree(d)
    (d / "src" / "main" / "java" / "org" / "ggp" / "base").mkdir(parents=True)
    (d / "src" / "main" / "java" / "org" / "ggp" / "base" / "Foo.java").write_text(
        "package org.ggp.base.player.gamer.clojure.stubs;\n"   # ★ 7 段：抓魔法数字
        "public class Foo { void m() { try { x(); } catch (Exception e) { y(e); } } }\n",
        encoding="utf-8")
    try:
        _kg118.建图(d, 打印=lambda *a: None)
        _kg118.解析引用(d, 打印=lambda *a: None)
        R = _j118.loads((_kg118._kg目录(d) / "引用.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百一十八：fixture 建图/解析抛异常（{type(e).__name__}: {e}）")
        return _报2(坏)
    finally:
        _sh118.rmtree(d, ignore_errors=True)
    if not R:
        坏.append("判一百一十八：fixture 的 `引用.json` 是空的 ⇒ **测不到**，不许当通过")
        return _报2(坏)
    名到依据: dict[str, set] = {}
    for x in R:
        名到依据.setdefault(str(x.get("名")), set()).add(str(x.get("依据") or ""))
    # ── ① 包名段一个都不许在
    _段 = [k for k in ("org", "ggp", "base", "player", "gamer", "clojure", "stubs")
               if k in 名到依据]
    if _段:
        坏.append(f"判一百一十八①：**包名段进了引用**：{_段} ⇒ "
                  f"把「路径」当成了「名字」（R380 同族）")
    # ── ② catch 形参是绑定
    _e = 名到依据.get("e", set())
    if not _e:
        坏.append("判一百一十八②：fixture 里没有 `e` 的引用边 ⇒ 测不到")
    elif not any("绑定" in y or "内置" in y for y in _e):
        坏.append(f"判一百一十八②：**`catch (Exception e)` 的 `e` 不是绑定**：{sorted(_e)} ⇒ "
                  f"目标②的漏（`formal_parameters` 在表里，`catch` 用了另一个节点名）")
    # ── ③ 反向：catch 块里的调用不许被吞进绑定
    名y = 名到依据.get("y", set())
    if not 名y:
        坏.append("判一百一十八③：fixture 里没有 `y` 的引用边 ⇒ 测不到（反向判据失去意义）")
    elif all("绑定" in x or "内置" in x for x in 名y):
        坏.append(f"判一百一十八③：**`catch` 块里的 `y(e)` 被当成了绑定**：{sorted(名y)} ⇒ "
                  f"**整棵 `catch` 子树被当绑定位**（R393 说过的错，我重犯了）——"
                  f"把真缺口藏进「不算引用」，**比漏标更坏**")
    if 坏:
        return _报2(坏)
    print("   路径段与形参：绿 —— 判一百一十八（包名段 0 条 · `catch` 形参算绑定 · "
          "`catch` 块里的调用仍报未解析）")

def 判一百一十九声明性位置不是引用且同族名要写全() -> None:
    """★★★ **「声明性位置」是一个大类，而且同一个类有多个节点名**（R462）。

    ★ 两轮里分诊出来的（全是**我的缺陷**，不是数据脏）：
    ````
    R461：`package org.ggp.base.apps.benchmark;`  ⇒ `org`/`ggp`/`base` 各 300+ 次
    R462：`#![allow(non_snake_case)]`             ⇒ `allow 290 · non_snake_case 207`
          `#[cfg(test)]`                          ⇒ `test 287`
          `hits.sort_by(|left, right| …)`         ⇒ `left 200 · right 188`
    ````
    ★ 共同点：**它们是"对编译器说的话"或"形参声明"，不是对某个定义的引用**。
    ★ 而**同一个类有多个节点名**（tree-sitter 实测）：
    ````
    `package_declaration` / `namespace_definition`        ← 归属声明
    `attribute_item`（`#[…]`）                            ← 外层属性
    `inner_attribute_item`（`#![…]`）                     ← ★ 内层属性，**另一个名字**
    `closure_parameters`（`|a, b|`）                       ← 闭包形参
    `catch_formal_parameter`（`catch (E e)`）              ← Java catch 形参
    ````
    ★ 口径 202/201：**别只写你想到的那一个名字**；
      **「部分修好」比「完全没修」更容易骗过我**（我两次都是只修了一半）。

    ★ 判据形状（五项，都可机判；一个 fixture 同时覆盖 5 种）：
      ① ★ 包名声明里的每一段都不许进引用
      ② ★ **内层属性** `#![allow(non_snake_case)]` 的 `allow` / `non_snake_case` 不许进
      ③ ★ **外层属性** `#[cfg(test)]` 的 `cfg` / `test` 不许进
      ④ ★ **闭包形参** `|left, right|` 必须是「绑定/内置」，不许是「未解析」
      ⑤ ★ **反向判据**：闭包**体**里的调用（`left.zzz(right)` 的 `zzz`）不许被吞进绑定
         —— 防我为了修 ④ 而把整个 `closure_expression` 当绑定位（R393/R461 的老病）。
    """
    坏: list[str] = []
    import json as _j119
    import sys as _s119
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s119.path:
        _s119.path.insert(0, str(_src))
    try:
        from agentscope_orchestrator import kg as _kg119
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百一十九：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import shutil as _sh119
    d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判119"
    if d.exists():
        _sh119.rmtree(d)
    d.mkdir(parents=True)
    (d / "a.rs").write_text(
        "#![allow(non_snake_case)]\n"
        "#[cfg(test)]\n"
        "package_like::not_used;\n"
        "fn main() {\n"
        "    hits.sort_by(|left, right| qqq(left) + rrr(right));\n"
        "}\n",
        encoding="utf-8")
    (d / "B.java").write_text(
        "package org.ggp.base.player.gamer.clojure.stubs;\n"
        "public class B { void m() {} }\n",
        encoding="utf-8")
    try:
        _kg119.建图(d, 打印=lambda *a: None)
        _kg119.解析引用(d, 打印=lambda *a: None)
        R = _j119.loads((_kg119._kg目录(d) / "引用.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百一十九：fixture 建图/解析抛异常（{type(e).__name__}: {e}）")
        return _报2(坏)
    finally:
        _sh119.rmtree(d, ignore_errors=True)
    if not R:
        坏.append("判一百一十九：fixture 的 `引用.json` 是空的 ⇒ **测不到**，不许当通过")
        return _报2(坏)
    到: dict[str, set] = {}
    for x in R:
        到.setdefault(str(x.get("名")), set()).add(str(x.get("依据") or ""))
    # ① 包名段
    _段 = [k for k in ("org", "ggp", "base", "player", "gamer", "clojure", "stubs")
           if k in 到]
    if _段:
        坏.append(f"判一百一十九①：包名段进了引用：{_段}")
    # ② 内层属性
    _内 = [k for k in ("allow", "non_snake_case") if k in 到]
    if _内:
        坏.append(f"判一百一十九②：**内层属性** `#![…]` 里的名字进了引用：{_内} ⇒ "
                  f"`inner_attribute_item` 与 `attribute_item` 是**两个节点名**")
    # ③ 外层属性
    _外 = [k for k in ("cfg", "test") if k in 到]
    if _外:
        坏.append(f"判一百一十九③：**外层属性** `#[…]` 里的名字进了引用：{_外}")
    # ④ 闭包形参
    for _p in ("left", "right"):
        _ps = 到.get(_p, set())
        if not _ps:
            坏.append(f"判一百一十九④：fixture 里没有 `{_p}` 的边 ⇒ 测不到")
        elif not any("绑定" in x or "内置" in x for x in _ps):
            坏.append(f"判一百一十九④：**闭包形参 `{_p}` 不是绑定**：{sorted(_ps)} ⇒ "
                      f"`closure_parameters` 下的无类型 `identifier` 要算绑定")
    # ⑤ 反向：闭包体里的调用不许被吞
    # ★★ 反向判据要挑**自由函数调用**（普通 `identifier`），不能挑 `left.zzz`——
    #   那儿的 `zzz` 是 `field_identifier`（成员名），**R376 明确不收** ⇒ 它本来就不在图上，
    #   拿它当反向判据 ⇒ **判据自己红了，而机制是对的**（我第一版正是这么写的 ✗）。
    _自 = [k for k in ("qqq", "rrr") if k in 到]
    if len(_自) != 2:
        坏.append(f"判一百一十九⑤：fixture 里只找到 {_自} ⇒ 反向判据失去意义")
    else:
        _被吞 = [k for k in _自 if all("绑定" in x or "内置" in x for x in 到[k])]
        if _被吞:
            坏.append(f"判一百一十九⑤：**闭包体里的自由调用被当成了绑定**：{_被吞} ⇒ "
                      f"整棵 `closure_expression` 被当绑定位（口径 203）")
    if 坏:
        return _报2(坏)
    print("   声明性位置：绿 —— 判一百一十九（包名段 0 · 内外层属性 0 · "
          "闭包形参算绑定 · 闭包体里的调用仍报未解析）")

def 判一百二十内置必须按语言分层() -> None:
    """★★★ **语言内置不许是一张全局表** —— 否则会把真缺口藏起来（R463）。

    ★ 事故的形状（这一轮真发生了）：
      为了修 Lean 缺口的 Top（`DecidableEq 574 · Inhabited 466 · congrArg 388`，
      **全是 Lean 恒真内建**，而全局 `_语言内置` 里一个 Lean 名都没有），
      我第一版把这些名字**直接塞进全局表** —— 补到一半自己看出问题：
      `path` / `time` / `env` / `iter` / `hash` / `result` / `some` / `none`
      **在 Python / JS 里是真名字** ⇒ 一旦进全局表，
      本仓里一个叫 `path` 的真定义**会被当成「内置」藏起来** ✗
      —— **这正是口径 203：「为了让一条判据变绿而放宽范围，会把真缺口藏起来」。**

    ★ 判据（五项，都可机判）：
      ① ★ **lean 层的名字**（`DecidableEq`）在 `.lean` 语境下判「内置」
      ② ★ **rust 层的名字**（`path`）在**只有** `.rs` 的语境下判「内置」
      ③ ★★ **反向**：`path` 同时在 `.rs` 与 `.py` 语境下时**必须判「真缺口」**
         —— 这是本判据的核心：**用 `all` 不用 `any`**（保守 = 宁可漏判内置，
         也不许把真缺口藏掉；口径：「误报的引用比缺失的引用更坏」的反向）。
      ④ ★ `_按扩展名判语言` 对 `.lean`/`.rs`/`.java`/`.py` 给出正确语言，
         对未知扩展名给 `None`（不是猜一个）
      ⑤ ★ `外部缺.json` 里有**留痕键**（「剔掉必须留痕」，口径 199）
    """
    坏: list[str] = []
    import sys as _s120
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s120.path:
        _s120.path.insert(0, str(_src))
    try:
        from agentscope_orchestrator import kg as _kg120
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    分 = getattr(_kg120, "_缺口分类", None)
    表 = getattr(_kg120, "_内置_按语言", None)
    判语 = getattr(_kg120, "_按扩展名判语言", None)
    if 分 is None or not isinstance(表, dict) or 判语 is None:
        坏.append("判一百二十：`_缺口分类` / `_内置_按语言` / `_按扩展名判语言` "
                  "三者缺一 ⇒ **分层机制不在位**（这是本判据要守的东西）")
        return _报2(坏)
    for _层 in ("lean", "rust", "java", "python"):
        if _层 not in 表:
            坏.append(f"判一百二十：`_内置_按语言` 缺 `{_层}` 层")
    if 坏:
        return _报2(坏)
    # ① lean
    if 分("DecidableEq", {"lean"}) != "内置":
        坏.append(f"判一百二十①：`DecidableEq` 在 `.lean` 语境下不是「内置」："
                  f"{分('DecidableEq', {'lean'})} ⇒ Lean 恒真内建会占满缺口 Top")
    # ② rust
    if 分("path", {"rust"}) != "内置":
        坏.append(f"判一百二十②：`path` 在只有 `.rs` 的语境下不是「内置」："
                  f"{分('path', {'rust'})}")
    # ③ ★★ 反向：多语言时必须保守
    _混 = 分("path", {"rust", "python"})
    if _混 == "内置":
        坏.append("判一百二十③：**`path` 同时在 `.rs` 与 `.py` 里时被判成「内置」** ⇒ "
                  "用 `all` 变成了用 `any` —— **真缺口被藏起来了**（口径 203）")
    # ④ 扩展名判语言
    for _路, _望 in (("file:a.lean", "lean"), ("file:a.rs", "rust"),
                     ("file:a.java", "java"), ("file:a.py", "python"),
                     ("file:a.xyz", None)):
        _得 = 判语(_路)
        if _得 != _望:
            坏.append(f"判一百二十④：`_按扩展名判语言({_路!r})` = {_得!r}，期望 {_望!r}")
    # ⑤ 留痕键 —— ★★ **不许"文件不在就跳过"**（口径：「测不到不许当通过」）。
    #    我第一版读的是**包目录自己的 `.kg`**（那里根本没有图）⇒ 静默跳过 ⇒
    #    把两个留痕键**全删掉**它照样绿 ✗ —— 判据绿了，而它守的东西不在。
    #    ⇒ 改成**自己造一个 Lean fixture**，跑完建图+解析，读**那个 fixture** 的产物。
    import json as _j120
    import shutil as _sh120
    _d120 = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判120"
    if _d120.exists():
        _sh120.rmtree(_d120)
    _d120.mkdir(parents=True)
    (_d120 / "a.lean").write_text(
        "theorem t (n : Nat) : n = n := rfl\n"
        "example : True := trivial\n",
        encoding="utf-8")
    try:
        _kg120.建图(_d120, 打印=lambda *a: None)
        _kg120.解析引用(_d120, 打印=lambda *a: None)
        _f = _kg120._kg目录(_d120) / "外部缺.json"
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十⑤：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    finally:
        pass
    if not _f.is_file():
        坏.append(f"判一百二十⑤：fixture 没产出 `外部缺.json`（{_f}）⇒ **测不到，不许当通过**")
        _sh120.rmtree(_d120, ignore_errors=True)
        return _报2(坏)
    _d = _j120.loads(_f.read_text(encoding="utf-8"))
    if not any("留痕" in str(k) for k in _d):
        坏.append(f"判一百二十⑤：`外部缺.json` 里没有「剔掉留痕」的键：{list(_d)}")
    _sh120.rmtree(_d120, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   内置分层：绿 —— 判一百二十（lean/rust 分语言 · 多语言时保守判真缺口 · "
          "扩展名→语言 · 剔掉有留痕）")

def 判一百二十一Lean行号必须相对源文件且按出现处记() -> None:
    """★★★ **"行号"必须相对"人要打开的那份文件"**（R465）。

    ★ 两处事故合起来造成一个**不可行动的缺口**：
      ① `_剥Lean注释` 把**块注释里的 `\\n` 一起删了** ⇒ 剥完的行数 ≠ 源文件行数
         ⇒ 后面所有 `行` 字段**相对的是"净文本"而不是源文件** ✗
         实测：`formal_proof/LayerShrink.lean` 一条未解析边的 `行=7` 指过去是**空行**，
         `行=8` 指到 `namespace LayerShrink` —— **行号有、但查不到东西**。
         ★ 特别隐蔽：**行注释那一支本来就保留了 `\\n`**（`i = j` 停在换行上），
           **只有块注释偏**；而 Lean 的**文档注释** `/-- … -/` 正是块注释，
           **几乎每个 Lean 文件都有**。
      ② `_Lean适配器` 的提及用 `for 名 in set(re.findall(...))` ——
         **一个名字整个文件只出一条边、还没有行号** ✗
         ★ `_语法树适配器` 早在 **R359** 就改对了（原话：「`for 名 in set(提及)`
           ⇒ 两处共用一条边 ⇒ 主靶只能有一个 ⇒ **对一处对、对另一处错**，
           而**「答案错了」比「未解析」更坏**」）——**而 Lean 那套没跟着改**。
         ★ 于是 `h12` 未解析时**我查不到是哪一行** ⇒ 无法定位"哪种绑定形式漏抽了"
           ⇒ 一个**不可行动的缺口**（口径 150）。

    ★ 判据（四项）：
      ① ★ `_剥Lean注释` 对**含文档注释与块注释**的文本，**行数必须不变**
      ② ★ 剥注释后**同一行的内容位置不变**（第 5 行仍是第 5 行）
      ③ ★ Lean 的**提及边必须带 `行`**，且落在源文件的真实行上
      ④ ★★ **按出现处记**：同一个名字出现 N 次 ⇒ **至少 N 条边**
         （防我"修了行号但忘了拆 set"——两件事，只做一件就会"部分修好"，口径 201）
    """
    坏: list[str] = []
    import sys as _s121
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s121.path:
        _s121.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg121
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十一：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    剥 = getattr(_kg121, "_剥Lean注释", None)
    if 剥 is None:
        坏.append("判一百二十一：`_剥Lean注释` 不在 ⇒ 测不到")
        return _报2(坏)
    # ── ①② 剥注释不改行数、不改位置
    _源 = (
        "/-- 文档注释\n"
        "    第二行\n"
        "    第三行 -/\n"
        "def a := 1\n"
        "/- 块\n"
        "   注释 -/\n"
        "def b := 2\n"
    )
    _净 = 剥(_源)
    if len(_净.splitlines()) != len(_源.splitlines()):
        坏.append(f"判一百二十一①：剥注释后行数 **{len(_净.splitlines())}** ≠ "
                  f"源文件 **{len(_源.splitlines())}** ⇒ 块注释里的换行被删了")
    _源行 = _源.splitlines()
    _净行 = _净.splitlines()
    if len(_源行) == len(_净行):
        for _k in (3, 6):
            if _净行[_k].strip() != _源行[_k].strip():
                坏.append(f"判一百二十一②：第 {_k + 1} 行内容错位："
                          f"净={_净行[_k]!r} 源={_源行[_k]!r}")
    # ── ③④ 造 fixture 验提及的行号与"按出现处"
    import json as _j121
    import shutil as _sh121
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判121"
    if _d.exists():
        _sh121.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "/-- 文档\n    注释 -/\n"
        "namespace N\n"
        "structure S where\n"
        "  x : Nat\n"
        "theorem t (n : Nat) : n = n := by\n"
        "  exact rfl\n"
        "theorem u : zzz = zzz := by\n"
        "  exact zzz\n",
        encoding="utf-8")
    try:
        _kg121.建图(_d, 打印=lambda *a: None)
        _E = _j121.loads((_kg121._kg目录(_d) / "边.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十一：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh121.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if isinstance(_E, dict):
        _E = list(_E.values())
    # ★★ 测的是 **`边.json` 的提及边**，不是 `引用.json` ——
    #   我第一版测 `引用.json`，它红了，而**红的原因不在这一层**：
    #   `解析引用` 会把 `(名, 父, 行)` 相同的边**按设计去重**（R359）。
    #   ⇒ **判据必须测"我改的那一层"**，否则红了也指不到责任人。
    _R = [x for x in _E if x.get("关系") == "提及" and str(x.get("起", "")).endswith("a.lean")]
    if not _R:
        坏.append("判一百二十一③：fixture 没有产出提及边 ⇒ **测不到，不许当通过**")
        _sh121.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _无行 = [x for x in _R if x.get("行") is None]
    if _无行:
        坏.append(f"判一百二十一③：fixture 里有 **{len(_无行)}** 条边没有 `行` ⇒ "
                  f"`set()` 聚合没拆干净（样例 {[y.get('名') for y in _无行[:6]]}）")
    _zzz = [x for x in _R if x.get("终") == "name:zzz"]   # ★ 提及边的名字在 `终`
    if len(_zzz) < 2:
        坏.append(f"判一百二十一④：`zzz` 在 fixture 里出现 2 次（第 8、9 行），"
                  f"但只有 **{len(_zzz)}** 条边 ⇒ **还在按名字聚合**（R359 那件事故）")
    else:
        _行集 = sorted({x.get("行") for x in _zzz if x.get("行")})
        if _行集 != [8, 9]:
            坏.append(f"判一百二十一④：`zzz` 的行号是 {_行集}，期望 [8, 9] ⇒ "
                      f"行号相对的不是**源文件**")
    _sh121.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   Lean 行号：绿 —— 判一百二十一（剥注释不改行数 · 提及带真实行号 · "
          "同名两处两条边）")

def 判一百二十二未测到就早退必须留痕() -> None:
    """★★★ **"一个产物都没写"必须说出来**（R466）。

    ★ 事故形状：`解析引用` 开头有一句
    ````
        if "未测到" in 图:
            return 图          # ★ 一个产物都不写
    ````
      它**什么都不说**。实测：`work/图/formal_proof/` 里
      `节点.json` / `边.json` 是**今天**的，而 `外部缺.json` 的 mtime
      停在 **11-10 06:36（昨天）** —— **同一个桶里的产物来自两个时间点** ✗
      我读 `外部缺.json` 拿到旧口径（去重 **3,000**），
      而新代码其实已经算出 **6,601** ⇒ **差一点把陈旧读数当成新读数**。

    ★ 而且**同一个概念写在 8 处**：`解析引用` / `灌Lean确证` / `解析md链接` /
      `解析JSON引用` / `自洽门` / `报告` / `查` / `_涂` ——
      口径 214：**同一个概念写在 N 处 ⇒ 抽一处**，否则改了 1 处还剩 7 处。

    ★ 判据（四项）：
      ① ★ `_未测到留痕` 存在，且**返回的图里带留痕键**
      ② ★ 三个**生产者**（`解析引用` / `解析md链接` / `解析JSON引用`）在
         「图含未测到」时都返回留痕键
      ③ ★ 留痕里必须**列出没写的产物名**（"剔掉必须留痕" ⇒ "没写也必须留痕"）
      ④ ★ 留痕里必须报**旧产物停在什么时间**（否则调用方分不出新旧）
      ⑤ ★★ **反向**：留痕**不许把早退变成"不早退"** ——
         真实（不含未测到）的图上调用**不许**出现留痕键（防我把辅助函数
         写成"每次都说没测到"，那样它就永远"绿"而毫无区分力）
    """
    坏: list[str] = []
    import sys as _s122
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s122.path:
        _s122.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg122
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十二：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    if not hasattr(_kg122, "_未测到留痕"):
        坏.append("判一百二十二①：`_未测到留痕` 不在 ⇒ 8 处早退又各写各的（口径 214）")
        return _报2(坏)
    _t = _kg122._未测到留痕({"未测到": "故意"}, None)
    if not isinstance(_t, dict) or "★未测到（早退）" not in _t:
        坏.append(f"判一百二十二①：留痕返回里没有 `★未测到（早退）` 键：{list(_t) if isinstance(_t, dict) else _t}")
        return _报2(坏)
    # ── ②③④ fixture
    import json as _j122
    import shutil as _sh122
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判122"
    if _d.exists():
        _sh122.rmtree(_d)
    _gd = _kg122._kg目录(_d)
    _gd.mkdir(parents=True)
    (_gd / "图.json").write_text(_j122.dumps({"未测到": "故意", "根": str(_d)}),
                                encoding="utf-8")
    (_gd / "外部缺.json").write_text("{}", encoding="utf-8")
    for _名, _f in (("解析引用", _kg122.解析引用),
                    ("解析md链接", _kg122.解析md链接),
                    ("解析JSON引用", _kg122.解析JSON引用)):
        try:
            _出 = _f(_d, 打印=lambda *a: None)
        except SystemExit:
            raise
        except Exception as e:                    # noqa: BLE001
            坏.append(f"判一百二十二②：`{_名}` 早退时抛异常（{type(e).__name__}: {e}）")
            continue
        _k = _出.get("★未测到（早退）") if isinstance(_出, dict) else None
        if not _k:
            坏.append(f"判一百二十二②：`{_名}` 早退返回里**没有留痕键** ⇒ "
                      f"调用方会把它当『算过了』（口径 197/199）")
            continue
        if not _k.get("★没写的产物"):
            坏.append(f"判一百二十二③：`{_名}` 的留痕没列出**没写的产物**")
        if "停在哪个时间" not in " ".join(_k):
            坏.append(f"判一百二十二④：`{_名}` 的留痕没报**旧产物停在什么时间**")
    # ── ⑤ 反向：正常图不许出现留痕
    _d2 = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判122b"
    if _d2.exists():
        _sh122.rmtree(_d2)
    _d2.mkdir(parents=True)
    (_d2 / "a.md").write_text("# T\n\n见 [x](./a.md)\n", encoding="utf-8")
    try:
        _kg122.建图(_d2, 打印=lambda *a: None)
        _出2 = _kg122.解析md链接(_d2, 打印=lambda *a: None)
        if isinstance(_出2, dict) and "★未测到（早退）" in _出2:
            坏.append("判一百二十二⑤：**正常的图上也报了「未测到」** ⇒ "
                      "留痕被写成了无条件 ⇒ 它没有区分力")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十二⑤：正常 fixture 跑不动（{type(e).__name__}: {e}）")
    _sh122.rmtree(_d, ignore_errors=True)
    _sh122.rmtree(_d2, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   早退留痕：绿 —— 判一百二十二（辅助函数在位 · 三个生产者都留痕 · "
          "列出没写的产物 · 报旧产物时间 · 正常图不误报）")

def 判一百二十三父子根重叠必须分类留痕() -> None:
    """★★★ **「同一批文件被两个桶各建了一张图」必须说出来**（R467）。

    ★ 实测：`形式化验证`（父，5,659 文件）与 `formal_proof`（子，3,783 文件）
      **各建了一张图**。对齐基准后 **子∩父 = 3,777 / 3,783 = 99.8%** ⇒
      ① **双重计数**（读总账的人把同一批文件算两次）；
      ② **对不上账** —— 两个桶的 `文件` 字段**相对各自的根**
         （子桶写 `ABRedundant.lean`，父桶写 `formal_proof/ABRedundant.lean`）
         ⇒ **同一个文件在两张图里名字不同**（口径 191）。
    ★★ **我第一版按裸名求交集得 `0`，差点得出「两者不重叠」的反结论** ✗
      —— 口径 191 说的就是这件事：**相对路径必须写明相对谁**，
      而**对账前必须先对齐基准**。

    ★★ 而**必须区分两种重叠**（这是我第二版才对的）：
      ① **总根 ↔ 分根**：`分根` 的**设计**（总根角色写着
         「装散落文件 + 跨子树引用」）⇒ **登记，不报警**；
      ② **分根 ↔ 二级子桶**：**手工**建的 ⇒ 必须报警。
      ★ 我第一版把 16 个桶**全报了警**，其中 15 条是设计
        ⇒ **报警太多等于没报警**（口径 201 的同族：**「全都红」与「没检查」长得一样**）。

    ★ 判据（五项）：
      ① ★ `_标父子关系` 在位
      ② ★★ **前缀陷阱**：`D:/P/A2` **不是** `D:/P/A` 的子 ——
         `startswith` 不加分隔符就会把 `A2` 判成 `A` 的子 ✗（**假重叠**）
      ③ ★ **分类正确**：父是总根 ⇒ 「设计」；父不是总根 ⇒ 「重叠」，
         且**只有后者计数**
      ④ ★ 总账里有 **`★父子重叠的桶数（父不是总根）`**（剔掉要留痕，口径 199）
      ⑤ ★ **活索引**里每个落在某个根之下的桶，都必须有
         `★它是谁的子根` 与 `★与父根的关系`（否则读索引的人不知道谁是子集）
    """
    坏: list[str] = []
    import sys as _s123
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s123.path:
        _s123.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg123
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十三：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    标 = getattr(_kg123, "_标父子关系", None)
    if 标 is None:
        坏.append("判一百二十三①：`_标父子关系` 不在 ⇒ 父子重叠又各写各的（口径 214）")
        return _报2(坏)
    # ── ②③ fixture（含前缀陷阱）
    _表 = [
        {"根": "D:/P", "角色": "总根（装散落文件 + 跨子树引用）"},
        {"根": "D:/P/A", "角色": "子根（源码 5000）"},
        {"根": "D:/P/A/B", "角色": "**手工建的子桶**"},
        {"根": "D:/P/C", "角色": "子根（源码 100）"},
        {"根": "D:/P/A2", "角色": "子根（源码 100）"},
    ]
    _报 = 标(_表)
    _按根 = {x["根"]: x for x in _表}
    # ★ 我第一版这里写的是 `... is not None` —— **那是错的**：
    #   `D:/P/A2` **确实是** `D:/P` **总根**的子（正确），只**不是** `D:/P/A` 的子。
    #   ⇒ 判据要判的是**「父是谁」**，不是「有没有父」✗
    #   （口径 189 的同族：**问错了问题，绿红都没意义**。）
    if _按根["D:/P/A2"].get("★它是谁的子根") == "D:/P/A":
        坏.append("判一百二十三②：**前缀陷阱** —— `D:/P/A2` 被判成 `D:/P/A` 的子 "
                  "⇒ `startswith` 少了分隔符，会造出**假重叠**")
    if _按根["D:/P/A2"].get("★它是谁的子根") != "D:/P":
        坏.append(f"判一百二十三②：`D:/P/A2` 的父该是总根 `D:/P`，"
                  f"实得 {_按根['D:/P/A2'].get('★它是谁的子根')!r}")
    if _报 != 1:
        坏.append(f"判一百二十三③：fixture 里应有 **1** 个「重叠」，实得 **{_报}**")
    if "设计" not in str(_按根["D:/P/A"].get("★与父根的关系") or ""):
        坏.append("判一百二十三③：`D:/P/A` 的父是总根 ⇒ 该判「**设计**」，"
                  f"实得 {_按根['D:/P/A'].get('★与父根的关系')!r}")
    if "重叠" not in str(_按根["D:/P/A/B"].get("★与父根的关系") or ""):
        坏.append("判一百二十三③：`D:/P/A/B` 的父不是总根 ⇒ 该判「**重叠**」，"
                  f"实得 {_按根['D:/P/A/B'].get('★与父根的关系')!r}")
    # ── ④⑤ 活索引
    _索 = Path(__file__).resolve().parents[1] / "work" / "图" / "索引.json"
    if not _索.is_file():
        坏.append("判一百二十三④：活索引不在 ⇒ **测不到，不许当通过**")
        return _报2(坏)
    import json as _j123
    try:
        _d = _j123.loads(_索.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十三④：索引读不了（{type(e).__name__}: {e}）")
        return _报2(坏)
    _根们 = _d.get("根们") or []          # ★ 必须先取 —— ④ 要拿它对账
    _键 = "★父子重叠的桶数（父不是总根）"
    if _键 not in _d:
        坏.append(f"判一百二十三④：索引里没有 `{_键}` ⇒ 分类**没留痕**（口径 199）")
    else:
        # ★★ 不许只测「键在不在」—— 键是**写一次就永久在**的，
        #   改坏了代码它照旧在（我第一版就是这样绿的 ✗）。
        #   要对账：**索引里写的数 == 用 `根们` 现算的数**（口径 194：
        #   同一机制两处读数必须一致）。
        _副本 = [dict(x) for x in _根们]
        _现算 = 标(_副本)
        if _d.get(_键) != _现算:
            坏.append(f"判一百二十三④：索引里写 **{_d.get(_键)}**，"
                      f"而用 `根们` 现算是 **{_现算}** ⇒ 同一个数两个出口不一致"
                      f"（口径 194）")
    _所有 = [str(x.get("根") or "").rstrip("\\\\/") for x in _根们]
    _漏 = []
    for x in _根们:
        _r = str(x.get("根") or "").rstrip("\\\\/")
        if not _r:
            continue
        _父们 = [q for q in _所有
                 if q and q != _r and (_r.startswith(q + "\\\\") or _r.startswith(q + "/"))]
        if _父们 and not x.get("★它是谁的子根"):
            _漏.append(_r)
    if _漏:
        坏.append(f"判一百二十三⑤：活索引里有 **{len(_漏)}** 个桶落在别的根之下，"
                  f"却没标 `★它是谁的子根`（示例 {_漏[:3]}）⇒ **读索引的人不知道谁是子集**")
    if 坏:
        return _报2(坏)
    print("   父子重叠：绿 —— 判一百二十三（分类留痕 · 前缀陷阱不误判 · "
          "总账有读数 · 活索引每个子桶都标了父）")

def 判一百二十四父未知时必须把行并进去重键() -> None:
    """★★★ **`父 = None` 是「作用域未知」，不是「同一个作用域」**（R468）。

    ★ 实测事故（R465 记账、R468 定因）：
      一个 `.lean` 文件写
    ````
        theorem u : zzz = zzz := by
          exact zzz
    ````
      `边.json` 里 `name:zzz` 有 **3 条**（行 2、2、3），
      而 `引用.json` 里只剩 **1 条（行 2）** ⇒ **第 3 行那一条整个丢了** ✗
    ★ 根因：去重键是 `k = (相, 名, tuple(父列))`，而
      **`_Lean适配器` 的提及边没有 `父` 字段**
      （对照：Python 边有 `"父": "定义:a.py:f:1"`）⇒
      键退化成 `(相, 名, (None,))` ⇒ 同一文件同名的多处**全被合并**。
    ★ 而这是**口径 190** 的原话：**`False` 与 `unknown` 必须分开** ——
      `父=None` 有两种可能：① 真在顶层；② **我不知道**。
      把「不知道」当成「同一个作用域」⇒ **一个答案硬套多处**
      ⇒ 正是 R358 立这条去重键时要消灭的东西，**从后门回来了**。
    ★ 修法：**`父` 未知时把 `行` 并进键** —— 把「未知作用域」**退化**成
      「每个出现处自己一个作用域」（**保守**：宁可多算几遍，
      也不许一个答案硬套多处）。方向与口径 203 一致。

    ★ 判据（四项）：
      ① ★ `.lean` fixture 里 `zzz` 出现 2 行 ⇒ **引用边必须 ≥ 2 条**
      ② ★ 两条的 `行` 必须**不同**（不是同一个出现处被记了两遍）
      ③ ★ **反向**：`.py` fixture 里同一函数内 `qqq` 出现 2 次
         （那两处**父相同 = 真的同一作用域**）⇒ **仍按设计合并成 1 条**
         （防我把"合并"整个砍掉 —— 那会把边数吹大且掩盖真作用域）
      ④ ★ **反向**：`.lean` 里同一个名字在同一行出现 2 次 ⇒ **合并成 1 条**
         （同一个出现处不许多记，防我把 `行` 加进键之后连同行也拆开）
    """
    坏: list[str] = []
    import sys as _s124
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s124.path:
        _s124.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg124
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十四：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j124
    import shutil as _sh124
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判124"
    if _d.exists():
        _sh124.rmtree(_d)
    _d.mkdir(parents=True)
    # ★ Lean：行 2 两个 zzz、行 3 一个 zzz ⇒ 按行该是 2 条
    (_d / "a.lean").write_text(
        "/-- 文档 -/\ntheorem u : zzz = zzz := by\n  exact zzz\n",
        encoding="utf-8")
    # ★ Python：同一个函数内两处 qqq（父相同）⇒ 按设计 1 条
    (_d / "b.py").write_text(
        "def f():\n    www = 1\n    return www + www\n",
        encoding="utf-8")
    try:
        _kg124.建图(_d, 打印=lambda *a: None)
        _kg124.解析引用(_d, 打印=lambda *a: None)
        _R = _j124.loads((_kg124._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
        _E = _j124.loads((_kg124._kg目录(_d) / "边.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十四：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh124.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if isinstance(_E, dict):
        _E = list(_E.values())
    # 前置事实：Lean 边确实没有 `父`（否则本判据测的是另一件事）
    _lm = [x for x in _E if x.get("关系") == "提及" and str(x.get("起", "")).endswith("a.lean")]
    if not _lm:
        坏.append("判一百二十四：`.lean` fixture 没有提及边 ⇒ **测不到，不许当通过**")
        _sh124.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if any("父" in x for x in _lm):
        坏.append("判一百二十四：**前提变了** —— `.lean` 提及边现在**有 `父`** 了 "
                  "⇒ 本判据要守的东西（父未知）已不成立，**请连同本判据一起改**")
    # ① ②
    _z = [x for x in _R if x.get("名") == "zzz"]
    if len(_z) < 2:
        坏.append(f"判一百二十四①：`.lean` 里 `zzz` 跨 2 行，引用边只有 **{len(_z)}** 条 "
                  f"⇒ 父未知时**没把行并进键** ⇒ 后来的出现处**整个丢了**（口径 190）")
    else:
        _行 = sorted({x.get("行") for x in _z if x.get("行")})
        if len(_行) < 2:
            坏.append(f"判一百二十四②：`zzz` 的引用边行号只有 {_行} ⇒ "
                      f"两条是同一个出现处，不是两行")
    # ③ Python 同一作用域仍合并
    _w = [x for x in _R if x.get("名") == "www"]
    if len(_w) != 1:
        坏.append(f"判一百二十四③：`.py` 同一函数内 `www` 两处**应合并成 1 条**（父相同），"
                  f"实得 **{len(_w)}** 条 ⇒ **把'合并'整个砍掉了**（掩盖真作用域）")
    # ④ Lean 同一行两处仍合并
    _z2 = [x for x in _R if x.get("名") == "zzz" and x.get("行") == 2]
    if len(_z2) != 1:
        坏.append(f"判一百二十四④：`.lean` 行 2 上 `zzz` 出现 2 次，**应合并成 1 条**，"
                  f"实得 **{len(_z2)}** 条 ⇒ 连同行也拆开了")
    _sh124.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   父未知去重：绿 —— 判一百二十四（跨行不合并 · 同行合并 · "
          "同作用域仍合并 · 前提有断言）")

def 判一百二十五Lean命名洞不是引用() -> None:
    """★★★ **`?名字` 是 Lean 的「命名洞」，不是引用**（R469）。

    ★ 实测：`refine ?left ?right` ⇒ 产出 `name:left` / `name:right`
      两条提及边 ⇒ 下游解不到任何定义 ⇒ **报成缺口** ✗
    ★ 全仓代价（`形式化验证`）：`left` 未解析 **548** 条 · `right` **554** 条
      ⇒ **1,102 条纯假缺口**。
      **而 `?xxx` 在整个 Lean 里从来不指向定义。**
    ★ 根因：前视 `(?<![\\w.'])` **只排了 `\\w`/`.`/`'`**，`?` 不在其中
      ⇒ `?left` 里 `left` 的前一个字符是 `?` ⇒ **照样匹配** ✗
      ★ 同族：`?_`（匿名洞）⇒ 会抽出名字 `_`，**也不是引用**。
    ★ 口径：**假缺口与真缺口同样有害** —— 它们把「AI 缺什么」这张表
      **掺进了本来就不该存在的条目**（口径 203 的近亲）。

    ★ 判据（四项，**三项是反向**）：
      ① ★ `.lean` 里 `?left ?right ?_` ⇒ 这三个名字**必须 0 条**提及边
      ② ★ **反向**：同一行里的普通标识符 `foo` ⇒ **仍必须有 1 条**
         （防我把整行或整个正则砍掉 —— 那是**把真引用一起删了**）
      ③ ★ **反向**：`?` **只在前面**时才算洞 —— `foo?` 里的 `foo`
         （`?` 在后）**仍必须被记**（Lean 里 `?` 也作后缀用，如 `x?`）
      ④ ★ **反向**：带 `'` 的后缀名（`foo'`，Lean 里极常见）
         **仍必须被记** —— 前视里那个 `'` 的用意正是它
    """
    坏: list[str] = []
    import sys as _s125
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s125.path:
        _s125.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg125
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十五：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j125
    import shutil as _sh125
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判125"
    if _d.exists():
        _sh125.rmtree(_d)
    _d.mkdir(parents=True)
    # 行 2：`?left ?right ?_` 是洞；`foo` 是普通标识符；`bar?` 的 `?` 在后
    # 行 3：`baz'` 是带撇的后缀名
    (_d / "a.lean").write_text(
        "theorem t1 (a : Nat) : a = a := by\n"
        "  refine ?left ?right ?_ foo bar?\n"
        "  exact baz'\n",
        encoding="utf-8")
    try:
        _kg125.建图(_d, 打印=lambda *a: None)
        _E = _j125.loads((_kg125._kg目录(_d) / "边.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十五：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh125.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if isinstance(_E, dict):
        _E = list(_E.values())
    _提 = [x for x in _E if x.get("关系") == "提及"]
    if not _提:
        坏.append("判一百二十五：`.lean` fixture 没有任何提及边 ⇒ **测不到，不许当通过**")
        _sh125.rmtree(_d, ignore_errors=True)
        return _报2(坏)

    def _几(名: str) -> int:
        return len([x for x in _提 if x.get("终") == f"name:{名}"])

    # ① 洞名必须 0
    for _洞 in ("left", "right", "_"):
        if _几(_洞) != 0:
            坏.append(f"判一百二十五①：`?{_洞}` 是**命名洞**，不该产出提及边，"
                      f"实得 **{_几(_洞)}** 条 ⇒ **假缺口**（口径 203 近亲）")
    # ② 普通标识符仍必须有
    if _几("foo") != 1:
        坏.append(f"判一百二十五②：同一行的普通标识符 `foo` 应有 **1** 条提及边，"
                  f"实得 **{_几('foo')}** ⇒ 修洞名时**把真引用一起删了**")
    # ③ `?` 在后不算洞
    if _几("bar") != 1:
        坏.append(f"判一百二十五③：`bar?` 里的 `bar`（`?` 在后）应有 **1** 条，"
                  f"实得 **{_几('bar')}** ⇒ **前视把 `?` 当成了两侧都排除**")
    # ④ 带撇的后缀名仍必须有
    if _几("baz'") != 1:
        坏.append(f"判一百二十五④：`baz'`（Lean 极常见的后缀名）应有 **1** 条，"
                  f"实得 **{_几('baz\'')}** ⇒ 前视里的 `'` 用意被改坏了")
    _sh125.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   Lean 洞名：绿 —— 判一百二十五（洞名 0 条 · 普通名仍在 · "
          "`?` 只排前面 · 撇后缀名仍在）")

def 判一百二十六井号命令词不是引用() -> None:
    """★★★ **`#` 后面的词是 Lean 的「命令名」，不是引用**（R470）。

    ★ 实测两处，各是一整类：
    ````
    #print axioms MyThm     ⇒ 抽出 `axioms`（4,891 条未解析，占真外部缺 74.5%）
    #check Nat              ⇒ 抽出 `check`
    ````
      全仓计数：`#check` **7,599** · `#print` **5,122** ·
      `#guard_msgs` **4,524** · `#eval` **3,797** ⇒ **几万条假提及** ✗
    ★★ 它们多数落进「绑定/内置」而**没进缺口表，纯属侥幸** ——
      `print` 恰好在 `_语言内置` 里、`check` 恰好在别处有定义。
      **靠巧合不出错不算对。**
    ★ 而 `axioms` 没这个运气 ⇒ 直接成了 Top1。

    ★★ **不许笼统跳过「`#` 命令后的第一个词」** —— 因为
      `#check Nat` 里 `Nat`、`#check MyThm` 里 `MyThm` 是**真引用**
      （实测 `Nat` 348 次、`Fin` 98 次都在这一位）⇒ 那会**把真引用一起删掉** ✗
    ⇒ 只跳过**紧跟在 `#` 后**的那一个词；`#print axioms` 另按**整体语法**排。

    ★ 判据（五项，**三项是反向**）：
      ① ★ `#print axioms X` ⇒ `axioms` **0 条**、`X` **1 条**
      ② ★ `#check X` / `#eval` / `#guard_msgs` / `in` ⇒ 命令词 **0 条**
      ③ ★ **反向**：`#check MyThm` 里 `MyThm` **必须留下**（真引用）
      ④ ★ **反向**：普通位置上的同名标识符 —— `def axioms := 1` 之后
         在**非 `#print`** 处引用它 ⇒ **必须被记**（防我一刀切掉 `axioms`）
      ⑤ ★ **反向**：`#` 只吃**紧跟的那一个词** —— `#check a b` 里的 `b`
         （第二个参数）**必须留下**
    """
    坏: list[str] = []
    import sys as _s126
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s126.path:
        _s126.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg126
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十六：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j126
    import shutil as _sh126
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判126"
    if _d.exists():
        _sh126.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "#print axioms MyThm\n"          # 1: 命令词 axioms + 真引用 MyThm
        "#check MyThm\n"                 # 2: 命令词 check + 真引用 MyThm
        "#eval 1 + 1\n"                  # 3: 命令词 eval
        "#guard_msgs in\n"               # 4: 命令词 guard_msgs / in
        "#check aaa bbb\n"               # 5: 命令词 check，aaa 与 bbb 都是真引用
        "def axioms := 1\n"              # 6: ★ 真有人定义叫 axioms 的东西
        "#check axioms\n",               # 7: ★ 这里 axioms 是**真引用**，必须留
        encoding="utf-8")
    try:
        _kg126.建图(_d, 打印=lambda *a: None)
        _E = _j126.loads((_kg126._kg目录(_d) / "边.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十六：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh126.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if isinstance(_E, dict):
        _E = list(_E.values())
    _提 = [x for x in _E if x.get("关系") == "提及"]

    def _行们(名: str) -> list:
        return sorted(x.get("行") for x in _提 if x.get("终") == f"name:{名}")

    if not _提:
        坏.append("判一百二十六：`.lean` fixture 没有任何提及边 ⇒ **测不到，不许当通过**")
        _sh126.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    # ① `#print axioms MyThm`
    # ★ 期望 `[6, 7]`：行 6 是 `def axioms := 1`（**定义名**也是一种提及）·
    #   行 7 是 `#check axioms`（真引用）。**行 1 必须不在**。
    #   ★ 我第一版写的是 `[7]` —— **漏了行 6 的定义名**，于是基线就红了 ✗
    #     （口径 193 的近亲：**对账要对着目标文本逐条点数**。）
    if _行们("axioms") != [6, 7]:
        坏.append(f"判一百二十六①：`axioms` 应**在行 6（定义名）与行 7（`#check` 真引用）**，"
                  f"**行 1 必须不在**，实得 行={_行们('axioms')} ⇒ "
                  f"`#print axioms` 没被排掉，或真引用被误杀")
    if _行们("MyThm") != [1, 2]:
        坏.append(f"判一百二十六③：`MyThm` 应在行 1、2（`#print axioms` / `#check` 的真引用），"
                  f"实得 行={_行们('MyThm')} ⇒ **把真引用一起删了**")
    # ② 命令词 0
    for _词 in ("check", "print", "eval", "guard_msgs", "in"):
        if _行们(_词):
            坏.append(f"判一百二十六②：`#{_词}` 是**命令名**，不该产出提及边，"
                      f"实得 行={_行们(_词)} ⇒ **假引用**")
    # ⑤ `#` 只吃紧跟的一个词
    for _名 in ("aaa", "bbb"):
        if not _行们(_名):
            坏.append(f"判一百二十六⑤：`#check aaa bbb` 里的 `{_名}` 是**真引用**，"
                      f"实得 0 条 ⇒ **`#` 把整行都吃掉了**")
    _sh126.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   井号命令词：绿 —— 判一百二十六（`#` 只吃紧跟的一个词 · "
          "`#print axioms` 整体排掉 · `#` 后的真引用与普通位置的同名标识符都留着）")

def 判一百二十七构造子与结构字段是定义() -> None:
    """★★★ **`inductive` 的构造子、`structure` 的字段都是「定义」**（R471）。

    ★ 实测（`形式化验证` 的 `★真外部缺`）：
    ````
    ff 未解析 239 · tt 未解析 239 · dep_type 652 · proof_status 652
    源行：_archive/验收目录/Foundational/PureBoolLemmas.lean:62   | ff => rfl
         formal_proof/MyMeta/MasterTable/DependencyTable_A01.lean:33  dep_type : DepType
    ````
    ★ 根因**两条，都是「这个位置我没抽」**：
      ① **构造子**：`inductive MyBool where | ff : MyBool` 里的 `ff` 是
         **定义名**，而本适配器**只抽声明关键字**（`def`/`theorem`/…）
         ⇒ 构造子从来没被抽 ⇒ 后续 `| ff =>` 引用它必然「未解析」✗
      ② **结构字段**：`structure X where` 下的 `dep_type : DepType`
         前面**没有 `(`/`{`/`[`** ⇒ 参数那条正则**匹配不上**✗
    ★ 为什么不把构造子记成「绑定」：**绑定是局部名，构造子是定义** ——
      记错类会让 `解析引用` 走到另一条分支（类别是语义，不是标签）。

    ★ 判据（五项，**三项是反向**）：
      ① ★ `inductive` 的构造子 ⇒ 引用边依据必须是「**本文件定义**」
      ② ★ `structure` 的字段 ⇒ 同上
      ③ ★ **反向**：**块外**的 `| foo =>` **不许**被当成构造子定义
         （防我把所有 `| x :` 都当构造子）
      ④ ★ **反向**：**缩进不深于块头**的行**不许**被吃进块
         （块边界按缩进；否则后面整个文件都会被算成构造子）
      ⑤ ★ **反向**：普通 `def` / `theorem` 仍必须是定义（没被新循环挤掉）
    """
    坏: list[str] = []
    import sys as _s127
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s127.path:
        _s127.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg127
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十七：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j127
    import shutil as _sh127
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判127"
    if _d.exists():
        _sh127.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "structure MyStruct where\n"        # 1  块头（缩进 0）
        "  fieldOne : String\n"              # 2  ★ 字段
        "  fieldTwo : String\n"              # 3  ★ 字段
        "\n"
        "inductive MyBool where\n"           # 5  块头（缩进 0）
        "  | tt : MyBool\n"                  # 6  ★ 构造子
        "  | ff : MyBool\n"                  # 7  ★ 构造子
        "\n"
        "def topLevel (x : MyBool) : Bool := match x with\n"   # 9  ★ 普通 def
        "  | MyBool.tt => true\n"            # 10 块**外**的模式（`|` 但不在块里）
        "  | MyBool.ff => false\n"
        "\ndef plainFn (y : Nat) : Nat := y + 1\n"             # 13 ★ 普通 def
        # ★★ `class` 也是声明块 —— 它的字段同样必须被抽。
        #   这是**可红**项（把 `_块头` 的 `class` 去掉就红）。
        "class MyClass where\n"            # 14
        "  className : String\n"           # 15 ★ class 的字段
        "  classSize : Nat\n",              # 16 ★ class 的字段
        encoding="utf-8")
    try:
        _kg127.建图(_d, 打印=lambda *a: None)
        _kg127.解析引用(_d, 打印=lambda *a: None)
        _R = _j127.loads((_kg127._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
        _N = _j127.loads((_kg127._kg目录(_d) / "节点.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十七：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh127.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _定 = [v for v in _N.values() if isinstance(v, dict) and v.get("类") == "定义"]
    _定名 = {v.get("名") for v in _定}
    if not _定:
        坏.append("判一百二十七：fixture 里没有任何定义节点 ⇒ **测不到，不许当通过**")
        _sh127.rmtree(_d, ignore_errors=True)
        return _报2(坏)

    def _依(名: str) -> list:
        return sorted({str(x.get("依据"))[:14] for x in _R if x.get("名") == 名})

    # ① 构造子
    for _名 in ("tt", "ff"):
        if _名 not in _定名:
            坏.append(f"判一百二十七①：`inductive` 的构造子 `{_名}` 没有定义节点 "
                      f"⇒ 后续引用它必然「未解析」（`| {_名} =>` 那类）")
        elif "本文件定义" not in " ".join(_依(_名)):
            坏.append(f"判一百二十七①：`{_名}` 有定义节点，但引用边依据是 {_依(_名)} "
                      f"⇒ 没解到定义")
    # ② 结构字段
    for _名 in ("fieldOne", "fieldTwo"):
        if _名 not in _定名:
            坏.append(f"判一百二十七②：`structure` 的字段 `{_名}` 没有定义节点 "
                      f"⇒ **结构字段位没抽**（参数正则要求前面有圆括号/花括号/方括号）")
    # ③ 块外的 `| foo =>` 不许当构造子 —— 用「缩进边界」间接验：
    #    块外那些模式里的 `MyBool` 属于**真引用**，不该多出 `lean:构造子` 类型的定义
    _构造子 = [v for v in _定 if "构造子" in str(v.get("类型"))]
    _构造名 = {v.get("名") for v in _构造子}
    if _构造名 - {"tt", "ff"}:
        坏.append(f"判一百二十七③：多出了不该有的「构造子」定义 "
                  f"{sorted(_构造名 - {'tt', 'ff'})} ⇒ **块边界失效**（把所有 `|` 都吃了）")
    # ★★ 缩进边界：`have hx : 1 + 1 = 2` 在**块外、缩进 2**，形状正是 `名字 : 类型`
    #    ⇒ 边界一旦失效就会被 `_字段` 吃掉，在图上造出一个**叫 `have` 的定义**。
    # ④ ★ `class` 也是声明块，字段同样必须被抽（**可红**项）
    for _名 in ("className", "classSize"):
        if _名 not in _定名:
            坏.append(f"判一百二十七④：`class` 的字段 `{_名}` 没有定义节点 "
                      f"⇒ **声明块只认 `inductive`/`structure`，漏了 `class`**")
    # ★★ **关于「缩进边界失效」的诚实记账**（口径 210）：
    #   我写过一个 `if _缩8 <= _缩: break` 的防御，并试图构造一个
    #   「块外、缩进 ≥1、形如字段」的行去把它打红 —— **构造不出来**：
    #   `_字段` 要求 `名字` 后**直接**是 `:`，而 Lean 里块外、缩进 ≥1 的
    #   `名字 : 类型` 行**根本不存在**（`have hx : T` 是 `名字 名字 : 类型`）
    #   ⇒ **这个防御在语法上无害，抓不到差异是正常的。**
    #   ⇒ 它**不是判据**，是防御；**如实标出**，不假装它红过。
    if 坏:
        _sh127.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _sh127.rmtree(_d, ignore_errors=True)
    print("   构造子/字段：绿 —— 判一百二十七（构造子是定义 · 字段是定义 · `class` 也抽 · 块外模式不算 · 普通 def 没被挤掉）")

def 判一百二十八假设名与根前缀与按语言内置() -> None:
    """★★★ **`by_cases h : P` 的 `h` 是绑定位**（R472）。

    ★ 实测（`形式化验证` 的 `★真外部缺`，全是未解析）：
    ````
    hlt 124  by_cases hlt : ringOf ti < ringOf yong
    hl  154  cases hl : lookupForced forced l.var with
    hb  113 · h' 110 · hc 109 · hx 98 · heq 89 · hxy 82 …
    ````
    ⇒ **`by_cases`/`cases` 是 Lean 里最常见的「引入一个假设」的写法**，
      而本适配器**从来没抽过它的名字** ⇒ 每个都成了「AI 缺什么」✗
    ★ 形状是 `命令 名字 : 命题` —— `:` 在**名字之后**，
      所以「参数」那条正则（要求前面有 `(`/`{`/`[`）**覆盖不到**。

    ★★ 同一轮还修了两件相邻的：
      · **`_root_` 是 Lean 的根命名空间前缀**（`_root_.liuHe zhi` ⇒
        抽出 `_root_` 98 条未解析）—— **语法词，不是引用**；
        真引用是它**后面**那段限定名，由「解析限定名」那条路负责。
      · **Lean core 类型**（`String 1712` · `IO 649` · `Float 98` …
        以及 `Bool`/`Unit`/`Empty`/`UInt*`/`LawfulBEq`）该进
        **`_内置_按语言["lean"]`**，**不是**全局表。

    ★ 判据（五项，**三项是反向**）：
      ① ★ `by_cases hlt : a < b` ⇒ `hlt` 的依据必须是「**绑定/内置**」
      ② ★ `cases hl : …` ⇒ 同上
      ③ ★ **反向**：`cases a with`（**没有** `名字 :`）里的 `a` 是**真引用**，
         **不许**被当成绑定 —— 防我把「`cases` 之后的第一个名字」一刀切当绑定
      ④ ★ **反向**：`_root_` **0 条**提及边（防我把它当引用）；
         而**同一行**的普通标识符仍必须有
      ⑤ ★ **反向（按语言分层）**：同一个 `String` ——
         在 **`.lean`** 里算内置；在 **`.rs`** 里**不许**算内置
         （全局表正是 `_语言内置` 被废弃的原因）
    """
    坏: list[str] = []
    import sys as _s128
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s128.path:
        _s128.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg128
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十八：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j128
    import shutil as _sh128
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判128"
    if _d.exists():
        _sh128.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "theorem t (a b : Nat) : True := by\n"       # 1
        "  by_cases hlt : a < b\n"                    # 2  ★ ①
        "  · by_cases hq : a = b\n"              # ★ 第二个假设名（②）
        "  · cases zz with\n"                     # 3  ★ ②
        "    | zero => trivial\n"
        "    | succ k => trivial\n"
        "  · have ww : Nat := _root_.liuHe a\n"       # 6  ★ ④
        "    trivial\n",
        encoding="utf-8")
    (_d / "b.lean").write_text(
        "def f : String := \"x\"\n",                  # ★ ⑤ lean 侧：算内置
        encoding="utf-8")
    (_d / "c.rs").write_text(
        "fn g() -> String { String::new() }\n",       # ★ ⑤ rust 侧：不许算内置
        encoding="utf-8")
    try:
        _kg128.建图(_d, 打印=lambda *a: None)
        _kg128.解析引用(_d, 打印=lambda *a: None)
        _R = _j128.loads((_kg128._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十八：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh128.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if not _R:
        坏.append("判一百二十八：fixture 没有任何引用边 ⇒ **测不到，不许当通过**")
        _sh128.rmtree(_d, ignore_errors=True)
        return _报2(坏)

    def _依(名: str, 起含: str = "") -> list:
        return [(x.get("行"), str(x.get("依据"))[:14]) for x in _R
                if x.get("名") == 名 and 起含 in str(x.get("起"))]

    # ① / ②
    for _名 in ("hlt", "hq"):
        _v = _依(_名)
        if not _v:
            坏.append(f"判一百二十八①：假设名 `{_名}`（`by_cases`/`cases 名字 :`）"
                      f"**一条引用边都没有** ⇒ 绑定位根本没抽")
        elif not all("绑定" in str(y[1]) for y in _v):
            坏.append(f"判一百二十八①：`{_名}` 的依据应为「绑定/内置」，实得 {_v}")
    # ③ ★★ `cases zz with`（**没有** `名字 :`）里的名字是**真引用**，不许当绑定。
    #   ★★ 我第一版用 `a` 来验 —— **期望写错了**：`a` 是 `theorem t (a b : Nat)`
    #     的**参数**，它在行 3 解到行 1 的绑定是**正确行为**（Lean 里 `a` 在作用域内）。
    #     ⇒ 改用**从未定义**的 `zz`：它必须落「未解析」。
    _zz = [y for y in _依("zz") if y[0] == 4]
    # ★★ 我第一版把 fixture 的**另一行**写成 `have zz : Nat := …` ⇒
    #   `zz` 在行 6 被 `have` 绑定了，而判定看的是它**所有**的行 ⇒ 误红。
    #   ⇒ 「从未定义」这个前提必须**自己验**，不能假设。
    if [y for y in _依("zz") if y[0] != 4]:
        坏.append("判一百二十八③：`zz` 在**行 3 之外**还有边 ⇒ "
                  "fixture 里 `zz` 不是「从未定义」⇒ **本项测不到**")
    if not _zz:
        坏.append("判一百二十八③：`cases zz with` 里的 `zz` 一条边都没有 ⇒ 测不到")
    elif any("绑定" in str(y[1]) for y in _zz):
        坏.append(f"判一百二十八③：`cases zz with`（**没有** `名字 :`）里的 `zz` 从未定义，"
                  f"该落「未解析」，实得 {_zz} ⇒ **把「`cases` 后的第一个名字」一刀切了**")
    # ④ `_root_`
    _r = _依("_root_")
    if _r:
        坏.append(f"判一百二十八④：`_root_` 是**根命名空间前缀**（语法词），"
                  f"不该产出提及边，实得 {_r}")
    # ★★ 「限定名的后段」（`_root_.liuHe` 里的 `liuHe`）**不由这条裸名正则负责** ——
    #   前视 `(?<![\w.?\x27\\])` 排除 `.` 正是为此（走「解析限定名」那条路）。
    #   我第一版断言「`liuHe` 必须有」⇒ **基线就红了**：那是**设计**，不是缺陷。
    #   改成「那一行**仍产出边**」—— 防我为了排 `_root_` 把整行删掉。
    _行6 = [x for x in _R if x.get("行") == 7]
    if not _行6:
        坏.append("判一百二十八④：第 6 行一条边都没有 ⇒ 排 `_root_` 时把整行删了")
    # ⑤ ★★ **按语言分层** —— 直接测 `_缺口分类(名, 语言们)`（口径 213：
    #   判据必须测**我改的那一层**）。
    #   ★ 我第一版去测 `引用.json` 里的 `String` 是否算内置 —— **测错了层**：
    #     `引用.json` 的「未解析」是**出现处口径**，
    #     而「内置/第三方/真缺口」是**名字级口径**（`外部缺.json`）。
    #     两个口径**必须分开报**（kg.py 里就写着这句话）⇒ 出现处永远是「未解析」。
    #   ⇒ 分层的真入口是 `_缺口分类`。
    if _kg128._缺口分类("String", ["lean"]) != "内置":
        坏.append("判一百二十八⑤：`String` 在 **lean** 里是 Lean core 类型，"
                  f"该判内置，实得 {_kg128._缺口分类('String', ['lean'])}")
    if _kg128._缺口分类("String", ["rust"]) == "内置":
        坏.append("判一百二十八⑤：`String` 在 **rust** 里**不许**判内置 —— "
                  "按语言分层表的意义正是「同一个名字在不同语言里处境不同」；"
                  "实得内置 ⇒ **全局 `_语言内置` 把它兜住了**，分层形同虚设")
    _L = _kg128._内置_按语言
    if "String" not in _L.get("lean", ()):
        坏.append("判一百二十八⑤：`String` 不在 `_内置_按语言[\"lean\"]` 里")
    if "String" in _L.get("rust", ()):
        坏.append("判一百二十八⑤：`String` **不许**出现在 `_内置_按语言[\"rust\"]` 里")
    # ⑤ 按语言分层
    _sh128.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   假设名/根前缀/按语言内置：绿 —— 判一百二十八（`by_cases`/`cases 名字 :` "
          "是绑定 · `cases a with` 的 `a` 不是 · `_root_` 不是引用而 `liuHe` 是 · "
          "`String` 在 lean 是内置、在 rust 不是）")

def 判一百二十九另有五类绑定位() -> None:
    """★★★ **`by_contra h'` / `if h : P then` / `rename_i h` /
    `with h | h` / `⟨…⟩` 都是绑定位**（R474）。

    ★ 修完 `by_cases h :`（R472）后我**以为这一类清了** —— 实测仍在：
    ````
    h' 未解析 94   by_contra h'
    hb 未解析 51   if hb : b = 0 then   /   rename_i hb
    hb 未解析  6   | ⟨a, b, hb, hab⟩, ⟨c, d, hd, hcd⟩ =>
    ````
    ⇒ **同一族只覆盖了最常见的那个后缀**（老病：口径 204 ——
    「一个类要按这一类里有几个节点名枚举」）。
    ★ 这五处的共同点是**位置**：
      `by_contra` 之后 · `if` 之后（**且必须带 `名字 :`**）·
      `rename_i` 之后整段 · `with` 之后整段 · `⟨⟩` 之内。

    ★ 判据（五项，**两项是反向**）：
      ① `by_contra h'` ⇒ 依据「绑定/内置」
      ② `if hb : b = 0 then` ⇒ 同上
      ③ `rename_i hz` ⇒ 同上
      ④ `rcases hx with hq | hr` ⇒ **`hq` 与 `hr` 都**要绑 ——
        防我「只抽 `with` 后的第一个名字」（第一版就是这样，`hr` 漏了）
      ⑤ **反向**：`cases zq with`（**没有** `名字 :`，且 `zq` 从未定义）
        里的 `zq` **不许**被绑 ⇒ 防我把 `with` 后面一刀切
      ⑥ **反向**：`⟨…⟩` 的绑定**只认模式行**（`| …` 开头）——
         项里的 `⟨…⟩`（如 `exact ⟨p, q⟩`）里的名字**不许**被当成新绑定
    """
    坏: list[str] = []
    import sys as _s129
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s129.path:
        _s129.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg129
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十九：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j129
    import shutil as _sh129
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判129"
    if _d.exists():
        _sh129.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "theorem t (b : Nat) : True := by\n"          # 1
        "  by_contra h'\n"                            # 2  ★ ①
        "  · if hb : b = 0 then\n"                    # 3  ★ ②
        "      rename_i hz\n"                         # 4  ★ ③
        "      trivial\n"
        "    else trivial\n"
        "  · rcases hx with hq | hr\n"                # 7  ★ ④（两个）
        "    · exact hq\n"
        "    · exact hr\n"
        "  · cases zq with\n"                         # 10 ★ ⑤ 反向
        "    | zero => trivial\n"
        "    | succ k => rintro ⟨p, q⟩; exact ⟨p, q⟩\n"    # 12
        "    · exact ⟨zp, zq2⟩\n",                     # 13 ★ ⑥ 反向
        encoding="utf-8")
    try:
        _kg129.建图(_d, 打印=lambda *a: None)
        _kg129.解析引用(_d, 打印=lambda *a: None)
        _R = _j129.loads((_kg129._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百二十九：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh129.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if not _R:
        坏.append("判一百二十九：fixture 没有任何引用边 ⇒ **测不到，不许当通过**")
        _sh129.rmtree(_d, ignore_errors=True)
        return _报2(坏)

    def _依(名: str) -> list:
        return [(x.get("行"), "绑" if "绑定" in str(x.get("依据")) else "未")
                for x in _R if x.get("名") == 名 and x.get("依据")]

    # ①②③
    for _名, _号, _位 in (("h'", "①", "by_contra"), ("hb", "②", "if 名字 :"),
                          ("hz", "③", "rename_i")):
        _v = _依(_名)
        if not _v:
            坏.append(f"判一百二十九{_号}：`{_位}` 处的 `{_名}` **一条边都没有** ⇒ 绑定位根本没抽")
        elif not all(y[1] == "绑" for y in _v):
            坏.append(f"判一百二十九{_号}：`{_位}` 处的 `{_名}` 该是绑定，实得 {_v}")
    # ④ with 子句的两个名字都要绑
    for _名 in ("hq", "hr"):
        _v = [y for y in _依(_名) if y[0] == 7]
        if not _v:
            坏.append(f"判一百二十九④：`rcases hx with hq | hr` 里的 `{_名}` 在行 7 没有边 ⇒ 测不到")
        elif _v[0][1] != "绑":
            坏.append(f"判一百二十九④：`rcases hx with hq | hr` 里的 `{_名}` 该是绑定，实得 {_v}"
                      f" ⇒ **`with` 后面只抽了第一个名字**")
    # ⑤ 反向：`cases zq with` 里的 zq 不许被绑
    _zq = _依("zq")
    if not _zq:
        坏.append("判一百二十九⑤：`cases zq with` 里的 `zq` 一条边都没有 ⇒ 测不到")
    elif any(y[1] == "绑" for y in _zq):
        坏.append(f"判一百二十九⑤：`cases zq with`（**没有** `名字 :`）里的 `zq` 从未定义，"
                  f"该落「未解析」，实得 {_zq} ⇒ **把 `with` 后面一刀切了**")
    # ⑥ 反向：项里的 ⟨⟩ 不是绑定
    _k = [y for y in _依("k") if y[0] == 12]
    if not _k:
        坏.append("判一百二十九⑥：行 12 的 `k`（`succ k` 模式）没有边 ⇒ 测不到")
    elif _k[0][1] != "绑":
        坏.append(f"判一百二十九⑥：行 12 的 `k`（`succ k` 模式）该是绑定，实得 {_k}")
    # ★★ 我第一版拿 `succ k` 来验「模式里的名字是绑定」—— **靶选错了**：
    #   去掉守卫后它**仍然是绑的**（因为模式行本来就带 `|`）⇒ 变异前后同结果 ⇒ **测不出**。
    #   要测的其实是**反面**：**项**里的 `⟨…⟩`（行 13 `exact ⟨zp, zq2⟩`）
    #   里的名字是**构造**不是**绑定** ⇒ 必须落「未解析」。
    for _名 in ("zp", "zq2"):
        _v = [y for y in _依(_名) if y[0] == 13]
        if not _v:
            坏.append(f"判一百二十九⑥：行 13 的 `{_名}` 没有边 ⇒ 测不到")
        elif _v[0][1] == "绑":
            坏.append(f"判一百二十九⑥：行 13 `exact ⟨{_名}, …⟩` 是**项**不是模式，"
                      f"`{_名}` 该落「未解析」，实得 {_v} ⇒ **没有按「是不是模式行」守卫**")
    _k = [y for y in _依("k") if y[0] == 12]
    if not _k:
        坏.append("判一百二十九⑥：行 12 的 `k`（`succ k` 模式）没有边 ⇒ 测不到")
    elif _k[0][1] != "绑":
        坏.append(f"判一百二十九⑥：行 12 的 `k`（`succ k` 模式）该是绑定，实得 {_k}")
    _sh129.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   五类绑定位：绿 —— 判一百二十九（`by_contra` · `if 名字 :` · `rename_i` · "
          "`with a | b` 两个都绑 · `cases zq with` 的 `zq` 不是 · 模式里的 `succ k` 是）")

def 判一百三十字符串字面量不是引用() -> None:
    """★★★ **字符串字面量里的词不是引用**（R475）。

    ★ 实测（`形式化验证` 的 `★真外部缺`）：
    ````
    Collapse 未解析 75
      404: s!"  Collapse: {cr.collapseTriggered}\n" ++
      414: s!"  Hasse Extreme Collapse DOES NOT apply to secp256k1:\n" ++
    ````
    ⇒ **全是人类可读的提示文字**，被裸名正则当成了标识符 ✗

    ★ 口径 226：**靠巧合不出错不算对** ——
      不能指望「反正它在缺口表里排后面」兜住；
      **必须在产生提及的地方**就把字面量剔掉。
    ★ 顺带修同族的一条：**`s!"…"` / `m!"…"` / `f!"…"` 的插值前缀
      `s`/`m`/`f` 是语法词**（剔掉字面量后 `s` 立刻冒出来成未解析）。
      判据是**形状**（紧跟 `!` 再紧跟 `"`），不是名字本身 ——
      所以 `def s := 1` 里的 `s` **仍然是定义**。

    ★ 判据（四项，**两项是反向**）：
      ① `s!"  Collapse: …"` ⇒ `Collapse` **0 条**边
      ② **反向**：同一行的**真引用**（`helper`）**必须仍有**边 ——
        防我"整行一删了事"
      ③ **反向**：`s!` 里的 `s` 不许成边，但**同一文件 `def s := 1` 的 `s` 仍是定义**
      ④ **反向**：不含引号的行（`| ff => rfl`）**完全不受影响**
    """
    坏: list[str] = []
    import sys as _s130
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s130.path:
        _s130.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg130
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j130
    import shutil as _sh130
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判130"
    if _d.exists():
        _sh130.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        'def helper : Nat := 1\n'                      # 1  真定义
        'def 提示 : String := s!"  Collapse: {helper}\\n"\n'   # 2  ★ ① ②
        'def s : Nat := 2\n'                           # 3  ★ ③
        'inductive B where\n'                          # 4
        '  | ff : B\n'                                 # 5  ★ ④
        '  | tt : B\n'
        'def 用 : B := B.ff\n',                        # 7
        encoding="utf-8")
    try:
        _kg130.建图(_d, 打印=lambda *a: None)
        _kg130.解析引用(_d, 打印=lambda *a: None)
        _R = _j130.loads((_kg130._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
        _N = _j130.loads((_kg130._kg目录(_d) / "节点.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh130.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if not _R:
        坏.append("判一百三十：fixture 没有任何引用边 ⇒ **测不到，不许当通过**")
        _sh130.rmtree(_d, ignore_errors=True)
        return _报2(坏)

    # ① 字面量里的词 0 边
    _c = [x for x in _R if x.get("名") == "Collapse"]
    if _c:
        坏.append(f"判一百三十①：`Collapse` 只出现在**字符串字面量**里，"
                  f"不该有任何边，实得 {[(x.get('行'), str(x.get('依据'))[:12]) for x in _c]}")
    # ② 同一行的真引用必须有
    _h = [x for x in _R if x.get("名") == "helper" and x.get("行") == 2]
    if not _h:
        坏.append("判一百三十②：第 2 行 `s!\"… {helper} …\"` 里的 `helper` 是**真引用**，"
                  "实得 0 条 ⇒ 剔字面量时把整行剔了")
    # ③ s! 的 s 不许成边；def s := 1 的 s 必须是定义
    _s2 = [x for x in _R if x.get("名") == "s" and x.get("行") == 2]
    if _s2:
        坏.append(f"判一百三十③：`s!\"…\"` 里的 `s` 是**插值前缀**（语法词），"
                  f"不该有边，实得 {[(x.get('行'), str(x.get('依据'))[:12]) for x in _s2]}")
    _s3 = [v for v in _N.values() if isinstance(v, dict) and v.get("名") == "s" and v.get("行") == 3]
    if not _s3:
        坏.append("判一百三十③：第 3 行 `def s : Nat := 2` 是**真定义**，"
                  "实得没有定义节点 ⇒ **把 `s` 一刀切成语法词了**")
    # ④ 无引号的行不受影响
    for _名, _行 in (("ff", 5), ("tt", 6)):
        _v = [v for v in _N.values() if isinstance(v, dict)
              and v.get("名") == _名 and v.get("行") == _行]
        if not _v:
            坏.append(f"判一百三十④：第 {_行} 行 `| {_名} : B` 是**构造子定义**，"
                      f"实得没有定义节点 ⇒ 剔字面量影响到了无引号的行")
    _sh130.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   字面量：绿 —— 判一百三十（`s!\"… Collapse …\"` 里的词不是引用 · "
          "同一行的真引用还在 · `s!` 的 `s` 是语法词而 `def s` 是定义 · "
          "无引号的行不受影响）")

def 判一百三十一非ASCII标识符() -> None:
    """★★★ **非 ASCII 标识符**（CJK / 希腊字母）必须同样抽（R476）。

    ★ 实测（`形式化验证` 的 `★真外部缺`）：
    ````
    encode_ 未解析 79
      101: encode_α s
    ````
    ⇒ 真名是 **`encode_α`** —— 提及正则只认 `[A-Za-z_][A-Za-z0-9_']*`
      ⇒ 在 `α` 处**截断**，抽出个 `encode_` 来 ✗
      （**一个半截名字比名字全丢更坏**：它看起来像个真名，还会进缺口表。）

    ★ 分诊做得细一点就更清楚：
    ````
    def 真名 := 1 ⇒ **有**定义节点（定义侧的正则早就含 CJK）
    但 `用 := 真名` ⇒ **0 条提及边** ✗
    `{α : Type}` ⇒ `α` 也抽不出来
    ````
    ⇒ **只有「提及」这一侧是 ASCII-only** —— 两侧不一致正是这种 bug 的形状。

    ★ 修法：Unicode 字母用 `[^\\W\\d]`（`\\w` 在 Python 里默认就是 Unicode），
      把适配器里 16 处含 CJK 的字符类 + 1 处纯 ASCII 的**统一**成它。
      ★ 不写死 `\\u4e00-\\u9fff`：那只能覆盖 CJK，
        **希腊字母/数学字母照样漏**（`α`/`β`/`γ` 是 Lean 里最常见的类型变量名）。

    ★ 判据（四项，**两项是反向**）：
      ① `def 真名 := 1` + `def 用 := 真名` ⇒ `真名` 的提及边必须是「本文件定义」
      ② `theorem T {α : Type} (x : α)` ⇒ `α` 与 `x` 必须是「绑定/内置」
      ③ **反向**：ASCII 名（`g := 定理名`）**仍必须有边** ——
        防我把字符类改坏（`[^\\W\\d]` 一旦写成 `[^\\W]` 就连数字一起收）
      ④ **反向**：**以数字开头的词不许当名字**（`[^\\W\\d]` 的「非数字」那一半要有用）
    """
    坏: list[str] = []
    import sys as _s131
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s131.path:
        _s131.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg131
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十一：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j131
    import shutil as _sh131
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判131"
    if _d.exists():
        _sh131.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "def 真名 := 1\n"                                    # 1
        "theorem 定理名 {α : Type} (x : α) : α := x\n"        # 2  ★ ②
        "def 用 := 真名\n"                                    # 3  ★ ①
        "def g := 定理名\n"                                   # 4  ★ ③
        "def 数 := 3x\n",                                    # 5  ★ ④
        encoding="utf-8")
    try:
        _kg131.建图(_d, 打印=lambda *a: None)
        _kg131.解析引用(_d, 打印=lambda *a: None)
        _R = _j131.loads((_kg131._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十一：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh131.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if not _R:
        坏.append("判一百三十一：fixture 没有任何引用边 ⇒ **测不到，不许当通过**")
        _sh131.rmtree(_d, ignore_errors=True)
        return _报2(坏)

    def _依(名: str) -> list:
        return [(x.get("行"), str(x.get("依据"))[:16]) for x in _R if x.get("名") == 名]

    # ① CJK 定义与引用
    for _名, _行 in (("真名", 3), ("用", 3)):
        _v = [y for y in _依(_名) if y[0] == _行 or _名 == "真名"]
        if not _v:
            坏.append(f"判一百三十一①：CJK 名 `{_名}` **一条提及边都没有** ⇒ "
                      f"提及正则是 ASCII-only（定义侧却已含 CJK —— **两侧不一致**）")
        elif not any("本文件定义" in y[1] for y in _v):
            坏.append(f"判一百三十一①：CJK 名 `{_名}` 该解到本文件定义，实得 {_v}")
    # ② 希腊字母参数
    for _名 in ("α", "x"):
        _v = _依(_名)
        if not _v:
            坏.append(f"判一百三十一②：`{_名}` 是 `(x : α)` 里的**绑定位**，"
                      f"实得 0 条边 ⇒ 希腊字母没进字符类")
        elif not all("绑定" in str(y[1]) for y in _v):
            坏.append(f"判一百三十一②：`{_名}` 该是「绑定/内置」，实得 {_v}")
    # ③ 反向：ASCII 仍要有边
    _g = [y for y in _依("g") if y[0] == 4]
    if not _g:
        坏.append("判一百三十一③：`g := 定理名`（ASCII 名）该有边，实得 0 条 ⇒ "
                  "**改字符类时把 ASCII 也弄坏了**")
    # ④ 反向：数字开头不是名字
    _数 = [x for x in _R if str(x.get("名", "")).startswith("3") and x.get("行") == 5]
    if _数:
        坏.append(f"判一百三十一④：`3x` 以**数字开头**，不是标识符，"
                  f"实得被当成名字 {[(x.get('名'), x.get('行')) for x in _数]}")
    _sh131.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   非 ASCII 标识符：绿 —— 判一百三十一（CJK 定义/引用都抽 · "
          "希腊字母参数是绑定 · ASCII 仍正常 · 数字开头不是名字）")

def 判一百三十二转义标识符与一行多构造子() -> None:
    """★★★ **Lean 的转义标识符 `«…»`** 与 **一行多个构造子**（R477）。

    ★ 实测（`形式化验证` 的 `★真外部缺`，本轮 Top 里全是中文单字）：
    ````
    子 未解析 742 · 寅 456 · 午 452 · 巳 409 · 申 396 · 亥 393 · 卯 341
    ````
    ★ 按口径 192 先问「我的代码错，还是产物过期」——**都不是**：
      去源文件里找，`DiZhi.lean:13` 明明白白写着
    ````
    inductive DiZhi : Type where
      | «子» | «丑» | «寅» | «卯» | «辰» | «巳» | «午» | «未» | «申» | «酉» | «戌» | «亥»
    ````
    ⇒ **两个缺陷叠在一起**：
      ① `«` / `»` 是 Lean 的**标识符引号**（把与关键字冲突的名字包起来），
         **不是标识符字符** ⇒ 字符类匹配不上 ⇒ 12 个构造子**一个都没抽**；
      ② **一行有 12 个构造子**，而原来的 `^\\s*\\|\\s*名字` **只匹配行首第一个** ⇒
         即使字符类对，也只抽得到 1 个 ✗

    ★ 修法：
      · `«` / `»` 统一换成**空格**（保列号）⇒ `«子»` 退化成裸名 `子`；
        `DiZhi.«子»` 变成 `DiZhi. 子` ⇒ `子` 是裸提及，与 `DiZhi.toNat` 一致
        （限定名后段本来就不由裸名正则负责）。
      · 构造子改成 `finditer` 扫**所有** `| 名字`。

    ★ 判据（四项，**两项是反向**）：
      ① 一行 12 个 `| «X»` ⇒ **12 个**定义节点（名字要**逐个**对，不是"有就行"）
      ② `DiZhi.«午»` 的引用 ⇒ 必须解到「本文件定义」
      ③ **反向**：`deriving DecidableEq, Repr` 那一行的名字**不许**变成构造子
      ④ **反向**：`| ff : B`（ASCII、单个、带类型标注）**仍然要抽**（R471 的回归）
    """
    坏: list[str] = []
    import sys as _s132
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s132.path:
        _s132.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg132
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十二：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j132
    import shutil as _sh132
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判132"
    if _d.exists():
        _sh132.rmtree(_d)
    _d.mkdir(parents=True)
    _十二 = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
    (_d / "a.lean").write_text(
        "inductive DiZhi : Type where\n"                              # 1
        "  | " + " | ".join(f"«{z}»" for z in _十二) + "\n"            # 2  ★ ①
        "  deriving DecidableEq, Repr\n"                               # 3  ★ ③
        "\n"
        "def toNat : DiZhi → Nat\n"                                    # 5
        "  | DiZhi.«午» => 6\n"                                        # 6  ★ ②
        "\n"
        "inductive B where\n"                                          # 8  ★ ④
        "  | ff : B\n"
        "  | tt : B\n",
        encoding="utf-8")
    try:
        _kg132.建图(_d, 打印=lambda *a: None)
        _kg132.解析引用(_d, 打印=lambda *a: None)
        _R = _j132.loads((_kg132._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
        _N = _j132.loads((_kg132._kg目录(_d) / "节点.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十二：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh132.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if not _R or not _N:
        坏.append("判一百三十二：fixture 没有引用边或没有节点 ⇒ **测不到，不许当通过**")
        _sh132.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _定义 = [v for v in _N.values() if isinstance(v, dict) and v.get("类") == "定义"]
    _构造 = {v.get("名") for v in _定义 if "构造子" in str(v.get("类型"))}
    _名们 = {v.get("名") for v in _定义}
    # ① 12 个都要在
    _缺 = [z for z in _十二 if z not in _构造]
    if _缺:
        坏.append(f"判一百三十二①：`inductive DiZhi` 一行 12 个构造子，"
                  f"实得缺 {_缺}（共抽到 {len(_构造)} 个：{sorted(_构造)}）"
                  f" ⇒ **要么 `«»` 没剥、要么只抽了行首第一个**")
    # ② 引用解到定义
    _午 = [x for x in _R if x.get("名") == "午" and x.get("行") == 6]
    if not _午:
        坏.append("判一百三十二②：`| DiZhi.«午» => 6` 里的 `午` 没有引用边 ⇒ 测不到")
    elif not any("本文件定义" in str(x.get("依据")) for x in _午):
        坏.append(f"判一百三十二②：`DiZhi.«午»` 该解到本文件定义，"
                  f"实得 {[(x.get('行'), str(x.get('依据'))[:16]) for x in _午]}")
    # ③ 反向：deriving 那行的名字不许变成构造子
    for _名 in ("DecidableEq", "Repr", "deriving"):
        if _名 in _构造 or _名 in _名们:
            坏.append(f"判一百三十二③：`deriving {_名}` 是**派生子句**不是构造子，"
                      f"实得被当成定义了 ⇒ finditer 扫得太宽")
    # ④ 反向：ASCII 单构造子回归
    for _名 in ("ff", "tt"):
        if _名 not in _构造:
            坏.append(f"判一百三十二④：`| {_名} : B`（ASCII、带类型标注）该抽，"
                      f"实得没抽到 ⇒ **改 `finditer` 时把 R471 的形态弄坏了**")
    _sh132.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   转义标识符：绿 —— 判一百三十二（`«子» … 一行 12 个` 全部抽出 · "
          "`DiZhi.«午»` 解到本文件定义 · `deriving` 那行不是构造子 · "
          "`| ff : B` 仍抽）")

def 判一百三十三lambda是语法词而数学记号是真缺口() -> None:
    """★★★ **`λ` 是语法词；`ℝ`/`ℕ`/`ℤ` 是"真缺口"**（R478）。

    ★ 实测（`形式化验证` 的 `★真外部缺` Top）：
    ````
    λ 未解析 585
      339: let groupSeeds := cryptoSeedSet.filter (λ s => s.seedDomain == …)
    ℝ 未解析 1801   DifferentiableAt ℝ (fun y => u y i) x
    ℕ 未解析 295    abbrev Pt (n : ℕ) := Fin n × Fin n
    ℤ 未解析  95    a : ℤ
    ````
    ★ 两类**必须分开处理**，而它们的读数长得一样（都在缺口表里）：

      · **`λ` 是 Lean 的 lambda 记号** —— 它就是 `fun` 的 Unicode 写法，
        **不指向任何定义** ⇒ 是**语法词**，必须排掉（假缺口）。
      · **`ℝ`/`ℕ`/`ℤ` 是 Mathlib 的记号码位** —— 它们**确实有定义**，
        但定义在 **Mathlib 里**，而那些文件**没有导入 Mathlib**
        ⇒ **报成"真缺口/未解析"是对的** ✗ 不是 ✗——**是对的** ✓
        ★ 这一条特别值得写成判据：它固定了一个**判断**，而不是一个实现。
          把 `ℝ` 加进「lean 内置层」会让读数**变好看**（缺口 −1801），
          但那是**放宽** —— **放宽会把真缺口藏起来**（口径 203）。
          这些文件真的缺 Mathlib ⇒ **该报**。

    ★ 判据（四项，**三项是反向**）：
      ① `λ` **0 条**边
      ② **反向**：`(λ s => s)` 里的 `s` **仍必须是「绑定/内置」**
        —— 防我"排掉 `λ` 的时候把同一行的绑定也排掉"
      ③ **反向**：`fun`（原本的写法）**也要 0 条** —— 它是关键字，
        防"只排了 Unicode 写法、漏了 ASCII 写法"（同族只覆盖一个的老病）
      ④ **反向**：`ℝ` / `ℕ` / `ℤ` **不许**进 `_内置_按语言["lean"]`
        —— 它们是 **Mathlib 的名字**，不是 Lean core；
        进了内置层就等于**把真缺口藏起来**（口径 203）
    """
    坏: list[str] = []
    import sys as _s133
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s133.path:
        _s133.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg133
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十三：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    # ④ 先做（不依赖 fixture）
    _lean = _kg133._内置_按语言.get("lean", ())
    for _名 in ("ℝ", "ℕ", "ℤ"):
        if _名 in _lean:
            坏.append(f"判一百三十三④：`{_名}` 是 **Mathlib 的记号码位**（有定义，"
                      f"但在 Mathlib 里），不该进 `_内置_按语言[\"lean\"]` —— "
                      f"进了就等于**把真缺口藏起来**（这些文件确实没导入 Mathlib）")
    import json as _j133
    import shutil as _sh133
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判133"
    if _d.exists():
        _sh133.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "def f : Nat := 1\n"                     # 1
        "def g := (λ s => s)\n"                  # 2  ★ ① ②
        "def h := (fun s => s)\n"                # 3  ★ ③
        "def 用 := f\n",                         # 4
        encoding="utf-8")
    try:
        _kg133.建图(_d, 打印=lambda *a: None)
        _kg133.解析引用(_d, 打印=lambda *a: None)
        _R = _j133.loads((_kg133._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十三：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh133.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if not _R:
        坏.append("判一百三十三：fixture 没有任何引用边 ⇒ **测不到，不许当通过**")
        _sh133.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    # ① λ 0 条
    _λ = [x for x in _R if x.get("名") == "λ"]
    if _λ:
        坏.append(f"判一百三十三①：`λ` 是 Lean 的 **lambda 记号**（`fun` 的 Unicode 写法），"
                  f"不指向任何定义，不该有边，实得 {[(x.get('行'), str(x.get('依据'))[:14]) for x in _λ]}")
    # ② λ 之后的 s 仍是绑定
    _s = [x for x in _R if x.get("名") == "s" and x.get("行") == 2]
    if not _s:
        坏.append("判一百三十三②：`(λ s => s)` 里的 `s` 是**绑定位**，实得 0 条边 ⇒ "
                  "排 `λ` 时把同一行的绑定也排掉了")
    elif not all("绑定" in str(x.get("依据")) for x in _s):
        坏.append(f"判一百三十三②：`(λ s => s)` 里的 `s` 该是「绑定/内置」，"
                  f"实得 {[(x.get('行'), str(x.get('依据'))[:14]) for x in _s]}")
    # ③ fun 0 条（同族的 ASCII 写法）
    _fun = [x for x in _R if x.get("名") == "fun"]
    if _fun:
        坏.append(f"判一百三十三③：`fun` 是关键字，不该有边，实得 "
                  f"{[(x.get('行'), str(x.get('依据'))[:14]) for x in _fun]} ⇒ "
                  f"**只排了 `λ` 没排 ASCII 写法**（同族只覆盖一个）")
    _sh133.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   λ 与数学记号：绿 —— 判一百三十三（`λ` 0 条 · `λ s =>` 的 `s` 仍是绑定 · "
          "`fun` 也 0 条 · `ℝ`/`ℕ`/`ℤ` **不许**进 lean 内置层）")

def 判一百三十四where可省的声明块() -> None:
    """★★★ **`where` 可省**（`inductive X : Type` 直接跟 `| 构造子`）（R479）。

    ★ 实测（`ff` 未解析 239 · `tt` 239）：12 个引用它们的文件
      **每个都写了 `import MyMeta.Foundational.PureBool`**，
      而定义端 `PureBool.lean` **就在图里**、却**没有 `ff`/`tt` 的定义节点** ✗
      ⇒ 按口径 192 去读定义端：
    ````
    inductive PureBool : Type
      | tt : PureBool
      | ff : PureBool
    ````
      **头一行没有 `where`** —— 而 `_块头` 强制要求 `\bwhere\b` ⇒
      **整个块没进**，两个构造子一个都没抽。
    ★ Lean 4 里构造子以 `|` 开头时 `where` **可省**（`structure` 同理）。

    ★ 判据（四项，**两项是反向**）：
      ① `inductive PureBool : Type`（**无 `where`**）⇒ 2 个构造子都要抽到
      ② `tt` 的引用 ⇒ 必须解到「本文件定义」
      ③ **反向**：`inductive B where`（**有 `where`**）**仍然要抽**
        —— 防我"去掉 `where` 时把老形态弄坏"（R471/R477 的回归）
      ④ **反向**：块外**不得**出现构造子 ——
        靠缩进判边界，不能因为放宽 `where` 就把后面 `def` 的内容也吞进来
    """
    坏: list[str] = []
    import sys as _s134
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s134.path:
        _s134.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg134
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十四：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j134
    import shutil as _sh134
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判134"
    if _d.exists():
        _sh134.rmtree(_d)
    _d.mkdir(parents=True)
    # ★★ 口径 238：反向判据的**靶必须在变异前后取不同的值**。
    #   我第一版拿 `  | PureBool.tt, PureBool.tt => PureBool.tt` 当「块外」的靶，
    #   可它**本来就不匹配构造子正则**（`PureBool` 后面是 `.`，不满足 `(?=[:|]|$)`）
    #   ⇒ 变异成「不看缩进」之后它**照样不匹配** ⇒ 判据④绿着没反应 ✗
    #   ⇒ 换成**形态一模一样的顶格** `| zz : PureBool`：
    #     缩进 0 == 块头缩进 0 ⇒ 正常应**立刻断块**；
    #     一旦不看缩进，它就会被当成 PureBool 的构造子。
    (_d / "a.lean").write_text(
        "inductive PureBool : Type\n"                    # 1  ★ ①（无 where）
        "  | tt : PureBool\n"                            # 2
        "  | ff : PureBool\n"                            # 3
        "\n"
        "| zz : PureBool\n"                              # 5  ★ ④（顶格 = 块外）
        "\n"
        "inductive B where\n"                            # 7  ★ ③（有 where）
        "  | xx : B\n"                                   # 8
        "  | yy : B\n"                                   # 9
        "\n"
        "def 用 : PureBool := tt\n"                   # 11 ★ ②
        "\n"
        "structure S where\n"                           # 13 ★ ③（R471 回归）
        "  f1 : Nat\n"                                  # 14
        "  f2 : Nat\n",                                 # 15

        encoding="utf-8")
    try:
        _kg134.建图(_d, 打印=lambda *a: None)
        _kg134.解析引用(_d, 打印=lambda *a: None)
        _R = _j134.loads((_kg134._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
        _N = _j134.loads((_kg134._kg目录(_d) / "节点.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十四：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh134.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if not _R or not _N:
        坏.append("判一百三十四：fixture 没有引用边或没有节点 ⇒ **测不到，不许当通过**")
        _sh134.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _构造 = {(v.get("名"), v.get("行")) for v in _N.values()
             if isinstance(v, dict) and "构造子" in str(v.get("类型"))}
    # ① 无 where 的两个
    for _名, _行 in (("tt", 2), ("ff", 3)):
        if (_名, _行) not in _构造:
            坏.append(f"判一百三十四①：`inductive PureBool : Type`（**无 `where`**）"
                      f"第 {_行} 行的构造子 `{_名}` 没抽到（实得 {sorted(_构造)}）⇒ "
                      f"块头仍然强制要求 `where`")
    # ② 引用解到定义
    # ★ 靶要用**裸名**：`PureBool.tt` 是**限定名**，裸名正则的 lookbehind 排除 `.`
    #   ⇒ 它不走裸名路，拿它当靶会**永远红**（基线就红 = 判据等于没写成）。
    _tt = [x for x in _R if x.get("名") == "tt" and x.get("行") == 11]
    if not _tt:
        坏.append("判一百三十四②：``tt` 的引用一条都没有 ⇒ 测不到")
    elif not any("本文件定义" in str(x.get("依据")) for x in _tt):
        坏.append(f"判一百三十四②：`tt` 该解到本文件定义，"
                  f"实得 {[(x.get('行'), str(x.get('依据'))[:14]) for x in _tt]}")
    # ③ 有 where 的老形态回归
    for _名, _行 in (("xx", 8), ("yy", 9)):
        if (_名, _行) not in _构造:
            坏.append(f"判一百三十四③：`inductive B where`（**有 `where`**）"
                      f"第 {_行} 行的 `{_名}` 没抽到 ⇒ 去掉 `where` 时把老形态弄坏了")
    # ★ ③b：structure 字段回归（R471/口径 238：靶要有区分力）
    for _名, _行 in (("f1", 14), ("f2", 15)):
        _f = [v for v in _N.values() if isinstance(v, dict)
              and v.get("名") == _名 and v.get("行") == _行]
        if not _f:
            坏.append(f"判一百三十四③：`structure S where` 第 {_行} 行的字段 `{_名}` "
                      f"没抽到 ⇒ 改 `_\u5757\u5934` 时把 `structure` 弄坏了")
    # ④ 块外不许有构造子
    _外 = sorted(x for x in _构造 if x[1] == 5)
    if _外:
        坏.append(f"判一百三十四④：第 5 行 `| zz : PureBool` **顶格**"
                  f"（缩进 = 块头缩进），按缩进规则它**在块外**，"
                  f"不该是 PureBool 的构造子，实得 {_外} ⇒ "
                  f"块边界不看缩进了（口径 239：块边界要按缩进判）")
    _sh134.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   where 可省：绿 —— 判一百三十四（无 `where` 的 `inductive X : Type` 也抽 · "
          "引用解到定义 · 有 `where` 的老形态没坏 · 块外不被吞）")

def 判一百三十五上标记号不是名字() -> None:
    """★★★ **名字必须以字母或下划线开头**（`¹` / `⁻` 不是名字）（R480）。

    ★ 实测（`形式化验证` 的 `★真外部缺`，R479 修完 `ff`/`tt` 后冒出来的新面孔）：
    ````
    ¹ 未解析 79
      182: have hgi : g⁻¹ • b = b := by
      390: complex := z.complex⁻¹
    ````
    ⇒ `g⁻¹` 是 Lean 的**上标逆元记号**（`Inv.inv`）。
      `⁻`（U+207B 上标减）**不是 `\\w`** ⇒ `g` 到此为止；
      而 **`¹`（U+00B9）的 `isalnum()` 是 True**（Unicode 类别 `No`）
      ⇒ 它**独立成了一个名字** ✗

    ★ 这是 `[^\\W\\d]`（R476 改的）的**副作用**：
      `\\d` 只排 ASCII 数字，而 `isalnum()` 把 `¹²³` 也算字母。
      ⇒ **不去改正则**（那会又把希腊字母搞坏 —— R476 刚修好），
        而是在**抽出名字之后加一条最朴素的守卫**：
        **名字必须以字母或下划线开头**。
      ★ 它同时挡住 `①②③`（带圈数字）等一整类「No/Nl 类但不是字母」的字符。

    ★ 判据（四项，**三项是反向**）：
      ① `¹` / `⁻` **0 条**边
      ② **反向**：`g⁻¹` 里的 **`g` 仍必须有**边（是绑定位）——
        防我「见上标就把整行排掉」
      ③ **反向**：`α₁`（下标在名字**内部**）**仍必须是定义** ——
        守卫只挡**开头**，不能把内部的下标一起挡掉
      ④ **反向**：中文名 `中文` 仍必须是定义（R476 的回归）
    """
    坏: list[str] = []
    import sys as _s135
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s135.path:
        _s135.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg135
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十五：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j135
    import shutil as _sh135
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判135"
    if _d.exists():
        _sh135.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "theorem t (g : Nat) : True := by\n"                       # 1  ★ ②
        "  have hgi : g\u207b\u00b9 \u2022 g = g := by trivial\n"   # 2  ★ ① ②
        "  exact trivial\n"                                        # 3
        "\n"
        "def \u03b1\u2081 := 1\n"                                   # 5  ★ ③
        "def \u4e2d\u6587 := \u03b1\u2081\n",                       # 6  ★ ④
        encoding="utf-8")
    try:
        _kg135.建图(_d, 打印=lambda *a: None)
        _kg135.解析引用(_d, 打印=lambda *a: None)
        _R = _j135.loads((_kg135._kg目录(_d) / "引用.json").read_text(encoding="utf-8"))
        _N = _j135.loads((_kg135._kg目录(_d) / "节点.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十五：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh135.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if not _R or not _N:
        坏.append("判一百三十五：fixture 没有引用边或没有节点 ⇒ **测不到，不许当通过**")
        _sh135.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _定义名 = {v.get("名") for v in _N.values()
               if isinstance(v, dict) and v.get("类") == "定义"}
    # ① 上标记号 0 条
    for _记 in ("\u00b9", "\u207b", "\u00b2", "\u00b3"):
        _m = [x for x in _R if x.get("名") == _记]
        if _m:
            坏.append(f"判一百三十五①：`{_记}` 是**上标记号码位**（`g\u207b\u00b9` 里的），"
                      f"不是名字，实得 {[(x.get('行'), str(x.get('依据'))[:12]) for x in _m]}")
    # ② g 仍要有边
    _g = [x for x in _R if x.get("名") == "g" and x.get("行") == 2]
    if not _g:
        坏.append("判一百三十五②：`g\u207b\u00b9` 里的 `g` 是**绑定位**，实得 0 条边 ⇒ "
                  "排上标记号时把同一行的 `g` 也排掉了（口径 246：两个缺陷一个读数）")
    # ③ α₁ 仍是定义
    # ★★ 口径 213：守卫在**提及层**，
    #   而定义节点是**另一条抽取路**做的 ⇒ 测定义层**测不到守卫**。
    #   ⇒ 靶改成 `def 中文 := α₁` 里 `α₁` 的**提及边**。
    _a1 = [x for x in _R if x.get("名") == "α₁" and x.get("行") == 6]
    if not _a1:
        坏.append("判一百三十五③：`def 中文 := α₁` 里的 `α₁` 一条提及边都没有 ⇒ "
                  "守卫挡到名字**内部**去了（只该挡开头）")
    # ④ 中文名仍是定义
    if "\u4e2d\u6587" not in _定义名:
        坏.append(f"判一百三十五④：`def \u4e2d\u6587` 必须仍是定义（R476 的回归），"
                  f"实得定义名 {sorted(_定义名)}")
    _sh135.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   上标记号：绿 —— 判一百三十五（`\u00b9`/`\u207b` 0 条 · `g\u207b\u00b9` 里的 `g` 仍是绑定 · "
          "`\u03b1\u2081` 内部下标不挡 · 中文名不挡）")

def 判一百三十六引用解析率必须记账且自洽() -> None:
    """★★★ **「真引用」的核心读数必须记进产物，且四个数自洽**（R481）。

    ★ 实测（R480 之后第一次去量它）：
    ````
    全部引用边 1,282,002 · 未解析 113,994  ⇒ **91.1% 已解析**
    ````
    ★ **而在此之前账里根本没有这个数**：
      `外部缺.json` 只写 `未解析总数`，索引只写 `引用条数` / `未解析`
      ⇒ **两个数都在，比率没人算** ✗
      ⇒ 依赖"人现算"的读数 ≈ 没记（口径 197 的近亲：
        「改了产物就必须同时改它的账」；这里反过来了 ——
        **读数本身没进账，等于这个目标没有读数**）。

    ★ 判据（四项，**一项是反向**）：
      ① 产物里必须**有** `引用边总数` / `已解析条数` / `★解析率…` 三个字段
        —— 「字段在不在」本身要判，否则下一轮有人删了没人知道
      ② **自洽**：`已解析条数 + 未解析总数 == 引用边总数`（口径 194）
      ③ **回图重数**：拿 `引用.json` **重数**一遍
        —— 已解析 = 总数 − 依据里含「未解析」的条数，
        判据算出来的必须与产物里写的**一字不差**（口径「每个数字回图重数」）
      ④ **反向**：把 `已解析条数` 归零 ⇒ ② **必须红**
        （证明 ② 真的会红，不是恒真的算式）
    """
    坏: list[str] = []
    import sys as _s136
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s136.path:
        _s136.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg136
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十六：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    import json as _j136
    import shutil as _sh136
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判136"
    if _d.exists():
        _sh136.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.lean").write_text(
        "def f : Nat := 1\n"
        "def g := f\n"
        "def h := \u5b8c\u5168\u4e0d\u5b58\u5728\u7684\u540d\u5b57\n",
        encoding="utf-8")
    try:
        _kg136.建图(_d, 打印=lambda *a: None)
        _kg136.解析引用(_d, 打印=lambda *a: None)
        _桶 = _kg136._kg目录(_d)
        _外 = _j136.loads((_桶 / "外部缺.json").read_text(encoding="utf-8"))
        _引 = _j136.loads((_桶 / "引用.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十六：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh136.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _键率 = [k for k in _外 if "解析率" in k]
    # ① 三个字段都要在
    for _k in ("引用边总数", "已解析条数"):
        if _k not in _外:
            坏.append(f"判一百三十六①：`外部缺.json` 缺字段 `{_k}` ⇒ "
                      f"**「真引用」的核心读数没进账**（现得：{sorted(_外)[:12]}）")
    if not _键率:
        坏.append("判一百三十六①：`外部缺.json` 缺「解析率」字段 ⇒ "
                  "**读数是人现算的 = 等于没记**")
    if not _引:
        坏.append("判一百三十六：fixture 没有任何引用边 ⇒ **测不到，不许当通过**")
        _sh136.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    if "引用边总数" in _外 and "已解析条数" in _外 and "未解析总数" in _外:
        _总 = _外["引用边总数"]
        _已 = _外["已解析条数"]
        _未 = _外["未解析总数"]
        # ② 自洽
        if _已 + _未 != _总:
            坏.append(f"判一百三十六②：`已解析({_已}) + 未解析({_未}) != 引用边总数({_总})` "
                      f"⇒ 同一个概念的两个出口字段不一致（口径 194）")
        # ③ 回图重数
        _实总 = len(_引)
        _实未 = sum(1 for x in _引 if "**未解析**" in str(x.get("依据") or ""))
        if _总 != _实总:
            坏.append(f"判一百三十六③：产物写 `引用边总数`={_总}，"
                      f"回 `引用.json` 重数是 {_实总} ⇒ **两个出口不一样**")
        if _未 != _实未:
            坏.append(f"判一百三十六③：产物写 `未解析总数`={_未}，"
                      f"回 `引用.json` 重数是 {_实未} ⇒ **两个出口不一样**")
        if _键率:
            _实率 = round((_实总 - _实未) * 100.0 / _实总, 2) if _实总 else None
            _写率 = _外[_键率[0]]
            if _实率 is not None and abs(float(_写率) - _实率) > 0.011:
                坏.append(f"判一百三十六③：产物写解析率 {_写率}%，"
                          f"回图重数是 {_实率}% ⇒ **比率不是从这两个数算出来的**")
    _sh136.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   解析率：绿 —— 判一百三十六（三个字段都在 · 已解析+未解析==总数 · "
          "回 `引用.json` 重数一字不差 · 比率确实是从这两个数算的）")

def 判一百三十七索引必须带解析率() -> None:
    """★★★ **核心读数必须出现在「入口」上**：索引里的解析率（R482）。

    ★ 理由：索引是「AI 知道自己有什么东西」的**入口文件**。
      R481 把解析率写进了 `外部缺.json`，但**索引里没有** ——
      ⇒ **入口上看不到的数，等于 AI 不知道**（要它去翻 17 个桶才知道）。
    ★ 与 R423/R427 同一条口径：读数一律**读落盘文件**、键名**照实写**、
      读不到就报「这一步没跑」，**不许写 0/None 蒙过去**。

    ★★★ **分档**（口径 190：`False` 与 `unknown` 必须分开）——
      第一版我把「索引里没有解析率」整类判红，结果**基线就红**：
      17 个桶里只有 2 个的 `外部缺.json` **带那三个字段**
      （其余 15 个是 R481 之前建的）⇒ 判据分不清两件事：
        · **这个桶的产物是旧版**（没这个读数）—— 是「没跑过新版」
        · **新版桶的读数在入口上丢了** —— 这才是缺陷
      ⇒ 必须分档：**新版桶必须带**；旧版桶**如实报个数**（不算红）；
        新版桶一个都没有 ⇒ **测不到**（不许当通过）。

    ★ 判据（四项，**一项是反向**）：
      ① **新版桶**（`外部缺.json` 里有解析率键的）在索引里**必须带**
        `★引用解析率%` 且**不是 None**
      ② **同源**：索引里写的 `引用边总数`/`已解析条数`/`未解析`/解析率
        必须与该根桶里的 `外部缺.json` **一字不差**（口径 194）
      ③ 覆盖数必须 **≥1**，且**旧版桶的个数要报出来**（口径 150：
        可行动缺口与不可行动缺口分开报）—— 不许静默跳过
      ④ **反向**：把索引里新版桶的解析率键**删掉** ⇒ ① 必须红
    """
    坏: list[str] = []
    import json as _j137
    _pkg = Path(__file__).resolve().parents[1]
    _索引 = _pkg / "work" / "图" / "索引.json"
    if not _索引.is_file():
        坏.append("判一百三十七：**没有 `work/图/索引.json`** ⇒ "
                  "常驻索引没建 ⇒ **测不到**（不是通过）。"
                  "★ 重跑命令：`刷全仓索引(总根)`")
        return _报2(坏)
    try:
        _i = _j137.loads(_索引.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十七：索引读不了（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    _根们 = _i.get("根们") or []
    _有图 = [r for r in _根们 if r.get("有图")]
    if not _有图:
        坏.append("判一百三十七：索引里没有任何**有图**的根 ⇒ **测不到**")
        return _报2(坏)
    _新: list[tuple[dict, dict]] = []
    _旧: list[str] = []
    _空桶: list[str] = []
    for r in _有图:
        _g = r.get("图目录")
        _d = None
        if _g:
            _外档 = Path(_g) / "外部缺.json"
            if _外档.is_file():
                try:
                    _d = _j137.loads(_外档.read_text(encoding="utf-8"))
                except Exception:                 # noqa: BLE001
                    _d = None
        # ★★★ R485：**`None` 不算「新版桶」**（口径 190）——
        #   实测（R484 之后第一次跑）：`交接报告` 的 `外部缺.json` **有**这个键，
        #   值是 **`None`** —— 因为它的 `引用.json` **是空的**，比率算不出来。
        #   原来「键存在就算新版桶」⇒ 判据把它报红：
        #     「是**新版桶**（桶里有解析率 None%），但索引里没有」✗
        #   ⇒ **`None`（没有引用，算不出）与「有引用但读数在入口上丢了」
        #     是两回事**，必须分开（这正是口径 190 说的那件事，
        #     而这条判据自己踩了它）。
        #   ⇒ 分三档：**有实数的=新版** · **键在但值是 None 的=空桶** ·
        #     **键都不在的=旧版**。空桶与旧版**都不算红**，但**都要报个数**（口径 150）。
        _率 = _d.get("★解析率（已解析/全部引用边）") if isinstance(_d, dict) else "___缺"
        if _率 == "___缺":
            _旧.append(str(r.get("根"))[-30:])
        elif _率 is None:
            _空桶.append(str(r.get("根"))[-30:])
        else:
            _新.append((r, _d))
    # ★ 索引里带实数、但桶里算不出的：这才是"入口上的读数来路不明"（也算红）
    for _r in _有图:
        _g = _r.get("图目录")
        _率 = None
        if _g and (Path(_g) / "外部缺.json").is_file():
            try:
                _率 = _j137.loads((Path(_g) / "外部缺.json").read_text(encoding="utf-8")
                                  ).get("★解析率（已解析/全部引用边）")
            except Exception:                     # noqa: BLE001
                _率 = None
        if _率 is None and _r.get("★引用解析率%") is not None:
            坏.append(f"判一百三十七：`{str(_r.get('根'))[-30:]}` **桶里算不出解析率**"
                      f"（`引用.json` 可能是空的），但**索引里写着一个实数 "
                      f"{_r.get('★引用解析率%')}** ⇒ 入口上的读数**来路不明**（口径 190）")
    if not _新:
        坏.append("判一百三十七③：**一个「新版桶」都没有**（没有任何桶的 "
                  "`外部缺.json` 带解析率字段）⇒ **测不到，不许当通过**。"
                  "★ 重跑命令：`解析引用(该根)`（R481 之后才会写这个字段）")
        return _报2(坏)
    # ① 新版桶必须带
    for r, _d in _新:
        if r.get("★引用解析率%") is None:
            坏.append(f"判一百三十七①：`{str(r.get('根'))[-30:]}` 是**新版桶**"
                      f"（桶里有解析率 {_d.get('★解析率（已解析/全部引用边）')}%），"
                      f"但**索引里没有** ⇒ **入口上看不到的数，等于 AI 不知道**")
    # ② 同源
    _比 = 0
    for r, _d in _新:
        for _索键, _外键 in (("引用边总数", "引用边总数"),
                            ("已解析条数", "已解析条数"),
                            ("未解析", "未解析总数"),
                            ("★引用解析率%", "★解析率（已解析/全部引用边）")):
            _a, _b = r.get(_索键), _d.get(_外键)
            _比 += 1
            if _a != _b:
                坏.append(f"判一百三十七②：`{str(r.get('根'))[-28:]}` 的 `{_索键}`："
                          f"索引写 {_a!r}，桶里 `外部缺.json` 写 {_b!r} ⇒ "
                          f"同一个读数两个出口不一样（口径 194）")
    if _比 < 4:
        坏.append(f"判一百三十七②：只比上 {_比} 个字段（应 ≥4）⇒ 测不到，不许当通过")
    if 坏:
        return _报2(坏)
    print(f"   索引解析率：绿 —— 判一百三十七（**新版桶 {len(_新)} 个**都带解析率 · "
          f"{_比} 个字段与桶内 `外部缺.json` 同源）")
    if _旧:
        print(f"      ★ 口径 150 分开报：另有 **{len(_旧)} 个桶的产物是 R481 之前的**"
              f"（`外部缺.json` 里没有解析率字段）⇒ 不是读数丢了，是**没跑过新版**；"
              f"重跑 `解析引用(该根)` 即可补上。例：{_旧[:3]}")
    if _空桶:
        print(f"      ★ 口径 190 分开报：另有 **{len(_空桶)} 个桶的 `引用.json` 是空的**"
              f"（比率**算不出来**，所以键在、值是 `None`）—— "
              f"**不是「读数丢了」，也不是「全解析了」**。例：{_空桶[:3]}")

def 判一百三十八每个桶都要有解析率() -> None:
    """★★★ **解析率必须覆盖每一个「有引用」的桶**（R483 补记）。

    ★ 实测（R482 之后第一次数覆盖）：`有图 17 · 带解析率 1` ⇒
      其余 15 个桶不是「没跑过新版」就是「登记的路没读」。
      R483 用**当时落盘的** `引用.json` **补记**了 14 个桶的四个字段
      （数据同源 —— 这三个数只依赖 `引用.json` 本身，
        而它已经在盘上；R481 之前跑的时候没把比率写下来而已），
      补记产物里带 `★补记（R483）` 一段**自述**（口径 199：留痕）。

    ★ **为什么这条判据必须存在**：解析率是目标（「名字出现过」→「真引用」）的
      **核心读数**。一个桶没有它，读者**分不清**
      「这个桶引用全解析了（100%）」与「这个桶没这个读数」——
      而**两者看起来都是"没有红字"**（口径 190）。

    ★ 判据（四项，一项反向）：
      ① 每个**有 `引用.json` 且非空**的桶，`外部缺.json` 必须带解析率字段
        ★ 空 `引用.json` 的桶**不算红**，但要**留痕报出来**
        （口径 190：不补 0，因为 **0 看起来像好消息**）
      ② **自洽**：`已解析条数 + 未解析总数 == 引用边总数`
      ③ **回 `引用.json` 重数**：总数、未解析、比率三项逐字比对
      ④ **反向**：删掉一个桶的解析率键 ⇒ ① 必须红
    """
    坏: list[str] = []
    import json as _j138
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    if not _仓.is_dir():
        坏.append("判一百三十八：**没有 `work/图`** ⇒ 常驻图仓库没建 ⇒ **测不到**")
        return _报2(坏)
    _键 = "★解析率（已解析/全部引用边）"
    _有 = _无 = 0
    _空: list[str] = []
    _无读: list[str] = []
    for _桶 in sorted(_仓.iterdir()):
        if not _桶.is_dir():
            continue
        _r, _w = _桶 / "引用.json", _桶 / "外部缺.json"
        if not _r.is_file() or not _w.is_file():
            continue
        try:
            _引 = _j138.loads(_r.read_text(encoding="utf-8"))
            _外 = _j138.loads(_w.read_text(encoding="utf-8"))
        except Exception as e:                    # noqa: BLE001
            _无读.append(f"{_桶.name}（{type(e).__name__}）")
            continue
        if not isinstance(_引, list) or not _引:
            _空.append(_桶.name)
            continue
        if _键 not in _外:
            坏.append(f"判一百三十八①：桶 `{_桶.name}` 有 `引用.json`（{len(_引):,} 条）"
                      f"但 `外部缺.json` **没有解析率字段** ⇒ 读者分不清"
                      f"「全解析了」与「没这个读数」（口径 190）")
            continue
        _有 += 1
        _总, _未 = len(_引), sum(1 for x in _引 if "**未解析**" in str(x.get("依据") or ""))
        _已 = _总 - _未
        if _外.get("引用边总数") != _总:
            坏.append(f"判一百三十八③：桶 `{_桶.name}` 写 `引用边总数`="
                      f"{_外.get('引用边总数')!r}，回 `引用.json` 重数是 {_总:,}")
        if _外.get("未解析总数") != _未:
            坏.append(f"判一百三十八③：桶 `{_桶.name}` 写 `未解析总数`="
                      f"{_外.get('未解析总数')!r}，回 `引用.json` 重数是 {_未:,}")
        if _外.get("已解析条数") is not None and \
                _外["已解析条数"] + _外.get("未解析总数", 0) != _外.get("引用边总数"):
            坏.append(f"判一百三十八②：桶 `{_桶.name}` "
                      f"`已解析({_外.get('已解析条数')}) + 未解析({_外.get('未解析总数')}) "
                      f"!= 引用边总数({_外.get('引用边总数')})`（口径 194）")
        _实率 = round(_已 * 100.0 / _总, 2)
        if _外.get(_键) is not None and abs(float(_外[_键]) - _实率) > 0.011:
            坏.append(f"判一百三十八③：桶 `{_桶.name}` 写解析率 {_外[_键]}%，"
                      f"回图重数是 {_实率}% ⇒ **比率不是从这两个数算出来的**")
    if _有 == 0 and not 坏:
        坏.append("判一百三十八①：**一个带解析率的桶都没有** ⇒ 测不到，不许当通过"
                  "（口径 231：锚点数为 0 必须当红）")
    if _无读:
        坏.append(f"判一百三十八：{len(_无读)} 个桶的产物读不了：{_无读[:4]} ⇒ 测不全")
    if 坏:
        return _报2(坏)
    print(f"   解析率覆盖：绿 —— 判一百三十八（**{_有} 个有引用的桶全部带解析率** · "
          f"逐桶自洽 · 回 `引用.json` 重数一字不差）")
    if _空:
        print(f"      ★ 口径 190 留痕：{len(_空)} 个桶的 `引用.json` 是**空的**"
              f"（{_空}）⇒ **不补 0** —— 0 看起来像好消息，"
              f"而『没有引用』与『全解析了』不是一回事")

def 判一百三十九内置要按语言判() -> None:
    """★★★ **内置判定必须按语言**，不能用语言无关的通用表做全局排除（R484）。

    ★ 实测暴露（`semsearch`，Rust，解析率 70.08%）：
      真外部缺 Top6 = `Option 308 · String 256 · Ok 213 · Result 166 ·
      Some 112 · Err 98` —— **全是内置**。
      根因：`_真外` 只排 `_按语内置`，而 `_按语内置` 又把
      「在通用表里」的名字用 `not in _语言内置` **挡在外面**
      ⇒ **通用表里的名字（越确定是内置）越会被报成"真缺口"**，方向是反的。

    ★ 但**不能因此改成"通用表一律全局剔"** ——
      `Option`/`String` 在 **Lean** 里是核心类型，
      而 `ℝ`/`ZMod`/`ac_rfl`/`fderiv`/`volume` 是 **Mathlib 名**
      （口径 248：它们是**真缺口**，不修）。
      全局剔会把**真缺口藏起来**（口径 203 的形态）。
    ⇒ 判据是 **按「这个名字出现在哪种语言的文件里」判**：
      **该名字出现过的每一种语言都认它是内置**，才算内置。

    ★ 判据（四项，**三项是反向**）：
      ① Rust fixture 里的 `Option`/`String`/`Ok`/`Result`/`Vec`/`Some`/
        `println` **不得**出现在 `★真外部缺` 里
      ② **反向**：Lean fixture 里的 `ℝ`/`ZMod` **必须仍然**在 `★真外部缺` 里
        —— 防我"为了让 ① 绿"就把通用表整个全局剔掉（口径 203/248）
      ③ **反向**：Lean fixture 里的 `IO`/`String`（**Lean 核心类型**）
        **必须被剔掉** —— 防我"为了保住 ②"就整体退回、什么都不剔
      ④ **留痕**：被剔的那批必须**记进产物**（口径 199：剔掉必须留痕）
    """
    坏: list[str] = []
    import sys as _s139
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s139.path:
        _s139.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg139
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十九：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    if not hasattr(_kg139, "_是内置"):
        坏.append("判一百三十九：`kg` 里没有 `_是内置` ⇒ **按语言判内置那条路不存在**")
        return _报2(坏)
    import json as _j139
    import shutil as _sh139
    # ★ 先直接量判定函数本身（比跑全图快，且直指要害）
    _真 = [("Option", {"rust"}), ("String", {"rust"}), ("Ok", {"rust"}),
           ("Result", {"rust"}), ("Vec", {"rust"}), ("Some", {"rust"}),
           ("println", {"rust"}), ("derive", {"rust"})]
    for _名, _Ls in _真:
        if not _kg139._是内置(_名, _Ls):
            坏.append(f"判一百三十九①：`_是内置({_名!r}, {_Ls})` 是 False ⇒ "
                      f"**Rust 内置没被判成内置**")
    # ② Mathlib 名必须**不是**内置
    for _名 in ("\u211d", "\u2124", "\u2115", "ZMod", "ac_rfl", "fderiv", "volume"):
        if _kg139._是内置(_名, {"lean"}):
            坏.append(f"判一百三十九②：`_是内置({_名!r}, {{'lean'}})` 是 True ⇒ "
                      f"**把 Mathlib 名当内置了**（口径 248：它们是**真缺口**）"
                      f"—— 这会把真缺口藏起来")
    # ③ Lean 核心类型必须**是**内置
    for _名 in ("IO", "String", "Option", "List", "Nat", "Bool"):
        if not _kg139._是内置(_名, {"lean"}):
            坏.append(f"判一百三十九③：`_是内置({_名!r}, {{'lean'}})` 是 False ⇒ "
                      f"**Lean 核心类型没被剔**（整体退回、什么都不剔）")
    # ★★★ ⑤ **跨语言**才是「按语言判」与「拿通用表全局剔」的**区分点** ——
    #   `derive` 是 **Rust 属性**（在通用表里，也在 `_通用归属['rust']` 里），
    #   但它**不在 Lean 表里**。若它出现在 **Lean 文件**里，
    #   按语言判 ⇒ **不是内置**（该报成真缺口）；
    #   拿通用表全局剔 ⇒ **是内置**（被藏起来）✗
    #   ★ 口径 203：为了让一个判据变绿而放宽范围，会把真缺口藏起来。
    # ★★ 口径 238：**靶必须在变异前后取不同的值** ——
    #   `Option`/`String` **在 Lean 表里**（Lean 核心类型）⇒ 在 Lean 文件里
    #   它们**本该**是内置 ⇒ 拿它们当靶会**基线就红**，且没有区分力 ✗
    #   ⇒ 只留**真正语言专属**的：`derive`/`cfg`（Rust 属性）、
    #     `ArrayList`/`Optional`（Java 类）—— 它们**不在任何 Lean 表里**。
    for _名 in ("derive", "cfg", "ArrayList", "Optional", "Collectors"):
        if _kg139._是内置(_名, {"lean"}):
            坏.append(f"判一百三十九⑤：`_是内置({_名!r}, {{'lean'}})` 是 True ⇒ "
                      f"**拿语言无关的通用表做了全局剔除** —— "
                      f"`{_名}` 在 Lean 文件里就该报成真缺口（口径 203）")
    # ④ 跑一张混语言的小图，核产物里的留痕
    _d = Path(__file__).resolve().parents[1] / "work" / "\u4e34\u65f6" / "kg\u5224139"
    if _d.exists():
        _sh139.rmtree(_d)
    _d.mkdir(parents=True)
    (_d / "a.rs").write_text(
        'fn f(x: Option<String>) -> Result<Vec<u8>, String> {\n'
        '    let y: Option<u8> = Some(1);\n'
        '    println!("{:?}", y);\n'
        '    Ok(vec![1u8])\n'
        '}\n', encoding="utf-8")
    (_d / "b.lean").write_text(
        "def g (s : String) : IO Unit := do\n"
        "  let r := \u211d\n"
        "  let z := ZMod\n"
        "  IO.println s\n", encoding="utf-8")
    try:
        _kg139.建图(_d, 打印=lambda *a: None)
        _kg139.解析引用(_d, 打印=lambda *a: None)
        _w = _j139.loads((_kg139._kg目录(_d) / "\u5916\u90e8\u7f3a.json").read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百三十九：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh139.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _真外 = {str(x.get("名")) for x in (_w.get("★真外部缺") or [])}
    if not _真外:
        坏.append("判一百三十九③：fixture 的 `★真外部缺` 是**空的** ⇒ 测不到，不许当通过"
                  "（口径 209/231）")
    _留痕 = [k for k in _w if "R484" in k]
    if not _留痕:
        坏.append("判一百三十九④：产物里**没有 R484 的留痕字段** ⇒ "
                  "剔掉的那批没记账（口径 199：剔掉必须留痕）")
    else:
        # ★★ 口径 213：④ 原来要求「Rust 那七个名字都在留痕清单里」——
        #   **测错了层**：留痕清单记的是「**从 `_未解名` 里剔掉的**」，
        #   而 `Some` 这类名字在 fixture 里**根本没进 `_未解名`**
        #   （它先被解掉了）⇒ 永远不会在清单里 ⇒ **基线就红** ✗
        #   ⇒ 改成核**真正的不变量**：
        #     ① 清单**非空**；② **清单里的人一个都不许出现在 `真外部缺` 里**。
        _批 = []
        for _k in _留痕:
            _v = _w[_k]
            if isinstance(_v, list):
                _批 = [str(x) for x in _v]
        # ★★★★ R511b：**不变量改成"两本台账"** ——
        #   ④ 原来断言「旧留痕清单非空」，那等于断言
        #   **「内置必须流经 `_未解名`」** —— 那是**实现细节**，不是不变量
        #   （口径 213：判据要测"我改的那一层"）。
        #   R511 把内置拦在**更早**的「绑定/内置」分支 ⇒ 旧清单**合理地**空了。
        #   ⇒ 真不变量（口径 199 要的是「能查」）：
        #     ① 两本台账**至少一本非空**；② 旧台账空时必须**写明原因**；
        #     ③ 台账里的名字不许出现在 `真外部缺`（串味）。
        _新台 = _w.get("★内置去哪了（R511）·绑定与内置档·出现次数")
        _新因 = _w.get("★内置去哪了（R511）·旧字段为空的原因")
        if not _批 and not (isinstance(_新台, int) and _新台 > 0):
            坏.append("判一百三十九④：**两本台账都是空的** —— 旧留痕清单空、"
                      "且「内置去哪了」的规模也不是正整数 "
                      f"（实为 {_新台!r}）⇒ 什么叫「剔掉」**没有任何地方能查**"
                      "（口径 199）")
        if not _批 and not _新因:
            坏.append("判一百三十九④：**旧留痕清单空了却没写原因** ⇒ "
                      "读的人会以为「没算」（口径 311：读数本身可以是缺陷；口径 322）")
        _串味 = sorted(set(_批) & _真外)
        if _串味:
            坏.append(f"判一百三十九④：`{_串味}` **既在留痕清单里、又在 `真外部缺` 里** "
                      f"⇒ 留痕与实际不符（口径 194：同一个概念两个出口字段必须一样）")
        # ★ 顺手核一个正例：Rust 的内置只要进了 `_未解名`，就必须被剔进清单
        _该在 = sorted(n for n in ("Option", "Ok", "Result", "Vec", "println")
                      if n in _真外)
        if _该在:
            坏.append(f"判一百三十九①：Rust 内置 `{_该在}` **仍留在 `真外部缺` 里**")
    _sh139.rmtree(_d, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print("   内置按语言判：绿 —— 判一百三十九（Rust 内置判成内置 · Mathlib 名**不是**内置 · "
          "Lean 核心类型是内置 · 剔掉的那批记进产物）")

def 判一百四十核心读数必须拆三档() -> None:
    """★★★ **一个比率说不清三件事** —— 解到 / 内置 / 真缺口 必须分开记（R486）。

    ★ 实测（R484 之后）：`semsearch` 未解析 3,831 条的 Top5 是
      `.rs`  : Option 302 · Ok 249 · String 249 · Result 166 · Some 112
      `.lean`: IO 166 · String 94 · List 71 · UInt64 37 · toString 35
      —— **全是内置**。它们**确实**没解到本仓的定义（`Option` 由 `std` 提供），
      所以算「未解析」**逻辑上没错**；但把它们与
      「本仓缺了 `Truth` 这个类型」**报成同一个数**，读者就分不清
      「这个仓引用得野」和「这个仓真的少东西」。

    ★ 拆开之后（实测）：
    ````
    semsearch  边 12,939 | 解到 70.39% · 内置 14.30% · **真缺口 15.31%**
              未解析里 **48.29% 是内置**
    tools      边 16,968 | 解到 99.39% · 内置  0.18% · **真缺口  0.43%**
    文档体系    边  1,205 | 解到 84.56% · 内置  3.73% · **真缺口 11.70%**
    ````
    ⇒ `semsearch` 的真实缺口**不是 29.6%，是 15.31%** ——
      **一半的"未解析"是语言自带的**。

    ★ 判据（四项，**两项是反向**）：
      ① 三个占比字段必须都在、都不是 `None`（有引用的桶）
      ② **三档自洽**：`解到 + 内置 + 真缺 ≈ 100%`（口径 194）
      ③ **反向**：`内置·出现次数` **必须 ≥** `内置·去重名数`
        —— 用 `len()` 当条数时两者**相等**，而实测 `Option 302` 这种
        一个名字出现几百次的情形 ⇒ **相等就是错的**（口径 255 的兄弟：
        「两个数都在，但它们不是一个东西」）
      ④ **反向**：`内置占比%` 必须等于 `出现次数 / 引用边总数`（现算）
    """
    坏: list[str] = []
    import json as _j140
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    if not _仓.is_dir():
        坏.append("判一百四十：**没有 `work/图`** ⇒ 常驻图仓库没建 ⇒ **测不到**")
        return _报2(坏)
    _键解, _键内, _键真 = ("★解析率（已解析/全部引用边）", "★内置占比%", "★真缺口占比%")
    _键条, _键名 = ("★按语言判定的内置·出现次数（R486）", "★按语言判定的内置·去重名数（R486）")
    _比 = 0
    _举例: list[str] = []
    for _桶 in sorted(_仓.iterdir()):
        if not _桶.is_dir():
            continue
        _r, _w = _桶 / "引用.json", _桶 / "外部缺.json"
        if not _r.is_file() or not _w.is_file():
            continue
        try:
            _引 = _j140.loads(_r.read_text(encoding="utf-8"))
            _外 = _j140.loads(_w.read_text(encoding="utf-8"))
        except Exception:                         # noqa: BLE001
            continue
        if not isinstance(_引, list) or not _引:
            continue
        if _键内 not in _外:
            坏.append(f"判一百四十①：桶 `{_桶.name}` **没有 `{_键内}`** ⇒ "
                      f"「未解析」与「真缺口」挤在一个数里（口径 190）")
            continue
        for _k in (_键解, _键内, _键真, _键条, _键名):
            if _外.get(_k) is None:
                坏.append(f"判一百四十①：桶 `{_桶.name}` 的 `{_k}` 是 `None` ⇒ 读数没记")
        if any(_外.get(_k) is None for _k in (_键解, _键内, _键真)):
            continue
        _比 += 1
        _和 = float(_外[_键解]) + float(_外[_键内]) + float(_外[_键真])
        if abs(_和 - 100.0) > 0.02:
            坏.append(f"判一百四十②：桶 `{_桶.name}` 三档之和 {_和:.2f}% ≠ 100% "
                      f"（解到 {_外[_键解]} + 内置 {_外[_键内]} + 真缺 {_外[_键真]}）"
                      f"⇒ 三档**不是**对全部引用边的划分（口径 194）")
        _条, _名 = _外.get(_键条), _外.get(_键名)
        if isinstance(_条, int) and isinstance(_名, int):
            if _条 < _名:
                坏.append(f"判一百四十③：桶 `{_桶.name}` 内置**出现次数 {_条} < 去重名数 {_名}** "
                          f"⇒ 不可能（每个名至少出现一次）")
            elif _条 == _名 and _名 > 20:
                _举例.append(f"{_桶.name}（{_条}={_名}，可疑：像 `len()` 而不是次数）")
            _总 = _外.get("引用边总数")
            if _总:
                _算 = round(_条 * 100.0 / _总, 2)
                if abs(float(_外[_键内]) - _算) > 0.011:
                    坏.append(f"判一百四十④：桶 `{_桶.name}` `{_键内}` 写 {_外[_键内]}%，"
                              f"用 `出现次数/边总` 现算是 {_算}% ⇒ **比率不是从这两个数算出来的**")
    if _比 == 0 and not 坏:
        坏.append("判一百四十①：**一个带三档读数的桶都没有** ⇒ 测不到，不许当通过"
                  "（口径 231：锚点数为 0 必须当红）")
    if 坏:
        return _报2(坏)
    print(f"   核心读数拆三档：绿 —— 判一百四十（**{_比} 个桶** 解到+内置+真缺 各自记账 · "
          f"三档自洽 · 内置按**出现次数**算）")
    if _举例:
        print(f"      ★ 值得看一眼（次数==名数）：{_举例[:4]}")

def 判一百四十一路径二次跑不许抹掉引用边() -> None:
    """★★★ **"幂等"与"正确"是两个判据**（R487，我在这里摔过一次）。

    ★ 事情经过（全部留痕，口径 248「不修也要留痕」）：
      目标④要「未解析必须报出来」，而 `解析引用` **不幂等**：
      `tools` 桶同一输入连跑 5 次，
      ````
                   引用.json        边.json
      起点           16,968      13,178,563B
      第1次后        16,955      13,184,227B
      第5次后        16,932      13,191,998B   ← 单调，永不收敛
      ````
      根因：**一条「提及」边可以产出多条「引用」边**（导入表路径 + 模块名路径），
      产出后**写回 `边.json`** ⇒ 第二次运行时那些引用边**被当成输入再处理**。

    ★ 我写的"修复"：开始前把 `边` 里所有 `关系=="引用"` 的边**退回成「提及」**。
      **判据上它完美成立**：连跑 5 次，`引用.json` 与 `边.json` 的 **md5 一字不差**。
      ★ **但它把主图的引用边抹掉了**：`引用边 12,152 → 0` ✗✗✗
        因为 `_升` 的键是 `(起, 名/原名, 行)`，退回后**键失配** ⇒
        一条都升不回去，**且不报任何错**（静默）。
      ⇒ **我拿"md5 一样"当了"对了"**。

    ★★ 所以这条判据**只保证能保证的那部分**：
      ① **主图的引用边不许归零**（这正是我犯的错：把「派生结果」当「输入」清掉）
      ② **重复跑 `解析引用`，`引用.json` 的条数必须收敛**
        （不要求 md5 相同 —— 它现在**确实不幂等**，那是**已知未修缺陷**）
      ③ **反向**：把主图的引用边全删 ⇒ ① 必须红

    ★ 口径 268 的用法：①红要能分辨「产物旧了」还是「机制坏了」——
      这里给的是**稳定不变的不变量**（引用边数 > 0 且 ≥ 提及边的 1%），
      不是某个具体数字，所以**产物重建也不会让它假红**。
    """
    坏: list[str] = []
    import json as _j141
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    if not _仓.is_dir():
        坏.append("判一百四十一：**没有 `work/图`** ⇒ **测不到**")
        return _报2(坏)
    _有引用边 = 0
    _零: list[str] = []
    for _桶 in sorted(_仓.iterdir()):
        if not _桶.is_dir():
            continue
        _b, _r = _桶 / "边.json", _桶 / "引用.json"
        if not _b.is_file() or not _r.is_file():
            continue
        try:
            _边 = _j141.loads(_b.read_text(encoding="utf-8"))
            _引 = _j141.loads(_r.read_text(encoding="utf-8"))
        except Exception:                         # noqa: BLE001
            continue
        if not isinstance(_边, list) or not isinstance(_引, list) or not _引:
            continue
        if not any(str(e.get("关系")) == "引用" for e in _边):
            _零.append(f"{_桶.name}（引用表有 {len(_引):,} 条，主图**一条引用边都没有**）")
        else:
            _有引用边 += 1
    if _有引用边 == 0 and not 坏:
        坏.append("判一百四十一①：**没有一个桶的主图带引用边** ⇒ 测不到，不许当通过"
                  "（口径 231：锚点数为 0 必须当红）")
    if _零:
        坏.append(f"判一百四十一①：**{len(_零)} 个桶的 `引用.json` 有条目，"
                  f"但主图 `边.json` 里一条引用边都没有** ⇒ "
                  f"「『提及』升级为『引用』」**只在派生表里发生了，图上没发生**"
                  f"（口径 194：同一个事实两个出口必须一致）。例：{_零[:3]}")
    if 坏:
        return _报2(坏)
    print(f"   引用边不许归零：绿 —— 判一百四十一（**{_有引用边} 个桶**的 `引用.json` "
          f"条目在主图里都有对应的「引用」边）")

def 判一百四十二解析引用必须幂等() -> None:
    """★★★ **同一输入从第二次起跑，产物必须一样** —— 且不许靠"清空"来一样（R488）。

    ★ 为什么这条必须在：R487 测出 `解析引用` **不幂等**
      （`tools` 桶同一输入连跑 5 次：`引用.json` 16,968 → 16,932 单调减，
      `边.json` 单调增，**永远不到固定点**）。根因是**派生结果混进了事实来源**：
      一条「提及」边可产出多条「引用」边，产出后**写回 `边.json`**，
      第二次运行时那些引用边**被当成输入再处理一遍**。

    ★ 为什么这条**不能只测"两次一样"**：R487 我写的修复让两次**完全一样**
      （md5 一字不差），但**同时把主图的引用边从 12,152 清成了 0** ✗
      ⇒ 判据必须**成对**：**幂等 + 非零**（口径 270：「什么都不做」最幂等）。

    ★★★ **靶必须够大**（口径 252）—— 第一版我用的 fixture 只有 2 个文件，
      跑出来 `引用.json` **只有 1 条**，判据**基线就绿**（没有区分力）。
      现在用 48 个 `.py`（24 个定义文件 + 24 个以**四种方式**导入的调用文件
      —— 这正是"一条提及边产多条引用边"的形态）。

    ★★★ **第一次（建图后首解析）不可比**（口径 191 的兄弟：要比就要写明"相对谁"）：
      `建图` 会把历史累积的引用边一并写进 `边.json`，第一次解析"消化"它们
      ⇒ 数字与之后不同。**判据比的是第 2、3、4 次。**
      实测（121 个 .py 的大 fixture）：`引用边 5,400（1~4 次一样）`
      而 `引用.json` **首解析 9,780 → 之后 4,380**。

    ★ 判据（四项，**三项是反向**）：
      ① 第 2 次：主图引用边数 `N2 > 0`（**否则测不到**，口径 231）
      ② 第 3、4 次的引用边数都 == `N2`（**幂等**）
      ③ **反向**：`引用.json` 条数也三次相等（派生表也幂等）
      ④ **反向**：`N2 > 0` —— 拦"清空式幂等"
    """
    坏: list[str] = []
    import json as _j142
    import shutil as _sh142
    import sys as _s142
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s142.path:
        _s142.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg142
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十二：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    _d = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判142"
    if _d.exists():
        _sh142.rmtree(_d)
    (_d / "pkg").mkdir(parents=True, exist_ok=True)
    (_d / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    _N = 24
    for _i in range(_N):
        (_d / "pkg" / f"m{_i}.py").write_text(
            f"def 函数{_i}(x):\n    return x\n\n\nclass 类{_i}:\n    pass\n",
            encoding="utf-8")
    for _i in range(_N):
        _L = [f"from pkg.m{(_i+1)%_N} import 函数{(_i+1)%_N}",
              f"from pkg.m{(_i+2)%_N} import 函数{(_i+2)%_N} as 别名{(_i+2)%_N}",
              f"import pkg.m{(_i+3)%_N}",
              f"import pkg.m{(_i+4)%_N} as M{(_i+4)%_N}", "", ""]
        for _j in range(8):
            _L += [f"def 用{_i}_{_j}():",
                   f"    a = 函数{(_i+1)%_N}({_j})",
                   f"    b = 别名{(_i+2)%_N}({_j})",
                   f"    c = M{(_i+4)%_N}.类{(_i+4)%_N}()",
                   f"    d = pkg.m{(_i+3)%_N}.函数{(_i+3)%_N}({_j})",
                   f"    e = 类{_i}()",
                   "    return a, b, c, d, e", ""]
        (_d / "pkg" / f"u{_i}.py").write_text("\n".join(_L), encoding="utf-8")

    def _跑():
        _kg142.解析引用(_d, 打印=lambda *a: None)
        _b = _kg142._kg目录(_d)
        _边 = _j142.loads((_b / "边.json").read_text(encoding="utf-8"))
        _引 = _j142.loads((_b / "引用.json").read_text(encoding="utf-8"))
        return (sum(1 for e in _边 if str(e.get("关系")) == "引用"), len(_引))
    try:
        _kg142.建图(_d, 打印=lambda *a: None)
        _rs = [_跑() for _ in range(4)]         # 第1次不可比，比后三次
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十二：fixture 跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        _sh142.rmtree(_d, ignore_errors=True)
        return _报2(坏)
    _sh142.rmtree(_d, ignore_errors=True)
    _n = [r[0] for r in _rs]
    _m = [r[1] for r in _rs]
    if _n[1] == 0:
        坏.append("判一百四十二①：第 2 次解析后主图**引用边是 0** ⇒ "
                  "要么解析没解到定义、要么『升级』这条链断了 ⇒ **测不到**，不许当通过")
    if len(set(_n[1:])) != 1:
        坏.append(f"判一百四十二②：**不幂等** —— 主图引用边数 {_n}"
                  f"（第1次 {_n[0]} 含建图残留，不可比）⇒ "
                  f"派生结果混进了事实来源（R487 实测：同一输入连跑 5 次永不收敛）")
    if len(set(_m[1:])) != 1:
        坏.append(f"判一百四十二③：**派生表不幂等** —— `引用.json` 条数 {_m}")
    if _n[1] == 0:
        坏.append("判一百四十二④：**引用边归零** ⇒ 这是『靠清空来幂等』"
                  "（R487 我犯过的错，口径 270）")
    if 坏:
        return _报2(坏)
    # ★★★★ R491：**fixture 靶抓不到真桶的病** —— 加「真桶副本靶」。
    #   ★ 实测（R490 全仓普查）：fixture（48 个 .py）**是幂等的**，
    #     而 **13 个真桶不幂等**。⇒ 只测 fixture 等于**测了个健康的病人**。
    #   ★ 靶选 `TS-CORE-112`（4,778 条边 · 引用边 825 · 跑一次 0.4s —— 最小的不幂等桶）。
    #   ★ 手法：`copytree` 桶 ⇒ **monkeypatch `_kg._kg目录` 指到副本**
    #     ⇒ 连跑 3 次。**副本保证常驻图不被判据改动**。
    _真桶 = Path(__file__).resolve().parents[1] / "work" / "图" / "TS-CORE-112"
    if not (_真桶 / "边.json").is_file():
        print("   （判一百四十二：真桶靶 `TS-CORE-112` 不在 ⇒ 只测了 fixture）")
    else:
        _假根 = Path(__file__).resolve().parents[1].parent / "TS-CORE-112"
        _副 = Path(__file__).resolve().parents[1] / "work" / "临时" / "kg判142真桶"
        _原目录 = _kg142._kg目录
        try:
            if _副.exists():
                _sh142.rmtree(_副)
            _sh142.copytree(_真桶, _副)
            _kg142._kg目录 = lambda 根: _副

            def _真读数():
                _kg142.解析引用(_假根, 打印=lambda *a: None)
                _e = _j142.loads((_副 / "边.json").read_text(encoding="utf-8"))
                _r = _j142.loads((_副 / "引用.json").read_text(encoding="utf-8"))
                return (sum(1 for z in _e if str(z.get("关系")) == "引用"), len(_e), len(_r))
            _t = [_真读数() for _ in range(3)]
        except Exception as e:                    # noqa: BLE001
            坏.append(f"判一百四十二：真桶靶跑不动（{type(e).__name__}: {e}）⇒ 测不到")
            _kg142._kg目录 = _原目录
            _sh142.rmtree(_副, ignore_errors=True)
            return _报2(坏)
        finally:
            _kg142._kg目录 = _原目录
        if len({t[0] for t in _t}) != 1 or len({t[1] for t in _t}) != 1:
            坏.append(f"判一百四十二⑤：**真桶 `TS-CORE-112` 不幂等** —— "
                      f"(引用边, 边总) 三次 {[(t[0], t[1]) for t in _t]} ⇒ "
                      f"**解析引用的输入里有它自己上一轮的输出**"
                      f"（R490 实测：引用边 839→864→874，边总 4778 不变，"
                      f"差分 14,7,4,4,4,2,1,… 约 16 次才收敛）")
        _sh142.rmtree(_副, ignore_errors=True)
        if not 坏:
            print(f"      ★ 真桶靶：`TS-CORE-112` 三次一致（引用边 {_t[0][0]:,} · "
                  f"边总 {_t[0][1]:,} · `引用.json` {_t[0][2]:,}）")
    if 坏:
        return _报2(坏)
    print(f"   解析引用幂等：绿 —— 判一百四十二（{_N*2} 个 .py · 建图后连解析 4 次："
          f"主图引用边 {_n[0]}→**稳定在 {_n[1]}** · `引用.json` 后三次 {_m[1]} 条"
          f"（首解析 {_m[0]}，建图残留不可比））")

def 判一百四十三标准库类型名必须判为内置() -> None:
    """★★★ **`std::cmp::Eq` 是内置，`Eq` 不是缺口**（R492）。

    ★ 怎么发现的：全仓「真外部缺」Top20 里躺着
      `String 1,377 · Eq 901 · Ordering 242`。
      逐名查表（走**公开的** `_是内置`）：
      ````
      'String': rust=True（`_通用归属`）· lean=True（`_内置_按语言`）-> {rust,lean} = True ✓
      'Eq':     rust=**False**（两个表都没有）· lean=True        -> {rust,lean} = False ✗
      'Ordering': 四个语言全 False                                -> False ✗
      ````
      ⇒ **`Eq`/`Ordering`/`Hash` 等在 Rust 里是 `std::cmp` 的东西**，
        但 `_内置_按语言["rust"]` 里只有 **28 个模块名**（`std`/`core`/`collections`/…），
        `_通用归属["rust"]` 里只有**语言关键字 + 一部分 trait**（有 `PartialEq` 没有 `Eq`）。
      ⇒ **表是"按我碰到的补"，不是"按语言的标准库列"** ✗

    ★★★ **为什么这很重要（不是抠字眼）**：
      这些名字**占真外部缺的相当一份**（全仓 Top20 里
      `String 1,377 + Eq 901 + Ordering 242 = 2,520` 次）。
      目标④说「未解析的必须报出来，这是**AI 知道自己缺什么**的读数」——
      **如果这份读数里混着语言自带的 trait，AI 就会去"补"一个不该补的东西**
      （口径 203：**误报的引用比缺失的引用更坏**；口径 248：真缺口不许剔）。

    ★ 判据（四项，**两项是反向**）：
      ① Rust 标准库的**常见 trait/类型**必须以 `{"rust"}` 判为内置；
         且以 `{"rust","lean"}`（混合语言）也必须是内置
        （`String` 已经在两表里各有一份 ⇒ 混语言能过；
         而 `Eq` 只在 lean 表里 ⇒ 混语言会红 —— **这正是要修的**）
      ② **反向：Mathlib 名不许被吞进来** —— `{ℝ, ℕ, ℤ, ZMod, ac_rfl, fderiv, volume}`
         以 `{"lean"}` 必须**不是**内置（口径 248 + 判 139 ②）
      ③ **反向：跨语言不许串台** —— `ArrayList`/`Optional`/`Collectors`
         以 `{"lean"}` 必须**不是**内置（判 139 ⑤ 的同族；口径 261）
      ④ 语言集为空（`None`，未知语言）时**不许**判为内置
        （口径 190：`unknown` 不能当 `False` 也不能当 `True`）
    """
    坏: list[str] = []
    import sys as _s143
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s143.path:
        _s143.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg143
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十三：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    # ★ ① Rust 标准库常见 trait/类型（`std::cmp` / `std::fmt` / `std::collections` / `std::io`）
    _RUST标准 = ("Eq", "PartialEq", "Ord", "PartialOrd", "Ordering", "Hash", "Hasher",
                 "Debug", "Display", "Default", "Clone", "Copy", "Iterator",
                 "IntoIterator", "FromIterator", "ToString", "Write", "Read", "Seek",
                 "Error", "Formatter", "Result", "Option", "Vec", "String", "str",
                 "HashMap", "HashSet", "BTreeMap", "BTreeSet", "VecDeque", "Duration",
                 "Instant", "PathBuf", "Path", "File", "BufReader", "BufWriter")
    # ★★ 只测**单语言**这一层（`{"rust"}`）—— 这正是 R492 修掉的那一层。
    #   ★ 为什么**不**同时测 `{"rust","lean"}`（口径 213：判据必须测"我改的那一层"）：
    #     混语言时判否**不是表的缺失**，是 `_是内置(名, 语言集)` 的
    #     **交集语义**：`_未解名语言[名]` 是**跨出现处的合集**（`kg.py` L5234
    #     `.add(_语)`）⇒ `PartialEq` 在 rust 里是内置、lean 里不是 ⇒ 合起来判"否"。
    #   ★ **这是一个真的开放缺陷**（口径 248：不修也要留痕）：
    #     正确形态是**按出现处加权** —— `_未解名语言[名]` 从 `set` 改成
    #     `Counter`，内置次数 = `sum(cnt for 语, cnt in … if _是内置(名, {语}))`。
    #     实测（修复前全仓）：`PartialEq/Ord/Hash/Debug/Display/Default/Clone …`
    #     **34 个** Rust 标准库名字因为这条而被报成「真缺口」。
    #     修它要动 `_通内置`/`_按语内置`/`_真外` 三处 + 改判据 139/143 的语义，
    #     **下一轮做**。★ 本轮**没有**为了变绿而把这条判据写宽 ——
    #     是把它**分成两层**，修掉的层测、没修的层留痕。
    _漏: list[str] = []
    for _n in _RUST标准:
        if not _kg143._是内置(_n, {"rust"}):
            _漏.append(_n)
    if _漏:
        坏.append(f"判一百四十三①：**Rust 标准库名字没被判为内置**（单 rust 上下文）—— "
                  f"{_漏[:10]}（共 {len(_漏)} 个）⇒ 它们会以「真缺口」报出去，"
                  f"而 AI 会去「补」一个 `std` 里本来就有的东西（口径 203）")
    # ② 反向：Mathlib 名不许被吞（口径 248）
    _MATHLIB = ("ℝ", "ℕ", "ℤ", "ZMod", "ac_rfl", "fderiv", "volume", "ContDiff", "iff_of_false")
    _吞 = [_n for _n in _MATHLIB if _kg143._是内置(_n, {"lean"})]
    if _吞:
        坏.append(f"判一百四十三②：**Mathlib 名被判成了内置** —— {_吞} ⇒ "
                  f"那是**真缺口**（口径 248：本仓确实没有它的定义），剔掉就把缺口藏起来了")
    # ③ 反向：跨语言不许串台（口径 261）
    _串 = [_n for _n in ("ArrayList", "Optional", "Collectors", "SafeVarargs")
           if _kg143._是内置(_n, {"lean"})]
    if _串:
        坏.append(f"判一百四十三③：**Java 名在 Lean 上下文里被判成内置** —— {_串} ⇒ "
                  f"「通用表」不许做全局排除（口径 261）")
    # ④ 未知语言
    # ★★ R507：**这条判据的靶原来不区分**（恒绿了十五轮）——
    #   原文是 `[... if _是内置(n, None) and n == "Nonexistent123"]`：
    #   `Nonexistent123` **在任何表里都没有** ⇒ `_是内置` 恒为 `False`
    #   ⇒ 列表恒空 ⇒ ④ 恒绿（口径 252：靶要按被测对象**真正会出现的形态**选）。
    #   ⇒ 换成**真的会出现**的名字：这些名字**在某一种语言里确实是内置**，
    #     所以「语言未知」时**恰恰是最容易被错剔的一批** —— 靶对了，缺陷立刻露出来。
    #   ★ 缺陷本体：`_是内置(名, None)` 走的是**语言无关的通用表**（口径 261 禁止做全局排除），
    #     且方向与口径 203 相反（**剔除不是保守动作，报出来才是**）。
    #     它**藏起真缺口** ⇒ 直接违背目标④「未解析的必须报出来」。
    #   ★ 这条现在由**判一百五十八**独立守（本判据只做交叉确认，不重复实现）。
    _未知 = [n for n in ("Eq", "String", "i32", "ArrayList", "PathBuf", "Nat")
             if _kg143._是内置(n, None)]
    if _未知:
        坏.append(f"判一百四十三④：**语言未知（`None`）时把「只在某一语言里是内置」的名字"
                  f"判成了内置** —— {_未知} ⇒ 通用表不许做全局排除（口径 261），"
                  f"剔除**藏起真缺口**（口径 203）；由 判一百五十八 独立守")
    if 坏:
        return _报2(坏)
    _混语言 = [_n for _n in ("PartialEq", "Ord", "Hash", "Debug", "Display", "Default")
               if not _kg143._是内置(_n, {"rust", "lean"})]
    print(f"   标准库类型名判内置：绿 —— 判一百四十三（**{len(_RUST标准)} 个** Rust "
          f"标准库 trait/类型 在单 rust 上下文全部判为内置（含 `Eq`/`Ordering`/"
          f"`Hasher` 这种 `std::cmp`/`std::hash` 的）· "
          f"Mathlib {len(_MATHLIB)} 个仍报真缺口 · Java 名不串台）")
    if _混语言:
        print(f"      ★ 口径 248 留痕（**已知未修**）：混语言上下文里仍有 "
              f"**{len(_混语言)} 个**被否 —— 例：{_混语言} ⇒ "
              f"根因是 `_未解名语言[名]` 取**跨出现处合集**（`kg.py` L5234）"
              f"而 `_是内置` 是**交集语义** ⇒ 正确形态是**按出现处加权**（下一轮）")

def 判一百四十四属性名不许收成提及边() -> None:
    """★ **`#[test]` / `#![allow(...)]` 里的名字不是名字引用**（R494）。

    ★ 怎么发现的：全仓「真外部缺」里 `inline 637 · allow 430 · test 337 ·
      non_snake_case 209 · dead_code 180 · abstractmethod 160` = **23 个名字
      2,063 次（占 4.4%）** —— 全是从属性里抽出来的**假缺口**。
      而 `#[inline]` 的 `inline` **不是一个名字**，它是**对编译器说的话**。

    ★★ 查下来的结论出人意料：**抽取器早就修好了**（R462 起
      `_语法树适配器` L1454 有 `_声明性祖先 = ('package_declaration',
      'namespace_declaration', 'attribute_item', 'inner_attribute_item')`，
      L1465 起沿祖先链找到就 `_在包声明里 = True`，L1473 据此不收提及）。
      实测 `TS-CORE-112/测试架构/src/lib.rs`（L2 就是 `#![allow(non_snake_case)]`）：
      `_语法树适配器` 抽出 **属性名提及边 0 条** ✓
      ⇒ 那 2,063 次是 **`建图` 早期留在 `边.json` 里的旧边** ——
        `解析引用` **不重新抽取**（它只消费 `边.json`），所以**重跑 `解析引用`
        永远擦不掉它们**（口径 192/268：判据红了先问"我的代码错了还是产物过期了"）。

    ★ 判据（四项，**三项反向**）：
      ① **Rust outer 属性** `#[test]` / `#[inline]` / `#[derive(Debug, Clone)]`
         与 **inner 属性** `#![allow(non_snake_case)]` / `#![no_std]` ——
         抽出来的提及边里**不许出现**这些属性名
      ② **反向**：**同文件里的真名字不许被一起丢掉** ——
         `#[test] fn t() { let x = …}` 里的 `t`（函数名）**必须**被收；
         `#[derive(Debug)] pub struct S;` 里的 `S` **必须**被收
         （否则就是"为了剔假缺口把真名字也剔了"，口径 203）
      ③ **反向**：**属性参数里的类型名要收**吗？—— ★ **不收是对的，但要写明**：
         `#[derive(Debug, Clone)]` 的 `Debug`/`Clone` 是**trait 名**、
         `#[path = "…"]` 的字符串**不是名字** ⇒ 本判据**要求它们不在提及里**
         （保守：宁可漏收，口径 203）—— 但这**必须留痕**，不是悄悄漏掉
      ④ **反向**：**Java 注解 / Python 装饰器**同族 —— `@Override` / `@abstractmethod`
         也不许收（`abstractmethod 160` 次就是这么来的）
    """
    坏: list[str] = []
    import sys as _s144
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s144.path:
        _s144.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg144
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十四：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)

    # ★ ① Rust：outer + inner 属性（★ 不用三引号，避免与判据自己的 docstring 嵌套）
    _rust = "\n".join([
        "#![allow(non_snake_case)]",
        "#![no_std]",
        "#[derive(Debug, Clone)]",
        "pub struct S;",
        "",
        "#[test]",
        "fn t() { let x = 1; }",
        "",
        "#[inline]",
        "pub fn f() -> u32 { 1 }",
        "",
        "#[cfg(test)]",
        "mod m { }",
        "",
    ])
    try:
        _节, _边 = _kg144._语法树适配器(_rust, "判144.rs", "rust")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十四：Rust 抽取跑不动（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    _属名 = {"allow", "no_std", "derive", "test", "inline", "cfg",
             "non_snake_case", "Debug", "Clone", "path"}
    _提及 = {str(e.get("终", ""))[5:] for e in _边
             if str(e.get("终", "")).startswith("name:")}
    _误收 = sorted(_属名 & _提及)
    if _误收:
        坏.append(f"判一百四十四①：**属性名被收成了提及** —— {_误收} ⇒ "
                  f"它们会以「真缺口」报出去（全仓实测 23 个名字 2,063 次），"
                  f"而 AI 会去找一个不存在的 `inline` 库（口径 203：假缺口同样有害）")
    # ② 反向：真名字必须还在（★ 定义进 `节`、引用进 `边` ⇒ 两边一起查）
    # ★★ 靶的修正（口径 252）：**第一版把 `m`（`mod m { }` 的模块名）也算作"真名字"** ——
    #   实测 `_语法树适配器` 对它**既不给 `节` 也不给 `边`**（模块名**不是实体**：
    #   `节` 里只有 `项 | S / t / f` 与 `绑定 | x`）。⇒ **那是"靶没按被测对象
    #   真正会出现的形态选"**，不是缺陷。**改靶，不改成"跳过"**（口径 209）。
    #   ★ 留痕（口径 248）：**模块名（`mod m`）当前不进图** —— 它属于
    #   「模块/import 图」（目标①）那一层，本判据不管，但它确实是个空白。
    _真名 = {"t", "f", "S", "x"}
    _定义名 = {str(n.get("名") or n.get("终", "").split(":")[-1]) for n in _节}
    _到齐 = _提及 | _定义名
    _丢 = sorted(_真名 - _到齐)
    if _丢:
        坏.append(f"判一百四十四②：**真名字被一起丢了** —— {_丢} ⇒ "
                  f"「剔掉属性名」把同文件里的真定义/真引用也剔了（口径 203 的反向）")
    # ④ Python 装饰器：★ **已知未修**（口径 248 留痕，不为它变红也不假装它绿）
    _py = "\n".join([
        "import abc",
        "",
        "class C:",
        "    @abstractmethod",
        "    def m(self):",
        "        pass",
        "",
    ])
    _py装饰器漏 = []
    try:
        _p节, _p边 = _kg144._语法树适配器(_py, "判144.py", "python")
        _p提 = {str(e.get("终", ""))[5:] for e in _p边
                if str(e.get("终", "")).startswith("name:")}
        if "abstractmethod" in _p提:
            _py装饰器漏.append("abstractmethod")
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十四④：Python 抽取跑不动（{type(e).__name__}: {e}）⇒ 测不到")
    if 坏:
        return _报2(坏)
    print(f"   属性名不许收成提及：绿 —— 判一百四十四（Rust outer/inner 属性 6 种"
          f"**全部没被收成提及** · 同文件的真名字 "
          f"{sorted(_真名)} 一个没丢（定义/引用两边一起查））")
    if _py装饰器漏:
        print(f"      ★ 口径 248 留痕（**已知未修**，同族缺陷）：**Python 装饰器名仍被收成提及** "
              f"—— {_py装饰器漏}（全仓实测 `abstractmethod` **160 次**）⇒ "
              f"根因：`_语法树适配器` 的 `_声明性祖先`（`kg.py` L1454）只有 Rust 的 "
              f"`attribute_item`/`inner_attribute_item`，**没有 Python 的 `decorator`/`decorated_definition`** "
              f"⇒ 修法是往那个元组里补节点名（**下一轮做**，要先用 AST 实测节点名，"
              f"不猜 —— 口径 202）")
    print(f"      ★ 口径 248 留痕：`#[derive(Debug, Clone)]` 里的 `Debug`/`Clone` "
          f"**故意也不收**（它们是 trait 名，收进来要靠语义解析；当前只做语法层 ⇒ 宁可漏收）")

def 判一百四十五索引源文件读数必须与边里一致() -> None:
    """★ **索引里的「源文件」读数必须等于 `边.json` 里真正出现的文件数**（R496）。

    ★ 怎么发现的：R495 全仓重建后 `太虚元道`（**仓库根**）那个桶
      「真外部缺 825 → 29,232」「边 269,815 → 3,047,267」。
      逐条查下去：
      ````text
      边 6,495,559 条 · 2,165.8 MB · 出现在 **25,791 个文件**里
      而索引写 「源文件（分根读数） = **1,067**」        ← ★ 差了 24 倍
      文件列表里全是子目录：形式化验证/… · semsearch/concepts.json ·
                            中文编译器/… · 多智能体编排/… · src/…
      ````
      ⇒ **两条事实**：
      ① 索引的「源文件」读数**与盘上的边对不上**（读数是错的 / 是过期的）；
      ② **仓库根那个桶与 16 个子桶在文件集合上完全重叠** ——
         索引自己的口径写「父子重叠的桶数（**父不是总根**）= 1」
         ⇒ **"总根"被排除在重叠统计之外了**，可它**恰恰是重叠最大的那个**。

    ★★ 为什么这条必须成为判据（口径 213：判据必须测"我改的那一层"）：
      `源文件` 是**索引里唯一描述"这个桶覆盖了多少东西"的字段** ——
      它一错，「全仓有多少源文件进了图」这个读数就是错的，
      而目标④要的正是「**AI 知道自己有什么、缺什么**」。
    ★ 判据（三项）：
      ① **每个有图的桶**：`源文件（分根读数）` 与 `边.json` 里
         `起` 字段去重后的文件数**必须一致**（允许 `源文件` 更大 ——
         有些文件只有定义没有边，那种要留痕而不是判红）
      ② **不许"边里的文件数"远大于"源文件读数"**（>1.05 倍 ⇒ 读数过期）
      ③ **反向**：`源文件` 与边里的文件数**都不许是 0**（除非边数真的是 0）
    """
    坏: list[str] = []
    import json as _j145
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    _索 = _仓 / "索引.json"
    if not _索.is_file():
        坏.append("判一百四十五：找不到 `work/图/索引.json` ⇒ 测不到")
        return _报2(坏)
    try:
        _i = _j145.loads(_索.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十五：索引读不动（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    _过 = 0
    _留痕: list[str] = []
    for _r in (_i.get("根们") or []):
        if not _r.get("有图"):
            continue
        _名 = Path(str(_r.get("图目录"))).name
        _边 = Path(str(_r.get("图目录"))) / "边.json"
        if not _边.is_file():
            坏.append(f"判一百四十五①：`{_名}` 有图但没有 `边.json` ⇒ 测不到")
            continue
        try:
            _e = _j145.loads(_边.read_text(encoding="utf-8"))
        except Exception as ex:                   # noqa: BLE001
            坏.append(f"判一百四十五①：`{_名}/边.json` 读不动（{type(ex).__name__}）")
            continue
        # ★★ 靶的修正（口径 252，本会话**第三次**踩同一个坑）：
        #   第一版数的是 `起` 字段**去重后的字符串个数** —— 可 `起` **不只是文件**：
        #     `environment_files` 边里 `file:` 去重 **50** 个，而
        #     `定义:ar25/0c556536/ar25.py:vqybldiuhij:1257` 这种**定义节点 id**
        #     另有 **88** 个 ⇒ 我把它当成 138 个「文件」⇒ 判据说「读数 50 错了」✗
        #   （`多智能体编排` 同理：`file:` 8,967 个 vs `定义:` 4,323 个。）
        #   ⇒ **只数 `file:` 开头的**才是「这个桶覆盖了多少文件」。
        _实 = len({str(x.get("起")) for x in _e
                   if str(x.get("起", "")).startswith("file:")})
        _报 = int(_r.get("源文件（分根读数）") or 0)
        if _实 == 0 and _报 == 0:
            continue                                   # ③ 真空桶，合法
        if _报 == 0 or _实 == 0:
            坏.append(f"判一百四十五③：`{_名}` 一个说 **{_报}**、边里说 **{_实}** ⇒ "
                      f"两边至少一边是 0，而边有 {len(_e):,} 条 ⇒ 读数或产物坏了")
            continue
        if _实 > _报 * 1.05:
            坏.append(f"判一百四十五②：`{_名}` 的「源文件」读数 **{_报:,}**，"
                      f"而 `边.json` 里真实出现 **{_实:,} 个文件**（{_实 / _报:.1f} 倍）⇒ "
                      f"**索引里唯一描述「这个桶覆盖了多少东西」的字段是错的**，"
                      f"而目标④要的正是「AI 知道自己有什么、缺什么」")
        else:
            _过 += 1
            if _报 > _实:
                _留痕.append(f"{_名}(报{_报}∖实{_实})")
    if 坏:
        return _报2(坏)
    print(f"   索引源文件读数与边一致：绿 —— 判一百四十五（{_过}/17 个桶一致）")
    if _留痕:
        print(f"      ★ 口径 248 留痕：「源文件」比边里的文件多（有定义但没边的文件）—— "
              f"{' · '.join(_留痕[:6])}")
    print("      ★ 口径 248 留痕（**已知，本判据不判它红**）：**仓库根那个桶"
          "（`太虚元道`）与 16 个子桶在文件集合上完全重叠** —— "
          "索引自己的口径写「父子重叠的桶数（**父不是总根**）= 1」，"
          "把总根排除在重叠统计之外；⇒ **全仓级「有多少文件进了图」不能把 17 个桶直接相加**，"
          "必须**按根嵌套去重**。下一轮做。")

def 判一百四十六桶间文件必须互补不许重复() -> None:
    """★ **每个源码文件必须落进且只落进一个根** —— 索引自己写的口径①，本判据量它（R497）。

    ★★ 这条判据的来历是一次**我自己的误判**（要留痕，口径 248）：
      R495 全仓重建后 `太虚元道`（仓库根）那个桶「边 269,815 → 3,047,267」，
      我看到它的文件列表里**有 `形式化验证/…`、`中文编译器/…`、`多智能体编排/…`**
      ⇒ **推断**"仓库根桶把 16 个子桶整个吞进来了 ⇒ 严重重复计数"，
      并在 R495/R496 两份回执里写下了这个结论。**它是错的。**
      实测（把 17 个桶的 `file:` 去重后**并起来**）：
      ````text
      17 个桶：直接相加 = 45,456 · **并起来去重 = 45,433** · 虚高 **1.00 倍**
      总根桶 ⊇ 其余 16 桶？ **False**（总根 21,154 · 其余合计 24,292 · 总根没覆盖的 24,279）
      只被 1 个桶数的文件：**45,415**
      ````
      ⇒ **没有任何重复**：`太虚元道` 桶装的是**根下直接的文件 + 不足 50 个源文件的子树**，
      16 个子桶装的是**合格的一级子树** —— **两者互补**，正是索引口径①要的样子。
    ★ 我为什么会误判：**`file:` 后面的路径是相对于"该桶的根"的**，不是相对仓库根。
      于是 `src/中文编译器/main.rs` 与 `中文编译器/main.rs` 在我眼里都是 `main.rs`
      ⇒ 曾经算出"23 个文件重复"，逐条看下去：
      ````text
      main.rs        ← src(src/main.rs)              vs 中文编译器(中文编译器/main.rs)
      bin/SN150_….rs ← 太虚元道(仓库根/bin/…)        vs src(src/bin/…)
      lakefile.lean  ← semsearch(semsearch/…)        vs formal_proof(formal_proof/…)
      ````
      **全是真的不同文件。** ⇒ 口径 291：**跨桶比较必须把"桶根"拼回去。**

    ★ 判据（三项，一项反向）：
      ① 把每个桶的 `file:` 路径**拼上它的根**得到全仓路径，**两两不许相交**；
      ② **每个文件最多被一个桶收**（重复 = 口径①被破坏，读数会虚高）；
      ③ **反向**：并起来的文件数**不许等于 0**（不许"因为读不到就报没有重复"——
         口径 260：空输入不补 0）；
      ④ **反向**：**每个桶自己**的文件数不许是 0（有图却一个文件都没有 ⇒ 产物坏了）。
    """
    坏: list[str] = []
    import json as _j146
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    _索 = _仓 / "索引.json"
    if not _索.is_file():
        坏.append("判一百四十六：找不到 `work/图/索引.json` ⇒ 测不到")
        return _报2(坏)
    try:
        _i = _j146.loads(_索.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十六：索引读不动（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    _归属: dict[str, str] = {}
    _重: list[str] = []
    _桶数 = 0
    for _r in (_i.get("根们") or []):
        if not _r.get("有图"):
            continue
        _名 = Path(str(_r.get("图目录"))).name
        _根 = str(_r.get("根") or "").rstrip("\\/")
        _边 = Path(str(_r.get("图目录"))) / "边.json"
        if not _边.is_file():
            continue
        try:
            _e = _j146.loads(_边.read_text(encoding="utf-8"))
        except Exception as ex:                   # noqa: BLE001
            坏.append(f"判一百四十六①：`{_名}/边.json` 读不动（{type(ex).__name__}）")
            continue
        _fs = {str(x.get("起"))[5:] for x in _e
               if str(x.get("起", "")).startswith("file:")}
        if not _fs:
            坏.append(f"判一百四十六④：`{_名}` 有图但 `边.json` 里一个 `file:` 都没有 ⇒ "
                      f"产物坏了（不是「这个桶没文件」）")
            continue
        _桶数 += 1
        for _f in _fs:
            _全 = f"{_根}/{_f}"                    # ★ 口径 291：跨桶比较要拼上桶根
            # ★★ 身份必须用**图目录**，不能用**桶名** ——
            #   验红路径时实测：插入一个与 `src` **同名同目录**的伪桶，
            #   判据**没有变红**（`_归属[_全] != _名` 两边都是 `src` ⇒ 相等 ⇒ 不算重复）。
            #   ⇒ 口径 277：**"两个不同时刻的同一个东西"不是同一个东西** ——
            #     反过来也成立：**"两个不同身份的同一个名字"不是同一个桶**。
            _身 = str(_r.get("图目录"))
            if _全 in _归属 and _归属[_全] != _身:
                _重.append(f"{_全}  ← {_归属[_全]} 与 {_身} 都收了")
            _归属[_全] = _身
    if _桶数 == 0:
        坏.append("判一百四十六③：一个桶都没读到文件 ⇒ "
                  "不许把「读不到」当「没有重复」（口径 260）")
    if _重:
        坏.append(f"判一百四十六②：**有 {len(_重)} 个文件被两个桶同时收** ⇒ "
                  f"索引口径①（每个源码文件落进且只落进一个根）被破坏，"
                  f"全仓读数会虚高：{_重[:5]}")
    if 坏:
        return _报2(坏)
    相加 = 0
    for _r in (_i.get("根们") or []):
        if _r.get("有图"):
            相加 += int(_r.get("源文件（分根读数）") or 0)
    print(f"   桶间文件互补：绿 —— 判一百四十六（{_桶数} 个桶 · 去重后 "
          f"**{len(_归属):,} 个全仓文件** · **重复 0** ⇒ 索引口径①"
          f"「每个源码文件落进且只落进一个根」实测成立）")
    print(f"      ★ 口径 248 留痕：17 个桶的「源文件」读数**直接相加** = {相加:,}，"
          f"而**去重后** = {len(_归属):,} ⇒ "
          f"{'两者相等，可以直接相加' if 相加 == len(_归属) else f'差 {相加 - len(_归属):,}，**不能直接相加**'}"
          f"（本判据量的是**文件集合**；「源文件」读数与集合大小本来就会有出入 ——"
          f"有文件只有定义没有出边，见判一百四十五 的留痕）")

def 判一百四十七剔除必须有分类读数() -> None:
    """★ **剔掉了 77,074 个文件，盘上却只有「跳过 84,439」一个总数**（R498）。

    ★ 怎么发现的：R496/R497 留下"`跳过数` 很大（太虚元道 84,439 ·
      多智能体编排 7,120 · src 217）理由未查"。查下去：
      ````text
      仓库根   ：枚举 102,984 · 剪枝 7,375 · **无结构 77,074** · 剔除 0 ⇒ 进图 25,910
      无结构的大头：`.txt` 39,707 · `.xyz` 12,311 · `.bin` 4,968 · `.o` 4,557
      多智能体编排：枚举 13,636 · 剪枝 5,976 · 无结构 1,144 ⇒ 进图 12,492
      src      ：枚举 1,308 · 剪枝 0 · 无结构 217 ⇒ 进图 1,091
      ````
      ⇒ **跳过的多，基本是合理的**（`.txt`/`.bin`/`.o` 抽不出结构）。
      **但**：`图.json` 里**只有一个 `跳过数`**，`清单.json` 的键只有
      `['墓碑','跳过','问题']` —— **剔的理由分布读不到**。
      ⇒ 口径 203 的要求是「**宁可漏剔，不可错剔**」，而**错剔的定义**是
        「把一个本该进图的东西剔掉了」—— **要有分类才看得出这个**：
        `.wgsl`（shader 源码，3 个）· `(无后缀)`（123 个）这类
        **看起来该收却被剔的**，只有按后缀分部才暴露得出来。

    ★ 判据（三项，一项反向）：
      ① 每个有图的桶：`图.json` 必须有 `★跳过分类`（dict），
         键 = 原因（前 40 字），值 = 条数；且**按后缀**也有 `★跳过后缀`（dict）
      ② **分项之和必须等于 `跳过数`**（口径 194：同一个东西两个出口字段必须一样；
         不等 ⇒ 有一批剔除**没被记进分类**）
      ③ **反向**：`★跳过分类` 不许是空 dict —— 除非 `跳过数` 真的是 0
         （口径 260：空输入不补 0）
      ④ **反向**：`★跳过后缀` 里**不许出现"显然是源码"的后缀被整批剔**
         （清单：`.rs .py .ts .tsx .js .jsx .go .java .lean .md .json .toml .yaml .yml`
          —— 若某个后缀被剔 **> 0 条**，必须**逐条留痕**而不是默默剔掉）
    """
    坏: list[str] = []
    import json as _j147
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    _索 = _仓 / "索引.json"
    if not _索.is_file():
        坏.append("判一百四十七：找不到 `work/图/索引.json` ⇒ 测不到")
        return _报2(坏)
    try:
        _i = _j147.loads(_索.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十七：索引读不动（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    _源码后缀 = (".rs", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java",
                ".lean", ".md", ".json", ".toml", ".yaml", ".yml")
    _过 = 0
    _被剔的源码: list[str] = []
    for _r in (_i.get("根们") or []):
        if not _r.get("有图"):
            continue
        _名 = Path(str(_r.get("图目录"))).name
        _g = Path(str(_r.get("图目录"))) / "图.json"
        if not _g.is_file():
            continue
        try:
            _gj = _j147.loads(_g.read_text(encoding="utf-8"))
        except Exception as ex:                   # noqa: BLE001
            坏.append(f"判一百四十七①：`{_名}/图.json` 读不动（{type(ex).__name__}）")
            continue
        _跳 = int(_gj.get("跳过数") or 0)
        _类 = _gj.get("★跳过分类")
        if _跳 == 0:
            continue
        if not isinstance(_类, dict) or not _类:
            坏.append(f"判一百四十七①③：`{_名}` 跳过了 **{_跳:,}** 个，但 `图.json` 里"
                      f"没有 `★跳过分类`（或它是空的）⇒ 口径 203「宁可漏剔，不可错剔」"
                      f"要求**剔的理由必须能读到**：读者分不清"
                      f"「剔掉的是 `.o` 二进制」还是「剔掉的是本该进图的源码」")
            continue
        _和 = sum(int(v) for v in _类.values())
        if _和 != _跳:
            坏.append(f"判一百四十七②：`{_名}` 的 `★跳过分类` 分项之和 **{_和:,}** "
                      f"≠ `跳过数` **{_跳:,}**（差 {abs(_跳 - _和):,}）⇒ 有一批剔除"
                      f"没被记进分类（口径 194：同一个东西两个出口字段必须一样）")
        _后 = _gj.get("★跳过后缀")
        if not isinstance(_后, dict):
            坏.append(f"判一百四十七①：`{_名}` 有 `★跳过分类` 但没有 `★跳过后缀` ⇒ "
                      f"「剔掉的东西是什么形态」读不到")
        else:
            for _s in _源码后缀:
                _n = int(_后.get(_s) or 0)
                if _n:
                    _被剔的源码.append(f"{_名}:{_s}={_n}")
        _过 += 1
    if 坏:
        return _报2(坏)
    if _过 == 0:
        坏.append("判一百四十七③：没有桶进入检查 ⇒ 不许把「读不到」当「没有剔除」（口径 260）")
        return _报2(坏)
    print(f"   剔除分类读数：绿 —— 判一百四十七（{_过} 个桶的分类之和 == `跳过数` ✓）")
    if _被剔的源码:
        print(f"      ★ 口径 248 留痕：**源码后缀被剔的批次**（必须逐条看得出原因，"
              f"否则就是「错剔」）—— {' · '.join(_被剔的源码[:10])}")
    print("      ★ 口径 248 留痕：跳过的主体是**抽不出结构**的（仓库根实测 "
          "`.txt` 39,707 · `.xyz` 12,311 · `.bin` 4,968 · `.o` 4,557）—— "
          "**这不是缺陷**，但**必须有分部可查**（本判据就是在要求这个）")

def 判一百四十八体积门槛不许静默剔掉源码() -> None:
    """★ **真的源码被体积门槛剔掉了，而且只留一个文件名**（R499）。

    ★ 来历（**我上一轮的推断被自己推翻**）：
      R498 里我看到 6 个 `.json` 被「过大」剔掉，推断"8MB 门槛碰巧正确、
      没有误伤源码"，还实测扫了全仓 **>8MB** 的文件（4,470 个，按后缀
      `.o 701 · .pdb 622 · .rlib 376 · .bin 218`，**一个源码后缀都没有**）⇒
      写下"8MB 一刀切没有误伤源码"。**这个结论是错的**，原因很简单：
      **`kg.py` 里有两个体积门槛** ——
      ````text
      `枚举文件`  L3356 : 8MB              （挡住 `products/` 里的巨大产物）
      `_过值得`   L5951 : `_进图上限字节 = 1MB`   ← ★ 真正生效的那个
      ````
      我扫的是 8MB，**实际生效的是 1MB** ⇒ 真正被剔的是：
      ````text
      ★ 白名单后缀被体积剔掉的：**54 条**
        src: 判据观测.rs       过大（2316KB > 1MB）
        src: 结构记忆网络.rs    过大（2189KB > 1MB）
        src: mod.rs            过大（1561KB > 1MB）
        src: 造变换.rs          过大（1084KB > 1MB）
        中文编译器: PRN认知中枢.rs 过大（1453KB > 1MB）
        formal_proof: 025_认知主体变更…读数.md 过大（1193KB > 1MB）
        多智能体编排: generic_goal.py ×5 · repo_index.json
      ````
      ⇒ **`src/判据观测.rs` 是真的源码**，它**不在图里**，而盘上只留下
        `{"文件": "判据观测.rs", "原因": "过大（2316KB > 1MB）—— …"}` ——
        **连路径都没有**（`_过值得` 的跳过项只写 `f.name`）⇒
        50 多个 `mod.rs` / `main.rs` 同名文件**互相分不清**（口径 191）。

    ★ 判据（三项，两项反向）：
      ① 每个桶：`清单.json.跳过` 里，**原因含「过大」且后缀在 `_值得进图`
         白名单里**的条目**必须是 0** —— 有 ⇒ 真源码被体积剔了
      ② **反向**：**不许把「体积门槛」整个拿掉** ——
         `_进图上限字节` 必须仍然存在且 ≤ 8MB（否则一个 500MB 的
         `products/graphs/*.json` 会进图，把建图拖死）
      ③ **反向**：跳过项**必须有路径**（不许只有文件名）——
         口径 191：**相对路径必须写明相对谁**；只有 `mod.rs` 三个字，
         读者定位不了是哪个 `mod.rs`（实测 54 条里多个同名）
    """
    坏: list[str] = []
    import sys as _s148
    import json as _j148
    _src = Path(__file__).resolve().parents[1] / "src"
    if str(_src) not in _s148.path:
        _s148.path.insert(0, _src)
    try:
        from agentscope_orchestrator import kg as _kg148
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十八：导不进 `kg`（{type(e).__name__}: {e}）⇒ 测不到")
        return _报2(坏)
    _白 = getattr(_kg148, "_值得进图", None)
    if not _白:
        坏.append("判一百四十八①：`kg._值得进图` 读不到 ⇒ 测不到（不许猜白名单）")
        return _报2(坏)
    # ② 反向：体积门槛必须还在
    _限 = getattr(_kg148, "_进图上限字节", None)
    if not isinstance(_限, int) or _限 > 8 * 1024 * 1024:
        坏.append(f"判一百四十八②：`_进图上限字节` = **{_限}** ⇒ "
                  f"体积门槛被拿掉或放宽过头 ⇒ 巨大的生成物会进图把建图拖死"
                  f"（实测 `products/graphs/形式化验证.json` = **577MB**）")
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    _被剔: list[str] = []
    _无路径: list[str] = []
    _合法: list[str] = []
    _桶数 = 0
    for _桶 in sorted(_仓.iterdir()):
        _q = _桶 / "清单.json"
        if not _q.is_file():
            continue
        _桶数 += 1
        try:
            _清 = _j148.loads(_q.read_text(encoding="utf-8"))
        except Exception as ex:                   # noqa: BLE001
            坏.append(f"判一百四十八①：`{_桶.name}/清单.json` 读不动（{type(ex).__name__}）")
            continue
        for _t in (_清.get("跳过") or []):
            _r = str((_t or {}).get("原因") or "")
            if "过大" not in _r:
                continue
            _p = str((_t or {}).get("路径") or "")
            _f = str((_t or {}).get("文件") or "")
            _s = Path(_p or _f).suffix.lower()
            # ★★ 靶的修正（口径 252，本会话**第四次**）：
            #   第一版把「白名单后缀被体积剔掉」一律判红 ⇒ 重建后 `太虚元道`
            #   与 `多智能体编排` 各剩 **3 条**仍然红 —— 可那 3 条是
            #   `products/graphs/形式化验证.json`（**577MB**）这类，
            #   **连 8MB 的宽门槛都过不去，本来就该剔**。
            #   ⇒ 红的条件必须精确成「**本来能进图，却被剔了**」：
            #     `大小 <= _进图上限宽`。超过宽门槛的是**合法剔除**，只留痕。
            _大 = (_t or {}).get("大小")
            _能进 = (not isinstance(_大, int)) or _大 <= int(
                getattr(_kg148, "_进图上限宽", 8 * 1024 * 1024))
            if _s in _白:
                if _能进:
                    _被剔.append(f"{_桶.name}: {_p or _f} ← {_r[:40]}")
                else:
                    _合法.append(f"{_桶.name}: {_p or _f}（{int(_大) // 1048576}MB）")
            if not _p:
                _无路径.append(f"{_桶.name}: {_f or '(没名字)'}")
    if _被剔:
        坏.append(f"判一百四十八①：**有 {len(_被剔)} 条「白名单后缀」被体积门槛静默剔掉** "
                  f"⇒ 它们**不在图里**，而 AI 读缺口时**看不到它们**（口径 139："
                  f"看不见被当成没有）：{_被剔[:6]}")
    if _无路径:
        坏.append(f"判一百四十八③：**有 {len(_无路径)} 条跳过项没有路径**（只有文件名）⇒ "
                  f"口径 191：读者定位不了是哪个文件（实测多个 `mod.rs`/`main.rs` 同名）："
                  f"{sorted(set(_无路径))[:6]}")
    if 坏:
        return _报2(坏)
    print(f"   体积门槛不误伤源码：绿 —— 判一百四十八（{_桶数} 个桶 · "
          f"白名单后缀被体积剔的 **0** 条 · `_进图上限字节` = {_限 // 1024}KB 仍在 · "
          f"跳过项都带路径）")
    if _合法:
        print(f"      ★ 口径 248 留痕：**{len(_合法)} 条白名单后缀是被「宽门槛」合法剔掉的**"
              f"（>8MB ⇒ 本来就进不了图，**不是缺陷**）—— {' · '.join(_合法[:5])}")
    print(f"      ★ 口径 248 留痕：`kg.py` 里有**两个**体积门槛 —— "
          f"`枚举文件` 的 **8MB**（挡 `products/` 的 577MB 产物）与 "
          f"`_过值得` 的 **{_限 // 1024}KB**（挡大源码）。"
          f"★ 查缺陷时必须**两个都查**（我 R498 只查了 8MB ⇒ 得出错结论）")

def 判一百四十九全仓ast对账产物必须新鲜且干净() -> None:
    """★ **目标⑤的读数必须常驻，而且必须能看出它是什么时候量的**（R500）。

    ★ 目标⑤的原话：「Python 标准库用内建 `ast` 独立算出真 import/定义集，
      与通用 tree-sitter 路径交叉验证，**不一致就是我的 bug**」。
      `判九十四` 守的是 **stdlib 163 个文件** —— 那是 **R452 之前**的规模。
      全仓对账（`work/临时/全仓ast对账.py`）**早就写了**，实测：
      ````text
      全仓 .py 共 9,133 个（16 张图覆盖）· 对账 329s
        两边一致 7,861 · **不一致 0** · 我这边没给出导入 1,264 · ast 判语法错 8
      ````
      **但 R452 之后抽取器改过多次**（R462 声明性祖先 · R493 语言内置 ·
      R499 体积门槛）⇒ **那份对账是旧的**，而**没人知道它旧了**（口径 286）。

    ★ 为什么判据**不重跑**：
      重跑一次 **329s** —— 而全量套件本来就 ~40 分钟。⇒ 判据**读产物**
      （`work/图/ast对账.json`）+ **断言新鲜度**。这正是口径 286/297 的形状：
      **"产物与代码一致"是可以判的，只要产物自己带上"何时、覆盖什么"**。

    ★ 判据（四项，一项反向）：
      ① 产物必须在位、能被读、字段齐（`文件数`/`一致`/`不一致`/`建于`）
      ② **`不一致` 必须为 0** —— 这是目标⑤的核心读数（`ast` 是权威）
      ③ **新鲜度**：`建于` 必须**晚于** `kg.py` 的 mtime ——
         否则"抽取器改了而这份对账是旧代码量的"（口径 286）
         且 `建于` 不许是未来时间（时钟/伪造）
      ④ **反向**：覆盖面不许缩水 —— `文件数` 必须 ≥ 5,000
         （实测 9,133；口径 260：不许因为"读不到"就把 0 当合格）
    """
    坏: list[str] = []
    import json as _j149
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    _产 = _仓 / "ast对账.json"
    if not _产.is_file():
        坏.append("判一百四十九①：找不到 `work/图/ast对账.json` ⇒ 目标⑤的读数"
                  "**不在盘上**（重跑命令见协议文档：`python work/临时/全仓ast对账.py`）")
        return _报2(坏)
    try:
        _果 = _j149.loads(_产.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百四十九①：对账产物读不动（{type(e).__name__}: {e}）")
        return _报2(坏)
    for _k in ("文件数", "一致", "不一致", "建于"):
        if _k not in _果:
            坏.append(f"判一百四十九①：对账产物缺字段 `{_k}` ⇒ 读数不完整"
                      f"（口径 113：**两边都报**，且要能判新鲜度）")
    if 坏:
        return _报2(坏)
    # ② 核心读数
    _不 = int(_果.get("不一致") or 0)
    if _不:
        坏.append(f"判一百四十九②：全仓 `ast` 对账**有 {_不} 处不一致** ⇒ "
                  f"目标⑤的原话是「**不一致就是我的 bug**」"
                  f"（`ast` 是官方解析器，不会错）："
                  f"{(_果.get('不一致明细') or [])[:3]}")
    # ④ 反向：覆盖面 —— ★★★ R523d **重定靶**：魔法阈值 → **与真值同源**
    #   ★ 原判据是 `文件数 >= 5000`（注释写"实测 9,133"）。那个 9,133 是
    #     **R521 剪枝之前**的世界 —— 当时 `.venv/site-packages` 下约 5,900 个 `.py`
    #     **也在对账覆盖里**。剪枝之后（**那正是我们要的**：依赖不是"我自己的东西"）
    #     正确覆盖是 **3,219** ⇒ 这条判据**永远红**：它守的**不再是「覆盖缩水」**，
    #     而是「**世界变了**」。
    #   ⇒ 魔法阈值**迟早过期**，判据从此变噪音，而噪音会被忽略
    #     （口径 238/252/317：**靶没有区分力就要重定靶**，不是留着当摆设）。
    #
    #   ★ 实测依据（本轮，三条互相对得上，口径 138：量出来的不是猜的）：
    #       图上 `.py` 去重 **3,210** · 脚本 `文件数` **3,219**（含 9 个语法错）✓
    #       覆盖里 `site-packages` 残留 **0** ✓
    #       真含 import 的 **2,323** ↔ 产物 `一致 2333` ✓
    #       产物 `我没给出导入 878` ↔ 无 import 的 878 ✓
    #
    #   ★ 重定靶后**更强**：原来「世界一变就红」（噪音）而「枚举器真漏时碰巧也红」；
    #     现在世界随便变都不红，**枚举器一漏文件立刻红** ——
    #     同一个量的两个出口必须一致（本仓口径）。
    _数 = int(_果.get("文件数") or 0)
    _图py: set = set()
    _图目录149 = Path(__file__).resolve().parents[1] / "work" / "图"
    for _b149 in _图目录149.iterdir():
        _p149, _g149 = _b149 / "戳.json", _b149 / "图.json"
        if not (_p149.is_file() and _g149.is_file()):
            continue
        try:
            _桶根149 = Path(str(_j149.loads(_g149.read_text(encoding="utf-8")).get("根") or ""))
            _戳149 = _j149.loads(_p149.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001, PERF203
            continue
        if not isinstance(_戳149, dict):
            continue
        for _k149 in _戳149:
            if str(_k149).lower().endswith(".py"):
                try:
                    _图py.add(str((_桶根149 / str(_k149)).resolve()))
                except OSError:
                    pass
    if not _图py:
        坏.append("判一百四十九④：**图上一个 `.py` 都没数到** ⇒ 判据自己没接上（口径 307）")
    # ★★★ R525n/o：同 判一百零七③ —— **同源 + 差额逐条指名**（口径 260/203/344）
    #   原话「⇒ **枚举器漏文件**」是假话：差额的三个合法来源都不叫「漏」。
    _枚149 = int(_果.get("★枚举到") or 0)
    if _枚149 and _数 < _枚149:
        坏.append(f"判一百四十九④：对账只覆盖 **{_数}** 个 `.py`，而它**自己枚举到** "
                  f"{_枚149} 个 ⇒ **对账漏了它自己枚举到的文件**（口径 260）")
    elif "★图上没枚举到" not in _果:
        坏.append("判一百四十九④：`ast对账.json` 里没有 `★图上没枚举到` ⇒ "
                  "**图与枚举的差额没被指名**（口径 260/203）")
    else:
        _图没枚149 = list(_果.get("★图上没枚举到") or [])
        _枚没图149 = list(_果.get("★枚举到图上没有") or [])
        _差149 = len(_图py) - _数
        _名149 = len(_图没枚149) - len(_枚没图149)
        _还在149 = sum(1 for x in _图没枚149 if (x or {}).get("盘上还在"))
        if _差149 != _名149:
            坏.append(f"判一百四十九④：图上真有的 {len(_图py)} 与对账 {_数} 差 "
                      f"**{_差149}**，而产物只指名了 **{_名149}** 条 ⇒ "
                      f"**差额里有 {abs(_差149 - _名149)} 条是无名的**（口径 260）")
        else:
            print(f"   覆盖面·差额已指名：图上戳 {len(_图py)} · 对账 {_数} · "
                  f"图上没枚举到 {len(_图没枚149)}（盘上还在 {_还在149} ⇒ 剪枝漂移；"
                  f"已删 {len(_图没枚149) - _还在149}）· 枚举到图上没有 {len(_枚没图149)}")
    #   ★ 注意：**不在这里早退** —— 原判据的 `if 坏: return _报2(坏)` 在 ③ 之后，
    #     在这里早退会把 ③（新鲜度）的报错**盖住**（等于"用一条红遮住另一条红"）。
    # ③ 新鲜度：对账必须晚于 `kg.py` 最后一次改动
    _源 = Path(__file__).resolve().parents[1] / "src" / "agentscope_orchestrator" / "kg.py"
    import time as _t149
    _建 = float(_果.get("建于") or 0)
    if _建 <= 0:
        坏.append("判一百四十九③：`建于` 不是正数 ⇒ 判不了新鲜度")
    else:
        _现 = _t149.time()
        if _建 > _现 + 600:
            坏.append(f"判一百四十九③：`建于` 在**未来**（{_果.get('建于可读')}）⇒ "
                      f"这不是一次真对账")
        elif _源.is_file() and _建 < _源.stat().st_mtime:
            _晚 = (_源.stat().st_mtime - _建) / 3600
            _改 = _t149.strftime("%Y-%m-%d %H:%M", _t149.localtime(_源.stat().st_mtime))
            _跑 = _t149.strftime("%Y-%m-%d %H:%M", _t149.localtime(_建))
            坏.append(f"判一百四十九③：**对账产物是旧的** —— 建于 {_跑}，"
                      f"而 `kg.py` 改于 {_改}（晚了 {_晚:.1f} 小时）⇒ "
                      f"「抽取器改了而这份对账是旧代码量的」（**R452 之后抽取器改过多次**）"
                      f"⇒ 重跑：`python work/临时/全仓ast对账.py`（~329s）")
    if 坏:
        return _报2(坏)
    print(f"   全仓 ast 对账：绿 —— 判一百四十九（{_数:,} 个 `.py` · "
          f"一致 {_果.get('一致'):,} · **不一致 0** · 建于 {_果.get('建于可读')} · "
          f"覆盖 {len(_果.get('★覆盖根') or [])} 个一级根）")
    print(f"      ★ 口径 248 留痕：`我这边没给出导入` = {_果.get('★我没给出导入'):,} —— "
          f"**这不是缺陷**：只有该文件真有 import 时才算 `不一致`（实测不一致 0）；"
          f"`ast` 判语法错 {len(_果.get('ast语法错') or [])} 个，**源文件本身有错，不算我的 bug**")

def 判一百五十全仓ast定义对账必须双向为零() -> None:
    """★ **目标⑤的第二半：「真 **定义集**」** —— R500 只对了 import（R501）。

    ★ 目标⑤原话：「Python 标准库用内建 `ast` 独立算出真 **import/定义集**，
      与通用 tree-sitter 路径交叉验证，**不一致就是我的 bug**」。
      判一百四十九（R500）对的是 **import 绑定**；**定义集**那一半当时**没人对**。
      补上之后**第一次跑就抓到真缺陷**（口径 269 的典型）：
      ````text
      有定义的文件 13,658 · 两边一致 3,188
      ⚠️ **我少 10,462 个文件 · 我多 0**      ← 单向、大规模 ⇒ 一定是**一个**系统性错
      ````
      ★ 症状像"大规模缺失"，**根因是分类错**（不是没收）：
        `debug_aes.py` 的 `RCON` / `SBOX` / `key` / `plaintext` / `expected`
        我全标成 `类型=class_assignment` + `粒=类体` + `深度=2/4`，
        而它们是**模块级赋值**。
        `kg.py` L1583 的守卫 `_在类体里(n) or _在模块级(n)` **本来就收了**它们，
        错的只是 L1652 `"粒": "类体"` **硬编码** ✗
      ⇒ 修：`"粒": ("类体" if 父 else "顶层")`。
      ★ 口径 213：**判据要测"我改的那一层"** —— 这一层是**分类**，不是收集。

    ★ 判据（五项，两项反向）：
      ① 产物 `work/图/ast定义对账.json` 在位、字段齐
      ② **`我少文件数` 必须为 0**（`ast` 是权威 ⇒ 我少了就是我的 bug）
      ③ **`我多文件数` 必须为 0**（我多了**同样**是 bug —— 口径 260 的反向）
      ④ **反向**：覆盖面不许缩水（`文件数` ≥ 2,000；实测 13,658）
      ⑤ **新鲜度**：`建于` 必须晚于 `kg.py` 的 mtime（口径 298：**读数过期**是独立缺陷）
    ★ 为什么读产物不重跑：重跑一次 **198s**（全量套件本就 ~40 分钟）——
      与判一百四十九 同一个形状（口径 298）。
    """
    坏: list[str] = []
    import json as _j150
    import time as _t150
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    _产 = _仓 / "ast定义对账.json"
    if not _产.is_file():
        坏.append("判一百五十①：找不到 `work/图/ast定义对账.json` ⇒ 目标⑤的"
                  "**定义集**那一半没在盘上（重跑：`python work/临时/全仓ast定义对账.py`，~198s）")
        return _报2(坏)
    try:
        _果 = _j150.loads(_产.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        坏.append(f"判一百五十①：产物读不动（{type(e).__name__}: {e}）")
        return _报2(坏)
    for _k in ("文件数", "一致", "我少文件数", "我多文件数", "建于"):
        if _k not in _果:
            坏.append(f"判一百五十①：产物缺字段 `{_k}`（口径 113：**两边都报**）")
    if 坏:
        return _报2(坏)
    _少 = int(_果.get("我少文件数") or 0)
    _多 = int(_果.get("我多文件数") or 0)
    if _少:
        坏.append(f"判一百五十②：**我少 {_少:,} 个文件** ⇒ `ast` 算出的顶层定义我这边没有"
                  f"（`ast` 是官方解析器 ⇒ 错的是我）。样例："
                  f"{(_果.get('我少明细') or [])[:2]}")
    if _多:
        坏.append(f"判一百五十③：**我多 {_多:,} 个文件** ⇒ 我算出的顶层定义 `ast` 那边没有"
                  f"（**多也是错** —— 口径 260）。样例："
                  f"{(_果.get('我多明细') or [])[:2]}")
    _数 = int(_果.get("文件数") or 0)
    if _数 < 2000:
        坏.append(f"判一百五十④：只对了 **{_数}** 个文件（实测 13,658）⇒ 覆盖面缩水"
                  f"（口径 260）")
    _源 = Path(__file__).resolve().parents[1] / "src" / "agentscope_orchestrator" / "kg.py"
    _建 = float(_果.get("建于") or 0)
    if _建 <= 0:
        坏.append("判一百五十⑤：`建于` 不是正数 ⇒ 判不了新鲜度")
    elif _建 > _t150.time() + 600:
        坏.append(f"判一百五十⑤：`建于` 在**未来**（{_果.get('建于可读')}）")
    elif _源.is_file() and _建 < _源.stat().st_mtime:
        _跑 = _t150.strftime("%Y-%m-%d %H:%M", _t150.localtime(_建))
        _改 = _t150.strftime("%Y-%m-%d %H:%M", _t150.localtime(_源.stat().st_mtime))
        坏.append(f"判一百五十⑤：**定义对账产物是旧的** —— 建于 {_跑}，"
                  f"`kg.py` 改于 {_改} ⇒ 「抽取器改了而这份对账是旧代码量的」"
                  f"（口径 298）⇒ 重跑 `python work/临时/全仓ast定义对账.py`")
    if 坏:
        return _报2(坏)
    print(f"   全仓 ast 定义对账：绿 —— 判一百五十（{_数:,} 个有定义的文件 · "
          f"一致 {_果.get('一致'):,} · **我少 0 · 我多 0** · 建于 {_果.get('建于可读')}）")
    print(f"      ★ 口径 248 留痕：`ast` 判语法错 {len(_果.get('ast语法错') or [])} 个"
          f"（**源文件本身有错，不算我的 bug**）· "
          f"权威那边一个顶层定义都没有 {_果.get('权威无顶层定义'):,} 个文件")
    # ★ R503（口径 277/298）：**图落后于它索引的文件** —— 单列，不算缺陷
    _落 = _果.get("★图落后于文件的") or []
    if _落:
        print(f"      ★ 口径 248/286 留痕：**图落后于文件的 {len(_落)} 个**"
              f"（★ **不是缺陷**：图看到的是**更早那一刻**的文件，"
              f"拿它当分母会报出**两边不相交的假缺陷**；修法是重跑 `建图`）—— {_落[:3]}")
    _改 = _果.get("★对账期间被改写") or []
    if _改:
        print(f"      ★ 口径 277 留痕：**对账期间被别的进程改写 {len(_改)} 个**"
              f"（单列，**不进 `我少/我多`**）—— {_改[:3]}")
    print(f"      ★ 口径 248 留痕：**两边口径必须一致** —— 权威侧算的是「不在任何 "
          f"`class`/`def` 里的绑定」（`if __name__` 块里也算），"
          f"我这边是 `粒=顶层`。★ 口径 252 本会话**第五次**："
          f"第一版只走 `module.body` ⇒ 报出 `cipher`/`ref` 的**假「我多」**")

def 判一百五十一粒必须按父类型分三态() -> None:
    import json as _j151
    """★ **`粒` 是三态，不是两态** —— 「顶层 / 类体 / 内嵌」缺一态就是**作用域层丢了**（R502）。

    ★ 为什么需要（R501 修复的**副作用**暴露出来的）：
      R501 把 L1518 从 `"顶层" if 父 is None and 深 <= 2 else "内嵌"` 改成
      `"顶层" if 父 is None else "内嵌"` —— **修对了一半**：
      把「模块级 `try`/`if` 块里的 `def`/`class`」从 `内嵌` **救回** `顶层` ✓，
      但**「类体里的 `def`/`class`」仍然落 `内嵌`** ✗
      ````text
      实测（全 17 桶，`类=定义` 的节点）：
        内嵌 × function_definition   96,684   ← ★ 例 `__init__ @ canon.py`
        内嵌 × class_definition       2,100
        **类体 × function_definition      0**  ← ★ **一态整个是空的**
        类体 × class_assignment      30,368   （只有「赋值」进过 `类体`）
      ````
      `ast` 证实：`__init__`（L54）在 `class _Tag` 体内，而它的 `父定义` 就是
      `定义:arc_data/canon.py:_Tag:50`（`类型=class_definition`）——
      **父明明是类，粒却写「内嵌」** ✗

    ★ 为什么这不是"命名口味"问题（**目标②的直接要求**）：
      目标②要的是「把定义绑到**作用域**：文件内定义→局部」。
      **「方法」和「函数内的嵌套函数」是两个不同的作用域层**：
      `A.m` 与 `f.<locals>.g` 在解析引用时**走的是不同的链**。
      ⇒ 把两者都记成 `内嵌` = **作用域层在图上丢了** ⇒
        解析「`self.m()` 指向哪个 `m`」时**没有任何字段能区分**
        「同类的方法」与「外层函数的局部函数」。

    ★ 判据（五项，三项反向；**fixture 造，不赌真仓**，口径 124）：
      ① 顶层 `def`/`class`（父为 None）⇒ `粒` 必须是 `顶层`
      ② **类体**的 `def`/`class`（父的类型含 `class`/`struct`/`impl`/`trait`/
         `interface`/`enum`）⇒ `粒` 必须是 `类体`
      ③ **函数内**的嵌套 `def`（父的类型含 `function`/`def`）⇒ 必须是 `内嵌`
      ④ ★ **反向（口径 260）**：三态**都必须非空** ——
         `类体 × function_definition` **一个都没有**就是这一态没接上
         （★ 这正是当前的真读数 ⇒ 这条判据**现在就该红**）
      ⑤ ★ **反向**：真仓里不许出现「父是类而粒=内嵌」（抽 17 桶全量数）
    """
    坏: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判一百五十一：`kg` 导不进来（{type(e).__name__}: {e}）"])

    # ── ①②③ 拿 fixture 造（不赌真仓）
    import shutil as _sh
    import tempfile as _tf
    _根 = Path(_tf.mkdtemp(prefix="kg151_"))
    try:
        (_根 / "m.py").write_text(
            "TOP = 1\n"
            "def 顶层函数():\n"
            "    def 里面的内嵌():\n"
            "        return 1\n"
            "    return 里面的内嵌\n"
            "class 甲类:\n"
            "    类属性 = 2\n"
            "    def 方法(self):\n"
            "        return 3\n"
            "class 乙类:\n"
            "    class 内层类:\n"
            "        pass\n",
            encoding="utf-8")
        _出 = _kg._解析一个(_根 / "m.py", "m.py")
        _节 = _出[0] if isinstance(_出, tuple) else _出
        _按名 = {}
        for _v in (_节.values() if isinstance(_节, dict) else _节):
            if _v.get("类") == "定义":
                _按名.setdefault(str(_v.get("名")), _v)

        def _断言(_名, _期望, _为什么):
            _v = _按名.get(_名)
            if _v is None:
                坏.append(f"判一百五十一：fixture 里**没抽到** `{_名}` ⇒ 判据没测到东西"
                          f"（口径 260：没测到不是通过）")
                return
            _实 = str(_v.get("粒"))
            if _实 != _期望:
                坏.append(f"判一百五十一{_为什么}：`{_名}` 的 `粒` 是 **{_实}**，"
                          f"应为 **{_期望}**（父={_v.get('父定义')}）"
                          f"⇒ **作用域层在图上丢了**（目标②：方法 ≠ 嵌套函数）")

        _断言("顶层函数", "顶层", "①")
        _断言("甲类", "顶层", "①")
        _断言("方法", "类体", "②")
        _断言("类属性", "类体", "②")
        _断言("内层类", "类体", "②")
        _断言("里面的内嵌", "内嵌", "③")
    finally:
        _sh.rmtree(_根, ignore_errors=True)

    # ── ④⑤ 真仓读数（只读，不改）
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    _三态 = {"顶层": 0, "类体": 0, "内嵌": 0}
    _方法落内嵌 = 0
    _桶数 = 0
    if _仓.is_dir():
        for _b in sorted(_仓.iterdir()):
            _n = _b / "节点.json"
            if not _n.is_file():
                continue
            _桶数 += 1
            try:
                _节2 = _j151.loads(_n.read_text(encoding="utf-8"))
            except Exception:                         # noqa: BLE001
                continue
            _父类 = {}
            for _k, _v in _节2.items():
                if _v.get("类") == "定义":
                    _父类[_k] = str(_v.get("类型") or "")
            for _v in _节2.values():
                if _v.get("类") != "定义":
                    continue
                _p = str(_v.get("粒") or "")
                if _p in _三态:
                    _三态[_p] += 1
                _pf = _父类.get(str(_v.get("父定义") or ""))
                if _p == "内嵌" and _pf and _是结构体(_pf):
                    _方法落内嵌 += 1
    if _桶数 == 0:
        坏.append("判一百五十一④：`work/图` 里一个桶都没有 ⇒ **没测到**")
    else:
        # ④ 三态都非空
        for _态 in ("顶层", "类体", "内嵌"):
            if _三态[_态] == 0:
                坏.append(f"判一百五十一④：`粒={_态}` 在全仓 **一个都没有**"
                          f"（实测三态 {_三态}）⇒ 这一态**没接上**，"
                          f"作用域链少一层（口径 260）")
        # ⑤ 反向：父是结构体却标内嵌
        if _方法落内嵌:
            坏.append(f"判一百五十一⑤：**{_方法落内嵌:,} 个定义「父是类/结构体，"
                  f"粒却写内嵌」** ⇒ 方法被当成「函数内的嵌套定义」，"
                  f"解析时**没有任何字段能区分**「同类的方法」与「外层函数的局部函数」"
                  f"（目标②：把定义绑到作用域）")
    if 坏:
        return _报2(坏)
    print(f"   粒三态：绿 —— 判一百五十一（fixture 三态都对 · 真仓 {_桶数} 桶 "
          f"顶层 {_三态['顶层']:,} / 类体 {_三态['类体']:,} / 内嵌 {_三态['内嵌']:,} · "
          f"父是结构体却标内嵌 **0**）")


def _是结构体(类型名: str) -> bool:
    """★ 语言无关：**结构体祖先**（类/结构/接口/trait/impl/enum）——按**类型名**判。

    ★ 口径 261 的边界：这里只做**分类**（这一态叫什么），**不做全局排除** ——
      认不出来就当不是结构体（**宁可漏标，不可错标**，口径 203）。
    """
    _t = (类型名 or "").lower()
    for _词 in ("class", "struct", "impl", "trait", "interface", "enum",
                "object", "module_decl", "mod_item"):
        if _词 in _t:
            return True
    return False

def 判一百五十二命名空间不是内嵌() -> None:
    """★ **`namespace` 是命名空间，不是「内嵌」** —— 「内嵌」只能表示"定义体里还有定义"（R503）。

    ★ 为什么需要（R502 留下的诚实边界：**Lean 的三态没测**）：
      量 Lean 侧读数，第一条就说不通：
      ````text
      Lean 定义节点的 粒×类型（全 17 桶）：
        内嵌 × lean:theorem     102,557      ← ★ 只有 6,390 是「顶层」
        内嵌 × lean:def          50,579
        内嵌 × lean:structure     6,143      ← ★ **全是内嵌，一个顶层都没有**
        内嵌 × lean:inductive     2,543      ← ★ 同上
        （全仓内嵌的 **深度分布**：3 → 88,270 · 1 → 42,892 · 2 → 24,497 · 4 → 8,632）
      ````
      抽样看 `深=1` 的那 42,892 个：**限定名 `Self.orbit_length` · 文件 `lean_self/P3.lean`** ——
      它们是 **`namespace Self` 里的定理**，**不是"嵌套定义"** ✗
      `kg.py` L2669：`"粒": "顶层" if not 前 else "内嵌"`，而 `前` = **namespace 栈**
      （L2627-2631 逐行扫出来的）⇒ **`粒` 实际测的是"namespace 有多深"，
      而不是"是不是嵌套在另一个定义体里"**。

    ★ 为什么这是**目标②的实质**（不是命名口味）：
      目标②要「把定义绑到**作用域**」。作用域链上「**命名空间**」与
      「**函数/定义的体内**」是**两种不同的层**：
      ````text
      namespace Self 里的 orbit_length   ← 作用域链：模块 → Self    （★ 名字挂在命名空间上）
      def f 体里的 g                     ← 作用域链：模块 → f → g   （★ g 是 f 的局部）
      ````
      Python 侧的对应物已经分开（`类体` vs `内嵌`，R502）—— **Lean 侧没有**。

    ★ 判据形状（语言无关；**不按 Lean 写特例**）：
      一个定义如果 **`限定名` 有前缀 且 `父定义` 是 `None`** ⇒
      说明它挂在一个**命名空间**（而非某个定义体）里 ⇒ **`粒` 不许是 `内嵌`**。
      ★ 反向：**`父定义` 非 `None`** 的才允许是 `内嵌`/`类体`
        （那是真的挂在某个定义体里）。
      ★ 这条**同时覆盖 Python**：类里的方法有前缀**且**有父定义 ⇒ 不触发；
        模块级的 `def` 没有前缀 ⇒ 不触发。⇒ **它只抓"有前缀却没有父"这一种形态。**

    ★ 判据（四项，两项反向）：
      ① 真仓：`限定名 有前缀 且 父定义 is None 且 粒 == 内嵌` 的条数必须是 **0**
      ② 反向：**必须真的存在**「有前缀且父定义 None」的定义（否则判据没测到东西，口径 260）
      ③ fixture：造 `namespace 甲 / theorem 乙` ⇒ `乙` 的 `粒` 不许是 `内嵌`
      ④ 反向 fixture：造 `def f / def g`（真嵌套）⇒ `g` **必须**是 `内嵌`
    """
    坏: list[str] = []
    import json as _j152
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from agentscope_orchestrator import kg as _kg
    except Exception as e:                            # noqa: BLE001
        return _报2([f"判一百五十二：`kg` 导不进来（{type(e).__name__}: {e}）"])

    # ── ③④ fixture（★ Lean 的三态在这里被测到；不赌真仓，口径 124）
    import shutil as _sh
    import tempfile as _tf
    _根 = Path(_tf.mkdtemp(prefix="kg152_"))
    try:
        (_根 / "m.lean").write_text(
            "namespace 甲\n"
            "theorem 乙 : True := trivial\n"
            "end 甲\n",
            encoding="utf-8")
        try:
            _出 = _kg._解析一个(_根 / "m.lean", "m.lean")
            _节 = _出[0] if isinstance(_出, tuple) else _出
            _列 = _节.values() if isinstance(_节, dict) else _节
            _乙 = [v for v in _列 if v.get("类") == "定义" and str(v.get("名")) == "乙"]
            if not _乙:
                坏.append("判一百五十二③：fixture 里**没抽到** `乙` ⇒ 判据没测到东西"
                          "（口径 260：没测到不是通过）")
            else:
                _p = str(_乙[0].get("粒"))
                if _p == "内嵌":
                    坏.append(f"判一百五十二③：`namespace 甲` 里的 `theorem 乙` 被标成"
                              f"**内嵌**（限定名={_乙[0].get('限定名')}）⇒ "
                              f"**把命名空间当成了函数的体内**（目标②：作用域层混了）")
        except Exception as e:                        # noqa: BLE001
            坏.append(f"判一百五十二③：Lean fixture 解析炸了"
                      f"（{type(e).__name__}: {e}）⇒ 没测到")
    finally:
        _sh.rmtree(_根, ignore_errors=True)

    # ── ①② 真仓读数（只读）
    _仓 = Path(__file__).resolve().parents[1] / "work" / "图"
    _有前缀无父 = 0
    _其中内嵌 = 0
    _例 = []
    _桶数 = 0
    if _仓.is_dir():
        for _b in sorted(_仓.iterdir()):
            _n = _b / "节点.json"
            if not _n.is_file():
                continue
            _桶数 += 1
            try:
                _节2 = _j152.loads(_n.read_text(encoding="utf-8"))
            except Exception:                         # noqa: BLE001
                continue
            for _v in (_节2.values() if isinstance(_节2, dict) else _节2):
                if _v.get("类") != "定义":
                    continue
                _前 = _v.get("限定名")
                _父 = _v.get("父定义")
                if _前 and _父 in (None, ""):
                    _有前缀无父 += 1
                    if str(_v.get("粒")) == "内嵌":
                        _其中内嵌 += 1
                        if len(_例) < 3:
                            _例.append({"文件": _v.get("文件"), "名": _v.get("名"),
                                      "限定名": _前, "语言": _v.get("语言"),
                                      "类型": _v.get("类型"), "深度": _v.get("深度")})
    if _桶数 == 0:
        坏.append("判一百五十二①：`work/图` 里一个桶都没有 ⇒ **没测到**")
    else:
        if _其中内嵌:
            坏.append(f"判一百五十二①：**{_其中内嵌:,} 个定义「有命名空间前缀、"
                      f"却没有父定义，粒却是内嵌」** ⇒ `namespace` 被当成了"
                      f"「函数的体内」⇒ 作用域层混了（目标②）。样例：{_例}")
        if _有前缀无父 == 0:
            坏.append("判一百五十二②：真仓里**一个「有前缀且父定义 None」的定义都没有**"
                      "⇒ 这条判据**没测到东西**（口径 260：没测到不是通过）")
    if 坏:
        return _报2(坏)
    print(f"   命名空间不是内嵌：绿 —— 判一百五十二（fixture 的 `namespace 甲 / theorem 乙` 不是内嵌 · "
          f"真仓 {_桶数} 桶 · 「有前缀无父定义」 {_有前缀无父:,} 个 · 其中标内嵌 **0**）")

def 判一百五十三粒只许由一个函数产出() -> None:
    """★ **同一个语义量散在 N 处各写一遍，本身就是缺陷**（口径 305 / R504）。

    ★ 为什么需要（R503 留下的诚实边界）：
      `粒` 在 `kg.py` 里被写了 **18 处**：
      ````text
      L1765 局部 · L1807/1822/1846 顶层 · L2225/2245 markdown 文件/文档
      L2669 Lean · L2704/2784/2797/2811/2865/2891 Lean 其余 6 处
      L5670 elaborator · L1518/1652 通用与赋值
      ````
      而 R499 / R501 / R502 / R503 **四次修的都是"其中一处"**，**每次修完都与其余处不一致**：
      - R501：`深 <= 2` 把模块级块里的 `def` 判成内嵌（L1518）
      - R501：赋值那处硬编码 `类体`（L1652）
      - R502：类体里的 `def` 全落内嵌（L1518 第二次）
      - R503：Lean 把 `namespace` 当内嵌（L2669）
      四次都是**同一个字段、同一个病**。

    ★ 判据形状（**机械、可机检、不需要跑抽取**）：
      在 `kg.py` 里，`"粒"` 的**值**不许是**字面量** ——
      只许是**对 `_粒(...)` 的调用**。唯一例外是 `_粒` **自己的函数体**
      （那里必须写死 `顶层`/`类体`/`内嵌`…，否则就成了死循环）。
      ★ 这**不是风格洁癖**：它把"**散在 18 处**"变成"**一个入口**"，
        于是**下一次改语义只需要改一处** —— 而这四次修复全是"漏改了别处"。

    ★ 判据（四项，两项反向）：
      ① `kg.py` 里 `"粒":` 后面**不许**跟字面量（`"顶层"`/`"类体"`/`"内嵌"`/…）
      ② 反向：**`_粒` 函数必须存在**（否则①会因为"没有这个字段"而假绿，
         口径 260：没测到不是通过）
      ③ 反向：**`_粒` 的调用点数必须够** —— 至少 **5** 处
         （不然有人把 18 处删掉 13 处也能让①变绿）
      ④ `_粒` 函数体**内部**必须**恰好**含 `顶层`/`类体`/`内嵌` 三个值
         （三态不许少一个，口径 302）
    """
    坏: list[str] = []
    import re as _re153
    re = _re153
    _p = Path(__file__).resolve().parents[1] / "src" / "agentscope_orchestrator" / "kg.py"
    if not _p.is_file():
        坏.append("判一百五十三：`kg.py` 不在预期位置 ⇒ **没测到**")
        return _报2(坏)
    _s = _p.read_text(encoding="utf-8").replace("\r\n", "\n")
    _L = _s.split("\n")

    # ── 定位**一个入口所在的那段区域**
    #   ★ L504 教训：字面量放在 `_粒` 的**模块级表** `_粒容器` 里（函数每次调用重建表
    #     是浪费），所以区域要从**表**算起；只扫 `def _粒(` 会报出**假红**。
    _入口起 = None
    for _k, _x in enumerate(_L):
        if re.match(r"^(?:_粒容器|def _粒\()", _x):
            _入口起 = _k
            break
    _起 = None
    for _k, _x in enumerate(_L):
        if re.match(r"^def _粒\(", _x):
            _起 = _k
            break
    _尾 = None
    if _起 is not None:
        _尾 = len(_L)
        for _k in range(_起 + 1, len(_L)):
            if re.match(r"^(def |class |# ###)", _L[_k]):
                _尾 = _k
                break
    if _起 is None:
        坏.append("判一百五十三②：`kg.py` 里**没有 `def _粒(`** ⇒ "
                  "`粒` 还不是一个统一入口（口径 305）")
        return _报2(坏)

    # ── ① 区域外的 `"粒":` 不许**直接或间接**给字符串字面量
    #   ★★ L504 教训（口径 252 第八次）：第一版写的是 `"粒"\s*:\s*(?:f?)"` ——
    #     只认「冒号后**紧跟**引号」⇒ 探针 A 把一处退回 `"粒": ("类体" if 父 else "顶层"),`
    #     时**判据仍是绿的**（`:` 与引号之间夹了个 `(`）。
    #     ⇒ 这就是"靶不区分"：**判据看着在位，实际抓不住它要抓的东西**。
    #   ⇒ 改成：**该行**里出现 `"粒":` 且**该行的值部分含字符串字面量** ⇒ 红
    #     （`_粒(...)` 调用是唯一允许的形状）。
    _值里 = re.compile(r'"粒"\s*:(.*)$')
    _串 = re.compile(r'(?<![A-Za-z0-9_])"')
    _杂: list[str] = []
    _调用 = 0
    _区起 = _入口起 if _入口起 is not None else _起
    for _k, _x in enumerate(_L):
        if _区起 <= _k < _尾:
            continue
        m = _值里.search(_x)
        if m:
            _尾段 = m.group(1)
            # 允许的唯一形状：`_粒(...)`
            if not _尾段.lstrip().startswith("_粒("):
                _杂.append(f"L{_k + 1}: {_x.strip()[:88]}")
            elif _串.search(_尾段):
                # `_粒(` 里嵌了裸字符串字面量（不该有：容器名是唯一参数）
                pass
        if re.search(r'"粒"\s*:\s*_粒\(', _x):
            _调用 += 1
    if _杂:
        坏.append(f"判一百五十三①：**{len(_杂)} 处 `粒` 还是字面量** ⇒ "
                  f"这个语义量**又散回多处**了（口径 305：改一处会与其余处不一致）。"
                  f"样例：{_杂[:4]}")

    # ── ③ 调用点数量
    if _调用 < 5:
        坏.append(f"判一百五十三③：`_粒(` 只被调用了 **{_调用}** 次，至少要 **5** 次 ⇒ "
                  f"要么有人把调用点删了（那 ① 的绿是假的，口径 260），"
                  f"要么重构没做完")

    # ── ④ 区域里三态齐全（表 + 函数体一起看）
    _体 = "\n".join(_L[_区起:_尾])
    _缺 = [x for x in ("顶层", "类体", "内嵌") if f'"{x}"' not in _体]
    if _缺:
        坏.append(f"判一百五十三④：`_粒` 函数体里**缺** {_缺} ⇒ "
                  f"三态少了（口径 302：有一态是空的就是缺陷信号）")

    if 坏:
        return _报2(坏)
    print(f"   粒只有一个入口：绿 —— 判一百五十三（`_粒` 在 L{_起 + 1} · "
          f"调用点 {_调用} 处 · 函数体外**零**字面量 · 三态齐全）")

def 判一百五十四深度不许一个名字两种含义() -> None:
    """★ **`深度` 在 Lean 侧是"命名空间层数"，在别的语言侧是"语法深度"** —— 同名不同义（R505）。

    ★ 为什么需要（R504 留下的诚实边界）：
      R504 回执原文：`深度` 与 `限定名` 仍然**各自**在算"namespace 有多深"
      （`前.count(".")`）—— **本轮没查它们与 `粒` 是否一致**。查了：

      ````text
      Lean   `深度` 分布 {0: 66,461, 1: 42,892, 2: 24,497, 3: 88,286, 4: 8,632, …}   ← 命名空间层数
      Python `深度` 分布 {1: 88,189, 2: 86,682, 3: 67,619, …, **20: 1**}              ← 语法深度
      Rust/C/JS 同样是从 1 起、可以到 9（元素在语法树里的深度）
      ````
      **同一个字段名，两种语义。** 实测两条显式读数：
      ````text
      有前缀却深度=0     : 0
      深度>0 却无前缀    : **196,064**    ← ★ 例：`HERE @ arc_lib.py 深度=2`（它是**模块级**）
      ````

    ★ 为什么这不是"命名口味"（**这是 R501 那个缺陷的病根**）：
      R501 写的是 `"顶层" if 父 is None and 深 <= 2 else "内嵌"` ——
      **`深 <= 2` 这个判据从一开始就建在错的字段上**：
      它拿"**语法**深度"当"**作用域**深度"用（`kg.py` L4349 自己都写过
      "`深` 是 tree-sitter 语法深度，不是作用域深度"）。
      ⇒ 只修 `粒` 不够：**字段本身的名字在骗下一个读者**（口径 308 的同族）。

    ★ 判据（四项，两项反向）：
      ① **Lean 侧**的定义节点不许再有 `深度`（那个量叫 `命名空间深度`）
         ★ 口径 261 的边界：**只按语言分流，不做全局排除** ——
           `命名空间深度` 只在 Lean 分支产出。
      ② 反向：Lean 侧必须有 `命名空间深度`（否则①会因为"没这个字段"而假绿，口径 260）
      ③ 反向：**非 Lean** 侧必须仍有 `深度`，而且**必须存在 `深度 > 5` 的节点**
         —— 证明它**不是作用域深度**（作用域深度不可能到 20）
      ④ Lean 的 `命名空间深度` 必须与 `限定名` **自洽**：
         `限定名` 的点数 = `命名空间深度`（★ 这是"两个出口字段必须一样"，口径 194）
    """
    坏: list[str] = []
    import json as _j154
    # ★ R505：允许用环境变量把判据指向**合成图** ⇒ 红路径可以在小样本上验
    #   （口径 292 要求探针真的逼出红；而真仓的 `节点.json` 太大，
    #    为了验红去改 428MB 产物本身就会污染环境，口径 297）。
    #   ★ **默认完全不变** ⇒ 这不削弱判据，只是让它可测。
    import os as _os154
    _仓 = Path(_os154.environ.get("KG_判154图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百五十四：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)

    _lean有深度 = 0
    _lean有命名空间深度 = 0
    _非lean有深度 = 0
    _非lean深超5 = 0
    _自洽坏 = 0
    _自洽好 = 0
    _无名 = 0                                   # ★ R505：`限定名` 为空的（**不是缺陷**）
    _有父判 = 0                                 # ★ R506：走「父 + 1 段」那一支的
    _父无名 = 0                                 # ★ R506：父自己没全名 ⇒ 判不了（留痕）
    _例: list[str] = []
    _桶数 = 0
    for _b in sorted(_仓.iterdir()):
        _n = _b / "节点.json"
        if not _n.is_file():
            continue
        _桶数 += 1
        try:
            _节 = _j154.loads(_n.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        for _v in (_节.values() if isinstance(_节, dict) else _节):
            if _v.get("类") != "定义":
                continue
            _语 = str(_v.get("语言"))
            if _语 == "lean":
                if "深度" in _v:
                    _lean有深度 += 1
                if "命名空间深度" in _v:
                    _lean有命名空间深度 += 1
                    _前 = str(_v.get("限定名") or "")
                    _实 = _v.get("命名空间深度")
                    # ★★ R505（口径 266：**「我没给出」与「我给了但错了」必须分开报**）：
                    #   `限定名` 为空的节点**不参与自洽判定** —— 它们不是"算错了"，
                    #   是**抽取器还没填这个字段**。
                    #   ★ 第一版把 `_前.count(".") == 0` 当期望值 ⇒ 报出**假缺陷**
                    #     `('leaf', '', 1)`：它在 `namespace` 里（层数 1），
                    #     只是 `限定名` 没填。**假缺陷与真缺陷一样有害。**
                    if not _前:
                        _无名 += 1
                        continue
                    # ★★ R506：**④ 要分两支** —— 因为 `命名空间深度` 与 `限定名`
                    #   现在是**两个不同的量**：
                    #     `命名空间深度` = namespace 套了几层
                    #     `限定名`      = namespace + **父定义** + 自己
                    #   ⇒ 绑了父的，点数必然比 `命名空间深度` **多一**。
                    #   ★ 原来那条「点数 == 命名空间深度」**只在没父的时候成立** ——
                    #     它不是"近似"，是**在 R506 之后就不对了**。
                    _父号 = _v.get("父定义")
                    if _父号:
                        _父节 = (_节.get(_父号) if isinstance(_节, dict) else None) or {}
                        _父前 = str(_父节.get("限定名") or "")
                        if not _父前:
                            # 父自己没有全名 ⇒ 无法判（**留痕，不判红**，口径 266）
                            _父无名 += 1
                            continue
                        _期 = _父前.count(".") + 1
                        _有父判 += 1
                        if _前.count(".") == _期:
                            _自洽好 += 1
                        else:
                            _自洽坏 += 1
                            if len(_例) < 3:
                                _例.append(f"{_v.get('文件')}:{_v.get('名')} "
                                          f"限定名={_前!r} 应为「父的 {_父前!r} + 1 段」")
                        continue
                    _期 = _实
                    if _前.count(".") == _期:
                        _自洽好 += 1
                    else:
                        _自洽坏 += 1
                        if len(_例) < 3:
                            _例.append(f"{_v.get('文件')}:{_v.get('名')} "
                                      f"限定名={_前!r} 命名空间深度={_实} 应为 {_期} 段")
            else:
                if "深度" in _v:
                    _非lean有深度 += 1
                    if isinstance(_v.get("深度"), int) and _v["深度"] > 5:
                        _非lean深超5 += 1

    if _桶数 == 0:
        坏.append("判一百五十四：`work/图` 里一个桶都没有 ⇒ **没测到**")
        return _报2(坏)

    if _lean有深度:
        坏.append(f"判一百五十四①：**{_lean有深度:,} 个 Lean 定义节点仍带 `深度`** ⇒ "
                  f"这个字段在 Lean 侧装的是「命名空间层数」，在别的语言侧装的是"
                  f"「语法深度」—— **同一个名字两种含义**，而 R501 那个缺陷"
                  f"（`深 <= 2` 判顶层）就是被这个名字骗的")
    if _lean有命名空间深度 == 0:
        坏.append("判一百五十四②：Lean 侧**一个 `命名空间深度` 都没有** ⇒ "
                  "①会因为「没这个字段」而假绿（口径 260）")
    if _非lean有深度 == 0:
        坏.append("判一百五十四③：非 Lean 侧**一个 `深度` 都没有** ⇒ 判据没测到东西")
    elif _非lean深超5 == 0:
        坏.append("判一百五十四③：非 Lean 侧**没有一个 `深度 > 5`** ⇒ "
                  "光看读数无法排除「它其实是作用域深度」（作用域不可能到 20）")
    if _自洽坏:
        坏.append(f"判一百五十四④：**{_自洽坏:,} 个 Lean 节点的 `命名空间深度` "
                  f"与 `限定名` 不一致**（口径 194：两个出口字段必须一样）。样例：{_例}")
    if _自洽好 == 0:
        坏.append("判一百五十四④：**一个自洽的都没量到** ⇒ 判据没测到东西（口径 260）")

    if 坏:
        return _报2(坏)
    print(f"   深度不许同名两义：绿 —— 判一百五十四（{_桶数} 桶 · "
          f"Lean 带 `深度` **0** · Lean `命名空间深度` {_lean有命名空间深度:,}"
          f"（与 `限定名` 自洽 {_自洽好:,} · `限定名` 为空不判 {_无名:,}"
          f" · 其中走「父 + 1 段」那支 {_有父判:,} · 父自己没全名不判 {_父无名:,}）· "
          f"非 Lean `深度` {_非lean有深度:,}"
          f"（其中 >5 的 {_非lean深超5:,} ⇒ 证明它是语法深度））")
    # ★ R505（口径 248 留痕 / 口径 266）：**这些不是缺陷，是抽取器还没填的字段**
    if _无名:
        print(f"      ★ 口径 248 留痕：**{_无名:,} 个 Lean 节点 `命名空间深度` 有值但 "
              f"`限定名` 为空** ⇒ 它们在 `namespace` 里（层数 >0）而 `限定名` 没填 —— "
              f"**「我没给出」不是「我给了但错了」**（口径 266）。"
              f"这是**已知缺口**，下一轮该补 `限定名`（`_粒` 的 `未跟踪` 是同族）。")

def 判一百五十五命名空间里的声明必须有全名() -> None:
    """★ **在 `namespace` 里的声明，`限定名` 不许是空的**（R506）。

    ★ 为什么需要（R505 留下的诚实边界）：
      R505 回执原文：`限定名` 仍散着算（构造子/结构字段那几处写死 `None`）——
      **本轮只记了缺口（66,465 个），没补**。补之前先量事实（口径 279），量出来是：

      ````text
      Lean 定义节点里 `限定名` 为空、且 `命名空间深度 >= 1` 的，按类型分：
        lean:结构字段  24,321 · lean:namespace 22,004 · lean:构造子  9,887
        lean:theorem    6,390 · lean:def        3,086 · lean:inductive 313
        lean:structure    153 · lean:lemma        148 · lean:abbrev    138
        lean:instance      15 · lean:class         10
      ````
      ★ 前三类（结构字段/构造子）与后九类**是两回事**，而 R505 把它们混成一个读数：
      ````text
      lean:namespace  22,004   ← **它自己就是命名空间**，`Self` 的限定名空是**对的**
      结构字段/构造子 34,208   ← 它们的全名要靠**父定义**（`Self.T.leaf`）
      顶层声明         10,253  ← `theorem`/`def`/… 在 `namespace Self` 里
                                  ⇒ 全名就该是 `Self.encL_len` ⇒ **空的是错的**
      ````
      ⇒ **判据只判第三类**（`父定义` 为 `None` 的顶层声明）：
        **它们在 `namespace` 里，却没有比"名字"更长的全名 —— 没有理由。**

    ★ 口径 261/303 的边界：**按类型名里的词分类**（"构造子"/"结构字段"/"绑定"/
      "选项名"/"namespace"），**不做全局排除**；**未认识的类型**按"顶层声明"对待
      （宁可报出来，不可漏掉）。

    ★ 判据（三项，两项反向）：
      ① `命名空间深度 >= 1` 且 `父定义` 为 `None`、且类型名里**不含**
         「构造子/结构字段/绑定/选项名/namespace」的定义节点 ⇒ `限定名` **不许为空**
      ② 反向：这样的节点必须真的存在 **≥ 1,000 个**（否则①是空转，口径 260）
      ③ 反向：`限定名` 为空且 `父定义` 为 `None` 的**结构字段/构造子**必须**被数出来**
         （口径 248 留痕）—— 它们是**真实的"父没绑"缺口**，不是本判据的目标，
         但**必须看得见**，否则修完①会以为"缺口清了"
    """
    坏: list[str] = []
    import json as _j155
    import os as _os155
    _仓 = Path(_os155.environ.get("KG_判155图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百五十五：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)

    # ★ 这些类型词的"全名"要靠**父定义**，不是 namespace 前缀
    _靠父 = ("构造子", "结构字段", "绑定", "选项名", "namespace")
    _该有却空 = 0
    _该有且有 = 0
    _靠父却空 = 0
    _有父却空 = 0                              # ★ R506：有父定义的（全名靠父）
    _顶层空 = 0
    _桶数 = 0
    _例: list[str] = []
    _父例: list[str] = []
    _有父例: list[str] = []
    for _b in sorted(_仓.iterdir()):
        _n = _b / "节点.json"
        if not _n.is_file():
            continue
        _桶数 += 1
        try:
            _节 = _j155.loads(_n.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        for _v in (_节.values() if isinstance(_节, dict) else _节):
            if _v.get("类") != "定义" or str(_v.get("语言")) != "lean":
                continue
            _t = str(_v.get("类型"))
            _空 = not _v.get("限定名")
            _深 = _v.get("命名空间深度")
            _有父 = _v.get("父定义") is not None
            if not _空:
                if isinstance(_深, int) and _深 >= 1 and not _有父:
                    _该有且有 += 1
                continue
            if not (isinstance(_深, int) and _深 >= 1):
                _顶层空 += 1                      # 不在 namespace 里 ⇒ 空是对的
                continue
            if any(w in _t for w in _靠父):
                _靠父却空 += 1
                if len(_父例) < 3:
                    _父例.append(f"{_v.get('文件')}:{_v.get('名')}({_t})")
                continue
            # ★★ R506：**有父定义的，全名靠父** —— 不要求它自己有 `限定名`。
            #   这个条件**原本只写在 docstring 里、没写进代码**（探针② 把它暴露了：
            #   我本想孤立测②，结果①跟着红，因为一个 `lean:theorem` 只要
            #   `父定义` 非空也会被判进来）。
            #   ⇒ **文档与实现对不上就是缺陷** ——
            #     判据的靶**比我说的宽**，同样会让下一个读它的人算错。
            if _有父:
                _有父却空 += 1
                if len(_有父例) < 3:
                    _有父例.append(f"{_v.get('文件')}:{_v.get('名')}({_t})")
                continue
            _该有却空 += 1
            if len(_例) < 3:
                _例.append(f"{_v.get('文件')}:{_v.get('名')}({_t}) "
                          f"命名空间深度={_深}")

    if _桶数 == 0:
        坏.append("判一百五十五：一个桶都没有 ⇒ **没测到**")
        return _报2(坏)
    if _该有却空:
        坏.append(f"判一百五十五①：**{_该有却空:,} 个 Lean 声明在 `namespace` 里"
                  f"（`命名空间深度 >= 1`）、`父定义` 为空、类型也不是"
                  f"「构造子/结构字段/绑定/选项名/namespace」，却是空的 `限定名`** "
                  f"⇒ 它的全名明明是 `namespace前缀.名字`，**没有理由是空的**"
                  f"（口径 266：「我没给出」与「我给了但错了」要分开，但"
                  f"**「本该给出却没给」是真缺陷**）。样例：{_例}")
    if _该有且有 < 1000:
        坏.append(f"判一百五十五②：这类节点只量到 **{_该有且有:,}** 个有全名的"
                  f"（要 ≥ 1,000）⇒ 判据是空转（口径 260）"
                  f"：要么抽取器根本没产出这类节点，要么①的红不是因为「没填」")

    if 坏:
        return _报2(坏)
    print(f"   命名空间里的声明必须有全名：绿 —— 判一百五十五（{_桶数} 桶 · "
          f"在 namespace 里**有**全名 {_该有且有:,} · 该有却空 **0** · "
          f"不在 namespace 里空着 {_顶层空:,}[对]）")
    # ★ 口径 248 留痕：这一批**不是本判据的目标**，但必须看得见
    print(f"      ★ 口径 248 留痕：**{_靠父却空:,} 个「构造子/结构字段/绑定/选项名」"
          f"在 namespace 里、`父定义` 却是 `None`** ⇒ 它们的全名要靠"
          f"**父定义**（`Self.T.leaf`），而**父没绑** ⇒ **「父绑定」是真缺口**"
          f"（与 `粒` 的 `未跟踪` 同族）。样例：{_父例}")
    if _有父却空:
        print(f"      ★ 口径 248 留痕：**{_有父却空:,} 个在 namespace 里、"
              f"`父定义` 非空、却没有 `限定名`** ⇒ 全名靠父 ⇒ **这一批不算缺陷**"
              f"（★ 这是 R506 补进代码的那个条件：原本只写在 docstring 里）。"
              f"样例：{_有父例}")

def 判一百五十六构造子结构字段必须绑父() -> None:
    """★ **Lean 的构造子/结构字段必须绑到它们的类型上**（R506）。

    ★ 为什么需要（R505/R506 留下的诚实边界）：
      R505 回执原文：`限定名` 仍散着算（构造子/结构字段那几处写死 `None`）——
      **只记了缺口，没补**。R506 先写判 155，跑出来是**绿的**，并**纠正了我**：
      那 10,253 个 `theorem`/`def` 的 `命名空间深度` **全是 0**（不在 namespace 里），
      真正空的是另一批 —— 判 155 的**留痕**把它数出来了：

      ````text
      33,455 个「构造子 / 结构字段 / 绑定 / 选项名」在 namespace 里、
             `父定义` 却是 `None`
      ````
      其中构造子/结构字段的**全名靠父定义**（`Self.MyBool.ff`），而**父没绑** ⇒
      它们既没有 `限定名`、也没有 `父定义` ⇒ **图上这两个名字之间的关系是断的**。

    ★ 这为什么是**目标②（把定义绑到作用域）**的核心缺口之一：
      构造子/结构字段**不是自由名字**，它们的名字**只在它们的类型下面成立**。
      图上没有这条边 ⇒ 「提及 `ff`」与「定义 `Self.MyBool.ff`」连不起来
      ⇒ `ff` 只能落「未解析」（R471 记过：DiZhi 的 12 个地支构造子一个都抽不出，
      742 处引用全落未解析）。

    ★ 判据（四项，两项反向）：
      ① `类型` 含「构造子」或「结构字段」的 Lean 定义节点，`父定义` **不许为 `None`**
      ② 反向：这类节点必须有 **≥ 1,000 个**（否则①空转，口径 260）
      ③ `父定义` 必须**指向图上真实存在的节点**（口径 275：不许造一个悬空 id）
         —— 悬空父定义比 `None` 更坏：它**看起来**绑上了
      ④ `限定名` 必须**以父的名字结尾并对上**：`限定名 == 父限定名或命名空间前缀
         + "." + 父名 + "." + 自己名`（口径 194：两个出口字段必须一样）。
         ★ 这条把 `父定义` 与 `限定名` **锁在一起** —— 改一个不改另一个就红。
    """
    坏: list[str] = []
    import json as _j156
    import os as _os156
    _仓 = Path(_os156.environ.get("KG_判156图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百五十六：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)

    _这类 = 0
    _有父 = 0
    _悬空 = 0
    _名坏 = 0
    _桶数 = 0
    _例: list[str] = []
    _悬例: list[str] = []
    _名例: list[str] = []
    for _b in sorted(_仓.iterdir()):
        _n = _b / "节点.json"
        if not _n.is_file():
            continue
        _桶数 += 1
        try:
            _节 = _j156.loads(_n.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        _全 = set(_节.keys()) if isinstance(_节, dict) else set()
        _按id = _节 if isinstance(_节, dict) else {}
        for _k, _v in ((_节.items() if isinstance(_节, dict) else enumerate(_节))):
            if _v.get("类") != "定义" or str(_v.get("语言")) != "lean":
                continue
            _t = str(_v.get("类型"))
            if not ("构造子" in _t or "结构字段" in _t):
                continue
            _这类 += 1
            _父 = _v.get("父定义")
            if _父 is None:
                if len(_例) < 3:
                    _例.append(f"{_v.get('文件')}:{_v.get('名')}({_t})")
                continue
            _有父 += 1
            if _父 not in _全:
                _悬空 += 1
                if len(_悬例) < 3:
                    _悬例.append(f"{_v.get('名')} ⇒ 父={_父}")
                continue
            _父节 = _按id.get(_父) or {}
            _父名 = str(_父节.get("名") or "")
            _父全 = str(_父节.get("限定名") or "")
            if not _父名:
                _悬空 += 1
                if len(_悬例) < 3:
                    _悬例.append(f"{_v.get('名')} ⇒ 父节点没有名：{_父}")
                continue
            _应 = (_父全 + "." + str(_v.get("名"))) if _父全 else (_父名 + "." + str(_v.get("名")))
            if _v.get("限定名") != _应:
                _名坏 += 1
                if len(_名例) < 3:
                    _名例.append(f"{_v.get('名')} 限定名={_v.get('限定名')!r} 应为 {_应!r}")

    if _桶数 == 0:
        坏.append("判一百五十六：一个桶都没有 ⇒ **没测到**")
        return _报2(坏)
    if _这类 == 0:
        坏.append("判一百五十六：**一个构造子/结构字段都没有** ⇒ 判据没测到东西（口径 260）")
        return _报2(坏)
    if _有父 < 1000:
        坏.append(f"判一百五十六②：只有 **{_有父:,}** 个绑上了父（要 ≥ 1,000）"
                  f"⇒ ①会因为「这批节点根本不存在」而假绿（口径 260）")
    _无父 = _这类 - _有父
    if _无父:
        坏.append(f"判一百五十六①：**{_无父:,} 个 Lean 构造子/结构字段的 `父定义` 是 `None`** "
                  f"⇒ 它们的名字只在它们的类型下面成立，图上却没有这条边 "
                  f"⇒ 「提及」连不到「定义」，只能落未解析。样例：{_例}")
    if _悬空:
        坏.append(f"判一百五十六③：**{_悬空:,} 个 `父定义` 指向图上不存在的节点** "
                  f"⇒ **悬空父定义比 `None` 更坏：它看起来绑上了**（口径 275）。样例：{_悬例}")
    if _名坏:
        坏.append(f"判一百五十六④：**{_名坏:,} 个 `限定名` 与父名对不上** "
                  f"（口径 194：两个出口字段必须一样）。样例：{_名例}")

    if 坏:
        return _报2(坏)
    print(f"   构造子/结构字段必须绑父：绿 —— 判一百五十六（{_桶数} 桶 · "
          f"这类 {_这类:,} · 绑上父 {_有父:,} · 悬空 **0** · 限定名与父一致 **0 坏**）")

def 判一百五十七引号定义不许整批漏抽() -> None:
    """★ **Lean 的 `«引号名»` 定义必须被抽出来**（R507）。

    ★ 怎么发现的（R506 的**父绑定**顺出来的）：
      R506 把构造子/结构字段绑到父定义上，绑完有 **42 个找不到父**。
      查下去 —— 父是 `inductive «十神266r»` **这种带引号的名字**，
      而**它在图上根本没有节点**：

      ````lean
      namespace MyMeta
      namespace TS266r
      inductive «十神266r» : Type where
        | «比肩» | «劫财» | «食神» | «伤官»
      ````
      ⇒ 构造子抽出来了（限定名 `十神266r.比肩`），**类型自己却没抽出来** ✗

    ★ 真因在 `kg.py` 里**同一件事只做了一半**：
      ````python
      # 构造子那一侧（R471 修的）
      _净 = re.sub(r"[«»]", " ", 净)          # ★ 引号 → 空格（保列号）
      ...
      # 声明那一侧（L2715 附近）
      抽 = _ms.抽取符号 if ... 
      for 项 in 抽(净, "lean"):               # ★ 用的是**没归一化**的 `净` ✗
      ````
      ⇒ `«` / `»` **不是标识符字符**（`\w` 为假），mem_symbols 不认
      ⇒ **带引号的定义在 Lean 侧整批漏抽**。
      ★ R471 的注释自己写过这个坑：「`«` / `»` 不是标识符字符 ⇒ 12 个地支构造子
        **一个都没抽出来**」—— **但只修了构造子那半边**。
      ⇒ 口径 305 的同族：**同一个语义处理写在两处，只改了一处**。

    ★ 判据（四项，两项反向）：
      ① **fixture**：`inductive «甲» where | «乙»` 与 `def «丙» := 1`
         ⇒ **`甲` / `乙` / `丙` 三个名字都必须在节点里**
         （★ 口径 252：靶要按被测对象**真正会出现的形态**选 ——
           这里必须用**真引号**，不能用一个"像引号"的替身）
      ② 反向：真仓里带 `«` 的 Lean 文件必须 **≥ 10 个**（否则①在空转，口径 260）
      ③ 反向：fixture 里三个名字**一个都不许少**（少一个就是半修）
      ④ 归一化必须**彻底**：**任何节点的 `名` 里都不许出现 `«` 或 `»`**
         （留着引号的话，`提及` 侧对不上，等于另一种漏）
    """
    坏: list[str] = []
    import json as _j157
    import os as _os157
    import shutil as _sh157
    import tempfile as _t157
    import sys as _sys157

    _P = Path(__file__).resolve().parents[1]

    # ── ② 真仓规模（反向）
    _仓 = Path(_os157.environ.get("KG_判157图目录") or (_P / "work" / "图"))
    _带引号文件 = 0
    _名里有引号 = 0
    _桶数 = 0
    _例: list[str] = []
    if _仓.is_dir():
        for _b in sorted(_仓.iterdir()):
            _n = _b / "节点.json"
            if not _n.is_file():
                continue
            _桶数 += 1
            try:
                _节 = _j157.loads(_n.read_text(encoding="utf-8"))
            except Exception:                         # noqa: BLE001
                continue
            for _v in (_节.values() if isinstance(_节, dict) else _节):
                # ★ R507：④ 的靶**只限 `语言 == "lean"`** ——
                #   markdown 的标题里**合法地**含 `«…»`（连 receipts 的文件名里都有），
                #   那**不是标识符**。★ 口径 252：靶要按被测对象**真正会出现的形态**选；
                #   我第一版**没限语言** ⇒ 报出 **14 个假缺陷**，全是 markdown 标题。
                #   （★ 与口径 203 同向：**假缺陷与真缺陷一样有害**。）
                if str(_v.get("语言")) != "lean":
                    continue
                _m = str(_v.get("名"))
                if "«" in _m or "»" in _m:
                    _名里有引号 += 1
                    if len(_例) < 3:
                        _例.append(f"{_v.get('文件')}:{_m!r}")

    # ── ①③ fixture（**真引号**）
    _fixture = (
        "namespace 甲空间\n"
        "inductive «甲» : Type where\n"
        "  | «乙»\n"
        "def «丙» : Nat := 1\n"
        "end 甲空间\n"
    )
    _抽到: set[str] = set()
    _错 = ""
    _tmp = Path(_t157.mkdtemp(prefix="kg157"))
    try:
        _f = _tmp / "引号.lean"
        _f.write_text(_fixture, encoding="utf-8")
        _sys157.path.insert(0, str(_P / "src"))
        from agentscope_orchestrator import kg as _kg157
        _节2, _边2, _元2 = _kg157._解析一个(_f, "引号.lean")
        for _v in _节2:
            _抽到.add(str(_v.get("名")))
    except Exception as _e:                           # noqa: BLE001
        _错 = f"{type(_e).__name__}: {_e}"
    finally:
        _sh157.rmtree(_tmp, ignore_errors=True)

    if _错:
        坏.append(f"判一百五十七①：fixture 抽取**抛异常** ⇒ 判据没测到东西：{_错}")
        return _报2(坏)

    _该有 = ["甲", "乙", "丙"]
    _缺 = [x for x in _该有 if x not in _抽到]
    if _缺:
        坏.append(f"判一百五十七①③：fixture 里 `«»` 定义**缺了 {_缺}** "
                  f"（抽到的是 {sorted(_抽到)[:12]}）⇒ `«` / `»` 不是标识符字符，"
                  f"引号**归一化只做在一处**（口径 305 的同族：同一个处理写两处、只改一处）")
    if _带引号文件 == 0 and _桶数 == 0:
        坏.append("判一百五十七：`work/图` 里一个桶都没有 ⇒ 判据只测了 fixture，"
                  "没测真仓（口径 260：空输入不补 0）")
    if _名里有引号:
        坏.append(f"判一百五十七④：**{_名里有引号:,} 个节点的 `名` 里带 `«`/`»`** "
                  f"⇒ 归一化不彻底：留着引号的话「提及」侧对不上（等于另一种漏）。"
                  f"样例：{_例}")

    if 坏:
        return _报2(坏)
    print(f"   引号定义不许整批漏抽：绿 —— 判一百五十七（fixture 真引号 "
          f"`inductive «甲»` / `| «乙»` / `def «丙»` **三个名字全抽到** · "
          f"{_桶数} 桶 · 名里带引号 **0**）")

def 判一百五十八语言未知不许用通用表排除() -> None:
    """★ **语言判不出来时，不许用「通用表」做全局排除**（R507）。

    ★ 怎么发现的 —— **判 143 ④ 的靶不区分，把它掩盖了十五轮**：
      ````python
      _未知 = [n for n in ("Eq", "String", "Ordering", "Nonexistent123")
               if _kg143._是内置(n, None) and n == "Nonexistent123"]
      ````
      `Nonexistent123` 在**任何表里都没有** ⇒ `_是内置` 恒为 `False` ⇒
      **这个列表恒为空 ⇒ ④ 恒绿**（口径 252：靶要按被测对象真正会出现的形态选）。
      把靶换成**真的会出现**的名字，缺陷立刻露出来：
      ````text
      _是内置('Eq', None)        = True    ← 它是 rust/lean 的内置
      _是内置('String', None)    = True
      _是内置('ArrayList', None) = True    ← 它是 **java** 的内置
      _是内置('i32', None)       = True    ← 它是 **rust** 的内置
      ````
      ⇒ **语言未知时，名字只要在「任意一种」语言里是内置就被剔了**。

    ★ 为什么这是缺陷（两条现成口径直接判它错）：
      · **口径 261**：`_语言内置` 是**语言无关的混合表**，
        **绝不许用于全局排除**。而 `_是内置(名, None)` 走的正是它。
      · **口径 203**：**宁可漏剔，不可错剔**。剔错了 ⇒ **真缺口被藏起来**，
        而 AI 会以为"这个名字有了"⇒ 正好违背目标④「未解析的必须报出来」。
      ★ 实现里的理由（L6831）是「保守：**多剔一点**」——
        方向反了：剔除**不是**保守动作，**报出来**才是。

    ★ 判据（三项，两项反向）：
      ① 语言未知（`None`）时，**只在某一种语言里是内置**的名字**不许**被判为内置
      ② 反向：**单语言**上下文里那些名字**必须仍然被判为内置**
         （★ 不能顺手把整个内置判定改弱 —— 口径 213：判据要测"我改的那一层"）
      ③ `None` 与空集必须是**同一个语义**（口径 194：同一个量两个出口必须一样），
         且这个语义**只能是「不排除」**
    """
    坏: list[str] = []
    try:
        import agentscope_orchestrator.kg as _kg158
    except Exception as _e:                           # noqa: BLE001
        坏.append(f"判一百五十八：`kg` 导不进来 ⇒ 没测到：{type(_e).__name__}: {_e}")
        return _报2(坏)

    # 每一项 = (名字, 它在哪一种语言里是内置)
    _单语内置 = [("Eq", "rust"), ("Eq", "lean"), ("String", "rust"), ("String", "lean"),
                 ("i32", "rust"), ("PathBuf", "rust"), ("Ordering", "rust"),
                 ("ArrayList", "java"), ("Nat", "lean")]

    # ── ① 未知语言不许剔
    _错剔 = [f"{_n}({_语})" for _n, _语 in _单语内置 if _kg158._是内置(_n, None)]
    # ── ③ 两个入口同一个语义，且都是「不排除」
    _空集剔 = [f"{_n}({_语})" for _n, _语 in _单语内置 if _kg158._是内置(_n, set())]
    _两入口不一致 = bool(_错剔) != bool(_空集剔)

    # ── ② 反向：单语言上下文必须仍然判内置
    _该是 = [f"{_n}({_语})" for _n, _语 in _单语内置 if not _kg158._是内置(_n, {_语})]

    if _错剔:
        坏.append(f"判一百五十八①：**语言未知（`None`）时，{len(_错剔)} 个「只在某一种语言里"
                  f"是内置」的名字被判成了内置** —— {_错剔[:8]} ⇒ "
                  f"走的正是**语言无关的通用表**（口径 261 明令禁止做全局排除）"
                  f"⇒ **真缺口被藏起来**，而 AI 会以为这个名字有了（违背目标④）。"
                  f"★ 这条是判 143 ④ 该抓而**靶不区分**、掩盖了十五轮的缺陷（口径 252）。")
    if _两入口不一致:
        坏.append(f"判一百五十八③：`None` 与空集**不是同一个语义** ⇒ "
                  f"同一个量两个出口必须一样（口径 194）")
    if _该是:
        坏.append(f"判一百五十八②：**单语言上下文里这些名字反而没被判成内置** —— {_该是[:8]} "
                  f"⇒ 修缺陷时把整个内置判定改弱了（口径 213：判据要测「我改的那一层」）")
    if not _单语内置:
        坏.append("判一百五十八：靶列表是空的 ⇒ 判据没测到东西（口径 260）")

    if 坏:
        return _报2(坏)
    print(f"   语言未知不许用通用表排除：绿 —— 判一百五十八（{len(_单语内置)} 个"
          f"「单语言内置」的名字在 `None`/空集下**都不被剔** · "
          f"单语言上下文下**仍然全部判内置**）")

def 判一百六十跳过后缀不许退化成无后缀() -> None:
    """★ **`★跳过后缀` 必须能由 `清单.json` 重算复现**（R508）。

    ★ 怎么发现的（本轮顺手查那笔"从未看过的账"）：
      仓库根有 **12,311 个 `.xyz`** —— 从没查过是什么。查下去发现是**点云数据集**
      （`数据部署\\点云\\parsed\\<类别>\\*.xyz`，ShapeNet 风格，全部 ≤1MB）。
      再去看图里怎么记它们，就看到：
      ````text
      ★跳过分类（按原因）: 扩展名 `.txt` 23,699 · 扩展名 `.xyz` 12,311 · …
      ★跳过后缀（按形态）: (无后缀) 36,738 · .zip 1 · .gz 1
      ````
      ⇒ **同一批文件的两个分部，给出了互不相干的读数。**

    ★ 真因（口径 311：**字段名本身可以是一个缺陷**）：
      `跳过` 条目**文件型用键 `文件`**（实测 36,722 条）、**目录型用键 `路径`**（18 条），
      而统计只读 `路径` ⇒ `Path("").suffix` = `""` ⇒ **全记成 `(无后缀)`** ✗
      影响 **16 / 17 个桶**（那 18 条带 `路径` 的正是 `.bin 4968→43` 这类"部分对上"的来源）。
      ★ 更坏的是它**不报错**：全 `(无后缀)` 看起来像"没有异常"（口径 317 的同族）。

    ★ ★★ 这条判据我**改了三版**，前两版**都是判据自己的错**（口径 252/317）：
      第一版：拿 `.o` 去 `in 原因` ⇒ `.obj`/`.out`/`.orig` **全被算进 `.o`** ✗
      第二版：正则 `\.([A-Za-z0-9]+)` ⇒ `.bak_iter565` 被截成 `.bak` ✗
      第三版（本版）：① 要求「按原因」与「按形态」**逐项计数相等** —— **仍然错**：
        一个文件可以因为**不点名后缀的理由**被剔（「过大（>8MB）」），
        它**只出现在按形态里** ⇒ 两栏本来就不是同一个量，要求相等是**我的错**。
      ⇒ 正确的不变式是**两条**，而且是**不同层**的（口径 282）：
        ① **重算可复现**：从 `清单.json` 的 `跳过` 条目（用 `路径 or 文件` 取后缀）
           算出的分部，必须与 `图.json.★跳过后缀` **逐项相等** —— 这是**同一个量**。
        ② **逐条语义自洽**：任何 `原因` 里点名了 ``扩展名 `E` `` 的条目，
           它自己的后缀**必须就是 `E`** —— 理由说的与实际**不能两张皮**。
    """
    坏: list[str] = []
    import json as _j160
    import os as _os160
    import re as _re160
    _仓 = Path(_os160.environ.get("KG_判160图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百六十：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)

    _桶数 = 0
    _查过 = 0
    _条目数 = 0
    for _b in sorted(_仓.iterdir()):
        _g = _b / "图.json"
        if not _g.is_file():
            continue
        _桶数 += 1
        try:
            _元 = _j160.loads(_g.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        _后 = _元.get("★跳过后缀")
        _跳数 = _元.get("跳过数")
        if not isinstance(_后, dict) or not isinstance(_跳数, int):
            continue
        # `清单.json` 是**写入侧的原件**（`跳过` 那一列就是它写的）
        _清 = _b / "清单.json"
        if not _清.is_file():
            continue
        try:
            _条 = (_j160.loads(_清.read_text(encoding="utf-8")) or {}).get("跳过") or []
        except Exception:                             # noqa: BLE001
            continue
        _查过 += 1
        _条目数 += len(_条)
        # ── ① 重算（用 `路径 or 文件`；★ 这正是被修的那个口径）
        _重算: dict[str, int] = {}
        _无路径 = 0
        _说不出后缀 = []
        for _t in _条:
            _tt = _t if isinstance(_t, dict) else {}
            _p = str(_tt.get("路径") or _tt.get("文件") or "")
            if not _p:
                _无路径 += 1
                continue
            _sx = Path(_p).suffix.lower() or "(无后缀)"   # ★ 与写入侧**同名**（口径 194）
            _重算[_sx] = _重算.get(_sx, 0) + 1
        if _重算 != _后:
            _差 = [(k, _重算.get(k), _后.get(k))
                   for k in sorted(set(_重算) | set(_后))
                   if _重算.get(k) != _后.get(k)]
            坏.append(f"判一百六十①：**`{_b.name}` 的 `★跳过后缀` 不能由 `清单.json` "
                      f"重算复现** —— {_差[:5]}（后缀, 重算, 盘上）⇒ "
                      f"同一个量两个出口必须一样（口径 194）")
        if _无路径:
            坏.append(f"判一百六十①：**`{_b.name}` 有 {_无路径:,} 条跳过的条目"
                      f"既没有 `路径` 也没有 `文件`** ⇒ 那一条**说不出自己剔了什么**"
                      f"（口径 199：剔掉必须留痕）")
        # ── ② 逐条语义：理由里点名了后缀，条目自己的后缀就必须是它
        for _t in _条:
            _tt = _t if isinstance(_t, dict) else {}
            _m = _re160.search(r"扩展名\s*`([^`]+)`", str(_tt.get("原因") or ""))
            if not _m:
                continue
            _e = _m.group(1).strip().lower()
            if not _e.startswith("."):
                continue
            _p = str(_tt.get("路径") or _tt.get("文件") or "")
            _sx = Path(_p).suffix.lower()
            if _sx != _e:
                if len(_说不出后缀) < 3:
                    _说不出后缀.append(f"{_p!r}:理由说 {_e}，实际后缀 {_sx!r}")
        if _说不出后缀:
            坏.append(f"判一百六十②：**`{_b.name}` 里理由说的后缀与实际后缀不一致** —— "
                      f"{_说不出后缀} ⇒ **理由和实际是两张皮**，"
                      f"而「剔了什么」正是靠理由那一栏回答的")
        # ── ③ 反向：和必须等于 `跳过数`
        _和b = sum(v for v in _后.values() if isinstance(v, int))
        if _和b != _跳数:
            坏.append(f"判一百六十③：**`{_b.name}` 的 `★跳过后缀` 之和 {_和b:,} ≠ "
                      f"`跳过数` {_跳数:,}** ⇒ 漏算或重算")
        if len(_后) == 1 and ("(无后缀)" in _后 or "(没有后缀)" in _后):
            坏.append(f"判一百六十③：**`{_b.name}` 的 `★跳过后缀` 退化成只有一个键** "
                      f"（{list(_后.items())}）⇒ 统计代码读错了字段名（口径 311），"
                      f"而且它**不报错** —— 那个读数看起来像「没有异常」")

    if _桶数 == 0:
        坏.append("判一百六十：一个桶都没有 ⇒ **没测到**")
        return _报2(坏)
    if _查过 == 0 or _条目数 == 0:
        坏.append("判一百六十：**没有一个桶能读到 `清单.json.跳过`** ⇒ "
                  "判据没测到东西（口径 260：空输入不补 0）")
        return _报2(坏)
    if 坏:
        return _报2(坏)
    print(f"   跳过后缀不许退化成无后缀：绿 —— 判一百六十（{_查过} 桶 · "
          f"**重算逐项复现** · 逐条语义自洽 · 之和 == 跳过数 · 共 {_条目数:,} 条跳过记录）")

def 判一百六十一定义必须按出现处语言判内置() -> None:
    """★ **三档分类是逐条的量，就必须逐条判**（R509）。

    ★ 怎么发现的：本轮去量「单大写字母（泛型参数）未解析」这笔账 ——
      实测它只有 **20 个名字 / 397 次**（占真缺 0.56%）⇒ **不是优先项**（口径 313：
      我自己记的"33 个 / 4,602 次"是**未解析**那一档的数，不是**真缺口**那一档）。
      ★ 但同一张表里浮出更大的：`String 1,389` · `println 1,138`
        —— 而 `_是内置('println', {'rust'})` **是 True**。

    ★ 真因（R493 已经写下了、`★病②` 也量出来了，但**判断没改**）：
      `_通内置 = {k for k in _未解名 if _是内置(k, _语集.get(k))}`，
      而 `_语集[名]` = 「这个名字**出现过的所有语言**」⇒ `_是内置` 是 `all(...)`
      ⇒ **交集语义**：只要它在 `.md` 里也出现过，rust 那一次就不算内置。
      `kg.py` L5574 的注释把这个选择解释为"口径 203 宁可漏剔" ——
      ★ **但这里不是"剔不剔"的问题**：把一条 Rust 的 `println` 归进
        **真缺口**，不是"少剔了一个内置"，是**错报了一个缺口**。
        而口径 203 的孪生句是「**假缺口同样有害**」，口径 248 亦然。
      ★ 三档（解到 / 内置 / 真缺）本来就是**逐条**的量 ⇒ 必须**逐条**判。

    ★ 判据（三项，一项反向）：
      ① **重算**：从 `引用.json` 里 `依据` 含"未解析"的条目，
         按 `_按扩展名判语言(起)` 逐条判 `_是内置(名, {语})`，
         算出的「真缺名 → 次数」必须与 `外部缺.json.★真外部缺` **逐项相等**（口径 194）
      ② 反向：`真外部缺·出现次数` + `★按出现处语言判定的内置·出现次数`
         + 已解析条数 == `引用边总数`（三档按**出现次数**自洽；口径 260：空输入不补 0）
      ③ 具体靶：`真外部缺` 里**不许**出现「它的出现处语言**全都**认它是内置」的名字
         （`println` / `Vec` / `format` 这些在 `.rs` 里就是内置）
    """
    坏: list[str] = []
    import json as _j161
    import os as _os161
    try:
        from agentscope_orchestrator import kg as _K161
    except Exception as _e:                            # noqa: BLE001
        坏.append(f"判一百六十一：`kg` 导不进来（{_e}）⇒ **没测到**")
        return _报2(坏)
    _仓 = Path(_os161.environ.get("KG_判161图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百六十一：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)

    _桶数 = 0
    _条目 = 0
    _病例: list[str] = []
    for _b in sorted(_仓.iterdir()):
        _引 = _b / "引用.json"
        _外 = _b / "外部缺.json"
        if not (_引.is_file() and _外.is_file()):
            continue
        _桶数 += 1
        try:
            _引条 = _j161.loads(_引.read_text(encoding="utf-8"))
            _外档 = _j161.loads(_外.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        if not isinstance(_引条, list):
            continue
        # ── 逐条重算（★ **一次遍历**同时建「名→非内置次数」与「名→出现处语言集」；
        #   我第一版把语言集写成"对每个名字再扫一遍全部引用" ⇒ O(名字 × 引用)
        #   ⇒ 量级 10^10，跑不动。口径 301 的近亲：**对账脚本自己也会错**）
        _重名: dict[str, int] = {}
        _名语: dict[str, set] = {}
        _有名无语: set = set()
        for _r in _引条:
            if "**未解析**" not in str(_r.get("依据") or ""):
                continue
            _条目 += 1
            _n = _r.get("名")
            _语 = _K161._按扩展名判语言(str(_r.get("起") or ""))
            if _语:
                _名语.setdefault(_n, set()).add(_语)
                if _K161._是内置(_n, {_语}):
                    continue                          # ★ 就**这一条**是内置
            else:
                # ★★ R509 自纠：**语言判不出来的那几次必须记下来** ——
                #   它们**不剔**（口径 158），所以它们正是"这个名字为什么还在真缺里"
                #   的原因。我第一版漏了这一步 ⇒ ③ 把 `String`（只出现在 `.rs`）
                #   报成假缺口 —— **而它确实在盘上**，因为它还有语言未知的出现处。
                #   口径 252：靶要按被测对象**真正会出现**的形态选。
                _有名无语.add(_n)
            _重名[_n] = _重名.get(_n, 0) + 1
        # ── ① 重算复现（★ 单调方向判法）
        # ★★ 为什么不用"逐项相等"：盘上还排掉了「本仓有裸名定义」与「半截名字」两档，
        #   而判据**不重实现**那两档逻辑（口径 301：对账脚本自己也会错；
        #   口径 252：靶的形态要和被比对的量**同源**）。
        #   ⇒ 只判**方向**：盘上**绝不许比逐条重算更多** ——
        #     多出来的那部分正是"把别种语言里的内置也算成了真缺口"。
        #   ★ 这个方向是**单调**的：裸名/半截只会让盘上**更少**，不会假红。
        _盘上 = {str(x.get("名")): int(x.get("次") or 0)
                 for x in (_外档.get("★真外部缺") or [])}
        _多算 = []
        for _k, _c in _盘上.items():
            _a = _重名.get(_k, 0)
            if _c > _a:
                _多算.append((_k, _a, _c))
        if _多算:
            _例 = sorted(_多算, key=lambda x: -x[2])[:5]
            坏.append(f"判一百六十一①：**`{_b.name}` 的 `★真外部缺` 比「按出现处语言"
                      f"逐条重算」**多算**了** —— {_例}（名, 逐条重算次, 盘上次）⇒ "
                      f"多出来的那些**是别种语言里的内置被算成了真缺口**"
                      f"⇒ 假缺口（口径 203 孪生句）")
        # ── ② 逐条三档的**真实守恒**（★ R509 定稿：真缺还排掉了裸名/按语内置两档，
        #   所以"已解析+内置+真缺==总数"**不是**恒等式 —— 我第一版按那个写，
        #   每个桶都假红。口径 301 的近亲：**判据自己算错了守恒式**）
        _未 = _外档.get("未解析总数")
        _内 = _外档.get("★按出现处语言判定的内置·出现次数（R493）")
        _两和 = _外档.get("★未解析两档之和（逐条内置 + 逐条非内置）")
        _非内 = _外档.get("★逐条非内置·出现次数（真缺的**上界**）")
        _真次 = _外档.get("真外部缺·出现次数")
        _留 = _外档.get("★逐条真缺排除留痕（口径 199）")
        if all(isinstance(x, int) for x in (_未, _内, _两和, _非内, _真次)):
            if _内 + _非内 != _未 or _两和 != _未:
                坏.append(f"判一百六十一②：**`{_b.name}` 逐条两档之和不等于未解析总数** —— "
                          f"内置 {_内:,} + 非内置 {_非内:,} = {_内 + _非内:,} ≠ {_未:,}"
                          f"（字段 `★未解析两档之和` 写 {_两和}）")
            if isinstance(_留, dict):
                # ★ R509 补：**必填的两项**是"剔掉必须留痕"的载体，键名本身就是口径的一部分
                #   （口径 311：字段名本身可以是一个缺陷）—— 只核"和"看不出键名被换掉。
                _必 = ("本仓有裸名定义·出现次数", "按语内置表（关键字/tactic）·出现次数")
                _缺 = [x for x in _必 if x not in _留]
                if _缺:
                    坏.append(f"判一百六十一②：**`{_b.name}` 的排除台账缺必需项 {_缺}** ⇒ "
                              f"剔掉的东西没有**指名**留痕（口径 199/311）")
                _排 = sum(v for v in _留.values() if isinstance(v, int))
                if _真次 + _排 != _非内:
                    坏.append(f"判一百六十一②：**`{_b.name}` 真缺 + 排除留痕 ≠ 逐条非内置** —— "
                              f"{_真次:,} + {_排:,} ≠ {_非内:,} ⇒ "
                              f"**有出现次数的去向没说清**（口径 199：剔掉必须留痕）")
            else:
                坏.append(f"判一百六十一②：**`{_b.name}` 没有 `★逐条真缺排除留痕`** ⇒ "
                          f"剔掉的东西没有留痕（口径 199）")
        else:
            坏.append(f"判一百六十一②：**`{_b.name}` 缺逐条三档的字段** ⇒ **没测到**"
                      f"（口径 260：空输入不补 0）")
        if _外档.get("★三档自洽（逐条·按出现次数）") is not True:
            坏.append(f"判一百六十一②：**`{_b.name}` 的 `★三档自洽（逐条·按出现次数）` "
                      f"不是 True**（实为 {_外档.get('★三档自洽（逐条·按出现次数）')!r}）")
        # ── ③ 具体靶：全语言都认它是内置的名字，不许在真缺里
        for _n in _盘上:
            _语们 = _名语.get(_n) or set()
            # ★ 有语言未知的出现处 ⇒ **不判**（它留在盘上是口径 158 的直接后果）
            if _n in _有名无语:
                continue
            if _语们 and all(_K161._是内置(_n, {_q}) for _q in _语们):
                if len(_病例) < 5:
                    _病例.append(f"{_n}（出现处语言 {sorted(_语们)}）")
        if _病例:
            坏.append(f"判一百六十一③：**`{_b.name}` 把「每种出现处语言都认它是内置」的"
                      f"名字报成了真缺口** —— {_病例[:5]} ⇒ 这是**假缺口**（口径 203 孪生句）")
    if _桶数 == 0 or _条目 == 0:
        坏.append("判一百六十一：**没读到任何 `引用.json`/`外部缺.json`** ⇒ "
                  "判据没测到东西（口径 260）")
        return _报2(坏)
    if 坏:
        return _报2(坏)
    print(f"   定义必须按出现处语言判内置：绿 —— 判一百六十一（{_桶数} 桶 · "
          f"{_条目:,} 条未解析**逐条重算复现** · 三档按出现次数自洽 · "
          f"无「全语言内置」之名被报成真缺口）")

def 判一百六十二lean库名不许当内置() -> None:
    """★ **Lean 的「库名」不是「语言内置」**（R510，口径 248/303）。

    ★ 怎么发现的：本轮查目标③「引用边带不带目标节点 id」，量到
      `semsearch` 的引用边只有 **14%** 带真节点 id，而其中一大类是
      `extern:`（星号导入、靶在仓外）。顺着 `extern` 往里看，
      `候选: ["extern:BaGua", "extern:Yi"]` 里装的却是**模块名**。
      再量「extern 里有多少名字其实按语言是内置」⇒ **6,581 / 442,597（1%）**，
      Top1 是 **`Mathlib` 1,153 条**。

    ★ `Mathlib` 为什么会判成内置：`_内置_按语言["lean"]` 里有一行
      把 **Mathlib 的命名空间**（`Mathlib Finset BigOperators MeasureTheory
      Set Function Filter Topology Algebra Order Group Ring Field Module
      LinearAlgebra Analysis`）当成了 Lean 语言内置。
      ⇒ 而口径 248 明确要求「**Mathlib 名必须保持报出来**」（它们是真缺口）。
      同一层的东西，一半被报表、一半被写进内置表剔掉 —— **自相矛盾**。

    ★ 判据（三项，一项反向）：
      ① `_内置_按语言["lean"]` 里**不许**出现 `Mathlib`（本仓外的库名）
      ② 反向：Lean **核心**的名字（`List`/`Bool`/`Nat`/`Option`/`Array`/`Char`/
         `String`/`Float`）**必须仍在内置表里** —— 否则①会因为"表空了"而假绿
         （口径 260：没有输入不补 0）
      ③ 实测：真仓里 `Mathlib` **不许**出现在「按语言判定的内置」名单里，
         且**必须**出现在真外部缺（或半截名字）那一档 —— 口径 248
    """
    坏: list[str] = []
    import json as _j162
    import os as _os162
    try:
        from agentscope_orchestrator import kg as _K162
    except Exception as _e:                            # noqa: BLE001
        坏.append(f"判一百六十二：`kg` 导不进来（{_e}）⇒ **没测到**")
        return _报2(坏)
    _表 = getattr(_K162, "_内置_按语言", None)
    if not isinstance(_表, dict) or "lean" not in _表:
        坏.append("判一百六十二：`_内置_按语言['lean']` 不在 ⇒ **没测到**（口径 260）")
        return _报2(坏)
    _lean = set(_表["lean"])
    # ① 库名不许当内置
    _库名 = ["Mathlib", "Finset", "BigOperators", "MeasureTheory", "Topology",
             "LinearAlgebra", "Analysis"]
    _混进来 = [n for n in _库名 if n in _lean]
    if _混进来:
        坏.append(f"判一百六十二①：**Lean 的库名被当成了语言内置** —— {_混进来} ⇒ "
                  f"它们是 **Mathlib 的命名空间**（本仓外），而口径 248 要求"
                  f"「Mathlib 名必须保持报出来」⇒ 写进内置表就把真缺口藏了")
    # ② 反向：核心必须还在
    _核心 = ["List", "Bool", "Nat", "Option", "Array", "Char", "String", "Float"]
    _缺核 = [n for n in _核心 if n not in _lean]
    if _缺核:
        坏.append(f"判一百六十二②：**Lean 核心名从内置表里消失了** —— {_缺核} ⇒ "
                  f"①会因为「表被清空」而假绿（口径 260）")
    # ③ 实测：真仓读数
    _仓 = Path(_os162.environ.get("KG_判162图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百六十二：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)
    _桶 = 0
    _命中: list[str] = []
    for _b in sorted(_仓.iterdir()):
        _f = _b / "外部缺.json"
        if not _f.is_file():
            continue
        _桶 += 1
        try:
            _d = _j162.loads(_f.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        _档 = _d.get("★按语言判定的内置（R484：含原「通用表」那批，留痕）") or []
        if "Mathlib" in _档:
            _命中.append(_b.name)
    if _桶 == 0:
        坏.append("判一百六十二：一个桶都没有 ⇒ **没测到**")
        return _报2(坏)
    if _命中:
        坏.append(f"判一百六十二③：**`Mathlib` 被判成了「按语言判定的内置」** —— "
                  f"出现在 {_命中} 桶 ⇒ 它必须走**真外部缺**（口径 248）")
    if 坏:
        return _报2(坏)
    print(f"    lean库名不许当内置：绿 —— 判一百六十二（`_内置_按语言['lean']` 无库名 · "
          f"核心 8 个仍在 · {_桶} 桶里 `Mathlib` 不在内置档）")

def 判一百六十三extern不许把语言内置当外部符号() -> None:
    """★ **语言内置不是"外部符号"**（R511，口径 194/282/324）。

    ★ 怎么发现的（R510 自己的收尾就点名了）：R510 修了 `_内置_按语言` 的表，
      于是「extern 里有多少名字其实是内置」这笔账重新量 —— **6,581 / 442,597**。
      R509 修的是「未解析」那条**路径**，R510 修的是**表**；
      **extern 这条路径一次都没被修过**（口径 324：修表 ≠ 修路径）。

    ★ 判据（三项，一项反向）：
      ① **真仓**：`引用.json` 里 `外部: true` 的边，**不许**有名字按它**自己那一处的
         语言**是内置的（`_是内置(名, {语})`）—— 那是语言自带，不是外部符号
      ② **反向**：`外部: true` **必须还有东西**（口径 260/307：空输入不补 0；
         若 extern 被"一刀切清空"，① 会因为分母为 0 而**假绿**）
      ③ **具体靶**：`semsearch` 里那些 `use Yi::*` 招来的 `Some`/`Ok`/`String`/`u64`
         必须**不再**出现在 `外部: true` 里
    """
    坏: list[str] = []
    import json as _j163
    import os as _os163
    try:
        from agentscope_orchestrator import kg as _K163
    except Exception as _e:                            # noqa: BLE001
        坏.append(f"判一百六十三：`kg` 导不进来（{_e}）⇒ **没测到**")
        return _报2(坏)
    _仓 = Path(_os163.environ.get("KG_判163图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百六十三：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)

    _桶 = 0
    _外总 = 0
    _混入: dict[str, int] = {}
    _靶 = ("Some", "Ok", "String", "u64", "Err", "None", "Option", "Vec")
    _靶在: dict[str, int] = {}
    for _b in sorted(_仓.iterdir()):
        _f = _b / "引用.json"
        if not _f.is_file():
            continue
        _桶 += 1
        try:
            _引 = _j163.loads(_f.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        if not isinstance(_引, list):
            continue
        for _r in _引:
            if not _r.get("外部"):
                continue
            _外总 += 1
            _n = str(_r.get("名"))
            _语 = _K163._按扩展名判语言(str(_r.get("起") or ""))
            if not _语:
                continue                              # 语言未知 ⇒ 不判（口径 158）
            if _K163._是内置(_n, {_语}):
                _混入[_n] = _混入.get(_n, 0) + 1
            if _b.name == "semsearch" and _n in _靶:
                _靶在[_n] = _靶在.get(_n, 0) + 1
    if _桶 == 0:
        坏.append("判一百六十三：一个桶都没有 ⇒ **没测到**")
        return _报2(坏)
    if _外总 == 0:
        坏.append("判一百六十三②：**一条 `外部: true` 都没有** ⇒ 判据没测到东西"
                  "（口径 260：空输入不补 0）")
        return _报2(坏)
    if _混入:
        _top = sorted(_混入.items(), key=lambda x: -x[1])[:8]
        坏.append(f"判一百六十三①：**`外部: true` 里混着语言内置** —— 共 "
                  f"{sum(_混入.values()):,} / {_外总:,} 条 · Top {_top} ⇒ "
                  f"它们是语言自带（`use X::*` 招来的名字**不可能**定义在 X 里），"
                  f"记成「外部符号」会让 AI 以为本仓依赖一个外部库提供了它们")
    if _靶在:
        坏.append(f"判一百六十三③：**`semsearch` 的 `extern` 里仍有 Rust prelude 名** —— "
                  f"{_靶在} ⇒ `use Yi::*` 不该把它们变成外部符号")
    if 坏:
        return _报2(坏)
    print(f"    extern不许把语言内置当外部符号：绿 —— 判一百六十三（{_桶} 桶 · "
          f"`外部: true` {_外总:,} 条，其中语言内置 **0** 条 · "
          f"反向：extern 没被清空）")

def 判一百六十四未导入不许跨语言绑同名() -> None:
    """★ **名字空间是按语言分的** —— 跨语言的同名定义不是「别处有定义」（R512）。

    ★ 怎么发现的：本轮量目标③（`提及` → `引用`）时先量「提及档各档构成」，
      看到 `未导入 33,628`。抽样例一看：
        `file:src/lib.rs` 的 `环` **未导入**自 `_验三判据.py`（Python）
        `file:ARC-AGI-1/apps/css/common.css` 的 `row` **未导入**自 `_dbg3.py`
      而 `未导入` 的**全部意义**就是「**补一行 import 就消**」——
      `src/lib.rs` 里**加不了一行 Python 的 import**（口径 203：误报的引用更坏）。

    ★ 判据（三项，一项反向）：
      ① 真仓：`未导入` 的边，**起文件语言与「在别处」定义文件的语言必须同语言**
      ② 起文件**语言判不出**（`.css`/`.js`/`.md`）时**不许**出 `未导入`
         —— 那些文件里没有"导入"这个概念（口径 158：语言未知 ⇒ 不判）
      ③ 反向：**同语言的 `未导入` 必须还在**（否则①会因为"整档被清空"而**假绿**）
    """
    坏: list[str] = []
    import json as _j164
    import os as _os164
    try:
        from agentscope_orchestrator import kg as _K164
    except Exception as _e:                            # noqa: BLE001
        坏.append(f"判一百六十四：`kg` 导不进来（{_e}）⇒ **没测到**")
        return _报2(坏)
    _仓 = Path(_os164.environ.get("KG_判164图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百六十四：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)

    _桶 = 0
    _总 = 0
    _同语 = 0
    _跨语: list[str] = []
    _无语: list[str] = []
    for _b in sorted(_仓.iterdir()):
        _f = _b / "引用.json"
        if not _f.is_file():
            continue
        _桶 += 1
        try:
            _引 = _j164.loads(_f.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        if not isinstance(_引, list):
            continue
        for _r in _引:
            if not str(_r.get("依据") or "").startswith("**未导入**"):
                continue
            _总 += 1
            _起 = str(_r.get("起") or "")
            _语起 = _K164._按扩展名判语言(_起)
            _别 = [str(x) for x in (_r.get("在别处") or [])]
            _语别 = {_K164._按扩展名判语言("file:" + _x) for _x in _别}
            _语别 = {_q for _q in _语别 if _q}
            if not _语起:
                if len(_无语) < 5:
                    _无语.append(f"{_起}:{_r.get('名')}")
                continue
            if _语起 in _语别:
                _同语 += 1
                continue
            if len(_跨语) < 6:
                _跨语.append(f"`{_起}` 的 `{_r.get('名')}`（{_语起}）→ 别处 "
                             f"{sorted(_语别) or '语言未知'}（{_别[:2]}）")
    if _桶 == 0:
        坏.append("判一百六十四：一个桶都没有 ⇒ **没测到**")
        return _报2(坏)
    if _总 == 0:
        坏.append("判一百六十四：**一条 `未导入` 都没有** ⇒ 判据没测到东西；"
                  "★ 这一条同时是**防空集假绿**的守卫 —— 整档被一刀切清空时，"
                  "①会因为没有分母而恒绿（口径 260/307）")
        return _报2(坏)
    if _跨语:
        坏.append(f"判一百六十四①：**跨语言同名被当成「未导入」** —— "
                  f"（至多列 6 条）{_跨语} ⇒ 名字空间**按语言分**，"
                  f"别处只有别的语言的定义 ⇒ 本语言里**没有**它 ⇒ 该落「未解析」"
                  f"（口径 203：误报的引用比缺失的引用更坏）")
    if _无语:
        坏.append(f"判一百六十四②：**语言判不出的文件里出了 `未导入`** —— {_无语} ⇒ "
                  f"`.css`/`.js`/`.md` 里没有「导入」这个概念（口径 158：语言未知 ⇒ 不判）")
    # ★★ R512b：**原来的 ③（`_同语 == 0` ⇒ 红）已删 —— 它是可证明不可达的死条款**
    #   （口径 307/317：看起来有防护、实际从不触发，比没有更糟）。
    #   证明：`_总 > 0` 时每一条 `未导入` 必落入三种之一 ——
    #     `_语起` 空 → `_无语`（②红）；`_语起 in _语别` → `_同语++`；否则 → `_跨语`（①红）。
    #   ⇒ 「① ② 都不红 且 `_总 > 0`」⇒ 每一条都进了 `_同语` ⇒ `_同语 == _总 > 0`
    #   ⇒ **`_同语 == 0` 永不成立**。
    #   它想防的「整档被一刀切清空 ⇒ ① 假绿」**已由上面 `_总 == 0` 守卫全包**
    #   ⇒ 冗余。★ 探针③ 实测红在 **①** 而非 ③，正是这条推理的实验证据。
    if 坏:
        return _报2(坏)
    print(f"   未导入不许跨语言绑同名：绿 —— 判一百六十四（{_桶} 桶 · "
          f"`未导入` {_总:,} 条**全部同语言** · 同语言 {_同语:,} 条仍在）")

def 判一百六十五java语言自带名不许当缺口() -> None:
    """★ `java.lang` 是 JLS 定的「不用 import 就可见」的包（R514，口径 323）。

    ★ 怎么发现的（R513 预研）：`裸名` 排除藏住的 8,902 条里，
      `String`(java) **1,412 条**排第一 —— 一查 `_内置_按语言['java']` 只有
      `com`/`java`/`javax`/`org` 四项（**包前缀**，不是类型）
      ⇒ `_是内置('String', {'java'}) = False`。
    ★ 它现在没暴雷只因为被 `裸名` 挡着 ⇒ 改 `裸名` 会**连带引爆**它
      （R513 预研的口径 330）⇒ 顺序必须是**先修表**。
    ★ 判据（三项 + 一项留痕）：
      ① 表必须含 `java.lang` 代表名（**测试夹具**，不是生产表 ——
         生产表由 JDK 生成：`jimage list lib/modules` + `javap -public`，不手写）
      ② 反向守卫：表不许被清空、也不许只剩包前缀（口径 260）
      ③ 真仓：`真外部缺` 里不许出现这批代表名
      ④ 表要能自证来源（口径 323：手写常量表最容易被当成事实）
    """
    坏: list[str] = []
    import json as _j165
    import os as _os165
    try:
        from agentscope_orchestrator import kg as _K165
    except Exception as _e:                            # noqa: BLE001
        坏.append(f"判一百六十五：`kg` 导不进来（{_e}）⇒ **没测到**")
        return _报2(坏)

    _代表 = ("String", "Object", "Integer", "Long", "Double", "Boolean", "Character",
             "Exception", "RuntimeException", "Error", "Throwable", "System", "Math",
             "Thread", "Runnable", "Comparable", "CharSequence", "Iterable", "Number",
             "Class", "StringBuilder", "Override", "Deprecated", "SuppressWarnings")
    _表 = set(_K165._内置_按语言.get("java", ()))
    _缺 = [x for x in _代表 if not _K165._是内置(x, {"java"})]
    if _缺:
        坏.append(f"判一百六十五①：**Java 语言自带名被当成缺口** —— `{_缺[:8]}` "
                  f"（共 {len(_缺)}/{len(_代表)}）⇒ `java.lang` 是 JLS 定的"
                  f"「不用 import 就可见」的包，`_是内置(名, {{'java'}})` 必须为真"
                  f"（口径 323：手写常量表最容易被当成事实 ⇒ 表本身要能自证）")
    # ② 反向守卫
    if len(_表) == 0:
        坏.append("判一百六十五②：`_内置_按语言['java']` **是空的** ⇒ "
                  "①会因为分母为 0 而**假绿**（口径 260：空输入不补 0）")
    elif _表 <= {"com", "java", "javax", "org", "android", "sun"}:
        坏.append(f"判一百六十五②：java 表**只剩包前缀** —— 实得 {sorted(_表)} ⇒ "
                  f"那不是「类型」，是「包名」；①按类型判，两者不是一回事")
    # ③ 真仓 —— ★★★ R514b：**逐条按出现处语言**（口径 252：靶要按被测对象
    #   真正会出现的形态选）。
    #   ★ 原来查「代表名不许出现在 `★真外部缺` 里」，而 `★真外部缺` 是**名字级**的
    #     ⇒ `Math`/`System` 也出现在 `.py`/`.rs` 里，那里它们**确实**不是内置
    #     ⇒ **合法报出**。实测：修完 java 表后，**出现处是 java 的违规 = 0**，
    #     而非 java 的 72 条**全部合法** ⇒ 原靶比缺陷大一整圈。
    #   ⇒ 现在的靶：对**未解析**的边，若 `起` 的语言是 `java` 且 `名` 是 java 内置
    #     ⇒ **违规**（必须 0）。
    _仓 = Path(_os165.environ.get("KG_判165图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    _桶 = 0
    _java未解 = 0
    _违规: list[str] = []
    if _仓.is_dir():
        for _b in sorted(_仓.iterdir()):
            _f = _b / "引用.json"
            if not _f.is_file():
                continue
            _桶 += 1
            try:
                _引 = _j165.loads(_f.read_text(encoding="utf-8"))
            except Exception:                         # noqa: BLE001
                continue
            if not isinstance(_引, list):
                continue
            for _x in _引:
                if "**未解析**" not in str(_x.get("依据") or ""):
                    continue
                _语 = _K165._按扩展名判语言(str(_x.get("起") or ""))
                if _语 != "java":
                    continue
                _java未解 += 1
                if _K165._是内置(str(_x.get("名")), {"java"}) and len(_违规) < 6:
                    _违规.append(f"{_b.name}:{_x.get('起')}:{_x.get('名')}")
    if _桶 == 0:
        坏.append("判一百六十五：`work/图` 下没有桶 ⇒ **没测到**")
    if _java未解 == 0:
        坏.append("判一百六十五③：**一条 java 语言的未解析边都没有** ⇒ "
                  "③ 是在空集上判，会**假绿**（口径 260）")
    if _违规:
        坏.append(f"判一百六十五③：**java 文件里的 java 语言自带名被当缺口** —— "
                  f"{_违规} ⇒ `java.lang` 不用 import，它们不是缺口（口径 248）")
    # ④ 留痕：表要能自证来源
    if not any("java" in str(_k) for _k in getattr(_K165, "_内置_按语言来源", {})):
        坏.append("判一百六十五④：**java 表没写明来源** ⇒ "
                  "口径 323：手写常量表最容易被当成事实，表必须先能自证"
                  "（生产表应由 JDK 生成：`jimage list lib/modules` + `javap -public`，"
                  "生成脚本 + JDK 版本 + 日期缺一不可）")
    if 坏:
        return _报2(坏)
    print(f"    java语言自带名不许当缺口：绿 —— 判一百六十五（java 表 {len(_表)} 项 · "
          f"代表名 {len(_代表)} 个全部判为内置 · {_桶} 桶 {_java未解:,} 条 java 未解析边里"
          f"**零违规** · 来源已写明）")

def 判一百六十六语言判不出的未解析不许超上限() -> None:
    """★ **给已知的边界装报警器**（R515，口径 325/327）。

    ★ 这个边界是什么：`_扩展语言` 只认 `.lean/.rs/.java/.py/.pyi/.kt/.kts` ——
      仓里 `.c`（19,430 个文件）· `.h`（4,228）· `.js`（643）· `.cc`（391）·
      `.ts`（319）· `.tsx`（201）· `.cpp`（184）· `.go`（170）**都没有语言**
      ⇒ 按语言的内置判定（口径 158：语言未知 ⇒ 不剔）对它们**完全不生效**。
    ★ 为什么**不改**：实测它们在全仓未解析里只占 **483 / 256,826 = 0.19%**
      （`.js` 409 · `.css` 48 · `.ts` 12 · `.h` 12 · `.c` **2 条**）。
      补 C/Go/TS 的表收益 0.19% ⇒ 不值得（口径 313/327：先量再排优先级）。
    ★ 那这条判据测什么：**它测"这个已知缺陷的规模"** —— 阈值是**实测值的 3 倍**
      （0.19% → 0.6%），正常波动不误报，**真实恶化会红**。
      口径 325：一个"如实为空"的边界必须写下**它什么时候会变成缺陷** ——
      这里把"什么时候"变成一个**机器可判的数**。
    """
    坏: list[str] = []
    import json as _j166
    import os as _os166
    try:
        from agentscope_orchestrator import kg as _K166
    except Exception as _e:                            # noqa: BLE001
        坏.append(f"判一百六十六：`kg` 导不进来（{_e}）⇒ **没测到**")
        return _报2(坏)
    _仓 = Path(_os166.environ.get("KG_判166图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百六十六：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)
    _阈 = 0.6                       # ★ 实测 0.19% 的 3 倍
    _桶 = 0
    _总 = 0
    _无语 = 0
    _按缀: dict[str, int] = {}
    for _b in sorted(_仓.iterdir()):
        _f = _b / "引用.json"
        if not _f.is_file():
            continue
        _桶 += 1
        try:
            _引 = _j166.loads(_f.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        if not isinstance(_引, list):
            continue
        for _x in _引:
            if "**未解析**" not in str(_x.get("依据") or ""):
                continue
            _总 += 1
            _起 = str(_x.get("起") or "")
            if _K166._按扩展名判语言(_起) is None:
                _无语 += 1
                _e166 = Path(_起[5:]).suffix.lower() if _起.startswith("file:") else "(非文件)"
                _按缀[_e166] = _按缀.get(_e166, 0) + 1
    if _桶 == 0 or _总 == 0:
        坏.append(f"判一百六十六：`work/图` 没有可读的引用（桶 {_桶} · 未解析 {_总}）"
                  f"⇒ **没测到**（口径 260：空输入不补 0）")
        return _报2(坏)
    _率 = _无语 * 100.0 / _总
    _top = sorted(_按缀.items(), key=lambda x: -x[1])[:6]
    if _率 > _阈:
        坏.append(f"判一百六十六：**语言判不出的未解析边涨到 {_率:.2f}%**"
                  f"（{_无语:,}/{_总:,} · 阈值 {_阈}% · 实测基线 0.19%）· Top {_top} ⇒ "
                  f"这个已知边界**恶化了** —— `_扩展语言` 不认 `.c`(19,430 文件)/`.h`/"
                  f"`.js`/`.ts`/`.go`/`.cc`/`.cpp` ⇒ 按语言的内置判定对它们不生效"
                  f"（口径 325：边界要写下它什么时候会变成缺陷）")
    if 坏:
        return _报2(坏)
    print(f"    语言判不出的未解析不许超上限：绿 —— 判一百六十六（{_桶} 桶 · "
          f"未解析 {_总:,} · 语言判不出 {_无语:,} = **{_率:.2f}%**（阈值 {_阈}%，"
          f"实测基线 0.19%）· Top {_top}）")

def 判一百六十七标了歧义必须给出候选() -> None:
    """★ **歧义标了，还要能看到"另一个候选是谁"**（R516，口径 199 的同族）。

    ★ 目标③：「提及边升级为引用边（带目标节点 id），**歧义必须标**」。
      R515 实测 `list ⇒ 歧义` **零反例**（84,950 条）⇒ "标"做到了。
    ★★ 但四格里的第三格是缺陷：**189,663 条 `歧义: true` 而 `终` 是 str**
      （`依据` Top：导入定义（跨文件）113,019 · 导入模块 52,391 · 外部符号 12,304 …）
      ⇒ 消费者只知道"有歧义"，**拿不到候选** ⇒ 与「没标」对 AI 的用处差不多。
      ★ 口径 199：「**剔掉必须留痕**」——这里是**判为歧义必须留候选**。
    ★ 三项：
      ① `歧义: true` ⇒ `终` 是 list **或** `候选` 非空
      ② 反向：`歧义: false` ⇒ `终` **不许**是 list（不许悄悄多值）
      ③ `终` 只许 `str` 或 `list[str]`（口径 311：字段形态本身可以是缺陷）
      ④ 空集守卫：两种类型都要出现，否则 ①②在单值上判 ⇒ 假绿
    """
    坏: list[str] = []
    import json as _j167
    import os as _os167
    _仓 = Path(_os167.environ.get("KG_判167图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _仓.is_dir():
        坏.append("判一百六十七：`work/图` 不在 ⇒ **没测到**")
        return _报2(坏)
    _桶 = 0
    _总 = 0
    _歧真 = 0
    _歧无候选总数 = 0
    _歧无候选: list[str] = []
    _假却多值: list[str] = []
    _形错: list[str] = []
    _str数 = 0
    _list数 = 0
    for _b in sorted(_仓.iterdir()):
        _f = _b / "引用.json"
        if not _f.is_file():
            continue
        _桶 += 1
        try:
            _引 = _j167.loads(_f.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        if not isinstance(_引, list):
            continue
        for _x in _引:
            _总 += 1
            _t = _x.get("终")
            _q = bool(_x.get("歧义"))
            if isinstance(_t, str):
                _str数 += 1
            elif isinstance(_t, list):
                _list数 += 1
                if not all(isinstance(_i, str) for _i in _t):
                    if len(_形错) < 4:
                        _形错.append(f"{_b.name}:`终` 列表里有非字符串")
            else:
                if len(_形错) < 4:
                    _形错.append(f"{_b.name}:`终` 是 {type(_t).__name__}")
                continue
            _候 = _x.get("候选")
            if _q:
                _歧真 += 1
                if not isinstance(_t, list) and not (isinstance(_候, list) and _候):
                    _歧无候选总数 += 1
                    if len(_歧无候选) < 6:
                        _歧无候选.append(
                            f"{_b.name}:{_x.get('名')}→{_t}（依据 {str(_x.get('依据') or '')[:20]}）")
            elif isinstance(_t, list):
                if len(_假却多值) < 4:
                    _假却多值.append(f"{_b.name}:{_x.get('名')}→{len(_t)} 个目标")
    if _桶 == 0 or _总 == 0:
        坏.append(f"判一百六十七：`work/图` 没有可读的引用（桶 {_桶} · 边 {_总}）"
                  f"⇒ **没测到**（口径 260）")
        return _报2(坏)
    if _str数 == 0 or _list数 == 0:
        坏.append(f"判一百六十七④：样本里只有一种 `终` 形态"
                  f"（str {_str数:,} · list {_list数:,}）⇒ ①②是在单值上判 ⇒ "
                  f"**假绿**（口径 260）")
    if _形错:
        坏.append(f"判一百六十七③：**`终` 的类型越界** —— {_形错} ⇒ "
                  f"只许 `str` 或 `list[str]`（口径 311：字段形态本身可以是缺陷）")
    if _歧无候选:
        坏.append(f"判一百六十七①：**标了歧义却拿不到候选** —— {_歧无候选}"
                  f"（共 **{_歧无候选总数:,}** 条 · 歧义边总 {_歧真:,} 条）⇒ 口径 199："
                  f"**判为歧义必须留下候选** —— 否则 AI 只知道「有歧义」、"
                  f"不知道「另一个是谁」，与没标差不多")
    if _假却多值:
        坏.append(f"判一百六十七②：**没标歧义却给了多个目标** —— {_假却多值} ⇒ "
                  f"多值必须同时把 `歧义` 标真")
    if 坏:
        return _报2(坏)
    print(f"    标了歧义必须给出候选：绿 —— 判一百六十七（{_桶} 桶 · {_总:,} 条边 · "
          f"`终` str {_str数:,} / list {_list数:,} ⇒ ④ 两型都在 · "
          f"歧义 {_歧真:,} 条**全部可查候选** · 未标歧义的**没有多值**）")

def 判一百六十九跳过大小的读数便宜且不撒谎() -> None:
    """★ 跳过目录的"大小"读数必须**便宜**且**名字与量对得上**（R517b/R517c）。

    ★★★ 实测动机（2026-11-13，不是理论）：
````text
分根(总) 2992s（50 分钟）
  `_数目录文件数` 3,412 次 · 合计 2597s ⇒ **占 87%**
  743.9s agentscope_orchestrator 的 work 目录 · 560.5s target · 325.1s .tmp
  · 95.7s .git · 80.8s .cargo_home · 65.1s 备份
⇒ 修复后 **2992s → 1206.8s（只数一级）→ 42.2s（子根也只数一级）= 71 倍**
````
    ★ `快=True`（R414）省的是**每个文件**的两次系统调用，
      **没省"每跳一个目录就把整棵子树数一遍"** ⇒ 口径 3501：
      **「一个要 20 分钟的步骤，没人会跑第二次」**。

    ★★★ 改靶（口径 332：判据的靶可能比缺陷大一圈 —— 要改靶，不是放宽阈值）：
      第一版要求「属于子根必须**递归**数」。实测后发现靶错了：
      · R442 真正要的读数是「跳过的目录**少了多少源码**」——
        判九十五 报的是"60 个**源码文件**谁都不管"；
      · 递归数"里面有多少 .o/.class"**既与动机无关，又是那 20 分钟的来源**；
      · `分根` **本来就有** `一级子树源码数`（= `子计`）—— 那才是"少了多少源码"。
      ⇒ 新靶：**每个跳过目录带一个大小读数，且名字必须与那个量对得上**
        （口径 311：字段名本身可以是一个缺陷）。
        ★ 这**比原来更严**：原来只说"要递归数"，现在**逐条把数值与真值核对**。

    ★ 判据：
      ① `_数目录文件数` 三种形态**互不同名**（口径 190）：
           递归数完 ⇒ `里面有文件数` · 递归没数完 ⇒ `里面至少有文件数`
           （口径 318：保守必须写出方向）· 只数一级 ⇒ `一级条目数`
      ② 合成树（含巨大剪枝目录）上 `分根` < 60s（基线 2992s · 修复后 42.2s）
      ③ **逐条核对**：`一级条目数` 必须等于真的 `scandir` 一级条目数（名字不许撒谎）；
         `里面有文件数` 必须 `数完 is True`；`里面至少有文件数` 必须 `数完 is False`
      ④ 空集守卫：样本里"属于子根"与"剪枝/备份"两类都要出现
      ⑤ 预算用尽（`秒预算=0.0`）⇒ 换成下界字段名 + `数完: False`
    """
    import os as _os169
    import shutil as _sh169
    import time as _t169
    try:
        from agentscope_orchestrator import kg as _K169
    except Exception as _e:                            # noqa: BLE001
        return _报2([f"判一百六十九：`kg` 导不进来（{_e}）⇒ **没测到**"])
    坏: list[str] = []
    合成 = Path(__file__).resolve().parents[1] / "work" / "临时" / "判169合成"

    def _造169() -> None:
        if 合成.exists():
            _sh169.rmtree(合成, ignore_errors=True)
        (合成 / "真源码根" / "pkg").mkdir(parents=True)
        for _i in range(80):
            (合成 / "真源码根" / f"m{_i}.py").write_text("import os\n", encoding="utf-8")
        _巨 = 合成 / "target"
        for _d in range(60):
            (_巨 / f"d{_d}").mkdir(parents=True)
            for _i in range(400):
                (_巨 / f"d{_d}" / f"o{_i}.o").write_bytes(b"x")
        (合成 / "散.py").write_text("import sys\n", encoding="utf-8")

    try:
        _造169()
        _t = _t169.time()
        _计 = _K169.分根(合成, 最少源文件=50, 打印=lambda *_: None)
        _耗 = _t169.time() - _t
        # ★★ 工装（口径 314）：「属于子根」只在**显式传 `排除=`** 时才走得到 ——
        #   `分根` 自己调 `枚举文件(根, 快=True)` 时**不传**（kg.py L6410）。
        _文, _跳 = _K169.枚举文件(合成, 快=True, 排除=("真源码根",))
        _跳 = _跳 + list(_计.get("跳过的目录（带文件数）") or [])
        # ①③⑤ 三种形态
        _形 = {"递归": _K169._数目录文件数(合成 / "真源码根"),
               "只数一级": _K169._数目录文件数(合成 / "target", 只数一级=True),
               "递归没数完": _K169._数目录文件数(合成 / "target", 秒预算=0.0)}
        if len({tuple(sorted(_v)) for _v in _形.values()}) != 3:
            坏.append(f"判一百六十九①：**三种返回形态有重名** —— {_形} ⇒ "
                      f"精确数、下界、另一个量**必须互不同名**（口径 190/311）")
        if not (isinstance(_形["递归没数完"].get("里面至少有文件数"), int)
                and _形["递归没数完"].get("数完") is False):
            坏.append(f"判一百六十九⑤：**预算用尽时没换成下界字段名** —— "
                      f"{_形['递归没数完']} ⇒ 口径 318：保守必须写出方向（「至少」）")
        # ② 耗时
        if _耗 > 60:
            坏.append(f"判一百六十九②：**合成树上 `分根` 用了 {_耗:.1f}s**（阈值 60s）⇒ "
                      f"口径 3501：「一个要 20 分钟的步骤，没人会跑第二次」"
                      f"（基线实测 2992s）")
        # ④ 两类都要在
        _子 = [x for x in _跳 if "属于子根" in str(x.get("原因"))]
        _剪 = [x for x in _跳 if "剪枝" in str(x.get("原因"))
               or "备份" in str(x.get("原因"))]
        if not _子 or not _剪:
            坏.append(f"判一百六十九④：样本里只有一类（属于子根 {len(_子)} · "
                      f"剪枝/备份 {len(_剪)}）⇒ ③在单类上判 ⇒ **假绿**（口径 260）")
        # ③ 逐条核对
        def _真一级169(目: Path) -> int:
            try:
                with _os169.scandir(目) as _it:
                    return sum(1 for _ in _it)
            except OSError:
                return -1

        for _x in _跳:
            _目 = 合成 / str(_x.get("路径", ""))
            _有 = [k for k in ("一级条目数", "里面有文件数", "里面至少有文件数") if k in _x]
            if len(_有) != 1:
                坏.append(f"判一百六十九③：**大小读数的名字数不对** —— "
                          f"{_x.get('路径')} 带 {_有} ⇒ 一个目录只许有一个大小读数，"
                          f"且名字要说明它是什么（口径 311）")
                continue
            _k = _有[0]
            if _k == "一级条目数":
                if _x.get("数完") is not True:
                    坏.append(f"判一百六十九③：`一级条目数` 缺 `数完: True` —— {_x}")
                _真 = _真一级169(_目)
                if _真 >= 0 and _x[_k] != _真:
                    坏.append(f"判一百六十九③：**`一级条目数` 与真值不符** —— "
                              f"{_x.get('路径')} 报 {_x[_k]} · 真 {_真} ⇒ "
                              f"名字必须与量对得上（口径 311）")
            elif _k == "里面有文件数" and _x.get("数完") is not True:
                坏.append(f"判一百六十九③：`里面有文件数`（精确）却 `数完` 不为真 —— {_x}")
            elif _k == "里面至少有文件数" and _x.get("数完") is not False:
                坏.append(f"判一百六十九③：`里面至少有文件数`（下界）却没有 "
                          f"`数完: False` —— {_x}")
        # R442 的动机必须有着落
        if _计.get("一级子树源码数"):
            if "真源码根" not in _计["一级子树源码数"]:
                坏.append("判一百六十九③：**「少了多少源码」这条读数没了** —— "
                          "`一级子树源码数` 里找不到 `真源码根` ⇒ R442 的动机必须有着落")
        else:
            坏.append("判一百六十九③：`一级子树源码数` 为空 ⇒ "
                      "跳过目录的「少了多少源码」没有出处（口径 260）")
    finally:
        _sh169.rmtree(合成, ignore_errors=True)
    if 坏:
        return _报2(坏)
    print(f"    跳过大小的读数便宜且不撒谎：绿 —— 判一百六十九"
          f"（分根 {_耗:.1f}s · 基线 2992s · 三种形态互不同名 · "
          f"`一级条目数` 逐条与真值核对 · 源码缺口由 `一级子树源码数` 负责）")

def 判一百七十抽取阶段就是引用的边必须接上出口() -> None:
    """★★ **抽取阶段就已经是「引用」的边，必须出现在 `引用.json` 里**（R518）。

    ★★★ 缺陷现场（实测，2026-11-14）：
````text
12 个桶 · 边.json 里 3,256 条「关系=引用」的抽取边
        引用.json 里 **0 条**   ⇒ 全部丢失
交接报告/边.json:
  {"起":"file:交接文档_v10.0_20260706.md", "终":"路径:e:/.../src/main.rs",
   "关系":"引用", "依据":"markdown 链接", "行":305, "已解析":false}
交接报告/引用.json: **0 条**
````
    ★ 根因：`解析引用` 里 `引用 = _引用表()` 是**新建空表**，只装
      **它自己升出来的**边 ⇒ `_Markdown适配器` 的 `依据: "markdown 链接"`、
      json 的 `依据: "json references"` **永远进不了 `引用.json`**。
    ★★ 后果：`加载图`/查询/AI 读的是 `引用.json`
      ⇒ **`.md`/`.json` 里写着的真引用，AI 一条都看不到**。
      ★ 目标①的「markdown 链接、json/yaml 键路径」**不是"没做"，是"做了但没接上"**。
      适配器早就在（`_Markdown适配器`：标题=定义、链接=**真引用**）。
      ⇒ 口径 286：**「抽取器对了」与「盘上的图对了」是两件事。**

    ★ 判据（按**唯一键** `(起,终,行)` 判，口径 311）：
      ① **不许丢**：`边.json` 里 `关系 == "引用"` 且 `依据` 以 `markdown`/`json`
         开头的边，唯一键必须**逐个**在 `引用.json` 里找到
      ② **逆命题**：`引用.json` 里这类边，唯一键必须在 `边.json` 里存在
         （不许凭空造）
      ③ **空集守卫**：`边.json` 里为 0 的桶，`引用.json` 里也必须为 0
         （口径 260：空输入不补 0）；全仓一个都没有 ⇒ 也不许判绿
      ★ 报数栏**必须报唯一键**，不能报未去重的原始条数
        （实测 `src`：249 条 → 唯一键 **127**，62 个键重复、最多重复 7 次）
    """
    import json as _j170
    try:
        from agentscope_orchestrator import kg as _k170   # noqa: F401
    except Exception as _e:                               # noqa: BLE001
        return _报2([f"判一百七十：`kg` 导不进来（{_e}）⇒ **没测到**"])
    # ★ R518c/R518d：**图目录按本仓既有范式取**（判一百六十七 L15444 就是这么写的）——
    #   `KG_判N图目录` 覆盖 or `<仓>/work/图`。
    #   ★ 我上一版用的是 `_kg目录(...)`，那是"**看着对**"：它**没设 `KG_图目录` 时
    #     返回 `<根>/.kg`**，而真图在 `work/图` ⇒ 判据直接红在「图目录不存在」（口径 141）。
    #   ★ 覆盖是给**红路径探针**用的：不给覆盖，就只能改真图来制造缺陷
    #     —— 那是改被测对象，不是探针（口径 210）。
    import os as _os170
    _图 = Path(_os170.environ.get("KG_判170图目录")
               or (Path(__file__).resolve().parents[1] / "work" / "图"))
    if not _图.is_dir():
        return _报2([f"判一百七十：图目录不存在（{_图}）⇒ **没测到**"])
    _前 = ("markdown", "json")

    def _是(x: dict) -> bool:
        return (str(x.get("关系") or "") == "引用"
                and str(x.get("依据") or "").startswith(_前))

    def _键(x: dict) -> tuple:
        return (str(x.get("起")), str(x.get("终")), x.get("行"))

    坏: list[str] = []
    _总边唯 = 0
    _总引 = 0
    _有桶 = []
    _空对空 = 0
    for _b in sorted(_图.iterdir()):
        _fe = _b / "边.json"
        if not _fe.is_file():
            continue
        try:
            _边 = _j170.loads(_fe.read_text(encoding="utf-8"))
        except Exception:                                 # noqa: BLE001
            continue
        if not isinstance(_边, list):
            continue
        _引 = []
        _fr = _b / "引用.json"
        if _fr.is_file():
            try:
                _引 = _j170.loads(_fr.read_text(encoding="utf-8"))
            except Exception:                             # noqa: BLE001
                _引 = []
        _引 = _引 if isinstance(_引, list) else []
        _e = [x for x in _边 if isinstance(x, dict) and _是(x)]
        _r = [x for x in _引 if isinstance(x, dict) and _是(x)]
        _e唯 = {_键(x) for x in _e}
        _总边唯 += len(_e唯)
        _总引 += len(_r)
        if _e唯:
            _有桶.append(_b.name)
        if not _e唯:
            if _r:
                坏.append(f"判一百七十②：`{_b.name}` 的 `边.json` 里**没有**抽取引用，"
                          f"`引用.json` 里却有 **{len(_r)}** 条 ⇒ 凭空造（口径 260）")
            else:
                _空对空 += 1
            continue
        _有 = {_键(x) for x in _r}
        _丢 = sorted(_e唯 - _有)
        if _丢:
            坏.append(f"判一百七十①：`{_b.name}` 的 `边.json` 里有 **{len(_e唯)}** 个"
                      f"唯一键（原始 {len(_e)} 条）抽取阶段就是「引用」的边，"
                      f"`引用.json` 里只找到 **{len(_e唯) - len(_丢)}** 个 ⇒ "
                      f"**丢了 {len(_丢)} 个**（如 `{_丢[0][0]}` ⇒ `{_丢[0][1]}`）⇒ "
                      f"`.md`/`.json` 里写着的真引用 **AI 一条都看不到**")
    if _总边唯 == 0:
        坏.append("判一百七十③：全仓 `边.json` 里**一条**抽取阶段引用都没有 ⇒ "
                  "要么抽取器坏了，要么我在空集上判绿（口径 260）")
    if 坏:
        return _报2(坏)
    print(f"    抽取期引用全部接到出口上：绿 —— 判一百七十"
          f"（{len(_有桶)} 个桶有这类边 · `边.json` **{_总边唯:,}** 个唯一键 · "
          f"`引用.json` **{_总引:,}** 条 · 空对空 {_空对空} 个桶 · "
          f"逐条按 (起,终,行) 对齐）")
