"""代码 → 角色网格：结构真值的表示层（零常识、零依赖、可机验）。

★ 分工（这是与"模型层"的边界）
  本层只做**不需要常识**的事：把源码变成"结构"，丢掉一切名字与文字内容。
  需要常识/代码知识的判断（这段改动对不对、哪个文件相关）**不在这里做** ——
  那是模型（API）的事。本层只负责让模型看到**结构上必然相关**的东西。

★ 为什么色是"秩"而不是"名字"
  源实现的色是值的**保序秩**（`色秩`），不是色名。于是：
    换标识符名 / 换字符串内容 ⇒ 网格逐格相同 ⇒ 同一身份。
  这正是 TS-778 的口径在代码上的对应：**换编码/换命名不变，换结构必变**。

★ 编码约定
  · 每行 = 网格一行；**只保留有 token 的行**（空行与纯注释行不进网格）⇒ 加注释不改变身份；
  · 行首缩进写成 `缩进` 色格，格数 = 缩进层级（层级 = 前导空白 ÷ 本文件缩进单位）
    ⇒ 改缩进宽度（2↔4 空格）不改变身份，改嵌套层级必然改变；
  · 每个 token 占**一格**（不看长度）⇒ 改名长度变化不影响；
  · 行尾不足处补背景，成矩形。

★ 已知限制（诚实登记，不用"以后再补"糊过去）
  · 不解析块注释 `/* */`（v1 只认行注释）；块注释里的内容会当代码算；
  · 缩进单位取"本文件出现的最小正缩进"，制表符 `\\t` 记 1 格；
  · 只到"词法形状"层：语义等价但形状不同的代码（如 for↔while 改写）**判为不同** ——
    这是刻意的：本层不做语义判断，语义判断归模型。
"""

from __future__ import annotations

import re

from .mem_grid import 结构真值, 结构像

背景 = 0
缩进 = 1
标识 = 2
数字 = 3
字符串 = 4
运算符 = 5
括号 = 6
_关键字基 = 10          # 关键字各自一色：10 + 关键字表下标

# 关键字表：按**固定顺序**，保证同一语言内跨文件同色
_关键字表: dict[str, tuple[str, ...]] = {
    "python": (
        "def", "class", "return", "if", "elif", "else", "for", "while", "in",
        "not", "and", "or", "import", "from", "as", "try", "except", "finally",
        "raise", "with", "lambda", "yield", "async", "await", "pass", "break",
        "continue", "global", "nonlocal", "assert", "del", "is",
    ),
    "rust": (
        "fn", "let", "mut", "pub", "impl", "trait", "struct", "enum", "use",
        "mod", "crate", "self", "Self", "return", "if", "else", "match", "for",
        "while", "loop", "in", "where", "as", "const", "static", "unsafe",
        "async", "await", "dyn", "ref", "move", "break", "continue",
    ),
    "lean": (
        "theorem", "lemma", "def", "structure", "class", "instance", "inductive",
        "axiom", "import", "open", "namespace", "section", "variable", "example",
        "by", "have", "show", "from", "exact", "apply", "intro", "rw", "simp",
        "at", "if", "then", "else", "match", "with", "let", "fun", "forall",
    ),
}
_关键字表["generic"] = tuple(sorted({
    *_关键字表["python"], *_关键字表["rust"], *_关键字表["lean"],
}))

_规则 = re.compile(
    r"""
     (?P<注释>\#[^\n]*|//[^\n]*)
    |(?P<字符串>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
    |(?P<数字>\d+(?:\.\d+)?)
    |(?P<标识>[^\W\d]\w*)
    |(?P<运算符>[+\-*/%=<>!&|^~]+)
    |(?P<括号>[(){}\[\],;:.])
    |(?P<空白>\s+)
    """,
    re.X,
)


def 代码转网格(源: str, 语言: str = "generic") -> tuple[list[list[int]], dict[int, str]]:
    """源码 → (角色网格, 色名映射)。色名映射只用于排错，不参与身份。"""
    关键字 = _关键字表.get(语言) or _关键字表["generic"]
    色名 = {背景: "空白", 缩进: "缩进", 标识: "标识", 数字: "数字",
            字符串: "字符串", 运算符: "运算符", 括号: "括号"}
    for i, k in enumerate(关键字):
        色名[_关键字基 + i] = f"kw:{k}"

    行们: list[list[int]] = []
    缩进们: list[int] = []
    for 原行 in 源.splitlines():
        格: list[int] = []
        前导 = 0
        见首token = False
        for m in _规则.finditer(原行):
            类 = m.lastgroup
            if 类 == "空白":
                if not 见首token:
                    前导 += len(m.group())
                continue
            if 类 == "注释":
                break                                  # 行注释：丢弃到行尾
            见首token = True
            if 类 == "标识":
                w = m.group()
                格.append(_关键字基 + 关键字.index(w) if w in 关键字 else 标识)
            elif 类 == "数字":
                格.append(数字)
            elif 类 == "字符串":
                格.append(字符串)
            elif 类 == "运算符":
                格.append(运算符)
            elif 类 == "括号":
                格.append(括号)
        if not 格:
            continue                                   # 空行/纯注释行：不进网格
        行们.append(格)
        缩进们.append(前导)

    单位 = min([x for x in 缩进们 if x > 0], default=1)
    网格: list[list[int]] = []
    for 格, 前导 in zip(行们, 缩进们):
        层 = 前导 // 单位 if 单位 else 0
        网格.append([缩进] * 层 + 格)
    宽 = max((len(r) for r in 网格), default=0)
    for r in 网格:
        r.extend([背景] * (宽 - len(r)))
    return 网格, 色名


