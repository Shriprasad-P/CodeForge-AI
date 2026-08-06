import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SystemStatus } from "@/components/system-status";

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("SystemStatus", () => {
  it("renders healthy dependency status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/health")) {
          return Response.json({
            status: "ok",
            service: "AgentDock",
            version: "0.1.0",
            timestamp: "2026-08-06T00:00:00Z",
          });
        }
        if (url.endsWith("/api/ready")) {
          return Response.json({
            status: "ready",
            checks: { postgres: true, redis: true },
            timestamp: "2026-08-06T00:00:00Z",
          });
        }
        return new Response("not found", { status: 404 });
      }),
    );

    renderWithQuery(<SystemStatus />);

    await waitFor(() => {
      expect(screen.getByText("up")).toBeInTheDocument();
    });
    expect(screen.getAllByText("ready")).toHaveLength(2);
  });

  it("shows an error when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network");
      }),
    );

    renderWithQuery(<SystemStatus />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach API");
    });
  });
});
