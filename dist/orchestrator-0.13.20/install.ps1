# 多智能体编排器 · Windows 一键安装
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# 做的事（每一步都可解释，失败就停）：
#   ① 找一个 Python ≥3.12
#   ② 在本目录下建 .venv（不污染系统 Python）
#   ③ 从 wheels/ 里离线装（不联网也能装）
#   ④ 写/检查检索桥配置 semsearch_link.json（B 方案：知识库一处维护、多处引用）
#   ⑤ 跑 orch-doctor 环境自检
#   ⑥ 把激活命令和常用命令打在最后
#
# ★ 真证据（2026-10-30）：powershell.exe(5.1) 默认代码页把中文输出成乱码，
#   用户会以为装坏了。开头就把控制台设成 UTF-8。
try { chcp 65001 > $null } catch { }
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "═══ 多智能体编排器 安装 ═══" -ForegroundColor Cyan

# ① Python
$py = $null
foreach ($c in @("python", "python3", "py")) {
    $p = Get-Command $c -ErrorAction SilentlyContinue
    if ($p) {
        $v = & $p.Source -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        if ($v -and ([version]$v -ge [version]"3.12")) { $py = $p.Source; break }
    }
}
if (-not $py) {
    Write-Host "✗ 找不到 Python ≥3.12。装一个再来：https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Python: $py ($(& $py -c 'import sys;print(sys.version.split()[0])'))"

# ② venv
$venv = Join-Path $here ".venv"
if (-not (Test-Path $venv)) {
    Write-Host "· 建虚拟环境 .venv"
    & $py -m venv $venv
} else {
    Write-Host "· 复用已有 .venv"
}
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) { Write-Host "✗ venv 建失败" -ForegroundColor Red; exit 1 }

# ③ 离线装
#
# ★ 别用 `Sort-Object Name | Select-Object -Last 1` 选 wheel（2026-11-03 实测踩过）：
#   按名字排序时 "0.9.2" 排在 "0.13.11" 之后 ⇒ 装的是**两个月前那个旧版**，
#   而输出只说"安装 agentscope_orchestrator-0.9.2"，不细看根本发现不了。
#   这里按**版本号**排序取最高，并且把选中的版本打出来。
$whls = @(Get-ChildItem (Join-Path $here "wheels") -Filter "*.whl")
if (-not $whls) { Write-Host "✗ wheels/ 里没有 wheel" -ForegroundColor Red; exit 1 }
# ★ 必须按**名字前缀**筛出编排器自己的轮子（2026-11-19 实测踩过）：
#   一旦 wheels/ 里放进依赖轮子（如 pydantic_core-2.50.4），"按版本号取最高"会挑中**依赖包**，
#   于是"安装了成功"但装的是别的东西 —— 和 2026-11-03 那次"按名字排序装了旧版"是同一类错。
$own = @($whls | Where-Object { $_.Name -like "agentscope_orchestrator-*" })
if (-not $own) { Write-Host "✗ wheels/ 里没有 agentscope_orchestrator-*.whl" -ForegroundColor Red; exit 1 }
$whl = $own | Sort-Object { try { [version](($_.BaseName -split '-')[-3]) } catch { [version]"0.0.0" } } | Select-Object -Last 1
Write-Host "· 安装 $($whl.Name)（wheels/ 共 $($whls.Count) 个：编排器 $($own.Count) + 依赖轮子；取编排器版本最高）"

# ★ pip 一律不碰缓存：有机器把 PIP_CACHE_DIR 配在写不进去/已断开的盘上，pip 会**静默卡死**
#   （2026-11-19 本机实测：`pip install --upgrade pip -q` 卡 >10 分钟；加 --no-cache-dir 后 3 秒完）。
#   install.sh 早就修过这行，Windows 侧此前没同步。
& $vpy -m pip install --no-cache-dir --disable-pip-version-check --upgrade pip -q 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "· pip 自升级没成功（不影响安装，继续）" -ForegroundColor Yellow }

