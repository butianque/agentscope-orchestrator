# -*- coding: utf-8 -*-
"""`orch-kb` —— **指向任意文件夹，一条命令把它做成知识图谱**。

★★★ 这个入口存在的理由（用户原话）：

> 「**能把任意文件夹内所有内容都做成知识图谱，让 AI 知道自己有什么东西，
>   AI 才能应用自己的知识储备做计划，做执行，改代码**。」

`知库.py` 早就实现了三层（① 索引 ② 按需全扫 ③ 按需深读），
**但没有任何一条命令能调它** —— 只有测试与临时脚本调过。
⇒ 本模块就是那根线：把「任意文件夹」变成「可查的知识图谱」，并且**人/AI 读得懂**。

用法：
    orch-kb <文件夹>                  建图谱 + 报「你有什么」
    orch-kb <文件夹> --问 "某个词"      在哪（路径命中 + 内容命中）
    orch-kb <文件夹> --看 相对路径      打印那个文件的开头
    orch-kb <文件夹> --json            机读
    orch-kb <文件夹> --用旧            不重扫，直接用上次的索引（快）
    orch-kb <文件夹> --大文件 20        顺便列出最大的 20 个文件

★ 诚实边界（口径 92）：本命令只回答「**你有什么、在哪**」；
  它**不**回答「这段代码干什么」—— 那要真读文件（`--看`）。
★ 索引是**快照**（口径 367）：`--用旧` 时输出会写明「建于何时」，
  不会让一份旧索引看起来像刚扫的。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _能重配(流) -> bool:
    """★ 口径 387：库模块**不许在 import 时改全局状态**。

    这个函数是给 `main()` 用的（那是入口，不是 import 期）。
    判一百九十二 的负对照证明：谁在 import 期动 `sys.stdout`，谁就会被抓住。
    """
    try:
        return callable(getattr(流, "reconfigure", None))
    except Exception:                                      # noqa: BLE001
        return False


def _字节(n: float) -> str:
    for 单, 名 in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")):
        if n >= 单:
            return f"{n / 单:.1f}{名}"
    return f"{int(n)}B"


#: 后缀 → 人话（让「你有什么」读起来像一个项目，而不是一张后缀表）
语言 = {
    ".py": "Python", ".rs": "Rust", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".java": "Java", ".kt": "Kotlin",
    ".go": "Go", ".c": "C", ".h": "C 头", ".cpp": "C++", ".cc": "C++", ".hpp": "C++ 头",
    ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".m": "ObjC",
    ".lean": "Lean", ".v": "Coq", ".md": "文档", ".rst": "文档", ".txt": "文本",
    ".yml": "配置", ".yaml": "配置", ".toml": "配置", ".json": "数据",
    ".ini": "配置", ".cfg": "配置", ".xml": "数据", ".html": "网页", ".css": "样式",
    ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell", ".bat": "批处理",
    ".csv": "表格", ".xlsx": "表格", ".pdf": "PDF", ".png": "图片", ".jpg": "图片",
    ".svg": "图片", ".ico": "图片", ".lock": "锁文件", ".whl": "轮子",
}


def _报你有什么(根: Path, 盘: dict, 大文件: int, 打印=print) -> None:
    """把盘点结果讲成一句话级的「你有什么」——这是给**人**看的那一版。"""
    后缀分布 = 盘.get("后缀分布（前 N）") or {}
    顶层分布 = 盘.get("顶层段分布（前 N）") or {}
    print(f"\n  ── 「{根.name}」里有什么 " + "─" * 34)
    print(f"     文件 {盘['文件数']:,} 个 · {_字节(盘['总字节'])} · "
          f"{len(后缀分布)} 类后缀（前 {len(后缀分布)} 名）")
    # ① 语言构成（把后缀翻译成人话；**认不出的后缀也照样报**，口径 92）
    语 = {}
    未知 = {}
    for s, n in 后缀分布.items():
        k = 语言.get(s.lower() if isinstance(s, str) else s, None)
        if k:
            语[k] = 语.get(k, 0) + n
        else:
            未知[s] = 未知.get(s, 0) + n
    段 = [f"{k} {v:,}" for k, v in sorted(语.items(), key=lambda x: -x[1])[:9]]
    段 += [f"{k} {v:,}" for k, v in sorted(未知.items(), key=lambda x: -x[1])[:4]]
    if 段:
        print("     主要构成：" + " · ".join(段))
    # ② 顶层段（项目的骨架）
    if 顶层分布:
        print("     顶层：" + " · ".join(
            f"{k} {v:,}" for k, v in list(顶层分布.items())[:12]))
    # ③ 最大的文件（口径 92：顺手指出「一眼看去最重的是什么」）
    if 大文件 > 0:
        索 = Path(盘["索引文件"])
        if 索.is_file():
            try:
                行 = [json.loads(x) for x in 索.read_text(
                    encoding="utf-8").splitlines() if x]
                行.sort(key=lambda x: -x[2])
                print(f"     最大 {大文件} 个：")
                for 相, _s, n, _m, _t in 行[:大文件]:
                    print(f"        {_字节(n):>9}  {相[:88]}")
            except Exception as e:                          # noqa: BLE001
                print(f"     ★ 列大文件失败（{type(e).__name__}）⇒ 这一栏没有读数")
    print(f"     索引：{盘['索引文件']}")
    print(f"     ★ 建索引 {盘.get('耗时秒', '?')} 秒 · "
          f"排除了 {盘.get('排除的目录个数')}")


def main(argv: list[str] | None = None) -> int:
    if _能重配(sys.stdout):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            pass
    解 = argparse.ArgumentParser(
        prog="orch-kb",
        description="把**任意文件夹**做成知识图谱，并回答「你有什么 / 在哪 / 长什么样」",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例： orch-kb D:\\某个项目\n"
               "     orch-kb D:\\某个项目 --问 登录\n"
               "     orch-kb D:\\某个项目 --看 src/main.py --看头 80")
    解.add_argument("文件夹", help="要被做成知识图谱的目录（任意路径）")
    解.add_argument("--问", dest="问句", default="",
                    help="问一个词/一句话 ⇒ 报「在哪」（路径命中 + 内容命中）")
    解.add_argument("--看", dest="看路径", default="",
                    help="按相对路径打印一个文件的开头（配合索引里的相对路径）")
    解.add_argument("--看头", dest="看头", type=int, default=60, help="--看 打印多少行")
    解.add_argument("--大文件", dest="大文件", type=int, default=0,
                    help="顺便列出最大的 N 个文件（0=不列）")
    解.add_argument("--用旧", dest="用旧", action="store_true",
                    help="不重扫，直接用上次的索引（★ 会写明建于何时）")
    解.add_argument("--json", dest="json", action="store_true", help="机读输出")
    参 = 解.parse_args(argv)

    根 = Path(参.文件夹).expanduser()
    if not 根.is_dir():
        print(f"  ✗ 不是目录：{根}", file=sys.stderr)
        return 2
    try:
        from . import 知库
    except ImportError:                                    # 直接跑脚本时
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from agentscope_orchestrator import 知库        # type: ignore

    t0 = time.time()
    打印 = (lambda *a: None) if 参.json else (lambda *a: print("     " + "".join(map(str, a))))
    给出 = {}
    try:
        if 参.用旧:
            出 = 知库._库目录(根)
            汇档 = 出 / "知库.json"
            if not 汇档.is_file():
                print(f"  ✗ 没有旧索引（{汇档}）⇒ 去掉 --用旧 先建一次", file=sys.stderr)
                return 3
            盘 = json.loads(汇档.read_text(encoding="utf-8"))
            print(f"  ★ 用的是**旧索引**（`--用旧`）—— 建于 "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(盘.get('建于', 0)))}，"
                  f"**此后该文件夹的改动没有进去**")
        else:
            盘 = 知库.盘点(根, 打印=打印)
        给出["盘点"] = 盘

        if 参.问句:
            给出["问"] = 知库.问(根, 参.问句, 上限=12)
        if 参.看路径:
            给出["看"] = 知库.看(根, 参.看路径, 上限行=参.看头)
    except AssertionError as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return 4
    except Exception as e:                                 # noqa: BLE001
        print(f"  ✗ {type(e).__name__}: {e}", file=sys.stderr)
        return 5

    if 参.json:
        print(json.dumps(给出, ensure_ascii=False, indent=1))
        return 0

    _报你有什么(根, 盘, 参.大文件)

    if 参.问句:
        答 = 给出["问"]
        命 = 答.get("命中") or []
        print(f"\n  ── 「{参.问句}」在哪 " + "─" * 34)
        print(f"     路径命中 {答.get('路径命中数', 0)} · 内容命中 {答.get('内容命中数', 0)}"
              f" · 扫描器 {答.get('扫描器')} · 扫了 {答.get('扫了')}")
        if not 命:
            print("     ★ 一处都没命中 —— 这是**真的没有**，不是没查（索引与扫描器都跑过了）")
        for x in 命[:12]:
            相 = x.get("路径") if isinstance(x, dict) else x
            因 = x.get("为什么") if isinstance(x, dict) else ""
            print(f"       · {str(相)[:92]}" + (f"   ← {因}" if 因 else ""))
        if 答.get("边界"):
            print(f"     ★ 边界：{答['边界']}")

    if 参.看路径:
        内 = 给出["看"]
        print(f"\n  ── {参.看路径} 的开头 " + "─" * 30)
        for l in (内 or "").splitlines()[:参.看头]:
            print("     " + l)

    print(f"\n  ✓ 完成（{time.time() - t0:.1f} 秒）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
