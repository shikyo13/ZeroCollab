/* ZeroCollab - WebSocket client */

// Session token injected by the server into the HTML page.
// Sent with every API call and WebSocket connection to authenticate.
const SESSION_TOKEN = window.__SESSION_TOKEN__ || "";

let ws = null;
let pendingAttachments = [];
let autoScroll = true;
let reconnectTimer = null;
let username = 'user';
let agentConfig = {};  // { name: { color, label } } — registered instances (used for pills)
let baseColors = {};   // { name: { color, label } } — base agent colors (for message coloring)
let activeMentions = new Set();  // agent names with pre-@ toggled on (for the active channel)
let _channelMentions = {};  // channel -> array of toggled agent names (per-channel memory of activeMentions)
let replyingTo = null;  // { id, sender, text, mention } or null; mention is the '@handle ' Reply inserted
let unreadCount = 0;    // messages received while scrolled up
let lastMessageDate = null;  // track date for dividers (general channel)
let lastMessageDates = Object.create(null);  // { channel: dateString } for per-channel dividers; no prototype, chats can be named constructor
let soundEnabled = false;  // suppress sounds during initial history load
// Chat (session) ids, as app.py _CHANNEL_NAME_RE and project_registry.py SESSION_ID accept them.
const CHANNEL_NAME_RE = /^[a-z0-9][a-z0-9-]{0,19}$/;
const linkedChannel = new URLSearchParams(location.search).get('channel');
let activeChannel = (linkedChannel && CHANNEL_NAME_RE.test(linkedChannel) ? linkedChannel : null) || prefGet('zerocollab-channel') || 'general';
let channelList = [];
let channelUnread = {};  // { channelName: count }
let colorOverrides = prefObject('zerocollab-color-overrides');

// Expose globals that channels.js and the React shell read via window.*
// Using defineProperty so live values are always returned.
Object.defineProperty(window, 'SESSION_TOKEN', { get() { return SESSION_TOKEN; } });
Object.defineProperty(window, 'activeChannel', { get() { return activeChannel; } });
Object.defineProperty(window, 'channelList', { get() { return channelList; }, set(v) { channelList = v; } });
Object.defineProperty(window, 'channelUnread', { get() { return channelUnread; }, set(v) { channelUnread = v; } });
window._setActiveChannel = function(v) { activeChannel = v; };
window._setPendingChannelSwitch = function(v) { pendingChannelSwitch = v; };
// scrollToBottom is set after function definition (see below)
Object.defineProperty(window, 'username', { get() { return username; } });
Object.defineProperty(window, 'agentConfig', { get() { return agentConfig; } });
Object.defineProperty(window, 'ws', { get() { return ws; } });
Object.defineProperty(window, 'soundEnabled', { get() { return soundEnabled; } });
Object.defineProperty(window, 'autoScroll', { get() { return autoScroll; } });

// Timings (ms), distances (px) and counts used across the chat client.
const CONFIG = {
    sessionRejectedCode: 4003,  // websocket close code for a stale session token: reload the page
    reconnectDelayMs: 2000,     // wait before reconnecting a dropped websocket
    historyBatch: 100,          // history messages rendered per task, keeps the page responsive
    historyWindow: 200,         // newest saved messages per chat built into the page on load
    olderBatch: 200,            // earlier messages added per "Show earlier messages" step
    loadOlderPx: 300,           // scrolling this close to the top adds the next earlier batch
    defaultHistory: 50,         // history setting when the field is not a number
    nearBottomPx: 60,           // this close to the bottom counts as "at the bottom" (auto-scroll)
    anchorGapPx: 12,            // jump-to-latest button gap above the composer
    dragEdgePx: 60,             // delete-drag auto-scrolls inside this band at the timeline edges
    dragScrollPx: 8,            // ...by this much
    dragScrollTickMs: 16,       // ...every frame
    scrollSettleFrames: 20,     // frames to hold the scroll position while layout settles
    composerMaxPx: 120,         // the composer grows with its text up to this height
    dictationPollMs: 300,       // composer value check while focused (OS dictation fires no events)
    voiceRestartMs: 250,        // restart voice input once edits pause this long
    mentionLookbackChars: 30,   // how far back from the cursor an @mention is searched
    replyPreviewChars: 100,     // composer reply banner
    quotePreviewChars: 80,      // quoted parent inside a reply bubble
    feedbackMs: 1500,           // copy confirmations and the jump highlight
    dayLabelMs: 1200,           // floating date label after scrolling stops
    toastMs: 4000,
    toastFadeMs: 300,           // matches the .toast transition in style.css
    fadeOutMs: 200,             // matches the .delete-radio.leaving transition in style.css
};

