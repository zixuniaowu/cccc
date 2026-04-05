import { memo, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from "react";
import type { MutableRefObject } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { LedgerEvent, Actor, AgentState, PresentationMessageRef } from "../types";
import { MessageBubble } from "./MessageBubble";
import { useActorDisplayNameMap } from "../hooks/useActorDisplayName";
import {
  getChatTailMutationSnapshot,
  getChatTailSnapshot,
  shouldAutoFollowOnTailAppend,
  shouldAutoFollowOnTailMutation,
} from "../utils/chatAutoFollow";
import { estimateMessageRowHeight } from "./messageBubble/estimate";
import type { ChatFollowMode } from "../stores/useUIStore";

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

function shouldCollapseMessageHeader(previousMessage: LedgerEvent | undefined, message: LedgerEvent | undefined): boolean {
  return (
    !!previousMessage &&
    !!message &&
    String(previousMessage.kind || "") === "chat.message" &&
    String(message.kind || "") === "chat.message" &&
    String(previousMessage.by || "").trim() !== "" &&
    String(previousMessage.by || "").trim() === String(message.by || "").trim()
  );
}

function getMessageRowGrouping(previousMessage: LedgerEvent | undefined, message: LedgerEvent | undefined): {
  collapseHeader: boolean;
  compactSpacing: boolean;
} {
  const collapseHeader = shouldCollapseMessageHeader(previousMessage, message);
  return {
    collapseHeader,
    compactSpacing: collapseHeader,
  };
}

export interface VirtualMessageListProps {
  messages: LedgerEvent[];
  actors: Actor[];
  agentStates: AgentState[];
  isDark: boolean;
  readOnly?: boolean;
  groupId: string;
  groupLabelById: Record<string, string>;
  viewKey?: string;
  initialScrollTargetId?: string;
  initialScrollAnchorId?: string;
  initialScrollAnchorOffsetPx?: number;
  highlightEventId?: string;
  scrollRef?: MutableRefObject<HTMLDivElement | null>;
  onReply: (ev: LedgerEvent) => void;
  onShowRecipients: (eventId: string) => void;
  onCopyLink?: (eventId: string) => void;
  onCopyContent?: (ev: LedgerEvent) => void;
  onRelay?: (ev: LedgerEvent) => void;
  onOpenSource?: (srcGroupId: string, srcEventId: string) => void;
  onOpenPresentationRef?: (ref: PresentationMessageRef, event: LedgerEvent) => void;
  showScrollButton: boolean;
  onScrollButtonClick: () => void;
  chatUnreadCount: number;
  onScrollChange?: (isAtBottom: boolean) => void;
  onScrollSnapshot?: (snap: { mode: ChatFollowMode; anchorId: string; offsetPx: number; updatedAt: number }, groupId?: string) => void;
  forceStickToBottomToken?: number;
  // History loading
  isLoadingHistory?: boolean;
  hasMoreHistory?: boolean;
  onLoadMore?: () => void;
}

type VirtualMessageListInnerProps = VirtualMessageListProps & {
  resetKey: string;
};

type VirtualMessageRowProps = {
  virtualRow: { key: React.Key; index: number; start: number };
  message: LedgerEvent;
  collapseHeader?: boolean;
  compactSpacing?: boolean;
  actorById: Map<string, Actor>;
  actors: Actor[];
  displayNameMap: Map<string, string>;
  agentState: AgentState | null;
  isDark: boolean;
  readOnly?: boolean;
  groupId: string;
  groupLabelById: Record<string, string>;
  highlightEventId?: string;
  onReply: (ev: LedgerEvent) => void;
  onShowRecipients: (eventId: string) => void;
  onCopyLink?: (eventId: string) => void;
  onCopyContent?: (ev: LedgerEvent) => void;
  onRelay?: (ev: LedgerEvent) => void;
  onOpenSource?: (srcGroupId: string, srcEventId: string) => void;
  onOpenPresentationRef?: (ref: PresentationMessageRef, event: LedgerEvent) => void;
  measureElement: (node: Element | null) => void;
  onRowLayoutChange: (node: HTMLDivElement | null) => void;
};

const VirtualMessageRow = memo(function VirtualMessageRow({
  virtualRow,
  message,
  collapseHeader,
  compactSpacing,
  actorById,
  actors,
  displayNameMap,
  agentState,
  isDark,
  readOnly,
  groupId,
  groupLabelById,
  highlightEventId,
  onReply,
  onShowRecipients,
  onCopyLink,
  onCopyContent,
  onRelay,
  onOpenSource,
  onOpenPresentationRef,
  measureElement,
  onRowLayoutChange,
}: VirtualMessageRowProps) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  const isStreaming = !!message?._streaming;
  const lastMeasuredHeightRef = useRef(0);
  const lastMeasureAtRef = useRef(0);
  const measureRafRef = useRef<number | null>(null);

  useEffect(() => {
    const el = rowRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;

    const MEASURE_INTERVAL_MS = 120;
    const HEIGHT_DELTA_PX = 16;
    const runMeasure = () => {
      if (measureRafRef.current != null) return;
      measureRafRef.current = window.requestAnimationFrame(() => {
        measureRafRef.current = null;
        lastMeasuredHeightRef.current = el.offsetHeight;
        lastMeasureAtRef.current = performance.now();
        measureElement(el);
        onRowLayoutChange(el);
      });
    };
    const observer = new ResizeObserver(() => {
      if (isStreaming) {
        const now = performance.now();
        const nextHeight = el.offsetHeight;
        const lastHeight = lastMeasuredHeightRef.current || nextHeight;
        const elapsed = now - lastMeasureAtRef.current;
        if (Math.abs(nextHeight - lastHeight) < HEIGHT_DELTA_PX && elapsed < MEASURE_INTERVAL_MS) {
          return;
        }
        runMeasure();
        return;
      }
      runMeasure();
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      if (measureRafRef.current != null) {
        window.cancelAnimationFrame(measureRafRef.current);
        measureRafRef.current = null;
      }
    };
  }, [isStreaming, measureElement, onRowLayoutChange]);

  return (
    <div
      data-index={virtualRow.index}
      data-message-row="true"
      data-message-id={message.id ? String(message.id) : ""}
      ref={(node) => {
        rowRef.current = node;
        if (node) {
          lastMeasuredHeightRef.current = node.offsetHeight;
          lastMeasureAtRef.current = performance.now();
        }
        measureElement(node);
      }}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        transform: `translateY(${virtualRow.start}px)`,
      }}
      className={compactSpacing ? "pb-3" : "pb-6"}
    >
      <MessageBubble
        event={message}
        actorById={actorById}
        actors={actors}
        displayNameMap={displayNameMap}
        agentState={agentState}
        isDark={isDark}
        readOnly={readOnly}
        groupId={groupId}
        groupLabelById={groupLabelById}
        isHighlighted={!!highlightEventId && String(message.id || "") === String(highlightEventId)}
        collapseHeader={collapseHeader}
        onReply={() => onReply(message)}
        onShowRecipients={() => {
          if (message.id) {
            onShowRecipients(String(message.id));
          }
        }}
        onCopyLink={onCopyLink}
        onCopyContent={onCopyContent}
        onRelay={onRelay}
        onOpenSource={onOpenSource}
        onOpenPresentationRef={onOpenPresentationRef}
      />
    </div>
  );
});

