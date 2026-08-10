"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  ApiError,
  connectGitHubRepository,
  disconnectGitHubConnection,
  fetchGitHubConnect,
  fetchGitHubConnections,
  fetchGitHubInstallations,
  fetchGitHubRepositories,
  fetchGitHubStatus,
  type GitHubInstallation,
  type GitHubRepository,
  type RepositoryConnection,
} from "@/lib/api";
import { useMe } from "@/lib/auth";

const statusKey = ["github", "status"] as const;
const installationsKey = ["github", "installations"] as const;
const connectionsKey = ["github", "connections"] as const;

function repositoriesKey(installationId: string) {
  return ["github", "repositories", installationId] as const;
}

export function GitHubPanel() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const me = useMe();
  const [selectedInstallationId, setSelectedInstallationId] = useState<string>("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    if (me.isError && me.error instanceof ApiError && me.error.status === 401) {
      router.replace("/login");
    }
  }, [me.isError, me.error, router]);

  useEffect(() => {
    const error = searchParams.get("error");
    if (error) {
      setBanner(`GitHub connect failed (${error.replaceAll("_", " ")}).`);
      return;
    }
    if (searchParams.get("installed") === "1") {
      setBanner("GitHub App installation linked.");
      void queryClient.invalidateQueries({ queryKey: statusKey });
      void queryClient.invalidateQueries({ queryKey: installationsKey });
    }
  }, [searchParams, queryClient]);

  const status = useQuery({
    queryKey: statusKey,
    queryFn: fetchGitHubStatus,
    enabled: Boolean(me.data),
    retry: false,
  });

  const installations = useQuery({
    queryKey: installationsKey,
    queryFn: fetchGitHubInstallations,
    enabled: Boolean(me.data) && Boolean(status.data?.configured),
    retry: false,
  });

  const connections = useQuery({
    queryKey: connectionsKey,
    queryFn: fetchGitHubConnections,
    enabled: Boolean(me.data) && Boolean(status.data?.configured),
    retry: false,
  });

  useEffect(() => {
    const rows = installations.data ?? [];
    if (!rows.length) {
      setSelectedInstallationId("");
      return;
    }
    if (!selectedInstallationId || !rows.some((row) => row.id === selectedInstallationId)) {
      setSelectedInstallationId(rows[0].id);
    }
  }, [installations.data, selectedInstallationId]);

  const repositories = useQuery({
    queryKey: repositoriesKey(selectedInstallationId),
    queryFn: () => fetchGitHubRepositories(selectedInstallationId),
    enabled: Boolean(selectedInstallationId),
    retry: false,
  });

  const connectMutation = useMutation({
    mutationFn: fetchGitHubConnect,
    onSuccess: (data) => {
      window.location.href = data.authorize_url;
    },
    onError: (error) => {
      setActionError(error instanceof ApiError ? error.message : "Unable to start GitHub connect");
    },
  });

  const linkRepo = useMutation({
    mutationFn: connectGitHubRepository,
    onSuccess: async () => {
      setActionError(null);
      await queryClient.invalidateQueries({ queryKey: connectionsKey });
      await queryClient.invalidateQueries({ queryKey: statusKey });
    },
    onError: (error) => {
      setActionError(error instanceof ApiError ? error.message : "Unable to connect repository");
    },
  });

  const unlinkRepo = useMutation({
    mutationFn: disconnectGitHubConnection,
    onSuccess: async () => {
      setActionError(null);
      await queryClient.invalidateQueries({ queryKey: connectionsKey });
      await queryClient.invalidateQueries({ queryKey: statusKey });
    },
    onError: (error) => {
      setActionError(error instanceof ApiError ? error.message : "Unable to disconnect repository");
    },
  });

  if (me.isLoading || (me.data && status.isLoading)) {
    return <p className="text-muted">Loading GitHub integration…</p>;
  }

  if (!me.data) {
    return <p className="text-muted">Redirecting to sign in…</p>;
  }

  if (status.isError) {
    const message =
      status.error instanceof ApiError ? status.error.message : "Unable to load GitHub status";
    return <p className="text-bad">{message}</p>;
  }

  const github = status.data;
  if (!github) {
    return <p className="text-muted">Unable to load GitHub status.</p>;
  }

  const connectedIds = new Set(
    (connections.data ?? []).map((row) => row.github_repository_id),
  );
  const selected = (installations.data ?? []).find((row) => row.id === selectedInstallationId);

  return (
    <section className="mx-auto w-full max-w-3xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl font-bold">
            GitHub
          </h1>
          <p className="mt-2 text-muted">
            Connect the GitHub App and select repositories for AgentDock.
          </p>
        </div>
        <Link
          href="/dashboard"
          className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-semibold"
        >
          Dashboard
        </Link>
      </div>

      {banner ? <p className="rounded-lg border border-border bg-surface/80 px-4 py-3 text-sm">{banner}</p> : null}
      {actionError ? <p className="text-sm text-bad">{actionError}</p> : null}

      {!github.configured ? (
        <div className="rounded-xl border border-border bg-surface/80 p-5">
          <p className="font-semibold">GitHub integration is not configured</p>
          <p className="mt-2 text-sm text-muted">
            Set GitHub App environment variables on the API to enable connect. The rest of AgentDock
            still works without them.
          </p>
        </div>
      ) : !github.linked ? (
        <div className="rounded-xl border border-border bg-surface/80 p-5 space-y-4">
          <p className="text-sm text-muted">
            Link your GitHub identity, then install the AgentDock GitHub App on an account or
            organization.
          </p>
          <button
            type="button"
            onClick={() => {
              setActionError(null);
              connectMutation.mutate();
            }}
            disabled={connectMutation.isPending}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-background"
          >
            {connectMutation.isPending ? "Redirecting…" : "Connect GitHub"}
          </button>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="rounded-xl border border-border bg-surface/80 p-5">
            <p className="text-sm text-muted">Linked as</p>
            <p className="mt-1 font-semibold">@{github.github_login}</p>
            <p className="mt-2 text-sm text-muted">
              {github.installation_count} installation
              {github.installation_count === 1 ? "" : "s"} · {github.connection_count} connected
              {github.connection_count === 1 ? " repository" : " repositories"}
            </p>
            <button
              type="button"
              onClick={() => {
                setActionError(null);
                connectMutation.mutate();
              }}
              disabled={connectMutation.isPending}
              className="mt-4 rounded-lg border border-border px-4 py-2 text-sm font-semibold"
            >
              {connectMutation.isPending ? "Redirecting…" : "Install or re-authorize"}
            </button>
          </div>

          <InstallationsBlock
            loading={installations.isLoading}
            error={installations.error}
            installations={installations.data ?? []}
            selectedId={selectedInstallationId}
            onSelect={setSelectedInstallationId}
          />

          {selected ? (
            <RepositoriesBlock
              installation={selected}
              loading={repositories.isLoading}
              error={repositories.error}
              repositories={repositories.data?.repositories ?? []}
              connectedIds={connectedIds}
              connectingId={
                linkRepo.isPending ? linkRepo.variables?.github_repository_id ?? null : null
              }
              onConnect={(repo) => {
                setActionError(null);
                linkRepo.mutate({
                  installation_id: selected.id,
                  github_repository_id: repo.id,
                });
              }}
            />
          ) : null}

          <ConnectionsBlock
            loading={connections.isLoading}
            error={connections.error}
            connections={connections.data ?? []}
            disconnectingId={unlinkRepo.isPending ? unlinkRepo.variables ?? null : null}
            onDisconnect={(id) => {
              setActionError(null);
              unlinkRepo.mutate(id);
            }}
          />
        </div>
      )}
    </section>
  );
}

