"""taixu MCP server 冒烟测试：真实 stdio 握手 + 锚定闸 + 后台任务闭环。"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ★ 2026-10-31（交付审计）：原写作 `[sys.executable, "src/.../taixu_mcp.py"]` —— **脚本路径启动**。
#   脚本启动时 sys.path[0] 落在包目录里、`__package__` 为空 ⇒ 模块内任何相对导入
#   都抛 `ImportError: attempted relative import with no known parent package`
#   ⇒ 服务器进程一启动就死 ⇒ 客户端只看到 `McpError: Connection closed`。
#   （实测：用空输入手动起同一个文件，得到的就是那条 ImportError。）
#   ⇒ 必须 `-m 包名`，与部署档案里的真实启动方式一致；
#      否则这个测速程序测的不是"真服务器"，而是"一种没人用的错误启动方式"。
SERVER = [sys.executable, "-m", "agentscope_orchestrator.taixu_mcp"]
# 本测试自己的状态根：必须**真可写**，且不碰生产状态。
# 注意本机 `tempfile` 会回退到当前目录（系统 TEMP 对 python 不可写），
# 所以这里不靠 tempfile 猜位置，直接钉在包根下的临时目录里，并可被环境变量覆盖。
测试状态根 = Path(os.environ.get("ORCH_SMOKE_STATE") or
                (Path(__file__).resolve().parents[1] / "_smoke_state")).resolve()
TEMPLATE = """import MyMeta.InfraCore.Derivation.InfinitePan

namespace MyMeta.Orchestrated_002

open InfinitePan

/-- 收口引理：非零输入的无界盘展开必非空盘（[] 只由 0 展开）。 -/
theorem siZhuInfinite_ne_empty_of_pos {n : Nat} (h : n ≠ 0) :
    siZhuInfinite n ≠ ([] : List Nat) := by
  sorry

