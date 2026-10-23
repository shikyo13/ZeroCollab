import { runtime } from './api';
import type { RoomEvent } from './types';
import { createProjectStateStore } from './projectStateStore';

const store = createProjectStateStore(() => runtime('/project-state'));
let attached = false;
let currentProject: string | undefined;
function attach() {
  if (attached || !window.Hub) return;
  attached = true;
  window.Hub.on('project_state', e => store.full(e.data));
  window.Hub.on('project_delta', e => store.delta(e.data));
  window.Hub.on('settings', () => store.invalidate());
  window.Hub.on('runtime_state', (e: RoomEvent) => {
    const id = (e.data as { project_id?: string })?.project_id;
    if (id !== undefined && id !== currentProject) {
      currentProject = id;
      store.invalidate(id);
    }
  });
  window.Store?.watch('activeChannel', () => { void store.refresh(); });
  void store.refresh();
}
/** The shell owns one Hub subscription for its lifetime, just like followStore. */
export const projectState = {
  read: store.read,
  refresh: store.refresh,
  subscribe(fn: () => void) {
    const unsubscribe = store.subscribe(fn);
    attach();
    return unsubscribe;
  },
};
if (typeof window !== 'undefined') {
  if (window.Hub) attach();
  else window.addEventListener('load', attach, { once: true });
}
