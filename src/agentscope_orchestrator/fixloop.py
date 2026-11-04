"""★ 通用修复环：**模型改代码（可新建/删除/重命名），机器门裁决，绿保红全量回滚**。

为什么长成这样（2026-10-30 用户的两次质问）：
  ①「外面改代码不是 LLM 干的吗？」——改代码是**模型**能力，不是框架能力；
  ②「工程项目哪里有不能新建文件删除重构的道理？」——上一版把"没沙箱"的恐惧
    变成了"只许改已存在文件"，那等于把新项目和真工程排除在外。**这是设计缺陷**。

⇒ 正确的替代不是收紧权限，而是**可回滚 + 机器裁决**：
     全仓快照 → 应用模型给的 ops（write/delete/rename）→ **机器门复跑**
     → 绿=保留 / 红=**按快照全量还原**（新建的删掉、删除的恢复、改名/改写的还原）。
  权限边界只剩：不出仓、不碰 .git、总量有上限。**不再限制能不能新建/删除/重构。**

纪律不变：**产出方不判自己**。模型成功与否由门说，不由模型说。

不做的事：不出仓、不碰 `.git`、不联网、不装依赖。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import asyncio
import logging

import json
import os
import re
import time
from pathlib import Path

import agentscope_orchestrator.real_task as rt

_跳过目录 = {".git", "target", "node_modules", "__pycache__", ".venv", "dist",
             "build", ".pytest_cache", ".mypy_cache", ".orch_backup", ".orchestrator",
             ".idea", ".vscode", ".next", ".turbo", ".cache"}
_允许后缀 = {".py", ".rs", ".ts", ".tsx", ".mts", ".js", ".jsx", ".mjs",
             ".lean", ".toml", ".cfg", ".ini", ".txt", ".md", ".yml", ".yaml",
             ".json", ".c", ".h", ".cpp", ".hpp", ".go", ".java", ".kt", ".sql",
             ".html", ".css", ".sh", ".ps1"}
_无后缀允许 = {"Dockerfile", "Makefile", ".gitignore", ".dockerignore", "LICENSE",
               "NOTE", "README"}
# ★ 判据环境文件（2026-10-30 实测发现的攻击面）：
#   真跑里模型为了骗过门，写了 `sitecustomize.py`（Python 启动时自动加载）——
#   这不是修 bug，是**改考试规则**。这类文件一律不许模型改；另一些（conftest/配置）
#   不禁止但要**标记**，因为那个绿可能是"把测试跳过"换来的。
_禁改文件 = {"sitecustomize.py", "usercustomize.py"}
_禁改后缀 = {".pth"}
_标记文件 = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
            "jest.config.js", "package.json", ".python-version", "requirements.txt"}

单文件上限 = 300_000            # 300 KB
整包上限 = 2_000_000            # 一轮最多写 2 MB
单轮操作上限 = 200              # 防"整个仓重构"失控
快照文件上限 = 20_000
快照字节上限 = 200_000_000      # 200 MB


# ─── 判据环境指纹（2026-11-01 补：把"黑名单"升级成"行为层检测"）──────────────
# ★ 缺口：`_禁改文件` / `_禁改后缀` 是**黑名单**——只挡"名单里那几种写法"。
#   模型完全可能改一个**不在名单上**的东西来让门变绿（公共夹具里加个 monkeypatch、
#   在 `tests/` 里放个被自动收集的模块、改解释器能读到但名字不叫 sitecustomize 的文件…
#   Web/其他语言各有自己的"启动时自动加载"机制，名单永远列不完）。
# ★ 修法：**门前门后比对判据环境指纹**。这不是"再猜一遍作弊写法"，而是把
#   "考试规则有没有被改"变成一个**可机验的差分**：同一批文件、同一个哈希，
#   变了就是变了 —— 不需要预先想到改法。
# ★ 诚实边界：指纹只覆盖**仓内**这几类文件；仓外（`%LOCALAPPDATA%` 的 site-packages、
#   解释器路径、环境变量）改不到也看不见。这条边界写进返回值的 `判据指纹边界`。
def _是判据环境文件(p: Path) -> bool:
    return (p.name in _禁改文件 or p.suffix.lower() in _禁改后缀
            or p.name in _标记文件)


def _环境指纹() -> dict[str, str]:
    """**仓外**判据环境指纹：`sys.path` 全链上的启动钩子 + 解释器 + 相关环境变量。

    ★ 补的是上一版**明确承认的不可检测面**：原 `_判据指纹` 只扫仓内，
      而 Python 的启动钩子（`sitecustomize.py` / `*.pth`）**根本不在仓内**：
      它们躺在 `site-packages` 里，改一行就能让门里跑的每个子进程多执行一段代码。
      实测证据：模型曾**真的**去写 `sitecustomize.py` 骗门。
    ★ 覆盖口径（尽可能便宜且确定）：
      ① `sys.path` 每个目录下的 `sitecustomize.py` / `usercustomize.py` / 顶层 `*.pth`
      ② `sys.executable` 的大小 + mtime + sha256
      ③ 影响 import 的四个环境变量（值哈希，不存明文）
      ④ 仓外的 `conftest.py` **不**扫（太贵且噪声大）——这条边界写进返回值。
    ★ 代价：一次调用通常是**个位数文件**（钩子本来就少），解释器哈希几 MB。
    """
    import hashlib
    import os
    import sys

    表: dict[str, str] = {"__解释器__": "", "__环境__": ""}
    exe = Path(sys.executable)
    摘要 = []
    try:
        摘要.append(f"{exe}|{exe.stat().st_size}|{int(exe.stat().st_mtime)}")
        h = hashlib.sha256()
        with exe.open("rb") as fh:
            for 块 in iter(lambda: fh.read(1 << 20), b""):
                h.update(块)
        摘要.append(h.hexdigest())
    except OSError as e:
        摘要.append(f"读不到:{type(e).__name__}")
    表["__解释器__"] = "|".join(摘要)

    钩子: dict[str, str] = {}
    for d0 in list(getattr(sys, "path", []) or []):
        if not d0:
            continue
        d = Path(d0)
        if not d.is_dir():
            continue
        for 名 in ("sitecustomize.py", "usercustomize.py"):
            p = d / 名
            if p.is_file():
                try:
                    钩子[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
                except OSError:
                    continue
        try:
            for p in sorted(d.glob("*.pth")):
                if p.is_file():
                    try:
                        钩子[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
                    except OSError:
                        continue
        except OSError:
            continue

    变量 = []
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE",
              "PYTHONSAFEPATH", "PYTHONNOUSERSITE"):
        v = os.environ.get(k)
        if v is None:
            continue
        变量.append(f"{k}={hashlib.sha256(v.encode('utf-8', 'replace')).hexdigest()[:16]}")
    表["__环境__"] = "|".join(sorted(变量))
    for k, v in 钩子.items():
        表["[仓外] " + k] = v
    return 表


def 预置点名(表: dict[str, str]) -> list[str]:
    """从指纹表里挑出**要在回执里点名**的键（"这些文件本来就躺在那儿"）。

    ★ 口径（0.12.9）：
      · 仓内：禁改文件名（`sitecustomize.py`/`usercustomize.py`）与禁改后缀（`*.pth`）
        —— 它们都能让门里的**每个**子进程多执行一段代码；
      · 仓外：**只点名启动钩子本体**（`sitecustomize.py`/`usercustomize.py`）。
        仓外 `*.pth` 是 venv 的正常安装产物（`__editable__*`、`pywin32.pth`），
        点名等于每次跑都亮红灯 —— 噪声会把真信号淹掉。
      · 抽成一个函数是必须的：测试与修复环都读这个口径，两份实现必然分叉。
    """
    出: list[str] = []
    for k in 表:
        名 = Path(k).name
        if k.startswith("[仓外] "):
            if 名 in _禁改文件:
                出.append(k)
        elif 名 in _禁改文件 or Path(k).suffix.lower() in _禁改后缀:
            出.append(k)
    return sorted(出)


def _判据指纹(根: Path) -> dict[str, str]:
    """判据环境指纹 = **仓内**判据环境文件 sha256 ∪ **仓外**全链指纹（`_环境指纹`）。

    ★ 合并成一个 dict 而不是"两个比对"：比对逻辑只有一处，少一处就少一种漏。
    """
    import hashlib
    表: dict[str, str] = {}
    try:
        for p in 根.rglob("*"):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(根).parts
            if any(x in _跳过目录 for x in rel_parts):
                continue
            if not _是判据环境文件(p):
                continue
            try:
                表[str(p.relative_to(根)).replace("\\", "/")] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
            except OSError:
                continue
    except OSError:
        pass
    # ★ 合入仓外指纹：仓内改了抓得到，仓外（site-packages 启动钩子 / 解释器 / import 环境变量）
    #   也抓得到 —— 这条正是上一版**明确承认的不可检测面**，此处补掉。
    try:
        表.update(_环境指纹())
    except Exception:      # noqa: BLE001 环境指纹失败不许让判据整体失效
        pass
    return 表


def _判据差异(前: dict[str, str], 后: dict[str, str]) -> dict[str, list[str]]:
    """前→后的差异，分成 改/新增/删除 三桶（空桶不出现）。"""
    改 = sorted(k for k in 前 if k in 后 and 前[k] != 后[k])
    增 = sorted(k for k in 后 if k not in 前)
    删 = sorted(k for k in 前 if k not in 后)
    果: dict[str, list[str]] = {}
    if 改:
        果["改"] = 改
    if 增:
        果["新增"] = 增
    if 删:
        果["删除"] = 删
    return 果


def _证据文本(audit: list[dict], 上限: int = 3000) -> tuple[str, list[str]]:
    片: list[str] = []
    提及: list[str] = []
    后缀模式 = "|".join(s.lstrip(".") for s in sorted(_允许后缀))
    for a in audit or []:
        if a.get("ok"):
            continue
        名, rc = a.get("gate"), a.get("rc")
        cause = (a.get("cause") or "").strip()
        err = (a.get("err") or "")
        片.append(f"### 门 {名} 未过（rc={rc}，{cause}）\n{err[:上限]}")
        for m in re.finditer(r"([A-Za-z0-9_./\\-]+\.(?:%s))(?::\d+)?" % 后缀模式, err):
            p = m.group(1).replace("\\", "/")
            if p not in 提及:
                提及.append(p)
    return "\n\n".join(片), 提及


def _安全路径(根: Path, 相对: str, 允许不存在: bool = True) -> Path | None:
    """收敛到仓内路径；越界/碰 .git/后缀不允许 ⇒ None。允许指向尚不存在的文件。"""
    s = (相对 or "").strip().replace("\\", "/").lstrip("./")
    if not s or s.startswith("/") or ".." in Path(s).parts:
        return None
    p = (根 / s).resolve()
    try:
        相对路径 = p.relative_to(根.resolve())
    except ValueError:
        return None
    if any(part in _跳过目录 for part in 相对路径.parts):
        return None
    if p.suffix.lower() not in _允许后缀 and p.name not in _无后缀允许:
        return None
    if not 允许不存在 and not p.is_file():
        return None
    return p


def _快照(根: Path) -> dict:
    """全仓快照（内容 + 权限），用于整批还原。"""
    项: dict[str, dict] = {}
    字节 = 0
    for p in 根.rglob("*"):
        if any(part in _跳过目录 for part in p.relative_to(根).parts):
            continue
        if not p.is_file():
            continue
        if len(项) >= 快照文件上限 or 字节 >= 快照字节上限:
            break
        try:
            st = p.stat()
            if st.st_size > 5_000_000:      # 单个 >5 MB 不进快照（大二进制）
                continue
            data = p.read_bytes()
        except OSError:
            continue
        相对 = str(p.relative_to(根)).replace("\\", "/")
        项[相对] = {"data": data, "mode": st.st_mode}
        字节 += len(data)
    return {"项": 项, "字节": 字节, "文件数": len(项),
            "完整": len(项) < 快照文件上限 and 字节 < 快照字节上限}


def _还原(根: Path, 快: dict) -> dict:
    """还原到快照状态：恢复被改/被删，删掉快照之后新建的，清理空目录。"""
    原 = 快["项"]
    当前: dict[str, Path] = {}
    for p in 根.rglob("*"):
        if any(part in _跳过目录 for part in p.relative_to(根).parts):
            continue
        if p.is_file():
            当前[str(p.relative_to(根)).replace("\\", "/")] = p
    删新建 = 0
    for 相对, p in 当前.items():
        if 相对 not in 原:
            try:
                p.unlink()
                删新建 += 1
            except OSError:
                pass
    恢复 = 0
    for 相对, 元 in 原.items():
        p = 根 / 相对
        try:
            现 = p.read_bytes() if p.is_file() else None
        except OSError:
            现 = None
        if 现 != 元["data"]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(元["data"])
            try:
                os.chmod(p, 元["mode"])
            except OSError:
                pass
            恢复 += 1
    空目录 = 0
    for d in sorted((x for x in 根.rglob("*") if x.is_dir()),
                    key=lambda x: -len(x.parts)):
        if any(part in _跳过目录 for part in d.relative_to(根).parts):
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                空目录 += 1
        except OSError:
            pass
    return {"恢复文件": 恢复, "删除新建": 删新建, "清理空目录": 空目录}


# ★ 2026-10-31 真实仓实测：上下文 29.7KB + 文件清单 200 条 ⇒ 提示 ≈76KB，
#   上游 136s 不回（超时）。⇒ 默认收紧到 24KB，并支持环境覆盖。
_整包上下文上限 = int(os.environ.get("ORCH_CTX_BYTES") or 24_000)
_上下文单文件上限 = int(os.environ.get("ORCH_CTX_FILE_BYTES") or 12_000)          # 一轮给模型的上下文总量上限（防 prompt 爆炸）


_表缓存: dict[str, object] = {}


def _符号表(根: Path):
    """进程内缓存符号表：首次建表贵（375 个 .rs 实测 25.6s），之后**增量**同步。

    ★ 真证据（2026-10-31 真实仓）：每轮都重建 ⇒ 一轮白花 25s，三轮 75s；
      而符号表自己的不变量是"成本 ∝ Δ"，缓存起来才吃得到这个好处。
    """
    try:
        from agentscope_orchestrator import mem_symbols as ms
    except Exception:                       # noqa: BLE001
        return None
    键 = str(Path(根).resolve())
    表 = _表缓存.get(键)
    try:
        if 表 is None:
            表 = ms.符号表()
            表.同步(键)
            _表缓存[键] = 表
        else:
            表.同步(键)                     # 增量：无改动 ⇒ 重算 0
    except Exception:                       # noqa: BLE001
        _表缓存.pop(键, None)
        return None
    return 表


def _符号相关(根: Path, 证据: str, 排除: set[Path], 上限: int = 6) -> list[Path]:
    """用**本包自己的跨语言符号表**按名字找相关文件（这是本层已有能力，之前没用上）。

    为什么加（2026-10-30 自查发现的自身劣势）：第一版只按"证据点名的文件 + 仓里最小的 N 个"
    选上下文 ⇒ 真正的缺陷文件一旦比一堆小文件大，就**根本进不了模型的视野**。
    外面（Aider/SWE-agent 一类）用 repo map 按相关性选文件；我本来就有符号表，却没用。
    """
    名们 = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", 证据 or ""))
    if not 名们:
        return []
    表 = _符号表(根)
    if 表 is None:
        return []
    命中: list[tuple[int, Path]] = []
    for 名 in 名们:
        try:
            条们 = 表.查(名) or []
        except Exception:                   # noqa: BLE001
            continue
        for 条 in 条们:
            相对 = getattr(条, "相对路径", None) or getattr(条, "file", None) or getattr(条, "文件", None)
            if not 相对:
                continue
            p = _安全路径(根, str(相对), 允许不存在=False)
            if p and p not in 排除:
                命中.append((0, p))
    去重: list[Path] = []
    for _, p in 命中:
        if p not in 去重:
            去重.append(p)
        if len(去重) >= 上限:
            break
    return 去重


_代码后缀 = {".py", ".rs", ".go", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".mts",
             ".lean", ".c", ".h", ".cpp", ".hpp", ".java", ".kt", ".sh", ".ps1"}
_证据路径式 = re.compile(
    r"([\w\u4e00-\u9fff][\w\u4e00-\u9fff./\\ -]{0,120}?\.(?:py|rs|go|js|mjs|ts|tsx|jsx|"
    r"lean|c|h|cpp|hpp|java|kt))(?::\d+)")


def _证据路径(证据: str) -> list[str]:
    """从红门证据里抽**文件路径**（含 `xxx.rs:763:9` 这种带行号的）。

    ★ 真证据（2026-10-31 真实仓）：证据里明明有 `辅灵集\效能天鉴.rs:763`，
      旧实现没抽出来 ⇒ 上下文退化成"仓里最小的 N 个文件"，挑到一堆 `_temp_fix_*.txt`。
      症状不指病根时这是致命的：模型拿到的是**无关文件**。
    """
    出: list[str] = []
    for m in _证据路径式.finditer(证据 or ""):
        p = m.group(1).strip().lstrip("./\\").replace("\\", "/")
        if p and p not in 出:
            出.append(p)
    return 出


def _证据行(证据: str) -> dict:
    """从证据里抽 `路径:行号`，供"只给失败行附近"用。"""
    出: dict[str, int] = {}
    for m in re.finditer(r"([\w\u4e00-\u9fff][\w\u4e00-\u9fff./\\ -]{0,120}?"
                         r"\.(?:py|rs|go|js|mjs|ts|tsx|jsx|lean|c|h|cpp|hpp|java|kt))"
                         r":(\d+)", 证据 or ""):
        p = m.group(1).strip().lstrip("./\\").replace("\\", "/")
        出.setdefault(p, int(m.group(2)))
    return 出


_轮廓式 = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?(?:unsafe[ \t]+)?"
    r"(?:export[ \t]+)?(fn|def|struct|enum|trait|impl|class|mod|const|static|type)"
    r"[ \t]+([\w\u4e00-\u9fff'.]+)")


def _轮廓(文: str, 上限: int = 80) -> str:
    """给出文件的**签名地图**（名字 + 行号），不含函数体。

    ★ 为什么必须有（2026-10-31 真实仓实测）：只给"断言行附近 80 行"时，
      模型看到的是**测试**而不是**病根**（病根在同一文件另一处）⇒ 它去写 `build.rs` 旁路。
      签名地图让模型用几百字节看到"这个文件里有什么"，再决定读哪一段。
    """
    行们 = 文.splitlines()
    出 = []
    for i, 行 in enumerate(行们, 1):
        m = _轮廓式.match(行)
        if m:
            出.append(f"{i:>6}| {m.group(1)} {m.group(2)}")
        if len(出) >= 上限:
            出.append("…（更多签名省略）")
            break
    return "\n".join(出)


_标识符式 = re.compile(r"[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]{1,}")


def _函数块(文: str) -> dict[str, str]:
    """抽出 `{函数名: 源码块}`（Rust 花括号配平 / Python 缩进），用于"只给被调用的函数"。"""
    行们 = 文.splitlines()
    出: dict[str, str] = {}
    for i, 行 in enumerate(行们):
        m = re.search(r"\bfn\s+([\w\u4e00-\u9fff]+)\s*[(<]", 行) or \
            re.search(r"^\s*(?:async\s+)?def\s+([\w\u4e00-\u9fff]+)\s*\(", 行)
        if not m:
            continue
        名 = m.group(1)
        if 名 in 出:
            continue
        if "fn " in 行:                      # Rust：从声明行数到配平
            深 = 0
            开 = False
            for j in range(i, min(len(行们), i + 400)):
                深 += 行们[j].count("{") - 行们[j].count("}")
                if "{" in 行们[j]:
                    开 = True
                if 开 and 深 <= 0:
                    出[名] = "\n".join(行们[i:j + 1])
                    break
        else:                               # Python：缩进块
            缩 = len(行) - len(行.lstrip())
            j = i + 1
            while j < len(行们):
                空 = not 行们[j].strip()
                缩2 = len(行们[j]) - len(行们[j].lstrip())
                if not 空 and 缩2 <= 缩:
                    break
                j += 1
            出[名] = "\n".join(行们[i:j])
    return 出


def _含行函数(文: str, 行号: int) -> str | None:
    """找出**包含第 行号 行**的函数名（证据 `file:763` ⇒ 断言行所在的测试函数）。"""
    行们 = 文.splitlines()
    if not (1 <= 行号 <= len(行们)):
        return None
    for i in range(行号 - 1, -1, -1):
        m = (re.search(r"\bfn\s+([\w\u4e00-\u9fff]+)\s*[(<]", 行们[i]) or
             re.search(r"^\s*(?:async\s+)?def\s+([\w\u4e00-\u9fff]+)\s*\(", 行们[i]))
        if m:
            return m.group(1)
    return None


def _调用切片(文: str, 证据: str, 行号: int | None = None, 预算: int = 12_000) -> str:
    """只给"失败测试实际调到"的函数（一层调用追踪），而不是整个文件。

    ★ 2026-10-31 第四次修正（真实仓实测链）：整文件 26KB ⇒ 提示 35KB ⇒ 上游 >145s 超时；
      只给断言行附近 ⇒ 模型看到测试、看不到病根 ⇒ 去改无关函数。
      ⇒ 正确粒度是**调用切片**：先从证据里取标识符，命中该文件的函数块；
        再把这些块里出现的标识符做**一层**追踪（`产物膨胀率` → `_分派` → `_继续/_终止`）。
    """
    块表 = _函数块(文)
    if not 块表:
        return 文[:预算]
    种子 = {m.group(0) for m in _标识符式.finditer(证据 or "")} & set(块表)
    if 行号:
        含 = _含行函数(文, 行号)
        if 含 and 含 in 块表:
            种子.add(含)
    取: list[str] = []
    已 = set()
    for 名 in sorted(种子):
        if 名 in 已:
            continue
        已.add(名)
        取.append(名)
    # 同族函数（`产物膨胀率` → `产物膨胀率_分派/_继续/_终止`）+ 一层追踪
    for 名 in list(取):
        族根 = 名.split("_")[0]
        for 他 in 块表:
            if 他 != 名 and 他.split("_")[0] == 族根 and 他 not in 已:
                已.add(他)
                取.append(他)
    for 名 in list(取):
        内 = {m.group(0) for m in _标识符式.finditer(块表.get(名, ""))} & set(块表)
        for 二 in sorted(内 - 已):
            已.add(二)
            取.append(二)
    片 = ["#### 函数签名地图（全文件）", _轮廓(文), "", "#### 失败测试直接/一层调用到的函数"]
    用 = 0
    for 名 in 取:
        块 = 块表[名]
        b = len(块.encode("utf-8"))
        if 用 + b > 预算:
            continue
        用 += b
        片.append(f"```\n{块}\n```")
    return "\n".join(片)


def _窗口(文: str, 行: int, 半径: int = 80) -> str:
    """取第 `行` 前后各 `半径` 行（带真实行号），而不是整文件。"""
    行们 = 文.splitlines()
    if not 行们:
        return 文
    起 = max(0, 行 - 1 - 半径)
    止 = min(len(行们), 行 + 半径)
    片 = [f"{i + 1:>6}| {行们[i]}" for i in range(起, 止)]
    return (f"（只给第 {行} 行附近 {起 + 1}~{止} 行；整文件 {len(行们)} 行）\n"
            + "\n".join(片))


def _上下文(根: Path, 提及: list[str], 上限文件: int = 12,
           证据: str = "") -> tuple[str, list[str]]:
    选中: list[Path] = []
    # ⓪ 证据点名的路径优先（含相对/绝对两种写法，按后缀匹配仓内文件）
    for 相对 in _证据路径(证据):
        候选 = [p for p in (根.rglob("*")) if p.is_file()
                and str(p.relative_to(根)).replace("\\", "/").endswith(相对)]
        if 候选 and 候选[0] not in 选中:
            选中.append(sorted(候选, key=lambda x: len(str(x)))[0])
        if len(选中) >= 3:
            break
    for 相对 in 提及:
        p = _安全路径(根, 相对, 允许不存在=False)
        if p and p not in 选中:
            选中.append(p)
        if len(选中) >= 上限文件:
            break
    # ② 符号表相关性（补上"证据没点名但缺陷就在那里"的情况）
    for p in _符号相关(根, 证据, set(选中), 上限=6):
        if len(选中) >= 上限文件:
            break
        选中.append(p)
    if len(选中) < 上限文件:
        候选 = []
        for p in 根.rglob("*"):
            if any(part in _跳过目录 for part in p.relative_to(根).parts):
                continue
            # 纯数据/文档后缀（.txt/.md/.json/.yml/.toml）只在被点名时才进上下文：
            # 否则"按大小排序"会把一堆构建日志顶到模型眼前（真实仓实测）。
            _点名 = any(str(p).replace("\\", "/").endswith(x) for x in 提及 + _证据路径(证据))
            if p.is_file() and ((p.suffix.lower() in _代码后缀)
                                or (_点名 and (p.suffix.lower() in _允许后缀
                                               or p.name in _无后缀允许))):
                try:
                    候选.append((p.stat().st_size, p))
                except OSError:
                    continue
        候选.sort()
        for _, p in 候选:
            if p not in 选中:
                选中.append(p)
            if len(选中) >= 上限文件:
                break
    片: list[str] = []
    相对们: list[str] = []
    已用 = 0
    for p in 选中:
        if 已用 >= _整包上下文上限:
            break
        try:
            文 = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        行号 = _证据行(证据).get(str(p.relative_to(根)).replace("\\", "/"))
        是证据文件 = 行号 is not None
        if 是证据文件 and len(文.encode("utf-8")) > 6_000:
            文 = _调用切片(文, 证据, 行号)
        elif 是证据文件 and len(文.encode("utf-8")) > 48_000:
            # ★ 2026-10-31 第三次修正：只给"断言行附近"时，模型看到的是**测试**、
            #   看不到病根函数 ⇒ 实测它去改无关的 Kahn 排序（1 轮 2 处改动、门仍红）。
            #   证据文件就是**病根文件**：≤48KB 时**整篇给**（输入 token 便宜，慢的是输出）；
            #   超过 48KB 才退化成"轮廓 + 窗口"。
            文 = (_轮廓(文) + "\n…（以下为证据行附近）\n" + _窗口(文, 行号))
        elif (not 是证据文件) and len(文.encode("utf-8")) > _上下文单文件上限:
            文 = 文[: _上下文单文件上限 // 3] + "\n…（文件过大已截断）"
        相对 = str(p.relative_to(根)).replace("\\", "/")
        块 = f"#### FILE: {相对}\n```\n{文}\n```"
        b = len(块.encode("utf-8"))
        # 预算：证据文件放宽到 48KB，其余文件合计 ≤ 12KB（证据文件可能排在后面，故不早退）
        if 是证据文件:
            if 已用 + b > 48_000 and 已用 > 0:
                pass
        elif 已用 + b > _整包上下文上限:
            continue
        已用 += b
        相对们.append(相对)
        片.append(块)
    return "\n\n".join(片), 相对们


def _提示(仓: Path, 命令: list[str], 证据: str, 上下文: str, 轮: int,
          已有文件: list[str]) -> str:
    try:
        _清单上限 = max(5, min(200, int(os.environ.get("ORCH_CTX_LIST") or 60)))
    except (TypeError, ValueError):
        _清单上限 = 60
    清单 = "\n".join(f"- {x}" for x in 已有文件[:_清单上限]) or "（空仓）"
    return f"""你在修一个真实的工程仓。目标是让**机器判据门**通过，不是你自认为改好了。

