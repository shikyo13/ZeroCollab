/* Agent profile form, ported from static/profiles.js with the same validation
 * hints, handle generation, model/effort catalogs and archive semantics. */
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { runtime } from '../lib/api';
import { RoleProfile } from './RoleProfile';
import type { AgentState } from '../lib/types';
import { ErrorText, Field, useAction } from '../board/shared';
import { HANDLE_PATTERN, LIMITS, RESERVED_HANDLES } from '../lib/constants';

export interface Profile {
  /** Permanent internal id: tasks, history, worktree and sessions key on it. */
  id: string;
  /** Public @handle; defaults to id. Earlier handles keep routing to this agent. */
  handle?: string;
  previous_handles?: string[];
  template: string;
  name: string;
  color: string;
  background: string;
  model: string;
  effort: string;
  archived?: boolean;
  paused?: boolean;
}

interface ModelOption {
  value: string;
  label: string;
  efforts?: string[];
}

export interface ProviderInfo {
  id: string;
  provider: string;
  label: string;
  color: string;
  /** False for an API key connection that is switched off: Add agent does not offer it. */
  offered?: boolean;
  controls: {
    models: ModelOption[];
    selected?: { model: string; effort: string };
    error?: string;
    loading?: boolean;
    effort_note?: string;
  };
}

/** GET /api/runtime/profiles */
export interface ProfilesData {
  project: string;
  profiles: Profile[];
  providers: ProviderInfo[];
  paused: boolean;
}

const SWATCHES = ['#8b5cf6', '#3b82f6', '#14b8a6', '#22c55e', '#f59e0b', '#f97316', '#ec4899'];

/** Every name that routes to a profile: its id, current handle and previous handles. */
function namesOf(p: Profile): string[] {
  return [p.id, p.handle || p.id, ...(p.previous_handles ?? [])];
}

function availableHandle(name: string, profiles: Profile[]): string {
  let stem = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 30) || 'expert';
  if (!/^[a-z]/.test(stem) || RESERVED_HANDLES.includes(stem)) stem = 'agent-' + stem;
  let handle = stem;
  for (let i = 2; profiles.some(p => namesOf(p).includes(handle)); i++) handle = `${stem}-${i}`;
  return handle;
}

/** Mirrors the server's checks so the problem shows while typing; the server stays authoritative. */
function handleProblem(handle: string, selfId: string, profiles: Profile[]): string {
  if (!handle) return '';
  if (!new RegExp(`^${HANDLE_PATTERN}$`).test(handle)) return 'Start with a letter; use lowercase letters, digits and hyphens.';
  if (RESERVED_HANDLES.includes(handle)) return `@${handle} is reserved.`;
  const holder = profiles.find(p => p.id !== selfId && namesOf(p).includes(handle));
  if (!holder) return '';
  const current = (holder.handle || holder.id) === handle || holder.id === handle;
  return `@${handle} ${current ? 'is used by' : 'is a previous handle of'} ${holder.name}${holder.archived ? ' (archived)' : ''}.`;
}

function effortLabel(value: string): string {
  return value === 'xhigh' ? 'Extra high (xhigh)' : value[0].toUpperCase() + value.slice(1);
}

interface Draft {
  name: string;
  id: string;
  handle: string;
  template: string;
  color: string;
  model: string;
  effort: string;
  background: string;
}

function initialDraft(data: ProfilesData, profile: Profile | null, duplicate: boolean): Draft {
  const provider = data.providers.find(p => p.offered !== false);
  if (profile) {
    const name = duplicate ? profile.name + ' copy' : profile.name;
    const id = duplicate ? availableHandle(name, data.profiles) : profile.id;
    // A copy is a new agent: if its provider is no longer offered (an API key switched off), it starts
    // on the first offered one, as Add agent does, rather than on a connection that can't run it.
    const moved = duplicate && data.providers.find(p => p.id === profile.template)?.offered === false;
    return {
      name,
      id,
      handle: duplicate ? id : profile.handle || profile.id,
      template: moved ? provider?.id ?? '' : profile.template,
      color: profile.color,
      model: moved ? provider?.controls.selected?.model ?? provider?.controls.models[0]?.value ?? '' : profile.model,
      effort: moved ? '' : profile.effort || '',
      background: profile.background || '',
    };
  }
  return {
    name: '',
    id: '',
    handle: '',
    template: provider?.id ?? '',
    color: provider?.color || '#8b5cf6',
    model: provider?.controls.selected?.model ?? provider?.controls.models[0]?.value ?? '',
    effort: '',
    background: '',
  };
}

