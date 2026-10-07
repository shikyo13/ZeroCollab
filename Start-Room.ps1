param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$roomUrl = 'http://127.0.0.1:8300'
$roomData = Join-Path $PSScriptRoot 'data'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$supervisorPath = Join-Path $PSScriptRoot 'supervisor.py'
New-Item -ItemType Directory -Path $roomData -Force | Out-Null
try {
    $page = Invoke-WebRequest -Uri $roomUrl -UseBasicParsing -TimeoutSec 2
    if ($page.Content -notmatch 'window\.__SESSION_TOKEN__=') { throw 'Port 8300 belongs to another application.' }
    Write-Output "Room is already running: $roomUrl"
    if (-not $NoBrowser) { Start-Process $roomUrl }
    exit 0
} catch {
    if ($_.Exception.Message -like 'Port 8300*') { throw }
}
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Private Python environment is missing.' }
# The supervisor detaches itself, owns the room server and runs approved deployments.
# Its output goes to files, so this script never waits on an inherited pipe.
& $pythonPath -X utf8 $supervisorPath start
if ($LASTEXITCODE -ne 0) { throw "Room did not start. See data\supervisor.log and data\server-error.log." }
if (-not $NoBrowser) { Start-Process $roomUrl }
