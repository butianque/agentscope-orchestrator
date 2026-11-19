# -*- coding: utf-8 -*-
"""隔离工作树 —— 无人值守跑在 git worktree 里，**主树全程一个字节都不写**。

═══════════════════════════════════════════════════════════════════════
为什么需要它（真跑抓出的缺口，2026-10-16）
═══════════════════════════════════════════════════════════════════════
编排器原状**直接写主树**：`_apply_file` 落盘到 `REPO_ROOT / rel`，
备份在 `.audit_trail/generic_backup`，红门就回滚。

★ 这对「**跑一夜**」这种全自动形态是**结构性**不成立的：

  ① **主树在跑动期间处于中间态** —— 用户/编辑器/别的 agent 随时可能
     在同一棵树上干活。实测（2026-10-16 10:2x）：主树有 **563 个未提交
     改动**，且 `src/模块甲/*`、`src/判据观测.rs` 正被**另一个 agent**
     持续修改；同时本工具也在写同一批文件。
  ② R54 只在**文件级**加了并发守卫（让开、不覆盖），但**没有把冲突消掉** ——
     它只是把「静默覆盖」变成「拒写并报错」⇒ 无人值守会**空转**。
  ③ **回滚是常规路径**（判红就回滚）。也就是说跑一夜 = 反复改主树又改回来。
     期间任何一次外部写入，都会让「安全网」变成「吃掉别人工作」的机器。
  ④ 用户**没法同时用这棵树**。

★ 项目自己早就在用隔离树：`示例工程_ts187` / `_ts199` / `_ts203` /
  `_wt_ts381` 都是 `git worktree`。**工具反而没有**这个能力。

═══════════════════════════════════════════════════════════════════════
修法：跑在隔离树，绿了才回灌
═══════════════════════════════════════════════════════════════════════
    ① `git worktree add -b orch/<标签> <父目录>/<仓库名>_orch_<标签> HEAD`
    ② **把主树当前的未提交改动同步过去** —— 跑的人看到的必须是**真状态**，
       不是干净的 HEAD（否则判据门的结果与用户看到的不一致，属于假报告）。
    ③ 把同步后的状态**提交成基线** ⇒ 之后 `diff 基线` 就是**这次跑的净改动**。
    ④ 临时档案：`repo_root` = 隔离树（档案放在隔离树根 ⇒ 相对路径自然指过去），
       `audit_dir` = **主树**的绝对路径 ⇒ **回执不随隔离树删除而消失**。
    ⑤ 跑：仍是产品自己的入口（`generic_goal`），**不另起一套执行机制**。
    ⑥ 绿 ⇒ `回灌`：**逐文件指纹核对**，别人动过的**让开不覆盖**（R54 口径）。
       红 ⇒ `拆` 掉隔离树，**主树自始至终没被动过**。

★ 诚实边界：
  · 回灌是**逐文件整文件拷贝**，不是 git merge —— 本工具不该替用户猜语义合并。
    冲突就如实报「这些文件别人动过，没回灌」，交人处理。
  · 隔离树**不复制** `.gitignore` 排除的大数据目录（那些本来就不入 git）；
    若判据门依赖它们，需在档案里声明（当前未声明 ⇒ 已知限制，非静默）。
  · `cargo` 在隔离树里要**重新编译**（target 不共享）⇒ 首次门会慢很多。
    这是隔离的代价，不是缺陷；共享 target 会让两边互相污染，宁可慢。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentscope_orchestrator import profile as _pf  # noqa: E402

# 这些不往隔离树里搬：版本库元数据、构建产物、以及**本工具自己的运行期目录**
#   （回执/备份必须留在主树，否则隔离树一删，审计记录就没了）
不搬 = {".git", "target", ".lake", ".venv", "__pycache__", "node_modules"}


class 隔离树错(RuntimeError):
    pass


def _git(cwd: Path, *args: str, 超时: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=超时)


def _git字节(cwd: Path, *args: str, 超时: int = 600) -> bytes | None:
    """按**字节**取 git 输出（`text=True, encoding="utf-8", errors="replace"` 会翻译行尾 ⇒ 不能用来比字节）。"""
    cp = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                        timeout=超时)
    return cp.stdout if cp.returncode == 0 else None


def _解析状态(文本: str) -> list[tuple[str, str, str]]:
    """解析 `git status --porcelain`。返回 [(状态, 相对路径, 新路径)]。

    ★ 用**文本**形态而非 `-z`：`-z` 下改名条目占两个 NUL，解析更绕且易错。
      代价是**含换行的文件名**解析不了 —— 那种情况**如实跳过并记数**，
      不静默当成没问题（「报表与真实状态不一致，比没有报表更坏」）。
    """
    出: list[tuple[str, str, str]] = []
    for 行 in 文本.splitlines():
        if len(行) < 4:
            continue
        态 = 行[:2]
        余 = 行[3:]
        if 态[0] in ("R", "C") and " -> " in 余:
            旧, 新 = 余.split(" -> ", 1)
            出.append((态, 旧.strip('"'), 新.strip('"')))
        else:
            出.append((态, 余.strip('"'), ""))
    return 出


def _同步脏文件(主树: Path, 树: Path) -> dict:
    """把主树**未提交**的改动搬到隔离树，让跑的人看到真状态。

    返回 {"搬": n, "删": n, "跳过": n, "前": {相对路径: 指纹}}。
    `前` 是**回灌时的核对基准**：主树这些文件若在跑动期间被别人改过，
    回灌必须让开（R54 口径）。
    """
    cp = _git(主树, "status", "--porcelain")
    if cp.returncode != 0:
        raise 隔离树错(f"读主树状态失败：{cp.stderr.strip()}")
    条目 = _解析状态(cp.stdout)
    搬 = 删 = 跳 = 0
    前: dict[str, str] = {}
    for 态, rel, 新 in 条目:
        # 本工具自己的运行期目录不搬（回执/备份留主树）
        if Path(rel).parts and Path(rel).parts[0] in 不搬:
            跳 += 1
            continue
        if "\n" in rel or "\n" in 新:
            跳 += 1
            continue
        源 = 主树 / rel
        标 = 树 / rel
        if 态.strip() == "D":
            if 标.exists():
                标.unlink()
            删 += 1
            continue
        if 态.strip() == "R" and 新:
            # 改名：把新路径的内容搬过去（旧路径在隔离树里本就不存在）
            源 = 主树 / 新
            标 = 树 / 新
            rel = 新
        if not 源.is_file():
            跳 += 1
            continue
        标.parent.mkdir(parents=True, exist_ok=True)
        # ★★★★★ R56（真仓库实测抓出 · 2026-10-16）：**先取字节，再写，再拿同一份字节算指纹**。
        #   【现象】真仓库 486 个同步文件里 **1 个**与记录的指纹不一致；
        #     合成自检（临时小仓库、无并发写者）**测不出来**。
        #   【根因】原写法是「读源 → 写标 → **重新读源**算指纹」。
        #     而真仓库有**活的外部写者** ⇒ 在"读"与"重读"之间源文件被改
        #     ⇒ 记下的指纹**不是我们真正写下去的那份字节**。
        #   ★ 后果比"计数差 1"严重：`前` 是**回灌时判断"别人动过没有"的基准**
        #     （R54 口径）。基准错了 ⇒ 要么**误拒**合法回灌，要么**误放**
        #     覆盖别人的改动 —— 后者正是 R54 要防的事。
        #   ⇒ 修法：指纹取自**手上这份字节**，与"写了什么"由构造同一。
        _字节 = 源.read_bytes()
        标.write_bytes(_字节)                # ★ 字节级：不碰行尾、不碰非法字节
        前[rel] = hashlib.sha1(_字节).hexdigest()
        搬 += 1
    return {"搬": 搬, "删": 删, "跳过": 跳, "前": 前}


def 构建缓存(主: Path) -> Path:
    """**跨隔离树存活**的构建缓存目录。

    ★ 为什么必须有（否则隔离会造出**假红**）：
      隔离树是新建的 ⇒ 里面**没有** `target/`（本项目主树那个有 **82 GB**）。
      不带缓存 ⇒ 每次跑都是**冷编译**；而门的 timeout 是按**暖编译**标定的
      （项目自己记：判据质量门 65 秒，门 timeout 900 秒）。
      ⇒ 冷编译一旦超时，就是**假红门**：合格产出被拒，而报表看着像真问题。
      ★ 本工具一贯口径：**假红比无齿更坏**。
      ⇒ 用一个持久目录：第一次冷、之后暖。目录名带仓库名 ⇒ 多仓库互不污染。
    """
    d = 主.parent / f"{主.name}_orch_target"
    d.mkdir(parents=True, exist_ok=True)
    return d


def 树在动(主: Path, 间隔: float = 20.0, 最多等: float = 200.0) -> dict:
    """量主树**是不是正在被外部写者改**；等到它静下来（或等到超时）。

    ★★★★★ R61（真无人值守抓出 · 2026-10-16）：**隔离只解决"我弄脏主树"，
      没解决"主树在动"。**
      【现象】12:09 建的隔离树里 `cargo check --lib` 有 **4 个 serde 错误**，
        编排器据此把载体从「修判据质量门」改成「修编译红门」，花掉约 20 分钟
        与模型预算。而 **12:30 主树 `cargo check --lib` 零错误** ——
        `规律网络.rs` 改于 12:07、`mod.rs` 改于 12:23，都在快照前后几分钟内。
      【根因】外部写者（另一个 agent）正在编辑；隔离树快照到的是它
        **没写完的中间态**。这个缺口**几秒后就不存在了** —— 是**幽灵缺口**。
      ★ 危险在于它**看起来完全正常**：探针真跑了、真红、计划真覆盖了必改文件、
        增量裁决真在收敛 —— **每一环都在尽职，只是整件事没有意义**。
      ⇒ 口径：**缺口必须"锚得住"** —— 一个只在转瞬即逝的中间态里存在的缺口，
        不是缺口，是噪声。起跑前必须先确认树静下来了。

    ★ 为什么用「两次采样一致」而不是「mtime 距今多久」：
      mtime 是**代理量**（写者可能改完一个文件后想 5 分钟再改下一个）；
      两次采样一致量的是**这件事本身**（树还在不在变）。
      ★ 这是库内固定点口径（Δ=∅）用在**探针自己**身上。
    """
    def 采样() -> tuple[str, dict]:
        st = _git(主, "status", "--porcelain")
        行 = sorted(l for l in st.stdout.splitlines() if l.strip())
        head = _git(主, "rev-parse", "HEAD").stdout.strip()
        细: dict[str, tuple[int, int]] = {}
        for l in 行:
            rel = l[3:].strip().strip('"')
            try:
                s = (主 / rel).stat()
                细[rel] = (s.st_size, s.st_mtime_ns)
            except OSError:
                细[rel] = (-1, -1)
        原 = "\n".join(行) + f"\n@{head}\n" + "\n".join(
            f"{k}\t{v[0]}\t{v[1]}" for k, v in sorted(细.items()))
        return hashlib.sha1(原.encode("utf-8")).hexdigest(), 细

    t0 = time.time()
    前指, 前细 = 采样()
    次 = 1
    while time.time() - t0 < 最多等:
        time.sleep(间隔)
        指, 细 = 采样()
        次 += 1
        if 指 == 前指:
            return {"静": True, "秒": round(time.time() - t0, 1), "采样次数": 次,
                    "动过": []}
        变 = sorted(k for k in set(前细) | set(细)
                    if 前细.get(k) != 细.get(k))
        前指, 前细 = 指, 细
        最后动 = 变
    return {"静": False, "秒": round(time.time() - t0, 1), "采样次数": 次,
            "动过": 最后动[:10], "动过数": len(最后动)}


def 建(档案路径=None, 标签: str | None = None, 主树: Path | None = None,
       留运行期目录: bool = True) -> dict:
    """建隔离树。返回句柄（含临时档案路径、基线提交、核对基准）。"""
    档 = _pf.load_profile(档案路径) if 档案路径 else _pf.get_profile()
    主 = Path(主树 or 档.repo_root).resolve()
    标签 = 标签 or time.strftime("%m%d_%H%M%S")
    树 = 主.parent / f"{主.name}_orch_{标签}"
    分支 = f"orch/{标签}"
    if 树.exists():
        raise 隔离树错(f"隔离树已存在：{树}（换个标签，或先 `orch-run --clean`）")
    档源 = getattr(档, "source_path", None)
    # ★★★★★ R59（真跑抓出 · 2026-10-16）：**绝不回落到空壳档案**。
    #   【现象】真起跑时子进程打出：
    #       `[profile] 档案 `orch-isolated` 未声明任何判据门（gates）
    #        ⇒ 工程级门为空，不会用恒真占位门冒充。`
    #     即：**跑起来根本没有判据门**，而编排器的全部报表看着正常。
    #   【根因】字段名是 `Profile.source_path`，我写成了 `getattr(档, "path", None)`
    #     —— `getattr(..., 默认值)` 把**拼写错误吞掉了** ⇒ 回落成
    #     `name: orch-isolated\nrepo_root: .\n` 这个空壳档案。
    #   ★ 这正是本库反复出现的形态：**声明了保护却静默不生效**。
    #     "判据门唯一裁决"是这台机器的核心承诺；档案一回落，承诺就没了，
    #     而**没有任何一条报错**。⇒ 修法两条：
    #       ① 用对字段；② **拿不到源档案就报错**，不许回落。
    if not 档源 or not Path(档源).is_file():
        raise 隔离树错(
            f"拿不到源档案路径（Profile.source_path={档源!r}）⇒ **拒绝建树**。"
            "绝不回落到空壳档案：那会让跑起来没有判据门，而报表看着一切正常。")
    档源 = Path(档源)

    cp = _git(主, "worktree", "add", "-b", 分支, str(树), "HEAD")
    if cp.returncode != 0:
        raise 隔离树错(f"建 worktree 失败：{cp.stderr.strip() or cp.stdout.strip()}")

    同步 = _同步脏文件(主, 树)

    # ★ 临时档案必须**在基线提交之前**写好 —— 实测坑：写在提交之后，
    #   它自己会出现在 `diff 基线` 里 ⇒ 被当成"这次跑的交付物"⇒ 回灌清单
    #   里混进工具自己的档案文件（自检抓出：文件集多出 orchestrator.profile.yml）。
    #   ⇒ 它是**工具的账本**，不是交付物，必须落在基线之内。
    临档 = 树 / (档源.name if 档源 else "orchestrator.profile.yml")
    if 档源 and 档源.is_file():
        文 = 档源.read_text(encoding="utf-8")
    else:
        文 = "name: orch-isolated\nrepo_root: .\n"
    import re
    # ★★★★★ R60（R59 的齿当场抓出 · 2026-10-16）：
    #   【现象】`re.error: bad escape \o at position 23`，建树直接崩。
    #   【根因】`re.sub(pat, 替换串, 文)` 会把**替换串里的反斜杠当转义序列**，
    #     而 Windows 绝对路径（`E:\基础设施\…`）**必然带反斜杠**
    #     ⇒ 这个写法在真仓库上**必崩**（合成自检路径短，恰好没踩到）。
    #   ⇒ 修法：替换用**函数**，替换串不被解析。
    #   ★ 教训：**凡把路径塞进正则替换串的地方，都要防这一手。**
    if re.search(r"(?m)^audit_dir:", 文):
        文 = re.sub(r"(?m)^audit_dir:.*$",
                    lambda m: f"audit_dir: {档.audit_dir}", 文)
    else:
        文 = f"audit_dir: {档.audit_dir}\n" + 文
    临档.write_text(文, encoding="utf-8")

    # ★★★★★ R59 第二道：**写完必须核对判据门真的还在**。
    #   "声明了保护却静默不生效 = 没跑看起来像绿" —— 光写对字段不够，
    #   必须**证明**临时档案真的带着源档案的门。核不上就拒绝建树。
    try:
        临档档 = _pf.load_profile(临档)
        源门 = set(档.gates_by_name.keys())
        临门 = set(临档档.gates_by_name.keys())
    except Exception as exc:      # noqa: BLE001
        raise 隔离树错(f"临时档案读不回来：{type(exc).__name__}: {exc}") from exc
    if 源门 != 临门:
        raise 隔离树错(
            f"★ 临时档案的判据门与源档案不一致 ⇒ **拒绝建树**。"
            f"源={sorted(源门)} 临={sorted(临门)}"
            "（判据门一丢，跑起来就没有裁决者，而报表看着正常）")

    # 基线提交 ⇒ 之后 `diff 基线` 就是**这次跑的净改动**
    _git(树, "add", "-A")
    _git(树, "-c", "user.email=orch@local", "-c", "user.name=orch",
         "commit", "-m", "orch 基线（含主树未提交改动）", "--allow-empty", "--quiet")
    基线 = _git(树, "rev-parse", "HEAD").stdout.strip()

    return {"主树": str(主), "树": str(树), "分支": 分支, "基线": 基线,
            "临档": str(临档), "同步": 同步, "标签": 标签,
            "构建缓存": str(构建缓存(主))}


def 改动(句柄: dict) -> dict:
    """这次跑的**净改动**（相对基线）。

    ★ 实测坑：`git diff --name-status <基线>` **看不到未跟踪的新文件**
      （自检里隔离树新加的 `c.txt` 就漏了 ⇒ 新产物不进回灌清单 = 静默丢交付）。
      ⇒ 先 `git add -A` 再 `git diff --cached`。隔离树是本工具自己的，
        动它的 index 不越界；主树的 index 一个字节都不碰。
    """
    树 = Path(句柄["树"])
    cp = _git(树, "add", "-A")
    if cp.returncode != 0:
        raise 隔离树错(f"隔离树暂存失败：{cp.stderr.strip()}")
    cp = _git(树, "diff", "--cached", "--name-status", 句柄["基线"])
    if cp.returncode != 0:
        raise 隔离树错(f"读隔离树改动失败：{cp.stderr.strip()}")
    改 = 增 = 删 = 0
    文件: list[str] = []
    for 行 in cp.stdout.splitlines():
        if len(行) < 3:
            continue
        态, _, 路径 = 行.partition("\t")
        文件.append(路径)
        if 态.startswith("A"):
            增 += 1
        elif 态.startswith("D"):
            删 += 1
        else:
            改 += 1
    return {"改": 改, "增": 增, "删": 删, "文件": 文件}


def 回灌(句柄: dict, 只这些: list[str] | None = None) -> dict:
    """把隔离树的改动写回主树 —— **逐文件指纹核对，别人动过的让开不覆盖**。

    ★ 口径与 R54 完全一致：**让开，不覆盖，如实报**。
      不是"重试"（那还是覆盖），也不是"合并"（那是猜语义）。

    ★★★★★ R57（`unattended` 自检抓出 · 2026-10-16）：
      【现象】绿跑**改动没回灌**，隔离树成了**黑洞** —— 跑成功却什么都没交付。
      【根因】原核对基准用 `句柄["同步"]["前"]`，而它**只装了同步过的脏文件**。
        若被改的文件在开跑时是**干净的**（绝大多数情况！），`前` 里就没有它
        ⇒ 走到"主树里已有同名文件"那条分支 ⇒ 被判成"别人建的"⇒ **误拒**。
      【修法】基准改用**基线提交**（`git show <基线>:<rel>`）—— 它覆盖**全部**
        文件：干净文件的基线 = HEAD 版本，脏文件的基线 = 同步进去的那份，
        跑新加的文件基线 = 不存在。**这才是完整的基准。**
      ★ 教训：**基准必须与被判样本戊的全集一样宽**。
        只覆盖子集的基准，会把"我没见过"误判成"别人动过"。
    """
    主 = Path(句柄["主树"])
    树 = Path(句柄["树"])
    基线 = 句柄["基线"]
    净 = 改动(句柄)
    目标 = 只这些 if 只这些 is not None else 净["文件"]
    落 = 让 = 缺 = 0
    让开: list[str] = []
    落下: list[str] = []
    for rel in 目标:
        源 = 树 / rel
        标 = 主 / rel
        # 基线里这个文件长什么样？（None = 基线里没有 = 是这次跑新加的）
        基 = _git字节(树, "show", f"{基线}:{rel}")
        if 基 is None:
            # 跑新加的文件：主树若已冒出同名 ⇒ 别人建的 ⇒ 让开
            if 标.exists():
                让开.append(rel)
                让 += 1
                continue
        else:
            # 主树现在的字节必须**仍等于基线** ⇒ 期间没人动过
            try:
                现 = 标.read_bytes()
            except OSError:
                让开.append(rel)
                让 += 1
                continue
            if 现 != 基:
                让开.append(rel)
                让 += 1
                continue
        if 源.is_file():
            标.parent.mkdir(parents=True, exist_ok=True)
            标.write_bytes(源.read_bytes())
            落 += 1
            落下.append(rel)
        else:
            缺 += 1
    return {"落": 落, "让开": 让, "缺": 缺, "让开文件": 让开, "落下文件": 落下}


def _清只读(树: Path) -> int:
    """清掉树内所有只读位，返回清了几个。

    ★ 为什么需要（R556）：Windows 上 `shutil.rmtree` 遇到只读文件会失败，而原来的
      `ignore_errors=True` 把失败吞掉 ⇒ 目录还在却报「已删」。`git worktree remove --force`
      自己会清一部分，但兜底路径（元数据损坏时）走的是 rmtree，得自己清。
    """
    import os as _os
    import stat as _stat
    数 = 0
    try:
        项 = list(树.rglob("*"))
    except OSError:
        return 0
    for p in 项:
        try:
            m = p.stat().st_mode
            if not (m & _stat.S_IWRITE):
                _os.chmod(p, m | _stat.S_IWRITE)
                数 += 1
        except OSError:
            continue
    return 数


def _残留台账(主: Path) -> Path:
    """`<主仓>/.git/orch_残留.jsonl`：拆失败留下的目录清单（工具元数据，不进 git 工作区）。"""
    g = Path(主) / ".git"
    根 = g if g.is_dir() else Path(主)
    return 根 / "orch_残留.jsonl"


def _记残留(主: Path, 树: Path) -> None:
    """把"没删掉的目录"记下来 —— 否则登记/标记都被摘掉后，连自己人也认不出它。"""
    try:
        台 = _残留台账(主)
        行 = str(Path(树))
        旧 = 台.read_text(encoding="utf-8", errors="replace") if 台.exists() else ""
        if 行 in 旧.splitlines():
            return
        台.write_text(旧 + ("" if not 旧 or 旧.endswith("\n") else "\n") + 行 + "\n",
                      encoding="utf-8")
    except OSError:
        pass


def _是残留(主: Path, 树: Path) -> bool:
    try:
        台 = _残留台账(主)
        if not 台.exists():
            return False
        return str(Path(树)) in 台.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False


def _清残留记录(主: Path, 树: Path) -> None:
    try:
        台 = _残留台账(主)
        if not 台.exists():
            return
        行们 = [x for x in 台.read_text(encoding="utf-8", errors="replace").splitlines()
               if x and x != str(Path(树))]
        台.write_text("\n".join(行们) + ("\n" if 行们 else ""), encoding="utf-8")
    except OSError:
        pass


def 拆(句柄: dict, 保留: bool = False, 删分支: bool = True) -> dict:
    """拆隔离树。`保留=True` 只解绑不删目录（留证据用）。

    ★ R556：**删不掉必须说出来**。原实现 `rmtree(ignore_errors=True)` 吞掉失败，
      而 `main --clean` 照打「已删」并 return 0 ⇒ 假绿（实测：子进程还占用工作树时
      删目录=False、目录仍在，却报「已删」）。
      现在返回里带 `清只读` 与 `剩余`（剩余项数；-1 = 数不出来），由调用方判决。
    """
    主 = Path(句柄["主树"])
    树 = Path(句柄["树"])
    出: dict = {"删目录": False, "删分支": False, "清只读": 0, "剩余": 0}
    if not 保留:
        cp = _git(主, "worktree", "remove", "--force", str(树))
        if cp.returncode != 0:
            # 兜底：目录还在就手工删（worktree 元数据可能已损坏）
            出["清只读"] += _清只读(树)
            shutil.rmtree(树, ignore_errors=True)
            _git(主, "worktree", "prune")
        if 树.exists():                      # 再试一次：清了只读位再删
            出["清只读"] += _清只读(树)
            shutil.rmtree(树, ignore_errors=True)
        出["删目录"] = not 树.exists()
        if not 出["删目录"]:
            try:
                出["剩余"] = sum(1 for _ in 树.rglob("*"))
            except OSError:
                出["剩余"] = -1
    # ★ 2026-11-19 实测：`git worktree remove --force` 会**先摘登记再删文件**，
    #   删文件失败 ⇒ 登记没了、目录还在。若此时再把分支删掉，就**连重跑的匹配句柄也没了**
    #   （`--clean` 只会说"没找到"），残留变成只能手工删的垃圾。
    #   ⇒ 目录没删掉就保留分支（它还是一条线索），交给 `--clean` 按路径兜底重跑。
    if not 出["删目录"]:
        _记残留(主, 树)                     # ★ 见 _记残留 注释：标记会被摘掉，必须自己记
    if 删分支 and 出["删目录"]:
        cp = _git(主, "branch", "-D", 句柄["分支"])
        出["删分支"] = cp.returncode == 0
    return 出


def _删结果判决(树: Path, r: dict, 分支: str = "") -> tuple[int, str]:
    """把「删成功没」的判决抽成**函数**（口径 388：判据测函数、交付测入口）。

    返回 (退出码, 消息)。删不掉 ⇒ **3**，且消息里必须写明剩余项数与常见原因；
    不许返回 0 —— 那就是拿「我试过了」冒充「删掉了」。
    """
    分支尾巴 = ""
    if 分支 and not r.get("删分支"):
        分支尾巴 = f"（⚠ 分支 `{分支}` 也没删掉）"
    if r.get("删目录"):
        return 0, f"已删 {树}：{r}{分支尾巴}"
    return 3, (f"✗ 删不掉 {树}：目录**还在**（剩 {r.get('剩余')} 项；已清只读位 "
               f"{r.get('清只读')} 个）{分支尾巴}。"
               "常见原因：仍有进程以它为工作目录（Windows 会锁住），或权限不足。"
               "⇒ **不算删成功**，退出码 3；请先停掉占用进程再重跑 `orch-tree --clean`。")


def 列(主树: Path | None = None) -> list[dict]:
    """列出现存隔离树（供 --list / --clean 用）。

    ★ 调用方给了主树就**不再读档案**：档案缺失/损坏时 `--clean` 本可以正常工作，
      不该因为一个用不到的配置而抛（实测：给不了 ORCH_PROFILE 的环境里 `列(主)` 会连带失败）。
    """
    主 = Path(主树 or _pf.get_profile().repo_root).resolve()
    cp = _git(主, "worktree", "list", "--porcelain")
    出: list[dict] = []
    当: dict = {}
    for 行 in cp.stdout.splitlines():
        if 行.startswith("worktree "):
            if 当:
                出.append(当)
            当 = {"树": 行.split(" ", 1)[1]}
        elif 行.startswith("branch "):
            当["分支"] = 行.split(" ", 1)[1].replace("refs/heads/", "")
        elif 行.startswith("HEAD "):
            当["HEAD"] = 行.split(" ", 1)[1][:8]
    if 当:
        出.append(当)
    return 出


# ════════════════════════════════════════════════════════════════════
# 自检
# ════════════════════════════════════════════════════════════════════
def _自检() -> int:
    """在**临时真 git 仓库**上跑一遍建/改/回灌/拆。不碰任何真项目、不联网。"""
    import tempfile
    fails: list[str] = []
    N = 0

    def 断言(条件: bool, 说明: str) -> None:
        nonlocal N
        N += 1
        if not 条件:
            fails.append(说明)

    根 = Path(tempfile.mkdtemp(prefix="orch_wt_"))
    仓 = 根 / "repo"
    仓.mkdir()
    try:
        _git(仓, "init", "-q")
        (仓 / "a.txt").write_bytes(b"A\n")
        (仓 / "b.txt").write_bytes(b"B\n")
        _git(仓, "add", "-A")
        _git(仓, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "init")
        # 主树留一个未提交改动 ⇒ 隔离树必须看到它
        (仓 / "a.txt").write_bytes(b"A-dirty\n")

        档 = 仓 / "orch.yml"
        # ★ R59 齿的夹具：源档案**必须带判据门**，否则"临时档案丢了门"这条
        #   根本测不出来（没有门可丢 ⇒ 齿空转）。夹具不自洽 = 齿是假的。
        档.write_text(
            "name: t\nrepo_root: .\naudit_dir: .audit\n"
            "gates:\n"
            "  - name: g1\n    type: grep_required\n    pattern: A\n"
            "  - name: g2\n    type: grep_forbidden\n    pattern: ZZ\n",
            encoding="utf-8")
        源档带门 = bool(_pf.load_profile(档).gates_by_name)
        句 = 建(档案路径=档, 标签="selftest", 主树=仓)
        树 = Path(句["树"])
        断言(树.is_dir(), "建：隔离树目录没建出来")
        断言(句.get("构建缓存") and Path(句["构建缓存"]).is_dir(),
             "★ 建：缺构建缓存目录（隔离会冷编译 ⇒ 门 timeout 变假红）")
        断言(Path(句["构建缓存"]).parent == 仓.parent,
             "建：构建缓存不该放在仓库里（会被当成产物或被删）")
        断言((树 / "a.txt").read_bytes() == b"A-dirty\n",
             "建：主树未提交改动**没同步**过去（跑的人看到的是假状态）")
        断言(句["同步"]["搬"] >= 1, "建：同步计数不对")
        断言(Path(句["临档"]).is_file(), "建：临时档案没生成")
        # ── ★ R59 齿：临时档案必须**带着源档案的判据门** ──
        #   旧写法 `getattr(档, "path", None)` 把字段名拼错吞掉了 ⇒ 回落成空壳档案
        #   ⇒ 跑起来**没有判据门**（"判据门唯一裁决"这条承诺静默失效），
        #   而所有报表看着正常。★ 真跑才抓出来；合成自检必须把它锁住。
        断言(源档带门, "R59 齿：夹具没给源档案配判据门（齿空转 ⇒ 无齿）")
        临 = _pf.load_profile(Path(句["临档"]))
        源 = _pf.load_profile(档)
        断言(set(临.gates_by_name) == set(源.gates_by_name),
             "★★ R59：临时档案的判据门与源档案不一致"
             "（跑起来没有裁决者，而报表看着正常）")
        断言(str(源.audit_dir) in Path(句["临档"]).read_text(encoding="utf-8"),
             "★ 临时档案的 audit_dir 没指向主树绝对路径"
             "（回执会随隔离树一起被删掉 ⇒ 跑完查不到）")
        断言("audit_dir: " in Path(句["临档"]).read_text(encoding="utf-8"),
             "建：临时档案缺 audit_dir 行")

        # 主树自始至终不该被写
        断言((仓 / "a.txt").read_bytes() == b"A-dirty\n",
             "★ 建：主树被写了（隔离失效）")

        # 在隔离树里"跑"（改一个、加一个）
        (树 / "a.txt").write_bytes(b"A-fixed\n")
        (树 / "c.txt").write_bytes(b"C-new\n")
        净 = 改动(句)
        断言(净["改"] == 1 and 净["增"] == 1, f"改动：计数不对 {净}")
        断言(set(净["文件"]) == {"a.txt", "c.txt"}, f"改动：文件集不对 {净['文件']}")

        # 回灌：a.txt 主树没人动 ⇒ 应落；先把 c.txt 在主树造一个 ⇒ 应让开
        #   ★ 用 encode()：`b"中文"` 是 SyntaxError（本文件已踩过一次）
        别人C = "C-别人的\n".encode("utf-8")
        (仓 / "c.txt").write_bytes(别人C)
        灌 = 回灌(句)
        断言(灌["落"] == 1 and 灌["让开"] == 1, f"回灌：计数不对 {灌}")
        断言((仓 / "a.txt").read_bytes() == b"A-fixed\n", "回灌：该落的没落")
        断言((仓 / "c.txt").read_bytes() == 别人C,
             "★★ 回灌：**覆盖了别人的文件**（R54 口径被破坏）")
        断言("c.txt" in 灌["让开文件"], "回灌：让开清单没点名")

        # 反向：主树 a.txt 被别人改过 ⇒ 必须让开
        别人A = "A-又被别人改了\n".encode("utf-8")
        (仓 / "a.txt").write_bytes(别人A)
        (树 / "a.txt").write_bytes(b"A-fixed2\n")
        灌2 = 回灌(句, 只这些=["a.txt"])
        断言(灌2["让开"] == 1, f"★ 回灌：别人改过的**没让开** {灌2}")
        断言((仓 / "a.txt").read_bytes() == 别人A,
             "★★ 回灌：覆盖了别人后改的内容")

        # ── ★ R56 齿：注入「写完源文件立刻被外部改」的竞态 ──
        #   旧写法「读源 → 写标 → **重读源**算指纹」在活写者下会记错基准；
        #   合成自检没有并发写者 ⇒ 必须**把竞态注入进去**才测得到。
        #   注入点：在写隔离树那一刻，同时改掉主树的源文件。
        脏 = "脏的\n".encode("utf-8")
        外 = "外部插进来的\n".encode("utf-8")
        (仓 / "r56.txt").write_bytes(b"orig\n")
        _git(仓, "add", "-A")
        _git(仓, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "r56")
        (仓 / "r56.txt").write_bytes(脏)

        真写 = Path.write_bytes
        捣过: list[int] = []

        def 写时捣乱(self, 数据):
            r = 真写(self, 数据)
            if self.name == "r56.txt" and self.parent != 仓 and not 捣过:
                捣过.append(1)
                真写(仓 / "r56.txt", 外)      # 模拟外部写者正好插在这一刻
            return r

        Path.write_bytes = 写时捣乱
        try:
            句56 = 建(档案路径=档, 标签="r56", 主树=仓)
        finally:
            Path.write_bytes = 真写
        try:
            树56 = Path(句56["树"])
            记 = 句56["同步"]["前"].get("r56.txt")
            实 = hashlib.sha1((树56 / "r56.txt").read_bytes()).hexdigest()
            断言(捣过, "R56 齿：竞态没被注入（齿空转 ⇒ 无齿）")
            断言((树56 / "r56.txt").read_bytes() == 脏,
                 "R56：隔离树里不是我们读到的那些字节")
            断言(记 == 实,
                 "★★ R56：记下的指纹**不是写下去的字节**"
                 "（回灌基准错 ⇒ 会误拒或误放覆盖）")
        finally:
            拆(句56)

        拆(句)
        断言(not 树.exists(), "拆：目录还在")
        断言(all(w["树"] != str(树) for w in 列(仓)),
             "拆：worktree 登记没清掉")
    finally:
        shutil.rmtree(根, ignore_errors=True)

    if fails:
        print(f"workspace 自检：{N - len(fails)}/{N} —— 红 {len(fails)}")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print(f"workspace 自检：{N}/{N} 全绿")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="隔离工作树（无人值守跑在 worktree 里）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true", help="列出现存隔离树")
    ap.add_argument("--clean", metavar="标签或路径",
                    help="删掉某个隔离树（含分支）")
    ap.add_argument("--profile")
    a = ap.parse_args(argv)
    if a.selftest:
        return _自检()
    if a.list:
        for w in 列():
            print(json.dumps(w, ensure_ascii=False))
        return 0
    if a.clean:
        档 = _pf.load_profile(a.profile) if a.profile else _pf.get_profile()
        主 = Path(档.repo_root).resolve()
        命 = Path(a.clean)
        for w in 列(主):
            树 = Path(w["树"])
            # ★ 匹配要认**标签**：树名是 `<仓库名>_orch_<标签>`，而用户敲的是 `<标签>`。
            #   实测：`--clean warm` 原状匹配不上（树名是 `示例工程_orch_warm`）⇒ 静默没删。
            命中 = (树.name == a.clean or str(树) == str(命)
                    or 树.name.endswith(f"_orch_{a.clean}")
                    or w.get("分支") == f"orch/{a.clean}")
            if 命中:
                r = 拆({"主树": str(主), "树": str(树),
                        "分支": w.get("分支", "")}, 删分支=bool(w.get("分支")))
                码, 话 = _删结果判决(树, r, w.get("分支", ""))
                print(话)
                return 码
        # ★ 2026-11-19 实测兜底：占用时 `worktree remove --force` 摘掉了登记，
        #   只剩目录留在盘上（见 `拆()` 里那段注释）⇒ 光靠 `列()` 永远找不到它。
        #   只要 `--clean` 指的是**盘上还在的路径**，就按「残留」删，判决口径不变（失败仍 3）。
        if 命.exists() and 命.is_dir():
            # ★★ 安全护栏（2026-11-19 自查）：这条兜底会 **rmtree 用户给的路径**，
            #   不加限制的话 `orch-tree --clean C:\\Windows` 会把系统目录删掉。
            #   只认"看起来确实是隔离树"的目录：① 名字是 `<仓库名>_orch_<标签>`（本工具自己的命名）
            #   ② 或带 worktree 标记（`.git` 是**文件**且指向主仓 worktrees 目录）。
            #   另外一律拒绝：主仓本身、主仓内部的任何目录（那是在删源码）。
            _名像 = 命.name.startswith(主.name + "_orch_")
            _标记 = (命 / ".git").is_file()
            _记过 = _是残留(主, 命)         # ★ 台账：只有**我们自己**没删掉的目录才算
            if 主.resolve() == 命 or 主 in 命.resolve().parents:
                print(f"✗ 拒绝：`{命}` 在主仓 `{主}` 里面（这是源码，不是隔离树）")
                return 1
            if not (_名像 or _标记 or _记过):
                print(f"✗ 拒绝：`{命}` 既不是 `<仓库名>_orch_*` 命名、也没有 worktree 标记"
                      " —— 不按路径删（防误删）。若确要删，请手工确认后处理。")
                return 1
            出 = {"删目录": False, "删分支": False, "清只读": _清只读(命), "剩余": 0}
            shutil.rmtree(命, ignore_errors=True)
            出["删目录"] = not 命.exists()
            if not 出["删目录"]:
                try:
                    出["剩余"] = sum(1 for _ in 命.rglob("*"))
                except OSError:
                    出["剩余"] = -1
            _git(主, "worktree", "prune")
            码, 话 = _删结果判决(命, 出)
            if 出["删目录"]:
                _清残留记录(主, 命)
            print("（不在 git 登记里，按**路径残留**处理）" + 话)
            return 码
        print(f"没找到隔离树：{a.clean}")
        return 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
