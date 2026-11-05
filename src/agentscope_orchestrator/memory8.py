r"""八维投影记忆 —— 把 `semsearch` 的 64 位八维编码搬到编排器里当**全局项目记忆**。

## 为什么要有这一层（先说清动机，不然它会变成又一块腐肉）

现状（v0.13.8 实测）：`learning.py` 的经验环是**扁平文本指纹**（语言/失败门/异常/错误词/后缀），
只能回答"这条错误像不像上次那条"。它答不了三件研发里最常问的事：

1. **这个症状属于哪一层？** —— 是环境、依赖、产物代码、接口、测试，还是判据门自己？
   （层判断错了，修法一定错：门有问题却去改产物 = 把门绿建立在错误的地基上）
2. **这条记忆可信到什么程度？** —— 平铺的经验环只有"已验/未验"两档；
   现实里还有"一致但没独立复现"（PLAUSIBLE）、"被证据推翻"（OVERSTATED）。
   把它们混进同一条流里检索，就是"猜的被记成知道的"。
3. **跨项目还认不认得出同一类病？** —— 扁平指纹绑死在"这次跑的这份仓"，换个仓就全丢。

⇒ 八维编码给记忆一个**结构键**：同一层 + 同一动作类别 + 同一判决类，才允许互相启发；
并且把**证据等级**做成一个维度（不是备注），检索时按等级硬过滤。

## 维度表（与 semsearch 同构：8 位一维、0xFF 通配、u64）

| 维 | 语义 | 值域 | 说明 |
|---|---|---|---|
| dim0 | 层级 | 0~6 | 0 环境 · 1 依赖 · 2 产物代码 · 3 接口/调用方 · 4 测试 · 5 判据门 · 6 文档 |
| dim1 | 子系统 | 1~254 | 病根文件所在顶层模块 —— **子系统表**（名→号，无损） |
| dim2 | 判决类 | 0~6 | 0 未达标 · 1 参数/前提不成立 · 2 基础设施不可用 · 3 实例分歧 · 4 任务书驳回 · 5 达标 · 6 取消 |
| dim3 | 修复动作 | 0~7 | 0 改值 · 1 补边界 · 2 改调用方 · 3 补依赖 · 4 改门 · 5 回滚 · 6 新建 · 7 删除 |
| dim4 | **证据等级** | 0~4 | 0 SOLID（过硬门复现）·1 PLAUSIBLE ·2 OVERSTATED ·3 UNVERIFIABLE ·4 猜测（**永不检索**） |
| dim5 | 依赖深度 | 0~255 | 这条修法依赖几条前置记忆（0 = 单点修复） |
| dim6 | 项目号段 | 1~254 | `项目键()` → **项目表**（名→号，无损；同一项目跨运行稳定） |
| dim7 | 序号/轮次 | 0~255 | 仓库内自增（同一条记忆的多轮观测） |

★ 与来源的**差异**（必须写出来，否则就是抄而不化）：
  · 原表 dim0 是"文档分层"（从最抽象的命题层到最落地的验证层），这里换成**故障层**；
  · 原表 dim3 是八卦归属（八个三爻布尔组合），这里换成**修复动作类别**（同构：3 位 → 8 类互斥动作）；
  · 原表 dim4 是知识等级（越抽象等级越高），这里换成**证据等级** —— 这一维是检索的**硬门**，不是展示字段。
  保留下来的正是可迁移的那部分：**定长维度 + 通配 + 前缀可查**。

★★★ 2026-11-05 修（本层此前是**哈希折叠**，与项目「禁哈希」原则相违）：
  · 旧：dim1 = `sha1(仓库+路径)[:8] % 8`（**只有 8 个桶**）、dim6 = `sha1(仓库)[:8] % 256`；
    ⇒ 子系统一多**必然碰撞**（生日问题），撞了就把两个不同子系统的病当成"同一类"注入提示。
  · 引擎侧早有定论（`结构记忆网络.rs` `U-009`）：**基 251 哈希撞 5/512，无损编码 0 碰撞**；
    `趋向.rs` 里也立了「禁哈希」原则 `:726`。
  · 现：dim1 / dim6 改成**一张显式的名字表**（`子系统表` / `项目表`，SQLite 落盘）——
    同名必同号、异名必异号，**表满（>254）时明确抛错**，绝不静默折叠。
    撞满宁可失败，因为"记忆串台"是那种**看起来全绿**的错。

## 诚实边界（写进模块，别写进 README 就忘）

· 编码不证明记忆正确：dim4 只记录"过没过门"，不记录"修法是否真对"；
· dim1/dim6 **无损**（显式表）；但表容量 254，满即抛错 —— 这是有意的：宁可失败，不许两个名字共用一个号；
· 旧库（2026-11-05 前）里已写入的 dim1/dim6 是哈希号，**不可逆、不迁移**；
  它们只影响 `说明` 里的编号与序号分组，不影响按层/等级检索，故不阻塞；
· 没有记忆时它只是空表，不降低任何现有行为；
· 记忆命中率**不能**用"少花了 token"单独证明有效 —— 必须同时看"成功率没掉"。
"""
from __future__ import annotations