// --- Drag-scroll for overflow containers ---
function enableDragScroll(el) {
    if (el.dataset.dragScroll) return;  // re-renders call this repeatedly; listeners attach once
    el.dataset.dragScroll = '1';
    let isDown = false, startX, scrollLeft;
    el.addEventListener('mousedown', e => {
        if (e.button !== 0) return;  // left-click only
        isDown = true; startX = e.pageX - el.offsetLeft; scrollLeft = el.scrollLeft;
        el.classList.add('drag-scrolling');
    });
    el.addEventListener('mouseleave', () => { isDown = false; el.classList.remove('drag-scrolling'); });
    el.addEventListener('mouseup', () => { isDown = false; el.classList.remove('drag-scrolling'); });
    el.addEventListener('mousemove', e => {
        if (!isDown) return;
        e.preventDefault();
        el.scrollLeft = scrollLeft - (e.pageX - el.offsetLeft - startX);
    });
}

// --- Init ---

function init() {
    // Configure marked for chat-style rendering
    marked.setOptions({
        breaks: true,      // single newline → <br>
        gfm: true,         // GitHub-flavored markdown
    });

    detectPlatform();
    connectWebSocket();
    setupInput();
    setupDragDrop();
    setupPaste();
    setupScroll();
    setupSettingsKeys();
    setupKeyboardShortcuts();
    Channels.init();

    // Dismiss channel edit controls when clicking outside channel bar
    document.addEventListener('click', (e) => {
        if (!e.target.closest('#channel-bar')) {
            document.querySelectorAll('.channel-tab.editing').forEach(t => t.classList.remove('editing'));
        }
    });
}

// Render saved history in small batches, without per-message layout reads.
let historyLoading = true;
let historyReceived = false;
let pendingHistory = [];
let pendingLiveEvents = [];
let historyScheduled = false;
let historyGeneration = 0;
// Saved history can hold thousands of messages across chats, so only the newest
// CONFIG.historyWindow of each chat are built into the page. Every received message stays in
// messageIndex (reply quotes, jumps), and the older ones wait in olderHistory, per chat and
// oldest first, until the reader scrolls up.
// Keyed by chat name, so a Map: chats named like Object members (constructor, __proto__) stay plain keys.
const messageIndex = new Map();
let olderHistory = new Map();
// A task queue yields between batches and also runs in background tabs,
// where animation frames can be suspended.
const historyTasks = new MessageChannel();
historyTasks.port1.onmessage = (event) => {
    if (event.data === historyGeneration) renderHistoryBatch();
};

function setHistoryIndicator(visible) {
    const loader = document.getElementById('loading-indicator');
    if (!loader) return;
    const timeline = document.getElementById('timeline');
    const top = timeline.scrollTop;
    const height = loader.getBoundingClientRect().height;
    loader.classList.toggle('hidden', !visible);
    if (!autoScroll && top > height) {
        timeline.scrollTo({ top: top + loader.getBoundingClientRect().height - height, behavior: 'instant' });
    }
}

function scheduleHistoryRender() {
    if (!historyScheduled) {
        historyScheduled = true;
        historyTasks.port2.postMessage(historyGeneration);
    }
}

function renderHistoryBatch() {
    historyScheduled = false;
    const fragment = document.createDocumentFragment();
    for (const msg of pendingHistory.splice(0, CONFIG.historyBatch)) {
        appendMessage(msg, { history: true, container: fragment });
    }
    document.getElementById('messages').appendChild(fragment);
    if (autoScroll) scrollToBottom(true);
    if (pendingHistory.length) {
        scheduleHistoryRender();
    } else if (historyReceived) {
        historyLoading = false;
        soundEnabled = true;
        setHistoryIndicator(false);
        updateOlderBar();
        const events = pendingLiveEvents;
        pendingLiveEvents = [];
        for (const event of events) handleServerEvent(event);
        if (autoScroll) scrollToBottom(true);
    }
}

// Split received history per chat: the newest CONFIG.historyWindow messages are rendered and
// the rest go to olderHistory. After a reconnect, a chat that already has messages on the page
// keeps them; only what is newer than its oldest shown message is rendered.
function windowHistory() {
    const byChannel = new Map();
    for (const msg of pendingHistory) {
        const channel = msg.channel || 'general';
        if (!byChannel.has(channel)) byChannel.set(channel, []);
        byChannel.get(channel).push(msg);
    }
    const render = new Set();
    olderHistory = new Map();
    for (const [channel, list] of byChannel) {
        const shown = list.findIndex(msg => document.getElementById(`message-${msg.id}`));
        const cut = shown === -1 ? Math.max(0, list.length - CONFIG.historyWindow) : shown;
        olderHistory.set(channel, list.slice(0, cut));
        for (const msg of list.slice(cut)) render.add(msg);
    }
    pendingHistory = pendingHistory.filter(msg => render.has(msg));
}