仓根：{仓}
被判定失败的命令：{json.dumps(命令, ensure_ascii=False)}
当前是第 {轮} 轮修复。

## 失败证据（机器判据门的原始输出）
{证据}

## 相关文件当前内容
{上下文}

## 仓内已有文件（节选，用来判断缺什么）
{清单}

## 你的任务
做**能让上面那条命令通过**的完整最小改动。**允许**：新建文件（含新目录）、修改已有文件、
删除文件、重命名/移动。**不允许**：出仓路径、改 .git、装依赖、联网。

只输出一个 JSON 对象，不要解释文字、不要 markdown 围栏：
{{"ops": [
  {{"op": "replace", "path": "<仓内相对路径>", "find": "<原文片段，必须恰好出现 1 次>", "with": "<替换成的片段>"}},
  {{"op": "write", "path": "<仓内相对路径>", "content": "<完整文件内容>"}},
  {{"op": "delete", "path": "<仓内相对路径>"}},
  {{"op": "rename", "from": "<旧路径>", "to": "<新路径>"}}
], "说明": "<一句话：改了什么、为什么这样能过>"}}

规则：
- **改已有文件优先用 `replace`**（只给改动片段）：`find` 必须在文件里**恰好出现 1 次**，
  否则整条操作被拒；长文件（>8 KB）用 `write` 回吐全文会很慢，可能超时；
