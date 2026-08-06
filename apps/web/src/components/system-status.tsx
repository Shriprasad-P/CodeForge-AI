"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchHealth, fetchReady } from "@/lib/api";

function StatusDot({ ok }: { ok: boolean | undefined }) {
  const color =
    ok === undefined ? "bg-warn" : ok ? "bg-ok" : "bg-bad";
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ${color}`}
      aria-hidden
    />
  );
}

export function SystemStatus() {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth });
  const ready = useQuery({ queryKey: ["ready"], queryFn: fetchReady });

  const apiUp = health.isSuccess;
  const postgres = ready.data?.checks.postgres;
  const redis = ready.data?.checks.redis;

  return (
    <section
      aria-label="System status"
      className="rounded-xl border border-border bg-surface/80 p-5 backdrop-blur"
    >
      <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold tracking-tight">
        System status
      </h2>
      <p className="mt-1 text-sm text-muted">
        Live checks against the AgentDock API (Phase 1).
      </p>

      <ul className="mt-4 space-y-3 font-mono text-sm">
        <li className="flex items-center justify-between gap-4">
          <span className="text-muted">API</span>
          <span className="flex items-center gap-2">
            <StatusDot ok={apiUp} />
            {health.isLoading ? "checking…" : apiUp ? "up" : "down"}
          </span>
        </li>
        <li className="flex items-center justify-between gap-4">
          <span className="text-muted">PostgreSQL</span>
          <span className="flex items-center gap-2">
            <StatusDot ok={postgres} />
            {ready.isLoading ? "checking…" : postgres ? "ready" : "not ready"}
          </span>
        </li>
        <li className="flex items-center justify-between gap-4">
          <span className="text-muted">Redis</span>
          <span className="flex items-center gap-2">
            <StatusDot ok={redis} />
            {ready.isLoading ? "checking…" : redis ? "ready" : "not ready"}
          </span>
        </li>
      </ul>

      {(health.isError || ready.isError) && (
        <p className="mt-4 text-sm text-bad" role="alert">
          Cannot reach API at{" "}
          <code className="font-mono">
            {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}
          </code>
          . Start Compose or the API locally.
        </p>
      )}
    </section>
  );
}