const VirtualMessageListInner = function VirtualMessageListInner({
  messages,
  actors,
  agentStates,
  isDark,
  readOnly,
  groupId,
  groupLabelById,
  viewKey: _viewKey,
  initialScrollTargetId,
  initialScrollAnchorId,
  initialScrollAnchorOffsetPx,
  highlightEventId,
  scrollRef,
  onReply,
  onShowRecipients,
  onCopyLink,
  onCopyContent,
  onRelay,
  onOpenSource,
  onOpenPresentationRef,
  showScrollButton,
  onScrollButtonClick,
  chatUnreadCount,
  onScrollChange,
  onScrollSnapshot,
  forceStickToBottomToken = 0,
  isLoadingHistory = false,
  hasMoreHistory = true,
  onLoadMore,
  resetKey,
}: VirtualMessageListInnerProps) {
  const parentRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const remeasureRafRef = useRef<number | null>(null);
  const displayOrderRef = useRef<Map<string, number>>(new Map());
  const nextDisplayOrderRef = useRef(0);
  const displayMessages = useMemo(() => {
    const hasStreaming = messages.some((message) => !!message?._streaming);
    if (!hasStreaming) {
      displayOrderRef.current = new Map();
      nextDisplayOrderRef.current = 0;
      return messages;
    }

    const nextOrderMap = new Map(displayOrderRef.current);
    let nextOrder = nextDisplayOrderRef.current;
    for (let index = 0; index < messages.length; index += 1) {
      const key = String(getStableMessageKey(messages[index], index));
      if (!nextOrderMap.has(key)) {
        nextOrderMap.set(key, nextOrder);
        nextOrder += 1;
      }
    }
    displayOrderRef.current = nextOrderMap;
    nextDisplayOrderRef.current = nextOrder;

    return messages.slice().sort((a, b) => {
      const ao = nextOrderMap.get(String(getStableMessageKey(a, 0))) ?? Number.MAX_SAFE_INTEGER;
      const bo = nextOrderMap.get(String(getStableMessageKey(b, 0))) ?? Number.MAX_SAFE_INTEGER;
      if (ao !== bo) return ao - bo;
      const ats = String(a?.ts || "");
      const bts = String(b?.ts || "");
      if (ats && bts && ats !== bts) return ats.localeCompare(bts);
      return 0;
    });
  }, [messages]);
  const shouldVirtualize = shouldUseVirtualizedMessageList(displayMessages.length);

  const agentStateById = useMemo(() => {
    const m = new Map<string, AgentState>();
    for (const p of agentStates || []) m.set(String(p.id || ""), p);
    return m;
  }, [agentStates]);

  const actorById = useMemo(() => {
    const map = new Map<string, Actor>();
    for (const actor of actors || []) {
      const actorId = String(actor.id || "").trim();
      if (actorId) map.set(actorId, actor);

      const actorTitle = String(actor.title || "").trim();
      if (actorTitle && !map.has(actorTitle)) map.set(actorTitle, actor);

      const actorIdLower = actorId.toLowerCase();
      if (actorIdLower && !map.has(actorIdLower)) map.set(actorIdLower, actor);

      const actorTitleLower = actorTitle.toLowerCase();
      if (actorTitleLower && !map.has(actorTitleLower)) map.set(actorTitleLower, actor);
    }
    return map;
  }, [actors]);

  // Create display name map once at the list level (not per-message)
  const displayNameMap = useActorDisplayNameMap(actors);

  // Stable ref for messages — used by getEstimatedSize to avoid rebuilding
  // the callback (and thus the virtualizer) on every messages change.
  const messagesRef = useRef(displayMessages);
  messagesRef.current = displayMessages;

  const isAtBottomRef = useRef(true);
  const followModeRef = useRef<ChatFollowMode>("follow");
  const prevTailSnapshotRef = useRef(
    getChatTailSnapshot(
      displayMessages.length > 0 ? getStableMessageKey(displayMessages[displayMessages.length - 1], displayMessages.length - 1) : null,
      displayMessages.length,
    )
  );
  const prevTailMutationSnapshotRef = useRef(
    getChatTailMutationSnapshot(
      displayMessages.length > 0 ? getStableMessageKey(displayMessages[displayMessages.length - 1], displayMessages.length - 1) : null,
      "",
    )
  );
  const didInitialScrollRef = useRef(false);
  const scrollRafRef = useRef<number | null>(null);
  const scrollTokenRef = useRef(0);
  const scrollRafScheduledRef = useRef(false);
  const snapshotFlushTimerRef = useRef<number | null>(null);
  // For history loading scroll position preservation (prepend older messages)
  const topLoadArmedRef = useRef(true);
  const pendingRestoreRef = useRef(false);
  const pendingRestoreSeqRef = useRef<number | null>(null);
  const lastScrollEventAtRef = useRef(0);
  const scrollEventSeqRef = useRef(0);
  const anchorMessageIdRef = useRef<string>("");
  const anchorOffsetRef = useRef(0);
  const lastScrollTopRef = useRef(0);
  // 标记容器正在处理 resize（如 footer 回复栏出现/消失），
  // 防止 handleScroll 将浏览器裁剪 scrollTop 误判为用户上滑
  const isContainerResizingRef = useRef(false);
  const forceStickToBottomUntilRef = useRef(0);

  // Track previous resetKey for scroll snapshot before group switch
  const prevResetKeyRef = useRef<string | undefined>(undefined);
  // Store latest scroll snapshot for saving on group switch
  const latestSnapshotRef = useRef<{ mode: ChatFollowMode; anchorId: string; offsetPx: number; updatedAt: number } | null>(null);

  const getEstimatedSize = useCallback(
    (index: number): number => {
      const message = messagesRef.current[index];
      const previousMessage = index > 0 ? messagesRef.current[index - 1] : undefined;
      const grouping = getMessageRowGrouping(previousMessage, message);
      return estimateMessageRowHeight(message, { collapseHeader: grouping.collapseHeader });
    },
    [] // Stable ref — reads from messagesRef.current, no dep on messages array
  );

  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: displayMessages.length,
    getScrollElement: () => parentRef.current,
    getItemKey: (index) => getStableMessageKey(displayMessages[index], index),
    estimateSize: getEstimatedSize,
    overscan: 10,
    paddingStart: 72,
  });


  // Direct ref — synchronous measurement eliminates the jitter caused by
  // the old queueMicrotask wrapper (which deferred measurement by one frame,
  // making the first paint use the stale estimate height).
  const measureElement = virtualizer.measureElement;

  const getMessageRowById = useCallback((eventId: string): HTMLDivElement | null => {
    const container = parentRef.current;
    if (!container || !eventId) return null;
    return container.querySelector(`[data-message-row="true"][data-message-id="${CSS.escape(eventId)}"]`);
  }, []);

  const getAnchorSnapshot = useCallback((scrollTop: number) => {
    const container = parentRef.current;
    if (!container) return null;

    if (shouldVirtualize) {
      const vItems = virtualizer.getVirtualItems();
      if (vItems.length <= 0) return null;
      const anchorItem = vItems.find((v) => v.start + v.size > scrollTop + 1) || vItems[0];
      const msg = displayMessages[anchorItem.index];
      const anchorId = msg?.id ? String(msg.id) : "";
      if (!anchorId) return null;
      return {
        anchorId,
        offsetPx: Math.max(0, scrollTop - anchorItem.start),
      };
    }

    const rows = Array.from(container.querySelectorAll<HTMLDivElement>('[data-message-row="true"]'));
    if (rows.length <= 0) return null;
    const anchorRow =
      rows.find((row) => row.offsetTop + row.offsetHeight > scrollTop + 1) || rows[0];
    const anchorId = String(anchorRow.dataset.messageId || "").trim();
    if (!anchorId) return null;
    return {
      anchorId,
      offsetPx: Math.max(0, scrollTop - anchorRow.offsetTop),
    };
  }, [displayMessages, shouldVirtualize, virtualizer]);

  const setAtBottom = useCallback((next: boolean) => {
    isAtBottomRef.current = next;
  }, []);

  const setFollowMode = useCallback((next: ChatFollowMode) => {
    followModeRef.current = next;
  }, []);

  const scrollToMessageAnchor = useCallback((eventId: string, offsetPx = 0) => {
    const el = parentRef.current;
    if (!el || !eventId) return false;

    if (shouldVirtualize) {
      const idx = displayMessages.findIndex((m) => String(m?.id || "") === String(eventId));
      if (idx < 0) return false;
      const offsetInfo = virtualizer.getOffsetForIndex(idx, "start");
      if (offsetInfo) {
        virtualizer.scrollToOffset(offsetInfo[0] + Math.max(0, offsetPx), { align: "start", behavior: "auto" });
      } else {
        virtualizer.scrollToIndex(idx, { align: "start", behavior: "auto" });
      }
      return true;
    }

    const row = getMessageRowById(String(eventId));
    if (!row) return false;
    el.scrollTo({ top: row.offsetTop + Math.max(0, offsetPx), behavior: "auto" });
    return true;
  }, [displayMessages, getMessageRowById, shouldVirtualize, virtualizer]);

  const handleRowLayoutChange = useCallback((node: HTMLDivElement | null) => {
    if (shouldVirtualize && node) {
      measureElement(node);
    }
  }, [measureElement, shouldVirtualize]);

  const checkIsAtBottom = useCallback(() => {
    const el = parentRef.current;
    if (!el) return true;
    const threshold = 32;
    return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = parentRef.current;
    if (!el || displayMessages.length <= 0) return;
    window.requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    });
  }, [displayMessages.length]);

  const cancelScheduledScroll = useCallback(() => {
    const rid = scrollRafRef.current;
    if (rid != null) {
      scrollRafRef.current = null;
      window.cancelAnimationFrame(rid);
    }
  }, []);

  const shouldForceStickToBottom = useCallback(() => {
    return forceStickToBottomUntilRef.current > performance.now();
  }, []);

  const scheduleForceStickToBottom = useCallback(() => {
    forceStickToBottomUntilRef.current = performance.now() + 900;
    cancelScheduledScroll();
    scrollRafRef.current = window.requestAnimationFrame(() => {
      scrollRafRef.current = null;
      if (!shouldForceStickToBottom()) return;
      scrollToBottom();
    });
  }, [cancelScheduledScroll, scrollToBottom, shouldForceStickToBottom]);

  const scheduleScroll = useCallback(
    (fn: () => void) => {
      cancelScheduledScroll();
      scrollRafRef.current = window.requestAnimationFrame(() => {
        scrollRafRef.current = null;
        fn();
      });
    },
    [cancelScheduledScroll]
  );

  const scrollToIndexStable = useCallback(
    (idx: number) => {
      cancelScheduledScroll();
      const token = scrollTokenRef.current;
      const doScroll = () => {
        virtualizer.scrollToIndex(idx, { align: "center", behavior: "auto" });
      };
      doScroll();

      scrollRafRef.current = window.requestAnimationFrame(() => {
        scrollRafRef.current = null;
        if (scrollTokenRef.current !== token) return;
        doScroll();
      });
    },
    [cancelScheduledScroll, virtualizer]
  );

  const scrollToAnchorStable = useCallback(
    (idx: number, offsetPx: number) => {
      cancelScheduledScroll();
      const token = scrollTokenRef.current;
      const doScroll = () => {
        const offsetInfo = virtualizer.getOffsetForIndex(idx, "start");
        if (offsetInfo) {
          virtualizer.scrollToOffset(offsetInfo[0] + Math.max(0, offsetPx), { align: "start", behavior: "auto" });
        } else {
          virtualizer.scrollToIndex(idx, { align: "start", behavior: "auto" });
        }
      };
      doScroll();

      scrollRafRef.current = window.requestAnimationFrame(() => {
        scrollRafRef.current = null;
        if (scrollTokenRef.current !== token) return;
        doScroll();
      });
    },
    [cancelScheduledScroll, virtualizer]
  );

  const handleScroll = useCallback(() => {
    if (scrollRafScheduledRef.current) return;
    scrollRafScheduledRef.current = true;

    window.requestAnimationFrame(() => {
      scrollRafScheduledRef.current = false;

    const el = parentRef.current;
    if (!el) return;

    scrollEventSeqRef.current += 1;
    lastScrollEventAtRef.current = performance.now();
    const curTop = el.scrollTop;
    const previousTop = lastScrollTopRef.current;
    const userScrolledUp = curTop < previousTop - 4;
    if (userScrolledUp && !isContainerResizingRef.current) {
      setFollowMode("detached");
      forceStickToBottomUntilRef.current = 0;
      cancelScheduledScroll();
    }
    lastScrollTopRef.current = curTop;

    const atBottom = checkIsAtBottom();
    // Only notify parent when atBottom state actually changes (not on every scroll event)
    // to avoid triggering store updates and re-renders during inertia scrolling.
    const wasAtBottom = isAtBottomRef.current;
    setAtBottom(atBottom);
    if (atBottom !== wasAtBottom) {
      onScrollChange?.(atBottom);
    }

    // Capture a stable "anchor" (first visible message id + offset into that row)
    // so the parent can restore scroll position when switching groups.
    // Save to ref only during scroll; flush to store via debounce (not every frame)
    // to prevent zustand state churn that kills browser scroll inertia.
    const anchor = getAnchorSnapshot(curTop);
    if (anchor) {
      const snap = {
        mode: atBottom ? "follow" as const : followModeRef.current,
        anchorId: atBottom ? "" : anchor.anchorId,
        offsetPx: atBottom ? 0 : anchor.offsetPx,
        updatedAt: Date.now(),
      };
      latestSnapshotRef.current = snap;
      // Debounced flush to store — only after 300ms idle
      if (snapshotFlushTimerRef.current) window.clearTimeout(snapshotFlushTimerRef.current);
      snapshotFlushTimerRef.current = window.setTimeout(() => {
        snapshotFlushTimerRef.current = null;
        if (latestSnapshotRef.current) {
          onScrollSnapshot?.(latestSnapshotRef.current);
        }
      }, 300);
    }

    // Top detection for loading more history.
    //
    // Use a hysteresis "arm/disarm" gate instead of relying on scroll direction.
    // This prevents repeated loads when the scroll position jitters near the top
    // (e.g. due to browser scroll anchoring or dynamic row measurement).
    const topTriggerPx = 80;
    const topRearmPx = 240;
    if (curTop > topRearmPx) topLoadArmedRef.current = true;

    const atTop = curTop < topTriggerPx;
    if (atTop && topLoadArmedRef.current && hasMoreHistory && !isLoadingHistory && onLoadMore) {
      topLoadArmedRef.current = false;
      pendingRestoreRef.current = true;
      pendingRestoreSeqRef.current = scrollEventSeqRef.current;

      const pendingAnchor = getAnchorSnapshot(curTop);
      anchorMessageIdRef.current = pendingAnchor?.anchorId || "";
      anchorOffsetRef.current = pendingAnchor?.offsetPx || 0;

      onLoadMore();
    }
    });
  }, [cancelScheduledScroll, checkIsAtBottom, getAnchorSnapshot, hasMoreHistory, isLoadingHistory, onLoadMore, onScrollChange, onScrollSnapshot, setAtBottom, setFollowMode]);

  // When switching views (group or window-mode), reset internal scroll bookkeeping.
  //
  // Important: this must run before the auto-scroll effects below, otherwise it may
  // cancel their scheduled scrolls (breaking deep-link jump precision).
  useEffect(() => {
    const prevKey = prevResetKeyRef.current;

    // Only reset state when resetKey actually changes (not on re-renders with same key)
    if (prevKey === resetKey) {
      return;
    }

    // Before resetting, save the scroll snapshot from previous group (if any)
    if (prevKey && latestSnapshotRef.current) {
      // Extract groupId from prevKey (format: "groupId:live" or "groupId:window:eventId")
      const prevGroupId = prevKey.split(":")[0];
      if (prevGroupId) {
        // Save the last known scroll position for the previous group
        onScrollSnapshot?.(latestSnapshotRef.current, prevGroupId);
      }
    }

    prevResetKeyRef.current = resetKey;
    latestSnapshotRef.current = null;

    scrollTokenRef.current += 1;
    setAtBottom(true);
    setFollowMode(initialScrollAnchorId ? "detached" : "follow");
    didInitialScrollRef.current = false;
    topLoadArmedRef.current = true;
    cancelScheduledScroll();
    if (snapshotFlushTimerRef.current) {
      window.clearTimeout(snapshotFlushTimerRef.current);
      snapshotFlushTimerRef.current = null;
    }
    pendingRestoreRef.current = false;
    pendingRestoreSeqRef.current = null;
    anchorMessageIdRef.current = "";
    anchorOffsetRef.current = 0;
    lastScrollTopRef.current = 0;
    prevTailSnapshotRef.current = getChatTailSnapshot(
      displayMessages.length > 0 ? getStableMessageKey(displayMessages[displayMessages.length - 1], displayMessages.length - 1) : null,
      displayMessages.length,
    );
    prevTailMutationSnapshotRef.current = getChatTailMutationSnapshot(
      displayMessages.length > 0 ? getStableMessageKey(displayMessages[displayMessages.length - 1], displayMessages.length - 1) : null,
      "",
    );

    // Without key-based remount, the virtualizer keeps stale measurement
    // caches from the previous group. Force a full re-measure so item
    // sizes are recalculated for the new messages.
    if (shouldVirtualize) {
      virtualizer.measure();
    }
  }, [displayMessages, initialScrollAnchorId, resetKey, cancelScheduledScroll, onScrollSnapshot, setAtBottom, setFollowMode, shouldVirtualize, virtualizer]);

  const tailMutationSignature = useMemo(() => {
    const lastMessage = displayMessages[displayMessages.length - 1];
    if (!lastMessage) return "";
    const data = lastMessage.data && typeof lastMessage.data === "object"
      ? (lastMessage.data as { text?: unknown; attachments?: unknown[]; client_id?: unknown })
      : null;
    const attachmentCount = Array.isArray(data?.attachments) ? data.attachments.length : 0;
    const textLength = typeof data?.text === "string" ? data.text.length : 0;
    const clientId = typeof data?.client_id === "string" ? data.client_id.trim() : "";
    return [
      displayMessages.length,
      String(lastMessage.id || "").trim(),
      String(lastMessage.by || "").trim(),
      String(lastMessage.ts || "").trim(),
      clientId,
      textLength,
      attachmentCount,
    ].join("|");
  }, [displayMessages]);

  useEffect(() => {
    if (didInitialScrollRef.current) return;
    if (displayMessages.length <= 0) return;
    didInitialScrollRef.current = true;
    scheduleScroll(() => {
      if (initialScrollTargetId) {
        setAtBottom(false);
        setFollowMode("detached");
        if (shouldVirtualize) {
          const idx = displayMessages.findIndex((m) => String(m?.id || "") === String(initialScrollTargetId));
          if (idx >= 0) {
            scrollToIndexStable(idx);
            return;
          }
        } else if (scrollToMessageAnchor(String(initialScrollTargetId), 0)) {
          return;
        }
      }
      if (initialScrollAnchorId) {
        if (shouldVirtualize) {
          const idx = displayMessages.findIndex((m) => String(m?.id || "") === String(initialScrollAnchorId));
          if (idx >= 0) {
            setAtBottom(false);
            setFollowMode("detached");
            scrollToAnchorStable(idx, Number(initialScrollAnchorOffsetPx || 0));
            return;
          }
        } else if (scrollToMessageAnchor(String(initialScrollAnchorId), Number(initialScrollAnchorOffsetPx || 0))) {
          setAtBottom(false);
          setFollowMode("detached");
          return;
        }
        onScrollSnapshot?.({
          mode: "follow",
          anchorId: "",
          offsetPx: 0,
          updatedAt: Date.now(),
        });
      }
      setAtBottom(true);
      setFollowMode("follow");
      scheduleForceStickToBottom();
    });
  }, [
    displayMessages,
    initialScrollAnchorId,
    initialScrollAnchorOffsetPx,
    initialScrollTargetId,
    scheduleForceStickToBottom,
    scheduleScroll,
    onScrollSnapshot,
    scrollToAnchorStable,
    scrollToBottom,
    scrollToIndexStable,
    scrollToMessageAnchor,
    setAtBottom,
    setFollowMode,
    shouldVirtualize,
  ]);

  useEffect(() => {
    if (!forceStickToBottomToken) return;
    setAtBottom(true);
    setFollowMode("follow");
    scheduleForceStickToBottom();
  }, [forceStickToBottomToken, scheduleForceStickToBottom, setAtBottom, setFollowMode]);

  useEffect(() => {
    const nextTailSnapshot = getChatTailSnapshot(
      displayMessages.length > 0 ? getStableMessageKey(displayMessages[displayMessages.length - 1], displayMessages.length - 1) : null,
      displayMessages.length,
    );
    const nextSnapshot = getChatTailMutationSnapshot(
      displayMessages.length > 0 ? getStableMessageKey(displayMessages[displayMessages.length - 1], displayMessages.length - 1) : null,
      tailMutationSignature,
    );
    const prevTailSnapshot = prevTailSnapshotRef.current;
    const prevSnapshot = prevTailMutationSnapshotRef.current;
    prevTailSnapshotRef.current = nextTailSnapshot;
    prevTailMutationSnapshotRef.current = nextSnapshot;
    if (!didInitialScrollRef.current) return;
    if (isLoadingHistory || pendingRestoreRef.current) return;
    if (followModeRef.current !== "follow" && !shouldForceStickToBottom()) return;
    if (
      !getAutoFollowTrigger({
        previousTailSnapshot: prevTailSnapshot,
        nextTailSnapshot,
        previousTailMutationSnapshot: prevSnapshot,
        nextTailMutationSnapshot: nextSnapshot,
      })
    ) {
      return;
    }

    scheduleScroll(() => {
      if ((followModeRef.current !== "follow" && !shouldForceStickToBottom()) || pendingRestoreRef.current) return;
      scrollToBottom();
    });
  }, [displayMessages, isLoadingHistory, scheduleScroll, scrollToBottom, shouldForceStickToBottom, tailMutationSignature]);

  useEffect(() => cancelScheduledScroll, [cancelScheduledScroll]);

  useEffect(() => {
    const scrollEl = parentRef.current;
    const observedEl = contentRef.current;
    if (!scrollEl || !observedEl || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      // 这里监听消息内容层，而不是滚动容器本身。
      // 图片加载、流式正文补全、附件列表展开都会改变内容层高度，
      // 但不会改变滚动容器自身高度；如果只观察容器就会漏掉追底。
      isContainerResizingRef.current = true;
      lastScrollTopRef.current = scrollEl.scrollTop;

      if (followModeRef.current === "follow" || shouldForceStickToBottom()) {
        scheduleScroll(() => {
          if (followModeRef.current !== "follow" && !shouldForceStickToBottom()) return;
          scrollToBottom();
        });
      }

      window.requestAnimationFrame(() => {
        lastScrollTopRef.current = scrollEl.scrollTop;
        window.requestAnimationFrame(() => {
          isContainerResizingRef.current = false;
          lastScrollTopRef.current = scrollEl.scrollTop;
        });
      });
    });
    observer.observe(observedEl);
    return () => observer.disconnect();
  }, [scheduleScroll, scrollToBottom, shouldForceStickToBottom, shouldVirtualize, displayMessages.length, tailMutationSignature]);

  useEffect(() => {
    return () => {
      if (remeasureRafRef.current != null) {
        window.cancelAnimationFrame(remeasureRafRef.current);
        remeasureRafRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    return () => {
      // Cancel pending debounced flush
      if (snapshotFlushTimerRef.current) {
        window.clearTimeout(snapshotFlushTimerRef.current);
        snapshotFlushTimerRef.current = null;
      }
      // Immediate flush on unmount
      if (latestSnapshotRef.current) {
        const currentGroupId = resetKey.split(":")[0];
        if (currentGroupId) {
          onScrollSnapshot?.(latestSnapshotRef.current, currentGroupId);
        }
      }
      if (scrollRef) {
        scrollRef.current = null;
      }
    };
  }, [onScrollSnapshot, resetKey, scrollRef]);

  // Restore scroll position after loading older messages (prepend).
  //
  // The old implementation used a scrollEventSeq check to detect "user scrolled
  // away during loading, so skip restore". But layout-triggered scroll events
  // from the virtualizer (Loading indicator appearing, row re-measurement, etc.)
  // also increment the seq, causing the restore to be falsely skipped — leaving
  // scrollTop near 0 and immediately re-triggering onLoadMore in a loop.
  //
  // The arm/disarm gate (topLoadArmedRef) already prevents duplicate loads, so
  // the seq check is unnecessary. Removed.
  const restorePendingAnchor = useCallback(() => {
    if (isLoadingHistory) return;
    if (!pendingRestoreRef.current) return;
    const el = parentRef.current;
    if (!el) return;

    const idleMs = performance.now() - lastScrollEventAtRef.current;
    if (idleMs < 120) {
      const remaining = Math.max(20, 120 - idleMs);
      window.setTimeout(() => restorePendingAnchor(), remaining);
      return;
    }

    pendingRestoreRef.current = false;
    pendingRestoreSeqRef.current = null;

    const anchorId = anchorMessageIdRef.current;
    const offsetInRow = anchorOffsetRef.current;
    anchorMessageIdRef.current = "";
    anchorOffsetRef.current = 0;

    if (anchorId) {
      const restored = scrollToMessageAnchor(anchorId, offsetInRow);
      if (restored) {
        // Keep topLoad disarmed so the restored position (which may still be
        // near the top) doesn't immediately re-trigger another load.
        topLoadArmedRef.current = false;
      }
    }
  }, [isLoadingHistory, scrollToMessageAnchor]);

  useLayoutEffect(() => {
    if (isLoadingHistory) return;
    if (!pendingRestoreRef.current) return;
    restorePendingAnchor();
  }, [isLoadingHistory, restorePendingAnchor]);

  return (
    <div
      ref={(el) => {
        parentRef.current = el;
        if (scrollRef) scrollRef.current = el;
      }}
      className="flex-1 min-h-0 overflow-auto px-4 py-4 relative"
      style={{ overflowAnchor: "none" }}
      onScroll={displayMessages.length > 0 ? handleScroll : undefined}
      role="log"
      aria-label="Chat messages"
    >
      {displayMessages.length === 0 ? (
        (isLoadingHistory || hasMoreHistory) ? (
          <div className="flex flex-col items-center justify-center h-full text-center pb-20">
            <div
              className={`flex items-center gap-2 px-3 py-1.5 rounded-full shadow-md ${isDark ? "bg-slate-800 text-slate-300" : "bg-white text-gray-600"
                }`}
            >
              <div className="animate-spin w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
              <span className="text-xs">Loading...</span>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center pb-20 opacity-50">
            <div className="text-4xl mb-4 grayscale">💬</div>
            <p className={`text-sm font-medium ${isDark ? "text-slate-400" : "text-gray-500"}`}>
              No messages yet
            </p>
            <p className={`text-xs mt-1 ${isDark ? "text-slate-600" : "text-gray-400"}`}>
              Start the conversation with your AI team.
            </p>
          </div>
        )
      ) : (
        <>
          {(isLoadingHistory || (!hasMoreHistory && !isLoadingHistory)) && (
            <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex justify-center py-3">
              {isLoadingHistory ? (
                <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full shadow-md ${isDark ? "bg-slate-800 text-slate-300" : "bg-white text-gray-600"
                  }`}>
                  <div className="animate-spin w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
                  <span className="text-xs">Loading...</span>
                </div>
              ) : (
                <div className={`px-3 py-1.5 rounded-full text-sm shadow-sm ${isDark ? "bg-slate-900/85 text-slate-400" : "bg-white/90 text-gray-400"
                  }`}>
                  No more messages
                </div>
              )}
            </div>
          )}

          {shouldVirtualize ? (
            <div
              ref={contentRef}
              style={{
                height: `${virtualizer.getTotalSize()}px`,
                width: "100%",
                position: "relative",
                contain: "layout paint",
              }}
            >
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const message = displayMessages[virtualRow.index];
                const previousMessage = virtualRow.index > 0 ? displayMessages[virtualRow.index - 1] : undefined;
                const grouping = getMessageRowGrouping(previousMessage, message);
                return (
                  <VirtualMessageRow
                    key={virtualRow.key}
                    virtualRow={virtualRow}
                    message={message}
                    collapseHeader={grouping.collapseHeader}
                    compactSpacing={grouping.compactSpacing}
                    actorById={actorById}
                    actors={actors}
                    displayNameMap={displayNameMap}
                    agentState={agentStateById.get(String(message.by || "")) || null}
                    isDark={isDark}
                    readOnly={readOnly}
                    groupId={groupId}
                    groupLabelById={groupLabelById}
                    highlightEventId={highlightEventId}
                    onReply={onReply}
                    onShowRecipients={onShowRecipients}
                    onCopyLink={onCopyLink}
                    onCopyContent={onCopyContent}
                    onRelay={onRelay}
                    onOpenSource={onOpenSource}
                    onOpenPresentationRef={onOpenPresentationRef}
                    measureElement={measureElement}
                    onRowLayoutChange={handleRowLayoutChange}
                  />
                );
              })}
            </div>
          ) : (
            <div ref={contentRef} className="w-full">
              {displayMessages.map((message, index) => {
                const previousMessage = index > 0 ? displayMessages[index - 1] : undefined;
                const grouping = getMessageRowGrouping(previousMessage, message);
                return (
                  <div
                    key={String(getStableMessageKey(message, index))}
                    data-message-row="true"
                    data-message-id={message.id ? String(message.id) : ""}
                    className={grouping.compactSpacing ? "pb-3" : "pb-6"}
                  >
                    <MessageBubble
                      event={message}
                      actorById={actorById}
                      actors={actors}
                      displayNameMap={displayNameMap}
                      agentState={agentStateById.get(String(message.by || "")) || null}
                      isDark={isDark}
                      readOnly={readOnly}
                      groupId={groupId}
                      groupLabelById={groupLabelById}
                      isHighlighted={!!highlightEventId && String(message.id || "") === String(highlightEventId)}
                      collapseHeader={grouping.collapseHeader}
                      onReply={() => onReply(message)}
                      onShowRecipients={() => {
                        if (message.id) {
                          onShowRecipients(String(message.id));
                        }
                      }}
                      onCopyLink={onCopyLink}
                      onCopyContent={onCopyContent}
                      onRelay={onRelay}
                      onOpenSource={onOpenSource}
                      onOpenPresentationRef={onOpenPresentationRef}
                    />
                  </div>
                );
              })}
            </div>
          )}

          {/* Scroll Button */}
          {!readOnly && showScrollButton && (
            <button
              className={`fixed bottom-36 right-6 p-3 rounded-full shadow-xl transition-all z-10 ${isDark
                ? "bg-slate-800 text-white hover:bg-slate-700 border border-slate-700"
                : "bg-white text-gray-600 hover:bg-gray-50 border border-gray-100"
                }`}
              onClick={() => {
                scrollToBottom();
                onScrollButtonClick();
              }}
              aria-label="Scroll to bottom"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
              </svg>
              {chatUnreadCount > 0 && (
                <span className="absolute -top-1 -right-1 flex h-4 w-4">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-60"></span>
                  <span className="relative inline-flex rounded-full h-4 w-4 bg-indigo-500 text-[9px] text-white items-center justify-center font-bold">
                    {chatUnreadCount > 9 ? "!" : chatUnreadCount}
                  </span>
                </span>
              )}
            </button>
          )}
        </>
      )}
    </div>
  );
};

export const VirtualMessageList = memo(function VirtualMessageList(props: VirtualMessageListProps) {
  const resetKey = props.viewKey ?? props.groupId;
  // No `key` prop — avoid full unmount/remount on group switch which causes
  // a visible flash (virtualizer needs 1-2 frames to measure after mount).
  // Internal state is reset via the resetKey useEffect instead.
  return <VirtualMessageListInner {...props} resetKey={resetKey} />;
});
