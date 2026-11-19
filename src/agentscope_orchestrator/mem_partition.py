"""分区：把「**一个大文件夹里混着的许多项目**」切成机器说得清的块。

★ 用户原话（R303，2026-11-07）：
  「知识图谱的构建**不是按整库目录文件夹**构建的。」
  「我**这个大文件夹里这么多项目混在一起**。」
  「怎么知道这个文件夹下有什么东西？**那些东西有用那些东西没用**？」
  「得**精确到具体的文件夹，目录，文件，代码内容**，这样才能**分清楚什么东西是什么**啊。」

⇒ 「分得清什么东西是什么」拆成**三层**，本模块逐层给**机器判据**（不调模型）：

  ① **项目边界** —— 谁是一个独立项目。
     判据 = **清单文件**（`Cargo.toml` / `lakefile.lean` / `pyproject.toml` /
     `package.json` / `go.mod` …）。**不猜，只认清单。** 没有清单的目录不是项目根。
  ② **项目内角色** —— 每一块里什么是源/测/文/数/配/器/录/产。
     判据 = **路径段 + 后缀**，纯字符串规则。
  ③ **可再生成性** —— 哪些文件**能由别的文件重新生成**（构建产物/缓存/虚拟环境/依赖包）。

★★ 口径（这一条必须写死，否则就是骗人）：
  **机器判不了「有用没用」** —— 那是价值判断，没有真值。
  机器判得了的是**「可再生成 vs 不可再生成」**。
  这两个词**不能混用**：说「产物没用」是越权，说「产物可再生成」是事实。
  用户真正要的是后者（哪些不必入库/哪些是原料），所以本模块只报后者，
  并在返回里**显式标注**它不是价值判断。

★ 作用域（与符号表**故意不同**，不是笔误）：
  · **分区**：扫**整个仓根** —— 因为要回答「这个大文件夹里有哪些项目」，
    只扫已声明的根就永远发现不了没声明的那一块（那正是用户批评的
    「没有扫完整文件夹」）。
  · **符号表**：只读**档案声明的根** —— 因为逐文件解析很贵，
    而且符号查询的语义是「这张表覆盖什么」，不是「仓里有什么」。
  ⇒ 两个作用域两个用途。**把它们混起来，就会拿「表里没有」当「仓里没有」。**
"""
from __future__ import annotations

import os
from pathlib import Path

# ════════════════════════════════════════════════════════════
# ① 项目边界：只认清单，不猜
# ════════════════════════════════════════════════════════════

清单规则: dict[str, str] = {
    "Cargo.toml": "rust",
    "lakefile.lean": "lean",
    "lake-manifest.json": "lean",
    "lean-toolchain": "lean",
    "pyproject.toml": "py",
    "setup.py": "py",
    "setup.cfg": "py",
    "package.json": "node",
    "tsconfig.json": "ts",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "CMakeLists.txt": "cpp",
    "meson.build": "cpp",
    "*.csproj": "dotnet",
    "pubspec.yaml": "dart",
    "Gemfile": "ruby",
    "composer.json": "php",
    "mix.exs": "elixir",
}

# ════════════════════════════════════════════════════════════
# ② 项目内角色：路径段 + 后缀，纯字符串
# ════════════════════════════════════════════════════════════

_源后缀 = frozenset({
    ".rs", ".py", ".lean", ".java", ".kt", ".ts", ".tsx", ".js", ".jsx", ".mjs",
    ".go", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".scala", ".hs", ".ml", ".ex", ".exs", ".dart", ".lua", ".r", ".jl", ".sh",
    ".bash", ".ps1", ".bat", ".cmd", ".sql", ".vim", ".el",
})
_文后缀 = frozenset({".md", ".rst", ".adoc", ".tex", ".txt", ".org"})
_数后缀 = frozenset({
    ".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".parquet", ".arrow",
    ".xlsx", ".xls", ".db", ".sqlite", ".sqlite3", ".pkl", ".pickle",
    ".npy", ".npz", ".h5", ".hdf5", ".bin", ".pt", ".onnx", ".safetensors",
})
_配后缀 = frozenset({
    ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".properties",
    ".xml", ".html", ".css", ".scss", ".lock", ".gitignore", ".dockerfile",
})

