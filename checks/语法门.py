# -*- coding: utf-8 -*-
"""全称语法门：**每个模块都必须能编译**（不枚举、不看它有没有自检）。

── 为什么必须有这一条 ──

  全量回归里 17 个模块被记为「**无自检**」。而「无自检」只意味着**没跑 `--selftest`**
  —— 它**不检查模块本身能不能编译**。⇒ 一个**根本无法导入**的模块
  （语法错、括号不配、缩进崩），在回归里与 16 个正常模块**看起来一模一样**。

  ★ 这是本库反复踩的同一个病：**"没跑"看起来跟"绿"一样**。
  ★ 而且是**全称**层面的：不是"这个模块少了条自检"，
    而是"**任何**模块都可能编译不过而无人知"。

── 如实登记：本门的起因是一次**我自己的假警报**（2026-10-15）──

  我当时读到 `autodispatch.py:98` 显示为 `..., battch, ...`，判定"位置实参跟在
  关键字实参后面 = 语法错"，并据此写了本门。**那是错的。**

  用码点验（`<62><61><74><63><68><3d><62><61><74><63><68>`）才看清真值：
  那一行是 `battch`（关键字实参 `batch=batch`）—— **完全合法**。
  是**显示层**把 `batch=batch` 渲染成了 `battch`。

  ⇒ 教训（已并入纪律）：**凡是"我读到某段文本像是缺陷"的判断，
    必须用码点/哈希复核，不能凭渲染结果下结论。**
    文本渲染是有损的，而 `read_bytes` / 码点 / sha1 不是。

  ★ 所以本门**不是为了修那个不存在的错**，而是补上一个**真实存在**的层：
    `--selftest` 需要模块**自己实现**它才生效；本门**不需要任何模块配合**，
    对**每一个** `.py` 都成立 ⇒ 覆盖那 17 个"无自检"模块。
  ★ 与 `--selftest` 是两层：语法门管「能不能读」，自检门管「行为对不对」。
    一个模块可以语法全对而行为全错（那就该有自检）；
    但**语法错是更基本的一层**，不该被"无自检"这个词吞掉。

用法：
    python checks/语法门.py                 # 扫 src 包 + checks/
    python checks/语法门.py <目录> [<目录>]  # 只扫给定目录（齿探针用）
"""
import py_compile
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent / "src" / "agentscope_orchestrator"
_CHECKS = Path(__file__).resolve().parent


def _扫(目录: Path) -> list[Path]:
    if not 目录.is_dir():
        return []
    return sorted(p for p in 目录.glob("*.py") if p.stem != "__init__")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    目录s = [Path(a).resolve() for a in argv] or [_PKG, _CHECKS]

    fails: list[str] = []
    n = 0
    计 = []
    for d in 目录s:
        mods = _扫(d)
        计.append(f"{d.name} {len(mods)}")
        for f in mods:
            n += 1
            try:
                py_compile.compile(str(f), doraise=True,
                                   cfile=str(f) + ".syntaxgate.pyc")
            except py_compile.PyCompileError as e:
                # 只留最后一行（真正的错误行），并截短
                msg = [x for x in str(e).strip().splitlines() if x.strip()]
                fails.append(f"{f.name}: {msg[-1][:200] if msg else e}")
            except Exception as e:                      # noqa: BLE001
                fails.append(f"{f.name}: {type(e).__name__}: {str(e)[:160]}")
            finally:
                t = Path(str(f) + ".syntaxgate.pyc")
                if t.exists():
                    try:
                        t.unlink()
                    except OSError:
                        pass

    if n == 0:
        # ★ 「什么都没扫到」**不得**当成通过 —— 那正是本库"没跑看起来像绿"的病。
        #   真跑过：`checks/全量回归.py` 的汇总曾把 `checks/` 排除在计数外，
        #   于是一个红的探针被报成「红 0」。同一个病的另一面。
        print("语法门：★ 一个模块都没扫到（0/0）⇒ 判**红**，"
              "不得把「没扫到」当成通过。目录：" + "、".join(str(d) for d in 目录s))
        return 1
    if fails:
        print(f"语法门：{n - len(fails)}/{n} 通过，{len(fails)} 个模块**语法错**"
              f"（无法导入 ⇒ 回归报的「无自检」会把它们与正常模块混为一谈）")
        for x in fails:
            print("  ✗", x)
        return 1
    print(f"语法门：{n}/{n} 全绿（{' + '.join(计)}，"
          f"每个模块都能编译 ⇒ 「无自检」不再掩盖「读不了」）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
