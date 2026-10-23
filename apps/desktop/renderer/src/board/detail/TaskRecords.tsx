/* Evidence, reviews and follow-ups attached to one task. */
import { EVIDENCE_KINDS, type Followup, type Review, type Task } from '../../lib/types';
import { FollowupForm, FollowupItem, RequestReviewForm, ReviewItem, isOpenFollowup, isOpenReview } from '../Coordination';
import type { Agents } from '../shared';

export function TaskEvidence({ task }: { task: Task }) {
  if (!(Object.keys(task.evidence ?? {}).length > 0 || task.status === 'review' || task.status === 'done')) return null;
  return (
    <section className="bd-detail-section" aria-labelledby="bd-task-evidence">
      <h3 id="bd-task-evidence">Evidence</h3>
      <dl className="bd-facts">
        {EVIDENCE_KINDS.map(([kind, label]) => (
          <div key={kind} className="bd-facts-row">
            <dt>{label}</dt>
            <dd className={task.evidence?.[kind] ? '' : 'bd-muted'}>{task.evidence?.[kind] || 'Not recorded'}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function TaskReviews({ task, reviews, agents, channel }: { task: Task; reviews: Review[]; agents: Agents; channel: string }) {
  return (
    <section className="bd-detail-section" aria-labelledby="bd-task-reviews">
      <h3 id="bd-task-reviews">Reviews ({reviews.filter(isOpenReview).length} open)</h3>
      {reviews.length > 0 && (
        <ul className="bd-records">
          {reviews.map(r => (
            <ReviewItem key={r.id} review={r} agents={agents} channel={channel} />
          ))}
        </ul>
      )}
      <details className="bd-disclosure">
        <summary>Request a review</summary>
        <RequestReviewForm task={task} agents={agents} channel={channel} />
      </details>
    </section>
  );
}

export function TaskFollowups({ task, followups, agents, channel }: { task: Task; followups: Followup[]; agents: Agents; channel: string }) {
  return (
    <section className="bd-detail-section" aria-labelledby="bd-task-followups">
      <h3 id="bd-task-followups">Follow-ups ({followups.filter(isOpenFollowup).length} open)</h3>
      {followups.length > 0 && (
        <ul className="bd-records">
          {followups.map(f => (
            <FollowupItem key={f.id} followup={f} agents={agents} channel={channel} />
          ))}
        </ul>
      )}
      <details className="bd-disclosure">
        <summary>Add a follow-up</summary>
        <FollowupForm agents={agents} channel={channel} taskId={task.id} />
      </details>
    </section>
  );
}
