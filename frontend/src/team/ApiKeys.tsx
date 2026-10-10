/* Team > Providers, API keys (Docs/providers.md S5a, provider_store.py key routes). Keys are for
 * OpenAI-compatible servers the room calls itself, such as OpenRouter. A key is checked before it is
 * saved, kept only in the operating system's credential store and never shown again; this page knows
 * at most its last four characters. With no key saved, the panel is one button and one line. */
import { useEffect, useId, useRef, useState } from 'react';
import { ApiError, errorMessage, runtime } from '../lib/api';
import { CONTEXT_RANGE, type ProviderDef } from './ProviderForm';

type Bucket = { cost: number; complete: boolean; input: number; cached: number; output: number };
export type KeyUsage = { today: Bucket; month: Bucket; agents: Record<string, Bucket>; incomplete: string[] };
export type Price = { input?: number; cached?: number; output?: number };
/** The key fields GET /api/runtime/providers adds to a keyed row. */
export type KeyedDef = ProviderDef & { keyed: true; key_hint: string; enabled: boolean; prices: Record<string, Price>; origin: string; usage: KeyUsage };
export type ServerState = { reachable: boolean; detail?: string; model_count?: number };

export const isKeyed = (def: ProviderDef | undefined): def is KeyedDef => !!def && (def as Partial<KeyedDef>).keyed === true;

const tokens = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n);
// A cost below a cent is still a cost: "<$0.01", never "$0.00".
const dollars = (n: number) => n > 0 && n < 0.005 ? '<$0.01' : `$${n < 10 ? n.toFixed(2) : n.toFixed(0)}`;
/** Missing usage or prices never count as $0: a partial total says "at least", none at all says unavailable. */
function estimate(b: Bucket): string {
  if (b.complete) return dollars(b.cost);
  return b.cost > 0 ? `at least ${dollars(b.cost)}` : 'unavailable';
}

/** Restores focus to whatever opened the dialog once it closes (Cancel, Escape or a successful save).
 * The first saved key replaces the no-key panel and its button, so focus then moves to the API keys
 * heading instead of falling to the page. */
function useModal() {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const node = dialog.current;
    if (node && !node.open) node.showModal();
    return () => {
      node?.close();
      // After the save re-renders the panel: the opener may be gone by then.
      window.setTimeout(() => (opener?.isConnected ? opener : document.getElementById('pv-keys'))?.focus(), 0);
    };
  }, []);
  return dialog;
}

function originOf(address: string): string {
  try { return new URL(address.trim()).origin; } catch { return ''; }
}

type KeyFormRequest = { mode: 'create' } | { mode: 'replace'; def: KeyedDef };
/** Where a refusal is shown: next to the field the server named, or for the whole form. A context
 * window the server can't report is not a key problem, so it never marks the key invalid. */
type FormError = { message: string; field: 'key' | 'model' | 'context_tokens' | 'form' };

function formError(e: unknown, fallback: FormError['field']): FormError {
  const field = e instanceof ApiError ? e.field : undefined;
  return { message: errorMessage(e), field: field === 'key' || field === 'model' || field === 'context_tokens' ? field : fallback };
}

