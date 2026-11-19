r"""本地 CI 守门：一次跑完「套件 + 装完即验 + wheel 一致性」，结果写成回执。

★ 为什么需要它（2026-11-02 定）：本仓没有远程 CI，于是"交付物是对的"这件事
  靠**我记得跑套件**。实测已经吃过两次亏：改完源码没重打包（`wheel_bytes` 红）、
  用例临时目录漏进提交（混进两次提交）。纪律不是工程状态 —— 这个脚本把它变成一条命令。

口径：
  · 任何一支非 0（含 `未验=3`）⇒ 本次 CI 红，退出码非 0。
  · **未验（rc=3）在 CI 里当红**：CI 的职责是"能不能交付"，不是"今天环境好不好"。
    想放行未验，跑 `--allow-unverified`（会在回执里显式记一笔）。
  · 回执写到 `receipts/ci-<时间戳>.json/.md`，可回读、可对比历史。

跑法：
  python tools/ci.py             # 全量：34 支套件 + selfcheck + wheel_bytes
  python tools/ci.py --fast      # 快集：selfcheck 回归 + wheel_bytes（提交前用）
"""
from __future__ import annotations

import argparse
import json
import tempfile
import os
import subprocess
import sys
import time
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
py = sys.executable

# ★★★ 2026-11-04（真根因，一次修掉一整类病）：
#   本机 `tempfile.gettempdir()` 实测**回退到仓根** —— `TEMP`/`TMP`/`TMPDIR`
#   三个候选对这个 python **都不可写** ⇒ 每一次 `mkdtemp()` 都落在仓根附近。
#   长期后果有两个，而且看起来毫不相干：
#     ① 仓根/包根攒出**上千个泄漏目录**（实测 1318 个，全被 .gitignore 藏着）；
#     ② **套件里判据互相顶替**：`bench_fixtures` 的"正确解"被旧的变异体文件顶掉
#        （实测 `add(2,3)` 得 -1、`scale(3)` 得 6）、`gate_power` 报"度量过程改动了
#        调用方的仓" —— 两者**单跑全绿**，只有在套件里串跑才红。
#   ⇒ 修法不是继续加 atexit 兜底（那是治症状），而是**把临时根钉到一个可写目录**，
#     并导出给所有子进程，让 `mkdtemp` 不再有"回退到 cwd"这条路。
_临时根 = 根 / "tests" / "_run_all_state" / "_tmp"
_临时根.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_临时根)
for _k in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_k] = str(_临时根)

快集 = [
    ("wheel_bytes", [py, "tests/wheel_bytes.py"]),
    ("selfcheck", [py, "tests/selfcheck.py"]),
]
全量额外 = [
    ("selfcheck_live", [py, "-m", "agentscope_orchestrator.selfcheck"]),
    # ★ 引用体检：注释里点名的出处不存在 ⇒ 可核验的缺陷，进 CI 才有人管。
    #   它只读**项目级**图缓存（`.orchestrator/refgraph.json`），不建图；
    #   缓存不在就报**未验**（不是通过）—— 没验成不许当通过。
    ("refcheck", [py, "tools/refcheck.py"]),
]


