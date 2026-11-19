"""★ R247：**不调模型的**端到端接线自检（dry 闭环）。

为什么必须有一个不花模型调用的闭环自检（真读数，2026-11-05）：
  上游整段不可用（直连实测：`HTTP 200 + {"error":"上游服务异常"}`）时，
  所有"端到端"验证全部停摆 —— **判据层的问题因此看不见**。
  ⇒ 必须有一条**不需要模型**的路，把「失败栈→改动面→产生产物→逐文件裁决→落地主树→
    项目门复跑→否掉不许交付的」这整条接线跑一遍，并给出可复核的成功/失败。

做法：造一个临时仓（人写的红判据 + 三个模块缺口），
  用**脚本化的"假模型"**直接返回正确产物，喂给真实的 `_增量应用` 与 `_project_acceptance`，
  最后**独立复跑**仓自己的测试。全程 0 次模型调用。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

坏: list[str] = []

服务_坏 = '''"""业务逻辑（取消未实现）。"""

LIMIT = 3


def 取消(编号):
    raise NotImplementedError("取消尚未实现")
'''

服务_好 = '''"""业务逻辑（取消已实现）。"""

LIMIT = 3


def 取消(编号):
    if 编号 is None:
        raise ValueError("编号必填")
    return {"编号": 编号, "状态": "已取消"}
'''

库存_坏 = '''"""库存（释放未实现）。"""


def 释放(编号):
    raise NotImplementedError("释放尚未实现")
'''

库存_好 = '''"""库存（释放已实现）。"""


def 释放(编号):
    return {"编号": 编号, "已释放": True}
'''

测试 = '''import sys, pathlib, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import shop.service as S
import shop.inventory as I


class 取消流程(unittest.TestCase):
    def test_取消(self):
        self.assertEqual(S.取消(1)["状态"], "已取消")

    def test_释放(self):
        self.assertTrue(I.释放(1)["已释放"])

    def test_编号必填(self):
        with self.assertRaises(ValueError):
            S.取消(None)
'''


def 建仓(根: Path) -> None:
    (根 / "src" / "shop").mkdir(parents=True)
    (根 / "src" / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (根 / "src" / "shop" / "service.py").write_text(服务_坏, encoding="utf-8")
    (根 / "src" / "shop" / "inventory.py").write_text(库存_坏, encoding="utf-8")
    (根 / "tests").mkdir()
    (根 / "tests" / "test_shop.py").write_text(测试, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=根, check=False)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"],
                   cwd=根, check=False)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                    "-q", "--no-verify", "-m", "红基线"], cwd=根, check=False)


def _跑测试(根: Path) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-q"],
                       cwd=根, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    from agentscope_orchestrator import gate_spec
    from agentscope_orchestrator import generic_goal as G

    基 = Path(tempfile.mkdtemp(prefix="r247_"))
    try:
        仓 = 基 / "repo"
        建仓(仓)
        绿, 输出 = _跑测试(仓)
        print(f"    ① 红基线：测试绿={绿}（必须 False）")
        if 绿:
            坏.append("① 夹具基线不红 ⇒ 后面的绿色无法归因")

        # ② 机器自己从失败栈读出改动面
        门 = [{"type": "compile", "name": "全量测试", "cwd": str(仓),
              "cmd": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
              "expect_exit": 0}]
        _res, red = gate_spec.evaluate(门, 仓, 仓)
        面 = G._失败栈改动面236(red, 仓, ["src"])
        print(f"    ② 失败栈读出的改动面 = {面}")
        for 必需 in ("src/shop/service.py", "src/shop/inventory.py"):
            if 必需 not in 面:
                坏.append(f"② 失败栈没指出必改文件 {必需}")
        if any("tests/" in p for p in 面):
            坏.append("② 把判据文件读成了改动面")

        # ③ 白名单：判据文件不许当交付物
        留, 摘 = G._过滤判据文件244([{"path": "src/shop/service.py"},
                                    {"path": "tests/test_shop.py"},
                                    {"path": "conftest.py"}])
        留路 = [d["path"] for d in 留]
        print(f"    ③ 白名单保留={留路} 摘掉={摘}")
        if "tests/test_shop.py" not in 摘 or "conftest.py" not in 摘:
            坏.append("③ 判据/配置文件没被白名单摘掉")

        # ④ 真实 `_增量应用`：把"假模型的产物"喂进去，要真落盘
        产物 = {"src/shop/service.py": 服务_好, "src/shop/inventory.py": 库存_好}
        交付物 = [{"path": p, "focus": 0, "agent_instruction": "", "gates": 门,
                   "depends_on": [], "machine": None} for p in 产物]
        原 = G.REPO_ROOT
        try:
            G.REPO_ROOT = 仓
            _前res, _前red = gate_spec.evaluate(门, 仓, 仓)
            基线 = G._红门违规点(_前red)
            结果, _红后, 留43, 退43 = G._增量应用(产物, 基线, 仓, 交付物)
        finally:
            G.REPO_ROOT = 原
        落库 = [(仓 / p).read_text(encoding="utf-8") == t for p, t in 产物.items()]
        print(f"    ④ 增量裁决：保留 {[p for p, _ in 留43]} / 回退 {退43}")
        print(f"    ④ 产物真落盘 = {落库}")
        if len(落库) != 2 or not all(落库):
            坏.append("④ 模型产物**没有真写进主树**（接线断了）")

        # ⑤ 独立复跑：仓自己的判据必须转绿
        绿2, 输出2 = _跑测试(仓)
        尾巴 = [l for l in 输出2.strip().splitlines() if l.strip()][-1:] or [""]
        print(f"    ⑤ 独立复跑：绿={绿2} | {尾巴[0][:70]}")
        if not 绿2:
            坏.append("⑤ 产物落盘了但仓自己的判据没转绿 ⇒ 落地不等于达标")

        # ⑥ 反向：坏的产物必须被回退（不许把树改坏）
        仓2 = 基 / "repo2"
        建仓(仓2)
        # ★ R249：这里要造**更坏**的产物（把已实现整个删掉），
        #   第一版只删了一句 `raise ValueError` ⇒ 删完之后另两条用例**仍然通过**
        #   ⇒ 门仍绿 ⇒ "该回退却没回退"其实**是我测试造错了**（不是产品缺陷）。
        #   「一段我写不出红的判据，不是判据」同样适用于**反向用例**。
        坏服 = '"""业务逻辑（被改坏）。"""\n\nLIMIT = 3\n' 
        try:
            G.REPO_ROOT = 仓2
            _r0, _d0 = gate_spec.evaluate(门, 仓2, 仓2)
            基2 = G._红门违规点(_d0)
            _r1, _, 留2, 退2 = _增量应用包装(仓2, 门, 坏服, G)
        finally:
            G.REPO_ROOT = 原
        原文 = (仓2 / "src" / "shop" / "service.py").read_text(encoding="utf-8")
        print(f"    ⑥ 反向：坏产物是否被回退 = {原文 == 服务_坏}")
        if 原文 != 服务_坏:
            # ★ 判据要**自证**：先确认"我最开始造的就是红的"。
            #   第一版这里只报"没回退"，而真相是**夹具本身造坏了**（仓2 一开始就不是 服务_坏）。
            print(f"    ⑥ 自证：仓2 初始内容 == 服务_坏 ? {原文[:40]!r} / 期望 {服务_坏[:20]!r}")
            print(f"    ⑥ 自证：仓2 当前测试是否红 ? {_跑测试(仓2)[1].strip().splitlines()[-1:] }")
        if 原文 != 服务_坏:
            坏.append("⑥ 坏产物没有被回退 ⇒ 主树可能被改坏")

        # ⑦ ★ R253（2026-11-05）：**跨模块互相依赖时，"逐个试"会拆散正确解**。
        #   这是多文件目标的**核心**形状：A 单独改 ⇒ 先断在 A 的新签名上；
        #   B 单独改 ⇒ 断在 B；只有 A+B 一起才绿。
        #   ⇒ 判据：`_增量应用` 必须**整批保留**它们（R180/R252 那条路真的走通）。
        仓3 = 基 / "repo3"
        建仓(仓3)
        # 先把两个文件都改成"互相依赖"的正确实现，测的是**裁决**而不是模型
        互赖 = {
            "src/shop/service.py": (
                '"""业务逻辑：取消时要归还库存（跨模块调用）。"""\n\n'
                'from shop import inventory\n\n'
                'LIMIT = 3\n\n\n'
                'def 取消(编号):\n'
                '    if 编号 is None:\n'
                '        raise ValueError("编号必填")\n'
                '    inventory.释放(编号)\n'
                '    return {"编号": 编号, "状态": "已取消"}\n'),
            "src/shop/inventory.py": (
                '"""库存：释放被 service 取消调用。"""\n\n\n'
                'def 释放(编号):\n'
                '    return {"编号": 编号, "已释放": True}\n'),
        }
        try:
            G.REPO_ROOT = 仓3
            _r3, _d3 = gate_spec.evaluate(门, 仓3, 仓3)
            基3 = G._红门违规点(_d3)
            交3 = [{"path": p, "focus": 0, "agent_instruction": "",
                    "gates": 门, "depends_on": [], "machine": None} for p in 互赖]
            _rr3, _dd3, 留3, 退3 = G._增量应用(互赖, 基3, 仓3, 交3)
        finally:
            G.REPO_ROOT = 原
        绿3, 输出3 = _跑测试(仓3)
        尾3 = ([l for l in 输出3.strip().splitlines() if l.strip()] or [""])[-1]
        print(f"    ⑦ 互赖两文件：保留 {len(留3)} / 回退 {len(退3)}；独立复跑 绿={绿3} | {尾3[:50]}")
        if len(留3) != 2:
            坏.append(f"⑦ 互相依赖的两个文件被拆散裁决（保留 {len(留3)}/2）"
                      "⇒ 多文件目标最常见的正确解会被否掉")
        if not 绿3:
            坏.append("⑦ 两个文件都保留了，仓自己的判据却没转绿")
    finally:
        shutil.rmtree(基, ignore_errors=True)

    if 坏:
        print("dry 闭环：红 —— " + "；".join(坏))
        return 1
    print("dry 闭环：绿 —— **失败栈→改动面 / 白名单 / 逐文件落地 / 独立复跑转绿 / "
          "坏产物被回退 / 互赖两文件整批保留**，全程 0 次模型调用。")
    return 0


def _增量应用包装(仓, 门, 坏服, G):
    from agentscope_orchestrator import gate_spec
    _r, _d = gate_spec.evaluate(门, 仓, 仓)
    基 = G._红门违规点(_d)
    交付物 = [{"path": "src/shop/service.py", "focus": 0, "agent_instruction": "",
               "gates": 门, "depends_on": [], "machine": None}]
    return G._增量应用({"src/shop/service.py": 坏服}, 基, 仓, 交付物)


if __name__ == "__main__":
    raise SystemExit(main())
