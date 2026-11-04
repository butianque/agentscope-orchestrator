"""判据多样性门族回归（差分对比 / 属性测试）—— 2026-11-01 新增门型的守门用例。

★ 为什么要这两族门（此前"门只有一族"的代价，写在交付说明里）：
  老门型只会回答「命令跑通了吗 / 文件里有这个词吗」。它能拦住"报错"，
  **拦不住**"两个实现都跑通但行为分叉"，也拦不住"只把样例喂对"。
  这两族门把"改对了"与"只是不报错了"分开。

判据形状：只认行为，不认字段存在。
  · 差分门：输出一致 ⇒ 绿；输出分叉 ⇒ 红且因由带**首个差异位**；退出码不符 ⇒ 红且因由不同。
  · 属性门：全用例成立 ⇒ 绿；某个用例破属性 ⇒ 红且因由点名用例；重复跑输出漂移 ⇒ 红（不确定性）。
  · 白名单：差分门的 `ref` 也要过顶层命令白名单（新门型不得开出旧沙箱没覆盖的口子）。

跑法：python tests/gate_family.py     退出码 0 = 全绿
"""
from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))

from agentscope_orchestrator import gate_spec as gs  # noqa: E402

PY = sys.executable
失败: list[str] = []
条数 = 0


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    global 条数
    条数 += 1
    if not 条件:
        失败.append(f"{名}：{说明}")


def 跑(门: dict, 临时: Path) -> tuple[dict, dict, list]:
    产物 = 临时 / "产物.txt"
    产物.write_text("x\n", encoding="utf-8")
    return gs.evaluate_lossless([门], 产物, 临时, str(临时))


def main() -> int:
    临时 = Path(tempfile.mkdtemp(prefix="gfam_"))
    atexit.register(shutil.rmtree, 临时, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录

    # ① 差分：两实现输出一致 ⇒ 绿
    门 = {"name": "同", "type": "differential", "cwd": str(临时),
          "cmd": [PY, "-c", "print('sum=3')"],
          "ref": [PY, "-c", "print('sum=3')"]}
    因由, 结果, _ = 跑(门, 临时)
    判("差分-一致判绿", 结果.get("同") is True, f"因由 {因由}")

    # ② 差分：输出分叉 ⇒ 红，因由点名"实现分歧"，且**不是**退出码因由
    门 = {"name": "异", "type": "differential", "cwd": str(临时),
          "cmd": [PY, "-c", "print('sum=3')"],
          "ref": [PY, "-c", "print('sum=4')"]}
    因由, 结果, 审计 = 跑(门, 临时)
    码 = 因由.get("异", "")
    判("差分-分叉判红", 结果.get("异") is False, f"因由 {码}")
    判("差分-因由=实现分歧", 码.startswith("实现分歧|"), f"实得 {码}")
    判("差分-带首个差异位", "@" in 码 and 码.split("@")[-1].isdigit(), f"实得 {码}")
    判("差分-不塌缩成退出码因由", "退出码" not in 码, f"实得 {码}")
    判("差分-审计留两侧证据", bool(审计 and 审计[0].get("out") is not None
                                 and 审计[0].get("ref_out") is not None), f"实得 {审计}")

    # ③ 差分：一侧非零退出 ⇒ 红，因由是**退出码**（与分叉区分开）
    门 = {"name": "错", "type": "differential", "cwd": str(临时),
          "cmd": [PY, "-c", "print('ok')"],
          "ref": [PY, "-c", "raise SystemExit(3)"]}
    因由, 结果, _ = 跑(门, 临时)
    码 = 因由.get("错", "")
    判("差分-参照非零判红", 结果.get("错") is False, f"因由 {码}")
    判("差分-因由=退出码不符", 码.startswith("差分退出码不符|"), f"实得 {码}")

    # ④ 差分：normalize 压掉噪声后一致 ⇒ 绿（压的是噪声，不是语义）
    门 = {"name": "规整", "type": "differential", "cwd": str(临时),
          "normalize": [[r"耗时=\d+ms", "耗时=X"]],
          "cmd": [PY, "-c", "print('耗时=12ms ok')"],
          "ref": [PY, "-c", "print('耗时=99ms ok')"]}
    因由, 结果, _ = 跑(门, 临时)
    判("差分-normalize 生效", 结果.get("规整") is True, f"因由 {因由}")

    # ⑤ 属性：全部用例成立 ⇒ 绿
    门 = {"name": "属性", "type": "property", "cwd": str(临时),
          "cmd": [PY, "-c", "import sys; print('ok', int(sys.argv[1]) * 2)"],
          "cases": [["1"], ["2"], ["21"]], "require": ["ok"], "expect_exit": 0}
    因由, 结果, 审计 = 跑(门, 临时)
    判("属性-全成立判绿", 结果.get("属性") is True, f"因由 {因由}")
    判("属性-跑了全部用例", bool(审计 and 审计[0].get("cases") == 3), f"实得 {审计}")

    # ⑥ 属性：某用例破属性 ⇒ 红，因由点名用例
    门 = {"name": "属性红", "type": "property", "cwd": str(临时),
          "cmd": [PY, "-c", "import sys; print(int(sys.argv[1]) * 2)"],
          "cases": [["1"], ["2"]], "require": ["42"]}
    因由, 结果, _ = 跑(门, 临时)
    码 = 因由.get("属性红", "")
    判("属性-破属性判红", 结果.get("属性红") is False, f"因由 {码}")
    判("属性-因由点名用例", "用例0" in 码, f"实得 {码}")

    # ⑦ 属性：重复跑输出漂移 ⇒ 红（不确定性），因由与破属性区分开
    门 = {"name": "抖", "type": "property", "cwd": str(临时),
          "cmd": [PY, "-c", "import os; print(os.urandom(4).hex())"],
          "cases": [[""]], "repeat": 3, "same_output": True}
    因由, 结果, _ = 跑(门, 临时)
    码 = 因由.get("抖", "")
    判("属性-抖动判红", 结果.get("抖") is False, f"因由 {码}")
    判("属性-因由=输出变了", "输出变了" in 码, f"实得 {码}")

    # ⑧ 属性：确定性命令重复跑 ⇒ 绿（不误伤）
    门 = {"name": "稳", "type": "property", "cwd": str(临时),
          "cmd": [PY, "-c", "print('stable')"],
          "cases": [[""]], "repeat": 3, "same_output": True}
    因由, 结果, _ = 跑(门, 临时)
    判("属性-确定性判绿", 结果.get("稳") is True, f"因由 {因由}")

    # ⑨ 门型已注册
    for t in ("differential", "property"):
        判(f"门型已注册-{t}", t in gs._TYPES, f"_TYPES={sorted(gs._TYPES)}")

    # ⑩ 白名单跟着门型走：差分门的 ref 头必须在白名单内
    from agentscope_orchestrator import gate_derive as gd
    坏门 = [{"name": "偷跑", "type": "differential", "cwd": str(临时),
             "cmd": [PY, "-c", "print(1)"],
             "ref": ["definitely-not-whitelisted-xyz", "-c", "print(1)"]}]
    通过, 违规 = gd.check_cmd_safety(坏门, 临时, 临时)
    判("白名单-ref 头被拦", 通过 is False and any("ref" in str(v) for v in 违规),
      f"通过={通过} 违规={违规}")

    if 失败:
        print(f"gate_family：{len(失败)}/{条数} 红")
        for f in 失败:
            print(f"  [FAIL] {f}")
        return 1
    print(f"gate_family：{条数}/{条数} 全绿（差分 4 档 · 属性 4 档 · 注册 · 白名单）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
