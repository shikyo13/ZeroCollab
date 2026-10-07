// Project-scoped chat sessions. IDs route messages; titles are presentation only.
'use strict';
const _channelScrollMsg = {};
let _sessionProject = '';
let _sessions = [];
// Closed chats of this project, and pre-project channels no project owns (read-only history).
let _closed = [];
let _history = [];
// A history channel opened from the menu for this visit only; it is never a permanent tab.
let _viewing = '';
let _moreOpen = false;
const CHAT_TITLE_MAX = 40;    // project_registry.py SESSION_TITLE_MAX
const UNREAD_SHOWN_MAX = 99;  // larger unread counts show as "99+"
const SESSION_PREF_PREFIX = 'zerocollab-session:';  // + project id: the last chat opened there
window.chatSessionTitle = id => _sessions.find(s => s.id === id)?.title || id;
window.chatSessionReadonly = id => !_sessions.some(s => s.id === id && !s.readonly);
Object.defineProperty(window, 'activeProjectId', { get: () => _sessionProject });

async function sessionRequest(path, body) {
    const response = await fetch('/api/runtime' + path, { method: 'POST',
        headers: { 'X-Session-Token': window.__SESSION_TOKEN__, 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, project_id: _sessionProject }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
}
// POST /sessions/<id>[/action] for the active project.
function _sessionPath(id, action = '') {
    return '/sessions/' + encodeURIComponent(id) + (action ? '/' + action : '');
}
function _rememberSession(id) {
    prefSet(SESSION_PREF_PREFIX + _sessionProject, id);
}
// Open (writable) chats; a history channel being viewed is the only read-only entry.
function _openSessions() {
    return _sessions.filter(s => !s.readonly);
}
// Publish the session list to chat.js (channelList) and the shared store.
function _syncSessions() {
    window.channelList = _sessions.map(s => s.id);
    Store.set('chatSessions', [..._sessions]);
}
function _getTopVisibleMsgId() {
    const scroll = document.getElementById('timeline');
    const container = document.getElementById('messages');
    if (!scroll || !container) return null;
    const rect = scroll.getBoundingClientRect();
    for (const el of container.children) {
        if (el.classList.contains('off-channel') || !el.dataset.id) continue;
        if (el.getBoundingClientRect().bottom > rect.top) return el.dataset.id;
    }
    return null;
}
function _sessionComposer() {
    const readonly = window.chatSessionReadonly(window.activeChannel);
    const input = document.getElementById('input');
    if (input) { input.disabled = readonly; input.placeholder = readonly ? 'History from before projects is read-only. Open a project chat to reply.' : 'Message the team…'; }
    for (const id of ['send', 'clear-chat-btn', 'mic']) {
        const control = document.getElementById(id);
        if (control) control.disabled = readonly;
    }
    document.body.classList.toggle('chat-session-readonly', readonly);
}
function applyProjectSessions(data) {
    if (!Array.isArray(data.sessions) || !data.project_id) return;
    const initial = !_sessionProject;
    const changed = _sessionProject !== data.project_id;
    // Drop unfinished create/rename controls when their project is no longer active.
    if (changed) document.querySelectorAll('.channel-inline-create').forEach(el => el.remove());
    _sessionProject = data.project_id;
    _closed = Array.isArray(data.closed_sessions) ? data.closed_sessions : [];
    _history = Array.isArray(data.history_sessions) ? data.history_sessions : [];
    if (changed || !_history.some(h => h.id === _viewing)) _viewing = '';
    _sessions = data.sessions.filter(s => !s.readonly);
    const viewed = _history.find(h => h.id === _viewing);
    if (viewed) _sessions.push({ id: viewed.id, title: viewed.title, readonly: true });
    _syncSessions();
    const saved = prefGet(SESSION_PREF_PREFIX + _sessionProject, '');
    const valid = id => _sessions.some(s => s.id === id);
    const linked = new URLSearchParams(location.search).get('channel');
    let next = !changed && valid(window.activeChannel) ? window.activeChannel :
        initial && valid(linked) ? linked : valid(saved) ? saved : valid(data.last_session) ? data.last_session :
        _openSessions()[0]?.id || _sessions[0]?.id || '';
    switchChannel(next, true);
    Store.set('projectId', _sessionProject);
    for (const id of ['channel-add-btn', 'channel-sidebar-add']) {
        const add = document.getElementById(id);
        if (add) add.disabled = !_sessionProject;
    }
}
window.applyProjectSessions = applyProjectSessions;

function _renderSessions(container, sidebar) {
    if (!container) return;
    const existingCreate = container.querySelector('.channel-inline-create');
    const left = container.scrollLeft;
    container.replaceChildren();
    for (const session of _sessions) {
        const name = session.id;
        const tab = document.createElement('div');
        tab.className = (sidebar ? 'channel-sidebar-row' : 'channel-tab') + (name === window.activeChannel ? ' active' : '');
        tab.dataset.channel = name;
        const label = document.createElement('button');
        label.className = sidebar ? 'channel-sidebar-row-label' : 'channel-tab-label';
        label.textContent = session.title + (session.readonly ? ' · History' : '');
        label.title = session.readonly ? 'Read-only history from before projects' : session.title + ' (double-click to rename)';
        if (!session.readonly) label.ondblclick = () => _sessionEditor(name, sidebar);
        label.setAttribute('aria-pressed', String(name === window.activeChannel));
        label.onclick = () => switchChannel(name);
        tab.append(label);
        const unread = window.channelUnread[name] || 0;
        if (unread && name !== window.activeChannel) {
            const dot = document.createElement('span'); dot.className = 'channel-unread-dot';
            dot.textContent = unread > UNREAD_SHOWN_MAX ? UNREAD_SHOWN_MAX + '+' : unread; tab.append(dot);
        }
        const actions = document.createElement('span'); actions.className = 'channel-tab-actions';
        if (!session.readonly) {
            const rename = document.createElement('button'); rename.className = 'ch-edit-btn';
            rename.textContent = '✎'; rename.title = 'Rename ' + session.title; rename.setAttribute('aria-label', rename.title);
            rename.onclick = () => _sessionEditor(name, sidebar);
            actions.append(rename);
        }
        const close = document.createElement('button'); close.className = 'ch-delete-btn';
        close.textContent = '×';
        const closeLabel = (session.readonly ? 'Stop viewing ' : 'Close ') + session.title;
        close.title = session.readonly ? closeLabel : closeLabel + ' (history is kept; reopen it from Closed & older chats)';
        close.setAttribute('aria-label', closeLabel);
        close.onclick = () => session.readonly ? _stopViewing() : _closeSession(name, close);
        actions.append(close); tab.append(actions);
        container.append(tab);
    }
    if (existingCreate) container.append(existingCreate);
    // The tab strip scrolls horizontally and would clip a dropdown, so the menu sits after the + button.
    const host = sidebar ? container : container.parentElement;
    host?.querySelectorAll(':scope > .channel-more').forEach(m => m.remove());
    if (host && (_closed.length || _history.length)) host.append(_moreMenu());
    container.scrollLeft = left;
}
function renderChannelTabs() {
    _renderSessions(document.getElementById('channel-tabs'), false);
    renderChannelSidebar();
    _sessionComposer();
}
function renderChannelSidebar() { _renderSessions(document.getElementById('channel-sidebar-list'), true); }
function filterMessagesByChannel() {
    const container = document.getElementById('messages');
    if (!container) return;
    for (const el of container.children) el.classList.toggle('off-channel', (el.dataset.channel || 'general') !== window.activeChannel);
    window.updateOlderBar?.();
}
function switchChannel(name, fromSettings = false) {
    if (!_sessions.some(s => s.id === name) && name !== '') return;
    const prev = window.activeChannel;
    if (name !== prev) {
        const top = _getTopVisibleMsgId(); if (top) _channelScrollMsg[prev] = top;
        window._onSessionDraftSwitch?.(prev, name);
        window._setActiveChannel(name);
        window._onChannelSwitchMentions?.(prev, name);
    }
    window.channelUnread[name] = 0;
    _rememberSession(name);
    filterMessagesByChannel(); renderChannelTabs();
    Store.set('activeChannel', name);
    if (!fromSettings && name && !window.chatSessionReadonly(name)) {
        sessionRequest(_sessionPath(name, 'open'), {}).catch(error => console.warn(error.message));
    }
    if (name === prev) return;
    const saved = _channelScrollMsg[name];
    const el = saved && document.getElementById('message-' + saved);
    if (el) el.scrollIntoView({ block: 'start' }); else window.scrollToBottom();
    if (typeof dayFloatRefresh === 'function') dayFloatRefresh();
}
function _sessionEditor(id, sidebar = false) {
    const container = document.getElementById(sidebar ? 'channel-sidebar-list' : 'channel-tabs');
    if (!container || !_sessionProject || (id && window.chatSessionReadonly(id))) return;
    container.querySelector('.channel-inline-create')?.remove();
    const project = _sessionProject;
    const wrapper = document.createElement('form'); wrapper.className = 'channel-inline-create';
    const input = document.createElement('input'); input.type = 'text'; input.maxLength = CHAT_TITLE_MAX;
    input.value = id ? window.chatSessionTitle(id) : ''; input.placeholder = 'Chat name';
    input.setAttribute('aria-label', id ? 'Rename chat' : 'New chat name');
    const submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = id ? 'Rename' : 'Create';
    const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel'; cancel.onclick = () => wrapper.remove();
    const error = document.createElement('span'); error.setAttribute('role', 'alert');
    wrapper.append(input, submit, cancel, error);
    wrapper.onsubmit = async e => {
        e.preventDefault(); const title = input.value.trim();
        if (!title || submit.disabled) return;
        if (project !== _sessionProject) { wrapper.remove(); return; }
        submit.disabled = true; error.textContent = '';
        try {
            const result = await sessionRequest(id ? _sessionPath(id) : '/sessions', { title });
            if (project === _sessionProject) {
                // The settings broadcast and REST response may arrive in either order.
                const target = id || result.id;
                if (target) {
                    const known = _sessions.find(s => s.id === target);
                    if (known) known.title = title; else _addOpenSession(target, title);
                    _syncSessions();
                    switchChannel(target);
                }
                wrapper.remove();
            }
        } catch (e) { error.textContent = e.message; submit.disabled = false; }
    };
    input.onkeydown = e => { if (e.key === 'Escape') { e.preventDefault(); wrapper.remove(); } };
    container.append(wrapper); input.focus(); input.select();
}
function _moreMenu() {
    const menu = document.createElement('details'); menu.className = 'channel-more'; menu.open = _moreOpen;
    menu.ontoggle = () => { _moreOpen = menu.open; };
    const summary = document.createElement('summary'); summary.textContent = 'Closed & older chats';
    const list = document.createElement('div'); list.className = 'channel-more-list'; list.setAttribute('role', 'menu');
    const group = (heading, items, action, verb) => {
        if (!items.length) return;
        const h = document.createElement('div'); h.className = 'channel-more-heading'; h.textContent = heading; list.append(h);
        for (const item of items) {
            const row = document.createElement('button'); row.className = 'channel-more-item'; row.setAttribute('role', 'menuitem');
            const name = document.createElement('span'); name.textContent = item.title;
            const hint = document.createElement('small'); hint.textContent = verb;
            row.append(name, hint); row.onclick = () => action(item.id, row); list.append(row);
        }
    };
    group('Closed in this project', _closed, _reopenSession, 'Reopen');
    group('Before projects (read-only)', _history, _viewHistory, 'View');
    menu.append(summary, list);
    return menu;
}
// New and reopened chats go after the open ones, before any history being viewed.
function _addOpenSession(id, title) {
    if (!_sessions.some(s => s.id === id)) _sessions.splice(_openSessions().length, 0, { id, title, readonly: false });
}
// Run a close/reopen request with its control disabled meanwhile. apply(result) updates
// local state only if the project is unchanged; a failure re-enables the control.
async function _sessionAction(id, action, control, apply) {
    const project = _sessionProject;
    if (control) control.disabled = true;
    try {
        const result = await sessionRequest(_sessionPath(id, action), {});
        if (project === _sessionProject) apply(result);
    } catch (e) {
        if (control) control.disabled = false;
        showToast(e.message, 'error');
    }
}
function _closeSession(id, button) {
    return _sessionAction(id, 'close', button, () => {
        const session = _sessions.find(s => s.id === id);
        _sessions = _sessions.filter(s => s.id !== id);
        if (session && !_closed.some(c => c.id === id)) _closed.push({ id, title: session.title });
        _syncSessions();
        switchChannel(window.activeChannel === id ? (_openSessions()[0]?.id || '') : window.activeChannel);
    });
}
function _reopenSession(id, row) {
    return _sessionAction(id, 'reopen', row, result => {
        _closed = _closed.filter(c => c.id !== id);
        _addOpenSession(id, result.title);
        _moreOpen = false;
        _syncSessions();
        switchChannel(id);
    });
}
function _viewHistory(id) {
    const item = _history.find(h => h.id === id);
    if (!item) return;
    _sessions = [..._openSessions(), { id: item.id, title: item.title, readonly: true }];
    _viewing = id; _moreOpen = false;
    _syncSessions();
    switchChannel(id);
}
function _stopViewing() {
    const id = _viewing;
    _viewing = '';
    _sessions = _openSessions();
    _syncSessions();
    if (window.activeChannel === id) switchChannel(_sessions[0]?.id || ''); else renderChannelTabs();
}
document.addEventListener('click', e => {
    if (!_moreOpen || e.target.closest?.('.channel-more')) return;
    _moreOpen = false;
    document.querySelectorAll('.channel-more').forEach(m => { m.open = false; });
});
// Rename is the tab's ✎ button or double-click; chats are closed (×), never deleted.
function showChannelCreateDialog() { _sessionEditor(null, document.body.classList.contains('channels-in-sidebar')); }

// ---------------------------------------------------------------------------
// Sidebar mode toggle + resize grip
// ---------------------------------------------------------------------------

const SIDEBAR_MODE_KEY = 'zerocollab-channel-sidebar-mode';
const SIDEBAR_WIDTH_KEY = 'zerocollab-channel-sidebar-w';
const SIDEBAR_MIN_PX = 140;
const SIDEBAR_MAX_PX = 400;

function setChannelSidebarMode(mode, persist = true) {
    const sidebar = document.getElementById('channel-sidebar');
    if (!sidebar) return;

    const on = mode === 'sidebar';
    document.body.classList.toggle('channels-in-sidebar', on);
    sidebar.classList.toggle('hidden', !on);

    if (persist) prefSet(SIDEBAR_MODE_KEY, mode);
    const setting = document.getElementById('setting-channel-sidebar');
    if (setting && setting.value !== mode) setting.value = mode;

    if (on) renderChannelSidebar();
}

// The width is a CSS variable (#channel-sidebar uses var(--channel-sidebar-w)).
function _setSidebarWidth(panel, px) {
    panel.style.setProperty('--channel-sidebar-w', px + 'px');
}

function setupChannelSidebarGrip() {
    const grip = document.getElementById('channel-sidebar-grip');
    const panel = document.getElementById('channel-sidebar');
    if (!grip || !panel) return;

    let dragging = false;
    let startX = 0;
    let startWidth = 0;
    // body.channel-sidebar-resizing keeps the resize cursor and blocks text selection mid-drag.
    const setDragging = on => {
        dragging = on;
        grip.classList.toggle('dragging', on);
        document.body.classList.toggle('channel-sidebar-resizing', on);
    };

    grip.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startX = e.clientX;
        startWidth = panel.offsetWidth;
        setDragging(true);
    });

    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        // Sidebar is on the left, so dragging right grows it (positive delta).
        const delta = e.clientX - startX;
        _setSidebarWidth(panel, Math.min(Math.max(startWidth + delta, SIDEBAR_MIN_PX), SIDEBAR_MAX_PX));
    });

    document.addEventListener('mouseup', () => {
        if (!dragging) return;
        setDragging(false);
        prefSet(SIDEBAR_WIDTH_KEY, panel.offsetWidth);
    });
}

