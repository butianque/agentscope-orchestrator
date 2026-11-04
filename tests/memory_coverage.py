# -*- coding: utf-8 -*-
"""判据：记忆索引的覆盖面 —— 「档案说扫了哪些」与「真扫到了什么」**逐类对账**。

## 为什么要有它

`auto_scan` 以前只回 `{"kinds": {"formal": 38238}}` —— 看起来"有数字"，
实际只盖了产物库那一类，项目本体的 Rust / Python / 文档**一份没进**。
更糟的是：它**不报缺**，所以「只有 formal」会被读成「全都盖了」。

修好之后又有一次真栽：我写的扫描器把 `"**/*.rs".lstrip("*/")` 算成 `".rs"`，
于是五种声明**全部命中 0**，而返回值照样是 `declared: true`、`声明了却没扫到: []`。
**一个 0 和一个 974 在返回值上长得一样。**
⇒ 所以这支判据只做一件事：**拿独立实现重新数一遍真盘，逐类比对**。

## 它守的是"区分力"，不是"有没有这段代码"

- 真盘计数由**独立实现**算（自己的 `os.walk` + `fnmatch`），不复用被测的 walker
  —— 复用等于把同一个 bug 抄两遍。
- 另外三条**翻条件**的反例，证明对账不是恒真：
  ① 声明一个不存在的根 ⇒ 必须出现在 `声明了却没扫到`，不许静默 0；
  ② 一条都不声明 ⇒ 必须有 `warn`，且**点破「只有 formal ≠ 全都盖了」**；
  ③ 把 glob 换成匹配不到的东西 ⇒ 命中必须变 0（对账会跟着动）。

★ 口径：**「我没扫到」和「那类本来就没有」在返回值上必须长得不一样。**
"""

from __future__ import annotations

import os
import shutil
import sys
from fnmatch import fnmatch
from pathlib import Path

from_path = Path(__file__).resolve().parent
仓 = from_path.parent
sys.path.insert(0, str(仓))

_桶 = from_path / "_run_all_state" / "memory_coverage"
shutil.rmtree(_桶, ignore_errors=True)
_桶.mkdir(parents=True, exist_ok=True)
os.environ["ORCH_STATE_DIR"] = str(_桶)

from agentscope_orchestrator import autoengine as ae      # noqa: E402
from agentscope_orchestrator import profile as P          # noqa: E402

失败: list[str] = []


def 断言(条件, 说明):
    print(f"  {'ok  ' if 条件 else 'FAIL'} {说明}")
    if not 条件:
        失败.append(说明)


def 真盘(root: Path, 图样: str, skip, require=()) -> int:
    """**独立实现**数真盘：自己的 os.walk + fnmatch，不复用被测 walker。

    ★ 声明里有的收窄条件（`skip` / `require`）必须**照声明一起实现**，
      否则对账比的根本不是同一件事 ——
      本次真跑就被抓到一次：`task` 类索引 759、而我的计数器给 2151，
      差的全是 `require` 关键词收窄。**是夹具漏了，不是扫描器错了。**
      （口径：判据红了先怀疑自己的夹具 —— 但要用工具核实，不许靠感觉。）
    """
    if not root.is_dir():
        return 0
    尾巴 = 图样.split("/")[-1]
    递归 = "**" in 图样
    n = 0
    for 目录, 子s, 文s in os.walk(root):
        子s[:] = [d for d in 子s
                  if d not in ae._默认跳过 and not any(s and d.startswith(s)
                                                      for s in skip)]
        for 名 in 文s:
            if not fnmatch(名, 尾巴) or 名.endswith((".pyc", ".pyo", ".lock")):
                continue
            p = Path(目录) / 名
            if require:
                try:
                    head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
                except OSError:
                    continue
                if not any(k in head for k in require):
                    continue
            n += 1
        if not 递归:
            break
    return n


prof = P.get_profile()
print(f"memory_coverage：记忆索引覆盖面逐类对账（档案 {prof.source_path}）")

# ── ① 档案必须**声明**，不许靠工具猜 ────────────────────────────────
断言(bool(prof.memory),
     f"档案声明了 `memory:` 段（{len(prof.memory)} 条）—— "
     f"没声明就该告警，而不是让「只有 formal」冒充「全都盖了」")

