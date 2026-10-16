/* Board view preferences remembered in localStorage: section tab, swimlane
 * grouping, card order and whether the activity feed is open. */
import { useCallback, useState } from 'react';
import { STORAGE_KEYS } from '../lib/constants';
import { readStored, writeStored } from '../lib/storage';

// The first entry of each list is the default when nothing valid is saved.
export const TABS = ['tasks', 'coordination', 'decisions', 'spec', 'memory', 'controls'] as const;
export const GROUPINGS = ['activity', 'status', 'area', 'owner'] as const;
export const SORTS = ['recent', 'oldest', 'title'] as const;
export type Tab = typeof TABS[number];
export type GroupBy = typeof GROUPINGS[number];
export type SortBy = typeof SORTS[number];

export const GROUP_LABELS: Record<GroupBy, string> = { activity: 'Group: Activity', status: 'Group: status only', area: 'Group: area', owner: 'Group: owner' };
export const SORT_LABELS: Record<SortBy, string> = { recent: 'Sort: recent activity', oldest: 'Sort: least recent', title: 'Sort: title' };

function readChoice<T extends string>(key: string, allowed: readonly T[]): T {
  const saved = readStored(key) as T | null;
  return saved && allowed.includes(saved) ? saved : allowed[0];
}

/** A choice restored from storage; set(next, false) changes it for this visit only. */
function useStoredChoice<T extends string>(key: string, allowed: readonly T[]) {
  const [value, setValue] = useState<T>(() => readChoice(key, allowed));
  const set = useCallback((next: T, remember = true) => {
    setValue(next);
    if (remember) writeStored(key, next);
  }, [key]);
  return [value, set] as const;
}

export function useBoardPrefs() {
  const [tab, setTab] = useStoredChoice(STORAGE_KEYS.boardTab, TABS);
  const [groupBy, setGroupBy] = useStoredChoice(STORAGE_KEYS.boardGroup, GROUPINGS);
  const [sortBy, setSortBy] = useStoredChoice(STORAGE_KEYS.boardSort, SORTS);
  // Give tasks the Board's width by default; retain an explicit choice to open the feed.
  const [feedOpen, setFeedOpenState] = useState(() => {
    const saved = readStored(STORAGE_KEYS.boardFeed);
    return saved === '1';
  });
  const setFeedOpen = useCallback((next: boolean) => {
    setFeedOpenState(next);
    writeStored(STORAGE_KEYS.boardFeed, next ? '1' : '0');
  }, []);
  return { tab, setTab, groupBy, setGroupBy, sortBy, setSortBy, feedOpen, setFeedOpen };
}
