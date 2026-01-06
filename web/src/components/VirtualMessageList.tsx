import { memo, useRef, useEffect, useCallback, useMemo } from "react";
import type { MutableRefObject } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { LedgerEvent, Actor, PresenceAgent } from "../types";
import { MessageBubble } from "./MessageBubble";

export interface VirtualMessageListProps {
  messages: LedgerEvent[];
  actors: Actor[];
  presenceAgents: PresenceAgent[];
  isDark: boolean;
  groupId: string;
  scrollRef?: MutableRefObject<HTMLDivElement | null>;
  onReply: (ev: LedgerEvent) => void;
  onShowRecipients: (eventId: string) => void;
  showScrollButton: boolean;
  onScrollButtonClick: () => void;
  chatUnreadCount: number;
  onScrollChange?: (isAtBottom: boolean) => void;
  // History loading
  isLoadingHistory?: boolean;
  hasMoreHistory?: boolean;
  onLoadMore?: () => void;
}

export const VirtualMessageList = memo(function VirtualMessageList({
  messages,
  actors,
  presenceAgents,
  isDark,
  groupId,
  scrollRef,
  onReply,
  onShowRecipients,
  showScrollButton,
  onScrollButtonClick,
  chatUnreadCount,
  onScrollChange,
  isLoadingHistory = false,
  hasMoreHistory = true,
  onLoadMore,
}: VirtualMessageListProps) {
  const parentRef = useRef<HTMLDivElement | null>(null);

  const presenceById = useMemo(() => {
    const m = new Map<string, PresenceAgent>();
    for (const p of presenceAgents || []) m.set(String(p.id || ""), p);
    return m;
  }, [presenceAgents]);

  const prevMessageCountRef = useRef(messages.length);
  const isAtBottomRef = useRef(true);
  const didInitialScrollRef = useRef(false);
  const scrollTimeoutRef = useRef<number | null>(null);
  const lastScrollTopRef = useRef(0);

  // For history loading scroll position preservation
  const prevScrollHeightRef = useRef(0);
  const wasAtTopRef = useRef(false);

  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 120,
    overscan: 5,
  });

  const measureElement = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node) return;
      if (typeof queueMicrotask === "function") {
        queueMicrotask(() => virtualizer.measureElement(node));
      } else {
        Promise.resolve().then(() => virtualizer.measureElement(node));
      }
    },
    [virtualizer]
  );

  const checkIsAtBottom = useCallback(() => {
    const el = parentRef.current;
    if (!el) return true;
    const threshold = 100;
    return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = parentRef.current;
    if (!el || messages.length <= 0) return;
    requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    });
  }, [messages.length]);

  const cancelScheduledScroll = useCallback(() => {
    const id = scrollTimeoutRef.current;
    if (id == null) return;
    scrollTimeoutRef.current = null;
    window.clearTimeout(id);
  }, []);

  const scheduleScroll = useCallback(
    (fn: () => void) => {
      cancelScheduledScroll();
      // Use setTimeout(0) to avoid flushSync warning during React lifecycle.
      scrollTimeoutRef.current = window.setTimeout(() => {
        scrollTimeoutRef.current = null;
        fn();
      }, 0);
    },
    [cancelScheduledScroll]
  );

  const scheduleScrollToBottom = useCallback(
    (opts?: { requireAtBottom?: boolean }) => {
      scheduleScroll(() => {
        if (opts?.requireAtBottom && !isAtBottomRef.current) return;
        scrollToBottom();
      });
    },
    [scheduleScroll, scrollToBottom]
  );

  const handleScroll = useCallback(() => {
    const el = parentRef.current;
    if (!el) return;

    const prevTop = lastScrollTopRef.current;
    const curTop = el.scrollTop;
    lastScrollTopRef.current = curTop;
    // Only allow top-history loading when the user is scrolling up (or attempting to),
    // otherwise being near the top while scrolling down would repeatedly trigger loads.
    const isScrollingUpOrStationary = curTop <= prevTop;

    const atBottom = checkIsAtBottom();
    isAtBottomRef.current = atBottom;
    onScrollChange?.(atBottom);

    // Top detection for loading more history
    const atTop = curTop < 100;
    if (isScrollingUpOrStationary && atTop && hasMoreHistory && !isLoadingHistory && onLoadMore) {
      wasAtTopRef.current = true;
      prevScrollHeightRef.current = el.scrollHeight || 0;
      onLoadMore();
    }
  }, [checkIsAtBottom, hasMoreHistory, isLoadingHistory, onLoadMore, onScrollChange]);

  useEffect(() => {
    const prevCount = prevMessageCountRef.current;
    const newCount = messages.length;
    prevMessageCountRef.current = newCount;

    if (newCount > prevCount && isAtBottomRef.current) {
      scheduleScrollToBottom({ requireAtBottom: true });
    }
  }, [messages.length, scheduleScrollToBottom]);

  useEffect(() => {
    if (didInitialScrollRef.current) return;
    if (messages.length <= 0) return;
    didInitialScrollRef.current = true;
    scheduleScrollToBottom();
  }, [messages.length, scheduleScrollToBottom]);

  // When switching groups, default to showing the latest messages.
  // Important: do NOT depend on messages.length here, otherwise every new message would
  // look like a "group switch" and force-scroll to bottom even if the user scrolled up.
  useEffect(() => {
    prevMessageCountRef.current = 0;
    isAtBottomRef.current = true;
    didInitialScrollRef.current = false;
    lastScrollTopRef.current = 0;
    cancelScheduledScroll();
  }, [groupId, cancelScheduledScroll]);

  useEffect(() => cancelScheduledScroll, [cancelScheduledScroll]);

  // Restore scroll position after loading older messages
  useEffect(() => {
    if (!wasAtTopRef.current || isLoadingHistory) return;
    const el = parentRef.current;
    if (!el) return;

    const heightDiff = el.scrollHeight - prevScrollHeightRef.current;
    if (heightDiff > 0) {
      el.scrollTop = heightDiff;
    }
    wasAtTopRef.current = false;
  }, [isLoadingHistory, messages.length]);

  return (
    <div
      ref={(el) => {
        parentRef.current = el;
        if (scrollRef) scrollRef.current = el;
      }}
      className="flex-1 min-h-0 overflow-auto px-4 py-4 relative"
      onScroll={messages.length > 0 ? handleScroll : undefined}
      role="log"
      aria-label="Chat messages"
    >
      {messages.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-full text-center pb-20 opacity-50">
          <div className="text-4xl mb-4 grayscale">💬</div>
          <p className={`text-sm font-medium ${isDark ? "text-slate-400" : "text-gray-500"}`}>
            No messages yet
          </p>
          <p className={`text-xs mt-1 ${isDark ? "text-slate-600" : "text-gray-400"}`}>
            Start the conversation with your AI team.
          </p>
        </div>
      ) : (
        <>
          {/* Loading indicator for history */}
          {isLoadingHistory && (
            <div className="flex justify-center py-4">
              <div className="animate-spin w-5 h-5 border-2 border-current border-t-transparent rounded-full opacity-50" />
            </div>
          )}

          {/* No more history indicator */}
          {!hasMoreHistory && !isLoadingHistory && (
            <div className={`text-center py-4 text-sm ${isDark ? "text-slate-500" : "text-gray-400"}`}>
              No more messages
            </div>
          )}

          {/* Virtual list container */}
          <div
            style={{
              height: `${virtualizer.getTotalSize()}px`,
              width: "100%",
              position: "relative",
              contain: "strict",
            }}
          >
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const message = messages[virtualRow.index];
              return (
                <div
                  key={message.id || virtualRow.index}
                  data-index={virtualRow.index}
                  ref={measureElement}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                  className="pb-6"
                >
                  <MessageBubble
                    event={message}
                    actors={actors}
                    presenceAgent={presenceById.get(String(message.by || "")) || null}
                    isDark={isDark}
                    groupId={groupId}
                    onReply={() => onReply(message)}
                    onShowRecipients={() => {
                      if (message.id) {
                        onShowRecipients(String(message.id));
                      }
                    }}
                  />
                </div>
              );
            })}
          </div>

          {/* Scroll Button */}
          {showScrollButton && (
            <button
              className={`fixed bottom-24 right-6 p-3 rounded-full shadow-xl transition-all z-10 ${
                isDark
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
});
