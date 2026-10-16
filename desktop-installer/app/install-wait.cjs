'use strict';
const { spawn } = require('node:child_process');
const path = require('node:path');

// electron-builder UUID.v5(appId, ELECTRON_BUILDER_NS_UUID), unchanged since 0.1.1.
// Its NSIS installer holds this mutex until it exits, including file extraction.
const INSTALLER_MUTEX = '41630143-3270-5511-8cf2-a539a5f4d76b';

function waitForInstaller({ onWaiting = () => {}, signal } = {}) {
  if (process.platform !== 'win32') return Promise.resolve();
  const script = `
$ErrorActionPreference = 'Stop'
try { $installer = [System.Threading.Mutex]::OpenExisting('${INSTALLER_MUTEX}') }
catch [System.Threading.WaitHandleCannotBeOpenedException] { exit 0 }
try {
  [Console]::Out.WriteLine('waiting')
  $deadline = [DateTime]::UtcNow.AddMinutes(2)
  while ([DateTime]::UtcNow -lt $deadline) {
    if (-not (Get-Process -Id ${process.pid} -ErrorAction SilentlyContinue)) { exit 3 }
    try { $released = $installer.WaitOne(250) }
    catch [System.Threading.AbandonedMutexException] { $released = $true }
    if ($released) { $installer.ReleaseMutex(); exit 0 }
  }
  exit 2
} finally { $installer.Dispose() }
`;
  return new Promise((resolve, reject) => {
    const probe = spawn(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
      ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')],
      { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'], signal, timeout: 125000 });
    let reported = false;
    probe.stdout.on('data', () => {
      if (!reported) { reported = true; onWaiting(); }
    });
    probe.once('error', reject);
    probe.once('close', code => code === 0 ? resolve() : reject(Error('Installer wait did not complete')));
  });
}
module.exports = { waitForInstaller, INSTALLER_MUTEX };
