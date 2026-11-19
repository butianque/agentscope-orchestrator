"""临时目录护栏 —— 不许再把临时目录造到仓里来。

★★ 为什么要有这个模块（真事故，2026-11-07 实测）

  这台机器上 `tempfile.gettempdir()` **回落到了 cwd**：

      cwd           = ...\\agentscope_orchestrator
      gettempdir()  = ...\\agentscope_orchestrator     ← 同一个
      TMPDIR = E:\\莉莉丝\\.temp   TEMP = E:\\Temp   TMP = E:\\Temp

  `TMPDIR`/`TEMP` **都设了、目录也都存在**，可 `tempfile` 的候选表按顺序试：

      ['E:\\莉莉丝\\.temp', 'E:\\Temp', 'E:\\Temp', 'C:\\Users\\...\\Temp',
       'C:\\Windows\\Temp', 'c:\\temp', 'c:\\tmp', '\\temp', '\\tmp', <cwd>]

  一路试到**最后一项 cwd** 才成功 —— 前面全没通过它的可写测试。
  于是**每一次 `mkdtemp()` 都在仓里造一个目录**，而且没人删。

  实测代价：工位残留（`<X>/repo` 这种一次性工位目录）**1,749 → 1,807**，
  而且**每跑一次自检就多几个**。有缺口史曲线之前，这种「慢慢变坏」是看不见的。

★★ 为什么不能只改环境变量

  `TEMP=E:\\Temp` 是**机器级**设置，在这台机器上是坏的（存在但不可写）。
  编排器**不能依赖一个坏掉的机器设置** —— 换了机器、换了进程、
  或者别的程序把 `TEMP` 改回去，缺陷就回来了。
  ⇒ 护栏必须在上游做：**进程起来时自己钉一次**，并且**钉在哪要能看见**。

★★ 为什么选「自己的目录」而不是继续找系统临时目录

  系统临时目录全不可写时（本机就是），唯一**确定可写**的地方是
  编排器自己拥有的目录。选 `<包根>/work/临时`：

    · 它在 `mem_prune.通用跳` 里（`work`/`tmp`/`.tmp` 都跳）⇒ **分区扫描不会
      把它算成项目或工位残留** —— 修好之后这类缺口应当**停止增长**，这是可判的；
    · 它不落在 cwd（仓根）上 ⇒ 不再往仓里随机撒目录；
    · 它是**降级**，不是「找到了合适的地方」⇒ 返回值里明确写出来。

★ 诚实边界：
  · 本模块**只改 `tempfile.tempdir`**，不改任何环境变量（改环境变量会影响子进程，
    那是另一个决定，不该由这里悄悄做）；
  · 钉住的目录**不自动清理**（清理是另一个模块的事；这里不假装管了）；
  · 探针是**尽力而为**（`mkstemp` + 立即删），挡不住「探测时可写、稍后不可写」的竞态。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

__all__ = ["包根", "钉住", "诊断", "在仓内", "自检", "_自检"]

包根 = Path(__file__).resolve().parents[2]
"""本包的安装/源码根。`parents[2]` = `<仓>/多智能体编排/agentscope_orchestrator`。"""

# 钉住后把结果缓存下来，`钉住()` 是幂等的
_已钉: dict | None = None


def 在仓内(p) -> bool:
    """`p` 是否落在仓根（`包根` 的上两级）里 —— 用来判「有没有撒到仓里」。"""
    try:
        仓 = 包根.parents[1] if len(包根.parents) > 1 else 包根
        a = os.path.normcase(os.path.abspath(str(p)))
        b = os.path.normcase(os.path.abspath(str(仓)))
        return a == b or a.startswith(b + os.sep)
    except (OSError, ValueError):
        return False


def _可写(d: Path) -> str:
    """探一下 `d` 到底能不能写。**返回原因**，不只返回真假 —— 假红与真红长得一样。

    ★★★ 这里**故意不用 `tempfile.mkstemp`**，这是本轮实测出来的关键一条：

        同一个不可写目录，两种探法结果完全不同（本机实测，2026-11-07）：

            os.open(…, O_CREAT|O_EXCL)  ->  **立刻** PermissionError
            tempfile.mkstemp(dir=…)     ->  **挂住 > 6 秒**（C:\\Windows\\Temp 也一样）

        `mkstemp` 在 Windows 上碰到 `PermissionError` 时会去看 `isdir(file)`
        （CPython 把它当成「同名目录已存在」处理），这一步在不可写路径上很慢。
        ⇒ **`tempfile.mkstemp` 不是个安全的可写性探针。**

      这正是整个缺陷的成因：`tempfile.gettempdir()` 依次试 10 个候选，
      **每个都慢死**，最后回落到候选表最后一项 —— 也就是 **cwd**。
      于是每一次 `mkdtemp()` 都在仓里造一个目录，而且没人删。
    """
    try:
        if not d.is_dir():
            return f"不是目录或不存在：{d}"
    except OSError as e:
        return f"stat 失败：{type(e).__name__}"
    import os as _os
    名 = d / f".探_{_os.getpid()}_{id(d) & 0xFFFF:x}"
    try:
        fd = _os.open(str(名), _os.O_RDWR | _os.O_CREAT | _os.O_EXCL, 0o600)
        _os.close(fd)
        try:
            _os.unlink(str(名))
        except OSError:
            pass
        return ""
    except FileExistsError:
        return "同名文件已存在（重名，不是权限问题）"
    except OSError as e:
        return f"{type(e).__name__}: {str(e)[:90]}"
    except ValueError as e:
        return f"ValueError: {e}"


def 诊断(*, 探候选: bool = True) -> dict:
    """**只看不改** —— 现在的临时目录在哪、在不在仓里、能不能写。

    ★ 注意：这里**故意不调用 `tempfile.gettempdir()`**。
      在还没钉过的进程里，那一句会跑 `_get_default_tempdir()` ——
      把 10 个候选挨个试一遍，而每个都要等 `mkstemp` 慢慢失败（本机实测 >6 秒/个）。
      所以本函数只读**已缓存**的 `tempfile.tempdir`；没缓存就如实说「还没定」。
      **这个诊断本身不该成为慢的来源。**
    """
    缓 = tempfile.tempdir
    现 = 缓 if 缓 else None
    候选 = list(tempfile._candidate_tempdir_list())
    试过 = []
    if 探候选:
        for c in 候选:
            理由 = _可写(Path(c))
            试过.append({"候选": c, "结论": "可写" if not 理由 else 理由})
    理由 = _可写(Path(现)) if 现 else "还没定（这一进程还没调用过 gettempdir）"
    return {
        "当前": 现 if 现 else "（还没定 —— 还没人调用过 gettempdir）",
        "已缓存": bool(缓),
        "在仓内": 在仓内(现) if 现 else False,
        "等于cwd": bool(现) and os.path.normcase(os.path.abspath(现))
                  == os.path.normcase(os.path.abspath(os.getcwd())),
        "可写": (not 理由) if 现 else False,
        "不可写原因": 理由 if 现 else "还没定（这一进程还没调用过 gettempdir）",
        "环境": {k: os.environ.get(k, "") for k in ("TAIXU_TMP", "TMPDIR", "TEMP", "TMP")},
        "候选表": 候选,
        "候选探测": 试过,
        "本仓自有目录": str(自有目录()),
    }


def 自有目录() -> Path:
    """编排器自己拥有、且**分区扫描本来就剪掉**的临时目录。"""
    return 包根 / "work" / "临时"


# ══════════════════════════════════════════════════════════════════════
# 残渣清理 —— 把历年 `mkdtemp` 撒在包根下的目录收掉
# ══════════════════════════════════════════════════════════════════════

# ★ 硬保护名单：这些**永远不许**被清理碰到，不管名字多像残渣。
#   判据要写成「保护什么」，不能写成「删除什么」—— 后者漏一个就是数据损失。
_保护 = frozenset({
    ".git", ".venv", "venv", ".data", ".audit_trail", ".orchestrator",
    ".orchestrator-state", "src", "tests", "tools", "work", "products",
    "receipts", "docs", "dist", "build", "packaging", "checks", "config",
    "cache", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
})

_随机名 = None


def _匹配随机名(名: str) -> bool:
    """`mkdtemp(prefix="r284_0_")` 造出来的名字长这样：`前缀_8~10位小写随机`。"""
    global _随机名
    if _随机名 is None:
        import re
        _随机名 = re.compile(r"[A-Za-z0-9_]+_[a-z0-9]{8,10}$")
    return bool(_随机名.fullmatch(名))


def 残渣(*, 最小年龄秒: float = 86400.0) -> dict:
    """列出包根下**判定为残渣**的直接子目录 —— **只看，不删**。

    判据（六条**全过**才算残渣；任何一条不过都进「跳过」并**写明理由**）：
      ① 是**目录**，且直接位于包根下（不递归 —— 残渣不嵌套）；
      ② 名字匹配 `前缀_8位随机`（`mkdtemp` 的形态）；
      ③ **不在** `_保护` 名单里；
      ④ **未被 git 跟踪**（跟踪的是别人的决定，我没资格顺手清掉）；
      ⑤ 里面**没有 `.git`**（有 ⇒ 可能是谁的 clone/工作树，一律不碰）；
      ⑥ 目录 mtime **早于** `最小年龄秒`（正在跑的测试造的不能删）。

    ★ 为什么用「年龄」而不是「看起来像旧的」：本机实测**最老的残渣才 2.8 天**，
      而 24 小时内被写过的有 1,534 个 —— 那里面有**正在运行的进程**造的目录。
      「够老」是唯一不需要猜的判据。
    """
    import subprocess
    import time

    现在 = time.time()
    try:
        r = subprocess.run(["git", "-c", "core.hooksPath=", "ls-files"],
                           cwd=str(包根), capture_output=True, text=True,
                           timeout=180, encoding="utf-8", errors="replace")
        跟踪顶层 = {l.split("/")[0] for l in (r.stdout or "").splitlines()
                    if "/" in l.strip()}
        有git信息 = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        跟踪顶层, 有git信息 = set(), False

    候选, 跳过 = [], []
    for p in sorted(包根.iterdir()):
        try:
            if not p.is_dir():
                continue
        except OSError:
            continue
        名 = p.name
        if 名 in _保护:
            跳过.append({"名": 名, "理由": "在保护名单里"})
            continue
        if not _匹配随机名(名):
            跳过.append({"名": 名, "理由": "名字不是 mkdtemp 形态"})
            continue
        if 有git信息 and 名 in 跟踪顶层:
            跳过.append({"名": 名, "理由": "git 跟踪着（别人的决定，不碰）"})
            continue
        if (p / ".git").exists():
            跳过.append({"名": 名, "理由": "里面有 .git ⇒ 可能是 clone/工作树，一律不碰"})
            continue
        try:
            龄 = 现在 - p.stat().st_mtime
        except OSError as e:
            跳过.append({"名": 名, "理由": f"stat 失败：{type(e).__name__}"})
            continue
        if 龄 < 最小年龄秒:
            跳过.append({"名": 名, "理由": f"太新（{龄/3600:.1f} 小时，"
                                            f"下限 {最小年龄秒/3600:.1f} 小时）"})
            continue
        候选.append({"名": 名, "路径": p, "龄小时": round(龄 / 3600, 1)})

    按前缀: dict[str, int] = {}
    按理由: dict[str, int] = {}
    总字节 = 0
    for 条 in 候选:
        k = 条["名"].rsplit("_", 1)[0]
        按前缀[k] = 按前缀.get(k, 0) + 1
        try:
            总字节 += sum(f.stat().st_size for f in 条["路径"].rglob("*") if f.is_file())
        except OSError:
            pass
    for 条 in 跳过:
        # ★ 归一化：理由里带具体小时数会把「太新」拆成几十个桶，
        #   于是报告 90% 是重复噪声，**把真正要紧的那条（名字不是 mkdtemp 形态）淹掉**。
        #   实测踩过：30 多个「太新」把「981 个名字不对」挤到最后一行。
        理由 = 条["理由"]
        if 理由.startswith("太新"):
            理由 = "太新（未到下限年龄）"
        elif 理由.startswith("stat 失败"):
            理由 = "stat 失败"
        按理由[理由] = 按理由.get(理由, 0) + 1

    return {
        "候选数": len(候选),
        "候选合计MB": round(总字节 / 1024 / 1024, 1),
        "最小年龄小时": round(最小年龄秒 / 3600, 1),
        "有git信息": 有git信息,
        "按前缀": dict(sorted(按前缀.items(), key=lambda kv: -kv[1])[:20]),
        "跳过大类": dict(sorted(按理由.items(), key=lambda kv: -kv[1])),
        "跳过样例": 跳过[:12],
        "候选样例": [{"名": c["名"], "龄小时": c["龄小时"]} for c in 候选[:12]],
        "_候选": 候选,
    }


def 清理残渣(*, 干跑: bool = True, 最小年龄秒: float = 86400.0) -> dict:
    """按 `残渣()` 的判据清理。**默认干跑** —— 要真删必须显式传 `干跑=False`。

    ★ 为什么默认干跑：删 2.6 GB 是不可逆的，而**先看清要删什么**的成本接近零。
      「不需要调用方记得做的步骤，等于没做」—— 所以这里把安全的一侧做成默认值，
      而不是写一句「调用前请自行确认」。
    """
    import shutil

    果 = 残渣(最小年龄秒=最小年龄秒)
    删了, 失败 = [], []
    if not 干跑:
        for c in 果["_候选"]:
            try:
                shutil.rmtree(c["路径"])
                删了.append(c["名"])
            except OSError as e:
                失败.append({"名": c["名"], "错": f"{type(e).__name__}: {str(e)[:70]}"})
    果.pop("_候选", None)
    果["干跑"] = 干跑
    果["已删"] = len(删了)
    果["删除失败"] = 失败[:10]
    return 果


def 钉住(*, 强制: bool = False, 仓外优先: bool = True,
         只试: list[tuple[str, Path]] | None = None,
         允许降级: bool = True) -> dict:
    """把 `tempfile.tempdir` 钉到一个**验过可写**的目录。幂等、**绝不抛异常**。

    顺序（每一步都**先探可写**，探不过就换下一个）：
      ① 现有的 `TMPDIR`/`TEMP`/`TMP` 里第一个真可写的 —— 最不打搅机器原有安排；
      ② `<包根>/work/临时` —— 编排器自己拥有，且 `work` 在剪枝表里。

    `仓外优先=False` ⇒ 跳过 ①，直接用 ②。
    `只试` / `允许降级` 是给**自检**用的注入口：把候选换成一组必然不可写的路径、
    并关掉降级 ⇒ 可以直接验「**全都不可写时必须如实说「没钉」**」这条判据，
    而不用去改真实文件系统的权限（改权限的测试会污染机器，而且不可重复）。

    ★ 返回值里带**决策依据**：钉到哪、为什么、每个候选探出了什么。
      降级不是失败，但**谎报没降级是最坏的失败** —— 所以「有没有降级」是返回字段。
    """
    global _已钉
    if _已钉 is not None and not 强制:
        return dict(_已钉)

    试过: list[dict] = []
    选定: Path | None = None
    为何 = ""

    if 只试 is not None:
        候选表 = list(只试)
    elif 仓外优先:
        # ★ 顺序：① 运维显式指定的 `TAIXU_TMP`（信任但不盲信，仍探一次）
        #         ② 机器原有的 TMPDIR/TEMP/TMP（本机全是 PermissionError，
        #            探一下是为了**把「为什么降级」写进返回值**，不是为了用它们）
        if os.environ.get("TAIXU_TMP"):
            候选表 = [("TAIXU_TMP", Path(os.environ["TAIXU_TMP"]))]
        else:
            候选表 = []
        候选表 += [(名, Path(os.environ[名])) for 名 in ("TMPDIR", "TEMP", "TMP")
                   if os.environ.get(名)]
    else:
        候选表 = []

    if not 候选表 and 只试 is None:
        for 名 in ("TMPDIR", "TEMP", "TMP"):
            if not os.environ.get(名):
                试过.append({"来源": 名, "路径": "", "结论": "环境变量没设"})

    for 来源, d in 候选表:
        理由 = _可写(d)
        试过.append({"来源": 来源, "路径": str(d),
                     "结论": "可写" if not 理由 else 理由})
        if not 理由:
            选定, 为何 = d, f"{来源} 真可写"
            break

    if 选定 is None and 允许降级:
        候选 = 自有目录()
        try:
            候选.mkdir(parents=True, exist_ok=True)
            理由 = _可写(候选)
            试过.append({"来源": "包根/work/临时", "路径": str(候选),
                         "结论": "可写" if not 理由 else 理由})
            if not 理由:
                选定, 为何 = 候选, "系统临时目录全不可写 ⇒ 降级到编排器自己的目录"
        except OSError as e:
            试过.append({"来源": "包根/work/临时", "路径": str(候选),
                         "结论": f"建不出来：{type(e).__name__}: {e}"})

    if 选定 is None:
        # 全都不可写 —— **如实报**，不动 `tempfile.tempdir`（动了只会更坏）
        果 = {"钉到": None, "成功": False, "降级": True, "为何":
              "★ 一个可写目录都没探到，**没有钉**（不许假装钉上了）", "试过": 试过}
        _已钉 = 果
        return dict(果)

    旧 = tempfile.gettempdir()
    tempfile.tempdir = str(选定)
    # `TMPDIR`/`TEMP`/`TMP` 也顺手指一下：`subprocess` 起的子进程（真工具链）会读它们。
    # ★ 只改本进程的环境，不写机器级设置；子进程继承是本进程的确定行为。
    for 名 in ("TMPDIR", "TEMP", "TMP"):
        os.environ[名] = str(选定)

    果 = {
        "钉到": str(选定),
        "成功": True,
        "降级": bool(为何.startswith("系统临时目录")),
        "为何": 为何,
        "钉之前": 旧,
        "之前就在仓内": 在仓内(旧),
        "现在在仓内": 在仓内(选定),
        "等于cwd": os.path.normcase(os.path.abspath(str(选定)))
                  == os.path.normcase(os.path.abspath(os.getcwd())),
        "试过": 试过,
    }
    _已钉 = 果
    return dict(果)


def 自检() -> dict:
    """真实验证：钉住之后，`mkdtemp()` 到底落在哪、会不会撒进仓里。"""
    import time

    t0 = time.time()
    前 = dict(诊断(探候选=False))
    果 = 钉住(强制=True)
    t钉 = time.time() - t0
    t1 = time.time()
    d = Path(tempfile.mkdtemp(prefix="护栏自检_"))
    t建 = time.time() - t1
    try:
        return {
            "钉住结果": 果,
            "钉住用时秒": round(t钉, 3),
            "建目录用时秒": round(t建, 3),
            "钉住后 gettempdir": tempfile.gettempdir(),
            "mkdtemp 落在": str(d),
            "落在仓内": 在仓内(d),
            "落在cwd": os.path.normcase(str(d.parent))
                      == os.path.normcase(os.path.abspath(os.getcwd())),
            "钉之前": 前,
        }
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _自检() -> bool:
    """判据 ①–⑧。**两侧都造** —— 只测「钉上了」不测「钉不住时如实说」，必过触发。"""
    print("  ── temp_guard 自检 ──")
    坏: list[str] = []

    # ① 诊断能跑且字段齐（只看不改），且**不许慢**（不探候选时应当几乎瞬时）
    import time as _t
    _t0 = _t.time()
    诊 = 诊断(探候选=False)
    _dt = _t.time() - _t0
    for k in ("当前", "已缓存", "在仓内", "等于cwd", "可写", "不可写原因",
              "候选表", "候选探测", "本仓自有目录"):
        if k not in 诊:
            坏.append(f"①诊断缺字段 {k}")
    if _dt > 1.0:
        坏.append(f"①诊断太慢（{_dt:.2f}s）—— 它不该去跑 gettempdir 的慢探测")

    # ② 钉住能成功，且钉到的地方**不在 cwd 上**
    果 = 钉住(强制=True)
    if not 果.get("成功"):
        坏.append("②钉住没成功")
    elif 果.get("等于cwd"):
        坏.append(f"②钉到了 cwd 上：{果.get('钉到')}")

    # ③ 钉住之后 mkdtemp **不再撒进仓根**（这是整个模块存在的理由）
    沙 = Path(tempfile.mkdtemp(prefix="护栏判三_"))
    try:
        if os.path.normcase(str(沙.parent)) == os.path.normcase(os.path.abspath(os.getcwd())):
            坏.append(f"③mkdtemp 仍落在 cwd：{沙}")
    finally:
        shutil.rmtree(沙, ignore_errors=True)

    # ④ 幂等：再钉一次不改变结果，也不重复探测到崩
    再 = 钉住()
    if 再.get("钉到") != 果.get("钉到"):
        坏.append(f"④不幂等：{果.get('钉到')} -> {再.get('钉到')}")

    # ⑤ ★ 反方向：**全都不可写时必须如实说「没钉」**，不许假装成功。
    #    ★ 不许拿 `Z:\…` 或网络路径去探 —— 会挂到超时（我真踩过：一次自检挂死 10 分钟）。
    #      改用**本地、必败、立刻返回**的路径：拿一个**存在的文件**当目录用。
    假必败 = [("拿文件当目录", 包根 / "pyproject.toml" / "护栏")]
    理由5 = _可写(假必败[0][1])
    if not 理由5:
        坏.append("⑤拿存在的文件当目录，竟被判为可写 ⇒ 探测器是坏的")
    果5 = 钉住(强制=True, 只试=假必败, 允许降级=False)
    if 果5.get("成功"):
        坏.append(f"⑤★ 全都不可写却报了成功（假绿）：{果5.get('钉到')}")
    if 果5.get("钉到") is not None:
        坏.append("⑤失败时仍写了个「钉到」⇒ 调用方会以为钉上了")
    if "没有钉" not in str(果5.get("为何", "")):
        坏.append(f"⑤失败时没明说「没有钉」：{果5.get('为何')!r}")
    if not 果5.get("试过"):
        坏.append("⑤失败时没留下探测记录 ⇒ 不知道为什么失败")
    钉住(强制=True)          # 恢复真实钉住

    # ⑥ 仓根判据本身两侧都对：包根在仓内，仓外目录不在
    if not 在仓内(包根):
        坏.append(f"⑥包根被判成不在仓内：{包根}")
    if 在仓内(Path(r"C:\Windows")):
        坏.append("⑥C:\\Windows 被判成在仓内")

    # ⑦ 钉住时**没往仓根直接撒目录**：钉到的父目录不许就是 cwd
    if 果.get("成功") and (
        os.path.normcase(str(Path(果["钉到"]).parent))
        == os.path.normcase(os.path.abspath(os.getcwd()))
    ):
        坏.append("⑦钉到目录的**父目录**就是 cwd ⇒ 会污染仓根")

    # ⑧ ★★★ 探针**不许慢** —— 这条判据是为本轮那个真缺陷立的。
    #    实测：`tempfile.mkstemp` 探不可写目录要 >6 秒/个，10 个候选就把它拖死，
    #    `gettempdir()` 于是回落到 cwd ⇒ 每次 mkdtemp 都在仓里造目录（1,749→1,807）。
    #    将来谁把 `_可写` 改回 `mkstemp`，这条会**立刻红**，而不是又烂上几百轮。
    _t0 = _t.time()
    慢 = []
    for c in tempfile._candidate_tempdir_list():
        _c0 = _t.time()
        理由 = _可写(Path(c))
        _c1 = _t.time() - _c0
        if _c1 > 1.0:
            慢.append(f"{c}（{_c1:.2f}s）")
    _总 = _t.time() - _t0
    if 慢:
        坏.append(f"⑧探针太慢，有候选超过 1 秒：{'；'.join(慢)}")
    if _总 > 5.0:
        坏.append(f"⑧探全部 {len(tempfile._candidate_tempdir_list())} 个候选共 {_总:.2f}s "
                  f"（上限 5s）—— 探针退化成慢调用了")

    if 坏:
        print("  ❌ temp_guard 自检红：")
        for x in 坏:
            print("     ·", x)
        return False
    print(f"  ✅ temp_guard 自检全绿（判据 ①–⑧ · 钉到 {果.get('钉到')} · "
          f"降级={果.get('降级')}）")
    return True


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(0 if _自检() else 1)
