"""把**随包的知识种子**播种进运行期知识库目录（薄壳；逻辑在 `project_kb.播种`）。

用法：
    python tools/kb_seed.py            # 补缺失 + 报告差异 + 打印真实检索读数
    python tools/kb_seed.py --force    # 以种子为准强制对齐（覆盖人改过的条目）
    python tools/kb_seed.py --干跑      # 只看会做什么，不写

★ 逻辑为什么放在包里而不是这个脚本里：消费者（部署机、MCP、doctor）**没有这个脚本**，
  只有装好的包。知识要随 wheel 走（`src/agentscope_orchestrator/kb_seed/`），
  装法就必须也在包里 —— 所以 `播种()` 在 `project_kb`，本脚本只是给人用的入口。
  部署机上请用：`python -m agentscope_orchestrator.doctor --播种`
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

仓 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(仓 / "src"))

from agentscope_orchestrator import project_kb as K  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="以种子为准覆盖人改过的条目")
    ap.add_argument("--干跑", action="store_true", dest="干跑")
    a = ap.parse_args()

    print(f"种子（随包）= {K.内建种子()}")
    print(f"运行期库   = {K.库根() / 'items'}")
    r = K.播种(力=a.force, 干跑=a.干跑)
    if r.get("说明"):
        print(f"★ {r['说明']}")
        return 1
    if r["是同一处"]:
        print("运行期库就是随包种子（ORCH_KB_DIR 指过去了）⇒ 无需播种")
    else:
        print(f"\n新增 {r['新增']} · 相同 {r['相同']} · 有差异 {r['差异']}"
              + ("（默认不动差异项：人的修改优先）" if r["差异"] and not a.force else ""))
        if a.干跑:
            print("（干跑，未写任何文件）")
            return 0
        print(f"已写入 {r['已写']} 条" + (f" · 重建索引 {r.get('重建索引')}" if r.get("已写") else ""))

    # ★ 读数不能只看条数：**真查一次**。条数好看而检索恒零命中，等于没播种。
    st = K.统计()
    print(f"\n库：{st.get('条数')} 条 · 按类别 {st.get('按类别')} · 可机判门 {st.get('可机判门')}")
    if st.get("冲突"):
        print(f"★ 冲突 {len(st['冲突'])}：{str(st['冲突'])[:200]}")
    块 = K.注入(关键词="判据子进程编码口径不符 UnicodeEncodeError gbk 分类=None", 上限字=700)
    print(f"抽样注入 → {len(块)} 字")
    if not 块:
        print("  ★ 空！知识库查不出东西 —— 等于没播种（这正是要防的静默失效）")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
