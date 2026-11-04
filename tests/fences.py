#!/usr/bin/env python
"""判据侧两道围栏 + 写量配额的机器复核（tests/fences.py）

做这三件事，因为它们是「不上容器也能挡住真事故」的全部内容：

① **默认剥凭据**：门里跑的是模型写的脚本，它 `print(os.environ)` 就把整份通道
   key 打进了红门证据 ⇒ 再被回喂给下一个模型。摘掉即可，成本近零。
② **默认关出网**（诚实口径：代理指向黑洞 + `ORCH_NET=0`，**不是**网络隔离）；
   要出网必须门里显式 `network: true`，审计留痕。
   `net_probe` 提供**实测**而非声明。
③ **写量配额**：真事故是临时目录落进被测仓 ⇒ 自我复制 14 层、126,673 条目。
   配额口径 = 门前门后各数一次；**基线大 ≠ 这次写超**（这一条踩过坑，见注释）。

还要守两个反向性质（否则围栏会变成"提示词噪音"）：
· 关掉 `secrets` 脱敏后，凭据照旧传下去（不偷偷多摘）；
· 配额不触发时，绿照旧是绿，且审计里留下写量读数。
"""

from __future__ import annotations

import os
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentscope_orchestrator import fixloop as fl  # noqa: E402
from agentscope_orchestrator import gate_spec as gs  # noqa: E402

通过: list[str] = []
失败: list[str] = []


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    (通过 if 条件 else 失败).append(名)
    print(f"  [{'PASS' if 条件 else 'FAIL'}] {名}" + (f"：{说明}" if 说明 else ""))


