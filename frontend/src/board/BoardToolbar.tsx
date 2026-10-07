/* Task list toolbar: text, owner and area filters, grouping, ordering, the feed toggle and New task. */
import type { Agents } from './shared';
import type { TaskFilters } from './lanes';
import { GROUPINGS, GROUP_LABELS, SORTS, SORT_LABELS, type GroupBy, type SortBy } from './useBoardPrefs';

export function BoardToolbar({
  filters,
  onFilters,
  owners,
  labels,
  agents,
  groupBy,
  onGroupBy,
  sortBy,
  onSortBy,
  feedShown,
  onToggleFeed,
  creating,
  onToggleCreate,
}: {
  filters: TaskFilters;
  onFilters: (next: TaskFilters) => void;
  owners: string[];
  labels: string[];
  agents: Agents;
  groupBy: GroupBy;
  onGroupBy: (next: GroupBy) => void;
  sortBy: SortBy;
  onSortBy: (next: SortBy) => void;
  feedShown: boolean;
  onToggleFeed: () => void;
  creating: boolean;
  onToggleCreate: () => void;
}) {
  const set = (patch: Partial<TaskFilters>) => onFilters({ ...filters, ...patch });
  return (
    <div className="bd-toolbar">
      <input
        type="search"
        className="bd-search"
        placeholder="Filter by title, path, owner or ID"
        aria-label="Filter tasks"
        value={filters.query}
        onChange={e => set({ query: e.target.value })}
      />
      <select aria-label="Filter by owner" value={filters.owner} onChange={e => set({ owner: e.target.value })}>
        <option value="">Everyone</option>
        <option value="-">Unclaimed</option>
        {owners.map(o => (
          <option key={o} value={o}>
            {agents[o]?.label || o}
          </option>
        ))}
      </select>
      {labels.length > 0 && (
        <select aria-label="Filter by area" value={filters.label} onChange={e => set({ label: e.target.value })}>
          <option value="">All areas</option>
          <option value="-">No area</option>
          {labels.map(label => (
            <option key={label} value={label}>
              {label}
            </option>
          ))}
        </select>
      )}
      <select aria-label="Group by" value={groupBy} onChange={e => onGroupBy(e.target.value as GroupBy)}>
        {GROUPINGS.map(value => (
          <option key={value} value={value}>
            {GROUP_LABELS[value]}
          </option>
        ))}
      </select>
      <select aria-label="Sort cards" value={sortBy} onChange={e => onSortBy(e.target.value as SortBy)}>
        {SORTS.map(value => (
          <option key={value} value={value}>
            {SORT_LABELS[value]}
          </option>
        ))}
      </select>
      <button type="button" className="bd-btn" aria-pressed={feedShown} onClick={onToggleFeed}>
        Team activity
      </button>
      <button type="button" className="bd-btn bd-btn-primary" onClick={onToggleCreate} aria-expanded={creating}>
        {creating ? 'Close' : 'New task'}
      </button>
    </div>
  );
}
