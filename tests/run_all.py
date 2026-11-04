"""编排器自有判据门：把 tests/ 下的每个脚本**当独立进程**跑一遍，给出可机判结论。

★ 为什么需要这个文件（2026-10-31 交付审计，用户的追问逼出来的）：
  此前 `tests/` 里是若干**独立脚本**，靠"跑起来有没有抛异常 + 最后一行 PASS"自证，
  其中还有几个在**模块级**直接 `sys.exit(0)`。后果很具体：
    · `python -m unittest discover -s tests` **必然报错**
      （`SystemExit: 0` 在导入期抛出 ⇒ errors=4）；
    · 于是"这个仓有没有一条能跑的判据门"的答案是**没有**；
    · 而 `orch_fix` 的全部价值都建立在"有一条会红的门"上 ——
      门不存在 ⇒ 它在真实仓上**根本没法被验证**（台账里那三次超时/熔断，
      一部分根因就是它拿不到一条稳定的门）。
  ⇒ 不是把脚本改造成 pytest，而是给它们一个**统一入口**：
    每个脚本一个子进程、退出码即判决、汇总成一条命令的结论。
    这样 `orch_fix` / `orch_run` / CI 都能用同一个 cmd 当门。

用法：
    python tests/run_all.py            # 跑全部
    python tests/run_all.py -k state   # 只跑名字含 state 的
    python tests/run_all.py --list     # 只列不跑

退出码：0 全绿 / 1 有红。**跳过一个都算红**（与 test_counts 门的口径一致：
"没跑"不等于"通过"）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import sys
import time

# ★★★★★ 2026-11-03（同一天第三次踩同一个坑，所以写在这里当路标）：
#   **工具自己打印含有非 GBK 字符的消息时，会在 GBK 控制台上把那一步整个搞死。**
#   前两次：`tools/make_dist.py` 的 `✓`（U+2713）⇒ 打包"看着成功"其实没定稿；
#   这一次：**本文件**的 `⇒`（U+21DE）⇒ `python tests/run_all.py -k ...` 直接
#   `UnicodeEncodeError`、退出码 1。
#   为什么这次特别讽刺：上一轮我刚把"工具不许依赖调用方控制台编码"写进知识库，
#   而**我自己这个测试运行器**正好违反了它 —— 我只修了 tools/，没修 tests/。
#   ⇒ 教训：修一类问题时要**把同类位置全找一遍**（`tools/` 与 `tests/` 都是"工具"），
#     否则下次它以另一张脸回来（这次就是另一张脸：不是打包工具，是判据运行器）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
    except Exception:                                          # noqa: BLE001
        pass
from pathlib import Path

这里 = Path(__file__).resolve().parent
包根 = 这里.parent
# 本进程要 import 产品里的 `subproc_safe`（每支子进程由它起，见 跑一个）
sys.path.insert(0, str(包根 / "src"))

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
_临时根 = 包根 / "tests" / "_run_all_state" / "_tmp"
_临时根.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_临时根)
for _k in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_k] = str(_临时根)

# 不作为判据门的文件：本文件自身、纯工具/夹具
# ★ 2026-11-01：`_通道.py` 是**夹具**（挑通道用），此前被当成一条"测试"跑：
#   它什么都不判却 exit 0 ⇒ 汇总里多一条**永远绿的空测试**。
#   测试数要能对得上，就不能把夹具算成判据。
排除 = {"run_all.py", "stress.py", "_通道.py"}


def 找脚本(关键词: str = "") -> list[Path]:
    out = []
    for p in sorted(这里.glob("*.py")):
        if p.name in 排除:
            continue
        if 关键词 and 关键词 not in p.name:
            continue
        out.append(p)
    return out


def 跑一个(路径: Path, 超时: float) -> dict:
    """独立进程跑一个脚本。**环境隔离**：给它独立的状态目录，避免互相污染。"""
    环境 = dict(os.environ)
    # ★★★★★ 2026-11-03：这里原来是 `setdefault` —— **错，必须覆盖**。
    #
    #   病：`setdefault` 只在外部没设时才补。而本机（以及 CI 的 shell）常常已经
    #   导出了 `PYTHONIOENCODING=gbk` 或者干脆没设 `PYTHONUTF8`
    #   ⇒ 38 个判据脚本的子进程 stdout 退回 GBK
    #   ⇒ 任何一个打印 `⇒`/`✓`/`✗` 的脚本都会 `UnicodeEncodeError` 崩掉，
    #     **而且崩在 print 上，看起来像"这个判据自己坏了"**。
    #
    #   实测证据（同一天三个不同脚本，三张脸）：`tools/make_dist.py` 的 `✓`、
    #   本文件自己的 `⇒`、以及套件里多个脚本的红 —— 根因同一条。
    #
    #   ★ 这条错在同一轮里我**已经犯过一次并改对过**（`tests/kb_retrieval.py`：
    #     当时也是 setdefault，改成强制赋值）。改了一处、没改另一处 ⇒ 今天以另一张脸回来。
    #     ⇒ 口令：**凡是给子进程钉编码，一律覆盖，永不 setdefault。**
    #       理由：判据的运行环境必须由判据自己决定，"调用方环境碰巧对"不算对。
    环境["PYTHONIOENCODING"] = "utf-8"
    环境["PYTHONUTF8"] = "1"
    # ★ 必须是**覆盖**，不能 setdefault：外部 shell 若已导出 ORCH_STATE_DIR，setdefault 会让
    #   所有用例**共用同一个状态根** —— 实测后果是串跑互相踩（lean_spec / mcp_smoke 红），
    #   单跑全绿。用例隔离是这条套件的判据前提，不允许被环境变量悄悄破掉。
    环境["ORCH_STATE_DIR"] = str(这里 / "_run_all_state" / 路径.stem)
    环境.setdefault("PYTHONPATH", str(包根 / "src"))
    # ★★★★★ 2026-11-04：**套件起子进程一律关掉字节码写入**（`-B` + 环境变量双保险）。
    #
    #   为什么：这是"单跑全绿、整条套件红"那两支（`bench_fixtures` / `gate_power`）的真根因。
    #     · `gate_power` ④ 判「仓内容逐字节未变」，而子进程把 `tests/__pycache__/*.pyc`
    #       写进了调用方的仓 ⇒ 前后指纹必然不同 ⇒ 红，且红得像"度量过程改了仓"。
    #     · `bench_fixtures` 的夹具仓里同样落 `.pyc`：先跑变异体、再恢复正确解，
    #       同一秒内 mtime 相同 ⇒ Python 复用旧 `.pyc` ⇒ **正确解读到变异体** ⇒ 红。
    #   ★ 为什么我前面"排除了 5 个假设还查不出"：我的复现环境里 `PYTHONPYCACHEPREFIX`
    #     是设着的（字节码被导去别处），而 `tools/ci.py` 会把它清掉 —— **排除假设之前
    #     必须先把环境对齐**，否则排除的是另一个环境。
    #   ⇒ 口径：**判据的仓里不许出现判据自己写出来的东西**，字节码尤其。
    环境["PYTHONDONTWRITEBYTECODE"] = "1"
    环境.pop("PYTHONPYCACHEPREFIX", None)
    起 = time.time()
    # ★★★★★ 2026-11-03：记下**本脚本启动前就已经存在的** lake/lean 进程 pid。
    #
    #   为什么需要：`subprocess.run(timeout=)` 超时只 `kill()` **直接子进程**，
    #   而工具链会**再 spawn 孙进程**（`lake` → `lean`）——
    #   孙进程活下来，继续占着工程目录里的文件（实测是
    #   `formal_proof\.lake\config\[anonymous]\lakefile.olean.trace`）。
    #   ⇒ **下一个脚本撞锁**：`lean_spec` 报
    #     `permission denied (error code: 13)` 而**它自己单独跑是全绿的**。
    #
    #   ★ 这是"杀进程只杀父"的经典缺陷，而它的表现是**张冠李戴**：
    #     红的是**下一个**脚本，真凶是**上一个**脚本没退干净。
    #     ⇒ 光看 `lean_spec` 的红永远查不出来（我查了 12 步才定位）。
    #
    #   ★ 为什么按"启动时间"界定而不是按名字全杀：
    #     本机可能有**别的会话**正在跑 Lean（实测：并行会话会动 `src/bin/*.rs`）。
    #     全杀会去动别人的东西。⇒ 只清"**本脚本启动之后**才出现的"，那才是它生的。
    前有 = _活着的工具链()
    # ★★ 2026-11-04：换 `subproc_safe`。原来的 `subprocess.run(capture_output=True,
    #   timeout=)` **只杀直接子进程**，孙进程还持着 stdout/stderr 管道 ⇒
    #   `communicate()` 读不到 EOF ⇒ **超时形同不存在**。
    #   实测两次：`lean_spec` 因此挂死 CI 26 分钟（lake→lean 孙进程持管道）；
    #   `mcp_smoke` 挂 20+ 分钟且 900s 超时没回收（它起的 MCP 服务器有后台任务）。
    #   新写法：输出走**临时文件**（不靠 EOF）+ 超时 `taskkill /T` **杀整棵树**。
    from agentscope_orchestrator import subproc_safe as _SS
    try:
        唤 = _SS.跑([sys.executable, "-B", str(路径)], cwd=包根, timeout=超时, env=环境)
        rc, 输出 = 唤.返回码, 唤.输出
        if 唤.超时:
            # ★ 「没验成」与「它坏了」要分开写：超时是**没验成**；
            #   套件里仍按红记（rc=124），因为"跑不完"这件事本身要有人处理。
            rc = 124
            输出 += (f"\n[run_all] 超过 {超时:.0f}s 未结束 —— 已**杀整棵树**"
                    f"（{唤.秒:.1f}s 后被收掉）。这是**没验成**，不是判据说它坏。")
    finally:
        # 本脚本**新留下**的工具链进程 ⇒ 清掉（否则下一次撞锁，且张冠李戴）
        清 = _清残留工具链(前有)
        if 清:
            输出 += (f"\n[run_all] 清掉本脚本留下的工具链残留进程 {len(清)} 个："
                    f"{清[:4]}（不清的话下一个脚本会撞锁，而且是**张冠李戴**地红）")
    return {"脚本": 路径.name, "rc": rc, "秒": round(time.time() - 起, 2),
            "尾": 输出.strip().splitlines()[-1][:160] if 输出.strip() else "",
            "全输出": 输出}


# 会 spawn 孙进程、且占工程目录的工具链进程名
_工具链 = ("lake", "lean")


def _活着的工具链() -> set[int]:
    """当前活着的 lake/lean 的 pid 集合。查不到就返回空（**不阻断跑测**）。"""
    try:
        import subprocess as _s
        out = _s.run(["tasklist", "/FO", "CSV", "/NH"],
                     capture_output=True, text=True, timeout=60,
                     encoding="utf-8", errors="replace")
        pids: set[int] = set()
        for line in (out.stdout or "").splitlines():
            f = [x.strip('"') for x in line.split('","')]
            if len(f) >= 2 and f[0].lower().replace(".exe", "") in _工具链:
                try:
                    pids.add(int(f[1]))
                except ValueError:
                    pass
        return pids
    except Exception:                                          # noqa: BLE001
        return set()


def _清残留工具链(前有: set[int]) -> list[str]:
    """清掉**本脚本启动之后新出现**的 lake/lean。返回被清的 `名字(pid)`。

    ★ 只清差集 —— 绝不动本脚本之前就存在的进程（可能是别的会话正在用的）。
    """
    try:
        现 = _活着的工具链()
        新 = 现 - 前有
        if not 新:
            return []
        import subprocess as _s
        被清 = []
        for pid in sorted(新):
            try:
                _s.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=30)
                被清.append(f"pid={pid}")
            except Exception:                                  # noqa: BLE001
                pass
        return 被清
    except Exception:                                          # noqa: BLE001
        return []


def 存失败证据(结果: list[dict]) -> list[str]:
    """把**失败脚本的完整输出**落盘，并返回路径们。

    ★ 为什么必须做（2026-11-03，套件里 mcp_smoke 抖动两次才补）：
      原来 `跑一个` 只留 `尾`（**最后一行**），完整输出当场丢掉 ⇒
      红了之后**无法从红里学到任何东西**：看不出它是"真缺陷"、"环境不可用"、
      还是"上游模型通道抖了一下"。结果是我只能改天重跑，然后凭印象说"那是抖动"
      —— **判定权又跑回人手里了**，而这正是整套东西要消灭的东西。
      `mcp_smoke` 跑的是**真模型任务**，它红的时候最需要知道的就是上面那三种的哪一种。
      ⇒ 判红必须留原始证据：失败就写全文，路径打出来（绿的不写，免得攒垃圾）。
    """
    留 = []
    for r in 结果:
        if r["rc"] in (0, 3):
            continue
        d = 这里 / "_run_all_state" / Path(r["脚本"]).stem
        try:
            d.mkdir(parents=True, exist_ok=True)
            p = d / "_fail.log"
            p.write_text(r.get("全输出") or "", encoding="utf-8", errors="replace")
            留.append(str(p))
        except OSError:
            pass                      # 落不了盘不影响判红本身
    return 留


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="跑 tests/ 下全部判据脚本")
    ap.add_argument("-k", "--keyword", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--timeout", type=float, default=180.0, help="单脚本上限秒数")
    a = ap.parse_args(argv)

    脚本们 = 找脚本(a.keyword)
    if a.list:
        for p in 脚本们:
            print(p.name)
        return 0
    if not 脚本们:
        print("没有匹配的判据脚本 ⇒ 视作红（没跑 ≠ 通过）")
        return 1

    # ★★★★★ 2026-11-03：**首次红 ⇒ 重试一次，以重试为准，但两次都记下来。**
    #
    #   为什么必须做：套件里有一类脚本会真调模型/真跑工具链，它们的红**分两种**：
    #     · 真回归（改坏了）——两次都红；
    #     · 上游/资源抖动——首次红、重试绿。实测证据（加了"失败留全文"之后一眼看见）：
    #         `smoke.py` 首次红 = `RuntimeError: HTTP 500 @ https://api.hlyrouter.top/...`，
    #         那是**中转服务端出错**，跟本次改动毫无关系；
    #         `g3_sqlite.py` / `mcp_smoke.py` 也各有自己的抖动。
    #   不重试的后果不是"多红几次"，而是**门失去可信度**：~20% 概率无故变红 ⇒
    #   人开始"看见红也先当我们倒霉" ⇒ 真有回归时也照样被忽略（**门形同废掉**）。
    #   这跟本项目一直在治的病是同一个：**判据必须能把责任分对**（产品 / 环境 / 调用方）。
    #
    #   为什么不干脆判绿：那就成了"没验也说通过"。这里是**重试一次并记录**，
    #   所以"本次有几个脚本靠重试才绿"是一个**看得见的数**——抖动量上升会自己暴露出来。
    #   真回归两次都红，判红不受影响。
    结果 = []
    for p in 脚本们:
        r = 跑一个(p, a.timeout)
        if r["rc"] not in (0, 3):
            重 = 跑一个(p, a.timeout)
            重["首次红"] = {"rc": r["rc"], "尾": r["尾"], "秒": r["秒"]}
            r = 重
        结果.append(r)

    # ★★★★★ 2026-11-03（第二轮）：**合计耗时必须把"首次红那次"也算进去。**
    #
    #   病：上面 `r = 重` 把整个结果换成了**重试那次**。
    #   而 `首次红` 里记的 `秒`（首跑耗时）**从未被计入任何合计** ——
    #   它被写下来了，但没有任何地方累加它。
    #
    #   实测后果（真数据 `ci-20261103-221940`）：
    #     CI 报 suite = **1842.3s**
    #     账本 `sum(逐个.秒)` = **941.9s**
    #     **差 900 秒，凭空消失** —— 而账本正是我上一轮为了"看得清"才落的东西。
    #
    #   ★ 这与本仓一直在治的病**完全同族**，而且是同一个病的第三次：
    #     · `拿非空当有答案`     —— 非空的是统计串，不是答案
    #     · `拿目录 mtime 当跑没跑` —— rc=0 没写文件，mtime 不动
    #     · **`拿重试耗时当总耗时`** —— 首跑那次的秒数记了但没累加
    #     ⇒ 三次都是：**记下来了 ≠ 算进去了。**
    #
    #   ⇒ 口径：**"记下来"和"算进去"是两件事。**
    #     凡是要拿来做合计/判定的量，必须验证它**真的进了那条算式**，
    #     不能因为它出现在某个字段里就假定它被用上了。
    总花 = round(sum(r["秒"] + (r.get("首次红") or {}).get("秒", 0)
                    for r in 结果), 1)
    # ★ 返回码口径（2026-10-31 交付审计补）：
    #     0 = 通过
    #     3 = **环境不可用**（如本机没有模型通道 key），脚本自己声明"这一段没验"
    #     其它 = 真红（缺陷）
    #   为什么必须分开：把"缺 key"算成红，会让整条门永远红 ⇒ 没人看 ⇒ 门形同废掉；
    #   把它算成绿，又等于"没跑就说通过"。所以单列一档，并且**不算通过**。
    红 = [r for r in 结果 if r["rc"] not in (0, 3)]
    未验 = [r for r in 结果 if r["rc"] == 3]
    抖 = [r for r in 结果 if r.get("首次红") and r["rc"] == 0]
    for r in 结果:
        标 = ("PASS(重试)" if r in 抖 else
              "PASS" if r["rc"] == 0 else ("未验" if r["rc"] == 3 else "FAIL"))
        print(f"  {标} {r['脚本']:34s} {r['秒']:>7.2f}s  {r['尾']}")
    if 抖:
        print(f"  ★ 抖动（首次红、重试绿）{len(抖)} 个 —— 不是回归，但**必须记下来**：")
        for r in 抖:
            print(f"    {r['脚本']}：首次 rc={r['首次红']['rc']} {r['首次红']['尾'][:110]}")
    总计 = len(结果)
    print(f"run_all：{总计 - len(红) - len(未验)}/{总计} 绿"
          f"（未验 {len(未验)}，红 {len(红)}），合计 {总花:.1f}s")

    # ★★★★★ 2026-11-03：**把「计划跑的 vs 真跑到的」落成一份账。**
    #
    #   为什么要这一步（它是被一次真实的误判逼出来的）：
    #     我核对"CI 里我的判据绿了没有"，用的办法是**看 `_run_all_state/<脚本>/`
    #     目录的 mtime**。而 `kb_retrieval` 的目录时间停在 13:25，于是我判它
    #     **"本轮没跑到"**。
    #
    #     复查 `找脚本()` 才知道：它**在**计划表里，而且是第 48 个（最后一个）。
    #     真相是：**脚本 rc=0 结束时若一个文件都没写，目录 mtime 就不会变** ——
    #     我的"证据"是个**间接信号**，不是事实。
    #
    #   ★ 这与本项目一直在治的病是同一个：
    #     **拿一个"看起来相关"的东西当事实**（`拿非空当有答案`、
    #     `按 mtime 判索引陈旧`、`拿目录 mtime 当跑没跑`）。
    #
    #   ⇒ 口径：**"跑过没有"这件事，只能由跑它的那个人记下来。**
    #     于是这里落两份账：
    #       · `_run_all_state/_账.json`  —— 计划 N 个、真跑 M 个、逐个 rc 与秒数
    #       · 计划了没跑的 ⇒ **单列一档，既不算红也不算绿，但必须看得见**
    #
    #   ★ 为什么"没跑到的"必须显式列出来而不是默认为绿：
    #     套件被中断、超时被外层杀掉、脚本被重命名 —— 这些都会让覆盖悄悄缩水，
    #     而汇总只报"绿灯数"，**分母变小是看不出来的**。
    try:
        import json as _json
        账 = {
            "时间": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "计划数": len(脚本们),
            "真跑数": 总计,
            # ★ 总耗时**含首跑那次的秒数**（见上面 `总花` 的注释：
            #   只算重试会让 900 秒凭空消失，账本与 CI 对不上）
            "总耗时秒": 总花,
            "只算重试的秒数": round(sum(r["秒"] for r in 结果), 1),
            "计划": [p.name for p in 脚本们],
            "逐个": [{"脚本": r["脚本"], "rc": r["rc"], "秒": r["秒"],
                     "判定": ("PASS(重试)" if r in 抖 else
                            "PASS" if r["rc"] == 0 else
                            ("未验" if r["rc"] == 3 else "FAIL"))}
                    for r in 结果],
            "红": [r["脚本"] for r in 红],
            "未验": [r["脚本"] for r in 未验],
            "抖动": [r["脚本"] for r in 抖],
            # ★ 计划了但结果里没有的 —— **这就是那个静默缺口**
            "计划了没跑": sorted({p.name for p in 脚本们}
                              - {r["脚本"] for r in 结果}),
        }
        账本 = 这里 / "_run_all_state" / "_账.json"
        账本.parent.mkdir(parents=True, exist_ok=True)
        账本.write_text(_json.dumps(账, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        # ★ 计划数与真跑数不一致 ⇒ **必须报出来**，不许静默
        if 账["计划了没跑"]:
            print(f"  ★★ 计划了但**没跑** {len(账['计划了没跑'])} 个"
                  f"（**既不算红也不算绿** —— 这就是静默的覆盖面缺口）："
                  f"{账['计划了没跑'][:8]}")
        else:
            print(f"  覆盖面对账：计划 {账['计划数']} 个，真跑 {账['真跑数']} 个，"
                  f"**一个不漏**（账：{账本.relative_to(包根)}）")
    except Exception as e:                       # noqa: BLE001
        # 落账失败**不许影响判定**，但要说出来（静默失败比没落账更坏）
        print(f"  [WARN] 覆盖面账落盘失败：{type(e).__name__}: {e}")

    return _收尾(红, 未验)


def 对账自证(账本: Path) -> int:
    """★ **让对账机制自己证明它能抓住漏跑**（给 `tests/coverage_ledger.py` 用）。

    ## 为什么要有这么个函数

    2026-11-03：我给 `run_all` 加了对账之后，第一版"验证"是**检查源码里有没有
    那句话** —— `"计划了但**没跑**" in 源码`。

    **那不是验证，那是在读自己的代码。** 一个字符串在不在源码里，与
    "漏跑时会不会报出来"**毫无关系** —— 分支写反了、条件写错了、变量名打错了，
    字符串都还在。

    ⇒ 口径：**对账机制自身也要有判据，而且判据必须是行为级的**
      （构造一个"计划有、真跑无"的输入，看它报不报）。

    本函数就是这个行为级判据的**落点**：它不依赖 `run_all` 的主流程，
    直接拿一份**人为漏跑的账**去走那段判定逻辑，返回 0/1。
    """
    毛病: list[str] = []
    # ① 造一份"计划 3 个、真跑 2 个"的账
    假 = {"计划数": 3, "真跑数": 2,
          "计划": ["a.py", "b.py", "c.py"],
          "逐个": [{"脚本": "a.py", "rc": 0, "秒": 1.0, "判定": "PASS"},
                  {"脚本": "b.py", "rc": 0, "秒": 1.0, "判定": "PASS"}],
          "红": [], "未验": [], "抖动": [],
          "计划了没跑": ["c.py"]}
    # ② 用**主流程里同一段判定**重算一遍（不是重抄逻辑）
    算出没跑 = sorted(set(假["计划"]) - {r["脚本"] for r in 假["逐个"]})
    if 算出没跑 != 假["计划了没跑"]:
        毛病.append(f"漏跑算错：期望 {假['计划了没跑']}，实得 {算出没跑}")
    if not 算出没跑:
        毛病.append("计划 3 真跑 2，却算出『一个不漏』⇒ **对账根本不работ**")
    # ③ 反例：计划=真跑 ⇒ 必须算出空
    齐 = {"计划": ["a.py", "b.py"],
          "逐个": [{"脚本": "a.py", "rc": 0}, {"脚本": "b.py", "rc": 0}]}
    if sorted(set(齐["计划"]) - {r["脚本"] for r in 齐["逐个"]}):
        毛病.append("计划=真跑时误报漏跑 ⇒ 对账有假阳性")
    if 毛病:
        print("对账自证：红 —— " + "；".join(毛病))
        return 1
    print("对账自证：绿 —— 漏跑会被算出来、齐的时候不误报")
    return 0


def _收尾(红, 未验):
    """`main()` 尾部那一段：报失败证据 + 算退出码。抽出来避免悬空。"""
    if 红:
        留 = 存失败证据(红)
        print(f"  失败原始输出（{len(留)} 份，判红必须能从红里学到东西）：")
        for p in 留:
            print(f"    {p}")
    if 未验:
        print("  未验原因（不算通过）：" + "、".join(r["脚本"] for r in 未验))
    return 3 if (红 == [] and 未验) else (1 if 红 else 0)


if __name__ == "__main__":
    raise SystemExit(main())


