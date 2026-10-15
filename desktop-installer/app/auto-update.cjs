'use strict';
const { autoUpdater } = require('electron-updater');
const CHECK_INTERVAL_MS = 60_000;

function createUpdater({ publish, prepare, running }) {
  let configured = false, checking = false, timer;
  let state = { phase: 'checking', canBack: true };
  const set = (phase, values = {}) => {
    state = { phase, canBack: !['draining', 'restarting'].includes(phase), ...values };
    publish(state);
  };
  // The standard NSIS updater owns downloads, verification, replacement and relaunch.
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.logger = null; // Generic download errors can contain the private feed headers.
  autoUpdater.on('checking-for-update', () => set('checking'));
  autoUpdater.on('update-not-available', () => set('current'));
  autoUpdater.on('update-available', info => set('downloading', { version: info.version, percent: 0 }));
  autoUpdater.on('download-progress', progress => set('downloading', { percent: Math.round(progress.percent) }));
  autoUpdater.on('error', error => {
    set('failed', { error: 'The desktop update could not complete. The current app remains installed.', code: error.code || 'update-error' });
  });
  const install = () => {
    if (!running() || ['draining', 'restarting'].includes(state.phase)) return;
    set('restarting');
    prepare();
  };
  autoUpdater.on('update-downloaded', info => { set('ready', { version: info.version }); install(); });
  async function check() {
    if (!configured || checking || !running() || ['downloading', 'draining', 'restarting'].includes(state.phase)) return;
    if (state.phase === 'ready') { install(); return; }
    checking = true;
    try { await autoUpdater.checkForUpdates(); }
    catch { /* The error event carries a fixed, token-free message. */ }
    finally { checking = false; }
  }
  return {
    configure(origin, token) {
      autoUpdater.setFeedURL({ provider: 'generic', url: origin + '/api/desktop-updates/' });
      autoUpdater.requestHeaders = { 'X-Session-Token': token };
      configured = true;
      if (!timer) { timer = setInterval(check, CHECK_INTERVAL_MS); timer.unref(); }
      void check();
    },
    state: () => state,
    check,
    install,
    quitAndInstall() { clearInterval(timer); autoUpdater.quitAndInstall(true, true); }
  };
}
module.exports = { createUpdater };