def 代码结构(源: str, 语言: str = "generic") -> 结构真值:
    """源码 → 结构真值（身份 = 它的指纹）。

    ★ 用**绝对色秩**（`{v: v}`）：让 `for` 与 `while` 这类关键字互相可区分。
      默认的"在场名次"口径会把它们压成同一个秩 ⇒ 实测同指纹（这就是这个参数的由来）。
    """
    网格, 色名 = 代码转网格(源, 语言)
    return 结构像(网格, 色秩表={v: v for v in 色名})


# ════════════════════════════════════════════════════════════
# 自检：每条判据都是"同/不同"，没有一个阈值
# ════════════════════════════════════════════════════════════

def _自检() -> int:
    失败: list[str] = []

    def 判(名: str, 条件: bool, 说明: str = "") -> None:
        if not 条件:
            失败.append(f"{名}{('：' + 说明) if 说明 else ''}")

    甲 = "def 甲(乙):\n    return 乙 + 1\n"
    乙 = "def 丙(丁):\n    return 丁 + 1\n"
    判("★ 改名不变", 代码结构(甲, "python").指纹() == 代码结构(乙, "python").指纹(),
       "换函数名/参数名后身份必须相同")

    判("★ 改名到不同长度不变",
       代码结构(甲, "python").指纹()
       == 代码结构("def 很长的函数名字(某个很长的参数名):\n    return 某个很长的参数名 + 1\n",
                 "python").指纹())

    判("★ 行尾注释不变",
       代码结构(甲, "python").指纹()
       == 代码结构("def 甲(乙):  # 这是注释\n    return 乙 + 1  # 尾注\n", "python").指纹())

    判("★ 独立注释行不变",
       代码结构(甲, "python").指纹()
       == 代码结构("# 文件头注释\n\ndef 甲(乙):\n    # 体内注释\n    return 乙 + 1\n",
                 "python").指纹())

    判("★ 缩进宽度 2↔4 不变",
       代码结构("def f(a):\n  if a:\n    return 1\n", "python").指纹()
       == 代码结构("def f(a):\n    if a:\n        return 1\n", "python").指纹())

    判("★ 字符串内容不影响",
       代码结构('x = "abc"\n').指纹() == 代码结构('x = "完全不同的内容"\n').指纹())

    判("★★ 加一层块则必变",
       代码结构("def f(a):\n    return 1\n", "python").指纹()
       != 代码结构("def f(a):\n    if a:\n        return 1\n", "python").指纹())

    判("★★ 换控制结构则必变",
       代码结构("for x in y:\n    f(x)\n", "python").指纹()
       != 代码结构("while x in y:\n    f(x)\n", "python").指纹())

    判("★★ 改名不掩盖结构差异（与加块交叉）",
       代码结构("def f(a):\n    return 1\n", "python").指纹()
       != 代码结构("def g(b):\n    return b\n", "python").指纹(),
       "返回字面量 vs 返回参数：形状不同")

    # 确定性 + 结构流往返
    真 = 代码结构(甲, "python")
    判("确定性", 代码结构(甲, "python").指纹() == 真.指纹())
    from .mem_grid import 结构流, 解结构流
    判("落盘往返逐位一致", 解结构流(结构流(真)) == 真)

    # 语法着色覆盖：确认关键字真的被当关键字（而非标识符）
    网, 色名 = 代码转网格("def f(x):\n    return x\n", "python")
    值集 = {v for r in 网 for v in r}
    判("关键字与标识符分色",
       any(v >= _关键字基 for v in 值集) and 标识 in 值集,
       f"网格色集 {sorted(值集)}")

    # 规模读数（不是判据，只报数）
    大 = "\n".join(f"def f{i}(x):\n    return x + {i}" for i in range(200))
    真大 = 代码结构(大, "python")

    for f in 失败:
        print(f"  ✗ {f}")
    if not 失败:
        print("  ✓ 代码结构自检全绿")
        print(f"    小样：原子 {真.原子数} · 洞 {真.洞总数} · 指纹 {真.指纹()[:12]}")
        print(f"    200 函数：原子 {真大.原子数} · 洞 {真大.洞总数} · 网格高 {真大.高}")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(_自检())
