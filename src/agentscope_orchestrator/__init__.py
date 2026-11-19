"""多智能体编排器 · 通用工程开发工具。

★ 为什么要这个文件（2026-10-30 真实部署时发现）：
  本包此前**没有 `__init__.py`**，于是它是一个**命名空间包**。
  在源码树里靠"所有导入都是子模块导入"侥幸能跑，所以 1075 点自检一直全绿；
  但一旦真装进 site-packages，三个后果立刻出现：
    ① `agentscope_orchestrator.__file__` 为 None ⇒ 取包路径的代码会崩
       （`gateway._找配置目录()` 正是靠 `__file__` 定位配置，装完找不到）；
    ② `__version__` 取不到 ⇒ 外部无法判断装的是哪个版本；
    ③ 若 sys.path 上还有另一个同名目录，两者的模块会**静默合并** ——
       这种错误不会报错，只会让行为变得无法解释。
  ⇒ 这是"源码里对、装完不对"的又一例：只有真装一遍才会现形。
"""

# ★ 版本号**单一来源**（2026-10-31 交付审计 P0-2）：
#   此前这里写死 "0.1.0"，而 pyproject 是 "0.12.3"、交付说明标题是 "v0.3.1" ——
#   三套版本并存，调用方拿 `__version__` 判版本会得到 0.1.0，出问题无法对齐"跑的是哪版"。
#   现在只有一个源：
#     ① 源码布局（`<仓>/src/<包>/`）⇒ 读本仓 pyproject.toml（改了立刻生效，不受旧安装干扰）；
#     ② 安装布局 ⇒ 问分发元数据（wheel 里 pyproject 已被消费，元数据才是真源）。
#   ★ 为什么不能只靠 `importlib.metadata`：源码树的 venv 里常装着**旧版本**，
#     元数据会返回那个旧号 —— 实测过（源码 0.12.4 时它报 0.1.0）。
def _版本() -> str:
    from pathlib import Path
    try:
        包 = Path(__file__).resolve().parent
        src = 包.parent
        if src.name == "src" and (src.parent / "pyproject.toml").is_file():
            import tomllib
            with (src.parent / "pyproject.toml").open("rb") as _fp:
                return str(tomllib.load(_fp)["project"]["version"])
    except Exception:                        # noqa: BLE001
        pass
    try:
        from importlib.metadata import version as _dist_version
        return _dist_version("agentscope-orchestrator")
    except Exception:                        # noqa: BLE001
        return "0.0.0+unknown"


__version__ = _版本()

# 对外只暴露版本与包用途；子模块按需显式导入，避免 `import agentscope_orchestrator`
# 时把 httpx/mcp 等重依赖一并拉起。
# ★★★ 临时目录护栏（2026-11-07 真事故）
#   本机 `tempfile.gettempdir()` **回落到 cwd**（= 包根），于是每一次 `mkdtemp()`
#   都在仓里造一个目录。实测代价：包根下积了 **4,736 个**直接子目录，
#   其中 3,739 个随机名的合计 **1,309 MB**（>24h 的已清理），工位残留 1,749 → 1,807。
#
#   根因**不是环境变量没设**（TMPDIR/TEMP 都设了、目录也在），而是**探针本身**：
#       os.open(不可写目录)        ->  立刻 PermissionError
#       tempfile.mkstemp(dir=…)    ->  **挂住 > 6 秒**（连 C:\Windows\Temp 也一样）
#   `gettempdir()` 依次试 10 个候选、每个都慢死，最后只能回落到候选表末项 cwd。
#   ⇒ 必须在**任何人调用 gettempdir 之前**钉住它，所以放在包导入时。
#
#   ★ 为什么在 `__init__` 里做副作用：这是**唯一能保证先于一切**的位置 ——
#     MCP 服务、CLI、自检、真工具链子进程全都先 `import` 本包。
#   ★ 绝不抛异常：护栏挂了只许让临时目录不受控，**不许让整个包 import 失败**。
def _钉临时目录() -> None:                      # noqa: D401
    try:
        from . import temp_guard as _tg
        果 = _tg.钉住()
        if 果.get("成功"):
            if 果.get("降级"):                   # 降级要看得见；正常时保持安静
                import sys as _sys
                print(f"[orchestrator] 临时目录降级 -> {果['钉到']}"
                      f"（{果['为何']}）", file=_sys.stderr)
        else:
            import sys as _sys
            print(f"[orchestrator] ⚠ 临时目录**没有钉**：{果.get('为何')}",
                  file=_sys.stderr)
    except Exception:                            # noqa: BLE001
        pass


_钉临时目录()


__all__ = ["__version__"]

