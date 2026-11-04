---
id: 口径-子进程编码必须覆盖不能setdefault
类别: 规则
强度: 必须
来源: 第212轮真事故：套件 3 个脚本齐红，根因同一条；同一天以三张脸出现
等级: SOLID
版本: 1
更新: 2026-11-03T15:38:00
范围: ["tests/*.py", "tests/**/*.py", "tools/*.py", "src/**/*.py"]
---
症状：`UnicodeEncodeError: 'gbk' codec can't encode character '\u21de'`、`UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'`、`打包工具打印 ✓ 时崩掉导致后续步骤被跳过`、`套件里多个脚本齐红而单跑全绿`、`判定脚本打印 ⇒ / ✓ / ✗ 时崩在 print 上`。修法：给**子进程**钉编码一律**覆盖赋值**（`环境["PYTHONIOENCODING"]="utf-8"`、`环境["PYTHONUTF8"]="1"`），**永不用 `setdefault`** —— setdefault 只在外部没设时才补，外部 shell 一旦导出 `PYTHONIOENCODING=gbk` 或非 UTF-8 环境，全部子进程 stdout 退回 GBK 而崩；判据的运行环境必须由判据自己决定，"调用方环境碰巧对"不算对。另外：工具或测试脚本自己 `print` 含非 ASCII 符号时，先在模块顶部对 `sys.stdout/sys.stderr` 做 `reconfigure(encoding="utf-8", errors="replace")`。三轮实测（同一天三张脸）：① `tools/make_dist.py` 打印 ✓ 崩掉 ⇒ 因为它 main 是「写轮子→校验→组装」顺序，崩在校验后**跳过了组装**，dist/ 留着上一个包却看起来成功；② `tests/run_all.py` 自己打印 ⇒ 崩掉，`run_all -k` 直接退出码 1；③ 套件里 lean_spec/mcp_smoke/g3_sqlite 三脚本齐红、单跑全绿，根因是 `跑一个` 用了 `环境.setdefault("PYTHONIOENCODING","utf-8")`。**修一处必须把同类位置全找一遍**（tools/ 与 tests/ 都是"工具"），否则下次以另一张脸回来
