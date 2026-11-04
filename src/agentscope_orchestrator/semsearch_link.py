r"""semsearch 外挂知识载体的**只读桥**（`semsearch_link.py`）—— 把项目里已索引的知识接进修复环。

## 这个模块存在的理由（先说清边界，别把它当万灵药）

`semsearch`（Rust，所在目录见 `二进制()`）本身是**上游项目的**知识载体：
389 条已索引概念，八维定长编码（层级/G-XX/AUD/操作/知识等级/依赖深度/PR段/偏移），
每条带 `PR编号 + 定义原文 + 追溯链 + 文档路径:行号`。它能：

- `search --keyword/--concept/--pr/--dim --value` 按语义轴取条目（带追溯链）；
- `search --dir <目录> --pattern <正则> --recursive` 在**任意目录**做带行号的检索（实测 1.3s/次）；
- `index --add` 写入（★ 实测被两件事挡住：PR 编号**必须是 PR-XXX**；写入目标索引**不受 cwd 控制**
  —— 临时目录里放空索引，`index --stats` 仍报 389 条 ⇒ 多项目会互相污染）。

⇒ 所以本桥**只做只读检索**，不假装能写多项目索引。写入真相仍归 `project_kb`（人写的 Markdown）。

## 它回答的那个问题

"这个文件夹里的机制能不能做成外部知识载体，提供全局项目记忆配合编排器提升模型能力？"

- **能**的部分：检索（含跨目录、带行号、带追溯链）⇒ 这就是"全局项目记忆"的读取面，今天就能接；
- **不能（现状）**的部分：多项目隔离、自定义 id 体系、维度轴参数化 —— 要改 Rust 三处才通用；
- 本桥据此只接**读**，并在 `能力()` 里把"写不进去"如实回报，避免调用方以为能写。

## 用法

```python
from agentscope_orchestrator import semsearch_link as S
S.可用()                      # 二进制在不在、能不能跑
S.条目(关键词="判据门")        # 语义条目（带 PR/追溯/文档行号）
S.目录命中(目录="src", 正则="判据门")   # file:line 命中
S.注入(关键词="判据门")        # 带预算的提示块（无命中返回 ""）
```

## 诚实边界

- 检索是**字面/维度**级的，不是向量语义；同义词查不到就是查不到（不做相似度伪造）；
- 二进制缺失、超时、输出解析失败 ⇒ 一律返回空并给原因（`能力()` 可查），**绝不编造命中**；
- 二进制位置**不写死**（本模块第一版写死了某个项目的绝对路径，被 `tests/phase_separation.py` 的 A1
  当场判红）：环境变量 `SEMSEARCH_BIN` 优先，其次 PATH 上的 `semsearch`，最后才是候选表（默认为空）。
- 只读：不调用 `index --add` / `update` / `fix` / `workflow`（会写别人的审计链）。
"""
from __future__ import annotations

import json
import os
import re
import shutil as _shutil
import subprocess
from pathlib import Path

默认二进制候选: tuple[str, ...] = ()      # 见 `二进制()`：只认环境变量与 PATH，不写死绝对路径

_行RE = re.compile(r"^(?P<文件>.+?):(?P<行>\d+)\s+(?P<文>.*)$")
_块分隔 = "---"


