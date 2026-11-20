# -*- coding: utf-8 -*-
"""门区分力（判据 #51）—— **跨门具、跨产物**地量「门到底有多能挡」。

## 为什么要有它（N4 的正当替代）
「改得对不对」= 程序等价，**不可判定**（Rice 定理）。所以「语义正确性」永远做不出来。
但可以被**度量**：往产物里注机械性错误，看**判据门抓不抓得住** —— 抓住/注入 = 区分力。

此前只量过 **3 条变异、且只对 1 个门具**（`三替身绿率.py` 的 ③，靶全在 `workspace.py`、
门具只有 `拆树判决探针.py`）⇒ 那只证明"一条门有齿"，不证明"门族有齿"。
2026-11-19 扩展到 **6 条变异 · 4 个门具 · 3 类产物**（源码 / 打包件 / 公共仓提交）。

## 靶（每条 = 一个**真**机械错误）
| # | 产物 | 注入什么 | 该抓住它的门 |
|---|---|---|---|
| 1 | `workspace.py` | 删不掉也判成功（判决恒 0）| 拆树判决探针 |
| 2 | `workspace.py` | `拆()` 恒报删成功（不看盘上还在不在）| 拆树判决探针 |
| 3 | `workspace.py` | `main --clean` **去掉防误删护栏**（任意路径都可删）| 拆树判决探针（④ 入口级）|
| 4 | 任一源码 `.py` | 追加一行注释（sha 变 ⇒ 已装 != 源码）| 已装一致性 |
| 5 | 任一源码 `.py` | 同上（sha 变 ⇒ 部署包 != 源码）| 部署一致性 |
| 6 | **公共仓提交** | 提交一个语法坏文件（不推送）| 公仓可编译 |

## 三态，不许混
· **抓住**：门变红（rc≠0 或 无 `判定=OK`）—— 门挡得住；
· **存活**：门仍绿 —— **门有洞**，如实列出来；
· **无从注入**：产物里没这段原文 —— 分母不算它，但**报条数**（口径 375：空集不构成判据）。

## 诚实边界
· 变异是**机械的**（判决恒 0 / 恒 True / 去护栏 / 改一个字节）。区分力高只说明"挡得住这类机械错误"，
  **不**说明"改得对" —— 后者不可判定，这里不冒充。
· 只支持 Python 源码靶 + 一个 git 提交靶；非 Python 语言的门族要另配靶。
· 全部在**就地改、finally 还原**模式下跑，还原后**逐字节 sha256 复核**；公共仓靶用本地 clone 的
  `git commit` + `git reset --hard`，**不推远端**。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

包根 = pathlib.Path(__file__).resolve().parents[1]
公仓 = pathlib.Path(r"E:\基础设施\orch-share\agentscope-orchestrator")
Q = '"'
输出 = 包根 / "work" / "门区分力.json"

_工作区 = 包根 / "src" / "agentscope_orchestrator" / "workspace.py"
_任一源 = 包根 / "src" / "agentscope_orchestrator" / "_winutf8.py"


def _当前版本() -> str:
    """从 `pyproject.toml` **现读**版本（单一源）。

    ★ 2026-11-20：原来变异表把 `0.13.20` 写死在靶路径与替换串里，版本一升
      ⇒ 三条变异打的是**过时产物** ⇒ 门不红 ⇒ 被记成「洞」。
      读数：`抓住 12/14 · 判定=门有洞`，而**门是好的**（假红，比漏报更坏）。
    """
    import tomllib
    try:
        d = tomllib.loads((包根 / "pyproject.toml").read_text(encoding="utf-8"))
        return str(d["project"]["version"])
    except Exception:
        return "0.0.0"


def _下一版本(v: str) -> str:
    """末位 +1（用来造「版本单一源被改坏」的变异）。取不到就退一个固定串。"""
    try:
        a, b, c = (int(x) for x in v.split(".")[:3])
        return f"{a}.{b}.{c + 1}"
    except Exception:
        return "9.9.9"


_版 = _当前版本()
_新版 = _下一版本(_版)
_发货包 = 包根 / "dist" / f"orchestrator-{_版}"

# ── 靶表 ───────────────────────────────────────────────────────────
# 每条：名 / 靶文件（None = 公共仓）/ 动作 / 旧 / 新 / 门具 argv
靶们 = [
    dict(名="删不掉也判成功（判决恒 0）",
         靶=_工作区, 动作="替换",
         旧="    return 3, (f" + Q + "✗ 删不掉",
         新="    return 0, (f" + Q + "✗ 删不掉",
         门=[sys.executable, "-B", str(包根 / "tools" / "拆树判决探针.py")],
         门名="拆树判决探针"),
    dict(名="拆() 恒报删成功（不看盘上还在不在）",
         靶=_工作区, 动作="替换",
         旧="        出[" + Q + "删目录" + Q + "] = not 树.exists()",
         新="        出[" + Q + "删目录" + Q + "] = True",
         门=[sys.executable, "-B", str(包根 / "tools" / "拆树判决探针.py")],
         门名="拆树判决探针"),
    dict(名="main --clean 去掉防误删护栏（任意路径都可删）",
         靶=_工作区, 动作="替换",
         旧="            if not (_名像 or _标记 or _记过):",
         新="            if False:",
         门=[sys.executable, "-B", str(包根 / "tools" / "拆树判决探针.py")],
         门名="拆树判决探针"),
    dict(名="源码 sha 变了（注释一行）⇒ 已装 != 源码",
         靶=_任一源, 动作="追加", 追加="\n# 门区分力量具注入（还原后应为 0 处）\n",
         旧=None, 新=None,
         门=[sys.executable, "-B", str(包根 / "tools" / "已装一致性.py")],
         门名="已装一致性"),
    dict(名="源码 sha 变了 ⇒ 部署包 != 源码",
         靶=_任一源, 动作="追加", 追加="\n# 门区分力量具注入 2（还原后应为 0 处）\n",
         旧=None, 新=None,
         门=[sys.executable, "-B", str(包根 / "tools" / "部署一致性.py")],
         门名="部署一致性"),
    dict(名="公共仓提交里有一个语法坏文件",
         靶=None, 动作="git破坏",
         旧=None, 新=None,
         门=[sys.executable, "-B", str(包根 / "tools" / "公仓可编译.py")],
         门名="公仓可编译"),
    # ── 2026-11-19 第二批：从「4 门具」扩到「8 门具」，覆盖 #33/#46/#48/#49 ──
    dict(名="src 多出一个 .py（装入目录没有）⇒ 已装出现「缺」",
         靶=None, 动作="新增文件",
         路径=包根 / "src" / "agentscope_orchestrator" / "_门区新增.py",
         内容="X = 1\n",
         旧=None, 新=None,
         门=[sys.executable, "-B", str(包根 / "tools" / "已装一致性.py")],
         门名="已装一致性"),
    dict(名="发货包里缺主轮子 ⇒ 离线装装不上",
         靶=None, 动作="移走文件",
         路径=_发货包 / "wheels" / f"agentscope_orchestrator-{_版}-py3-none-any.whl",
         旧=None, 新=None,
         门=[sys.executable, "-B", str(包根 / "tools" / "离线装可用.py")],
         门名="离线装可用"),
    dict(名="代码行里出现项目名词（太虚元道_泄漏 = 1）⇒ 阶段分离棘轮升了",
         靶=_任一源, 动作="追加", 追加="\n太虚元道_泄漏 = 1\n",
         旧=None, 新=None,
         门=[sys.executable, "-B", str(包根 / "tools" / "阶段分离棘轮.py")],
         门名="阶段分离棘轮"),
    dict(名="dist VERSION.txt 被改 ⇒ 部署三方不一致",
         靶=_发货包 / "VERSION.txt", 动作="替换",
         旧=_版, 新=_新版,
         门=[sys.executable, "-B", str(包根 / "tools" / "部署一致性.py")],
         门名="部署一致性"),
    # ── 2026-11-19 第三批：靶扩到「仓库整体 / 契约数据 / 发货脚本」──
    #   为什么加这三类：前两批只打"代码/打包件/公仓提交"，而**台账与契约数据**坏了没人看；
    #   发货脚本 install.ps1 的承诺（--no-index）此前也只有"真装一次"兜着，没有静态断言。
    dict(名="src 里一个 .py 语法坏 ⇒ 全仓 ast 对账必须红",
         靶=None, 动作="新增文件",
         路径=包根 / "src" / "agentscope_orchestrator" / "_门区坏语法.py",
         内容="def 坏(:\n",
         旧=None, 新=None,
         门=[sys.executable, "-B", str(包根 / "tools" / "全仓ast对账.py")],
         门名="全仓ast对账"),
    dict(名="契约 JSON 少一个工具名 ⇒ 工具面契约覆盖不符",
         靶=包根 / "work" / "工具面契约.json", 动作="替换",
         旧='"name": "audit_delivery"',
         新='"name": "audit_delivery_被改名"',
         门=[sys.executable, "-B", str(包根 / "tools" / "工具面契约.py")],
         门名="工具面契约"),
    dict(名="门区分力.json 把「抓住」改小 ⇒ 仓内指标必须发现自相矛盾",
         靶=包根 / "work" / "门区分力.json", 动作="正则替换",
         正则=r'"抓住": \d+', 新='"抓住": 1',  # ★ 数值会变，正则锚才不脆
         门=[sys.executable, "-B", str(包根 / "tools" / "仓内生产力.py")],
         门名="仓内生产力"),
    # ── 2026-11-19 第四批：靶扩到「读数台账 / 另一个发货脚本 / 版本单一源」──
    dict(名="交付验收_最近.json 的条数被改（999）⇒ 仓内指标必须发现读数自相矛盾",
         靶=包根 / "work" / "交付验收_最近.json", 动作="正则替换",
         正则=r'"条数": \d+', 新='"条数": 999',
         门=[sys.executable, "-B", str(包根 / "tools" / "仓内生产力.py")],
         门名="仓内生产力"),
    dict(名="install.sh（Linux 发货脚本）去掉 --no-index ⇒ 离线装承诺被改成联网装",
         靶=包根 / "packaging" / "install.sh", 动作="替换",
         旧="--no-index --find-links", 新="--find-links",
         门=[sys.executable, "-B", str(包根 / "tools" / "离线装可用.py")],
         门名="离线装可用"),
    dict(名=f"pyproject 版本被改（{_版} → {_新版}）⇒ 版本单一源破了，部署三方不一致",
         靶=包根 / "pyproject.toml", 动作="替换",
         旧=f'version = "{_版}"', 新=f'version = "{_新版}"',
         门=[sys.executable, "-B", str(包根 / "tools" / "部署一致性.py")],
         门名="部署一致性"),
    dict(名="仓内生产力.json 的人工交互点被改成 3 ⇒ 交付状态一页纸必须发现",
         靶=包根 / "work" / "仓内生产力.json", 动作="正则替换",
         正则=r'"人工交互点": \d+', 新='"人工交互点": 3',
         门=[sys.executable, "-B", str(包根 / "tools" / "交付状态.py")],
         门名="交付状态"),
    dict(名="install.ps1 去掉 --no-index ⇒ 离线装承诺被改成联网装",
         靶=包根 / "packaging" / "install.ps1", 动作="替换",
         旧="--disable-pip-version-check --no-index --find-links",
         新="--disable-pip-version-check --find-links",
         门=[sys.executable, "-B", str(包根 / "tools" / "离线装可用.py")],
         门名="离线装可用"),
]


def _跑门(argv: list[str], *, env: dict | None = None, timeout: int = 1200) -> tuple[bool, str]:
    """返回 (是否绿, 输出尾)。绿 = rc==0 且输出里有 `判定=OK`。"""
    import os
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e.pop("PYTHONPYCACHEPREFIX", None)
    if env:
        e.update(env)
    cp = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=timeout, env=e)
    出 = ((cp.stdout or "") + (cp.stderr or "")).strip()
    绿 = cp.returncode == 0 and "判定=OK" in 出
    return 绿, 出[-400:]


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _写回(p: pathlib.Path, 字节: bytes, *, 次数: int = 10, 间隔: float = 0.4) -> None:
    """写 + 逐字节校验 + **瞬时失败重试**；彻底失败就抛（调用方必须硬停，不许留残）。

    ★ 为什么必须有（2026-11-19 真事故）：本工具跑第 11~14 条变异时，
      `workspace.py` 的**还原写盘**抛了 `OSError [Errno 22] Invalid argument`（瞬时，
      随后同一文件连写 3 次全 OK）—— 而当时异常让流程提前跳出，
      **注入版 `return 0` 就留在了源码里**（下一轮门全靠人工 git checkout 救回）。
      ⇒ 「还原」不能是裸 `write_bytes`：要重试 + 校验 + 失败就硬停。
    """
    import time
    末: Exception | None = None
    for i in range(次数):
        try:
            p.write_bytes(字节)
            if p.read_bytes() == 字节:
                return
        except OSError as e:                    # noqa: PERF203
            末 = e
        time.sleep(间隔)
    raise RuntimeError(f"{p.name} 写不回（{末}）—— 产物可能仍是变异版，必须人工介入")


def main() -> int:
    if not (公仓 / ".git").exists():
        print(f"○ 未验：公共仓不在 `{公仓}` —— 环境没就绪，不是产品缺陷（口径 375）")
        return 3

    结果: list[dict] = []
    门基线: dict[str, bool] = {}
    原件: dict[pathlib.Path, bytes] = {}
    原哈希: dict[pathlib.Path, str] = {}
    # 所有「替换/追加」类靶：先把原位字节记下来（靶不一定落在 _工作区/_任一源 里，
    # 也可能是 work/ 的契约数据或 packaging/ 的发货脚本）
    for t in 靶们:
        p = t.get("靶")
        if t["动作"] in ("替换", "追加", "正则替换") and p is not None:
            原件[p] = p.read_bytes()
            原哈希[p] = hashlib.sha256(原件[p]).hexdigest()
    # 新增/移走类靶的"原位字节"（存在才记）
    for t in 靶们:
        if t["动作"] in ("新增文件", "移走文件"):
            p: pathlib.Path = t["路径"]
            原件[p] = p.read_bytes() if p.exists() else b""
            原哈希[p] = hashlib.sha256(原件[p]).hexdigest()

    # ①′ 写盘预检：这些靶待会儿要「写坏 → 写回」，先证明**此刻可写**；
    #    否则量到一半写不回去会留下变异版（2026-11-19 真事故就出在这）。
    for _p, _b in 原件.items():
        if _p.exists():
            try:
                _写回(_p, _b)
            except RuntimeError as _e:
                print(f"○ 未验：{_e}")
                return 3

    try:
        # ① 基线：每条门在正确产物上必须先绿，否则量的是"门本来就不行"
        print("   ① 基线（未变异时门必须绿）")
        for t in 靶们:
            名 = t["门名"]
            if 名 in 门基线:
                continue
            绿, 尾 = _跑门(t["门"], env={"ORCH_PUB": str(公仓)} if 名 == "公仓可编译" else None)
            门基线[名] = 绿
            print(f"      {'✓' if 绿 else '✗'} {名}：{'绿（可作对照）' if 绿 else '红 ⇒ 该门记未验，'
                  '先修门再量区分力'}")
        if not any(门基线.values()):
            print("   判定=未验（没有一条门是绿的，无从量区分力）")
            return 3

        # ② 逐条注入 → 跑门 → 立刻还原 → 记三态
        print("   ② 注入变异，看门抓不抓")
        抓 = 存 = 无 = 0
        for i, t in enumerate(靶们, 1):
            if not 门基线.get(t["门名"], False):
                print(f"      ○ {i}. {t['名']}：门「{t['门名']}」基线红 ⇒ 未验（不计入分母）")
                结果.append(dict(序=i, 名=t["名"], 门=t["门名"], 态="未验"))
                continue
            if t["动作"] == "git破坏":
                r = _公仓注入(t["门"])
                结果.append(dict(序=i, 名=t["名"], 门=t["门名"], 态=r))
                if r == "抓住":
                    抓 += 1
                    print(f"      ✓ {i}. {t['名']}：抓住（门「{t['门名']}」变红）")
                elif r == "存活":
                    存 += 1
                    print(f"      ✗ {i}. {t['名']}：**存活**（门「{t['门名']}」还绿 ⇒ 门有洞）")
                else:
                    无 += 1
                    print(f"      ○ {i}. {t['名']}：**无从注入**（{r}）")
                continue

            靶: pathlib.Path = t["靶"]
            动作 = t["动作"]
            if 动作 in ("新增文件", "移走文件"):
                路径: pathlib.Path = t["路径"]
                原 = 原件[路径]
                if 动作 == "新增文件" and 原:
                    无 += 1
                    print(f"      ○ {i}. {t['名']}：**无从注入**（文件已存在）")
                    结果.append(dict(序=i, 名=t["名"], 门=t["门名"], 态="无从注入"))
                    continue
                if 动作 == "移走文件" and not 原:
                    无 += 1
                    print(f"      ○ {i}. {t['名']}：**无从注入**（文件本来就没有）")
                    结果.append(dict(序=i, 名=t["名"], 门=t["门名"], 态="无从注入"))
                    continue
                try:
                    if 动作 == "新增文件":
                        路径.write_bytes(t["内容"].encode("utf-8"))
                    else:
                        路径.unlink()
                    绿, 尾 = _跑门(t["门"], env={"ORCH_PUB": str(公仓)} if t["门名"] == "公仓可编译" else None)
                finally:
                    if 动作 == "新增文件":
                        if 路径.exists():
                            路径.unlink()
                    else:
                        _写回(路径, 原)               # ★ 钝化：重试 + 校验
                复核 = hashlib.sha256(路径.read_bytes() if 路径.exists() else b"").hexdigest()
                if 复核 != 原哈希[路径]:
                    print(f"      ✗✗ 产物没能还原！{路径.name} 必须人工介入")
                    return 4
            else:
                文 = 原件[靶].decode("utf-8")
                if 动作 == "正则替换":
                    import re as _re
                    新文 = _re.sub(t["正则"], t["新"], 文, count=1)
                    if 新文 == 文:
                        无 += 1
                        print(f"      ○ {i}. {t['名']}：**无从注入**（正则没匹配到）")
                        结果.append(dict(序=i, 名=t["名"], 门=t["门名"], 态="无从注入"))
                        continue
                elif 动作 == "替换":
                    if t["旧"] not in 文:
                        无 += 1
                        print(f"      ○ {i}. {t['名']}：**无从注入**（原文没这段）")
                        结果.append(dict(序=i, 名=t["名"], 门=t["门名"], 态="无从注入"))
                        continue
                    新文 = 文.replace(t["旧"], t["新"], 1)
                else:
                    新文 = 文 + t["追加"]
                try:
                    _写回(靶, 新文.encode("utf-8"))
                    绿, 尾 = _跑门(t["门"], env={"ORCH_PUB": str(公仓)} if t["门名"] == "公仓可编译" else None)
                finally:
                    _写回(靶, 原件[靶])               # ★ 钝化：重试 + 校验
                复核 = hashlib.sha256(靶.read_bytes()).hexdigest()
                if 复核 != 原哈希[靶]:
                    print(f"      ✗✗ 产物没能还原！{靶.name} 必须人工介入")
                    return 4
            if 绿:
                存 += 1
                态 = "存活"
                print(f"      ✗ {i}. {t['名']}：**存活**（产物坏了 门「{t['门名']}」还绿 ⇒ 门有洞）")
            else:
                抓 += 1
                态 = "抓住"
                print(f"      ✓ {i}. {t['名']}：抓住（门「{t['门名']}」变红）")
            结果.append(dict(序=i, 名=t["名"], 门=t["门名"], 态=态))
    finally:
        for p, b in 原件.items():
            if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() != 原哈希[p]:
                _写回(p, b)
                print(f"      ⚠ 强制还原 {p.name}")

    # ③ 收口：分母只算注得进的，未验与无从注入如实单列
    计入 = 抓 + 存
    率 = (抓 / 计入) if 计入 else 0.0
    未验数 = len(靶们) - 计入 - 无
    print(f"   ③ 区分力 = 抓住 {抓}/{计入}（注入成功 {计入} · 无从注入 {无} · 未验 {未验数}）"
          f"＝ {率:.0%} · 门具 {len(门基线)} 个")

    # ★★★★★ R555：**未验必须带归因**，而且两种归因的下一步动作完全相反。
    #   真读数（2026-11-20 16:07）：#46/#48 真红 ⇒ 仓内生产力/交付状态 基线红
    #   ⇒ 3 条变异未验 ⇒ 旧判据串写死 `抓住 18/18` ⇒ #51 额外记一条红
    #   ⇒ **1 个真红看起来像 5 个红** ⇒ 正是 R550 学到的「红太多 ⇒ 没人再看」。
    #   · 上游真红 ⇒ 门基线红是**后果**，去修那个上游红（#51 不用动）；
    #   · 仪器自指 ⇒ 上游**全绿**却基线红，那才是要修 #51/门具（R550 类）。
    #   旧实现把两者混成一句「基线红 ⇒ 未验」⇒ 读的人只能自己去查上游 ——
    #   而这正是「判据不能要求人事后复现才拿到证据」（R551c）。
    _归因 = "无"
    _自指门: list[str] = []
    if 未验数:
        _上游败 = 0
        try:
            _读 = json.loads((包根 / "work" / "交付验收_最近.json").read_text(encoding="utf-8"))
            _元 = {"51", "52", "53"}
            for _x in (_读.get("明细") or _读.get("结果") or []):
                _i = str(_x.get("id") or _x.get("序") or "")
                if _i in _元:
                    continue
                if str(_x.get("判") or _x.get("态") or "").upper().startswith("FAIL"):
                    _上游败 += 1
        except Exception:                                       # noqa: BLE001
            _上游败 = -1                                     # 读不到 ⇒ 不猜
        _红门 = sorted({t["门名"] for t in 靶们 if not 门基线.get(t["门名"], False)})
        if _上游败 > 0:
            _归因 = "上游真红"
            print(f"   · 未验归因=上游真红（上一趟非元判据 FAIL {_上游败} 条 ⇒ "
                  f"门基线红是**后果**：{'、'.join(_红门)}；去修上游红，#51 不用动）")
        elif _上游败 == 0:
            _归因 = "仪器自指"
            _自指门 = _红门
            print(f"   · 未验归因=**仪器自指**（上一趟非元判据**全绿**，而门基线却红："
                  f"{'、'.join(_红门)}）⇒ 这不是级联，是**仪器自己的毛病**（R550 类），必须修")
        else:
            _归因 = "读数读不到"
            print("   · 未验归因=读数读不到（`work/交付验收_最近.json` 不可用）"
                  "⇒ **不猜**是级联还是自指，请先跑一趟全量验收")
    else:
        print("   · 未验归因=无（每条变异都注得进）")
    if _归因 == "仪器自指":
        print("   判定=仪器自指（上游全绿却门基线红 ⇒ 先修门具，别把它当级联）")
        return 5

    存活的 = [x for x in 结果 if x["态"] == "存活"]
    for x in 存活的:
        print(f"      ✗ 洞：{x['名']}（门「{x['门']}」没抓住）")
    输出.parent.mkdir(parents=True, exist_ok=True)
    输出.write_text(json.dumps(dict(区分力=率, 抓住=抓, 注入成功=计入, 无从注入=无,
                                    门具=sorted(门基线), 明细=结果),
                               ensure_ascii=False, indent=2), encoding="utf-8")
    if 计入 == 0:
        print("   判定=未验（一条都没注入成功 ⇒ 空集不构成判据）")
        return 3
    print("   判定=OK" if 存 == 0 else "   判定=门有洞")
    return 0 if 存 == 0 else 1


def _公仓注入(门: list[str]) -> str:
    """在**工作区内的临时 clone** 里提交一个语法坏文件（不推送），跑门，然后丢弃 clone。

    ★ 为什么不在真公仓上改：本机沙箱只允许 Python 子进程写**工作区**，
      公仓在 `E:\\基础设施\\orch-share` 之外 ⇒ `PermissionError`（真读过）。
      用临时 clone 既避开沙箱，又不碰真公仓（连 HEAD 都不用动）。
    """
    import shutil
    import tempfile
    临时 = pathlib.Path(tempfile.mkdtemp(prefix="_门区分力-", dir=str(包根 / "work")))
    克隆 = 临时 / "pub"
    try:
        r = subprocess.run(["git", "-c", "http.version=HTTP/1.1", "clone", "-q", str(公仓), str(克隆)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"临时 clone 失败：{r.stderr[-200:]!r}"
        坏 = 克隆 / "src" / "agentscope_orchestrator" / "_门区分力注入.py"
        坏.write_text("def 坏(:\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(克隆), "-c", "core.hooksPath=", "add", "-A"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(克隆), "-c", "core.hooksPath=", "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "--no-verify", "-qm", "门区分力注入"],
                       capture_output=True)
        绿, _ = _跑门(门, env={"ORCH_PUB": str(克隆)})
        return "存活" if 绿 else "抓住"
    finally:
        shutil.rmtree(临时, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())