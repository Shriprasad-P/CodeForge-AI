import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GitHubPanel } from "@/components/github-panel";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: replace, replace }),
  useSearchParams: () => new URLSearchParams(),
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

describe("GitHubPanel", () => {
  it("shows not configured state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/auth/me")) return meOk();
        if (url.endsWith("/api/github/status")) {
          return Response.json({
            configured: false,
            linked: false,
            github_login: null,
            installation_count: 0,
            connection_count: 0,
          });
        }
        return new Response("not found", { status: 404 });
      }),
    );

    renderWithQuery(<GitHubPanel />);
    await waitFor(() => {
      expect(
        screen.getByText(/GitHub integration is not configured/i),
      ).toBeInTheDocument();
    });
  });

  it("shows connect button when configured but not linked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/auth/me")) return meOk();
        if (url.endsWith("/api/github/status")) {
          return Response.json({
            configured: true,
            linked: false,
            github_login: null,
            installation_count: 0,
            connection_count: 0,
          });
        }
        return new Response("not found", { status: 404 });
      }),
    );

    renderWithQuery(<GitHubPanel />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /connect github/i })).toBeInTheDocument();
    });
  });

  it("lists repositories and connects one", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return meOk();
      if (url.endsWith("/api/github/status")) {
        return Response.json({
          configured: true,
          linked: true,
          github_login: "ada",
          installation_count: 1,
          connection_count: 0,
        });
      }
      if (url.endsWith("/api/github/installations")) {
        return Response.json([
          {
            id: "22222222-2222-2222-2222-222222222222",
            github_installation_id: 99,
            account_login: "ada",
            account_type: "User",
            repository_selection: "selected",
            suspended: false,
            created_at: "2026-01-01T00:00:00Z",
          },
        ]);
      }
      if (url.includes("/api/github/repositories?")) {
        return Response.json({
          total_count: 1,
          page: 1,
          per_page: 30,
          repositories: [
            {
              id: 123,
              name: "demo",
              full_name: "ada/demo",
              private: true,
              default_branch: "main",
              html_url: "https://github.com/ada/demo",
              owner: "ada",
            },
          ],
        });
      }
      if (url.endsWith("/api/github/connections") && !init?.method) {
        return Response.json([]);
      }
      if (
        url.endsWith("/api/github/repositories/123/connect") &&
        init?.method === "POST"
      ) {
        return Response.json(
          {
            id: "33333333-3333-3333-3333-333333333333",
            github_repository_id: 123,
            installation_id: "22222222-2222-2222-2222-222222222222",
            owner: "ada",
            name: "demo",
            full_name: "ada/demo",
            default_branch: "main",
            private: true,
            html_url: "https://github.com/ada/demo",
            is_active: true,
            created_at: "2026-01-01T00:00:00Z",
          },
          { status: 201 },
        );
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithQuery(<GitHubPanel />);
    await waitFor(() => {
      expect(screen.getByText("ada/demo")).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /^connect$/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/github/repositories/123/connect"),
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("disconnects a connected repository", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return meOk();
      if (url.endsWith("/api/github/status")) {
        return Response.json({
          configured: true,
          linked: true,
          github_login: "ada",
          installation_count: 1,
          connection_count: 1,
        });
      }
      if (url.endsWith("/api/github/installations")) {
        return Response.json([
          {
            id: "22222222-2222-2222-2222-222222222222",
            github_installation_id: 99,
            account_login: "ada",
            account_type: "User",
            repository_selection: "all",
            suspended: false,
            created_at: "2026-01-01T00:00:00Z",
          },
        ]);
      }
      if (url.includes("/api/github/repositories?")) {
        return Response.json({
          total_count: 0,
          page: 1,
          per_page: 30,
          repositories: [],
        });
      }
      if (url.endsWith("/api/github/connections") && !init?.method) {
        return Response.json([
          {
            id: "33333333-3333-3333-3333-333333333333",
            github_repository_id: 123,
            installation_id: "22222222-2222-2222-2222-222222222222",
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
      if (
        url.endsWith("/api/github/connections/33333333-3333-3333-3333-333333333333") &&
        init?.method === "DELETE"
      ) {
        return new Response(null, { status: 204 });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithQuery(<GitHubPanel />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /disconnect/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /disconnect/i }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/github/connections/33333333-3333-3333-3333-333333333333"),
        expect.objectContaining({ method: "DELETE" }),
      );
    });
  });

  it("shows GitHub API error state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/auth/me")) return meOk();
        if (url.endsWith("/api/github/status")) {
          return Response.json({
            configured: true,
            linked: true,
            github_login: "ada",
            installation_count: 1,
            connection_count: 0,
          });
        }
        if (url.endsWith("/api/github/installations")) {
          return Response.json({ detail: "GitHub API request failed" }, { status: 502 });
        }
        if (url.endsWith("/api/github/connections")) {
          return Response.json([]);
        }
        return new Response("not found", { status: 404 });
      }),
    );

    renderWithQuery(<GitHubPanel />);
    await waitFor(() => {
      expect(screen.getByText(/GitHub API request failed/i)).toBeInTheDocument();
    });
  });

  it("redirects when unauthenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "Not authenticated" }, { status: 401 }),
      ),
    );

    renderWithQuery(<GitHubPanel />);
    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith("/login");
    });
  });

  it("shows loading state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/auth/me")) {
          await new Promise((resolve) => setTimeout(resolve, 50));
          return meOk();
        }
        return new Response("not found", { status: 404 });
      }),
    );

    renderWithQuery(<GitHubPanel />);
    expect(screen.getByText(/Loading GitHub integration/i)).toBeInTheDocument();
  });
});
