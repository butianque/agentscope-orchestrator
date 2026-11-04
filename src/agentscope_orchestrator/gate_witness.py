"""判据门区分力见证（discrimination witness）—— 完备闭环缺失的最后一块。

━━━ 为什么需要它（数学模型对齐）━━━

活库 TS375 `O1Spec375` 把「O(1) 内容件」的口径写成四样，其中第四样是**非空见证**：

    discriminates : ∃ x y : α, content x ≠ content y

并机证其必要性（`no_spec_of_const_content`）：
    「内容为常函数的件**不存在**满足统一口径的实例」
    —— 「成本是 1」本身不构成 O(1) 内容件；必须有**区分力见证**。

TS375 的诚实边界同时指出这条判据的**来源**：
    「库内 TS-261:47 已用 `wholeRead261_not_constant` 作为『真写』与『换值』的判据」

同一思想在 TS392–TS410 被推广成「假证明形态分类学」，其中：
  · 形态 5（恒真式）：结论对**任意**输入成立 ⇒ 零内容
  · 形态 7（取象件常值）：输入**整体不被消费**
  · 形态 9（字段已算出却无人读）：写入但不产生下游约束
  · 形态 12（前提重述为结论）：结论 ⇔ 前提 ⇒ 零新信息

**它们的共同判据只有一条**：*该对象能否被证否？*
  不能被证否的检查 = 恒真检查 = 不是判据。

━━━ 编排器的缺口（本模块补的）━━━

编排器的 `sufficiency_bound` 只做**静态**检查：看门的 `type`、`cmd` 形状、
有没有 `{file}` 占位符。它**从不验证门真的能变红**。

后果（与库内形态 5/7 同构）：一个 `grep_required` 门写着
`pattern: "def "` 几乎在任何 Python 文件里都命中；一个 `whole_reading`
写着 `must_contain: ["import"]` 在多数文件里都成立。这些门**永远绿**，
于是「门全绿」与「目标达成」脱钩 —— 正是 TS375 说的
「光看成本函数分不出真 O(1) 与空件」的编排层同构。

活库自己的做法（`.tools/假边界总账.md`）：
    「真证的定理 24 条（均经**反证控制探针**确认非定义性相等；
      探针对同陈述施 `:= rfl` **必须编译失败**）」
  ⇒ **库对每条真证都跑了一个"必须失败"的控制探针**。
    编排器对每个判据门**从未跑过控制探针**。本模块补上。

━━━ 做法（按门类型做对抗性变异）━━━

对每个门，构造一份**应当使其变红**的变异产物，真跑该门：
  · compile            → 注入语法错误 → 门必须红
  · grep_required      → 删掉全部必需 pattern  → 门必须红
  · grep_forbidden     → 注入全部禁词          → 门必须红
  · whole_reading(contains)  → 删掉全部 must_contain → 门必须红
  · whole_reading(forbidden) → 注入全部 must_not     → 门必须红
  · scan_output        → 注入其 pattern          → 门必须红

**变异后仍然绿 ⇒ 该门无区分力 ⇒ 拒绝**（不是警告，是拒：恒真门不构成判据）。

诚实边界（本模块不声称）：
  · 变异是**启发式**的，不是完备的：门有区分力（能对某个输入变红）是**必要**条件，
    不是充分条件。本模块只把「恒真门」这一类**必然假完成**挡在门外。
  · 变异在**副本**上做，绝不触碰原产物；副本用完即删。
  · 无法构造变异的门（如需要外部网络的命令）标 `witness=unknown` 并**如实报告**，
    不静默放行、也不假装已验。
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentscope_orchestrator.gate_spec import evaluate as gate_evaluate


# ── 变异构造：每个返回 (变异后文本, 变异说明) 或 None（无法构造）──────

def _mutate_compile(text: str, g: dict) -> tuple[str, str] | None:
    """compile 门：注入语法错误。"""
    return (text + "\n\n@@@ SYNTAX_ERROR_WITNESS @@@\n",
            "追加非法语法行")


def _mutate_grep_required(text: str, g: dict) -> tuple[str, str] | None:
    """grep_required 门：**清空文件**（最强对抗探针）。

    ★ 为什么用空文件而不是"删掉 pattern"：
      `grep_required` 在 gate_spec 里是**子串**判定（`p not in text`），
      不是正则判定。空文件是最强的对抗输入——必需内容应当随之消失。
      若门在**空文件**上仍绿，则该 pattern 是空串一类恒真项 ⇒ 无区分力。
      （早先版本先做 `re.sub(p, ...)` 删除，对非正则 pattern 是错的。）"""
    pats = _pats(g)
    if not pats:
        return None
    return ("", "清空文件（必需 pattern 应随之消失）")


def _mutate_grep_forbidden(text: str, g: dict) -> tuple[str, str] | None:
    """grep_forbidden 门：注入一个**真能被该正则命中**的串。"""
    pats = _pats(g)
    if not pats:
        return None
    inject = []
    for p in pats:
        lit = _witness_literal(p, text)
        if lit is None:
            return None          # 造不出可靠见证 → 如实标 unknown，不假装
        inject.append(lit)
    return (text + "\n" + "\n".join(inject) + "\n", f"注入禁词 {len(inject)} 个")


def _mutate_whole_reading(text: str, g: dict) -> tuple[str, str] | None:
    """whole_reading 门：按 mode 做对抗变异。"""
    mode = g.get("mode", "contains")
    if mode == "contains":
        markers = [str(m) for m in (g.get("must_contain") or [])]
        if not markers:
            markers = _pats(g)
        if not markers:
            return None
        out = text
        for m in markers:
            out = out.replace(m, "")
        if out == text:
            return ("", "清空文件（必需标记未在原文命中）")
        return (out, f"删除 must_contain {len(markers)} 项")
    if mode == "forbidden":
        emp = [str(x) for x in (g.get("must_not") or [])]
        if not emp:
            return None
        return (text + "\n" + "\n".join(emp) + "\n", f"注入 must_not {len(emp)} 项")
    if mode == "equal":
        return (text + "\n# witness drift\n", "追加一行破坏整读签名")
    return None


def _mutate_scan_output(text: str, g: dict) -> tuple[str, str] | None:
    """scan_output 门：注入其 pattern（须真能被命中）。"""
    pats = _pats(g)
    if not pats:
        return None
    inject = []
    for p in pats:
        lit = _witness_literal(p, text)
        if lit is None:
            return None
        inject.append(lit)
    return (text + "\n" + "\n".join(inject) + "\n",
            f"注入 scan pattern {len(inject)} 个")


_MUTATORS = {
    "compile": _mutate_compile,
    "grep_required": _mutate_grep_required,
    "grep_forbidden": _mutate_grep_forbidden,
    "whole_reading": _mutate_whole_reading,
    "scan_output": _mutate_scan_output,
}


def _pats(g: dict) -> list[str]:
    p = g.get("patterns")
    if isinstance(p, list) and p:
        return [str(x) for x in p if str(x).strip()]
    if g.get("pattern"):
        return [str(g["pattern"])]
    return []


def _witness_literal(pattern: str, text: str) -> str | None:
    """造一个**能被该正则真正命中**的字面串（用于"注入禁词"变异）。

    ★ 教训（两轮自检红换来的）：
      · 早先版本用「剥掉正则元字符」退化 pattern，`todo!\\(` 被剥成 `todo!`，
        注入后正则**根本匹配不上** ⇒ 真门被误判成"恒真门"（假红）。
      · 剥元字符时若连 `\\(` 的括号一起剥掉，同样造不出可命中串。
    正确做法按可信度依次尝试，**每步都用 `re.search` 当场验证**，
    验不过就不返回——宁可标 unknown，也不给假见证。"""
    # ① 最可信：原文里已有的真实匹配串
    try:
        m = re.search(pattern, text)
    except re.error:
        return None
    if m and m.group():
        return m.group()
    # ② 去掉反斜杠转义（`\\(` → `(`）——保留被转义的元字符本身
    cands = []
    cands.append(re.sub(r"\\(.)", r"\1", pattern))
    # ③ 退一步：剥掉全部元字符
    cands.append(re.sub(r"[\[\](){}*+?^$|.\\]", "", pattern))
    for s in cands:
        if not s:
            continue
        try:
            if re.search(pattern, s):
                return s
        except re.error:
            continue
    return None


# 兼容旧名（模块内其它处引用）
def _literal_of(pattern: str) -> str:      # pragma: no cover - 保留旧签名
    return pattern


# ── 主入口 ────────────────────────────────────────────────────────

def _gate_read_target(g: dict, product: Path, work_dir: Path) -> Path | None:
    """该门**实际读取的那个文件**。None 表示工程级（不读单个文件）。

    ★ 这是本模块最容易错、且错了会**假红**的一处（实测换来的）：
      真工程任务里模型写了 `lib_declares_probe2_mod` 门，它读的是
      `src/lib.rs`（检查 lib.rs 有没有声明新模块），**不是**产物文件。
      早先版本一律去变异**产物**，门根本看不见那个变异 ⇒ 门仍绿
      ⇒ 被误判为"恒真门" ⇒ 编排器反复换门集、烧掉 31 万字符仍不收敛。

      正确口径：**变异门读取的那个文件**，而不是产物。
        · whole_reading / grep_* / scan_output → 读 `in`/`src`（缺省=产物）
        · compile 且 cmd 含 `{file}`       → 读产物
        · compile 且 cmd 不含 `{file}`     → 工程级（不读单个文件）
    """
    for key in ("in", "src"):
        v = g.get(key)
        if isinstance(v, str) and v.strip():
            p = Path(v.replace("{file}", str(product)).replace("{work}", str(work_dir)))
            if not p.is_absolute():
                p = Path(work_dir) / p
            return p
    typ = str(g.get("type", ""))
    if typ == "compile":
        cmd = g.get("cmd")
        if isinstance(cmd, list) and any("{file}" in str(c) for c in cmd):
            return product
        return None                     # 工程级门
    return product                      # 其余类型缺省读产物


def _dir_witness_inject(target: Path, g: dict) -> tuple[Path, str] | None:
    """**目录读空间的 σ**：在目录里注入一个含见证词的新文件。

    ★★★★★ R13：为什么需要 —— `in` 可为**目录**（R11 起 grep 门可扫目录）。
      目录不是单一文本 ⇒ 旧路径 `target.is_file()` 为假 ⇒ 恒判 `unknown`，
      而 `unknown` **不置 `all_ok=False`** ⇒ 门**永不接受见证**却照样通过
      ⇒ 与恒真门一样是**假绿**。
    ★ 为什么不是"变异某个文件"：门读的空间是**整棵树**，
      所以 σ 必须作用在**树的内容**上 —— 加一份新内容是最小、可还原的作用。
    ★ 见证词由 `_witness_literal` 造，**已当场 `re.search` 验过能被该门的
      正则命中** ⇒ 注入后门**必然**看得见（这正是 σ 的「无不动点」条件）。
    """
    pats = _pats(g)
    if not pats:
        return None
    lits: list[str] = []
    for pat in pats:
        lit = _witness_literal(pat, "")
        if lit is None:
            return None
        lits.append(lit)
    词 = "\n".join(lits)
    try:
        f = target / "_witness_probe_注入.rs"
        f.write_text(词 + "\n", encoding="utf-8")
    except OSError:
        return None
    return f, 词


def witness_gates(gates, product: Path, work_dir: Path, cwd_default=".",
                  repo_root: Path | None = None,
                  _derive: bool = True) -> tuple[bool, list[dict]]:
    """对每个门做对抗性变异，验证它**真的能变红**。

    返回 (all_have_discrimination: bool, report: list[dict])。
    report 每项 = {"gate", "type", "witness", "mutation", "detail"}，witness 取：
      · "ok"        —— 变异后门变红 ⇒ 有区分力
      · "vacuous"   —— 变异后仍绿 ⇒ 门对该变异无区分力（疑恒真门）⇒ 换门集
      · "decoupled" —— 工程级门仍绿且产物**未被工程引用** ⇒ 产物没接进工程
                       （如新模块未在 lib.rs 声明）⇒ **补接线**，不是换门
      · "unknown"   —— 无法构造变异 / 门执行异常 ⇒ 如实报告，不假装已验

    做法：**变异门实际读取的那个文件**（见 `_gate_read_target`），
    在副本上变异并重定向门的读入目标；工程级门才原位变异并立即还原。

    `_derive=True`（默认）：判出 `vacuous` 的门**再走一步**——按 TS-384
    `defect_is_endogenous_drive` 构造性地派生一个更细的替换门，放进
    `entry["derived_gate"]`。这是"判出缺陷"到"修掉缺陷"的那一段闭环。
    """
    if gates is None:
        return True, []
    items = gates if isinstance(gates, list) else [gates]
    product = Path(product)
    if not product.is_file():
        return True, [{"gate": "?", "type": "?", "witness": "unknown",
                       "mutation": "", "detail": f"产物不存在，无法做见证：{product}"}]

    report: list[dict] = []
    all_ok = True
    tmpdir = Path(tempfile.mkdtemp(prefix="gate_witness_"))
    try:
        for g in items:
            typ = str(g.get("type", "?"))
            name = str(g.get("name") or typ)
            entry = {"gate": name, "type": typ, "witness": "unknown",
                     "mutation": "", "detail": ""}
            mut = _MUTATORS.get(typ)
            if mut is None:
                entry["detail"] = f"无变异策略（门类型 {typ}）"
                report.append(entry)
                continue

            target = _gate_read_target(g, product, work_dir)
            if target is None:
                # 工程级门：变异产物本身。若产物不被工程引用，则门对它无反应
                # —— 这正是 decoupled 的判据。
                target = product
                project_level = True
            else:
                project_level = False

            # ★★★★★ R13（数术层接线）：**σ 必须是平衡**（TS-468 §2）。
            #   `_gate_read_target` 可能返回**目录**（R11 起 grep 门可扫目录）。
            #   目录不是单一文本 ⇒ 旧路径 `target.is_file()` 为假 ⇒ 恒判 unknown，
            #   而 unknown **不置 all_ok=False** ⇒ 该门**永不接受见证**却照样通过
            #   ⇒ 与恒真门一样是**假绿**（实测：这是我自己 R11 改目录后引入的）。
            目录 = target.is_dir()
            _新文件: Path | None = None
            if 目录:
                _注入 = _dir_witness_inject(target, g)
                if _注入 is None:
                    entry["detail"] = ("目录读空间：无法构造可命中的注入见证"
                                       "（门缺 pattern）⇒ 如实报 unknown")
                    report.append(entry)
                    continue
                _新文件, _见证词 = _注入
                desc = f"注入见证词 {_见证词!r} 到目录新文件"
                entry["mutation"] = desc
                # ★ σ 的「无不动点」条件：注入的串**真落地且真被该门的正则命中**
                #   （`_witness_literal` 已当场 `re.search` 验过）。
                try:
                    _落地 = _新文件.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    _落地 = ""
                if not _见证词 or _见证词 not in _落地:
                    _新文件.unlink(missing_ok=True)
                    entry["detail"] = ("目录读空间：注入见证未落地 ⇒ σ 有不动点"
                                       " ⇒ 保真方程无判据力，如实报 unknown")
                    report.append(entry)
                    continue
                mutated_text = ""
            else:
                if not target.is_file():
                    entry["detail"] = f"门读取的目标不存在：{target}"
                    report.append(entry)
                    continue
                try:
                    target_text = target.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    entry["detail"] = f"门读取目标不可读：{exc}"
                    report.append(entry)
                    continue

                made = mut(target_text, g)
                if made is None:
                    entry["detail"] = "无法构造对抗变异（门缺 pattern/must_contain 等）"
                    report.append(entry)
                    continue
                mutated_text, desc = made
                entry["mutation"] = desc
                # ★★★★★ R13：**σ 有不动点 ⇒ 判据无判据力**（TS-468 §2）。
                #   若变异后门读的文本**逐字未变**，则 `门(σx) = 门(x)` 恒成立
                #   ⇒ 保真方程对**任何**门都不成立 ⇒ 真门被误判「恒真」= **假红**。
                #   ⇒ 必须如实报 unknown，**不能**报 vacuous。
                if mutated_text == target_text:
                    entry["detail"] = ("变异是**不动点**（门读的文本逐字未变）⇒ "
                                       "σ 不是平衡 ⇒ 保真方程无判据力，如实报 unknown"
                                       "（**不得**据此判 vacuous：那是假红）")
                    report.append(entry)
                    continue

            # 归一化门（与 _score_gates 同口径）：补 cwd、替换 {repo}，
            # 否则 `cwd="{repo}"` 会以字面目录名执行 → WinError 267（实测踩过）。
            g_run = dict(g)
            if not g_run.get("cwd"):
                g_run["cwd"] = str(repo_root if repo_root is not None else work_dir)
            elif isinstance(g_run["cwd"], str) and repo_root is not None:
                g_run["cwd"] = g_run["cwd"].replace("{repo}", str(repo_root))
            if isinstance(g_run.get("cmd"), list) and repo_root is not None:
                g_run["cmd"] = [str(c).replace("{repo}", str(repo_root))
                                for c in g_run["cmd"]]

            if project_level:
                # 原位变异 + 无条件还原（工程编译只看原位文件）
                run_path = target
                try:
                    backup = target.read_bytes()
                    try:
                        target.write_text(mutated_text, encoding="utf-8")
                        results, audit = gate_evaluate(
                            [g_run], run_path, work_dir, cwd_default)
                    finally:
                        target.write_bytes(backup)
                except Exception as exc:      # noqa: BLE001
                    entry["detail"] = f"门执行异常：{type(exc).__name__}: {exc}"
                    report.append(entry)
                    continue
            elif 目录:
                # ★ R13 目录读空间：注入文件已在位（门扫整棵树）⇒ 直接跑；
                #   跑完**无条件**删除（异常路径也要删，否则污染真实源码树）。
                run_path = target
                try:
                    results, audit = gate_evaluate(
                        [g_run], run_path, work_dir, cwd_default)
                except Exception as exc:      # noqa: BLE001
                    entry["detail"] = f"门执行异常：{type(exc).__name__}: {exc}"
                    report.append(entry)
                    continue
                finally:
                    if _新文件 is not None:
                        _新文件.unlink(missing_ok=True)
            else:
                # 副本变异 + 把门的读入目标重定向到副本（绝不触碰原文件）
                cand = tmpdir / f"{len(report)}_{target.name}"
                cand.write_text(mutated_text, encoding="utf-8")
                for key in ("in", "src"):
                    if isinstance(g_run.get(key), str):
                        g_run[key] = str(cand)
                run_path = cand
                try:
                    results, audit = gate_evaluate(
                        [g_run], run_path, work_dir, cwd_default)
                except Exception as exc:      # noqa: BLE001
                    entry["detail"] = f"门执行异常：{type(exc).__name__}: {exc}"
                    report.append(entry)
                    continue

            still_green = all(results.values()) if results else False
            # ★★★★★ R13：判据换成**保真方程**（TS-468 §2 的 `IsBalancedHom`）：
            #     门(σx) == flip2(门(x))
            #   门在原产物上是通过的（值 1，这是前提）；变异后仍绿 ⇒ 值 1 ⇒ 破。
            #   ⇒ **假绿与假红是同一个违反**（轨道退化为不动点），一条方程同时挡住。
            from agentscope_orchestrator import shushu_logic as _sl
            _保真, _保真因由 = _sl.保真方程(1, 1 if still_green else 0)
            entry["保真"] = _保真
            entry["保真因由"] = _保真因由
            if _保真:
                entry["witness"] = "ok"
                entry["detail"] = f"变异后门变红（{desc}）⇒ 有区分力"
            else:
                # 门仍绿：区分「恒真门」与「产物没接进工程」
                if project_level and not _referenced_by_project(product, work_dir):
                    entry["witness"] = "decoupled"
                    entry["detail"] = (
                        f"工程级门对产物变异无反应（{desc}），且产物**未被工程任何"
                        f"文件引用** ⇒ 门是真门但产物**没接进工程**"
                        f"（如新模块未在 lib.rs 声明）。补救=补接线，不是换门。")
                else:
                    entry["witness"] = "vacuous"
                    entry["detail"] = (
                        f"变异后门**仍绿**（{desc}，变异目标={target.name}）"
                        f"⇒ 该门对该变异无区分力，疑为恒真门")
                    # ★ 关键补全（TS-384 路径）：恒真门不再只是"被拒绝"，
                    #   而是**构造性地派生**一个更细的门替它。
                    #   对应 `defect_is_endogenous_drive`：
                    #     缺陷对 (x,y) → T' z = if z = x then 0 else 1
                    #   全程零 LLM：只用文本抽取 + 真跑门验证。
                    #   早先版本判出 vacuous 就停（只能"换门集"，而换门集要人给新门）
                    #   ⇒ 闭环缺一段：判出缺陷却无人修。
                    if _derive:
                        新门 = _try_derive(g, target, work_dir, cwd_default, repo_root)
                        if 新门 is not None:
                            entry["derived_gate"] = 新门
                            entry["detail"] += (
                                f"；★ 已派生替换门 `{新门.get('name')}`"
                                f"（缺陷对：{新门.get('_defect_pair')}，"
                                f"{新门.get('_discriminates')}）")
                        else:
                            entry["detail"] += "；派生失败（如实记录，不假装已修）"
                all_ok = False
            report.append(entry)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return all_ok, report


def _try_derive(g: dict, target: Path, work_dir: Path, cwd_default,
                repo_root: Path | None) -> dict | None:
    """对恒真门尝试构造性派生一个更细的门（TS-384 路径）。

    失败一律返回 None（如实报告，不假装）。派生本身不抛异常到调用方。

    ★ 防无限细化：**派生门不再派生**（`_derived_from` 已存在 ⇒ 直接返回 None）。
      对应 `evolution_loop_closes` 的"每轮落回全称内"——细化是**有界**的，
      不是无休止地越切越细。少了这条守卫，恒真门会每轮派生一个 `_细分`，
      名字与数量都无界增长（自检会红）。
    """
    if g.get("_derived_from"):
        return None                     # 派生门不再派生（有界性守卫）
    try:
        from agentscope_orchestrator import discriminant as _disc
        # 派生需要"门读的是哪份内容"——用 witness 已解析出的真实目标，
        # 与 _gate_read_target 同口径（否则又会踩 in:/src 固定文件的坑）。
        g2 = dict(g)
        for key in ("in", "src"):
            if isinstance(g2.get(key), str):
                g2[key] = str(target)
        if not g2.get("cwd"):
            g2["cwd"] = str(repo_root if repo_root is not None else work_dir)
        return _disc.派生判别式(g2, target, work_dir, cwd_default)
    except Exception:      # noqa: BLE001 派生失败不该炸见证
        return None


def _referenced_by_project(product: Path, work_dir: Path) -> bool:
    """产物是否**被工程任何文件引用**（机检，用于区分 decoupled 与 vacuous）。

    以产物**模块名**（去扩展名）为键，在 work_dir 下的源码文件里搜。
    这是廉价静态判据：找不到任何引用 ⇒ 该文件不参与工程编译/运行。

    诚实边界：只做**名字级**匹配，不做语义分析；命中即认为"已接进工程"
    （宁可放过，不冤枉真接线）。找不到才判 decoupled。"""
    stem = product.stem
    if not stem:
        return True                     # 无从判断 → 不判 decoupled（宁可放过）
    exts = {".rs", ".py", ".toml", ".mod", ".ts", ".js", ".c", ".h", ".cpp"}
    try:
        for f in work_dir.rglob("*"):
            if not f.is_file() or f.suffix not in exts:
                continue
            if f.resolve() == product.resolve():
                continue                # 跳过产物自身
            try:
                if stem in f.read_text(encoding="utf-8", errors="ignore"):
                    return True
            except OSError:
                continue
    except OSError:
        return True
    return False


def summary_line(report: list[dict]) -> str:
    """一行摘要（供日志/回执）。"""
    ok = sum(1 for r in report if r["witness"] == "ok")
    vac = [r["gate"] for r in report if r["witness"] == "vacuous"]
    dec = [r["gate"] for r in report if r["witness"] == "decoupled"]
    unk = [r["gate"] for r in report if r["witness"] == "unknown"]
    parts = [f"有区分力 {ok}/{len(report)}"]
    if vac:
        parts.append(f"★恒真门 {len(vac)}：{', '.join(vac[:5])}")
    if dec:
        parts.append(f"★未接线 {len(dec)}：{', '.join(dec[:5])}")
    if unk:
        parts.append(f"未验 {len(unk)}：{', '.join(unk[:5])}")
    return "；".join(parts)


def _selftest() -> int:
    """自检：真门必须有区分力，恒真门必须被识别为 vacuous。"""
    import tempfile as _tf
    fails: list[str] = []
    d = Path(_tf.mkdtemp(prefix="gw_selftest_"))
    try:
        f = d / "m.py"
        f.write_text("import os\n\ndef add(a,b):\n    return a+b\n", encoding="utf-8")

        # ① 真门：必须通过见证
        good = [
            {"type": "grep_required", "name": "has_add", "in": str(f),
             "pattern": r"def add"},
            {"type": "grep_forbidden", "name": "no_todo", "in": str(f),
             "pattern": r"todo!\("},
            {"type": "whole_reading", "name": "has_import", "in": str(f),
             "mode": "contains", "must_contain": ["import os"]},
        ]
        ok, rep = witness_gates(good, f, d)
        if not ok:
            fails.append("真门应通过见证：" + summary_line(rep))

        # ② 恒真门（真·无区分力）：`must_contain: [""]` —— 空串在**任何**文本
        #    （含空文件）里都命中 ⇒ 该门对任何产物都绿 ⇒ 必须判 vacuous。
        #    注：早先我误用 `grep_required` + `pattern="^"` 当恒真门，那是**错的**——
        #    grep_required 在 gate_spec 里是**子串**判定，`"^"` 是普通字面串、
        #    空文件里必然不命中，所以那个门其实**有**区分力。自检用例本身写错了。
        vac = [{"type": "whole_reading", "name": "always", "in": str(f),
                "mode": "contains", "must_contain": [""]}]
        ok2, rep2 = witness_gates(vac, f, d)
        got = rep2[0]["witness"] if rep2 else "?"
        if got != "vacuous":
            fails.append(f"恒真门（must_contain=[''] 恒命中）应判 vacuous，实得 {got}")

        # ③ 无 pattern 的 grep 门 ⇒ 造不出变异 ⇒ 必须如实报 unknown，
        #    既不误判为 ok（假装验过），也不误判为 vacuous（冤枉真门）。
        unk0 = [{"type": "grep_required", "name": "noPat", "in": str(f)}]
        ok5, rep5 = witness_gates(unk0, f, d)
        if rep5 and rep5[0]["witness"] != "unknown":
            fails.append(f"无 pattern 的门应报 unknown，实得 {rep5[0]['witness']}")

        # ④ 不可构造变异的门 → unknown，且不误判为 ok
        unk = [{"type": "scan_output", "name": "noPat", "src": str(f)}]
        ok4, rep4 = witness_gates(unk, f, d)
        if rep4 and rep4[0]["witness"] not in ("unknown", "vacuous"):
            fails.append(f"无 pattern 的 scan_output 应为 unknown，实得 {rep4[0]['witness']}")

        # ⑤ ★ 未接线（decoupled）：工程级门 + 产物**未被任何文件引用**
        #    实测来源：真工程任务里模型把新模块写进 src/ 但没在 lib.rs 声明它，
        #    于是 `cargo check --lib` 根本不编译它 —— 门绿但目标未达成。
        #    决定性实验：把该文件写成语法错误，cargo check 仍 exit=0。
        #    ⇒ 必须判 decoupled（补救=补接线），**不能**误判成 vacuous（换门）。
        d2 = Path(_tf.mkdtemp(prefix="gw_decoupled_"))
        try:
            orphan = d2 / "孤立模块.py"
            orphan.write_text("def f():\n    return 1\n", encoding="utf-8")
            # 一个"只看工程、不看该文件"的门：跑 python 编译**另一个**文件
            other = d2 / "主入口.py"
            other.write_text("x = 1\n", encoding="utf-8")
            proj_gate = [{"type": "compile", "name": "proj_ok",
                          "cmd": ["python", "-m", "py_compile", str(other)],
                          "cwd": str(d2), "expect_exit": 0}]
            ok6, rep6 = witness_gates(proj_gate, orphan, d2)
            got6 = rep6[0]["witness"] if rep6 else "?"
            if got6 != "decoupled":
                fails.append(f"未接线产物（无任何引用）应判 decoupled，实得 {got6}")
        finally:
            shutil.rmtree(d2, ignore_errors=True)

        # ⑥ ★★ 门读的是**另一个文件**（不是产物）—— 这是最易假红的一处。
        #    实测来源：真工程任务里 `lib_declares_probe2_mod` 门读 `src/lib.rs`，
        #    检查 lib.rs 有没有声明新模块。早先版本一律变异**产物**，
        #    门看不见变异 ⇒ 门仍绿 ⇒ 误判恒真 ⇒ 编排器反复换门集烧 31 万字符。
        #    正确行为：变异**门读取的那个文件**（lib.rs），门应变红。
        d3 = Path(_tf.mkdtemp(prefix="gw_readother_"))
        try:
            prod = d3 / "新模块.rs"
            prod.write_text("pub fn f() -> u64 { 1 }\n", encoding="utf-8")
            lib = d3 / "lib.rs"
            lib.write_text("#[path = \"新模块.rs\"] pub mod 新模块;\n", encoding="utf-8")
            # 门读 lib.rs，要求含 mod 声明；产物是 新模块.rs
            gate_other = [{"type": "whole_reading", "name": "lib_declares_mod",
                           "in": str(lib), "mode": "contains",
                           "must_contain": ["新模块"]}]
            ok7, rep7 = witness_gates(gate_other, prod, d3)
            got7 = rep7[0]["witness"] if rep7 else "?"
            if got7 != "ok":
                fails.append(
                    f"门读另一文件（lib.rs）时应变异该文件并判 ok，实得 {got7}"
                    f"（detail={rep7[0]['detail'] if rep7 else ''}）")
        finally:
            shutil.rmtree(d3, ignore_errors=True)

        # ⑦ ★★★ 恒真门必须**派生**替换门（TS-384 路径的接线验证）。
        #    早先版本判出 vacuous 就停 —— 只能"换门集"，而换门集要**人给新门**，
        #    闭环缺一段。现按 `defect_is_endogenous_drive` 构造性派生：
        #      缺陷对 (x,y) → T' z = if z = x then 0 else 1
        #    这里造一个**恒真门**（must_contain=[''] 恒命中，任何内容都绿），
        #    要求见证不但判它 vacuous，还要给出一个**真的能变红**的替换门。
        d4 = Path(_tf.mkdtemp(prefix="gw_derive_"))
        try:
            prod4 = d4 / "产物.py"
            prod4.write_text("def f():\n    return 1\n", encoding="utf-8")
            vac4 = [{"type": "whole_reading", "name": "always", "in": str(prod4),
                     "mode": "contains", "must_contain": [""]}]
            ok8, rep8 = witness_gates(vac4, prod4, d4)
            got8 = rep8[0]["witness"] if rep8 else "?"
            if got8 != "vacuous":
                fails.append(f"恒真门应判 vacuous，实得 {got8}")
            dg = rep8[0].get("derived_gate") if rep8 else None
            if dg is None:
                fails.append(
                    "恒真门未派生出替换门（TS-384 路径未接线）"
                    f"（detail={rep8[0].get('detail') if rep8 else ''}）")
            else:
                # 派生门必须**当场真跑**证明有区分力：对空内容应变红
                if not dg.get("_discriminates"):
                    fails.append("派生门缺区分力记录")
                # 独立复验：把派生门跑在"好内容"与"空内容"上，必须判不同档
                from agentscope_orchestrator import gate_spec as _gs
                good_p, bad_p = d4 / "good.py", d4 / "bad.py"
                good_p.write_text("def f():\n    return 1\n", encoding="utf-8")
                bad_p.write_text("", encoding="utf-8")
                dg_run = dict(dg)
                dg_run.pop("_derived_from", None)
                dg_run.pop("_defect_pair", None)
                dg_run.pop("_discriminates", None)
                nm = dg_run.get("name")
                rg, _ = _gs.evaluate([dg_run], good_p, d4, ".")
                rb, _ = _gs.evaluate([dg_run], bad_p, d4, ".")
                if rg.get(nm) == rb.get(nm):
                    fails.append(
                        f"派生门无区分力（好内容={rg.get(nm)} 空内容={rb.get(nm)}）"
                        "—— 派生不许假装")
        finally:
            shutil.rmtree(d4, ignore_errors=True)

        # ★★★★★ R13 判据力（数术层接线·TS-468 §2）：**σ 必须是平衡**。
        #   ① **目录读空间**：R11 起 `in` 可为目录 ⇒ 旧路径 `target.is_file()`
        #      为假 ⇒ 恒判 unknown，而 unknown **不置 all_ok=False**
        #      ⇒ 门**永不接受见证**却照样通过 = **假绿**（我自己 R11 引入的）。
        #   ② **σ 不动点**：变异后门读的文本逐字未变 ⇒ 保真方程对**任何**门
        #      都不成立 ⇒ 真门被误判「恒真」= **假红** ⇒ 必须报 unknown。
        d5 = Path(tempfile.mkdtemp(prefix="gw_r13_"))
        try:
            (d5 / "sub").mkdir()
            (d5 / "sub" / "干净.rs").write_text("fn a() {}\n", encoding="utf-8")
            _门目录 = {"name": "no_scaffold", "type": "grep_forbidden",
                       "in": str(d5), "strip": "comments",
                       "pattern": r"todo!\(|unimplemented!\("}
            _ok, _rep = witness_gates([_门目录], d5 / "sub" / "干净.rs", d5,
                                      repo_root=d5, _derive=False)
            _e = _rep[0]
            if _e["witness"] != "ok":
                fails.append(f"R13 目录读空间的门必须能见证，实得 {_e['witness']}"
                             f"（旧实现恒判 unknown ⇒ 假绿）")
            if not _e.get("保真"):
                fails.append("R13 目录读空间见证应给出保真=True")
            # 注入文件必须已无条件删除（不得污染真实源码树）
            _残 = [x.name for x in d5.rglob("*") if x.is_file()]
            if any("注入" in x for x in _残):
                fails.append(f"R13 注入文件残留污染：{_残}")
            # ② 造不出见证 ⇒ unknown，不假装
            _ok2, _rep2 = witness_gates(
                [{"name": "造不出", "type": "grep_forbidden", "in": str(d5),
                  "pattern": r"\A\z."}], d5 / "sub" / "干净.rs", d5,
                repo_root=d5, _derive=False)
            if _rep2[0]["witness"] != "unknown":
                fails.append(f"R13 造不出见证的门应报 unknown，实得"
                             f" {_rep2[0]['witness']}")
            # ③ σ 是不动点 ⇒ unknown（**不得** vacuous：那是假红）
            _f1 = d5 / "单文件.rs"
            _f1.write_text("fn ok() {}\n", encoding="utf-8")
            _原mut = _MUTATORS["grep_forbidden"]
            _MUTATORS["grep_forbidden"] = lambda text, g: (text, "逐字未变")
            try:
                _ok3, _rep3 = witness_gates(
                    [{"name": "不动点σ", "type": "grep_forbidden",
                      "in": str(_f1), "pattern": r"todo!\("}],
                    _f1, d5, repo_root=d5, _derive=False)
                _e3 = _rep3[0]
                if _e3["witness"] != "unknown":
                    fails.append(f"R13 σ 有不动点（变异逐字未变）必须报 unknown"
                                 f"（报 vacuous 就是**假红**），实得 {_e3['witness']}")
                if "derived_gate" in _e3:
                    fails.append("R13 σ 有不动点时**不得**派生替换门（假红会烧 token）")
                if "不动点" not in _e3.get("detail", ""):
                    fails.append("R13 因由须写明「不动点」")
            finally:
                _MUTATORS["grep_forbidden"] = _原mut
        finally:
            shutil.rmtree(d5, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    if fails:
        print("gate_witness 自检红：")
        for x in fails:
            print("  ✗", x)
        return 1
    print("gate_witness 自检：10/10 全绿（真门有区分力 · 恒真门判 vacuous 并**派生**"
          "替换门 · 未接线判 decoupled · 门读他文件时变异该文件 · 不可验如实报 unknown"
          " · **σ 必须是平衡**：目录读空间可见证 + σ 不动点报 unknown 不报假红）")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
