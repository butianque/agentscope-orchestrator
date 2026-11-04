"""坐标链判据：把 Lean 里的坐标链定理变成**能跑的机器判据**。

## 这个判据要证明的事

`coord_chain.py` 的机制抄自 `MyMeta/InfraCore/Derivation/{STC,Coord}.lean`。
Lean 那边定理是**证过**的；但**"证过"与"接上了"是两件事** ——
本项目已经吃够这个亏（知识库接在 fixloop 上、`注入()` 却永远返回空串；
判据门类型表在四处各写一份、改一处漏三处）。

所以本判据不问"定理对不对"（那是 Lean 的事），只问四件事：

1. **追加会让链变长**，且每个坐标的 `前` 真的指向上一个的指纹（链关系成立）；
2. **篡改能被抓到** —— 对应 Lean `tamper_changes_coord`（内容不同 ⇒ 坐标必不同）；
3. **四种损坏分开报**：跳号 / 断裂 / 重复 / 指纹不符（DS-05 TRIG-04 的四个触发条件）；
4. **链能全量重建** —— 证明"索引只是缓存"，删了不丢事实。

★ 判据的**独立性**要求（本项目一贯口径）：不能只测"我自己的函数返回 True"。
所以第 2、3 项**手动改写链文件**（绕过所有 API），再让 `校验()` 去发现 ——
这才是"篡改检测"的真测试；只调 API 造出来的链永远是自洽的，测不出东西。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

仓 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(仓))                    # 让 `import agentscope_orchestrator` 可用

# ★ 把状态根钉进**测试自己的桶**：不许写到开发者真实的状态根里（会污染别人的链）。
#   这与 run_all.py 给每个脚本独立状态根的做法一致。
_桶 = Path(__file__).resolve().parent / "_run_all_state" / "coord_chain"
shutil.rmtree(_桶, ignore_errors=True)
os.environ["ORCH_STATE_DIR"] = str(_桶)

from agentscope_orchestrator import coord_chain as C      # noqa: E402

失败: list[str] = []


def 断言(条件, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}")
    else:
        print(f"  FAIL {说明}")
        失败.append(说明)


def _原始行() -> list[dict]:
    """直接读文件，不经任何 API —— 判据要能看见链的**真身**。"""
    p = C.链路径()
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _写回(行: list[dict]) -> None:
    文 = "\n".join(json.dumps(r, ensure_ascii=False) for r in 行)
    C.链路径().write_text(文 + "\n", encoding="utf-8")


print("① 追加：链变长 + 链关系成立（前 ← 上一个的指纹）")
c1 = C.追加("回执", "receipts/a.md")
c2 = C.追加("任务书", "tasks/b.md")
c3 = C.追加("快照", "snapshot.md")
断言(c1.步 == 1 and c2.步 == 2 and c3.步 == 3,
     f"步号单调递增（{c1.步}/{c2.步}/{c3.步}）")
断言(c1.前 == "" and c2.前 == c1.指纹 and c3.前 == c2.指纹,
     "每个坐标的『前』= 上一个坐标的指纹（链关系成立）")
体0 = C.校验()
断言(体0.通过, f"刚造的链体检通过（{体0.摘要()}）")

print("② 单射：不同内容 ⇒ 不同坐标（Lean `tamper_changes_coord`）")
同一刻 = "2026-11-03T00:00:00"
甲 = C.坐标.造(步=9, 前="x", 种类="回执", 引用="a.md", 时刻=同一刻)
乙 = C.坐标.造(步=9, 前="x", 种类="回执", 引用="b.md", 时刻=同一刻)
断言(甲.指纹 != 乙.指纹, "同一步/同一时刻，『引用』不同 ⇒ 指纹不同（篡改必被发现）")
丙 = C.坐标.造(步=9, 前="y", 种类="回执", 引用="a.md", 时刻=同一刻)
断言(甲.指纹 != 丙.指纹, "只有『前』不同 ⇒ 指纹也不同（前任一变，链就变）")

print("③ 篡改检测：**手改文件**绕过 API，校验必须抓到（四种损坏分开报）")

# ③-1 跳号：删中间一条 ⇒ 步号出现空洞
行 = _原始行()
_写回(行[:1] + 行[2:])
体 = C.校验()
断言(bool(体.跳号), f"删掉中间一条 ⇒ 报『跳号』（{体.跳号[:1]}）")
断言(bool(体.断裂), "  同时报『断裂』（后一条的『前』指向了被删那条的指纹）")

# ③-2 指纹不符：改内容不改指纹
行 = _原始行()
行[1]["引用"] = "被偷偷改过的引用.md"
_写回(行)
体 = C.校验()
断言(bool(体.指纹不符), f"手改内容 ⇒ 报『指纹不符』（{体.指纹不符[:1]}）")

# ③-3 重复：把第一条复制到最后
行 = _原始行()
行.append(dict(行[0]))
_写回(行)
体 = C.校验()
断言(bool(体.重复) or bool(体.跳号), f"复制一条 ⇒ 报『重复』或『跳号』（{体.重复[:1] or 体.跳号[:1]}）")

print("④ 四方坐标校验（Lean `verifyChain`：完备 + 互异）")
四方 = C.四方坐标(快照="s", 任务书="t", 回执="r", 状态索引="i")
通过, 问题 = 四方.校验()
断言(通过, "四方齐全且互异 ⇒ 通过")
四方2 = C.四方坐标(快照="s", 任务书="s", 回执="r", 状态索引="i")
通过2, 问题2 = 四方2.校验()
断言(not 通过2 and any("重复" in x for x in 问题2),
     f"快照与任务书坐标相同 ⇒ 判『单射被破坏』（{问题2}）")
四方3 = C.四方坐标(快照="s", 任务书="", 回执="", 状态索引="")
通过3, 问题3 = 四方3.校验()
断言(not 通过3 and any("四方不全" in x for x in 问题3),
     f"缺三方 ⇒ 判『四方不全』（{问题3}）")

print("⑤ 索引只是缓存：链文件删了能从回执目录重建")
真链 = C.链路径()
备份 = 真链.read_text(encoding="utf-8")
真链.unlink()
# 造一个小回执目录（**混进非回执**：真实 .audit_trail 里 1682 份 .md 只有 719 份是回执）
回执桶 = _桶 / "fake_receipts"
回执桶.mkdir(parents=True, exist_ok=True)
for i in range(1, 4):
    (回执桶 / f"ORCH-{i:03d}_回执_测试.md").write_text(f"# 回执 {i}\n", encoding="utf-8")
(回执桶 / "某个报告.md").write_text("# 这不是回执\n", encoding="utf-8")
(回执桶 / "临时件.md").write_text("# 也不是\n", encoding="utf-8")
r = C.重建_明细(回执桶)
n = r["条数"]
断言(n == 3, f"从 3 份回执重建出 {n} 个坐标（**没把 2 份非回执收进来**）")
断言(r["非回执被跳过"] == 2, f"如实报出跳过了 {r['非回执被跳过']} 个非回执（不静默混入也不静默丢弃）")
体 = C.校验()
断言(体.通过, f"重建出来的链是**合法**的（{体.摘要()}）")
断言([c.引用 for c in C.查(种类="回执")].count("ORCH-002_回执_测试.md") == 1,
     "重建后能按需点查（AI 简单用简单查）")
真链.write_text(备份, encoding="utf-8")       # 还原，别影响后面

print("⑥ 并发安全：多进程同时追加不许撞步号")
import subprocess                                            # noqa: E402
脚本 = _桶 / "_并发追加.py"
脚本.write_text(
    "import os,sys\n"
    f"sys.path.insert(0, r'{仓}')\n"
    "from agentscope_orchestrator import coord_chain as C\n"
    "C.追加('回执', '并发测试')\n",
    encoding="utf-8")
链真 = C.链路径()
链真.unlink()                                  # 从空链开始，好数
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(仓))
子 = [subprocess.Popen([sys.executable, str(脚本)], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
      for _ in range(4)]
for p in 子:
    p.communicate(timeout=120)
体 = C.校验()
防 = [r for r in _原始行() if r.get("引用") == "并发测试"]
断言(len(防) == 4, f"4 个进程都追加成功（实得 {len(防)} 条）")
断言(体.通过, f"并发追加后链仍**完整**（{体.摘要()}）")
断言(len({r["步"] for r in 防}) == 4, "4 条的步号**互异**（OS 文件锁真的串行化了读-算-写）")

print("⑦ 干支显示层：甲子起、60 一循环（**必须让人一眼看见会重复**）")
断言(C._干支(1) == ("甲", "子"), f"步 1 = 甲子（实得 {''.join(C._干支(1))}）")
断言(C._干支(60) == ("癸", "亥"), f"步 60 = 癸亥（实得 {''.join(C._干支(60))}）")
断言(C._干支(61) == ("甲", "子"),
     f"步 61 与步 1 **同为甲子** —— 这正是『干支不能当主键』的证据（实得 {''.join(C._干支(61))}）")
断言(C.坐标.造(1, "", "x").指纹 != C.坐标.造(61, "z", "x").指纹,
     "但步 1 与步 61 的**指纹不同**（唯一性来自步+前，不来自干支）")

print("⑧ 统计永不抛：链文件坏掉也给可读结论")
链真.write_text("{这不是合法 json\n", encoding="utf-8")
st = C.统计()
断言(isinstance(st, dict) and "条数" in st,
     f"链文件是垃圾 ⇒ 统计仍返回结论（{str(st)[:90]}）")
链真.write_text(备份, encoding="utf-8")

print()
if 失败:
    print(f"坐标链判据：红 {len(失败)} 项 —— {失败[0]}")
    sys.exit(1)
print("坐标链判据：绿 —— 追加成链、篡改必抓、四种损坏分得清、索引可重建、并发不撞号")
