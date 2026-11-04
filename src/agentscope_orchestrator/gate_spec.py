"""通用判据门评估器：语言/项目无关的机器原语 + 声明式 GateSpec。

防跑偏哲学不变：全部原语都是机器硬判定（退出码 / 文本扫描），
绝不"模型审定模型"。语言、项目、工具、规约的差异全部下沉到任务
自带的 GateSpec 数据里，判据门机制本体与任何语言无关，可全范围复用。

每条任务携带自己的 gates 规格（默认给 Lean 原生四条），评估器按规格
现场组装闭环 —— 即"判据门随任务动态重新生成"，不是写死某语言的专用机制。

原语（spec 中每项的 type）：
  compile         跑命令，断言退出码 == expect_exit（任何语言的编译/构建/测试）
  grep_forbidden  读 in 文件，正则不得命中 pattern(s)
  grep_required   读 in 文件，字面子串必须出现 pattern(s)（全部命中）
  scan_output     src 文本 + append 生成临时件，跑命令，扫 stdout/stderr 不得含 must_not
  whole_reading   **读整体一次判（O(1)）**：对 in 文本做一次稳定规范化（去空白/NFKC）
                  得到紧致签名与统一被测表示，再与键控期望**一次比对**，不再逐类/逐层
                  偏扫描。mode:
                    contains  读整体一次判：must_contain（或 patterns）全部子串须出现在
                              规范化整体中（吸收 grep_required 多 pattern 于一次读一条）
                    forbidden 规范化整体不得含 must_not 任一子串
                    equal     规范化整体签名 == 键控期望（expect 文本或 expect_sig 散列）
                  仍是机器原语（无模型）；"归一化一次 + 内存子串/散列比对"即 O(1) 紧致判。
                  ★ 默认**剥注释后判**（读代码，不读散文）：过时的占位注释不得
                    冒充"已实现"。要"必须写文档"类要求时显式 `include_comments: true`。

占位符（cmd/文本参数统一替换）：{file}→被测文件路径，{work}→临时目录，{src}→src 路径。
入口 evaluate(spec, path, work_dir, cwd_default) -> (results, audit)
  spec 可为 list（多门）或单 dict；None 视为无门（恒过）。
"""
from __future__ import annotations

import logging
import re
from contextvars import ContextVar
import subprocess
from pathlib import Path

logger = logging.getLogger("gate_spec")

def strip_for_matching(text: str) -> str:
    """把注释与字符串字面量抹成**空格**，供 grep 门在「代码本体」上匹配。

    ★ R12：为什么需要 —— 源码文本门读**原始文本**时，
      文档注释里提到 `todo!()`（如"禁止留 todo!/unimplemented! 占位"）
      会让 `no_scaffold` 变红，而代码里根本没有脚手架
      ⇒ **假红门**：合格产出被误拒，且**越写文档越红**。

    ★ 纪律：**清洗不得改变坐标** —— 输出与输入**等长、换行位置一致**，
      被剥掉的字符一律换空格 ⇒ 行号列号**精确**（与 `observation` 同一纪律）。

    ★ 诚实边界：只覆盖 `//` `#` 行注释与 `/* */` 块注释、`"…"` `'…'` 字面量；
      不做语言判定，不剥嵌套块注释。够用于"禁止词"这类结构扫描。
    """
    buf = list(text)
    n = len(text)

    def 抹(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if buf[k] != "\n":
                buf[k] = " "

    i = 0
    while i < n:
        ch = text[i]
        if text.startswith("//", i) or ch == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            抹(i, j)
            i = j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            抹(i, j)
            i = j
            continue
        if ch == '"' or ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == ch:
                    j += 1
                    break
                j += 1
            抹(i, j)
            i = j
            continue
        i += 1
    return "".join(buf)


# ★ R11：目录扫描的有界（**触顶如实报**，不静默截断 —— 静默截断＝假绿）
_GREP_MAX_FILES = 2000
_GREP_MAX_BYTES = 4 * 1024 * 1024

# 差分验收用的实例环境覆盖（默认空 = 不改变既有行为）
实例环境: dict[str, str] = {}

# ─────────────── 判据侧的两道围栏：默认关出网、默认剥凭据 ───────────────
# 为什么是这两条而不是容器：容器解决"会不会弄坏这台机器"，而判据工程最怕的是
#   **凭据被门里跑的东西带走**（模型写的排查脚本 print(os.environ) 就打出来了）
#   —— 这件事不需要容器，把 secret 从子进程环境里摘掉即可，成本近零。
#
# 诚实边界（别把它当沙箱卖）：
#   · 代理变量指向黑洞只挡**遵守代理设置**的客户端；直接 socket.connect 照样出网。
#     所以它叫"默认关出网"，不叫"网络隔离"。真隔离要网络命名空间（轻隔离版才有）。
#   · 出网可达性由 net_probe 单独**实测上报**，不靠这句话自证。
_出网允许: ContextVar["bool"] = ContextVar("orch_gate_net", default=False)
_凭据脱敏: ContextVar["bool"] = ContextVar("orch_gate_secrets", default=True)

_代理键 = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
           "all_proxy", "FTP_PROXY", "ftp_proxy")
_黑洞 = "http://127.0.0.1:9"          # 保留端口，连接必被拒

# 凭据类变量：名字匹配，不猜值
_凭据名 = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY",
           "CREDENTIAL", "COOKIE", "SESSION", "PRIVATE_KEY", "ACCESS_KEY")
_凭据前缀 = ("AWS_", "AZURE_", "GCP_", "GOOGLE_", "OPENAI", "ANTHROPIC", "DEEPSEEK",
             "GITHUB_", "GH_", "GITLAB_", "HF_", "HUGGING", "SLACK", "TELEGRAM",
             "STRIPE_", "SENDGRID", "TWILIO", "NPM_TOKEN", "PYPI_")

# 脱敏计数（label -> 摘掉的变量数），供审计与测试读取
_脱敏计数: dict[str, int] = {}

# ─────────────── ② 资源上限：一次门跑最多能"新造"多少东西 ───────────────
# 为什么需要（真事故）：`gate_power` 的临时目录落在被测仓内 ⇒ 自我复制
#   14 层、**126,673 个条目**、仓被搞脏、`git add` fatal。
#   容器能防住它；但**一条可机判的写量配额**同样能防，而且不改变任何执行语义。
# 口径：门前门后各数一次（新增文件数 / 新增字节），超限 ⇒ **红**，不是"警告"——
#   判据工程里"没坏但是干了件疯事"必须判红，否则等于默认允许。
_配额默认 = {"max_new_files": 20000, "max_new_bytes": 500 * 1024 * 1024}
_配额跳过 = {".git", "target", "node_modules", "__pycache__", ".venv", "dist",
             "build", ".pytest_cache", ".mypy_cache", ".orch_backup", ".lake",
             ".orchestrator-state", "work", ".idea", ".vscode", ".next", ".turbo"}


def _规模(根: Path, 秒预算: float = 3.0, 硬顶: int = 200_000) -> tuple[int, int, bool]:
    """数 (文件数, 字节数, 是否没有数完)。跳过构建产物目录。

    ★★★ 语义修正（自检当场抓到）：初版把**配额**当早退线 —— 于是"仓本来就比上限大"
      会被读成"这次写超了"，一个 51 个文件的仓配 5 的配额，**每个门都红**。
      那是把"基线"和"增量"混成一个值，同类错在本文件出现过多次。
    ★ 现在早退只有两个理由：**硬顶**（20 万条目，说明已经疯了）与**计时**（3 s，
      防止在被污染的大树上再走一遍）。任一触发 ⇒ `没数完=True`，由调用方如实上报。
    """
    import os as _os
    import time as _t
    起 = _t.time()
    文件 = 字节 = 0
    栈 = [str(根)]
    # ★ 用 `os.scandir` 而不是 `Path.rglob`：rglob 每个条目都要造 Path 对象并 stat，
    #   在 Windows 上慢一个量级（真跑：大仓两次计量直接吃满 3 s 预算 ⇒ 每次都"不可计"）。
    #   scandir 的 `is_dir`/`stat` 走目录项缓存，实测快 5-10 倍。
    while 栈:
        d = 栈.pop()
        try:
            it = _os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.name in _配额跳过:
                            continue
                        栈.append(e.path)
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                    文件 += 1
                    try:
                        字节 += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
                except OSError:
                    continue
                if 文件 > 硬顶:
                    return 文件, 字节, True
                if (文件 & 0x7FF) == 0 and (_t.time() - 起) > 秒预算:
                    return 文件, 字节, True
    return 文件, 字节, False


def _net_probe(超时: float = 3.0) -> dict:
    """实测"这台机器此刻能不能出网"，作为**事实**上报（不当判据）。

    ★ 为什么要有：我们说"默认关出网"只是环境变量层面的约定；直接 socket 照样出网。
      与其让调用方相信一句注释，不如让它拿到一个**当场测出来的读数**。
      注意：这是**编排器自己**的可达性，不是门子进程的可达性 —— 后者受同样网络但
      可能被代理变量影响。两个数不同，别混着读。
    """
    import socket
    结果 = {"实测": False, "目标": "1.1.1.1:443"}
    try:
        s0 = socket.create_connection(("1.1.1.1", 443), timeout=超时)
        s0.close()
        结果["实测"] = True
    except OSError as e:
        结果["原因"] = f"{type(e).__name__}: {e}"
    return 结果


def _脱敏(env: dict[str, str], 允许出网: bool,
          保留: tuple[str, ...] = ()) -> tuple[dict[str, str], int]:
    """返回 (脱敏后的环境, 摘掉的变量数)。

    ★ 为什么默认摘：门里跑的是**模型写的**测试与脚本，它读什么我们控制不了。
      实测形态：模型为排查问题写 print(dict(os.environ))，整份通道 key
      进了红门证据，再被回喂给下一个模型的提示词。"证据污染"比越界更隐蔽。
    ★ 允许出网 != 允许带凭据：两者正交（抓公网不需要我们的 key）。
      需要凭据的门必须显式 secrets: "keep"，审计里会留痕。
    """
    掉 = 0
    保 = {str(x).upper() for x in 保留}
    for k in list(env):
        ku = k.upper()
        if ku in 保:
            continue
        if any(ku == s0 or ku.endswith("_" + s0) for s0 in _凭据名) or ku.startswith(_凭据前缀):
            env.pop(k, None)
            掉 += 1
    if not 允许出网:
        for k in _代理键:
            env.pop(k, None)
        for k in _代理键:
            env[k] = _黑洞
        env["NO_PROXY"] = env["no_proxy"] = ""
        env["ORCH_NET"] = "0"
    else:
        env["ORCH_NET"] = "1"
    return env, 掉


def 合法门类型() -> frozenset:
    """**从本文件的执行分支里数出**真正能跑的门类型（唯一来源）。

    ★★★★★ 2026-11-03 修**第三把尺子**（真事故）：
      原来这里是个手写集合 `_TYPES = {...}`，10 项。而 `_run_one_inner` 里的
      `if typ == "..."` 分支有 **14** 项 —— 差的四个是 `oracle`（既有门）
      与当天新加的 `precondition` / `instance_agreement` / `regression_parity`。
      真症状（经 MCP 通道实测）：
        `非法判据门规格项：{...}（type 须属于 ['axiom_footprint','compile',
         'differential','grep_forbidden','grep_required','kernel_statement',
         'property','scan_output','test_counts','whole_reading']）`
      ⇒ **能执行的门被判非法**，调用方只能盲猜；而 `oracle` 已经这样被拒了很久，
        因为校验器与执行器各记一份清单。

    ★ 为什么"数出来"而不是"把清单补全"：补全只是**这一次**对齐；
      下次加门型，两份清单又会分叉。**手写清单的维护者不会记得改另一份** ——
      本仓为此吃过多次亏（R130 记的就是同一形状）。
      数出来则**结构上不可能分叉**。

    ★ 与 `auto_goal.合法门类型()` 同源：那个函数读本文件数 `typ == "..."`。
      这里也读本文件（同一批分支）⇒ 两处答案必然相同。
      改 `auto_goal` 复用本函数会引入循环导入（它已 import 本模块），
      所以保留两处**但都指向同一物理来源**，并由 `auto_goal.门类型一致()` 守着。
    """
    import re as _re
    from pathlib import Path as _P
    try:
        _src = _P(__file__).read_text(encoding="utf-8", errors="replace")
    except OSError:
        # 读不到就退回一个**保守但正确**的小集合（宁可拒真门，不可放假门）
        return frozenset({"compile", "grep_required", "grep_forbidden"})
    return frozenset(_re.findall(r'\btyp\s*==\s*"([a-z_]+)"', _src))


_TYPES = 合法门类型()

# Lean 三条标准公理（数学界公认，Lean 内核自带）。
#   ★ 为什么用**白名单**而不是黑名单：黑名单（`must_not: [sorryAx]`）
#     只能抓住**想到过**的作弊；白名单抓的是"**除这三条以外的一切**"。
#     实测漏洞（2026-10-13）：模型写 `axiom cheat : <命题>` 再用
#     `theorem ... := by exact cheat` 转调 ⇒ 四门**全绿**被误判为"证明完成"，
#     因为 `#print axioms` 只列出 `cheat`，**不含 sorryAx**。
_LEAN_STD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

# 解析 `#print axioms` 的输出：
#   'X' does not depend on any axioms
#   'X' depends on axioms: [a, b, c]
_AXIOM_LINE = re.compile(
    r"depends on axioms:\s*\[([^\]]*)\]", re.IGNORECASE)


def _canon(text: str) -> str:
    """稳定规范化（whole_reading 的统一被测表示）：NFKC + 去全部空白。

    语言无关、确定性、跨格式稳定 —— 缩进/换行/尾随空白不改变签名，故 '机制不变、
    换域只换输入字节'：同一机判在任意语言/文件上拿相同 canonical 形式。"""
    import unicodedata as _ud
    return re.sub(r"\s+", "", _ud.normalize("NFKC", text))


# ═══════════════════════════════════════════════════════════════════
# ★★★ 注释剥离（2026-10-13 补 · **同一类病第五次出现**）
#
# ── 实测缺口（在独立 Rust 工程上真跑通用目标时暴露）──
#   目标要求实现中文方法名 `推入`/`取出`/`长度`；模型写成了
#   `push`/`pop`/`len`。而 `whole_reading` 门要求 must_contain 里含
#   这三个词 —— 它**匹配到了文件顶部的过时注释**：
#       //! 待实现（当前为占位）：`推入` / `取出` / `长度`。
#   ⇒ 门判"达标"，而**用户的要求根本没实现**。
#   ★ 这是产品可信度问题：编排器报"目标达成"，但需求没满足。
#
# ── 病根与修法 ──
#   与前面四次**完全同源**：**判据读错了东西**（读散文，不读代码）。
#   ① 门被 Lean 注释骗过   → 改读**内核类型**
#   ② 候选表把散文当代码   → 剥注释再判
#   ③ 空真定理从注释抽出   → 剥注释再切块
#   ④ 候选 99.3% 是假的    → `\Z` 而非 `$`
#   ⑤ **门被过时注释满足** → 本条：whole_reading **默认读代码**
#
# ── 设计 ──
#   · 默认：按文件后缀剥注释（语言感知），判据只看**代码**。
#   · 显式 `include_comments: true` 才连注释一起读（用于"必须写文档"类要求）。
#   · 后缀未知 ⇒ 只剥**无歧义**的注释符（`//` `/* */` `<!-- -->`），
#     不碰 `#` / `--`（它们在很多语言里不是注释，乱剥会**误删真代码**）。
# ═══════════════════════════════════════════════════════════════════

# 每项 = (行注释符列表, 块注释符对列表, 块是否可嵌套)
_注释语法: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...], bool]] = {
    ".rs":   (("//",), (("/*", "*/"),), True),
    ".c":    (("//",), (("/*", "*/"),), False),
    ".h":    (("//",), (("/*", "*/"),), False),
    ".cpp":  (("//",), (("/*", "*/"),), False),
    ".hpp":  (("//",), (("/*", "*/"),), False),
    ".cc":   (("//",), (("/*", "*/"),), False),
    ".go":   (("//",), (("/*", "*/"),), False),
    ".java": (("//",), (("/*", "*/"),), False),
    ".js":   (("//",), (("/*", "*/"),), False),
    ".jsx":  (("//",), (("/*", "*/"),), False),
    ".ts":   (("//",), (("/*", "*/"),), False),
    ".tsx":  (("//",), (("/*", "*/"),), False),
    ".kt":   (("//",), (("/*", "*/"),), True),
    ".swift": (("//",), (("/*", "*/"),), True),
    ".lean": (("--",), (("/-", "-/"),), True),
    ".hs":   (("--",), (("{-", "-}"),), True),
    ".elm":  (("--",), (("{-", "-}"),), True),
    ".lua":  (("--",), (("--[[", "]]"),), False),
    ".sql":  (("--",), (("/*", "*/"),), False),
    ".py":   (("#",), (), False),
    ".pyi":  (("#",), (), False),
    ".sh":   (("#",), (), False),
    ".bash": (("#",), (), False),
    ".ps1":  (("#",), (("<#", "#>"),), False),
    ".rb":   (("#",), (), False),
    ".pl":   (("#",), (), False),
    ".r":    (("#",), (), False),
    ".jl":   (("#",), (("#=", "=#"),), True),
    ".yml":  (("#",), (), False),
    ".yaml": (("#",), (), False),
    ".toml": (("#",), (), False),
    ".ini":  (("#", ";"), (), False),
    ".cfg":  (("#",), (), False),
    ".conf": (("#",), (), False),
    ".html": ((), (("<!--", "-->"),), False),
    ".htm":  ((), (("<!--", "-->"),), False),
    ".xml":  ((), (("<!--", "-->"),), False),
    ".md":   ((), (("<!--", "-->"),), False),
    ".tex":  (("%",), (), False),
    ".bat":  (("REM ", "rem ", "::"), (), False),
    ".cmd":  (("REM ", "rem ", "::"), (), False),
}

# 后缀未知时的**保守**集：只剥无歧义符号（`#`/`--` 在太多语言里不是注释）。
_注释语法_保守 = (("//",), (("/*", "*/"), ("<!--", "-->")), False)


def _去注释(text: str, suffix: str = "") -> str:
    """按语言剥注释，**保留换行**（行号与结构不丢）。

    ★ 目的：让判据**读代码，不读散文**。
    ★ 块注释可嵌套时按深度匹配（Rust/Lean/Kotlin/Swift 的 `/- -/` 等）。
    ★ 字符串字面量**不**特殊处理 —— 这是保守取舍：宁可少剥（把字符串里的
      `//` 当注释起点）也不引入"误删真代码"的风险吗？**不**，反过来：
      这里对字符串**不**做保护，因为本函数只用于**包含性判据**，
      少剥会**漏判**（把注释当代码 ⇒ 正是要修的 bug），
      多剥会**误判**。两者都坏。⇒ 折中：按语言剥，且**不**碰字符串内部
      是做不到的（需要真词法分析）。**如实标注这个边界**。
    """
    if not text:
        return text
    suf = (suffix or "").lower()
    if not suf.startswith("."):
        suf = "." + suf if suf else ""
    行符, 块对, 可嵌套 = _注释语法.get(suf, _注释语法_保守)

    出 = []
    i = 0
    n = len(text)
    深度 = 0
    当前块: tuple[str, str] | None = None
    while i < n:
        # ── 在块注释里：找嵌套开 / 结束 ──
        if 当前块 is not None:
            开, 闭 = 当前块
            if 可嵌套 and text.startswith(开, i):
                深度 += 1
                出.append(" " * len(开))
                i += len(开)
                continue
            if text.startswith(闭, i):
                深度 -= 1
                出.append(" " * len(闭))
                i += len(闭)
                if 深度 <= 0:
                    当前块 = None
                continue
            出.append("\n" if text[i] == "\n" else " ")
            i += 1
            continue

        # ── 不在注释里：试块注释 ──
        命中块 = None
        for 开, 闭 in 块对:
            if text.startswith(开, i):
                命中块 = (开, 闭)
                break
        if 命中块 is not None:
            当前块 = 命中块
            深度 = 1
            出.append(" " * len(命中块[0]))
            i += len(命中块[0])
            continue

        # ── 试行注释（须在行内，且行注释符优先于块开符同位置时按列表顺序）──
        命中行 = None
        for 符 in 行符:
            if text.startswith(符, i):
                命中行 = 符
                break
        if 命中行 is not None:
            # 吞到行尾（换行保留）
            j = text.find("\n", i)
            if j < 0:
                出.append(" " * (n - i))
                i = n
            else:
                出.append(" " * (j - i))
                i = j
            continue

        出.append(text[i])
        i += 1
    return "".join(出)


