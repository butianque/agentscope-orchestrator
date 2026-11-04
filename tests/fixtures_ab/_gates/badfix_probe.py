# -*- coding: utf-8 -*-
"""坏修探针：把几种「看起来像修好了」的改法分别打到副本上，看门会不会红。

用法：python _gates/badfix_probe.py
只写 fixtures_ab/_verify_bad/ 下的副本，不碰三个夹具仓。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
# 副本一律放**仓外**：放包树里会被打进 wheel，打完又删 ⇒ wheel 与源码不一致（实测顶红 CI 的 wheel_bytes）
BAD = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tmp\abver\bad")
PY312 = r"E:\基础设施\orch-deploy\.venv\Scripts\python.exe"


def 复制(名: str) -> Path:
    目标 = BAD / 名
    if 目标.exists():
        shutil.rmtree(目标)
    shutil.copytree(根 / 名.split("__")[0], 目标)
    return 目标


def 替换(文件: Path, 旧: str, 新: str) -> None:
    文 = 文件.read_text(encoding="utf-8")
    assert 旧 in 文, f"没改到：{文件}"
    文件.write_text(文.replace(旧, 新, 1), encoding="utf-8")


def 跑(名: str, cwd: Path, 命令: list[str]) -> int:
    r = subprocess.run(命令, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    尾 = [x for x in (r.stdout or "").strip().splitlines() if x.strip()][-1:] or [""]
    print(f"  [{名}] rc={r.returncode} | {尾[0][:120]}")
    return r.returncode


def main() -> int:
    BAD.mkdir(exist_ok=True)
    结果 = []

    # 1：把 judge 的读端改成 gbk 蒙过去（不设子进程环境）
    d = 复制("t1_subprocess_encoding__A改解码")
    替换(d / "judge.py", 'text=True, encoding="utf-8", errors="replace"',
         'text=True, encoding="gbk", errors="replace"')
    结果.append(("A judge按gbk解码", 跑("A", d, [PY312, "run_ci.py"])))

    # 2：读端按本机 locale 解码
    d = 复制("t1_subprocess_encoding__B按locale解码")
    替换(d / "judge.py", 'text=True, encoding="utf-8", errors="replace"',
         'text=True, encoding=__import__("locale").getpreferredencoding(False), errors="replace"')
    结果.append(("B judge按locale解码", 跑("B", d, [PY312, "run_ci.py"])))

    # 3：只在 run_ci.py 里给 judge 设编码（judge 自己不设）
    d = 复制("t1_subprocess_encoding__C只在CI设")
    替换(d / "run_ci.py", "env=干净环境,",
         'env={**干净环境, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},')
    结果.append(("C 只在run_ci设", 跑("C", d, [PY312, "run_ci.py"])))

    # 4：t2 只调顺序、词形无条件覆盖
    d = 复制("t2_count_misparse__D只调顺序")
    替换(d / "parse_counts.py", """    命中 = _词形.findall(文 or "")
    if 命中:
        for 数, 键 in 命中:
            出[键] = int(数)
        return 出

    for 键, 模式 in _键形:
        m = 模式.search(文 or "")
        if m:
            出[键] = int(m.group(1))
    return 出""", """    for 键, 模式 in _键形:
        m = 模式.search(文 or "")
        if m:
            出[键] = int(m.group(1))
    for 数, 键 in _词形.findall(文 or ""):
        出[键] = int(数)
    return 出""")
    结果.append(("D t2只调顺序+无条件覆盖", 跑("D", d, [PY312, "run_tests.py"])))

    # 5：t3 无条件覆盖调用方的 cmd
    d = 复制("t3_topcmd_not_injected__E无条件覆盖")
    替换(d / "assemble.py", """        if 门.get("type") == "regression_parity" and not 门.get("baseline_cmd"):
            门["baseline_cmd"] = list(顶层)""",
         """        if 门.get("type") == "regression_parity":
            门["baseline_cmd"] = list(顶层)
            门["cmd"] = list(顶层)""")
    结果.append(("E t3无条件覆盖", 跑("E", d, [PY312, "run_tests.py"])))

    # 6：把 t2 仓内测试文件改宽（假装通过）
    d = 复制("t2_count_misparse__F改测试")
    (d / "tests" / "test_counts.py").write_text(
        "# -*- coding: utf-8 -*-\nfrom parse_counts import 计数\n\n\n"
        "def test_随便(): \n    assert 计数(\"1 passed\") == {\"passed\": 1, \"failed\": 0, \"skipped\": 0}\n",
        encoding="utf-8")
    结果.append(("F t2改仓内测试(仓内门会绿)", 跑("F", d, [PY312, "run_tests.py"])))
    结果.append(("F t2改仓内测试(仓外门)", 跑("F外门", d, [PY312, str(根 / "_gates" / "t2_gate.py"), str(d)])))

    print("\n红的（被拦住）=", [n for n, rc in 结果 if rc != 0])
    print("绿的（没拦住）=", [n for n, rc in 结果 if rc == 0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
