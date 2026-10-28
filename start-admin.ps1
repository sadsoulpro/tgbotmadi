$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pidFile = Join-Path $projectRoot 'data\admin.pid'
$port = if ($env:LOCAL_ADMIN_PORT) { [int]$env:LOCAL_ADMIN_PORT } else { 8080 }
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Не найден .venv. Выполните установку из README.md.'
}
if (Test-Path -LiteralPath $pidFile) {
    $previousId = [int](Get-Content -LiteralPath $pidFile -Raw)
    $previous = Get-CimInstance Win32_Process -Filter "ProcessId = $previousId"
    if ($previous -and $previous.CommandLine -like '*-m app.local_admin*') {
        Write-Output "Локальная админка уже запущена (PID $previousId)."
        exit 0
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'data') | Out-Null
$process = Start-Process -FilePath $python -ArgumentList '-m', 'app.local_admin' -WorkingDirectory $projectRoot -RedirectStandardOutput (Join-Path $projectRoot 'data\admin.stdout.log') -RedirectStandardError (Join-Path $projectRoot 'data\admin.stderr.log') -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id
Write-Output "Локальная админка запущена (PID $($process.Id)). Адрес: http://127.0.0.1:$port/admin"
