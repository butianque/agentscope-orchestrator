# -*- coding: utf-8 -*-
"""阶段分离棘轮（#33）：A1/A2 的项目名词命中数**只许降，不许升**。

★ 为什么不是"判红就完了"：
  实测 A1 命中 **77 处**，其中约 12 处是**功能性泄漏**（代码行里把某个项目的目录名
  写死成 lean 根 / 库根），其余是注释里的事故叙事（「实测 xxx 桶 6,307 个文件」这类
  带真实读数的教训，把名字抹掉会连读数一起失去可核性）。
  ⇒ 一次性清零既不诚实（会逼人把读数改成空话），也不是本轮该做的事。
  但**放着不管**更坏：它会继续长。
  所以做成**棘轮**：记录基线，超过基线即红；低于基线则提示"可以把基线降下来"。
  规则一旦只降不升，乱写就被挡住了，而清理可以分批做。

★ 输出（机器可读，一行）：
    A1=<总命中>/<代码行命中> A2=<命中> 基线=<A1总>/<A1代码>/<A2> 判定=OK|超基线
★ 退出码：0 = 未超基线（含低于）；1 = 超基线；2 = 基线文件缺失（先跑一次 --写基线）
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

包根 = pathlib.Path(__file__).resolve().parents[1]
SRC = 包根 / "src" / "agentscope_orchestrator"
基线文件 = 包根 / "work" / "阶段分离基线.json"

# ★ 与 tests/phase_separation.py 的 PROJECT_NOUNS **必须一致**（口径 R117：同一个事实不许两处
#   各说一套）。这里不复制词表，而是从那个测试文件里读出来 —— 它才是权威。
def _词表() -> tuple[list[str], list[str]]:
    测试 = 包根 / "tests" / "phase_separation.py"
    源 = 测试.read_text(encoding="utf-8", errors="replace")
    import ast as _ast
    树 = _ast.parse(源)
    名词: list[str] = []
    标识: list[str] = []
    for 节 in 树.body:
        if isinstance(节, _ast.Assign) and len(节.targets) == 1:
            tgt = 节.targets[0]
            名 = getattr(tgt, "id", "")
            if 名 == "PROJECT_NOUNS":
                名词 = [e.value for e in 节.value.elts if isinstance(e, _ast.Constant)]
    # A2 的 bad_tokens 是函数内局部量，按源码里的字面量取（它也是唯一权威）
    import re
    命中 = re.search(r"bad_tokens\s*=\s*\[([^\]]*)\]", 源)
    if 命中:
        标识 = [x.strip().strip("\"'") for x in 命中.group(1).split(",") if x.strip()]
    return 名词, 标识


def _代码行集(py: pathlib.Path, 行数: int) -> set[int]:
    """该文件里**真是代码**的行号集（排除只有字符串/注释的行，含 docstring 内容）。

    ★ 为什么不能用 `line.split("#", 1)[0]`：那只能排 `#` 注释，排不掉 三引号。
      实测 36 条"代码行命中"里大半是文档字符串里的**读数叙事**（`kg.py:3800` 等），
      把散文当代码 ⇒ 既不敢清零、也看不出真泄漏（口径 311：假红比漏报更坏）。
    ★ 解析失败 ⇒ **全部算代码行**（宁可红，不许静默放过 —— R525）。
    """
    import io
    import tokenize

    _跳过 = {tokenize.STRING, tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
             tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}
    代码行: set[int] = set()
    try:
        源 = py.read_text(encoding="utf-8", errors="ignore")
        for tok in tokenize.generate_tokens(io.StringIO(源).readline):
            if tok.type in _跳过:
                continue
            for l in range(tok.start[0], tok.end[0] + 1):
                代码行.add(l)
    except (tokenize.TokenError, IndentationError, SyntaxError, OSError):
        return set(range(1, 行数 + 1))
    return 代码行


def 扫(名词: list[str], 标识: list[str]) -> dict:
    总 = 代码 = 文档 = 0
    明细: list[str] = []
    a2 = 0
    for py in sorted(SRC.glob("*.py")):
        行们 = py.read_text(encoding="utf-8", errors="ignore").splitlines()
        代码行 = _代码行集(py, len(行们))
        for i, line in enumerate(行们, 1):
            for n in 名词:
                if n in line:
                    总 += 1
                    if i in 代码行:
                        代码 += 1
                        明细.append(f"{py.name}:{i} [{n}]")
                    else:
                        文档 += 1
            for tok in 标识:
                if tok in line and "兼容" not in line and "alias" not in line.lower():
                    a2 += 1
                    break
    return {"A1总": 总, "A1代码": 代码, "A1文档": 文档, "A2": a2, "代码明细": 明细[:40]}


def main() -> int:
    名词, 标识 = _词表()
    if not 名词:
        print("   ✗ 读不到 PROJECT_NOUNS（测试文件结构变了）")
        return 2
    现 = 扫(名词, 标识)
    # ★ 守恒：A1总 必须等于 代码 + 文档（否则分档本身有洞）
    if 现["A1总"] != 现["A1代码"] + 现.get("A1文档", 0):
        print(f"A1分档**不平**：总 {现['A1总']} ≠ 代码 {现['A1代码']} + 文档 {现.get('A1文档')} 判定=超基线")
        return 1

    if "--写基线" in sys.argv:
        基线文件.parent.mkdir(parents=True, exist_ok=True)
        基线文件.write_text(json.dumps(
            {"A1总": 现["A1总"], "A1代码": 现["A1代码"], "A2": 现["A2"],
             "_说明": "只许降不许升。清理若干处之后手动跑 `python tools/阶段分离棘轮.py --写基线` 把它压下去。"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"A1={现['A1总']}/{现['A1代码']}(文档{现.get('A1文档',0)}) A2={现['A2']} 基线=已写入 {基线文件.name} 判定=OK")
        return 0

    if not 基线文件.is_file():
        print(f"   ✗ 基线文件不存在：{基线文件}（先跑 --写基线）")
        return 2
    基线 = json.loads(基线文件.read_text(encoding="utf-8"))
    ok, 降 = True, []
    for k in ("A1总", "A1代码", "A2"):
        if 现[k] > int(基线.get(k, 0)):
            ok = False
        elif 现[k] < int(基线.get(k, 0)):
            降.append(f"{k} {基线[k]}→{现[k]}")
    # ★ 独立记号：真泄漏（代码行命中的项目名）必须为 0；文档里的读数叙事不算泄漏。
    print(f"代码行泄漏={现['A1代码']} · 文档叙事={现.get('A1文档', 0)} · 合计={现['A1总']}")
    print(f"A1={现['A1总']}/{现['A1代码']} A2={现['A2']} "
          f"基线={基线.get('A1总')}/{基线.get('A1代码')}/{基线.get('A2')} "
          f"判定={'OK' if ok else '超基线'}")
    if 降:
        print(f"   ↓ 已低于基线：{'、'.join(降)} ⇒ 可以跑 `--写基线` 把它压下去（棘轮只降不升）")
    if not ok:
        print("   ⇒ **超基线**：新写的代码/注释又点项目名了。"
              "要么改用中性占位（`<key_prefix>`/「证明目录」/「仓根」），要么在基线文件里说明为什么必须加。")
        if 现["代码明细"]:
            print("   代码行命中（这些是**功能性**的，优先清）：")
            for x in 现["代码明细"]:
                print("     ·", x)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