export function ProfileEditor({
  data,
  profile,
  duplicate,
  agent,
  onDirty,
  onSaved,
  onDuplicate,
  onCancel,
}: {
  data: ProfilesData;
  profile: Profile | null;
  duplicate: boolean;
  agent?: AgentState;
  onDirty: (dirty: boolean) => void;
  onSaved: (id: string, message: string) => void | Promise<void>;
  onDuplicate: () => void;
  onCancel: () => void;
}) {
  const editing = !!profile && !duplicate;
  const [draft, setDraft] = useState<Draft>(() => initialDraft(data, profile, duplicate));
  const [dirty, setDirty] = useState(false);
  const [handleEdited, setHandleEdited] = useState(false);
  const save = useAction();
  const archive = useAction();

  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  const provider = data.providers.find(p => p.id === draft.template);
  const catalog = provider?.controls;
  const models = useMemo(() => {
    const list = [...(catalog?.models ?? [])];
    if (!list.some(m => m.value === draft.model)) list.unshift({ value: draft.model, label: (draft.model || 'Provider default') + ' · saved' });
    return list;
  }, [catalog, draft.model]);
  const efforts = catalog?.models.find(m => m.value === draft.model)?.efforts ?? [];

  const update = (patch: Partial<Draft>) => {
    setDraft(d => ({ ...d, ...patch }));
    setDirty(true);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let saved: Profile | null = null;
    const ok = await save.run(async () => {
      saved = await runtime<Profile>(editing ? `/profiles/${encodeURIComponent(profile!.id)}` : '/profiles', {
        ...draft,
        // A new agent's handle is its id; an existing agent keeps its id and changes only the handle.
        handle: editing ? draft.handle : draft.id,
        // Without a catalog (provider offline) keep the saved effort so cosmetic edits still save.
        effort: !catalog?.models.length || efforts.includes(draft.effort) ? draft.effort : '',
      });
    });
    if (ok && saved) {
      setDirty(false);
      await onSaved(
        (saved as Profile).id,
        editing ? 'Saved. Name and handle are updated; model and effort apply from the next turn.' : data.paused ? 'Agent added. The room remains stopped.' : 'Agent added. It will respond when addressed.',
      );
    }
  };

  const toggleArchive = async () => {
    if (!profile) return;
    if (dirty) {
      archive.setError('Save or cancel your changes first.');
      return;
    }
    const restoring = !!profile.archived;
    const ok = await archive.run(() => runtime(`/profiles/${encodeURIComponent(profile.id)}/archive`, { archived: !restoring }));
    if (ok)
      await onSaved(
        profile.id,
        restoring ? 'Restored and paused. Resume this agent when ready.' : 'Archived. Its history and working tree are preserved.',
      );
  };

  const handleValue = editing ? draft.handle : draft.id;
  const savedHandle = profile ? profile.handle || profile.id : '';
  const problem = handleProblem(handleValue, editing ? profile!.id : '', data.profiles);
  const handleHint = problem
    ? ''
    : !editing
      ? 'Lowercase letters, digits and hyphens.'
      : handleValue !== savedHandle
        ? `@${savedHandle} keeps working for earlier messages and tasks.`
        : 'Rename any time; earlier handles keep working.';
  // Edits keep saving without a catalog (the server only checks model/effort when they change).
  const saveDisabled = save.busy || !!problem || (!editing && (!!catalog?.error || !!catalog?.loading));
  const initials = (draft.name.trim() || 'Expert').slice(0, 2).toUpperCase();

  return (
    <form className="bd-form tm-form" onSubmit={submit}>
      <div className="tm-editor-head">
        <span className="tm-avatar tm-avatar-lg" style={{ background: draft.color }} aria-hidden="true">
          {initials}
        </span>
        <div>
          <h2>{editing ? `Edit ${profile!.name}` : duplicate ? 'Duplicate agent' : 'Add an agent'}</h2>
          <p className="bd-muted">
            {editing ? 'Name and handle update everywhere when you save. Model and effort apply from the next turn.' : 'Give them a perspective. The team works together.'}
          </p>
        </div>
      </div>

      <div className="bd-form-row">
        <Field label="Name">
          <input
            value={draft.name}
            required
            maxLength={LIMITS.name}
            autoComplete="off"
            placeholder="e.g. Maya - UX researcher"
            onChange={e => {
              const name = e.target.value;
              update(editing || handleEdited ? { name } : { name, id: availableHandle(name, data.profiles) });
            }}
          />
        </Field>
        <Field label="Mention handle" hint={handleHint}>
          <div className="tm-handle">
            <span aria-hidden="true">@</span>
            <input
              value={handleValue}
              required
              pattern={HANDLE_PATTERN}
              maxLength={LIMITS.handle}
              autoComplete="off"
              placeholder="maya-ux"
              aria-invalid={!!problem}
              onChange={e => {
                const value = e.target.value.toLowerCase();
                setHandleEdited(true);
                update(editing ? { handle: value } : { id: value });
              }}
            />
          </div>
          {problem && <span className="bd-error tm-handle-problem" role="alert">{problem}</span>}
        </Field>
      </div>

      <div className="bd-form-row">
        <Field label="Provider" hint={editing ? 'Duplicate the agent to use a different provider.' : undefined}>
          <select
            value={draft.template}
            disabled={editing}
            onChange={e => {
              const next = data.providers.find(p => p.id === e.target.value);
              update({
                template: e.target.value,
                color: next?.color || '#8b5cf6',
                model: next?.controls.selected?.model ?? next?.controls.models[0]?.value ?? '',
                effort: '',
              });
            }}
          >
            {/* A switched-off API key connection is not offered for new agents or copies; an agent being edited keeps showing its own. */}
            {data.providers.filter(p => p.offered !== false || (editing && p.id === draft.template)).map(p => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </Field>
        <div className="bd-field">
          <span className="bd-field-label" id="tm-color-label">
            Color
          </span>
          <div className="tm-colors" role="group" aria-labelledby="tm-color-label">
            <input type="color" value={draft.color} onChange={e => update({ color: e.target.value })} aria-label="Custom agent color" />
            <code>{draft.color}</code>
            {SWATCHES.map(color => (
              <button
                key={color}
                type="button"
                className="tm-swatch"
                style={{ background: color }}
                aria-label={`Use color ${color}`}
                aria-pressed={draft.color.toLowerCase() === color}
                onClick={() => update({ color })}
              />
            ))}
          </div>
        </div>
      </div>

      <div className="bd-form-row">
        <Field label="Model">
          <select value={draft.model} disabled={!catalog?.models.length} onChange={e => update({ model: e.target.value, effort: '' })}>
            {models.map(m => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Reasoning effort">
          <select value={efforts.includes(draft.effort) ? draft.effort : ''} disabled={!efforts.length} onChange={e => update({ effort: e.target.value })}>
            <option value="">Provider default</option>
            {efforts.map(v => (
              <option key={v} value={v}>
                {effortLabel(v)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {catalog?.loading ? <p className="bd-muted">Loading model choices…</p>
        : (catalog?.error || catalog?.effort_note) && <p className={catalog?.error ? 'bd-error' : 'bd-muted'}>{catalog?.error || catalog?.effort_note}</p>}

      {editing && <RoleProfile key={profile!.id} name={profile!.id} guarded={dirty || save.busy || archive.busy} />}

      <Field label="Background and personal prompt" hint={`${draft.background.length.toLocaleString()} / ${LIMITS.profileText.toLocaleString()} characters · used when no approved role profile is active; kept as the fallback after revocation.`}>
        <textarea
          value={draft.background}
          rows={9}
          maxLength={LIMITS.profileText}
          onChange={e => update({ background: e.target.value })}
          placeholder="Describe this agent's expertise, perspective and approach. For example: You are a senior UX researcher. Focus on user goals, accessibility and the evidence behind design decisions. Challenge assumptions constructively."
        />
      </Field>

      {editing && agent?.worktree && (
        <p className="bd-muted tm-worktree">
          Working tree <code>{agent.worktree}</code>
          {agent.branch ? (
            <>
              {' '}
              on branch <code>{String(agent.branch)}</code>
            </>
          ) : null}
        </p>
      )}

      <ErrorText message={save.error || archive.error} />
      {/* Sticks to the bottom of the view while there are unsaved changes, so Save is never below the fold. */}
      <div className={`bd-actions tm-form-actions${dirty ? ' tm-form-dirty' : ''}`}>
        {dirty && editing && <span className="tm-unsaved" role="status">Unsaved changes</span>}
        {editing && !dirty && (
          <>
            <button type="button" className="bd-btn" onClick={onDuplicate} disabled={dirty} title={dirty ? 'Save or cancel first' : undefined}>
              Duplicate
            </button>
            <button type="button" className="bd-btn bd-btn-quiet" disabled={archive.busy} onClick={() => void toggleArchive()}>
              {profile!.archived ? 'Restore agent' : 'Archive'}
            </button>
          </>
        )}
        <span className="tm-spacer" />
        {(dirty || !editing) && (
          <button type="button" className="bd-btn" onClick={onCancel}>
            Cancel
          </button>
        )}
        <button type="submit" className="bd-btn bd-btn-primary" disabled={saveDisabled || (editing && !dirty)}>
          {save.busy ? 'Saving...' : editing ? 'Save changes' : 'Add agent'}
        </button>
      </div>
    </form>
  );
}
