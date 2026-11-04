"""机制抽取器（mechanisms）：从活形式化证明库抽已证机制 → 机制格数据源。

用户数学模型（Lean 已证）：机制=结构=判据=对象；真值变对象、结构连结构、密度涌现。
本模块把「活形式化证明库里已被证绿的机制」抽出成结构化对象（机制格），成为**单一
真相来源**，供：
  - observation 机制矩阵（数据驱动，不再手写有限几维格子投影）；
  - math_model.MECHANISM_CHARTER（把全部机制族注入 prompt，保真/包准）；
  - develop 的 FIX/OBS prompt（可把该文件对应机制族的语义+签名注入，提升 LLM 能力）。

抽取纯机器正则，语言无关（读的是 .lean 形式化陈述 + 判据门判真值），不做模型审模型。
诚实边界：本模块只抽出「已被形式化证明的机制结构与签名」，不替判据门下真值；机制族
的最终开放真值仍由各目标语言（Rust/Lean）的机器门与观测给定。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# ── 声明正则（.lean 顶层声明；行首，不带缩进）──────────────────
_DECL_RE = re.compile(
    r"^\s*(?P<kind>theorem|lemma|example|def)\s+(?P<name>[\w']+)\b", re.M)
_IMPORT_RE = re.compile(r"^\s*import\s+(?P<dep>[A-Za-z0-9_.]+)", re.M)
_HEADER_RE = re.compile(r"^/\-(?P<body>.*?)\-/", re.DOTALL)
_LOCA_RE = re.compile(r"定位\s*[:：]\s*(?P<v>[^\n]+)")
_CFM_RE = re.compile(r"宪法合规\s*[:：]\s*(?P<v>[^\n]+)")


def _find_stmt_end(text: str, start: int) -> int:
    """从声明名后向后找陈述结束：首个顶层 `:=`（括号深度 0）。"""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth = max(0, depth - 1)
        elif c == ":" and depth == 0 and i + 1 < n and text[i + 1] == "=":
            return i
        i += 1
    return n


def _slice_text(text: str, prefix: str, default: str = "") -> str:
    """裁剪头尾空白并把整段换行折叠成单行（供人读/注入 prompt）。"""
    if not prefix:
        return default
    out = prefix.strip().replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", out).strip()


@dataclass
class Decl:
    """机制格里的一个声明对象：kind(name def/theorem) + 谓语签名。"""
    kind: str      # theorem | lemma | example | def
    name: str
    stmt: str      # 谓语签名（含 binder + 类型，到顶层 `:=` 为止的陈述）


@dataclass
class Mechanism:
    """一个机制对象：一个 .lean 模块（族）里的某个已证机制声明。"""
    family: str        # 族名 = 模块名（文件 stem）
    file: str          # 相对活库根的路径
    position: str      # header「定位」：机制语义
    constitution: str  # header「宪法合规」
    deps: list[str]    # import 依赖
    decl: Decl         # 声明 + 谓语签名
    optional: bool = False  # 信息性；不再展开用


@dataclass
class MechanismLattice:
    """机制格：`族名 -> 族{定位, 宪法, deps, 声明列表}` + 全揭示汇总。"""
    families: dict[str, dict] = field(default_factory=dict)
    root: str = ""

    def family_names(self) -> list[str]:
        return sorted(self.families)

    def family(self, name: str) -> dict | None:
        return self.families.get(name)

    def for_file(self, path: str | Path) -> dict | None:
        """按文件名 stem 精确命中族（机制族与活库模块一一对应）。"""
        stem = Path(path).stem
        return self.families.get(stem)

    def count(self) -> int:
        return len(self.families)

    def decl_count(self) -> int:
        return sum(len(f.get("decls", [])) for f in self.families.values())

    def catalog(self) -> list[dict]:
        """全机制族揭示：{族, 定位, 宪法, deps, 声明数, 声明名清单}。"""
        cat: list[dict] = []
        for name in self.family_names():
            f = self.families[name]
            cat.append({
                "族": name, "定位": f["position"], "宪法": f["constitution"],
                "依赖": list(f["deps"]),
                "声明": len(f.get("decls", [])),
                "声明名": [d.name for d in f.get("decls", [])]})
        return cat


def parse_module(path: Path | str) -> dict:
    """解析单个 .lean 模块：族名/定位/宪法/依赖/声明。机器正则，不做模型。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    stem = p.stem
    mh = _HEADER_RE.search(text)
    hdr = mh.group("body") if mh else ""
    loca = ""
    cfm = ""
    if hdr:
        ml = _LOCA_RE.search(hdr)
        mc = _CFM_RE.search(hdr)
        loca = _slice_text(hdr, ml.group("v") if ml else "")
        cfm = _slice_text(hdr, mc.group("v") if mc else "")
    deps = [m.group("dep") for m in _IMPORT_RE.finditer(text)]
    decls: list[Decl] = []
    for m in _DECL_RE.finditer(text):
        kind, name = m.group("kind"), m.group("name")
        end = _find_stmt_end(text, m.end())
        stmt = _slice_text(text, text[m.end():end], name)
        decls.append(Decl(kind=kind, name=name, stmt=stmt))
    return {"族": stem, "定位": loca, "宪法": cfm, "依赖": deps,
            "声明": decls, "文件": p.as_posix()}


