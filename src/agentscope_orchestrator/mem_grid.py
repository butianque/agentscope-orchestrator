"""结构真值 —— 网格原子的**规范形**（口径移植，非源码复制）。

★ 来源与边界（2026-10-30）
  口径取自 示例工程 `src\\核心映射\\感知流.rs`（`原子像` / `结构真值_完整` / `数洞` /
  结构序），算法逐行对齐；**不复制源码文件、不建立运行时依赖**。
  两个库必须独立：那边一改，编排器不能跟着废。

★ 为什么编排器要它
  编排器缺的不是"判据"（判据够严），是**喂给模型什么上下文**。
  而这条缺口的一半是「结构寻址」——找"结构上必然相关"的东西，不需要常识。
  这套原子模型正好是零常识、可机验的：面积/盒/掩/洞全是结构量，没有阈值、没有统计。

★ 关键性质（验收判据，非承诺）
  · 确定性：同一输入 ⇒ 逐位相同的结构真值与结构流字节；
  · 命名无关：色是**秩**不是名字 ⇒ 换标识符名不影响；
  · 结构敏感：块结构变 ⇒ 原子集变。
"""

from __future__ import annotations

from dataclasses import dataclass

背景 = 0          # 约定：0 恒为背景（空白/被丢弃的注释）
_结构流魔数 = b"ST1"


@dataclass(frozen=True)
class 原子像:
    """一个结构原子（同色 4 向连通块）的形状本身。"""

    面积: int
    色秩: int
    盒: tuple[int, int, int, int]        # (y0, x0, y1, x1) 相对内容包围盒，上界开
    掩: tuple[bool, ...]                 # 盒内形状，行优先
    洞: int                              # 盒内不与盒边界连通的空白连通块数


@dataclass(frozen=True)
class 结构真值:
    """能逆向渲染的结构真值（规范形）：内容包围盒 + 原子们（结构序）。"""

    宽: int
    高: int
    原子们: tuple[原子像, ...]

    # ── 派生读数（全部是结构量，无阈值）──────────────────────────
    @property
    def 原子数(self) -> int:
        return len(self.原子们)

    @property
    def 洞总数(self) -> int:
        return sum(a.洞 for a in self.原子们)

    def 指纹(self) -> str:
        """结构流字节的 sha256 —— 跨轮记忆用它认身份。"""
        import hashlib

        return hashlib.sha256(结构流(self)).hexdigest()


def _色集(值: list[int]) -> list[int]:
    return sorted({v for v in 值 if v != 背景})


def _数洞(掩: list[bool], w: int, h: int) -> int:
    """掩码盒内、**不与盒边界连通**的空白连通块数（与源实现同定义）。"""
    if w <= 0 or h <= 0:
        return 0
    可达 = [False] * (w * h)
    栈: list[int] = []
    for i in range(w * h):
        x, y = i % w, i // w
        if (x == 0 or y == 0 or x + 1 == w or y + 1 == h) and not 掩[i]:
            可达[i] = True
            栈.append(i)
    while 栈:
        j = 栈.pop()
        x, y = j % w, j // w
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= w or ny >= h:
                continue
            k = ny * w + nx
            if not 掩[k] and not 可达[k]:
                可达[k] = True
                栈.append(k)
    # 剩下没被可达的空白 = 洞；按连通块计数
    见 = [False] * (w * h)
    洞数 = 0
    for i in range(w * h):
        if 掩[i] or 可达[i] or 见[i]:
            continue
        洞数 += 1
        栈 = [i]
        见[i] = True
        while 栈:
            j = 栈.pop()
            x, y = j % w, j // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                k = ny * w + nx
                if not 掩[k] and not 可达[k] and not 见[k]:
                    见[k] = True
                    栈.append(k)
    return 洞数


def 结构像(行们: list[list[int]], 背景值: int = 背景,
           色秩表: dict[int, int] | None = None) -> 结构真值:
    """网格 → 结构真值（内容包围盒裁掉外边空白，原子按结构序排列）。

    ★ `色秩表`（2026-10-30 加，实测必需）
      默认 `None` ⇒ 色秩 = **在场颜色的排序名次**（源实现口径）。这个口径让
      「换编码不变」成立，但代价是**同名次的不同色不可区分**：
      实测 `for x in y:` 与 `while x in y:` 得到**同一指纹** —— 因为 `for` 与
      `while` 在各自文件里都排第 1。
      代码不同：关键字承担语义，必须能区分。⇒ 调用方可传**绝对色秩表**
      （如 `{v: v}`），使色秩不再依赖"这次出现了哪些色"。
      默认不变，故既有网格口径不受影响。
    """
    h = len(行们)
    w = len(行们[0]) if h else 0
    if h == 0 or w == 0:
        return 结构真值(0, 0, ())
    值 = [v for 行 in 行们 for v in 行]
    if 色秩表 is not None:
        秩表 = 色秩表
    else:
        集 = _色集([v for v in 值 if v != 背景值])
        秩表 = {v: i + 1 for i, v in enumerate(集)}

    上, 下, 左, 右 = h, 0, w, 0
    for y in range(h):
        for x in range(w):
            if 值[y * w + x] != 背景值:
                上 = min(上, y)
                下 = max(下, y + 1)
                左 = min(左, x)
                右 = max(右, x + 1)
    if 上 >= 下 or 左 >= 右:                     # 全背景
        return 结构真值(0, 0, ())

    访 = [False] * (w * h)
    原子们: list[原子像] = []
    for i in range(w * h):
        if 访[i] or 值[i] == 背景值:
            continue
        色 = 值[i]
        栈 = [i]
        访[i] = True
        格们: list[int] = []
        while 栈:
            j = 栈.pop()
            格们.append(j)
            x, y = j % w, j // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                k = ny * w + nx
                if not 访[k] and 值[k] == 色:
                    访[k] = True
                    栈.append(k)
        y0, y1, x0, x1 = h, 0, w, 0
        for j in 格们:
            x, y = j % w, j // w
            y0, y1 = min(y0, y), max(y1, y + 1)
            x0, x1 = min(x0, x), max(x1, x + 1)
        ry0, ry1, rx0, rx1 = y0 - 上, y1 - 上, x0 - 左, x1 - 左
        bw, bh = rx1 - rx0, ry1 - ry0
        掩 = [False] * (bw * bh)
        for j in 格们:
            x, y = j % w, j // w
            掩[(y - 上 - ry0) * bw + (x - 左 - rx0)] = True
        原子们.append(原子像(len(格们), 秩表.get(色, 色),
                            (ry0, rx0, ry1, rx1), tuple(掩), _数洞(掩, bw, bh)))

    # 结构序：面积降 → 色秩升 → 盒升 → 掩升（元素自带键的全序，与遍历顺序无关）
    原子们.sort(key=lambda a: (-a.面积, a.色秩, a.盒, a.掩))
    return 结构真值(右 - 左, 下 - 上, tuple(原子们))


