r"""公理足迹：**问 Lean 的内核**，不猜文本。

## 为什么必须做这一层（R303 实测出来的真结论）

本仓 4,021 个 `.lean` 里，**全仓 `sorry` 文本命中 0**。
按「查 `sorry`」这条老办法，会得出「**证明是干净的**」这个结论。

用 `#print axioms`（`Lean.collectAxioms`，走**内核环境**）扫了 **53,411 条**
本工程声明，结果是：

| 类别 | 条数 | 含义 |
|---|---|---|
| 无公理足迹（纯内核） | 40,873（76.5%） | 真·干净 |
| 标准三公理之一 | 12,519 | `propext` / `Quot.sound` / `Classical.choice` —— **数学基础，正常** |
| **`sorryAx`** | **0** | 没有留洞 |
| **`Lean.trustCompiler`** | **19** | ★ **证明不是内核验的，是「相信编译器」** |

★★★ 那 19 条全部落在**音律/节气**这一域（`LvPan` 11 · `SanFenSunYiXuLie` 4 ·
`JieQiDiTui` 2 · `WuXianYanTuoE3` 2），内容正是工程的核心主张：
十二律长度=12 · 十二律互不相同 · 黄钟第一 · 隔八相生 · 节气 24 循环 · 三分损益自洽。

**它们是 `native_decide` 出来的** —— 证明由**跑编译好的代码**给出，
而不是内核逐步验算。这在形式化里是一个**真实的信任缺口**，
而它**在源代码里可以一个字都不出现**（`sorry` 没有，`native_decide` 也可能藏在
被调用的引理里）。**只有内核的公理集能告诉你。**

⇒ 这一层的价值不是「更大的图」，是**维度**：
`grep` 数不出这个数，正则抽不出这个数，LSP 也不知道 axiom 是什么。

## 口径（三条，都不许改）

1. **按模块判归属，不按名字前缀**。第一版我用「名字以 `Mathlib.` 开头」判库 ——
   **错的**：mathlib 的**命名空间**不叫 `Mathlib`（那只是**模块**前缀），
   实测 377,777 条常量里只剔掉 **145** 条，过滤器等于没有。
   改用 `env.getModuleIdxFor?` + `env.header.moduleNames`。
2. **未覆盖要报「未测到」，不许报「0 洞」**。`import MyMeta` **不等于整个工程**：
   实测 1,832 个 olean 里只有 **790** 个在它环境里（缺 **1,042**，57%）。
   拿 43% 的覆盖率报「0 洞」就是把「看不见」当「没有」。
3. **成本不许进问答路径**。扫一遍 53,411 条 ≈ 10 分钟（`collectAxioms` 要爬证明项）。
   **扫是索引期的事**，读是问答期的事。

## 工具链约束（实测，不是猜）

- Lean **v4.27.0** 拒收**裸 CJK 标识符**（`def 库前缀` ⇒ `expected token`）。
- 模块名里**数字开头的段**（`MyMeta.05_IntelligentLife.*`）与 **CJK 段**
  （`对外认证包.*`）必须用**书名号**：`import «05_IntelligentLife»`。
- `IO.FS.Handle` **没有 `close`**（用 `flush`）。
- `let mut x := 0` 在 `run_cmd` 里推断不出类型 ⇒ 要写 `let mut x : Nat := 0`。
"""

from __future__ import annotations
from .proj_ident import 聚合入口名 as _聚合入口名
from .proj_ident import lean工程们 as _lean工程们
from .proj_ident import 最佳lean工程 as _最佳工程

import json
import os
import re
import subprocess
import time
from pathlib import Path

__all__ = ["扫", "读数", "缺口", "报告", "建参考", "参考语料", "查参考"]

# 产物档名：Lean 侧写出来的 TSV + 我们整理的 JSON
_档名 = "公理足迹.json"
_原始 = "公理足迹.tsv"
_超时秒 = 3600

# ★ 这些是**数学基础**（mathlib 自己也用），出现它们**不是缺陷**。
标准公理 = frozenset({"propext", "Quot.sound", "Classical.choice"})
# ★ 这两个的意思是「**相信编译器**」—— `native_decide` 走的路。
#   证明不是内核逐步验算出来的。**这是真缺口**，而且是文本查不出来的那种。
信任缺口 = frozenset({"Lean.trustCompiler", "Lean.ofReduceBool"})
# ★ `sorryAx` = 明说的洞。
洞 = frozenset({"sorryAx"})


def _包根() -> Path:
    return Path(__file__).resolve().parent


def 档路径(仓根: Path | None = None) -> Path:
    """整理后的读数档。默认落在包的 `products/mem/` 下（跟其它常驻读数一起）。

    ★ 这里踩过一次：原来写 `_包根().parents[2]`，那是**仓根**，然后我又拼了
      `多智能体编排/agentscope_orchestrator/...` ⇒ 路径**多一层**
      （`多智能体编排\\多智能体编排\\...`），档永远找不到 ——
      而「找不到」会**伪装成「未测到」**，看起来像正常降级，其实是路径写错。
      ⇒ 默认根取 `_包根().parents[1]`（**包目录**），再 `products/mem`。
    """
    包 = _包根()
    根 = Path(仓根) if 仓根 else (包.parents[1] if len(包.parents) > 1 else 包)
    if (根 / "products").is_dir() or 根.name == "agentscope_orchestrator":
        return 根 / "products" / "mem" / _档名
    # 给的是仓根（含「多智能体编排/agentscope_orchestrator」）
    侯 = 根 / "多智能体编排" / "agentscope_orchestrator"
    if 侯.is_dir():
        return 侯 / "products" / "mem" / _档名
    return 根 / "products" / "mem" / _档名


