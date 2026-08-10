export function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

/** Derive ws(s):// from the API base URL (or NEXT_PUBLIC_WS_URL override). */
export function getWsBaseUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return explicit.replace(/\/$/, "");
  const api = getApiBaseUrl();
  if (api.startsWith("https://")) return `wss://${api.slice("https://".length)}`;
  if (api.startsWith("http://")) return `ws://${api.slice("http://".length)}`;
  return `ws://${api}`;
}

export function agentRunWsUrl(runId: string): string {
  return `${getWsBaseUrl()}/ws/agent-runs/${runId}`;
}
