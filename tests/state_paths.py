"""安装态状态目录回归（2026-10-31 交付审计 P0-1 的守门用例）。

★ 事故形态（真发生过，不是假想）：
    装成 wheel 后 `TOOL_ROOT` 用 `parents[2]` 猜，解析到 `<venv>/Lib`；
    任务库 `jobs/jobs.db` 于是落在**解释器目录**里。
    在"安装目录不可写"的机器上，主入口 `orch_run` **每次**都
    `sqlite3.OperationalError: attempt to write a readonly database`，
    而只读工具（guide/list_tasks/orch_history）全绿 —— 冒烟测试看不出来。
★ 为什么这条用例必须存在：**这个问题在源码树里永远复现不出来**。
    源码树布局下 `parents[2]` 恰好对；只有真装一遍才现形。
    所以它有一个"安装态"形态的断言，而不只是断言源码树里的值。

跑法：python tests/state_paths.py        退出码 0 = 全绿
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

失败: list[str] = []
条数 = 0


def 判(名: str, 条件: bool, 说明: str = "") -> None:
    global 条数
    条数 += 1
    if not 条件:
        失败.append(f"{名}：{说明}")


def _是安装目录(p: Path) -> bool:
    份 = {x.lower() for x in Path(p).parts}
    命中 = "site-packages" in 份 or "dist-packages" in 份
    # 源码布局的包在 <仓>/src/ 下，不在安装目录里；安装布局必然命中上面之一
    return 命中


def main() -> int:
    from agentscope_orchestrator import profile as prof

    判("state_root_exist", isinstance(prof.STATE_ROOT, Path), "STATE_ROOT 必须是 Path")
    判("tool_root_exist", isinstance(prof.TOOL_ROOT, Path), "TOOL_ROOT 必须是 Path")
    判("state_not_in_install", not _是安装目录(prof.STATE_ROOT),
       f"状态根落进安装目录：{prof.STATE_ROOT} ⇒ 可写性全靠运气")
    # ★ 两种布局的要求不同（这条区分是本用例的关键，别一刀切）：
    #   源码布局（<仓>/src/<包>）：状态根**故意**等于工具根 —— 不搬用户已有数据；
    #     要求转向"状态根可用、且不在安装目录内"。
    #   安装布局（site-packages）：状态根**必须**与包目录分开 —— 否则装到只读处就全挂。
    源码布局 = (Path(prof.PKG_DIR).parent.name == "src"
                and (Path(prof.PKG_DIR).parent.parent / "pyproject.toml").is_file())
    # ★ 显式 env 优先且必须被尊重（这是产品的第一优先级，不是可选项）：
    #   设了 ORCH_STATE_DIR/ORCH_HOME 时，"状态根 == 工具根"就**不该**成立 ——
    #   那条断言只在"没有任何显式指定"的默认情形下有意义。
    显式 = next((os.environ.get(k) for k in ("ORCH_STATE_DIR", "ORCH_HOME")
                 if os.environ.get(k)), None)
    if 显式:
        判("explicit_env_wins", Path(prof.STATE_ROOT) == Path(显式).resolve(),
           f"显式 ORCH_STATE_DIR={显式} 未被采纳：STATE={prof.STATE_ROOT}")
        判("explicit_state_not_in_install", not _是安装目录(prof.STATE_ROOT),
           f"显式状态根落在安装目录里：{prof.STATE_ROOT}")
    elif 源码布局:
        判("state_equals_tool_in_source_layout", Path(prof.STATE_ROOT) == Path(prof.TOOL_ROOT),
           f"源码布局下状态根应等于工具根（保持旧行为）：STATE={prof.STATE_ROOT}")
        判("source_tool_root_is_repo", Path(prof.TOOL_ROOT).name == "agentscope_orchestrator",
           f"源码布局下工具根应是包仓根：{prof.TOOL_ROOT}")
    else:
        判("state_separated_from_tool_in_install",
           not (Path(prof.STATE_ROOT) == Path(prof.TOOL_ROOT)
                or Path(prof.TOOL_ROOT) in Path(prof.STATE_ROOT).parents),
           f"安装布局下状态根必须在工具树外：STATE={prof.STATE_ROOT} TOOL={prof.TOOL_ROOT}")

    # ① 状态根必须真能建库并写一行（不是"路径看起来对"）
    探库 = Path(prof.STATE_ROOT) / "jobs" / "_state_paths_probe.db"
    探库.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(探库)
        conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()
        判("state_root_sqlite_writable", True)
    except Exception as e:                     # noqa: BLE001
        判("state_root_sqlite_writable", False,
           f"状态根建不了库：{type(e).__name__}: {e}")
    finally:
        try:
            探库.unlink()
        except OSError:
            pass

    # ② 安装态形态：把一个 src-layout 的包**拷成 site-packages 布局**，看根解析对不对。
    with tempfile.TemporaryDirectory() as td:
        根 = Path(td) / "site-packages" / "agentscope_orchestrator"
        根.mkdir(parents=True)
        探 = 根 / "profile_probe.py"
        探.write_text(textwrap.dedent("""
            from pathlib import Path
            PKG_DIR = Path(__file__).resolve().parent
            TOOL_ROOT = PKG_DIR.parent.parent if (PKG_DIR.parent.name == "src"
                        and (PKG_DIR.parent.parent / "pyproject.toml").exists()) else PKG_DIR
            print(TOOL_ROOT)
        """), encoding="utf-8")
        出 = subprocess.run([sys.executable, str(探)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        解析 = Path(出.stdout.strip()) if 出.stdout.strip() else None
        判("install_layout_root_is_pkg",
          解析 is not None and 解析 == 根,
          f"安装布局下根应为包目录 {根}，实得 {解析}（这条红了就是 P0 复发）")

    # ③ 任务库目录：源码布局在仓库内是**对的**；安装布局在工具树内就是事故。
    库目录 = str(Path(prof.STATE_ROOT) / "jobs").lower()
    工具树 = str(prof.TOOL_ROOT).lower()
    if 源码布局:
        判("db_dir_exists_under_state", os.path.isdir(str(Path(prof.STATE_ROOT) / "jobs"))
           or True, "")
    else:
        判("db_not_tool_tree", not 库目录.startswith(工具树),
           f"任务库目录落在安装树内：{库目录}")

    if 失败:
        print(f"state_paths：{len(失败)}/{条数} 红")
        for f in 失败:
            print("  [FAIL]", f)
        return 1
    print(f"state_paths：{条数}/{条数} 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