# 「可再生成」的路径段：构建产物 / 缓存 / 虚拟环境 / 依赖包 / 打包输出
_产段 = frozenset({
    "target", "target_", "build", "dist", "out", "output", "obj", "bin",
    "node_modules", "__pycache__", ".venv", "venv", "env", ".eggs",
    "site-packages", "products", "artifacts", ".lake", ".cache", ".tmp", "tmp",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".cargo_home",
    ".tools", ".git", ".hg", ".svn",
})
# 「记录/证据」的路径段：过程留痕（不可再生成 —— 是事实记录）
_录段 = frozenset({
    "receipts", "receipt", ".audit_trail", "audit", "logs", "log",
    "work", "runs", "history", ".dsh",
})
# 「测试」的路径段 / 文件名标记
_测标 = frozenset({"test", "tests", "testing", "spec", "specs", "__tests__"})


def _段们(相对: str) -> list[str]:
    return [s for s in 相对.replace("\\", "/").split("/") if s]


def 定角色(相对: str) -> str:
    """一个文件在这块里**干什么用** —— 纯规则，先看路径再看后缀。

    优先级（高→低）：产 > 录 > 清单 > 测 > 源/文/数/配 > 其它
    ★ 「产」排最前：一个 `target/debug/x.rs` 是**产物**，不是「源」——
      按后缀判会把它误算成源码，虚增项目规模。
    """
    段 = _段们(相对)
    名 = 段[-1] if 段 else ""
    去名 = 段[:-1] if len(段) > 1 else []
    低名 = 名.lower()

    # ★ 前缀匹配（`target_基线`/`target_g` 都是产物）
    for s in 去名:
        ls = s.lower()
        if any(ls == p or ls.startswith(p) for p in _产段):
            return "产"
        if any(ls == r or ls.startswith(r) for r in _录段):
            return "录"
    if 名 in 清单规则 or any(名.endswith(k.lstrip("*")) for k in 清单规则 if k.startswith("*")):
        return "清单"
    # 测试：目录段或文件名前缀
    if any(s.lower() in _测标 for s in 去名) or 低名.startswith(("test_", "test-")) \
            or 低名.endswith(("_test.py", "_test.go", ".test.ts", ".spec.ts", "Test.java")):
        return "测"
    后缀 = Path(低名).suffix
    if 后缀 in _源后缀:
        return "源"
    if 后缀 in _文后缀:
        return "文"
    if 后缀 in _数后缀:
        return "数"
    if 后缀 in _配后缀 or 低名 in ("dockerfile", "makefile", "justfile", "rakefile"):
        return "配"
    return "它"


# ════════════════════════════════════════════════════════════
# ③ 扫「整个大文件夹」并分区
# ════════════════════════════════════════════════════════════

def _该跳(名: str, 跳过: set[str]) -> bool:
    # ★ **必须**用 `mem_prune` 那一份，不许在这里再写一份。
    #   2026-11-07 事故：这里原来只用档案 `skip` ⇒ 没剪 `.git`/`.lake`/`.cargo_home`
    #   ⇒ 扫出 2,011 个「项目」（绝大多数是 mathlib / crates 这些依赖包）。
    from .mem_prune import 该跳 as _剪
    return _剪(名, 跳过)


_归档标 = ("_archive", "备份", "backup", "_备份", "tmp_", "_tmp", "old_", "_old",
           "snapshot", "_sn91", "_sn", "_t1", "归档")
# 「工位」项目根的**子目录名**：一次性工位的夹具一般就叫这几个名字
_工位子 = ("repo", "work", "proj", "project", "sandbox", "fixture")


