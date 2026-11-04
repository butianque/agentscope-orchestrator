# -*- coding: utf-8 -*-
"""自举判据力探针：把自举的**排除机制**与 R25 **逐条退回** ⇒ 自检必须变红。

═══════════════════════════════════════════════════════════════════
本探针针对的缺陷（**真跑抓出来的**）
═══════════════════════════════════════════════════════════════════
在一个**真 Rust 工程**上跑 `bootstrap`，自动扫出的冻结区是**两条**：

    · src/lib.rs                                ← 对
    · .orchestrator/generic_backup/src/lib.rs   ← **工具自己的备份**

★ 我起初断言"那会导致**每次运行都假红**"（备份在运行中被覆写）。
  **探针②推翻了这个断言**：备份与源码的冻结区恰好相同 ⇒ 比不出差异。
  ⇒ 如实纠正，不留夸大的因由。

★★ 但顺着这条线查下去，发现了**更基本、可证**的那个洞（= R25）：
  `_冻结区取文` 对**文件不存在**返回 `None`，而 `_冻结区快照` 里是 `continue`
  —— **静默跳过**。于是：

    · 首次运行时备份目录**还不存在** ⇒ 那条声明**一点保护都没有**；
    · 而**没有任何人知道** —— 日志里只看到「冻结区逐字未变」，
      会被读成"判据受保护了"。

  ★ 这正是本库反复踩的病：**"没跑"看起来跟"绿"一样**。

本探针因此分两层：
  ② **证明**上面那条（真调生产代码：快照 → 改真文件 → 看报不报得出来）；
  ③④ 齿：退回排除机制（bootstrap）与 R25 报告（generic_goal）⇒ 自检必须红。

═══════════════════════════════════════════════════════════════════
探针自身纪律
═══════════════════════════════════════════════════════════════════
  · 判据写**全**（看真跑结果，不看文本）；
  · 一律从 `__file__` 推导路径（可移植）；
  · **字节级还原**，最后断言两个被改文件的 sha1 与开跑前一致；
  · 锚点要求**唯一**（`_退回` 自检出现次数，不唯一就报错而不是乱改）；
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
BOOT = PKG / "bootstrap.py"
GG = PKG / "generic_goal.py"
sys.path.insert(0, str(PKG.parent))

fails: list[str] = []
# ★★ R65 互斥：两个进程同时改同一个源文件会**互相还原对方的改动**（实测各 7 红）。
#   锁的键是**被改的文件**，不是探针名 ⇒ 新增探针自动共享同一把锁。
#   ★ 多文件按**同一顺序**取锁（防两探针各持一把互相等 ⇒ 死锁）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _互斥 import 占住 as _占住, 原子写   #  noqa: E402
_锁0 = _占住(BOOT)   # 等不到就抛 占用错（绝不静默放行）
_锁1 = _占住(GG)   # 等不到就抛 占用错（绝不静默放行）

原 = {BOOT: BOOT.read_bytes(), GG: GG.read_bytes()}
原_sha = {p: hashlib.sha1(b).hexdigest() for p, b in 原.items()}


def _跑自检(目标: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PKG.parent)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cp = subprocess.run([sys.executable, str(目标), "--selftest"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=900, env=env)
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def _行(目标: Path, 文本: str) -> bytes:
    """按**该文件自己的行尾风格**把文本模板转成字节。

    ★ 真跑抓出的探针自身缺陷（2026-10-15）：本探针第一版用 `\\n` 写锚点，
      而 `generic_goal.py` 是 **CRLF**（`bootstrap.py` 是 LF）
      ⇒ 锚点**一处也匹配不上**，报"锚点出现 0 次"。
    ★ 这是**同一类**行尾陷阱的第三次现形（R22 落盘、R23c 冻结区比对，
      现在是探针自己的锚点）。⇒ 修法：**不假设**行尾，从文件字节里读出来。
      ★ 绝不能"把文件统一成 LF 再改" —— 那会改掉整个文件的行尾（R22 的坑）。
    """
    nl = "\r\n" if b"\r\n" in 原[目标] else "\n"
    return 文本.replace("\n", nl).encode("utf-8")


def _退回(目标: Path, 替换表: list[tuple[str, str]]) -> None:
    """按字节替换源码（锚点用**文本模板**，行尾自动适应该文件）。

    锚点**必须唯一**（不唯一 ⇒ 报错，绝不乱改）。
    """
    b = 原[目标]
    for 旧文本, 新文本 in 替换表:
        旧, 新 = _行(目标, 旧文本), _行(目标, 新文本)
        n = b.count(旧)
        if n != 1:
            raise AssertionError(f"锚点出现 {n} 次（要求唯一）：{旧文本[:70]!r}")
        b = b.replace(旧, 新)
    原子写(目标, b)


def _场景(名: str, 目标: Path, 替换表: list[tuple[str, str]], 要点: str) -> None:
    """跑一个齿场景：退回 ⇒ 自检必须红 **且点名** 要点。

    ★ R65b（2026-10-16）：**动手前先证明基准是干净的**，否则拒绝启动。

    ── 真事故（本次）──
    本探针的还原是 `finally: 目标.write_bytes(原[目标])`，而 `原` 是**模块导入时**
    读进来的。两份探针并发时会出现这个交错：

        甲：读 `原` = 干净
        甲：注入破坏
        乙：读 `原` = **已被破坏的版本**        ← 关键一步
        甲：还原成干净
        乙：还原成**被破坏的版本**              ← 永久损坏

    实测后果：`_冻结区未取到` 开头被永久插入 `out = []` / `return out`
    ⇒ 整个函数变成死代码 ⇒ R25 的「取不到的冻结区必须报出来」**静默失效**，
    而**每个报表看起来都正常**（我是靠"自检 240/242"才发现的）。

    ★ 只加锁（R65）**不够**：锁防得住"同时跑"，防不住"上一次就没还原干净"。
    ★ 口径：**基准不干净，"还原"本身就是污染。**
      ⇒ 每次动手前先查"替换后的形态是不是已经在原文里" —— 在 ⇒ 基准脏了 ⇒ 拒绝。
    """
    try:
        # ★ 比对必须用**该文件自己的行尾**（`generic_goal.py` 是 CRLF）——
        #   直接拿 `\n` 模板去比会**一处也匹配不上** ⇒ 守卫恒不触发（假绿）。
        #   这是行尾陷阱的**第四次**现形（R22 落盘 / R23c 冻结区 / 探针锚点 / 本守卫）。
        # ★ 空替换串（删除型场景）必须跳过：`"" in 任意字节` 恒真 ⇒ 会**假拒**。
        for _旧, _新 in 替换表:
            if not _新.strip():
                continue
            if _行(目标, _新) in 原[目标]:
                raise SystemExit(
                    f"★ 拒绝启动：{目标.name} 的基准**已被污染** —— "
                    f"「{_新.strip()[:60]!r}」已经在原文里。\n"
                    f"  含义：上一次注入**没还原干净**（或两份探针并发交错）。\n"
                    f"  ★ 继续跑会把**破坏版本**当成原文永久写回。\n"
                    f"  修法：先把源码改回干净形态，再跑本探针。")
        _退回(目标, 替换表)
        rc, 出 = _跑自检(目标)
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
        原子写(目标, 原[目标])


# ═══════════════════════════════════════════════════════════════════
# ① 反自证：未改时两个自检都必须绿（防探针自己恒红）
# ═══════════════════════════════════════════════════════════════════
for _t, _名 in ((BOOT, "bootstrap"), (GG, "generic_goal")):
    rc, 出 = _跑自检(_t)
    if rc != 0:
        fails.append(f"① 未改时 {_名} 自检竟然红（探针自身有问题）：{出.strip()[-300:]}")
    else:
        print(f"PASS ① 未改时 {_名} 自检全绿（反自证）")

# ═══════════════════════════════════════════════════════════════════
# ② ★★★★★ 证明「声明了冻结区，却**静默不生效**」真实存在
#     真调生产代码：快照 → 改真文件 → 看报不报得出来。不推理、不看文本。
# ═══════════════════════════════════════════════════════════════════
d = Path(tempfile.mkdtemp(prefix="bootteeth_"))
try:
    (d / "src").mkdir(parents=True)
    原子写(d / "src" / "lib.rs",
           b"pub fn max() -> i32 { 5 }\n\n#[cfg(test)]\nmod assert_eq_5 {}\n")

    import agentscope_orchestrator.bootstrap as boot
    import agentscope_orchestrator.generic_goal as gg

    # ── ②a bootstrap 侧：排除生效（备份目录**不得**进冻结候选）──
    (d / ".orchestrator" / "generic_backup" / "src").mkdir(parents=True)
    原子写(d / ".orchestrator" / "generic_backup" / "src" / "lib.rs",
           b"pub fn max() -> i32 { 0 }\n\n#[cfg(test)]\nmod assert_eq_5 {}\n")
    排除 = boot._排除集(d)
    if ".orchestrator" not in 排除:
        fails.append("②a 排除集里没有工具自己的审计目录 `.orchestrator`")
    冻结, _ = boot._扫冻结(d, boot._工程谱[0], 排除)
    路径s = [f["path"] for f in 冻结]
    if 路径s != ["src/lib.rs"]:
        fails.append(f"②a 排除后冻结候选仍不对：{路径s}")
    else:
        print("PASS ②a 排除后冻结候选只有 src/lib.rs（不含工具自己的备份）")

    # ── ②b generic_goal 侧：**证明**静默不生效 ──
    #   声明一条指向**不存在**的文件（= 首次运行时尚无备份的真实情形）
    旧_REPO = gg.REPO_ROOT
    try:
        gg.REPO_ROOT = d
        坏 = [{"path": ".orchestrator/generic_backup/src/lib.rs",
               "begin": "#[cfg(test)]"}]
        if (d / 坏[0]["path"]).exists():
            (d / 坏[0]["path"]).unlink()          # 造出"首次运行"的情形
        _snap = gg._冻结区快照(坏)
        if _snap:
            fails.append("②b 文件不存在时快照**非空**（探针假设不成立，需修探针）")
        # 现在把**真判据**改弱（把断言去掉）—— 看能不能报出来
        真 = d / "src" / "lib.rs"
        原子写(真, "pub fn max() -> i32 { 5 }\n\n#[cfg(test)]\nmod 空 {}\n"
                .encode("utf-8"))
        if gg._冻结区违反(_snap):
            fails.append("②b 快照为空却报出了违反（探针假设不成立）")
        else:
            print("PASS ②b ★ 证明：声明了冻结区、但文件不存在时**一点保护都没有**"
                  "（改弱真判据也报不出来）—— R25 的洞是**真的**")
        # 修好后：必须**报出来**
        未取 = gg._冻结区未取到(坏)
        if len(未取) != 1 or "不存在" not in str(未取[0].get("err")):
            fails.append(f"②b ★ R25 未生效：取不到的冻结区未被报出：{未取}")
        else:
            print("PASS ②b ★ R25：取不到的冻结区**被报出来了**（不再静默）")
    finally:
        gg.REPO_ROOT = 旧_REPO
finally:
    shutil.rmtree(d, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════
# ③④⑤⑥ 齿：逐条退回
# ═══════════════════════════════════════════════════════════════════
# ③ ★ R25：让"未取到"恒报空 ⇒ 又回到静默跳过
_场景("③", GG,
      [("    out = []\n"
        "    for s, got in _冻结区逐条(specs):\n"
        "        if got is not None:\n"
        "            continue\n",
        "    out = []\n"
        "    return out\n"
        "    for s, got in _冻结区逐条(specs):\n"
        "        if got is not None:\n"
        "            continue\n")],
      "【R25】")

# ④ 去掉工具审计目录的排除（最直接的那一条）
_场景("④", BOOT, [('    ".orchestrator",\n', "")], "⑪")

# ⑤ 让 `_收集` **不做**排除（排除表还在，但没人用它）
_场景("⑤", BOOT,
      [("            if any(x in 排除 for x in parts[:-1]):\n"
        "                continue\n",
        "            if False:\n"
        "                continue\n")],
      "⑪")

# ⑥ `.gitignore` 的取反条目（`!保留/`）不再被跳过 ⇒ 方向反了
_场景("⑥", BOOT,
      [('        if not s or s.startswith("#") or s.startswith("!"):\n',
        '        if not s or s.startswith("#"):\n')],
      "⑪b")

# ⑦ 去掉通配过滤 ⇒ 通配被当成目录名
_场景("⑦", BOOT,
      [('        if any(ch in s for ch in "*?[]"):\n'
        '            continue                       # 通配模式：不猜\n',
        "        if False:\n"
        "            continue\n")],
      "⑪b")

# ═══════════════════════════════════════════════════════════════════
# ⑧ 还原：两个文件都必须与开跑前**字节级一致**
# ═══════════════════════════════════════════════════════════════════
for _t, _名 in ((BOOT, "bootstrap"), (GG, "generic_goal")):
    if hashlib.sha1(_t.read_bytes()).hexdigest() != 原_sha[_t]:
        fails.append(f"⑧ {_名} 未还原干净")
    elif _跑自检(_t)[0] != 0:
        fails.append(f"⑧ 还原后 {_名} 自检不绿")
    else:
        print(f"PASS ⑧ 还原：{_名} 与探针开跑前**字节级一致**且自检绿")

print()
if fails:
    print("FAIL")
    for f in fails:
        print("  ✗", f)
    raise SystemExit(1)
print("自举齿探针：全部 PASS（静默不生效已证明 · 排除机制四条齿 · R25 一条齿 · 字节级还原）")
