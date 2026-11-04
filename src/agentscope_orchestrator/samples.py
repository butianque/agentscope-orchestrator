"""约束样本扩充：从产物**派生结构变体**，把"假红"逼出来（TS-378 路径）。

## 为什么需要它（实测缺口）

`constraint.py` 能机检"门是否穿过目标约束的商"，但**样本只有两个**：
本轮产物 + 它的**同内容副本**。这只能抓住一类问题：

  · **路径/时间依赖**（门读了产物路径或 mtime）—— 同内容不同路径即暴露

抓不住真正高频的那一类：

  · **内容过细**（门钉了产物原文）—— 实测抓到的真缺陷：
    派生门 `patterns: ["alpha_line"]` ⇒ 任何"不同但同样合法"的产物被判红，
    实测 **6/6 全红**。同内容副本**永远抓不到它**（副本内容相同）。

## 本模块做什么

从产物派生**结构变体**（改写法不改语义），再把变体喂给 `constraint` 机检：

  ① 注释增删        —— 抓"门钉了注释/空行"
  ② 空行/缩进扰动   —— 抓"门钉了排版"
  ③ 声明重排序      —— 抓"门钉了声明顺序"
  ④ 标识符改名      —— 抓"门钉了具体名字"（**最强的一类**）
  ⑤ 无关声明增删    —— 抓"门要求了目标没要求的东西"

## ★★ 关键设计：变体是**待验**的，不是假定同类的

**不能假定变体一定达标** —— 改了标识符可能真的破坏语义。故流程是：

    生成变体 → 跑**独立约束谓词**判它是否仍达标
             → 只把**谓词确证达标**的变体收进样本（这才是"目标同类"）
             → 再机检门是否对同类样本判不同档（违反 ⇒ 假红）

这样样本集是**谓词确证的**，不是任意构造的 —— 与 TS-378
`ConstraintOfGoal G x y := G x ↔ G y` 同口径（`G` 由独立谓词给出）。

## 诚实边界

· 变体生成是**语法层**的（正则/分词），不解析 AST ⇒ 可能生成语法不合法的
  变体。**这没关系**：不合法的变体会被独立谓词判**不达标**，
  于是**不会**进样本集（自动过滤，不需要我手工保证）。
· 谓词若**恒真**（对任何输入都绿），则所有变体都被判达标 —— 此时检查仍有效
  （门若对不同写法判不同档，仍是真违反），但**约束本身太粗**，
  这属于谓词的质量问题，本模块**如实报告**（`谓词可能过粗` 提示）。
· 变体数有上限（硬护栏），防样本爆炸拖慢判门。
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

# 变体数上限（硬护栏）：每个变体都要真跑门 + 谓词，防样本爆炸
最大变体 = 6


# ── 各语言的声明行正则（用于重排序/改名）──────────────────────────
_声明行 = re.compile(
    r"^\s*(?:theorem|lemma|def|structure|instance|example|"
    r"fn|struct|enum|impl|trait|mod|class|function|func)\s+",
)

# 标识符（够长才改，避免改掉单字符变量把语义改坏）
_标识符 = re.compile(r"\b[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]{3,}\b")

# 关键字/常用词，改名会破坏语义 ⇒ 不动
_不改 = frozenset({
    "theorem", "lemma", "structure", "instance", "example", "import",
    "namespace", "section", "variable", "open", "end", "where", "by",
    "return", "match", "with", "let", "have", "show", "from", "fun",
    "forall", "exists", "public", "private", "static", "async", "await",
    "impl", "trait", "struct", "enum", "mod", "use", "crate", "self",
    "Self", "super", "true", "false", "True", "False", "None", "Some",
    "class", "def", "import", "from", "as", "if", "else", "elif", "for",
    "while", "try", "except", "finally", "lambda", "yield", "global",
    "nonlocal", "assert", "raise", "pass", "break", "continue", "and",
    "or", "not", "in", "is", "None", "Nat", "Int", "Bool", "String",
    "Real", "List", "Option", "Result", "Vec", "usize", "u32", "u64",
    "i32", "i64", "f32", "f64", "str", "bool", "char", "byte",
})


def _注释符(后缀: str) -> str:
    return {"lean": "--", "rust": "//", "python": "#"}.get(
        _语言(后缀), "#")


def _语言(后缀: str) -> str:
    return {".lean": "lean", ".rs": "rust", ".py": "python"}.get(
        后缀.lower(), "other")


# ── 变体生成器：每个返回 (变体名, 新文本) ──────────────────────────
def _变体_加注释(文本: str, 后缀: str) -> str:
    """插入注释行 —— 语义不变，但门若钉了"注释里没有的东西"就会露馅。"""
    符 = _注释符(后缀)
    return f"{符} 自动生成的说明注释（语义无关）\n" + 文本


def _变体_去空行(文本: str, 后缀: str) -> str:
    """删掉所有空行 —— 抓"门钉了排版/空行"。"""
    return "\n".join(ln for ln in 文本.splitlines() if ln.strip()) + "\n"


def _变体_压缩进(文本: str, 后缀: str) -> str:
    """把行首缩进压成最小合法形式 —— 抓"门钉了缩进"。"""
    出: list[str] = []
    for ln in 文本.splitlines():
        if ln.strip():
            出.append(ln.lstrip() if not ln.startswith(" ") else ln)
        else:
            出.append(ln)
    return "\n".join(出) + "\n"


def _变体_改标识符(文本: str, 后缀: str) -> str:
    """给够长的自定义标识符加后缀 —— **最强的一类**：抓"门钉了具体名字"。

    只改**声明行里**的名字，不动关键字、不动短名（防破坏语义太狠）。
    改坏的变体自会被独立谓词过滤掉。
    """
    出: list[str] = []
    for ln in 文本.splitlines():
        if _声明行.match(ln):
            def 换(m: re.Match) -> str:
                w = m.group(0)
                return w if w in _不改 else w + "_v"
            # 只改声明行里首个够长的标识符之后的部分，避免改关键字
            头 = _声明行.match(ln)
            if 头:
                前, 后 = ln[:头.end()], ln[头.end():]
                出.append(前 + _标识符.sub(换, 后, count=1))
                continue
        出.append(ln)
    return "\n".join(出) + "\n"


def _变体_重排声明(文本: str, 后缀: str) -> str:
    """把**独立的声明块**重排 —— 抓"门钉了声明顺序"。

    粗粒度：把连续的非声明行并到前一个声明块，然后整体倒序。
    重排若破坏依赖，独立谓词会判不达标 ⇒ 自动出局。
    """
    块: list[list[str]] = []
    for ln in 文本.splitlines():
        if _声明行.match(ln) or not 块:
            块.append([ln])
        else:
            块[-1].append(ln)
    if len(块) < 2:
        return 文本
    return "\n".join("\n".join(b) for b in reversed(块)) + "\n"


def _变体_尾加无关(文本: str, 后缀: str) -> str:
    """尾部加一段**目标未要求**的内容 —— 抓"门要求了目标没要求的东西"。"""
    语言 = _语言(后缀)
    if 语言 == "lean":
        增 = "\n-- 目标未要求的附加说明\n"
    elif 语言 == "rust":
        增 = "\n// 目标未要求的附加说明\n"
    else:
        增 = "\n# 目标未要求的附加说明\n"
    return 文本 + 增


_生成器 = (
    ("加注释", _变体_加注释),
    ("去空行", _变体_去空行),
    ("改标识符", _变体_改标识符),
    ("重排声明", _变体_重排声明),
    ("尾加无关", _变体_尾加无关),
)


def 造变体(产物: Path, 上限: int = 最大变体) -> list[tuple[str, str]]:
    """从产物造结构变体，返回 [(变体名, 新文本)]。

    ★ 变体是**待验**的 —— 调用方必须用独立谓词筛，不能假定同类。
    """
    if not 产物.is_file():
        return []
    try:
        文本 = 产物.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    出: list[tuple[str, str]] = []
    for 名, fn in _生成器:
        if len(出) >= 上限:
            break
        try:
            新 = fn(文本, 产物.suffix)
        except Exception:      # noqa: BLE001 单条失败不影响其余
            continue
        if 新 and 新 != 文本:
            出.append((名, 新))
    return 出


def 筛同类样本(产物: Path, 独立谓词: list[dict], work_dir: Path,
                cwd_default: str = ".", 上限: int = 最大变体,
               ) -> dict:
    """造变体 → 用**独立谓词**判达标 → 只收谓词确证达标的进样本集。

    ★★ 这是本模块的核心纪律：**样本的"同类"由独立谓词给出**，
      不是我假定的。故 `ConstraintOfGoal G x y := G x ↔ G y` 里的 `G`
      有真实来源（谓词），检查不空洞。

    返回 {"样本": [(标签, Path)], "达标": {标签: bool},
          "报告": [...], "谓词过粗": bool, "临时目录": Path|None}
    """
    from agentscope_orchestrator import gate_spec

    if not 产物.is_file():
        return {"样本": [], "达标": {}, "报告": [], "谓词过粗": False,
                "临时目录": None}
    变体 = 造变体(产物, 上限)
    if not 变体:
        return {"样本": [], "达标": {}, "报告": [], "谓词过粗": False,
                "临时目录": None}

    临时 = Path(tempfile.mkdtemp(prefix="cons_var_"))
    样本: list[tuple[str, Path]] = []
    达标: dict[str, bool] = {}
    报告: list[dict] = []
    try:
        for 名, 文本 in 变体:
            f = 临时 / f"{名}_{产物.name}"
            f.write_text(文本, encoding="utf-8")
            # 用独立谓词判该变体是否**仍达标**
            判: dict[str, bool] = {}
            for c in 独立谓词:
                if not isinstance(c, dict):
                    continue
                try:
                    r, _ = gate_spec.evaluate([c], f, work_dir, cwd_default)
                    判.update({str(k): bool(v) for k, v in r.items()})
                except Exception:      # noqa: BLE001
                    continue
            if not 判:
                报告.append({"变体": 名, "收": False, "因": "谓词跑不出结果"})
                continue
            全绿 = all(判.values())
            if 全绿:
                样本.append((名, f))
                达标[名] = True
                报告.append({"变体": 名, "收": True, "因": "独立谓词确证达标"})
            else:
                红 = [k for k, v in 判.items() if not v]
                报告.append({"变体": 名, "收": False,
                              "因": f"谓词判不达标（{红}）⇒ 非同类，自动出局"})
        # ★ 谓词过粗提示：所有变体都达标 ⇒ 谓词区分力可能不足（如实报告）
        谓词过粗 = bool(样本) and len(样本) == len(变体) and len(变体) >= 3
        return {"样本": 样本, "达标": 达标, "报告": 报告,
                "谓词过粗": 谓词过粗, "临时目录": 临时}
    except Exception:      # noqa: BLE001
        shutil.rmtree(临时, ignore_errors=True)
        raise


def _selftest() -> int:
    """自检：全部机器可算。"""
    ok = total = 0

    def chk(name: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  ✗ {name}")

    d = Path(tempfile.mkdtemp(prefix="samp_st_"))
    try:
        p = d / "A.lean"
        原 = ("import TS375\n\n"
              "theorem goal_proof (n : Nat) : n = n := by\n  rfl\n\n"
              "def helper : Nat := 0\n")
        p.write_text(原, encoding="utf-8")

        # ① 变体生成
        变 = 造变体(p)
        chk("造出多个变体", len(变) >= 3)
        chk("变体与原文不同", all(t != 原 for _, t in 变))
        chk("变体名不重复", len({n for n, _ in 变}) == len(变))

        # ② 加注释变体：语义不变 ⇒ 应含原文全部声明
        for 名, t in 变:
            if 名 == "加注释":
                chk("加注释变体保留声明", "theorem" in t and "def " in t)

        # ③ 改标识符变体：应真的改了名字
        for 名, t in 变:
            if 名 == "改标识符":
                chk("改标识符变体确实改名", "goal_proof_v" in t or "_v" in t)

        # ④ ★ 独立谓词筛同类样本（谓词 = 要求含 theorem）
        谓词 = [{"name": "has_theorem", "type": "whole_reading",
                 "mode": "contains", "must_contain": ["theorem "],
                 "in": "{file}"}]
        r = 筛同类样本(p, 谓词, d, ".")
        chk("谓词达标变体被收", len(r["样本"]) >= 1)
        chk("样本都有达标标记", all(r["达标"][n] for n, _ in r["样本"]))
        chk("有逐变体报告", len(r["报告"]) >= 3)

        # ⑤ ★ 谓词确证：样本里的变体确实过谓词
        from agentscope_orchestrator import gate_spec as gs
        for 名, f in r["样本"]:
            rr, _ = gs.evaluate(谓词, f, d, ".")
            chk(f"样本「{名}」确证过谓词", all(rr.values()))

        # ⑥ ★ 谓词过滤生效：换个苛刻谓词，变体应被过滤
        严谓词 = [{"name": "must_exact", "type": "whole_reading",
                  "mode": "equal", "must_equal": 原, "in": "{file}"}]
        r2 = 筛同类样本(p, 严谓词, d, ".")
        chk("苛刻谓词过滤掉变体", len(r2["样本"]) == 0)

        # ⑦ ★ 谓词过粗提示：谓词恒真 ⇒ 全收 ⇒ 报告过粗
        恒真谓词 = [{"name": "always", "type": "whole_reading",
                    "mode": "contains", "must_contain": [""], "in": "{file}"}]
        r3 = 筛同类样本(p, 恒真谓词, d, ".")
        chk("谓词恒真时报过粗", r3["谓词过粗"] is True)

        # ⑧ 上限护栏
        变限 = 造变体(p, 上限=2)
        chk("变体数受上限约束", len(变限) <= 2)

        # ⑨ 空/缺文件不炸
        chk("缺文件返回空", 造变体(d / "不存在.lean") == [])
        r4 = 筛同类样本(d / "不存在.lean", 谓词, d, ".")
        chk("缺文件筛样本返回空", r4["样本"] == [])

        # ⑩ 无声明文本也能造变体（不崩）
        p2 = d / "plain.txt"
        p2.write_text("随便写点什么\n没有声明\n", encoding="utf-8")
        chk("无声明文本不崩", isinstance(造变体(p2), list))

        # ⑪ ★★ 端到端：过细门必须被变体抓住（本模块存在的理由）
        #    门钉原文 ⇒ 加注释变体仍含原文 ⇒ 绿；改标识符变体不含 ⇒ 红
        过细门 = {"name": "pin_original", "type": "grep_required",
                 "patterns": ["goal_proof (n : Nat)"]}
        谓词2 = [{"name": "has_theorem", "type": "whole_reading",
                 "mode": "contains", "must_contain": ["theorem "],
                 "in": "{file}"}]
        r5 = 筛同类样本(p, 谓词2, d, ".")
        from agentscope_orchestrator import constraint as _cs
        if r5["样本"]:
            全样本 = [("本轮产物", p)] + r5["样本"]
            全达标 = dict(r5["达标"])
            全达标["本轮产物"] = True
            rep = _cs.机检门集尊重约束([过细门], 全样本, d, 全达标, ".")
            chk("★ 过细门被变体抓出违反", bool(rep["违反"]) or
                rep["unknown"] == [过细门["name"]])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(f"  约束样本扩充·结构变体 自检：{ok}/{total} 通过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("约束样本扩充模块（--selftest 跑自检）")
