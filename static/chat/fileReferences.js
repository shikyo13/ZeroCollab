/* File-reference input recognition. Registration and access belong to the
 * backend; this helper never opens a path or uploads file bytes. */
window.FileReferences = (() => {
    const DRIVE_PATH = /^[a-z]:[\\/]/i;
    const UNC_PATH = /^\\\\[^\\/]+[\\/][^\\/]+(?:[\\/].*)?$/;
    const FILE_URI = /^file:\/\//i;
    const INVALID_CONTROL = /[\u0000-\u001f]/;
    const REFERENCE_URL = /^\/api\/files\/ref\/[0-9a-f]{16}\/[^/]+$/i;
    const UPLOAD_URL = /^\/uploads\/[a-z0-9_-][a-z0-9_.-]*$/i;
    const REFERENCE_TIMEOUT_MS = 30000;
    const MAX_PENDING_FILES = 20;
    const choices = new Map();

    function unquote(value) {
        const trimmed = value.trim();
        return trimmed.startsWith('"') && trimmed.endsWith('"')
            ? trimmed.slice(1, -1) : trimmed;
    }

    function isReferencePath(value) {
        if (!value || INVALID_CONTROL.test(value)) return false;
        if (DRIVE_PATH.test(value) || UNC_PATH.test(value)) return true;
        if (!FILE_URI.test(value)) return false;
        try {
            const uri = new URL(value);
            return uri.protocol === 'file:' && !!uri.pathname && !uri.username && !uri.password;
        } catch {
            return false;
        }
    }

    /** Only intercept a paste made entirely of paths. Mixed prose and ordinary
     * URLs keep the browser's normal text-paste behavior and cursor position. */
    function clipboardPaths(text) {
        if (typeof text !== 'string') return null;
        const lines = text.split(/\r?\n/).map(unquote).filter(Boolean);
        return lines.length && lines.every(isReferencePath) ? lines : null;
    }

    // Keep quotes, spaces and separators for text fallback without changing recognition.
    function clipboardEntries(text) {
        if (!clipboardPaths(text)) return null;
        const entries = [];
        let pending = '';
        for (const line of text.match(/[^\n]*(?:\n|$)/g).filter(Boolean)) {
            pending += line;
            if (!line.trim()) continue;
            entries.push({ reference: unquote(line), fallback: pending });
            pending = '';
        }
        if (pending) entries[entries.length - 1].fallback += pending;
        return entries;
    }

    function mediaURL(value) {
        try {
            const url = new URL(value, location.href);
            return url.origin === location.origin && !url.username && !url.password
                && (UPLOAD_URL.test(url.pathname) || REFERENCE_URL.test(url.pathname)) ? url : null;
        } catch { return null; }
    }

    function sessionToken() {
        return window.__SESSION_TOKEN__ || '';
    }

    function authenticatedURL(value) {
        const url = mediaURL(value);
        if (!url) return '';
        const token = sessionToken();
        if (token) url.searchParams.set('token', token);
        return url.pathname + url.search;
    }

    async function register(ref) {
        const token = sessionToken();
        const response = await fetch('/api/files/reference', {
            method: 'POST', headers: { 'Content-Type': 'application/json',
                ...(token ? { 'X-Session-Token': token } : {}) },
            body: JSON.stringify({ ref }), signal: AbortSignal.timeout(REFERENCE_TIMEOUT_MS),
        });
        const data = await response.json();
        if (!response.ok || !REFERENCE_URL.test(data.url || '') || !['image', 'video', 'file'].includes(data.kind))
            throw new Error(data.error || 'The file reference server returned an invalid response');
        return data;
    }

    function button(label, action) {
        const node = document.createElement('button');
        node.type = 'button'; node.textContent = label; node.addEventListener('click', action);
        return node;
    }

    function renderChoice(channel, upload, changed) {
        window.ReleaseComposing?.changed();
        const host = document.getElementById('composer-file-choice');
        if (!host) return;
        host.replaceChildren();
        const choice = choices.get(channel);
        if (!choice) return;
        const summary = document.createElement('strong');
        summary.textContent = choice.files.map(file => file.name).join(', ');
        const hint = document.createElement('span');
        hint.textContent = 'Link the original: paste its path (Explorer: Ctrl+Shift+C). Nothing has been copied.';
        if (choice.overflow) hint.textContent += ` Only the first ${MAX_PENDING_FILES} files are selected.`;
        const actions = document.createElement('div'); actions.className = 'composer-file-actions';
        actions.append(button('Paste a path', async () => {
            try { await choice.paste(); }
            catch (error) {
                hint.textContent = error.message || 'Copy the path first: in Explorer select it and press Ctrl+Shift+C.';
                document.getElementById('input').focus();
            }
        }),
            button('Upload a copy', () => {
                choices.delete(channel); renderChoice(channel, upload, changed); changed();
                void upload(choice.files);
            }), button('Dismiss', () => { choices.delete(channel); renderChoice(channel, upload, changed); changed(); }));
        host.append(summary, hint, actions);
    }

    function offerFiles(channel, files, upload, changed, paste) {
        if (!files.length) return;
        const batch = [...(choices.get(channel)?.files || []), ...files];
        choices.set(channel, { files: batch.slice(0, MAX_PENDING_FILES), overflow: batch.length > MAX_PENDING_FILES, paste });
        renderChoice(channel, upload, changed); changed();
    }

    function clearChoice(channel, upload, changed) {
        choices.delete(channel); renderChoice(channel, upload, changed); changed();
    }

    // Probe only after a referenced media element fails. Do not download or poll originals.
    async function explainFailure(node, value) {
        if (!REFERENCE_URL.test(mediaURL(value)?.pathname || '')) return;
        const note = document.createElement('span'); note.className = 'msg-reference-status';
        note.textContent = 'The original file is unavailable. It may have moved or been deleted.';
        node.after(note);
        try {
            const response = await fetch(authenticatedURL(value), {
                headers: { Range: 'bytes=0-0' }, signal: AbortSignal.timeout(REFERENCE_TIMEOUT_MS),
            });
            if (!response.ok) note.textContent = (await response.json()).error || note.textContent;
            else {
                if (response.headers.get('X-File-Changed') === '1') note.textContent = 'The original file has changed since it was linked.';
                await response.body?.cancel();
            }
        } catch { /* Keep the useful local-file error if the connection is unavailable. */ }
    }

    return Object.freeze({ clipboardPaths, clipboardEntries, isReferencePath, mediaURL, authenticatedURL, register,
        offerFiles, renderChoice, clearChoice, explainFailure, hasPendingChoice: () => choices.size > 0 });
})();
