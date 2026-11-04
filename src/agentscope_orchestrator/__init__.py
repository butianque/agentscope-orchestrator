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
__all__ = ["__version__"]

