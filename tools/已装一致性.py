# -*- coding: utf-8 -*-
"""装机一致性判据（#46）：**已装包必须与源码逐字节一致**。

## 为什么必须有一条这样的判据

2026-11-18 实测到的洞：改完 `src\\` 之后**忘了重装**，于是
· 已装的 wheel 是**旧代码**（`mem_symbols.py` 哈希不同）；
· 而**全部 39 条验收判据照样全绿** —— 因为它们跑的是**源码树**，不是装出来的包。

⇒ 于是出现一个很坏的状态：**源码正确、仓库全绿、用户装出来的东西是旧的**。
这正是口径 388 的第三层（判据测函数 · 交付测入口 · **部署测安装产物**）：
前两层都对，部署那一层没人测。

## 判据

1. 能 import 到 `agentscope_orchestrator`，并定位它的**真实安装目录**（不许用 `src` 顶替）。
2. 源码树里每个 `.py`，在安装目录里**存在且 sha256 相同** —— 一个不一致就是红。
3. 安装目录里**不许有源码树里没有的 `.py`**（旧版本残留 ⇒ 你看到的行为和你读的代码不是同一份）。
4. 安装目录必须**在 `src` 之外**（否则本判据会因为"装的其实就是源码"而空转通过 —— 空集不许当判据）。

## 诚实边界

· 只比 `.py`（库的实际可执行内容）；`pyproject.toml`/README 之类元数据不在比的范围。
· `tools\\` 不随 wheel 发布（在仓里交付），所以本判据不覆盖它们 —— 这是设计，不是遗漏。
· 拿不到安装目录 ⇒ 记**未验**（退出码 3），不记绿。
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_包根 = pathlib.Path(__file__).resolve().parents[1]
_源码 = _包根 / "src" / "agentscope_orchestrator"


def _哈希(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    try:
        import agentscope_orchestrator as _A
    except Exception as e:  # noqa: BLE001
        print(f"⚠ 未验：import 不到 agentscope_orchestrator（{type(e).__name__}: {e}）")
        print("判定=未验")
        return 3

    装 = pathlib.Path(_A.__file__).resolve().parent
    try:
        源 = _源码.resolve()
    except OSError:
        print(f"⚠ 未验：找不到源码树 {_源码}")
        print("判定=未验")
        return 3

    print(f"安装目录 = {装}")
    print(f"源码树　 = {源}")

    # ── 判据 4：安装目录不许就是源码树（否则判据空转） ──
    if 装 == 源 or 源 in 装.parents or 装 in 源.parents:
        print("✗ 安装目录与源码树重合/嵌套 ⇒ 本判据空转通过（口径 375：空集不构成判据）")
        print("判定=不达标")
        return 1

    源码文件们 = sorted(p for p in 源.glob("*.py"))
    if not 源码文件们:
        print(f"✗ 源码树里一个 .py 都没有（{源}）⇒ 空集 ⇒ 不构成判据")
        print("判定=不达标")
        return 1

    缺: list[str] = []
    差: list[str] = []
    for f in 源码文件们:
        g = 装 / f.name
        if not g.is_file():
            缺.append(f.name)
            continue
        if _哈希(g) != _哈希(f):
            差.append(f.name)

    名集 = {f.name for f in 源码文件们}
    多余 = sorted(p.name for p in 装.glob("*.py") if p.name not in 名集)

    print(f"src {len(源码文件们)} 个 .py · 缺 {len(缺)} · 内容不一致 {len(差)} · 安装目录多余 {len(多余)}")
    for 标, 列 in (("缺", 缺), ("不一致", 差), ("多余", 多余)):
        if 列:
            print(f"    {标}：{列[:8]}{' …' if len(列) > 8 else ''}")

    失 = False
    if 缺:
        print("✗ 有源码文件没装进去（装出来的包少文件）")
        失 = True
    if 差:
        print("✗ 已装内容与源码不一致 —— **装的是旧代码**（改完 src 忘了重装？）")
        失 = True
    if 多余:
        print("✗ 安装目录有源码树里没有的 .py（旧版本残留 ⇒ 你看到的和你读的不是同一份）")
        失 = True

    if 失:
        print("  修法：python tools\\打wheel.py --源树 <包根> --出 work\\_dist，"
              "再 pip install --no-index --no-deps --force-reinstall <whl>")
        print("判定=不达标")
        return 1
    print("判定=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())