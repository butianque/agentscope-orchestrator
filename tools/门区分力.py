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


def main() -> int:
    if not (公仓 / ".git").exists():
        print(f"○ 未验：公共仓不在 `{公仓}` —— 环境没就绪，不是产品缺陷（口径 375）")
        return 3

    结果: list[dict] = []
    门基线: dict[str, bool] = {}
    原件: dict[pathlib.Path, bytes] = {}
    原哈希: dict[pathlib.Path, str] = {}
    for p in (_工作区, _任一源):
        原件[p] = p.read_bytes()
        原哈希[p] = hashlib.sha256(原件[p]).hexdigest()

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
                elif r == "存活":
                    存 += 1
                else:
                    无 += 1
                continue

            靶: pathlib.Path = t["靶"]
            文 = 原件[靶].decode("utf-8")
            动作 = t["动作"]
            if 动作 == "替换":
                if t["旧"] not in 文:
                    无 += 1
                    print(f"      ○ {i}. {t['名']}：**无从注入**（原文没这段）")
                    结果.append(dict(序=i, 名=t["名"], 门=t["门名"], 态="无从注入"))
                    continue
                新文 = 文.replace(t["旧"], t["新"], 1)
            else:
                新文 = 文 + t["追加"]
            try:
                靶.write_bytes(新文.encode("utf-8"))
                绿, 尾 = _跑门(t["门"], env={"ORCH_PUB": str(公仓)} if t["门名"] == "公仓可编译" else None)
            finally:
                靶.write_bytes(原件[靶])          # ★ 无论成败立刻还原
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
                p.write_bytes(b)
                print(f"      ⚠ 强制还原 {p.name}")

    # ③ 收口：分母只算注得进的，未验与无从注入如实单列
    计入 = 抓 + 存
    率 = (抓 / 计入) if 计入 else 0.0
    print(f"   ③ 区分力 = 抓住 {抓}/{计入}（注入成功 {计入} · 无从注入 {无} · 未验 {len(靶们) - 计入 - 无}）"
          f"＝ {率:.0%} · 门具 {len(门基线)} 个")
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