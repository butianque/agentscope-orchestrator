# -*- coding: utf-8 -*-
"""夹具：一个**起孙进程并让孙进程持有 stdout** 的程序。

它模拟真实事故里的 `lake env lean`：

    观察者 ──管道──> 夹具(lake) ──继承同一根管道──> 孙进程(lean)

夹具自己也会睡，孙进程睡得更久。这样两种写法会走出**不同**的结局：

· `subprocess.run(..., capture_output=True, timeout=T)`：
  超时到 ⇒ 杀夹具 ⇒ **再 `communicate()` 收输出** ⇒ 孙进程还开着管道写端
  ⇒ 读不到 EOF ⇒ **永远不返回**（这就是那次 CI 挂 26 分钟的原因）。
· `subproc_safe.跑(..., timeout=T)`：
  输出走**临时文件**（不靠 EOF）⇒ 超时 ⇒ `taskkill /F /T` 杀**整棵树** ⇒ 按时返回。

孙进程的 pid 写到 argv[1]，好让判据能核实"整棵树真的死了"（不是杀完还留着孤儿）。
"""
import subprocess
import sys
import time

pid文件 = sys.argv[1]

孙 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
# 孙进程**不重定向 stdout** ⇒ 它继承夹具的 stdout（也就是观察者那根管道）⇒ 它活着，管道就不关。
with open(pid文件, "w", encoding="utf-8") as f:
    f.write(str(孙.pid))

time.sleep(60)
print("夹具正常结束")
