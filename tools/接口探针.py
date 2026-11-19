# -*- coding: utf-8 -*-
"""**接口探针** —— 调生产函数**之前**先打印它的真签名与真返回形状。

★ 为什么（口径 154 的落地）：
  目标⑤那一轮我**猜了四次形状，四次全错**：
````
第 1 猜 `_一文件`              —— 不存在（真名 `取导入`）
第 2 猜 `_解析一个(源, 相)` 返回树 —— 它返回 **(节, 边, 元)**
第 3 猜 `枚举文件` 返回文件列表   —— 它返回 **2 元组**
第 4 猜 `_解析一个` 返回 tuple 第 0 个是树 —— 第 0 个是**节点列表**
第 5 次才去读生产的 L1202-L1205  ✅
````
★ **每一次错都只要读一行生产代码就能避免** —— 代价是 5 轮往返。
⇒ 口径 154：**"读生产者"必须发生在第一次尝试之前**。

★ 这个工具就是"第一次尝试之前"那一步：
````
python tools/接口探针.py 取导入 _解析一个 枚举文件
    ⇒ 打印 签名 / docstring 首段 / **源码前几行** / **真实返回形状**（有样例时）
````
★ 它**不改任何东西**，只读（口径 132：守结果，不守风格）。
★ 与判据/闸的分工（口径 153）：
````
判据（判一百零一/一百零七）—— 守**库里的东西**（事后）
闸（引号哨兵）            —— 守**我手上的动作**（落盘前）
探针（本工具）            —— 守**我要调的那个接口**（调用前）
````
"""
import inspect
import pathlib
import sys

sys.path.insert(0, r"E:\基础设施\离散时空认知物理基础研究\太虚元道\多智能体编排\agentscope_orchestrator\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agentscope_orchestrator import kg as _kg  # noqa: E402


def 形状(x, 深=0) -> str:
    if 深 >= 2:
        return type(x).__name__
    名 = type(x).__name__
    if isinstance(x, (list, tuple)):
        _前 = x[:2] if x else []
        _内 = "、".join(形状(y, 深 + 1) for y in _前)
        return f"{名}({len(x)})[{_内}]"
    if isinstance(x, dict):
        _k = list(x)[:5]
        return f"dict({len(x)}) keys={_k}"
    return 名


def main(名们: list[str]) -> int:
    if not 名们:
        print("   用法：接口探针.py 函数名 …（或 `*` 列全部公开名）")
        return 2
    if 名们 == ["*"]:
        名们 = [n for n in dir(_kg) if not n.startswith("_")]
        名们 = [n for n in 名们 if callable(getattr(_kg, n, None))]
        print(f"   ★ `kg` 公开可调用名 {len(名们)} 个：")
        for n in 名们:
            try:
                print(f"      {n}{inspect.signature(getattr(_kg, n))}")
            except (TypeError, ValueError):
                print(f"      {n}(?)")
        return 0
    坏 = 0
    for 名 in 名们:
        p = getattr(_kg, 名, None)
        if p is None:
            近 = [n for n in dir(_kg) if 名 in n or n in 名][:6]
            print(f"   ✗ `{名}` **不存在**。相近的：{近}")
            坏 += 1
            continue
        print(f"   ── `{名}`")
        try:
            print(f"      签名: {名}{inspect.signature(p)}")
        except (TypeError, ValueError):
            print("      签名: （拿不到）")
        _文 = inspect.getdoc(p) or ""
        if _文:
            print(f"      文档首段: {_文.splitlines()[0][:110]}")
        try:
            _源 = inspect.getsource(p).splitlines()
            print(f"      源码 {len(_源)} 行；**返回语句**：")
            for i, 行 in enumerate(_源):
                if "return " in 行:
                    print(f"         L{i + 1}: {行.strip()[:104]}")
        except (OSError, TypeError):
            print("      源码: （拿不到）")
    return 1 if 坏 else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
