export function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

type ErrorBody = {
  detail?: string | { msg?: string }[];
};

async function parseError(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status})`;
  try {
    const body = (await response.json()) as ErrorBody;
    if (typeof body.detail === "string") {
      message = body.detail;
    } else if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      message = body.detail[0].msg;
    }
  } catch {
    // ignore non-JSON errors
  }
  return new ApiError(response.status, message);
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
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

export type User = {
  id: string;
  email: string;
  display_name: string;
};

export type AuthResponse = {
  user: User;
};

export async function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/api/health");
}

export async function fetchReady(): Promise<ReadyResponse> {
  const response = await fetch(`${getApiBaseUrl()}/api/ready`, {
    cache: "no-store",
    credentials: "include",
  });
  if (!response.ok && response.status !== 503) {
    throw await parseError(response);
  }
  return response.json();
}

export async function fetchMe(): Promise<User> {
  return apiFetch<User>("/api/auth/me");
}

export async function register(input: {
  email: string;
  password: string;
  display_name: string;
}): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function login(input: {
  email: string;
  password: string;
}): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function logout(): Promise<void> {
  await apiFetch<void>("/api/auth/logout", { method: "POST" });
}

export type GitHubStatus = {
  configured: boolean;
  linked: boolean;
  github_login: string | null;
  installation_count: number;
  connection_count: number;
};

export type GitHubConnect = {
  authorize_url: string;
};

export type GitHubInstallation = {
  id: string;
  github_installation_id: number;
  account_login: string;
  account_type: string;
  repository_selection: string;
  suspended: boolean;
  created_at: string;
};

export type GitHubRepository = {
  id: number;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  owner: string;
};

export type GitHubRepositoryList = {
  total_count: number;
  page: number;
  per_page: number;
  repositories: GitHubRepository[];
};

export type RepositoryConnection = {
  id: string;
  github_repository_id: number;
  installation_id: string;
  owner: string;
  name: string;
  full_name: string;
  default_branch: string;
  private: boolean;
  html_url: string;
  is_active: boolean;
  created_at: string;
};

export async function fetchGitHubStatus(): Promise<GitHubStatus> {
  return apiFetch<GitHubStatus>("/api/github/status");
}

export async function fetchGitHubConnect(): Promise<GitHubConnect> {
  return apiFetch<GitHubConnect>("/api/github/connect");
}

export async function fetchGitHubInstallations(): Promise<GitHubInstallation[]> {
  return apiFetch<GitHubInstallation[]>("/api/github/installations");
}

export async function fetchGitHubRepositories(
  installationId: string,
  page = 1,
): Promise<GitHubRepositoryList> {
  const params = new URLSearchParams({
    installation_id: installationId,
    page: String(page),
    per_page: "30",
  });
  return apiFetch<GitHubRepositoryList>(`/api/github/repositories?${params}`);
}

export async function connectGitHubRepository(input: {
  installation_id: string;
  github_repository_id: number;
}): Promise<RepositoryConnection> {
  return apiFetch<RepositoryConnection>(
    `/api/github/repositories/${input.github_repository_id}/connect`,
    {
      method: "POST",
      body: JSON.stringify(input),
    },
  );
}

export async function fetchGitHubConnections(): Promise<RepositoryConnection[]> {
  return apiFetch<RepositoryConnection[]>("/api/github/connections");
}

export async function disconnectGitHubConnection(
  connectionId: string,
): Promise<void> {
  await apiFetch<void>(`/api/github/connections/${connectionId}`, {
    method: "DELETE",
  });
}

export type ExecutionJob = {
  id: string;
  repository_connection_id: string;
  agent_session_id: string | null;
  status: string;
  command: string[];
  working_directory: string | null;
  exit_code: number | null;
  error_type: string | null;
  error_message: string | null;
  output_truncated: boolean;
  cancel_requested: boolean;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  approval_status: string;
  approved_at: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  base_commit_sha: string | null;
  diff_hash: string | null;
  publication_status: string;
  branch_name: string | null;
  commit_sha: string | null;
  github_pr_number: number | null;
  github_pr_id: number | null;
  github_pr_url: string | null;
};

export type ExecutionLogs = {
  id: string;
  status: string;
  stdout: string;
  stderr: string;
  output_truncated: boolean;
  exit_code: number | null;
};

export async function createExecution(input: {
  repository_connection_id: string;
  command: string[];
  working_directory?: string | null;
  agent_session_id?: string | null;
}): Promise<ExecutionJob> {
  return apiFetch<ExecutionJob>("/api/executions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function fetchExecutions(): Promise<ExecutionJob[]> {
  return apiFetch<ExecutionJob[]>("/api/executions");
}

export async function fetchExecution(id: string): Promise<ExecutionJob> {
  return apiFetch<ExecutionJob>(`/api/executions/${id}`);
}

export async function fetchExecutionLogs(id: string): Promise<ExecutionLogs> {
  return apiFetch<ExecutionLogs>(`/api/executions/${id}/logs`);
}

export async function cancelExecution(id: string): Promise<ExecutionJob> {
  return apiFetch<ExecutionJob>(`/api/executions/${id}/cancel`, {
    method: "POST",
  });
}

export type AgentStatus = {
  configured: boolean;
  provider: string;
  model: string;
};

export type AgentRun = {
  id: string;
  repository_connection_id: string;
  agent_session_id: string | null;
  status: string;
  task: string;
  model_provider: string;
  model_name: string;
  max_steps: number;
  steps_used: number;
  tool_calls_used: number;
  cancel_requested: boolean;
  summary: string | null;
  result_status: string | null;
  changed_files: { path?: string; change_type?: string }[] | null;
  validation: Record<string, unknown> | null;
  diff_truncated: boolean;
  error_type: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  approval_status: string;
  approved_at: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  base_commit_sha: string | null;
  diff_hash: string | null;
  publication_status: string;
  branch_name: string | null;
  commit_sha: string | null;
  github_pr_number: number | null;
  github_pr_id: number | null;
  github_pr_url: string | null;
};

export type AgentStep = {
  id: string;
  step_number: number;
  kind: string;
  tool_name: string | null;
  tool_input: Record<string, unknown> | null;
  tool_result_summary: string | null;
  duration_ms: number | null;
  created_at: string;
};

export type AgentDiff = {
  id: string;
  status: string;
  diff_stat: string;
  diff_text: string;
  diff_truncated: boolean;
  changed_files: { path?: string; change_type?: string }[];
  diff_hash: string | null;
  base_commit_sha: string | null;
};

export async function fetchAgentStatus(): Promise<AgentStatus> {
  return apiFetch<AgentStatus>("/api/agent-runs/status");
}

export async function createAgentRun(input: {
  repository_connection_id: string;
  task: string;
  agent_session_id?: string | null;
}): Promise<AgentRun> {
  return apiFetch<AgentRun>("/api/agent-runs", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function fetchAgentRuns(): Promise<AgentRun[]> {
  return apiFetch<AgentRun[]>("/api/agent-runs");
}

export async function fetchAgentRun(id: string): Promise<AgentRun> {
  return apiFetch<AgentRun>(`/api/agent-runs/${id}`);
}

export async function fetchAgentSteps(id: string): Promise<AgentStep[]> {
  return apiFetch<AgentStep[]>(`/api/agent-runs/${id}/steps`);
}

export async function fetchAgentDiff(id: string): Promise<AgentDiff> {
  return apiFetch<AgentDiff>(`/api/agent-runs/${id}/diff`);
}

export async function cancelAgentRun(id: string): Promise<AgentRun> {
  return apiFetch<AgentRun>(`/api/agent-runs/${id}/cancel`, { method: "POST" });
}

export async function approveAgentRun(id: string): Promise<AgentRun> {
  return apiFetch<AgentRun>(`/api/agent-runs/${id}/approve`, { method: "POST" });
}

export async function rejectAgentRun(id: string): Promise<AgentRun> {
  return apiFetch<AgentRun>(`/api/agent-runs/${id}/reject`, { method: "POST" });
}
