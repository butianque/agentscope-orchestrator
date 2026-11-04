# FIXME（实验方看的，跑之前请删掉/藏起来）

- **病根**：`parse_counts.py` 优先用宽松的「数字 + 指标名」正则（`(\d+)\s+(passed|failed|skipped)`），
  它会跨过 `=` 把上一条的数值吃掉 —— 对 `passed=10 failed=3 skipped=0` 返回
  `{'passed': 0, 'failed': 10, 'skipped': 3}`；`key=值` 形式只在词形整体没命中时才兜底。
- **正确修法**：每项先按 `key=值` 形式取（`passed\s*=\s*(\d+)`），词形作兜底并加负向环视
  `(?<![\w=])`，防止 `passed=10 failed=3` 里的 `10` 被当成 `failed` 的计数。
- **为什么光读代码不容易看出来**：正则本身合法、`findall` 也真的命中了，报出来的数
  （`failed=10`）看起来完全像一个正常计数；只有拿 `key=值` 形式的**期望值**去比，才发现它读的是别人家的数。

## 跑法
- 门命令（仓内·无第三方依赖）：`python run_tests.py`（cwd = 本目录）。
- 门命令（仓内·按原设计）：`python -m pytest -q tests/test_counts.py`
  —— 需要**装了 pytest 的解释器**：本机 3.10.11（`E:\huanjing\python.exe`）有 pytest；
  编排器的门会把裸 `python` 改写成 3.12.10（`E:\基础设施\orch-deploy\.venv\Scripts\python.exe`），
  **那只没装 pytest** ⇒ 用 pytest 当门会「命令本身跑不起来」。所以门建议用 `run_tests.py`。
- 门命令（仓外、防篡改，推荐）：`python ..\_gates\t2_gate.py <本目录绝对路径>`。
- 秒级：单次 `run_tests.py` ≈ 0.05 s，无 sleep、无联网。
