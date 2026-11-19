# -*- coding: utf-8 -*-
"""打 wheel —— **不依赖 setuptools/wheel/网络，也不经 tar 解包**（R535f/g/h）。

★★★★ 为什么自己写（三件实测的事，每件都让我猜错了方向）：
  ① `pip wheel .` **挂 20 分钟且一个字不报** —— 查到底是
     **venv 里只有 pip**（setuptools 没有、wheel 没有、基础解释器也没有、
     pip 缓存里也没有）⇒ `--no-build-isolation` 找不到后端 ⇒ **转头联网装**，
     而这台机器网络极慢。★ 口径 380：**「慢」与「在联网」在读数上无法区分**。
  ② `RECORD` 的第一版有 **1,739,722 字符**的一行 —— 因为 `_行(h)` 是个
     **双关签名**（`str` 走摘要、`bytes` 直接当摘要用），而我传的是**文件内容**。
     ★ 是 `pip install` 报 `_csv.Error` 抓出来的，**我自己的自检发现不了**。
     ⇒ 口径 381：**参数不许双关**。
  ③ `tar -xf` 解包 `git archive` 的产物时**丢掉中文名文件** ✗ ——
     实测差 6 个：`口径门.py` `只读探针.py` `接线门.py` **`知库.py`**
     `见证门.py` `语义校准.py`（**全是汉字名**），而 `git archive` 的 tar
     成员里它们**都在** ✓。⇒ 交付包因此缺模块、**4 个命令行入口 ImportError**。
     ★ 与当天早先抓到的 `findstr` ANSI 输出**同一族**：**跨工具边界时编码会变**。
     ⇒ 本版**直接用 Python 的 `tarfile` 吃 `git archive` 的字节流**，
       **一步都不经文件系统**（顺带保证：包的内容 = git 的内容，可审计）。

★ 用法：
    python tools/打wheel.py                      # 从 HEAD 打包（推荐：可审计）
    python tools/打wheel.py --源树 <目录>        # 从目录打包（开发时用）
"""
import base64
import csv
import hashlib
import io
import pathlib
import subprocess
import sys
import tarfile
import tomllib
import zipfile


def _能重配(流) -> bool:
    """★ 只有当这个流**真的是可重配的文本流**时才动它。

    ★★★ 真缺陷（2026-11-16，判一百九十跑到时才暴露）：
      原来这里是无条件的 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
      —— **模块顶级的副作用**。而判一百九十在测试里 `import 打wheel`，
      测试框架把 `sys.stdout` 换成了 `io.StringIO` ⇒
        `AttributeError: '_io.StringIO' object has no attribute 'reconfigure'` ⇒
      **判据红**，而它红的原因跟「交付包完整性」毫无关系 ✗
      ⇒ 口径 387：**库模块不许在 import 时改全局状态** ——
        要改也得(a)判断对象支持不支持、(b)失败了也不许抛。
        ★ 这正是「判据的判据」那一族：**判据的失败信息必须指向被测物**，
          指向自己就会把人带偏（我第一眼以为是判 190 的代码有问题）。
    """
    f = getattr(流, "reconfigure", None)
    if not callable(f):
        return False
    try:
        f(encoding="utf-8", errors="replace")
        return True
    except Exception:                                   # noqa: BLE001
        return False


_能重配(sys.stdout)

包名 = "agentscope_orchestrator"
子树 = f"多智能体编排/{包名}"


def _行(节: bytes) -> str:
    """RECORD 的哈希格：`sha256=<urlsafe-b64 去掉 padding>`（PEP 427）。

    ★★ 只收**内容**，摘要它自己算 —— 第一版把签名写成双关（`str` 或 `bytes`），
      调用方一定传错，而且**我这边看起来完全正常**（只检查了「有 sha256= 前缀」）。
      ⇒ 口径 381：**参数不许双关**。★ 这个错是 pip 抓出来的，不是我自己。
    """
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(节).digest()).decode("ascii").rstrip("=")


