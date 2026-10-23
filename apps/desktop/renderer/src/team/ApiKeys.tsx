/* Team > Providers, API keys (Docs/providers.md S5a, provider_store.py key routes). Keys are for
 * OpenAI-compatible servers the room calls itself, such as OpenRouter. A key is checked before it is
 * saved, kept only in the operating system's credential store and never shown again; this page knows
 * at most its last four characters. With no key saved, the panel is one button and one line. */
import { useEffect, useId, useRef, useState } from 'react';
import { ApiError, errorMessage, runtime } from '../lib/api';
import { CONTEXT_RANGE, type ProviderDef } from './ProviderForm';

type Bucket = { cost: number; complete: boolean; input: number; cached: number; output: number };
export type KeyUsage = { today: Bucket; month: Bucket; agents: Record<string, Bucket>; incomplete: string[]; models: string[] };
export type Price = { input?: number; cached?: number; output?: number };
/** The key fields GET /api/runtime/providers adds to a keyed row. */
export type KeyedDef = ProviderDef & { keyed: true; vendor: string; key_hint: string; enabled: boolean; prices: Record<string, Price>; origin: string; usage: KeyUsage;
  /** S5c: monthly spending limit in USD (estimated), or null for none. */
  limit: number | null; limit_problem: string;
  /** S5b: the vendor's listed prices for Claude (Anthropic) and Codex (OpenAI) keys; null for other servers. */
  builtin_prices: BuiltinPrices | null };
export type BuiltinPrices = { vendor: string; source: string; as_of: string; models: Record<string, Price> };
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
/** Which server a new key is for: an OpenAI-compatible server the room calls itself, or a vendor key
 * that Claude or Codex agents use through the room relay (S5b; fixed address, no billed check). */
