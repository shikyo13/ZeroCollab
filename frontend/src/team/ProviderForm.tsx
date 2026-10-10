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
};
export type ProviderDefs = {
  providers: ProviderDef[]; removed: { id: string; label: string }[]; error: string;
  available: { provider: ProviderKind; label: string }[];
};
export type FormRequest =
  | { mode: 'create'; provider: ProviderKind; label: string; base_url?: string }
  | { mode: 'edit'; def: ProviderDef };

type Probe = { reachable: boolean; detail?: string; models?: string[]; model_count?: number };

const DEFAULT_OUTPUT = 1024;
const OUTPUT_RANGE = [256, 32768] as const;

export function ProviderForm({ request, onClose, onSaved }: { request: FormRequest; onClose: () => void; onSaved: (note: string) => void }) {
  const editing = request.mode === 'edit' ? request.def : null;
  const kind = editing ? editing.provider : (request as Extract<FormRequest, { mode: 'create' }>).provider;
  const local = kind === 'api';
  const [label, setLabel] = useState(editing?.label ?? (request.mode === 'create' ? request.label : ''));
  const [color, setColor] = useState(editing?.color ?? '#8b5cf6');
  const [baseUrl, setBaseUrl] = useState(editing?.base_url ?? (request.mode === 'create' ? request.base_url ?? '' : ''));
  const [model, setModel] = useState(editing?.model ?? '');
  const [output, setOutput] = useState(String(editing?.max_tokens ?? DEFAULT_OUTPUT));
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
      const result = await runtime<Probe>('/providers/probe', { base_url: address.trim() });
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
  const ready = label.trim() && (!local || (baseUrl.trim() && model && outputValid));

  const save = async () => {
    setBusy(true);
    setError('');
    const body: Record<string, unknown> = { label: label.trim(), color };
    if (local) Object.assign(body, { base_url: baseUrl.trim(), model, max_tokens: outputValue });
    try {
      if (editing) await runtime(`/providers/${encodeURIComponent(editing.id)}`, body);
      else await runtime('/providers', { ...body, provider: kind });
      onSaved(editing ? `Saved ${label.trim()}.` : `Added ${label.trim()}. Choose it in Add agent.`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const title = editing ? `Edit ${editing.label}` : local ? 'Add local provider' : `Connect ${label || 'provider'}`;
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
        <Field label="Name" hint="Shown on the Team page and on each agent that uses it">
          <input value={label} maxLength={60} onChange={e => setLabel(e.target.value)} required autoFocus />
        </Field>
        <Field label="Color">
          <span className="tm-colors"><input type="color" value={color} onChange={e => setColor(e.target.value)} /><code>{color}</code></span>
        </Field>
        {local && <>
          <Field label="Server address" hint="An OpenAI-compatible endpoint, on this computer or your network">
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
          <Field label="Maximum response length (tokens)" hint={`${OUTPUT_RANGE[0]} to ${OUTPUT_RANGE[1]}. Longer answers stop at this length.`}>
            <input inputMode="numeric" value={output} aria-invalid={!outputValid} onChange={e => setOutput(e.target.value.replace(/[^0-9]/g, ''))} />
          </Field>
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
