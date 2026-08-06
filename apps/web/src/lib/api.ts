export function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export type HealthResponse = {
  status: string;
  service: string;
  version: string;
  timestamp: string;
};

export type ReadyResponse = {
  status: string;
  checks: {
    postgres: boolean;
    redis: boolean;
  };
  timestamp: string;
};

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${getApiBaseUrl()}/api/health`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Health check failed (${response.status})`);
  }
  return response.json();
}

export async function fetchReady(): Promise<ReadyResponse> {
  const response = await fetch(`${getApiBaseUrl()}/api/ready`, {
    cache: "no-store",
  });
  // 503 is a valid readiness payload
  if (!response.ok && response.status !== 503) {
    throw new Error(`Ready check failed (${response.status})`);
  }
  return response.json();
}
