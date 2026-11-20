"""`orch-doctor`：交付包的**环境自检**——装完先跑它，别等跑任务才发现缺东西。

═══════════════════════════════════════════════════════════════════════
它回答六个问题（全部机器可算，不猜）
═══════════════════════════════════════════════════════════════════════
① Python 版本够不够（≥3.12，与 pyproject 的 requires-python 对齐）
② 包能不能导入、版本号是什么
③ 项目档案找不找得到、解析出来是什么（repo_root / 门 / 允许命令）
④ 模型通道：**文件里有几条、环境自动发现了几条、哪几条真能拿 key**
   ★ 只打印**名字**，绝不打印 key（日志会被复制到工单里）
⑤ MCP 工具面：名字是否全部合规（不然任意客户端接不进去）
⑥ 外部工具：`cargo` / `git` / `pwsh` 在不在（在不在**只提示，不判红**：
   有的项目用 Python 判据门，不需要 cargo）

退出码（★ 2026-11-01 定案，台账 DR-C1）：doctor **不再与「任务裁决四值」共用 0/1/2/3 码空间**。
    两者语义完全不同（一个说"这台机器能不能开工"，一个说"这个任务成没成"），
    共用码空间的后果是**脚本无法分辨**：`exit 1` 到底是"doctor 有硬阻塞"还是"任务未达标"？
    ⇒ doctor 的失败码移到 **64 以上**（sysexits 惯例），0 仍然只表示"可以开工"：

        0   可以开工
        64  装坏了（Python 版本不够 / 包导入失败 / 缺依赖）
        65  项目档案有问题（找不到 / 解析失败 / 解析出空）
        66  没有任何可用通道（文件里 0 条、环境也发现不了）
        70  其它硬阻塞组合（含指针型环境变量指向无空间盘等）

    对照：任务裁决四值 **0 达标 / 1 未达标 / 2 参数前提不成立 / 3 未验**（见 `guide`）。
"""
from __future__ import annotations

import ast
import functools
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

_硬阻塞: list[str] = []
_提示: list[str] = []


def _行(tag: str, ok: bool | None, 文本: str) -> None:
    符 = {True: "✓", False: "✗", None: "·"}[ok]
    print(f"  {符} [{tag}] {文本}")
    if ok is False:
        _硬阻塞.append(f"{tag}: {文本}")


def check_state_root() -> None:
    """★★ R547：**状态根真可写吗**（不是"路径算出来了没有"）。

    ★ 为什么要单列一项（真事故，verifier 在干净环境里抓到）：
      知识库默认根曾经自己算 `Path.home()/.orchestrator-state/kb`，与
      `profile.STATE_ROOT`（真试写选出来的）**不是同一处**。本机实测那个路径
      `WinError 5 拒绝访问` ⇒ 播种必死 ⇒ doctor 报「知识库 0 条」，
      被读成「这个项目本来没有外挂知识」。**同一个事实两处实现**（R117）。
      ⇒ 状态根可写性是**所有持久化**的前提，必须自己有一行判据，
        而不是等它下面的每一项各自显示成「空的」。
    """
    import os as _os
    from . import profile as _pf
    根 = _pf.STATE_ROOT
    源 = "ORCH_STATE_DIR" if _os.environ.get("ORCH_STATE_DIR") else "自动探测"
    探 = 根 / f".写探针doctor.{_os.getpid()}"
    try:
        根.mkdir(parents=True, exist_ok=True)
        探.write_text("ok", encoding="utf-8")
        探.unlink()
    except OSError as _e:                       # noqa: BLE001
        _行("状态根", False,
            f"**不可写**：{根}（来源 {源}）⇒ {type(_e).__name__}: {_e}。"
            "★ 这一项红 ⇒ 知识库/回执/任务库**全都落不了盘**，"
            "而它们各自只会显示成「空的」——会被误读成「本来就没有」。"
            "修法：设 ORCH_STATE_DIR 到一个可写目录。")
        return
    _行("状态根", True, f"{根}（来源 {源}，真试写通过）")


def check_kbpower() -> None:
    """`project_kb.门统计()` —— 知识库里**能转成机器的判据**有多少条。

    ★ R262：也是"写了没人调"的一个（全仓引用 = 0）。
      而这个数字比"库里有几条"有意义得多：**库再大，转不出门的规则都只是提示**
      （提示级 = 模型可能不听）。"能变门的条数"才是这套东西的**硬约束存量**。
    """
    from agentscope_orchestrator import project_kb as _kb
    try:
        统 = _kb.门统计()
    except Exception as _e:                       # noqa: BLE001
        _行("知识库可机判", None, f"读取失败（不影响别的检查）：{type(_e).__name__}: {_e}")
        return
    # ★ 字段名是**读**出来的（`门统计()` 真返回：总数/可转门/没写范围/转不出模式/不可机判），
    #   第一版我凭想象写 `可机判`/`条数` ⇒ 永远取到 0，报「0/0 条」还打绿勾 ——
    #   **"看不见"被当成了"没有"**。口径：不许猜数据的形状，去读它的生产者。
    _总 = int(统.get("总数") or 0)
    _可 = int(统.get("可转门") or 0)
    _不 = int(统.get("不可机判") or 0)
    if _总 == 0:
        _行("知识库可机判", None, "知识库是空的（0 条规则）⇒ 无从谈可机判性")
    elif _可 == 0:
        _行("知识库可机判", False,
            f"0/{_总} 条能变判据门（不可机判 {_不} 条）⇒ 全是提示级，模型可能不听")
    else:
        _行("知识库可机判", True, f"{_可}/{_总} 条能变判据门（不可机判 {_不} 条）")


def check_indexdegrade() -> None:
    """读 `repo_index.缓存状态()` —— 索引降级原因（没降级时为空）。

    ★ R262（2026-11-05）：这个函数**写了但没人调**（我数全仓引用 = 0），
      而"索引降级"正是会让人把**环境问题误判成任务失败**的一类读数。
      「一段驱动不了的接线，等于没有接线」⇒ 接进 doctor 才有意义。

    ★ 判档：降级**不是**致命（索引还能用，只是慢/不全）⇒ 记 `None`（提示级），
      而**不许**记成通过：那会把"我这儿退化了"藏起来。
    """
    from agentscope_orchestrator import repo_index as _ri
    降 = _ri.缓存状态()
    _行("索引降级", None if 降 else True,
        "；".join(f"{k}={v}" for k, v in 降.items()) if 降
        else "索引可用（无降级）")


def check_python() -> None:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 12)
    _行("python", ok, f"{v.major}.{v.minor}.{v.micro}"
                      + ("" if ok else "（需要 ≥3.12）"))


def check_package() -> None:
    try:
        import agentscope_orchestrator as A  # noqa: F401
        # ★ 命名空间包（无 __init__.py）时 `__file__` 是 None ⇒ 不许拿它做 Path。
        loc = getattr(A, "__file__", None)
        路径 = str(Path(loc).parent) if loc else "（命名空间包，路径见 sys.path）"
        ver = getattr(A, "__version__", None)
        if not ver:
            try:
                from importlib.metadata import version as _v
                ver = _v("agentscope-orchestrator")
            except Exception:                    # noqa: BLE001
                ver = "?"
        _行("package", True, f"agentscope_orchestrator {ver} 可导入（{路径}）")
    except Exception as e:                       # noqa: BLE001
        _行("package", False, f"导不进来：{e}")


def check_deps() -> None:
    """第三方依赖**单独判**。

    ★ 真证据（2026-10-29 21:2x，全新 venv 里装 wheel 后跑 doctor）：
      缺 `yaml` 时 doctor 报的是「profile: 档案解析失败：Expecting value: line 1
      column 1」——那是 **ImportError 被 yaml 的解析错误替代**，
      读数指向"档案写坏了"，而真相是"没装依赖"。
      ⇒ 诊断读数必须指回真因：先查依赖，再查档案。
    """
    import importlib.util as u
    必需 = [("yaml", "pyyaml"), ("httpx", "httpx"), ("mcp", "mcp")]
    缺 = [包 for 模, 包 in 必需 if u.find_spec(模) is None]
    _行("deps", not 缺,
        "齐全（yaml/httpx/mcp）" if not 缺
        else f"缺 {缺} ⇒ pip install {' '.join(缺)}（缺依赖时下面的档案/通道检查没有意义）")
    _行("deps.agentscope", None,
        "已装" if u.find_spec("agentscope") else "未装（仅当用 agentscope 后端时需要）")


# ★ R589：工程仓的**可判**标志 = 有清单文件（不猜目录名、不看有没有 .git）
_工程清单 = (
    "pyproject.toml", "setup.py", "Cargo.toml", "package.json", "go.mod",
    "pom.xml", "build.gradle", "build.gradle.kts", "CMakeLists.txt", "Makefile",
    "setup.cfg", "meson.build",
)


def _是工程仓(根: "Path | None") -> bool:
    """`根` 下有没有工程清单文件（含一层子目录）。"""
    if 根 is None:
        return False
    try:
        if not 根.is_dir():
            return False
    except OSError:
        return False
    for 名 in _工程清单:
        try:
            if (根 / 名).is_file():
                return True
        except OSError:
            continue
    try:
        for 子 in 根.iterdir():
            if not 子.is_dir() or 子.name in (".git", ".venv", "node_modules", "__pycache__"):
                continue
            for 名 in _工程清单:
                try:
                    if (子 / 名).is_file():
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    # 有 profile 文件也算（用户显式声明过工程档案）
    try:
        for 名 in ("orchestrator.profile.yml", "orchestrator.profile.yaml"):
            if (根 / 名).is_file():
                return True
    except OSError:
        pass
    return False


