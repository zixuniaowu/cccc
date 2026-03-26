// Chat Outbox Store — manages optimistic pending messages before server confirmation.
// Pending-only by design: failed sends roll back to the composer instead of living
// as a second local-only message state in the chat timeline.

import { create } from "zustand";
import type { LedgerEvent } from "../types";

function revokeEventObjectUrls(event: LedgerEvent | undefined): void {
  const data = event?.data;
  const attachments =
    data && typeof data === "object" && Array.isArray((data as { attachments?: unknown[] }).attachments)
      ? ((data as { attachments?: unknown[] }).attachments as unknown[])
      : [];
  for (const item of attachments) {
    if (!item || typeof item !== "object") continue;
    const previewUrl = typeof (item as { local_preview_url?: unknown }).local_preview_url === "string"
      ? String((item as { local_preview_url?: string }).local_preview_url || "").trim()
      : "";
    if (previewUrl.startsWith("blob:")) {
      try {
        URL.revokeObjectURL(previewUrl);
      } catch {
        void 0;
      }
    }
  }
}

export interface OutboxEntry {
  localId: string;
  groupId: string;
  event: LedgerEvent; // The optimistic event shape for display while the request is in flight.
  createdAt: number;
}

interface ChatOutboxState {
  /** Outbox entries keyed by groupId. Each value is a stable array reference. */
  entriesByGroup: Record<string, OutboxEntry[]>;

  /** Add a pending message to the outbox. */
  enqueue: (groupId: string, localId: string, event: LedgerEvent) => void;

  /** Remove an entry by localId (on HTTP success or SSE reconciliation). */
  remove: (groupId: string, localId: string) => void;

  /** Clear all pending entries for a group and release local preview URLs. */
  clearGroup: (groupId: string) => void;

  /** Clear every pending entry and release all local preview URLs. */
  clearAll: () => void;
}

export const useChatOutboxStore = create<ChatOutboxState>((set) => ({
  entriesByGroup: {},

  enqueue: (groupId, localId, event) =>
    set((state) => {
      const prev = state.entriesByGroup[groupId] || [];
      const entry: OutboxEntry = {
        localId,
        groupId,
        event,
        createdAt: Date.now(),
      };
      return {
        entriesByGroup: {
          ...state.entriesByGroup,
          [groupId]: [...prev, entry],
        },
      };
    }),

  remove: (groupId, localId) =>
    set((state) => {
      const prev = state.entriesByGroup[groupId];
      if (!prev || prev.length === 0) return state;
      const removed = prev.find((e) => e.localId === localId);
      const next = prev.filter((e) => e.localId !== localId);
      if (next.length === prev.length) return state; // no change
      revokeEventObjectUrls(removed?.event);
      return {
        entriesByGroup: {
          ...state.entriesByGroup,
          [groupId]: next,
        },
      };
    }),

  clearGroup: (groupId) =>
    set((state) => {
      const prev = state.entriesByGroup[groupId];
      if (!prev || prev.length === 0) return state;
      for (const entry of prev) revokeEventObjectUrls(entry.event);
      const nextEntries = { ...state.entriesByGroup };
      delete nextEntries[groupId];
      return { entriesByGroup: nextEntries };
    }),

  clearAll: () =>
    set((state) => {
      const groups = Object.values(state.entriesByGroup);
      if (groups.length === 0) return state;
      for (const entries of groups) {
        for (const entry of entries) revokeEventObjectUrls(entry.event);
      }
      return { entriesByGroup: {} };
    }),
}));

/** Stable selector: returns the outbox entries array for a group (or empty array). */
const EMPTY: OutboxEntry[] = [];
export function selectOutboxEntries(state: ChatOutboxState, groupId: string): OutboxEntry[] {
  return state.entriesByGroup[groupId] || EMPTY;
}
