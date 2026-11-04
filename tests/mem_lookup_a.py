#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据 A：按名字**直接取回路径**（不靠遍历）（tests/mem_lookup_a.py）

## 要证的那句话
「名字 → 路径」这件事由**索引**回答，不由"查询时去磁盘找一遍"回答。

## 为什么"不靠遍历"必须单独证
因为两种实现的**读数可以一模一样**：一个真索引，和一个"每次查询都全盘扫一遍再比对"
的实现，都会回同一个路径。差别只在两处，而这两处都是可机判的：
  · **代价**：全盘扫一次是「几千次目录枚举 / 几秒」；取回应当是常数级、0 次枚举；
  · **语义**：索引是**快照** —— 盘变了不重扫就不该变（见 tests/mem_fresh_b.py）。
⇒ 所以判据不能只看"取回的值对不对"，必须**把遍历这条路堵死再看**：
  取回期把 `os.scandir` 换成炸弹，若还取得到且路径逐字正确，答案就只能来自索引。

## 三段
① 建索引（此时统计目录枚举次数 N：它必须是**大数**，说明"建"确实遍历了盘）；
② ★ 关掉遍历（`os.scandir` = 炸弹）→ 三样东西**按名取回**，路径**逐字断言**：
     · 定理名      `MyMeta.InfraCore.Derivation.InfinitePan.si_zhu_infinite_well_defined`
       （Lean 形式化库，已证明的定理）
     · Rust 模块名  `递归深度`（`src/递归深度.rs`，`src/lib.rs` 里 `pub mod 递归深度;`）
     · 审计回执名   `TS-1_回执_develop_Rust开发_1010202857`（`.audit_trail/…md`）
   并断言**取回期目录枚举 == 0 次**；
③ ★ 区分力（四条反例，逐条机判）：
     ① 不存在的名字 → **None**（三处都试，不许"勉强命中"）；
     ② 近似名（少一个字母）→ None（不许前缀/模糊匹配偷答案）；
     ③ 同一个名字 register 到**磁盘上不存在的假路径** → 取回必须回**假路径**
        （若取回是现场遍历找的，它只会回真路径或 None ⇒ 这条就红）；
     ④ 把该键从索引里删掉 → 同名取回变 None（命中确实依赖**键存在**）。
④ 名字 ↔ 文件互证（恢复遍历后做）：取回的路径真的存在，且**文件里真有那个名字**
   （定理名出现在 .lean 源码里、模块在 `src/lib.rs` 里被 `pub mod` 声明、
   模块文件里有 `pub fn`、回执文件非空）。

## 诚实边界
- Rust 那一项的"符号名"是**文件级**的：档案 `memory:` 段的 `rs` 类登记的是
  「相对路径」与「文件 stem」（见 `autoengine.scan_declared`）⇒ 能按名取回的
  Rust 符号名就是**模块名**。判据随后用 `src/lib.rs` 里的 `pub mod <名>;` 与文件内的
  `pub fn` 把"名字 ↔ 真实现"接上，而不是只说"文件在"就完事。
- 回执名是**写死的一个真实文件名**：它要被删掉，本判据会**如实变红**
  （名字链断了就该报，不许换成"随便找一个"来假装通过）。
- 本判据只读，不写仓里的任何文件（唯一的写是 `_run_all_state` 下的状态桶）。
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback
from pathlib import Path

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

桶 = 此处 / '_run_all_state' / 'mem_lookup_a'
桶.mkdir(parents=True, exist_ok=True)
os.environ['ORCH_STATE_DIR'] = str(桶)

from agentscope_orchestrator import autoengine as ae      # noqa: E402
from agentscope_orchestrator import profile as P          # noqa: E402

prof = P.get_profile()
仓 = prof.repo_root
失败: list[str] = []


def 断言(条件, 说明: str) -> None:
    print(f"  {'ok  ' if 条件 else 'FAIL'} {说明}")
    if not 条件:
        失败.append(说明)


# ── 三样要取回的东西：名字都来自项目里**真实存在**的东西 ───────────────
定理名 = 'MyMeta.InfraCore.Derivation.InfinitePan.si_zhu_infinite_well_defined'
定理模块键 = 'MyMeta.InfraCore.Derivation.InfinitePan'
定理文件 = (仓 / '形式化验证' / 'formal_proof' / 'MyMeta' / 'InfraCore'
            / 'Derivation' / 'InfinitePan.lean')
模块名 = '递归深度'
模块文件 = 仓 / 'src' / '递归深度.rs'
回执名 = 'TS-1_回执_develop_Rust开发_1010202857'
回执文件 = 仓 / '.audit_trail' / (回执名 + '.md')
回执键 = '.audit_trail/' + 回执名 + '.md'

