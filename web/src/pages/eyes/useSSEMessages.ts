import { useEffect, useRef, useCallback, useState } from "react";
import * as api from "../../services/api";
import { withAuthToken } from "../../services/api";
import type { LedgerEvent } from "../../types";

/** After this many consecutive SSE failures, fall back to polling */
const SSE_FAILURE_THRESHOLD = 5;
const POLL_INTERVAL = 3500;

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
 * Falls back to polling after 5 consecutive SSE failures.
 */
export function useSSEMessages({
  groupId,
  onAgentMessage,
  onUserMessage,
}: UseSSEMessagesOptions) {
  const [connected, setConnected] = useState(false);
  const [mode, setMode] = useState<"sse" | "polling">("sse");
  const [reconnecting, setReconnecting] = useState(false);
  const seenIdsRef = useRef<Set<string>>(new Set());
  const pageStartTsRef = useRef<number>(Date.now());
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectDelayRef = useRef<number>(1000);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const failCountRef = useRef(0);
  const pollTimerRef = useRef<ReturnType<typeof setInterval>>();
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
      setReconnecting(false);
      reconnectDelayRef.current = 1000;
      failCountRef.current = 0;
      setMode("sse");
    };

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      setConnected(false);

      failCountRef.current += 1;

      // After N consecutive failures, fall back to polling
      if (failCountRef.current >= SSE_FAILURE_THRESHOLD) {
        console.warn("[SSE] %d failures, falling back to polling", failCountRef.current);
        setReconnecting(false);
        setMode("polling");
        startPolling(gid);
        return;
      }

      setReconnecting(true);
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

  // ── Polling fallback ──
  const processEvent = useCallback((ev: LedgerEvent) => {
    if (ev.kind !== "chat.message" && ev.kind !== "chat") return;
    const id = String(ev.id || "");
    if (!id || seenIdsRef.current.has(id)) return;
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
  }, []);

  const startPolling = useCallback((gid: string) => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    const poll = async () => {
      try {
        const resp = await api.fetchLedgerTail(gid, 10);
        if (resp.ok && resp.result?.events) {
          for (const ev of resp.result.events) {
            processEvent(ev as LedgerEvent);
          }
        }
        setConnected(true);
      } catch {
        setConnected(false);
      }
    };
    void poll();
    pollTimerRef.current = setInterval(() => void poll(), POLL_INTERVAL);
  }, [processEvent]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = undefined;
    }
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
      stopPolling();
      setConnected(false);
    };
  }, [groupId, connect, stopPolling]);

  return { connected, mode, reconnecting };
}
