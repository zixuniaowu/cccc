import type { Actor, ChatMessageData, GroupSettings, LedgerEvent, ReplyTarget } from "../types";

type ReplyComposerState = {
  destGroupId: string;
  toText: string;
  replyTarget: ReplyTarget;
};

export function buildReplyComposerState(
  event: LedgerEvent,
  selectedGroupId: string,
  actors: Actor[],
  groupSettings: GroupSettings | null | undefined
): ReplyComposerState | null {
  if (!event.id || event.kind !== "chat.message") return null;

  const data = event.data && typeof event.data === "object" ? (event.data as ChatMessageData) : null;
  const text = data && typeof data.text === "string" ? String(data.text) : "";
  const by = String(event.by || "").trim();
  const authorIsActor = by && by !== "user" && actors.some((actor) => String(actor.id || "") === by);
  const originalTo = Array.isArray(data?.to)
    ? data.to.map((token: string) => String(token || "").trim()).filter(Boolean)
    : [];
  const policy = groupSettings?.default_send_to || "foreman";
  const defaultTo =
    authorIsActor
      ? [by]
      : originalTo.length > 0
        ? originalTo
        : policy === "foreman"
          ? ["@foreman"]
          : [];

  return {
    destGroupId: String(selectedGroupId || "").trim(),
    toText: defaultTo.join(", "),
    replyTarget: {
      eventId: String(event.id),
      by: String(event.by || "unknown"),
      text: text.slice(0, 100) + (text.length > 100 ? "..." : ""),
    },
  };
}
