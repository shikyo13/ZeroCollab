/* ZeroCollab chat - Message text rendering: markdown, URL safety, links, file paths, code copy buttons, hashtags and HTML escaping.
 * Loaded after chat.js; shares its global state (see index.html script order). */

function renderMarkdown(text) {
    // Protect Windows paths from escape replacement (e.g. \tests → tab, \new → newline)
    const pathSlots = [];
    text = text.replace(/(?:[A-Za-z]:[\\\/]|\\\\[\w.-]+[\\\/])[\w\-.\\ \/]+/g, (m) => {
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
    // Markdown rejects control-character placeholders in ordinary link destinations.
    text = text.replace(/\]\(([^\n)]*)\)/g, (_, dest) => '](' + dest.replace(/\x00P(\d+)\x00/g,
        (_, i) => encodeURI(pathSlots[parseInt(i)])) + ')');
    // Escape angle brackets in non-code text only
    text = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // Restore code blocks (angle brackets inside them stay unescaped)
    text = text.replace(/\x00C(\d+)\x00/g, function(_, i) { return codeSlots[parseInt(i)]; });
    // Parse markdown, then color @mentions, URLs, and file paths in the output
    let html = marked.parse(text);
    // Restore literal paths after Markdown so UNC backslashes are not consumed as escapes.
    html = html.replace(/(?:\x00P|%00P)(\d+)(?:\x00|%00)/g, (_, i) => escapeHtml(pathSlots[parseInt(i)]));
    // Remove wrapping <p> tags for single-line messages to keep them inline
    const trimmed = html.trim();
    if (trimmed.startsWith('<p>') && trimmed.endsWith('</p>') && trimmed.indexOf('<p>', 1) === -1) {
        html = trimmed.slice(3, -4);
    }
    html = colorMentions(html);
    html = linkifyUrls(html);
    html = linkifyUploadImages(html);
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
        const r = await fetch('/api/platform', { headers: { ...(SESSION_TOKEN ? { 'X-Session-Token': SESSION_TOKEN } : {}) } });
        const data = await r.json();
        serverPlatform = data.platform || 'win32';
    } catch (e) { /* fallback to win32 */ }
}

// --- File references ---
// Absolute paths (C:/x/y.py, /home/x/y.py, MSYS /c/x), repository paths (static/chat.js:42) and
// file names (app.py) become links. Absolute paths open Explorer; relative references keep the file panel. Plain text
// stops a path at whitespace, except spaced Windows folders ending in a file extension.
// Inline code and explicit Markdown links may hold absolute paths with spaces anywhere.
const FILE_EXTS = 'py|pyi|js|mjs|cjs|ts|tsx|jsx|css|scss|html|md|json|jsonl|toml|yaml|yml|ini|cfg|txt|ps1|psm1|sh|bat|cmd|cs|csproj|sln|go|rs|java|kt|c|h|cpp|hpp|rb|php|sql|lua|gd|tscn|tres|xml|svg|lock|log';
const FILE_SEG = String.raw`\.?[\w-]+(?:\.[\w-]+)*`;  // never ends in a sentence's full stop
// Spaced folders in prose are unambiguous only with a final file extension.
const FILE_DIR = String.raw`\.?[\w-]+(?:[. ]\(?[\w-]+\)?)*`;
const FILE_WITH_EXT = String.raw`${FILE_SEG}\.[A-Za-z][A-Za-z0-9]{0,7}`;
const FILE_LINE = String.raw`(?::\d+(?:-\d+|:\d+)?|#L\d+(?:-L?\d+)?)?`;
const FILE_REF = new RegExp(String.raw`(?<![\w./\\:@-])(?:` +
    String.raw`(?<uri>file:\/\/[^\s<>"\x27\x60]+?)(?=[.,;!?)]?(?:\s|$))|` +
    String.raw`(?<unc>\\\\${FILE_SEG}[\\/]${FILE_SEG}(?:[\\/]${FILE_SEG})*)|` +
    String.raw`(?<abs>[A-Za-z]:[\\/](?:(?:${FILE_DIR}[\\/])*${FILE_WITH_EXT}|(?:${FILE_SEG}[\\/])*${FILE_SEG})` +
    String.raw`|/(?:[A-Za-z]|Users|home|tmp|opt|var|etc|usr|mnt)/(?:${FILE_SEG}/)*${FILE_SEG})` +
    String.raw`|(?<rel>(?:\.?[\w-]+/)+[\w-]+(?:\.[\w-]+)*\.[A-Za-z][A-Za-z0-9]{0,7})` +
    String.raw`|(?<bare>[\w-]+(?:\.[\w-]+)*\.(?:${FILE_EXTS}))` +
    String.raw`)(?<line>${FILE_LINE})(?![\w/\\])`, 'g');
// Inline code that is a single absolute path, spaces allowed: `C:\Program Files\x\y.exe`.
const CODE_ABS_REF = new RegExp(String.raw`^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/]|file://|/(?:[A-Za-z]|Users|home|tmp|opt|var|etc|usr|mnt)/)[^\n<>"|?*]*?${FILE_LINE}$`);
const FILE_LINE_SUFFIX = new RegExp(FILE_LINE + '$');
const FILE_OPEN_TIMEOUT_MS = 10000;

