# -*- coding: utf-8 -*-
"""语义正确性的**三个替身**（P4 · 判据 #27）。

★★★ 永久写明（不是免责，是定义）：
    **语义本身不可判** —— 「这段代码改得对不对」等价于自动程序验证，一般不可判
    （判定问题的不可判定性），任何声称"判得了语义"的工具都是在骗。
    这里判的是语义的**三个可判替身**，绿了只说明这三件事成立，**不**说明语义对：
      ① **契约一致**：函数真的返回它声明的那几个字段（`field_contract.校验`）
      ② **不变量**：约束关系必须是等价关系（自反/对称/传递），且与布尔取值一致
      ③ **变异区分**：往判据工具体里注机械错误，**判据门抓不抓得住**
        （抓不住 = 门形同虚设；这一段直接对着 R552「退出码门被 sys.exit(0) 击穿」）

判据：三个替身的绿率**分别报数**；① ② 必须 100%，③ 的区分力 ≥ 2/3。
退出码 0 = 三条都达标；末行固定 `判定=OK` / `判定=不达标`。
"""
from __future__ import annotations

import itertools
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
包根 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(包根 / "src"))

不可判声明 = ("语义本身仍不可判（等价于自动程序验证）；这里判的是它的三个替身："
              "契约一致 / 不变量 / 变异区分。")


def 替身一_契约():
    from agentscope_orchestrator import field_contract as fc
    果 = fc.校验()
    过 = int(果.get("过") or 0)
    错 = 果.get("错") or []
    总 = 过 + len(错)
    率 = (过 / 总) if 总 else 0.0
    print(f"   ① 契约一致：{过}/{总} = {率:.1%}" + ("（ok=true）" if 果.get("ok") else ""))
    for e in 错[:5]:
        print(f"      ✗ {e}")
    return 率, 总 >= 10


def 替身二_不变量():
    from agentscope_orchestrator import constraint as ct
    组 = 0
    过 = 0
    # 1) 等价关系：全 8 种三元素布尔组合
    for 组值 in itertools.product([True, False], repeat=3):
        组 += 1
        ok, 说 = ct.约束是等价关系(list(组值))
        if ok:
            过 += 1
    # 2) 与布尔取值一致：约束(a,b) 必须等价于 (a == b)（它是"同一约束"的判据）
    一致 = True
    for a in (True, False):
        for b in (True, False):
            if bool(ct.约束(a, b)) != (a == b):
                一致 = False
    组 += 1
    过 += 1 if 一致 else 0
    # 3) 目标变约束变：**样本不足时必须返回 False**（不许把"看不出来"说成"变了"）
    if ct.目标变约束变({"a": True, "b": True}, {"a": True, "b": True}, ["a", "b"])[0] is False:
        组 += 1
        过 += 1
    else:
        组 += 1
    率 = 过 / 组 if 组 else 0.0
    print(f"   ② 不变量：{过}/{组} = {率:.1%}（等价关系 8 组 + 布尔一致 + 样本不足须说不知道）")
    return 率, 组 >= 9


# ★ 变异串必须与 `tools\\覆盖核算.py` 的**原文逐字一致**（先 grep 出来再写死，
#   不这么干就会「无从注入」——而"无从注入"若被算成"抓住"，就是假绿）。
# 靶：`tools\\拆树判决探针.py`（**自包含**：自带 temp 夹具，复制到哪都一样）
# ── 变异靶：**产物** `src\\agentscope_orchestrator\\workspace.py`（就地改、finally 还原）──
#   每条 = (说明, 原文, 改成)。原文必须与文件**逐字一致**，否则记「无从注入」。
Q = chr(34)          # 双引号：拼出来，不在补丁里转义

# ── 变异靶：**产物** `src\\agentscope_orchestrator\\workspace.py`（就地改、finally 还原）──
#   每条 = (说明, 原文片段, 改成)。原文片段必须与文件**逐字一致**，否则记「无从注入」。
_变异们 = [
    ("删不掉也判成功（判决恒 0）",
     "    return 3, (f" + Q + "✗ 删不掉",
     "    return 0, (f" + Q + "✗ 删不掉"),
    ("拆() 恒报删成功（不看盘上还在不在）",
     "        出[" + Q + "删目录" + Q + "] = not 树.exists()",
     "        出[" + Q + "删目录" + Q + "] = True"),
    ("main --clean 无条件成功（码=0）",
     "                码, 话 = _删结果判决(树, r, w.get(" + Q + "分支" + Q + ", " + Q + Q + "))",
     "                码, 话 = _删结果判决(树, r, w.get(" + Q + "分支" + Q + ", " + Q + Q + ")); 码 = 0"),
]

