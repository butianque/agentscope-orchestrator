"""静态结构门：**全库**扫「被读却从未定义的全局名」。

★ 为什么需要它（本模块存在的唯一理由）：
  这一类缺陷**逃过所有运行期判据门** —— 因为那些门从不走那条路径。
  已实测两例，都是 P0（相关工具一调就 `NameError`）：
    · `taixu_mcp._RUST_ROOT` —— 「任意任务」工具完全不可用；
    · `observation.logger`   —— 漏读账一写就崩。
  ⇒ 只有**静态**判据能抓住它们。

★ 判法（语言无关的「读」，不是启发式）：
  扫字节码取 `LOAD_GLOBAL` 的名字，扣掉模块全局、内建名
  ⇒ 剩下的就是解释器执行时**查不到**的名字。
  它就是运行期查表的**同一个集合**，只是提前到了不执行的时候。

★ 诚实边界：只覆盖**模块级**函数体（含嵌套）的全局读；
  不解析 `getattr`/`globals()[...]` 这类动态取用（那是语言层面的不可判定面），
  也不判"这个全局名是否在使用前被赋值"（那需要数据流分析）。
  本门判的是**存在性**：这个名字在模块里**到底有没有过定义**。
"""
from __future__ import annotations

import builtins
import dis
import importlib
import types
from pathlib import Path

# 解释器自己塞进来的名（不算未定义）
_隐式 = {
    "__name__", "__file__", "__doc__", "__package__", "__loader__",
    "__spec__", "__builtins__", "__debug__", "__path__", "__all__",
    "__annotations__", "__cached__",
}


def undefined_globals(mod: types.ModuleType) -> list:
    """返回 `["函数名:全局名", ...]`（有序、去重）。空 = 全绿。"""
    已知 = set(vars(mod)) | set(dir(builtins)) | _隐式
    缺: set = set()
    for obj in list(vars(mod).values()):
        if not isinstance(obj, types.FunctionType):
            continue
        if getattr(obj, "__module__", "") != mod.__name__:
            continue
        try:
            指令 = dis.get_instructions(obj)
        except Exception:                       # noqa: BLE001 反编译失败不算缺陷
            continue
        for ins in 指令:
            if ins.opname == "LOAD_GLOBAL":
                名 = str(ins.argval)
                if 名 not in 已知:
                    缺.add(f"{obj.__name__}:{名}")
    return sorted(缺)


def _子码(码) -> list:
    """递归取一个 code object 里全部嵌套 code（含自身）。"""
    出 = [码]
    for c in 码.co_consts:
        if isinstance(c, types.CodeType):
            出 += _子码(c)
    return 出


def undefined_globals_in_file(path: Path, modname: str = "") -> list:
    """按文件路径静态扫（**不 import**，故不触发任何副作用）。

    用于"还没被任何门 import 的模块" —— 那些模块的运行期门根本不存在，
    静态门是**唯一**能覆盖它们的手段。

    ★ 判法与 import 版**语义完全等价**（查的是同一个集合），只是不执行模块：
      顶层定义 = 顶层 code 里 `STORE_NAME` / `IMPORT_NAME` 的目标；
      未定义   = 任一 code 里 `LOAD_GLOBAL` 的名字 ∉ 顶层定义 ∪ 内建 ∪ 隐式。
    ★ 语法错**如实报**（不静默通过）—— 静默通过会让门对坏文件失效。
    """
    源 = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else None
    if 源 is None:
        # ★ 读不到必须**如实报**，不是崩 —— 与 grep 门同一条纪律：
        #   扫描器遇到坏输入要产出"因由"，不能把调用方带崩。
        return [f"<读不到>:{path.name}"]
    try:
        顶层 = compile(源, str(path), "exec")
    except SyntaxError as exc:
        return [f"<语法错>:{exc.lineno}:{exc.msg}"]
    顶层定义: set = set()
    for ins in dis.get_instructions(顶层):
        if ins.opname in ("STORE_NAME", "IMPORT_NAME", "IMPORT_FROM"):
            顶层定义.add(str(ins.argval))
    # ★★★★★ 关键（本门第一版在这里**假红**，实测 45 处误报）：
    #   模块级全局也常由函数内 `global X; X = ...` 赋值 ⇒ 那是 `STORE_GLOBAL`，
    #   不是顶层 `STORE_NAME`。只收 STORE_NAME ⇒ 把 `develop.ROOT` / `_WORK` /
    #   `LEDGER`、`taixu_mcp._MEM` / `_seq`、`profile._PROFILE`、`gate_memory.GATE_MEMORY`
    #   等**全部误报**（45 处）。
    #   ⇒ 定义集必须**并上任意 code 里的 `STORE_GLOBAL`**。
    #   ★ 这是"修门不改代码"：误报的门比没有门更坏 —— 它教人忽略红。
    所有码 = _子码(顶层)
    for 码 in 所有码:
        for ins in dis.get_instructions(码):
            if ins.opname == "STORE_GLOBAL":
                顶层定义.add(str(ins.argval))
    已知 = 顶层定义 | set(dir(builtins)) | _隐式
    缺: set = set()
    for 码 in 所有码:
        if 码 is 顶层:
            continue                      # 顶层自己的 LOAD_NAME 由定义序保证，不判
        for ins in dis.get_instructions(码):
            if ins.opname == "LOAD_GLOBAL":
                名 = str(ins.argval)
                if 名 not in 已知:
                    缺.add(f"{码.co_name}:{名}")
    return sorted(缺)


