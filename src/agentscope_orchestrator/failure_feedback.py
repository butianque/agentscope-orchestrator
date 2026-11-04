"""失败反馈环（G6 缺口修复）：把判据门的**真实报错**转成可回喂的改进信号。

## 为什么需要它（实测缺陷，2026-10-13）

原实现在 `real_task.run_task` 里，窗口全红就 `return 1`（convergence_failed）。
判据门的 audit 明明抓到了**具体报错**——实测 `t1013_017` w0：

    shell_ok: error(lean.unknownIdentifier): Unknown identifier `ratMul_nonneg`

但这份 audit **只写进状态、从不回喂**。于是：
  · `resume_task` 复用已过窗、重跑失败窗时，模型收到的是**一模一样的提示词**；
  · 同一个模型、同一个提示、同一个温度 ⇒ 极可能给出同一个错答案；
  · 实测 `realMul_nonneg` 连跑 3 次（t1013_015/016/017）**三次同错**。

这不是「模型不行」，是**闭环缺一段**：红了之后没人告诉它「你错在哪、正确的东西长什么样」。
本模块补这一段，且**判据仍然全部机器可算**——回喂的是编译器原文与源码里的真实符号，
不是「模型评价模型」。

## 设计要点

1. `抽取错误信号(audit)` —— 从门 audit 里抠出**机器原文**：
   编译器报错行、缺失的 required pattern、命中的 forbidden pattern。
2. `可用符号(源文件)` —— 从目标模块源码抽出**真实存在的**定理/定义名，
   连同签名，供模型替换掉它幻觉出来的名字。
3. `构造反馈提示(...)` —— 拼成追加到原提示词后的「上一轮失败」段。
4. `反馈是否有效(旧,新)` —— **区分力自检**：若新旧提示词相同 ⇒ 反馈无效，
   必须报错而不是假装重试（防「原样重跑冒充改进」）。

全程零 LLM 判据：本模块只做**文本抽取与拼接**，判断仍由编译器等机器门做。
"""
from __future__ import annotations

import re
from pathlib import Path

# ── 编译器/解释器报错行（多语言，机制本体语言无关）─────────────────
#   Lean:    file:10:8: error(lean.unknownIdentifier): Unknown identifier `x`
#   Rust:    error[E0433]: failed to resolve: use of undeclared crate or module `x`
#   Python:  NameError: name 'x' is not defined
#
# ★★★ 修（2026-10-13）：原 `loc` 组写的是 `[^\s:]+` —— **不允许冒号**。
#   但 **Windows 盘符 `E:` 自带冒号** ⇒ 形如
#       E:\...\t1013_001_w1.out:9:24: error: Application type mismatch: ...
#   的**每一条** Lean/Rust 报错行都匹配不上 ⇒ `编译报错=0条` ⇒
#   失败反馈环**拿不到信号**、误判"无可操作信号"直接停止。
#   ★ 这是**只在 Windows 上静默失效**的缺陷：Linux 路径 `/a/f.lean:9:24:`
#     无冒号，同一正则工作正常 ⇒ 换平台就复发，极难察觉。
#   修法：显式允许可选的盘符前缀 `(?:[A-Za-z]:[\\/])?`，路径主体仍禁冒号。
_ERR_LINE_RE = re.compile(
    r"^\s*(?P<loc>(?:[A-Za-z]:[\\/])?[^\s:]*?:\d+:\d+:\s*)?"
    r"(?P<kind>error|warning|错误|Error|Traceback)"
    r"(?P<rest>[^\n]*)$",
    re.MULTILINE)

# ★ 定位行（编译器指向源码位置的 `-->` / `Note:` 等）——同样带 Windows 盘符，
#   供反馈提示里给出"错在哪个符号附近"。
_LOC_LINE_RE = re.compile(
    r"^\s*-->\s*(?P<file>(?:[A-Za-z]:[\\/])?[^\s:]*):(?P<line>\d+):(?P<col>\d+)",
    re.MULTILINE)

# Lean 未知标识符：反引号内的名字（最可操作的信号）
_UNKNOWN_IDENT_RE = re.compile(r"Unknown identifier\s+[`'\"]([^`'\"]+)[`'\"]")

# Rust 未解析名：`use of undeclared ... `x``
_RUST_UNRESOLVED_RE = re.compile(r"undeclared[^\n]*?[`']([A-Za-z_][A-Za-z0-9_]*)['`]")

# Python 未定义名
_PY_UNDEF_RE = re.compile(r"name '([A-Za-z_][A-Za-z0-9_]*)' is not defined")

# 报错行里带出的标识符（通用兜底）
_IDENT_IN_ERR_RE = re.compile(r"[`'\"]([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff']*)[`'\"]")


