export function buildCompactMessageSummary(
  kind: "mention" | "reply_required",
  actor: string,
): string {
  const displayActor = String(actor || "").trim() || "system";
  if (kind === "mention") {
    return `${displayActor} 给了一个可直接发送的建议。`;
  }
  return `${displayActor} 给了一个可直接回复的建议。`;
}