export function ApiKeyForm({ request, onClose, onSaved }: { request: KeyFormRequest; onClose: () => void; onSaved: (note: string) => void }) {
  const replacing = request.mode === 'replace' ? request.def : null;
  const dialog = useModal();
  const ids = useId();
  const [label, setLabel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [key, setKey] = useState('');
  const [models, setModels] = useState<string[] | null>(null);
  const [model, setModel] = useState('');
  const [warning, setWarning] = useState('');
  const [status, setStatus] = useState('');
  // Optional: blank lets the save read the window from the server; entered when the server can't report it.
  const [contextText, setContextText] = useState('');
  const [error, setError] = useState<FormError | null>(null);
  const [busy, setBusy] = useState(false);
  const keyFor = useRef('');  // the origin the typed key was entered for
  const contextInput = useRef<HTMLInputElement>(null);
  const contextValue = contextText ? Number(contextText) : null;
  const contextValid = contextValue === null || (Number.isInteger(contextValue) && contextValue >= CONTEXT_RANGE[0] && contextValue <= CONTEXT_RANGE[1]);

  const changeAddress = (value: string) => {
    setBaseUrl(value);
    if (key && originOf(value) !== keyFor.current) {
      // A key belongs to one server: a different address clears it rather than sending it there.
      setKey(''); setModels(null); setWarning('');
      setStatus('Key cleared because the server address changed.');
    }
  };
  const changeKey = (value: string) => {
    setKey(value); setModels(null); setError(null);
    keyFor.current = originOf(baseUrl);
  };
  const changeContext = (value: string) => {
    setContextText(value.replace(/[^0-9]/g, ''));
    if (error?.field === 'context_tokens') setError(null);
  };

  const checkKey = async () => {
    setBusy(true); setError(null); setStatus('Checking key…');
    try {
      const result = await runtime<{ models: string[]; warning: string }>('/providers/keys/probe', { base_url: baseUrl.trim(), key });
      setModels(result.models);
      setModel(m => result.models.includes(m) ? m : result.models[0]);
      setWarning(result.warning);
      setStatus(`Key accepted: ${result.models.length} models available. Choose one, then Check and save.`);
    } catch (e) {
      // Listing models only tests the address and key, so an unnamed failure is shown on the key.
      setModels(null); setStatus(''); setError(formError(e, 'key'));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true); setError(null); setStatus(replacing ? 'Checking key…' : 'Checking key and tool calls…');
    try {
      if (replacing) {
        await runtime(`/providers/keys/${encodeURIComponent(replacing.id)}`, { key });
        setKey('');
        onSaved(`Replaced the key for ${replacing.label}.`);
      } else {
        const body = { label: label.trim(), base_url: baseUrl.trim(), model, key, ...(contextValue === null ? {} : { context_tokens: contextValue }) };
        const saved = await runtime<{ budget_note?: string }>('/providers/keys', body);
        setKey('');
        onSaved(`Added ${label.trim()}. Choose it in Add agent.${saved?.budget_note ? ` ${saved.budget_note}.` : ''}`);
      }
    } catch (e) {
      // Nothing was saved and every field keeps its value; Check and save stays the explicit retry.
      const failed = formError(e, replacing ? 'key' : 'form');
      setStatus(''); setError(failed);
      if (failed.field === 'context_tokens') window.setTimeout(() => contextInput.current?.focus(), 0);
    } finally {
      setBusy(false);
    }
  };

  const keyReady = key.length >= 8 && (replacing || originOf(baseUrl));
  const ready = keyReady && (replacing || (label.trim() && models && model && contextValid));
  const errorId = `${ids}-error`;
  const contextHintId = `${ids}-context-hint`;
  const contextInvalid = !contextValid || error?.field === 'context_tokens';
  // One message, placed under the field it belongs to; role=alert announces it wherever it lands.
  const errorAt = (field: FormError['field']) => error?.field === field && <p id={errorId} className="bd-error" role="alert">{error.message}</p>;
  const title = replacing ? `Replace the key for ${replacing.label}` : 'Add an API key';
  return (
    <dialog ref={dialog} className="pv-dialog" aria-labelledby={`${ids}-title`} onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}>
      <form className="pv-form" onSubmit={e => { e.preventDefault(); if (ready && !busy) void save(); }}>
        <div className="pv-head">
          <h2 id={`${ids}-title`}>{title}</h2>
          <button type="button" className="bd-btn bd-btn-quiet" aria-label="Close" disabled={busy} onClick={onClose}>✕</button>
        </div>
        <p className="pv-note">
          {replacing
            ? `Saved keys can't be shown. The new key is checked with ${replacing.origin.replace(/:\d+$/, '')} first; if it fails, the previous key stays in use.`
            : 'For an OpenAI-compatible server that needs a key, such as OpenRouter. Usage is billed by that provider. The key is kept in your system\'s credential store and sent only to this server.'}
        </p>
        {!replacing && <>
          <label className="bd-field">
            <span className="bd-field-label">Name</span>
            <input value={label} maxLength={60} onChange={e => setLabel(e.target.value)} required autoFocus />
          </label>
          <label className="bd-field">
            <span className="bd-field-label">Server address</span>
            <input className="bd-mono" value={baseUrl} placeholder="https://openrouter.ai/api/v1" required onChange={e => changeAddress(e.target.value)} />
          </label>
        </>}
        {/* An explicit label: the Check key button sits beside the field, outside its accessible name. */}
        <div className="bd-field">
          <label className="bd-field-label" htmlFor={`${ids}-key`}>API key</label>
          <span className="pv-inline">
            <input id={`${ids}-key`} className="bd-mono" type="password" value={key} autoComplete="off" spellCheck={false} required autoFocus={!!replacing}
              aria-invalid={error?.field === 'key'} aria-describedby={error?.field === 'key' ? errorId : undefined} onChange={e => changeKey(e.target.value)} />
            {!replacing && (
              <button type="button" className="bd-btn" disabled={busy || !keyReady} onClick={() => void checkKey()}>{models ? 'Check again' : 'Check key'}</button>
            )}
          </span>
        </div>
        {errorAt('key')}
        {!replacing && models && <>
          <div className="bd-field">
            <label className="bd-field-label" htmlFor={`${ids}-model`}>Model</label>
            <select id={`${ids}-model`} value={model} aria-invalid={error?.field === 'model'} aria-describedby={error?.field === 'model' ? errorId : undefined}
              onChange={e => { setModel(e.target.value); if (error?.field === 'model') setError(null); }}>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          {errorAt('model')}
          <div className="bd-field">
            <label className="bd-field-label" htmlFor={`${ids}-context`}>Context window (tokens, optional)</label>
            <input id={`${ids}-context`} ref={contextInput} inputMode="numeric" value={contextText} placeholder="Read from the server"
              aria-invalid={contextInvalid} aria-describedby={error?.field === 'context_tokens' ? `${errorId} ${contextHintId}` : contextHintId}
              onChange={e => changeContext(e.target.value)} />
            <span id={contextHintId} className="pv-note">
              {contextValid
                ? 'Leave empty to read it from the server. If the server can\'t report it, enter the context it runs this model with.'
                : `Enter ${CONTEXT_RANGE[0].toLocaleString()} to ${CONTEXT_RANGE[1].toLocaleString()} tokens, or leave it empty.`}
            </span>
          </div>
          {errorAt('context_tokens')}
        </>}
        {errorAt('form')}
        {warning && <p className="bd-error" role="alert">{warning}</p>}
        <p className="pv-note" role="status" aria-live="polite">{status}</p>
        {!replacing && (
          <p className="pv-note">Check key lists the server's models (free). Check and save also runs one small tool-call request, billed to this key (a few hundred tokens).</p>
        )}
        <div className="pv-form-actions">
          <button type="button" className="bd-btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" className="bd-btn bd-btn-primary" disabled={!ready || busy} aria-disabled={!ready || busy}>
            {busy ? 'Checking key…' : replacing ? 'Check and replace' : 'Check and save'}
          </button>
        </div>
      </form>
    </dialog>
  );
}

