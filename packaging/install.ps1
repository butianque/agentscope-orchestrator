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
$whls = Get-ChildItem (Join-Path $here "wheels") -Filter "*.whl"
if (-not $whls) { Write-Host "✗ wheels/ 里没有 wheel" -ForegroundColor Red; exit 1 }
$whl = $whls | Sort-Object { try { [version](($_.BaseName -split '-')[-3]) } catch { [version]"0.0.0" } } | Select-Object -Last 1
Write-Host "· 安装 $($whl.Name)（wheels/ 里共 $($whls.Count) 个，取版本最高）"
& $vpy -m pip install --upgrade pip -q
& $vpy -m pip install --force-reinstall --no-deps --no-cache-dir "$($whl.FullName)"
if ($LASTEXITCODE -ne 0) { Write-Host "✗ 安装失败" -ForegroundColor Red; exit 1 }

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
