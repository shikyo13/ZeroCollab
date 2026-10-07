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
let lastMessageDates = {};  // { channel: dateString } for per-channel dividers
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
        const events = pendingLiveEvents;
        pendingLiveEvents = [];
        for (const event of events) handleServerEvent(event);
        if (autoScroll) scrollToBottom(true);
    }
}

// --- WebSocket ---

function handleServerEvent(event) {
    if (historyLoading && ['message', 'edit', 'message_update', 'delete', 'clear', 'channel_renamed', 'agent_renamed'].includes(event.type)) {
        pendingLiveEvents.push(event);
        return;
    }
    if (event.type === 'history') {
        pendingHistory.push(...event.messages);
        scheduleHistoryRender();
        return;
    }
    if (event.type === 'history_complete') {
        historyReceived = true;
        scheduleHistoryRender();
        return;
    }
    // Emit through Hub for modules to subscribe (PR 1 seam)
    Hub.emit(event.type, event);
    if (event.type === 'message') {
        if (document.getElementById(`message-${event.data.id}`)) return;
        // Play notification sound for new messages from others (not joins, not when focused)
        if (soundEnabled && !document.hasFocus() && event.data.type !== 'join' && event.data.type !== 'leave' && event.data.sender && event.data.sender.toLowerCase() !== username.toLowerCase()) {
            playNotificationSound(event.data.sender);
        }
        appendMessage(event.data);
    } else if (event.type === 'agent_renamed') {
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
    } else if (event.type === 'agents') {
        applyAgentConfig(event.data);
    } else if (event.type === 'base_colors') {
        baseColors = event.data || {};
    } else if (event.type === 'status') {
        updateStatus(event.data);
    } else if (event.type === 'typing') {
        updateTyping(event.agent, event.active);
    } else if (event.type === 'settings') {
        applySettings(event.data);
    } else if (event.type === 'delete') {
        handleDeleteBroadcast(event.ids);
    } else if (event.type === 'channel_renamed') {
        // Migrate per-channel client state to the new name
        const channelIndex = channelList.indexOf(event.old_name);
        if (channelIndex !== -1) {
            channelList = [...channelList];
            channelList[channelIndex] = event.new_name;
        }
        if (channelUnread[event.old_name] !== undefined) {
            channelUnread[event.new_name] = channelUnread[event.old_name];
            delete channelUnread[event.old_name];
        }
        if (_channelMentions[event.old_name] !== undefined) {
            _channelMentions[event.new_name] = _channelMentions[event.old_name];
            delete _channelMentions[event.old_name];
        }
        if (pendingChannelSwitch === event.old_name) {
            pendingChannelSwitch = event.new_name;
        }
        // Migrate data-channel on existing DOM elements
        const container = document.getElementById('messages');
        for (const el of container.children) {
            if ((el.dataset.channel || 'general') === event.old_name) {
                el.dataset.channel = event.new_name;
            }
        }
        // Update per-channel date tracking
        if (lastMessageDates[event.old_name]) {
            lastMessageDates[event.new_name] = lastMessageDates[event.old_name];
            delete lastMessageDates[event.old_name];
        }
        // Update active channel if we were on the renamed one
        if (activeChannel === event.old_name) {
            activeChannel = event.new_name;
            prefSet('zerocollab-channel', event.new_name);
            Store.set('activeChannel', event.new_name);
        }
        filterMessagesByChannel();
        renderChannelTabs();
    } else if (event.type === 'edit') {
        // A message was edited/demoted — re-render it in place
        const updatedMsg = event.message;
        if (updatedMsg && updatedMsg.id != null) {
            const el = document.querySelector(`.message[data-id="${updatedMsg.id}"]`);
            if (el) {
                // Insert a fresh message after the old one, then remove the old
                const placeholder = document.createElement('div');
                el.after(placeholder);
                el.remove();
                // Temporarily hijack container to insert at the right spot
                const container = document.getElementById('messages');
                appendMessage(updatedMsg, { history: true });
                // Move the newly appended message to where the old one was
                const newEl = container.lastElementChild;
                if (newEl && newEl.dataset.id == updatedMsg.id) {
                    placeholder.replaceWith(newEl);
                } else {
                    placeholder.remove();
                }
            }
        }
    } else if (event.type === 'clear') {
        const clearChannel = event.channel || null;
        if (clearChannel) {
            // Per-channel clear: remove only messages from that channel
            const container = document.getElementById('messages');
            const toRemove = [];
            for (const el of container.children) {
                const isDivider = el.classList.contains('date-divider');
                if ((el.dataset.id || isDivider) && (el.dataset.channel || 'general') === clearChannel) {
                    toRemove.push(el);
                }
            }
            toRemove.forEach(el => el.remove());
            // Clean up orphaned date dividers and reset tracking
            delete lastMessageDates[clearChannel];
            filterMessagesByChannel();
        } else {
            // Full clear (all channels)
            document.getElementById('messages').innerHTML = '';
            lastMessageDate = null;
            lastMessageDates = {};
        }
        dayFloatRefresh();
    } else if (event.type === 'reload') {
        // Server requests full page reload (e.g. after import)
        location.reload();
    }
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
            const parentEl = document.querySelector(`.message[data-id="${msg.reply_to}"]`);
            if (parentEl) {
                const parentSender = parentEl.querySelector('.msg-sender')?.textContent || '?';
                const parentText = parentEl.dataset.rawText || parentEl.querySelector('.msg-text')?.textContent || '';
                const truncated = parentText.length > CONFIG.quotePreviewChars ? parentText.slice(0, CONFIG.quotePreviewChars) + '...' : parentText;
                const parentColor = parentEl.querySelector('.msg-sender') ? getColor(parentEl.dataset.sender) : 'var(--text-dim)';
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

// --- Settings ---

let pendingChannelSwitch = null;

function applySettings(data) {
    if (data.title) {
        document.getElementById('room-title').textContent = data.title;
        // Tab title carries both names, so several servers are distinguishable
        document.title = data.subtitle ? data.title + ' - ' + data.subtitle : data.title;
    }
    if (data.subtitle !== undefined) {
        const subEl = document.getElementById('room-subtitle');
        if (subEl) subEl.textContent = data.subtitle;
        const subInput = document.getElementById('setting-subtitle');
        if (subInput) subInput.value = data.subtitle;
    }
    if (data.username) {
        username = data.username;
        document.getElementById('sender-label').textContent = username;
        document.getElementById('setting-username').value = username;
    }
    if (data.font) {
        document.body.classList.remove('font-mono', 'font-serif', 'font-sans');
        document.body.classList.add('font-' + data.font);
        document.getElementById('setting-font').value = data.font;
    }
    if (data.history_limit !== undefined) {
        document.getElementById('setting-history').value = String(data.history_limit);
    }
    if (data.contrast) {
        document.body.classList.toggle('high-contrast', data.contrast === 'high');
        document.getElementById('setting-contrast').value = data.contrast;
    }
    if (Array.isArray(data.sessions)) window.applyProjectSessions?.(data);

}

function toggleSettings() {
    const bar = document.getElementById('settings-bar');
    bar.classList.toggle('hidden');
    document.getElementById('settings-toggle').classList.toggle('active', !bar.classList.contains('hidden'));
    if (!bar.classList.contains('hidden')) {
        document.getElementById('setting-username').focus();
    }
}

function _clearClearChatConfirm() {
    const btn = document.getElementById('clear-chat-btn');
    const confirmEl = document.getElementById('clear-chat-confirm');
    if (confirmEl) confirmEl.remove();
    if (btn) {
        btn.textContent = 'Clear Chat';
        btn.classList.remove('confirming');
    }
    document.removeEventListener('click', _clearChatOutsideClick, true);
}

function _clearChatOutsideClick(e) {
    const btn = document.getElementById('clear-chat-btn');
    const confirmEl = document.getElementById('clear-chat-confirm');
    if (!btn || !confirmEl) return;
    if (!btn.contains(e.target) && !confirmEl.contains(e.target)) {
        _clearClearChatConfirm();
    }
}

function clearChat() {
    if (window.chatSessionReadonly?.(activeChannel)) return;
    const btn = document.getElementById('clear-chat-btn');
    if (!btn) return;

    // Second click -> execute. First click -> inline confirm, matching the
    // End Session pattern elsewhere.
    if (btn.classList.contains('confirming')) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'message', text: '/clear', sender: username, channel: activeChannel }));
        }
        _clearClearChatConfirm();
        document.getElementById('settings-bar').classList.add('hidden');
        return;
    }

    btn.textContent = 'Clear Chat?';
    btn.classList.add('confirming');

    const confirmWrap = document.createElement('span');
    confirmWrap.id = 'clear-chat-confirm';
    confirmWrap.className = 'inline-confirm';
    confirmWrap.innerHTML = `
        <button class="inline-confirm-yes ch-confirm-yes" title="Confirm clear chat">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5 6.5-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="inline-confirm-no ch-confirm-no" title="Cancel">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        </button>
    `;
    btn.parentElement.insertBefore(confirmWrap, btn);

    confirmWrap.querySelector('.ch-confirm-yes').onclick = (e) => {
        e.stopPropagation();
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'message', text: '/clear', sender: username, channel: activeChannel }));
        }
        _clearClearChatConfirm();
        document.getElementById('settings-bar').classList.add('hidden');
    };
    confirmWrap.querySelector('.ch-confirm-no').onclick = (e) => {
        e.stopPropagation();
        _clearClearChatConfirm();
    };

    setTimeout(() => document.addEventListener('click', _clearChatOutsideClick, true), 0);
}