def _配置文件() -> dict:
    """读**部署侧**的检索桥配置。

    查找顺序（先找到先用）：
      ① `SEMSEARCH_LINK_CONFIG` 指定的文件；
      ② **从 cwd 向上**找 `semsearch_link.json`（部署目录常是 cwd 的父辈 ⇒ 一处配好，
         在该部署目录下跑任何东西都认）；
      ③ 包目录内的 `semsearch_link.json`（随包发的一份，留空也行）。

    ★ 为什么要有它（B 方案落地的关键）：知识库该"一处维护、多处引用"，
      但"引用哪一份"是**部署机的属性**，不该写进产品源码（会污染判据
      `phase_separation.py`：产品源码里不许出现任何项目路径），也不该塞进项目档案
      （`orchestrator.profile.yml` 是**项目**的，不是**部署**的）。
      所以：配置文件放部署目录，键名与二进制认的环境变量同名，一眼能对上。

    能配的键（都可不填）：
      `bin` 二进制位置 · `project_root` 知识库所在项目根 · `index` 索引文件 ·
      `schema` 口径文件 · `library_mode` 是否库模式（默认 true）
    """
    候选: list[Path] = []
    指定 = os.environ.get("SEMSEARCH_LINK_CONFIG")
    if 指定:
        候选.append(Path(指定))
    try:
        现 = Path.cwd().resolve()
        for 层 in (现, *现.parents[:4]):
            候选.append(层 / "semsearch_link.json")
    except OSError:
        pass
    候选.append(Path(__file__).resolve().parent / "semsearch_link.json")
    for c in 候选:
        try:
            if c.is_file():
                d = json.loads(c.read_text(encoding="utf-8-sig"))
                return d if isinstance(d, dict) else {}
        except (OSError, json.JSONDecodeError):
            continue          # 配置坏了不该拖死检索：当作没配
    return {}


def _库模式环境(项目根: str | Path | None = None, 索引: str | Path | None = None,
                口径: str | Path | None = None) -> dict:
    """构造"把它当**通用知识库**用"的环境变量。

    ★ 2026-11-03：semsearch 侧已改成通用形态（库里那三个硬门槛解开了），本桥据此把
      三个开关透出去，调用方不必知道里面的细节：

      · `SEMSEARCH_LIBRARY_MODE=1` —— **不做项目记账**（任务书/回执/快照）。
        不开它，在外部仓库里跑会直接 exit(1)「任务书缺少项目名称」（实测），
        因为记账流程以前是**每次调用都跑**的；开了它就只做检索与编码。
      · `SEMSEARCH_INDEX=<path>` —— 索引写哪读哪。不开它在外部项目会回落到
        `~/.semsearch/index/concepts.json` ⇒ **多项目共用一个文件**。
      · `SEMSEARCH_SCHEMA=<path>` —— 口径文件（id 正则 + 等级标签 + 八维轴名）。
      · `SEMSEARCH_PROJECT_ROOT=<dir>` —— 项目根，交给 semsearch 定位用。

    ★ 参数优先级：**显式参数 > 配置文件 > 当前环境**。三者都不给就什么都不设，
      交给二进制自己探测（在本项目里就是它自己那份索引）。
    """
    环境 = dict(os.environ)
    配置 = _配置文件()
    环境["SEMSEARCH_LIBRARY_MODE"] = "1"          # 库模式是本桥的**默认**：我们是借它当知识库
    根 = 项目根 if 项目根 is not None else 配置.get("project_root")
    索 = 索引 if 索引 is not None else 配置.get("index")
    径 = 口径 if 口径 is not None else 配置.get("schema")
    if 根 is not None:
        环境["SEMSEARCH_PROJECT_ROOT"] = str(根)
    if 索 is not None:
        环境["SEMSEARCH_INDEX"] = str(索)
    if 径 is not None:
        环境["SEMSEARCH_SCHEMA"] = str(径)
    return 环境


