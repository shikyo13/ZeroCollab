/* ZeroCollab chat - Voice typing through the Web Speech API.
 * Loaded after chat.js; shares its global state (see index.html script order). */

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
    voiceRestartTimer = setTimeout(startVoiceRecognition, CONFIG.voiceRestartMs);
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