// Build the next batch of earlier messages of the active chat above the shown ones, keeping
// the reader's place. Returns whether anything was added.
function loadOlderMessages() {
    const older = olderHistory.get(activeChannel);
    if (historyLoading || !older?.length) return false;
    const batch = older.splice(-CONFIG.olderBatch);
    const timeline = document.getElementById('timeline');
    const container = document.getElementById('messages');
    const fragment = document.createDocumentFragment();
    // The batch gets its own date dividers; restore the tracker for live appends afterwards.
    const liveDate = lastMessageDates[activeChannel];
    delete lastMessageDates[activeChannel];
    for (const msg of batch) appendMessage(msg, { history: true, container: fragment });
    const batchEnd = lastMessageDates[activeChannel];
    lastMessageDates[activeChannel] = liveDate;
    // Everything shown moves with the container's bottom edge. Measuring how far it actually moved
    // also covers the browser's own scroll anchoring, which may already have compensated.
    const bottom = container.getBoundingClientRect().bottom;
    // A day that continues into the shown messages keeps one divider, the batch's.
    const firstShown = [...container.children].find(el => (el.dataset.channel || 'general') === activeChannel);
    if (firstShown?.classList.contains('date-divider') && firstShown.dataset.date === batchEnd) firstShown.remove();
    container.prepend(fragment);
    // Hiding the bar after the last batch also changes the height above the reader.
    updateOlderBar();
    timeline.scrollBy({ top: container.getBoundingClientRect().bottom - bottom, behavior: 'instant' });
    dayFloatRefresh();
    return true;
}

function updateOlderBar() {
    const bar = document.getElementById('older-messages');
    if (!bar) return;
    const count = olderHistory.get(activeChannel)?.length || 0;
    bar.classList.toggle('hidden', !count);
    bar.querySelector('.older-count').textContent = `${count.toLocaleString()} more in this chat`;
}
window.updateOlderBar = updateOlderBar;

// Drop deleted or cleared messages from the index and from the not-yet-shown history.
function forgetMessages(matches) {
    for (const [id, msg] of messageIndex) if (matches(msg)) messageIndex.delete(id);
    for (const [channel, list] of olderHistory) olderHistory.set(channel, list.filter(msg => !matches(msg)));
    updateOlderBar();
}

// --- WebSocket ---

// Live events that must wait until saved history has rendered (replayed in order after it).
const HISTORY_BUFFERED_EVENTS = new Set(['message', 'delete', 'clear', 'channel_renamed', 'agent_renamed']);

const SERVER_EVENTS = {
    message: event => {
        if (document.getElementById(`message-${event.data.id}`)) return;
        messageIndex.set(event.data.id, event.data);
        // Play notification sound for new messages from others (not joins, not when focused)
        if (soundEnabled && !document.hasFocus() && event.data.type !== 'join' && event.data.type !== 'leave' && event.data.sender && event.data.sender.toLowerCase() !== username.toLowerCase()) {
            playNotificationSound(event.data.sender);
        }
        appendMessage(event.data);
    },
    agent_renamed: onAgentRenamed,
    agents: event => applyAgentConfig(event.data),
    base_colors: event => { baseColors = event.data || {}; },
    status: event => updateStatus(event.data),
    typing: event => updateTyping(event.agent, event.active),
    settings: event => applySettings(event.data),
    delete: event => handleDeleteBroadcast(event.ids),
    channel_renamed: onChannelRenamed,
    clear: onClear,
    // Server requests full page reload (e.g. after import)
    reload: () => location.reload(),
};

function handleServerEvent(event) {
    if (historyLoading && HISTORY_BUFFERED_EVENTS.has(event.type)) {
        pendingLiveEvents.push(event);
        return;
    }
    if (event.type === 'history') {
        // Rendering waits for history_complete: the window per chat needs the whole history.
        pendingHistory.push(...event.messages);
        for (const msg of event.messages) messageIndex.set(msg.id, msg);
        return;
    }
    if (event.type === 'history_complete') {
        historyReceived = true;
        windowHistory();
        scheduleHistoryRender();
        return;
    }
    // Emit through Hub for modules to subscribe (PR 1 seam)
    Hub.emit(event.type, event);
    // Own properties only: inherited names such as __proto__ are not handlers.
    if (Object.hasOwn(SERVER_EVENTS, event.type)) SERVER_EVENTS[event.type](event);
}

// Send a client event if the socket is open; returns whether it was sent.
function sendServerEvent(payload) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(payload));
    return true;
}

