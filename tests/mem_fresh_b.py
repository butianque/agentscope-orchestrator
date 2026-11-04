#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据 B：记忆索引**随工程进度自动更新**（tests/mem_fresh_b.py）

## 要证的那句话
「改动项目里一个受控文件（新建 / 删除）之后，**重新扫描**，索引的读数必须跟着变，
  不许是缓存的旧数。」

## 为什么这句话需要一条机械判据
记忆索引（`autoengine.scan_project`，真入口 `taixu_mcp.auto_scan`）是**唯一**按
「哪一类 / 什么键 / 在哪个文件」把项目资产登记下来的东西。它要是**陈旧**，
后果不是"少一条记录"，而是：**后续所有按名取回的动作都拿到一个旧世界**
（回执已删却查得到、新文件永远查不到）。而陈旧是**静默的** ——
返回值照样是一份 `{"doc": 1888}` 这样的漂亮读数。

## 判据五段（逐段机判）
① 新建受控文件 → 重扫 → 该条声明的命中**严格 +1**，且按名取回拿到**该文件的绝对路径**；
② 删除该受控文件 → 重扫 → 命中**回到基线**，且该名字**取不回**（两个方向都跟得住）；
③ ★ 区分力之一：**没有重扫的旧索引必须看不见新文件**。
   若旧索引也看得见，说明本判据的读数与"有没有重扫"无关 ⇒ 判据恒真、没牙。
④ ★ 区分力之二：**复用同一个索引对象续扫**会留下已删文件的**灰键** ——
   所以「重扫 = 新建索引」这件事本身是被测行为，不是实现细节。
⑤ ★ 真入口：`taixu_mcp.auto_scan()` 必须**换掉**懒缓存（`_MEM`），
   并给出**删除之后**的新读数（已删文件的名字不许残留）。

## 诚实边界
- 受控文件（探针）落在档案那条声明**自己报出来的、实测可写的**根里，
  名字带本进程 pid，结束前删净并断言仓保持原状。
- ★ 实测（2026-11-04）：`.audit_trail` 对本进程**不可写**
  （`PermissionError: [Errno 13]`；`icacls` 显示该目录有一条显式 `(RX)` ACE、无写位），
  同一体声明里的 `receipts` 可写。⇒ 硬把探针写死进 `.audit_trail` 会让本判据在
  **正确实现上恒红**（那是假红门，比没有判据更坏）。所以可写性由**实测**决定，
  并在输出里写清"探针落在哪一个根、哪一个根写不动"。
- 计数断言是**严格 +1 / 回到基线**。若同一窗口内另有第三方在写这些目录，
  会看到"偏了 k 份"的实测值 —— 本判据把实测数打进消息里，供区分
  「索引陈旧」与「真有人在写」。
- 本判据不改任何生产文件，也不改工具源码。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

# 控制台编码：本仓踩过 `⇒`(U+21DE) 在 GBK 控制台上把整步搞死的坑 ⇒ 先钉编码。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                          # noqa: BLE001
        pass