function saveSettings() {
    const newUsername = document.getElementById('setting-username').value.trim();
    const newSubtitle = document.getElementById('setting-subtitle').value.trim();
    const newFont = document.getElementById('setting-font').value;
    const histVal = document.getElementById('setting-history').value;
    const newHistory = histVal === 'all' ? 'all' : (parseInt(histVal, 10) || CONFIG.defaultHistory);
    const newContrast = document.getElementById('setting-contrast').value;

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'update_settings',
            data: {
                username: newUsername || 'user',
                subtitle: newSubtitle,
                font: newFont,
                history_limit: newHistory,
                contrast: newContrast,
            }
        }));
    }
}

function setupSettingsKeys() {
    // Auto-save on blur/Enter for text/number fields
    for (const id of ['setting-username', 'setting-subtitle']) {
        const el = document.getElementById(id);
        el.addEventListener('blur', () => saveSettings());
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                el.blur();
            }
            if (e.key === 'Escape') {
                toggleSettings();
            }
        });
    }

    // Auto-save on change for selects, escape to close
    for (const id of ['setting-font', 'setting-history', 'setting-contrast']) {
        const el = document.getElementById(id);
        el.addEventListener('change', () => {
            // Apply contrast immediately (don't wait for server round-trip)
            if (id === 'setting-contrast') {
                document.body.classList.toggle('high-contrast', el.value === 'high');
            }
            saveSettings();
        });
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                toggleSettings();
            }
        });
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

