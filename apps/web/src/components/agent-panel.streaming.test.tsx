import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentPanel } from "@/components/agent-panel";
import { getWsBaseUrl, agentRunWsUrl } from "@/lib/ws";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: replace, replace }),
}));

type FakeSocket = {
  onopen: ((ev?: unknown) => void) | null;
  onmessage: ((ev: { data: string }) => void) | null;
  onclose: ((ev?: unknown) => void) | null;
  onerror: ((ev?: unknown) => void) | null;
  close: () => void;
  send: (data: string) => void;
  readyState: number;
};

function makeSocket(): FakeSocket {
  const socket: FakeSocket = {
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    readyState: 0,
    close: vi.fn(() => {
      socket.readyState = 3;
      socket.onclose?.({});
    }),
    send: vi.fn(),
  };
  return socket;
}

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const runId = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";

function stubAuthAndRun(fetchExtra?: (url: string, init?: RequestInit) => Response | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const extra = fetchExtra?.(url, init);
      if (extra) return extra;
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
          },
        ]);
      }
      if (url.endsWith("/api/agent-runs") && (!init || init.method === undefined || init.method === "GET")) {
        return Response.json([
          {
            id: runId,
            repository_connection_id: "22222222-2222-2222-2222-222222222222",
            agent_session_id: null,
            status: "running",
            task: "Ship it",
            model_provider: "fake",
            model_name: "fake",
            max_steps: 20,
            steps_used: 1,
            tool_calls_used: 1,
            cancel_requested: false,
            summary: null,
            result_status: null,
            changed_files: [],
            validation: null,
            diff_truncated: false,
            error_type: null,
            error_message: null,
            started_at: null,
            finished_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ]);
      }
      if (url.includes("/steps")) return Response.json([]);
      if (url.includes("/diff")) {
        return Response.json({
          id: runId,
          status: "running",
          diff_stat: "",
          diff_text: "",
          diff_truncated: false,
          changed_files: [],
        });
      }
      return new Response("not found", { status: 404 });
    }),
  );
}

describe("ws url helpers", () => {
  it("derives ws from http api url", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://localhost:8000");
    vi.stubEnv("NEXT_PUBLIC_WS_URL", "");
    expect(getWsBaseUrl()).toBe("ws://localhost:8000");
    expect(agentRunWsUrl("r1")).toBe("ws://localhost:8000/ws/agent-runs/r1");
  });

  it("derives wss from https", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example.com");
    vi.stubEnv("NEXT_PUBLIC_WS_URL", "");
    expect(getWsBaseUrl()).toBe("wss://api.example.com");
  });
});

describe("AgentPanel live streaming", () => {
  let sockets: FakeSocket[];

  beforeEach(() => {
    sockets = [];
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      constructor(url: string) {
      void url;
        const sock = makeSocket();
        sockets.push(sock);
        queueMicrotask(() => {
          sock.readyState = 1;
          sock.onopen?.({});
        });
        return sock as unknown as WebSocket;
      }
    }
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    replace.mockReset();
  });

  it("renders live activity and command output from websocket events", async () => {
    stubAuthAndRun();
    renderWithQuery(<AgentPanel />);
    await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
    const sock = sockets[0];
    sock.onmessage?.({
      data: JSON.stringify({
        version: 1,
        event: "agent.tool.started",
        run_id: runId,
        sequence: 1,
        timestamp: new Date().toISOString(),
        data: { tool: "read_file", summary: 'Reading app/validators.py' },
      }),
    });
    sock.onmessage?.({
      data: JSON.stringify({
        version: 1,
        event: "agent.command.output",
        run_id: runId,
        sequence: 2,
        timestamp: new Date().toISOString(),
        data: { stream: "stdout", chunk: "<script>alert(1)</script>\n" },
      }),
    });
    sock.onmessage?.({
      data: JSON.stringify({
        version: 1,
        event: "agent.run.status",
        run_id: runId,
        sequence: 3,
        timestamp: new Date().toISOString(),
        data: { status: "validating" },
      }),
    });

    await waitFor(() => {
      expect(screen.getByTestId("live-activity")).toHaveTextContent("Reading app/validators.py");
    });
    const output = screen.getByTestId("command-output");
    expect(output.textContent).toContain("<script>alert(1)</script>");
    // Must remain text — no HTML execution nodes
    expect(output.querySelector("script")).toBeNull();
    expect(screen.getByTestId("run-status")).toHaveTextContent("Validating");
  });

  it("shows reconnecting indicator after socket close", async () => {
    stubAuthAndRun();
    renderWithQuery(<AgentPanel />);
    await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
    sockets[0].onclose?.({});
    await waitFor(() => {
      expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
    });
  });

  it("cancels via REST", async () => {
    const user = await import("@testing-library/user-event").then((m) => m.default.setup());
    let cancelled = false;
    stubAuthAndRun((url, init) => {
      if (url.includes("/cancel") && init?.method === "POST") {
        cancelled = true;
        return Response.json({
          id: runId,
          repository_connection_id: "22222222-2222-2222-2222-222222222222",
          agent_session_id: null,
          status: "cancelled",
          task: "Ship it",
          model_provider: "fake",
          model_name: "fake",
          max_steps: 20,
          steps_used: 1,
          tool_calls_used: 1,
          cancel_requested: true,
          summary: null,
          result_status: null,
          changed_files: [],
          validation: null,
          diff_truncated: false,
          error_type: "cancelled",
          error_message: "Cancelled",
          started_at: null,
          finished_at: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        });
      }
      return null;
    });
    renderWithQuery(<AgentPanel />);
    await waitFor(() => screen.getByRole("button", { name: /cancel/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(cancelled).toBe(true));
  });
});
