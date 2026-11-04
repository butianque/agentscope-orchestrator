"""档案自举：把一个**陌生工程目录**变成一个**可跑的档案**（一句话入口的第一段）。

═══════════════════════════════════════════════════════════════════════
为什么需要本模块（真跑抓出的产品缺口）
═══════════════════════════════════════════════════════════════════════
本工具的纪律是「换项目 = 换一个档案文件，代码零改动」。这很干净，
但**代价全压在用户身上**：想在一个新工程上跑，用户得**手写一份 `profile.yml`**
（判据门命令、源码根、冻结区……）。

  · 而 R21 之后，档案里的 `gates` 是**独立验收来源**——
    没有它，主树任务就**无从独立验收**（只能信模型自己写的门）。
  · 而 R23 之后，`frozen` 是**判据不可改**的唯一保证。
  ⇒ **手写档案 = 这个产品不可用**（用户不会写，也不知道该写什么）。

★★★ 用户口径：「能用库里的**全称机制**就别枚举」。
    本模块的形态就是这条口径：**一张谱系表 + 一条通路**，
    **没有一处 `if 语言 == "rust"`** —— 表是数据，通路读表。
    新增一个生态 = **往表里加一行**，代码不动。

═══════════════════════════════════════════════════════════════════════
诚实边界（不许含糊）
═══════════════════════════════════════════════════════════════════════
1. **不猜语义**。本模块只认**仓库根真实存在的清单文件**，只读**工程自己声明的**
   命令（`package.json` 的 `scripts`、`Makefile` 的 `test:` 目标）。
   清单文件不认识 ⇒ **不生成任何门**，如实报「不认识这个工程」，
   **绝不用恒真占位门冒充判据**（与 `profile.py` 同一口径）。

2. ★★★★★ **生成的门必须当场真跑一遍**（`探跑=True` 默认开）。
   这是 R21 的教训的直接应用：门是**独立验收来源**，
   若它其实**跑不起来**（工具链没装 / 命令名错），
   那"项目验收门红"就会**因为与环境无关的原因**红 ——
   一个**假红**的项目级门，比没有门更坏（它把真问题淹掉）。
   ⇒ 探跑把候选门分成三类：
        `可跑·绿`  —— 命令真能跑，且现在**通过**
        `可跑·红`  —— 命令真能跑，但现在**不通过**（★ 这**正是缺口**，要写进档案）
        `不可用`   —— 命令起不来（不存在/超时）⇒ **不写进档案**，如实报告
   ⇒ 只有前两类进档案；第三类**不写**，并在档案里留一行注释说明缺什么。

3. **基线留档**。验收门的数字（`4 passed; 2 failed`）**没有参照就没有意义**。
   探跑同时把「改之前」的退出码与输出尾写进 `<audit>/自举基线.json`，
   于是交付后能说「改前 X / 改后 Y」，而不是只报一个孤零零的绿。

4. **探跑会留下构建产物**（`target/`、`node_modules/`、`.pytest_cache/`）。
   这是编译的必然产物，本模块**不清理**（清理比留下更危险），但会如实打印。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentscope_orchestrator import profile as prof_mod   # noqa: E402

# ═══════════════════════════════════════════════════════════════════
# 谱系表：清单文件 → 这个工程**自己**的构建/验收方式
#
# ★ 表是**数据**；下面的 `_识别()` 是**唯一**的通路。
#   新增生态 = 加一行；**不要在通路里加分支**。
# ═══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class 工程谱:
    清单: str                       # 仓库根的清单文件（存在才命中）
    语言: str
    构建: tuple[str, ...] = ()      # 构建门命令（空 = 该工程没有独立构建步）
    验收: tuple[str, ...] = ()      # 验收门命令（测试；空 = 未能确定）
    源码根: tuple[str, ...] = ()    # 源码索引根
    后缀: tuple[str, ...] = ()      # 源码 glob
    冻结标记: tuple[str, ...] = ()  # 在**源码文件里**搜这些标记，从标记到文件末冻结
    冻结文件: tuple[str, ...] = ()  # 这些 glob 命中的文件**整文件**冻结
    声明源: str = ""                # 命令从清单**自己声明**里读（""=用表里写死的）
    说明: str = ""


_工程谱: tuple[工程谱, ...] = (
    工程谱("Cargo.toml", "rust",
          构建=("cargo", "build"), 验收=("cargo", "test"),
          源码根=("src",), 后缀=("**/*.rs",),
          冻结标记=("#[cfg(test)]",),
          说明="cargo 真编译 + 真测试"),
    工程谱("go.mod", "go",
          构建=("go", "build", "./..."), 验收=("go", "test", "./..."),
          源码根=(".",), 后缀=("**/*.go",),
          冻结文件=("**/*_test.go",),
          说明="go 真编译 + 真测试"),
    工程谱("package.json", "js",
          源码根=("src",), 后缀=("**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"),
          冻结文件=("**/*.test.js", "**/*.test.ts", "**/*.spec.js", "**/*.spec.ts",
                    "**/__tests__/**/*.js", "**/__tests__/**/*.ts"),
          声明源="npm",
          说明="npm scripts（**工程自己声明**的命令优先）"),
    工程谱("pyproject.toml", "python",
          验收=("python", "-m", "pytest", "-q"),
          源码根=("src", "."), 后缀=("**/*.py",),
          冻结文件=("tests/**/test_*.py", "**/test_*.py", "**/*_test.py"),
          说明="pytest 真执行断言"),
    工程谱("setup.py", "python",
          验收=("python", "-m", "pytest", "-q"),
          源码根=(".",), 后缀=("**/*.py",),
          冻结文件=("tests/**/test_*.py", "**/test_*.py", "**/*_test.py"),
          说明="pytest 真执行断言"),
    工程谱("requirements.txt", "python",
          验收=("python", "-m", "pytest", "-q"),
          源码根=(".",), 后缀=("**/*.py",),
          冻结文件=("tests/**/test_*.py", "**/test_*.py", "**/*_test.py"),
          说明="pytest 真执行断言"),
    工程谱("Makefile", "make",
          源码根=(".",),
          声明源="make",
          说明="make（**只认 Makefile 里真的声明了的目标**）"),
    工程谱("CMakeLists.txt", "cpp",
          构建=("cmake", "-S", ".", "-B", "build"), 验收=("ctest", "--test-dir", "build"),
          源码根=("src", "."), 后缀=("**/*.c", "**/*.cc", "**/*.cpp", "**/*.h", "**/*.hpp"),
          说明="cmake 配置 + ctest"),
    工程谱("pom.xml", "java",
          构建=("mvn", "-q", "-DskipTests", "package"), 验收=("mvn", "-q", "test"),
          源码根=("src/main/java",), 后缀=("**/*.java",),
          冻结文件=("src/test/**/*.java",),
          说明="maven 编译 + 测试"),
    工程谱("build.gradle", "java",
          构建=("gradle", "build", "-x", "test"), 验收=("gradle", "test"),
          源码根=("src/main/java",), 后缀=("**/*.java",),
          冻结文件=("src/test/**/*.java",),
          说明="gradle 编译 + 测试"),
    工程谱("build.gradle.kts", "java",
          构建=("gradle", "build", "-x", "test"), 验收=("gradle", "test"),
          源码根=("src/main/java",), 后缀=("**/*.java", "**/*.kt"),
          冻结文件=("src/test/**/*.java", "src/test/**/*.kt"),
          说明="gradle(kts) 编译 + 测试"),
    工程谱("Gemfile", "ruby",
          验收=("bundle", "exec", "rspec"),
          源码根=("lib",), 后缀=("**/*.rb",),
          冻结文件=("spec/**/*_spec.rb",),
          说明="rspec"),
    工程谱("composer.json", "php",
          验收=("composer", "test"),
          源码根=("src",), 后缀=("**/*.php",),
          冻结文件=("tests/**/*Test.php",),
          说明="composer 脚本"),
    工程谱("mix.exs", "elixir",
          构建=("mix", "compile"), 验收=("mix", "test"),
          源码根=("lib",), 后缀=("**/*.ex", "**/*.exs"),
          冻结文件=("test/**/*_test.exs",),
          说明="mix 编译 + 测试"),
    工程谱("pubspec.yaml", "dart",
          验收=("dart", "test"),
          源码根=("lib",), 后缀=("**/*.dart",),
          冻结文件=("test/**/*_test.dart",),
          说明="dart test"),
    工程谱("tsconfig.json", "ts",
          源码根=("src",), 后缀=("**/*.ts", "**/*.tsx"),
          冻结文件=("**/*.test.ts", "**/*.spec.ts"),
          说明="仅识别（无独立验收命令；与 package.json 同时存在时后者优先）"),
)

# 清单文件**越靠前越优先**（同一仓库同时有 `package.json` 与 `tsconfig.json` 时，
# 用前者 —— 因为前者才声明了验收命令）。表序即优先级。
#
# ★ 为什么 `Makefile` 排在语言清单之后：Makefile 常是**包装层**
#   （`cargo test` 的别名），它自己声明的目标不如语言生态的标准命令可靠。


# ═══════════════════════════════════════════════════════════════════
# 排除：**不是源码**的目录（一个事实，一处声明，两处生效）
#
# ── 真跑抓出的产品缺陷（2026-10-15，在真 Rust 工程上自举）──
#   自动扫出来的冻结区是**两条**：
#       · src/lib.rs                                    ← 对
#       · .orchestrator/generic_backup/src/lib.rs       ← **工具自己的备份**
#   ★ 根因：扫描只按后缀找文件，**没排除"不是源码"的目录**。
#
#   ★★ 我起初断言"这会导致**每次运行都假红**"（备份在运行中被覆写）。
#      **齿探针②推翻了这个断言**：备份的冻结区与源码的冻结区**恰好相同**
#      （`begin` 标记之后的内容一致）⇒ 比不出差异 ⇒ **不会假红**。
#      ⇒ 如实纠正，**不留夸大的因由**。
#
#   ★★★ 但顺着这条线查下去，查到了**更基本、可证**的那个洞（= R25）：
#      `_冻结区取文` 对**文件不存在**返回 `None`，而 `_冻结区快照` 里是
#      `continue` —— **静默跳过**。而备份目录在**首次运行时还不存在** ⇒
#      那条声明**一点保护都没有，且没有任何人知道**。
#      （已修：`generic_goal._冻结区未取到()` 把它**报出来**，
#        并落进回执的 `frozen_regions_skipped`。）
#
#   ⇒ 本排除表要治的正是**这个**：把一个**不是判据**的东西
#     （工具自己的草稿空间）声明成"不可改的判据"，本身就是错的；
#     它还会占用冻结区名额、并让「冻结区逐字未变」这句话**失真**。
#
# ── 修法（全称，不枚举具体项目）──
#   ① 一张**通用排除表**（VCS / 构建产物 / 依赖 / 缓存 / 编辑器）；
#   ② 工具**自己的审计目录**（`.orchestrator` —— 备份与回执都住这里）；
#   ③ ★ **读工程自己声明的忽略规则**（`.gitignore` 顶层目录名）——
#      这是"工程自己说了什么"，与 `_从清单取命令` 同一口径，**不是我们猜**。
#   ⇒ 三处来源合成一个集合，**同时**用于：本模块的扫描，
#     以及写进档案的 `source.skip`（`repo_index` 按**路径分量名**过滤，
#     所以这一个声明把索引侧也一起管住了）。
# ═══════════════════════════════════════════════════════════════════
_通用排除: frozenset[str] = frozenset({
    # VCS
    ".git", ".hg", ".svn", ".bzr",
    # 构建产物
    "target", "build", "dist", "out", "bin", "obj", "_build", "coverage",
    ".next", ".nuxt", ".output", ".svelte-kit",
    # 依赖
    "node_modules", "vendor", "third_party", "deps", "Pods",
    # 虚拟环境 / 缓存
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", ".cache", ".parcel-cache", ".turbo",
    # 编辑器 / 工具
    ".idea", ".vscode", ".vs", ".gradle", ".mvn",
    # ★ 工具**自己**的审计目录（备份/回执住这里；它一定不是项目源码）
    ".orchestrator",
})


def _读忽略(root: Path) -> frozenset[str]:
    """读工程**自己声明的**忽略规则（`.gitignore` 顶层目录名）。

    ★ 只取**目录形态**的简单条目（`foo/` 或 `foo`，不含通配与取反）——
      复杂模式留给 git 自己，本模块**不假装**能完整实现 gitignore 语义。
      取不到 ⇒ 空集（保守：宁可不排除，也不误排真源码）。
    """
    名: set[str] = set()
    p = root / ".gitignore"
    if not p.is_file():
        return frozenset()
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        if any(ch in s for ch in "*?[]"):
            continue                       # 通配模式：不猜
        s = s.strip("/")
        if not s or "/" in s:
            continue                       # 多级路径：不猜
        名.add(s)
    return frozenset(名)


def _排除集(root: Path) -> frozenset[str]:
    return _通用排除 | _读忽略(root)


def _收集(root: Path, globs: tuple[str, ...], 排除: frozenset[str]) -> list[Path]:
    """按 glob 收文件，**排除**任何路径分量命中排除集的。

    ★ 这是本模块**唯一**的收集通路 —— 冻结区扫描与锚点猜测**共用**它，
      所以"排除"只写一次就同时管住两处（与 R22c 收成 `_落盘` 同一手法）。
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for g in globs:
        for p in root.glob(g):
            if not p.is_file():
                continue
            try:
                parts = p.relative_to(root).parts
            except ValueError:
                continue
            if any(x in 排除 for x in parts[:-1]):
                continue
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
    return sorted(out)


