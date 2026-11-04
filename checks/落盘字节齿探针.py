# -*- coding: utf-8 -*-
"""R22 判据力探针：主树落盘必须**字节级**（行尾不得被翻译 · 非法字节不得被丢）。

── 被检缺陷（2026-10-15 真跑 Rust 工程后当场量到）──

  编排器改过的 `src/lib.rs`：**CRLF 145 / LF 0**；它没碰过的 `Cargo.toml`：**LF 11**
  ⇒ 工具**静默重写了整个文件的行尾**；且 `read_text(errors="ignore")` 让
  **非法 UTF-8 字节被静默丢弃**（实测 38 字节回滚成 35 字节）
  ⇒ 「红滚」这个安全网**自己有损**。

── 探针怎么做 ──

  逐条把**真实现**退回有损写法（字节级替换），跑 `generic_goal --selftest`，
  要求自检**变红且指出是 R22**；然后**无条件字节级还原**。

  ★ 探针自己先跑一遍「未改时」：自检必须**全绿** —— 防探针自己恒红（纪律 R3 的反面）。
  ★ 判据是**自检的真实退出码与输出**，不是"源码里有没有某个字符串"。
"""
import re
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
GEN = (TOOL / "src" / "agentscope_orchestrator" / "generic_goal.py")
原文 = GEN.read_text(encoding="utf-8")
# ★★ R65 互斥：两个进程同时改同一个源文件会**互相还原对方的改动**（实测各 7 红）。
#   锁的键是**被改的文件**，不是探针名 ⇒ 新增探针自动共享同一把锁。
#   ★ 多文件按**同一顺序**取锁（防两探针各持一把互相等 ⇒ 死锁）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _互斥 import 占住 as _占住, 原子写   #  noqa: E402
_锁0 = _占住(GEN)   # 等不到就抛 占用错（绝不静默放行）
原始字节 = GEN.read_bytes()

# (名称, 找, 换成) —— 每条都把**真实现**退回 R22 之前的有损写法
回退表 = [
    # ★ R22c 之后 `_apply_file` / 窗副本 / 正本**都调 `_落盘`**
    #   ⇒ 原来那条「`_apply_file` 退回 `write_text`」的针已不存在：
    #     回退**唯一实现**（④）就等价于同时回退这三处。少一条针，覆盖反而更全
    #     —— 这正是「收成全称实现」的好处：齿不必枚举落盘点。
    ("② `_rollback_file` 退回有损回滚（非法字节被丢 + 行尾被翻译）",
     '        p.write_bytes(Path(bk["backup"]).read_bytes())',
     '        p.write_text(Path(bk["backup"]).read_text(encoding="utf-8",\n'
     '                                                  errors="ignore"),\n'
     '                     encoding="utf-8")'),

    ("③ `_backup_file` 退回有损备份",
     '        b.write_bytes(p.read_bytes())',
     '        b.write_text(p.read_text(encoding="utf-8", errors="ignore"),\n'
     '                     encoding="utf-8")'),

    ("④ **唯一落盘实现** `_落盘` 退回 `write_text`（行尾被翻译成 CRLF）"
     "—— 一条覆盖主树/窗副本/正本三处",
     '    p.parent.mkdir(parents=True, exist_ok=True)\n'
     '    p.write_bytes(text.encode("utf-8"))\n'
     '\n'
     '\n'
     'def _落盘拷贝',
     '    p.parent.mkdir(parents=True, exist_ok=True)\n'
     '    p.write_text(text, encoding="utf-8")\n'
     '\n'
     '\n'
     'def _落盘拷贝'),

    ("⑤ 产物副本拷贝 `_落盘拷贝` 退回有损（非法字节被丢 + 行尾被翻译）",
     '    tgt.parent.mkdir(parents=True, exist_ok=True)\n'
     '    tgt.write_bytes(src.read_bytes())',
     '    tgt.parent.mkdir(parents=True, exist_ok=True)\n'
     '    tgt.write_text(src.read_text(encoding="utf-8", errors="ignore"),\n'
     '                   encoding="utf-8")'),
]


def 跑自检() -> tuple[int, str]:
    env = {**__import__("os").environ,
           "PYTHONPATH": str(TOOL / "src"),
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    cp = subprocess.run([sys.executable, str(GEN), "--selftest"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=env, timeout=1800)
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


fails: list[str] = []
try:
    # ⑦ 反自证：未改时自检**必须**全绿
    rc, 出 = 跑自检()
    if rc != 0 or "全绿" not in 出:
        fails.append(f"⑦ 未改时自检本应全绿，实得退出码={rc}"
                     f"（{'全绿' if '全绿' in 出 else '非全绿'}）")
    else:
        print(f"⑦ 未改 退出码={rc}（自检全绿）")

    for 名, 找, 换 in 回退表:
        if 原文.count(找) != 1:
            fails.append(f"{名}：探针锚点命中 {原文.count(找)} 次（期望 1）"
                         f" ⇒ 探针自身要跟着改")
            continue
        GEN.write_text(原文.replace(找, 换), encoding="utf-8")
        try:
            rc2, 出2 = 跑自检()
            # 判据：**自检必须变红，且红在 R22 上**
            红 = (rc2 != 0) or ("全绿" not in 出2)
            R22红 = "R22" in 出2
            if 红 and R22红:
                print(f"PASS {名} ⇒ 自检变红且点名 R22（退出码={rc2}）")
            elif 红:
                fails.append(f"{名}：自检红了但**没点名 R22** ⇒ 门可能红在别处"
                             f"（红错地方 = 无齿）")
            else:
                fails.append(f"{名}：★ 门无齿 —— 退回有损写法后自检**仍全绿**"
                             f"（退出码={rc2}）")
        finally:
            原子写(GEN, 原始字节)          # ★ 每条都无条件字节级还原
finally:
    原子写(GEN, 原始字节)

# ⑥ 探针不得留痕
if GEN.read_bytes() != 原始字节:
    fails.append("⑥ 探针跑完后 generic_goal.py 未字节级还原")
else:
    print("PASS ⑥ 还原：generic_goal.py 与探针开跑前**字节级一致**")

print()
if fails:
    print("FAIL")
    for f in fails:
        print("  ✗", f)
    raise SystemExit(1)
print("R22 探针：全部 PASS（行尾不得翻译 · 非法字节不得丢 · 红滚安全网有损性为零）")
