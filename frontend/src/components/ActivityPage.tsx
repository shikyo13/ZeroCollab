/* Server activity retains the existing runtime log and Changes controls. */
import { legacy } from '../legacy';
import { LegacySlot } from './LegacySlot';

export function ActivityPage() {
  return <>
    <div className="workspace-page-heading"><div><h2 id="activity-title">Activity</h2>
      <p>Agent requests, tool calls and changes for the active project.</p></div></div>
    <LegacySlot node={legacy.runtime} />
  </>;
}
