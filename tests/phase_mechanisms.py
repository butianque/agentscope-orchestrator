"""phase_mechanisms 干测：机制抽取器（mechanisms）从活库抽机制格。

覆盖：真库不崩 + 抽全 + 关键族定位/签名/依赖正确 + lakefile 排除 + charter/matrix。
无需联网（不调模型）。

项目无关：具体族名/签名断言属**项目知识**，从档案 mechanism.assert_families
读取；档案未声明时只做通用断言（格非空、可枚举、可渲染）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentscope_orchestrator import mechanisms  # noqa: E402
from agentscope_orchestrator import profile as prof_mod  # noqa: E402

PASS = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {extra}"
    PASS += 1
    print(f"  ok  {name}")


def main() -> int:
    prof = prof_mod.get_profile()
    lat = mechanisms.build_default(prof)
    names = lat.family_names()
    check("真库不崩且>=1族", lat.count() >= 1, f"count={lat.count()}")
    check("排除 lakefile", "lakefile" not in names, str(names))

    # 项目声明的族断言（档案 mechanism.assert_families；未声明则跳过）
    declared = list((prof.raw.get("mechanism") or {}).get("assert_families") or [])
    for fam in declared:
        check(f"{fam} 入格", fam in names)
    if not declared:
        print("  --  档案未声明 mechanism.assert_families，跳过具体族断言")

    # 通用断言：格可枚举、可渲染、可出矩阵（与具体族名无关）
    if names:
        _f0 = lat.family(names[0])
        check("族结构可枚举", isinstance(_f0, dict) and "decls" in _f0)
    check("charter 可渲染", isinstance(mechanisms.charter(lat), str))
    check("matrix 可渲染", isinstance(mechanisms.mechanism_matrix(lat), dict))

    # 项目声明的签名断言（档案 mechanism.assert_decls）
    for item in ((prof.raw.get("mechanism") or {}).get("assert_decls") or []):
        fam = str(item.get("family") or "")
        decl = str(item.get("decl") or "")
        if fam and fam in names:
            dn = [d.name for d in lat.family(fam)["decls"]]
            check(f"{fam} 声明 {decl}", decl in dn, str(dn))
            want = str(item.get("sig_contains") or "")
            if want:
                sig = next((d.stmt for d in lat.family(fam)["decls"]
                            if d.name == decl), "")
                check(f"{decl} 签名含 {want}", want in sig, sig)

    # 项目声明的定位断言（档案 mechanism.assert_position）
    for item in ((prof.raw.get("mechanism") or {}).get("assert_position") or []):
        fam = str(item.get("family") or "")
        if fam and fam in names:
            check(f"{fam} 定位存在", bool(lat.family(fam)["position"]),
                  str(lat.family(fam)["position"]))

    # 项目声明的依赖断言（档案 mechanism.assert_deps）
    for item in ((prof.raw.get("mechanism") or {}).get("assert_deps") or []):
        fam = str(item.get("family") or "")
        needle = str(item.get("contains") or "")
        if fam and fam in names and needle:
            check(f"{fam} 依赖含 {needle}",
                  any(needle in d for d in lat.family(fam)["deps"]),
                  str(lat.family(fam)["deps"]))

    # 项目声明的"无定位不伪造"断言（档案 mechanism.assert_no_fake_position）
    for fam in ((prof.raw.get("mechanism") or {}).get("assert_no_fake_position") or []):
        if fam in names:
            lr = lat.family(fam)
            check(f"{fam} 无定位时不伪造(空或占位)",
                  not lr["position"] or "(缺定位描述)" in str(lr["position"]))

    charter_text = mechanisms.charter(lat)
    check("charter 覆盖全部族", all(f"[{n}]" in charter_text for n in names),
          f"family 数={lat.count()}")
    mat = mechanisms.mechanism_matrix(lat)
    check("机制矩阵族数=格族数", len(mat) == lat.count())
    check("机制矩阵含判定谓词计数", all("判定谓词" in v for v in mat.values()))

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        live = root / "live"; live.mkdir()
        (live / "FakeMech.lean").write_text(
            "/-\n模块: FakeMech\n定位: 测试机制族\n宪法合规: 0 sorry\n-/\n"
            "import FakeBase\nnamespace FakeMech\n"
            "theorem fake_ok (x : Nat) : x + 1 = x + 1 := by rfl\n"
            "theorem fake_invol (x : Nat) : f x = x := by rfl\n"
            "def helper (x : Nat) : Nat := x\nend FakeMech\n",
            encoding="utf-8")
        arch = root / "_archive"; arch.mkdir()
        (arch / "Old.lean").write_text("theorem old : True := by trivial\n",
                                       encoding="utf-8")
        (root / "_probe.lean").write_text("theorem probe : True := by trivial\n",
                                          encoding="utf-8")
        (root / "lakefile.lean").write_text("import Lake\npackage p\n",
                                            encoding="utf-8")
        fake = mechanisms.build_lattice([root])
        check("假库只含 1 族(live 下)", fake.count() == 1,
              f"names={fake.family_names()}")
        f = fake.family(fake.family_names()[0])
        check("假库族定位正确", f["position"] == "测试机制族", f["position"])
        fnames = [d.name for d in f["decls"]]
        check("假库声明含 fake_ok/fake_invol/helper",
              set(fnames) == {"fake_ok", "fake_invol", "helper"}, str(fnames))

    print(f"\nPASS={PASS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())