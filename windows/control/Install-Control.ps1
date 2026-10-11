param([Parameter(Mandatory=$true)][string]$RoomRoot, [switch]$RefreshRunning)
$ErrorActionPreference = 'Stop'
$RoomRoot = (Resolve-Path -LiteralPath $RoomRoot).Path
if (!(Test-Path -LiteralPath (Join-Path $RoomRoot 'supervisor.py'))) { throw 'RoomRoot must be the installed ZeroCollab folder.' }
$destination = Join-Path $env:LOCALAPPDATA 'ZeroCollabControl'
# Explicit one-time upgrade for controllers installed before self-refresh existed.
# This never stops the supervisor or room. Ordinary installation refuses a loaded copy.
$controllerPath = Join-Path $destination 'ZeroCollab-Control.ps1'
$serverPath = Join-Path $destination 'control_server.py'
$active = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='python.exe' OR Name='pythonw.exe'" | Where-Object {
    $_.CommandLine -and ($_.CommandLine.Contains($controllerPath) -or $_.CommandLine.Contains($serverPath))
})
if ($active.Count -gt 0) {
    if (!$RefreshRunning) { throw 'Exit ZeroCollab Control first, or use -RefreshRunning to upgrade only the controller.' }
    $statusPath = Join-Path $RoomRoot 'data\deploy\status.json'
    $status = if (Test-Path -LiteralPath $statusPath) { Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json } else { $null }
    $requests = @(Get-ChildItem -LiteralPath (Join-Path $RoomRoot 'data\deploy\requests') -Filter '*.json' -ErrorAction SilentlyContinue)
    if ($status.state -in @('deploying','starting','stopping') -or $requests.Count -gt 0 -or (Test-Path -LiteralPath (Join-Path $RoomRoot 'data\deploy\journal.json'))) { throw 'Wait until the current control or deployment operation has finished before upgrading the controller.' }
    foreach ($process in $active) {
        if (!$process.CommandLine.Contains($RoomRoot) -or $process.CommandLine -match '(?i)-Action\s+(Start|Stop|Restart)|--execute\s') { throw 'A controller operation or a controller for another room is active. Leave it untouched.' }
    }
    # Verified private controller scripts only; never select a process by its port or image alone.
    foreach ($process in $active) { Stop-Process -Id $process.ProcessId -ErrorAction Stop }
    foreach ($process in $active) { Wait-Process -Id $process.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
foreach ($file in @('ZeroCollab-Control.ps1','control_server.py','refresh_controller.py','ZeroCollab.ico')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $destination $file)
    if ((Get-FileHash (Join-Path $PSScriptRoot $file)).Hash -ne (Get-FileHash (Join-Path $destination $file)).Hash) { throw "Copy verification failed: $file" }
}
@{ version = (Get-Content -LiteralPath (Join-Path $RoomRoot 'VERSION') -Raw).Trim() } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'controller-version.json') -Encoding UTF8
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
foreach ($name in @('ZeroCollab Control')) {
    $link = $shell.CreateShortcut((Join-Path $desktop "$name.lnk"))
    $link.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
    $link.Arguments = '-NoProfile -STA -WindowStyle Hidden -File "{0}" -RoomRoot "{1}"' -f (Join-Path $destination 'ZeroCollab-Control.ps1'),$RoomRoot
    $link.WorkingDirectory = $destination
    $link.Description = 'Start, stop and restart the local ZeroCollab backend'
    $link.IconLocation = (Join-Path $destination 'ZeroCollab.ico') + ',0'
    $link.Save()
    Write-Output (Join-Path $desktop "$name.lnk")
}
$oldLink = Join-Path $desktop 'ZeroCollab Browser Controls.lnk'
if (Test-Path -LiteralPath $oldLink) {
    $old = $shell.CreateShortcut($oldLink)
    if ($old.Arguments.Contains((Join-Path $destination 'ZeroCollab-Control.ps1'))) { Remove-Item -LiteralPath $oldLink }
}
Write-Output "Installed controller: $destination. Backend unchanged."
if ($RefreshRunning) {
    $arguments = '-NoProfile -STA -WindowStyle Hidden -File "{0}" -RoomRoot "{1}" -TrayOnly' -f (Join-Path $destination 'ZeroCollab-Control.ps1'),$RoomRoot
    Start-Process powershell.exe -ArgumentList $arguments -WindowStyle Hidden
    Write-Output 'Tray controller relaunched. Room process was left running.'
}
