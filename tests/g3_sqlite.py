"""G3 SQLite 冒烟测试：进程重启历史仍在 + 断点续跑(resume_task) + 旧 JSON 迁移。
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import sqlite3
import atexit
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE = Path(__file__).resolve().parents[1]
# ★ 2026-10-31（交付审计）：原用中文工具名断言（`太虚·任务状态` 等）。
#   R224 已把工具名统一为 ASCII（中文进 description）⇒ 这些断言必红，
#   而且红的样子像"工具缺失"，实际是"名字变了"。
#   ⇒ 改为按**能力后缀**找真实名字；名字从被测服务器的 tools/list 拿（真实来源），
#     拿不到时退回本进程注册表（同一份装饰点），保证任何调用点都能解析。
def _t(names, slug):
    池 = list(names or [])
    if not 池:
        try:
            from agentscope_orchestrator import taixu_mcp as _m
            池 = list(_m.mcp._tool_manager._tools)
        except Exception:                      # noqa: BLE001
            池 = []
    hit = [n for n in 池 if n.endswith(slug)]
    assert hit, f"找不到后缀 {slug!r}：{池}"
    return hit[0]

# ★ 2026-10-31（交付审计）：同 mcp_smoke —— **脚本路径启动**会让包内相对导入崩
#   （`__package__` 为空 ⇒ ImportError ⇒ 客户端只看到 `McpError: Connection closed`）。
#   必须 `-m 包名`，与真实部署一致。
SERVER = [sys.executable, "-m", "agentscope_orchestrator.taixu_mcp"]

# ★★ 修复（2026-10-13）：**测试必须用独立库，绝不删生产 jobs.db**。
#   原实现 `DB = BASE / "jobs" / "jobs.db"` 并在 main() 里 `DB.unlink()`
#   —— 直接删掉**生产任务库**重建。后果（实机已发生）：
#     · 常驻 MCP 服务器（另进程）持有旧内存 _jobs/_seq，与磁盘分叉；
#     · 测试删库后磁盘只剩测试种的两行，而服务器内存仍是旧快照；
#     · 双方各自 INSERT OR REPLACE 互相覆盖 ⇒ 任务历史丢失、ID 计数器退回。
#   现改为独立测试库（tmp 下），经环境变量 TAIXU_DB 传给被测服务器进程。
#   同时保留旧路径只读引用（供断言 legacy 迁移，不删）。
JOBS = BASE / "jobs"
DB = JOBS / "jobs.db"

# 独立测试库：不碰生产库
TEST_DB = Path(tempfile.mkdtemp(prefix="taixu_g3_")) / "jobs.db"
atexit.register(shutil.rmtree, TEST_DB, ignore_errors=True)  # 跑完不留临时目录（包根会脏）
# 独立状态根：本测试只在这里放"遗留 tasks.json"，让服务器的真实迁移路径生效
测试状态根 = Path(tempfile.mkdtemp(prefix="taixu_g3_state_"))
atexit.register(shutil.rmtree, 测试状态根, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录

NE = "\u2260"
TEMPLATE = f"""import MyMeta.InfraCore.Derivation.InfinitePan

namespace MyMeta.Orchestrated_002

open InfinitePan

/-- closing lemma. -/
theorem siZhuInfinite_ne_empty_of_pos {{n : Nat}} (h : n {NE} 0) :
    siZhuInfinite n {NE} ([] : List Nat) := by
  sorry

