# -*- coding: utf-8 -*-
"""语法门判据力探针：**放一个真语法错的模块进去 ⇒ 门必须变红**。

── 为什么探针非有不可 ──

  语法门第一次跑就是 **54/54 全绿** —— 而**全绿什么也证明不了**：
  它可能真的全对，也可能**门自己没在查**（例如把异常吞了、目录扫空了）。
  本库纪律 R7：**无齿的门比没有门更坏**（它教人忽略红）。

── 探针怎么做 ──

  在临时目录里造三份文件，逐场景跑**真的** `语法门.py`（用 argv 指目录）：
    ① 一份**正常**模块 ⇒ 门必须**绿**（反自证：防门恒红）；
    ② 一份**真语法错**（`f(a=1, x)` —— 位置实参跟在关键字实参后）⇒ 门必须**红**，
       且必须**点名那个文件**；
    ③ 一份**缩进崩**（`if True:` 后没有缩进块）⇒ 门必须**红**；
    ④ 空目录 ⇒ 门必须**绿**但报告 `0/0`（**不得**把"什么都没扫到"当成通过
       —— 那正是本库"没跑看起来像绿"的病；这里如实打印 `0/0`，
       并且**总量为 0 时另行判红**）。
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
GATE = Path(__file__).resolve().parent / "语法门.py"

好模块 = "def f(x):\n    return x + 1\n"
语法错 = "def f(a, b=1):\n    pass\n\nf(a=1, x)\n"        # 位置实参跟在关键字实参后
缩进崩 = "def g():\n    if True:\n    return 1\n"          # 缩进块缺失


def 跑(文件表: dict[str, str]) -> tuple[int, str]:
    d = Path(tempfile.mkdtemp(prefix="syn_"))
    try:
        for 名, 内容 in 文件表.items():
            (d / 名).write_text(内容, encoding="utf-8")
        cp = subprocess.run([sys.executable, str(GATE), str(d)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=180)
        return cp.returncode, (cp.stdout or "") + (cp.stderr or "")
    finally:
        shutil.rmtree(d, ignore_errors=True)


fails: list[str] = []

# ① 反自证：正常模块 ⇒ 必须绿
rc, 出 = 跑({"好.py": 好模块})
if rc != 0:
    fails.append(f"① 正常模块竟被判红（门恒红）：{出.strip()[-200:]}")
else:
    print("PASS ① 正常模块 ⇒ 门绿（反自证：门不是恒红）")

# ② 真语法错 ⇒ 必须红 + 点名文件
rc, 出 = 跑({"好.py": 好模块, "坏.py": 语法错})
if rc == 0:
    fails.append("② ★ 门无齿：放了一个**真语法错**的模块进去，门**仍全绿**")
elif "坏.py" not in 出:
    fails.append(f"② 门红了但**没点名** `坏.py`（红错地方 = 无齿）：{出.strip()[-200:]}")
else:
    print("PASS ② 真语法错 ⇒ 门红且点名 `坏.py`")

# ③ 缩进崩 ⇒ 必须红
rc, 出 = 跑({"缩进.py": 缩进崩})
if rc == 0:
    fails.append("③ ★ 门无齿：缩进崩的模块竟通过")
else:
    print("PASS ③ 缩进崩 ⇒ 门红")

# ④ 空目录 ⇒ 必须**红**（"什么都没扫到"不得当成通过）
rc, 出 = 跑({})
if rc == 0:
    fails.append("④ ★ 空目录被当成通过 ⇒ 与「没跑看起来像绿」是同一个病")
else:
    print("PASS ④ 空目录 ⇒ 门红（不得把「什么都没扫到」当成通过）")

print()
if fails:
    print("FAIL")
    for f in fails:
        print("  ✗", f)
    raise SystemExit(1)
print("语法门探针：全部 PASS（真语法错必红且点名 · 缩进崩必红 · 空目录必红）")
