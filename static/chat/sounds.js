/* ZeroCollab chat - Notification sounds: per-agent choices, the cross-channel pluck and the settings rows.
 * Loaded after chat.js; shares its global state (see index.html script order). */

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
let soundPrefs = prefObject('zerocollab-sounds');
const soundCache = {};

function playSound(soundName) {
    if (soundName === 'none' || (!document.hasFocus() && prefGet('zerocollab-mute-unfocused-chat', '0') === '1')) return;
    soundCache[soundName] ||= new Audio(`/static/sounds/${soundName}.mp3`);
    const audio = soundCache[soundName];
    audio.currentTime = 0;
    audio.play().catch(() => {});  // ignore autoplay policy errors
}

function playNotificationSound(sender) {
    playSound(soundPrefs[sender.toLowerCase()] || soundPrefs['default'] || DEFAULT_SOUND);
}

function playCrossChannelSound() {
    playSound(soundPrefs['cross-channel'] || CROSS_CHANNEL_SOUND);
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
            : name === 'cross-channel' ? 'Other chats'
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
            prefSet('zerocollab-sounds', JSON.stringify(soundPrefs));
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
