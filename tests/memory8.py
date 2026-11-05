r"""八维投影记忆的判据（`memory8.py`）。

锁五件事：
  ① 编码/解码**可逆且确定**：同 8 维必得同 u64；越界维当通配(0xFF)，不抛异常；
  ② **证据等级是检索硬门**：OVERSTATED(2) / 猜测(4) 写进去也**永不返回**；
     这正是"猜的被记成知道的"那道闸；
  ③ 结构键真的收窄：**跨层**的记忆不会被注入（层判定错了，修法一定错）；
  ④ 记忆坏了不许影响主链：库路径指到不可写处，`记/检索` 抛错由调用方吞掉 —— 这里验"吞得掉"。
  ⑤ **dim1/dim6 无损**（2026-11-05 修）：旧实现 `sha1 % 8` / `sha1 % 256` 折叠，
     子系统一多必撞（生日问题）、跨项目会串台；现在走显式名字表，且**表满明确抛错**。
     另锁：同一项目内序号单调递增（旧式 `WHERE 码 >> 56` 把 dim7 当项目、把 dim0 当序号，
     导致序号恒在 1..7 循环）。
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
        # ★ dim5~dim7 的合法上界本来就是 0xFF；dim1（子系统）2026-11-05 起上限改成 254（名字表），
        #   所以"越界当通配"这条要对 0/2/3/4 维验，另给 dim1 单验一次"300 越界"。
        d = m.解码(m.编码([9, 9, 9, 9, 9, 300, 300, 300]))
        断言(d == [255, 9, 255, 255, 255, 255, 255, 255],
             f"越界维一律当通配（dim0/2/3/4 上界 6/6/7/4；dim1 上界 254 故 9 合法）实得={d}")
        d1 = m.解码(m.编码([0, 300, 0, 0, 0, 0, 300, 0]))
        断言(d1[1] == 255 and d1[6] == 255, f"dim1/dim6 超 254 时当通配 实得={d1}")
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

        # ⑤ 无损（2026-11-05 修）：子系统/项目改成显式名字表，不再哈希折叠
        子号们 = []
        for i in range(9):
            条 = m.记(症状=f"AssertionError: sub{i}", 修法="改值", 证据="AssertionError",
                      路径们=[f"mod{i}/x.py"], 等级=0, 判决=5, 仓库="r1")
            子号们.append(m.解码(条["码"])[1])
        断言(len(set(子号们)) == 9,
             f"9 个不同子系统必得 9 个不同号（旧实现只有 8 个桶，必撞）实得={子号们}")
        条a = m.记(症状="AssertionError: a", 修法="改值", 证据="AssertionError",
                   路径们=["mod0/y.py"], 等级=0, 判决=5, 仓库="r2")
        断言(m.解码(条a["码"])[1] == 子号们[0], "同名子系统跨仓库仍是同一号")
        断言(条a["子系统"] == "mod0", f"子系统名可从路径读出（实得 {条a['子系统']}）")

        项号们 = []
        for i in range(5):
            条b = m.记(症状="AssertionError: p", 修法="改值", 证据="AssertionError",
                       路径们=["mod0/z.py"], 等级=0, 判决=5, 仓库=f"proj{i}")
            项号们.append(m.解码(条b["码"])[6])
        断言(len(set(项号们)) == 5, f"5 个不同项目必得 5 个不同号 实得={项号们}")
        条c = m.记(症状="AssertionError: p", 修法="改值", 证据="AssertionError",
                   路径们=["mod0/z.py"], 等级=0, 判决=5, 仓库="proj0")
        断言(m.解码(条c["码"])[6] == 项号们[0], "同项目跨次仍是同一号（不随机折叠）")

        # 表满：明确抛，不折叠
        旧上限 = m.表内上限
        m.表内上限 = 3
        try:
            抛满 = False
            try:
                m.记(症状="AssertionError: full", 修法="改值", 证据="AssertionError",
                     路径们=["mod0/w.py"], 等级=0, 判决=5, 仓库="full-new-project")
            except RuntimeError:
                抛满 = True
            断言(抛满, "项目表满时明确抛错（不静默折叠成同一个号）")
        finally:
            m.表内上限 = 旧上限

        # 序号：同一项目内单调递增（旧式把 dim7 当项目过滤、dim0 当序号，恒在 1..7 循环）
        序们 = [m.解码(m.记(症状=f"AssertionError: seq{i}", 修法="改值", 证据="AssertionError",
                             路径们=["mod0/s.py"], 等级=0, 判决=5, 仓库="seqproj")["码"])[7]
                for i in range(5)]
        断言(len(set(序们)) == 5 and 序们 == sorted(序们),
             f"同一项目内序号单调递增（旧实现恒为同一值）实得={序们}")

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
    print("\nmemory8：绿 —— 编码可逆、等级硬门、跨层不注入、子系统/项目无损、坏库会抛")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())