# ★ Lean 类型不符：抠出**期望类型**（最有操作性的信号 —— 直接告诉模型要证什么）。
#   实测（2026-10-13）：候选写 `exact ⟨rfl, rfl, rfl, rfl⟩` 而目标里
#   `readAllStep375 (panDepth n) = 3 * panDepth n` 不是 `rfl`，Lean 报
#       error: Application type mismatch: The argument
#         rfl
#       has type
#         ?m.36 = ?m.36
#       but is expected to have type
#         readAllStep375 (panDepth n) = 3 * panDepth n
#   ⇒ 「期望类型」就是模型下一轮要证的**准确命题**。原实现只抓报错首行，
#     把这段最有用的上下文丢了 ⇒ 反馈环无法据此改进。
_EXPECTED_TYPE_RE = re.compile(
    r"but is expected to have type\s*\n(?P<ty>(?:[ \t]+[^\n]*\n?)+)",
    re.MULTILINE)

# ★ 符号类型不符（Rust/通用）：`expected `X`, found `Y``
_EXPECTED_FOUND_RE = re.compile(
    r"expected\s+[`'](?P<want>[^`']+)[`'][^\n]*?found\s+[`'](?P<got>[^`']+)[`']")


def 抽取期望类型(编译报错原文: str) -> list[str]:
    """从编译原文里抠「期望类型 / expected ... found ...」（机器原文，非判断）。"""
    out: list[str] = []
    for m in _EXPECTED_TYPE_RE.finditer(编译报错原文 or ""):
        ty = " ".join(m.group("ty").split())
        if ty and ty not in out:
            out.append(ty[:300])
    for m in _EXPECTED_FOUND_RE.finditer(编译报错原文 or ""):
        s = f"expected {m.group('want')} · found {m.group('got')}"
        if s not in out:
            out.append(s[:300])
    return out


def 抽取错误信号(audit: list[dict] | None) -> dict:
    """从门 audit 抽取**机器原文**错误信号。

    返回 {编译报错: [行...], 未知名: [名...], 缺模式: [pat...], 命中禁词: [pat...]}。
    纯文本抽取，不做任何语义判断。
    """
    out: dict[str, list[str]] = {
        "编译报错": [], "未知名": [], "缺模式": [], "命中禁词": [], "期望类型": [],
    }
    for rec in (audit or []):
        if not isinstance(rec, dict):
            continue
        gate = str(rec.get("gate") or "")

        # ① 编译类门：抠 err 原文里的报错行
        err = rec.get("err") or rec.get("stderr") or ""
        if isinstance(err, str) and err.strip():
            for m in _ERR_LINE_RE.finditer(err):
                line = m.group(0).strip()
                if line and line not in out["编译报错"]:
                    out["编译报错"].append(line[:300])
            for rx in (_UNKNOWN_IDENT_RE, _RUST_UNRESOLVED_RE, _PY_UNDEF_RE):
                for m in rx.finditer(err):
                    n = m.group(1)
                    if n not in out["未知名"]:
                        out["未知名"].append(n)
            # 兜底：报错行里带反引号的名字
            if not out["未知名"]:
                for m in _IDENT_IN_ERR_RE.finditer(err):
                    n = m.group(1)
                    if n not in out["未知名"]:
                        out["未知名"].append(n)
            # ★ 期望类型（最有操作性：直接给出下一轮要证的准确命题）
            for ty in 抽取期望类型(err):
                if ty not in out["期望类型"]:
                    out["期望类型"].append(ty)

        # ② grep_required：缺哪些必需模式
        for pat in (rec.get("missing") or []):
            if pat and str(pat) not in out["缺模式"]:
                out["缺模式"].append(str(pat)[:200])

        # ③ grep_forbidden / scan_output：命中了哪些禁词
        for pat in (rec.get("hits") or []) + (rec.get("must_not_hits") or []):
            if pat and str(pat) not in out["命中禁词"]:
                out["命中禁词"].append(str(pat)[:200])

    return out


def 信号是否为空(信号: dict) -> bool:
    """五桶全空 ⇒ 门红了但没给出可操作信号（此时不该假装能反馈）。"""
    return not any(信号.get(k) for k in
                   ("编译报错", "未知名", "缺模式", "命中禁词", "期望类型"))


# ── 从源码抽真实符号 ────────────────────────────────────────────────
_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?"
    r"(?:noncomputable\s+|private\s+|protected\s+|partial\s+|unsafe\s+)*"
    r"(theorem|lemma|def|abbrev|instance|structure|class|inductive|axiom)\s+"
    r"([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff'.]*)",
    re.MULTILINE)

