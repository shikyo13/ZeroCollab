/* Bridge between React views and the legacy room client (static/core.js,
 * static/store.js, static/chat.js). chat.js owns the single /ws connection and
 * re-emits every frame on window.Hub (chat.js: Hub.emit(event.type, event)),
 * so React subscribes there instead of opening a second socket that would
 * replay the whole history. */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { errorMessage, runtime } from './api';
import type { ProjectState, RoomEvent, RuntimeState } from './types';
import { USER } from './constants';

type Listener = (event: RoomEvent) => void;

interface LegacyHub {
  on(type: string, fn: Listener): void;
  off(type: string, fn: Listener): void;
  emit(type: string, data: unknown): void;
}

interface LegacyStore {
  get(key: string): unknown;
  set(key: string, value: unknown): void;
  watch(key: string, fn: (value: unknown, old: unknown) => void): void;
  unwatch(key: string, fn: (value: unknown, old: unknown) => void): void;
}

declare global {
  interface Window {
    __SESSION_TOKEN__?: string;
    Hub?: LegacyHub;
    Store?: LegacyStore;
    activeChannel?: string;
    activeProjectId?: string;
    chatSessionTitle?: (id: string) => string;
    chatSessionReadonly?: (id: string) => boolean;
    ws?: WebSocket | null;
    username?: string;
    /** channels.js: keep chats in the sidebar list while the workspace shell owns the page. */
    __chatsInShellSidebar?: boolean;
    setChannelSidebarMode?: (mode: 'top' | 'sidebar', persist?: boolean) => void;
    showChannelCreateDialog?: () => void;
    switchChannel?: (id: string) => void;
    reopenChatSession?: (id: string) => void;
    viewChatHistory?: (id: string) => void;
    toggleSettings?: () => void;
  }
}

/** Run once the legacy globals exist (classic scripts may still be loading). */
function whenLegacyReady(run: () => void): () => void {
  if (window.Hub) {
    run();
    return () => undefined;
  }
  const onLoad = () => run();
  window.addEventListener('load', onLoad, { once: true });
  return () => window.removeEventListener('load', onLoad);
}

/** Subscribe to a WebSocket event type relayed by the legacy Hub. */
export function useRoomEvent<T = unknown>(type: string, handler: (event: RoomEvent<T>) => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const listener: Listener = event => ref.current(event as RoomEvent<T>);
    let attached = false;
    const cancel = whenLegacyReady(() => {
      if (!window.Hub) return;
      window.Hub.on(type, listener);
      attached = true;
    });
    return () => {
      cancel();
      if (attached) window.Hub?.off(type, listener);
    };
  }, [type]);
}

function subscribeChannel(notify: () => void): () => void {
  const watcher = () => notify();
  let watching = false;
  const cancel = whenLegacyReady(() => {
    if (!window.Store) return;
    window.Store.watch('activeChannel', watcher);
    watching = true;
    notify();
  });
  return () => {
    cancel();
    if (watching) window.Store?.unwatch('activeChannel', watcher);
  };
}

function readChannel(): string {
  return window.activeChannel || 'general';
}

/** The chat channel currently selected in the legacy chat client. */
export function useActiveChannel(): string {
  return useSyncExternalStore(subscribeChannel, readChannel, () => 'general');
}

/** A value the legacy scripts publish with Store.set (for example 'chatSessions'). */
export function useStoreValue<T>(key: string, fallback: T): T {
  const subscribe = useCallback((notify: () => void) => {
    let watching = false;
    const cancel = whenLegacyReady(() => {
      if (!window.Store) return;
      window.Store.watch(key, notify);
      watching = true;
      notify();
    });
    return () => { cancel(); if (watching) window.Store?.unwatch(key, notify); };
  }, [key]);
  const read = useCallback(() => (window.Store?.get(key) as T | undefined) ?? fallback, [key, fallback]);
  return useSyncExternalStore(subscribe, read, () => fallback);
}

/** Post a chat message as the human user through the legacy socket. */
export function sendChatMessage(text: string, channel = readChannel()): void {
  if (window.chatSessionReadonly?.(channel)) throw new Error("Choose a project chat; legacy history is read-only.");
  const ws = window.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    throw new Error('Reconnect to the room before sending a message.');
  }
  ws.send(JSON.stringify({ type: 'message', sender: window.username || USER, channel, project_id: window.activeProjectId, text }));
}

