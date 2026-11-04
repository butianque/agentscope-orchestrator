# -*- coding: utf-8 -*-
"""一句话入口判据力探针：把入口的**关键机制**逐条退回 ⇒ 自检必须变红。

═══════════════════════════════════════════════════════════════════
产品形态（用户口径原文）
═══════════════════════════════════════════════════════════════════
> 「我不懂，但是要做成**你能操作的**。我直接给你对话。你就能启动的，
>   然后自动调用我提供的 API 跑真实代码的。」

⇒ 使用者只给两样东西：**一个工程目录** + **一句人话**。
   档案、判据门、冻结区、锚点这些东西，他**不需要知道存在**。

═══════════════════════════════════════════════════════════════════
本探针钉住的两条**真跑抓出的缺陷**
═══════════════════════════════════════════════════════════════════
① **`nargs=REMAINDER` 不管用**（入口第一版）：
   `REMAINDER` 只在遇到**第一个位置参数**之后才开始收，而入口的参数
   全是选项 ⇒ `--channel dddddd` 直接被判「unrecognized arguments」
   ⇒ **入口连跑都跑不起来**。修法：`parse_known_args()`。

② **argparse 前缀缩写把下游参数悄悄吃掉**（修①之后暴露出来的）：
   `--n` 是入口自己的 `--no-probe` 的**唯一前缀** ⇒ argparse 把
   `--n 2`（`generic_goal` 的合法参数，表示窗数）匹配成 `--no-probe`
   ⇒ `--n` **没了**，`2` 变成多余位置参数。
   ⇒ 修法：`allow_abbrev=False`。**透传层的参数空间是开放的**，
     缩写匹配会让"入口自己的开关"与"下游的开关"**互相抢**。

★ 两条都是**跑出来的**，不是读出来的 —— 与 R18/R19/R21/R22/R23 同源。
★ 两条都由 `entry.py --selftest` 的 ⑦ 抓出，本探针把 ⑦ 的**齿**钉死：
  退回 `allow_abbrev=False` ⇒ 必须变红。

═══════════════════════════════════════════════════════════════════
探针自身纪律
═══════════════════════════════════════════════════════════════════
  · 判据写**全**（看真跑结果，不看文本）；· 从 `__file__` 推导路径；
  · **字节级还原**并断言 sha1；· 锚点要求**唯一**；
  · 行尾**不假设**（从文件字节读出，`generic_goal` 是 CRLF、`bootstrap` 是 LF）；
  · 检查必须**报告**，不能自己崩。
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
PKG = TOOL / "src" / "agentscope_orchestrator"
ENTRY = PKG / "entry.py"
GG = PKG / "generic_goal.py"
sys.path.insert(0, str(PKG.parent))

fails: list[str] = []

# ★ 探针要改的文件**可能不止一个**（入口 + 回执落盘所在的 generic_goal）
#   ⇒ 每个文件各自留原始字节，还原时逐个字节级还原。
# ★★ R65 互斥：两个进程同时改同一个源文件会**互相还原对方的改动**（实测各 7 红）。
#   锁的键是**被改的文件**，不是探针名 ⇒ 新增探针自动共享同一把锁。
#   ★ 多文件按**同一顺序**取锁（防两探针各持一把互相等 ⇒ 死锁）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _互斥 import 占住 as _占住, 原子写   #  noqa: E402
_锁0 = _占住(ENTRY)   # 等不到就抛 占用错（绝不静默放行）
_锁1 = _占住(GG)   # 等不到就抛 占用错（绝不静默放行）
原字节 = {ENTRY: ENTRY.read_bytes(), GG: GG.read_bytes()}
原_sha = {p: hashlib.sha1(b).hexdigest() for p, b in 原字节.items()}


def _跑自检(超时: int = 420) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PKG.parent)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        cp = subprocess.run([sys.executable, str(ENTRY), "--selftest"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=超时, env=env)
    except subprocess.TimeoutExpired:
        # ★ 超时**必须报告**，不能崩 —— 本库纪律：检查必须报告。
        #   ★ 而且超时**算红**：一条门"跑到挂死"绝不能算通过
        #     （否则退回缺陷只会让自检变慢，不会让它变红 ⇒ 无齿）。
        #   真跑抓出：退回"拒跑无判据工程"那条守卫后，入口真去调模型
        #   ⇒ 自检 900 秒超时 ⇒ 探针自己崩掉，把"门有没有齿"这个问题
        #     变成"探针能不能跑完"。现在超时被如实报成红。
        return 1, f"（自检超时 >{超时}s ⇒ 判红：跑到挂死绝不能算通过）"
    except OSError as _eOS:
        # ★★★★★ R150（第 63 层 · 2026-10-24 · 全量回归 R148_2000）：
        #   **「子进程起不来」不是「自检不绿」。**
        #
        #   真证据：
        #     回归里 `红 checks/入口齿探针.py ✗ ⑦ 还原后自检不绿`
        #     而**单独跑同一个探针：全 PASS**（含 ⑦ 两条字节级还原）。
        #     回归 stderr 里躺着：
        #       `OSError: [WinError 1455] 页面文件太小，无法完成操作。`
        #       `  File "…\subprocess.py", line 1538, in _execute_child`
        #
        #   ★ 当时另一个 agent 正在跑 cargo（一个 taixu_lingti 进程占 8.4 GB，
        #     外加多个 rustc）⇒ 页面文件吃满 ⇒ `CreateProcess` 失败。
        #   ★★ 而本函数只捕获 `TimeoutExpired` ⇒ `OSError` 冒出去 ⇒
        #     探针**崩在自己的第 ⑦ 项**上 ⇒ 报表写「还原后自检不绿」。
        #     **它没说谎，但它说错了原因**：自检根本没跑，不是没跑绿。
        #
        #   ★★★ 这是「手里那个异常不是判据需要的那个会不会自己好」的**第 2 次**
        #     （第 1 次是 R144：`no_provider_under_bid` 被当终态）：
        #     判据需要的是「**自检绿不绿**」，
        #     我拿到的是「**这次子进程有没有起来**」—— 后者失败时前者**无值**。
        #
        #   ★★★★ 处置：起不来 ⇒ 返回**专门的码 3**（≠0 也≠1），
        #     并**说清楚是环境不足**。调用方据此判「跳过（无法判定）」，
        #     ★ 绝不判绿（那会把没验说成验了），
        #     ★ 也不判红（那会把环境问题栽给产品 —— 正是本次的假红）。
        return 3, (f"（子进程**起不来**：{type(_eOS).__name__}: {_eOS}）"
                   f" ⇒ ★ 这不是「自检不绿」，是**自检没跑**"
                   f"（多半是内存/页面文件不足，或并发进程过多）")
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def _退回(目标: Path, 替换表: list[tuple[str, str]]) -> None:
    """按字节替换（锚点用文本模板，行尾从**该文件**的字节读出）。锚点**必须唯一**。

    ★ 行尾不假设：`generic_goal.py` 是 CRLF、`entry.py` 是 LF
      —— 这是本库行尾陷阱的第 N 次现形，修法是**读出来**而不是猜。
    """
    nl = "\r\n" if b"\r\n" in 原字节[目标] else "\n"
    b = 原字节[目标]
    for 旧文本, 新文本 in 替换表:
        旧 = 旧文本.replace("\n", nl).encode("utf-8")
        新 = 新文本.replace("\n", nl).encode("utf-8")
        n = b.count(旧)
        if n != 1:
            raise AssertionError(f"锚点出现 {n} 次（要求唯一）：{旧文本[:70]!r}")
        b = b.replace(旧, 新)
    原子写(目标, b)


def _场景(名: str, 目标: Path, 替换表: list[tuple[str, str]], 要点: str) -> None:
    try:
        _退回(目标, 替换表)
        rc, 出 = _跑自检()
        if rc == 0:
            fails.append(f"{名} ★ 门无齿：退回后自检**仍全绿**")
        elif 要点 not in 出:
            fails.append(f"{名} 自检红了但**没点名 {要点}**（红错地方）："
                         f"{出.strip()[-240:]}")
        else:
            print(f"PASS {名} 退回后自检红且点名 {要点}")
    except AssertionError as e:
        fails.append(f"{名} 探针锚点失效（实现改过了？）：{e}")
    finally:
        原子写(目标, 原字节[目标])


# ═══════════════════════════════════════════════════════════════════
# ① 反自证
# ═══════════════════════════════════════════════════════════════════
rc, 出 = _跑自检()
if rc != 0:
    fails.append(f"① 未改时自检竟然红（探针自身有问题）：{出.strip()[-300:]}")
else:
    print("PASS ① 未改时自检全绿（反自证）")

# ═══════════════════════════════════════════════════════════════════
# ② ★★★★ 真缺陷①：退回 `parse_known_args` → `parse_args`
#      ⇒ 未知参数被拒 ⇒ 入口跑不起来。
#      ★ 要点是 argparse 的**真实报错特征**（`unrecognized arguments`），
#        不是自检门号 —— 因为退回后自检**根本走不到 ⑦**
#        （argparse 直接 `SystemExit(2)`）。
#        ★ 我第一版把要点写成 "⑦" ⇒ 恒不匹配 ⇒ 报"红错地方"（探针判据写窄了）。
# ═══════════════════════════════════════════════════════════════════
_场景("②", ENTRY, [("    a, 透传 = ap.parse_known_args(argv)",
                   "    a = ap.parse_args(argv)\n    \u900f\u4f20 = []")],
      "unrecognized arguments")

# ═══════════════════════════════════════════════════════════════════
# ③ ★★★★ 真缺陷②：退回 `allow_abbrev=False` ⇒ `--n` 被 `--no-probe` 吃掉
# ═══════════════════════════════════════════════════════════════════
_场景("③", ENTRY, [("        allow_abbrev=False)", "        )")], "⑦")

# ═══════════════════════════════════════════════════════════════════
# ④ ★★★ 延迟导入：把 import 提到设置 ORCH_PROFILE **之前**
#      ⇒ REPO_ROOT 会绑定成旧值（改错树）
# ═══════════════════════════════════════════════════════════════════
_场景("④", ENTRY, [("    os.environ[\"ORCH_PROFILE\"] = str(\u6863\u6848)",
                   "    import agentscope_orchestrator.generic_goal as _gg_early\n"
                   "    os.environ[\"ORCH_PROFILE\"] = str(\u6863\u6848)")],
      "④")

# ═══════════════════════════════════════════════════════════════════
# ⑤ ★★★ 拒跑无判据工程：不可识别时**不得**动手（R21 的同一条原理）
# ═══════════════════════════════════════════════════════════════════
_场景("⑤", ENTRY, [("    if \u6863\u6848 is None:", "    if False:")], "③")

# ═══════════════════════════════════════════════════════════════════
# ⑥ ★★ 回执写盘不得崩整趟跑（真跑抓出：审计目录缺失 ⇒ FileNotFoundError）
#    ★ 这一条的锚点在 **generic_goal.py**（`_emit` 住那里），不在入口 ——
#      所以我第一版把目标写成 ENTRY ⇒ 锚点 0 次匹配。
#      ⇒ 探针支持多目标文件（`原字节` 是一张表）。
# ═══════════════════════════════════════════════════════════════════
_场景("⑥", GG, [("            _RECEIPT.parent.mkdir(parents=True, exist_ok=True)\n"
                 "            with _RECEIPT.open(\"a\", encoding=\"utf-8\") as f:\n"
                 "                f.write(line + \"\\n\")\n",
                 "            with _RECEIPT.open(\"a\", encoding=\"utf-8\") as f:\n"
                 "                f.write(line + \"\\n\")\n")],
      "⑤b")

# ═══════════════════════════════════════════════════════════════════
# ⑦ 还原：**每个**被改文件都必须字节级一致 + 自检绿
# ═══════════════════════════════════════════════════════════════════
for _t, _名 in ((ENTRY, "entry"), (GG, "generic_goal")):
    if hashlib.sha1(_t.read_bytes()).hexdigest() != 原_sha[_t]:
        fails.append(f"⑦ {_名} 未还原干净")
    else:
        print(f"PASS ⑦ 还原：{_名}.py 与探针开跑前**字节级一致**")
# ★ R150：**起不来 ≠ 不绿**。字节级还原已经过了（上面两条 PASS），
#   自检跑不起来时本项**无法判定** ⇒ 明确跳过，不栽给产品。
_码150, _出150 = _跑自检()
if _码150 == 3:
    print("跳过 ⑦ 还原后自检：子进程起不来（环境不足）⇒ 这一项此刻无法判定")
    print(f"  ★ {_出150}")
elif _码150 != 0:
    fails.append("⑦ 还原后自检不绿")

print()
if fails:
    print("FAIL")
    for f in fails:
        print("  ✗", f)
    raise SystemExit(1)
print("入口齿探针：全部 PASS（REMAINDER 坑 · 缩写吃参数 · 延迟导入 · 拒跑无判据 · "
      "回执不崩 · 字节级还原）")