def 找工位(项目根们: dict) -> dict[str, str]:
    """**结构性**判据：`<X>/repo` 自己是一个项目根 ⇒ **X 是一次性工位**。

    ★ 为什么用这个判据，而不是猜目录名（2026-11-07）：
      实测 `多智能体编排/agentscope_orchestrator` 包根下躺着 **1,750 个**
      `r267_0_062cwwmb/repo/Cargo.toml`。若按**名字**判（`_` + 8 位随机），
      会漏掉 `chk_fz_yc3fv`（尾 5 位）、`orch_rs_probe`，也会误伤人名目录。
      **结构性判据可复核**：去看 `X/repo` 是不是一个项目根，一看就知道。

    ★ 边界：若有人真把检出目录命名为 `repo/` 并作为子项目，会**误判成工位**。
      所以本函数只**报数并标类型**，**不删任何东西**，且返回里带 `父目录` 供人复核。
    """
    工位: dict[str, str] = {}
    for 根 in 项目根们:
        段 = [s for s in 根.split("/") if s]
        if len(段) >= 2 and 段[-1].lower() in _工位子:
            工位["/".join(段[:-1])] = 段[-1]
    return 工位


def _项目类型(根: str, 工位: dict[str, str] | None = None) -> str:
    """一方 / 工位残留 / 归档 —— **每条都要能说出依据**。

    ★ 为什么不猜「哪个项目重要」：那要人给真值。这里三条各有可复核依据：
      · **工位残留**：`<X>/repo` 是个项目根（结构性，可复核）；
      · **归档**：路径里有 `_archive`/`备份`/`tmp_` 等**作者自己留的标记**；
      · **一方**：以上都不是 —— ★ 注意这是**默认值**，不是「已验证是一方」。
    """
    段 = [s for s in 根.replace("\\", "/").split("/") if s]
    p = 根.replace("\\", "/")
    if 工位:
        for i in range(len(段), 0, -1):
            if "/".join(段[:i]) in 工位:
                return "工位残留"
    for s in 段:
        ls = s.lower()
        if any(ls.startswith(m) or m in ls for m in _归档标):
            return "归档"
    return "一方（按排除法默认，未逐个核实）"