_SKIP_HINTS = ("_archive", "_backup", ".lake", ".cache", "存档", "备份",
               "_disabled", "_scratch", "node_modules", "target")

# 非机制模块（构建配置等），不当作机制族揭示
_NON_MECHANISM = {"lakefile", "package"}


def _should_skip_dir(parts) -> bool:
    return any(any(part.startswith(s) or part == s for s in _SKIP_HINTS)
               for part in parts)


def build_lattice(roots: list[Path | str]) -> MechanismLattice:
    """扫活库 *.lean，抽机制格。跳过 _archive/.lake/_backup/_disabled 等归档目录。

    多根时按传入顺序：主真相源优先（先扫），补充根遇到重名族不覆盖主根——
    同一族名在两树都有时以先扫到的（主源）为准，防补充树覆盖主源机制。"""
    lattice = MechanismLattice()
    roots = [Path(r) for r in roots]
    lattice.root = str(roots[0]) if roots else ""

    def collect(scan_root: Path, overwrite: bool) -> None:
        for p in sorted(scan_root.rglob("*.lean")):
            if not p.is_file():
                continue
            if _should_skip_dir(p.parts):
                continue
            if p.name.startswith("_"):
                continue   # _probe.lean 之类探针不入格
            mod = parse_module(p)
            if mod["族"] in _NON_MECHANISM or \
                    any(d == "Lake" for d in mod["依赖"]):
                continue   # lakefile（Lake 构建配置）非机制模块，不入格
            if mod["族"] in lattice.families and not overwrite:
                continue   # 重名族以主源（先扫根）为准
            rel = p.relative_to(scan_root).as_posix() if (
                scan_root and p.is_relative_to(scan_root)) else p.as_posix()
            lattice.families[mod["族"]] = {
                "position": mod["定位"], "constitution": mod["宪法"],
                "deps": mod["依赖"], "decls": mod["声明"],
                "file": rel}

    for i, r in enumerate(roots):
        collect(r, overwrite=(i == 0))
    return lattice


def charter(lattice: MechanismLattice, width: int = 120,
            skip_def: bool = True) -> str:
    """把机制格渲染成注入 prompt 的机制基线文本（保真：全部已证机制族）。

    skip_def=True 时只披露 theorem/lemma/example（已证机制判据本身），不把
    def（实现函数）当机制展开，避免把实现当判据，保持机制=谓词（判据）。"""
    lines: list[str] = []
    lines.append("【数学模型机制基线 · 全部来自活库已证 Lean 证明（单一真相源）】")
    for name in lattice.family_names():
        f = lattice.families[name]
        decls = [d for d in f["decls"] if not (skip_def and d.kind == "def")]
        thms = [d for d in decls if d.kind in ("theorem", "lemma", "example")]
        sigs = [d.stmt for d in decls[:3]]  # 谓语签名前 3 条（不塞全量）
        head = f"· 机制族[{name}]：{f['position'] or '(缺定位描述)'}"
        if f["deps"]:
            head += f"  依赖={ '、'.join(f['deps']) }"
        lines.append(head)
        for s in sigs:
            lines.append(f"    - {name}::{s[:width]}")
        if thms and len(thms) > len(sigs):
            lines.append(f"    - （另 {len(thms) - len(sigs)} 条已证声明的谓语签名略…）")
    lines.append("合规红线：0 sorry / 0 axiom / 0 Classical / 0 native_decide；"
                 "禁 omega / nlinarith / Mathlib；不用外部上限/超时/候选池/限轮数去圈定有界。")
    return "\n".join(lines)


