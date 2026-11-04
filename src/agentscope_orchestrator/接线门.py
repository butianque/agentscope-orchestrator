#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""接线门 —— 判「目标点名的接口，**真的接上生产路径**了吗」。

用法：
    python 接线门.py <接口名> <仓库根>
    退出码 0 = 接上了；1 = 没接上（**判红**）；2 = 用法错

★★★★★ R88（2026-10-18 · 由 R86/R87 顺着查出来）：

  【真跑证据 —— 门会撒谎】第 1 轮报 green，门里有
    `新测试_判据_甲子生产路径生效 = True`
  而 `判据_甲子` 的生产消费者**仍然是 0**（唯一调用点在 `#[cfg(test)]` 里）。
  ★ 那条门**名字承诺**「生产路径生效」，**实际判**的是「文件里有这个词」——
    而这个词当然有（定义行 + 别人的测试）。
    ⇒ 门绿了，但它证明的东西和它名字说的**不是一回事**。

  【为什么这条判据是必然的，不是启发式】
    "这个接口接上了" 的**定义**就是 "生产路径上有引用"。
    剥净后零引用 ⇒ **它不可能被任何生产路径调用** ⇒ 目标未达成。
    这与 `gap_source.扫描无消费者接口` **同一口径** —— 同一条判据，两个用法：
      · 规划前：零引用 ⇒ **缺口**（该派活）；
      · 交付时：零引用 ⇒ **没接上**（该判红）。
    ★ 这就是「判据不得由被判方改动」的正确形态：
      判定用的是**结构**，模型改不了结构，只能改出真结构。

  【剥净的三步，顺序不能反】
    ① 剥注释与字面量（**长度不变**，换成空格 ⇒ 行号不变）；
    ② 剥 `#[cfg(test)] mod`（按大括号配平）；
    ③ 数**裸名字**（不带括号也算 —— 函数被当值传、被 `use` 引入都是消费者）。
    ★ ① 必须在 ② 之前：字符串里的 `}` 会让大括号配平**提前闭合**
      （`gap_source` 的齿 ④ 当场抓到过）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def 剥注释字面量(源: str) -> str:
    """注释与字符串/字符字面量换成空格（**保留换行**，长度不变）。"""
    出 = list(源)
    i, n = 0, len(源)
    while i < n:
        c = 源[i]
        if c == "/" and i + 1 < n and 源[i + 1] == "/":
            j = 源.find("\n", i)
            if j < 0:
                j = n
            for k in range(i, j):
                出[k] = " "
            i = j
            continue
        if c == "/" and i + 1 < n and 源[i + 1] == "*":
            j, 深 = i + 2, 1
            while j < n and 深:
                if 源[j] == "/" and j + 1 < n and 源[j + 1] == "*":
                    深 += 1
                    j += 2
                    continue
                if 源[j] == "*" and j + 1 < n and 源[j + 1] == "/":
                    深 -= 1
                    j += 2
                    continue
                j += 1
            for k in range(i, min(j, n)):
                if 出[k] != "\n":
                    出[k] = " "
            i = j
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if 源[j] == "\\":
                    j += 2
                    continue
                if 源[j] == '"':
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, n)):
                if 出[k] != "\n":
                    出[k] = " "
            i = j
            continue
        i += 1
    return "".join(出)


def 剥测试模块(源: str) -> str:
    """把 `#[cfg(test)] mod … { … }` 整块剥掉（按大括号配平）。"""
    区间 = []
    for m in re.finditer(r"#\[cfg\(test\)\]\s*(?:pub\s+)?mod\s+\w+\s*\{", 源):
        j, 深 = m.end(), 1
        while j < len(源) and 深:
            if 源[j] == "{":
                深 += 1
            elif 源[j] == "}":
                深 -= 1
            j += 1
        区间.append((m.start(), j))
    if not 区间:
        return 源
    出, 上 = [], 0
    for a, b in 区间:
        出.append(源[上:a])
        上 = b
    出.append(源[上:])
    return "".join(出)


