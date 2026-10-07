/* Step 3: the proposed team at a glance, and adding the approved members. */
import { addApproved, capacityText, type BuilderContext, type Proposal, type ProposedMember } from '../../lib/teamBuilder';
import { ErrorText, useAction } from '../../board/shared';
import type { Profile } from '../ProfileEditor';
import { plural } from './labels';

export function Summary({
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