def check_profile() -> None:
    if importlib.util.find_spec("yaml") is None:
        _行("profile", None, "跳过（缺 yaml，先装依赖）")
        return
    try:
        from agentscope_orchestrator import profile as P
        pf = P.get_profile()
        源 = getattr(pf, "source_path", None) or getattr(pf, "path", None) or "（内置/空档案）"
        _行("profile", True, f"档案 = {源}")
        rr = getattr(pf, "repo_root", "")
        _行("repo_root", bool(str(rr).strip()), f"{rr}")
        门 = list(getattr(pf, "gates", []) or [])
        # ★ R589：gates 属于**工程仓**，不属于安装目录。
        #   在安装目录里跑 doctor 时 `gates 0 条` 是一条**永远修不掉**的假红
        #   （安装目录按设计就不该有 gates）⇒ 先判 `repo_root` 是不是工程仓，
        #   不是就判「不适用」并说清该去哪儿跑。清单文件是**可判**的，不猜目录名。
        if not _是工程仓(Path(str(rr)) if str(rr).strip() else None):
            _行("gates", None,
                f"{len(门)} 条 ⇒ **不适用**：`repo_root` 不是工程仓（像安装目录/空目录）。"
                f"gates 属于被编排的工程 ⇒ 到目标仓里跑 `orch-doctor` 才会判它")
        else:
            _行("gates", bool(门), f"{len(门)} 条" + ("" if 门 else " ⇒ 没判据门，跑起来不知道什么叫'成'"))
        允许 = list(getattr(pf, "gate_cmd_whitelist", []) or [])
        _行("whitelist", None, f"{允许 or '（未声明 ⇒ 门命令不受限，建议声明）'}")
    except Exception as e:                       # noqa: BLE001
        _行("profile", False, f"档案解析失败：{e}")


def check_channels() -> None:
    if importlib.util.find_spec("yaml") is None:
        _行("channels", None, "跳过（缺 yaml，先装依赖）")
        return
    try:
        from agentscope_orchestrator.gateway import load_channels, 渠道文件
        from agentscope_orchestrator.channels_auto import 环境通道
        文件 = Path(渠道文件())
        文件里 = []
        try:
            文件里 = load_channels(文件)
        except Exception as e:                   # noqa: BLE001
            # ★ 真证据（2026-10-30 真跑 install.ps1）：干净机器上**本来就没有**
            #   channels.yml（它在仓库 config/ 里，不随 wheel 分发）。
            #   此前这被算成**硬阻塞** ⇒ 装完就报"不可开工"，而其实安装是成功的。
            #   ⇒ 缺配置文件只说明"还没配通道"，是信息，不是错误。
            _行("channels.file", None, f"未提供 {文件.name}（不必须：可用环境变量自动适配）")
        环境 = 环境通道()
        _行("channels.file", None, f"{文件.name}：{len(文件里)} 条"
                                    + (f" → {[c.name for c in 文件里]}" if 文件里 else ""))
        _行("channels.env", None, f"环境自动发现：{len(环境)} 条"
                                   + (f" → {[c['name'] for c in 环境]}" if 环境 else "（未设任何 *_API_KEY）"))
        # ★ 只判"能不能拿到 key"，不探活（探活要联网，doctor 不该等）
        能拿 = []
        for c in list(文件里):
            try:
                _ = c.api_key
                能拿.append(c.name)
            except Exception:                    # noqa: BLE001
                pass
        for d in 环境:
            if (os.environ.get(d["key_env"]) or "").strip():
                能拿.append(d["name"])
        if 能拿:
            _行("channels.key", True, f"能拿到 key 的通道：{能拿}")
        else:
            # ★ 同样是 install.ps1 真跑暴露的：**没有 key ≠ 装坏了**。
            #   用户还没配自己的模型 API 而已。判成硬阻塞会让"一键部署"看起来失败。
            print("  ! [channels.key] 还没有可用的模型通道 —— 这不是安装失败，是**下一步**:")
            print("        最省事：")
            print("          setx ORCH_BASE_URL \"https://你的端点/v1\"")
            print("          setx ORCH_API_KEY  \"sk-...\"")
            print("          setx ORCH_MODEL    \"你的模型名\"")
            print("        （或用 OPENAI_API_KEY / DEEPSEEK_API_KEY 等，见 QUICKSTART）")
    except Exception as e:                       # noqa: BLE001
        _行("channels", False, f"通道装配失败：{e}")


def check_mcp_names() -> None:
    try:
        import re
        from agentscope_orchestrator import taixu_mcp as T
        源 = Path(T.__file__).read_text(encoding="utf-8")
        短 = re.findall(r'@mcp\.tool\(name=_TOOL\("([^"]+)"\)\)', 源)
        坏 = [s for s in 短 if not re.match(r"^[A-Za-z0-9_.-]+$", s)]
        _行("mcp.names", not 坏, f"{len(短)} 个工具，不合规 {坏 or '无'}")
    except Exception as e:                       # noqa: BLE001
        _行("mcp.names", None, f"跳过：{e}")


def check_tools() -> None:
    for 名, 必需 in (("cargo", False), ("git", False), ("pwsh", False)):
        p = shutil.which(名)
        _行(f"tool.{名}", None, p or "（未安装；若判据门不用它则无妨）")


def check_tempdir() -> None:
    """临时目录**必须可写且不为空盘**。

    ★ 真证据（2026-10-29 21:2x）：本机 `TMPDIR` 指向一个 0 字节可用的盘，
      而 `TEMP` 指向有空间的盘。Python 的 `tempfile` **先看 TMPDIR**，
      于是 pip 全程在满盘上写 ⇒ 报 `OSError: [Errno 28] No space left on device`，
      而 `Get-PSDrive` 明明显示 100GB 空闲。**这个错报把原因指到了完全错误的地方**，
      我为此白查了三轮。⇒ 交付自检必须把"临时目录在哪、还剩多少"直接印出来。

    ★★ 2026-11-03 又一条真证据：这一步**自己会把 doctor 整死**。
      在**一个可用临时目录都没有**的环境里（实测：`TMPDIR/TEMP/TMP` 全空、且
      `E:/Temp`、`%LocalAppData%/Temp`、`C:/Windows/Temp` 都不存在或不可写），
      `tempfile.gettempdir()` 直接抛
      `FileNotFoundError: [Errno 2] No usable temporary directory found in [...]`
      ⇒ doctor **退出码 1、一行报告都没有**。

      这为什么必须修（而不是"环境太坏，崩了也正常"）：
        · doctor 存在的意义**恰恰是在环境坏的时候告诉你坏在哪**。
          它在"最该工作"的场景下死掉，就是最坏的一种失效；
        · 它与本文件里 `project_kb.统计()` 那条规矩是同一个：
          **只读工具崩掉，比返回"这里读不了"更糟** —— 后者能修，前者只能猜；
        · 而且崩掉会把**后面所有检查项**一起吞掉，人看到的是"doctor 坏了"，
          不是"临时目录坏了" ⇒ 又把人指到错误的地方（上面那次事故就是
          "错报把原因指到完全错误的地方，白查三轮"）。
      ⇒ 口径：`gettempdir()` 的失败**本身就是要报告的一项**，不是异常。
    """
    import tempfile
    try:
        d = Path(tempfile.gettempdir())
    except Exception as e:                       # noqa: BLE001
        # ★ 不许 re-raise：这里失败是**体检结果**，不是体检事故。
        _行("tmpdir", False,
            f"**一个可用的临时目录都没有**（{type(e).__name__}: {e}）"
            " ⇒ 装包/编译/子进程判据会失败，且报错通常指向别处。"
            " 修法：设 TMPDIR/TEMP/TMP 到一个存在且可写的目录（三者最好一致）")
        return
    try:
        free = shutil.disk_usage(d).free / (1024 ** 3)
    except Exception as e:                       # noqa: BLE001
        _行("tmpdir", False, f"{d} 不可用：{e}")
        return
    try:
        probe = d / f".orch_probe_{os.getpid()}"
        probe.write_bytes(b"x" * 65536)
        probe.unlink()
        可写 = True
    except Exception as e:                       # noqa: BLE001
        _行("tmpdir", False, f"{d} 不可写：{e}（pip 装包会失败且报错会误导到别的盘）")
        return
    _行("tmpdir", free > 0.2,
        f"{d} · 可写={可写} · 剩 {free:.2f}GB"
        + ("" if free > 0.2 else " ⇒ 空间不足，装包会报 Errno 28（且可能指向别的盘，别被骗）"))

    # ── ★★★ 2026-11-07 新增：残渣报告 ────────────────────────────────
    # 为什么必须在这里报（不是写个脚本等人去跑）：
    #   `gettempdir()` 曾在本机**回落到 cwd**（= 包根），于是每一次 `mkdtemp()`
    #   都在仓里造一个目录、而且没人删。实测代价：包根下积了 **4,736 个**直接子目录，
    #   其中 3,739 个随机名的合计 **2.6 GB**，全是历年测试夹具的整仓拷贝；
    #   工位残留同步从 1,749 涨到 1,807。
    #   「缺口史」之前这种「慢慢变坏」是**看不见的** —— 所以它得进体检，
    #   每跑一次 doctor 就看见一次。**需要调用方记得做的事，等于没做。**
    try:
        from . import temp_guard as _tg
        # ★ 只调**一次**：`清理残渣(干跑=True)` 内部会算 `残渣()`，返回值里
        #   候选数/体积/跳过大类都齐。
        #   我先写成「调 `残渣()` 报一个数、再调 `清理残渣()` 报另一个数」——
        #   实测立刻出现 **687 vs 690** 的差：两次调用之间又有 3 个目录越过年龄线。
        #   「同一个判断写在两处，必然给出两个答案」—— 这里就是。
        残 = _tg.清理残渣(干跑=True)
        if 残["候选数"]:
            _行("tmp残渣", False,
                f"包根下判定为临时残渣 **{残['候选数']} 个**、约 **{残['候选合计MB']:,.0f} MB**"
                f"（下限年龄 {残['最小年龄小时']} 小时；跳过 {sum(残['跳过大类'].values())} 个："
                + "；".join(f"{k}×{v}" for k, v in list(残["跳过大类"].items())[:3]) + "）"
                f" ⇒ 修法：`temp_guard.清理残渣(干跑=False, 最小年龄秒=86400)`")
        else:
            _行("tmp残渣", True, "包根下没有超龄的临时残渣目录")
    except Exception as e:                       # noqa: BLE001
        # 与上面同一条口径：这里失败是**体检结果**，不是体检事故
        _行("tmp残渣", None, f"残渣统计没跑出来：{type(e).__name__}: {e}")


