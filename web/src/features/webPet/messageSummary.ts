import type { ReminderKind } from "./types";

export function buildCompactMessageSummary(
  kind: Extract<ReminderKind, "mention" | "reply_required">,
  actor: string,
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): string {
  const displayActor = String(actor || "").trim() || "system";
  if (kind === "mention") {
    return tr("reminderSummary.mentionCompact", "{{actor}} mentioned you.", {
      actor: displayActor,
    });
  }
  return tr("reminderSummary.replyRequiredCompact", "{{actor}} needs your reply.", {
    actor: displayActor,
  });
}
