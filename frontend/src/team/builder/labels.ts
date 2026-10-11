/* Display labels and small formatters shared by the team builder steps. */
import type { BriefSource, ProposedMember } from '../../lib/teamBuilder';
import type { ProviderInfo } from '../ProfileEditor';

export const SOURCE_LABELS: Record<BriefSource, string> = { spec: 'Project spec', goal: 'Shared goal', text: 'Your own description', agent: 'One agent' };

export const plural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`;

export function providerSummary(member: ProposedMember, providers: ProviderInfo[]): string {
  const provider = providers.find(p => p.id === member.template);
  const label = provider?.label || member.template || 'Provider';
  if (!member.model && !member.effort) return `${label} · connection defaults`;
  const model = provider?.controls.models.find(m => m.value === member.model)?.label || member.model || 'default model';
  return `${label} · ${model} · effort ${member.effort || 'default'}`;
}

export function effortLabel(value: string): string {
  return value === 'xhigh' ? 'Extra high (xhigh)' : value[0].toUpperCase() + value.slice(1);
}
