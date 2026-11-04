r"""八维投影记忆的判据（`memory8.py`）。

锁四件事：
  ① 编码/解码**可逆且确定**：同 8 维必得同 u64；越界维当通配(0xFF)，不抛异常；
  ② **证据等级是检索硬门**：OVERSTATED(2) / 猜测(4) 写进去也**永不返回**；
     这正是"猜的被记成知道的"那道闸；
  ③ 结构键真的收窄：**跨层**的记忆不会被注入（层判定错了，修法一定错）；
  ④ 记忆坏了不许影响主链：库路径指到不可写处，`记/检索` 抛错由调用方吞掉 —— 这里验"吞得掉"。
"""
from __future__ import annotations

import os
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))
from agentscope_orchestrator import memory8 as m  # noqa: E402

失败: list[str] = []


def 断言(条件, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}", flush=True)
    else:
        失败.append(说明)
        print(f"  FAIL {说明}", flush=True)


def main() -> int:
    # 独立状态库：绝不碰生产记忆
    临时 = Path(tempfile.mkdtemp(prefix="mem8-test-", dir=str(Path(__file__).parent)))
    atexit.register(shutil.rmtree, 临时, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    旧 = os.environ.get("ORCH_STATE_DIR")
    os.environ["ORCH_STATE_DIR"] = str(临时)
    try:
        # ① 可逆 + 通配
        码 = m.编码([2, 3, 5, 0, 0, 0, 7, 1])
        断言(m.解码(码) == [2, 3, 5, 0, 0, 0, 7, 1], "编码/解码可逆")
        断言(m.编码([2, 3, 5, 0, 0, 0, 7, 1]) == 码, "同一组维度必得同一码（确定）")
        # ★ dim5~dim7 的合法上界本来就是 0xFF，所以"越界当通配"这条只能对前五维验；
        #   首版测试写成 [9]*8 是**测试自己错**（9 在后三维是合法值），当场被这条断言抓住。
        d = m.解码(m.编码([9, 9, 9, 9, 9, 300, 300, 300]))
        断言(d[:5] == [255] * 5 and d[5:] == [255, 255, 255],
             f"越界维一律当通配（前五维上界 6/7/6/7/4；后三维上界 255）实得={d}")
        断言("产物代码" in m.说明(码) and "SOLID" in m.说明(码), "说明可读（含层与等级）")

        # ② 证据等级硬门
        m.记(症状="AssertionError: 1000 != '1.0 kHz'", 修法="khz 返回 x/1000", 证据="AssertionError",
             路径们=["pkg/khz.py"], 等级=0, 判决=5, 仓库="r1")
        m.记(症状="AssertionError: 1000 != '1.0 kHz'", 修法="猜：把测试改了", 证据="AssertionError",
             路径们=["pkg/khz.py"], 等级=2, 判决=5, 仓库="r1")
        m.记(症状="AssertionError: 1000 != '1.0 kHz'", 修法="纯猜", 证据="AssertionError",
             路径们=["pkg/khz.py"], 等级=4, 判决=5, 仓库="r1")
        检 = m.检索(症状="AssertionError: 1000 != '1.0 kHz'", 证据="AssertionError",
                    仓库="r1", 上限=10, 最大等级=1)
        断言(检 and all(x["等级"] <= 1 for x in 检), "只返回 SOLID/PLAUSIBLE")
        断言(not any("把测试改了" in (x["修法"] or "") for x in 检), "被推翻的修法永不返回")
        断言(not any("纯猜" in (x["修法"] or "") for x in 检), "猜测永不返回")

        # ③ 结构键收窄：换一层就检索不到
        检2 = m.检索(症状="AssertionError", 证据="ModuleNotFoundError: No module named 'x'",
                     仓库="r1", 上限=10)
        断言(not 检2, f"跨层不注入（实得 {len(检2)} 条）")

        # ④ 库不可写：读写都抛，但**能被吞**
        挡 = 根 / "_mem8挡板.txt"
        挡.write_text("x", encoding="utf-8")
        os.environ["ORCH_STATE_DIR"] = str(挡 / "下层" / "不可写")
        抛了 = False
        try:
            m.记(症状="x", 修法="y", 等级=1, 判决=5, 仓库="r2")
        except Exception:                     # noqa: BLE001 这正是要验的"抛得出来"
            抛了 = True
        断言(抛了, "库不可写时写入会抛（由调用方吞，不静默假成功）")
        挡.unlink(missing_ok=True)
    finally:
        if 旧 is None:
            os.environ.pop("ORCH_STATE_DIR", None)
        else:
            os.environ["ORCH_STATE_DIR"] = 旧
        # ★ 清理必须**可验**：首版 `rmtree(ignore_errors=True)` 把 Windows 上的占用失败吞掉，
        #   结果 `tests/mem8-test-*/memory8.sqlite3` 被 `git add -A` 收进提交（真发生过）。
        #   根因在 memory8 侧：`with sqlite3.connect() as con` 只提交事务、**不关连接**。
        import gc
        import time as _t
        for _ in range(5):
            shutil.rmtree(临时, ignore_errors=True)
            if not 临时.exists():
                break
            gc.collect()
            _t.sleep(0.1)
        断言(not 临时.exists(), f"用例自己不留临时目录（残留：{[p.name for p in 临时.rglob('*')][:3]}）")

    if 失败:
        print(f"\nmemory8：红 {len(失败)} 项 —— " + "；".join(失败))
        return 1
    print("\nmemory8：绿 —— 编码可逆、等级硬门、跨层不注入、坏库会抛")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
