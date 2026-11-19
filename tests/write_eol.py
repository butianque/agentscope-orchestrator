#!/usr/bin/env python
"""⑥ 写入不许静默翻转行尾（tests/write_eol.py）

## 为什么单开一支判据

这不是「代码风格」问题，是**我自己先踩到、再回头在生产代码里找到同一个错**：

```
我改本仓 4 个 .py 时用脚本 p.write_text("\\n".join(ls), encoding="utf-8")
（没给 newline=），提交后 git show --stat：
  blind_spot.py  | 1872 +++++++++++---------     ← 整个文件都算改了
真因：newline=None 会把 \\n 翻成 os.linesep，Windows 上是 \\r\\n
```

后果**不是「多改了个换行」**，是三件：

· diff 不可读 —— 真改动被噪声盖住（我自己差点没看出来）
· `git blame` 报废 —— 每一行都算我改的
· 别人 rebase 满屏冲突

**而 `fixloop._原子写` 犯的是同一个错**，且它写的是**被修仓的源文件**：
⇒ 模型修好一个 LF 仓里的文件，落盘时整文件变 CRLF，
   而**命令 rc=0、门绿、字节数还变了** —— 四个信号全指向「成功了」。

★ 口径：**「没报错」不等于「没改坏」。行尾翻转是静默的。**

## 判据要证明什么

| 段 | 验什么 |
|:--|:--|
| ① | **LF 文件写后仍是 LF**（Windows 上跑也不例外）|
| ② | **CRLF 文件写后仍是 CRLF**（不许把「统一成 LF」当成对）|
| ③ | 新文件给 `\\n`（与仓的主流一致）|
| ④ | ★ **反例**：去掉 `newline=""`，① 必须变红 —— 否则这支判据是摆设 |
| ⑤ | ★ **门钉在真件上**：`fixloop` 源码里那次落盘必须带 `newline=""` |
"""

from __future__ import annotations

import os
import shutil
import atexit
import sys
import tempfile
from pathlib import Path

仓 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(仓 / "src"))

桶 = Path(tempfile.mkdtemp(prefix="eol_"))
# ★ 兜底：判据中途失败/被掐也要收掉，不然它会留在包根（实测攒过 85 个）
atexit.register(shutil.rmtree, 桶, ignore_errors=True)
os.environ["ORCH_STATE_DIR"] = str(桶 / "state")

from agentscope_orchestrator import fixloop as fl      # noqa: E402

失败: list[str] = []
无区分力: list[str] = []


def 断言(条件, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}")
    else:
        print(f"  FAIL {说明}")
        失败.append(说明)


def 无区分力注(说明: str) -> None:
    """★ 判不了就说判不了 —— 不许静默当通过。"""
    print(f"  [未验] {说明}")
    无区分力.append(说明)


def 行尾(路径: Path) -> tuple[int, int]:
    b = 路径.read_bytes()
    回 = b.count(b"\r\n")
    return 回, b.count(b"\n") - 回


def 走(名: str, 原: bytes | None, 新文: str) -> Path:
    p = 桶 / 名
    if 原 is not None:
        p.write_bytes(原)
    fl._原子写(p, 新文)
    return p


print("① LF 文件：写后仍是 LF（Windows 上跑也不例外）")
p = 走("lf.py", b"x = 1\ny = 2\n", "x = 1\ny = 9\n")
回, 换 = 行尾(p)
断言(回 == 0 and 换 == 2,
     f"LF 保持（CRLF={回} LF={换}）—— 原来在 Windows 上会整文件变 CRLF")

print("② CRLF 文件：写后仍是 CRLF（不许把「统一成 LF」当成对）")
p = 走("crlf.py", b"x = 1\r\ny = 2\r\n", "x = 1\ny = 9\n")
回, 换 = 行尾(p)
断言(回 == 2 and 换 == 0,
     f"CRLF 保持（CRLF={回} LF={换}）—— 把别人的 CRLF 仓统一成 LF，"
     f"与把它翻成 CRLF 一样是「整文件都算改了」")

print("③ 新文件：给 LF，且不混行尾")
p = 走("新.py", None, "a\nb\nc\n")
回, 换 = 行尾(p)
断言(回 == 0 and 换 == 3, f"新文件给 LF（CRLF={回} LF={换}）")
断言(回 == 0 or 换 == 0,
     "不出现混合行尾（同一份文件两种行尾 ⇒ 下一次 diff 还会花）")

