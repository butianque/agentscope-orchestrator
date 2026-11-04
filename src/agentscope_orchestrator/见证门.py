#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""见证门 —— 判「测试名点名的接口，**断言里真的调了它**吗」。

用法：
    python 见证门.py <测试名> <仓库根> [前缀1 前缀2 …]
    退出码 0 = 有见证（或名字没点名任何接口，本条不适用）
           1 = **没见证**（名字承诺了，测试体里没调 ⇒ 判红）
           2 = 用法错

★★★★★ R91（2026-10-18 · 由 R88 顺着查出来的最后一段）：

  【真跑证据 —— 门是诚实的，撒谎的是测试】
    第 1 轮 green 里有 `新测试_判据_甲子生产路径生效 = True`。
    去读那条门：`{"type": "compile", "cmd": ["cargo", "test", "--lib", "新测试_判据_甲子生产路径生效"]}`。
    ★ 门**老老实实**跑了那条测试，测试**真的通过了**。
      **撒谎的是测试本身** —— 名字叫「生产路径生效」，
      断言是「源码里出现了这个词」。而这个词当然有（定义行 + 别人的测试）。

  ⇒ 真正的问题不是「门名不副实」，是：
    > **测试名承诺的行为，测试体能不能见证？**

  【为什么这是机器可判的，而且判据是必然的】
    一条断言要能「见证」某接口的行为，**前提是它真的调用了那个接口**：
      · 断言 `源.contains("判据_甲子")` ⇒ 见证的是**文本**，删掉这个词它就红；
      · 断言 `判据_甲子(1, 1) == true` ⇒ 见证的是**行为**，改坏它才红。
    **两者的失败条件不同，所以能分开。**

    ★ 与库内 `MetaTheory/WitnessExtractor.lean` 同一件事：
      「答案 = 证明树里的 witness」。
      一条承诺「∃ 生产调用点」的证明，**必须在证明树里携带那个调用点**。
      没携带 ⇒ 它证的是别的东西。
    ★ 与 `TS525_TheRuleComputesTheBreaker` 同一件事：
      **值必须来自表，不来自 `Prop`。**
      「测试通过了」是 `Prop`；「断言里调了它几次」是**表里的数**。

  【与 `接线门.py` 的关系：同一台机器，第四个用法】
    | 用法 | 选区 | 零引用的含义 |
    |:--|:--|:--|
    | 缺口扫描 | **去掉**测试模块 | 缺口 |
    | 接线门（R88） | **去掉**测试模块 | 没接上 ⇒ 判红 |
    | **见证门（R91）** | **只要**测试函数体 | 见证不了它名字说的 ⇒ 判红 |

  【诚实边界 —— 三条，都朝「零回退」】
    · **只认完整接口名**（带前缀）。核心名太松：
      `判据_复用` 的核心是 `复用`，而 `复用链_代表元升入当前代` 里有 `复用`
      ⇒ 会**误伤一个完全无关的测试**。
    · 名字里**没点名任何接口** ⇒ **本条不适用**（不猜意图）。
      这是 TS-512 的口径：**名字是锚（选择），不是判据（判断）**；
      机器只在有锚的地方扫表。
    · 找不到那个测试函数 ⇒ 判红（门要跑它，它却不存在 ⇒ 门本身有问题）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


# 不扫的目录：构建产物 / 版本库 / 依赖 / 备份
_跳过目录 = frozenset({"target", ".git", "node_modules", ".venv", "__pycache__",
                       "vendor", "dist", "build", ".data", ".cargo_home",
                       "备份", "backup"})


def 源文件们(根: Path, 后缀: str = "*.rs") -> list[Path]:
    """**本项目自己的**源文件。

    ★★★★★ 2026-10-18 真项目实测（这一条把门从 45 秒压到亚秒级）：

      第一版用 `根.rglob("*.rs")` ⇒ 在 `示例工程` 上扫到 **15,576 个文件 / 553 MB**。
      而真源码只有 **484 个**（`src/` 下）。多出来的是：
        · `.cargo_home`（依赖源码）5,738 个
        · `备份` 5,308 个
        · 各种 `_backup_*` / `*_源码包`

      ★ 后果不只是**慢**：备份目录里有**旧版本的接口定义** ⇒
        机器会把「一个早已删掉的接口」当成有效声明 ⇒ **判据的域是错的**。

      ⇒ 改用 **`git ls-files`**：那正是「本项目自己的源码」的定义 ——
        它天然排除备份、依赖缓存、构建产物，且**与语言无关**。
        1,747 个文件（真源码 + 少量示例），**0 秒**列举。

      ★ 不是 git 仓库（或 git 不可用）⇒ 退回 rglob + 跳过目录（老行为）。
    """
    import subprocess as _sp
    try:
        r = _sp.run(["git", "-C", str(根), "ls-files", 后缀],
                    capture_output=True, timeout=120)
        if r.returncode == 0:
            名们 = [x for x in (r.stdout or b"").decode("utf-8", "replace").splitlines()
                    if x.strip()]
            if 名们:
                出 = []
                for x in 名们:
                    f = 根 / x.strip()
                    if f.is_file() and not any(y in f.parts for y in _跳过目录):
                        出.append(f)
                return sorted(出)
    except Exception:
        pass
    return sorted(f for f in 根.rglob(后缀)
                  if f.is_file() and not any(x in f.parts for x in _跳过目录))