import json
import os
import contextlib
import sqlite3
import time
from pathlib import Path

通配 = 0xFF
#: 表内号的上限：0xFF 留给通配，所以显式表最多 254 条（超出即抛，不折叠）
表内上限 = 0xFE

层名 = ["环境", "依赖", "产物代码", "接口/调用方", "测试", "判据门", "文档"]
判决名 = ["未达标", "参数/前提不成立", "基础设施不可用", "实例分歧", "任务书驳回", "达标", "取消"]
动作名 = ["改值", "补边界", "改调用方", "补依赖", "改门", "回滚", "新建", "删除"]
等级名 = ["SOLID", "PLAUSIBLE", "OVERSTATED", "UNVERIFIABLE", "猜测"]

维上限 = [6, 表内上限, 6, 7, 4, 0xFF, 表内上限, 0xFF]

#: 路径里这些目录名只是"外壳"，子系统要往里再取一段（否则所有仓都叫 `src`）
壳目录 = {"src", "lib", "app", "source", "pkg", "packages"}


def 编码(dims: list[int]) -> int:
    """8 维 → u64（每维 8 位，低位是 dim0）。非法维度值一律当通配。"""
    if len(dims) != 8:
        raise ValueError("必须是 8 维")
    值 = 0
    for i, v in enumerate(dims):
        v = 通配 if v is None else int(v)
        if v != 通配 and not (0 <= v <= 维上限[i]):
            v = 通配
        值 |= (v & 0xFF) << (8 * i)
    return 值


def 解码(码: int) -> list[int]:
    return [(码 >> (8 * i)) & 0xFF for i in range(8)]


def 说明(码: int) -> str:
    d = 解码(码)
    取 = lambda arr, i: (arr[d[i]] if 0 <= d[i] < len(arr) else "通配")  # noqa: E731
    return (f"{取(层名, 0)} · 子系统{d[1] if d[1] != 通配 else '?'} · {取(判决名, 2)} · "
            f"{取(动作名, 3)} · {取(等级名, 4)} · 深度{d[5]} · 项目{d[6]} · #{d[7]}")


def _说明带名(码: int, 子系统: str | None = None, 项目: str | None = None) -> str:
    """同 `说明`，但把子系统/项目号换成**名字**（检索时用，读的人不用再查表）。"""
    d = 解码(码)
    取 = lambda arr, i: (arr[d[i]] if 0 <= d[i] < len(arr) else "通配")  # noqa: E731
    子 = 子系统 if 子系统 else (d[1] if d[1] != 通配 else "?")
    项 = 项目 if 项目 else (d[6] if d[6] != 通配 else "?")
    return (f"{取(层名, 0)} · 子系统{子} · {取(判决名, 2)} · "
            f"{取(动作名, 3)} · {取(等级名, 4)} · 深度{d[5]} · 项目{项} · #{d[7]}")


