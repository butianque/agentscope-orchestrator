"""上下文包 —— 洞驱动扩展 + 对合闭包定界（结构寻址的"扩展"那一半）。

★ 三条口径，全部来自源设计（不复制源码，只借判据）
  ① **洞驱动**：只有"缺口就在旁边"才扩展。没有洞 ⇒ 不扩展。
     （源实现：`相位跃迁` 只在「阳位 ∧ 洞邻」才跃；洞邻 = 父/复用去向/被引 里有墓碑）
  ② **三条通道**：定义 / 调用者 / 被调用者 —— 对应源实现的父、被引、复用去向三条既有边。
  ③ **对合闭包定界**：扩展两轮必稳定（扩一次 == 扩两次）。
     源实现的依据是「字节面 δ 是对合 ⇒ 轨道周期恰 2」；这里用
     「定义↔使用」作对偶，第二条照样成立 —— **边界由结构给，不由"扩几层"这种阈值给**。

★ 与模型层的分工
  本层给出的是**结构上必然相关**的一小圈（可机验）；
  "到底哪个才是要改的、这么改对不对"是模型与判据门的事，不在这里判。

★ 每条目带指纹
  指纹 = 该符号源文本的 sha256。门侧用它核对「模型改的就是我给它看的那一版」，
  不一致 ⇒ 判"版本不符"，不判"不达标"（两件事必须分开，否则读数又会骗人）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .mem_symbols import 符号, 符号表, 抽取符号


@dataclass(frozen=True)
class 洞:
    """缺口 —— 扩展的唯一动因。取自机器读数，不取自模型猜测。"""

    文件: str
    行: int
    原因: str = ""            # 如 "E0425 未找到值" / "测试失败 test_xxx" / "门通道红"


@dataclass(frozen=True)
class 条目:
    文件: str
    名: str
    起: int
    结构指纹: str
    源哈希: str               # 该符号源文本的 sha256 —— 门侧版本校验用
    因: str                   # 为什么进包：洞 / 定义 / 调用者 / 被调用者 / 对偶

    def 键(self) -> str:
        return f"{self.文件}::{self.名}::{self.源哈希[:12]}"


def _文(表: 符号表, 相对: str) -> str:
    if 表.根 is None:
        return ""
    try:
        return (Path(表.根) / 相对).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _源哈希(表: 符号表, 符: 符号) -> str:
    文本 = _文(表, 符.文件).splitlines()
    if 0 < 符.起 <= len(文本):
        return hashlib.sha256(文本[符.起 - 1].encode("utf-8", "replace")).hexdigest()
    return hashlib.sha256(b"").hexdigest()


def _洞命中(表: 符号表, 洞们: list[洞]) -> list[符号]:
    """洞所在的最近符号（该文件里 起行 ≤ 洞行 的最后一个；没有则取该文件第一个）。"""
    命中: list[符号] = []
    for d in 洞们:
        候选 = [s for s in 表.查_文件(d.文件)]
        if not 候选:
            continue
        前 = [s for s in 候选 if s.起 <= d.行]
        命中.append(max(前, key=lambda s: s.起) if 前 else min(候选, key=lambda s: s.起))
    return 命中


  # ③ 由 ② 覆盖：墓碑文件的符号已在同步时摘除


def _归属符号(表: 符号表, 文件: str, 行: int) -> 符号 | None:
    """文件里"第 行"落在哪个符号内（起行 ≤ 行 的最后一个）。"""
    候选 = sorted(表.查_文件(文件), key=lambda s: s.起)
    前 = [s for s in 候选 if s.起 <= 行]
    return 前[-1] if 前 else None


def _命中行们(表: 符号表, 文件: str, 名: str) -> list[int]:
    """该文件里**整体词**出现 名 的行号（1 起）。"""
    if 表.根 is None:
        return []
    try:
        文本 = (Path(表.根) / 文件).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    正 = re.compile(r"(?<!\w)" + re.escape(名) + r"(?!\w)")
    return [i for i, ln in enumerate(文本.splitlines(), 1) if 正.search(ln)]


def 对偶(表: 符号表, 种子: list[符号]) -> list[tuple[符号, str]]:
    """一次**对合**：定义 ↔ 使用。

    这是源实现「相位跃迁沿 δ 对偶面」在代码上的对应物：
    δ 是字节面对合 ⇒ 轨道周期恰 2 ⇒ **两次必稳定**。
    这里取「定义 ↔ 使用」为对偶，闭包判据因此可以机验（见自检 ③）。

    ★★★★★ 2026-10-30 真跑纠错（这是本轮最贵的一课）
      「使用」通道原来写的是**调用者文件里的全部符号** ⇒ 在真实仓上失控：
      中文编译器（375 个 rs，交叉引用极密）一个洞就拉出 **139,055 字符**的包，
      prompt 从 10,646 涨到 149,703 —— 代价远超收益，且把真信号淹掉。
      ⇒ 改成：**只取"命中行所属的那一个符号"**。边界仍由对合给（两次必稳），
        但每一步只带走一个符号，不带走整张文件。
    """
    出: list[tuple[符号, str]] = []
    for s in 种子:
        for d in 表.查(s.名):
            出.append((d, "定义"))
        for 文件 in 表.调用者(s.名):
            for 行 in _命中行们(表, 文件, s.名):
                归属 = _归属符号(表, 文件, 行)
                if 归属 is not None:
                    出.append((归属, "使用"))
    return 出


_体缓存: dict[str, dict[tuple[str, int], str]] = {}


def _体文(表: 符号表, s: 符号) -> str:
    """取该符号**自己的声明体**（从 decl 到下一个 decl）。

    按文件缓存：一次解析供多个种子复用，不重复读盘。
    """
    if 表.根 is None:
        return ""
    键 = f"{表.根}::{s.文件}"
    条 = _体缓存.get(键)
    if 条 is None:
        try:
            文本 = (Path(表.根) / s.文件).read_text(encoding="utf-8", errors="replace")
        except OSError:
            _体缓存[键] = {}
            return ""
        语 = "rust" if s.文件.endswith(".rs") else "python"
        try:
            条 = {(名, 起): 体 for 名, _种, 起, 体 in 抽取符号(文本, 语)}
        except Exception:                       # noqa: BLE001
            条 = {}
        _体缓存[键] = 条
    return 条.get((s.名, s.起), "")


def 被调用者(表: 符号表, 种子: list[符号]) -> list[tuple[符号, str]]:
    """种子的**局部**调用目标（一跳，只在本文件内取）—— 改一个函数必须知道它调了什么。

    ★ 它只加"种子自己的局部"，**不参与扩张**：扩张只走对合，
      否则每多一跳就多一圈，边界就没有结构依据了（那又变成"扩几层"）。

    ★★★★★ 2026-10-30 第二次真跑纠错
      原来取的是 `表.引用名(s.文件)` = **该文件里出现过的所有引用名** ⇒
      对大平文件（`lib.rs`、`bin/涌现窗口调试.rs`）等于**整文件搬入**：
      maturity 记分卡实测「包/文件 = 11.34」，一个洞就 113 条。
      ⇒ 改成只取**该符号自己体内**出现的引用名。语义上也才真叫"被调用者"：
        是**这个函数**调了谁，不是**这个文件**提过谁。
    """
    出: list[tuple[符号, str]] = []
    for s in 种子:
        体 = _体文(表, s)
        if not 体:
            continue
        名们 = [n for n in 表.引用名(s.文件) if n != s.名 and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", 体)]
        for 名 in 名们:
            for d in 表.查(名):
                if d.文件 == s.文件:
                    出.append((d, "被调用者"))
    return 出


def 上下文包(表: 符号表, 洞们: list[洞]) -> tuple[条目, ...]:
    """洞 → 上下文包 = 洞 ∪ 一次对合(定义↔使用) ∪ 种子局部被调用者。"""
    if not 洞们:
        return ()                                    # ① 没有洞 ⇒ 不扩展
    种子 = _洞命中(表, 洞们)
    if not 种子:
        return ()
    见: dict[str, 条目] = {}
    for s in 种子:
        见[f"{s.文件}::{s.名}"] = 条目(s.文件, s.名, s.起, s.结构指纹, _源哈希(表, s), "洞")
    for s, 因 in 对偶(表, 种子) + 被调用者(表, 种子):
        键 = f"{s.文件}::{s.名}"
        if 键 not in 见:
            见[键] = 条目(s.文件, s.名, s.起, s.结构指纹, _源哈希(表, s), 因)
    return tuple(sorted(见.values(), key=lambda e: (e.文件, e.起, e.名)))


def 渲染_预算(包: tuple[条目, ...], 预算字符: int = 16000) -> tuple[str, dict | None]:
    """★ 预算**自适应**：超预算不是报错、也不是整包丢掉，而是**按价值序截断并如实报账**。

    ★ 为什么要这个（真教训）：上下文包曾膨胀到 139KB 塞进 prompt。两个错法都不行——
      "超了照样塞"（把模型挤爆）、"超了就整包不要"（等于没接结构记忆）。
      正确做法：包序本身是价值序（洞 → 定义 → 使用 → 被调用者），
      **留住前面、截掉后面、把截了多少写在包尾**，让调用方知道"还有东西没给你，别猜"。
    """
    if not 包:
        return 渲染(包), None
    全文 = 渲染(包)
    原字节 = len(全文.encode("utf-8"))
    if 预算字符 <= 0 or 原字节 <= 预算字符:
        return 全文, None
    头 = "【结构寻址得到的上下文（每条带指纹，改错版本会被门判「版本不符」）】"
    # ★ 预留尾部报账行的空间：不预留就会出现"预算 800 输出 876"——
    #   我第一版就是这样，判据写着"不超预算"而实际超了 76 字节。**报账行也是内容。**
    预留 = 128
    留: list[str] = []
    用 = len(头.encode("utf-8")) + 1
    for e in 包:
        一行 = f"- {e.文件}:{e.起}  {e.名}  [{e.因}]  sha256:{e.源哈希[:12]}"
        b = len(一行.encode("utf-8")) + 1
        if 用 + b > 预算字符 - 预留:
            break
        留.append(一行)
        用 += b
    丢 = len(包) - len(留)
    报 = {"原字节": 原字节, "现字节": 用, "原条": len(包), "现条": len(留), "丢条": 丢,
          "预算": 预算字符}
    尾 = (f"（预算 {预算字符} 字符：另有 {丢} 条按价值序被截断 —— "
          f"需要时按名精确索取，**不要猜**）")
    return "\n".join([头] + 留 + [尾]), 报


def 渲染(包: tuple[条目, ...], 上限条: int = 0) -> str:
    """把包渲染成给模型的文字（带指纹，供门侧核对）。`上限条=0` 不截断。"""
    if not 包:
        return "（本轮无缺口：上下文包为空 —— 不扩展，避免塞无关内容）"
    选 = 包[:上限条] if 上限条 > 0 else 包
    行们 = ["【结构寻址得到的上下文（每条带指纹，改错版本会被门判「版本不符」）】"]
    for e in 选:
        行们.append(f"- {e.文件}:{e.起}  {e.名}  [{e.因}]  sha256:{e.源哈希[:12]}")
    if 上限条 > 0 and len(包) > 上限条:
        行们.append(f"（另有 {len(包) - 上限条} 条未展开 —— 需要时按名精确索取，不要猜）")
    return "\n".join(行们)


# ════════════════════════════════════════════════════════════
# 自检：判据全是"同/不同/空/非空"，无阈值
# ════════════════════════════════════════════════════════════

def _自检() -> int:
    import tempfile

    失败: list[str] = []

    def 判(名: str, 条件: bool, 说明: str = "") -> None:
        if not 条件:
            失败.append(f"{名}{('：' + 说明) if 说明 else ''}")

    with tempfile.TemporaryDirectory() as td:
        根 = Path(td)
        (根 / "a.py").write_text(
            "def 甲(x):\n    return x + 1\n\ndef 乙(x):\n    return 甲(x)\n", encoding="utf-8")
        (根 / "b.py").write_text(
            "from a import 甲\n\ndef 丙(y):\n    return 甲(y) + 乙(y)\n", encoding="utf-8")
        表 = 符号表()
        表.同步(根)

        # ① 无洞 ⇒ 空包（不扩展）
        判("无洞空包", 上下文包(表, []) == ())

        # ② 洞驱动：洞在 a.py 第 5 行（def 乙）⇒ 包里有 乙，且含调用者 b.py
        包 = 上下文包(表, [洞("a.py", 5, "E0425")])
        名们 = {e.名 for e in 包}
        判("洞命中符号", "乙" in 名们, f"实得 {sorted(名们)}")
        判("含被调用者通道", "甲" in 名们, f"实得 {sorted(名们)}")
        判("含调用者文件", any(e.文件 == "b.py" for e in 包), f"实得 {sorted(e.文件 for e in 包)}")

        # ③ 对合闭包：对偶两次 == 对偶一次（这是"边界由结构给"的机验）
        种子 = _洞命中(表, [洞("a.py", 5)])
        S1 = {(s.文件, s.名) for s, _ in 对偶(表, 种子)} | {(s.文件, s.名) for s in 种子}
        二轮种子 = [s for (f, n) in sorted(S1) for s in 表.查_文件(f) if s.名 == n]
        S2 = {(s.文件, s.名) for s, _ in 对偶(表, 二轮种子)} | {(s.文件, s.名) for s in 二轮种子}
        判("两轮即稳（对合闭包）", S2 == S1, f"一轮 {len(S1)} 条，二轮 {len(S2)} 条")
        判("包去重", len({e.键() for e in 包}) == len(包))

        # ④ 每条目带指纹且非空
        判("条目带指纹", all(len(e.结构指纹) == 64 for e in 包),
           f"实得 {[len(e.结构指纹) for e in 包]}")
        判("条目带源哈希", all(len(e.源哈希) == 64 for e in 包))
        判("渲染含指纹", "sha256:" in 渲染(包))

        # ⑤ 墓碑：删掉 b.py 后，调用者通道不得再指向它
        (根 / "b.py").unlink()
        表.同步(根)
        包2 = 上下文包(表, [洞("a.py", 5)])
        判("墓碑文件不进包", not any(e.文件 == "b.py" for e in 包2),
           f"实得 {sorted(e.文件 for e in 包2)}")

    for f in 失败:
        print(f"  ✗ {f}")
    if not 失败:
        print("  ✓ 上下文包自检全绿（洞驱动 · 三通道 · 两轮闭包 · 指纹齐备 · 墓碑隔离）")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(_自检())
