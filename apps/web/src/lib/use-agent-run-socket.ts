"use client";

import { useEffect, useRef, useState } from "react";

import { agentRunWsUrl } from "@/lib/ws";

export type AgentStreamEvent = {
  version: number;
  event: string;
  run_id: string;
  sequence: number;
  timestamp: string;
  data: Record<string, unknown>;
};

export type LiveLogLine = {
  stream: "stdout" | "stderr" | "system";
  text: string;
};

export type LiveActivity = {
  at: string;
  text: string;
};

type Options = {
  runId: string | null;
  enabled?: boolean;
  onDiffReady?: () => void;
  onStatusHint?: (status: string) => void;
  onNeedRestSync?: () => void;
};

const MAX_LOG_CHARS = 120_000;
const MAX_ACTIVITY = 200;
const SUPPORTED_VERSION = 1;
const MAX_RETRY_DELAY_MS = 8_000;
const PERMANENT_CLOSE_CODES = new Set([4401, 4403, 4404, 1008]);

export function useAgentRunSocket({
  runId,
  enabled = true,
  onDiffReady,
  onStatusHint,
  onNeedRestSync,
}: Options) {
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [wsFailed, setWsFailed] = useState(false);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [activity, setActivity] = useState<LiveActivity[]>([]);
  const [logs, setLogs] = useState<LiveLogLine[]>([]);
  const [changedFiles, setChangedFiles] = useState<
    { path?: string; change_type?: string }[]
  >([]);
  const seenSeq = useRef(new Set<number>());
  const backoff = useRef(500);
  const onDiffReadyRef = useRef(onDiffReady);
  const onStatusHintRef = useRef(onStatusHint);
  const onNeedRestSyncRef = useRef(onNeedRestSync);
  onDiffReadyRef.current = onDiffReady;
  onStatusHintRef.current = onStatusHint;
  onNeedRestSyncRef.current = onNeedRestSync;

  useEffect(() => {
    if (!runId || !enabled || typeof window === "undefined") return;
    const activeRunId = runId;

    seenSeq.current = new Set();
    setWsFailed(false);
    setReconnecting(false);
    setLiveStatus(null);
    setActivity([]);
    setLogs([]);
    setChangedFiles([]);
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stableTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;
    let retryAttempt = 0;

    function appendLog(line: LiveLogLine) {
      setLogs((prev) => {
        const next = [...prev, line];
        let total = next.reduce((n, row) => n + row.text.length, 0);
        while (next.length > 1 && total > MAX_LOG_CHARS) {
          total -= next[0].text.length;
          next.shift();
        }
        return next;
      });
    }

    function pushActivity(text: string, at?: string) {
      setActivity((prev) => {
        const row = { at: at || new Date().toISOString(), text };
        const next = [...prev, row];
        return next.length > MAX_ACTIVITY ? next.slice(-MAX_ACTIVITY) : next;
      });
    }

    function handleEvent(raw: AgentStreamEvent) {
      if (raw.version !== SUPPORTED_VERSION) return;
      if (raw.run_id !== activeRunId) return;
      if (raw.sequence > 0) {
        if (seenSeq.current.has(raw.sequence)) return;
        seenSeq.current.add(raw.sequence);
      }
      const data = raw.data || {};
      switch (raw.event) {
        case "agent.snapshot":
          if (typeof data.status === "string") {
            setLiveStatus(data.status);
            onStatusHintRef.current?.(data.status);
          }
          if (Array.isArray(data.changed_files)) {
            setChangedFiles(data.changed_files as { path?: string; change_type?: string }[]);
          }
          break;
        case "agent.run.queued":
        case "agent.run.started":
        case "agent.run.status":
        case "agent.run.completed":
        case "agent.run.failed":
        case "agent.run.cancelled":
        case "agent.run.timed_out":
        case "agent.run.step_limit_reached": {
          const status =
            typeof data.status === "string"
              ? data.status
              : raw.event.replace("agent.run.", "");
          setLiveStatus(status);
          onStatusHintRef.current?.(status);
          pushActivity(statusLabel(status), raw.timestamp);
          break;
        }
        case "agent.run.repository_revoked":
          setLiveStatus("repository_revoked");
          pushActivity("Repository authorization revoked", raw.timestamp);
          onNeedRestSyncRef.current?.();
          break;
        case "agent.tool.started":
          pushActivity(String(data.summary || data.tool || "Tool started"), raw.timestamp);
          break;
        case "agent.tool.completed":
          pushActivity(
            `${data.summary || data.tool || "Tool"} — ${data.ok === false ? "failed" : "done"}`,
            raw.timestamp,
          );
          break;
        case "agent.validation.started":
          pushActivity("Validation started", raw.timestamp);
          break;
        case "agent.validation.completed":
          pushActivity(
            data.ok ? "Validation passed" : "Validation failed",
            raw.timestamp,
          );
          break;
        case "agent.command.output": {
          const stream = data.stream === "stderr" ? "stderr" : "stdout";
          const chunk = typeof data.chunk === "string" ? data.chunk : "";
          if (chunk) appendLog({ stream, text: chunk });
          if (data.truncated) appendLog({ stream: "system", text: "\n[output truncated]\n" });
          break;
        }
        case "agent.files.changed":
          if (Array.isArray(data.files)) {
            setChangedFiles(data.files as { path?: string; change_type?: string }[]);
          }
          break;
        case "agent.diff.ready":
          pushActivity("Diff ready", raw.timestamp);
          onDiffReadyRef.current?.();
          break;
        case "agent.approval.required":
          pushActivity("Awaiting human approval", raw.timestamp);
          onStatusHintRef.current?.("awaiting_approval");
          break;
        case "agent.approved":
          setLiveStatus("approved");
          pushActivity("Publication approved", raw.timestamp);
          break;
        case "agent.rejected":
          setLiveStatus("rejected");
          pushActivity("Publication rejected", raw.timestamp);
          break;
        case "publication.started":
          setLiveStatus("publishing");
          pushActivity(statusLabel(raw.event.replace("publication.", "")), raw.timestamp);
          onNeedRestSyncRef.current?.();
          break;
        case "publication.validation.started":
        case "publication.validation.completed":
        case "publication.commit.created":
        case "publication.branch.pushed":
          pushActivity(statusLabel(raw.event.replace("publication.", "")), raw.timestamp);
          onNeedRestSyncRef.current?.();
          break;
        case "publication.pr.created":
          setLiveStatus("succeeded");
          pushActivity(statusLabel(raw.event.replace("publication.", "")), raw.timestamp);
          onNeedRestSyncRef.current?.();
          break;
        case "publication.failed":
          setLiveStatus("failed");
          pushActivity(statusLabel(raw.event.replace("publication.", "")), raw.timestamp);
          onNeedRestSyncRef.current?.();
          break;
        default:
          break;
      }
    }

    function connect() {
      if (disposed) return;
      try {
        socket = new WebSocket(agentRunWsUrl(activeRunId));
      } catch {
        setWsFailed(true);
        onNeedRestSyncRef.current?.();
        return;
      }

      socket.onopen = () => {
        if (disposed) return;
        setConnected(true);
        setReconnecting(false);
        if (stableTimer) clearTimeout(stableTimer);
        stableTimer = setTimeout(() => {
          retryAttempt = 0;
          backoff.current = 500;
        }, 2_000);
        onNeedRestSyncRef.current?.();
      };

      socket.onmessage = (ev) => {
        try {
          const parsed = JSON.parse(String(ev.data)) as AgentStreamEvent;
          handleEvent(parsed);
        } catch {
          // ignore malformed
        }
      };

      socket.onclose = (event) => {
        setConnected(false);
        if (disposed) return;
        onNeedRestSyncRef.current?.();
        const code = event?.code ?? 0;
        if (PERMANENT_CLOSE_CODES.has(code)) {
          setReconnecting(false);
          setWsFailed(true);
          return;
        }
        if (retryAttempt >= 8) {
          setReconnecting(false);
          setWsFailed(true);
          return;
        }
        setReconnecting(true);
        const base = Math.min(backoff.current, MAX_RETRY_DELAY_MS);
        // Small jitter prevents a fleet of clients from reconnecting in lockstep.
        const jitter = base * (0.75 + Math.random() * 0.5);
        const delay = Math.min(Math.round(jitter), MAX_RETRY_DELAY_MS);
        retryAttempt += 1;
        backoff.current = Math.min(backoff.current * 2, MAX_RETRY_DELAY_MS);
        timer = setTimeout(connect, delay);
      };

      socket.onerror = () => {
        onNeedRestSyncRef.current?.();
      };
    }

    connect();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      if (stableTimer) clearTimeout(stableTimer);
      socket?.close();
    };
  }, [runId, enabled]);

  return {
    connected,
    reconnecting,
    wsFailed,
    liveStatus,
    activity,
    logs,
    changedFiles,
  };
}

function statusLabel(status: string): string {
  return status
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
