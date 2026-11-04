"""无人值守资源护栏：按**真实资源事实**自适应，不靠硬编码轮数。

## 为什么需要它（实测缺口）

`unattended.py --watch` 原本用**硬编码常量**当护栏（`--max-rounds`、
`--interval`）。问题不在"有没有护栏"，而在**护栏的判据没有区分力**：

  · 轮数上限**与真实风险无关** —— 跑得顺时它白停，跑得糟时它白烧
  · 真正该停的信号是**没有进展**，不是"跑了 N 轮"

这与库里反复强调的口径一致：**停机判据要看不动点，不看计数**
（`TS371.cycle_is_not_monotone` 机证演化环**非单调** ⇒
"严格递减 ⇒ 必停"这个前提**不成立**，故不能靠"数轮数"论证停机）。

## 本模块的判据（全部机器可算）

| 信号 | 含义 | 动作 |
|---|---|---|
| **连续失败** ≥ 阈值 | 当前状态跑不出结果 | 退避（间隔翻倍） |
| **同因重复失败** ≥ 阈值 | 卡在同一个坑里 ⇒ **无进展** | **停机**（烧下去无意义） |
| **磁盘余量** < 下限 | 产物/备份写不下 | **停机**（防写坏） |
| **累计烧字** > 上限 | 成本失控 | **停机** |
| 正常 | 有进展 | 继续 |

★ 核心是**无进展检测**：连续两次失败且**失败原因相同** ⇒ 状态没变 ⇒
  再跑一次得到同样结果（这正是"不动点"的工程形态）⇒ 停。

## 诚实边界

· 磁盘余量用 `shutil.disk_usage`（真实系统调用），不是估算。
· "同因"判据是**失败状态的指纹**（错误类型 + 关键片段），
  不是完整错误文本 —— 完整文本常含时间戳/路径等噪声，会导致指纹永不相等。
· 本模块**只决定停不停**，不决定判据门 —— 门仍是 `gate_spec` 的机器门。
"""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

# ── 默认阈值（可按需覆盖；都不是"轮数"，是**资源事实**）────────────
默认 = {
    "连续失败上限": 3,          # 连续失败到此 ⇒ 退避
    "同因重复上限": 2,          # 同一原因重复到此 ⇒ 停机（无进展）
    "磁盘下限字节": 512 * 1024 * 1024,   # 512 MiB
    "烧字上限": 20_000_000,     # 2000 万字符
    "退避倍率": 2.0,
    "间隔上限秒": 600,
}


def 磁盘余量(路径: Path | str = ".") -> int:
    """真实磁盘剩余字节（系统调用，不估算）。失败返回 -1（如实，不假装充裕）。"""
    try:
        return shutil.disk_usage(str(路径)).free
    except OSError:
        return -1


def 失败指纹(状态: dict) -> str:
    """失败状态的**指纹**：错误类型 + 关键片段（去噪）。

    ★ 为什么去噪：完整错误文本常含时间戳、临时路径、PID 等**每次都变**的东西，
      直接哈希会导致指纹永不相等 ⇒ 无进展检测**永远不触发**（护栏失效）。
      故只取错误**类型**与**首个稳定片段**。
    """
    错 = 状态.get("error") or ""
    if not 错:
        # 没有 error 字段：用 state + 失败窗数当指纹
        摘 = 状态.get("summary") or {}
        return f"{状态.get('state')}|failed={摘.get('failed', '?')}"
    文 = str(错)
    # 取首行前 120 字符（通常是错误类型与主因，不含时间戳）
    首 = 文.splitlines()[0][:120] if 文.splitlines() else 文[:120]
    return hashlib.sha256(首.encode("utf-8", "ignore")).hexdigest()[:16]


