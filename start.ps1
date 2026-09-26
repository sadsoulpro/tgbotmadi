$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectRoot 'data\bot.pid'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Не найден .venv. Выполните установку из README.md.'
}
if (Test-Path -LiteralPath $pidFile) {
    $previousId = [int](Get-Content -LiteralPath $pidFile -Raw)
    if (Get-Process -Id $previousId -ErrorAction SilentlyContinue) {
        Write-Output "Бот уже запущен (PID $previousId)."
        exit 0
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'data') | Out-Null
$process = Start-Process -FilePath $python -ArgumentList '-m', 'app.main' -WorkingDirectory $projectRoot -RedirectStandardOutput (Join-Path $projectRoot 'data\bot.stdout.log') -RedirectStandardError (Join-Path $projectRoot 'data\bot.stderr.log') -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id
Write-Output "Бот запущен (PID $($process.Id)). Админка: http://127.0.0.1:8080/admin"
