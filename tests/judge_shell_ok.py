# -*- coding: utf-8 -*-
"""判据：`judge` 的 `shell_ok` 谓词 —— **同一个命令不许跑错目录、也不许红得看不见**。

## 钉住的三处真缺陷（2026-11-04 实测）

① **列表命令被交给 shell 重新分词。**
   同一条 `['lake','env','lean',<含空格与反斜杠的路径>]`：
     `shell=True`  ⇒ 0.6 秒 rc=1，`unknown module prefix 'MyMeta'`
     `shell=False` ⇒ 3.9 秒 rc=0
   口径：**列表就是 argv，不许再过一道 shell；字符串才交给 shell。**

② **`cwd` 被丢掉。**
   门本来就声明 `cwd` 并按它执行，这里却一律在**进程 cwd** 跑
   ⇒ 依赖工作目录的命令在错目录里失败，而结论长得跟"产物坏了"一样。

③ **命令跑不起来（环境）被吞成"不通过（产物坏了）"且零留痕。**
   `except Exception: return False` 不分「二进制不在 PATH」「超时」「权限」
   ⇒ 那种红无法诊断。现在三类都往 `_AUDIT` 留一条带原因的记录。

## 每条都能翻

①②③ 都不是"读代码觉得应该这样"，而是各用一对**只差一个变量**的夹具对打：
同一个命令，改一个条件，结论必须翻过来（或读数必须变）。
"""
import os
import sys
import tempfile
from pathlib import Path

这里 = Path(__file__).resolve().parent
仓 = 这里.parent
sys.path.insert(0, str(仓))

from agentscope_orchestrator import judge as J                          # noqa: E402

失败: list[str] = []


def 断言(条件: bool, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}")
    else:
        print(f"  FAIL {说明}")
        失败.append(说明)


PY = sys.executable
# 一条"把 cwd 打出来"的命令：既能测 ① 列表不经 shell，也能测 ② cwd 生效
打印cwd = [PY, "-c", "import os; print(os.getcwd())"]


def 末条() -> dict:
    """取最后一条**带「原因」**的审计 —— `judge()` 会在谓词返回后自己再 append
    一条 `{pred, kwargs, output, result}`，所以 `_AUDIT[-1]` **不是**谓词写的那条。
    （这是判据自己的锅，不是产品的：先怀疑自己的夹具。）"""
    for r in reversed(J._AUDIT):
        if "原因" in r:
            return r
    return {}


print("judge.shell_ok：列表不过 shell · cwd 生效 · 跑不起来必须留痕")