def 扫(
    仓库根: Path | str,
    跳过: set[str] | frozenset[str] = frozenset(),
    上限目录: int = 0,
) -> dict:
    """扫**整个仓根**，按「项目边界 + 角色 + 可再生成性」聚合。

    ★ 不存全量路径，**边走边聚合** —— 仓根实测 28 万文件，全存下来是几十 MB
      纯浪费；这里只留计数器与每个项目的清单位置。
    ★ 跳过目录**根本不进 walk**（`dn[:] = ...`），不是走完再过滤。
    """
    仓库根 = Path(仓库根).resolve()
    跳 = set(跳过)

    # 第一遍：找出所有清单 ⇒ 项目根（不猜，只认清单）
    项目根: dict[str, tuple[str, str]] = {}   # 相对目录 -> (清单名, 语言)
    目录数 = 0
    文件数 = 0
    被跳目录数 = 0
    剪枝按类: dict[str, int] = {}
    按角色: dict[str, int] = {}
    按后缀: dict[str, int] = {}
    按顶层: dict[str, int] = {}
    项目按角色: dict[str, dict[str, int]] = {}
    # ★ 顺路收集**全仓** `.lean` 文件名 —— 锚定层要用它区分
    #   「真悬空」与「文件在别的根里」。分区本来就在走全仓，**零额外代价**；
    #   不这么做就得再走一遍全仓（那又是「同一个判断写在两处」）。
    #   ★ 这个字段**不落 JSON**（`分区同步` 会 pop 掉）—— 只给同进程的锚定层用。
    全仓lean名: set[str] = set()

    def _归属(相对目录: str) -> str | None:
        """把目录挂到**最长的**匹配项目根上（支持嵌套项目：子项目优先）。"""
        段 = 相对目录.replace("\\", "/").split("/") if 相对目录 not in ("", ".") else []
        while True:
            候选 = "/".join(段)
            if 候选 in 项目根:
                return 候选
            if not 段:
                return None
            段 = 段[:-1]

    def _走(基: Path) -> None:
        nonlocal 目录数, 文件数, 被跳目录数
        from .mem_prune import 归类 as _归类
        for dp, dn, fn in os.walk(基):
            保留 = []
            for d in dn:
                if _该跳(d, 跳):
                    被跳目录数 += 1
                    类 = _归类(d)
                    剪枝按类[类] = 剪枝按类.get(类, 0) + 1
                else:
                    保留.append(d)
            dn[:] = sorted(保留)
            目录数 += 1
            try:
                rel_dir = str(Path(dp).relative_to(仓库根)).replace("\\", "/")
            except ValueError:
                rel_dir = ""
            if rel_dir == ".":
                rel_dir = ""
            # 清单 ⇒ 项目根（只认清单，不猜）
            for f in fn:
                if f in 清单规则:
                    项目根.setdefault(rel_dir, (f, 清单规则[f]))
                    break
            for f in fn:
                文件数 += 1
                相对 = f"{rel_dir}/{f}" if rel_dir else f
                if f.endswith(".lean"):
                    全仓lean名.add(f)
                角 = 定角色(相对)
                按角色[角] = 按角色.get(角, 0) + 1
                后缀 = Path(f).suffix.lower() or "(无后缀)"
                按后缀[后缀] = 按后缀.get(后缀, 0) + 1
                顶 = 相对.split("/")[0] if "/" in 相对 else "(仓根文件)"
                按顶层[顶] = 按顶层.get(顶, 0) + 1
                属 = _归属(rel_dir) or "(无项目)"
                项目按角色.setdefault(属, {})
                项目按角色[属][角] = 项目按角色[属].get(角, 0) + 1

    _走(仓库根)

    项目们 = []
    工位 = 找工位(项目根)
    # ★ 排序：一方在前，其次归档，工位残留垫底（它们**不是项目**，是缺陷信号）。
    #   否则 1,749 个工位会把「这个仓里真正的项目」淹掉 —— 那正是用户抱怨的
    #   「分不清什么东西是什么」。
    _序 = (("一方", 0), ("归档", 1), ("工位残留", 2))

    def _键(x: dict) -> tuple[int, int]:
        for 名, i in _序:
            if str(x["类型"]).startswith(名):
                return (i, -x["文件"])
        return (3, -x["文件"])

    全部 = []
    for 根, (清单名, 语言) in 项目根.items():
        角 = dict(项目按角色.get(根, {}))
        全部.append({
            "根": 根 or "(仓根)",
            "清单": 清单名,
            "语言": 语言,
            "类型": _项目类型(根, 工位),
            "文件": sum(角.values()),
            "按角色": 角,
            "可再生成": 角.get("产", 0),
        })
    全部.sort(key=_键)
    项目们 = 全部
    # 不属于任何项目的文件（含仓根散文件）
    无项目: dict[str, int] = {}
    for 属, 角 in 项目按角色.items():
        if 属 == "(无项目)":
            无项目 = dict(角)

    工位 = 找工位(项目根)
    可再生成 = 按角色.get("产", 0)
    return {
        "仓库根": str(仓库根),
        # ★ 不落 JSON（`分区同步` 会 pop）：只给同进程的锚定层用，避免第二遍全仓 walk
        "_全仓lean名": 全仓lean名,
        "目录数": 目录数,
        "被跳目录数": 被跳目录数,
        "剪枝按类": dict(sorted(剪枝按类.items(), key=lambda kv: -kv[1])),
        "文件数": 文件数,
        "项目数": len(项目们),
        "工位数": len(工位),
        "工位父目录": sorted(工位)[:40],
        "项目们": 项目们 if not 上限目录 else 项目们[:上限目录],
        "无项目区": {"文件": sum(无项目.values()), "按角色": 无项目},
        "按角色": 按角色,
        "按后缀": dict(sorted(按后缀.items(), key=lambda kv: -kv[1])[:25]),
        "按顶层": dict(sorted(按顶层.items(), key=lambda kv: -kv[1])[:25]),
        "未剪枝里的产物角色": 可再生成,
        "口径": ("机器判的是**分区与角色**（纯路径/后缀规则），"
                 "**不是「有用没用」** —— 后者是价值判断，没有真值。"),
        "剪枝边界": (
            "★ 剪枝 = **不进去看** ⇒ **不知道里面有多少文件**。所以："
            "① `剪枝按类` 报的是**目录数**，**不是文件数** —— 报文件数就是编；"
            "② 曾经这里报过「可再生成 N 个文件」，那是**假的**："
            "产物目录已被剪掉，我从没数过里面的文件（自检当场抓到它恒为 0）。"
            "「可再生成」这件事只能靠**目录名**说，不能靠**文件计数**说。"),
    }


