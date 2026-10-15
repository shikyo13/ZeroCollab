/* Team page: named agent profiles (static/profiles.js) and their live runtime
 * controls (static/runtime.js provider cards) in one place. Same endpoints:
 * /api/runtime/profiles[/{id}[/archive]], /api/runtime/agent/{id}/stop|resume,
 * /api/runtime/refresh, plus runtime_state/agents events. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';
import { leaderId, leaderName } from '../lib/responders';
import { sessionTitle, useRoomEvent, useRuntimeState } from '../lib/room';
import type { AgentState, RunInfo, RuntimeState } from '../lib/types';
import { ErrorText, useAction } from '../board/shared';
import { ProjectManagerControls } from '../board/ManagerAuditPanel';
import '../board/board.css';
import { ProfileEditor, type Profile, type ProfilesData } from './ProfileEditor';
import { Providers } from './Providers';
import { TeamBuilder } from './TeamBuilder';
import { useReleaseGrant } from '../components/useReleaseGrant';
import { leaveSuggestion, suggestionTarget } from '../lib/teamBuilder';
import './team.css';

const STATE_LABELS: Record<string, string> = {
  ready: 'Ready',
  working: 'Working',
  login_required: 'Sign-in needed',
  blocked: 'Unavailable',
};

function statusOf(agent: AgentState | undefined, profile: Profile, running: boolean): { label: string; tone: string } {
  if (profile.archived) return { label: 'Archived', tone: 'muted' };
  if (!agent) return { label: 'Starting', tone: 'muted' };
  if (agent.paused) return { label: 'Stopped', tone: 'warn' };
  if (running) return { label: 'Working', tone: 'accent' };
  return { label: STATE_LABELS[agent.state] || agent.state.replaceAll('_', ' '), tone: agent.state === 'ready' ? 'ok' : agent.state === 'working' ? 'accent' : 'danger' };
}

function settingsText(run: RunInfo | undefined): string {
  if (!run) return '';
  const requested = (run.settings ?? {}) as { model?: string; effort?: string };
  const reported = (run.reported_settings ?? {}) as { model?: string; effort?: string };
  return `${reported.model || requested.model || 'CLI default'} · effort ${reported.effort || requested.effort || 'default'}`;
}

function AgentRow({
  profile,
  agent,
  run,
  providerLabel,
  selected,
  leader,
  onSelect,
  guarded,
  onProviders,
}: {
  profile: Profile;
  agent?: AgentState;
  run?: RunInfo;
  providerLabel: string;
  selected: boolean;
  leader: boolean;
  onSelect: () => void;
  /** True when an unsaved edit must be saved or cancelled first (it warns instead). */
  guarded: () => boolean;
  /** Opens the existing provider recovery page without abandoning an edit. */
  onProviders: () => void;
}) {
  const { run: act, busy, error } = useAction();
  const status = statusOf(agent, profile, !!run);
  const toggle = () => {
    if (guarded()) return;
    void act(() => runtime(`/agent/${encodeURIComponent(profile.id)}/${agent?.paused ? 'resume' : 'stop'}`, {}));
  };
  return (
    <li className={`tm-agent${selected ? ' tm-agent-selected' : ''}${profile.archived ? ' tm-agent-archived' : ''}`}>
      <button type="button" className="tm-agent-open" onClick={onSelect} aria-current={selected ? 'true' : undefined}>
        <span className="tm-avatar" style={{ background: profile.color }} aria-hidden="true">
          {profile.name.slice(0, 2).toUpperCase()}
        </span>
        <span className="tm-agent-text">
          <strong>{profile.name}{leader && <span className="conversation-leader-badge" title="Current project manager">Project manager</span>}</strong>
          <small>
            @{profile.handle || profile.id} · {providerLabel}
          </small>
        </span>
      </button>
      <div className="tm-agent-status">
        <span className={`tm-state tm-tone-${status.tone}`}>{status.label}</span>
        {!!agent?.queued && <span className="bd-muted">{agent.queued} queued</span>}
        {!profile.archived && agent && (
          <button type="button" className={`bd-btn ${agent.paused ? '' : 'bd-btn-danger'} tm-stop`} disabled={busy} onClick={toggle}>
            {agent.paused ? 'Resume' : 'Stop'}
          </button>
        )}
      </div>
      {run && <p className="tm-agent-run">Active turn in {sessionTitle(run.channel)}: {settingsText(run)}</p>}
      {agent?.detail && !agent.paused && agent.state !== 'ready' && (
        <p className="tm-agent-detail">{agent.detail}
          {agent.detail.includes('Team > Providers') && <> <button type="button" className="bd-link" onClick={onProviders}>Open Providers</button></>}
        </p>
      )}
      <ErrorText message={error} />
    </li>
  );
}

