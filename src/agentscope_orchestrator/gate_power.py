"""门区分力度量（2026-11-01：给"过门 ≠ 改得对"一个**数字**，而不是一句免责声明）。

★ 要解决的问题
  `orch_fix` 的结论建立在"门绿"上，而门绿只证明**这一条判据**没意见。
  0.12.5 的真事故：模型把 `_RUST_ROOT` 改成 `Path(__file__).resolve().parents[2]`，
  门（`_selftest`，只要求"已定义 + 是真目录"）**绿了**，语义却与原意不同。
  这类事**不可能被彻底消灭**（"改得对不对"等价于自动程序验证，一般不可判），
  但它可以被**度量**：往产物里注机械性错误（变异），看门能抓住几种。

★ 判据形状（只认行为）
  对一个固定门，逐个注入变异 → 每次跑门 → **门仍绿 = 这个变异存活（门有洞）**。
  区分力 = 抓住的变异数 / 注入的变异数。存活清单点名"哪种错误能骗过这个门"。

★ 诚实边界（写进返回值）
  · 变异是**机械的**（换返回值/翻条件/删断言/插 `sys.exit(0)`/清空函数体），
    **不是**语义级恶意改写 ⇒ 区分力高只说明"挡得住这类机械错误"，
    不说明"改得对"（真正的语义等价性没有廉价判据）。
  · 只在**临时副本**上做，不动调用方的仓。
  · 只支持 Python 源（其它语言的变异规则各异，没做就如实说）。
"""
from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from pathlib import Path

_跳 = {".git", "target", "node_modules", "__pycache__", ".venv", "dist", "build",
       ".pytest_cache", ".mypy_cache", ".orchestrator", ".orchestrator-state",
       "products", ".audit_trail"}


def _是虚拟环境(p: Path) -> bool:
    """按**目录标记**认虚拟环境，不按名字。

    ★ 实测（2026-11-01）：不按标记认的话，一个叫 `_probe5` 的临时 venv 会被整棵复制，
      光是复制就 >600 s —— 度量工具自己被"仓里有个 venv"拖死。
      标记是 `pyvenv.cfg`（`python -m venv` 必然生成），比维护名字白名单可靠。
    """
    try:
        return (p / "pyvenv.cfg").is_file()
    except OSError:
        return False


def _跳过目录(p: Path) -> bool:
    return p.name in _跳 or _是虚拟环境(p)


def _树规模(根: Path, 上限文件: int) -> tuple[int, int, bool]:
    """快速统计"会被复制的那部分"规模：(文件数, 字节, 是否超上限就早停)。

    ★ 为什么不用 `rglob`：本包仓里躺着 `_probe5/` 这种临时 venv（上万文件），
      `rglob("*")` 一次就把工具拖死（实测 >600 s 仍没返回）。
      这里用 `scandir` 逐层走并在**遇到虚拟环境/构建目录时整枝跳过**，
      加上"超过上限立即返回"，保证本工具在大仓上**快速失败**而不是慢慢挂住。
    """
    n = 0
    字节 = 0
    for 目录, 子们, 文们 in __import__("os").walk(根):
        d = Path(目录)
        if d != 根 and _跳过目录(d):
            子们[:] = []
            continue
        for f in 文们:
            n += 1
            try:
                字节 += (d / f).stat().st_size
            except OSError:
                pass
            if n > 上限文件:
                return n, 字节, True
    return n, 字节, False


def _候选文件(根: Path, 上限: int = 12) -> list[Path]:
    """挑要变异的源文件：优先非测试的业务代码（变异测试代码本身意义不大）。"""
    业: list[Path] = []
    测: list[Path] = []
    for p in sorted(根.rglob("*.py")):
        rel = p.relative_to(根)
        if any(x in _跳 for x in rel.parts):
            continue
        # 名为 `_probe5` 这类临时 venv 里的 .py 不是项目源码（按 pyvenv.cfg 认，不按名字）
        if any(_是虚拟环境(根.joinpath(*rel.parts[:i]))
               for i in range(1, len(rel.parts))):
            continue
        try:
            if p.stat().st_size > 200_000:
                continue
        except OSError:
            continue
        (测 if (p.name.startswith("test_") or p.name.endswith("_test.py")
               or "tests" in p.relative_to(根).parts) else 业).append(p)
    # ★ 业务代码优先，但**测试代码也要算**：判据常是"跑测试"，把断言删掉正是
    #   最该被抓住的那类变异（只测业务代码会漏掉它）。
    return (业 + 测)[:上限]