# ═══════════════════════════════════════════════════════════════════
# 读「工程自己声明的命令」（数据驱动：只有 `声明源` 非空时才走）
# ═══════════════════════════════════════════════════════════════════
def _从清单取命令(声明源: str, root: Path) -> dict[str, tuple[str, ...]]:
    """从清单**自己声明**里取命令。取不到 ⇒ 空 dict（**不猜**）。"""
    out: dict[str, tuple[str, ...]] = {}
    if 声明源 == "npm":
        p = root / "package.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:                     # noqa: BLE001 读不了就当没声明
            return out
        sc = (d or {}).get("scripts") or {}
        if isinstance(sc, dict):
            # ★ 只认**真声明了**的脚本；`npm test` 在没有 `test` 脚本时必红
            #   （`Missing script: "test"`）—— 那会是**假红的项目级门**。
            if sc.get("build"):
                out["构建"] = ("npm", "run", "build")
            if sc.get("test"):
                out["验收"] = ("npm", "test")
    elif 声明源 == "make":
        p = root / "Makefile"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:                     # noqa: BLE001
            return out
        # 顶层目标：行首非空白、`名字:` 形态
        目标 = set(re.findall(r"(?m)^([A-Za-z0-9_.\-]+)\s*:(?!=)", text))
        if "build" in 目标 or "all" in 目标:
            out["构建"] = ("make", "build" if "build" in 目标 else "all")
        if "test" in 目标:
            out["验收"] = ("make", "test")
        if "check" in 目标 and "验收" not in out:
            out["验收"] = ("make", "check")
    return out


