/* Read-only task body: details, areas, reserved paths and dependency links. */
import type { Task } from '../../lib/types';
import { LabelChips, StatusPill } from '../shared';

export function TaskOverview({ task, tasks, onOpenTask }: { task: Task; tasks: Task[]; onOpenTask: (id: string) => void }) {
  const byId = new Map(tasks.map(t => [t.id, t]));
  const links: { relation: string; task: Task }[] = [
    ...(task.depends_on ?? []).map(id => byId.get(id)).filter((t): t is Task => !!t).map(t => ({ relation: 'Needs', task: t })),
    ...tasks.filter(t => t.depends_on?.includes(task.id)).map(t => ({ relation: 'Unblocks', task: t })),
  ];
  return (
    <section className="bd-detail-section">
      {task.details ? <p className="bd-prose">{task.details}</p> : <p className="bd-muted">No details yet.</p>}
      {task.labels?.length ? (
        <>
          <h3>Areas</h3>
          <LabelChips labels={task.labels} />
        </>
      ) : null}
      <h3>Reserved paths</h3>
      {task.paths.length ? (
        <ul className="bd-paths bd-paths-full">
          {task.paths.map(p => (
            <li key={p}>
              <code>{p}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p className="bd-muted">None - discussion or research. Agents must reserve paths before editing.</p>
      )}
      {links.length > 0 && (
        <>
          <h3>Dependencies</h3>
          <ul className="bd-dep-list">
            {links.map(({ relation, task: t }) => (
              <li key={t.id}>
                <span className="bd-muted">{relation}</span>{' '}
                <button type="button" className="bd-link" onClick={() => onOpenTask(t.id)}>
                  {t.title}
                </button>{' '}
                <StatusPill status={t.status} />
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