# Rust 顶层符号
_RS_DECL_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+|unsafe\s+|async\s+)*"
    r"(fn|struct|enum|trait|type|const|static|mod)\s+"
    r"([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*)",
    re.MULTILINE)


def 可用符号(源文件: Path | str | None, 上限: int = 120) -> list[str]:
    """抽出源文件里**真实存在**的声明名（含签名首行），供模型替换幻觉名。

    只做文本抽取，不判断语义；不返回整段证明体（防注入超长上下文）。
    """
    if 源文件 is None:
        return []
    p = Path(源文件)
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    # 去块注释，防注释里的假声明被当成真符号
    text = re.sub(r"/-.*?-/", " ", text, flags=re.DOTALL)

    out: list[str] = []
    seen: set[str] = set()
    rx = _RS_DECL_RE if p.suffix == ".rs" else _DECL_RE
    lines = text.splitlines()
    for m in rx.finditer(text):
        kind, name = m.group(1), m.group(2)
        if name in seen:
            continue
        seen.add(name)
        # ★ 用**关键字所在位置**算行号：`^` + MULTILINE 的匹配可能含前导空行，
        #   若用 m.start() 会落到空行上（实测缺陷：抽出的签名全是空串）。
        关键字位 = m.start() + m.group(0).find(kind)
        ln = text[:关键字位].count("\n")
        sig = lines[ln].strip() if ln < len(lines) else f"{kind} {name}"
        if ln + 1 < len(lines) and sig.rstrip().endswith((":", "->", "=", "(")):
            sig = sig + " " + lines[ln + 1].strip()
        out.append(re.sub(r"\s+", " ", sig)[:200])
        if len(out) >= 上限:
            break
    return out


def 构造反馈提示(信号: dict, 符号: list[str] | None = None,
                 门真值: dict | None = None) -> str:
    """把机器信号拼成「上一轮失败」反馈段（追加到原提示词之后）。

    只陈述机器给出的事实：哪门红了、编译器原话、源码里真实存在的符号。
    不写「你应该…」之类的主观指导（那会把判据交回模型）。
    """
    seg: list[str] = ["", "════════ 上一轮失败（机器判据原文，必须据此改正）════════"]

    if 门真值:
        红 = [k for k, v in 门真值.items() if not v]
        if 红:
            seg.append(f"未通过的门：{'、'.join(红)}")

    if 信号.get("编译报错"):
        seg.append("")
        seg.append("编译器原文：")
        for line in 信号["编译报错"][:8]:
            seg.append(f"  {line}")

    if 信号.get("未知名"):
        seg.append("")
        seg.append("下列名字在库中**不存在**（你上一轮用了它们，请勿再用）：")
        for n in 信号["未知名"][:12]:
            seg.append(f"  · {n}")

    if 信号.get("期望类型"):
        seg.append("")
        seg.append("编译器指出**期望的类型**（下一轮必须证明的准确命题）：")
        for ty in 信号["期望类型"][:4]:
            seg.append(f"  · {ty}")

    if 信号.get("缺模式"):
        seg.append("")
        seg.append("产物中**必须逐字出现**下列片段（当前缺失）：")
        for pat in 信号["缺模式"][:8]:
            seg.append(f"  · {pat}")

    if 信号.get("命中禁词"):
        seg.append("")
        seg.append("产物中**不得出现**下列片段（当前命中）：")
        for pat in 信号["命中禁词"][:8]:
            seg.append(f"  · {pat}")

    if 符号:
        seg.append("")
        seg.append("目标模块中**真实存在**的声明（可直接引用，名字须逐字一致）：")
        for s in 符号[:60]:
            seg.append(f"  {s}")

    seg.append("")
    seg.append("要求：仍只输出完整文件内容，前后不要解释文字、不要代码块围栏。"
               "不要重复上一轮的写法——它已被机器判据证明不通过。")
    return "\n".join(seg)


def 反馈是否有效(旧提示: str, 新提示: str) -> bool:
    """区分力自检：新提示必须**真的不同于**旧提示，否则是「原样重跑冒充改进」。"""
    return bool(新提示) and 新提示.strip() != (旧提示 or "").strip()


