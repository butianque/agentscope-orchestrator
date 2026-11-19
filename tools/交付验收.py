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
    # ★ #49 离线装可用（2026-11-19 加入）：install.ps1 承诺"从 wheels/ 离线装（不联网也能装）"，
    #   但 wheels/ 里只有编排器自己的轮子 ⇒ 干净机器上离线装装出 doctor=64（缺 pyyaml/httpx/mcp）。
    #   判据：#46/#48 都只测"已装/部署目录"，没人测"按发货承诺装一遍能不能用" —— 这就是那一栏。
    dict(id="49", 名="离线装可用（全新 venv + --no-index 装 + 握手）",
         argv=["python", "tools/离线装可用.py"], expect="zero",
         must=["判定=OK", "离线装 rc=0", "工具"], max_s=900),
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
                                 creationflags=0x00000008 | 0x00000200)
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
        return "FAIL", f"退出码 {rc}（期望 {'0' if 项['expect'] == 'zero' else '非 0'}）", dt, 标
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
    for 项 in 底线表:
        if _只 and 项["id"] not in _只:
            continue
        if 快 and 项["id"] in 慢的:
            print(f"  跳过 {项['id']:<4}{项['名']}（--快）")
            continue
        判定, 摘要, 秒, 标 = _跑(项, 全新根)
        if 标:
            本趟.append(标)
        结果.append((项["id"], 项["名"], 判定, 摘要, 秒))
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