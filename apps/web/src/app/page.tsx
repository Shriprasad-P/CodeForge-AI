import Link from "next/link";

import { SystemStatus } from "@/components/system-status";

const apiDocsUrl = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/docs`;

const workflow = [
  ["01", "Connect GitHub", "Choose the installation and repository you trust."],
  ["02", "Describe the task", "Give the agent a bounded coding objective."],
  ["03", "Inspect safely", "The worker materializes a fresh, isolated workspace."],
  ["04", "Edit with tools", "Only approved argv-based tools can change files."],
  ["05", "Validate", "Tests and checks run before anything can be published."],
  ["06", "Review the diff", "See the exact immutable artifact and its integrity status."],
  ["07", "Approve the PR", "A trusted service creates one branch, commit, and PR."],
] as const;

export default function HomePage() {
  return (
    <main className="min-h-screen overflow-hidden">
      <header className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-6 sm:px-8">
        <Link href="/" className="flex items-center gap-2.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
          <span className="grid h-8 w-8 place-items-center rounded-md bg-accent font-mono font-bold text-background">A</span>
          <span className="font-[family-name:var(--font-display)] text-xl font-bold tracking-tight">AgentDock</span>
        </Link>
        <nav aria-label="Landing navigation" className="flex items-center gap-2 text-sm">
          <a href="#how-it-works" className="hidden rounded-md px-3 py-2 text-muted hover:text-foreground sm:inline-block">How it works</a>
          <a href={apiDocsUrl} className="hidden rounded-md px-3 py-2 text-muted hover:text-foreground sm:inline-block">API docs</a>
          <Link href="/login" className="rounded-md border border-border px-3 py-2 font-medium text-foreground hover:bg-surface">Sign in</Link>
        </nav>
      </header>

      <section className="relative mx-auto grid max-w-7xl items-center gap-12 px-5 pb-20 pt-12 sm:px-8 lg:grid-cols-[1.1fr_0.9fr] lg:pb-28 lg:pt-20">
        <div className="relative z-10">
          <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/8 px-3 py-1.5 font-mono text-xs text-accent-soft">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
            Secure coding workflow · human approval required
          </p>
          <h1 className="max-w-3xl font-[family-name:var(--font-display)] text-5xl font-extrabold leading-[0.98] tracking-[-0.04em] sm:text-7xl">
            Ship code with a checkpoint you can trust.
          </h1>
          <p className="mt-7 max-w-2xl text-lg leading-relaxed text-muted sm:text-xl">
            AgentDock is a secure, human-approved AI coding-agent platform. Connect GitHub, let a bounded agent inspect, edit, and validate inside an isolated sandbox, then review the exact diff before publication.
          </p>
          <div className="mt-9 flex flex-wrap gap-3">
            <Link href="/github" className="rounded-md bg-accent px-5 py-3 text-sm font-semibold text-background shadow-[0_8px_30px_rgba(217,119,6,0.2)] hover:bg-accent-soft">
              Start with GitHub
            </Link>
            <Link href="/dashboard" className="rounded-md border border-border bg-surface px-5 py-3 text-sm font-semibold text-foreground hover:border-accent/60">
              Open Dashboard
            </Link>
          </div>
          <div className="mt-7 flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <a href="#architecture" className="text-accent-soft underline-offset-4 hover:underline">View Architecture</a>
            <a href="#how-it-works" className="text-accent-soft underline-offset-4 hover:underline">How It Works</a>
          </div>
        </div>

        <div className="relative" aria-label="AgentDock workflow preview">
          <div className="absolute -inset-8 bg-[radial-gradient(circle_at_center,rgba(217,119,6,0.14),transparent_68%)]" aria-hidden="true" />
          <div className="relative overflow-hidden rounded-xl border border-border bg-surface shadow-2xl shadow-black/30">
            <div className="flex items-center justify-between border-b border-border px-4 py-3 text-xs">
              <span className="font-mono text-muted">agentdock / workspace</span>
              <span className="inline-flex items-center gap-1.5 text-ok"><span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />live</span>
            </div>
            <div className="grid gap-0 divide-y divide-border md:grid-cols-[0.9fr_1.1fr] md:divide-x md:divide-y-0">
              <div className="p-5">
                <p className="text-xs uppercase tracking-[0.16em] text-muted">Run status</p>
                <p className="mt-2 text-lg font-semibold">Awaiting approval</p>
                <div className="mt-5 space-y-3">
                  {["queued", "inspect", "edit", "validate", "review"].map((step, index) => (
                    <div key={step} className="flex items-center gap-3 text-sm">
                      <span className={`grid h-5 w-5 place-items-center rounded-full font-mono text-[10px] ${index < 4 ? "bg-ok/15 text-ok" : "bg-accent/15 text-accent-soft"}`} aria-hidden="true">{index < 4 ? "✓" : "5"}</span>
                      <span className={index < 4 ? "text-muted" : "font-medium"}>{step}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="bg-background/45 p-5 font-mono text-xs">
                <p className="text-muted">activity</p>
                <p className="mt-4 text-accent-soft">$ pytest tests/users -q</p>
                <p className="mt-2 text-ok">47 passed in 1.82s</p>
                <p className="mt-5 text-muted">changed files</p>
                <p className="mt-2 text-foreground">M src/users/routes.py</p>
                <p className="text-foreground">A tests/users/test_routes.py</p>
                <div className="mt-6 rounded-md border border-accent/40 bg-accent/8 px-3 py-2 text-accent-soft">diff integrity · verified</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="how-it-works" className="border-y border-border/80 bg-surface/35">
        <div className="mx-auto max-w-7xl px-5 py-16 sm:px-8">
          <div className="max-w-2xl">
            <p className="font-mono text-xs uppercase tracking-[0.18em] text-accent-soft">The workflow</p>
            <h2 className="mt-3 font-[family-name:var(--font-display)] text-3xl font-bold tracking-tight sm:text-4xl">A visible path from task to pull request.</h2>
            <p className="mt-4 text-muted">Every transition is durable, observable, and stops for a human review of the exact change.</p>
          </div>
          <ol className="mt-10 grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
            {workflow.map(([number, title, description]) => (
              <li key={number} className="bg-background/95 p-5">
                <span className="font-mono text-xs text-accent-soft">{number}</span>
                <h3 className="mt-5 font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted">{description}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="architecture" className="mx-auto grid max-w-7xl gap-10 px-5 py-16 sm:px-8 lg:grid-cols-[0.85fr_1.15fr] lg:py-24">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-accent-soft">Trust boundary</p>
          <h2 className="mt-3 font-[family-name:var(--font-display)] text-3xl font-bold tracking-tight">Credentials stay in the control plane.</h2>
          <p className="mt-4 leading-relaxed text-muted">The worker owns orchestration. The ephemeral Docker sandbox receives only the bounded checkout and tool inputs it needs. GitHub credentials, database secrets, Redis credentials, and LLM keys never cross that boundary.</p>
          <Link href="/register" className="mt-7 inline-flex rounded-md border border-border px-4 py-2.5 text-sm font-semibold hover:border-accent/60">Create a local account</Link>
        </div>
        <div className="overflow-x-auto rounded-xl border border-border bg-surface p-5 font-mono text-xs leading-loose text-muted sm:p-7">
          <p><span className="text-foreground">Browser</span> <span className="text-accent-soft">→</span> Next.js workspace</p>
          <p className="pl-8"><span className="text-accent-soft">↓</span></p>
          <p><span className="text-foreground">FastAPI</span> <span className="text-accent-soft">→</span> PostgreSQL (workflow truth)</p>
          <p className="pl-20"><span className="text-accent-soft">↘</span> Redis (queues + realtime)</p>
          <p className="pl-8"><span className="text-accent-soft">↓</span></p>
          <p><span className="text-foreground">Worker</span> <span className="text-accent-soft">→</span> Docker sandbox (no socket, no network)</p>
          <p className="mt-4 border-t border-border pt-4 text-ok">Human approval <span className="text-accent-soft">→</span> fresh checkout <span className="text-accent-soft">→</span> branch / commit / PR</p>
        </div>
      </section>

      <section id="status" className="mx-auto max-w-7xl px-5 pb-20 sm:px-8">
        <SystemStatus />
      </section>
      <footer className="border-t border-border/80 px-5 py-8 text-center text-xs text-muted sm:px-8">
        AgentDock · secure execution, inspectable changes, deliberate publication.
      </footer>
    </main>
  );
}