# ═══════════════════════════════════════════════════════════════════
# 识别
# ═══════════════════════════════════════════════════════════════════
@dataclass
class 识别结果:
    root: Path
    谱: 工程谱 | None
    命中清单: list[str] = field(default_factory=list)
    构建: tuple[str, ...] = ()
    验收: tuple[str, ...] = ()
    命令来源: dict[str, str] = field(default_factory=dict)
    源码根: tuple[str, ...] = ()
    后缀: tuple[str, ...] = ()
    排除: frozenset[str] = frozenset()
    冻结候选: list[dict] = field(default_factory=list)
    跳过冻结: list[str] = field(default_factory=list)
    备注: list[str] = field(default_factory=list)

    @property
    def 可自举(self) -> bool:
        return self.谱 is not None and bool(self.构建 or self.验收)


def _文件数(root: Path, 后缀: tuple[str, ...], 上限: int = 4000) -> int:
    n = 0
    for g in 后缀:
        for _ in root.glob(g):
            n += 1
            if n >= 上限:
                return n
    return n


def _扫冻结(root: Path, 谱: 工程谱, 排除: frozenset[str],
            上限: int = 24) -> tuple[list[dict], list[str]]:
    """按谱系找**真的存在**的验收判据区域 ⇒ `frozen` 条目。**不猜、不造**。

    · `冻结标记` —— 在**源码文件**（谱.后缀 命中）里搜标记，从标记到文件末。
      典型：Rust 把 `#[cfg(test)] mod tests` 写在 `src/lib.rs` 里。
    · `冻结文件` —— 这些 glob 命中的文件**整文件**冻结。典型：测试独立成文件。
    · ★ `排除` —— **不是源码**的目录一律不看（否则会把工具自己的备份冻上，
      见 `_通用排除` 的因由：那会导致**每次运行都假红**）。
    ★ 命中数为 0 ⇒ 返回空列表（**不写空冻结区**：声明了却取不到，
      会让"判据不可改"变成一句空话，而 `_冻结区快照` 会静默跳过它）。
    """
    out: list[dict] = []
    跳过: list[str] = []

    def _相对(p: Path) -> str:
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            return p.as_posix()

    for g in 谱.冻结文件:
        for p in _收集(root, (g,), 排除):
            if len(out) >= 上限:
                跳过.append(f"{_相对(p)}（超出 {上限} 个冻结区上限）")
                continue
            rel = _相对(p)
            if any(x["path"] == rel for x in out):
                continue
            out.append({"path": rel, "whole": True})

    if 谱.冻结标记:
        for p in _收集(root, 谱.后缀, 排除):
            try:
                text = p.read_bytes().decode("utf-8", errors="replace")
            except OSError:
                continue
            for 标记 in 谱.冻结标记:
                if 标记 in text:
                    rel = _相对(p)
                    if any(x["path"] == rel for x in out):
                        continue
                    if len(out) >= 上限:
                        跳过.append(f"{rel}（超出 {上限} 个冻结区上限）")
                        continue
                    out.append({"path": rel, "begin": 标记})
                    break
    return out, 跳过


def 识别(root: Path) -> 识别结果:
    """扫仓库根：命中哪个谱系、它自己声明了什么命令、判据区域在哪。"""
    root = Path(root).resolve()
    r = 识别结果(root=root, 谱=None)
    if not root.is_dir():
        r.备注.append(f"目录不存在：{root}")
        return r

    命中 = [s for s in _工程谱 if (root / s.清单).is_file()]
    r.命中清单 = [s.清单 for s in 命中]
    if not 命中:
        r.备注.append("仓库根没有任何**认识的**清单文件（Cargo.toml / package.json / "
                      "pyproject.toml / go.mod / Makefile …）⇒ 无法确定这个工程怎么构建、"
                      "怎么验收。**不生成门**（绝不用恒真占位门冒充判据）。")
        return r

    # 表序即优先级；但**声明了命令的清单优先于只被识别的清单**：
    # 若 `tsconfig.json` 在前而 `package.json` 也在，后者才声明了验收命令。
    谱 = 命中[0]
    for s in 命中:
        if _从清单取命令(s.声明源, root) if s.声明源 else {}:
            谱 = s
            break

    r.谱 = 谱
    r.源码根 = 谱.源码根
    r.后缀 = 谱.后缀
    r.排除 = _排除集(root)

    声明 = _从清单取命令(谱.声明源, root) if 谱.声明源 else {}
    r.构建 = 声明.get("构建") or 谱.构建
    r.验收 = 声明.get("验收") or 谱.验收
    r.命令来源["构建"] = ("清单自声明" if 声明.get("构建") else
                       ("谱系表" if 谱.构建 else "无"))
    r.命令来源["验收"] = ("清单自声明" if 声明.get("验收") else
                       ("谱系表" if 谱.验收 else "**未确定**"))

    if len(命中) > 1:
        r.备注.append("同时命中多个清单：" + "、".join(r.命中清单)
                      + f"；采用 `{谱.清单}`（{r.命令来源.get('验收')}）。")
    if 谱.说明:
        r.备注.append(f"谱系：{谱.语言} —— {谱.说明}")

    r.冻结候选, r.跳过冻结 = _扫冻结(root, 谱, r.排除)
    if not r.冻结候选:
        r.备注.append("**未找到任何验收判据区域** ⇒ 档案不写 `frozen`。"
                      "这意味着「判据不得由被判方改动」这条保证**本工程没有**"
                      "（R23 的洞仍然敞着），请人工确认测试放在哪里。")
    return r


