"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useLogout, useMe } from "@/lib/auth";
import { ApiError } from "@/lib/api";

export function Dashboard() {
  const router = useRouter();
  const me = useMe();
  const logout = useLogout();

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
    return <p className="text-muted">Loading your account…</p>;
  }

  if (!me.data) {
    return <p className="text-muted">Redirecting to sign in…</p>;
  }

  const name = me.data.display_name || me.data.email;

  return (
    <section className="mx-auto w-full max-w-2xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl font-bold">
            Welcome, {name}
          </h1>
          <p className="mt-2 text-muted">AgentDock Phase 5</p>
        </div>
        <button
          type="button"
          onClick={onLogout}
          disabled={logout.isPending}
          className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-semibold"
        >
          {logout.isPending ? "Signing out…" : "Sign out"}
        </button>
      </div>
      <div className="space-y-3 rounded-xl border border-border bg-surface/80 p-5">
        <p className="text-sm text-muted">
          Connect GitHub, then run constrained commands in an isolated sandbox. Agent autonomy arrives
          in Phase 5.
        </p>
        <div className="flex flex-wrap gap-2">
          <Link
            href="/github"
            className="inline-flex rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-background"
          >
            Manage GitHub
          </Link>
          <Link
            href="/executions"
            className="inline-flex rounded-lg border border-border px-4 py-2 text-sm font-semibold"
          >
            Executions
          </Link>
          <Link
            href="/agent"
            className="inline-flex rounded-lg border border-border px-4 py-2 text-sm font-semibold"
          >
            Coding agent
          </Link>
        </div>
        <p className="font-mono text-xs text-muted">{me.data.email}</p>
      </div>
    </section>
  );
}
