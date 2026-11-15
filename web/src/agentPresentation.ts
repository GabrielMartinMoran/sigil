import type { AgentView } from './types';

export function agentLabel(name: string): string {
  return name.replace(/[-_]/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function agentOptions(profiles: AgentView[]) {
  return profiles
    .filter((profile) => profile.role === 'agent')
    .map((profile) => ({
      name: profile.name,
      label: agentLabel(profile.name),
      description: profile.description,
      origin: profile.source === 'builtin' ? 'built-in' : profile.source,
    }));
}
