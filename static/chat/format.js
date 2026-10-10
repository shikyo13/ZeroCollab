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
    // A complete link with an angle-bracket destination ([x](<C:/a b/y.py:12>), the Markdown form for
    // paths with spaces) is rewritten with a percent-encoded destination, so no angle bracket survives.
    text = text.replace(/\[([^\[\]\n]*)\]\(<([^<>\n]*)>\)/g, (_, label, dest) => {
        dest = dest.replace(/\x00P(\d+)\x00/g, (_, i) => pathSlots[parseInt(i)]);
        return `[${label}](${encodeURI(dest).replace(/\(/g, '%28').replace(/\)/g, '%29')})`;
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

// --- File references ---
// Absolute paths (C:/x/y.py, /home/x/y.py, MSYS /c/x), repository paths (static/chat.js:42) and
// file names (app.py) become links that open the file in the workspace side panel. Plain text
// stops a path at whitespace; inline code may hold an absolute path with spaces.
const FILE_EXTS = 'py|pyi|js|mjs|cjs|ts|tsx|jsx|css|scss|html|md|json|jsonl|toml|yaml|yml|ini|cfg|txt|ps1|psm1|sh|bat|cmd|cs|csproj|sln|go|rs|java|kt|c|h|cpp|hpp|rb|php|sql|lua|gd|tscn|tres|xml|svg|lock|log';
const FILE_SEG = String.raw`\.?[\w-]+(?:\.[\w-]+)*`;  // never ends in a sentence's full stop
// Folders of an absolute Windows path may hold spaces (Program Files (x86), Microsoft VS Code); the file name may not.
const FILE_DIR = String.raw`\.?[\w-]+(?:[. ]\(?[\w-]+\)?)*`;
const FILE_LINE = String.raw`(?::\d+(?:-\d+|:\d+)?|#L\d+(?:-L?\d+)?)?`;
const FILE_REF = new RegExp(String.raw`(?<![\w./\\:@-])(?:` +
    String.raw`(?<abs>[A-Za-z]:[\\/](?:${FILE_DIR}[\\/])*${FILE_SEG}` +
    String.raw`|/(?:[A-Za-z]|Users|home|tmp|opt|var|etc|usr|mnt)/(?:${FILE_SEG}/)*${FILE_SEG})` +
    String.raw`|(?<rel>(?:\.?[\w-]+/)+[\w-]+(?:\.[\w-]+)*\.[A-Za-z][A-Za-z0-9]{0,7})` +
    String.raw`|(?<bare>[\w-]+(?:\.[\w-]+)*\.(?:${FILE_EXTS}))` +
    String.raw`)(?<line>${FILE_LINE})(?![\w/\\])`, 'g');
// Inline code that is a single absolute path, spaces allowed: `C:\Program Files\x\y.exe`.
const CODE_ABS_REF = new RegExp(String.raw`^(?:[A-Za-z]:[\\/]|/(?:[A-Za-z]|Users|home|tmp|opt|var|etc|usr|mnt)/)[^\n<>"|?*]*?${FILE_LINE}$`);

function fileRefLink(ref, text) {
    const a = document.createElement('a');
    a.className = 'file-link file-ref';
    a.href = '#';
    a.dataset.ref = ref;
    a.title = 'Open ' + ref;
    a.textContent = text;
    return a;
}

// Replace file references in one text node; inCode accepts capitalised bare names such as README.md.
function linkifyFileText(node, inCode) {
    const text = node.nodeValue;
    const parts = [];
    let last = 0;
    for (const m of text.matchAll(FILE_REF)) {
        // In prose, a bare name needs to look like a file: lowercase (app.py) or with a line (Board.tsx:12).
        if (m.groups.bare && !inCode && !m.groups.line && /[A-Z]/.test(m.groups.bare)) continue;
        parts.push(text.slice(last, m.index), fileRefLink(m[0], m[0]));
        last = m.index + m[0].length;
    }
    if (!parts.length) return;
    parts.push(text.slice(last));
    node.replaceWith(...parts.filter(p => p !== ''));
}

// The file reference a Markdown link points at ([app](app.py:12), [x](C:/repo/x.py), file:///...), or null
// for web, mail, in-page, upload and API links.
function localLinkRef(href) {
    let ref = (href || '').trim();
    if (!ref || /^(https?:|mailto:|#|\/uploads\/|\/api\/|\/static\/)/i.test(ref)) return null;
    ref = ref.replace(/^file:\/\/(localhost)?\/(?=[A-Za-z]:)/i, '').replace(/^file:\/\/(localhost)?(?=\/)/i, '');
    try { ref = decodeURI(ref); } catch { /* keep it encoded */ }
    if (/^[a-z][\w+.-]*:\/\//i.test(ref)) return null;  // any other scheme
    if (CODE_ABS_REF.test(ref)) return ref;
    const match = [...ref.matchAll(FILE_REF)];
    return match.length === 1 && match[0][0] === ref ? ref : null;
}

function linkifyPaths(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    for (const a of tpl.content.querySelectorAll('a[href]')) {
        const ref = localLinkRef(a.getAttribute('href'));
        if (!ref) continue;
        a.className = 'file-link file-ref';
        a.dataset.ref = ref;
        a.title = 'Open ' + ref;
        a.setAttribute('href', '#');
        a.removeAttribute('target');
    }
    for (const code of tpl.content.querySelectorAll('code')) {
        if (code.closest('pre, a')) continue;
        const text = code.textContent.trim();
        if (CODE_ABS_REF.test(text) && !/\s-/.test(text)) {
            code.replaceChildren(fileRefLink(text, code.textContent));
        }
    }
    const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_TEXT, {
        acceptNode: n => n.parentElement?.closest('a, pre') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) linkifyFileText(node, !!node.parentElement?.closest('code'));
    return tpl.innerHTML;
}

// The workspace side panel claims the event (preventDefault); without it, open the file in the editor.
function openFileRef(link) {
    const message = link.closest('.message');
    const id = message ? Number(message.dataset.id) : null;
    const detail = {
        tab: 'file',
        ref: link.dataset.ref,
        sender: message?.dataset.sender || '',
        messageId: id,
        time: messageIndex.get(id)?.timestamp || null,
    };
    if (window.dispatchEvent(new CustomEvent('workspace-side-panel', { detail, cancelable: true }))) {
        openFileExternally(detail, 'editor');
    }
}

async function openFileExternally(detail, target) {
    try {
        const r = await fetch('/api/file-view/open', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Session-Token': SESSION_TOKEN },
            body: JSON.stringify({ ref: detail.ref, sender: detail.sender, root: detail.root, target }),
        });
        if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    } catch (err) {
        console.error('Failed to open file:', err);
    }
}

document.addEventListener('click', (e) => {
    const link = e.target.closest?.('a.file-ref');
    if (!link) return;
    e.preventDefault();
    openFileRef(link);
});

// Uploaded image links in prose use the same viewer as attachment thumbnails.
// Keep the real href for copy-link, downloads and intentional modified clicks.
function chatUploadImageUrl(href) {
    try {
        const url = new URL(href, location.href);
        if (url.origin !== location.origin || url.username || url.password) return null;
        if (!/^\/uploads\/[^/]+\.(png|jpe?g|gif|webp)$/i.test(url.pathname)) return null;
        return url.href;
    } catch { return null; }
}

document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const link = e.target.closest?.('.msg-text a[href]');
    if (!link || link.hasAttribute('download') || typeof openImageModal !== 'function') return;
    const url = chatUploadImageUrl(link.getAttribute('href'));
    if (!url) return;
    e.preventDefault();
    openImageModal(url);
});

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