def 数生产引用(根: Path, 名: str, 后缀: str = "*.rs") -> int:
    """剥净之后数**生产引用**（排除定义本身）。

    ★★★★★ R96（真跑抓出的第 9 层 · 2026-10-19）：

      【真跑证据】`判据_两侧全等` 在本模块报「生产引用 **6 处** ⇒ 真的接上了」，
        而 `gap_source.扫描无消费者接口` 同时报「**零引用**，还可派」。
        ★ **两台机器对同一件事给出相反答案**，而两台都是「机器」。

      【根因】本函数原来用 `根.rglob(后缀)` 扫**整个仓库**。
        编排器自己的工作副本在
            `多智能体编排/agentscope_orchestrator/work/task/r1|r2|r3/…`
        —— 那是**模型本轮刚写的草稿**。6 处引用**全部来自那三个草稿目录**，
        主树里真正的生产引用是 **0**。
        ⇒ 于是：模型把接口接进**草稿** ⇒ 本门读到草稿 ⇒ **绿**
          ⇒ 而 `gap_source` 只扫 `src/` ⇒ 仍报缺口 ⇒ 规划器被反复告知
          「它还是零引用」⇒ 判据违规点不降 ⇒ **全轮回退，永不收敛**。

      【性质】**判据由被判方喂饱**。R88 建本门的目的是「门名承诺生产路径、
        身体只查文本」，而它自己犯的正是同一件事的更狠版本：
        **它把被测对象的草稿当成了它的证据。**
        这与本库「判据不得由被判方改动」是同一条宪法级约束。

      【修法】**不写第三份枚举实现**，直接用 `只读探针.源文件们` ——
        它用 `git ls-files`（= 「本项目自己的源码」的定义），
        未跟踪的 `work/`、`products/`、备份目录**自然不在其中**。
        ★ 三份枚举 = 三把尺子 = 正是本库反复吃亏的那个形状
          （`见证门` 与 `只读探针` 早已改用 git，本模块是漏网的那个）。
    """
    总 = 0
    名RE = re.compile(r"(?<![\w])" + re.escape(名) + r"(?![\w])")
    定义RE = re.compile(r"pub\s+fn\s+" + re.escape(名) + r"(?![\w])")
    from agentscope_orchestrator import 只读探针 as _探针96
    for f in _探针96.源文件们(根, 后缀):
        if not f.is_file():
            continue
        try:
            原 = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        净 = 剥测试模块(剥注释字面量(原))
        总 += _数非透传引用(净, 名, 名RE) - len(定义RE.findall(净))
    return 总


def _函数体们(净: str) -> list[tuple[int, int, str]]:
    """剥净文本里每个 `fn` 的 (体起点, 体终点, 体文本)。大括号配平。"""
    出: list[tuple[int, int, str]] = []
    for m in re.finditer(r"\bfn\s+[A-Za-z0-9_\u4e00-\u9fff]+", 净):
        b = 净.find("{", m.end())
        if b < 0:
            continue
        # ★ 跳过返回类型里的花括号（极少见，但 `-> impl Fn() {` 会误配）
        深 = 0
        for i in range(b, len(净)):
            c = 净[i]
            if c == "{":
                深 += 1
            elif c == "}":
                深 -= 1
                if 深 == 0:
                    出.append((b, i, 净[b + 1:i]))
                    break
    return 出


def _是透传体(体: str, 名: str) -> bool:
    """这个函数体是不是**纯透传**：整段就是一次对 `名` 的调用。

    ★ R98（真跑抓出的第 12 层 · 2026-10-19）：
      `判据_甲子` 的「生产消费者」是 `甲子环_连续裁决`，其体是
          `接口与判据::判据_甲子(甲相位, 乙相位)`
      —— 它**只是把参数原样转手**，自己不做任何裁决。
      而 `甲子环_连续裁决` 自己**零消费者** ⇒ 整条链终止于死代码。
      ★ 可本门当时报「生产引用 1 处 ⇒ 真的接上了」。
      ⇒ **本门只查一跳。** 引用链可以在**任意深度**上终止于死代码，
        而每一跳都满足「有 1 处生产引用」。
      ★ 于是两次真跑都交出了**假闭合**：
        · `判据_甲子`   → `甲子环_连续裁决` → `甲子环_并入裁决`（零消费者）
        · `判据_两侧全等` → `裁决_两侧全等`   → （零消费者）
        而缺口清单从 3 项降到 1 项 —— **不是因为做完了，是因为
        模型建了 `裁决_*` / `甲子环_*` 这种不在 `判据_` 前缀里的包装，
        扫描器按前缀过滤，看不见它们。**

      ★ 判据：**透传不算消费者**。消费者必须「结果参与改变输出」——
        一个整段只有一次调用的函数，没有改变任何东西。
        这是本库 `TS525_TheRuleComputesTheBreaker` 那条的工程对应：
        **值必须被用，不是被传递。**
    """
    s = 体.strip().rstrip(";").strip()
    if not s:
        return False
    # 允许 `路径::名(...)`、`名(...)`，且整段不得有第二句（`;` 之外的分号、`if`、`let` 等）
    m = re.fullmatch(r"(?:[A-Za-z0-9_\u4e00-\u9fff]+\s*::\s*)*"
                     + re.escape(名) + r"\s*\(.*\)", s, re.S)
    return bool(m)


