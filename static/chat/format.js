/* ZeroCollab chat - Message text rendering: markdown, URL safety, links, file paths, code copy buttons, hashtags and HTML escaping.
 * Loaded after chat.js; shares its global state (see index.html script order). */

function renderMarkdown(text) {
    // Protect Windows paths from escape replacement (e.g. \tests → tab, \new → newline)
    const pathSlots = [];
    text = text.replace(/[A-Z]:[\\\/][\w\-.\\ \/]+/g, (m) => {
        pathSlots.push(m);
        return `\x00P${pathSlots.length - 1}\x00`;
    });
    // Unescape literal \n and \t that agents sometimes send as escaped text
    text = text.replace(/\\\\n/g, '\n').replace(/\\n/g, '\n').replace(/\\t/g, '\t');
    // Escape < and > outside of code blocks/inline code so raw HTML
    // can't break layout or cause XSS, while preserving angle brackets
    // inside backtick-delimited code where users expect them to render.
    // Protect fenced code blocks and inline code by replacing them with placeholders
    var codeSlots = [];
    text = text.replace(/(```[\s\S]*?```|`[^`\n]+`)/g, function(match) {
        codeSlots.push(match);
        return '\x00C' + (codeSlots.length - 1) + '\x00';
    });
    // Escape angle brackets in non-code text only
    text = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // Restore code blocks (angle brackets inside them stay unescaped)
    text = text.replace(/\x00C(\d+)\x00/g, function(_, i) { return codeSlots[parseInt(i)]; });
    // Restore paths
    text = text.replace(/\x00P(\d+)\x00/g, (_, i) => pathSlots[parseInt(i)]);
    // Parse markdown, then color @mentions, URLs, and file paths in the output
    let html = marked.parse(text);
    // Remove wrapping <p> tags for single-line messages to keep them inline
    const trimmed = html.trim();
    if (trimmed.startsWith('<p>') && trimmed.endsWith('</p>') && trimmed.indexOf('<p>', 1) === -1) {
        html = trimmed.slice(3, -4);
    }
    html = colorMentions(html);
    html = linkifyUrls(html);
    html = linkifyPaths(html);
    return stripUnsafeUrls(html);
}

// Markdown links and images may carry any scheme; keep only ones that cannot run script.
const SAFE_URL = /^(https?:|mailto:|\/|#|\.{0,2}\/|[^:]*$)/i;
function stripUnsafeUrls(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    for (const el of tpl.content.querySelectorAll('[href], [src]')) {
        for (const attr of ['href', 'src']) {
            const value = el.getAttribute(attr);
            if (value !== null && !SAFE_URL.test(value.trim())) el.removeAttribute(attr);
        }
    }
    return tpl.innerHTML;
}

function linkifyUrls(html) {
    // Match http/https URLs not already inside an <a> tag.
    // We match tags first to skip them, then capture URLs in the same pass.
    return html.replace(/<a\b[^>]*>.*?<\/a>|(?<!["=])(https?:\/\/[^\s<>"')\]]+)/gs, (match, url) => {
        if (url) {
            return `<a href="${url}" target="_blank" rel="noopener">${url}</a>`;
        }
        return match;
    });
}

let serverPlatform = 'win32';  // default, updated on connect
async function detectPlatform() {
    try {
        const r = await fetch('/api/platform', { headers: { 'X-Session-Token': SESSION_TOKEN } });
        const data = await r.json();
        serverPlatform = data.platform || 'win32';
    } catch (e) { /* fallback to win32 */ }
}

function linkifyPaths(html) {
    // Windows paths: E:\foo\bar or E:/foo/bar
    html = html.replace(/(?<!["=\/])([A-Z]):[\\\/][\w\-.\\ \/]+/g, (match) => {
        const escaped = match.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        return `<a class="file-link" href="#" onclick="openPath('${escaped}'); return false;" title="Open in file manager">${match}</a>`;
    });
    // Unix paths: /Users/..., /home/..., /tmp/..., /opt/..., /var/..., /etc/...
    if (serverPlatform !== 'win32') {
        html = html.replace(/(?<!["=\w])(\/(?:Users|home|tmp|opt|var|etc|usr)\/[\w\-.\/ ]+)/g, (match) => {
            const escaped = match.replace(/'/g, "\\'");
            return `<a class="file-link" href="#" onclick="openPath('${escaped}'); return false;" title="Open in file manager">${match}</a>`;
        });
    }
    return html;
}

async function openPath(path) {
    try {
        await fetch('/api/open-path', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({ path: path }),
        });
    } catch (err) {
        console.error('Failed to open path:', err);
    }
}

function addCodeCopyButtons(container) {
    const blocks = container.querySelectorAll('pre');
    for (const pre of blocks) {
        if (pre.querySelector('.code-copy-btn')) continue;
        const btn = document.createElement('button');
        btn.className = 'code-copy-btn';
        btn.textContent = 'copy';
        btn.onclick = async (e) => {
            e.stopPropagation();
            const code = pre.querySelector('code')?.textContent || pre.textContent;
            try {
                await navigator.clipboard.writeText(code);
                btn.textContent = 'copied!';
                setTimeout(() => { btn.textContent = 'copy'; }, CONFIG.feedbackMs);
            } catch (err) {
                btn.textContent = 'failed';
                setTimeout(() => { btn.textContent = 'copy'; }, CONFIG.feedbackMs);
            }
        };
        pre.appendChild(btn);
    }
}


// Style #hashtags in rendered message text
function styleHashtags(html) {
    // "Match and skip" pattern: consume HTML tags first (to skip hex colors in
    // style attributes like color: #da7756), then match real hashtags in text.
    return html.replace(/<[^>]*>|((?:^|\s))(#([a-zA-Z][a-zA-Z0-9_-]{0,39}))\b/g,
        (match, prefix, fullHash, tag) => {
            if (tag === undefined) return match; // HTML tag — skip
            const lower = tag.toLowerCase();
            if (['clear', 'off', 'none', 'end'].includes(lower)) {
                return `${prefix}<span class="msg-hashtag muted">#${tag}</span>`;
            }
            return `${prefix}<span class="msg-hashtag">#${tag}</span>`;
        });
}

// --- Helpers ---


const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

// Safe for element text and quoted attribute values (not for inline JS).
function escapeHtml(text) {
    return String(text ?? '').replace(/[&<>"']/g, ch => HTML_ESCAPES[ch]);
}
window.escapeHtml = escapeHtml;