# ════════════════════════════════════════════════════════════
# 简报（常驻那一小段）
# ════════════════════════════════════════════════════════════

_角色序 = ("源", "文", "数", "配", "测", "清单", "录", "产", "它")


def 简报分节(果: dict, 项目上限: int = 8,
             顶层上限: int = 6) -> tuple[str, str]:
    """返回 `(摘要, 明细)` 两节。

    ★ **为什么必须分开**（2026-11-07，真被截断过两次）：
      `简报()` 是按行截断的，而「摘要级事实」（这里有哪些项目、剪了什么、哪里缺）
      **必须比「明细」（逐个项目的文件数）先保**。
      原来汇总成一节时，项目清单把预算吃光，**缺口行每轮都被截掉** ——
      而缺口恰恰是最该常驻的东西。
      所以调用方按 **摘要 ≥ 缺口 ≥ 明细** 的顺序拼。
    """
    if not 果 or not 果.get("文件数"):
        return "", ""
    一方 = sum(1 for p in (果.get("项目们") or []) if p.get("类型", "").startswith("一方"))
    工位 = sum(1 for p in (果.get("项目们") or []) if p.get("类型") == "工位残留")
    归档 = 果["项目数"] - 一方 - 工位
    摘 = [
        f"分区（扫的是**整个仓根**，与符号表作用域不同）："
        f"目录 {果['目录数']:,}（剪掉 {果['被跳目录数']:,}）· 文件 {果['文件数']:,} · "
        f"**项目 {果['项目数']} 个**（一方 {一方} · 工位残留 {工位} · 归档 {归档}）",
    ]
    if 工位:
        父 = 果.get("工位父目录") or []
        摘.append(f"  ⚠ 工位残留 {工位} 个（判据：`<X>/repo` 自己是项目根）—— "
                  f"**这是缺陷信号，不是项目**；最常见父目录："
                  + " · ".join(父[:3]) if 父 else "")
    类 = 果.get("剪枝按类") or {}
    if 类:
        摘.append("  剪掉的目录按类（**目录数**，不是文件数——剪枝就是不进去看）："
                  + " · ".join(f"{k} {v:,}" for k, v in list(类.items())[:6]))
    细: list[str] = []
    for p in (果.get("项目们") or []):
        if len(细) >= 项目上限:
            break
        if p.get("类型") == "工位残留":
            continue                     # 已在摘要那条 ⚠ 里报过数，不占正文
        角 = p.get("按角色") or {}
        细.append(f"    ▸ {p['根']} 〔{p.get('清单','')}/{p['语言']}〕 文件 {p['文件']:,}"
                  + (" · " + " · ".join(f"{角[k]:,}{k}" for k in _角色序 if 角.get(k))
                     if 角 else ""))
    真项目 = 果["项目数"] - 工位
    if 真项目 > 项目上限:
        细.append(f"    … 另有 {真项目 - 项目上限} 个真项目（工位残留不在此列；"
                  f"用 `repo_state` 看全）")
    顶 = 果.get("按顶层") or {}
    if 顶:
        细.append("  顶层（文件数）：" + " · ".join(
            f"{k} {v:,}" for k, v in list(顶.items())[:顶层上限]))
    return "\n".join(摘), "\n".join(细)


def 简报段(果: dict, 项目上限: int = 8, 顶层上限: int = 6) -> str:
    """摘要 + 明细合成一段（给不需要插别的东西的调用方）。"""
    摘, 细 = 简报分节(果, 项目上限, 顶层上限)
    return 摘 + ("\n" + 细 if 细 else "")


