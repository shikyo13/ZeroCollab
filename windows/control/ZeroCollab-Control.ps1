param(
    [Parameter(Mandatory=$true)][string]$RoomRoot,
    [ValidateSet('Window','Start','Stop','Restart')][string]$Action = 'Window',
    [string]$PreviewPath,
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
$form = New-Object Windows.Forms.Form
$form.Text = 'ZeroCollab Control'
$form.ClientSize = New-Object Drawing.Size(530,310)
$form.MinimumSize = New-Object Drawing.Size(546,349)
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
$detail.SetBounds(26,108,475,62)
$detail.Anchor = 'Top,Left,Right'
$detail.ForeColor = [Drawing.Color]::FromArgb(190,201,219)
$form.Controls.AddRange(@($title,$stateLabel,$detail))
$buttons = @{}
$x = 24
foreach ($name in @('Start','Stop','Restart','Open')) {
    $button = New-Object Windows.Forms.Button
    $button.Text = $name
    $button.SetBounds($x,183,112,38)
    $button.FlatStyle = 'Flat'
    $button.BackColor = [Drawing.Color]::FromArgb(43,53,70)
    $buttons[$name] = $button
    $form.Controls.Add($button)
    $x += 121
}
$logs = New-Object Windows.Forms.LinkLabel
$logs.Text = 'Open logs folder'
$logs.LinkColor = [Drawing.Color]::LightSkyBlue
$logs.SetBounds(26,236,180,25)
$logs.Add_Click({ Start-Process explorer.exe -ArgumentList ('"' + (Join-Path $RoomRoot 'data') + '"') })
$hint = New-Object Windows.Forms.Label
$hint.Text = 'Closing this window keeps the controller in the notification area.'
$hint.SetBounds(26,274,480,25)
$hint.Font = New-Object Drawing.Font('Segoe UI',9)
$form.Controls.AddRange(@($logs,$hint))
$menu = New-Object Windows.Forms.ContextMenuStrip
$show = $menu.Items.Add('Show controller')
$show.Add_Click({ $form.Show(); $form.WindowState = 'Normal'; $form.Activate() })
$trayItems = @{}
foreach ($name in @('Start','Stop','Restart','Open')) { $trayItems[$name] = $menu.Items.Add($name) }
[void]$menu.Items.Add('-')
$quit = $menu.Items.Add('Exit controller (leave backend running)')
$quit.Add_Click({ if (!$script:worker) { $script:closing = $true; $form.Close() } })
$tray = New-Object Windows.Forms.NotifyIcon
$tray.Icon = $form.Icon
$tray.Text = 'ZeroCollab Control'
$tray.ContextMenuStrip = $menu
$tray.Visible = $true
$tray.Add_DoubleClick({ $form.Show(); $form.WindowState = 'Normal'; $form.Activate() })
$form.Add_FormClosing({ param($sender,$eventArgs) if (!$script:closing) { $eventArgs.Cancel = $true; $form.Hide() } })
if ($TrayOnly) { $form.Add_Shown({ $form.Hide() }) }

function Invoke-Control([string]$command) {
    if ($script:worker) { return }
    if ($command -eq 'Open') {
        # The supervisor opens the browser on a single-use sign-in link; the page no longer carries the token.
        Start-Process -FilePath $python -ArgumentList @('-X', 'utf8', "`"$supervisor`"", 'open') -WindowStyle Hidden
        return
    }
    if ($command -in @('Stop','Restart')) {
        $answer = [Windows.Forms.MessageBox]::Show("$command the ZeroCollab backend? Active agent turns will be interrupted. Saved chats and files remain.", 'ZeroCollab', 'YesNo', 'Warning', 'Button2')
        if ($answer -ne 'Yes') { return }
    }
    try {
        $logRoot = Join-Path $RoomRoot 'data'
        $script:outputPath = Join-Path $logRoot 'controller-output.log'
        $script:errorPath = Join-Path $logRoot 'controller-error.log'
        $arguments = '-NoProfile -NonInteractive -File "{0}" -RoomRoot "{1}" -Action {2}' -f $PSCommandPath,$RoomRoot,$command
        $script:worker = Start-Process -FilePath "$PSHOME\powershell.exe" -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $script:outputPath -RedirectStandardError $script:errorPath
        $null = $script:worker.Handle
        $script:pendingAction = $command
        $script:notice = ''
    } catch { $script:notice = $_.Exception.Message }
    Update-Control
}
foreach ($name in @('Start','Stop','Restart','Open')) {
    $handler = { param($sender,$eventArgs) Invoke-Control $sender.Text }
    $buttons[$name].Add_Click($handler)
    $trayItems[$name].Add_Click($handler)
}
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
    $status = Read-Status
    if ($script:webProcess -and $script:webProcess.HasExited) {
        $script:notice = 'The frontend connection could not start. See controller-web-server-error.log (port 8301 may be in use).'
    }
    $alive = $false
    if ($status.supervisor_pid) {
        $alive = $null -ne (Get-Process -Id $status.supervisor_pid -ErrorAction SilentlyContinue)
    }
    # Status is advisory. The supervisor independently verifies process identity before any action.
    $state = if ($alive) { [string]$status.state } else { 'stopped' }
    if ($status.url -match '^http://127\.0\.0\.1:\d+/?$') { $script:url = $status.url }
    $busy = $null -ne $script:worker
    $stateLabel.Text = if ($busy) { "$script:pendingAction in progress..." } else { "Backend: $state" }
    $detail.Text = if ($script:notice) { $script:notice } elseif ($status.error) { [string]$status.error } else { "$script:url`nThe controller stays available when the backend is stopped." }
    $tray.Text = ('ZeroCollab - ' + $stateLabel.Text).Substring(0,[Math]::Min(63,('ZeroCollab - ' + $stateLabel.Text).Length))
    foreach ($name in @('Start','Stop','Restart','Open')) {
        $enabled = !$busy
        if ($name -eq 'Start') { $enabled = !$busy -and !$alive }
        if ($name -in @('Stop','Restart')) { $enabled = !$busy -and $alive }
        $buttons[$name].Enabled = $enabled
        $trayItems[$name].Enabled = $enabled
    }
    $quit.Enabled = !$busy
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
