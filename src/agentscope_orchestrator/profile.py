"""项目档案（Profile）：编排器【唯一】的项目知识入口。

═══════════════════════════════════════════════════════════════════════
纪律（工具与项目分离）——本文件是这条纪律的落形处
═══════════════════════════════════════════════════════════════════════
编排器是**通用自动化开发工具**，不隶属任何项目。因此：

  · 本模块属【工具】侧，**不含任何具体项目的名称 / 路径 / 语言 / 命令**。
    工具源码里搜不到项目名，是本纪律的可检验形式。
  · 一切项目专有信息由**外部档案文件**提供（默认 `config/profile.yml`）。
  · 未提供档案时回落到【通用空档案】：仓库根 = 进程工作目录，无判据门。
    此时工具仍可启动 / 建索引 / 派活 / 出回执，只是"不知道该跑什么门"——
    显式告警而非静默假装通过（**绝不用恒真占位门冒充判据**）。

⇒ 换项目 = 换一个档案文件，**代码零改动**；删掉档案 = 工具退化为通用空壳。
  这就是"开发工具是开发工具，项目是项目"。

档案查找顺序（首个命中者生效）：
  1. 显式传入 path（`--profile` / 调用方）
  2. 环境变量 `ORCH_PROFILE`
  3. `config/profile.yml`（工具自己的配置目录）
  4. 通用空档案（兜底，告警）

档案内相对路径**相对档案文件所在目录**解析，故档案可放任意位置。
"""
from __future__ import annotations

import re

def _mcp_合规224(名: str) -> str:
    """把任意字符串压成 MCP 允许的字符集（`A-Z a-z 0-9 _ - .`）。

    ★ 非 ASCII 一律折叠成 `_`；连续 `_` 合并、首尾 `_` 去掉。
      中文语义**不进名字**（进 description），故折叠不丢信息。
    """
    _s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(名 or ""))
    _s = re.sub(r"_{2,}", "_", _s)
    return _s.strip("_")

import json
import hashlib
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

logger = logging.getLogger("profile")

# ══════════════════════════════════════════════════════════════════
# 两个根：**代码根**（只读资产）与**状态根**（可变数据）——必须分开
# ══════════════════════════════════════════════════════════════════
# ★★★ 真事故（2026-10-31 交付审计，P0）：
#   原来是 `TOOL_ROOT = Path(__file__).resolve().parents[2]`。
#   源码树布局 `<包>/src/agentscope_orchestrator/profile.py` ⇒ parents[2] = `<包>` ✅
#   wheel 布局 `site-packages/agentscope_orchestrator/profile.py` ⇒ parents[2] = **`<venv>/Lib`** ❌
#   ⇒ 于是 `jobs/jobs.db`、`成本账.jsonl`、`熔断状态.json`、`products/` 全落在**解释器目录**里。
#   实测后果：在"安装目录不可写"的机器上（AppContainer/服务账号/Program Files/本项目现场的 ACL），
#   主入口 `orch_run` **每次都** `sqlite3.OperationalError: attempt to write a readonly database`，
#   而只读工具（guide/list_tasks）全绿 —— 最容易被冒烟测试放过去的那一类失败。
#   ⇒ 纪律：**代码根可以在安装目录里，状态根永远不可以。**
PKG_DIR = Path(__file__).resolve().parent          # 包代码目录（安装后 = site-packages/<包>）


def _是源码布局() -> bool:
    """包是否躺在 `<仓>/src/` 下（有同级 pyproject.toml 才算）。"""
    src = PKG_DIR.parent
    return src.name == "src" and (src.parent / "pyproject.toml").is_file()


def _detect_tool_root() -> Path:
    """代码根：源码布局返回包仓根；安装布局返回包目录自身。

    只用于**只读**资产（`config/`、`src/`、`tests/`、自带档案）。
    """
    return PKG_DIR.parent.parent if _是源码布局() else PKG_DIR


def _可写(p: Path) -> bool:
    """真演示一次可写（建目录 + 写探针 + 删）。**不靠 os.access 猜**。

    ★ 实测教训（2026-10-31）：本机 python 进程在 `%LOCALAPPDATA%`、某些磁盘目录
      建目录直接 `WinError 5 拒绝访问`，而同 token 的 cmd/powershell 却能建。
      `os.access` 在这种环境下返回 True（它只看 ACL 位），但真写就炸 ——
      ⇒ 默认落点必须**试写**才知道能不能用。
    """
    try:
        Path(p).mkdir(parents=True, exist_ok=True)
        探 = Path(p) / f".写探针.{os.getpid()}"
        探.write_text("ok", encoding="utf-8")
        探.unlink()
        return True
    except OSError:
        return False