function _restoreSidebarState() {
    const savedWidth = parseInt(prefGet(SIDEBAR_WIDTH_KEY, ''), 10);
    const panel = document.getElementById('channel-sidebar');
    if (panel && savedWidth >= SIDEBAR_MIN_PX && savedWidth <= SIDEBAR_MAX_PX) _setSidebarWidth(panel, savedWidth);
    const savedMode = prefGet(SIDEBAR_MODE_KEY, 'top');
    setChannelSidebarMode(savedMode, false);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

// Keep Clear Chat the same pixel width as the send-group so its left edge
// aligns with the Send button's left edge. Pure CSS can't match another
// element's measured width, so we sync via JS on load + resize.
function _syncClearChatWidth() {
    const clearBtn = document.getElementById('clear-chat-btn');
    const sendGroup = document.querySelector('#input-row .send-group');
    if (!clearBtn || !sendGroup) return;
    const w = sendGroup.offsetWidth;
    if (w > 0) clearBtn.style.width = w + 'px';
}

function _channelsInit() {
    setupChannelSidebarGrip();
    _restoreSidebarState();

    const setting = document.getElementById('setting-channel-sidebar');
    if (setting) {
        setting.addEventListener('change', () => setChannelSidebarMode(setting.value));
    }

    requestAnimationFrame(_syncClearChatWidth);
    window.addEventListener('resize', _syncClearChatWidth);
}

// ---------------------------------------------------------------------------
// Window exports (for inline onclick in index.html and chat.js callers)
// ---------------------------------------------------------------------------

window.showChannelCreateDialog = showChannelCreateDialog;
window.switchChannel = switchChannel;
window.filterMessagesByChannel = filterMessagesByChannel;
window.renderChannelTabs = renderChannelTabs;
window.setChannelSidebarMode = setChannelSidebarMode;
window.Channels = { init: _channelsInit };
