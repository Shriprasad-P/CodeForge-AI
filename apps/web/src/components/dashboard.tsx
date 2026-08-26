"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { useLogout, useMe } from "@/lib/auth";
import { ApiError, fetchAgentRuns, fetchGitHubConnections, type AgentRun } from "@/lib/api";
import { StatusPill } from "@/components/status-pill";

function formatDuration(run: AgentRun): string {
  if (!run.started_at) return "Not started";
  const end = run.finished_at ? new Date(run.finished_at) : new Date();
  const seconds = Math.max(0, Math.round((end.getTime() - new Date(run.started_at).getTime()) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function Dashboard() {
  const router = useRouter();
  const me = useMe();
  const logout = useLogout();
  const connections = useQuery({
    queryKey: ["github", "connections"],
    queryFn: fetchGitHubConnections,
    enabled: Boolean(me.data),
    retry: false,
  });
  const runs = useQuery({
    queryKey: ["agent-runs"],
    queryFn: fetchAgentRuns,
    enabled: Boolean(me.data),
    retry: false,
  });

  useEffect(() => {
    if (me.isError && me.error instanceof ApiError && me.error.status === 401) {
      router.replace("/login");
    }
  }, [me.isError, me.error, router]);

  async function onLogout() {
    await logout.mutateAsync();
    router.replace("/login");
  }

  if (me.isLoading) {
    return <p className="text-muted" role="status">Loading your AgentDock workspace…</p>;
  }

  if (!me.data) {
    return <p className="text-muted" role="status">Redirecting to sign in…</p>;
  }

  const name = me.data.display_name || me.data.email;
  const rows = runs.data ?? [];
  const active = rows.filter((run) => ["queued", "planning", "running", "validating", "publishing"].includes(run.status)).length;
  const succeeded = rows.filter((run) => run.status === "succeeded").length;

  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-accent-soft">Workspace overview</p>
          <h1 className="mt-3 font-[family-name:var(--font-display)] text-4xl font-bold tracking-tight sm:text-5xl">Welcome, {name}</h1>
          <p className="mt-3 max-w-2xl text-muted">A calm control surface for turning an approved task into a reviewable pull request.</p>
        </div>
        <button
          type="button"
          onClick={onLogout}
          disabled={logout.isPending}
          className="rounded-md border border-border bg-surface px-4 py-2 text-sm font-semibold hover:border-accent/60 disabled:opacity-60"
        >
          {logout.isPending ? "Signing out…" : "Sign out"}
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        <Link href="/github" className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-background hover:bg-accent-soft">Manage GitHub</Link>
        <Link href="/agent" className="rounded-md border border-border bg-surface px-4 py-2 text-sm font-semibold hover:border-accent/60">Coding agent</Link>
        <Link href="/executions" className="rounded-md border border-border bg-surface px-4 py-2 text-sm font-semibold hover:border-accent/60">Executions</Link>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <MetricCard label="Connected repositories" value={connections.isLoading ? "…" : String(connections.data?.length ?? 0)} detail="GitHub installations you selected" />
        <MetricCard label="Active workflows" value={String(active)} detail="Durable runs in progress" />
        <MetricCard label="Completed runs" value={String(succeeded)} detail="Ready for review or published" />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
        <section className="rounded-xl border border-border bg-surface/70 p-5 sm:p-6">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-[family-name:var(--font-display)] text-xl font-semibold">Recent workflows</h2>
              <p className="mt-1 text-sm text-muted">Inspect the run, validation, and publication outcome.</p>
            </div>
            <Link href="/agent" className="text-sm font-semibold text-accent-soft hover:underline">New task</Link>
          </div>
          {runs.isLoading ? <p className="mt-6 text-sm text-muted" role="status">Loading recent workflows…</p> : null}
          {!runs.isLoading && !rows.length ? (
            <div className="mt-6 rounded-lg border border-dashed border-border p-6 text-center">
              <p className="font-medium">No agent runs yet</p>
              <p className="mt-2 text-sm text-muted">Connect a repository, describe a small task, and AgentDock will guide you to a diff review.</p>
              <Link href={connections.data?.length ? "/agent" : "/github"} className="mt-4 inline-flex rounded-md bg-accent px-4 py-2 text-sm font-semibold text-background">
                {connections.data?.length ? "Start a coding task" : "Connect GitHub"}
              </Link>
            </div>
          ) : null}
          <ul className="mt-5 divide-y divide-border">
            {rows.slice(0, 6).map((run) => (
              <li key={run.id} className="flex flex-wrap items-center justify-between gap-3 py-4 first:pt-0 last:pb-0">
                <Link href={`/agent?run=${run.id}`} className="min-w-0 flex-1 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                  <p className="truncate font-medium">{run.task}</p>
                  <p className="mt-1 font-mono text-xs text-muted">{new Date(run.created_at).toLocaleString()} · {run.changed_files?.length ?? 0} changed files · {formatDuration(run)}</p>
                </Link>
                <div className="flex items-center gap-3">
                  <StatusPill status={run.status} />
                  {run.github_pr_url ? <a href={run.github_pr_url} target="_blank" rel="noreferrer" className="text-xs font-semibold text-accent-soft hover:underline">PR ↗</a> : null}
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded-xl border border-border bg-surface/70 p-5 sm:p-6">
          <h2 className="font-[family-name:var(--font-display)] text-xl font-semibold">Start here</h2>
          <p className="mt-1 text-sm text-muted">The shortest path through the product.</p>
          <ol className="mt-5 space-y-4">
            <QuickStep number="1" title="Connect a repository" detail="Choose a GitHub installation and repository." href="/github" />
            <QuickStep number="2" title="Describe a bounded task" detail="Give the agent one change and its validation target." href="/agent" />
            <QuickStep number="3" title="Review before publish" detail="Approve only the immutable artifact you inspected." href="/agent" />
          </ol>
          <div className="mt-6 rounded-lg border border-accent/25 bg-accent/8 p-4 text-sm">
            <p className="font-semibold">Your account</p>
            <p className="mt-1 break-all font-mono text-xs text-muted">{me.data.email}</p>
          </div>
        </section>
      </div>
    </section>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface/70 p-5">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-3 font-[family-name:var(--font-display)] text-3xl font-bold">{value}</p>
      <p className="mt-1 text-xs text-muted">{detail}</p>
    </div>
  );
}

function QuickStep({ number, title, detail, href }: { number: string; title: string; detail: string; href: string }) {
  return (
    <li className="flex gap-3">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-accent/40 font-mono text-xs text-accent-soft">{number}</span>
      <div>
        <Link href={href} className="font-medium hover:text-accent-soft">{title}</Link>
        <p className="mt-1 text-sm leading-relaxed text-muted">{detail}</p>
      </div>
    </li>
  );
}
