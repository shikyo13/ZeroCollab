/* Activity retains compact deployment results; approvals and full history live in Server. */
import { legacy } from '../legacy';
import { openServer } from '../workspace/useWorkspaceRoute';
import type { DeploymentFeed } from './Deployments';
import { LegacySlot } from './LegacySlot';

export function ActivityPage({ deployments }: { deployments: DeploymentFeed }) {
  return <>
    <div className="workspace-page-heading"><div><h1 id="activity-title">Activity</h1>
      <p>Agent requests, tool calls and recent results for the active project.</p></div></div>
    <section className="activity-deployments bd-panel" aria-labelledby="activity-deployments-title">
      <div className="deploy-heading"><h2 id="activity-deployments-title">Recent updates</h2>
        <button className="bd-btn" onClick={() => openServer('history')}>View update history</button></div>
      {deployments.error && <p role="status" className="bd-muted">Update results could not be refreshed.</p>}
      {!deployments.data && !deployments.error && <p className="bd-muted">Loading update results...</p>}
      {deployments.data && !deployments.data.receipts.length && <p className="bd-muted">No deployment results yet.</p>}
      <ul>{deployments.data?.receipts.slice(0, 3).map(receipt => <li key={receipt.id}>
        <button onClick={() => openServer('history')}><span>{receipt.status.replaceAll('_', ' ')} <code>{receipt.release_id}</code></span>
          <small>{receipt.started || 'Time not recorded'}</small></button>
      </li>)}</ul>
    </section>
    <LegacySlot node={legacy.runtime} />
  </>;
}