def 跑(名: str, 命令: list[str], 上限: float) -> dict:
    r"""跑一项；超时必须**杀整棵进程树**，并把输出写在文件里而不是管道。

    ★ 2026-11-02 真事故：首版用 `capture_output=True` + `timeout=`，超时后
      `subprocess.run` 只杀**直接子进程**，而套件派生的孙进程还**占着 stdout 管道** ⇒
      主进程读管道永远读不到 EOF，`TimeoutExpired` 迟迟不返回（实测把 20 分钟的
      CI 拖成 40 分钟还不结束）。修法两条：输出落文件（不靠管道 EOF），
      超时按**进程树**杀（Windows: taskkill /T；POSIX: killpg）。
    """
    t0 = time.perf_counter()
    # ★ 两条硬教训（都在同一晚真机上踩到）：
    #   ① 日志落 receipts/ —— 这台机器 %TEMP% 写不进去，mkstemp 会**回落到 cwd**，
    #      把 `ci-suite-*.log` 丢进仓根；
    #   ② mkstemp 的 fd **必须立刻关**，否则 Windows 上 unlink 报
    #      `PermissionError: WinError 32`（CI 自己把自己搞崩）。
    回执目录 = 根 / "receipts"
    回执目录.mkdir(exist_ok=True)
    fd, 日志名 = tempfile.mkstemp(prefix=f"ci-{名}-", suffix=".log", dir=str(回执目录))
    os.close(fd)
    日志 = Path(日志名)
    try:
        with open(日志, "wb") as fh:
            进 = subprocess.Popen(命令, cwd=str(根), stdout=fh, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL,
                                  start_new_session=(os.name != "nt"),
                                  creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                                 if os.name == "nt" else 0))
            try:
                进.wait(timeout=上限)
                rc = 进.returncode
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(进.pid)],
                                   capture_output=True)
                else:
                    import signal
                    try:
                        os.killpg(os.getpgid(进.pid), signal.SIGKILL)
                    except OSError:
                        进.kill()
                rc = 124
        文 = 日志.read_text(encoding="utf-8", errors="replace")
        if rc == 124:
            文 += f"\n[ci] 超过 {上限:.0f}s 未结束 —— 进程树已杀（含孙进程）"
    finally:
        try:
            日志.unlink(missing_ok=True)
        except OSError:
            pass        # 杀完进程树后句柄可能还没释放；留着不影响结论，别让清理把 CI 弄崩
    行们 = 文.strip().splitlines()
    # ★ 回执必须留得住**红的那一行**：只存末 8 行时，`FAIL xxx.py` 会被后面的
    #   PASS 行挤出窗口 ⇒ 回执上只剩「37/38 绿（红 1）」却**说不出是哪支**，
    #   等于把可诊断变成不可诊断（2026-11-02 真踩：CI 红，回执里查不到谁红）。
    红行 = [x for x in 行们 if ("FAIL" in x or "未验" in x) and x not in 行们[-8:]]
    return {"项": 名, "rc": rc, "秒": round(time.perf_counter() - t0, 1),
            "判": "绿" if rc == 0 else ("未验" if rc == 3 else "红"),
            "尾巴": (红行[:10] + 行们[-8:]) if 红行 else 行们[-8:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--suite-timeout", type=float, default=900)
    a = ap.parse_args()

    环境 = dict(os.environ)
    环境.setdefault("ORCH_STATE_DIR", str(根 / ".orchestrator-state"))
    os.environ["ORCH_STATE_DIR"] = 环境["ORCH_STATE_DIR"]

    # ★ 单脚本上限要跟着套件上限走：`run_all.py` 默认**每个**脚本 180s，
    #   而 0.13.9 起 selfcheck 含真模型链（实测 180s 内跑不完，会被 run_all 杀掉 ⇒ 假红）。
    #   两个"超时"是**两层**：外层杀整个套件，内层杀单脚本，必须一起给。
    单脚本 = max(180.0, min(float(a.suite_timeout), 900.0))
    项 = list(快集) if a.fast else ([("suite", [py, "tests/run_all.py", "--timeout", f"{单脚本:.0f}"])]
                                    + 快集 + 全量额外)
    # ★★★★★ 2026-11-04：**CI 起的每个子进程都关字节码写入**（`-B` + 环境变量双保险）。
    #   这是"单跑全绿、整条套件红"那两支的真根因：
    #     · `gate_power` ④ 判「仓内容逐字节未变」，而子进程把 `tests/__pycache__/*.pyc`
    #       写进调用方的仓 ⇒ 前后指纹必然不同 ⇒ 红得像"度量过程改了仓"；
    #     · `bench_fixtures` 的夹具仓里同样落 `.pyc`：跑过变异体再恢复正确解，
    #       同秒内 mtime 相同 ⇒ Python 复用旧 `.pyc` ⇒ **正确解读到变异体**。
    #   实测：本仓当时已积了 74 个 `.pyc`（`src/.../__pycache__` 69 个），全被 .gitignore 挡着 ——
    #   **「git 忽略」≠ 不存在**，而 `gate_power` 是唯一把它抓出来的判据。
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ.pop("PYTHONPYCACHEPREFIX", None)   # 它会把字节码导去仓外，掩盖这条缺陷
    项 = [(名, ([c[0], "-B", *c[1:]] if c and str(c[0]).lower().endswith(("python.exe", "python"))
               else c)) for 名, c in 项]
    结果 = []
    for 名, 命令 in 项:
        上限 = a.suite_timeout if 名 == "suite" else 600
        print(f"[ci] {名} …", flush=True)
        r = 跑(名, 命令, 上限)
        结果.append(r)
        print(f"[ci] {名} → {r['判']}（{r['秒']}s）", flush=True)

    红 = [r for r in 结果 if r["判"] == "红"]
    未验 = [r for r in 结果 if r["判"] == "未验"]
    通过 = not 红 and (not 未验 or a.allow_unverified)

    回执 = 根 / "receipts"
    回执.mkdir(exist_ok=True)
    时间 = time.strftime("%Y%m%d-%H%M%S")
    数据 = {"时间": 时间, "模式": "fast" if a.fast else "full", "通过": 通过,
            "允许未验": bool(a.allow_unverified), "项": 结果}
    (回执 / f"ci-{时间}.json").write_text(json.dumps(数据, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    行 = [f"# CI 回执 {时间}",
          f"- 模式：{'fast' if a.fast else 'full'} · 结论：**{'通过' if 通过 else '不通过'}**",
          "", "| 项 | 判 | rc | 秒 |", "|---|---|---|---|"]
    for r in 结果:
        行.append(f"| {r['项']} | {r['判']} | {r['rc']} | {r['秒']} |")
    for r in 结果:
        if r["判"] != "绿":
            行 += ["", f"### {r['项']}（{r['判']}）", "```", *(r["尾巴"] or []), "```"]
    md = 回执 / f"ci-{时间}.md"
    md.write_text("\n".join(行) + "\n", encoding="utf-8")
    print(f"\n[ci] 结论：{'通过' if 通过 else '不通过'} · 回执 {md}")
    return 0 if 通过 else 1


if __name__ == "__main__":
    raise SystemExit(main())