def 二进制() -> Path | None:
    """解析二进制位置：**部署配置 → 环境变量 → PATH → 候选表**。

    ★ 顺序是**实测定的**，不是随手排的（2026-11-03）：这台机器的 PATH 里有一个旧版
      `semsearch/.semsearch_bin/semsearch.EXE`，它**没有**通用形态（还是会去写
      `E:\\基础设施\\.audit_trail\\`，rc=1「拒绝访问」）。若 PATH 优先于显式配置，
      就会出现"部署配置写了新二进制、实际仍在跑旧二进制"——查半天查不出来的那种。
      显式配置是**部署机的意图**，意图必须压过环境里的偶然。

    ★ 环境变量 `SEMSEARCH_BIN` 仍排在配置之前：它是**当次调用**的显式指定，
      比"这台机器默认用哪个"更具体。产品源码里不写死任何项目路径。
    """
    环境 = os.environ.get("SEMSEARCH_BIN")
    if 环境 and Path(环境).is_file():
        return Path(环境)
    配的 = _配置文件().get("bin")
    if 配的 and Path(配的).is_file():
        return Path(配的)
    在PATH = _shutil.which("semsearch") or _shutil.which("semsearch.exe")
    if 在PATH:
        return Path(在PATH)
    for c in 默认二进制候选:
        if Path(c).is_file():
            return Path(c)
    return None


def _默认环境() -> dict:
    """普通调用（不显式指项目）用哪套环境。

    ★ 这里的分支逻辑是**实测逼出来的**，别简化掉：

      · 从编排器目录调用、且没有任何项目标记时，二进制会先跑"任务书检测/生成"那套
        项目记账，探测不到就 `exit(1)`「任务书缺少项目名称」——它不是没能力，
        是**记账流程挡住了检索**（2026-11-03 实测）。
      · 但也不能无条件开库模式：那会让它在**本仓所在的那个项目**里也走"没有项目根"的分支，
        普通 `search --pr PR-001` 就查不到那批已索引条目（同样实测过）。

      所以优先级是：**环境变量（当次显式指定）> 部署配置 > 往上认项目根 > 原样交给它自己探测。**

    ★ 环境变量排在配置之前是**判据逼出来的**（2026-11-03）：`tests/semsearch_link.py`
      为了不靠 cwd 猜，自己设了 `SEMSEARCH_PROJECT_ROOT`；若配置压过它，判据就会被
      部署目录里那份配置"劫持"，从"验过"静默降级成"跳过"——而跳过看起来像绿。
      规则：**谁更具体谁优先**，当次调用 > 这台机器的默认。
    """
    环境 = dict(os.environ)
    if any(环境.get(k) for k in ("SEMSEARCH_PROJECT_ROOT", "SEMSEARCH_INDEX", "SEMSEARCH_SCHEMA")):
        return 环境                       # 当次调用已经说清楚了，别覆盖
    if _配置文件():
        return _库模式环境()              # B 方案：部署机显式指向那份共享知识库
    现 = Path.cwd()
    for 候选 in (现, *现.parents[:3]):
        if (候选 / ".semsearch_index").is_dir() or (候选 / ".audit_trail").is_dir():
            环境["SEMSEARCH_PROJECT_ROOT"] = str(候选)
            环境["SEMSEARCH_LIBRARY_MODE"] = "1"
            return 环境
    return 环境


def _跑(参数: list[str], 超时: float = 20.0, 环境: dict | None = None,
       cwd: str | Path | None = None) -> tuple[bool, str, str]:
    二 = 二进制()
    if 二 is None:
        return False, "", "找不到 semsearch 二进制（设 SEMSEARCH_BIN 指过去）"
    try:
        p = subprocess.run([str(二), *参数], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=超时,
                           env=环境 if 环境 is not None else _默认环境(),
                           cwd=str(cwd) if cwd else None)
    except subprocess.TimeoutExpired:
        return False, "", f"超时（>{超时}s）"
    except OSError as exc:
        return False, "", f"起不来：{type(exc).__name__}: {exc}"
    出 = (p.stdout or "") + (p.stderr or "")
    # 二进制每次都往 stderr 打"定理索引加载器…"，它不是结果，抹掉免得污染解析
    出 = "\n".join(行 for 行 in 出.splitlines()
                   if not 行.startswith("定理索引加载器"))
    return p.returncode == 0, 出.strip(), ""