export default function Team() {
  const [target, setTarget] = useState(suggestionTarget);
  useEffect(() => {
    const read = () => setTarget(suggestionTarget());
    window.addEventListener('popstate', read);
    return () => window.removeEventListener('popstate', read);
  }, []);
  return <>
    <div hidden={!!target}><TeamHome /></div>
    {target && <div className="bd-root tm-root"><TeamBuilder
      key={`${target.project}:${target.proposal}`} target={target}
      onClose={() => { leaveSuggestion(); window.setTimeout(() => document.querySelector<HTMLElement>('.tm-root h1')?.focus(), 0); }}
      onAdded={() => undefined}
    /></div>}
  </>;
}

function TeamHome() {
  const { data: runtimeState, error: runtimeError, refresh: refreshRuntime } = useRuntimeState();
  const { view: releaseGrant } = useReleaseGrant(runtimeState?.project_id ?? '');
  const [data, setData] = useState<ProfilesData | null>(null);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<'edit' | 'add' | 'duplicate'>('edit');
  const [dirty, setDirty] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [guard, setGuard] = useState('');
  const [notice, setNotice] = useState('');
  const [resetKey, setResetKey] = useState(0);
  const [building, setBuilding] = useState(false);
  const [section, setSection] = useState<'agents' | 'providers'>('agents');
  const [checks, setChecks] = useState(0);
  const refreshAction = useAction();
  const onDirty = useCallback((value: boolean) => {
    setDirty(value);
    if (!value) setGuard('');
  }, []);

  const load = useCallback(async () => {
    try {
      const next = await runtime<ProfilesData>('/profiles');
      setData(next);
      setLoadError('');
      return next;
    } catch (e) {
      setLoadError(errorMessage(e));
      return null;
    }
  }, []);

  useEffect(() => {
    void load().then(next => {
      if (next) setSelected(cur => cur ?? next.profiles.find(p => !p.archived)?.id ?? null);
    });
  }, [load]);
  // broadcast_agents follows every profile change; catalogs arrive with runtime refreshes.
  useRoomEvent('agents', () => void load());

  const profile = data?.profiles.find(p => p.id === selected) ?? null;
  const visible = useMemo(() => (data?.profiles ?? []).filter(p => !p.archived || showArchived || p.id === selected), [data, showArchived, selected]);
  const archivedCount = (data?.profiles ?? []).filter(p => p.archived).length;
  const runs = runtimeState?.runs ?? [];

  // Actions that would leave an edit behind warn instead, so a draft is never silently dropped.
  const guarded = (message = 'Save or cancel your changes first.') => {
    if (dirty) setGuard(message);
    return dirty;
  };

  const choose = (id: string | null, nextMode: 'edit' | 'add' | 'duplicate') => {
    if (guarded('Save or cancel your changes before switching.')) return;
    setGuard('');
    setNotice('');
    setSelected(id);
    setMode(nextMode);
  };

  if (!data) {
    return (
      <div className="bd-root tm-root">
        {loadError ? <ErrorText message={`Could not load the team: ${loadError}`} /> : <p className="bd-empty">Loading team...</p>}
      </div>
    );
  }

  if (building) {
    return (
      <div className="bd-root tm-root">
        <TeamBuilder onClose={() => setBuilding(false)} onAdded={() => void load()} />
      </div>
    );
  }

  const providerLabel = (template: string) => data.providers.find(p => p.id === template)?.label || template;
  const active = data.profiles.filter(p => !p.archived).length;

  return (
    <div className="bd-root tm-root">
      <header className="bd-header">
        <div>
          <h1 className="bd-title" tabIndex={-1}>Team</h1>
          <p className="bd-muted">
            {active} active {active === 1 ? 'agent' : 'agents'}
            {data.paused ? ' · room stopped' : ''}. Each agent has its own prompt, model, inbox, task ownership and working tree.
          </p>
        </div>
        <div className="bd-actions">
          <button
            type="button"
            className="bd-btn"
            disabled={refreshAction.busy}
            title="Re-check provider sign-in and reload model catalogs"
            onClick={() => guarded() || void refreshAction.run(async () => {
              await runtime('/refresh', {});
              await load();
              setChecks(n => n + 1);
            })}
          >
            {refreshAction.busy ? 'Refreshing...' : 'Refresh connections'}
          </button>
          <button
            type="button"
            className="bd-btn"
            disabled={dirty}
            title={dirty ? 'Save or cancel your changes first' : 'Agents propose experts for your project brief; you approve who joins'}
            onClick={() => setBuilding(true)}
          >
            Build a team
          </button>
          <button type="button" className="bd-btn bd-btn-primary" onClick={() => choose(null, 'add')}>
            Add agent
          </button>
        </div>
      </header>
      <ErrorText message={loadError || runtimeError || refreshAction.error} />
      <ProjectManagerControls key={runtimeState?.project_id ?? runtimeState?.project} state={runtimeState} refresh={refreshRuntime} />
      <nav className="bd-tabs tm-sections" aria-label="Team sections">
        <button type="button" className="bd-tab" aria-current={section === 'agents' ? 'page' : undefined}
          onClick={() => guarded('Save or cancel your changes first.') || setSection('agents')}>Agents ({active})</button>
        <button type="button" className="bd-tab" aria-current={section === 'providers' ? 'page' : undefined}
          onClick={() => guarded('Save or cancel your changes first.') || setSection('providers')}>Providers</button>
      </nav>

      {section === 'providers' ? <Providers reload={checks} /> : <div className="tm-layout">
        <section className="tm-roster" aria-label="Agents">
          {visible.length ? (
            <ul className="tm-agents">
              {visible.map(p => (
                <AgentRow
                  key={p.id}
                  profile={p}
                  agent={runtimeState?.agents[p.id]}
                  run={runs.find(r => r.agent === p.id)}
                  providerLabel={providerLabel(p.template)}
                  onProviders={() => guarded('Save or cancel your changes first.') || setSection('providers')}
                  leader={p.id === leaderId(runtimeState?.responders)}
                  selected={mode === 'edit' && p.id === selected}
                  onSelect={() => choose(p.id, 'edit')}
                  guarded={() => guarded()}
                />
              ))}
            </ul>
          ) : (
            <p className="bd-empty">Add the experts this project needs.</p>
          )}
          {archivedCount > 0 && (
            <label className="bd-check tm-archived-toggle">
              <input type="checkbox" checked={showArchived} onChange={e => setShowArchived(e.target.checked)} />
              Show archived agents ({archivedCount})
            </label>
          )}
        </section>
        <section className="bd-panel tm-editor" aria-label="Agent profile">
          {notice && !guard && (
            <p className="tm-notice" role="status">
              {notice}
            </p>
          )}
          {guard && (
            <p className="bd-banner bd-banner-warn" role="alert">
              {guard}
            </p>
          )}
          <ProfileEditor
            key={`${mode}:${selected ?? ''}:${resetKey}`}
            data={data}
            profile={mode === 'add' ? null : profile}
            duplicate={mode === 'duplicate'}
            agent={profile ? runtimeState?.agents[profile.id] : undefined}
            releasePermission={!!releaseGrant?.enabled && releaseGrant.profile === profile?.id}
            onDirty={onDirty}
            onSaved={async (id, message) => {
              setDirty(false);
              await load();
              setSelected(id);
              setMode('edit');
              setResetKey(k => k + 1);
              setNotice(message);
            }}
            onDuplicate={() => choose(selected, 'duplicate')}
            onCancel={() => {
              setDirty(false);
              setGuard('');
              setNotice('');
              setMode('edit');
              setResetKey(k => k + 1);
              if (mode !== 'edit') setSelected(cur => cur ?? data.profiles.find(p => !p.archived)?.id ?? null);
            }}
          />
        </section>
      </div>}
    </div>
  );
}
