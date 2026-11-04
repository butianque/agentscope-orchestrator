# -*- coding: utf-8 -*-
"""判据：`lean_spec` 在**环境撞锁**时会不会给出「未验」（rc=3），而不是记成红。

## 为什么要有这支判据

`tests/lean_spec.py` 的 ③「Lean 编译通过」在 CI 里反复红，而**同一个脚本单独跑 6/6 全绿**。
真因是并行会话的 `lake` 占着 `.lake` 里的锁。老实现**已经认出**了这一类
（`【归类】像环境撞锁/权限`），却把它只写进说明文字、**然后照样 `[FAIL]`**。

⇒ 后果：`红` 这一格里混着「产物坏了」和「今天环境不好」两种东西，
  于是「**CI 红不红**」这个动作不再可信 —— 看见红的人得先证伪一遍。
  （本轮实测：为此多跑了一次 382 秒的完整 suite。）

## 这支判据守的是"**区分力**"，不是"有没有这段代码"

它**不读源码里有没有 `判未验` 这个字符串**，而是：
  ① 直接驱动真的 `main()`（把 `找项目` / `subprocess` / `_看得见别的lake` 注入成假的）；
  ② 用**同一份夹具**，只翻一个条件，看退出码会不会真的跟着翻
     —— 翻不动 ⇒ 那个条件是装饰；**翻得动但方向不对 ⇒ 那道门在误杀**。

★ 口径：**一段驱动不了的接线，等于没有接线。**
★ 以及本轮真流程换来的那条：**门只能落在可复核的事实上**（文件、输出原文）。
  「某个探测看不见」不是事实，**不能**拿来当「没有」。
"""

import shutil
import atexit
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import lean_spec as L  # noqa: E402

失败 = []


def 断言(条件, 说明):
    print(f"  {'ok  ' if 条件 else 'FAIL'} {说明}")
    if not 条件:
        失败.append(说明)


# ⑥ 那一项要求"声明的定理都在" ⇒ 假规格里得把 17 条真写上，
# 否则本判据会因为它自己造的文件不全而红（**测的会是别的东西**）。
假规格 = "\n".join([f"theorem {t} : True := trivial" for t in L.必查定理])
假规格 = "-- 判据造的假规格（只为驱动 main，不是真 Lean 源）\n" + 假规格 + "\n"

# ★ `file:` 那一行**必须在 `.lake` 下** —— 这正是「撞锁」与「真权限问题」的分界：
#   锁冲突打的是 lake 自己的账本文件（`lakefile.olean.trace`），不是你的源码。
撞锁输出 = (
    "error: permission denied (error code: 13)\n"
    "  file: .lake/config/[anonymous]/lakefile.olean.trace\n"
)
# 真权限问题打的会是**你的源码**（或根本没有 `file:` 行）。
源码被拒 = (
    "error: permission denied (error code: 13)\n"
    "  file: MyMeta/OrchJudge.lean\n"
)


class _假唤:
    def __init__(self, rc, out):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


class _假subprocess:
    """`main()` 只用到 `run` 与 `TimeoutExpired`（`tasklist` 现场探测也走它）。"""

    TimeoutExpired = Exception

    def __init__(self, rc, out):
        self._r = _假唤(rc, out)

    def run(self, *a, **k):
        return self._r


class _假结果:
    """`subproc_safe.跑()` 的返回（2026-11-04 起 `main()` 编译真跑走这条）。"""

    def __init__(self, rc=None, out="", 超时=False):
        self.返回码 = rc
        self.输出 = out
        self.超时 = 超时
        self.秒 = 0.0

    def 行(self) -> str:
        return f"{'超时' if self.超时 else 'rc=' + str(self.返回码)} · 0.0s · {len(self.输出)} 字符"


末文 = ""          # 上一次 驱动() 里 main() 的完整打印文本


