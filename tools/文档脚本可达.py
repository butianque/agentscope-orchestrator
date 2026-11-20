# -*- coding: utf-8 -*-
"""文档点名的**脚本**必须真的存在、且**随仓走**（判据 #57）。

## 为什么需要它（两次真事故，同一个根因）

R584：判据 #55 读 `work/轮次编号.json`，而 `.gitignore:17` 是 `work/`
      ⇒ **新克隆的仓里没有这个文件** ⇒ #55 直接红，而本机是绿的。
R591：文档与 #56 的提示语都写着「改文件请用 `work/R536/安全改写.py`」，
      而那个文件**未跟踪** ⇒ 新克隆的仓里**指向一个不存在的文件**。

共同点：**文档/判据依赖的东西，住在不会随仓走的目录里。**
`work/` 下另有 1334 个历史 force-add 的已跟踪文件 ⇒ 这是个**半跟踪区**：
老文件在、新文件被静默忽略 ⇒ 「我本机有」被当成「发出去也有」。

## 判据（只判**脚本**，不判运行期产物）

扫包根 `*.md` 里**反引号包起来的仓内相对路径**，只取后缀
`.py` / `.sh` / `.ps1` / `.cmd` / `.exe`（运行期产物是 `.json`/`.jsonl`/`.md`，不判）：

| 情形 | 判法 |
|---|---|
| 路径**不存在** | ✗ 文档指向空气 |
| 存在但**被 git 忽略**（`git check-ignore` 命中） | ✗ 发出去的仓里没有它 |
| 存在且已跟踪 | ✓ |

含通配符（`*` `?` `{`）或 `<…>` 的跳过 —— 那是模式，不是路径。

退出码：0 = 全部可达；1 = 有不可达。
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

包根 = pathlib.Path(__file__).resolve().parents[1]
_脚本后缀 = (".py", ".sh", ".ps1", ".cmd", ".exe")
_反引号 = re.compile(r"`([^`\n]+)`")
# 豁免表：**历史叙述/示例**里点到的路径，不是"给你用的工具"。
# 每一条都必须写理由 —— 空理由不许豁免（否则这表就成了藏污纳垢处）。
_豁免文件 = 包根 / "tools" / "文档脚本可达_豁免.json"
# ★ 不判 `src/`：文档里 `src/…` 常常是**被编排的目标仓**的路径
#   （例：`src/calc.py` 真变成 `return a * b`）—— 那是本产品的主题，不是本仓的文件。
#   判它就是假红（2026-11-21 实测撞上）。
_仓内前缀 = ("work/", "tools/", "tests/", "packaging/", "dist/", "config/")


def _已跟踪(相对: str) -> bool:
    """`相对` 是否被 git 跟踪（`git ls-files` 命中即算）。"""
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", "--", 相对],
                           cwd=str(包根), capture_output=True, timeout=30)
    except Exception:                                            # noqa: BLE001
        return False
    return r.returncode == 0


def _被忽略(相对: str) -> bool:
    try:
        r = subprocess.run(["git", "check-ignore", "-q", "--", 相对],
                           cwd=str(包根), capture_output=True, timeout=30)
    except Exception:                                            # noqa: BLE001
        return False
    return r.returncode == 0


def main() -> int:
    md们 = sorted(包根.glob("*.md"))
    if not md们:
        print("判定=未验；包根没有 .md，无从判起")
        return 2
    豁免: dict[str, str] = {}
    if _豁免文件.is_file():
        try:
            豁免 = {str(k): str(v) for k, v in
                    (json.loads(_豁免文件.read_text(encoding="utf-8")).get("豁免") or {}).items()}
        except Exception:                                        # noqa: BLE001
            豁免 = {}
    坏: list[str] = []
    查过 = 0
    跳 = 0
    for md in md们:
        try:
            文 = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for 段 in _反引号.findall(文):
            段 = 段.strip()
            if not 段.lower().endswith(_脚本后缀):
                continue
            if any(c in 段 for c in "*?{}<>"):
                continue                                     # 模式，不是路径
            if not 段.startswith(_仓内前缀):
                continue
            相对 = 段.replace("\\", "/").lstrip("./")
            if 相对 in 豁免 and 豁免[相对].strip():
                跳 += 1
                continue
            查过 += 1
            目标 = 包根 / 相对
            if not 目标.exists():
                坏.append(f"{md.name} → `{相对}`：**文件不存在**")
            elif _被忽略(相对) and not _已跟踪(相对):
                坏.append(f"{md.name} → `{相对}`：**存在但未进 git**"
                          f"（新克隆的仓里没有它）")

    print(f"① 扫了 {len(md们)} 份 .md · 点到 {查过} 个脚本路径 · 豁免 {跳} 个")
    if 坏:
        print(f"② 不可达 {len(坏)} 个：")
        for x in 坏:
            print(f"   ✗ {x}")
        print("判定=不达标")
        print("修法：脚本放 `tools/`（随仓走）或 `git add -f`；"
              "运行期产物（.json/.jsonl）不在此判据范围内。")
        return 1
    print("② 全部可达（存在 ∧ 已进 git）")
    print("判定=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
