import type { RuntimeDockItem } from "./runtimeDockItems";

export type RuntimeRingTone = "stopped" | "ready" | "queued" | "active" | "attention";

export function getRuntimeRingTone(item: Pick<RuntimeDockItem, "liveWorkCard">, isRunning: boolean, workingState: string): RuntimeRingTone {
  const actorState = String(workingState || "").trim().toLowerCase();
  if (item.liveWorkCard?.phase === "failed") return "attention";
  if (!isRunning) return "stopped";
  if (actorState === "stuck") return "attention";
  if (item.liveWorkCard?.phase === "pending") return "queued";
  if (item.liveWorkCard?.phase === "streaming") return "active";
  if (actorState === "working" || actorState === "waiting") return "active";
  return "ready";
}
