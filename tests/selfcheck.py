"""自检本身的回归（`tests/selfcheck.py`）。

**为什么需要**：`selfcheck` 是交付后调用方**唯一**会主动跑的东西 —— 它自己坏了，
"装完一条命令就知道成不成"就是空话，而且坏法很难看（实测崩在 tempfile 上）。

这里锁四件事：
  ① 八项都绿（⑧ 真模型链在本测试里显式关掉 ⇒ 记未验；在**干净状态目录**下跑）
  ② 跑完**不在调用方的仓里留下任何目录**（曾经真留下过，还混进了提交）
  ③ `run()` 的返回结构可机读：`ok/版本/平台/耗时_s/检查[]/结论/边界`
  ④ `main()` 不会因为环境怪而崩：崩了也要给 `ok=False` + 一句可执行结论
"""

from __future__ import annotations

import json
import os
import subprocess
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(根 / "src"))

# ★★★★★ 2026-11-03：**判据自己的输出**也要 UTF-8。
#
#   真事故（本文件修完子进程编码后立刻踩到）：
#     我给子进程加了 `errors="replace"` ⇒ 坏字节变成 `\ufffd` ⇒
#     断言说明里带上了它 ⇒ `print(...)` 在 GBK 控制台上抛
#         `UnicodeEncodeError: 'gbk' codec can't encode character '\ufffd'`
#     ⇒ **判据崩在自己的输出上**，而不是被测对象上。
#
#   这是同一个坑的第二层：先修"读别人"（子进程解码），
#   再修"写自己"（本进程输出）。只修一层等于没修 —— 而且新坑更隐蔽，
#   因为它在**报错路径**上，只有真出错时才触发。
#
#   `reconfigure` 只在 TextIOWrapper 上有；被重定向到管道时也是（Python 3.7+）。
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

失败: list[str] = []


def 断言(条件: bool, 说明: str) -> None:
    if not 条件:
        失败.append(说明)
        print(f"  FAIL {说明}", flush=True)
    else:
        print(f"  ok   {说明}", flush=True)


# 「合法运行期目录」：会在包根出现、但**不是残留**的那几个。
# ★ 这份名单要**尽量短且稳定**（它是"允许出现的非仓目录"），
#   而**恰恰不能**放临时目录前缀 —— 前缀随每个新判据增长，名单必然漏（实测漏了三类共 85 个）。
_合法运行期目录 = frozenset({
    ".venv", "__pycache__", ".orchestrator-state", ".orchestrator", ".data",
    "cache", "jobs", "products", "work", "checks", "_smoke_state", "wheels",
})


def _无主目录(处: Path) -> list[str] | None:
    """包根里**既不被 git 跟踪、也不在合法运行期名单**的目录 —— 也就是残留。

    ★ 为什么不用 `git status` 判"未跟踪"（第 1 版就是那么写的，当场被自己的探针判死）：
      `.gitignore` 里声明过的临时前缀会被 git **藏起来**，而我们要抓的**正是**那些。
      「用 git status 判残留」与「把模式写进 .gitignore」**互相抵消** ——
      实测：造一个 `bl_残留门区分力_zzz/`，git-status 版的门回 `[]`，也就是说
      **它恰好对"我们刚声明为运行期产物"的那类瞎了**。
      ⇒ git 只回答「哪些是仓里的正规目录」（`ls-files`）；
        残留靠**文件系统**看（跨过 gitignore），再减去那份短名单。
    ★ 回 `None` 表示**没验成**（取不到 git），调用方必须显式打"未验"，不许当"干净"。
    """
    实际 = {x.name for x in 处.iterdir() if x.is_dir()}
    try:
        出 = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", str(处), "ls-files"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if 出.returncode != 0:
        return None
    已跟踪 = {行.split("/", 1)[0] for 行 in (出.stdout or "").splitlines() if 行}
    return sorted(实际 - 已跟踪 - _合法运行期目录)


