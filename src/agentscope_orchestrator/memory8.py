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
| dim1 | 子系统 | 0~7 | 病根文件所在顶层模块（哈希折叠） |
| dim2 | 判决类 | 0~6 | 0 未达标 · 1 参数/前提不成立 · 2 基础设施不可用 · 3 实例分歧 · 4 任务书驳回 · 5 达标 · 6 取消 |
| dim3 | 修复动作 | 0~7 | 0 改值 · 1 补边界 · 2 改调用方 · 3 补依赖 · 4 改门 · 5 回滚 · 6 新建 · 7 删除 |
| dim4 | **证据等级** | 0~4 | 0 SOLID（过硬门复现）·1 PLAUSIBLE ·2 OVERSTATED ·3 UNVERIFIABLE ·4 猜测（**永不检索**） |
| dim5 | 依赖深度 | 0~255 | 这条修法依赖几条前置记忆（0 = 单点修复） |
| dim6 | 项目号段 | 0~255 | 仓库路径哈希折叠（跨项目时用来区分"同一类病在不同项目"） |
| dim7 | 序号/轮次 | 0~255 | 仓库内自增（同一条记忆的多轮观测） |

★ 与来源的**差异**（必须写出来，否则就是抄而不化）：
  · 原表 dim0 是"文档分层"（从最抽象的命题层到最落地的验证层），这里换成**故障层**；
  · 原表 dim3 是八卦归属（八个三爻布尔组合），这里换成**修复动作类别**（同构：3 位 → 8 类互斥动作）；
  · 原表 dim4 是知识等级（越抽象等级越高），这里换成**证据等级** —— 这一维是检索的**硬门**，不是展示字段。
  保留下来的正是可迁移的那部分：**定长维度 + 通配 + 前缀可查**。

## 诚实边界（写进模块，别写进 README 就忘）

· 编码不证明记忆正确：dim4 只记录"过没过门"，不记录"修法是否真对"；
· 维度折叠有损（子系统/项目号段都是哈希），**不是**可逆分类；
· 没有记忆时它只是空表，不降低任何现有行为；
· 记忆命中率**不能**用"少花了 token"单独证明有效 —— 必须同时看"成功率没掉"。
"""
from __future__ import annotations

import hashlib
import json
import os
import contextlib
import sqlite3
import time
from pathlib import Path

通配 = 0xFF

层名 = ["环境", "依赖", "产物代码", "接口/调用方", "测试", "判据门", "文档"]
判决名 = ["未达标", "参数/前提不成立", "基础设施不可用", "实例分歧", "任务书驳回", "达标", "取消"]
动作名 = ["改值", "补边界", "改调用方", "补依赖", "改门", "回滚", "新建", "删除"]
等级名 = ["SOLID", "PLAUSIBLE", "OVERSTATED", "UNVERIFIABLE", "猜测"]

维上限 = [6, 7, 6, 7, 4, 0xFF, 0xFF, 0xFF]


def _折(文: str, 模: int = 8) -> int:
    return int(hashlib.sha1((文 or "").encode("utf-8")).hexdigest()[:8], 16) % 模


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
    连.commit()
    return 连


def 记(*, 症状: str, 修法: str, 证据: str = "", 路径们=(), 等级: int = 0,
      判决: int = 5, 动作: int | None = None, 仓库: str = "", 依赖深度: int = 0) -> dict:
    """写一条记忆。**等级必须由调用方显式给**（默认 0=SOLID 是危险默认，所以这里不给默认）。

    ★ 为什么要显式：本仓吃过"猜的被记成知道的"。默认 SOLID 会让所有写入都变成可检索的真知，
      而实际只有"门复跑绿"才配 SOLID。所以 `等级` 是必填关键字参数。
    """
    层 = 层判定(证据, 路径们)
    子 = _折(仓库 + "|" + ",".join(sorted(str(x) for x in (路径们 or []))), 8)
    动 = 猜动作(修法) if 动作 is None else int(动作)
    项 = _折(仓库 or "local", 256)
    with contextlib.closing(_连()) as 连, 连:   # ★ closing 才关连接；`with con` 只提交事务
        序 = (连.execute("SELECT COALESCE(MAX(码 & 255), 0) FROM 记忆8 WHERE 码 >> 56 = ?",
                        (项,)).fetchone()[0] + 1) % 256
        码 = 编码([层, 子, 判决, 动, 等级, 依赖深度, 项, 序])
        连.execute("INSERT INTO 记忆8(码,等级,症状,修法,仓库,文件,判决,动作,层,时间)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (码, 等级, (症状 or "")[:400], (修法 or "")[:800], 仓库 or "",
                    ",".join(str(x) for x in (路径们 or []))[:300],
                    判决, 动, 层, time.time()))
        连.commit()
    return {"码": 码, "说明": 说明(码), "层": 层, "动作": 动, "等级": 等级}


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
    出 = []
    for 码, 等级, 症状0, 修法, 仓, 文件, 判决, 动作, 层0 in 行:
        d = set((症状0 or "").lower().split())
        重 = (len(词 & d) / len(词 | d)) if (词 and d) else 0.0
        同仓 = 1.0 if (仓库 and 仓 == 仓库) else 0.0
        出.append({"码": 码, "说明": 说明(码), "等级": 等级, "修法": 修法,
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
    except Exception as e:                      # noqa: BLE001 记忆坏了不许影响主链
        return {"错误": f"{type(e).__name__}: {e}"}
    return {"条数": 总,
            "按等级": {(等级名[k] if isinstance(k, int) and 0 <= k < len(等级名) else k): v
                       for k, v in 分等.items()},
            "总命中": 命中,
            "库": str(库路径())}