function absoluteFilePath(ref) {
    let path = (ref || '').replace(FILE_LINE_SUFFIX, '');
    if (/^file:\/\//i.test(path)) {
        try {
            const uri = new URL(path);
            if (!FileReferences.isReferencePath(path)) return null;
            path = decodeURIComponent(uri.pathname);
            if (uri.hostname) path = '//' + uri.hostname + path;
            else path = path.replace(/^\/(?=[A-Za-z]:[\\/])/, '');
        } catch { return null; }
    }
    if (FileReferences.isReferencePath(path) || /^\/\/[^/]+\//.test(path)) return path;
    if (!CODE_ABS_REF.test(path)) return null;
    if (serverPlatform === 'win32') path = path.replace(/^\/([A-Za-z])\//, (_, drive) => drive + ':/');
    return path;
}

function fileRefTitle(ref) {
    return (absoluteFilePath(ref) ? 'Show in Explorer: ' : 'Open in file panel: ') + ref;
}

function fileRefLink(ref, text) {
    const a = document.createElement('a');
    a.className = 'file-link file-ref';
    a.href = '#';
    a.dataset.ref = ref;
    a.title = fileRefTitle(ref);
    a.textContent = text;
    return a;
}

// Replace file references in one text node; inCode accepts capitalised bare names such as README.md.
function linkifyFileText(node, inCode, allowQuoted = true) {
    const text = node.nodeValue;
    if (allowQuoted) {
        const quoted = /"([^"\n]+)"/g;
        let end = 0;
        const pieces = [];
        for (const match of text.matchAll(quoted)) {
            if (!absoluteFilePath(match[1])) continue;
            pieces.push(document.createTextNode(text.slice(end, match.index) + '"'), fileRefLink(match[1], match[1]), document.createTextNode('"'));
            end = match.index + match[0].length;
        }
        if (pieces.length) {
            pieces.push(document.createTextNode(text.slice(end)));
            node.replaceWith(...pieces);
            for (const piece of pieces) if (piece.nodeType === Node.TEXT_NODE) linkifyFileText(piece, inCode, false);
            return;
        }
    }
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
    // File URIs are decoded by absoluteFilePath; other Markdown destinations were URI-encoded.
    if (/^file:\/\//i.test(ref)) return absoluteFilePath(ref) ? ref : null;
    try { ref = decodeURI(ref); } catch { /* keep it encoded */ }
    if (absoluteFilePath(ref)) return ref;
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
        a.title = fileRefTitle(ref);
        a.setAttribute('href', '#');
        a.removeAttribute('target');
    }
    for (const code of tpl.content.querySelectorAll('code')) {
        if (code.closest('pre, a')) continue;
        const text = code.textContent.trim();
        if (absoluteFilePath(text) && !/\s-/.test(text)) {
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
    const path = absoluteFilePath(link.dataset.ref);
    if (path) {
        void openAbsoluteFileRef(link, path);
        return;
    }
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

async function openAbsoluteFileRef(link, path) {
    if (link.getAttribute('aria-busy') === 'true') return;
    let note = link.nextElementSibling;
    if (!note?.classList.contains('file-open-status')) {
        note = document.createElement('span');
        note.className = 'msg-reference-status file-open-status';
        note.setAttribute('role', 'status');
        link.after(note);
    }
    note.textContent = 'Opening in Explorer...';
    link.setAttribute('aria-busy', 'true');
    try {
        const response = await fetch('/api/open-path', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(SESSION_TOKEN ? { 'X-Session-Token': SESSION_TOKEN } : {}) },
            body: JSON.stringify({ path }), signal: AbortSignal.timeout(FILE_OPEN_TIMEOUT_MS),
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || response.statusText || 'The server could not open this path');
        note.remove();
    } catch (error) {
        note.textContent = 'Could not open in Explorer: ' + (error.message || 'The server could not be reached');
    } finally {
        link.removeAttribute('aria-busy');
    }
}

async function openFileExternally(detail, target) {
    try {
        const r = await fetch('/api/file-view/open', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(SESSION_TOKEN ? { 'X-Session-Token': SESSION_TOKEN } : {}) },
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
const CHAT_IMAGE_EXTENSIONS = 'png|jpe?g|gif|webp';
const CHAT_UPLOAD_IMAGE_PATH = new RegExp(`^/uploads/[^/]+\\.(${CHAT_IMAGE_EXTENSIONS})$`, 'i');
const CHAT_BARE_IMAGE = new RegExp(String.raw`(?<![\w/.:@-])/uploads/[a-z0-9_-][a-z0-9_.-]*\.(?:${CHAT_IMAGE_EXTENSIONS})(?:[?#][^\s<>"')\]]*)?(?=$|[\s).,!:;?\]])`, 'gi');

function chatUploadImageUrl(href) {
    try {
        const url = new URL(href, location.href);
        if (url.origin !== location.origin || url.username || url.password) return null;
        if (!CHAT_UPLOAD_IMAGE_PATH.test(url.pathname)) return null;
        return url.href;
    } catch { return null; }
}

// Bare root-relative upload URLs are not autolinked by Markdown. Leave code and existing links alone.
function linkifyUploadImages(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_TEXT, {
        acceptNode: node => node.parentElement?.closest('a, code, pre') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
        let end = 0;
        const parts = [];
        for (const match of node.nodeValue.matchAll(CHAT_BARE_IMAGE)) {
            if (!chatUploadImageUrl(match[0])) continue;
            const link = document.createElement('a');
            link.setAttribute('href', match[0]);
            link.textContent = match[0];
            parts.push(node.nodeValue.slice(end, match.index), link);
            end = match.index + match[0].length;
        }
        if (parts.length) node.replaceWith(...parts, node.nodeValue.slice(end));
    }
    return tpl.innerHTML;
}

document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const link = e.target.closest?.('.msg-text a[href], .msg-attachments a[data-chat-image]');
    if (!link || link.hasAttribute('download') || typeof openImageModal !== 'function') return;
    const url = link.hasAttribute('data-chat-image')
        ? FileReferences.mediaURL(link.getAttribute('href'))?.href
        : chatUploadImageUrl(link.getAttribute('href'));
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