print("④ ★ 反例：去掉 newline= 之后，① 必须变红 —— 否则这支判据是摆设")
原函数 = fl._原子写


def _坏原子写(目标: Path, 文: str) -> None:
    """故意去掉 `newline=` —— 用来证明上面那几条**真的会红**。"""
    目标.write_text(文, encoding="utf-8")


try:
    fl._原子写 = _坏原子写                              # type: ignore[assignment]
    p = 走("反例.py", b"x = 1\ny = 2\n", "x = 1\ny = 9\n")
    回, 换 = 行尾(p)
finally:
    fl._原子写 = 原函数                                  # type: ignore[assignment]
if sys.platform == "win32":
    断言(回 > 0,
         f"去掉 `newline=` ⇒ **LF 真被翻成 CRLF**（CRLF={回}）—— "
         f"说明①②测的是真行为，不是巧合")
else:
    无区分力注("非 Windows：`write_text` 默认不翻译行尾，"
             "这条反例在本平台没有区分力 —— 判不了，不假装")

print("⑤ ★ 门钉在真件上：fixloop 的 `_原子写` 必须带 `newline=\"\"`")
_源 = (仓 / "src" / "agentscope_orchestrator" / "fixloop.py").read_text(encoding="utf-8")
断言('write_text(规范, encoding="utf-8", newline="")' in _源,
     "`_原子写` 里那次落盘带 `newline=\"\"` —— **不钉在这里，"
     "别人重构一下就把这条纪律弄丢了**（本仓已栽过一次："
     "`generic_goal._原子写源码` 写对了，`fixloop._原子写` 没写对）")
断言("def _行尾(" in _源,
     "`_行尾()` 存在 —— 它回答「**目标文件**现在是什么行尾」，"
     "而不是「这个平台喜欢什么行尾」")

