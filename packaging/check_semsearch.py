r"""检索桥自检（部署机上跑，验"知识库到底连没连上"）。

    用法（在部署目录下）：
        .venv\Scripts\python.exe check_semsearch.py

为什么需要它：编排器自己的 doctor 只验编排器（Python 环境/判据门/profile），
**不验检索桥**。于是"部署完了"和"检索其实没接上"长得一模一样 —— 后者会静默降级成
"没有外挂知识"，而这正是最该被发现的那种失败（绿是假的）。

★ 子进程写权限提示：本机实测被 spawn 的进程无法在 `E:\Temp` 等公共临时目录建目录
  （WinError 5），所以本脚本**不建临时目录**，只做只读探测。
"""
import sys
from pathlib import Path

根 = Path(__file__).resolve().parent
sys.path.insert(0, str(根))
try:
    from agentscope_orchestrator import semsearch_link as S
except ImportError as exc:
    print(f"✗ 导入不了检索桥：{exc}")
    print("  ⇒ 先跑 install.ps1 把包装上")
    raise SystemExit(2)

失败: list[str] = []


def 查(条件: bool, 说明: str) -> None:
    print(f"  {'ok  ' if 条件 else 'FAIL'} {说明}")
    if not 条件:
        失败.append(说明)


def main() -> int:
    print("═══ 检索桥自检 ═══")

    # ① 配置
    配置 = S._配置文件()
    print(f"· 配置来源：{'semsearch_link.json（已读到）' if 配置 else '无（靠环境变量/自动探测）'}")
    for k in ("bin", "project_root", "index", "schema"):
        if 配置.get(k):
            print(f"    {k} = {配置[k]}")

    # ② 二进制
    二 = S.二进制()
    查(二 is not None, f"找到二进制：{二}")
    if 二 is None:
        print("\n✗ 没有可用二进制。装法：给 semsearch_link.json 的 bin，或设 SEMSEARCH_BIN，")
        print("  或把 semsearch 放进 PATH。没有它，编排器**不会**报错 —— 它只是没有外挂知识。")
        return 1

    # ③ 能力
    能力 = S.可用()
    if not 能力.get("可用"):
        print(f"\n✗ 二进制在 {二}，但索引读不到：")
        print(f"  {能力.get('原因')}")
        print("\n  常见原因：")
        print("    · 没配 project_root ⇒ 它不知道去哪个项目找 `.semsearch_index/concepts.json`")
        print("    · 配了但那份索引不存在（知识库还没建）")
        print("    · PATH 里那是个**旧版**二进制（没有库模式）⇒ 显式配 bin 指向新的")
        return 1

    print(f"· 索引条目：{能力.get('索引条目')} 条")
    查((能力.get("索引条目") or 0) > 0, "知识库非空")

    # ④ 真检索（拿**确定有料**的词当探针）
    #
    #   ★ 探针词要选库里真有的（2026-11-03 实测）：`判据`/`证据` 在这个库里是 **0 条**，
    #     拿它们当探针会把"桥是好的"报成红。`交互`(22)/`编码`(2)/`维度`(3) 才有料。
    for 词 in ("交互", "编码", "维度"):
        条 = S.条目(关键词=词)
        if 条:
            break
    查(len(条) >= 1, f"关键词检索返回条目（'{词}' 实得 {len(条)}）")

    块 = S.注入("交互", 上限字=300)
    查(0 < len(块) <= 520, f"注入块守预算（实得 {len(块)} 字）")
    查("##" in 块 or "- [" in 块, "注入块带标题与来源编号")

    空 = S.注入("这个词库里一定没有·zzzqqq")
    查(空 == "", "查不到时返回空串（不编造命中）")

    # ★ 这里要搜**装好的包目录**，不是部署目录顶层（顶层只有 .venv 和几个 md/whl，
    #   没有 .py ⇒ 必然 0 条，看着像能力坏了）。搜包目录实测 3 条。
    包目录 = 根 / ".venv" / "Lib" / "site-packages" / "agentscope_orchestrator"
    命中 = S.目录命中(str(包目录 if 包目录.is_dir() else 根), "def ", 上限=3)
    查(len(命中) >= 1, f"目录正则检索有命中（实得 {len(命中)}）")

    print()
    if 失败:
        print(f"✗ 检索桥：红 {len(失败)} 项 —— " + "；".join(失败))
        return 1
    print("✓ 检索桥可用：能读知识库、带来源、守预算、查不到不编")
    print("  ⇒ 编排器在修不动的时候会去这里找**别人踩过的坑**（带 PR 编号可追溯）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
