$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectRoot 'data\admin.pid'
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'Локальная админка не запущена через start-admin.ps1.'
    exit 0
}
$adminId = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $adminId"
if ($process -and $process.CommandLine -like '*-m app.local_admin*') {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $adminId"
    foreach ($child in $children) {
        if ($child.CommandLine -like '*-m app.local_admin*') {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    Stop-Process -Id $adminId -Force
    Write-Output 'Локальная админка остановлена.'
} else {
    Write-Output 'Процесс из PID-файла уже завершён или не совпадает с админкой.'
}
Remove-Item -LiteralPath $pidFile -Force
