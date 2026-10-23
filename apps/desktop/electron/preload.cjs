const { contextBridge, ipcRenderer, webUtils } = require('electron');
// The native chooser bridge exists only on the packaged handover document.
// Every invocation is checked again against that trusted main frame in main.
if (process.isMainFrame) {
  if (ipcRenderer.sendSync('zc-update-enabled') === true) {
    contextBridge.exposeInMainWorld('desktopUpdate', {
      state: () => ipcRenderer.invoke('zc-update-action', 'state'),
      start: id => ipcRenderer.invoke('zc-update-action', 'start', id),
      check: () => ipcRenderer.invoke('zc-update-action', 'check'),
      back: () => ipcRenderer.invoke('zc-update-action', 'back'),
      onState: callback => {
        if (typeof callback === 'function') ipcRenderer.on('zc-update-state', (_, state) => callback(state));
      }
    });
  }
  if (ipcRenderer.sendSync('zc-handover-enabled') === true) {
    contextBridge.exposeInMainWorld('desktopRoom', {
      state: () => ipcRenderer.invoke('zc-room-action', 'state'),
      choose: () => ipcRenderer.invoke('zc-room-action', 'choose'),
      open: () => ipcRenderer.invoke('zc-room-action', 'open'),
      onState: callback => {
        if (typeof callback === 'function') ipcRenderer.on('zc-room-state', (_, state) => callback(state));
      }
    });
  }
  const token = ipcRenderer.sendSync('zc-test-token');
  if (typeof token === 'string' && /^[a-f0-9]{64}$/.test(token)) {
    contextBridge.exposeInMainWorld('__SESSION_TOKEN__', token);
    // Only the trusted room main document receives paths for user-supplied Files.
    contextBridge.exposeInMainWorld('ZeroCollabDesktop', {
      pathForFile: file => {
        try { return webUtils.getPathForFile(file) || ''; }
        catch { return ''; }
      }
    });
    // Only the main process's bounded UI allowlist crosses origins. Authentication,
    // chat drafts and arbitrary localStorage never leave this page's origin.
    const preferences = ipcRenderer.sendSync('zc-ui-state');
    if (preferences) {
      let previous = '';
      try {
        for (const key of preferences.keys) {
          const value = preferences.values[key];
          if (typeof value === 'string') localStorage.setItem(key, value);
          else localStorage.removeItem(key);
        }
      } catch {}
      const flush = () => {
        try {
          const values = {};
          for (const key of preferences.keys) values[key] = localStorage.getItem(key);
          const serialized = JSON.stringify(values);
          if (serialized !== previous) { ipcRenderer.sendSync('zc-ui-state', values); previous = serialized; }
        } catch {}
      };
      const timer = setInterval(flush, preferences.interval);
      ipcRenderer.on('zc-ui-flush', flush);
      window.addEventListener('pagehide', () => { clearInterval(timer); flush(); }, { once: true });
    }
  }
}
