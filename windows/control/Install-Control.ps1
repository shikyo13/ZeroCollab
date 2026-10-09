param([Parameter(Mandatory=$true)][string]$RoomRoot)
$ErrorActionPreference = 'Stop'
$RoomRoot = (Resolve-Path -LiteralPath $RoomRoot).Path
if (!(Test-Path -LiteralPath (Join-Path $RoomRoot 'supervisor.py'))) { throw 'RoomRoot must be the installed ZeroCollab folder.' }
$destination = Join-Path $env:LOCALAPPDATA 'ZeroCollabControl'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
foreach ($file in @('ZeroCollab-Control.ps1','control_server.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $destination $file)
    if ((Get-FileHash (Join-Path $PSScriptRoot $file)).Hash -ne (Get-FileHash (Join-Path $destination $file)).Hash) { throw "Copy verification failed: $file" }
}
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
foreach ($name in @('ZeroCollab Control')) {
    $link = $shell.CreateShortcut((Join-Path $desktop "$name.lnk"))
    $link.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
    $link.Arguments = '-NoProfile -STA -WindowStyle Hidden -File "{0}" -RoomRoot "{1}"' -f (Join-Path $destination 'ZeroCollab-Control.ps1'),$RoomRoot
    $link.WorkingDirectory = $destination
    $link.Description = 'Start, stop and restart the local ZeroCollab backend'
    $link.Save()
    Write-Output (Join-Path $desktop "$name.lnk")
}
$oldLink = Join-Path $desktop 'ZeroCollab Browser Controls.lnk'
if (Test-Path -LiteralPath $oldLink) {
    $old = $shell.CreateShortcut($oldLink)
    if ($old.Arguments.Contains((Join-Path $destination 'ZeroCollab-Control.ps1'))) { Remove-Item -LiteralPath $oldLink }
}
Write-Output "Installed controller: $destination. Backend unchanged."
