"""主树项目理解索引：文件清单 + 顶层符号 + use 依赖 + 跨文件相关检索。

背景：编排器（generic_goal）之前的 repo_scope 机制①只把目标文件全文 + 规约声明的
依赖（截断）注入执行智能体，没有项目结构/符号/引用关系信息 → 曾误删目标文件后半段
仍自认为正确。本模块让编排器"读懂项目"：自己建主树索引、跨文件检索相关上下文，
把机器扫库所得的真值（文件、符号签名、use 引用关系）喂给规划器与执行器。

诚实哲学与 index.py / gate_spec 一致：
  - 索引只记元数据（文件清单/符号/use 关系），不缓存文件全文（取回按需读盘）。
  - 索引是机器扫主树所得真值，只"喂给模型看"，不代写码；判据门仍是机器硬判定。
  - 只读主树，绝不写主树。

能力：
  A. 机器索引：主树 src/**/*.rs 全清单 + 每文件顶层符号（pub fn/struct/const/type/
     enum/trait，存签名原文行）+ use 依赖模块路径。mtime+size 指纹增量缓存。
  B. 跨文件检索 related()：目标 use 的模块（正向）+ 反引用的文件（反向）+ 符号命中
     → 按相关性排序、cap 截断返回注入文本。
  C. 结构快照 structure_snapshot()：从目标关键词命中的符号 + src/ 模块级概览，
     注入 planner 让规约更准（选对文件、grep 列全真实符号、门更强）。

缓存落位：{AUDIT}/repo_index.json（AUDIT 由档案决定）。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agentscope_orchestrator import profile as prof_mod

logger = logging.getLogger("repo_index")


def _p():
    return prof_mod.get_profile()


# ── 路径事实（全部由档案派生；工具不持有项目路径）───────────────
def ORCH() -> Path:
    return prof_mod.TOOL_ROOT


def REPO_ROOT() -> Path:
    return _p().repo_root


def AUDIT() -> Path:
    return _p().audit_dir


def CACHE() -> Path:
    return AUDIT() / "repo_index.json"


def _skip() -> set:
    return set(_p().source.skip)


def _src_roots() -> list[Path]:
    prof = _p()
    out = []
    for r in prof.source.roots:
        p = Path(r)
        out.append(p if p.is_absolute() else prof.repo_root / p)
    return out or [prof.repo_root / "src"]


def _globs() -> tuple:
    return _p().source.globs or ("**/*.rs",)

# 注入容量（字符）
_SNAP_CAP = 6000    # planner 结构快照上限
_REL_CAP = 8000     # 执行器相关上下文上限
_SYM_CAP = 2000     # 单模块符号摘要上限

# 中英标识符（复用 develop 的 `[\w\u4e00-\u9fff]` 风格）
_IDENT = r"[a-zA-Z0-9_\u4e00-\u9fff]+"

# ★★★★★ R47b：**抽取器版本号**。抽取逻辑（正则/字段含义）一变就必须 +1，
#   否则旧缓存**永远不失效**（文件没动 ⇒ mtime/size 没变 ⇒ 不重算）
#   ⇒ "修好了却读不到新结果"。★ 这是缓存正确性的通病，不是本项目特例。
_EXTRACTOR_VER = 2
_VER_KEY = "__抽取器版本__"

# ═══════════════════════════════════════════════════════════════════
# ★★★★★ R47（真跑暴露的**通用性**缺口 · 2026-10-15）：
#   这三条正则原本是**写死的 Rust 语法**（`pub fn` / `use a::b` / `impl Foo`）。
#   ⇒ 换到 Python / TS / Go / C 工程上，**符号索引与依赖图全是空的**
#     ⇒ 规划器拿到空的结构快照 ⇒ **又变成 R33 那种"计划是在盲的"**。
#   ★ 这是"通用工程开发工具"的**硬要求**：工具本体不得只认一种语言。
#   ★ 修法遵本库一贯纪律（bootstrap R27「表是数据，通路读表」）：
#     把"声明"和"导入"抽象成**跨语言的全称形态** —— **一条正则**，
#     覆盖各生态的同类关键字，**没有一处 `if 语言 ==`**。
#     声明 = 「可见性/修饰符* + 声明关键字 + 名字」；
#     导入 = 「导入关键字 + 模块路径」。
#     这与 `samples._声明行` 已经是同一形态（那里也一条正则覆盖多语言）。
#   ★ 为什么不是"每种语言一张表"：那张表会长成 N 个分支，而这里要的
#     只是**索引**（给模型看的符号清单），不是**语法解析** ——
#     索引容得下多抓，容不下"漏光"。⇒ 全称形态在这里**更准也更简单**。
# ═══════════════════════════════════════════════════════════════════

# 顶层声明行（跨语言全称）：可见性/修饰符前缀 + 声明关键字 + 名字
#   rust: pub fn/struct/enum/trait/const/type   python: def/class
#   ts/js: export function/const/class/interface/type   go: func/type/struct
#   c/c++: struct/class/typedef/enum/union   java/c#: public class/interface
#   lean: theorem/lemma/def/structure   sql/pascal 等: procedure/function/sub
_修饰 = (r"(?:pub|public|private|protected|internal|export|open|static|"
         r"abstract|final|async|unsafe|extern|inline|virtual|override|"
         r"sealed|partial|declare|default)\s+")
#   ★ 自检 ① 抓出：`const` 曾同时出现在**修饰**与**声明词**里 ⇒ 贪婪的修饰循环
#     把 `pub const 丙` 的 `const` 吃掉了 ⇒ 后面接不上声明词 ⇒ **`pub const` 漏抽**。
#     ⇒ 一个词只能在一个位置。`const`/`static` 属**声明词**，不属修饰。
_声明词 = (r"(?:fn|func|function|def|procedure|sub|struct|enum|trait|"
           r"interface|type|class|impl|mod|module|namespace|record|union|"
           r"typedef|theorem|lemma|structure|instance|macro|object|protocol|"
           r"const|static)")
_SYM_RE = re.compile(
    r"^[ \t]*(?:" + _修饰 + r")*" + _声明词 + r"\s+(" + _IDENT + r")\b",
    re.MULTILINE)

# 导入行（跨语言全称）：导入关键字 + 模块路径
#   rust: use crate::a::b;      python: import a.b / from c.d import e
#   js/ts: import x from 'a/b' / require('a/b')   c/c++: #include <a/b.h>
#   go: import "a/b"   java/c#: import a.b.C / using A.B;   lean: import A.B
#   ruby/php: require 'a/b' / include 'a/b'
#   ★ 自检 ⑧ 抓出两处（都不是加语言分支，而是把全称形态写全）：
#     ① JS 的 `import x from '路径'` 里，`import` 后跟的是**绑定名**而不是路径
#        ⇒ 必须**同时**认行中的 `from '…'` / `require('…')` / `include '…'`；
#     ② 开引号可能是**一串**（`require('路径')` 里是 `(` 加 `'` 两个字符）
#        ⇒ 原来写 `[\(\"'<]?`（只允许一个）会漏掉整条 ⇒ 改成**允许一串**。
#     ※ 已知无害噪声：`import x from …` 会额外抽到绑定名 `x`。
#       它解析不到任何文件 ⇒ 下游自动忽略。**不为此加特例**（那会引入分支）。
_USE_RE = re.compile(
    r"(?:^[ \t]*(?:use|using|import|from|require|include|open|#include)\b"
    r"[\s\(\"'<]*"
    r"|(?<![\w])(?:from|require|include)\b[\s\(\"'<]*)"
    + r"(" + _IDENT + r"(?:(?:::|\\.|/)" + _IDENT + r")*)",
    re.MULTILINE)

# 实现/继承目标：`impl Foo`(rust) / `implements Foo` / `extends Foo`(ts/java)
#   → 归为"被引用类型"
#   ★ 自检 ⑨ 抓出：`class 丙 implements 丁` 的 `implements` **不在行首** ⇒
#     只认行首 `impl` 会漏。⇒ `impl` 锚行首（rust 语法），
#     `implements|extends` 允许行中（java/ts 语法）。
_IMPL_RE = re.compile(
    r"(?:^[ \t]*impl\b(?:\s*<[^>]*>)?\s*"
    r"|(?<![\w])(?:implements|extends)\b(?:\s*<[^>]*>)?\s*)"
    r"(" + _IDENT + r")\b", re.MULTILINE)


def _extract(text: str) -> tuple[list[str], list[str]]:
    """解析源码 → (uses, symbols)。uses：外部模块路径；symbols：签名原文行。"""
    uses: list[str] = []
    use_seen: set[str] = set()
    for m in _USE_RE.finditer(text):
        mod = m.group(1).rstrip("::")
        if mod and mod not in use_seen:
            use_seen.add(mod)
            uses.append(mod)
    for m in _IMPL_RE.finditer(text):
        t = m.group(1)
        if t not in use_seen:
            use_seen.add(t)
            uses.append(t)
    symbols: list[str] = []
    sym_seen: set[str] = set()
    for m in _SYM_RE.finditer(text):
        name, kind = m.group(1), m.group(0).strip()
        if name and name not in sym_seen:
            sym_seen.add(name)
            symbols.append(kind[:160])   # 只存签名行（原文其中一行），不存整段
    return uses, symbols


def _rel_key(p: Path) -> str:
    """相对仓库根的 src-相对路径，统一正斜杠（如 src/gpu/gpu探测.rs）。"""
    return p.relative_to(REPO_ROOT()).as_posix()


def _fingerprint(p: Path) -> tuple[float, int]:
    st = p.stat()
    return st.st_mtime_ns, st.st_size


def _build() -> dict[str, dict]:
    """全量扫描档案声明的源码根，返回 {rel: entry}。"""
    entries: dict[str, dict] = {}
    roots = _src_roots()
    seen: set = set()
    for root in roots:
        if not root.is_dir():
            continue
        for g in _globs():
            for p in sorted(root.rglob(g)):
                if p in seen or not p.is_file():
                    continue
                seen.add(p)
                if any(s in p.parts for s in _skip()):
                    continue
                rel = p.relative_to(REPO_ROOT()).as_posix()
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
                mt, size = _fingerprint(p)
                uses, symbols = _extract(text)
                entries[rel] = {"rel": rel, "mtime_ns": mt, "size": size,
                                "uses": uses, "symbols": symbols}
    return entries


def ensure(force: bool = False) -> int:
    """mtime+size 指纹增量构建索引缓存；force=True 全量重建兜底。
    返回索引条目数。缓存现存且指纹一致则跳过该文件（增量）。"""
    cur = _build()
    if force or not CACHE().is_file():
        _save(cur)
    else:
        try:
            old = json.loads(CACHE().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}
        # ★★★★★ R47b：**抽取器版本必须参与缓存失效判定**。
        #   【为什么】原实现只看每个文件的 mtime/size。而 R47 改的是**抽取逻辑**
        #   （正则），**文件本身一个字节没动** ⇒ 缓存**永远不失效** ⇒
        #   非 Rust 工程即使修好了，读到的仍是**旧缓存里的空符号表** ⇒
        #   **修了却看不出修好**（最坏的一种：缺陷被缓存掩盖，排查方向全错）。
        #   ★ 这是"缓存正确性"的通病：**只要输入集合没变，就不会重算**，
        #     哪怕**计算方式**变了。⇒ 版本号必须进缓存。
        _旧版本 = old.pop(_VER_KEY, None)
        dirty = _旧版本 != _EXTRACTOR_VER
        # 目录扫描已是最新全集 cur；任何 mtime/size 变化或新增/删除都判脏
        if not dirty:
            for rel, e in cur.items():
                o = old.get(rel)
                if o is None or o.get("mtime_ns") != e["mtime_ns"] \
                   or o.get("size") != e["size"]:
                    dirty = True
                    break
            if len(old) != len(cur):
                dirty = True
        if dirty:
            _save(cur)
    return len(cur)


# ★ 2026-10-31（交付审计 P1-3）：缓存写失败原先只 `logger.warning` 一行 ⇒
#   表面上"没影响"，实际是**每次冷启动都全量重建**（缓存永远落不了盘）。
#   真因不是代码坏，而是落点在项目审计目录里：该目录可能只读、或文件被常驻进程占用
#   （实测：`.audit_trail/repo_index.json` 被 MCP 服务器持有 ⇒ PermissionError）。
#   ⇒ 落点降级：项目目录写不进去就退到 **状态根**（一定可写），并记住降级事实供读数。
_降级: dict[str, str] = {}


def _备用缓存() -> Path:
    return prof_mod.STATE_ROOT / "cache" / "repo_index.json"


def 缓存状态() -> dict:
    """缓存落点与降级原因（供自检/读数；没有降级时为空 dict）。"""
    return dict(_降级)


def _写缓存(路径: Path, payload: dict) -> None:
    路径.parent.mkdir(parents=True, exist_ok=True)
    路径.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _save(entries: dict[str, dict]) -> None:
    payload = dict(entries)
    payload[_VER_KEY] = _EXTRACTOR_VER
    首 = CACHE()
    try:
        _写缓存(首, payload)
        _降级.pop("原因", None)
        return
    except Exception as e:                     # noqa: BLE001
        首因 = f"{type(e).__name__}: {e}"
    try:
        _写缓存(_备用缓存(), payload)
        _降级.update({"原因": f"项目审计目录不可写（{首因}）", "落点": str(_备用缓存())})
        logger.info("repo_index 缓存降级到状态根：%s", _备用缓存())
    except Exception as e:                     # noqa: BLE001
        _降级.update({"原因": f"两处都写不进（{首因}；备用 {type(e).__name__}: {e}）",
                      "落点": ""})
        logger.warning("repo_index 缓存两处均写不进，本次只在内存里用", exc_info=True)


def _读缓存() -> dict[str, dict]:
    """读缓存条目（剥掉版本键）。读不到 ⇒ 空表（不抛）。先主落点，再备用落点。"""
    for 路 in (CACHE(), _备用缓存()):
        try:
            d = json.loads(路.read_text(encoding="utf-8"))
        except Exception:                      # noqa: BLE001
            continue
        if isinstance(d, dict):
            d.pop(_VER_KEY, None)
            return d
    return {}


def _load() -> dict[str, dict]:
    try:
        d = json.loads(CACHE().read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.pop(_VER_KEY, None)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        ensure(force=True)
        return _读缓存()


def entry(rel: str) -> dict | None:
    """取单文件条目（无则 None）。rel 为 src-相对路径（src/gpu/gpu探测.rs）。"""
    return _load().get(rel)


def _crate_prefixes() -> tuple[str, ...]:
    """crate 前缀形态（档案 crate_prefixes 声明；未声明 ⇒ 仅通用 crate::）。

    项目专有的 crate 名（连字符/下划线两种形态）属项目知识，由档案给出。
    """
    declared = _p().raw.get("crate_prefixes") or []
    out = ["crate::"]
    for d in declared:
        d = str(d)
        out.append(f"{d}::")
        out.append(f"{d}.")
    return tuple(out)


def _src_rel_prefixes() -> tuple[str, ...]:
    """源码根的相对前缀形态（把点式键还原成相对路径）。"""
    out = []
    for r in _p().source.roots:
        rr = str(r).replace("\\", "/").strip("/")
        if rr:
            out.append(rr + ".")
            out.append(rr + "/")
    return tuple(out or ("src.", "src/"))


def _src_rel_bases() -> tuple[str, ...]:
    out = []
    for r in _p().source.roots:
        rr = str(r).replace("\\", "/").strip("/")
        if rr:
            out.append(rr)
    return tuple(out or ("src",))


# ★ R47：目录式模块的**入口文件名**惯例（各生态，**表是数据**；通路只读表）
#   rust: a/b/mod.rs    python: a/b/__init__.py    js/ts: a/b/index.ts
_模块入口: tuple[str, ...] = ("mod", "__init__", "index")


def _exts() -> tuple[str, ...]:
    """档案声明的源码后缀集（**由 globs 派生**，工具不写死语言）。

    ★ R47：原实现把 `.rs` / `mod.rs` **写死**在解析逻辑里 ⇒ 非 Rust 工程
      的模块永远解析不到文件。现改为从档案的 globs 取后缀。
    """
    out: list[str] = []
    for g in _globs():
        s = str(g)
        i = s.rfind(".")
        if i >= 0:
            e = s[i:]
            if e and e not in out:
                out.append(e)
    return tuple(out or (".rs",))


def _rel_file_of_module(mod: str) -> str | None:
    """模块路径 → 源码相对路径。剥 crate 前缀后逐段回退查找。

    支持形态：a / a::b / crate::a::b / <crate>::a::b / 点式键 src.a /
             a.b（python）/ a/b（js、c include）。
    ★ R47：后缀与目录入口名**从档案派生**（原实现写死 `.rs`/`mod.rs`）。
    命中即返回 rel，否则 None。"""
    seg = mod
    for prefix in _src_rel_prefixes():
        if mod.startswith(prefix):
            seg = mod[len(prefix):]
            break
    seg = seg.rstrip("::")
    # 剥 crate 前缀（档案声明 + 通用 crate::）
    for pre in _crate_prefixes():
        if seg.startswith(pre):
            seg = seg[len(pre):]
            break
    # ★ R47：`::`（rust）/ `.`（python、java）/ `/`（js、c）都当分隔符
    segs = [s for s in re.split(r"::|[./\\]", seg) if s]
    idx = _load()
    # 候选 rel：全段聚合的每级前缀，优先最长匹配（子/孙模块归属目录）
    for depth in range(len(segs), 0, -1):
        relpath = "/".join(segs[:depth])
        for base in _src_rel_bases():
            cands = [f"{base}/{relpath}{e}" for e in _exts()]
            cands += [f"{base}/{relpath}/{n}{e}"
                      for n in _模块入口 for e in _exts()]
            for cand in cands:
                if cand in idx:
                    return cand
    return None


def related(target_rel: str, cap: int = _REL_CAP) -> str:
    """跨文件相关上下文：目标 use 的模块（正向）+ 反引用的文件（反向）+
    符号命中定位。按"直接 use > 反引用 > 符号命中"排序、cap 截断返回注入文本。
    target 无条目或未命中 → 空串（退化为现实现，不破坏 dry/sandbox 路径）。"""
    idx = _load()
    tgt = idx.get(target_rel)
    if not tgt:
        return ""
    # 正向：目标 use 到的模块 → 其真实文件符号摘要
    forward: list[str] = []
    for mod in tgt.get("uses", []):
        rel = _rel_file_of_module(mod)
        if rel and rel != target_rel and idx.get(rel) and rel not in forward:
            forward.append(rel)
    # 反向：全索引哪个文件引用了目标（use/impl 命中的目标符号名）
    tgt_names = set()
    for s in tgt.get("symbols", []):
        m = _SYM_RE.match(s)
        if m:
            tgt_names.add(m.group(1))
    backref: list[str] = []
    for rel, e in idx.items():
        if rel == target_rel:
            continue
        hits = [s for s in e.get("uses", [])
                if any(t == s.split("::")[-1] for t in tgt_names)]
        if hits:
            backref.append(rel)
    # 组装输出：正向模块符号摘要 + 反向文件清单（带符号命中）
    parts: list[str] = []
    for rel in forward[:6]:
        e = idx[rel]
        sym = (e.get("symbols") or [])[:12]
        if not sym:
            continue
        blob = "\n".join(f"  {s}" for s in sym)
        cap_one = max(0, cap - len("".join(parts)))
        if cap_one < 200:
            break
        parts.append(f"[相关模块 {rel}] 顶层符号：\n{blob[:cap_one]}")
    for rel in backref[:4]:
        parts.append(f"[反引用本文件的模块 {rel}]")
    if not parts:
        return ""
    body = "\n".join(parts)
    if len(body) > cap:
        body = body[:cap] + ("\n...[相关上下文截断：仅注入前几项，未注入项不涉及本次改动]")
    return ("【项目相关上下文（机器扫主树索引所得真值，仅参考；改时以目标文件整文件为准）】\n"
            + body)


def _strip_kw(goal: str) -> set[str]:
    """从自然语言目标抽候选符号关键词（中英标识符 token，去掉纯长度筛余）。"""
    kw = re.findall(_IDENT, goal)
    out = set()
    for k in kw:
        k = k.strip()
        if len(k) >= 2 and k not in {"use", "for", "and", "mod", "fn"}:
            out.add(k)
    return out


def structure_snapshot(goal: str = "", cap: int = _SNAP_CAP) -> str:
    """规划快照：目标关键词命中的真实符号 + src/ 模块级概览。机器真值仅参考。"""
    idx = _load()
    if not idx:
        ensure(force=True)
        idx = _load()
    kws = _strip_kw(goal)
    lines: list[str] = []
    # 1) 目标关键词 → 真实符号命中（优先列）
    if kws:
        hit: list[tuple[str, str]] = []
        for rel, e in idx.items():
            for s in e.get("symbols", []):
                m = _SYM_RE.match(s)
                if not m:
                    continue
                if m.group(1) in kws:
                    hit.append((rel, s))
        for rel, s in hit[:20]:
            lines.append(f"  {rel}: {s}")
    # 2) src/ 模块级概览（每模块 .rs 数 + 顶层符号数）
    per_mod: dict[str, int] = {}
    for rel, e in idx.items():
        parts = rel.split("/")
        mod = parts[1] if len(parts) >= 3 else "src/"
        per_mod[mod] = per_mod.get(mod, 0) + 1
    top = sorted(per_mod.items(), key=lambda kv: -kv[1])[:25]
    for mod, cnt in top:
        lines.append(f"  {mod or 'src/'}: {cnt} 个 .rs")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > cap:
        body = body[:cap] + "\n...[快照截断]"
    return (f"【项目结构快照（机器扫主树所得真值，仅参考）】主树 src/ 共 {len(idx)} 个 .rs。\n"
            + body)


# 模块级首次构建（仿 MCP 服务顶部执行一次）★ R47 自检见文件末
try:
    ensure()
except Exception as _e:  # noqa: BLE001
    logger.warning("repo_index initial build failed: %s", _e)


# ═══════════════════════════════════════════════════════════════════
# 纯机器自检（**不碰网络、不碰主树**：只在内存文本上跑正则）
# ═══════════════════════════════════════════════════════════════════
# ★★★★★ 为什么本件必须有自检（R47 的教训）：
#   本模块此前**没有自检** —— 而它正是"规划器结构快照"的唯一来源。
#   它把语言写死成 Rust，**没有任何地方会因此报错**：非 Rust 工程上
#   索引**静默为空**，规划器只是"变笨了"，看起来像模型不行。
#   ⇒ 没自检的模块，缺陷**没有地方暴露**。这条自检专门钉**通用性**。
def _selftest() -> int:
    fails: list[str] = []
    N = 11

    def chk(名: str, 条件: bool, 详情: str = "") -> None:
        if not 条件:
            fails.append(f"{名}{('：' + 详情) if 详情 else ''}")

    # ① Rust 符号仍抽得出（**防通用化把老行为改坏**）
    _rs = ("pub fn 甲() {}\npub struct 乙;\npub const 丙: u32 = 1;\n"
           "pub trait 丁 {}\npub type 戊 = u32;\npub enum 己 { A }\n")
    _, _s1 = _extract(_rs)
    _n1 = {x.split()[-1].split("(")[0].split(":")[0].split("<")[0] for x in _s1}
    chk("①Rust符号", {"甲", "乙", "丙", "丁", "戊", "己"} <= _n1, f"得 {sorted(_n1)}")

    # ② ★★★★★ **Python** 符号必须抽得出（**旧实现这里是空的**）
    _py = ("import os\nfrom a.b import c\n\n"
           "class 处理器:\n    pass\n\ndef 计算(值):\n    return 值\n\n"
           "async def 取数():\n    pass\n")
    _, _s2 = _extract(_py)
    chk("②Python符号（旧实现为空）",
        any("处理器" in x for x in _s2) and any("计算" in x for x in _s2),
        f"得 {_s2}")

    # ③ ★★★★★ **TS/JS** 符号必须抽得出
    _ts = ("export function 求值(x: number) { return x }\n"
           "export const 常量 = 1\n"
           "export class 服务 {}\n"
           "interface 契约 {}\n"
           "type 别名 = number\n")
    _, _s3 = _extract(_ts)
    chk("③TS符号", any("求值" in x for x in _s3) and any("服务" in x for x in _s3)
        and any("契约" in x for x in _s3), f"得 {_s3}")

    # ④ ★ **Go** 符号
    _go = "func 主() {}\ntype 结构 struct{}\nfunc 辅助() {}\n"
    _, _s4 = _extract(_go)
    chk("④Go符号", any("主" in x for x in _s4) and any("辅助" in x for x in _s4),
        f"得 {_s4}")

    # ⑤ ★ **C** 符号与 `#include`
    _c = "#include <stdio.h>\nstruct 点 { int x; };\ntypedef int 整数;\n"
    _u5, _s5 = _extract(_c)
    chk("⑤C符号与include",
        any("点" in x for x in _s5) and any("stdio" in x for x in _u5),
        f"符号 {_s5} / 依赖 {_u5}")

    # ⑥ Rust 依赖仍抽得出（`use crate::a::b;`）
    _u6, _ = _extract("use crate::模块甲::推变换环;\nuse std::collections::HashMap;\n")
    chk("⑥Rust依赖", any("模块甲" in x for x in _u6), f"得 {_u6}")

    # ⑦ ★ Python 依赖：`import a.b` 与 `from c.d import e`
    _u7, _ = _extract("import 包.模块\nfrom 另一包.子包 import 东西\n")
    chk("⑦Python依赖（旧实现为空）",
        any("包" in x for x in _u7) and any("另一包" in x for x in _u7), f"得 {_u7}")

    # ⑧ ★ JS 依赖：`import x from 'a/b'` 与 `require('a/b')`
    _u8, _ = _extract("import x from '组件/按钮'\nconst y = require('工具/格式化')\n")
    chk("⑧JS依赖", any("组件" in x for x in _u8) and any("工具" in x for x in _u8),
        f"得 {_u8}")

    # ⑨ ★ 实现/继承目标（旧实现只认 rust `impl`）
    _u9, _ = _extract("impl 甲 for 乙 {}\nclass 丙 implements 丁 {}\n"
                      "class 戊 extends 己 {}\n")
    chk("⑨impl/implements/extends",
        sum(1 for x in _u9 if x in ("乙", "丁", "己")) >= 2, f"得 {_u9}")

    # ⑩ ★★★★★ 后缀集**由档案派生** + 目录入口惯例表（跨生态，通路只读表）
    _e = _exts()
    chk("⑩后缀由档案派生 + 目录入口表",
        bool(_e) and all(x.startswith(".") for x in _e)
        and set(_模块入口) == {"mod", "__init__", "index"},
        f"后缀 {_e} / 入口 {_模块入口}")

    # ⑪ ★★★★★ R47b：抽取器版本必须**进缓存**、且 `_load` **不得**把它当条目。
    #   【为什么】抽取逻辑改了、文件没改 ⇒ 只看 mtime/size 的缓存**永不失效**
    #   ⇒ 修好了也读不到新结果。⇒ 版本号必须落盘并参与失效判定。
    import tempfile as _tf
    import shutil as _sh
    _d = Path(_tf.mkdtemp(prefix="ri_ver_"))
    _存CACHE = globals()["CACHE"]
    try:
        globals()["CACHE"] = lambda: _d / "idx.json"
        _save({"src/a.rs": {"rel": "src/a.rs", "uses": ["x"], "symbols": ["fn a"]}})
        _raw = json.loads((_d / "idx.json").read_text(encoding="utf-8"))
        _条目 = _读缓存()
        chk("⑪抽取器版本进缓存且不污染条目",
            _raw.get(_VER_KEY) == _EXTRACTOR_VER
            and _VER_KEY not in _条目
            and list(_条目) == ["src/a.rs"],
            f"落盘 {_raw} / 读出 {_条目}")
    finally:
        globals()["CACHE"] = _存CACHE
        _sh.rmtree(_d, ignore_errors=True)

    print(f"repo_index 自检：{N - len(fails)}/{N} "
          + ("全绿" if not fails else "红"))
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_selftest())