真枚举 = os.scandir
计数 = {'建库': 0, '取回': 0}
索引 = None
取回期枚举 = -1
建库秒 = -1.0


def _数枚举(path='.'):
    计数['建库'] += 1
    return 真枚举(path)


def _炸弹(path='.'):
    """取回期把目录枚举变成炸弹 —— 遍历这条路被堵死，还取得到就说明答案来自索引。"""
    计数['取回'] += 1
    raise RuntimeError('取回期不许遍历目录（os.scandir 已被本判据换成炸弹）')


def _读(路径: Path, 限: int = 400000) -> str:
    try:
        return 路径.read_text(encoding='utf-8', errors='ignore')[:限]
    except OSError:
        return ''


def _剥注释(源: str) -> str:
    """只留**代码**：去掉 Lean 的块注释与行注释。

    ★ 为什么非剥不可（本判据第一版真栽在这条上）：那个 .lean 文件头部写着
      「宪法合规: 0 sorry」，直接 `'sorry' in 源` 读到的是**散文里的那个词**
      ⇒ 把一条已证明的定理判成"可能有 sorry"。这正是本仓命名过的病：
      **拿散文当代码读**。剥离器由本判据**独立实现**（不复用被测方的 `_去注释`）。
    """
    去块 = re.sub(r'/-.*?-/', ' ', 源, flags=re.DOTALL)
    return re.sub(r'--[^\n]*', ' ', 去块)


def _定理块(代码: str, 名: str):
    """取**该定理自己那一段**（到下一条 theorem/lemma/end 为止），不是整个文件。"""
    m = re.search(rf'^\s*(?:theorem|lemma)\s+{re.escape(名)}\b', 代码, re.MULTILINE)
    if m is None:
        return None
    尾 = re.search(r'^\s*(?:theorem|lemma|end)\b', 代码[m.end():], re.MULTILINE)
    截止 = m.end() + (尾.start() if 尾 else 0)
    return 代码[m.start():截止]


print(f"mem_lookup_a：按名取回路径（不靠遍历）（档案 {prof.source_path}）")
断言(bool(prof.memory),
     f'档案声明了 `memory:` 段（{len(prof.memory)} 条）—— 名字→路径的键空间由它定')