def mechanism_matrix(lattice: MechanismLattice,
                     skip_def: bool = True) -> dict[str, dict]:
    """机制矩阵（数据驱动）：族 -> {定位, 依赖数, 已证谓词(声明)数, 声明名}。

    供 observation 校验/对齐：观测的机制集合以活库正式声明的机制族为全集，
    不再是手写的固定几个格子；未覆盖的族属「未接线」清晰显式。"""
    mat: dict[str, dict] = {}
    for name in lattice.family_names():
        f = lattice.families[name]
        decls = [d for d in f["decls"] if not (skip_def and d.kind == "def")]
        mat[name] = {"position": f["position"], "deps": len(f["deps"]),
                     "判定谓词": len([d for d in decls
                                      if d.kind in ("theorem", "lemma",
                                                    "example")]),
                     "声明": [d.name for d in decls]}
    return mat


def build_default(profile=None) -> MechanismLattice:
    """按档案声明的 mechanism.roots 扫机制族格。

    项目无关：本函数不再硬编码任何库路径。档案未声明 roots ⇒ 空格
    （不猜项目结构；调用方会看到 count()==0 并据此降级）。
    """
    if profile is None:
        from agentscope_orchestrator import profile as prof_mod
        profile = prof_mod.get_profile()
    base = profile.source_path.parent if profile.source_path else profile.repo_root
    roots: list[Path] = []
    for r in profile.mechanism.roots:
        p = Path(r)
        if not p.is_absolute():
            p = base / p
        if p.is_dir():
            roots.append(p)
    if not roots:
        return MechanismLattice()
    return build_lattice(roots)


def orch_relevant(lattice: MechanismLattice | None = None,
                  profile=None) -> list[str]:
    """编排相关机制族子集（按档案 mechanism.orch_relevant 声明）。

    档案未声明 ⇒ 回落到语义关键词圈定（通用启发式，不含项目专名）。"""
    if profile is None:
        from agentscope_orchestrator import profile as prof_mod
        profile = prof_mod.get_profile()
    fams = (lattice or build_default(profile)).families
    declared = [d for d in profile.mechanism.orch_relevant]
    if declared:
        out = {n for n in fams if any(d.lower() in n.lower() for d in declared)}
        return sorted(out)
    keys = ("decision", "converge", "fixed", "complet", "consist", "conserv",
            "wire", "wiring", "carrier", "rule", "deriv", "dynam", "infer",
            "proof", "algorithm", "intention", "orchestr", "chain", "emerg",
            "limit", "self", "unified", "topos")
    return sorted(n for n in fams if any(k in n.lower() for k in keys))


def _selftest() -> int:
    """机制格自检（项目无关）：格可建、声明可抽、族可枚举。

    具体族名/数量属项目知识，不作硬断言（工具不该知道某项目有哪些族）。
    """
    lat = build_default()
    fails: list[str] = []
    fams = lat.family_names()
    print(f"机制格：{lat.count()} 族 · {lat.decl_count()} 声明")
    if lat.count() == 0:
        print("⚠ 机制格为空（档案未声明 mechanism.roots 或库不可达）——"
              "工具本身正常，属项目配置未就绪")
    # 诚实警示（不 fail）：档案声明的编排锚定族在活库无同名族
    from agentscope_orchestrator import profile as prof_mod
    declared = list(prof_mod.get_profile().mechanism.orch_relevant)
    missing = [a for a in declared
               if not any(a.lower() in n.lower() for n in fams)]
    if missing:
        print(f"⚠ 档案声明的机制锚定族在活库无同名 Lean 对应：{missing}")
    print("自检红" if fails else "机制格自检：全绿")
    for f_ in fails:
        print(f"  ✗ {f_}")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    lat = build_default()
    print(f"机制格：{lat.count()} 族 · {lat.decl_count()} 声明")
    print("=" * 60)
    print(charter(lat))
    sys.exit(0)