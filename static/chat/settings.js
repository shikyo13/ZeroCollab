/* ZeroCollab chat - Settings bar: apply server settings, auto-save fields, Clear Chat confirm.
 * Loaded after chat.js; shares its global state (see index.html script order). */

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
        btn.textContent = 'Clear chat';
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

// Confirmed (second click or the inline check): clear the active session and close the bar.
function _confirmClearChat() {
    sendServerEvent({ type: 'message', text: '/clear', sender: username, channel: activeChannel });
    _clearClearChatConfirm();
    document.getElementById('settings-bar').classList.add('hidden');
}

function clearChat() {
    if (window.chatSessionReadonly?.(activeChannel)) return;
    const btn = document.getElementById('clear-chat-btn');
    if (!btn) return;

    // Second click -> execute. First click -> inline confirm, matching the
    // End Session pattern elsewhere.
    if (btn.classList.contains('confirming')) {
        _confirmClearChat();
        return;
    }

    btn.textContent = 'Clear chat?';
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
        _confirmClearChat();
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

    sendServerEvent({
        type: 'update_settings',
        data: {
            username: newUsername || 'user',
            subtitle: newSubtitle,
            font: newFont,
            history_limit: newHistory,
            contrast: newContrast,
        }
    });
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
