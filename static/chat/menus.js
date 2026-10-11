/* ZeroCollab chat - Composer menus: slash commands and @mention autocomplete.
 * Loaded after chat.js; shares its global state (see index.html script order). */

// --- Slash command menu ---

const SLASH_COMMANDS = [
    { cmd: '/goal', desc: 'Set the shared goal, or /goal pause, resume or cancel', broadcast: false },
    { cmd: '/clear', desc: 'Clear messages in current channel', broadcast: false },
];

let slashMenuIndex = 0;
let slashMenuVisible = false;
let mentionMenuIndex = 0;
let mentionMenuVisible = false;
let mentionMenuStart = -1;  // cursor position of the '@'

function updateSlashMenu(text) {
    const menu = document.getElementById('slash-menu');
    if (!text.startsWith('/') || text.includes(' ')) {
        menu.classList.add('hidden');
        slashMenuVisible = false;
        return;
    }

    const query = text.toLowerCase();
    const matches = SLASH_COMMANDS.filter(c => !c.hidden && c.cmd.startsWith(query));

    if (matches.length === 0 || (matches.length === 1 && matches[0].cmd === query)) {
        menu.classList.add('hidden');
        slashMenuVisible = false;
        return;
    }

    menu.innerHTML = '';
    slashMenuIndex = Math.min(slashMenuIndex, matches.length - 1);

    matches.forEach((item, i) => {
        const row = document.createElement('div');
        row.className = 'slash-item' + (i === slashMenuIndex ? ' active' : '');
        row.innerHTML = `<span class="slash-cmd">${escapeHtml(item.cmd)}</span><span class="slash-desc">${escapeHtml(item.desc)}</span>`;
        row.addEventListener('mousedown', (e) => {
            e.preventDefault();
            selectSlashCommand(item.cmd);
        });
        row.addEventListener('mouseenter', () => {
            slashMenuIndex = i;
            menu.querySelectorAll('.slash-item').forEach((el, j) => el.classList.toggle('active', j === i));
        });
        menu.appendChild(row);
    });

    menu.classList.remove('hidden');
    slashMenuVisible = true;
}

function selectSlashCommand(cmd) {
    const input = document.getElementById('input');
    input.value = cmd;
    restartVoiceAfterEdit();
    input.focus();
    document.getElementById('slash-menu').classList.add('hidden');
    slashMenuVisible = false;
}

// --- Mention autocomplete ---

function getMentionCandidates() {
    // Build list: registered agents + "all agents" + username (self) + known humans
    const candidates = [];
    for (const [name, cfg] of Object.entries(agentConfig)) {
        if (cfg.state === 'pending') continue;
        // Insert the public handle; the router maps it (and old handles) to the agent's id.
        candidates.push({ name: cfg.handle || name, label: cfg.label || name, color: cfg.color, leader: name === conversationLeaderId });
    }
    candidates.push({ name: 'all', label: 'Whole team', color: 'var(--accent)' });
    return candidates;
}

function updateMentionMenu() {
    const menu = document.getElementById('mention-menu');
    const input = document.getElementById('input');
    const text = input.value;
    const cursor = input.selectionStart;

    // Don't show if slash menu is active
    if (slashMenuVisible) {
        menu.classList.add('hidden');
        mentionMenuVisible = false;
        return;
    }

    // Find the '@' before cursor that starts this mention
    let atPos = -1;
    for (let i = cursor - 1; i >= 0; i--) {
        if (text[i] === '@') { atPos = i; break; }
        // Allow spaces if we are still matching a multi-word label like "all agents"
        if (!/[\w\-\s]/.test(text[i])) break;
        if (cursor - i > CONFIG.mentionLookbackChars) break;
    }

    if (atPos < 0 || (atPos > 0 && /\w/.test(text[atPos - 1]))) {
        // No @ found, or @ is mid-word (e.g. email)
        menu.classList.add('hidden');
        mentionMenuVisible = false;
        return;
    }

    const query = text.slice(atPos + 1, cursor).toLowerCase();
    mentionMenuStart = atPos;

    const candidates = getMentionCandidates();
    const matches = candidates.filter(c =>
        c.name.toLowerCase().includes(query) || c.label.toLowerCase().includes(query)
    );

    if (matches.length === 0) {
        menu.classList.add('hidden');
        mentionMenuVisible = false;
        return;
    }

    menu.innerHTML = '';
    mentionMenuIndex = Math.min(mentionMenuIndex, matches.length - 1);

    matches.forEach((item, i) => {
        const row = document.createElement('div');
        row.className = 'mention-item' + (i === mentionMenuIndex ? ' active' : '');
        row.dataset.name = item.name;
        row.innerHTML = `<span class="mention-dot" style="--sender-color: ${escapeHtml(item.color)}"></span><span class="mention-name">${escapeHtml(item.label)}</span><span class="mention-handle">@${escapeHtml(item.name)}</span>${item.leader ? '<span class="conversation-leader-badge" title="Current conversation leader">Leader</span>' : ''}`;
        row.addEventListener('mousedown', (e) => {
            e.preventDefault();
            selectMention(item.name);
        });
        row.addEventListener('mouseenter', () => {
            mentionMenuIndex = i;
            menu.querySelectorAll('.mention-item').forEach((el, j) => el.classList.toggle('active', j === i));
        });
        menu.appendChild(row);
    });

    menu.classList.remove('hidden');
    mentionMenuVisible = true;
}

function selectMention(name) {
    const input = document.getElementById('input');
    const text = input.value;
    const cursor = input.selectionStart;
    // Replace from @ to cursor with @name + space
    const before = text.slice(0, mentionMenuStart);
    const after = text.slice(cursor);
    const mention = `@${name} `;
    input.value = before + mention + after;
    restartVoiceAfterEdit();
    const newPos = mentionMenuStart + mention.length;
    input.setSelectionRange(newPos, newPos);
    input.focus();
    document.getElementById('mention-menu').classList.add('hidden');
    mentionMenuVisible = false;
}
