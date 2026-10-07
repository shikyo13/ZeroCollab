/* ZeroCollab - WebSocket client */

// Session token injected by the server into the HTML page.
// Sent with every API call and WebSocket connection to authenticate.
const SESSION_TOKEN = window.__SESSION_TOKEN__ || "";

let ws = null;
let pendingAttachments = [];
let autoScroll = true;
let reconnectTimer = null;
let username = 'user';
let agentConfig = {};  // { name: { color, label } } — registered instances (used for pills)
let baseColors = {};   // { name: { color, label } } — base agent colors (for message coloring)
let activeMentions = new Set();  // agent names with pre-@ toggled on (for the active channel)
let _channelMentions = {};  // channel -> array of toggled agent names (per-channel memory of activeMentions)
let replyingTo = null;  // { id, sender, text, mention } or null; mention is the '@handle ' Reply inserted
let unreadCount = 0;    // messages received while scrolled up
let lastMessageDate = null;  // track date for dividers (general channel)
let lastMessageDates = {};  // { channel: dateString } for per-channel dividers
let soundEnabled = false;  // suppress sounds during initial history load
const linkedChannel = new URLSearchParams(location.search).get('channel');
let activeChannel = (linkedChannel && /^[a-z0-9][a-z0-9-]{0,19}$/.test(linkedChannel) ? linkedChannel : null) || localStorage.getItem('zerocollab-channel') || 'general';
let channelList = [];
let channelUnread = {};  // { channelName: count }
let colorOverrides = JSON.parse(localStorage.getItem('zerocollab-color-overrides') || '{}');

// Expose globals that channels.js and the React shell read via window.*
// Using defineProperty so live values are always returned.
Object.defineProperty(window, 'SESSION_TOKEN', { get() { return SESSION_TOKEN; } });
Object.defineProperty(window, 'activeChannel', { get() { return activeChannel; } });
Object.defineProperty(window, 'channelList', { get() { return channelList; }, set(v) { channelList = v; } });
Object.defineProperty(window, 'channelUnread', { get() { return channelUnread; }, set(v) { channelUnread = v; } });
window._setActiveChannel = function(v) { activeChannel = v; };
window._setPendingChannelSwitch = function(v) { pendingChannelSwitch = v; };
// scrollToBottom is set after function definition (see below)
Object.defineProperty(window, 'username', { get() { return username; } });
Object.defineProperty(window, 'agentConfig', { get() { return agentConfig; } });
Object.defineProperty(window, 'ws', { get() { return ws; } });
Object.defineProperty(window, 'soundEnabled', { get() { return soundEnabled; } });
Object.defineProperty(window, 'autoScroll', { get() { return autoScroll; } });

// --- Drag-scroll for overflow containers ---
function enableDragScroll(el) {
    let isDown = false, startX, scrollLeft;
    el.addEventListener('mousedown', e => {
        if (e.button !== 0) return;  // left-click only
        isDown = true; startX = e.pageX - el.offsetLeft; scrollLeft = el.scrollLeft;
        el.style.cursor = 'grabbing';
    });
    el.addEventListener('mouseleave', () => { isDown = false; el.style.cursor = ''; });
    el.addEventListener('mouseup', () => { isDown = false; el.style.cursor = ''; });
    el.addEventListener('mousemove', e => {
        if (!isDown) return;
        e.preventDefault();
        el.scrollLeft = scrollLeft - (e.pageX - el.offsetLeft - startX);
    });
}

// --- Notification sounds ---
const SOUND_OPTIONS = [
    { value: 'soft-chime', label: 'Soft Chime' },
    { value: 'bright-ping', label: 'Bright Ping' },
    { value: 'gentle-pop', label: 'Gentle Pop' },
    { value: 'alert-tone', label: 'Alert Tone' },
    { value: 'pluck', label: 'Pluck' },
    { value: 'click', label: 'Click' },
    { value: 'warm-bell', label: 'Warm Bell' },
    { value: 'none', label: 'None' },
];
const DEFAULT_SOUND = 'soft-chime';
const CROSS_CHANNEL_SOUND = 'pluck';
let soundPrefs = JSON.parse(localStorage.getItem('zerocollab-sounds') || '{}');
const soundCache = {};

function playNotificationSound(sender) {
    const key = sender.toLowerCase();
    const soundName = soundPrefs[key] || soundPrefs['default'] || DEFAULT_SOUND;
    if (soundName === 'none') return;
    if (!soundCache[soundName]) {
        soundCache[soundName] = new Audio(`/static/sounds/${soundName}.mp3`);
    }
    const audio = soundCache[soundName];
    audio.currentTime = 0;
    audio.play().catch(() => {});  // ignore autoplay policy errors
}

function playCrossChannelSound() {
    const soundName = soundPrefs['cross-channel'] || CROSS_CHANNEL_SOUND;
    if (soundName === 'none') return;
    if (!soundCache[soundName]) {
        soundCache[soundName] = new Audio(`/static/sounds/${soundName}.mp3`);
    }
    const audio = soundCache[soundName];
    audio.currentTime = 0;
    audio.play().catch(() => {});
}
window.playCrossChannelSound = playCrossChannelSound;

function buildSoundSettings() {
    const container = document.getElementById('sound-settings');
    if (!container) return;
    container.innerHTML = '';

    // Default sound + cross-channel sound + per-agent rows
    const agents = ['default', 'cross-channel', ...Object.keys(agentConfig)];
    for (const name of agents) {
        const row = document.createElement('div');
        row.className = 'sound-row';
        const label = document.createElement('span');
        label.className = 'sound-label';
        label.textContent = name === 'default' ? 'Default sound'
            : name === 'cross-channel' ? 'Background alerts'
            : (agentConfig[name]?.label || name);
        const select = document.createElement('select');
        select.className = 'sound-select';
        select.dataset.agent = name;
        const currentVal = soundPrefs[name]
            || (name === 'default' ? DEFAULT_SOUND : name === 'cross-channel' ? CROSS_CHANNEL_SOUND : '');
        for (const opt of SOUND_OPTIONS) {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            if (currentVal === opt.value) o.selected = true;
            select.appendChild(o);
        }
        // Add "Use default" option for per-agent rows (not default or cross-channel)
        if (name !== 'default' && name !== 'cross-channel') {
            const o = document.createElement('option');
            o.value = '';
            o.textContent = 'Use default';
            if (!soundPrefs[name]) o.selected = true;
            select.insertBefore(o, select.firstChild);
        }
        // Preview on change
        select.addEventListener('change', () => {
            const val = select.value;
            soundPrefs[name] = val;
            localStorage.setItem('zerocollab-sounds', JSON.stringify(soundPrefs));
            if (val && val !== 'none') {
                if (!soundCache[val]) soundCache[val] = new Audio(`/static/sounds/${val}.mp3`);
                soundCache[val].currentTime = 0;
                soundCache[val].play().catch(() => {});
            }
        });
        row.appendChild(label);
        row.appendChild(select);
        container.appendChild(row);
    }
}

