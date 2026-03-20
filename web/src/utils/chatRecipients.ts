export function getEffectiveComposerRecipientText(
  toText: string,
  activeGroupId: string,
  selectedGroupId: string
): string {
  const active = String(activeGroupId || "").trim();
  const selected = String(selectedGroupId || "").trim();
  if (selected && active !== selected) return "";
  return String(toText || "");
}

export function getOptimisticRecipients(
  toTokens: string[],
  defaultSendTo: "foreman" | "broadcast" | undefined
): string[] {
  if (toTokens.length > 0) return toTokens;
  return defaultSendTo === "foreman" ? ["@foreman"] : [];
}
