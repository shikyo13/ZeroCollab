/* Optional spec, project memory, execution limits and conversation budgets.
 * Same endpoints and limit ranges as the legacy static/project.js panel. */
import { useEffect, useState, type FormEvent } from 'react';
import { runtime } from '../lib/api';
import { sendChatMessage, sessionTitle } from '../lib/room';
import type { Chain, Limits, ProjectState } from '../lib/types';
import { ErrorText, Field, RelativeTime, useAction, type Agents, displayName } from './shared';
import { LIMITS } from '../lib/constants';

export function SpecPanel({ project, channel, refresh }: { project: ProjectState; channel: string; refresh: () => Promise<void> }) {
  const { run, busy, error } = useAction();
  const [text, setText] = useState(project.spec);
  const [dirty, setDirty] = useState(false);
  const [state, setState] = useState(project.spec ? 'Saved' : '');
  // Live updates only overwrite the editor when the user has no unsaved edits.
  useEffect(() => {
    if (!dirty) {
      setText(project.spec);
      setState(project.spec ? 'Saved' : '');
    }
  }, [project.spec, dirty]);

  const save = () =>
    run(async () => {
      await runtime('/spec', { text });
      setDirty(false);
      setState(text.trim() ? 'Saved' : 'Cleared');
      await refresh();
    });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void save();
  };

  const discuss = () =>
    run(async () => {
      if (!text.trim()) throw new Error('Write a spec before asking the team to discuss it.');
      // Validate the socket first so we never save-and-silently-not-send.
      if (!window.ws || window.ws.readyState !== WebSocket.OPEN) throw new Error('Reconnect to the room before starting discussion.');
      await runtime('/spec', { text });
      setDirty(false);
      sendChatMessage('@all ' + text, channel);
      setState(`Saved and posted to ${sessionTitle(channel)}`);
      await refresh();
    });

  const useLast = () =>
    run(async () => {
      const latest = await runtime<ProjectState>('/project-state');
      setText(latest.last_human_message || '');
      setDirty(true);
      setState(latest.last_human_message ? 'Review and save' : 'No human message found');
    });

  return (
    <section className="bd-panel" aria-labelledby="bd-spec-heading">
      <h3 id="bd-spec-heading">Shared spec (optional)</h3>
      <p className="bd-muted">
        Goal, constraints and done criteria. Chat and tasks work without a spec. Agents read it on demand; unchanged specs are not resent.
      </p>
      <form className="bd-form" onSubmit={onSubmit}>
        <textarea
          aria-label="Shared project spec"
          className="bd-spec"
          rows={12}
          maxLength={LIMITS.spec}
          value={text}
          placeholder="Paste or write the spec for this project..."
          onChange={e => {
            setText(e.target.value);
            setDirty(true);
            setState('Unsaved');
          }}
        />
        <div className="bd-actions">
          <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !dirty}>
            Save spec
          </button>
          <button type="button" className="bd-btn" disabled={busy} onClick={() => void discuss()} title={`Save, then post it to ${sessionTitle(channel)} addressed to @all`}>
            Save and discuss in {sessionTitle(channel)}
          </button>
          <button type="button" className="bd-btn bd-btn-quiet" disabled={busy} onClick={() => void useLast()}>
            Use my last chat message
          </button>
          <span className="bd-muted" role="status">
            {state} {text.length > 0 && `· ${text.length.toLocaleString()} / ${LIMITS.spec.toLocaleString()}`}
          </span>
        </div>
        <ErrorText message={error} />
      </form>
    </section>
  );
}

