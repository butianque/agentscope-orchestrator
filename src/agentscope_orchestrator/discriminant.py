"""缺陷 → 判别式的构造性派生（TS-384 路径的工程落形）。

## 数学依据（活库已机证，逐条核验过）

`TS384.defect_is_endogenous_drive`（`TS384_SemanticTruthUniversality.lean:529`）：

    theorem defect_is_endogenous_drive (T : SemTruth384 Nat) :
      (Defect384 T → ¬ Function.Injective T)
      ∧ (∀ x y : Nat, x ≠ y → T x = T y →
          ∃ T' : SemTruth384 Nat,
            T' x ≠ T' y                          -- 新判别式**保证**分开该对
            ∧ (∀ z : Nat, T' z = 0 ∨ T' z = 1)   -- 值域 {0,1}
            ∧ T' x = 0)

`TS384.Defect384`（同文件 :507）：

    def Defect384 {α : Type} (T : SemTruth384 α) : Prop := ∃ x y : α, x ≠ y ∧ T x = T y

`TS384.evolution_loop_closes`（同文件 :566）：
    缺陷 → 细化 → 新真值 → **仍满足全称**（每轮落回全称内，故演化不破坏全称性）。

## 本模块的映射（判据仍全机器可算）

| 数学对象 | 工程对应物 |
|---|---|
| 判别式 `T : α → β` | 判据门（把产物映到 绿/红） |
| `Defect384 T`（分不开的两点 `x ≠ y` 而 `T x = T y`） | **两个不同的坏产物被判同一档**（门分不开它们） |
| 缺陷对 `(x, y)` | 一对**必须被分开**的产物（一个是坏的、一个也是坏的但坏法不同） |
| `T' z = if z = x then 0 else 1` | **新门**：专门认得出 `x` 这一类，与 `y` 分开 |
| `defect_is_endogenous_drive` | 本模块 `派生判别式()`：从缺陷对**构造性**造新门，不调模型 |
| `evolution_loop_closes` | 新门加入门集后，**门集仍是有界判别式**（不会无限细化） |

## 关键设计：不靠模型

原 `gate_witness` 只能**判**一个门是不是恒真（`vacuous`），判出来之后**没有下文**——
只能"换门集"（要人给新门）。本模块补的是**下文**：
从缺陷对**构造性地**造出替换门，全程零 LLM。

## 诚实边界

· 构造是**二值**的（`T' z ∈ {0,1}`），**一轮只分开一对**。多缺陷需多轮
  （对应库里 `truthMod384 q` 的 `q` 类版本）。
· 造出的门必须**当场真跑验证**有区分力（造门 ≠ 门有用）；
  验不过就如实报 `未能派生`，不假装。
· 只对**有缺陷对可指认**的门型做派生（compile / grep 系）。
  需要外部网络的命令门标 `unknown`，不猜。
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from agentscope_orchestrator import gate_spec

# ── 缺陷对：一对"应当被分开"的产物 ────────────────────────────────
def _坏产物对(门: dict, 原产物文本: str) -> tuple[str, str, str] | None:
    """构造一对**应当被该门区分开**的产物 `(坏, 好, 说明)`。

    ★ 口径（自检红换来的，务必守住）：
      `Defect384 T := ∃ x y, x ≠ y ∧ T x = T y` —— 缺陷 = **两个不同输入被判同一档**。
      所以缺陷对取 `(坏, 好)`：
        · 好 = 原产物（应当**绿**）
        · 坏 = 一份**应当红**的产物
      门若判 `T(坏) = T(好)` ⇒ 分不开 ⇒ 正是缺陷。
      **不能**用"两个坏产物都红"当缺陷判据：恒真门**从不红**，
      那个判据在恒真门上永远不触发（早先版本就错在这里，自检当场抓住）。

    返回 None ⇒ 该门型造不出缺陷对（如实标 unknown）。
    """
    t = 门.get("type")
    if t == "compile":
        # 坏：语法错（应当红）
        return (原产物文本 + "\n@@@ SYNTAX_WITNESS @@@\n",
                原产物文本, "语法错产物 vs 原产物")
    if t == "grep_required":
        pats = _模式(门)
        if not pats:
            return None
        # 坏：清空（必需模式全缺，应当红）
        return ("", 原产物文本, f"清空 vs 原产物（必需 {pats[0]!r}）")
    if t == "grep_forbidden":
        pats = _模式(门)
        if not pats:
            return None
        # 坏：注入禁词（应当红）
        lit0 = _可命中字面(pats[0], 原产物文本)
        if lit0 is None:
            return None
        return (原产物文本 + "\n" + lit0 + "\n",
                原产物文本, f"注入禁词 {lit0!r} vs 原产物")
    if t == "whole_reading":
        mode = 门.get("mode", "contains")
        if mode == "contains":
            # 坏：清空（必需标记全缺，应当红）
            return ("", 原产物文本, "清空 vs 原产物")
        if mode == "forbidden":
            emp = [str(x) for x in (门.get("must_not") or [])]
            if not emp:
                return None
            return (原产物文本 + "\n" + "\n".join(emp) + "\n",
                    原产物文本, "注入 must_not vs 原产物")
        if mode == "equal":
            return (原产物文本 + "\n# 漂移\n", 原产物文本, "漂移 vs 原产物")
        return None
    if t == "scan_output":
        pats = _模式(门)
        if not pats:
            return None
        lit0 = _可命中字面(pats[0], 原产物文本)
        if lit0 is None:
            return None
        return (原产物文本 + "\n" + lit0 + "\n",
                原产物文本, f"注入 scan pattern {lit0!r} vs 原产物")
    return None


def _模式(门: dict) -> list[str]:
    p = 门.get("patterns")
    if isinstance(p, list) and p:
        return [str(x) for x in p if str(x).strip()]
    if 门.get("pattern"):
        return [str(门["pattern"])]
    return []


def _可命中字面(模式: str, 文本: str) -> str | None:
    """造一个能被该正则真正命中的字面串；验不过返回 None（不给假见证）。"""
    try:
        m = re.search(模式, 文本)
    except re.error:
        return None
    if m and m.group():
        return m.group()
    # 去转义后试
    cands = [re.sub(r"\\(.)", r"\1", 模式)]
    # 去正则元字符后试
    cands.append(re.sub(r"[\\^$.|?*+()\[\]{}]", "", 模式))
    for c in cands:
        if not c:
            continue
        try:
            if re.search(模式, c):
                return c
        except re.error:
            continue
    return None


def _以产物为被测(门: dict, 文件: Path) -> dict:
    """把门的读取目标**指向被测产物**，使门真的读这份内容。

    ★ 为什么必须做（自检红换来的）：
      门若显式写了 `in:`（或 `scan_output` 的 `src:`），它就**固定读那个文件**，
      与传入的 `path` 无关。此时拿变异副本去评估，门读的仍是原文件 ⇒
      两个坏产物都被判绿 ⇒ 缺陷对被误判成"不存在"，派生永远不发生。
      这与 `gate_witness._gate_read_target` 是同一个坑的另一面。
    """
    g = dict(门)
    if "in" in g:
        g["in"] = str(文件)
    if g.get("type") == "scan_output" and "src" in g:
        g["src"] = str(文件)
    return g


# ── 结构关键字：判"产物里有没有真声明"（语言无关）──────────────────
#   ★ 为什么用结构关键字而不是产物原文（实测换来的真教训）：
#     初版派生门直接钉**好产物的原文行**（如 `patterns: ["alpha_line"]`）。
#     后果：**任何"不同但同样合法"的产物都被判红** —— 实测 6/6 全红。
#     这正是 `constraint.py`（TS-378）抓的**假红**：
#       约束同类（目标眼里都达标）却被门判不同档 ⇒ 判别力超出目标上限。
#     即：门钉的是"这一份产物"，不是"目标要求的结构"。
#     修法：派生门钉**结构性质**（有没有真声明），不同写法同样满足。
_声明关键字: tuple[str, ...] = (
    # Lean
    "theorem ", "lemma ", "def ", "structure ", "instance ", "example ",
    # Rust
    "fn ", "struct ", "enum ", "impl ", "trait ", "mod ",
    # Python / 通用
    "def ", "class ", "import ", "function ", "func ",
)


def _结构关键字(文本: str) -> list[str]:
    """产物里出现的**声明关键字**（去重，保序）。

    这是语言无关的**结构性质**：真写了声明的产物必有其一；
    空文件/纯注释/纯文本必无。用它造门 ⇒ 换写法仍绿（不假红）。
    """
    出: list[str] = []
    for k in _声明关键字:
        if k in 文本 and k not in 出:
            出.append(k)
    return 出


def _造结构门(门: dict, 好文本: str) -> dict | None:
    """按**结构性质**造一个细分门（替代钉原文的过细门）。

    对应 `rate_is_free_parameter`（TS369:253）的满射性：
    门的分辨率与"目标真要区分的东西"**恰好相称**，不多不少。
    钉原文 = 多分辨了（假红）；钉结构 = 恰好。

    返回门 dict，或 None（造不出结构门时**如实返回 None**，
    而不是退回过细门去造假红 —— 这是本模块的纪律）。
    """
    基础 = {k: v for k, v in 门.items() if k in ("cwd", "timeout", "stall")}
    if "in" in 门:
        基础["in"] = "{file}"
    关键字 = _结构关键字(好文本)
    if not 关键字:
        return None                 # 好产物里没有结构关键字 ⇒ 不硬造
    return dict(基础, name=f"{门.get('name','gate')}_结构",
                type="whole_reading", mode="contains",
                must_contain=[关键字[0]], _structural=True)


def 派生判别式(门: dict, 原产物: Path, work_dir: Path,
               cwd_default: str = ".") -> dict | None:
    """从一个**有缺陷**的门构造性地派生一个**更细的**门（TS-384 路径）。

    做法严格对应 `defect_is_endogenous_drive` 的构造 `T' z = if z = x then 0 else 1`：
      ① 找出该门分不开的一对产物 `(坏, 好)`（缺陷对）
      ② 构造新门 `T'`：**只认"坏"那一类的特征**（钉成必需/禁止模式）
      ③ **当场真跑**验证 `T'` 确实把 `坏` 与 `好` 分开
      ④ 验不过 ⇒ 返回 None（如实报"未能派生"，不假装）

    返回新门 dict（可加入门集），或 None。
    全程零 LLM：只用文本抽取 + 真跑门。
    """
    if not 原产物.is_file():
        return None
    try:
        原文本 = 原产物.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    对 = _坏产物对(门, 原文本)
    if 对 is None:
        return None
    坏文本, 好文本, 说明 = 对

    # ── ① 先确认这确实是**缺陷**：门对 `坏` 与 `好` 判**同一档** ──
    #    即 `T 坏 = T 好`（`Defect384` 的工程形态）。
    #    理想门应当 T 坏 = 红、T 好 = 绿；判同一档 ⇒ 分不开 ⇒ 缺陷。
    原门名 = 门.get("name", 门.get("type", "gate"))
    临时 = Path(tempfile.mkdtemp(prefix="disc_"))
    try:
        p坏, p好 = 临时 / "坏.tmp", 临时 / "好.tmp"
        p坏.write_text(坏文本, encoding="utf-8")
        p好.write_text(好文本, encoding="utf-8")
        r坏, _ = gate_spec.evaluate([_以产物为被测(门, p坏)], p坏, work_dir, cwd_default)
        r好, _ = gate_spec.evaluate([_以产物为被测(门, p好)], p好, work_dir, cwd_default)
        v坏, v好 = r坏.get(原门名), r好.get(原门名)
        if v坏 != v好:
            # 门已能分开 ⇒ 不是缺陷，无需派生
            return None
        if not v好:
            # 好产物自己就被判红 ⇒ 产物本身不合格（不是"门分不开"的缺陷）。
            # 这是另一类问题（该返工产物，不是换门），本模块**不**越权派生。
            return None
        # 到这里：好绿、坏也绿 ⇒ 门对"坏"无反应 = 恒真门 = Defect384 的工程形态

        # ── ② 构造新判别式：只认"坏"那一类的特征 ──
        新门 = _构造只认x(门, 坏文本, 好文本)
        if 新门 is None:
            return None

        # ── ③ 当场真跑验证：新门必须把 `坏` 与 `好` 分开 ──
        新名 = 新门.get("name")
        n坏, _ = gate_spec.evaluate([_以产物为被测(新门, p坏)], p坏, work_dir, cwd_default)
        n好, _ = gate_spec.evaluate([_以产物为被测(新门, p好)], p好, work_dir, cwd_default)
        v坏, v好 = n坏.get(新名), n好.get(新名)
        if v坏 == v好:
            # 分不开 ⇒ 构造失败，如实返回 None（不假装）
            return None

        新门["_derived_from"] = 原门名
        新门["_defect_pair"] = 说明
        新门["_discriminates"] = f"坏={'绿' if v坏 else '红'} 好={'绿' if v好 else '红'}"
        return 新门
    finally:
        shutil.rmtree(临时, ignore_errors=True)


def _构造只认x(门: dict, 坏文本: str, 好文本: str) -> dict | None:
    """构造一个能把 `坏` 与 `好` 分开的新门。

    ★ 优先级（**结构优先**，实测换来的）：
      ① **结构门**（`_造结构门`）：钉"有没有真声明" —— 换写法仍绿，**不假红**
      ② `坏` 独有行 → 必需（罕见：坏产物通常是被删的）
      ③ `好` 独有行 → 必需（**过细**：钉的是这一份产物 ⇒ 假红；
         仅当结构门造不出时才退到这条，且仍**当场真跑验证**）

    新门**不写死 `in:`**，而是写 `in: "{file}"` —— 让它**跟随被评判的那个产物**
    （`gate_spec._sub` 会把 `{file}` 换成当前产物路径）。写死具体路径会让门
    永远读同一个文件，正是本模块反复踩到的坑（自检抓了三次）。
    """
    原类型 = 门.get("type")
    基础 = {k: v for k, v in 门.items() if k in ("cwd", "timeout", "stall")}
    # 原门若显式读某文件，新门**跟随被评判产物**（不继承那个固定路径）
    if "in" in 门:
        基础["in"] = "{file}"
    if 门.get("type") == "scan_output" and "src" in 门:
        基础["src"] = "{file}"

    # ① ★ 结构门优先（不假红）
    sg = _造结构门(门, 好文本)
    if sg is not None and _验证可分开(sg, 坏文本, 好文本):
        return sg

    # ② 坏独有行 → 必需
    for 行 in _独有行(坏文本, 好文本):
        s = 行.strip()
        if len(s) < 3:
            continue
        g = dict(基础)
        g.update({"name": f"{门.get('name','gate')}_细分",
                  "type": "grep_required", "patterns": [s]})
        if _验证可分开(g, 坏文本, 好文本):
            return g

    # ③ 好独有行 → 必需（**过细**，退路）
    #    诚实标注：这类门钉的是"这一份产物"，换写法会假红
    #    ⇒ 由 `constraint.py`（TS-378）在交付前机检拦截。
    for 行 in _独有行(好文本, 坏文本):
        s = 行.strip()
        if len(s) < 3:
            continue
        g = dict(基础)
        g.update({"name": f"{门.get('name','gate')}_细分",
                  "type": "grep_required", "patterns": [s],
                  "_over_specific": True})     # ← 如实标注，供约束机检拦截
        if _验证可分开(g, 坏文本, 好文本):
            return g

    # ④ 好为空的极端情形：坏非空 ⇒ 用 forbidden 钉坏里的行
    for 行 in _独有行(坏文本, 好文本):
        s = 行.strip()
        if len(s) < 3:
            continue
        g = dict(基础)
        g.update({"name": f"{门.get('name','gate')}_细分",
                  "type": "grep_forbidden", "pattern": re.escape(s)})
        if _验证可分开(g, 坏文本, 好文本):
            return g

    # ⑤ compile 门：直接用 compile
    if 原类型 == "compile" and 门.get("cmd"):
        g = dict(基础)
        g.update({"name": f"{门.get('name','gate')}_细分", "type": "compile",
                  "cmd": 门["cmd"], "expect_exit": 门.get("expect_exit", 0)})
        if _验证可分开(g, 坏文本, 好文本):
            return g
    return None


def _独有行(文本A: str, 文本B: str) -> list[str]:
    """A 有而 B 没有的行（用于造区分性模式）。"""
    bs = set(文本B.splitlines())
    out = []
    seen = set()
    for ln in 文本A.splitlines():
        s = ln.strip()
        if s and s not in bs and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _验证可分开(g: dict, 坏文本: str, 好文本: str) -> bool:
    """真跑该门，确认它把 `坏` 与 `好` 分开**且方向正确**。

    ★★★★★ R19（真跑抓出的严重缺陷）：旧实现只查 `rx != ry` —— **只看分不分得开，
      不看方向**。于是**反向门**（好红坏绿）被当成合格派生门装进交付门集，
      编排器烧 17895 字符去追一条**只有坏产物能过**的门。

      实证（2026-10-15 10:32 真跑）派生出的反向门：
        `grep_required patterns=["@@@ SYNTAX_WITNESS @@@"]`，`_discriminates="坏=绿 好=红"`
        —— 那个标记正是 `_坏产物对` 注入的**语法错标记**。

    ★★★★★ 契约（`_坏产物对` 的 docstring）：**好 = 原产物（应当绿），
      坏 = 应当红**。所以合格判据是**两条都要**：
        · `好绿`（合格产物**不得**被误拒 ⇒ 防**假红**）；
        · `坏红`（不合格产物**必须**被抓 ⇒ 防**假绿**）。

    ★★★ 这不是打补丁，是接上库里已有的定理：TS-447 §6 机证
      **充要条件是方向性**（`read ∘ f = g ∘ read`），不是"能分开"；
      「能分开」只是单射性，而 TS-453 机证
      `read_inj_is_a_premise_not_a_fact` —— **单射是前提，不是结论**。
      本缺陷正是把「单射」当成了「方向保真」。
    """
    临时 = Path(tempfile.mkdtemp(prefix="disc_v_"))
    try:
        px, py = 临时 / "bad.tmp", 临时 / "good.tmp"
        px.write_text(坏文本, encoding="utf-8")
        py.write_text(好文本, encoding="utf-8")
        nm = g.get("name")
        rx, _ = gate_spec.evaluate([g], px, 临时, ".")
        ry, _ = gate_spec.evaluate([g], py, 临时, ".")
        v坏, v好 = rx.get(nm), ry.get(nm)
        # ★ 两条都查：好必须绿（防假红）**且** 坏必须红（防假绿）
        return (v好 is True) and (v坏 is False)
    except Exception:  # noqa: BLE001 造门失败不该炸调用方
        return False
    finally:
        shutil.rmtree(临时, ignore_errors=True)


def 补全门集(门集: list[dict], 原产物: Path, work_dir: Path,
             cwd_default: str = ".", 最大轮: int = 3) -> tuple[list[dict], list[dict]]:
    """迭代补全门集：对每个"分不开缺陷"的门，派生一个更细的门。

    对应 `evolution_loop_closes`：每轮派生**都落回**全称内（新门仍是有界判别式），
    故演化不破坏全称性。最大轮数防无限细化。

    返回 (新门集, 派生记录)。派生记录含每轮的缺陷对与区分结果，供审计回放。
    """
    当前 = [dict(g) for g in 门集]
    记录: list[dict] = []
    for 轮 in range(最大轮):
        本轮派生 = 0
        for g in list(当前):
            if g.get("_derived_from"):
                continue          # 派生门不再派生（防无限）
            新 = 派生判别式(g, 原产物, work_dir, cwd_default)
            if 新 is None:
                continue
            当前.append(新)
            本轮派生 += 1
            记录.append({"round": 轮 + 1, "from": g.get("name"),
                         "to": 新.get("name"),
                         "defect_pair": 新.get("_defect_pair"),
                         "discriminates": 新.get("_discriminates")})
        if 本轮派生 == 0:
            break
    return 当前, 记录


def 派生判别式q类(门: dict, 原产物: Path, work_dir: Path,
                   cwd_default: str = ".", 最大类: int = 8,
                  ) -> tuple[list[dict], list[dict]]:
    """**q 类**构造性派生：一轮分开**多对**缺陷（TS-384 §6 / TS-369 路径）。

    ## 数学依据（活库已机证，`#check` 核验过）

    `TS369.rate_is_free_parameter`（`TS369_RuleCapacity.lean:253`）：

        theorem rate_is_free_parameter (q : Nat) :
          ∃ disc : Nat → Nat, ∃ f : Nat → Fin (q + 1),
            (∀ a b : Nat, disc a = disc b ↔ f a = f b)   -- 恰好区分，不多不少
            ∧ Function.Surjective f                      -- 满射，不浪费状态

    `TS384.truthMod384`（`TS384_SemanticTruthUniversality.lean:345`）：
        def truthMod384 (q : Nat) : SemTruth384 Nat := fun n => n % (q + 1)
    `TS384.seed_is_exactly_truth_relevant`（:355）：
        `T a = T b ↔ enc a = enc b` —— 种子**恰好**保留 `T` 的分辨。

    ★ 关键点（用户「**不再**是鸽笼限制能力」的机器形式）：
      **`q` 是自由参数**，不是固定的界。q=2 时压到 1 bit；q 大时不压缩。
      故「一轮能分开几类」**由判别函数决定**，不由技巧定
      （`gap_determined_by_discriminant` :272）。

    ## 与二值版的差别

    二值版（`派生判别式`）严格照 `defect_is_endogenous_drive` 的构造
    `T' z = if z = x then 0 else 1` —— **一轮只分开一对**（值域 {0,1}）。
    本函数是它的 q 类推广：把缺陷对**归成 q 个等价类**，
    每类造一个门，一轮分开**多对**。对应库里 §6 的 `truthMod384 q`。

    ## 诚实边界

    · `q` 由**实际发现的缺陷类数**定，不是随便取大 —— 对应满射性
      `seed_uses_all_states`（「分辨率与状态数恰好相称」，不浪费）。
    · 每类造出的门**逐个当场真跑验证**；验不过的**不返回**（如实记录）。
    · 类数上限 `最大类` 防门集爆炸（硬护栏）。

    返回 (新门列表, 派生记录)。
    """
    if not 原产物.is_file():
        return [], []
    try:
        原文本 = 原产物.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], []

    对 = _坏产物对(门, 原文本)
    if 对 is None:
        return [], []
    坏文本, 好文本, 说明 = 对

    原门名 = 门.get("name", 门.get("type", "gate"))
    临时 = Path(tempfile.mkdtemp(prefix="discq_"))
    try:
        p坏, p好 = 临时 / "坏.tmp", 临时 / "好.tmp"
        p坏.write_text(坏文本, encoding="utf-8")
        p好.write_text(好文本, encoding="utf-8")
        r坏, _ = gate_spec.evaluate([_以产物为被测(门, p坏)], p坏, work_dir, cwd_default)
        r好, _ = gate_spec.evaluate([_以产物为被测(门, p好)], p好, work_dir, cwd_default)
        v坏, v好 = r坏.get(原门名), r好.get(原门名)
        if v坏 != v好 or not v好:
            return [], []          # 不是"恒真门"型缺陷 ⇒ 不越权派生

        # ── 把"好"产物的特征切成 q 个等价类，每类造一个门 ──
        #   对应 `truthMod384 q`：把 n 按 q 取模归类。
        #   ★ 每类钉的是**结构关键字**（不是产物原文）—— 换写法仍绿，不假红。
        #     实测教训：初版钉原文 ⇒ 6/6 对不同合法产物全判红（假红）。
        关键字 = _结构关键字(好文本)
        if not 关键字:
            # 好产物里没有结构关键字 ⇒ 只用结构门（造不出就如实返回空）
            sg = _造结构门(门, 好文本)
            if sg is not None and _验证可分开(sg, 坏文本, 好文本):
                sg["_derived_from"] = 原门名
                sg["_defect_pair"] = 说明
                sg["_discriminates"] = "坏=红 好=绿"
                sg["_class"] = "0/1"
                return [sg], [{"class": 0, "of": 1, "gate": sg["name"],
                               "line": str(关键字)}]
            return [], []
        q = min(最大类, max(1, len(关键字)))
        新门们: list[dict] = []
        记录: list[dict] = []
        for i, k in enumerate(关键字[:q]):
            候选 = {k2: v for k2, v in 门.items()
                    if k2 in ("cwd", "timeout", "stall")}
            if "in" in 门:
                候选["in"] = "{file}"
            候选.update({"name": f"{原门名}_类{i}",
                         "type": "whole_reading", "mode": "contains",
                         "must_contain": [k]})
            if _验证可分开(候选, 坏文本, 好文本):
                候选["_derived_from"] = 原门名
                候选["_defect_pair"] = 说明
                候选["_discriminates"] = "坏=红 好=绿"
                候选["_class"] = f"{i}/{q}"
                候选["_structural"] = True      # 结构门（非钉原文）
                新门们.append(候选)
                记录.append({"class": i, "of": q, "gate": 候选["name"],
                              "line": k})
        return 新门们, 记录
    finally:
        shutil.rmtree(临时, ignore_errors=True)


def 补全门集q类(门集: list[dict], 原产物: Path, work_dir: Path,
                cwd_default: str = ".", 最大轮: int = 3,
                最大类: int = 8) -> tuple[list[dict], list[dict]]:
    """q 类版补全门集：一轮为每个缺陷门派生**多个**细分门。

    对应 `evolution_loop_closes`：每轮派生都落回全称内（新门仍有界）。
    返回 (新门集, 派生记录)。
    """
    当前 = [dict(g) for g in 门集]
    记录: list[dict] = []
    for 轮 in range(最大轮):
        本轮派生 = 0
        for g in list(当前):
            if g.get("_derived_from"):
                continue          # 派生门不再派生（有界性守卫）
            新门们, 子记录 = 派生判别式q类(g, 原产物, work_dir, cwd_default, 最大类)
            for 新, 子 in zip(新门们, 子记录):
                当前.append(新)
                本轮派生 += 1
                记录.append({"round": 轮 + 1, "from": g.get("name"),
                             **子})
        if 本轮派生 == 0:
            break
    return 当前, 记录


def _selftest() -> int:
    """自检：全部机器可算。"""
    import tempfile as _tf
    ok = total = 0

    def chk(name: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  ✗ {name}")

    d = Path(_tf.mkdtemp(prefix="disc_st_"))
    try:
        p = d / "p.txt"
        p.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        # ① 恒真门（must_contain=[''] 恒命中）⇒ 应派生
        恒真 = {"name": "always", "type": "whole_reading", "in": str(p),
                "mode": "contains", "must_contain": [""]}
        对 = _坏产物对(恒真, p.read_text(encoding="utf-8"))
        chk("恒真门造出缺陷对", 对 is not None)
        新 = 派生判别式(恒真, p, d, ".")
        chk("恒真门派生成功", 新 is not None)
        if 新:
            chk("派生门带区分说明", bool(新.get("_discriminates")))
            chk("派生门记来源", 新.get("_derived_from") == "always")
            # ★ 独立复验：派生门必须真能分开（好绿、坏红）
            from agentscope_orchestrator import gate_spec as _gs
            gp, bp = d / "good.txt", d / "bad.txt"
            gp.write_text("alpha\nbeta\n", encoding="utf-8")
            bp.write_text("", encoding="utf-8")
            run = {k: v for k, v in 新.items() if not k.startswith("_")}
            nm = run.get("name")
            rg, _ = _gs.evaluate([run], gp, d, ".")
            rb, _ = _gs.evaluate([run], bp, d, ".")
            chk("派生门真好绿", rg.get(nm) is True)
            chk("派生门真坏红", rb.get(nm) is False)

        # ② 真门（有区分力）⇒ **不**派生（不越权改好门）
        真门 = {"name": "req", "type": "grep_required", "in": str(p),
                "patterns": ["alpha"]}
        chk("真门不派生", 派生判别式(真门, p, d, ".") is None)

        # ③ 好产物自己就红 ⇒ 不派生（那是产物该返工，不是换门）
        过严 = {"name": "strict", "type": "grep_required", "in": str(p),
                "patterns": ["绝不存在的模式"]}
        chk("好产物自身红则不派生", 派生判别式(过严, p, d, ".") is None)

        # ④ 补全门集：不炸、记录结构正确
        门集 = [dict(恒真)]
        新集, 记录 = 补全门集(门集, p, d, ".")
        chk("补全后门集变大", len(新集) > len(门集))
        chk("记录结构正确",
            all("round" in r and "from" in r for r in 记录))

        # ⑤ 源文件不存在 → None，不抛
        chk("缺产物返回 None",
            派生判别式(恒真, d / "不存在.txt", d, ".") is None)

        # ⑥ 无法造缺陷对的门型 → None
        chk("未知门型返回 None",
            _坏产物对({"type": "外星门"}, "x") is None)

        # ⑦ 独有行抽取正确
        chk("独有行正确", _独有行("a\nb\nc", "a\nc") == ["b"])

        # ⑧ 可命中字面：验不过给 None
        chk("非法正则给 None", _可命中字面("[", "abc") is None)
        chk("可命中字面真命中",
            _可命中字面("TODO", "x TODO y") == "TODO")

        # ⑨ ★ q 类派生：一轮分开**多对**（TS-384 §6 / TS-369 路径）
        #    库内 `rate_is_free_parameter q`：q 是**自由参数**，
        #    f 恰好区分 disc 能区分的（不多不少）且满射。
        #    ★ 用**真实形态**产物（含结构关键字）—— 纯文本无结构关键字时
        #      本模块**如实返回空**，不硬造过细门去造假红（见 ⑫）。
        p3 = d / "Artifact.lean"
        好Lean = ("import TS375_O1UniformLedger\n\n"
                  "namespace Orchestrated_auto\n\n"
                  "theorem global_ledger375 (n : Nat) : layerStep375 n = n := by\n"
                  "  rfl\n\n"
                  "def helper375 : Nat := 0\n\n"
                  "end Orchestrated_auto\n")
        p3.write_text(好Lean, encoding="utf-8")
        恒真3 = {"name": "always3", "type": "whole_reading", "in": str(p3),
                 "mode": "contains", "must_contain": [""]}
        门们, 记 = 派生判别式q类(恒真3, p3, d, ".", 最大类=8)
        chk("q 类派生返回多个门", len(门们) > 1)
        chk("q 类派生记录与门数一致", len(记) == len(门们))
        chk("q 类门都带溯源", all(g.get("_derived_from") == "always3" for g in 门们))
        chk("q 类门都带类号", all(g.get("_class") for g in 门们))
        chk("q 类门是结构门（非钉原文）",
            all(g.get("_structural") for g in 门们))
        # ★ 独立复验：每个 q 类门必须真能分开（好绿、坏红）
        全可分开 = True
        for g in 门们:
            # ★ R19：实参口径是 (坏, 好) —— 这里是「坏=空文本（应红）,
            #   好=好Lean（应绿）」，与全文件统一。
            if not _验证可分开(g, "", 好Lean):
                全可分开 = False
        chk("q 类门逐个真能分开", 全可分开)

        # ⑩ q 类补全：一轮派生多个（对比二值版一轮只派生一个）
        新集q, 记录q = 补全门集q类([dict(恒真3)], p3, d, ".", 最大轮=2, 最大类=8)
        chk("q 类补全门集增大", len(新集q) > 1)
        chk("q 类补全有记录", len(记录q) > 0)
        # 对比：二值版对同一门只派生一个
        新集2, 记录2 = 补全门集([dict(恒真3)], p3, d, ".", 最大轮=1)
        chk("二值版一轮只派生一个", len(记录2) == 1)
        chk("q 类一轮派生多于二值版", len(记录q) > len(记录2))

        # ⑪ 类数受上限约束（防门集爆炸）
        门们限, _ = 派生判别式q类(恒真3, p3, d, ".", 最大类=2)
        chk("类数不超上限", len(门们限) <= 2)

        # ⑫ ★★ 假红回归（本模块自查抓出的真缺陷，必须长期守住）
        #    初版派生门**钉好产物原文**（`patterns: ["alpha_line"]`）
        #    ⇒ 任何"不同但同样合法"的产物被判红（实测 6/6 全红）。
        #    这正是 `constraint.py`（TS-378）抓的**假红**：
        #      约束同类（目标眼里都达标）却被判不同档 ⇒ 判别力超出目标上限。
        #    修法：派生门钉**结构关键字**（"theorem " 等），换写法同样满足。
        别的 = d / "Artifact2.lean"
        别的.write_text("import TS376_InfoUniversality\n\n"
                        "theorem 另写的证明 (n : Nat) : n = n := by\n"
                        "  rfl\n\n"
                        "def 另写的辅助 : Nat := 1\n", encoding="utf-8")
        # 二值版派生门对"不同但合法"产物应**绿**（不假红）
        新b = 派生判别式(恒真3, p3, d, ".")
        chk("二值派生门是结构门", bool(新b and 新b.get("_structural")))
        新b跑 = {k: v for k, v in (新b or {}).items() if not k.startswith("_")}
        r别的, _ = gate_spec.evaluate([新b跑], 别的, d, ".") if 新b else ({}, [])
        chk("二值派生门对不同合法产物判绿（不假红）",
            新b is not None and r别的.get(新b["name"]) is True)
        # q 类派生门同样不假红
        rq全绿 = True
        for g in 门们:
            rq, _ = gate_spec.evaluate([{k: v for k, v in g.items()
                                         if not k.startswith("_")}], 别的, d, ".")
            if rq.get(g["name"]) is not True:
                rq全绿 = False
        chk("q 类派生门对不同合法产物全绿（不假红）", rq全绿)
        # 用 constraint.py（TS-378）独立复核：应判**尊重**约束
        if 门们:
            from agentscope_orchestrator import constraint as _cs
            rep = _cs.机检门集尊重约束(
                门们, [("原产物", p3), ("别的合法产物", 别的)], d,
                {"原产物": True, "别的合法产物": True}, ".")
            chk("TS-378 独立复核：结构门尊重约束", rep["ok"] is True)

        # ★★★★★ R19 方向门（真跑抓出的**严重**缺陷·必须长期守住）
        #   实证（2026-10-15 10:32 真跑）：编排器判一条 compile 门恒真后派生替换门
        #     出了 `grep_required patterns=["@@@ SYNTAX_WITNESS @@@"]`，
        #     `_discriminates="坏=绿 好=红"` —— **方向是反的**：
        #     那个标记正是 `_坏产物对` 注入的**语法错标记**
        #     ⇒ 该门**只让坏产物过**，任何合格产物必红
        #     ⇒ 装进交付门集后烧 17895 字符追一条不可能过的门。
        #   根因：`_验证可分开` 只查 `rx != ry`（分不分得开），**不查方向**。
        #   修法：好必须绿 **且** 坏必须红（TS-447 §6：充要条件是**方向性**）。
        #
        #   ★ 本门直接判**方向**，不判"有没有派生出门" —— 后者会因为
        #     "造不出门" 而恒真（无齿）。
        好文 = "import TS376_InfoUniversality\n\n" \
               "theorem 真定理 (n : Nat) : n = n := by\n  rfl\n"
        坏文 = 好文 + "\n@@@ SYNTAX_WITNESS @@@\n"     # ← 与 _坏产物对 同口径
        # ① 反向门必须被**拒绝**（这正是缺陷的形态）
        反向门 = {"name": "反向", "type": "grep_required", "in": "{file}",
                  "patterns": ["@@@ SYNTAX_WITNESS @@@"], "cwd": "."}
        chk("★ 反向门（好红坏绿）必须**不被接受**",
            _验证可分开(反向门, 坏文, 好文) is False)
        # ② 正向门必须被**接受**（否则本门恒红＝另一种无齿）
        #   ★ 探针自身的错（如实登记）：第一版用 `grep_required ["theorem "]`，
        #     但**好坏两条文本都含 "theorem "** ⇒ 那门**分不开**，被拒是**对的**
        #     —— 是我判据写错，不是门错。
        #   ★★ 这一对的正确**正向**门是「**禁止**该标记」：
        #     同一个 pattern，`required` 与 `forbidden` **恰是两个方向**。
        正向门 = {"name": "正向", "type": "grep_forbidden", "in": "{file}",
                  "pattern": re.escape("@@@ SYNTAX_WITNESS @@@"), "cwd": "."}
        chk("★ 正向门（好绿坏红）必须**被接受**",
            _验证可分开(正向门, 坏文, 好文) is True)
        # ③ 恒绿门（都绿）与恒红门（都红）都必须被拒
        恒绿门 = {"name": "恒绿", "type": "grep_required", "in": "{file}",
                  "patterns": ["import "], "cwd": "."}
        chk("★ 恒绿门（分开不了）必须被拒",
            _验证可分开(恒绿门, 坏文, 好文) is False)
        恒红门 = {"name": "恒红", "type": "grep_required", "in": "{file}",
                  "patterns": ["绝不可能出现的串ZZZ"], "cwd": "."}
        chk("★ 恒红门（分开不了）必须被拒",
            _验证可分开(恒红门, 坏文, 好文) is False)
        # ④ ★★★ 端到端：对**语法错型缺陷对**派生，结果**不得**是反向门。
        #    这是缺陷的真实形态：compile 门恒真 ⇒ 派生 ⇒ 出了反向门。
        假门 = {"name": "语法错门", "type": "compile", "in": "{file}",
                "cwd": ".", "cmd": ["python", "-c", "pass"], "expect_exit": 0}
        派 = _构造只认x(假门, 坏文, 好文)
        if 派 is None:
            chk("★ 语法错型缺陷对：派生门为 None（造不出可接受的门）", True)
        else:
            派跑 = {k: v for k, v in 派.items() if not k.startswith("_")}
            r好, _ = gate_spec.evaluate([派跑], p3, d, ".")
            # 用真实文件再跑一次"好"（口径与 _验证可分开 一致）
            pf = d / "R19好.txt"
            pf.write_text(好文, encoding="utf-8")
            rb = d / "R19坏.txt"
            rb.write_text(坏文, encoding="utf-8")
            r好2, _ = gate_spec.evaluate([派跑], pf, d, ".")
            r坏2, _ = gate_spec.evaluate([派跑], rb, d, ".")
            chk("★★ 派生门必须好绿（合格产物不得被误拒）",
                r好2.get(派["name"]) is True)
            chk("★★ 派生门必须坏红（不合格产物必须被抓）",
                r坏2.get(派["name"]) is False)
            chk("★★★ 派生门**不得**要求语法错标记（反向门形态）",
                "@@@ SYNTAX_WITNESS @@@" not in str(
                    派.get("patterns") or 派.get("pattern") or ""))
            del r好
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(f"  缺陷→判别式派生 自检：{ok}/{total} 通过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
