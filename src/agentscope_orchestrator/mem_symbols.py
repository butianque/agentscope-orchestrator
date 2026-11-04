"""符号表 —— O(1) 寻址 + 墓碑 + 指纹（结构寻址的"准"那一半）。

★ 它管什么、不管什么（与模型层的边界）
  管：**结构上必然相关**的东西 —— 这个名字定义在哪、谁引用了它、
      它现在的结构指纹是什么、哪些符号已经死了（墓碑）。
  不管：**哪些东西"有关"**（相关性、语义）—— 那需要常识与代码知识，交给模型（API）。
  本层零常识、零依赖、零阈值。

★ 三条不变量（都写成了自检）
  ① 查在 O(1)：建表后查询不读盘（自检用读盘计数证明，不用计时）；
  ② 成本 ∝ Δ：第二次同步无改动 ⇒ 重算 0、重扫描 0；
  ③ 墓碑不进活集：删掉的文件/符号标墓碑，查活集不得返回它。

★ 口径来源
  「字表只增」「墓碑」「出生或复用」的口径来自 示例工程 数据感知记忆网络
  （`结构记忆网络.rs` / 交接_总览 的五条目标 ③与⑤），此处按其判据重写，不复制源码。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .mem_code import 代码结构


def 枚举源文件(根: str | Path, 后缀: tuple[str, ...] = (".py", ".rs", ".lean"),
               跳过: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
               glob阶: str = "**/*"):
    """按后缀枚举源文件，**可剪枝**。

    ★ 为什么需要（2026-10-31 交付审计）：`同步()` 原先只有 `sorted(根.glob(glob阶))`
      —— 它先把**整棵树**物化再按后缀过滤。在真实大仓上（`target/`、备份目录、
      `.tmp/` 各含上万文件）光枚举就几分钟，`maturity` 的 D/F 两条判据因此直接超时。
      剪枝走 `os.walk`，被跳过的目录**根本不进**枚举。
    跳过的匹配：目录名 == 条目，或目录名以条目开头（项目档案里 `target_` 就是前缀式写法）。
    """
    根 = Path(根)
    跳 = {str(s).replace("\\", "/").strip("/") for s in (跳过 or ()) if str(s).strip()}
    跳后缀 = {s if s.startswith(".") else "." + s for s in 后缀}
    if not 跳 or glob阶 != "**/*":
        for p in sorted(根.glob(glob阶)):
            if p.is_file() and p.suffix in 跳后缀:
                yield p
        return

    def _该跳(名: str) -> bool:
        return any(名 == s or 名.startswith(s) for s in 跳)

    for dirpath, dirnames, filenames in os.walk(根):
        dirnames[:] = sorted(d for d in dirnames if not _该跳(d))
        for fn in sorted(filenames):
            if Path(fn).suffix in 跳后缀:
                yield Path(dirpath) / fn

# 定义抽取：行首声明（语言无关写法，各自一段）
_定义正则: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^[ \t]*(?:async[ \t]+)?(?P<kind>def|class)[ \t]+(?P<name>\w+)", re.M),
    "rust": re.compile(
        r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?(?:unsafe[ \t]+)?"
        r"(?P<kind>fn|struct|enum|trait|mod|const|static|type)[ \t]+(?P<name>\w+)",
        re.M),
    "lean": re.compile(
        r"^[ \t]*(?:@\[[^\]]*\][ \t]*)?(?P<kind>theorem|lemma|def|structure|class|inductive|"
        r"axiom|instance)[ \t]+(?P<name>[\w\u4e00-\u9fff'.]+)",
        re.M),
}
_标识符 = re.compile(r"[^\W\d]\w*", re.U)
_语言后缀 = {".py": "python", ".rs": "rust", ".lean": "lean",
             ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript",
             ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript"}


@dataclass(frozen=True)
class 符号:
    名: str
    种类: str
    文件: str
    起: int                 # 1 起
    结构指纹: str           # mem_code 口径（改名不变、改结构变）

    def 身份(self) -> str:
        """身份 = 名 + 文件 + 结构指纹（不是行号：行号会随编辑漂移）。"""
        return f"{self.文件}::{self.名}::{self.结构指纹[:16]}"


@dataclass
class 同步读数:
    扫描文件: int = 0
    变更文件: int = 0
    重算符号: int = 0
    读盘次数: int = 0
    新增墓碑: int = 0

    def 行(self) -> str:
        return (f"扫描 {self.扫描文件} · 变更 {self.变更文件} · "
                f"重算符号 {self.重算符号} · 读盘 {self.读盘次数} · 新增墓碑 {self.新增墓碑}")


def 抽取符号(文本: str, 语言: str) -> list[tuple[str, str, int, str]]:
    """→ [(名, 种类, 起行 1 起, 体文)]。

    ★ 体文 = 本声明起、到**下一个声明**之间（含定义行、含函数体）。
      实测教训：早先只对"定义行"取指纹 ⇒ **改函数体测不出来**（形状仍判"未变"），
      跨轮记忆就变成了摆设。改体文后，改函数体必然判「形状已变」。
    ★ v1 限制：嵌套定义会被按"下一个声明"切开，方法体的切片偏粗 —— 已登记。
    """
    规则 = _定义正则.get(语言)
    if 规则 is None:
        return []
    行们 = 文本.splitlines(keepends=True)
    点: list[tuple[int, str, str]] = []
    for m in 规则.finditer(文本):
        名 = m.group("name").split(".")[0]
        if not 名:
            continue
        点.append((文本.count("\n", 0, m.start("name")) + 1, 名, m.group("kind")))
    出: list[tuple[str, str, int, str]] = []
    for i, (起行, 名, 种类) in enumerate(点):
        下一 = 点[i + 1][0] if i + 1 < len(点) else len(行们) + 1
        出.append((名, 种类, 起行, "".join(行们[起行 - 1:下一 - 1])))
    return 出


class 符号表:
    """活符号 + 墓碑 + 引用索引；全部增量维护。"""

    def __init__(self) -> None:
        self._活: dict[str, list[符号]] = {}          # 名 → 符号们（可能重名）
        self._引: dict[str, set[str]] = {}            # 名 → 引用它的文件们
        self._文件名: dict[str, set[str]] = {}        # 文件 → 它引用的名字们（反向索引）
        self._文哈希: dict[str, str] = {}             # 文件 → sha256
        self._文符号: dict[str, list[str]] = {}       # 文件 → 它定义的符号名
        self._墓碑: set[str] = set()                  # 文件（已删/不可读）
        self._读盘 = 0
        self.根: Path | None = None

    # ── 同步（增量：只碰哈希变了的文件）───────────────────────
    def 同步(self, 根: str | Path, 后缀: tuple[str, ...] = (".py", ".rs", ".lean"),
             glob阶: str = "**/*", 跳过: frozenset[str] | set[str] | tuple[str, ...] = frozenset()
             ) -> 同步读数:
        根 = Path(根)
        self.根 = 根
        读 = 同步读数()
        现存: set[str] = set()
        for 路径 in 枚举源文件(根, 后缀, 跳过, glob阶):
            相对 = str(路径.relative_to(根)).replace("\\", "/")
            现存.add(相对)
            读.扫描文件 += 1
            文本 = self._读(路径)
            if 文本 is None:
                continue
            读.读盘次数 += 1
            哈 = hashlib.sha256(文本.encode("utf-8", "replace")).hexdigest()
            if self._文哈希.get(相对) == 哈:
                continue                                    # ② 未变 ⇒ 一律不动
            读.变更文件 += 1
            self._文哈希[相对] = 哈
            self._墓碑.discard(相对)
            self._重算(相对, 文本)
        # 墓碑：文件消失 ⇒ 标墓碑，并从活集摘除
        for 相对 in list(self._文哈希):
            if 相对 not in 现存:
                读.新增墓碑 += 1
                self._标墓碑(相对)
        return 读

    def _读(self, 路径: Path) -> str | None:
        # ★ 唯一的读盘点，也是**唯一**该自增计数器的地方。
        #   原来这个计数器建了却从没自增 ⇒ 「查询零读盘」那条判据恒真
        #   （拿一个没人碰的快照比自己）。计数器是死的时候，
        #   任何"零变化"的断言都不证明任何事。
        self._读盘 += 1
        try:
            return 路径.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def 读盘次数(self) -> int:
        """本表自建立以来**真的**读了几次盘 —— 给"查询零读盘"当读数用。"""
        return self._读盘

    def _重算(self, 相对: str, 文本: str) -> None:
        for 名 in self._文符号.get(相对, []):
            self._活[名] = [s for s in self._活.get(名, []) if s.文件 != 相对]
            if not self._活[名]:
                del self._活[名]
        语言 = _语言后缀.get(Path(相对).suffix, "generic")
        名们: list[str] = []
        for 名, 种类, 起行, 体 in 抽取符号(文本, 语言):
            符 = 符号(名, 种类, 相对, 起行, 代码结构(体, 语言).指纹())
            self._活.setdefault(名, []).append(符)
            名们.append(名)
        self._文符号[相对] = 名们
        # 引用索引：文件里出现过的所有标识符（除它自己的定义行不做区分，宁多不漏）
        self._文件名[相对] = set(_标识符.findall(文本))
        引名 = set(_标识符.findall(文本))
        for 名 in list(self._引):
            if 相对 in self._引[名]:
                self._引[名].discard(相对)
                if not self._引[名]:
                    del self._引[名]
        for 名 in 引名:
            self._引.setdefault(名, set()).add(相对)

    def _标墓碑(self, 相对: str) -> None:
        for 名 in self._文符号.pop(相对, []):
            活 = [s for s in self._活.get(名, []) if s.文件 != 相对]
            if 活:
                self._活[名] = 活
            else:
                self._活.pop(名, None)
        self._文哈希.pop(相对, None)
        self._文件名.pop(相对, None)
        for 名 in list(self._引):
            self._引[名].discard(相对)
            if not self._引[名]:
                del self._引[名]
        self._墓碑.add(相对)

    # ── 查询（① 不读盘）────────────────────────────────────
    def 查(self, 名: str) -> tuple[符号, ...]:
        return tuple(self._活.get(名, ()))

    def 查_文件(self, 文件: str) -> tuple[符号, ...]:
        """某文件里定义的活符号（墓碑文件返回空）。"""
        return tuple(s for 名 in self._文符号.get(文件, ())
                     for s in self._活.get(名, ()) if s.文件 == 文件)

    def 调用者(self, 名: str, 排除自身: bool = True) -> tuple[str, ...]:
        定义文件 = {s.文件 for s in self.查(名)}
        引用 = set(self._引.get(名, set()))
        if 排除自身:
            引用 -= 定义文件
        return tuple(sorted(引用))

    def 墓碑们(self) -> tuple[str, ...]:
        return tuple(sorted(self._墓碑))

    def 引用名(self, 文件: str) -> tuple[str, ...]:
        """该文件里出现过的标识符（反向索引；供"被调用者"通道用）。"""
        return tuple(sorted(self._文件名.get(文件, ())))

    def 规模(self) -> dict[str, int]:
        return {"符号名": len(self._活),
                "符号条": sum(len(v) for v in self._活.values()),
                "引用名": len(self._引),
                "文件": len(self._文哈希),
                "墓碑": len(self._墓碑)}


# ════════════════════════════════════════════════════════════
# 自检：三条不变量，全部结构性（无计时断言）
# ════════════════════════════════════════════════════════════

def _自检() -> int:
    import tempfile

    失败: list[str] = []

    def 判(名: str, 条件: bool, 说明: str = "") -> None:
        if not 条件:
            失败.append(f"{名}{('：' + 说明) if 说明 else ''}")

    with tempfile.TemporaryDirectory() as td:
        根 = Path(td)
        (根 / "a.py").write_text("def 甲(x):\n    return x + 1\n\ndef 乙(x):\n    return 甲(x)\n",
                                encoding="utf-8")
        (根 / "b.py").write_text("from a import 甲\n\ndef 丙(y):\n    return 甲(y)\n",
                                encoding="utf-8")
        表 = 符号表()
        读1 = 表.同步(根)
        判("首次同步有变更", 读1.变更文件 == 2, 读1.行())
        判("能查到符号", bool(表.查("甲")), f"规模 {表.规模()}")
        判("跨文件调用者", set(表.调用者("甲")) == {"b.py"}, f"实得 {表.调用者('甲')}")

        # ① O(1)：查询不读盘（读盘计数是**表级**结构量，不是计时）
        #
        # ★ 这里原来是一条**恒真门**：`前 = 读1.读盘次数` 之后拿
        #   `读1.读盘次数 == 前` 当判据 —— 而 `读1` 是同步返回的快照，
        #   中间没人碰它 ⇒ 查询读不读盘它都不会变，**永远绿**。
        #   现在改成读**表自己的**计数器、取查询前后的差；并且紧跟一条
        #   **正控**：跑一次同步，读数必须变大 —— 不然计数器是死的，
        #   那条 0 差就什么都证明不了。
        前 = 表.读盘次数()
        表.查("甲")
        表.调用者("甲")
        判("查询零读盘", 表.读盘次数() == 前,
           f"{前} → {表.读盘次数()}（查询把盘读了，O(1) 不再成立）")
        表.同步(根)
        判("读盘计数是活的（正控）", 表.读盘次数() > 前,
           f"同步后仍是 {表.读盘次数()}（= 查询前 {前}）⇒ 计数器没接线，"
           f"上面那条「查询零读盘」等于没判")

        # ② 成本 ∝ Δ：无改动 ⇒ 变更 0、重算 0
        读2 = 表.同步(根)
        判("无改动零重算", 读2.变更文件 == 0 and 读2.重算符号 == 0, 读2.行())

        # ②′ 只改一个文件 ⇒ 变更恰为 1
        (根 / "b.py").write_text("from a import 甲\n\ndef 丙(y):\n    return 甲(y) + 1\n",
                                 encoding="utf-8")
        读3 = 表.同步(根)
        判("改一个文件只动一个", 读3.变更文件 == 1, 读3.行())

        # ③ 墓碑：删文件 ⇒ 它的独占符号进墓碑，不进活集
        (根 / "b.py").unlink()
        读4 = 表.同步(根)
        判("删文件记墓碑", "b.py" in 表.墓碑们(), f"墓碑 {表.墓碑们()}")
        判("墓碑符号不在活集", not 表.查("丙"), f"实得 {表.查('丙')}")
        判("他文件的符号仍在", bool(表.查("甲")))

        # 改名 vs 结构：形状同、名不同 ⇒ 指纹同
        from .mem_code import 代码结构
        判("同名形状指纹同",
           代码结构("def 甲(x):\n    return x + 1", "python").指纹()
           == 代码结构("def 乙(x):\n    return x + 1", "python").指纹())

    for f in 失败:
        print(f"  ✗ {f}")
    if not 失败:
        print("  ✓ 符号表自检全绿（O(1) 查询 · 增量 ∝ Δ · 墓碑不进活集）")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(_自检())


# ★ 跨语言结构记忆（阶段 5）：TypeScript / JavaScript 抽取规则。
#   必须放在 `_定义正则` 定义之后——我第一次写成 `_定`（不存在的名字）⇒ 运行时 NameError。
#   教训：改别人文件前先确认**真实标识符**，别按记忆里的缩写写。
_定义正则["typescript"] = re.compile(
    r"^[ \t]*(?:export[ \t]+)?(?:default[ \t]+)?(?:declare[ \t]+)?"
    r"(?P<kind>abstract[ \t]+class|function|class|interface|type|const|let|var|enum|namespace)"
    r"[ \t]+(?P<name>[A-Za-z_$][\w$]*)", re.M)
_定义正则["javascript"] = _定义正则["typescript"]