function onAgentRenamed(event) {
    // Migrate active mentions before the agents config rebuild
    if (activeMentions.has(event.old_name)) {
        activeMentions.delete(event.old_name);
        activeMentions.add(event.new_name);
    }
    // Migrate the per-channel remembered toggles too
    for (const ch of Object.keys(_channelMentions)) {
        const arr = _channelMentions[ch];
        const idx = arr.indexOf(event.old_name);
        if (idx !== -1) arr[idx] = event.new_name;
    }
    // Messages not shown yet take the new name when they are built.
    for (const msg of messageIndex.values()) if (msg.sender === event.old_name) msg.sender = event.new_name;
    // Move existing messages (chat and join/leave) to the new name. Matched on the raw
    // sender id: the visible label can differ from the handle.
    const newAvatar = getAvatarSvg(event.new_name);
    const newAgentKey = (resolveAgent(event.new_name.toLowerCase()) || event.new_name).toLowerCase();
    document.querySelectorAll(`${SENDER_MESSAGES}[data-sender="${CSS.escape(event.old_name)}"]`).forEach(el => {
        el.dataset.sender = event.new_name;
        const avatarWrap = el.querySelector('.avatar-wrap');
        if (avatarWrap) {
            avatarWrap.dataset.agent = newAgentKey;
            avatarWrap.querySelector('.avatar').innerHTML = newAvatar;
        }
        applySender(el);
    });
}

// Move a per-channel key in a plain object map, if present.
function renameKey(map, oldName, newName) {
    if (map[oldName] === undefined) return;
    map[newName] = map[oldName];
    delete map[oldName];
}

function onChannelRenamed(event) {
    // Migrate per-channel client state to the new name
    const channelIndex = channelList.indexOf(event.old_name);
    if (channelIndex !== -1) {
        channelList = [...channelList];
        channelList[channelIndex] = event.new_name;
    }
    renameKey(channelUnread, event.old_name, event.new_name);
    renameKey(_channelMentions, event.old_name, event.new_name);
    renameKey(lastMessageDates, event.old_name, event.new_name);
    if (olderHistory.has(event.old_name)) {
        olderHistory.set(event.new_name, olderHistory.get(event.old_name));
        olderHistory.delete(event.old_name);
    }
    for (const msg of messageIndex.values()) {
        if ((msg.channel || 'general') === event.old_name) msg.channel = event.new_name;
    }
    if (pendingChannelSwitch === event.old_name) {
        pendingChannelSwitch = event.new_name;
    }
    // Migrate data-channel on existing DOM elements
    for (const el of document.getElementById('messages').children) {
        if ((el.dataset.channel || 'general') === event.old_name) {
            el.dataset.channel = event.new_name;
        }
    }
    // Update active channel if we were on the renamed one
    if (activeChannel === event.old_name) {
        activeChannel = event.new_name;
        prefSet('zerocollab-channel', event.new_name);
        Store.set('activeChannel', event.new_name);
    }
    filterMessagesByChannel();
    renderChannelTabs();
}

function onClear(event) {
    const clearChannel = event.channel || null;
    const container = document.getElementById('messages');
    if (clearChannel) {
        // Per-channel clear: remove only messages and date dividers from that channel
        const toRemove = [...container.children].filter(el =>
            (el.dataset.id || el.classList.contains('date-divider')) && (el.dataset.channel || 'general') === clearChannel);
        toRemove.forEach(el => el.remove());
        delete lastMessageDates[clearChannel];
        forgetMessages(msg => (msg.channel || 'general') === clearChannel);
        filterMessagesByChannel();
    } else {
        // Full clear (all channels)
        container.innerHTML = '';
        lastMessageDate = null;
        lastMessageDates = Object.create(null);
        messageIndex.clear();
        olderHistory.clear();
        updateOlderBar();
    }
    dayFloatRefresh();
}