def check_pointers() -> None:
    """**指向型环境变量**必须落在有空间的盘上。

    ★ 真证据（2026-10-30，引擎自检 5 条红，查了两轮才定位）：
      本机 `PYTHONPYCACHEPREFIX = I:\\莉莉丝\\.cache\\python\\pycache`、
      `TMPDIR = I:\\莉莉丝\\.temp`、`TEMP = I:\\Temp` —— 三者全指向 **I 盘**，
      而 I 盘 **0 字节可用**。
      于是 `python -m py_compile fib.py` 报
        `[WinError 112] 磁盘空间不足: 'I:\\...\\pycache\\...'`，
      编译门全线判红 —— 而**代码完全正确**。
      我先把责任算到"依赖替换"上，又把责任算到"门站错目录"上，两次都错：
      **读数（编译失败）与真因（盘满）之间隔着一个环境变量。**

    ⇒ 交付自检必须把这些**指针型**变量直接印出来并判空间。
    """
    import tempfile
    查 = ["TMPDIR", "TEMP", "TMP", "PYTHONPYCACHEPREFIX"]
    坏 = []
    for 名 in 查:
        v = os.environ.get(名)
        if not v:
            continue
        try:
            free = shutil.disk_usage(v if Path(v).exists() else Path(v).anchor).free / (1024 ** 3)
        except Exception:                        # noqa: BLE001
            continue
        标 = "✗" if free < 0.2 else "·"
        print(f"  {标} [{名}] {v} · 剩 {free:.2f}GB")
        if free < 0.2:
            坏.append(f"{名}={v}（剩 {free:.2f}GB）")
    _行("pointers", not 坏,
        "全部落在有空间的盘" if not 坏
        else f"★ 这些变量指向无空间盘：{坏} ⇒ 编译/gate 会报【磁盘空间不足】而让人以为是代码坏了")


def _退出码() -> int:
    """把"哪一类硬阻塞"折成**不与裁决四值冲突**的码（见模块 docstring 的对照表）。

    ★ 顺序即优先级：装坏了先说装坏了 —— 后面的档案/通道问题在装坏时本就不可信。
    """
    标签 = " ".join(_硬阻塞)
    if any(t in 标签 for t in ("python", "import", "deps")):
        return 64
    if any(t in 标签 for t in ("profile", "档案")):
        return 65
    if any(t in 标签 for t in ("channel", "通道")):
        return 66
    return 70


def check_semsearch() -> None:
    """检索桥（外挂知识库）能不能用。

    ★ 2026-11-03 补：以前 doctor 只验编排器自己，**不验检索桥**。后果是
      「部署完了」和「检索其实没接上」在自检里长得一模一样 —— 后者会静默降级成
      「没有外挂知识」，编排器不报错、只是修不动时少一份参考。那是最该被发现的那种失败。

    判据口径：**没有二进制 ⇒ 警告不阻塞**（这台机器就是没知识库，编排器仍能干活）；
    **二进制在但索引读不到 ⇒ 警告并说清三种常见原因**（配错 project_root / 索引还没建 /
    PATH 里是旧版二进制）。这些都不算硬阻塞，因为"没有外挂知识"不等于"不能开工"。
    """
    try:
        from . import semsearch_link as S
    except ImportError as exc:                       # noqa: BLE001
        print(f"  · [semsearch] 导入不了检索桥：{exc}")
        return
    try:
        配置 = S._配置文件()
    except Exception:                                # noqa: BLE001
        配置 = {}
    if 配置:
        print(f"  · [semsearch] 部署配置：{ {k: v for k, v in 配置.items() if not k.startswith('_')} }")
    二 = S.二进制()
    if 二 is None:
        print("  · [semsearch] 无可用二进制 ⇒ 没有外挂知识（不影响开工）")
        return
    能力 = S.可用()
    if 能力.get("可用"):
        条 = 能力.get("索引条目")
        print(f"  ✓ [semsearch] {二} · 索引 {条} 条")
        return
    print(f"  · [semsearch] 二进制在但索引读不到：{能力.get('原因')}")
    print("      常见原因：① 没配 project_root ⇒ 不知道去哪找 .semsearch_index/concepts.json；")
    print("                ② 配了但索引还没建；")
    print("                ③ PATH 里是**旧版**二进制（没有库模式）⇒ 显式配 bin 指新的")


def check_project_kb() -> None:
    """**人写的外挂知识**（project_kb）到底有没有东西、查不查得出来。

    ★★★★★ 2026-11-03 补这一条的理由（本轮最值钱的发现）：
      这个知识库**功能完整、注入链路接通、每次真跑都在注入** —— 但它当时是 **0 条**，
      也就是**一直在往模型提示里拼空串**。而这件事**没有任何判据会红**：
      注入空串不是错误，修复主链照跑，`doctor` 也说"✓ 可以开工"。
      ⇒ 症状是"外挂知识看起来接了、实际一条都到不了模型"，而所有人都看不见。
      ⇒ 所以必须让 doctor **把它读出来**：条数 + **一次真实检索**的结果长度。
        光报条数不够 —— 条数非零但检索恒零命中（就是当时另一个缺陷）同样是空转。

    判据口径：**库为空 ⇒ 警告不阻塞**（没有外挂知识不等于不能开工），
    但话要说白：明确写出"外挂知识空转"以及怎么把它装上。
    """
    try:
        from . import project_kb as K
    except ImportError as exc:                       # noqa: BLE001
        print(f"  · [知识库] 导入不了：{exc}")
        return
    try:
        统计 = K.统计()
    except Exception as exc:                         # noqa: BLE001
        print(f"  · [知识库] 读不了（不阻塞）：{type(exc).__name__}: {exc}")
        return
    条数 = 统计.get("条数") or 0
    库 = 统计.get("库")
    # ★ 配置指到了死路径 ⇒ 必须**说出来**，否则"库 0 条"会被读成"这项目本来就没知识"。
    #   实测（2026-11-03）：知识种子搬进包内后，MCP 配置里那条硬路径成了死路径，
    #   于是库 0 条、注入空串、而 doctor 照样说"可以开工"。
    if 统计.get("配置回落"):
        r = 统计["配置回落"]
        print(f"  ! [知识库] **ORCH_KB_DIR 指到了没用的地方**：{r['ORCH_KB_DIR']}（{r['原因']}）")
        print(f"      已回落到 {r['实际使用']} —— 去把那条配置改对或删掉，别让它静默空转。")
    if not 条数:
        print(f"  · [知识库] **0 条**（{库}）⇒ 外挂知识在**空转**：每轮注入的都是空串，")
        print("      而这件事不会报错、不会让门变红 —— 装上它：")
        print("        python -m agentscope_orchestrator.doctor --播种")
        print("      （把随包的知识种子装进运行期目录；部署机上也能直接用）")
        return
    # ★ 光看条数不算验：**真查一次**。检索恒零命中（整串子串匹配那类病）时条数是好看的。
    块 = ""
    try:
        块 = K.注入(关键词="判据起子进程 编码 不符 分类=None 报错", 上限字=700)
    except Exception:                                # noqa: BLE001
        pass
    if not 块:
        print(f"  · [知识库] {条数} 条，但**抽样检索查不出任何东西**（{库}）⇒ 同样等于空转：")
        print("      条数好看、注入为空，最容易被误当成'知识没用'。查检索口径与条目是否带症状。")
        return
    冲突 = 统计.get("冲突") or []
    print(f"  ✓ [知识库] {条数} 条 · 抽样检索注入 {len(块)} 字 · 可机判门 {统计.get('可机判门')} 条"
          + (f" · **冲突 {len(冲突)}**" if 冲突 else ""))


