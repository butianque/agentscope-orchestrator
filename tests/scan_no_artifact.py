"""判据：**档案没声明产物库（artifact）的普通项目，记忆索引必须能建**。

真事（2026-11-05 跨项目验证，case01 记账 CLI）：
  那个仓没有形式化证明库，档案里也就没有 `artifact:` 段。结果
  `autoengine.scan_lean(mem, prof.artifact.root, prof)` 拿到 `None` 之后直接
  `Path.rglob` ⇒ `AttributeError: 'NoneType' object has no attribute 'rglob'`。
  ⇒ 一个没有证明库的仓**连记忆索引都建不起来**，"换项目 = 换档案"当场断在第二行。

口径：**档案没声明的产物库 = 这类项目没有这种产物**，不是"缺文件"，也不是"要报错"。
      扫 0 个文件、继续扫项目自己声明的 memory 段，这才是对的。

这条判据同时在钉两件事：
  ① `artifact:` 段缺席时不许崩（本判据的主判据）；
  ② 项目**声明了** memory 段时，那些文件必须真的进索引（不许"没崩但也没扫"）。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

坏: list[str] = []


def _写(p: Path, 文: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(文, encoding="utf-8", newline="\n")


def _夹具(根: Path) -> None:
    """一个最小工程：有源码、有文档、**没有**形式化产物库。"""
    _写(根 / "src" / "case01" / "__init__.py", "")
    _写(根 / "src" / "case01" / "ledger.py",
        "def 汇总(记录):\n    \"\"\"按类别汇总。\"\"\"\n    return 记录\n")
    _写(根 / "README.md", "# 小工程\n\n没有形式化证明库。\n")
    _写(根 / "orchestrator.profile.yml", """name: 无产物库工程
repo_root: .
source:
  roots: [src]
  globs: ["**/*.py"]
anchors:
  main: src.case01
memory:
  - kind: py
    roots: [src]
    globs: ["**/*.py"]
  - kind: doc
    roots: [.]
    globs: ["*.md"]
gates:
  - type: cmd
    name: 冒烟
    cmd: ["python", "-c", "pass"]
    cwd: .
    expect_exit: 0
""")


def 判() -> None:
    from agentscope_orchestrator import autoengine
    from agentscope_orchestrator import profile as P
    from agentscope_orchestrator.autoengine import MemoryIndex

    根 = Path(tempfile.mkdtemp(prefix="no_artifact_"))
    try:
        _夹具(根)
        prof = P.load_profile(根 / "orchestrator.profile.yml")
        print(f"    ① 档案 artifact.root = {prof.artifact.root}（应为 None）")
        if prof.artifact.root is not None:
            坏.append("夹具本意是没有产物库，但 artifact.root 不是 None（夹具坏了）")

        mem = MemoryIndex()
        try:
            n = autoengine.scan_lean(mem, prof.artifact.root, prof)
        except Exception as e:                                   # noqa: BLE001
            坏.append(f"scan_lean(None) 仍然崩：{type(e).__name__}: {e}")
            return
        print(f"    ② scan_lean(None) → 扫了 {n} 个产物文件（不崩）")
        if n != 0:
            坏.append(f"没有产物库却扫出 {n} 个产物文件（读数不该是这样）")

        mem2 = MemoryIndex()
        覆盖 = autoengine.scan_project(mem2, prof.artifact.root, 根, prof)
        计 = 覆盖.get("计数") or {}
        print(f"    ③ scan_project 覆盖读数 {计}")
        py = 计.get("py", 0) if isinstance(计, dict) else 0
        doc = 计.get("doc", 0) if isinstance(计, dict) else 0
        if py < 1:
            坏.append(f"声明了 py 记忆段却没扫到 .py 文件（计数 {计}）—— 没崩但也什么都没扫")
        if doc < 1:
            坏.append(f"声明了 doc 记忆段却没扫到 .md 文件（计数 {计}）")
        # ④ 反例保护：兜底不许把"真有的产物库"也一起跳过。
        产 = 根 / "formal"
        _写(产 / "MyMeta" / "A.lean", "theorem a : True := by trivial\n")
        prof2 = P.load_profile(根 / "orchestrator.profile.yml")
        设上 = getattr(prof2.artifact, "root", "缺字段")
        if 设上 not in (None, "缺字段", ""):
            坏.append(f"夹具没有 artifact 段，artifact.root 却是 {设上}")
        mem3 = MemoryIndex()
        n3 = autoengine.scan_lean(mem3, 产, prof2)         # 显式给根 ⇒ 必须真扫
        print(f"    ④ 显式给产物库根 → 扫了 {n3} 个（反例：不许把真产物库也跳过）")
        if n3 < 1:
            坏.append("显式给了产物库根却扫 0 个 —— 兜底把真能力关掉了")
    finally:
        shutil.rmtree(根, ignore_errors=True)


def main() -> int:
    判()
    if 坏:
        print("无产物库项目：红 —— " + "；".join(坏))
        return 1
    print("无产物库项目：绿 —— **没有 artifact 段的工程也能建记忆索引；"
          "声明了 memory 段就真扫；显式给了产物库根仍照扫**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())