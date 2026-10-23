/* ZeroCollab chat - Chat history export to and import from a JSON file.
 * Loaded after chat.js; shares its global state (see index.html script order). */

// --- Export / Import ---

async function exportHistory() {
    try {
        const resp = await fetch('/api/export', {
            headers: { ...(SESSION_TOKEN ? { 'X-Session-Token': SESSION_TOKEN } : {}) },
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            showToast(err.error || 'Export failed', 'error');
            return;
        }
        const blob = await resp.blob();
        const disposition = resp.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="(.+?)"/);
        const filename = match ? match[1] : 'zerocollab-export.zip';
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
        const counts = [];
        // Parse counts from manifest if possible, or just show success
        showToast('History exported', 'success');
    } catch (e) {
        showToast('Export failed: ' + e.message, 'error');
    }
}

async function importHistory(input) {
    const file = input.files[0];
    if (!file) return;
    input.value = ''; // Reset so same file can be picked again
    const btn = document.getElementById('import-history-btn');
    const origText = btn.textContent;
    btn.textContent = 'Importing...';
    btn.disabled = true;
    try {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await fetch('/api/import', {
            method: 'POST',
            headers: { ...(SESSION_TOKEN ? { 'X-Session-Token': SESSION_TOKEN } : {}) },
            body: formData,
        });
        const data = await resp.json();
        if (!resp.ok || !data.ok) {
            showToast(data.error || 'Import failed', 'error');
            return;
        }
        // Build result message
        const parts = [];
        const s = data.sections || {};
        if (s.messages) parts.push(`${s.messages.created} messages`);
        const dupes = s.messages?.duplicates || 0;
        let msg = 'Imported ' + parts.join(', ');
        if (dupes > 0) msg += ` (${dupes} duplicates skipped)`;
        if (data.warnings && data.warnings.length > 0) {
            msg += `. ${data.warnings.length} warning(s)`;
        }
        showToast(msg, 'success');
    } catch (e) {
        showToast('Import failed: ' + e.message, 'error');
    } finally {
        btn.textContent = origText;
        btn.disabled = false;
    }
}
