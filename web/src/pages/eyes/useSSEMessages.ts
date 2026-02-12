import { useEffect, useRef, useCallback, useState } from "react";
import { withAuthToken } from "../../services/api";
import type { LedgerEvent } from "../../types";

interface UseSSEMessagesOptions {
  groupId: string | null;
  /** Called when an agent message arrives */
  onAgentMessage: (text: string, eventId: string) => void;
  /** Called when a user's own message echoes back (optional) */
  onUserMessage?: (text: string, eventId: string) => void;
}

/**
 * SSE-based message listener — replaces the 3.5s polling loop.
 * Connects to GET /api/v1/groups/{gid}/ledger/stream and listens for
 * `chat.message` events.  Includes exponential-backoff reconnection.
 */
export function useSSEMessages({
  groupId,
  onAgentMessage,
  onUserMessage,
}: UseSSEMessagesOptions) {
  const [connected, setConnected] = useState(false);
  const seenIdsRef = useRef<Set<string>>(new Set());
  const pageStartTsRef = useRef<number>(Date.now());
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectDelayRef = useRef<number>(1000);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const groupIdRef = useRef(groupId);
  const onAgentRef = useRef(onAgentMessage);
  const onUserRef = useRef(onUserMessage);

  useEffect(() => {
    groupIdRef.current = groupId;
  }, [groupId]);
  useEffect(() => {
    onAgentRef.current = onAgentMessage;
  }, [onAgentMessage]);
  useEffect(() => {
    onUserRef.current = onUserMessage;
  }, [onUserMessage]);

  const connect = useCallback((gid: string) => {
    // Cleanup previous connection
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = undefined;
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    const url = withAuthToken(
      `/api/v1/groups/${encodeURIComponent(gid)}/ledger/stream`
    );
    const es = new EventSource(url);

    es.onopen = () => {
      setConnected(true);
      reconnectDelayRef.current = 1000;
    };

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      setConnected(false);

      const delay = reconnectDelayRef.current;
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = undefined;
        if (groupIdRef.current === gid) {
          connect(gid);
        }
      }, delay);
      reconnectDelayRef.current = Math.min(delay * 2, 30000);
    };

    es.addEventListener("ledger", (e) => {
      const msg = e as MessageEvent;
      try {
        const ev = JSON.parse(String(msg.data || "{}")) as LedgerEvent;
        if (ev.kind !== "chat.message" && ev.kind !== "chat") return;

        const id = String(ev.id || "");
        if (!id || seenIdsRef.current.has(id)) return;

        // Skip historical events before page load
        const evTs = new Date(ev.ts || 0).getTime();
        if (evTs && evTs < pageStartTsRef.current - 500) {
          seenIdsRef.current.add(id);
          return;
        }
        seenIdsRef.current.add(id);

        const text = ((ev.data as any)?.text || "").trim();
        if (!text) return;

        if (ev.by === "user") {
          onUserRef.current?.(text, id);
        } else {
          onAgentRef.current(text, id);
        }
      } catch {
        // ignore parse errors
      }
    });

    eventSourceRef.current = es;
  }, []);

  useEffect(() => {
    if (groupId) {
      connect(groupId);
    }
    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setConnected(false);
    };
  }, [groupId, connect]);

  return { connected };
}