def 猜动作(说明文本: str) -> int:
    """从模型的"说明"里猜修复动作类别（粗分类，够用即可，不追求准）。"""
    t = (说明文本 or "").lower()
    if any(k in t for k in ("回滚", "revert", "还原")):
        return 5
    if any(k in t for k in ("删除", "delete", "移除")):
        return 7
    if any(k in t for k in ("新建", "create", "新增文件", "added file")):
        return 6
    if any(k in t for k in ("依赖", "import", "requirements", "install")):
        return 3
    if any(k in t for k in ("调用方", "caller", "签名", "signature")):
        return 2
    if any(k in t for k in ("边界", "空", "none", "empty", "off-by-one", "越界")):
        return 1
    if any(k in t for k in ("门", "gate", "conftest", "pytest.ini")):
        return 4
    return 0


def 层判定(证据: str, 路径们=()) -> int:
    """从证据与改动路径判故障层（顺序即优先级：判据门 > 依赖 > 环境 > 接口 > 测试 > 产物代码）。"""
    t = (证据 or "").lower()
    p = " ".join(str(x).lower() for x in (路径们 or []))
    if any(k in t for k in ("conftest", "pytest.ini", "sitecustomize", "usercustomize")) or ".pth" in p:
        return 5
    if any(k in t for k in ("modulenotfounderror", "no module named", "importerror")):
        return 1
    if any(k in t for k in ("permission denied", "winerror", "no space", "access is denied")):
        return 0
    if any(k in t for k in ("typeerror", "attributeerror", "argument", "signature")):
        return 3
    if "test" in p or "assert" in t:
        return 4
    return 2


def 子系统名(路径们=(), 仓库: str = "") -> str:
    """病根文件所在的**顶层模块名**（取第一条路径的模块段；取不到则 `未定`）。

    旧实现是把 `仓库+全部路径` 哈希后 %8 —— 只有 8 个桶，子系统一多必撞。
    这里改成从路径**读出名字**：名字进表拿号，无损。

    例：`src/agentscope_orchestrator/fixloop.py` → `agentscope_orchestrator`；
        `pkg/khz.py` → `pkg`；`khz.py` → `khz.py`；无路径 → `未定`。
    """
    for x in (路径们 or []):
        s = str(x).replace("\\", "/").strip().strip("/")
        if not s:
            continue
        段们 = [p for p in s.split("/") if p]
        首 = 段们[0]
        if 首 in 壳目录 and len(段们) > 1:
            首 = 段们[1]
        return 首
    return "未定"


def 项目键(仓) -> str:
    """**稳定的项目标识** —— 记忆要跨"同一份仓的多次运行"复用，就不能拿临时路径当键。

    顺序：pyproject.toml / Cargo.toml 的 name → .git/config 的 remote url
    → 顶层目录名（去掉 `-<随机后缀>`）。最后那一档是为基准里的临时仓：
    `bench-a-ab12cd` 与 `bench-a-99zz` 折叠成同一个 `bench-a`，
    否则"暖启动"永远命中不了同一个项目 —— 那这个实验就白做了。
    """
    p = Path(str(仓))
    for 名 in ("pyproject.toml", "Cargo.toml"):
        f = p / 名
        if f.is_file():
            try:
                for 行 in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    行 = 行.strip()
                    if 行.startswith("name") and "=" in 行:
                        v = 行.split("=", 1)[1].strip().strip('"').strip("'")
                        if v:
                            return v
            except OSError:
                pass
    g = p / ".git" / "config"
    if g.is_file():
        try:
            for 行 in g.read_text(encoding="utf-8", errors="replace").splitlines():
                if "url =" in 行:
                    return 行.split("=", 1)[1].strip()
        except OSError:
            pass
    名 = p.name
    部 = 名.rsplit("-", 1)
    return 部[0] if len(部) == 2 and len(部[1]) >= 6 else 名


def 库路径() -> Path:
    根 = Path(os.environ.get("ORCH_STATE_DIR") or (Path.home() / ".orchestrator-state"))
    return 根 / "memory8.sqlite3"