_剥RE = re.compile(
    r"//[^\n]*"                      # 行注释
    r"|/\*.*?\*/"                    # 块注释（Rust 可嵌套；非嵌套情形已足够，
                                      #   嵌套时多剥的那段仍是注释 ⇒ 不影响判定）
    r"|\"(?:\\.|[^\"\\\\])*\""  # 字符串
    r"|'(?:\\.|[^'\\\\])'"         # 字符字面量
    , re.S)


def _换空格(m: re.Match) -> str:
    """把命中段换成**等长**空格（保留换行 ⇒ 行号与括号位置不变）。"""
    return "".join("\n" if ch == "\n" else " " for ch in m.group(0))


def 剥注释字面量(源: str) -> str:
    """注释与字符串/字符字面量换成空格（保留换行，**长度不变**）。

    ★ 为什么用正则而不是逐字符循环（2026-10-18 · 真项目实测）：
      逐字符版本在 `示例工程` 这棵树上**单次要几分钟**，
      而见证门要在**每个任务**上跑 ⇒ 直接拖垮门。
      正则在 C 层扫，同一棵树**亚秒级**。
      ★ 这不是过早优化：**门的耗时是产品属性** ——
        一条 900 秒超时的门如果常态跑 600 秒，它迟早会假红。
    """
    return _剥RE.sub(_换空格, 源)


def 取函数体(净源: str, 函数名: str) -> str | None:
    """取 `fn <函数名>(…) -> … { … }` 的**函数体**（按大括号配平）。

    ★ 传进来的必须是**已剥净**的源（注释/字面量已换空格）——
      否则字符串里的 `{` `}` 会让配平**提前闭合**。
      这个顺序在 `gap_source` 的齿 ④ 和 R88 的齿 ② 上各错过一次。
    """
    模式 = re.compile(r"\bfn\s+" + re.escape(函数名) + r"\s*[(<]")
    for m in 模式.finditer(净源):
        # 从签名末尾找第一个 `{`
        j = m.end()
        深括 = 0
        while j < len(净源):
            c = 净源[j]
            if c in "([<":
                深括 += 1
            elif c in ")]>":
                深括 -= 1
            elif c == "{" and 深括 <= 0:
                break
            elif c == ";":
                break
            j += 1
        if j >= len(净源) or 净源[j] != "{":
            continue          # 声明没有函数体（trait 声明等）
        起 = j
        深 = 1
        j += 1
        while j < len(净源) and 深:
            if 净源[j] == "{":
                深 += 1
            elif 净源[j] == "}":
                深 -= 1
            j += 1
        return 净源[起:j]
    return None