def _变异(源: str) -> list[tuple[str, str]]:
    """对一段 Python 源产出 (变异名, 变异后源码)：只做**能在任何真实文件上落地**的机械改。"""
    果: list[tuple[str, str]] = []
    m = re.search(r"^(\s*)return\s+(.+)$", 源, re.M)
    if m:
        果.append(("返回值置空", 源[:m.start()] + f"{m.group(1)}return None" + 源[m.end():]))
    m = re.search(r"^(\s*)assert\s+(.+)$", 源, re.M)
    if m:
        果.append(("删断言", 源[:m.start()] + f"{m.group(1)}pass" + 源[m.end():]))
    if "==" in 源:
        果.append(("相等翻成不等", 源.replace("==", "!=", 1)))
    if "!=" in 源:
        果.append(("不等翻成相等", 源.replace("!=", "==", 1)))
    if re.search(r"\breturn\s+True\b", 源):
        果.append(("True 翻 False", re.sub(r"\breturn\s+True\b", "return False", 源, count=1)))
    果.append(("顶层强制退出 0",
               源 + "\n\nimport sys as _sys\n_sys.exit(0)\n"))
    return 果


def _临时根(仓根: Path) -> tuple[Path | None, str]:
    """选"副本放哪儿"：**绝不许落在被测仓里**。

    ★ 2026-11-01 真事故（自查）：`tempfile.mkdtemp()` 用的目录在这台机器上被解析到了
      **当前工作目录**（`TEMP` 不可用时 python 会退到 cwd）。于是倍数变异把"上一轮自己的
      副本"一起复制进去 ⇒ `gp_xxx/r/gp_xxx/r/...` **14 层递归**，仓被污染、`git add` 直接
      `Filename too long` 失败。工具造污染比不做更坏。
    规则：候选目录按优先级试，**第一个不在被测仓内、且能写的**用它；都不行就如实拒绝，
    并把 `ORCH_GATE_POWER_TMP` 这个开关告诉调用方（不静默降级到"仓里"）。
    """
    import os
    import tempfile
    候: list[tuple[Path, str]] = []
    环 = os.environ.get("ORCH_GATE_POWER_TMP")
    if 环:
        候.append((Path(环), "ORCH_GATE_POWER_TMP"))
    候.append((Path(tempfile.gettempdir()), "tempfile.gettempdir()"))
    for k in ("TEMP", "TMP", "LOCALAPPDATA", "TMPDIR"):
        v = os.environ.get(k)
        if v:
            候.append((Path(v) / "orch_gp", k))
    拒: list[str] = []
    for 基, 来源 in 候:
        try:
            if 基.resolve().is_relative_to(仓根.resolve()):
                拒.append(f"{基}（{来源}，在仓内）")
                continue
            基.mkdir(parents=True, exist_ok=True)
            判 = 基 / f"_wprobe_{os.getpid()}"
            判.write_text("x", encoding="utf-8")
            判.unlink()
            return 基, 来源
        except Exception as e:                     # noqa: BLE001
            拒.append(f"{基}（{来源}，{type(e).__name__}）")
    return None, "；".join(拒)


