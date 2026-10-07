/* ZeroCollab chat - Delete mode: select messages by click or drag, then confirm.
 * Loaded after chat.js; shares its global state (see index.html script order). */

// --- Delete mode ---

let deleteMode = false;
let deleteSelected = new Set();
let deleteDragging = false;

function deleteClick(msgId, event) {
    event.stopPropagation();
    enterDeleteMode(msgId);
}

function enterDeleteMode(initialId) {
    if (deleteMode) return;
    deleteMode = true;
    deleteSelected.clear();
    if (initialId != null) deleteSelected.add(initialId);

    // Add delete-mode class — children transform right (no layout reflow)
    document.getElementById('messages').classList.add('delete-mode');

    // Add radio circles to all messages (not joins)
    document.querySelectorAll('.message[data-id]').forEach(el => {
        if (el.classList.contains('join-msg') || el.classList.contains('system-msg')) return;
        const id = parseInt(el.dataset.id);
        const circle = document.createElement('div');
        circle.className = 'delete-radio' + (deleteSelected.has(id) ? ' selected' : '');
        circle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            toggleDeleteSelect(id);
            deleteDragging = true;
        });
        circle.addEventListener('mouseenter', () => {
            if (deleteDragging) toggleDeleteSelect(id, true);
        });
        el.prepend(circle);
    });


    // Show floating delete bar
    showDeleteBar();
    updateDeleteBar();
    repositionScrollAnchor();
}

function toggleDeleteSelect(id, dragForceSelect) {
    const el = document.querySelector(`.message[data-id="${id}"]`);
    if (!el) return;
    const circle = el.querySelector('.delete-radio');

    if (dragForceSelect) {
        deleteSelected.add(id);
        if (circle) circle.classList.add('selected');
    } else {
        if (deleteSelected.has(id)) {
            deleteSelected.delete(id);
            if (circle) circle.classList.remove('selected');
        } else {
            deleteSelected.add(id);
            if (circle) circle.classList.add('selected');
        }
    }
    updateDeleteBar();
}

function showDeleteBar() {
    let bar = document.getElementById('delete-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'delete-bar';
        bar.innerHTML = `<button class="delete-bar-cancel" onclick="exitDeleteMode()">Cancel</button><span class="delete-bar-count"></span><button class="delete-bar-confirm" onclick="confirmDelete()">Delete</button>`;
        const footer = document.querySelector('footer');
        footer.parentNode.insertBefore(bar, footer);
    }
    bar.classList.remove('hidden');
}

function updateDeleteBar() {
    const count = deleteSelected.size;
    const span = document.querySelector('.delete-bar-count');
    if (span) span.textContent = count > 0 ? `${count} selected` : 'Select messages';
    const btn = document.querySelector('.delete-bar-confirm');
    if (btn) {
        btn.textContent = count > 0 ? `Delete (${count})` : 'Delete';
        btn.disabled = count === 0;
    }
}

function confirmDelete() {
    if (deleteSelected.size === 0) return;
    if (!sendServerEvent({ type: 'delete', project_id: window.activeProjectId, ids: [...deleteSelected] })) return;
    exitDeleteMode();
}

function exitDeleteMode() {
    deleteMode = false;
    deleteSelected.clear();
    deleteDragging = false;

    // Remove delete-mode — children transform back (no layout reflow)
    document.getElementById('messages').classList.remove('delete-mode');

    // Collapse bar
    const bar = document.getElementById('delete-bar');
    if (bar) {
        bar.classList.add('hidden');
    }

    // Fade out radios then remove
    document.querySelectorAll('.delete-radio').forEach(el => {
        el.classList.add('leaving');
        setTimeout(() => el.remove(), CONFIG.fadeOutMs);
    });

    repositionScrollAnchor();
}

// Auto-scroll while dragging near edges
let deleteScrollInterval = null;
document.addEventListener('mousemove', (e) => {
    if (!deleteDragging) return;
    const timeline = document.getElementById('timeline');
    const rect = timeline.getBoundingClientRect();
    const edgeZone = CONFIG.dragEdgePx;

    if (e.clientY < rect.top + edgeZone) {
        // Near top — scroll up
        if (!deleteScrollInterval) {
            deleteScrollInterval = setInterval(() => timeline.scrollTop -= CONFIG.dragScrollPx, CONFIG.dragScrollTickMs);
        }
    } else if (e.clientY > rect.bottom - edgeZone) {
        // Near bottom — scroll down
        if (!deleteScrollInterval) {
            deleteScrollInterval = setInterval(() => timeline.scrollTop += CONFIG.dragScrollPx, CONFIG.dragScrollTickMs);
        }
    } else if (deleteScrollInterval) {
        clearInterval(deleteScrollInterval);
        deleteScrollInterval = null;
    }
});

// Stop drag on mouseup
document.addEventListener('mouseup', () => {
    deleteDragging = false;
    if (deleteScrollInterval) {
        clearInterval(deleteScrollInterval);
        deleteScrollInterval = null;
    }
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && deleteMode) exitDeleteMode();
});

function handleDeleteBroadcast(ids) {
    for (const id of ids) {
        const el = document.querySelector(`.message[data-id="${id}"]`);
        if (el) el.remove();
    }
}
