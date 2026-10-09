/* Step 2: one proposed member, with approve / edit / revise / decline decisions. */
import { useState, type FormEvent } from 'react';
import { actOnMember, type MemberChanges, type Proposal, type ProposedMember } from '../../lib/teamBuilder';
import { ErrorText, Field, useAction } from '../../board/shared';
import { HANDLE_PATTERN, LIMITS } from '../../lib/constants';
import type { ProviderInfo } from '../ProfileEditor';
import { effortLabel, plural, providerSummary } from './labels';

const STATUS_TAGS: Record<ProposedMember['status'], { label: string; tone: string }> = {
  proposed: { label: 'Needs your review', tone: '' },
  revision_requested: { label: 'Revision requested', tone: 'warn' },
  approved: { label: 'Approved', tone: 'ok' },
  declined: { label: 'Declined', tone: 'muted' },
  added: { label: 'Added to team', tone: 'accent' },
};

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
      {catalog?.loading ? <p className="bd-muted">Loading model choices…</p>
        : catalog?.error && <p className="bd-error">{catalog.error}</p>}
      <Field label="Working background" hint={`${draft.background.length.toLocaleString()} / ${LIMITS.profileText.toLocaleString()} characters`}>
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

export function MemberCard({
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
            <>
              <button type="button" className="bd-btn" disabled={disabled} onClick={() => setMode('revise')}>
                Ask for revision
              </button>
              <button type="button" className="bd-btn bd-btn-quiet" disabled={disabled} onClick={() => void send({ action: 'decline' })}>
                Decline
              </button>
            </>
          )}
        </div>
      )}
    </article>
  );
}
