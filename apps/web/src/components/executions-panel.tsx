"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  cancelExecution,
  createExecution,
  fetchExecutionLogs,
  fetchExecutions,
  fetchGitHubConnections,
  type ExecutionJob,
  type RepositoryConnection,
} from "@/lib/api";
import { useMe } from "@/lib/auth";

const PRESETS: { label: string; command: string[] }[] = [
  { label: "python hello.py", command: ["python", "hello.py"] },
  { label: "python -m pytest -q", command: ["python", "-m", "pytest", "-q"] },
  { label: "python --version", command: ["python", "--version"] },
];

function statusLabel(status: string): string {
  return status
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function ExecutionsPanel() {
  const router = useRouter();
  const me = useMe();
  const queryClient = useQueryClient();
  const [connectionId, setConnectionId] = useState("");
  const [preset, setPreset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (me.isError && me.error instanceof ApiError && me.error.status === 401) {
      router.replace("/login");
    }
  }, [me.isError, me.error, router]);

  const connections = useQuery({
    queryKey: ["github", "connections"],
    queryFn: fetchGitHubConnections,
    enabled: Boolean(me.data),
    retry: false,
  });

  const executions = useQuery({
    queryKey: ["executions"],
    queryFn: fetchExecutions,
    enabled: Boolean(me.data),
    refetchInterval: (query) => {
      const rows = query.state.data as ExecutionJob[] | undefined;
      if (!rows) return false;
      const active = rows.some((row) =>
        ["queued", "starting", "cloning", "running"].includes(row.status),
      );
      return active ? 2000 : false;
    },
  });

  const logs = useQuery({
    queryKey: ["executions", selectedId, "logs"],
    queryFn: () => fetchExecutionLogs(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: () => {
      const job = executions.data?.find((row) => row.id === selectedId);
      if (!job) return false;
      return ["queued", "starting", "cloning", "running"].includes(job.status)
        ? 2000
        : false;
    },
  });

  useEffect(() => {
    const rows = connections.data ?? [];
    if (rows.length && !connectionId) {
      setConnectionId(rows[0].id);
    }
  }, [connections.data, connectionId]);

  useEffect(() => {
    if (!selectedId && executions.data?.[0]) {
      setSelectedId(executions.data[0].id);
    }
  }, [executions.data, selectedId]);

  const start = useMutation({
    mutationFn: createExecution,
    onSuccess: async (job) => {
      setError(null);
      setSelectedId(job.id);
      await queryClient.invalidateQueries({ queryKey: ["executions"] });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Unable to start execution");
    },
  });

  const cancel = useMutation({
    mutationFn: cancelExecution,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["executions"] });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Unable to cancel");
    },
  });

  const selected = useMemo(
    () => executions.data?.find((row) => row.id === selectedId) ?? null,
    [executions.data, selectedId],
  );

  if (me.isLoading) {
    return <p className="text-muted">Loading executions…</p>;
  }
  if (!me.data) {
    return <p className="text-muted">Redirecting to sign in…</p>;
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!connectionId) {
      setError("Connect a repository first.");
      return;
    }
    start.mutate({
      repository_connection_id: connectionId,
      command: PRESETS[preset].command,
    });
  }

  return (
    <section className="mx-auto w-full max-w-3xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl font-bold">
            Executions
          </h1>
          <p className="mt-2 text-muted">
            Run constrained commands in an isolated sandbox for a connected repository.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/github"
            className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-semibold"
          >
            GitHub
          </Link>
          <Link
            href="/dashboard"
            className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-semibold"
          >
            Dashboard
          </Link>
        </div>
      </div>

      {error ? <p className="text-sm text-bad">{error}</p> : null}

      <form
        onSubmit={onSubmit}
        className="space-y-4 rounded-xl border border-border bg-surface/80 p-5"
      >
        <label className="block text-sm text-muted" htmlFor="connection">
          Repository connection
        </label>
        <select
          id="connection"
          value={connectionId}
          onChange={(event) => setConnectionId(event.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
        >
          {(connections.data ?? []).length === 0 ? (
            <option value="">No connected repositories</option>
          ) : (
            (connections.data as RepositoryConnection[]).map((row) => (
              <option key={row.id} value={row.id}>
                {row.full_name}
              </option>
            ))
          )}
        </select>

        <label className="block text-sm text-muted" htmlFor="preset">
          Command
        </label>
        <select
          id="preset"
          value={preset}
          onChange={(event) => setPreset(Number(event.target.value))}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
        >
          {PRESETS.map((item, index) => (
            <option key={item.label} value={index}>
              {item.label}
            </option>
          ))}
        </select>

        <button
          type="submit"
          disabled={start.isPending || !connectionId}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-background disabled:opacity-60"
        >
          {start.isPending ? "Queuing…" : "Start execution"}
        </button>
      </form>

      <div className="rounded-xl border border-border bg-surface/80 p-5 space-y-3">
        <h2 className="font-semibold">Recent jobs</h2>
        {executions.isLoading ? <p className="text-muted">Loading…</p> : null}
        {!executions.isLoading && !(executions.data ?? []).length ? (
          <p className="text-sm text-muted">No executions yet.</p>
        ) : null}
        <ul className="space-y-2">
          {(executions.data ?? []).map((job) => (
            <li key={job.id}>
              <button
                type="button"
                onClick={() => setSelectedId(job.id)}
                className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${
                  selectedId === job.id ? "border-accent" : "border-border"
                }`}
              >
                <span className="font-medium">{statusLabel(job.status)}</span>
                <span className="ml-2 text-muted">{job.command.join(" ")}</span>
                {job.exit_code != null ? (
                  <span className="ml-2 text-xs text-muted">exit {job.exit_code}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      </div>

      {selected ? (
        <div className="space-y-3 rounded-xl border border-border bg-surface/80 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-semibold">{statusLabel(selected.status)}</p>
              <p className="text-xs text-muted">{selected.id}</p>
            </div>
            {["queued", "starting", "cloning", "running"].includes(selected.status) ? (
              <button
                type="button"
                onClick={() => cancel.mutate(selected.id)}
                disabled={cancel.isPending}
                className="rounded-lg border border-border px-3 py-1.5 text-sm font-semibold"
              >
                {cancel.isPending ? "Cancelling…" : "Cancel"}
              </button>
            ) : null}
          </div>
          {selected.error_message ? (
            <p className="text-sm text-bad">{selected.error_message}</p>
          ) : null}
          <div>
            <p className="text-xs uppercase tracking-wide text-muted">stdout</p>
            <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-background/80 p-3 font-mono text-xs">
              {logs.data?.stdout || "—"}
              {logs.data?.output_truncated ? "\n… truncated" : ""}
            </pre>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-muted">stderr</p>
            <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-background/80 p-3 font-mono text-xs">
              {logs.data?.stderr || "—"}
            </pre>
          </div>
        </div>
      ) : null}
    </section>
  );
}
