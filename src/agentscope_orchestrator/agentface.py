"""面向**智能体消费者**的两个能力（本模块存在的理由）

调用方是 AI，不是人。所以这里只有两件东西值得做：

1. `orch_object` —— **接收 AI 的怀疑**。
   外部产品的盲点：模型说"我怀疑这个绿是假的"时，产品只能让它重跑或自己瞎猜。
   本模块把它变成一个**可机验的动作**：在当前产物上跑判据（应绿）→ 在**破坏副本**上
   再跑（应变红）。两个读数都成立 ⇒ 这个判据**确实抓得住单点破坏**，入册；否则如实
   告诉它"这条判据抓不住"，避免它把希望寄托在一条没区分力的判据上。
   这是 `gate_power` 的**定向版**：那边是量的门族整体，这边是量**你此刻信的那一条**。

2. `决策摘要` —— **回执的 token 预算**。
   AI 的上下文有限。原回执有 15+ 字段，其中 `审计官`/`仓改动`/`成本` 多数时候用不上。
   这里给一个 ≤1KB 的定长摘要，让调用方**先读摘要再决定要不要读细节**。

诚实边界：`orch_object` 只测**机械变异**（返回置空 / 比较反号 / 常量翻转 / 断言删除），
测不出语义错；"变异没红"只说明这条判据不敏感，**不**说明产物是对的。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

_跳过 = {".git", "target", "node_modules", "__pycache__", ".venv", "dist", "build",
         ".pytest_cache", ".mypy_cache", ".orch_backup", ".lake", ".orchestrator-state",
         ".idea", ".vscode", ".next", ".turbo"}
_源后缀 = {".py", ".rs", ".ts", ".tsx", ".js", ".mjs", ".go", ".java", ".kt", ".c",
           ".h", ".cpp", ".lean"}


def _状态根() -> Path:
    """★ R550：收口到**唯一取法** `profile.state_dir()`（verifier 抓出：这处仍是自有实现）。

    ★ 原来这里自己按「env → home → 临时目录」两级退，落点是 `临时目录/orch-state`，
      而本模块持有的是**判据册 `判据册.jsonl` 与 `receipts/`** —— 都是「跨重启要记住的事」。
      于是它们与 `profile.STATE_ROOT` 分成两个根：各自都能写、各自都自洽，
      **所以没有任何判据会红** —— 而这正是最坏的一类：再遇到 home 不可写，
      「写不了」会被显示成「没有」，与 R547/R548 那个 P0 一模一样。
    """
    from . import profile as _pf550
    p = _pf550.state_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _回执目录() -> Path:
    d = _状态根() / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _留痕(类型: str, 内容: dict) -> str:
    """把一次回执落盘（AI 会话会断，**历史必须活在文件里**）。返回文件路径。"""
    # 毫秒 + 进程内序号：同一秒的多次调用不许互相覆盖（踩过：3 次只落 1 个文件）
    _序 = globals().setdefault("_留痕序", {"n": 0})
    _序["n"] += 1
    名 = (f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
          f"-{_序['n']:03d}_{类型}.json")
    p = _回执目录() / 名
    try:
        p.write_text(json.dumps(内容, ensure_ascii=False, indent=1, default=str),
                     encoding="utf-8")
    except OSError:
        return ""
    return str(p)


def 落检查点(task_id: str, 内容: dict) -> str:
    """把"此刻走到哪、下一步做什么、怎么接上"写成一个文件。

    ★ 为什么检查点不是"日志"：AI 会话会断（换会话、上下文溢出、进程重启）。
      日志只告诉你过去发生了什么；检查点必须回答**接下来该调什么工具、带什么参数**，
      否则断线后只能从头再来（对智能体来说等于整个任务白做）。
      内容里强制带 `续跑={工具,参数}`，且**参数可直接用**。
    """
    p = _回执目录() / f"{task_id}.checkpoint.json"
    try:
        p.write_text(json.dumps({"task_id": task_id,
                                 "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 **内容}, ensure_ascii=False, indent=1, default=str),
                     encoding="utf-8")
        return str(p)
    except OSError:
        return ""


def 决策摘要(分类: str, 责任: str | None, 门: list[dict],
             修法: dict | None, 额外: dict | None = None) -> dict:
    """≤1KB 的可决策摘要。**先读它，再决定要不要读细节。**

    ★ 为什么不是"更短的回执"而是"额外一个摘要"：摘要会丢证据，而证据是回执的
      存在理由。两者并存，让调用方**自己**按 token 预算取舍，而不是我们替它猜。
    """
    红门 = [g for g in (门 or []) if g.get("通过") is False]
    未知 = [g.get("门") for g in (门 or []) if "未知" in str(g.get("证据") or "")]
    下一步: list[dict] = []
    if 修法:
        下一步.append({"做": 修法.get("工具"), "参数": 修法.get("参数"),
                       "为什么": (修法.get("说明") or "")[:60]})
    if 未知:
        下一步.append({"做": "换判据或缩范围", "为什么": f"这 {len(未知)} 门给不出结论（未知≠未达标）"})
    if not 红门 and not 未知:
        下一步.append({"做": "用产物，别复查", "为什么": "全绿且无未知"})
    出 = {
        "分类": 分类,
        "责任": 责任,
        "门": {"总数": len(门 or []), "红": len(红门), "未知": len(未知),
               "红门名": [g.get("门") for g in 红门][:5]},
        "下一步": 下一步[:3],
    }
    if 额外:
        出.update({k: v for k, v in 额外.items() if v is not None})
    # 硬上限：摘要超过 1000 字节就砍"为什么"（它是最不承重的字段）
    if len(json.dumps(出, ensure_ascii=False)) > 1000:
        for s0 in 出["下一步"]:
            s0.pop("为什么", None)
    return 出


# ─────────────── 机械变异器（只做"一定改变行为"的那几种） ───────────────
def _变异器(文本: str, 后缀: str) -> list[tuple[str, str]]:
    """返回 [(说明, 变异后文本)] —— 每种都保证**行为**改变，不是改格式。"""
    出: list[tuple[str, str]] = []
    if 后缀 in (".py",):
        # ① 返回常量置空
        for a, b, 名 in ((" return True", " return False", "return True→False"),
                         ("return True", "return False", "return True→False"),
                         ("return False", "return True", "return False→True"),
                         ("return 0", "return 1", "return 0→1"),
                         ("return None", "return 0", "return None→0")):
            if a in 文本:
                出.append((名, 文本.replace(a, b, 1)))
        # ② 比较反号
        if " == " in 文本:
            出.append(("== → !=", 文本.replace(" == ", " != ", 1)))
        if " != " in 文本:
            出.append(("!= → ==", 文本.replace(" != ", " == ", 1)))
        # ③ 真值翻转
        if " = True" in 文本:
            出.append(("= True → = False", 文本.replace(" = True", " = False", 1)))
        # ④ **通用返回值放大**：`return X` → `return (X) + 1`
        #    这条能覆盖 `return a + b` 这类最常见的实现体（此前是盲区）。
        import re as _re
        m = _re.search(r"(?m)^([ \t]*)return[ \t]+([^\n]+)$", 文本)
        if m and "return (" not in m.group(2):
            出.append(("return X → return (X)+1",
                      文本[:m.start()] + f"{m.group(1)}return ({m.group(2)}) + 1" + 文本[m.end():]))
        # ⑤ 断言删除
        for 行 in 文本.splitlines():
            if 行.strip().startswith("assert "):
                出.append(("删一条 assert",
                          文本.replace(行, 行[:len(行) - len(行.lstrip())] + "pass", 1)))
                break
    elif 后缀 in (".rs",):
        if "==" in 文本:
            出.append(("== → !=", 文本.replace("==", "!=", 1)))
        if "Ok(" in 文本:
            出.append(("Ok→Err", 文本.replace("Ok(", "Err(", 1)))
        import re as _re
        m = _re.search(r"(?m)^([ \t]*)return[ \t]+([^;\n]+);", 文本)
        if m:
            出.append(("return X → return X+1",
                      文本[:m.start()] + f"{m.group(1)}return {m.group(2)} + 1;" + 文本[m.end():]))
    elif 后缀 in (".ts", ".tsx", ".js", ".mjs"):
        if "===" in 文本:
            出.append(("=== → !==", 文本.replace("===", "!==", 1)))
        if "return true" in 文本:
            出.append(("return true→false", 文本.replace("return true", "return false", 1)))
        import re as _re
        m = _re.search(r"(?m)^([ \t]*)return[ \t]+([^;\n]+);", 文本)
        if m:
            出.append(("return X → return X+1",
                      文本[:m.start()] + f"{m.group(1)}return ({m.group(2)}) + 1;" + 文本[m.end():]))
    elif 后缀 == ".lean":
        if ":= true" in 文本:
            出.append((":= true → := false", 文本.replace(":= true", ":= false", 1)))
        if ":= rfl" in 文本:
            出.append((":= rfl → := sorry", 文本.replace(":= rfl", ":= sorry", 1)))
    return 出


def _副本(源: Path, 上限: int = 12000) -> tuple[Path | None, int]:
    """复制被测仓（跳过构建产物/依赖）。返回 (副本根 或 None, 文件数)。"""
    数 = 0
    栈 = [源]
    while 栈:
        d = 栈.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.name in _跳过:
                            continue
                        栈.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        数 += 1
                        if 数 > 上限:
                            return None, 数
                except OSError:
                    continue
    根 = Path(tempfile.mkdtemp(prefix="obj_"))
    目标 = 根 / "repo"
    try:
        shutil.copytree(源, 目标, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*_跳过))
    except OSError:
        return None, 数
    return 目标, 数


_垃圾前缀 = ("tmp", "_t_", "zz_", ".tmp")


def _候选源(仓: Path, 命令: list[str], 上限: int = 6) -> list[Path]:
    """挑"该挑的"被测源文件 —— 按**与命令的相关性**，不按新鲜度。

    真事故：按 mtime 倒序 ⇒ 命中测试留下的探针（`tmpXXXX/x.py`，0 行逻辑）⇒
    变异器一个都挂不上 ⇒ 回执说"判据不敏感"，而**真正被判断的模块根本没试过**。
    打分口径（可解释、无魔法）：
      ① 命令里**点名**了该文件路径/文件名        +100
      ② 命令里出现了该文件的 stem（模块名）      +60
      ③ 体积（真逻辑通常更大，每 4 KB +1，封顶 20）
      剔除：明显临时文件（tmp*/_t_*/zz_*）、<200 字节、构建产物目录。
    """
    文本 = " ".join(str(c) for c in 命令).replace("\\", "/")
    得分: dict[Path, int] = {}
    for p in 仓.rglob("*"):
        try:
            if p.suffix.lower() not in _源后缀 or not p.is_file():
                continue
            段 = p.relative_to(仓).parts
            if any(x in _跳过 for x in 段):
                continue
            # 垃圾判定要查**每一段**：`tmpXXXX/x.py` 的文件名很干净，脏在目录名上。
            if any(str(x).startswith(_垃圾前缀) for x in 段):
                continue
            大小 = p.stat().st_size
            if 大小 < 60:          # 只滤空壳；小而正当的模块必须留在候选里
                continue
        except OSError:
            continue
        相对 = str(p.relative_to(仓)).replace("\\", "/")
        s0 = 0
        if 相对 in 文本 or p.name in 文本:
            s0 += 100
        elif len(p.stem) > 2 and p.stem in 文本:
            s0 += 60
        s0 += min(20, 大小 // 4096)
        得分[p] = s0
    排序 = sorted(得分.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return [p for p, _s in 排序[:上限]]


async def 实现(repo: str, 怀疑: str, cmd: list[str],
             timeout_s: int = 300, max_files: int = 12000) -> dict:
    """`orch_object` 的实现：把 AI 的怀疑变成可机验的双读数。"""
    from agentscope_orchestrator import gate_spec as gs

    仓 = Path(str(repo)).resolve()
    if not 仓.is_dir():
        return {"分类": "参数/前提不成立", "责任归属": "调用方侧",
                "error": f"repo 不是目录：{仓}",
                "决策摘要": 决策摘要("参数/前提不成立", "调用方侧", [], None,
                                 {"证据": "repo 路径不成立"})}
    命令 = [str(c) for c in (cmd or [])]
    if not 命令:
        return {"分类": "参数/前提不成立", "责任归属": "调用方侧",
                "error": "cmd 不能为空",
                "决策摘要": 决策摘要("参数/前提不成立", "调用方侧", [], None)}

    # ── ① 现状：这条判据此刻在**真仓**上是什么读数 ──
    rc0, out0, err0 = gs._run(命令, 仓, int(timeout_s), None, "object-现状")
    现状绿 = rc0 == 0
    证据0 = (out0 + err0)[-1200:]

    if not 现状绿:
        # 怀疑成立：现在就不绿。不需要变异，直接回报可诊断证据。
        回执 = {
            "分类": "未达标", "责任归属": "执行侧", "怀疑": 怀疑,
            "现状": {"绿": False, "rc": rc0, "证据": 证据0},
            "区分力": "未测（现状已红，无需变异）",
            "修法": {"工具": "orch_fix", "参数": {"repo": str(仓), "cmd": 命令},
                     "说明": "红了让模型改并由机器门复跑；绿保红滚"},
        }
    else:
        # ── ② 区分力：在**破坏副本**里同一条判据还绿不绿 ──
        副, 数 = _副本(仓, int(max_files))
        if 副 is None:
            回执 = {
                "分类": "达标", "责任归属": None, "怀疑": 怀疑,
                "现状": {"绿": True, "rc": 0},
                "区分力": f"未测（仓内文件 {数} 超出副本上限，不做破坏测试）",
                "建议": "缩到子目录再问，或直接用 gate_power 量门族区分力",
            }
        else:
            候选 = _候选源(副, 命令)
            命中 = None
            试过 = 0
            try:
                for 源 in 候选:
                    try:
                        原文 = 源.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for 说明, 变后 in _变异器(原文, 源.suffix.lower())[:8]:
                        试过 += 1
                        try:
                            源.write_text(变后, encoding="utf-8")
                        except OSError:
                            continue
                        rc1, o1, e1 = gs._run(命令, 副, int(timeout_s), None, "object-变异")
                        源.write_text(原文, encoding="utf-8")
                        if rc1 != 0:
                            命中 = {"文件": str(源.relative_to(副)).replace("\\", "/"),
                                    "变异": 说明, "rc": rc1,
                                    "证据": (o1 + e1)[-600:]}
                            break
                    if 命中:
                        break
            finally:
                shutil.rmtree(副.parent, ignore_errors=True)

            if 命中:
                册 = _状态根() / "判据册.jsonl"
                try:
                    with 册.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"时间": time.strftime("%Y-%m-%d %H:%M:%S"),
                                             "仓": str(仓), "cmd": 命令, "怀疑": 怀疑,
                                             "双验": 命中}, ensure_ascii=False) + "\n")
                except OSError:
                    pass
                回执 = {
                    "分类": "达标", "责任归属": None, "怀疑": 怀疑,
                    "现状": {"绿": True, "rc": 0},
                    "区分力": {"抓住": 命中["变异"], "文件": 命中["文件"],
                               "说明": "这条判据**确实抓得住单点破坏**；但它抓不住语义错"},
                    "已入册": str(册),
                    "试过变异数": 试过,
                }
            else:
                回执 = {
                    "分类": "达标", "责任归属": None, "怀疑": 怀疑,
                    "现状": {"绿": True, "rc": 0},
                    "区分力": {"抓住": None, "试过变异数": 试过,
                               "说明": "**没抓住任何机械变异** ⇒ 这条判据对破坏不敏感，"
                                       "别把结论寄托在它上面（换 oracle/grep_required/test_counts）"},
                }

    _抽 = 决策摘要(回执["分类"], 回执.get("责任归属"),
                  [{"门": "现状", "通过": 现状绿, "证据": 证据0[:200]}],
                  回执.get("修法"),
                  {"怀疑": 怀疑[:60], "区分力": str(回执.get("区分力"))[:80]})
    _区 = 回执.get("区分力")
    _没验 = isinstance(_区, str) or (_区 or {}).get("抓住") in (None, "")
    if _没验:
        # 判据没被证明有区分力 ⇒ 摘要**不许**说"收工"
        _抽["下一步"] = [{"做": "换判据或缩到子目录再测",
                           "为什么": "这条判据对机械破坏不敏感或未测，绿不足以支撑结论"[:60]}]
        _抽["可信度"] = "低（判据区分力未验）"
    else:
        _抽["可信度"] = f"中（抓住 {_区.get('变异')}）"
    回执["决策摘要"] = _抽
    回执["留痕"] = _留痕("orch_object", 回执)
    return 回执
