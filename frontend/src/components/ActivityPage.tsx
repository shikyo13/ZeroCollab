/* Activity & controls page: deployments, then the legacy runtime panel (agent
 * requests, tool activity and the Changes view). */
import { legacy } from '../legacy';
import { Deployments, type DeploymentFeed } from './Deployments';
import { LegacySlot } from './LegacySlot';

export function ActivityPage({ deployments, onManageTeam }: { deployments: DeploymentFeed; onManageTeam: () => void }) {
  return (
    <>
      <div className="workspace-page-heading">
        <div>
          <span className="workspace-eyebrow">ROOM OPERATIONS</span>
          <h1 id="activity-title">Activity & controls</h1>
          <p>Agent requests, recent tool calls and the Changes view. Repositories are in Projects; agents and models are in Team.</p>
        </div>
        <button onClick={onManageTeam}>Manage team</button>
      </div>
      <Deployments feed={deployments} />
      <LegacySlot node={legacy.runtime} />
    </>
  );
}