// --- Input ---

function setupInput() {
    const input = document.getElementById('input');

    input.addEventListener('keydown', (e) => {
        if (mentionMenuVisible) {
            const menu = document.getElementById('mention-menu');
            const items = menu.querySelectorAll('.mention-item');
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                mentionMenuIndex = (mentionMenuIndex - 1 + items.length) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === mentionMenuIndex));
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                mentionMenuIndex = (mentionMenuIndex + 1) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === mentionMenuIndex));
                return;
            }
            if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                e.preventDefault();
                const active = items[mentionMenuIndex];
                if (active) {
                    selectMention(active.dataset.name);
                }
                return;
            }
            if (e.key === 'Escape') {
                menu.classList.add('hidden');
                mentionMenuVisible = false;
                return;
            }
        }
        if (slashMenuVisible) {
            const menu = document.getElementById('slash-menu');
            const items = menu.querySelectorAll('.slash-item');
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                slashMenuIndex = (slashMenuIndex - 1 + items.length) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === slashMenuIndex));
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                slashMenuIndex = (slashMenuIndex + 1) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === slashMenuIndex));
                return;
            }
            if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                e.preventDefault();
                const active = items[slashMenuIndex];
                if (active) selectSlashCommand(active.querySelector('.slash-cmd').textContent);
                if (e.key === 'Enter') sendMessage();
                return;
            }
            if (e.key === 'Escape') {
                menu.classList.add('hidden');
                slashMenuVisible = false;
                return;
            }
        }
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize + slash menu + mention menu + send button state
    function onInputChange() {
        restartVoiceAfterEdit();
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, CONFIG.composerMaxPx) + 'px';
        updateSlashMenu(input.value);
        updateMentionMenu();
        updateSendButton();
    }
    input.addEventListener('input', onInputChange);
    // Voice typing doesn't always fire 'input' — catch with additional events
    input.addEventListener('compositionend', onInputChange);
    input.addEventListener('change', onInputChange);
    // OS dictation can change the value without firing any event, so poll,
    // but only while the composer has focus (dictation needs focus anyway).
    let _lastInputVal = '';
    let _dictationPoll = null;
    input.addEventListener('focus', () => {
        clearInterval(_dictationPoll);
        _dictationPoll = setInterval(() => {
            if (input.value !== _lastInputVal) {
                _lastInputVal = input.value;
                updateSendButton();
            }
        }, CONFIG.dictationPollMs);
    });
    input.addEventListener('blur', () => { clearInterval(_dictationPoll); _dictationPoll = null; });
    updateSendButton();
}

