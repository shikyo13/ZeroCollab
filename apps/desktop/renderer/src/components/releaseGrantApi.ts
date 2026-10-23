import { ApiError, runtime } from '../lib/api';

export type ReleaseGrant = {
  version: 1; revision: number; enabled: boolean; project?: string;
  profile?: 'astra'; lapse_reason?: string; changed_at?: number;
};

export async function readReleaseGrant(): Promise<ReleaseGrant> {
  const value = await runtime<ReleaseGrant>('/deployments/grant');
  if (!value || value.version !== 1 || !Number.isSafeInteger(value.revision) || value.revision < 0 ||
      typeof value.enabled !== 'boolean' || (value.enabled && (value.profile !== 'astra' || typeof value.project !== 'string'))) {
    throw new Error('The release permission response is invalid. Refresh before changing it.');
  }
  return value;
}

export async function changeReleaseGrant(enabled: boolean, revision: number): Promise<void> {
  await runtime('/deployments/grant', { enabled, expected_revision: revision });
}

export const isStaleGrant = (error: unknown) => error instanceof ApiError && error.status === 409;
