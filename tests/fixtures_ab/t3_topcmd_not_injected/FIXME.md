# FIXME（实验方看的，跑之前请删掉/藏起来）

- **病根**：`assemble.py::装配` 只给 `regression_parity` 门补了 `baseline_cmd`，**没补 `cmd`**；
  而 `runner.py` 校验门时要求这一门型「两条命令都在」⇒ 顶层命令在顶层、门里只有 `baseline_cmd` 时，
  执行前校验直接抛 `未知|regression_parity 需要 baseline_cmd 与 cmd 两条命令`。
- **正确修法**：装配时对该门型做**缺省补齐**（缺才补，不覆盖调用方显式给的值）：
  `if not 门.get("cmd"): 门["cmd"] = list(顶层cmd)`。
- **为什么光读代码不容易看出来**：缺陷是**少写一行**（缺席），不是写错；视线容易被旁边那行
  `baseline_cmd` 补齐带走，以为「补齐逻辑已经在这儿了」；契约（`runner.py` 要求两条命令）
  和实现（`assemble.py` 只补一条）在两个文件里，得对着看才连得上。

## 跑法
- 门命令（仓内·无第三方依赖）：`python run_tests.py`（cwd = 本目录）。
- 门命令（仓内·按原设计）：`python -m pytest -q tests/test_assemble.py`
  —— 需要装了 pytest 的解释器（本机 3.10.11 `E:\huanjing\python.exe`）；编排器的门会改写成
  3.12.10（`E:\基础设施\orch-deploy\.venv\Scripts\python.exe`），**那只没装 pytest**。
- 门命令（仓外、防篡改，推荐）：`python ..\_gates\t3_gate.py <本目录绝对路径>`。
- 秒级：单次 `run_tests.py` ≈ 0.05 s，无 sleep、无联网。
