/* ZeroCollab chat - Composer: keys, autosize, send, image paste/drop/upload, attachments
 * and per-session drafts.
 * Loaded after chat.js; shares its global state (see index.html script order). */

// --- Input ---

const MENU_STEPS = { ArrowUp: -1, ArrowDown: 1 };

// Keyboard handling shared by the @mention and /command menus. Returns the new highlighted
// index, -1 when Escape closed the menu, or null when the key is not a menu key.
function menuKey(e, menuId, itemSelector, index, pick) {
    const menu = document.getElementById(menuId);
    const items = menu.querySelectorAll(itemSelector);
    const step = MENU_STEPS[e.key];
    if (step) {
        e.preventDefault();
        const next = (index + step + items.length) % items.length;
        items.forEach((el, i) => el.classList.toggle('active', i === next));
        return next;
    }
    if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        pick(items[index]);
        return index;
    }
    if (e.key === 'Escape') {
        menu.classList.add('hidden');
        return -1;
    }
    return null;
}

// Empty composer after a send or a /goal hand-off.
function resetComposerInput(input) {
    input.value = '';
    input.style.height = 'auto';
}

function setupInput() {
    const input = document.getElementById('input');

    input.addEventListener('keydown', (e) => {
        if (mentionMenuVisible) {
            const result = menuKey(e, 'mention-menu', '.mention-item', mentionMenuIndex, item => {
                if (item) selectMention(item.dataset.name);
            });
            if (result === -1) mentionMenuVisible = false;
            else if (result !== null) mentionMenuIndex = result;
            if (result !== null) return;
        }
        if (slashMenuVisible) {
            const result = menuKey(e, 'slash-menu', '.slash-item', slashMenuIndex, item => {
                if (item) selectSlashCommand(item.querySelector('.slash-cmd').textContent);
                if (e.key === 'Enter') sendMessage();
            });
            if (result === -1) slashMenuVisible = false;
            else if (result !== null) slashMenuIndex = result;
            if (result !== null) return;
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
        resetComposerInput(input);
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
        // activeMentions holds agent ids. Prefix the public handle, and skip agents the
        // text already mentions by id, current handle or any previous handle.
        const lower = text.toLowerCase();
        const mentioned = n => n && new RegExp(`@${n.replace(/[-]/g, '\\-')}(?![\\w-])`).test(lower);
        const missing = [...activeMentions].filter(id => {
            const cfg = window.agentConfig?.[id] || {};
            return ![id, cfg.handle, ...(cfg.previous_handles || [])].some(mentioned);
        });
        if (missing.length > 0) {
            text = missing.map(id => `@${window.agentConfig?.[id]?.handle || id}`).join(' ') + ' ' + text;
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

    sendServerEvent(payload);

    resetComposerInput(input);
    restartVoiceAfterEdit();
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
        const resp = await fetch('/api/upload', { method: 'POST', headers: { ...(SESSION_TOKEN ? { 'X-Session-Token': SESSION_TOKEN } : {}) }, body: form });
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
            <img src="${escapeHtml(att.url)}" alt="${escapeHtml(att.name)}" data-note="${escapeHtml(att.note || '')}" data-source="${escapeHtml(att.source || '')}" data-url="${escapeHtml(att.url)}" onclick="openImageModal(this.dataset.url)" title="Click to preview">
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