def check_coord_chain() -> None:
    """坐标链体检：链在不在、完不完整（这是"项目全局图谱"的第一根柱子）。

    ★ 为什么归 doctor：`coord_chain` 是**四方文档联动的判定地基**（LINK-01~05 全靠
      坐标判"漂移/缺项"）。链一旦断裂，此后所有联动判据都会给出**错的结论**
      —— 它们会说"没漂移"，其实是因为比较的两端都读错了。
      ⇒ "链是不是完整"必须每次体检都看见，和"知识库是不是空的"同理：
        **不会报错、也不让门变红的失效，必须有人主动报出来。**
    """
    from . import coord_chain as C
    st = C.统计()
    if st.get("错误"):
        _行("坐标链", False, f"读不了：{st['错误']}（{st.get('链')}）")
        return
    n = st.get("条数") or 0
    if not n:
        print(f"  · [坐标链] 空链（{st.get('链')}）⇒ 还没有回执挂上来。")
        print("      回执一落地就会自动挂链（`ts_receipt.render`）；历史回执可以整批补：")
        print("        python -m agentscope_orchestrator.doctor --重建链 <回执目录>")
        return
    if st.get("完整"):
        _行("坐标链", True, f"{n} 条 · {st.get('首')} → {st.get('尾')} · 按种类 {st.get('按种类')}")
        return
    # 链受损 —— 这是硬问题：判据会基于坏链给出错结论，必须显式报出来
    _行("坐标链", False, f"**链不完整**（{n} 条）—— {st.get('体检')}")
    print("      链坏了以后，LINK-01~05 的『漂移/缺项』判定都会基于错的数据给出结论。")
    print("      修法：从回执全量重建 `doctor --重建链 <回执目录>`"
          "（链是缓存，回执才是事实；重建不保证与旧链逐字相同）")


def _重建链(目录: str) -> int:
    """`doctor --重建链 <回执目录>`：从回执全量重算链。

    ★ 为什么允许"重算"而不是"必须原样恢复"：原链的**步序**无法从产物完全还原
      （同一分钟内可能有多份回执，顺序信息只在链里）。所以这是**造一条合法的新链**，
      不是恢复旧链。把这件事说清楚，比让你以为"恢复成功"要好。
    """
    from . import coord_chain as C
    src = Path(目录)
    if not src.is_dir():
        print(f"✗ 不是目录：{src}")
        return 1
    r = C.重建_明细(src)
    st = C.统计()
    print(f"✓ [坐标链] 从 {src} 收 {r['条数']} 个坐标"
          f"（目录内 .md 共 {r['目录内_md总数']}，按 `{r['过滤']}` 跳过 {r['非回执被跳过']} 个非回执）")
    print(f"  {st.get('首')} → {st.get('尾')} · 完整={st.get('完整')} · 按种类 {st.get('按种类')}")
    print("  注意：这是**重算**出来的链（步序按文件 mtime 排），与原链不保证逐字相同。")
    return 0


def check_linkage() -> None:
    """四方文档联动体检：能找到几份、能跑几条判据。

    ★ 为什么归 doctor：联动的价值全在**四份文档互相对得上**。而"对不上"
      这件事**不会报错**（每份文档单看都好好的）—— 正是本项目里"哪里断了看不出来"
      的源头。⇒ 必须有人主动去看一眼，doctor 就是这个"有人"。

    诚实边界：**找不到文档时不判红**。通用工具不知道项目把四份文档放在哪
    （那是档案的事），硬猜路径会把"没配档案"误报成"联动坏了"。
    ⇒ 找不到就报"未配置"，并告诉调用方怎么配。
    """
    import os
    from pathlib import Path as _P
    from . import linkage as L
    环 = os.environ.get("ORCH_LINKAGE_DIR", "").strip()
    if not 环:
        print("  · [四方联动] **未配置**（ORCH_LINKAGE_DIR 没设）⇒ 不知道该去哪找四份文档。")
        print("      配法：ORCH_LINKAGE_DIR=<.audit_trail 目录>，其中放 "
              "`<项目>_工程快照.md` 与 `<项目>_状态索引.md`")
        return
    d = _P(环)
    if not d.is_dir():
        _行("四方联动", False, f"ORCH_LINKAGE_DIR 指到了不是目录的地方：{d}")
        return
    快照s = sorted(d.glob("*工程快照*.md"))
    索引s = sorted(d.glob("*状态索引*.md"))
    if not 快照s or not 索引s:
        print(f"  · [四方联动] 在 {d} 里找不到 {'快照' if not 快照s else ''}"
              f"{'/' if (not 快照s and not 索引s) else ''}{'状态索引' if not 索引s else ''}")
        print("      快照/状态索引的文件名要能让 `*工程快照*.md` / `*状态索引*.md` 匹配上")
        return

    # ★★ 挑哪一份：**按"能不能解析出真东西"挑，不按文件名排序挑。**
    #   实测（2026-11-03）：一个审计目录里同时存在两份快照 ——
    #   一份是**别的项目**留下的（很小、没有版本行也没有编号条目），
    #   一份是本项目的真快照。按文件名排序，别项目那份排前面
    #   ⇒ 体检拿错了对象，报了一串假问题（"快照没有版本行 / 抽不出编号条目"），
    #   **看起来像项目文档坏了，其实是我挑错了文件**。
    #
    #   ⇒ 口径：**候选有多个时，选"信息最全"的那一个**（版本行 + 编号条目最多的），
    #     并把"有几个候选、选了哪个"如实打出来 —— 免得下次又是我自己看错。
    #     这里也**不写具体项目名**（那条判据连注释都算）：举例一律用中性说法。
    def _打分(p) -> tuple:
        x = L.解析快照(p)
        return (1 if x.get("版本") else 0, len(x.get("条目") or []), len(x.get("概念") or []))

    快照s.sort(key=_打分, reverse=True)
    # ★ 索引同理（同一个 bug 的第二个位置）：也按"信息最全"挑 ——
    #   索引的"信息量"就是**归档条数**（还有那一章在不在）。
    #   实测：别项目那份状态索引排在真索引前面，前者没有归档章节 ⇒ 又是一串假问题。
    #   ★ 教训**再记一遍**：挑文件要按**内容**挑，不能按**文件名排序**挑。
    #     这个坑在同一个函数里出现了两次（快照一次、索引一次），因为第一版我
    #     只给快照打了分、忘了索引 —— **改一处就要把同类位置全找一遍**。
    索引s.sort(key=lambda p: (1 if L.解析状态索引(p).get("有归档章节") else 0,
                               len(L.解析状态索引(p).get("归档") or [])), reverse=True)
    多 = f"（候选 {len(快照s)} 份，取信息最全的）" if len(快照s) > 1 else ""
    多Z = f"（候选 {len(索引s)} 份，取信息最全的）" if len(索引s) > 1 else ""
    S = L.解析快照(快照s[0])
    Z = L.解析状态索引(索引s[0])
    问 = []
    if not S.get("版本"):
        问.append("快照没有版本行（LINK-02 无法判漂移）")
    if not S.get("条目"):
        问.append("快照抽不出编号条目（LINK-01 无法核引用）")
    if not Z.get("有归档章节"):
        问.append("状态索引没有『回执归档记录』这一章")
    if 问:
        _行("四方联动", False,
            "；".join(问) + f"（快照 {快照s[0].name}{多} · 索引 {索引s[0].name}{多Z}）")
        return
    _行("四方联动", True,
        f"快照 {S.get('版本')}{多} · 条目 {len(S.get('条目') or [])} 个"
        f" · 概念 {len(S.get('概念') or [])} 个"
        f" · 索引已归档 {len(Z.get('归档') or [])} 条{多Z}"
        + ("  ⇒ 归档**是空的**，说明这一步一直没做" if not Z.get("归档") else ""))


