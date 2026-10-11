/* Contract types for the existing backend (room_runtime.py, project_room.py,
 * coordination_records.py). These mirror server payloads; they are not a new API. */

export type TaskStatus = 'open' | 'working' | 'blocked' | 'review' | 'done';
export const TASK_STATUSES: TaskStatus[] = ['open', 'working', 'blocked', 'review', 'done'];

export interface TaskUpdate {
  by: string;
  text: string;
  time: number;
  /** Ownership/status change recorded with this entry, e.g. "claimed; moved to working". */
  event?: string;
  /** Lifecycle-only entry with no check-in text; history, not progress. */
  auto?: boolean;
}

export interface Task {
  id: string;
  title: string;
  details: string;
  paths: string[];
  owner: string;
  status: TaskStatus;
  updates: TaskUpdate[];
  updated: number;
  depends_on?: string[];
  /** Area tags such as frontend, backend or docs. Older tasks have none. */
  labels?: string[];
  attachments?: TaskAttachment[];
  /** Images moved off the card when newer ones were added (project_room.arrange_attachments), newest first. */
  earlier_attachments?: EarlierAttachment[];
  /** project_state only: total earlier images when it carries just the newest ones. */
  earlier_total?: number;
  /** Observed by the server (coordination_state.execution_state); separate from the chosen status. */
  execution_state?: ExecutionState;
  /** No check-in for a while on unattended work; never set while waiting on the user. */
  stale?: boolean;
  next_action?: string;
  /** 1 (highest) to 5. */
  priority?: number;
  /** Links an open request to the user on this task while the owner waits for them. */
  wait?: { kind: 'followup' | 'review'; record_id: string };
  /** Proposed ownership change; the current owner keeps the task until the recipient accepts. */
  transfer?: { from: string; to: string; requested_at: number };
  evidence?: Partial<Record<EvidenceKind, string>>;
}

export type ExecutionState =
  | 'executing'
  | 'queued'
  | 'waiting_on_human'
  | 'needs_attention'
  | 'blocked'
  | 'waiting_on_dependency'
  | 'in_review'
  | 'idle'
  | 'unassigned'
  | 'done';

export type EvidenceKind = 'implementation' | 'review' | 'installation' | 'acceptance';
export const EVIDENCE_KINDS: [EvidenceKind, string][] = [
  ['implementation', 'Implementation'],
  ['review', 'Review'],
  ['installation', 'Installation'],
  ['acceptance', 'Owner acceptance'],
];

/** An uploaded image on a task. Annotated copies keep the original as `source`. */
export interface TaskAttachment {
  url: string;
  name: string;
  note?: string;
  source?: string;
}

/** superseded: a newer annotation of the same original replaced it on the card; overflow: newer images took its place. */
export interface EarlierAttachment extends TaskAttachment {
  moved?: number;
  reason?: 'superseded' | 'overflow';
  superseded_by?: string;
}

export type ReviewStatus = 'pending' | 'approved' | 'changes_requested' | 'superseded' | 'cancelled';

export interface Review {
  id: string;
  task_id: string;
  requested_by: string;
  owner: string;
  reviewer: string;
  revision: string;
  request: string;
  status: ReviewStatus;
  findings: string;
  cancellation?: string;
  channel?: string;
  created: number;
  updated: number;
  /** Files the server hashed when the review was requested, in the owner's folder at `root`. */
  snapshot?: { root?: string; files: { path: string; sha256: string; bytes: number }[]; unavailable?: string[] };
  /** Snapshot files that differ now; approval is refused while any did. */
  changed_files?: string[];
  fingerprinted?: boolean;
}

export type FollowupStatus = 'open' | 'resolved' | 'cancelled';

export interface Followup {
  id: string;
  task_id: string;
  created_by: string;
  assigned_to: string;
  text: string;
  status: FollowupStatus;
  result: string;
  channel?: string;
  created: number;
  updated: number;
}

export interface Note {
  id?: string;
  /** Decisions: active, or retired by a later decision. */
  status?: 'active' | 'superseded' | 'resolved';
  replaced_by?: string;
  kind: string;
  by: string;
  text: string;
  time?: number;
}

export interface Limits {
  /** Explicit human opt-in; absent on older servers means off. */
  ready_task_wakes?: 0 | 1;
  input_chars: number;
  agent_turns: number;
  parallel_agents: number;
  turn_seconds: number;
  turn_max_seconds: number;
  native_tool_calls: number;
  local_tool_rounds: number;
  reported_token_budget: number;
}

export interface Chain {
  id: string;
  channel: string;
  turns: number;
  handoffs: number;
  reported_tokens: number;
  input_chars: number;
  paused: boolean;
  /** 'manager_audit' for the manager check-ins' own budget chain (systems f394c1c); absent for chat conversations. */
  kind?: string;
}

/** GET /api/runtime/project-state and the `project_state` WS event. */
export interface ProjectState {
  spec: string;
  tasks: Task[];
  notes: Note[];
  reviews?: Review[];
  followups?: Followup[];
  limits: Limits;
  chains: Chain[];
  stale_seconds: number;
  last_human_message?: string;
}

export interface AgentControls {
  model?: string;
  effort?: string;
  [key: string]: unknown;
}

export interface AgentState {
  provider: string;
  template: string;
  label: string;
  /** Public @handle (defaults to the id the state is keyed by). */
  handle?: string;
  color: string;
  archived: boolean;
  state: string;
  detail: string;
  paused: boolean;
  queued: number;
  worktree?: string;
  branch?: string;
  controls?: AgentControls;
  [key: string]: unknown;
}

export interface Approval {
  id: string;
  run: string;
  agent: string;
  channel: string;
  tool: string;
  params: unknown;
  reason: string;
  worktree: string;
  question: boolean;
}

export interface RunInfo {
  id: string;
  agent: string;
  channel: string;
  text: string;
  settings: unknown;
  reported_settings: unknown;
  reported_tokens: number;
  input_chars: number;
}

/** Saved selection and server-selected available responders; ids, never display labels. */
export interface Responders {
  lead: string | null;
  backup: string | null;
  configured: { lead: string; backup: string };
}

/** GET /api/runtime and the `runtime_state` WS event. */
export interface RuntimeState {
  agents: Record<string, AgentState>;
  responders?: Responders;
  project: string;
  project_id?: string;
  paused: boolean;
  automatic_tool_approvals: boolean;
  limits: Limits & { parallel_agents_effective: number; queue_per_agent: number };
  budgets: Chain[];
  approvals: Approval[];
  runs: RunInfo[];
  /** A repository change in progress for the attached project, or null. */
  repository_transition?: RepositoryTransition | null;
}

/** draining: running turns finish (or are interrupted) while new turns are held;
 * preparing: agent worktrees are being made in the new repository. */
export interface RepositoryTransition {
  path: string;
  phase: 'draining' | 'preparing';
  /** Absent from servers without multi-repository support, which only replace. */
  action?: 'replace' | 'add' | 'primary' | 'remove';
  waiting_on: string[];
  queued: number;
  cancel_requested: boolean;
}

/** Every WS frame is {type, ...}; runtime frames carry their payload in `data`. */
export interface RoomEvent<T = unknown> {
  type: string;
  data: T;
  [key: string]: unknown;
}
