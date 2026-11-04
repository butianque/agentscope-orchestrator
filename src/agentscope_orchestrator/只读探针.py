#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只读探针 —— **把实现窗口想查的东西，由机器代它查好递过去**。

★★★★★ R93（2026-10-18）：

    真跑抓到的形态（不是推测）：窗口输出过

        I'll start by exploring the repository and reproducing the failing gates.

        <｜｜DSML｜｜ calls>
        <｜｜DSML｜｜ invoke name="bash">

    它**想跑 bash 去看仓库**。编排器的反应是认出「这不是内容」**拒收**（R32）。
    拒得对 —— 那次它把这段转录当源码写进了 `src/接线状态图.rs`，
    靠编译门判红才没进主树。**但拒完之后没给它一条正当的路。**

  ⇒ 所以准确说不是「完全剥夺了能力」，而是：
    **智能体保留了「想」的能力，被剥夺了「查」的能力。**

  ── 本件补什么 ──

  不给它 bash（那会把「产物只能是被机器门裁决的代码」这条破坏掉），
  而是**由机器代它查**：机器跑只读探针，把结果作为**上下文**递过去。

  这正是它想查的那几件事：

    · 「这个接口到底长什么样」   → `接口画像`：定义行 + 真签名
    · 「谁在调它、谁没调」       → `接口画像`：生产调用点 / 测试调用点
    · 「这个文件里有什么」       → `文件画像`：结构（函数/结构体 + 行号）
    · 「门为什么红了」           → `门失败摘要`：门自己的证据文本

  ── 为什么是"只读"而不是"给工具" ──

  ★ 一条硬边界：**产物必须只能是被机器门裁决的代码。**
    给它 bash 就等于给了它一条绕过门的侧信道（写文件、改环境、改门）。
  ⇒ 本件只做**读**，且读的结果只进 prompt、不进主树。
    窗口仍然只能交内容，由门裁决。

  ── 诚实边界 ──

    · 探针是**启发式**的（正则找调用点），不是语义分析。
      所以输出里必须带**行号**，让窗口自己去看真源码判断。
    · 探针**跑不动时如实返回空**，不假装「查过了、没问题」。
      （空结果与「查了发现没有」是两件事，混淆了就是假绿。）
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

__all__ = [
    "接口画像", "文件画像", "门失败摘要", "探针包",
    "源文件们", "剥注释字面量",
]

_跳过目录 = frozenset({
    "target", ".git", "node_modules", ".venv", "__pycache__",
    "vendor", "dist", "build", ".data", ".cargo_home", "备份", "backup",
    # ★ R96：**编排器自己的草稿区与产物区**。
    #   `work/task/rN/` 是模型本轮刚写的草稿，`products/` 是交付暂存。
    #   它们**不是本项目自己的源码** ⇒ 不得被算成「生产消费者」。
    #   ★ 真跑证据：`接线门` 曾把 6 处草稿引用当成生产引用 ⇒ 门恒绿
    #     （详见 `接线门.数生产引用` 的 R96 段注释）。
    #   ★ 这里补的是**非 git 回退路径**的防线：git 路径已由 `git ls-files`
    #     天然排除（它们未跟踪）。两条路都得对，否则换个环境就复发。
    "work", "products",
})

# 剥注释/字面量：**长度保持**（换成空格），否则行号会漂
_剥RE = re.compile(
    r'//[^\n]*'
    r'|/\*.*?\*/'
    r'|"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'",
    re.S,
)


def 剥注释字面量(文: str) -> str:
    """把注释与字面量换成等长空格（**行号不漂**）。"""
    return _剥RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), 文)


def 源文件们(根: Path, 后缀: str = "*.rs") -> list[Path]:
    """本项目**自己的**源文件。

    ★ 用 `git ls-files` 而不是 `rglob`（实测换来的）：
      `示例工程` 下 rglob 扫到 15,576 个 `.rs` / 553 MB，真源码只有 484 个；
      多出来的是 `.cargo_home`（依赖源码）与 `备份`（**旧版本定义**）。
      ⇒ 备份目录里的旧定义会让机器把「早已删掉的接口」当成有效声明
        —— **这不只是慢，是判据的域错了**。
    """
    根 = Path(根)
    try:
        r = subprocess.run(["git", "-C", str(根), "ls-files", 后缀],
                           capture_output=True, timeout=120)
        if r.returncode == 0:
            名们 = (r.stdout or b"").decode("utf-8", "replace").split()
            出 = [根 / x for x in 名们 if x.strip()]
            if 出:
                return [p for p in 出 if p.is_file()]
    except Exception:
        pass
    出 = []
    for p in 根.rglob(后缀):
        if any(part in _跳过目录 for part in p.parts):
            continue
        if p.is_file():
            出.append(p)
    return 出


