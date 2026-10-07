/* Step 2: read-only matrix of which member leads or supports each need in the brief. */
import { useMemo, useState } from 'react';
import { coverageState, type CoverageState, type Proposal } from '../../lib/teamBuilder';
import type { Profile } from '../ProfileEditor';

const COVERAGE_FLAGS: Record<CoverageState, { label: string; tone: string }> = {
  led: { label: 'led', tone: 'ok' },
  gap_if_declined: { label: 'gap if declined', tone: 'warn' },
  shared: { label: 'shared, no lead', tone: 'info' },
  gap: { label: 'no one covers this', tone: 'danger' },
};

// Needs the collapsed summary calls out by name, in this order.
const SUMMARY_FLAGS: { state: CoverageState; label: string }[] = [
  { state: 'gap_if_declined', label: 'gap if declined' },
  { state: 'shared', label: 'shared, no lead' },
  { state: 'gap', label: 'not covered' },
];

export function Coverage({ proposal, current }: { proposal: Proposal; current: Profile[] }) {
  const [open, setOpen] = useState(true);
  const currentIds = useMemo(() => new Set(current.map(p => p.id)), [current]);
  const inPlay = proposal.members.filter(m => m.status !== 'declined');
  const rows = proposal.coverage.map(row => ({ row, state: coverageState(row, currentIds, proposal.members) }));
  if (!rows.length) return null;
  const led = rows.filter(r => r.state === 'led').length;
  const columns = [
    ...current.map(p => ({ handle: p.id, name: p.name, proposed: false })),
    ...inPlay.map(m => ({ handle: m.id, name: m.name, proposed: m.status !== 'added' })),
  ];
  return (
    <details className="tb-coverage" open={open} onToggle={e => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <b>Expertise coverage</b>
        <span className="tb-flag tb-tone-ok">
          {led} of {rows.length} needs led
        </span>
        {SUMMARY_FLAGS.flatMap(({ state, label }) =>
          rows
            .filter(r => r.state === state)
            .map(({ row }) => (
              <span key={row.need} className={`tb-flag tb-tone-${COVERAGE_FLAGS[state].tone}`}>
                {row.need}: {label}
              </span>
            )),
        )}
        <span className="tb-toggle">{open ? 'Hide coverage' : 'Show coverage'}</span>
      </summary>
      <div className="tb-coverage-inner">
        <div className="tb-table-scroll">
          <table>
            <thead>
              <tr>
                <th className="tb-need">Need (from the brief)</th>
                {columns.map(c => (
                  <th key={c.handle}>
                    {c.name}
                    <small>@{c.handle}</small>
                    <span className={`tb-col-tag${c.proposed ? ' tb-col-new' : ''}`}>{c.proposed ? 'proposed' : 'current'}</span>
                  </th>
                ))}
                <th>
                  <span className="tb-sr">Status</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ row, state }) => (
                <tr key={row.need} className={state === 'gap_if_declined' || state === 'gap' ? 'tb-row-gap' : undefined}>
                  <td className="tb-need">
                    {row.need}
                    {row.detail && <small>{row.detail}</small>}
                  </td>
                  {columns.map(c => (
                    <td key={c.handle}>
                      {row.lead.includes(c.handle) ? (
                        <span className="tb-dot" title={`${c.name} leads`} aria-label="leads" />
                      ) : row.support.includes(c.handle) ? (
                        <span className="tb-ring" title={`${c.name} supports or reviews`} aria-label="supports" />
                      ) : null}
                    </td>
                  ))}
                  <td>
                    <span className={`tb-flag tb-tone-${COVERAGE_FLAGS[state].tone}`}>{COVERAGE_FLAGS[state].label}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="tb-legend">
          <span>
            <span className="tb-dot tb-dot-sm" aria-hidden="true" /> leads
          </span>
          <span>
            <span className="tb-ring tb-dot-sm" aria-hidden="true" /> supports or reviews
          </span>
          <span>Read-only. To change who covers what, use Ask for revision on a member.</span>
        </div>
      </div>
    </details>
  );
}