try:
    # ── ① 建索引：把"遍历"的代价**量下来** ──────────────────────────
    t0 = time.time()
    os.scandir = _数枚举
    try:
        索引 = ae.MemoryIndex()
        出 = ae.scan_project(索引, prof.artifact.root, 仓)
    finally:
        os.scandir = 真枚举
    建库秒 = time.time() - t0
    断言(计数['建库'] > 100,
         f'建索引期**目录枚举 {计数["建库"]} 次**（{建库秒:.1f}s）—— 这就是"遍历"的代价，'
         f'取回期要小得多（本判据要求是 0）')
    断言(索引.count('formal') > 0 and 索引.count('rs') > 0 and 索引.count('doc') > 0,
         f'三类都在索引里（formal={索引.count("formal")} / rs={索引.count("rs")} '
         f'/ doc={索引.count("doc")}）—— 三样名字各有归口')
    断言(出.get('声明了却没扫到') == [],
         f'档案声明的根都在（没扫到 = {出.get("声明了却没扫到")}）')

    # ── ② ★ 关掉遍历，再按名取回（这一段的结论就是本判据的主体）──────
    print('② ★ 关掉遍历（os.scandir = 炸弹）后仍按名取回，路径逐字断言')
    期望表 = {
        '定理名': 定理文件,
        '定理模块键': 定理文件,
        'Rust 模块名': 模块文件,
        '回执名（路径键）': 回执文件,
        '回执名（stem 键）': 回执文件,
    }
    os.scandir = _炸弹
    try:
        取回表 = {
            '定理名': 索引.index('formal').query(定理名),
            '定理模块键': 索引.index('formal').query(定理模块键),
            'Rust 模块名': 索引.index('rs').query(模块名),
            '回执名（路径键）': 索引.index('doc').query(回执键),
            '回执名（stem 键）': 索引.index('doc').query(回执名),
        }
        for 标签, 期望 in 期望表.items():
            实得 = 取回表[标签]
            断言(实得 is not None and Path(实得) == 期望,
                 f'取回：{标签} → {实得}')
        断言(Path(取回表['定理名']) == Path(取回表['定理模块键']),
             '定理键与模块键指向**同一个文件**（定理键是从模块键派生的）')

        # ── ③ 区分力：四条反例 ───────────────────────────────────────
        print('③ ★ 区分力：不存在的名字 / 近似名 / 植假路径 / 删键')
        假名们 = ('MyMeta.InfraCore.Derivation.InfinitePan.绝无此定理_20261104',
                  '绝无此模块_20261104',
                  '.audit_trail/绝无此回执_20261104.md')
        for 名 in 假名们:
            实测 = [(k, str(索引.index(k).query(名))) for k in ('formal', 'rs', 'doc')]
            断言(all(v == 'None' for _, v in 实测),
                 f'③-① 不存在的名字取不回：`{名}`（实测 {实测}）')
        近似名们 = (定理名[:-1], 模块名 + '_后缀不存在', 回执名 + '.bak')
        for 名 in 近似名们:
            实测 = [(k, 索引.index(k).query(名)) for k in ('formal', 'rs', 'doc')]
            断言(all(v is None for _, v in 实测),
                 f'③-② 近似名不许命中（不许前缀/模糊匹配偷答案）：`{名}`')

        植入路径 = 仓 / 'src' / '绝无此模块_20261104.rs'
        新索引 = ae.MemoryIndex()
        新索引.register('rs', 模块名, 植入路径)
        植入取回 = 新索引.index('rs').query(模块名)
        断言(植入取回 is not None and Path(植入取回) == 植入路径
             and not 植入路径.is_file(),
             f'③-③ 同名 register 到磁盘上**不存在**的路径 ⇒ 取回的就是那条路径'
             f'（{植入路径}）—— 若取回是现场遍历找的，这条必红')

        文档索引 = 索引.index('doc')
        文档索引._units.pop(回执键, None)
        断言(文档索引.query(回执键) is None,
             '③-④ 把键从索引里删掉 → 同名取回变 None（命中确实依赖**键存在**）')
    finally:
        取回期枚举 = 计数['取回']
        os.scandir = 真枚举
    断言(取回期枚举 == 0,
         f'取回期**目录枚举 {取回期枚举} 次**（必须 0）—— '
         f'名字→路径由索引回答，不靠现场遍历')

    # ── ④ 名字 ↔ 文件互证（恢复遍历后做：读的是文件本身）────────────
    print('④ 名字 ↔ 文件互证（读文件本身，不是读索引）')
    定理源 = _读(定理文件)
    断言('theorem si_zhu_infinite_well_defined' in 定理源,
         f'定理名真的写在取回的那个文件里：{定理文件.name}（{len(定理源)} 字符）')
    定理代码 = _剥注释(定理源)
    定理块 = _定理块(定理代码, 'si_zhu_infinite_well_defined')
    断言(定理块 is not None
         and not re.search(r'\bsorry\b|\badmit\b', 定理块),
         '该定理**自己那段**里没有 sorry/admit ⇒ 取回的是**已证明的**定理名')
    断言(not re.search(r'\bsorry\b|\badmit\b', 定理代码),
         f'{定理文件.name} 的**代码**里 0 sorry / 0 admit'
         f'（散文里那个「0 sorry」不算 —— 先剥注释再读，别拿散文当代码）')
    lib源 = _读(仓 / 'src' / 'lib.rs')
    断言(f'pub mod {模块名};' in lib源,
         f'模块名在 `src/lib.rs` 里被 `pub mod {模块名};` 声明 ⇒ 它是接了线的真模块')
    模块源 = _读(模块文件)
    断言('pub fn' in 模块源,
         f'{模块文件.name} 里有 `pub fn` ⇒ 取回的是**有实现**的模块文件'
         f'（{len(模块源)} 字符）')
    断言(回执文件.is_file() and 回执文件.stat().st_size > 0,
         f'回执文件存在且非空（{回执文件.stat().st_size} 字节）')

except Exception:                                              # noqa: BLE001
    traceback.print_exc()
    断言(False, '判据自身出错（上面的 traceback 是归因，不是被测方的红）')
finally:
    os.scandir = 真枚举

print()
print(f"代价对照：建索引 **{计数['建库']} 次**目录枚举 / {建库秒:.1f}s（首次）；"
      f"取回 **{取回期枚举} 次** / 常数级")
if 失败:
    print(f"mem_lookup_a：红 {len(失败)} 项 —— {失败[0]}")
    sys.exit(1)
print('mem_lookup_a：绿 —— 定理名 / Rust 模块名 / 审计回执名三样**关掉遍历**仍取得到'
      '且路径逐字正确；不存在的名字取不回、近似名不命中、'
      '取回只由索引给（植假路径跟着变、键删了就查不到）')