def _跑(命令: list[str], cwd: Path, 超时: float = 300.0, 测类: bool = False) -> dict:
    """跑一条命令，取"成不成"。**测试类命令还要看计数**。

    ★ 2026-11-01 自打脸记录（本工具抓出来的）：第一版这里只认退出码。跑自己的
      `tests/gate_power.py` 就红了：变异「文件尾加 `sys.exit(0)`」被判**存活** ——
      因为被测模块一被 import 就 `exit(0)`，测试**一个都没跑**，退出码却是 0。
      这正是 `test_counts` 门存在的理由（`Ran 0 tests` / `NO TESTS RAN` 不是绿）。
      所以这里对测试类命令复用**同一个计数解析器**，且 `passed < 1` 视为红。
    """
    import subprocess
    import tempfile
    # ★ 2026-11-01（活体实测）：在**被沙箱包住的 MCP 服务进程**里，`capture_output=True`
    #   会用管道接子进程输出 —— 那个环境不让开管道，子进程起来就不返回，实测挂满 300 s 超时。
    #   改成"子进程写临时文件 + 父进程读文件"：不碰管道，语义一样（退出码 + 输出全文）。
    尾巴直 = ""
    try:
        with tempfile.TemporaryFile(mode="w+b") as 桶:
            唤 = subprocess.run([str(x) for x in 命令], cwd=str(cwd), timeout=超时,
                               stdin=subprocess.DEVNULL, stdout=桶, stderr=subprocess.STDOUT)
            桶.seek(0)
            尾巴直 = 桶.read().decode("utf-8", "replace").strip()
        合并 = 尾巴直
        ok = 唤.returncode == 0
        计 = None
        if 测类:
            from .gate_spec import _测试计数
            计 = _测试计数(合并)
            可判 = bool(计.get("可判定")) or "passed" in 计 or "ran" in 计
            if 可判 and int(计.get("passed") or 0) < 1:
                ok = False                      # 退出码 0 但一个测试都没过 ⇒ 红
        return {"ok": ok, "rc": 唤.returncode, "尾部": 合并[-500:], "计数": 计}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": -1, "尾部": f"超时（>{超时}s）", "计数": None}
    except Exception as e:                     # noqa: BLE001
        return {"ok": False, "rc": -2, "尾部": f"{type(e).__name__}: {e}", "计数": None}