- write 的 content 是**完整文件内容**，用于新建文件或确实要整篇重写；
- delete 只删确实该删的；rename 用于移动/改名；
- 最多 {单轮操作上限} 个操作，单文件 ≤ {单文件上限 // 1000} KB；
- **禁止**用 skip / xfail / ignore / `@unittest.skip` / `# [ignore]` 把测试变成绿：
  门会数 passed/skipped/failed，跳过数超过上限即红；也不许删测试文件来"通过"；
- 若判断无法在不改门的前提下通过，返回 {{"ops": [], "说明": "无法修复：<原因>"}}。
"""


def _解析(文本: str) -> tuple[list[dict], str]:
    t = (文本 or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.M).strip()
    if not t:
        return [], "模型输出为空"
    d = None
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            try:
                d = json.loads(t[i:j + 1])
            except json.JSONDecodeError as ex:
                return [], f"JSON 解析失败：{ex}"
    if not isinstance(d, dict):
        return [], "顶层不是对象"
    说明 = str(d.get("说明") or "")
    ops: list[dict] = []
    if isinstance(d.get("ops"), list):
        for x in d["ops"]:
            if isinstance(x, dict) and x.get("op"):
                ops.append(x)
    elif isinstance(d.get("files"), dict):      # 向后兼容旧协议
        for k, v in d["files"].items():
            if isinstance(v, str):
                ops.append({"op": "write", "path": k, "content": v})
    return ops, 说明


def _事件(kind: str, 引用: str = "", 载荷: dict | None = None) -> None:
    """把一个真实发生的动作，翻译成事件 + 落一个检查点（2026-11-03 接）。

    ## 为什么事件必须挂在**真实动作**上，而不是靠扫文件

    扫描式（定时去看"文件变了没"）有三个躲不掉的坑：
    ① **改了又改回来** ⇒ 扫描看不到（而中间那一步可能已经破坏过门）；
    ② **不知道是谁改的** ⇒ 只能知道"变了"，不知道"哪一步引起的"；
    ③ **有延迟** ⇒ 破坏和发现之间隔着一段时间。

    ⇒ 挂在动作上，这些问题都不存在：改的瞬间就知道、知道是哪一步、没有延迟。

    ## 为什么"链断裂"要先查再落

    别的动作是"做了才记"，链断裂是"发现了才记" ——
    它本来就是**观测结果**，不是动作。所以这里先核一遍链，
    真断了才发事件，否则会给链上平白加一个"其实没事"的事件。

    ★ **绝不拖垮修复主链**：任何异常都被吞掉。事件驱动是**旁路**，
      它坏了不该让"修代码"这件事做不成（本项目已有过"给目录做递归扫描
      把 CI 顶到 900s 被杀"的教训）。
    """
    try:
        from agentscope_orchestrator import events as _E
        裁 = _E.触发(kind, 引用, 载荷 or {})
        if kind == _E.链断裂:
            try:
                from agentscope_orchestrator import coord_chain as _CC
                体检 = _CC.校验()
                if 体检.通过:
                    return                      # 链没断 ⇒ 不给它加一个假事件
            except Exception:                   # noqa: BLE001 核不了就照发（宁可多记）
                pass
        # 落检查点：状态 = "这一步干了什么"，值取内容指纹（**不是全文**）。
        # ★ 为什么存指纹不存全文：检查点是**变化量**存储，存全文等于每次全量，
        #   增量存储的意义就没了。指纹足够判"这一步跟下一步一样不一样"。
        状态 = {"动作": kind, "引用": 引用,
                "指纹": __import__("hashlib").sha256(
                    json.dumps(载荷 or {}, ensure_ascii=False, sort_keys=True,
                               default=str).encode("utf-8")).hexdigest()[:16]}
        _E.检查点(状态, 全量=bool(裁.get("周期全量")), 事件=kind, 引用=引用)
    except Exception:                           # noqa: BLE001 事件坏绝不影响修复主链
        pass


def _行尾(目标: Path) -> str:
    """取目标文件**现有的**行尾（新文件给 `\\n`）。

    ★ 为什么必须问文件、不能问平台：`write_text()` 在不给 `newline=` 时把 `\\n`
      翻成 `os.linesep` ⇒ **在 Windows 上跑一次，整个文件的行尾就变了**。
    """
    try:
        if 目标.exists():
            原 = 目标.read_bytes()
            回 = 原.count(b"\r\n")
            换 = 原.count(b"\n") - 回
            return "\r\n" if 回 > 换 else "\n"
    except Exception:                           # noqa: BLE001 读不了就按新文件对待
        pass
    return "\n"


def _原子写(目标: Path, 文: str) -> None:
    """★★★★★ 2026-11-04（第229轮）：**写入不许因为"在 Windows 上跑"就把行尾翻掉。**

    ## 这个缺陷是我自己先踩到、再回头在**生产代码里**找到的

    我改本仓 4 个 `.py` 时用脚本 `p.write_text("\\n".join(ls), encoding="utf-8")`
    （**没给 `newline=`**），提交后 `git show --stat` 一看：

    ```
    blind_spot.py    | 1872 +++++++++++---------      ← 整个文件都算改了
    ```

    **真因**：`newline=None` 会把 `\n` 翻成 `os.linesep`，Windows 上是 `\r\n`。
    ⇒ **后果不是"多改了个换行"，是三个**：
      · diff 不可读（172 行真改动被 1872 行噪声盖住）
      · `git blame` 报废（每一行都算我改的）
      · 别人 rebase 时满屏冲突

    ## 然后回到本函数 —— 它犯的是**同一个错**

    `临时.write_text(文, encoding="utf-8")`，没给 `newline=`。
    这不是测试夹具，是**修复环往被修仓写文件的两条真路**（本文件 815 / 852）。
    ⇒ 模型修好一个 LF 仓里的文件，落盘时**整文件变 CRLF** ——
      门可能照样绿，但人的 diff 全花。

    ★ 口径：**"没报错"不等于"没改坏"。** 行尾翻转是**静默**的：
      命令 rc=0、门绿、字节数还变了（`_事件` 里那行 `len(文)`）——
      四个信号全指向"成功了"。

    ## 修法：以**目标文件现有的**行尾为准（同 `generic_goal._原子写源码` 的字节级纪律）

    本仓已经有一处写对过 —— `generic_goal.py` 的 `_原子写源码(路径, bytes)` 是
    **字节级**的，还配了一条 R113 门盯着「变异与还原两处都必须用它」。
    ⇒ 同一个项目里两种纪律，说明**靠人记住是不够的**（这条也进判据）。
    """
    行尾 = _行尾(目标)
    # 把文里的各种行尾先归一，再按目标的行尾落盘 ⇒ 不翻转、也不留混合行尾
    规范 = (文 or "").replace("\r\n", "\n").replace("\r", "\n")
    if 行尾 == "\r\n":
        规范 = 规范.replace("\n", "\r\n")
    临时 = 目标.with_name(目标.name + f".tmp{int(time.time() * 1000) % 100000}")
    # ★ `newline=""` 是关键：它让 write_text **原样落盘**，不再翻译
    临时.write_text(规范, encoding="utf-8", newline="")
    临时.replace(目标)
    # ★ 文件真被改了 ⇒ 这就是"文件变更"事件（TRIG-01 的即时触发）。
    #   挂在**写入成功之后**：写失败了不算变更 —— 记一个没发生的事，
    #   比漏记更坏（会让"哪一步引起的红"指错地方）。
    _事件("文件变更", 目标.name, {"路径": str(目标), "字节": len(规范 or "")})


def new_b_guard(片段) -> int:
    """replace 的写入字节（只算被替换进去的那一段，避免把整包上限算错）。"""
    try:
        return len(str(片段).encode("utf-8"))
    except Exception:                       # noqa: BLE001
        return 0


def _应用(根: Path, ops: list[dict]) -> tuple[list[str], list[str], int]:
    """应用操作 → (已应用, 未采纳, 写字节)。不抛异常。

    ★ 额外把"改了判据环境文件"记进函数属性 `_应用.最后标记`（保持返回签名稳定，
      调用方不必解构三个变四个值）。
    """
    已: list[str] = []
    未: list[str] = []
    标记: list[str] = []
    收缩: list[str] = []
    字节 = 0
    for op in ops[:单轮操作上限]:
        类 = str(op.get("op") or "").lower()
        if 类 == "write":
            p = _安全路径(根, str(op.get("path") or ""))
            内容 = op.get("content")
            if p is None or not isinstance(内容, str):
                未.append(f"write {op.get('path')}（越界/后缀/内容非法）")
                continue
            b = len(内容.encode("utf-8"))
            if b > 单文件上限 or 字节 + b > 整包上限:
                未.append(f"write {op.get('path')}（过大 {b}B）")
                continue
            相对写 = str(p.relative_to(根)).replace("\\", "/")
            if p.name in _禁改文件 or p.suffix.lower() in _禁改后缀:
                未.append(f"write {相对写}（判据环境文件，禁止模型修改）")
                continue
            if p.name in _标记文件:
                标记.append(相对写)
            # ★ 可疑收缩（2026-10-30 真跑发现）：模型常给"整篇重写"，
            #   顺手把与门无关的内容删掉——门看不见这个损失。这里只**标记不拦**：
            #   拦会挡住合理的精简，不标记则调用方以为仓没被动过。
            try:
                旧b = p.stat().st_size if p.is_file() else 0
            except OSError:
                旧b = 0
            if 旧b > 2000 and b < 旧b * 0.5:
                收缩.append(f"{相对写} {旧b}B→{b}B（-{100 - int(b * 100 / 旧b)}%）")
            p.parent.mkdir(parents=True, exist_ok=True)
            _原子写(p, 内容)
            字节 += b
            已.append(f"write {str(p.relative_to(根)).replace(chr(92), '/')}")
        elif 类 == "replace":
            # ★★ 2026-10-31 真实仓实测根因：`write` 要求**整篇内容**，
            #   修 26 KB 的 Rust 文件就得回吐 26 KB ⇒ 上游 130~200s 不返回（超时）。
            #   `replace` 只回吐被改的片段，长文件才能被及时修好。
            #   安全口径：`find` 必须在文件里**恰好命中 1 次**，否则拒绝（不猜、不模糊匹配）。
            p = _安全路径(根, str(op.get("path") or ""), 允许不存在=False)
            找 = op.get("find")
            换 = op.get("with")
            if p is None or not isinstance(找, str) or not isinstance(换, str) or not 找:
                未.append(f"replace {op.get('path')}（路径不存在/参数非法/找为空）")
                continue
            相对r = str(p.relative_to(根)).replace("\\", "/")
            if p.name in _禁改文件 or p.suffix.lower() in _禁改后缀:
                未.append(f"replace {相对r}（判据环境文件，禁止模型修改）")
                continue
            try:
                原文 = p.read_text(encoding="utf-8", errors="replace")
            except OSError as ex:
                未.append(f"replace {相对r}（读失败 {ex}）")
                continue
            命中 = 原文.count(找)
            if 命中 != 1:
                未.append(f"replace {相对r}（find 命中 {命中} 次，必须恰好 1 次）")
                continue
            新文 = 原文.replace(找, 换, 1)
            新b = len(新文.encode("utf-8"))
            if 新b > 单文件上限 or 字节 + new_b_guard(换) > 整包上限:
                未.append(f"replace {相对r}（结果过大 {新b}B）")
                continue
            if p.name in _标记文件:
                标记.append(相对r)
            旧b = len(原文.encode("utf-8"))
            if 旧b > 2000 and 新b < 旧b * 0.5:
                收缩.append(f"{相对r} {旧b}B→{新b}B（-{100 - int(新b * 100 / 旧b)}%）")
            _原子写(p, 新文)
            字节 += len(换)
            已.append(f"replace {相对r}")
        elif 类 == "delete":
            p = _安全路径(根, str(op.get("path") or ""), 允许不存在=False)
            if p is None:
                未.append(f"delete {op.get('path')}（不存在/越界）")
                continue
            try:
                p.unlink()
                已.append(f"delete {str(p.relative_to(根)).replace(chr(92), '/')}")
            except OSError as ex:
                未.append(f"delete {op.get('path')}（{ex}）")
        elif 类 == "rename":
            a = _安全路径(根, str(op.get("from") or ""), 允许不存在=False)
            b2 = _安全路径(根, str(op.get("to") or ""))
            if a is None or b2 is None:
                未.append(f"rename {op.get('from')}->{op.get('to')}（越界/不存在）")
                continue
            b2.parent.mkdir(parents=True, exist_ok=True)
            try:
                a.replace(b2)
                已.append(f"rename {str(a.relative_to(根)).replace(chr(92), '/')}"
                          f"->{str(b2.relative_to(根)).replace(chr(92), '/')}")
            except OSError as ex:
                未.append(f"rename {op.get('from')}->{op.get('to')}（{ex}）")
        else:
            未.append(f"{类}（不支持的操作类型）")
    if len(ops) > 单轮操作上限:
        未.append(f"超出单轮 {单轮操作上限} 个操作上限，余下 {len(ops) - 单轮操作上限} 个未执行")
    _应用.最后标记 = 标记
    _应用.最后收缩 = 收缩
    return 已, 未, 字节


async def 修复(仓: str | Path, 命令: list[str], 门: list[dict] | None = None,
             轮数: int = 2, 通道: str = "", 干跑: bool = False) -> dict:
    """跑门 → 红了让模型改（可新建/删除/重命名）→ 门复跑 → 绿保红全量还原。"""
    根 = Path(仓).resolve()
    if not 根.is_dir():
        return {"分类": "参数/前提不成立", "error": f"repo 不是目录：{根}",
                "修法": {"工具": "orch_fix", "参数": {"repo": "<存在的仓>", "cmd": list(命令)}}}
    from agentscope_orchestrator.gate_spec import 像测试命令
    给 = list(门 or [])
    if 像测试命令(命令) and not any(str((x or {}).get("type")) == "test_counts" for x in 给):
        spec = [{"name": "test_counts", "type": "test_counts", "cmd": list(命令),
                 "cwd": str(根), "min_passed": 1, "max_skipped": 0}] + 给
    else:
        spec = [{"name": "cmd_exit_ok", "type": "compile", "cmd": list(命令),
                 "cwd": str(根), "expect_exit": 0}] + 给
    记录: list[dict] = []
    t0 = time.time()

    # ★ 2026-11-01：判据环境基线。① 仓里**本来就躺着**禁改文件（前一轮/模型手写的
    #   `sitecustomize.py`）⇒ 这次的门读数天然不可信，必须点名，不能当没看见；
    #   ② 每轮改动前后比对，绕过黑名单的改动也躲不过哈希差分。
    判据基线 = _判据指纹(根)
    预置判据文件 = 预置点名(判据基线)
    _指纹边界 = ("仓内：sitecustomize/usercustomize/*.pth/conftest/pytest.ini/tox.ini/"
                 "setup.cfg/pyproject.toml 等；仓外：sys.path 全链上的 "
                 "sitecustomize.py / usercustomize.py / 顶层 *.pth + 解释器(大小/mtime/sha256)"
                 " + PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTHONUSERBASE 等（存哈希）。"
                 "仍未覆盖：非 Python 的启动机制、PATH 上的同名可执行、防火墙级网络面")

    res, audit = await rt.evaluate_async(spec, 根, 根, str(根))
    if all(res.values()):
        return {"分类": "达标", "修复轮数": 0, "门": _门表(res, audit),
                "说明": "首跑即绿，无需修改", "耗时_s": round(time.time() - t0, 2),
                "修复记录": [],
                "预置判据文件": 预置判据文件 or None,
                "判据指纹边界": _指纹边界}

    if 干跑:
        return {"分类": "未达标", "干跑": True, "门": _门表(res, audit),
                "修复记录": [], "说明": "干跑：未调用模型",
                "预置判据文件": 预置判据文件 or None,
                "判据指纹边界": _指纹边界}

    # ★ 早停（2026-10-30 实测教训）：门命令**本身跑不起来**时，让模型改代码是白烧时间。
    #   真跑里用 `python -m pytest` 当门，而那台 venv 没装 pytest ⇒ 环转了 960 s 才超时。
    #   "命令缺依赖/命令不存在" 是**调用方/环境**问题（该装依赖或换命令），不是产物问题。
    缺 = _缺依赖(audit)
    if 缺:
        return {"分类": "参数/前提不成立", "门": _门表(res, audit),
                "error": f"门命令本身跑不起来：{缺}",
                "说明": "这不是代码问题，改模型没用——先装依赖或改 cmd，再调用 orch_fix",
                "修法": {"工具": "orch_run",
                         "参数": {"repo": str(根),
                                  "cmd": ["<装好依赖后可用的一条命令>"]}},
                "修复记录": [],
                "预置判据文件": 预置判据文件 or None,
                "判据指纹边界": _指纹边界}

    from agentscope_orchestrator.gateway import (build_client, live_model_pairs,
                                                 select_live_channels)
    from agentscope_orchestrator.router import Router
    通道们, 报告 = select_live_channels(通道)
    对 = [(槽, build_client(c, m)) for 槽, c, m in live_model_pairs(通道们, 报告)]
    if not 对:
        return {"分类": "基础设施不可用", "门": _门表(res, audit),
                "error": f"没有可用模型通道：{json.dumps(报告.get('dead') or {}, ensure_ascii=False)[:300]}",
                "修法": {"工具": "orch_fix", "参数": {"repo": str(根), "cmd": list(命令),
                                                      "channel": "<可用通道名>"}}}
    路由 = Router(对)
    最后门 = _门表(res, audit)

    for 轮 in range(1, max(1, 轮数) + 1):
        证据, 提及 = _证据文本(audit)
        上下文, _ = _上下文(根, 提及, 证据=证据)
        # ★ 跨任务经验环：检索**已过门验证**的同类修法，作为提示（不是判据）。
        #   写入只发生在"门绿"分支 ⇒ 不存在"猜的被记成知道的"。
        经验文 = ""
        try:
            from agentscope_orchestrator import learning as _学
            经验文 = _学.取(命令, 证据)
            if 经验文:
                上下文 = 经验文 + "\n\n" + 上下文
            # ★★★★★ 分工线（2026-11-03 接）：**把上面四条知识源从"平铺"改成"有序判定"**。
            #
            #   此前的写法是：经验环、KB、semsearch、memory8 **全都无条件注入**。
            #   问题不在"注入了什么"，在于**没有"谁先谁后、命中就停"** ——
            #   于是模型每次拿到四段可能互相矛盾、且与本题无关的材料，
            #   它必须自己在脑子里判"哪段算数"。**那是把判定权又还给模型了。**
            #
            #   分工线把这件事变回机器的活：按 判据 → 知识库 → 盲区台账 的顺序判，
            #   **命中就停**，只在真的都没有时才把题交给模型，并且**明说"为什么找你"**。
            #
            #   ★ 为什么这一步很要紧：不告诉模型"前面几档都没答"，它会**倾向用通用做法补上**
            #     —— 那正是本项目要治的病（用训练先验替代项目判定基准）。
            #     所以分工线不光决定"谁做"，还把"为什么轮到你"交代清楚。
            #
            #   ★ 分工线**绝不拖垮修复主链**：任何异常都退回原来的平铺注入行为。
            try:
                from agentscope_orchestrator import dispatch as _派遣
                _裁 = _派遣.裁(命令=命令, 证据=证据, 项目根=根)
                if _裁.该谁 in ("知识", "台账") and _裁.依据:
                    # 命中 ⇒ 把**这一档的依据**放最前面（其余材料仍给，但退居其后）
                    上下文 = _派遣.给LLM的交代(_裁) + "\n\n" + 上下文
                elif _裁.该谁 == "LLM":
                    # 真盲区 ⇒ 交代清楚，并**记账**（这是"下次该加什么门"的原料）
                    上下文 = _派遣.给LLM的交代(_裁) + "\n\n" + 上下文
                    try:
                        _派遣.记这次(证据=证据, 上下文=(上下文 or "")[:400],
                                      来源=f"fixloop:轮{轮}")
                    except Exception:            # noqa: BLE001 记账失败绝不影响修复
                        pass
                # ★★★★★ 2026-11-03：**闭环后半段的接线**（真缺口，不是缺真跑）。
                #
                #   实测：`[坐标链] 719 条` 说明**真任务跑过很多次**，
                #   而 `[盲区闭环] 验过拦得住的 0`。查调用方才发现：
                #     记盲区 ✅ 接进了真流程（就是上面这几行）
                #     读队列 ✅ dispatch 会读并提醒
                #     **提为门 ✅ 修复环出口（`_提为门出口`，2026-11-04 接）**
                #     **验门   ✅ 同上 ＋ 循环内 `自动验门`（2026-11-03 接）**
                #   ⇒ 闭环**前半段接上了、后半段没接**。
                #     不接上，这套东西就是"**记了一堆该加门，然后没人加**"——
                #     台账越积越多，而 `验过拦得住的` 永远是 0。
                #
                #   ★ 为什么会漏接：`验门` 是这一轮刚加的（第221轮），
                #     加的时候只在判据里验了它，**忘了问"真流程谁会调它"**。
                #     ⇒ 口径：**新增一个机制之后，必须问一句"谁在真流程里调它"**；
                #       答不上来就说明这条线还没接上（判据全绿也白搭）。
                #
                #   ★ 放在这里而不是别处：这一轮刚记完盲区，此刻**试件最新鲜**
                #     （`最近证据` 就是刚才这道题）。过几轮再验，试件可能已被覆盖。
                #
                #   ★ 绝不拖垮修复主链：整个块包在 try 里，任何异常都吞掉。
                try:
                    from agentscope_orchestrator import blind_spot as _BS
                    from agentscope_orchestrator import project_kb as _KB2

                    # ★★★★★ 2026-11-03（第228轮）：**原来那个 `_造门` 是自证的，必须换掉。**
                    #
                    #   原写法：`_pat = (_候选 or "").strip()[:60]`
                    #           `return {"patterns": [re.escape(_pat)]}`   # 字面匹配
                    #   而调用处传的是 `_造门(u, str((证据 or "")[:60]))`
                    #   ⇒ **门是从试件回抄出来的**。
                    #
                    #   ★ **试件与模式同源 ⇒ 必然命中 ⇒ 永远得出"抓得住"。**
                    #     实测（复刻这段接线）：
                    #       验证: 拦得住(推断)
                    #       ⇒ 验证 == '未验证' 的条目数: **0**
                    #     ⇒ 下面 `if 验证=='未验证': _事件(...)` **永远不执行**
                    #       ⇒ **事件检查点永远是 0**。
                    #       （这就是目标③第二个数字为 0 的真因）
                    #
                    #   ★ 口径：**拿试件去造门，等于对照念。**
                    #     门必须来自**独立于试件**的地方 —— 即项目知识库那条规则。
                    def _造门(_用途: str, _候选: str = "") -> dict:
                        _c = (_候选 or "").strip()
                        if _c:
                            try:
                                for _r in (_KB2.查(关键词=_c[:24]) or []):
                                    _rid = str(_r.get("id") or "")
                                    if not _rid:
                                        continue
                                    for _g in (_KB2.门() or []):
                                        if _rid[:16] in str(_g.get("name") or ""):
                                            return dict(_g)
                            except Exception:            # noqa: BLE001
                                pass
                        # ★★★★★ 2026-11-04（第229轮）：**拿不到真门就交空 dict。**
                        #
                        #   原来这里返回一道"必然对不上的门"（pattern = 一句废话），
                        #   想法是"让验门如实得出没命中"。**实测证明那是错的**：
                        #   台账记成 `验证=没拦住 · 判法=反例实测`
                        #   —— 而真相是**连门都没有**。
                        #   ⇒ 看的人会去改一道**不存在的门**。
                        #
                        #   ⇒ 现在交空 dict，`验门` 会如实记 `验证=造不出门`：
                        #     「没门」与「门坏了」是两种缺陷、两种下一步动作
                        #     （没门 ⇒ 攒知识找判据；门坏了 ⇒ 改那道门）。
                        return {}

                    # ★ 不传假阳性件：真流程里**没有合规样本**，编一个就是造假。
                    #   ⇒ 结果停在「拦得住(推断)」并说出没验哪一半 —— 那是诚实状态。
                    # ★ 传 `仓`：让它用**门自己范围里的真文件**加强证据
                    #   （第227轮做的 `真文件反例候选`）。
                    #   ★ 用**顶层已 import 的 `Path`**（第30行）。第一版我写成
                    #     `_pathlib_module.Path(...)`，那是**猜的**、根本不存在。
                    #     又一次印证：**不要猜名字，去读文件。**
                    _仓 = str(Path(__file__).resolve().parent.parent.parent)
                    _验 = _BS.自动验门(
                        lambda u: _造门(u, str((证据 or "")[:60])),
                        上限=3, 仓=_仓)
                    for _x in _验:
                        # ★ 记事件的条件放宽：修好后正常结果是「拦得住(推断)」
                        #   或「没拦住」——两种**都值得留检查点**
                        #   （一个说明门有用，一个说明门没用）。
                        #
                        # ★★★★★ 2026-11-04（第229轮）：**新加的两档也必须进来。**
                        #
                        #   为什么这是一个真缺口而不是"顺手补个词"：
                        #   本轮的整条因果链是
                        #     `验门` 多判一档 `拦不住这一类` ⇒ `闭环概览` 把它排最前
                        #     ⇒ `doctor` 单列它 ⇒ **而 `fixloop` 的『仲裁』检查点不认它**
                        #   ⇒ 真跑时这一档**根本不会留下检查点**，
                        #     于是"闭环动了没有"这个数字在真数据上**又变回看不见**。
                        #
                        #   ★ 口径：**新增一个机制之后，必须问一句「谁在真流程里调它」。**
                        #     本仓栽过同型跟头（`提为门` 一直只有判据里调 ——
                        #     2026-11-04 才由 `_提为门出口` 接进修复环出口）。
                        #
                        #   四档的语义各不相同，但**都值得留检查点**：
                        #     · `拦得住(未验同类)` —— 这一次真拦住了，没验到这一类
                        #     · `拦不住这一类`     —— **只拦得住那一次**（旧算法会把它
                        #                             当成"验过 ⇒ 闭环闭合"，是最危险的一档）
                        if _x.get("验证") in ("未验证", "拦得住(推断)", "没拦住",
                                              "跳过", "出错", "造不出门",
                                              "拦不住这一类", "拦得住(未验同类)"):
                            _事件("仲裁", f"盲区{_x.get('键')}",
                                  {"验证": _x.get("验证"),
                                   "门名": _x.get("门名"),
                                   "判法": _x.get("判法")})
                except Exception:                # noqa: BLE001 验门失败绝不影响修复
                    pass
            except Exception:                    # noqa: BLE001 分工线坏了退回平铺注入
                pass
            # ★ 项目知识库：**每一轮都注入**（这是"项目口径"，不是"回收的经验"）。
            #   与八维记忆（轮>=2 才注入）分工不同：KB 是**人写的外挂知识**，
            #   优先级高于模型训练先验；memory8 是"这张病以前怎么治好的"。
            try:
                from agentscope_orchestrator import project_kb as _KB
                # ★★★★★ 2026-11-03 真事故：这里原来**算好了 `_键` 却没用** ——
                #   `_块kb = _KB.注入(关键词="", 上限字=700) if not _键.strip() else _KB.注入(上限字=700)`
                #   两条分支都**没给关键词** ⇒ 注入的是"按类别排好的固定 700 字前缀"，
                #   与当前这道题毫无关系。调用方以为在查，其实每次拿到同一段。
                #
                #   ⇒ 把命令 + 证据当关键词传进去，让 KB **按相关度检索**（project_kb 侧
                #     已同步改成"分词 + 覆盖度打分"，见那边的注释）。
                #   ★ 长度上限 1500：证据可能是长 traceback，全量喂进去只增加切词成本、
                #     不增加信号（打分量的是"规则被覆盖多少"，噪声不贡献命中）。
                _键 = (" ".join(str(x) for x in 命令) + " " + (证据 or ""))[-1500:]
                _块kb = _KB.注入(关键词=_键, 上限字=700)
                if _块kb:
                    上下文 = _块kb + "\n\n" + 上下文
            except Exception:        # noqa: BLE001 KB 坏了绝不影响修复主链
                pass
            # ★ semsearch（上游项目的已索引知识，本机 389 条）：**只读**接入。
            #   与 project_kb 分工：kb 是人写可改的真相；semsearch 是带 PR/行号追溯的检索面。
            #   `ORCH_SEMSEARCH=0` 关掉它（二进制不在、或换机器没这份索引时）。
            #   ★ 只做**条目检索**、超时 8s：它是修复主链上的旁路，绝不能把主链拖死
            #     （2026-11-03 实测：给目录做递归扫描把 CI 里的 mcp_smoke 顶到 900s 被杀）。
            try:
                if os.environ.get("ORCH_SEMSEARCH", "1") != "0":
                    from agentscope_orchestrator import semsearch_link as _SS
                    _块ss = _SS.注入(关键词=(证据 or "")[-300:] or "判据", 上限字=400, 超时=8.0)
                    if _块ss:
                        上下文 = _块ss + "\n\n" + 上下文
            except Exception:        # noqa: BLE001 检索失败绝不影响修复主链
                pass
            # ★ 八维记忆：按 (层, 判决) 收窄 + **证据等级硬过滤**（OVERSTATED/猜测永不返回）
            #
            # ★★ 只在**第 2 轮及以后**注入。测量依据（v0.13.9 冷/暖两趟基准）：
            #    11 个任务、每个 1 轮就过门 —— 首轮注入记忆 = 纯成本（+3.6% token，往返不变）。
            #    记忆要救的是"第一把没修对、卡住了"的时刻，不是"一次就改对"的时刻。
            if 轮 >= 2:
                try:
                    from agentscope_orchestrator import memory8 as _记8
                    _块8 = _记8.提示(_记8.检索(症状=证据, 证据=证据, 仓库=_记8.项目键(根), 上限=2))
                    if _块8:
                        上下文 = _块8 + "\n\n" + 上下文
                except Exception:        # noqa: BLE001 记忆坏了绝不影响修复主链
                    pass
        except Exception:            # noqa: BLE001 经验环坏了绝不影响修复主链
            经验文 = ""
        已有 = sorted(str(p.relative_to(根)).replace("\\", "/") for p in 根.rglob("*")
                      if p.is_file() and not any(x in _跳过目录
                                                 for x in p.relative_to(根).parts))
        提示 = _提示(根, 命令, 证据, 上下文, 轮, 已有)
        try:
            from agentscope_orchestrator.router import Msg
            # ★★★ 2026-10-31 真事故：`patience="auto"` 是**无人值守**语义（睡等冷却），
            #   按在交互式 MCP 调用上 ⇒ 真实仓一次 `orch_fix` 实测 **990.8 s** 才返回
            #   「基础设施不可用」，其中几百秒是"睡着等不会来的槽"。
            #   按 router 自己的 R62 口径：交互式必须 `fast`（不睡，立刻拒并给"何时可再试"）。
            #   另加**单轮墙钟上限**（默认 180s）：即使上游单次请求吊死，也不许把调用方钉住。
            _t模型 = time.time()
            回复 = await asyncio.to_thread(
                _带冷却等待的调用, 路由, 提示,
                _模型单轮上限(len(提示.encode("utf-8"))))
            模型耗时 = round(time.time() - _t模型, 1)
            _记延迟(模型耗时)
            原 = getattr(回复, "content", None)
            if 原 is None:
                原 = getattr(回复, "text", None) or str(回复)
            if isinstance(原, list):
                原 = "".join((b.get("text") or "") if isinstance(b, dict)
                             else (getattr(b, "text", "") or "") for b in 原)
            elif not isinstance(原, str):
                原 = str(原)
        except asyncio.TimeoutError:
            return {"分类": "基础设施不可用", "门": 最后门,
                    "error": (f"第 {轮} 轮模型调用超过 "
                              f"{round(_模型单轮上限(len(提示.encode('utf-8'))))}s 未返回"
                              "（上游吊死，已中止，不等）"),
                    "修复记录": 记录, "责任归属": "环境侧",
                    "修法": {"工具": "orch_run", "参数": {"repo": str(根), "cmd": list(命令)},
                             "说明": "模型侧不可用：先只判不修（orch_run 不调模型）；"
                                     "修复要等通道恢复或换 channel"}}
        except Exception as ex:                     # noqa: BLE001
            return {"分类": "基础设施不可用", "门": 最后门,
                    "error": f"第 {轮} 轮调用模型失败：{type(ex).__name__}: {ex}",
                    "修复记录": 记录, "责任归属": "环境侧",
                    "修法": {"工具": "orch_run", "参数": {"repo": str(根), "cmd": list(命令)},
                             "说明": "模型侧不可用：先只判不修（orch_run 不调模型）"}}
        ops, 模型说明 = _解析(原)
        if not ops:
            # ★ 零改动必须**可归因**（2026-10-31 真实仓实测：第 1 轮改动=[]，
            #   但记录里看不出是"模型拒绝"还是"解析失败" ⇒ 无法改进）。
            记录.append({"轮": 轮, "模型说明": 模型说明, "改动": [], "门": 最后门,
                        "原回复": (原 or "")[:400], "回复字节": len((原 or "").encode("utf-8")),
                        "模型耗时_s": 模型耗时, "提示字节": len(提示.encode("utf-8"))})
            continue

        快 = _快照(根)
        已应用, 未采纳, 字节 = _应用(根, ops)
        if not 已应用:
            记录.append({"轮": 轮, "模型说明": 模型说明, "改动": [], "未采纳": 未采纳,
                        "门": 最后门})
            continue
        条目经验 = 经验文 or None
        res2, audit2 = await rt.evaluate_async(spec, 根, 根, str(根))
        绿 = all(res2.values())
        # ★ 2026-11-01：门跑完了，先查"考试规则有没有被改"再看这个绿算不算数。
        #   黑名单只挡名单内的写法；哈希差分挡的是"任何改法"，包括从没被想到过的。
        判据后 = _判据指纹(根)
        判据异 = _判据差异(判据基线, 判据后)
        判据违规 = {k: [x for x in v if Path(x).name in _禁改文件
                        or Path(x).suffix.lower() in _禁改后缀]
                    for k, v in 判据异.items()}
        判据违规 = {k: v for k, v in 判据违规.items() if v}
        if 判据违规:
            绿 = False
        条目 = {"轮": 轮, "模型说明": 模型说明, "改动": 已应用, "未采纳": 未采纳,
                "模型耗时_s": 模型耗时, "提示字节": len(提示.encode("utf-8")),
                "经验命中": (条目经验 or "")[:200] or None,
                "写字节": 字节, "门": _门表(res2, audit2), "保留": 绿,
                "门环境改动": getattr(_应用, "最后标记", []) or None,
                "可疑收缩": getattr(_应用, "最后收缩", []) or None,
                "判据指纹差异": 判据异 or None,
                "判据环境被改": 判据违规 or None,
                "快照": {"文件数": 快["文件数"], "字节": 快["字节"], "完整": 快["完整"]}}
        if 判据违规:
            条目["说明"] = ("门读数作废：判据环境文件在门跑动前后被改（"
                          f"{json.dumps(判据违规, ensure_ascii=False)[:300]}）"
                          "⇒ 这个绿是改考试规则换来的，不算过门")
        if 绿:
            记录.append(条目)
            # ★ 门跑完了 ⇒ "命令完成"事件（TRIG-02）。
            #   ★ 门绿与门红**两个出口都要发** —— 只发绿的那个是本仓的经典毛病
            #     （"改一处漏三处"）。门红那次恰恰更值得记：**它是失败的那一步**，
            #     而"从哪一步开始红的"正是事件驱动要回答的问题。
            _事件("命令完成", " ".join(str(x) for x in (命令 or []))[:200],
                  {"结果": "绿", "轮": 轮, "门数": len(条目.get("门") or [])})
            # ★★★★★ 2026-11-04（第229轮后段）：**用真流程自己产出的那对原文验门。**
            #
            #   用户 2026-11-04 定向：反例取「**修好的那个文件**」。
            #   三样全是真的（修前快照 / 修后原文 / 刚由红转绿的那道门的模式），
            #   **一份都不用编**；细节与"证什么不证什么"见 `_验修前修后()`。
            _验修前修后(根, spec, 最后门, 快, 已应用, 轮)
            # ★★★★★ 2026-11-04（第230轮）：**出口处把「该加门」的盲区
            #   真的提成一道门**（`门规格` 存进台账，下一轮能被复用）。
            #   与上一行的分工：`_验修前修后` 判「这道门拦不拦得住」，
            #   这里把「已经拦住的那道门」**落成可复用的东西** ——
            #   只判不提，闭环就停在「记下了」上（真库台账里那两条只留门名的行）。
            _提为门出口(根, 轮)
            try:
                from agentscope_orchestrator import learning as _学
                _学.记(命令, 证据, 模型说明, 已应用, 条目.get("门"))
            except Exception:        # noqa: BLE001 记不上不影响本次结论
                pass
            # ★ 八维记忆（semsearch 结构键的移植）：只有**门真的绿**才写 SOLID(0)。
            #   为什么单开一层：扁平经验环只有"已验/未验"两档，答不了"这条属于哪一层"，
            #   也挡不住"被推翻的修法"混进同一条流 —— 八维把**证据等级**做成检索硬门。
            try:
                from agentscope_orchestrator import memory8 as _记8
                _记8.记(症状=证据[-1200:],
                        修法=(模型说明 or "") + "｜" + ", ".join(str(x) for x in (已应用 or [])),
                        证据=证据, 路径们=已应用 or (), 等级=0, 判决=5,
                        仓库=_记8.项目键(根), 动作=None)
            except Exception:        # noqa: BLE001 记忆坏了绝不影响修复主链
                pass
            return {"分类": "达标", "修复轮数": 轮, "门": _门表(res2, audit2),
                    "修复记录": 记录, "耗时_s": round(time.time() - t0, 2),
                    "预置判据文件": 预置判据文件 or None,
                    "判据指纹边界": _指纹边界,
                    "说明": "模型改动已通过机器判据门 ⇒ 保留（回退依据见 修复记录[].快照）"}
        条目["还原"] = _还原(根, 快)
        记录.append(条目)
        # ★ 门红出口也要发（与门绿出口对称）—— 见上面那段注释：
        #   只发绿的那一侧，事件链上就**没有失败**，而失败恰恰是最该被定位的。
        _事件("命令完成", " ".join(str(x) for x in (命令 or []))[:200],
              {"结果": "红", "轮": 轮, "还原": 条目.get("还原")})
        res, audit = res2, audit2
        最后门 = _门表(res2, audit2)

    # ★ 未达标出口**同样**是出口：这一轮真跑过（只是没修好），该提的门照提。
    #   不提的话，那些盲区要等到「下一次修好」才可能进队列 —— 而「下一次」
    #   在真数据里可能永远不来（本仓已删掉过一个「够不着」的阈值门）。
    _提为门出口(根, 轮)

    return {"分类": "未达标", "门": 最后门, "修复记录": 记录,
            "预置判据文件": 预置判据文件 or None,
            "判据指纹边界": _指纹边界,
            "耗时_s": round(time.time() - t0, 2),
            "说明": f"跑完 {max(1, 轮数)} 轮仍未过门；每轮改动均已按快照全量还原，仓保持原状",
            "修法": {"工具": "orch_run", "参数": {"repo": str(根), "cmd": list(命令)}}}


_延迟样本: list[float] = []          # 最近若干次**成功**的模型调用耗时（秒）


def _记延迟(秒: float) -> None:
    _延迟样本.append(float(秒))
    del _延迟样本[:-8]               # 只留最近 8 次


_冷却式 = re.compile(r"最早\s*(\d+)\s*秒后可试")


def _冷却秒(ex: BaseException) -> float | None:
    """从 `[CIRCUIT_OPEN] … 最早 N 秒后可试` 里取出 N（秒）。取不到 ⇒ None。"""
    m = _冷却式.search(str(ex))
    return float(m.group(1)) if m else None


def _可等上限() -> float:
    """调用方愿意为"等冷却"付出的秒数（默认 240，`ORCH_FIX_WAIT_MAX` 覆盖）。

    ★ 为什么由**调用方**决定（2026-10-31 用户提问引出）：
      路由器的 R62/R72 自动重连本来就在，但它把"要不要等"写成了 `patience`：
      `fast` = 不睡（交互式），`auto` = 最多睡 600 s（无人值守）。
      而修复环是**第三种主体**：它愿意等几分钟，但不愿被钉住十几分钟。
      ⇒ 口径：路由器照旧"立刻报 + 给出最早可试时间"，**等多久由修复环决定**（有界、记账）。
    """
    try:
        return max(0.0, min(900.0, float(os.environ.get("ORCH_FIX_WAIT_MAX") or 240)))
    except (TypeError, ValueError):
        return 240.0


def _模型调用带上限(路由, 提示: str, 上限: float):
    """在**独立线程**里跑模型调用，用硬 join 上限放弃。

    ★ 为什么 `asyncio.wait_for` 不够（2026-10-31 实测）：路由内部有**同步**探测
      （渠道探活走同步 HTTP），它会把事件循环堵住 ⇒ `wait_for` 的超时**根本触发不了**：
      实测把上限设成 1.5s，调用仍然花了 **55.1s** 才回来。
      ⇒ 要真能放弃，必须让它在**另一个线程**里跑，超时后**放弃线程**（daemon，不再等）。
      代价：被放弃的线程可能仍在后台跑完（结果丢弃），这是"宁可丢结果也不钉住调用方"的取舍。
    """
    import threading
    from agentscope_orchestrator.router import Msg
    箱: dict = {}

    def 跑() -> None:
        try:
            箱["r"] = asyncio.run(路由.call([Msg(role="user", content=提示)], patience="fast"))
        except BaseException as e:              # noqa: BLE001 原样带回主线程
            箱["e"] = e

    t = threading.Thread(target=跑, daemon=True, name="orch-model")
    t.start()
    t.join(max(5.0, float(上限)))
    if t.is_alive():
        raise TimeoutError(f"模型调用超过 {上限}s 未返回（线程已放弃，不再等）")
    if "e" in 箱:
        raise 箱["e"]
    return 箱.get("r")


def _带冷却等待的调用(路由, 提示: str, 上限: float):
    """调一次；若"全槽冷却"且最早可试时间在**可等上限**内 ⇒ 等一次再试（只等一次）。"""
    try:
        return _模型调用带上限(路由, 提示, 上限)
    except Exception as ex:                     # noqa: BLE001
        等 = _冷却秒(ex)
        if 等 is None or 等 > _可等上限():
            raise
        logger.warning("[fixloop] 全槽冷却，最早 %.0f 秒后可试 ⇒ 等一次再试"
                       "（上限 %.0f 秒；这是**有界等待**，不是死等）", 等, _可等上限())
        time.sleep(等 + 3.0)
        return _模型调用带上限(路由, 提示, 上限)


def _模型单轮上限(提示字节: int = 0) -> float:
    """单轮模型调用的墙钟上限（秒）：**按提示体积自适应**，并可用环境覆盖。

    ★ 为什么自适应（2026-10-31 真数据）：
      · 上游变慢时，小修复实测 **88.7 s**（同一条命令早先只要 6~9 s）；
      · 于是"固定 60 s"会**误杀正常运行**，而"固定 900 s"又会让吊死回到不可接受。
      ⇒ 预算跟着**工作量**走：60 s 起，每 KB 提示 +1 s，夹在 [60, 420]。
      · `ORCH_FIX_MODEL_TIMEOUT` 仍可强制覆盖（排障/实验用）。
    口径不变：**上限存在的意义是"能放弃"**，不是"给足时间"。
    """
    环 = os.environ.get("ORCH_FIX_MODEL_TIMEOUT")
    if 环:
        try:
            return max(10.0, min(900.0, float(环)))
        except (TypeError, ValueError):
            pass
    # ★ 2026-10-31 再修：固定公式（60s + 1s/KB）在**通道变慢**时会误杀正常任务——
    #   实测同一个通道上"只回一个字"的提示也要 **88.7 s**（早先同命令 6~9 s）。
    #   ⇒ 预算必须跟着**实测延迟**走：EWMA(成功调用耗时) 的 2.2 倍 + 1s/KB，夹在 [120, 900]。
    # ★ 2026-10-31 再修：新进程里 `_延迟样本` 为空 ⇒ 基准只有 120 s，而实测慢通道上
    #   20 KB 提示要 150~200 s ⇒ 第一次调用就被误杀。基准改为 240 s（`ORCH_FIX_BASE_SEC` 可配），
    #   有样本时再用 EWMA×2.2 抬高；总上限 600 s（真吊死靠"线程放弃"止血，不靠预算大小）。
    try:
        基 = float(os.environ.get("ORCH_FIX_BASE_SEC") or 240)
    except (TypeError, ValueError):
        基 = 240.0
    if _延迟样本:
        均 = sum(_延迟样本) / len(_延迟样本)
        基 = max(基, min(600.0, 均 * 2.2))
    return max(60.0, min(600.0, 基 + max(0, int(提示字节)) / 1024.0))


_缺依赖特征 = ("No module named", "ModuleNotFoundError", "command not found",
              "不是内部或外部命令", "is not recognized as an internal or external command",
              "cannot find the file", "系统找不到指定的文件", "ENOENT")


def _缺依赖(audit: list[dict]) -> str | None:
    """红门证据里出现"命令/模块不存在"⇒ 这是环境/参数问题，不该让模型去改代码。"""
    for a in audit or []:
        if a.get("ok"):
            continue
        err = (a.get("err") or "") + " " + (a.get("cause") or "")
        for 特 in _缺依赖特征:
            if 特.lower() in err.lower():
                行 = next((x.strip() for x in err.splitlines() if 特.lower() in x.lower()), 特)
                return 行[:160]
    return None


def _验修前修后(根, spec, 前门, 快, 已应用, 轮: int = 1) -> list[dict]:
    """★ **真流程验门**（第229轮后段）：修前当试件、修后当反例。**永不抛。**

    ## 为什么抽成独立函数（而不是留在 `修复()` 里内联）

    内联的话，"这条线接上了没有"就只能靠**相信**。
    抽出来之后判据可以**直接驱动它**：给一个真仓、一份真的修前快照、
    一张真的门表 —— 看它有没有真产出 `拦得住`。
    （本仓口径：**一段驱动不了的接线，等于没有接线。**）

    ## 它拿的三样东西全是真的

    · `快["项"][相对]["data"]` —— `_快照()` 在应用模型改动**之前**存的字节
    · 盘上现在的原文         —— 机器门判绿**之后**的样子
    · `spec` 里那道刚由红转绿的门的模式

    ⇒ **一份都不用编。**（第227轮的纪律：编一个反例就是造假。）

    ## 它证什么、不证什么

    证：**这道门恰好分得开「修前」和「修后」**（它认的就是这次修掉的那个东西）。
    不证：「能拦住这一类」（那一档由 `变异同类` 单独判）。
    不证：「修后的文件是合规的」（只证「它不再命中这道门」）。
    """
    出: list[dict] = []
    try:
        from agentscope_orchestrator import blind_spot as _BS
        _spec表 = {}
        for _g in (spec if isinstance(spec, list) else []):
            if isinstance(_g, dict):
                _spec表[str(_g.get("name"))] = _g
        _快项 = ((快 or {}).get("项") or {})
        for _m in (前门 or []):
            if _m.get("通过"):              # 只验**刚由红转绿**的那道
                continue
            _g = _spec表.get(str(_m.get("门")))
            if not _g or not _g.get("patterns"):
                continue
            _在 = str(_g.get("in") or "").replace("\\", "/").strip("/")
            for _相 in _动了哪些文件(已应用):
                if _相.strip("/") != _在:   # 门指名了哪个文件就只验那个文件
                    continue
                _旧b = (_快项.get(_相) or {}).get("data")
                if not isinstance(_旧b, (bytes, bytearray)):
                    continue
                try:
                    _修前 = bytes(_旧b).decode("utf-8", "replace")
                    _修后 = (Path(根) / _相).read_text(encoding="utf-8",
                                                      errors="replace")
                except (OSError, ValueError):
                    continue
                if not _修前.strip() or not _修后.strip():
                    continue
                _名 = f"修前修后-{_m.get('门')}"
                # ★ 2026-11-04（第229轮收尾）：**症状里必须有症状。**
                #   实测：真跑那一行写成 `禁setdefault钉编码：` —— 冒号后面**空的**。
                #   因为我取的是 `证据`（=门审计里的 `err`），而 `grep_forbidden`
                #   命中时 `err` 是空的、有信息的是 **`因由`（= `cause`，
                #   形如「禁词命中|环境\.setdefault\(」）**。
                #   ⇒ 后果两层，都不小：
                #     ① 人翻台账看不出这条盲区到底是什么；
                #     ② `键` 是从 `症状` 算出的指纹 ⇒ 症状塌成门名之后，
                #        **不同原因会并进同一行**，「次数」这个排序依据就失真了。
                #   （口径：记账的本事，一半在**记什么**。记了但记不下内容，
                #     和没记的区别只是多占一行。）
                _症状 = (f"{_m.get('门')}：{str(_m.get('因由') or '')[:100]}"
                         f"｜{str(_m.get('证据') or '')[:100]}")
                _r = _BS.记并验(
                    症状=_症状,
                    上下文=f"fixloop:轮{轮}",
                    # ★ 2026-11-04（第230轮）：**上限从 200 抬到 600。**
                    #   这一行存的是**那道门本身**（门规格 JSON），而出口的
                    #   `提为门` 要靠它认出「台账这一行是哪道门判的」。
                    #   截断 ⇒ `json.loads` 失败 ⇒ 认不出 ⇒ 拿一份更弱的样本
                    #   重验 ⇒ **把已经实测到的结论降级**（伪造降级）。
                    #   600 字足够装下 `in`+`patterns` 全是长正则的门。
                    候选判据=json.dumps(_g, ensure_ascii=False)[:600],
                    来源=f"fixloop:轮{轮}:修前修后",
                    造门=lambda _u, _gg=dict(_g): dict(_gg),
                    试件=_修前, 假阳性件=_修后, 门名=_名, 仓=str(根))
                出.append({"门名": _名, **(_r or {})})
                break
    except Exception:                            # noqa: BLE001
        pass
    return 出


def _门合法(g) -> dict:
    """门要能被 gates 直接用 ⇒ 先过**真校验器**；不合格交空 dict。

    ★ 宁可少提一道，不许提一道废的：门集只要有一条形状不对，
      `gate_spec.validate` 会**整批拒掉**（把能用的门也一起弄坏）。
    ★ 这里是"合法"的唯一出处，不另抄一份清单（本仓为"两处清单分叉"栽过多次）。
    """
    try:
        from agentscope_orchestrator import gate_spec as _GS
        if not isinstance(g, dict) or not g.get("type"):
            return {}
        return {} if _GS.validate([g]) else dict(g)
    except Exception:                            # noqa: BLE001
        return {}


def _盲区造门(根, 键: str, 症状: str, 候选s, 试件: str) -> dict:
    """**门从哪来**的策略：拿不到 ⇒ 交空 dict（台账会如实记「造不出门」）。

    两条真来源，**都要行的内容**才找得到：

    ```
    ① 台账行自己的 `候选判据们` —— 上一轮 `_验修前修后` 存进去的，
       就是那次真跑里**由红转绿的那道门**（门规格 JSON）；
    ② 项目知识库 `KB.门()`    —— 人写的外挂口径按（症状 + 证据）检索出来的门。
    ```

    ★ 门**不许**从试件回抄：门与试件同源 ⇒ 必然命中 ⇒ 永远"抓得住"，
      于是「造不出门」这一档**永远不出现**（`自动验门` 的 docstring 记着那次实测）。

    ★ 为什么它是**模块级函数**而不是出口里的闭包：闭包藏在函数体里，
      证据脚本就只能**照抄一份**它的逻辑 —— 照抄件一与真件分叉，
      那份"真流程读数"读到的其实是另一个东西。
    """
    for _c in (候选s or []):
        try:
            _o = json.loads(str(_c))
        except Exception:                        # noqa: BLE001 不是 JSON ⇒ 不是门规格
            continue
        _g = _门合法(_o)
        if _g:
            return _g
    try:
        from agentscope_orchestrator import project_kb as _KB2
        _词 = (" ".join([str(症状 or ""), str(试件 or "")[-1500:]]))[-1500:]
        _仓 = str(根)
        for _r in (_KB2.查(关键词=_词, 上限=3) or []):
            for _g in (_KB2.门(条们=[_r], 仓=_仓) or []):
                _g2 = _门合法(_g)
                if _g2:
                    return _g2
    except Exception:                            # noqa: BLE001 知识库坏了不阻断提门
        pass
    return {}


def _提为门出口(根, 轮: int = 1) -> list[dict]:
    """★ **修复环出口：把「该加门」的盲区真的提成一道门**（第230轮接线）。**永不抛。**

    ## 为什么必须有它

    `blind_spot` 的闭环设计是「记 → 该加门 → 提为门 → 那道门在**下一次同类缺陷**
    上拦红」。而在这之前，真流程里**出口没有任何人调 `提为门`** ——
    于是台账上积着"要求加门"、`门规格` 永远为空：**门名留下了，门本身没有地方存**，
    下一轮谁也拿不去用（"加过的门"只活在那一次的任务书里）。

    ★ 口径：**一段驱动不了的接线，等于没有接线。** 所以抽成函数、判据直接驱动
      （同 `_验修前修后`：那条线也是抽出来之后才发现"`已应用` 是操作描述不是路径"
      导致它**静默不触发**）。

    ## 抽成独立函数的理由（同 `_验修前修后`）

    内联 ⇒ "这条线接上了没有"只能靠**相信**。抽出来之后判据可以**直接驱动它**：
    给一个真仓、一条真的「该加门」台账行 —— 看它有没有真把门存进 `门规格`。

    ## 门从哪来（**独立于试件**）

    两条真来源，都要**行的内容**才找得到：

    ```
    ① 台账行自己的 `候选判据们` —— `_验修前修后` 存进去的就是那次真跑里
       **由红转绿的那道门**（判据门规格 JSON）；
    ② 项目知识库 `KB.门()`    —— 人写的外挂口径转出来的门（按症状检索规则）。
    ```

    ★ 门**不许**从试件回抄：试件与模式同源 ⇒ 必然命中 ⇒ 永远得出"抓得住"，
      于是"造不出门"这一档**永远不出现**（`自动验门` 的 docstring 里记着那次实测）。
    ★ 造出来的门先过 `gate_spec.validate` —— 它是**唯一**的合法性判据，
      不另抄一份清单（本仓为"两处清单分叉"栽过多次）。
    """

    出: list[dict] = []
    try:
        from agentscope_orchestrator import blind_spot as _BS

        def _造门(键: str, 症状: str, 候选s, 试件: str) -> dict:
            return _盲区造门(根, 键, 症状, 候选s, 试件)

        for _x in (_BS.提该加门的(造门=_造门, 仓=str(根), 上限=3) or []):
            出.append(_x)
            # ★ 只给**真提成的那一步**发事件：没提成的不改状态（它只是台账上一条
            #   "为什么没提"的读数）—— 给没变的事发事件，会让事件链指错地方。
            if _x.get("提成"):
                try:
                    _事件("状态转移", f"盲区{_x.get('键')}",
                          {"提为门": True, "门名": _x.get("门名"), "轮": 轮,
                           "结论": _x.get("结论")})
                except Exception:                # noqa: BLE001
                    pass
    except Exception:                            # noqa: BLE001 提门失败绝不影响修复
        pass
    return 出


def _动了哪些文件(已应用) -> list[str]:
    """从 `_应用()` 的操作描述里取出**相对路径**。

    ★ 为什么必须有它：`已应用` 的元素是**操作描述**，不是路径 ——
      实测格式（`_应用()` 里四处 append）：

    ```
    write   src/x.py
    replace mod.py
    delete  src/y.py
    rename  src/a.py → src/b.py
    ```

    ★ 我第一版直接把整串拿去跟门的 `in` 比 ⇒ **永远不相等** ⇒
      新接的「修前修后验门」**静默不触发**（真跑一次，台账 0 条才发现）。
      ⇒ 口径：**不要猜数据的形状，去读它的生产者。**
      这一条本轮第三次被自己违反（前两次见 `kb_retrieval` 的范围、
      `变异同类` 的 `patterns[1]`）。
    """
    出 = []
    for x in (已应用 or []):
        段 = str(x).split(None, 1)
        if len(段) < 2:
            continue
        路 = 段[1].strip()
        if 段[0] == "rename":                    # rename a → b ⇒ 取新的那个
            路 = 路.split("→")[-1].strip()
        if 路:
            出.append(路.replace("\\", "/"))
    return 出


def _门表(res: dict, audit: list[dict]) -> list[dict]:
    明细 = {a.get("gate"): a for a in (audit or [])}
    表 = []
    for 名, 过 in (res or {}).items():
        a = 明细.get(名) or {}
        表.append({"门": 名, "通过": bool(过), "因由": a.get("cause"),
                   "rc": a.get("rc"), "证据": (a.get("err") or "")[:400]})
    return 表
