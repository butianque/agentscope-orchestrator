"""G4 判据：把 UnitIndex 扩到真实产物库 —— 方向锚定能从索引取回真单元。

项目无关：库路径/键前缀来自档案（orchestrator.profile.yml）。
无档案时本测试跳过（工具本身不持有项目知识）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator.index import UnitIndex
from agentscope_orchestrator import profile as prof_mod

_prof = prof_mod.load_profile()
MYMETA = _prof.artifact.root
if MYMETA is None:
    print("phase_g4_mymeta: 跳过（档案未声明 artifact.root，无产物库可锚定）")
    raise SystemExit(0)
if not MYMETA.is_dir():
    print(f"phase_g4_mymeta: 跳过（产物库目录不存在：{MYMETA}）")
    raise SystemExit(0)

_PREFIX = _prof.artifact.key_prefix
idx = UnitIndex()
reg, disc = idx.build_artifact(MYMETA,
                               key_prefix=_PREFIX,
                               disabled_marker=_prof.artifact.disabled_marker,
                               glob=_prof.artifact.glob)

# 1) 覆盖不变量：发现真实单元且整体成功（无 duplicate key 冲突即全建库）
assert disc > 0, "产物库必能发现单元"
assert reg > 0, "索引非空"
assert idx.coverage_holds(), "coverage invariant must hold"

# 2) 锚定取回真单元：点式 + 前缀式 都命中（用索引里真实存在的第一个模块）
_sample = None
for key, path in idx._units.items():
    if key.endswith(".lean") and path.is_file():
        _sample = path
        break
assert _sample is not None, "索引里应有真实文件"
_rel = _sample.relative_to(MYMETA).with_suffix("")
_dotted = ".".join(_rel.parts)
for key in (_dotted, f"{_PREFIX}.{_dotted}"):
    p = idx.query(key)
    assert p and p.is_file(), f"{key} 应解析到真实文件"
    assert p.name == _sample.name, key

# 3) 单元级锚定：theorem/lemma 名解析到同一文件（用真实存在的一条声明）
_thm = None
for key in list(idx._units):
    if key.startswith(_dotted + ".") and not key.endswith(".lean"):
        _thm = key
        break
if _thm:
    t = idx.query(_thm)
    assert t and t.is_file(), "theorem 级锚定失败"
    t2 = idx.query(_thm[len(_PREFIX) + 1:] if _PREFIX and
                   _thm.startswith(_PREFIX + ".") else _thm)
    assert t2 and t2.is_file(), "theorem 级锚定(无前缀)失败"

# 4) 全路径键可用
full = idx.query(str(_sample))
assert full and full.is_file()

# 5) 停用目录不入索引；未知键诚实返回 None
assert not any(_prof.artifact.disabled_marker in k for k in idx._units), \
    "停用目录不应入索引"
assert idx.query(f"{_PREFIX}.__no_such_unit__") is None

# 6) 各键指向同一物理文件（路径一致）
_same = {str(idx.query(_dotted)), str(idx.query(f"{_PREFIX}.{_dotted}")),
         str(full)}
assert len(_same) == 1, f"各键应指向同一物理文件，got {_same}"

print(f"[g4] PASS: discovered={disc} registered={reg} "
      f"coverage_holds={idx.coverage_holds()}")
sys.exit(0)