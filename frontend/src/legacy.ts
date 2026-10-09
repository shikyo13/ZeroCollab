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
  // channels.js renders the project's chats (rename, close, unread, closed & older) into this list.
  chatList: required('#channel-sidebar'),
  // settings.js's fields; the workspace shows them in the Settings dialog.
  settings: required('#settings-bar'),
};

/** Put every moved node back where index.html and runtime.js created it
 * (#app > header, then #runtime-panel holding controls and approvals), so the
 * plain chat page keeps working if the React shell is unmounted. */
export function restoreLegacyLayout() {
  const { chat, header, runtime, controls, approvals, chatList, settings } = legacy;
  window.__chatsInShellSidebar = false;
  if (!chat.contains(settings)) {
    settings.classList.add('hidden');
    const channelBar = document.getElementById('channel-bar');
    if (channelBar) channelBar.before(settings);
    else chat.append(settings);
  }
  const contentArea = document.getElementById('content-area');
  if (contentArea && !contentArea.contains(chatList)) contentArea.prepend(chatList);
  if (!runtime.contains(approvals)) runtime.prepend(approvals);
  if (!runtime.contains(controls)) runtime.prepend(controls);
  if (!chat.contains(header)) chat.prepend(header);
  if (!chat.contains(runtime)) header.after(runtime);
  if (chat.isConnected) return;
  // Below the React root, so its error notice stays visible above the chat.
  const root = document.getElementById('workspace-root');
  if (root) root.after(chat);
  else document.body.prepend(chat);
}

export function prepareLegacy() {
  // Chats are listed in the workspace sidebar; the chat's own top tab bar is hidden.
  window.__chatsInShellSidebar = true;
  window.setChannelSidebarMode?.('sidebar', false);
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