def 驱动(根: Path, *, rc: int, 出: str, 有锁: bool, 看得见: bool,
        超时: bool = False) -> tuple[int, list, list]:
    """跑一次真的 `main()`，返回（退出码, 失败, 未验）。"""
    锁目录 = 根 / ".lake" / "config" / "[anonymous]"
    if 有锁:
        锁目录.mkdir(parents=True, exist_ok=True)
        (锁目录 / "lakefile.olean.lock").write_text("", encoding="utf-8")
    elif 锁目录.is_dir():
        shutil.rmtree(锁目录)

    L.通过.clear()
    L.失败.clear()
    L.未验.clear()
    L.找项目 = lambda: 根
    L.subprocess = _假subprocess(rc, 出)
    # ★ 2026-11-04：编译真跑改成 `subproc_safe.跑()`（孙进程持管道 ⇒
    #   `subprocess` 的超时管不到它，会把 CI 挂死）。桩要跟着挪过来，
    #   否则这里会**安静地不打桩**、驱动的是真的 `lake`（判据自己变成慢测试）。
    import agentscope_orchestrator.subproc_safe as _SS
    _SS.跑 = lambda *a, **k: _假结果(rc, 出, 超时)
    L._看得见别的lake = lambda: 看得见
    # ★ `未验` 列表只存**名字**，那句"为什么没验成"是 print 出来的 ⇒
    #   不捕获 stdout，判据就只能核"落没落进未验"、核不了"说明写没说清"。
    import contextlib
    import io
    缓冲 = io.StringIO()
    with contextlib.redirect_stdout(缓冲):
        rc出 = L.main()
    global 末文
    末文 = 缓冲.getvalue()
    return rc出, list(L.失败), list(L.未验)


print("lean_spec 撞锁出口判据：认得出环境的走「未验」，认不出的照实报红")

根 = Path(tempfile.mkdtemp(prefix="ls_lock_"))
atexit.register(shutil.rmtree, 根, ignore_errors=True)  # 跑完/跑挂都不在包根留临时目录
(根 / "lakefile.lean").write_text("-- 假的\n", encoding="utf-8")
(根 / L.模块相对).parent.mkdir(parents=True, exist_ok=True)
(根 / L.模块相对).write_text(假规格, encoding="utf-8")

