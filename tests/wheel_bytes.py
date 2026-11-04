"""wheel 与源码**逐字节**一致性（交付纪律的机器判据）。

★ 为什么它必须真判（自查抓到的假绿）：本脚本的第一版是
  `print("ALL_SAME", bad == 0)` 然后**照常 exit 0** ——
  `run_all` 于是记了一条 `PASS wheel_bytes.py  ALL_SAME False`。
  一个"输出说不对、结论说通过"的检查，比没有检查更坏：它给人"这条有人守着"的错觉。
  现在：有任何差异 / 缺件 / 找不到 wheel ⇒ **退出码 1**。

判两件事：
  ① 方向一（wheel → 源码）：wheel 里每个 `.py` 与仓内对应文件 sha256 相同；
  ② 方向二（源码 → wheel）：仓内 `src/**/*.py` 全都在 wheel 里（防"改了源码忘了打包"）。

跑法：python tests/wheel_bytes.py [<wheel 路径>]      退出码 0 = 全绿
"""
from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

根 = Path(__file__).resolve().parent.parent


def 版本() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (根 / "pyproject.toml").read_text(encoding="utf-8"),
                  re.M)
    return m.group(1) if m else "0"


def _源文件(n: str) -> Path | None:
    """wheel 内名 → 仓内路径。包里同时带 `tests/`（随包夹具），它们在仓里是 `tests/`。"""
    rel = n[len("agentscope_orchestrator/"):]
    if rel.startswith("tests/"):
        return 根 / "tests" / rel[len("tests/"):]
    return 根 / "src" / n


def main(argv: list[str]) -> int:
    v = 版本()
    候选 = Path(argv[1]) if len(argv) > 1 else (
        根 / "dist" / f"orchestrator-{v}" / "wheels"
        / f"agentscope_orchestrator-{v}-py3-none-any.whl")
    if not 候选.is_file():
        print(f"wheel_bytes：红 —— 找不到 wheel（{候选}）⇒ 先跑 tools/make_dist.py")
        return 1

    z = zipfile.ZipFile(候选)
    名们 = [n for n in z.namelist()
            if n.startswith("agentscope_orchestrator/") and n.endswith(".py")]
    坏: list[str] = []
    比 = 0
    for n in 名们:
        sb = _源文件(n)
        if sb is None or not sb.is_file():
            print("NOT_IN_SRC", n)
            坏.append(n)
            continue
        比 += 1
        if hashlib.sha256(z.read(n)).hexdigest() != hashlib.sha256(sb.read_bytes()).hexdigest():
            print("DIFF", n)
            坏.append(n)
    在包 = set(名们)           # 用**包内全名**比：拦的是"源码里有、包内没有"
    漏 = sorted("agentscope_orchestrator/" + str(p.relative_to(根 / "src" / "agentscope_orchestrator"))
                .replace("\\", "/")
                for p in (根 / "src" / "agentscope_orchestrator").rglob("*.py")
                if "__pycache__" not in p.parts)
    漏 = [x for x in 漏 if x not in 在包]
    if 漏:
        print("MISSING_IN_WHEEL", 漏[:5], f"（共 {len(漏)} 个）")

    print(f"py_files={比} diff={len(坏)} missing={len(漏)} "
          f"wheel={候选.name} bytes={候选.stat().st_size} ver={v}")
    if 坏 or 漏 or not 比:
        print("wheel_bytes：红 —— wheel 与源码不一致（改完源码没重打包？）")
        return 1
    print("wheel_bytes：绿 —— wheel 内 .py 与源码逐字节相同，且源码无漏打包")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