_产物 = 包根 / "src" / "agentscope_orchestrator" / "workspace.py"
_门具 = 包根 / "tools" / "拆树判决探针.py"


def 替身三_变异():
    """变异注进**产物**，看**判据工具**是否变红。返回 (区分力, 样本够否)。

    ★ 三态：抓住 / 存活（门有洞）/ 无从注入（原文没这段）——分母只算注得进的那些，
      并**如实报**"注不进"的条数（口径 375：别拿两个空集比）。
    """
    import hashlib
    import subprocess
    import sys as _sys

    原件 = _产物.read_bytes()
    原哈希 = hashlib.sha256(原件).hexdigest()
    文 = 原件.decode("utf-8")
    抓 = 存 = 0
    无 = 0
    try:
        # ① 基线：没变异时门必须绿（否则测的不是这条门）
        cp0 = subprocess.run([_sys.executable, "-B", str(_门具)], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=900)
        if "判定=OK" not in (cp0.stdout or ""):
            print("      ⚠ 基线就不达标 ⇒ ③ 记未验（先让门在正确产物上变绿）")
            return None, False
        print("      · 基线：门是绿的（可作对照）")
        for 说明, 旧, 新 in _变异们:
            if 旧 not in 文:
                无 += 1
                print(f"      · {说明}：**无从注入**（原文没这段）")
                continue
            try:
                _产物.write_text(文.replace(旧, 新, 1), encoding="utf-8")
                cp = subprocess.run([_sys.executable, "-B", str(_门具)], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=900)
                出 = (cp.stdout or "") + (cp.stderr or "")
                绿 = ("判定=OK" in 出) and cp.returncode == 0
            finally:
                _产物.write_bytes(原件)          # ★ 无论成败都立刻还原
            if 绿:
                存 += 1
                print(f"      · {说明}：**存活**（产物坏了门还绿 ⇒ 门有洞）")
            else:
                抓 += 1
                print(f"      · {说明}：抓住了")
    finally:
        _产物.write_bytes(原件)
        复核 = hashlib.sha256(_产物.read_bytes()).hexdigest()
        if 复核 != 原哈希:
            print(f"      ✗✗ 产物没能还原！{原哈希[:12]} → {复核[:12]}（必须人工介入）")
    总 = 抓 + 存
    率 = (抓 / 总) if 总 else 0.0
    print(f"   ③ 变异区分：抓住 {抓}/{总}（注得进 {总} · 无从注入 {无}）· 区分力 = {率:.0%}")
    return 率, 总 >= 2


def main() -> int:
    print(f"★ {不可判声明}")
    a, a够 = 替身一_契约()
    b, b够 = 替身二_不变量()
    c, c够 = 替身三_变异()
    c文 = "未验" if c is None else f"{c:.0%}"
    print(f"   三替身绿率：契约 {a:.1%} · 不变量 {b:.1%} · 变异区分 {c文}")
    if c is None:
        print("判定=未验（③ 的基线副本自己就不达标 ⇒ 那个位置会改变被测量的东西）")
        print("   ⇒ 不判绿也不判红：环境问题记未验（口径 545），别混进 PASS/FAIL")
        return 2
    ok = a >= 1.0 and b >= 1.0 and c >= 0.66 and a够 and b够 and c够
    print(f"判定={'OK' if ok else '不达标'}")
    if not ok:
        if a < 1.0:
            print("   ⇒ 契约有不一致（函数没返回它声明的字段）")
        if b < 1.0:
            print("   ⇒ 不变量不成立（约束不是等价关系 / 或把'看不出来'说成了'变了'）")
        if c < 0.66:
            print("   ⇒ 变异区分力不足：注进去的机械错误没被 must 记号抓住 ⇒ 门形同虚设")
        if not (a够 and b够 and c够):
            print("   ⇒ 样本太小，判据不成立（不许拿三个样本说'全局没问题'）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