def _分类(公理们) -> str:
    集 = {str(x) for x in 公理们}
    if 集 & 洞:
        return "①洞"
    if 集 & 信任缺口:
        return "②信任编译器"
    if 集 and not (集 - 标准公理):
        return "③标准公理"
    if 集:
        return "④其它"
    return "⑤无公理"


def 整理(tsv: Path, 覆盖: dict | None = None) -> dict:
    """把 Lean 写的 TSV 整理成读数。**纯读，不跑 Lean。**"""
    n带 = n无 = 0
    分类: dict[str, int] = {}
    组合: dict[str, int] = {}
    信任: list[dict] = []
    洞们: list[dict] = []
    例: dict[str, list[str]] = {}
    总行 = 0
    if not tsv.is_file():
        return {"未测到": f"没有原始档（{tsv.name}）—— **这不算「0 洞」**"}
    for 行 in tsv.read_text(encoding="utf-8", errors="replace").splitlines():
        if not 行 or 行.startswith(("PICK", "STATS", "PROGRESS")):
            continue
        if 行.startswith("STATS"):
            continue
        总行 += 1
        部 = 行.split("\t")
        if len(部) < 2:
            continue
        标签, 名 = 部[0], 部[1]
        公理文 = 部[2] if len(部) > 2 else "[]"
        公理们 = [x.strip() for x in 公理文.strip("[]").split(",") if x.strip()]
        类 = _分类(公理们)
        分类[类] = 分类.get(类, 0) + 1
        if 类 in ("①洞", "②信任编译器"):
            组合[公理文] = 组合.get(公理文, 0) + 1
            if 类 == "①洞":
                洞们.append({"名": 名, "公理": 公理们})
            else:
                信任.append({"名": 名, "公理": 公理们})
        例.setdefault(类, [])
        if len(例[类]) < 12:
            例[类].append(名)
    n带 = sum(分类.values())
    n无 = max(0, (覆盖 or {}).get("扫了", n带) - n带) if 覆盖 else 0
    出: dict = {
        "★这一层在算什么": ("每条声明的**公理集**（内核给的，不是文本找的）—— "
                            "`sorryAx` = 洞 · `Lean.trustCompiler` = 相信编译器"),
        "①洞（sorryAx）": 分类.get("①洞", 0),
        "★②信任编译器（证明不是内核验的）": 分类.get("②信任编译器", 0),
        "③只站标准公理（数学基础，正常）": 分类.get("③标准公理", 0),
        "④其它公理": 分类.get("④其它", 0),
        # ★ 踩过一次：原来写 `分类.get("⑤无公理", 0)` —— **永远是 0**。
        #   因为 Lean 侧**只写有足迹的行**（`AX`/`HOLE`），「没有公理」在 TSV 里
        #   **根本没有对应的行** ⇒ 必须从 `扫了 − 带足迹` **推**出来。
        #   这个 bug 的形状很坏：它把一个**好数字**（40,873 条纯内核）显示成 0，
        #   而 0 看起来像「一个都没有」，不会有人去质疑。
        # ★ 三踩：`扫了` 拼不全时（实测拼出 4,690 < 带足迹 12,917）
        #   **不许显示 0** —— 0 看起来像「一个都没有」。宁可如实说「未测到」。
        "⑤无公理（纯内核，真干净）": (n无 if (覆盖 and (覆盖.get("扫了") or 0) >= n带)
                                     else "未测到（TSV 里没有可靠的「扫了」⇒ 无足迹的那些数不出来）"),
        "带足迹的声明数": n带,
        "★②的名单": 信任[:40],
        "①的名单": 洞们[:40],
        "各例": 例,
        "★组合分布（只列①与②）": dict(sorted(组合.items(), key=lambda kv: -kv[1])[:12]),
        "★为什么必须查内核（留档）": ("本仓全仓 `sorry` 文本命中 **0** —— "
                                        "只查文本会得出「证明是干净的」。"
                                        "那 19 条 `trustCompiler` **文本里一个字都不出现**。"),
    }
    if 覆盖:
        出.update({k: v for k, v in 覆盖.items()})
    if not 覆盖:
        出["★未测到"] = ("**没有覆盖率信息** ⇒ 下面的分类数只是「扫到的那些」的分类，"
                          "**不许当全工程结论**。")
    return 出


def 读数(仓根: Path | None = None) -> dict:
    """读常驻档。**不走 Lean、不走仓库**。缺档 ⇒ 明说「未测到」。"""
    档 = 档路径(仓根)
    try:
        return json.loads(档.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"未测到": f"没有 `{档.name}`（公理足迹是**索引期**产物，"
                          f"要跑 `python -m agentscope_orchestrator.mem_axiom 扫`）—— "
                          f"**这不算「0 洞」**"}
    except Exception as e:                       # noqa: BLE001
        return {"未测到": f"`{档.name}` 读不出来（{type(e).__name__}: {e}）"}