def _测试区间(净: str) -> list[tuple[int, int]]:
    """`#[cfg(test)]` 之后那个 mod 的花括号区间（字符下标）。"""
    区 = []
    for m in re.finditer(r"#\[cfg\(test\)\]", 净):
        i = 净.find("{", m.end())
        if i < 0:
            continue
        深 = 0
        for j in range(i, len(净)):
            c = 净[j]
            if c == "{":
                深 += 1
            elif c == "}":
                深 -= 1
                if 深 == 0:
                    区.append((m.start(), j + 1))
                    break
    return 区


def _在区间内(位置: int, 区: list[tuple[int, int]]) -> bool:
    return any(a <= 位置 < b for a, b in 区)


def 接口画像(根: Path, 接口名: str, 后缀: str = "*.rs",
             上限: int = 8) -> str:
    """一个接口的**真实画像**：定义在哪、生产路径谁调了、测试里谁调了。

    ★ 为什么这三样必须分开报：
      「测试里调了」与「生产路径调了」是**两件完全不同的事** ——
      前者只证明函数能被调用，后者才证明它参与裁决。
      真跑里模型正是分不清这两件事（它起过一条门叫
      `新测试_判据_甲子生产路径生效`，断言只是「文件里有这个词」）。
    """
    根 = Path(根)
    名 = str(接口名 or "").strip()
    if not 名:
        return ""
    定义: list[str] = []
    生产: list[str] = []
    测试: list[str] = []
    # ★ 只认**完整名字**，不认核心名（`判据_复用` 的核心 `复用`
    #   会命中 `复用链_代表元升入当前代` 这种完全无关的东西）。
    词界 = re.compile(r"(?<![\w\u4e00-\u9fff])" + re.escape(名)
                      + r"(?![\w\u4e00-\u9fff])")
    定义RE = re.compile(r"\b(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?"
                        r"(?:fn|struct|enum|trait|type|const|static|mod)\s+"
                        + re.escape(名) + r"\b")
    for p in 源文件们(根, 后缀):
        try:
            文 = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if 名 not in 文:
            continue
        净 = 剥注释字面量(文)
        区 = _测试区间(净)
        rel = p.relative_to(根).as_posix()
        原行 = 文.splitlines()
        for i, L in enumerate(净.splitlines(), 1):
            if not 词界.search(L):
                continue
            原文行 = 原行[i - 1].strip() if i - 1 < len(原行) else ""
            # 定义行
            if 定义RE.search(L) and len(定义) < 上限:
                定义.append(f"{rel}:{i}  {原文行[:150]}")
                continue
            # 调用点（按测试/生产分流）
            位置 = sum(len(x) + 1 for x in 净.splitlines()[:i - 1])
            类 = "测试" if _在区间内(位置, 区) else "生产"
            if 类 == "测试":
                if len(测试) < 上限:
                    测试.append(f"{rel}:{i}  {原文行[:150]}")
            else:
                if len(生产) < 上限:
                    生产.append(f"{rel}:{i}  {原文行[:150]}")
    段 = [f"【接口画像】`{名}`（机器只读探针实测，不是推测）"]
    段.append("· 定义：")
    段.append("\n".join("    " + x for x in 定义) if 定义 else "    （**没有找到定义**）")
    段.append(f"· 生产路径调用点：{len(生产)} 处")
    段.append("\n".join("    " + x for x in 生产) if 生产 else
              "    ★ **零处** —— 它是 `pub` 承诺，但没有生产消费者 ⇒ 死代码")
    段.append(f"· 测试里调用点：{len(测试)} 处")
    段.append("\n".join("    " + x for x in 测试) if 测试 else "    （零处）")
    段.append("  ★ 「测试里调了」**不等于**「生产路径调了」："
              "前者只证明函数能被调用，后者才证明它参与裁决。")
    return "\n".join(段)


