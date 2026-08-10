import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/dashboard";

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

describe("Dashboard", () => {
  it("shows welcome content when authenticated", async () => {
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
        return new Response("not found", { status: 404 });
      }),
    );

    renderWithQuery(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText(/Welcome, Ada/)).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: /manage github/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /executions/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /coding agent/i })).toBeInTheDocument();
  });

  it("redirects when unauthenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "Not authenticated" }, { status: 401 }),
      ),
    );

    renderWithQuery(<Dashboard />);
    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith("/login");
    });
  });

  it("logs out and redirects", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/auth/me")) {
          return Response.json({
            id: "11111111-1111-1111-1111-111111111111",
            email: "ada@example.com",
            display_name: "Ada",
          });
        }
        if (url.endsWith("/api/auth/logout") && init?.method === "POST") {
          return new Response(null, { status: 204 });
        }
        return new Response("not found", { status: 404 });
      }),
    );

    renderWithQuery(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /sign out/i }));
    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith("/login");
    });
  });
});