/** Per-model prices in USD per million tokens; blank cached input is charged at the input price. */
function PriceEditor({ def, onDone }: { def: KeyedDef; onDone: (note: string, error?: boolean) => void }) {
  const models = Array.from(new Set([def.model, ...Object.keys(def.prices)]));
  const [rows, setRows] = useState<Record<string, Record<keyof Price, string>>>(() => Object.fromEntries(models.map(m => {
    const p = def.prices[m] || {};
    return [m, { input: p.input?.toString() ?? '', cached: p.cached?.toString() ?? '', output: p.output?.toString() ?? '' }];
  })));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const prices = Object.fromEntries(Object.entries(rows).map(([m, r]) => [m, Object.fromEntries(
      (Object.keys(r) as (keyof Price)[]).filter(k => r[k].trim()).map(k => [k, Number(r[k])]))]));
    try {
      await runtime(`/providers/keys/${encodeURIComponent(def.id)}`, { prices });
      onDone(`Saved prices for ${def.label}.`);
    } catch (e) {
      onDone(errorMessage(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="pv-prices">
      <table className="pv-usage">
        <thead><tr><th>Model</th><th>Input $/M</th><th>Cached input $/M</th><th>Output $/M</th></tr></thead>
        <tbody>{models.map(m => (
          <tr key={m}><td>{m}</td>{(['input', 'cached', 'output'] as const).map(k => (
            <td key={k}><input inputMode="decimal" aria-label={`${m} ${k === 'cached' ? 'cached input' : k} price, USD per million tokens`}
              value={rows[m][k]} onChange={e => setRows(old => ({ ...old, [m]: { ...old[m], [k]: e.target.value.replace(/[^0-9.]/g, '') } }))} /></td>
          ))}</tr>
        ))}</tbody>
      </table>
      <p className="pv-note">Take these from the provider's price list. Blank cached input is charged at the input price, so the estimate errs high.</p>
      <span className="pv-inline"><button type="button" className="bd-btn pv-small" disabled={busy} onClick={() => void save()}>Save prices</button>
        <button type="button" className="bd-btn bd-btn-quiet pv-small" disabled={busy} onClick={() => onDone('')}>Cancel</button></span>
    </div>
  );
}

function Usage({ usage }: { usage: KeyUsage }) {
  const agents = Object.entries(usage.agents);
  return <>
    <p className="pv-totals"><span className="bd-muted">Estimated cost</span> <span>Today <strong>{estimate(usage.today)}</strong></span>
      <span>This month <strong>{estimate(usage.month)}</strong></span></p>
    {agents.length > 0 && (
      <table className="pv-usage">
        <thead><tr><th>Agent</th><th>Input tokens</th><th>Cached tokens</th><th>Output tokens</th><th>Estimated cost</th></tr></thead>
        <tbody>{agents.map(([name, b]) => (
          <tr key={name}><td>{name}</td><td>{tokens(b.input)}</td><td>{b.cached ? tokens(b.cached) : '-'}</td><td>{tokens(b.output)}</td>
            <td className={b.complete ? '' : 'bd-muted'}>{estimate(b)}</td></tr>
        ))}</tbody>
      </table>
    )}
    <p className="pv-note">
      {usage.incomplete.length ? `Incomplete estimate: ${usage.incomplete.join('; ')}. ` : ''}
      This month, from token counts at the prices you entered. Your provider's bill is authoritative.
    </p>
  </>;
}

type PanelProps = {
  defs: KeyedDef[]; servers: Record<string, ServerState | undefined>; editable: boolean; changing: boolean;
  manage: (def: ProviderDef) => React.ReactNode; onChanged: (note: string, error?: boolean) => void;
};

export function ApiKeys({ defs, servers, editable, changing, manage, onChanged }: PanelProps) {
  const [form, setForm] = useState<KeyFormRequest | null>(null);
  const [pricing, setPricing] = useState('');
  const [switching, setSwitching] = useState('');

  const toggle = async (def: KeyedDef) => {
    setSwitching(def.id);
    try {
      await runtime(`/providers/keys/${encodeURIComponent(def.id)}`, { enabled: !def.enabled });
      onChanged(def.enabled ? `Turned off ${def.label}. Its key is kept; agents on it don't start.` : `Turned on ${def.label}.`);
    } catch (e) {
      onChanged(errorMessage(e), true);
    } finally {
      setSwitching('');
    }
  };

  const dialog = form && <ApiKeyForm request={form} onClose={() => setForm(null)} onSaved={note => { setForm(null); onChanged(note); }} />;
  const add = editable && <button type="button" className="bd-btn" onClick={() => setForm({ mode: 'create' })}>Add an API key</button>;

  if (!defs.length) {
    return (
      <section className="bd-panel pv-panel" aria-label="API keys">
        {dialog}
        <div className="pv-add pv-add-only">{add}
          <span className="pv-note">Optional. For servers that need a key, such as OpenRouter. Usage is billed by that provider.</span></div>
      </section>
    );
  }
  return (
    <section className="bd-panel pv-panel" aria-labelledby="pv-keys">
      {dialog}
      <div className="pv-head"><h2 id="pv-keys" tabIndex={-1}>API keys</h2><span className="pv-note">Billed by each provider, per token</span></div>
      <p className="pv-note">The room adds each key to its own requests and sends it only to that server. Saved keys can't be shown. To replace one, enter a new key.</p>
      <ul className="pv-list">
        {defs.map(def => {
          const server = servers[def.id];
          const state = !def.enabled ? { label: 'Turned off', tone: 'muted' } : server?.reachable ? { label: 'Key valid', tone: 'ok' }
            : { label: server ? 'Needs attention' : 'Checking…', tone: server ? 'danger' : 'muted' };
          return (
            <li key={def.id} className="pv-row">
              <div className="pv-name">
                <strong>{def.label}</strong>
                <span className={`tm-state tm-tone-${state.tone}`}>{state.label}</span>
                <span className="pv-manage">
                  <button type="button" role="switch" aria-checked={def.enabled} aria-label={`Use ${def.label} API`}
                    className="pv-switch" disabled={!editable || switching === def.id} onClick={() => void toggle(def)}>
                    <span className="pv-track" aria-hidden="true" />{def.enabled ? 'On' : 'Off'}
                  </button>
                  {editable && <button type="button" className="bd-btn pv-small" disabled={changing} onClick={() => setForm({ mode: 'replace', def })}>Replace key</button>}
                  {manage(def)}
                </span>
              </div>
              <p className="bd-muted">Key ending …{def.key_hint} · sent only to {def.origin.replace(/:443$/, '')} · model {def.model}</p>
              {!def.enabled && <p className="bd-muted">Off: no new requests, and agents on it show "API provider turned off". A request already running finishes. Agents are never moved to a subscription.</p>}
              {def.enabled && server && !server.reachable && <p className="bd-muted">{server.detail}</p>}
              <Usage usage={def.usage} />
              {pricing === def.id
                ? <PriceEditor def={def} onDone={(note, failed) => { if (!failed) setPricing(''); if (note) onChanged(note, failed); }} />
                : editable && <span><button type="button" className="bd-btn bd-btn-quiet pv-small" onClick={() => setPricing(def.id)}>Edit prices</button></span>}
            </li>
          );
        })}
      </ul>
      {editable && <div className="pv-add">{add}</div>}
    </section>
  );
}
