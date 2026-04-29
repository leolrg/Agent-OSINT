// Server-side fetches run inside the web-next container, where `localhost`
// resolves to the container itself. Use the docker-internal hostname
// (`api-py`) on the server, but keep `NEXT_PUBLIC_API_BASE` for browser-side
// calls so user-host URLs (http://localhost:8000) still work.
const BASE = typeof window === 'undefined'
  ? (process.env.API_BASE_INTERNAL ?? 'http://api-py:8000')
  : (process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000');

export type ParamField = {
  name: string;
  label: string;
  type: 'select' | 'text' | 'int' | 'float' | 'bool';
  default: unknown;
  options?: string[];
  help?: string;
  advanced?: boolean;
  min?: number;
  max?: number;
};

export type AgentManifest = {
  name: string;
  display_name: string;
  description: string;
  estimated_duration: string;
  params: ParamField[];
};

export type AgentCatalog = {
  agents: AgentManifest[];
  common_params: ParamField[];
};

export async function fetchAgentCatalog(): Promise<AgentCatalog> {
  const r = await fetch(`${BASE}/api/agents`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`Failed to fetch /api/agents: ${r.status}`);
  return r.json();
}
