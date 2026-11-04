# -*- coding: utf-8 -*-
"""R63 定界符配对闸 —— 齿探针。

两道：
 ① **合成齿**：掩码是否保守（字符串/注释里的括号不许算）、入口闸是否
    只拦不终裁（原文本来就不平衡时**不得拒**，否则是假红源）。
 ② **真文件校准**：在真仓库能编译的 .rs 上失衡率必须极低。
    只做合成齿是不够的 —— 闸若在正常文件上就报失衡，它会变成**假红源**，
    而「假红比无齿更坏」。这一条**必须拿真文件量**。

只读：不改任何源码。
"""
import sys
from pathlib import Path

_工具 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_工具 / "src"))

from agentscope_orchestrator.generic_goal import (  # noqa: E402
    _apply_patch, _定界符失衡)

_仓 = _工具.parents[1]
好 = True


def 断言(c, s):
    global 好
    print(("  ✓ " if c else "  ✗ ") + s)
    if not c:
        好 = False


def 平衡(文):
    return not _定界符失衡(文)


print("── ① 掩码必须保守（字符串/注释里的括号不许算）──")
断言(平衡('fn f() { let s = "{"; }'), "平衡的 Rust")
断言(not 平衡("mod t {\n  fn a() {}\n"), "未闭合 mod 必须判失衡")
断言(平衡('fn f() { let s = "((("; }'), "字符串里的括号不算")
断言(平衡("fn f() {} // )))\n/* { */"), "注释里的括号不算")
断言(平衡("x = 1  # )))\n"), "行注释 #")
断言(平衡("#[derive(Clone)]\nfn f() {}\n"), "Rust 属性 `#[…]` 不是注释")
断言(平衡("#![allow(dead_code)]\nfn f() {}\n"), "Rust 内属性 `#![…]` 不是注释")
断言(平衡('fn f() { let s = r#"{ )"#; }'), "Rust 原始串 r#")
断言(平衡("fn f<'a>(x: &'a str) -> &'a str { x }"), "生命周期不当字符字面量")
断言(平衡("fn f() { let c = '}'; }"), "字符字面量")
断言(平衡('x = """))) """\n'), "三引号 Python")
断言(not 平衡("fn f() {} }\n"), "真失衡：多一个闭括号")
断言(not 平衡("fn f() { ] }\n"), "嵌套不配对")

print("── ② 入口闸：只拦，不终裁 ──")
原 = "mod t {\n    fn a() {}\n}\n"
r, e = _apply_patch(原, "<<<REPLACE 2 2>>>\n    fn a() {\n<<<END>>>")
断言(r is None and e and "入口闸" in e[0], "破坏配对必须被入口闸拒收")
r2, e2 = _apply_patch(原, "<<<REPLACE 2 2>>>\n    fn a() { let x = 1; }\n<<<END>>>")
断言(r2 is not None and not e2, "合法局部改动必须放行")
r3, e3 = _apply_patch("mod t {\n", "<<<REPLACE 1 1>>>\nmod t {\n    fn a() {}\n<<<END>>>")
断言(r3 is not None, "原文本就不平衡时**不得拒**（否则是假红源）")
断言(not 平衡("mod t {\n"), "齿空转检查：该夹具原文必须真的是不平衡的")

print("── ③ 真文件校准（决定性：闸不许是假红源）──")
候选 = [p for p in (_仓 / "src").rglob("*.rs")]
候选.sort(key=lambda p: p.stat().st_size, reverse=True)   # 大文件更易暴露问题
样本 = 候选[:150]
平衡数 = 0
坏 = []
for p in 样本:
    try:
        文 = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if 平衡(文):
        平衡数 += 1
    else:
        坏.append(p.relative_to(_仓).as_posix())
总 = max(1, 平衡数 + len(坏))
率 = len(坏) / 总
print(f"  真 .rs 样本 {总} 个（按大小取最大的 150 个）：判失衡 {len(坏)}，失衡率 {率:.1%}")
for x in 坏[:6]:
    print(f"      · {x}")
断言(率 <= 0.05,
     f"真文件失衡率必须极低（实得 {率:.1%}）—— 高 ⇒ 闸是假红源，必须撤或改")

print("=" * 60)
print("R63 定界符齿探针：", "全绿" if 好 else "★ 有红")
sys.exit(0 if 好 else 1)