function connectWebSocket() {
    historyGeneration++;
    historyScheduled = false;
    historyLoading = true;
    historyReceived = false;
    pendingHistory = [];
    pendingLiveEvents = [];
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(SESSION_TOKEN)}`);

    ws.onopen = () => {
        console.log('WebSocket connected');
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    };

    ws.onmessage = (e) => handleServerEvent(JSON.parse(e.data));

    ws.onclose = (e) => {
        // Server sends 4003 when session token is invalid (server restarted).
        // Auto-reload to pick up the fresh token from the new HTML page.
        if (e.code === CONFIG.sessionRejectedCode) {
            console.warn('Session token rejected (server restarted?) — reloading page...');
            location.reload();
            return;
        }
        console.log('Disconnected, reconnecting in 2s...');
        soundEnabled = false;  // suppress sounds during reconnect history replay
        historyGeneration++;
        historyScheduled = false;
        historyLoading = true;
        historyReceived = false;
        pendingHistory = [];
        pendingLiveEvents = [];
        setHistoryIndicator(true);
        reconnectTimer = setTimeout(connectWebSocket, CONFIG.reconnectDelayMs);
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
        ws.close();
    };
}

// --- Messages ---

function appendMessage(msg, options = {}) {
    // History replay and a live delivery can contain the same saved message.
    if (document.getElementById(`message-${msg.id}`)) return;
    const container = options.container || document.getElementById('messages');

    // Insert date divider if needed
    maybeInsertDateDivider(container, msg);

    const el = document.createElement('div');
    el.className = 'message';
    el.id = `message-${msg.id}`;
    el.dataset.id = msg.id;
    el.dataset.sender = msg.sender;
    const msgChannel = msg.channel || 'general';
    el.dataset.channel = msgChannel;

    if (msg.type === 'join' || msg.type === 'leave') {
        el.classList.add('join-msg');
        el.innerHTML = `<span class="join-dot"></span><span class="join-text"><strong></strong> ${msg.type === 'join' ? 'joined' : 'left'}</span>`;
        applySender(el);
    } else if (msg.type === 'system' || msg.sender === 'system') {
        el.classList.add('system-msg');
        el.innerHTML = `<span class="msg-text">${escapeHtml(msg.text)}</span>`;
    } else {
        const isError = msg.text.startsWith('[') && msg.text.includes('error');
        if (isError) el.classList.add('error-msg');

        let textHtml = styleHashtags(renderMarkdown(msg.text));

        const isSelf = msg.sender.toLowerCase() === username.toLowerCase();
        el.classList.add(isSelf ? 'self' : 'other');

        let attachmentsHtml = '';
        if (msg.attachments && msg.attachments.length > 0) {
            attachmentsHtml = '<div class="msg-attachments">';
            for (const att of msg.attachments) {
                attachmentsHtml += `<img src="${escapeHtml(att.url)}" alt="${escapeHtml(att.name)}" data-note="${escapeHtml(att.note || '')}" data-url="${escapeHtml(att.url)}" onclick="openImageModal(this.dataset.url)">`;
            }
            attachmentsHtml += '</div>';
        }

        // Reply quote (if this message is a reply)
        let replyHtml = '';
        if (msg.reply_to !== undefined && msg.reply_to !== null) {
            // From the index: the parent may be older than the shown window.
            const parent = messageIndex.get(msg.reply_to);
            if (parent) {
                const isJoin = parent.type === 'join' || parent.type === 'leave';
                const hasSender = !isJoin && parent.type !== 'system' && parent.sender !== 'system';
                const parentSender = hasSender ? getAgentDisplayName(parent.sender) : '?';
                const parentText = isJoin ? '' : parent.text || '';
                const truncated = parentText.length > CONFIG.quotePreviewChars ? parentText.slice(0, CONFIG.quotePreviewChars) + '...' : parentText;
                const parentColor = hasSender ? getColor(parent.sender) : 'var(--text-dim)';
                replyHtml = `<div class="reply-quote" onclick="scrollToMessage(${msg.reply_to})"><span class="reply-sender" style="--sender-color: ${escapeHtml(parentColor)}">${escapeHtml(parentSender)}</span> ${escapeHtml(truncated)}</div>`;
            }
        }

        const agentKey = (resolveAgent(msg.sender.toLowerCase()) || msg.sender).toLowerCase();
        const avatarHtml = `<div class="avatar-wrap" data-agent="${escapeHtml(agentKey)}"><div class="avatar">${getAvatarSvg(msg.sender)}</div></div>`;

        el.dataset.rawText = msg.text;
        el.innerHTML = `${isSelf ? '' : avatarHtml}<div class="chat-bubble">${replyHtml}<div class="bubble-header"><span class="msg-sender"></span><span class="msg-time">${msg.time || ''}</span><span class="msg-num">#${msg.id}</span><div class="message-toolbar" role="group" aria-label="Message actions"><button class="create-task-pill" onclick="createTaskFromMessage(${msg.id}); event.stopPropagation();" title="Create a Board task from this message">Create task</button><button class="bubble-copy" onclick="copyMessage(${msg.id}, event)" title="Copy message"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button><button class="reply-btn" onclick="startReply(${msg.id}, event)">Reply</button><button class="delete-btn" onclick="deleteClick(${msg.id}, event)" title="Delete">Delete</button></div></div><div class="msg-text">${textHtml}</div>${attachmentsHtml}</div>`;
        applySender(el);

        // Add copy buttons to code blocks
        addCodeCopyButtons(el);
    }

    // Hide messages from other channels
    if (msgChannel !== activeChannel) {
        el.classList.add('off-channel');
        // Track unread for background channels (skip joins/leaves and initial history load)
        if (!options.history && soundEnabled && msg.type !== 'join' && msg.type !== 'leave') {
            channelUnread[msgChannel] = (channelUnread[msgChannel] || 0) + 1;
            renderChannelTabs();
            // Play soft pluck for cross-channel chat messages from others (only when focused)
            if (document.hasFocus() && msg.type === 'chat' && msg.sender && msg.sender.toLowerCase() !== username.toLowerCase()) {
                playCrossChannelSound();
            }
        }
    }

    container.appendChild(el);

    if (options.history || msgChannel !== activeChannel) return;  // history scrolls once per batch

    if (autoScroll) {
        scrollToBottom();
    } else {
        unreadCount++;
        updateScrollAnchor();
    }
}

