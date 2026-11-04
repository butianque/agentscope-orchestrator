r"""判据：三档新门把"人的推理"换成"可机判结论"（2026-11-03）。

它们补的是同一类洞 —— **判定权跑到了机器之外**：

    · `precondition`       假故障：把"环境前提"与"产物正确性"拆开
    · `instance_agreement` 抖动：  把"结论不稳定"报成结论，而不是让人重跑三次
    · `regression_parity`  回归：  把"我没引入回归"从推理变成**对照实测**

本判据用**真起进程**验证它们，并锁住三条不可退化的语义：
    ① 前提不成立 ⇒ 红，且带 `前提不成立` 标记（上层据此报调用方责任，不去修产物）
    ② 实例分歧   ⇒ 红，且带 `实例分歧`  标记（责任在环境耦合，不是产物）
    ③ 对照两边**都红且数字相同** ⇒ **绿**（本次无新增回归）——这是最反直觉、
       也最容易被"顺手改成'红就是红'"的一条，所以必须锁。
"""
import os
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(根 / "src"))

from agentscope_orchestrator import gate_spec as G  # noqa: E402
from agentscope_orchestrator import auto_goal as AG  # noqa: E402

失败: list[str] = []


def 断言(条件, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}", flush=True)
    else:
        失败.append(说明)
        print(f"  FAIL {说明}", flush=True)


def 跑门(门: dict, 工作目录: Path, 产物=None):
    p = 产物 or (工作目录 / "x.txt")
    if not p.exists():
        p.write_text("x", encoding="utf-8")
    return G._run_one(门, p, 工作目录, str(工作目录))