// Real brand logo SVGs from Bootstrap Icons (MIT licensed)
const BRAND_AVATARS = {
    claude: `<svg viewBox="0 0 16 16" fill="white"><path d="m3.127 10.604 3.135-1.76.053-.153-.053-.085H6.11l-.525-.032-1.791-.048-1.554-.065-1.505-.08-.38-.081L0 7.832l.036-.234.32-.214.455.04 1.009.069 1.513.105 1.097.064 1.626.17h.259l.036-.105-.089-.065-.068-.064-1.566-1.062-1.695-1.121-.887-.646-.48-.327-.243-.306-.104-.67.435-.48.585.04.15.04.593.456 1.267.981 1.654 1.218.242.202.097-.068.012-.049-.109-.181-.9-1.626-.96-1.655-.428-.686-.113-.411a2 2 0 0 1-.068-.484l.496-.674L4.446 0l.662.089.279.242.411.94.666 1.48 1.033 2.014.302.597.162.553.06.17h.105v-.097l.085-1.134.157-1.392.154-1.792.052-.504.25-.605.497-.327.387.186.319.456-.045.294-.19 1.23-.37 1.93-.243 1.29h.142l.161-.16.654-.868 1.097-1.372.484-.545.565-.601.363-.287h.686l.505.751-.226.775-.707.895-.585.759-.839 1.13-.524.904.048.072.125-.012 1.897-.403 1.024-.186 1.223-.21.553.258.06.263-.218.536-1.307.323-1.533.307-2.284.54-.028.02.032.04 1.029.098.44.024h1.077l2.005.15.525.346.315.424-.053.323-.807.411-3.631-.863-.872-.218h-.12v.073l.726.71 1.331 1.202 1.667 1.55.084.383-.214.302-.226-.032-1.464-1.101-.565-.497-1.28-1.077h-.084v.113l.295.432 1.557 2.34.08.718-.112.234-.404.141-.444-.08-.911-1.28-.94-1.44-.759-1.291-.093.053-.448 4.821-.21.246-.484.186-.403-.307-.214-.496.214-.98.258-1.28.21-1.016.19-1.263.112-.42-.008-.028-.092.012-.953 1.307-1.448 1.957-1.146 1.227-.274.109-.477-.247.045-.44.266-.39 1.586-2.018.956-1.25.617-.723-.004-.105h-.036l-4.212 2.736-.75.096-.324-.302.04-.496.154-.162 1.267-.871z"/></svg>`,
    codex: `<svg viewBox="0 0 16 16" fill="white"><path d="M14.949 6.547a3.94 3.94 0 0 0-.348-3.273 4.11 4.11 0 0 0-4.4-1.934A4.1 4.1 0 0 0 8.423.2 4.15 4.15 0 0 0 6.305.086a4.1 4.1 0 0 0-1.891.948 4.04 4.04 0 0 0-1.158 1.753 4.1 4.1 0 0 0-1.563.679A4 4 0 0 0 .554 4.72a3.99 3.99 0 0 0 .502 4.731 3.94 3.94 0 0 0 .346 3.274 4.11 4.11 0 0 0 4.402 1.933c.382.425.852.764 1.377.995.526.231 1.095.35 1.67.346 1.78.002 3.358-1.132 3.901-2.804a4.1 4.1 0 0 0 1.563-.68 4 4 0 0 0 1.14-1.253 3.99 3.99 0 0 0-.506-4.716m-6.097 8.406a3.05 3.05 0 0 1-1.945-.694l.096-.054 3.23-1.838a.53.53 0 0 0 .265-.455v-4.49l1.366.778q.02.011.025.035v3.722c-.003 1.653-1.361 2.992-3.037 2.996m-6.53-2.75a2.95 2.95 0 0 1-.36-2.01l.095.057L5.29 12.09a.53.53 0 0 0 .527 0l3.949-2.246v1.555a.05.05 0 0 1-.022.041L6.473 13.3c-1.454.826-3.311.335-4.15-1.098m-.85-6.94A3.02 3.02 0 0 1 3.07 3.949v3.785a.51.51 0 0 0 .262.451l3.93 2.237-1.366.779a.05.05 0 0 1-.048 0L2.585 9.342a2.98 2.98 0 0 1-1.113-4.094zm11.216 2.571L8.747 5.576l1.362-.776a.05.05 0 0 1 .048 0l3.265 1.86a3 3 0 0 1 1.173 1.207 2.96 2.96 0 0 1-.27 3.2 3.05 3.05 0 0 1-1.36.997V8.279a.52.52 0 0 0-.276-.445m1.36-2.015-.097-.057-3.226-1.855a.53.53 0 0 0-.53 0L6.249 6.153V4.598a.04.04 0 0 1 .019-.04L9.533 2.7a3.07 3.07 0 0 1 3.257.139c.474.325.843.778 1.066 1.303.223.526.289 1.103.191 1.664zM5.503 8.575 4.139 7.8a.05.05 0 0 1-.026-.037V4.049c0-.57.166-1.127.476-1.607s.752-.864 1.275-1.105a3.08 3.08 0 0 1 3.234.41l-.096.054-3.23 1.838a.53.53 0 0 0-.265.455zm.742-1.577 1.758-1 1.762 1v2l-1.755 1-1.762-1z"/></svg>`,
    gemini: `<svg viewBox="0 0 65 65" fill="white"><path d="M32.447 0c.68 0 1.273.465 1.439 1.125a38.904 38.904 0 001.999 5.905c2.152 5 5.105 9.376 8.854 13.125 3.751 3.75 8.126 6.703 13.125 8.855a38.98 38.98 0 005.906 1.999c.66.166 1.124.758 1.124 1.438 0 .68-.464 1.273-1.125 1.439a38.902 38.902 0 00-5.905 1.999c-5 2.152-9.375 5.105-13.125 8.854-3.749 3.751-6.702 8.126-8.854 13.125a38.973 38.973 0 00-2 5.906 1.485 1.485 0 01-1.438 1.124c-.68 0-1.272-.464-1.438-1.125a38.913 38.913 0 00-2-5.905c-2.151-5-5.103-9.375-8.854-13.125-3.75-3.749-8.125-6.702-13.125-8.854a38.973 38.973 0 00-5.905-2A1.485 1.485 0 010 32.448c0-.68.465-1.272 1.125-1.438a38.903 38.903 0 005.905-2c5-2.151 9.376-5.104 13.125-8.854 3.75-3.749 6.703-8.125 8.855-13.125a38.972 38.972 0 001.999-5.905A1.485 1.485 0 0132.447 0z"/></svg>`,
    kimi: `<svg viewBox="0 0 16 16" fill="white"><path d="M6 .278a.77.77 0 0 1 .08.858 7.2 7.2 0 0 0-.878 3.46c0 4.021 3.278 7.277 7.318 7.277q.792-.001 1.533-.16a.79.79 0 0 1 .81.316.73.73 0 0 1-.031.893A8.35 8.35 0 0 1 8.344 16C3.734 16 0 12.286 0 7.71 0 4.266 2.114 1.312 5.124.06A.75.75 0 0 1 6 .278"/></svg>`,
    qwen: `<svg viewBox="0 0 331 328" fill="white"><path d="M120 8l23 39-23 39h180l-23 39H102L77 82l43-74z"/><path d="M30 86h45l88 152-25 43H53l22-39h45L30 86z"/><path d="M143 280l22 39 90-156 22 39h45l-43-74-49 0-87 152z"/></svg>`,
    kilo: `<svg viewBox="48 48 129 132" fill="black"><path d="M66.44 63.01Q64.87 65.23 65.5 68.28A2.33 2.33 0 0 0 67.78 70.13L86.53 70.13A2.43 2.41 67.4 0 1 88.24 70.84L102.02 84.62A3.21 3.21 0 0 1 102.96 86.89L102.96 102.05A.81.81 0 0 1 102.15 102.86L89.67 102.86A.78.77 0 0 1 88.89 102.09L88.89 86.2A2.04 2.04 0 0 0 86.86 84.16Q74.44 84.1 69.34 83.99Q68.24 83.97 67.5 84.16Q65.99 84.54 66.64 85.33L66.35 87.63L65.35 102.17A.65.64 2.3 0 1 64.69 102.77L50.01 102.57L49.99 50.57A.6.59-90 0 1 50.58 49.97L66.18 49.97A.31.31 0 0 1 66.49 50.29L66.44 63.01Z"/><rect x="88.81" y="51" width="14.18" height="16.62" rx="1.08"/><path d="M122.05 63.79L122.05 51.91A1.15 1.14 90 0 1 123.19 50.76L142.37 50.76A4.57 4.56 67.6 0 1 145.61 52.11L153.94 60.44A3.97 3.94 23 0 1 155.1 63.28Q154.92 74.65 155.02 86.25C155.04 88.43 156.32 88.89 158.37 88.92Q166.52 89.04 173.16 88.88A.91.91 0 0 1 174.09 89.79L174.09 102.07A.79.79 0 0 1 173.3 102.86L123.01 102.86A.89.88-90 0 1 122.13 101.97L122.13 89.71A.8.79-89 0 1 122.95 88.91Q130.98 89.21 138 88.78Q140.89 88.61 140.89 85.47Q140.89 75.62 140.52 67.56A2.12 2.11-1.3 0 0 138.4 65.54L123.8 65.54A1.75 1.75 0 0 1 122.05 63.79Z"/><rect x="-6.95" y="-6.95" transform="translate(95.98,129.08) rotate(-0.3)" width="13.9" height="13.9" rx=".88"/><path d="M66.82 158.21Q67.07 158.47 68.12 158.47Q103 158.42 103.5 158.44A.63.62-.5 0 1 104.14 159.06L104.19 174.48A.5.5 0 0 1 103.69 174.98Q94.92 174.96 64.49 175.1C60.2 175.12 58.29 172.24 55.55 169.5C52.81 166.76 49.92 164.85 49.94 160.56Q50.03 130.13 50 121.36A.5.5 0 0 1 50.5 120.86L65.92 120.89A.63.62-89.6 0 1 66.54 121.53Q66.56 122.03 66.56 156.91Q66.56 157.96 66.82 158.21Z"/><path d="M151.27 140.92Q151.82 140.86 151.98 140.39A.33.33 0 0 0 151.67 139.95L139.26 139.95A.32.31 0 0 1 138.94 139.64L138.94 126.18A.55.55 0 0 1 139.5 125.63Q145.94 125.66 153.44 125.56Q156.57 125.51 159.56 124.49L163.35 124.52A1.49 1.48-22.2 0 1 164.36 124.94L173.85 134.44A4.03 4.02-67.7 0 1 175.04 137.3L175.04 161.14A.63.63 0 0 1 174.41 161.77L158.82 161.77A.4.39-90 0 1 158.43 161.37L158.43 141.62A.55.55 0 0 0 157.89 141.07L151.27 140.92Z"/><path d="M136.83 162.31Q137.51 162.99 138.52 163.02Q147.16 163.23 155.66 162.98A1.29 1.29 0 0 1 156.98 164.27L156.98 176.37A.7.69-.4 0 1 156.29 177.06L133.55 177.06A2.39 2.38-21.8 0 1 131.83 176.32Q131.43 175.93 127.32 171.82Q123.2 167.7 122.81 167.3A2.39 2.38-68.1 0 1 122.08 165.58L122.11 142.84A.7.69-89.5 0 1 122.8 142.15L134.9 142.16A1.29 1.29 0 0 1 136.18 143.48Q135.92 151.98 136.13 160.62Q136.15 161.63 136.83 162.31Z"/></svg>`,
    codebuddy: `<svg viewBox="0 0 52 52" fill="white"><path d="M30.5918 3.12856C30.984 2.77679 31.0078 2.7632 31.2955 2.74593C31.7615 2.71193 32.1882 2.93586 32.9147 3.59728C34.6119 5.13959 36.9755 8.30995 38.4449 11.0177L39.0125 12.0691L39.8143 12.4677C40.5885 12.8589 41.8587 13.6611 42.389 14.0913C42.6286 14.2894 42.6626 14.2934 42.912 14.1964C44.0375 13.7583 45.6494 14.3393 47.0714 15.7033C48.3516 16.9303 49.5781 19.0269 50.0478 20.7767C50.1164 21.0582 50.2074 21.6636 50.2405 22.1144C50.3477 23.6973 49.84 24.9617 48.8624 25.5341C48.6628 25.6493 48.6492 25.6807 48.6548 26.1783C48.6998 28.5492 48.0606 30.9165 46.7768 33.2244C45.3276 35.8156 42.7467 38.496 39.2544 41.0214C37.3789 42.3862 32.9421 44.9717 30.9361 45.8792C26.1304 48.0428 22.278 48.8718 18.9316 48.4618C16.9356 48.22 14.6761 47.4417 13.3392 46.5373C12.9873 46.294 12.9318 46.2791 12.6629 46.3561C11.2318 46.7671 9.35752 45.9219 7.76528 44.1544C7.13027 43.448 6.10508 41.7136 5.77273 40.7853C5.00409 38.6128 5.15721 36.6516 6.18105 35.4808C6.44522 35.1797 6.4538 35.1667 6.39603 34.6598C6.30065 33.8298 6.25703 32.6017 6.30061 31.809L6.33535 31.0683L5.22371 29.1019C3.50212 26.0386 2.40857 23.4663 1.98661 21.501C1.76389 20.4233 1.77734 19.9446 2.05091 19.5908C2.21741 19.3773 2.76347 19.1568 3.42155 19.0352C5.07869 18.7442 8.69327 19.0065 12.7142 19.7165L13.1316 19.789L14.0497 18.977C15.5733 17.6274 16.5858 16.8705 18.4518 15.707C20.3967 14.4901 22.5922 13.4895 25.064 12.6968L25.8564 12.4423L26.2926 11.2974C27.8535 7.17701 29.452 4.13917 30.5918 3.12856ZM17.5169 24.2439C15.7528 25.2625 14.8705 25.7716 14.2223 26.3423C11.5975 28.6536 10.6172 32.3151 11.7346 35.6292C12.0106 36.4475 12.5193 37.3301 13.5378 39.0941C14.5563 40.8582 15.0662 41.7401 15.637 42.3882C17.9483 45.0128 21.6091 45.9938 24.923 44.8764C25.7414 44.6004 26.6233 44.0909 28.3875 43.0724L38.5362 37.213C40.3004 36.1945 41.1826 35.6854 41.8308 35.1147C44.4555 32.8034 45.4363 29.1426 44.319 25.8286C44.043 25.0103 43.5343 24.1277 42.5158 22.3637C41.4974 20.5997 40.9873 19.7177 40.4166 19.0696C38.1053 16.4448 34.4441 15.4631 31.1301 16.5806C30.3118 16.8565 29.4297 17.3661 27.6656 18.3846L17.5169 24.2439Z"/></svg>`,
    copilot: `<svg viewBox="0 0 16 16" fill="white"><path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z"/></svg>`,
};
const USER_AVATAR = `<svg viewBox="0 0 32 32" fill="none"><circle cx="16" cy="12" r="5" fill="white" opacity="0.85"/><path d="M7 27C7 21.5 11 18 16 18C21 18 25 21.5 25 27" fill="white" opacity="0.85"/></svg>`;

