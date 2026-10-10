/* Team builder: Brief > Review team > Add approved members. Implements the
 * user-approved design (task ccc5fbdb, mockup final.html 5783b87f5e0f1949).
 * Connected agents draft and challenge the proposal on the server; this view
 * only records the user's per-member decisions and, on request, adds the
 * approved members. It never edits or archives existing agents. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, errorMessage } from '../lib/api';
import { useRoomEvent, useRuntimeState } from '../lib/room';
import { useProjects } from '../workspace/projectData';
import { leaveSuggestion, loadContext, loadProposal, retryProposal, type BuilderContext, type Proposal, type SuggestionTarget } from '../lib/teamBuilder';
import { ErrorText, useAction } from '../board/shared';
import { BriefStep } from './builder/BriefStep';
import { Coverage } from './builder/Coverage';
import { MemberCard } from './builder/MemberCard';
import { Summary } from './builder/Summary';
import { SOURCE_LABELS, plural } from './builder/labels';
import './teamBuilder.css';

const POLL_MS = 2500;
const WORKING: Proposal['status'][] = ['drafting', 'challenging'];

export function TeamBuilder({ onClose, onAdded, target }: { onClose: () => void; onAdded: () => void | Promise<void>; target?: SuggestionTarget }) {
  const [context, setContext] = useState<BuilderContext | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [step, setStep] = useState<'brief' | 'review'>('brief');
  const [error, setError] = useState('');
  const [unavailable, setUnavailable] = useState(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  const heading = useRef<HTMLHeadingElement>(null);
  const { data: running } = useRuntimeState();
  const [activeProject, setActiveProject] = useState(window.activeProjectId || '');
  const runningProject = activeProject || running?.project_id || running?.project || '';
  const { projects } = useProjects();
  const targetProject = target?.project;
  const targetProposal = target?.proposal;
  const readOnly = !!target && (proposal?.read_only !== false || runningProject !== target.project);
  const viewedName = projects.find(p => p.id === targetProject)?.name || targetProject;
  const runningName = projects.find(p => p.id === runningProject)?.name || runningProject || 'unknown';
  useEffect(() => { mounted.current = true; if (target) heading.current?.focus(); return () => { mounted.current = false; generation.current++; }; }, []);

  const reload = useCallback(async () => {
    const request = ++generation.current;
    try {
      const [next, exact] = await Promise.all([loadContext(), targetProject && targetProposal ? loadProposal(targetProposal, targetProject) : Promise.resolve(null)]);
      if (!mounted.current || request !== generation.current) return null;
      if (exact && (exact.id !== targetProposal || exact.project_id !== targetProject)) throw new Error('The server returned a different suggestion.');
      setContext(next);
      if (exact) { setProposal(exact); setStep('review'); }
      setUnavailable(false);
      setError('');
      return next;
    } catch (e) {
      if (!mounted.current || request !== generation.current) return null;
      if (targetProject) { setUnavailable(true); setProposal(null); }
      setError(targetProject && e instanceof ApiError && e.status === 409 ? 'This suggestion is no longer available' : errorMessage(e));
      return null;
    }
  }, [targetProject, targetProposal]);

  useEffect(() => {
    void reload().then(next => {
      if (!target && mounted.current && next?.proposal) {
        setProposal(next.proposal);
        setStep('review');
      }
    });
  }, [reload]);

  const refreshProposal = useCallback((id: string) => {
    if (targetProject) { void reload(); return; }
    loadProposal(id)
      .then(next => setProposal(cur => (cur?.id === id ? next : cur)))
      .catch(e => setError(errorMessage(e)));
  }, [targetProject, reload]);
  useRoomEvent<{ proposal?: string; id?: string }>('team_builder', () => {
    if (proposal) refreshProposal(proposal.id);
  });
  useRoomEvent<{project_id?: string}>('settings', event => {
    if (event.data.project_id) setActiveProject(event.data.project_id);
    if (target) void reload();
  });
  useEffect(() => { if (target) void reload(); }, [runningProject]);
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
    if (!mounted.current || (target && next.id !== target.proposal)) return;
    generation.current++;
    const newlyAdded = next.members.some(m => m.status === 'added' && proposal?.members.find(p => p.member_id === m.member_id)?.status !== 'added');
    setProposal(target ? { ...next, project_id: target.project, read_only: proposal?.read_only } : next);
    if (newlyAdded) void Promise.resolve(onAdded()).then(() => reload());
  };

  // Members this proposal added are shown as additions, not as part of the team they joined.
  const current = useMemo(() => {
    const addedHere = new Set((proposal?.members ?? []).filter(m => m.status === 'added').map(m => m.id));
    return (context?.profiles ?? []).filter(p => !p.archived && !addedHere.has(p.id));
  }, [context, proposal]);

  const targetedHeader = target && <header className="bd-header">
    <div><h1 ref={heading} className="bd-title" tabIndex={-1}>Team suggestion</h1>
      <p className="bd-muted">Viewing {viewedName} · Running project: {runningName}</p>
      <small className="bd-muted">Project {target.project} · Proposal {target.proposal}</small></div>
    <button type="button" className="bd-btn" onClick={onClose}>Back to team</button>
  </header>;
  if (target && (!context || !proposal || unavailable)) return <div className="tb-root">
    {targetedHeader}
    {error ? <><ErrorText message={error} /><button type="button" className="bd-btn" onClick={() => void reload()}>Retry this suggestion</button></> : <p role="status">Loading this suggestion...</p>}
  </div>;
  if (!context) {
    return <div className="tb-root">{error ? <ErrorText message={`Could not open the team builder: ${error}`} /> : <p className="bd-empty">Loading team builder...</p>}</div>;
  }

  const ready = proposal?.status === 'ready';
  const suggestion = proposal?.source === 'suggestion';
  const drafter = proposal?.drafter ? '@' + proposal.drafter : 'The drafter';
  const challenger = proposal?.challenger ? '@' + proposal.challenger : 'The challenger';
  const visible = proposal?.members ?? [];
  const pending = visible.filter(m => m.status !== 'added' && m.status !== 'declined').length;

  return (
    <div className="tb-root">
      {targetedHeader || <header className="bd-header">
        <div>
          <ol className="tb-steps" aria-label="Steps">
            <li aria-current={step === 'brief' ? 'step' : undefined}>1 Brief</li>
            <li aria-current={step === 'review' && pending > 0 ? 'step' : undefined}>2 Review team</li>
            <li aria-current={step === 'review' && !!proposal && pending === 0 ? 'step' : undefined}>3 Add members</li>
          </ol>
          <h1 className="bd-title" tabIndex={-1}>A team shaped around your project</h1>
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
      </header>}
      <ErrorText message={error} />
      {readOnly && <section className="bd-banner bd-banner-warn" aria-label="Read-only suggestion">
        <b>Read-only. Open this project to edit, approve or add its suggested member.</b>
        <span>Reading this suggestion does not switch or stop {runningName}.</span>
        <button type="button" className="bd-btn" onClick={() => leaveSuggestion(target!.project)}>View project opening options</button>
      </section>}

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
          <section className={`tb-brief${suggestion ? ' tb-suggestion-brief' : ''}`}>
            <div>
              <small className="tb-eyebrow">{suggestion ? `@${proposal.suggested_by || proposal.drafter} · AGENT-PROVIDED REASON` : `PROJECT BRIEF · ${(proposal.source && proposal.source !== 'suggestion' ? SOURCE_LABELS[proposal.source] : 'Brief').toUpperCase()}`}</small>
              <p>{proposal.brief}</p>
              <small className="bd-muted">{suggestion ? 'Suggestion only. You review the instructions, approve the profile, then choose Add.' : 'Keeps the current team · Drafting again creates a new proposal for approval'}</small>
            </div>
            {!suggestion && !target && <button type="button" className="bd-btn bd-btn-quiet" disabled={working} onClick={() => setStep('brief')}>
              Edit brief &amp; re-draft
            </button>}
          </section>

          {working && (
            <p className="tb-progress" role="status">
              <span className="tb-spinner" aria-hidden="true" />
              {proposal.status === 'drafting' ? `${drafter} is drafting the team from your brief...` : `${challenger} is challenging the draft for overlaps and gaps...`}
            </p>
          )}
          {proposal.status === 'failed' && !suggestion && !target && (
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
              {!suggestion && !readOnly && <Coverage proposal={proposal} current={current} />}
              <div className="tb-layout">
                <section className="tb-cards" aria-label="Proposed members">
                  {visible.length ? (
                    visible.map(m => (
                      <MemberCard key={m.member_id} proposal={proposal} member={m} providers={context.providers} locked={proposal.status !== 'ready' || readOnly} readOnly={readOnly} project={target?.project || running?.project_id} onRefusal={() => { if (target) void reload(); }} onChange={change} />
                    ))
                  ) : (
                    <p className="bd-empty">The agents found no gap worth a new member. Edit the brief to ask for a different team.</p>
                  )}
                </section>
                <Summary context={context} proposal={proposal} current={current} readOnly={readOnly} project={target?.project || running?.project_id} projectName={viewedName || runningName} onRefusal={() => { if (target) void reload(); }} onChange={change} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
