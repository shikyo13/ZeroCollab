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
    if (!input) return;
    const hasContent = input.value.trim().length > 0 || pendingAttachments.length > 0;
    const uploading = [...composerUploads.values()].some(job => job.channel === activeChannel && job.state === 'uploading');
    document.getElementById('send').disabled = uploading;
    group?.setAttribute('aria-busy', String(uploading));
    group?.classList.toggle('inactive', !hasContent);
}

function sendMessage() {
    if (window.chatSessionReadonly?.(activeChannel)) return;
    if ([...composerUploads.values()].some(job => job.channel === activeChannel && job.state === 'uploading')) return;
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
        attachments: [...pendingAttachments, ...(composerLinks.get(activeChannel) || []).filter(a => text.includes(a.url))].map(a => ({
            path: a.path,
            name: a.name,
            url: a.url,
            ...(a.kind ? { kind: a.kind, size: a.size, type: a.type } : {}),
            ...(a.note ? { note: a.note } : {}),
            ...(a.source ? { source: a.source } : {}),
        })),
    };
    if (replyingTo) {
        payload.reply_to = replyingTo.id;
    }

    sendServerEvent(payload);
    composerLinks.delete(activeChannel);

    resetComposerInput(input);
    restartVoiceAfterEdit();
    clearAttachments();
    cancelReply();
    updateSendButton();
    input.focus();
}

// Uploads are scoped to the original draft, including a session switch mid-upload.
const composerUploads = new Map();
const composerLinks = new Map();
let composerUploadId = 0;

function setupPaste() {
    document.getElementById('input').addEventListener('paste', e => {
        const files = [...(e.clipboardData?.items || [])].filter(item => item.kind === 'file').map(item => item.getAsFile()).filter(Boolean);
        if (!files.length) return; // ordinary text and URL pastes keep their native behavior
        e.preventDefault();
        void uploadComposerFiles(files);
    });
}

function setupDragDrop() {
    const input = document.getElementById('input');
    const footer = input.closest('footer');
    const inChat = target => footer.contains(target) || !!target.closest?.('#timeline');
    const dropzone = document.getElementById('dropzone');
    dropzone.querySelector('.dropzone-inner').textContent = 'Drop images, videos or files into chat';
    const hint = footer.querySelector('.input-hint');
    if (hint) hint.textContent = 'Enter to send · Shift+Enter for newline · Paste/drop images, videos or files';
    const picker = document.createElement('input');
    picker.type = 'file'; picker.multiple = true; picker.hidden = true;
    const attach = document.createElement('button');
    attach.id = 'attach-files'; attach.type = 'button'; attach.textContent = '＋';
    attach.title = 'Attach images, videos or files'; attach.setAttribute('aria-label', attach.title);
    attach.addEventListener('click', () => { if (!window.chatSessionReadonly?.(activeChannel)) picker.click(); });
    picker.addEventListener('change', () => { void uploadComposerFiles([...picker.files]); picker.value = ''; });
    document.getElementById('input-row').insertBefore(attach, input);
    footer.appendChild(picker);
    const status = document.createElement('div');
    status.id = 'composer-uploads'; status.setAttribute('aria-live', 'polite');
    footer.insertBefore(status, document.getElementById('input-row'));
    let dragCount = 0;
    document.addEventListener('dragenter', e => {
        if (!e.dataTransfer?.types?.includes('Files') || window.chatSessionReadonly?.(activeChannel)) return;
        if (!inChat(e.target) && dropzone.classList.contains('hidden')) return;
        e.preventDefault(); dragCount++; dropzone.classList.remove('hidden');
    });
    document.addEventListener('dragleave', e => {
        if (dropzone.classList.contains('hidden')) return;
        if (--dragCount <= 0) { dragCount = 0; dropzone.classList.add('hidden'); }
    });
    document.addEventListener('dragover', e => {
        if (!e.dataTransfer?.types?.includes('Files')) return;
        const handled = e.defaultPrevented;
        e.preventDefault(); // an OS file must never navigate away from the room
        if (!handled)
            e.dataTransfer.dropEffect = !dropzone.classList.contains('hidden') || inChat(e.target) ? 'copy' : 'none';
    });
    document.addEventListener('drop', e => {
        const inComposer = inChat(e.target) || !dropzone.classList.contains('hidden');
        dragCount = 0; dropzone.classList.add('hidden');
        if (e.defaultPrevented) return; // e.g. Board gallery owns this drop
        if (e.dataTransfer?.types?.includes('Files')) e.preventDefault();
        if (!inComposer || !e.dataTransfer?.files?.length) return;
        e.preventDefault(); void uploadComposerFiles([...e.dataTransfer.files]);
    });
    window.addEventListener('blur', () => { dragCount = 0; dropzone.classList.add('hidden'); });
}