def main() -> int:
    print("═══ 三档新门 ═══")

    # ① 契约表与执行器**同源**（这是本仓反复强调的"不许有第二把尺子"）
    一致, 说明 = AG.门类型一致()
    断言(一致, f"门类型表与执行器一致（{说明 or '一致'}）")
    for t in ("precondition", "instance_agreement", "regression_parity"):
        断言(t in AG.合法门类型(), f"执行器认 {t}")
        断言(t in AG._门字段表, f"契约表登记 {t}")

    # ①a ★ 三处门类型清单必须**同源**（2026-11-03 修的真事故）
    #
    #   原来 `gate_spec._TYPES` 是**手写集合**（10 项），而执行器有 14 分支
    #   ⇒ 真症状（经 MCP 通道实测）：
    #     `非法判据门规格项：{...}（type 须属于 [... 10 项 ...]）`
    #   —— **能执行的门被判非法**，调用方只能盲猜。
    #   最久的一份烂账：`oracle` 门一直这样被拒（校验器与执行器各记一份清单）。
    #   现在 `_TYPES = 合法门类型()`（从执行分支数出来），本判据锁住它不再分叉。
    print("\n· 门类型清单同源（防第三把尺子）")
    _执 = set(AG.合法门类型())
    _校 = set(G._TYPES)
    断言(_执 == _校, f"校验器清单 == 执行器清单（校验 {len(_校)} / 执行 {len(_执)}）")
    断言("oracle" in _校, "既有的 oracle 门不再被误判非法（历史烂账）")
    _源 = Path(G.__file__).read_text(encoding="utf-8")
    断言("_TYPES = 合法门类型()" in _源 or "_TYPES = 合法门类型" in _源,
         "_TYPES 是**数出来的**，不是手写清单（手写的下次加门型又会分叉）")

    # ①b ★ 计数解析不许错位（2026-11-03 修的真漏洞）
    #
    #   真证据：`passed=10 failed=3 skipped=0` 曾被解析成
    #     {passed: 0, failed: 10, skipped: 3} —— 整体错位。
    #   根因：`failed` 的式子是 `(\d+)\s+failed`，而 `passed=10 failed` 里的
    #     `10 failed` 正好满足它（**`=` 被当透明字符跨过去了**）。
    #   危害：`test_counts` 门的三个阈值全读这几个数 ⇒ 计数一错位，
    #     "跳过过多"会报成"有失败"，**该红的可能判绿**。
    #   这是能直接制造假绿的洞，所以必须永久锁住。
    print("\n· 计数解析（防错位）")
    _字 = {True: None}
    for _样, _期 in [
        ("passed=10 failed=3 skipped=0", (10, 3, 0)),
        ("passed=10 failed=0 skipped=0", (10, 0, 0)),
        ("3 passed, 1 failed, 2 skipped", (3, 1, 2)),
        ("test result: ok. 121 passed; 30 failed; 0 ignored", (121, 30, 0)),
        ("Tests: 5 passed, 2 failed", (5, 2, 0)),
    ]:
        _c = G._测试计数(_样)
        _得 = (_c["passed"], _c["failed"], _c["skipped"])
        断言(_得 == _期, f"「{_样}」→ passed/failed/skipped={_得}（期望 {_期}）")

    临时 = Path(tempfile.mkdtemp(prefix="gate3-", dir=str(根)))
    atexit.register(shutil.rmtree, 临时, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    try:
        # ② precondition
        print("\n· precondition（假故障 → 显式前提）")
        可写 = 临时 / "writable"
        可写.mkdir()
        门 = {"name": "p1", "type": "precondition", "in": str(可写), "needs_writable_dir": True}
        过, 据 = 跑门(门, 临时)
        断言(过, "可写目录 ⇒ 绿（准入通过）")
        断言("不代表产物合格" in str(据.get("cause", "")),
             "绿时明说「不代表产物合格」（防被当成验收证据）")

        # 用一个**必然是文件**的路径当目录 ⇒ 建目录失败 ⇒ 前提不成立
        假目录 = 临时 / "这是文件不是目录"
        假目录.write_text("x", encoding="utf-8")
        门 = {"name": "p2", "type": "precondition", "in": str(假目录), "needs_writable_dir": True}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and 据.get("前提不成立") is True,
             "不可写 ⇒ 红**且带 `前提不成立` 标记**（上层才能报调用方责任）")

        门 = {"name": "p3", "type": "precondition", "in": str(可写),
              "requires_env": ["ORCH_这个变量一定不存在_zzz"]}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and 据.get("前提不成立") is True, "缺环境变量 ⇒ 前提不成立")

        门 = {"name": "p4", "type": "precondition", "in": str(可写),
              "requires_path": [str(临时 / "不存在的路径_zzz")]}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and 据.get("前提不成立") is True, "缺路径 ⇒ 前提不成立")

        门 = {"name": "p5", "type": "precondition", "in": str(可写)}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and 据.get("三态") == "未知",
             "没声明任何前提 ⇒ 未知（不是绿：空门不许冒充通过）")

        # ③ instance_agreement
        print("\n· instance_agreement（抖动 → 可机判结论）")
        统 = [sys.executable, "-c", "print('passed=3 failed=0 skipped=0')"]
        门 = {"name": "i1", "type": "instance_agreement", "cmd": 统, "instances": 3}
        过, 据 = 跑门(门, 临时)
        断言(过, f"三实例结论一致 ⇒ 绿（{len(据.get('实例', []))} 个实例）")
        断言("实例一致" in str(据.get("cause", "")), "证据写明「实例一致」")

        # 让第 2 个实例（PYTHONHASHSEED=2）给出不同计数 ⇒ 分歧
        抖 = [sys.executable, "-c",
              "import os;print('passed=%d failed=0 skipped=0' % (3 if os.environ.get('PYTHONHASHSEED')=='2' else 5))"]
        门 = {"name": "i2", "type": "instance_agreement", "cmd": 抖, "instances": 3}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and 据.get("实例分歧") is True,
             "实例分歧 ⇒ 红**且带 `实例分歧` 标记**（责任在环境耦合，不去修产物）")
        断言(any("passed" in str(x.get("计数")) for x in 据.get("实例", [])),
             "分歧证据带每实例计数（能看清谁不一样）")

        门 = {"name": "i3", "type": "instance_agreement", "cmd": 统, "instances": 1}
        过, 据 = 跑门(门, 临时)
        断言(len(据.get("实例", [])) >= 2, "instances=1 被抬到 ≥2（一个实例证明不了稳定性）")

        门 = {"name": "i4", "type": "instance_agreement", "cmd": 抖, "instances": 3,
              "require_same_verdict": False}
        过, 据 = 跑门(门, 临时)
        断言(过, "require_same_verdict=false ⇒ 只报分歧不判红")

        # ★★ 边界必须写进源码注释（防被当成万能）：本门测**环境**异构，
        #    测不出**上游服务**抖动。真证据：`g3_sqlite.py` 套件里连红两次、
        #    单独跑 ALL PASS，而本门拿它跑两实例是**一致绿**（两个实例都在轻载下）。
        _源 = Path(G.__file__).read_text(encoding="utf-8")
        _i = _源.find('typ == "instance_agreement"')
        _段 = _源[_i:_i + 3500] if _i >= 0 else ""
        断言("测不出上游服务抖动" in _段,
             "门源码里写明第二条边界（测不出上游抖动，别当万能）")

        # ④ regression_parity
        print("\n· regression_parity（回归 → 对照实测）")
        旧 = [sys.executable, "-c", "print('passed=10 failed=3 skipped=0')"]
        新同 = [sys.executable, "-c", "print('passed=10 failed=3 skipped=0')"]
        门 = {"name": "r1", "type": "regression_parity",
              "baseline_cmd": 旧, "cmd": 新同, "allow_baseline_red": True}
        过, 据 = 跑门(门, 临时)
        断言(过, "★ **两边都红且数字相同 ⇒ 绿**（本次无新增回归）")
        断言("无回归" in str(据.get("cause", "")), "证据写明「无回归」")

        新增 = [sys.executable, "-c", "print('passed=10 failed=5 skipped=0')"]
        门 = {"name": "r2", "type": "regression_parity",
              "baseline_cmd": 旧, "cmd": 新增, "allow_baseline_red": True}
        过, 据 = 跑门(门, 临时)
        断言(not 过, "failed 新增 ⇒ 红（这才是回归）")
        断言(any("failed 新增" in d for d in 据.get("差", [])), "差集指出是 failed 新增")

        退 = [sys.executable, "-c", "print('passed=7 failed=3 skipped=0')"]
        门 = {"name": "r3", "type": "regression_parity",
              "baseline_cmd": 旧, "cmd": 退, "allow_baseline_red": True}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and any("passed 减少" in d for d in 据.get("差", [])),
             "passed 减少 ⇒ 红（有东西从过变成不过）")

        跳 = [sys.executable, "-c", "print('passed=10 failed=3 skipped=2')"]
        门 = {"name": "r4", "type": "regression_parity",
              "baseline_cmd": 旧, "cmd": 跳, "allow_baseline_red": True}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and any("skipped 新增" in d for d in 据.get("差", [])),
             "skipped 新增 ⇒ 红（跳过了也算回归）")

        # 基线红、未显式允许 ⇒ 未知（拿坏基线当参照无意义）
        门 = {"name": "r5", "type": "regression_parity", "baseline_cmd": 旧, "cmd": 新同}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and 据.get("三态") == "未知",
             "基线本身红且未 allow_baseline_red ⇒ **未知**（不拿坏基线当参照）")

        # 两边都数不出计数 ⇒ 未知，不是绿
        噪 = [sys.executable, "-c", "print('就是一句普通输出')"]
        门 = {"name": "r6", "type": "regression_parity", "baseline_cmd": 噪, "cmd": 噪}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and 据.get("三态") == "未知",
             "数不出测试计数 ⇒ 未知（不是测试命令时不硬判）")

        # 缺命令 ⇒ 未知
        门 = {"name": "r7", "type": "regression_parity", "cmd": 新同}
        过, 据 = 跑门(门, 临时)
        断言(not 过 and 据.get("三态") == "未知", "缺 baseline_cmd ⇒ 未知")
    finally:
        import gc
        import time
        for _ in range(5):
            shutil.rmtree(临时, ignore_errors=True)
            if not 临时.exists():
                break
            gc.collect()
            time.sleep(0.1)
        断言(not 临时.exists(), "判据不留临时目录")

    # ══════════════════════════════════════════════════════════════════════════
    # ④ ★★★ 端到端：新门必须**经 MCP 通道**真跑一次（进程内绿**不算数**）
    #
    #   为什么加这一节（2026-11-03 真事故，值得逐字留下）：
    #     三档门在**进程内**判据里全绿（直接调 `gate_spec._run_one`），
    #     我一提交就以为完事。结果经 MCP 通道真跑时：
    #       `error: TypeError: '>' not supported between instances of
    #        'NoneType' and 'int'`，且 `门数.实得=0`
    #     —— 看起来像"新门根本没跑"，实际是**聚合层**（`taixu_mcp` 里
    #     聚合各门 `rc` 求 max）拿 `rc=None` 当数字用了：
    #        `a.get("rc", -1) for a in audit if "rc" in a`
    #     两个坑叠加：`in` 只判键在不在；`get` 的默认值只在**键缺失**时生效。
    #     ⇒ `precondition` 门不跑命令，`rc=None` 是**合法语义**，
    #       却被聚合层判成了崩溃。
    #
    #   ⇒ 教训（硬要求，本节就是它的机判形式）：
    #     **进程内判据覆盖不到"门被上层聚合"这条路径。**
    #     新门/改门之后，必须经 MCP 通道端到端跑一次 —— 否则你验的是
    #     "这个函数能返回对的元组"，不是"这门在真管线里能活"。
    #
    #   本节做得起这件事：`orch_run` 在无网络、无模型时也能跑（cmd 是
    #   `python -c print(1)`，门是两类不跑命令的门）⇒ 秒级、可进套件。
    # ══════════════════════════════════════════════════════════════════════════
    print("\n· 端到端（经 MCP 通道，进程内绿不算数）")
    import asyncio
    import json as _json
    try:
        from agentscope_orchestrator import taixu_mcp as _M
        # ★ 复用本函数**已有**的 `根`（不要在这里再赋一次：
        #   一旦在本函数里对 `根` 赋值，Python 就把整个函数作用域的 `根`
        #   当局部变量 ⇒ 上面 `mkdtemp(dir=str(根))` 那行报 UnboundLocalError。
        #   我加这节时就踩了，实测报错，记在这里省下一个人的十分钟。）

        async def _跑端到端():
            回 = await _M.orch_run(
                str(根),
                [sys.executable, "-c", "print(1)"],
                gates=[
                    {"name": "e2e 前提", "type": "precondition",
                     "needs_writable_dir": True},
                ],
                timeout_s=180)
            return _json.loads(回) if isinstance(回, str) else 回

        _回 = asyncio.run(_跑端到端())
        _错 = _回.get("error")
        _门 = _回.get("门") or []
        # ① 聚合层不许因为"某个门没有 rc"而崩（这就是上面那个真事故）
        断言(not (isinstance(_错, str) and "NoneType" in _错),
             f"聚合层容忍 rc=None 的门（error={str(_错)[:90]}）")
        # ② 门必须**真的进过管线**（实得 > 0）——防"静默丢弃"
        断言(len(_门) > 0,
             f"门经管线真的跑了（门数={len(_门)}，实得 0 就是静默丢弃）")
        # ③ 前提门在真管线里也必须给出"前提成立"的绿
        _过 = [x for x in _门 if x.get("通过")]
        断言(bool(_过), f"前提成立 ⇒ 门绿（实得 {[(x.get('门'), x.get('通过')) for x in _门]}）")

        # ④ ★★★ 按**真实调用方的方式**再跑一条：主命令放**顶层** `cmd`，
        #    门里只写它额外需要的东西（`regression_parity` 的 `baseline_cmd`）。
        #
        #    为什么必须这样测（第二个只在端到端暴露的缺陷，2026-11-03）：
        #      我第一次测 `regression_parity` 是**把 cmd 写在门里**的，
        #      于是进程内绿、我也以为没问题；真跑时按契约把主命令放顶层，
        #      门却报 `未知|regression_parity 需要 baseline_cmd 与 cmd 两条命令`
        #      —— **调用方明明给了主命令**。契约（顶层 cmd）与实现（读 g["cmd"]）
        #      不对齐，而"我自己写测试时把 cmd 也塞进门里"恰好绕过了它。
        #    ⇒ 教训：自测要**照调用方的写法**写，不能照实现的读法写；
        #      否则你验的是"实现能读到我给的东西"，不是"调用方给的东西能被读到"。
        #    本情形同时锁住那条最反直觉的语义：**两边都红且数字相同 ⇒ 门绿**。
        _坏 = [sys.executable, "-c",
               "import sys; print('5 passed, 3 failed'); sys.exit(1)"]

        async def _跑回归对照():
            回 = await _M.orch_run(
                str(根), _坏,
                gates=[{"name": "回归对照", "type": "regression_parity",
                        "baseline_cmd": list(_坏),
                        # ★ `allow_baseline_red=true` 是**必须显式给**的：
                        #   本情形故意用"基线本来就红"的场景（本机 Rust 那 30 条
                        #   环境红就是这样），不显式声明时门报'未知' —— 那是
                        #   **正确**的保守语义（无法区分"基线红是常态"与
                        #   "基线红是我造成的"）。判据第一次写漏了这个参数，
                        #   门如实报未知 ⇒ 我改成"照真实用法给全参数"。
                        "allow_baseline_red": True}],
                timeout_s=180)
            return _json.loads(回) if isinstance(回, str) else 回

        _回2 = asyncio.run(_跑回归对照())
        _门2 = _回2.get("门") or []
        _对照 = [x for x in _门2 if x.get("门") == "回归对照"]
        断言(bool(_对照), f"回归对照门进了管线（实得 {[x.get('门') for x in _门2]}）")
        if _对照:
            _证 = str(_对照[0].get("证据") or "")
            断言("需要 `baseline_cmd` 与 `cmd` 两条命令" not in _证,
                 f"顶层 cmd 能被门读到（不能报缺 cmd）：{_证[:90]}")
            断言(_对照[0].get("通过") is True,
                 f"两边都红且数字相同 ⇒ **门绿**（最反直觉的一条）：{_证[:100]}")
            断言("无回归" in _证 or "一致" in _证,
                 f"绿的理由写清了是'无新增回归'：{_证[:90]}")
    except Exception as e:                      # noqa: BLE001
        断言(False, f"端到端跑通（{type(e).__name__}: {str(e)[:120]}）")

    print()
    if 失败:
        print(f"新门：红 {len(失败)} 项 —— " + "；".join(失败))
        return 1
    print("新门：绿 —— 三档都能机判、标记可供定责，且**经 MCP 通道**端到端跑通")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