def _自检() -> int:
    """分区自检：**七个夹具，每个夹一个已知的坑**。"""
    import tempfile
    失败: list[str] = []

    def 判(名: str, 条件: bool, 说明: str = "") -> None:
        if not 条件:
            失败.append(f"{名}{('：' + 说明) if 说明 else ''}")

    with tempfile.TemporaryDirectory() as td:
        R = Path(td)
        (R / "甲项目").mkdir()
        (R / "甲项目" / "Cargo.toml").write_text("[package]\nname='甲'\n", encoding="utf-8")
        (R / "甲项目" / "src").mkdir()
        (R / "甲项目" / "src" / "a.rs").write_text("pub fn f(){}\n", encoding="utf-8")
        (R / "甲项目" / "target").mkdir()
        (R / "甲项目" / "target" / "a.rs").write_text("PRODUCT\n", encoding="utf-8")
        (R / "乙项目").mkdir()
        (R / "乙项目" / "lakefile.lean").write_text("lean_lib 乙\n", encoding="utf-8")
        (R / "乙项目" / "X.lean").write_text("theorem t : True := by trivial\n", encoding="utf-8")
        (R / "乙项目" / "receipts").mkdir()
        (R / "乙项目" / "receipts" / "r.md").write_text("# 回执\n", encoding="utf-8")
        (R / "散文件.md").write_text("# 没项目\n", encoding="utf-8")
        # ★ 工位残留夹具：`<X>/repo` 自己是项目根 ⇒ X 是工位（结构性判据）
        (R / "r267_0_abcd1234" / "repo").mkdir(parents=True)
        (R / "r267_0_abcd1234" / "repo" / "Cargo.toml").write_text(
            "[package]\nname='w'\n", encoding="utf-8")
        (R / "r267_0_abcd1234" / "repo" / "src").mkdir()
        (R / "r267_0_abcd1234" / "repo" / "src" / "w.rs").write_text(
            "fn w(){}\n", encoding="utf-8")

        果 = 扫(R)
        名 = {p["根"]: p for p in 果["项目们"]}

        判("认得出三个项目（甲/乙 + 工位里那个 repo）", 果["项目数"] == 3,
           f"实得 {果['项目数']}: {list(名)}")
        判("工位被判成「工位残留」",
           名.get("r267_0_abcd1234/repo", {}).get("类型") == "工位残留",
           str(名.get("r267_0_abcd1234/repo", {}).get("类型")))
        判("工位数 = 1", 果["工位数"] == 1, f"实得 {果['工位数']}")
        判("甲项目语言是 rust", 名.get("甲项目", {}).get("语言") == "rust")
        判("乙项目语言是 lean", 名.get("乙项目", {}).get("语言") == "lean")
        判("甲项目文件数 = 2（清单 + 源）",
           名.get("甲项目", {}).get("文件") == 2,
           str(名.get("甲项目", {}).get("按角色")))
        判("receipts 下的 md 算**记录**不算文档",
           名.get("乙项目", {}).get("按角色", {}).get("录") == 1
           and 名.get("乙项目", {}).get("按角色", {}).get("源") == 1,
           str(名.get("乙项目", {}).get("按角色")))
        判("仓根散文件落在「无项目区」", 果["无项目区"]["文件"] == 1,
           str(果["无项目区"]))
        # ★ 关键：`target` 被剪掉 ⇒ **它不进文件计数**，但必须**报出来剪了什么**
        判("target 目录被剪（不进文件计数）",
           "构建产物" in (果.get("剪枝按类") or {}),
           str(果.get("剪枝按类")))
        判("剪枝只报**目录数**，不假装知道文件数",
           "不是文件数" in 果["剪枝边界"] and "可再生成" not in 果,
           str(list(果)[:8]))

        # 跳过的目录**不进**计数（不是走完再过滤）
        果2 = 扫(R, 跳过={"甲项目"})
        判("跳过的目录真的没进 walk", 果2["文件数"] < 果["文件数"] and 果2["项目数"] == 2,
           f"{果2['文件数']} vs {果['文件数']} · 项目 {果2['项目数']}")

        # 简报不能把「可再生成」说成「有用没用」
        段 = 简报段(果)
        判("简报带分区且报了一方/工位/归档",
           "项目 3 个" in 段 and "一方" in 段 and "工位残留" in 段
           and "剪枝就是不进去看" in 段, 段[:140])

    if 失败:
        print("  ✗ 分区自检未全过：")
        for f in 失败:
            print("     ·", f)
        return 1
    print("  ✅ 分区自检全绿（项目边界靠清单 · 工位靠结构判据 · 剪枝只报目录数 · 跳过生效）")
    return 0


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(_自检())
