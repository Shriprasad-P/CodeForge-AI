"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { href: "/dashboard", label: "Overview" },
  { href: "/github", label: "Repositories" },
  { href: "/agent", label: "Coding agent" },
  { href: "/executions", label: "Executions" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="border-b border-border/80 bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex min-h-16 max-w-7xl items-center justify-between gap-5 px-5 sm:px-8">
          <Link
            href="/dashboard"
            className="flex shrink-0 items-center gap-2.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span className="grid h-7 w-7 place-items-center rounded-md bg-accent font-mono text-sm font-bold text-background">
              A
            </span>
            <span className="font-[family-name:var(--font-display)] text-lg font-bold tracking-tight">
              AgentDock
            </span>
          </Link>
          <nav aria-label="Primary navigation" className="hidden items-center gap-1 md:flex">
            {navigation.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-md px-3 py-2 text-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                    active
                      ? "bg-accent/12 font-semibold text-foreground"
                      : "text-muted hover:bg-surface hover:text-foreground"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="flex items-center gap-2">
            <span className="hidden items-center gap-2 rounded-full border border-border px-3 py-1.5 text-xs text-muted sm:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
              Control plane ready
            </span>
            <Link
              href="/"
              className="rounded-md px-2 py-2 text-sm text-muted transition hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Home
            </Link>
          </div>
        </div>
        <nav aria-label="Mobile navigation" className="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-5 pb-3 md:hidden sm:px-8">
          {navigation.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium ${active ? "bg-accent/12 text-foreground" : "text-muted"}`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>
      <main id="main-content" className="mx-auto w-full max-w-7xl px-5 py-8 sm:px-8 sm:py-10">
        {children}
      </main>
    </div>
  );
}
