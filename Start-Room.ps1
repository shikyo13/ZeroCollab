param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$roomUrl = 'http://127.0.0.1:8300'
$roomData = Join-Path $PSScriptRoot 'data'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$supervisorPath = Join-Path $PSScriptRoot 'supervisor.py'
New-Item -ItemType Directory -Path $roomData -Force | Out-Null

# The supervisor opens the browser on a single-use sign-in link; only an older room without a supervisor
# is opened at its bare address (that room still carries its own token in the page).
function Open-Room {
    if ($NoBrowser) { return }
    & $pythonPath -X utf8 $supervisorPath open
    if ($LASTEXITCODE -ne 0) { Start-Process $roomUrl }
}

try {
    $page = Invoke-WebRequest -Uri $roomUrl -UseBasicParsing -TimeoutSec 2
    $isRoom = $page.Headers['X-ZeroCollab-Room'] -or ($page.Content -match 'window\.__SESSION_TOKEN__=')
    if (-not $isRoom) { throw 'Port 8300 belongs to another application.' }
    Write-Output "Room is already running: $roomUrl"
    Open-Room
    exit 0
} catch {
    if ($_.Exception.Message -like 'Port 8300*') { throw }
}
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Private Python environment is missing.' }
# The supervisor detaches itself, owns the room server and runs approved deployments.
# Its output goes to files, so this script never waits on an inherited pipe.
& $pythonPath -X utf8 $supervisorPath start
if ($LASTEXITCODE -ne 0) { throw "Room did not start. See data\supervisor.log and data\server-error.log." }
Open-Room
