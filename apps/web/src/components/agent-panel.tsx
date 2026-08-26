"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  approveAgentRun,
  cancelAgentRun,
  createAgentRun,
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
import { readableStatus, StatusPill } from "@/components/status-pill";

function statusLabel(status: string | undefined): string {
  return readableStatus(status);
}

function formatClock(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

const WORKFLOW_STATES = [
  { key: "queued", label: "Queued", detail: "Durable work item accepted" },
  { key: "planning", label: "Planning", detail: "Agent is selecting bounded tools" },
  { key: "running", label: "Inspect & edit", detail: "Sandbox changes are being made" },
  { key: "validating", label: "Validating", detail: "Checks run before review" },
  { key: "awaiting_approval", label: "Review", detail: "Human approval is required" },
  { key: "publishing", label: "Publishing", detail: "Trusted service is opening the PR" },
  { key: "succeeded", label: "Published", detail: "Pull request is ready" },
] as const;

function statusExplanation(status: string): string {
  const messages: Record<string, string> = {
    queued: "Your task is safely queued. A worker will prepare an isolated checkout.",
    planning: "The agent is planning the next bounded operation inside the sandbox.",
    running: "The agent is inspecting and editing files. Credentials remain outside the sandbox.",
    validating: "Validation is running. Publication stays blocked until checks finish.",
    awaiting_approval: "Review the exact diff and validation result before allowing publication.",
    publishing: "Your approval was recorded. A trusted service is creating the branch, commit, and PR.",
    succeeded: "The pull request was created from the approved immutable artifact.",
    failed: "The run stopped safely. Review the error and retry with a smaller task if needed.",
    cancelled: "The run was cancelled. No publication was attempted.",
    timed_out: "The sandbox reached its time limit. No publication was attempted.",
    repository_revoked: "GitHub access was revoked, so the run stopped before publication.",
  };
  return messages[status] || "AgentDock is recovering the latest durable workflow state.";
}

function safeRunError(run: AgentRun): string | null {
  if (!run.error_type && !run.error_message) return null;
  const messages: Record<string, string> = {
    artifact_too_large: "The artifact is larger than the configured publication limit. Reduce the task scope and try again.",
    artifact_integrity_failed: "The artifact integrity check failed. The run is safe and publication is blocked; refresh before retrying.",
    unsupported_artifact: "The change could not be captured safely. No publication was attempted.",
    failed_validation: "Validation did not pass. Review the command output and adjust the task before retrying.",
    publication_failed: "Publication did not complete. Your reviewed artifact is still unchanged; retry after checking the repository.",
    repository_changed: "The repository changed after the run. Start a new run to review against the latest base.",
    approval_invalidated: "Approval is no longer valid for this artifact. Refresh the run and review again.",
    repository_revoked: "GitHub access was revoked. Reconnect the repository before retrying.",
    runtime_limit_reached: "The sandbox reached its time limit. Narrow the task and retry.",
    cancelled: "The run was cancelled. No publication was attempted.",
    not_configured: "The agent capability is not configured. Enable the local fake provider or a hosted provider.",
  };
  return messages[run.error_type || ""] || "The run stopped safely. Review the timeline and retry if needed.";
}

type DiffSection = {
  path: string;
  changeType: string;
  lines: string[];
  binary: boolean;
  renamed: boolean;
};

function parseDiffSections(text: string, changedFiles: { path?: string; change_type?: string }[]): DiffSection[] {
  const chunks = text.split(/(?=^diff --git )/m).filter((chunk) => chunk.trim());
  if (!chunks.length) {
    return changedFiles.map((file) => ({
      path: file.path || "unknown file",
      changeType: file.change_type || "modified",
      lines: [],
      binary: false,
      renamed: file.change_type === "renamed",
    }));
  }
  return chunks.map((chunk) => {
    const header = chunk.match(/^diff --git a\/(.+?) b\/(.+)$/m);
    const oldPath = chunk.match(/^--- (?:a\/)?(.+)$/m)?.[1];
    const newPath = chunk.match(/^\+\+\+ (?:b\/)?(.+)$/m)?.[1];
    const path = newPath && newPath !== "/dev/null" ? newPath : oldPath || header?.[2] || "unknown file";
    const renamed = /^similarity index|^rename from|^rename to/m.test(chunk);
    const binary = /^Binary files /m.test(chunk);
    const changeType = renamed ? "renamed" : path === "/dev/null" ? "deleted" : oldPath === "/dev/null" ? "added" : "modified";
    return { path, changeType, lines: chunk.split("\n"), binary, renamed };
  });
}

export function AgentPanel() {
  const router = useRouter();
  const me = useMe();
  const queryClient = useQueryClient();
  const [connectionId, setConnectionId] = useState("");
  const [task, setTask] = useState("Add request validation to the /users endpoint and add tests.");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDiffPath, setSelectedDiffPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    if (me.isError && me.error instanceof ApiError && me.error.status === 401) router.replace("/login");
  }, [me.isError, me.error, router]);

  const status = useQuery({ queryKey: ["agent", "status"], queryFn: fetchAgentStatus, enabled: Boolean(me.data), retry: false });
  const connections = useQuery({ queryKey: ["github", "connections"], queryFn: fetchGitHubConnections, enabled: Boolean(me.data), retry: false });
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
        void queryClient.invalidateQueries({ queryKey: ["agent-runs", selectedId, "diff"] });
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
      if (!rows || !rows.some((row) => ["queued", "planning", "running", "validating", "publishing"].includes(row.status))) return false;
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
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [live.logs]);

  const start = useMutation({
    mutationFn: createAgentRun,
    onSuccess: async (run) => {
      setError(null);
      setSelectedId(run.id);
      await queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to start agent run. Check the repository connection and try again."),
  });
  const cancel = useMutation({
    mutationFn: cancelAgentRun,
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["agent-runs"] }); },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to cancel this run."),
  });
  const approve = useMutation({
    mutationFn: (input: { id: string; artifact_hash: string; artifact_version: number; base_commit_sha: string }) => approveAgentRun(input.id, input),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["agent-runs"] }); },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Publication could not be started. Your artifact remains unchanged."),
  });
  const reject = useMutation({
    mutationFn: rejectAgentRun,
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["agent-runs"] }); },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reject publication."),
  });

  const selected = useMemo(() => runs.data?.find((row) => row.id === selectedId) ?? null, [runs.data, selectedId]);
  const displayStatus = live.liveStatus || selected?.status || "";
  const files = useMemo(
    () => (live.changedFiles.length ? live.changedFiles : selected?.changed_files ?? []),
    [live.changedFiles, selected?.changed_files],
  );
  const diffSections = useMemo(() => parseDiffSections(diff.data?.diff_text || "", files), [diff.data?.diff_text, files]);
  const activeDiff = diffSections.find((section) => section.path === selectedDiffPath) || diffSections[0];
  const selectedRepo = connections.data?.find((row) => row.id === selected?.repository_connection_id);
  const terminalStates = new Set(["failed", "cancelled", "rejected", "timed_out", "step_limit_reached", "repository_revoked"]);
  const timelineStates = terminalStates.has(displayStatus)
    ? [...WORKFLOW_STATES, { key: displayStatus, label: readableStatus(displayStatus), detail: "Terminal state · no publication" }]
    : WORKFLOW_STATES;
  const currentWorkflowIndex = timelineStates.findIndex((step) => step.key === displayStatus);
  const diffReady = diff.data?.artifact_status === "ready" && Boolean(diff.data.artifact_hash && diff.data.artifact_version && diff.data.base_commit_sha);
  const validationOk = selected?.validation && (selected.validation as { ok?: unknown }).ok === true;

  if (me.isLoading || (me.data && status.isLoading)) return <p className="text-muted" role="status">Connecting to AgentDock…</p>;
  if (!me.data) return <p className="text-muted" role="status">Redirecting to sign in…</p>;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!connectionId) { setError("Connect a repository first so the worker has a trusted checkout."); return; }
    start.mutate({ repository_connection_id: connectionId, task });
  }

  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-accent-soft">Execution workspace</p>
          <h1 className="mt-3 font-[family-name:var(--font-display)] text-4xl font-bold tracking-tight sm:text-5xl">Coding agent</h1>
          <p className="mt-3 max-w-2xl text-muted">A bounded inspect → edit → validate loop. Nothing is published until you review the exact diff.</p>
        </div>
        <div className="flex gap-2">
          <Link href="/github" className="rounded-md border border-border bg-surface px-3 py-2 text-sm font-semibold hover:border-accent/60">Repositories</Link>
          <Link href="/dashboard" className="rounded-md border border-border bg-surface px-3 py-2 text-sm font-semibold hover:border-accent/60">Overview</Link>
        </div>
      </div>

      {error ? <p className="rounded-md border border-bad/40 bg-bad/8 px-4 py-3 text-sm text-bad" role="alert">{error}</p> : null}

      {!status.data?.configured ? (
        <div className="rounded-xl border border-warn/35 bg-warn/8 p-5">
          <p className="font-semibold">Agent LLM is not configured</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">Set <code className="font-mono text-foreground">LLM_PROVIDER=fake</code> for deterministic local runs, or configure a hosted provider. GitHub connections and the rest of the control plane remain available.</p>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="rounded-xl border border-border bg-surface/70 p-5 sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div><h2 className="font-[family-name:var(--font-display)] text-xl font-semibold">Start a bounded task</h2><p className="mt-1 text-sm text-muted">One focused change produces one reviewable artifact.</p></div>
            <span className="rounded-full border border-ok/30 bg-ok/8 px-3 py-1 font-mono text-xs text-ok">sandboxed · argv-only tools</span>
          </div>
          <div className="mt-5 grid gap-4 lg:grid-cols-[0.7fr_1.3fr]">
            <label className="block text-sm"><span className="mb-2 block font-medium">Repository</span><select id="connection" aria-label="Repository" value={connectionId} onChange={(e) => setConnectionId(e.target.value)} className="w-full rounded-md border border-border bg-background px-3 py-2.5 text-sm text-foreground">
              {(connections.data ?? []).length === 0 ? <option value="">No connected repositories</option> : (connections.data as RepositoryConnection[]).map((row) => <option key={row.id} value={row.id}>{row.full_name}</option>)}
            </select>{!connections.isLoading && !(connections.data ?? []).length ? <span className="mt-2 block text-xs text-muted"><Link href="/github" className="text-accent-soft hover:underline">Connect a repository</Link> to begin.</span> : null}</label>
            <label className="block text-sm"><span className="mb-2 block font-medium">Task</span><textarea id="task" aria-label="Task" value={task} onChange={(e) => setTask(e.target.value)} rows={3} className="w-full resize-y rounded-md border border-border bg-background px-3 py-2.5 text-sm leading-relaxed text-foreground" /></label>
          </div>
          <button type="submit" disabled={start.isPending || !connectionId} className="mt-4 rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-background hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-60">{start.isPending ? "Preparing sandbox…" : "Start agent run"}</button>
        </form>
      )}

      <div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="rounded-xl border border-border bg-surface/50 p-4" aria-label="Agent runs">
          <div className="flex items-center justify-between gap-2"><h2 className="font-[family-name:var(--font-display)] text-lg font-semibold">Recent runs</h2><span className="font-mono text-xs text-muted">{runs.data?.length ?? 0}</span></div>
          {runs.isLoading ? <p className="mt-5 text-sm text-muted" role="status">Loading runs…</p> : null}
          {!runs.isLoading && !(runs.data ?? []).length ? <p className="mt-5 rounded-md border border-dashed border-border p-4 text-sm leading-relaxed text-muted">No agent runs yet. Start a task above to create your first reviewable change.</p> : null}
          <ul className="mt-4 space-y-2">
            {(runs.data ?? []).map((run) => <li key={run.id}><button type="button" onClick={() => { setSelectedId(run.id); setSelectedDiffPath(""); }} aria-current={selectedId === run.id ? "true" : undefined} className={`w-full rounded-md border p-3 text-left transition ${selectedId === run.id ? "border-accent bg-accent/8" : "border-border hover:border-accent/50"}`}><div className="flex items-center justify-between gap-2"><StatusPill status={run.status} /><span className="font-mono text-[10px] text-muted">{new Date(run.created_at).toLocaleDateString()}</span></div><p className="mt-2 line-clamp-2 text-sm text-foreground">{run.task}</p><p className="mt-2 text-xs text-muted">{run.changed_files?.length ?? 0} changed files</p></button></li>)}
          </ul>
        </aside>

        {!selected ? <div className="rounded-xl border border-dashed border-border p-8 text-center text-muted">Select a run to inspect its timeline, activity, and diff.</div> : (
          <div className="min-w-0 space-y-5">
            <section className="rounded-xl border border-border bg-surface/70 p-5 sm:p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0"><div className="flex flex-wrap items-center gap-3"><StatusPill status={displayStatus} /><span className="font-mono text-xs text-muted">{selectedRepo?.full_name || "Repository connection"}</span></div><h2 className="mt-3 break-words font-[family-name:var(--font-display)] text-2xl font-semibold" data-testid="run-status">{statusLabel(displayStatus)}</h2><p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">{statusExplanation(displayStatus)}</p></div>
                <div className="flex items-center gap-3"><span className={`font-mono text-xs ${live.connected ? "text-ok" : live.reconnecting ? "text-warn" : "text-muted"}`} role="status">{live.connected ? "● live" : live.reconnecting ? "↻ reconnecting" : live.wsFailed ? "REST fallback" : "connecting"}</span>{["queued", "planning", "running", "validating"].includes(selected.status) ? <button type="button" onClick={() => cancel.mutate(selected.id)} disabled={cancel.isPending} className="rounded-md border border-border px-3 py-1.5 text-sm font-semibold hover:border-bad/60 disabled:opacity-60">{cancel.isPending ? "Cancelling…" : "Cancel run"}</button> : null}</div>
              </div>
              <div className="mt-7 overflow-x-auto pb-1"><ol className="flex min-w-[680px] items-start"><li className="sr-only">Execution timeline</li>{timelineStates.map((step, index) => { const done = currentWorkflowIndex > index || displayStatus === "succeeded"; const current = currentWorkflowIndex === index && !done; return <li key={`${step.key}-${index}`} className="relative flex min-w-[96px] flex-1 flex-col items-center text-center"><div className="flex w-full items-center"><span className={`z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full border font-mono text-xs ${done ? "border-ok bg-ok/15 text-ok" : current ? terminalStates.has(displayStatus) ? "border-bad bg-bad/15 text-bad" : "border-accent bg-accent/15 text-accent-soft" : "border-border bg-background text-muted"}`} aria-label={`${step.label}: ${done ? "complete" : current ? "current" : "pending"}`}>{done ? "✓" : index + 1}</span>{index < timelineStates.length - 1 ? <span className={`h-px w-full ${done ? "bg-ok/60" : "bg-border"}`} /> : null}</div><span className={`mt-2 text-xs ${current ? "font-semibold text-foreground" : "text-muted"}`}>{step.label}</span><span className="mt-1 hidden max-w-[100px] text-[10px] leading-tight text-muted sm:block">{step.detail}</span></li>; })}</ol></div>
              <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 border-t border-border pt-4 font-mono text-xs text-muted"><span>steps {selected.steps_used}/{selected.max_steps}</span><span>tools {selected.tool_calls_used}</span>{selected.base_commit_sha ? <span>base {selected.base_commit_sha.slice(0, 12)}</span> : null}</div>
            </section>

            {selected.status === "awaiting_approval" && (selected.publication_status || "pending") === "pending" ? <section className="rounded-xl border border-accent/45 bg-accent/8 p-5 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="font-[family-name:var(--font-display)] text-xl font-semibold">Review before publication</p><p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">Approval is deliberate: AgentDock will publish only this immutable artifact against the displayed base commit.</p></div><span className={`status-pill ${diffReady ? "status-pill-ok" : "status-pill-warn"}`}>{diffReady ? "artifact verified" : "artifact pending"}</span></div><dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4"><ReviewFact label="Repository" value={selectedRepo?.full_name || "Unknown"} /><ReviewFact label="Base commit" value={selected.base_commit_sha?.slice(0, 12) || "Unavailable"} mono /><ReviewFact label="Changed files" value={String(files.length)} /><ReviewFact label="Validation" value={validationOk ? "Passed" : selected.validation ? "Review result" : "Not reported"} /></dl><div className="mt-5 flex flex-wrap gap-2"><button type="button" disabled={approve.isPending || reject.isPending || !diffReady} onClick={() => { if (!diff.data?.artifact_hash || !diff.data.artifact_version || !diff.data.base_commit_sha) { setError("The immutable publication artifact is unavailable. Refresh before approving."); return; } approve.mutate({ id: selected.id, artifact_hash: diff.data.artifact_hash, artifact_version: diff.data.artifact_version, base_commit_sha: diff.data.base_commit_sha }); }} className="rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-background hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50">{approve.isPending ? "Approving…" : "Approve & Create PR"}</button><button type="button" disabled={approve.isPending || reject.isPending} onClick={() => reject.mutate(selected.id)} className="rounded-md border border-border px-4 py-2.5 text-sm font-semibold hover:border-bad/60 disabled:opacity-50">{reject.isPending ? "Rejecting…" : "Reject"}</button></div><p className="mt-3 text-xs text-muted">Publication creates a branch, one commit, and one pull request in a fresh checkout.</p></section> : null}

            {(selected.publication_status || "pending") !== "pending" ? <section className="rounded-xl border border-border bg-surface/70 p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs uppercase tracking-[0.16em] text-muted">Publication result</p><p className="mt-2 font-semibold">{statusLabel(selected.publication_status || "pending")}</p></div>{selected.github_pr_url ? <a className="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-background hover:bg-accent-soft" href={selected.github_pr_url} target="_blank" rel="noreferrer">Open pull request ↗</a> : null}</div>{selected.branch_name || selected.commit_sha ? <p className="mt-3 font-mono text-xs text-muted">{selected.branch_name ? `branch ${selected.branch_name}` : ""}{selected.commit_sha ? ` · commit ${selected.commit_sha.slice(0, 12)}` : ""}</p> : null}</section> : null}
            {selected.summary ? <p className="rounded-md border border-border bg-surface/45 p-4 text-sm leading-relaxed">{selected.summary}</p> : null}
            {safeRunError(selected) ? <p className="rounded-md border border-bad/40 bg-bad/8 p-4 text-sm leading-relaxed text-bad" role="alert">{statusExplanation(selected.status)} {safeRunError(selected)}</p> : null}

            <div className="grid gap-5 lg:grid-cols-[1fr_0.95fr]">
              <section className="rounded-xl border border-border bg-surface/70 p-5"><div className="flex items-center justify-between gap-3"><div><h3 className="font-[family-name:var(--font-display)] text-lg font-semibold">Live activity</h3><p className="mt-1 text-xs text-muted">Operational events only; no hidden reasoning is shown.</p></div><span className="font-mono text-xs text-muted">{live.activity.length || steps.data?.length || 0} events</span></div><ul className="mt-4 max-h-72 space-y-3 overflow-auto pr-1 text-sm" data-testid="live-activity">{live.activity.length ? live.activity.map((row, index) => <li key={`${row.at}-${index}`} className="flex gap-3 border-l border-accent/40 pl-3"><span className="shrink-0 font-mono text-[10px] text-muted">{formatClock(row.at)}</span><span>{row.text}</span></li>) : steps.data?.length ? steps.data.map((step) => <li key={step.id} className="border-l border-border pl-3"><div className="flex items-center justify-between gap-3"><span className="font-medium">{step.tool_name || step.kind}</span>{step.duration_ms ? <span className="font-mono text-[10px] text-muted">{step.duration_ms}ms</span> : null}</div><pre className="mt-1 max-h-20 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted">{step.tool_result_summary || "No result summary"}</pre></li>) : <li className="rounded-md border border-dashed border-border p-4 text-muted">Waiting for the first sandbox event…</li>}</ul></section>
              <section className="rounded-xl border border-border bg-surface/70 p-5"><div><h3 className="font-[family-name:var(--font-display)] text-lg font-semibold">Command output</h3><p className="mt-1 text-xs text-muted">Technical output is kept separate from the activity timeline.</p></div><pre ref={logRef} data-testid="command-output" onScroll={(e) => { const el = e.currentTarget; stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48; }} className="mt-4 max-h-72 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed">{live.logs.length ? live.logs.map((line, index) => <span key={index} className={line.stream === "stderr" ? "text-bad" : undefined}>{line.text}</span>) : "Waiting for validation output…"}</pre></section>
            </div>

            <section className="rounded-xl border border-border bg-surface/70 p-5 sm:p-6"><div className="flex flex-wrap items-end justify-between gap-3"><div><h3 className="font-[family-name:var(--font-display)] text-xl font-semibold">Diff review</h3><p className="mt-1 text-sm text-muted">The browser receives the bounded preview; the immutable artifact stays server-side.</p></div><div className="font-mono text-xs text-muted">{files.length} file{files.length === 1 ? "" : "s"} · {diff.data?.diff_stat || "stat pending"}</div></div>{diff.data?.diff_truncated || diff.data?.preview_truncated ? <p className="mt-4 rounded-md border border-warn/35 bg-warn/8 px-3 py-2 text-xs text-warn">Preview truncated. The complete artifact remains protected for integrity-checked publication.</p> : null}<div className="mt-5 grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]"><nav aria-label="Changed files" className="min-w-0"><p className="mb-2 text-xs uppercase tracking-[0.16em] text-muted">Changed files</p><ul className="space-y-1">{(diffSections.length ? diffSections : files.map((file) => ({ path: file.path || "unknown file", changeType: file.change_type || "modified" } as DiffSection))).map((file) => <li key={file.path}><button type="button" onClick={() => setSelectedDiffPath(file.path)} aria-current={activeDiff?.path === file.path ? "true" : undefined} className={`w-full rounded-md border px-2.5 py-2 text-left text-xs ${activeDiff?.path === file.path ? "border-accent bg-accent/8 text-foreground" : "border-transparent text-muted hover:border-border hover:text-foreground"}`}><span className="mr-1.5 font-mono text-accent-soft">{file.changeType === "added" ? "+" : file.changeType === "deleted" ? "−" : file.changeType === "renamed" ? "→" : "±"}</span><span className="break-all">{file.path}</span>{"binary" in file && file.binary ? <span className="ml-1 text-[10px]">· binary</span> : null}</button></li>)}</ul>{!files.length ? <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted">No changed files yet.</p> : null}</nav><div className="min-w-0">{activeDiff?.binary ? <div className="rounded-md border border-border bg-background p-5 font-mono text-xs text-muted">Binary change · preview is not available.</div> : activeDiff?.lines.length ? <pre className="max-h-[30rem] overflow-auto rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed" aria-label={`Diff for ${activeDiff.path}`}><code>{activeDiff.lines.map((line, index) => <span key={`${index}-${line}`} className={`block min-w-max px-2 ${line.startsWith("+") && !line.startsWith("+++") ? "bg-ok/10 text-ok" : line.startsWith("-") && !line.startsWith("---") ? "bg-bad/10 text-bad" : line.startsWith("@@") ? "text-accent-soft" : "text-muted"}`}>{line || " "}</span>)}</code></pre> : <div className="rounded-md border border-dashed border-border bg-background p-8 text-sm text-muted">Diff preview will appear after validation completes.</div>}</div></div><div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 border-t border-border pt-4 font-mono text-xs text-muted"><span>artifact {diff.data?.artifact_status || "pending"}</span>{diff.data?.artifact_hash ? <span>sha256 {diff.data.artifact_hash.slice(0, 12)}…</span> : null}{diff.data?.artifact_size ? <span>{Math.round(diff.data.artifact_size / 1024)} KiB</span> : null}</div></section>
          </div>
        )}
      </div>
    </section>
  );
}

function ReviewFact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="rounded-md border border-border bg-background/35 p-3"><dt className="text-xs text-muted">{label}</dt><dd className={`mt-1 truncate font-medium ${mono ? "font-mono text-xs" : ""}`}>{value}</dd></div>;
}
