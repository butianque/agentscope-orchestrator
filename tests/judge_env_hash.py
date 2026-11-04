"""判据环境指纹回归（2026-11-01：把"黑名单"升级成"行为层检测"）。

★ 守的是什么：`orch_fix` 原来的防线是**黑名单**（`sitecustomize.py` / `*.pth` 不许写）。
  黑名单的区分力上限 = 设计者的想象力 —— 模型改一个**不在名单上**的东西照样能把门骗绿
  （公共夹具里插 monkeypatch、放个被自动收集的模块、别的语言的"启动即加载"机制…）。
  本判据换成**门前门后比对判据环境文件的 sha256**：不需要预先想到改法，
  "改过"这个事实本身就是可机验的差分。

★ 只判机制本身（不调模型、不真跑 orch_fix）：
  ① 内容被改 ⇒ 能认出来，并归到"改"桶；
  ② 新增一个禁改文件（名单**外**名字的 `.pth`）⇒ 认得出，并判为违规；
  ③ 删除 ⇒ 认得出；
  ④ 只改**非判据**文件（正常业务代码）⇒ 不算违规（不许把正常修复误判成作弊）；
  ⑤ 违规集合只挑禁改类（`_标记文件` 变了要可见但**不算**违规 —— 它进"门环境改动"警示）；
  ⑥ 预置：仓里本来就躺着 `sitecustomize.py` ⇒ `修复()` 要把它点出来（否则这一次的门读数天然不可信）。

跑法：python tests/judge_env_hash.py      退出码 0 = 全绿
"""
from __future__ import annotations

import asyncio
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))

from agentscope_orchestrator import fixloop as fl  # noqa: E402

失败: list[str] = []
条数 = 0


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    global 条数
    条数 += 1
    if not 条件:
        失败.append(f"{名}：{说明}")


def _违规(异: dict) -> dict:
    """与 `修复()` 里同一套判定：只把禁改类（名单文件 / 禁改后缀）算违规。"""
    out = {k: [x for x in v if Path(x).name in fl._禁改文件
               or Path(x).suffix.lower() in fl._禁改后缀]
           for k, v in (异 or {}).items()}
    return {k: v for k, v in out.items() if v}


def main() -> int:
    顶 = Path(tempfile.mkdtemp(prefix="jenv_"))
    atexit.register(shutil.rmtree, 顶, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    try:
        (顶 / "conftest.py").write_text("# 判据环境：marker 类\n", encoding="utf-8")
        (顶 / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        (顶 / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (顶 / "tests").mkdir()
        (顶 / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n",
                                               encoding="utf-8")

        一 = fl._判据指纹(顶)
        判("① 认出 conftest/pyproject", {"conftest.py", "pyproject.toml"} <= set(一),
          f"指纹={sorted(一)}")
        判("① 不把业务文件当判据", "app.py" not in 一 and "tests/test_a.py" not in 一,
          f"指纹={sorted(一)}")

        # ② 改内容（黑名单拦不住：conftest.py 只是"标记"，不禁止）
        (顶 / "conftest.py").write_text("# 判据环境：marker 类\nimport os\n"
                                       "os.environ['X']='1'\n", encoding="utf-8")
        二 = fl._判据指纹(顶)
        异 = fl._判据差异(一, 二)
        判("② 改内容被认出", 异.get("改") == ["conftest.py"], f"差异={异}")
        判("② marker 变更不算违规（要可见但不作废门）", _违规(异) == {}, f"违规={_违规(异)}")

        # ③ 新增一个**名单外名字**的 .pth（黑名单按名字拦不住，后缀能拦）
        (顶 / "zz_startup.pth").write_text("import sys\n", encoding="utf-8")
        三 = fl._判据指纹(顶)
        异3 = fl._判据差异(二, 三)
        判("③ 新增 .pth 认得出", 异3.get("新增") == ["zz_startup.pth"], f"差异={异3}")
        判("③ 新增 .pth 判违规", _违规(异3).get("新增") == ["zz_startup.pth"],
          f"违规={_违规(异3)}")

        # ④ 删掉 sitecustomize（本来不该存在的存在过 ⇒ 删除也是改动）
        (顶 / "sitecustomize.py").write_text("import sys\n", encoding="utf-8")
        四 = fl._判据指纹(顶)
        (顶 / "sitecustomize.py").unlink()
        异4 = fl._判据差异(四, fl._判据指纹(顶))
        判("④ 删除判据文件认得出", 异4.get("删除") == ["sitecustomize.py"], f"差异={异4}")
        判("④ 删除判违规", _违规(异4).get("删除") == ["sitecustomize.py"],
          f"违规={_违规(异4)}")

        # ⑤ 正常改业务代码 ⇒ 不算违规（不许把真修复误判成作弊）
        (顶 / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        (顶 / "tests" / "test_a.py").write_text("def test_a():\n    assert 2 == 2\n",
                                                encoding="utf-8")
        异5 = fl._判据差异(fl._判据指纹(顶), fl._判据指纹(顶))
        判("⑤ 无改动 ⇒ 空差异", 异5 == {}, f"差异={异5}")

        # ⑥ 预置判据文件：仓里已经躺着 sitecustomize.py ⇒ 修复() 必须点名（首个门读数不可信）
        (顶 / "zz_startup.pth").unlink(missing_ok=True)   # ③ 造的，清掉以隔离本档
        (顶 / "sitecustomize.py").write_text("import sys\n", encoding="utf-8")
        预置 = fl.预置点名(fl._判据指纹(顶))
        判("⑥ 预置判据文件被点名", 预置 == ["sitecustomize.py"], f"预置={预置}")
        # 真调一次 `修复()`：门故意绿（`python -c exit 0`），验证返回值里带上这条警示
        r = asyncio.run(fl.修复(顶, [sys.executable, "-c", "pass"], 轮数=0))
        判("⑥ 修复() 返回值带 预置判据文件",
          r.get("预置判据文件") == ["sitecustomize.py"], f"返回={list(r)}")
        判("⑥ 修复() 说明指纹边界", "仓外" in str(r.get("判据指纹边界") or ""),
          f"边界={r.get('判据指纹边界')}")
    finally:
        shutil.rmtree(顶, ignore_errors=True)

    if 失败:
        print(f"judge_env_hash：{len(失败)}/{条数} 红")
        for f in 失败:
            print(f"  [FAIL] {f}")
        return 1
    print(f"judge_env_hash：{条数}/{条数} 全绿（改/新增/删除/正常改动不误判/预置点名）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


