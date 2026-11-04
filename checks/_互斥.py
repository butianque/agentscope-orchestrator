# -*- coding: utf-8 -*-
"""★ 互斥：独占「改某个源文件」的权利（R65）。

═══════════════════════════════════════════════════════════════════════
为什么要有这个模块（不是"再加一个锁"，是修一个**全称**缺陷）
═══════════════════════════════════════════════════════════════════════
【现象·实测】同时跑两份 `全量回归`：两份各 **7 红**；单跑 **0 红**。
  红的探针集合每次还不一样（典型的竞态）。
【根因】有 **10 个**探针的做法都是「读源 → 改源 → 跑子进程 → 还原」，
  而它们改的是**同一个文件**（`generic_goal.py`）。
  原来的互斥是**每个探针自己写一个 `<探针名>.lock`**
  ⇒ 每个探针锁的是**自己**，谁也没锁住**那个文件**。
【为什么这是全称缺陷】R55 当时只给 `规划层齿探针` 加了锁 ——
  那是**枚举式修法**：来一个新探针就得记得再加一把，必然漏。
  真正要锁的不是"我"，是**被改的那个资源**。
⇒ 本模块把锁**按资源**建：谁改同一个文件，谁就撞同一把锁。
  **新增探针不用记得加锁**（只要用本模块的入口），因为锁的键是文件，不是探针。

═══════════════════════════════════════════════════════════════════════
三条设计口径
═══════════════════════════════════════════════════════════════════════
① **等待，而不是抢占**：并发是**正常的**（两份回归同时跑很合理），
   所以默认等（可配超时），而不是第二个直接拒绝。
   ★ 但**超时必须抛**，绝不静默放行 —— 「声明了保护却静默不生效 = 没跑看起来像绿」。
② **锁的粒度 = 被改的文件**：改 `a.py` 与改 `b.py` 互不阻塞
   （R52 的教训：**同一性必须和被测对象一样细**）。
③ **陈旧锁必须能自愈**：持有者被强杀（`taskkill`/崩溃）时锁文件会留下。
   若不管，一次意外就**永久卡死所有后续运行**。
   ⇒ 锁里记 PID；发现持有者已死 ⇒ 接管（并如实说明）。
"""
from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import os
import time
from pathlib import Path

_锁目录默认 = Path(__file__).resolve().parent / ".locks"
# ★ 可经环境变量覆盖：自检要能在临时目录里跑（否则会污染真实锁目录），
#   而**跨进程**那条齿必须让子进程用**同一个**锁目录才测得出真互斥。
#   （第一版自检就踩了：父进程 patch 了临时目录、子进程读真实目录 ⇒
#     两边锁的不是同一个文件 ⇒ 齿**假绿**在别处、红在这一条。）
_锁目录 = Path(os.environ.get("ORCH_LOCK_DIR") or _锁目录默认)


class 占用错(RuntimeError):
    """在超时内没能拿到锁。★ **必须抛**，不许静默继续。"""


def _锁路径(资源) -> Path:
    p = Path(资源).resolve()
    # 大小写不敏感（Windows）：同一个文件的不同写法必须撞同一把锁
    h = hashlib.sha1(str(p).lower().encode("utf-8")).hexdigest()[:16]
    return _锁目录 / f"{p.stem}_{h}.lock"


