r"""semsearch 桥的判据（`semsearch_link.py`）。

锁的是"把项目已有的知识接进编排器"这条链上会被说成"接了"其实是空接的四处：
  ① 二进制/索引**真能跑**（不是路径猜对了就算）；
  ② 关键词检索**真返回条目**（首版加了 `--trace` 与 `--keyword` 一起用 ⇒ 二进制直接报
     「--trace 需要配合 --pr 使用」⇒ 恒 0 条，却"看起来接口通"）；
  ③ `--pr` 分支输出是**单个 JSON 对象**（不是数组），解析必须容得下；
  ④ 目录正则检索在**任意目录**可用（这是"跨项目全局记忆"唯一不依赖改 Rust 的能力）；
  ⑤ 注入块守预算、无命中返回空串（绝不编造命中）。

★ 二进制不在 ⇒ 本判据判"跳过"（rc=0 并写明），不判绿：没装的东西不能算验过。
"""
from __future__ import annotations

import os
import atexit
import shutil
import sys
from pathlib import Path

根 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(根 / "src"))
from agentscope_orchestrator import semsearch_link as S  # noqa: E402

失败: list[str] = []


def 断言(条件, 说明: str) -> None:
    if 条件:
        print(f"  ok   {说明}", flush=True)
    else:
        失败.append(说明)
        print(f"  FAIL {说明}", flush=True)


