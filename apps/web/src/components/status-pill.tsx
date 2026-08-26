export function readableStatus(status: string | null | undefined): string {
  return (status || "unknown")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function statusTone(status: string | null | undefined): string {
  if (["succeeded", "approved", "connected", "ready"].includes(status || "")) {
    return "status-pill status-pill-ok";
  }
  if (["failed", "cancelled", "rejected", "step_limit_reached", "repository_revoked", "timed_out"].includes(status || "")) {
    return "status-pill status-pill-bad";
  }
  if (["awaiting_approval", "publishing", "validating"].includes(status || "")) {
    return "status-pill status-pill-warn";
  }
  return "status-pill status-pill-neutral";
}

export function StatusPill({ status }: { status: string | null | undefined }) {
  return <span className={statusTone(status)}>{readableStatus(status)}</span>;
}