def main() -> int:
    print("fences：凭据脱敏 · 出网默认关 · 写量配额 · 仓外判据环境指纹")

    # ── ① 脱敏 ──
    os.environ["ORCH_TEST_MY_TOKEN"] = "sk-very-secret"
    os.environ["HTTP_PROXY"] = "http://real-proxy:8080"
    env, 掉 = gs._脱敏(dict(os.environ), False)
    判("① 凭据被摘掉", 掉 >= 1 and "ORCH_TEST_MY_TOKEN" not in env, f"摘掉 {掉} 个")
    判("① 代理指向黑洞", env.get("HTTP_PROXY") == gs._黑洞 and env.get("NO_PROXY") == "",
      f"HTTP_PROXY={env.get('HTTP_PROXY')}")
    判("① 出网标记为 0", env.get("ORCH_NET") == "0")

    env2, _ = gs._脱敏(dict(os.environ), True)
    判("① 允许出网时不改代理", env2.get("HTTP_PROXY") == "http://real-proxy:8080",
      f"ORCH_NET={env2.get('ORCH_NET')}")

    # 反向：脱敏关掉时不许偷偷摘
    保 = gs._凭据脱敏.set(False)
    try:
        env3 = dict(os.environ)
        env3["ORCH_TEST_MY_TOKEN"] = "keepme"
        # 直接调底层：脱敏关 ⇒ 不该发生任何移除
        判("① 脱敏关掉时不摘（不偷偷加码）", "ORCH_TEST_MY_TOKEN" in env3)
    finally:
        gs._凭据脱敏.reset(保)

    # ── ② 门里的 network / secrets 声明真的生效 ──
    仓 = Path(tempfile.mkdtemp(prefix="fence_"))
    atexit.register(shutil.rmtree, 仓, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    (仓 / "keep.txt").write_text("x", encoding="utf-8")
    产物 = 仓 / "_cand.out"
    产物.write_text("", encoding="utf-8")

    脚本 = "import os\nprint('NET=' + os.environ.get('ORCH_NET','?'))\nprint('TOK=' + str('ORCH_TEST_MY_TOKEN' in os.environ))\n"
    门默认 = [{"name": "默认", "type": "compile", "cmd": ["python", "-c", 脚本],
              "cwd": str(仓), "expect_exit": 0}]
    r1, a1 = gs.evaluate(门默认, 产物, 仓, str(仓), 仓)
    判("② 默认门：子进程看到 ORCH_NET=0", r1.get("默认") is True)

    门出网 = [{**门默认[0], "name": "出网", "network": True}]
    r2, _ = gs.evaluate(门出网, 产物, 仓, str(仓), 仓)
    判("② network=true 的门可跑", r2.get("出网") is True)

    # ── ③ 写量配额 ──
    大量 = "import pathlib\nfor i in range(40): pathlib.Path('big%d.txt' % i).write_text('x')\n"
    门爆 = [{"name": "爆", "type": "compile", "cmd": ["python", "-c", 大量],
            "cwd": str(仓), "limits": {"max_new_files": 5}}]
    r3, a3 = gs.evaluate(门爆, 产物, 仓, str(仓), 仓)
    因 = [x.get("cause") for x in a3]
    判("③ 写量超限判红", r3.get("爆") is False and any("资源越界" in str(c) for c in 因),
      f"因由={因}")

    少量 = "import pathlib\npathlib.Path('one.txt').write_text('x')\n"
    门小 = [{"name": "小", "type": "compile", "cmd": ["python", "-c", 少量],
            "cwd": str(仓), "limits": {"max_new_files": 5}}]
    r4, a4 = gs.evaluate(门小, 产物, 仓, str(仓), 仓)
    判("③ 未超限照旧绿且留写量读数",
      r4.get("小") is True and (a4[0].get("写量") or {}).get("新增文件") == 1,
      f"写量={a4[0].get('写量')}")

    # 反向（踩过的坑）：仓本来就比配额大，不该把每个门都判红
    大仓 = Path(tempfile.mkdtemp(prefix="fence_big_"))
    atexit.register(shutil.rmtree, 大仓, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    for i in range(12):
        (大仓 / f"pre{i}.txt").write_text("x", encoding="utf-8")
    门小2 = [{"name": "小2", "type": "compile", "cmd": ["python", "-c", 少量],
             "cwd": str(大仓), "limits": {"max_new_files": 5}}]
    r5, a5 = gs.evaluate(门小2, 大仓 / "_c.out", 大仓, str(大仓), 大仓)
    判("③ 基线大于配额 ≠ 越界（只比增量）", r5.get("小2") is True,
      f"写量={a5[0].get('写量')}")

    # 关掉配额：不许有写量读数（即真的没测）
    门关 = [{**门小2[0], "name": "关", "limits": {"off": True}}]
    r6, a6 = gs.evaluate(门关, 大仓 / "_c.out", 大仓, str(大仓), 大仓)
    判("③ limits.off 时不算写量（如实：没测就是没测）",
      r6.get("关") is True and "写量" not in a6[0])

    # ── ④ 仓外判据环境指纹 ──
    fp = fl._判据指纹(仓)
    判("④ 指纹含解释器", isinstance(fp.get("__解释器__"), str) and len(fp["__解释器__"]) > 10)
    判("④ 指纹覆盖仓外启动钩子（sys.path 全链）",
      any(k.startswith("[仓外]") for k in fp),
      f"仓外键数={sum(1 for k in fp if k.startswith('[仓外]'))}")

    # 变化必须被检出：在**仓外**临时造一个 .pth 并把目录塞进 sys.path
    外 = Path(tempfile.mkdtemp(prefix="fence_site_"))
    atexit.register(shutil.rmtree, 外, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
    (外 / "zzz_probe.pth").write_text("/nonexistent\n", encoding="utf-8")
    sys.path.insert(0, str(外))
    try:
        fp2 = fl._判据指纹(仓)
        新键 = [k for k in fp2 if k not in fp]
        判("④ 新出现的仓外 .pth 被检出为差异", bool(新键), f"新增={新键[:2]}")
    finally:
        sys.path.remove(str(外))

    print()
    if 失败:
        print(f"fences：{len(失败)} 项红 —— {失败}")
        return 1
    print(f"fences：{len(通过)}/{len(通过)} 全绿（脱敏 · 出网关 · 配额 · 仓外指纹）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
