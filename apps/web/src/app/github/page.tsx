import { Suspense } from "react";

import { GitHubPanel } from "@/components/github-panel";
import { AppShell } from "@/components/app-shell";

export default function GitHubPage() {
  return (
    <AppShell>
      <Suspense fallback={<p className="text-muted">Loading GitHub integration…</p>}>
        <GitHubPanel />
      </Suspense>
    </AppShell>
  );
}
