#Requires -Version 5.1
<#
.SYNOPSIS
    feishu-claude bridge 一键初始化脚本

.DESCRIPTION
    1. 检查 Python 3.12+
    2. 创建 .venv 并安装依赖
    3. 创建 data/ 目录
    4. 注册 Windows 计划任务（登录触发 + 每 5 分钟 watchdog）

.NOTES
    运行前请先将 .env.example 复制为 .env 并填入真实值。
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir   = Join-Path $ScriptDir ".venv"
$Python    = $null

Write-Host "====================================" -ForegroundColor Cyan
Write-Host "  feishu-claude bridge setup"        -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan

# ── 1. 检查 Python 3.12+ ────────────────────────────────────

foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate --version 2>&1 | Select-String -Pattern "Python (\d+)\.(\d+)"
        if ($ver) {
            $m = $ver.Matches[0]
            $major = [int]$m.Groups[1].Value
            $minor  = [int]$m.Groups[2].Value
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 12)) {
                $Python = $candidate
                Write-Host "[OK] 找到 Python $major.$minor ($candidate)" -ForegroundColor Green
                break
            }
        }
    } catch {}
}

if (-not $Python) {
    Write-Host "[错误] 需要 Python 3.12+，未找到。请先安装 Python。" -ForegroundColor Red
    exit 1
}

# ── 2. 检查 .env ────────────────────────────────────────────

$EnvFile = Join-Path $ScriptDir ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[警告] 未找到 .env 文件。" -ForegroundColor Yellow
    Write-Host "       请复制 .env.example 为 .env 并填入真实值后重新运行。" -ForegroundColor Yellow
    Copy-Item (Join-Path $ScriptDir ".env.example") $EnvFile
    Write-Host "       已自动复制 .env.example → .env，请编辑后再次运行 setup.ps1。" -ForegroundColor Yellow
    exit 1
}
Write-Host "[OK] .env 文件存在" -ForegroundColor Green

# ── 3. 创建 .venv ───────────────────────────────────────────

if (-not (Test-Path $VenvDir)) {
    Write-Host "[..] 创建虚拟环境..." -ForegroundColor Cyan
    & $Python -m venv $VenvDir
    Write-Host "[OK] .venv 已创建" -ForegroundColor Green
} else {
    Write-Host "[OK] .venv 已存在，跳过创建" -ForegroundColor Green
}

$PythonVenv = Join-Path $VenvDir "Scripts\python.exe"

# ── 4. 安装依赖 ─────────────────────────────────────────────

Write-Host "[..] 安装依赖..." -ForegroundColor Cyan
& $PythonVenv -m pip install --quiet --upgrade pip
& $PythonVenv -m pip install --quiet -r (Join-Path $ScriptDir "requirements.txt")
Write-Host "[OK] 依赖安装完成" -ForegroundColor Green

# ── 5. 创建 data/ 目录 ──────────────────────────────────────

$DataDir = Join-Path $ScriptDir "data"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Write-Host "[OK] data/ 目录已准备" -ForegroundColor Green

# ── 6. 注册计划任务 ─────────────────────────────────────────

$PythonwVenv = Join-Path $VenvDir "Scripts\pythonw.exe"
$TaskSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 3) `
    -RestartCount 0 `
    -MultipleInstances IgnoreNew

# 6a. 登录触发：用 pythonw.exe 启动 bridge（无窗口）
$TaskAction = New-ScheduledTaskAction `
    -Execute $PythonwVenv `
    -Argument "$(Join-Path $ScriptDir 'bridge.py')" `
    -WorkingDirectory $ScriptDir

$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn
Unregister-ScheduledTask -TaskName "FeishuClaude-Startup" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName    "FeishuClaude-Startup" `
    -Action      $TaskAction `
    -Trigger     $TriggerLogon `
    -Settings    $TaskSettings `
    -RunLevel    Limited `
    -Description "feishu-claude bridge - 登录时启动" | Out-Null
Write-Host "[OK] 计划任务 FeishuClaude-Startup 已注册（登录触发）" -ForegroundColor Green

# 6b. Watchdog：每 5 分钟用 pythonw.exe 运行 watchdog.py（完全无窗口）
$WatchdogAction = New-ScheduledTaskAction `
    -Execute $PythonwVenv `
    -Argument "$(Join-Path $ScriptDir 'watchdog.py')" `
    -WorkingDirectory $ScriptDir

$TriggerRepeat = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At (Get-Date)
Unregister-ScheduledTask -TaskName "FeishuClaude-Watchdog" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName    "FeishuClaude-Watchdog" `
    -Action      $WatchdogAction `
    -Trigger     $TriggerRepeat `
    -Settings    $TaskSettings `
    -RunLevel    Limited `
    -Description "feishu-claude bridge - 每 5 分钟保活检查" | Out-Null
Write-Host "[OK] 计划任务 FeishuClaude-Watchdog 已注册（每 5 分钟，pythonw 无窗口）" -ForegroundColor Green

# ── 完成 ────────────────────────────────────────────────────

Write-Host ""
Write-Host "====================================" -ForegroundColor Cyan
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "下次登录时 bridge 将自动启动。" -ForegroundColor White
Write-Host "立即启动：" -ForegroundColor White
Write-Host "  $PythonVenv $(Join-Path $ScriptDir 'bridge.py')" -ForegroundColor DarkGray
Write-Host ""