def _selftest() -> int:
    """自检：全部机器可算，无外部依赖。"""
    ok = 0
    total = 0

    def chk(name: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  ✗ {name}")

    # ① 抽 Lean 未知标识符
    au = [{"gate": "shell_ok", "ok": False, "rc": 1,
           "err": "f.lean:10:8: error(lean.unknownIdentifier): "
                  "Unknown identifier `ratMul_nonneg`\n"}]
    s = 抽取错误信号(au)
    chk("抽 Lean 未知名", s["未知名"] == ["ratMul_nonneg"])
    chk("抽编译报错", len(s["编译报错"]) >= 1)

    # ② 抽 Rust 未解析名
    au2 = [{"gate": "compile", "ok": False,
            "err": "error[E0433]: failed to resolve: use of undeclared "
                   "crate or module `foo_bar`\n"}]
    s2 = 抽取错误信号(au2)
    chk("抽 Rust 未解析名", "foo_bar" in s2["未知名"])

    # ③ 抽 Python 未定义名
    au3 = [{"gate": "run", "ok": False, "err": "NameError: name 'zzz' is not defined"}]
    chk("抽 Python 未定义名", "zzz" in 抽取错误信号(au3)["未知名"])

    # ④ 缺模式 / 禁词
    au4 = [{"gate": "statement_intact", "ok": False, "missing": ["theorem T"]},
           {"gate": "no_forbidden", "ok": False, "hits": ["sorry"]}]
    s4 = 抽取错误信号(au4)
    chk("抽缺模式", s4["缺模式"] == ["theorem T"])

    # ⑤ ★★ Windows 盘符路径的报错行必须抓得住（本轮修的静默失效）
    #    原 `loc` 组 `[^\s:]+` 不允许冒号，而 `E:` 自带冒号 ⇒ 全行漏抓
    win = ("E:\\基础设施\\项目\\work\\t1_w0.out:9:24: error: "
           "Application type mismatch: The argument\n"
           "  rfl\nhas type\n  ?m.36 = ?m.36\n"
           "but is expected to have type\n"
           "  readAllStep375 (panDepth n) = 3 * panDepth n\n")
    s5 = 抽取错误信号([{"gate": "shell_ok", "ok": False, "rc": 1, "err": win}])
    chk("★ Windows 盘符报错行抓得住", len(s5["编译报错"]) >= 1)
    chk("★ 不再误判『无可操作信号』", not 信号是否为空(s5))

    # ⑥ ★ 期望类型（最有操作性）
    chk("★ 抽出期望类型", any("readAllStep375" in t for t in s5["期望类型"]))

    # ⑦ Windows Rust 路径（回归）
    s7 = 抽取错误信号([{"gate": "compile", "ok": False,
                        "err": "E:\\p\\src\\a.rs:3:5: error[E0308]: mismatched types\n"}])
    chk("Windows Rust 报错行抓得住", len(s7["编译报错"]) >= 1)

    # ⑧ Linux 路径仍抓（不能为修 Windows 而破 Linux）
    s8 = 抽取错误信号([{"gate": "shell_ok", "ok": False, "rc": 1,
                        "err": "/home/u/f.lean:10:8: error(lean.unknownIdentifier): "
                               "Unknown identifier `ratMul_nonneg`\n"}])
    chk("Linux 路径仍抓（回归）", len(s8["编译报错"]) >= 1
        and s8["未知名"] == ["ratMul_nonneg"])

    # ⑨ 无定位前缀仍抓（回归）
    s9 = 抽取错误信号([{"gate": "x", "ok": False, "err": "error: boom\n"}])
    chk("无定位前缀仍抓（回归）", len(s9["编译报错"]) >= 1)

    # ⑩ expected/found 形态
    s10 = 抽取错误信号([{"gate": "x", "ok": False,
                         "err": "error: expected `usize`, found `String`\n"}])
    chk("抽 expected/found", any("usize" in t for t in s10["期望类型"]))
    chk("抽命中禁词", s4["命中禁词"] == ["sorry"])

    # ⑤ 空信号识别
    chk("空信号识别", 信号是否为空(抽取错误信号([{"gate": "x", "ok": False}])))
    chk("非空信号识别", not 信号是否为空(s))

    # ⑥ 源码符号抽取（真文件）
    me = Path(__file__)
    syms = 可用符号(me)
    chk("抽本文件符号", any("抽取错误信号" in x for x in syms))

    # ⑦ 反馈提示真的包含机器原文
    txt = 构造反馈提示(s, ["theorem realMul_nonneg {a b : Real} : ..."],
                       门真值={"shell_ok": False, "no_forbidden": True})
    chk("反馈含未知名", "ratMul_nonneg" in txt)
    chk("反馈含符号表", "realMul_nonneg" in txt)
    chk("反馈点名红门", "shell_ok" in txt)
    chk("反馈不点名绿门", "no_forbidden" not in txt.split("未通过的门")[1][:40])

    # ⑧ 区分力自检
    chk("同提示判无效", not 反馈是否有效("abc", "abc"))
    chk("异提示判有效", 反馈是否有效("abc", "abc" + txt))

    # ⑨ 源文件不存在 → 空，不抛
    chk("缺文件返回空", 可用符号(Path("___不存在___.lean")) == [])
    chk("None 返回空", 可用符号(None) == [])

    print(f"  失败反馈环自检：{ok}/{total} 通过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
