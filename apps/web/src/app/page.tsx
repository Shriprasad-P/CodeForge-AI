import Link from "next/link";

import { SystemStatus } from "@/components/system-status";

const apiDocsUrl = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/docs`;

export default function HomePage() {
  return (
    <main className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-6 pb-16 pt-8">
      <header className="flex items-center justify-between gap-4">
        <p className="font-[family-name:var(--font-display)] text-2xl font-extrabold tracking-tight text-accent-soft">
          AgentDock
        </p>
        <nav className="flex items-center gap-3 text-sm">
          <a
            href="#status"
            className="text-muted transition hover:text-foreground"
          >
            Status
          </a>
          <a
            href={apiDocsUrl}
            className="text-muted transition hover:text-foreground"
          >
            API docs
          </a>
          <Link
            href="/login"
            className="text-muted transition hover:text-foreground"
          >
            Sign in
          </Link>
        </nav>
      </header>

      <section className="mt-16 grid flex-1 items-center gap-12 lg:grid-cols-[1.2fr_0.8fr]">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-5xl font-extrabold leading-[1.05] tracking-tight sm:text-6xl">
            AgentDock
          </h1>
          <p className="mt-5 max-w-xl text-lg leading-relaxed text-muted">
            Secure cloud coding agents in isolated sandboxes. Connect GitHub,
            describe a task, review the diff, approve the pull request.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/login"
              className="rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-background"
            >
              Sign in
            </Link>
            <Link
              href="/register"
              className="rounded-lg border border-border bg-surface px-5 py-2.5 text-sm font-semibold text-foreground"
            >
              Create account
            </Link>
            <button
              type="button"
              disabled
              title="Available in Phase 3"
              className="rounded-lg border border-border bg-surface px-5 py-2.5 text-sm font-semibold text-foreground opacity-80"
            >
              Connect GitHub
            </button>
          </div>
          <p className="mt-3 text-xs text-muted">
            Phase 2 adds email/password auth. GitHub App lands in Phase 3.
          </p>
        </div>

        <div
          className="relative min-h-[280px] overflow-hidden rounded-2xl border border-border"
          aria-hidden
        >
          <div className="absolute inset-0 bg-[linear-gradient(135deg,#1a2330_0%,#0c1117_45%,#1f2937_100%)]" />
          <div className="absolute inset-0 opacity-40 [background-image:linear-gradient(rgba(251,191,36,0.15)_1px,transparent_1px),linear-gradient(90deg,rgba(251,191,36,0.12)_1px,transparent_1px)] [background-size:28px_28px]" />
          <div className="absolute bottom-6 left-6 right-6 rounded-lg border border-border/80 bg-background/70 p-4 font-mono text-xs backdrop-blur">
            <p className="text-ok">sandbox.ready</p>
            <p className="mt-1 text-muted">agent.planning → tool.run_tests</p>
            <p className="mt-1 text-accent-soft">approval.required · push + PR</p>
          </div>
        </div>
      </section>

      <section id="status" className="mt-16 max-w-md">
        <SystemStatus />
      </section>
    </main>
  );
}
