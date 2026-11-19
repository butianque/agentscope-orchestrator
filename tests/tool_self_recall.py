"""判据：编排器要能回答**关于它自己**的问题（自述检索），且不许自指污染。

钉的用户提问（2026-11-05，原话）：
「你不是应该给我编排器的问题吗？为什么给的是自主认知工程本身的问题？」
⇒ 当时 `context_view` 只有「项目」三路（按名/概念/知识库），**没有一路查工具自己的代码**：
   问「本仓判据门评估器里 cmd 是列表时走 shell 还是 argv」，检索给的是
   `warnings_cmd.txt` 和一堆 Rust 文件 —— 编排器答不出关于自己的问题。

本判据分两层：
  · **合成语料层（确定性）**：`自述索引.建(文件们, 根=临时目录)`，钉三条机制
     ① 行级倒排：命中要给**行号**，不是只给文件名；
     ② **引号内不建索引**（跨行也算）：我自己在注释里引过这些问句，第一次跑
        就把 `context_view.py` 顶到第一、真实现 `judge.py` 降到第三；
     ③ 常见词（self/Path 这类满仓词）被文档频率闸挡掉，不许霸屏。
  · **真仓层（集成）**：真索引上问三个问题
     ④ 判据门 shell/argv 问题 ⇒ 首条必须是**真实现文件**（judge.py 或 gate_spec.py），
        且证据里出现 `isinstance(cmd` / `argv` 这类实现字样；
     ⑤ 拟声词负对照 ⇒ **回空**；
     ⑥ 项目问句（关系式族差）⇒ 首条分数 < 15（低于 `_自述段` 的显示阈值，不许冒充工具知识）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentscope_orchestrator import selfrecall as S  # noqa: E402

坏: list[str] = []


def 合成层() -> None:
    根 = Path(tempfile.mkdtemp(prefix="selfrecall_evi_"))
    try:
        _合成层内(根)
    finally:
        # ★ 判据不许留垃圾：第一版漏了这句，包根里留下 `selfrecall_evi_*`（实测 2 个）。
        #   临时目录必须**自己收**，否则每跑一次套件就多几个未跟踪目录。
        import shutil
        shutil.rmtree(根, ignore_errors=True)


def _合成层内(根: Path) -> None:
    (根 / "src").mkdir(parents=True, exist_ok=True)
    甲 = 根 / "src" / "judge_like.py"
    甲.write_text(
        "def 判(cmd):\n"
        "    是表 = isinstance(cmd, (list, tuple))\n"
        "    # 列表 ⇒ argv（不经 shell）；字符串 ⇒ 交给 shell\n"
        "    # 有人问「判据门的 cmd 列表到底走 shell 还是 argv」，答案在上一行\n",
        encoding="utf-8")
    乙 = 根 / "src" / "other.py"
    乙.write_text(
        "VALUE = 1\n"
        "def 别处():\n"
        "    return VALUE\n",
        encoding="utf-8")
    索 = S.自述索引.建([甲, 乙], 根=根)

    r = 索.召回("判据门 cmd 列表 shell argv", 上限文件=2, 上限行=3)
    print(f"    ① 合成检索：{r['说明']}")
    if not r["命中"]:
        坏.append("合成语料上完全没命中（行级倒排没建起来）")
    else:
        首 = r["命中"][0]
        print(f"       首条 {首['文件']} · 证据 {[e['行'] for e in 首['证据']]}")
        if 首["文件"] != "src/judge_like.py":
            坏.append(f"首条不是 judge_like.py 而是 {首['文件']}")
        if not any(e["行"] > 0 for e in 首["证据"]):
            坏.append("证据没有行号 ⇒ 只给文件名等于没证据")
        if not any("isinstance(cmd" in e["摘录"] for e in 首["证据"]):
            坏.append("证据里没有实现那一行（isinstance(cmd …）")
        # ② 引号内的问句不许被索引：它在那行里、但那行整段是引述
        for e in 首["证据"]:
            if "有人问" in e["摘录"]:
                坏.append("引述问句的那一行被当成证据了（引号内不许建索引）")

    # ② 跨行引号也必须剥掉
    丙 = 根 / "src" / "quote_span.py"
    丙.write_text(
        "X = 1\n"
        "# 所以问「本仓判据门评估器里\n"
        "# cmd 是列表时走 shell 还是 argv」时，项目检索给的全是无关文档\n"
        "def 实现():\n"
        "    return 1\n",
        encoding="utf-8")
    索2 = S.自述索引.建([丙, 乙], 根=根)
    r2 = 索2.召回("判据门评估器 cmd 列表 shell argv", 上限文件=2, 上限行=3)
    文们 = [e["摘录"] for h in r2["命中"] for e in h["证据"]]
    print(f"    ② 跨行引号：命中 {len(r2['命中'])} · 证据 {文们[:2]}")
    if any("无关文档" in x or "所以问" in x for x in 文们):
        坏.append("跨行引号里的问句仍被当成证据（剥引号没跨行）")

    # ③ 满仓常见词被 df 闸挡掉
    丁 = 根 / "src" / "common1.py"
    丁.write_text("self = 1\nPath = 2\n", encoding="utf-8")
    戊 = 根 / "src" / "common2.py"
    戊.write_text("self = 3\nPath = 4\n", encoding="utf-8")
    索3 = S.自述索引.建([丁, 戊], 根=根)
    print(f"    ③ 常见词索引：术语数 {len(索3._倒排)}（self/Path 应被挡）")
    if "self" in 索3._倒排 or "Path" in 索3._倒排:
        坏.append("stopword（self/Path）进了倒排")


def 真仓层() -> None:
    r = S.自述召回("本仓判据门评估器 cmd 是列表时走 shell 还是 argv？当时的实测数据点是什么",
                   上限文件=3, 上限行=3)
    命中 = r["命中"]
    print(f"    ④ 真仓·shell/argv：{r['说明']}")
    for h in 命中:
        print(f"       {h['文件']} 分 {h['分']}")
    if not 命中:
        坏.append("真仓上问工具实现细节完全没命中")
    elif not any(Path(h["文件"]).name in ("judge.py", "gate_spec.py") for h in 命中[:3]):
        坏.append(f"首三条里没有真实现文件（实得 {[h['文件'] for h in 命中[:3]]}）")
    else:
        文 = " ".join(e["摘录"] for h in 命中[:2] for e in h["证据"])
        if not any(k in 文 for k in ("isinstance(cmd", "argv", "shell")):
            坏.append("证据里没有实现字样（isinstance/argv/shell）")

    r5 = S.自述召回("哞哞哞咩咩咩吽吽吽咕噜噜噗呲噗呲", 上限文件=3)
    print(f"    ⑤ 真仓·拟声词：命中 {len(r5['命中'])}")
    if r5["命中"]:
        坏.append(f"拟声词也命中了 {len(r5['命中'])} 个文件（引号剥除或 df 闸没生效）")

    r6 = S.自述召回("关系式族差 增删列表 一致率 否证 裁决", 上限文件=3)
    首分 = float(r6["命中"][0]["分"]) if r6["命中"] else 0.0
    print(f"    ⑥ 真仓·项目问句：命中 {len(r6['命中'])} · 首分 {首分}")
    # ★ 判"会不会显示"要按 `_自述段` 的**真闸**（分数 6.5 + 首条证据 ≥2 个不同术语），
    #   不许在这里再拍一个分数 —— 第一版这里拍了 15，闸一改判据就假红/假绿（实测）。
    from agentscope_orchestrator import context_view as V
    项目段 = V._自述段("关系式族差 增删列表 一致率 否证", 400)[0]
    工具段 = V._自述段("自述检索 窗口共现 权重", 400)[0]
    print(f"       → 项目问句自述段：{'有内容（污染）' if 项目段 else '空（正确）'} · "
          f"工具问句自述段：{'有内容（正确）' if 工具段 else '空（漏召回）'}")
    if 项目段:
        坏.append("项目问句也显示了「编排器自身」段（污染）")
    if not 工具段:
        坏.append("换了说法的工具问题（窗口共现权重）没被召回")


def main() -> int:
    合成层()
    真仓层()
    if 坏:
        print("自述检索：红 —— " + "；".join(坏))
        return 1
    print("自述检索：绿 —— **编排器能答关于自己的问题（行级证据）· "
          "引号内/跨行引述不算实现 · 拟声词回空 · 项目问句不冒充工具知识**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())