# -*- coding: utf-8 -*-
"""拆树判决探针（#34）—— 证明「删不掉」不再静默吞。

判据（口径 388 的两面：函数要测，**入口也要测**）：
  ① 占用场景（子进程以树为 cwd）⇒ `拆()` 必须返回 `删目录=False` 且 `剩余>0`；
  ② `_删结果判决(树, r)` 必须给**退出码 3**且消息含「删不掉」；
  ③ 正向对照：释放占用后同一棵树必须**真删掉**（`删目录=True`，判决 0）——
     不验这一条，我就可能把「永远返回 3」当成修好了（那是假红）；
  ④ **入口级**：子进程真跑 `python -m agentscope_orchestrator.workspace --clean <树>`：
     占用时**退出码非 0**且含「删不掉」；释放后**退出码 0**且目录真没了。

  ★ ④ 是 2026-11-19 补的：`#39` ③ 的变异「`main --clean` 无条件成功（码=0）」此前**存活**，
    因为 ①②③ 测的是 `拆()` / `_删结果判决()` 两个**函数**，`main` 的返回码没人验。
    ⇒ 判据测函数很方便，但**入口的假成功没人测** —— 这一栏就是补它。
  ★ ④ 的子进程必须 `PYTHONPATH=<包根>/src`：否则 import 到 site-packages 的**旧副本**，
    变异注在 `src/` 却测不到（"看起来验了、其实没验"）。

退出码：0 = 三条全过；1 = 有不过（回归）。
输出末尾固定一行 `判定=OK` / `判定=回归`，供交付验收用 must 匹配。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator import workspace as W  # noqa: E402

失败: list[str] = []


def 判(条件: bool, 说明: str) -> None:
    print(("   ok  " if 条件 else "   ✗   ") + 说明)
    if not 条件:
        失败.append(说明)


def _清(基: pathlib.Path) -> None:
    for p in 基.rglob("*"):
        try:
            if p.is_file():
                os.chmod(p, 0o666)
        except OSError:
            pass
    shutil.rmtree(基, ignore_errors=True)


def main() -> int:
    基 = pathlib.Path(__import__("tempfile").mkdtemp(prefix="orch-clean-探针-"))
    主 = 基 / "主仓"
    主.mkdir()

    def git(*a):
        return subprocess.run(["git", *a], cwd=str(主), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    git("init", "-q")
    (主 / "a.txt").write_text("h\n", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i")
    树 = 基 / "wt_占用"
    cp = git("worktree", "add", "-b", "orch/探针", str(树), "HEAD")
    判(cp.returncode == 0, f"worktree 建好了（rc={cp.returncode}）")

    持 = subprocess.Popen([sys.executable, "-c",
                          "import os,time; os.chdir(r'%s'); f=open('a.txt'); time.sleep(120)"
                          % 树], cwd=str(树))
    time.sleep(1.5)
    try:
        r = W.拆({"主树": str(主), "树": str(树), "分支": "orch/探针"}, 删分支=True)
        判(r.get("删目录") is False, f"占用时 删目录=False（实测 {r}）")
        判(int(r.get("剩余") or 0) > 0, f"占用时 剩余>0（实测 剩余={r.get('剩余')}）")
        码, 话 = W._删结果判决(树, r, "orch/探针")
        判(码 == 3, f"判决给**非 0**（实测 {码}）")
        判("删不掉" in 话, "判决消息含「删不掉」（不再假装已删）")
    finally:
        try:
            持.kill()
            持.wait(timeout=10)
        except Exception:                     # noqa: BLE001
            pass
    time.sleep(1.0)

    # ③ 正向对照：释放占用后必须真删掉
    r2 = W.拆({"主树": str(主), "树": str(树), "分支": "orch/探针"}, 删分支=True)
    判(r2.get("删目录") is True, f"释放后 删目录=True（实测 {r2}）")
    判(not 树.exists(), "释放后 目录真的不在了")
    码2, 话2 = W._删结果判决(树, r2, "orch/探针")
    判(码2 == 0, f"释放后判决 0（实测 {码2}）")

    # ── ④ 入口级（2026-11-19 补）：main --clean 的**返回码**必须跟着盘上真实情况走 ──
    #   为什么另建一棵：③ 已把 树 删掉，这里要"占用⇒非0 / 释放⇒0"各跑一次真进程。
    树2 = 基 / "wt_入口"
    cp2 = git("worktree", "add", "-b", "orch/探针入口", str(树2), "HEAD")
    判(cp2.returncode == 0, f"入口级夹具建好了（rc={cp2.returncode}）")
    档 = 基 / "档.yml"
    档.write_text(f"repo_root: {主.as_posix()}\n", encoding="utf-8")
    环 = dict(os.environ)
    环["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[1] / "src")  # 见头部 ★
    环["PYTHONIOENCODING"] = "utf-8"

    def 跑入口() -> tuple[int, str]:
        cp3 = subprocess.run(
            [sys.executable, "-B", "-m", "agentscope_orchestrator.workspace",
             "--clean", str(树2), "--profile", str(档)],
            cwd=str(主), capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=环, timeout=300)
        return cp3.returncode, (cp3.stdout or "") + (cp3.stderr or "")

    持2 = subprocess.Popen([sys.executable, "-c",
                           "import os,time; os.chdir(r'%s'); f=open('a.txt'); time.sleep(120)"
                           % 树2], cwd=str(树2))
    time.sleep(1.5)
    try:
        码占, 出占 = 跑入口()
        判(码占 != 0, f"入口：占用时退出码**非 0**（实测 {码占}）")
        判("删不掉" in 出占, "入口：占用时输出含「删不掉」")
    finally:
        try:
            持2.kill()
            持2.wait(timeout=10)
        except Exception:                     # noqa: BLE001
            pass
    time.sleep(1.0)
    码放, 出放 = 跑入口()
    判(码放 == 0, f"入口：释放后退出码 0（实测 {码放}；正控：不许恒定非 0）")
    判(not 树2.exists(), "入口：释放后目录真的不在了")

    # ── ⑤ 防误删护栏（2026-11-19 补）：**不是我们造的树**一律拒删 ──
    #   为什么补（#51 门区分力抓到的洞）：变异「去掉 `if not (_名像 or _标记 or _记过)`」
    #   此前在 ④ 下**存活** —— ④ 只测"占用/释放"，没测"任意路径"。
    #   而那正是 `--clean C:\Windows` 这类误删的入口。判两条：
    #   ①拒绝（非 0 + 明说「不按路径删」）②目录原封不动（正控：不许真删）。
    野 = 基 / "不是我们造的树"
    野.mkdir()
    (野 / "重要.txt").write_text("别删我", encoding="utf-8")
    cp4 = subprocess.run(
        [sys.executable, "-B", "-m", "agentscope_orchestrator.workspace",
         "--clean", str(野), "--profile", str(档)],
        cwd=str(主), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=环, timeout=300)
    出野 = (cp4.stdout or "") + (cp4.stderr or "")
    判(cp4.returncode != 0, f"护栏：任意路径退出码**非 0**（实测 {cp4.returncode}）")
    判(("不按路径删" in 出野) or ("拒绝" in 出野), "护栏：输出含「不按路径删」/「拒绝」")
    判(野.exists() and (野 / "重要.txt").exists(), "护栏：目录原封不动（正控：不许真删）")

    _清(基)
    print(f"判定={'OK' if not 失败 else '回归'}")
    if 失败:
        print("   ⇒ 不过的项：" + "；".join(失败))
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(main())
