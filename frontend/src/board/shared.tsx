import { useCallback, useState, type ReactNode } from 'react';
import { errorMessage } from '../lib/api';
import { since } from '../lib/room';
import type { AgentState, FollowupStatus, ReviewStatus, TaskStatus } from '../lib/types';
import { USER } from '../lib/constants';

export type Agents = Record<string, AgentState>;

export const STATUS_LABELS: Record<TaskStatus, string> = {
  open: 'Open',
  working: 'Working',
  blocked: 'Blocked',
  review: 'In review',
  done: 'Done',
};

export const REVIEW_LABELS: Record<ReviewStatus, string> = {
  pending: 'Pending',
  approved: 'Approved',
  changes_requested: 'Changes requested',
  superseded: 'Superseded',
  cancelled: 'Cancelled',
};

export const FOLLOWUP_LABELS: Record<FollowupStatus, string> = {
  open: 'Open',
  resolved: 'Resolved',
  cancelled: 'Cancelled',
};

/** Run an async mutation with busy/error state; resolves true on success. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = useCallback(async (work: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setError('');
    try {
      await work();
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  return { run, busy, error, setError };
}

export function displayName(name: string, agents: Agents): string {
  if (!name) return 'Unclaimed';
  if (name === USER) return 'You';
  return agents[name]?.label || name;
}

export function AgentChip({ name, agents, empty = 'Unclaimed' }: { name: string; agents: Agents; empty?: string }) {
  if (!name) return <span className="bd-chip bd-chip-empty">{empty}</span>;
  const color = name === USER ? 'var(--accent)' : agents[name]?.color || 'var(--text-muted)';
  return (
    <span className="bd-chip" title={name === USER ? 'You' : '@' + name}>
      <span className="bd-dot" style={{ background: color }} aria-hidden="true" />
      {displayName(name, agents)}
    </span>
  );
}

export function StatusPill({ status }: { status: TaskStatus }) {
  return <span className={`bd-pill bd-status-${status}`}>{STATUS_LABELS[status]}</span>;
}

export function ReviewPill({ status }: { status: ReviewStatus }) {
  return <span className={`bd-pill bd-review-${status}`}>{REVIEW_LABELS[status]}</span>;
}

export function FollowupPill({ status }: { status: FollowupStatus }) {
  return <span className={`bd-pill bd-followup-${status}`}>{FOLLOWUP_LABELS[status]}</span>;
}

/** Relative age ("5m ago") with the absolute local time as its tooltip. `time` is in epoch seconds;
 * pass `now` from useNow() where the age should keep ticking. */
export function RelativeTime({ time, now, prefix = '', className }: { time: number; now?: number; prefix?: string; className?: string }) {
  return (
    <span className={className} title={new Date(time * 1000).toLocaleString()}>
      {prefix}
      {since(time, now)}
    </span>
  );
}

export function ErrorText({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="bd-error" role="alert">
      {message}
    </p>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="bd-field">
      <span className="bd-field-label">{label}</span>
      {children}
      {hint && <span className="bd-field-hint">{hint}</span>}
    </label>
  );
}

/** Owner/assignee options: active (non-archived) agents, plus any current value so it never silently disappears. */
export function ParticipantOptions({
  agents,
  current = '',
  includeUser = false,
  exclude = [],
  emptyLabel,
}: {
  agents: Agents;
  current?: string;
  includeUser?: boolean;
  exclude?: string[];
  emptyLabel?: string;
}) {
  const names = Object.entries(agents)
    .filter(([name, info]) => !info.archived || name === current)
    .map(([name]) => name)
    .filter(name => !exclude.includes(name));
  if (current && current !== USER && !names.includes(current) && !exclude.includes(current)) names.push(current);
  return (
    <>
      {emptyLabel !== undefined && <option value="">{emptyLabel}</option>}
      {includeUser && !exclude.includes(USER) && <option value={USER}>You (user)</option>}
      {names.map(name => (
        <option key={name} value={name}>
          {displayName(name, agents)} (@{name})
        </option>
      ))}
    </>
  );
}

/** Mirrors project_room.task_labels so the form shows what the server will store. */
export function splitLabels(raw: string): string[] {
  const result: string[] = [];
  for (const value of raw.split(/[,\n]/)) {
    const label = value
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9._]+/g, '-')
      .split('-')
      .filter(Boolean)
      .join('-')
      .slice(0, 24);
    if (label && !result.includes(label)) result.push(label);
  }
  return result.slice(0, 4);
}

export function LabelChips({ labels }: { labels?: string[] }) {
  if (!labels?.length) return null;
  return (
    <ul className="bd-labels" aria-label="Areas">
      {labels.map(label => (
        <li key={label} className="bd-label">
          {label}
        </li>
      ))}
    </ul>
  );
}

/** Field for area labels with the labels already used on this board as quick picks. */
export function LabelField({ value, onChange, known }: { value: string; onChange: (next: string) => void; known: string[] }) {
  const current = splitLabels(value);
  const suggestions = known.filter(label => !current.includes(label)).slice(0, 8);
  return (
    <div className="bd-field">
      <label className="bd-field">
        <span className="bd-field-label">Areas</span>
        <input value={value} onChange={e => onChange(e.target.value)} placeholder="frontend, docs" />
      </label>
      <span className="bd-field-hint">
        Up to 4, comma-separated. Grouping by area uses the first one; reserved paths still decide file ownership.
        {suggestions.length > 0 && ' Used here: '}
        {suggestions.map(label => (
          <button
            key={label}
            type="button"
            className="bd-label bd-label-pick"
            disabled={current.length >= 4}
            onClick={() => onChange([...current, label].join(', '))}
          >
            + {label}
          </button>
        ))}
      </span>
    </div>
  );
}

export function splitPaths(raw: string): string[] {
  return raw
    .split(/[,\n]/)
    .map(p => p.trim())
    .filter(Boolean);
}