function getAvatarSvg(sender) {
    const s = sender.toLowerCase();
    const resolved = resolveAgent(s);
    if (resolved) {
        if (BRAND_AVATARS[resolved]) return BRAND_AVATARS[resolved];
        // Use base field from agent config (handles custom names like "claudeypops" → claude)
        const cfg = agentConfig[resolved];
        if (cfg && cfg.base && BRAND_AVATARS[cfg.base]) return BRAND_AVATARS[cfg.base];
        // Fallback: parse base-N pattern (claude-2 → claude)
        const base = resolved.replace(/-\d+$/, '');
        if (base !== resolved && BRAND_AVATARS[base]) return BRAND_AVATARS[base];
    }
    // Fall back for offline agents: check config base, then parse pattern
    const cfg = agentConfig[s];
    if (cfg && cfg.base && BRAND_AVATARS[cfg.base]) return BRAND_AVATARS[cfg.base];
    const base = s.replace(/-\d+$/, '');
    if (BRAND_AVATARS[base]) return BRAND_AVATARS[base];
    return USER_AVATAR;
}

// --- Init ---

function init() {
    // Configure marked for chat-style rendering
    marked.setOptions({
        breaks: true,      // single newline → <br>
        gfm: true,         // GitHub-flavored markdown
    });

    detectPlatform();
    connectWebSocket();
    setupInput();
    setupDragDrop();
    setupPaste();
    setupScroll();
    setupSettingsKeys();
    setupKeyboardShortcuts();
    Channels.init();

    // Dismiss channel edit controls when clicking outside channel bar
    document.addEventListener('click', (e) => {
        if (!e.target.closest('#channel-bar')) {
            document.querySelectorAll('.channel-tab.editing').forEach(t => t.classList.remove('editing'));
        }
    });
}

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
    return html;
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
                setTimeout(() => { btn.textContent = 'copy'; }, 1500);
            } catch (err) {
                btn.textContent = 'failed';
                setTimeout(() => { btn.textContent = 'copy'; }, 1500);
            }
        };
        pre.style.position = 'relative';
        pre.appendChild(btn);
    }
}

// Render saved history in small batches, without per-message layout reads.
let historyLoading = true;
let historyReceived = false;
let pendingHistory = [];
let pendingLiveEvents = [];
let historyScheduled = false;
let historyGeneration = 0;
// A task queue yields between batches and also runs in background tabs,
// where animation frames can be suspended.
const historyTasks = new MessageChannel();
historyTasks.port1.onmessage = (event) => {
    if (event.data === historyGeneration) renderHistoryBatch();
};

function setHistoryIndicator(visible) {
    const loader = document.getElementById('loading-indicator');
    if (!loader) return;
    const timeline = document.getElementById('timeline');
    const top = timeline.scrollTop;
    const height = loader.getBoundingClientRect().height;
    loader.classList.toggle('hidden', !visible);
    if (!autoScroll && top > height) {
        timeline.scrollTo({ top: top + loader.getBoundingClientRect().height - height, behavior: 'instant' });
    }
}

function scheduleHistoryRender() {
    if (!historyScheduled) {
        historyScheduled = true;
        historyTasks.port2.postMessage(historyGeneration);
    }
}

function renderHistoryBatch() {
    historyScheduled = false;
    const fragment = document.createDocumentFragment();
    for (const msg of pendingHistory.splice(0, 100)) {
        appendMessage(msg, { history: true, container: fragment });
    }
    document.getElementById('messages').appendChild(fragment);
    if (autoScroll) scrollToBottom(true);
    if (pendingHistory.length) {
        scheduleHistoryRender();
    } else if (historyReceived) {
        historyLoading = false;
        soundEnabled = true;
        setHistoryIndicator(false);
        const events = pendingLiveEvents;
        pendingLiveEvents = [];
        for (const event of events) handleServerEvent(event);
        if (autoScroll) scrollToBottom(true);
    }
}

// --- WebSocket ---