# ★ 内核 pretty-print 的符号差异（2026-10-13 补）：
#   Lean 内核回显类型时用**数学符号**，而源文件/任务陈述里常写 **ASCII 名**：
#       Nat ↔ ℕ · Int ↔ ℤ · Real ↔ ℝ · Rat ↔ ℚ · Bool ↔ 𝔹 · Prop ↔ ℙ
#   若不做归一，`must_contain: ["... : Nat"]` 在内核读法下会**假红**
#   （内核回显 `ℕ`）—— 那是**读法引入的假红**，必须消除。
#   ★ 只在内核读法上用，**不动** `_canon` 全局行为（whole_reading 的
#     逐字相等判据不能被改）。
_内核符号 = {
    "ℕ": "Nat", "ℤ": "Int", "ℝ": "Real", "ℚ": "Rat", "𝔹": "Bool",
    "ℙ": "Prop", "ℂ": "Complex", "𝔽": "Fin", "∀": "forall", "∃": "exists",
    "→": "->", "↔": "<->", "∧": "/\\", "∨": "\\/", "¬": "not",
    "≠": "!=", "≤": "<=", "≥": ">=", "⁻¹": "inv",
}


def _canon_kernel(text: str) -> str:
    """内核读法的规范化 = **先符号名归一** + 再 `_canon`。

    ★★ 顺序是承重的（实测踩过）：`_canon` 里的 **NFKC 会把 `ℕ` 分解成 ASCII `N`**
      （双线体字符有兼容分解），若先 NFKC，符号表里的 `ℕ` 键**永远命中不到**
      —— 字符已经变成 `N` 了。实测症状：`must_contain: ["(n k : Nat)"]`
      对内核回显 `(n k : ℕ)` 判**假红**（读值成了 `(nk:N)`）。
      ⇒ 必须**先**做符号替换，**再** NFKC。
    """
    s = text
    for k, v in _内核符号.items():
        s = s.replace(k, v)
    return _canon(s)


def _sig(canon: str) -> str:
    """紧致签名：长度不变式 + 定长散列（O(1) 可比核对；等长不保证等，散列再锁）。"""
    import hashlib as _h
    if not canon:
        return "0:000000000000000000000000"
    return f"{len(canon)}:{_h.sha256(canon.encode('utf-8')).hexdigest()[:24]}"


def _sub(v, **mapping):
    if isinstance(v, str):
        for k, val in mapping.items():
            v = v.replace("{" + k + "}", val)
        return v
    if isinstance(v, list):
        return [_sub(x, **mapping) for x in v]
    return v


# ── POSIX→Windows 命令适配层 ─────────────────────────────
# 通用 planner 产出的 scan_output/compile 门可能用 `["bash","-c", <script>]`
# 一类 POSIX 管道；Windows 裸环境虽有 WSL bash，但判门 subprocess 直接调 `bash`
# 会命中 system32\bash（WSL）且 cwd 映射、grep/awk 常缺 → WinError 而非红门。
# 这里做平台透明兜底：Windows 下把 `bash ...` 命令转发给 `wsl bash ...`，并把
# 参数里的 Windows 盘符路径（工作目录/仓库根/待判文件）映射为 `/mnt/<drive>/...`，
# 让真实 planner 产出的 POSIX 门命令在 WSL 沙箱内可靠执行。
# 判据门哲学不变：仍是机器硬判定，只是"跑命令"这一层做了跨平台适配。

import platform as _platform
import sys as _sys


def _to_wsl_path(p: str) -> str:
    """Windows 路径 → WSL /mnt 路径（E:\\a → /mnt/e/a）。已是 /mnt/ 则原样。"""
    if not p:
        return p
    p = p.replace("\\", "/")
    if p.startswith("/mnt/"):
        return p
    m = re.match(r"^([A-Za-z]):", p)
    if m:
        return "/mnt/" + m.group(1).lower() + p[2:]
    return p


def _map_wsl_in_text(s: str) -> str:
    """把文本内所有盘符路径（`E:\\...` / `E:/...`）映射为 /mnt/<drive>/...。
    用于 bash 脚本字符串——脚本里 grep/awk 引用的 {file}/{work} 绝对路径在 WSL
    内须是 /mnt 形式才能被读取。"""
    def _repl(m):
        drive = m.group(1).lower()
        rest = m.group(2).replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return re.sub(r"(?<![A-Za-z0-9])([A-Za-z]):([/\\].*?)(?=\s|'|\"|;|$)",
                  _repl, s)


def _win_bash_cmd(cmd: list) -> list | None:
    """Windows 下 `["bash","-c", <script>]` → `["wsl","bash","-c", <script>]`，
    并把 cmd 兼得的 Windows 盘符路径（工作目录/仓库根/script 内的 {file} 等）
    映射为 /mnt 路径，让 POSIX 门命令在 WSL 沙箱内可靠执行。
    非 Windows、或 cmd[0] 不是 bash/sh → 返回 None（不做适配，交给系统）。"""
    if _platform.system() != "Windows":
        return None
    if not (isinstance(cmd, list) and cmd
            and Path(str(cmd[0])).name.lower() in ("bash", "sh")):
        return None
    # `bash ...` → `wsl bash ...`（WSL 内再 bash）；-c 后脚本做文本级 /mnt 路径映射。
    out: list[str] = ["wsl"]
    for i, c in enumerate(cmd):
        s = str(c)
        if i == 0:
            s = "bash"          # 保留原解释器名（现在是 wsl 的子命令）
        elif s == "-c":
            out.append(s)
            continue
        else:
            s = _map_wsl_in_text(s)   # -c 的 script：文本级路径映射
        out.append(s)
    return out


_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*[A-Za-z]")

# 代码里**不可能出现**的注释符（用于判「这个标记只能住在注释里」）。
# ★ 保守白名单：只认明确的注释语法，不靠"是不是标识符字符"去猜 ——
#   否则 `pub fn 推入`（含空格）会被误判成"只能住注释"，反作弊就漏了。
_注释符 = ("【", "】", "//", "/*", "*/", "<!--", "-->", "#|", "|#", "(*", "*)")


def _只能在注释里(marker: str) -> bool:
    """该标记是否**只能住在注释里**（含注释符 ⇒ 代码里写不出来）。

    ★ 2026-10-14：`whole_reading` 缺省剥注释以反作弊（防"过时占位注释满足实现
      要求"）。但**登记标记本身就在注释里**（`// 【已闭合】…`）⇒ 门把要找的
      东西自己删掉 ⇒ 恒红。故需按标记自身性质决定读不读注释。
      判据：含注释符 ⇒ 只能是注释 ⇒ 按原文判；否则仍只读代码（反作弊不变）。
    """
    s = str(marker or "")
    return any(t in s for t in _注释符)


def _strip_ansi(s: str) -> str:
    """剥掉终端转义码。

    ★★ 2026-10-14 实测（真跑红门证据）：6000 字符证据里含 **468 处** ANSI 转义
      序列 ⇒ 回喂给模型的"错误原因"实际是 `[1m[96m|` 这种乱码。判据读错了东西
      的又一例：它读的是**带终端格式的字节流**，而不是**错误文本**。
    """
    return _ANSI_RE.sub("", s or "")


def _差分规整(s: str, 规: list) -> str:
    """差分/属性门用的**规范化**：压掉不影响语义的噪声，不压语义。

    默认只做三件几乎无风险的事：剥 ANSI、统一换行、去行尾空白。
    `规` = [[正则, 替换], …]，由写门的人显式给（例如把临时路径、耗时、指针地址抹平）。
    ★ 刻意**不做**默认抹路径：那会让"输出里路径变了"这种真差异也被判成一致，
      而"位置/标识换了"往往正是行为分叉的第一现场。要抹就显式写出来。
    """
    文 = _strip_ansi(s).replace("\r\n", "\n").replace("\r", "\n")
    for pair in 规 or []:
        try:
            pat, rep = pair[0], pair[1]
        except Exception:                      # noqa: BLE001
            continue
        try:
            文 = __import__("re").sub(str(pat), str(rep), 文)
        except __import__("re").error:
            continue
    文 = "\n".join(line.rstrip() for line in 文.split("\n"))
    return 文.strip()


def _首异处(a: str, b: str) -> int:
    """首个不同字符的下标（给因由用：分歧位置比"不一致"三个字有用得多）。"""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


# ── 内核探针的名字退回（2026-11-01 真跑回归抓出）──────────────────────────
# 现象：`#check {module}.{theorem}` 只在**产物自己声明了同名 namespace** 时才对。
#   实测两种产物：
#     · 写 `namespace <工程命名空间> … end <工程命名空间>`
#       ⇒ 限定名可解析（一直绿）；
#     · 不写 namespace（裸 `theorem foo …`）⇒ `error(lean.unknownIdentifier)`
#       ⇒ 门对**完全正确的产物**恒红。
#   反过来只写非限定名，对第一种产物恒红 —— 两种写法各覆盖一半情形，
#   于是"门恒绿"和"门恒红"都能被一个与本判据无关的排版选择制造出来。
# 修法：解析不了时**退回最后一段标识符重试一次**；两次都解析不了才判红。
#   退回这件事写进审计（`probe_fallback`），因为它改变了"读法"，必须可见。
_探针名_RE = __import__("re").compile(
    r"#(?:check|print axioms)\s+([A-Za-z_\u4e00-\u9fff][\w.'\u4e00-\u9fff]*)")


def _探针退回(append: str) -> str | None:
    """把探针里的 `A.B.c` 退成 `c`；没有可退的（本就非限定 / 没有探针）返回 None。"""
    m = _探针名_RE.search(append or "")
    if not m:
        return None
    名 = m.group(1)
    if "." not in 名:
        return None
    尾 = 名.rsplit(".", 1)[-1]
    return append[:m.start(1)] + 尾 + append[m.end(1):]


def _跑探针带退回(命令: list, cwd, timeout: float, stall, text: str,
                  append: str, tmp: Path, 标记: str) -> tuple[int, str, str, bool]:
    """落探针文件 → 跑 → 若 `unknownIdentifier` 则退回一次重跑。

    返回 (rc, 合并输出, err, 是否退回过)。**退回只针对"名字解析不了"**；
    其它非零退出（真编译错、超时、挂起）原样返回，由调用方判红。
    """
    tmp.write_text(text + append, encoding="utf-8")
    rc, out, err = _run(命令, cwd, timeout, stall, 标记)
    comb = out + err
    if rc != 0 and "unknownIdentifier" in comb:
        退 = _探针退回(append)
        if 退:
            tmp.write_text(text + 退, encoding="utf-8")
            rc, out, err = _run(命令, cwd, timeout, stall, 标记 + "-退回非限定名")
            return rc, out + err, err, True
    return rc, comb, err, False


# 证据里优先保留的"错误块"起点
_ERR_START_RE = __import__("re").compile(
    r"^(?:error(?:\[[A-Za-z0-9]+\])?:|thread '[^']*' panicked at|"
    r"test .* FAILED|failures:|assertion|left:|right:|"
    r"\[SUBPROCESS_TIMEOUT|\[HANG_STALL|\[CMD_NOT_FOUND|note: run with)")


