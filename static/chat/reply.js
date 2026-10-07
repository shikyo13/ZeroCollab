/* ZeroCollab chat - Copy, reply banner and jump-to-message.
 * Loaded after chat.js; shares its global state (see index.html script order). */

// --- Reply ---

function copyMessage(msgId, event) {
    if (event) event.stopPropagation();
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!el) return;
    const msgText = el.querySelector('.msg-text');
    const html = msgText?.innerHTML || '';
    const markdown = el.dataset.rawText || msgText?.innerText || '';
    const done = () => {
        const btn = el.querySelector('.bubble-copy');
        if (btn) {
            btn.classList.add('copied');
            setTimeout(() => btn.classList.remove('copied'), CONFIG.feedbackMs);
        }
    };
    // Rich HTML + raw markdown — rich editors get HTML, code/markdown editors get source
    if (navigator.clipboard.write) {
        navigator.clipboard.write([new ClipboardItem({
            'text/html': new Blob([html], {type: 'text/html'}),
            'text/plain': new Blob([markdown], {type: 'text/plain'}),
        })]).then(done);
    } else {
        navigator.clipboard.writeText(markdown).then(done);
    }
}

function startReply(msgId, event) {
    if (event) event.stopPropagation();
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!el) return;
    const sender = el.querySelector('.msg-sender')?.textContent?.trim() || '?';
    const text = el.dataset.rawText || el.querySelector('.msg-text')?.textContent || '';
    // Resolve from the raw sender id; the visible label can differ from the handle.
    const resolved = resolveAgent(el.dataset.sender || sender);
    const input = document.getElementById('input');
    removeReplyMention(input);
    replyingTo = { id: msgId, sender, text, mention: addReplyMention(input, resolved) };
    renderReplyPreview();

    // Auto-activate mention chip for the replied-to sender, deactivate others
    if (resolved) {
        for (const btn of document.querySelectorAll('.mention-toggle')) {
            const agent = btn.dataset.agent;
            if (agent === resolved) {
                activeMentions.add(agent);
                btn.classList.add('active');
            } else {
                activeMentions.delete(agent);
                btn.classList.remove('active');
            }
        }
    }

    document.getElementById('input').focus();
}

function renderReplyPreview() {
    let container = document.getElementById('reply-preview');
    if (!replyingTo) {
        if (container) container.remove();
        return;
    }
    if (!container) {
        container = document.createElement('div');
        container.id = 'reply-preview';
        const inputRow = document.getElementById('input-row');
        inputRow.parentNode.insertBefore(container, inputRow);
    }
    const truncated = replyingTo.text.length > CONFIG.replyPreviewChars ? replyingTo.text.slice(0, CONFIG.replyPreviewChars) + '...' : replyingTo.text;
    const color = getColor(replyingTo.sender);
    container.innerHTML = `<span class="reply-preview-label">replying to</span> <span class="reply-preview-sender" style="--sender-color: ${escapeHtml(color)}">${escapeHtml(replyingTo.sender)}</span>: ${escapeHtml(truncated)} <button class="dismiss-btn reply-cancel" onclick="cancelReply()">&times;</button>`;
}

// Replies only reach an agent through an @mention, so Reply addresses the
// agent visibly in the draft. Returns the inserted prefix, or '' if none.
function addReplyMention(input, agent) {
    if (!agent || !input) return '';
    const handle = agent.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const mentioned = new RegExp(`(^|[^\\w-])@${handle}(?![\\w-])`, 'i');
    if (mentioned.test(input.value)) return '';
    const prefix = `@${agent} `;
    input.value = prefix + input.value;
    input.setSelectionRange(input.value.length, input.value.length);
    input.dispatchEvent(new Event('input'));
    return prefix;
}

// Undo addReplyMention only while the inserted prefix is still untouched.
function removeReplyMention(input) {
    const prefix = replyingTo?.mention;
    if (!prefix || !input || !input.value.startsWith(prefix)) return;
    input.value = input.value.slice(prefix.length);
    input.dispatchEvent(new Event('input'));
}

function cancelReply() {
    removeReplyMention(document.getElementById('input'));
    replyingTo = null;
    const el = document.getElementById('reply-preview');
    if (el) el.remove();
}

function scrollToMessage(msgId) {
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('highlight');
    setTimeout(() => el.classList.remove('highlight'), CONFIG.feedbackMs);
}
