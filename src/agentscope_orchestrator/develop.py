"""无人值守自主开发驱动（develop）：多智能体自动改源码 → 机器判据门 → 绿了才落盘。

角色纪律（与编排口径严格一致）：
  - 本驱动只做【编排/调度/判据门/落盘】，绝不创作源码内容。
  - 每一个工程改动方案 100% 由真实模型智能体产出（走 real_task 已验证的
    Router + load_channels/build_client 通道）；驱动只是【派活 → 收方案 →
    过门 → 落盘】的中转，不亲自 Edit/Write 生产源码。
  - 唯一裁决 = 机器判据门（gate_spec.evaluate + engine_dev.gate_suite），
    纯退出码/文本扫描，拒绝"模型审模型"。

闭环流程（每一门失败最多重试 4 次，仍失败记 blocked 继续下一个，不整体停）：
  1. 扫描红门：跑 engine_dev 门套件，拿 results+audit；从 compile 门 audit 的
     err 文本用正则抓 `--> <file>:<line>:<col>` 与 `error[E..]`，作为缺口证据。
  2. 缺口→修复任务：把证据（文件+行号+编译器错误原文）打包 prompt，调真实模型
     让模型输出【逐字 旧→新 替换块】（不是让驱动自己想改法）。
  3. 先暂存后落地（安全闸）：把合并后的候选全文先写入 _WORK 暂存件留痕；
     然后【备份原文件到 .audit_trail/develop_backup/ → 原子应用（绿保/红滚）】。
     ⚠ 诚实说明：Rust 的 `cargo test --lib` 只能在 crate 工作树内编译测试目标，
     无法对 _WORK 脱离 crate 的文件直接跑内联测试门。因此本驱动对该目标文件采用
     【备份→应用方案→跑 crate 门→全绿保留 / 任一红则从备份回滚】的原子编辑事务：
     备份先于任何覆盖，红门零残留，且候选已先在 _WORK 暂存留痕供回放审计。
  4. 循环：处理完红门 → 重跑一轮门套件 → 有新红门继续；目标完成判据 =
     配置目标门全绿（check_lib+test_lib+build_all_targets）。
  5. 持久化+回执（无人值守跨会话）：账本 .audit_trail/develop_ledger.jsonl
     （每轮：时间/扫描门结果/捕获缺口/每缺口尝试与 resolved|blocked|not_wired），
     加 develop_live.json（最近一轮全量）。启动时从账本恢复：已 resolved/blocked
     的缺口不重复跑（可用 --force 重跑）。

用法：
  venv\\Scripts\\python.exe src\\agentscope_orchestrator\\develop.py --once
  ... develop.py --iterations 3 --channel aaa
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from .msgs import UserMsg

from agentscope_orchestrator import gate_spec as gate_spec_mod
from agentscope_orchestrator.gate_spec import evaluate
# ★ R157：这三个**不再被本文件使用** —— 通道一律走 `build_adaptive_router`
#   （自适应探活 + 全部 (通道,模型) 对 + 持续重探）。
#   ★ 保留死导入会让后来者以为「这里是入口」，从而又抄一遍静态口径
#     —— 「修了一侧另一侧没修」正是这么来的。
#   ⇒ 删掉，把入口收成一处。
from agentscope_orchestrator import engine_dev
from agentscope_orchestrator import ts_receipt
from agentscope_orchestrator import math_model
from agentscope_orchestrator import profile as prof_mod
from agentscope_orchestrator import observation as obs_mod
from agentscope_orchestrator import window_lock
from agentscope_orchestrator import auto_mechanism as ao_mech  # 工具调用环：机器演化候选骨架
from agentscope_orchestrator import auto_objective  # 第二层闭环：偏差→全局目标 自指闭环

logger = logging.getLogger("develop")
logger.addHandler(logging.StreamHandler(sys.stderr))
logger.setLevel(logging.INFO)

# ── 工作区 / 回执（全部由档案派生；工具不持有任何项目路径）────────
ROOT: Path = Path(".")
AUDIT: Path = Path(".")
BACKUP: Path = Path(".")
LEDGER: Path = Path(".")
LIVE: Path = Path(".")
EVOLVED: Path = Path(".")
MEMORY_PATH: Path = Path(".")
_WORK: Path = Path(".")


def reload_paths() -> None:
    """按当前档案重算全部路径（换档案后调用；模块级名字保持可用）。"""
    global ROOT, AUDIT, BACKUP, LEDGER, LIVE, EVOLVED, MEMORY_PATH, _WORK
    prof = prof_mod.get_profile()
    ROOT = prof.repo_root
    AUDIT = prof.audit_dir
    AUDIT.mkdir(parents=True, exist_ok=True)
    BACKUP = AUDIT / "develop_backup"
    BACKUP.mkdir(exist_ok=True)
    LEDGER = AUDIT / "develop_ledger.jsonl"
    LIVE = AUDIT / "develop_live.json"
    EVOLVED = AUDIT / "develop_evolved.jsonl"   # 判据演化对象账本（机构沉淀：真值变对象）
    MEMORY_PATH = AUDIT / "structure_memory.json"  # 结构记忆（按需增量观测；记忆检索联动防膨胀）
    _WORK = prof.work_dir
    _WORK.mkdir(parents=True, exist_ok=True)


reload_paths()

# ── 缺口证据正则（从含 `--> <file>:<line>:<col>` 与 `error[E..]` 的编译输出抓取）──
_RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# 行号缺口正则：`--> file:line:col`。file 段须容忍 Windows 盘符绝对路径中的“:”（
# 如 E:\...\趋向.rs），故用惰性 `[^\s]+?` 而非 `[^\s:]+`——否则盘符后的首个“:”
# 就把路径截断 → trace_gaps 抓不到行号 → 误判成 run_hang 卡死（真实落地被它挡住）。
_RE_LOC = re.compile(r"-->\s+([^\s]+?):(\d+):(\d+)", re.MULTILINE)
_RE_ERR = re.compile(r"error(\[[A-Za-z0-9]+\])?:?\s*(.{0,200})", re.MULTILINE)
# cargo 错误段头：行首 `error[E..]: msg` 或 `error: msg`；用于块感知切段。
_RE_ERR_HEAD = re.compile(r"^error(\[[A-Za-z0-9]+\])?:?\s*(.*)$")
# 段内紧随的 `-->` 定位：error 段正文里、NOT 行首 warning 的 -->。
_RE_LOC_LINE = re.compile(r"^[ \t]*-->\s+([^\s]+?):(\d+):(\d+)")
# error 段与后续错误段的边界：下一段以 `error` 或 `error:` 或 `error[` 开头，
# 或出现未缩进的 `warning:`（新 warning 段）。指示符 `| ^^^` 不算段头。
def _block_locations(err: str) -> list[tuple[str, str, str, int, int]]:
    """块感知提取 (code, msg, file, line, col)。

    只用错误段内、紧随 error 头的 `-->` 作为该错误的真实位置；warning 段的
    `-->`（如 src 下面附加 main.rs:50 这样的 warning 定位）不再被误配到错误码
    —— 这是"包准"的兑现点。
    遍历多错误：每 `error[...]` 段各取其紧随的 `-->`，回收多个独立编译错误。
    """
    out: list[tuple[str, str, str, int, int]] = []
    cur_code = ""
    cur_msg = ""
    in_error = False          # 当前是否在某一 error 段内（其后 --> 属该错）
    for line in err.splitlines():
        s = line.rstrip()
        stripped = s.strip()
        m = _RE_ERR_HEAD.match(s)
        # ★★★★★ 修复 R4-F8（**真缺陷**）：裸 `error:` 也要能开错误段。
        #   原实现只认 `error[E..]`（带码型），理由是"裸 `error:` 开场是
        #   `error: could not compile` 之类收尾段，不带 -->"。
        #   ★ 但 rustc 有一大类**真错误没有码**：
        #     `error: expected one of ...` / `error: cannot find macro ...` /
        #     `error: this file contains an unclosed delimiter` 等，
        #     它们**后面紧跟 `-->`**，却被挡在段外 ⇒ 缺口证据丢失
        #     ⇒ 该错误**永不派回炉**（引擎卡死在它上面，账本里连缺口都没有）。
        #   ★ 修法：两类都开段。收尾段（`error: could not compile`）开了段但
        #     **其后没有 `-->`**，故不发射任何缺口 —— 天然无害。
        if m and (stripped.startswith("error[") or stripped.startswith("error:")):
            cur_code = m.group(1) or "error"
            cur_msg = (m.group(2) or "").strip()
            in_error = True
            continue
        if in_error and stripped.startswith("warning:"):
            in_error = False            # 新 warning 段开始，关闭错误段
            continue
        if in_error:
            lm = _RE_LOC_LINE.match(s)
            if lm:
                f, ln, cl = lm.group(1), int(lm.group(2)), int(lm.group(3))
                out.append((cur_code, cur_msg, f, ln, cl))
                in_error = False        # 一个错误段通常一个 -->；取紧随首处
            # 否则是该错误段正文（`|`, 数字行, `^^^`），继续等待 --> 或新 error/warning
    return out


def _clean_err(err: str) -> str:
    """清洗编译输出中的 ANSI 转义序列（cargo 带色输出含 \\x1b[0m 污染路径）。"""
    return _RE_ANSI.sub("", err)


@dataclass
class Gap:
    file: Path            # 相对 ROOT 的路径
    abs_path: Path
    line: int
    col: int
    code: str             # E0433 / 标记名 / 无
    message: str          # 报错原文（截断）
    status: str = "open"  # open|resolved|blocked|not_wired
    attempts: int = 0
    error: str = ""
    kind: str = "compile"  # compile=编译/挂起门缺口；observation=观测机制违反缺口
    phase_priority: int = 0  # 0=编译根(先修)  1=观测机制(根绿后再修)
    cls: str = "production"  # production|bench   基准/测试只扫不改

    @property
    def key(self) -> str:
        """缺口身份 = `文件@错误码@行`。

        ★★★★★ 修复 R4-F7（**真缺陷**）：原先 key **不含行号**
          （`file@code`），理由是"一次修复带多行，别把同问题多行拆开"。

        ★ 那个理由对**轮内**成立，对**跨轮账本**是错的：
          `load_ledger_state()` 按 key 记 `resolved/blocked`，轮次循环里
          `if g.key not in state` 直接跳过 ⇒ 一旦某文件某码被修好过，
          **此后该文件里新出现的同码错误全部被静默跳过、永不修复**。
          实测形态：`src/x.rs@E0433` 第 100 行修好入账；第 200 行新写的一处
          `E0433` 编译错误 ⇒ key 相同 ⇒ **跳过** ⇒ 引擎永远编译不过，
          而账本说"这个缺口已 resolved"。这是**最坏的一类**：静默、永久、自洽。

        ★ 修法：行号并入 key。轮内"一次修复带多行"由 `trace_gaps` 的
          `(file, code, line)` 去重**天然保证**（同一行只留一条）；
          跨轮则**逐行**记账 —— 新行 = 新缺口 = 必须重新修。
          多花的是"同一个码多修几行"，省下的是"静默漏修"。
        """
        return f"{self.file.as_posix()}@{self.code}@{self.line}"


def _text_gate_signature(entry: dict) -> tuple[str, str] | None:
    """把**文本门**的 audit 条目映射成缺口 (code, message)；非文本门返回 None。

    ★ 新增（2026-10-12）：gate_spec 的文本门 audit 结构各不相同，且都不带 `err`：
      · grep_required  → {"missing": [...]}          必含模式未命中
      · grep_forbidden → {"hits": [...]}             出现禁词
      · whole_reading  → {"missing": [...]}          （contains 模式）
                       → {"must_not_hits": [...]}    （forbidden 模式）
                       → {"sig","expect_sig"}        （equal 模式，签名不符）
                       → {"err": "..."}              （缺 must_contain/patterns）
    原实现只认 `err`，故这些红门全部产不出缺口、永不派回炉。此处逐一还原。"""
    g = str(entry.get("gate") or entry.get("type") or "?")
    missing = entry.get("missing")
    if isinstance(missing, list) and missing:
        return ("text_missing",
                f"门[{g}] 缺必需结构键 {len(missing)} 项："
                + "; ".join(str(m)[:60] for m in missing[:3]))
    hits = entry.get("hits")
    if isinstance(hits, list) and hits:
        return ("text_forbidden_hit",
                f"门[{g}] 命中禁词 {len(hits)} 处：" + "; ".join(str(h)[:60] for h in hits[:3]))
    mnh = entry.get("must_not_hits")
    if isinstance(mnh, list) and mnh:
        return ("text_forbidden_hit",
                f"门[{g}] 命中禁词 {len(mnh)} 处：" + "; ".join(str(h)[:60] for h in mnh[:3]))
    # whole_reading equal 模式：签名不符（没带 missing 字段）
    if "expect_sig" in entry and "sig" in entry and entry.get("sig") != entry.get("expect_sig"):
        return ("text_not_equal",
                f"门[{g}] 整读签名与期望不符：got={str(entry.get('sig'))[:40]} "
                f"expect={str(entry.get('expect_sig'))[:40]}")
    # 编译/行为门交给原路径（err / rc）
    if entry.get("rc") is not None or entry.get("err"):
        return None
    return None


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```\w*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_text(content) -> str:
    parts: list[str] = []
    for block in content:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def trace_gaps(audit: list[dict]) -> list[Gap]:
    """从机器门 audit 抓缺口证据。

    两类缺口都会派发真实模型：
      ① 编译错误 —— 从 compile 门 ok=False 的 err 里抓 `--> file:line:col`（行号缺口）。
      ② 挂起/失败门 —— compile 门 ok=False 但**没有**编译错误行号（rc≠0 或超时，
        说明不是编译报错，而是命令本身跑不完/超时/失败），产出一个 run_hang 缺口，
        派发真实模型去诊断该门为何不收敛、按机制改到跑完。这是让工具不因
        「运行时慢/挂起」就哑火卡死的关键：之前这类缺口因无行号而不派发 → 卡死。
    """
    gaps: list[Gap] = []
    for entry in audit:
        # ★ 修复（2026-10-12）：**只按 ok 判定**，删掉 `or rc==0` 这个错误短路。
        #   原实现 `if entry.get("ok") or entry.get("rc", 0) == 0: continue`：
        #   文本门（grep_forbidden / grep_required / whole_reading）的 audit 条目
        #   **根本没有 `rc` 键** ⇒ `get("rc", 0)` 取默认 0 ⇒ 条件恒真 ⇒ **红文本门被
        #   静默跳过，永不派发**（实机：3 条红门只产出 1 条缺口；no_scaffold 红、
        #   必需标记缺失永不修）。ok 才是唯一权威判据；rc 只用于 compile 门的细分。
        if entry.get("ok"):
            continue
        gate_name = entry.get("gate", "compile")

        # ⓪ 文本门缺口（★ 修复 2026-10-12）：grep_forbidden / grep_required /
        #    whole_reading 的 audit 条目**没有 `err` 键**，只有 missing / hits /
        #    must_not_hits。原实现 `if not err: continue` 把它们全部丢在门外 ⇒
        #    即使上面的 ok 判定修好了，红文本门依然产不出缺口。
        #    现按门语义各自成缺口，让"缺必需标记""出现禁词""缺结构键"都能回炉。
        text_sig = _text_gate_signature(entry)
        if text_sig is not None:
            code, msg = text_sig
            gaps.append(Gap(file=Path(gate_name + ".txt"), abs_path=ROOT,
                            line=1, col=1, code=code, message=msg[:200],
                            kind="text"))
            continue

        err = _clean_err(entry.get("err", "") or "")
        if not err:
            # 无 err 也无已知文本签名：仍要留痕，不得静默吞掉红门
            gaps.append(Gap(
                file=Path(gate_name + ".txt"), abs_path=ROOT, line=1, col=1,
                code="gate_red_unknown",
                message=(f"门[{gate_name}] 判红但无 err/文本证据字段："
                         f"{ {k: v for k, v in entry.items() if k not in ('gate', 'file', 'ok')} }"[:200]),
                kind="text"))
            continue
        # ① 编译错误：块感知定位。cargo 输出的真实形态是
        #   `  error[E0599]: <msg>` 段 → 紧随 `  --> <path>:<line>:<col>`。
        # 旧实现把**所有** `-->`（含前面 warning 段的 `--> src\main.rs:50`）
        # 配给全局**第一处** error 码 → 派发到错误文件（模型永久接不到真缺口）。
        # 该缺陷属"包准"兑现点：此处按 error 段内紧随的 `-->` 精确配对。
        locs = _block_locations(err)
        if locs:
            # locs = (code, msg, file, line, col) 已按错误段配对
            for code, msg, f, ln, cl in locs:
                raw = Path(f)
                if raw.is_absolute():
                    try:
                        rel = raw.relative_to(ROOT)
                    except ValueError:
                        rel = Path(raw.name)
                else:
                    rel = raw
                abs_path = (ROOT / raw).resolve()
                gaps.append(Gap(file=rel, abs_path=abs_path, line=int(ln),
                                col=int(cl), code=code, message=msg[:200],
                                kind="compile"))
        else:
            # ② 挂起/失败门缺口：无行号 → 不是编译报错，是命令跑不完/超时
            if any(tk in err for tk in ("[SUBPROCESS_TIMEOUT", "[HANG_STALL",
                                        "error[", "panicked",
                                        "!!", "failures:", "warning: ")):
                gaps.append(Gap(
                    file=Path(gate_name + ".rs"), abs_path=ROOT, line=1, col=1,
                    code="run_hang",
                    message=(f"门[{gate_name}] 非编译错误地未通过（rc≠0 或超时）："
                             f"{err[:200]}")))
    # 去重：同 (file, code, line) 只留一条
    seen: set = set()
    out: list[Gap] = []
    for g in gaps:
        d = (g.file.as_posix(), g.code, g.line)
        if d in seen:
            continue
        seen.add(d)
        out.append(g)
    return out


def observation_gaps(verdict) -> list[Gap]:
    """从全称机制判定（观察机制主动全量扫）的违反转成可派发的缺口。

    每个违反 = 一个机器可算的「未做出某机制」缺口（真值 False），派发真实模型
    按机制基线改到机器门 + 再观测双过。同一文件同标记只留一条（一次修复带多处）。
    分类：基准/测试文件（src/bin/*基准.rs 等）只扫不改，跳过并标 skipped。"""
    gaps: list[Gap] = []
    seen: set = set()
    skipped: list[str] = []
    for v in verdict.violations:
        d = (v.rel, v.pattern)
        if d in seen:
            continue
        seen.add(d)
        rel = Path(v.rel)
        cls = obs_mod.classify_source(v.rel)
        if cls == "bench":
            skipped.append(f"{v.rel}@{v.pattern}")  # 基准/测试：语义是固定轮数，只扫不改
            continue
        gaps.append(Gap(
            file=rel, abs_path=(ROOT / v.rel).resolve(), line=v.line, col=v.col,
            code=v.pattern, kind="observation", phase_priority=1, cls=cls,
            message=(f"观测机制违反[{v.mechanism}]·{v.pattern} · {v.hint}")))
    # 语义：文件@标记 唯一 → 兼容账本去重 key 格式
    return gaps


def _selftest_gap_identity() -> list:
    """★★★★★ R4-F7/F8 判据力探针：缺口身份 + 错误段切分。

    ★ 这两条都是**静默**缺陷（不报错、只漏修），运行期门抓不到，
      只能靠**直接构造输入**的门来守。故独立成函数，供 `--selftest` 调。
    """
    坏: list = []

    # ── 门①（F7）：**同一文件同一码、不同行 ⇒ 必须是两个不同的缺口** ──
    _g100 = Gap(file=Path("src/x.rs"), abs_path=Path("/x"), line=100, col=1,
                code="E0433", message="m")
    _g200 = Gap(file=Path("src/x.rs"), abs_path=Path("/x"), line=200, col=1,
                code="E0433", message="m")
    if _g100.key == _g200.key:
        坏.append("R4-F7：同文件同码不同行被判成同一缺口 ⇒ 新错误会被账本静默跳过")
    if _g100.key == "src/x.rs@E0433":
        坏.append("R4-F7：缺口 key 仍不含行号")
    # 同一行同一码 ⇒ 仍是同一缺口（轮内"一次修复带多行"的语义不能被破坏）
    _g100b = Gap(file=Path("src/x.rs"), abs_path=Path("/x"), line=100, col=9,
                 code="E0433", message="m2")
    if _g100.key != _g100b.key:
        坏.append("R4-F7：同一行同一码应是同一缺口（轮内去重语义被破坏）")

    # ── 门②（F8）：**裸 `error:` 的真错误**必须收集到它的 `-->` ──
    _裸 = ("error: expected one of `,` or `>`, found `;`\n"
           "  --> src/y.rs:42:17\n"
           "   |\n"
           "42 |     let x = foo(;\n"
           "   |                 ^ expected one of `,` or `>`\n")
    _有码 = ("error[E0433]: failed to resolve\n"
             "  --> src/z.rs:7:5\n")
    _收尾 = ("error: could not compile `x` (bin \"x\") due to 1 previous error\n")
    _b1 = _block_locations(_裸)
    _b2 = _block_locations(_有码)
    _b3 = _block_locations(_收尾)
    if not _b1 or _b1[0][2] != "src/y.rs" or _b1[0][3] != 42:
        坏.append(f"R4-F8：裸 `error:` 的真错误没收集到 -->（得 {_b1}）"
                  " ⇒ 该错误永不派回炉")
    if not _b2 or _b2[0][0].strip("[]") != "E0433":
        坏.append(f"R4-F8：带码错误回归了（得 {_b2}）")
    # ★ 诚实登记（**我自己写错的探针**）：本门第一版断言 `_b2[0][0] == "E0433"`，
    #   当场变红。查下去发现**不是代码错**：`_block_locations` 返回的码本来就
    #   **带方括号**（正则捕获组含括号），下游 `develop.py:657`
    #   `code_plain = (gap.code or "").strip("[]")` 才去掉。
    #   ⇒ 是我的期望写错了，**改探针、不改代码**。现在用 `.strip("[]")` 比较 ——
    #   这才是语义上真正要判的东西（"码是不是 E0433"），也不依赖原始形态。
    if _b3:
        坏.append(f"R4-F8：无 --> 的收尾段不该产缺口（得 {_b3}）")

    # ── 门③（F8）：一段 error + 一段 warning，warning 的 --> 不得被误配 ──
    _混 = ("error[E0308]: mismatched types\n"
           "  --> src/a.rs:10:9\n"
           "warning: unused variable: `x`\n"
           "  --> src/a.rs:99:9\n")
    _b4 = _block_locations(_混)
    if len(_b4) != 1 or _b4[0][3] != 10:
        坏.append(f"R4-F8：warning 段的 --> 被误配到 error（得 {_b4}）")
    return 坏


def load_ledger_state() -> dict:
    """从账本恢复：已 resolved / blocked 的缺口 key -> Gap（供启动跳过）。"""
    state: dict = {}
    if not LEDGER.exists():
        return state
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for g in rec.get("gaps", []):
            if g.get("status") in ("resolved", "blocked"):
                state[g["key"]] = g
    return state


def record_round(rec: dict) -> None:
    rec["ts"] = time.time()
    rec["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    LIVE.write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    # 每轮（iteration）结束渲染一份人类可读 .md 回执；fix_attempt 只进账本不另开
    if rec.get("round") != "iteration":
        return
    try:
        batch = ts_receipt.claim_batch(ROOT)
        ts_receipt.render(
            repo_root=ROOT, batch=batch, window="develop",
            nature="执行回执（Rust 自主开发驱动 · 多智能体改源码）",
            topic=f"develop_Rust开发_{time.strftime('%m%d%H%M%S')}",
            summary=(f"**objective_status={rec.get('objective_status')}**；"
                     f"state={rec.get('state')}；锚定 {rec.get('anchor')}。"),
            gates=rec.get("gates"),
            gaps=rec.get("gaps"),
            notes=(["目标判据 " + (rec.get("objective_hint") or "")]
                   if rec.get("objective_status") != "done"
                   else ["目标门全绿（Objective done）。"]),
            extra_sections={"修复落盘":
                            "逐缺口经过 备份→应用方案→跑门→全绿保留/红门回滚 的原子事务，"
                            "见 develop_ledger.jsonl 与 develop_backup/。"})
    except Exception as e:  # noqa: BLE001 回执渲染失败不阻断开发闭环
        logger.error("[develop] 回执渲染失败：%s", e)


# ── 判据演化（meta-loop）：真值变对象 → 对象出结构 → 结构密度涌现行为 ──
# 模型对齐（用户拍板）：意图不是单独函数/载体，而是结构连接密度高之后的行为特征结果。
# 因此这里**没有**任何"意图/目标"表；只有真值累积成的对象结构。
# 复用（0-API 精准派发）只在结构密度达到 MIN_DENSITY 时才涌现——单次确证不算意图表演，
# 结构呈现一致 ≥MIN_DENSITY 次，'该模式该作何修'的意图才作为高密度行为显式化。
# 演化锚=独立真值门：对象只在「来源修正整门复跑全绿」后累积；每次复用再整门复跑，红则回滚。
MIN_DENSITY = 2   # 结构连接密度的最小涌现阈值（意图涌现的门槛）
def load_evolved() -> dict:
    """读判据演化账本：key=`rel@code` -> {old,new,hits,file}。机构已确证的对象。"""
    table: dict = {}
    if not EVOLVED.exists():
        return table
    for line in EVOLVED.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        table[rec["key"]] = rec   # 追加式账本，后写覆盖前条（最新命中计数为准）
    return table


def sediment_evolved(gap: Gap, patches: list, bump: bool = False) -> None:
    """沉淀判据演化对象：机制缺口被门确证的修正(O→N)记入账本供同模式复用。

    bump=True（或已有该 key 且本次为复用）→ 只把命中计数 +1，不重存 OLD/NEW。
    演化锚=独立真值门：新对象只在「其修正此刻仍与主线逐字匹配」且由门确证后才沉淀。
    仅 observation 机制缺口沉淀（compile/run_hang 缺口串味不沉淀）。"""
    if gap.kind != "observation":
        return
    table = load_evolved()
    if gap.key in table:
        base = dict(table[gap.key])
        base["hits"] = base.get("hits", 0) + 1
        base["ts"] = time.time()
        base["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with EVOLVED.open("a", encoding="utf-8") as f:
            f.write(json.dumps(base, ensure_ascii=False) + "\n")
        return
    if not patches:
        return
    old, new = patches[0]
    cur = gap.abs_path.read_text(encoding="utf-8", errors="ignore") if gap.abs_path.is_file() else ""
    if old not in cur:
        return   # 该修正与主线此刻不匹配，防串味沉淀
    rec = {"key": gap.key, "file": str(gap.file), "code": gap.code,
           "old": old, "new": new, "hits": 1,
           "ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    with EVOLVED.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def evolved_reuse(gap: Gap) -> tuple[str, bool]:
    """结构密度驱动的复用：对象在账本、结构一致密度≥MIN_DENSITY、主线此刻逐字唯一命中
    → 返 (新全文, True)。意图不作为单独函数，而是密度达标后这里才涌现出现。"""
    table = load_evolved()
    rec = table.get(gap.key)
    if not rec or rec.get("hits", 0) < MIN_DENSITY:
        return "", False
    orig = gap.abs_path.read_text(encoding="utf-8", errors="ignore")
    old, new = rec.get("old"), rec.get("new")
    if not old or orig.count(old) != 1:
        return "", False
    return orig.replace(old, new), True


_ROOT_REL_CACHE = {}
def _api_hints(gap: Gap, max_per_type: int = 60, max_types: int = 6) -> str:
    """库内真实 API 清单注入（提升 LLM 能力：不盲猜方法名）。

    compile 缺口（E0599/E0425 等引用不存在的 `机.读当前方向`）常因模型不知道库里
    真实存在哪些方法而反复猜错。此处从 Rust 索引扫真实 `pub fn 名称(`,按缺口文件
    `use`/引用到的模块给出该类型的真实方法清单,并标注缺口里那个未知方法名是否存在。
    这是机器真值（扫库所得）,不是模型建议;只喂给模型看,不代写码,门仍是模型方案过机门。
    """
    if gap.kind == "observation" or not gap.abs_path.is_file():
        return ""
    try:
        text = gap.abs_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""
    # 1) 抓该文件引用的模块路径（use 行 + 全限定 `foo::bar::baz::` + crate::路径）
    #    把类型路径（如 模块A::模块A）也归到其父模块（模块A），以匹配缓存 key
    mods: set[str] = set()
    for m in re.finditer(r"(?:use\s+|crate::)([\w\u4e00-\u9fff]+(?:::[a-zA-Z0-9_\u4e00-\u9fff]+)*)",
                         text):
        path = m.group(1).rstrip("::").replace("crate::", "")
        if not path:
            continue
        mods.add(path)
        segs = path.split("::")
        if len(segs) >= 2 and segs[-1] != "mod":
            mods.add("::".join(segs[:-1]))   # 父模块（含 mod.rs 所在层）
        elif len(segs) >= 2 and segs[-1] == "mod":
            mods.add("::".join(segs[:-1]))
    # 2) 扫库:文件路径(去掉 .rs + 各层) -> 该文件内 pub fn 名集
    #    缓存同时登记 `模块A::mod` 与其父 `模块A` 两个 key,以兼容
    #    `use ...::模块A::模块A` 归到父模块 与 目录即模块 两种 crate 组织。
    if not _ROOT_REL_CACHE:
        import os as _os
        prof = prof_mod.get_profile()
        globs = prof.source.globs or ("**/*.rs",)
        skip = prof.source.skip
        seen_paths: set = set()
        for g in globs:
            for p in sorted(ROOT.rglob(g)):
                if p in seen_paths or not p.is_file():
                    continue
                seen_paths.add(p)
                if skip and any(s in p.parts for s in skip):
                    continue
                low = p.read_text(encoding="utf-8", errors="ignore")
                names = sorted({m.group(1) for m in
                               re.finditer(r"pub\s+(?:unsafe\s+)?fn\s+([a-zA-Z0-9_\u4e00-\u9fff]+)\s*\(",
                                           low)})
                relmod = _os.path.splitext(p.relative_to(ROOT).as_posix())[0].replace("/", "::")
                _ROOT_REL_CACHE[relmod] = names
                if relmod.endswith("::mod"):
                    _ROOT_REL_CACHE.setdefault(relmod[:-4], names)
    # 3) 依据文件 use 到的模块,收集其真实方法名(并集、去重、截断)
    allnames: list[str] = []
    seen: set = set()
    used_mods = [m for m in mods if m in _ROOT_REL_CACHE]
    for m in used_mods[:max_types]:
        for n in _ROOT_REL_CACHE[m]:
            if n not in seen:
                seen.add(n)
                allnames.append(n)
    # 4) 检查缺口里的未知方法名是否真的不在库:从证据 message 里抽方法名形态
    unknown_hint = ""
    for m in re.finditer(r"no method named `([^`]+)`", gap.message):
        name = m.group(1)
        if name and all(name != n for n in seen):
            unknown_hint = (f"\n★ 缺口引用方法 `{name}` 在库内真实方法清单中**未找到**,"
                            f"需改用下列清单里语义匹配的真实方法名。")
    if not allnames:
        return ""
    listing = "、".join(allnames[:max_per_type])
    return (f"【库内真实 API（机器扫库所得,仅参考,改时以其为准）】\n"
            f"缺口文件引用到 {len(used_mods)} 个模块,其真实 `pub fn` 方法清单:\n"
            f"{listing}"
            f"{unknown_hint}")


FIX_PROMPT_TEMPLATE = """{ROLE}。当前编译判据门（机器判据）报告了下列编译缺口，
需要你产出**最小**的、能让该处编译通过的源码改动方案。

【待修复文件（相对工程根）】{file}
【缺口证据（行号=编译输出`-->`行号）】
{evidence}

【机器判据门上下文】
{GATE_NOTE}

{MECH}

【纪律约束】
- 只做让该缺口处编译通过的最小改动，不改功能语义，不新增/删除逻辑，不动无关文件。
- 不得引入 todo!()/unimplemented!()。

【输出格式（严格，供机械解析）】只输出如下结构，前后不要解释文字、不要代码块围栏：
每对替换操作先给**逐字原文本**（含足够上下文使它在整个文件中唯一），再给**新文本**。

@<<OLD>>
<逐字旧文本，必须与整文件逐字匹配且唯一>
@OLD>>
@<<NEW>>
<新文本>
@NEW>>
以上 OLD/NEW 块可重复多次，覆盖本文件所有缺口处。
注意：OLD 必须逐字取自原文件、且在整个文件中唯一命中，否则该块被丢弃。
""".strip()


def _api_tip_for(gap: "Gap") -> str:
    """按档案声明的 api_tips 匹配缺口，返回真实 API 事实提示（防模型盲猜）。

    档案格式（项目知识，工具不持有）：
      api_tips:
        - file_suffix: "某文件.rs"
          code_prefix: "E0"
          text: |
            【该缺口的真实 API 事实...】
    """
    raw = prof_mod.get_profile().raw.get("api_tips") or []
    posix = gap.file.as_posix()
    code_plain = (gap.code or "").strip("[]")
    for tip in raw:
        suf = str(tip.get("file_suffix") or "")
        pre = str(tip.get("code_prefix") or "")
        if suf and not posix.endswith(suf):
            continue
        if pre and not code_plain.startswith(pre):
            continue
        txt = str(tip.get("text") or "")
        if txt:
            return "\n\n" + txt.strip()
    return ""


def _写机制文件(路径, 文: str, *, 什么: str, 文件相对: str = "") -> None:
    """机制文件写入的**唯一出口** —— 六处调用点全部走这里（2026-11-03 接）。

    ## 为什么包一层，而不是在六处各打一个补丁

    这个文件里 `write_text` 到机制路径的地方是**六处**（第一遍我数成四处）：
    机械候选写入 / 机械红回滚 / 演化复用写入 / 复用红回滚 / 模型方案写入 / 模型红回滚。

    ⇒ 六处各打补丁 = **六次改一处漏五处的机会**。
      而本仓已经因为这个病栽过四次（编码环境、挑文件排序、必填项正则、
      规范化顺序），每一次都是"同一个缺陷在两个地方各写了一遍，只修了一个"。

    ⇒ 正确形态是**把写法收敛成一个出口**：以后新增调用点只有一个选择
      （调这个函数），不需要记得"还要发事件"。
      **这是把纪律变成结构** —— 靠人记的纪律迟早会漏。

    ## 回滚走不走这里？**走**。

    第一版我想过"回滚不算变更、跳过"。错了：
    回滚**真的改了文件**（内容从新变回旧）。不记的话，事件链上会出现
    "改了 → 门绿" 这种假象，而实际发生过"改了 → 门红 → 改回来"。
    ⇒ **回滚也要记**，但它带 `回滚=True` 标记，读的人能分清方向。

    ★ 绝不拖垮修复主链：任何异常被吞掉。
    """
    try:
        路径.write_text(文, encoding="utf-8")
    except Exception:
        raise                                   # 写入失败必须让调用方知道（这是真失败）
    try:
        from agentscope_orchestrator.fixloop import _事件
        _事件("文件变更", 文件相对 or str(路径),
              {"什么": 什么, "字节": len(文 or "")})
    except Exception:                           # noqa: BLE001 事件坏绝不影响修复主链
        pass


def _分工交代(gap: Gap, 证据: str, 根) -> str:
    """把「这一步该谁做」的裁定附到修复提示上（2026-11-03 接）。

    ## 为什么 develop 也要接分工线（不是只在 fixloop 接就够了）

    `develop` **不经过 `fixloop`** —— 它自己组提示（就是这个文件里的
    `build_fix_prompt`）。所以只在 fixloop 里接分工线，**这条主链完全不受影响**，
    而这条链恰恰是"无人值守自动修"用的那条 ⇒ 盲区台账在这条线上**一条都记不到**。

    ## 分工线在这里干什么

    顺序判：判据没命中 ⇒ 查知识库 ⇒ 查盲区台账 ⇒ 都没有才落到 LLM，
    并且**明说"为什么落到你这里"**。落到 LLM 的那一步顺手**记账**——
    这就是"下次该加什么门"的原料。

    ## 为什么是**加法式**（把交代放最前，而不是删掉原有的东西）

    还没有证据说"原来的提示组装方式更差"。贸然删会让"注入总量"这个可控量
    变不可控。⇒ 先**只加不删**，等 A/B 量出来再决定。

    ★ 分工线**绝不拖垮修复主链**：任何异常都返回空串（调用方拿到空串 = 行为不变）。
    """
    try:
        from agentscope_orchestrator import dispatch as _派遣
        裁 = _派遣.裁(命令=["develop", gap.file], 证据=证据, 项目根=根)
        if 裁.该谁 == "机器":
            return ""                      # 机器能判 ⇒ 不必在提示里多话
        if 裁.该谁 == "LLM":
            try:
                _派遣.记这次(证据=证据, 上下文=f"{gap.file}:{gap.line}",
                              来源="develop")
            except Exception:              # noqa: BLE001 记账失败绝不影响修复
                pass
        return _派遣.给LLM的交代(裁)
    except Exception:                      # noqa: BLE001 分工线坏了，行为退回原样
        return ""


def build_fix_prompt(gap: Gap, extras: str = "") -> str:
    file_txt = ""
    if gap.abs_path.is_file():
        raw = gap.abs_path.read_text(encoding="utf-8", errors="ignore")
        lo = max(0, gap.line - 6)
        hi = gap.line + 5
        lines = raw.splitlines()
        file_txt = "\n".join(
            f"{i+1:>5}| {lines[i] if i < len(lines) else ''}"
            for i in range(lo, min(hi, len(lines))))
    evidence = (
        f"文件 {gap.file} 行 {gap.line} 列 {gap.col}"
        f"{'  [' + gap.code + ']' if gap.code else ''}：{gap.message}"
    )
    # 编译缺口真实 API 清单（防模型盲猜）：由档案按 (文件后缀, 错误码前缀)
    # 声明 api_tips；工具不持有任何项目 API 事实。
    api_tip = _api_tip_for(gap)
    prof = prof_mod.get_profile()
    # ★ 分工线的交代放在**最前**（它决定"这一步的定位"，模型该先读到它）。
    _交代 = _分工交代(gap, evidence, gap.abs_path.parent if gap.abs_path else None)
    return (_交代 + "\n\n" if _交代 else "") + FIX_PROMPT_TEMPLATE.format(
        ROLE=prof.prompt("fix_role", "你是工程修复智能体"),
        file=gap.file, evidence=evidence,
        GATE_NOTE=prof.prompt("fix_gate_note"),
        MECH=math_model.get_charter()) + (
        api_tip + (f"\n\n{extras}" if extras else "") + (
            f"\n\n【该缺口附近源码片段（仅供参考，改时以整文件为准）】\n{file_txt}"))


OBS_FIX_PROMPT_TEMPLATE = """{ROLE}。观测机制（机器全量扫描）报告该文件存在
「违反数学模型机制」的缺口，需要你产出**最小**的、遵循机制基线的源码改动方案。

【待修复文件（相对工程根）】{file}
【观测缺口（机器判定，真值=False=该机制尚未做出）】
{evidence}

【机制基线（来自已证绿的证明，必须严格遵循）】
{MECH}

【纪律约束】
- 只做让该缺口处**符合机制**的最小改动：把「预设边界/限轮/候选池/写死档位/脚手架占位」
  换成以**结构演化的真值做机制**（自演化至一步不动点即停、读盘结构即得最优），
  不得新增传统骨架、外部上限、超时、候选池、限轮数，不得引入 todo!()/unimplemented!()。
- 若该处确实是合法业务判断而非机制（例如错误处理的边界校验），可保持不动并在
  方案里用一行 @KEEP>> 说明，避免对合规代码乱改。
- 不得改变函数对外语义/签名（除非为符合机制所必需）。

【输出格式（严格，供机械解析）】只输出如下结构，前后不要解释文字、不要代码块围栏：
每对替换操作先给**逐字原文本**（含足够上下文使它在整个文件中唯一），再给**新文本**。

@<<OLD>>
<逐字旧文本，必须与整文件逐字匹配且唯一>
@OLD>>
@<<NEW>>
<新文本>
@NEW>>
以上 OLD/NEW 块可重复多次；若判断无需改动，输出单个 @<<KEEP>>
keep
@KEEP>>
注意：OLD 必须逐字取自原文件、且在整个文件中唯一命中，否则该块被丢弃。
""".strip()


def build_obs_fix_prompt(gap: Gap, extras: str = "") -> str:
    file_txt = ""
    if gap.abs_path.is_file():
        raw = gap.abs_path.read_text(encoding="utf-8", errors="ignore")
        lo = max(0, gap.line - 6)
        hi = gap.line + 5
        lines = raw.splitlines()
        file_txt = "\n".join(
            f"{i+1:>5}| {lines[i] if i < len(lines) else ''}"
            for i in range(lo, min(hi, len(lines))))
    evidence = (f"文件 {gap.file} 行 {gap.line} 列 {gap.col} "
                f"[{gap.code}]：{gap.message}")
    prof = prof_mod.get_profile()
    # ★ 与 build_fix_prompt 同源的分工交代（两处**都要接** ——
    #   只接一处的话，另一条链上盲区就记不到；本仓已有过"改一处漏三处"的教训）。
    _交代 = _分工交代(gap, evidence, gap.abs_path.parent if gap.abs_path else None)
    return (_交代 + "\n\n" if _交代 else "") + OBS_FIX_PROMPT_TEMPLATE.format(
        ROLE=prof.prompt("obs_fix_role", "你是机制修复智能体"),
        file=gap.file, evidence=evidence,
        MECH=math_model.get_charter()) + (
        f"\n\n{extras}" if extras else "") + (
        f"\n\n【该缺口附近源码片段（仅供参考，改时以整文件为准）】\n{file_txt}")


_RE_OLD = re.compile(r"@<<OLD>>\n(.*?)\n@OLD>>", re.DOTALL)
_RE_NEW = re.compile(r"@<<NEW>>\n(.*?)\n@NEW>>", re.DOTALL)


def parse_patches(model_text: str, orig: str) -> tuple[list, list]:
    """解析模型输出 OLD/NEW 块。返回 (patches, reasons_skipped)。"""
    olds = [m.group(1) for m in _RE_OLD.finditer(model_text)]
    news = [m.group(1) for m in _RE_NEW.finditer(model_text)]
    patches: list = []
    skipped: list = []
    for old, new in zip(olds, news):
        if old == new:
            skipped.append("OLD==NEW，无实际改动")
            continue
        if old not in orig:
            skipped.append(f"OLD 未命中（{old[:50]!r}）")
            continue
        if orig.count(old) > 1:
            skipped.append(f"OLD 不唯一（命中{orig.count(old)}处）：{old[:50]!r}")
            continue
        patches.append((old, new))
    if len(olds) != len(news):
        skipped.append(f"OLD({len(olds)}) 与 NEW({len(news)}) 块数不一致")
    return patches, skipped


def apply_patches(orig: str, patches: list) -> tuple[str, bool]:
    t = orig
    ok = len(patches) > 0
    for old, new in patches:
        if old not in t or t.count(old) != 1:
            ok = False
            break
        t = t.replace(old, new)
    return t, ok


_SELF_IDENT = re.compile(r"Self::([A-Za-z0-9_\u4e00-\u9fff]+)")
_MECH_RULES = 0  # 命中走机械分支的缺口数（本次驱动内计数，供账本反映省了 API）


def mechanical_candidates(gap: Gap, orig: str) -> list[str]:
    """确定性机械修复候选（机器本地自动接线，不调模型）。

    现有规则（gate_spec 会验证，猜错即回滚，零残留）：
      - E0433 `Self::NAME` 在 impl 外非法 → 把该名字的 `Self::NAME` 全部改为
        `父模块名::NAME`（即 dry 修法所用的同一类机械替换）。
    未来规则在此追加。每次新增规则都是"多一个本地自动，少一次 AI 手工"。
    """
    cands: list[str] = []
    lines = orig.splitlines()
    name: str | None = None
    if 0 < gap.line <= len(lines):
        m = _SELF_IDENT.search(lines[gap.line - 1])
        if m:
            name = m.group(1)
    parent_mod = gap.file.parts[-2] if len(gap.file.parts) >= 2 else None
    if parent_mod and name:
        t = orig.replace(f"Self::{name}", f"{parent_mod}::{name}")
        if t != orig:
            cands.append(t)
    return list(dict.fromkeys(cands))


def is_objective_done(results: dict) -> bool:
    """目标完成判据 = 档案声明的目标门全绿。

    目标门名由档案 objective_gates 给出（工具不持有任何门名）。
    档案未声明 ⇒ 永不判完成（不假装达成）。
    """
    return prof_mod.get_profile().is_objective_done(results)


class DevelopDriver:
    def __init__(self, channel_name: str = "", dry: bool = False,
                 force: bool = False, max_attempts: int = 4):
        # ★★★★★ R157（2026-10-25）：**改走统一的自适应通道入口。**
        #   原来这里（以及 `real_task.py:392`）是**静态**的：
        #     `channels = select_channels(channel_name)`   ← 配置口径
        #     `pairs = [(c.name, build_client(c, c.models[0]))]`  ← 只取第一个模型
        #   ★ 实测（本仓此刻）：`select_live_channels("")` 探出
        #     `能用: ['dddddd'] / 硬死: ['aaa','ggg','mf']`
        #     —— 而静态口径会把**3 个死通道**交给 Router，且每个通道只给 1 个模型
        #     （`dddddd` 声明了 `kimi-k3`，永远用不到）。
        #   ⇒ 这是「修了一侧，另一侧没修」的第 8 次：
        #     R50/R18/R72 全都在 `generic_goal` 上修过，这两条入口没修。
        #   ⇒ 现在**不抄了**，统一问 `build_adaptive_router`。
        from agentscope_orchestrator.gateway import build_adaptive_router
        self.router, self.channels, self.channel_report = build_adaptive_router(
            channel_name, probe=not dry)
        self.dry = dry
        self.force = force
        self.max_attempts = max_attempts
        self._suite = []
        self._idx = None
        self._mem = None          # 结构记忆（按需增量观测；供取象注入 prompt）
        # ★ 轮级事务快照（2026-10-12）：{绝对路径: 轮初原文}。
        #   逐缺口级的绿保/红滚只管单缺口；轮末全量门是唯一能看见"跨缺口回归"的门，
        #   它红了必须能把本轮净改动整体撤销。_snapshot_round() 在每轮开头填充。
        self._round_backups: dict[str, str] = {}

    def _snapshot_round(self, paths: list[Path]) -> None:
        """轮初快照：记录本轮可能改动的文件原文，供轮末全量门红时整体回滚。

        ★ 修复（2026-10-12）：原实现**没有轮级快照**，轮末全量验收门红只记进账本、
        改动留在工作树 ⇒ "编译得过、行为是错的"改动被永久接受。
        只对**已存在**的文件做快照：新建文件由逐缺口事务负责删除，不在此重复。"""
        self._round_backups = {}
        for p in {Path(x) for x in paths}:
            try:
                if p.is_file():
                    self._round_backups[str(p)] = p.read_text(
                        encoding="utf-8", errors="replace")
            except OSError as exc:
                _log("[develop] 轮快照失败 %s：%s", p, exc)

    # ── 门套件（分层关卡：快验证门轮内用 · 全量验收门每轮末跑一次）──
    def _dev_suite(self) -> list:
        """快验证门（每轮 / 每缺口落盘内循环用，秒级）= 档案 dev_gates + 工具自检门。

        编译/构建/脚手架扫描等**项目级**门全部来自档案（工具不持有命令）。
        工具自身机制的守卫门（auto_mechanism / auto_goal / doc_store /
        auto_objective 自检）属**工具侧**，与项目无关，故在此固定挂载。
        """
        _proj = [dict(g) for g in prof_mod.get_profile().dev_gates]
        # ★★★★★ 修复 R4-F10（**fail-closed**）：套件里**必须至少有一条项目级门**。
        #   下面挂的全是**工具自检门**（工具自身的机制守卫，与被检项目无关）。
        #   若档案没声明 dev_gates，套件就退化成"全在检查编排器自己"
        #   ⇒ **达标判定不独立于被检对象** ⇒ 无论项目多烂都判绿（恒真门）。
        #   故此处**硬拒**：宁可停下让人去补档案，也不出一份恒真的绿。
        if not _proj:
            raise RuntimeError(
                "dev_suite 退化：档案未声明任何 dev_gates（项目级门），"
                "套件将只剩工具自检门 ⇒ 达标判定不独立于被检对象（恒真）。"
                "请在 orchestrator.profile.yml 的 dev_gates 里声明至少一条"
                "作用于被检项目的门（如 compile/grep）。")
        suite = _proj
        suite.extend([
        # 已落地自动全称机制工具位守卫门：0-LLM 机选内核/自动接线/自动构造，
        # 每条自检对应一个已机证 Lean 定理的工程等价断言，全绿才视为工具位健康。
        {"type": "compile", "name": "auto_mechanism_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "auto_mechanism.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0},
        # 目标→任务序列 机器计划器守卫门：载体合法/规则升阶 RA-03/接线守恒，
        # 全绿才允许计划器参与编排（0-LLM 分解，语义映射仍由真模型做）。
        {"type": "compile", "name": "auto_goal_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "auto_goal.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0},
        # 文档外置+自建索引守卫门：长内容落盘/按需读取/越界拒绝 的机器断言，
        # 全绿才视为「对话窗口只留记录、其余以文档按需查看」机制健康。
        {"type": "compile", "name": "doc_store_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "doc_store.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 120},
        # ★★★★★ R8-F12：**全库静态门** —— 扫出「被读却从未定义的全局名」。
        #   这一类缺陷**逃过所有运行期门**（那些门从不走那条路径），
        #   本轮实测两例 P0：`taixu_mcp._RUST_ROOT`（「任意任务」工具完全不可用）、
        #   `observation.logger`（漏读账一写就崩），另有 `generic_goal` 自检里
        #   一处 `repo` 未定义（**门一红就崩，失败原因永远打不出来**）。
        #   ⇒ 挂成常驻门，让这一类**长期可判定**，不是修完这轮就没人守。
        {"type": "compile", "name": "static_audit_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "static_audit.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 120},
        # ★★★★★ R9-F12：**端到端真跑门** —— 这两件是抓「属性读不存在」这一类
        #   缺陷的**根本手段**，因为那一类**只有真的走一遍才现形**：
        #   实测 `taixu_mcp` 调 `rt.REPO_ROOT` 而 `real_task` 从未暴露它
        #   ⇒ `run_errand` / `submit_task` **每次调用都崩**，
        #     而所有"直接调函数"的门**全部是绿的**（它们从不走这条路）。
        #
        #   ① `_编排器落地验证.py`（实测 7s）：真提交/真调度/真执行/真判据/真落库，
        #      每条都对着一个本轮修掉的真缺陷，且**反向验证**（缺陷放回必须红）。
        #   ② `_编排器协议验证.py`（实测 9s）：**真起 MCP 服务 + 真过 stdio 协议**
        #      握手、列工具、调工具、真跑一条命令并拿到 promoted。
        #      ⇒ 它证明的不是"函数对"，而是"**从对话里一调就能跑**"。
        #   ★ 两件都独立于被检项目（查的是编排器本体），故与 dev_gates 并列而非替代。
        #   ★ 两件都放在**工具自己的** `checks/` 里（产品件随工具走），
        #     且路径从 `__file__` 推导、解释器用当前 `sys.executable`
        #     —— 不写死本机绝对路径，换机即可跑。
        {"type": "compile", "name": "e2e_landing_verify",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "落地验证.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 300},
        {"type": "compile", "name": "e2e_protocol_verify",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "协议验证.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 300},
        # ★★★★★ R10-F12：**观测坐标门** —— 观测机制的全部价值在于
        #   「报出的位置**真是**违规的位置」。位置一旦错位，修复环就会**改错地方**，
        #   而所有门**照样全绿**（它们只看"报了几条"，不看"报得对不对"）。
        #   实测缺陷：`_lex_strip` 剥注释连换行一起删 ⇒
        #   `src/运行时样本.rs` 原 4764 行 → 清洗后 1729 行，**错位 3035 行**，
        #   报出的 `:301:21` 在原文件里是**注释**。
        #   ⇒ 这条门直接判**坐标**，不判数量。
        {"type": "compile", "name": "observation_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "observation.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 180},
        # 第二层闭环守卫门：偏差结构→目标函数（auto_objective）机器自检——防平凡
        # 不动点（对照独立真值门禁放松已绿门）/损坏行容忍/幂等回读/不震荡，
        # 全绿才允许"偏差→全局目标"参与编排（0-LLM，目标=偏差密度函数非意图表）。
        {"type": "compile", "name": "auto_objective_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "auto_objective.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0},
        # ★★★★★ R13–R17：**数术逻辑层门**（TS-468）。顶层此前全程在**经典层**
        #   工作（`Bool` + `all` + `and` + 另打补丁 + 枚举变异），而经典层里
        #   「平衡/保真/生成/判定/增信/求解」每一样都要**付排中律**。
        #   本层把顶层挪回数术层：保真 = **交换律**（不是"存在 Bool 扛住真值"）；
        #   生成 = **无不动点**；求解 = **翻转**（恰好 2 步，O(1)，**不搜索**）；
        #   增信 = **沿格上升**（判定性来自结构 ⇒ 免费）。
        #   ★ 一条**保真方程**取代两个补丁：**假绿与假红是同一个违反**
        #     （轨道退化为不动点）。
        {"type": "compile", "name": "shushu_logic_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "shushu_logic.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 180},
        # ★★★★★ R18：**通道探活门**。本模块此前**没有自检**，而它正是那个
        #   让**整台编排器拒跑**的缺陷所在：探活把「慢」（超时）判成「死」
        #   （401 凭据错）—— 同一个桶 ⇒ 一个活的慢通道被判死 ⇒ 拒跑。
        #   ★ 与数术层同一条原理：**「读不到」≠「违反」，处置不同**。
        {"type": "compile", "name": "gateway_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "gateway.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 300},
        # ★ 齿门（R13）：σ 必须是**已验平衡**。缺了这一步，保真方程对任何门
        #   都不成立 ⇒ **真门被误判恒真**（假红）⇒ 白白派生替换门、烧 token。
        #   同时守 R11 引入的**目录读空间假绿**（目录门恒判 unknown 却照样通过）。
        {"type": "compile", "name": "gate_witness_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "gate_witness.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 300},
        # ★★★★★ 齿门（R16/R17）：把顶层判定**退回经典层**，自检必须**变红**。
        #   一条门如果"改回去也全绿"，它就是**无齿门**（教人忽略红）。
        #   本探针在**原位**回退、`finally` **字节级还原**。
        {"type": "compile", "name": "shushu_teeth_probe",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "数术层齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 1800},
        {"type": "compile", "name": "shushu_teeth_probe_r17",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "接线齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 1800},
        # ★ R18 齿：把探活退回**两桶**（慢=死）⇒ 自检必须变红。
        {"type": "compile", "name": "probe_teeth_r18",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "探活齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 900},
        # ★★★★★ R19 齿：把 `_验证可分开` 退回「只查分开不查方向」⇒ 自检必须变红。
        #   这是真跑抓出的**严重**缺陷：派生门反向 ⇒ 只让坏产物过 ⇒ 烧 17895 字符。
        {"type": "compile", "name": "direction_teeth_r19",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "方向齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 1200},
        # ★ R19：判别式派生自检（含**方向门**：反向门必须被拒）
        {"type": "compile", "name": "discriminant_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "discriminant.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 900},
        # ★★★★★ R20（TS-469）：**机制自己真值空间** `Fin n` 上的闭环。
        #   判定层原来住在经典层（六值字符串 + all/and）；TS-469 机证
        #   真值空间是 `Fin n`、**没有第三值** ⇒ 六值字符串是**因由**，不是真值。
        {"type": "compile", "name": "truth_space_selftest",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parent / "truth_space.py"),
                 "--selftest"],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 900},
        # ★ R20 齿：把真值空间接线逐条退回（含穷尽、载体消去）⇒ 自检必须变红。
        {"type": "compile", "name": "truth_space_teeth_r20",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "真值空间齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 1800},
        # ════════════════════════════════════════════════════════════
        # R21–R23（2026-10-15 · **真跑一个真 Rust 工程**跑出来的三个产品缺口）
        #   R18/R19 是"跑出来的"，R21/R22/R23 同样是 —— 而 R20 是"读出来的"。
        #   这三条都不是数学，是**工程**：主树被改坏还报成功、静默重写行尾、
        #   判据（验收测试）由被判方持有。
        # ════════════════════════════════════════════════════════════
        # ★★★★★ R21：主树交付前必须过**项目自己的验收门**（档案声明·人写的）。
        #   原来只信**模型自己写的门** ⇒ 模型写弱门就能让主树被改坏还报成功交付。
        #   齿：真造一个 `cargo test` 真红的 Rust 工程，把"没真修"的改动应用进主树，
        #       项目验收门红 ⇒ 必须**回滚主树 + 不交付**；回退该守卫 ⇒ 坏主树被交付。
        {"type": "compile", "name": "project_acceptance_teeth_r21",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "项目验收齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 1800},
        # ★★★★★ R22：主树落盘必须**字节级**（行尾不得被翻译 · 非法字节不得被丢）。
        #   实测：编排器改过的 `src/lib.rs` CRLF 145/LF 0，没碰过的 `Cargo.toml` LF 11；
        #         `read_text(errors="ignore")` 让 38 字节回滚成 35 字节
        #         ⇒ 「红滚」这个安全网**自己有损**。
        #   齿：把唯一落盘实现 / 备份 / 回滚 / 产物拷贝逐条退回有损写法 ⇒ 自检必须变红。
        {"type": "compile", "name": "byte_write_teeth_r22",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "落盘字节齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 2400},
        # ★★★★★ R23：**判据不得由被判方改动**（档案声明 `frozen` 冻结区）。
        #   验收测试常与实现**同文件** ⇒ 模型改测试就能让自己过；
        #   而目标里那句「测试一条都不能改」在 R23 之前**没有任何机器门**。
        #   齿：真改弱验收测试 ⇒ 必须回滚拒交；只改实现区 ⇒ 必须照常交付（防假红）。
        {"type": "compile", "name": "frozen_region_teeth_r23",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "冻结区齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 1800},
        # ════════════════════════════════════════════════════════════
        # R25–R27（2026-10-15 · **一句话入口**跑出来的）
        #   用户要的产品形态是「我直接给你对话，你就能启动的」。
        #   补齐这条路上缺的三块：**语法门**（"无自检"掩盖"读不了"）、
        #   **档案自举**（用户不该手写档案）、**一句话入口**（用户只给一句话）。
        # ════════════════════════════════════════════════════════════
        # ★ 全称语法门：**每个模块都必须能编译**。
        #   回归里 17 个模块记为「无自检」—— 而"无自检"只意味着没跑 `--selftest`，
        #   它**不检查模块本身能不能编译** ⇒ 读不了的模块与正常模块**看起来一样**。
        #   本门不需要模块自己配合（不像 `--selftest`），对**每一个** `.py` 都成立。
        {"type": "compile", "name": "syntax_gate",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "语法门.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 900},
        # ★ 语法门齿：真放一个语法错的模块进去 ⇒ 必须红且**点名**；空目录 ⇒ 必须红
        #   （"什么都没扫到"不得当成通过）。
        {"type": "compile", "name": "syntax_teeth",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "语法齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 900},
        # ★★★★★ 自举齿（**一句话入口**的核心）：排除机制 + R25 逐条退回 ⇒ 自检必须红。
        #   并**证明**「声明了冻结区却静默不生效」真实存在（真调生产代码）。
        {"type": "compile", "name": "bootstrap_teeth",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "自举齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 2400},
        # ★ 一句话入口：给一句话目标 + 一个工程目录 ⇒ 自己认工程、自己造档案、自己跑。
        #   齿：不可识别的工程必须**拒跑**（不是硬上）；延迟导入必须让 REPO_ROOT 正确；
        #       未知参数必须**原样透传**（`--n` 被 `--no-probe` 缩写吃掉是抓出来的真缺陷）。
        {"type": "compile", "name": "one_sentence_entry",
         "cmd": [sys.executable,
                 str(Path(__file__).resolve().parents[1] / "checks" / "入口齿探针.py")],
         "cwd": str(Path(__file__).resolve().parents[2]),
         "expect_exit": 0, "timeout": 1800},
        ])
        return suite

    def _full_suite(self) -> list:
        """全量验收门（每轮末判 objective done 用）：快验证门 + 档案声明的重门。

        档案里 dev_gates 之外的工程级门（如全量测试）在轮末补跑一次。
        """
        suite = self._dev_suite()
        seen = {g.get("name") for g in suite}
        for g in prof_mod.get_profile().gates:
            if g.get("name") not in seen:
                suite.append(dict(g))
        return suite

    def scan_gates(self) -> dict:
        idx = engine_dev.build_rust_index()
        anchor_main = engine_dev.anchor(idx, prof_mod.get_profile().anchors.main)
        if anchor_main is None:
            raise RuntimeError("锚定失败：main 未入 Rust 索引，拒跑硬化门")
        suite = self._dev_suite()
        results, audit = evaluate(suite, anchor_main, _WORK, str(ROOT))
        return {"idx": idx, "anchor": str(anchor_main), "results": results,
                "audit": audit}

    def _gate_score(self) -> tuple:
        """轮内验证门（秒级，用于每缺口落盘绿保/红滚）。"""
        anchor_main = engine_dev.anchor(self._idx, prof_mod.get_profile().anchors.main)
        return evaluate(self._dev_suite(), anchor_main, _WORK, str(ROOT))

    def _struct_context(self, gap: Gap) -> str:
        """从结构记忆取目标文件之「象」（签名+依赖+机制命中），按需注入 prompt。

        提升 LLM 能力：让模型看到该单元的**结构**（取象），而不只看到缺口附近局部
        片段——这是"结构求真值→真值引结构→模型越省词元越精准"的机器对应。只在
        prompt 里带该文件这一个单元的象，不塞全量上下文。无记忆/未扫描时安静为空。
        """
        if self._mem is None or not gap.abs_path.is_file():
            return ""
        try:
            text = gap.abs_path.read_text(encoding="utf-8", errors="ignore")
            img = self._mem.image(gap.file.as_posix(), text)
        except Exception:  # noqa: BLE001 取象失败不阻断修复，空即可
            return ""
        parts = []
        if img.signatures:
            parts.append("签名=" + ", ".join(img.signatures[:12]))
        if img.deps:
            parts.append("依赖=" + ", ".join(img.deps[:12]))
        marks = [m["name"] for m in img.markers]
        if marks:
            parts.append("机制命中=" + ", ".join(sorted(set(marks))[:6]))
        if not parts:
            return ""
        return ("【该单元结构取象（机器，来自结构记忆）· 仅此单元，不塞全量】\n"
                + "；".join(parts))

    # ── Gap 修复 ─────────────────────────────────────────────
    async def _diagnose_hang(self, gap: Gap) -> Gap:
        """派真实模型做「运行时挂起/超时门」机制诊断。

        背景：compile 门 rc≠0 或超时、却无编译错误行号 → 是命令跑不完/慢，不是编译错。
        这类缺口此前因无 `-->` 行号而不派发 → 工具哑火卡死。现在派真实模型读源码，
        判断该门（测试/引擎函数）为何不按机制收敛，给出修复方向。产物=诊断建议
        （记账本 blocked+方向），不改码，让工具能继续下一轮而不卡死。
        """
        attempt = 0
        prompt = (
            f"{prof_mod.get_profile().prompt('diag_role', '你是多智能体诊断角色')}。"
            "机器判据门报告：\n"
            f"{gap.message}\n\n"
            "诊断后，请按如下格式输出（供机械解析，重要）：\n"
            "诊断结论：<一两句：违反哪条机制、为何不收敛>\n"
            "建议修复文件：<相对工程根的源文件路径>"
            "（若确实无法定位到具体文件则写：无）\n"
            "建议改造点：<一两句，按机制应改成什么样的收敛判据>（不要输出代码）"
        )
        while attempt < self.max_attempts:
            attempt += 1
            gap.attempts = attempt
            try:
                if self.dry:
                    verdict = ("诊断结论：（dry）机制基线已注入，疑似门命令未按"
                               "自演化收敛。\n建议修复文件：无\n建议改造点：循环应改为"
                               "自演化至一步到不动点即停，不预先限轮数。")
                else:
                    resp = await self.router.call(
                        [UserMsg(name="user", content=prompt)])
                    verdict = _strip_fence(_extract_text(resp.content))[:400]
                gap.error = f"诊断方向：{verdict}"
                gap.status = "blocked"   # 诊断产出方向；无文件则本轮不改码
                record_round({"round": "fix_attempt", "gap_key": gap.key,
                              "attempt": attempt, "status": gap.status,
                              "source": "diagnose_hang", "diagnosis": verdict})
                logger.info("  [gap %s][run_hang] 诊断：%.120s", gap.key, verdict)
                return verdict            # 返回诊断文本，供定位修复文件
            except Exception as e:  # noqa: BLE001 通道瞬态失败不整死
                gap.error = f"模型通道失败(第{attempt}次)：{e}"
                gap.status = "blocked" if attempt >= self.max_attempts else "open"
                logger.error("  [gap %s][run_hang] %s", gap.key, gap.error)
                if attempt >= self.max_attempts:
                    record_round({"round": "fix_attempt", "gap_key": gap.key,
                                  "attempt": attempt, "status": gap.status,
                                  "error": gap.error})
                if attempt >= self.max_attempts:
                    return ""
        return ""

    def _locate_hang_target(self, verdict: str) -> Path | None:
        """从诊断文本解析『建议修复文件』路径；命中 ROOT 下真实 .rs 才返回。"""
        import os
        for line in (verdict or "").splitlines():
            if "建议修复文件" not in line and "修复文件" not in line:
                continue
            for tok in line.split():
                t = tok.strip("：:，,。\t").strip()
                if t.endswith(".rs") and not t.startswith("无"):
                    cand = (ROOT / t.lstrip("/\\")).resolve()
                    if cand.suffix == ".rs" and cand.is_file():
                        return cand
        # 兜底：文本里任何 *.rs 路径
        import re as _re
        m = _re.findall(r"[\w/\\]+\.rs", verdict or "")
        for t in reversed(m):
            cand = (ROOT / t.lstrip("/\\")).resolve()
            if cand.suffix == ".rs" and cand.is_file():
                return cand
        return None

    async def fix_one_gap(self, gap: Gap) -> Gap:
        # run_hang 缺口：不是编译错误，是某门命令跑不完/超时。
        # abs_path=ROOT（无具体文件）→ 先派真实模型诊断，从诊断里解析出
        # 「建议修复文件路径」；命中真实 .rs 文件后，重定位 gap 指向它，
        # 进入通用 AI 落盘事务（产出 OLD/NEW 补丁 → 过门 → 绿保/红滚）。
        if gap.code == "run_hang":
            verdict = await self._diagnose_hang(gap)   # 诊断，返回文本方向
            target = self._locate_hang_target(verdict) # 从方向解析候选文件
            if target is not None:
                gap.file = Path(target).relative_to(ROOT)
                gap.abs_path = (ROOT / target).resolve()
                gap.line = 1
                gap.code = "hang_fix"
            else:
                return gap   # 诊断无具体文件 → 保留 blocked，等下一轮

        # 多窗并发防护：目标文件加跨进程互斥锁。另一窗正在修同一文件 → 本窗跳过
        # （不重复修、不覆盖、不备份错乱），根治多窗口并发撞文件的联动毛病。
        _wl_held = window_lock.acquire(AUDIT, gap.abs_path, timeout=0.0)
        if not _wl_held:
            gap.status = "blocked"
            gap.error = "另一窗口正在修复同一文件（多窗互斥）→ 本窗跳过，不重复修"
            record_round({"round": "fix_attempt", "gap_key": gap.key,
                          "attempt": 0, "status": gap.status,
                          "source": "window_conflict",
                          "error": gap.error})
            logger.info("  [gap %s] 多窗冲突：另一窗在修，跳过", gap.key)
            return gap

        orig = gap.abs_path.read_text(encoding="utf-8", errors="ignore")
        rag = f"（file={gap.file.name} line={gap.line} code={gap.code}）"
        # 本地机械先行：先用确定性规则自动接线（机器门验证），全红才落 AI 手工
        try:
            for mech in mechanical_candidates(gap, orig):
                if mech == orig:
                    continue
                stage = _WORK / f"{gap.file.name}.mech_candidate"
                stage.write_text(mech, encoding="utf-8")
                bak = BACKUP / f"{gap.file.name}.{int(time.time())}.mech.bak"
                bak.write_text(orig, encoding="utf-8")
                _写机制文件(gap.abs_path, mech, 什么="机械候选", 文件相对=gap.file.as_posix())
                try:
                    results, _ = self._gate_score()
                except Exception as e:  # noqa: BLE001
                    logger.warning("  [gap %s] 机械门异常：%s", rag, e)
                    results = {}
                # ★ R14：数术层格裁决（取值等价于 `results and all(...)`）
                from agentscope_orchestrator import shushu_logic as _sl14
                if _sl14.从门结果(results)[0]:
                    gap.status = "resolved"
                    gap.attempts = 1
                    gap.error = ""
                    logger.info("  [gap %s] 本地机械自动接线即绿（零 API）：%s",
                                rag, results)
                    record_round({"round": "fix_attempt", "gap_key": gap.key,
                                  "attempt": 1, "status": "resolved",
                                  "source": "local_mechanical",
                                  "gates": results, "backup": str(bak),
                                  "patch_blocks": 0})
                    window_lock.release(AUDIT, gap.abs_path)
                    return gap
                _写机制文件(gap.abs_path, orig, 什么="机械红回滚", 文件相对=gap.file.as_posix())
        except Exception as e:  # noqa: BLE001 机械层异常不阻断，回退 AI 路径
            logger.warning("  [gap %s] 机械层异常，回退 AI：%s", rag, e)
        # 判据演化复用：对象在账本且主线此刻逐字命中 → 机械应用→整门复跑→绿保/红滚让位 AI。
        # dry 不真改机制代码，跳过。每次复用_必整门复跑_全绿才保留（独立真值锚，不放松已绿门）。
        if not (self.dry and gap.kind == "observation"):
            cand, hit = evolved_reuse(gap)
            if hit and cand != orig:
                stage = _WORK / f"{gap.file.name}.evolved_candidate"
                stage.write_text(cand, encoding="utf-8")
                bak = BACKUP / f"{gap.file.name}.{int(time.time())}.evolved.bak"
                bak.write_text(orig, encoding="utf-8")
                _写机制文件(gap.abs_path, cand, 什么="演化复用", 文件相对=gap.file.as_posix())
                try:
                    results_, _ = self._gate_score()
                except Exception as e:  # noqa: BLE001
                    results_ = {}
                if results_ and all(results_.values()):
                    gap.status = "resolved"
                    gap.attempts = 1
                    gap.error = ""
                    sediment_evolved(gap, [])   # 复用命中：已有对象 → 计数+1
                    logger.info("  [gap %s] 判据演化复用即绿（零 API）：%s", rag, results_)
                    record_round({"round": "fix_attempt", "gap_key": gap.key,
                                  "attempt": 1, "status": "resolved",
                                  "source": "criteria_evolve", "gates": results_,
                                  "backup": str(bak), "patch_blocks": 0})
                    window_lock.release(AUDIT, gap.abs_path)
                    return gap
                _写机制文件(gap.abs_path, orig, 什么="复用红回滚", 文件相对=gap.file.as_posix())
                logger.info("  [gap %s] 判据演化复用红→回滚，让位 AI", rag)
        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            gap.attempts = attempt
            struct_ctx = self._struct_context(gap)   # 结构取象注入（提升 LLM 能力）
            api_hint = _api_hints(gap)               # 库内真实 API 清单（compile 缺口用）
            # 工具调用环：缺口规约结构真值 -> auto_mechanism 机器演化候选骨架，注入
            # prompt 作参考变体。真模型据此产 OLD/NEW（以机器演化的结构骨架为准，非
            # 臆造）；本驱动不改引擎源码，落盘仍由机器判据门唯一裁决。
            tool_hint = ao_mech.tool_brief(gap.file.stem, gap.code)
            extras = "\n".join(x for x in (struct_ctx, api_hint, tool_hint) if x)
            prompt = (build_obs_fix_prompt(gap, extras=extras)
                      if gap.kind == "observation"
                      else build_fix_prompt(gap, extras=extras))
            try:
                if self.dry:
                    if gap.kind == "observation":
                        # dry 不真改机制代码：观测缺口如实标 blocked，不伪装落地
                        gap.status = "blocked"
                        gap.error = "dry 模式不真改机制代码（观测缺口不伪装落地）"
                        record_round({"round": "fix_attempt", "gap_key": gap.key,
                                      "attempt": attempt, "status": gap.status,
                                      "source": "observation_dry", "error": gap.error})
                        logger.info("  [gap %s][observation] dry 跳过不改码", rag)
                        window_lock.release(AUDIT, gap.abs_path)
                        return gap
                    # dry 用档案声明的已知可行替换对验证管线（工具不持有项目文本）
                    pairs = prof_mod.get_profile().raw.get("dry_fix_pairs") or []
                    model_text = orig
                    for pr in pairs:
                        old_t = str(pr.get("old") or "")
                        new_t = str(pr.get("new") or "")
                        if old_t and new_t and old_t in model_text:
                            model_text = model_text.replace(old_t, new_t, 1)
                            break
                else:
                    resp = await self.router.call([UserMsg(name="user", content=prompt)])
                    model_text = _strip_fence(_extract_text(resp.content))
                    if "@KEEP>>" in model_text:
                        # 模型判定该处合规（与观测标记冲突）→ 如实阻塞留人工复核，不再重派
                        gap.status = "blocked"
                        gap.error = "模型判定该处合规机制无需改动（与观测标记冲突，待人工复核）"
                        record_round({"round": "fix_attempt", "gap_key": gap.key,
                                      "attempt": attempt, "status": gap.status,
                                      "source": "observation_keep", "error": gap.error})
                        logger.info("  [gap %s][observation] 模型判定合规，阻塞留核", rag)
                        window_lock.release(AUDIT, gap.abs_path)
                        return gap
            except Exception as e:  # noqa: BLE001 通道瞬态失败不整死
                gap.error = f"模型通道失败(第{attempt}次)：{e}"
                gap.status = "blocked" if attempt >= self.max_attempts else "open"
                logger.error("  [gap %s] %s", rag, gap.error)
                record_round({"round": "fix_attempt", "gap_key": gap.key,
                              "attempt": attempt, "status": gap.status,
                              "error": gap.error})
                continue

            if self.dry:
                # dry：全文件替换文本即候选，跳过 OLD/NEW 解析
                candidate, applied = model_text, True
                patches, skipped = [], []
            else:
                patches, skipped = parse_patches(model_text, orig)
                candidate, applied = apply_patches(orig, patches)
            logger.info("  [gap %s] 第%d次 方案替换块 %d 条（跳过%d）；channels=%s",
                        rag, attempt, len(patches), len(skipped), self.channels)

            if not applied:
                gap.error = f"模型方案不可用：{skipped or '无有效替换块'}"
                if attempt >= self.max_attempts:
                    gap.status = "blocked"
                    record_round({"round": "fix_attempt", "gap_key": gap.key,
                                  "attempt": attempt, "status": gap.status,
                                  "error": gap.error})
                continue
            stage = _WORK / f"{gap.file.name}.candidate"
            stage.write_text(candidate, encoding="utf-8")

            # 落盘事务（改5：改前基线 → 备份 → 应用 → 改后门 → 基线比较决定绿保/红滚）
            # 基线：贴改之前抓当前门态 = "本次修改无关的既有红门"。回滚判据据此正交：
            #   改前过→改后挂 = 修改引入红门 → 必回滚（改5核心）
            #   改前挂→改后挂 = 修改无关（本就不绿）→ 恢复基线，不误判"改坏了"
            #   改前挂→改后过 / 改前过→改后过 = 修改有效/保持 → 保留
            bak_path = BACKUP / f"{gap.file.name}.{int(time.time())}.bak"
            bak_path.write_text(orig, encoding="utf-8")  # 备份先于任何覆盖
            base_results: dict = {}
            if not self.dry:
                # 改5 基线：真实管线在贴改之前抓当前门态（dry 不真落盘，基线无意义，跳过省 cargo）
                try:
                    base_results, _base_audit = self._gate_score()
                except Exception as e:  # noqa: BLE001
                    base_results = {}
                    logger.warning("  [gap %s] 改前基线门异常(第%d次)：%s", rag, attempt, e)
            _写机制文件(gap.abs_path, candidate, 什么="模型方案", 文件相对=gap.file.as_posix())
            try:
                results, audit = self._gate_score()  # 改后门
            except Exception as e:  # noqa: BLE001
                results = {}
                # ★ R14 顺带修：异常路径下 `audit` 也必须重置 ——
                #   只重置 `results` 会让后续读到**上一轮的 audit**（或未绑定 ⇒ NameError）。
                #   与 `_RUST_ROOT`/`REPO_ROOT` 同一类：异常路径下变量状态不一致。
                audit = [{"gate": "?", "ok": False,
                          "cause": f"门运行异常：{type(e).__name__}: {e}"}]
                gap.error = f"门运行异常(第{attempt}次)：{e}"
            # ★ R14：数术层格裁决（取值等价于 `results and all(...)`）
            from agentscope_orchestrator import shushu_logic as _sl14b
            land_ok = bool(_sl14b.从审计(results, audit)[0])
            base_ok = bool(_sl14b.从门结果(base_results)[0])
            regressed = base_ok and not land_ok  # 改前过→改后挂：修改引入红门
            if land_ok:
                gap.status = "resolved"
                sediment_evolved(gap, patches)  # 判据演化：门确证的机制修正沉淀为对象
                logger.info("  [gap %s] 落盘成功：门全绿 %s（备份 %s）", rag, results, bak_path.name)
            else:
                _写机制文件(gap.abs_path, orig, 什么="模型红回滚", 文件相对=gap.file.as_posix())
                red = {k: v for k, v in results.items() if v is False}
                if regressed:
                    gap.error = f"回归(改前过→改后挂)：修改把门改红{red}(第{attempt}次)"
                else:
                    gap.error = f"落盘门未全绿(第{attempt}次)：{red}（基线亦红={not base_ok}）"
                gap.status = "blocked" if attempt >= self.max_attempts else "open"
                logger.info("  [gap %s] %s（回滚，备份 %s 保留）",
                            rag, gap.error, bak_path.name)
            record_round({"round": "fix_attempt", "gap_key": gap.key,
                          "attempt": attempt, "status": gap.status,
                          "error": gap.error, "landed": land_ok,
                          "pre_gates": base_results, "gates": results,
                          "regressed": regressed, "backup": str(bak_path),
                          "patch_blocks": len(patches)})
        window_lock.release(AUDIT, gap.abs_path)
        return gap

    async def run_iteration(self) -> dict:
        """一轮：启动即观测全库 → 扫描红门 → 修缺口 → 重扫确认是否 objective done。

        观测（observation）是主动机制、非等门：每轮先对 Rust 索引全量读结构→取象→
        全称机制判定→求真值；观测发现的机制违反作为缺口（kind=observation）与编译
        红门缺口一起派发真实模型，过机器门＋再观测双验证。判定不设上限（扫完整个
        索引为准），演化收敛到观测 clear（无新违反）为不动点。"""
        scan = self.scan_gates()
        self._idx = scan["idx"]
        self._suite = self._full_suite()
        self._anchor = scan["anchor"]
        results = scan["results"]
        gap_seed = trace_gaps(scan["audit"])

        # 主动观测全库（启动即执行，不等人喂信号；经结构记忆→按需增量、记忆检索联动）
        mem = obs_mod.StructureMemory(MEMORY_PATH)
        self._mem = mem             # 注入用：取象进 prompt（提升 LLM 能力，按需不塞全量）
        verdict = obs_mod.observe_rust(scan["idx"], ROOT, memory=mem)
        gap_seed += observation_gaps(verdict)
        _log("[observe] 全域观测：单元=%d 违反单元=%d 全机制真值True=%d（持续演化收敛），"
             "新派发观测缺口 %d 条；记忆检索：实读=%d 记忆复用=%d（跨轮结构相连）",
             verdict.total_units, verdict.units_with_violations,
             verdict.clean_units, len(observation_gaps(verdict)),
             mem.stats()["scanned_changed"], mem.stats()["reused_unchanged"])

        rec: dict = {"round": "iteration", "anchor": scan["anchor"],
                     "gates": results, "audit": scan["audit"],
                     "observation": {"total_units": verdict.total_units,
                                     "units_with_violations": verdict.units_with_violations,
                                     "clean_units": verdict.clean_units,
                                     "mechanism_matrix": verdict.mechanism_matrix,
                                     "memory": mem.stats(),
                                     "violations": [v.to_gap_dict() for v in verdict.violations]},
                     "gaps": [dict(g.__dict__,
                                   file=str(g.file), abs_path=str(g.abs_path),
                                   key=g.key) for g in gap_seed],
                     "objective_status": ("done" if is_objective_done(results)
                                          else "open"),
                     "objective_hint": ("ARC1/点云/GGP 满分=未接入占位(not_wired)；"
                                        "机制族全接入="
                                        + str(len(math_model.mechanism_families()["families"])) + "族"),
                     "mechanism_families": math_model.mechanism_families()}

        state = load_ledger_state() if not self.force else {}
        # 分层编排（改2+改5）：编译根(phase_priority=0)先修；编译全绿后才派观测叶缺口
        compile_open = any(g.kind == "compile" for g in gap_seed)
        if compile_open:
            gap_seed = [g for g in gap_seed if g.kind == "compile"]
        todo = [g for g in gap_seed
                if g.key not in state and g.cls == "production"]

        # ★ 轮级事务快照（2026-10-12）：在派发任何修复前，记录本轮拟改文件的原文。
        #   轮末全量验收门红时据此整体回滚（见下方 round_rollback），
        #   根治"跨缺口回归被永久接受、改动留在工作树"。
        self._snapshot_round([g.abs_path for g in todo if g.abs_path != ROOT])

        # ── 第二层闭环：偏差→全局目标（auto_objective）─────────────────
        # 回读账本历史真值(attempts/blocked/regressed/recurred)→偏差密度权重，替代
        # 纯 phase_priority 静态排序：同层内先派最大偏差（全局方向随偏差收窄）。
        # 判闭合对照本轮独立真值门(results)，禁放松已绿门（防平凡不动点，0-LLM）。
        # dry/force 也照常计算——它是纯折叠、不真对话，正好在 dry 下验证闭环接线。
        ledger_lines = (LEDGER.read_text(encoding="utf-8")
                        if LEDGER.exists() else "")
        objective = auto_objective.compute_from_lines(ledger_lines,
                                                      current_gates=results)
        obj_w = {d.key: d.weight for d in objective.active}
        todo.sort(key=lambda g: (g.phase_priority, -obj_w.get(g.key, 0.0), g.key))
        rec["objective"] = objective.to_dict()
        rec["objective_weight_by_key"] = {
            g.key: (g.phase_priority, obj_w.get(g.key, 0.0)) for g in todo}
        rec["skipped_gaps"] = [g.key for g in gap_seed if g.key in state]
        rec["skipped_bench"] = [
            f"{v.rel}@{v.pattern}" for v in verdict.violations
            if obs_mod.classify_source(v.rel) == "bench"]
        rec["deferred_observation"] = not compile_open and any(
            g.kind == "observation" for g in gap_seed)

        for gap in todo:
            _log(f"[develop] 缺口 {gap.file.name}@{gap.code}@{gap.line} "
                 f"[{gap.kind}] -> 派发修复模型")
            await self.fix_one_gap(gap)
        # 重扫确认闭环（编译门 + 再观测机制清零）
        confirm = self.scan_gates()                # 快验证门（秒级）
        rec["post_gates"] = confirm["results"]
        post_verdict = obs_mod.observe_rust(confirm["idx"], ROOT, memory=mem)
        rec["post_observation"] = {"total_units": post_verdict.total_units,
                                   "units_with_violations": post_verdict.units_with_violations,
                                   "clean_units": post_verdict.clean_units,
                                   "memory": mem.stats()}
        # objective 判定才跑全量验收门（快验证门全绿后一次；否则本轮无需全量）。
        # 结果并入 post_gates 供回执；cargo_test_lib 仅此 final 一次，不当每轮/每缺口门。
        if all(confirm["results"].values()):
            _log("[develop] 快验证门全绿 → 跑全量验收门(cargo test --lib)判 objective")
            idx2 = engine_dev.build_rust_index()
            anchor2 = engine_dev.anchor(idx2, prof_mod.get_profile().anchors.main)
            if anchor2 is not None:
                acc, _audit2 = await gate_spec_mod.evaluate_async(
                    self._full_suite(), anchor2, _WORK, str(ROOT))
                rec["post_gates"] = acc
                rec["acceptance"] = acc
                # ★ 修复（2026-10-12）：**轮末全量回归红 ⇒ 回滚本轮全部改动**。
                #   原实现只把 acc 记进账本，**改动全部留在工作树** —— 于是"编译得过、
                #   行为是错的"改动被永久接受（实机 diff 已证：有改动只改注释/常量推导，
                #   甚至引入 `loop {}` 无界循环，却因快验证门绿而留在树里）。
                #   逐缺口级的绿保/红滚只管单缺口；轮末全量是唯一能看见"跨缺口回归"的门，
                #   它红了就必须把本轮净改动整体撤销，回到轮初状态。
                if not all(acc.values()) and self._round_backups:
                    _log("[develop] ★ 全量验收门红 → 回滚本轮全部改动（%d 个文件）"
                         % len(self._round_backups))
                    rolled = 0
                    for path_str, orig in self._round_backups.items():
                        try:
                            Path(path_str).write_text(orig, encoding="utf-8")
                            rolled += 1
                        except OSError as exc:
                            _log("[develop] 回滚失败 %s：%s", path_str, exc)
                    rec["round_rollback"] = {"reason": "acceptance_gates_red",
                                             "rolled_back": rolled,
                                             "gates": acc}
                    # 回滚后重扫，账本记真实终态（不是回滚前的假绿）
                    confirm = self.scan_gates()
                    rec["post_gates"] = confirm["results"]
                    rec["acceptance"] = acc
                    post_verdict = obs_mod.observe_rust(confirm["idx"], ROOT, memory=mem)
                    rec["post_observation"] = {
                        "total_units": post_verdict.total_units,
                        "units_with_violations": post_verdict.units_with_violations,
                        "clean_units": post_verdict.clean_units,
                        "memory": mem.stats()}
        mechanism_done = post_verdict.is_clean
        rec["mechanism_done"] = mechanism_done
        rec["structure_density"] = {"objects": len(load_evolved()),
                                    "dense_reusable": sum(1 for r in load_evolved().values()
                                                          if r.get("hits", 0) >= MIN_DENSITY)}
        rec["objective_status"] = (
            "done" if (is_objective_done(rec.get("post_gates", {}) or {})
                       and mechanism_done)
            else "open")
        rec["state"] = ("objective_done" if rec["objective_status"] == "done"
                        else "need_more_iterations")
        record_round(rec)
        return rec


_log = logger.info


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=1, help="最多轮数")
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--channel", default="", help="锁定单频道（空=全通道）")
    ap.add_argument("--dry", action="store_true", help="不真对话，用已知可行方案验管线")
    ap.add_argument("--force", action="store_true", help="忽略账本已有记录重跑")
    ap.add_argument("--profile", default=None, help="项目档案路径")
    ap.add_argument("--selftest", action="store_true",
                    help="只跑结构性自检（不连网、不改树）")
    a = ap.parse_args()
    prof_mod.reset_profile(a.profile)
    if a.selftest:
        # ★ R5：把 R4-F7/F8 两条**静默**缺陷的判据力探针接进自检。
        #   这两条不报错、只漏修，运行期门抓不到，只能靠直接构造输入的门守。
        _坏 = _selftest_gap_identity()
        if _坏:
            print("develop 自检：失败")
            for x in _坏:
                print("  [FAIL]", x)
            return 1
        print("develop 自检：缺口身份/错误段 4 门全绿")
        return 0
    math_model.reset_cache()
    reload_paths()
    n = 1 if a.once else max(1, a.iterations)

    driver = DevelopDriver(channel_name=a.channel, dry=a.dry, force=a.force)
    logger.info("develop 驱动就绪：channels=%s dry=%s force=%s 轮数=%d",
                driver.channels, a.dry, a.force, n)
    for i in range(1, n + 1):
        logger.info("========== develop 第 %d 轮 ==========", i)
        rec = await driver.run_iteration()
        if rec.get("objective_status") == "done":
            logger.info("目标门全绿，提前结束。")
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))