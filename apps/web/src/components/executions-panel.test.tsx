import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExecutionsPanel } from "@/components/executions-panel";

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

function meOk() {
  return Response.json({
    id: "11111111-1111-1111-1111-111111111111",
    email: "ada@example.com",
    display_name: "Ada",
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  replace.mockReset();
});

describe("ExecutionsPanel", () => {
  it("starts an execution and shows status", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return meOk();
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
      if (url.endsWith("/api/executions") && init?.method === "POST") {
        return Response.json(
          {
            id: "44444444-4444-4444-4444-444444444444",
            repository_connection_id: "22222222-2222-2222-2222-222222222222",
            agent_session_id: null,
            status: "queued",
            command: ["python", "hello.py"],
            working_directory: null,
            exit_code: null,
            error_type: null,
            error_message: null,
            output_truncated: false,
            cancel_requested: false,
            started_at: null,
            finished_at: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
          { status: 201 },
        );
      }
      if (url.endsWith("/api/executions") && !init?.method) {
        return Response.json([
          {
            id: "44444444-4444-4444-4444-444444444444",
            repository_connection_id: "22222222-2222-2222-2222-222222222222",
            agent_session_id: null,
            status: "succeeded",
            command: ["python", "hello.py"],
            working_directory: null,
            exit_code: 0,
            error_type: null,
            error_message: null,
            output_truncated: false,
            cancel_requested: false,
            started_at: "2026-01-01T00:00:00Z",
            finished_at: "2026-01-01T00:00:01Z",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:01Z",
          },
        ]);
      }
      if (url.endsWith("/api/executions/44444444-4444-4444-4444-444444444444/logs")) {
        return Response.json({
          id: "44444444-4444-4444-4444-444444444444",
          status: "succeeded",
          stdout: "hello from fixture\n",
          stderr: "",
          output_truncated: false,
          exit_code: 0,
        });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithQuery(<ExecutionsPanel />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /start execution/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /start execution/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/executions"),
        expect.objectContaining({ method: "POST" }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText(/hello from fixture/i)).toBeInTheDocument();
    });
  });

  it("redirects when unauthenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "Not authenticated" }, { status: 401 }),
      ),
    );
    renderWithQuery(<ExecutionsPanel />);
    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith("/login");
    });
  });
});