def 缺口(仓根: Path | None = None) -> dict:
    """接进常驻缺口读数的第五类。"""
    读 = 读数(仓根)
    if "未测到" in 读:
        return 读
    出 = {
        "★缺口节点来自": "Lean 内核的公理集（`collectAxioms`）—— **语义级**，非文本",
        "①洞（sorryAx）": 读.get("①洞（sorryAx）", 0),
        "★②信任编译器": 读.get("★②信任编译器（证明不是内核验的）", 0),
        "③标准公理（正常）": 读.get("③只站标准公理（数学基础，正常）", 0),
        "⑤无公理（真干净）": 读.get("⑤无公理（纯内核，真干净）", 0),
        "★②的名单": 读.get("★②的名单", []),
        "★覆盖率": 读.get("★覆盖率", "未测到"),
        "★口径": ("①=明说的洞 · ②=**相信编译器**（`native_decide`，证明不是内核验的）· "
                   "③=propext/Quot.sound/Classical.choice（数学基础）· ⑤=纯内核"),
    }
    return 出


def 报告(仓根: Path | None = None) -> str:
    读 = 读数(仓根)
    if "未测到" in 读:
        return f"公理足迹：未测到（{读['未测到']}）"
    部 = [f"公理足迹：①洞 {读.get('①洞（sorryAx）', 0)}"]
    if 读.get("★②信任编译器（证明不是内核验的）"):
        部.append(f"**★②信任编译器 {读['★②信任编译器（证明不是内核验的）']}**")
    部.append(f"③标准公理 {读.get('③只站标准公理（数学基础，正常）', 0)}")
    部.append(f"⑤无公理 {读.get('⑤无公理（纯内核，真干净）', 0)}")
    if 读.get("★覆盖率"):
        部.append(读["★覆盖率"])
    return " · ".join(部)


# ── 扫（索引期）────────────────────────────────────────────────────────
def _找工程(仓根: Path) -> Path | None:
    """含 `lakefile.lean` 的 lean 工程目录。

    ★ R566：原来只认两个**写死**路径（`形式化验证/formal_proof`、根 `formal_proof`），
      而本仓这两处**都没有** lakefile.lean ⇒ 公理足迹**永远**返回「未测到」。
      现在全仓 rglob（跳过构建/归档目录），取名交给 `proj_ident`（R117 唯一取名点）。
    """
    们 = _lean工程们(仓根)
    if not 们:
        return None
    # ★ 只取"最浅的那个"会选中 `semsearch`（考试题库工程）—— **选错项目且不报错**。
    #   改用打分：在 profile 声明的 lean 根下 +1000 · 有 `.lake` 构建 +100 · lean 文件数 +1。
    return _最佳工程(仓根) or 们[0]


def 规范名(名: str) -> str:
    """★ R519：**Lean 转义标识符的唯一归一化点**（`«…»` → 去掉引号）。

    ★ 为什么必须有这么个函数（口径 305）：`«` / `»` 是 **Lean 的标识符引号**，
      不是名字的一部分。`Name.toString` 对非法标识符段自动加它们 ⇒
      elaborator 导出的自动名（`«term_<n_»`）**天生带引号**，
      而源码里写的是别的东西 ⇒ 「提及」侧**永远对不上**。

    ★ 与 `kg._剥Lean注释` 之后那处 `re.sub(r"[«»]", " ", 净)` 的分工：
      那里换**空格**是为了**保列号**（源码文本）；这里是**名字**，
      没有列号要保 ⇒ **直接去掉**（`term_<n_` 比 `term_ n_ ` 更像一个名字）。

    ★ 安全性（实测，不是推理）：确证档 53,411 行里带引号 772 行，
      ★ 归一化后**互相撞 0 组** · **与裸名相同 0 个** ⇒ 去掉引号**零碰撞**。
    """
    if "«" in 名 or "»" in 名:
        return 名.replace("«", "").replace("»", "")
    return 名


def 带引号吗(名: str) -> bool:
    """这个名字有没有被 Lean 的标识符引号包过（归一化**前**的形态）。"""
    return "«" in 名 or "»" in 名


def _模块清单(工程: Path) -> list[str]:
    库 = 工程 / ".lake" / "build" / "lib" / "lean"
    原 = 库.parent.parent.parent / "build" / "lib" / "lean"
    基 = 原 if 原.is_dir() else 库
    if not 基.is_dir():
        return []
    出: set[str] = set()
    for p in 基.rglob("*.olean"):
        rel = p.relative_to(基).with_suffix("")
        出.add(".".join(rel.parts))
    return sorted(出)


def _引号(模块: str) -> str:
    """非法段用书名号 —— Lean 拒收裸 CJK / 数字开头的段（实测）。"""
    部 = []
    for 段 in 模块.split("."):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", 段):
            部.append(段)
        else:
            部.append(f"«{段}»")
    return ".".join(部)