def 从git(仓库: pathlib.Path, 出: pathlib.Path, 打印=print, 树="HEAD") -> pathlib.Path:
    """**从 git 的 tree-ish 打包**（推荐）：内容由 git 决定 ⇒ 可审计、不丢文件。

    ★ 关键：`git archive` 的字节流**直接喂给 `tarfile`**（内存里），
      **不用 `tar -xf` 落盘** —— 实测落盘会丢中文名文件（见模块文档 ③）。
    """
    仓库 = pathlib.Path(仓库).resolve()
    # ★ R539：`<tree-ish>:<path>` 的 path 是**相对 cwd** 解析的，而 `子树` 是
    #   **仓根相对**的 ⇒ 必须站在 git 的顶层跑，否则前缀被叠两次。
    #   ★ 而且 git 对"`tree:path` 不存在"**不报错、给空归档** ⇒ 静默出空包。
    _顶 = subprocess.run(["git", "-C", str(仓库), "rev-parse", "--show-toplevel"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace")
    if _顶.returncode == 0 and _顶.stdout.strip():
        仓库 = pathlib.Path(_顶.stdout.strip())
    # ★ R571：把 `树` 钉成**确定的 SHA**。`archive` 与下面的 `ls-tree` 是两次独立解析，
    #   中间若有提交落盘，两次看到的 HEAD 就不是同一棵 ⇒ 自查会报「归档丢了 N 个文件」，
    #   而其实一个都没丢（假红）。钉住 SHA 之后，两条问题问的是**同一棵树**。
    _钉 = subprocess.run(["git", "-C", str(仓库), "rev-parse", 树],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if _钉.returncode == 0 and _钉.stdout.strip():
        树 = _钉.stdout.strip()
    进 = subprocess.run(["git", "-C", str(仓库), "archive", "--format=tar", f"{树}:{子树}"],
                       capture_output=True)
    assert 进.returncode == 0, f"git archive 失败：{进.stderr[:300]!r}"
    assert 进.stdout, "git archive 返回空"
    件: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(进.stdout)) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            f = tf.extractfile(m)
            if f is not None:
                件[m.name.replace("\\", "/")] = f.read()
    # ★ 自带判据：**git 里有多少 .py，包里就得有多少 .py**（口径 375：能被机器对）
    进2 = subprocess.run(["git", "-C", str(仓库), "ls-tree", "-r", "--name-only", f"{树}:{子树}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    git件 = {x.strip() for x in 进2.stdout.splitlines() if x.strip()}
    # ★ R539：**空集不许当判据**（口径 375）。`git件` 为空说明上面那条
    #   `archive` 的路径根本没命中 —— 那时 `缺 = ∅` 会让自查**空转通过**，
    #   印出「0 文件 ⇒ 0 文件 · 一个不少 ✓」这种全绿废话，而包里其实没有源码。
    assert git件, (f"★ 自查取到 0 个文件（`{树}:{子树}` 在 git 里根本没命中）"
                  f"⇒ 归档必然也是空的 ⇒ **拒发**："
                  f"守恒等式两边都是空集时它不构成判据（口径 375）")
    缺 = sorted(git件 - set(件))
    assert not 缺, (f"★ 归档丢了 {len(缺)} 个文件（git 里有、包里没有）：{缺[:6]}；"
                  f"★ 这多半就是「中文名在某个工具边界被丢掉」——不许静默少文件（口径 92）")
    打印(f"★ 归档自查：git {len(git件)} 文件 ⇒ 包 {len(件)} 文件 · **一个不少** ✓")
    return _组装(件, 出, 打印)


def 从目录(源树: pathlib.Path, 出: pathlib.Path, 打印=print) -> pathlib.Path:
    """从**工作区目录**打包（开发时用；★ 内容由工作区决定，不如从 git 可审计）。"""
    源树 = pathlib.Path(源树).resolve()
    件: dict[str, bytes] = {}
    for f in sorted((源树 / "src" / 包名).rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            件[f"src/{包名}/{f.relative_to(源树 / 'src' / 包名).as_posix()}"] = f.read_bytes()
    pp = 源树 / "pyproject.toml"
    件["pyproject.toml"] = pp.read_bytes()
    return _组装(件, 出, 打印)


def _组装(件: dict, 出: pathlib.Path, 打印=print) -> pathlib.Path:
    出 = pathlib.Path(出).resolve()
    pp = tomllib.loads(件["pyproject.toml"].decode("utf-8"))
    项目 = pp["project"]
    名 = 项目["name"].replace("-", "_")
    版本 = 项目["version"]
    dist = f"{名}-{版本}.dist-info"
    # 包目录前缀可能是 `src/agentscope_orchestrator/` 或直接 `agentscope_orchestrator/`
    队 = []
    for 路, 节 in 件.items():
        p = 路.replace("\\", "/")
        if p.startswith("src/"):
            p = p[4:]
        if p.startswith(f"{包名}/"):
            队.append((p, 节))
    assert 队, "没找到包本体（`src/agentscope_orchestrator/` 或 `agentscope_orchestrator/`）"
    出.mkdir(parents=True, exist_ok=True)
    轮 = 出 / f"{名}-{版本}-py3-none-any.whl"

    meta = ["Metadata-Version: 2.1", f"Name: {项目['name']}", f"Version: {版本}"]
    if 项目.get("description"):
        meta.append(f"Summary: {项目['description']}")
    if 项目.get("requires-python"):
        meta.append(f"Requires-Python: {项目['requires-python']}")
    for d in 项目.get("dependencies", []):
        meta.append(f"Requires-Dist: {d}")
    # ★ R548：**extras 必须进 METADATA**（verifier 逐行读 zip 里的 METADATA 抓出）。
    #   此前只有 `Requires-Dist`，没有 `Provides-Extra:` 与 `; extra == "…"`，
    #   ⇒ 用户照提示敲 `pip install agentscope_orchestrator[parsers]`
    #     **一个 grammar 都装不到**，而那句提示看上去是「有解」。
    for _名, _项 in (项目.get("optional-dependencies") or {}).items():
        meta.append(f"Provides-Extra: {_名}")
        for _d in _项:
            meta.append(f'Requires-Dist: {_d}; extra == "{_名}"')
    脚 = pp.get("project", {}).get("scripts", {})
    assert 脚, "pyproject 里没有 [project.scripts] ⇒ 装完没有命令行入口"

    队.append((f"{dist}/METADATA", ("\n".join(meta) + "\n").encode("utf-8")))
    队.append((f"{dist}/WHEEL", ("Wheel-Version: 1.0\nGenerator: 太虚元道/打wheel.py\n"
                             "Root-Is-Purelib: true\nTag: py3-none-any\n").encode("utf-8")))
    队.append((f"{dist}/entry_points.txt",
             ("\n".join(["[console_scripts]"] + [f"{k} = {v}" for k, v in sorted(脚.items())]) + "\n").encode("utf-8")))
    队.append((f"{dist}/top_level.txt", f"{包名}\n".encode()))

    记 = io.StringIO()
    w = csv.writer(记, lineterminator="\n")
    for 路, 节 in 队:
        w.writerow([路, _行(节), len(节)])
    w.writerow([f"{dist}/RECORD", "", ""])            # ★ RECORD 自己不写哈希
    队.append((f"{dist}/RECORD", 记.getvalue().encode("utf-8")))

    with zipfile.ZipFile(轮, "w", zipfile.ZIP_DEFLATED) as z:
        for 路, 节 in 队:
            z.writestr(路, 节)
    py = sum(1 for p, _ in 队 if p.endswith(".py"))
    打印(f"★ 打 wheel：{轮.name} · {轮.stat().st_size/1024:.0f}KB · "
       f"{len(队)} 项（.py {py}）· 入口 {len(脚)} 个 · **没用 setuptools/wheel/网络**")
    return 轮


def main() -> int:
    参数 = sys.argv[1:]
    if "--源树" in 参数:
        源 = pathlib.Path(参数[参数.index("--源树") + 1])
        出 = pathlib.Path(参数[参数.index("--出") + 1]) if "--出" in 参数 else 源 / "work" / "_新dist"
        return 0 if 从目录(源, 出) else 1
    仓 = pathlib.Path(参数[0]) if 参数 else pathlib.Path(__file__).resolve().parents[2]
    出 = pathlib.Path(参数[1]) if len(参数) > 1 else pathlib.Path(__file__).resolve().parents[1] / "work" / "_新dist"
    从git(仓, 出)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