def _evidence(out: str, err: str, limit: int = 6000) -> str:
    """把命令输出压成**可读的错误证据**：优先抽错误块，不做位置截断。

    ★★ 2026-10-14 实测根因：原实现取 `out[-6000:] + err[-6000:]`（保尾部）。
      对 `cargo test --lib <测试名>` 而言，**396 条编译警告**会把 9 条真正的
      `error[E...]` 正文**整个挤出 6000 字符窗口** —— 于是回喂给模型的红门证据
      里**一条错误正文都没有**，只剩 unused variable 警告。模型据此无法修。
      （原注释声称"保尾部以免只留头部 warning"—— 它修了头部，尾部同样被淹没。）

    现改为：先剥转义码 → 抽**错误块**（error 行 + 其后若干行上下文，含
    `--> 文件:行`）→ 错误块够用就只给错误块；不够用再补尾部。
    """
    out_c = _strip_ansi(out)
    err_c = _strip_ansi(err)
    合并 = (out_c + "\n" + err_c) if out_c.strip() else err_c
    行们 = 合并.splitlines()

    块: list[str] = []
    取 = 0
    for i, ln in enumerate(行们):
        if _ERR_START_RE.match(ln):
            # 取该错误行 + 后续上下文（cargo 的 `--> file:line` 紧跟在下一行）
            段 = 行们[i:i + 14]
            块.append("\n".join(段))
            取 += len(段)
            if 取 >= limit // 2:
                break

    if 块:
        主体 = "\n…\n".join(块)
        if len(主体) < limit // 2:
            # 错误块太少 ⇒ 补尾部（可能含测试汇总行）
            主体 += "\n\n[尾部]\n" + 合并[-max(0, limit - len(主体) - 16):]
        return 主体[:limit]

    # 一条错误行都没有（例如只是退出码非零）⇒ 退回首尾兼顾
    if len(合并) <= limit:
        return 合并
    return 合并[:limit // 3] + "\n\n[尾部]\n" + 合并[-(limit - limit // 3 - 16):]


def _run(cmd: list[str], cwd, timeout: int = 300, stall: int | None = None,
         label: str = "", env: dict | None = None) -> tuple[int, str, str]:
    """跑命令回 (rc, stdout, stderr)。两类软失败都不抛异常：
      · 总超时 timeout → rc=-1 + [SUBPROCESS_TIMEOUT]（develop 不会被崩掉）
      · 挂起 stall：若指定 `stall` 秒，则流式读输出，**长时间无新输出即判
        命令挂起**，立即杀掉，rc=-1 + [HANG_STALL]。这解决『整批 cargo
        test 里一条不收敛测试拖死全线的卡死』——不等整批自然超时，及早
        暴露给 run_hang 派发真实模型处理。
    否则会把整条自主开发线卡死在干等编译/测试上（此前就是这样 → 停任务）。"""
    # ★★★★★ 2026-10-30 真事故：**环境漂移被误读成任务失败**（门白等 300 秒）
    #   真证据（经 MCP 通道实跑）：
    #     cmd=["python","-c","print(1)"] → 部署服务器里 **300s 超时判红**；
    #     同一条命令在源码 venv 里 0.4s 绿；换成 pwsh 子进程立即绿；
    #     探针读出服务进程里 `python` 解析到 `E:\huanjing\python.exe`（PATH 上碰巧的那只）。
    #   ⇒ 门里写的 `python` 应当就是**跑门的那只解释器**，而不是 PATH 上碰巧的那只。
    #     这与「模型名过期 ⇒ 全 503 ⇒ 被读成任务失败」是同一类病：**环境漂移冒充结果**。
    #   可用 ORCH_CMD_PYTHON=0 关掉（确需系统 python 时）。
    if (__import__("os").environ.get("ORCH_CMD_PYTHON", "1") != "0" and cmd
            and str(cmd[0]).lower() in ("python", "python3", "py")):
        cmd = [__import__("sys").executable] + [str(c) for c in cmd[1:]]
    # Windows 平台透明适配：`bash ...` 转发给 WSL bash + /mnt 路径映射
    win = _win_bash_cmd([str(c) for c in cmd])
    if win is not None:
        # WSL 命令须带启动目录（--cd 接受 Windows 路径），否则默认落到 WSL home
        # 而非仓库根，cwd 语义就丢了（compile/grep 在仓库根跑真实行为会错位）。
        try:
            cd_win = str(Path(str(cwd)).resolve())
        except Exception:
            cd_win = str(cwd)
        win = [win[0], "--cd", cd_win] + win[1:]
        cmd = win
    use_stall = stall and stall > 0
    # ★ 从**源头**禁彩：不给终端格式，就不必事后剥（`_strip_ansi` 仍保留兜底）。
    #   实测红门证据里曾有 468 处转义序列 ⇒ 回喂模型全是乱码。
    _env = dict(__import__("os").environ)
    # ★ 2026-11-03：调用方可以**覆盖**若干环境变量（`instance_agreement` 用它做环境异构：
    #   不同实例换 PYTHONHASHSEED 与独立临时目录）。
    #   ★ 是**合并**不是替换：围栏（脱敏/禁网/pycache 断链）在下面继续生效 ——
    #     否则调用方一个 env 就能把凭据脱敏绕过去，那是安全退化。
    if env:
        _env.update({str(k): str(v) for k, v in env.items()})
    # 围栏：默认剥凭据 + 默认关出网（诚实边界见 `_脱敏`）
    _允许出网 = bool(_出网允许.get())
    if bool(_凭据脱敏.get()):
        _env, _掉了 = _脱敏(_env, _允许出网)
        if _掉了:
            _lb = label or "cmd"
            _脱敏计数[_lb] = _脱敏计数.get(_lb, 0) + _掉了
    else:
        _env["ORCH_NET"] = "1" if _允许出网 else "0"
    # ★★★★★ 2026-10-30 真事故（**这台机器上所有 python 门都在白等**）
    #   真证据（经 MCP 通道逐层探针）：
    #     `["python","-c","print(1)"]` 在部署服务器里 300s 超时判红；
    #     pwsh 子进程立即绿 ⇒ 不是"子进程起不来"；
    #     探针读出服务进程环境里 `PYTHONPYCACHEPREFIX=I:\莉莉丝\.cache\python\pycache`，
    #     而 **I: 盘 0 字节可用**（本机实测 Free=0.0GB）⇒ 子 python 写字节码缓存卡死，
    #     `subprocess.run` 等到超时 ⇒ 门判"超时" ⇒ 最终被读成**任务未达标**。
    #   ⇒ 门的语义与"字节码缓存写哪儿"无关：断掉这个前缀 + 干脆不写 pyc。
    #     这与「模型名过期 ⇒ 503 ⇒ 读成任务失败」同族：**宿主环境漂移冒充任务结果**。
    for _k in ("PYTHONPYCACHEPREFIX", "PYTHONSTARTUP", "PYTHONEXECUTABLE"):
        if _k in _env:
            _env.pop(_k, None)
    _env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    # ★★★★★ 同批真事故：**门子进程不许继承宿主的 stdin**
    #   MCP 服务器把**协议管道**当 stdin；子 python（其 site/启动钩子）去读它 ⇒
    #   永远等不到 EOF ⇒ 300s 超时；而 `pwsh -Command` 不读 stdin，所以只有
    #   python 门挂、pwsh 门绿 —— 于是"子进程被管道卡住"被读成"任务未达标"。
    #   修法：stdin=DEVNULL（下面两处）。**门的判据不该受宿主通道形态影响。**
    # ★ 差分验收的载体：`实例环境` 由调用方按实例覆盖（不同 PYTHONHASHSEED/独立 TMP）。
    #   为什么要这个：同一只主机只跑一只环境，"环境耦合型缺陷"根本看不见 ——
    #   本会话真事故（门子进程继承 MCP 管道 stdin、PYTHONPYCACHEPREFIX 指向 0 字节盘）
    #   都是靠"换一只环境才暴露"的。全局默认为空 ⇒ 不改变任何既有行为。
    try:
        _env.update(实例环境 or {})
    except Exception:                     # noqa: BLE001 环境覆盖失败不许影响判据
        pass
    _env.setdefault("NO_COLOR", "1")
    _env.setdefault("CLICOLOR", "0")
    _env.setdefault("CARGO_TERM_COLOR", "never")
    if not use_stall:
        try:
            proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                                  capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace", env=_env,
                                  stdin=subprocess.DEVNULL)
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            out = getattr(e, "stdout", None) or b""
            err = getattr(e, "stderr", None) or b""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="ignore")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="ignore")
            return -1, out, f"[SUBPROCESS_TIMEOUT >{timeout}s] {err}"
        except OSError as e:
            # ★★★ 2026-10-14 真跑抓到的缺陷（**命令起不来 ⇒ 炸穿整个门评估**）：
            #   原实现不接 `OSError` ⇒ 门规格里命令路径写错（或工具未安装）时，
            #   `FileNotFoundError` 直接**抛出 `evaluate`** ⇒ 一个坏门把**所有门**
            #   一起带崩，而**不是**判红。实测：`cargo.exe` 路径写错即复现
            #   （`FileNotFoundError: [WinError 2] 系统找不到指定的文件`）。
            #   ⇒ 这是「读数**没有**这一档」的形态：机器**观察到了**"起不来"，
            #     却没有把这个因由读出来，而是让它以异常形式逃逸。
            #   ⇒ 修法：与超时/挂起同法 —— 回 rc=-1 + 显式标记，由读数判红。
            return -1, "", f"[CMD_NOT_FOUND] <{label or (cmd[0] if cmd else '')}> {e}"

    # ── 流式挂起监控（stall 检测）：线程灌输出队列，主循环计时 ──
    import queue
    import subprocess as sp
    import threading
    try:
        proc = sp.Popen([str(c) for c in cmd], cwd=str(cwd),
                        stdout=sp.PIPE, stderr=sp.STDOUT, stdin=sp.DEVNULL,
                        bufsize=1, text=True, encoding="utf-8", errors="replace", env=_env)
    except OSError as e:
        # 同上的 `CMD_NOT_FOUND` 处置（stall 分支）
        return -1, "", f"[CMD_NOT_FOUND] <{label or (cmd[0] if cmd else '')}> {e}"
    q: "queue.Queue[str]" = queue.Queue()
    def _pump():
        try:
            for line in proc.stdout:
                q.put(line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            q.put(None)   # EOF 哨兵
    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    started = __import__("time").time()
    last_tick = started
    output: list[str] = []
    while True:
        drained = False
        try:
            while True:
                line = q.get_nowait()
                if line is None:
                    drained = True
                    break
                output.append(line)
                last_tick = __import__("time").time()
        except queue.Empty:
            pass
        if drained:
            break
        if __import__("time").time() - started > timeout:
            proc.kill()
            return -1, "".join(output), f"[SUBPROCESS_TIMEOUT >{timeout}s]"
        if __import__("time").time() - last_tick > stall:
            proc.kill()
            return (-1, "".join(output),
                    f"[HANG_STALL 无输出>{stall}s] <{label or cmd[0]}> 疑似挂起/"
                    f"不收敛，被提前终止以派发智能体处理")
        __import__("time").sleep(0.2)
    proc.wait()
    return proc.returncode, "".join(output), ""


def validate(gates) -> str | None:
    """校验 GateSpec 合法性：返回 None 合法，否则返回拒因。

    ★ 修复（2026-10-12）：**空门集必须拒绝**。
      原实现对 `None` 与 `[]` 一律返回 None（合法），而 `evaluate([])` 返回
      `({}, [])`，`all({}.values())` 恒为 True ⇒ **零门判据会被判全绿并 promoted**。
      这是"判据门是唯一裁决者"这条铁律的漏洞：没有判据 ≠ 判据全过。
      语义澄清：`None` = "未指定，由调用方回落到默认门集"（如 lean_native_gates）；
      `[]` = "显式声明零个判据" ⇒ 无可裁决，必须拒。"""
    if gates is None:
        return None
    items = gates if isinstance(gates, list) else [gates]
    if not items:
        return "门集为空：零个机器判据 ⇒ 无裁决依据，拒绝（空门集不得判全绿）"
    for g in items:
        if isinstance(g, dict) and g.get("type") in _TYPES:
            continue
        return f"非法判据门规格项：{g!r}（type 须属于 {sorted(_TYPES)}）"
    return None


# ★★★★★ 「跳过测试不算绿」（2026-10-31）：
#   真证据：`pytest` 里 `@pytest.mark.skip` 退出码仍是 0，`unittest` 打 `OK (skipped=1)`
#   ⇒ 只判退出码时，**模型可以把测试全 skip 掉而拿到绿**。这是能直接制造假绿的洞。
#   本门把"通过数/跳过数/失败数"数出来：
#     · 通过数 < min_passed（默认 1）⇒ 红
#     · 跳过数 > max_skipped（默认 0）⇒ 红（skip/xfail/ignore 都不算过）
#     · 一个数都解析不出 ⇒ **红**（无法判定不许默认绿；真要放行设 allow_unparsed=true）
_计数式 = {
    # ★★★★★ 2026-11-03 修**计数错位**（真漏洞，不是洁癖）：
    #
    #   真证据：`passed=10 failed=3 skipped=0` 被解析成
    #       {passed: 0, failed: 10, skipped: 3}   ← **整体错位**
    #   根因：`failed` 的式子是 `(\d+)\s+failed`，而这句话里 `passed=10 failed`
    #     的 `10 failed` 正好满足它（`\d+` 吃掉了 `10`，`\s+` 吃掉了空格）——
    #     **`=` 被当成透明字符跨过去了**。`skipped` 同理（`failed=3 skipped`）。
    #
    #   危害（为什么必须修）：`test_counts` 门的**全部判定都建立在这几个数上**：
    #     `min_passed` / `max_skipped` / `max_failed` 三个阈值都读它。
    #     计数一错位，"跳过过多"会被报成"有失败"，更糟的是**该红的可能判绿**
    #     ——又一个能直接制造假绿的洞，而它伪装成"框架输出格式不标准"。
    #
    #   修法：**带 `key=` 的精确形式优先**，且词边界不许跨越 `=`/`:`。
    #     `(?<![\w=:=])(\d+)\s*[:=]\s*name` 只认"紧跟在本键名之前"的那个数。
    #   同时保留宽松形式**兜底**（pytest/cargo 的 "3 passed" 没有 key= 前缀）。
    "passed": [r"(?<![\w=])\bpassed\s*[:=]\s*(\d+)", r"(\d+)\s*[:=]\s*passed\b",
               r"(\d+)\s+passed\b", r"Test Suites:.*?(\d+)\s+passed"],
    "failed": [r"(?<![\w=])\bfailed\s*[:=]\s*(\d+)", r"(\d+)\s*[:=]\s*failed\b",
               r"(\d+)\s+failed\b", r"(\d+)\s+FAILED\b"],
    "skipped": [r"(?<![\w=])\bskipped\s*[:=]\s*(\d+)", r"(\d+)\s*[:=]\s*skipped\b",
                r"(\d+)\s+skipped\b"],
    "ignored": [r"(?<![\w=])\bignored\s*[:=]\s*(\d+)", r"(\d+)\s*[:=]\s*ignored\b",
                r"(\d+)\s+ignored\b", r"(\d+)\s+\[ignored\]"],
    "ran": [r"Ran\s+(\d+)\s+tests?", r"Tests:\s+.*?(\d+)\s+total"],
}


def _测试计数(文: str) -> dict:
    """从测试框架输出里数出 passed/failed/skipped/ignored。跨语言、不绑定单一框架。"""
    低 = 文
    取 = {}
    for 键, 式们 in _计数式.items():
        for re_ in 式们:
            m = re.search(re_, 低, re.I)
            if m:
                try:
                    取[键] = int(m.group(1))
                except (ValueError, IndexError):
                    取[键] = 0
                break
    # unittest：只报 Ran N + OK(skipped=k)/FAILED(failures=k) ⇒ 通过数要算出来
    if "passed" not in 取 and "ran" in 取:
        跳 = 取.get("skipped", 0) + 取.get("ignored", 0)
        败 = 取.get("failed", 0)
        取["passed"] = max(0, 取["ran"] - 跳 - 败)
    if "ignored" in 取:
        取["skipped"] = 取.get("skipped", 0) + 取["ignored"]
    取.setdefault("passed", 0)
    取.setdefault("failed", 0)
    取.setdefault("skipped", 0)
    取["可判定"] = bool(取.get("ran") or 取.get("passed") or 取.get("failed")
                     or 取.get("skipped"))
    return 取


测试命令特征 = ("pytest", "py.test", "unittest", "cargo test", "cargo nextest",
                "go test", "jest", "vitest", "mocha", "npm test", "pnpm test",
                "yarn test", "bun test", "deno test", "node --test", "mvn test",
                "gradle test", "gradlew test", "dotnet test", "ctest", "rspec",
                "phpunit", "tox", "nox", "mix test", "swift test", "zig build test")


def 像测试命令(cmd: list) -> bool:
    """粗判"这条命令是不是在跑测试"。只看**前三个 token**，且排除 `-c`/`--eval`。

    为什么要判：测试类命令默认要挂 `test_counts` 门（否则 skip 全绿）；
    但普通构建命令没有测试计数，挂上去必然"无法判定 ⇒ 红"。
    """
    if not cmd:
        return False
    头 = [str(x).lower() for x in list(cmd)[:3]]
    if "-c" in 头 or "--eval" in 头:
        return False
    文 = " ".join(头)
    return any(k in 文 for k in 测试命令特征)


def _pats_of(g: dict) -> list:
    """grep 类门的匹配目标：patterns 数组优先，退化到单 pattern；都没有 → 空。"""
    pats = g.get("patterns")
    if isinstance(pats, list) and pats:
        return pats
    if "pattern" in g and str(g.get("pattern") or "").strip():
        return [g["pattern"]]
    return []


# ══════════════════════════════════════════════════════════════════════════
# 【TS-442 / TS-447 / TS-424】判据读数：把「绿/红」抬成**无损因由**
# ══════════════════════════════════════════════════════════════════════════
# ── 为什么必须改（库内机证，逐条核过；这就是「修不完 BUG」的真因）──
#
# · TS-424 `panDing_collapses_all_non_dao424`
#   （`TS424_StructureTruthRenderCaliber.lean:258`）：
#     **两个相异结构，只要都不等于道，投影恒同（全丢）**。
#   同件 §4 把读法钉死（原文）：
#     「我原写『= 真真值对全体结构**未完成**』——该说法量的是**投影**，
#       不是真值 ⇒ **作用域错误**；**正确**读法：**真值对全体结构已完成**，
#       **未完成的**只是『把无界真值压进 `Bool` 投影』——而那个**本来就不可能**
#       （`Bool` 只有 2 值），且**不需要**。」
#   ⇒ 本模块旧读数 `results: dict[str, bool]` **就是那个 `Bool` 投影**：
#     编译错 / 超时 / 挂起 / 退出码不符 / 标记缺失 …… 相异因由全部压成 `False`。
#     这正是 `Defect384 T := ∃ x y, x ≠ y ∧ T x = T y`（:507）。
#
# · TS-381 `truth_layer_is_not_universal`：**盘码不同、判定相同**。
# · TS-369 `rate_is_free_parameter`：去掉单射后压到多小**是自由参数**，
#     由**判别函数**决定 ⇒ 读数**可设计**，不是只能二值。
# · TS-442 `ReadInjective` / `read_eq_reflects`：读单射 ⇒ 读等式反推态等式。
# · TS-447 `DirectionalityIsEnough`：★ **机制的充要条件是方向性**
#     （`read ∘ f = g ∘ read`），**单射 / 双射 / 轨道单射全部不需要**；
#     机制只在**有限判定空间**上跑。
#   ⇒ 工程落形：读数只需满足两条 ——
#       (a) **确定性**：同一产物恒得同一因由，且**与噪声无关**
#           （ANSI / 行序 / 尾截断都不得改变因由）—— 方向性的可测形式；
#       (b) **因由可分辨**：不同因由**不得同码** —— 不塌缩。
#
# ── 读数空间**无界**（TS-424 `truth_space_is_unbounded424`）──
#   因由码**携带具体证据**（错误码 + 文件:行:列 / 缺失标记名 / 命中模式名），
#   故码空间随证据细节增长，**不是固定小枚举**。
#   ⇒ 这正是 `exact_observation_price`（`N ≤ R`，要无损分辨 N 种因由，
#     读数空间必须 ≥ N）被**满足**的方式：N 种因由 ⟹ N 个相异码。
#
# ── 本读数**不改判据**（守住「判据由机器算」）──
#   绿/红仍由**同一批门**同一批原语算出；本段只把 `(ok, info)` **无损转写**
#   成因由码。`通过(码)` 仍回得出原 `ok` ⇒ 不新增判据、不放宽、不收紧。
# ══════════════════════════════════════════════════════════════════════════

_TIMEOUT_RE = re.compile(r"\[SUBPROCESS_TIMEOUT\s*>(\d+)s\]")
_STALL_RE = re.compile(r"\[HANG_STALL\s*无输出>(\d+)s\]")
_CMDNF_RE = re.compile(r"\[CMD_NOT_FOUND\]\s*<([^>]*)>")
_ERRCODE_RE = re.compile(r"error\[(E\d{4})\]")
_ERRHEAD_RE = re.compile(r"^\s*error(\[E\d{4}\])?:\s*(.*)$")
_ERRLOC_RE = re.compile(r"^\s*-->\s*(\S+?):(\d+):(\d+)")


def _编译错因由(err: str) -> str | None:
    """从红门证据里**逐块**抽「错误码 @ 文件:行:列」。

    ★ 为什么逐块配对而不是分别 `findall` 后拉链：`findall` 会丢掉
      「哪个错误码在哪个位置」的对应 —— 那正是**又一次把相异因由压平**。
      实测证据里可同时有 E0308 与 E0432，各自位置不同，必须成对读出。

    ★★ **必须去重**（本函数第一版被自检当场抓住的真缺陷，如实登记）：
      实测单错场景 `_evidence` 在补「尾部」时会把**同一个错误块再贴一遍**
      （证据 = 错误块 + `[尾部]` + 同一错误块）⇒ 因由码变成
        `编译错|[E0308]@趋向.rs:8030:36;[E0308]@趋向.rs:8030:36`
      而警告淹没场景不重复 ⇒ 同一因由**两个码**。
      ⇒ 那违反不变式 (a) **确定性**：码成了「证据装配过程的函数」，
        而不是「产物的函数」。
      ⇒ 修法：**按出现顺序去重** —— 码 = 相异错误位置的**集合**的规范表示。
        这不是美化，是把读数从「装配过程的影子」搬回「产物本身」。
    """
    对: list[str] = []
    当前码: str | None = None
    for 行 in (err or "").splitlines():
        m = _ERRHEAD_RE.match(行)
        if m:
            码 = (m.group(1) or "error").strip("[]")
            当前码 = 码 if 码.startswith("E") or 码 == "error" else f"E{码}"
            continue
        m = _ERRLOC_RE.match(行)
        if m:
            文件 = m.group(1).replace("\\", "/")
            文件 = 文件.rsplit("/", 1)[-1] if "/" in 文件 else 文件
            对.append(f"{当前码 or 'error'}@{文件}:{m.group(2)}:{m.group(3)}")
            当前码 = None
    if 对:
        # 去重保序（见 docstring：码必须是产物的函数，不是装配过程的函数）
        见: set[str] = set()
        唯一 = [x for x in 对 if not (x in 见 or 见.add(x))]
        return ";".join(唯一)
    码 = [c.strip("[]") for c in _ERRCODE_RE.findall(err or "")]
    if not 码:
        return None
    见2: set[str] = set()
    return ",".join([c for c in 码 if not (c in 见2 or 见2.add(c))])


def 判据因由(g: dict, ok: bool, info: dict) -> str:
    """把一次门执行读成**因由码** —— 读它**真正要判的那个东西**。

    ★ 不变式（自检守）：
      · 绿 ⟺ 码 == `"绿"`（`通过(码)` 必须能还原原 `ok`，不改判据）；
      · 相异因由 ⇒ 相异码（不塌缩，这是本函数存在的**唯一**理由）；
      · 码是 `(g, info)` 的**纯函数**（无时间、无随机、无路径噪声）。

    ★ 诚实边界：码里**不含**被测产物的路径（避免同一因由因路径不同而异码，
      那会造出**假区分力**：看起来分开了，其实分开的是路径不是因由）。
    """
    if ok:
        return "绿"
    t = g.get("type")
    err = str(info.get("err") or "")

    # ★★★ 三态门：证明/反例/未知三者**因由必须不同**（未知 ≠ 未达标）。
    _态 = info.get("三态")
    if _态 == "反例":
        return f"反例|{str(info.get('反例') or '')[:80]}"
    if _态 == "未知":
        return f"未知|{info.get('未知原因') or '判据未给出结论'}"

    # ★★★ 资源越界**第一判**（2026-11-01）：它先于"命令成没成"——
    #   一个 rc=0 却新造 12 万个文件的命令**必须红**。放在末尾会让它被
    #   "退出码不符|期望0实得0"这类空转因由吃掉（自检当场抓到）。
    if info.get("资源越界"):
        _q = info["资源越界"]
        新f, 新b = _q.get("新增文件"), _q.get("新增字节")
        if 新f is None or 新b is None:
            return f"资源越界|写量不可计且触及硬顶（{_q.get('note', '')}）"
        return (f"资源越界|新增文件{新f}>{_q.get('上限文件')}"
                f"或新增字节{新b}>{_q.get('上限字节')}")
    if info.get("写量不可计") and ok:
        # 只有"本来就该绿、只是没量成"才算这一档。
        # 否则真实的失败因由会被它顶掉（活体真跑抓到过：rc=1 文件不存在，
        # 回执却写"写量不可计" ⇒ 可诊断变成不可诊断）。
        return "资源上限未验|写量不可计（仓规模超出计时/硬顶）"

    # ★★★ 命令起不来 —— 对**一切**跑命令的门型统一先判（2026-10-14 真跑抓到）。
    #   原实现让 `FileNotFoundError` 逃逸出 `evaluate` ⇒ 一个坏门规格把**所有门**
    #   一起带崩。现在它是一档**正常因由**：机器观察到了"起不来"，就得读出来。
    m = _CMDNF_RE.search(err)
    if m:
        # 只取**可执行文件名**：换机器不该换因由（绝对路径是环境噪声，不是因由）。
        名 = m.group(1).replace("\\", "/").rsplit("/", 1)[-1]
        return f"命令无法启动|{名}"

    if t == "compile":
        m = _TIMEOUT_RE.search(err)
        if m:
            return f"超时|上限{m.group(1)}s"
        m = _STALL_RE.search(err)
        if m:
            return f"挂起|无输出{m.group(1)}s"
        错 = _编译错因由(err)
        if 错:
            return f"编译错|{错}"
        return f"退出码不符|期望{int(g.get('expect_exit', 0))}实得{info.get('rc')}"

    if t in ("grep_forbidden", "whole_reading") and info.get("must_not_hits") is not None:
        return "禁词命中|" + ";".join(sorted(str(x) for x in info["must_not_hits"]))

    if t == "grep_forbidden":
        # ★ 修复 R1-F6：给上面两档失败**各自的因由码** ——
        #   "读不到" 与 "正则非法" 是**两个不同的因由**，压平成一个
        #   `门规格非法` 就又把诊断力丢回 `Defect384`。
        if info.get("unreadable"):
            return "禁词门读不到被测文件"
        bad = info.get("bad_patterns")
        if bad:
            return "禁词门正则非法|" + ";".join(sorted(str(x) for x in bad))
        pats = info.get("hit_patterns")
        if pats:
            return "禁词命中|" + ";".join(sorted(str(x) for x in pats))
        if info.get("hits"):
            return "禁词命中|" + ";".join(sorted(str(x) for x in info["hits"]))
        return f"门规格非法|{err or '无 pattern'}"

    if t == "grep_required":
        miss = info.get("missing")
        if miss is not None:
            return "必需缺失|" + ";".join(sorted(str(x) for x in miss))
        return f"门规格非法|{err or '无 pattern'}"

    if t == "scan_output":
        hits = info.get("must_not_hits")
        if hits:
            return "输出禁词|" + ";".join(sorted(str(x) for x in hits))
        return f"退出码不符|期望{int(g.get('expect_exit', 0))}实得{info.get('rc')}"

    if t == "axiom_footprint":
        bad = info.get("forbidden_axioms")
        if bad:
            return "公理越界|" + ";".join(sorted(str(x) for x in bad))
        return f"公理足迹无法确认|{err}"

    if t == "kernel_statement":
        miss = info.get("missing")
        if miss is not None:
            return "内核陈述缺失|" + ";".join(sorted(str(x) for x in miss))
        return f"内核读法无法执行|{err}"

    if t == "whole_reading":
        mode = g.get("mode", "contains")
        if mode == "equal":
            return f"签名不符|实得{info.get('sig')}期望{info.get('expect_sig')}"
        miss = info.get("missing")
        if miss is not None:
            return "结构键缺失|" + ";".join(sorted(str(x) for x in miss))
        return f"门规格非法|{err}"

    if t == "property":
        if info.get("nondeterministic"):
            return f"属性不成立|用例{info.get('case')}第{info.get('run')}跑输出变了（首异@{info.get('first_diff_at')}）"
        if info.get("missing") is not None:
            return (f"属性不成立|用例{info.get('case')}缺必需|"
                    + ";".join(sorted(str(x) for x in info["missing"])))
        if info.get("forbidden_hits"):
            return (f"属性不成立|用例{info.get('case')}命中禁词|"
                    + ";".join(sorted(str(x) for x in info["forbidden_hits"])))
        if info.get("rc") is not None:
            return f"属性不成立|用例{info.get('case')}退出码{info.get('rc')}"
        return f"门规格非法|{err}"

    if t == "differential":
        if info.get("rc") is not None:
            return (f"差分退出码不符|被测{info.get('rc')}参照{info.get('rc_ref')}")
        if info.get("same") is False:
            return f"实现分歧|首个差异@{info.get('first_diff_at')}"
        return f"门规格非法|{err}"

    return f"未分类因由|{t}|{err or info}"


def 通过(因由码: str) -> bool:
    """因由码 → 绿/红。**唯一**的布尔投影点（投影只此一处，故不会两处不一致）。"""
    return 因由码 == "绿"


# ★★★★★ 2026-11-01 真缺陷（本工程在真 Lean 工程上自证时抓到）：
#   门里的 `in` / `src` 相对路径，此前**一律**按 `work_dir`（编排器自己的工作目录，
#   本机是 `<包>/work`）解析。于是：
#       `orch_run(repo=<某Lean工程>, gates=[{"in": "src/模型/规格.lean", ...}])`
#   读的是 `<包>/work/src/模型/规格.lean` ⇒ 读不到 ⇒ 门报红，
#   而**同一条门集里 `cmd` 却按 repo 跑**（cwd=cwd_default）⇒ 一个门集里
#   **两把尺子**：命令按 A 解析、文件按 B 解析。
#   这正是本文件 R114/R116/R117 那族错的同型复发（"同一个事实，两处实现"），
#   只是这次错在两把尺子**同时**存在却没人比对。
#
#   修法：显式引入"文件根"（`_文件根`），由调用方声明"这一批门读的是哪个工程"。
#   用 `ContextVar` 而不是穿参：`_入参路径` / `_读入参` 有 10+ 个调用点，
#   逐个穿参会漏（漏一处就又是一把尺子）；而 ContextVar 天然按线程/任务隔离，
#   与 `evaluate_async` 的 `asyncio.to_thread` 也相容。
_文件根: ContextVar["Path | None"] = ContextVar("orch_gate_file_root", default=None)


def _入参路径(g: dict, key: str, path: Path, work_dir: Path) -> Path:
    """把门的 `in` / `src` 解析成**真路径** —— 相对路径以 `work_dir` 为基准。

    ★★★★★ R117（真跑抓出的第 30 层 · 2026-10-20 · 第十五次端到端）：
      **同一个 `in` 字段，在两个消费者里是两把尺子。**

        · `gate_witness._gate_read_target(g, product, work_dir)`：
            `if not p.is_absolute(): p = Path(work_dir) / p`   ← **拼工作根**
        · 本文件 `_run_one`：`Path(str(_sub(g.get("in", ...))))`  ← **不拼**

      真证据（契约自证那 10 条门）：
        `FileNotFoundError: [Errno 2] No such file or directory:
         'src\\模块甲\\mod.rs'`
      —— 门要读的是「工程根下的 `src/模块甲/mod.rs`」，而 `_run_one`
      按**进程 cwd**（编排器自己的目录）去解析 ⇒ 找不到 ⇒
      **10 条好门全被判「疑恒真门」** ⇒ 契约破裂非空 ⇒ `_达标` 永远 False。

      ★ 这一层比 R114 / R116 都更靠下：
        · R114 修「在哪份产物里找样本」
        · R116 修「找到之后按哪个根解析路径」
        · R117 修「**门自己**按哪个根解析路径」← 这里
        三次都是同一件事：**同一个事实，两处实现**（R98b 的老教训）。

    ★ 为什么只对**相对路径**生效：绝对路径是调用方明确指定的，不许改写。
    ★ 为什么不做「先试 cwd、读不到再试 work_dir」：
      那会在 cwd 下**恰好有同名文件**时静默读到错的那一份 ——
      「找不到」至少是显式的，「读错了」不是。**两把尺子必须合成一把。**
    """
    raw = g.get(key)
    if raw is None:
        return path
    p = Path(str(_sub(raw, file=str(path), work=str(work_dir))))
    if p.is_absolute():
        return p
    # 显式文件根存在 ⇒ 一律按它解析（**不再回看 work_dir**：
    # "先试 A 再试 B" 会在两边都有同名文件时静默读错的那一份 —— 本文件既有注释已立此规）
    根 = _文件根.get()
    if 根 is not None:
        return Path(根) / p
    return Path(work_dir) / p


def _读入参(g: dict, key: str, path: Path, work_dir: Path
           ) -> tuple[str | None, str]:
    """读门的 `in`/`src` 文件。**读不到 ⇒ 返回 (None, 原因)**，绝不抛异常。

    ★★★★★ R118（真跑抓出的第 31 层 · 2026-10-20）：**R1-F5 只修了枚举里的一个项。**

      R1-F5 的注释写得很清楚：
        「旧实现直接 `read_text`：`in` 文件不存在 ⇒ `FileNotFoundError`
          **逃出 `_run_one` → 逃出 `evaluate_lossless`** ⇒ 一条坏门规格把
          **整个任务**打成 `error`（其余门的结果全部丢失）」
      它修的是 `grep_forbidden` 那一支。而 `_run_one` 里还有 **两个** 读取点
      照样裸 `read_text`：
        · `grep_required`（行 108）
        · `whole_reading`（行 248）
      ⇒ 真跑里，一条门的 `in` 指向一个**本轮没产出的文件** ⇒ 抛
        `FileNotFoundError` ⇒ 被 `验否定样本` 的 `except` 吞成
        「门执行异常 ⇒ 不假装已验」⇒ **10 条门里 7 条这样"验不了"**。

      ★ 这是 R107 的原样复发：**修了枚举里的项，没修「枚举」。**
        R107 的注释里就写着这句教训，R1-F5 又犯了一次。
      ⇒ 修法不是"再给这两处包 try"，而是**一个读口**
        （「能不枚举就别枚举」）：所有读取都经这里，
        读不到是一种**正常因由**，不是异常。

    ★ 为什么返回 `(None, 原因)` 而不是空串：空串是**合法的文件内容**，
      把它和"读不到"压成同一个值，就是 R95/R98/R102/R116b 那一族的错。
    """
    目标 = _入参路径(g, key, path, work_dir)
    try:
        if not 目标.is_file():
            return None, f"读不到|{key}={目标.name}（不存在或不是文件）"
        return 目标.read_text(encoding="utf-8", errors="ignore"), ""
    except OSError as exc:
        return None, f"读不到|{key}={目标.name}（{type(exc).__name__}）"


def _run_one(g: dict, path: Path, work_dir: Path, cwd_default) -> tuple[bool, dict]:
    """单门执行。**围栏在这里设**：network / secrets 是门声明的能力项。

    ★ 默认 network=false、secrets=strip（fail-closed）：门要出网、要凭据必须
      **显式声明**并在审计里留痕。这不是限制，是让能力成为**可审计事实** ——
      "这个门为什么需要出网"必须写在门规格里，而不是藏在某个环境变量里。
    """
    _t1 = _出网允许.set(bool(g.get("network") is True))
    _t2 = _凭据脱敏.set(str(g.get("secrets", "")).lower() != "keep")
    限 = g.get("limits")
    if isinstance(限, dict) and 限.get("off") is True:
        限 = None
    elif not isinstance(限, dict):
        限 = None
    try:
        if 限 is None:
            return _run_one_inner(g, path, work_dir, cwd_default)
        # ── ② 写量配额：门前门后各数一次（只对待判根，跳构建产物）──
        根 = Path(str(g.get("cwd") or cwd_default))
        上文件 = int(限.get("max_new_files", _配额默认["max_new_files"]))
        上字节 = int(限.get("max_new_bytes", _配额默认["max_new_bytes"]))
        前文件, 前字节, 前未完 = _规模(根)
        ok, info = _run_one_inner(g, path, work_dir, cwd_default)
        if g.get("net_probe") is True:
            info = {**info, "出网实测": _net_probe()}
        后文件, 后字节, 后未完 = _规模(根)
        if 前未完 or 后未完:
            # 数不完就**如实说数不完**：既不许静默放行，也不许凭想象判红。
            return ok, {**info, "写量不可计": True,
                        "写量": {"新增文件": None, "新增字节": None,
                                "前": [前文件, 前字节], "后": [后文件, 后字节]}}
        新文件, 新字节 = 后文件 - 前文件, 后字节 - 前字节
        if 新文件 > 上文件 or 新字节 > 上字节:
            return False, {**info, "资源越界": {
                "新增文件": 新文件, "新增字节": 新字节,
                "上限文件": 上文件, "上限字节": 上字节,
                "note": "写量超限 ⇒ 判红（'没坏但干了件疯事'不许当绿）"}}
        return ok, {**info, "写量": {"新增文件": 新文件, "新增字节": 新字节}}
    finally:
        _出网允许.reset(_t1)
        _凭据脱敏.reset(_t2)


def _run_one_inner(g: dict, path: Path, work_dir: Path, cwd_default) -> tuple[bool, dict]:
    typ = g["type"]
    # ★★★ ③ 三态门 `oracle`（2026-11-01）：**证明 / 反例 / 未知**。
    #
    # 为什么二值门不够：科研级判据里大量情形是"我判不了"，而二值门只能把
    # "判不了"写成红 —— 于是 AI 会把它当失败去"修"，越修越坏；或者反过来
    # 把超时/无输出当绿（假绿的头号来源）。
    # 这里把第三态**显式化**：`未知` 仍然**不是绿**（不变式不破），但它的因由码
    # 与 `反例` 不同，调用方据此选择"换判据 / 缩范围 / 交还给人"，而不是瞎改。
    #
    # 规格：{"type":"oracle", "cmd":[...], "witness_re":"...", "unknown_re":[...]}
    #   rc==0                     ⇒ 三态=证明（绿）
    #   rc!=0 且 witness_re 命中  ⇒ 三态=反例（红，附反例片段）
    #   其它（超时/起不来/只给"不知道"）⇒ 三态=未知（红，但因由是"未知"）
    if typ == "oracle":
        cmd = _sub(g.get("cmd", []), file=str(path), work=str(work_dir))
        if not cmd:
            return False, {"err": "oracle 需要 cmd", "三态": "未知", "未知原因": "无命令"}
        cwd = g.get("cwd", cwd_default)
        rc, out, err = _run(cmd, cwd, g.get("timeout", 300), g.get("stall"),
                            g.get("name", "oracle"))
        组合 = out + err
        import re as _re
        反例 = None
        _wr = g.get("witness_re")
        if _wr:
            m = _re.search(str(_wr), 组合)
            if m:
                反例 = m.group(0)[:400]
        if rc == 0:
            return True, {"三态": "证明", "rc": 0}
        if 反例:
            return False, {"三态": "反例", "rc": rc, "反例": 反例,
                           "err": _evidence(out, err)}
        if _TIMEOUT_RE.search(err) or _STALL_RE.search(err if 'err' in dir() else err):
            return False, {"三态": "未知", "rc": rc, "未知原因": "预算耗尽（超时/挂起）",
                           "err": _evidence(out, err)}
        return False, {"三态": "未知", "rc": rc,
                       "未知原因": "判据没给出可判结论（无证明标记亦无反例标记）",
                       "err": _evidence(out, err)}
    if typ == "compile":
        # `src` 占位符与 `grep_*`/`axiom_footprint` 统一支持：
        # 取门的 `src`/`in`（按文件根解析），缺省用被测产物路径。
        _s = g.get("src") or g.get("in")
        _srcp = str(_入参路径(g, "src", path, work_dir)) if _s else str(path)
        cmd = _sub(g.get("cmd", []), file=str(path), work=str(work_dir), src=_srcp)
        cwd = g.get("cwd", cwd_default)
        rc, out, err = _run(cmd, cwd, g.get("timeout", 300),
                            g.get("stall"), g.get("name", cmd[0] if cmd else "cmd"))
        cwd = g.get("cwd", cwd_default)
        # 失败证据**抽错误块**（不是位置截断）—— 见 `_evidence` 的注释：
        # 实测 396 条警告会把 9 条 error 正文挤出窗口，模型拿到"无错误的红门"。
        # 绿色时短记（保尾部足够），红门时抽错误块。
        if rc == int(g.get("expect_exit", 0)):
            evidence = _strip_ansi(err)[:400]
        else:
            evidence = _evidence(out, err)
        return rc == int(g.get("expect_exit", 0)), {"rc": rc, "err": evidence}

    if typ == "test_counts":
        cmd = _sub(g.get("cmd", []), file=str(path), work=str(work_dir))
        cwd = g.get("cwd", cwd_default)
        rc, out, err = _run(cmd, cwd, g.get("timeout", 900), g.get("stall"),
                            g.get("name", "test_counts"))
        文 = _strip_ansi(out) + "\n" + _strip_ansi(err)
        计 = _测试计数(文)
        须过 = int(g.get("min_passed", 1))
        许跳 = int(g.get("max_skipped", 0))
        许败 = int(g.get("max_failed", 0))
        无法 = not 计.get("可判定")
        期rc = int(g.get("expect_exit", 0))
        if rc != 期rc:
            过 = False
            因 = f"退出码不符|期望{期rc}实得{rc}"
        elif 无法 and not bool(g.get("allow_unparsed", False)):
            过 = False
            因 = ("无法判定|输出里数不出任何测试计数"
                 "（不是测试命令？或框架输出不识别）")
        elif 计["passed"] < 须过:
            过 = False
            因 = f"通过不足|passed={计['passed']} < min_passed={须过}"
        elif 计["skipped"] > 许跳:
            过 = False
            因 = f"跳过过多|skipped={计['skipped']} > max_skipped={许跳}（skip/xfail/ignore 不算过）"
        elif 计["failed"] > 许败:
            过 = False
            因 = f"有失败|failed={计['failed']} > max_failed={许败}"
        else:
            过 = True
            因 = (f"绿|passed={计['passed']} skipped={计['skipped']} "
                 f"failed={计['failed']} rc={rc}")
        return 过, {"rc": rc, "cause": 因, "err": 文[-1200:], "计数": 计}

    if typ == "regression_parity":
        # ══════════════════════════════════════════════════════════════════════
        # 回归对照：**把"我没引入回归"从推理变成实测**。
        #
        # 为什么需要（2026-11-03 的真实教训）：Rust 侧 121 passed / 30 failed，
        # 我需要说明"这 30 条不是我引入的"。当时做的是**推理**：
        #   「这些模块我碰都没碰」+「panic 行号都在 create_dir_all」。
        # 这个推论大概率对，但它的强度是推理，**不是对照实测** ——
        # 科研工程里这个差别是要命的：你没法把它写进"方法"一节。
        #
        # 正确形式是**把对照做成任务，而不是做成分析**：在两个版本上各跑一次同一条
        # 命令，比较**计数**；差集为空 ⇒ 无回归。
        #
        # ★ 判据口径（钉死，避免"比了个寂寞"）：
        #   · 只比**测试计数**（passed/failed/skipped/ignored），不比退出码 ——
        #     退出码会因"两版都红"而相同，那说明不了"没引入回归"；
        #   · `failed`/`skipped` **新增**即红（这是回归的定义）；
        #   · `passed` **减少**即红（有东西从过变成不过）；
        #   · 两版**都**红、且数字相同 ⇒ **绿**（本次没有新增回归）——
        #     这正是我要的结论：环境造成的既有红不算我的账；
        #   · `allow_baseline_red=False` 时，基线本身红 ⇒ 判"未知"而不是绿，
        #     免得拿一个坏基线当参照。
        # ══════════════════════════════════════════════════════════════════════
        _基 = _sub(g.get("baseline_cmd", []), file=str(path), work=str(work_dir))
        _现 = _sub(g.get("cmd", []), file=str(path), work=str(work_dir))
        if not _基 or not _现:
            return False, {"三态": "未知",
                           "未知原因": "regression_parity 需要 `baseline_cmd` 与 `cmd` 两条命令"}
        _超 = int(g.get("timeout", 900))
        _停 = g.get("stall")
        _基cwd = str(_入参路径(g, "baseline_cwd", path, work_dir)) if g.get("baseline_cwd") else g.get("cwd", cwd_default)
        _现cwd = g.get("cwd", cwd_default)
        _brc, _bout, _berr = _run(_基, _基cwd, _超, _停, g.get("name", "baseline"))
        _nrc, _nout, _nerr = _run(_现, _现cwd, _超, _停, g.get("name", "current"))
        _基计 = _测试计数(_strip_ansi(_bout) + "\n" + _strip_ansi(_berr))
        _现计 = _测试计数(_strip_ansi(_nout) + "\n" + _strip_ansi(_nerr))
        _允基红 = bool(g.get("allow_baseline_red", False))

        if not _基计.get("可判定") or not _现计.get("可判定"):
            return False, {"三态": "未知", "rc": _nrc,
                           "未知原因": ("对照两边必须都能数出测试计数；"
                                    f"基线可判定={_基计.get('可判定')} 当前可判定={_现计.get('可判定')}"
                                    "（不是测试命令？或框架输出不识别）"),
                           "基线": _基计, "当前": _现计}
        if _基计["failed"] > 0 and not _允基红:
            return False, {"三态": "未知", "rc": _nrc,
                           "未知原因": (f"基线本身是红的（failed={_基计['failed']}）⇒ 拿它当参照无意义；"
                                    "要么先让基线绿，要么显式设 allow_baseline_red=true 只比增量"),
                           "基线": _基计, "当前": _现计}

        _新增败 = _现计["failed"] - _基计["failed"]
        _新增跳 = _现计["skipped"] - _基计["skipped"]
        _减少过 = _基计["passed"] - _现计["passed"]
        _差 = []
        if _新增败 > 0:
            _差.append(f"failed 新增 {_新增败}（{_基计['failed']}→{_现计['failed']}）")
        if _新增跳 > 0:
            _差.append(f"skipped 新增 {_新增跳}（{_基计['skipped']}→{_现计['skipped']}）")
        if _减少过 > 0:
            _差.append(f"passed 减少 {_减少过}（{_基计['passed']}→{_现计['passed']}）")
        # ★ 无法判定（rc≠0 且数不出计数）已在上面拦掉，所以这里"两边都红且数字相同"
        #   是**可判的绿**：本次没有新增回归。
        _过 = not _差
        _因 = ("无回归|" if _过 else "有回归|") + (
            f"passed {_基计['passed']}→{_现计['passed']} "
            f"failed {_基计['failed']}→{_现计['failed']} "
            f"skipped {_基计['skipped']}→{_现计['skipped']}"
            + ("；" + "；".join(_差) if _差 else ""))
        return _过, {"rc": _nrc, "基线rc": _brc, "cause": _因,
                     "基线": _基计, "当前": _现计, "差": _差,
                     "err": (_evidence(_nout, _nerr) if not _过 else
                             (_strip_ansi(_nerr)[:400] if _nerr else ""))}

    if typ == "precondition":
        # ══════════════════════════════════════════════════════════════════════
        # 前提门：**把"环境前提"与"产物正确性"拆开**。
        #
        # 为什么需要（2026-11-03 真实误诊）：本机被 spawn 的进程写不了 `E:\Temp`
        # （WinError 5）⇒ `cargo test` 30 条红、pip 装不上。我第一反应是"我的改动坏了"。
        # 那 30 条红**看起来和真回归一模一样**。
        #
        # 根因不是环境难缠，是**判据把两件事混在一个结论里**：
        #   「临时目录可写」是**前提**，「产物对不对」是**结论**。
        # 前提不成立时，结论根本没资格被判定 —— 但旧形式只会报"红"，
        # 于是调用方拿着一个"红"去修产物，**修一个不存在的问题**。
        #
        # ★ 语义（钉死）：
        #   · 前提成立 ⇒ 绿（**不代表产物对**，只代表这一层没挡住）；
        #   · 前提不成立 ⇒ 红，且证据里带 `前提不成立` 标记 ⇒ 上层应报
        #     `参数/前提不成立`（调用方责任），**不进入产物判定**；
        #   · 这门的绿**绝不能**被当成"产物合格"的证据 —— 它是**准入检查**，不是验收。
        # ══════════════════════════════════════════════════════════════════════
        _缺 = []
        _据: dict = {}
        # ① 路径存在性
        for _p in (g.get("requires_path") or []):
            _真 = Path(_p) if Path(_p).is_absolute() else (work_dir / _p)
            if not _真.exists():
                _缺.append(f"路径不存在：{_p}")
            _据.setdefault("路径", {})[_p] = _真.exists()
        # ② 环境变量非空
        for _k in (g.get("requires_env") or []):
            _v = __import__("os").environ.get(_k, "")
            if not _v:
                _缺.append(f"环境变量未设或为空：{_k}")
            _据.setdefault("环境", {})[_k] = bool(_v)
        # ③ 可写目录（临时目录那一类前提）
        if g.get("needs_writable_dir"):
            _目 = _入参路径(g, "in", path, work_dir)
            _探 = _目 / f".写入探针-{__import__('os').getpid()}"
            _能 = False
            try:
                _目.mkdir(parents=True, exist_ok=True)
                _探.write_text("x", encoding="utf-8")
                _能 = _探.read_text(encoding="utf-8") == "x"
            except OSError as _e:
                _缺.append(f"目录不可写：{_目}（{type(_e).__name__}: {_e}）")
            finally:
                try:
                    _探.unlink(missing_ok=True)
                except OSError:
                    pass
            _据["可写目录"] = {"路径": str(_目), "可写": _能}
        if not _据:
            return False, {"三态": "未知",
                           "未知原因": ("precondition 门没声明任何要检查的前提："
                                    "至少给 needs_writable_dir / requires_env / requires_path 之一")}
        if _缺:
            return False, {"rc": None, "前提不成立": True, "cause": "前提不成立|" + "；".join(_缺),
                           "err": "；".join(_缺), "前提": _据}
        return True, {"rc": None, "cause": "前提成立|（**不代表产物合格**，本门只是准入检查）",
                      "前提": _据}

    if typ == "instance_agreement":
        # ══════════════════════════════════════════════════════════════════════
        # 实例一致门：把「抖动」从现象变成**可机判的结论**。
        #
        # 为什么需要（2026-11-03 真实事件）：套件里 `g3_sqlite.py` 红了一次，
        # 单独跑 `ALL PASS`，重跑套件又 38/38 绿。我用"重跑三次确认"判断它是抖动 ——
        # **这个判断是人肉的、没记录的、没标准的**。科研工程里这是最不该留的东西：
        # 判据的判定权交回了人的直觉，而"可能是抖动吧"会慢慢变成忽略红的借口。
        #
        # 正确形式：同一门集在**刻意异构的实例**里各跑一次，绿要**交集**。
        # 结论不同 ⇒ 这是**耦合/抖动**，不是"任务失败" —— 两种结论对应两种处置。
        #
        # ★ 判据口径（钉死）：
        #   · 各实例**计数一致** ⇒ 绿（结论稳定）；
        #   · 有实例数不出计数 ⇒ 该实例记"未知"；全未知 ⇒ 未知；
        #   · 结论分歧 ⇒ **红**，但证据里带 `实例分歧` 标记与每实例数字，
        #     让上层报 `实例分歧` 分类而不是 `未达标`（责任在环境耦合，不在产物）；
        #   · `require_same_verdict=false` 时只报分歧事实、不判红（供"我只想看差异"用）。
        # ★ 边界（如实）：这里的"异构"是**环境异构**（独立临时目录/独立 PYTHONHASHSEED），
        #   不是完整容器隔离。要抓"绿只因有构建产物"的伪绿，得用 `instances_mode=copy`
        #   的仓副本 —— 那是 `orch_run` 那一层的参数，本门不做（避免两把尺子）。
        #   ★★ 2026-11-03 实测确认的第二条边界（**必须写下来，否则会被当成万能**）：
        #   本门测的是「同一门集在**环境**异构下稳不稳」，**测不出上游服务抖动**。
        #   真证据：`g3_sqlite.py` 在套件里连红两次（并发时走真模型，上游被拒/超时），
        #   单独跑 ALL PASS；拿本门跑它两个实例 ⇒ 两个都 `passed=2` 一致绿 ——
        #   因为两个实例都跑在**轻载**下，异构里没有"并发压力"这一维。
        #   ⇒ 要覆盖那类抖动，异构必须包含**并发**（同时起 N 个同类任务），
        #     或干脆把那类判据标成"稳定后复验用，不作准入"。
        #     **别用它给上游抖动打包票。**
        # ══════════════════════════════════════════════════════════════════════
        _cmd = _sub(g.get("cmd", []), file=str(path), work=str(work_dir))
        if not _cmd:
            return False, {"三态": "未知", "未知原因": "instance_agreement 需要 `cmd`"}
        _n = max(2, int(g.get("instances", 2)))
        _须同 = bool(g.get("require_same_verdict", True))
        _超 = int(g.get("timeout", 900))
        _停 = g.get("stall")
        _cwd = g.get("cwd", cwd_default)
        _各: list[dict] = []
        for _i in range(_n):
            _env = dict(__import__("os").environ)
            # 刻意异构：换哈希种子 + 换临时目录（不同实例不同）
            _env["PYTHONHASHSEED"] = str(_i + 1)
            _tmp = work_dir / f".ia-instant-{_i}"
            try:
                _tmp.mkdir(parents=True, exist_ok=True)
            except OSError:
                _tmp = work_dir
            _env["TMPDIR"] = str(_tmp)
            _env["TEMP"] = str(_tmp)
            _env["TMP"] = str(_tmp)
            _rc, _o, _e = _run(_cmd, _cwd, _超, _停, f"{g.get('name', 'ia')}-{_i}", env=_env)
            _c = _测试计数(_strip_ansi(_o) + "\n" + _strip_ansi(_e))
            _各.append({"实例": _i, "rc": _rc, "计数": _c,
                        "可判定": bool(_c.get("可判定")), "临时候选": str(_tmp)})
        _可判 = [x for x in _各 if x["可判定"]]
        if not _可判:
            return False, {"三态": "未知",
                           "未知原因": f"{_n} 个实例都数不出测试计数（不是测试命令？）",
                           "实例": _各}
        # 一致性的口径：**通过/失败/跳过三元组**全同才算一致
        _签 = {(x["计数"]["passed"], x["计数"]["failed"], x["计数"]["skipped"]) for x in _可判}
        _分歧 = len(_签) > 1
        _因 = ("实例一致|" if not _分歧 else "实例分歧|") + "；".join(
            f"#{x['实例']} passed={x['计数']['passed']} failed={x['计数']['failed']} "
            f"skipped={x['计数']['skipped']}" for x in _各)
        if _分歧 and _须同:
            return False, {"rc": _各[-1]["rc"], "实例分歧": True, "cause": _因,
                           "实例": _各,
                           "处置": ("这是**环境耦合/抖动**，不是产物不合格 ⇒ 上层应报"
                                 "『实例分歧』并看各实例数字，别拿去『修』产物")}
        if _分歧:                      # require_same_verdict=false：只报不判
            return True, {"rc": _各[-1]["rc"],
                          "cause": _因 + "（require_same_verdict=false ⇒ 只报不判）",
                          "实例": _各}
        if len(_可判) < _n:            # 一致，但有实例没判出来 ⇒ 如实记，仍绿
            return True, {"rc": _各[-1]["rc"],
                          "cause": _因 + f"（注意：{_n - len(_可判)} 个实例未能判定）",
                          "实例": _各}
        return True, {"rc": _各[-1]["rc"], "cause": _因, "实例": _各}

    if typ == "grep_forbidden":
        # ★★★★★ 修复 R1-F5：**读不到就报因由，绝不让异常逃逸**。
        #   旧实现直接 `read_text`：`in` 文件不存在 / 是目录 ⇒ `FileNotFoundError`
        #   / `IsADirectoryError` **逃出 `_run_one` → 逃出 `evaluate_lossless`**
        #   ⇒ 一条坏门规格把**整个任务**打成 `error`（其余门的结果全部丢失）。
        #   现在它是一档**正常因由**：机器观察到了"读不到"，就得读出来。
        #
        # ★★★★★ 修复 R11：**`in` 支持目录**（让门的范围与它声称的意图相符）。
        #   实测缺陷：档案里的 `no_scaffold` 门声称"禁止 todo!/unimplemented!
        #   脚手架"，`in` 却只写了一个文件 `{repo}/src/lib.rs`；而本 crate 的
        #   `lib.rs` 基本只有 `mod` 声明 ⇒ **门的范围远小于它声称的意图**
        #   ⇒ 任意模块文件里的 `todo!()` 都查不到（门绿了但没真查）。
        #   ★ 不能靠"给每个文件写一条门"来补 —— 那是**枚举**：文件增删就得改档案，
        #     漏一个就静默漏查。⇒ 让门**能扫目录**：一条门覆盖全树，自动跟上增删。
        _in = _入参路径(g, "in", path, work_dir)
        pats = _pats_of(g)
        if not pats:
            return False, {"err": "grep 门缺 pattern/patterns 字段"}
        # ── 收集待扫文本（文件=1 份；目录=递归多份，逐份记住**来自哪个文件**）──
        if _in.is_dir():
            份: list[tuple[str, str]] = []
            跳过: list[str] = []
            for f in sorted(_in.rglob("*")):
                if not f.is_file():
                    continue
                if any(x in f.parts for x in ("__pycache__", ".git", "target",
                                              "node_modules")):
                    continue
                try:
                    if f.stat().st_size > _GREP_MAX_BYTES:
                        跳过.append(f"{f.name}(超{_GREP_MAX_BYTES}B)")
                        continue
                    份.append((str(f.relative_to(_in)).replace("\\", "/"),
                               f.read_text(encoding="utf-8", errors="ignore")))
                except OSError:
                    跳过.append(f"{f.name}(读不出)")
                if len(份) >= _GREP_MAX_FILES:
                    跳过.append(f"…还有更多（已达 {_GREP_MAX_FILES} 份上限）")
                    break
            if not 份 and not 跳过:
                return False, {"err": f"禁词门目录为空：{_in.name}",
                               "empty_dir": True}
        else:
            try:
                text = _in.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                return False, {"err": f"禁词门读不到被测文件：{_in.name}"
                                      f"（{type(exc).__name__}）",
                               "unreadable": True}
            份 = [("", text)]
            跳过 = []
        # ★ R12：可选剥注释/字符串再匹配（关掉源码文本门的假红类）
        _剥 = str(g.get("strip") or "").lower() in ("comments", "code", "注释")
        if _剥:
            份 = [(r, strip_for_matching(x)) for r, x in 份]
        hits: list = []
        # ★【TS-442 无损读】同时记下**是哪个模式命中**。
        #   旧版只回 `hits`（匹配到的**文本**）⇒ 两个不同模式命中同一段文本时
        #   因由被压平（又一处 `Defect384`）。记模式名才能分辨"哪条约束被破"。
        # ★ R11 续：目录扫描时**还要记哪个文件命中** —— 同理，不压平。
        hit_pats: list[str] = []
        hit_files: list[str] = []
        bad_pats: list[str] = []
        for 相对, text in 份:
            for p in pats:
                # ★ 修复 R1-F5（续）：**正则非法也是因由，不是异常**。
                #   旧实现 `re.findall` 抛 `re.error` ⇒ 同样把整个任务带崩。
                try:
                    f = re.findall(p, text)
                except re.error as exc:
                    bad_pats.append(f"{p}（{exc.msg}）")
                    continue
                if f:
                    if p not in hit_pats:
                        hit_pats.append(p)
                    if 相对 and 相对 not in hit_files:
                        hit_files.append(相对)
                    hits += f
        if bad_pats:
            return False, {"err": "禁词门正则非法：" + ";".join(bad_pats),
                           "bad_patterns": bad_pats}
        因由 = {"hits": hits, "hit_patterns": hit_pats}
        if hit_files:
            因由["hit_files"] = hit_files
        if 跳过:
            # ★ 有界必须**如实报**：触顶/跳过的文件不能静默吞掉，
            #   否则门会"因为没扫到"而假绿。
            因由["skipped"] = 跳过
        return not hits, 因由

    if typ == "grep_required":
        text, _读错 = _读入参(g, "in", path, work_dir)
        if text is None:
            return False, {"err": _读错}
        pats = _pats_of(g)
        if not pats:
            return False, {"err": "grep 门缺 pattern/patterns 字段"}
        missing = [p for p in pats if p not in text]
        return not missing, {"missing": missing}

    if typ == "scan_output":
        src = _入参路径(g, "src", path, work_dir)
        append = g.get("append", "")
        # ★ R118：**读不到 ≠ 空文件**。空文件是合法输入，读不到不是。
        #   旧写法 `if src.exists() else ""` 把两者压成同一个值
        #   ⇒ `scan_output` 对空串返回 0 就判绿（自检当场抓到）。
        text, _读错3 = _读入参(g, "src", path, work_dir)
        if text is None:
            return False, {"err": _读错3}
        tmp = Path(work_dir) / f"_{src.stem}_scan.tmp"
        tmp.write_text(text + append, encoding="utf-8")
        try:
            cmd = _sub(g.get("cmd", []), file=str(tmp), work=str(work_dir),
                       src=str(src))
            cwd = g.get("cwd", cwd_default)
            rc, out, err = _run(cmd, cwd, g.get("timeout", 300))
            comb = out + err
            hits = [t for t in g.get("must_not", []) if t in comb]
            ok = rc == int(g.get("expect_exit", 0)) and not hits
            return ok, {"rc": rc, "must_not_hits": hits}
        finally:
            tmp.unlink(missing_ok=True)

    if typ == "axiom_footprint":
        # ★★★ 公理足迹**白名单**门（2026-10-13 补，修实测漏洞）。
        #
        # 缺口：原 `scan_output` + `must_not: [sorryAx]` 是**黑名单** ——
        #   只能抓住"想到过"的作弊。实测：模型写
        #       axiom cheat : <命题>
        #       theorem 目标 ... := by exact cheat
        #   ⇒ `#print axioms` 只列 `cheat`，**不含 sorryAx** ⇒ 四门全绿，
        #   被误判为"证明完成"（实测已复现）。这是**判据没有区分力**的
        #   又一形态：黑名单的区分力上限 = 设计者的想象力。
        #
        # 修法：改成**白名单** —— 只允许 Lean 三条标准公理，
        #   其余任何名字（含模型自声明的 axiom）一律红。
        #   ⇒ 区分力不再依赖"我想到哪些作弊"，而是"除白名单外全红"。
        #
        # 诚实边界：本门**只查公理足迹**，不查"证明是否真有内容"。
        #   若模型把命题弱化到恒真，本门不拦（那由 `statement_intact`
        #   的陈述锚定与 `no_forbidden` 共同守）。本门守的是**不靠假公理**。
        src = _入参路径(g, "src", path, work_dir)
        append = g.get("append", "")
        # ★ R118：**读不到 ≠ 空文件**。空文件是合法输入，读不到不是。
        #   旧写法 `if src.exists() else ""` 把两者压成同一个值
        #   ⇒ `scan_output` 对空串返回 0 就判绿（自检当场抓到）。
        text, _读错3 = _读入参(g, "src", path, work_dir)
        if text is None:
            return False, {"err": _读错3}
        tmp = Path(work_dir) / f"_{src.stem}_axf.tmp"
        try:
            cmd = _sub(g.get("cmd", []), file=str(tmp), work=str(work_dir),
                       src=str(src))
            cwd = g.get("cwd", cwd_default)
            rc, comb, err, 退过 = _跑探针带退回(
                cmd, cwd, g.get("timeout", 300), g.get("stall"), text, append,
                tmp, "公理足迹")
            if rc != int(g.get("expect_exit", 0)):
                # ★ 2026-11-01：同 kernel_statement —— 把内核原文错误带回，
                #   否则"探针名字写错"与"产物真错"是同一个因由（门恒红不可诊断）。
                _错行 = [l.strip() for l in comb.splitlines()
                         if "error" in l.lower()][:2]
                return False, {"rc": rc, "err": "命令非零退出，无法判公理足迹",
                               "kernel_error": _错行,
                               "hint": "若错误是 unknownIdentifier：探针里的名字两种写法"
                                       "都解析不了（产物里没有这个声明？）",
                               "probe_fallback": 退过}
            m = _AXIOM_LINE.search(comb)
            if not m:
                # 找不到 `depends on axioms` 行 ⇒ **无法确认** ⇒ 如实红
                #   （不默认放行：反作弊门必须 fail-closed）
                if "does not depend on any axioms" in comb:
                    return True, {"axioms": [], "note": "不依赖任何公理"}
                return False, {"err": "输出里找不到公理足迹行 ⇒ 无法确认，如实红"}
            names = [x.strip() for x in m.group(1).split(",") if x.strip()]
            # 允许集：白名单 + 可选的 allowed_extra（供工程加自己的公理）
            allowed = set(_LEAN_STD_AXIOMS)
            allowed |= {str(x) for x in (g.get("allowed") or [])}
            bad = [n for n in names if n.split(".")[-1] not in allowed
                   and n not in allowed]
            return not bad, {"axioms": names, "forbidden_axioms": bad,
                             "allowed": sorted(allowed), "probe_fallback": 退过}
        finally:
            tmp.unlink(missing_ok=True)

    if typ == "kernel_statement":
        # ★★★★★ 内核陈述门 —— **换读法**，不是"让门演化"（2026-10-13 补）。
        #
        # ── 缺口（实测）──
        #   `statement_intact` 用 `grep_required` **读文件文本**。实测：
        #   把陈述写在**注释**里、正文写 `theorem X : True := by trivial`
        #   ⇒ 门判**绿** ⇒ 作弊产物四门全绿被当成"证明完成"。
        #
        # ── 为什么"让门演化"救不了它（库里机证，实测确认）──
        #   TS428 `separating_predicate_needs_distinct_readings`（:203）：
        #     能分开两点 ⇒ **读值必须不同**。
        #   TS428 `fibers_are_indistinguishable_for_any_pipeline_predicate`（:224）：
        #     读值**相同** ⇒ **任何**经管线的判据都分不开。
        #   实测：注释版与真版在**文本读法**下读值**相同**（都含那串字符）
        #   ⇒ 派生再多轮细分判别式都**分不开**（TS384 `defect_is_endogenous_drive`
        #     的前提"存在可分开的两点"不满足）。
        #   ⇒ ★ 演化只能沿**读值已分开**的方向细分；读值分不开的，演化无效。
        #
        # ── 修法：重定位（TS430 `relocate`，:206）──
        #   把观测者从"文件字节"搬到"**内核展开后的类型**"。
        #   实测：注释版内核读 `: ∀ (n k : ℕ), True`，真版读真陈述 ⇒ **读值不同**
        #   ⇒ 门就能分开。★ 注释**改不了内核说什么** ⇒ 这个读法是**无损的**。
        #
        #   ★ 与 TS430 的对应：
        #     `decideInjective`（:165）= 判定器 → 本门的 `must_contain` 比对
        #     `relocate`（:206）        = 重定位 → 从 `in:{file}` 改读 `#check` 输出
        #     `relocation_keeps_objects`（:230）= **不改样本戊** → 被测产物**不变**，
        #        只是**换了个地方读它**（这里读内核，不读字节）
        #
        # 诚实边界：本门只保证"内核里的类型**包含**给定陈述片段"。
        #   它**不**保证证明有内容（那由 `axiom_footprint` 守），
        #   也**不**保证陈述与源模块逐字相同（那由 `whole_reading` 守）。
        src = _入参路径(g, "src", path, work_dir)
        append = g.get("append", "")
        # ★ R118：**读不到 ≠ 空文件**。空文件是合法输入，读不到不是。
        #   旧写法 `if src.exists() else ""` 把两者压成同一个值
        #   ⇒ `scan_output` 对空串返回 0 就判绿（自检当场抓到）。
        text, _读错3 = _读入参(g, "src", path, work_dir)
        if text is None:
            return False, {"err": _读错3}
        tmp = Path(work_dir) / f"_{src.stem}_kstmt.tmp"
        try:
            cmd = _sub(g.get("cmd", []), file=str(tmp), work=str(work_dir),
                       src=str(src))
            cwd = g.get("cwd", cwd_default)
            rc, comb, err, 退过 = _跑探针带退回(
                cmd, cwd, g.get("timeout", 300), g.get("stall"), text, append,
                tmp, "内核陈述")
            if rc != int(g.get("expect_exit", 0)):
                # ★★★ 2026-11-01 真跑回归抓出：原来这里只回一句"命令非零退出"，
                #   把**探针名字的写法问题**（限定名 vs 非限定名，见 `_探针退回` 的注释）
                #   和**产物真错**压成同一个因由 ⇒ 门恒红而没人看得出为什么。
                #   现在：① 先自动退回非限定名重试；② 仍红就把内核原文错误带回审计。
                _错行 = [l.strip() for l in comb.splitlines()
                         if "error" in l.lower()][:2]
                return False, {"rc": rc, "err": "命令非零退出，内核读法无法执行",
                               "kernel_error": _错行,
                               "probe_fallback": 退过,
                               "hint": "两种写法都解析不了 ⇒ 产物里可能没有这个声明"
                                       "（不是门的读法问题）"}
            # ★ 读的是**内核展开后的类型**：规范化后判包含（跨排版稳定）
            #   ★ 用 `_canon_kernel`（含符号名归一）—— 内核回显 `ℕ` 而
            #     陈述里写 `Nat`，不归一会**假红**（读法引入的假红）。
            读值 = _canon_kernel(comb)
            需 = [_canon_kernel(str(x)) for x in (g.get("must_contain")
                                                or g.get("patterns") or [])]
            需 = [x for x in 需 if x]
            if not 需:
                return False, {"err": "内核陈述门缺 must_contain/patterns"}
            缺 = [x for x in 需 if x not in 读值]
            # ★ 顺带把内核读到的类型行回传，供审计留痕（可回放）
            类型行 = [l.strip() for l in comb.splitlines()
                     if l.strip() and ":" in l and not l.strip().startswith("#")]
            return not 缺, {"missing": 缺, "kernel_read": 类型行[:3],
                            "read_space": "kernel_type", "probe_fallback": 退过}
        finally:
            tmp.unlink(missing_ok=True)

    if typ == "whole_reading":
        被测 = _入参路径(g, "in", path, work_dir)
        原文, _读错2 = _读入参(g, "in", path, work_dir)
        if 原文 is None:
            return False, {"err": _读错2}
        # ★★★ 默认**读代码，不读注释**（2026-10-13 补 · 修产品可信度缺口）。
        #   实测：要求实现中文方法名 `推入`/`取出`/`长度`，模型写成
        #   `push`/`pop`/`len`，而门匹配到了文件顶部**过时的占位注释**
        #   （`//! 待实现：推入 / 取出 / 长度`）⇒ 判"达标"，
        #   **用户的要求根本没实现**。⇒ 默认剥注释。
        #   要"必须写文档"类要求时，显式 `include_comments: true`。
        text = 原文 if g.get("include_comments") else _去注释(原文, 被测.suffix)
        canon = _canon(text)          # 读整体一次判：一次读 + 一次稳定规范化
        mode = g.get("mode", "contains")
        if mode == "equal":
            exp = g.get("expect", g.get("expect_text"))
            if isinstance(exp, str) and exp.strip():
                exp_sig = _sig(_canon(exp))
            else:
                exp_sig = g.get("expect_sig")
            got_sig = _sig(canon)
            ok = bool(exp_sig) and got_sig == exp_sig
            return ok, {"sig": got_sig, "expect_sig": exp_sig}
        if mode == "forbidden":
            emp = [str(x) for x in (g.get("must_not") or [])]
            hits = [m for m in emp if _canon(m) in canon]
            return not hits, {"must_not_hits": hits}
        # mode == "contains"（默认）：一次归一化后内存子串全命中，吸收偏扫描多 pattern。
        # 定位键同样归一化（去空白）——格式差异不再影响"是否存在该结构键"（紧致极值判）。
        markers = g.get("must_contain")
        if not isinstance(markers, list):
            markers = g.get("patterns")
        if not isinstance(markers, list):
            markers = _pats_of(g)
        if not markers:
            return False, {"err": "whole_reading contains 缺 must_contain/patterns"}
        missing = [m for m in markers if _canon(m) not in canon]

        # ★★★ 2026-10-14 修（第 13 处"判据读错了东西"，且是纯假红）：
        #   实测：门 `gap_marker_closed_with_evidence` 要求 must_contain 含
        #   `【已闭合】`，而该标记**就写在注释里**（`// 【已闭合】本测试用 …`）。
        #   上面缺省剥注释 ⇒ **门把要找的东西自己删掉了** ⇒ 恒红。
        #   证据：候选产物里 `【已闭合】` 明明在 L8463、新测试名明明在 L8612，
        #   门仍报 `missing: ["【已闭合】"]` ⇒ 任务永远不可能收敛。
        #   修法（**保守**，不放宽反作弊）：按**标记自身能不能住在代码里**判：
        #     · 含 `【】`/`//`/`/*` 等**代码里不可能出现**的注释符 ⇒ 只能住注释
        #       ⇒ 这一条按**原文**判；
        #     · 像 `推入`/`push`/`pub fn x` 这种**可能是代码**的 ⇒ 仍只读代码
        #       （防上面 2026-10-13 那个"过时占位注释满足实现要求"回潮）。
        注释类命中: list[str] = []
        if missing and not g.get("include_comments"):
            注释类 = [m for m in missing if _只能在注释里(m)]
            if 注释类:
                原canon = _canon(_strip_ansi(原文))
                仍缺 = [m for m in 注释类 if _canon(m) not in 原canon]
                注释类命中 = [m for m in 注释类 if m not in 仍缺]
                missing = [m for m in missing if m not in 注释类] + 仍缺
        审计 = {"missing": missing}
        if 注释类命中:
            # 如实记账：这条是靠**注释**满足的，不是靠代码 —— 不静默。
            审计["satisfied_in_comments"] = 注释类命中
        return not missing, 审计

    if typ == "differential":
        # ★★★ 2026-11-01 判据多样性门族 ①：**差分对比门**。
        #
        # 缺口（交付验收里如实写着"判据多样性门族未实现"）：此前的门只有一族 →
        #   「命令跑通了吗 / 文件里有这个词吗」。它能回答"报没报错"，**回答不了**
        #   "改完之后的行为和参照实现是不是同一个"。重构/换实现时这是真风险：
        #   两版都 exit 0，语义已经分叉。
        #
        # 本门同时跑 `cmd`（被测）与 `ref`（参照：黄金实现/旧实现/独立实现），要求
        #   ① 两条都按 expect_exit 退出；② **规范化后输出逐字相同**。
        #   `normalize` 可给 [[正则, 替换], …] 压掉路径/耗时/地址等噪声 ——
        #   压噪声是为了让"分歧"只反映行为差异，不是为了放过差异（默认不压任何东西）。
        命令 = _sub(g.get("cmd", []), file=str(path), work=str(work_dir))
        参照 = _sub(g.get("ref", []), file=str(path), work=str(work_dir))
        if not 命令 or not 参照:
            return False, {"err": "differential 需要 cmd 与 ref 两条命令"}
        cwd = g.get("cwd", cwd_default)
        期望 = int(g.get("expect_exit", 0))
        rc1, out1, err1 = _run(命令, cwd, g.get("timeout", 300), g.get("stall"),
                              "differential-被测")
        rc2, out2, err2 = _run(参照, cwd, g.get("timeout", 300), g.get("stall"),
                              "differential-参照")
        if rc1 != 期望 or rc2 != 期望:
            return False, {"rc": rc1, "rc_ref": rc2,
                           "err": _evidence(out1, err1), "err_ref": _evidence(out2, err2)}
        规 = g.get("normalize") or []
        文1 = _差分规整(out1 + err1, 规)
        文2 = _差分规整(out2 + err2, 规)
        同 = 文1 == 文2
        异 = -1 if 同 else _首异处(文1, 文2)
        return 同, {"same": 同, "first_diff_at": 异,
                    "out": "" if 同 else 文1[max(0, 异 - 80):异 + 160],
                    "ref_out": "" if 同 else 文2[max(0, 异 - 80):异 + 160]}

    if typ == "property":
        # ★★★ 2026-11-01 判据多样性门族 ②：**属性测试门**。
        #
        # 差分门回答"和参照实现一致吗"；本门回答"**对一组输入都成立吗**"。
        # 只跑一条样例的门有个通病：样例能过 ≠ 规律成立（模型可以只把样例喂对）。
        # 这里要求同一条命令在 `cases`（每个 case = 追加参数列表）上**逐个成立**，
        # 并可要求 `repeat` 次**输出一致**（确定性属性：抓"跑两次两个结果"的抖动）。
        #
        # 诚实边界：`cases` 由写门的人给 ⇒ 本门不产生新知识，它只是**不放过特判**。
        用例集 = g.get("cases")
        if not isinstance(用例集, list) or not 用例集:
            return False, {"err": "property 需要 cases（每项 = 追加参数列表）"}
        基 = list(g.get("cmd", []))
        if not 基:
            return False, {"err": "property 需要 cmd"}
        cwd = g.get("cwd", cwd_default)
        期望 = int(g.get("expect_exit", 0))
        必含 = [str(x) for x in (g.get("require") or [])]
        禁含 = [str(x) for x in (g.get("forbid") or [])]
        重复 = max(1, int(g.get("repeat", 1)))
        要一致 = bool(g.get("same_output", False))
        已跑 = 0
        for k, case in enumerate(用例集):
            追加 = case if isinstance(case, list) else [str(case)]
            命令 = _sub(基 + [str(x) for x in 追加],
                        file=str(path), work=str(work_dir))
            首文 = None
            for r in range(重复):
                rc, out, err = _run(命令, cwd, g.get("timeout", 300), g.get("stall"),
                                   f"property-case{k}")
                已跑 += 1
                组合 = out + err
                if rc != 期望:
                    return False, {"case": k, "run": r, "rc": rc, "cases": len(用例集),
                                   "ran": 已跑, "err": _evidence(out, err)}
                缺 = [m for m in 必含 if m not in 组合]
                if 缺:
                    return False, {"case": k, "run": r, "missing": 缺, "cases": len(用例集),
                                   "ran": 已跑}
                碰 = [m for m in 禁含 if m in 组合]
                if 碰:
                    return False, {"case": k, "run": r, "forbidden_hits": 碰,
                                   "cases": len(用例集), "ran": 已跑}
                if 要一致:
                    文 = _差分规整(组合, [])
                    if 首文 is None:
                        首文 = 文
                    elif 文 != 首文:
                        return False, {"case": k, "run": r, "nondeterministic": True,
                                       "first_diff_at": _首异处(首文, 文),
                                       "cases": len(用例集), "ran": 已跑}
        return True, {"cases": len(用例集), "ran": 已跑, "repeat": 重复,
                      "same_output": 要一致}

    raise ValueError(f"unknown gate type: {typ}")


def evaluate_lossless(spec, path: Path, work_dir: Path, cwd_default=".",
                      file_root: Path | None = None,
                      ) -> tuple[dict[str, str], dict[str, bool], list[dict]]:
    """**无损读数**版：返回 (因由:{门名:因由码}, results:{门名:bool}, audit)。

    ★ 这是 TS-442/TS-447/TS-424 的工程落形（见 `判据因由` 的段注释）：
      · `因由` 是**读**（无损，携带具体证据，码空间无界）；
      · `results` 是**投影**（`Bool`，只用于"是否全绿"这类必须二值的判定）。
      ⇒ 二者**同时**给出，且 `results[k] == 通过(因由[k])` **恒成立**
        （由本函数唯一构造，故不会两处不一致）。

    ★ 调用方守则：**要诊断、要回喂模型、要记账，一律用 `因由`**；
      只有"全绿否 / 该不该换门集"这类二值决策才用 `results`。
      用 `results` 做诊断 = 回到 `Bool` 投影 = 重新制造 `Defect384`。
    """
    if spec is None:
        return {}, {}, []
    _tok = _文件根.set(Path(file_root) if file_root is not None else None)
    try:
        return _evaluate_lossless_inner(spec, path, work_dir, cwd_default)
    finally:
        _文件根.reset(_tok)


def _evaluate_lossless_inner(spec, path: Path, work_dir: Path, cwd_default=".",
                             ) -> tuple[dict[str, str], dict[str, bool], list[dict]]:
    items = spec if isinstance(spec, list) else [spec]
    因由: dict[str, str] = {}
    results: dict[str, bool] = {}
    audit: list[dict] = []
    for g in items:
        name = g.get("name", g.get("type", "gate"))
        ok, info = _run_one(g, path, work_dir, cwd_default)
        码 = 判据因由(g, ok, info)
        因由[name] = 码
        # ★ 投影由因由**唯一**导出 —— 不让 `ok` 与 `码` 各自独立地进结果，
        #   否则两者可能不一致（那会造出"账本说绿、判据说红"的新假象）。
        results[name] = 通过(码)
        audit.append({"gate": name, "file": path.name, "ok": results[name],
                      "cause": 码, **info})
    return 因由, results, audit


def evaluate(spec, path: Path, work_dir: Path, cwd_default=".",
             file_root: Path | None = None,
             ) -> tuple[dict[str, bool], list[dict]]:
    """按规格现场执行判据门，返回 (results:{门名:bool}, audit:list)。

    audit 条目 = {"gate": 门名, "file": 被测文件名, "ok": bool,
                  "cause": 因由码, **原语信息}，与既有状态审计结构一致，全量可回放。

    ★ `cause` 是**无损读数**（TS-442/TS-447/TS-424 落形）；`ok` 是它的 `Bool` 投影。
      诊断/回喂请用 `cause`，或用 `evaluate_lossless` 直接取全部因由。

    ★ 本函数是**同步阻塞**的（内部 subprocess.run，compile 门可能跑数分钟到数十分钟）。
      在协程里直接调用会**冻住整个事件循环**：同进程的 MCP 服务器将无法收发任何
      消息，正在等待的工具响应被推迟到门跑完才发出（实机表现为"提交任务永不回包"）。
      协程上下文请改用 `await evaluate_async(...)`。"""
    _, results, audit = evaluate_lossless(spec, path, work_dir, cwd_default, file_root)
    return results, audit


async def evaluate_async(spec, path: Path, work_dir: Path, cwd_default=".",
                         file_root: Path | None = None,
                         ) -> tuple[dict[str, bool], list[dict]]:
    """`evaluate` 的**不冻事件循环**版本：把阻塞门执行放到线程里跑。

    ★ 修复（2026-10-12）：判据门执行是同步 subprocess.run，compile 门动辄数分钟。
      在协程里直接 `evaluate(...)` 会把 MCP 服务器的事件循环冻住 —— 服务器既不能
      响应其他工具调用，也不能把自己刚返回的响应写出去（实机：提交任务后客户端
      等 240s 收不到任何回包，而 stderr 显示任务 8.8s 就跑完了）。
      用 `asyncio.to_thread` 把这段阻塞挪出事件循环：门照跑，服务器照常收发。
    """
    import asyncio
    return await asyncio.to_thread(evaluate, spec, path, work_dir, cwd_default, file_root)


def _源码R117() -> str:
    """本文件的源码（齿用它看**实现形态**，而不只是行为）。"""
    return Path(__file__).read_text(encoding="utf-8")


def _齿R117() -> list[str]:
    """★ R117 的齿：**同一个 `in`，见证器与门执行器必须解析成同一个路径。**

    ★ 为什么这条齿必须在 `gate_spec` 里：缺陷就在这个文件里。
      而它此前**没有任何症状** —— 门照跑、照绿，只是读的是 cwd 下的
      同名文件（或者读不到而报「疑恒真门」）。
      「坏了没症状」的那一类，只能靠**两条路互相比对**才发现。
    """
    import tempfile as _tf
    fails: list[str] = []
    根 = Path(_tf.mkdtemp(prefix="r117_"))
    try:
        (根 / "src").mkdir(parents=True)
        (根 / "src" / "真.rs").write_text("pub fn 目标() {}\n", encoding="utf-8")
        # ① 相对 `in` ⇒ 必须按 work_dir 解析
        g = {"name": "t", "type": "grep_required", "in": "src/真.rs",
             "patterns": ["目标"]}
        绿, _ = _run_one(g, 根 / "src" / "真.rs", 根, ".")
        if not 绿:
            fails.append("【R117】★ 相对 `in` 没按 work_dir 解析 ⇒ "
                         "门读不到工程根下的文件（真跑里 10 条好门被这样误杀）")
        # ② 绝对 `in` ⇒ 原样（不许被 work_dir 改写）
        g2 = dict(g, **{"in": str(根 / "src" / "真.rs")})
        绿2, _ = _run_one(g2, 根 / "src" / "真.rs", 根, ".")
        if not 绿2:
            fails.append("【R117】★ 绝对 `in` 被改写 ⇒ 调用方明确指定的路径不许动")
        # ★ ②b 但这条行为齿**测不出实现差异**：`Path(a) / Path("C:/x")` 按
        #   pathlib 语义会**丢弃左侧** ⇒ 「拼」与「不拼」结果相同（实测确认）。
        #   ⇒ 真正要守的是「用的是 pathlib 除法，不是字符串拼接」——
        #     后者会把绝对路径拼坏（`"C:/x" + "/" + "D:/y"` 得到垃圾）。
        # ★★★★★ 用 **AST** 判，不用文本 —— 这是「门匹配到自己」第 14 次。
        #   前 13 次都是针匹配到自己的注释；这次是匹配到**齿自己所在函数的
        #   docstring**：`_入参路径` 的 docstring 里就引用了
        #   `p = Path(work_dir) / p`（作为「见证器怎么做的」的说明）
        #   ⇒ 把实现改成字符串拼接后，docstring 里那句还在 ⇒ 齿仍然绿。
        #   ★ 结论：**文本会骗人，语法树不会。** 这条规矩已经用过 4 次，
        #     每次都是先试文本、被咬、再换 AST。这次直接上 AST。
        import ast as _a117
        _树 = _a117.parse(_源码R117())
        _fn = next((n for n in _树.body
                    if isinstance(n, _a117.FunctionDef) and n.name == "_入参路径"),
                   None)
        if _fn is None:
            fails.append("【R117】★ 找不到 `_入参路径` 的定义（AST）")
        else:
            # 找 `return Path(work_dir) / p` 这一句：必须是 BinOp(Div)，
            # 左 = Call(Name('Path'), [Name('work_dir')])，右 = Name('p')
            _好 = False
            for _n in _a117.walk(_fn):
                if not isinstance(_n, _a117.Return) or _n.value is None:
                    continue
                _v = _n.value
                if not (isinstance(_v, _a117.BinOp)
                        and isinstance(_v.op, _a117.Div)):
                    continue
                _l, _r = _v.left, _v.right
                if (isinstance(_l, _a117.Call)
                        and isinstance(_l.func, _a117.Name)
                        and _l.func.id == "Path"
                        and len(_l.args) == 1
                        and isinstance(_l.args[0], _a117.Name)
                        and _l.args[0].id == "work_dir"
                        and isinstance(_r, _a117.Name) and _r.id == "p"):
                    _好 = True
                    break
            if not _好:
                fails.append("【R117】★ `_入参路径` 的返回不是 `Path(work_dir) / p`"
                             "（AST 判）⇒ 字符串拼接会把绝对路径拼坏")
        # ③ ★ 与 `gate_witness._gate_read_target` **同口径**（两把尺子合一）
        try:
            from agentscope_orchestrator import gate_witness as _gw
            _a = _入参路径(g, "in", 根 / "src" / "真.rs", 根).resolve()
            _b = _gw._gate_read_target(g, 根 / "src" / "真.rs", 根).resolve()
            if _a != _b:
                fails.append(f"【R117】★ 两把尺子仍然不一致："
                             f"门执行器→{_a.name} 见证器→{_b.name}")
        except Exception as exc:
            fails.append(f"【R117】齿③自身异常：{type(exc).__name__}: {exc}")
        # ④ `src` 字段同规矩
        g3 = {"name": "t3", "type": "whole_reading", "src": "src/真.rs",
              "patterns": ["目标"]}
        绿3, _ = _run_one(g3, 根 / "src" / "真.rs", 根, ".")
        if not 绿3:
            fails.append("【R117】★ `src` 字段没按 work_dir 解析 ⇒ 与 `in` 又成两把尺子")
        # ── ⑤ ★★ R118：**读不到必须是因由，不能是异常** ──────────────
        #   真跑证据：10 条门里 7 条报
        #     `FileNotFoundError` ⇒ 被吞成「门执行异常 ⇒ 不假装已验」
        #   ⇒ 什么都验不了，而日志看起来像"验过了但没通过"。
        #   ★ 每一个**会读文件**的门型都必须单独验一次 —— 这正是
        #     R1-F5 漏掉两个门型的原因（它只测了 grep_forbidden）。
        for _型, _门 in (
                ("grep_forbidden", {"name": "a", "type": "grep_forbidden",
                                    "in": "src/不存在.rs", "pattern": "x"}),
                ("grep_required", {"name": "b", "type": "grep_required",
                                   "in": "src/不存在.rs", "patterns": ["x"]}),
                ("whole_reading", {"name": "c", "type": "whole_reading",
                                   "in": "src/不存在.rs", "patterns": ["x"]}),
                ("scan_output", {"name": "d", "type": "scan_output",
                                 "src": "src/不存在.rs",
                                 "cmd": ["cmd", "/c", "echo", "ok"]})):
            try:
                _绿, _信 = _run_one(_门, 根 / "src" / "真.rs", 根, ".")
                if _绿:
                    fails.append(f"【R118】★ `{_型}` 读不到文件却判绿 ⇒ 假绿")
                elif "读不到" not in str(_信.get("err") or ""):
                    fails.append(f"【R118】★ `{_型}` 读不到但没报「读不到」"
                                 f"（err={str(_信.get('err'))[:80]!r}）⇒ "
                                 f"分不清「读不到」与「内容不符」")
            except Exception as _e:
                fails.append(f"【R118】★ `{_型}` 读不到时**抛异常**"
                             f"（{type(_e).__name__}）⇒ 一条坏门带崩所有门"
                             f"（R1-F5 只修了 grep_forbidden，漏了这里）")
    finally:
        import shutil as _sh
        _sh.rmtree(根, ignore_errors=True)
    return fails


def _selftest() -> int:
    """自检：全部机器可算（用假命令测**解析+白名单逻辑**，不依赖 Lean）。

    ★ 为什么要自检这个模块：`axiom_footprint` 是**反作弊门**，
      它坏了不会有任何症状 —— 门照绿，只是把作弊放过去。
      故必须有回归。这里用假命令产出**确定的公理足迹文本**，
      测的是我写的那段解析与白名单判断（慢的 Lean 调用另有真跑验证）。
    """
    import shutil
    import sys
    import tempfile

    ok = total = 0

    def chk(name: str, cond: bool, 说明: str = "") -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  ✗ {name}" + (f"（{说明}）" if 说明 else ""))

    d = Path(tempfile.mkdtemp(prefix="gs_st_"))

    def 造门(输出: str, allowed=None, expect_exit=0) -> dict:
        """造一个 axiom_footprint 门：假命令直接打印给定输出。"""
        py = ("import sys; sys.stdout.write(" + repr(输出) + ")")
        g = {"name": "no_sorry_axiom", "type": "axiom_footprint",
             "src": "{file}", "append": "",
             "cmd": [sys.executable, "-c", py], "cwd": str(d),
             "expect_exit": expect_exit}
        if allowed is not None:
            g["allowed"] = allowed
        return g

    try:
        f = d / "T.lean"
        f.write_text("theorem t : True := trivial\n", encoding="utf-8")
        标 = ["propext", "Classical.choice", "Quot.sound"]

        # ① 标准公理 ⇒ 放行
        g = 造门("'t' depends on axioms: [propext, Classical.choice]\n", 标)
        r, _ = evaluate([g], f, d, str(d))
        chk("标准公理放行", r["no_sorry_axiom"] is True)

        # ② ★ 自声明公理 ⇒ 拦（这是本次修的实测漏洞）
        g = 造门("'t' depends on axioms: [propext, cheat_axiom]\n", 标)
        r, au = evaluate([g], f, d, str(d))
        chk("★ 自声明公理被拦", r["no_sorry_axiom"] is False)
        chk("★ 报出违禁公理名",
            "cheat_axiom" in (au[0].get("forbidden_axioms") or []))

        # ③ sorryAx ⇒ 拦（原有能力不退化）
        g = 造门("'t' depends on axioms: [propext, sorryAx]\n", 标)
        r, _ = evaluate([g], f, d, str(d))
        chk("sorryAx 仍被拦", r["no_sorry_axiom"] is False)

        # ④ 全限定名也能认（X.cheat ⇒ 取末段比对）
        g = 造门("'t' depends on axioms: [Orchestrated_auto.cheat]\n", 标)
        r, au = evaluate([g], f, d, str(d))
        chk("全限定违禁名被拦", r["no_sorry_axiom"] is False)

        # ⑤ 不依赖任何公理 ⇒ 放行
        g = 造门("'t' does not depend on any axioms\n", 标)
        r, _ = evaluate([g], f, d, str(d))
        chk("无公理依赖放行", r["no_sorry_axiom"] is True)

        # ⑥ ★ fail-closed：输出里没有足迹行 ⇒ 红（不默认放行）
        g = 造门("随便什么输出，没有足迹行\n", 标)
        r, _ = evaluate([g], f, d, str(d))
        chk("★ 无足迹行 ⇒ 红（fail-closed）", r["no_sorry_axiom"] is False)

        # ⑦ 命令非零退出 ⇒ 红
        g = 造门("'t' depends on axioms: [propext]\n", 标, expect_exit=1)
        r, _ = evaluate([g], f, d, str(d))
        chk("命令非零退出 ⇒ 红", r["no_sorry_axiom"] is False)

        # ⑧ ★ 设计决策（钉死）：标准三公理**始终**放行，`allowed` 是**追加**而非替换。
        #   理由：propext/Classical.choice/Quot.sound 是 Lean **内核自带**的
        #   数学界公认公理，不是"作弊"；反作弊门要拦的是**模型自声明**的公理。
        #   故不给 allowed 时，propext 仍应放行。
        g = 造门("'t' depends on axioms: [propext]\n", [])
        r, _ = evaluate([g], f, d, str(d))
        chk("★ 标准公理始终放行（allowed 是追加非替换）",
            r["no_sorry_axiom"] is True)

        # ⑨ allowed 可加工程自有公理
        g = 造门("'t' depends on axioms: [my_engine_axiom]\n", ["my_engine_axiom"])
        r, _ = evaluate([g], f, d, str(d))
        chk("allowed 可加自有公理", r["no_sorry_axiom"] is True)

        # ⑨b ★ 加了自有公理**不削弱**默认白名单
        g = 造门("'t' depends on axioms: [propext, my_engine_axiom]\n",
                 ["my_engine_axiom"])
        r, _ = evaluate([g], f, d, str(d))
        chk("加自有公理不削弱默认白名单", r["no_sorry_axiom"] is True)

        # ⑨c ★ 未列入 allowed 的自有公理仍拦
        g = 造门("'t' depends on axioms: [my_engine_axiom, other_cheat]\n",
                 ["my_engine_axiom"])
        r, au = evaluate([g], f, d, str(d))
        chk("未列入 allowed 的公理仍拦", r["no_sorry_axiom"] is False)
        chk("违禁列表只列未允许的",
            (au[0].get("forbidden_axioms") or []) == ["other_cheat"])

        # ⑩ 门类型已注册
        chk("axiom_footprint 已注册", "axiom_footprint" in _TYPES)

        # ⑪ 空 spec 仍返回空（既有行为不退化）
        r0, a0 = evaluate([], f, d, str(d))
        chk("空门集返回空", r0 == {} and a0 == [])

        # ── 内核陈述门（重定位读法）──
        chk("kernel_statement 已注册", "kernel_statement" in _TYPES)

        # ⑫ ★★ 符号归一**顺序**：NFKC 会把 ℕ 分解成 N，故必须先映射
        chk("★ ℕ 归一到 Nat（不是 N）", "Nat" in _canon_kernel("(n k : ℕ)"))
        chk("★ ℕ 不被 NFKC 吃掉成 N",
            _canon_kernel("(n k : ℕ)") == _canon_kernel("(n k : Nat)"))
        chk("ℤ→Int", _canon_kernel("ℤ") == _canon_kernel("Int"))
        chk("ℝ→Real", _canon_kernel("ℝ") == _canon_kernel("Real"))
        chk("∀→forall", _canon_kernel("∀ x") == _canon_kernel("forall x"))
        chk("∧ 与 /\\ 同形", _canon_kernel("a ∧ b") == _canon_kernel("a /\\ b"))

        # ⑬ 内核读法：用假命令模拟内核输出
        def 造内核门(输出: str, must) -> dict:
            py = "import sys; sys.stdout.write(" + repr(输出) + ")"
            return {"name": "statement_intact", "type": "kernel_statement",
                    "src": "{file}", "append": "",
                    "cmd": [sys.executable, "-c", py], "cwd": str(d),
                    "expect_exit": 0, "must_contain": must}

        内核出 = ("Orchestrated_auto.global_ledger375 (n k : ℕ) :\n"
                 "  locateStep375 = 1 ∧ readAllStep375 k = 3 * k\n")
        g = 造内核门(内核出, ["readAllStep375 k = 3 * k"])
        r, _ = evaluate([g], f, d, str(d))
        chk("内核读法·含片段 ⇒ 绿", r["statement_intact"] is True)

        g = 造内核门(内核出, ["(n k : Nat)"])
        r, _ = evaluate([g], f, d, str(d))
        chk("★ 内核 ℕ vs 陈述 Nat ⇒ 仍绿（不假红）",
            r["statement_intact"] is True)

        g = 造内核门(内核出, ["readAllStep375 k = 999 * k"])
        r, _ = evaluate([g], f, d, str(d))
        chk("内核读法·无片段 ⇒ 红", r["statement_intact"] is False)

        # ⑭ ★ 内核读法**抓得住注释作弊**：注释里有陈述，内核里是 True
        作弊出 = ("Orchestrated_auto.global_ledger375 (n k : ℕ) :\n"
                 "  True\n")
        g = 造内核门(作弊出, ["locateStep375 = 1"])
        r, au = evaluate([g], f, d, str(d))
        chk("★★ 注释作弊被内核读法抓出", r["statement_intact"] is False)
        chk("内核读法标注读空间", au[0].get("read_space") == "kernel_type")

        # ⑮ 缺 must_contain ⇒ 红（不默认放行）
        g = 造内核门(内核出, [])
        r, _ = evaluate([g], f, d, str(d))
        chk("内核门缺 must_contain ⇒ 红", r["statement_intact"] is False)

        # ⑯ 命令非零退出 ⇒ 红
        py = "import sys; sys.exit(3)"
        g = {"name": "statement_intact", "type": "kernel_statement",
             "src": "{file}", "append": "", "cmd": [sys.executable, "-c", py],
             "cwd": str(d), "expect_exit": 0, "must_contain": ["x"]}
        r, _ = evaluate([g], f, d, str(d))
        chk("内核门命令失败 ⇒ 红", r["statement_intact"] is False)

        # ⑰ 逐字相等读法**不受**内核符号归一影响（全局行为未被改）
        chk("_canon 未受内核归一影响", _canon("ℕ") != _canon("Nat"))

        # ═══════════════════════════════════════════════════════════
        # ★★★ 注释剥离：whole_reading 默认**读代码不读散文**
        #   （2026-10-13 补 · 同一类病第五次出现 · 产品可信度缺口）
        # ═══════════════════════════════════════════════════════════
        # ⑱ 真实失败现场复现：过时占位注释冒充"已实现"
        真现场 = (
            "//! 环形队列：固定容量、覆盖式写入。\n"
            "//!\n"
            "//! 待实现（当前为占位）：`推入` / `取出` / `长度`。\n"
            "\n"
            "use std::collections::VecDeque;\n"
            "\n"
            "pub struct 环队<T> { pub 容量: usize, 数据: VecDeque<T> }\n"
            "\n"
            "impl<T> 环队<T> {\n"
            "    pub fn push(&mut self, item: T) { self.数据.push_back(item); }\n"
            "    pub fn pop(&mut self) -> Option<T> { self.数据.pop_front() }\n"
            "    pub fn len(&self) -> usize { self.数据.len() }\n"
            "}\n")
        rf = d / "queue.rs"
        rf.write_text(真现场, encoding="utf-8")
        门 = {"name": "q", "type": "whole_reading", "in": "{file}",
              "must_contain": ["推入", "取出", "长度"]}
        r, _ = evaluate([门], rf, d, str(d))
        chk("★★★ 过时注释不得冒充已实现（默认剥注释 ⇒ 红）",
            r["q"] is False)
        # ⑲ 真的实现了 ⇒ 绿（不能剥过头把真代码也剥掉）
        真实现 = 真现场 + (
            "\nimpl<T> 环队<T> {\n"
            "    pub fn 推入(&mut self, x: T) { self.数据.push_back(x); }\n"
            "    pub fn 取出(&mut self) -> Option<T> { self.数据.pop_front() }\n"
            "    pub fn 长度(&self) -> usize { self.数据.len() }\n"
            "}\n")
        rf.write_text(真实现, encoding="utf-8")
        r, _ = evaluate([门], rf, d, str(d))
        chk("★ 真实现 ⇒ 绿（未剥过头）", r["q"] is True)
        # ⑳ 显式 include_comments ⇒ 注释也算（"必须写文档"类要求）
        rf.write_text(真现场, encoding="utf-8")
        门2 = dict(门, include_comments=True)
        r, _ = evaluate([门2], rf, d, str(d))
        chk("include_comments=True ⇒ 注释也算", r["q"] is True)

        # ㉑ 注释符剥离正确性（各语言）
        chk("Rust 行注释", "推入" not in _去注释("pub fn push(){} // 推入", ".rs"))
        chk("Rust 块注释（可嵌套）",
            "推入" not in _去注释("/* 外 /* 内 推入 */ 还注释 */ fn f(){}", ".rs"))
        chk("Rust 真代码保留", "推入" in _去注释("fn 推入(){}", ".rs"))
        chk("Lean 行注释", "sorry" not in _去注释("-- sorry\nx", ".lean"))
        chk("Lean 嵌套块注释",
            "sorry" not in _去注释("/- 外 /- 内 sorry -/ 还 -/ x", ".lean"))
        chk("Lean 真 sorry 保留", "sorry" in _去注释("by sorry", ".lean"))
        chk("Python # 注释", "x" not in _去注释("# x\ny=1", ".py"))
        chk("Python 真代码保留", "y=1" in _去注释("# x\ny=1", ".py"))
        chk("HTML 注释", "x" not in _去注释("<!-- x --><p>", ".html"))
        chk("未知后缀·保守：只剥 // 与块",
            "# x" in _去注释("# x\n// y\nz", ".weird"))
        chk("未知后缀·仍剥 //", "y" not in _去注释("# x\n// y\nz", ".weird"))
        chk("★ 剥注释保留换行（行号不丢）",
            _去注释("a\n// c\nd\n", ".rs").count("\n") == 3)
        chk("★ 剥注释不删代码行数",
            len(_去注释("a\n// c\nd\n", ".rs").splitlines()) == 3)
        chk("空文本安全", _去注释("", ".rs") == "")

        # ㉘ ★★ 注释类标记必须**按原文判**（实测恒红根因）
        #   ★ 用自己的临时目录：上面那个 `d` 已在本块之前的 finally 里删掉了。
        d2 = Path(tempfile.mkdtemp(prefix="gs_mk_"))
        cf = d2 / "marker.rs"
        cf.write_text("// 【已闭合】本测试用整数比值。★ 证据：TS283b_x 通过\n"
                      "fn TS283b_x() {}\n", encoding="utf-8")
        r, _ = evaluate([{"name": "m", "type": "whole_reading", "in": "{file}",
                          "mode": "contains", "must_contain": ["【已闭合】"]}],
                        cf, d2, str(d2))
        chk("★ 注释里的登记标记必须判绿（原为恒红）", r["m"] is True)

        # ㉙ ★ 反例：**代码类**键不得被注释满足（2026-10-13 那个缺陷不得回潮）
        cf.write_text("//! 待实现：推入 / 取出 / 长度\n"
                      "pub fn push(){} pub fn pop(){} pub fn len(){}\n",
                      encoding="utf-8")
        r, _ = evaluate([{"name": "m", "type": "whole_reading", "in": "{file}",
                          "mode": "contains", "must_contain": ["推入"]}],
                        cf, d2, str(d2))
        chk("★ 反例：代码键不得被过时注释满足", r["m"] is False)

        # ㉚ ★ 反例：`pub fn 推入` 含空格但**不是**注释符 ⇒ 仍只读代码
        cf.write_text("// pub fn 推入\npub fn push(){}\n", encoding="utf-8")
        r, _ = evaluate([{"name": "m", "type": "whole_reading", "in": "{file}",
                          "mode": "contains", "must_contain": ["pub fn 推入"]}],
                        cf, d2, str(d2))
        chk("★ 反例：带空格的代码键不得被注释满足", r["m"] is False)

        # ㉛ 混合：一个注释键 + 一个代码键 ⇒ 两者都真才绿
        cf.write_text("// 【已闭合】x\nfn TS283b_x() {}\n", encoding="utf-8")
        r, _ = evaluate([{"name": "m", "type": "whole_reading", "in": "{file}",
                          "mode": "contains",
                          "must_contain": ["【已闭合】", "TS283b_x"]}],
                        cf, d2, str(d2))
        chk("★ 混合键：两者都在 ⇒ 绿", r["m"] is True)
        r, _ = evaluate([{"name": "m", "type": "whole_reading", "in": "{file}",
                          "mode": "contains",
                          "must_contain": ["【已闭合】", "TS283b_不存在"]}],
                        cf, d2, str(d2))
        chk("★ 混合键：代码键缺 ⇒ 仍红", r["m"] is False)

        # ㉜ 审计必须**如实记账**「这条是靠注释满足的」（不静默）
        r, audit = evaluate([{"name": "m", "type": "whole_reading", "in": "{file}",
                              "mode": "contains", "must_contain": ["【已闭合】"]}],
                            cf, d2, str(d2))
        chk("★ 注释满足必须记账（不静默）",
            any(a.get("satisfied_in_comments") for a in audit))

        # ㉝ `_只能在注释里` 自身
        chk("【已闭合】只能是注释", _只能在注释里("【已闭合】") is True)
        chk("推入 可能是代码", _只能在注释里("推入") is False)
        chk("pub fn x 可能是代码", _只能在注释里("pub fn x") is False)
        chk("空串安全", _只能在注释里("") is False)
        shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ════════════════════════════════════════════════════════════
    # 【TS-386】红门证据必须是**可读的错误文本**（实测根因）
    # ════════════════════════════════════════════════════════════
    # 2026-10-14 真跑：6000 字符红门证据里 **468 处 ANSI 转义序列**，
    # 且 **396 条编译警告**把 9 条 `error[E...]` 正文**整个挤出窗口** ——
    # 回喂给模型的"错误原因"一条错误正文都没有。判据读错了东西：
    # 读的是"带终端格式的输出尾部"，而不是"错误文本"。
    try:
        # ㉒ 剥转义码
        chk("剥 ANSI：颜色码消失",
            "\x1b[" not in _strip_ansi("\x1b[1m\x1b[91merror\x1b[0m: boom"))
        chk("剥 ANSI：正文保留", "error" in _strip_ansi("\x1b[1merror\x1b[0m: boom"))
        chk("剥 ANSI：无码文本不变", _strip_ansi("plain text") == "plain text")
        chk("剥 ANSI：空安全", _strip_ansi("") == "")

        # ㉓ ★ 核心：396 条警告在前、9 条 error 在中间 ⇒ 必须抽到 error
        警告 = "\n".join(
            f"warning: unused variable: `v{i}`\n"
            f"  --> src\\模块甲\\mod.rs:{5000 + i}:17\n"
            f"   |\n{5000 + i} |     let v{i} = 1;\n   |         ^^\n" for i in range(400))
        真错 = ("error[E0308]: mismatched types\n"
                " --> src\\模块甲\\mod.rs:8463:9\n"
                "  |\n8463 |     let x: u8 = 真;\n"
                "  |            ^^ expected `u8`, found `bool`\n")
        尾部 = "\n".join(f"note: 噪声 {i}" for i in range(400))
        ev = _evidence("", 警告 + "\n" + 真错 + "\n" + 尾部)
        chk("★ 警告淹没时仍抽到 error[E0308]", "E0308" in ev)
        chk("★ 抽到的 error 带 `--> 文件:行`", "8463" in ev)
        chk("★ 抽到的 error 带原因正文", "mismatched types" in ev)
        chk("★ 证据里不得残留转义码", "\x1b[" not in ev)
        chk("★ 证据有长度上限", len(ev) <= 6000)

        # ㉔ 只有警告、没有 error（例如只是退出码非零）⇒ 不得返回空
        ev2 = _evidence("", 警告)
        chk("无 error 行时不得返回空证据", len(ev2) > 0)

        # ㉕ 测试失败（stdout）也要抽到 —— 不能只看 stderr
        ev3 = _evidence("test foo::bar ... FAILED\n"
                        "thread 'foo::bar' panicked at src/x.rs:9:5:\n"
                        "assertion `left == right` failed\n"
                        "  left: 1\n right: 2\n", "")
        chk("★ stdout 里的测试失败也要抽到", "FAILED" in ev3 or "panicked" in ev3)

        # ㉖ 软失败标记必须被识别为错误块（否则超时/挂起证据会丢）
        chk("超时标记进错误块",
            "SUBPROCESS_TIMEOUT" in _evidence("", "x\n[SUBPROCESS_TIMEOUT >600s]\n"))
        chk("挂起标记进错误块",
            "HANG_STALL" in _evidence("", "x\n[HANG_STALL] 无输出 900s\n"))

        # ㉗ 短输出（无错误行、比上限短）⇒ 原样返回，不丢信息
        chk("短输出原样保留", _evidence("hello", "world") .count("hello") == 1)
    except Exception as _e386:      # noqa: BLE001
        print(f"  ✗ 【TS-386】证据门自身异常：{type(_e386).__name__}: {_e386}")

    # ════════════════════════════════════════════════════════════
    # 【TS-442 / TS-447 / TS-424】判据读数必须**不塌缩**
    # ════════════════════════════════════════════════════════════
    # 本段是这一步的**承重断言**：它要机器证明「旧 `dict[str,bool]` 读数
    # 把相异因由压成一档」= `Defect384`，且新因由读数**分得开**。
    # 全部用**真跑**（真起子进程、真读真输出），不 mock 判据。
    try:
        d3 = Path(tempfile.mkdtemp(prefix="gs_cause_"))
        产物 = d3 / "产物.txt"
        产物.write_text("hello\n", encoding="utf-8")

        def 假命令(输出: str, rc: int = 0, 走stderr: bool = True) -> list:
            ch = "sys.stderr" if 走stderr else "sys.stdout"
            return [sys.executable, "-c",
                    f"import sys; {ch}.write({输出!r}); sys.exit({rc})"]

        def 跑(门: dict) -> tuple[str, bool]:
            c, r, _a = evaluate_lossless([门], 产物, d3, str(d3))
            n = 门["name"]
            return c[n], r[n]

        门集: list[dict] = [
            {"name": "绿", "type": "compile", "cwd": str(d3),
             "cmd": 假命令("all good", 0)},
            {"name": "错E0308", "type": "compile", "cwd": str(d3),
             "cmd": 假命令("error[E0308]: mismatched types\n"
                           " --> src/模块甲/趋向.rs:8030:36\n", 1)},
            {"name": "错E0432", "type": "compile", "cwd": str(d3),
             "cmd": 假命令("error[E0432]: unresolved import\n"
                           " --> src/lib.rs:12:5\n", 1)},
            {"name": "超时", "type": "compile", "cwd": str(d3), "timeout": 1,
             "cmd": [sys.executable, "-c", "import time;time.sleep(5)"]},
            {"name": "挂起", "type": "compile", "cwd": str(d3),
             "timeout": 30, "stall": 1,
             "cmd": [sys.executable, "-c", "import time;time.sleep(5)"]},
            {"name": "退出码", "type": "compile", "cwd": str(d3),
             "cmd": 假命令("no error line here", 3)},
            {"name": "缺A", "type": "grep_required", "in": str(产物),
             "patterns": ["MARKER_A"]},
            {"name": "缺B", "type": "grep_required", "in": str(产物),
             "patterns": ["MARKER_B"]},
            {"name": "禁词", "type": "grep_forbidden", "in": str(产物),
             "patterns": ["hello"]},
            {"name": "结构键", "type": "whole_reading", "in": str(产物),
             "must_contain": ["KEY_X"]},
            {"name": "公理越界", "type": "axiom_footprint", "src": str(产物),
             "append": "", "cwd": str(d3),
             "cmd": 假命令("'t' depends on axioms: [propext, cheat]\n", 0)},
            {"name": "足迹未确认", "type": "axiom_footprint", "src": str(产物),
             "append": "", "cwd": str(d3),
             "cmd": 假命令("garbage, no axiom line\n", 0)},
            # ★★★ 命令不存在（2026-10-14 真跑抓到的缺陷）：
            #   原实现让 `FileNotFoundError` **逃逸出 evaluate** ⇒ 一个坏门规格
            #   把**所有门**一起带崩。此门必须**判红**且给出「命令无法启动」因由，
            #   而**不是**抛异常。
            {"name": "命令不存在", "type": "compile", "cwd": str(d3),
             "cmd": [str(d3 / "根本没有这个编译器.exe"), "--version"]},
        ]

        码表: dict[str, str] = {}
        布尔表: dict[str, bool] = {}
        for 门 in 门集:
            c, r = 跑(门)
            码表[门["name"]] = c
            布尔表[门["name"]] = r

        红码 = [c for n, c in 码表.items() if n != "绿"]

        # ① 绿码唯一，且**只有**绿码投影为 True（投影与读数一致）
        chk("★ 读数：绿码 == '绿'", 码表["绿"] == "绿")
        chk("★ 读数：绿码投影为 True", 布尔表["绿"] is True)
        chk("★ 读数：通过(码) 能还原原 ok（唯一投影点）",
            all(通过(c) == 布尔表[n] for n, c in 码表.items()))

        # ② ★★★ 核心：12 个相异因由 ⇒ 12 个相异码（**不塌缩**）
        chk("★★★ 读数不塌缩：相异因由必得相异码",
            len(set(红码)) == len(红码))
        chk("★★★ 因由数 = 12（相异因由齐备）", len(红码) == 12)

        # ③ 旧 Bool 读数**必然塌缩**：12 个相异因由全压成 False
        #    ⇒ 这就是 `Defect384 T := ∃ x y, x ≠ y ∧ T x = T y` 的实例
        布尔像 = {布尔表[n] for n in 码表 if n != "绿"}
        chk("★★ 旧 Bool 读数把 12 个相异因由压成 1 档（Defect384 实例）",
            布尔像 == {False})
        chk("★★ 旧 Bool 像大小 2 < 因由数 13 ⇒ 鸽笼（投影必塌）",
            len({True, False}) < len(码表))

        # ④ 读数空间**无界**（TS-424）：码携带具体证据，不是固定小枚举
        chk("★ 码携带错误码与位置（E0308@…:8030:36）",
            "E0308" in 码表["错E0308"] and "8030:36" in 码表["错E0308"])
        chk("★ 两个编译错因**码不同**（不是同一个'编译错'）",
            码表["错E0308"] != 码表["错E0432"])
        chk("★ 两个缺失标记因**码不同**", 码表["缺A"] != 码表["缺B"])
        chk("★ 超时与挂起**码不同**（软失败可分辨）",
            码表["超时"] != 码表["挂起"])
        chk("★ 超时码带上限", "上限1s" in 码表["超时"])
        chk("★ 挂起码带无输出时长", "无输出1s" in 码表["挂起"])
        chk("★ 退出码码带期望与实得",
            "期望0" in 码表["退出码"] and "实得3" in 码表["退出码"])
        chk("★ 公理越界码带越界公理名", "cheat" in 码表["公理越界"])
        chk("★ 公理越界 vs 足迹未确认**码不同**",
            码表["公理越界"] != 码表["足迹未确认"])

        # ④b ★★★ 命令不存在必须**判红并给出因由**，而**不是**抛异常
        #     （2026-10-14 真跑抓到的缺陷：FileNotFoundError 逃逸出 evaluate，
        #      一个坏门规格把**所有门**一起带崩。）
        chk("★★★ 命令不存在 ⇒ 判红（不抛异常）", 布尔表["命令不存在"] is False)
        chk("★★★ 命令不存在 ⇒ 因由类 = 命令无法启动",
            码表["命令不存在"].startswith("命令无法启动|"))
        chk("★ 因由只带可执行文件名（不带绝对路径噪声）",
            ":" not in 码表["命令不存在"] and "\\" not in 码表["命令不存在"])
        chk("★ 命令无法启动 ≠ 超时/挂起/退出码不符（四档互异）",
            len({码表["命令不存在"], 码表["超时"], 码表["挂起"],
                 码表["退出码"]}) == 4)

        # ⑤ 确定性（TS-447 方向性的可测形式）：同一产物恒得同一因由
        chk("★ 确定性：同一门跑两次得同一码",
            跑(门集[1])[0] == 跑(门集[1])[0])
        chk("★ 确定性：缺失标记码可复现", 跑(门集[6])[0] == 码表["缺A"])

        # ⑥ 噪声不变性：ANSI 包裹同一错误 ⇒ **同码**（读数不读终端格式）
        门ANSI = dict(门集[1])
        门ANSI["name"] = "错E0308带色"
        门ANSI["cmd"] = 假命令("\x1b[1m\x1b[91merror[E0308]\x1b[0m: mismatched types\n"
                              " \x1b[1m-->\x1b[0m src/模块甲/趋向.rs:8030:36\n", 1)
        chk("★ 噪声不变：ANSI 不改变因由码",
            跑(门ANSI)[0] == 码表["错E0308"])

        # ⑦ 警告淹没时因由仍抽到真错（不是读到噪声）
        门淹 = dict(门集[1])
        门淹["name"] = "错E0308淹没"
        门淹["cmd"] = 假命令(
            "\n".join(f"warning: unused variable: `v{i}`" for i in range(400))
            + "\nerror[E0308]: mismatched types\n"
              " --> src/模块甲/趋向.rs:8030:36\n"
            + "\n".join(f"note: noise {i}" for i in range(400)), 1)
        chk("★ 警告淹没：因由仍是 E0308@…:8030:36",
            跑(门淹)[0] == 码表["错E0308"])

        # ⑦b ★★ 确定性：**证据里同一错误块被重复贴**（`_evidence` 补尾部时会这样）
        #     ⇒ 因由码**不得**跟着重复。本函数第一版就栽在这里，被自检抓住。
        门重 = dict(门集[1])
        门重["name"] = "错E0308重复"
        门重["cmd"] = 假命令("error[E0308]: mismatched types\n"
                            " --> src/模块甲/趋向.rs:8030:36\n"
                            "\n[尾部]\n"
                            "error[E0308]: mismatched types\n"
                            " --> src/模块甲/趋向.rs:8030:36\n", 1)
        _重码 = 跑(门重)[0]
        chk("★★ 确定性：证据重复贴同一错误块 ⇒ 因由码不变",
            _重码 == 码表["错E0308"])
        chk("★★ 去重后码里该错误只出现一次",
            _重码.count("E0308@趋向.rs:8030:36") == 1)

        # ⑦c 但**相异**错误必须全保住（去重不得把真区分力也去掉）
        门双 = dict(门集[1])
        门双["name"] = "错两个"
        门双["cmd"] = 假命令("error[E0308]: mismatched types\n"
                            " --> src/模块甲/趋向.rs:8030:36\n"
                            "error[E0432]: unresolved import\n"
                            " --> src/lib.rs:12:5\n", 1)
        _双码 = 跑(门双)[0]
        chk("★ 两个相异错误都进码（去重没吃掉真区分力）",
            "E0308@趋向.rs:8030:36" in _双码 and "E0432@lib.rs:12:5" in _双码)
        chk("★ 双错码 ≠ 单错码", _双码 != 码表["错E0308"])

        # ⑧ 判据未被改动：因由读数**不放宽也不收紧**
        chk("★ 绿门仍绿（读数没把好产物判红）", 布尔表["绿"] is True)
        chk("★ 红门仍红（读数没把坏产物判绿）",
            all(布尔表[n] is False for n in 码表 if n != "绿"))
        shutil.rmtree(d3, ignore_errors=True)
    except Exception as _e442:      # noqa: BLE001
        print(f"  ✗ 【TS-442/447/424】因由读数自检自身异常："
              f"{type(_e442).__name__}: {_e442}")

    # ★★★★★ R11 判据力：`grep_forbidden` 的 `in` 支持**目录**。
    #   为什么需要：档案里的 `no_scaffold` 门声称"禁止 todo!/unimplemented!
    #   脚手架"，`in` 却只写一个文件 `src/lib.rs`（本 crate 里那文件基本只有
    #   `mod` 声明）⇒ **门的范围远小于它声称的意图**，模块文件里的脚手架查不到。
    #   ⇒ 必须有一条门直接判"目录里的嵌套命中**真能被查到**"。
    try:
        import tempfile as _tf11
        _d11 = Path(_tf11.mkdtemp(prefix="gs_dir_"))
        (_d11 / "sub").mkdir()
        (_d11 / "干净.rs").write_text("fn a() {}\n", encoding="utf-8")
        (_d11 / "sub" / "脏.rs").write_text("fn b() { todo!() }\n", encoding="utf-8")

        def _跑11(门):
            r, a = evaluate([门], _d11, str(_d11))
            return list(r.values())[0], a

        _脏门 = {"name": "脚手架", "type": "grep_forbidden",
                 "in": str(_d11), "pattern": r"todo!\(|unimplemented!\("}
        _ok脏, _au脏 = _跑11(_脏门)
        chk("★ R11 目录扫描：嵌套文件里的命中**必须判红**（旧实现只扫一个文件 ⇒ 假绿）",
            _ok脏 is False)
        _因 = _au脏[0] if _au脏 else {}
        chk("★ R11 因由记明**哪个文件**命中（不压平）",
            _因.get("hit_files") == ["sub/脏.rs"],
            f"hit_files={_因.get('hit_files')}")
        chk("★ R11 因由记明**哪条模式**命中",
            _因.get("hit_patterns") == [r"todo!\(|unimplemented!\("])
        # 干净目录必须绿（别把好树判红）
        (_d11 / "sub" / "脏.rs").write_text("fn b() {}\n", encoding="utf-8")
        _ok净, _ = _跑11(_脏门)
        chk("★ R11 干净目录判绿", _ok净 is True)
        # 单文件模式**向后兼容**（老档案不能坏）
        _单 = {"name": "单文件", "type": "grep_forbidden",
               "in": str(_d11 / "干净.rs"), "pattern": "todo"}
        _ok单, _ = _跑11(_单)
        chk("★ R11 单文件模式向后兼容（仍绿）", _ok单 is True)
        # 目录不存在 ⇒ 正常因由，不抛异常
        _缺 = {"name": "缺目录", "type": "grep_forbidden",
               "in": str(_d11 / "根本没有"), "pattern": "x"}
        _ok缺, _au缺 = _跑11(_缺)
        chk("★ R11 目录不存在 ⇒ 判红且有因由（不抛异常）",
            _ok缺 is False and bool((_au缺[0] or {}).get("err")))
        # 超大文件被跳过时**必须如实报**（静默跳过＝假绿）
        _大 = _d11 / "大.rs"
        _大.write_text("x" * (_GREP_MAX_BYTES + 10), encoding="utf-8")
        _ok大, _au大 = _跑11({"name": "大", "type": "grep_forbidden",
                              "in": str(_d11), "pattern": "zzz不存在zzz"})
        chk("★ R11 跳过的文件**如实报**（不静默 ⇒ 不假绿）",
            bool((_au大[0] or {}).get("skipped")),
            f"skipped={( _au大[0] or {}).get('skipped')}")
        shutil.rmtree(_d11, ignore_errors=True)
    except Exception as _e11:      # noqa: BLE001
        # ★ 必须**入账**：只打印不入账 ⇒ 总数照样"全绿"（**假绿**）。
        #   这正是本模块一直在治的那一类：读数与判定脱钩。
        chk("★ R11 目录扫描自检未异常", False,
            f"{type(_e11).__name__}: {_e11}")

    # ★★★★★ R12 判据力：grep 门可**剥注释/字符串再匹配**。
    #   为什么需要：源码文本门读**原始文本**时，文档注释里提到 `todo!()`
    #   （如"禁止留 todo!/unimplemented! 占位"）会让 `no_scaffold` 变红，
    #   而代码里根本没有脚手架 ⇒ **假红门**：合格产出被误拒，**越写文档越红**。
    try:
        import tempfile as _tf12
        _d12 = Path(_tf12.mkdtemp(prefix="gs_strip_"))
        # ① 注释里的禁止词：**不剥 ⇒ 假红**（这就是缺陷本体）
        #   ★ 探针自身的错（如实登记）：第一版把注释写成
        #     `// 禁止留 todo!/unimplemented! 占位` —— **没有括号**，
        #     而模式是 `todo!\(` ⇒ 本就不该命中，门是绿的。
        #     那是我**探针写错**，不是代码有缺陷。这里必须写成**真形态** `todo!()`。
        _注 = _d12 / "带文档.rs"
        _注.write_text("// 禁止留 todo!() / unimplemented!() 占位\n"
                       "fn 真代码() {}\n", encoding="utf-8")
        _门 = {"name": "脚手架", "type": "grep_forbidden",
               "in": str(_注), "pattern": r"todo!\(|unimplemented!\("}
        _r不剥, _ = evaluate([_门], _d12, str(_d12))
        chk("★ R12 复现缺陷：注释里的禁止词**不剥就假红**",
            list(_r不剥.values())[0] is False)
        _r剥, _a剥 = evaluate([dict(_门, strip="comments")], _d12, str(_d12))
        chk("★ R12 剥注释后 ⇒ **判绿**（假红被关掉）",
            list(_r剥.values())[0] is True,
            f"因由={(_a剥[0] if _a剥 else {})}")
        # ② 真代码里的禁止词**必须仍判红**（别为了关假红把真红也关掉）
        _真 = _d12 / "真脚手架.rs"
        _真.write_text("fn f() { todo!() }  // 行尾注释\n", encoding="utf-8")
        _门真 = {"name": "脚手架", "type": "grep_forbidden",
                 "in": str(_真), "pattern": r"todo!\(|unimplemented!\(",
                 "strip": "comments"}
        _r真, _a真 = evaluate([_门真], _d12, str(_d12))
        chk("★★★ R12 真代码里的禁止词**仍判红**（真红没被关掉）",
            list(_r真.values())[0] is False,
            f"因由={(_a真[0] if _a真 else {})}")
        # ③ 字符串字面量里的禁止词也不该算
        _串 = _d12 / "串.rs"
        _串.write_text('let s = "todo!()";\nfn g() {}\n', encoding="utf-8")
        _门串 = {"name": "脚手架", "type": "grep_forbidden",
                 "in": str(_串), "pattern": r"todo!\(|unimplemented!\(",
                 "strip": "comments"}
        _r串, _ = evaluate([_门串], _d12, str(_d12))
        chk("★ R12 字符串字面量里的禁止词不算命中", list(_r串.values())[0] is True)
        # ④ 不传 strip ⇒ 行为完全不变（向后兼容）
        _门默 = {"name": "脚手架", "type": "grep_forbidden",
                 "in": str(_串), "pattern": r"todo!\(|unimplemented!\("}
        _r默, _ = evaluate([_门默], _d12, str(_d12))
        chk("★ R12 不传 strip ⇒ 行为不变（仍按原始文本判，向后兼容）",
            list(_r默.values())[0] is False)
        # ⑤ ★ 坐标纪律：清洗**不得改变坐标**（等长 + 换行位置一致）
        _例 = "// 注释\nlet a = 1;\n/* 多行\n注释 */\nlet b = 2;\n"
        _净 = strip_for_matching(_例)
        chk("★ R12 剥注释**保长**（等长 ⇒ 行列不错位）", len(_净) == len(_例),
            f"{len(_例)} → {len(_净)}")
        chk("★ R12 剥注释**换行位置一致**",
            [k for k, c in enumerate(_例) if c == "\n"]
            == [k for k, c in enumerate(_净) if c == "\n"])
        chk("★ R12 剥注释后代码本体仍在",
            "let a = 1;" in _净 and "let b = 2;" in _净)
        shutil.rmtree(_d12, ignore_errors=True)
    except Exception as _e12:      # noqa: BLE001
        chk("★ R12 剥注释自检未异常", False,
            f"{type(_e12).__name__}: {_e12}")

    # ★ R117：`in`/`src` 的解析口径（见 `_齿R117`）
    _f117 = _齿R117()
    for _x in _f117:
        print(f"  ✗ {_x}")
    total += 1
    if not _f117:
        ok += 1

    print(f"  gate_spec 自检：{ok}/{total} 通过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("判据门规格模块（--selftest 跑自检）")