# ═══════════════════════════════════════════════════════════════════
# 探跑：候选门**当场真跑一遍**（R21 的直接应用）
# ═══════════════════════════════════════════════════════════════════
_可跑绿 = "可跑·绿"
_可跑红 = "可跑·红"
_不可用 = "不可用"


def 探命令(cmd, cwd: Path, timeout: int) -> dict:
    """真跑一条命令，三分类。**只看"能不能跑"，不判"该不该过"。**"""
    t0 = time.time()
    try:
        cp = subprocess.run([str(x) for x in cmd], cwd=str(cwd),
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        return {"状态": _不可用, "因由": f"命令不存在（{e.filename or cmd[0]}）",
                "秒": round(time.time() - t0, 1)}
    except subprocess.TimeoutExpired:
        return {"状态": _不可用, "因由": f"超时（>{timeout}s）",
                "秒": round(time.time() - t0, 1)}
    except OSError as e:
        return {"状态": _不可用, "因由": f"起不来（{type(e).__name__}: {e}）",
                "秒": round(time.time() - t0, 1)}
    尾 = ((cp.stdout or "") + (cp.stderr or "")).strip().splitlines()
    return {"状态": _可跑绿 if cp.returncode == 0 else _可跑红,
            "退出码": cp.returncode,
            "尾": [x for x in 尾 if x.strip()][-6:],
            "秒": round(time.time() - t0, 1)}


def 探跑(r: 识别结果, timeout: int) -> dict:
    """按「先构建后验收」真跑候选门 ⇒ 探跑报告。"""
    报 = {"构建": None, "验收": None}
    if r.构建:
        报["构建"] = {"cmd": list(r.构建), **探命令(r.构建, r.root, timeout)}
    if r.验收:
        报["验收"] = {"cmd": list(r.验收), **探命令(r.验收, r.root, timeout)}
    return 报


def _可入档(探: dict | None) -> bool:
    """只有**真能跑**的门才入档。`不可用` 一律不入档（防项目级假红）。"""
    return bool(探) and 探.get("状态") in (_可跑绿, _可跑红)


# ═══════════════════════════════════════════════════════════════════
# 生成档案（手写 YAML：可读、带因由，比 dump 出来的稳定）
# ═══════════════════════════════════════════════════════════════════
def _yaml_str(s: str) -> str:
    """安全标量：需要引号就加双引号（本工具生成的字符串都很简单）。"""
    s = str(s)
    if s and re.fullmatch(r"[A-Za-z0-9_.\-/]+", s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _门(name: str, cmd, timeout: int) -> list[str]:
    return ["  - name: " + _yaml_str(name),
            "    type: compile",
            "    cmd: [" + ", ".join(_yaml_str(x) for x in cmd) + "]",
            '    cwd: "{repo}"',
            "    expect_exit: 0",
            f"    timeout: {timeout}"]


def 生成档案(r: 识别结果, 探: dict, timeout: int, *, 已探跑: bool = True) -> str:
    """把识别 + 探跑结果落成一份档案文本。

    ★ `已探跑` 决定"入档口径"（自检 ⑧ 抓出来的语义缺口）：
        `True` （默认）—— **只写探跑确认「真能跑」的门**。
                          起不来的门**不写**：它每次都会红，那是**项目级假红**，
                          比没有门更坏（把真问题淹掉）。
        `False`（`--no-probe`）—— 把谱系表声明的门**照写**，但**明标「未探跑验证」**。
                          否则这个开关就等于"什么都不生成"，毫无意义。
    """
    名 = r.root.name or "project"
    L: list[str] = []
    A = L.append
    A("# ═══════════════════════════════════════════════════════════════")
    A("# 项目档案（**自动生成** —— `agentscope_orchestrator.bootstrap`）")
    A("#")
    A("# 这份档案是「换项目」的全部代价。自动生成只做了**能机器确定**的部分：")
    A("#   · 判据门（`gates`）= 这个工程**自己**的构建/验收命令，且**已当场真跑过**")
    A("#   · 冻结区（`frozen`）= 仓库里**真实存在**的验收判据区域")
    A("# 自动生成**不能**确定的部分，档案里会留一行 `# ★ 待人工` —— 请人工补齐。")
    A("#")
    A("# ★ 生成时间：" + time.strftime("%Y-%m-%d %H:%M:%S"))
    A("# ★ 工程目录：" + str(r.root))
    A("# ★ 识别到的清单：" + ("、".join(r.命中清单) or "（无）"))
    A("# ★ 谱系：" + (r.谱.语言 + " —— " + r.谱.说明 if r.谱 else "未识别"))
    if not 已探跑:
        A("# ★★ **未探跑验证**（`--no-probe`）：下面每条门都**没跑过**，"
          "可能一跑就红。")
    A("# ═══════════════════════════════════════════════════════════════")
    A("")
    A("name: " + _yaml_str(名))
    A("")
    A("repo_root: .")
    A("audit_dir: .orchestrator")
    # ★ 产物/工作区**住在工程自己的 `.orchestrator/` 里**（不混进工具目录）。
    #   真跑抓出：不声明时它们回落到**工具自己**的 `products/`、`work/`
    #   ⇒ 同一个工具跑多个工程时，产物全堆在一处、分不清谁是谁；
    #     而用户在自己的工程里根本找不到产物。
    #   ★ 相对路径以**档案所在目录**为基准（`profile.py` 的口径）。
    A("work_dir: .orchestrator/work")
    A("products_dir: .orchestrator/products")
    A("")
    A("# ── 判据门：项目**自己的**验收标准（独立来源 · 非模型给）────────────")
    A("# ★ R21：编排器在主树交付**之前**必定跑一遍这组门。")
    A("#   所以这组门必须是**真的**：下面每条都经探跑确认「命令真能跑」。")
    A("gates:")
    有门 = False
    候选 = (("构建", r.构建, "构建_" + (r.谱.语言 if r.谱 else "proj")),
            ("验收", r.验收, "验收_" + (r.谱.语言 if r.谱 else "proj")))
    for 类, 命令, 门名 in 候选:
        t = 探.get(类)
        if 已探跑:
            if not _可入档(t):
                A(f"  # ★ {类}门**未入档**："
                  + (t or {}).get("因由", "该工程谱系未声明此命令"))
                if 类 == "验收":
                    A("  #    ⇒ 本工程**没有独立验收来源**，主树交付只能信模型"
                      "自己写的门。")
                    A("  #      请人工在此补一条真能跑的验收命令（否则 R21 的洞仍然敞着）。")
                continue
            A(f"  # 探跑：{t['状态']}（退出码 {t.get('退出码')}，{t.get('秒')}s）")
            if 类 == "验收" and t["状态"] == _可跑红:
                A("  #   ★ `可跑·红` 不是错误 —— 它正是**当前缺口**（改之前本来就不过）。")
        else:
            if not 命令:
                A(f"  # ★ {类}门：该工程谱系未声明此命令。")
                continue
            A(f"  # ★★ 未探跑验证：{' '.join(命令)}")
            有门 = True
            L += _门(门名, 命令, timeout)
            continue
        有门 = True
        L += _门(门名, 命令, timeout)
    if not 有门:
        A("  # ★★ 一条门都没能确定 ⇒ 本档案无判据门。")
        A("  #    工具仍可启动，但**不会跑任何项目级判据**（不用恒真门冒充）。")
    A("")

    A("# ── 冻结区：判据不得由被判方改动（R23）──────────────────────────")
    A("#   验收测试常与实现**同文件** ⇒ 模型改测试就能让自己过。")
    A("#   下面每条都是**仓库里真实存在**的判据区域（自动扫描命中才写）。")
    if r.冻结候选:
        A("frozen:")
        for f in r.冻结候选:
            if f.get("whole"):
                A("  - path: " + _yaml_str(f["path"]))
                A("    whole: true")
            else:
                A("  - path: " + _yaml_str(f["path"]))
                A("    begin: " + _yaml_str(f["begin"]))
    else:
        A("# ★ 待人工：本工程**未找到**验收判据区域 ⇒ 不写 `frozen`。")
        A("#   若测试在别处（或被内联在源码里），请人工补：")
        A("#     frozen:")
        A("#       - path: <相对路径>")
        A("#         begin: <从哪个标记起>      # 或 whole: true")
    for x in r.跳过冻结:
        A("#   （跳过：" + x + "）")
    A("")

    A("# ── 源码索引：供结构键可达性判定 ──────────────────────────────")
    if r.源码根:
        A("source:")
        A("  roots: [" + ", ".join(_yaml_str(x) for x in r.源码根) + "]")
        if r.后缀:
            A("  globs: [" + ", ".join(_yaml_str(x) for x in r.后缀) + "]")
        # ★ `skip` 是**路径分量名**集合（`repo_index` 按 `s in p.parts` 过滤）。
        #   把同一套排除写进档案 ⇒ 索引侧与自举扫描侧**口径一致**，
        #   而不是两边各写一份（那必然会分叉）。
        if r.排除:
            排 = sorted(r.排除)
            A("  # ★ 不是源码的目录（构建产物/依赖/缓存/工具自己的审计目录/"
              "`.gitignore` 声明的）。索引与自举扫描共用这一份声明。")
            A("  skip: [" + ", ".join(_yaml_str(x) for x in 排) + "]")
    else:
        A("# ★ 待人工：未确定源码根。")
        A("# source:")
        A('#   roots: ["src"]')
    A("")

    A("# ── 主树锚点：跑工程级门时锚定哪个单元（**必填**，自动生成填最可能的一个）──")
    A("#   若不对，请人工改成你真正要它改的那个文件。")
    A("anchors:")
    A("  main: " + _yaml_str(_猜锚点(r)))
    A("")
    return "\n".join(L) + "\n"


def _猜锚点(r: 识别结果) -> str:
    """猜主锚点 = **真存在**的源码文件里最像"主文件"的那个。猜不到 ⇒ 空。

    ★ 与冻结区扫描**共用** `_收集`（同一套排除）—— 否则可能猜到备份文件。
    """
    if not r.后缀:
        return ""
    候选 = _收集(r.root, r.后缀, r.排除 or _排除集(r.root))
    if not 候选:
        return ""
    # 优先级：路径里有 src/ 的；文件名含 main/lib/index/mod 的；其余按路径序
    优先词 = ("main", "lib", "index", "mod", "__init__", "app")

    def 键(p: Path) -> tuple:
        rel = p.relative_to(r.root).as_posix()
        名 = p.stem.lower()
        return (0 if rel.startswith("src/") else 1,
                0 if any(w in 名 for w in 优先词) else 1,
                len(rel), rel)

    return sorted(候选, key=键)[0].relative_to(r.root).as_posix()


# ═══════════════════════════════════════════════════════════════════
# 自举：识别 → 探跑 → 写档案 → 写基线
# ═══════════════════════════════════════════════════════════════════
def 自举(root: Path, *, 探跑开关: bool = True, timeout: int = 900,
          out: Path | None = None, 覆盖: bool = False,
          log=print) -> dict:
    """把一个工程目录变成一份可跑的档案。返回回执（含全部证据）。"""
    root = Path(root).resolve()
    r = 识别(root)
    out = Path(out) if out else (root / prof_mod.PROFILE_FILENAME)

    log(f"[自举] 工程目录：{root}")
    log(f"[自举] 识别的清单：{'、'.join(r.命中清单) or '（无）'}"
        + (f"；谱系 {r.谱.语言}" if r.谱 else ""))
    for x in r.备注:
        log("[自举] · " + x)

    if not r.可自举:
        log("[自举] ★★ 无法自举 ⇒ **不写档案**（不猜、不用恒真门冒充）。")
        return {"ok": False, "识别": r, "档案": None, "探跑": None,
                "因由": "工程不可识别或无可用判据命令"}

    if out.is_file() and not 覆盖:
        log(f"[自举] 档案已存在 ⇒ 不覆盖（要重生成请 --overwrite）：{out}")
        return {"ok": True, "识别": r, "档案": out, "探跑": None,
                "因由": "已存在"}

    探: dict = {}
    if 探跑开关:
        log(f"[自举] 探跑候选门（真跑一遍，超时 {timeout}s）——"
            "**只有真能跑的门才入档**（R21：项目级假红比没有门更坏）")
        探 = 探跑(r, timeout)
        for k in ("构建", "验收"):
            t = 探.get(k)
            if not t:
                continue
            log(f"[自举]   {k}：{' '.join(t['cmd'])}  ⇒ {t['状态']}"
                + (f"（退出码 {t.get('退出码')}，{t.get('秒')}s）" if "退出码" in t else "")
                + (f" 因由：{t['因由']}" if t.get("因由") else ""))
            for line in (t.get("尾") or [])[-3:]:
                log(f"[自举]       │ {line[:160]}")
        log("[自举] ★ 探跑会留下构建产物（target/、node_modules/、.pytest_cache/ 等）"
            "—— 本工具**不清理**（清理比留下更危险）。")
    else:
        log("[自举] 跳过探跑（--no-probe）⇒ 门**未经验证**，可能一跑就红。"
            "正式使用不建议。")

    text = 生成档案(r, 探, timeout, 已探跑=探跑开关)
    # ★ R22 口径：落盘走**字节**（不翻译行尾、不丢字节）
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text.encode("utf-8"))
    log(f"[自举] 档案已写：{out}（{len(text.encode('utf-8'))} 字节）")

    # 基线留档：验收门的数字**没有参照就没有意义**
    基线 = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(root),
        "识别": {"清单": r.命中清单, "语言": (r.谱.语言 if r.谱 else ""),
                 "构建": list(r.构建), "验收": list(r.验收),
                 "命令来源": r.命令来源},
        "探跑": 探,
        "冻结区": r.冻结候选,
        "备注": r.备注,
    }
    bd = root / ".orchestrator"
    try:
        bd.mkdir(parents=True, exist_ok=True)
        (bd / "自举基线.json").write_bytes(
            json.dumps(基线, ensure_ascii=False, indent=2).encode("utf-8"))
        log(f"[自举] 基线已写：{bd / '自举基线.json'}（交付后可与它对比「改前/改后」）")
    except OSError as e:                      # noqa: BLE001 基线写不了不阻断
        log(f"[自举] 基线写盘失败（不阻断）：{e}")

    入档 = [k for k in ("构建", "验收") if _可入档(探.get(k))]
    log(f"[自举] 入档门：{'、'.join(入档) or '（无）'}"
        f"；冻结区 {len(r.冻结候选)} 个")
    if "验收" not in 入档:
        log("[自举] ★★ 验收门未入档 ⇒ 本工程**没有独立验收来源**，"
            "主树交付只能信模型自己写的门（R21 的洞仍然敞着）。")
    return {"ok": True, "识别": r, "档案": out, "探跑": 探, "基线": 基线,
            "入档门": 入档, "因由": "已生成"}