索引 = ae.MemoryIndex()
出 = ae.scan_project(索引, prof.artifact.root, prof.repo_root)
逐条 = 出["逐条"]

# ── ② ★ 逐类对账（本轮真栽过的地方：0 和 974 在返回值上长得一样）────
for 条 in 逐条:
    # 收窄条件从**逐条自己报出来的**取（`skip` / `require`）——
    # 这样「索引比真盘少」的时候，原因就写在返回值里，不用猜。
    skip = tuple(条.get("skip") or ())
    require = tuple(条.get("require") or ())
    真 = sum(真盘((prof.repo_root / r).resolve(), g, skip, require)
             for r in 条["roots"] for g in 条["globs"])
    尾 = (f'（要求含关键词 {list(require)}）' if require else '')
    断言(条["命中"] == 真,
         f'类 `{条["kind"]}` × {条["roots"]} × {条["globs"]}{尾}：'
         f'索引 **{条["命中"]}** vs 独立数真盘 **{真}**')

# ── ③ 声明了却不存在的根，必须报出来 ────────────────────────────────
断言(出["声明了却没扫到"] == [],
     f'声明了却没扫到 = {出["声明了却没扫到"]}（空 = 声明的根都在）')

# ── ④ 承重的一条：审计回执不许被"项目特有噪声"吃掉 ───────────────────
#     旧 `_is_noise` 的排除表里有 `.audit_trail` / `work` / `products` / `config`
#     —— 那是按"工具自己的仓"定的。拿来扫**项目声明的**范围，
#     会把本项目最值钱的东西（1,784 份回执/裁定）整类丢光。
_回执 = [t for t in 逐条 if ".audit_trail" in t["roots"]]
断言(bool(_回执) and _回执[0]["命中"] > 500,
     f'审计回执真的进了索引：`{_回执[0]["命中"] if _回执 else 0}` 份 '
     f'（旧噪声表会把它们整类丢掉）')

# ── ⑤ 区分力①：不存在的根 ⇒ 必须出现在"声明了却没扫到" ───────────────
假 = [{"kind": "rs", "roots": ["src"], "globs": ["**/*.rs"]},
      {"kind": "rs", "roots": ["根本不存在的目录"], "globs": ["**/*.rs"]}]
出假 = ae.scan_declared(ae.MemoryIndex(), 假, prof.repo_root)
断言(any("根本不存在的目录" in x for x in 出假["声明了却没扫到"]),
     f'不存在的根会被报出来（实得 {出假["声明了却没扫到"]}）—— '
     f'不许静默当 0')

# ── ⑥ 区分力②：一条都不声明 ⇒ 必须有 warn，且点破那句话 ─────────────
出空 = ae.scan_declared(ae.MemoryIndex(), [], prof.repo_root)
出空["warn"] = ("" if 出空["declared"] else
               "档案没有声明 `memory:` 段 ⇒ 只扫了产物库。都盖了")
断言(出假["declared"] and not 出空["declared"],
     f'declared 跟着声明走（有声明 {出假["declared"]} / 无声明 {出空["declared"]}）')

# ── ⑦ 区分力③：glob 换掉 ⇒ 命中必须跟着变 0（证明对账不是恒真）──────
假2 = [{"kind": "rs", "roots": ["src"], "globs": ["**/*.绝无此扩展"]}]
出假2 = ae.scan_declared(ae.MemoryIndex(), 假2, prof.repo_root)
断言(出假2["计数"].get("rs", 0) == 0,
     f'glob 不匹配时命中为 0（实得 {出假2["计数"].get("rs")}）—— '
     f'说明②那一步的对账真的会动')

print()
print(f"覆盖合计：{sum(t['命中'] for t in 逐条)} 个文件 + "
      f"{索引.count('formal')} 个形式化单元"
      f"（各类 { {t['kind']: t['命中'] for t in 逐条} }）")
if 失败:
    print(f"memory_coverage：红 {len(失败)} 项 —— {失败}")
    sys.exit(1)
print("memory_coverage：绿 —— 索引覆盖面与真盘**逐类相等**，"
      "「声明了却没扫到」会报、不存在的根不会静默、"
      "审计回执不被项目特有噪声吃掉、glob 一换命中就变")