type KeyKind = 'api' | 'claude' | 'codex';
const KINDS: { value: KeyKind; label: string; vendor: string; agents: string }[] = [
  { value: 'api', label: 'OpenAI-compatible server (OpenRouter, vLLM and others)', vendor: '', agents: '' },
  { value: 'claude', label: 'Anthropic API, for Claude agents', vendor: 'Anthropic', agents: 'Claude' },
  { value: 'codex', label: 'OpenAI API, for Codex agents', vendor: 'OpenAI', agents: 'Codex' },
];
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
  const [kind, setKind] = useState<KeyKind>('api');
  const vendor = KINDS.find(k => k.value === kind)!;
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
  const changeKind = (value: KeyKind) => {
    // A key belongs to one server: choosing another clears it rather than sending it there.
    setKind(value); setModels(null); setWarning(''); setError(null);
    if (key) { setKey(''); setStatus('Key cleared because the provider changed.'); }
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
      const result = await runtime<{ models: string[]; warning: string }>('/providers/keys/probe',
        kind === 'api' ? { base_url: baseUrl.trim(), key } : { provider: kind, key });
      setModels(result.models);
      setModel(m => result.models.includes(m) ? m : result.models[0]);
      setWarning(result.warning);
      const count = `${result.models.length} model${result.models.length === 1 ? '' : 's'} available`;
      setStatus(kind === 'api' ? `Key accepted: ${count}. Choose one, then Check and save.`
        : `Key accepted: ${count}. ${vendor.agents} agents choose their model in Add agent.`);
    } catch (e) {
      // Listing models only tests the address and key, so an unnamed failure is shown on the key.
      setModels(null); setStatus(''); setError(formError(e, 'key'));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true); setError(null); setStatus(replacing || kind !== 'api' ? 'Checking key…' : 'Checking key and tool calls…');
    try {
      if (replacing) {
        await runtime(`/providers/keys/${encodeURIComponent(replacing.id)}`, { key });
        setKey('');
        onSaved(`Replaced the key for ${replacing.label}.`);
      } else {
        const body = kind !== 'api' ? { provider: kind, label: label.trim(), key }
          : { label: label.trim(), base_url: baseUrl.trim(), model, key, ...(contextValue === null ? {} : { context_tokens: contextValue }) };
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

  const keyReady = key.length >= 8 && (replacing || kind !== 'api' || originOf(baseUrl));
  const ready = keyReady && (replacing || (label.trim() && models && (kind !== 'api' || (model && contextValid))));
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
            : kind === 'api'
              ? 'For an OpenAI-compatible server that needs a key, such as OpenRouter. Usage is billed by that provider. The key is kept in your system\'s credential store and sent only to this server.'
              : `${vendor.agents} agents on this key are billed by ${vendor.vendor} per token. The key is kept in your system's credential store; agents never see it, and the room sends it only to ${vendor.vendor}.`}
        </p>
        {!replacing && <>
          <label className="bd-field">
            <span className="bd-field-label">Provider</span>
            <select value={kind} disabled={busy} autoFocus onChange={e => changeKind(e.target.value as KeyKind)}>
              {KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </label>
          <label className="bd-field">
            <span className="bd-field-label">Name</span>
            <input value={label} maxLength={60} onChange={e => setLabel(e.target.value)} required />
          </label>
          {kind === 'api' && (
            <label className="bd-field">
              <span className="bd-field-label">Server address</span>
              <input className="bd-mono" value={baseUrl} placeholder="https://openrouter.ai/api/v1" required onChange={e => changeAddress(e.target.value)} />
            </label>
          )}
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
        {!replacing && kind === 'api' && models && <>
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
          <p className="pv-note">{kind === 'api'
            ? 'Check key lists the server\'s models (free). Check and save also runs one small tool-call request, billed to this key (a few hundred tokens).'
            : `Check key lists the models this key can use (free). Saving makes no billed request; ${vendor.agents} agents on it are billed when they work.`}</p>
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

const PRICE_MAX = 10_000;  // provider_store.PRICE_LIMIT
const LIMIT_MAX = 1_000_000;  // provider_store.LIMIT_MAX
/** A typed amount as a number, or NaN unless it is plain decimal digits ("1.2.3" and "." are not amounts). */
const amount = (text: string) => /^(\d+\.?\d*|\.\d+)$/.test(text.trim()) ? Number(text.trim()) : NaN;
const priceOk = (text: string) => !text.trim() || (amount(text) >= 0 && amount(text) <= PRICE_MAX);
// Blank removes the limit on purpose; anything else must be a real amount, never quietly sent as "no limit".
const limitOk = (text: string) => !text.trim() || (amount(text) > 0 && amount(text) <= LIMIT_MAX);

/** Per-model prices in USD per million tokens; blank cached input is charged at the input price. */
function PriceEditor({ def, onDone }: { def: KeyedDef; onDone: (note: string, error?: boolean) => void }) {
  // The connection's model, priced ones, and every model it served this month (Claude and Codex keys have no single model).
  const models = Array.from(new Set([def.model, ...Object.keys(def.prices), ...def.usage.models].filter(Boolean)));
  const ids = useId();
  const [limit, setLimit] = useState(def.limit?.toString() ?? '');
  const [rows, setRows] = useState<Record<string, Record<keyof Price, string>>>(() => Object.fromEntries(models.map(m => {
    const p = def.prices[m] || {};
    return [m, { input: p.input?.toString() ?? '', cached: p.cached?.toString() ?? '', output: p.output?.toString() ?? '' }];
  })));
  const [busy, setBusy] = useState(false);
  // An entered price replaces the model's whole listed row (provider_keys.prices_for), so a row is either
  // empty (listed prices apply) or has its own input and output; a blank cached input then uses that input.
  const rowEmpty = (r: Record<keyof Price, string>) => !r.input.trim() && !r.cached.trim() && !r.output.trim();
  const rowIncomplete = (r: Record<keyof Price, string>) => !rowEmpty(r) && (!r.input.trim() || !r.output.trim());
  const incomplete = models.filter(m => rowIncomplete(rows[m]));
  const pricesValid = Object.values(rows).every(r => Object.values(r).every(priceOk)) && !incomplete.length;
  const limitValid = limitOk(limit);
  const noteId = `${ids}-price-note`;
  const rowErrorId = (m: string) => `${ids}-row-${models.indexOf(m)}`;
  const placeholder = (m: string, k: keyof Price) => rowEmpty(rows[m]) ? builtinFor(def, m)?.[k]?.toString() ?? ''
    : k === 'cached' ? '= input' : 'required';
  const save = async () => {
    if (!pricesValid || !limitValid) return;  // the draft stays as typed; nothing is sent
    setBusy(true);
    const prices = Object.fromEntries(Object.entries(rows).map(([m, r]) => [m, Object.fromEntries(
      (Object.keys(r) as (keyof Price)[]).filter(k => r[k].trim()).map(k => [k, amount(r[k])]))]));
    try {
      await runtime(`/providers/keys/${encodeURIComponent(def.id)}`, { prices, limit: limit.trim() ? amount(limit) : null });
      onDone(`Saved prices and limit for ${def.label}.`);
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
              placeholder={placeholder(m, k)}
              aria-invalid={!priceOk(rows[m][k]) || (k !== 'cached' && rowIncomplete(rows[m]) && !rows[m][k].trim())}
              aria-describedby={[noteId, rowIncomplete(rows[m]) ? rowErrorId(m) : '', priceOk(rows[m][k]) ? '' : `${ids}-price-error`].filter(Boolean).join(' ')}
              value={rows[m][k]} onChange={e => setRows(old => ({ ...old, [m]: { ...old[m], [k]: e.target.value.replace(/[^0-9.]/g, '') } }))} /></td>
          ))}</tr>
        ))}</tbody>
      </table>
      {!Object.values(rows).every(r => Object.values(r).every(priceOk)) && <p id={`${ids}-price-error`} className="bd-error" role="alert">
        Enter each price as a number from 0 to {PRICE_MAX.toLocaleString()}, or leave it empty.</p>}
      {incomplete.map(m => <p key={m} id={rowErrorId(m)} className="bd-error" role="alert">
        {m}: enter both an input and an output price, or clear the row{builtinFor(def, m) ? ' to use the listed prices' : ''}.</p>)}
      {!models.length && <p className="pv-note">No model has been used yet; models appear here once agents use this key.</p>}
      {def.builtin_prices
        ? <p id={noteId} className="pv-note">Greyed numbers are {def.builtin_prices.vendor}'s listed prices as of {def.builtin_prices.as_of} (<a href={def.builtin_prices.source}
            target="_blank" rel="noreferrer noopener">price list</a>), used while a model's row is empty. Entering any price replaces that whole row, for
            example for a negotiated rate: input and output are then required, and a blank cached input is charged at your input price. A response in a
            priority or fast tier is listed separately, as "model (priority)", and needs its own price. Batch and regional pricing aren't modelled.</p>
        : <p id={noteId} className="pv-note">Take these from the provider's price list. Enter both input and output; a blank cached input is charged at the
            input price, so the estimate errs high.</p>}
      <div className="bd-field">
        <label className="bd-field-label" htmlFor={`${ids}-limit`}>Monthly spending limit (USD, estimated)</label>
        <input id={`${ids}-limit`} inputMode="decimal" value={limit} placeholder="No limit" aria-invalid={!limitValid}
          aria-describedby={limitValid ? `${ids}-limit-hint` : `${ids}-limit-error ${ids}-limit-hint`}
          onChange={e => setLimit(e.target.value.replace(/[^0-9.]/g, ''))} />
        {!limitValid && <span id={`${ids}-limit-error`} className="bd-error" role="alert">
          Enter the limit as an amount above 0 and up to {LIMIT_MAX.toLocaleString()} US dollars, or leave it empty for no limit.</span>}
        {limitValid && !limit.trim() && def.limit !== null && <span className="pv-note">Saving with this empty removes the monthly limit.</span>}
        <span id={`${ids}-limit-hint`} className="pv-note">At or over it, new requests are refused until next month or a higher limit. Requests already
          running complete, so the month can end above the limit by those responses. Every model used needs a price: while one is
          missing, new requests are refused.</span>
      </div>
      <span className="pv-inline"><button type="button" className="bd-btn pv-small" disabled={busy || !pricesValid || !limitValid}
        onClick={() => void save()}>Save prices and limit</button>
        <button type="button" className="bd-btn bd-btn-quiet pv-small" disabled={busy} onClick={() => onDone('')}>Cancel</button></span>
    </div>
  );
}