function handleServerEvent(event) {
    if (historyLoading && ['message', 'edit', 'message_update', 'delete', 'clear', 'channel_renamed', 'agent_renamed'].includes(event.type)) {
        pendingLiveEvents.push(event);
        return;
    }
    if (event.type === 'history') {
        pendingHistory.push(...event.messages);
        scheduleHistoryRender();
        return;
    }
    if (event.type === 'history_complete') {
        historyReceived = true;
        scheduleHistoryRender();
        return;
    }
    // Emit through Hub for modules to subscribe (PR 1 seam)
    Hub.emit(event.type, event);
    if (event.type === 'message') {
        if (document.getElementById(`message-${event.data.id}`)) return;
        // Play notification sound for new messages from others (not joins, not when focused)
        if (soundEnabled && !document.hasFocus() && event.data.type !== 'join' && event.data.type !== 'leave' && event.data.sender && event.data.sender.toLowerCase() !== username.toLowerCase()) {
            playNotificationSound(event.data.sender);
        }
        appendMessage(event.data);
    } else if (event.type === 'agent_renamed') {
        // Migrate active mentions before the agents config rebuild
        if (activeMentions.has(event.old_name)) {
            activeMentions.delete(event.old_name);
            activeMentions.add(event.new_name);
        }
        // Migrate the per-channel remembered toggles too
        for (const ch of Object.keys(_channelMentions)) {
            const arr = _channelMentions[ch];
            const idx = arr.indexOf(event.old_name);
            if (idx !== -1) arr[idx] = event.new_name;
        }
        // Update sender name, color, and avatar on all existing messages in the DOM
        const newColor = getColor(event.new_name);
        const newAvatar = getAvatarSvg(event.new_name);
        const newAgentKey = (resolveAgent(event.new_name.toLowerCase()) || event.new_name).toLowerCase();
        document.querySelectorAll('#messages .message').forEach(el => {
            // Regular chat messages
            const senderEl = el.querySelector('.msg-sender');
            if (senderEl && senderEl.textContent === event.old_name) {

                senderEl.textContent = event.new_name;
                senderEl.style.color = newColor;
                // Update bubble accent color
                const bubble = el.querySelector('.chat-bubble');
                if (bubble) bubble.style.setProperty('--bubble-color', newColor);
                // Update avatar
                const avatarWrap = el.querySelector('.avatar-wrap');
                if (avatarWrap) {
                    avatarWrap.dataset.agent = newAgentKey;
                    const avatar = avatarWrap.querySelector('.avatar');
                    if (avatar) {
                        avatar.style.backgroundColor = newColor;
                        avatar.innerHTML = newAvatar;
                    }
                }
            }
            // Join/leave messages (separate structure, no .msg-sender)
            const joinText = el.querySelector('.join-text strong');
            if (joinText && joinText.textContent === event.old_name) {

                joinText.textContent = event.new_name;
                joinText.style.color = newColor;
                const joinDot = el.querySelector('.join-dot');
                if (joinDot) joinDot.style.background = newColor;
            }
        });
    } else if (event.type === 'agents') {
        applyAgentConfig(event.data);
    } else if (event.type === 'base_colors') {
        baseColors = event.data || {};
    } else if (event.type === 'status') {
        updateStatus(event.data);
    } else if (event.type === 'typing') {
        updateTyping(event.agent, event.active);
    } else if (event.type === 'settings') {
        applySettings(event.data);
    } else if (event.type === 'delete') {
        handleDeleteBroadcast(event.ids);
    } else if (event.type === 'channel_renamed') {
        // Migrate per-channel client state to the new name
        const channelIndex = channelList.indexOf(event.old_name);
        if (channelIndex !== -1) {
            channelList = [...channelList];
            channelList[channelIndex] = event.new_name;
        }
        if (channelUnread[event.old_name] !== undefined) {
            channelUnread[event.new_name] = channelUnread[event.old_name];
            delete channelUnread[event.old_name];
        }
        if (_channelMentions[event.old_name] !== undefined) {
            _channelMentions[event.new_name] = _channelMentions[event.old_name];
            delete _channelMentions[event.old_name];
        }
        if (pendingChannelSwitch === event.old_name) {
            pendingChannelSwitch = event.new_name;
        }
        // Migrate data-channel on existing DOM elements
        const container = document.getElementById('messages');
        for (const el of container.children) {
            if ((el.dataset.channel || 'general') === event.old_name) {
                el.dataset.channel = event.new_name;
            }
        }
        // Update per-channel date tracking
        if (lastMessageDates[event.old_name]) {
            lastMessageDates[event.new_name] = lastMessageDates[event.old_name];
            delete lastMessageDates[event.old_name];
        }
        // Update active channel if we were on the renamed one
        if (activeChannel === event.old_name) {
            activeChannel = event.new_name;
            localStorage.setItem('zerocollab-channel', event.new_name);
            Store.set('activeChannel', event.new_name);
        }
        filterMessagesByChannel();
        renderChannelTabs();
    } else if (event.type === 'edit') {
        // A message was edited/demoted — re-render it in place
        const updatedMsg = event.message;
        if (updatedMsg && updatedMsg.id != null) {
            const el = document.querySelector(`.message[data-id="${updatedMsg.id}"]`);
            if (el) {
                // Insert a fresh message after the old one, then remove the old
                const placeholder = document.createElement('div');
                el.after(placeholder);
                el.remove();
                // Temporarily hijack container to insert at the right spot
                const container = document.getElementById('messages');
                appendMessage(updatedMsg, { history: true });
                // Move the newly appended message to where the old one was
                const newEl = container.lastElementChild;
                if (newEl && newEl.dataset.id == updatedMsg.id) {
                    placeholder.replaceWith(newEl);
                } else {
                    placeholder.remove();
                }
            }
        }
    } else if (event.type === 'clear') {
        const clearChannel = event.channel || null;
        if (clearChannel) {
            // Per-channel clear: remove only messages from that channel
            const container = document.getElementById('messages');
            const toRemove = [];
            for (const el of container.children) {
                const isDivider = el.classList.contains('date-divider');
                if ((el.dataset.id || isDivider) && (el.dataset.channel || 'general') === clearChannel) {
                    toRemove.push(el);
                }
            }
            toRemove.forEach(el => el.remove());
            // Clean up orphaned date dividers and reset tracking
            delete lastMessageDates[clearChannel];
            filterMessagesByChannel();
        } else {
            // Full clear (all channels)
            document.getElementById('messages').innerHTML = '';
            lastMessageDate = null;
            lastMessageDates = {};
        }
        dayFloatRefresh();
    } else if (event.type === 'reload') {
        // Server requests full page reload (e.g. after import)
        location.reload();
    }
}

function connectWebSocket() {
    historyGeneration++;
    historyScheduled = false;
    historyLoading = true;
    historyReceived = false;
    pendingHistory = [];
    pendingLiveEvents = [];
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(SESSION_TOKEN)}`);

    ws.onopen = () => {
        console.log('WebSocket connected');
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    };

    ws.onmessage = (e) => handleServerEvent(JSON.parse(e.data));

    ws.onclose = (e) => {
        // Server sends 4003 when session token is invalid (server restarted).
        // Auto-reload to pick up the fresh token from the new HTML page.
        if (e.code === 4003) {
            console.warn('Session token rejected (server restarted?) — reloading page...');
            location.reload();
            return;
        }
        console.log('Disconnected, reconnecting in 2s...');
        soundEnabled = false;  // suppress sounds during reconnect history replay
        historyGeneration++;
        historyScheduled = false;
        historyLoading = true;
        historyReceived = false;
        pendingHistory = [];
        pendingLiveEvents = [];
        setHistoryIndicator(true);
        reconnectTimer = setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
        ws.close();
    };
}

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
            divider.style.display = 'none';
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
        if (d.style.display === 'none') continue;
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
        }, 1200);
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

// --- Messages ---

function appendMessage(msg, options = {}) {
    // History replay and a live delivery can contain the same saved message.
    if (document.getElementById(`message-${msg.id}`)) return;
    const container = options.container || document.getElementById('messages');

    // Insert date divider if needed
    maybeInsertDateDivider(container, msg);

    const el = document.createElement('div');
    el.className = 'message';
    el.id = `message-${msg.id}`;
    el.dataset.id = msg.id;
    el.dataset.sender = msg.sender;
    const msgChannel = msg.channel || 'general';
    el.dataset.channel = msgChannel;

    if (msg.type === 'join' || msg.type === 'leave') {
        el.classList.add('join-msg');
        const color = getColor(msg.sender);
        el.innerHTML = `<span class="join-dot" style="background: ${color}"></span><span class="join-text"><strong style="color: ${color}">${escapeHtml(getAgentDisplayName(msg.sender))}</strong> ${msg.type === 'join' ? 'joined' : 'left'}</span>`;
    } else if (msg.type === 'system' || msg.sender === 'system') {
        el.classList.add('system-msg');
        el.innerHTML = `<span class="msg-text">${escapeHtml(msg.text)}</span>`;
    } else {
        const isError = msg.text.startsWith('[') && msg.text.includes('error');
        if (isError) el.classList.add('error-msg');

        let textHtml = styleHashtags(renderMarkdown(msg.text));

        const senderColor = getColor(msg.sender);
        const isSelf = msg.sender.toLowerCase() === username.toLowerCase();
        el.classList.add(isSelf ? 'self' : 'other');

        let attachmentsHtml = '';
        if (msg.attachments && msg.attachments.length > 0) {
            attachmentsHtml = '<div class="msg-attachments">';
            for (const att of msg.attachments) {
                attachmentsHtml += `<img src="${escapeHtml(att.url)}" alt="${escapeHtml(att.name)}" data-note="${escapeHtml(att.note || '')}" onclick="openImageModal('${escapeHtml(att.url)}')">`;
            }
            attachmentsHtml += '</div>';
        }

        // Reply quote (if this message is a reply)
        let replyHtml = '';
        if (msg.reply_to !== undefined && msg.reply_to !== null) {
            const parentEl = document.querySelector(`.message[data-id="${msg.reply_to}"]`);
            if (parentEl) {
                const parentSender = parentEl.querySelector('.msg-sender')?.textContent || '?';
                const parentText = parentEl.dataset.rawText || parentEl.querySelector('.msg-text')?.textContent || '';
                const truncated = parentText.length > 80 ? parentText.slice(0, 80) + '...' : parentText;
                const parentColor = parentEl.querySelector('.msg-sender')?.style.color || 'var(--text-dim)';
                replyHtml = `<div class="reply-quote" onclick="scrollToMessage(${msg.reply_to})"><span class="reply-sender" style="color: ${parentColor}">${escapeHtml(parentSender)}</span> ${escapeHtml(truncated)}</div>`;
            }
        }

        const agentKey = (resolveAgent(msg.sender.toLowerCase()) || msg.sender).toLowerCase();
        const avatarHtml = `<div class="avatar-wrap" data-agent="${escapeHtml(agentKey)}"><div class="avatar" style="background-color: ${senderColor}">${getAvatarSvg(msg.sender)}</div></div>`;

        el.dataset.rawText = msg.text;
        el.innerHTML = `${isSelf ? '' : avatarHtml}<div class="chat-bubble" style="--bubble-color: ${senderColor}">${replyHtml}<div class="bubble-header"><span class="msg-sender" style="color: ${senderColor}">${escapeHtml(getAgentDisplayName(msg.sender))}</span><span class="msg-time">${msg.time || ''}</span><span class="msg-num">#${msg.id}</span><div class="message-toolbar" role="group" aria-label="Message actions"><button class="create-task-pill" onclick="createTaskFromMessage(${msg.id}); event.stopPropagation();" title="Create a Board task from this message">Create task</button><button class="bubble-copy" onclick="copyMessage(${msg.id}, event)" title="Copy message"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button><button class="reply-btn" onclick="startReply(${msg.id}, event)">Reply</button><button class="delete-btn" onclick="deleteClick(${msg.id}, event)" title="Delete">Delete</button></div></div><div class="msg-text">${textHtml}</div>${attachmentsHtml}</div>`;

        // Add copy buttons to code blocks
        addCodeCopyButtons(el);
    }

    // Hide messages from other channels
    if (msgChannel !== activeChannel) {
        el.style.display = 'none';
        // Track unread for background channels (skip joins/leaves and initial history load)
        if (!options.history && soundEnabled && msg.type !== 'join' && msg.type !== 'leave') {
            channelUnread[msgChannel] = (channelUnread[msgChannel] || 0) + 1;
            renderChannelTabs();
            // Play soft pluck for cross-channel chat messages from others (only when focused)
            if (document.hasFocus() && msg.type === 'chat' && msg.sender && msg.sender.toLowerCase() !== username.toLowerCase()) {
                playCrossChannelSound();
            }
        }
    }

    container.appendChild(el);

    if (options.history || msgChannel !== activeChannel) return;  // history scrolls once per batch

    if (autoScroll) {
        scrollToBottom();
    } else {
        unreadCount++;
        updateScrollAnchor();
    }
}

