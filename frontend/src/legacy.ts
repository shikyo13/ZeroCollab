// The migration keeps the proven feature modules alive. Move their actual DOM,
// never clone it: listeners, streaming state, selections and drafts must survive.
export const legacy = {
  chat: document.getElementById('app')!,
  header: document.querySelector<HTMLElement>('#app > header')!,
  runtime: document.getElementById('runtime-panel')!,
  controls: document.querySelector<HTMLElement>('.runtime-controls')!,
  approvals: document.getElementById('room-approvals')!,
};

export function prepareLegacy() {
  const settings = document.getElementById('settings-toggle');
  if (settings) {
    settings.setAttribute('aria-label', 'Settings');
    const text = document.createElement('span');
    text.className = 'workspace-tool-label';
    text.textContent = 'Settings';
    settings.append(text);
  }
  document.getElementById('room-resume')!.textContent = 'Resume all';
  document.getElementById('room-mode')!.setAttribute('role', 'status');
  legacy.approvals.setAttribute('aria-label', 'Agent requests');
  const toolDetails = document.getElementById('room-tools')?.closest('details');
  if (toolDetails) toolDetails.open = true;
}
