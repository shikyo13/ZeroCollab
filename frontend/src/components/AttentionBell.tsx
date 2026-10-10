/* Header bell: everything waiting on the user, with a count and a link to the exact place to act.
 * Sources the workspace already loads: repository requests (/projects), tool approvals
 * (runtime state), reviews and follow-ups addressed to the user (/project-state) and a
 * prepared release that passed rehearsal (/deployments). Items disappear when resolved. */
import { useEffect, useRef, useState } from 'react';
import { EVENTS, USER } from '../lib/constants';
import { useProjectState, useRuntimeState } from '../lib/room';
import type { RuntimeState } from '../lib/types';
import type { ProjectSummary } from '../workspace/projectData';
import { openServer } from '../workspace/useWorkspaceRoute';
import type { View } from '../workspace/useWorkspaceRoute';
import type { DeploymentFeed } from './Deployments';
import { Icon } from './Icon';

type Item = { key: string; title: string; detail: string; action: string; open: () => void };

const name = (state: RuntimeState | null, id: string) => id === USER ? 'You' : state?.agents[id]?.label || id;
const clip = (text: string, max = 140) => (text.length > max ? text.slice(0, max - 1) + '…' : text);

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
      key: `review:${review.id}`,
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
      key: `followup:${followup.id}`,
      title: followup.created_by === USER ? 'Your follow-up note is open' : `${name(state, followup.created_by)} needs your answer`,
      detail: clip(followup.text),
      action: 'Open follow-up',
      open: () => openBoard(task ? { task: task.id } : { tab: 'coordination' }),
    });
  }
  const feed = deployments.data;
  const release = feed && !feed.pending.length ? feed.releases.find(r => r.relation === 'newer' && r.rehearsal_passed) : undefined;
  if (release) {
    items.push({
      key: `release:${release.id}`,
      title: `Release ${release.version || release.id} is ready to deploy`,
      detail: clip(release.title || release.subject || 'Reviewed and rehearsed; approve it when you are ready.'),
      action: 'Review deployment',
      open: () => { openServer('updates'); reveal('.server-page .deploy-panel'); },
    });
  }

  const count = items.length;
  return <div className="ws-bell" ref={root}>
    <button className={`ws-icon-button ws-bell-button${count ? ' has-items' : ''}`} aria-haspopup="dialog" aria-expanded={open}
      aria-label={count ? `${count} ${count === 1 ? 'item' : 'items'} waiting on you` : 'Nothing is waiting on you'}
      title={count ? `${count} waiting on you` : 'Nothing is waiting on you'} onClick={() => setOpen(!open)}>
      <Icon name="bell" size={18} />
      {count > 0 && <span className="ws-bell-count" aria-hidden="true">{count > 9 ? '9+' : count}</span>}
    </button>
    {open && <div className="ws-popover ws-bell-menu" role="dialog" aria-label="Waiting on you">
      <h2>Waiting on you{count ? ` (${count})` : ''}</h2>
      {count ? <ul>{items.map(item => <li key={item.key}>
        <button onClick={() => { setOpen(false); item.open(); }}>
          <strong>{item.title}</strong>
          {item.detail && <small>{item.detail}</small>}
          <span className="ws-bell-action">{item.action} →</span>
        </button>
      </li>)}</ul> : <p>Nothing needs you right now. Approvals, questions, reviews, repository requests and releases ready to deploy show up here.</p>}
    </div>}
  </div>;
}
