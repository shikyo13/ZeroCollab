/* Team builder: Brief > Review team > Add approved members. Implements the
 * user-approved design (task ccc5fbdb, mockup final.html 5783b87f5e0f1949).
 * Connected agents draft and challenge the proposal on the server; this view
 * only records the user's per-member decisions and, on request, adds the
 * approved members. It never edits or archives existing agents. */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { errorMessage } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import {
  actOnMember,
  addApproved,
  agentList,
  capacityText,
  coverageState,
  createProposal,
  loadContext,
  loadProposal,
  retryProposal,
  type BriefSource,
  type BuilderContext,
  type CoverageState,
  type MemberChanges,
  type Proposal,
  type ProposedMember,
} from '../lib/teamBuilder';
import { ErrorText, Field, useAction } from '../board/shared';
import type { Profile, ProviderInfo } from './ProfileEditor';
import { HANDLE_PATTERN, LIMITS } from '../lib/constants';
import './teamBuilder.css';

const POLL_MS = 2500;
const WORKING: Proposal['status'][] = ['drafting', 'challenging'];

const STATUS_TAGS: Record<ProposedMember['status'], { label: string; tone: string }> = {
  proposed: { label: 'Needs your review', tone: '' },
  revision_requested: { label: 'Revision requested', tone: 'warn' },
  approved: { label: 'Approved', tone: 'ok' },
  declined: { label: 'Declined', tone: 'muted' },
  added: { label: 'Added to team', tone: 'accent' },
};

const COVERAGE_FLAGS: Record<CoverageState, { label: string; tone: string }> = {
  led: { label: 'led', tone: 'ok' },
  gap_if_declined: { label: 'gap if declined', tone: 'warn' },
  shared: { label: 'shared, no lead', tone: 'info' },
  gap: { label: 'no one covers this', tone: 'danger' },
};

const SOURCE_LABELS: Record<BriefSource, string> = { spec: 'Project spec', goal: 'Shared goal', text: 'Your own description' };