def _活(pid: int) -> bool:
    """该 PID 还活着吗？拿不准一律当**活着**（保守：宁可等，不可抢）。"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=20).stdout or ""
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:      # noqa: BLE001
        return True


class 锁:
    def __init__(self, 资源, 说明: str = ""):
        self.资源 = Path(资源).resolve()
        self.路径 = _锁路径(资源)
        self.说明 = 说明 or self.资源.name
        self._持有 = False

    # ── 获取 ──────────────────────────────────────────────────────
    def 取(self, 超时: float = 1800.0, 间隔: float = 0.25) -> "锁":
        self.路径.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        让过陈旧 = 0
        while True:
            try:
                # ★ `O_CREAT|O_EXCL` 的创建是**原子**的 ⇒ 没有竞态窗口
                fd = os.open(str(self.路径), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "资源": str(self.资源),
                               "说明": self.说明, "ts": time.time()}, f)
                self._持有 = True
                atexit.register(self.放)
                return self
            except FileExistsError:
                # ── 陈旧锁自愈：持有者已死 ⇒ 接管 ──
                try:
                    信 = json.loads(self.路径.read_text(encoding="utf-8"))
                except Exception:      # noqa: BLE001
                    信 = {}
                pid = int(信.get("pid") or 0)
                if not _活(pid):
                    让过陈旧 += 1
                    try:
                        self.路径.unlink()
                        print(f"[互斥] 接管陈旧锁（持有者 pid={pid} 已不在）："
                              f"{self.路径.name}")
                    except OSError:
                        pass
                    continue
                if time.time() - t0 >= 超时:
                    raise 占用错(
                        f"等 {超时:.0f} 秒仍拿不到「{self.资源.name}」的独占权"
                        f"（持有者 pid={pid}，说明={信.get('说明')!r}）。"
                        f"★ 不是静默放行：两个进程同时改同一个源文件会互相"
                        f"还原对方的改动（R55/R65 实测过）。"
                        f"如确认无人在跑，删掉 {self.路径} 重试。")
                time.sleep(间隔)

    # ── 释放 ──────────────────────────────────────────────────────
    def 放(self) -> None:
        if not self._持有:
            return
        self._持有 = False
        try:
            # ★ 只删**自己的**锁：若已被接管，删别人的锁会把互斥弄坏
            信 = json.loads(self.路径.read_text(encoding="utf-8"))
            if int(信.get("pid") or 0) != os.getpid():
                return
        except Exception:      # noqa: BLE001
            pass
        try:
            self.路径.unlink()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.放()
        return False


@contextlib.contextmanager
def 独占(资源, 超时: float = 1800.0, 说明: str = ""):
    """独占某资源；退出即释放（异常也释放）。"""
    锁_ = 锁(资源, 说明).取(超时=超时)
    try:
        yield 锁_
    finally:
        锁_.放()


@contextlib.contextmanager
def 改文件(路径, 超时: float = 1800.0, 说明: str = ""):
    """★ 探针的标准入口：**独占 → 交出原文 → 退出时字节级还原 → 释放**。

    用法（探针里三行）：
        from _互斥 import 改文件
        with 改文件(F) as (原文, 源):
            ...改 F / 跑子进程 / 比对...

    ★ 为什么把"还原"也收进来：原来每个探针各写一遍 `F.write_bytes(原文)`，
      而**漏写一处**就会把补丁永久留在源码里（R55 真的发生过：`if False:`
      被永久留在 `generic_goal.py` 第 613 行）。
      收口一处 ⇒ 不可能漏。
    """
    锁_ = 锁(路径, 说明 or f"改 {Path(路径).name}").取(超时=超时)
    原文 = b""
    try:
        原文 = Path(路径).read_bytes()
        yield 原文, 原文.decode("utf-8", errors="replace")
    finally:
        try:
            if 原文:
                # ★ R113：**原子写**。原来这里是 `Path(路径).write_bytes(原文)`，
                #   强杀会留下全零的源码（真事故，见 `原子写` 的长注释）。
                原子写(路径, 原文)                # ★ 字节级：不碰行尾
        finally:
            锁_.放()


def 原子写(路径, 数据: bytes) -> None:
    """**产品源码只能经这里落盘** —— 写临时文件 → fsync → 原子替换（R113）。

    ★★★★★ 为什么必须有它（2026-10-20 03:52 的真实事故 · 我自己造成的）：
      我强杀了一个正在跑的 `规划层齿探针.py`，它正处在
          `目标文件.write_bytes(原字节)`        ← 还原那一步
      这一句**不是原子的**。进程在"文件已按新长度分配、数据还没落盘"时被杀
      ⇒ NTFS 留下一个**长度正确、内容全是 0x00** 的 `generic_goal.py`
      （785,173 字节的纯零）。

      **产品源码当场没了。** 我能救回来，只因为那个探针在开跑时把清洁字节
      写进了侧车 `规划层齿探针.clean`（R74c 的设计）——
      **纯属侥幸**：换一个探针、换一个时刻，就是不可恢复的损失。

    ★ 这不是"探针的错"，是**所有写源码的地方**共同的错：
      `write_bytes` 的语义是"打开-截断-写"，中间任何时刻被杀都会留下
      半截或全零的文件。**"截断"和"写入"之间有一段时间窗，文件是坏的。**
      ⇒ 正确的落盘只有一种：**写到同目录的临时文件 → fsync → `os.replace`**。
        `os.replace` 在同卷上是原子的：要么是旧字节，要么是新字节，
        **不存在"中间态"**。被杀时最坏情况是留下一个临时文件（可清理），
        而**目标文件永远完好**。

    ★ 为什么放在 `_互斥` 而不是各个探针里：
      "能不枚举就别枚举" —— 11 个探针各写一遍，就必然有一处漏（R55 的教训：
      `if False:` 曾被永久留在源码里）。**一个写口，一条规矩。**

    ★ 齿（`_自检` 里）：`checks/` 下除本文件外，**不得出现任何
      `X.write_bytes(...)` 调用**（AST 判，不受注释/字符串干扰）。
      这条规矩是可机检的、无例外的。
    """
    路径 = Path(路径)
    父 = 路径.parent
    父.mkdir(parents=True, exist_ok=True)
    # 临时文件必须与目标**同目录同卷**，否则 `os.replace` 会退化成跨卷拷贝
    # （那就又不是原子的了）。
    临 = 父 / f".{路径.name}.原子写.{os.getpid()}.tmp"
    try:
        with open(临, "wb") as fh:
            fh.write(数据)
            fh.flush()
            os.fsync(fh.fileno())        # ★ 数据真到盘上，再替换
        os.replace(临, 路径)             # ★ 同卷原子替换
    finally:
        # 被杀时可能留下临时文件；正常路径下它已经被 replace 掉
        try:
            临.unlink(missing_ok=True)
        except OSError:
            pass


def 原文守卫(路径, 原字节: bytes, 谁: str = "探针") -> str | None:
    """★ R74：**写回/注入别人的文件之前，先确认它还是我读的那个。**

    返回 `None` = 一致（可以安全注入或还原）；否则返回**冲突描述**（调用方
    必须**拒绝写回**并如实报出，不许静默覆盖）。

    ★★★★★ 真事故（2026-10-17 01:04）：
      我在编辑 `src/agentscope_orchestrator/generic_goal.py`（加 R73 基线口径），
      **同时** `checks/规划层齿探针.py` 在跑 —— 它**导入时**读原文、
      `finally` **无条件**写回 `原字节`。我的改动写在两者之间
      ⇒ **被写回成它读到的旧版本** ⇒ 我的 R73 改动**凭空消失**（0 处）。
      ★ 我是靠"发现刚写的函数不见了"才察觉的 —— 这正是最危险的那种：
        **没有报错、没有红、只是安静地把你的工作擦掉。**

    ★ 这是 **R65b 的原样复发**：R65b 修的是"**基线本身**不干净"
      （读到一份已经被污染的原文），而**没有修**
      "**读过之后、写回之前**，别人改了"这个窗口。

    ★ 锁**挡不住**它：锁只对**同样用锁的进程**有效，而人是直接用编辑器的
      （我用的编辑工具就不加锁）。⇒ 唯一可靠的办法是**乐观并发**：
      **写回前校验内容**。

    ★ 用法（探针三行）：
        if (冲突 := 原文守卫(F, 原字节, "规划层齿探针")):
            失败.append(冲突); 跳过注入
    """
    try:
        当前 = Path(路径).read_bytes()
    except OSError as e:                          # noqa: BLE001
        return f"★ {谁}：读不到 {路径}（{type(e).__name__}: {e}）⇒ 拒绝写回"
    if 当前 == 原字节:
        return None
    return (f"★ {谁}：{Path(路径).name} 在**读过之后被改过**"
            f"（我读 {len(原字节)} 字节 / 现在 {len(当前)} 字节）"
            f"⇒ **拒绝写回**（否则会抹掉别人的改动）")


def 占住(资源, 超时: float = 1800.0, 说明: str = "") -> 锁:
    """**探针最省事的入口**：拿到锁就返回，退出时由 `atexit` 自动释放。

    ★ 为什么不要求探针写成 `with`：现有探针的结构是
      「模块级读原文 → 改 → 跑子进程 → `finally` 还原 → `sys.exit(...)`」，
      套 `with` 要重排整个缩进（10 个文件、易错）。
      而 `atexit` 覆盖正常退出 / 异常退出 / `sys.exit` 三条路，
      与它们现有的结构**正交**，插入三行即可。
    ★ 代价：锁的持有期是**整个探针**（比 `改文件` 的临界区粗）。
      这是**故意的保守**：探针之间本来就不该并发改同一个文件。
    """
    return 锁(资源, 说明).取(超时=超时)


# ════════════════════════════════════════════════════════════════
# 纯机器自检（不碰网络、不改任何源码）
# ════════════════════════════════════════════════════════════════
def _自检() -> int:
    import re
    import subprocess
    import sys
    import tempfile

    fails: list[str] = []
    N = 0

    def 断言(c, s):
        nonlocal N
        N += 1
        if not c:
            fails.append(s)

    根 = Path(tempfile.mkdtemp(prefix="orch_lock_"))
    旧锁目录 = os.environ.get("ORCH_LOCK_DIR")
    # ★ 让**子进程**也用同一个锁目录（跨进程那条齿才测得准）
    os.environ["ORCH_LOCK_DIR"] = str(根 / ".locks")
    globals()["_锁目录"] = 根 / ".locks"
    try:
        甲 = 根 / "a.py"
        乙 = 根 / "b.py"
        甲.write_bytes(b"A\n")
        乙.write_bytes(b"B\n")

        # ① 拿到锁后，**同一个资源**在超时内拿不到 ⇒ 必须抛（不许静默放行）
        锁1 = 锁(甲).取(超时=5)
        抛了 = False
        try:
            锁(甲).取(超时=0.6, 间隔=0.1)
        except 占用错:
            抛了 = True
        断言(抛了, "① 同资源二次获取必须抛 占用错（静默放行 = 没跑像绿）")

        # ② 粒度 = 文件：改另一个文件**不该被阻塞**（R52：同一性要够细）
        try:
            锁2 = 锁(乙).取(超时=1.0)
            断言(True, "② 不同资源不该互相阻塞")
            锁2.放()
        except 占用错:
            断言(False, "② 不同资源被误阻塞（锁粒度太粗）")

        # ③ 释放后必须能再拿到
        锁1.放()
        try:
            锁3 = 锁(甲).取(超时=1.0)
            断言(True, "③ 释放后应能再获取")
            锁3.放()
        except 占用错:
            断言(False, "③ 释放后仍拿不到（锁没真释放）")

        # ④ ★ 跨进程互斥（同进程内的互斥证明不了真原子性）
        #   ★★ 第二版自检在这里也踩过：用 `subprocess.run` ⇒ 它**等子进程结束**
        #     才返回，那时子进程已释放锁 ⇒ 父进程当然拿得到 ⇒ **齿空转**。
        #     必须 `Popen` + 读到 HOLD 再测（否则测的不是"持锁期间"）。
        丙 = 根 / "c.py"
        丙.write_bytes(b"C\n")
        子 = subprocess.Popen(
            [sys.executable, "-c",
             "import sys,time;"
             f"sys.path.insert(0,{str(Path(__file__).resolve().parent)!r});"
             "from _互斥 import 锁;"
             f"l=锁({str(丙)!r}).取(超时=10);"
             "print('HOLD');sys.stdout.flush();time.sleep(8)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        try:
            行 = (子.stdout.readline() or "").strip()
            断言("HOLD" in 行, f"④ 子进程应成功持锁（实得 {行!r}）")
            抛4 = False
            try:
                锁(丙).取(超时=0.8, 间隔=0.1)
            except 占用错:
                抛4 = True
            断言(抛4, "④ 子进程持锁期间父进程必须拿不到（跨进程互斥）")
        finally:
            子.terminate()
            try:
                子.wait(timeout=20)
            except Exception:      # noqa: BLE001
                子.kill()

        # ⑤ ★ 陈旧锁自愈：持有者已死 ⇒ 必须能接管
        #   否则一次强杀就**永久卡死所有后续运行**。
        丁 = 根 / "d.py"
        丁.write_bytes(b"D\n")
        p丁 = _锁路径(丁)
        p丁.parent.mkdir(parents=True, exist_ok=True)
        # 找一个**肯定不存在**的 pid（用一个大数）
        p丁.write_text(json.dumps({"pid": 999999, "说明": "死进程"}), encoding="utf-8")
        接 = False
        try:
            锁4 = 锁(丁).取(超时=2.0, 间隔=0.1)
            接 = True
            锁4.放()
        except 占用错:
            接 = False
        断言(接, "⑤ 陈旧锁（持有者已死）必须能自愈接管"
                 "（否则一次强杀永久卡死）")

        # ⑥ ★ `改文件` 必须**字节级还原**（含 CRLF 与非法字节）
        戊 = 根 / "e.py"
        原 = "行一\r\n行二\r\n".encode("utf-8") + b"\xff\xfe"
        戊.write_bytes(原)
        with 改文件(戊) as (文, _):
            断言(文 == 原, "⑥ 改文件：交出的必须是原文**字节**")
            戊.write_bytes("改过了\n".encode("utf-8"))
        断言(戊.read_bytes() == 原, "⑥ 改文件：退出必须**字节级还原**")
        # 异常路径也要还原
        try:
            with 改文件(戊) as (_, _2):
                戊.write_bytes("又改了\n".encode("utf-8"))
                raise RuntimeError("模拟探针中途炸")
        except RuntimeError:
            pass
        断言(戊.read_bytes() == 原, "⑥ 改文件：**异常路径**也必须还原"
                                    "（R55：补丁被永久留在源码里）")
        # 还原之后锁必须已释放
        try:
            锁5 = 锁(戊).取(超时=1.0)
            断言(True, "⑥ 改文件退出后锁应已释放")
            锁5.放()
        except 占用错:
            断言(False, "⑥ 改文件退出后锁没释放（会卡死后续运行）")

        # ════════════════════════════════════════════════════════════
        # ⑦ ★★★★★ R65c（2026-10-16 真事故）：**"声明了保护"不等于"保护在"**
        #   整轮回归的独占保护**曾经被 R66 的 stash 事故连带回退掉**，
        #   而我**以为它还在** —— 直到并发验证打出退出码 (1,1) 才发现。
        #   ⇒ 保护本身也必须有齿：**机器读源码**，断言独占代码在。
        #   ★ 这条齿的形状与 R68 一致：**读自己的源码、断言关键构造存在**。
        #     它防的不是"逻辑写错"，而是"**保护被静默抹掉**"。
        # ════════════════════════════════════════════════════════════
        _回归 = Path(__file__).resolve().parent / "全量回归.py"
        if not _回归.is_file():
            断言(False, f"⑦ 找不到全量回归.py：{_回归}")
        else:
            _源 = _回归.read_text(encoding="utf-8", errors="replace")
            断言("from _互斥 import" in _源,
                 "⑦ 全量回归.py 里**没有**引入 _互斥 ⇒ 整轮独占保护被抹掉了"
                 "（真事故：R66 回退后我以为它还在）")
            断言("占住(" in _源,
                 "⑦ 全量回归.py 里**没有**取锁 ⇒ 两份回归会静默并行")
            断言("return 2" in _源,
                 "⑦ 全量回归.py 里**没有**「拒绝启动」的退出码 2"
                 " ⇒ 第二份会静默并行（而不是明确拒绝）")
            # ★★ 超时必须**短**：`_占住` 的语义是"等到拿到锁"，
            #   给大超时 ⇒ 第二份**排队等**而不是拒绝 ⇒ 变成"静默串行"。
            #   实测踩过：写 7200 时两份都跑了，验证打出 (0,0) 而非 (0,2)。
            #   ★ 正则不能写 `[^)]*?`：`占住(Path(__file__).resolve(), 超时=…)`
            #     的参数里**本来就有括号** ⇒ 匹配不到 ⇒ 假红（我刚踩过）。
            _m = re.search(r"占住\(.{0,300}?超时\s*=\s*([0-9.]+)", _源, re.S)
            断言(_m is not None and float(_m.group(1)) <= 30.0,
                 f"⑦ 全量回归.py 的整轮独占超时太大（{_m.group(1) if _m else '找不到'}）"
                 f" ⇒ 第二份会**排队等**而不是**拒绝启动**（实测踩过）")

        # ⑧ 按**资源**加锁必须已经接进那些会改源码的探针里
        #    ★ 同 R65c：这也是"接了一次就被抹掉"的地方（`接锁.py` 是机器接的，
        #      但文件被回退后接线就没了）。只查**存在性**，不查具体探针名单
        #      （名单会变，硬编码名单就是枚举 ⇒ 来一个新探针就漏）。
        _检查目录 = Path(__file__).resolve().parent
        _接了 = []
        for _p in sorted(_检查目录.glob("*.py")):
            if _p.name in ("_互斥.py", "接锁.py", "_验并发互斥.py"):
                continue
            _t = _p.read_text(encoding="utf-8", errors="replace")
            if "read_bytes()" in _t and "from _互斥 import" not in _t:
                # 会读源码字节、却**没**接锁 ⇒ 可能改源码
                if ".write_bytes(" in _t:
                    _接了.append(_p.name)
        断言(not _接了,
             f"⑧ 这些探针会**改源码却没接锁**（并发时互相抹补丁）：{_接了}")

        # ════════════════════════════════════════════════════════════
        # ⑨ ★★★★★ R74（2026-10-17 真事故）：**锁挡不住不加锁的写者。**
        #   事故：我在编辑 `generic_goal.py`（加 R73），**同时**
        #   `规划层齿探针.py` 在跑 —— 它**导入时**读原文、`finally`
        #   **无条件**写回 `原字节` ⇒ 我的改动被写回成旧版本 ⇒ **凭空消失**。
        #   没有报错、没有红，只是**安静地把工作擦掉**。
        #   ★ 这是 R65b 的原样复发：R65b 修的是"**基线**不干净"，
        #     没修"**读过之后、写回之前**别人改了"这个窗口。
        #   ⇒ 唯一可靠办法是**乐观并发**：写回前校验内容。
        # ════════════════════════════════════════════════════════════
        己 = 根 / "f.py"
        原己 = b"original\n"
        己.write_bytes(原己)
        断言(原文守卫(己, 原己) is None,
             "⑨ 原文守卫：内容没变时必须放行（否则探针永远注入不了）")
        己.write_bytes(b"someone else edited this\n")
        冲突 = 原文守卫(己, 原己)
        断言(冲突 is not None,
             "⑨ 原文守卫：内容被改过时必须**报冲突**"
             "（不报 = 又会安静地抹掉别人的改动）")
        断言("拒绝写回" in (冲突 or ""),
             f"⑨ 冲突描述必须点明后果（实得 {冲突!r}）")
        # ★ 反向齿：**必须真的挡住写回**，不能只是"报一声然后照写"。
        #   形状 = "注入一个真实的并发写者，看守卫是否拦住"。
        己.write_bytes(原己)
        with 改文件(己) as (文, _):
            己.write_bytes(b"probe patch\n")      # 模拟探针注入
            己.write_bytes(b"human edit\n")       # 模拟**人在探针跑的时候**改
            断言(原文守卫(己, b"probe patch\n") is not None,
                 "⑨ 探针注入后若被人改过，还原前的守卫必须报冲突"
                 "（这就是真事故那一秒）")
        # ★ 如实登记：`改文件` 自己仍是"**无条件**还原" ⇒ 它也有这个洞。
        #   R74 只修了探针侧（`规划层齿探针.py`），这里把洞**钉在明面上**，
        #   免得以后有人以为 `改文件` 已经安全了。
        断言(己.read_bytes() == 原己,
             "⑨ 改文件：**无条件**还原（如实登记：这个洞 R74 尚未修）")
    finally:
        globals()["_锁目录"] = 旧锁目录
        if 旧锁目录 is None:
            os.environ.pop("ORCH_LOCK_DIR", None)
        else:
            os.environ["ORCH_LOCK_DIR"] = 旧锁目录
        import shutil
        shutil.rmtree(根, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════
    # 【R113】产品源码只能经 `原子写` 落盘（真事故：强杀留下全零源码）
    # ══════════════════════════════════════════════════════════════════
    # 真事故（2026-10-20 03:52）：我强杀了一个正在 `write_bytes(原字节)` 的
    #   探针 ⇒ NTFS 留下 785,173 字节的纯零 `generic_goal.py`。
    #   **产品源码当场没了**；能救回来纯属侥幸（侧车里有清洁字节）。
    try:
        # ① 行为齿：`原子写` 真的写得对
        _t113 = Path(tempfile.mkdtemp(prefix="r113_"))
        try:
            _f113 = _t113 / "a.txt"
            _f113.write_bytes(b"old")
            原子写(_f113, b"new")
            if _f113.read_bytes() != b"new":
                fails.append("【R113】★ `原子写` 没把内容写进去")
            # ② ★ 核心语义：**中途被杀不产生中间态**。
            #   模拟：临时文件写了一半、进程死掉（不 replace）
            #   ⇒ 目标文件**必须还是旧字节**，绝不能是半截或全零。
            _临113 = _t113 / ".a.txt.原子写.99999.tmp"
            _临113.write_bytes(b"half")
            if _f113.read_bytes() != b"new":
                fails.append("【R113】★ 临时文件存在时目标已被改 ⇒ 不是原子写")
            # ③ ★ 反向（防假红）：**非**原子写在这种情况下**确实**会毁文件
            #   —— 证明本齿守的是一件真事，不是摆设。
            _坏113 = _t113 / "b.txt"
            with open(_坏113, "wb") as _fh113:
                _fh113.write(b"old")
            # "截断但没写" = 强杀恰好落在两者之间
            with open(_坏113, "wb"):
                pass
            if _坏113.read_bytes() != b"":
                fails.append("【R113】★ 夹具不成立：非原子写在截断后不是空文件")
        finally:
            import shutil as _sh113
            _sh113.rmtree(_t113, ignore_errors=True)
        # ④ ★★ 静态齿（AST，不受注释/字符串干扰）：`checks/` 下除本文件外
        #   **不得出现任何 `X.write_bytes(...)`** —— 一条无例外的规矩。
        #   ★ 为什么用 AST 而不是文本：文本会被注释和字符串骗（已踩 12 次）。
        import ast as _ast113
        _根113 = Path(__file__).parent
        for _g113 in sorted(_根113.glob("*.py")):
            if _g113.name == "_互斥.py":
                continue
            _树113 = _ast113.parse(_g113.read_text(encoding="utf-8"))
            for _n113 in _ast113.walk(_树113):
                if (isinstance(_n113, _ast113.Call)
                        and isinstance(_n113.func, _ast113.Attribute)
                        and _n113.func.attr == "write_bytes"):
                    fails.append(f"【R113】★ `{_g113.name}` 第 {_n113.lineno} 行仍用 "
                                 f"`write_bytes` 写文件 ⇒ 强杀会留下全零/半截文件"
                                 f"（真事故：`generic_goal.py` 变 785173 字节纯零）"
                                 f" ⇒ 必须改用 `原子写`")
    except Exception as _e113:
        fails.append(f"【R113】齿自身异常：{type(_e113).__name__}: {_e113}")

    if fails:
        print(f"互斥 自检：{N - len(fails)}/{N} —— 红 {len(fails)}")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print(f"互斥 自检：{N}/{N} 全绿")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_自检())
