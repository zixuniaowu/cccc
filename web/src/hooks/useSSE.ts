// SSE connection management for the ledger stream.
import { useEffect, useRef } from "react";
import { useGroupStore, useUIStore, useModalStore } from "../stores";
import {
  getOutboxEntry,
  releaseTransferredPreviewUrls,
  transferOutboxPreviewUrls,
  useChatOutboxStore,
} from "../stores/chatOutboxStore";
import { beginContextRequest, isLatestContextRequest } from "../stores/useGroupStore";
import * as api from "../services/api";
import type { FetchContextOptions } from "../services/api";
import type { Actor, ChatMessageData, CodexStreamEvent, GroupContext, StreamingActivity } from "../types";
import { runReconnectCatchup, scheduleContextSummaryCatchup } from "./sseCatchup";
import {
  isContextSyncEvent,
  isChatReadEvent,
  isChatAckEvent,
  isChatMessageEvent,
  isActorActivityEvent,
  extractChatReadData,
  extractChatAckData,
  initializeReadStatus,
  initializeAckStatus,
  initializeObligationStatus,
  shouldIncrementUnread,
  getActorRefreshMode,
  isPresentationPublishEvent,
  isPresentationClearEvent,
  // Re-export for consumers
  getRecipientActorIdsForEvent,
  getAckRecipientIdsForEvent,
} from "../utils/ledgerEventHandlers";
import { getPresentationMessageRefs, getPresentationRefStatus } from "../utils/presentationRefs";
import { mergeLedgerEvents } from "../utils/mergeLedgerEvents";

// Re-export for backward compatibility
export { getRecipientActorIdsForEvent, getAckRecipientIdsForEvent };

const MAX_RECONCILED_EVENTS = 800;
const RECONNECT_LEDGER_TAIL_LIMIT = 60;

function hasRenderableChatMessageContent(event: Record<string, unknown>): boolean {
  if (String(event.kind || "").trim() !== "chat.message") return false;
  const data = event.data && typeof event.data === "object"
    ? event.data as { text?: unknown; attachments?: unknown; refs?: unknown }
    : null;
  const text = typeof data?.text === "string" ? data.text.trim() : "";
  if (text) return true;
  const attachments = Array.isArray(data?.attachments) ? data.attachments : [];
  if (attachments.length > 0) return true;
  const refs = Array.isArray(data?.refs) ? data.refs : [];
  return refs.length > 0;
}

function mergeCanonicalAttachmentsWithOptimisticPreview(
  ev: Record<string, unknown>,
  groupId: string,
): { event: Record<string, unknown>; transferredPreviewUrls: string[] } {
  if (String(ev.kind || "").trim() !== "chat.message" || String(ev.by || "").trim() !== "user") {
    return { event: ev, transferredPreviewUrls: [] };
  }
  const data = ev.data && typeof ev.data === "object" ? (ev.data as Record<string, unknown>) : null;
  const clientId = data && typeof data.client_id === "string" ? data.client_id.trim() : "";
  if (!clientId) {
    return { event: ev, transferredPreviewUrls: [] };
  }

  const outboxEntry = getOutboxEntry(groupId, clientId);
  const optimisticData = outboxEntry?.event?.data && typeof outboxEntry.event.data === "object"
    ? (outboxEntry.event.data as { attachments?: unknown[] })
    : null;
  const optimisticAttachments = Array.isArray(optimisticData?.attachments) ? optimisticData.attachments : [];
  const canonicalAttachments = Array.isArray(data?.attachments) ? data.attachments : [];
  if (optimisticAttachments.length <= 0 || canonicalAttachments.length <= 0) {
    return { event: ev, transferredPreviewUrls: [] };
  }

  const mergedAttachments = canonicalAttachments.map((attachment, index) => {
    if (!attachment || typeof attachment !== "object") return attachment;
    const optimistic = optimisticAttachments[index];
    if (!optimistic || typeof optimistic !== "object") return attachment;
    const previewUrl = typeof (optimistic as { local_preview_url?: unknown }).local_preview_url === "string"
      ? String((optimistic as { local_preview_url?: string }).local_preview_url || "").trim()
      : "";
    if (!previewUrl.startsWith("blob:")) return attachment;
    return {
      ...attachment,
      local_preview_url: previewUrl,
    };
  });

  const transferredPreviewUrls = transferOutboxPreviewUrls(groupId, clientId);
  return {
    event: {
      ...ev,
      data: {
        ...data,
        attachments: mergedAttachments,
      },
    },
    transferredPreviewUrls,
  };
}