def 可用() -> dict:
    """能力探测：**能读什么、不能写什么**，一次说清（供 guide/调用方判断）。"""
    二 = 二进制()
    if 二 is None:
        return {"可用": False, "原因": "找不到二进制（设 SEMSEARCH_BIN）"}
    ok, 出, 错 = _跑(["index", "--stats"])
    if not ok:
        # ★ 两种失败要分开说：二进制起不来 vs 起来了但索引读不到（工作目录不在项目里时，
        #   它自己会去找 `.audit_trail/` 并尝试写任务书 ⇒ 那是**环境不对**，不是产品缺能力）。
        return {"可用": False, "二进制": str(二),
                "原因": 错 or f"索引读不到（工作目录可能不在该知识库所属项目内）：{出[:160]}"}
    m = re.search(r"(?:总条目数|total_entries)[=：]\s*(\d+)", 出)
    return {"可用": True, "二进制": str(二), "索引条目": int(m.group(1)) if m else None,
            "能读": ["语义条目(--keyword/--concept/--pr/--dim)", "目录正则(--dir --pattern --recursive)",
                     "写入由调用方显式指项目时可用（SEMSEARCH_PROJECT_ROOT + 项目自己的 semsearch.schema.json）"],
            "写边界": "默认不写别人的项目记账（库模式）；每项目一份索引要显式给 SEMSEARCH_INDEX 或口径里的 index_path"}


def _取首个JSON(文: str):
    """从输出里取**开头那个** JSON 值，忽略其后的散文尾巴。

    ★ 实测（2026-11-03）：`search --pr PR-001 --format json` 打印完 JSON 后还会追加一段
      阶段播报（「当前阶段：InfoGathering…」「执行项状态：…」），于是 `json.loads(整段)`
      直接 `Extra data: line 63` 失败 ⇒ 解析层把**查到了**当成**查不到**返回空表。
      这种"数据后面跟着人读的话"在 CLI 里很常见，解析器必须容得下。
    """
    s = 文.lstrip()
    if not s or s[0] not in "[{":
        return None
    try:
        return json.JSONDecoder().raw_decode(s)[0]
    except json.JSONDecodeError:
        return None


def 条目(关键词: str = "", 概念: str = "", pr: str = "", 维度: int | None = None,
        值: int | None = None, 超时: float = 20.0) -> list[dict]:
    """语义检索 → 结构化条目 [{pr, 名称, 编码, 维度, 定义, 追溯}]。查不到就是空列表。"""
    参数 = ["search"]
    if 概念:
        参数 += ["--concept", 概念]
    elif pr:
        参数 += ["--pr", pr]
    elif 关键词:
        参数 += ["--keyword", 关键词]
    elif 维度 is not None and 值 is not None:
        参数 += ["--dim", str(维度), "--value", str(值)]
    else:
        return []
    # ★ 被实测纠正的两处（别再"顺手加参数"）：
    #   · `--trace` 只对 `--pr` 有效，配 `--keyword/--concept` 会直接报
    #     「--trace 需要配合 --pr 使用」⇒ 整条检索白跑（首版就这样返回 0 条）；
    #   · `--format json` 只在 `--pr` 分支生效，keyword/concept/dim 分支**忽略它**仍输出文本。
    # ★ `--trace` 的 pr 分支输出的是**追溯证据包（文本）**，不是 JSON ⇒ 别把两者一起加。
    参数 += ["--format", "json"] if pr else []
    ok, 出, 错 = _跑(参数, 超时)
    if not ok or not 出:
        return []
    # JSON 分支：**用 raw_decode 取开头那个值**（后面常跟着阶段播报的散文）
    解 = _取首个JSON(出)
    if isinstance(解, list):
        return 解
    if isinstance(解, dict):
        return [解]                       # ★ `--pr` 分支输出的是**单个对象**，不是数组
    return _解析文本(出)
    return _解析文本(出)


