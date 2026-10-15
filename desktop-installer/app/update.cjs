"use strict";
const status = document.getElementById('status');
const detail = document.getElementById('detail');
const check = document.getElementById('check');
const back = document.getElementById('back');
const captions = { checking: 'Checking for updates...', current: 'You have the latest desktop app.',
  downloading: 'Downloading the next version...', ready: 'Update downloaded.',
  draining: 'Finishing agent turns and saving the room...', restarting: 'Installing and reopening ZeroCollab...',
  failed: 'The desktop update could not complete.' };
function render(state) {
  status.textContent = (captions[state.phase] || 'Updates unavailable.') +
    (state.phase === 'downloading' ? ` ${state.percent || 0}%` : '');
  detail.textContent = state.error || (state.phase === 'draining' ? 'Running turns finish before the app restarts.' : '');
  check.hidden = !['current', 'failed', 'ready'].includes(state.phase);
  back.hidden = !state.canBack;
}
if (window.desktopUpdate) {
  window.desktopUpdate.onState(render);
  window.desktopUpdate.state().then(render);
  check.onclick = () => window.desktopUpdate.check().then(render);
  back.onclick = () => window.desktopUpdate.back();
} else render({ phase: 'failed', error: 'Open Updates from the ZeroCollab desktop app.', canBack: false });
