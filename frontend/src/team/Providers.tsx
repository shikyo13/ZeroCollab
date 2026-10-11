/* Team > Providers: which provider apps are installed and signed in, and which local model
 * servers are running (GET /api/runtime/providers/status, provider_setup.py). Sign in opens the
 * provider's own official login in a new window (POST .../providers/<provider>/sign-in); this page
 * never sees the password or token and only polls the status until the CLI reports signed in. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';
import { ErrorText } from '../board/shared';
import { ProviderForm, type FormRequest, type ProviderDef, type ProviderDefs } from './ProviderForm';
import { ApiKeys, isKeyed } from './ApiKeys';

type SignIn = {
  state: 'signed_in' | 'signed_out' | 'unknown';
  billing?: 'subscription' | 'api' | 'unknown'; method?: string; detail?: string; command: string; reports?: boolean;
};
type Cli = { installed: boolean; path?: string; version?: string; detail?: string; sign_in?: SignIn };
type Server = { base_url: string; reachable: boolean; detail?: string; models?: string[]; model_count?: number };
type ProviderRow = {
  template: string; provider: string; label: string;
  models: { loading: boolean; count: number; error: string };
  cli?: Cli; server?: Server;
};
/** Where servers share a default address (MLX LM and llama.cpp on 8080): port_taken_by names the one that
 * identified itself there; identity_unconfirmed marks one row for an answer that named no server. */
type LocalServer = Server & { id: string; name: string; start_command: string; installed: boolean; configured: boolean;
  port_taken_by?: string; identity_unconfirmed?: boolean };
export type ProviderStatus = { platform: string; providers: ProviderRow[]; local_servers: LocalServer[] };

type Line = { label: string; tone: 'ok' | 'warn' | 'danger' | 'muted'; details: React.ReactNode[]; action?: { label: string; primary?: boolean } };
type SignInResult = { launched: boolean; already_open?: boolean; command: string };
/** A sign-in the user started here: the page re-checks until that CLI reports signed in or the deadline passes. */
/** from: the method when it started signed in (Change sign-in); polling: the window opened and the page waits for the CLI. */
type Pending = { until: number; message: string; from: string | null; polling: boolean };

const POLL_MS = 5000;
const WAIT_MS = 3 * 60 * 1000;
const STILL_SIGNED_OUT = 'Still not signed in. Finish in the sign-in window, then use Refresh connections.';

/** A model list line for an agent-backed provider; the catalog exists only once an agent uses it. */
function modelsLine(models: ProviderRow['models']): string {
  if (models.loading) return 'Loading model list…';
  if (models.error) return `Model list failed: ${models.error}`;
  if (models.count) return `${models.count} models available`;
  return 'No agent uses this provider yet';
}

function cliLine(row: ProviderRow): Line {
  const cli = row.cli!;
  if (!cli.installed) return { label: 'Not installed', tone: 'danger', details: [cli.detail || 'The official CLI was not found on this computer'] };
  const sign = cli.sign_in;
  const app = cli.version || 'Version unknown';
  // Sign in is offered only where the backend has the provider's official login command.
  const run = sign?.command ? <>Sign in opens <code>{sign.command}</code> in a new window</> : 'Sign in with its CLI, then Refresh connections';
  const signIn = sign?.command ? { label: 'Sign in', primary: true } : undefined;
  if (sign?.state === 'signed_in') {
    const details = [`${sign.method || 'Signed in'} · ${app}`, modelsLine(row.models)];
    if (sign.billing === 'api') {
      return { label: 'Signed in, API billing', tone: 'warn', details: [...details, 'Agents using this provider bill that API account'],
               action: sign.command ? { label: 'Change sign-in' } : undefined };
    }
    if (sign.billing === 'subscription') return { label: 'Signed in', tone: 'ok', details };
    return { label: 'Signed in, billing unknown', tone: 'warn', details };
  }
  if (sign?.state === 'signed_out') return { label: 'Signed out', tone: 'danger', details: [app, run], action: signIn };
  // Unknown stays unknown: a model list shows the CLI answers, not how it is signed in or billed.
  if (row.models.error) return { label: 'Needs attention', tone: 'danger', details: [app, modelsLine(row.models), run], action: signIn };
  return { label: 'Sign-in not reported', tone: 'warn', details: [`${app} · ${sign?.detail || 'sign-in status unknown'}`, modelsLine(row.models)],
           action: signIn && { label: 'Sign in' } };
}

function startHint(start: string) {
  return <>Start it with <code>{start}</code></>;
}

function serverLine(server: Server, start?: string): Line {
  if (server.reachable) return { label: 'Running', tone: 'ok', details: [`${server.base_url} · ${server.model_count ?? 0} models served`] };
  const details: React.ReactNode[] = [`${server.base_url} · ${server.detail || 'Not reachable'}`];
  // Only a refused connection means stopped; anything else answered but not as an OpenAI /models endpoint.
  const stopped = server.detail === 'Not running';
  if (start && stopped) details.push(startHint(start));
  return { label: stopped ? 'Not running' : 'Needs attention', tone: 'danger', details };
}