try:
    # ── ⓪ 超时（**排队等锁**这一种，没有 permission denied）⇒ 未验 ──
    #
    # ★ 2026-11-04 新增。老 `lean_spec` 只有"被拒"那一种出口；并行会话持锁时
    #   `lake` 是**排队等**，于是它挂死、超时后被判成**[FAIL] 红** ——
    #   同一件环境问题，两种形态两种判法。这条把新出口钉住。
    码, 红, 未 = 驱动(根, rc=0, 出="", 有锁=False, 看得见=True, 超时=True)
    断言(码 == 3, f"⓪ **超时（排队等锁）⇒ rc=3（未验）**，实得 {码}")
    断言(not 红, f"⓪ 超时**不许**记成红（红={红}）—— 那 180 秒里它一次都没验过")
    断言(any("③" in x for x in 未) and ("没验成" in 末文 or "超时" in 末文),
         f"⓪ 超时落进「未验」且**说明**写清了「没验成，不是它坏了」：未验={未}、"
         f"打印={末文.strip()[-90:]!r}")

    # ── ① 撞锁三条件齐全 ⇒ **未验（3）** ──────────────────────────
    码, 红, 未 = 驱动(根, rc=1, 出=撞锁输出, 有锁=True, 看得见=True)
    断言(码 == 3, f"**撞锁三条件齐全 ⇒ rc=3（未验）**，实得 {码}")
    断言(any("③" in x for x in 未), f"③ 落进 `未验` 而不是 `失败`（未验={未}）")
    断言(any("④" in x for x in 未),
         f"④ **也**落进 `未验` —— 编都没编成，`#print axioms` 什么都没有，"
         f"拿它断言「定理覆盖不全」是**假红**（未验={未}）")
    断言(not 红, f"一个真红都不该有（失败={红}）")

    # ── ② ★ 只翻「进程探测」⇒ **结论不许变** ────────────────────
    #    这是本轮最值钱的一条：我原来把"当时真有别的 lake 在跑"当成了门，
    #    结果真流程里 `tasklist` 只看得见本会话的进程、判成「没人跑」，
    #    **把一次真撞锁判成了红**。⇒ 探测只能是旁证。
    码2, 红2, 未2 = 驱动(根, rc=1, 出=撞锁输出, 有锁=True, 看得见=False)
    断言(码2 == 3, f"**只把「看得见别的 lake」翻成假 ⇒ rc 仍是 3**（探测是旁证，不是门），"
                   f"实得 {码2}")
    断言(not 红2, f"不许因为「看不见」就报红（失败={红2}）")

    # ── ②b ★ 只翻「被拒路径在不在 .lake 下」⇒ 结论**必须**变 ─────
    码2b, 红2b, 未2b = 驱动(根, rc=1, 出=源码被拒, 有锁=True, 看得见=True)
    断言(码2b == 1, f"**被拒的是源码文件 ⇒ 照实报红**，实得 {码2b}")
    断言(any("③" in x for x in 红2b), f"这时它必须落进 `失败`（失败={红2b}）")

    # ── ③ 只翻一个条件：没有 .lock 文件 ⇒ 报红 ────────────────────
    码3, 红3, 未3 = 驱动(根, rc=1, 出=撞锁输出, 有锁=False, 看得见=True)
    断言(码3 == 1, f"**没有 `.lock` 文件 ⇒ 不像撞锁，报红**，实得 {码3}")

    # ── ④ 只翻一个条件：输出里没有 permission denied ⇒ 报红 ───────
    码4, 红4, 未4 = 驱动(根, rc=1, 出="error: unknown identifier 'foo'\n",
                        有锁=True, 看得见=True)
    断言(码4 == 1, f"**真编译错误 ⇒ 报红**（不许被吞成未验），实得 {码4}")

    # ── ⑤ 编译成功 ⇒ 全绿（0） ────────────────────────────────────
    #  ★ 这一步的假输出**必须把 17 条定理的 axioms 都印全** ——
    #    第一版我只喂了一条 `OrchJudge.foo`，于是 ④「覆盖全部必查定理」如实报红、
    #    退出码 1，判据红在**它自己造的夹具**上。
    #    （口径：判据红了先怀疑自己的夹具 —— 但要用工具核实，不许靠感觉。）
    全公理 = "\n".join(f"OrchJudge.{t} does not depend on any axioms" for t in L.必查定理)
    码5, 红5, 未5 = 驱动(根, rc=0, 出=全公理, 有锁=True, 看得见=True)
    断言(码5 == 0, f"**编译成功 ⇒ rc=0**，实得 {码5}（红={红5} 未验={未5}）")

    # ── ⑥ 读数要能说清"凭什么"，而且**不许把"看不见"说成"没有"** ──
    是, 证 = L.像撞锁(撞锁输出, 根, 进程探测=lambda: True)
    断言(是 and ".lock" in 证 and ".lake" in 证,
         f"判「是撞锁」时证据里要有 `.lock` 与「被拒路径在 .lake 下」两样（实得 {证!r}）")
    是2, 证2 = L.像撞锁(撞锁输出, 根, 进程探测=lambda: False)
    断言(是2, "探测看不见**也照样**是「是撞锁」（旁证不改变结论）")
    断言("看不见" in 证2 and "不影响结论" in 证2,
         f"★ 探测看不见时，说明里必须点破「看不见≠没人在跑、这条旁证不影响结论」"
         f"（实得 {证2!r}）")
    否, 证3 = L.像撞锁("error: unknown identifier 'foo'", 根, 进程探测=lambda: True)
    断言((not 否) and "permission denied" in 证3,
         f"判「不是撞锁」时要说清**缺哪个条件**（实得 {证3!r}）")
    否2, 证4 = L.像撞锁(源码被拒, 根, 进程探测=lambda: True)
    断言((not 否2) and ".lake" in 证4,
         f"源码被拒 ⇒ 不是撞锁，且要说清是「被拒路径不在 .lake 下」（实得 {证4!r}）")

    # ── ⑦ `退出码` 的优先级：**真红不许被未验稀释** ───────────────
    断言(L.退出码([], []) == 0, "退出码：全绿 ⇒ 0")
    断言(L.退出码([], ["x"]) == 3, "退出码：没真红、有未验 ⇒ 3")
    断言(L.退出码(["x"], []) == 1, "退出码：有真红 ⇒ 1")
    断言(L.退出码(["x"], ["y"]) == 1,
         "退出码：**真红优先** —— 有一条真红就不许被「未验」稀释掉")
finally:
    shutil.rmtree(根, ignore_errors=True)

print()
if 失败:
    print(f"lean_spec 撞锁出口判据：红 {len(失败)} 项 —— {失败}")
    sys.exit(1)
print("lean_spec 撞锁出口判据：绿 —— **认得出环境的走「未验」、认不出的照实报红**，"
      "而且每一条都靠**翻一个条件**证明它不是装饰；"
      "**「看不见」不当「没有」**（探测翻成假，结论照样是 3）")
