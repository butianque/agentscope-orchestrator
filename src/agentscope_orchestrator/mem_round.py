"""跨轮结构记忆 —— 把「偏差密度」换成「结构指纹变没变」。

★ 它替换的是什么（先讲清，避免又长一套并行机制）
  编排器已有跨轮机制 `_hist_note`，口径是**偏差密度**（数量）。数量是统计量：
  它告诉你"还有 3 个偏差"，但**不告诉你哪个符号变了形状**，于是模型每轮
  重写整份文件（真跑里出现过 731 行被写回 1 行）。本模块补的就是这块：
  用**结构指纹**逐符号判定「新增 / 消失 / 形状已变 / 形状未变」——
  四个都是结构谓词，没有阈值、没有统计、没有计时。

★ 与 mem_symbols 的分工
  mem_symbols 建**全库**索引（O(1) 寻址、增量 ∝ Δ）；
  本模块只对**本轮目标文件**做快照与比对（不扫库，成本 ∝ 文件数）。

★ 落盘
  `<存盘目录>/结构快照.json`，内容 = {文件: {符号名: 指纹}}。
  同一输入必得同一快照（指纹来自 mem_code 的确定性结构流）⇒ 可校验、可恢复。
  **不进主树**（默认落 `.data/结构记忆/`），避免污染引擎索引。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .mem_code import 代码结构
from .mem_symbols import _语言后缀, 抽取符号

快照名 = "结构快照.json"


def _语言(文件: str) -> str:
    return _语言后缀.get(Path(文件).suffix, "generic")


def 文件快照(路径: str | Path) -> dict[str, tuple[str, str]]:
    """单个文件 → {符号名: (结构指纹 16, 内容哈希 12)}。

    ★ 为什么必须**两个**（实测教训，2026-10-30）
      只存结构指纹时，`return x + 1` → `return x + 2` 判为"形状未变"
      —— 因为数字在结构层同色（这正是"改名/换字面量不变"的设计，见 mem_code）。
      但**修复恰是改常量**时，"形状未变"会让"不要重写"的指引把模型带偏。
      ⇒ 结构指纹管**形状**，内容哈希管**这处文字动没动**。两者分工，互不替代。
    """
    路径 = Path(路径)
    try:
        文本 = 路径.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    语言 = _语言(str(路径))
    出: dict[str, tuple[str, str]] = {}
    for 名, _种类, _起行, 体 in 抽取符号(文本, 语言):
        出[名] = (代码结构(体, 语言).指纹()[:16],
                  hashlib.sha256(体.encode("utf-8", "replace")).hexdigest()[:12])
    return 出


def 变化行(旧: dict[str, tuple[str, str]], 新: dict[str, tuple[str, str]]) -> list[str]:
    """结构比对：五类判定，形状用结构指纹、内容用哈希。"""
    if 旧 == 新:
        return []
    行: list[str] = []
    for 名 in sorted(旧.keys() - 新.keys()):
        行.append(f"  · 符号 {名}：**消失**（上轮在，本轮源码里没了）")
    for 名 in sorted(新.keys() - 旧.keys()):
        行.append(f"  · 符号 {名}：**新增**（本轮源码里出现了）")
    形状变: list[str] = []
    仅内容变: list[str] = []
    未变: list[str] = []
    for 名 in sorted(旧.keys() & 新.keys()):
        旧形, 旧容 = 旧[名]
        新形, 新容 = 新[名]
        if 旧形 != 新形:
            形状变.append(f"  · 符号 {名}：**形状已变**（{旧形} → {新形}）")
        elif 旧容 != 新容:
            仅内容变.append(
                f"  · 符号 {名}：形状未变、**内容已变**（{旧容} → {新容}）"
                "——只改这一处的字面量/标识符，**不要重写整块**")
        else:
            未变.append(名)
    行.extend(形状变)
    行.extend(仅内容变)
    if 未变:
        行.append(f"  · 形状与内容都未变：{'、'.join(未变)}")
    return 行


class 跨轮记忆:
    """目标文件的结构快照 + 与上一轮的判定。"""

    def __init__(self, 存盘目录: str | Path | None = None) -> None:
        self.存盘目录 = Path(存盘目录) if 存盘目录 else None
        self._盘: dict[str, dict[str, str]] = {}
        self._载()

    def _文件(self) -> Path | None:
        return (self.存盘目录 / 快照名) if self.存盘目录 else None

    def _载(self) -> None:
        文件 = self._文件()
        if 文件 is None or not 文件.exists():
            return
        try:
            self._盘 = json.loads(文件.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._盘 = {}

    def _存(self) -> None:
        文件 = self._文件()
        if 文件 is None:
            return
        try:
            文件.parent.mkdir(parents=True, exist_ok=True)
            文件.write_text(json.dumps(self._盘, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def 判定(self, 根: str | Path, 文件们: list[str]) -> str:
        """返回给人/模型看的结构比对块；同时把本轮快照落盘（供下一轮比）。"""
        根 = Path(根)
        块: list[str] = []
        for 相对 in 文件们:
            甲 = str(相对).replace("\\", "/")
            新 = 文件快照(根 / 甲)
            旧 = self._盘.get(甲)
            if 旧 is None:
                块.append(f"【{甲}】首次记录（{len(新)} 个符号）；下一轮起可判形状是否变化")
            else:
                行 = 变化行(旧, 新)
                块.append(f"【{甲}】\n" + ("\n".join(行) if 行 else "  · 逐符号形状与上轮**完全一致**"))
            self._盘[甲] = 新
        self._存()
        return "\n".join(块)


def 记要(根: str | Path, 文件们: list[str], 存盘目录: str | Path | None = None) -> str:
    """一步到位：判定 + 落盘 + 给模型的可执行指引。异常由调用方降级。"""
    记忆 = 跨轮记忆(存盘目录)
    正文 = 记忆.判定(根, 文件们)
    if not 正文:
        return ""
    return (
        "\n【跨轮结构比对（机器判定，不是模型自述）】\n" + 正文 + "\n"
        "★ 读法：形状**未变**的符号不要重写（重写就是白烧 token 且容易截断）；\n"
        "  只改**形状已变**、**新增**、以及红门指到的部分。\n"
        "★ 若某个符号你打算删掉，那会记为「消失」——请确认这是红门要求，不是你图省事。\n"
    )


# ════════════════════════════════════════════════════════════
# 自检：四类变化各判一次，全部结构谓词
# ════════════════════════════════════════════════════════════

def _自检() -> int:
    import tempfile

    失败: list[str] = []
    源0 = "def 甲(x):\n    return x + 1\n\ndef 乙(x):\n    return 甲(x)\n"

    with tempfile.TemporaryDirectory() as td:
        根 = Path(td)
        目标 = 根 / "a.py"
        目标.write_text(源0, encoding="utf-8")

        记忆 = 跨轮记忆(根 / ".mem")
        首 = 记忆.判定(根, ["a.py"])
        判据首 = "首次记录" in 首
        if not 判据首:
            失败.append(f"首轮应记「首次记录」：{首}")

        同 = 记忆.判定(根, ["a.py"])
        if "完全一致" not in 同:
            失败.append(f"内容不变应判「完全一致」：{同}")

        # 形状已变：改函数体（增删 token）
        目标.write_text("def 甲(x):\n    return x + 2 * 3\n\ndef 乙(x):\n    return 甲(x)\n",
                        encoding="utf-8")
        变 = 记忆.判定(根, ["a.py"])
        if "形状已变" not in 变 or "甲" not in 变:
            失败.append(f"改函数体应判「形状已变 · 甲」：{变}")
        if "形状与内容都未变" not in 变 or "乙" not in 变:
            失败.append(f"未改的 乙 应判「都未变」：{变}")

        # ★ 只改字面量：形状未变、内容已变（必须与"都未变"分开）
        目标.write_text("def 甲(x):\n    return x + 9 * 3\n\ndef 乙(x):\n    return 甲(x)\n",
                        encoding="utf-8")
        仅 = 记忆.判定(根, ["a.py"])
        if "内容已变" not in 仅 or "形状已变" in 仅:
            失败.append(f"只改字面量应判「形状未变·内容已变」：{仅}")

        # 消失 / 新增
        目标.write_text("def 甲(x):\n    return x + 2 * 3\n", encoding="utf-8")
        删 = 记忆.判定(根, ["a.py"])
        if "消失" not in 删 or "乙" not in 删:
            失败.append(f"删函数应判「消失 · 乙」：{删}")
        目标.write_text(源0 + "\ndef 丙(y):\n    return y\n", encoding="utf-8")
        增 = 记忆.判定(根, ["a.py"])
        if "新增" not in 增 or "丙" not in 增:
            失败.append(f"加函数应判「新增 · 丙」：{增}")

        # 改名 ⇒ 消失 + 新增（身份变了，这是正确行为，不是漏判）
        目标.write_text(源0.replace("甲", "戊"), encoding="utf-8")
        改 = 记忆.判定(根, ["a.py"])
        if "消失" not in 改 or "新增" not in 改:
            失败.append(f"改名应同时报消失与新增：{改}")

        # 确定性：同内容两次快照逐位相同
        目标.write_text(源0, encoding="utf-8")
        if 文件快照(目标) != 文件快照(目标):
            失败.append("快照不确定")

        # 留痕：存盘文件真的写了
        if not (根 / ".mem" / 快照名).exists():
            失败.append("快照未落盘")

    for f in 失败:
        print(f"  ✗ {f}")
    if not 失败:
        print("  ✓ 跨轮结构记忆自检全绿（首次/未变/形状变/消失/新增/改名/确定性/落盘）")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(_自检())
