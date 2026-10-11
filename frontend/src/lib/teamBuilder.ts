/* Team builder API (/api/runtime/team-builder), contract agreed with the
 * backend owner in follow-up 65ef16f7. Proposals are drafted and challenged by
 * connected agents, saved server-side, and only "Add approved members" changes
 * the roster. */
import { runtime } from './api';
import type { Profile, ProviderInfo } from '../team/ProfileEditor';

/** agent: one member from the user's short description, with an expert background (Zero #12932). */
export type BriefSource = 'spec' | 'goal' | 'text' | 'agent';
export type ProposalStatus = 'drafting' | 'challenging' | 'ready' | 'failed';
export type MemberStatus = 'proposed' | 'approved' | 'revision_requested' | 'declined' | 'added';
export type MemberAction = 'approve' | 'edit' | 'request_revision' | 'decline';

export interface ProposedMember {
  member_id: string;
  name: string;
  /** Proposed @handle. */
  id: string;
  background: string;
  /** Provider template id, as in profiles. */
  template: string;
  model: string;
  effort: string;
  rationale: string;
  status: MemberStatus;
  handle_available: boolean;
  error?: string;
  /** On a suggestion: the agent who suggested this member, and its reason. */
  suggested_by?: string;
  reason?: string;
}

export interface CoverageRow {
  need: string;
  detail?: string;
  lead: string[];
  support: string[];
}

export interface Proposal {
  id: string;
  status: ProposalStatus;
  source?: BriefSource | 'suggestion';
  suggested_by?: string;
  project_id?: string;
  read_only?: boolean;
  brief: string;
  members: ProposedMember[];
  coverage: CoverageRow[];
  summary: string;
  drafter: string;
  challenger: string;
  /** What each agent actually decided, in order. */
  history?: { actor: string; stage: string; summary: string }[];
  error?: string;
}

export interface BuilderAgent {
  id: string;
  name?: string;
  provider?: string;
  label?: string;
  state?: string;
  available?: boolean;
}

export interface BuilderContext {
  profiles: Profile[];
  providers: ProviderInfo[];
  /** Available native agents that can draft or challenge (two are required). */
  agents: BuilderAgent[];
  /** Effective limit on agents running together; 0 or null means no limit. */
  capacity?: number | null;
  spec: string;
  goal: string;
  proposal: Proposal | null;
}

export interface MemberChanges {
  name?: string;
  id?: string;
  background?: string;
  template?: string;
  model?: string;
  effort?: string;
}

const BASE = '/team-builder';

export interface SuggestionTarget { project: string; proposal: string }

export function suggestionTarget(): SuggestionTarget | null {
  const params = new URL(location.href).searchParams;
  const project = params.get('team_project'), proposal = params.get('team_proposal');
  return project && proposal ? { project, proposal } : null;
}

/** View a saved suggestion without switching the running project or sending a turn. */
export function leaveSuggestion(project?: string): void {
  const url = new URL(location.href);
  url.searchParams.delete('team_project');
  url.searchParams.delete('team_proposal');
  if (project) { url.searchParams.set('view', 'projects'); url.searchParams.set('project', project); }
  history.pushState({}, '', url);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export const loadContext = () => runtime<BuilderContext>(BASE);

export const createProposal = (body: { source: BriefSource; brief: string; drafter?: string; challenger?: string; channel?: string }) =>
  runtime<Proposal>(`${BASE}/proposals`, body);

export const loadProposal = (id: string, project?: string) => runtime<Proposal>(project
  ? `${BASE}/projects/${encodeURIComponent(project)}/proposals/${encodeURIComponent(id)}`
  : `${BASE}/proposals/${encodeURIComponent(id)}`);

export const actOnMember = (proposal: string, member: string, body: { action: MemberAction; changes?: MemberChanges; feedback?: string; project_id?: string }) =>
  runtime<Proposal>(`${BASE}/proposals/${encodeURIComponent(proposal)}/members/${encodeURIComponent(member)}`, body);

export const addApproved = (proposal: string, project?: string) => runtime<Proposal>(`${BASE}/proposals/${encodeURIComponent(proposal)}/add`, project ? { project_id: project } : {});

/** Re-run drafting for a failed proposal with the same brief and agents. */
export const retryProposal = (proposal: string) => runtime<Proposal>(`${BASE}/proposals/${encodeURIComponent(proposal)}/retry`, {});

/** Connected agents as {handle, label}. */
export function agentList(agents: BuilderContext['agents']): { handle: string; label: string; available: boolean }[] {
  return (agents ?? [])
    .filter(a => a.id)
    .map(a => ({ handle: a.id, label: a.label || a.name || a.id, available: a.available ?? !['blocked', 'login_required'].includes(a.state ?? '') }));
}

export function capacityText(capacity: BuilderContext['capacity']): string {
  return capacity ? `This room runs up to ${capacity} agents together` : 'This room has no limit on agents running together';
}

export type CoverageState = 'led' | 'gap_if_declined' | 'shared' | 'gap';

/** How well one need is covered, given which members are still in play.
 * A lead counts when it is a current member or an approved/added addition;
 * a lead that is only a pending proposal means the need is at risk. */
export function coverageState(row: CoverageRow, current: Set<string>, members: ProposedMember[]): CoverageState {
  const byHandle = new Map(members.map(m => [m.id, m]));
  const live = (handle: string) => current.has(handle) || ['approved', 'added'].includes(byHandle.get(handle)?.status ?? '');
  const pending = (handle: string) => ['proposed', 'revision_requested'].includes(byHandle.get(handle)?.status ?? '');
  if (row.lead.some(live)) return 'led';
  if (row.lead.some(pending)) return 'gap_if_declined';
  if (row.support.some(h => live(h) || pending(h))) return 'shared';
  return 'gap';
}