function updateSendButton() {
    const input = document.getElementById('input');
    const group = document.querySelector('.send-group');
    if (!input || !group) return;
    const hasContent = input.value.trim().length > 0 || pendingAttachments.length > 0;
    group.classList.toggle('inactive', !hasContent);
}

function sendMessage() {
    if (window.chatSessionReadonly?.(activeChannel)) return;
    const input = document.getElementById('input');
    let text = input.value.trim();

    if (document.body.classList.contains('modern-workspace') && /^\/goal(?:\s|$)/i.test(text)) {
        window.dispatchEvent(new CustomEvent('workspace-goal', {detail: text.replace(/^\/goal\s*/i, '')}));
        input.value = '';
        input.style.height = 'auto';
        updateSendButton();
        return;
    }

    if (!text && pendingAttachments.length === 0) return;

    // Prepend active mention toggles if the message doesn't already mention them
    // Skip for non-broadcast slash commands (e.g. /clear, /continue)
    let skipMentions = false;
    if (text.startsWith('/')) {
        const cmdWord = text.split(/\s/)[0].toLowerCase();
        const matchedCmd = SLASH_COMMANDS.find(c => c.cmd.startsWith(cmdWord) || cmdWord.startsWith(c.cmd.split(/\s/)[0]));
        if (matchedCmd && !matchedCmd.broadcast) {
            skipMentions = true;
        }
    }
    if (activeMentions.size > 0 && text && !skipMentions) {
        const prefix = [...activeMentions].map(n => `@${n}`).join(' ');
        // Only prepend if user didn't already @mention these agents
        const lower = text.toLowerCase();
        const missing = [...activeMentions].filter(n => !lower.includes(`@${n}`));
        if (missing.length > 0) {
            text = missing.map(n => `@${n}`).join(' ') + ' ' + text;
        }
    }

    const payload = {
        type: 'message',
        text: text,
        sender: username,
        channel: activeChannel,
        project_id: window.activeProjectId,
        attachments: pendingAttachments.map(a => ({
            path: a.path,
            name: a.name,
            url: a.url,
            ...(a.note ? { note: a.note } : {}),
            ...(a.source ? { source: a.source } : {}),
        })),
    };
    if (replyingTo) {
        payload.reply_to = replyingTo.id;
    }

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
    }

    input.value = '';
    restartVoiceAfterEdit();
    input.style.height = 'auto';
    clearAttachments();
    cancelReply();
    updateSendButton();
    input.focus();
}

