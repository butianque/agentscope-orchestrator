# -*- coding: utf-8 -*-
"""行尾一致性（判据 #56）—— **改文件不许把行尾弄脏**。

## 为什么需要它（同一天踩了两次，同一个根因）

本仓同时存在 **CRLF 文件**（`generic_goal.py` 20436 行、`gateway.py` 2098 行、
`交付验收.py` 644 行、`install.sh`、`门区分力.py`）和 **LF 文件**
（`bootstrap.py` 1309 行、`init_cli.py` 364 行、`交付状态.py`、`仓内生产力.py`、
`齿单测.py`、`install.ps1`）。**两种都对**（历史如此），但**不许被悄悄换掉**。

真事故 1（2026-11-20 17:28）：编号替换写成 `p.read_text()` + `p.write_text(t)`
  ⇒ `write_text` 默认 `newline=None`，在 Windows 上把 `\\n` 翻译成 `\\r\\n`
  ⇒ **7 个 LF 文件整篇变 CRLF**：`bootstrap.py` 报 **2618 行改动**（实际只改 2 处编号）。

真事故 2（2026-11-20 17:36）：明知要保 CRLF，写成
  `write_text(t.replace("\\n", "\\r\\n"))` ⇒ `write_text` **又翻译一次**
  ⇒ 每行变成 `\\r\\r\\n` ⇒ `交付验收.py` 报 **644 行全篇改动**。

★ 这两次的共同点：**不报错、不崩溃、语法照样对**。
  唯一症状是 `git diff --stat` 里那个大得离谱的数字 ——
  而它极易被读成"这次改得多"，**掩盖真正的改动**。
  更坏的是它会污染"注入留残为零"的核对：几千行假改动里，真留残看不见。

## 判据（三条，全机器可判）

① **纯**：每个文件的行尾必须**纯 CRLF 或纯 LF**，不许混合（混合 = 某次改写只换了一半）。
② **不改**：工作区文件的行尾必须与 `git show HEAD:` 一致
   ⇒ 谁把 CRLF 换成 LF（或反过来）当场点名。
   · 新增文件（HEAD 里没有）跳过 ②，但照样过 ①。
③ **无 `\\r\\r\\n`**：这类"双重翻译"是最典型的 `write_text` 事故指纹，单独点名。

## 边界（如实）

· 它判**行尾**，判不了内容对错；
· 依赖 `git`（要 `git show HEAD:`）⇒ 非 git 环境报「未验」而不是「通过」；
· 只扫 `src/ tools/ tests/ packaging/` 下的文本文件，不扫 `work/`（运行时产物本就该随跑随变）。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

包根 = pathlib.Path(__file__).resolve().parents[1]
扫的目录 = ("src", "tools", "tests", "packaging")
文本后缀 = {".py", ".ps1", ".sh", ".md", ".yml", ".yaml", ".toml", ".cfg", ".txt", ".json"}


def _行尾种类(b: bytes) -> tuple[int, int, int]:
    """返回 (CRLF 数, 纯 LF 数, 双 CR 数)"""
    crcrlf = b.count(b"\r\r\n")
    crlf = b.count(b"\r\n")
    lf = b.count(b"\n") - crlf
    return crlf, lf, crcrlf


def _HEAD字节(相对: str) -> bytes | None:
    try:
        r = subprocess.run(["git", "show", f"HEAD:{相对}"], cwd=str(包根),
                           capture_output=True, timeout=60)
    except Exception:                                           # noqa: BLE001
        return None
    return r.stdout if r.returncode == 0 and r.stdout else None


def _期望形态(相对: str, 原: bytes) -> tuple[str, str]:
    """★ R585：**git 期望的检出形态**，不是 HEAD blob 的原始字节。

    公仓 `.gitattributes` 写着 `*.ps1 text eol=crlf` ⇒ 工作区就该是 CRLF，
    而 blob 是 LF。拿 blob 当基线会把**正确的检出转换**判成事故
    （2026-11-20 干净克隆里真撞上：`install.ps1` 假红）。
    """
    try:
        r = subprocess.run(["git", "check-attr", "eol", "--", 相对], cwd=str(包根),
                           capture_output=True, timeout=30)
        出 = r.stdout.decode("utf-8", "replace").strip()
        # 形如 `path: eol: crlf` / `path: eol: unspecified`
        if r.returncode == 0 and ":" in 出:
            值 = 出.rsplit(":", 1)[1].strip()
            if 值 == "crlf":
                return "CRLF", "`.gitattributes` 声明 eol=crlf"
            if 值 == "lf":
                return "LF", "`.gitattributes` 声明 eol=lf"
    except Exception:                                           # noqa: BLE001
        pass
    try:
        r = subprocess.run(["git", "config", "--get", "core.autocrlf"], cwd=str(包根),
                           capture_output=True, timeout=30)
        值 = r.stdout.decode("utf-8", "replace").strip().lower()
        if 值 in ("true", "always"):
            return "CRLF", "`core.autocrlf=true`"
        if 值 in ("input",):
            return "LF", "`core.autocrlf=input`"
    except Exception:                                           # noqa: BLE001
        pass
    o_crlf, o_lf, _ = _行尾种类(原)
    原态 = "CRLF" if o_crlf and not o_lf else ("LF" if o_lf and not o_crlf else "混合")
    return 原态, "无行尾声明 ⇒ 沿用 HEAD 自身形态"


def main() -> int:
    try:
        if callable(getattr(sys.stdout, "reconfigure", None)):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

    print("═══ 行尾一致性（判据 #56）═══")

    # git 可用性：不可用 ⇒ 未验（不是通过）
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(包根),
                           capture_output=True, timeout=30)
        if r.returncode != 0:
            print("○ 未验：这不是 git 仓（`git rev-parse` 失败）⇒ 只能判 ① ③，② 无从判")
            只判纯度 = True
        else:
            只判纯度 = False
    except Exception as e:                                      # noqa: BLE001
        print(f"○ 未验：git 不可用（{type(e).__name__}）⇒ 只能判 ① ③")
        只判纯度 = True

    文件: list[pathlib.Path] = []
    for d in 扫的目录:
        p = 包根 / d
        if p.is_dir():
            文件 += [x for x in p.rglob("*") if x.is_file() and x.suffix.lower() in 文本后缀]
    文件 = [x for x in 文件 if ".venv" not in x.parts and "__pycache__" not in x.parts]

    混合: list[str] = []
    双CR: list[str] = []
    变了: list[str] = []
    crlf文件 = lf文件 = 0

    for f in 文件:
        b = f.read_bytes()
        crlf, lf, crcrlf = _行尾种类(b)
        if crcrlf:
            双CR.append(f"{f.relative_to(包根)}（{crcrlf} 处 `\\r\\r\\n`）")
        if crlf and lf:
            混合.append(f"{f.relative_to(包根)}（CRLF {crlf} · LF {lf}）")
        elif crlf:
            crlf文件 += 1
        elif lf:
            lf文件 += 1
        if 只判纯度:
            continue
        相对 = str(f.relative_to(包根)).replace("\\", "/")
        原 = _HEAD字节(相对)
        if 原 is None:
            continue                                            # 新文件，无基线
        期望, 依据 = _期望形态(相对, 原)
        今态 = "CRLF" if crlf and not lf else ("LF" if lf and not crlf else "混合")
        if 期望 != 今态:
            变了.append(f"{相对}：期望 {期望}（{依据}），工作区是 {今态}")

    print(f"\n① 纯度：扫 {len(文件)} 个文件（CRLF {crlf文件} · LF {lf文件}）")
    if 混合:
        print(f"   ✗ 混合行尾 {len(混合)} 个：")
        for x in 混合[:8]:
            print(f"      {x}")
    else:
        print("   ✓ 没有混合行尾")

    print("\n② 工作区 == git 期望的检出形态" + ("（未验：无 git）" if 只判纯度 else ""))
    if 只判纯度:
        print("   ○ 未验（不是通过）")
    elif 变了:
        print(f"   ✗ {len(变了)} 个文件的行尾与 git 期望不符：")
        for x in 变了[:10]:
            print(f"      {x}")
    else:
        print("   ✓ 全部与 git 期望形态一致（含 `.gitattributes` 的 eol 声明）")

    print("\n③ 双重翻译指纹 `\\r\\r\\n`")
    if 双CR:
        print(f"   ✗ {len(双CR)} 个文件：")
        for x in 双CR[:8]:
            print(f"      {x}")
    else:
        print("   ✓ 0 处（`write_text` 事故指纹没有出现）")

    失败 = 混合 + 变了 + 双CR
    print()
    if 失败:
        print(f"判定=不达标（{len(失败)} 个文件）")
        print("  修法：改文件必须 `read_bytes → decode → 改 → encode → write_bytes`，")
        print("        并**还原成原行尾**；仓里已有现成工具 `work/R536/安全改写.py`（`替换()`）。")
        return 1
    态 = "判定=OK" if not 只判纯度 else "判定=OK（② 未验）"
    print(f"{态}；{len(文件)} 个文件行尾纯净 · 与 git 期望形态一致 · 无双重翻译")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