def check_dispatch() -> None:
    """分工线体检：四档分流各自的存量，以及"有多少盲区在等一张门"。

    ★ 为什么归 doctor：这是**整套东西的进度表**。
      你真正想知道的不是"这轮修了几个 bug"，而是
      **"项目的判定基准厚到什么程度了"** —— 那就看这几个数：

        · 知识条数  = 人写下的判定基准（外挂知识）
        · 盲区条数  = 门盖不到、已记账的地方
        · 该加门    = **记过、还没被门盖住的盲区**（**记下即提**）⇒ 下一步该干活的清单

      ★ "该加门"这个数**只要不为 0，就说明有一件事该做了** ——
        它把"一边错一边加门"从口号变成了一个可读的数字。

      ★★★★★ 2026-11-04：**「该做哪一件」也必须从读数推，不许写死。**
        `该加门` 只是个**计数**，里面装着三种下一步完全不同的东西
        （造不出门 / 只拦得住那一次 / 其余待提门）。原来这一支固定说
        「把它们转成真门」—— 实测（真库 2026-11-04）拦住的那 1 条
        真身是**造不出门**（知识库里没有对应规则 ⇒ 连门都没有），
        那句话会把人指去干一件**做了也没用**的事（没门可提）。
        ⇒ 动作按 `blind_spot.闭环概览()` 的读数分档；
          并且把 `闭环接线()` 扫出来的 `提为门` 真调用点一起报出来 ——
          「还没接进真流程」时，待提的那几条靠的是**人/LLM 手动调**，
          这与「已接、只等下一次真跑」是两件事。
    """
    from . import dispatch as D
    st = D.统计()
    if st.get("错误"):
        _行("分工线", False, f"读不了：{st['错误']}")
        return
    知, 盲, 该 = st.get("知识条数", 0), st.get("盲区条数", 0), st.get("该加门", 0)
    if 该:
        # ★★★★★ 2026-11-04：**下一步动作从读数分档，不许写死。**
        #   原来这一支固定说「把它们转成真门」。实测（真库 2026-11-04）
        #   拦住的那 1 条的 `验证` 正是**造不出门**（KB 里没有对应规则
        #   ⇒ 连门都没有），那句话把人指去干一件**做了也没用**的事。
        #   三档的下一步完全不同：造不出门 ⇒ 攒知识/找判据；
        #   只拦得住那一次 ⇒ 把模式从「这一次」提到「这一类」；
        #   其余 ⇒ 去调 `提为门`，或写明理由 `否决`。
        概 = _闭环概览()
        闭 = 闭环接线()                          # ★ 接线读数**现算**（含包装函数那一档）
        造 = int(概.get("造不出门", 0) or 0)
        类废 = int(概.get("拦不住这一类", 0) or 0)
        可提 = max(0, 该 - 造 - 类废)
        话 = []
        if 可提:
            话.append(f"{可提} 条**该去提门**（`blind_spot.提为门(键, 门名)`，"
                      f"或写明理由否掉：`blind_spot.否决(键, 理由)`）")
        if 造:
            话.append(f"{造} 条**造不出门**（真验过了，知识库里没有对应规则"
                      f" ⇒ 去**攒知识/找判据**；调 `提为门` 没用，没门可提）")
        if 类废:
            话.append(f"{类废} 条**只拦得住那一次**（模式抄了证据原文）"
                      f" ⇒ 该把模式从「这一次」提到「这一类」")
        _行("分工线", False,
            f"知识 {知} 条 · 盲区 {盲} 条 · **{该} 个盲区在等一张门**："
            + "；".join(话) + " · " + _提为门文本(
                闭.get("提为门") or {}, 闭.get("提为门入口") or {}))
        return
    if not 知 and not 盲:
        print("  · [分工线] 知识 0 条、盲区 0 条 ⇒ 分工线现在**只会走第四档（交给 LLM）**，")
        print("      也就是「项目判定基准还是空的」。先 `doctor --播种` 把随包知识装上。")
        return
    _行("分工线", True,
        f"知识 {知} 条 · 盲区 {盲} 条 · 该加门 {该} 个"
        + (f" · 按状态 {st.get('按状态')}" if st.get("按状态") else ""))


def check_events() -> None:
    """事件驱动体检：检查点数、步数、该不该全量、有没有高级告警。

    ★ 为什么归 doctor：这套东西的价值是"**随时**知道有没有变坏"。
      而"随时"要有个地方能一眼看到 —— 检查点数涨不涨，
      说明**到底有没有人在用事件触发**（而不是又退回"交付前跑一次"）。
    """
    from . import events as E
    st = E.统计()
    if st.get("错误"):
        _行("事件驱动", False, f"读不了：{st['错误']}")
        return
    高 = st.get("高级告警", 0)
    if 高:
        _行("事件驱动", False,
            f"检查点 {st.get('检查点数')} · 步数 {st.get('步数')} · "
            f"**{高} 条 ERROR 以上告警**（最近：{(st.get('高级告警示例') or [''])[0]}）")
        return
    _行("事件驱动", True,
        f"检查点 {st.get('检查点数')} 个 · 步数 {st.get('步数')} · 周期 {st.get('周期')}"
        f" · 变化合计 {st.get('变化数合计', 0)} 项 · 高级告警 {高}"
        + (f" · 上次全量 {st.get('上次全量')}" if st.get("上次全量") else
           "  ⇒ 还没落过全量检查点（增量要有个基准才成立）"))


def check_receipt_fmt() -> None:
    """回执格式体检：模板/证据类型/禁句式/六部分是否齐。

    ★ 为什么归 doctor：这些**只在"有没有配"这一层能判**。
      模板文档位置是环境给的（工具不写死项目路径），
      没配就说清怎么配 —— 不说的话，回执格式判据会静默不工作。
    """
    from . import receipt_fmt as R
    st = R.统计()
    tpl = st.get("模板文档", "")
    if not tpl or "未配置" in tpl:
        print(f"  · [回执格式] 已内置 {st['模板种类']} 种模板 · {st['证据类型']} 类证据链"
              f" · {st['禁止句式类']} 类禁句式 · {st['六部分']} 部分结构")
        print("      模板文档**未配置**（ORCH_RECEIPT_TPL）⇒ 校验不依赖它，"
              "但人工核对时要知道去哪看")
        return
    _行("回执格式", True,
        f"{st['模板种类']} 种模板 · {st['证据类型']} 类证据链 · "
        f"{st['禁止句式类']} 类禁句式 · {st['六部分']} 部分结构")


