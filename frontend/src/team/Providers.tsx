/* Team > Providers: which provider apps are installed and signed in, and which local model
 * servers are running (GET /api/runtime/providers/status, provider_setup.py). Sign in opens the
 * provider's own official login in a new window (POST .../providers/<provider>/sign-in); this page
 * never sees the password or token and only polls the status until the CLI reports signed in. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage, runtime } from '../lib/api';
import { ErrorText } from '../board/shared';

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
type LocalServer = Server & { id: string; name: string; start_command: string; installed: boolean; configured: boolean };
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
  if (local.reachable) return { label: 'Running', tone: 'ok', details: [`${local.base_url} · ${local.model_count ?? 0} models served`, 'Not added as a provider yet'] };
  if (local.installed) return { label: 'Installed, not running', tone: 'muted', details: [local.base_url, startHint(local.start_command)] };
  return { label: 'Not installed', tone: 'muted', details: [local.base_url] };
}

type RowProps = { name: string; line: Line; note?: string; busy?: boolean; onAction?: () => void };

function Row({ name, line, note, busy, onAction }: RowProps) {
  return (
    <li className="pv-row">
      <div className="pv-name">
        <strong>{name}</strong>
        <span className={`tm-state tm-tone-${line.tone}`}>{line.label}</span>
        {line.action && onAction && (
          <button type="button" className={`bd-btn pv-action${line.action.primary ? ' bd-btn-primary' : ''}`} disabled={busy} onClick={onAction}>
            {busy ? 'Waiting for sign-in…' : line.action.label}
          </button>
        )}
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

  if (!status) return error ? <ErrorText message={`Could not check providers: ${error}`} /> : <p className="bd-empty">Checking providers…</p>;

  const subscriptions = status.providers.filter(p => p.cli);
  const configured = status.providers.filter(p => p.server);
  const localByBase = new Map(status.local_servers.map(s => [s.base_url.replace(/\/$/, ''), s]));
  const unconfigured = status.local_servers.filter(s => !s.configured);
  const when = checking ? 'Checking…' : checked ? `Checked at ${checked.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : '';

  return (<>
    <ErrorText message={error && `Last check failed: ${error}`} />
    <div className="pv-grid">
      <section className="bd-panel pv-panel" aria-labelledby="pv-subscriptions">
        <div className="pv-head"><h2 id="pv-subscriptions">Subscriptions</h2><span className="pv-note" role="status">{when}</span></div>
        <p className="pv-note">
          Each provider signs in with its own official app, and ZeroCollab never sees your password or token. API keys in
          your environment are ignored, but a CLI signed in with an API account bills that account; each row shows the
          sign-in its CLI reports.
        </p>
        <ul className="pv-list">
          {subscriptions.map(row => (
            <Row key={row.template} name={row.label} line={cliLine(row)} busy={row.provider in pending}
                 note={pending[row.provider]?.message || notes[row.provider]} onAction={() => void signIn(row.provider, row.cli?.sign_in?.state === 'signed_in' ? row.cli.sign_in.method || '' : null,
                   row.cli?.sign_in?.reports === true)} />
          ))}
        </ul>
      </section>
      <section className="bd-panel pv-panel" aria-labelledby="pv-local">
        <div className="pv-head"><h2 id="pv-local">Local models</h2></div>
        <p className="pv-note">OpenAI-compatible servers on this computer, found on their usual ports.</p>
        <ul className="pv-list">
          {configured.map(row => {
            const server = row.server!;
            const local = localByBase.get(server.base_url.replace(/\/$/, ''));
            return <Row key={row.template} name={local ? `${row.label} (${local.name})` : row.label} line={serverLine(server, local?.start_command)} />;
          })}
          {unconfigured.map(local => <Row key={local.id} name={local.name} line={localLine(local)} />)}
        </ul>
      </section>
    </div>
  </>);
}