# ★ 依赖必须一起装，而且**只从本地 wheels/ 解析**（--no-index --find-links）⇒ 这才是真离线装。
#   旧写法 `--no-deps` 在干净机器上装出 doctor=64 的坏装（缺 pyyaml/httpx/mcp）。
& $vpy -m pip install --no-cache-dir --disable-pip-version-check --no-index --find-links (Join-Path $here "wheels") --force-reinstall "$($whl.FullName)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ 安装失败（wheels/ 里缺依赖轮子？）" -ForegroundColor Red
    Write-Host "  修法 A：联网补装依赖  .\.venv\Scripts\python.exe -m pip install --no-cache-dir pyyaml httpx `"mcp<2.0.0`" pywin32" -ForegroundColor Yellow
    Write-Host "  修法 B：重新构建交付包（tools/make_dist.py 会把 packaging/deps_wheels/ 一并打进 wheels/）" -ForegroundColor Yellow
    exit 1
}

# ③.5 预编译字节码（★ R547）
#
# ★ 为什么单列一步：verifier 在**干净 venv** 里实测到「第一次 import 15.73s，
#   之后 3.26~5.16s」。那 15.7 秒不是模块顶层建索引（R543 已去掉），
#   是 109 个模块的 .pyc **第一次现编** + 冷盘。
#   而「装完敲第一条命令就要等十几秒」会被直接读成"这东西卡死"。
#   ⇒ 装的最后一步把字节码先编出来，用户第一次敲就是热的。
Write-Host "· 预编译字节码（省掉首次 import 的十几秒）"
$purelib = & $vpy -c "import sysconfig;print(sysconfig.get_paths()['purelib'])"
& $vpy -m compileall -q "$purelib\agentscope_orchestrator" 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ 字节码已预编译" } else { Write-Host "  · 预编译没成功（不影响可用，只是首次慢）" -ForegroundColor Yellow }


# ④ 检索桥配置（B 方案）
#
# 语义：semsearch 那份知识库留在**源仓**（一处维护），部署机只配"去哪读"。
# 这样改一次规则，所有引用它的部署立刻吃到 —— 这正是"外挂知识随时查随时改"。
# 本文件属**这台机器**：不写进产品源码（判据 phase_separation.py 会红），
# 也不塞进项目档案 orchestrator.profile.yml（那是项目的，不是部署的）。
$cfg = Join-Path $here "semsearch_link.json"
if (Test-Path $cfg) {
    Write-Host "· 已有检索桥配置 semsearch_link.json（不动它）"
    try {
        $j = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($k in @("bin", "project_root", "index", "schema")) {
            $v = $j.$k
            if ($v) {
                $ok = Test-Path $v
                $mark = if ($ok) { "✓" } else { "✗" }
                $color = if ($ok) { "Gray" } else { "Yellow" }
                Write-Host "    $mark $k = $v" -ForegroundColor $color
                if (-not $ok) { Write-Host "      ⇒ 这个路径不存在；检索桥会退回 PATH/自动探测" -ForegroundColor Yellow }
            }
        }
    } catch { Write-Host "    ⚠ 配置读不出来（JSON 坏了？）：$_" -ForegroundColor Yellow }
} else {
    Write-Host "· 没有 semsearch_link.json ⇒ 检索桥会自己往上找知识库（功能正常，但不显式）"
    Write-Host "  想显式指向某份共享知识库，就在本目录建一个："
    Write-Host '  {"bin":"<semsearch.exe>","project_root":"<知识库所在项目根>"}'
}

# ⑤ doctor
Write-Host ""
& $vpy -m agentscope_orchestrator.doctor
$doc = $LASTEXITCODE

# ⑥ 收尾提示
Write-Host ""
Write-Host "═══ 装完了 ═══" -ForegroundColor Cyan
Write-Host "  激活：      .\\.venv\\Scripts\\Activate.ps1"
Write-Host "  自检：      .\\.venv\\Scripts\\orch-doctor.exe"
Write-Host "  跑目标：    .\\.venv\\Scripts\\orch-goal.exe --goal `"<你的目标>`" --max-work 4"
Write-Host "  当插件用：  .\\.venv\\Scripts\\orch-mcp.exe"
Write-Host "  查检索桥：  .\\.venv\\Scripts\\python.exe -c `"import sys;sys.path.insert(0,'.');from agentscope_orchestrator import semsearch_link as S;print(S.可用())`""
Write-Host "  上手文档：  QUICKSTART.md"
if ($doc -ne 0) {
    Write-Host ""
    Write-Host "注意：doctor 报了硬阻塞（见上），先解决再跑任务。" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "下一步：配你自己的模型 API（三条环境变量即可，见 QUICKSTART.md 第三节）" -ForegroundColor Green
}
exit $doc
