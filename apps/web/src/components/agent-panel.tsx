"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  approveAgentRun,
  createAgentRun,
  cancelAgentRun,
  fetchAgentDiff,
  fetchAgentRuns,
  fetchAgentStatus,
  fetchAgentSteps,
  fetchGitHubConnections,
  rejectAgentRun,
  type AgentRun,
  type RepositoryConnection,
} from "@/lib/api";
import { useMe } from "@/lib/auth";
import { useAgentRunSocket } from "@/lib/use-agent-run-socket";

function statusLabel(status: string | undefined): string {
  return (status || "unknown")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatClock(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return "";
  }
}

export function AgentPanel() {
  const router = useRouter();
  const me = useMe();
  const queryClient = useQueryClient();
  const [connectionId, setConnectionId] = useState("");
  const [task, setTask] = useState(
    "Add a function that returns the sum of two integers and add tests.",
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    if (me.isError && me.error instanceof ApiError && me.error.status === 401) {
      router.replace("/login");
    }
  }, [me.isError, me.error, router]);

  const status = useQuery({
    queryKey: ["agent", "status"],
    queryFn: fetchAgentStatus,
    enabled: Boolean(me.data),
    retry: false,
  });

  const connections = useQuery({
    queryKey: ["github", "connections"],
    queryFn: fetchGitHubConnections,
    enabled: Boolean(me.data),
    retry: false,
  });

  const live = useAgentRunSocket({
    runId: selectedId,
    enabled: Boolean(selectedId && me.data),
    onDiffReady: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent-runs", selectedId, "diff"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
    onNeedRestSync: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
      if (selectedId) {
        void queryClient.invalidateQueries({ queryKey: ["agent-runs", selectedId, "steps"] });
      }
    },
  });

  const pollFallback = live.wsFailed || live.reconnecting || !live.connected;

  const runs = useQuery({
    queryKey: ["agent-runs"],
    queryFn: fetchAgentRuns,
    enabled: Boolean(me.data),
    refetchInterval: (query) => {
      const rows = query.state.data as AgentRun[] | undefined;
      if (!rows) return false;
      const active = rows.some((row) =>
        ["queued", "planning", "running", "validating", "publishing"].includes(row.status),
      );
      if (!active) return false;
      // WebSocket primary; poll when disconnected / reconnecting.
      return pollFallback ? 2000 : 8000;
    },
  });

  const steps = useQuery({
    queryKey: ["agent-runs", selectedId, "steps"],
    queryFn: () => fetchAgentSteps(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: pollFallback ? 2000 : false,
  });

  const diff = useQuery({
    queryKey: ["agent-runs", selectedId, "diff"],
    queryFn: () => fetchAgentDiff(selectedId!),
    enabled: Boolean(selectedId),
  });

  useEffect(() => {
    const rows = connections.data ?? [];
    if (rows.length && !connectionId) setConnectionId(rows[0].id);
  }, [connections.data, connectionId]);

  useEffect(() => {
    if (!selectedId && runs.data?.[0]) setSelectedId(runs.data[0].id);
  }, [runs.data, selectedId]);

  useEffect(() => {
    const el = logRef.current;
    if (!el || !stickToBottom.current) return;
    el.scrollTop = el.scrollHeight;
  }, [live.logs]);

  const start = useMutation({
    mutationFn: createAgentRun,
    onSuccess: async (run) => {
      setError(null);
      setSelectedId(run.id);
      await queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Unable to start agent run");
    },
  });

  const cancel = useMutation({
    mutationFn: cancelAgentRun,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
  });

  const approve = useMutation({
    mutationFn: (input: {
      id: string;
      artifact_hash: string;
      artifact_version: number;
      base_commit_sha: string;
    }) => approveAgentRun(input.id, input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to approve publication"),
  });

  const reject = useMutation({
    mutationFn: rejectAgentRun,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reject publication"),
  });

  const selected = useMemo(
    () => runs.data?.find((row) => row.id === selectedId) ?? null,
    [runs.data, selectedId],
  );

  const displayStatus = live.liveStatus || selected?.status || "";
  const files =
    live.changedFiles.length > 0
      ? live.changedFiles
      : ((selected?.changed_files as { path?: string; change_type?: string }[] | null) ?? []);

  if (me.isLoading || (me.data && status.isLoading)) {
    return <p className="text-muted">Loading agent…</p>;
  }
  if (!me.data) return <p className="text-muted">Redirecting to sign in…</p>;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!connectionId) {
      setError("Connect a repository first.");
      return;
    }
    start.mutate({ repository_connection_id: connectionId, task });
  }

  return (
    <section className="mx-auto w-full max-w-3xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl font-bold">
            Coding agent
          </h1>
          <p className="mt-2 text-muted">
            Bounded inspect → edit → validate loop with live workspace streaming.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/executions" className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-semibold">
            Executions
          </Link>
          <Link href="/dashboard" className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-semibold">
            Dashboard
          </Link>
        </div>
      </div>

      {error ? <p className="text-sm text-bad">{error}</p> : null}

      {!status.data?.configured ? (
        <div className="rounded-xl border border-border bg-surface/80 p-5">
          <p className="font-semibold">Agent LLM is not configured</p>
          <p className="mt-2 text-sm text-muted">
            Set <code>LLM_PROVIDER=fake</code> for local deterministic runs, or configure OpenAI.
            The rest of AgentDock still works.
          </p>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4 rounded-xl border border-border bg-surface/80 p-5">
          <label className="block text-sm text-muted" htmlFor="connection">
            Repository
          </label>
          <select
            id="connection"
            value={connectionId}
            onChange={(e) => setConnectionId(e.target.value)}
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
          <label className="block text-sm text-muted" htmlFor="task">
            Task
          </label>
          <textarea
            id="task"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            rows={4}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={start.isPending || !connectionId}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-background disabled:opacity-60"
          >
            {start.isPending ? "Queuing…" : "Start agent run"}
          </button>
        </form>
      )}

      <div className="space-y-3 rounded-xl border border-border bg-surface/80 p-5">
        <h2 className="font-semibold">Recent runs</h2>
        <ul className="space-y-2">
          {(runs.data ?? []).map((run) => (
            <li key={run.id}>
              <button
                type="button"
                onClick={() => setSelectedId(run.id)}
                className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${
                  selectedId === run.id ? "border-accent" : "border-border"
                }`}
              >
                <span className="font-medium">{statusLabel(run.status)}</span>
                <span className="ml-2 text-muted">{run.task.slice(0, 80)}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {selected ? (
        <div className="space-y-4 rounded-xl border border-border bg-surface/80 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-semibold" data-testid="run-status">
                {statusLabel(displayStatus)}
              </p>
              <p className="text-xs text-muted">
                steps {selected.steps_used}/{selected.max_steps}
                {" · "}
                {live.connected
                  ? "live"
                  : live.reconnecting
                    ? "reconnecting…"
                    : live.wsFailed
                      ? "polling fallback"
                      : "connecting…"}
              </p>
            </div>
            {["queued", "planning", "running", "validating"].includes(selected.status) ? (
              <button
                type="button"
                onClick={() => cancel.mutate(selected.id)}
                className="rounded-lg border border-border px-3 py-1.5 text-sm font-semibold"
              >
                Cancel
              </button>
            ) : null}
          </div>
          {selected.status === "awaiting_approval" && (selected.publication_status || "pending") === "pending" ? (
            <div className="rounded-lg border border-accent/50 bg-accent/10 p-4">
              <p className="font-semibold">Human approval required</p>
              <p className="mt-1 text-sm text-muted">
                Review the exact diff and validation result below. Approval will create a branch, commit, push it, and open a pull request.
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  disabled={
                    approve.isPending ||
                    reject.isPending ||
                    diff.data?.artifact_status !== "ready" ||
                    !diff.data?.artifact_hash ||
                    !diff.data?.artifact_version ||
                    !diff.data?.base_commit_sha
                  }
                  onClick={() => {
                    if (!diff.data?.artifact_hash || !diff.data.artifact_version || !diff.data.base_commit_sha) {
                      setError("The immutable publication artifact is unavailable. Refresh and try again.");
                      return;
                    }
                    approve.mutate({
                      id: selected.id,
                      artifact_hash: diff.data.artifact_hash,
                      artifact_version: diff.data.artifact_version,
                      base_commit_sha: diff.data.base_commit_sha,
                    });
                  }}
                  className="rounded-lg bg-accent px-3 py-1.5 text-sm font-semibold text-background disabled:opacity-50"
                >
                  {approve.isPending ? "Approving…" : "Approve & Create PR"}
                </button>
                <button
                  type="button"
                  disabled={approve.isPending || reject.isPending}
                  onClick={() => reject.mutate(selected.id)}
                  className="rounded-lg border border-border px-3 py-1.5 text-sm font-semibold disabled:opacity-50"
                >
                  {reject.isPending ? "Rejecting…" : "Reject"}
                </button>
              </div>
              <p className="mt-2 text-xs text-muted">
                Base commit: {selected.base_commit_sha ? selected.base_commit_sha.slice(0, 12) : "unknown"} · Artifact: {diff.data?.artifact_hash ? `${diff.data.artifact_hash.slice(0, 12)}…` : "unavailable"} · Changed files: {files.length}
              </p>
            </div>
          ) : null}
          {(selected.publication_status || "pending") !== "pending" ? (
            <p className="rounded-lg border border-border p-3 text-sm">
              Publication: {statusLabel(selected.publication_status || "pending")}
              {selected.github_pr_url ? <>{" · "}<a className="text-accent underline" href={selected.github_pr_url} target="_blank" rel="noreferrer">Open pull request</a></> : null}
            </p>
          ) : null}
          {selected.summary ? <p className="text-sm">{selected.summary}</p> : null}
          {selected.error_message ? <p className="text-sm text-bad">{selected.error_message}</p> : null}

          <div>
            <h3 className="text-sm font-semibold">Live activity</h3>
            <ul className="mt-2 space-y-2 text-sm" data-testid="live-activity">
              {live.activity.length === 0
                ? (steps.data ?? []).map((step) => (
                    <li key={step.id} className="border-t border-border pt-2 first:border-t-0 first:pt-0">
                      <span className="font-medium">{step.tool_name || step.kind}</span>
                      <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted">
                        {step.tool_result_summary || "—"}
                      </pre>
                    </li>
                  ))
                : live.activity.map((row, index) => (
                    <li key={`${row.at}-${index}`} className="text-muted">
                      <span className="font-mono text-xs">{formatClock(row.at)}</span>{" "}
                      <span className="text-foreground">{row.text}</span>
                    </li>
                  ))}
            </ul>
          </div>

          <div>
            <h3 className="text-sm font-semibold">Command output</h3>
            <pre
              ref={logRef}
              data-testid="command-output"
              onScroll={(e) => {
                const el = e.currentTarget;
                stickToBottom.current =
                  el.scrollHeight - el.scrollTop - el.clientHeight < 48;
              }}
              className="mt-1 max-h-64 overflow-auto rounded-lg bg-background/80 p-3 font-mono text-xs"
            >
              {live.logs.length === 0
                ? "—"
                : live.logs.map((line, index) => (
                    <span
                      key={index}
                      className={line.stream === "stderr" ? "text-bad" : undefined}
                    >
                      {line.text}
                    </span>
                  ))}
            </pre>
          </div>

          <div>
            <h3 className="text-sm font-semibold">Changed files</h3>
            <ul className="mt-2 text-sm text-muted" data-testid="changed-files">
              {files.length
                ? files.map((file, index) => (
                    <li key={`${file.path}-${index}`}>
                      {file.change_type}: {file.path}
                    </li>
                  ))
                : <li>None yet</li>}
            </ul>
          </div>

          <div>
            <h3 className="text-sm font-semibold">Diff</h3>
            {diff.data?.diff_truncated ? (
              <p className="text-xs text-muted">Preview truncated. Full change artifact preserved for publication.</p>
            ) : null}
            <pre className="mt-1 max-h-80 overflow-auto rounded-lg bg-background/80 p-3 font-mono text-xs">
              {diff.data?.diff_stat || ""}
              {"\n"}
              {diff.data?.diff_text || "—"}
            </pre>
          </div>
        </div>
      ) : null}
    </section>
  );
}
