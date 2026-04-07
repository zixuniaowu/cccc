import type { LedgerEvent } from "../types";
import { getChatTailMutationSnapshot, getChatTailSnapshot, shouldAutoFollowOnTailAppend, shouldAutoFollowOnTailMutation } from "../utils/chatAutoFollow";
import { hasRenderableChatMessageContent } from "../utils/ledgerEventHandlers";

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
    const streamId = typeof (message.data as { stream_id?: unknown }).stream_id === "string"
      ? String((message.data as { stream_id?: string }).stream_id || "").trim()
      : "";
    const isPlaceholderLike =
      Boolean((message.data as { pending_placeholder?: unknown }).pending_placeholder) ||
      streamId.startsWith("local:") ||
      streamId.startsWith("pending:");
    // Daemon-emitted canonical replies can carry both stream_id and
    // pending_event_id. Renderable stream-backed rows must key by stream_id,
    // otherwise TanStack reuses DOM/measurement state across distinct messages
    // from the same reply slot and produces gaps/overlap.
    if (streamId && !isPlaceholderLike) {
      return `stream:${streamId}`;
    }
    const pendingEventId = typeof (message.data as { pending_event_id?: unknown }).pending_event_id === "string"
      ? String((message.data as { pending_event_id?: string }).pending_event_id || "").trim()
      : "";
    const actorId = typeof message.by === "string" ? String(message.by || "").trim() : "";
    if (pendingEventId && actorId && (!hasRenderableChatMessageContent(message) || isPlaceholderLike)) {
      return `pending:${actorId}:${pendingEventId}`;
    }
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
