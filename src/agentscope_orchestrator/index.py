"""全称索引：登记可检索单元 + 覆盖不变量 + 按需取回。

核心承诺（诚实）："不漏的是可达性，不是同时常驻"。索引只记录
key->path 元数据（不缓存文件内容），取回按需读盘。覆盖不变量：
建库后 registered 集合 == discovered 集合，即没有遗漏的可检索单元。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("index")

_THEOREM_RE = re.compile(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_']*)\b",
                         re.MULTILINE)


class UnitIndex:
    def __init__(self) -> None:
        self._units: dict[str, Path] = {}
        self._coverage: int | None = None  # 建库时 discovered 单元总数
        # ★★★★★ 修复 R1-F1：覆盖不变量的**证据本体**。
        #   旧实现 `coverage_holds` 只查 `self._coverage >= 0` —— 那对「计数」
        #   **恒真** ⇒ 它声称守「registered == discovered」，实际只查了「索引非空」。
        #   现在把 discovered 的**规范化键**存下来，逐条查可达性。
        self._discovered_keys: list[str] = []

    def register(self, key: str, path: Path) -> None:
        if key in self._units and self._units[key] != path:
            raise ValueError(f"duplicate key with different path: {key}")
        self._units[key] = path

    def build(self, root: Path, glob: str = "**/*.py") -> tuple[int, int]:
        """扫描目录建库，返回 (registered, discovered)。覆盖不变量：两者相等。"""
        discovered = sorted(p for p in root.rglob(glob) if p.is_file())
        for p in discovered:
            self.register(p.stem, p)          # 按文件名
            self.register(str(p), p)          # 按全路径
        self._coverage = len(discovered)
        # ★ 每个 discovered 文件的**规范可达键** = 其全路径（两种 build 都注册了它）
        self._discovered_keys = [str(p) for p in discovered]
        return len(self._units), len(discovered)

    @staticmethod
    def _rel_key(root: Path, p: Path) -> str:
        """点式路径：<root>/A/B/X.lean -> A.B.X（与项目无关）"""
        rel = p.relative_to(root).with_suffix("")
        return ".".join(rel.parts)

    def build_artifact(self, root: Path, exclude_disabled: bool = True,
                       key_prefix: str = "",
                       disabled_marker: str = "_disabled",
                       glob: str = "**/*.lean") -> tuple[int, int]:
        """把产物库的模块登记进索引，供方向锚定取回真单元（项目无关）。

        键空间（唯一性保证：不与其他文件冲突）：
          - 全路径 str(p)
          - 点式路径      InfraCore.Derivation.InfinitePan
          - 前缀式路径    <key_prefix>.InfraCore.Derivation.InfinitePan（若声明）
          - 单元级锚定    上两种前缀 + '.' + theorem/lemma 名

        exclude_disabled=True 跳过含 disabled_marker 的目录（归档/不可编译代码
        不入索引，避免把真实生产线锚定到已停用单元）。注意：不注册裸文件名
        （树内 stem 可能跨目录重复，裸名会触发 duplicate key 冲突）。
        返回 (registered, discovered)。"""
        discovered = []
        for p in sorted(root.rglob(glob)):
            if not p.is_file():
                continue
            if exclude_disabled and disabled_marker and \
                    any(disabled_marker in d for d in p.parts):
                continue
            discovered.append(p)
        for p in discovered:
            dotted = self._rel_key(root, p)
            self.register(str(p), p)
            self.register(dotted, p)
            prefixes = [f"{key_prefix}.{dotted}"] if key_prefix else []
            for pre in prefixes:
                self.register(pre, p)
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            for m in _THEOREM_RE.finditer(text):
                name = m.group(1)
                self.register(f"{dotted}.{name}", p)
                for pre in prefixes:
                    self.register(f"{pre}.{name}", p)
        self._coverage = len(discovered)
        self._discovered_keys = [str(p) for p in discovered]
        return len(self._units), len(discovered)

    def coverage_holds(self) -> bool:
        """★★★★★ 覆盖不变量（修复 R1-F1）：**每个 discovered 单元都可达**。

        ★ 旧实现是 `self._coverage >= 0 and len(self._units) > 0` ——
          `>= 0` 对计数**恒真** ⇒ 这是一条**恒真门**：
          它声称守「registered == discovered，没有遗漏的可检索单元」，
          实际只查了「索引非空」。索引少登记一半文件它也照样报「覆盖闭合」。
        ★ 现在逐条查：`discovered` 里每个文件的全路径键必须能 `query` 回**它自己**。
          这才对得起本模块开头那句承诺（"不漏的是可达性"）。
        """
        if self._coverage is None:
            return False
        if not self._discovered_keys:
            return False
        return all(self._units.get(k) is not None for k in self._discovered_keys)

    def coverage_gap(self) -> list[str]:
        """覆盖缺口的**无损读数**（R1-F1）：返回不可达的 discovered 键。

        ★ 与 `coverage_holds` 同一判据的**因由面** —— 只给 `Bool` 无法诊断
          「漏了哪个」（那正是 `Defect384`）。诊断一律读这里。
        """
        if self._coverage is None:
            return ["<未建库>"]
        return [k for k in self._discovered_keys if self._units.get(k) is None]

    def keys(self) -> list[str]:
        """索引里全部键（只读快照）—— 供"按名字找"用。"""
        return list(self._units)

    def 找(self, 词: str, 上限: int = 8) -> list[tuple[str, Path]]:
        """按名字找：**精确 > 后缀 > 包含**，同级按键长升序（短的更像"就是它"）。

        ★ 为什么必须有后缀这一档：**层级化库**（形式化/命名空间类仓库）的键是
          **点式模块键**（`顶层.子模块.符号`），而人只会说**裸符号名**。
          实测：裸名 `query` 返回 `None` —— 于是"已证明的东西能不能按名字
          准确拿到"这一步在裸名上整条是断的。
          修法在**查询**侧，不许去改数据去迁就查询。
        """
        词 = (词 or "").strip()
        if not 词:
            return []
        精: list[tuple[str, Path]] = []
        后: list[tuple[str, Path]] = []
        前: list[tuple[str, Path]] = []
        含: list[tuple[str, Path]] = []
        for k, p in self._units.items():
            if k == 词:
                精.append((k, p))
            elif k.endswith("." + 词):
                后.append((k, p))
            elif k.startswith(词):
                # ★ 前缀单独一档：实测查「回执255」时，前缀命中的那份
                #   因为名字长被"按长度升序"排到了后面、直接截掉 ——
                #   而那恰恰是最相关的一份。**相关度不许由长度替它决定。**
                前.append((k, p))
            elif 词 in k or 词 in p.name:
                含.append((k, p))
        out = (精 + sorted(后, key=lambda x: len(x[0]))
               + sorted(前, key=lambda x: (len(x[0]), x[0]))
               + sorted(含, key=lambda x: (len(x[0]), x[0])))
        return out[:max(1, 上限)]

    def query(self, key: str) -> Path | None:
        """按需取回：返回单元路径，不加载内容。可达性不漏=query 不漏已知 key。"""
        return self._units.get(key)

    def fetch(self, key: str) -> str | None:
        """按需取回内容（读盘）。"""
        p = self.query(key)
        return p.read_text(encoding="utf-8") if p and p.is_file() else None

    def size(self) -> int:
        return len(self._units)