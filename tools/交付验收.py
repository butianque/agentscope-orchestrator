# -*- coding: utf-8 -*-
"""交付验收：把《交付章程》第 2 节那张底线表**逐条真跑**，给一个退出码。

    python tools\\交付验收.py            # 全跑
    python tools\\交付验收.py --快        # 跳过最慢的几条（--dry-rework / P3 干净装）

★ 为什么必须有这个文件（而不是一份 md）：
  章程写成散文，就只能靠人读、靠人记、靠人自觉 —— 那正是"底线不清晰"的来源。
  所以底线的**唯一权威**是本文件里的 `底线表`；`交付章程.md` 是它的说明书。
  本文件还会反向核对：**表里每条 id 都必须在章程里出现过**，否则报「表与章程不一致」。

★ 三态纪律（口径 311）：PASS / FAIL / **未验**。
  跑不起来的环境问题记「未验」，**不许**记成 PASS，也**不许**混进 FAIL 里冒充产品缺陷。

★ 退出码：0 = 全部 PASS（未验不算 PASS）；1 = 有 FAIL；2 = 有未验但无 FAIL。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import re as _re
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

包根 = pathlib.Path(__file__).resolve().parents[1]
章程 = 包根 / "交付章程.md"
日志根 = 包根 / "work" / "_交付验收"
日志根.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════
# 底线表 —— 唯一权威。id 必须与《交付章程.md》第 2 节一一对应。
#   argv      : 真命令行（第一个是 exe 名，会解析成 .venv\Scripts\<名>.exe）
#   expect    : "zero" 退出 0 / "nonzero" 退出非 0
#   must      : 输出里必须出现的片段（关键读数，不只看退出码）
#   forbid    : 输出里不许出现的片段（例如 Python Traceback）
#   max_s     : 墙钟上限（口径 390：慢的机制等于没有的机制）
#   env       : 额外环境变量；env_清空 = 清掉这些变量（造"全新状态根"）
# ══════════════════════════════════════════════════════════════════════
底线表: list[dict] = [
    # ── 2.1 入口可用性
    dict(id="1", 名="orch-goal --help", argv=["orch-goal", "--help"], expect="zero", max_s=12),
    dict(id="2", 名="orch-goal --dry-machine", argv=["orch-goal", "--dry-machine"], expect="zero", max_s=300),
    dict(id="3", 名="orch-goal --dry", argv=["orch-goal", "--dry"], expect="zero", max_s=300),
    dict(id="4", 名="orch-goal --dry-multi", argv=["orch-goal", "--dry-multi"], expect="zero", max_s=300),
    # ★ #5 的底线不是"快"，是"**必须给裁决**"（口径 390）。
    #   --dry-rework 真跑真门（本仓 2354 单元），工程量决定时长；
    #   所以给它一个明确预算，超预算必须是**退出码 4 + wall_timeout 回执**，
    #   而不是无声挂住让外部杀掉。见 #16。
    dict(id="5", 名="orch-goal --dry-rework（预算内到终态）",
         argv=["orch-goal", "--dry-rework", "--max-wall", "1500"], expect="zero", max_s=1700),
    dict(id="16", 名="反例 墙钟闸有齿（--max-wall 15）",
         argv=["orch-goal", "--dry-rework", "--max-wall", "15"], expect="nonzero", max_s=120,
         must=["R544 墙钟超时"], forbid=["Traceback"]),
    dict(id="6", 名="orch-goal --selftest", argv=["orch-goal", "--selftest"], expect="zero", max_s=1500,
         must=["自检：全绿", "失败 0"]),
    dict(id="7a", 名="orch-run --help", argv=["orch-run", "--help"], expect="zero", max_s=120),
    dict(id="7b", 名="orch-run --selftest", argv=["orch-run", "--selftest"], expect="zero", max_s=600),
    dict(id="8", 名="orch-check --help", argv=["orch-check", "--help"], expect="zero", max_s=12, must=["用法"]),
    dict(id="9", 名="orch-check（无参）", argv=["orch-check"], expect="zero", max_s=600),
    dict(id="10", 名="orch-doctor --help", argv=["orch-doctor", "--help"], expect="zero", max_s=12, must=["用法"]),
    dict(id="11", 名="orch-doctor（全新状态根）", argv=["orch-doctor"], expect="zero", max_s=600,
         must=["可以开工"], env={"ORCH_STATE_DIR": "@全新@"}),
    dict(id="12", 名="orch-gaps --selftest", argv=["orch-gaps", "--selftest"], expect="zero", max_s=300,
         must=["35/35"]),
    dict(id="13a", 名="orch-kb --help", argv=["orch-kb", "--help"], expect="zero", max_s=60),
    dict(id="13b", 名="orch-log", argv=["orch-log"], expect="zero", max_s=120),
    dict(id="13c", 名="orch-tree", argv=["orch-tree"], expect="zero", max_s=120),
    dict(id="13d", 名="orch-mcp --help", argv=["orch-mcp", "--help"], expect="zero", max_s=120),
    dict(id="14", 名="orch-init --help", argv=["orch-init", "--help"], expect="zero", max_s=60),
    dict(id="15", 名="orch-plan --help", argv=["orch-plan", "--help"], expect="zero", max_s=60),
    # ── 2.3 反例（区分力）
    # ★ #33 阶段分离棘轮（2026-11-18 加入）：A1/A2 的项目名词命中数**只许降不许升**。
    #   为什么不是"清零才算绿"：实测 A1 命中 77 处（其中 36 处在**代码行**，是真功能性泄漏），
    #   一次性清零会逼人把带真实读数的事故注释改成空话。棘轮既能挡住新增，又允许分批清理。
    dict(id="33", 名="阶段分离棘轮（A1/A2 只降不升）",
         argv=["python", "tools/阶段分离棘轮.py"], expect="zero",
         must=["判定=OK", "A1="], max_s=90),
    # ★ #34 拆树判决（2026-11-18 加入）：R556 前「删不掉」被 ignore_errors 吞掉，
    #   main --clean 照打「已删」并退出 0 ⇒ 假绿（实测：子进程占用工作树时）。
    dict(id="34", 名="拆树判决（删不掉必须非 0）",
         argv=["python", "tools/拆树判决探针.py"], expect="zero",
         must=["判定=OK", "判决给"], max_s=180),
    # ★ #35 跨机续跑（N1，2026-11-18 加入）：状态 bundle 的**核对**必须真有牙齿 ——
    #   逐字节 sha256 一致 · 不覆盖 · 篡改包**整包拒收且零落盘**。
    dict(id="35", 名="跨机续跑（状态 bundle 核对）",
         argv=["python", "tools/跨机续跑探针.py"], expect="zero",
         must=["判定=OK", "整包拒收"], max_s=120),
    # ★ #36 覆盖核算（P1 · 判据 #21/#22，2026-11-18 加入）：守恒式 + 每文件带 reason。
    #   原件只报一个「差」（减法，不是守恒式）⇒ 差里混着"本来就不该进图"与"真漏了"，两边都判不了。
    dict(id="36", 名="覆盖核算（守恒式 + reason 覆盖）",
         argv=["python", "tools/覆盖核算.py"], expect="zero",
         must=["守恒=平", "跳过缺原因=0", "判定=OK"], max_s=300),
    # ★ #37 图谱缺口归类（P1 · 判据 #23/#24，2026-11-18 加入）：把「无符号」与「孤立点」
    #   摊开分真缺陷；#23 目标 5%，当前 8%（棘轮只降）。
    dict(id="37", 名="图谱缺口归类（#23 棘轮 / #24 真缺陷=0）",
         argv=["python", "tools/图谱缺口归类.py"], expect="zero",
         must=["判定=OK", "真缺陷（有主体代码却抽不出符号） = 0"], max_s=300),
    # ★ #38 悬空锚定归零（P2 · 判据 #25/#26，2026-11-18 加入）：A 级锚定边只认**活代码**点名，
    #   注释里提到不算边（实测这一条把 495 条边里的 85% 打成注释、准确率 15%→100%）。
    dict(id="38", 名="悬空锚定归零（#25 守恒+登记 / #26 边准确率）",
         argv=["python", "tools/悬空锚定归零.py"], expect="zero",
         must=["判定=OK", "守恒=平", "未登记=0"], max_s=600),
    # ★ #39 语义三替身（P4 · 判据 #27，2026-11-18 加入）：语义本身不可判，
    #   这里判它的三个替身：契约一致 / 不变量 / **变异注进产物看门抓不抓得住**。
    dict(id="39", 名="语义三替身（契约/不变量/变异区分）",
         argv=["python", "tools/三替身绿率.py"], expect="zero",
         must=["判定=OK", "三替身绿率"], max_s=900),
    # ★ #40 下游对账归类（P5 · 判据 #28，2026-11-18 加入）：**先修尺子再量** ——
    #   原报表 2276 vs 2200+0+8=2208 差 68 个文件（尺子漏了一桶），修后守恒平、真差 0。
    dict(id="40", 名="下游对账归类（#28 守恒+可复核+真差=0）",
         argv=["python", "tools/下游对账归类.py"], expect="zero",
         must=["判定=OK", "守恒：", "真差 = 0"], max_s=300),
    # ★ #41 平台拒绝（N2 · 判据 #30，2026-11-18 加入）：非 win32 必须显式拒绝（67），
    #   不许静默往下走；并带正控（真实平台不许 67）。
    dict(id="41", 名="平台拒绝（非 win32 显式 67 + 正控）",
         argv=["python", "tools/平台拒绝探针.py"], expect="zero",
         must=["判定=OK", "恰好 67"], max_s=600),
    # ★ #42 MCP 工具面契约（N10 · 判据 #29，2026-11-18 加入）：契约从**活的** FastMCP
    #   注册表现算，独立进程可解析、覆盖全部工具、schema 自洽。
    dict(id="42", 名="MCP 工具面契约（#29 可机读+全覆盖）",
         argv=["python", "tools/工具面契约.py"], expect="zero",
         must=["判定=OK", "独立进程解析成功", "覆盖全部工具"], max_s=900),
    # ★ #43 阶段分离（N11 · 判据 #33 收紧，2026-11-18）：尺子改用 tokenize —— **文档字符串
    #   不是代码**。原来 `line.split("#")` 把散文当代码（36 条里大半是读数叙事）。
    #   现判「代码行泄漏 = 0」（项目名写死在**代码行**才是功能性泄漏），并守恒 总=代码+文档。
    dict(id="43", 名="阶段分离（代码行泄漏=0 · tokenize 分档）",
         argv=["python", "tools/阶段分离棘轮.py"], expect="zero",
         must=["判定=OK", "代码行泄漏=0"], max_s=600),
    # ★ #44 派生门端到端（N7 剩余）：把「判据 P → 派生门 → 真判」连起来问一句
    #   「派生出来的门**真的能判、且判得出坏东西吗**」。每段各自绿 ≠ 连起来能判。
    dict(id="44", 名="派生门端到端（P→门→真判→坏产物逐条变红）",
         argv=["python", "tools/派生门端到端.py"], expect="zero",
         must=["判定=OK", "来源唯一违例 0", "派生确定 = 是"], max_s=420),
    # ★ #45 可证伪命题（P6）：命题**跑之前预注册**（含「若被否」）、判据**第三方**、
    #   **阴性结果必入账**。自检会故意喂一个阴性结果，并断言它真的进了账（不是被过滤）。
    dict(id="45", 名="可证伪命题（预注册+第三方判据+阴性必入账）",
         argv=["python", "tools/可证伪命题.py"], expect="zero",
         must=["判定=OK", "阴性入账 = True", "已注册预注册"], max_s=300),
    # ★ #46 装机一致性：**已装包必须与源码逐字节一致**。
    #   本轮实测到的洞：改完 src 忘了重装 ⇒ 已装是旧代码，而 39 条判据全绿（它们跑源码树）。
    #   口径 388 第三层：判据测函数 · 交付测入口 · **部署测安装产物**。
    dict(id="46", 名="装机一致性（已装 == 源码，逐文件 sha256）",
         argv=["python", "tools/已装一致性.py"], expect="zero",
         must=["判定=OK", "内容不一致 0"], max_s=120),
    # ★ #47 ⑧ 的区分力（N9）：把 ⑧ 的**判决本体**抽成纯函数后，用**合成回执**注入变异量它。
    #   原来 ⑧ 只有"真模型能改对过一次"这一个事实，量区分力就得再花一次真模型调用 ⇒ 永远没人量。
    dict(id="47", 名="⑧ 的区分力（注入假绿回执，判决必须抓住）",
         argv=["python", "tools/八号区分力.py"], expect="zero",
         must=["判定=OK", "区分力 = 6/6", "未验口径 = 对"], max_s=300),
    # ★ #48 部署一致性：**部署目录里到底是哪一版**。
    #   实测：E:\基础设施\orch-deploy 装的是 0.13.11，仓库 0.13.17，差 6 版，而 39 条判据全绿。
    #   判据只测了源码树和构建产物，"部署漂移"是覆盖盲区 —— 用户问"能直接用吗"没有人能机答。
    # ★ #55 轮次编号对账（2026-11-20 加入）：**一个 R 编号只能有一个含义**。
    #   真事故：本轮我连写 10 个新编号 R547…R556，收尾才发现**全被用过了**
    #   ⇒ 查 R552 会得到两个完全不同的答案（"门加成功标记" vs "无清单文件的语言兜底"）。
    #   ★ 这类错**不报错、不崩溃**，只是让回查失效 —— 而回查是这个仓唯一的经验载体。
    #   判据：新号段（> R571）必须登记；别名不许被两处认领；已知冲突必须带**可数**到期条件
    #   且到期后必须清理（否则它变成挡箭牌，挡住新出现的真冲突 —— R298）。
    dict(id="55", 名="轮次编号对账（一个编号一个含义 · 新编号必登记 · 到期条件可数）",
         argv=["python", "tools/轮次编号对账.py"], expect="zero",
         must=["判定=OK", "别名冲突 0", "未登记 0"], max_s=120),
    # ★ #56 行尾一致性（2026-11-20 加入）：**改文件不许把行尾弄脏**。
    #   同一天踩了两次、同一个根因：`Path.write_text` 在 Windows 上会翻译行尾
    #   （默认 newline=None ⇒ \n 变 \r\n）；明知要保 CRLF 又手写 replace 的，会被**再翻译一次**
    #   ⇒ 每行变 \r\r\n。两次都**不报错、语法照样对**，唯一症状是
    #   `git diff --stat` 里那个大得离谱的数字（bootstrap.py 2618 行 / 交付验收.py 644 行）。
    #   ★ 它还会污染「注入留残为零」：几千行假改动里，真留残看不见。
    #   判据：①每个文件行尾纯 CRLF 或纯 LF（不许混合）②与 `git show HEAD:` 一致
    #   ③不许出现 `\r\r\n`（write_text 事故指纹）。
    dict(id="56", 名="行尾一致性（纯 CRLF 或纯 LF · 与 HEAD 一致 · 无双重翻译）",
         argv=["python", "tools/行尾一致性.py"], expect="zero",
         must=["判定=OK", "与 git 期望形态一致", "0 处"], max_s=180),
    # ★ #57 文档脚本可达（R591 · 2026-11-21 加入）：文档点名的**脚本**必须存在且随仓走。
    #   两次真事故同一个根因：文档/判据依赖的东西住在**不会随仓走**的目录里
    #   （R584 登记表放 work/；R591 改写器放 work/R536/）。
    #   只判脚本后缀（运行期产物 .json/.jsonl 不判），不判 `src/`（那是目标仓的路径）。
    dict(id="57", 名="文档脚本可达（点名的脚本存在 ∧ 已进 git）",
         argv=["python", "tools/文档脚本可达.py"], expect="zero",
         must=["判定=OK", "全部可达"], max_s=120),
    # ★ #49 离线装可用（2026-11-19 加入）：install.ps1 承诺"从 wheels/ 离线装（不联网也能装）"，
    #   但 wheels/ 里只有编排器自己的轮子 ⇒ 干净机器上离线装装出 doctor=64（缺 pyyaml/httpx/mcp）。
    #   判据：#46/#48 都只测"已装/部署目录"，没人测"按发货承诺装一遍能不能用" —— 这就是那一栏。
    dict(id="49", 名="离线装可用（全新 venv + --no-index 装 + 握手）",
         argv=["python", "tools/离线装可用.py"], expect="zero",
         must=["判定=OK", "离线装 rc=0", "工具"], max_s=900),
    # ★ #50 公仓可编译可导入（2026-11-19 加入）：**发布出去的那份字节**没人测过。
    #   真事故：公仓推到 0.13.20，本地 43 条全绿，但公仓 HEAD 里有一份未完成的 WIP 编辑
    #   （bootstrap 第 574 行未闭合 + 字符串嵌双引号）⇒ clone 下来 `import bootstrap` 直接 SyntaxError。
    #   本判据只回答一个问题：**把公仓 clone 下来，能不能编译、能不能 import、版本对不对。**
    # ★ #51 门区分力（2026-11-19 扩版）：N4「语义正确性不可判」的**正当替代** ——
    #   不可判 ≠ 不可量：往产物注机械错误，看门抓不抓。旧版只量 3 条变异 × 1 个门具（靶全在
    #   workspace.py）；现扩到 **6 条 × 4 个门具 × 3 类产物**（源码 / 打包件 / 公仓提交）。
    #   ★ 它第一次跑就抓到一个**真洞**：变异「去掉 --clean 防误删护栏」此前**存活**
    #     （④ 只测占用/释放，没测"任意路径"）⇒ 探针补 ⑤ 后才 6/6。
    # ★ #52 仓内生产力（2026-11-19 加）：N3「通用生产力」的**可机判替代**。
    #   N3 要分母（人类基线/外部基准），本机没有 ⇒ 永远 UNVERIFIABLE。但「不可判」≠「不记账」：
    #   把交付机器的**趋势指标**机判化（判据数/绿率/墙钟/区分力/人工交互点/台账真事故数），
    #   每次全量跑完重跑一次即可看是否退化。它**明确不答**"比外部强"（输出里写着边界）。
    # ★ #53 交付状态一页纸（2026-11-20 加）：把「已证明 / 未验 / 缺什么条件」合成**一份可复跑的文件**。
    #   不重测任何东西（真读数由各自的门产生），只汇总 ⇒ 防「文档说的和实测的慢慢分开」（口径 191/197）。
    #   外部依赖项（N1/N3/N10）**永远**在这张纸上标未验 —— 不许因为"代码写完了"就消失。
    dict(id="53", 名="交付状态一页纸（汇总 + 外部依赖项如实列）",
         argv=["python", "tools/交付状态.py"], expect="zero",
         must=["判定=OK", "外部依赖项 = 3 项", "已写 work"], max_s=300),
    dict(id="52", 名="仓内生产力（判据数/绿率/墙钟/区分力/交互点，机判趋势）",
         argv=["python", "tools/仓内生产力.py"], expect="zero",
         must=["判定=OK", "人工交互点 = 0", "无外部效度", "趋势"], max_s=300),
    dict(id="51", 名="门区分力（18 条机械变异 × 10 个门具 × 4 类产物）",
         argv=["python", "tools/门区分力.py"], expect="zero",
         # ★ R555：判据串**不许**写死「18/18」—— 那个数字跟上游状态耦合：
         #   #46/#48 真红 ⇒ 仓内生产力/交付状态 基线红 ⇒ 3 条变异验不了
         #   ⇒ 旧串失配 ⇒ #51 额外记一条红 ⇒ **1 个真红看起来像 5 个红**（R550 的老毛病）。
         #   要的是「**注得进的都抓住了**」（100%）+「未验**有归因**」：
         #   归因=无 / 上游真红 / 仪器自指 / 读数读不到，四种机器可读（仪器自指时退出码 5）。
         must=["判定=OK", "＝ 100%", "未验归因=", "门具 10 个"], max_s=1800),
    # ★★★★★ #54 齿单测（2026-11-20 加）：**一条齿必须能单独跑、也要能单独被咬**。
    #   真代价（R540 的教训）：那条齿改了 3 轮才不红，每轮验证跑 `--selftest`
    #   **2392 / 2764 秒**；而它本身只要 **4 秒**。改一行等 40 分钟 ⇒ 人会开始
    #   "看着差不多就过" ⇒ **假绿的温床**。
    #   这条同时机判两件事：① 齿在干净源码上必须全绿（不许恒红）；
    #   ② 每条变异都必须让它变红（不许是装饰）。全程 ~20 秒。
    dict(id="54", 名="齿单测（能单跑的齿逐条跑 + 逐条变异咬合，秒级）",
         argv=["python", "tools/齿单测.py", "--变异"], expect="zero",
         must=["是真齿", "红 0", "咬住"], max_s=600),
    dict(id="50", 名="公仓可编译可导入（clone → compileall → 逐模块 import）",
         argv=["python", "tools/公仓可编译.py"], expect="zero",
         must=["判定=OK", "模块数", "导入失败 = 0"], max_s=900),
    dict(id="48", 名="部署一致性（部署目录 == 仓库版本 == 源码内容）",
         argv=["python", "tools/部署一致性.py"], expect="zero",
         must=["判定=OK", "部署包 vs 仓库源码"], max_s=180),
    dict(id="N1", 名="反例 orch-kb 不存在的目录", argv=["orch-kb", r"D:\不存在xyz_交付验收"],
         expect="nonzero", max_s=60, must=["不是目录"], forbid=["Traceback"]),
    dict(id="N2", 名="反例 orch-plan --root 不存在的目录", argv=["orch-plan", "--root", r"D:\不存在xyz_交付验收",
                                                            "--goal-json", "@@规约@@"],
         expect="nonzero", max_s=120, must=["计划器拒发"], forbid=["Traceback"]),
]

慢的 = {"5", "7b", "9", "11", "P3"}


def _exe(名: str) -> pathlib.Path:
    return 包根 / ".venv" / "Scripts" / (名 + ".exe")


def _规约文件() -> pathlib.Path:
    """给 N2 用的一份合法规约（门类型必须是合法的那几种）。"""
    import json
    文 = 日志根 / "_规约.json"
    文.write_text(json.dumps({
        "name": "交付验收探针", "pre": [], "constraints": [],
        "gates": [{"type": "compile", "cmd": ["cmd", "--help"], "expect_exit": 0,
                   "cwd": ".", "timeout": 30}],
        "acceptance": [],
    }, ensure_ascii=False), encoding="utf-8")
    return 文


# ★ R586：Windows 的 NTSTATUS 退出码在十进制下完全不可读（0xC000013A = 3221225786）。
#   实测：`#5` 报「退出码 3221225786」，单看这个数字**查不出下一步该做什么** ——
#   而它其实是 STATUS_CONTROL_C_EXIT（被外部终止），单独重跑同一条就 PASS 353.6s。
#   ⇒ 退出码后面直接跟上含义；认不出的照实写「未收录」（不猜）。
_退出码含义 = {
    3221225786: "0xC000013A = STATUS_CONTROL_C_EXIT：**被外部终止**（Ctrl+C / 控制台关闭 / 上层杀进程）"
                "⇒ 不是它自己算错，先看是不是并发跑或被杀",
    3221225477: "0xC0000005 = STATUS_ACCESS_VIOLATION（段错误）",
    3221225725: "0xC00000FD = STATUS_STACK_OVERFLOW",
    3221226505: "0xC0000409 = STATUS_STACK_BUFFER_OVERRUN（快速失败）",
    -9: "被本判据按墙钟上限杀掉（口径 390）",
    -1: "进程未能启动或被信号终止",
}


def _退出码(rc: int) -> str:
    """`rc` 的十进制 + 十六进制 + 含义（认不出就写未收录，不猜）。"""
    if rc in _退出码含义:
        return f"{rc}（{_退出码含义[rc]}）"
    if rc < 0 or rc >= 0xC0000000:
        return f"{rc}（0x{rc & 0xFFFFFFFF:08X} · 含义未收录 ⇒ 别猜，把 .out/.err 原文贴出来）"
    return str(rc)


def _跑(项: dict, 全新根: pathlib.Path) -> tuple[str, str, float, str]:
    """返回 (判定, 摘要, 秒, 日志路径后缀)。判定 ∈ PASS/FAIL/未验。"""
    名 = str(项["argv"][0])
    if not _exe(名).exists():
        return "未验", f"{名}.exe 不存在（环境没装好，不是产品缺陷）", 0.0, ""
    argv = list(项["argv"])
    # ★ 替换时必须**带上 `@`** —— `auto_goal` 只认「以 @ 开头」才是文件路径，
    #   漏掉 @ 它会把路径字符串当 JSON 解析，报「Expecting value: line 1 column 1」。
    #   （这一条我自己踩过一次，见 work/_交付验收/N2.out）
    argv[1:] = [a.replace("@@规约@@", "@" + str(_规约文件())) for a in argv[1:]]
    if any(a.startswith("@@" ) for a in argv):
        return "未验", "用例自身参数没替换干净", 0.0, ""

    环 = dict(os.environ)
    for k in ("TMPDIR", "TMP", "TEMP"):
        环.pop(k, None)
    环["PYTHONIOENCODING"] = "utf-8"
    环["PYTHONUTF8"] = "1"
    环.setdefault("ORCH_MEM_CTX", "0")
    for k, v in (项.get("env") or {}).items():
        环[k] = str(全新根 if v == "@全新@" else v)

    标 = 项["id"].replace("/", "_")
    so, se = 日志根 / f"{标}.out", 日志根 / f"{标}.err"
    t0 = time.time()
    try:
        with so.open("wb") as fo, se.open("wb") as fe:
            p = subprocess.Popen([str(_exe(名)), *argv[1:]], stdout=fo, stderr=fe,
                                 stdin=subprocess.DEVNULL, cwd=str(包根), env=环,
                                 # ★ 0x8 = DETACHED_PROCESS ⇒ 子进程**没有控制台**；
                                 #   而子进程内部（21 处 subprocess 调用，全部默认 flags）再起
                                 #   孙进程时，Windows 会给孙进程**新建一个可见控制台** ⇒
                                 #   每跑一条用例弹一个空黑窗（里面没内容是因为输出已重定向到
                                 #   .out/.err）。47 条底线 ⇒ 几十次弹窗。
                                 #   改成 CREATE_NO_WINDOW(0x8000000)：子进程有一个**隐藏**控制台，
                                 #   孙进程**继承**它而不再新建 ⇒ 整条链一个窗口都不弹，
                                 #   21 处调用点一行都不用改。Ctrl+C 免疫由 0x200 保留。
                                 creationflags=0x08000000 | 0x00000200)
            rc = p.wait(timeout=float(项["max_s"]))
        超 = False
    except subprocess.TimeoutExpired:
        try:
            p.kill()
        except Exception:                # noqa: BLE001
            pass
        rc, 超 = -9, True
    dt = time.time() - t0
    出 = so.read_text(encoding="utf-8", errors="replace") + "\n" + se.read_text(encoding="utf-8", errors="replace")
    出 = "\n".join(l for l in 出.splitlines()
                   if "orchestrator] INFO" not in l and "临时目录降级" not in l)

    if 超:
        return "FAIL", f"超过 {项['max_s']}s 未到终态（口径 390：慢的机制等于没有的机制）", dt, 标
    好 = (rc == 0) if 项["expect"] == "zero" else (rc != 0)
    if not 好:
        # ★★★★★ R551：**FAIL 摘要必须自带证据。**
        #   真事故：批量里 #42 报「退出码 1」，而它的 .err 被我同时跑的另一趟覆盖了
        #   ⇒ 事后完全查不出原因，只能重跑。判据不能要求人**事后复现**才拿到证据。
        _尾 = [l.strip() for l in 出.splitlines() if l.strip()]
        _尾 = [l for l in _尾 if not l.startswith("  ")] or _尾
        _证 = (" ｜ " + " ⏎ ".join(_尾[-3:]))[:420] if _尾 else ""
        return "FAIL", f"退出码 {_退出码(rc)}（期望 {'0' if 项['expect'] == 'zero' else '非 0'}）{_证}", dt, 标
    缺 = [m for m in 项.get("must", []) if m not in 出]
    if 缺:
        return "FAIL", f"退出码对，但关键读数缺失：{缺}", dt, 标
    忌 = [f for f in 项.get("forbid", []) if f in 出]
    if 忌:
        return "FAIL", f"输出里出现不许有的东西：{忌}", dt, 标
    要 = "；".join(项.get("must", [])) or "退出码"
    return "PASS", f"退出码 {rc} · {要}", dt, 标


def _py数(包根: pathlib.Path, *, 走git: bool) -> int:
    """这条打包路径**应产出的 .py 条数**（现算，不写死）。

    ★ R570：原判据要求输出里出现字面量 `.py 109` —— 一加文件就假红，
      而红说的是"数字变了"，不是"打包坏了"（口径 311）。
    ★ 两条路的真值不同：`--源树` 的真值是**工作区**；走 git 的真值是 `git ls-files`。
      工作区有未提交文件时两者本来就会差，那不是缺陷 —— 拿同一个数卡两条路才是假红。
    """
    if 走git:
        try:
            r = subprocess.run(["git", "ls-files", "src"], cwd=str(包根),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            return sum(1 for x in (r.stdout or "").splitlines() if x.endswith(".py"))
        except Exception:                      # noqa: BLE001
            return -1                          # 拿不到 ⇒ 判必红（不许静默放过）
    return sum(1 for _p in (包根 / "src").rglob("*.py"))


def _打包路径() -> list[dict]:
    """P1/P2：两条打包路径都必须产出 .py 109 的 wheel 且自查非空。"""
    出 = []
    打 = 包根 / "tools" / "打wheel.py"
    for 标, argv, 名, 要自查 in (
        ("P1", [str(包根 / ".venv" / "Scripts" / "python.exe"), "-B", str(打), "--源树", str(包根),
                "--出", str(日志根 / "_dist1")], "打wheel --源树", False),
        # ★ P2 走 git 那条路**有**「一个不少」的自查（git 件 vs 包件的守恒等式），
        #   P1 走工作区目录没有（工作区就是真值，没有第二个集合可比）。
        #   判据不能一条通吃两条路 —— 那会造出"P1 永远红"这种假红（口径 311）。
        ("P2", [str(包根 / ".venv" / "Scripts" / "python.exe"), "-B", str(打), str(包根),
                str(日志根 / "_dist2")], "打wheel 走 git", True),
    ):
        t0 = time.time()
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=str(包根), timeout=600)
            rc, o = r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            rc, o = -9, ""
        # ★ R570：**不写死数量**。期望现算，两条路各算各的（工作区 / git 各自为真值）——
        #   写死 `.py 109` 的后果：一加文件就红，而红说的是"数字变了"、不是"打包坏了"（口径 311）。
        _m = _re.search(r"\.py (\d+)", o or "")
        _实 = _py数(包根, 走git=要自查)
        好 = rc == 0 and _m is not None and int(_m.group(1)) == _实 and (
            "一个不少" in o or not 要自查)
        出.append((标, 名, "PASS" if 好 else "FAIL",
                   f"退出码 {rc} · " + (f"包 .py {_实}" + (" 且自查非空" if 要自查 else "")
                                        if 好 else (o.strip().splitlines()[-1][:90] if o.strip() else "无输出")),
                   time.time() - t0))
    # ★ P4：**发布物自相矛盾**的机器判据。
    #   verifier 靠肉眼发现「SHA256SUMS 记的是旧 wheel 的哈希，而 wheels/ 里的 whl
    #   mtime 更晚」——这种账**不该靠眼睛**：它只在"重打包后忘了更新校验文件"时出现，
    #   而那正是发版流程最容易漏的一步。这里反向逐个校验。
    t0 = time.time()
    _d = max((x for x in (包根 / "dist").glob("orchestrator-*") if x.is_dir()),
             key=lambda p: p.stat().st_mtime, default=None)
    if _d is None or not (_d / "SHA256SUMS").is_file():
        出.append(("P4", "SHA256SUMS 与文件一致", "未验", "没找到 dist/orchestrator-*/SHA256SUMS",
                   time.time() - t0))
    else:
        import hashlib as _h
        _坏 = []
        _n = 0
        for _l in (_d / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            if not _l.strip():
                continue
            _记, _名 = _l.split("  ", 1)
            _f = _d / _名
            if not _f.is_file():
                _坏.append(f"缺文件 {_名}")
                continue
            _n += 1
            if _h.sha256(_f.read_bytes()).hexdigest() != _记:
                _坏.append(f"哈希不符 {_名}")
        出.append(("P4", "SHA256SUMS 与文件一致", "PASS" if not _坏 else "FAIL",
                   f"{_d.name}：校验 {_n} 个" + ("全部一致" if not _坏 else "；" + " / ".join(_坏[:3])),
                   time.time() - t0))
    return 出


def _并发预检() -> str:
    """★ 检查有没有**别的会话**正在编译同一个仓 —— 有的话，耗时读数一概不可作数。

    ★ 为什么必须做（实测踩到）：2026-11-18 06:44 那趟，`--dry-machine` 平时 31 秒，
      这次十分钟还没完。查下去发现机器上同时有 4 个 `cargo test` 在跑同一个仓
      （`机械循环::趋向::测试` / `第2层` / `接线状态图` / `本源桥`，都**不是本趟起的**），
      它们和本趟抢同一批 `target/*/.cargo-lock`。
      ⇒ 那时候量到的不是「产品变慢」，是「尺子被别人掰弯了」。
      ★ 口径：一个漂亮的对照读数，先怀疑尺子。判据（退出码/读数）仍然有效，
        但**墙钟**必须打上污染标记，否则会拿它去改一个不存在的缺陷。
    """
    try:
        import subprocess as _sp
        _r = _sp.run(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_Process -Filter \"Name='cargo.exe' OR Name='rustc.exe'\" "
                      "| ForEach-Object { $_.ProcessId }"],
                     capture_output=True, text=True, timeout=60)
        _ids = [x.strip() for x in (_r.stdout or "").splitlines() if x.strip().isdigit()]
    except Exception:                        # noqa: BLE001
        return ""
    if not _ids:
        return ""
    return (f"⚠ 机器上有 {len(_ids)} 个别的 cargo/rustc 进程在跑（pid {', '.join(_ids[:6])}）"
            f"⇒ **本趟墙钟不可作数**（抢 target 锁）；退出码与读数仍有效")


# 读「最近一趟读数」的那三条 ⇒ 必须最后跑，**且按依赖方向排**：
#   #51 写 work/门区分力.json → #52 读它 → #53 读两者。
#   ★ 不能只写成集合：`[x for x in 表 if x.id in 集合]` 只挪位置、**保留表内顺序**
#     （表里是 53/52/51 ⇒ 会跑成 53→52→51，等于又读上一趟 ⇒ 同一个死锁换个方向）。
_元判据 = ("51", "52", "53")


def _写读数(结果: list, *, 完成: bool) -> None:
    """把**当前已跑到的**结果写进 `work/交付验收_最近.json`（增量落盘）。

    ★ 为什么必须增量（2026-11-20，真读数）：原来只在整趟结束时写 ⇒ 同一趟里
      #51/#52/#53 读到的是**上一趟**的读数 ⇒ 上一趟红则这一趟也红 ⇒
      **判据套件没有不动点**（一趟红，永远红）。
      实测：04:43 那趟 FAIL 2 之后，`仓内生产力` 直接报「退化 PASS 47→45」rc=1。

    ★ `完成=False` 的含义是「本趟还没跑完」，不是「跑完了但少了」——
      所以消费方（仓内生产力）**不许拿部分读数的条数去比大小**。
    """
    import json as _json
    import time as _time
    件 = 包根 / "work" / "交付验收_最近.json"
    件.parent.mkdir(parents=True, exist_ok=True)
    过 = sum(1 for r in 结果 if r[2] == "PASS")
    败 = sum(1 for r in 结果 if r[2] == "FAIL")
    未 = sum(1 for r in 结果 if r[2] == "未验")
    件.write_text(_json.dumps(dict(
        时间=_time.strftime("%Y-%m-%d %H:%M:%S"),
        完成=bool(完成),
        条数=len(结果), PASS=过, FAIL=败, 未验=未,
        总墙钟秒=round(sum(float(r[4] or 0) for r in 结果), 1),
        明细=[dict(id=r[0], 名=r[1], 态=r[2], 用时=r[4]) for r in 结果],
    ), ensure_ascii=False, indent=2), encoding="utf-8")


def _进程在(pid: int) -> bool:
    """这个 pid 现在还在不在。**探活不许靠外部程序。**

    ★ R551b：原来用 `tasklist /FI "PID eq N" /NH` + `stdout.count(str(pid))`：
      ① 外部程序 + 本地化输出（中文系统列名/分隔都不同）；
      ② `count(pid)` 会被**内存列**里的数字撞上 ⇒ 判活不可靠；
      ③ 更要命：撞不上时它**返回 False 而不是抛异常** ⇒ 那个
         `except: _活 = True`（保守当活）**根本不生效** ⇒ 静默接管。
      实测：我拿一把活 pid 的假锁去测，它没认出活锁、直接接管并把整趟
      `--快` 验收跑起来了（10 分钟后我才发现）。
      **判据的"安全兜底"挂在一条不会触发的分支上** —— 这是本项目的老病。
    ⇒ 用 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`：句柄非 0 ⇒ 在。
      探不了（非 Windows / 调用失败）⇒ **保守当活**（拒跑）：
      "误拒跑"的代价是等一会儿，"误接管"的代价是**读数不可信**。
    """
    if pid <= 0:
        return False
    try:
        import ctypes
        _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32                       # type: ignore[attr-defined]
        h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False                                   # 打不开 = 不存在
        try:
            return True
        finally:
            k32.CloseHandle(h)
    except Exception:                                      # noqa: BLE001
        return True                                        # 探不了 ⇒ 保守当活


def _拿锁() -> "object | None":
    """同一包根**同时只许一趟**验收。拿不到 ⇒ 返回 None（调用方拒跑）。

    ★★★★★ R551：为什么必须拒跑而不是"两趟并行"。
      真事故（2026-11-20 13:33）：我一边等全量验收，一边手动跑 `--只 42` 复现；
      两个进程同时写 `work/工具面契约.json` 与 `42.out`/`42.err`
      ⇒ 其中一趟读到另一趟的中间态 ⇒ **假红**（42 报退出码 1，单独跑 5 次全绿）。
      **假红比漏报更坏**（口径 311）：它教人忽略红灯。
      ⇒ 工具必须让「并发跑」**做不到**，而不是靠人记得。
    ★ 陈旧锁自动接管：写 pid + 启动时间；pid 已不在 ⇒ 视为上一趟崩了，接管。
      否则一次崩溃就把验收永久锁死。
    """
    import json as _j
    锁 = 日志根 / "_交付验收.lock"
    try:
        日志根.mkdir(parents=True, exist_ok=True)
    except OSError as _e:
        # ★ R551b：**不许静默放行**。建不出锁目录 = "我不知道有没有别人在跑"，
        #   不是"没有别人在跑"。放行可以（可跑性优先），但必须说出来。
        print(f"○ 锁目录建不出来（{_e}）⇒ 本趟**无法防止并发**，读数可能与另一趟互相覆盖")
        return object()
    if 锁.exists():
        try:
            旧 = _j.loads(锁.read_text(encoding="utf-8"))
            _pid = int(旧.get("pid") or 0)
        except Exception:                  # noqa: BLE001 坏锁 ⇒ 当陈旧
            _pid = 0
        _活 = _进程在(_pid)
        if _活:
            print(f"✗ 另一趟验收正在跑（pid {_pid}，锁 {锁}）")
            print("  ⇒ **拒绝并发跑**：两趟会互相覆盖 work/*.json 与 .out/.err，")
            print("     跑出来的读数不可信 —— 假红比漏报更坏（口径 311）。")
            print("     等它结束；若确认它已死，删掉那把锁再跑。")
            return None
        print(f"○ 接管陈旧锁（pid {_pid} 已不在）")
    try:
        锁.write_text(_j.dumps({"pid": os.getpid(),
                              "起": time.strftime("%Y-%m-%d %H:%M:%S")},
                             ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return 锁


def _放锁(锁) -> None:
    try:
        if 锁 is not None and hasattr(锁, "unlink"):
            锁.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> int:
    快 = "--快" in sys.argv
    # ★ `--只 11,38,45`：只跑指定用例。本轮三条红要逐条复验，
    #   而整趟 42 条约 50 分钟 —— 没有它就只能"整趟重来"，实际后果是红了不复查。
    _只: list[str] = []
    if "--只" in sys.argv:
        _i = sys.argv.index("--只")
        if _i + 1 < len(sys.argv):
            _只 = [x.strip() for x in sys.argv[_i + 1].split(",") if x.strip()]
    print(f"═══ 交付验收 · {time.strftime('%Y-%m-%d %H:%M:%S')} ═══")
    print(f"包根：{包根}")
    print(f"章程：{章程}（{'在' if 章程.is_file() else '**不在**'}）")
    _污 = _并发预检()
    if _污:
        print(_污)
    print()
    _锁 = _拿锁()
    if _锁 is None:
        return 2
    try:
        return _跑全套(快, _只)
    finally:
        _放锁(_锁)

def _跑全套(快: bool, _只: list[str]) -> int:
    """原来的 main 主体（拆出来是为了让锁能 finally 释放）。"""
    # ── 表与章程一致性（防止"章程写一套、脚本跑另一套"）
    章 = 章程.read_text(encoding="utf-8", errors="replace") if 章程.is_file() else ""
    缺id = [x["id"] for x in 底线表 if x["id"].rstrip("abcd") not in 章]
    if 缺id:
        print(f"⚠ 表与章程不一致：这些 id 在章程里找不到 → {缺id}")
        print("  ⇒ 先改章程（它是给人看的说明书），再来跑本脚本。")
        return 2

    全新根 = 日志根 / "_全新state"
    shutil.rmtree(全新根, ignore_errors=True)

    结果: list[tuple[str, str, str, str, float]] = []
    本趟: list[str] = []          # ★ #32：本趟真跑过的用例标（只扫这些的 .err）
    # ★ 元判据（读「最近一趟读数」的那三条）**排到最后**：这样它们读到的是
    #   **本趟**已经跑完的那些用例的读数，而不是上一趟的（自指死锁的根源）。
    _按id = {x["id"]: x for x in 底线表}
    _排 = ([x for x in 底线表 if x["id"] not in _元判据]
           + [_按id[i] for i in _元判据 if i in _按id])
    for 项 in _排:
        if _只 and 项["id"] not in _只:
            continue
        if 快 and 项["id"] in 慢的:
            print(f"  跳过 {项['id']:<4}{项['名']}（--快）")
            continue
        判定, 摘要, 秒, 标 = _跑(项, 全新根)
        if 标:
            本趟.append(标)
        结果.append((项["id"], 项["名"], 判定, 摘要, 秒))
        # ★ 增量落盘：后面读「最近一趟」的判据必须看得到**本趟**的读数
        if not _只:
            try:
                _写读数(结果, 完成=False)
            except Exception as _ew:              # noqa: BLE001
                print(f"   ⚠ 增量写读数失败（不影响判定）：{_ew}")
        print(f"  {'✓' if 判定 == 'PASS' else ('✗' if 判定 == 'FAIL' else '○')} {项['id']:<4}{项['名']:<34} {判定:<4} {秒:7.1f}s  {摘要}")

    if not 快 and not _只:
        print()
        for 标, 名, 判定, 摘要, 秒 in _打包路径():
            结果.append((标, 名, 判定, 摘要, 秒))
            print(f"  {'✓' if 判定 == 'PASS' else '✗'} {标:<4}{名:<34} {判定:<4} {秒:7.1f}s  {摘要}")

    # ── #32 stderr 干净门（横切）：本趟跑过的每个用例，stderr 都不许有未消费协程这类运行期警告
    #   ★ 为什么是横切而不是用例：这类缺陷**不改变退出码**，只能靠"有人去看 stderr"抓；
    #     而人不会天天看 stderr ⇒ 必须机器看。R551 就是这么漏了很久的。
    禁词 = ("RuntimeWarning", "never awaited")
    脏: list[str] = []
    for 标 in 本趟:
        f = 日志根 / f"{标}.err"
        try:
            文 = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for 词 in 禁词:
            if 词 in 文:
                脏.append(f"{标}·{词}")
    结果.append(("32", "stderr 干净（无 RuntimeWarning / never awaited）",
                "FAIL" if 脏 else "PASS",
                ("出现：" + "、".join(脏[:6])) if 脏 else f"扫了本趟 {len(本趟)} 份 .err",
                0.0))
    print(f"  {'✗' if 脏 else '✓'} {'32':<4}{'stderr 干净（横切全趟）':<34} "
          f"{'FAIL' if 脏 else 'PASS':<4} {'':>7}  "
          f"{('出现：' + '、'.join(脏[:6])) if 脏 else f'扫了本趟 {len(本趟)} 份 .err'}")

    print()
    过 = sum(1 for r in 结果 if r[2] == "PASS")
    败 = sum(1 for r in 结果 if r[2] == "FAIL")
    未 = sum(1 for r in 结果 if r[2] == "未验")
    print(f"══ 结果：PASS {过} · FAIL {败} · 未验 {未}（共 {len(结果)} 条；另有 P3 干净装由 verifier 独立复现）")

    # ── 机器可读的「最近一趟」读数（供 #52 仓内生产力指标读；**只写全量跑**）──
    #   为什么不写 --只 的跑：那会把"2 条用例"当成一次交付读数，指标立刻失真（口径 375）。
    try:
        if not _只:
            _写读数(结果, 完成=True)
            print(f"   · 读数已存 work/交付验收_最近.json（{len(结果)} 条 · "
                  f"墙钟 {sum(float(r[4] or 0) for r in 结果):.0f}s · 完成=True，供 #52 读）")
    except Exception as _e:                    # noqa: BLE001
        print(f"   ⚠ 写最近读数失败（不影响判定）：{_e}")
    if 败:
        print("   ⇒ **未达成交付条件**（见下面 FAIL 行）")
        for r in 结果:
            if r[2] == "FAIL":
                print(f"      ✗ {r[0]} {r[1]}：{r[3]}")
        return 1
    if 未:
        print("   ⇒ 无 FAIL，但有未验项；**未验不算达成**")
        return 2
    print("   ⇒ 全部**机判**底线达成。"
          "P3（干净环境独立复现）**不由本脚本判** —— 它要一个独立方在真干净 venv 里重装；"
          "本脚本跑在自己的 venv 里，自己验自己不算验。复验记录见《交付章程》第 4 节。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())