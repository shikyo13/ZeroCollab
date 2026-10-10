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
        const nameChanged = username !== data.username;
        username = data.username;
        document.getElementById('sender-label').textContent = username;
        document.getElementById('setting-username').value = username;
        if (nameChanged) {
            for (const message of document.querySelectorAll(SENDER_MESSAGES)) applySender(message);
        }
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

// Clears a chat for everyone, the same as typing /clear. The workspace header's chat menu
// asks for confirmation first.
function clearChat(channel = activeChannel) {
    if (!channel || window.chatSessionReadonly?.(channel)) return;
    sendServerEvent({ type: 'message', text: '/clear', sender: username, channel });
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
