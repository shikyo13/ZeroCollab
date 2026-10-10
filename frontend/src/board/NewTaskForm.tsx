import { useState, type FormEvent } from 'react';
import { runtime } from '../lib/api';
import type { Task, TaskAttachment } from '../lib/types';
import { addInBatches, ATTACHMENT_INPUT, CARD_IMAGES, ImageDropZone, uploadAll } from './Attachments';
import { ErrorText, Field, LabelField, ParticipantOptions, StatusPill, splitLabels, splitPaths, useAction, type Agents } from './shared';
import { sessionTitle } from '../lib/room';
import { LIMITS } from '../lib/constants';

export function NewTaskForm({
  agents,
  tasks,
  channel,
  onCreated,
  onCancel,
}: {
  agents: Agents;
  tasks: Task[];
  channel: string;
  onCreated: (task: Task) => void;
  onCancel: () => void;
}) {
  const { run, busy, error } = useAction();
  const [title, setTitle] = useState('');
  const [owner, setOwner] = useState('');
  const [paths, setPaths] = useState('');
  const [details, setDetails] = useState('');
  const [deps, setDeps] = useState<string[]>([]);
  const [start, setStart] = useState(false);
  const [labels, setLabels] = useState('');
  const [images, setImages] = useState<TaskAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const openTasks = tasks.filter(t => t.status !== 'done');
  const knownLabels = [...new Set(tasks.flatMap(t => t.labels ?? []))].sort();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let created: Task | null = null;
    const ok = await run(async () => {
      // One save takes up to ATTACHMENT_INPUT images; the rest follow in further saves, so none is dropped.
      const first = images.slice(0, ATTACHMENT_INPUT);
      created = await runtime<Task>('/tasks', {
        action: 'create',
        title: title.trim(),
        owner,
        paths: splitPaths(paths),
        details: details.trim(),
        depends_on: deps,
        ...(splitLabels(labels).length ? { labels: splitLabels(labels) } : {}),
        ...(first.length ? { attachments: first } : {}),
        start: start && !!owner,
        channel,
      });
      const id = (created as Task).id;
      const rest = images.slice(ATTACHMENT_INPUT);
      if (rest.length) {
        created = (await addInBatches((created as Task).attachments ?? [], rest,
          next => runtime<Task>('/tasks', { action: 'update', task_id: id, channel, attachments: next }))) ?? created;
      }
    });
    if (ok && created) onCreated(created);
  };

  return (
    <form className="bd-form bd-new-task" onSubmit={submit}>
      <Field label="Task">
        <input value={title} onChange={e => setTitle(e.target.value)} maxLength={LIMITS.taskTitle} required autoFocus placeholder="Concrete outcome, e.g. Add retry to upload" />
      </Field>
      <div className="bd-form-row">
        <Field label="Owner">
          <select value={owner} onChange={e => setOwner(e.target.value)}>
            <ParticipantOptions agents={agents} emptyLabel="Unclaimed" />
          </select>
        </Field>
        <Field label="Reserved paths" hint="Comma-separated; leave empty for discussion/research">
          <input value={paths} onChange={e => setPaths(e.target.value)} className="bd-mono" placeholder="src/auth, README.md" />
        </Field>
      </div>
      <Field label="Details and done criteria">
        <textarea value={details} onChange={e => setDetails(e.target.value)} rows={3} maxLength={LIMITS.taskDetails} />
      </Field>
      <LabelField value={labels} onChange={setLabels} known={knownLabels} />
      <div className="bd-field">
        <span className="bd-field-label">Images</span>
        <ImageDropZone
          disabled={uploading}
          onFiles={files =>
            void run(async () => {
              setUploading(true);
              try {
                const uploaded = await uploadAll(files);
                setImages(cur => [...cur, ...uploaded]);
              } finally {
                setUploading(false);
              }
            })
          }
        >
          {images.length > CARD_IMAGES && (
            <p className="bd-field-hint">
              {images.length} images: the card will show the newest {CARD_IMAGES}, and the rest go to Earlier images on the task.
            </p>
          )}
          {images.length > 0 && (
            <ul className="bd-gallery bd-gallery-compact">
              {images.map(a => (
                <li key={a.url} className="bd-gallery-item">
                  <img src={a.url} alt={a.name} />
                  <button type="button" className="bd-btn bd-btn-quiet" onClick={() => setImages(cur => cur.filter(x => x.url !== a.url))}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
        </ImageDropZone>
      </div>
      {openTasks.length > 0 && (
        <details className="bd-disclosure">
          <summary>Depends on{deps.length ? ` (${deps.length})` : ''}</summary>
          <fieldset className="bd-dep-picker">
            <legend className="bd-sr-only">Dependencies</legend>
            {openTasks.map(t => (
              <label key={t.id} className="bd-check">
                <input
                  type="checkbox"
                  checked={deps.includes(t.id)}
                  onChange={e => setDeps(d => (e.target.checked ? [...d, t.id] : d.filter(id => id !== t.id)))}
                />
                <span>
                  {t.title} <StatusPill status={t.status} />
                </span>
              </label>
            ))}
          </fieldset>
        </details>
      )}
      <label className="bd-check">
        <input type="checkbox" checked={start} disabled={!owner} onChange={e => setStart(e.target.checked)} />
        Start the owner on it now in {sessionTitle(channel)}
      </label>
      <div className="bd-actions">
        <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || uploading || !title.trim()}>
          Create task
        </button>
        <button type="button" className="bd-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <ErrorText message={error} />
    </form>
  );
}
