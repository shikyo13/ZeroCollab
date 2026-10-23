import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ErrorText } from '../board/shared';
import { errorMessage } from '../lib/api';
import { confirmImport, previewImport, type ImportPreview, type ImportReceipt, type ImportSource } from '../lib/sessionImport';
import { TeamBuilder } from '../team/TeamBuilder';
import type { ProjectSummary } from './projectData';
import './importProject.css';

const date = (value: number | null) => value ? new Date(value * 1000).toLocaleString() : 'Not recorded';

export function ImportProject({ projects, onClose, onImported, renderOpen }: {
  projects: ProjectSummary[]; onClose: () => void; onImported: () => Promise<void>;
  renderOpen: (project: string) => ReactNode;
}) {
  const [source, setSource] = useState<ImportSource>('claude');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [receipt, setReceipt] = useState<ImportReceipt | null>(null);
  const [destination, setDestination] = useState<'new' | 'existing'>('new');
  const [name, setName] = useState('');
  const [projectId, setProjectId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [builder, setBuilder] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const step = receipt ? 3 : preview ? 2 : 1;
  const available = projects.filter(p => !p.archived);
  useEffect(() => { heading.current?.focus(); }, [step, builder]);

  const read = async () => {
    if (!file || busy) return;
    setBusy(true); setError('');
    try {
      const next = await previewImport(source, file);
      setPreview(next); setName(next.title.slice(0, 60));
      setProjectId(available.find(p => p.active)?.id || available[0]?.id || '');
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };
  const write = async () => {
    if (!preview || busy || preview.duplicate) return;
    setBusy(true); setError('');
    try {
      const next = await confirmImport({ token: preview.token, destination,
        ...(destination === 'new' ? { name: name.trim() } : { project_id: projectId }) });
      setReceipt(next);
      // Bulk import sends no live message events. Reconnect the existing chat
      // socket to replay saved history without losing this receipt or brief.
      if (projects.some(p => p.id === next.project_id && p.active)) window.ws?.close(1000, 'Refresh imported history');
      void onImported();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };

  if (builder && receipt) return <section className="ip-root">
    <TeamBuilder onClose={() => setBuilder(false)} onAdded={onImported}
      importBrief={{ projectId: receipt.project_id, projectName: receipt.project_name, brief: receipt.brief }} />
  </section>;

  return <section className="ip-root" aria-labelledby="import-heading">
    <header className="workspace-page-heading"><div>
      <span className="workspace-eyebrow">PROJECTS / IMPORT</span>
      <h1 id="import-heading" ref={heading} tabIndex={-1}>{step === 1 ? 'Bring your work into ZeroCollab' : step === 2 ? 'Review exactly what will be imported' : 'Your imported history is ready'}</h1>
      <p>Continue a Claude Code or Codex CLI conversation in a new or existing project.</p>
    </div><button className="bd-btn" disabled={busy} onClick={onClose}>Back to projects</button></header>
    <ol className="ip-steps" aria-label="Import steps">{['Source and file', 'Preview import', 'Team builder'].map((label, i) => <li key={label} aria-current={step === i + 1 ? 'step' : undefined}>{i + 1} {label}</li>)}</ol>
    <ErrorText message={error} />
    {step === 1 && <div className="ip-layout"><form className="bd-panel bd-form" onSubmit={e => { e.preventDefault(); void read(); }}>
      <fieldset disabled={busy} className="ip-sources"><legend>Where is the conversation coming from?</legend>
        {([['claude', 'Claude Code'], ['codex', 'Codex CLI']] as const).map(([id, label]) => <label key={id}>
          <input type="radio" name="import-source" checked={source === id} onChange={() => { setSource(id); setError(''); }} />
          <strong>{label}</strong><small>Saved session transcript</small>
        </label>)}
      </fieldset>
      <label className="bd-field">Session file<input type="file" accept=".jsonl,.json,application/json" disabled={busy} onChange={e => { setFile(e.target.files?.[0] || null); setError(''); }} /></label>
      {file && <p className="bd-muted">Selected: {file.name}</p>}
      <p className="bd-muted">Choose a saved session JSONL file. The source stays untouched; preview does not import anything.</p>
      <div className="bd-actions"><button type="button" className="bd-btn" disabled={busy} onClick={onClose}>Cancel</button><button className="bd-btn bd-btn-primary" disabled={!file || busy}>{busy ? 'Reading transcript...' : 'Preview import'}</button></div>
    </form><aside className="bd-panel"><h2>Bring context, then choose a team</h2><p>Prompts and replies become attributed chat history with their original dates.</p><p>Folder paths stay references. No repository is copied or attached.</p><p className="bd-muted">Imported messages do not start agent work. Review a written brief before asking connected agents to draft a team.</p></aside></div>}
    {preview && step === 2 && <div className="ip-layout"><div className="bd-panel">
      <h2>Where should this conversation land?</h2>
      <div className="ip-fields"><label>Destination<select disabled={busy} value={destination} onChange={e => setDestination(e.target.value as 'new' | 'existing')}><option value="new">New project</option><option value="existing" disabled={!available.length}>Existing project</option></select></label>
        {destination === 'new' ? <label>Project name<input value={name} maxLength={60} disabled={busy} onChange={e => setName(e.target.value)} /></label>
          : <label>Project<select value={projectId} disabled={busy} onChange={e => setProjectId(e.target.value)}>{available.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
      </div>
      <p className="bd-muted">{destination === 'new' ? 'Creates a separate project with no agents. The current project stays open.' : 'Adds one chat. Existing conversations, board records and team stay intact.'}</p>
      <dl className="ip-facts"><div><dt>Source</dt><dd>{preview.label}</dd></div><div><dt>Conversation</dt><dd>{preview.title}</dd></div><div><dt>Started</dt><dd>{date(preview.started)}</dd></div><div><dt>Ended</dt><dd>{date(preview.ended)}</dd></div><div><dt>Imported content</dt><dd>{preview.messages} messages: {preview.user_messages} prompts, {preview.assistant_messages} replies</dd></div><div><dt>Destination</dt><dd>One imported chat; no tasks or decisions inferred</dd></div></dl>
      {preview.cwd && <div className="bd-banner bd-banner-warn"><strong>Source working folder{!preview.cwd_exists && ' (not on this machine)'}</strong><code>{preview.cwd}</code><span>Kept as a reference. Attach a local repository separately.</span></div>}
      {!!preview.truncated && <p className="bd-attention">{preview.truncated} additional messages exceed the import limit and will not be imported.</p>}
      <h3>Conversation preview</h3><ol className="ip-samples">{preview.sample.map((line, i) => <li key={i}><strong>{line.role === 'user' ? 'User' : preview.label}</strong><small>{date(line.time)}</small><p>{line.text}</p></li>)}</ol>
      <div className="bd-actions"><button className="bd-btn" disabled={busy} onClick={() => { setPreview(null); setError(''); }}>Back</button><button className="bd-btn bd-btn-primary" disabled={busy || preview.duplicate || !(destination === 'new' ? name.trim() : projectId)} onClick={() => void write()}>{busy ? 'Importing...' : `Import into ${destination === 'new' ? name.trim() || 'new project' : available.find(p => p.id === projectId)?.name || 'project'}`}</button></div>
    </div><aside className="bd-panel"><h2>Import summary</h2><p>{file?.name}</p><p><strong>{preview.messages} messages</strong> from {preview.label}</p><p>Only the previewed conversation is imported. Tool output and internal session records stay out.</p>{preview.duplicate && <p className="bd-banner bd-banner-warn" role="status">This session is already imported. Open its existing history instead of importing it again.</p>}<p className="bd-muted">Nothing runs on import. You choose whether to draft a team afterwards.</p></aside></div>}
    {receipt && <div className="bd-panel"><h2>Imported into {receipt.project_name}</h2><p role="status">{receipt.messages} messages saved in <strong>{receipt.chat_title}</strong>.</p><p>Your conversation is saved. No agents were added and no work started.</p><h3>Continue in Team builder</h3><p>Review an editable brief from the imported history. Connected agents in the current room can draft for <strong>{receipt.project_name}</strong> without joining it.</p><div className="bd-actions"><button className="bd-btn bd-btn-primary" onClick={() => setBuilder(true)}>Draft a team</button><button className="bd-btn" onClick={onClose}>Do this later</button></div><div className="ip-open">{renderOpen(receipt.project_id)}</div></div>}
  </section>;
}
