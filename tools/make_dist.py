# -*- coding: utf-8 -*-
"""多智能体编排器 · 一键部署包构建（**纯标准库，不依赖构建后端/网络**）。

生成 `dist/orchestrator-<版本>/`：install.ps1 / install.sh / QUICKSTART.md /
示例档案 / wheels/*.whl（手写 wheel，离线可装）/ VERSION.txt / SHA256SUMS

★ 为什么手写 wheel 而不用 `pip wheel`：
  真证据（2026-10-29）——`pip wheel` 需临时建隔离环境装 setuptools，
  而 TEMP 落在一个已满的盘上 ⇒ `OSError: [Errno 28] No space left on device`；
  去掉隔离又 `BackendUnavailable: Cannot import 'setuptools.build_meta'`。
  ⇒ **交付包的构建不该依赖"目标机恰好有构建后端、恰好有空间"**。
  wheel = zip + 元数据文件，用标准库写出来**在任何机器上都能构建**。
"""
from __future__ import annotations

import base64
import hashlib
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path

根 = Path(__file__).resolve().parents[1]
dist = 根 / "dist"
包名 = "agentscope_orchestrator"

QUICKSTART = """# 三分钟上手

## 一、装（**不联网也能装**：`wheels/` 里带全部依赖轮子）

Windows：`powershell -ExecutionPolicy Bypass -File .\\install.ps1`
Linux/macOS：`bash install.sh`

装完脚本会跑 `orch-doctor`。看到 `✓ 可以开工` 就继续。

两个脚本都用 `pip install --no-index --find-links wheels/` 装 —— **只认本地轮子、全程不联网**；
万一离线装失败（比如平台轮子不匹配），错误信息里会直接给"联网补装依赖"的修法。

## 二、让它认识你的项目

把 `orchestrator.profile.example.yml` 复制到项目根，改名 `orchestrator.profile.yml`，改四处：

| 字段 | 填什么 |
|---|---|
| `repo_root` | 仓库根（相对档案文件或绝对路径） |
| `work_dir` | 编排器工作目录（放大文件，别放仓库里） |
| `gates` | 判据门：怎么算"改对了"，如 `[cargo, test, --lib, --no-run]` / `[pytest, -q]` |
| `anchors.main` | 源码根的点分键（模型改哪些地方） |

判据门写得好不好的**唯一标准**：门绿了，是不是真的等于"目标达成了"。

## 三、接你自己的模型 API（无需改代码）

```powershell
# ① 任意 OpenAI 兼容服务
$env:ORCH_BASE_URL = "https://your-endpoint/v1"
$env:ORCH_API_KEY  = "sk-..."
$env:ORCH_MODEL    = "your-model"

# ② 常见提供方：只要 key
$env:OPENAI_API_KEY = "sk-..."        # 或 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / …

# ③ 任何别的提供方：前缀三件套
$env:MYSERVICE_API_KEY="sk-..."; $env:MYSERVICE_BASE_URL="https://api.x.com/v1"; $env:MYSERVICE_MODEL="m"
```

★ 一条通道都拿不到 key 时，编排器报**基础设施不可用**（退出码 3），
不会假装是"任务没做到"。

## 四、跑

```powershell
orch-goal --goal "让 cargo test --lib --no-run 退出码 0" --max-work 4
orch-plan --goal "…"      # 只看它打算怎么干
orch-mcp                  # 当插件给别的智能体用
```

## 五、跑完怎么核

```powershell
orch-doctor     # 环境还健康吗
orch-log        # 历次运行的账
```

**永远自己再跑一遍判据命令**：日志说成了不算，`cargo` / `pytest` 说成了才算。
"""


def 元数据() -> dict:
    with (根 / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]


def _记录(路径: str, 数据: bytes) -> str:
    h = base64.urlsafe_b64encode(hashlib.sha256(数据).digest()).rstrip(b"=").decode()
    return f"{路径},sha256={h},{len(数据)}"


