import type { ReminderKind } from "./types";

export function buildCompactMessageSummary(
  kind: Extract<ReminderKind, "mention" | "reply_required">,
  actor: string,
): string {
  const displayActor = String(actor || "").trim() || "system";
  if (kind === "mention") {
    return `${displayActor} 提到了你，需要你查看。`;
  }
  return `${displayActor} 在等你回复。`;
}