此处 = Path(__file__).resolve().parent
包根 = 此处.parent
for _p in (str(包根 / 'src'), str(包根)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ★ ORCH_STATE_DIR 必须**覆盖**（不是 setdefault）：判据的运行环境由判据自己钉死，
#   "调用方环境碰巧对"不算对（run_all 里同一条纪律）。
桶 = 此处 / '_run_all_state' / 'mem_fresh_b'
桶.mkdir(parents=True, exist_ok=True)
os.environ['ORCH_STATE_DIR'] = str(桶)

from agentscope_orchestrator import autoengine as ae      # noqa: E402
from agentscope_orchestrator import profile as P          # noqa: E402

prof = P.get_profile()
仓 = prof.repo_root
# ★ 探针前缀带**本判据的名字**：清残留只清自己这一支留下的，不去动别人（同仓可能
#   有别的判据进程同时在跑，实测 2026-11-04 就见过兄弟组留下的探针）。
探针前缀 = '_判据探针_mem_fresh_b_'
失败: list[str] = []


def 断言(条件, 说明: str) -> None:
    print(f"  {'ok  ' if 条件 else 'FAIL'} {说明}")
    if not 条件:
        失败.append(说明)


def 重扫():
    """重扫 = **新建索引** + 按档案 `memory:` 段全量扫。

    ★ 与 `taixu_mcp.auto_scan` 的写法逐字对应（`_MEM = MemoryIndex()` 再
      `scan_project`）—— 判据测的是**这条路**，不是我自己发明的一条路。
    """
    索引 = ae.MemoryIndex()
    出 = ae.scan_declared(索引, tuple(prof.memory), 仓)
    return 索引, 出


def 回执条(出: dict) -> dict | None:
    """档案里那条把「回执」（项目事实）算作资产的声明。

    ★ 2026-11-04 改（本判据当场红过一次）：档案原来把 `.audit_trail` 与
      `多智能体编排/agentscope_orchestrator/receipts` 写在**一条**声明里；
      为了让「`.audit_trail` 有多少份 .md」**可拆、可对账**，已拆成**两条**。
      而 `.audit_trail` 对本进程**不可写**（显式 RX ACE、`PermissionError errno=13`）
      ⇒ 探针必须落在 `receipts` 那条上；继续按 `.audit_trail` 挑，会让本判据
      在**正确实现上恒红** —— 那是假红门，比没有判据更坏。
    """
    for 条 in (出.get('逐条') or []):
        根们 = 条.get('roots') or []
        if 条.get('kind') == 'doc' and any('receipts' in r for r in 根们):
            return 条
    return None


def 命中(出: dict) -> int:
    条 = 回执条(出)
    return -1 if 条 is None else int(条.get('命中') or 0)


def 可写根(条: dict | None):
    """从那条声明**自己报出来的** roots 里挑一个**实测能写**的根。

    ★ 为什么不写死：见文件头「诚实边界」—— `.audit_trail` 实测不可写。
      写死它，本判据会在**正确的实现上恒红**；而假红门比无齿门更坏。
    """
    拒: list[str] = []
    for r in (条 or {}).get('roots') or []:
        根 = (仓 / str(r)).resolve()
        if not 根.is_dir():
            拒.append(f'{r}（根不存在）')
            continue
        试探 = 根 / f'{探针前缀}可写试探_{os.getpid()}.md'
        try:
            试探.write_text('x', encoding='utf-8', newline='')
            试探.unlink()
        except OSError as e:
            拒.append(f'{r}（{type(e).__name__} errno={e.errno}）')
            continue
        return 根, 拒
    return None, 拒


def 探针键(路径: Path) -> str:
    """索引里的键 = **仓相对 posix 路径**（与 `scan_declared` 的算法一致）。"""
    return 路径.resolve().relative_to(仓).as_posix()


def 扫残留() -> list[str]:
    """清掉上次跑崩留下的探针（否则读数会被自己的残留污染）。"""
    见 = []
    for 条0 in (prof.memory or ()):
        for r in (条0.get('roots') or []):
            根 = (仓 / str(r)).resolve()
            if not 根.is_dir():
                continue
            for 残留 in sorted(根.glob(f'{探针前缀}*.md')):
                try:
                    残留.unlink()
                    见.append(f'清掉 {残留.name}')
                except OSError as e:
                    见.append(f'{残留.name}（删不掉：{type(e).__name__} errno={e.errno}）')
    return 见


def 找残留() -> list[str]:
    """**只找不删**：收尾之后声明范围内还有没有本判据的探针（证明真删净了）。"""
    见 = []
    for 条0 in (prof.memory or ()):
        for r in (条0.get('roots') or []):
            根 = (仓 / str(r)).resolve()
            if not 根.is_dir():
                continue
            for f in sorted(根.glob(f'{探针前缀}*.md')):
                见.append(f.name)
    return 见


def 并存探针(我自己的: tuple = ()) -> list[str]:
    """声明范围内**别的判据进程**留下的探针。

    ★ 为什么要它：本判据的计数断言是**严格 +1**，而读数里的**每一份 .md 都算**。
      同仓可能有别的判据在跑（实测 2026-11-04 有兄弟组同时在写探针）⇒
      若计数偏了 k 份，消息里必须能立刻看出"偏的那 k-1 份是谁的" ——
      否则一次并发会被误读成"索引陈旧"，归因就跑错了。
    """
    我 = {str(x) for x in 我自己的 if x is not None}
    见 = []
    for 条0 in (prof.memory or ()):
        for r in (条0.get('roots') or []):
            根 = (仓 / str(r)).resolve()
            if not 根.is_dir():
                continue
            for f in sorted(根.glob('_判据探针_*.md')):
                if f.name not in 我:
                    见.append(f.name)
    return 见


探针 = None
入口探针 = None
探针根 = None
M = None
基线 = -1

print(f"mem_fresh_b：记忆索引新鲜度（档案 {prof.source_path}）")
断言(bool(prof.memory),
     f'档案声明了 `memory:` 段（{len(prof.memory)} 条）—— 没这一段的索引不覆盖项目本体')

try:
    for 行 in 扫残留():
        print(f"  [前置] {行}")

    # ── ① 改动前：基线读数 ──────────────────────────────────────────
    t0 = time.time()
    索引0, 出0 = 重扫()
    基线 = 命中(出0)
    秒0 = time.time() - t0
    条0 = 回执条(出0)
    断言(条0 is not None,
         '档案里有一条声明把回执（`receipts`，可写的那一条）算作项目资产')
    断言(基线 > 0, f'基线读数：该条声明命中 {基线} 份（重扫 {秒0:.1f}s）')
    探针根, 拒根 = 可写根(条0)
    断言(探针根 is not None,
         f'那条声明里至少有一个**实测可写**的根（不可写的：{拒根}）')
    if 探针根 is None:
        raise RuntimeError('没有可写的受控根，本判据的前提不成立')
    print(f"  [说明] 探针落在 {探针根}（不可写的根：{拒根}）")
    探针 = 探针根 / f'{探针前缀}{os.getpid()}.md'
    入口探针 = 探针根 / f'{探针前缀}入口_{os.getpid()}.md'
    断言(索引0.index('doc').query(探针键(探针)) is None,
         '改动前：探针名**不在**索引里（前提干净）')

    # ── ② 新建 → 重扫 → 读数必须跟着变（严格 +1）────────────────────
    print('① 新建受控文件 → 重扫 → 读数 +1 且按名取回得到该文件的绝对路径')
    探针.write_text('# 判据探针（mem_fresh_b）\n', encoding='utf-8', newline='')
    索引1, 出1 = 重扫()
    新建后 = 命中(出1)
    断言(新建后 == 基线 + 1,
         f'新建后重扫：该条声明命中 {基线} → {新建后}（严格 +1）'
         f'；读数若偏 k 份，先看是谁的探针：并存 '
         f'{并存探针((探针.name, 入口探针.name))}')
    取回 = 索引1.index('doc').query(探针键(探针))
    断言(取回 is not None and Path(取回) == 探针,
         f'按名取回（键 {探针键(探针)}）拿到**该文件的绝对路径**：{取回}')
    断言(索引1.index('doc').query(探针.stem) is not None,
         '按**文件名**（stem）也取得到 ⇒ 探针确实进了索引的键空间')

    # ── ③ ★ 反例一：旧索引必须看不见新文件 ───────────────────────────
    print('② ★ 反例：**没重扫的旧索引**必须看不见新文件（否则本判据恒真）')
    断言(索引0.index('doc').query(探针键(探针)) is None,
         '改动前建的索引（**未重扫**）查同一个名字 → None '
         '⇒ 读数只随"这一扫"变，不是恒真')

    # ── ④ ★ 反例二：复用索引对象续扫会留下已删文件的灰键 ──────────────
    print('③ ★ 反例：复用索引对象续扫 → 已删文件的键留下（灰键）'
          '⇒「重扫 = 新建索引」是被测行为')
    索引2, _ = 重扫()
    断言(索引2.index('doc').query(探针键(探针)) is not None,
         '重扫后探针名可达（灰键实验的前提）')
    探针.unlink()
    ae.scan_declared(索引2, tuple(prof.memory), 仓)      # 续扫到**同一个对象**
    断言(索引2.index('doc').query(探针键(探针)) is not None,
         '文件已删、续扫后旧键**仍在** ⇒ 索引是**快照**、复用缓存确实会给出旧数 '
         '（所以 ④ 的"回到基线"不是恒真）')

    # ── ⑤ 删除 → 重扫 → 读数回到基线，名字取不回（负方向）────────────
    print('④ 删除受控文件 → 重扫 → 读数回到基线，且该名字取不回')
    索引3, 出3 = 重扫()
    删除后 = 命中(出3)
    断言(删除后 == 基线,
         f'删除后重扫：该条声明命中回到基线 {基线}（实得 {删除后}）'
         f'；若不等，先看是谁的探针：并存 '
         f'{并存探针((探针.name, 入口探针.name))}')
    断言(索引3.index('doc').query(探针键(探针)) is None,
         '删除后按名取回 → None（删除方向也跟得住）')

    # ── ⑥ ★ 真入口：懒缓存 + 刷新入口 ────────────────────────────────
    print('⑤ ★ 真入口：懒缓存 `_ensure_memory` 与刷新入口 `auto_scan`')
    try:
        from agentscope_orchestrator import taixu_mcp as M      # noqa: E402
    except Exception as e:                                      # noqa: BLE001
        M = None
        断言(False, f'真入口不可用：{type(e).__name__}: {e}')
    if M is not None:
        入口探针.write_text('# 判据探针（真入口）\n', encoding='utf-8', newline='')
        t0 = time.time()
        缓存 = M._ensure_memory()
        秒缓存 = time.time() - t0
        断言(缓存.index('doc').query(探针键(入口探针)) is not None,
             f'懒缓存建成时看得见入口探针（此刻它确实在盘上；建缓存 {秒缓存:.1f}s）')
        入口探针.unlink()
        断言(缓存.index('doc').query(探针键(入口探针)) is not None,
             '文件已删、**没刷新**：懒缓存仍拿得到它 —— 缓存不会自己更新'
             '（这是缓存的语义，不是缺陷；要新读数就得走刷新入口）')
        t0 = time.time()
        出入口 = json.loads(M.auto_scan())
        秒入口 = time.time() - t0
        断言(M._MEM is not 缓存, '刷新入口**换了索引对象**（不是往旧对象里续扫）')
        断言(M._ensure_memory() is M._MEM,
             '刷新后懒缓存路径给出的就是新索引（调用方不必自己找门）')
        断言(命中(出入口) == 基线,
             f'刷新入口的读数跟着**删除**变了：该条声明命中 {基线}'
             f'（实得 {命中(出入口)}；刷新耗时 {秒入口:.1f}s，其中含与读数无关的选题扫描）')
        断言(M._ensure_memory().index('doc').query(探针键(入口探针)) is None,
             '刷新后按名取回 → None：**已删文件不留在索引里** '
             '（这就是"不许是缓存的旧数"）')

except Exception:                                              # noqa: BLE001
    traceback.print_exc()
    断言(False, '判据自身出错（上面的 traceback 是归因，不是被测方的红）')
finally:
    # ── 收尾：探针必须删净（仓保持原状）+ 工具进程内的懒缓存复位 ─────
    for f in (探针, 入口探针):
        if f is None:
            continue
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass
    if M is not None:
        M._MEM = None          # 只复位**进程内**的懒缓存（不落盘；进程退出即没了）
    残留 = 找残留()
    断言(not 残留,
         f'仓保持原状：声明范围内无本判据探针残留（实得 {残留}）')

print()
print(f"读数：该条声明基线 {基线} 份 → 新建后 +1 → 删除后回基线；"
      f"名字→路径由**索引**回答，不靠现场遍历")
if 失败:
    print(f"mem_fresh_b：红 {len(失败)} 项 —— {失败[0]}")
    sys.exit(1)
print('mem_fresh_b：绿 —— 新建/删除后重扫读数严格跟着变（+1 / 回基线）、'
      '未重扫的旧索引看不见新文件、复用索引对象会留灰键、'
      '真入口 auto_scan 换掉懒缓存且已删文件的名字取不回')
