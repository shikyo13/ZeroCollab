param(
    [Parameter(Mandatory=$true)][string]$RoomRoot,
    [ValidateSet('Window','Start','Stop','Restart')][string]$Action = 'Window',
    [string]$PreviewPath,
    [string]$PreviewStatusPath,
    [switch]$TrayOnly
)
$ErrorActionPreference = 'Stop'
$RoomRoot = (Resolve-Path -LiteralPath $RoomRoot).Path.TrimEnd('\')
$python = Join-Path $RoomRoot '.venv\Scripts\python.exe'
$supervisor = Join-Path $RoomRoot 'supervisor.py'
$statusPath = Join-Path $RoomRoot 'data\deploy\status.json'
if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $supervisor)) {
    throw 'Select the installed ZeroCollab folder containing supervisor.py and .venv.'
}
function Read-Status {
    try { return Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json } catch { return $null }
}
if ($Action -ne 'Window') {
    $digest = [Security.Cryptography.SHA256]::Create()
    $identity = [BitConverter]::ToString($digest.ComputeHash([Text.Encoding]::UTF8.GetBytes($RoomRoot.ToLowerInvariant()))).Replace('-','')
    $digest.Dispose()
    $operation = New-Object Threading.Mutex($false, "Local\ZeroCollab-Operation-$identity")
    if (!$operation.WaitOne(0)) { throw 'Another backend control operation is in progress.' }
    try {
    if ($Action -in @('Stop','Restart')) {
        & $python -X utf8 $supervisor stop
        if ($LASTEXITCODE -ne 0) { throw 'Stop was not confirmed. No restart attempted. See supervisor.log.' }
        $stopped = Read-Status
        if ($stopped.state -ne 'stopped') { throw "Supervisor ended with state '$($stopped.state)'. No restart attempted." }
    }
    if ($Action -in @('Start','Restart')) {
        & $python -X utf8 $supervisor start
        if ($LASTEXITCODE -ne 0) { throw 'Startup failed. See supervisor.log and server-error.log.' }
    }
    } finally { $operation.ReleaseMutex(); $operation.Dispose() }
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class ZeroCollabWindowIdentity {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SetCurrentProcessExplicitAppUserModelID(string appID);
}
'@
[void][ZeroCollabWindowIdentity]::SetCurrentProcessExplicitAppUserModelID('ZeroCollab.Control')
[Windows.Forms.Application]::EnableVisualStyles()
$hash = [Security.Cryptography.SHA256]::Create()
$key = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($RoomRoot.ToLowerInvariant()))).Replace('-','')
$hash.Dispose()
$mutex = New-Object Threading.Mutex($false, "Local\ZeroCollab-Control-$key")
if (!$mutex.WaitOne(0)) {
    [Windows.Forms.MessageBox]::Show('ZeroCollab Control is already open. Look in the Windows notification area.', 'ZeroCollab') | Out-Null
    $mutex.Dispose()
    exit
}
$script:worker = $null
$script:pendingAction = ''
$script:notice = ''
$script:url = 'http://127.0.0.1:8300'
$script:closing = $false
$script:webProcess = $null
$savedStatus = Read-Status
$script:origin = if ($savedStatus.url -match '^http://127\.0\.0\.1:\d+/?$') { $savedStatus.url.TrimEnd('/') } else { 'http://127.0.0.1:8300' }
$form = New-Object Windows.Forms.Form
$form.Text = 'ZeroCollab Control'
$form.ClientSize = New-Object Drawing.Size(530,430)
$form.MinimumSize = New-Object Drawing.Size(546,469)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object Drawing.Font('Segoe UI',10)
$form.BackColor = [Drawing.Color]::FromArgb(24,28,36)
$form.ForeColor = [Drawing.Color]::FromArgb(235,239,246)
$productIcon = $null
$iconPath = Join-Path $PSScriptRoot 'ZeroCollab.ico'
if (Test-Path -LiteralPath $iconPath) {
    $productIcon = New-Object Drawing.Icon($iconPath)
    $form.Icon = $productIcon
} else { $form.Icon = [Drawing.SystemIcons]::Application }
$title = New-Object Windows.Forms.Label
$title.Text = 'ZeroCollab'
$title.Font = New-Object Drawing.Font('Segoe UI',20,[Drawing.FontStyle]::Bold)
$title.SetBounds(24,20,470,42)
$stateLabel = New-Object Windows.Forms.Label
$stateLabel.SetBounds(26,74,475,30)
$detail = New-Object Windows.Forms.Label
$detail.SetBounds(26,145,475,52)
$detail.Anchor = 'Top,Left,Right'
$detail.ForeColor = [Drawing.Color]::FromArgb(190,201,219)
$versionLabel = New-Object Windows.Forms.Label
$versionLabel.SetBounds(26,108,475,30)
$versionLabel.ForeColor = [Drawing.Color]::LightSkyBlue
$form.Controls.AddRange(@($title,$stateLabel,$versionLabel,$detail))
$buttons = @{}
$x = 24
foreach ($name in @('Start','Stop','Restart','Open')) {
    $button = New-Object Windows.Forms.Button
    $button.Text = $name
    $button.SetBounds($x,210,112,38)
    $button.FlatStyle = 'Flat'
    $button.BackColor = [Drawing.Color]::FromArgb(43,53,70)
    $buttons[$name] = $button
    $form.Controls.Add($button)
    $x += 121
}
$logs = New-Object Windows.Forms.LinkLabel
$logs.Text = 'Open logs folder'
$logs.LinkColor = [Drawing.Color]::LightSkyBlue
$logs.SetBounds(26,355,180,25)
$logs.Add_Click({ Start-Process explorer.exe -ArgumentList ('"' + (Join-Path $RoomRoot 'data') + '"') })
$hint = New-Object Windows.Forms.Label
$hint.Text = 'Closing this window keeps the controller in the notification area.'
$hint.SetBounds(26,394,480,25)
$hint.Font = New-Object Drawing.Font('Segoe UI',9)
$form.Controls.AddRange(@($logs,$hint))
$updateLabel = New-Object Windows.Forms.Label
$updateLabel.SetBounds(26,269,345,60)
$updateLabel.ForeColor = [Drawing.Color]::FromArgb(190,201,219)
$install = New-Object Windows.Forms.Button
$install.Text = 'Install'
$install.SetBounds(389,272,112,38)
$install.FlatStyle = 'Flat'
$install.BackColor = [Drawing.Color]::FromArgb(43,53,70)
$form.Controls.AddRange(@($updateLabel,$install))
$menu = New-Object Windows.Forms.ContextMenuStrip
$show = $menu.Items.Add('Show controller')
$show.Add_Click({ $form.Show(); $form.WindowState = 'Normal'; $form.Activate() })
$trayItems = @{}
foreach ($name in @('Start','Stop','Restart','Open')) { $trayItems[$name] = $menu.Items.Add($name) }
$trayInstall = $menu.Items.Add('Install staged update')
[void]$menu.Items.Add('-')
$quit = $menu.Items.Add('Exit controller (leave backend running)')
$quit.Add_Click({ if (!$script:worker) { $script:closing = $true; $form.Close() } })
$tray = New-Object Windows.Forms.NotifyIcon
$tray.Icon = $form.Icon
$tray.Text = 'ZeroCollab Control'
$tray.ContextMenuStrip = $menu
$tray.Visible = !$PreviewPath
$tray.Add_DoubleClick({ $form.Show(); $form.WindowState = 'Normal'; $form.Activate() })
$form.Add_FormClosing({ param($sender,$eventArgs) if (!$script:closing) { $eventArgs.Cancel = $true; $form.Hide() } })
if ($TrayOnly) { $form.Add_Shown({ $form.Hide() }) }

