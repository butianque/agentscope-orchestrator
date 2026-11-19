"""**通用剪枝** —— 全项目**唯一**的一份「哪些目录不进去看」。

★ 为什么单独成一个模块（2026-11-07 真事故）：
  我先把剪枝写在 `mem_live._通用跳`，又在 `mem_partition._该跳` 写了一份。
  两份**立刻就不一致**，后果实测：

    | 谁 | 剪了什么 | 漏了什么 |
    |---|---|---|
    | `mem_live` | `.git` / `.lake` / `.cargo_home` / `target*` / `.venv` … | 档案专有项 |
    | `mem_partition` | **只**用档案 `skip` | `.git`(21,168 文件) / `.lake`(21 万+) / `.cargo_home`(12,852) |

  ⇒ 分区扫出 **2,011 个「项目」**，其中绝大多数是 `.lake/packages/mathlib`、
    `.cargo_home/registry/src/*` 这种**依赖包**。
  ⇒ **「同一个判断写在两处，必然给出两个答案」** —— 所以抽成一份，两边都 import。

★ 分层（这是口径，不是偏好）：
  · **本模块的 `通用跳`** = **任何项目都不该进去看**的（版本库 / 虚拟环境 / 依赖包 /
    缓存 / 构建产物）。项目无权把它关掉。
  · **档案的 `skip:`** = **项目专有**的排除项（本项目的 `备份`、`节点`、`semsearch` …）。
  · 两者**取并集**，且**前缀匹配**（档案里 `target_` 就是前缀式写法）。

★ 诚实边界：剪枝 = **不进去看**，所以**不知道里面有多少文件**。
  调用方要报「剪了什么」，只能报**目录数与目录名**，不能报文件数 ——
  报文件数就是在编。`归类()` 提供按前缀归到哪一类，供简报用。
"""
from __future__ import annotations

# 通用剪枝：任何项目都不该进去看
通用跳: frozenset[str] = frozenset({
    # ── 版本库/会话元数据（不是项目内容）
    ".git", ".hg", ".svn", ".dsh", ".idea", ".vscode",
    # ── 虚拟环境 / 依赖包（不是本项目写的）
    ".venv", "venv", ".eggs", "site-packages", "node_modules",
    ".lake", ".cargo_home", ".cargo", ".rustup", ".tools", ".opam", ".stack-work",
    # ── 缓存
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
    ".ipynb_checkpoints", ".gradle", ".m2", ".parcel-cache",
    # ── 构建产物 / 打包输出
    "target", "target_", "build", "dist", "out", "obj", ".next", "coverage",
    # ── 编排器自己的产物与临时（工具产物，不是被开发项目的内容）
    "products", "work", ".tmp", "tmp",
})

# 归类（只用于**简报里报「剪掉了哪些类」**，不用于决定剪不剪）
_类表: tuple[tuple[str, str], ...] = (
    (".git", "版本库"), (".hg", "版本库"), (".svn", "版本库"), (".dsh", "会话元数据"),
    (".lake", "Lean 依赖包"), (".cargo_home", "Rust 依赖包"), (".cargo", "Rust 工具链"),
    (".rustup", "Rust 工具链"), (".tools", "内嵌运行时"), (".opam", "OCaml 依赖"),
    ("node_modules", "Node 依赖包"), (".venv", "虚拟环境"), ("venv", "虚拟环境"),
    (".egg", "虚拟环境"), ("site-packages", "依赖包"),
    ("__pycache__", "缓存"), (".mypy_cache", "缓存"), (".pytest_cache", "缓存"),
    (".ruff_cache", "缓存"), (".tox", "缓存"), (".nox", "缓存"), (".gradle", "缓存"),
    (".m2", "缓存"), (".cache", "缓存"),
    ("target", "构建产物"), ("build", "构建产物"), ("dist", "打包产物"),
    ("out", "构建产物"), ("obj", "构建产物"), ("coverage", "覆盖率产物"),
    ("products", "工具产物"), ("work", "工具临时"), (".tmp", "临时"), ("tmp", "临时"),
)


def 归类(名: str) -> str:
    """一个被剪掉的目录名属于哪一类 —— 只为简报能说「剪了些什么」。"""
    ls = 名.lower()
    for 前缀, 类 in _类表:
        if ls == 前缀 or ls.startswith(前缀):
            return 类
    return "档案声明"


def 该跳(名: str, 档案跳过: set[str] | frozenset[str] = frozenset()) -> bool:
    """前缀匹配（档案里 `target_` 就是前缀式写法）。

    ★ 用**并集**：通用项**不可被档案关掉** —— 否则换个档案就能把 `.git` 扫进来。
    """
    ls = 名.lower()
    for s in 通用跳:
        if ls == s or ls.startswith(s):
            return True
    for s in 档案跳过:
        ss = str(s).lower()
        if ls == ss or ls.startswith(ss):
            return True
    return False


def _自检() -> int:
    失败 = []

    def 判(名, 条件, 说明=""):
        if not 条件:
            失败.append(f"{名}{('：' + 说明) if 说明 else ''}")

    判(".git 必跳", 该跳(".git"))
    判("target_基线 前缀跳", 该跳("target_基线"))
    判(".lake 必跳", 该跳(".lake"))
    判(".cargo_home 必跳", 该跳(".cargo_home"))
    判("档案项生效", 该跳("备份", {"备份"}))
    判("普通目录不跳", not 该跳("src"))
    判("通用项不可被档案关掉", 该跳(".git", set()))
    判("归类: .lake/packages 归依赖包", 归类(".lake") == "Lean 依赖包", 归类(".lake"))
    判("归类: 未知名归档案声明", 归类("节点") == "档案声明", 归类("节点"))
    if 失败:
        print("  ✗ 剪枝自检未全过：")
        for f in 失败:
            print("     ·", f)
        return 1
    print("  ✅ 剪枝自检全绿（9 条 · 通用不可关 / 前缀生效 / 归类可读）")
    return 0


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(_自检())
