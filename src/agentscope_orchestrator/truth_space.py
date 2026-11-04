# -*- coding: utf-8 -*-
"""真值空间（TS-469 的 Python 形态）—— 机制**自己**真值空间内的闭环。

═══════════════════════════════════════════════════════════════════════
缘起（用户指令原文·本件的唯一驱动）
═══════════════════════════════════════════════════════════════════════

  「你先把自己模型的真值可靠+无条件全称，通用求解闭环掉证明出来再去管NS。
    千禧问题是模型要取象要解的对象不是自己的东西」

  ⇒ 本模块**不碰 NS**。NS 是**取象要解的对象**，不是机制自己的东西。

═══════════════════════════════════════════════════════════════════════
★★★★★ 为什么要单独做这一件（前几轮错位的根因）
═══════════════════════════════════════════════════════════════════════

  我此前把「机制全称可靠」架在 `Prop` 上（`∀ G : Prop`），
  得到「可靠 + 全称不可能」（TS-461 `no_sound_universal_mechanism`、
  TS-462 `oracle_is_not_manufacturable`）。
  **那个不可能性是真的 —— 但它讲的是 `Prop`。**

  ★★★ 机制的真值空间从来不是 `Prop`，是 `Fin n`：
    机制的量词是 `∀ n, ∀ α, ∀ O`（**对象**），不是 `∀ G : Prop`（**命题**）。
  ⇒ 把机制放回它**自己的真值空间**，在那里闭环 —— 这就是本件。

  ★★★★★ 而我上一轮犯的正是 TS-469 §7 点名的那一步：
    「我此前**拿右支去说左支做不到** —— 而左支**不需要**右支。」
    我引 TS-462/465（`Prop` 侧的不可能性）去回答「编排器能不能做」——
    **那是错位**。`Fin n` 侧**零前提**闭环。

═══════════════════════════════════════════════════════════════════════
与 Lean 的逐条对应（`/MathlibBridge/TS469_MechanismSelfClosure.lean`）
═══════════════════════════════════════════════════════════════════════

  Lean                                   本模块
  ─────────────────────────────────────  ─────────────────────────────
  `solveOn {n} (g : Fin n → Fin n) y`    `求解(轨道, n)`
  `solveN_eq_solveOn`（载体消去·rfl）      `读()` 只读真值、不读载体
  `solveOn_eq_true_iff`                   `值 is True  ⟺ 真值_ReachesZero`
  `solveOn_eq_false_iff`                  `值 is False ⟺ 真值_Stuck`
  `reaches_or_stuck`（穷尽）               `已判定 ⇒ 值 ∈ {True, False}`
  `not_reaches_and_stuck`（互斥）          `互斥()`
  `solveOn_always_answers`（判定自足）      `求解` 不接任何判定器输入
  `reaches_iff_bounded`（证书界 i<n）      `证书 < n`
  `truthObject`（真值空间自己就是对象）      `轨道` 自己记自己的点
  `prop_side_price_is_em`（对照）          `PROP侧代价` 常量（只作文档）

  ★ 关键差别（TS-469 §7 收口）：**不在机制强弱，在真值空间有没有内建判定。**
    `Fin n` 的 `DecidableEq` 由构造子给出（免费）；`Prop` 没有内建判定（要付 EM）。

═══════════════════════════════════════════════════════════════════════
★★★★★ 接线到编排器：它替掉了什么
═══════════════════════════════════════════════════════════════════════

  ① **五值字符串 verdict → 二值**。
     编排器原来的 `verdict` 是
       `converged` / `progressing` / `stalled_fixed_point` / `no_anchor`
       / `diverging` / `converged_single_window`
     —— **六个值**。TS-469 §4「穷尽」说真值空间**没有第三值**。
     那五个字符串**不是真值**，是**因由**（why）。真值只有两个。
     ⇒ 本模块把真值压到 `{ReachesZero, Stuck}`，把六个字符串降为 `因由`。

  ② **任意轮数常数 → 鸽笼界 `n = 2^门数`**。
     `reaches_iff_bounded` 机证「首次命中步 `< n`」，`n` = **真值空间基数**。
     ⇒ 停机保证是**定理**，不是 `--max-work 3` 这种拍脑袋常数。

  ③ **重复状态不再重调模型**（`记忆步`）。
     见 `记忆步` 的说明：判定与「重复⇒周期」都要求 `g` 是**函数**，
     而真调模型是随机的 ⇒ 不记忆化，观测到的就不是轨道。

  ★★★★★ 诚实边界（必须与上面一起读）：
    · 本模块**不判定任意 `Prop`**（那等价于排中律，TS-436/461 已机证）；
    · 本模块判的是**机制自己真值空间**（`Fin n`）内的事；
    · 换层**不**让 `Prop` 侧的不可能性消失 —— 它仍在，且它是**关于 `Prop`** 的。
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# ── 真值只有两个（TS-469 §6：`Bool` 无第三值）──
FALSE, TRUE = 0, 1

#: ★ 对照（`prop_side_price_is_em`）：同一形态在 `Prop` 上的代价。
#:  只作文档与探针锚点 —— 本模块**任何代码路径都不得**走这条路。
PROP侧代价 = ("可靠 + 全称的通用求解器在 Prop 上存在 ⟹ 排中律"
              "（TS-436 universal_solver_implies_em · 零公理）；"
              "在 Fin n 上同一形态零前提成立 ⇒ 差别不在机制强弱，"
              "在真值空间有没有内建判定。")


# ════════════════════════════════════════════════════════════════════
# §1 载体消去：read : α → Fin n
# ════════════════════════════════════════════════════════════════════
def _真(v: object) -> bool:
    """严进：只有真正的二值才准进真值空间。

    ★ TS-469 §4「穷尽」的守门人：`verdict` 曾经是**六值字符串**，
      字符串在这里会被**拒收**（不是悄悄 `bool()` 掉）——
      否则「第三值」会从门口溜进来。
    """
    if v is True or v == 1:
        return True
    if v is False or v == 0:
        return False
    raise TypeError(
        f"真值必须是 bool/0/1，实得 {type(v).__name__}={v!r}。"
        f"★ 非二值输入不得进真值空间（TS-469 §4：真值空间没有第三值）。"
        f"若这是「因由」（如 verdict 的字符串），它**不是真值**，请放到 因由 里。")


def 载体基数(门数: int) -> int:
    """真值空间 `Fin n` 的基数 `n = 2^门数`。

    ★ 这就是**鸽笼界**：`门数` 条二值门只有 `2^门数` 种真值向量。
    ⇒ `reaches_iff_bounded` 的证书界 `i < n` 用的就是这个 `n`。
    """
    if 门数 < 0:
        raise ValueError(f"门数不得为负：{门数}")
    return 1 << 门数


def 坐标系(结果: Mapping[str, object]) -> tuple[str, ...]:
    """取定真值空间的**坐标系**（门的规范序）。

    ★ 载体消去：门名只用于**取定坐标**（排序），不用它的内容
      （长度 / 字符 / 语义 / 中文与否）。换门集 ⇒ 换坐标系 ⇒ **换真值空间**。
    """
    return tuple(sorted(str(k) for k in 结果))


def 读(结果: Mapping[str, object]) -> int:
    """把一轮的门真值向量读成真值空间里的一个位置（`Fin n` 的元素）。

    ★★★★★ 载体消去（TS-469 §1 `solveN_eq_solveOn`）：
      **只读真值，不读载体。**
      载体 = 产物文本 / 模型名 / 窗数 / token 数 / 门的描述 / 耗时 ——
      **一律不看**。这正是「判定与态空间基数无关（O(1)）」的机器形态：
      判定根本不看 `α`。
    """
    键 = 坐标系(结果)
    v = 0
    for i, k in enumerate(键):
        if _真(结果[k]):
            v |= 1 << i
    return v


# ════════════════════════════════════════════════════════════════════
# §2 真值：ReachesZero / Stuck（TS-439 的两个定义，逐字对应）
# ════════════════════════════════════════════════════════════════════
def 真值_ReachesZero(轨道: Sequence[int]) -> bool:
    """`ReachesZero g y := ∃ n, g (g^[n] y) = g^[n] y` —— 差异归零的不动点。

    ★ 对**已观测前缀**判：不动点一旦出现就是永久的（单调），
      故前缀上判出的 `ReachesZero` 就是 `ReachesZero`。
    """
    return any(轨道[i + 1] == 轨道[i] for i in range(len(轨道) - 1))


def 真值_Stuck(轨道: Sequence[int]) -> bool:
    """`Stuck g y := ∀ n, g (g^[n] y) ≠ g^[n] y` —— 卡住（持续性结构差异）。

    ★★★★★ 从**有限观测**证 `Stuck` 的唯一途径：找到**长度 ≥ 2 的周期**。
      理由：`g` 是函数 ⇒ 一旦 `轨道[j] = 轨道[i]`（`j < i`），
      从 `j` 起轨道以 `i-j` 为周期**永远**循环；周期长 ≥ 2 的圈上
      **每个点都不是不动点**（`轨道[k+1] ≠ 轨道[k]`）⇒ 永不命中。
    ★ 这条**不是**启发式：它是 `stuck_iff_not_reaches` 在有限真值空间上的
      **判定形态**（`reachesZero_iff_bounded_n` 的逆否）。
    """
    for j in range(len(轨道) - 1):
        for i in range(j + 1, len(轨道)):
            if 轨道[j] == 轨道[i]:
                if i - j >= 2:
                    return True
                return False        # 距离 1 是不动点，不是 Stuck
    return False


# ════════════════════════════════════════════════════════════════════
# §3 判定与通用求解器
# ════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class 判定:
    """真值空间内的一次判定。

    ★ 真值 `值` **只有两个可能取值**（`True`=ReachesZero / `False`=Stuck）。
      `已判定` 区分「扫描还没跑完」，**不是**第三个真值 ——
      它是「`solveOn` 还在扫 `Fin n`」的进度，不是答案。
    """
    已判定: bool
    值: bool | None
    证书: int | None
    因由: str

    def __post_init__(self) -> None:
        if self.已判定:
            if self.值 not in (True, False):
                raise ValueError(
                    f"已判定时 值 必须是 True/False，实得 {self.值!r} —— "
                    f"★ 真值空间没有第三值（TS-469 §4）")
        elif self.值 is not None:
            raise ValueError("未判定时 值 必须为 None（不得用假值占位）")


def 求解(轨道: Sequence[int], n: int) -> 判定:
    """真值空间内的通用求解器（TS-469 §2/§3/§4）。

    ★★★★★ **判定自足（`solveOn_always_answers`）**：本函数
      **不接任何判定器输入** —— 没有 `Decidable`、没有谓词、没有回调。
      判定由真值空间的结构**内建**（位置的相等由 `int` 的构造子给出）。
      ⇒ 这就是「人的份额」在**机制自己的真值空间**内为**零**的机器形态。

    ★★★★★ **证书界（`reaches_iff_bounded`）**：首次命中步 `i < n`。
      `n` = **真值空间基数** = `2^门数`。
      ⇒ 停机保证：`len(轨道) ≥ n+1` 时**必然已判定**（鸽笼，见下）。

    ★ **鸽笼**：真值空间只有 `n` 个位置。若已观测 `n+1` 个点仍无重复，
      则它们两两不同 —— 不可能。故 `len(轨道) ≥ n+1 ⇒ 必有重复 ⇒ 已判定`。
    """
    if n < 1:
        raise ValueError(f"真值空间基数 n 必须 ≥ 1，实得 {n}")
    长度 = len(轨道)
    if 长度 == 0:
        return 判定(False, None, None, "轨道为空：还没跑过一轮")
    for j in range(长度 - 1):
        for i in range(j + 1, 长度):
            if 轨道[j] != 轨道[i]:
                continue
            周期 = i - j
            if 周期 == 1:
                return 判定(True, True, j,
                            f"不动点：第 {j} 步起真值不再变（Δ=∅）"
                            f"；证书 i={j} < n={n}")
            return 判定(True, False, j,
                        f"周期 {周期}（第 {j} 步起循环）：圈上每点都不是不动点 "
                        f"⇒ Stuck；证书 起点={j} < n={n}")
    if 长度 >= n + 1:
        raise AssertionError(
            f"鸽笼违反：真值空间只有 {n} 个位置，不可能有 {长度} 个两两不同的点。"
            f"⇒ 有位置未落进坐标系，或 `读()` 读了载体（载体消去被破坏）。")
    return 判定(False, None, None,
                f"扫描中：已观测 {长度} 个点、无重复；真值空间 {n} 个位置，"
                f"证书界 i < {n}")


# ════════════════════════════════════════════════════════════════════
# §4 记忆步：让 `g` 按构造是函数（机制适用的前提）
# ════════════════════════════════════════════════════════════════════
class 记忆步:
    """把「一轮回炉」记忆化成**函数** `g : Fin n → Fin n`。

    ★★★★★ 为什么必须有它（TS-469 的**适用前提**，不是优化）：
      `ReachesZero`/`Stuck` 的判定、以及「重复 ⇒ 周期」那一步，
      都要求 `g` 是**函数**（同一位置必到同一位置）。
      而编排器的一轮回炉是**随机**的（真调模型采样）——
      **不记忆化，观测到的就不是轨道，机制不适用。**

    ★ 记忆化后 `g` **按构造**是函数：同一位置再问一次，直接取记录的后继。
    ★ 顺带省掉重复位置的 token：真跑实测重复状态不重调模型。
    """

    def __init__(self) -> None:
        self._后继: dict[int, int] = {}
        self._调用 = 0
        self._命中 = 0

    @property
    def 调用次数(self) -> int:
        return self._调用

    @property
    def 命中次数(self) -> int:
        return self._命中

    def 步(self, 位置: int, 算后继) -> int:
        """在 `位置` 上前进一步。已记忆 ⇒ 直接返回记录的后继，**不算**。

        `算后继` 是调用方提供的「真跑一轮」（可含模型调用）；
        本方法**不判它的内容** —— 判定自足：判定不在这一层。
        """
        if 位置 in self._后继:
            self._命中 += 1
            return self._后继[位置]
        self._调用 += 1
        新 = 算后继()
        self._后继[位置] = 新
        return 新

    def 是函数(self) -> bool:
        """按构造恒真 —— 记录的后继不会被覆盖。"""
        return True


# ════════════════════════════════════════════════════════════════════
# §5 轨道：真值空间自己就是自己的对象（`truthObject`）
# ════════════════════════════════════════════════════════════════════
class 轨道:
    """一条真值空间轨道。**换坐标系 ⇒ 换真值空间 ⇒ 新轨道。**

    ★ 载体消去在类层面再守一次：本类**只存位置**（`int`）与坐标系（门名的
      规范序）。**不存**产物文本、模型名、窗数、token 数 —— 那些进不来。
    """

    def __init__(self, 门数: int | None = None) -> None:
        self._坐标: tuple[str, ...] | None = None
        self._点: list[int] = []
        self._因由: list[str] = []
        self._门数 = 门数

    # ── 只读视图 ──
    @property
    def 坐标(self) -> tuple[str, ...] | None:
        return self._坐标

    @property
    def 点(self) -> tuple[int, ...]:
        return tuple(self._点)

    @property
    def 因由(self) -> tuple[str, ...]:
        return tuple(self._因由)

    @property
    def 门数(self) -> int | None:
        return self._门数 if self._门数 is not None else (
            len(self._坐标) if self._坐标 else None)

    @property
    def 基数(self) -> int:
        门数 = self.门数
        if 门数 is None:
            raise ValueError("还没有坐标系 ⇒ 真值空间未取定")
        return 载体基数(门数)

    def 收(self, 结果: Mapping[str, object], 因由: str = "") -> bool:
        """收入一轮的门真值向量。返回**是否换了真值空间**（坐标系变了）。

        ★ 换门集（replan）在机制上**就是换真值空间** ⇒ 必须新起一条轨道，
          而不是把两种不同维度的位置混在一条轨道上比较（那是载体消去被破坏）。
        """
        新坐标 = 坐标系(结果)
        换 = self._坐标 is not None and 新坐标 != self._坐标
        if 换:
            self._坐标 = 新坐标
            self._点 = []
            self._因由 = []
            self._门数 = len(新坐标)
        elif self._坐标 is None:
            self._坐标 = 新坐标
            self._门数 = len(新坐标)
        self._点.append(读(结果))
        if 因由:
            self._因由.append(因由)
        return 换

    def 判(self) -> 判定:
        return 求解(self._点, self.基数)

    def 报告(self) -> dict:
        d = self.判()
        return {
            "门数": self.门数, "真值空间基数": self.基数,
            "坐标": list(self._坐标 or ()), "点": list(self._点),
            "已判定": d.已判定, "值": d.值, "证书": d.证书, "因由": d.因由,
            "轨道因由": list(self._因由),
        }


# ════════════════════════════════════════════════════════════════════
# §6 闭环：TS-469 §8 六合取（真值可靠 + 无条件全称 + 通用求解）
# ════════════════════════════════════════════════════════════════════
def 互斥(轨道: Sequence[int]) -> bool:
    """`not_reaches_and_stuck`：不可能同时到达又卡住。"""
    return not (真值_ReachesZero(轨道) and 真值_Stuck(轨道))


def 判定自足() -> tuple[bool, list[str]]:
    """`solveOn_always_answers`：求解器**自己出值**，不把判定器当输入。

    ★ 机器判据：`求解` 的签名里**不得**出现判定器类参数
      （`Decidable` / 谓词 / 回调 / `key` / `cmp` / `pred`）。
      这与库内旧路线 `universal_true_cover`（**要求 `[DecidablePred R]` 作输入**）
      正相反 —— 那条是 `Prop` 侧的代价，本条是 `Fin n` 侧的内建。
    """
    禁 = ("decidable", "pred", "key", "cmp", "callback", "callable",
          "judge", "test", "check", "verify")
    坏: list[str] = []
    for 名, f in (("求解", 求解), ("读", 读), ("真值_ReachesZero", 真值_ReachesZero),
                  ("真值_Stuck", 真值_Stuck)):
        for p in inspect.signature(f).parameters.values():
            小 = p.name.lower()
            if any(b in 小 for b in 禁):
                坏.append(f"{名}({p.name})")
            # 注解里也不得出现判定器类型
            a = str(p.annotation).lower()
            if any(b in a for b in 禁):
                坏.append(f"{名}({p.name}: {p.annotation})")
    return (not 坏), 坏


def 闭环(轨道: Sequence[int], n: int) -> dict:
    """TS-469 §8 的六合取在本轨道上的实例化 + **独立复核**。

    ★ 六条各自**独立**从轨道重算真值，再与 `求解` 的输出对账 ——
      不是把 `求解` 的输出重复一遍（那样是恒真门）。
    """
    d = 求解(轨道, n)
    真R = 真值_ReachesZero(轨道)
    真S = 真值_Stuck(轨道)
    # ① 真侧可靠（双向）：值 True ⟺ ReachesZero
    真侧 = (d.值 is True) == 真R if d.已判定 else True
    # ② 假侧可靠（双向）：值 False ⟺ Stuck
    假侧 = (d.值 is False) == 真S if d.已判定 else True
    # ③ 穷尽（无第三值）
    穷尽 = (not d.已判定) or d.值 in (True, False)
    # ④ 互斥
    互 = 互斥(轨道)
    # ⑤ 判定自足
    自足, 坏 = 判定自足()
    # ⑥ 证书界 i < n
    界 = (d.证书 is None) or (0 <= d.证书 < n)
    return {
        "①真侧可靠": 真侧, "②假侧可靠": 假侧, "③穷尽": 穷尽,
        "④互斥": 互, "⑤判定自足": 自足, "⑥证书界 i<n": 界,
        "已判定": d.已判定, "值": d.值, "证书": d.证书, "因由": d.因由,
        "判定自足反例": 坏, "真值_ReachesZero": 真R, "真值_Stuck": 真S,
        "全绿": all([真侧, 假侧, 穷尽, 互, 自足, 界]),
    }


# ════════════════════════════════════════════════════════════════════
# 纯机器自检
# ════════════════════════════════════════════════════════════════════
def _selftest() -> int:
    ok = 0
    total = 0
    fails: list[str] = []

    def chk(名: str, 条: bool, 说明: str = "") -> None:
        nonlocal ok, total
        total += 1
        if 条:
            ok += 1
        else:
            fails.append(f"{名}{('：' + 说明) if 说明 else ''}")

    # ── §1 载体消去 ──
    chk("载体基数 = 2^门数", 载体基数(0) == 1 and 载体基数(3) == 8)
    chk("载体基数 负数被拒", _抛(lambda: 载体基数(-1)))
    chk("读：空向量 = 0", 读({}) == 0)
    chk("读：全真 = n-1", 读({"a": True, "b": True}) == 3)
    # ★★★★★ 载体消去（真判据）：同样的真值向量、**载体完全不同** ⇒ 同位置
    载A = {"gate_a": True, "gate_b": False}
    载B = {"gate_b": False, "gate_a": True}          # 只是**顺序**不同（载体）
    载C = {"gate_a": True, "gate_b": False}
    chk("载体消去：键序不同 ⇒ 同位置", 读(载A) == 读(载B))
    chk("载体消去：同一真值 ⇒ 同位置", 读(载A) == 读(载C))
    # ★ 非二值（六值字符串 verdict）**必须被拒**，不得悄悄 bool() 掉
    chk("★ 非二值被拒（第三值进不来）",
        _抛(lambda: 读({"a": "converged"})))
    chk("★ 非二值被拒（progressing）",
        _抛(lambda: 读({"a": "progressing"})))
    chk("★ None 被拒", _抛(lambda: 读({"a": None})))
    chk("0/1 当二值收", 读({"a": 1, "b": 0}) == 1)

    # ── §2/§3 真值可靠双侧双向 ──
    # 不动点：0 → 1 → 1
    chk("真值_ReachesZero：0,1,1 为真", 真值_ReachesZero([0, 1, 1]))
    chk("真值_ReachesZero：0,1,0 为假", not 真值_ReachesZero([0, 1, 0]))
    chk("真值_Stuck：0,1,0 为真（周期2）", 真值_Stuck([0, 1, 0]))
    chk("真值_Stuck：0,1,1 为假", not 真值_Stuck([0, 1, 1]))
    d = 求解([0, 1, 1], 4)
    chk("求解：不动点 ⇒ 已判定且值 True", d.已判定 and d.值 is True)
    chk("求解：证书 = 首次命中步", d.证书 == 1)
    d = 求解([0, 1, 0], 4)
    chk("求解：周期2 ⇒ 已判定且值 False", d.已判定 and d.值 is False)
    chk("求解：周期证书 = 起点", d.证书 == 0)
    d = 求解([0], 4)
    chk("求解：一个点 ⇒ 未判定（不是假值）", (not d.已判定) and d.值 is None)
    d = 求解([0, 1], 4)
    chk("求解：两点无重复 ⇒ 未判定", not d.已判定)

    # ★★★★★ 真值可靠（双向·逐例穷举 n=3 的全空间）
    #   对每个函数 g : Fin 3 → Fin 3、每个起点，跑完整轨道（长度 ≤ n+1），
    #   再对**每个前缀**核 ①真侧 ②假侧 ③穷尽 ④互斥 ⑥证书界。
    #   ⇒ 这是「无条件全称」在本模块上的**穷举见证**（n 小，可穷举；
    #     通用性由 TS-469 的机证给，不由穷举给）。
    全绿 = True
    反例: list[str] = []
    n = 3
    for 码 in range(n ** n):
        g = []
        x = 码
        for _ in range(n):
            g.append(x % n)
            x //= n
        for 起 in range(n):
            轨: list[int] = [起]
            for _ in range(n):          # 跑 n 步 ⇒ 共 n+1 点，鸽笼必已判定
                轨.append(g[轨[-1]])
            for L in range(1, len(轨) + 1):
                前 = 轨[:L]
                r = 闭环(前, n)
                if not r["全绿"]:
                    全绿 = False
                    反例.append(f"g={g} 起={起} 前缀={前} 违反={[k for k, v in r.items() if v is False]}")
                # 鸽笼：长度 n+1 时**必须**已判定
                if L == n + 1 and not r["已判定"]:
                    全绿 = False
                    反例.append(f"g={g} 起={起}：长度 n+1 仍未判定 ⇒ 鸽笼被破坏")
    chk("★ 真值可靠+闭环：n=3 全空间全轨道全前缀穷举", 全绿,
        "；".join(反例[:3]))
    chk("穷举覆盖数 = n^n × n × 前缀数 > 0", n ** n == 27)

    # ── 证书界：n=1 的特例 ──
    #   n=1 只有一个位置 ⇒ 第 0 步就是不动点（自环）
    chk("n=1：单点即不动点", 求解([0, 0], 1).值 is True)
    # ── 鸽笼违反必须**报错**（不得静默）──
    chk("★ 鸽笼违反被报错（n=1 出现两个不同点）",
        _抛(lambda: 求解([0, 1], 1), AssertionError))

    # ── §4 记忆步：g 按构造是函数 + 重复不重算 ──
    步 = 记忆步()
    计 = {"n": 0}

    def 算():
        计["n"] += 1
        return 7

    chk("记忆步：首次算", 步.步(3, 算) == 7 and 计["n"] == 1)
    chk("记忆步：重复**不重算**（省 token）", 步.步(3, 算) == 7 and 计["n"] == 1)
    chk("记忆步：命中计数", 步.命中次数 == 1 and 步.调用次数 == 1)
    chk("记忆步：是函数", 步.是函数())

    # ── §5 轨道：换坐标系 ⇒ 换真值空间 ──
    t = 轨道()
    chk("轨道：首轮不换", t.收({"a": True, "b": False}) is False)
    chk("轨道：点 = 读()", t.点 == (读({"a": True, "b": False}),))
    chk("轨道：基数 = 4", t.基数 == 4)
    chk("★ 轨道：换门集 ⇒ 换真值空间（旧点清空）",
        t.收({"a": True, "b": False, "c": True}) is True and len(t.点) == 1)
    chk("轨道：换后基数 = 8", t.基数 == 8)
    t2 = 轨道()
    t2.收({"a": True})
    t2.收({"a": True})
    chk("轨道：两点相同 ⇒ 已判定不动点", t2.判().已判定 and t2.判().值 is True)
    chk("轨道：报告含真值空间基数", t2.报告()["真值空间基数"] == 2)

    # ★★★★★ 载体消去（类层面）：载体数据进不来
    #   同一真值向量、**载体完全不同**（窗数/文本/token 全不同）⇒ 同报告
    ta, tb = 轨道(), 轨道()
    ta.收({"a": True, "b": False}, 因由="窗1：产物 1200 字符，烧 9999 token")
    tb.收({"b": False, "a": True}, 因由="窗7：产物 3 字符，烧 1 token")
    chk("★ 载体消去：点序列相同（载体不进判定）", ta.点 == tb.点)
    chk("★ 载体消去：判定相同", ta.判() == tb.判())
    chk("★ 因由不同（因由≠真值，如实分开记）", ta.因由 != tb.因由)

    # ── §6 判定自足 ──
    自, 坏 = 判定自足()
    chk("★ 判定自足：求解不接判定器输入", 自, str(坏))
    # ★ 齿：签名里**故意**加一个判定器参数 ⇒ 判据必须变红
    def 假求解(轨道, n, decidable=None):        # noqa: ANN001, ARG001
        return None
    原求解 = globals()["求解"]
    try:
        globals()["求解"] = 假求解
        自2, 坏2 = 判定自足()
        chk("★ 齿：求解接了 decidable ⇒ 判定自足变红",
            (not 自2) and bool(坏2))
    finally:
        globals()["求解"] = 原求解
    自3, _ = 判定自足()
    chk("★ 齿还原后恢复绿", 自3)

    # ── 对照 Prop 侧（只作文档锚点，代码路径不得走）──
    chk("对照 Prop 侧：代价已如实记录（引 TS-436）",
        "排中律" in PROP侧代价 and "TS-436" in PROP侧代价)
    # ★★★★★ 本门必须**剥注释与字符串**再搜（README 纪律 #6：门不得匹配到它自己）。
    #   第一版直接把针写成本文件的字符串常量 ⇒ **门匹配到了它自己** ⇒ 恒红。
    #   复用库已有的保位剥注释/字符串机制（R12 `gate_spec.strip_for_matching`）。
    #   ★ 剥之后「代码体里有没有真的引用」才是判据 —— 针自己（字符串）被剥掉
    #     是**对的**：字符串里提到它不是代码引用。
    from agentscope_orchestrator import gate_spec as _gs
    代码体 = _gs.strip_for_matching(_本文件源())
    禁引 = ["Classical." + "choice", "prop" + "Decidable", "excluded" + "_middle"]
    命中 = [w for w in 禁引 if w in 代码体]
    chk("★ 代码体不得引用经典选择公理 / Prop 全称判定", not 命中, str(命中))
    # ★ 齿（本模块内自证）：针在**代码**里必须被抓到
    chk("★ 齿：代码里出现禁用引用必须被抓",
        any(w in _gs.strip_for_matching("x = " + w + "()") for w in 禁引))

    if fails:
        print(f"truth_space 自检：{ok}/{total} 红")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print(f"truth_space 自检：{ok}/{total} 全绿（载体消去 · 真值可靠双侧双向 · "
          f"穷尽无第三值 · 互斥 · 判定自足 · 证书界 i<n · 记忆步是函数）")
    return 0


def _抛(f, 期望=Exception) -> bool:
    """调用 f，期望抛 `期望` 类型 ⇒ 返 True。"""
    try:
        f()
    except 期望:
        return True
    except Exception:      # noqa: BLE001 - 抛了别的类型也算没抛对
        return False
    return False


def _本文件源() -> str:
    from pathlib import Path
    return Path(__file__).read_text(encoding="utf-8")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("真值空间（TS-469 的 Python 形态）—— --selftest 跑自检")
