$ErrorActionPreference = 'Stop'
$roomData = Join-Path $PSScriptRoot 'data'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$supervisorPath = Join-Path $PSScriptRoot 'supervisor.py'
if ((Test-Path -LiteralPath $pythonPath) -and (Test-Path -LiteralPath $supervisorPath)) {
    # A supervised room stops through its supervisor, which waits for any deployment step in progress.
    & $pythonPath -X utf8 $supervisorPath stop
    if ($LASTEXITCODE -eq 0) { exit 0 }
    if ($LASTEXITCODE -ne 3) { throw 'The supervisor did not stop the room; see data\deploy\status.json.' }
}
# Exit code 3: no supervisor is running, so stop an unsupervised server directly.
$recordPath = Join-Path $roomData 'server-process.json'
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Output 'No room process record exists.'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$server = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if (-not $server) { Write-Output 'Room is already closed.'; exit 0 }
if ($server.StartTime.ToUniversalTime().Ticks -ne ([datetime]$record.startUtc).ToUniversalTime().Ticks -or $record.root -ne $PSScriptRoot) {
    throw 'Process identity changed; no process was stopped.'
}
$details = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)"
if ($details.CommandLine -notlike ('*' + (Join-Path $PSScriptRoot 'run.py') + '*')) {
    throw 'The saved process is not this room server; no process was stopped.'
}
$page = Invoke-WebRequest -Uri 'http://127.0.0.1:8300/' -UseBasicParsing -TimeoutSec 5
if ($page.Content -notmatch 'window\.__SESSION_TOKEN__="([^"]+)"') { throw 'Could not identify room session.' }
$headers = @{'X-Session-Token'=$Matches[1]}
Invoke-RestMethod -Uri 'http://127.0.0.1:8300/api/runtime/stop' -Method Post -Headers $headers -TimeoutSec 20 | Out-Null
# Only this launcher-owned process and its children; the existing Bonsai server is independent.
& taskkill.exe /PID $record.pid /T /F | Out-Null
Write-Output 'Room closed. Transcript and worktrees are saved.'
