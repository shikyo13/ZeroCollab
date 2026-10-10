/* Step 1: the brief, and which connected agents draft and challenge it. */
import { useState, type FormEvent } from 'react';
import { agentList, createProposal, type BriefSource, type BuilderContext, type Proposal } from '../../lib/teamBuilder';
import { ErrorText, Field, useAction } from '../../board/shared';
import { SOURCE_LABELS } from './labels';

export function BriefStep({
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
  const initialSource: BriefSource = previous?.source === 'suggestion' ? 'text' : previous?.source ?? (context.spec.trim() ? 'spec' : context.goal.trim() ? 'goal' : 'text');
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
        {(Object.keys(SOURCE_LABELS) as BriefSource[]).map(value => {
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
