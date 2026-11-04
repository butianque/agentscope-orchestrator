"""判据门：judge(criterion, output) -> bool，纯机器可算，不经任何模型判定。

Predicates 注册表按名字取判据；判据是纯函数/谓词(可闭包参数化)。
硬规则：判据必须对"软判/空/含糊"返回 False（拒绝），绝不模棱两可。
"""
from __future__ import annotations

import logging
import re
import subprocess
from typing import Callable

logger = logging.getLogger("judge")

Predicate = Callable[[str], bool]

_REGISTRY: dict[str, Callable[..., bool]] = {}
_AUDIT: list[dict] = []  # 判据调用审计日志


def register(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def judge(criterion: dict | str, output: str = "") -> bool:
    """执行判据门并记审计。criterion 形如 {"pred": name, **args} 或直接话名。"""
    if isinstance(criterion, str):
        name, kwargs = criterion, {}
    else:
        criterion = dict(criterion)
        name = criterion.pop("pred")
        kwargs = criterion
    fn = _REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"unknown predicate: {name}")
    result = bool(fn(output, **kwargs))
    _AUDIT.append({"pred": name, "kwargs": kwargs, "output": output[:200], "result": result})
    return result


def audit_log() -> list[dict]:
    return list(_AUDIT)


@register("non_empty")
def _non_empty(output: str) -> bool:
    return bool(output and output.strip())


@register("exact_int")
def _exact_int(output: str, expected: int) -> bool:
    """输出中提取首个整数，等于 expected 才 True；提取不到则 False。"""
    m = re.search(r"-?\d+", output or "")
    if not m:
        return False
    try:
        return int(m.group(0)) == int(expected)
    except (ValueError, TypeError):
        return False


@register("contains_keyword")
def _contains_keyword(output: str, keyword: str) -> bool:
    return bool(output) and keyword in output


@register("shell_ok")
def _shell_ok(output: str, cmd, timeout: int = 120, cwd: str | None = None) -> bool:
    """子进程 exit 0 即通过。用于可机算硬证（如项目的构建/检查命令）。

    ★★ 2026-11-04 实测修了三处（原实现把三类完全不同的结局**压成一个 False**）：

    ① **`cmd` 是列表时 `shell=True` 会跑错命令。**
       平台把列表拼成一行、交给 shell 重新分词 ⇒ 含空格/反斜杠的路径被拆开。
       实测同一文件同一条 `['lake','env','lean',<路径>]`：
         `shell=True`  ⇒ **0.6 秒 rc=1**，报「unknown module prefix」（找不到顶层模块）
         `shell=False` ⇒ **3.9 秒 rc=0**
       口径：**列表就是 argv，不许再过一道 shell；字符串才交给 shell。**

    ② **`cwd` 被整个丢掉了。**
       门（`gate_spec`）本来就声明 `cwd` 并按它执行；这里却一律在**进程 cwd**
       跑 ⇒ 依赖工作目录的命令（编译器、包管理器）在错目录里失败，
       而结论长得跟"产物坏了"一模一样。现按 `cwd` 参数执行。

    ③ **命令跑不起来（环境）被吞成"不通过（产物坏了）"且零留痕。**
       `except Exception: return False` 不分"二进制不存在""超时""权限"，
       也不写任何证据 ⇒ 那种红**无法诊断**，只能反复重跑碰运气。
       现在三类都往 `_AUDIT` 留一条带 `异常/原因` 的记录：
       判据可以仍然返回 False（**拒判**是对的），但**不许红得看不见**。
    """
    import os as _os                                               # noqa: PLC0415
    import shutil as _shutil                                       # noqa: PLC0415

    def 记(原因: str, **kw) -> bool:
        _AUDIT.append({"pred": "shell_ok", "cmd": cmd, "cwd": cwd,
                       "原因": 原因, **kw, "result": False})
        return False

    是表 = isinstance(cmd, (list, tuple))
    命令 = [str(x) for x in cmd] if 是表 else cmd
    # ① 列表 ⇒ argv（不经 shell）；字符串 ⇒ 交给 shell（保持旧语义）
    壳 = not 是表
    # ② 先判"这个命令到底存不存在"，好把「环境缺件」与「命令跑了但失败」分开
    if 是表 and 命令:
        头 = 命令[0]
        if (_os.sep in 头 or "/" in 头) and not _os.path.exists(头):
            return 记("二进制不存在（给了路径但文件不存在）", 路径=头)
        if (_os.sep not in 头 and "/" not in 头) and _shutil.which(头) is None:
            return 记("二进制不在 PATH 里（环境缺件，不是产物坏了）", 二进制=头)
    try:
        proc = subprocess.run(命令, shell=壳, cwd=(cwd or None),
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 记(f"超时（>{timeout}s）—— **没验成**，不是它坏了", 超时=timeout)
    except FileNotFoundError as e:
        return 记(f"无法执行（FileNotFoundError）：{e}", 异常=type(e).__name__)
    except OSError as e:
        return 记(f"无法执行（OSError）：{e}", 异常=type(e).__name__)
    except Exception as e:                                         # noqa: BLE001
        return 记(f"无法执行（{type(e).__name__}）：{e}", 异常=type(e).__name__)
    if proc.returncode == 0:
        return True
    出 = (proc.stdout or b"") + (proc.stderr or b"")
    _AUDIT.append({"pred": "shell_ok", "cmd": cmd, "cwd": cwd,
                   "原因": f"命令真跑了但非零退出（rc={proc.returncode}）",
                   "rc": proc.returncode,
                   "stderr": 出.decode("utf-8", "replace")[-600:], "result": False})
    return False


@register("grep_unmatched")
def _grep_unmatched(output: str, path: str, token: str, encoding: str = "utf-8") -> bool:
    """文件里 forbidden token 出现次数==0 即通过。用于 '0 sorryAx' 类检查。"""
    try:
        text = open(path, encoding=encoding).read()
        return text.count(token) == 0
    except Exception:  # noqa: BLE001 - 文件不可读视为不通过
        return False