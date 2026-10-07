// The migration keeps the proven feature modules alive. Move their actual DOM,
// never clone it: listeners, streaming state, selections and drafts must survive.
// A missing node means index.html and the shell disagree; failing with its name lets
// index.html's import() fallback keep the plain chat page and log a useful error.
function required(selector: string): HTMLElement {
  const node = document.querySelector<HTMLElement>(selector);
  if (!node) throw new Error(`Workspace shell expected ${selector} in index.html`);
  return node;
}

export const legacy = {
  chat: required('#app'),
  header: required('#app > header'),
  runtime: required('#runtime-panel'),
  controls: required('.runtime-controls'),
  approvals: required('#room-approvals'),
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
  required('#room-resume').textContent = 'Resume all';
  required('#room-mode').setAttribute('role', 'status');
  legacy.approvals.setAttribute('aria-label', 'Agent requests');
  const toolDetails = document.getElementById('room-tools')?.closest('details');
  if (toolDetails) toolDetails.open = true;
}