async def 测区分力(仓: str | Path, 命令: list[str], 变异数: int = 6,
                   cwd: str | None = None) -> dict:
    """返回 {区分力, 抓住, 存活, 变异明细, 边界}。**不改调用方的仓**（临时副本）。"""
    from agentscope_orchestrator.gate_spec import 像测试命令

    根 = Path(仓).resolve()
    if not 根.is_dir():
        return {"ok": False, "error": f"repo 不是目录：{根}"}
    给 = list(命令 or [])
    if not 给:
        return {"ok": False, "error": "缺 cmd"}
    测类 = 像测试命令(给)

    # ★ 复制前先量规模：本工具要在**副本**上变异，仓一大（有 venv/产物/大二进制）
    #   复制本身就会把调用方钉住。宁可**快速拒绝并给准确做法**，也不慢慢挂住。
    import os as _os
    上限 = int(_os.environ.get("ORCH_GATE_POWER_MAX_FILES", "4000"))
    n, 字节, 超 = _树规模(根, 上限)
    if 超:
        return {"ok": False, "分类": "参数/前提不成立",
                "error": (f"仓太大：可复制范围内 > {上限} 个文件（已数到 {n}，"
                          f"{字节 / 1e6:.1f} MB）⇒ 变异度量会先把时间花在复制上"),
                "修法": {"工具": "gate_power",
                         "参数": {"repo": "<子工程根，如 <仓>/src 或某个子包>",
                                  "cmd": list(给)}},
                "或": f"确要整仓度量就调高 ORCH_GATE_POWER_MAX_FILES（当前 {上限}）"}

    # ★ 2026-11-01（实测踩到）：这里原来用完整评估器 `rt.evaluate_async` 跑每一次变异。
    #   评估器每次都要在这份副本上建**引擎源码索引**（本包实测 1700 units）——
    #   与"这条命令成不成"毫无关系，却让 2 次变异 >600 s。度量只需要**命令的退出码**，
    #   所以改成直接起子进程：快 100 倍，语义不变（0 = 过，非 0 = 红）。
    基 = _跑(给, 根, 测类=测类)
    if not 基["ok"]:
        return {"ok": False, "分类": "参数/前提不成立",
                "error": ("基线（未变异）跑门就是红的 ⇒ 先让门在正确产物上变绿，再测区分力"
                          f"（rc={基['rc']}；{基['尾部'][:300]}）"),
                "门": {"cmd_exit_ok": False}}

    基, 基说明 = _临时根(根)
    if 基 is None:
        return {"ok": False, "分类": "参数/前提不成立",
                "error": f"找不到**仓外**可写的临时目录：{基说明}",
                "修法": {"工具": "gate_power",
                         "参数": {"repo": str(根), "cmd": 给}},
                "或": "设 ORCH_GATE_POWER_TMP=<仓外可写目录> 再调"}
    副本 = Path(tempfile.mkdtemp(prefix="gp_", dir=str(基)))
    明细: list[dict] = []

    def _忽略(目录: str, 名们: list[str]) -> set[str]:
        """复制时剪掉构建/缓存目录**和任何虚拟环境**（按 `pyvenv.cfg` 认，不按名字）。"""
        d = Path(目录)
        return {n for n in 名们
                if n in _跳 or _是虚拟环境(d / n)}

    try:
        shutil.copytree(根, 副本 / "r", dirs_exist_ok=True, ignore=_忽略)
        工 = 副本 / "r"
        文件 = _候选文件(工)
        if not 文件:
            return {"ok": False, "error": "仓里没有可变异的 .py 源（只支持 Python）"}
        for f in 文件:
            if len(明细) >= max(1, 变异数):
                break
            原 = f.read_text(encoding="utf-8", errors="replace")
            for 名, 新 in _变异(原):
                if len(明细) >= max(1, 变异数):
                    break
                if 新 == 原:
                    continue
                f.write_text(新, encoding="utf-8")
                try:
                    命 = [a.replace(str(根), str(工)) if isinstance(a, str) else a for a in 给]
                    果 = _跑(命, 工, 测类=测类)
                    存活 = bool(果["ok"])
                    因由 = (f"门仍绿 ⇒ 这个错误骗过了门（rc={果['rc']}）" if 存活
                            else f"门变红 ⇒ 抓住了（rc={果['rc']}）")
                finally:
                    f.write_text(原, encoding="utf-8")
                明细.append({"文件": str(f.relative_to(工)).replace("\\", "/"),
                           "变异": 名, "存活": 存活, "说明": 因由})
    finally:
        shutil.rmtree(副本, ignore_errors=True)

    抓住 = [d for d in 明细 if not d["存活"]]
    存活 = [d for d in 明细 if d["存活"]]
    比 = (len(抓住) / len(明细)) if 明细 else 0.0
    return {
        "ok": True,
        "区分力": round(比, 3),
        "注入": len(明细),
        "抓住": len(抓住),
        "存活": [{k: d[k] for k in ("文件", "变异")} for d in 存活],
        "变异明细": 明细,
        "临时目录": {"基": str(基), "来源": 基说明},
        "边界": ("变异是机械性的（换返回值/翻条件/删断言/顶层 sys.exit(0)）；"
                 "区分力高 ≠ 改得对 —— 语义等价性没有廉价判据；只支持 Python 源；"
                 "在临时副本上做，不动调用方的仓"),
        "建议": ("存活的变异说明这个门只认某一种信号（常见：只判退出码）。"
                 "要压低这类洞：加 grep_required / whole_reading / test_counts，"
                 "或用 instances=2 + instances_mode=copy 抓『绿只因有构建产物』"),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="门区分力度量（变异测试）")
    ap.add_argument("--repo", required=True)
    # ★ `nargs="*"` + `parse_known_args`：命令里带 `-c`/`-q` 这类**以短横线开头**的参数是常态，
    #   argparse 默认会把它当成自己的选项并报 `unrecognized arguments`（实测踩过）。
    #   所以：能认的归 argparse，剩下的原样并入 cmd（顺序保持），`--` 分隔符丢掉。
    ap.add_argument("--cmd", nargs="*", default=[])
    ap.add_argument("--mutations", type=int, default=6)
    认, 余 = ap.parse_known_args(argv)
    命令 = [x for x in 认.cmd if x != "--"]
    余 = [x for x in 余 if x != "--"]
    if 余:
        命令 = 命令 + 余
    果 = asyncio.run(测区分力(认.repo, 命令, 认.mutations))
    print(json.dumps(果, ensure_ascii=False, indent=2))
    if not 果.get("ok"):
        return 1
    return 0 if 果["区分力"] >= 0.5 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]) if (sys := __import__("sys")) else 0)
