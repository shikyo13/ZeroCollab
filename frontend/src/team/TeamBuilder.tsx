/* Team builder: Brief > Review team > Add approved members. Implements the
 * user-approved design (task ccc5fbdb, mockup final.html 5783b87f5e0f1949).
 * Connected agents draft and challenge the proposal on the server; this view
 * only records the user's per-member decisions and, on request, adds the
 * approved members. It never edits or archives existing agents. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { errorMessage } from '../lib/api';
import { useRoomEvent } from '../lib/room';
import { loadContext, loadProposal, retryProposal, type BuilderContext, type Proposal } from '../lib/teamBuilder';
import { ErrorText, useAction } from '../board/shared';
import { BriefStep } from './builder/BriefStep';
import { Coverage } from './builder/Coverage';
import { MemberCard } from './builder/MemberCard';
import { Summary } from './builder/Summary';
import { SOURCE_LABELS, plural } from './builder/labels';
import './teamBuilder.css';

const POLL_MS = 2500;
const WORKING: Proposal['status'][] = ['drafting', 'challenging'];

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
