# -*- coding: utf-8 -*-
"""R21 判据力探针：**主树交付前必须过项目自己的验收门**（真工程，不是文本检查）。

── 被检缺陷（2026-10-15 真跑 Rust 工程时暴露）──

  `_deliver` 对主树任务**不重判任何门**，而它信的门**全是模型自己写的**。
  ⇒ 模型只要写几条弱门，**主树被改坏也能报"成功交付"**（绿保 ⇒ 文件留在主树）。

── 探针怎么造这个缺陷（**真跑**，不是字符串比对）──

  ① 造一个真 Rust 工程（crate 的 `cargo test --lib` **真的红**：缓存最值不重算）；
  ② 按主树流程走：`_backup_file` 备份 → `_apply_file` 应用一个**只加注释、没修 bug**
     的"模型改动"（模拟"模型写了弱门、没真修"）；
  ③ 直接调**真的 `_deliver`**（不是复刻它的逻辑）：
       · R21 在场 ⇒ 项目验收门（档案声明的 `cargo test --lib`）**真跑** ⇒ 红
                    ⇒ 必须**回滚主树** + 返回 1（不交付）；
       · 回退 R21（`if not _acc_ok:` → `if False:`）⇒ 返回 0 ⇒ **坏主树被保留**
                    ⇒ 探针判 **FAIL（门无齿）**。

  ★ 判据用的是**主树文件的真实内容**（注释还在不在）与**真 cargo 退出码**，
    **不是**「源码里有没有某个字符串」—— 那正是本库反复踩过的无齿门。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
GEN = TOOL / "src" / "agentscope_orchestrator" / "generic_goal.py"
CARGO_BIN = Path(r"C:\Program Files\Rust stable MSVC 1.94\bin")

# ── 真 Rust 工程：`cargo test --lib` 真的红 ──────────────────────
BUGGY_LIB = textwrap.dedent('''
    //! 定长滑动窗口统计。
    #[derive(Debug, Clone)]
    pub struct SlidingWindow {
        cap: usize,
        buf: Vec<f64>,
        cached_max: Option<f64>,
    }

    impl SlidingWindow {
        pub fn new(cap: usize) -> Self {
            SlidingWindow { cap, buf: Vec::new(), cached_max: None }
        }

        pub fn push(&mut self, v: f64) {
            if self.cap == 0 { return; }
            if self.buf.len() == self.cap { self.buf.remove(0); }
            self.buf.push(v);
            // ★ 缺陷：只与新样本和旧缓存比较 ⇒ 最值被淘汰后不重算
            self.cached_max = Some(match self.cached_max {
                Some(m) if m > v => m,
                _ => v,
            });
        }

        pub fn len(&self) -> usize { self.buf.len() }
        pub fn max(&self) -> Option<f64> { self.cached_max }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn max_ignores_evicted_element() {
            let mut w = SlidingWindow::new(2);
            w.push(5.0);
            w.push(3.0);
            w.push(1.0); // 5.0 被淘汰 ⇒ 窗口 = [3.0, 1.0]
            assert_eq!(w.max(), Some(3.0), "5.0 已不在窗口内");
        }
    }
''').strip() + "\n"

CARGO_TOML = '[package]\nname = "sliding"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n\n[workspace]\n'

PROFILE = """name: rust-r21
repo_root: .
audit_dir: .orchestrator
gates:
  - name: cargo_test_lib
    type: compile
    cmd: ["cargo", "test", "--lib"]
    cwd: "{repo}"
    expect_exit: 0
    timeout: 900
source:
  roots: ["src"]
  globs: ["**/*.rs"]
anchors:
  main: "src/lib.rs"