/** The vendor's listed price for a model, matching a dated snapshot ID as the backend does. */
function builtinFor(def: KeyedDef, model: string): Price | undefined {
  const models = def.builtin_prices?.models;
  return models && (models[model] ?? models[model.replace(/-(\d{8}|\d{4}-\d{2}-\d{2})$/, '')]);
}

function Usage({ usage, builtin }: { usage: KeyUsage; builtin: BuiltinPrices | null }) {
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
      This month, from token counts at {builtin ? `${builtin.vendor}'s listed prices (as of ${builtin.as_of}) and any you entered` : 'the prices you entered'}.
      Your provider's bill is authoritative.
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
          const limitState = def.limit_problem.startsWith('Monthly API limit reached') ? 'Limit reached' : 'Needs prices';
          const state = !def.enabled ? { label: 'Turned off', tone: 'muted' } : def.limit_problem ? { label: limitState, tone: 'danger' }
            : server?.reachable ? { label: 'Key valid', tone: 'ok' }
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
              <p className="bd-muted">Key ending …{def.key_hint} · sent only to {def.origin.replace(/:443$/, '')} · {def.vendor
                ? `used by ${def.provider === 'claude' ? 'Claude' : 'Codex'} agents through the room; agents never see the key` : `model ${def.model}`}</p>
              {!def.enabled && <p className="bd-muted">Off: no new requests, and agents on it show "API provider turned off". A request already running finishes. Agents are never moved to a subscription.</p>}
              {def.enabled && !def.limit_problem && server && !server.reachable && <p className="bd-muted">{server.detail}</p>}
              <Usage usage={def.usage} builtin={def.builtin_prices} />
              {def.limit !== null && (
                <p className={def.limit_problem ? 'bd-error' : 'bd-muted'}>
                  {def.limit_problem ? `${def.limit_problem} New requests are refused until then.` : `Monthly limit ${dollars(def.limit)} (estimated)`}
                </p>
              )}
              {pricing === def.id
                ? <PriceEditor def={def} onDone={(note, failed) => { if (!failed) setPricing(''); if (note) onChanged(note, failed); }} />
                : editable && <span><button type="button" className="bd-btn bd-btn-quiet pv-small" onClick={() => setPricing(def.id)}>Edit prices and limit</button></span>}
            </li>
          );
        })}
      </ul>
      {editable && <div className="pv-add">{add}</div>}
    </section>
  );
}