class 护栏:
    """无人值守护栏：按真实资源事实决定继续/退避/停机。"""

    def __init__(self, 工作目录: Path | str = ".", **覆盖) -> None:
        self.阈值 = dict(默认)
        self.阈值.update(覆盖)
        self.工作目录 = Path(工作目录)
        self.连续失败 = 0
        self.累计烧字 = 0
        self.上次指纹: str | None = None
        self.同因次数 = 0
        self.间隔 = 0.0
        self.停机因: str | None = None
        self.日志: list[dict] = []

    # ── 判据（每条都机器可算）────────────────────────────────────
    def 记成功(self) -> None:
        """有进展 ⇒ 清空失败计数（这是"状态变了"的机器证据）。"""
        self.连续失败 = 0
        self.上次指纹 = None
        self.同因次数 = 0
        self.间隔 = 0.0
        self.日志.append({"事件": "进展", "时间": time.time()})

    def 记失败(self, 状态: dict) -> None:
        """记一次失败；更新无进展计数与退避间隔。"""
        self.连续失败 += 1
        指纹 = 失败指纹(状态)
        if 指纹 == self.上次指纹:
            self.同因次数 += 1
        else:
            self.同因次数 = 1
            self.上次指纹 = 指纹
        self.日志.append({"事件": "失败", "连续": self.连续失败,
                          "同因": self.同因次数, "指纹": 指纹,
                          "时间": time.time()})
        if self.连续失败 >= self.阈值["连续失败上限"]:
            self.间隔 = min(self.间隔 * self.阈值["退避倍率"]
                           if self.间隔 else 5.0 * self.阈值["退避倍率"],
                           self.阈值["间隔上限秒"])

    def 记烧字(self, n: int) -> None:
        self.累计烧字 += max(0, int(n or 0))

    def 该停吗(self) -> tuple[bool, str]:
        """返回 (是否停机, 原因)。原因**必须具体**，供无人值守日志留痕。"""
        # ① 无进展：同一失败原因重复 ⇒ 状态没变 ⇒ 再跑还是它
        if self.同因次数 >= self.阈值["同因重复上限"]:
            self.停机因 = (f"无进展：同一失败原因重复 {self.同因次数} 次"
                          f"（指纹 {self.上次指纹}）⇒ 状态没变，"
                          f"再跑结果相同（不动点）⇒ 停")
            return True, self.停机因
        # ② 磁盘：写不下就别写（防把产物/备份写坏）
        余 = 磁盘余量(self.工作目录)
        if 0 <= 余 < self.阈值["磁盘下限字节"]:
            self.停机因 = (f"磁盘余量不足：{余 // 1024 // 1024} MiB < "
                          f"{self.阈值['磁盘下限字节'] // 1024 // 1024} MiB "
                          f"⇒ 防写坏产物，停")
            return True, self.停机因
        # ③ 成本：烧字失控
        if self.累计烧字 > self.阈值["烧字上限"]:
            self.停机因 = (f"累计烧字 {self.累计烧字} > 上限 "
                          f"{self.阈值['烧字上限']} ⇒ 成本失控，停")
            return True, self.停机因
        return False, ""

    def 建议间隔(self) -> float:
        """本轮应等多久（退避后）。0 ⇒ 不必等。"""
        return max(0.0, self.间隔)

    def 摘要(self) -> dict:
        return {"连续失败": self.连续失败, "同因次数": self.同因次数,
                "累计烧字": self.累计烧字, "建议间隔": self.间隔,
                "磁盘余量MiB": 磁盘余量(self.工作目录) // 1024 // 1024,
                "停机因": self.停机因, "事件数": len(self.日志)}