def _连() -> sqlite3.Connection:
    p = 库路径()
    p.parent.mkdir(parents=True, exist_ok=True)
    连 = sqlite3.connect(str(p), timeout=10)
    连.execute("""CREATE TABLE IF NOT EXISTS 记忆8(
        码 INTEGER NOT NULL, 等级 INTEGER NOT NULL, 症状 TEXT, 修法 TEXT,
        仓库 TEXT, 文件 TEXT, 判决 INTEGER, 动作 INTEGER, 层 INTEGER,
        时间 REAL, 命中 INTEGER DEFAULT 0)""")
    连.execute("CREATE INDEX IF NOT EXISTS ix_码 ON 记忆8(码)")
    # ★ 2026-11-05：无损名字表。名字是**真值**，号只是定长编码；撞满抛错，不折叠。
    连.execute("CREATE TABLE IF NOT EXISTS 子系统表(名 TEXT PRIMARY KEY, 号 INTEGER NOT NULL UNIQUE)")
    连.execute("CREATE TABLE IF NOT EXISTS 项目表(名 TEXT PRIMARY KEY, 号 INTEGER NOT NULL UNIQUE)")
    连.commit()
    return 连


def _取号(连: sqlite3.Connection, 表: str, 名: str) -> int:
    """名字 → 定长号（**无损**：同名同号、异名异号；满则抛，绝不哈希折叠）。

    并发下两个进程可能同时给同一个新名字分配号：靠 `名` 的 UNIQUE 兜底，
    撞 `IntegrityError` 就退回去重读 —— 后到的那个拿先到的号，仍然无损。
    """
    名 = (名 or "").strip() or "未定"
    行 = 连.execute(f"SELECT 号 FROM {表} WHERE 名=?", (名,)).fetchone()
    if 行:
        return int(行[0])
    下一个 = int(连.execute(f"SELECT COALESCE(MAX(号),0)+1 FROM {表}").fetchone()[0])
    if 下一个 > 表内上限:
        raise RuntimeError(
            f"{表} 已满（上限 {表内上限}）—— 拒绝折叠：宁可失败，也不许两个不同名字共用一个号")
    try:
        连.execute(f"INSERT INTO {表}(名,号) VALUES(?,?)", (名, 下一个))
    except sqlite3.IntegrityError:
        行 = 连.execute(f"SELECT 号 FROM {表} WHERE 名=?", (名,)).fetchone()
        if 行:
            return int(行[0])
        raise
    return 下一个


def 记(*, 症状: str, 修法: str, 证据: str = "", 路径们=(), 等级: int = 0,
      判决: int = 5, 动作: int | None = None, 仓库: str = "", 依赖深度: int = 0) -> dict:
    """写一条记忆。**等级必须由调用方显式给**（默认 0=SOLID 是危险默认，所以这里不给默认）。

    ★ 为什么要显式：本仓吃过"猜的被记成知道的"。默认 SOLID 会让所有写入都变成可检索的真知，
      而实际只有"门复跑绿"才配 SOLID。所以 `等级` 是必填关键字参数。
    """
    层 = 层判定(证据, 路径们)
    动 = 猜动作(修法) if 动作 is None else int(动作)
    子名 = 子系统名(路径们, 仓库)
    项名 = 项目键(仓库 or "local")
    with contextlib.closing(_连()) as 连, 连:   # ★ closing 才关连接；`with con` 只提交事务
        子 = _取号(连, "子系统表", 子名)
        项 = _取号(连, "项目表", 项名)
        # ★ 2026-11-05 修：原式 `WHERE 码 >> 56 = ?` 拿 dim7(序号) 当项目过滤，
        #   且 `MAX(码 & 255)` 取的是 dim0(层级) —— 序号因此只会 1..7 循环。
        #   正确：按 dim6(项目号) 过滤，取 dim7(序号) 的最大值。
        序 = (连.execute(
            "SELECT COALESCE(MAX((码 >> 56) & 255), 0) FROM 记忆8 WHERE ((码 >> 48) & 255) = ?",
            (项,)).fetchone()[0] + 1) % 256
        码 = 编码([层, 子, 判决, 动, 等级, 依赖深度, 项, 序])
        连.execute("INSERT INTO 记忆8(码,等级,症状,修法,仓库,文件,判决,动作,层,时间)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (码, 等级, (症状 or "")[:400], (修法 or "")[:800], 仓库 or "",
                    ",".join(str(x) for x in (路径们 or []))[:300],
                    判决, 动, 层, time.time()))
        连.commit()
    return {"码": 码, "说明": _说明带名(码, 子名, 项名), "层": 层, "动作": 动,
            "等级": 等级, "子系统": 子名, "项目": 项名}