def main() -> int:
    # ★ 判据自己把环境**说清楚**，不靠 cwd 恰好对（2026-11-03 教训）：
    #   本层要在"哪个项目的知识库上验"是明确的 —— 就用本仓上层的那个项目根，
    #   通过 SEMSEARCH_PROJECT_ROOT 传进去。之前靠 cwd 猜，套件里换目录就"跳过"，
    #   于是判据从"验过"静默降级成"没验"，而且看不出来。
    项目根 = 根.parents[1]
    if (项目根 / ".semsearch_index").is_dir() and not os.environ.get("SEMSEARCH_PROJECT_ROOT"):
        os.environ["SEMSEARCH_PROJECT_ROOT"] = str(项目根)

    # ★ 二进制同理：PATH 里可能有个**旧版** semsearch（本机实测有：
    #   `semsearch/.semsearch_bin/semsearch.EXE`，没有通用形态 —— 它会去写
    #   `E:\基础设施\.audit_trail\` 然后 rc=1）。不显式指定的话，判据就在验一个
    #   旧二进制 ⇒ 结论无效（而现象是"跳过"，看着像无害）。
    #   优先源仓里那份刚构建的；`SEMSEARCH_BIN` 已给就尊重调用方。
    if not os.environ.get("SEMSEARCH_BIN"):
        for _c in (项目根 / "semsearch" / "bin" / "semsearch.exe",
                   项目根 / "target" / "release" / "semsearch.exe"):
            if _c.is_file():
                os.environ["SEMSEARCH_BIN"] = str(_c)
                break

    能力 = S.可用()
    if not 能力.get("可用"):
        print(f"semsearch_link：跳过 —— {能力.get('原因')}")
        print("边界：跳过≠绿。这台机器没有可用二进制/索引时，本层能力**未验**。")
        return 0

    print(f"  · 二进制={能力.get('二进制')} 索引条目={能力.get('索引条目')}")
    断言((能力.get("索引条目") or 0) > 0, "索引有条目（读得到别人的知识）")

    # ② 关键词检索真有料
    条 = S.条目(关键词="交互")
    断言(len(条) >= 1, f"关键词检索返回条目（实得 {len(条)}）")
    if 条:
        断言(bool(条[0].get("pr")) and bool(条[0].get("名称")),
             f"条目带 PR 与名称（{条[0].get('pr')} {条[0].get('名称')}）")

    # ③ pr 分支单对象
    pr条 = S.条目(pr="PR-001")
    断言(len(pr条) == 1 and pr条[0].get("concept_name") == "交互",
         f"pr 分支解析单个 JSON 对象（实得 {len(pr条)}）")

    # ④ 任意目录正则检索
    src = 根 / "src"
    中 = S.目录命中(str(src), "判据门", 上限=3)
    断言(len(中) >= 1, f"目录正则检索有命中（实得 {len(中)}）")
    if 中:
        断言(all(h.get("行") and h.get("文件") for h in 中), "命中带 file:line")

    # ⑤ 注入守预算、无命中为空
    块 = S.注入("交互", 上限字=300)
    断言(0 < len(块) <= 520, f"注入块非空且守预算（实得 {len(块)} 字）")
    断言("PR-" in 块, "注入块带来源编号（PR-…）")
    断言(S.注入("这个词库里一定没有·zzzqqq") == "", "查不到时返回空串（不编造命中）")

    # ⑥ ★ 通用形态：在**外部项目**里当知识库用（2026-11-03 新增）
    #
    #   这一条锁的是"它不再只能活在太虚元道里"。改造前三个硬门槛：
    #     索引位置焊死（临时目录放空索引会被忽略，stats 仍报 389 条）、
    #     id 只能 PR-xxx（KB-001 被拒）、轴名写死（层级/知识等级）。
    #   现在由调用方给 `SEMSEARCH_PROJECT_ROOT` + 项目自己的 `semsearch.schema.json`。
    import json as _json
    import shutil as _shutil
    import subprocess as _sp
    import tempfile

    二 = S.二进制()
    if 二 is None:
        断言(False, "通用形态：需要二进制")
    else:
        临时 = Path(tempfile.mkdtemp(prefix="sem-link-", dir=str(根)))
        atexit.register(shutil.rmtree, 临时, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
        try:
            (临时 / ".semsearch_index").mkdir()
            (临时 / ".semsearch_index" / "concepts.json").write_text("[]", encoding="utf-8")
            (临时 / "semsearch.schema.json").write_text(_json.dumps({
                "id_pattern": r"^KB-\d+$",
                "level_labels": ["线索", "经验", "事实"],
                "index_path": ".semsearch_index/concepts.json",
                "axes": [
                    {"name": "故障层", "labels": ["语法", "类型", "编译"]},
                    {"name": "子系统", "labels": []},
                    {"name": "判决", "labels": ["绿", "红", "未验"]},
                    {"name": "动作", "labels": []},
                    {"name": "证据等级", "labels": ["SOLID", "PLAUSIBLE", "UNVERIFIABLE"]},
                    {"name": "深度", "labels": []},
                    {"name": "编号段", "labels": []},
                    {"name": "编号偏移", "labels": []},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            环境 = S._库模式环境(项目根=临时)

            def _跑(*参):
                p = _sp.run([str(二), *参], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=环境, cwd=str(根), timeout=120)
                return p.returncode, (p.stdout or "") + (p.stderr or "")

            rc, 出 = _跑("index", "--add", "--pr", "KB-001", "--name", "刻度单位",
                        "--definition", "绘图刻度统一 kHz", "--level", "事实")
            断言(rc == 0 and "成功" in 出, f"外部项目能用**自己的 id** 写入（{出.strip().splitlines()[-1][:60] if 出.strip() else '无输出'}）")
            条 = _json.loads((临时 / ".semsearch_index" / "concepts.json").read_text(encoding="utf-8"))
            断言(len(条) == 1 and 条[0]["dimensions"]["dim4"]["value"] == 2,
                 "写进了**本项目**的索引，且中文等级「事实」编码为 2")
            断言(条 and 条[0]["dimensions"]["dim0"]["meaning"] == "故障层",
                 "轴名来自本项目口径（dim0=故障层），不是写死的「层级」")

            # 命名空间隔离：本项目写不进"别人项目的号"。
            # ★ 必须给全 `--name/--definition/--level`：漏参数走的是"参数检查"那条路，
            #   测不到命名空间（首版就漏了 --definition，结果测了个 panic）。
            rc, 出 = _跑("index", "--add", "--pr", "PR-001", "--name", "串味",
                        "--definition", "不该成功", "--level", "事实")
            断言(rc != 0 and "命名空间" in 出, "本项目的口径挡住了别人的 id（PR-001 写不进来）")

            # 旧库（太虚元道）不能被这次实验碰到
            旧 = 根.parent.parent / ".semsearch_index" / "concepts.json"
            if 旧.is_file():
                旧数 = len(_json.loads(旧.read_text(encoding="utf-8-sig")))
                断言(旧数 >= 300, f"实验期间太虚元道索引仍是 {旧数} 条（没被写坏）")
        finally:
            for _ in range(5):
                _shutil.rmtree(临时, ignore_errors=True)
                if not 临时.exists():
                    break
            断言(not 临时.exists(), "通用形态用例不留临时目录")

    if 失败:
        print(f"\nsemsearch_link：红 {len(失败)} 项 —— " + "；".join(失败))
        return 1
    print("\nsemsearch_link：绿 —— 能读、带来源、守预算、查不到不编")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