def _解析文本(出: str) -> list[dict]:
    条们: list[dict] = []
    当前: dict = {}
    for 行 in 出.splitlines():
        行 = 行.rstrip()
        if not 行 or 行.startswith("未找到匹配条目") or 行.startswith("dim"):
            continue
        if 行.strip() == _块分隔 or 行.startswith("PR编号："):
            if 当前.get("pr"):
                条们.append(当前)
            当前 = {}
            if 行.startswith("PR编号："):
                当前["pr"] = 行.split("：", 1)[1].strip()
            continue
        for 键, 名 in (("概念名称：", "名称"), ("编码：", "编码"), ("定义原文：", "定义"),
                       ("追溯链：", "追溯"), ("dim0·层级：", "层级"), ("dim4·知识等级：", "知识等级")):
            if 行.strip().startswith(键):
                当前[名] = 行.split("：", 1)[1].strip()
                break
    if 当前.get("pr"):
        条们.append(当前)
    return 条们


def 目录命中(目录: str, 正则: str, 超时: float = 30.0, 上限: int = 20) -> list[dict]:
    """在**任意目录**做带行号的正则检索（这是"全局项目记忆"里最稳的一条能力）。"""
    if not Path(目录).is_dir():
        return []
    ok, 出, 错 = _跑(["search", "--dir", str(目录), "--pattern", 正则, "--recursive"], 超时)
    if not ok:
        return []
    中 = []
    for 行 in 出.splitlines():
        m = _行RE.match(行.strip())
        if m and len(中) < 上限:
            中.append({"文件": m.group("文件"), "行": int(m.group("行")), "文": m.group("文")[:200]})
    return 中


_注入缓存: dict[str, str] = {}
_注入缓存上限 = 32


def 注入(关键词: str, 目录: str = "", 上限字: int = 500, 超时: float = 8.0) -> str:
    """生成提示块：**带来源编号**，预算内截断；一条都没查到时返回空串。

    ★ 性能与**不可阻塞**约束（2026-11-03 实测踩过）：

      · 本函数被 `fixloop` 每轮调用一次，所以它**绝不能拖慢或卡住修复主链**；
      · 首版默认超时 20s，且只要传了 `目录` 就再跑一次递归目录扫描（`--recursive`），
        在 CI 里把 `mcp_smoke` 顶到 900s 超时被杀 —— 那是"知识注入把主链拖死"；
      · 现在的口径：**注入只做条目检索**（语义轴，快），递归目录扫描留给调用方
        显式用 `目录命中()`；再加一个按关键词的小缓存，同一轮里不重复起进程。
    """
    键 = f"{关键词}|{上限字}"
    if 键 in _注入缓存:
        return _注入缓存[键]
    片: list[str] = []
    预算 = 上限字
    for t in 条目(关键词=关键词, 超时=超时)[:6]:
        名 = t.get("名称") or t.get("concept_name") or "?"
        定义 = (t.get("定义") or t.get("definition") or "").strip().replace("\n", " ")
        追溯 = (t.get("追溯") or t.get("trace_chain") or "").strip()
        pr = t.get("pr") or t.get("pr_id") or "?"
        行 = f"- [{pr}] {名}：{定义[:160]}" + (f"（追溯：{追溯[:60]}）" if 追溯 else "")
        if len(行) > 预算:
            break
        片.append(行)
        预算 -= len(行)
    if 目录:
        # 显式给了目录才扫，且**只扫一次、限时**（调用方要控制成本就少传目录）
        for h in 目录命中(目录, 关键词, 超时=超时, 上限=4):
            行 = f"- {Path(h['文件']).name}:{h['行']} {h['文'][:110]}"
            if len(行) > 预算:
                break
            片.append(行)
            预算 -= len(行)
    出 = "" if not 片 else ("## 项目知识库（semsearch 检索命中，带来源；与你的常识冲突时按这里做）\n"
                            + "\n".join(片))
    if len(_注入缓存) >= _注入缓存上限:
        _注入缓存.clear()
    _注入缓存[键] = 出
    return 出


def 统计() -> dict:
    return 可用()