function getSenderClass(sender) {
    const s = sender.toLowerCase();
    if (s === 'system') return 'system';
    if (resolveAgent(s)) return 'agent';
    // Check base colors for offline agents
    const base = s.replace(/-\d+$/, '');
    if (base in baseColors) return 'agent';
    return 'user';
}

function resolveAgent(name) {
    const s = name.toLowerCase();
    if (s in agentConfig) return s;
    // Try prefix match: "gemini-cli" → "gemini"
    for (const key of Object.keys(agentConfig)) {
        if (!agentConfig[key].managed_profile && s.startsWith(key)) return key;
    }
    return null;
}

function getAgentDisplayName(sender) {
    const key = sender.toLowerCase();
    return agentConfig[key]?.label || baseColors[key]?.label || sender;
}

function getColor(sender) {
    const s = sender.toLowerCase();
    if (s === 'system') return 'var(--system-color)';
    const resolved = resolveAgent(s);
    if (resolved) {
        if (colorOverrides[resolved]) return colorOverrides[resolved];
        return agentConfig[resolved].color;
    }
    // Check overrides for unresolved names too
    if (colorOverrides[s]) return colorOverrides[s];
    // Fall back to base agent colors (for historical messages from offline agents)
    const base = s.replace(/-\d+$/, '');
    if (colorOverrides[base]) return colorOverrides[base];
    if (base in baseColors) return baseColors[base].color;
    return 'var(--user-color)';
}

function colorMentions(textHtml) {
    // Match any @word — we'll resolve color per match
    return textHtml.replace(/@(\w[\w-]*)/gi, (match, name) => {
        const lower = name.toLowerCase();
        if (lower === 'both' || lower === 'all') {
            return `<span class="mention" style="color: var(--accent)">@${name}</span>`;
        }
        const resolved = resolveAgent(lower);
        if (resolved) {
            const color = getColor(resolved);
            return `<span class="mention" style="color: ${color}">@${name}</span>`;
        }
        // Non-agent mention (e.g. @user, @user) — use user color
        return `<span class="mention" style="color: var(--user-color)">@${name}</span>`;
    });
}

function scrollToBottom(instant = false) {
    const timeline = document.getElementById('timeline');
    autoScroll = true;
    timeline.scrollTo({ top: timeline.scrollHeight, behavior: instant ? 'instant' : 'auto' });
    unreadCount = 0;
    updateScrollAnchor();
}
window.scrollToBottom = scrollToBottom;
window.appendMessage = appendMessage;
// Streaming updates must obey the same reading-position preference as messages.
window.followIncomingContent = function () {
    if (autoScroll) scrollToBottom();
    else {
        unreadCount = Math.max(1, unreadCount);
        updateScrollAnchor();
    }
};

function updateScrollAnchor() {
    const anchor = document.getElementById('scroll-anchor');
    if (autoScroll || unreadCount === 0) {
        anchor.classList.add('hidden');
    } else {
        anchor.classList.remove('hidden');
        const badge = anchor.querySelector('.unread-badge');
        if (badge) {
            badge.textContent = unreadCount;
            badge.style.display = unreadCount > 0 ? 'flex' : 'none';
        }
    }
    repositionScrollAnchor();
}

function repositionScrollAnchor() {
    const anchor = document.getElementById('scroll-anchor');
    if (!anchor) return;
    const footer = document.querySelector('#app footer');
    if (footer) {
        anchor.style.bottom = (footer.offsetHeight + 12) + 'px';
    }
    const timeline = document.getElementById('timeline');
    if (timeline) {
        const rect = timeline.getBoundingClientRect();
        anchor.style.left = (rect.left + rect.width / 2) + 'px';
    }
}

// --- Agents ---

function applyAgentConfig(data) {
    agentConfig = {};
    for (const [name, cfg] of Object.entries(data)) {
        agentConfig[name.toLowerCase()] = cfg;
        if (cfg.managed_profile) delete colorOverrides[name.toLowerCase()];
    }
    buildStatusPills();
    buildMentionToggles();
    buildSoundSettings();
    // Re-color any messages already rendered (e.g. from a reconnect)
    recolorMessages();
}

function recolorMessages() {
    const msgs = document.querySelectorAll('.message[data-id]');
    for (const el of msgs) {
        const sender = el.querySelector('.msg-sender');
        if (!sender) continue;
        const name = el.dataset.sender || sender.textContent.trim();
        const color = getColor(name);
        sender.textContent = getAgentDisplayName(name);
        sender.title = '@' + name;
        sender.style.color = color;
        // Update bubble color
        const bubble = el.querySelector('.chat-bubble');
        if (bubble) bubble.style.setProperty('--bubble-color', color);
        // Update avatar color
        const avatar = el.querySelector('.avatar');
        if (avatar) avatar.style.backgroundColor = color;
        // Re-render markdown with updated mention colors and hashtags
        const textEl = el.querySelector('.msg-text');
        if (textEl && el.dataset.rawText) {
            textEl.innerHTML = styleHashtags(renderMarkdown(el.dataset.rawText));
            addCodeCopyButtons(el);
        }
    }
}

function buildStatusPills() {
    const container = document.getElementById('agent-status');
    container.innerHTML = '';
    for (const [name, cfg] of Object.entries(agentConfig)) {
        const pill = document.createElement('div');
        pill.className = 'status-pill';
        if (cfg.state === 'pending') pill.classList.add('pending');
        pill.id = `status-${name}`;
        pill.title = `@${name}`;  // Tooltip: canonical name for manual @-typing
        pill.style.setProperty('--agent-color', colorOverrides[name] || cfg.color || '#4ade80');
        pill.innerHTML = `<span class="status-dot"></span><span class="status-label">${escapeHtml(cfg.label || name)}</span>`;
        // Agent names, colors and instructions are managed in Team.
        pill.addEventListener('click', (e) => {
            e.stopPropagation();
            window.dispatchEvent(new Event('workspace-team'));
        });
        container.appendChild(pill);
    }
    enableDragScroll(container);
}

// --- Status ---

function updateStatus(data) {
    for (const [name, info] of Object.entries(data)) {
        if (name === 'paused') continue;
        const pill = document.getElementById(`status-${name}`);
        if (!pill) continue;

        pill.classList.remove('available', 'working', 'offline');
        // Pending pills keep their pending animation (set in buildStatusPills)
        if (!pill.classList.contains('pending')) {
            if (info.busy && info.available) {
                pill.classList.add('working');
            } else if (info.available) {
                pill.classList.add('available');
            } else {
                pill.classList.add('offline');
            }
        }

        // Keep agent color in sync
        if (info.color) pill.style.setProperty('--agent-color', info.color);
    }
}

function updateTyping(agent, active) {
    const indicator = document.getElementById('typing-indicator');
    if (active) {
        indicator.querySelector('.typing-name').textContent = agent;
        indicator.classList.remove('hidden');
        if (autoScroll) scrollToBottom();
    } else {
        indicator.classList.add('hidden');
    }
}

// --- Settings ---

let pendingChannelSwitch = null;

function applySettings(data) {
    if (data.title) {
        document.getElementById('room-title').textContent = data.title;
        // Tab title carries both names, so several servers are distinguishable
        document.title = data.subtitle ? data.title + ' - ' + data.subtitle : data.title;
    }
    if (data.subtitle !== undefined) {
        const subEl = document.getElementById('room-subtitle');
        if (subEl) subEl.textContent = data.subtitle;
        const subInput = document.getElementById('setting-subtitle');
        if (subInput) subInput.value = data.subtitle;
    }
    if (data.username) {
        username = data.username;
        document.getElementById('sender-label').textContent = username;
        document.getElementById('setting-username').value = username;
    }
    if (data.font) {
        document.body.classList.remove('font-mono', 'font-serif', 'font-sans');
        document.body.classList.add('font-' + data.font);
        document.getElementById('setting-font').value = data.font;
    }
    if (data.history_limit !== undefined) {
        document.getElementById('setting-history').value = String(data.history_limit);
    }
    if (data.contrast) {
        document.body.classList.toggle('high-contrast', data.contrast === 'high');
        document.getElementById('setting-contrast').value = data.contrast;
    }
    if (Array.isArray(data.sessions)) window.applyProjectSessions?.(data);

}

function toggleSettings() {
    const bar = document.getElementById('settings-bar');
    bar.classList.toggle('hidden');
    document.getElementById('settings-toggle').classList.toggle('active', !bar.classList.contains('hidden'));
    if (!bar.classList.contains('hidden')) {
        document.getElementById('setting-username').focus();
    }
}

function _clearClearChatConfirm() {
    const btn = document.getElementById('clear-chat-btn');
    const confirmEl = document.getElementById('clear-chat-confirm');
    if (confirmEl) confirmEl.remove();
    if (btn) {
        btn.textContent = 'Clear Chat';
        btn.classList.remove('confirming');
    }
    document.removeEventListener('click', _clearChatOutsideClick, true);
}