def 收集接口名(根: Path, 前缀们: tuple[str, ...], 后缀: str = "*.rs") -> list[str]:
    """仓库里**声明过的**、符合命名约定的接口名（`pub fn <前缀>…`）。"""
    出: list[str] = []
    模式 = re.compile(r"\bpub\s+fn\s+([\w\u4e00-\u9fff]+)")
    for f in 源文件们(根, 后缀):
        try:
            净 = 剥注释字面量(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for m in 模式.finditer(净):
            名 = m.group(1)
            if any(名.startswith(p) for p in 前缀们) and 名 not in 出:
                出.append(名)
    return 出


def 见证缺口(根: Path, 测试名: str, 前缀们: tuple[str, ...],
             后缀: str = "*.rs") -> tuple[list[str], list[str], bool]:
    """返回 (名字点名的接口们, 其中**没被调用**的, 是否找到了那个测试函数)。

    ★ **单趟扫树**（2026-10-18）：第一版分两趟、每趟都把全树剥一遍，
      在真项目上要几分钟。合成一趟后只需剥一次。
    """
    接口名RE = re.compile(r"\bpub\s+fn\s+([\w\u4e00-\u9fff]+)")
    测试RE = re.compile(r"\bfn\s+" + re.escape(测试名) + r"\s*[(<]")
    接口们: list[str] = []
    体: str | None = None
    for f in 源文件们(根, 后缀):
        try:
            原 = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "fn " not in 原:
            continue
        净 = 剥注释字面量(原)
        for m in 接口名RE.finditer(净):
            名 = m.group(1)
            if any(名.startswith(p) for p in 前缀们) and 名 not in 接口们:
                接口们.append(名)
        if 体 is None and 测试RE.search(净):
            体 = 取函数体(净, 测试名)

    # ★ 只认**完整接口名**（见文件头「诚实边界」第一条）
    点名 = [x for x in 接口们 if x in 测试名]
    if not 点名:
        return [], [], True          # 名字没点名 ⇒ 本条不适用
    if not 体:
        return 点名, list(点名), False     # 找不到 ⇒ 门在跑一个不存在的东西

    缺 = [x for x in 点名
          if not re.search(r"(?<![\w])" + re.escape(x) + r"(?![\w])", 体)]
    return 点名, 缺, True


def _selftest() -> int:
    """自检：**两个方向都要给相反答案**，且三条边界都要守住。"""
    import tempfile
    fails: list[str] = []
    根 = Path(tempfile.mkdtemp())
    (根 / "a.rs").write_text(
        "pub fn 判据_真(x: u32) -> u32 { x }\n"
        "pub fn 判据_假(x: u32) -> u32 { x }\n"
        "pub fn 判据_复用(x: u32) -> u32 { x }\n"
        "#[cfg(test)]\n"
        "mod 测试 {\n"
        "    fn 判据_真_行为被见证() { assert_eq!(判据_真(1), 1); }\n"
        "    fn 判据_假_行为被见证() { assert!(true); }\n"
        "    fn 复用链_代表元升入当前代() { assert!(true); }\n"
        "}\n",
        encoding="utf-8")

    # ① 断言里真的调了 ⇒ 无缺口
    _, 缺, 找到了 = 见证缺口(根, "判据_真_行为被见证", ("判据_",))
    if 缺 or not 找到了:
        fails.append(f"① 断言里真的调了接口，却判成缺 {缺}（找到了={找到了}）⇒ 恒红门")

    # ② 名字点名了但**没调** ⇒ 必须判缺（真跑那条假 green 的形态）
    _, 缺2, _ = 见证缺口(根, "判据_假_行为被见证", ("判据_",))
    if not 缺2:
        fails.append("② 名字点名 `判据_假`、断言只写 `assert!(true)`，却判成有见证 ⇒ 假绿")

    # ③ 名字**没点名**任何接口 ⇒ 不适用（零回退）
    点名3, _, _ = 见证缺口(根, "复用链_代表元升入当前代", ("判据_",))
    if 点名3:
        fails.append(f"③ 名字没点名任何接口，却点名了 {点名3} ⇒ 会误伤无关测试")

    # ④ 只认**完整**接口名：核心名 `复用` 命中 `判据_复用` ⇒ 必须不成立
    点名4, _, _ = 见证缺口(根, "复用链_代表元升入当前代", ("判据_",))
    if 点名4:
        fails.append(f"④ 按核心名匹配了 {点名4} ⇒ 误伤 `复用链_…` 这种无关测试")

    # ⑤ 找不到测试函数 ⇒ 必须判红
    _, 缺5, 找到了5 = 见证缺口(根, "判据_真_这个名字不存在", ("判据_",))
    if 找到了5:
        fails.append("⑤ 测试函数不存在却报「找到了」⇒「找不到就放行」")

    if fails:
        for x in fails:
            print(f"  ✗ 见证门 {x}")
        print(f"见证门 自检：失败 {len(fails)}")
        return 1
    print("见证门 自检：全绿（真调用判绿 · 只点名不调用判红 · 未点名不适用 · 只认完整名 · 找不到判红）")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if a and a[0] == "--selftest":
        return _selftest()
    if len(a) < 3:
        print("用法：见证门.py <测试名> <仓库根> <前缀1> [前缀2 …]")
        return 2
    测试名, 根 = a[0], Path(a[1])
    前缀们 = tuple(a[2:])
    if not 根.is_dir():
        print(f"【R91】仓库根不存在：{根}")
        return 2
    点名, 缺, 找到了 = 见证缺口(根, 测试名, 前缀们)
    if not 点名:
        print(f"【R91】`{测试名}` 名字里没点名任何接口 ⇒ **本条不适用**（零回退）")
        return 0
    if not 找到了:
        print(f"【R91】找不到测试函数 `{测试名}` ⇒ 门在跑一个**不存在**的东西")
        return 1
    if 缺:
        print(f"【R91】`{测试名}` 名字点名了 {点名}，"
              f"但**测试体里没调用** {缺} ⇒ 它见证不了自己名字说的东西")
        print("  ★ 判定口径与库内 WitnessExtractor 同一条：")
        print("    承诺「∃ 生产调用」的证明，**必须在证明树里携带那个调用点**。")
        print("  ★ 「测试通过了」是 Prop；「断言里调了它」是表里的数。")
        return 1
    print(f"【R91】`{测试名}` 点名 {点名}，测试体里**真的调了** ⇒ 有见证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
