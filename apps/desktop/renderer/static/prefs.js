/*
 * prefs.js -- Saved browser preferences for the classic chat scripts.
 *
 * localStorage throws when storage is disabled or full, and saved values can be
 * corrupt or of the wrong shape. Preferences are optional, so every read falls
 * back to a default and every write that fails is dropped.
 */

function prefGet(key, fallback = null) {
    try {
        const value = localStorage.getItem(key);
        return value === null ? fallback : value;
    } catch (_) {
        return fallback;
    }
}

function prefSet(key, value) {
    try {
        localStorage.setItem(key, String(value));
    } catch (_) {
        /* not remembered */
    }
}

/** A saved JSON object (a map of name -> setting); anything else yields {}. */
function prefObject(key) {
    try {
        const parsed = JSON.parse(prefGet(key, '{}'));
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch (_) {
        return {};
    }
}