function _clearChatOutsideClick(e) {
    const btn = document.getElementById('clear-chat-btn');
    const confirmEl = document.getElementById('clear-chat-confirm');
    if (!btn || !confirmEl) return;
    if (!btn.contains(e.target) && !confirmEl.contains(e.target)) {
        _clearClearChatConfirm();
    }
}

function clearChat() {
    if (window.chatSessionReadonly?.(activeChannel)) return;
    const btn = document.getElementById('clear-chat-btn');
    if (!btn) return;

    // Second click -> execute. First click -> inline confirm, matching the
    // End Session pattern elsewhere.
    if (btn.classList.contains('confirming')) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'message', text: '/clear', sender: username, channel: activeChannel }));
        }
        _clearClearChatConfirm();
        document.getElementById('settings-bar').classList.add('hidden');
        return;
    }

    btn.textContent = 'Clear Chat?';
    btn.classList.add('confirming');

    const confirmWrap = document.createElement('span');
    confirmWrap.id = 'clear-chat-confirm';
    confirmWrap.className = 'inline-confirm';
    confirmWrap.innerHTML = `
        <button class="inline-confirm-yes ch-confirm-yes" title="Confirm clear chat">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5 6.5-7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="inline-confirm-no ch-confirm-no" title="Cancel">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        </button>
    `;
    btn.parentElement.insertBefore(confirmWrap, btn);

    confirmWrap.querySelector('.ch-confirm-yes').onclick = (e) => {
        e.stopPropagation();
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'message', text: '/clear', sender: username, channel: activeChannel }));
        }
        _clearClearChatConfirm();
        document.getElementById('settings-bar').classList.add('hidden');
    };
    confirmWrap.querySelector('.ch-confirm-no').onclick = (e) => {
        e.stopPropagation();
        _clearClearChatConfirm();
    };

    setTimeout(() => document.addEventListener('click', _clearChatOutsideClick, true), 0);
}

function saveSettings() {
    const newUsername = document.getElementById('setting-username').value.trim();
    const newSubtitle = document.getElementById('setting-subtitle').value.trim();
    const newFont = document.getElementById('setting-font').value;
    const histVal = document.getElementById('setting-history').value;
    const newHistory = histVal === 'all' ? 'all' : (parseInt(histVal) || 50);
    const newContrast = document.getElementById('setting-contrast').value;

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'update_settings',
            data: {
                username: newUsername || 'user',
                subtitle: newSubtitle,
                font: newFont,
                history_limit: newHistory,
                contrast: newContrast,
            }
        }));
    }
}

function setupSettingsKeys() {
    // Auto-save on blur/Enter for text/number fields
    for (const id of ['setting-username', 'setting-subtitle']) {
        const el = document.getElementById(id);
        el.addEventListener('blur', () => saveSettings());
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                el.blur();
            }
            if (e.key === 'Escape') {
                toggleSettings();
            }
        });
    }

    // Auto-save on change for selects, escape to close
    for (const id of ['setting-font', 'setting-history', 'setting-contrast']) {
        const el = document.getElementById(id);
        el.addEventListener('change', () => {
            // Apply contrast immediately (don't wait for server round-trip)
            if (id === 'setting-contrast') {
                document.body.classList.toggle('high-contrast', el.value === 'high');
            }
            saveSettings();
        });
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                toggleSettings();
            }
        });
    }
}

// --- Toast notifications ---

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('toast--visible'));
    setTimeout(() => {
        toast.classList.remove('toast--visible');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// --- Export / Import ---

async function exportHistory() {
    try {
        const resp = await fetch('/api/export', {
            headers: { 'X-Session-Token': SESSION_TOKEN },
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
            headers: { 'X-Session-Token': SESSION_TOKEN },
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

// --- Keyboard shortcuts ---

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('image-modal');
        const modalOpen = modal && !modal.classList.contains('hidden');

        if (e.key === 'Escape') {
            if (modalOpen) { closeImageModal(); return; }
            if (replyingTo) { cancelReply(); }
        }
        if (modalOpen && e.key === 'ArrowLeft') { e.preventDefault(); modalPrev(e); }
        if (modalOpen && e.key === 'ArrowRight') { e.preventDefault(); modalNext(e); }

    });
}

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
        candidates.push({ name, label: cfg.label || name, color: cfg.color });
    }
    candidates.push({ name: 'all agents', label: 'all agents', color: 'var(--accent)' });
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
        // Optimization: don't look back more than 30 chars
        if (cursor - i > 30) break;
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
        row.innerHTML = `<span class="mention-dot" style="background: ${item.color}"></span><span class="mention-name">${escapeHtml(item.label)}</span>`;
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

// --- Input ---

function setupInput() {
    const input = document.getElementById('input');

    input.addEventListener('keydown', (e) => {
        if (mentionMenuVisible) {
            const menu = document.getElementById('mention-menu');
            const items = menu.querySelectorAll('.mention-item');
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                mentionMenuIndex = (mentionMenuIndex - 1 + items.length) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === mentionMenuIndex));
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                mentionMenuIndex = (mentionMenuIndex + 1) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === mentionMenuIndex));
                return;
            }
            if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                e.preventDefault();
                const active = items[mentionMenuIndex];
                if (active) {
                    selectMention(active.dataset.name);
                }
                return;
            }
            if (e.key === 'Escape') {
                menu.classList.add('hidden');
                mentionMenuVisible = false;
                return;
            }
        }
        if (slashMenuVisible) {
            const menu = document.getElementById('slash-menu');
            const items = menu.querySelectorAll('.slash-item');
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                slashMenuIndex = (slashMenuIndex - 1 + items.length) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === slashMenuIndex));
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                slashMenuIndex = (slashMenuIndex + 1) % items.length;
                items.forEach((el, i) => el.classList.toggle('active', i === slashMenuIndex));
                return;
            }
            if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                e.preventDefault();
                const active = items[slashMenuIndex];
                if (active) selectSlashCommand(active.querySelector('.slash-cmd').textContent);
                if (e.key === 'Enter') sendMessage();
                return;
            }
            if (e.key === 'Escape') {
                menu.classList.add('hidden');
                slashMenuVisible = false;
                return;
            }
        }
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize + slash menu + mention menu + send button state
    function onInputChange() {
        restartVoiceAfterEdit();
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
        updateSlashMenu(input.value);
        updateMentionMenu();
        updateSendButton();
    }
    input.addEventListener('input', onInputChange);
    // Voice typing doesn't always fire 'input' — catch with additional events
    input.addEventListener('compositionend', onInputChange);
    input.addEventListener('change', onInputChange);
    // Fallback poll for speech-to-text that bypasses all events
    let _lastInputVal = '';
    setInterval(() => {
        if (input.value !== _lastInputVal) {
            _lastInputVal = input.value;
            updateSendButton();
        }
    }, 300);
    updateSendButton();
}

function updateSendButton() {
    const input = document.getElementById('input');
    const group = document.querySelector('.send-group');
    if (!input || !group) return;
    const hasContent = input.value.trim().length > 0 || pendingAttachments.length > 0;
    group.classList.toggle('inactive', !hasContent);
}

function sendMessage() {
    if (window.chatSessionReadonly?.(activeChannel)) return;
    const input = document.getElementById('input');
    let text = input.value.trim();

    if (document.body.classList.contains('modern-workspace') && /^\/goal(?:\s|$)/i.test(text)) {
        window.dispatchEvent(new CustomEvent('workspace-goal', {detail: text.replace(/^\/goal\s*/i, '')}));
        input.value = '';
        input.style.height = 'auto';
        updateSendButton();
        return;
    }

    if (!text && pendingAttachments.length === 0) return;

    // Prepend active mention toggles if the message doesn't already mention them
    // Skip for non-broadcast slash commands (e.g. /clear, /continue)
    let skipMentions = false;
    if (text.startsWith('/')) {
        const cmdWord = text.split(/\s/)[0].toLowerCase();
        const matchedCmd = SLASH_COMMANDS.find(c => c.cmd.startsWith(cmdWord) || cmdWord.startsWith(c.cmd.split(/\s/)[0]));
        if (matchedCmd && !matchedCmd.broadcast) {
            skipMentions = true;
        }
    }
    if (activeMentions.size > 0 && text && !skipMentions) {
        const prefix = [...activeMentions].map(n => `@${n}`).join(' ');
        // Only prepend if user didn't already @mention these agents
        const lower = text.toLowerCase();
        const missing = [...activeMentions].filter(n => !lower.includes(`@${n}`));
        if (missing.length > 0) {
            text = missing.map(n => `@${n}`).join(' ') + ' ' + text;
        }
    }

    const payload = {
        type: 'message',
        text: text,
        sender: username,
        channel: activeChannel,
        project_id: window.activeProjectId,
        attachments: pendingAttachments.map(a => ({
            path: a.path,
            name: a.name,
            url: a.url,
            ...(a.note ? { note: a.note } : {}),
            ...(a.source ? { source: a.source } : {}),
        })),
    };
    if (replyingTo) {
        payload.reply_to = replyingTo.id;
    }

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
    }

    input.value = '';
    restartVoiceAfterEdit();
    input.style.height = 'auto';
    clearAttachments();
    cancelReply();
    updateSendButton();
    input.focus();
}

// --- Image paste/drop ---

function setupPaste() {
    document.addEventListener('paste', async (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;

        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                await uploadImage(item.getAsFile());
            }
        }
    });
}