def 结构流(真: 结构真值) -> bytes:
    """确定性序列化（跨轮记忆落盘用；同一真值 ⇒ 逐位相同字节）。"""
    出 = bytearray(_结构流魔数)
    出 += 真.宽.to_bytes(8, "big") + 真.高.to_bytes(8, "big")
    出 += len(真.原子们).to_bytes(8, "big")
    for a in 真.原子们:
        出 += a.面积.to_bytes(8, "big") + a.色秩.to_bytes(8, "big") + a.洞.to_bytes(8, "big")
        for v in a.盒:
            出 += v.to_bytes(8, "big")
        出 += len(a.掩).to_bytes(8, "big")
        位 = 0
        for i, b in enumerate(a.掩):
            位 |= (1 << (i % 8)) if b else 0
            if i % 8 == 7:
                出.append(位)
                位 = 0
        if len(a.掩) % 8:
            出.append(位)
    return bytes(出)


def 解结构流(字节: bytes) -> 结构真值:
    """结构流 → 结构真值（往返逐位一致 ⇒ 落盘可信）。"""
    if not 字节.startswith(_结构流魔数):
        raise ValueError("不是 ST1 结构流")
    p = len(_结构流魔数)

    def 取8() -> int:
        nonlocal p
        v = int.from_bytes(字节[p:p + 8], "big")
        p += 8
        return v

    宽, 高, 数 = 取8(), 取8(), 取8()
    原子们: list[原子像] = []
    for _ in range(数):
        面积, 色秩_, 洞 = 取8(), 取8(), 取8()
        盒 = (取8(), 取8(), 取8(), 取8())
        长 = 取8()
        字节数 = (长 + 7) // 8
        位们 = 字节[p:p + 字节数]
        p += 字节数
        掩 = tuple(bool(位们[i // 8] & (1 << (i % 8))) for i in range(长))
        原子们.append(原子像(面积, 色秩_, 盒, 掩, 洞))
    return 结构真值(宽, 高, tuple(原子们))


# ════════════════════════════════════════════════════════════
# 自检：判据全部是结构谓词，没有阈值、没有统计量
# ════════════════════════════════════════════════════════════

def _自检() -> int:
    失败: list[str] = []

    def 判(名: str, 条件: bool, 说明: str = "") -> None:
        if not 条件:
            失败.append(f"{名}{('：' + 说明) if 说明 else ''}")

    G = [[0, 1, 1, 0],
         [0, 1, 0, 0],
         [2, 2, 2, 0],
         [0, 0, 0, 0]]
    真 = 结构像(G)
    判("原子数", 真.原子数 == 2, f"实得 {真.原子数}")
    判("内容盒", (真.宽, 真.高) == (3, 3), f"实得 {(真.宽, 真.高)}")
    判("结构序=面积降", 真.原子们[0].面积 >= 真.原子们[1].面积)
    判("色秩 1 起", min(a.色秩 for a in 真.原子们) >= 1)

    # 洞：一个口字形（内部空白不触边）
    口 = [[1, 1, 1],
         [1, 0, 1],
         [1, 1, 1]]
    判("口字有 1 洞", 结构像(口).洞总数 == 1, f"实得 {结构像(口).洞总数}")
    判("实心无洞", 结构像([[1, 1], [1, 1]]).洞总数 == 0)

    # 改名无关：色是秩，不是名字 ⇒ 换值不影响（值 1/2 与 7/9 等价）
    甲 = 结构像([[1, 1], [2, 0]])
    乙 = 结构像([[7, 7], [9, 0]])
    判("颜色改名不变", 甲 == 乙 and 甲.指纹() == 乙.指纹())

    # 结构敏感：多一块 ⇒ 原子集必须变
    丙 = 结构像([[1, 1], [2, 0], [3, 0]])
    判("多一块则不同", 甲.指纹() != 丙.指纹())

    # 确定性 + 落盘往返
    判("同输入同字节", 结构流(真) == 结构流(结构像(G)))
    判("往返逐位一致", 解结构流(结构流(真)) == 真)
    判("指纹等于字节哈希", len(真.指纹()) == 64)

    for f in 失败:
        print(f"  ✗ {f}")
    if not 失败:
        print(f"  ✓ 结构真值自检全绿（原子 {真.原子数} · 洞 {真.洞总数} · 指纹 {真.指纹()[:12]}）")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(_自检())
