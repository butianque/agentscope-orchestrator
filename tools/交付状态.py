# -*- coding: utf-8 -*-
"""交付状态一页纸（判据 #53）—— 把「已证明 / 未验 / 缺什么条件」合成**一份可复跑的文件**。

## 为什么需要它
仓里现在有 46 条判据、9 个门具、17 条变异，读数散在 `work/*.json` 里。
要回答「现在到底能不能交付」，得读五个文件再自己拼 —— 人不会天天拼，
于是**文档说的和实测的会慢慢分开**（口径 191/197：账与实测矛盾 ⇒ 当场改账）。

⇒ 本工具**不重测任何东西**，只做汇总（每条的真读数由它自己的门产生），
  生成 `work/交付状态.md` 一页纸 + 末行 `判定=OK / 不达标 / 未验`。

## 它判什么
① 全量判据读数在、且 **FAIL = 0、未验 = 0**；
② 门区分力 **100%**（抓住 == 注入成功 > 0）且门具数 ≥ 8；
③ 仓内指标：人工交互点 = 0、趋势无退化；
④ 工具面契约：工具数 > 0 且契约文件可解析；
⑤ **外部依赖项如实列**（N1 第二台机器 / N3 外部基准 / N10 客户端清单）——
   它们**永远**在这张纸上标「未验 · 缺什么」，不许因为"代码写完了"就消失。

## 诚实边界
· 这是**汇总**，不是复测：某个门自己判绿了，这里就照它报绿；门本身腐坏要靠 #51 去抓；
· 外部依赖项是**事实清单**，不是待办列表 —— 没有它们，那三项就永远 `未验`；
· 一页纸由机器写，人不许手改（手改会在下次跑时被覆盖）。
"""
from __future__ import annotations

import json
import pathlib
import sys

包根 = pathlib.Path(__file__).resolve().parents[1]
work = 包根 / "work"
验收 = work / "交付验收_最近.json"
区分 = work / "门区分力.json"
生产 = work / "仓内生产力.json"
契约 = work / "工具面契约.json"
输出 = work / "交付状态.md"

# 外部依赖项：这些是**事实**（缺的东西在机器之外），不是"还没做"。
# 每一行写清「缺什么 / 为什么本机做不了 / 给了就能做什么」。
外部依赖 = [
    ("N1 真跨机器续跑",
     "第二台 **Windows** 机器（或 VM/ISO）",
     "产品支持面是 win32，`doctor` 在 linux 上**自己拒判 67**（点名 4 处 Windows 行为依赖）；"
     "WSL2 只能证明「代码层跨平台」（110/110 模块 import 成功），不能冒充第二台机器。",
     "在两台机器间真跑一次「A 机断点 → B 机续跑」，把账本迁移前后逐条对比。"),
    ("N3 开放任务通用生产力",
     "外部基准（docker + SWE-bench Lite 类 ground truth）**或**人类工时基线",
     "生产力 = 产出/投入，需要**分母**；本机没有 docker、没有数据集、没有人类工时记录。"
     "自己造基准 = 自己出题自己判分（假绿的典型形状）；且 A/B 已证成功率无差异 ⇒ 不能声称更强。",
     "在给定基准上跑 5–10 题，报「解题率 / 耗时 / 人工介入次数」，与基线并排。"),
    ("N10 生态覆盖面",
     "目标客户端清单（VSCode / Claude Desktop / Cursor / …）",
     "生态没有完成态；不定清单就是无边际投入。",
     "逐个握手 + 各跑一个真实任务，报「握手成功 / 任务成功 / 失败原因」。"),
]


def _读(p: pathlib.Path) -> dict:
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                              # noqa: BLE001
        return {}


def _版本() -> str:
    import tomllib
    with (包根 / "pyproject.toml").open("rb") as f:
        return str(tomllib.load(f)["project"]["version"])