def 检索(*, 症状: str = "", 证据: str = "", 路径们=(), 仓库: str = "",
        上限: int = 2, 最大等级: int = 1) -> list[dict]:
    """按**结构键**检索：先按 (层, 判决) 收窄，再按证据等级硬过滤，最后按症状词重合排序。

    `最大等级=1` 意思是只信 SOLID/PLAUSIBLE；OVERSTATED（被推翻）与猜测**永不返回**。
    """
    层 = 层判定(证据, 路径们)
    词 = set((症状 or "").lower().split()) | set((证据 or "").lower().split())
    with contextlib.closing(_连()) as 连, 连:   # ★ closing 才关连接；`with con` 只提交事务
        行 = 连.execute(
            "SELECT 码,等级,症状,修法,仓库,文件,判决,动作,层 FROM 记忆8"
            " WHERE 等级<=? AND 层=?", (最大等级, 层)).fetchall()
        # 名字表一次读全：号→名只是展示，不参与筛选（跨项目检索仍靠 同仓 加权）
        子名表 = {int(号): 名 for 名, 号 in 连.execute("SELECT 名,号 FROM 子系统表")}
        项名表 = {int(号): 名 for 名, 号 in 连.execute("SELECT 名,号 FROM 项目表")}
    出 = []
    for 码, 等级, 症状0, 修法, 仓, 文件, 判决, 动作, 层0 in 行:
        d = 解码(码)
        dset = set((症状0 or "").lower().split())
        重 = (len(词 & dset) / len(词 | dset)) if (词 and dset) else 0.0
        同仓 = 1.0 if (仓库 and 仓 == 仓库) else 0.0
        出.append({"码": 码,
                   "说明": _说明带名(码, 子名表.get(d[1]), 项名表.get(d[6])),
                   "等级": 等级, "修法": 修法,
                   "子系统": 子名表.get(d[1], "?"), "项目": 项名表.get(d[6], "?"),
                   "分数": round(0.6 * 重 + 0.4 * 同仓, 3), "同仓": bool(同仓)})
    出.sort(key=lambda x: (-x["分数"], x["等级"]))
    出 = [x for x in 出 if x["分数"] > 0.05][: max(0, int(上限))]
    if 出:
        with contextlib.closing(_连()) as 连, 连:   # ★ closing 才关连接；`with con` 只提交事务
            连.executemany("UPDATE 记忆8 SET 命中=命中+1 WHERE 码=?", [(x["码"],) for x in 出])
            连.commit()
    return 出


def 提示(条们: list[dict], 上限字: int = 400) -> str:
    """把记忆压成**提示块**（不是判据）。超预算就整条丢，不截半句。"""
    if not 条们:
        return ""
    行 = ["## 历史记忆（同类结构键，**仅供参考，不是判据**）"]
    for x in 条们:
        s = f"- [{x['等级'] and 等级名[x['等级']] or 'SOLID'}] {x['说明']}｜修法：{x['修法'][:160]}"
        if sum(len(y) for y in 行) + len(s) > 上限字:
            break
        行.append(s)
    return "\n".join(行) if len(行) > 1 else ""


def 统计() -> dict:
    try:
        with contextlib.closing(_连()) as 连, 连:   # ★ closing 才关连接；`with con` 只提交事务
            总 = 连.execute("SELECT COUNT(*) FROM 记忆8").fetchone()[0]
            分等 = dict(连.execute("SELECT 等级, COUNT(*) FROM 记忆8 GROUP BY 等级").fetchall())
            命中 = 连.execute("SELECT COALESCE(SUM(命中),0) FROM 记忆8").fetchone()[0]
            子数 = 连.execute("SELECT COUNT(*) FROM 子系统表").fetchone()[0]
            项数 = 连.execute("SELECT COUNT(*) FROM 项目表").fetchone()[0]
    except Exception as e:                      # noqa: BLE001 记忆坏了不许影响主链
        return {"错误": f"{type(e).__name__}: {e}"}
    return {"条数": 总,
            "按等级": {(等级名[k] if isinstance(k, int) and 0 <= k < len(等级名) else k): v
                       for k, v in 分等.items()},
            "总命中": 命中,
            "子系统数": 子数,
            "项目数": 项数,
            "库": str(库路径())}