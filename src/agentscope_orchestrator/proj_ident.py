# -*- coding: utf-8 -*-
"""项目身份的**唯一取名点**（R117：同一个事实不许两处各说一套）。

★ 为什么有这个模块：`src/` 里曾有 9 处**代码行**把项目名写死
  （`"形式化验证"` 当 lean 根、`"MyMeta"` 当聚合入口、`"太虚元道"` 当仓名）。
  后果不只是"不通用"，还有**真缺陷**：`mem_axiom._找工程` 只认
  `形式化验证/formal_proof` 与根 `formal_proof` 两个写死路径，
  而本仓这两处**都没有 `lakefile.lean`** ⇒ 公理足迹**永远**返回「未测到」。
  实测（2026-11-18）：真正的 lakefile 在 `arc_data/lean_self/lakefile.lean`（`package MyMeta where`）。

★ 取名顺序（都能在盘上验，不靠常量）：
  1. **lean 工程** = 含 `lakefile.lean` 的目录（全仓 rglob，跳过构建/归档目录）
  2. **聚合入口名** = 该 lakefile 里的 `package X where` 的 `X`
  3. **lean 根们** = `profile.memory` 里 `kind == "lean"` 的 `roots`（profile 是权威）
  4. **仓名** = 仓根目录名（`Path.name`）

★ 取不到就是 **None / 空列表**，由调用方记「未测到」——
  **不许**悄悄退回写死的项目名（那正是本模块要消灭的东西）。
"""
from __future__ import annotations

import pathlib
import re

__all__ = ["lean工程们", "聚合入口名", "lean根们", "仓名", "最佳lean工程"]

# 构建/归档/虚拟环境目录：里面可能有 lakefile 但不是"这个项目的工程"
_跳过段 = frozenset({
    ".git", ".lake", "node_modules", "__pycache__", "_final", "_finalA", "_finalB",
    "_finalC", "work", ".venv", "build", "target", ".pytest_cache", ".mypy_cache",
    "_archive", "_已废弃", "_验收干净", "验收",
})


def lean工程们(仓根: pathlib.Path | str, 封顶: int = 50) -> list[pathlib.Path]:
    """仓内所有含 `lakefile.lean` 的**目录**（按路径深度、再按字典序）。"""
    根 = pathlib.Path(仓根)
    if not 根.is_dir():
        return []
    出: list[pathlib.Path] = []
    for f in 根.rglob("lakefile.lean"):
        部 = set(f.relative_to(根).parts[:-1])
        if 部 & _跳过段:
            continue
        出.append(f.parent)
        if len(出) >= 封顶:
            break
    出.sort(key=lambda p: (len(p.relative_to(根).parts), str(p).lower()))
    return 出


def 聚合入口名(工程: pathlib.Path | str) -> str | None:
    """`lakefile.lean` 里 `package X where` 的 `X`（聚合入口模块名）。"""
    p = pathlib.Path(工程) / "lakefile.lean"
    if not p.is_file():
        return None
    try:
        文 = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    命中 = re.search(r"^\s*package\s+([A-Za-z_][\w.]*)\b", 文, re.M)
    return 命中.group(1) if 命中 else None


def lean根们() -> list[str]:
    """`profile.memory` 里 `kind == "lean"` 的 `roots`（相对仓根）；取不到返回 []。"""
    try:
        from . import profile as _profile
    except ImportError:  # pragma: no cover
        return []
    try:
        prof = _profile.get_profile()
        记忆 = getattr(prof, "memory", None) or ()
    except Exception:  # noqa: BLE001 —— profile 装坏了不该让索引全崩
        return []
    出: list[str] = []
    for 条 in 记忆:
        if not isinstance(条, dict):
            continue
        if str(条.get("kind") or "").lower() != "lean":
            continue
        for r in (条.get("roots") or []):
            if r:
                出.append(str(r))
    return 出


def 仓名(仓根: pathlib.Path | str) -> str:
    """仓根目录名（不写死任何项目名）。"""
    return pathlib.Path(仓根).resolve().name


def 最佳lean工程(仓根: pathlib.Path | str, 封顶: int = 50) -> pathlib.Path | None:
    """仓里**最像本项目主工程**的那个 lean 工程（打分，不猜名字）。

    ★ 为什么需要打分：全仓有 6 个 lakefile（`semsearch`、`arc_data/lean_self`、
      `形式化验证/_portproj`、`形式化验证/formal_proof`、`文档体系/06_形式化验证`、
      `存储基础设施/.../翻转门`）。只取"最浅的那个"会选中 `semsearch` —— **选错项目**，
      然后公理足迹会去扫一个考试题库工程，且**不报错**。
    ★ 打分（都在盘上可验）：
      +1000 在 `profile.memory` 声明的 lean 根之一下（profile 是权威）
      +100  有 `.lake` 构建目录（真建过）
      +1    每个 `.lean` 文件（封顶 2000，避免超大工程独占）
      同分按路径字典序（稳定、可复现）。
    """
    根 = pathlib.Path(仓根)
    们 = lean工程们(根, 封顶=封顶)
    if not 们:
        return None
    根们 = set(lean根们())
    最优, 最高 = None, -1
    for 工程 in sorted(们, key=lambda p: str(p).lower()):
        相 = str(工程.relative_to(根)).replace(chr(92), "/")
        分 = 0
        if any(相 == r or 相.startswith(str(r).replace(chr(92), "/") + "/") for r in 根们):
            分 += 1000
        if (工程 / ".lake").is_dir():
            分 += 100
        数 = 0
        try:
            for _ in 工程.rglob("*.lean"):
                数 += 1
                if 数 >= 2000:
                    break
        except OSError:
            pass
        分 += 数
        if 分 > 最高:
            最优, 最高 = 工程, 分
    return 最优