def 文件画像(根: Path, 相对: str, 后缀: str = "*.rs",
             上限: int = 60) -> str:
    """一个文件的结构：顶层项 + 行号。让窗口知道**里面有什么、在哪**。"""
    根 = Path(根)
    p = (Path(相对) if Path(相对).is_absolute() else Path(根) / 相对)
    if not p.is_file():
        return f"【文件画像】{相对}：**文件不存在**"
    try:
        文 = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"【文件画像】{相对}：读不了（{exc}）"
    净 = 剥注释字面量(文)
    项RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?"
                      r"(fn|struct|enum|trait|type|const|static|mod|impl)\b")
    行 = []
    for i, L in enumerate(净.splitlines(), 1):
        m = 项RE.match(L)
        if m and len(行) < 上限:
            原文行 = 文.splitlines()[i - 1].strip() if i - 1 < len(文.splitlines()) else ""
            行.append(f"{i:6d}  {原文行[:120]}")
    头 = f"【文件画像】{相对}（{len(文)} 字符，{len(文.splitlines())} 行）"
    if not 行:
        return 头 + "\n    （没扫到顶层项）"
    return 头 + "\n" + "\n".join("    " + x for x in 行)


def 门失败摘要(审计: list, 文件: str = "", 上限: int = 4) -> str:
    """把红门自己的证据文本抽出来 —— **窗口最想知道的就是这个**。

    ★ 为什么必须带门的**原文**而不是一句「门红了」：
      「门红了」不提供任何改动方向；门自己的输出里通常**直接带文件与行号**。
      真跑实测：规划器盲猜文件猜错了**全部**（R33 就是这么修的）。
    """
    红 = []
    for a in (审计 or []):
        if not isinstance(a, dict):
            continue
        if a.get("ok"):
            continue
        if 文件 and a.get("file") and a.get("file") != 文件:
            continue
        红.append(a)
        if len(红) >= 上限:
            break
    if not 红:
        return ""
    段 = ["【红门证据】（机器实测原文，请据此定位改动点）"]
    for a in 红:
        因 = str(a.get("cause") or "")
        证 = str(a.get("evidence") or a.get("detail") or a.get("输出") or "")
        段.append(f"· 门 `{a.get('gate')}`（文件 {a.get('file') or '?'}）"
                  + (f" 因由 {因}" if 因 else ""))
        if 证:
            段.append("    " + 证.replace("\n", "\n    ")[:1800])
    return "\n".join(段)


def 探针包(根: Path, 目标: str, 相对: str, 审计: list | None = None,
           接口前缀: tuple[str, ...] = (), 后缀: str = "*.rs") -> str:
    """把窗口**会想查的几件事**一次查好，拼成一段可直接进 prompt 的上下文。

    ★ 为什么合成一个函数：三个探针必须**口径一致**（同一个源文件域、
      同一个"只认完整名"规则）。分开调用就会出现「接口画像说零调用，
      文件画像里却有那个调用点」这种自相矛盾 —— R35 的教训。
    """
    段: list[str] = []
    文 = str(目标 or "")
    for 前缀 in 接口前缀 or ():
        for m in re.finditer(re.escape(前缀) + r"[\w\u4e00-\u9fff]+", 文):
            名 = m.group(0)
            x = 接口画像(根, 名, 后缀)
            if x and "没有找到定义" not in x:
                段.append(x)
            break                      # 每个前缀只查第一个点名
    段.append(文件画像(根, 相对, 后缀))
    if 审计:
        y = 门失败摘要(审计, 文件=相对)
        if y:
            段.append(y)
    return "\n\n".join(x for x in 段 if x)


# ══════════════════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════════════════

