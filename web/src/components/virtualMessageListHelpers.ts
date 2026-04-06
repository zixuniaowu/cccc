import type { LedgerEvent } from "../types";
import { getChatTailMutationSnapshot, getChatTailSnapshot, shouldAutoFollowOnTailAppend, shouldAutoFollowOnTailMutation } from "../utils/chatAutoFollow";

const VIRTUALIZATION_THRESHOLD = 80;

export function getAutoFollowTrigger(input: {
  previousTailSnapshot: ReturnType<typeof getChatTailSnapshot>;
  nextTailSnapshot: ReturnType<typeof getChatTailSnapshot>;
  previousTailMutationSnapshot: ReturnType<typeof getChatTailMutationSnapshot>;
  nextTailMutationSnapshot: ReturnType<typeof getChatTailMutationSnapshot>;
}): "append" | "mutation" | null {
  if (shouldAutoFollowOnTailAppend(input.previousTailSnapshot, input.nextTailSnapshot)) {
    return "append";
  }
  if (shouldAutoFollowOnTailMutation(input.previousTailMutationSnapshot, input.nextTailMutationSnapshot)) {
    return "mutation";
  }
  return null;
}

export function getStableMessageKey(message: LedgerEvent | undefined, index: number): string | number {
  if (message?.kind === "chat.message" && message.data && typeof message.data === "object") {
    const eventId = typeof message.id === "string" ? String(message.id || "").trim() : "";
    if (eventId && (message._streaming || eventId.startsWith("local:") || eventId.startsWith("stream:"))) {
      return `message-event:${eventId}`;
    }
    const pendingEventId = typeof (message.data as { pending_event_id?: unknown }).pending_event_id === "string"
      ? String((message.data as { pending_event_id?: string }).pending_event_id || "").trim()
      : "";
    const actorId = typeof message.by === "string" ? String(message.by || "").trim() : "";
    if (pendingEventId && actorId) return `pending:${actorId}:${pendingEventId}`;
    const streamId = typeof (message.data as { stream_id?: unknown }).stream_id === "string"
      ? String((message.data as { stream_id?: string }).stream_id || "").trim()
      : "";
    if (streamId) return `stream:${streamId}`;
    const clientId = typeof (message.data as { client_id?: unknown }).client_id === "string"
      ? String((message.data as { client_id?: string }).client_id || "").trim()
      : "";
    if (clientId) return `client:${clientId}`;
  }
  const eventId = typeof message?.id === "string" ? String(message.id || "").trim() : "";
  return eventId || index;
}

export function shouldUseVirtualizedMessageList(messageCount: number): boolean {
  return Math.max(0, Number(messageCount) || 0) >= VIRTUALIZATION_THRESHOLD;
}
