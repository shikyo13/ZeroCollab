/* Add or edit a provider connection (Team > Providers, provider_store.py). Subscription connections
 * have a name and color; a local OpenAI-compatible server also has its address, model and maximum
 * response length. The server checks the exact address and model against its model list on save. */
import { useEffect, useRef, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';
import { ErrorText, Field } from '../board/shared';

export type ProviderKind = 'codex' | 'claude' | 'antigravity' | 'api';
/** GET /api/runtime/providers row. source: config (config.toml), edited (config.toml, changed here) or app (added here). */
export type ProviderDef = {
  id: string; provider: ProviderKind; label: string; color: string; source: 'config' | 'edited' | 'app';
  base_url: string; model: string; max_tokens: number | null;
  /** S4: approximate context window, where it came from, and the last tool-call check. */
  context_tokens?: number | null; context_source?: string;
  tool_check?: { ok: boolean; detail: string; base_url: string; model: string; checked: number } | null;
  /** A local model run inside Codex (provider codex), and what its sandbox does (provider_store.local_codex_limits). */
  local?: boolean; limits?: string;
};
export type ProviderDefs = {
  providers: ProviderDef[]; removed: { id: string; label: string }[]; error: string;
  available: { provider: ProviderKind; label: string }[];
};
export type FormRequest =
  | { mode: 'create'; provider: ProviderKind; label: string; base_url?: string; runner?: Runner }
  | { mode: 'edit'; def: ProviderDef };

/** Who runs a local model: the room's own small loop (file tools, no shell), or Codex with its own tools. */
type Runner = 'room' | 'codex';
type Probe = { reachable: boolean; detail?: string; models?: string[]; model_count?: number };
/** POST /providers/check: nothing is saved. */
type Check = { tool_check: { ok: boolean; detail: string }; context: { tokens: number | null; source: string }; budget_note: string; fits: boolean };
type Saved = { budget_note?: string };

const DEFAULT_OUTPUT = 1024;
const OUTPUT_RANGE = [256, 32768] as const;
/** provider_store.CONTEXT_TOKENS */
export const CONTEXT_RANGE = [2048, 2_000_000] as const;

export function ProviderForm({ request, onClose, onSaved }: { request: FormRequest; onClose: () => void; onSaved: (note: string) => void }) {
  const editing = request.mode === 'edit' ? request.def : null;
  const created = request.mode === 'create' ? request : null;
  // A saved connection keeps its runner; a new local model chooses one (provider_store refuses a switch).
  const [runner, setRunner] = useState<Runner>(editing ? (editing.local ? 'codex' : 'room') : created?.runner ?? 'room');
  const serverForm = editing ? editing.provider === 'api' || !!editing.local : created!.provider === 'api';
  const viaCodex = serverForm && runner === 'codex';
  const kind: ProviderKind = editing ? editing.provider : viaCodex ? 'codex' : created!.provider;
  const local = serverForm;
  const [label, setLabel] = useState(editing?.label ?? (request.mode === 'create' ? request.label : ''));
  const [color, setColor] = useState(editing?.color ?? '#8b5cf6');
  const [baseUrl, setBaseUrl] = useState(editing?.base_url ?? (request.mode === 'create' ? request.base_url ?? '' : ''));
  const [model, setModel] = useState(editing?.model ?? '');
  const [output, setOutput] = useState(String(editing?.max_tokens ?? DEFAULT_OUTPUT));
  // Empty means: read it from the server when the connection is checked or saved.
  const [contextText, setContextText] = useState(editing?.context_tokens ? String(editing.context_tokens) : '');
  const [check, setCheck] = useState<Check | null>(null);
  const [checking, setChecking] = useState(false);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [probing, setProbing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const probedFor = useRef('');

  useEffect(() => {
    const node = dialog.current;
    if (node && !node.open) node.showModal();
    return () => node?.close();
  }, []);

  const loadModels = async (address = baseUrl) => {
    if (!address.trim()) return;
    probedFor.current = address.trim();
    setProbing(true);
    setError('');
    try {
      // Editing an API key connection lists its models with the saved key (checked against its server).
      const result = await runtime<Probe>('/providers/probe', { base_url: address.trim(), id: editing?.id });
      if (probedFor.current !== address.trim()) return;  // the address changed while this ran
      setProbe(result);
      if (result.reachable && result.models?.length && !result.models.includes(model)) setModel(result.models[0]);
    } catch (e) {
      setProbe(null);
      setError(errorMessage(e));
    } finally {
      setProbing(false);
    }
  };
  // A detected or saved server lists its models straight away.
  useEffect(() => { if (local && baseUrl) void loadModels(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const outputValue = Number(output);
  const outputValid = Number.isInteger(outputValue) && outputValue >= OUTPUT_RANGE[0] && outputValue <= OUTPUT_RANGE[1];
  const served = probe?.reachable ? probe.models ?? [] : [];
  const choices = model && !served.includes(model) ? [model, ...served] : served;
  const contextValue = contextText.trim() ? Number(contextText) : null;
  const contextValid = contextValue === null || (Number.isInteger(contextValue) && contextValue >= CONTEXT_RANGE[0] && contextValue <= CONTEXT_RANGE[1]);
  const ready = label.trim() && (!local || (baseUrl.trim() && model && (viaCodex || outputValid) && contextValid));
  // A check answers for one address, model and length; any change makes it stale.
  const checkKey = `${runner}|${baseUrl.trim()}|${model}|${outputValue}|${contextValue}`;
  const checkedKey = useRef('');
  const shownCheck = check && checkedKey.current === checkKey ? check : null;

  const runCheck = async () => {
    setChecking(true);
    setError('');
    const key = checkKey;
    try {
      const result = await runtime<Check>('/providers/check', viaCodex
        ? { local: true, base_url: baseUrl.trim(), model, context_tokens: contextValue, id: editing?.id }
        : { base_url: baseUrl.trim(), model, max_tokens: outputValue, context_tokens: contextValue, id: editing?.id });
      checkedKey.current = key;
      setCheck(result);
    } catch (e) {
      setCheck(null);
      setError(errorMessage(e));
    } finally {
      setChecking(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setError('');
    const body: Record<string, unknown> = { label: label.trim(), color };
    if (viaCodex) Object.assign(body, { local: true, base_url: baseUrl.trim(), model, context_tokens: contextValue });
    else if (local) Object.assign(body, { base_url: baseUrl.trim(), model, max_tokens: outputValue, context_tokens: contextValue });
    try {
      const saved = editing ? await runtime<Saved>(`/providers/${encodeURIComponent(editing.id)}`, body)
        : await runtime<Saved>('/providers', { ...body, provider: kind });
      const done = editing ? `Saved ${label.trim()}.` : `Added ${label.trim()}. Choose it in Add agent.`;
      onSaved(saved?.budget_note ? `${done} ${saved.budget_note}.` : done);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const title = editing ? `Edit ${editing.label}` : local ? 'Add local model' : `Connect ${label || 'provider'}`;
  return (
    <dialog ref={dialog} className="pv-dialog" aria-labelledby="pv-form-title" onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}>
      <form className="pv-form" method="dialog" onSubmit={e => { e.preventDefault(); if (ready && !busy) void save(); }}>
        <div className="pv-head">
          <h2 id="pv-form-title">{title}</h2>
          <button type="button" className="bd-btn bd-btn-quiet" aria-label="Close" disabled={busy} onClick={onClose}>✕</button>
        </div>
        <p className="pv-note">
          {local
            ? 'Saved in this room and offered in Add agent at once. No restart and no config.toml edit.'
            : 'Agents on this connection use the official CLI and its own sign-in. Sign in from the Subscriptions list.'}
          {editing?.source === 'config' && ' Changes are saved over config.toml; Reset restores its values.'}
        </p>
        {local && (
          <fieldset className="pv-runner">
            <legend>Runs with</legend>
            <label><input type="radio" name="pv-runner" checked={runner === 'room'} disabled={!!editing} onChange={() => setRunner('room')} />
              <span><strong>ZeroCollab's runner</strong><small className="bd-muted">File tools only, no shell. Works with servers on this computer or your network.</small></span></label>
            <label><input type="radio" name="pv-runner" checked={runner === 'codex'} disabled={!!editing} onChange={() => setRunner('codex')} />
              <span><strong>Codex</strong><small className="bd-muted">Shell, file edits and tests. Can read files on this computer, writes only in the agent's folder and asks before changes elsewhere. On Windows its network access is not blocked. Server on this computer only.</small></span></label>
            {editing && <small className="bd-muted">A connection keeps its runner. To change it, add a new connection.</small>}
          </fieldset>
        )}
        <Field label="Name" hint="Shown on the Team page and on each agent that uses it">
          <input value={label} maxLength={60} onChange={e => setLabel(e.target.value)} required autoFocus />
        </Field>
        <Field label="Color">
          <span className="tm-colors"><input type="color" value={color} onChange={e => setColor(e.target.value)} /><code>{color}</code></span>
        </Field>
        {local && <>
          <Field label="Server address" hint={viaCodex
            ? 'On this computer only, as http://127.0.0.1:port/v1. Needs the Responses API and a tool-capable model, for example llama.cpp llama-server --jinja.'
            : 'An OpenAI-compatible endpoint, on this computer or your network'}>
            <span className="pv-inline">
              <input className="bd-mono" value={baseUrl} placeholder="http://127.0.0.1:11434/v1" required
                onChange={e => { setBaseUrl(e.target.value); setProbe(null); }} onBlur={() => { if (baseUrl.trim() !== probedFor.current) void loadModels(); }} />
              <button type="button" className="bd-btn" disabled={probing || !baseUrl.trim()} onClick={() => void loadModels()}>
                {probing ? 'Checking…' : 'Load models'}
              </button>
            </span>
          </Field>
          <Field label="Model" hint={probe ? (probe.reachable ? `${probe.model_count ?? served.length} models served by this server` : probe.detail || 'Not reachable') : 'Load the server\'s models to choose one'}>
            <select value={model} onChange={e => setModel(e.target.value)} disabled={!choices.length}>
              {!choices.length && <option value="">No models loaded</option>}
              {choices.map(m => <option key={m} value={m}>{served.includes(m) ? m : `${m} (not served now)`}</option>)}
            </select>
          </Field>
          {!viaCodex && <Field label="Maximum response length (tokens)" hint={`${OUTPUT_RANGE[0]} to ${OUTPUT_RANGE[1]}. Longer answers stop at this length.`}>
            <input inputMode="numeric" value={output} aria-invalid={!outputValid} onChange={e => setOutput(e.target.value.replace(/[^0-9]/g, ''))} />
          </Field>}
          <Field label="Context window (tokens, approximate)"
            hint={contextText.trim() ? (editing?.context_source && Number(contextText) === editing.context_tokens ? `From ${editing.context_source}` : 'Entered by you')
              : viaCodex ? "Leave empty to read it from the server. Codex's own instructions and tools use about 16k tokens, so at least 24,576."
              : 'Leave empty to read it from the server. The room fits its prompt inside this window.'}>
            <input inputMode="numeric" value={contextText} placeholder="Read from the server" aria-invalid={!contextValid}
              onChange={e => setContextText(e.target.value.replace(/[^0-9]/g, ''))} />
          </Field>
          <div className="pv-check" aria-live="polite">
            <div className="pv-inline">
              <strong>Check</strong>
              <span className="tm-spacer" />
              <button type="button" className="bd-btn" disabled={checking || busy || !baseUrl.trim() || !model || (!viaCodex && !outputValid) || !contextValid} onClick={() => void runCheck()}>
                {checking ? 'Checking the server…' : 'Check server'}
              </button>
            </div>
            {shownCheck ? <>
              <p><span className={`tm-state tm-tone-${shownCheck.tool_check.ok ? 'ok' : 'danger'}`}>{shownCheck.tool_check.ok ? 'Passed' : 'Failed'}</span> {shownCheck.tool_check.detail}</p>
              <p className="bd-muted">Server reports: {shownCheck.context.tokens ? `about ${shownCheck.context.tokens.toLocaleString()} tokens of context (${shownCheck.context.source})` : `context ${shownCheck.context.source}`}
                {contextValue && shownCheck.context.tokens && contextValue !== shownCheck.context.tokens ? `. Your entry of ${contextValue.toLocaleString()} is used.` : ''}</p>
              <p className={shownCheck.fits ? 'bd-muted' : 'bd-error'}>{shownCheck.budget_note}</p>
            </> : <p className="bd-muted">{viaCodex
              ? 'Saving runs this check on the exact address and model: one tool call through the Responses API, as Codex sends it, and a context window with room for Codex. Check first to see the result without saving.'
              : 'Saving runs this check on the exact address and model: agents need structured tool calls, and the room must fit the context window. Check first to see the result without saving.'}</p>}
          </div>
        </>}
        <ErrorText message={error} />
        <div className="pv-form-actions">
          <button type="button" className="bd-btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" className="bd-btn bd-btn-primary" disabled={!ready || busy}>
            {busy ? (local ? 'Checking the server…' : 'Checking the CLI…') : editing ? 'Save' : local ? 'Add provider' : 'Connect'}
          </button>
        </div>
      </form>
    </dialog>
  );
}