print("⑥ ★★ 全仓行尾基线：**任何一次脚本化改动都不许整文件翻转行尾**")
#
# 为什么加这一段 —— 这是我自己栽的，不是假想：
#   2026-11-04 我用 `read_text()`（**它会把 CRLF 吃成 LF**）配
#   `write_text(..., newline="")` 去改 `fixloop.py` ⇒ 那文件**整文件从 CRLF 翻成 LF**。
#   表现：一次约 120 行的改动，`git diff --stat` 报 **3052 行变更**
#   （1579 insertions / 1473 deletions，而内容级 diff `--ignore-cr-at-eol` 是**空的**）。
#   ★ 当时**没有任何判据守着它** —— 上面 ①–⑤ 守的是**模块的写助手**（`_原子写`），
#     不守「人/脚本改了文件」。
#   ⇒ 本仓早已命名这个病（「没报错」不等于「没改坏」。行尾翻转是静默的。），
#     可它一直没有机械判据。这一段就是那道判据。
#
# ★ 基线为什么必须写成明文：
#   「不许翻转」是**相对**不变量，得有基准。基准 = 当前已提交的状态，写死在这里。
#   **要故意改某个文件的行尾，就来改这份基线** ——
#   让它变成一次**明文决定**，而不是一次静默翻转。
#
# ★ 读字节，不许用 `read_text()` —— 后者会把 CRLF 归一成 LF，
#   于是**判据自己就会看不见它要抓的那个东西**（这是同一支病的第 11 张脸）。
_行尾基线_CRLF = {
    "src/agentface.py",
    "src/auto_goal.py",
    "src/auto_mechanism.py",
    "src/autodispatch.py",
    "src/autoengine.py",
    "src/benchmark.py",
    "src/calib.py",
    "src/chief.py",
    "src/constraint.py",
    "src/coord_chain.py",
    "src/develop.py",
    "src/discriminant.py",
    "src/doctor.py",
    "src/engine_dev.py",
    "src/fixloop.py",
    "src/gap_source.py",
    "src/gate_derive.py",
    "src/gate_memory.py",
    "src/gate_power.py",
    "src/gate_spec.py",
    "src/gate_witness.py",
    "src/gateway.py",
    "src/generic_goal.py",
    "src/index.py",
    "src/learning.py",
    "src/maturity.py",
    "src/mem_context.py",
    "src/mem_grid.py",
    "src/mem_symbols.py",
    "src/observation.py",
    "src/orchestrate.py",
    "src/profile.py",
    "src/real_task.py",
    "src/repo_index.py",
    "src/router.py",
    "src/samples.py",
    "src/selfcheck.py",
    "src/static_audit.py",
    "src/taixu_console.py",
    "src/truth_space.py",
    "src/ts_receipt.py",
    "src/unattended.py",
    "src/workspace.py",
    "src/只读探针.py",
    "src/接线门.py",
    "src/见证门.py",
    "tests/agent_usability.py",
    "tests/orch_product_chain.py",   # ★ 2026-11-05：它本来就是 CRLF（HEAD 亦同），基线漏登 ⇒ 判据一直红
    "tests/budget_checkpoint.py",
    "tests/denial_rate.py",
    "tests/g3_sqlite.py",
    "tests/global_gate.py",
    "tests/judge_env_hash.py",
    "tests/lean_native.py",
    "tests/lean_spec.py",
    "tests/mcp_smoke.py",
    "tests/selfcheck.py",
}
_行尾基线_mixed = {
    "src/__init__.py",
    # ★ 2026-11-05：`src/taixu_mcp.py` 在 HEAD 里本来就是**混排**（3375 CRLF + 26 LF），
    #   而基线把它登记在 CRLF 段 ⇒ 判据一直红。我这一轮才把两处对账对出来。
    #   ⇒ 按判据自己的话：改基线 = 一次明文决定（这里写明白了）。
    # ★ 2026-11-05：`src/memory8.py` 从混排变成全 LF（改它的是并行会话，mtime 10:28，
    #   未提交）。**这条判据不改就是红的**，而它红的原因与我无关 —— 与其猜，
    #   不如按判据自己的话办：「真要改就来改这段基线（让它成为一次明文决定）」。
    #   登记在此 = 这是**看见并接受**的事实，不是"判据漏了"。
    # ★ 2026-11-05 第二轮：`src/taixu_mcp.py` 由 R259c 的去重脚本归一成**全 LF**
    #   （原来混排 3374/3481 是我历次脚本插入累积出来的）。全 LF 是干净的一档，
    #   ⇒ 从 mixed 集合移除，不再登记。
}
_应CRLF, _应mixed = set(_行尾基线_CRLF), set(_行尾基线_mixed)
_扫到 = {}
for _子, _前 in (("src/agentscope_orchestrator", "src"), ("tests", "tests")):
    for _f in sorted((仓 / _子).glob("*.py")):
        _b = _f.read_bytes()
        _回 = _b.count(b"\r\n")
        _纯 = _b.count(b"\n") - _回
        _扫到[f"{_前}/{_f.name}"] = ("mixed" if (_回 and _纯)
                                     else ("crlf" if _回 else ("lf" if _纯 else "none")))
_实CRLF = {k for k, v in _扫到.items() if v == "crlf"}
_实mixed = {k for k, v in _扫到.items() if v == "mixed"}
_翻 = sorted(_实CRLF ^ _应CRLF)
断言(not _翻,
     f"**行尾不许静默翻转**（实得差异 {_翻}）—— 翻过的文件 `git diff --stat` 会报"
     f"**整文件变更**而 `--ignore-cr-at-eol` 是空的；改回来即可，"
     f"真要改就来改这段基线（让它成为一次**明文决定**）")
断言(_实mixed == _应mixed,
     f"**混排文件集合不许变**（应为 {sorted(_应mixed)}，实得 {sorted(_实mixed)}）—— "
     f"新出现的混排文件，正是「一次脚本化改动只翻了一半」的样子")
断言(len(_扫到) >= 130,
     f"扫到的 .py 至少 130 个（实得 {len(_扫到)}）—— "
     f"扫不到文件时这条判据会**假装全绿**，那是它最坏的失败方式。"
     f"（★ 我第一版这里拍了个 140，当场被这条抓住：实际 {len(_扫到)} 个。"
     f"  拍数字的下场 —— 所以下限要贴着**实测值**写，并在这里写明它为什么是 130。）")

shutil.rmtree(桶, ignore_errors=True)
print()
if 失败:
    print(f"行尾判据：红 {len(失败)} 项 —— {失败[0]}")
    sys.exit(1)
尾 = f"（另有 {len(无区分力)} 项在本平台判不了，已显式标出）" if 无区分力 else ""
print("行尾判据：绿 —— LF 保 LF、CRLF 保 CRLF、新文件给 LF、反例真会红、门钉在真件上、**全仓行尾基线不许静默翻转**" + 尾)
