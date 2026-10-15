const { readFileSync, writeFileSync, renameSync, statSync } = require('node:fs');
const { createHash } = require('node:crypto');

const MAX_BYTES = 32768;
const MAX_ROOMS = 8;
const PREFERENCE_INTERVAL_MS = 500;
const CHOICES = Object.freeze({
  'zerocollab-follow': ['0', '1'],
  'zerocollab-board-tab': ['tasks', 'coordination', 'decisions', 'spec', 'memory', 'controls'],
  'zerocollab-board-group': ['status', 'area', 'owner'],
  'zerocollab-board-sort': ['recent', 'oldest', 'title'],
  'zerocollab-board-feed': ['0', '1'],
  'zerocollab-welcome-dismissed': ['0', '1'],
});
const KEYS = Object.keys(CHOICES);

function cleanPreferences(value) {
  const result = {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) return result;
  for (const key of KEYS) if (CHOICES[key].includes(value[key])) result[key] = value[key];
  return result;
}

function cleanWindow(value) {
  if (!value || typeof value !== 'object') return null;
  const { x, y, width, height, maximized } = value;
  if (![x, y, width, height].every(Number.isSafeInteger) ||
      width < 640 || height < 480 || width > 16384 || height > 16384 ||
      Math.abs(x) > 65536 || Math.abs(y) > 65536 || typeof maximized !== 'boolean') return null;
  return { x, y, width, height, maximized };
}

function createUIState(file, reportError) {
  let state = { rooms: {}, window: null };
  try {
    if (statSync(file).size > MAX_BYTES) throw Error('UI state exceeds bound');
    const saved = JSON.parse(readFileSync(file, 'utf8'));
    state.window = cleanWindow(saved.window);
    for (const [key, value] of Object.entries(saved.rooms || {}).slice(-MAX_ROOMS)) {
      if (/^[a-f0-9]{64}$/.test(key)) state.rooms[key] = cleanPreferences(value);
    }
  } catch (error) { if (error.code !== 'ENOENT') reportError(); }
  function persist() {
    try {
      writeFileSync(file + '.tmp', JSON.stringify(state) + '\n', { encoding: 'utf8', mode: 0o600 });
      renameSync(file + '.tmp', file);
    } catch { reportError(); }
  }
  const roomKey = room => createHash('sha256').update(room.toLowerCase()).digest('hex');
  return {
    preferences(room) {
      const saved = state.rooms[roomKey(room)];
      return { keys: KEYS, values: saved || { 'zerocollab-follow': '1' }, interval: PREFERENCE_INTERVAL_MS };
    },
    savePreferences(room, value) {
      const key = roomKey(room), next = cleanPreferences(value);
      if (JSON.stringify(state.rooms[key]) === JSON.stringify(next)) return;
      delete state.rooms[key];
      state.rooms[key] = next;
      while (Object.keys(state.rooms).length > MAX_ROOMS) delete state.rooms[Object.keys(state.rooms)[0]];
      persist();
    },
    window() { return state.window; },
    saveWindow(value) {
      const next = cleanWindow(value);
      if (!next || JSON.stringify(state.window) === JSON.stringify(next)) return;
      state.window = next;
      persist();
    },
  };
}

module.exports = { createUIState };