def 找到档案(root: Path) -> Path | None:
    """工程里已有一份可用档案吗？（沿 `profile.py` 的同一约定）"""
    p = Path(root).resolve() / prof_mod.PROFILE_FILENAME
    if p.is_file():
        return p
    c = Path(root).resolve() / "config" / "profile.yml"
    return c if c.is_file() else None


def 确保档案(root: Path, *, 探跑开关: bool | None = None, timeout: int = 900,
             log=print) -> tuple[Path | None, dict]:
    """有则用、无则自举。返回 `(档案路径, 自举回执)`。**一句话入口调这个。**

    ★ `探跑开关=None`（默认）⇒ 读环境变量 `ORCH_BOOT_NO_PROBE`：
      一句话入口用 `--no-probe` 时置它。这样那个开关**真的**生效，
      而不是"CLI 上写着、底层没人读"（那正是"没跑看起来像绿"的另一种形态）。
    """
    root = Path(root).resolve()
    if 探跑开关 is None:
        探跑开关 = not os.environ.get("ORCH_BOOT_NO_PROBE")
    have = 找到档案(root)
    if have is not None:
        log(f"[自举] 工程自带档案 ⇒ 直接用（不自举）：{have}")
        return have, {"ok": True, "因由": "已有档案", "档案": have}
    rec = 自举(root, 探跑开关=探跑开关, timeout=timeout, log=log)
    return (Path(rec["档案"]) if rec.get("档案") else None), rec


