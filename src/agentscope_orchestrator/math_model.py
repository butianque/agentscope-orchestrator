"""机制基线源（项目无关的取用口）。

纪律：本模块**不持有任何机制文字**。机制基线属项目知识，由项目档案的
`prompts.charter` 提供；本模块只负责"按档案取基线 + 可选叠加机制族清单"。

  · 有档案 charter ⇒ 返回它（叠加机制族清单，若档案声明了 mechanism.roots）
  · 无档案 charter ⇒ 返回空串（**不编造机制**；调用方应知基线为空）

机制族清单来自档案声明的活库（mechanism.roots），由 mechanisms 模块抽取；
库缺/坏时安静降级为空，不阻断基线。
"""
from __future__ import annotations

from agentscope_orchestrator import profile as prof_mod

_lattice_cache = None
_lattice_key = None


def _p() -> prof_mod.Profile:
    return prof_mod.get_profile()


def _lattice():
    """机制族格（按档案 mechanism.roots；无声明 ⇒ 空格）。"""
    global _lattice_cache, _lattice_key
    prof = _p()
    key = (prof.name, prof.mechanism.roots)
    if _lattice_cache is not None and _lattice_key == key:
        return _lattice_cache
    from agentscope_orchestrator import mechanisms
    try:
        _lattice_cache = mechanisms.build_default(prof)
    except Exception:  # noqa: BLE001 活库缺/坏不阻断基线，退化为空格
        _lattice_cache = mechanisms.MechanismLattice()
    _lattice_key = key
    return _lattice_cache


def reset_cache() -> None:
    """换档案后清缓存（机制格随档案变）。"""
    global _lattice_cache, _lattice_key
    _lattice_cache = None
    _lattice_key = None


def family_roster() -> str:
    """机制族全集（名+定位，紧凑行式；不塞全量签名，控 prompt 体积）。"""
    lat = _lattice()
    if lat.count() == 0:
        return ""
    lines = ["活库已证机制族："]
    for name in lat.family_names():
        f = lat.family(name)
        loc = f["position"] or "(缺定位描述)"
        lines.append(f"  · [{name}] {loc}")
    return "\n".join(lines)


def orch_relevant_block() -> str:
    """编排相关机制族优先子集（名+定位）；无声明 ⇒ 空串。"""
    from agentscope_orchestrator import mechanisms
    prof = _p()
    if not prof.mechanism.orch_relevant:
        return ""
    lat = _lattice()
    names = mechanisms.orch_relevant(lat, prof)
    if not names:
        return ""
    lines = ["编排相关机制族（优先）："]
    for name in names:
        f = lat.family(name)
        loc = f["position"] if f else "(缺定位描述)"
        lines.append(f"  · [{name}] {loc}")
    return "\n".join(lines)


def get_charter() -> str:
    """完整机制基线 = 档案 charter + 编排相关优先子集 + 机制族全集。

    档案未声明 charter ⇒ 返回空串（**不编造机制基线**）。
    """
    base = _p().prompt("charter")
    if not base:
        return ""
    parts = [base]
    for block in (orch_relevant_block(), family_roster()):
        if block:
            parts.append(block)
    return "\n\n".join(parts)


def mechanism_families() -> dict:
    """机制族全集记账：总族数 + 族->{定位, 判定谓词数}。无声明 ⇒ 空集。"""
    lat = _lattice()
    fams = []
    for n in lat.family_names():
        f = lat.family(n)
        fams.append({"族": n, "定位": f.get("position") or "",
                     "判定谓词": sum(1 for d in f.get("decls", [])
                                    if d.kind != "def")})
    return {"total": lat.count(), "families": fams}
