import type { Responders, RuntimeState } from './types';

/** Keep the human's selection visible when the server temporarily uses a fallback. */
export function leaderId(responders: Responders | undefined): string | null {
  return responders?.configured.lead || responders?.lead || null;
}
export function leaderName(state: RuntimeState | null | undefined, id: string | null): string {
  if (!id) return 'None available';
  return state?.agents[id]?.label || id;
}
declare global { interface Window { applyConversationLeader?: (responders?: Responders) => void } }
export function syncChatLeader(responders: Responders | undefined) {
  window.applyConversationLeader?.(responders);
}
