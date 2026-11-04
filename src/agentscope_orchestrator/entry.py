"""一句话入口：**给一句话目标 + 一个工程目录，工具自己认工程、自己跑。**

═══════════════════════════════════════════════════════════════════════
产品形态（用户口径原文）
═══════════════════════════════════════════════════════════════════════
> 「我不懂，但是要做成**你能操作的**。我直接给你对话。你就能启动的，
>   然后自动调用我提供的 API 跑真实代码的。」

⇒ 使用者**不需要知道**档案、判据门、冻结区、锚点这些东西存在。
   他给两样东西：**一个工程目录** + **一句人话**。

    python -m agentscope_orchestrator.entry --project <工程目录> --goal "把 max 的错修掉"

═══════════════════════════════════════════════════════════════════════
本模块只做三件事（**不重复实现任何已有机制**）
═══════════════════════════════════════════════════════════════════════
  ① **认工程**：调 `bootstrap.确保档案()` —— 有档案就用，没有就自举
     （识别清单 → 组装判据门 → **探跑** → 写档案 → 写基线）。
  ② **定环境**：把 `ORCH_PROFILE` 指向那份档案。
     ★★★ 这一步必须在 **import `generic_goal` 之前**做 ——
       因为 `generic_goal` 在**模块顶层**就把档案读成常量：
            _prof = prof_mod.get_profile();  REPO_ROOT = _prof.repo_root
       所以"先 import 再 set_profile"是**改不动** `REPO_ROOT` 的。
       ⇒ 本模块用**延迟导入**（在设置环境变量之后才 import），
         不跟那个绑定方式较劲 —— 用现成机制，不另造一套。
  ③ **转交**：把 `--goal` 交给 `generic_goal.main()` 去跑。
     编排器的**全部**机器算子（门、收敛、真值空间、R21/R22/R23/R25）
     都在那条路上，本模块**一条都不重写**。

═══════════════════════════════════════════════════════════════════════
诚实边界
═══════════════════════════════════════════════════════════════════════
· 自举**只做能机器确定的部分**。判据门不可用（工具链没装）时，
  它**如实报告并拒绝**在档案里写那条门 —— 绝不造假门。
· 本模块**不猜语义**：目标怎么解、要改哪个文件，仍由真模型 + 判据门决定。
· 自举会**探跑**（真跑一次构建/验收）⇒ 会在工程里留下构建产物。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agentscope_orchestrator import bootstrap as boot   # noqa: E402


def 起(project: str, goal: str, argv_extra: list[str] | None = None, *,
      log=print) -> int:
    """一句话入口的**可调用**形态（自检门直接调它，不必起进程）。"""
    root = Path(project).resolve()
    if not root.is_dir():
        log(f"[入口] ★ 工程目录不存在：{root}")
        return 2
    if not goal.strip():
        log("[入口] ★ 没给目标（--goal）。一句话入口至少需要**一句话**。")
        return 2

    log("═" * 68)
    log(f"[入口] 工程：{root}")
    log(f"[入口] 目标：{goal.strip()}")
    log("═" * 68)

    档案, 回执 = boot.确保档案(root, log=log)
    if 档案 is None:
        log("[入口] ★★ 无法为该工程建立档案 ⇒ **不跑**。")
        log("[入口]    原因：" + str(回执.get("因由") or "工程不可识别"))
        log("[入口]    ⇒ 这不是「失败」，是**拒绝在没有判据的情况下动手** ——")
        log("[入口]      没有独立验收来源就改主树，正是 R21 要防的事。")
        return 3

    # ★★★ 必须在 import generic_goal **之前**设置（见模块头注②）
    os.environ["ORCH_PROFILE"] = str(档案)
    log(f"[入口] 档案：{档案}")
    log("[入口] 已把 ORCH_PROFILE 指向它（**在 import generic_goal 之前** —— "
        "否则 REPO_ROOT 会绑定成旧值）")

    import agentscope_orchestrator.generic_goal as gg   # ← 延迟导入：此处才读档案

    log(f"[入口] 装载后的仓库根：{gg.REPO_ROOT}")
    log(f"[入口] 项目验收门（独立来源）：{len(gg.prof_mod.get_profile().gates)} 条")
    if not gg.prof_mod.get_profile().gates:
        log("[入口] ★★ 档案里**没有判据门** ⇒ 主树交付将无从独立验收"
            "（只能信模型自己写的门）。建议人工补一条真能跑的验收命令。")

    argv = ["--goal", goal.strip(), *(argv_extra or [])]
    log(f"[入口] 转交 generic_goal：{' '.join(argv)}")
    return gg.main(argv)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="一句话入口：给一句话目标 + 一个工程目录，工具自己认工程并跑",
        # ★★★★★ `allow_abbrev=False` —— **必须有**。真跑抓出的缺陷：
        #   argparse 默认允许**前缀缩写**，于是 `--n 2`（`generic_goal` 的合法参数，
        #   表示窗数）被匹配成入口自己的 `--no-probe`（唯一前缀）⇒
        #   `--n` 被**悄悄吃掉**，而 `2` 变成多余位置参数。
        #   ⇒ 透传层**绝不能**开启缩写匹配：它的参数空间是**开放的**
        #     （下面 `parse_known_args` 就是为了让未知参数原样过去），
        #     而缩写匹配会让"入口自己的开关"与"下游的开关"**互相抢**。
        #   ★ 这个缺陷是自检 ⑦ 抓出来的（针是"透传里必须还有 `--n`"）。
        allow_abbrev=False)
    ap.add_argument("--project", default="", help="工程目录（不给 ⇒ 用当前目录）")
    ap.add_argument("--goal", default="", help="一句话目标")
    ap.add_argument("--selftest", action="store_true", help="跑纯机器自检门")
    ap.add_argument("--no-probe", dest="probe", action="store_false",
                    help="自举时跳过探跑（不建议：门未经验证）")
    # ★ 其余参数**原样透传**给 `generic_goal`。
    #   ★★ 真跑抓出：第一版用 `nargs=argparse.REMAINDER` —— 那**不管用**：
    #      `REMAINDER` 只在遇到**第一个位置参数**之后才开始收，
    #      而这里所有参数都是选项 ⇒ `--channel dddddd` 直接被判
    #      「unrecognized arguments」⇒ 入口连跑都跑不起来。
    #   ⇒ 正确做法是 `parse_known_args()`：**已知的自己收，未知的透传**。
    #     这是"透传"这件事的通用做法，不依赖参数写法与顺序。
    a, 透传 = ap.parse_known_args(argv)

    if a.selftest:
        return _selftest()

    if not a.project:
        a.project = str(Path.cwd())
    # ★ `--no-probe` 要**真的**传到自举那一层（否则这个开关是个摆设）。
    if a.probe:
        os.environ.pop("ORCH_BOOT_NO_PROBE", None)
    else:
        os.environ["ORCH_BOOT_NO_PROBE"] = "1"
    return 起(a.project, a.goal, 透传)


# ═══════════════════════════════════════════════════════════════════
# 纯机器自检（不碰网络、不起真跑）
# ═══════════════════════════════════════════════════════════════════
def _selftest() -> int:
    import shutil
    import tempfile

    fails: list[str] = []
    N = 17

    def 造(文件: dict[str, str]) -> Path:
        d = Path(tempfile.mkdtemp(prefix="entry_"))
        for k, v in 文件.items():
            p = d / k
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(v.encode("utf-8"))
        return d

    # ① 目录不存在 ⇒ 拒跑（不是崩）
    if 起("Z:\\肯定不存在的目录_zzz", "x", log=lambda *a, **k: None) != 2:
        fails.append("① 工程目录不存在时应拒跑（返回 2）")

    # ② 没给目标 ⇒ 拒跑
    d = 造({"Cargo.toml": "[package]\nname='x'\n"})
    try:
        if 起(str(d), "   ", log=lambda *a, **k: None) != 2:
            fails.append("② 没给目标时应拒跑（返回 2）")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ③ ★ 不可识别的工程 ⇒ 返回 3（**拒绝在没有判据的情况下动手**）
    #   ★★ 必须带 `--dry-machine`：否则**一旦这条守卫被退回**，入口会真的
    #      去调模型（真网络）⇒ 自检**挂死**而不是报红。
    #      ★ 这是自检/探针的通用纪律：**检查不得依赖网络** ——
    #        否则"这条门有没有齿"会被网络超时掩盖。
    #        （真跑抓出：入口齿探针退回这条守卫后，自检 900 秒超时。
    #          自检挂死 = 既不是绿也不是红 ⇒ 比红更坏。）
    d = 造({"readme.txt": "x\n"})
    try:
        rc = 起(str(d), "随便改点什么", ["--dry-machine"], log=lambda *a, **k: None)
        if rc != 3:
            fails.append(f"③ 不可识别的工程应返回 3（拒绝动手），得 {rc}")
        if (d / "orchestrator.profile.yml").exists():
            fails.append("③ 不可识别却写了档案")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ④ ★★★ 可识别的工程 ⇒ 档案被建立，且 `ORCH_PROFILE` 指向它
    d = 造({"Cargo.toml": "[package]\nname='x'\n",
            "src/lib.rs": "pub fn a() {}\n\n#[cfg(test)]\nmod tests {}\n"})
    try:
        旧env = os.environ.pop("ORCH_PROFILE", None)
        记 = []
        rc = 起(str(d), "把 a 修好", ["--dry-machine"],
                log=lambda *a, **k: 记.append(" ".join(str(x) for x in a)))
        # ★ 判据写**全**：不看返回码（那取决于真跑），看**档案与环境**
        档 = d / "orchestrator.profile.yml"
        if not 档.is_file():
            fails.append("④ 可识别的工程**未建立档案**")
        else:
            if os.environ.get("ORCH_PROFILE") != str(档):
                fails.append(f"④ ORCH_PROFILE 未指向档案：{os.environ.get('ORCH_PROFILE')}")
            if not (d / ".orchestrator" / "自举基线.json").is_file():
                fails.append("④ 未写自举基线（验收数字没有参照就没有意义）")
            # ★ 档案必须是**装载后仓库根 = 工程目录**（否则会改错树）
            import agentscope_orchestrator.generic_goal as gg
            if Path(gg.REPO_ROOT).resolve() != d.resolve():
                fails.append(f"④ 装载后 REPO_ROOT 不对：{gg.REPO_ROOT}")
            else:
                print("PASS ④ ★ 装载后 REPO_ROOT = 工程目录"
                      "（延迟导入生效：档案在 import 前就位）")
            # ★ 档案里必须有验收门（否则 R21 的独立来源缺失）
            门s = [g.get("name") for g in gg.prof_mod.get_profile().gates]
            if not any(str(n).startswith("验收") for n in 门s):
                fails.append(f"④ 档案里没有验收门：{门s}")
            if not 记:
                fails.append("④ 入口未输出任何日志（用户看不到它在干什么）")
        if 旧env is not None:
            os.environ["ORCH_PROFILE"] = 旧env
        else:
            os.environ.pop("ORCH_PROFILE", None)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑤ 已有档案 ⇒ 不自举、不覆盖
    #   ★ 这里查 `boot.确保档案` 而**不**再跑一次 `起()` ——
    #     因为 `generic_goal` 的档案是**模块级绑定**（`_prof = get_profile()`），
    #     **一个进程只读一次**（这是设计：它把 REPO_ROOT 等常量定死）。
    #     ⇒ 同进程内第二次 `起()` 会复用第一次的 AUDIT/REPO_ROOT，
    #       那是**探针的构造缺陷**，不是产品缺陷（真用起来一进程一趟）。
    #     本库纪律：检查必须**报告**，不能自己崩 —— 所以查机制本身。
    d = 造({"Cargo.toml": "[package]\nname='x'\n"})
    try:
        档 = d / "orchestrator.profile.yml"
        原样 = "name: 手工\nrepo_root: .\n".encode("utf-8")
        档.write_bytes(原样)
        有, 回执 = boot.确保档案(d, log=lambda *a, **k: None)
        if 回执.get("因由") != "已有档案":
            fails.append(f"⑤ 已有档案时未直接采用：{回执.get('因由')}")
        if 档.read_bytes() != 原样:
            fails.append("⑤ 已有档案被入口覆盖了（应不覆盖）")
        if str(有) != str(档):
            fails.append(f"⑤ 采用的档案路径不对：{有}")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑤b ★ 回执写盘不得把整趟跑崩（真跑抓出：审计目录被外部删掉 ⇒ FileNotFoundError）
    #   真调 `_emit`：把审计目录删掉，看它是否**补建并继续**。
    d = 造({"Cargo.toml": "[package]\nname='x'\n",
            "src/lib.rs": "pub fn a() {}\n\n#[cfg(test)]\nmod tests {}\n"})
    try:
        import agentscope_orchestrator.generic_goal as gg
        旧_RECEIPT = gg._RECEIPT
        try:
            gg._RECEIPT = d / ".orchestrator" / "generic_receipt.jsonl"
            shutil.rmtree(d / ".orchestrator", ignore_errors=True)
            r = gg.GoalRunner.__new__(gg.GoalRunner)
            r.goal = "自检"
            r.n_windows = 1
            r._tok_chars = 0
            gg.GoalRunner._emit(r, {"kind": "selftest_emit"})   # 不得抛异常
            if not gg._RECEIPT.is_file():
                fails.append("⑤b 审计目录缺失时 `_emit` **未补建目录**"
                             "（原来会 FileNotFoundError 崩掉整趟跑）")
            else:
                print("PASS ⑤b 审计目录缺失时 `_emit` 补建并继续（不再崩整趟跑）")
        finally:
            gg._RECEIPT = 旧_RECEIPT
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ⑥ ★ 入口**不得**自己实现门/收敛（那会与 generic_goal 分叉）
    #   ★★ 这一条第一版**匹配到了它自己**：针（`def _round` 等）就写在
    #      下面这几行里 ⇒ 恒红。这是本库 R17/R20 同一类缺陷的第 N 次现形。
    #      ⇒ 修法用库**自己的**机制：`gate_spec.strip_for_matching`
    #        （保位剥注释/字符串）⇒ 只在**代码本体**里找针，
    #        而针所在的字符串字面量被剥掉 ⇒ 不再自匹配。
    from agentscope_orchestrator import gate_spec as _gs
    纯代码 = _gs.strip_for_matching(Path(__file__).read_bytes()
                                  .decode("utf-8", errors="replace"))
    for 禁 in ("def _round", "def _deliver", "def _gate", "_TS20", "def _align_windows"):
        if 禁 in 纯代码:
            fails.append(f"⑥ 入口的**代码本体**里出现了编排器内部机制 {禁!r}"
                         f" ⇒ 两处实现会分叉")

    # ⑦ ★ 透传：未知参数必须**原样**到 `generic_goal`，且入口自己不得吃掉它们
    #   ★ 真跑抓出：第一版用 `nargs=REMAINDER` ⇒ `--channel dddddd` 被判
    #     「unrecognized arguments」⇒ 入口**连跑都跑不起来**。
    #   ⇒ 现在用 `parse_known_args`。这一条把那个缺陷钉住。
    _记 = []
    真起 = globals()["起"]

    def _假起(project, goal, extra=None, **kw):
        _记.append({"project": project, "goal": goal, "extra": list(extra or [])})
        return 0
    try:
        globals()["起"] = _假起
        rc = main(["--project", ".", "--goal", "把 x 修好",
                   "--channel", "dddddd", "--n", "2", "--min-pass", "2",
                   "--max-work", "4", "--dry-machine"])
        if rc != 0:
            fails.append(f"⑦ 透传场景下 main 返回 {rc}（应把返回值交回调用方）")
        if not _记:
            fails.append("⑦ main 未调到 `起()`")
        else:
            透 = _记[0]["extra"]
            for 期望 in ("--channel", "dddddd", "--n", "2", "--min-pass", "2",
                         "--max-work", "4", "--dry-machine"):
                if 期望 not in 透:
                    fails.append(f"⑦ 透传丢了 {期望!r}（实得 {透}）")
            if _记[0]["goal"] != "把 x 修好":
                fails.append(f"⑦ 目标未正确取出：{_记[0]['goal']}")
            if "--channel" in _记[0]["project"]:
                fails.append("⑦ 透传参数被误当成 --project 的值")
            else:
                print("PASS ⑦ 未知参数原样透传给 generic_goal（REMAINDER 的坑已钉住）")
    finally:
        globals()["起"] = 真起

    # ⑦b `--no-probe` 必须**真的**传到自举那一层（不是 CLI 上写写而已）
    _旧 = os.environ.pop("ORCH_BOOT_NO_PROBE", None)
    try:
        globals()["起"] = _假起
        main(["--project", ".", "--goal", "x", "--no-probe"])
        if os.environ.get("ORCH_BOOT_NO_PROBE") != "1":
            fails.append("⑦b `--no-probe` 未置 ORCH_BOOT_NO_PROBE ⇒ 自举层读不到"
                         "（那个开关是个摆设）")
        main(["--project", ".", "--goal", "x"])
        if "ORCH_BOOT_NO_PROBE" in os.environ:
            fails.append("⑦b 不带 `--no-probe` 时环境变量残留 ⇒ 下次会静默不探跑")
        # ★ 自举层真的读它吗
        import agentscope_orchestrator.bootstrap as _b
        if _b.确保档案.__defaults__ is None:
            pass
        os.environ["ORCH_BOOT_NO_PROBE"] = "1"
        _d2 = 造({"Cargo.toml": "[package]\nname='x'\n"})
        try:
            _b.确保档案(_d2, log=lambda *a, **k: None)      # 探跑开关=None ⇒ 读环境
            _文 = (_d2 / "orchestrator.profile.yml").read_text(encoding="utf-8")
            if "未探跑验证" not in _文:
                fails.append("⑦b 自举层**没读** ORCH_BOOT_NO_PROBE ⇒ "
                             "`--no-probe` 形同虚设")
            else:
                print("PASS ⑦b `--no-probe` 真的一路传到自举层（环境变量被读到）")
        finally:
            shutil.rmtree(_d2, ignore_errors=True)
    finally:
        globals()["起"] = 真起
        os.environ.pop("ORCH_BOOT_NO_PROBE", None)
        if _旧 is not None:
            os.environ["ORCH_BOOT_NO_PROBE"] = _旧

    if fails:
        print(f"entry 自检：{N - len(fails)}/{N} 红")
        for x in fails:
            print("  ✗", x)
        return 1
    print(f"entry 自检：{N}/{N} 全绿（拒跑无判据工程 · 延迟导入使 REPO_ROOT 正确 · "
          f"不自举已存在的档案 · 不重复实现编排器机制）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