function Read-ControlStatus {
    if ($PreviewStatusPath) { return Get-Content -LiteralPath $PreviewStatusPath -Raw | ConvertFrom-Json }
    return Invoke-RestMethod -Uri 'http://127.0.0.1:8301/status' -Headers @{ Origin = $script:origin } -TimeoutSec 3
}
function Invoke-Control([string]$command) {
    if (!$script:controlStatus -or $script:controlStatus.busy -or $PreviewPath) { return }
    if ($command -in @('Stop','Restart')) {
        $answer = [Windows.Forms.MessageBox]::Show("$command the ZeroCollab backend? Active agent turns will be interrupted. Saved chats and files remain.", 'ZeroCollab', 'YesNo', 'Warning', 'Button2')
        if ($answer -ne 'Yes') { return }
    }
    if ($command -eq 'Install') {
        $selected = $script:controlStatus.update
        if (!$selected) { return }
        $answer = [Windows.Forms.MessageBox]::Show("Install ZeroCollab $($selected.version)? Current turns can finish before the room restarts. If the room is stopped, it will be started to install. Saved chats and files remain.", 'ZeroCollab', 'YesNo', 'Question', 'Button2')
        if ($answer -ne 'Yes') { return }
    }
    try {
        $body = @{ action = $command }
        if ($command -eq 'Install') { $body.release_id = $selected.id; $body.manifest_sha = $selected.manifest_sha }
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8301/action' -Method Post -ContentType 'application/json' -Body ($body | ConvertTo-Json -Compress) -Headers @{ Origin = $script:origin; 'X-Control-Token' = $script:controlStatus.token } -TimeoutSec 10
        $script:notice = "$command submitted."
    } catch { $script:notice = $_.Exception.Message }
    Update-Control
}
foreach ($name in @('Start','Stop','Restart','Open')) {
    $handler = { param($sender,$eventArgs) Invoke-Control $sender.Text }
    $buttons[$name].Add_Click($handler)
    $trayItems[$name].Add_Click($handler)
}
$install.Add_Click({ Invoke-Control 'Install' })
$trayInstall.Add_Click({ Invoke-Control 'Install' })
function Update-Control {
    if ($script:worker -and $script:worker.HasExited) {
        $script:worker.WaitForExit()
        if ($script:worker.ExitCode -eq 0) { $script:notice = "$script:pendingAction completed." }
        else {
            $script:notice = "$script:pendingAction failed. Open logs: controller-error.log and supervisor.log."
            $tray.ShowBalloonTip(6000,'ZeroCollab',$script:notice,[Windows.Forms.ToolTipIcon]::Error)
        }
        $script:worker.Dispose()
        $script:worker = $null
    }
    try { $script:controlStatus = Read-ControlStatus } catch { $script:controlStatus = $null }
    $status = $script:controlStatus
    if ($script:webProcess -and $script:webProcess.HasExited) {
        $script:notice = 'The frontend connection could not start. See controller-web-server-error.log (port 8301 may be in use).'
    }
    $alive = $status -and $status.alive
    $state = if ($alive) { [string]$status.state } else { 'stopped' }
    if ($status.url -match '^http://127\.0\.0\.1:\d+/?$') { $script:url = $status.url }
    $busy = !$status -or $status.busy
    $stateLabel.Text = if (!$status) { 'Controller connection unavailable' } elseif ($busy) { 'Backend: action or update in progress' } else { "Backend: $state" }
    $versionLabel.Text = if ($status) { "Installed $($status.current.version) | Controller $($status.controller.version) | " + $(if ($status.controller.in_sync) { 'In sync' } else { 'Refresh pending' }) } else { 'Retrying the tray connection...' }
    $detail.Text = if ($status.controller.refresh_error) { "Controller refresh failed. See controller-refresh-error.log. $($status.controller.refresh_error)" } elseif ($status.progress) { [string]$status.progress } elseif ($script:notice) { $script:notice } elseif ($status.message) { [string]$status.message } else { "$script:url`nThe controller stays available when the backend is stopped." }
    $updateLabel.Text = if ($busy -and $status) { "Update / control in progress`n$($status.progress)" } elseif ($status.update) { "$($status.update.version) ready`nCurrent turns can finish before installation." } elseif ($status.receipt) { "Latest update: $($status.receipt.status)`n$($status.receipt.detail)" } else { 'No staged update ready to install.' }
    $install.Enabled = $status -and !$busy -and $null -ne $status.update
    $trayInstall.Enabled = $install.Enabled
    $trayInstall.Text = if ($status.update) { "Install $($status.update.version)" } else { 'No staged update ready' }
    $tray.Text = ('ZeroCollab - ' + $stateLabel.Text).Substring(0,[Math]::Min(63,('ZeroCollab - ' + $stateLabel.Text).Length))
    foreach ($name in @('Start','Stop','Restart','Open')) {
        $enabled = !$busy
        if ($name -eq 'Start') { $enabled = !$busy -and !$alive }
        if ($name -eq 'Open') { $enabled = !$busy -and $alive }
        if ($name -in @('Stop','Restart')) { $enabled = !$busy -and $alive }
        $buttons[$name].Enabled = $enabled
        $trayItems[$name].Enabled = $enabled
    }
    $quit.Enabled = !$busy
    if ($status -and !$status.controller.in_sync -and !$status.controller.refresh_error -and !$busy -and !$PreviewPath -and !$script:refreshing) {
        # Only refresh the managed installed copy; never rewrite the runtime source.
        $managed = Join-Path $env:LOCALAPPDATA 'ZeroCollabControl'
        if ([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') -eq [IO.Path]::GetFullPath($managed).TrimEnd('\')) {
            $script:refreshing = $true
            $refreshArgs = '-X utf8 "{0}" --root "{1}" --destination "{2}" --parent-pid {3}' -f (Join-Path $RoomRoot 'windows\control\refresh_controller.py'),$RoomRoot,$PSScriptRoot,$PID
            if (!$form.Visible -or $TrayOnly) { $refreshArgs += ' --tray-only' }
            Start-Process -FilePath $python -ArgumentList $refreshArgs -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RoomRoot 'data\controller-refresh.log') -RedirectStandardError (Join-Path $RoomRoot 'data\controller-refresh-error.log')
            $script:closing = $true
            $form.Close()
        }
    }
}
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1500
$timer.Add_Tick({ Update-Control })
try {
    if (!$PreviewPath) {
        $webArguments = '-X utf8 "{0}" --root "{1}"' -f (Join-Path $PSScriptRoot 'control_server.py'),$RoomRoot
        $script:webProcess = Start-Process -FilePath $python -ArgumentList $webArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $RoomRoot 'data\controller-web.log') -RedirectStandardError (Join-Path $RoomRoot 'data\controller-web-server-error.log')
    }
    Update-Control
    $timer.Start()
    if ($PreviewPath) {
        $form.ShowInTaskbar = $false
        $form.StartPosition = 'Manual'
        $form.Location = New-Object Drawing.Point(-20000,-20000)
        $form.Show()
        [Windows.Forms.Application]::DoEvents()
        $bitmap = New-Object Drawing.Bitmap($form.Width,$form.Height)
        try { $form.DrawToBitmap($bitmap,(New-Object Drawing.Rectangle(0,0,$form.Width,$form.Height))); $bitmap.Save($PreviewPath) }
        finally { $bitmap.Dispose() }
    } else { [Windows.Forms.Application]::Run($form) }
} finally {
    if ($script:webProcess -and !$script:webProcess.HasExited) { $script:webProcess.Kill(); $script:webProcess.Dispose() }
    $timer.Stop(); $timer.Dispose()
    $tray.Visible = $false; $tray.Dispose(); $menu.Dispose(); $form.Dispose()
    if ($productIcon) { $productIcon.Dispose() }
    $mutex.ReleaseMutex(); $mutex.Dispose()
}
