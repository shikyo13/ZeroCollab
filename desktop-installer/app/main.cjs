const { app, BrowserWindow, session, ipcMain, Tray, Menu, nativeImage, dialog, screen, shell } = require('electron');
const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { mkdirSync, writeFileSync, readFileSync, lstatSync, realpathSync, statSync, openSync, closeSync } = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { allowedDownload } = require('./downloads.cjs');
const { createUIState } = require('./ui-state.cjs');
const { createUpdater } = require('./auto-update.cjs');
if (process.platform === 'win32') app.setAppUserModelId('com.zerocollab.desktop');
const DEFAULT_CONTENT_SIZE = { width: 1280, height: 800 };
const WINDOW_SAVE_DELAY_MS = 200;
const testCaption = process.env.ZC_DESKTOP_TEST_TITLE || '';
const windowCaption = /^Desktop engineer [A-Za-z0-9 -]{1,64}$/.test(testCaption) ? testCaption : 'Workspace';
const root = __dirname;
const resources = process.resourcesPath;
// This machine's GPU capture returned UnknownVizError. Author capture uses
// Electron's software rendering path; ordinary QA launch keeps its default.
if (process.env.ZC_TEST_CAPTURE === '1') app.disableHardwareAcceleration();
// Explicit Windows APPDATA also works when a fixture has no Windows user profile.
// Fail before any window/backend exists, without Electron's uncaught-error dialog.
let artifacts;
try {
  if (process.platform === 'win32' && process.env.APPDATA) {
    if (!path.isAbsolute(process.env.APPDATA)) throw Error('Invalid application data directory');
    mkdirSync(process.env.APPDATA, { recursive: true });
    app.setPath('appData', process.env.APPDATA);
  }
  artifacts = path.join(app.getPath('appData'), 'ZeroCollab-Desktop');
  mkdirSync(artifacts, { recursive: true });
  app.setName('ZeroCollab');
  for (const [name, folder] of [['userData', 'electron-profile'], ['crashDumps', 'crashes']]) {
    const directory = path.join(artifacts, folder);
    mkdirSync(directory, { recursive: true });
    app.setPath(name, directory);
  }
} catch {
  process.stderr.write('ZeroCollab could not initialize its application data directory. Nothing was opened.\n');
  app.exit(1);
}
let win, tray, child, origin = '', token = '', instance = '', exiting = false;
let creatingWindow = false, closingAck = false, handshake = false, startupTimer;
let launchState = 'starting', shutdownStarted = false, shutdownTimer;
let childClosed = false, childCloseCode = null;
let refusedBeforeOpen = false;
let generation = 0, returnURL = '';
let hostQuitting = false;
const evidence = { mode: 'desktop', cli_logins: { mode: 'shared' }, rendering: process.env.ZC_TEST_CAPTURE === '1' ? 'offscreen software author capture' : 'default', lifecycle: [], readiness: null, shutdown: null };
const loadingURL = pathToFileURL(path.join(root, 'loading.html')).href;
const handoverURL = pathToFileURL(path.join(root, 'handover.html')).href;
const updateURL = pathToFileURL(path.join(root, 'update.html')).href;
const updateAssetURLs = new Set([updateURL, ...['update.css', 'update.cjs'].map(name => pathToFileURL(path.join(root, name)).href)]);
let updateState = { phase: 'checking', canBack: true };
let installRequested = false;
let updateReturnURL = '';
let selectedRoom = '', selectedData = '', chooserBusy = false, chooserMessage = '';
const selectionFile = path.join(artifacts, 'room-selection.json');
const uiState = createUIState(path.join(artifacts, 'ui-state.json'), () => {
  process.stderr.write('Desktop view settings could not be saved or restored.\n');
});
function validRoom(folder) {
  if (typeof folder !== 'string' || !path.isAbsolute(folder)) throw Error('Invalid folder');
  const canonical = realpathSync(folder);
  if (!statSync(canonical).isDirectory() || !statSync(path.join(canonical, 'config.toml')).isFile()) throw Error('Missing config');
  return canonical;
}
let rememberedSelection;
try { rememberedSelection = readFileSync(selectionFile, 'utf8'); }
catch (error) {
  if (error.code !== 'ENOENT') chooserMessage = 'The saved room selection could not be read. Choose your existing room folder.';
}
if (rememberedSelection !== undefined) {
  try { selectedRoom = validRoom(JSON.parse(rememberedSelection).room); }
  catch { chooserMessage = 'Your saved room folder is unavailable. Choose your existing room folder.'; }
}
function childEnvironment() {
  const env = {};
  for (const key of ['PATH', 'Path', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
    'ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432', 'HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'ZC_DESKTOP_TEST_TITLE'])
    if (process.env[key]) env[key] = process.env[key];
  return env;
}
function chooserState() { return { room: selectedRoom, data: selectedData, message: chooserMessage,
  busy: chooserBusy || !!(child && !childClosed), canOpen: !!selectedRoom && !chooserBusy && !(child && !childClosed) }; }
function sendChooserState() {
  if (win && !win.isDestroyed() && win.webContents.mainFrame.url === handoverURL) win.webContents.send('zc-room-state', chooserState());
}
function save() { writeFileSync(path.join(artifacts, 'session.json'), JSON.stringify(evidence, null, 2)); }
function event(kind) { evidence.lifecycle.push({ kind, time: new Date().toISOString() }); save(); }
function trustedDocument(url) { try { const u = new URL(url); return u.origin === origin && u.pathname === '/'; } catch { return false; } }
function roomClipboardRead(contents, permission, requestingUrl, isMainFrame) {
  return permission === 'clipboard-read' && win && !win.isDestroyed() && contents === win.webContents &&
    isMainFrame === true && trustedDocument(contents.mainFrame.url) && trustedDocument(requestingUrl);
}
function openWebLink(contents, value) {
  // User-clicked web and room media links open in the default browser.
  // Packaged documents and other room endpoints stay inside the app.
  if (!trustedDocument(contents.mainFrame.url)) return;
  let url;
  try { url = new URL(value); } catch { return; }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return;
  if (url.origin === origin) {
    // Match the room's FileReferences media URL contract. A raw full-size link
    // needs the same token as its inline player; never pass it to another origin.
    const media = /^\/api\/files\/ref\/[0-9a-f]{16}\/[^/]+$/i.test(url.pathname) ||
      /^\/uploads\/[a-z0-9_-][a-z0-9_.-]*$/i.test(url.pathname);
    if (!media) return;
    url.searchParams.set('token', token);
  }
  setImmediate(() => {
    shell.openExternal(url.href).catch(() => console.error('External web link could not be opened.'));
  });
}
function fail(reason) {
  if (launchState === 'failed' || launchState === 'stopping' || launchState === 'closed') return;
  launchState = 'failed';
  clearTimeout(startupTimer);
  token = ''; origin = '';
  event('failed:' + reason);
  if (win && !win.isDestroyed() && !win.webContents.isDestroyed()) {
    // Fixed strings only. Failed child stays owned; no automatic restart/attach.
    win.webContents.stop();
    chooserMessage = reason;
    win.loadFile(path.join(root, 'handover.html')).then(sendChooserState).catch(() => {});
    if (process.env.ZC_TEST_CAPTURE !== '1') win.show();
  }
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { win.show(); win.focus(); } });
  app.on('web-contents-created', (_, contents) => {
    const allowedType = contents.getType() === 'window' ||
      (process.env.ZC_TEST_CAPTURE === '1' && contents.getType() === 'offscreen');
    if (!creatingWindow || !allowedType) { contents.destroy(); return; }
    const navigation = (event, url) => {
      if ([loadingURL, handoverURL, updateURL].includes(url) || trustedDocument(url)) return;
      event.preventDefault();
      if (event.isMainFrame !== false) openWebLink(contents, url);
    };
    contents.on('will-navigate', navigation);
    contents.on('will-redirect', navigation);
    contents.on('will-frame-navigate', event => {
      if (!event.isMainFrame) { event.preventDefault(); return; }
      // This precedes will-navigate; cancellation stops later events, so an
      // external click opens once while the app stays on its room document.
      navigation(event, event.url);
    });
    contents.on('will-attach-webview', event => event.preventDefault());
    contents.setWindowOpenHandler(({ url }) => {
      openWebLink(contents, url);
      return { action: 'deny' };
    });
    contents.on('render-process-gone', () => fail('Renderer process exited'));
    contents.on('did-fail-load', (_, code, __, ___, isMain) => {
      if (isMain && code !== -3) { evidence.load_error_code = code; fail('Workspace loading failed'); }
    });
  });
  ipcMain.on('zc-test-token', event => {
    event.returnValue = win && event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame &&
      trustedDocument(event.senderFrame.url) ? token : undefined;
  });
  function trustedWorkspace(event) { return win && event.sender === win.webContents &&
    event.senderFrame === win.webContents.mainFrame && trustedDocument(event.senderFrame.url); }
  function trustedUpdater(event) { return win && event.sender === win.webContents &&
    event.senderFrame === win.webContents.mainFrame && event.senderFrame.url === updateURL; }
  async function showUpdates() {
    if (launchState !== 'running' || exiting) return;
    if (trustedDocument(win.webContents.getURL())) updateReturnURL = win.webContents.getURL();
    win.webContents.send('zc-ui-flush');
    try { await win.loadFile(path.join(root, 'update.html')); }
    catch { if (launchState !== 'failed') fail('Desktop update page could not load'); return; }
    win.show(); win.focus();
  }
  const updater = createUpdater({
    publish(state) {
      updateState = state;
      evidence.desktop_update = { phase: state.phase, version: state.version, time: new Date().toISOString() };
      save();
      if (win && !win.isDestroyed() && win.webContents.mainFrame.url === updateURL) win.webContents.send('zc-update-state', state);
    },
    prepare() { installRequested = true; beginShutdown(); },
    running: () => launchState === 'running' && !exiting
  });
  ipcMain.on('zc-update-enabled', event => { event.returnValue = !!trustedUpdater(event); });
  ipcMain.handle('zc-update-action', async (event, action) => {
    if (!trustedUpdater(event)) throw Error('Untrusted desktop update command');
    if (action === 'back' && updateState.canBack) {
      await win.loadURL(trustedDocument(updateReturnURL) ? updateReturnURL : origin + '/?view=chat');
    } else if (action === 'start' && updateState.phase === 'ready') updater.install();
    else if (action === 'check') void updater.check();
    else if (action !== 'state') throw Error('Invalid desktop update command');
    return updateState;
  });
  ipcMain.on('zc-ui-state', (event, values) => {
    if (!trustedWorkspace(event) || !selectedRoom) { event.returnValue = null; return; }
    if (values !== undefined) { uiState.savePreferences(selectedRoom, values); event.returnValue = null; }
    else event.returnValue = uiState.preferences(selectedRoom);
  });
  function trustedChooser(event) { return win && event.sender === win.webContents &&
    event.senderFrame === win.webContents.mainFrame && event.senderFrame.url === handoverURL; }
  ipcMain.on('zc-handover-enabled', event => { event.returnValue = !!trustedChooser(event); });
  ipcMain.handle('zc-room-action', async (event, action) => {
    if (!trustedChooser(event)) throw Error('Untrusted room selection');
    if (action === 'state') return chooserState();
    if (chooserBusy || (child && !childClosed) || !['starting', 'failed', 'choosing'].includes(launchState)) return chooserState();
    if (action === 'choose') {
      chooserBusy = true; sendChooserState();
      try {
        const picked = await dialog.showOpenDialog(win, { title: 'Choose your ZeroCollab runtime folder', properties: ['openDirectory', 'dontAddToRecent'] });
        if (!picked.canceled && picked.filePaths.length === 1) {
          selectedRoom = validRoom(picked.filePaths[0]); selectedData = ''; chooserMessage = '';
          writeFileSync(selectionFile, JSON.stringify({ room: selectedRoom }));
        }
      } catch { chooserMessage = 'Choose an existing runtime folder containing config.toml.'; }
      finally { chooserBusy = false; sendChooserState(); }
    } else if (action === 'open' && selectedRoom) { launchRoom(); }
    return chooserState();
  });
  async function quitRoom() {
    if (exiting) return;
    const answer = await dialog.showMessageBox(win, { type: 'warning', title: 'Quit ZeroCollab?',
      message: 'Quit this desktop room and ZeroCollab?', detail: 'Active turns end and the room is saved. On the next launch, this room restores its running or stopped state. Individually paused agents stay paused.',
      buttons: ['Keep running', 'Quit ZeroCollab'], defaultId: 0, cancelId: 0 });
    if (answer.response !== 1) return;
    beginShutdown();
  }
  async function finishShutdown(timedOut = false) {
    if (launchState === 'closed' || (child && !childClosed && !timedOut)) return;
    clearTimeout(shutdownTimer);
    launchState = 'closed';
    token = ''; origin = '';
    const graceful = !timedOut && (!child || (closingAck && childCloseCode === 0) ||
      (refusedBeforeOpen && childClosed && !handshake));
    evidence.shutdown = { graceful, backend_started: !!child, child_exit: childCloseCode, close_ack: closingAck };
    if (!graceful) evidence.shutdown.reason = timedOut ? 'Close acknowledgement or child exit timed out'
      : 'Backend exited without matching close acknowledgement and successful exit';
    event(graceful ? 'shutdown-complete' : 'shutdown-failed');
    if (graceful) {
      if (installRequested) updater.quitAndInstall();
      else app.quit();
      return;
    }
    // Quit was already confirmed. Keep the failure receipt and exit without a second modal.
    // Exiting releases the parent-owned kill-on-close job, including remaining backend children.
    app.exit(1);
  }
  function beginShutdown() {
    if (shutdownStarted) return;
    shutdownStarted = true;
    if (win && !win.webContents.isDestroyed()) win.webContents.send('zc-ui-flush');
    exiting = true;
    launchState = 'stopping';
    clearTimeout(startupTimer);
    if (win && !win.webContents.isDestroyed()) win.webContents.stop();
    event('quit-requested');
    if (!child || childClosed) { void finishShutdown(); return; }
    shutdownTimer = setTimeout(() => { void finishShutdown(true); }, 15000);
    // An exited process can still have buffered stdout: await 'close', not 'exit'.
    if (child.exitCode === null && !child.stdin.destroyed && !child.stdin.writableEnded) {
      if (refusedBeforeOpen) child.stdin.end();
      else child.stdin.write('quit\n');
    }
  }
  app.on('before-quit', event => { if (!exiting) { event.preventDefault(); void quitRoom(); } });
  app.on('window-all-closed', () => {});
  app.whenReady().then(async () => {
    const ses = session.fromPartition('zc-test-' + Date.now());
    ses.setPermissionRequestHandler((contents, permission, callback, details) => callback(
      (permission === 'clipboard-sanitized-write' && trustedDocument(details.requestingUrl)) ||
      roomClipboardRead(contents, permission, details.requestingUrl, details.isMainFrame)));
    ses.setPermissionCheckHandler((contents, permission, url, details) =>
      (permission === 'clipboard-sanitized-write' && trustedDocument(url)) ||
      roomClipboardRead(contents, permission, details.requestingUrl, details.isMainFrame));
    ses.on('will-download', (event, item, contents, frame) => {
      // Validate the actual initiator, not the number of mounted preview frames.
      const main = win && !win.isDestroyed() && contents === win.webContents &&
        frame && frame === contents.mainFrame && trustedDocument(frame.url) && item.getInitiatorOrigin() === origin;
      const name = main && allowedDownload(origin, item.getURLChain(), item.getFilename(), item.getMimeType());
      if (!name) { event.preventDefault(); eventLogDownload('refused'); return; }
      // Electron opens its native Save As dialog. No default path is silently used.
      const options = { title: 'Save ZeroCollab download', defaultPath: name,
        buttonLabel: 'Save', properties: ['showOverwriteConfirmation'] };
      item.setSaveDialogOptions(options);
      const destination = dialog.showSaveDialogSync(win, options);
      if (!destination) { event.preventDefault(); eventLogDownload('cancelled'); return; }
      const chosen = path.basename(destination);
      if (!path.isAbsolute(destination) || allowedDownload(origin, item.getURLChain(), chosen, item.getMimeType()) !== chosen) {
        event.preventDefault(); eventLogDownload('refused-save-name'); return;
      }
      // setSavePath must run within this synchronous will-download callback.
      item.setSavePath(destination);
      item.once('done', (_, state) => {
        if (state !== 'completed') { eventLogDownload(state); return; }
        try {
          writeFileSync(item.getSavePath() + ':Zone.Identifier', '[ZoneTransfer]\r\nZoneId=3\r\n');
          eventLogDownload('saved-with-zone');
        } catch {
          eventLogDownload('zone-write-failed');
          void dialog.showMessageBox(win, { type: 'error', message: 'The download was saved, but Windows could not mark its origin.',
            detail: 'Treat this file as untrusted. ZeroCollab has not opened it.' });
        }
      });
    });
    ses.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
      let allowed = details.url === loadingURL || details.url === handoverURL || updateAssetURLs.has(details.url);
      try { const u = new URL(details.url); allowed ||= u.origin === origin ||
        (u.protocol === 'ws:' && origin && 'http://' + u.host === origin) ||
        (u.protocol === 'blob:' && u.origin === origin) ||
        ((u.protocol === 'blob:' || u.protocol === 'data:') && details.resourceType === 'image'); } catch {}
      callback({ cancel: !allowed });
    });
    creatingWindow = true;
    const icon = nativeImage.createFromPath(path.join(root, 'zerocollab.ico'));
    if (icon.isEmpty()) throw Error('Desktop icon is missing');
    const savedWindow = uiState.window();
    const initialBounds = savedWindow ? visibleBounds(savedWindow) : { ...DEFAULT_CONTENT_SIZE, useContentSize: true };
    Menu.setApplicationMenu(null);
    win = new BrowserWindow({ ...initialBounds, show: false, backgroundColor: '#0c0e10', title: windowCaption, icon,
      webPreferences: { session: ses, sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false,
        zoomFactor: 1,
        offscreen: process.env.ZC_TEST_CAPTURE === '1',
        preload: path.join(root, 'preload.cjs') } });
    creatingWindow = false;
    win.setMenu(null);
    if (process.env.ZC_TEST_CAPTURE !== '1' && (!savedWindow || savedWindow.maximized)) win.maximize();
    let windowSaveTimer;
    const saveWindow = () => {
      clearTimeout(windowSaveTimer);
      windowSaveTimer = setTimeout(() => {
        if (!win.isDestroyed() && !win.isMinimized()) uiState.saveWindow({ ...win.getNormalBounds(), maximized: win.isMaximized() });
      }, WINDOW_SAVE_DELAY_MS);
    };
    for (const name of ['resize', 'move', 'maximize', 'unmaximize']) win.on(name, saveWindow);
    win.on('closed', () => clearTimeout(windowSaveTimer));
    win.webContents.on('before-input-event', (event, input) => {
      if (input.type === 'keyDown' && (input.control || input.meta) && input.shift && !input.alt && input.key.toLowerCase() === 'u') {
        event.preventDefault(); void showUpdates(); return;
      }
      if (input.type === 'keyDown' && (input.control || input.meta) && !input.alt && input.key.toLowerCase() === 'q') {
        event.preventDefault(); void quitRoom();
      }
    });
    // Present the initial loading document after its first paint, not an empty surface.
    // Keep author capture hidden; late paints cannot revive a failed/stopping launch.
    win.once('ready-to-show', () => {
      if (!['starting', 'choosing', 'running'].includes(launchState) || win.isDestroyed()) return;
      event('window-first-paint');
      if (process.env.ZC_TEST_CAPTURE !== '1') win.show();
    });
    win.on('close', event => { if (!exiting) { event.preventDefault(); win.hide(); eventLogHide(); } });
    win.on('page-title-updated', event => event.preventDefault());
    tray = new Tray(icon);
    tray.setToolTip('ZeroCollab desktop room');
    tray.setContextMenu(Menu.buildFromTemplate([{ label: 'Show ZeroCollab', click: () => { win.show(); win.focus(); } },
      { label: 'Desktop updates...', click: () => { void showUpdates(); } },
      { label: 'Quit ZeroCollab...', click: quitRoom }]));
    tray.on('double-click', () => { win.show(); win.focus(); });
    await win.loadFile(path.join(root, 'loading.html'));
    if (launchState !== 'starting') return;
    if (selectedRoom) launchRoom();
    else {
      launchState = 'choosing';
      await win.loadFile(path.join(root, 'handover.html'));
    }
  }).catch(() => fail('Desktop initialization failed'));
  function launchRoom() {
    if (!selectedRoom || (child && !childClosed) || exiting) return;
    launchState = 'starting'; chooserMessage = 'Opening the existing room...';
    childClosed = false; childCloseCode = null; closingAck = false; handshake = false; refusedBeforeOpen = false;
    generation = 0; returnURL = '';
    selectedData = ''; sendChooserState();
    const env = childEnvironment();
    // Only stderr goes to a local diagnostic file; the private handshake stays on stdout.
    const diagnostics = path.join(artifacts, 'diagnostics');
    let stderrFd;
    try {
      mkdirSync(diagnostics, { recursive: true });
      evidence.backend_log = path.join(diagnostics, 'backend-' + Date.now() + '.log');
      stderrFd = openSync(evidence.backend_log, 'a', 0o600);
    } catch { fail('Backend diagnostic log could not be opened'); return; }
    try {
      child = spawn(path.join(resources, 'python', 'python.exe'), ['-I', '-B', '-u', path.join(resources, 'desktop_host.py'),
        '--owner', String(process.pid), '--profile', path.join(artifacts, 'profile'),
        '--expected-profile', path.join(artifacts, 'profile'),
        '--packages', path.join(resources, 'python', 'Lib', 'site-packages'), '--room-root', selectedRoom],
        { cwd: resources, env, windowsHide: true, stdio: ['pipe', 'pipe', stderrFd] });
    } catch { fail('Python could not launch'); return; }
    finally { closeSync(stderrFd); }
    evidence.child_pid = child.pid;
    evidence.electron = process.versions.electron;
    evidence.chromium = process.versions.chrome;
    event('child-spawned');
    child.on('error', () => fail('Python could not launch'));
    child.stdin.on('error', error => {
      // A refusal/exit can close the private input before the parent sees child 'close'.
      evidence.control_pipe_error = { code: String(error.code || 'unknown').slice(0, 80) }; save();
      if (!exiting && launchState !== 'failed') fail('Backend control connection closed');
    });
    child.on('exit', code => {
      evidence.child_exit = { code, time: new Date().toISOString() }; event('child-exited');
      token = ''; origin = '';
      if (!exiting && launchState !== 'failed') fail('Owned backend exited');
    });
    child.on('close', code => {
      childClosed = true; childCloseCode = code; sendChooserState();
      if (shutdownStarted) { void finishShutdown(); return; }
      evidence.shutdown = { graceful: closingAck && code === 0, child_exit: code, close_ack: closingAck }; save();
    });
    startupTimer = setTimeout(() => { fail('Backend readiness timed out'); child.stdin.end(); }, 30000);
    createInterface({ input: child.stdout, crlfDelay: Infinity }).on('line', async line => {
      if (line.length > 4096) { fail('Invalid backend handshake'); child.stdin.end(); return; }
      let value; try { value = JSON.parse(line); } catch { fail('Invalid backend protocol'); child.stdin.end(); return; }
      if (value.type === 'quit-pending' && shutdownStarted) {
        hostQuitting = true; clearTimeout(shutdownTimer); event('host-finishing-quit'); return;
      }
      if (value.type === 'show-window') { win.show(); win.focus(); return; }
      if (value.type === 'restarting' && handshake && !exiting) {
        if (launchState === 'running') returnURL = win.webContents.getURL();
        launchState = 'updating'; handshake = false; closingAck = false;
        event('backend-restarting');
        await win.loadFile(path.join(root, 'loading.html')); return;
      }
      if (value.type === 'startup-diagnostic') {
        const frames = rows => Array.isArray(rows) && rows.length <= 12 && rows.every(row =>
          row && typeof row === 'object' && /^[A-Za-z0-9_.-]{1,80}$/.test(row.file) &&
          /^[A-Za-z0-9_<>]{1,80}$/.test(row.function) && Number.isSafeInteger(row.line) && row.line > 0);
        if (!['watchdog', 'exception'].includes(value.kind) || !frames(value.stack) || !frames(value.awaiting) ||
          (value.error !== undefined && !/^[A-Za-z_][A-Za-z0-9_]{0,79}$/.test(value.error))) {
          return;
        }
        const clean = rows => rows.map(({ file, line, function: name }) => ({ file, line, function: name }));
        (evidence.startup_diagnostics ||= []).push({ kind: value.kind, time: new Date().toISOString(),
          error: value.error, stack: clean(value.stack), awaiting: clean(value.awaiting) });
        evidence.startup_diagnostics = evidence.startup_diagnostics.slice(-4); save(); return;
      }
      if (value.type === 'closed' && value.supervisor_closed === true && value.runtime_closed === true) { closingAck = true; event('runtime-close-ack'); return; }
      if (value.type === 'failed') {
        evidence.initialization_failure = { type: value.reason, code: value.code, module: value.module,
          parent_pid: value.parent_pid, owner_mismatch: value.owner_mismatch, time: new Date().toISOString() }; save();
      }
      if (launchState === 'failed' || launchState === 'stopping' || launchState === 'closed') return;
      if (value.type === 'progress') {
        evidence.startup_stage = { stage: value.stage, time: new Date().toISOString() };
        (evidence.startup_stages ||= []).push(evidence.startup_stage);
        evidence.startup_stages = evidence.startup_stages.slice(-32); save(); return;
      }
      if (value.type === 'selection') {
        selectedData = value.data; sendChooserState(); return;
      }
      if (value.type === 'failed') {
        const reasons = {
          'room-in-use': 'This room is running in the web app or another desktop backend. Stop the web room and its supervisor, close wrapper windows, then try again.',
          'invalid-room': 'Choose an existing runtime folder with its data and uploads on local fixed drives.',
        };
        refusedBeforeOpen = Object.hasOwn(reasons, value.handover_code);
        fail(reasons[value.handover_code] || 'The room could not start. Open the backend diagnostic log at ' + evidence.backend_log + ', then retry.');
        if (process.env.ZC_TEST_CAPTURE === '1') beginShutdown();
        return; }
      if (value.type !== 'ready' || handshake) return;
      generation = value.generation;
      launchState = 'starting';
      handshake = true; token = value.token; instance = value.instance; origin = 'http://127.0.0.1:' + value.port;
      try {
        await win.loadURL(trustedDocument(returnURL) ? returnURL : origin + '/?view=chat');
        if (launchState !== 'starting') return;
        clearTimeout(startupTimer);
        launchState = 'running';
        (evidence.generations ||= []).push({ generation, backend: value.backend, time: new Date().toISOString() });
        evidence.backend = value.backend;
        evidence.readiness = { instance, port: value.port, version: value.version, agents: value.agents, job_owned_by_parent: true, room: value.room_root, data: value.data, writer_owned: value.writer_owned,
          automatic_tool_approvals: value.automatic_tool_approvals, managed_updates: value.managed_updates,
          native_providers: value.native_providers, stopped: value.stopped, manager_disabled: value.manager_disabled }; save();
        evidence.desktop_version = app.getVersion();
        event('workspace-loaded');
        updater.configure(origin, token);
        // Optional author capture contains renderer pixels only, never token or process protocol.
        if (process.env.ZC_TEST_CAPTURE === '1') setTimeout(async () => {
          if (launchState !== 'running') return;
          event('capture-started');
          try {
            const html = await fetch(origin + '/').then(response => response.text());
            const unauthorized = await fetch(origin + '/api/desktop-instance');
            const reported = await fetch(origin + '/api/version', { headers: { 'X-Session-Token': token } }).then(response => response.json());
            evidence.runtime_version = reported.version;
            evidence.security_checks = { public_html_has_no_session_token: !html.includes(token) && !html.includes('window.__SESSION_TOKEN__='),
              unauthenticated_api_refused: unauthorized.status === 403 };
            const pixels = await Promise.race([win.webContents.capturePage(),
              new Promise((_, reject) => setTimeout(() => reject(Error('Capture timeout')), 3000))]);
            writeFileSync(path.join(artifacts, 'workspace.png'), pixels.toPNG());
            event('renderer-captured');
          } catch (error) { evidence.capture_error = String(error.message).slice(0, 180); event('renderer-capture-failed'); }
          if (launchState === 'running') beginShutdown();
        }, 4000);
      } catch { fail('The room window could not open'); child.stdin.end(); if (process.env.ZC_TEST_CAPTURE === '1') beginShutdown(); }
    });
  }
}
function visibleBounds(saved) {
  const area = screen.getDisplayMatching(saved).workArea;
  const width = Math.min(saved.width, area.width), height = Math.min(saved.height, area.height);
  return { width, height, x: Math.max(area.x, Math.min(saved.x, area.x + area.width - width)),
    y: Math.max(area.y, Math.min(saved.y, area.y + area.height - height)) };
}
function eventLogHide() { event('window-hidden'); }

function eventLogDownload(state) { event('download:' + state); }