def 写轮子() -> Path:
    md = 元数据()
    名, 版 = md["name"], md["version"]
    distinfo = f"{包名}-{版}.dist-info"
    轮 = dist / "wheels" / f"{包名}-{版}-py3-none-any.whl"
    轮.parent.mkdir(parents=True, exist_ok=True)

    METADATA = ["Metadata-Version: 2.1", f"Name: {名}", f"Version: {版}"]
    if md.get("description"):
        METADATA.append("Summary: " + md["description"].strip().splitlines()[0])
    METADATA.append("Requires-Python: " + md.get("requires-python", ">=3.12"))
    METADATA += [f"Requires-Dist: {d}" for d in (md.get("dependencies") or [])]
    # ★ R548：extras 进 METADATA（与 打wheel.py 同源同修）。
    for _名, _项 in (md.get("optional-dependencies") or {}).items():
        METADATA.append(f"Provides-Extra: {_名}")
        for _d in _项:
            METADATA.append(f'Requires-Dist: {_d}; extra == "{_名}"')
    METADATA.append("")

    WHEEL = ["Wheel-Version: 1.0", "Generator: orch-make-dist (stdlib)",
             "Root-Is-Purelib: true", "Tag: py3-none-any", ""]
    EP = ["[console_scripts]"] + [f"{k} = {v}" for k, v in (md.get("scripts") or {}).items()] + [""]

    记录: list[str] = []
    源 = 根 / "src" / 包名
    with zipfile.ZipFile(轮, "w", zipfile.ZIP_DEFLATED) as z:
        # ★ 仓库根的 config/*.yml 要随包进 `包/config/`：
        #   安装后 `_找配置目录()` 第 ③ 路就是「包内 config/」，
        #   不打包进去 ⇒ 装完的机器上永远没有默认配置（真事故 2026-10-30）。
        随包 = []
        _仓config = 根 / "config"
        if _仓config.is_dir():
            随包 = sorted(x for x in _仓config.glob("*.yml") if x.is_file())
            for x in 随包:
                arc = f"{包名}/config/{x.name}"
                数据 = x.read_bytes()
                z.writestr(arc, 数据)
                记录.append(_记录(arc, 数据))
        # ★★ 2026-10-31（交付审计）：这里原来是**黑名单**（只排 `__pycache__`），
        #   于是任何落在 src 下的杂物都会被静默打进 wheel。实机已发生：
        #   一次诊断命令在 src 里留下了 66 个 `*.py.tmpc`，重打包后
        #   **wheel 从 71 个文件涨到 138 个**（体积 1055 KB → 2474 KB），
        #   而自证照样打绿 —— 因为自证只验"入口点齐全 + 关键模块在包内"，
        #   不验"包里有没有不该有的东西"。
        #   ⇒ 改成**白名单**：只有这几种后缀允许进包；同时显式拦掉
        #     `__pycache__` 与任何以点结尾的临时产物。
        #     黑名单永远漏，白名单才守得住 —— 打包本来就该是"我确认过的才进"。
        _允许后缀 = {".py", ".yml", ".yaml", ".json", ".md", ".txt"}
        _拒收: list[str] = []
        for f in sorted(源.rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            if f.suffix.lower() not in _允许后缀:
                _拒收.append(f.relative_to(源).as_posix())
                continue
            arc = f"{包名}/{f.relative_to(源).as_posix()}"
            数据 = f.read_bytes()
            z.writestr(arc, 数据)
            记录.append(_记录(arc, 数据))
        if _拒收:
            print(f"  ⚠ 已拦下 {len(_拒收)} 个非源码文件（不在白名单内）："
                  f"{_拒收[:5]}{' …' if len(_拒收) > 5 else ''}")
        # ★★ 2026-10-31（交付审计）：**tests 必须随包**。
        #   文档《交付说明》第 5 节写着"换任何一台机器跑这几条自证"，
        #   其中 `tests/state_paths.py`、`tests/run_all.py` 是安装后**唯一**能验
        #   "状态与代码分离"和"自有判据门"的东西。实测：这几条此前根本没进 wheel
        #   ⇒ 照着文档在装完的机器上敲，得到的是
        #   `can't open file …\site-packages\agentscope_orchestrator\tests\state_paths.py`
        #   ⇒ 文档承诺的自证路径在安装布局下**不可执行**。
        #   装进 `包/tests/`：不参与导入，只在人手/CI 主动叫用时运行。
        _测试 = 根 / "tests"
        if _测试.is_dir():
            for f in sorted(_测试.rglob("*")):
                if not f.is_file() or "__pycache__" in f.parts:
                    continue
                if f.suffix.lower() not in _允许后缀:
                    continue
                arc = f"{包名}/tests/{f.relative_to(_测试).as_posix()}"
                数据 = f.read_bytes()
                z.writestr(arc, 数据)
                记录.append(_记录(arc, 数据))
        for 路径, 文本 in ((f"{distinfo}/METADATA", "\n".join(METADATA)),
                           (f"{distinfo}/WHEEL", "\n".join(WHEEL)),
                           (f"{distinfo}/entry_points.txt", "\n".join(EP))):
            数据 = 文本.encode("utf-8")
            z.writestr(路径, 数据)
            记录.append(_记录(路径, 数据))
        z.writestr(f"{distinfo}/RECORD", "\n".join(记录 + [f"{distinfo}/RECORD,,"]) + "\n")
    return 轮


def 组装(轮: Path) -> Path:
    包 = dist / f"orchestrator-{元数据()['version']}"
    if 包.exists():
        shutil.rmtree(包)
    (包 / "wheels").mkdir(parents=True)
    shutil.copy2(轮, 包 / "wheels" / 轮.name)
    # ★ 2026-11-19 实测：install.ps1 承诺「从 wheels/ 离线装（不联网也能装）」，
    #   但 wheels/ 里只有编排器自己的轮子 ⇒ 干净机器上"离线装"必然装出 doctor 退出 64（缺 pyyaml/httpx/mcp）。
    #   ⇒ 把**已解析好的依赖轮子**一起打包（packaging/deps_wheels/，Windows cp312）。
    依赖目录 = 根 / "packaging" / "deps_wheels"
    if 依赖目录.is_dir():
        依赖轮们 = sorted(依赖目录.glob("*.whl"))
        for _w in 依赖轮们:
            shutil.copy2(_w, 包 / "wheels" / _w.name)
        print(f"  + 依赖轮子 {len(依赖轮们)} 个（离线装用）")
    else:
        print("  ! 没有 packaging/deps_wheels/ ⇒ 离线装会缺依赖（doctor 退出 64）")
    for 源, 名 in ((根 / "packaging" / "install.ps1", "install.ps1"),
                   (根 / "packaging" / "install.sh", "install.sh"),
                   (根 / "orchestrator.profile.example.yml", "orchestrator.profile.example.yml"),
                   (根 / "config" / "channels.yml", "channels.example.yml")):
        if 源.exists():
            shutil.copy2(源, 包 / 名)
            if 名.endswith(".ps1"):
                # ★★★★★ 真事故（2026-10-30）：install.ps1 存成 UTF-8 **无 BOM**，
                #   而 `powershell.exe`(5.1) 按 ANSI 读 .ps1 ⇒ 中文变乱码 ⇒
                #   **ParserError「意外的标记 }」**，安装脚本一跑就 exit 1。
                #   我此前从未真跑过它，所以这个错一直没暴露。
                #   ⇒ 分发到包里的 .ps1 一律**带 BOM**，两种 PowerShell 都能读。
                (包 / 名).write_bytes(
                    b"\xef\xbb\xbf" + (包 / 名).read_bytes().lstrip(b"\xef\xbb\xbf"))
        else:
            print(f"  ! 缺 {源}（跳过）")
    (包 / "QUICKSTART.md").write_text(QUICKSTART, encoding="utf-8")
    (包 / "VERSION.txt").write_text(元数据()["version"] + "\n", encoding="utf-8")
    行 = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(包).as_posix()}"
          for f in sorted(包.rglob("*")) if f.is_file() and f.name != "SHA256SUMS"]
    (包 / "SHA256SUMS").write_text("\n".join(行) + "\n", encoding="utf-8")
    return 包