def scan_dir(root: Path, skip: tuple = ("__pycache__",)) -> dict:
    """扫一个目录下全部 .py，返回 {相对路径: [未定义名...]}（只含非空的）。"""
    坏: dict = {}
    for f in sorted(root.rglob("*.py")):
        if any(s in f.parts for s in skip):
            continue
        try:
            缺 = undefined_globals_in_file(f, f.stem)
        except Exception as exc:                # noqa: BLE001 扫不动不算缺陷
            缺 = [f"<扫描失败>:{type(exc).__name__}"]
        if 缺:
            坏[str(f.relative_to(root)).replace("\\", "/")] = 缺
    return 坏


def missing_module_attrs(src_dir: Path) -> dict:
    """★★★★★ 同一类缺陷**深一层**：扫「模块别名`.`属性」里**不存在的属性**。

    ★ 缘起（真缺陷，本门的存在理由）：
      `taixu_mcp` 调 `check_cmd_safety(..., repo_root=rt.REPO_ROOT)`，
      而 `real_task` **从未暴露** `REPO_ROOT`
      ⇒ `run_errand` / `submit_task` 每次调用都 `AttributeError`，
         MCP 回 "Error executing tool 示例·任意任务: REPO_ROOT"。
      `undefined_globals` 抓不到它 —— 那是**属性读**，不是全局名读。
      **同一类**（被读却从未定义），只是深了一层。

    ★ 判法：扫源码里 `<别名>.<属性>` 形态，把 `<别名>` 是**本包内模块**的那些
      收集起来，import 那个模块，`hasattr` 查属性是否存在。
      ⇒ 存在性判定，不是启发式。`real_task` 那种 `__getattr__` 兼容层也照样成立
        （`hasattr` 会真的去问它）。

    ★ 诚实边界：只查**本包内模块**的别名（`rt.` / `prof_mod.` / `ds.` 这类），
      不查第三方库、不查动态 `getattr(obj, name)`、不查实例属性。
    """
    import importlib
    import re as _re
    本包 = {f.stem for f in src_dir.glob("*.py") if not f.stem.startswith("_")}
    # ★★★★★ 关键（本门第一版在这里**假红**，实测 2 处误报，全是本门自己的错）：
    #   ① **别名表必须逐文件建**，不能全局建。
    #      第一版把全树的 `import X as a` 汇成一张全局表 ⇒
    #      `samples.py` 里 `gs` 是 `gate_spec`，却被别处的 `import gap_source as gs`
    #      覆盖成 `gap_source` ⇒ 报 `gs.evaluate` 不存在（**跨文件污染**，
    #      正是我一直在修的那一类缺陷，这回出在我自己的门里）。
    #   ② **别名被本文件局部重绑时必须跳过**：
    #      `generic_goal.py` 里 `gg = dict(g)` 是**局部字典**，
    #      而同文件另有 `... import generic_goal as gg` ⇒ 报 `gg.get` 不存在。
    #      ⇒ 本文件出现过 `gg = ...` 赋值，就不拿 `gg` 当模块别名查。
    #   ★ 纪律：**修门，不改代码**。误报的门比没有门更坏 —— 它教人忽略红。
    坏: dict = {}
    for f in sorted(src_dir.glob("*.py")):
        源 = f.read_text(encoding="utf-8", errors="ignore")
        # ① 逐文件建别名表
        本文件别名: dict = {}
        for m in _re.finditer(
                r"^\s*(?:from\s+[\w.]+\s+import\s+(\w+)\s+as\s+(\w+)"
                r"|import\s+[\w.]*?(\w+)\s+as\s+(\w+))", 源, _re.M):
            g = [x for x in m.groups() if x]
            if len(g) == 2 and g[0] in 本包:
                本文件别名[g[1]] = g[0]
        if not 本文件别名:
            continue
        # ② 局部重绑的别名不算模块
        for 别 in list(本文件别名):
            if _re.search(rf"^\s*{_re.escape(别)}\s*=[^=]", 源, _re.M) \
                    or _re.search(rf"\bfor\s+{_re.escape(别)}\b", 源):
                本文件别名.pop(别, None)
        缺: set = set()
        for m in _re.finditer(r"\b(\w+)\.([A-Za-z_]\w*)", 源):
            别, 属 = m.group(1), m.group(2)
            if 别 not in 本文件别名:
                continue
            真名 = 本文件别名[别]
            try:
                模 = importlib.import_module(
                    f"agentscope_orchestrator.{真名}")
            except Exception:                        # noqa: BLE001 导不动就跳过
                continue
            if not hasattr(模, 属):
                缺.add(f"{别}.{属}（{真名} 无此属性）")
        if 缺:
            坏[f.name] = sorted(缺)
    return 坏