function localLine(local: LocalServer): Line {
  if (local.reachable && local.identity_unconfirmed) {
    return { label: 'Running, not identified', tone: 'warn', details: [`${local.base_url} · ${local.model_count ?? 0} models served`,
      `A compatible server answered but did not say whether it is ${local.name}`] };
  }
  if (local.reachable) return { label: 'Running', tone: 'ok', details: [`${local.base_url} · ${local.model_count ?? 0} models served`, 'Not added as a provider yet'] };
  if (local.port_taken_by) {
    return { label: local.installed ? 'Installed, port in use' : 'Not installed', tone: 'muted',
             details: [`${local.base_url} · ${local.port_taken_by} is using this port`, ...(local.installed ? [`Only one server can use this address; stop ${local.port_taken_by} to use ${local.name} here`] : [])] };
  }
  if (local.installed) return { label: 'Installed, not running', tone: 'muted', details: [local.base_url, startHint(local.start_command)] };
  return { label: 'Not installed', tone: 'muted', details: [local.base_url] };
}

/** manage: the connection's own controls (source, Edit, Remove, Reset), after the row's action. */
/** S4 facts for a local connection: its approximate context window and its last tool-call check. */
function connectionFacts(def: ProviderDef | undefined): string[] {
  if (!def || def.provider !== 'api') return [];
  const context = def.context_tokens ? `Context about ${def.context_tokens.toLocaleString()} tokens (${def.context_source || 'source not recorded'})`
    : 'Context window unknown: the room uses its standard prompt budget';
  const check = def.tool_check?.ok ? `Tool calls checked for ${def.tool_check.model}` : 'Tool calls not checked yet: Edit and save to check';
  return [context, check];
}

type RowProps ={ name: string; line: Line; note?: string; busy?: boolean; onAction?: () => void; manage?: React.ReactNode };

function Row({ name, line, note, busy, onAction, manage }: RowProps) {
  return (
    <li className="pv-row">
      <div className="pv-name">
        <strong>{name}</strong>
        <span className={`tm-state tm-tone-${line.tone}`}>{line.label}</span>
        <span className="pv-manage">
          {line.action && onAction && (
            <button type="button" className={`bd-btn pv-action${line.action.primary ? ' bd-btn-primary' : ''}`} disabled={busy} onClick={onAction}>
              {busy ? 'Waiting for sign-in…' : line.action.label}
            </button>
          )}
          {manage}
        </span>
      </div>
      {line.details.map((detail, i) => <p key={i} className="bd-muted">{detail}</p>)}
      {note && <p className="pv-pending" role="status">{note}</p>}
    </li>
  );
}