def 校验(轮: Path) -> None:
    with zipfile.ZipFile(轮) as z:
        名 = set(z.namelist())
        ep = next(n for n in 名 if n.endswith("entry_points.txt"))
        文本 = z.read(ep).decode()
    缺 = [x for x in ("orch-goal", "orch-check", "orch-mcp") if x not in 文本]
    if 缺:
        raise SystemExit(f"✗ wheel 缺入口点：{缺}")
    缺2 = [m for m in (f"{包名}/doctor.py", f"{包名}/channels_auto.py") if m not in 名]
    if 缺2:
        raise SystemExit(f"✗ wheel 缺模块：{缺2}")
    # ★★ 2026-10-31（交付审计）：自证只会"缺了就报"，**多了不报**。
    #   实机因此漏过一次：src 里混进 66 个 `*.py.tmpc`，wheel 从 71 涨到 138 个文件、
    #   1055 KB → 2474 KB，而这里照样打绿（它只验入口点与关键模块）。
    #   ⇒ 补一条**只许多了不该有的就红**：包内每个文件的后缀必须在白名单内，
    #     且路径不得含 `__pycache__`。这样"打包打脏了"会当场失败，不靠人眼看。
    _允许 = {".py", ".yml", ".yaml", ".json", ".md", ".txt"}
    _脏 = [n for n in 名
           if not n.endswith("/RECORD") and not n.endswith(".dist-info/METADATA")
           and not n.endswith(".dist-info/WHEEL")
           and not n.endswith(".dist-info/entry_points.txt")
           and ("__pycache__" in n or Path(n).suffix.lower() not in _允许)]
    if _脏:
        raise SystemExit(f"✗ wheel 里混进了非源码文件 {len(_脏)} 个：{_脏[:6]}")
    print(f"✓ wheel 自证：{len(名)} 个文件、入口点齐全、doctor/channels_auto 在包内、"
          f"无杂物")


