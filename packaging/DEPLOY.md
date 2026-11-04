# 编排器 · 本机部署说明

这是**独立运行目录**（不是仓库），由 wheel 装出来，供 DSH 智能体当 MCP 工具调用。

## 装在哪、怎么起

| 项 | 值 |
|---|---|
| 运行时 | `E:\基础设施\orch-deploy\.venv` |
| 启动 | `.venv\Scripts\python.exe -m agentscope_orchestrator.taixu_mcp`（stdio） |
| 项目档案 | `E:/基础设施/离散时空认知物理基础研究/太虚元道/orchestrator.profile.yml` |
| 接入点 | `C:\Users\00\.dsh\profiles\web\cordis.patch.yml` 的 `mcp-taixu` 行 |

## 为什么在这儿

- **不放仓库里**：仓库要被引擎源码索引扫描，455 个依赖包进去会把全库型判据门搞乱；
- **不用 dist 目录**：dist 是构建产物，重建时会连同 `.venv` 一起被清掉；
- **不指向源码树**：源码改一次就变，部署应当指向装好的包。

## 手工自检

```powershell
cd E:\基础设施\orch-deploy
$env:ORCH_PROFILE='E:/基础设施/离散时空认知物理基础研究/太虚元道/orchestrator.profile.yml'
$env:TMPDIR='E:/基础设施/离散时空认知物理基础研究/太虚元道/.tmp'
$env:TEMP=$env:TMPDIR; $env:TMP=$env:TMPDIR
.\.venv\Scripts\python.exe -m agentscope_orchestrator.doctor
```

必须看到 `✓ 可以开工`。若报 `[gates] 0 条 ⇒ 没判据门`，就是忘了设 `ORCH_PROFILE`
—— 编排器是通用工具，没有项目档案就不知道「什么叫成」，它会拒绝开跑而不是假装能干。

## 检索桥（外挂知识库）怎么接 —— B 方案：一处维护、多处引用

2026-11-03 定：知识库留在**源仓**（`太虚元道\.semsearch_index\concepts.json`，389 条），
部署机只配"去哪读"。改一次规则，所有引用它的部署立刻吃到 —— 这就是"外挂知识随时查随时改"。

**做法**：在部署目录建 `semsearch_link.json`（本文件属**这台机器**，不进产品源码、
也不塞进 `orchestrator.profile.yml` —— 那是项目的，不是部署的）：

```json
{
  "bin": "E:/基础设施/离散时空认知物理基础研究/太虚元道/semsearch/bin/semsearch.exe",
  "project_root": "E:/基础设施/离散时空认知物理基础研究/太虚元道"
}
```

可配的键：`bin`（二进制）· `project_root`（知识库所在项目根）· `index`（索引文件）·
`schema`（口径文件，换知识库才要）· `library_mode`。

**优先级**（谁更具体谁优先）：环境变量 `SEMSEARCH_*`（当次调用）> 本配置 > 自动探测。

**为什么 `bin` 要显式写**：本机 PATH 里有一个**旧版** `semsearch/.semsearch_bin/semsearch.EXE`，
它没有通用形态 —— 会去写 `E:\基础设施\.audit_trail\` 然后 rc=1「拒绝访问」。
不显式指定就会出现"配置写了新二进制、实际跑的是旧的"，极难排查。

**验一下**（两条，都要绿）：

```powershell
.\.venv\Scripts\python.exe -m agentscope_orchestrator.doctor   # 应有 ✓ [semsearch] … 索引 N 条
.\.venv\Scripts\python.exe check_semsearch.py                  # 应末行 ✓ 检索桥可用
```

`doctor` 从 2026-11-03 起**会验检索桥**。在那之前它只验编排器自己，于是
"部署完了"和"检索其实没接上"看起来一模一样 —— 后者静默降级成"没有外挂知识"，
编排器不报错、只是修不动时少一份参考。没二进制只出 `·`（不阻塞开工），
二进制在但索引读不到会出原因和三种常见处置。

---

## 三条务必知道的环境陷阱（都真踩过）

1. `TMPDIR`/`TEMP`/`TMP` 或 `PYTHONPYCACHEPREFIX` 指向无空间的盘 ⇒ 编译门报
   「磁盘空间不足」，看起来像代码坏了。`doctor` 的 `[pointers]` 专查这个。
2. `install.ps1` 若存成 UTF-8 **无 BOM**，`powershell.exe`(5.1) 会 ParserError 一跑就死。
3. 通道 key 不必写进配置：编排器自己会读 `C:\Users\00\.dsh\.credentials.yaml`。

## 回滚

```powershell
# 配置：恢复备份
copy C:\Users\00\.dsh\profiles\web\cordis.patch.yml.bak-* C:\Users\00\.dsh\profiles\web\cordis.patch.yml
```

---

## Windows 10 专属注意事项（实测于 Win10 22H2 / 19045）

本机实测值：`powershell.exe` **5.1.19041**、`pwsh` 7.4.22、活动代码页 **936(GBK)**、
`LongPathsEnabled=1`、部署目录最长路径 **139 字符**。

| 坑 | 现象 | 处置 |
|---|---|---|
| `.ps1` 无 BOM | `powershell.exe` 5.1 按 ANSI 解释 UTF-8，中文变乱码 ⇒ **ParserError「意外的标记 }」**，安装脚本 1 秒即死 | 分发到包里的 `.ps1` **一律带 BOM**（`make_dist` 已固化） |
| 控制台代码页 936 | 不带 `PYTHONIOENCODING` 时，**重定向输出**按 GBK 编码 ⇒ 中文乱码，甚至 `UnicodeEncodeError` 崩溃 | 程序自保：`_winutf8.确保UTF8输出()` 已在 doctor / taixu_mcp / generic_goal 入口调用，**无需用户设任何环境变量** |
| 执行策略 | 直接双击 `.ps1` 被拦 | 用 `powershell -ExecutionPolicy Bypass -File .\install.ps1` |
| 长路径 260 | 本机已开 `LongPathsEnabled=1`，且最长路径 139 字符 ⇒ 当前无风险；若把部署目录移到更深的中文路径下需重新确认 | — |

**验证记录（对照实验）**：子进程清空 `PYTHONIOENCODING/PYTHONUTF8`，在 GBK 控制台下把 doctor 输出重定向到文件：
- 修前：`exit=1`，`UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'`
- 修后：`exit=0`，日志为合法 UTF-8，**212 个中文字符**可读，`✓ 可以开工`

★ 另注：PowerShell 的 `>` 默认写 **UTF-16LE**，所以「用 `>` 重定向得到的日志不是 UTF-8」不是程序的问题；
  要原始字节请用 `Start-Process -RedirectStandardOutput` 或 `cmd /c ... >`。

### Windows 10 生命周期

Windows 10 的官方支持已于 **2025-10-14** 结束。自用无妨，但若要长期跑无人值守任务，
建议迁到仍在支持期的系统，或至少保持离线/隔离环境。
