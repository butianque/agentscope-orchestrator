"""无人值守入口（命令行）—— 不依赖 MCP 服务器也能跑编排器。

## 为什么需要它（实测缺口）

编排器原状**只能在 MCP 服务器进程里跑**：`taixu_mcp._start_job` 用
`asyncio.create_task` 派发，命令行直接调用时没有运行中的事件循环 ⇒ 任务被拒：

    派发失败（no running event loop）：当前上下文无事件循环，任务未启动。
    请通过 MCP 服务器（异步工具）提交。

⇒ **没法挂后台跑一夜**，而"无人值守"的硬前提恰恰就是能挂后台。

## 修法

`taixu_mcp._ensure_loop()` / `_submit_coro()`（2026-10-13 新增）：
无运行循环时起一个**本进程的后台事件循环线程**接管派发。
与本入口配合，编排器即可在**普通进程**里无人值守运行。

★ 关键：这**不是另起一套执行机制**。同 `taixu_mcp` 的工具函数、同一套判据门、
同一套落库（jobs.db）、同一套反馈环。只是换了个事件循环宿主。

## 用法

    python -m agentscope_orchestrator.unattended --goal "自然语言目标"
    python -m agentscope_orchestrator.unattended --auto --limit 3
    python -m agentscope_orchestrator.unattended --watch        # 守护：持续拉未闭合件

## 诚实边界

· 本入口只**派发与守望**，不自己判门（判门仍是 `gate_spec` 的机器门）。
· `--watch` 的轮询间隔与最大轮数是**硬护栏**，防无人值守时无限烧资源。
· 无真模型通道时**如实报错退出**，不假装跑过。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

# 允许直接以脚本路径运行（与其它模块同口径）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentscope_orchestrator import taixu_mcp as tm  # noqa: E402


def _log(msg: str) -> None:
    print(f"[unattended {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _等任务(task_id: str, 超时秒: int = 3600, 轮询: float = 5.0) -> dict:
    """阻塞等待任务终态（后台循环线程在跑，这里只轮询落库状态）。"""
    t0 = time.time()
    while time.time() - t0 < 超时秒:
        try:
            raw = tm.task_status(task_id)
            st = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:      # noqa: BLE001
            _log(f"读状态失败（继续等）：{exc}")
            time.sleep(轮询)
            continue
        state = st.get("state")
        if state not in ("running", "queued", None):
            return st
        time.sleep(轮询)
    return {"state": "timeout", "task_id": task_id}


def _轮标签(标签: str | None, 轮: int, 序: int) -> str | None:
    """**每一件活一棵隔离树** —— 隔离树标签必须逐件唯一（★ R77）。

    ★★★★★ 真跑抓出（2026-10-17 · 第三次真项目验收）：

      【真跑证据】
        第 1 轮：`src/模块甲/mod.rs:10675` 派发 ⇒ 跑完 ⇒ 裁决 red
                ⇒ `隔离树保留：…_orch_R75_1017_0906（查看完可 --clean 删掉）`
        第 2 轮：`src/模块甲/mod.rs:10679` 派发 ⇒
                **`跑失败：隔离树错: 隔离树已存在：…_orch_R75_1017_0906`**
        第 3 轮：无可派缺口 ⇒ 停
        无人值守结束：成功 0，失败 2

      【根因】`--tag` 是**整趟一个**，而它被**原样**传给每一轮。
        第 1 轮失败时**故意保留**隔离树（留证据 —— 这是对的），
        于是第 2 轮拿着**同一个标签**去建树 ⇒ 撞上自己上一轮留下的树 ⇒
        **根本起不来**。

      ★ 后果是**结构性死锁**，而且**报表看不出来**：
        总账把第 2 轮记成「未收敛（exit 1）」，
        与"跑了但没收敛"**长得一模一样** —— 实际是"**一步都没跑**"。
      ★ 更坏：这是**一次失败之后的每一轮都失败**。
        无人值守的价值就是"失败之后继续试别的"，
        而这里**失败一次就把后续全部堵死** ⇒ 无人值守在最该工作的时候停摆。
      ★ 与 R74b（陈旧锁不自愈）**同族**：
        "上一轮留下的痕迹" 挡住了 "下一轮"，而报表把它说成别的东西。

      【修法】标签 = `{给的标签}_r{轮}_{序}`。
        · 轮次 + 序（一轮可派多件）⇒ **每件活一棵树**，互不相撞；
        · **不自动删旧树**：失败留证据是对的（要能回头看模型到底写了什么），
          删不删由人决定（`--clean`）；
        · 没给标签 ⇒ 返回 `None`，下游按时间戳自己造（本来就唯一，零改动）。
    """
    if not 标签:
        return None
    return f"{标签}_r{轮}_{序}"


def _跑无人值守(limit: int, min_pass: int, n_windows: int,
                最大轮: int, 间隔秒: int, 用缺口源: bool = False,
                最大回炉: int | None = None,
                隔离: bool = True, 保留: bool = False,
                档案: str | None = None, 通道: str = "",
                标签: str | None = None) -> int:
    """守护循环：持续拉未闭合件跑，直到没有可拉、**无进展**、或资源触底。

    ★ 护栏口径（2026-10-13 改）：原用**硬编码轮数**当护栏 ——
      但轮数上限与真实风险**无关**：跑得顺时白停，跑得糟时白烧。
      真正该停的信号是**没有进展**。这与库里口径一致：
      `TS371.cycle_is_not_monotone` 机证演化环**非单调** ⇒
      "严格递减 ⇒ 必停"前提不成立 ⇒ 不能靠数轮数论证停机。
      故改用 `guard.护栏`：无进展 / 磁盘触底 / 烧字失控 三条**资源事实**。
      `--max-rounds` 保留为**外层兜底**（防完全失控），不再是主判据。

    ★ 活源（2026-10-14 新增，实测缺口驱动）：
      原活源 = `auto_submit` → Lean 产物库未闭合声明。**实测该源为空**：
          ok=False, error=「库内已无未闭合声明（全闭合）或索引为空」
      ⇒ `--watch --auto` 立刻退出 ⇒ **无人值守在实测上不成立**。
      `用缺口源=True` 时改走 `gap_source`：扫**工程源码里项目自己登记的缺口**
      （`【未核】`/`【登记待办】`），只派「有明确工程规格」的那一类，
      需裁定 / 调查类**如实列出但不派**（派出去只会空转）。
    """
    from agentscope_orchestrator import guard as _gd
    g = _gd.护栏(Path.cwd())
    # ★★★★★ R71b（2026-10-16）：**不能保护就别假装能保护。**
    #   `--gaps`（登记缺口源）已接入隔离；`--auto`（产物库未闭合声明源）
    #   **尚未接入** —— 它在当前进程里跑 `auto_submit`，会直接改主树。
    #   ⇒ 若要求隔离（默认就是）却走了未接入隔离的源，**在入口拒绝启动**，
    #     并说清两条出路。★ 不静默降级：「声明了保护却静默不生效 =
    #     没跑看起来像绿」—— 用户会以为跑在隔离树里。
    if 隔离 and not 用缺口源:
        _log("★ 拒绝启动：`--auto`（未闭合声明源）**尚未接入隔离** ⇒ "
             "它会直接改主树，而 `--isolate` 是默认。")
        _log("  两条出路：① 改用 `--gaps`（登记缺口源，已接入隔离）；"
             "② 显式加 `--no-isolate` 并**承担直接改主树的风险**。")
        return 3
    _log(f"无人值守启动：每次拉 {limit} 件，外层兜底 {最大轮} 轮，"
         f"初始间隔 {间隔秒}s，活源={'登记缺口' if 用缺口源 else '未闭合声明'}")
    _log(f"  隔离：{'开（每个目标一棵 git worktree，绿了才回灌、主树全程不被写）' if 隔离 else '★ 关（直接改主树）'}")
    _log(f"  护栏（资源事实，非轮数）：同因重复≥{g.阈值['同因重复上限']} ⇒ 停 · "
         f"磁盘<{g.阈值['磁盘下限字节'] // 1024 // 1024}MiB ⇒ 停 · "
         f"烧字>{g.阈值['烧字上限']} ⇒ 停")
    总成功 = 总失败 = 0
    已派缺口: set[tuple[str, int]] = set()   # 本轮进程内防重复派同一处
    for 轮 in range(1, 最大轮 + 1):
        # ── 资源护栏：每条判据都机器可算 ──
        停, 因 = g.该停吗()
        if 停:
            _log(f"★ 护栏停机：{因}")
            _log(f"  摘要：{g.摘要()}")
            break

        # ── 取活 ──
        if 用缺口源:
            提交 = _取缺口活(limit, 已派缺口)
            if 提交 is None:
                _log(f"第 {轮} 轮缺口源扫描失败 ⇒ 停止")
                break
            if not 提交:
                _log(f"第 {轮} 轮无可派缺口 ⇒ 停止（全部已闭合/需裁定/调查类）")
                break
            for _序, 项 in enumerate(提交, 1):
                _log(f"第 {轮} 轮：{项['file']}:{项['line']} 派发（{项['类别']}）…")
                try:
                    # ★★★★★ R71（真事故 · 2026-10-16）：**这里原来是
                    #   `import generic_goal as gg; gg.main([...])` —— 在当前进程里
                    #   直接跑 ⇒ 完全绕过隔离 ⇒ 直接改主树。**
                    #
                    #   真事故：我用 `--gaps --isolate` 跑无人值守，日志里
                    #   **一条隔离相关记录都没有**，而主树 `src/模块甲/mod.rs`
                    #   被写进了 `【已闭合】` 与一个新测试函数（我把进程杀掉后
                    #   回滚没跑完，改动留在了**别人正在用的主树**上）。
                    #   ★ 危险在于：`--isolate` 是**默认 True**、帮助里也这么写，
                    #     而**唯一被真正隔离的只有 `--goal` 单目标那条路**。
                    #     `--gaps` / `--auto` / `--watch` —— 也就是**无人值守的
                    #     全部主路径** —— 都绕过了它。
                    #   ★ 这是 R66 那一课的**原样复发**：「主树是共享的，
                    #     我的交付物被寄存在别人的回收站里」—— 隔离就是为它做的，
                    #     却没接到最需要它的那条路上。
                    #   ⇒ 收口：**所有活都走 `_跑目标`**（它已经有建隔离树、
                    #     跨树构建缓存、绿跑回灌、红跑留证、总账记账）。
                    #     一条路，不是两条。
                    rc = _跑目标(项["目标"], 档案, 隔离, 保留, 通道,
                                 n=n_windows, min_pass=min_pass,
                                 最大回炉=最大回炉,
                                 # ★ R77：**每件活一棵隔离树**。
                                 #   原来原样传 `标签` ⇒ 第 1 轮失败保留的树
                                 #   把第 2 轮**挡在门外**（"隔离树已存在"），
                                 #   而总账把它记成"未收敛"—— 实际一步都没跑。
                                 标签=_轮标签(标签, 轮, _序))
                except Exception as exc:      # noqa: BLE001
                    _log(f"  通用目标闭环失败：{type(exc).__name__}: {exc}")
                    rc = 1
                if rc == 0:
                    总成功 += 1
                    g.记成功()
                    _log(f"  ✓ {项['file']}:{项['line']} 收敛交付")
                else:
                    总失败 += 1
                    g.记失败({"state": "error",
                              "error": f"{项['file']}:{项['line']} 未收敛"})
                    _log(f"  ✗ {项['file']}:{项['line']} 未收敛（exit {rc}）")
        else:
            try:
                raw = tm.auto_submit(limit=limit, min_pass=min_pass,
                                     n_windows=n_windows, dry=False, promote=False)
                out = json.loads(raw) if isinstance(raw, str) else raw
            except Exception as exc:      # noqa: BLE001
                _log(f"第 {轮} 轮拉件失败：{exc}")
                g.记失败({"state": "error", "error": f"拉件异常：{exc}"})
                continue
            if not out.get("ok"):
                # 无可拉件 = **正常的终止态**（已闭合），不是失败
                _log(f"第 {轮} 轮无可拉件（{out.get('error', '已闭合或索引空')}）⇒ 停止")
                break
            提交 = out.get("submitted") or []
            if not 提交:
                _log(f"第 {轮} 轮提交 0 件 ⇒ 停止")
                break
            for s in 提交:
                tid = (s.get("resp") or {}).get("task_id")
                if not tid:
                    continue
                _log(f"第 {轮} 轮：{s.get('theorem')} → {tid}，等待完成…")
                st = _等任务(tid)
                state = st.get("state")
                # ★ 烧字取真实位置（summary.tokens_chars）—— 此前取顶层，
                #   而 `task_status` 顶层**没有**这个键 ⇒ 拿到的永远是 0 ⇒
                #   护栏的"成本失控 ⇒ 停机"判据是**死的**（装饰）。已修：
                #   real_task 如实计量（含反馈环被替换掉的窗）并经 summary 透出。
                烧 = (st.get("summary") or {}).get("tokens_chars") or 0
                g.记烧字(烧)
                if 烧:
                    _log(f"  烧字 {烧}（累计 {g.累计烧字}）")
                if state in ("staged", "promoted"):
                    总成功 += 1
                    g.记成功()
                    _log(f"  ✓ {tid} {state} 通过 {st.get('summary', {}).get('passed')} 窗")
                else:
                    总失败 += 1
                    g.记失败(st)
                    _log(f"  ✗ {tid} {state}：{st.get('error') or st.get('summary')}")

        # 退避：连续失败时拉长间隔（资源事实驱动，不是固定值）
        等 = g.建议间隔() or 间隔秒
        if 等 and 轮 < 最大轮:
            if g.建议间隔():
                _log(f"  退避中：等 {等:.0f}s（连续失败 {g.连续失败}）")
            time.sleep(等)
    _log(f"无人值守结束：成功 {总成功}，失败 {总失败}")
    _log(f"  护栏摘要：{g.摘要()}")
    return 0 if 总失败 == 0 else 1


def _取缺口活(limit: int, 已派: set) -> list[dict] | None:
    """从登记缺口源取可派的活。返回 [] = 无活；None = 扫描失败。

    只取 `可派` 那一类（有明确工程规格）。需裁定 / 调查类**不派** ——
    它们需要人做选择或先查事实，派给模型只会空转（本库「候选表 99.3% 假」同型教训）。
    """
    try:
        from agentscope_orchestrator import gap_source as gs
        from agentscope_orchestrator import profile as _pf
        prof = _pf.get_profile()
        清 = gs.候选清单(Path(prof.repo_root),
                        无消费者前缀=prof.orphan_api_prefixes)
    except Exception as exc:      # noqa: BLE001
        _log(f"缺口源扫描失败：{type(exc).__name__}: {exc}")
        return None
    _log(f"  缺口源：共 {清['总登记数']} 处 · 可派 {len(清['可派'])} · "
         f"不派 {len(清['不派'])}")
    for 项 in 清["不派"]:
        _log(f"    · 不派 [{项['类别']}] {项['file']}:{项['line']} —— {项['理由']}")
    取 = []
    for 项 in 清["可派"]:
        键 = (项["file"], 项["line"])
        if 键 in 已派:
            continue
        已派.add(键)
        取.append(项)
        if len(取) >= limit:
            break
    return 取


def _子进程跑(临档: str | None, 目标: str, 通道: str, n: int, min_pass: int,
              最大回炉: int | None, 构建缓存: str | None = None) -> int:
    """在**隔离树的档案**下，用产品自己的入口跑通用目标闭环。

    ★ 为什么走子进程而不是直接 `gg.main(...)`：
      `generic_goal` 在**模块顶层**绑定档案（`REPO_ROOT = _prof.repo_root`），
      同进程换档案要靠 `reset_profile` 且任何已导入的模块都会带着旧值。
      子进程 ⇒ **档案绑定天然干净**，且退出码就是真裁决，不需要额外翻译。
      ★ 这**不是另起一套执行机制** —— 跑的还是 `generic_goal.main`。
    """
    import subprocess
    argv = [sys.executable, "-m", "agentscope_orchestrator.generic_goal",
            "--goal", 目标,
            # ★ R51 口径：空串 = 让它自己读渠道文件、全通道候选。
            #   调用方写死渠道 = 从外面把"自动 failover"掐掉。
            "--channel", 通道 or "",
            "--n", str(n), "--min-pass", str(min_pass)]
    if 最大回炉:
        argv += ["--max-work", str(最大回炉)]
    env = dict(os.environ)
    if 临档:
        env["ORCH_PROFILE"] = str(临档)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # ★★★★★ 构建缓存必须**跨隔离树存活**（2026-10-16）：
    #   隔离树是新建的 ⇒ 里面没有 `target/`（本项目主树那个 82 GB）。
    #   不指缓存 ⇒ 每次跑**冷编译**，而门的 timeout 是按**暖编译**标定的
    #   （项目自记：判据质量门 65 秒，门 timeout 900 秒）
    #   ⇒ 冷编译一超时就是**假红**：合格产出被拒，报表却像真问题。
    #   ★ 本工具一贯口径：**假红比无齿更坏**。
    if 构建缓存:
        env["CARGO_TARGET_DIR"] = str(构建缓存)
    _log(f"子进程起跑：{' '.join(argv[:3])} … （档案={临档 or '默认'}）")
    if 构建缓存:
        _log(f"  构建缓存：{构建缓存}（第一次冷、之后暖；防门 timeout 假红）")
    # 不捕获输出 ⇒ 实时继承本进程 stdio（无人值守要能在日志里看见进度）
    return int(subprocess.run(argv, env=env).returncode)


def _跑目标(目标: str, 档案: str | None, 隔离: bool, 保留: bool,
            通道: str, n: int, min_pass: int,
            最大回炉: int | None, 标签: str | None,
            静默间隔: float = 20.0, 静默最多等: float = 200.0) -> int:
    """跑一个自然语言目标 —— **默认跑在隔离树里，主树全程不被写**。

    ★ 这是「全自动形态」的关键一步（2026-10-16 实测缺口）：
      原状直接写主树。而无人值守意味着**跑一夜**，期间：
        · 用户/编辑器/别的 agent 随时可能改同一批文件；
        · 判红回滚是**常规路径** ⇒ 反复改主树又改回来；
        · 用户**没法同时用这棵树**。
      ⇒ 跑在 git worktree 里：绿了才**逐文件指纹核对**回灌（别人动过的让开），
        红了就拆掉 —— **主树自始至终没被动过**。
    """
    from agentscope_orchestrator import profile as _pf
    from agentscope_orchestrator import runlog as rl
    from agentscope_orchestrator import workspace as ws

    rid = rl.新号("goal")
    t0 = time.time()
    记录: dict = {"id": rid, "目标": 目标[:400], "模式": "goal",
                  "通道": 通道 or "(空=全通道自选)", "隔离": bool(隔离)}
    句 = None
    rc = 1
    try:
        if 隔离:
            # ★★★★★ R61 起跑闸：**主树没静下来就不起跑**。
            #   隔离只解决"我弄脏主树"，没解决"主树在动"。
            #   外部写者没写完的中间态会被快照进来 ⇒ 编排器去修一个
            #   **几秒后就不存在的缺口**（幽灵缺口），而每一环都看着在尽职。
            #   ★ 实测：主树 12:09 快照有 4 个 serde 错误，12:30 零错误。
            _log(f"起跑闸：等主树静下来（最多 {静默最多等:.0f} 秒）…")
            静 = ws.树在动(主=_pf.get_profile().repo_root,
                           间隔=静默间隔, 最多等=静默最多等)
            记录["起跑闸"] = 静
            if not 静["静"]:
                _log(f"★ 起跑闸拦下：主树在 {静['秒']:.0f} 秒内一直在变"
                     f"（动过 {静.get('动过数', 0)} 个文件：{静['动过'][:5]}）")
                _log("  ⇒ **不派活**。理由：此刻的缺口是别人没写完的中间态，"
                     "派下去就是拿模型预算去修一个几秒后就不存在的缺口。")
                记录["裁决"] = "error"
                记录["失败因"] = (f"主树持续在变（{静['秒']:.0f} 秒 / "
                                f"{静['采样次数']} 次采样）⇒ 拒绝起跑")
                return 2
            _log(f"  ✓ 主树已静（{静['秒']:.0f} 秒 / {静['采样次数']} 次采样一致）")

            句 = ws.建(档案路径=档案, 标签=标签)
            记录["隔离树"] = 句["树"]
            记录["分支"] = 句["分支"]
            记录["同步主树改动"] = {"搬": 句["同步"]["搬"],
                                    "跳过": 句["同步"]["跳过"]}
            记录["构建缓存"] = 句.get("构建缓存")
            _log(f"隔离树：{句['树']}（分支 {句['分支']}）")
            _log(f"  已同步主树未提交改动 {句['同步']['搬']} 个文件"
                 f"（跳过 {句['同步']['跳过']}）—— 跑的人看到的是真状态")

            # ★ 建树期间（数十秒）写者可能又动手 ⇒ 复核一次。
            #   不复核的话，"等静"只是一道**看起来在保护**的空门。
            复核 = ws.树在动(主=Path(句["主树"]), 间隔=静默间隔,
                             最多等=max(静默间隔 * 2, 40.0))
            记录["建树后复核"] = 复核
            if not 复核["静"]:
                _log(f"★ 建树期间主树又动了（{复核.get('动过数', 0)} 个文件）"
                     f"⇒ 快照可能已过时，拆掉重来一次")
                ws.拆(句)
                句 = None
                静2 = ws.树在动(主=_pf.get_profile().repo_root,
                               间隔=静默间隔, 最多等=静默最多等)
                记录["起跑闸二次"] = 静2
                if not 静2["静"]:
                    _log("★ 仍不静 ⇒ 不派活（如实报，不硬跑）")
                    记录["裁决"] = "error"
                    记录["失败因"] = "主树持续在变（复核后仍不静）⇒ 拒绝起跑"
                    return 2
                句 = ws.建(档案路径=档案, 标签=标签)
                记录["隔离树"] = 句["树"]
                _log(f"重建隔离树：{句['树']}")
            临档 = 句["临档"]
            缓存 = 句.get("构建缓存")
        else:
            临档 = 档案
            缓存 = None
            _log("★ 未隔离：**直接改主树**（--no-isolate；无人值守不建议）")

        rc = _子进程跑(临档, 目标, 通道, n, min_pass, 最大回炉, 构建缓存=缓存)
        记录["退出码"] = rc
        记录["裁决"] = "green" if rc == 0 else "red"
        _log(f"退出码={rc} ⇒ 裁决={'green' if rc == 0 else 'red'}")

        if 句 is not None:
            净 = ws.改动(句)
            记录["改动"] = 净
            _log(f"净改动：改{净['改']}/增{净['增']}/删{净['删']}")
            if rc == 0:
                灌 = ws.回灌(句)
                记录["回灌"] = 灌
                _log(f"回灌主树：落 {灌['落']} · 让开 {灌['让开']} · 缺 {灌['缺']}")
                if 灌["让开"]:
                    记录["失败因"] = (f"回灌时 {灌['让开']} 个文件被别人动过，"
                                     "**没覆盖**（交人处理）")
                    _log(f"  ★ 让开（别人动过，没覆盖）：{灌['让开文件'][:8]}")
                # 全落 ⇒ 隔离树已完成使命，拆掉；有让开 ⇒ 留着当证据
                要留 = 保留 or bool(灌["让开"])
            else:
                要留 = True     # 红 ⇒ 留证据（产物/回执都在里面）
            if not 要留:
                ws.拆(句)
                记录["保留隔离树"] = False
                _log("隔离树已拆（改动已全部回灌）")
            else:
                记录["保留隔离树"] = True
                _log(f"隔离树保留：{句['树']}"
                     f"（查看完可 `orch-run --clean {句['标签']}` 删掉）")
    except Exception as exc:      # noqa: BLE001
        记录["裁决"] = "error"
        记录["失败因"] = f"{type(exc).__name__}: {exc}"
        _log(f"跑失败：{type(exc).__name__}: {exc}")
        rc = 1
    finally:
        记录["秒"] = round(time.time() - t0, 1)
        try:
            p = rl.记(记录, 档案=档案)
            _log(f"总账已记：{p}（查：orch-run --history）")
        except Exception as exc:      # noqa: BLE001
            _log(f"★ 总账写入失败（不影响裁决）：{exc}")
    return rc


def _自检() -> int:
    """验「隔离 → 跑 → 回灌 → 拆」这条**接线**（打桩跑，不调模型、不联网）。

    ★ 为什么必须测接线而不是只测 `workspace`：
      `workspace` 的自检证明"隔离树本身对"，但**没人证明 `_跑目标` 真的用了它**。
      「声明了保护却静默不生效 = 没跑看起来像绿」是本库反复出现的形态
      ⇒ 这里测的是**端到端**：绿跑要回灌且拆树，红跑要**主树零改动**且留证据。
    """
    import shutil
    import subprocess
    import tempfile

    fails: list[str] = []
    N = 0

    def 断言(c: bool, s: str) -> None:
        nonlocal N
        N += 1
        if not c:
            fails.append(s)

    根 = Path(tempfile.mkdtemp(prefix="orch_un_"))
    旧档 = os.environ.get("ORCH_PROFILE")
    真跑 = globals()["_子进程跑"]
    try:
        # ── ★ R58 齿：前缀缩写必须关掉 ──
        #   `--n` 同时是 `--n-windows` 与 `--no-isolate` 的前缀 ⇒ 必须**报错**，
        #   不许被猜成其中一个。★ 无人值守时没人盯着，参数被悄悄解释成
        #   另一个意思 = 跑出来的东西不是你要的。
        import contextlib
        import io
        被拒 = False
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                main(["--n", "2"])
        except SystemExit:
            被拒 = True
        断言(被拒, "R58：`--n` 没被拒（前缀缩写还开着 ⇒ 可能静默猜成别的选项）")

        仓 = 根 / "repo"
        仓.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(仓), check=True)
        (仓 / "a.txt").write_bytes(b"A\n")
        subprocess.run(["git", "add", "-A"], cwd=str(仓), check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "-m", "i"], cwd=str(仓), check=True)
        档 = 仓 / "orch.yml"
        档.write_text("name: t\nrepo_root: .\naudit_dir: .audit\n",
                      encoding="utf-8")
        os.environ["ORCH_PROFILE"] = str(档)
        from agentscope_orchestrator import profile as _pf
        from agentscope_orchestrator import runlog as _rl
        _pf.reset_profile(str(档))

        跑过的树: list[Path] = []

        def _打桩(码: int):
            def 假(临档, 目标, 通道, n, min_pass, 最大回炉, 构建缓存=None):
                树 = Path(临档).parent          # 临档在隔离树根 ⇒ 这就是隔离树
                跑过的树.append(树)
                断言(bool(构建缓存), "★ 跑：构建缓存没传给子进程"
                                     "（隔离会冷编译 ⇒ 门 timeout 变假红）")
                (树 / "a.txt").write_bytes(b"A-fixed\n")   # 模拟"跑"出的改动
                return 码
            return 假

        # ── ★ R61 齿：主树在动 ⇒ 起跑闸必须**拒绝派活** ──
        #   幽灵缺口：外部写者没写完的中间态被快照进来 ⇒ 编排器去修一个
        #   几秒后就不存在的缺口，而每一环都看着在尽职。
        #   ★ 真跑才抓出来（12:09 快照 4 个 serde 错误 / 12:30 主树零错误）。
        动过 = 仓 / "moving.txt"
        动过.write_bytes(b"0\n")
        import threading
        import time as _t
        停 = threading.Event()

        def 一直写():
            i = 1
            while not 停.is_set():
                动过.write_bytes(f"{i}\n".encode())
                i += 1
                _t.sleep(0.05)

        线 = threading.Thread(target=一直写, daemon=True)
        线.start()
        _t.sleep(0.2)
        try:
            globals()["_子进程跑"] = _打桩(0)
            rc3 = _跑目标("动跑", str(档), True, False, "", 1, 1, None, "stmove",
                          静默间隔=0.3, 静默最多等=2.0)
        finally:
            停.set()
            线.join(timeout=3)
        断言(rc3 == 2, f"★★ R61：主树在动时**没拦下**（退出码 {rc3}）"
                       "⇒ 会拿预算去修幽灵缺口")
        断言(not 跑过的树, "★★ R61：拦下了却还是把活派出去了（打桩被调用过）")
        h = _rl.查(档案=str(档), 条数=20)
        末 = h["记录"][-1] if h["记录"] else {}
        断言(末.get("裁决") == "error" and "变" in str(末.get("失败因", "")),
             f"★ R61：拒绝起跑必须**如实记账**（实际 {末.get('裁决')} / "
             f"{末.get('失败因')}）")

        # ── 绿跑：必须回灌主树，且全落 ⇒ 隔离树拆掉 ──
        (仓 / "moving.txt").unlink()
        globals()["_子进程跑"] = _打桩(0)
        rc = _跑目标("绿跑", str(档), True, False, "", 1, 1, None, "stgreen",
                     静默间隔=0.3, 静默最多等=3.0)
        断言(rc == 0, f"绿跑：退出码应为 0（实际 {rc}）")
        断言((仓 / "a.txt").read_bytes() == b"A-fixed\n",
             "★★ 绿跑：改动**没回灌主树**（隔离成了黑洞）")
        断言(跑过的树 and not 跑过的树[0].exists(),
             "★ 绿且全落：隔离树应已拆（否则跑一夜会堆一地树）")

        # ── 红跑：主树必须零改动，隔离树留着当证据 ──
        (仓 / "a.txt").write_bytes(b"A2\n")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "-am", "2"], cwd=str(仓), check=True)
        跑过的树.clear()
        globals()["_子进程跑"] = _打桩(1)
        rc2 = _跑目标("红跑", str(档), True, False, "", 1, 1, None, "stred",
                      静默间隔=0.3, 静默最多等=3.0)
        断言(rc2 == 1, f"红跑：退出码应为 1（实际 {rc2}）")
        断言((仓 / "a.txt").read_bytes() == b"A2\n",
             "★★★ 红跑：**主树被改了**（无人值守跑一夜 = 把用户树弄脏）")
        断言(跑过的树 and 跑过的树[0].exists(),
             "★ 红跑：隔离树应保留当证据")
        # 收尾：把红跑留下的树拆掉
        if 跑过的树 and 跑过的树[0].exists():
            from agentscope_orchestrator import workspace as _ws
            _ws.拆({"主树": str(仓), "树": str(跑过的树[0]),
                    "分支": "orch/stred"})

        # ── 总账：三次跑都必须留记录，且能被查到 ──
        h = _rl.查(档案=str(档), 条数=10)
        断言(h["总行"] == 3, f"总账：应有 3 条（实际 {h['总行']}）")
        裁决 = [r.get("裁决") for r in h["记录"]]
        断言(裁决 == ["error", "green", "red"], f"总账：裁决序列不对 {裁决}")
        断言(all(r.get("隔离树") for r in h["记录"][1:]),
             "总账：缺隔离树字段（跑了却查不到跑在哪）")
        断言(h["记录"][1].get("回灌", {}).get("落") == 1,
             "总账：绿跑的回灌计数没记")
        断言("起跑闸" in h["记录"][1] and h["记录"][1]["起跑闸"].get("静") is True,
             "总账：没记起跑闸的静默事实（跑完查不到'当时树静没静'）")
    finally:
        globals()["_子进程跑"] = 真跑
        if 旧档 is None:
            os.environ.pop("ORCH_PROFILE", None)
        else:
            os.environ["ORCH_PROFILE"] = 旧档
        shutil.rmtree(根, ignore_errors=True)

    # ════════════════════════════════════════════════════════════
    # 【R71】无人值守的**全部主路径**都必须走隔离（真事故 · 2026-10-16）
    # ════════════════════════════════════════════════════════════
    # 真事故：`--gaps --isolate` 跑起来后，日志里**一条隔离记录都没有**，
    # 而主树 `src/模块甲/mod.rs` 被写进了 `【已闭合】` + 一个新测试函数
    # （进程被杀 ⇒ 回滚没跑完 ⇒ 改动留在了**别人正在用的主树**上）。
    # ★ 危险在于 `--isolate` 是**默认 True**、帮助里也这么写，
    #   而唯一被真正隔离的只有 `--goal` 单目标那条路。
    # ★ 这是 R66 那一课的**原样复发**：隔离就是为「主树是共享的」做的，
    #   却没接到最需要它的那条路上。
    # ⇒ 齿：**机器读自己的源码**，断言缺口分支走 `_跑目标`、且**不再**有
    #   "在当前进程里直接跑 generic_goal" 那种写法。
    try:
        _源71 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _头71, _尾71 = _源71.split("def _跑无人值守(", 1)
        _体71 = _尾71.split("\ndef ", 1)[0]
        # ★★ 必须先**剥注释**再查 —— 否则齿会匹配到它自己：
        #   我在上面的注释里**引用了旧写法**（为了说清根因），
        #   于是 `gg.main(` 出现在注释里 ⇒ 齿误判成"代码里还在用"。
        #   ★ 这是本库已有的规矩：**门不得匹配到它自己**。
        #   （第一版就是这么红的，如实登记。）
        _码71 = "\n".join(
            _l.split("#", 1)[0] for _l in _体71.splitlines())
        断言("_跑目标(" in _码71,
             "【R71】无人值守里**没有**走 `_跑目标` ⇒ 隔离不生效（真事故）")
        断言("gg.main(" not in _码71 and "generic_goal as gg" not in _码71,
             "【R71】无人值守里仍在**当前进程**直接跑 generic_goal "
             "⇒ 绕过隔离、直接改主树（真事故）")
        # ★ 反向齿：**不能保护就别假装** —— 未接入隔离的源必须被拦下
        断言("拒绝启动" in _体71,
             "【R71b】未接入隔离的活源**没有**被拦下 ⇒ 会静默直接改主树")
    except Exception as _e71:
        断言(False, f"【R71】结构齿自身异常：{type(_e71).__name__}: {_e71}")

    # ★ 行为齿：`--auto`（未接入隔离的源）+ 默认隔离 ⇒ 必须**拒绝启动**（退出码 3）
    try:
        _rc71 = _跑无人值守(1, 1, 1, 1, 0, 用缺口源=False, 隔离=True)
        断言(_rc71 == 3,
             f"【R71b】`--auto` 未接入隔离时必须拒绝启动（退出码 3），实得 {_rc71}")
        # ★ 反向齿：显式关掉隔离 ⇒ 允许（那是用户自担风险的选择）
        _rc71b = _跑无人值守(1, 1, 1, 0, 0, 用缺口源=False, 隔离=False)
        断言(_rc71b != 3,
             f"【R71b】显式 `--no-isolate` 时不该再拒绝（实得 {_rc71b}）")
    except Exception as _e71b:
        断言(False, f"【R71b】行为齿自身异常：{type(_e71b).__name__}: {_e71b}")

    # ════════════════════════════════════════════════════════════════
    # 【R77】隔离树标签必须**逐件唯一**（真跑抓出 · 2026-10-17）
    # ════════════════════════════════════════════════════════════════
    # 真跑证据：第 1 轮 red ⇒ 隔离树**保留**（留证据，对的）；
    #   第 2 轮拿**同一个标签**建树 ⇒ `隔离树已存在` ⇒ **一步都没跑**，
    #   而总账把它记成"未收敛（exit 1）"—— 与"跑了但没收敛"长得一样。
    try:
        # ① 语义齿：同一标签、不同轮/序 ⇒ **必须不同**
        _a77 = _轮标签("T", 1, 1)
        _b77 = _轮标签("T", 2, 1)
        _c77 = _轮标签("T", 1, 2)
        断言(len({_a77, _b77, _c77}) == 3,
             f"【R77】★ 不同轮/序拿到了**同一个**标签（{_a77} / {_b77} / {_c77}）"
             f" ⇒ 上一轮失败保留的树会把下一轮挡在门外")
        # ② 没给标签 ⇒ 交回 None（下游按时间戳造，本来就唯一；零改动）
        断言(_轮标签(None, 1, 1) is None,
             "【R77】没给标签时不该硬造一个 ⇒ 下游的时间戳默认被顶掉了")
        # ③ **接线齿**：光有函数不算 —— 必须真的被用在派发处。
        #   ★ R71/R74 那一课：写好了不等于接上了。
        _源77 = Path(__file__).read_text(encoding="utf-8", errors="replace")
        _码77 = "\n".join(_l.split("#", 1)[0] for _l in _源77.splitlines())
        断言("标签=_轮标签(标签, 轮, _序)" in _码77,
             "【R77】★ `_轮标签` **没有接到派发处** ⇒ 标签仍是整趟一个"
             " ⇒ 第 1 轮失败就把后续每一轮都堵死")
        断言("for _序, 项 in enumerate(提交, 1):" in _码77,
             "【R77】派发循环没有序号 ⇒ 一轮派多件时标签会撞")
    except Exception as _e77:
        断言(False, f"【R77】标签唯一性齿自身异常：{type(_e77).__name__}: {_e77}")

    if fails:
        print(f"unattended 自检：{N - len(fails)}/{N} —— 红 {len(fails)}")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print(f"unattended 自检：{N}/{N} 全绿")
    return 0


def main(argv=None) -> int:
    # ★ R548：`orch-run` 也 import `taixu_mcp`，所以状态根同样只在**进程入口**设。
    import os as _os548
    if not _os548.environ.get("ORCH_STATE_DIR"):
        _os548.environ["ORCH_STATE_DIR"] = str(tm.STATE)
    ap = argparse.ArgumentParser(
        description="示例编排器 · 无人值守入口（不依赖 MCP 服务器）",
        # ★★★★★ R58（真跑抓出 · 2026-10-16）：关掉**前缀缩写**。
        #   【现象】真起跑立刻死在参数解析：
        #       `error: ambiguous option: --n could match --n-windows, --no-isolate`
        #   【根因】argparse 默认允许前缀缩写。加了 `--no-isolate` 之后，
        #     原来能用的 `--n` 同时命中 `--n-windows` 与 `--no-isolate` ⇒ 歧义。
        #   ★ 危险的不是"报错"，而是**它可能不报错**：若只有一个候选，
        #     `--n` 会**静默变成别的选项**。无人值守场景下没人盯着，
        #     参数被悄悄解释成另一个意思 = 跑出来的东西不是你要的。
        #   ⇒ 前缀缩写是**猜意图**，产品不该猜。关掉。
        allow_abbrev=False)
    ap.add_argument("--goal", help="自然语言目标（走通用目标闭环 generic_goal）")
    ap.add_argument("--auto", action="store_true",
                    help="自动拉未闭合声明（走 auto_submit）")
    ap.add_argument("--watch", action="store_true",
                    help="守护模式：持续拉件跑，直到无可拉（配合 --auto）")
    ap.add_argument("--gaps", action="store_true",
                    help="★ 活源改用「登记缺口」：扫工程源码里项目自己登记的 "
                         "【未核】/【登记待办】，只派有明确工程规格的（需裁定/调查类不派）")
    ap.add_argument("--list-gaps", action="store_true",
                    help="只列出登记缺口（可派/不派两份），不派发")
    ap.add_argument("--limit", type=int, default=1, help="每轮拉件数（1..5）")
    ap.add_argument("--min-pass", type=int, default=2, help="通过窗数下限")
    ap.add_argument("--n-windows", type=int, default=3, help="并发窗数")
    ap.add_argument("--max-rounds", type=int, default=20,
                    help="外层兜底轮数（防完全失控；主护栏是资源事实，见 guard）")
    ap.add_argument("--interval", type=int, default=10, help="轮间基础间隔秒（失败时自动退避）")
    ap.add_argument("--max-work", type=int, default=None,
                    help="★ 每件活的**内部**回炉轮数上限（透传给 generic_goal）。"
                         "不填=用其默认。用于控制单件活的最大成本。")
    ap.add_argument("--profile", help="档案文件（换工程用，需进程内首次使用前指定）")
    # ── 隔离（2026-10-16 新增：无人值守的硬前提）──
    ap.add_argument("--isolate", dest="isolate", action="store_true", default=True,
                    help="★ 默认：跑在 git worktree 里，绿了才回灌，主树全程不被写")
    ap.add_argument("--no-isolate", dest="isolate", action="store_false",
                    help="直接改主树（**不推荐**；仅调试用）")
    ap.add_argument("--keep", action="store_true", help="跑完保留隔离树（不自动拆）")
    ap.add_argument("--tag", help="隔离树标签（默认时间戳）")
    ap.add_argument("--clean", metavar="标签", help="删掉某个隔离树")
    ap.add_argument("--list-trees", action="store_true", help="列出现存隔离树")
    # ── 起跑闸（R61）──
    ap.add_argument("--quiet-gap", type=float, default=20.0,
                    help="★ 起跑闸采样间隔秒（默认 20）：连续两次采样一致才算"
                         "主树静下来了")
    ap.add_argument("--quiet-wait", type=float, default=200.0,
                    help="★ 起跑闸最多等多少秒（默认 200）；等不到就**拒绝起跑**，"
                         "绝不拿模型预算去修一个别人没写完的中间态")
    # ── 总账（回执历史）──
    ap.add_argument("--history", action="store_true", help="★ 查无人值守跑的历史总账")
    ap.add_argument("--verdict", choices=["green", "red", "error"], help="只看某裁决")
    ap.add_argument("--grep", help="只看目标含该子串的")
    ap.add_argument("-n", "--history-limit", type=int, default=20)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _自检()

    if a.history or a.verdict or a.grep:
        from agentscope_orchestrator import runlog as rl
        return rl.main(["--profile", a.profile] if a.profile else [] +
                       (["--verdict", a.verdict] if a.verdict else []) +
                       (["--grep", a.grep] if a.grep else []) +
                       ["-n", str(a.history_limit)])

    if a.list_trees or a.clean:
        from agentscope_orchestrator import workspace as ws
        return ws.main(["--profile", a.profile] if a.profile else [] +
                       (["--list"] if a.list_trees else ["--clean", a.clean]))

    if a.profile:
        os.environ["ORCH_PROFILE"] = a.profile
        # ★ 换工程：进程内重建档案与全部索引（不再需要重启进程）
        try:
            info = tm.rebuild_indexes(a.profile)
            _log(f"档案切换：{info['旧档案']} → {info['新档案']}"
                 f"（源码 {info['源码单元数']} 单元 · 产物库 {info['产物库单元数']} 单元"
                 f" · repo_root={info['repo_root']}）")
        except Exception as exc:      # noqa: BLE001
            _log(f"档案切换失败：{type(exc).__name__}: {exc}")
            return 2

    # ★ 关键：确保事件循环可用（无 MCP 服务器时走后台循环线程）
    try:
        loop = tm._ensure_loop()
        _log(f"事件循环就绪：{'本线程' if not loop.is_running() else '后台线程'}"
             f"（无 MCP 服务器亦可跑）")
    except Exception as exc:      # noqa: BLE001
        _log(f"起事件循环失败：{exc}")
        return 2

    if a.list_gaps:
        from agentscope_orchestrator import gap_source as gs
        from agentscope_orchestrator import profile as _pf
        # ★ 实测抓到：`repo_root` 是 `WindowsPath`，直接塞进 argv 会让 argparse
        #   在 `arg_string[0]` 处抛 `TypeError: 'WindowsPath' object is not
        #   subscriptable`。必须显式转 str。
        _prof = _pf.get_profile()
        _argv = ["--root", str(_prof.repo_root)]
        # ★ R85：把**项目自己声明的**接口约定传下去 —— 否则 `--list-gaps`
        #   只看得到「人写下来的」缺口，看不到结构缺口。
        for _q in (_prof.orphan_api_prefixes or ()):
            _argv += ["--orphan-prefix", _q]
        return gs._cli(_argv)

    if a.watch or a.auto or a.gaps:
        return _跑无人值守(max(1, min(a.limit, 5)), a.min_pass,
                            a.n_windows, max(1, a.max_rounds), max(0, a.interval),
                            用缺口源=bool(a.gaps), 最大回炉=a.max_work,
                            隔离=a.isolate, 保留=a.keep, 档案=a.profile,
                            通道="", 标签=a.tag)

    if a.goal:
        _log(f"通用目标闭环：{a.goal[:80]}")
        return _跑目标(a.goal, a.profile, a.isolate, a.keep,
                      通道="", n=max(1, a.n_windows), min_pass=a.min_pass,
                      最大回炉=a.max_work, 标签=a.tag,
                      静默间隔=max(1.0, a.quiet_gap),
                      静默最多等=max(0.0, a.quiet_wait))

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
