import { useEffect, useId, useState } from 'react';
import type { Agents } from '../board/shared';
import { readStored, writeStored } from '../lib/storage';
import type { Draft, Listing } from './model';

export default function Setup({project, listing, agents, busy, save}: {
  project: string; listing: Listing | null; agents: Agents; busy: boolean;
  save: (draft: Draft) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<Draft>(() => {
    try {const d = JSON.parse(readStored('decision.setup:' + project) || 'null');
      if (d && typeof d.question === 'string' && typeof d.criteria === 'string' && Array.isArray(d.participants)) return {...d, participants: d.participants.filter((p: unknown) => typeof p === 'string')};
    } catch { /* An unavailable local draft starts empty. */ }
    return {question: '', criteria: '', participants: [], lead: ''};
  });
  const [error, setError] = useState(''), id = useId();
  useEffect(() => {if (project) writeStored('decision.setup:' + project, JSON.stringify(draft));}, [project, draft]);
  const choose = (name: string, checked: boolean) => setDraft(d => {
    const participants = checked ? [...d.participants, name] : d.participants.filter(p => p !== name);
    return {...d, participants, lead: participants.includes(d.lead) ? d.lead : participants[0] || ''};
  });
  return <form className="decision-card decision-setup" aria-labelledby={id} aria-describedby={error ? id + '-error' : undefined} onSubmit={async e => {
    e.preventDefault(); const criteria = draft.criteria.split('\n').map(s => s.trim()).filter(Boolean);
    if (!draft.question.trim() || !criteria.length) {setError('Enter a question and at least one criterion.'); return;}
    if (criteria.length > 8 || criteria.some(s => [...s].length > 300)) {setError('Use at most eight criteria, each up to 300 characters.'); return;}
    if (draft.participants.length < 2 || draft.participants.length > 8) {setError('Choose two to eight available participants.'); return;}
    if (draft.participants.some(p => !listing?.available.includes(p))) {setError('A selected participant is unavailable. Update the selection before saving.'); return;}
    setError(''); await save(draft);
  }}>
    <h2 id={id}>Question and criteria</h2><p>Save the draft first. No agent work is queued until you start the discussion.</p>
    <label htmlFor={id + '-question'}>Question</label><textarea id={id + '-question'} value={draft.question} maxLength={2000} required onChange={e => setDraft(d => ({...d, question: e.target.value}))} />
    <label htmlFor={id + '-criteria'}>Decision criteria</label><p id={id + '-criteria-help'} className="decision-muted">One per line, up to eight</p><textarea id={id + '-criteria'} aria-describedby={id + '-criteria-help'} value={draft.criteria} required onChange={e => setDraft(d => ({...d, criteria: e.target.value}))} />
    <fieldset><legend>Participants · choose two to eight</legend>
      {Object.entries(agents).filter(([,a]) => !a.archived).map(([name,a]) => {
        const available = !!listing?.available.includes(name);
        return <label key={name} className="decision-choice"><input type="checkbox" checked={draft.participants.includes(name)} disabled={busy || (!available && !draft.participants.includes(name)) || (draft.participants.length >= 8 && !draft.participants.includes(name))} onChange={e => choose(name,e.target.checked)} />{a.label || name}{!available && <small>Unavailable</small>}</label>;
      })}
    </fieldset>
    <label htmlFor={id + '-lead'}>Proposal lead</label><select id={id + '-lead'} value={draft.lead} disabled={!draft.participants.length} onChange={e => setDraft(d => ({...d, lead: e.target.value}))}>
      {!draft.participants.length && <option value="">Choose participants first</option>}{draft.participants.map(p => <option key={p} value={p}>{agents[p]?.label || p}</option>)}
    </select>
    <p className="decision-muted">Your entered draft stays here when you navigate away. Saving fixes its question and participants; later proposal edits require new verdicts.</p>
    {error && <p id={id + '-error'} role="alert" className="decision-error">{error}</p>}
    <button className="bd-btn bd-btn-primary" disabled={busy || !listing}>{busy ? 'Saving...' : 'Save draft'}</button>
  </form>;
}