function getSenderClass(sender) {
    const s = sender.toLowerCase();
    if (s === 'system') return 'system';
    if (resolveAgent(s)) return 'agent';
    // Check base colors for offline agents
    const base = s.replace(/-\d+$/, '');
    if (base in baseColors) return 'agent';
    return 'user';
}

function resolveAgent(name) {
    const s = name.toLowerCase();
    if (s in agentConfig) return s;
    // Try prefix match: "gemini-cli" → "gemini"
    for (const key of Object.keys(agentConfig)) {
        if (!agentConfig[key].managed_profile && s.startsWith(key)) return key;
    }
    return null;
}

function getAgentDisplayName(sender) {
    const key = sender.toLowerCase();
    return agentConfig[key]?.label || baseColors[key]?.label || sender;
}

function getColor(sender) {
    const s = sender.toLowerCase();
    if (s === 'system') return 'var(--system-color)';
    const resolved = resolveAgent(s);
    if (resolved) {
        if (colorOverrides[resolved]) return colorOverrides[resolved];
        return agentConfig[resolved].color;
    }
    // Check overrides for unresolved names too
    if (colorOverrides[s]) return colorOverrides[s];
    // Fall back to base agent colors (for historical messages from offline agents)
    const base = s.replace(/-\d+$/, '');
    if (colorOverrides[base]) return colorOverrides[base];
    if (base in baseColors) return baseColors[base].color;
    return 'var(--user-color)';
}

function colorMentions(textHtml) {
    // Match any @word — we'll resolve color per match
    return textHtml.replace(/@(\w[\w-]*)/gi, (match, name) => {
        const lower = name.toLowerCase();
        if (lower === 'both' || lower === 'all') {
            return `<span class="mention mention-all">@${name}</span>`;
        }
        const resolved = resolveAgent(lower);
        if (resolved) {
            const color = getColor(resolved);
            return `<span class="mention" style="--mention-color: ${escapeHtml(color)}">@${name}</span>`;
        }
        // Non-agent mention (e.g. @user, @user) — use user color
        return `<span class="mention">@${name}</span>`;
    });
}

function scrollToBottom(instant = false) {
    const timeline = document.getElementById('timeline');
    autoScroll = true;
    timeline.scrollTo({ top: timeline.scrollHeight, behavior: instant ? 'instant' : 'auto' });
    unreadCount = 0;
    updateScrollAnchor();
}
window.scrollToBottom = scrollToBottom;
window.appendMessage = appendMessage;
// Streaming updates must obey the same reading-position preference as messages.
window.followIncomingContent = function () {
    if (autoScroll) scrollToBottom();
    else {
        unreadCount = Math.max(1, unreadCount);
        updateScrollAnchor();
    }
};

function updateScrollAnchor() {
    const anchor = document.getElementById('scroll-anchor');
    if (autoScroll || unreadCount === 0) {
        anchor.classList.add('hidden');
    } else {
        anchor.classList.remove('hidden');
        const badge = anchor.querySelector('.unread-badge');
        if (badge) {
            badge.textContent = unreadCount;
        }
    }
    repositionScrollAnchor();
}

function repositionScrollAnchor() {
    const anchor = document.getElementById('scroll-anchor');
    if (!anchor) return;
    const footer = document.querySelector('#app footer');
    if (footer) {
        anchor.style.bottom = (footer.offsetHeight + CONFIG.anchorGapPx) + 'px';
    }
    const timeline = document.getElementById('timeline');
    if (timeline) {
        const rect = timeline.getBoundingClientRect();
        anchor.style.left = (rect.left + rect.width / 2) + 'px';
    }
}

// --- Agents ---

function applyAgentConfig(data) {
    agentConfig = {};
    for (const [name, cfg] of Object.entries(data)) {
        agentConfig[name.toLowerCase()] = cfg;
        if (cfg.managed_profile) delete colorOverrides[name.toLowerCase()];
    }
    buildStatusPills();
    buildMentionToggles();
    buildSoundSettings();
    // Re-color any messages already rendered (e.g. from a reconnect)
    recolorMessages();
}

// Messages that carry a sender label (chat and join/leave; system notices do not).
const SENDER_MESSAGES = '.message[data-id]:is(.join-msg, .self, .other)';

