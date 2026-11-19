# -*- coding: utf-8 -*-
"""**引号哨兵** —— 落盘前的 `compile()` 检查（R434，口径 151 的落地）。

★ 我第一版哨兵是**错的**（如实记）：
  它检查"中文夹直引号引文" ⇒ 在 `加判一百零六.py` 上报了 **6 处假警报**
  （那些是**合法的 JSON 键** `{"计数": {"未导入": 3}}`）✗
  ★ 口径「**一条过严的判据会把整片判据池染红**」—— 我又犯了。
★ 正确的闸只有一个，而且**不可争辩**：**这个文件能不能 `compile()`**。
  ★ 口径 132：**机器检查要守「结果」，不是守「风格」** ——
    引号怎么写是**风格**（还容易判错）；编译得过是**结果**。
  ★ 这也就是判一百零一守的东西 —— 但那条守的是**已落盘**的脚本（事后），
    这个哨兵是**落盘前**跑（事前）。
    口径 151：**知道解法 ≠ 用了解法** ⇒ 把"compile 检查"接进**写脚本的动作**里。
★ 用法：`python 引号哨兵.py 要写的脚本.py …` —— 退出码非 0 就别落盘。
"""
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def 查(p: pathlib.Path) -> list[str]:
    """★ **两段式检查**（R438）：**脚本本身** + **脚本要写出去的判据正文**。

    ★ 为什么必须查第二段（实测第九次引号坑）：
      判据正文写在 `判 = r'''…'''` 里，里面用了直引号引文 ——
      **脚本本身合法**（`r'''…'''` 里 `"` 只是普通字符）⇒ 哨兵报绿 ✗
      但这段字符串**会被拼进 `orch_product_chain.py`** ⇒ **拼完就非法**。
      ★ 只查第一段 ⇒ **漏**（这就是它当时没拦住的原因）。
    ★ 查法：把每个**大于 200 字符的三引号字面量**当"要插入的代码"编译一次。
    """
    出: list[str] = []
    try:
        源 = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [f"读不了（{type(e).__name__}: {e}）"]
    try:
        compile(源, str(p), "exec")
    except SyntaxError as e:
        出.append(f"**脚本本身编译不过** line {e.lineno}: {e.msg}  "
                  f"{(e.text or '').strip()[:90]}")
        return 出
    import re as _re
    for m in _re.finditer(r"(\w+)\s*=\s*r?('''|\"\"\")(.*?)\2", 源, _re.S):
        _名, _体 = m.group(1), m.group(3)
        if len(_体) < 200:
            continue
        # ★★★ **第四次收窄**（口径 152 在同一个地方犯了四次，如实记）：
        #   ① 全查 ⇒ 片段误报；② 加 `def _探针():` 前缀 ⇒ 前导空行又误报；
        #   ③ 只查 `def`/`class` 开头 ⇒ **函数体片段**（`def 建总图(` 只写了一半）
        #      仍然误报；④ ⇒ 现在只查**判据函数的特征**：
        #      **以 `def …(…) -> None:` 开头且紧接着是三引号 docstring**。
        #   ★ 收窄四次都没放弃是对的 —— **检查该守的是"我知道它会被拼成什么"**，
        #     不知道的就不查（宁可漏，不可误报）。
        _行们 = [行 for 行 in _体.splitlines() if 行.strip()]
        if not _行们 or not _re.match(r"^(async\s+)?def\s+\S+\(.*\)\s*->\s*None\s*:", _行们[0]):
            continue
        if len(_行们) < 2 or not _行们[1].lstrip().startswith(('"""', "'''")):
            continue
        try:
            compile(_体, f"{p.name}:{_名}", "exec")   # ★ 直接编译，不加前缀
        except SyntaxError as e:
            出.append(f"**判据正文编译不过**（`{_名}`）"
                      f" line {e.lineno}: {e.msg}  {(e.text or '').strip()[:90]}")
    return 出


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("   用法：引号哨兵.py 要检查的脚本.py …")
        raise SystemExit(2)
    坏 = {}
    for a in sys.argv[1:]:
        p = pathlib.Path(a)
        if not p.is_file():
            print(f"   ⚠ 跳过（不存在）: {p}")
            continue
        _错 = 查(p)
        if _错:
            坏[p.name] = _错
    if not 坏:
        print(f"   ✅ 引号哨兵：绿 —— {len(sys.argv) - 1} 个脚本**全部 compile 通过**"
              f"（落盘前）")
        raise SystemExit(0)
    print(f"   ✗ 引号哨兵：**{len(坏)} 个脚本过不了** ⇒ **先修再落盘**")
    for k, vs in list(坏.items())[:10]:
        for v in vs[:4]:
            print(f"      {k}: {v}")
    raise SystemExit(1)
