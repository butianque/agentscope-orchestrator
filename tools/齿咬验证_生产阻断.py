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


def main() -> int:
    shutil.rmtree(根, ignore_errors=True)
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
    for 名, cid, 族, 说明, p in 进程:
        出, _ = p.communicate(timeout=4200)
        红 = [ln.strip() for ln in (出 or "").splitlines() if f"【{cid}" in ln]
        if 红:
            print(f"\n   ✓ {cid}/{族} 咬住（{len(红)} 条红）：{说明}")
            print("     ", 红[0][:160])
        else:
            print(f"\n   ✗ {cid}/{族} **没咬**（缺陷版本上自检仍绿）：{说明}")
            存活.append(f"{cid}/{族}")

    print()
    if 存活:
        print("✗ 存活（齿是装饰）=", 存活)
        return 1
    print(f"✓ {len(变异)}/{len(变异)} 全部咬住（接线族 3 + 行为族 3）⇒ 是真齿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