export function MemoryPanel({ project, agents }: { project: ProjectState; agents: Agents }) {
  const [kind, setKind] = useState('');
  const notes = [...project.notes].reverse().filter(n => !kind || n.kind === kind);
  const kinds = [...new Set(project.notes.map(n => n.kind))];
  return (
    <section className="bd-panel" aria-labelledby="bd-memory-heading">
      <div className="bd-panel-head">
        <h3 id="bd-memory-heading">Project memory</h3>
        {kinds.length > 1 && (
          <select aria-label="Filter notes by kind" value={kind} onChange={e => setKind(e.target.value)}>
            <option value="">All notes</option>
            {kinds.map(k => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        )}
      </div>
      <p className="bd-muted">
        Durable progress, decision, reference, blocker and handoff notes left by the team. Decisions and references are kept
        permanently; superseded or resolved ones stay here for the record but no longer reach agents.
      </p>
      {notes.length ? (
        <ol className="bd-notes">
          {notes.map((n, i) => (
            <li key={`${n.time ?? i}-${i}`} className={`bd-note bd-note-${n.kind}${n.status && n.status !== 'active' ? ' bd-note-retired' : ''}`}>
              <div className="bd-history-head">
                <span className="bd-pill">{n.kind}</span>
                {n.status && n.status !== 'active' && (
                  <span className="bd-pill" title={n.replaced_by ? `Replaced by note ${n.replaced_by}` : undefined}>
                    {n.status}
                  </span>
                )}
                <strong>{displayName(n.by, agents)}</strong>
                {n.time && (
                  <RelativeTime className="bd-muted" time={n.time} />
                )}
              </div>
              <p>{n.text}</p>
            </li>
          ))}
        </ol>
      ) : (
        <p className="bd-empty">No notes yet.</p>
      )}
    </section>
  );
}

const LIMIT_FIELDS: [keyof Limits, string, number, number, string][] = [
  ['parallel_agents', 'Agents running together', 0, 64, '0 = whole team'],
  ['input_chars', 'Shared context (characters)', 4000, 48000, 'Excludes native instructions and tools'],
  ['agent_turns', 'Agent-turn cap', 0, 32, '0 = off'],
  ['turn_seconds', 'Idle seconds per turn', 0, 3600, '0 = off'],
  ['turn_max_seconds', 'Total seconds per turn', 0, 14400, '0 = off; at least idle seconds when both set'],
  ['native_tool_calls', 'Native-tool cap', 0, 64, '0 = off'],
  ['local_tool_rounds', 'Local tool rounds per turn', 0, 4, 'Local models only'],
  ['reported_token_budget', 'Reported-token cap', 0, 200000, '0 = off'],
];

function ChainSummary({ chain, limits }: { chain: Chain; limits: Limits }) {
  return (
    <dl className="bd-stats">
      <div>
        <dt>Agent turns</dt>
        <dd>
          {chain.turns}
          {limits.agent_turns ? ` / ${limits.agent_turns}` : ''}
        </dd>
      </div>
      <div>
        <dt>Chat handoffs</dt>
        <dd>
          {chain.handoffs || 0}
        </dd>
      </div>
      <div>
        <dt>Reported tokens</dt>
        <dd>
          {chain.reported_tokens.toLocaleString()}
          {limits.reported_token_budget ? ` / ${limits.reported_token_budget.toLocaleString()}` : ''}
        </dd>
      </div>
      <div>
        <dt>Shared context sent</dt>
        <dd>{chain.input_chars.toLocaleString()} chars</dd>
      </div>
    </dl>
  );
}

export function LimitsPanel({ project, channel, refresh }: { project: ProjectState; channel: string; refresh: () => Promise<void> }) {
  const { run, busy, error } = useAction();
  const [values, setValues] = useState<Limits>(project.limits);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (!dirty) setValues(project.limits);
  }, [project.limits, dirty]);
  const chain = [...(project.chains ?? [])].reverse().find(c => c.channel === channel);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await run(async () => {
      await runtime('/limits', values);
      await refresh();
    });
    if (ok) {
      setDirty(false);
      setSaved(true);
    }
  };

  return (
    <>
      <section className="bd-panel" aria-labelledby="bd-budget-heading">
        <h3 id="bd-budget-heading">Current conversation in {sessionTitle(channel)}</h3>
        {chain ? (
          <>
            <ChainSummary chain={chain} limits={project.limits} />
            {chain.paused && <p className="bd-banner bd-banner-warn">Paused by a limit. Send a new instruction in chat to continue.</p>}
          </>
        ) : (
          <p className="bd-empty">No agent work started in this channel yet.</p>
        )}
      </section>
      <section className="bd-panel" aria-labelledby="bd-limits-heading">
        <h3 id="bd-limits-heading">Execution controls (optional)</h3>
        <p className="bd-muted">Zero disables optional cutoffs and lets the whole team run together.</p>
        <form
          className="bd-form"
          onSubmit={submit}
          onChange={() => {
            setDirty(true);
            setSaved(false);
          }}
        >
          <div className="bd-limits-grid">
            {LIMIT_FIELDS.map(([name, label, min, max, hint]) => (
              <Field key={name} label={label} hint={`${hint} (${min.toLocaleString()}-${max.toLocaleString()})`}>
                <input
                  type="number"
                  min={min}
                  max={max}
                  step={1}
                  required
                  value={Number.isFinite(values[name]) ? values[name] : ''}
                  onChange={e => setValues(v => ({ ...v, [name]: e.target.value === '' ? Number.NaN : Math.trunc(Number(e.target.value)) }))}
                />
              </Field>
            ))}
          </div>
          <div className="bd-actions">
            <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !dirty}>
              Save controls
            </button>
            {dirty && (
              <button
                type="button"
                className="bd-btn bd-btn-quiet"
                onClick={() => {
                  setValues(project.limits);
                  setDirty(false);
                }}
              >
                Discard changes
              </button>
            )}
            {saved && !dirty && (
              <span className="bd-muted" role="status">
                Saved
              </span>
            )}
          </div>
          <ErrorText message={error} />
        </form>
      </section>
    </>
  );
}