def _候选状态根() -> list[Path]:
    """状态根候选，按优先级。**不包含安装目录**（那是 P0 事故本身）。

    顺序设计的理由（都是实测出来的，不是想出来的）：
      ① 显式 env（`ORCH_STATE_DIR`/`ORCH_HOME`）：运维能一句话定死，最强；
      ② 源码树工具仓根：保持旧行为，不搬用户已有数据；
      ③ 平台数据目录 → ④ 家目录 → ⑤ TEMP：标准位置，正常机器到此为止；
      ⑥ **仅当上面全不可写**才退到"当前工作区"下的 `.orchestrator-state`；
      ⑦ 兜底也**不是**安装目录：返回家目录下的名字，让上层可写断言**响亮地失败**。

    ★ 为什么 cwd 必须排到后面（2026-10-31 实测事故）：MCP 服务器的 cwd 由宿主决定
      （本项目部署档案里是 `E:/基础设施/orch-deploy`），而命令行/测试进程的 cwd 是项目根。
      把 cwd 放在前面 ⇒ **同一个用户在两个进程里算出两个不同的状态根** ⇒
      "我这刚跑的任务，历史里查不到"。状态根必须是**跨进程稳定**的同一处。
    """
    候: list[Path] = []
    for k in ("ORCH_STATE_DIR", "ORCH_HOME"):
        v = os.environ.get(k)
        if v:
            try:
                候.append(Path(v).expanduser().resolve())
            except OSError:
                logger.warning("[profile] %s 不是合法路径：%s（忽略）", k, v)
    if _是源码布局():
        候.append(PKG_DIR.parent.parent)          # 源码树：保持旧行为，数据留在工具仓
    if os.name == "nt":
        本 = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        基 = Path(本) if 本 else (Path.home() / "AppData" / "Local")
    else:
        基 = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    候.append(基 / "agentscope-orchestrator")
    候.append(Path.home() / ".agentscope-orchestrator")
    _临时 = os.environ.get("TEMP") or os.environ.get("TMPDIR") or os.environ.get("TMP")
    if _临时:
        候.append(Path(_临时) / "agentscope-orchestrator")
    try:
        候.append(Path.cwd() / ".orchestrator-state")   # 只作最后手段（cwd 不稳定）
    except OSError:
        pass
    去重: list[Path] = []
    for p in 候:
        if p not in 去重 and "site-packages" not in p.parts and "dist-packages" not in p.parts:
            去重.append(p)
    return 去重


def _detect_state_root() -> Path:
    """状态根：任务库 / 成本账 / 熔断状态 / 产物。**绝不在安装目录内，且必须真可写。**

    顺序见 `_候选状态根()`。**每个候选都试写**，第一个写进去的胜。
    全部不可写时返回第一项（让上层可写断言给出可执行错误），而不是偷偷塞回安装目录。
    """
    候 = _候选状态根()
    for p in 候:
        if _可写(p):
            return p
    logger.warning("[profile] 全部状态根候选都不可写：%s ⇒ 请设 ORCH_STATE_DIR 指向可写目录",
                   [str(x) for x in 候])
    return 候[0]


# ★★★★★ R549：**状态根的唯一取法**。
#
# 为什么要有这个函数（同一件事已经犯过三次）：
#   `project_kb.库根()` 曾经自己算 `Path.home()/".orchestrator-state"`，
#   与 `STATE_ROOT`（真试写选出来的）**不是同一处** —— 本机那个路径
#   `WinError 5 拒绝访问` ⇒ 播种必死 ⇒ doctor 报「知识库 0 条」，
#   看起来像「这项目本来没知识」（R547）。
#   同类代码在 `coord_chain` / `blind_spot` / `events` / `memory8` 里还有四份。
#   ⇒ 症状永远一样：**写不了的根被显示成「空的」**，而责任完全不同。
#
# 口径：任何要落盘的地方**只调这一个函数**，不许再自己拼路径。
#   它保证：显式 env 优先；否则就是 `_detect_state_root()` 逐个候选**真试写**
#   选出来的那个（会退到 TEMP 兜底，不会退到不可写的家目录）。
def state_dir() -> Path:
    """状态根（任务库/成本账/知识库/坐标链/盲点台账/事件检查点 **全在这一个根下**）。"""
    _v = os.environ.get("ORCH_STATE_DIR") or ""
    return Path(_v) if _v else Path(STATE_ROOT)