end MyMeta.Orchestrated_002
"""


async def main() -> int:
    # ★ 2026-11-01：真跑闭环这一段必须把**真可用通道**带进去。
    #   原来不传 channel ⇒ 服务器按配置顺序取第一个（`aaa`，本机无 key）
    #   ⇒ 必然 error ⇒ 记 3「未验」，而同一时刻 `dddddd` 实测可达。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _通道 import 选可用通道
    _通道名, _, _报告 = 选可用通道()
    # ★★ 2026-10-31（交付审计）：这里原来**不传 env**，服务器子进程于是自己去猜状态根。
    #   本机所有候选（`%LOCALAPPDATA%` / 家目录 / `E:\Temp`）都不可写
    #   ⇒ 服务器启动即 `PermissionError [WinError 5]` 退出 ⇒ 客户端只看到
    #   `McpError: Connection closed` —— **真正的原因被这一句吞掉了**，
    #   看起来像"产品握手坏了"，其实是"这台机器没给服务器一个能写的地方"。
    #   ⇒ 两件事一起修：
    #     ① 给本次测试一个**独立且可写**的状态根（也顺带保证不碰生产状态）；
    #     ② 把子进程 stderr 透到本进程，让启动期崩溃当场可见，不再只剩一句 Connection closed。
    env = dict(os.environ)
    env["ORCH_STATE_DIR"] = str(测试状态根)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    params = StdioServerParameters(command=SERVER[0], args=SERVER[1:],
                                   cwd=str(Path(__file__).resolve().parents[1]),
                                   env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as sess:
            init = await sess.initialize()
            print(f"[smoke] initialize ok server={init.serverInfo}", flush=True)
            tools = await sess.list_tools()
            names = [t.name for t in tools.tools]
            print(f"[smoke] tools/list -> {names}", flush=True)
            # ★ 2026-10-31（交付审计）：原期望 `太虚·提交任务` 等**中文工具名**，
            #   而 R224 已明令工具名必须是 ASCII（中文进 description）⇒ 断言必红。
            #   更要紧的是：**别再把名字写死**。这里从 list_tools 里按后缀匹配，
            #   名字怎么变都能找到真实工具，测的是"有这些能力"，不是"叫这几个字"。
            def 找(slug: str) -> str:
                命中 = [n for n in names if n.endswith(slug)]
                assert 命中, f"找不到后缀为 {slug!r} 的工具；实际={names}"
                return 命中[0]

            print(f"[smoke] 工具数={len(names)}", flush=True)
            assert len(names) >= 20, f"工具数偏少：{len(names)}"
            T提交, T列表 = 找("submit_task"), 找("list_tasks")
            T状态, T门 = 找("task_status"), 找("judge_result")
            T报告, T审计 = 找("read_report"), 找("audit_log")
            T取消 = 找("cancel_task")
            r = await sess.call_tool(T提交, {"unit_id": "MyMeta.Not.Exists",
                                                     "module": "MyMeta.Orchestrated_002",
                                                     "theorem": "siZhuInfinite_ne_empty_of_pos",
                                                     "statement_marker": "(h : n ≠ 0)",
                                                     "target_rel": "Orchestrated_002.lean",
                                                     "template": TEMPLATE})
            txt = r.content[0].text
            print(f"[smoke] anchor-gate: {txt[:120]}", flush=True)
            assert '"ok": false' in txt and "拒跑" in txt, "锚定闸未生效"
            # ★★ 2026-11-04：**没有可用通道就到此为止**，明说未验。
            #   本机实测四条通道全 dead（三条无 key、一条 HTTP 403 无额度）⇒
            #   `选可用通道()` 返回 live=[]。下面那段"真跑闭环"必须有模型；
            #   硬跑的话服务端任务会一直等/重试 ⇒ 本轮实测**套件里挂 20+ 分钟**，
            #   而且 900 秒超时都没回收它（子进程持管道）。
            #   口径：**接线该验的上面已经验完**（initialize / tools/list / 锚定闸），
            #   缺的只是"真模型那一段" ⇒ 记**未验**，不许当红，也不许当绿。
            if not _通道名:
                print("[smoke] 未验：「真跑闭环」需要至少一个可用模型通道，"
                      "本机实测一条都没有 ⇒ **这一步没验成**，不是接线坏了。", flush=True)
                print(f"[smoke] 通道探测报告：probed={_报告.get('probed')} "
                      f"live={_报告.get('live')} ⇒ 需要 key 或额度", flush=True)
                print("[smoke] 已经验过的（与模型无关，全部通过）："
                      "stdio 握手 initialize · tools/list（>=20） · 锚定闸拒跑", flush=True)
                return 3
            # ★★★★★ 2026-11-03（套件下 ~12% 抖动，连跑 8 次抓到 1 次失败）：
            #   原来这里**一次定生死**：`assert st["state"] == "staged"`。
            #   实测失败原文：`AssertionError: want staged, got convergence_failed: None`
            #   —— 而 `dry=True` 烧字 0 字符（不真的问模型），所以这个"不收敛"来自
            #   **下层的 Lean 调用/超时/资源争用**，不是接线坏了。
            #
            #   口径错在哪：这条测速程序的目的是验 **MCP 接线**
            #   （工具注册齐、锚定闸生效、submit→轮询→终态→judge/audit/report/list 全通），
            #   而它却拿**下游随机判定**（这次任务收不收敛）当判据。
            #   下游一抖，判据就红 ⇒ **CI 门本身不可信** ⇒ 门形同废掉。
            #
            #   修法：**重试到判据稳定**，而不是放宽判据。
            #     · 一次抖动放过（重试后到 staged ⇒ 接线是通的）；
            #     · 系统性坏掉两次都到不了 ⇒ 仍然红。
            #   为什么不用"放过任意终态"：那会把**真回归**（比如收敛逻辑坏了）一起放过。
            #   为什么打印重试次数：绿的时候也要让人看见"这次用了几次"，
            #   否则抖动被静默吸收，下次变成"偶发红"又要重新查一遍。
            st, task_id, 尝试 = None, None, 0
            for 尝试 in range(1, 3):
                r = await sess.call_tool(T提交, {
                    "unit_id": "MyMeta.InfraCore.Derivation.InfinitePan",
                    "module": "MyMeta.Orchestrated_002",
                    "theorem": "siZhuInfinite_ne_empty_of_pos",
                    "statement_marker": "(h : n ≠ 0)",
                    "target_rel": "Orchestrated_002.lean",
                    "template": TEMPLATE,
                    "n_windows": 2, "min_pass": 1, "dry": True,
                    "channel": _通道名})
                txt = r.content[0].text
                print(f"[smoke] submit(第{尝试}次): {txt[:160]}", flush=True)
                assert '"ok": true' in txt, f"submit fail: {txt}"
                task_id = json.loads(txt)["task_id"]
                st = None
                for _ in range(100):
                    await asyncio.sleep(2)
                    r = await sess.call_tool(T状态, {"task_id": task_id})
                    st = json.loads(r.content[0].text)
                    if st["state"] in ("staged", "promoted", "convergence_failed",
                                       "error", "cancelled"):
                        break
                print(f"[smoke] task_status(第{尝试}次) -> state={st['state']} rc={st.get('rc')}",
                      flush=True)
                _错 = str(st.get("error") or "")
                if st["state"] == "error" and "key not found" in _错:
                    print(f"[smoke] SKIP 环境不可用（无模型通道 key）：{_错[:120]}", flush=True)
                    print("[smoke] 这一步测的是'真跑闭环'，需要至少一个可用通道；"
                          "代码本身未参与判决。", flush=True)
                    return 3
                if st["state"] in ("staged", "promoted"):
                    break
                print(f"[smoke] 本次未到 staged（{st['state']}）⇒ 判为**下游抖动**并重试；"
                      "连续两次都这样才算接线有问题", flush=True)
            assert st["state"] in ("staged", "promoted"), \
                f"{尝试} 次都到不了 staged，最后是 {st['state']}: {st.get('error')}"
            r = await sess.call_tool(T门, {"task_id": task_id})
            print(f"[smoke] judge_result: {r.content[0].text[:200]}", flush=True)
            r = await sess.call_tool(T审计, {"task_id": task_id})
            print(f"[smoke] audit_log: {r.content[0].text[:200]}", flush=True)
            r = await sess.call_tool(T报告, {"task_id": task_id})
            print(f"[smoke] read_report ok={r.content[0].text[:80]}", flush=True)
            r = await sess.call_tool(T列表, {})
            print(f"[smoke] list_tasks -> {r.content[0].text[:200]}", flush=True)
    print("[smoke] ALL PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