// --- Image paste/drop ---

function setupPaste() {
    document.addEventListener('paste', async (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;

        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                await uploadImage(item.getAsFile());
            }
        }
    });
}

function setupDragDrop() {
    const dropzone = document.getElementById('dropzone');
    let dragCount = 0;

    document.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCount++;
        if (e.dataTransfer?.types?.includes('Files')) {
            dropzone.classList.remove('hidden');
        }
    });

    document.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dragCount--;
        if (dragCount <= 0) {
            dragCount = 0;
            dropzone.classList.add('hidden');
        }
    });

    document.addEventListener('dragover', (e) => {
        e.preventDefault();
    });

    document.addEventListener('drop', async (e) => {
        e.preventDefault();
        dragCount = 0;
        dropzone.classList.add('hidden');

        const files = e.dataTransfer?.files;
        if (!files) return;

        for (const file of files) {
            if (file.type.startsWith('image/')) {
                await uploadImage(file);
            }
        }
    });
}

async function uploadImage(file) {
    if (window.chatSessionReadonly?.(activeChannel)) return;
    const uploadSession = activeChannel;
    const form = new FormData();
    form.append('file', file);

    try {
        const resp = await fetch('/api/upload', { method: 'POST', headers: { 'X-Session-Token': SESSION_TOKEN }, body: form });
        const data = await resp.json();

        const attachment = { path: data.path, name: data.name, url: data.url };
        if (activeChannel === uploadSession) pendingAttachments.push(attachment);
        else {
            const draft = sessionDrafts.get(uploadSession) || { text: '', attachments: [] };
            draft.attachments.push(attachment); sessionDrafts.set(uploadSession, draft);
        }

        renderAttachments();
    } catch (err) {
        console.error('Upload failed:', err);
    }
}

function renderAttachments() {
    const container = document.getElementById('attachments');
    container.innerHTML = '';

    pendingAttachments.forEach((att, i) => {
        const wrap = document.createElement('div');
        wrap.className = 'attachment-preview';
        wrap.innerHTML = `
            <img src="${escapeHtml(att.url)}" alt="${escapeHtml(att.name)}" data-note="${escapeHtml(att.note || '')}" data-url="${escapeHtml(att.url)}" onclick="openImageModal(this.dataset.url)" title="Click to preview">
            <button class="remove-btn" onclick="removeAttachment(${i})">x</button>
        `;
        container.appendChild(wrap);
    });
    repositionScrollAnchor();
}

function removeAttachment(index) {
    pendingAttachments.splice(index, 1);
    renderAttachments();
}

function clearAttachments() {
    pendingAttachments = [];
    document.getElementById('attachments').innerHTML = '';
    repositionScrollAnchor();
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

    timeline.addEventListener('wheel', (event) => {
        if (event.deltaY < 0) autoScroll = false;
    }, { passive: true });

    timeline.addEventListener('scroll', () => {
        const distFromBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight;
        autoScroll = distFromBottom < CONFIG.nearBottomPx;

        if (autoScroll) {
            unreadCount = 0;
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

// Keep unsent content scoped to a stable session ID, including project switches.
const sessionDrafts = new Map();
window._onSessionDraftSwitch = function(previous, next) {
    const input = document.getElementById('input');
    if (!input) return;
    sessionDrafts.set(previous, { text: input.value, attachments: [...pendingAttachments], reply: replyingTo });
    exitDeleteMode();
    cancelReply();
    const draft = sessionDrafts.get(next);
    replyingTo = draft?.reply || null;
    renderReplyPreview();
    input.value = draft?.text || '';
    pendingAttachments = draft?.attachments ? [...draft.attachments] : [];
    renderAttachments();
    input.dispatchEvent(new Event('input', { bubbles: true }));
};