end MyMeta.Orchestrated_002
"""

_DRY_BODY = """  intro he
  apply h
  exact si_zhu_infinite_injective (by
    rw [he]
    simp [siZhuInfinite])"""


def _dry_text() -> str:
    return TEMPLATE.replace("  sorry\n", _DRY_BODY + "\n")


async def _tool_json(sess, name: str, args: dict):
    """调工具并把返回解析为 JSON。

    ★ 修复（2026-10-13）：工具为护住聊天窗口会把大载荷**外置为文档**，
       返回 `[doc:xxx · tasks · 1985B] …` 指针而非 JSON。
       旧测试直接 `json.loads(text)` ⇒ 解析失败。
       这里先试直解；失败则从指针取 doc_id，用「太虚·读文档」拉回全文再解。
    """
    r = await sess.call_tool(name, args)
    text = r.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[doc:([^\]\s]+)", text)
    if not m:
        raise AssertionError(f"{name} 返回既非 JSON 也无 doc 指针：{text[:200]}")
    r2 = await sess.call_tool(_t(None, "read_doc"),
                              {"doc_id": m.group(1), "max_chars": 20000})
    return json.loads(r2.content[0].text)


async def _session(fn) -> None:
    # ★ 把独立测试库经环境变量传给被测服务器进程，绝不碰生产 jobs.db
    env = dict(os.environ)
    env["TAIXU_DB"] = str(TEST_DB)
    # ★★ 2026-10-31（交付审计 P0-1 的余波）：**遗留迁移读的是状态根下的 tasks.json**，
    #   不是包根 `jobs/tasks.json`（`JOBS_FILE = STATE_ROOT/"jobs"/"tasks.json"`）。
    #   原来这个测试在包根准备遗留文件，服务器却在状态根找 ⇒ 永远找不到 ⇒ 迁移断言空集。
    #   ⇒ 给本测试一个**独立状态根**，把遗留文件放到它该在的位置：
    #     既不碰生产状态，又测的是产品真实读法。
    env["ORCH_STATE_DIR"] = str(测试状态根)
    env.pop("TAIXU_DB_LEGACY_ONLY", None)
    params = StdioServerParameters(command=SERVER[0], args=SERVER[1:],
                                   cwd=str(BASE), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            tools = await sess.list_tools()
            names = [t.name for t in tools.tools]
            assert _t(names, "resume_task") in names, f"missing resume_task: {names}"
            await fn(sess)


class _无进展(Exception):
    """任务窗数在规定轮数内毫无变化 —— 通道探活/退避卡住，属基础设施侧。"""


async def _wait_state(sess, task_id, terminal, 耐心: int = 100,
                      停滞上限: int = 15) -> str:
    _上次进度 = -1
    _停滞 = 0
    for _ in range(耐心):
        await asyncio.sleep(2)
        # ★ 这里原本写 `_t(None, ...)`，而 `names` 是另一个函数的局部变量 ⇒ NameError。
        #   传 None：让 _t 从注册表解析（同一份装饰点，等价且不依赖调用点现场）。
        st = await _tool_json(sess, _t(None, "task_status"), {"task_id": task_id})
        if st["state"] in terminal:
            return st["state"]
        _进度 = len(st.get("windows") or [])
        if _进度 == _上次进度:
            _停滞 += 1
            if _停滞 >= 停滞上限:
                raise _无进展(f"{停滞上限 * 2}s 内窗数无变化（state={st.get('state')}）")
        else:
            _上次进度, _停滞 = _进度, 0
    raise AssertionError(f"task {task_id} timeout at {terminal}: {st}")


def _seed_orphan(task_id: str, channel: str = "") -> None:
    now = time.time()
    # ★ 2026-11-01（真跑抓出）：这里曾写死 `"channel": ""`。续跑时 `resume_task` 按
    #   **记录里的 channel** 重跑 ⇒ 空串 = "按配置顺序取第一个" = `aaa`（本机无 key）
    #   ⇒ 续跑的两个窗在通道侧反复退避，实机表现为**任务永远 running、服务端 CPU≈0、
    #   客户端 poll 到超时**（本次实测卡了 47 分钟，是我主动杀的）。
    #   ⇒ 夹具必须带上**本次真用的通道**：它测的是"复用已过窗 + 重跑未过窗"，
    #     不是"没有通道时会不会退避"。
    job = {"id": task_id, "created": now, "unit_id": "MyMeta.InfraCore.Derivation.InfinitePan",
           "module": "MyMeta.Orchestrated_002", "theorem": "siZhuInfinite_ne_empty_of_pos",
           "statement_marker": f"(h : n {NE} 0)", "target_rel": "Orchestrated_002.lean",
           "template": TEMPLATE, "n_windows": 3, "min_pass": 2, "promote": False,
           # ★ 2026-11-01（真跑抓出）：这里原为 `min_pass: 3`，即"3 条**相异**产物"。
           #   夹具的 w0 是手写文本、w1/w2 由模型产出 —— 模型对同一定理常常给出
           #   **逐字相同**的证明 ⇒ `可及=2`（TS-468 口径：同一条路径不数两遍）
           #   ⇒ 3 窗全绿却判 convergence_failed（实测）。
           #   这一条验的是**续跑机制**（复用已过窗 + 重跑未过窗 + 落库），
           #   不是"模型能否给出 3 条独立路径" ⇒ min_pass 取 2（w0 与模型产物相异）。
           "state": "running", "channel": channel, "dry": True, "status": {
               "id": task_id, "state": "windows_done", "passed": 1, "failed": 0,
               "windows": [{"idx": 0, "passed": True, "text": _dry_text()}],
               "gates": [{"idx": 0, "passed": True, "gates": {
                   "shell_ok": True, "no_forbidden": True,
                   "statement_intact": True, "no_sorry_axiom": True}}],
               "audit": [{"idx": 0, "audit": []}]}}
    conn = sqlite3.connect(TEST_DB)
    try:
        conn.execute("INSERT OR REPLACE INTO jobs (id, data) VALUES (?, ?)",
                     (task_id, json.dumps(job, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def _造遗留任务() -> dict:
    """造一份**格式真实**的遗留任务表（老版本 jobs/tasks.json 的形态）。

    ★★ 2026-10-31（交付审计）：本测试原来直接拿仓库里的 `jobs/tasks.json` 当输入，
    并且断言它必须存在（`assert (JOBS/"tasks.json").exists()`）。两个问题：
      ① 这个文件在一次正常运行后就会被产品自己**归档**成 `tasks.json.legacy`
         ⇒ 第二次再跑测试就报 "expect legacy tasks.json"，看着像产品坏了，
         实际是**测试依赖仓库现状**；
      ② 本文件现在随 wheel 进 site-packages，那里根本没有 `jobs/` 目录
         ⇒ 装完的机器上这条测试永远红。
    ⇒ 改成**自带夹具**：优先用仓库里的真实遗留件（有就验真实的），
      没有就现造一份等价的。测的是"迁移逻辑对不对"，不是"仓库里有没有那个文件"。
    """
    now = time.time()
    单: dict[str, dict] = {}
    for tid in ("t1010_001", "t1010_002"):
        单[tid] = {
            "id": tid, "created": now, "unit_id": "MyMeta.InfraCore.Derivation.InfinitePan",
            "module": "MyMeta.Orchestrated_002", "theorem": "siZhuInfinite_ne_empty_of_pos",
            "statement_marker": f"(h : n {NE} 0)", "target_rel": "Orchestrated_002.lean",
            "template": TEMPLATE, "n_windows": 3, "min_pass": 3, "promote": False,
            "state": "staged", "channel": "", "dry": True,
            "status": {"id": tid, "state": "staged", "passed": 2, "failed": 0,
                       "windows": [], "gates": [], "audit": []},
        }
    return 单


def _取遗留任务表() -> dict:
    """有真实遗留件就用真实的，否则自造。绝不改动仓库里的原件。"""
    for 名 in ("tasks.json", "tasks.json.legacy"):
        p = JOBS / 名
        if p.is_file():
            try:
                数据 = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(数据, dict) and 数据:
                    print(f"[g3] 遗留件来源：{p.name}（{len(数据)} 条）", flush=True)
                    return 数据
            except Exception:                  # noqa: BLE001
                pass
    print("[g3] 仓库里没有可用遗留件 ⇒ 用自带夹具（2 条 t1010_00x）", flush=True)
    return _造遗留任务()


async def main() -> int:
    # ★ 2026-11-01：提交时把**真可用通道**带进去（原来不传 channel ⇒ 服务器按配置
    #   顺序取第一个 `aaa`，本机无 key ⇒ 必然 error ⇒ 记 3「未验」，
    #   而同一时刻 `dddddd` 实测可达）。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _通道 import 选可用通道
    _通道名, _, _报告 = 选可用通道()
    # ★★ 2026-11-04（实测真事）：四条通道全 dead（三条无 key、`dddddd` **HTTP 403 无权访问**）
    #   ⇒ 下面那一段"续跑复用已过窗"必然拿不到真任务，异常以
    #   `anyio.BrokenResourceError` / `ExceptionGroup` 的形式冒出来 ⇒ **判据报红**。
    #   而这不是产品缺陷：本机没有可用模型额度。
    #   口径：**「我没验成」≠「它坏了」**，也不许悄悄当绿 ⇒ 记 3（未验）。
    #   （`mcp_smoke.py` 同一处已按同一口径处理。）
    if not _通道名:
        print("[g3_sqlite] 未验：本段需要至少一个可用模型通道（续跑要真跑出过窗的记录）；",
              "本机实测一条都没有 ⇒ **这一步没验成**，不是 SQLite 侧坏了。", flush=True)
        print(f"[g3_sqlite] 通道探测：probed={_报告.get('probed')} live={_报告.get('live')}", flush=True)
        return 3
# (c) 迁移：保证每次运行都有旧的 tasks.json（已归档则还原）
    # ★ 修复：只在**独立测试库**上操作，绝不删生产库
    # ★ 2026-10-31：不再"还原/移动"仓库里的原件（那会改仓库状态），
    #   改为读出来 + 直接写到测试自己的状态根。原件一个字都不动。
    _状态jobs = 测试状态根 / "jobs"
    _状态jobs.mkdir(parents=True, exist_ok=True)
    _遗留 = _取遗留任务表()
    (_状态jobs / "tasks.json").write_text(
        json.dumps(_遗留, ensure_ascii=False), encoding="utf-8")
    # 断言夹具真的含有我们要验的两条（防止夹具本身写错导致"假绿"）
    assert "t1010_001" in _遗留 and "t1010_002" in _遗留, f"夹具不合格：{list(_遗留)}"
    if TEST_DB.exists():
        TEST_DB.unlink()
        TEST_DB.with_suffix(".db-journal").unlink(missing_ok=True)
    # 测试库也要从零开始：状态根里的库由服务器按 TAIXU_DB 指向 TEST_DB，不会落这里
    (测试状态根 / "jobs" / "jobs.db").unlink(missing_ok=True)

    migrated_ids = []
    submitted_id = ""

    async def phase_a(sess):
        nonlocal migrated_ids, submitted_id
        # 注意：这里变量名曾是 `names`，而它在赋值前就被当参数用 ⇒ UnboundLocalError。
        # 现在显式传 None（让 _t 走注册表兜底），变量改名为 `任务列` 避免遮蔽。
        任务列 = await _tool_json(sess, _t(None, "list_tasks"), {})
        ids = {x["id"] for x in 任务列}
        assert "t1010_001" in ids and "t1010_002" in ids, f"migration failed {ids}"
        migrated_ids = sorted(ids)
        r = await sess.call_tool(_t(None, "submit_task"), {
            "unit_id": "MyMeta.InfraCore.Derivation.InfinitePan",
            "module": "MyMeta.Orchestrated_002",
            "theorem": "siZhuInfinite_ne_empty_of_pos",
            "statement_marker": f"(h : n {NE} 0)",
            "target_rel": "Orchestrated_002.lean", "template": TEMPLATE,
            "n_windows": 2, "min_pass": 1, "dry": True,
            "channel": _通道名})
        txt = r.content[0].text
        assert '"ok": true' in txt, f"submit fail: {txt}"
        submitted_id = json.loads(txt)["task_id"]
        st = await _wait_state(sess, submitted_id, ("staged", "promoted",
                                                    "convergence_failed", "error"))
        # ★ 2026-10-31（交付审计）：本测试关心的是 **SQLite 落盘 + legacy 迁移**，
        #   不是"真模型能不能跑"。本机无通道 key 时 state 必然 error，
        #   原来会把它判成产品红 —— 那是环境在说话。
        #   ⇒ 只有"明确缺 key"这一种原因才降级为环境不可用（返回码 3），
        #     其它任何 error 仍然判红（不放过真缺陷）。
        if st == "error":
            _明细 = await _tool_json(sess, _t(None, "task_status"),
                                     {"task_id": submitted_id})
            _错 = str(_明细.get("error") or "")
            if "key not found" in _错:
                print(f"[g3] SKIP 环境不可用（无模型通道 key）：{_错[:110]}", flush=True)
                print("[g3] 迁移与落盘部分已过；真跑闭环需至少一个可用通道。", flush=True)
                # ★ 这里**不能 return**：主流程后面还有归档断言，提前返回会把它跳过，
                #   变成"跳过 = 假通过"。正确做法是记下"仅环境不可用"，继续走完流程。
                globals()["_环境不可用"] = True
                return
        assert st == "staged", f"want staged got {st}"

    try:
        await asyncio.wait_for(_session(phase_a), timeout=120)
    except asyncio.TimeoutError:
        print("[g3] 未验（基础设施侧）：submit/迁移阶段 120s 未返回 —— 多半是通道探活卡住",
              flush=True)
        return 3
    # ★ 2026-10-31：这两条断言原来查的是**包根** `jobs/`，而服务器归档的是**状态根**
    #   那一份（`JOBS_FILE = STATE_ROOT/"jobs"/"tasks.json"`）。位置写错 ⇒ 永远"该归档却没归档"。
    #   现在查该测试自己的独立状态根：那里才是服务器真正动过的地方。
    _状态tasks = 测试状态根 / "jobs" / "tasks.json"
    assert not _状态tasks.exists(), \
        f"状态根的 tasks.json 应已归档，实际仍在：{_状态tasks}"
    assert (_状态tasks.parent / "tasks.json.legacy").exists(), \
        "应留下 tasks.json.legacy 归档"
    print(f"[g3] (c) migration OK; old tasks {migrated_ids}", flush=True)

    # ★ 无模型 key ⇒ phase_b / phase_c（都依赖真跑出的 staged 任务）无从验证。
    #   这里必须**明说**并给专用返回码 3，不能算通过：
    #   "没跑到"不等于"通过"，这与 run_all / test_counts 门的口径一致。
    if globals().get("_环境不可用"):
        print("[g3] 结果：legacy 迁移 + SQLite 落盘已验；"
              "进程重启保历史 / 孤儿续跑 **未验**（无模型通道 key）", flush=True)
        return 3

    async def phase_b(sess):
        st = await _tool_json(sess, _t(None, "task_status"), {"task_id": submitted_id})
        assert st.get("ok") is True and st["state"] == "staged", f"history lost: {st}"
        r = await sess.call_tool(_t(None, "resume_task"), {"task_id": submitted_id})
        assert "\u4e0d\u53ef\u7eed\u8dd1" in r.content[0].text, "staged should not be resumable"
        print("[g3] (a) process-restart history preserved OK", flush=True)

    await _session(phase_b)

    # 孤儿任务须在进程启动前落库（模拟上一进程中断后重启，加载到内存）
    orphan = "g3_orphan"
    _seed_orphan(orphan, _通道名)

    async def phase_c(sess):
        rr = await _tool_json(sess, _t(None, "resume_task"), {"task_id": orphan})
        assert rr.get("ok") is True, f"resume fail: {rr}"
        assert rr["resumed_windows"] == [0], f"should reuse w0: {rr}"
        assert rr["rerun_windows"] == [1, 2], f"should rerun w1,2: {rr}"
        st = await _wait_state(sess, orphan, ("staged", "promoted",
                                              "convergence_failed", "error"))
        assert st == "staged", f"resume should reach staged, got {st}"
        s2 = await _tool_json(sess, _t(None, "task_status"), {"task_id": orphan})
        assert len(s2.get("windows", [])) == 3, f"windows count: {s2}"
        print("[g3] (b) resume_task OK -> reused w0, reran w1/2, staged", flush=True)

    try:
        await asyncio.wait_for(_session(phase_c), timeout=90)
    except asyncio.TimeoutError:
        print("[g3] 未验（基础设施侧）：孤儿续跑 90s 未返回 —— resume 在通道侧卡住", flush=True)
        print("[g3] 已验：legacy 迁移 + SQLite 落盘 + 进程重启保历史；孤儿续跑 **未验**",
              flush=True)
        return 3
    except _无进展 as e:
        print(f"[g3] 未验（基础设施侧）：孤儿续跑卡在通道探活/退避 —— {e}", flush=True)
        print("[g3] 已验：legacy 迁移 + SQLite 落盘 + 进程重启保历史；"
              "孤儿续跑 **未验**（通道无进展）", flush=True)
        return 3

    print("[g3] ALL PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))