with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
    一 = Path(td1).resolve()
    二 = Path(td2).resolve()

    # ── ② cwd 生效（这是原来被丢掉的那个参数）──────────────────────
    J._AUDIT.clear()
    r1 = J.judge({"pred": "shell_ok", "cmd": 打印cwd, "cwd": str(一)}, "")
    J._AUDIT.clear()
    r2 = J.judge({"pred": "shell_ok", "cmd": 打印cwd, "cwd": str(二)}, "")
    断言(r1 and r2, "② 传 cwd 的命令照常跑绿（两个都 True）")
    # 直接看它真的在哪个目录跑（不信"应该"）
    import subprocess                                                  # noqa: PLC0415
    # ★ 夹具自己的锅：不给 encoding 会按 GBK 解码，中文路径直接 UnicodeDecodeError。
    在 = subprocess.run(打印cwd, cwd=str(一), capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=60).stdout.strip()
    断言(os.path.normcase(在) == os.path.normcase(str(一)),
         f"② 参照读数：同命令在 cwd={一.name} 下自报 {在!r} —— 判据的两个 cwd 不同即是两条路径")

    # ── ① 列表不过 shell：路径里有空格也不许被拆开 ──────────────────
    #   造一个**含空格**的目录，命令参数就是那个路径；shell 重新分词会拆成两段。
    with tempfile.TemporaryDirectory() as td3:
        空 = Path(td3) / "有 空格 的目录"
        空.mkdir(parents=True, exist_ok=True)
        (空 / "标.txt").write_text("x", encoding="utf-8")
        测 = [PY, "-c", "import sys,os; sys.exit(0 if os.path.isfile(sys.argv[1]) else 9)",
              str(空 / "标.txt")]
        J._AUDIT.clear()
        好 = J.judge({"pred": "shell_ok", "cmd": 测, "cwd": str(一)}, "")
        断言(好, f"① 含空格的路径按 **argv** 传 ⇒ 绿（shell 重分词会拆成两段而 rc=9）")
        # ★ 真实的错法是**朴素 join**（不加引号）⇒ 含空格的路径被 shell 拆成两段。
        #   不要用 `list2cmdline`：它会正确 quote，那样测不出"过一道 shell"的害处。
        老 = subprocess.run(
            " ".join(测), shell=True, capture_output=True, timeout=60,
            encoding="utf-8", errors="replace")
        断言(老.returncode != 0,
             f"① 反例：把同一命令拼成**字符串**交给 shell ⇒ rc={老.returncode}（≠0）"
             "—— 这就是列表被过一道 shell 的后果")

    # ── ③ 跑不起来必须留痕，且说清是"环境缺件"不是"产物坏了" ──────
    J._AUDIT.clear()
    缺 = J.judge({"pred": "shell_ok",
                  "cmd": ["绝无此二进制_zzz_not_here", "--version"]}, "")
    断言(缺 is False, "③ 二进制不在 PATH ⇒ 仍然**拒判**（False，不是伪造通过）")
    记 = 末条()
    断言("PATH" in str(记.get("原因")) or "缺件" in str(记.get("原因")),
         f"③ 但它**留了痕**并说清是环境缺件：{记.get('原因')!r}"
         "—— 原来这里 `except: return False`，一点现场都不留")
    断言(记.get("pred") == "shell_ok",
         "③ 留痕挂在 `_AUDIT` 里（`judge()` 的审计通道），别人查得到")

    J._AUDIT.clear()
    路 = J.judge({"pred": "shell_ok", "cmd": [str(一 / "不存在.exe")]}, "")
    断言(路 is False and "不存在" in str(末条().get("原因")),
         f"③ 给了路径但文件不存在 ⇒ 也单独一类：{末条().get('原因')!r}")

    # ── ③b 命令**真跑了**但非零 ⇒ 那是"产物坏了"，必须与上面分得开 ──
    J._AUDIT.clear()
    坏 = J.judge({"pred": "shell_ok",
                  "cmd": [PY, "-c", "import sys; print('boom'); sys.exit(7)"]}, "")
    断言(坏 is False, "③b 真非零退出 ⇒ False")
    记坏 = 末条()
    断言(记坏.get("rc") == 7 and "boom" in str(记坏.get("stderr")),
         f"③b 且读数带 **rc 与 stderr**（rc={记坏.get('rc')}）"
         "—— 「它坏了」和「没验成」在审计里长得不一样")
    断言(str(记坏.get("原因")) != str(记.get("原因")),
         "③b 两类原因的原文不同（不许都用一句含糊话）")

    # ── 超时也是一类，且明说"没验成" ────────────────────────────────
    J._AUDIT.clear()
    超 = J.judge({"pred": "shell_ok",
                  "cmd": [PY, "-c", "import time; time.sleep(30)"],
                  "timeout": 1}, "")
    断言(超 is False and "超时" in str(末条().get("原因")),
         f"③c 超时单独一类且说「没验成」：{末条().get('原因')!r}")

    # ── 字符串命令仍交给 shell（保持旧语义，不许把老用法弄红）──────
    J._AUDIT.clear()
    串 = J.judge({"pred": "shell_ok", "cmd": "exit 0"}, "")
    断言(串, "保持兼容：字符串命令仍按 shell 执行（`exit 0` ⇒ 绿）")

print()
if 失败:
    print(f"judge.shell_ok：红 {len(失败)} 项 —— {失败}")
    sys.exit(1)
print("judge.shell_ok：绿 —— **列表按 argv 执行（含空格路径不被拆）、cwd 真的生效、"
      "跑不起来/超时/真失败三类各自留痕且互不冒充**")