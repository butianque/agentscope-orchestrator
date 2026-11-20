# -*- coding: utf-8 -*-
"""`orch-init` —— **给任意文件夹装上档案**，让它能被编排器接管。

★★★ 为什么必须有这条（用户原话）：

> 「**那现在距离我要的任意项目启动自动建知识图谱，根据我定的工程目标自动定计划，
>   自动派发任务，自动跑，自动生成判据，生成门监控代码质量…的可部署程序交付还差多少？**」

差的就是这一环：`orch-goal` / `orch-run` / `orch-plan` 的一切都从**档案**里来
（`REPO_ROOT = _prof.repo_root`），而档案是**人手写的**。
⇒ 「任意项目」在交付上等于「你得先手写一份 yaml」。这不是程序，是文档作业。

本命令把那一环补上：**扫一遍那个文件夹 → 认出它是什么项目 → 生成档案**。
★ 它**不留任何项目信息在编排器源码里**（档案属于项目，工具不认识任何具体项目）。

用法：
    orch-init <文件夹>                 → 在该文件夹里生成 orchestrator.profile.yml
    orch-init <文件夹> --出 <路径>      → 写到别处（便于只读文件夹）
    orch-init <文件夹> --覆盖           → 已存在也重写
    orch-init <文件夹> --只报           → 只报探测结果，不写文件

★ 诚实边界（口径 92）：
  · 语言/构建工具/源码根是**从索引里数出来的**，不是猜的 —— 每条都带「依据几个文件」；
  · 认不出的（比如自制构建系统）**明写「没认出」**，不编一个；
  · `anchors.main` 只在**确认存在**那类入口文件时才写，否则**不写**（猜错会让门锚到假单元）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

#: 后缀 → (语言, glob, 要剪的目录)—— 认不出语言的项目也能生成「按后缀」的档案
语言表 = {
    ".rs": ("rust", "**/*.rs", ["target"]),
    ".py": ("python", "**/*.py", ["__pycache__", ".venv", ".mypy_cache", ".pytest_cache"]),
    ".ts": ("typescript", "**/*.ts", ["node_modules", "dist", "build"]),
    ".tsx": ("typescript", "**/*.tsx", ["node_modules", "dist", "build"]),
    ".js": ("javascript", "**/*.js", ["node_modules", "dist", "build"]),
    ".jsx": ("javascript", "**/*.jsx", ["node_modules", "dist", "build"]),
    ".go": ("go", "**/*.go", ["vendor"]),
    ".java": ("java", "**/*.java", ["target", "build", "out"]),
    ".kt": ("kotlin", "**/*.kt", ["build", "out"]),
    ".c": ("c", "**/*.c", ["build", "out"]),
    ".h": ("c", "**/*.h", ["build", "out"]),
    ".cpp": ("cpp", "**/*.cpp", ["build", "out"]),
    ".cc": ("cpp", "**/*.cc", ["build", "out"]),
    ".hpp": ("cpp", "**/*.hpp", ["build", "out"]),
    ".cs": ("csharp", "**/*.cs", ["bin", "obj"]),
    ".lean": ("lean", "**/*.lean", [".lake", "build"]),
    ".rb": ("ruby", "**/*.rb", ["vendor"]),
    ".php": ("php", "**/*.php", ["vendor"]),
}

构建工具 = [
    ("Cargo.toml", "cargo", ["cargo", "build"]),
    ("go.mod", "go", ["go", "build", "./..."]),
    ("package.json", "npm", ["npm", "test"]),
    ("pyproject.toml", "python", ["python", "-m", "pytest", "-q"]),
    ("setup.py", "python", ["python", "-m", "pytest", "-q"]),
    ("pom.xml", "maven", ["mvn", "-q", "-DskipTests", "package"]),
    ("build.gradle", "gradle", ["gradle", "build"]),
    ("build.gradle.kts", "gradle", ["gradle", "build"]),
    ("Makefile", "make", ["make"]),
    ("CMakeLists.txt", "cmake", ["cmake", "--build", "."]),
]


def _字节(n: float) -> str:
    for 单, 名 in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")):
        if n >= 单:
            return f"{n / 单:.1f}{名}"
    return f"{int(n)}B"


def _从索引挑根(根: Path, 索引: Path, 后缀: str, 门槛: float = 0.30) -> tuple[list[str], int]:
    """按**索引**（不是猜）挑出承载该后缀的顶层目录。

    ★ 为什么用索引：`orch-kb` 刚把这个文件夹扫过一遍 —— 那是「AI 知道自己有什么」的原件。
      同一份读数既报给用户看，也拿来回填档案，**两处用的是同一个真值**（口径 375）。
    """
    计: dict[str, int] = {}
    总 = 0
    try:
        行们 = 索引.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    for x in 行们:
        if not x:
            continue
        try:
            相, s, _n, _m, _t = json.loads(x)
        except Exception:                                # noqa: BLE001
            continue
        if s != 后缀:
            continue
        总 += 1
        段 = 相.split("/")[0] if "/" in 相 else "."
        计[段] = 计.get(段, 0) + 1
    出 = [k for k, v in sorted(计.items(), key=lambda kv: -kv[1]) if v >= 总 * 门槛]
    return 出 or ([k for k, _ in sorted(计.items(), key=lambda kv: -kv[1])[:1]] if 计 else []), 总


def _探测(根: Path, 知库) -> dict:
    """扫一遍那个文件夹，认出它是什么项目。每条结论都带**依据**。"""
    盘 = 知库.盘点(根)
    后缀分布 = 盘.get("后缀分布（前 N）") or {}
    # ① 主导语言：只数**语言表里认识的**后缀，避免被 .lock/.json 带偏
    候选 = [(s, n) for s, n in 后缀分布.items() if s in 语言表]
    候选.sort(key=lambda kv: -kv[1])
    语 = None
    依据 = 0
    if 候选:
        s, n = 候选[0]
        语 = 语言表[s]
        依据 = n
    # ② 构建工具：只看**根目录**（不在子目录乱找，避免把 vendor 里的当成自己的）
    工具 = None
    for 名, 工具名, 命令 in 构建工具:
        if (根 / 名).is_file():
            工具 = (名, 工具名, 命令)
            break
    # ③ 源码根：从索引里数（不猜）
    根们: list[str] = []
    该后缀 = 候选[0][0] if 候选 else ""
    if 该后缀:
        根们, _总 = _从索引挑根(根, Path(盘["索引文件"]), 该后缀)
    return {
        "盘": 盘,
        "行": {**{k: v for k, v in 盘.items() if k != "后缀分布（前 N）"}},
        "语言": 语,
        "语言后缀": 该后缀,
        "语言依据": 依据,
        "构建": 工具,
        "源码根": 根们,
        "候选语言": [(语言表[s][0], s, n) for s, n in 候选[:5]],
    }


def _锚(根: Path, 语言名: str | None) -> str | None:
    """只在**确认存在**入口文件时才给锚 —— 猜错会让门锚到一个假单元（口径 311）。"""
    if 语言名 == "rust":
        if (根 / "src" / "lib.rs").is_file():
            return "src.lib"
        if (根 / "src" / "main.rs").is_file():
            return "src.main"
    if 语言名 == "python":
        for 子 in sorted((根 / "src").glob("*/__init__.py")) if (根 / "src").is_dir() else []:
            return "src." + 子.parent.name
    return None


def _候选门(根: Path, 测: dict) -> list[tuple[str, list[str], str]]:
    """列出这个工程**可能的**判据门：(名, 命令, 为什么)。

    ★ R552b：`orch-init` 原来把 `gates:` **整段注释掉** ⇒ 新用户拿到的档案 0 条门
      ⇒ `orch-doctor` 报「没判据门」、`orch-goal` 回执报「无从独立验收」。
    ★ 顺序 = **独立性强弱**：工程自己声明的 > 工程自己写的验收脚本 > 语言级兜底。
    """
    候: list[tuple[str, list[str], str]] = []
    if 测.get("构建"):
        _名, _工具, _cmd = 测["构建"]
        候.append((f"{_工具}_build_ok", [str(x) for x in _cmd],
                   f"根目录有 {_名} ⇒ 这是这个工程**自己声明**的构建方式"))
    # ② 工程自己写的验收脚本 —— 最强独立来源（人写的，不是模型给的）
    for _n in ("gate.py", "check.py", "verify.py"):
        if (根 / _n).is_file():
            候.append((f"{_n[:-3]}_exit0", ["python", _n],
                       f"根目录有 `{_n}` ⇒ **工程自己写的**验收脚本（独立来源）"))
            break
    # ③④ python 语言级兜底（只在 python 项目上做）
    _语 = (测.get("语言") or ("",))[0]
    if _语 == "python":
        _有测 = ((根 / "tests").is_dir()
                 or any(根.glob("test_*.py")) or any(根.glob("*_test.py")))
        if _有测:
            候.append(("pytest_ok", ["python", "-m", "pytest", "-q"],
                       "有测试文件 ⇒ pytest 真执行断言"))
        if not any(c[0].endswith("_build_ok") for c in 候):
            候.append(("syntax_ok", ["python", "-m", "compileall", "-q",
                                   "-x", r"(^|[\\/])(\.venv|venv|node_modules|__pycache__|\.git)([\\/]|$)", "."],
                       "python 项目**没有构建清单** ⇒ 兜底用 compileall 做语法编译"
                       "（有语法错就非 0，不是恒真占位门）"))
    return 候


def _写档案(根: Path, 测: dict, 出: Path, 名: str) -> str:
    """手写 YAML（不用 yaml.dump：注释会被吃掉，而这份档案是**给人读的**）。"""
    语 = 测["语言"]
    glob = 语[1] if 语 else "**/*"
    skip = 语[2] if 语 else []
    锚 = _锚(根, 语[0] if 语 else None)
    L: list[str] = []
    L.append("# ═══════════════════════════════════════════════════════════════")
    L.append("# 由 `orch-init` **自动生成** —— 依据是这个文件夹的真盘点，不是猜的。")
    L.append(f"# 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append("#")
    L.append(f"# 探测依据：文件 {测['盘']['文件数']:,} 个 · {_字节(测['盘']['总字节'])}")
    if 语:
        L.append(f"#   主导语言 {语[0]}（{测['语言后缀']} 共 {测['语言依据']:,} 个文件）")
    else:
        L.append("#   主导语言：★ **没认出** —— 语言表里没有这个项目的主要后缀")
    if 测["构建"]:
        L.append(f"#   构建工具 {测['构建'][1]}（因为没有 {测['构建'][0]}）")
    else:
        L.append("#   构建工具：★ **没认出** —— 根目录没有已知的构建清单")
    L.append(f"#   源码根 {测['源码根'] or '★ 没认出'}")
    L.append("#")
    L.append("# ★ 这份档案属于**你的项目**，不属于编排器工具。换项目 = 换这份文件。")
    L.append("# ★ 下面每一行都可以手改；`orch-init --覆盖` 会把它整份重写。")
    L.append("# ═══════════════════════════════════════════════════════════════")
    L.append("")
    L.append(f"name: {名}")
    L.append("")
    L.append("# 被开发项目的仓库根（绝对路径：档案可能不在项目里，相对路径会解错）")
    L.append(f"repo_root: {json.dumps(str(根), ensure_ascii=False)}")
    L.append("")
    L.append("source:")
    L.append("  roots:")
    for r in (测["源码根"] or ["."]):
        L.append(f"    - {json.dumps(r, ensure_ascii=False)}")
    L.append("  globs:")
    L.append(f"    - {json.dumps(glob, ensure_ascii=False)}")
    if skip:
        L.append("  skip:")
        for s in skip:
            L.append(f"    - {json.dumps(s, ensure_ascii=False)}")
    L.append("")
    L.append("# 记忆索引范围（★ 不声明 ⇒ 记忆索引只扫产物库 ⇒ 普通工程什么都进不去）")
    L.append("memory:")
    L.append(f"  - kind: {(语[0] if 语 else 'src')}")
    L.append("    roots:")
    for r in (测["源码根"] or ["."]):
        L.append(f"      - {json.dumps(r, ensure_ascii=False)}")
    L.append(f"    globs: [{json.dumps(glob, ensure_ascii=False)}]")
    if skip:
        L.append("    skip: [" + ", ".join(json.dumps(s, ensure_ascii=False) for s in skip) + "]")
    L.append("  - kind: doc")
    L.append('    roots: ["."]')
    L.append('    globs: ["*.md"]')
    L.append("")
    if 锚:
        L.append("# 锚点：跑工程级门时锚定的入口单元（**确认存在**才写，不猜）")
        L.append("anchors:")
        L.append(f"  main: {锚}")
    else:
        L.append("# anchors.main：★ 没写 —— 没找到确定的入口单元。")
        L.append("#   空着**不会**让门锚到假单元；需要时手填（rust: src.lib / python: src.包名）。")
    L.append("")
    # ★★★★★ R552b：**真写** gates，且每条**当场探跑**。
    #   原来这里整段是注释（`# gates:`）⇒ 新用户拿到的档案 0 条门 ⇒
    #   `orch-doctor` 报「没判据门」、`orch-goal` 回执报「无从独立验收」。
    #   口径与 `bootstrap.生成档案` 一致：**只写探跑确认能跑的门**。
    L.append("# ── 判据门：项目**自己的**验收标准（独立来源 · 非模型给）────────────")
    L.append("# ★ 下面每条都**当场真跑过**（探跑）。起不来的门不写 —— 那会每次必红，")
    L.append("#   是项目级假红，比没有门更坏（把真问题淹掉）。")
    L.append("# ★ `可跑·红` 会照写：它不是错误，它正是**当前缺口**（改之前本来就不过）。")
    候 = _候选门(根, 测)
    已写 = 0
    if 候:
        _行: list[str] = []
        for _门名, _cmd, _因 in 候:
            try:
                from agentscope_orchestrator import bootstrap as _bs
                _r = _bs.探命令(_cmd, 根, 120)
            except Exception as _e:                    # noqa: BLE001
                _r = {"状态": "不可用", "因由": f"{type(_e).__name__}: {_e}"}
            _态 = _r.get("状态")
            if _态 not in ("可跑·绿", "可跑·红"):
                L.append(f"# ★ 门 `{_门名}` **未入档**：探跑不可用（{_r.get('因由') or _态}）")
                continue
            L.append(f"# 探跑：{_态}（退出码 {_r.get('退出码')}，{_r.get('秒')}s）—— {_因}")
            _行.append(f"  - name: {_门名}")
            _行.append("    type: compile")
            _行.append("    cmd: " + json.dumps(_cmd, ensure_ascii=False))
            _行.append('    cwd: "{repo}"')
            _行.append("    expect_exit: 0")
            已写 += 1
        if 已写:
            L.append("gates:")
            L.extend(_行)
        else:
            L.append("# ★★ 候选门**全部探跑不可用** ⇒ 本档案无判据门。")
            L.append("#    工具仍可启动，但**不会跑任何项目级判据**（不用恒真门冒充）。")
    else:
        L.append("# ★★ 没找到任何可探跑的候选门 ⇒ 本档案无判据门。")
        L.append("#    请人工补一条真能跑的验收命令（否则交付时只能信模型自己写的门）。")
    L.append("")
    出.parent.mkdir(parents=True, exist_ok=True)
    出.write_text("\n".join(L) + "\n", encoding="utf-8")
    return str(出)


def main(argv: list[str] | None = None) -> int:
    try:
        if callable(getattr(sys.stdout, "reconfigure", None)):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                      # noqa: BLE001
        pass
    解 = argparse.ArgumentParser(
        prog="orch-init",
        description="给**任意文件夹**生成项目档案（之后 orch-plan / orch-goal / orch-run 就能接管它）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例： orch-init D:\\某个项目\n"
               "     orch-init D:\\某个项目 --出 D:\\档案\\p.yml   （只读文件夹就用这个）\n"
               "     orch-init D:\\某个项目 --只报")
    解.add_argument("文件夹")
    解.add_argument("--出", dest="出", default="", help="写到哪（默认 <文件夹>/orchestrator.profile.yml）")
    解.add_argument("--覆盖", dest="覆盖", action="store_true", help="已存在也重写")
    解.add_argument("--只报", dest="只报", action="store_true", help="只报探测结果，不写文件")
    解.add_argument("--name", dest="name", default="", help="档案里的 name（默认用文件夹名）")
    参 = 解.parse_args(argv)

    根 = Path(参.文件夹).expanduser()
    if not 根.is_dir():
        print(f"  ✗ 不是目录：{根}", file=sys.stderr)
        return 2
    根 = 根.resolve()
    try:
        from . import 知库
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from agentscope_orchestrator import 知库        # type: ignore

    出 = Path(参.出).expanduser() if 参.出 else (根 / "orchestrator.profile.yml")
    if 出.exists() and not 参.覆盖 and not 参.只报:
        print(f"  ✗ 档案已存在：{出}\n    要重写加 --覆盖；只想看看用 --只报", file=sys.stderr)
        return 3

    t0 = time.time()
    try:
        测 = _探测(根, 知库)
    except AssertionError as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return 4

    print(f"\n  ── 探测「{根.name}」 " + "─" * 36)
    print(f"     文件 {测['盘']['文件数']:,} 个 · {_字节(测['盘']['总字节'])}")
    语名 = 测["语言"][0] if 测["语言"] else "★ 没认出"
    print(f"     主导语言：{语名}" + (f"（{测['语言后缀']} {测['语言依据']:,} 个）" if 测["语言"] else ""))
    if len(测["候选语言"]) > 1:
        print("     其次：" + " · ".join(f"{a} {c:,}" for a, _s, c in 测["候选语言"][1:4]))
    print(f"     构建工具：{测['构建'][1] + '（' + 测['构建'][0] + '）' if 测['构建'] else '★ 没认出'}")
    print(f"     源码根：{测['源码根'] or '★ 没认出（档案里回落到 .）'}")

    if 参.只报:
        print(f"\n  ✓ 只报（没写文件，{time.time() - t0:.1f} 秒）")
        return 0

    写 = _写档案(根, 测, 出, 参.name or 根.name)
    print(f"\n  ✓ 档案已生成：{写}")
    print("\n  ── 接下来，对着这个项目：")
    print(f"     1) 建图谱     orch-kb {根}")
    print(f'     2) 定计划     set ORCH_PROFILE={写}')
    print('                   orch-plan --goal-json "{...}" --root ' + str(根))
    print('     3) 跑         orch-goal --goal "把 X 做对"')
    print(f"\n  ✓ 完成（{time.time() - t0:.1f} 秒）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())