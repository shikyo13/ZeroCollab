/* Header bell: everything waiting on the user, with a count and a link to the exact place to act.
 * Sources the workspace already loads: repository requests (/projects), tool approvals
 * (runtime state), reviews and follow-ups addressed to the user (/project-state) and a
 * prepared release that passed rehearsal (/deployments), and a team suggestion or proposal waiting for the
 * user's review (/team-builder, the open project only). Items disappear when resolved; current-project decisions notify while waiting for the user's choice. */
import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { Decision, Summary } from '../decisions/model';
import { EVENTS, USER } from '../lib/constants';
import { useProjectState, useRoomEvent, useRuntimeState } from '../lib/room';
import { loadContext, type Proposal } from '../lib/teamBuilder';
import type { RuntimeState } from '../lib/types';
import type { ProjectSummary } from '../workspace/projectData';
import { openServer } from '../workspace/useWorkspaceRoute';
import type { View } from '../workspace/useWorkspaceRoute';
import type { DeploymentFeed } from './Deployments';
import { Icon } from './Icon';
import { readStored, writeStored } from '../lib/storage';
import './attention.css';

type WaitingDecision = Pick<Decision, 'id'|'question'|'stage'|'revision'|'epoch'|'current_proposal'|'updated'|'choice'>;
type DecisionSummary = Summary & {choice?: string};

type Item = { key: string; revision: string; title: string; detail: string; action: string; open: () => void };

const name = (state: RuntimeState | null, id: string) => id === USER ? 'You' : state?.agents[id]?.label || id;
const clip = (text: string, max = 140) => (text.length > max ? text.slice(0, max - 1) + '…' : text);

const READ_KEY = 'zerocollab-notifications-read-v1';
const MUTE_KEY = 'zerocollab-mute-unfocused-chat';
function savedRead(): Record<string, string> {
  try {
    const value = JSON.parse(readStored(READ_KEY) || '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string'));
  } catch { return {}; }
}

const REVEAL_TRIES = 40;     // the target view may still be loading its data
const REVEAL_EVERY_MS = 100;

/** Scroll a target into view once the newly shown view has rendered it (it may load asynchronously). */
function reveal(selector: string, tries = REVEAL_TRIES) {
  window.setTimeout(() => {
    const node = document.querySelector<HTMLElement>(selector);
    if (!node || !node.offsetParent) {
      if (tries > 1) reveal(selector, tries - 1);
      return;
    }
    node.scrollIntoView({ block: 'center', behavior: 'smooth' });
    node.classList.add('ws-attention-flash');
    window.setTimeout(() => node.classList.remove('ws-attention-flash'), 2000);
  }, REVEAL_EVERY_MS);
}