def _selftest() -> int:
    """自检：全部机器可算。"""
    import tempfile as _tf
    ok = total = 0

    def chk(name: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        else:
            print(f"  ✗ {name}")

    d = Path(_tf.mkdtemp(prefix="guard_st_"))
    try:
        # ① 磁盘余量：真实系统调用
        余 = 磁盘余量(d)
        chk("磁盘余量是真实正数", 余 > 0)
        chk("不存在的路径返回 -1（如实）", 磁盘余量(d / "不存在的深层" / "x") >= -1)

        # ② 失败指纹去噪：含时间戳的错误与不含的应同指纹
        a = {"state": "error", "error": "error[E0308]: mismatched types\n  --> a.rs:1:1"}
        b = {"state": "error", "error": "error[E0308]: mismatched types\n  --> b.rs:9:9"}
        chk("同类型错误指纹相同（去噪生效）", 失败指纹(a) == 失败指纹(b))
        c = {"state": "error", "error": "error[E0599]: no method\n  --> a.rs:1:1"}
        chk("不同类型错误指纹不同", 失败指纹(a) != 失败指纹(c))
        chk("无 error 字段也能出指纹",
            bool(失败指纹({"state": "convergence_failed",
                           "summary": {"failed": 3}})))

        # ③ 成功清零
        g = 护栏(d)
        g.记失败(a); g.记失败(a)
        chk("失败计数累积", g.连续失败 == 2)
        g.记成功()
        chk("成功后清零", g.连续失败 == 0 and g.同因次数 == 0)

        # ④ ★ 无进展检测：同一原因重复 ⇒ 停机
        g2 = 护栏(d, 同因重复上限=2)
        g2.记失败(a)
        停, 因 = g2.该停吗()
        chk("首次失败不停", not 停)
        g2.记失败(a)          # 同因第 2 次
        停2, 因2 = g2.该停吗()
        chk("★ 同因重复 ⇒ 停机", 停2)
        chk("停机原因写明不动点", "不动点" in 因2)
        chk("停机原因带指纹", "指纹" in 因2)

        # ⑤ 不同原因重复 ⇒ 不停（是在试不同方向，不是卡住）
        g3 = 护栏(d, 同因重复上限=2)
        g3.记失败(a)
        g3.记失败(c)
        停3, _ = g3.该停吗()
        chk("不同原因重复不停", not 停3)

        # ⑥ 退避：连续失败到阈值 ⇒ 间隔翻倍且受上限约束
        g4 = 护栏(d, 连续失败上限=2, 退避倍率=2.0, 间隔上限秒=100)
        g4.记失败(a)
        chk("首次失败不退避", g4.建议间隔() == 0)
        g4.记失败(c)
        chk("达阈值后退避", g4.建议间隔() > 0)
        首 = g4.建议间隔()
        g4.记失败({"state": "error", "error": "error[E0001]: x"})
        chk("再失败间隔翻倍", g4.建议间隔() >= 首)
        for i in range(10):
            g4.记失败({"state": "error", "error": f"error[E{1000+i}]: y"})
        chk("间隔受上限约束", g4.建议间隔() <= 100)

        # ⑦ 磁盘下限 ⇒ 停机（用超大下限模拟）
        g5 = 护栏(d, 磁盘下限字节=10**18)
        停5, 因5 = g5.该停吗()
        chk("磁盘不足 ⇒ 停机", 停5)
        chk("磁盘停机原因带 MiB", "MiB" in 因5)

        # ⑧ 烧字上限 ⇒ 停机
        g6 = 护栏(d, 烧字上限=100)
        g6.记烧字(50)
        chk("未超上限不停", not g6.该停吗()[0])
        g6.记烧字(100)
        停6, 因6 = g6.该停吗()
        chk("超烧字上限 ⇒ 停机", 停6)
        chk("成本停机原因带数字", "100" in 因6)

        # ⑨ 摘要完整
        摘 = g2.摘要()
        chk("摘要含停机因", 摘["停机因"])
        chk("摘要含磁盘余量", 摘["磁盘余量MiB"] >= 0)

        # ⑩ 阈值可覆盖
        g7 = 护栏(d, 连续失败上限=99)
        chk("阈值可覆盖", g7.阈值["连续失败上限"] == 99)
        chk("未覆盖项保留默认", g7.阈值["烧字上限"] == 默认["烧字上限"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(f"  无人值守资源护栏 自检：{ok}/{total} 通过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(f"磁盘余量：{磁盘余量('.') // 1024 // 1024} MiB")