// Sender label and color live on the message element: CSS derives the name, avatar,
// bubble accent and join dot colors from --sender-color (style.css).
function applySender(el) {
    const name = el.dataset.sender;
    el.style.setProperty('--sender-color', getColor(name));
    const label = el.querySelector('.msg-sender, .join-text strong');
    label.textContent = getAgentDisplayName(name);
    label.title = '@' + name;
}

function recolorMessages() {
    for (const el of document.querySelectorAll(SENDER_MESSAGES)) {
        applySender(el);
        // Re-render markdown with updated mention colors and hashtags
        const textEl = el.querySelector('.msg-text');
        if (textEl && el.dataset.rawText) {
            textEl.innerHTML = styleHashtags(renderMarkdown(el.dataset.rawText));
            addCodeCopyButtons(el);
        }
    }
}

function buildStatusPills() {
    const container = document.getElementById('agent-status');
    container.innerHTML = '';
    for (const [name, cfg] of Object.entries(agentConfig)) {
        const pill = document.createElement('div');
        pill.className = 'status-pill';
        if (cfg.state === 'pending') pill.classList.add('pending');
        pill.id = `status-${name}`;
        pill.title = `@${name}`;  // Tooltip: canonical name for manual @-typing
        pill.style.setProperty('--agent-color', colorOverrides[name] || cfg.color || 'var(--online)');
        pill.innerHTML = `<span class="status-dot"></span><span class="status-label">${escapeHtml(cfg.label || name)}</span>`;
        // Agent names, colors and instructions are managed in Team.
        pill.addEventListener('click', (e) => {
            e.stopPropagation();
            window.dispatchEvent(new Event('workspace-team'));
        });
        container.appendChild(pill);
    }
    enableDragScroll(container);
}

// --- Status ---

function updateStatus(data) {
    for (const [name, info] of Object.entries(data)) {
        if (name === 'paused') continue;
        const pill = document.getElementById(`status-${name}`);
        if (!pill) continue;

        pill.classList.remove('available', 'working', 'offline');
        // Pending pills keep their pending animation (set in buildStatusPills)
        if (!pill.classList.contains('pending')) {
            if (info.busy && info.available) {
                pill.classList.add('working');
            } else if (info.available) {
                pill.classList.add('available');
            } else {
                pill.classList.add('offline');
            }
        }

        // Keep agent color in sync
        if (info.color) pill.style.setProperty('--agent-color', info.color);
    }
}

function updateTyping(agent, active) {
    const indicator = document.getElementById('typing-indicator');
    if (active) {
        indicator.querySelector('.typing-name').textContent = agent;
        indicator.classList.remove('hidden');
        if (autoScroll) scrollToBottom();
    } else {
        indicator.classList.add('hidden');
    }
}

// --- Toast notifications ---

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('toast--visible'));
    setTimeout(() => {
        toast.classList.remove('toast--visible');
        setTimeout(() => toast.remove(), CONFIG.toastFadeMs);
    }, CONFIG.toastMs);
}

// --- Keyboard shortcuts ---

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('image-modal');
        const modalOpen = modal && !modal.classList.contains('hidden');

        if (e.key === 'Escape') {
            if (modalOpen) { closeImageModal(); return; }
            if (replyingTo) { cancelReply(); }
        }
        if (modalOpen && e.key === 'ArrowLeft') { e.preventDefault(); modalPrev(e); }
        if (modalOpen && e.key === 'ArrowRight') { e.preventDefault(); modalNext(e); }

    });
}

// --- Scroll tracking ---

function setupScroll() {
    const timeline = document.getElementById('timeline');
    const messages = document.getElementById('messages');
    const anchor = document.getElementById('scroll-anchor');
    anchor.setAttribute('role', 'button');
    anchor.setAttribute('tabindex', '0');
    anchor.setAttribute('aria-label', 'New messages: jump to latest');
    const label = document.createElement('span');
    label.textContent = 'New messages';
    anchor.append(label);
    anchor.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            scrollToBottom();
        }
    });

    // Earlier messages of the active chat that are not built into the page yet (loadOlderMessages).
    const olderBar = document.createElement('button');
    olderBar.type = 'button';
    olderBar.id = 'older-messages';
    olderBar.className = 'older-messages hidden';
    const olderLabel = document.createElement('span');
    olderLabel.textContent = 'Show earlier messages';
    const olderCount = document.createElement('span');
    olderCount.className = 'older-count';
    olderBar.append(olderLabel, olderCount);
    olderBar.addEventListener('click', loadOlderMessages);
    messages.before(olderBar);

    timeline.addEventListener('wheel', (event) => {
        if (event.deltaY < 0) autoScroll = false;
    }, { passive: true });

    timeline.addEventListener('scroll', () => {
        const distFromBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight;
        autoScroll = distFromBottom < CONFIG.nearBottomPx;

        if (autoScroll) {
            unreadCount = 0;
        } else if (timeline.scrollTop < CONFIG.loadOlderPx) {
            loadOlderMessages();
        }
        updateScrollAnchor();
        dayFloatOnScroll();
    });

    // Keep pinned to bottom when content changes (e.g. images load)
    const resizeObserver = new ResizeObserver(() => {
        if (autoScroll && !historyLoading) {
            scrollToBottom();
        }
    });
    resizeObserver.observe(messages);

    // Reposition scroll-anchor when window resizes or sidebars toggle
    window.addEventListener('resize', repositionScrollAnchor);
    const contentArea = document.getElementById('content-area');
    if (contentArea) {
        new ResizeObserver(repositionScrollAnchor).observe(contentArea);
    }
}