def check_context_view() -> None:
    """语义入口体检：一次能给几段、抽样多长、截没截。

    ★ 为什么归 doctor：这个工具的全部价值是"**一次给全 + 不占上下文**"。
      所以要盯的是两个数：**段数**（少了说明关联不上）和**字数**（多了说明上限失效）。
      两个数在 doctor 里一眼能看到，才知道它有没有真的在按设计工作。
    """
    from . import context_view as CV
    try:
        体 = CV.看("__体检__", 上限=600)
        段 = 体.get("段们", 0)
        字 = len(体.get("正文") or "")
        _行("语义入口", True,
            f"一次抽样 {段} 段 · {字} 字（上限 {体.get('上限')}）"
            f" · 来源 {','.join(体.get('来源') or []) or '无'}"
            f" · 上限 {CV.默认上限}")
    except Exception as e:                       # noqa: BLE001
        _行("语义入口", False, f"读不了：{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 闭环接线：**接没接进真流程**只许从事实现算（两个读数），不许写在文档里
# ═══════════════════════════════════════════════════════════════════════════
def _被调名(节点: ast.AST) -> str:
    """`f` / `m.f` / `m.x.f` 一律解成**末段名** `f`（看的是调用，不是别名）。"""
    if isinstance(节点, ast.Attribute):
        return 节点.attr
    if isinstance(节点, ast.Name):
        return 节点.id
    return ""


@functools.lru_cache(maxsize=16)
def _扫调用点(源码根: str, 名字: str) -> dict:
    """`ast` 扫一个目录下的 `.py`，找出对 `名字` 的**真调用点**（结果按路径缓存）。

    ★ 为什么必须 `ast`、不能搜字符串（这不是洁癖，本包已经踩过）：
      本包**自己的文档字符串**里就留着一句**过期的现状断言**（`blind_spot.py` 说明表那一行，
      把 `提为门` 说成没有任何真流程调用方）。
      搜字符串会把那句**说明**当成**接线** ⇒ 「接没接」永远答成「接了」。
      `ast` 里**根本没有注释节点**（注释不进语法树）⇒ 「注释里写不算接线」是白拿的；
      文档字符串是 `Expr(Constant)`，而这里只认 `ast.Call` ⇒ 也不会上当。
    ★ 为什么「定义模块内部调自己」不算接线：那是模块内部实现，不是流程接线。
      定义模块是**扫出来的**（谁 `def 名字`），**不是写死的文件名**。
    """
    定义在: list[str] = []
    调用点: list[dict] = []
    跳过: list[str] = []
    树s: list[tuple[str, ast.AST]] = []
    for q in sorted(Path(源码根).glob("*.py")):
        try:
            树s.append((q.name, ast.parse(q.read_text(encoding="utf-8", errors="replace"))))
        except Exception:                        # noqa: BLE001 语法坏的文件不拖垮体检
            跳过.append(q.name)
    for 名, 树 in 树s:
        for 节 in ast.walk(树):
            if (isinstance(节, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and 节.name == 名字):
                定义在.append(名)
    for 名, 树 in 树s:
        if 名 in 定义在:
            continue
        for 节 in ast.walk(树):
            if isinstance(节, ast.Call) and _被调名(节.func) == 名字:
                调用点.append({"文件": 名, "行": 节.lineno})
    return {"定义在": sorted(set(定义在)), "调用点": 调用点, "跳过": 跳过,
            "扫过": len(树s)}


@functools.lru_cache(maxsize=32)
def _模块内上游(源码根: str, 模块文件: str, 名字: str) -> list[str]:
    """`名字` 的**模块内上游**：定义模块里哪些**函数体**调了它（按函数名）。

    ★ 为什么需要它（本轮实测，不是假想）：真流程接一个能力时，接的**常常不是它本身**，
      而是同模块的一个**包装函数**（`提为门` 就是这样：`fixloop` 调的是
      `提该加门的`，由它转调 `提为门`）。只问「`提为门` 有没有源码直调点」会答「没有」
      ⇒ 报告说「该接进修复环出口」，**而出口其实已经接了** ——
      那句建议会让人去接一条**已经接上的线**。
    ★ 上游是**扫出来的**，不许写死函数名：写死就等于换个包装函数又过期一次
      （这正是本仓反复栽的那个病）。
    """
    try:
        树 = ast.parse((Path(源码根) / 模块文件).read_text(encoding="utf-8", errors="replace"))
    except Exception:                            # noqa: BLE001 读不了就说读不了
        return []
    上游: list[str] = []
    for 节 in ast.walk(树):
        if not isinstance(节, (ast.FunctionDef, ast.AsyncFunctionDef)) or 节.name == 名字:
            continue                             # 「自己调自己」不是上游（那只是递归）
        if any(isinstance(内, ast.Call) and _被调名(内.func) == 名字 for 内 in ast.walk(节)):
            上游.append(节.name)
    return sorted(set(上游))


@functools.lru_cache(maxsize=32)
def _真入口(源码根: str, 名字: str, 深度: int = 3) -> dict:
    """`名字` 有没有**真流程入口** —— 直调点，**或**经模块内上游间接进来（永不抛）。

    ★ 判据换成这个的理由：问「函数有没有被直接调」问的是**实现形状**，
      而我们真正想知道的是「**这条线通了没有**」。形状会被重构改掉
      （加一层包装函数就变了），而「通没通」是稳定的。
    ★ 逐层往上找：每一层都问「这一层的包装函数**自己**有没有真调用点」——
      包装函数**也可以被再包一层**，所以是循环而不是一次。
    ★ `深度` 有上限：上游链可以成环（A 调 B、B 调 A）—— 上限之下**不会转圈**，
      到了上限还没找到入口就如实标 `到上限`，**不假装找完了**。
    """
    出: dict = {"名字": 名字, "直调": {}, "间接": [], "可达": False, "到上限": False}
    try:
        直 = _扫调用点(源码根, 名字)
        出["直调"] = 直
        if 直.get("调用点"):
            出["可达"] = True
            return 出
        # (模块文件, 要往上找的名字, 链) —— 第一层从**定义模块**里找上游
        当前 = [(f, 名字, []) for f in (直.get("定义在") or [])]
        见过: set[str] = {名字}
        for _层 in range(max(1, int(深度))):
            下一: list[tuple[str, str, list[str]]] = []
            for 文件, 名, 链 in 当前:
                for 上 in _模块内上游(源码根, 文件, 名):
                    if 上 in 见过:
                        continue
                    见过.add(上)
                    扫 = _扫调用点(源码根, 上)
                    if 扫.get("调用点"):
                        出["间接"].append({"经": 上, "调用点": 扫["调用点"],
                                          "链": 链 + [上]})
                        出["可达"] = True
                    else:
                        下一.extend((f2, 上, 链 + [上])
                                   for f2 in (扫.get("定义在") or []))
            当前 = 下一
            if 出["可达"] or not 当前:
                break
        else:
            出["到上限"] = bool(当前)
    except Exception as e:                       # noqa: BLE001 扫不了就如实说
        出["错误"] = f"{type(e).__name__}: {e}"
    return 出


def 闭环接线(源码根: Path | None = None) -> dict:
    """闭环后半段（`验门` / `提为门`）**接进真流程了没有** —— 两个读数，永不抛。

    ★ 为什么不许写死（这一整段的由来）：
      本函数的调用方（体检报告）原来写着一句**写死的现状断言**：
      把闭环后半段（`提为门` / `验门`）一律说成「只有判据在调」。它那时**是对的**，
      但**接线是会被改的**（正在被改）——
      写死的现状断言第二天就变成假话：实测真库台账里已经躺着验证读数，
      而报告还在说「没人调」。反过来更坏：报告说「已接」而其实没接，
      看的人于是**永远不去接**。
    ★ 两个读数**刻意用不同来源**，因为两种接线留下的证据不同：
      · `验门`   ⇒ 读**台账**：它的产物就是台账那一行（`验证`/`判法`/`核对证据`）。
                   有验证读数 = **真跑过、且留下了可复核的行**；没有 = 一次都没走过。
                   （这比「源码里有一处调用」更强：调用点只说「可能跑」。）
      · `提为门` ⇒ 扫**源码 `ast`**：它在台账上留下的痕迹与「人手工改的状态」
                   分辨不开 ⇒ 只能问「有没有真调用点」。注释、文档字符串、
                   定义模块内部调自己、`tests/` 下的调用**都不算真流程接线**。
    """
    出: dict = {"台账行数": 0, "验门": {}, "提为门": {}, "提为门入口": {},
           "源码根": ""}
    try:
        from . import blind_spot as BS
        行s = BS.查(上限=10 ** 6)                    # 全量（`上限` 默认只有 20）
        验 = [r for r in 行s if str(r.get("验证") or "").strip()]
        份 = {"拦得住": 0, "造不出门": 0, "拦不住这一类": 0, "拦得住(推断)": 0}
        来源: list[str] = []
        for r in 验:
            v = str(r.get("验证"))
            if v in 份:
                份[v] += 1
            for s in (r.get("来源们") or []):
                if s and s not in 来源:
                    来源.append(s)
        出["台账行数"] = len(行s)
        出["验门"] = {"读数行数": len(验), "来源": 来源[:3], **份}
    except Exception as e:                       # noqa: BLE001 读不了就说读不了
        出["错误"] = f"{type(e).__name__}: {e}"
    根 = 源码根 if 源码根 is not None else Path(__file__).resolve().parent
    出["源码根"] = str(根)
    try:
        出["提为门"] = _扫调用点(str(根), "提为门")
    except Exception as e:                       # noqa: BLE001
        出["提为门"] = {"错误": f"{type(e).__name__}: {e}"}
    try:
        出["提为门入口"] = _真入口(str(根), "提为门")   # ★ 直调**或**经包装函数
    except Exception as e:                       # noqa: BLE001
        出["提为门入口"] = {"错误": f"{type(e).__name__}: {e}"}
    return 出


def _提为门文本(扫: dict, 入: dict | None = None) -> str:
    """`提为门` 的扫描读数 → **一句照事实说的话**（三档：直调 / 经包装 / 真没接）。

    ★ 为什么是**三档**而不是两档（本轮实测）：真流程接进来时接的常常是**包装函数**
      （`fixloop` 调 `提该加门的`，由它转调 `提为门`）。只有两档的话，那种情况会被
      报成「**还没接** ⇒ 该接进修复环出口」—— **而出口已经接了**，
      那句建议会让人去接一条**已经接上的线**。
      ⇒ 档位必须能表达「**通了，但不是直调**」这一档。
    """
    扫 = 扫 or {}
    入 = 入 or {}
    if 扫.get("错误"):
        return f"`提为门` 接线**读不了**（{扫['错误']}）⇒ 这次结论不完整"
    if 入.get("错误"):
        return f"`提为门` 入口**读不了**（{入['错误']}）⇒ 这次结论不完整"
    点 = 扫.get("调用点") or []
    if 点:
        例 = "、".join(f"{x['文件']}:{x['行']}" for x in 点[:3])
        return f"`提为门` **已接**（扫到 {len(点)} 处直调点：{例}）"
    # ★ 第二档：**通了，但走的是包装函数** —— 本轮实测的真身就是这一档
    间 = 入.get("间接") or []
    if 间:
        说 = "；".join(
            "经 `" + str(x.get("经")) + "`（"
            + "、".join(f"{y['文件']}:{y['行']}" for y in (x.get("调用点") or [])[:2])
            + "）" for x in 间[:2])
        return (f"`提为门` **已接**（没有直调点，但真流程**经包装函数**进来：{说}"
                f" ⇒ **入口是包装函数，不是它本身**）")
    定 = 扫.get("定义在") or []
    尾上 = "（挖到深度上限，链路可能更长）" if 入.get("到上限") else ""
    return ("`提为门` **还没接**（源码里既没有真调用点，也没有走得到它的包装函数"
            f"{尾上} ⇒ 该接进修复环出口；注释、文档字符串"
            + (f"、定义处 {'、'.join(定)} 内部调自己" if 定 else "")
            + "**都不算接线**）")


def _尾接线警(尾: str, 提: dict) -> str:
    """报告里**不许同时出现两句打架的接线话** —— 打架就把冲突指出来（空串=没冲突）。

    ★ 为什么在这里兜：`尾` 是 `blind_spot.闭环概览()['阻断在']` 的文案，它把
      「`提为门` 接线到哪一步」**写死在句子里**。那句话会过期（**正在被改**：
      实测 13:0x 还写「没人调」，13:1x 已改成「已在修复环出口真调」），
      而本行刚用 `ast` 扫过 ⇒ 两边不一致时，报告会同时给出两种说法，
      看的人无所适从、**两句都像真的**。
      ⇒ 不替别人改文案，也**不许静静转述**：把冲突当场说出来。
    ★ 为什么**两个方向都判**：只有单向（说「没人调」而扫到了）能抓的话，
      说反的另一种照样漏 —— 而它更危险：报告说「已接」，
      看的人于是**永远不去接**（本仓反复栽的那个病）。
    ★ 边界如实说：这一段判的是「**报告里写了什么**」（词形识别），
      **不是**「接线通没通」—— 通没通只由 `ast` 判。
      一句话里两个方向都说 ⇒ **判不了就不猜**（猜错比不说更坏）。
    """
    点 = (提 or {}).get("调用点") or []
    if not 尾:
        return ""
    说没接 = ("没人调" in 尾) or ("还没接" in 尾) or ("没有调用点" in 尾)
    说已接 = ("真调" in 尾) or ("已接" in 尾) or ("接进了" in 尾)
    if 说没接 and 说已接:
        return ""                                # 两个方向都说 ⇒ 判不了，不猜
    if 说没接 and 点:
        例 = "、".join(f"{x['文件']}:{x['行']}" for x in 点[:3])
        return (f"  ⚠ 上面「卡在」说『`提为门` 没人调』，与源码扫描**冲突**："
                f"扫到 {len(点)} 处调用点（{例}）⇒ **以扫描为准**，"
                f"那句文案该由 `blind_spot` 侧改")
    if 说已接 and not 点:
        return ("  ⚠ 上面「卡在」说『`提为门` 已在真流程里调』，与源码扫描**冲突**："
                "源码里**一处调用点都没有** ⇒ **以扫描为准**。"
                "**这个方向更危险**：报告说接了、其实没接，看的人**永远不去接**；"
                "那句文案该由 `blind_spot` 侧改")
    return ""


def _接线行(接: dict) -> None:
    """把两个接线读数打成一行（`·` 提示档：接线状态不是环境阻塞，不判红）。"""
    if 接.get("错误"):
        _行("闭环接线", None,
            f"读不了：{接['错误']} ⇒ **这次的接线读数不完整**，别当结论用")
        return
    验 = 接.get("验门") or {}
    台账 = int(接.get("台账行数") or 0)
    读数 = int(验.get("读数行数") or 0)
    if 读数:
        份 = [("拦得住", 验.get("拦得住", 0)), ("造不出门", 验.get("造不出门", 0)),
              ("拦不住这一类", 验.get("拦不住这一类", 0)),
              ("拦得住(推断)", 验.get("拦得住(推断)", 0))]
        细 = " · ".join(f"{k} {v}" for k, v in 份 if v) or "读数全是空值"
        来 = "、".join(验.get("来源") or []) or "行里没记来源"
        验文 = (f"`验门` **已有验证读数 {读数} 行**（{细}）· 来源 {来}"
                f" ⇒ **真流程调过，台账里有可复核的行**")
    elif 台账 == 0:
        验文 = ("`验门` **还没有一条走过**（台账 0 行、一条验证读数都没有 ⇒ "
                "它一次都没被真流程调到；**不是**『跑了没拦住』）")
    else:
        验文 = (f"`验门` **还没有一条走过**（台账 {台账} 行，但**没有一行有验证读数**"
                f" ⇒ 它还没被真流程调到）")
    提 = 接.get("提为门") or {}
    跳过 = len(提.get("跳过") or [])
    _行("闭环接线", None,
        验文 + " · " + _提为门文本(提, 接.get("提为门入口") or {})
        + f" · 扫的是 {接.get('源码根')}（{提.get('扫过', 0)} 个 .py"
        + (f"，跳过 {跳过} 个读不了" if 跳过 else "") + "）")


def _闭环概览() -> dict:
    """`blind_spot.闭环概览()` 的读数 —— 读不了就返回空（**永不抛**）。"""
    try:
        from . import blind_spot as BS
        return BS.闭环概览() or {}
    except Exception:                            # noqa: BLE001
        return {}


def check_blind_loop(源码根: Path | None = None) -> None:
    """盲区闭环体检：**闭环真正闭合了几条**，以及**卡在哪一段**。

    ★ 这个数字比"盲区 N 条"重要得多：盲区条数只说明"发现了问题"，
      而「验过门的」才说明"这类问题**真的有机器判据了**"。
      ⇒ 两个数并列显示，是为了让"看着处理过、其实没验"的那一档**藏不住**。

    ★★★ 2026-11-03 补：**把「卡在哪一段」直接说出来。**

      实测教训（这一条是被自己的误判逼出来的）：
      我看到 `[盲区闭环] 台账 0 条 · 验过拦得住的 0`，第一反应是
      **「缺一次真跑」**。查完调用方才发现**判错了**：
        · `[坐标链] 719 条 · 按种类 {'回执': 719}` ⇒ **真任务跑过很多次**
        · 闭环前半段（`记`）—— 这一段是**读调用方**读出来的：接进了 `fixloop`/`develop`
        · 闭环后半段（`提为门` / `验门`）—— **当时**还没有真流程调用方
      ⇒ 真因是**接线缺失**，不是缺真跑。**两者要处置的动作完全不同**：
        缺真跑 ⇒ 去跑一次；接线缺失 ⇒ 去接上。

      ⇒ 光看四个数字（台账/该加门/没验/验过）**分不出这两种情况**。
        必须把「阻断在哪一段」说出来，看的人才知道下一步该干什么。

    ★★★★★ 2026-11-04：**「接没接」不许是一句回忆 —— 它是两个可复核读数。**
      上面那三行（尤其「闭环后半段还没接」那一句）原本是**写死**的。
      写死的现状断言**第二天就会变成假话**：实测 `验门` 已经接进了真流程、
      真库台账里躺着验证读数，而报告还在说「没人调」。反过来更坏 ——
      报告说「已接」而其实没接，看的人于是**永远不去接**。
      ⇒ 本函数现在**现算**两个读数（见 `闭环接线`），并由 `_判` 保证
        **每一档都打一行 `[闭环接线]`**（早返回也漏不掉）：
          · `验门`   ⇒ 读**台账里有几行 `验证` 非空**（它的产物就是那一行）
          · `提为门` ⇒ 用 `ast` 扫**本包源码的真调用点**（注释/文档字符串不算）
      两个读数都可能「还没走过」/「还没接」—— 那时**必须说出来**，不许沉默
      （「没走过」与「走过但没闭合」的下一步动作完全不同）。
    """
    from . import blind_spot as BS
    st = BS.统计()
    概 = _闭环概览()
    接 = 闭环接线(源码根)     # ★ 接线状态**现算**，见该函数的 docstring
    该 = st.get("该加门", 0)
    没验 = len(BS.没验门的())
    验过 = len(BS.验过门的())
    # ★ 2026-11-03（第225轮）：**「推断拦得住」单列** —— 它不进 `验过` 的计数。
    #   理由：`验过门的()` 是"闭环闭合"的**唯一凭据**。
    #   把"我分析模式推断它不乱拦"也算进去，就等于**把推断当实测用**——
    #   而本仓栽过一次这个跟头（`chief.py` 的"假阳性契约"：给没跑过的门发合格证）。
    #   ⇒ 单列，并且**明写"不算闭合"**。
    推断 = len(BS.推断拦得住的())
    # ★ 2026-11-04（第229轮）：**「造不出门」单列并降级 `_行` 的严重性。**
    #   实测（真跑修复环）这一档的真身是：`自动验门` **真跑了**，
    #   结论 `验证=造不出门 · 判法=无门可验`（KB 里没有对应规则 ⇒ 转不出门）。
    #
    #   ★ 为什么不跟「该加门」混着报：这两档**下一步动作完全不同** ——
    #     · 该加门    ⇒ 去调 `提为门`
    #     · 造不出门  ⇒ 去**攒知识/找判据**（调 `提为门` 没用，没门可提）
    #     混着报，看的人会白干一轮。这正是本轮反复在治的那个病：
    #     **一个较弱的结论不许借用较强的名字**（这里反过来：**"没门" 不许借 "该提门" 的名字**）。
    造不出 = int(概.get("造不出门", 0) or 0)
    # ★★★★★ 2026-11-04（第229轮）：**「拦不住这一类」单列，且排在 `造不出门` 之前。**
    #
    #   这一档的真身：门**写出来了、也真验了**，试件命中、假阳性件也放行 ——
    #   但**它的模式抄了证据原文**，换一次（第 88 行 → 第 89 行）就漏。
    #
    #   ★ 为什么必须排在最前：**它最容易被误当成"已经通了"。**
    #     旧算法正是给它发 `拦得住(实测)` 并算作闭环闭合的 ——
    #     界面上会显示成"验过拦得住的 ≥1"，而那条门是废的。
    #     这是本仓"假阳性契约"的又一实例：**给没用的东西发了合格证**。
    #
    #   ⇒ 下一步动作也不同：`没拦住` 是整道换，`拦不住这一类` 是**把模式
    #     从"这一次"提到"这一类"**（就近改，不是重写）。
    类废 = int(概.get("拦不住这一类", 0) or 0)
    阻断 = str(概.get("阻断在") or "")
    尾 = (f" · **卡在**：{阻断}" if 阻断 else "")
    推文 = (f" · 推断拦得住的 {推断}（**不算闭合**）" if 推断 else "")

    def _判(ok: bool, 文本: str) -> None:
        """`盲区闭环` 那一行 + **接线读数行**（两行一起出）。

        ★ 为什么包一层：下面每一档都是**早返回** —— 接线读数必须
          **每一档都跟着出**，否则「这一档说不说接线」取决于走了哪个分支，
          而那是最难发现的一类漏洞（本仓的病：**静默的缺口**）。
        """
        _行("盲区闭环", ok, 文本 + _尾接线警(尾, 接.get("提为门") or {}))
        _接线行(接)

    if 类废:
        _判(False,
            f"**{类废} 条的门只拦得住那一次**（模式抄了证据原文，换个数字就漏）"
            f"· 台账 {st.get('条数')} 条 · 验过拦得住的 {验过}"
            f" ⇒ 该把模式从「这一次」提到「这一类」，"
            f"**不是**整道换掉" + 尾)
        return
    if 造不出:
        # 这一档**不是"有活等干"** —— 它是"**缺知识**"，所以报成 True（不是未完成）。
        _判(True,
            f"台账 {st.get('条数')} 条 · **{造不出} 条造不出门**"
            f"（真验过了，结论是**没有门可提**）· 验过拦得住的 {验过}"
            f" ⇒ 缺的是**知识/判据**，不是缺一次 `提为门`" + 尾)
        return
    if 该:
        _判(False,
            f"**{该} 条记下了还没提为门**（台账共 {st.get('条数')} 条）"
            f" · 没验门的 {没验} · 验过的 {验过}" + 推文 + 尾)
        return
    _判(True,
        f"台账 {st.get('条数')} 条 · 该加门 {该} · "
        f"**验过拦得住的 {验过}** · 已加门但没验 {没验}" + 推文
        + ("  ⇒ 闭环尚未闭合过（没有一条门被真验证过）"
           if 验过 == 0 else "") + 尾)


def _播种() -> int:
    """`doctor --播种`：把**随包的知识种子**装进运行期知识库目录。

    ★ 为什么这件事归 doctor：doctor 的定位就是"让这台机器可以开工"，
      而"外挂知识库是空的"是一个**不会报错、不会让门变红**的开不了工状态
      （本轮实测：库 0 条、注入空串、doctor 照样说 ✓ 可以开工）。
      所以补一个显式入口，把"装上"变成一条命令，而不是一段文档。
      部署机上没有仓、只有装好的包 —— 种子在包内（`kb_seed/`）⇒ 这条命令在哪都能用。

    不覆盖人的修改（默认只补缺失），因为"人可改"是这个库的硬要求。
    """
    from . import project_kb as K
    print(f"种子（随包）= {K.内建种子()}")
    print(f"运行期库   = {K.库根() / 'items'}")
    r = K.播种()
    if r.get("说明"):
        print(f"✗ {r['说明']}")
        return 1
    if r["是同一处"]:
        print("· 运行期库就是随包种子（ORCH_KB_DIR 指过去了）⇒ 无需播种")
    else:
        print(f"· 新增 {r['新增']} · 相同 {r['相同']} · 有差异 {r['差异']}"
              + ("（差异项未动：人的修改优先；要强制对齐用 tools/kb_seed.py --force）"
                 if r["差异"] else ""))
        print(f"· 已写入 {r['已写']} 条"
              + (f" · 重建索引 {r.get('重建索引')}" if r.get("已写") else ""))
    st = K.统计()
    print(f"✓ [知识库] {st.get('条数')} 条 · 按类别 {st.get('按类别')} · 可机判门 {st.get('可机判门')}")
    return 0


def main() -> int:
    # ══════════════════════════════════════════════════════════════════════
    # ★ N2：**非 win32 显式拒绝**（2026-11-18）。
    #   以前这里是静默往下走 —— 在 Linux 上"跑完了但每条结论都不可信"，
    #   比"直接拒绝"坏得多：下游会把它当成一次合格交付。
    #   本工程依赖 Windows 专有行为的清单（都在实测里踩过）：
    #     · 临时目录降级（系统 TEMP 全不可写时的落点）
    #     · 只读属性清位（git worktree 拆树）
    #     · `DETACHED_PROCESS`（后台子进程）
    #     · `os.kill(pid, 0)` 在 DETACHED 下失效（进程存活判断）
    #   返回码 **67**：与 64 python / 65 profile / 66 channel 都分开。
    # ══════════════════════════════════════════════════════════════════════
    if sys.platform not in ("win32", "cygwin"):
        print(f"✗ 本平台未验，不承诺可用：sys.platform={sys.platform!r}")
        print("  ⇒ 本工程只在 **win32** 上证过（临时目录降级 / 只读清位 / "
              "DETACHED_PROCESS / os.kill(pid,0) 都依赖 Windows 行为）。")
        print("  ⇒ doctor **拒绝给出结论**（返回 67），不做静默降级。")
        return 67
    from ._winutf8 import 确保UTF8输出 as _u8; _u8()
    # ★ R540：`--help` 必须**立刻返回**。不认它时敲 `--help` 会把**整份体检**
    #   跑一遍（实测 160 秒、退出码 70），用户看到的是"帮助命令报错了"。
    if any(x in sys.argv[1:] for x in ("-h", "--help", "帮助")):
        print("用法：orch-doctor [-h|--help] [--播种 | --重建链 <回执目录>]\n"
              "\n"
              "  机器体检：一条命令回答「这台机器上这层能不能开工」。\n"
              "\n"
              "  （无参数）        跑全部体检项并逐项给证据\n"
              "  --播种            把内置的项目知识条目写进知识库（外挂知识不再空转）\n"
              "  --重建链 <目录>   从回执目录重建坐标链\n"
              "  --导出状态 <zip>  把状态（任务库/成本账/产物/工作目录）打成可迁移 zip；\n"
              "                   带清单.json（每文件 sha256）⇒ 换机器接手用这个\n"
              "  --导入状态 <zip>  核对清单后导入；任何一条 sha256 对不上 ⇒ **整包拒收**；\n"
              "                   默认不覆盖已有文件，并写 jobs/续跑来源.json\n"
              "  --状态根 <目录>   上面两条用它指定状态根（默认 profile.state_dir()，验夹具用）\n"
              "  -h/--help         本帮助（立刻返回）\n"
              "\n"
              "  退出码：0 可以开工 · 64 装坏了 · 65 档案有问题 · 66 无可用通道 · 70 其它\n"
              "  ★ 边界：doctor 不联网，通道探活发生在真跑时。")
        return 0
    if "--播种" in sys.argv:
        return _播种()
    if "--重建链" in sys.argv:
        i = sys.argv.index("--重建链")
        if i + 1 >= len(sys.argv):
            print("用法：doctor --重建链 <回执目录>")
            return 2
        return _重建链(sys.argv[i + 1])
    # ── N1/R557：状态 bundle（跨机器接手）──
    根 = None
    if "--状态根" in sys.argv:
        _i = sys.argv.index("--状态根")
        if _i + 1 >= len(sys.argv):
            print("用法：--状态根 <目录>")
            return 2
        根 = sys.argv[_i + 1]
    if "--导出状态" in sys.argv:
        _i = sys.argv.index("--导出状态")
        if _i + 1 >= len(sys.argv):
            print("用法：orch-doctor --导出状态 <zip> [--状态根 <目录>]")
            return 2
        from . import state_bundle as _sb
        r = _sb.导出(sys.argv[_i + 1], 根)
        print(f"✓ 已导出 {r['文件数']} 个文件 · {r['总字节']:,} B → {r['zip']}")
        print(f"  zip sha256 = {r['sha256']}")
        print("  ★ 换机器接手：`orch-doctor --导入状态 <zip>`；它会先核对清单再落盘。")
        return 0
    if "--导入状态" in sys.argv:
        _i = sys.argv.index("--导入状态")
        if _i + 1 >= len(sys.argv):
            print("用法：orch-doctor --导入状态 <zip> [--状态根 <目录>]")
            return 2
        from . import state_bundle as _sb
        r = _sb.导入(sys.argv[_i + 1], 根, 覆盖=("--覆盖" in sys.argv))
        if r["拒绝"]:
            print(f"✗ 拒收：{r['拒绝']}")
            return 3
        print(f"{'✓' if not r['失败'] else '✗'} 导入 {len(r['写入'])} 个文件 · 跳过 {len(r['跳过'])}"
              f" · 失败 {len(r['失败'])}")
        for x in r["失败"][:5]:
            print("   ✗", x)
        print(f"  续跑来源：{r['续跑来源']}")
        return 0 if not r["失败"] else 3
    print("═══ orchestrator doctor ═══")
    # ★ 2026-11-03：体检项**逐个隔离**，任何一项自己崩了也只能变成一行红，
    #   不许把整次体检、以及后面所有检查项一起吞掉。现场：`check_tempdir()` 里
    #   `tempfile.gettempdir()` 抛 FileNotFoundError ⇒ doctor 退出码 1、**一行报告都没有**，
    #   人看到的结论是"doctor 坏了"而不是"临时目录坏了"。
    #   理由同本文件里反复出现的那条：**只读工具崩掉，比返回"这里读不了"更糟**。
    #   注意这里只兜异常**不许**兜成通过 —— 崩了的项一律记成红，并明说"这次结论不完整"。
    检查们 = [
        ("python", check_python), ("package", check_package), ("deps", check_deps),
        ("索引降级", check_indexdegrade), ("知识库可机判", check_kbpower),
        ("状态根", check_state_root),
        ("tmpdir", check_tempdir), ("pointers", check_pointers), ("profile", check_profile),
        ("channels", check_channels), ("mcp-names", check_mcp_names), ("tools", check_tools),
        ("知识库", check_project_kb), ("坐标链", check_coord_chain),
        ("四方联动", check_linkage), ("分工线", check_dispatch),
        ("事件驱动", check_events), ("回执格式", check_receipt_fmt),
        ("语义入口", check_context_view), ("盲区闭环", check_blind_loop),
        ("semsearch", check_semsearch),
    ]
    for _名, _f in 检查们:
        try:
            _f()
        except Exception as _e:                  # noqa: BLE001
            _行(_名, False, f"**这项自己崩了**（{type(_e).__name__}: {_e}）"
                            " ⇒ 体检未做完，别把这次输出当完整结论")
    print()
    if _硬阻塞:
        rc = _退出码()
        print(f"✗ 不可开工：{len(_硬阻塞)} 项硬阻塞（退出码 {rc}）")
        for x in _硬阻塞:
            print(f"  - {x}")
        print("  退出码对照：0 可以开工 · 64 装坏了 · 65 档案有问题 · 66 无可用通道 · 70 其它")
        return rc
    print("✓ 可以开工（通道探活发生在真跑时；doctor 不联网）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
