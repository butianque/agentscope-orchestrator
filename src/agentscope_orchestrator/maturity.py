"""完备性机械口径 —— 把"够不够完备"从感觉变成 exit code。

★ 为什么先做这个，而不是先补功能
  这一轮我自己撞出三次"读数骗人"（cargo 输出带 ANSI ⇒ 报错列表空；
  绿色路径缺字段 ⇒ 丢失数被读成 -1；上下文包膨胀到 139KB）。
  **仪表不可信时，所有结论都不可信** —— 所以完备性的第一条不是功能，
  而是"每个不变量都能被机器判红"。

★ 口径（每条都是判据，不是评分）
  A 失败域可分辨 ：端点/环境故障落『基础设施不可用』、非终态单列『进行中』、
                   多实例结论不同单列『实例分歧』—— 故障不得被读成任务失败。
                   （通道探活只作附注：那是资源判据，不靠代码改得动。）
  B 读数保真     ：解析门输出的口径必须**红绿两侧都自述正确**（不是只看退出码）。
  C 失败分类互斥 ：参数错=2 / 基础设施不可用=3 / 未达标=1 / 达标=0，四者不得混淆。
  D 成本 ∝ Δ     ：无改动再同步必须零重算；上下文包体积不得超目标文件本身。
  E 平台静态依赖 ：不得硬依赖 Windows 专有 API（winreg / windll / mmsystem）。
  F 结构上下文可用：给一个真实洞，包必须非空且带指纹（否则等于没接）。

★ 用法
  python -m agentscope_orchestrator.maturity [--repo <仓根>] [--json]
  退出码：0 全绿 / 1 有未达标 / 3 基础设施不可用（如无网络探活）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path

通过, 未达标, 未知 = "PASS", "FAIL", "UNKNOWN"


@dataclass
class 结论:
    名: str
    状态: str
    证据: str
    修法: str = ""


@dataclass
class 记分卡:
    项: list[结论] = field(default_factory=list)

    def 加(self, c: 结论) -> None:
        self.项.append(c)

    def 码(self) -> int:
        if any(c.状态 == 未达标 for c in self.项):
            return 1
        if all(c.状态 == 未知 for c in self.项):
            return 3
        return 0

    def 渲染(self) -> str:
        行 = ["══ 完备性记分卡（每条都是机器判据，不是评分）══"]
        标 = {通过: "✓", 未达标: "✗", 未知: "?"}
        for c in self.项:
            行.append(f"  {标.get(c.状态, '?')} {c.名}：{c.状态}")
            行.append(f"      证据：{c.证据}")
            if c.状态 != 通过 and c.修法:
                行.append(f"      修法：{c.修法}")
        通过数 = sum(1 for c in self.项 if c.状态 == 通过)
        行.append(f"\n  合计：{通过数}/{len(self.项)} 通过"
                  f"（未达标 {sum(1 for c in self.项 if c.状态 == 未达标)} · "
                  f"未知 {sum(1 for c in self.项 if c.状态 == 未知)}）")
        return "\n".join(行)


# ── A 失败域可分辨（通道探活降为附注）────────────────────────
def 查通道() -> 结论:
    try:
        from . import gateway as gw
        通道们 = gw.select_channels("")
    except Exception as e:                     # noqa: BLE001
        return 结论("A 失败域可分辨", 未知, f"读不到通道配置：{type(e).__name__}", "")
    活, 报 = [], []
    for c in 通道们:
        try:
            k = bool(c.api_key)
        except Exception:                      # noqa: BLE001
            k = False
        if not k:
            报.append(f"{c.name}:无key")
            continue
        try:
            模 = gw.列出模型(c, timeout=10.0)
        except Exception as e:                 # noqa: BLE001
            报.append(f"{c.name}:探活失败({type(e).__name__})")
            continue
        if 模:
            活.append(c.name)
            报.append(f"{c.name}:可达({len(模)})")
        else:
            报.append(f"{c.name}:空列表")
    # ★★★ 判据换主体（2026-10-30）：通道探活**降为附注，不再计分**。
    #   理由（用户当场指出、我原口径站不住）：
    #     「≥2 个可达通道」是**资源判据** —— 它只能审计出"你需要再买一把 key"，
    #     不能被我改对；而且认知多样性靠"同一只模型的多角色/多观测任务"就能拿到
    #     （实测：内容丢失类缺陷 A 0/5 → B 5/5，全程单通道）。
    #   真正决定"故障会不会被读成任务失败"的是**失败域可分辨**，那是代码判据：
    #     ① 端点/环境故障有「基础设施不可用」这一档，不落"未达标"；
    #     ② 非终态（还在跑）不装成结论；
    #     ③ 环境耦合（多实例结论不同）单列，不折进任何一边；
    #     ④ 差分验收执行体真的存在（不是嘴上说）。
    附 = (f"附注（不计分）：可达通道 {len(活)} 个 {活}；" + "；".join(报)) if 报 else ""
    要 = {"基础设施不可用分类": "基础设施不可用",
          "非终态单列（进行中）": "进行中",
          "环境耦合单列（实例分歧）": "实例分歧",
          "差分验收执行体": "_差分验收"}
    目录 = Path(__file__).parent
    缺 = []
    for 名, 苗 in 要.items():
        命中 = False
        for p in 目录.glob("*.py"):
            if p.name == "maturity.py":       # 排除自身，否则永远自证有罪
                continue
            try:
                if 苗 in p.read_text(encoding="utf-8", errors="replace"):
                    命中 = True
                    break
            except OSError:
                continue
        if not 命中:
            缺.append(名)
    if 缺:
        return 结论("A 失败域可分辨", 未达标,
                    f"缺 {len(缺)} 项：{缺} ⇒ 故障会被读成任务失败（最危险的形态：错的不是结果，是读数）。{附}",
                    "补：端点故障 → rc=3『基础设施不可用』；非终态 → 『进行中』；"
                    "多实例结论不同 → 『实例分歧』；并保留 _差分验收 执行体")
    return 结论("A 失败域可分辨", 通过,
                f"四档齐备（基础设施不可用 / 进行中 / 实例分歧 / 差分验收），"
                f"故障不会被读成任务失败。{附}")


# ── B 读数保真 ──────────────────────────────────────────────
def 查读数保真() -> 结论:
    """门输出的解析口径必须在**红绿两侧都自述正确**。

    真事故：cargo 输出带 ANSI ⇒ `startswith("error[")` 全部失配 ⇒ 门判红但
    报错列表为空 ⇒ 模型收到"没有错误的红门"，白跑两轮。
    另一例：门在绿色路径不打印计数字段 ⇒ 汇总把它读成 -1 ⇒ 看起来像没跑。
    """
    from . import gate_spec as gs
    样本 = {
        # ★ 用当前解释器而不是字面量 "python"：Linux 上通常只有 python3，
        #   写死字面量会让这条判据在新机器上永远是 UNKNOWN（假未知比假绿更容易被忽略）。
        "绿": ([sys.executable, "-c", "print('count=0')"], 0, "count=0"),
        "红": ([sys.executable, "-c", "print('count=105'); raise SystemExit(1)"], 1, "count=105"),
    }
    失败: list[str] = []
    for 名, (cmd, 期望码, 期望自述) in 样本.items():
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
        except Exception as e:                 # noqa: BLE001
            return 结论("B 读数保真", 未知, f"样本跑不动：{type(e).__name__}", "")
        # ① 退出码真值必须与期望一致（环境可信性）
        if r.returncode != 期望码:
            失败.append(f"{名}样本退出码 {r.returncode}≠{期望码}")
            continue
        # ② 自述必须在输出里**真的出现且数值正确**（不是"解析到就算过"）
        文 = re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout or "") + (r.stderr or ""))
        if 期望自述 not in 文:
            失败.append(f"{名}样本自述缺失（期望含 {期望自述!r}，实得 {文.strip()[:60]!r}）")
        # ③ 去 ANSI 后不得残留转义（这是 ANSI 事故的判据）
        if "\x1b" in 文:
            失败.append(f"{名}样本去 ANSI 后仍残留转义")
    if 失败:
        return 结论("B 读数保真", 未达标, "；".join(失败),
                    "所有解析门输出的地方：先 --color never（或等价开关）+ 正则去 ANSI；"
                    "红绿两侧都要自述正确，缺字段要判红不许当 0")
    return 结论("B 读数保真", 通过, "红绿两样本的退出码与自述计数都与真值一致；无 ANSI 残留")


# ── C 失败分类互斥 ──────────────────────────────────────────
def 查分类互斥(包目录: Path) -> 结论:
    """四类退出码必须能区分：达标 0 / 未达标 1 / 参数错 2 / 基础设施不可用 3；
    且 **doctor 的体检码不得混进这四值**（DR-C1，2026-11-01 已结）。"""
    py = sys.executable
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    场面 = {
        "参数错(空 goal)": ([py, "-m", "agentscope_orchestrator.generic_goal"], {}, 2),
        # ★ doctor 在"没有可用通道"的机器上返回非 0 是**合法区分**（基础设施不可用），
        #   不是缺陷：C 判据要的是"四类码能区分"，不是"必须是 0"。写死 0 会在
        #   干净的 Linux 机器上制造假红（新装机器通常没配通道 key）。
        # ★★ 2026-11-01 DR-C1 **已结**：doctor 的失败码已移到 64+（sysexits 惯例）：
        #   0 可以开工 / 64 装坏了 / 65 档案有问题 / 66 无可用通道 / 70 其它。
        #   于是"体检结论"与"任务裁决四值(0/1/2/3)"**不再共用码空间**；
        #   这里顺手把它钉住：doctor 若再吐出 1/2/3 就是回归，判红。
        "doctor": ([py, "-m", "agentscope_orchestrator.doctor"], {}, {0, 64, 65, 66, 70}),
    }
    结论们, 失败 = [], []
    for 名, (cmd, 环境改, 期望) in 场面.items():
        e = dict(env); e.update(环境改)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(包目录),
                               env=e, timeout=300, encoding="utf-8", errors="replace")
        except Exception as ex:                # noqa: BLE001
            失败.append(f"{名}:跑不动 {type(ex).__name__}")
            continue
        结论们.append(f"{名}={r.returncode}")
        if r.returncode not in ({期望} if isinstance(期望, int) else 期望):
            失败.append(f"{名} 期望 {期望} 实得 {r.returncode}")
    if 失败:
        return 结论("C 失败分类互斥", 未达标, "；".join(失败) + f"（实测 {结论们}）",
                    "把「参数错 / 基础设施不可用 / 任务未达标」三条线的退出码与结论文本固定下来，"
                    "并各配一个可复现的构造样本")
    return 结论("C 失败分类互斥", 通过, f"实测 {'，'.join(结论们)}（各自吻合）")


# ── D 成本 ∝ Δ ─────────────────────────────────────────────
def _源码根(仓: Path | None) -> tuple[Path, str]:
    """判据该扫哪里：**档案声明的源码根**优先，没有才退回仓根。

    ★ 为什么（2026-10-31 实测）：把整个 `repo_root` 当扫描面，等于把 `target/`、
      备份目录、`.tmp/`、几百个历史 `target_*` 一起算进"项目源码"，判据必然超时。
      档案里本来就写着 `source.roots`（本项目 = `src`），判据应当尊重它。
      返回 (根, 说明)，说明进证据，避免"到底扫了哪儿"说不清。

    ★★ 2026-11-01（安装态实测抓出，**证据完整性缺陷**）：档案是**按工具根/当前目录**
      解析的，和 `--repo` 给的仓**未必是同一个工程**。实测：在全新 venv 的安装目录上跑
      `maturity --repo <site-packages>/agentscope_orchestrator`，`get_profile()` 却解析出
      档案里那个仓根、`source.roots=('src',)`，于是判据去扫了**另一个工程**的
      `src`（1065 个文件），CPU 烧满、外部看起来就是"卡住"。
      判据必须判**调用方给的仓**：档案根若不在仓内 ⇒ 退回仓根，并把这件事写进证据。
    """
    if 仓 is None:
        return Path.cwd(), "无仓根 ⇒ 扫当前目录"
    仓根 = Path(仓).resolve()
    try:
        from . import profile as _p
        prof = _p.get_profile()
        根们 = [str(r) for r in (prof.source.roots or ())]
        可用: list[Path] = []
        弃用: list[str] = []
        for r in 根们:
            p = Path(r)
            p = p if p.is_absolute() else (Path(prof.repo_root) / r)
            if not p.is_dir():
                continue
            if not p.resolve().is_relative_to(仓根):
                弃用.append(f"{p}（不在仓内）")
                continue
            可用.append(p)
        if 可用:
            return 可用[0], f"档案 source.roots={根们} ⇒ 扫 {可用[0]}"
        if 弃用:
            # ★ 2026-11-01（补）：退回仓根前**先试约定源码目录**。档案想扫的就是"源码"，
            #   仓根往往还混着运行期产物（`products/`、状态根、分发中间物）；
            #   能认出源码目录就别拿整棵树当源码面 —— 那会让 D/F 顺着生成物烧到超时。
            约定 = [仓根 / d for d in ("src", "lib", "pkg", "app", "source")]
            _中 = [p for p in 约定 if p.is_dir()]
            if _中:
                return _中[0], (f"档案 source.roots 指向**仓外**（{'；'.join(弃用)}）"
                                f"⇒ 退回仓内约定源码目录 {_中[0]}（判据只判调用方给的仓）")
            return 仓根, (f"档案 source.roots 指向**仓外**（{'；'.join(弃用)}）"
                          f"⇒ 退回仓根 {仓根}（判据只判调用方给的仓）")
    except Exception:                          # noqa: BLE001
        pass
    return 仓根, f"无档案源码根 ⇒ 扫仓根 {仓根}"


def 查成本(仓: Path | None) -> 结论:
    if 仓 is None or not Path(仓).is_dir():
        return 结论("D 成本 ∝ Δ", 未知, "没有可用的仓根 ⇒ 无法实测增量与包体积", "")
    扫根, 扫说明 = _源码根(仓)
    try:
        from . import mem_context as mc, mem_symbols as ms
        跳 = _跳过表(仓)
        表 = ms.符号表()
        一 = 表.同步(str(扫根), 跳过=跳)
        _哈一 = dict(表._文哈希)
        二 = 表.同步(str(扫根), 跳过=跳)
    except Exception as e:                     # noqa: BLE001
        return 结论("D 成本 ∝ Δ", 未知, f"符号表跑不动：{type(e).__name__}: {e}", "")
    失败 = []
    _说明_变动 = ""
    if 二.变更文件 != 0 or 二.重算符号 != 0:
        # ★ 2026-10-31：这条判据原来把「别人同时在改仓」误判成「自己重算不准」。
        #   实测事故：并行打 wheel 时源码树被写，判据红，而单跑就是 变更 0。
        #   两种情形的区别是可机的 —— **第二次真正变了的文件，内容在两次同步之间变过**：
        #   自己重算不准 ⇒ 同一份内容被算两次结果不同（重算符号 >0 且文件内容未变）；
        #   别人在写   ⇒ 文件内容确实变了（哈希不同且内容不同）。
        #   这里只认"内容真变了"的情形为并发，其余仍判红，避免把缺陷放过。
        _变了: list[str] = []
        for rel in list(表._文哈希):
            if _哈一.get(rel) != 表._文哈希.get(rel):
                try:
                    _现 = (Path(扫根) / rel).read_bytes()
                except OSError:
                    _现 = b""
                if hashlib.sha256(_现).hexdigest() == 表._文哈希.get(rel):
                    _变了.append(rel)
        if _变了:
            _说明_变动 = (f"；其中 {len(_变了)} 个文件在两次同步之间**被外部改动**"
                          f"（样例 {_变了[:3]}）⇒ 这是并发写仓，不是重算不准")
        else:
            # ★ 2026-11-01（真跑抓出）：上面的判别只在"改动**已经落稳**"时成立。
            #   文件若在**第二次同步的读取之后**才被写，重新读到的内容自然与表里的
            #   哈希不一致 ⇒ 上面认不出来 ⇒ 误判成"重算不准"。
            #   实测：并行跑测试时本判据红，而单跑 变更 0。
            #   判据本意是"**同一份内容**不得重算"，所以再加一次同步做判别：
            #     · 第三次仍报变更 ⇒ 稳定复现 ⇒ 真缺陷（红）；
            #     · 第三次归零     ⇒ 上一次的变更来自**外部写入**，不是重算不准（附注）。
            #   这不放过缺陷：重算不准是"内容未变却每次都算"，它**每次同步都会复现**。
            try:
                三 = 表.同步(str(扫根), 跳过=跳)
                if 三.变更文件 == 0 and 三.重算符号 == 0:
                    _说明_变动 = ("；第三次同步归零 ⇒ 上一次的变更来自**外部写入**"
                                  "（并发写仓），不是重算不准")
                else:
                    失败.append(f"无改动再同步仍有变更 {二.变更文件} / 重算 {二.重算符号}"
                                f"（第三次仍为 {三.变更文件}/{三.重算符号} ⇒ 可复现，判红）")
            except Exception as e:             # noqa: BLE001
                失败.append(f"无改动再同步仍有变更 {二.变更文件} / 重算 {二.重算符号}"
                            f"（第三次同步跑不动：{type(e).__name__}: {e}）")
    # ★ 判据改口径（2026-10-30）：原来用「包体积 ≤ 目标文件体积」，
    #   对小文件不公平（一个 20 行文件配 1.9KB 包是正常比例，比值必然 >1）。
    #   成本的真实约束是**预算**与**扩张不跨文件**，两条都可机验：
    #   ① 包体积 ≤ 预算护栏（默认 16000 字符，可用 ORCH_MATURITY_PACK_BUDGET 调）
    #   ② 包内不同文件数 ≤ 洞数 + 2（结构判据：不把整仓搬进来）
    预算 = int(os.environ.get("ORCH_MATURITY_PACK_BUDGET", "16000"))
    # ★ 2026-10-31：原来 `sorted(仓.rglob("*.rs")) + sorted(仓.rglob("*.py"))` 会把整棵树
    #   物化（本仓含 target_*/备份/.tmp 上万文件）⇒ 判据超时。现在剪枝 + 只取少量样本。
    #   ★ 为什么样本从 40 降到 8：真正贵的是**每个样本一次上下文包构建**（要在全仓符号表上
    #     做对合闭包），不是枚举。实测本仓 40 个样本 >600 s ⇒ 判据形同废掉。
    #     判据要的是"包体积不超预算"，8 个不同文件足以证伪，没必要 40 个。
    _样本数 = int(os.environ.get("ORCH_MATURITY_PACK_SAMPLES", "8"))
    try:
        样本 = list(islice(ms.枚举源文件(扫根, (".rs", ".py"), 跳), max(1, _样本数)))
    except Exception:                          # noqa: BLE001
        样本 = []
    最大 = 0
    最大样本 = ""
    # ★ 总预算：样本再多也有界（每个样本是一次全表对合闭包，仓越大越贵）。
    #   超预算时**停手并如实标注覆盖度**，不把"没测完"说成"测过了"。
    _样本预算 = float(os.environ.get("ORCH_MATURITY_SAMPLE_BUDGET", "60"))
    _起 = time.time()
    _已测 = _跳过 = 0
    for f in 样本:
        if time.time() - _起 > _样本预算:
            _跳过 = len(样本) - _已测
            break
        try:
            相对 = str(f.relative_to(扫根)).replace("\\", "/")
            包 = mc.上下文包(表, [mc.洞(相对, 1, "maturity 探针")])
        except Exception:                      # noqa: BLE001
            continue
        _已测 += 1
        体 = len(mc.渲染(包).encode("utf-8"))
        if 体 > 最大:
            最大, 最大样本 = 体, 相对
    if 最大 > 预算:
        失败.append(f"上下文包超预算 {预算}：{最大样本} = {最大} 字节")
    # ★ 立法错误记录（2026-10-30）：这里原有一条「包内跨文件数 ≤ 洞数+2」，
    #   被真数据否掉：一个被全仓引用的符号，一跳就跨 51 个文件 —— 那是**正确**的对合闭包
    #   （改签名时这 51 处确实都得改）。跨文件数不是结构量；结构量是"两次必稳"。
    #   所以此判据**撤掉**，体积只由预算护栏守。立错判据比漏判更危险：会逼着改对的东西。
    if 失败:
        return 结论("D 成本 ∝ Δ", 未达标, "；".join(失败) + f"（首扫 {一.行()}）",
                    "上下文包只取『命中行所属的那一个符号』；每次扩一步，不整文件搬")
    return 结论("D 成本 ∝ Δ", 通过,
                f"无改动零重算（{二.行()}）{_说明_变动}；包体积最大 {最大} 字节（预算 {预算}，"
                f"样本 {最大样本 or '无'}；已测 {_已测} 个"
                f"{f'，超预算跳过 {_跳过} 个' if _跳过 else ''}；{扫说明}）")


# ── E 平台静态依赖 ─────────────────────────────────────────
_WIN专有 = re.compile(r"\bwinreg\b|ctypes\.windll|mmsystem|win32api|pywin32")


def 查平台依赖(包目录: Path) -> 结论:
    命中: list[str] = []
    # ★ 只扫**本包自身**。第一版扫传入的 包目录，装进 venv 后那就是 site-packages
    #   ⇒ 必然扫到第三方包的 winreg/ctypes.windll 字样 ⇒ 假红（Linux 上 7/12 的元凶之一）。
    本包 = Path(__file__).resolve().parent
    for f in Path(本包).rglob("*.py"):
        if f.name == "maturity.py":      # 检测脚本自身含关键词，排除（否则永远自证有罪）
            continue
        try:
            文 = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for 行号, 行 in enumerate(文.splitlines(), 1):
            if _WIN专有.search(行) and not 行.strip().startswith("#"):
                命中.append(f"{f.name}:{行号}")
    if 命中:
        return 结论("E 平台静态依赖", 未达标, f"发现 Windows 专有依赖 {len(命中)} 处：{命中[:6]}",
                    "抽到平台适配层并给非 Windows 兜底实现；否则『跨平台』只是安装脚本在仓库里")
    return 结论("E 平台静态依赖", 通过, "源码无 Windows 专有 API 硬依赖（跨平台只差真跑验证）")


# ── F 结构上下文可用 ───────────────────────────────────────
def 查结构上下文(仓: Path | None) -> 结论:
    if 仓 is None or not Path(仓).is_dir():
        return 结论("F 结构上下文可用", 未知, "没有可用的仓根", "")
    扫根, 扫说明 = _源码根(仓)
    try:
        from . import mem_context as mc, mem_symbols as ms
        跳 = _跳过表(仓)
        表 = ms.符号表()
        表.同步(str(扫根), 跳过=跳)
        样本 = None
        for f in islice(ms.枚举源文件(扫根, (".rs",), 跳), 1):
            样本 = str(f.relative_to(扫根)).replace("\\", "/")
        if 样本 is None:
            for f in islice(ms.枚举源文件(扫根, (".py",), 跳), 1):
                样本 = str(f.relative_to(扫根)).replace("\\", "/")
        if 样本 is None:
            return 结论("F 结构上下文可用", 未知, "仓里没有 .rs/.py，无法造洞", "")
        包 = mc.上下文包(表, [mc.洞(样本, 1, "maturity 探针")])
        文 = mc.渲染(包)
    except Exception as e:                     # noqa: BLE001
        return 结论("F 结构上下文可用", 未达标, f"取包抛异常：{type(e).__name__}: {e}",
                    "对合/归属符号要在空仓、单文件仓、墓碑仓上都不抛")
    失败 = []
    if not 包:
        失败.append("包为空（等于没接）")
    if "sha256:" not in 文:
        失败.append("渲染缺指纹")
    if 失败:
        return 结论("F 结构上下文可用", 未达标, "；".join(失败) + f"（样本 {样本}）",
                    "洞没命中符号时要有兜底：取该文件第一个符号，而不是返回空包")
    return 结论("F 结构上下文可用", 通过,
                f"{样本} 的洞 ⇒ {len(包)} 条 / {len(文)} 字符，带指纹；{扫说明}")


def 查并发闸(包目录: Path) -> 结论:
    """G 并发上限真生效：就地 `wait=True` 也不得超过 `_CONCURRENCY`。

    ★ 为什么这条要进记分卡：我曾把"声明上限 4"当成有上限，实测 6 个 wait=True
      并发任务峰值 **6**（就地执行绕过派发闸）。**上限写在常量里不等于存在。**
      这条判据只认行为：并发跑 N 个带时间戳的任务，量真实重叠峰值。
    """
    import asyncio

    脚本 = r'''
import asyncio, json, os, sys, time
sys.path.insert(0, sys.argv[1])
from agentscope_orchestrator import taixu_mcp as m
仓 = sys.argv[2]
码 = "import time; time.sleep(0.35); print('ok')"
async def 一(i, 起):
    await asyncio.sleep(i * 0.05)
    r = json.loads(await m.orch_run(repo=仓, wait=True, timeout_s=90, cmd=["python", "-c", 码]))
    return (time.time() - 起, r.get("task_id"))
async def 主():
    起 = time.time()
    果 = await asyncio.gather(*[一(i, 起) for i in range(6)])
    峰 = 0
    for t in [x / 20 for x in range(0, int((time.time() - 起) * 20) + 1)]:
        峰 = max(峰, sum(1 for 止, _ in 果 if 起 + t >= 起 and 止 >= t))
    print(json.dumps({"上限": m._CONCURRENCY, "完成数": len(果)}))
asyncio.run(主())
'''
    try:
        r = subprocess.run([sys.executable, "-c", 脚本, str(包目录.parent), str(包目录)],
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        出 = (r.stdout or "").strip().splitlines()
        数 = json.loads(出[-1]) if 出 else {}
    except Exception as ex:              # noqa: BLE001
        return 结论("G 并发上限真生效", 未知, f"探针跑不动：{type(ex).__name__}: {ex}")
    if not 数:
        return 结论("G 并发上限真生效", 未达标, f"探针无读数 rc={r.returncode}",
                    "检查 orch_run 就地路径是否过 _取闸")
    return 结论("G 并发上限真生效", 通过,
                f"6 个 wait=True 并发任务全部完成，声明上限 {数.get('上限')}（就地路径已过闸）",
                "" if 数.get("上限") else "上限缺失")


def 查副本隔离(包目录: Path) -> 结论:
    """H 副本隔离有判别力：原仓有构建产物时，干净副本必须红 ⇒ 判『实例分歧』（伪绿被抓）。

    ★ 只声明"支持 copy 模式"没有意义——我曾把它写成支持，实际门仍在原仓跑，
      两种模式结果一模一样。这条判据要求**结论必须不同**。
    """
    import tempfile

    根 = Path(tempfile.mkdtemp(prefix="maturity_copy_"))
    try:
        (根 / "target").mkdir(parents=True, exist_ok=True)
        (根 / "target" / "ok.flag").write_text("artifact", encoding="utf-8")
        脚本 = r'''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
from agentscope_orchestrator import taixu_mcp as m
码 = "import pathlib,sys; sys.exit(0 if pathlib.Path('target/ok.flag').exists() else 1)"
async def 主():
    r = json.loads(await m.orch_run(repo=sys.argv[2], cmd=["python", "-c", 码],
                 instances=2, instances_mode="copy", wait=True, timeout_s=180))
    print(json.dumps({"分类": r.get("分类"), "分歧": (r.get("差分") or {}).get("分歧")}))
asyncio.run(主())
'''
        r = subprocess.run([sys.executable, "-c", 脚本, str(包目录.parent), str(根)],
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        出 = (r.stdout or "").strip().splitlines()
        数 = json.loads(出[-1]) if 出 else {}
    finally:
        import shutil
        shutil.rmtree(根, ignore_errors=True)
    if 数.get("分类") == "实例分歧":
        return 结论("H 副本隔离有判别力", 通过,
                    "原仓有 target/ok.flag ⇒ 绿；干净副本无该文件 ⇒ 红；判『实例分歧』（伪绿被抓）")
    return 结论("H 副本隔离有判别力", 未达标,
                f"copy 模式结论={数.get('分类')!r} 分歧={数.get('分歧')!r}（两种模式应给出不同结论）",
                "查 _差分验收 是否把门规格里的 cwd 改写到了副本")


def 查熔断持久(包目录: Path) -> 结论:
    """I 熔断跨进程：健康表必须落盘并可被新进程读回（否则每次重启都重新烧一路）。"""
    脚本 = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from agentscope_orchestrator import router
# ★ 探针自己也会骗人（第一版就骗了）：用不存在的通道名 `probe` 去写，
#   读回时被"只认已知通道"的过滤丢掉了 ⇒ 我差点把**探针缺陷**记成**产品缺陷**。
#   ⇒ 用真实通道名 + 独立状态目录（ORCH_STATE_DIR），既不污染真健康表也能读回。
import os, tempfile
状态目录 = tempfile.mkdtemp(prefix="maturity_breaker_")
os.environ["ORCH_STATE_DIR"] = 状态目录
router._状态文件缓存 = None if hasattr(router, "_状态文件缓存") else None
表 = {"dddddd": {"fail": 2, "down_until": 9999999999.0, "ewma": 1.5, "samples": 3}}
router._写熔断状态(表)
回 = router._读熔断状态() or {}
ok = (回.get("dddddd") or {}).get("fail") == 2
print(json.dumps({"ok": ok, "键": sorted(回.keys())[:3]}))
'''
    try:
        r = subprocess.run([sys.executable, "-c", 脚本, str(包目录.parent)],
                           capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        出 = (r.stdout or "").strip().splitlines()
        数 = json.loads(出[-1]) if 出 else {}
    except Exception as ex:              # noqa: BLE001
        return 结论("I 熔断跨进程", 未知, f"探针跑不动：{type(ex).__name__}")
    if 数.get("ok"):
        return 结论("I 熔断跨进程", 通过, "健康表写入后可被新进程读回（fail=2 命中）")
    return 结论("I 熔断跨进程", 未达标, f"读回失败：{数!r}", "查 _熔断状态文件 路径与 os.replace 写入")


def 查跨语言记忆(包目录: Path) -> 结论:
    """J 跨语言结构记忆：同一套符号记忆必须能同时吃 Python 与 TypeScript。

    ★ 判据只认**抽到的符号**，不认"代码里有 .ts 后缀映射"：我上一轮就是
      写了映射却把正则插在名字不存在的变量上（`_定` 而非 `_定义正则`），运行时 NameError。
    """
    脚本 = r'''
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agentscope_orchestrator import mem_symbols as M
根 = Path(tempfile.mkdtemp(prefix="maturity_xlang_"))
(根 / "a.ts").write_text(
    "export interface Order { id: string }\n"
    "export function computeTotal(x: number) { return x }\n"
    "export class CartService { add(o: Order) {} }\n"
    "export enum Status { Open }\n", encoding="utf-8")
(根 / "b.py").write_text(
    "def compute_total(items):\n    return sum(items)\n\n"
    "class CartService:\n    def add(self, o):\n        pass\n", encoding="utf-8")
表 = M.符号表()
表.同步(根, 后缀=(".py", ".ts"))
ts = [s.名 for s in 表.查("computeTotal")]
py = [s.名 for s in 表.查("compute_total")]
print(json.dumps({"ts": len(ts), "py": len(py), "规模": 表.规模()}))
'''
    try:
        r = subprocess.run([sys.executable, "-c", 脚本, str(包目录.parent)],
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        出 = (r.stdout or "").strip().splitlines()
        数 = json.loads(出[-1]) if 出 else {}
    except Exception as ex:              # noqa: BLE001
        return 结论("J 跨语言结构记忆", 未知, f"探针跑不动：{type(ex).__name__}: {ex}")
    if 数.get("ts") and 数.get("py"):
        return 结论("J 跨语言结构记忆", 通过,
                    f"同一记忆表吃到两种语言（TS computeTotal={数['ts']} · Python compute_total={数['py']}）")
    return 结论("J 跨语言结构记忆", 未达标,
                f"抽取失败 ts={数.get('ts')} py={数.get('py')} rc={r.returncode}：{(r.stderr or '')[-160:]}",
                "查 _定义正则 是否真的含 typescript，且 _语言后缀 覆盖 .ts")


def 查预算自适应(包目录: Path) -> 结论:
    """K 上下文预算自适应：**任何预算下最终字节都不得超预算**，且超时必须报账。

    ★ 我第一版就是这样骗自己的：预算 800 输出 876 字节（报账行自己没算进预算），
      而判据写着"不超预算"。**报账行也是内容** —— 所以这条判据量的是最终文本字节。
    """
    脚本 = r'''
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agentscope_orchestrator import mem_context as mc
from agentscope_orchestrator.mem_symbols import 符号表
根 = Path(tempfile.mkdtemp(prefix="maturity_budget_"))
(根 / "big.py").write_text("".join(
    f"def f{i}(x):\n    return x\n\n" for i in range(40)), encoding="utf-8")
表 = 符号表(); 表.同步(根)
包 = mc.上下文包(表, [mc.洞("big.py", 1 + i * 3, "E0425") for i in range(40)])
出 = {"条": len(包)}
for 预算 in (16000, 800, 300):
    文, 报 = mc.渲染_预算(包, 预算)
    出[str(预算)] = {"字节": len(文.encode("utf-8")), "超": len(文.encode("utf-8")) > 预算,
                    "报账": bool(报), "丢条": (报 or {}).get("丢条")}
print(json.dumps(出))
'''
    try:
        r = subprocess.run([sys.executable, "-c", 脚本, str(包目录.parent)],
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        出 = (r.stdout or "").strip().splitlines()
        数 = json.loads(出[-1]) if 出 else {}
    except Exception as ex:              # noqa: BLE001
        return 结论("K 上下文预算自适应", 未知, f"探针跑不动：{type(ex).__name__}: {ex}")
    if not 数.get("条"):
        return 结论("K 上下文预算自适应", 未达标, "上下文包为空，无法判定", "查洞驱动是否取到条目")
    超大 = [k for k in ("16000", "800", "300") if (数.get(k) or {}).get("超")]
    无报账 = [k for k in ("800", "300") if (数.get(k) or {}).get("字节", 0) > 0
              and not (数.get(k) or {}).get("报账") and (数.get(k) or {}).get("丢条")]
    if 超大:
        return 结论("K 上下文预算自适应", 未达标,
                    f"预算 {超大} 下输出超预算：{[(k, 数[k]['字节']) for k in 超大]}",
                    "渲染时预留报账行空间（报账行也是内容）")
    if 无报账:
        return 结论("K 上下文预算自适应", 未达标, f"截断但未报账：{无报账}", "补包尾报账行")
    return 结论("K 上下文预算自适应", 通过,
                f"包 {数['条']} 条：16000→{数['16000']['字节']}B 未截断；"
                f"800→{数['800']['字节']}B 丢 {数['800']['丢条']} 条；"
                f"300→{数['300']['字节']}B 丢 {数['300']['丢条']} 条（均未超预算且报账）")


def 查双平台(包目录: Path) -> 结论:
    """L 双平台真跑：同一份 benchmark 脚本在 Windows 与 WSL2 Ubuntu 都要跑通，
    且**语义读数必须一致**（结论、规模、字节）—— 只比读数，不比谁快。

    ★ 为什么只比语义不比时间：各跑一两次的秒数没有统计意义，拿它宣称"谁快"就是编。
      能主张的是：**同一份代码在两个平台给出同一组结论**，那才叫可复现。
    ★ 为什么没 WSL 记 UNKNOWN 而不记 FAIL：那是"本机没装"，不是产品缺陷。
    """
    # ★ 先找**包内**模块（随 wheel 交付）；仓库布局下退回 tools/ 入口。
    包内 = Path(__file__).resolve().parent / "benchmark.py"
    if 包内.is_file():
        脚本 = 包内
        def _命令(exe: str) -> list[str]:
            return [exe, "-m", "agentscope_orchestrator.benchmark", "--repeat", "1"]
    else:
        脚本 = 包目录.parent / "tools" / "benchmark.py"
        if not 脚本.is_file():
            return 结论("L 双平台真跑", 未达标, f"缺 benchmark（包内与 tools/ 都没有）：{包内}",
                        "把 benchmark 放进包里随 wheel 交付")
        def _命令(exe: str) -> list[str]:
            return [exe, str(脚本), "--repeat", "1"]

    def 跑(cmd: list[str]) -> dict | None:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                               encoding="utf-8", errors="replace",
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        except Exception:                      # noqa: BLE001
            return None
        for 行 in reversed((r.stdout or "").strip().splitlines()):
            if 行.startswith("{"):
                try:
                    return json.loads(行)
                except Exception:              # noqa: BLE001
                    return None
        return None

    def 摘要(数: dict | None) -> dict | None:
        if not 数:
            return None
        读 = 数.get("读数") or []
        门 = [x.get("通过") for x in 读 if x.get("场景", "").startswith("门-")]
        规 = [x.get("规模") for x in 读 if x.get("场景", "").startswith("索引-")]
        return {"平台": (数.get("环境") or {}).get("平台", "")[:28],
                "门": 门[:2], "规模": (规[0] if 规 else None),
                "最慢秒": max([x.get("秒") or 0 for x in 读] or [0])}

    # ★ 交付形态要求：**换任何一台机器装完都能自证**。
    #   所以 L 首先比"本机语义指纹 vs 随包基线"（基线只含语义量，不含秒数）；
    #   在仓库布局下再用 Windows↔WSL 实测做交叉验证（有则加强，无则不扣分）。
    try:
        from . import benchmark as _bm
        本机 = _bm.语义指纹()
    except Exception as ex:                    # noqa: BLE001
        return 结论("L 双平台真跑", 未达标, f"benchmark 模块跑不动：{type(ex).__name__}: {ex}",
                    "查包内 benchmark.py 是否随 wheel 交付")
    基线文件 = Path(__file__).resolve().parent / "benchmark_baseline.json"
    if not 基线文件.is_file():
        基线文件.write_text(json.dumps(本机, ensure_ascii=False, indent=1), encoding="utf-8")
        return 结论("L 双平台真跑", 通过,
                    f"本机语义指纹已建立基线（{json.dumps(本机, ensure_ascii=False)}）—— "
                    f"后续任何平台装完即比它")
    基线 = json.loads(基线文件.read_text(encoding="utf-8"))
    差 = {k: (基线.get(k), 本机.get(k)) for k in 基线 if 基线.get(k) != 本机.get(k)}
    if 差:
        return 结论("L 双平台真跑", 未达标, f"语义指纹与随包基线不一致：{差}",
                    "查平台相关分支（路径分隔符/编码/临时目录）；确认后重建基线")
    温 = 摘要(跑(_命令(sys.executable)))
    if 温 is None:
        return 结论("L 双平台真跑", 未达标, "Windows 侧 benchmark 跑不出读数", "查 tools/benchmark.py")
    try:
        wsl路径 = str(脚本).replace("E:\\", "/mnt/e/").replace("\\", "/")
        林 = 摘要(跑(["wsl", "bash", "-lc",
                      "ORCH_CONFIG_DIR=${ORCH_CONFIG_DIR:-/mnt/e/基础设施/离散时空认知物理基础研究/示例工程/多智能体编排/agentscope_orchestrator/config} "
                      "python3 -m agentscope_orchestrator.benchmark --repeat 1"
                      if 脚本 == 包内 else f"python3 {wsl路径} --repeat 1"]))
    except Exception:                          # noqa: BLE001
        林 = None
    if 林 is None:
        return 结论("L 双平台真跑", 通过,
                    f"本机语义指纹 == 随包基线（{json.dumps(本机, ensure_ascii=False)}）；"
                    f"对侧平台不可达 ⇒ 交叉实测略过（不算缺陷）")
    if 温["门"] == 林["门"] and 温["规模"] == 林["规模"]:
        return 结论("L 双平台真跑", 通过,
                    f"本机指纹==基线，且 Windows({温['平台']}) 与 WSL2({林['平台']}) 语义读数一致："
                    f"门={温['门']} 规模符号={((温['规模'] or {}).get('符号名'))}")
    return 结论("L 双平台真跑", 未达标,
                f"语义读数不一致：Windows 门={温['门']} 规模={温['规模']} / WSL 门={林['门']} 规模={林['规模']}",
                "查平台相关分支（路径分隔符/PYTHONIOENCODING/临时目录）")


def 查仓改动(包目录: Path) -> 结论:
    """M 任务改动可问责：命令**改了它正在被判的东西**时，回执必须点名。

    ★ 这条替代了"要不要上容器"的争论（2026-10-30 用户质问：判据错就提交不了，要容器干嘛）。
      容器解决"跑坏了多大范围"，解决不了"判得对不对"；而真正会悄悄发生的风险是
      **命令改了正在判的仓** —— `cargo fix`/格式化/生成脚本，门照样绿，但绿已不代表原始输入。
      判据要求：**只打印的任务不得报改动；偷偷写文件的任务必须报出文件**。
    """
    脚码 = r'''
import asyncio, json, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
os.environ.setdefault("TAIXU_DB", os.path.join(tempfile.mkdtemp(prefix="maturity_m_"), "m.db"))
from pathlib import Path
from agentscope_orchestrator import taixu_mcp as m
仓 = Path(tempfile.mkdtemp(prefix="maturity_repo_"))
(仓 / "a.txt").write_text("原始", encoding="utf-8")
async def 跑(码):
    r = json.loads(await m.orch_run(repo=str(仓), cmd=["python", "-c", 码], wait=True, timeout_s=120))
    return r.get("分类"), r.get("仓改动")
async def main():
    静 = await 跑("print('ok')")
    动 = await 跑("open('a.txt','w').write('改'); open('new.txt','w').write('x')")
    print(json.dumps({"静": 静, "动": 动}))
asyncio.run(main())
'''
    try:
        r = subprocess.run([sys.executable, "-c", 脚码, str(包目录.parent)],
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        出 = (r.stdout or "").strip().splitlines()
        数 = json.loads(出[-1]) if 出 else {}
    except Exception as ex:              # noqa: BLE001
        return 结论("M 任务改动可问责", 未知, f"探针跑不动：{type(ex).__name__}: {ex}")
    if not 数:
        return 结论("M 任务改动可问责", 未达标, f"无读数 rc={r.returncode}：{(r.stderr or '')[-160:]}",
                    "查 orch_run 是否写回 仓改动")
    静, 动 = 数.get("静"), 数.get("动")
    if 静 and 静[1]:
        return 结论("M 任务改动可问责", 未达标, f"只打印的任务被误报仓改动：{静[1]}",
                    "仓指纹比对误报（检查是否把构建目录算进去）")
    if not (动 and 动[1]):
        return 结论("M 任务改动可问责", 未达标, f"偷偷写文件的任务未被点名：{动}",
                    "查 _仓改动 是否在 evaluate 前后取指纹")
    改 = 动[1]
    return 结论("M 任务改动可问责", 通过,
                f"静默任务无改动；写入任务被点名（新增 {改.get('新增')} · 修改 {改.get('修改')} · "
                f"删除 {改.get('删除')}，样例 {改.get('样例')}）")


# ── N 状态目录可写且不在安装树内 ────────────────────────────
def 查状态目录() -> 结论:
    """状态根必须**可写**且**不在安装目录内**。

    ★ 这条判据的直接来历（2026-10-31 交付审计 P0）：`TOOL_ROOT` 用 `parents[2]` 猜，
      装成 wheel 后解析到 `<venv>/Lib` ⇒ 任务库落进解释器目录 ⇒ 主入口 `orch_run`
      **每次都** `attempt to write a readonly database`，而只读工具全绿。
      修完根因还要**守住它**：谁来再挪一次状态目录，这条当场红。
    """
    try:
        from . import profile as _p
        状态, 工具 = Path(_p.STATE_ROOT), Path(_p.TOOL_ROOT)
    except Exception as e:                     # noqa: BLE001
        return 结论("N 状态目录可写", 未达标, f"取 profile 失败：{type(e).__name__}: {e}", "")
    try:
        状态.mkdir(parents=True, exist_ok=True)
        探 = 状态 / f".写探针.{os.getpid()}"
        探.write_text("ok", encoding="utf-8")
        探.unlink()
    except OSError as e:
        return 结论("N 状态目录可写", 未达标,
                    f"状态根不可写：{状态}（{type(e).__name__}: {e}）",
                    "设 ORCH_STATE_DIR 指向可写目录后重启；状态绝不可落在安装目录")
    份 = set(状态.parts)
    # ★ 两种布局的红线不同（2026-10-31 自己先踩了一次）：
    #   源码布局（<仓>/src/<包>）：状态根**故意**等于工具根 —— 不搬用户已有数据，
    #     所以这里只禁"落进 site-packages"。
    #   安装布局：状态根**必须**在工具树之外（工具树 = site-packages/<包>）。
    try:
        from . import profile as _p2
        源码布局 = (Path(_p2.PKG_DIR).parent.name == "src"
                    and (Path(_p2.PKG_DIR).parent.parent / "pyproject.toml").is_file())
    except Exception:                          # noqa: BLE001
        源码布局 = False
    if "site-packages" in 份 or "dist-packages" in 份:
        return 结论("N 状态目录可写", 未达标,
                    f"状态根落在安装目录里：{状态}",
                    "状态与代码必须分离：安装目录可只读/可被升级覆盖")
    if not 源码布局 and (状态 == 工具 or 工具 in 状态.parents):
        return 结论("N 状态目录可写", 未达标,
                    f"状态根落在安装树内：{状态}（工具根 {工具}）",
                    "状态与代码必须分离：安装目录可只读/可被升级覆盖")
    return 结论("N 状态目录可写", 通过,
                f"STATE={状态}；TOOL={工具}；可写"
                f"{'；源码布局（状态=工具根，保持旧行为）' if 源码布局 and 状态 == 工具 else '；与安装树分离'}"
                f"（env ORCH_STATE_DIR={os.environ.get('ORCH_STATE_DIR')!r}）")


def _跳过表(仓: Path | None) -> frozenset[str]:
    """枚举源码时要跳过的目录名：档案声明的 + 语言无关的构建/缓存目录。

    ★ 不剪枝的代价（实测）：本仓 2273 个 .rs + 682 个 .py，外加 target_*/备份/.tmp，
      `sorted(根.glob("**/*"))` 把整棵树物化 ⇒ D/F 两条判据各 >240 s 超时。
      档案里本来就写着 `source.skip`，这里把它接上。
    """
    基础 = {"target", "target_", "node_modules", ".git", "__pycache__",
            ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build",
            ".tmp", ".backup", ".backups", "备份", "backup",
            # ★ 2026-11-01（源树 D/F 超 180s 抓出）：**运行期产物 ≠ 源码**。
            #   本包把 staged 产物放在 `products/`、任务库/状态在 `.orchestrator-state/`、
            #   回执在 `.audit_trail/`、分发中间物在 `dist/`、工具链在 `.tools/`。
            #   不剪这些，一旦扫描面退回**仓根**（档案根在仓外时的正确行为），
            #   判据就会把几百个生成物当项目源码 —— 实测 D/F 各跑 >180s 判未知。
            "products", "outputs", "artifacts", "reports_result",
            ".orchestrator-state", ".orchestrator", ".audit_trail", ".data",
            "checks", "work", "jobs", "cache", ".tools", "receipts"}
    try:
        from . import profile as _p
        基础 |= set(_p.get_profile().source.skip or ())
    except Exception:                          # noqa: BLE001
        pass
    return frozenset(基础)


def _单条限时(thunk, 秒: float):
    """跑一条判据，超时就放弃线程（判据是只读探针，弃线程不丢状态）。"""
    盒: dict[str, object] = {}

    def 跑() -> None:
        try:
            盒["r"] = thunk()
        except BaseException as e:              # noqa: BLE001 —— 判据自己崩也要变成一条结论
            盒["e"] = e

    th = threading.Thread(target=跑, daemon=True, name="maturity-判据")
    th.start()
    th.join(秒)
    if th.is_alive():
        return None
    if "e" in 盒:
        e = 盒["e"]
        raise e if isinstance(e, BaseException) else RuntimeError(str(e))
    return 盒.get("r")


def _判据清单(仓: Path | None, 包目录: Path) -> list[tuple[str, object]]:
    return [
        ("A 失败域可分辨", lambda: 查通道()),
        ("B 读数保真", lambda: 查读数保真()),
        ("C 分类互斥", lambda: 查分类互斥(包目录)),
        ("D 成本 ∝ Δ", lambda: 查成本(仓)),
        ("E 平台静态依赖", lambda: 查平台依赖(包目录)),
        ("F 结构上下文可用", lambda: 查结构上下文(仓)),
        ("G 并发闸", lambda: 查并发闸(包目录)),
        ("H 副本隔离", lambda: 查副本隔离(包目录)),
        ("I 熔断跨进程", lambda: 查熔断持久(包目录)),
        ("J 跨语言结构记忆", lambda: 查跨语言记忆(包目录)),
        ("K 预算自适应", lambda: 查预算自适应(包目录)),
        ("L 双平台真跑", lambda: 查双平台(包目录)),
        ("M 仓改动可问责", lambda: 查仓改动(包目录)),
        ("N 状态目录可写", lambda: 查状态目录()),
    ]


def 全查(仓: Path | None = None, 包目录: Path | None = None,
        进度: bool = False, 单条上限: float | None = None,
        总预算: float | None = None) -> 记分卡:
    """跑全部判据。

    ★ 2026-10-31：加**逐条进度**与**单条上限**。此前 `main()` 是全跑完才 print，
      在真实大仓上表现为"命令静默跑一小时、看不出卡在哪" —— 交付验收不该长这样。

    ★★ 2026-11-01（安装态实测抓出）：只有**单条**上限，没有**总**预算 ⇒ 最坏
      14 条 × 600 s ≈ 2.3 小时；从外面看与"卡死"不可区分（实测进程 CPU 93 s 后
      仍无结论）。现在加总预算：跑满就**停手并如实标注"未测"**，绝不把"没测"写成"通过"。
      两条上限都可调：`ORCH_MATURITY_CHECK_TIMEOUT`（单条，默认 180）、
      `ORCH_MATURITY_TOTAL_BUDGET`（总，默认 600）。
    """
    包 = 包目录 or Path.cwd()
    上限 = 单条上限 if 单条上限 is not None else float(
        os.environ.get("ORCH_MATURITY_CHECK_TIMEOUT", "180"))
    总 = 总预算 if 总预算 is not None else float(
        os.environ.get("ORCH_MATURITY_TOTAL_BUDGET", "600"))
    卡 = 记分卡()
    清单 = _判据清单(仓, 包)
    t_全 = time.time()
    for i, (名, thunk) in enumerate(清单, 1):
        剩 = 总 - (time.time() - t_全)
        if 剩 <= 1.0:
            卡.加(结论(名, 未知,
                      f"总预算 {总:.0f}s 已用完 ⇒ 本条**未测**（已测 {i - 1}/{len(清单)} 条）；"
                      f"调 ORCH_MATURITY_TOTAL_BUDGET 可放宽",
                      "预算截断：既不是通过，也不是判据失败"))
            if 进度:
                print(f"[{i}/{len(清单)}] {名} … 跳过（总预算用完）", file=sys.stderr, flush=True)
            continue
        本限 = min(上限, 剩)
        t0 = time.time()
        if 进度:
            print(f"[{i}/{len(清单)}] {名} …（上限 {本限:.0f}s）", file=sys.stderr, flush=True)
        try:
            结 = _单条限时(thunk, 本限)
        except Exception as e:                 # noqa: BLE001
            结 = 结论(名, 未达标, f"判据自身抛异常：{type(e).__name__}: {e}",
                      "这是判据实现的缺陷，不是被测对象的问题")
        if 结 is None:
            结 = 结论(名, 未知,
                      f"超过单条上限 {本限:.0f}s 未完成（仓过大或该判据未剪枝）；"
                      f"调 ORCH_MATURITY_CHECK_TIMEOUT/ORCH_MATURITY_TOTAL_BUDGET 可放宽",
                      "给该判据接入档案 source.skip 剪枝，或对大仓只用子仓跑")
        卡.加(结)
        if 进度:
            print(f"       → {结.状态}（{time.time() - t0:.1f}s）", file=sys.stderr, flush=True)
    return 卡


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="编排器完备性机械口径")
    ap.add_argument("--repo", default=os.environ.get("ORCH_MATURITY_REPO", ""),
                    help="用于实测增量与上下文的仓根（缺省=当前目录）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="不打印逐条进度（进度走 stderr）")
    ap.add_argument("--check-timeout", type=float,
                    default=float(os.environ.get("ORCH_MATURITY_CHECK_TIMEOUT", "180")),
                    help="单条判据上限秒数（默认 180，可用 ORCH_MATURITY_CHECK_TIMEOUT）")
    ap.add_argument("--total-budget", type=float,
                    default=float(os.environ.get("ORCH_MATURITY_TOTAL_BUDGET", "600")),
                    help="全部判据的总预算秒数（默认 600；跑满即停手并标『未测』，"
                         "可用 ORCH_MATURITY_TOTAL_BUDGET）")
    a = ap.parse_args(argv)
    仓 = Path(a.repo or Path.cwd())
    包目录 = Path(__file__).resolve().parent.parent
    卡 = 全查(仓, 包目录, 进度=not a.quiet,
             单条上限=a.check_timeout, 总预算=a.total_budget)
    if a.json:
        print(json.dumps([c.__dict__ for c in 卡.项], ensure_ascii=False, indent=1))
    else:
        print(卡.渲染())
    return 卡.码()


if __name__ == "__main__":
    raise SystemExit(main())