function renderComposerUploads() {
    const status = document.getElementById('composer-uploads');
    if (!status) return;
    status.replaceChildren();
    for (const [id, job] of composerUploads) {
        if (job.channel !== activeChannel) continue;
        const chip = document.createElement('span');
        chip.className = 'composer-upload ' + job.state;
        chip.textContent = job.state === 'error' ? `${job.name}: ${job.error}` : `Uploading ${job.name} · ${job.progress}`;
        if (job.state === 'error') {
            const dismiss = document.createElement('button');
            dismiss.type = 'button'; dismiss.textContent = '×'; dismiss.setAttribute('aria-label', `Dismiss upload error for ${job.name}`);
            dismiss.onclick = () => { composerUploads.delete(id); renderComposerUploads(); };
            chip.appendChild(dismiss);
        }
        status.appendChild(chip);
    }
    updateSendButton(); repositionScrollAnchor();
}

function requestComposerUpload(file, job) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload');
        if (SESSION_TOKEN) xhr.setRequestHeader('X-Session-Token', SESSION_TOKEN);
        xhr.timeout = 10 * 60 * 1000;
        xhr.upload.onprogress = e => {
            job.progress = e.lengthComputable ? Math.round(e.loaded * 100 / e.total) + '%' : 'uploading';
            renderComposerUploads();
        };
        xhr.onload = () => {
            let data;
            try { data = JSON.parse(xhr.responseText); } catch { reject(new Error('The upload server returned an unreadable response')); return; }
            if (xhr.status < 200 || xhr.status >= 300 || !/^\/uploads\/[a-z0-9_-][a-z0-9_.-]*$/i.test(data.url || '')) {
                reject(new Error(data.error || `Upload failed (${xhr.status})`)); return;
            }
            resolve(data);
        };
        xhr.onerror = () => reject(new Error('Upload connection lost. Try attaching the file again.'));
        xhr.ontimeout = () => reject(new Error('Upload timed out. Try attaching the file again.'));
        const form = new FormData(); form.append('file', file); xhr.send(form);
    });
}

// Rebase the captured insertion point when the user types while an upload runs.
function uploadAnchorPosition(anchor, text) {
    const old = anchor.text;
    let prefix = 0, suffix = 0;
    while (prefix < old.length && prefix < text.length && old[prefix] === text[prefix]) prefix++;
    while (suffix < old.length - prefix && suffix < text.length - prefix && old[old.length - 1 - suffix] === text[text.length - 1 - suffix]) suffix++;
    if (anchor.position <= prefix) return anchor.position;
    if (anchor.position >= old.length - suffix) return anchor.position + text.length - old.length;
    return prefix + text.length - prefix - suffix;
}

async function uploadComposerFiles(files) {
    if (!files.length || window.chatSessionReadonly?.(activeChannel)) return;
    const channel = activeChannel;
    const input = document.getElementById('input');
    const anchor = { text: input.value, position: input.selectionStart ?? input.value.length };
    // Register the whole batch before the first await so Send cannot race the next file.
    const jobs = files.map(file => {
        const id = ++composerUploadId;
        const job = { channel, name: file.name || 'Pasted image', state: 'uploading', progress: '0%' };
        composerUploads.set(id, job); return { id, job, file };
    });
    renderComposerUploads();
    for (const { id, job, file } of jobs) {
        try {
            const data = await requestComposerUpload(file, job);
            const attachment = { name: data.name || file.name || 'Pasted image', url: data.url, kind: data.kind || (file.type.startsWith('image/') ? 'image' : 'file'), size: data.size ?? file.size, type: data.type || file.type };
            if (attachment.kind === 'image') {
                if (channel === activeChannel) { pendingAttachments.push(attachment); renderAttachments(); }
                else {
                    const draft = sessionDrafts.get(channel) || { text: '', attachments: [] };
                    draft.attachments.push(attachment); sessionDrafts.set(channel, draft);
                }
            } else {
                const draft = channel === activeChannel ? null : (sessionDrafts.get(channel) || { text: '', attachments: [] });
                const text = draft ? draft.text : input.value;
                const pos = Math.max(0, Math.min(text.length, uploadAnchorPosition(anchor, text)));
                const label = attachment.name.replace(/[\[\]\\\r\n]/g, ' ');
                const link = `[${label}](${attachment.url})`;
                const insertion = (pos && !/\s/.test(text[pos - 1]) ? '\n' : '') + link + '\n';
                const next = text.slice(0, pos) + insertion + text.slice(pos);
                anchor.text = next; anchor.position = pos + insertion.length;
                if (draft) { draft.text = next; sessionDrafts.set(channel, draft); }
                else { input.value = next; input.setSelectionRange(anchor.position, anchor.position); input.dispatchEvent(new Event('input', { bubbles: true })); }
                composerLinks.set(channel, [...(composerLinks.get(channel) || []), attachment]);
            }
            composerUploads.delete(id);
        } catch (error) { job.state = 'error'; job.error = error.message || 'Upload failed'; }
        renderComposerUploads();
    }
}

// Kept for existing image callers, using the same accepted/error path.
async function uploadImage(file) { await uploadComposerFiles(file ? [file] : []); }

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
    queueMicrotask(() => { if (activeChannel === next) renderComposerUploads(); });
    input.dispatchEvent(new Event('input', { bubbles: true }));
};