def _selftest() -> int:
    import tempfile
    fails: list[str] = []
    根 = Path(tempfile.mkdtemp())
    (根 / "a.rs").write_text(
        "pub fn 判据_甲子(x: u32) -> u32 { x }\n"
        "pub fn 判据_复用(x: u32) -> u32 { x }\n"
        "// 判据_甲子 在注释里被提到\n"
        "pub fn 用() -> u32 { 判据_甲子(7) }\n"
        "#[cfg(test)]\n"
        "mod 测试 {\n"
        "    fn t() { let _s = \"}\"; assert_eq!(判据_复用(1), 1); }\n"
        "}\n",
        encoding="utf-8")
    (根 / "b.rs").write_text(
        "pub fn 复用链_代表元升入当前代() {}\n", encoding="utf-8")

    # ① 生产调用点与测试调用点必须**分开报**
    x = 接口画像(根, "判据_甲子")
    if "生产路径调用点：1 处" not in x:
        fails.append(f"① 生产调用点没数对：\n{x}")
    if "测试里调用点：0 处" not in x:
        fails.append("① 测试调用点没分开报 ⇒ 会与生产混为一谈")

    # ② 零生产消费者必须**明说**（这是缺口的核心形态）
    y = 接口画像(根, "判据_复用")
    if "零处" not in y:
        fails.append("② 零生产消费者时没明说 ⇒ 窗口看不出这是缺口")

    # ③ 注释里的提及**不算**调用点（剥注释必须生效）
    if "注释里被提到" in x:
        fails.append("③ 注释里的提及被算进画像 ⇒ 窗口会被误导")

    # ④ 字符串里的 `}` 不得让测试区间提前闭合（剥字面量必须生效）
    #   ★ 若先平衡花括号再剥字面量，`"}"` 会让 mod 提前闭合
    #     ⇒ `判据_复用` 那次调用会被算成「生产」。
    if "生产路径调用点：0 处" not in y:
        fails.append("④ 字符串里的 `}` 让测试区间提前闭合 ⇒ "
                     "测试调用被误算成生产调用（剥净顺序错）")

    # ⑤ 只认**完整名**：`复用` 的核心名不得命中 `复用链_代表元升入当前代`
    z = 接口画像(根, "复用链_代表元升入当前代")
    if "判据_复用" in z:
        fails.append("⑤ 核心名串到别的接口上 ⇒ 画像会张冠李戴")

    # ⑥ 文件画像带**行号**（否则窗口还是不知道在哪）
    w = 文件画像(根, "a.rs")
    if "判据_甲子" not in w or not re.search(r"^\s+\d+", w, re.MULTILINE):
        fails.append("⑥ 文件画像没有行号 ⇒ 窗口还是定位不了")

    # ⑦ 文件不存在 ⇒ 如实说，不假装
    if "不存在" not in 文件画像(根, "nope.rs"):
        fails.append("⑦ 文件不存在时没如实报")

    # ⑧ 红门证据：只取**红**的、且只取本文件的
    aud = [{"gate": "g1", "file": "a.rs", "ok": False, "cause": "c1",
            "evidence": "行 12: 未接上"},
           {"gate": "g2", "file": "a.rs", "ok": True, "cause": "c2"},
           {"gate": "g3", "file": "b.rs", "ok": False, "cause": "c3"}]
    e = 门失败摘要(aud, 文件="a.rs")
    if "g1" not in e or "未接上" not in e:
        fails.append("⑧ 红门证据没带门的原文 ⇒ 窗口拿不到改动方向")
    if "g2" in e:
        fails.append("⑧b 绿门被当成红门报 ⇒ 窗口会被误导去改不该改的地方")
    if "g3" in e:
        fails.append("⑧c 别的文件的红门被算进来 ⇒ 窗口去改不归它的文件")

    # ⑨ 探针包：接口前缀点名了就查，且**不重复**
    pk = 探针包(根, "把 判据_甲子 接上生产路径", "a.rs", aud,
                接口前缀=("判据_",))
    if "接口画像" not in pk or "文件画像" not in pk or "红门证据" not in pk:
        fails.append(f"⑨ 探针包没把三样拼齐：\n{pk[:400]}")

    # ⑩ 没有点名接口时不硬凑（零回退）
    pk2 = 探针包(根, "随便改点什么", "a.rs", None, 接口前缀=("判据_",))
    if "接口画像" in pk2:
        fails.append("⑩ 目标没点名接口却硬凑了接口画像 ⇒ 空画像会误导窗口")

    if fails:
        for x2 in fails:
            print(f"  ✗ 只读探针 {x2}")
        print(f"只读探针 自检：失败 {len(fails)}")
        return 1
    print("只读探针 自检：全绿（生产/测试分流 · 零消费者明说 · 剥净顺序 · "
          "只认完整名 · 红门只取本文件 · 零回退）")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if a and a[0] == "--selftest":
        return _selftest()
    if len(a) >= 3 and a[0] == "--接口":
        print(接口画像(Path(a[1]), a[2]))
        return 0
    if len(a) >= 3 and a[0] == "--文件":
        print(文件画像(Path(a[1]), a[2]))
        return 0
    print("用法：只读探针.py --selftest | --接口 <根> <接口名> | --文件 <根> <相对路径>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