def _selftest() -> int:
    """门必须**有判据力**：对含未定义名的模块必须报，对干净模块必须不报。"""
    坏: list = []
    # ① 有效：造一个真含未定义名的模块
    假 = types.ModuleType("_假")
    exec(compile("def f():\n    return _从未定义 + 1\n", "<假>", "exec"),
         假.__dict__)
    假.__name__ = "_假"
    if not undefined_globals(假):
        坏.append("对含未定义名的模块返回空 ⇒ 门失效（恒真）")
    # ② 不误报：干净模块必须为空
    净 = types.ModuleType("_净")
    exec(compile("import os\n\ndef f():\n    return os.getcwd()\n",
                 "<净>", "exec"), 净.__dict__)
    净.__name__ = "_净"
    if undefined_globals(净):
        坏.append(f"对干净模块误报：{undefined_globals(净)}")
    # ③ 文件版（不 import）：含未定义名必须报
    import tempfile
    坏f = Path(tempfile.mkdtemp()) / "x.py"
    坏f.write_text("def g():\n    return _也没有定义\n", encoding="utf-8")
    if not undefined_globals_in_file(坏f, "x"):
        坏.append("文件版静态扫对未定义名返回空 ⇒ 门失效")
    # ④ 文件版：语法错必须**如实报**，不是静默通过
    坏s = Path(tempfile.mkdtemp()) / "y.py"
    坏s.write_text("def g(:\n", encoding="utf-8")
    if not undefined_globals_in_file(坏s, "y"):
        坏.append("文件版对语法错返回空 ⇒ 静默通过（应如实报）")
    # ⑤ ★ 属性读门：对**真源码树**必须为 0（本轮修完 `rt.REPO_ROOT` 后）
    _src = Path(__file__).resolve().parent
    _缺 = missing_module_attrs(_src)
    if _缺:
        坏.append(f"本包存在'不存在的模块属性'读：{_缺}")
    if 坏:
        print("static_audit 自检：失败")
        for x in 坏:
            print("  [FAIL]", x)
        return 1
    print("static_audit 自检：5/5 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
