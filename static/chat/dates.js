/* ZeroCollab chat - Date dividers between days and the floating day label shown while scrolling.
 * Loaded after chat.js; shares its global state (see index.html script order). */

// --- Date dividers ---

function getMessageDate(msg) {
    // msg.time is "HH:MM:SS" — we also need the date
    // Use msg.timestamp (epoch) if available, otherwise try to infer from today
    if (msg.timestamp) {
        return new Date(msg.timestamp * 1000).toDateString();
    }
    // Fallback: assume today (messages from history might not have timestamps)
    return new Date().toDateString();
}

function formatDateDivider(dateStr) {
    const date = new Date(dateStr);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    if (date.toDateString() === today.toDateString()) return 'Today';
    if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';

    return date.toLocaleDateString('en-GB', {
        weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
    });
}

function maybeInsertDateDivider(container, msg) {
    const msgDate = getMessageDate(msg);
    const channel = msg.channel || 'general';
    const lastDate = lastMessageDates[channel];
    
    if (msgDate !== lastDate) {
        lastMessageDates[channel] = msgDate;
        const divider = document.createElement('div');
        divider.className = 'date-divider';
        divider.dataset.channel = channel;
        divider.innerHTML = `<span>${formatDateDivider(msgDate)}</span>`;
        if (channel !== activeChannel) {
            divider.classList.add('off-channel');
        }
        container.appendChild(divider);
    }
}

// --- Floating day indicator ---

let dayFloatDividers = null;   // live HTMLCollection — follows divider add/remove
let dayFloatFadeTimer = null;
let dayFloatRafPending = false;

function dayFloatLabel() {
    if (!dayFloatDividers) {
        const container = document.getElementById('messages');
        if (!container) return null;
        dayFloatDividers = container.getElementsByClassName('date-divider');
    }
    const scroll = document.getElementById('timeline');
    if (!scroll) return null;
    const top = scroll.getBoundingClientRect().top;
    let label = null;
    for (const d of dayFloatDividers) {
        if (d.classList.contains('off-channel')) continue;
        if (d.getBoundingClientRect().top <= top) {
            label = d.textContent;
        } else {
            break;
        }
    }
    return label;
}

function dayFloatHide() {
    const float = document.getElementById('day-float');
    if (!float) return;
    if (dayFloatFadeTimer) { clearTimeout(dayFloatFadeTimer); dayFloatFadeTimer = null; }
    float.classList.remove('visible');
}

function dayFloatOnScroll() {
    if (dayFloatRafPending) return;
    dayFloatRafPending = true;
    requestAnimationFrame(() => {
        dayFloatRafPending = false;
        const float = document.getElementById('day-float');
        if (!float) return;
        const label = dayFloatLabel();
        if (!label) { dayFloatHide(); return; }
        float.querySelector('span').textContent = label;
        float.classList.add('visible');
        if (dayFloatFadeTimer) clearTimeout(dayFloatFadeTimer);
        dayFloatFadeTimer = setTimeout(() => {
            dayFloatFadeTimer = null;
            float.classList.remove('visible');
        }, CONFIG.dayLabelMs);
    });
}

function dayFloatRefresh() {
    // Recompute without starting the show/fade cycle: swap the label if the
    // pill is relevant, hide (cancelling any fade) if no day is above the top.
    const float = document.getElementById('day-float');
    if (!float) return;
    const label = dayFloatLabel();
    if (!label) { dayFloatHide(); return; }
    float.querySelector('span').textContent = label;
}