def main() -> int:
    验 = _读(验收)
    区 = _读(区分)
    产 = _读(生产)
    契 = _读(契约)

    坏: list[str] = []
    未验: list[str] = []

    # ① 全量判据
    if not 验:
        未验.append("缺 `work/交付验收_最近.json`（还没跑过一次全量交付验收）")
        条数 = 过 = 败 = 未 = 0
    else:
        条数 = int(验.get("条数") or 0)
        过 = int(验.get("PASS") or 0)
        # ★ R575e：原始三数（自洽校验用）。
        败 = int(验.get("FAIL") or 0)
        未 = int(验.get("未验") or 0)
        # ★ R575：元判据（51/52/53）不许自己判自己 —— 见 `仓内生产力.py` 同处注释。
        #   它们量的是**度量 apparatus**，不是产品行为；互相计入判定会「一趟红 ⇒ 永远红」。
        _元判据550 = {"51", "52", "53"}
        _明细550 = list(验.get("明细") or [])
        _非元550 = [x for x in _明细550 if str(x.get("id")) not in _元判据550]
        _仪器红550 = [x for x in _明细550 if str(x.get("id")) in _元判据550
                      and str(x.get("态")) != "PASS"]
        # ★ R575d：`条数` 是规模量，不许被过滤改写；判定用独立的 判过/判败/判未。
        if _非元550:
            判过 = sum(1 for x in _非元550 if str(x.get("态")) == "PASS")
            判败 = sum(1 for x in _非元550 if str(x.get("态")) == "FAIL")
            判未 = sum(1 for x in _非元550 if str(x.get("态")) == "未验")
        else:
            判过, 判败, 判未 = 过, 败, 未
        if 判败:
            坏.append(f"全量判据有 {判败} 条 FAIL")
        if 判未:
            未验.append(f"全量判据有 {判未} 条未验")
        if 过 + 败 + 未 != 条数:
            坏.append(f"读数自相矛盾：{过}+{败}+{未} != {条数}")
        if len(验.get("明细") or []) > 条数:
            坏.append(f"明细 {len(验.get('明细') or [])} 条 > 条数 {条数}")

    # ② 门区分力
    抓 = 注 = 具 = 0
    if not 区:
        未验.append("缺 `work/门区分力.json`")
    else:
        抓 = int(区.get("抓住") or 0)
        注 = int(区.get("注入成功") or 0)
        具 = len(区.get("门具") or [])
        if 注 <= 0:
            坏.append("门区分力分母为 0（没注入成功任何变异）")
        elif 抓 != 注:
            坏.append(f"门区分力 {抓}/{注}：还有 {注 - 抓} 条变异**存活**（门有洞）")
        if 具 < 8:
            坏.append(f"门具只有 {具} 个（< 8，覆盖面不足）")

    # ③ 仓内指标
    交互 = 产.get("人工交互点")
    if 产.get("人工交互点") is None:
        未验.append("缺 `work/仓内生产力.json`")
    elif int(交互) != 0:
        坏.append(f"人工交互点 = {交互}（无人值守被破坏）")

    # ④ 工具面契约
    工具数 = int(契.get("工具数") or 0)
    if 工具数 <= 0:
        未验.append("缺 `work/工具面契约.json`（工具面契约没生成）")

    版本 = _版本()

    # ── 写一页纸 ──
    行: list[str] = []
    行.append(f"# 交付状态（机判生成 · 版本 {版本}）")
    行.append("")
    行.append("> 本文件由 `python tools\\交付状态.py` 生成，**不手改**。")
    行.append("> 它是**汇总**，不是复测：每条判据的真读数由它自己的门产生；门本身有没有洞，由 #51 量。")
    行.append("")
    行.append("## 一、机判底线")
    行.append("")
    行.append("| 项 | 读数 | 结论 |")
    行.append("|---|---|---|")
    行.append(f"| 全量判据 | {条数} 条 · PASS {过} · FAIL {败} · 未验 {未} | "
             f"{'✅ 全绿' if (条数 and not 败 and not 未) else '❌ 未达成'} |")
    行.append(f"| 门区分力 | 抓住 {抓}/{注} · 门具 {具} 个 | "
             f"{'✅ 100%' if (注 and 抓 == 注) else '❌ 有洞/未验'} |")
    行.append(f"| 人工交互点 | {交互 if 交互 is not None else '未验'} 处 | "
             f"{'✅ 无人值守' if 交互 == 0 else '❌ 破坏无人值守'} |")
    行.append(f"| MCP 工具面 | {工具数} 个工具（契约可解析） | "
             f"{'✅' if 工具数 > 0 else '❌ 未生成'} |")
    行.append(f"| 判据读数时间 | {验.get('时间') or '—'} | — |")
    行.append("")
    行.append("## 二、外部依赖项（**未验**，不是「没做」）")
    行.append("")
    行.append("这三项缺的东西在**机器之外**，本机怎么努力都测不出来；"
             "拿近似的读数冒充，就是假绿。")
    行.append("")
    行.append("| 项 | 缺什么 | 为什么本机做不了 | 给了之后能做什么 |")
    行.append("|---|---|---|---|")
    for 名, 缺, 为什么, 之后 in 外部依赖:
        行.append(f"| **{名}** | {缺} | {为什么} | {之后} |")
    行.append("")
    行.append("## 三、本机已证明的（可复跑）")
    行.append("")
    行.append("· **交付验收**：`python tools\\交付验收.py` ⇒ 退出 0 = 全部机判底线达成；")
    行.append("· **门区分力**：`python tools\\门区分力.py` ⇒ 往产物注机械错误，看门抓不抓（三态：抓住/存活/无从注入）；")
    行.append("· **仓内指标**：`python tools\\仓内生产力.py` ⇒ 判据数/绿率/墙钟/区分力/交互点 + 与上一趟的趋势；")
    行.append("· **离线装**：`python tools\\离线装可用.py` ⇒ 干净 venv 里 `--no-index` 装出 11 个入口；")
    行.append("· **公仓可编译**：`python tools\\公仓可编译.py` ⇒ clone 公仓 → compileall → 逐模块 import；")
    行.append("· **部署一致**：`python tools\\部署一致性.py` ⇒ 部署目录与仓库同版本同内容；")
    行.append("· **跨平台**：`python tools\\平台拒判探针.py` ⇒ 非 win32 主动拒判 67 并点名依赖（不是假装支持）。")
    行.append("")
    行.append("## 四、判定")
    行.append("")
    if 坏:
        行.append("**不达标**：")
        for x in 坏:
            行.append(f"· ❌ {x}")
    elif 未验:
        行.append("**未验**（无 FAIL，但缺读数）：")
        for x in 未验:
            行.append(f"· ○ {x}")
    else:
        行.append("**机判底线全部达成**：全量判据全绿、门区分力 100%、人工交互点 0、工具面契约可解析。")
        行.append("")
        行.append("⇒ 可以交付使用；**外部依赖项（第二节）仍是未验**，不得当作已达成。")

    输出.parent.mkdir(parents=True, exist_ok=True)
    输出.write_text("\n".join(行) + "\n", encoding="utf-8")

    print(f"   ① 全量判据 = {条数} 条（其中非元判据 {len(_非元550) or 条数} 条）"
          f" · PASS {判过} · FAIL {判败} · 未验 {判未}")
    if _仪器红550:
        print("   ①b **仪器红（元判据 51/52/53）** = "
              + " · ".join(f"{x.get('id')} {x.get('态')}" for x in _仪器红550)
              + "（照实列出；**不计入**判定 —— 仪器不许自己判自己）")
    print(f"   ② 门区分力 = 抓住 {抓}/{注} · 门具 {具} 个")
    print(f"   ③ 人工交互点 = {交互 if 交互 is not None else '未验'}")
    print(f"   ④ 工具面契约 = {工具数} 个工具")
    print(f"   ⑤ 外部依赖项 = {len(外部依赖)} 项（如实列，未验）")
    print(f"   ★ 已写 {输出.relative_to(包根)}")
    if 坏:
        for x in 坏:
            print(f"   ✗ {x}")
        print("判定=不达标")
        return 1
    if 未验:
        for x in 未验:
            print(f"   ○ {x}")
        print("判定=未验")
        return 3
    print("判定=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())