interface UseSSEOptions {
  activeTabRef: React.MutableRefObject<string>;
  chatAtBottomRef: React.MutableRefObject<boolean>;
  actorsRef: React.MutableRefObject<Actor[]>;
}

export function useSSE({ activeTabRef, chatAtBottomRef, actorsRef }: UseSSEOptions) {
  // Use individual selectors to avoid subscribing to the entire store.
  // Without selectors, every state change (e.g. appendEvent) would trigger
  // a re-render cascade through App.tsx → all children.
  const selectedGroupId = useGroupStore((s) => s.selectedGroupId);
  const appendEvent = useGroupStore((s) => s.appendEvent);
  const updateReadStatus = useGroupStore((s) => s.updateReadStatus);
  const updateAckStatus = useGroupStore((s) => s.updateAckStatus);
  const updateReplyStatus = useGroupStore((s) => s.updateReplyStatus);
  const incrementActorUnread = useGroupStore((s) => s.incrementActorUnread);
  const updateActorActivity = useGroupStore((s) => s.updateActorActivity);
  const upsertStreamingActivity = useGroupStore((s) => s.upsertStreamingActivity);
  const promoteStreamingEventToStream = useGroupStore((s) => s.promoteStreamingEventToStream);
  const reconcileStreamingMessage = useGroupStore((s) => s.reconcileStreamingMessage);
  const completeStreamingEventsForActor = useGroupStore((s) => s.completeStreamingEventsForActor);
  const removeStreamingEvent = useGroupStore((s) => s.removeStreamingEvent);
  const clearStreamingEventsForActor = useGroupStore((s) => s.clearStreamingEventsForActor);
  const clearEmptyStreamingEventsForActor = useGroupStore((s) => s.clearEmptyStreamingEventsForActor);
  const clearTransientStreamingEventsForActor = useGroupStore((s) => s.clearTransientStreamingEventsForActor);
  const setGroupContext = useGroupStore((s) => s.setGroupContext);
  const updateGroupRuntimeState = useGroupStore((s) => s.updateGroupRuntimeState);
  const refreshActors = useGroupStore((s) => s.refreshActors);
  const refreshPresentation = useGroupStore((s) => s.refreshPresentation);

  const incrementChatUnread = useUIStore((s) => s.incrementChatUnread);
  const setSSEStatus = useUIStore((s) => s.setSSEStatus);
  const markPresentationSlotAttention = useModalStore((s) => s.markPresentationSlotAttention);
  const clearPresentationSlotAttention = useModalStore((s) => s.clearPresentationSlotAttention);

  const eventSourceRef = useRef<EventSource | null>(null);
  const codexEventSourceRef = useRef<EventSource | null>(null);
  const contextRefreshTimerRef = useRef<number | null>(null);
  const selectedGroupIdRef = useRef<string>("");
  const reconnectDelayRef = useRef<number>(1000);
  const reconnectTimerRef = useRef<number | null>(null);
  const codexReconnectDelayRef = useRef<number>(1000);
  const codexReconnectTimerRef = useRef<number | null>(null);
  const hasConnectedOnceRef = useRef<boolean>(false);
  const pendingCodexMessageFlushRef = useRef<number | null>(null);
  const pendingCodexActivityFlushRef = useRef<number | null>(null);
  const pendingCodexMessagesRef = useRef(new Map<string, {
    groupId: string;
    actorId: string;
    streamId: string;
    pendingEventId: string;
    ts: string;
    explicitText: string | null;
    deltaText: string;
    completed: boolean;
    shouldClearPlaceholder: boolean;
    transientStream: boolean;
    phase: string;
  }>());
  const pendingCodexActivitiesRef = useRef(new Map<string, {
    actorId: string;
    groupId: string;
    match: { pendingEventId?: string; streamId?: string };
    activities: Map<string, StreamingActivity>;
  }>());

  useEffect(() => {
    selectedGroupIdRef.current = selectedGroupId;
  }, [selectedGroupId]);

  function getNotifyTargetActorId(ev: unknown): string {
    if (ev === null || typeof ev !== "object") return "";
    const kind = String((ev as { kind?: unknown }).kind || "").trim();
    if (kind !== "system.notify") return "";
    const data = (ev as { data?: unknown }).data;
    if (!data || typeof data !== "object") return "";
    const targetActorId = String((data as { target_actor_id?: unknown }).target_actor_id || "").trim();
    return targetActorId && targetActorId !== "user" ? targetActorId : "";
  }

  async function fetchContext(groupId: string, opts?: FetchContextOptions) {
    if (opts?.fresh && contextRefreshTimerRef.current) {
      window.clearTimeout(contextRefreshTimerRef.current);
      contextRefreshTimerRef.current = null;
    }
    const contextEpoch = beginContextRequest(groupId);
    const resp = await api.fetchContext(groupId, {
      fresh: opts?.fresh,
      detail: opts?.detail ?? "summary",
    });
    if (
      resp.ok &&
      resp.result &&
      typeof resp.result === "object" &&
      selectedGroupIdRef.current === groupId &&
      isLatestContextRequest(groupId, contextEpoch)
    ) {
      setGroupContext(resp.result as GroupContext);
    }
  }

  async function reconcileLedgerTail(groupId: string) {
    const resp = await api.fetchLedgerTail(groupId, RECONNECT_LEDGER_TAIL_LIMIT, { includeStatuses: false });
    if (!resp.ok || selectedGroupIdRef.current !== groupId) return;

    const store = useGroupStore.getState();
    const bucket = store.chatByGroup[groupId];
    const currentEvents = Array.isArray(bucket?.events) ? bucket.events : [];
    const fetchedEvents = Array.isArray(resp.result.events) ? resp.result.events : [];
    const nextEvents = mergeLedgerEvents(currentEvents, fetchedEvents, MAX_RECONCILED_EVENTS);

    store.setEvents(
      nextEvents,
      groupId
    );
    store.setHasMoreHistory(!!resp.result.has_more, groupId);
    const eventIds = nextEvents
      .filter((event) => event.kind === "chat.message")
      .map((event) => String(event.id || "").trim())
      .filter((eventId) => eventId);
    if (eventIds.length > 0) {
      const statusesResp = await api.fetchLedgerStatuses(groupId, eventIds);
      if (statusesResp.ok && selectedGroupIdRef.current === groupId) {
        store.mergeEventStatuses(statusesResp.result.statuses || {}, groupId);
      }
    }
  }

  async function resyncAfterReconnect(groupId: string) {
    await runReconnectCatchup(groupId, {
      invalidateContextRead: api.invalidateContextRead,
      reconcileLedgerTail,
      refreshActors,
      fetchContextSummary: fetchContext,
    });
  }

  function flushPendingCodexMessages(targetGroupId?: string, targetActorId?: string) {
    if (targetGroupId == null && targetActorId == null) {
      pendingCodexMessageFlushRef.current = null;
    }
    const pendingEntries = pendingCodexMessagesRef.current;
    if (pendingEntries.size <= 0) return;

    for (const [key, entry] of pendingEntries.entries()) {
      if (targetGroupId && entry.groupId !== targetGroupId) continue;
      if (targetActorId && entry.actorId !== targetActorId) continue;
      const streamingEvents = useGroupStore.getState().chatByGroup[entry.groupId]?.streamingEvents || [];
      const placeholder = entry.pendingEventId
        ? streamingEvents.find((item) => {
            if (String(item.by || "").trim() !== entry.actorId) return false;
            const itemData = item.data && typeof item.data === "object"
              ? item.data as { pending_event_id?: unknown; pending_placeholder?: unknown }
              : undefined;
            return Boolean(itemData?.pending_placeholder) && String(itemData?.pending_event_id || "").trim() === entry.pendingEventId;
          })
        : undefined;
      const existing = streamingEvents.find((item) => {
        const itemStreamId = item.data && typeof item.data === "object"
          ? String((item.data as { stream_id?: unknown }).stream_id || "").trim()
          : "";
        return itemStreamId === entry.streamId;
      });
      const bucket = useGroupStore.getState().chatByGroup[entry.groupId];
      const previousStreamText = String(bucket?.streamingTextByStreamId?.[entry.streamId] || "");
      const previousEventText = existing?.data && typeof existing.data === "object"
        ? String((existing.data as { text?: unknown }).text || "")
        : "";
      const existingData = existing?.data && typeof existing.data === "object"
        ? existing.data as {
            pending_event_id?: unknown;
            pending_placeholder?: unknown;
            text?: unknown;
            transient_stream?: unknown;
            stream_phase?: unknown;
          }
        : undefined;
      const previousPhase = String(existingData?.stream_phase || "").trim().toLowerCase();
      const nextPhase = String(entry.phase || "").trim().toLowerCase();
      const hasIncomingPhaseText =
        entry.explicitText != null
          ? entry.explicitText.length > 0
          : entry.deltaText.length > 0;
      const shouldResetTextForPhaseTransition =
        !!nextPhase &&
        previousPhase !== nextPhase &&
        previousPhase.length > 0 &&
        hasIncomingPhaseText;
      const previousText = shouldResetTextForPhaseTransition ? "" : (previousStreamText || previousEventText);
      const previousActivities = (() => {
        const source = existing ?? placeholder;
        if (!source?.data || typeof source.data !== "object") return [];
        const activities = (source.data as { activities?: unknown }).activities;
        return Array.isArray(activities) ? activities : [];
      })();
      const fullText = entry.explicitText ?? `${previousText}${entry.deltaText}`;
      const nextPlaceholderState = !fullText.trim() && previousActivities.length <= 0;
      const nextEventText =
        fullText
          ? fullText
          : shouldResetTextForPhaseTransition
            ? ""
            : previousEventText;
      const existingPendingEventId = String(existingData?.pending_event_id || "").trim();
      const needsEventUpsert =
        !existing ||
        !!existing._streaming !== !entry.completed ||
        existingPendingEventId !== entry.pendingEventId ||
        String(existingData?.text || "") !== nextEventText ||
        Boolean(existingData?.transient_stream) !== entry.transientStream ||
        String(existingData?.stream_phase || "") !== entry.phase ||
        Boolean(existingData?.pending_placeholder) !== nextPlaceholderState ||
        previousStreamText !== fullText;
      if (needsEventUpsert) {
        reconcileStreamingMessage({
          actorId: entry.actorId,
          pendingEventId: entry.pendingEventId,
          streamId: entry.streamId,
          ts: entry.ts,
          fullText,
          eventText: nextEventText,
          activities: previousActivities,
          completed: entry.completed,
          transientStream: entry.transientStream,
          phase: entry.phase || undefined,
          groupId: entry.groupId,
        });
      }
      pendingEntries.delete(key);
    }
  }

  function schedulePendingCodexMessageFlush(groupId: string) {
    if (pendingCodexMessageFlushRef.current != null) return;
    pendingCodexMessageFlushRef.current = window.requestAnimationFrame(() => {
      pendingCodexMessageFlushRef.current = null;
      flushPendingCodexMessages(groupId);
    });
  }

  function flushPendingCodexActivities(targetGroupId?: string, targetActorId?: string) {
    if (targetGroupId == null && targetActorId == null) {
      pendingCodexActivityFlushRef.current = null;
    }
    const pendingEntries = pendingCodexActivitiesRef.current;
    if (pendingEntries.size <= 0) return;

    for (const [key, entry] of pendingEntries.entries()) {
      if (targetGroupId && entry.groupId !== targetGroupId) continue;
      if (targetActorId && entry.actorId !== targetActorId) continue;
      for (const activity of entry.activities.values()) {
        upsertStreamingActivity(entry.actorId, entry.match, activity, entry.groupId);
      }
      pendingEntries.delete(key);
    }
  }

  function schedulePendingCodexActivityFlush() {
    if (pendingCodexActivityFlushRef.current != null) return;
    pendingCodexActivityFlushRef.current = window.requestAnimationFrame(() => {
      pendingCodexActivityFlushRef.current = null;
      flushPendingCodexActivities();
    });
  }

  function clearPendingCodexBuffers(groupId: string, actorId: string) {
    const targetGroupId = String(groupId || "").trim();
    const targetActorId = String(actorId || "").trim();
    if (!targetGroupId || !targetActorId) return;

    for (const [key, entry] of pendingCodexMessagesRef.current.entries()) {
      if (key.startsWith(`${targetGroupId}:`) && entry.actorId === targetActorId) {
        pendingCodexMessagesRef.current.delete(key);
      }
    }

    for (const [key, entry] of pendingCodexActivitiesRef.current.entries()) {
      if (entry.groupId === targetGroupId && entry.actorId === targetActorId) {
        pendingCodexActivitiesRef.current.delete(key);
      }
    }

    if (pendingCodexMessagesRef.current.size === 0 && pendingCodexMessageFlushRef.current != null) {
      window.cancelAnimationFrame(pendingCodexMessageFlushRef.current);
      pendingCodexMessageFlushRef.current = null;
    }
    if (pendingCodexActivitiesRef.current.size === 0 && pendingCodexActivityFlushRef.current != null) {
      window.cancelAnimationFrame(pendingCodexActivityFlushRef.current);
      pendingCodexActivityFlushRef.current = null;
    }
  }

  function handleCodexEvent(groupId: string, ev: CodexStreamEvent) {
    try {
      const actorId = String(ev.actor_id || "").trim();
      const eventType = String(ev.type || "").trim();
      const data = ev.data && typeof ev.data === "object" ? ev.data : {};
      const streamId = typeof data.stream_id === "string" ? data.stream_id.trim() : "";
      const pendingEventId = typeof data.event_id === "string" ? data.event_id.trim() : "";
      if (!actorId || !eventType) return;

      if (eventType === "codex.turn.started" || eventType === "codex.turn.progress") {
        updateActorActivity([{
          id: actorId,
          running: true,
          idle_seconds: null,
          effective_working_state: "working",
          effective_working_reason: "codex_turn_active",
          effective_working_updated_at: typeof ev.ts === "string" ? ev.ts : null,
          effective_active_task_id: typeof data.turn_id === "string" ? data.turn_id : null,
        }]);
        return;
      }

      if (eventType === "codex.turn.completed" || eventType === "codex.turn.failed") {
        flushPendingCodexActivities(groupId, actorId);
        flushPendingCodexMessages(groupId, actorId);
        clearPendingCodexBuffers(groupId, actorId);
        completeStreamingEventsForActor(actorId, groupId);
        clearTransientStreamingEventsForActor(actorId, groupId);
        updateActorActivity([{
          id: actorId,
          running: true,
          idle_seconds: null,
          effective_working_state: "idle",
          effective_working_reason: "codex_turn_idle",
          effective_working_updated_at: typeof ev.ts === "string" ? ev.ts : null,
          effective_active_task_id: null,
        }]);
        if (eventType === "codex.turn.failed") {
          clearStreamingEventsForActor(actorId, groupId);
        } else {
          clearEmptyStreamingEventsForActor(actorId, groupId);
        }
        return;
      }

      if (eventType === "codex.activity.started" || eventType === "codex.activity.updated" || eventType === "codex.activity.completed") {
        const activityId = typeof data.activity_id === "string" ? data.activity_id.trim() : "";
        const summary = typeof data.summary === "string" ? data.summary.trim() : "";
        if (!activityId || !summary) return;
        const activity: StreamingActivity = {
          id: activityId,
          kind: typeof data.kind === "string" ? data.kind.trim() : "thinking",
          status: eventType.replace("codex.activity.", ""),
          summary,
          detail: typeof data.detail === "string" ? data.detail.trim() : undefined,
          ts: typeof ev.ts === "string" ? ev.ts : new Date().toISOString(),
        };
        const activityKey = `${groupId}:${actorId}:${streamId || pendingEventId || "pending"}`;
        const existingActivityBatch = pendingCodexActivitiesRef.current.get(activityKey);
        if (existingActivityBatch) {
          existingActivityBatch.match = { pendingEventId, streamId };
          existingActivityBatch.activities.set(activityId, activity);
        } else {
          pendingCodexActivitiesRef.current.set(activityKey, {
            actorId,
            groupId,
            match: { pendingEventId, streamId },
            activities: new Map([[activityId, activity]]),
          });
        }
        schedulePendingCodexActivityFlush();
        return;
      }

      if (eventType === "codex.message.started" || eventType === "codex.message.delta" || eventType === "codex.message.completed") {
        if (!streamId) return;
        const delta = typeof data.delta === "string" ? data.delta : "";
        const explicitTextRaw = typeof data.text === "string" ? data.text : null;
        const explicitText = explicitTextRaw === "" && eventType === "codex.message.started" ? null : explicitTextRaw;
        const phase = typeof data.phase === "string" ? data.phase.trim().toLowerCase() : "";
        const transientStream = !!phase && phase !== "final_answer";
        const shouldBindToPendingPlaceholder = !!pendingEventId;
        if (pendingEventId && shouldBindToPendingPlaceholder) {
          promoteStreamingEventToStream(actorId, pendingEventId, streamId, groupId);
        }
        const messageKey = `${groupId}:${streamId}`;
        const existingMessageBatch = pendingCodexMessagesRef.current.get(messageKey);
        if (existingMessageBatch) {
          existingMessageBatch.pendingEventId = pendingEventId || existingMessageBatch.pendingEventId;
          existingMessageBatch.ts = typeof ev.ts === "string" ? ev.ts : existingMessageBatch.ts;
          existingMessageBatch.transientStream = transientStream;
          existingMessageBatch.phase = phase || existingMessageBatch.phase;
          if (explicitText != null) {
            existingMessageBatch.explicitText = explicitText;
            existingMessageBatch.deltaText = "";
          } else if (delta) {
            existingMessageBatch.deltaText += delta;
          }
          if (pendingEventId && shouldBindToPendingPlaceholder) {
            existingMessageBatch.shouldClearPlaceholder = true;
          }
          if (eventType === "codex.message.completed") {
            existingMessageBatch.completed = true;
          }
        } else {
          pendingCodexMessagesRef.current.set(messageKey, {
            groupId,
            actorId,
            streamId,
            pendingEventId,
            ts: typeof ev.ts === "string" ? ev.ts : new Date().toISOString(),
            explicitText,
            deltaText: explicitText == null ? delta : "",
            completed: eventType === "codex.message.completed",
            shouldClearPlaceholder: !!pendingEventId && shouldBindToPendingPlaceholder,
            transientStream,
            phase,
          });
        }
        schedulePendingCodexMessageFlush(groupId);
      }
    } catch {
      /* ignore parse errors */
    }
  }

  async function hydrateCodexSnapshot(groupId: string) {
    const resp = await api.fetchCodexSnapshot(groupId, { noCache: true });
    if (!resp.ok || selectedGroupIdRef.current !== groupId) return;
    const events = Array.isArray(resp.result.events) ? resp.result.events : [];
    for (const event of events) {
      const eventType = String(event?.type || "").trim();
      if (
        eventType === "codex.activity.started" ||
        eventType === "codex.activity.updated" ||
        eventType === "codex.activity.completed" ||
        eventType === "codex.message.started" ||
        eventType === "codex.message.delta" ||
        eventType === "codex.message.completed"
      ) {
        continue;
      }
      handleCodexEvent(groupId, event);
    }
    flushPendingCodexActivities(groupId);
    flushPendingCodexMessages(groupId);
  }

  function connectCodexStream(groupId: string, options?: { replay?: boolean }) {
    if (codexReconnectTimerRef.current) {
      window.clearTimeout(codexReconnectTimerRef.current);
      codexReconnectTimerRef.current = null;
    }
    if (codexEventSourceRef.current) {
      codexEventSourceRef.current.close();
      codexEventSourceRef.current = null;
    }

    const replay = options?.replay !== false;
    const params = new URLSearchParams();
    if (!replay) params.set("replay", "0");
    const codexPath = `/api/v1/groups/${encodeURIComponent(groupId)}/codex/stream${params.toString() ? `?${params.toString()}` : ""}`;
    const codexEs = new EventSource(api.withAuthToken(codexPath));
    codexEs.onopen = () => {
      codexReconnectDelayRef.current = 1000;
    };
    codexEs.onerror = () => {
      codexEs.close();
      codexEventSourceRef.current = null;
      if (codexReconnectTimerRef.current) {
        window.clearTimeout(codexReconnectTimerRef.current);
      }
      const delay = codexReconnectDelayRef.current;
      codexReconnectTimerRef.current = window.setTimeout(() => {
        codexReconnectTimerRef.current = null;
        if (selectedGroupIdRef.current === groupId) {
          connectCodexStream(groupId, { replay: true });
        }
      }, delay);
      codexReconnectDelayRef.current = Math.min(delay * 2, 30000);
    };
    codexEs.addEventListener("codex", (e) => {
      const msg = e as MessageEvent;
      try {
        handleCodexEvent(groupId, JSON.parse(String(msg.data || "{}")) as CodexStreamEvent);
      } catch {
        /* ignore parse errors */
      }
    });
    codexEventSourceRef.current = codexEs;
  }

  function connectStream(groupId: string) {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (codexReconnectTimerRef.current) {
      window.clearTimeout(codexReconnectTimerRef.current);
      codexReconnectTimerRef.current = null;
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (codexEventSourceRef.current) {
      codexEventSourceRef.current.close();
      codexEventSourceRef.current = null;
    }

    setSSEStatus("connecting");
    const es = new EventSource(api.withAuthToken(`/api/v1/groups/${encodeURIComponent(groupId)}/ledger/stream`));

    const isReconnect = hasConnectedOnceRef.current;

    es.onopen = () => {
      reconnectDelayRef.current = 1000;
      setSSEStatus("connected");
      hasConnectedOnceRef.current = true;

      // New SSE connections start at EOF, so every reconnect needs a
      // lightweight catch-up to cover the disconnect window.
      if (isReconnect) {
        void resyncAfterReconnect(groupId);
      }
    };

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      setSSEStatus("disconnected");

      const delay = reconnectDelayRef.current;
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        if (selectedGroupIdRef.current === groupId) {
          connectStream(groupId);
        }
      }, delay);

      reconnectDelayRef.current = Math.min(delay * 2, 30000);
    };

    es.addEventListener("ledger", (e) => {
      const msg = e as MessageEvent;
      try {
        const ev = JSON.parse(String(msg.data || "{}"));

        // Context sync - debounced refresh
        if (isContextSyncEvent(ev)) {
          contextRefreshTimerRef.current = scheduleContextSummaryCatchup(groupId, {
            invalidateContextRead: api.invalidateContextRead,
            existingTimer: contextRefreshTimerRef.current,
            clearTimer: window.clearTimeout,
            setTimer: (cb, delayMs) => window.setTimeout(cb, delayMs),
            fetchContextSummary: (gid, opts) => {
              void fetchContext(gid, opts);
            },
          });
          return;
        }

        // Actor activity - lightweight idle_seconds update (no full refresh)
        if (isActorActivityEvent(ev)) {
          const actors = ev.data?.actors;
          if (Array.isArray(actors) && actors.length > 0) {
            updateActorActivity(actors);
            const hasBusyActor = actors.some((actor) => {
              const state = String(actor.effective_working_state || "").trim().toLowerCase();
              return !!actor.running && state !== "idle" && state !== "stopped";
            });
            if (hasBusyActor && selectedGroupIdRef.current === groupId) {
              updateGroupRuntimeState(groupId, {
                lifecycle_state: "active",
                runtime_running: true,
              });
            }
          }
          return;
        }

        if (isPresentationPublishEvent(ev)) {
          void refreshPresentation(groupId);

          const slotId = String(ev.data?.slot_id || "").trim();
          if (slotId) {
            markPresentationSlotAttention(groupId, slotId);
          }
          return;
        }

        if (isPresentationClearEvent(ev)) {
          void refreshPresentation(groupId);
          const clearedSlots = Array.isArray(ev.data?.cleared_slots)
            ? ev.data.cleared_slots
            : [];
          for (const slot of clearedSlots) {
            const slotId = String(slot || "").trim();
            if (slotId) {
              clearPresentationSlotAttention(groupId, slotId);
            }
          }
          return;
        }

        // Chat read status update
        if (isChatReadEvent(ev)) {
          const data = extractChatReadData(ev);
          if (data) {
            updateReadStatus(data.eventId, data.actorId, groupId);
          }
          if (getActorRefreshMode(ev) === "unread") {
            void refreshActors(groupId, { includeUnread: true });
          }
          return;
        }

        // Chat ack status update
        if (isChatAckEvent(ev)) {
          const data = extractChatAckData(ev);
          if (data) {
            updateAckStatus(data.eventId, data.actorId, groupId);
          }
          return;
        }

        const reconciled = mergeCanonicalAttachmentsWithOptimisticPreview(ev as Record<string, unknown>, groupId);
        const nextEvent = reconciled.event;

        // Initialize read/ack status for new messages
        initializeReadStatus(nextEvent, actorsRef.current);
        initializeAckStatus(nextEvent, actorsRef.current);
        initializeObligationStatus(nextEvent, actorsRef.current);

        appendEvent(nextEvent, groupId);
        if (isChatMessageEvent(nextEvent)) {
          const msgData = nextEvent.data && typeof nextEvent.data === "object"
            ? (nextEvent.data as { stream_id?: unknown })
            : null;
          const streamId = msgData && typeof msgData.stream_id === "string" ? msgData.stream_id.trim() : "";
          if (streamId) {
            removeStreamingEvent(streamId, groupId);
          }
        }

        // Reconcile outbox: when a user's chat.message arrives via SSE,
        // remove only the exact optimistic entry that produced this canonical event.
        if (isChatMessageEvent(nextEvent) && String(nextEvent.by || "") === "user" && hasRenderableChatMessageContent(nextEvent)) {
          const msgData = nextEvent.data && typeof nextEvent.data === "object" ? (nextEvent.data as { client_id?: unknown }) : null;
          const clientId = msgData && typeof msgData.client_id === "string" ? msgData.client_id.trim() : "";
          if (clientId) {
            useChatOutboxStore.getState().remove(groupId, clientId);
            releaseTransferredPreviewUrls(reconciled.transferredPreviewUrls);
          }
        }

        // Reply to an earlier message updates its obligation status in-place.
        if (isChatMessageEvent(nextEvent)) {
          const msgData = nextEvent.data && typeof nextEvent.data === "object" ? (nextEvent.data as { reply_to?: unknown }) : null;
          const replyTo = msgData && typeof msgData.reply_to === "string" ? String(msgData.reply_to || "").trim() : "";
          const replyBy = String(nextEvent.by || "").trim();
          if (replyTo && replyBy) {
            updateReplyStatus(replyTo, replyBy, groupId);
          }
        }

        if (isChatMessageEvent(nextEvent) && String(nextEvent.by || "").trim() !== "user") {
          const msgData = nextEvent.data && typeof nextEvent.data === "object" ? (nextEvent.data as ChatMessageData) : null;
          const presentationRefs = getPresentationMessageRefs(msgData?.refs);
          const needsAttention =
            String(msgData?.priority || "normal").trim() === "attention" ||
            !!msgData?.reply_required;
          for (const ref of presentationRefs) {
            if (needsAttention || getPresentationRefStatus(ref, msgData, ev) === "needs_user") {
              const slotId = String(ref.slot_id || "").trim();
              if (slotId) {
                markPresentationSlotAttention(groupId, slotId);
              }
            }
          }
        }

        if (isChatMessageEvent(nextEvent)) {
          const recipients = getRecipientActorIdsForEvent(nextEvent, actorsRef.current);
          if (recipients.length > 0) {
            incrementActorUnread(recipients);
          }
        }

        // Update unread count
        if (shouldIncrementUnread(nextEvent, activeTabRef.current === "chat", chatAtBottomRef.current)) {
          incrementChatUnread(groupId);
        }

        const notifyTargetActorId = getNotifyTargetActorId(nextEvent);
        const actorRefreshMode = getActorRefreshMode(nextEvent);
        if (notifyTargetActorId) {
          // Fast local bump for responsiveness. system.notify is still an
          // authoritative unread-resync point, so the daemon unread projection
          // remains the final truth after this speculative increment.
          incrementActorUnread([notifyTargetActorId]);
        }
        if (actorRefreshMode === "unread") {
          void refreshActors(groupId, { includeUnread: true });
        } else if (actorRefreshMode === "readonly") {
          void refreshActors(groupId, { includeUnread: false });
        }
      } catch {
        /* ignore parse errors */
      }
    });
    eventSourceRef.current = es;

    void hydrateCodexSnapshot(groupId)
      .catch(() => {
        /* ignore snapshot hydration failures */
      })
      .finally(() => {
        if (selectedGroupIdRef.current === groupId) {
          connectCodexStream(groupId, { replay: false });
        }
      });
  }

  function cleanup() {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (codexReconnectTimerRef.current) {
      window.clearTimeout(codexReconnectTimerRef.current);
      codexReconnectTimerRef.current = null;
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (codexEventSourceRef.current) {
      codexEventSourceRef.current.close();
      codexEventSourceRef.current = null;
    }
    if (pendingCodexMessageFlushRef.current != null) {
      window.cancelAnimationFrame(pendingCodexMessageFlushRef.current);
      pendingCodexMessageFlushRef.current = null;
    }
    if (pendingCodexActivityFlushRef.current != null) {
      window.cancelAnimationFrame(pendingCodexActivityFlushRef.current);
      pendingCodexActivityFlushRef.current = null;
    }
    pendingCodexMessagesRef.current.clear();
    pendingCodexActivitiesRef.current.clear();
    if (contextRefreshTimerRef.current) {
      window.clearTimeout(contextRefreshTimerRef.current);
      contextRefreshTimerRef.current = null;
    }
    reconnectDelayRef.current = 1000;
    codexReconnectDelayRef.current = 1000;
    hasConnectedOnceRef.current = false;
    setSSEStatus("disconnected");
  }

  return {
    connectStream,
    fetchContext,
    cleanup,
    contextRefreshTimerRef,
  };
}