def 尽力建目录(路径, 名: str = "") -> bool:
    """**导入期**尽量把目录准备好；建不出来只 warning，**不许让导入失败**。返回是否成功。

    ★★★★★ R543 / R543b（2026-11-20 部署 venv 真跑）：`orch-check ③` 崩在
      `PermissionError: [WinError 5] … <部署目录>/.orchestrator`，完整栈指向
      `generic_goal.py` 的**模块级** `AUDIT.mkdir(...)`：
      只要有一处不可写（本机：部署目录对 python 子进程不可写，而档案没找到时
      `audit_dir` 落到 `<cwd>/.orchestrator`）⇒ **`import agentscope_orchestrator`
      直接抛 PermissionError** ⇒ `taixu_mcp` 导入失败 ⇒ **MCP 服务器起不来、自检崩**。

    ★ 口径：**导入期只做「尽量准备」，不做「必须成功」**。
      目录建不出来时，应该在该落盘的那一刻响亮地失败并说清修法；
      而不是让导入本身抛错 —— 那样连"报错说清原因"的机会都没有，
      用户只看到「自检本身跑不起来」，无法据此判断产品好坏（崩溃 ≠ 产品判定）。

    ★ **不吞掉**：warning 里明写哪个目录、为什么、怎么改。
    ★ 为什么不只在 `generic_goal` 里修：这条线是**普适**的 —— 机判扫描
      （`tools/导入期写盘扫描.py`）在 `doc_store` / `gate_memory` / `taixu_mcp`
      里各查出 1 处同族病。**按类别扫干净，不是按报错修一处。**
    """
    try:
        Path(路径).mkdir(parents=True, exist_ok=True)
        return True
    except Exception as _e:                  # noqa: BLE001 - 见上面口径
        logger.warning(
            "[profile] %s 目录建不出来（%s: %s）：%s ⇒ 真正落盘时会失败；"
            "修法：把 ORCH_STATE_DIR 指到可写目录，或在档案里显式声明它。"
            "导入**继续**（导入期不承担可写性）。",
            名 or "状态", type(_e).__name__, _e, 路径)
        return False


TOOL_ROOT = _detect_tool_root()
STATE_ROOT = _detect_state_root()


# ═══════════════════════════════════════════════════════════════════
# 子规格
# ═══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ArtifactSpec:
    """产物库规格：编排器把收敛产物写回哪里、按什么键索引。

    空 root ⇒ 本档案无产物库，promote 一律拒绝（不猜路径、不写库外）。
    """
    root: Path | None = None
    glob: str = "**/*.lean"
    key_prefix: str = ""          # 点式键前缀（如顶层命名空间名）；空 = 不加前缀
    lang: str = ""                # 语言标签（信息性；门由 gates 决定）
    exclude_disabled: bool = True
    disabled_marker: str = "_disabled"

    @property
    def enabled(self) -> bool:
        return self.root is not None


@dataclass(frozen=True)
class SourceSpec:
    """源码索引规格：扫哪些目录、按什么后缀、跳过什么。"""
    roots: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    skip: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AnchorSpec:
    """锚点：跑工程级门时锚定哪个单元；核心单元仅信息性。"""
    main: str = ""
    core: tuple[str, ...] = ()


@dataclass(frozen=True)
class MechanismSpec:
    """机制族格来源：从哪些库抽机制族（项目专有）。"""
    roots: tuple[str, ...] = ()
    skip_hints: tuple[str, ...] = ()
    non_mechanism: tuple[str, ...] = ()
    orch_relevant: tuple[str, ...] = ()


