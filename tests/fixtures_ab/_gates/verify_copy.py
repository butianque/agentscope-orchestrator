# -*- coding: utf-8 -*-
"""自证「复制后仍能独立跑」：按 ab.json 的门命令，在**临时目录副本**里跑（先删 FIXME.md）。

用法：python _gates/verify_copy.py            # 只跑缺陷态（期望 red）
      python _gates/verify_copy.py --fixed    # 顺带把三个仓按正确修法改一遍（期望 green）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
三个 = ("t1_subprocess_encoding", "t2_count_misparse", "t3_topcmd_not_injected")

修法 = {
    "t1_subprocess_encoding/judge.py": [
        ("import json\nimport subprocess", "import json\nimport os\nimport subprocess"),
        ('        capture_output=True, text=True, encoding="utf-8", errors="replace",\n    )',
         '        capture_output=True, text=True, encoding="utf-8", errors="replace",\n'
         '        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},\n    )'),
    ],
    "t2_count_misparse/parse_counts.py": [
        ('_词形 = re.compile(r"(\\d+)\\s+(passed|failed|skipped)")',
         '_词形 = (r"(?<![\\w=])(\\d+)\\s+passed",)' ),
    ],
    "t3_topcmd_not_injected/assemble.py": [
        ('        if 门.get("type") == "regression_parity" and not 门.get("baseline_cmd"):\n'
         '            门["baseline_cmd"] = list(顶层)',
         '        if 门.get("type") == "regression_parity":\n'
         '            if not 门.get("baseline_cmd"):\n'
         '                门["baseline_cmd"] = list(顶层)\n'
         '            if not 门.get("cmd"):\n'
         '                门["cmd"] = list(顶层)'),
    ],
}


def 复制到临时(名: str) -> Path:
    """复制到**仓外**的一次性目录（`太虚元道\\.tmp\\abver\\`）。

    为什么必须仓外：副本落在包树里会被打进 wheel，打完又删 ⇒ wheel 与源码不一致
    （2026-11-03 实测把 CI 的 wheel_bytes 顶红）。
    为什么不复制到 %TEMP%：本机 deploy 解释器对 `%TEMP%`/`E:\\莉莉丝\\.temp` 是
    PermissionError，只在产品树里可写（安全软件按进程映像拦）；复制到系统临时目录
    ⇒ 门会因为"写不了盘"而红，那不是夹具的缺陷信号。
    """
    基 = Path(r"E:\基础设施\离散时空认知物理基础研究\太虚元道\.tmp\abver")
    if "--keep" in sys.argv:
        基 = 基 / "fixed"
    else:
        基 = 基 / "run"
    if (基 / 名).exists():
        shutil.rmtree(基 / 名)
    基.mkdir(parents=True, exist_ok=True)
    目标 = 基 / 名
    shutil.copytree(根 / 名, 目标)
    (目标 / "FIXME.md").unlink(missing_ok=True)          # 实验时会删：删掉后必须照样跑
    return 目标


def 应用修法(副本: Path) -> None:
    """t2 的修法在 verify_copy 里整文件重写；t1/t3 按正确修法做定点替换。"""
    if 副本.name == "t1_subprocess_encoding":
        for 旧, 新 in 修法["t1_subprocess_encoding/judge.py"]:
            文 = (副本 / "judge.py").read_text(encoding="utf-8")
            assert 旧 in 文, 旧
            (副本 / "judge.py").write_text(文.replace(旧, 新, 1), encoding="utf-8")
    elif 副本.name == "t2_count_misparse":
        (副本 / "parse_counts.py").write_text(
            '''# -*- coding: utf-8 -*-
"""从一段测试输出里解析 passed / failed / skipped 三项计数。"""
from __future__ import annotations

import re

_键形 = {k: re.compile(k + r"\\s*=\\s*(\\d+)") for k in ("passed", "failed", "skipped")}
_词形 = {k: re.compile(r"(?<![\\w=])(\\d+)\\s+" + k) for k in ("passed", "failed", "skipped")}
_项 = ("passed", "failed", "skipped")


def 计数(文: str) -> dict:
    文 = 文 or ""
    出 = {}
    for 键 in _项:
        m = _键形[键].search(文)
        if m:
            出[键] = int(m.group(1))
    for 键 in _项:
        if 键 in 出:
            continue
        m = _词形[键].search(文)
        if m:
            出[键] = int(m.group(1))
    return {键: 出.get(键, 0) for 键 in _项}
''', encoding="utf-8")
    else:
        for 旧, 新 in 修法["t3_topcmd_not_injected/assemble.py"]:
            文 = (副本 / "assemble.py").read_text(encoding="utf-8")
            assert 旧 in 文, 旧
            (副本 / "assemble.py").write_text(文.replace(旧, 新, 1), encoding="utf-8")


def main() -> int:
    要修 = "--fixed" in sys.argv
    表 = []
    for 名 in 三个:
        副本 = 复制到临时(名)
        清单 = json.loads((副本 / "ab.json").read_text(encoding="utf-8"))
        if 要修:
            应用修法(副本)
        r = subprocess.run([str(x) for x in 清单["门命令"]], cwd=str(副本),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        尾 = [x for x in ((r.stdout or "") + (r.stderr or "")).strip().splitlines() if x.strip()]
        print(f"[{名}] argv={清单['门命令']} cwd={副本}\n    rc={r.returncode} | {尾[-1][:110] if 尾 else ''}")
        表.append((名, r.returncode))
        if "--keep" not in sys.argv:
            shutil.rmtree(副本.parent, ignore_errors=True)
    期望 = 0 if 要修 else 1
    坏 = [n for n, rc in 表 if rc != 期望]
    print(f"\n{'修后' if 要修 else '缺陷'}态：期望 rc={期望}；不符的仓 = {坏 or '无'}")
    return 1 if 坏 else 0


if __name__ == "__main__":
    raise SystemExit(main())
