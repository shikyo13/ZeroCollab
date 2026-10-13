import { ApiError, runtime } from '../lib/api';

export interface PinnedSkill { name: string; sha256: string }
export interface RoleCurrent {
  core: string | null;
  core_version: number | null;
  skills: PinnedSkill[] | null;
  approved_by: string | null;
  approved_at: string | null;
  active: boolean;
}
export interface RoleDraft {
  core: string;
  core_version: number;
  words: number;
  skills: PinnedSkill[];
  skill_details: { name: string; version: string; description: string }[];
  digest: string;
}
export interface RoleResponse { current: RoleCurrent; draft: RoleDraft | null; draft_error?: string }
export type RoleAction = 'approve' | 'revoke';

const route = (name: string) => `/profiles/${encodeURIComponent(name)}/core`;
export const readRole = (name: string) => runtime<RoleResponse>(route(name));
export const changeRole = (name: string, action: RoleAction, digest?: string) =>
  runtime<RoleCurrent & { note: string }>(route(name), { action, ...(action === 'approve' ? { digest } : {}) });

export function staleRole(error: unknown): boolean {
  // The approved server route returns 400 today; also accept a future conflict
  // status, but never classify an unrelated refusal as a stale approval.
  return error instanceof ApiError && [400, 409].includes(error.status)
    && error.message.includes('changed since you reviewed it');
}

export function matchesDraft(current: RoleCurrent, draft: RoleDraft): boolean {
  return current.active && current.core === draft.core && current.core_version === draft.core_version
    && current.skills?.length === draft.skills.length
    && draft.skills.every((skill, index) => current.skills?.[index].name === skill.name
      && current.skills[index].sha256 === skill.sha256);
}
