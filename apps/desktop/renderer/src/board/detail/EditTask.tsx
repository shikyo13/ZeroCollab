/* Task editor: title, details, areas, reserved paths and dependencies. */
import { useState, type FormEvent } from 'react';
import { runtime } from '../../lib/api';
import { LIMITS } from '../../lib/constants';
import type { Task } from '../../lib/types';
import { ErrorText, Field, LabelField, StatusPill, splitLabels, splitPaths, useAction } from '../shared';

function DependencyPicker({
  task,
  tasks,
  value,
  onChange,
}: {
  task: Task;
  tasks: Task[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const candidates = tasks.filter(t => t.id !== task.id);
  if (!candidates.length) return <p className="bd-muted">No other tasks to depend on.</p>;
  return (
    <fieldset className="bd-dep-picker">
      <legend>Depends on</legend>
      {candidates.map(t => (
        <label key={t.id} className="bd-check">
          <input
            type="checkbox"
            checked={value.includes(t.id)}
            onChange={e => onChange(e.target.checked ? [...value, t.id] : value.filter(id => id !== t.id))}
          />
          <span>
            {t.title} <code className="bd-id">{t.id}</code> <StatusPill status={t.status} />
          </span>
        </label>
      ))}
    </fieldset>
  );
}

export function EditTask({ task, tasks, onDone }: { task: Task; tasks: Task[]; onDone: () => void }) {
  const { run, busy, error } = useAction();
  const [title, setTitle] = useState(task.title);
  const [details, setDetails] = useState(task.details);
  const [paths, setPaths] = useState(task.paths.join('\n'));
  const [deps, setDeps] = useState<string[]>(task.depends_on ?? []);
  const [labels, setLabels] = useState((task.labels ?? []).join(', '));
  const knownLabels = [...new Set(tasks.flatMap(t => t.labels ?? []))].sort();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await run(() =>
      runtime('/tasks', {
        action: 'update',
        task_id: task.id,
        title: title.trim(),
        details: details.trim(),
        paths: splitPaths(paths),
        depends_on: deps,
        // Only sent when edited, so saving other fields never depends on label support.
        ...(splitLabels(labels).join() !== (task.labels ?? []).join() ? { labels: splitLabels(labels) } : {}),
      }),
    );
    if (ok) onDone();
  };
  return (
    <form className="bd-form" onSubmit={submit}>
      <Field label="Title">
        <input value={title} onChange={e => setTitle(e.target.value)} maxLength={LIMITS.taskTitle} required />
      </Field>
      <Field label="Details and done criteria">
        <textarea value={details} onChange={e => setDetails(e.target.value)} rows={4} maxLength={LIMITS.taskDetails} />
      </Field>
      <LabelField value={labels} onChange={setLabels} known={knownLabels} />
      <Field label="Reserved paths" hint="One per line or comma-separated. Overlapping reservations with other owned tasks are refused.">
        <textarea value={paths} onChange={e => setPaths(e.target.value)} rows={3} className="bd-mono" />
      </Field>
      <DependencyPicker task={task} tasks={tasks} value={deps} onChange={setDeps} />
      <div className="bd-actions">
        <button type="submit" className="bd-btn bd-btn-primary" disabled={busy || !title.trim()}>
          Save changes
        </button>
        <button type="button" className="bd-btn" onClick={onDone}>
          Cancel
        </button>
      </div>
      <ErrorText message={error} />
    </form>
  );
}
