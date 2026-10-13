/* Existing authenticated /ws only. No draft, identity or client timestamps leave this tab. */
window.ReleaseComposing = (() => {
    const HEARTBEAT_MS = 5000;
    const COMPOSING_ACTIVITY_MS = 30000;
    const ATTEMPT_ID = /^[A-Za-z0-9_-]{1,64}$/;
    let socket = null, timer = null, readDraft = null, ime = false, attempt = null;
    let locked = false, lastBusy = null, banner = null;
    let chatActivityAt = -Infinity, activityTimer = null;
    const edited = new Set();
    const editable = node => node instanceof Element && node.matches('input:not([type=button]):not([type=submit]):not([type=reset]), textarea, select, [contenteditable]:not([contenteditable=false])');
    function dirtyForms() {
        for (const node of edited) if (!node.isConnected) edited.delete(node);
        return edited.size > 0 || !!document.querySelector('.tm-form-dirty');
    }
    function busy() {
        // No initial idle report before the composer registers its retained draft reader.
        const focused = document.activeElement;
        const otherEditor = editable(focused) && focused.id !== 'input';
        const recentChatActivity = performance.now() - chatActivityAt < COMPOSING_ACTIVITY_MS;
        // Autofocus and post-send refocus alone are idle. Actual typing has a
        // bounded hold, even after clearing the input or moving to another tab.
        const openDialog = !!document.querySelector('dialog[open]');
        return !readDraft || ime || openDialog || otherEditor || recentChatActivity || dirtyForms() || !!readDraft();
    }
    function send(payload) {
        if (socket?.readyState !== WebSocket.OPEN) return;
        try { socket.send(JSON.stringify(payload)); } catch { /* Server treats stale/missing reports as busy. */ }
    }
    function report(force = false) {
        const next = busy();
        if (force || next !== lastBusy) { lastBusy = next; send({ type: 'ui_composing', busy: next }); }
    }
    function unlock() {
        locked = false; attempt = null;
        banner?.remove(); banner = null;
        report(true);
    }
    function lock() {
        locked = true;
        if (banner) return;
        banner = document.createElement('aside');
        banner.id = 'release-composing-notice'; banner.setAttribute('role', 'status');
        banner.style.cssText = 'position:fixed;inset:0 0 auto;z-index:2147483647;padding:12px 20px;background:var(--bg-elevated,#191923);color:var(--text,#eee);border-bottom:2px solid var(--accent,#8054ff);display:flex;align-items:center;gap:16px';
        const text = document.createElement('span');
        text.textContent = 'Preparing a room update. Editing is temporarily locked; drafts are kept in this tab.';
        const stop = document.createElement('button'); stop.type = 'button'; stop.textContent = 'Stop room';
        stop.dataset.releaseControl = 'stop';
        stop.addEventListener('click', () => document.getElementById('room-stop')?.click());
        banner.append(text, stop); document.body.append(banner);
    }
    function handle(event) {
        if (event.type === 'deployment_state') {
            if (event.id !== null && (typeof event.id !== 'string' || !ATTEMPT_ID.test(event.id))) return false;
            // The new authenticated socket confirms which attempt survives.
            // Preserve a matching lock; only the server can release an old one.
            if (attempt !== null && attempt !== event.id) unlock();
            else report(true);
            return true;
        }
        if (!ATTEMPT_ID.test(event.id || '')) return false;
        if (event.type === 'deployment_prepare') {
            const blocked = busy();
            attempt = event.id;
            if (!blocked) lock();
            report(true);
            send({ type: 'deployment_prepare_ack', id: event.id, busy: blocked });
            return true;
        }
        if (event.type === 'deployment_release') {
            if (attempt === event.id) unlock();
            return true;
        }
        return false;
    }
    function attach(next) {
        socket = next; lastBusy = null;
        clearInterval(timer);
        report(true);
        // Includes hidden tabs. Browser throttling cannot turn stale server state idle.
        timer = setInterval(() => report(true), HEARTBEAT_MS);
    }
    function detach(previous) {
        if (socket !== previous) return;
        socket = null; clearInterval(timer); timer = null;
        // Preserve any lock until the server releases the matching attempt.
    }
    const allowed = target => target instanceof Element && !!target.closest('#room-stop, [data-release-control=stop], [data-release-control=revoke]');
    function guard(event) {
        if (!locked || allowed(event.target)) return;
        if (event.type === 'keydown' && (event.key === 'Tab' || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c'))) return;
        event.preventDefault(); event.stopImmediatePropagation();
    }
    for (const name of ['pointerdown', 'click', 'keydown', 'beforeinput', 'paste', 'cut', 'drop', 'submit', 'change']) document.addEventListener(name, guard, true);
    document.addEventListener('focusin', event => {
        if (locked && editable(event.target) && !allowed(event.target)) { event.target.blur(); return; }
        report();
    }, true);
    document.addEventListener('focusout', () => queueMicrotask(() => report()), true);
    function changedField(event) {
        // Track only that a form was edited. Never read its field values.
        // A conservative hold lasts until the form is reset or closed.
        if (editable(event.target) && event.target.id !== 'input') edited.add(event.target);
        if (event.target.id === 'input') noteChatActivity();
        queueMicrotask(() => report());
    }
    function noteChatActivity() {
        chatActivityAt = performance.now();
        clearTimeout(activityTimer);
        activityTimer = setTimeout(() => { activityTimer = null; report(); }, COMPOSING_ACTIVITY_MS);
        report();
    }
    document.addEventListener('keydown', event => {
        if (event.target.id === 'input' && !['Tab', 'Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) noteChatActivity();
    }, true);
    document.addEventListener('input', changedField, true);
    document.addEventListener('change', changedField, true);
    document.addEventListener('reset', event => {
        for (const node of edited) if (event.target.contains(node)) edited.delete(node);
        queueMicrotask(() => report());
    }, true);
    document.addEventListener('compositionstart', () => { ime = true; report(); }, true);
    document.addEventListener('compositionend', () => { ime = false; report(); }, true);
    document.addEventListener('visibilitychange', () => report(true));
    document.addEventListener('close', () => report(), true);
    new MutationObserver(() => report()).observe(document.documentElement, {
        attributes: true, subtree: true, attributeFilter: ['open'],
    });
    window.addEventListener('focus', () => report(true));
    return Object.freeze({ attach, detach, handle, changed: () => report(), register: reader => { readDraft = reader; report(true); } });
})();