export function AttentionBell({ projects, deployments, navigate, openProject }: {
  projects: ProjectSummary[]; deployments: DeploymentFeed; navigate: (view: View) => void; openProject: (id: string) => void;
}) {
  const { data: state } = useRuntimeState();
  const { data: board } = useProjectState();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  // The open project's team proposal or suggestion, re-read whenever the team builder changes it.
  const [team, setTeam] = useState<Proposal | null>(null);
  const loadTeam = () => { loadContext().then(c => setTeam(c.proposal)).catch(() => undefined); };
  useEffect(loadTeam, []);
  useRoomEvent('team_builder', loadTeam);
  useRoomEvent('settings', loadTeam);
  // Listing summaries omit the proposal fence: fetch details only for decisions awaiting a choice.
  const [decisionFeed, setDecisionFeed] = useState<{project: string; items: WaitingDecision[]}>({project: '', items: []});
  const decisionProject = useRef(''), decisionLoad = useRef(0), decisionEvents = useRef(0);
  const decisionVersions = useRef(new Map<string, number>());
  const loadDecisions = async (project = decisionProject.current) => {
    if (!project) return;
    const generation = ++decisionLoad.current, began = decisionEvents.current;
    try {
      const listing = await api<{decisions: DecisionSummary[]}>('/api/decisions');
      const details = await Promise.all(listing.decisions.filter(d => d.stage === 'review' && !d.choice)
        .map(d => api<WaitingDecision>('/api/decisions/' + encodeURIComponent(d.id))));
      if (generation !== decisionLoad.current || project !== decisionProject.current) return;
      setDecisionFeed(previous => {
        const items = new Map(details.map(d => [d.id, d]));
        if (previous.project === project) for (const known of previous.items) {
          if ((decisionVersions.current.get(known.id) || 0) > began || known.updated > (items.get(known.id)?.updated ?? Infinity)) items.set(known.id, known);
        }
        return {project, items: [...items.values()]};
      });
    } catch { /* The next decision event or reconnect resynchronizes this optional notification source. */ }
  };
  const selectDecisionProject = (project: string) => {
    decisionProject.current = project; ++decisionLoad.current; decisionVersions.current.clear();
    setDecisionFeed({project, items: []}); void loadDecisions(project);
  };
  useEffect(() => {
    selectDecisionProject(state?.project_id || '');
    return () => { ++decisionLoad.current; };
  }, [state?.project_id]);
  useRoomEvent<{project_id?: string}>('settings', event => {
    selectDecisionProject(event.data.project_id || '');
  });
  useRoomEvent<Decision>('decision', event => {
    const project = decisionProject.current, d = event.data;
    if (!project || !d.id) return;
    decisionVersions.current.set(d.id, ++decisionEvents.current);
    setDecisionFeed(previous => {
      const items = previous.project === project ? previous.items : [];
      const known = items.find(p => p.id === d.id);
      return known && known.updated > d.updated ? previous : {project, items: [d, ...items.filter(p => p.id !== d.id)]};
    });
  });
  const [read, setRead] = useState(savedRead);
  const [muted, setMuted] = useState(() => readStored(MUTE_KEY) === '1');
  useEffect(() => {
    const sync = () => { setRead(savedRead()); setMuted(readStored(MUTE_KEY) === '1'); };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, []);
  const markRead = (item: Item) => {
    setRead(previous => {
      // Optional local preferences are bounded; request state stays on the server.
      const next = Object.fromEntries(Object.entries({ ...previous, [item.key]: item.revision }).slice(-200));
      writeStored(READ_KEY, JSON.stringify(next));
      return next;
    });
    window.requestAnimationFrame(() => {
      const menu = root.current?.querySelector('.ws-bell-menu');
      (menu?.querySelector<HTMLElement>('li button') || menu?.querySelector<HTMLElement>('h2'))?.focus();
    });
  };
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); root.current?.querySelector('button')?.focus(); } };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);

  const openBoard = (detail: { task?: string; tab?: 'coordination' }) => {
    navigate('board');
    window.dispatchEvent(new CustomEvent(EVENTS.openTask, { detail }));
  };
  const items: Item[] = [];
  for (const project of projects) {
    const request = project.repo_request;
    if (!request) continue;
    items.push({
      key: `repo:${project.id}`,
      revision: JSON.stringify([request.by, request.path, request.create, request.reason, request.time, request.action, request.replace, request.current]),
      // The runtime state names only the active project's team; another project's requester is in its own.
      title: `${project.team.find(m => m.id === request.by)?.name ?? name(state, request.by)} requested ${request.replace ? 'a repository change' : 'a repository'}`,
      detail: `${project.name}: ${request.path}`,
      action: 'Review in Projects',
      open: () => { openProject(project.id); reveal('#pj-repository'); },
    });
  }
  for (const approval of state?.approvals ?? []) {
    items.push({
      key: `approval:${approval.id}`,
      revision: JSON.stringify([approval.agent, approval.tool, approval.question, approval.reason]),
      title: `${name(state, approval.agent)} ${approval.question ? 'has a question' : `asks to run ${approval.tool}`}`,
      detail: clip(approval.reason || ''),
      action: 'Answer',
      open: () => reveal('.workspace-approvals'),
    });
  }
  for (const review of board?.reviews ?? []) {
    if (review.reviewer !== USER || review.status !== 'pending') continue;
    const task = board?.tasks.find(t => t.id === review.task_id);
    items.push({
      key: `review:${window.activeProjectId}:${review.id}`,
      revision: JSON.stringify([review.requested_by, review.revision, review.request]),
      title: `${name(state, review.requested_by)} asked you to review`,
      detail: clip(task ? `${task.title}: ${review.request}` : review.request),
      action: 'Open review',
      open: () => openBoard(task ? { task: task.id } : { tab: 'coordination' }),
    });
  }
  for (const followup of board?.followups ?? []) {
    if (followup.assigned_to !== USER || followup.status !== 'open') continue;
    const task = board?.tasks.find(t => t.id === followup.task_id);
    items.push({
      key: `followup:${window.activeProjectId}:${followup.id}`,
      revision: JSON.stringify([followup.created_by, followup.text]),
      title: followup.created_by === USER ? 'Your follow-up note is open' : `${name(state, followup.created_by)} needs your answer`,
      detail: clip(followup.text),
      action: 'Open follow-up',
      open: () => openBoard(task ? { task: task.id } : { tab: 'coordination' }),
    });
  }
  for (const decision of decisionFeed.items) {
    if (decisionFeed.project !== decisionProject.current || decision.stage !== 'review' || decision.choice) continue;
    items.push({
      key: `decision:${decisionFeed.project}:${decision.id}`,
      revision: JSON.stringify([decision.id, decision.revision, decision.epoch, decision.current_proposal]),
      title: 'Decision waiting for your choice',
      detail: clip(decision.question),
      action: 'Decide',
      open: () => {
        navigate('chat');
        window.dispatchEvent(new CustomEvent('workspace-side-panel', {detail: {
          tab: 'decision', decision_id: decision.id, opener: root.current?.querySelector('button'),
        }}));
      },
    });
  }
  const pending = team?.status === 'ready' ? team.members.filter(m => m.status === 'proposed' || m.status === 'approved') : [];
  if (team && pending.length) {
    const suggestion = team.source === 'suggestion';
    items.push({
      key: `team:${team.id}`,
      revision: JSON.stringify([team.source, team.brief, pending]),
      title: suggestion ? (pending.length === 1
          ? `${name(state, pending[0].suggested_by || team.suggested_by || team.drafter)} suggested a new team member`
          : `${pending.length} suggested team members are waiting`)
        : pending.length === 1 ? 'A proposed team member is ready to review' : `${pending.length} proposed team members are ready to review`,
      detail: clip(pending.map(m => `${m.name} (@${m.id})`).join(', ')),
      action: 'Review on Team',
      open: () => {
        // The same exact-proposal view a chat suggestion link opens (Team reads these on popstate).
        const url = new URL(location.href);
        url.searchParams.set('view', 'team');
        url.searchParams.set('team_project', window.activeProjectId || '');
        url.searchParams.set('team_proposal', team.id);
        history.pushState({}, '', url);
        window.dispatchEvent(new PopStateEvent('popstate'));
      },
    });
  }
  const feed = deployments.data;
  const release = feed && !feed.pending.length ? feed.releases.find(r => r.relation === 'newer' && r.rehearsal_passed) : undefined;
  if (release) {
    items.push({
      key: `release:${release.id}`,
      revision: JSON.stringify([release.commit, release.manifest_sha, release.title, release.subject, release.notes]),
      title: `Release ${release.version || release.id} is ready to deploy`,
      detail: clip(release.title || release.subject || 'Reviewed and rehearsed; approve it when you are ready.'),
      action: 'Review deployment',
      open: () => { openServer('updates'); reveal('.server-page .deploy-panel'); },
    });
  }

  const unread = items.filter(item => read[item.key] !== item.revision);
  const count = unread.length;
  return <div className="ws-bell" ref={root}>
    <button className={`ws-icon-button ws-bell-button${count ? ' has-items' : ''}`} aria-haspopup="dialog" aria-expanded={open}
      aria-label={count ? `${count} ${count === 1 ? 'unread notification' : 'unread notifications'}` : 'No unread notifications'}
      title={count ? `${count} unread notifications` : 'No unread notifications'} onClick={() => setOpen(!open)}>
      <Icon name="bell" size={18} />
      {count > 0 && <span className="ws-bell-count" aria-hidden="true">{count > 9 ? '9+' : count}</span>}
    </button>
    {open && <div className="ws-popover ws-bell-menu" role="dialog" aria-label="Notifications">
      <h2 tabIndex={-1}>Notifications · {count} unread</h2>
      {unread.length ? <ul>{unread.map(item => <li key={item.key}>
        <button onClick={() => { setOpen(false); item.open(); }}>
          <strong>{item.title}</strong>
          {item.detail && <small>{item.detail}</small>}
          <span className="ws-bell-action">{item.action} →</span>
        </button>
        <button className="ws-notification-read" onClick={() => markRead(item)}
          aria-label={`${read[item.key] === item.revision ? 'Read' : 'Mark as read'}: ${item.title}`}
          aria-pressed={read[item.key] === item.revision}>
          {read[item.key] === item.revision ? 'Read' : 'Mark as read'}
        </button>
      </li>)}</ul> : <p>All caught up.</p>}
      <label className="ws-notification-mute">
        <input type="checkbox" checked={muted} onChange={event => {
          const next = event.target.checked; setMuted(next); writeStored(MUTE_KEY, next ? '1' : '0');
        }} />
        <span>Mute chat sounds when unfocused</span>
      </label>
      <p className="ws-notification-help">Visual notifications stay on. Saved in this browser.</p>
    </div>}
  </div>;
}