function setupDragDrop() {
    const dropzone = document.getElementById('dropzone');
    let dragCount = 0;

    document.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCount++;
        if (e.dataTransfer?.types?.includes('Files')) {
            dropzone.classList.remove('hidden');
        }
    });

    document.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dragCount--;
        if (dragCount <= 0) {
            dragCount = 0;
            dropzone.classList.add('hidden');
        }
    });

    document.addEventListener('dragover', (e) => {
        e.preventDefault();
    });

    document.addEventListener('drop', async (e) => {
        e.preventDefault();
        dragCount = 0;
        dropzone.classList.add('hidden');

        const files = e.dataTransfer?.files;
        if (!files) return;

        for (const file of files) {
            if (file.type.startsWith('image/')) {
                await uploadImage(file);
            }
        }
    });
}

async function uploadImage(file) {
    if (window.chatSessionReadonly?.(activeChannel)) return;
    const uploadSession = activeChannel;
    const form = new FormData();
    form.append('file', file);

    try {
        const resp = await fetch('/api/upload', { method: 'POST', headers: { 'X-Session-Token': SESSION_TOKEN }, body: form });
        const data = await resp.json();

        const attachment = { path: data.path, name: data.name, url: data.url };
        if (activeChannel === uploadSession) pendingAttachments.push(attachment);
        else {
            const draft = sessionDrafts.get(uploadSession) || { text: '', attachments: [] };
            draft.attachments.push(attachment); sessionDrafts.set(uploadSession, draft);
        }

        renderAttachments();
    } catch (err) {
        console.error('Upload failed:', err);
    }
}

function renderAttachments() {
    const container = document.getElementById('attachments');
    container.innerHTML = '';

    pendingAttachments.forEach((att, i) => {
        const wrap = document.createElement('div');
        wrap.className = 'attachment-preview';
        wrap.innerHTML = `
            <img src="${att.url}" alt="${escapeHtml(att.name)}" data-note="${escapeHtml(att.note || '')}" onclick="openImageModal('${escapeHtml(att.url)}')" title="Click to preview">
            <button class="remove-btn" onclick="removeAttachment(${i})">x</button>
        `;
        container.appendChild(wrap);
    });
    repositionScrollAnchor();
}

function removeAttachment(index) {
    pendingAttachments.splice(index, 1);
    renderAttachments();
}

function clearAttachments() {
    pendingAttachments = [];
    document.getElementById('attachments').innerHTML = '';
    repositionScrollAnchor();
}

// --- Scroll tracking ---

function setupScroll() {
    const timeline = document.getElementById('timeline');
    const messages = document.getElementById('messages');
    const anchor = document.getElementById('scroll-anchor');
    anchor.setAttribute('role', 'button');
    anchor.setAttribute('tabindex', '0');
    anchor.setAttribute('aria-label', 'New messages: jump to latest');
    anchor.style.width = 'auto';
    anchor.style.padding = '0 16px';
    anchor.style.gap = '8px';
    const label = document.createElement('span');
    label.textContent = 'New messages';
    anchor.append(label);
    anchor.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            scrollToBottom();
        }
    });

    timeline.addEventListener('wheel', (event) => {
        if (event.deltaY < 0) autoScroll = false;
    }, { passive: true });

    timeline.addEventListener('scroll', () => {
        const distFromBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight;
        autoScroll = distFromBottom < 60;

        if (autoScroll) {
            unreadCount = 0;
        }
        updateScrollAnchor();
        dayFloatOnScroll();
    });

    // Keep pinned to bottom when content changes (e.g. images load)
    const resizeObserver = new ResizeObserver(() => {
        if (autoScroll && !historyLoading) {
            scrollToBottom();
        }
    });
    resizeObserver.observe(messages);

    // Reposition scroll-anchor when window resizes or sidebars toggle
    window.addEventListener('resize', repositionScrollAnchor);
    const contentArea = document.querySelector('.content-area');
    if (contentArea) {
        new ResizeObserver(repositionScrollAnchor).observe(contentArea);
    }

    // Support button label collapses via CSS overflow (grid column shrinks naturally).
    // No JS measurement needed.
}

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
            setTimeout(() => btn.classList.remove('copied'), 1500);
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
    const truncated = replyingTo.text.length > 100 ? replyingTo.text.slice(0, 100) + '...' : replyingTo.text;
    const color = getColor(replyingTo.sender);
    container.innerHTML = `<span class="reply-preview-label">replying to</span> <span style="color: ${color}; font-weight: 600">${escapeHtml(replyingTo.sender)}</span>: ${escapeHtml(truncated)} <button class="dismiss-btn reply-cancel" onclick="cancelReply()">&times;</button>`;
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
    setTimeout(() => el.classList.remove('highlight'), 1500);
}

// --- Todos ---

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
    if (!ws || deleteSelected.size === 0) return;
    ws.send(JSON.stringify({ type: 'delete', project_id: window.activeProjectId, ids: [...deleteSelected] }));
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
        el.style.opacity = '0';
        el.style.transition = 'opacity 0.2s';
        setTimeout(() => el.remove(), 200);
    });

    repositionScrollAnchor();
}