# ═══════════════════════════════════════════════════════════════════
# 档案
# ═══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Profile:
    name: str
    repo_root: Path
    audit_dir: Path
    work_dir: Path
    products_dir: Path
    receipt_prefix: str
    mcp_name: str
    console_title: str
    # MCP 工具名前缀（工具名 = f"{tool_prefix}·提交任务" 等）。空 = 不带前缀。
    tool_prefix: str
    artifact: ArtifactSpec
    source: SourceSpec
    anchors: AnchorSpec
    gates: tuple[dict, ...]
    dev_gates: tuple[dict, ...]
    objective_gates: tuple[str, ...]
    prompts: dict
    default_task: dict
    mechanism: MechanismSpec
    # ★★★★★ R23：**冻结区** —— 判据不得由被判方改动。
    #   验收测试常与实现**同文件**（本次真跑：`src/lib.rs` 的 `#[cfg(test)] mod tests`）
    #   ⇒ 模型可以改测试让自己过。`frozen` 声明需**逐字保持不变**的区域，
    #   编排器在任务开始时快照、每轮判门后与交付前各比一次，变了即判红。
    #   用**起止标记**（语言无关文本），不用文件名也不用语言语法。
    frozen: tuple[dict, ...] = ()
    # ★★★★★ R85：**接口命名约定** —— 项目自己声明「哪些前缀是对外承诺的接口」。
    #   编排器据此做**结构扫描**：这些前缀的 `pub fn` 若**测试之外零引用**，
    #   就是「声明了但没接线」的结构缺口（不靠人写标记，机器算出来）。
    #   ★ 机制通用、参数由项目给 ⇒ 不把某个项目的词表写进通用工具。
    orphan_api_prefixes: tuple[str, ...] = ()
    # ★★★★★ R230：**记忆索引范围** —— 项目自己声明「哪些文件算项目资产」。
    #
    #   为什么必须是项目声明（不是工具猜）：2026-11-04 真流程取证 ——
    #   记忆索引原来按 `TOOL_ROOT/src` 找代码根，而 `TOOL_ROOT` 是**工具的
    #   安装位置**。装成包跑时那个目录不存在，于是 `auto_scan` 只回
    #   `{"kinds": {"formal": 38238}}`：**只有形式化产物一类**，项目本体的
    #   Rust / Python / 文档一份没进。⇒ 换任何项目、任何安装方式都是断的。
    #
    #   条目形状（每项一个 dict）：
    #     kind:     索引到哪一类（如 `rs` / `py` / `doc`）
    #     roots:    相对**仓库根**的目录（`.` = 仓库根）
    #     globs:    glob；含 `**` 的递归，否则只扫那一层
    #     skip:     额外要剪掉的目录名（**前缀**匹配）
    #     require:  可选，文件头部必须命中的关键词（任务书一类用）
    #   ★ 空 tuple ⇒ 本档案没声明 ⇒ `auto_scan` 会**明确告警**，不静默。
    memory: tuple[dict, ...] = ()
    source_path: Path | None = None
    raw: dict = field(default_factory=dict)
    # ★ R231：装载期校验不通过的门（未知 type 等）**不放 `gates`**，单独记着。
    #   `gates` 里只有能真跑的门 ⇒ 结论不会建立在"没跑的门"上。
    #   非空即表示档案有问题：装载期已告警，调用方也可以据此不认"达标"。
    gates_invalid: tuple[dict, ...] = ()

    # ── 派生 ────────────────────────────────────────────────────
    @property
    def has_gates(self) -> bool:
        return bool(self.gates)

    @property
    def gates_by_name(self) -> dict[str, dict]:
        return {g.get("name", f"gate{i}"): g
                for i, g in enumerate(self.gates)}

    def prompt(self, key: str, default: str = "") -> str:
        return str(self.prompts.get(key) or default)

    def tool_name(self, short: str) -> str:
        """MCP 工具名 = `{前缀}__{slug}`，**必须是 ASCII**。

        ★★★ R224：MCP 规范（SEP-986）只允许 `A-Z a-z 0-9 _ - .`。
          真证据（2026-10-29 实跑 list_tools）：16 个工具名全是
          `示例·任意任务` —— SDK 直接警告「may cause compatibility issues」，
          即任何标准客户端都调不动。⇒ **name 用 ASCII，中文进 description**。

        ★★ 2026-10-31 交付审计补的洞：折叠可能**折成空串**。
          `_mcp_合规224("提交任务")` == `""`（全是非 ASCII）。
          于是纯中文档案下 `tool_name()` 返回 `""` —— 工具**没有名字**，
          注册即失败或注册出一个不可调用的空名工具。空名不是"降级"，是坏掉。
          ⇒ 折空时必须给一个**稳定可复现**的 ASCII 兜底名（`t_<8位哈希>`），
            而不是返回空串，也不是随手编一个会随重启变的名字。
        """
        _slug = _mcp_合规224(short)
        _前 = _mcp_合规224(self.tool_prefix)
        if not _slug:
            _slug = "t_" + hashlib.sha1(
                str(short or "").encode("utf-8")).hexdigest()[:8]
        if not _前:
            return _slug
        return f"{_前}__{_slug}"

    def is_objective_done(self, results: dict) -> bool:
        """目标完成判据 = 档案声明的目标门全绿。未声明目标门 ⇒ 永不判完成。"""
        if not self.objective_gates:
            return False
        return all(results.get(g) is True for g in self.objective_gates)

    def artifact_path(self, rel: str) -> Path | None:
        """产物库内解析 rel；越界或未配置 ⇒ None（调用方须拒写）。"""
        if self.artifact.root is None:
            return None
        try:
            root = self.artifact.root.resolve()
            resolved = (root / rel).resolve()
            resolved.relative_to(root)
            return resolved
        except (ValueError, OSError):
            return None