# ═══════════════════════════════════════════════════════════════════
# CLI：只看识别结果 / 只生成档案（都不跑目标）
# ═══════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="档案自举：把一个陌生工程目录变成一份可跑的档案")
    ap.add_argument("project", nargs="?", default="", help="工程目录")
    ap.add_argument("--out", default=None, help="档案输出路径（默认 <工程>/orchestrator.profile.yml）")
    ap.add_argument("--overwrite", action="store_true", help="档案已存在时覆盖")
    ap.add_argument("--no-probe", dest="probe", action="store_false",
                    help="跳过探跑（不建议：门未经验证）")
    ap.add_argument("--timeout", type=int, default=900, help="单条门探跑超时（秒）")
    ap.add_argument("--dry", action="store_true", help="只识别，不写档案")
    ap.add_argument("--selftest", action="store_true", help="跑纯机器自检门")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    if not a.project:
        ap.error("需要 <工程目录>（或用 --selftest）")

    if a.dry:
        r = 识别(Path(a.project))
        print(f"工程目录：{r.root}")
        print(f"命中清单：{'、'.join(r.命中清单) or '（无）'}")
        if r.谱:
            print(f"谱系：{r.谱.语言} —— {r.谱.说明}")
            print(f"构建：{' '.join(r.构建) or '（无）'}  [{r.命令来源.get('构建')}]")
            print(f"验收：{' '.join(r.验收) or '（无）'}  [{r.命令来源.get('验收')}]")
        print(f"冻结区候选（{len(r.冻结候选)}）：")
        for f in r.冻结候选:
            print("  · " + f["path"] + ("（整文件）" if f.get("whole")
                                       else f"（`{f['begin']}` 起）"))
        for x in r.跳过冻结:
            print("  （跳过）" + x)
        print(f"建议锚点：{_猜锚点(r) or '（无）'}")
        for x in r.备注:
            print("备注：" + x)
        return 0 if r.可自举 else 1

    rec = 自举(Path(a.project), 探跑开关=a.probe, timeout=a.timeout,
               out=Path(a.out) if a.out else None, 覆盖=a.overwrite)
    return 0 if rec.get("ok") else 1