// Auto-scroll while dragging near edges
let deleteScrollInterval = null;
document.addEventListener('mousemove', (e) => {
    if (!deleteDragging) return;
    const timeline = document.getElementById('timeline');
    const rect = timeline.getBoundingClientRect();
    const edgeZone = 60;

    if (e.clientY < rect.top + edgeZone) {
        // Near top — scroll up
        if (!deleteScrollInterval) {
            deleteScrollInterval = setInterval(() => timeline.scrollTop -= 8, 16);
        }
    } else if (e.clientY > rect.bottom - edgeZone) {
        // Near bottom — scroll down
        if (!deleteScrollInterval) {
            deleteScrollInterval = setInterval(() => timeline.scrollTop += 8, 16);
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

// --- Mention toggles ---

// Called by switchChannel (channels.js) so the sticky @-tag toggles are
// remembered per channel. Prevents carrying a tag (e.g. @codex from #bugfixing)
// into a channel where you meant to tag someone else.
window._onChannelSwitchMentions = function(oldChannel, newChannel) {
    if (oldChannel) _channelMentions[oldChannel] = [...activeMentions];
    activeMentions = new Set(_channelMentions[newChannel] || []);
    // Reflect the swapped state on the toggle buttons
    for (const btn of document.querySelectorAll('.mention-toggle')) {
        btn.classList.toggle('active', activeMentions.has(btn.dataset.agent));
    }
    if (typeof updateSchedulePopoverState === 'function') updateSchedulePopoverState();
};

function buildMentionToggles() {
    const container = document.getElementById('mention-toggles');
    container.innerHTML = '';

    // Prune stale mentions for agents no longer in config
    for (const name of activeMentions) {
        if (!(name in agentConfig)) activeMentions.delete(name);
    }

    for (const [name, cfg] of Object.entries(agentConfig)) {
        if (cfg.state === 'pending') continue;  // skip pending instances
        const btn = document.createElement('button');
        btn.className = 'mention-toggle';
        btn.dataset.agent = name;
        btn.textContent = `@${cfg.label || name}`;
        btn.title = `@${name}`;  // Tooltip: canonical name
        btn.style.setProperty('--agent-color', colorOverrides[name] || cfg.color);
        // Restore active state for mentions that survived the rebuild
        if (activeMentions.has(name)) {
            btn.classList.add('active');
        }
        btn.onclick = () => {
            if (activeMentions.has(name)) {
                activeMentions.delete(name);
                btn.classList.remove('active');
            } else {
                activeMentions.add(name);
                btn.classList.add('active');
            }

        };
        container.appendChild(btn);
    }
    enableDragScroll(container);
}

// --- Voice typing ---

let recognition = null;
let isListening = false;
let voiceText = '';
let voiceRestartPending = false;
let voiceRestartTimer = null;

function focusComposerInput() {
    const input = document.getElementById('input');
    if (!input) return null;
    try {
        input.focus({ preventScroll: true });
    } catch (_) {
        input.focus();
    }
    return input;
}

function toggleVoice() {
    if (isListening) {
        stopVoice();
        return;
    }
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
        alert('Speech recognition not supported — use Chrome or Edge.');
        return;
    }
    if (!focusComposerInput()) return;

    // Reserve the recording immediately, including while start/permission is pending.
    isListening = true;
    const micButton = document.getElementById('mic');
    micButton.classList.add('recording');
    micButton.setAttribute('aria-pressed', 'true');
    startVoiceRecognition();
}

function scheduleVoiceRestart() {
    clearTimeout(voiceRestartTimer);
    // Avoid restarting the microphone for every keystroke while the user edits.
    voiceRestartTimer = setTimeout(startVoiceRecognition, 250);
}

function restartVoiceAfterEdit() {
    const input = document.getElementById('input');
    if (!isListening || !input || input.value === voiceText) return;
    voiceText = input.value;
    if (!recognition) {
        scheduleVoiceRestart();
    } else if (!voiceRestartPending) {
        voiceRestartPending = true;
        try { recognition.abort(); } catch (_) { stopVoice(); }
    }
}

function startVoiceRecognition() {
    clearTimeout(voiceRestartTimer);
    voiceRestartTimer = null;
    if (!isListening || recognition) return;
    const input = document.getElementById('input');
    const baseText = input.value;
    voiceText = baseText;
    voiceRestartPending = false;

    try {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const current = new SpeechRecognition();
        recognition = current;
        current.lang = 'en-GB';
        current.continuous = true;
        current.interimResults = true;

        current.onresult = (e) => {
            if (!isListening || recognition !== current || voiceRestartPending) return;
            // Also protect programmatic edits that did not dispatch an input event.
            if (input.value !== voiceText) {
                restartVoiceAfterEdit();
                return;
            }
            const transcript = Array.from(e.results, result => result[0].transcript).join('');
            const separator = baseText && transcript && !/\s$/.test(baseText) && !/^\s/.test(transcript) ? ' ' : '';
            voiceText = baseText + separator + transcript;
            input.value = voiceText;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        };

        current.onerror = (e) => {
            if (!isListening || recognition !== current) return;
            if (e.error === 'no-speech' || e.error === 'aborted') return;
            stopVoice();
            if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
                alert('Microphone access was blocked. Allow microphone access in Chrome and try again.');
            } else {
                console.error('Speech error:', e.error);
            }
        };

        current.onend = () => {
            if (!isListening || recognition !== current) return;
            recognition = null;
            if (voiceRestartPending) scheduleVoiceRestart();
            else startVoiceRecognition();
        };

        current.start();
    } catch (e) {
        console.error('Speech start failed:', e);
        stopVoice();
    }
}

function stopVoice() {
    isListening = false;
    clearTimeout(voiceRestartTimer);
    voiceRestartTimer = null;
    voiceRestartPending = false;
    const current = recognition;
    recognition = null;
    const micButton = document.getElementById('mic');
    if (micButton) {
        micButton.classList.remove('recording');
        micButton.setAttribute('aria-pressed', 'false');
    }
    // Invalidate this run before abort: its late events must not touch a new run.
    if (current) {
        try { current.abort(); } catch (_) {}
    }
    focusComposerInput();
}

// --- Image modal ---

let modalImages = [];  // all image URLs in chat
let modalIndex = 0;    // current image index

function getAllChatImages() {
    const imgs = document.querySelectorAll('.msg-attachments img');
    return [...imgs].map(img => img.src);
}

function openImageModal(url) {
    modalImages = getAllChatImages();
    // Match by endsWith since onclick passes relative URL but img.src is absolute
    modalIndex = modalImages.findIndex(src => src.endsWith(url) || src === url);
    if (modalIndex === -1) {
        // Image not in chat gallery (e.g. composer preview) — show it standalone
        modalImages = [url];
        modalIndex = 0;
    }

    let modal = document.getElementById('image-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'image-modal';
        modal.className = 'hidden';
        modal.innerHTML = `<button class="modal-nav modal-prev" onclick="modalPrev(event)">&lsaquo;</button><img onclick="event.stopPropagation()"><button class="modal-nav modal-next" onclick="modalNext(event)">&rsaquo;</button><span class="modal-counter"></span>`;
        modal.addEventListener('click', closeImageModal);
        const tools = document.createElement('div');
        tools.className = 'image-modal-tools';
        Object.assign(tools.style, { position: 'absolute', top: '16px', right: '20px', display: 'flex', gap: '12px' });
        tools.addEventListener('click', event => event.stopPropagation());
        const annotate = document.createElement('button');
        annotate.textContent = 'Annotate a copy';
        annotate.className = 'settings-btn';
        annotate.onclick = () => {
            const src = modalImages[modalIndex];
            closeImageModal();
            window.ZeroAnnotate?.(src, attachment => {
                pendingAttachments.push(attachment);
                renderAttachments();
                updateSendButton();
                document.getElementById('input').focus();
            });
        };
        const close = document.createElement('button');
        close.textContent = 'Close ✕';
        close.className = 'settings-btn';
        close.onclick = closeImageModal;
        tools.append(annotate, close);
        modal.append(tools);
        const notes = document.createElement('details');
        notes.className = 'image-modal-notes';
        Object.assign(notes.style, { position: 'absolute', bottom: '16px', left: '5%', width: '90%', maxHeight: '30vh', overflow: 'auto', background: '#151b20', color: '#e8ecee', padding: '12px', borderRadius: '8px' });
        notes.addEventListener('click', event => event.stopPropagation());
        const summary = document.createElement('summary');
        summary.textContent = 'Image notes / message';
        const body = document.createElement('p');
        body.style.whiteSpace = 'pre-wrap';
        notes.append(summary, body);
        modal.append(notes);
        document.body.appendChild(modal);
    }
    updateModalImage(modal);
    modal.classList.remove('hidden');
}

function updateModalImage(modal) {
    if (!modal) modal = document.getElementById('image-modal');
    if (!modal || modalImages.length === 0) return;
    modal.querySelector('img').src = modalImages[modalIndex];
    const imageUrl = new URL(modalImages[modalIndex], location.href);
    const sourceImage = [...document.querySelectorAll('.msg-attachments img')].find(img => img.src === imageUrl.href);
    const draft = pendingAttachments.find(a => new URL(a.url, location.href).href === imageUrl.href);
    const noteText = draft?.note || sourceImage?.dataset.note || sourceImage?.closest('.message')?.querySelector('.msg-text')?.textContent || '';
    const notes = modal.querySelector('.image-modal-notes');
    if (notes) { notes.hidden = !noteText; notes.querySelector('p').textContent = noteText; }
    const annotate = modal.querySelector('.image-modal-tools button');
    if (annotate) annotate.hidden = !window.ZeroAnnotate || imageUrl.origin !== location.origin || !imageUrl.pathname.startsWith('/uploads/') || !/\.(png|jpe?g|gif|webp)$/i.test(imageUrl.pathname);
    const counter = modal.querySelector('.modal-counter');
    if (counter) {
        counter.textContent = `${modalIndex + 1} / ${modalImages.length}`;
    }
    // Hide arrows at beginning/end, or if only one image
    const prev = modal.querySelector('.modal-prev');
    const next = modal.querySelector('.modal-next');
    if (prev) prev.style.display = modalIndex > 0 ? 'flex' : 'none';
    if (next) next.style.display = modalIndex < modalImages.length - 1 ? 'flex' : 'none';
}

function modalPrev(event) {
    event.stopPropagation();
    if (modalIndex <= 0) return;
    modalIndex--;
    updateModalImage();
}

function modalNext(event) {
    event.stopPropagation();
    if (modalIndex >= modalImages.length - 1) return;
    modalIndex++;
    updateModalImage();
}

function closeImageModal() {
    const modal = document.getElementById('image-modal');
    if (modal) modal.classList.add('hidden');
}

async function _preserveScroll(fn) {
    const timeline = document.getElementById('timeline');
    if (!timeline) { await fn(); return; }

    // Check if we are at the bottom (with a small buffer)
    const wasAtBottom = autoScroll || (timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 60);
    const topId = _getTopVisibleMsgId();

    // Save the exact pixel offset of the top visible message
    let savedOffset = 0;
    if (topId) {
        const el = document.querySelector(`.message[data-id="${topId}"]`);
        if (el) {
            savedOffset = el.getBoundingClientRect().top - timeline.getBoundingClientRect().top;
        }
    }

    // Disable smooth scrolling for instant correction
    const oldSmooth = timeline.style.scrollBehavior;
    timeline.style.scrollBehavior = 'auto';

    await fn();

    // Continuously correct scroll during sidebar CSS transition
    function correctScroll() {
        if (wasAtBottom) {
            timeline.scrollTop = timeline.scrollHeight;
        } else if (topId) {
            const el = document.querySelector(`.message[data-id="${topId}"]`);
            if (el) {
                const newRect = el.getBoundingClientRect();
                const timelineRect = timeline.getBoundingClientRect();
                timeline.scrollTop += (newRect.top - timelineRect.top) - savedOffset;
            }
        }
    }

    // Correct immediately
    void timeline.scrollHeight;
    correctScroll();

    // Keep correcting each frame during the transition (~300ms)
    let frames = 0;
    function tick() {
        correctScroll();
        if (++frames < 20) requestAnimationFrame(tick); // ~333ms at 60fps
        else timeline.style.scrollBehavior = oldSmooth;
    }
    requestAnimationFrame(tick);
}
window._preserveScroll = _preserveScroll;

// Style #hashtags in rendered message text
function styleHashtags(html) {
    // "Match and skip" pattern: consume HTML tags first (to skip hex colors in
    // style attributes like color: #da7756), then match real hashtags in text.
    return html.replace(/<[^>]*>|((?:^|\s))(#([a-zA-Z][a-zA-Z0-9_-]{0,39}))\b/g,
        (match, prefix, fullHash, tag) => {
            if (tag === undefined) return match; // HTML tag — skip
            const lower = tag.toLowerCase();
            if (['clear', 'off', 'none', 'end'].includes(lower)) {
                return `${prefix}<span class="msg-hashtag" style="opacity:0.5">#${tag}</span>`;
            }
            return `${prefix}<span class="msg-hashtag">#${tag}</span>`;
        });
}

// --- Helpers ---


function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
window.escapeHtml = escapeHtml;

// --- Schedules strip ---


// --- Decision card resolve (with fade animation) ---

// --- Message to Board task ---

function createTaskFromMessage(msgId) {
    const el = document.querySelector(`.message[data-id="${msgId}"]`);
    window.dispatchEvent(new CustomEvent('workspace-create-task', { detail: {
        id: String(msgId), text: el?.dataset.rawText || el?.querySelector('.msg-text')?.textContent || '',
    } }));
}

// --- Start ---

document.addEventListener('DOMContentLoaded', init);

// Keep unsent content scoped to a stable session ID, including project switches.
const sessionDrafts = new Map();
window._onSessionDraftSwitch = function(previous, next) {
    const input = document.getElementById('input');
    if (!input) return;
    sessionDrafts.set(previous, { text: input.value, attachments: [...pendingAttachments], reply: replyingTo });
    exitDeleteMode();
    cancelReply();
    const draft = sessionDrafts.get(next);
    replyingTo = draft?.reply || null;
    renderReplyPreview();
    input.value = draft?.text || '';
    pendingAttachments = draft?.attachments ? [...draft.attachments] : [];
    renderAttachments();
    input.dispatchEvent(new Event('input', { bubbles: true }));
};
