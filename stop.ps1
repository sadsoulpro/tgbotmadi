$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectRoot 'data\bot.pid'
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'Файл PID не найден.'
    exit 0
}
$rootId = [int](Get-Content -LiteralPath $pidFile -Raw)
$root = Get-CimInstance Win32_Process -Filter "ProcessId = $rootId"
if ($root -and $root.CommandLine -like '*-m app.main*') {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $rootId"
    foreach ($child in $children) {
        if ($child.CommandLine -like '*-m app.main*') {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    Stop-Process -Id $rootId -Force -ErrorAction SilentlyContinue
    Write-Output 'Бот остановлен.'
} else {
    Write-Output 'Процесс из PID-файла уже завершён или не совпадает с ботом.'
}
Remove-Item -LiteralPath $pidFile -Force
