import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentPanel } from "@/components/agent-panel";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: replace, replace }),
}));

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  replace.mockReset();
});

beforeEach(() => {
  class MockWebSocket {
    onopen: ((ev?: unknown) => void) | null = null;
    onmessage: ((ev: { data: string }) => void) | null = null;
    onclose: ((ev?: unknown) => void) | null = null;
    onerror: ((ev?: unknown) => void) | null = null;
    close = vi.fn();
    send = vi.fn();
    constructor(url: string) {
      void url;
      queueMicrotask(() => this.onopen?.({}));
    }
  }
  vi.stubGlobal("WebSocket", MockWebSocket);
});

describe("AgentPanel", () => {
  it("shows unconfigured state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/auth/me")) {
          return Response.json({
            id: "11111111-1111-1111-1111-111111111111",
            email: "ada@example.com",
            display_name: "Ada",
          });
        }
        if (url.endsWith("/api/agent-runs/status")) {
          return Response.json({ configured: false, provider: "", model: "" });
        }
        if (url.endsWith("/api/github/connections")) return Response.json([]);
        if (url.endsWith("/api/agent-runs")) return Response.json([]);
        return new Response("not found", { status: 404 });
      }),
    );
    renderWithQuery(<AgentPanel />);
    await waitFor(() => {
      expect(screen.getByText(/Agent LLM is not configured/i)).toBeInTheDocument();
    });
  });

  it("starts an agent run when configured", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) {
        return Response.json({
          id: "11111111-1111-1111-1111-111111111111",
          email: "ada@example.com",
          display_name: "Ada",
        });
      }
      if (url.endsWith("/api/agent-runs/status")) {
        return Response.json({ configured: true, provider: "fake", model: "fake" });
      }
      if (url.endsWith("/api/github/connections")) {
        return Response.json([
          {
            id: "22222222-2222-2222-2222-222222222222",
            github_repository_id: 1,
            installation_id: "33333333-3333-3333-3333-333333333333",
            owner: "ada",
            name: "demo",
            full_name: "ada/demo",
            default_branch: "main",
            private: false,
            html_url: "https://github.com/ada/demo",
            is_active: true,
            created_at: "2026-01-01T00:00:00Z",
          },
        ]);
      }
      if (url.endsWith("/api/agent-runs") && init?.method === "POST") {
        return Response.json(
          {
            id: "44444444-4444-4444-4444-444444444444",
            repository_connection_id: "22222222-2222-2222-2222-222222222222",
            agent_session_id: null,
            status: "queued",
            task: "Add sum",
            model_provider: "fake",
            model_name: "fake",
            max_steps: 20,
            steps_used: 0,
            tool_calls_used: 0,
            cancel_requested: false,
            summary: null,
            result_status: null,
            changed_files: null,
            validation: null,
            diff_truncated: false,
            error_type: null,
            error_message: null,
            started_at: null,
            finished_at: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
          { status: 201 },
        );
      }
      if (url.endsWith("/api/agent-runs") && !init?.method) {
        return Response.json([]);
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithQuery(<AgentPanel />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start agent run/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /start agent run/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/agent-runs"),
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