# ═══════════════════════════════════════════════════════════════════
# 纯机器自检（不碰网络、不跑构建）
# ═══════════════════════════════════════════════════════════════════
def _selftest() -> int:
    import shutil
    import tempfile

    fails: list[str] = []
    N = 33

    def 造(文件: dict[str, str]) -> Path:
        d = Path(tempfile.mkdtemp(prefix="boot_"))
        for k, v in 文件.items():
            p = d / k
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(v.encode("utf-8"))
        return d

    # ① 不认识 ⇒ **不生成任何门**（绝不用恒真门冒充）
    d = 造({"readme.txt": "hi\n"})
    try:
        r = 识别(d)
        if r.可自举:
            fails.append("① 无清单文件的目录竟可自举（应用恒真门冒充）")
        if r.谱 is not None:
            fails.append("① 无清单文件竟识别出谱系")
        if not any("不生成门" in x for x in r.备注):
            fails.append("① 未如实说明「不生成门」")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ② Rust：识别 cargo 构建/验收 + `#[cfg(test)]` 冻结区
    d = 造({"Cargo.toml": "[package]\nname='x'\n",
            "src/lib.rs": "pub fn a() {}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {}\n}\n"})
    try:
        r = 识别(d)
        if r.谱 is None or r.谱.语言 != "rust":
            fails.append(f"② Cargo.toml 未识别为 rust（得 {r.谱 and r.谱.语言}）")
        if list(r.构建) != ["cargo", "build"] or list(r.验收) != ["cargo", "test"]:
            fails.append(f"② cargo 命令不对：{r.构建} / {r.验收}")
        if len(r.冻结候选) != 1 or r.冻结候选[0].get("begin") != "#[cfg(test)]":
            fails.append(f"② `#[cfg(test)]` 冻结区未识别：{r.冻结候选}")
        if r.冻结候选 and r.冻结候选[0]["path"] != "src/lib.rs":
            fails.append(f"② 冻结区路径不对：{r.冻结候选[0]['path']}")
        if _猜锚点(r) != "src/lib.rs":
            fails.append(f"② 锚点猜测不对：{_猜锚点(r)}")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ③ Python：pytest + 独立测试文件整文件冻结
    d = 造({"pyproject.toml": "[project]\nname='x'\n",
            "src/x.py": "def a():\n    return 1\n",
            "tests/test_x.py": "def test_a():\n    assert a() == 1\n"})
    try:
        r = 识别(d)
        if r.谱 is None or r.谱.语言 != "python":
            fails.append("③ pyproject.toml 未识别为 python")
        if list(r.验收) != ["python", "-m", "pytest", "-q"]:
            fails.append(f"③ pytest 命令不对：{r.验收}")
        if not any(f.get("whole") and "test_x.py" in f["path"] for f in r.冻结候选):
            fails.append(f"③ 独立测试文件未整文件冻结：{r.冻结候选}")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ④ ★ 清单**自己声明的命令优先于**谱系表猜的（防 npm 假红）
    d = 造({"package.json": json.dumps({"scripts": {"test": "node t.js"}}),
            "t.js": "process.exit(0)\n"})
    try:
        r = 识别(d)
        if list(r.验收) != ["npm", "test"]:
            fails.append(f"④ package.json 声明的 test 未被采用：{r.验收}")
        if r.命令来源.get("验收") != "清单自声明":
            fails.append(f"④ 命令来源未标为「清单自声明」：{r.命令来源}")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑤ ★★ 反向：package.json **没有** test 脚本 ⇒ **不得**造出 `npm test`
    #    （否则每次跑都 `Missing script: "test"` ⇒ **项目级假红**）
    d = 造({"package.json": json.dumps({"scripts": {"build": "echo b"}})})
    try:
        r = 识别(d)
        if r.验收:
            fails.append(f"⑤ package.json 没有 test 脚本，却造出了验收门 {r.验收}"
                         "（会造成项目级假红）")
        if r.命令来源.get("验收") != "**未确定**":
            fails.append(f"⑤ 未如实标注「验收未确定」：{r.命令来源}")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑥ Makefile：只认**真声明了**的目标
    d = 造({"Makefile": "build:\n\techo b\ntest:\n\techo t\n"})
    try:
        r = 识别(d)
        if list(r.构建) != ["make", "build"] or list(r.验收) != ["make", "test"]:
            fails.append(f"⑥ Makefile 目标识别不对：{r.构建} / {r.验收}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    d = 造({"Makefile": "all:\n\techo a\n"})
    try:
        r = 识别(d)
        if r.验收:
            fails.append(f"⑥ Makefile 无 test 目标却造出验收门：{r.验收}")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑦ 探跑三分类（真跑真命令，不碰网络）
    d = 造({})
    try:
        ok = 探命令([sys.executable, "-c", "print('hi')"], d, 60)
        if ok["状态"] != _可跑绿:
            fails.append(f"⑦ 退出码 0 的命令未判「可跑·绿」：{ok}")
        bad = 探命令([sys.executable, "-c", "import sys; sys.exit(3)"], d, 60)
        if bad["状态"] != _可跑红 or bad.get("退出码") != 3:
            fails.append(f"⑦ 退出码 3 的命令未判「可跑·红」：{bad}")
        miss = 探命令(["这个命令肯定不存在_zzz"], d, 60)
        if miss["状态"] != _不可用:
            fails.append(f"⑦ 不存在的命令未判「不可用」：{miss}")
        # ★ 关键：不可用的门**不得入档**
        if _可入档(miss) or _可入档(None):
            fails.append("⑦ 「不可用」的门竟被允许入档（会造成项目级假红）")
        if not _可入档(bad):
            fails.append("⑦ 「可跑·红」应允许入档（**那正是缺口**）")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑧ ★★★ 生成 → 装载 → 门真能跑（**闭环**，不是看文本）
    d = 造({"Cargo.toml": "[package]\nname='x'\n",
            "src/lib.rs": "pub fn a() {}\n\n#[cfg(test)]\nmod tests {}\n"})
    try:
        rec = 自举(d, 探跑开关=False, log=lambda *a, **k: None)
        if not rec.get("ok"):
            fails.append(f"⑧ 自举失败：{rec.get('因由')}")
        else:
            文本 = Path(rec["档案"]).read_bytes()
            if b"\r\n" in 文本:
                fails.append("⑧ 档案落盘含 CRLF ⇒ 违反 R22（落盘必须字节级）")
            if "未探跑验证".encode("utf-8") not in 文本:
                fails.append("⑧ `--no-probe` 生成的档案**未明标「未探跑验证」**"
                             "（那会让人误以为门已被确认）")
            # ★ 生成文本里必须是 `{repo}` 占位符（装载时会被替换成真路径）。
            #   查**装载后**的档案是查不到的 —— 那时它已经是绝对路径了
            #   （我自己第一版就查错了层，被自检抓出来）。
            if b'cwd: "{repo}"' not in 文本:
                fails.append("⑧ 生成的档案文本里门 cwd 未用 `{repo}` 占位符"
                             "（换机器/挪目录就会失效）")
            old = prof_mod.get_profile()
            try:
                p = prof_mod.load_profile(rec["档案"])
                if p.repo_root.resolve() != d.resolve():
                    fails.append(f"⑧ 装载后 repo_root 不对：{p.repo_root}")
                if not p.gates:
                    fails.append("⑧ `--no-probe` 生成的档案装载后**没有门**"
                                 "（那个开关就白设了）")
                名s = [g.get("name") for g in p.gates]
                if "验收_rust" not in 名s:
                    fails.append(f"⑧ 生成的档案缺验收门：{名s}")
                for g in p.gates:
                    # ★ 装载后 `{repo}` **应当已被替换**成仓库根
                    if Path(str(g.get("cwd"))).resolve() != d.resolve():
                        fails.append(f"⑧ 装载后门 cwd 未解析到仓库根：{g.get('cwd')}")
                    if not g.get("cmd"):
                        fails.append(f"⑧ 门无命令：{g}")
                if not p.frozen:
                    fails.append("⑧ 生成的档案缺 frozen（`#[cfg(test)]` 明明存在）")
            finally:
                prof_mod.set_profile(old)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑧b ★★★ 探跑口径：**只把真能跑的门入档**（不依赖任何工具链 —— 命令自造）
    d = 造({})
    try:
        r = 识别结果(root=d, 谱=_工程谱[0])
        r.构建 = (sys.executable, "-c", "print(1)")          # 真能跑
        r.验收 = ("这个命令肯定不存在_zzz",)                    # 起不来
        探 = 探跑(r, 60)
        if 探["构建"]["状态"] != _可跑绿:
            fails.append(f"⑧b 真能跑的命令未判绿：{探['构建']}")
        if 探["验收"]["状态"] != _不可用:
            fails.append(f"⑧b 不存在的命令未判不可用：{探['验收']}")
        文 = 生成档案(r, 探, 60, 已探跑=True)
        if "构建_rust" not in 文:
            fails.append("⑧b 真能跑的构建门**没入档**")
        if "验收_rust" in 文:
            fails.append("⑧b ★ 起不来的验收门竟入档了 ⇒ 每次都会红（项目级假红）")
        if "未入档" not in 文:
            fails.append("⑧b 未入档的门**未在档案里留痕**（人看不到缺什么）")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑨ ★ 不可识别 ⇒ 不写档案（不是写一份空档案）
    d = 造({"nothing.txt": "x\n"})
    try:
        rec = 自举(d, 探跑开关=False, log=lambda *a, **k: None)
        if rec.get("ok"):
            fails.append("⑨ 不可识别的工程竟报自举成功")
        if (d / prof_mod.PROFILE_FILENAME).exists():
            fails.append("⑨ 不可识别却写了档案（应在无门时**不写**）")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑩ 已存在档案 ⇒ 不覆盖（幂等）
    d = 造({"Cargo.toml": "[package]\nname='x'\n"})
    try:
        (d / prof_mod.PROFILE_FILENAME).write_bytes("name: 手工\n".encode("utf-8"))
        有, rec = 确保档案(d, 探跑开关=False, log=lambda *a, **k: None)
        if rec.get("因由") != "已有档案":
            fails.append(f"⑩ 已有档案时未直接采用：{rec.get('因由')}")
        if (d / prof_mod.PROFILE_FILENAME).read_bytes() != "name: 手工\n".encode("utf-8"):
            fails.append("⑩ 已有档案被覆盖了（应不覆盖）")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑪ ★★★★★ 排除：**工具自己的备份目录不得被当成源码**（真跑抓出的缺陷）
    #    在真 Rust 工程上自举时，冻结区扫出了
    #    `.orchestrator/generic_backup/src/lib.rs` —— 工具自己的备份。
    #    后果是**每次运行都假红**：冻结区快照在任务开始取，而运行中备份会被覆写
    #    ⇒ 必然报「判据被改动」⇒ 回滚 + 不交付 ⇒ 自动档案在跑过第二次的工程上不可用。
    d = 造({"Cargo.toml": "[package]\nname='x'\n",
            "src/lib.rs": "pub fn a() {}\n\n#[cfg(test)]\nmod tests {}\n",
            ".orchestrator/generic_backup/src/lib.rs": "备份\n\n#[cfg(test)]\n",
            "target/debug/build/生成.rs": "#[cfg(test)]\n",
            "node_modules/包/索引.rs": "#[cfg(test)]\n"})
    try:
        r = 识别(d)
        路径s = [f["path"] for f in r.冻结候选]
        if 路径s != ["src/lib.rs"]:
            fails.append(f"⑪ ★ 排除失效：冻结候选 = {路径s}（应只有 src/lib.rs）")
        if _猜锚点(r) != "src/lib.rs":
            fails.append(f"⑪ ★ 排除失效：锚点猜到了 {_猜锚点(r)}")
        # 排除集必须写进档案（索引侧共用同一份声明）
        rec = 自举(d, 探跑开关=False, log=lambda *a, **k: None)
        文 = Path(rec["档案"]).read_text(encoding="utf-8")
        if "skip:" not in 文 or ".orchestrator" not in 文:
            fails.append("⑪ 排除集**未写进档案**的 `source.skip` ⇒ 索引侧仍会把"
                         "备份目录当源码")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑪b ★ 读工程**自己声明的**忽略规则（`.gitignore` 顶层目录名）
    d = 造({"Cargo.toml": "[package]\nname='x'\n",
            "src/lib.rs": "#[cfg(test)]\n",
            ".gitignore": "# 注释\n供应商代码/\n*.log\n!保留/\n嵌套/深/路径\n",
            "供应商代码/三方.rs": "#[cfg(test)]\n",
            "保留/要看的.rs": "#[cfg(test)]\n"})
    try:
        排 = _读忽略(d)
        if "供应商代码" not in 排:
            fails.append(f"⑪b `.gitignore` 里的目录未被读进排除集：{sorted(排)}")
        if "*.log" in 排:
            fails.append("⑪b 通配模式被当成目录名了（不猜：应跳过通配）")
        # ★ 判据必须写**全**：取反条目被误收时，进集合的是 `!保留`（带叹号），
        #   不是 `保留`。我第一版查的是 `"保留" in 排` ⇒ **恒不成立** ⇒ **无齿**
        #   （被齿探针⑤抓出来）。⇒ 正确的判据是「**不得有任何 `!` 开头的条目**」。
        if any(x.startswith("!") for x in 排):
            fails.append(f"⑪b 取反条目（`!保留/`）被当成排除了 ⇒ 方向反了：{sorted(排)}")
        if "保留" in 排:
            fails.append("⑪b 取反条目的名字被当成目录名了")
        if "嵌套" in 排:
            fails.append("⑪b 多级路径被当成顶层目录名了（不猜）")
        r = 识别(d)
        路径s = [f["path"] for f in r.冻结候选]
        if "供应商代码/三方.rs" in 路径s:
            fails.append("⑪b `.gitignore` 声明的目录仍被当成源码")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    if fails:
        print(f"bootstrap 自检：{N - len(fails)}/{N} 红")
        for x in fails:
            print("  ✗", x)
        return 1
    print(f"bootstrap 自检：{N}/{N} 全绿（谱系表数据驱动 · 清单自声明优先 · "
          f"不可用门不入档 · 生成→装载→门闭环 · 不可识别不写档案）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
