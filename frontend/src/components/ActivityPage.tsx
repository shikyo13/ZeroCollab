/* Activity page: deployments, then the legacy runtime panel (agent requests,
 * tool activity and the Changes view). */
import { legacy } from '../legacy';
import { Deployments, type DeploymentFeed } from './Deployments';
import { LegacySlot } from './LegacySlot';

export function ActivityPage({ deployments }: { deployments: DeploymentFeed }) {
  return (
    <>
      <div className="workspace-page-heading">
        <div>
          <h1 id="activity-title">Activity</h1>
          <p>Deployments, agent requests and tool calls for the active project.</p>
        </div>
      </div>
      <Deployments feed={deployments} />
      <LegacySlot node={legacy.runtime} />
    </>
  );
}