"""

# 场景脚本：在**子进程**里跑（`generic_goal` 在 import 期读 ORCH_PROFILE，
#   必须在 import 之前设好环境变量）。
SCENARIO = r'''
import asyncio, json, os, sys
from pathlib import Path
TOOL = Path(os.environ["R21_TOOL"]); REPO = Path(os.environ["R21_REPO"])
sys.path.insert(0, str(TOOL / "src"))
from agentscope_orchestrator import generic_goal as gg

rel = "src/lib.rs"
src = REPO / rel
弱改动 = src.read_text(encoding="utf-8").replace(
    "//! 定长滑动窗口统计。",
    "//! 定长滑动窗口统计。\n// ★ 模型改动：只加了一行注释（没修 bug）")

# 走**真的**主树流程：备份 → 应用 → 判门（模型自己的弱门 = 绿）
bk = gg._backup_file(rel)
gg._apply_file(rel, 弱改动)

r = gg.GoalRunner.__new__(gg.GoalRunner)          # 绕过 __init__（不探通道）
r.goal = "R21 探针"; r.safe_name = "r21_probe"
r.out_dir = REPO / ".probe_products"; r.product_dir = None
r.round_root = REPO; r.deliverables = [{"path": rel, "gates": []}]
r.repo_scope = True; r._active_backups = [bk]; r._tok_chars = 0
r.n_windows = 2; r.records = []; r._ledger_buf = []
r._轨道20 = gg._ts20.轨道()
# ★ 本探针检的是 R21，不是 R23 ⇒ 冻结区留空。
#   （R23 之后 `_deliver` 会读 `self._冻结区`。这条**已经不需要显式补**了 ——
#    `GoalRunner` 现在有**类级默认值**覆盖全部 `self.X` 读取，
#    所以 `__new__` 型探针在实现新增属性时不会静默崩掉。
#    这里保留显式赋值只为把意图写明白。）
r._冻结区 = ()
r._emit = lambda ev: None                          # 回执丢弃（探针不关心）
r._project_acceptance = gg.GoalRunner._project_acceptance.__get__(r)

rc = asyncio.run(gg.GoalRunner._deliver(r, 1, {"模型弱门": True},
                                        {"fixed_point": True}))
now = src.read_text(encoding="utf-8")
print(json.dumps({
    "退出码": rc,
    "主树还留着弱改动": "只加了一行注释" in now,
    "主树已回滚到原始缺陷态": "只加了一行注释" not in now,
}, ensure_ascii=False))
'''


def 跑(回退: bool) -> tuple[int, dict]:
    """真跑一次场景。回退=True 时先把 R21 的守卫改成 `if False:`（字节级）。"""
    原文 = GEN.read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="r21_"))
    try:
        repo = tmp / "proj"
        (repo / "src").mkdir(parents=True)
        (repo / "Cargo.toml").write_text(CARGO_TOML, encoding="utf-8")
        (repo / "src" / "lib.rs").write_text(BUGGY_LIB, encoding="utf-8")
        (repo / "profile.yml").write_text(PROFILE, encoding="utf-8")

        if 回退:
            针 = "            if not _acc_ok:"
            if 原文.count(针) != 1:
                raise SystemExit(f"回退锚点命中 {原文.count(针)} 次（期望 1）")
            # 字节级回退：**不再过项目验收门**（= R21 之前的行为）
            GEN.write_text(原文.replace(针, "            if False:"),
                           encoding="utf-8")

        sc = tmp / "scenario.py"
        sc.write_text(SCENARIO, encoding="utf-8")
        env = dict(os.environ)
        env["R21_TOOL"] = str(TOOL)
        env["R21_REPO"] = str(repo)
        env["ORCH_PROFILE"] = str(repo / "profile.yml")
        env["PATH"] = str(CARGO_BIN) + os.pathsep + env.get("PATH", "")
        env["PYTHONPATH"] = str(TOOL / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"       # ★ 只接受 0/1（探针自身踩过：写成 "utf-8" ⇒
                                      #   `Fatal Python error: invalid PYTHONUTF8`）
        cp = subprocess.run([sys.executable, str(sc)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            env=env, timeout=900)
        out = cp.stdout or ""
        m = re.search(r"\{.*\}", out, re.S)
        报 = json.loads(m.group(0)) if m else {}
        if not 报:
            报["_stderr"] = (cp.stderr or "")[-800:]
        return cp.returncode, 报
    finally:
        GEN.write_text(原文, encoding="utf-8")          # ★ 无条件字节级还原
        shutil.rmtree(tmp, ignore_errors=True)


fails: list[str] = []
# ★★ R65 互斥：两个进程同时改同一个源文件会**互相还原对方的改动**（实测各 7 红）。
#   锁的键是**被改的文件**，不是探针名 ⇒ 新增探针自动共享同一把锁。
#   ★ 多文件按**同一顺序**取锁（防两探针各持一把互相等 ⇒ 死锁）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _互斥 import 占住 as _占住          # noqa: E402
_锁0 = _占住(GEN)   # 等不到就抛 占用错（绝不静默放行）
原始字节 = GEN.read_bytes()          # ★ 探针开跑前留底（末尾比对，防探针自己留痕）

# ⑦ 反自证：未改时**必须**能跑通（防探针自己恒红）
rc0, 报0 = 跑(回退=False)
if rc0 != 0 or 报0.get("退出码") is None:
    fails.append(f"⑦ 未改时场景自身跑不通：退出码={rc0} 报={报0}")
else:
    print(f"⑦ 未改 场景退出码={rc0} 报={报0}")

# ① 齿：R21 在场 ⇒ 坏主树**必须**被回滚且**不交付**（返回 1）
if 报0:
    if 报0.get("退出码") != 1:
        fails.append(f"① 项目验收门红时 `_deliver` 应返回 1（不交付），"
                     f"实得 {报0.get('退出码')}")
    else:
        print("PASS ① 项目验收门（cargo test --lib）真跑红 ⇒ 判不交付（返回 1）")
    if not 报0.get("主树已回滚到原始缺陷态"):
        fails.append("① 项目验收门红时必须**回滚主树**，实测主树仍留着改动")
    else:
        print("PASS ① 主树已回滚（坏改动不留在主树）")

# ② 齿：回退 R21 ⇒ **坏主树被当成功交付**（这正是缺陷）
rc1, 报1 = 跑(回退=True)
if not 报1:
    fails.append(f"② 回退后场景跑不出结果：退出码={rc1} 报={报1}")
else:
    if 报1.get("退出码") == 0 and 报1.get("主树还留着弱改动"):
        print("PASS ② 齿：回退 R21 ⇒ 坏主树被**当成功交付**（返回 0 且改动留在主树）"
              " ⇒ 门确实有区分力")
    else:
        fails.append(f"② 门无齿：回退 R21 后仍返回 {报1.get('退出码')}、"
                     f"主树留改动={报1.get('主树还留着弱改动')} ⇒ "
                     f"本门抓不出「主树被改坏还报成功」")

# ③ 还原后与原文**字节级一致**（探针不得留痕）
if GEN.read_bytes() != 原始字节:
    fails.append("③ 探针跑完后 generic_goal.py **未字节级还原** ⇒ 探针自己留痕")
else:
    print("PASS ③ 还原：generic_goal.py 与探针开跑前**字节级一致**")
print()
if fails:
    print("FAIL")
    for f in fails:
        print("  ✗", f)
    raise SystemExit(1)
print("R21 探针：全部 PASS（真 Rust 工程 · 真 cargo 退出码 · 真主树回滚）")