# ═══════════════════════════════════════════════════════════════════
# 占位符替换（档案里可用 {repo}/{artifact}/{audit}/{work}/{products}）
# ═══════════════════════════════════════════════════════════════════
def _subst(obj, mapping: dict[str, str]):
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace("{" + k + "}", v)
        return obj
    if isinstance(obj, list):
        return [_subst(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: _subst(v, mapping) for k, v in obj.items()}
    return obj


def _p(base: Path, v) -> Path | None:
    if v in (None, "", False):
        return None
    p = Path(str(v))
    return p if p.is_absolute() else (base / p)


def _as_tuple(v) -> tuple:
    if not v:
        return ()
    if isinstance(v, str):
        return (v,)
    return tuple(v)


# ═══════════════════════════════════════════════════════════════════
# 判据门规格校验（★ R231：装载期就查，别等运行期在深处抛）
# ═══════════════════════════════════════════════════════════════════
# 合法门类型 = `gate_spec.evaluate` 真正实现的那 14 种。**这里是唯一权威表**：
# 加新门类型时两边一起改，否则档案里写了也没人跑。
GATE_TYPES: frozenset[str] = frozenset({
    "compile", "test_counts", "regression_parity", "precondition",
    "instance_agreement", "grep_forbidden", "grep_required", "scan_output",
    "axiom_footprint", "kernel_statement", "whole_reading", "differential",
    "property", "oracle",
})

# 自然写法 ⇒ 正式门类型。`cmd` 是最常见的错写法（"跑这条命令"的直觉），
# 语义与 `compile` 完全一致（跑命令真退出码），所以**别名过去**，不报错。
GATE_ALIAS: dict[str, str] = {
    "cmd": "compile", "command": "compile", "shell": "compile", "run": "compile",
    "test": "test_counts", "tests": "test_counts", "grep": "grep_required",
}


def _规整门(门列表, mapping: dict[str, str], *, prof_name: str) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """把档案里的门规整成 (可用门, 非法门)。

    · `{repo}` 一类占位符在这里一次性替换（`_subst`）
    · `type` 走别名表；别名目标是合法类型 ⇒ 静默改写成正式类型（但记一条 debug）
    · 真不认识 ⇒ **移出可用门、记进非法门**，并大声告警（列出合法类型）
      —— 口径：**写错的门不许进运行期**，因为进去以后只会以"异常被吞 + 结论照样绿"收场。
    """
    ok: list[dict] = []
    坏: list[dict] = []
    for g in (_subst(门列表, mapping) or []):
        if not isinstance(g, dict):
            坏.append({"raw": repr(g), "因": "门不是 dict"})
            continue
        门 = dict(g)
        typ = str(门.get("type") or "").strip()
        真 = GATE_ALIAS.get(typ, typ)
        if 真 not in GATE_TYPES:
            坏.append({"name": 门.get("name") or "", "type": typ, "因": "未知门类型"})
            continue
        if 真 != typ:
            logger.info("[profile] 门 `%s` 的 type `%s` 是自然写法 ⇒ 按别名改写成 `%s`",
                        门.get("name") or "?", typ, 真)
        门["type"] = 真
        ok.append(门)
    if 坏:
        logger.warning(
            "[profile] 档案 `%s` 有 %d 条判据门**类型不认识**，已移出 gates（不许拿它判达标）：%s\n"
            "   合法类型：%s\n"
            "   常见写法：type: cmd 会自动当 compile 用；你若写了别的名字，改成上面某一个。",
            prof_name, len(坏),
            "；".join(f"{x.get('name') or '?'}({x.get('type') or '?'})" for x in 坏),
            " / ".join(sorted(GATE_TYPES)),
        )
    return tuple(ok), tuple(坏)


def _load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:  # 无 pyyaml 时退化：支持 JSON 档案
        return json.loads(text)


# ═══════════════════════════════════════════════════════════════════
# 通用空档案（兜底）：无项目知识，工具仍可用
# ═══════════════════════════════════════════════════════════════════
def _通用档案的审计目录(root: Path) -> Path:
    """通用空档案的审计目录：**cwd 可写就跟项目走，不可写就回落到状态根**。

    ★★★★★ R544（2026-11-20 部署 venv 真跑）：`work_dir`/`products_dir` 都挂在
      `STATE_ROOT` 下，**唯独 `audit_dir` 挂在 cwd 下** ⇒ cwd 恰好是安装/部署目录
      （只读）时，回执与备份没地方落 —— 而「cwd 可写」不是产品前提
      （MCP 的 cwd 由宿主决定，本项目部署档案里就是 `E:/基础设施/orch-deploy`）。
      真读数：`orch-check` 全绿但 stderr 上 3 条 WARNING，都是这个目录建不出来。

    ★ 这是**条件回落**，不改语义：cwd 可写时行为与原来一模一样（收据跟着项目走）；
      只有真写不了才落到 `STATE_ROOT/audit`（与 work/products 同一处）。
    """
    跟项目 = root / ".orchestrator" / "audit"
    try:
        跟项目.mkdir(parents=True, exist_ok=True)
        探 = 跟项目 / f".probe.{__import__('os').getpid()}"
        探.write_text("ok", encoding="utf-8")
        探.unlink()
        return 跟项目
    except Exception as _e:                  # noqa: BLE001 - 见上面口径
        logger.warning(
            "[profile] 通用空档案：审计目录跟项目走不通（%s: %s）：%s ⇒ "
            "回落到状态根 %s/audit（与 work/products 同一处）。"
            "若要让收据跟着项目走，请把 ORCH_STATE_DIR 指到可写目录，"
            "或在项目里 `orch-init` 建档案。",
            type(_e).__name__, _e, 跟项目, STATE_ROOT)
        return STATE_ROOT / "audit"


def _generic_profile(repo_root: Path | None = None) -> Profile:
    root = (repo_root or Path.cwd()).resolve()
    return Profile(
        name="generic",
        repo_root=root,
        audit_dir=_通用档案的审计目录(root),
        work_dir=STATE_ROOT / "work",
        products_dir=STATE_ROOT / "products",
        receipt_prefix="ORCH",
        mcp_name="orchestrator",
        console_title="OrchestratorConsole",
        tool_prefix="",
        artifact=ArtifactSpec(),
        source=SourceSpec(),
        anchors=AnchorSpec(),
        gates=(),
        dev_gates=(),
        objective_gates=(),
        prompts={},
        default_task={},
        mechanism=MechanismSpec(),
        source_path=None,
        raw={},
    )


# ═══════════════════════════════════════════════════════════════════
# 装载
# ═══════════════════════════════════════════════════════════════════
PROFILE_FILENAME = "orchestrator.profile.yml"


def _find_profile_file(explicit=None) -> Path | None:
    """通用查找链：显式 → 环境变量 → 逐级向上找项目档案 → 工具自带 → None。

    「逐级向上」是通用的：工具通常嵌在某个项目里，向上必然经过该项目根，
    因此项目只要在**自己根目录**放 `orchestrator.profile.yml` 即可被自动发现，
    而工具源码不需要知道任何项目路径。
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None

    # ★★ 显式"不要档案"（2026-10-31 交付审计）：
    #    在这台机器上，`tempfile` 建出来的临时目录**落在仓库内部**
    #    （系统 TEMP 对 python 不可写 ⇒ tempfile 回退到 cwd），
    #    于是"工具被拷到没有档案的地方"这个前提**永远造不出来** ——
    #    向上查找必然经过宿主项目根，命中的是宿主的档案。
    #    ⇒ 给一个明确开关：`ORCH_NO_PROFILE=1` 强制通用空壳。
    #      它同时是有用的产品能力：想把工具当"纯执行器"用、不挂任何项目判据时，
    #      不必靠"恰好站在没有档案的目录"这种运气。
    if str(os.environ.get("ORCH_NO_PROFILE", "")).strip().lower() in ("1", "true", "yes"):
        logger.info("[profile] ORCH_NO_PROFILE=1 ⇒ 强制通用空档案（不加载任何项目档案）")
        return None

    env = os.environ.get("ORCH_PROFILE")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        logger.warning("ORCH_PROFILE 指向的文件不存在：%s", p)

    # ① 从当前工作目录向上（调用方 cd 到项目里时命中）
    for base in (Path.cwd(), *Path.cwd().parents):
        cand = base / PROFILE_FILENAME
        if cand.is_file():
            return cand

    # ② 从工具目录向上找**宿主项目根**。
    # ★★ 2026-10-31（交付审计，B2/B3/B4/B6/E2 四条一起红）：
    #    原来是 `for base in TOOL_ROOT.parents` —— **一路走到磁盘根**，
    #    于是"工具被拷到任意目录、旁边没有任何档案"这个前提根本不成立：
    #    实测把工具拷到 temp 目录后，它仍然向上回溯，
    #    命中了 `E:\...\示例工程\orchestrator.profile.yml`，
    #    于是"无档案时应当回落通用空壳"这条判据永远测不出来（测出来的是宿主的档案）。
    #    ⇒ 边界：**只找工具根的上两级**，且**遇到 .git / 盘根就停**。
    #      工具正常嵌在项目里时（<项目>/多智能体编排/<工具>）两级正好到项目根；
    #      再远就不是"宿主"，是"同一块盘上另一个不相干的项目"。
    _停 = 0
    for base in list(TOOL_ROOT.parents)[:2]:
        cand = base / PROFILE_FILENAME
        if cand.is_file():
            return cand
        if (base / ".git").exists():
            _停 += 1
        if _停:
            break

    # ③ 工具自带配置（使用者可把档案放在工具里）
    cfg = TOOL_ROOT / "config" / "profile.yml"
    return cfg if cfg.is_file() else None


def load_profile(path=None) -> Profile:
    """装载档案。无档案 ⇒ 通用空档案（告警，不静默假装有门）。"""
    pf = _find_profile_file(path)
    if pf is None:
        logger.warning(
            "[profile] 未找到档案（config/profile.yml 或 ORCH_PROFILE）"
            " ⇒ 回落通用空档案：仓库根=%s，无判据门。"
            "工具可用，但不会跑任何项目级判据。", Path.cwd())
        return _generic_profile()

    pf = pf.resolve()
    base = pf.parent
    raw = _load_yaml(pf)

    # 仓库根：档案内相对路径以档案目录为基准
    repo_root = _p(base, raw.get("repo_root") or ".") or base
    repo_root = repo_root.resolve()

    # ★★★ R537：`audit_dir` 原来**默认落在仓库根里面**（`<repo>/.orchestrator`）。
    #   对**只读项目**（vendor 副本、参考资料树、别人给的源码包 —— 很常见）这一句直接
    #   `WinError 5 拒绝访问`；而「档案装载」不该因为写不进去就装不上（实测：
    #   真跑 `orch-init D:\tools` 生成的档案，一加载就刷这行错）。
    #   ⇒ 先试 `<repo>/.orchestrator`，**写不进去就落到状态区**并**明说落哪了**
    #     （口径 92：不许静默换地方 —— 否则用户去错目录找审计记录，等于没有审计）。
    audit_dir = _p(base, raw.get("audit_dir"))
    if audit_dir is None:
        _候选 = repo_root / ".orchestrator"
        try:
            _候选.mkdir(parents=True, exist_ok=True)
            audit_dir = _候选
        except OSError as _e:                              # noqa: BLE001
            audit_dir = STATE_ROOT / "audit"
            logger.warning("[profile] 仓库根不可写（%s：%s）⇒ 审计目录落到状态区 %s",
                           repo_root, _e, audit_dir)
    work_dir = _p(base, raw.get("work_dir")) or (STATE_ROOT / "work")
    products_dir = _p(base, raw.get("products_dir")) or (STATE_ROOT / "products")

    a = raw.get("artifact") or {}
    artifact = ArtifactSpec(
        root=_p(base, a.get("root")),
        glob=str(a.get("glob") or "**/*.lean"),
        key_prefix=str(a.get("key_prefix") or ""),
        lang=str(a.get("lang") or ""),
        exclude_disabled=bool(a.get("exclude_disabled", True)),
        disabled_marker=str(a.get("disabled_marker") or "_disabled"),
    )

    s = raw.get("source") or {}
    source = SourceSpec(
        roots=_as_tuple(s.get("roots")),
        globs=_as_tuple(s.get("globs")),
        skip=frozenset(s.get("skip") or ()),
    )

    an = raw.get("anchors") or {}
    anchors = AnchorSpec(main=str(an.get("main") or ""),
                         core=_as_tuple(an.get("core")))

    mapping = {
        "repo": str(repo_root),
        "audit": str(audit_dir),
        "work": str(work_dir),
        "products": str(products_dir),
        "artifact": str(artifact.root) if artifact.root else "",
        "artifact_parent": (str(artifact.root.parent)
                            if artifact.root else str(repo_root)),
    }
    # ★ R230：记忆索引范围 —— 项目声明的，工具不猜
    memory = tuple(dict(x) for x in (raw.get("memory") or []) if isinstance(x, dict))
    # ★ R231（2026-11-05 跨项目真跑撞出来的）：档案里的门**在装载期就要校验**。
    #
    #   真事：靶子仓档案里写了 `type: cmd`（用户最自然的写法），装载期一查不查，
    #   运行期才在 `gate_spec.evaluate` 深处抛 `ValueError: unknown gate type: cmd`，
    #   而调用方把这条异常按"如实报出、不阻断规划"吞掉了 ⇒ **我配的真测试门等于没接**，
    #   最后靠自动派生的"结构键门"判 `converged`。这就是**假绿的入口**：
    #   门规格写错 → 门没跑 → 结论照样"达标"。
    #
    #   口径：**档案里写错的判据门不许进运行期**。两条处理：
    #     ① 自然写法 `cmd` / `command` / `shell` ⇒ 别名到 `compile`（宽容，因为语义相同）；
    #     ② 真不认识的门 ⇒ 移出 `gates`、记进 `gates_invalid`、装载期**大声告警**（不静默）。
    gates, gates_invalid = _规整门(raw.get("gates") or [], mapping, prof_name=str(raw.get("name") or pf.stem))
    dev_gates, dev_invalid = _规整门(raw.get("dev_gates") or [], mapping, prof_name=str(raw.get("name") or pf.stem))
    gates_invalid = gates_invalid + dev_invalid
    # ★ R23：冻结区（路径相对仓库根；不参与 {repo} 替换 —— 它就是仓库内相对路径）
    frozen = tuple(dict(x) for x in (raw.get("frozen") or []) if isinstance(x, dict))

    m = raw.get("mechanism") or {}
    mechanism = MechanismSpec(
        roots=_as_tuple(m.get("roots")),
        skip_hints=_as_tuple(m.get("skip_hints")),
        non_mechanism=_as_tuple(m.get("non_mechanism")),
        orch_relevant=_as_tuple(m.get("orch_relevant")),
    )

    prof = Profile(
        name=str(raw.get("name") or pf.stem),
        repo_root=repo_root,
        audit_dir=audit_dir,
        work_dir=work_dir,
        products_dir=products_dir,
        receipt_prefix=str(raw.get("receipt_prefix") or "ORCH"),
        mcp_name=str(raw.get("mcp_name") or "orchestrator"),
        console_title=str(raw.get("console_title") or "OrchestratorConsole"),
        tool_prefix=str(raw.get("tool_prefix") or ""),
        artifact=artifact,
        source=source,
        anchors=anchors,
        gates=gates,
        dev_gates=dev_gates,
        gates_invalid=gates_invalid,
        frozen=frozen,
        orphan_api_prefixes=_as_tuple(raw.get("orphan_api_prefixes")),
        memory=memory,
        objective_gates=_as_tuple(raw.get("objective_gates")),
        prompts=dict(raw.get("prompts") or {}),
        default_task=dict(raw.get("default_task") or {}),
        mechanism=mechanism,
        source_path=pf,
        raw=raw,
    )

    # 目录就位（工具运行所需；不存在则建）
    for d in (prof.audit_dir, prof.work_dir, prof.products_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # noqa: BLE001 建目录失败不阻断装载
            logger.warning("[profile] 建目录失败 %s：%s", d, exc)

    if not prof.gates:
        logger.warning("[profile] 档案 `%s` 未声明任何判据门（gates）"
                       " ⇒ 工程级门为空，不会用恒真占位门冒充。", prof.name)
    return prof


# ── 模块级单例（惰性；测试可 reset） ─────────────────────────────
_PROFILE: Profile | None = None


def get_profile() -> Profile:
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = load_profile()
    return _PROFILE


def set_profile(prof: Profile) -> Profile:
    """注入档案（测试 / 多项目并发）。返回被替换的旧档案。"""
    global _PROFILE
    old = _PROFILE
    _PROFILE = prof
    return old or _generic_profile()


def reset_profile(path=None) -> Profile:
    global _PROFILE
    _PROFILE = load_profile(path)
    return _PROFILE


def with_overrides(prof: Profile, **kw) -> Profile:
    """按字段覆盖派生新档案（测试用，不改全局）。"""
    return replace(prof, **kw)


__all__ = [
    "ArtifactSpec", "SourceSpec", "AnchorSpec", "MechanismSpec", "Profile",
    "PKG_DIR", "TOOL_ROOT", "STATE_ROOT", "PROFILE_FILENAME",
    "load_profile", "get_profile", "set_profile", "reset_profile", "with_overrides",
]