def main() -> int:
    from agentscope_orchestrator import selfcheck

    # 干净状态目录：自检默认会自己造临时目录，这里显式给一个，跑完删掉
    临时 = Path(tempfile.mkdtemp(prefix="selfcheck-test-", dir=str(Path(__file__).parent)))
    atexit.register(shutil.rmtree, 临时, ignore_errors=True)
    # ★ 残留门基线：**本判据自己的临时目录建好之后**再记现状 ⇒ 后面只看"新增"。
    #   取早了会把它自己算成残留（实测就是这么红了一次）。
    残留前 = _无主目录(根) or []
    tests残留前 = _无主目录(根 / "tests") or []
    atexit.register(shutil.rmtree, 临时, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    旧 = os.environ.get("ORCH_STATE_DIR")
    os.environ["ORCH_STATE_DIR"] = str(临时)
    # ★ 2026-11-02：自检新增 ⑧ 真模型链（会真派模型，最多 420s）。这支测试验的是
    #   **自检自身的结构与不崩**，不是模型可用性 ⇒ 进程内一律关掉模型链，
    #   真模型链由 `orch-check` 在部署机上有 key 时自己验。
    旧模型 = os.environ.get("ORCH_SELFCHECK_MODEL")
    os.environ["ORCH_SELFCHECK_MODEL"] = "0"
    try:
        出 = selfcheck.run()
    finally:
        if 旧 is None:
            os.environ.pop("ORCH_STATE_DIR", None)
        else:
            os.environ["ORCH_STATE_DIR"] = 旧
        if 旧模型 is None:
            os.environ.pop("ORCH_SELFCHECK_MODEL", None)
        else:
            os.environ["ORCH_SELFCHECK_MODEL"] = 旧模型

    # ① 八项全绿 / ⑧ 未验单列（真模型链在本测试里显式关掉）
    检查 = 出.get("检查") or []
    断言(len(检查) == 8, f"检查项应为 8，实得 {len(检查)}")
    未验 = [x for x in 检查 if x.get("未验")]
    断言([x["检查"][:2] for x in 未验] == ["⑧ "],
         f"只有 ⑧ 可未验，实得={[x['检查'] for x in 未验]}")
    断言(all("ORCH_SELFCHECK_MODEL=0" in x["证据"] for x in 未验),
         "未验原因必须是显式关闭模型链")
    红 = [x for x in 检查 if not x["通过"] and not x.get("未验")]
    断言(not 红, f"全绿：红={[(x['检查'], x['证据'][:60]) for x in 红]}")
    断言(出.get("ok") is True, "ok=True")
    # 版本必须是**代码版**（不是残留 dist-info 的 0.1.0）
    版本项 = next((x for x in 检查 if "版本" in x["检查"]), None)
    断言(版本项 is not None and "0.1.0" not in 版本项["证据"].split("装版")[0],
         f"抬头/版本项用代码版：{版本项['证据'][:80] if 版本项 else '缺'}")

    # ② 不留家具：跑完仓根与 cwd 下都不该多出 orch-* 目录
    前 = {p.name for p in 根.iterdir() if p.is_dir()}
    出2 = selfcheck.run()
    后 = {p.name for p in 根.iterdir() if p.is_dir()}
    新增 = sorted(x for x in 后 - 前 if x.startswith("orch-"))
    断言(not 新增, f"跑完不留目录（新增={新增}）")
    断言(出2.get("ok") is True,
         "第二次跑也全绿（幂等）—— 红项：" +
         str([(x["检查"][:12], x["证据"][:160]) for x in (出2.get("检查") or [])
              if not x["通过"] and not x.get("未验")]))

    # ③ 结构可机读
    for 键 in ("ok", "版本", "平台", "耗时_s", "检查", "结论", "边界"):
        断言(键 in 出, f"返回含 {键}")
    断言(len(json.dumps(出, ensure_ascii=False)) < 8000, "结构够小（< 8KB）")
    import shutil as _shutil
    # ★★ 2026-11-04 换判法：原来只认 `orch-*` / `不存在*` 两个前缀 ⇒ **判据自己留的看不见**。
    #   实测：包根攒了 85 个临时目录（`bl_修前修后_*` / `bl_接线_*` / `kbgp_*` / `eol_*`），
    #   全部来自 `tempfile.mkdtemp(prefix=...)` 在 `%TEMP%` 不可用时**回落到 cwd**，
    #   而这道门每次都绿。**判据看不见的脏，和没脏是两件事。**
    #   ⇒ 前缀名单必然漏（新判据 = 新前缀），改问 git —— 「哪些是运行期产物」
    #     这件事 `.gitignore` 已经声明过，不该有第二份名单。
    # ★★ 2026-11-04（第二次修，因为第一版把这道门弄成了永久红）：
    #   第一版把**所有**无人目录都算红 ⇒ 兄弟判据的历史残留（`au_*`/`bc_*`/`fence_*`）
    #   会把 selfcheck 永久判红，而那不是它造成的。判据该回答的是
    #   「**我这次跑完**有没有新留下东西」⇒ 取跑前/跑后**差集**。
    #   跑前就有的只打印提示（不掩盖、也不算它头上）。
    根后 = _无主目录(根)
    tests后 = _无主目录(根 / "tests")
    if 根后 is None or tests后 is None:
        print("  · 未验 仓根无残留目录（取不到 git ⇒ **这一步没验成**，不是它干净）", flush=True)
    else:
        新增 = sorted((set(根后) - set(残留前))
                     | {f"tests/{x}" for x in (set(tests后) - set(tests残留前))})
        旧有 = sorted(set(残留前) | {f"tests/{x}" for x in tests残留前})
        if 旧有:
            print(f"  · 提示 跑之前就有的无人目录 {len(旧有)} 个（不记在本判据头上，"
                  f"但确实该清：{旧有[:5]}）", flush=True)
        断言(not 新增,
             f"本判据跑完没在仓根/tests 新留目录（实测新增：{新增[:8]}"
             + ("…" if len(新增) > 8 else "") + "）")

    # ④ 环境怪也不崩：把 ORCH_STATE_DIR 指到**真不可写**处，必须给 ok=False 而不是 traceback
    #   ★ "工作区里的某个不存在的盘"**是可写的**（实测它真的建出来了，还混进了提交）⇒
    #     用"拿文件当父目录"制造确定性失败（任何平台都建不出 `<文件>/子目录`）。
    挡板 = 根 / "_不可写挡板.txt"
    挡板.write_text("x", encoding="utf-8")
    独立cwd = tempfile.mkdtemp(prefix="selfcheck-badcwd-", dir=str(Path(__file__).parent))
    atexit.register(shutil.rmtree, 独立cwd, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    坏 = subprocess.run(
        [sys.executable, "-m", "agentscope_orchestrator.selfcheck", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, cwd=独立cwd,
        # ★★★★★ 2026-11-03 修**假故障**（真事故，不是洁癖）：
        #   原来只写 `text=True`，**没给 encoding** ⇒ Windows 上按活动代码页(GBK)解码，
        #   而子进程输出的是 UTF-8 中文 ⇒
        #     `UnicodeDecodeError: 'gbk' codec can't decode byte 0x80`
        #   异常发生在 subprocess 的**读线程**里 ⇒ 它把 stderr 置成 `None` ⇒
        #   下一行 `"Traceback" not in 坏.stderr` 抛 `TypeError: argument of type
        #   'NoneType' is not iterable`。
        #   ⇒ 结果：判据报的错**与它要判的东西毫无关系**，看起来像"自检崩了"。
        #     这就是「假故障」的教科书形态：**环境前提不成立，冒充产物不合格**。
        #   本仓 `_winutf8` 早就把这个坑记在案（doctor 入口调 `确保UTF8输出()`），
        #   但**测试自己起的子进程**没享受到 —— 同一件事两套待遇。
        #   ⇒ 显式 `encoding="utf-8"` + `errors="replace"`（不因一个坏字节丢全部输出）。
        env={**os.environ,
             # ★★★★★ 2026-11-03 第二层同一根因：**子进程的输出编码必须由判据钉死**。
             #
             #   上面只加了 `encoding="utf-8"`（修"我怎么读它"），但**没管它怎么写**
             #   —— 于是又红了一次，而且这次更阴：
             #     · `ci.py` 把本判据的 stdout 重定向到文件 ⇒ 非终端；
             #     · 子进程（`python -m agentscope_orchestrator.selfcheck --json`）
             #       于是退回活动代码页(GBK)输出那份 JSON；
             #     · 本判据按 utf-8 解码 ⇒ 中文键全成 `\ufffd` ⇒
             #       打印出来是 `{'ok': False, '�汾': '0.13.11', '��' ...}`
             #       —— **红项的名字都看不清**，我因此白追了一轮。
             #   ⇒ 结论：修"读"必须同时修"写"，两件事缺一不可；
             #     只修一半 = 仍然依赖调用方的编码环境变量（CI 恰好没设）。
             #   ⇒ 在这一层钉死两个变量，无论谁怎么起本判据，链路全程 UTF-8。
             "PYTHONIOENCODING": "utf-8",
             "PYTHONUTF8": "1",
             "ORCH_STATE_DIR": str(挡板 / "下层" / "不可写"),
             # ★ 2026-11-02：自检新增 ⑧ 真模型链（最多等 420s）。这一支测的是
             #   "环境怪也不崩"，**不该**去等上游模型 ⇒ 在测试里显式关掉模型链，
             #   否则套件会被真模型调用拖成几分钟。
             "ORCH_SELFCHECK_MODEL": "0",
             "PYTHONPATH": str(根 / "src")})
    断言("Traceback" not in 坏.stderr or 坏.returncode in (0, 1),
         "不可写状态目录：不抛 traceback")
    # ★ stdout 必须**只有** JSON：从第一个 `{` 起解析，前面的噪声算缺陷
    文 = 坏.stdout.lstrip()
    断言(文.startswith("{"), f"--json 的 stdout 以 JSON 开头（实际开头：{文[:40]!r}）")
    try:
        j, _尾 = json.JSONDecoder().raw_decode(文)
    except Exception as e:                  # noqa: BLE001
        j = {}
        print(f"  （JSON 解析失败：{e}）", flush=True)
    断言(isinstance(j, dict) and "结论" in j, f"仍给出可机读结论：{str(j)[:100]}")

    # ⑤ 自检绝不许挂：把 orch_run 换成"永不返回"，run() 必须在 ~150s 内给出红结论
    #    （不实际等 150s：把超时改小，验的是"有上限"这件事，不是具体秒数）
    from agentscope_orchestrator import selfcheck as _sc
    import asyncio as _aio

    async def _永不返回(*a, **k):
        await _aio.sleep(3600)

    原 = selfcheck.orch_run if hasattr(selfcheck, "orch_run") else None
    print("  （⑥ 硬上限：见 startswith 断言）", flush=True)
    源 = Path(selfcheck.__file__).read_text(encoding="utf-8")
    断言("timeout=150" in 源 and "subprocess" in 源,
         "⑥ 有可杀的硬边界（子进程 timeout=150）")
    段 = 源.split("def _临时目录")[1].split("def _记")[0]
    断言("tempfile.mkdtemp(" not in 段 and "候选.mkdir()" in 段,
         "临时目录自己 mkdir（不用 tempfile.mkdtemp：权限失败会重试上千个名字）")
    断言("TimeoutExpired" in 源 and "通道探活卡住" in 源 and "自检不会挂" in 源,
         "超时给的是对症修法，且明说已杀子进程（不是普通异常）")

    挡板.unlink(missing_ok=True)          # 别在仓里留东西（这条就是上一次的教训）
    import shutil as _shutil2
    _shutil2.rmtree(临时, ignore_errors=True)
    _shutil2.rmtree(独立cwd, ignore_errors=True)
    漏 = [p.name for p in 根.iterdir()
          if p.is_dir() and p.name.startswith(("selfcheck-test-", "selfcheck-badcwd-"))]
    断言(not 漏, f"用例自己不留临时目录（实测残留：{漏}）")

    print(f"\nselfcheck 回归：{'绿' if not 失败 else f'红 {len(失败)} 项'} —— "
          f"{len(失败)} 失败")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(main())