def 扫(仓根: Path, 每批: int = 60, 上限批: int = 200, 重试: int = 6,
       打印=print) -> dict:
    """跑 Lean 扫公理足迹。**索引期的事，绝不放进问答路径。**

    ★ 分两段：
      1. `import MyMeta` —— 拿到聚合入口能看到的那些（实测 790/1,832 模块）；
      2. **按批补齐**剩下的模块，每批一个干净环境（批内冲突好收敛，
         且一批失败不牵连全局）。
    ★ **冲突模块如实计入 `跳过`**，不假装覆盖到了。
    """
    仓根 = Path(仓根)
    工程 = _找工程(仓根)
    if 工程 is None:
        return {"未测到": f"全仓没有 lakefile.lean（找过 {仓根.name} 下所有非构建目录）"}
    lake = "lake"
    模块们 = _模块清单(工程)
    if not 模块们:
        return {"未测到": "oleans 目录不存在 ⇒ 工程没建过"}
    临时 = Path(os.environ.get("TEMP") or 仓根) / "r303_axiom"
    临时.mkdir(parents=True, exist_ok=True)

    def 跑(名字: str, 源: str) -> tuple[int, str, Path]:
        f = 临时 / f"{名字}.lean"
        f.write_text(源, encoding="utf-8")
        tsv = 临时 / f"{名字}.tsv"
        if tsv.exists():
            tsv.unlink()
        t0 = time.time()
        p = subprocess.run([lake, "env", "lean", str(f)], cwd=str(工程),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=_超时秒)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), tsv

    def 生成(导入: list[str], 名们: list[str], 目标: Path) -> str:
        imp = "\n".join(f"import {_引号(m)}" for m in 导入)
        lit = ",\n  ".join(f'"{m}"' for m in 名们)
        return f"""{imp}
import Lean.Util.CollectAxioms
open Lean Elab Command

def batchNames : List String := [
  {lit}
]
def batchSet : NameSet := batchNames.foldl (fun a s => a.insert s.toName) {{}}

def outPath : System.FilePath :=
  "{str(目标).replace(chr(92), '/')}"

run_cmd do
  let env ← getEnv
  let names := env.header.moduleNames
  let md := env.header.moduleData
  let h ← IO.FS.Handle.mk outPath IO.FS.Mode.write
  let mut decls : Array Name := #[]
  let mut hit : Nat := 0
  for i in List.range names.size do
    match names[i]? with
    | some m =>
      if batchSet.contains m then
        hit := hit + 1
        match md[i]? with
        | some d => decls := decls ++ d.constNames
        | none => pure ()
    | none => pure ()
  h.putStrLn s!"PICK\\thit_modules={{hit}}\\tpicked={{decls.size}}"
  h.flush
  let mut withAx : Nat := 0
  let mut withHole : Nat := 0
  for n in decls do
    let axs ← collectAxioms n
    if axs.size > 0 then
      withAx := withAx + 1
      if axs.contains ``sorryAx then
        withHole := withHole + 1
        h.putStrLn s!"HOLE\\t{{n}}\\t{{axs.toList}}"
      else
        h.putStrLn s!"AX\\t{{n}}\\t{{axs.toList}}"
  h.putStrLn s!"STATS\\tpicked={{decls.size}}\\twithAx={{withAx}}\\twithHole={{withHole}}"
  h.flush
"""

    # ── 第 1 段：聚合入口 ──
    全部tsv = 临时 / "累计.tsv"
    if 全部tsv.exists():
        全部tsv.unlink()
    覆盖: dict = {"wanted": len(模块们)}
    主 = 临时 / "主.tsv"
    _入口 = _聚合入口名(工程) or ""
    if not _入口:
        return {"未测到": f"lakefile.lean 里读不到 `package X where`：{工程}"}
    打印(f"   [1/2] `import {_入口}`（聚合入口）…")
    码, 文, _ = 跑("主", 生成([_入口], 模块们, 主))
    已覆盖: list[str] = []
    if 码 == 0 and 主.is_file():
        _c = 主.read_text(encoding="utf-8", errors="replace").splitlines()
        Add = [x for x in _c if not x.startswith(("PICK", "STATS", "PROGRESS"))]
        全部tsv.write_text("\n".join(Add) + "\n", encoding="utf-8")
        # 覆盖名单：从 Lean 再问一次（快，不 collectAxioms）
        覆源 = (f"import {_入口}\nimport Lean.Util.CollectAxioms\nopen Lean Elab Command\n"
                f'def outPath : System.FilePath := "{str(临时 / "覆盖.txt").replace(chr(92), "/")}"\n'
                "run_cmd do\n  let env ← getEnv\n"
                "  let names := env.header.moduleNames\n"
                "  let s : NameSet := names.foldl (fun a m => a.insert m) {}\n"
                "  let h ← IO.FS.Handle.mk outPath IO.FS.Mode.write\n"
                "  for i in List.range names.size do\n"
                "    match names[i]? with\n    | some m => h.putStrLn s!\"{m}\"\n"
                "    | none => pure ()\n  h.flush\n")
        码2, _, _ = 跑("覆盖", 覆源)
        if 码2 == 0 and (临时 / "覆盖.txt").is_file():
            环境模块 = set((临时 / "覆盖.txt").read_text(
                encoding="utf-8", errors="replace").split())
            已覆盖 = [m for m in 模块们 if m in 环境模块]
    覆盖["covered"] = len(已覆盖)
    剩余 = [m for m in 模块们 if m not in set(已覆盖)]
    打印(f"        覆盖 {len(已覆盖)}/{len(模块们)} · 剩 {len(剩余)}")

    # ── 第 2 段：分批补齐 ──
    跳过: list[str] = []
    批数 = (len(剩余) + 每批 - 1) // 每批
    for i in range(0, min(len(剩余), 上限批 * 每批), 每批):
        批 = 剩余[i:i + 每批]
        if not 批:
            continue
        号 = i // 每批 + 1
        剔: list[str] = []
        for _ in range(重试):
            可 = [m for m in 批 if m not in set(剔)]
            if not 可:
                break
            目标 = 临时 / f"批{号}.tsv"
            码, 文, _ = 跑(f"批{号}", 生成(可, 可, 目标))
            if 码 == 0:
                break
            m = re.search(r"import (\S+) failed", 文)
            if not m:
                break
            剔.append(m.group(1))
        if 目标.is_file():
            with 全部tsv.open("a", encoding="utf-8") as fh:
                fh.write(目标.read_text(encoding="utf-8", errors="replace"))
        跳过.extend(剔)
        打印(f"       批 {号}/{批数} · 可 {len(剔) and len(批) - len(剔) or len(批)} · "
             f"跳过 {len(剔)}")

    覆盖["跳过模块数"] = len(跳过)
    覆盖["跳过模块"] = 跳过[:60]
    覆盖["命中模块"] = len(已覆盖) + (len(剩余) - len(跳过))
    覆盖["★覆盖率"] = (f"{len(已覆盖)}/{len(模块们)} 模块由聚合入口覆盖"
                       f"（{(100.0 * len(已覆盖) / max(1, len(模块们))):.0f}%）"
                       f" · 补齐 {len(剩余) - len(跳过)} 个"
                       f"（跳过 {len(跳过)} 个冲突模块）")

    # ★★ 踩过**两次**的坑（第一次在 `整理` 的调用方，第二次就在这儿）：
    #    `覆盖` 里没有 `"扫了"` ⇒ `整理()` 算不出「无公理」⇒ `⑤无公理 = 0`。
    #    而 **0 看起来像「一个都没有」**，是个好数字被显示成 0，
    #    而且**不报错** —— 只有把实得值打出来核对才发现。
    #    「扫了」只能从 Lean 侧的 `PICK/STATS` 行拿：TSV 里**只写有足迹的行**，
    #    没有足迹的**根本没有行** ⇒ 数不出来，必须问写它的那一侧。
    _扫了 = 0
    if 全部tsv.is_file():
        for _行 in 全部tsv.read_text(encoding="utf-8", errors="replace").splitlines():
            if _行.startswith(("PICK\t", "STATS\t")):
                _m = re.search(r"picked=(\d+)", _行)
                if _m:
                    _扫了 += int(_m.group(1))
    覆盖["扫了"] = _扫了

    出 = 整理(全部tsv, 覆盖)
    档 = 档路径(仓根)
    档.parent.mkdir(parents=True, exist_ok=True)
    (档.parent / _原始).write_text(
        全部tsv.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    档.write_text(json.dumps(出, ensure_ascii=False, indent=1), encoding="utf-8")
    出["★落档"] = str(档)
    return 出



# ── ⑤ 参考语料：mathlib **独立索引**，不进代码图 ────────────────────
_参考档 = "参考语料-mathlib.json"
_全名档 = "参考语料-mathlib全名.txt"


def 参考档路径(仓根: Path | None = None) -> Path:
    return 档路径(仓根).parent / _参考档


def 建参考(仓根: Path | None = None) -> dict:
    """把 mathlib 做成**独立**的参考语料索引。

    ★ 为什么这是单独一层（目标 ⑤）——**这是本轮我自己纠正的一个概念错**：
      `.lake`（mathlib，约 11 万文件）在代码图里一直是**被剪掉**的。
      那对「**代码图成员资格**」是**对的**（mathlib 不是本仓的代码）。
      但我把两个角色**塌成了一条规则**：
        · 角色 A：**代码图成员资格** —— mathlib 不该进；
        · 角色 B：**参考语料** —— mathlib 是**最重要的**那一份
          （111k 文件、有人审过、公理口径可比）。
      ⇒ ⑤ 的修法是**分层，不是剪枝**：图成员资格**一个字不改**，
        mathlib 进这一份**独立**索引。两件事各归各的规则。

    ★ 上限：mathlib 有 149,030 条声明，全扫约 4.5 小时
      ⇒ 这份索引是**前 20,000 条抽样**。**上限必须跟着读数一起报。**
    """
    基 = 档路径(仓根).parent / "公理足迹-mathlib基线.tsv"
    if not 基.is_file():
        return {"未测到": f"没有 mathlib 基线原始档（`{基.name}`）—— "
                          f"要跑一次 mathlib 基线扫描（同 `mem_axiom` 的尺子）"}
    索引: dict[str, dict] = {}
    for 行 in 基.read_text(encoding="utf-8", errors="replace").splitlines():
        if not 行.startswith(("AX\t", "HOLE\t")):
            continue
        部 = 行.split("\t")
        if len(部) < 2:
            continue
        公理们 = [x.strip() for x in (部[2] if len(部) > 2 else "[]").strip("[]").split(",")
                  if x.strip()]
        索引[部[1]] = {"公理": 公理们, "类": _分类(公理们)}
    # ★ 全量名字：**必须另出**。`索引` 只含**带足迹**的（6,599 条），
    #   而 mathlib 有 149,030 条 —— 拿带足迹那份当「mathlib 有哪些定理」，
    #   `Nat.add_comm` 会查出「无」（它是纯内核的，不在那份里）。
    #   **「看不见」不能当「没有」**：名字全量，足迹才知道。
    全名档 = 档路径(仓根).parent / _全名档
    全名: set[str] = set()
    if 全名档.is_file():
        全名 = {x.strip() for x in 全名档.read_text(encoding="utf-8", errors="replace").splitlines()
                if x.strip()}
    尾巴: dict[str, list[str]] = {}
    for 名 in 全名 or 索引:
        尾巴.setdefault(名.rsplit(".", 1)[-1], []).append(名)
    出 = {
        "角色": "**参考语料**（≠ 代码图成员资格，mathlib 仍然**不进图**）",
        "来源": "`.lake/packages/mathlib`（已建，`Mathlib.olean` 在）",
        "★名字条数（全量）": len(全名),
        "★足迹条数（抽样）": len(索引),
        "★覆盖": ("名字：**mathlib 全量**（`import Mathlib`，7,517 个模块）· "
                  "足迹：**前 20,000 条抽样**（全扫约 4.5 小时）"),
        "★口径": "同一把尺子：`Lean.collectAxioms`，走内核环境",
        "★这一层能回答什么": ("①「mathlib 里有没有这个名字」（**全量**）· "
                             "②「它的公理足迹是什么」（**抽样**）"),
        "★两件事分开报的理由": ("名字查全量、足迹查抽样 —— 混成一份会让纯内核的定理"
                              "查出「无」，**把「看不见」当「没有」**。"),
        "★★全量这一步做过（R303 第 8 轮，98 秒，没 OOM）": (
            "先跑的是 `import MyMeta` ⇒ 只有它**传递需要**的 mathlib"
            "（1,605 个模块 / 148,931 条）—— 那份里 **`MeasureTheory.` 的声明 0 条**。\n"
            "改跑 `import Mathlib` ⇒ **7,517 个模块 / 497,907 条**，"
            "其中 **`MeasureTheory.` 14,408 条**（声明名里含 `MeasureTheory.` 的 19,269 条）。\n"
            "★ 教训：**「看不见」不能当「没有」** —— 差的是 **3.3 倍**，"
            "而且差的正好是**整个测度论**。原始档：`参考语料-mathlib全量.tsv`。"),
    }
    档 = 参考档路径(仓根)
    档.parent.mkdir(parents=True, exist_ok=True)
    档.write_text(json.dumps({**出, "_索引": 索引, "_尾名": 尾巴},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    出["★落档"] = str(档)
    return 出


def 参考语料(仓根: Path | None = None) -> dict:
    """读参考语料索引（**只读档，不跑 Lean**）。缺档 ⇒ 明说「未测到」。"""
    档 = 参考档路径(仓根)
    try:
        return json.loads(档.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"未测到": f"没有 `{_参考档}`（跑 `python -m agentscope_orchestrator.mem_axiom 参考`）"}
    except Exception as e:                       # noqa: BLE001
        return {"未测到": f"`{_参考档}` 读不出来（{type(e).__name__}: {e}）"}


def 查参考(名: str, 仓根: Path | None = None) -> dict:
    """「本仓这条声明，mathlib 里有没有对应物？」

    三档匹配，**弱到强分开报**（不许把弱匹配当结论）：
      · 同名（含命名空间）：`Nat.add_comm` ↔ `Nat.add_comm`
      · 同尾名：`LvPan.shiErLv_nodup` ↔ `Foo.shiErLv_nodup`
      · 无：mathlib 里没有同名/同尾名的（**不推论「没有对应定理」**）
    """
    读 = 参考语料(仓根)
    if "未测到" in 读:
        return 读
    索引 = 读.get("_索引") or {}
    尾 = 读.get("_尾名") or {}
    尾名 = 名.rsplit(".", 1)[-1]
    同名 = 索引.get(名)
    同尾 = [n for n in (尾.get(尾名) or []) if n != 名]
    出: dict = {
        "问的是": 名,
        "★mathlib 有这个名字吗（全量）": ("有" if 尾.get(尾名) and 名 in (尾.get(尾名) or [])
                                        else ("有（同尾名命中，见下）" if 同尾 else "**没有**")),
        "★同名": ({"mathlib": 名, **同名} if 同名
                  else ("有这个名字，但**不在足迹抽样里** ⇒ 它是纯内核的（**这是好事**）"
                        if 名 in (尾.get(尾名) or []) else "无")),
        "同尾名": [{"mathlib": n, **索引.get(n, {"公理": "（不在足迹抽样里）", "类": "?"})}
                   for n in 同尾[:6]],
        "★边界": ("扫的是 mathlib **前 20,000 条抽样** ⇒ 「无」只能说明"
                  "**这 2 万条里没有**，**不能推论「mathlib 没有这条定理」**。"),
    }
    if not 同名 and not 同尾:
        出["★读法"] = ("同名/同尾名都没有 —— **这不是「mathlib 做不到」，"
                       "是「这份抽样里没看到」**。要下结论得扫全量。")
    return 出



# ── ④ `.lean` 侧符号抽取：**elaborator 第一来源，正则降级为兜底** ──────
_确证档 = "工程声明名-elaborator.txt"


def 确证档路径(仓根: Path | None = None) -> Path:
    return 档路径(仓根).parent / _确证档


def lean符号(仓根: Path | None = None) -> dict:
    """④ 的落地：`.lean` 侧符号，**先问 elaborator，正则只兜底**。

    ★ 为什么要换（这是本目标的原话）：
      正则只看**文本**。elaborator 知道**它把每一条声明真识别成了什么名字** ——
      自动生成的证明项（`_proof_`）、私有名编码（`_private`）、结构生成的
      投影与 `_sizeOf`（`match_` / `_sizeOf`）。实测本仓 **4,396 条（8.2%）**
      是这样的名字，**它们在源码里根本不以那个形式出现** ⇒ 正则**永远抽不到**。

    ★ 三级口径，**必须一起报**（不许把兜底冒充确证）：
      · `确证`  —— 来自 elaborator（`env.header.moduleData[i].constNames`）
      · `兜底`  —— 正则抽的（`mem_symbols.抽取符号`），**只在 elaborator 覆盖不到时用**
      · `没测到` —— 两侧都没有的（**不许当「没有」**）

    ★ 覆盖边界：确证档是 `import MyMeta` 那 **790/1,832 个模块**。
      其余 1,042 个模块**没测到** —— 只能兜底，而且必须说明。
    """
    确 = 确证档路径(仓根)
    出: dict = {"★口径": "elaborator 第一来源；正则仅在覆盖不到时兜底"}
    if 确.is_file():
        _原们 = [x.strip() for x in
                 确.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
        # ★ R519：**把 `«…»` 去掉再交出去**（唯一归一化点：`规范名`）。
        #   ★ 不静默：条数变化、撞名、样例都报出来。
        _名们 = [规范名(x) for x in _原们]
        _带 = sum(1 for x in _原们 if 带引号吗(x))
        # ★★ 口径 194：**两个口径不许混成一个数**。
        #   实测（真档 53,411 行）：原始自带重复 **10** 条 ·
        #   归一化**额外**造成的撞名 **0** 条 —— 这是两回事，分两栏报。
        _原始自带重复 = len(_原们) - len(set(_原们))
        _名们 = sorted(set(_名们))
        _归一额外 = len(set(_原们)) - len(_名们)
        名们 = _名们
        出["★第一来源"] = "**elaborator**（`moduleData.constNames`）"
        出["确证条数"] = len(名们)
        出["确证"] = 名们
        出["★归一化（`«»` 是 Lean 的标识符引号，不是名字的一部分）"] = {
            "原始条数": len(_原们),
            "带引号的": _带,
            "归一化后去重条数": len(名们),
            "★原始档自带的重复（**与归一化无关**）": _原始自带重复,
            "★归一化额外造成的撞名": _归一额外,
            "★边界": ("**归一化额外造成的撞名** > 0 才说明去掉引号会造成错绑 —— "
                       "那时该保留引号或改成带引号的规范形，"
                       "**不许闷着归一化**（口径 203：宁可漏绑，不可错绑）"),
        }
        出["★覆盖"] = (f"`import {_入口}` ⇒ **790/1,832 个模块**；"
                       "其余 1,042 个**没测到**，只能兜底")
        形状 = {k: sum(1 for n in 名们 if k in n)
                for k in ("_proof_", "_private", "match_", "_sizeOf")}
        出["★正则抽不到的自动名"] = 形状
        出["★证明「问 elaborator 有区别」"] = (
            f"确证集里有 **{sum(形状.values())}** 条自动名（`_proof_`/`_private`/"
            f"`match_`/`_sizeOf`）—— 这些名字**源码里不出现**，正则抽不到。"
            f"**若这条数为 0，说明 elaborator 那条路其实没接上。**")
    else:
        出["★第一来源"] = "**正则兜底**"
        出["★为什么是兜底"] = f"没有 `{_确证档}` ⇒ **elaborator 那条路没接上**"
        出["确证条数"] = 0
    return 出


def 正则兜底(文本: str) -> list[tuple[str, str, int, str]]:
    """正则兜底 —— **只在 elaborator 覆盖不到时用**。

    ★ 这里踩过一次：上一轮我对账时取了 `group(1)`，拿到的是关键字
      （`abbrev`/`axiom`/`class`/`def`）而不是名字 ⇒ 对账数字全废。
      **正则用的是命名组 `name`** —— 去读生产者，别猜分组序号。
    """
    from . import mem_symbols as _ms
    return _ms.抽取符号(文本, "lean")



# ── ④ 的**接线**：模块 → elaborator 确证名（带归属） ────────────────
_归属档 = "模块声明名-elaborator.tsv"
_归属缓存: dict[str, set[str]] = {}


def 模块确证名(模块: str, 仓根: Path | None = None) -> set[str] | None:
    """给定模块名，返回 **elaborator 确证**的声明名集；没这个模块 ⇒ `None`。

    ★ 为什么必须带归属（这是「一个没有调用方的函数等于没有接线」的具体形状）：
      `抽取符号` 是**按文件文本**抽的，而 elaborator 索引原来只有名字、
      **没有归属** ⇒ 接线时对不上文件 ⇒ 只能挂在旁边当摆设。
      出了带模块列的档，才接得上。

    ★ 一次读全档、按模块缓存 —— 53,714 行，读一次约 40 ms。
    """
    档 = 档路径(仓根).parent / _归属档
    if not 档.is_file():
        return None
    if not _归属缓存:
        for 行 in 档.read_text(encoding="utf-8", errors="replace").splitlines():
            部 = 行.split("\t")
            if len(部) >= 2:
                # ★ R519：**同一把尺子** —— 归属档里的名字也要过 `规范名`
                #   （口径 305：同一个处理写三处、只改一处就是病）。
                _归属缓存.setdefault(部[0].strip(), set()).add(规范名(部[1].strip()))
    return _归属缓存.get(模块)


def _自检() -> int:
    """不带 Lean 也能跑的自检：分类逻辑的两侧 + 缺档时不许说「0 洞」。"""
    错: list[str] = []
    临时 = Path(os.environ.get("TEMP") or ".") / "r303_axiom_自检.tsv"
    # 两侧：一条纯内核、一条标准公理、一条信任编译器、一条洞
    临时.write_text(
        "AX\tA\t[propext]\n"
        "AX\tB\t[propext, Quot.sound, Classical.choice]\n"
        "HOLE\tC\t[sorryAx]\n"
        "AX\tD\t[Lean.ofReduceBool, Lean.trustCompiler]\n"
        "AX\tE\t[Lean.trustCompiler, propext]\n", encoding="utf-8")
    果 = 整理(临时, {"扫了": 8})
    if 果["①洞（sorryAx）"] != 1:
        错.append(f"①洞应为 1，实得 {果['①洞（sorryAx）']}")
    if 果["★②信任编译器（证明不是内核验的）"] != 2:
        错.append(f"②信任编译器应为 2，实得 {果['★②信任编译器（证明不是内核验的）']}")
    if 果["③只站标准公理（数学基础，正常）"] != 2:
        错.append(f"③标准公理应 2（A 和 B），实得 {果['③只站标准公理（数学基础，正常）']}")
    # ★ 踩过的 bug：`⑤无公理` 原来直接取分类里的 `"⑤无公理"`，那是**永远 0**
    #   —— 因为 Lean 侧只写**有足迹**的行，「无公理」在 TSV 里没有对应行。
    #   8 条扫了、5 条有足迹 ⇒ 应为 3。0 是个**好数字被显示成 0** 的坏 bug。
    if 果["⑤无公理（纯内核，真干净）"] != 3:
        错.append(f"⑤无公理应 3（8−5），实得 {果['⑤无公理（纯内核，真干净）']}"
                  f" —— 若为 0，说明「无公理」是取的分类而不是推的")
    if "sorryAx" in str(果["★②的名单"]):
        错.append("②的名单里混进了 `sorryAx` ⇒ 两类没分开")
    # ★ ④ 的两侧：确证档在 ⇒ 主来源必须是 elaborator；且自动名必须 > 0
    #   （若为 0，说明「问 elaborator」其实没接上，这条判据就没齿）
    _符 = lean符号()
    if _符.get("确证条数"):
        if "elaborator" not in str(_符.get("★第一来源", "")):
            错.append("④：确证档在，第一来源却不是 elaborator")
        if sum((_符.get("★正则抽不到的自动名") or {}).values()) == 0:
            错.append("④：确证集里**一条自动名都没有** ⇒ "
                      "「问 elaborator 有区别」这句话证不出来（正则也能抽到同名）")
    else:
        if "兜底" not in str(_符.get("★第一来源", "")):
            错.append("④：没有确证档时，第一来源必须明说是**正则兜底**，不许冒充确证")
    # ★ 正则兜底必须真的抽得到（用命名组 `name`，不许猜序号）
    try:
        _r = 正则兜底("theorem foo_bar : True := trivial\n")
        if not _r or not any("foo_bar" in str(x) for x in _r):
            错.append(f"④：正则兜底抽不出 `foo_bar`，实得 {_r[:2]} ⇒ "
                      f"兜底那条路是死的（或分组名取错了）")
    except Exception as _e:                      # noqa: BLE001
        错.append(f"④：正则兜底炸了（{type(_e).__name__}: {_e}）")

    # 缺档 ⇒ 必须说「未测到」，**不许**给 0
    缺 = 读数(Path("__绝不可能存在_r303__"))
    if "未测到" not in 缺:
        错.append("缺档时没出「未测到」⇒ 会把「没测」报成「干净」")
    if "0" in str(缺) and "未测到" in str(缺):
        pass
    临时.unlink(missing_ok=True)
    if 错:
        print("❌ 公理足迹自检：")
        for e in 错:
            print("   -", e)
        return 1
    print("✅ 公理足迹自检全绿（分类两侧 + 洞/信任分开 + 缺档出「未测到」）")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "扫":
        根 = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
        r = 扫(根)
        print(json.dumps({k: v for k, v in r.items()
                          if k not in ("★②的名单", "①的名单", "各例")},
                         ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "参考":
        根 = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
        print(json.dumps(建参考(根), ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "查参考":
        print(json.dumps(查参考(sys.argv[2]), ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "报告":
        print(报告(Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()))
    else:
        sys.exit(_自检())