/** Re-render periodically so relative times ("5m ago") stay current. */
export function useNow(intervalMs = 30000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Coarse age of an epoch-seconds timestamp: "just now", "5m ago", "3h ago", "2d ago". */
export function since(timestamp: number, now = Date.now() / 1000): string {
  const seconds = Math.max(0, Math.floor(now - timestamp));
  if (seconds < MINUTE) return 'just now';
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}m ago`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)}h ago`;
  return `${Math.floor(seconds / DAY)}d ago`;
}

interface Resource<T> {
  data: T | null;
  error: string;
  refresh: () => Promise<void>;
}

/** Live /api/runtime/project-state, refreshed by project_state events, reconnects and channel changes. */
export function useProjectState(): Resource<ProjectState> {
  const [data, setData] = useState<ProjectState | null>(null);
  const [error, setError] = useState('');
  const channel = useActiveChannel();
  const refresh = useCallback(async () => {
    try {
      setData(await runtime<ProjectState>('/project-state'));
      setError('');
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh, channel]);
  // The event payload omits last_human_message; keep the last fetched value.
  useRoomEvent<ProjectState>('project_state', event =>
    setData(prev => ({ ...event.data, last_human_message: prev?.last_human_message ?? '' })),
  );
  // chat.js consumes history_complete without relaying it; 'settings' is the
  // first frame of every (re)connect and does reach the Hub, so resync on it.
  useRoomEvent('settings', () => void refresh());
  return { data, error, refresh };
}

interface Snapshot<T> {
  data: T | null;
  error: string;
}

/** One fetch and one set of Hub listeners shared by every component reading a resource.
 * `replace` events carry the full resource; `refresh` events trigger a re-fetch. */
function sharedResource<T>(load: () => Promise<T>, events: { replace?: string; refresh: string[] }) {
  let snapshot: Snapshot<T> = { data: null, error: '' };
  const listeners = new Set<() => void>();
  let inFlight: Promise<void> | null = null;
  let again = false;
  let pushes = 0;  // bumps on each pushed snapshot so a slower fetch cannot overwrite it
  let detach: (() => void) | null = null;

  const publish = (next: Partial<Snapshot<T>>) => {
    snapshot = { ...snapshot, ...next };
    listeners.forEach(listener => listener());
  };
  const refresh = (): Promise<void> => {
    if (inFlight) {
      again = true;  // something changed mid-fetch; fetch once more afterwards
      return inFlight;
    }
    const startedAt = pushes;
    inFlight = load()
      .then(data => publish(startedAt === pushes ? { data, error: '' } : { error: '' }),
        e => publish({ error: errorMessage(e) }))
      .finally(() => {
        inFlight = null;
        if (again) {
          again = false;
          void refresh();
        }
      });
    return inFlight;
  };
  const attach = () => {
    const onReplace: Listener = event => {
      pushes += 1;
      publish({ data: event.data as T, error: '' });
    };
    const onRefresh: Listener = () => void refresh();
    const cancel = whenLegacyReady(() => {
      if (events.replace) window.Hub?.on(events.replace, onReplace);
      events.refresh.forEach(type => window.Hub?.on(type, onRefresh));
    });
    void refresh();
    return () => {
      cancel();
      if (events.replace) window.Hub?.off(events.replace, onReplace);
      events.refresh.forEach(type => window.Hub?.off(type, onRefresh));
    };
  };
  return {
    refresh,
    read: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      if (listeners.size === 1) detach = attach();
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) {
          detach?.();
          detach = null;
        }
      };
    },
  };
}

const runtimeResource = sharedResource<RuntimeState>(() => runtime<RuntimeState>(''), {
  replace: 'runtime_state',
  refresh: ['runtime_usage', 'settings'],
});

/** Live /api/runtime snapshot (agents, approvals, runs, limits), shared by all callers. */
export function useRuntimeState(): Resource<RuntimeState> {
  const { data, error } = useSyncExternalStore(runtimeResource.subscribe, runtimeResource.read);
  return { data, error, refresh: runtimeResource.refresh };
}

/** Collapse a burst of calls into one trailing call per window. */
export function useCoalesced(fn: () => void, windowMs: number): () => void {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const timer = useRef<number | null>(null);
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);
  return useCallback(() => {
    if (timer.current !== null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      fnRef.current();
    }, windowMs);
  }, [windowMs]);
}

export function sessionTitle(id: string): string { return window.chatSessionTitle?.(id) || id; }
