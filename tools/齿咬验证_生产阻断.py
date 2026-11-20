# -*- coding: utf-8 -*-
"""**齿咬验证**：把三条生产阻断修复各自**改回去**，看 `--selftest` 会不会叫。

口径（本项目已立）：**夹具必须能让两个实现给出相反答案。**
一条齿若在「缺陷版本」上也绿，那它不是齿，是装饰。

## 真读数（这就是它存在的理由）

第一版三条接线齿写的是**字符串自比**：
    `if "_落地保留文件(self, work)" not in _源539: fails.append(...)`
而这一行**本身**就在 `_源539` 里 ⇒ `not in` 恒假 ⇒ 齿永不叫。
本工具跑出来的读数：**3/3 全部存活** —— 光看代码是看不出来的。
改成 AST 判据（跳过 `_selftest` 子树）后：3/3 咬住。

⇒ 结论：**变异注入不是可选的质检，它是唯一能证明「齿是齿」的手段。**

## 两族变异

· 接线族：删掉调用 ⇒ 由**接线齿**（AST）抓；
· 行为族：把函数体改坏 ⇒ 由**行为齿**（真驱动）抓。
两族都要咬住，否则「接线对但逻辑错」或「逻辑对但没接线」都会漏。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
R = pathlib.Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator")
PY = R / ".venv" / "Scripts" / "python.exe"
根 = R / "work" / "_齿咬"
源 = R / "src" / "agentscope_orchestrator" / "generic_goal.py"

变异 = [
    # ── 接线族：删掉调用 ────────────────────────────────────────────
    ("R539", "接线", "删掉交付前的落地调用",
     "_落地539 = _落地保留文件(self, work)", "_落地539 = []  # 变异：不落地"),
    ("R539b", "接线", "删掉小文件退回整文件重写的判断",
     'if _该走哪种编辑(d["path"], 原文, True) == "whole":', "if False:  # 变异"),
    ("R539c", "接线", "删掉空读数强制为假",
     "_达标本 = _空读数不许达标(results, _达标本)", "_达标本 = _达标本  # 变异"),
    # ── 行为族：把函数体改坏 ────────────────────────────────────────
    ("R539", "行为", "落地时不写主树（只备份不写）",
     '            (REPO_ROOT / rel).write_bytes(src.read_bytes())   # ★ R22：字节级',
     '            pass  # 变异：备份了但不写'),
    ("R539b", "行为", "小文件判定恒为 patch（= 回到锁死）",
     "    if len(原文) <= _cap:\n        return \"whole\"",
     "    if False:\n        return \"whole\"  # 变异"),
    ("R539c", "行为", "空读数判定恒返回 True（= 回到假达标）",
     "    if not results:\n        return False\n    return bool(达标本)",
     "    return True  # 变异：空读数也算达标"),
    # ── R540 / R540b：2026-11-20 **假成功**修复（同一句命令两趟两种行为）──
    ("R540", "接线", "调用 _detect_repo_scope 时不传档案门",
     "_detect_repo_scope(spec, all_gates, _档案门540)",
     "_detect_repo_scope(spec, all_gates)"),
    ("R540", "行为", "档案门不再参与主树判定（= 回到只看模型自撰门）",
     "    if _档案声明了主树门(档案门):\n        return True\n",
     "    if False:\n        return True  # 变异\n"),
    ("R540b", "行为", "非主树路径红门也 emit delivered（= 回到假交付）",
     '        if not ok:\n            self._emit({"kind": "not_delivered"',
     '        if False:\n            self._emit({"kind": "not_delivered"'),
]


def 跑自检(包根: pathlib.Path) -> str:
    e = dict(os.environ)
    e.update({"PYTHONPATH": str(包根 / "src"), "PYTHONIOENCODING": "utf-8",
              "TEMP": str(包根 / "tmp"), "TMP": str(包根 / "tmp"),
              "TMPDIR": str(包根 / "tmp")})
    (包根 / "tmp").mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(PY), "-B", "-m", "agentscope_orchestrator.generic_goal",
                        "--selftest"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=3600,
                       env=e, cwd=str(包根))
    return (r.stdout or "") + (r.stderr or "")


def _跑基线() -> tuple[bool, str]:
    """干净源码跑一遍自检。返回 (是否全绿, 摘要原文)。

    ★ R545b：**基线绿是「变异→红」这个推理的前提**。R540 的齿自己崩了（恒红）⇒
      每条变异都"咬住"、毫无信息量，而我第一眼看还当成了成功。
      前提没验就下结论，等于没做实验。
    """
    包 = 根 / "_基线" / "包"
    shutil.rmtree(包.parent, ignore_errors=True)
    (包 / "tmp").mkdir(parents=True, exist_ok=True)
    shutil.copytree(R / "src" / "agentscope_orchestrator",
                    包 / "src" / "agentscope_orchestrator")
    print("══ 先验基线：干净源码自检（基线不绿 ⇒ 不许开始咬）══", flush=True)
    出 = 跑自检(包)
    失败 = None
    for ln in (出 or "").splitlines():
        if "失败" in ln and "判据点" in ln:
            失败 = ln.strip()
    if 失败 is None:
        return False, "（没找到摘要行）" + (出 or "")[-600:]
    数字 = 失败.split("失败")[-1].strip().split()[0]
    return 数字 in ("0", "0）", "0)"), 失败


def main() -> int:
    shutil.rmtree(根, ignore_errors=True)
    绿, 摘要 = _跑基线()
    print(f"   基线：{摘要}", flush=True)
    if not 绿:
        print("\n   ✗ **基线不绿 ⇒ 拒跑**：干净源码上自检就有失败项，"
              "此时任何「变异咬住」都不能归因于变异（齿可能恒红）。\n"
              "   先修齿，再验咬合。", flush=True)
        return 2
    print("   ✓ 基线全绿 ⇒ 可以开始咬\n", flush=True)
    进程 = []
    for i, (cid, 族, 说明, 旧, 新) in enumerate(变异):
        包 = 根 / f"m{i}" / "包"
        (包 / "src").mkdir(parents=True, exist_ok=True)
        shutil.copytree(R / "src" / "agentscope_orchestrator",
                        包 / "src" / "agentscope_orchestrator")
        p = 包 / "src" / "agentscope_orchestrator" / "generic_goal.py"
        raw = p.read_bytes().decode("utf-8")
        # ★ 行尾归一：`generic_goal.py` 是 **CRLF**，而变异锚里写的是 `\n`
        #   —— 不归一则跨行锚**一条都匹配不上**（真读数：m4/m5 报「锚不在」）。
        #   这是「不要猜数据的形状，去读它的生产者」的又一次：我以为它是 LF。
        _crlf = "\r\n" in raw
        _norm = raw.replace("\r\n", "\n")
        if 旧 not in _norm:
            print(f"   ✗ m{i} {cid}/{族}：变异锚不在源码里")
            return 1
        _改 = _norm.replace(旧, 新, 1)
        p.write_bytes((_改.replace("\n", "\r\n") if _crlf else _改).encode("utf-8"))
        进程.append((f"m{i}", cid, 族, 说明, subprocess.Popen(
            [str(PY), "-B", "-m", "agentscope_orchestrator.generic_goal", "--selftest"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONPATH": str(包 / "src"),
                 "PYTHONIOENCODING": "utf-8", "TEMP": str(包 / "tmp"),
                 "TMP": str(包 / "tmp"), "TMPDIR": str(包 / "tmp")},
            cwd=str(包))))
        (包 / "tmp").mkdir(parents=True, exist_ok=True)
        print(f"   起 m{i}：{cid}/{族} —— {说明}", flush=True)

    存活 = []
    齿坏 = []
    for 名, cid, 族, 说明, p in 进程:
        出, _ = p.communicate(timeout=4200)
        红 = [ln.strip() for ln in (出 or "").splitlines() if f"【{cid}" in ln]
        # ★★★★★ R545c：**必须分清「咬住」与「齿自己崩」**。
        #   原来只 grep `【R540` —— 而「门自身异常：NameError」那条 fail 也带这个前缀
        #   ⇒ **齿崩了被记成咬住**（真读数：R540 齿恒红，输出却是 `✓ 咬住`）。
        #   恒红的齿 = 假齿：变异删了接线它红，接线好好的它也红，一点信息量都没有。
        真咬 = [ln for ln in 红 if "自身异常" not in ln]
        if 真咬:
            print(f"\n   ✓ {cid}/{族} 咬住（{len(真咬)} 条红）：{说明}")
            print("     ", 真咬[0][:160])
        elif 红:
            print(f"\n   ✗ {cid}/{族} **齿自己崩了**（不是咬住！）：{说明}")
            print("     ", 红[0][:160])
            齿坏.append(f"{cid}/{族}")
        else:
            print(f"\n   ✗ {cid}/{族} **没咬**（缺陷版本上自检仍绿）：{说明}")
            存活.append(f"{cid}/{族}")

    print()
    # ★★★★★ R545d：汇总必须是**一个**判据。
    #   上一版写成 `if 齿坏: 打印` + `if 存活: return 1` ⇒ 齿坏非空、存活为空时
    #   **掉进成功分支**：先打一条 ✗，紧接着打「✓ 全部咬住 ⇒ 是真齿」并 return 0。
    #   **最后一句才是人读的那句** —— 这是「假绿比红更坏」的又一次。
    #   口径：加一个判据时必须同时问「它的否定分支会不会掉进成功出口」。
    if 齿坏:
        print("✗ **齿自己崩了**（恒红/异常，其「咬住」不可信）=", 齿坏)
    if 存活:
        print("✗ 存活（齿是装饰）=", 存活)
    未达标 = list(存活) + list(齿坏)
    if 未达标:
        print(f"\n✗ 咬合验证未通过：{len(未达标)}/{len(变异)} 条不可信 "
              f"（存活 {len(存活)} · 齿坏 {len(齿坏)}）")
        return 1
    接线 = sum(1 for x in 变异 if x[1] == "接线")
    行为 = sum(1 for x in 变异 if x[1] == "行为")
    print(f"✓ {len(变异)}/{len(变异)} 全部咬住（接线族 {接线} + 行为族 {行为}）⇒ 是真齿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