function InstallationsBlock({
  loading,
  error,
  installations,
  selectedId,
  onSelect,
}: {
  loading: boolean;
  error: unknown;
  installations: GitHubInstallation[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  if (loading) {
    return <p className="text-muted">Loading installations…</p>;
  }
  if (error) {
    const message = error instanceof ApiError ? error.message : "Unable to load installations";
    return <p className="text-bad">{message}</p>;
  }
  if (!installations.length) {
    return (
      <div className="rounded-xl border border-border bg-surface/80 p-5">
        <p className="font-semibold">No installations yet</p>
        <p className="mt-2 text-sm text-muted">
          Install the GitHub App on a personal account or organization to list repositories.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-surface/80 p-5 space-y-3">
      <h2 className="font-semibold">Installations</h2>
      <label className="block text-sm text-muted" htmlFor="installation">
        Account or organization
      </label>
      <select
        id="installation"
        value={selectedId}
        onChange={(event) => onSelect(event.target.value)}
        className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
      >
        {installations.map((row) => (
          <option key={row.id} value={row.id}>
            {row.account_login} ({row.account_type}
            {row.suspended ? ", suspended" : ""})
          </option>
        ))}
      </select>
    </div>
  );
}

function RepositoriesBlock({
  installation,
  loading,
  error,
  repositories,
  connectedIds,
  connectingId,
  onConnect,
}: {
  installation: GitHubInstallation;
  loading: boolean;
  error: unknown;
  repositories: GitHubRepository[];
  connectedIds: Set<number>;
  connectingId: number | null;
  onConnect: (repo: GitHubRepository) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface/80 p-5 space-y-3">
      <h2 className="font-semibold">Repositories · {installation.account_login}</h2>
      {installation.suspended ? (
        <p className="text-sm text-bad">This installation is suspended on GitHub.</p>
      ) : null}
      {loading ? <p className="text-muted">Loading repositories…</p> : null}
      {error ? (
        <p className="text-bad">
          {error instanceof ApiError ? error.message : "Unable to load repositories"}
        </p>
      ) : null}
      {!loading && !error && !repositories.length ? (
        <p className="text-sm text-muted">No repositories accessible through this installation.</p>
      ) : null}
      <ul className="space-y-2">
        {repositories.map((repo) => {
          const connected = connectedIds.has(repo.id);
          return (
            <li
              key={repo.id}
              className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 first:border-t-0 first:pt-0"
            >
              <div>
                <p className="font-medium">{repo.full_name}</p>
                <p className="text-xs text-muted">
                  {repo.private ? "Private" : "Public"} · {repo.default_branch}
                </p>
              </div>
              {connected ? (
                <span className="text-xs font-semibold text-muted">Connected</span>
              ) : (
                <button
                  type="button"
                  disabled={installation.suspended || connectingId === repo.id}
                  onClick={() => onConnect(repo)}
                  className="rounded-lg border border-border px-3 py-1.5 text-sm font-semibold"
                >
                  {connectingId === repo.id ? "Connecting…" : "Connect"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ConnectionsBlock({
  loading,
  error,
  connections,
  disconnectingId,
  onDisconnect,
}: {
  loading: boolean;
  error: unknown;
  connections: RepositoryConnection[];
  disconnectingId: string | null;
  onDisconnect: (id: string) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface/80 p-5 space-y-3">
      <h2 className="font-semibold">Connected repositories</h2>
      <p className="text-sm text-muted">
        Disconnect removes the AgentDock link only. It does not uninstall the GitHub App.
      </p>
      {loading ? <p className="text-muted">Loading connections…</p> : null}
      {error ? (
        <p className="text-bad">
          {error instanceof ApiError ? error.message : "Unable to load connections"}
        </p>
      ) : null}
      {!loading && !error && !connections.length ? (
        <p className="text-sm text-muted">No repositories connected yet.</p>
      ) : null}
      <ul className="space-y-2">
        {connections.map((row) => (
          <li
            key={row.id}
            className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 first:border-t-0 first:pt-0"
          >
            <div>
              <a
                href={row.html_url}
                target="_blank"
                rel="noreferrer"
                className="font-medium underline-offset-2 hover:underline"
              >
                {row.full_name}
              </a>
              <p className="text-xs text-muted">
                {row.private ? "Private" : "Public"} · {row.default_branch}
              </p>
            </div>
            <button
              type="button"
              disabled={disconnectingId === row.id}
              onClick={() => onDisconnect(row.id)}
              className="rounded-lg border border-border px-3 py-1.5 text-sm font-semibold"
            >
              {disconnectingId === row.id ? "Disconnecting…" : "Disconnect"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
