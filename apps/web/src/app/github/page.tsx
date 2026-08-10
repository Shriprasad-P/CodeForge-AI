import { Suspense } from "react";

import { GitHubPanel } from "@/components/github-panel";

export default function GitHubPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-6xl px-6 py-16">
      <Suspense fallback={<p className="text-muted">Loading GitHub integration…</p>}>
        <GitHubPanel />
      </Suspense>
    </main>
  );
}