// --- Mention toggles ---

// Called by switchChannel (channels.js) so the sticky @-tag toggles are
// remembered per channel. Prevents carrying a tag (e.g. @codex from #bugfixing)
// into a channel where you meant to tag someone else.
window._onChannelSwitchMentions = function(oldChannel, newChannel) {
    if (oldChannel) _channelMentions[oldChannel] = [...activeMentions];
    activeMentions = new Set(_channelMentions[newChannel] || []);
    // Reflect the swapped state on the toggle buttons
    for (const btn of document.querySelectorAll('.mention-toggle')) {
        btn.classList.toggle('active', activeMentions.has(btn.dataset.agent));
    }
    if (typeof updateSchedulePopoverState === 'function') updateSchedulePopoverState();
};

function buildMentionToggles() {
    const container = document.getElementById('mention-toggles');
    container.innerHTML = '';

    // Prune stale mentions for agents no longer in config
    for (const name of activeMentions) {
        if (!(name in agentConfig)) activeMentions.delete(name);
    }

    for (const [name, cfg] of Object.entries(agentConfig)) {
        if (cfg.state === 'pending') continue;  // skip pending instances
        const btn = document.createElement('button');
        btn.className = 'mention-toggle';
        btn.dataset.agent = name;
        btn.textContent = `@${cfg.label || name}`;
        btn.title = `@${name}`;  // Tooltip: canonical name
        btn.style.setProperty('--agent-color', colorOverrides[name] || cfg.color);
        // Restore active state for mentions that survived the rebuild
        if (activeMentions.has(name)) {
            btn.classList.add('active');
        }
        btn.onclick = () => {
            if (activeMentions.has(name)) {
                activeMentions.delete(name);
                btn.classList.remove('active');
            } else {
                activeMentions.add(name);
                btn.classList.add('active');
            }

        };
        container.appendChild(btn);
    }
    enableDragScroll(container);
}

async function _preserveScroll(fn) {
    const timeline = document.getElementById('timeline');
    if (!timeline) { await fn(); return; }

    // Check if we are at the bottom (with a small buffer)
    const wasAtBottom = autoScroll || (timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < CONFIG.nearBottomPx);
    const topId = _getTopVisibleMsgId();

    // Save the exact pixel offset of the top visible message
    let savedOffset = 0;
    if (topId) {
        const el = document.querySelector(`.message[data-id="${topId}"]`);
        if (el) {
            savedOffset = el.getBoundingClientRect().top - timeline.getBoundingClientRect().top;
        }
    }

    // Disable smooth scrolling for instant correction
    const oldSmooth = timeline.style.scrollBehavior;
    timeline.style.scrollBehavior = 'auto';

    await fn();

    // Continuously correct scroll during sidebar CSS transition
    function correctScroll() {
        if (wasAtBottom) {
            timeline.scrollTop = timeline.scrollHeight;
        } else if (topId) {
            const el = document.querySelector(`.message[data-id="${topId}"]`);
            if (el) {
                const newRect = el.getBoundingClientRect();
                const timelineRect = timeline.getBoundingClientRect();
                timeline.scrollTop += (newRect.top - timelineRect.top) - savedOffset;
            }
        }
    }

    // Correct immediately
    void timeline.scrollHeight;
    correctScroll();

    // Keep correcting each frame while the transition settles
    let frames = 0;
    function tick() {
        correctScroll();
        if (++frames < CONFIG.scrollSettleFrames) requestAnimationFrame(tick);
        else timeline.style.scrollBehavior = oldSmooth;
    }
    requestAnimationFrame(tick);
}
window._preserveScroll = _preserveScroll;

// --- Message to Board task ---

function createTaskFromMessage(msgId) {
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    window.dispatchEvent(new CustomEvent('workspace-create-task', { detail: {
        id: String(msgId), text: el?.dataset.rawText || el?.querySelector('.msg-text')?.textContent || '',
    } }));
}

// --- Start ---

document.addEventListener('DOMContentLoaded', init);
