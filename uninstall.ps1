#Requires -Version 5.1
<#
.SYNOPSIS
    卸载 feishu-claude bridge
.DESCRIPTION
    注销计划任务，终止正在运行的 bridge 进程。
    不删除 .venv 和 data/（保留数据），如需完全清除请手动删除。
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

Write-Host "====================================" -ForegroundColor Cyan
Write-Host "  feishu-claude bridge uninstall"    -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan

# 1. 注销计划任务
foreach ($name in @("FeishuClaude-Startup", "FeishuClaude-Watchdog")) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[OK] 已注销计划任务: $name" -ForegroundColor Green
}

# 2. 终止正在运行的 bridge 进程
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$killed = 0
Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
        if ($cmd -and $cmd -match "bridge\.py") {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            Write-Host "[OK] 已终止进程 PID $($_.Id)" -ForegroundColor Green
            $killed++
        }
    } catch {}
}
if ($killed -eq 0) {
    Write-Host "[  ] 未发现正在运行的 bridge 进程" -ForegroundColor Gray
}

# 3. 删除锁文件（防止残留阻止下次启动）
$LockFile = Join-Path $ScriptDir "data\bridge.lock"
if (Test-Path $LockFile) {
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] 已删除锁文件" -ForegroundColor Green
}

Write-Host ""
Write-Host "卸载完成。.venv/ 和 data/ 已保留，如需完全清除请手动删除。" -ForegroundColor White
Write-Host ""