def main() -> int:
    # ★★★★★ 2026-11-03 真事故（症状是"打包看起来成功、其实 dist/ 还是上一版"）：
    #   本脚本在**非 UTF-8 控制台**下跑不完。现场：`校验()` 末尾那句
    #   `✓ wheel 自证：…` 里的 `✓`(U+2713) 在 GBK 控制台抛
    #   `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'` ⇒ 退出码 1。
    #   而 `main()` 的顺序是 `写轮子() → 校验() → 组装()`：
    #   崩在**校验**里 ⇒ **`组装()` 根本没跑** ⇒ `dist/orchestrator-*/` 留的是旧包；
    #   但 wheel 已经写出来、屏幕上还有一大片看起来正常的清单
    #   ⇒ 表现完全像"打包成功"。最后是 `wheel_bytes` 判据抓出来的
    #     （`DIFF agentscope_orchestrator/tests/run_all.py`：源码改了，包没跟上）。
    #
    #   口径：**工具自己不许依赖调用方的控制台编码**（与"判据起子进程必须钉编码"是同一条
    #   规矩，只是这次受罚的是工具自己的 print），且必须 `errors="replace"` ——
    #   "打不出某个字符"绝不该变成"这一步没做完"。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except Exception:                                          # noqa: BLE001
            pass
    轮 = 写轮子()
    校验(轮)
    包 = 组装(轮)
    print(f"✓ 交付包：{包}")
    for f in sorted(包.rglob("*")):
        if f.is_file():
            print(f"   {f.relative_to(包).as_posix():44s} {f.stat().st_size // 1024:>6d} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