const plural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`;

function providerSummary(member: ProposedMember, providers: ProviderInfo[]): string {
  const provider = providers.find(p => p.id === member.template);
  const label = provider?.label || member.template || 'Provider';
  if (!member.model && !member.effort) return `${label} · connection defaults`;
  const model = provider?.controls.models.find(m => m.value === member.model)?.label || member.model || 'default model';
  return `${label} · ${model} · effort ${member.effort || 'default'}`;
}

/* ---------- Step 1: brief ---------- */

function BriefStep({
  context,
  previous,
  onCreated,
  onCancel,
}: {
  context: BuilderContext;
  previous: Proposal | null;
  onCreated: (proposal: Proposal) => void;
  onCancel: () => void;
}) {
  const agents = agentList(context.agents).filter(a => a.available);
  const initialSource: BriefSource = previous?.source ?? (context.spec.trim() ? 'spec' : context.goal.trim() ? 'goal' : 'text');
  const textFor = (source: BriefSource) => (source === 'spec' ? context.spec : source === 'goal' ? context.goal : '');
  const [source, setSource] = useState<BriefSource>(initialSource);
  const [brief, setBrief] = useState(previous?.brief ?? textFor(initialSource));
  const [drafter, setDrafter] = useState(previous?.drafter || agents[0]?.handle || '');
  const [challenger, setChallenger] = useState(previous?.challenger || agents.find(a => a.handle !== (previous?.drafter || agents[0]?.handle))?.handle || '');
  const create = useAction();

  const choose = (next: BriefSource) => {
    setSource(next);
    setBrief(textFor(next));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let created: Proposal | null = null;
    const ok = await create.run(async () => {
      created = await createProposal({ source, brief: brief.trim(), drafter, challenger, channel: window.activeChannel });
    });
    if (ok && created) onCreated(created);
  };

  return (
    <form className="bd-panel bd-form tb-brief-form" onSubmit={submit}>
      <div>
        <h2>What is this project for?</h2>
        <p className="bd-muted">
          The team is shaped around this brief. Your current agents stay as they are; the proposal only suggests additions.
        </p>
      </div>
      <fieldset className="tb-sources">
        <legend className="bd-field-label">Start from</legend>
        {(['spec', 'goal', 'text'] as BriefSource[]).map(value => {
          const empty = value !== 'text' && !textFor(value).trim();
          return (
            <label key={value} className={`tb-source${source === value ? ' tb-source-on' : ''}${empty ? ' tb-source-off' : ''}`}>
              <input type="radio" name="tb-source" value={value} checked={source === value} disabled={empty} onChange={() => choose(value)} />
              <span>
                <strong>{SOURCE_LABELS[value]}</strong>
                <small>{empty ? (value === 'spec' ? 'No saved spec in this project' : 'No shared goal is running') : value === 'text' ? 'Describe it in your own words' : 'Edit below before drafting'}</small>
              </span>
            </label>
          );
        })}
      </fieldset>
      <Field label="Brief" hint={`${brief.length.toLocaleString()} characters. Add what matters: audience, constraints, what the current team lacks.`}>
        <textarea value={brief} rows={8} required onChange={e => setBrief(e.target.value)} placeholder="e.g. Build an accessible tutorial hub from recovered pages and instructional images." />
      </Field>
      <div className="bd-form-row">
        <Field label="Drafted by" hint="Proposes the members and the expertise each one covers.">
          <select
            value={drafter}
            required
            onChange={e => {
              const next = e.target.value;
              setDrafter(next);
              // The challenger list hides the drafter; move the challenger rather than leave a hidden clash.
              if (challenger === next) setChallenger(agents.find(a => a.handle !== next)?.handle ?? '');
            }}
          >
            {!agents.length && <option value="">No connected agent available</option>}
            {agents.map(a => (
              <option key={a.handle} value={a.handle}>
                {a.label} (@{a.handle})
              </option>
            ))}
          </select>
        </Field>
        <Field label="Challenged by" hint="Questions overlaps and gaps before you see the proposal.">
          <select value={challenger} required onChange={e => setChallenger(e.target.value)}>
            {agents.length < 2 && <option value="">Needs a second connected agent</option>}
            {agents
              .filter(a => a.handle !== drafter)
              .map(a => (
                <option key={a.handle} value={a.handle}>
                  {a.label} (@{a.handle})
                </option>
              ))}
          </select>
        </Field>
      </div>
      {agents.length < 2 && (
        <p className="bd-banner bd-banner-warn" role="alert">
          Drafting needs two connected agents: one drafts, the other challenges. Start or sign in another agent on the Team page.
        </p>
      )}
      <ErrorText message={create.error} />
      <div className="bd-actions">
        <span className="tm-spacer" />
        <button type="button" className="bd-btn" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="bd-btn bd-btn-primary" disabled={create.busy || !drafter || !challenger || drafter === challenger || !brief.trim()}>
          {create.busy ? 'Starting...' : previous ? 'Draft a new proposal' : 'Draft the team'}
        </button>
      </div>
      {previous && <p className="bd-muted tb-note">Drafting again creates a new proposal. Members already added stay on the team.</p>}
    </form>
  );
}

/* ---------- Step 2: coverage ---------- */

function Coverage({ proposal, current }: { proposal: Proposal; current: Profile[] }) {
  const [open, setOpen] = useState(true);
  const currentIds = useMemo(() => new Set(current.map(p => p.id)), [current]);
  const inPlay = proposal.members.filter(m => m.status !== 'declined');
  const rows = proposal.coverage.map(row => ({ row, state: coverageState(row, currentIds, proposal.members) }));
  if (!rows.length) return null;
  const led = rows.filter(r => r.state === 'led').length;
  const atRisk = rows.filter(r => r.state === 'gap_if_declined');
  const shared = rows.filter(r => r.state === 'shared');
  const gaps = rows.filter(r => r.state === 'gap');
  const columns = [
    ...current.map(p => ({ handle: p.id, name: p.name, proposed: false })),
    ...inPlay.map(m => ({ handle: m.id, name: m.name, proposed: m.status !== 'added' })),
  ];
  return (
    <details className="tb-coverage" open={open} onToggle={e => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <b>Expertise coverage</b>
        <span className="tb-flag tb-tone-ok">
          {led} of {rows.length} needs led
        </span>
        {atRisk.map(({ row }) => (
          <span key={row.need} className="tb-flag tb-tone-warn">
            {row.need}: gap if declined
          </span>
        ))}
        {shared.map(({ row }) => (
          <span key={row.need} className="tb-flag tb-tone-info">
            {row.need}: shared, no lead
          </span>
        ))}
        {gaps.map(({ row }) => (
          <span key={row.need} className="tb-flag tb-tone-danger">
            {row.need}: not covered
          </span>
        ))}
        <span className="tb-toggle">{open ? 'Hide coverage' : 'Show coverage'}</span>
      </summary>
      <div className="tb-coverage-inner">
        <div className="tb-table-scroll">
          <table>
            <thead>
              <tr>
                <th className="tb-need">Need (from the brief)</th>
                {columns.map(c => (
                  <th key={c.handle}>
                    {c.name}
                    <small>@{c.handle}</small>
                    <span className={`tb-col-tag${c.proposed ? ' tb-col-new' : ''}`}>{c.proposed ? 'proposed' : 'current'}</span>
                  </th>
                ))}
                <th>
                  <span className="tb-sr">Status</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ row, state }) => (
                <tr key={row.need} className={state === 'gap_if_declined' || state === 'gap' ? 'tb-row-gap' : undefined}>
                  <td className="tb-need">
                    {row.need}
                    {row.detail && <small>{row.detail}</small>}
                  </td>
                  {columns.map(c => (
                    <td key={c.handle}>
                      {row.lead.includes(c.handle) ? (
                        <span className="tb-dot" title={`${c.name} leads`} aria-label="leads" />
                      ) : row.support.includes(c.handle) ? (
                        <span className="tb-ring" title={`${c.name} supports or reviews`} aria-label="supports" />
                      ) : null}
                    </td>
                  ))}
                  <td>
                    <span className={`tb-flag tb-tone-${COVERAGE_FLAGS[state].tone}`}>{COVERAGE_FLAGS[state].label}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="tb-legend">
          <span>
            <span className="tb-dot tb-dot-sm" aria-hidden="true" /> leads
          </span>
          <span>
            <span className="tb-ring tb-dot-sm" aria-hidden="true" /> supports or reviews
          </span>
          <span>Read-only. To change who covers what, use Ask for revision on a member.</span>
        </div>
      </div>
    </details>
  );
}

/* ---------- Step 2: member cards ---------- */

function effortLabel(value: string): string {
  return value === 'xhigh' ? 'Extra high (xhigh)' : value[0].toUpperCase() + value.slice(1);
}

function MemberEdit({
  member,
  providers,
  busy,
  onSave,
  onCancel,
}: {
  member: ProposedMember;
  providers: ProviderInfo[];
  busy: boolean;
  onSave: (changes: MemberChanges) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState({ name: member.name, id: member.id, template: member.template, model: member.model, effort: member.effort, background: member.background });
  const catalog = providers.find(p => p.id === draft.template)?.controls;
  const models = [...(catalog?.models ?? [])];
  if (!models.some(m => m.value === draft.model)) models.unshift({ value: draft.model, label: draft.model || 'Provider default' });
  const efforts = catalog?.models.find(m => m.value === draft.model)?.efforts ?? [];
  const update = (patch: Partial<typeof draft>) => setDraft(d => ({ ...d, ...patch }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const changes: MemberChanges = {};
    (Object.keys(draft) as (keyof typeof draft)[]).forEach(key => {
      if (draft[key] !== member[key]) changes[key] = draft[key];
    });
    onSave(changes);
  };
  return (
    <form className="bd-form tb-edit" onSubmit={submit}>
      <div className="bd-form-row">
        <Field label="Name">
          <input value={draft.name} required maxLength={LIMITS.name} onChange={e => update({ name: e.target.value })} />
        </Field>
        <Field label="Mention handle" hint="Lowercase letters, digits and hyphens.">
          <div className="tm-handle">
            <span aria-hidden="true">@</span>
            <input value={draft.id} required pattern={HANDLE_PATTERN} maxLength={LIMITS.handle} onChange={e => update({ id: e.target.value })} />
          </div>
        </Field>
      </div>
      <div className="bd-form-row">
        <Field label="Provider">
          <select
            value={draft.template}
            onChange={e => {
              const next = providers.find(p => p.id === e.target.value);
              update({ template: e.target.value, model: next?.controls.selected?.model ?? next?.controls.models[0]?.value ?? '', effort: '' });
            }}
          >
            {providers.map(p => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Model">
          <select value={draft.model} disabled={!catalog?.models.length} onChange={e => update({ model: e.target.value, effort: '' })}>
            {models.map(m => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Reasoning effort">
          <select value={efforts.includes(draft.effort) ? draft.effort : ''} disabled={!efforts.length} onChange={e => update({ effort: e.target.value })}>
            <option value="">Provider default</option>
            {efforts.map(v => (
              <option key={v} value={v}>
                {effortLabel(v)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {catalog?.error && <p className="bd-error">{catalog.error}</p>}
      <Field label="Working background" hint={`${draft.background.length.toLocaleString()} / 16,000 characters`}>
        <textarea value={draft.background} rows={6} maxLength={LIMITS.profileText} onChange={e => update({ background: e.target.value })} />
      </Field>
      <div className="bd-actions">
        <span className="tm-spacer" />
        <button type="button" className="bd-btn" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="bd-btn bd-btn-primary" disabled={busy}>
          {busy ? 'Saving...' : 'Save profile'}
        </button>
      </div>
      <p className="bd-muted tb-note">Saving an edited profile asks for your approval again.</p>
    </form>
  );
}

function MemberCard({
  proposal,
  member,
  providers,
  locked,
  onChange,
}: {
  proposal: Proposal;
  member: ProposedMember;
  providers: ProviderInfo[];
  locked: boolean;
  onChange: (next: Proposal) => void;
}) {
  const [mode, setMode] = useState<'view' | 'edit' | 'revise'>('view');
  const [feedback, setFeedback] = useState('');
  const act = useAction();
  const tag = STATUS_TAGS[member.status];
  const send = async (body: Parameters<typeof actOnMember>[2]) => {
    let next: Proposal | null = null;
    const ok = await act.run(async () => {
      next = await actOnMember(proposal.id, member.member_id, body);
    });
    if (ok && next) {
      onChange(next);
      setMode('view');
      setFeedback('');
    }
  };
  const added = member.status === 'added';
  const disabled = act.busy || locked;
  return (
    <article className={`tb-card tb-card-${member.status}`} aria-label={member.name}>
      <div className="tb-card-head">
        <h3>
          {member.name} <span className="bd-muted">@{member.id}</span>
        </h3>
        <span className={`tb-tag tb-tone-${tag.tone}`}>{tag.label}</span>
      </div>
      {!added && (
        <p className={`tb-handle-state${member.handle_available ? '' : ' tb-handle-taken'}`}>
          {member.handle_available ? 'Handle available at last check' : `@${member.id} is already taken. Edit the handle before approving.`}
        </p>
      )}
      {member.error && (
        <p className="bd-error" role="alert">
          {member.status === 'approved' ? 'Not added: ' : ''}
          {member.error}
        </p>
      )}
      {mode === 'edit' ? (
        <MemberEdit member={member} providers={providers} busy={act.busy} onSave={changes => void send({ action: 'edit', changes })} onCancel={() => setMode('view')} />
      ) : (
        <>
          <div className="tb-detail">
            <b>Why this member</b>
            <p>{member.rationale || 'No rationale given.'}</p>
          </div>
          <details className="tb-detail">
            <summary>Working background · {plural(member.background.length, 'character')}</summary>
            <p className="tb-background">{member.background || 'No background written yet.'}</p>
          </details>
          <small className="bd-muted">{providerSummary(member, providers)}</small>
        </>
      )}
      {mode === 'revise' && (
        <form
          className="bd-form tb-revise"
          onSubmit={e => {
            e.preventDefault();
            void send({ action: 'request_revision', feedback: feedback.trim() });
          }}
        >
          <Field label={`What should ${proposal.drafter ? '@' + proposal.drafter : 'the drafter'} change?`}>
            <textarea value={feedback} rows={3} required autoFocus onChange={e => setFeedback(e.target.value)} placeholder="e.g. Make this member the testing lead instead of a second reviewer." />
          </Field>
          <div className="bd-actions">
            <span className="tm-spacer" />
            <button type="button" className="bd-btn" onClick={() => setMode('view')}>
              Cancel
            </button>
            <button type="submit" className="bd-btn bd-btn-primary" disabled={act.busy || !feedback.trim()}>
              Send revision request
            </button>
          </div>
        </form>
      )}
      <ErrorText message={act.error} />
      {mode === 'view' && !added && (
        <div className="tb-actions">
          {member.status !== 'approved' && (
            <button
              type="button"
              className="bd-btn bd-btn-primary"
              disabled={disabled || !member.handle_available}
              title={member.handle_available ? undefined : 'Edit the handle first'}
              onClick={() => void send({ action: 'approve' })}
            >
              {member.status === 'declined' ? 'Approve after all' : 'Approve member'}
            </button>
          )}
          <button type="button" className="bd-btn" disabled={disabled} onClick={() => setMode('edit')}>
            Edit profile
          </button>
          {member.status !== 'declined' && (
            <button type="button" className="bd-btn" disabled={disabled} onClick={() => setMode('revise')}>
              Ask for revision
            </button>
          )}
          {member.status !== 'declined' && (
            <button type="button" className="bd-btn bd-btn-quiet" disabled={disabled} onClick={() => void send({ action: 'decline' })}>
              Decline
            </button>
          )}
        </div>
      )}
    </article>
  );
}

/* ---------- Step 3: summary and add ---------- */

function Summary({
  context,
  proposal,
  current,
  onChange,
}: {
  context: BuilderContext;
  proposal: Proposal;
  current: Profile[];
  onChange: (next: Proposal) => void;
}) {
  const add = useAction();
  const by = (status: ProposedMember['status']) => proposal.members.filter(m => m.status === status);
  const approved = by('approved');
  const added = by('added');
  const waiting = [...by('proposed'), ...by('revision_requested')];
  const declined = by('declined');
  const failed = approved.filter(m => m.error);
  // The server accepts decisions and additions only once drafting and challenging have settled.
  const working = proposal.status !== 'ready';
  const submit = async () => {
    let next: Proposal | null = null;
    const ok = await add.run(async () => {
      next = await addApproved(proposal.id);
    });
    if (ok && next) onChange(next);
  };
  const names = (list: ProposedMember[]) => list.map(m => m.name).join(', ');
  return (
    <aside className="bd-panel tb-summary" aria-label="Your proposed team">
      <h2>Your proposed team</h2>
      <div>
        <b>{plural(current.length, 'current member')}</b>
        <small>{current.map(p => p.name).join(', ') || 'None yet'}</small>
      </div>
      {added.length > 0 && (
        <div>
          <b>{plural(added.length, 'member')} added</b>
          <small>{names(added)}</small>
        </div>
      )}
      <div>
        <b>{plural(approved.length, 'approved addition')}</b>
        <small>{names(approved) || 'Approve members on their cards'}</small>
      </div>
      {waiting.length > 0 && (
        <div>
          <b>{waiting.length} awaiting your decision</b>
          <small>{names(waiting)}</small>
        </div>
      )}
      {declined.length > 0 && (
        <div>
          <b>{declined.length} declined</b>
          <small>{names(declined)}</small>
        </div>
      )}
      <div className="tb-summary-review">
        <b>Proposed total: {current.length + added.length + approved.length + waiting.length} if every pending member joins</b>
        <small>{capacityText(context.capacity)}. Provider limits still apply.</small>
        {(proposal.summary || !!proposal.history?.length) && <b>How the agents settled it</b>}
        {proposal.summary && <small className="tb-settled">{proposal.summary}</small>}
        {!!proposal.history?.length && (
          <ol className="tb-history">
            {proposal.history.map((h, i) => (
              <li key={i}>
                <small>
                  <b>@{h.actor}</b> {h.stage}: {h.summary}
                </small>
              </li>
            ))}
          </ol>
        )}
      </div>
      <ErrorText message={add.error} />
      <button type="button" className="bd-btn bd-btn-primary tb-add" disabled={add.busy || working || !approved.length} onClick={() => void submit()}>
        {add.busy
          ? 'Adding...'
          : failed.length && failed.length === approved.length
            ? `Retry adding ${plural(approved.length, 'member')}`
            : approved.length
              ? `Add ${plural(approved.length, 'approved member')}`
              : 'No approved members to add'}
      </button>
      {approved.length > 0 && (
        <small className="bd-muted">
          Adds {names(approved)} to this project without starting work.{waiting.length ? ` ${names(waiting)} ${waiting.length === 1 ? 'stays' : 'stay'} in this saved proposal.` : ''}
        </small>
      )}
      <small className="bd-muted">
        Adding rechecks handles, connections and model choices. If one member fails, the others are still added and the failed one stays approved with the reason. Retrying adds only the missing members.
      </small>
    </aside>
  );
}

/* ---------- Page ---------- */

export function TeamBuilder({ onClose, onAdded }: { onClose: () => void; onAdded: () => void | Promise<void> }) {
  const [context, setContext] = useState<BuilderContext | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [step, setStep] = useState<'brief' | 'review'>('brief');
  const [error, setError] = useState('');

  const reload = useCallback(async () => {
    try {
      const next = await loadContext();
      setContext(next);
      setError('');
      return next;
    } catch (e) {
      setError(errorMessage(e));
      return null;
    }
  }, []);

  useEffect(() => {
    void reload().then(next => {
      if (next?.proposal) {
        setProposal(next.proposal);
        setStep('review');
      }
    });
  }, [reload]);

  const refreshProposal = useCallback((id: string) => {
    loadProposal(id)
      .then(next => setProposal(cur => (cur?.id === id ? next : cur)))
      .catch(e => setError(errorMessage(e)));
  }, []);
  useRoomEvent<{ proposal?: string; id?: string }>('team_builder', () => {
    if (proposal) refreshProposal(proposal.id);
  });
  const retry = useAction();

  // Drafting and challenging run on the server; polling also notices a stalled or stopped drafter.
  const working = !!proposal && WORKING.includes(proposal.status);
  useEffect(() => {
    if (!working || !proposal) return;
    const id = proposal.id;
    const timer = window.setInterval(() => refreshProposal(id), POLL_MS);
    return () => window.clearInterval(timer);
  }, [working, proposal?.id, refreshProposal]); // eslint-disable-line react-hooks/exhaustive-deps

  const change = (next: Proposal) => {
    const newlyAdded = next.members.some(m => m.status === 'added' && proposal?.members.find(p => p.member_id === m.member_id)?.status !== 'added');
    setProposal(next);
    if (newlyAdded) void Promise.resolve(onAdded()).then(() => reload());
  };

  // Members this proposal added are shown as additions, not as part of the team they joined.
  const current = useMemo(() => {
    const addedHere = new Set((proposal?.members ?? []).filter(m => m.status === 'added').map(m => m.id));
    return (context?.profiles ?? []).filter(p => !p.archived && !addedHere.has(p.id));
  }, [context, proposal]);

  if (!context) {
    return <div className="tb-root">{error ? <ErrorText message={`Could not open the team builder: ${error}`} /> : <p className="bd-empty">Loading team builder...</p>}</div>;
  }

  const ready = proposal?.status === 'ready';
  const drafter = proposal?.drafter ? '@' + proposal.drafter : 'The drafter';
  const challenger = proposal?.challenger ? '@' + proposal.challenger : 'The challenger';
  const visible = proposal?.members ?? [];
  const pending = visible.filter(m => m.status !== 'added' && m.status !== 'declined').length;

  return (
    <div className="tb-root">
      <header className="bd-header">
        <div>
          <ol className="tb-steps" aria-label="Steps">
            <li aria-current={step === 'brief' ? 'step' : undefined}>1 Brief</li>
            <li aria-current={step === 'review' && pending > 0 ? 'step' : undefined}>2 Review team</li>
            <li aria-current={step === 'review' && !!proposal && pending === 0 ? 'step' : undefined}>3 Add members</li>
          </ol>
          <h1 className="bd-title">A team shaped around your project</h1>
          <p className="bd-muted">
            {proposal && step === 'review'
              ? `${proposal.drafter ? '@' + proposal.drafter : 'An agent'} proposed ${plural(visible.length, 'addition')}${proposal.challenger ? `, challenged by @${proposal.challenger}` : ''}. You choose who joins.`
              : 'Connected agents draft and challenge a team for your brief. You choose who joins.'}
          </p>
        </div>
        <div className="bd-actions">
          <button type="button" className="bd-btn" onClick={onClose}>
            Back to team
          </button>
        </div>
      </header>
      <ErrorText message={error} />

      {step === 'brief' || !proposal ? (
        <BriefStep
          context={context}
          previous={proposal}
          onCreated={next => {
            setProposal(next);
            setStep('review');
          }}
          onCancel={() => (proposal ? setStep('review') : onClose())}
        />
      ) : (
        <>
          <section className="tb-brief">
            <div>
              <small className="tb-eyebrow">PROJECT BRIEF · {(proposal.source ? SOURCE_LABELS[proposal.source] : 'Brief').toUpperCase()}</small>
              <p>{proposal.brief}</p>
              <small className="bd-muted">Keeps the current team · Drafting again creates a new proposal for approval</small>
            </div>
            <button type="button" className="bd-btn bd-btn-quiet" disabled={working} onClick={() => setStep('brief')}>
              Edit brief &amp; re-draft
            </button>
          </section>

          {working && (
            <p className="tb-progress" role="status">
              <span className="tb-spinner" aria-hidden="true" />
              {proposal.status === 'drafting' ? `${drafter} is drafting the team from your brief...` : `${challenger} is challenging the draft for overlaps and gaps...`}
            </p>
          )}
          {proposal.status === 'failed' && (
            <div className="bd-banner bd-banner-warn" role="alert">
              <b>The proposal could not be finished.</b>
              <span>{proposal.error || 'The drafting agent did not return a proposal.'}</span>
              <span className="bd-actions">
                <button
                  type="button"
                  className="bd-btn bd-btn-primary"
                  disabled={retry.busy}
                  onClick={() => void retry.run(async () => setProposal(await retryProposal(proposal.id)))}
                >
                  {retry.busy ? 'Retrying...' : 'Try again'}
                </button>
                <button type="button" className="bd-btn" onClick={() => setStep('brief')}>
                  Edit brief first
                </button>
              </span>
              <ErrorText message={retry.error} />
            </div>
          )}

          {(ready || visible.length > 0) && (
            <>
              <Coverage proposal={proposal} current={current} />
              <div className="tb-layout">
                <section className="tb-cards" aria-label="Proposed members">
                  {visible.length ? (
                    visible.map(m => (
                      <MemberCard key={m.member_id} proposal={proposal} member={m} providers={context.providers} locked={proposal.status !== 'ready'} onChange={change} />
                    ))
                  ) : (
                    <p className="bd-empty">The agents found no gap worth a new member. Edit the brief to ask for a different team.</p>
                  )}
                </section>
                <Summary context={context} proposal={proposal} current={current} onChange={change} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