/** reload changes whenever the Team page's Refresh connections finishes, so the view re-checks. */
export function Providers({ reload }: { reload: number }) {
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState<Date | null>(null);
  // Keyed by provider (one CLI login can serve several templates); notes outlive the wait.
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const inFlight = useRef(false);
  // Connections as configured (provider_store.py); the status check above says how each is doing.
  const [defs, setDefs] = useState<ProviderDefs | null>(null);
  const [form, setForm] = useState<FormRequest | null>(null);
  const [confirming, setConfirming] = useState('');
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [changing, setChanging] = useState(false);

  const loadDefs = useCallback(async () => {
    try { setDefs(await runtime<ProviderDefs>('/providers')); }
    catch (e) { setNotice({ text: `Could not load connections: ${errorMessage(e)}`, error: true }); }
  }, []);
  useEffect(() => { void loadDefs(); }, [loadDefs, reload]);

  const check = useCallback(async () => {
    if (inFlight.current) return;  // a status check runs CLIs and can take several seconds; never stack them
    inFlight.current = true;
    setChecking(true);
    try {
      setStatus(await runtime<ProviderStatus>('/providers/status'));
      setError('');
      setChecked(new Date());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      inFlight.current = false;
      setChecking(false);
    }
  }, []);
  useEffect(() => { void check(); }, [check, reload]);

  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const drop = (names: string[]) => setPending(old => Object.fromEntries(Object.entries(old).filter(([p]) => !names.includes(p))));

  // Deadlines are checked on every tick, so a wait ends even while status requests keep failing.
  const expire = useCallback(() => {
    const now = Date.now();
    const expired = Object.keys(pendingRef.current).filter(p => pendingRef.current[p].until <= now);
    if (!expired.length) return;
    setNotes(old => ({ ...old, ...Object.fromEntries(expired.map(p => [p, STILL_SIGNED_OUT])) }));
    drop(expired);
  }, []);
  const polling = Object.values(pending).some(p => p.polling);
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => { expire(); void check(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [polling, check, expire]);
  useEffect(() => {
    if (!status) return;
    // Change sign-in starts signed in, so it finishes when the reported method differs from where it started.
    const done = Object.keys(pending).filter(p => pending[p].polling && status.providers.some(row => row.provider === p
      && row.cli?.sign_in?.state === 'signed_in' && (row.cli.sign_in.method || '') !== pending[p].from));
    if (!done.length) return;
    setNotes(old => ({ ...old, ...Object.fromEntries(done.map(p => [p, ''])) }));
    drop(done);
  }, [status, pending]);

  /** reports: the CLI has a status command (provider capability, not the latest answer), so the page can wait for it. */
  const signIn = useCallback(async (provider: string, from: string | null, reports: boolean) => {
    if (pendingRef.current[provider]) return;  // one sign-in per provider; the button is held from the click
    const opening: Pending = { until: Date.now() + WAIT_MS, from, polling: false, message: 'Opening the sign-in window…' };
    pendingRef.current = { ...pendingRef.current, [provider]: opening };
    setPending(old => ({ ...old, [provider]: opening }));
    setNotes(old => ({ ...old, [provider]: '' }));
    const finish = (note: string) => {
      drop([provider]);
      setNotes(old => ({ ...old, [provider]: note }));
    };
    try {
      const result = await runtime<SignInResult>(`/providers/${encodeURIComponent(provider)}/sign-in`, {});
      const opened = result.already_open ? 'The sign-in window is already open.' : 'A sign-in window opened.';
      if (!result.launched && !result.already_open) {
        finish(`No terminal could be opened here. Run ${result.command} in a terminal, then use Refresh connections.`);
      } else if (!reports) {
        finish(`${opened} This CLI cannot report its sign-in here: once its prompt appears you are signed in. Close it, then use Refresh connections.`);
      } else {
        setPending(old => ({ ...old, [provider]: { until: Date.now() + WAIT_MS, from, polling: true,
          message: `${opened} Finish there; this page checks every 5 seconds.` } }));
      }
    } catch (e) {
      finish(`Could not open sign-in: ${errorMessage(e)}`);
    }
  }, []);

  /** Remove or reset a connection, then show the result and re-check. */
  const change = async (path: string, done: string) => {
    setChanging(true);
    try {
      const result = await runtime<{ catalog_error?: string }>(path, {});
      // A restored connection whose server is down offers no models until Refresh connections finds it.
      setNotice(result?.catalog_error
        ? { text: `${done} ${result.catalog_error}. Use Refresh connections once its server is running.`, error: true }
        : { text: done });
      setConfirming('');
      void loadDefs();
      void check();
    } catch (e) {
      setNotice({ text: errorMessage(e), error: true });
    } finally {
      setChanging(false);
    }
  };

  if (!status) return error ? <ErrorText message={`Could not check providers: ${error}`} /> : <p className="bd-empty">Checking providers…</p>;

  const defById = new Map((defs?.providers ?? []).map(d => [d.id, d]));
  const subscriptions = status.providers.filter(p => p.cli);
  // API key connections have their own panel; Local models lists only keyless servers.
  const configured = status.providers.filter(p => p.server && !isKeyed(defById.get(p.template)));
  const keyed = (defs?.providers ?? []).filter(isKeyed);
  const keyedServers = Object.fromEntries(status.providers.filter(p => p.server).map(p => [p.template, p.server]));
  // Servers can share a default address (MLX LM and llama.cpp on 8080); only one is the configured one.
  const localByBase = new Map(status.local_servers.filter(s => s.configured).map(s => [s.base_url.replace(/\/$/, ''), s]));
  const unconfigured = status.local_servers.filter(s => !s.configured);
  const when = checking ? 'Checking…' : checked ? `Checked at ${checked.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : '';
  const editable = !defs?.error;

  const manage = (def: ProviderDef | undefined) => def && <>
    {def.source !== 'app' && <span className="bd-label" title={def.source === 'config' ? 'Defined in config.toml' : 'config.toml values changed here'}>
      {def.source === 'config' ? 'config.toml' : 'edited'}</span>}
    {editable && <button type="button" className="bd-btn pv-small" disabled={changing} onClick={() => { setConfirming(''); setForm({ mode: 'edit', def }); }}>Edit</button>}
    {editable && def.source === 'edited' && (
      <button type="button" className="bd-btn bd-btn-quiet pv-small" disabled={changing}
        onClick={() => void change(`/providers/${encodeURIComponent(def.id)}/reset`, `Restored ${def.label} from config.toml.`)}>Reset</button>
    )}
    {editable && (confirming === def.id
      ? <>
        <button type="button" className="bd-btn bd-btn-danger pv-small" disabled={changing}
          onClick={() => void change(`/providers/${encodeURIComponent(def.id)}/remove`, `Removed ${def.label}.`)}>Confirm remove</button>
        <button type="button" className="bd-btn bd-btn-quiet pv-small" onClick={() => setConfirming('')}>Keep</button>
      </>
      : <button type="button" className="bd-btn bd-btn-quiet pv-small" disabled={changing} onClick={() => setConfirming(def.id)}>Remove</button>)}
  </>;

  return (<>
    <ErrorText message={error && `Last check failed: ${error}`} />
    <ErrorText message={defs?.error ?? ''} />
    {notice && <p className={notice.error ? 'bd-error' : 'tm-notice'} role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
    {form && <ProviderForm request={form} onClose={() => setForm(null)}
      onSaved={note => { setForm(null); setNotice({ text: note }); void loadDefs(); void check(); }} />}
    <div className="pv-grid">
      <section className="bd-panel pv-panel" aria-labelledby="pv-subscriptions">
        <div className="pv-head"><h2 id="pv-subscriptions">Subscriptions</h2><span className="pv-note" role="status">{when}</span></div>
        <p className="pv-note">
          Each provider signs in with its own official app, and ZeroCollab never sees your password or token. A CLI signed
          in with an API account bills that account; each row shows the sign-in its CLI reports.
          {keyed.length > 0 && ' API keys added below are used only by agents on that key; keys in your environment are ignored.'}
        </p>
        <ul className="pv-list">
          {subscriptions.map(row => (
            <Row key={row.template} name={row.label} line={cliLine(row)} busy={row.provider in pending} manage={manage(defById.get(row.template))}
                 note={pending[row.provider]?.message || notes[row.provider]} onAction={() => void signIn(row.provider, row.cli?.sign_in?.state === 'signed_in' ? row.cli.sign_in.method || '' : null,
                   row.cli?.sign_in?.reports === true)} />
          ))}
        </ul>
        {editable && !!defs?.available.length && (
          <div className="pv-add">
            {defs.available.map(a => (
              <button key={a.provider} type="button" className="bd-btn" onClick={() => setForm({ mode: 'create', provider: a.provider, label: a.label })}>
                Connect {a.label}
              </button>
            ))}
            <span className="pv-note">Uses that provider's official CLI and its own sign-in</span>
          </div>
        )}
      </section>
      <section className="bd-panel pv-panel" aria-labelledby="pv-local">
        <div className="pv-head"><h2 id="pv-local">Local models</h2></div>
        <p className="pv-note">OpenAI-compatible servers on this computer, found on their usual ports.</p>
        <ul className="pv-list">
          {configured.map(row => {
            const server = row.server!;
            const local = localByBase.get(server.base_url.replace(/\/$/, ''));
            const def = defById.get(row.template);
            const line = serverLine(server, local?.start_command);
            return <Row key={row.template} name={local ? `${row.label} (${local.name})` : row.label}
                        line={{ ...line, details: [...line.details, ...connectionFacts(def)] }} manage={manage(def)} />;
          })}
          {unconfigured.map(local => (
            <Row key={local.id} name={local.name} line={localLine(local)}
                 manage={editable && local.reachable && (
                   <button type="button" className="bd-btn bd-btn-primary pv-small"
                     onClick={() => setForm({ mode: 'create', provider: 'api', label: local.identity_unconfirmed ? 'Local model' : local.name, base_url: local.base_url })}>
                     Add as provider
                   </button>
                 )} />
          ))}
        </ul>
        {editable && (
          <div className="pv-add">
            <button type="button" className="bd-btn" onClick={() => setForm({ mode: 'create', provider: 'api', label: '' })}>Add another server…</button>
            <span className="pv-note">Any OpenAI-compatible address, on this computer or your network</span>
          </div>
        )}
      </section>
      {defs && <ApiKeys defs={keyed} servers={keyedServers} editable={editable} changing={changing} manage={manage}
        onChanged={(text, failed) => { setNotice({ text, error: failed }); void loadDefs(); void check(); }} />}
      {!!defs?.removed.length && (
        <section className="bd-panel pv-panel" aria-labelledby="pv-removed">
          <div className="pv-head"><h2 id="pv-removed">Removed from this room</h2></div>
          <p className="pv-note">These connections are in config.toml but hidden here. Restore brings back their config.toml values.</p>
          <ul className="pv-list">
            {defs.removed.map(r => (
              <li key={r.id} className="pv-row"><div className="pv-name"><strong>{r.label}</strong>
                <span className="pv-manage">
                  <button type="button" className="bd-btn pv-small" disabled={changing || !editable}
                    onClick={() => void change(`/providers/${encodeURIComponent(r.id)}/reset`, `Restored ${r.label}.`)}>Restore</button>
                </span>
              </div></li>
            ))}
          </ul>
        </section>
      )}
    </div>
  </>);
}