def _数非透传引用(净: str, 名: str, 名RE) -> int:
    """生产引用数，**排除落在纯透传函数体里的那些**（R98）。"""
    体们 = _函数体们(净)
    透传区 = [(a, b) for (a, b, 体) in 体们 if _是透传体(体, 名)]
    n = 0
    for m in 名RE.finditer(净):
        if any(a <= m.start() <= b for (a, b) in 透传区):
            continue
        n += 1
    return n


def _selftest() -> int:
    """自检：**两个方向都要给相反答案**（否则它既可能是恒红也可能是恒绿）。"""
    import tempfile
    fails: list[str] = []
    根 = Path(tempfile.mkdtemp())

    # ① 只有定义 + 测试里调用 + 注释里提到 ⇒ **零生产引用** ⇒ 必须判 0
    (根 / "a.rs").write_text(
        "pub fn 判据_测(x: u32) -> u32 { x }\n"
        "#[cfg(test)]\n"
        "mod 测试 {\n"
        "    fn t() { let _s = \"}\"; }\n"      # ★ 字符串含 } ⇒ 剥净顺序的判据
        "    fn u() { 判据_测(9); }\n"
        "}\n"
        "/// 判据_测 在这里被讨论\n",
        encoding="utf-8")
    if 数生产引用(根, "判据_测") != 0:
        fails.append("① 只在测试/注释里出现，却数出了生产引用 ⇒ 会假绿")

    # ② 真接到生产路径 ⇒ 必须 > 0（否则是恒红门）
    # ★ R98：夹具必须写成**真的用结果**的消费者。
    #   原来写 `pub fn 用() -> u32 { crate::a::判据_测(7) }` —— 那是**纯透传**，
    #   R98 之后正确地不算消费者 ⇒ 齿 ② 当场变红。
    #   ★ 这暴露了那条纪律：**夹具选得巧，齿就会变瞎** ——
    #     老夹具一直在替这个 bug 作证（它自己就是透传）。
    (根 / "b.rs").write_text(
        "pub fn 用() -> u32 { if crate::a::判据_测(7) { 1 } else { 0 } }\n",
        encoding="utf-8")
    if 数生产引用(根, "判据_测") <= 0:
        fails.append("② 真的接到生产路径，却数出 0 ⇒ 恒红门，任务永不收敛")

    # ③ 注释里再提一次不得加分（剥注释必须生效）
    前 = 数生产引用(根, "判据_测")
    (根 / "c.rs").write_text("// 判据_测 判据_测 判据_测\n", encoding="utf-8")
    if 数生产引用(根, "判据_测") != 前:
        fails.append("③ 注释里的提及被算成生产引用 ⇒ 剥注释失效")

    # ④ 字符串里再提一次不得加分
    (根 / "d.rs").write_text('pub fn s() -> &\'static str { "判据_测" }\n',
                             encoding="utf-8")
    if 数生产引用(根, "判据_测") != 前:
        fails.append("④ 字符串里的提及被算成生产引用 ⇒ 剥字面量失效")

    # ⑤ 剥净顺序：反序时字符串里的 `}` 会让测试模块提前闭合
    #   ⇒ 这条夹具在**反序实现**上会给出非 0（真缺陷形态）
    if 数生产引用(根, "判据_测") == 0:
        fails.append("⑤ 正向夹具给 0，但 ② 已经加过生产调用 ⇒ 实现自相矛盾")

    # ══════════════════════════════════════════════════════════════════
    # 【R96】判据不得由被判方喂饱：**编排器自己的草稿不算生产消费者**
    # ══════════════════════════════════════════════════════════════════
    # 真跑形态（2026-10-19 · 第二次端到端）：本模块报「生产引用 6 处 ⇒ 真的
    # 接上了」，而 `gap_source.扫描无消费者接口` 同时报「零引用」。**两台机器
    # 对同一件事给出相反答案**。根因：`数生产引用` 用 `根.rglob()` 扫全仓库，
    # 把 `work/task/r1|r2|r3/`（模型本轮刚写的**草稿**）算成了生产消费者。
    # ⇒ 门被**被测对象自己的草稿**喂饱 ⇒ 恒绿 ⇒ 缺口永远修不掉 ⇒ 不收敛。
    try:
        import tempfile as _tf
        import subprocess as _sp
        import shutil as _sh
        import contextlib as _cx

        # ★ Windows：`TemporaryDirectory` 退出时会因**文件句柄未释放**
        #   （git 子进程/读句柄）抛 `PermissionError: [WinError 32]`
        #   ⇒ 齿自己炸掉，看起来像"齿有问题"而不是"实现有问题"。
        #   用 `mkdtemp` + `ignore_errors` 清理，把失败隔离在夹具之外。
        @_cx.contextmanager
        def _临时():
            _d = _tf.mkdtemp(prefix="r96_")
            try:
                yield Path(_d)
            finally:
                _sh.rmtree(_d, ignore_errors=True)

        名96 = "判据_R96夹具"
        _def = f"pub fn {名96}() -> bool {{ true }}\n"

        # ① 非 git 环境（走 rglob 回退）：`work/` 草稿不得算生产
        with _临时() as _r:
            (_r / "src").mkdir()
            (_r / "src" / "a.rs").write_text(_def, encoding="utf-8")
            (_r / "work" / "task" / "r1" / "src").mkdir(parents=True)
            (_r / "work" / "task" / "r1" / "src" / "a.rs").write_text(
                _def + f"pub fn 用() -> bool {{ if {名96}() {{ true }} else {{ false }} }}\n", encoding="utf-8")
            if 数生产引用(_r, 名96) != 0:
                fails.append(f"【R96】★ 非 git 回退路径把 `work/` 草稿算成生产消费者"
                             f"（{数生产引用(_r, 名96)} 处）⇒ 门被草稿喂饱、恒绿")

        # ② 真 git 仓库 + 未跟踪草稿：git 路径也必须排除
        with _临时() as _r:
            (_r / "src").mkdir()
            (_r / "src" / "a.rs").write_text(_def, encoding="utf-8")
            _env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                    "PATH": __import__("os").environ.get("PATH", "")}
            for _c in (["git", "init", "-q"], ["git", "add", "-A"],
                       ["git", "commit", "-qm", "x"]):
                _sp.run(_c, cwd=str(_r), env=_env, capture_output=True, timeout=60)
            (_r / "work" / "task" / "r1" / "src").mkdir(parents=True)
            (_r / "work" / "task" / "r1" / "src" / "a.rs").write_text(
                _def + f"pub fn 用() -> bool {{ if {名96}() {{ true }} else {{ false }} }}\n", encoding="utf-8")
            if 数生产引用(_r, 名96) != 0:
                fails.append(f"【R96】★ git 路径把未跟踪的 `work/` 草稿算成生产消费者"
                             f"（{数生产引用(_r, 名96)} 处）")

        # ③ 正向对照：**真正的**生产消费者必须算进去（否则门恒红，是另一种瞎）
        with _临时() as _r:
            (_r / "src").mkdir()
            (_r / "src" / "a.rs").write_text(
                _def + f"pub fn 用() -> bool {{ if {名96}() {{ true }} else {{ false }} }}\n", encoding="utf-8")
            if 数生产引用(_r, 名96) != 1:
                fails.append(f"【R96】★ 真生产消费者没被数到（得 {数生产引用(_r, 名96)}，应为 1）")

        # ④ 只在测试模块里调用 ⇒ 不算（与缺口扫描同一口径）
        with _tf.TemporaryDirectory() as _d:
            _r = Path(_d)
            (_r / "src").mkdir()
            (_r / "src" / "a.rs").write_text(
                _def + "#[cfg(test)]\nmod tests {\n"
                f"    fn t() {{ super::{名96}(); }}\n}}\n", encoding="utf-8")
            if 数生产引用(_r, 名96) != 0:
                fails.append("【R96】★ 只在测试模块里调用也算成了生产消费者")
    except Exception as _e96:
        fails.append("【R96】草稿隔离齿自身异常：%s: %s"
                     % (type(_e96).__name__, _e96))

    # ══════════════════════════════════════════════════════════════════
    # 【R98】透传包装不算生产消费者（引用链不得终止于死代码）
    # ══════════════════════════════════════════════════════════════════
    # 真跑形态（2026-10-19 · 第 3/4 次端到端）：两次「闭合」都是假的 ——
    #   `判据_甲子` → `甲子环_连续裁决`（**纯透传**）→ `甲子环_并入裁决`（零消费者）
    #   `判据_两侧全等` → `裁决_两侧全等`（**纯透传**）→ 零消费者
    # 而本门当时报「生产引用 1 处 ⇒ 真的接上了」⇒ **只查一跳**。
    try:
        import tempfile as _tf98
        import shutil as _sh98

        名98 = "判据_R98夹具"

        def _写(files: dict) -> Path:
            d = Path(_tf98.mkdtemp(prefix="r98_"))
            (d / "src").mkdir()
            for k, v in files.items():
                (d / "src" / k).write_text(v, encoding="utf-8")
            return d

        定义98 = f"pub fn {名98}(甲: u8, 乙: u8) -> bool {{ 甲 == 乙 }}\n"

        # ① 纯透传包装 ⇒ 不算消费者（真跑那个 bug）
        _d = _写({"a.rs": 定义98 + "pub fn 包装(甲: u8, 乙: u8) -> bool {\n"
                            f"    {名98}(甲, 乙)\n}}\n"})
        try:
            if 数生产引用(_d, 名98) != 0:
                fails.append(f"【R98】★ 纯透传包装被算成消费者（{数生产引用(_d, 名98)} 处）"
                             f"⇒ 引用链可终止于死代码而门判绿（两次真跑都是假闭合）")
        finally:
            _sh98.rmtree(_d, ignore_errors=True)

        # ② 正向对照：**真的用了结果**的消费者必须算（否则门恒红，是另一种瞎）
        _d = _写({"a.rs": 定义98 + "pub fn 真用(甲: u8, 乙: u8) -> u8 {\n"
                            f"    if {名98}(甲, 乙) {{ 1 }} else {{ 0 }}\n}}\n"})
        try:
            if 数生产引用(_d, 名98) != 1:
                fails.append(f"【R98】★ 真消费者（结果参与改变输出）没被数到"
                             f"（得 {数生产引用(_d, 名98)}，应为 1）")
        finally:
            _sh98.rmtree(_d, ignore_errors=True)

        # ③ 不能过度排除：体里还有别的语句 ⇒ 不是纯透传 ⇒ 必须算
        _d = _写({"a.rs": 定义98 + "pub fn 记一笔(甲: u8, 乙: u8) -> bool {\n"
                            f"    let r = {名98}(甲, 乙);\n    r && 甲 > 0\n}}\n"})
        try:
            if 数生产引用(_d, 名98) != 1:
                fails.append(f"【R98】★ 过度排除：带 `let` 的真实使用也被当成透传"
                             f"（得 {数生产引用(_d, 名98)}，应为 1）")
        finally:
            _sh98.rmtree(_d, ignore_errors=True)

        # ④ 带路径的透传（`mod::名(...)`）也必须被认出
        _d = _写({"a.rs": 定义98 + "pub fn 包装(甲: u8, 乙: u8) -> bool {\n"
                            f"    crate::x::{名98}(甲, 乙)\n}}\n"})
        try:
            if 数生产引用(_d, 名98) != 0:
                fails.append("【R98】★ 带路径的纯透传没被认出")
        finally:
            _sh98.rmtree(_d, ignore_errors=True)
    except Exception as _e98:
        fails.append("【R98】透传齿自身异常：%s: %s" % (type(_e98).__name__, _e98))

    if fails:
        for x in fails:
            print(f"  ✗ 接线门 {x}")
        print(f"接线门 自检：失败 {len(fails)}")
        return 1
    print("接线门 自检：全绿（零生产引用判红 · 真接线判绿 · 注释/字符串不计）")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if a and a[0] == "--selftest":
        return _selftest()
    if len(a) < 2:
        print("用法：接线门.py <接口名> <仓库根> [后缀，默认 *.rs]")
        return 2
    名, 根 = a[0], Path(a[1])
    后缀 = a[2] if len(a) > 2 else "*.rs"
    if not 根.is_dir():
        print(f"【R88】仓库根不存在：{根}")
        return 2
    总 = 数生产引用(根, 名, 后缀)
    if 总 <= 0:
        print(f"【R88】`{名}` 在**测试模块之外零引用** ⇒ 目标说的「接上」没有发生")
        print("  ★ 判定口径与缺口扫描同一台机器：剥净后数裸名字。")
        print("  ★ 只改注释 / 只在测试里调用 **都不算**。")
        return 1
    print(f"【R88】`{名}` 生产引用 {总} 处 ⇒ 真的接上了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
