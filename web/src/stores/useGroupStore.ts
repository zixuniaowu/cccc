// Group state store (groups, actors, events, context, settings).
import { create } from "zustand";
import type {
  GroupMeta,
  GroupDoc,
  LedgerEvent,
  Actor,
  RuntimeInfo,
  GroupContext,
  GroupSettings,
} from "../types";
import * as api from "../services/api";

interface GroupState {
  // Data
  groups: GroupMeta[];
  groupOrder: string[]; // Group IDs in user-defined order
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  events: LedgerEvent[];
  chatWindow: {
    groupId: string;
    centerEventId: string;
    centerIndex: number;
    events: LedgerEvent[];
    hasMoreBefore: boolean;
    hasMoreAfter: boolean;
  } | null;
  actors: Actor[];
  groupContext: GroupContext | null;
  groupSettings: GroupSettings | null;
  runtimes: RuntimeInfo[];
  hasMoreHistory: boolean;
  isLoadingHistory: boolean;
  isChatWindowLoading: boolean;

  // Actions
  setGroups: (groups: GroupMeta[]) => void;
  setGroupOrder: (order: string[]) => void;
  reorderGroups: (fromIndex: number, toIndex: number) => void;
  getOrderedGroups: () => GroupMeta[];
  setSelectedGroupId: (id: string) => void;
  setGroupDoc: (doc: GroupDoc | null) => void;
  setEvents: (events: LedgerEvent[]) => void;
  appendEvent: (event: LedgerEvent) => void;
  prependEvents: (events: LedgerEvent[]) => void;
  setChatWindow: (w: GroupState["chatWindow"]) => void;
  setActors: (actors: Actor[]) => void;
  incrementActorUnread: (actorIds: string[]) => void;
  updateActorActivity: (updates: Array<{ id: string; idle_seconds: number; running: boolean }>) => void;
  setGroupContext: (ctx: GroupContext | null) => void;
  setGroupSettings: (settings: GroupSettings | null) => void;
  setRuntimes: (runtimes: RuntimeInfo[]) => void;
  updateReadStatus: (eventId: string, actorId: string) => void;
  updateAckStatus: (eventId: string, actorId: string) => void;
  updateReplyStatus: (eventId: string, actorId: string) => void;
  setHasMoreHistory: (v: boolean) => void;
  setIsLoadingHistory: (v: boolean) => void;
  setIsChatWindowLoading: (v: boolean) => void;

  // Async actions
  refreshGroups: () => Promise<void>;
  refreshActors: (groupId?: string) => Promise<void>;
  loadGroup: (groupId: string) => Promise<void>;
  warmGroup: (groupId: string) => Promise<void>;
  loadMoreHistory: () => Promise<void>;
  openChatWindow: (groupId: string, centerEventId: string) => Promise<void>;
  closeChatWindow: () => void;
}

const MAX_UI_EVENTS = 800;
const GROUP_VIEW_CACHE_TTL_MS = 60_000;

// localStorage key for group order
const GROUP_ORDER_KEY = "cccc-group-order";

function loadGroupOrder(): string[] {
  try {
    const stored = localStorage.getItem(GROUP_ORDER_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) return parsed;
    }
  } catch (e) {
    console.warn("Failed to read group order from localStorage:", e);
  }
  return [];
}

function saveGroupOrder(order: string[]): void {
  try {
    localStorage.setItem(GROUP_ORDER_KEY, JSON.stringify(order));
  } catch (e) {
    console.warn("Failed to persist group order to localStorage:", e);
  }
}

// Merge stored order with current groups: preserve order for existing groups, append new ones at end
function mergeGroupOrder(storedOrder: string[], groups: GroupMeta[]): string[] {
  const currentIds = new Set(groups.map((g) => String(g.group_id || "")));
  // Keep only IDs that still exist in the current group list
  const validOrder = storedOrder.filter((id) => currentIds.has(id));
  // Find new groups not in stored order
  const orderedSet = new Set(validOrder);
  const newIds = groups
    .map((g) => String(g.group_id || ""))
    .filter((id) => id && !orderedSet.has(id));
  return [...validOrder, ...newIds];
}

// In-flight guards
let refreshGroupsInFlight = false;
let refreshGroupsQueued = false;
const refreshActorsInFlight = new Set<string>();
const refreshActorsQueued = new Set<string>();
const warmGroupInFlight = new Set<string>();
let loadGroupToken = 0;

type GroupViewSnapshot = {
  groupDoc: GroupDoc | null;
  events: LedgerEvent[];
  actors: Actor[];
  groupContext: GroupContext | null;
  groupSettings: GroupSettings | null;
  hasMoreHistory: boolean;
  cachedAt: number;
};

const groupViewCache = new Map<string, GroupViewSnapshot>();

function cloneGroupDoc(doc: GroupDoc | null | undefined): GroupDoc | null {
  return doc ? { ...doc } : null;
}

function cloneEvents(events: LedgerEvent[] | undefined): LedgerEvent[] {
  return Array.isArray(events) ? [...events] : [];
}

function cloneActors(actors: Actor[] | undefined): Actor[] {
  return Array.isArray(actors) ? [...actors] : [];
}

function cloneGroupContext(ctx: GroupContext | null | undefined): GroupContext | null {
  return ctx ? { ...ctx } : null;
}

function cloneGroupSettings(settings: GroupSettings | null | undefined): GroupSettings | null {
  return settings ? { ...settings } : null;
}

function getCachedGroupView(groupId: string): GroupViewSnapshot | null {
  const gid = String(groupId || "").trim();
  if (!gid) return null;
  const cached = groupViewCache.get(gid);
  if (!cached) return null;
  if (Date.now() - cached.cachedAt > GROUP_VIEW_CACHE_TTL_MS) return null;
  return {
    groupDoc: cloneGroupDoc(cached.groupDoc),
    events: cloneEvents(cached.events),
    actors: cloneActors(cached.actors),
    groupContext: cloneGroupContext(cached.groupContext),
    groupSettings: cloneGroupSettings(cached.groupSettings),
    hasMoreHistory: !!cached.hasMoreHistory,
    cachedAt: cached.cachedAt,
  };
}

function saveGroupView(groupId: string, patch: Partial<Omit<GroupViewSnapshot, "cachedAt">>): void {
  const gid = String(groupId || "").trim();
  if (!gid) return;

  const prev = groupViewCache.get(gid);
  groupViewCache.set(gid, {
    groupDoc: patch.groupDoc !== undefined ? cloneGroupDoc(patch.groupDoc) : cloneGroupDoc(prev?.groupDoc),
    events: patch.events !== undefined ? cloneEvents(patch.events) : cloneEvents(prev?.events),
    actors: patch.actors !== undefined ? cloneActors(patch.actors) : cloneActors(prev?.actors),
    groupContext: patch.groupContext !== undefined ? cloneGroupContext(patch.groupContext) : cloneGroupContext(prev?.groupContext),
    groupSettings: patch.groupSettings !== undefined ? cloneGroupSettings(patch.groupSettings) : cloneGroupSettings(prev?.groupSettings),
    hasMoreHistory: patch.hasMoreHistory !== undefined ? !!patch.hasMoreHistory : !!prev?.hasMoreHistory,
    cachedAt: Date.now(),
  });
}

function saveCurrentViewSnapshot(groupId: string, state: {
  groups: GroupMeta[];
  groupDoc: GroupDoc | null;
  events: LedgerEvent[];
  actors: Actor[];
  groupContext: GroupContext | null;
  groupSettings: GroupSettings | null;
  hasMoreHistory: boolean;
}): void {
  const gid = String(groupId || "").trim();
  if (!gid) return;

  const shellDoc = buildShellGroupDoc(gid, state.groups, null);
  saveGroupView(gid, {
    groupDoc: state.groupDoc && String(state.groupDoc.group_id || "") === gid ? state.groupDoc : shellDoc,
    events: state.events,
    actors: state.actors,
    groupContext: state.groupContext,
    groupSettings: state.groupSettings,
    hasMoreHistory: state.hasMoreHistory,
  });
}

function buildShellGroupDoc(groupId: string, groups: GroupMeta[], cached: GroupViewSnapshot | null): GroupDoc | null {
  const gid = String(groupId || "").trim();
  if (!gid) return null;
  if (cached?.groupDoc && String(cached.groupDoc.group_id || "") === gid) {
    return cloneGroupDoc(cached.groupDoc);
  }

  const meta = groups.find((group) => String(group.group_id || "") === gid) || null;
  if (!meta) return null;
  return {
    group_id: gid,
    title: meta.title,
    topic: meta.topic,
    state: meta.state,
  };
}

function buildPrimedGroupState(groupId: string, groups: GroupMeta[]) {
  const gid = String(groupId || "").trim();
  if (!gid) {
    return {
      groupDoc: null,
      events: [],
      actors: [],
      groupContext: null,
      groupSettings: null,
      hasMoreHistory: false,
      chatWindow: null,
    };
  }

  const cached = getCachedGroupView(gid);
  return {
    groupDoc: buildShellGroupDoc(gid, groups, cached),
    events: cached?.events || [],
    actors: cached?.actors || [],
    groupContext: cached?.groupContext || null,
    groupSettings: cached?.groupSettings || null,
    hasMoreHistory: cached?.hasMoreHistory ?? true,
    chatWindow: null,
  };
}

function filterUiEvents(events: LedgerEvent[] | undefined): LedgerEvent[] {
  return Array.isArray(events) ? events.filter((ev) => ev && ev.kind !== "context.sync") : [];
}

export const useGroupStore = create<GroupState>((set, get) => ({
  // Initial state
  groups: [],
  groupOrder: loadGroupOrder(),
  selectedGroupId: "",
  groupDoc: null,
  events: [],
  chatWindow: null,
  actors: [],
  groupContext: null,
  groupSettings: null,
  runtimes: [],
  hasMoreHistory: true,
  isLoadingHistory: false,
  isChatWindowLoading: false,

  // Sync actions
  setGroups: (groups) => {
    const storedOrder = get().groupOrder;
    const mergedOrder = mergeGroupOrder(storedOrder, groups);
    saveGroupOrder(mergedOrder);
    set({ groups, groupOrder: mergedOrder });
  },
  setGroupOrder: (order) => {
    saveGroupOrder(order);
    set({ groupOrder: order });
  },
  reorderGroups: (fromIndex, toIndex) => {
    const order = get().groupOrder.slice();
    const [moved] = order.splice(fromIndex, 1);
    order.splice(toIndex, 0, moved);
    saveGroupOrder(order);
    set({ groupOrder: order });
  },
  getOrderedGroups: () => {
    const { groups, groupOrder } = get();
    const groupMap = new Map(groups.map((g) => [String(g.group_id || ""), g]));
    const ordered: GroupMeta[] = [];
    for (const id of groupOrder) {
      const g = groupMap.get(id);
      if (g) ordered.push(g);
    }
    // Include any groups not in order (shouldn't happen normally, but be safe)
    for (const g of groups) {
      const id = String(g.group_id || "");
      if (!groupOrder.includes(id)) ordered.push(g);
    }
    return ordered;
  },
  setSelectedGroupId: (id) =>
    set((state) => {
      const gid = String(id || "").trim();
      const prevGid = String(state.selectedGroupId || "").trim();

      // 切组前先把当前视图快照落到缓存，回切时才能做到秒开。
      if (prevGid && prevGid !== gid) {
        saveCurrentViewSnapshot(prevGid, state);
      }

      return {
        selectedGroupId: gid,
        ...buildPrimedGroupState(gid, state.groups),
      };
    }),
  setGroupDoc: (doc) => set({ groupDoc: doc }),
  setEvents: (events) => set({ events }),
  setChatWindow: (w) => set({ chatWindow: w }),
  appendEvent: (event) =>
    set((state) => {
      const next = state.events.concat([event]);
      return {
        events: next.length > MAX_UI_EVENTS ? next.slice(next.length - MAX_UI_EVENTS) : next,
      };
    }),
  prependEvents: (newEvents) =>
    set((state) => {
      // Deduplicate by id: filter out events that already exist
      const existingIds = new Set(state.events.map((e) => e.id).filter(Boolean));
      const uniqueNew = newEvents.filter((e) => e.id && !existingIds.has(e.id));
      const merged = [...uniqueNew, ...state.events];
      return {
        // Keep oldest when over limit (trim from end, preserving history)
        events: merged.length > MAX_UI_EVENTS ? merged.slice(0, MAX_UI_EVENTS) : merged,
      };
    }),
  setActors: (actors) => set({ actors }),
  incrementActorUnread: (actorIds) =>
    set((state) => {
      if (!state.actors.length || !actorIds.length) return state;
      const targets = new Set(actorIds.map((id) => String(id || "").trim()).filter(Boolean));
      if (targets.size === 0) return state;

      let changed = false;
      const next = state.actors.map((actor) => {
        const actorId = String(actor.id || "").trim();
        if (!targets.has(actorId)) return actor;
        changed = true;
        return {
          ...actor,
          unread_count: Math.max(0, Number(actor.unread_count || 0)) + 1,
        };
      });

      return changed ? { actors: next } : state;
    }),
  updateActorActivity: (updates) =>
    set((state) => {
      if (!state.actors.length || !updates.length) return state;
      const map = new Map(updates.map((u) => [u.id, u]));
      let changed = false;
      const next = state.actors.map((a) => {
        const u = map.get(a.id);
        if (u && a.idle_seconds !== u.idle_seconds) {
          changed = true;
          return { ...a, idle_seconds: u.idle_seconds };
        }
        return a;
      });
      return changed ? { actors: next } : state;
    }),
  setGroupContext: (ctx) => set({ groupContext: ctx }),
  setGroupSettings: (settings) => set({ groupSettings: settings }),
  setRuntimes: (runtimes) => set({ runtimes }),

  updateReadStatus: (eventId, actorId) =>
    set((state) => {
      const idx = state.events.findIndex(
        (x) => x.kind === "chat.message" && String(x.id || "") === eventId
      );
      if (idx < 0 && !state.chatWindow) return state;

      const next = state.events.slice();
      let didChange = false;

      if (idx >= 0) {
        for (let i = 0; i <= idx; i++) {
          const m = next[i];
          if (!m || m.kind !== "chat.message") continue;
          const rs: Record<string, boolean> | null =
            m._read_status && typeof m._read_status === "object" ? { ...m._read_status } : null;
          const os =
            m._obligation_status && typeof m._obligation_status === "object"
              ? { ...m._obligation_status }
              : null;
          // Only mark for actual recipients; never add keys for non-recipients.
          if (!rs || !Object.prototype.hasOwnProperty.call(rs, actorId)) continue;
          if (rs[actorId] === true) continue;
          rs[actorId] = true;
          if (os && Object.prototype.hasOwnProperty.call(os, actorId) && typeof os[actorId] === "object") {
            os[actorId] = { ...os[actorId], read: true };
            next[i] = { ...m, _read_status: rs, _obligation_status: os };
          } else {
            next[i] = { ...m, _read_status: rs };
          }
          didChange = true;
        }
      }

      let nextWindow = state.chatWindow;
      if (state.chatWindow && String(state.chatWindow.groupId || "") === String(state.selectedGroupId || "")) {
        const wNext = state.chatWindow.events.slice();
        const wIdx = wNext.findIndex((x) => x.kind === "chat.message" && String(x.id || "") === eventId);
        if (wIdx >= 0) {
          for (let i = 0; i <= wIdx; i++) {
            const m = wNext[i];
            if (!m || m.kind !== "chat.message") continue;
            const rs: Record<string, boolean> | null =
              m._read_status && typeof m._read_status === "object" ? { ...m._read_status } : null;
            const os =
              m._obligation_status && typeof m._obligation_status === "object"
                ? { ...m._obligation_status }
                : null;
            if (!rs || !Object.prototype.hasOwnProperty.call(rs, actorId)) continue;
            if (rs[actorId] === true) continue;
            rs[actorId] = true;
            if (os && Object.prototype.hasOwnProperty.call(os, actorId) && typeof os[actorId] === "object") {
              os[actorId] = { ...os[actorId], read: true };
              wNext[i] = { ...m, _read_status: rs, _obligation_status: os };
            } else {
              wNext[i] = { ...m, _read_status: rs };
            }
            didChange = true;
          }
          nextWindow = { ...state.chatWindow, events: wNext };
        }
      }

      if (!didChange) return state;
      return { events: next, chatWindow: nextWindow };
    }),

  updateAckStatus: (eventId, actorId) =>
    set((state) => {
      const idx = state.events.findIndex(
        (x) => x.kind === "chat.message" && String(x.id || "") === eventId
      );
      if (idx < 0 && !state.chatWindow) return state;

      const next = state.events.slice();
      const msg = idx >= 0 ? next[idx] : null;

      let didChange = false;

      if (msg && msg.kind === "chat.message") {
        const as: Record<string, boolean> | null =
          msg._ack_status && typeof msg._ack_status === "object" ? { ...msg._ack_status } : null;
        const os =
          msg._obligation_status && typeof msg._obligation_status === "object"
            ? { ...msg._obligation_status }
            : null;
        if (as && Object.prototype.hasOwnProperty.call(as, actorId) && as[actorId] !== true) {
          as[actorId] = true;
          if (os && Object.prototype.hasOwnProperty.call(os, actorId) && typeof os[actorId] === "object") {
            os[actorId] = { ...os[actorId], acked: true };
            next[idx] = { ...msg, _ack_status: as, _obligation_status: os };
          } else {
            next[idx] = { ...msg, _ack_status: as };
          }
          didChange = true;
        }
      }

      let nextWindow = state.chatWindow;
      if (state.chatWindow && String(state.chatWindow.groupId || "") === String(state.selectedGroupId || "")) {
        const wNext = state.chatWindow.events.slice();
        const wIdx = wNext.findIndex((x) => x.kind === "chat.message" && String(x.id || "") === eventId);
        if (wIdx >= 0) {
          const wMsg = wNext[wIdx];
          const as: Record<string, boolean> | null =
            wMsg && wMsg.kind === "chat.message" && wMsg._ack_status && typeof wMsg._ack_status === "object"
              ? { ...wMsg._ack_status }
              : null;
          const os =
            wMsg && wMsg.kind === "chat.message" && wMsg._obligation_status && typeof wMsg._obligation_status === "object"
              ? { ...wMsg._obligation_status }
              : null;
          if (as && Object.prototype.hasOwnProperty.call(as, actorId) && as[actorId] !== true) {
            as[actorId] = true;
            if (os && Object.prototype.hasOwnProperty.call(os, actorId) && typeof os[actorId] === "object") {
              os[actorId] = { ...os[actorId], acked: true };
              wNext[wIdx] = { ...wMsg, _ack_status: as, _obligation_status: os };
            } else {
              wNext[wIdx] = { ...wMsg, _ack_status: as };
            }
            nextWindow = { ...state.chatWindow, events: wNext };
            didChange = true;
          }
        }
      }

      if (!didChange) return state;
      return { events: next, chatWindow: nextWindow };
    }),

  updateReplyStatus: (eventId, actorId) =>
    set((state) => {
      const idx = state.events.findIndex(
        (x) => x.kind === "chat.message" && String(x.id || "") === eventId
      );
      if (idx < 0 && !state.chatWindow) return state;

      const next = state.events.slice();
      const msg = idx >= 0 ? next[idx] : null;

      let didChange = false;

      if (msg && msg.kind === "chat.message") {
        const as: Record<string, boolean> | null =
          msg._ack_status && typeof msg._ack_status === "object" ? { ...msg._ack_status } : null;
        const os =
          msg._obligation_status && typeof msg._obligation_status === "object"
            ? { ...msg._obligation_status }
            : null;
        if (os && Object.prototype.hasOwnProperty.call(os, actorId) && typeof os[actorId] === "object") {
          const prev = os[actorId];
          const changed = !prev.replied || !prev.acked;
          if (changed) {
            os[actorId] = { ...prev, replied: true, acked: true };
            if (as && Object.prototype.hasOwnProperty.call(as, actorId)) {
              as[actorId] = true;
              next[idx] = { ...msg, _ack_status: as, _obligation_status: os };
            } else {
              next[idx] = { ...msg, _obligation_status: os };
            }
            didChange = true;
          }
        }
      }

      let nextWindow = state.chatWindow;
      if (state.chatWindow && String(state.chatWindow.groupId || "") === String(state.selectedGroupId || "")) {
        const wNext = state.chatWindow.events.slice();
        const wIdx = wNext.findIndex((x) => x.kind === "chat.message" && String(x.id || "") === eventId);
        if (wIdx >= 0) {
          const wMsg = wNext[wIdx];
          const as: Record<string, boolean> | null =
            wMsg && wMsg.kind === "chat.message" && wMsg._ack_status && typeof wMsg._ack_status === "object"
              ? { ...wMsg._ack_status }
              : null;
          const os =
            wMsg && wMsg.kind === "chat.message" && wMsg._obligation_status && typeof wMsg._obligation_status === "object"
              ? { ...wMsg._obligation_status }
              : null;
          if (os && Object.prototype.hasOwnProperty.call(os, actorId) && typeof os[actorId] === "object") {
            const prev = os[actorId];
            const changed = !prev.replied || !prev.acked;
            if (changed) {
              os[actorId] = { ...prev, replied: true, acked: true };
              if (as && Object.prototype.hasOwnProperty.call(as, actorId)) {
                as[actorId] = true;
                wNext[wIdx] = { ...wMsg, _ack_status: as, _obligation_status: os };
              } else {
                wNext[wIdx] = { ...wMsg, _obligation_status: os };
              }
              nextWindow = { ...state.chatWindow, events: wNext };
              didChange = true;
            }
          }
        }
      }

      if (!didChange) return state;
      return { events: next, chatWindow: nextWindow };
    }),
  setHasMoreHistory: (v) => set({ hasMoreHistory: v }),
  setIsLoadingHistory: (v) => set({ isLoadingHistory: v }),
  setIsChatWindowLoading: (v) => set({ isChatWindowLoading: v }),

  // Async actions
  refreshGroups: async () => {
    if (refreshGroupsInFlight) {
      refreshGroupsQueued = true;
      return;
    }
    refreshGroupsInFlight = true;
    try {
      const resp = await api.fetchGroups();
      if (resp.ok) {
        const next = resp.result.groups || [];
        // Use setGroups to ensure groupOrder is updated
        get().setGroups(next);

        const cur = get().selectedGroupId;
        const curExists = !!cur && next.some((g) => String(g.group_id || "") === cur);
        if (!curExists) {
          if (next.length > 0) {
            // Selected group disappeared (or none selected): switch to first group
            // and clear stale per-group caches so UI does not render old data while switching.
            const nextGroupId = String(next[0].group_id || "");
            set({
              selectedGroupId: nextGroupId,
              ...buildPrimedGroupState(nextGroupId, next),
            });
          } else {
            // No groups remain: clear selection + per-group caches.
            groupViewCache.clear();
            set({
              selectedGroupId: "",
              groupDoc: null,
              events: [],
              actors: [],
              groupContext: null,
              groupSettings: null,
              chatWindow: null,
              hasMoreHistory: false,
            });
          }
          return;
        }

        // Keep groupDoc's basic fields in sync with the group list. This matters when
        // group state/title/topic is changed externally (CLI/MCP/etc.), since groupDoc
        // is otherwise only updated by web actions or explicit loadGroup().
        const selectedId = get().selectedGroupId;
        const doc = get().groupDoc;
        if (doc && selectedId && String(doc.group_id || "") === String(selectedId || "")) {
          const meta = next.find((g) => String(g.group_id || "") === String(selectedId || "")) || null;
          if (meta) {
            const patch: Partial<GroupDoc> = {};
            if (typeof meta.state === "string" && meta.state !== doc.state) patch.state = meta.state;
            if (typeof meta.title === "string" && meta.title !== doc.title) patch.title = meta.title;
            if (typeof meta.topic === "string" && meta.topic !== doc.topic) patch.topic = meta.topic;
            if (Object.keys(patch).length > 0) {
              const nextDoc = { ...doc, ...patch };
              set({ groupDoc: nextDoc });
              saveGroupView(selectedId, { groupDoc: nextDoc });
            }
          }
        }
      }
    } catch (e) {
      console.error("Failed to refresh groups:", e);
    } finally {
      refreshGroupsInFlight = false;
      if (refreshGroupsQueued) {
        refreshGroupsQueued = false;
        void get().refreshGroups();
      }
    }
  },

  refreshActors: async (groupId?: string) => {
    const gid = String(groupId || get().selectedGroupId || "").trim();
    if (!gid) return;
    if (refreshActorsInFlight.has(gid)) {
      refreshActorsQueued.add(gid);
      return;
    }
    refreshActorsInFlight.add(gid);
    try {
      const resp = await api.fetchActors(gid);
      if (resp.ok) {
        const nextActors = resp.result.actors || [];
        saveGroupView(gid, { actors: nextActors });
        if (get().selectedGroupId === gid) {
          set({ actors: nextActors });
        }
      }
    } catch (e) {
      console.error(`Failed to refresh actors for group=${gid}:`, e);
    } finally {
      refreshActorsInFlight.delete(gid);
      if (refreshActorsQueued.has(gid)) {
        refreshActorsQueued.delete(gid);
        void get().refreshActors(gid);
      }
    }
  },

  loadGroup: async (groupId: string) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;

    const token = ++loadGroupToken;
    const isLatestSelection = () => get().selectedGroupId === gid && loadGroupToken === token;
    const commitPatch = (patch: Partial<Pick<GroupState, "groupDoc" | "events" | "actors" | "groupContext" | "groupSettings" | "hasMoreHistory">>) => {
      saveGroupView(gid, patch);
      if (isLatestSelection()) {
        set(patch);
      }
    };

    const state = get();
    const currentDocGroupId = String(state.groupDoc?.group_id || "").trim();
    if (currentDocGroupId !== gid) {
      const primedState = buildPrimedGroupState(gid, state.groups);
      saveGroupView(gid, primedState);
      if (isLatestSelection()) {
        set(primedState);
      }
    }

    const showPromise = api.fetchGroup(gid);
    const tailPromise = api.fetchLedgerTail(gid);
    const actorsPromise = api.fetchActors(gid);

    void showPromise.then((show) => {
      if (show.ok) {
        commitPatch({ groupDoc: show.result.group });
        return;
      }

      const code = String(show.error?.code || "").trim();
      if (code === "group_not_found") {
        groupViewCache.delete(gid);
        if (isLatestSelection()) {
          set({
            groupDoc: null,
            events: [],
            actors: [],
            groupContext: null,
            groupSettings: null,
            hasMoreHistory: false,
          });
        }
      }
    }).catch((error) => {
      console.error(`Failed to load group metadata for group=${gid}:`, error);
    });

    void tailPromise.then((tail) => {
      if (!tail.ok) return;
      commitPatch({
        events: filterUiEvents(tail.result.events || []),
        hasMoreHistory: !!tail.result.has_more,
      });
    }).catch((error) => {
      console.error(`Failed to load ledger tail for group=${gid}:`, error);
    });

    void actorsPromise.then((actorsResp) => {
      if (!actorsResp.ok) return;
      commitPatch({ actors: actorsResp.result.actors || [] });
    }).catch((error) => {
      console.error(`Failed to load actors for group=${gid}:`, error);
    });

    // 首屏稳定后再补 context/settings，避免它们拖住切组体感。
    void Promise.allSettled([showPromise, tailPromise, actorsPromise]).then(() => {
      void api.fetchContext(gid).then((ctx) => {
        if (!ctx.ok) return;
        commitPatch({ groupContext: ctx.result as GroupContext });
      }).catch((error) => {
        console.error(`Failed to load context for group=${gid}:`, error);
      });

      void api.fetchSettings(gid).then((settings) => {
        if (!settings.ok || !settings.result.settings) return;
        commitPatch({ groupSettings: settings.result.settings });
      }).catch((error) => {
        console.error(`Failed to load settings for group=${gid}:`, error);
      });
    });
  },

  warmGroup: async (groupId: string) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    if (gid === String(get().selectedGroupId || "").trim()) return;
    if (warmGroupInFlight.has(gid)) return;
    if (getCachedGroupView(gid)) return;

    warmGroupInFlight.add(gid);
    try {
      const [show, tail, actorsResp] = await Promise.all([
        api.fetchGroup(gid),
        api.fetchLedgerTail(gid, 40),
        api.fetchActors(gid),
      ]);

      const patch: Partial<Omit<GroupViewSnapshot, "cachedAt">> = {};
      if (show.ok) {
        patch.groupDoc = show.result.group;
      } else {
        patch.groupDoc = buildShellGroupDoc(gid, get().groups, null);
      }
      if (tail.ok) {
        patch.events = filterUiEvents(tail.result.events || []);
        patch.hasMoreHistory = !!tail.result.has_more;
      }
      if (actorsResp.ok) {
        patch.actors = actorsResp.result.actors || [];
      }

      saveGroupView(gid, patch);
    } catch (error) {
      console.error(`Failed to warm group=${gid}:`, error);
    } finally {
      warmGroupInFlight.delete(gid);
    }
  },

  loadMoreHistory: async () => {
    const { selectedGroupId, events, isLoadingHistory, hasMoreHistory } = get();
    if (!selectedGroupId) return;
    if (isLoadingHistory || !hasMoreHistory) return;

    // Find first chat message to use as cursor
    const chatMessages = events.filter((ev) => ev.kind === "chat.message");
    const firstEvent = chatMessages[0];
    if (!firstEvent?.id) return;

    set({ isLoadingHistory: true });
    try {
      const resp = await api.fetchOlderMessages(selectedGroupId, String(firstEvent.id), 50);
      // Guard against group switch during loading
      if (get().selectedGroupId !== selectedGroupId) return;

      if (resp.ok) {
        // Filter to only chat messages (same as initial load)
        const olderChatEvents = (resp.result.events || []).filter(
          (ev) => ev && ev.kind === "chat.message"
        );
        // Deduplicate and prepend
        const existingIds = new Set(get().events.map((e) => e.id).filter(Boolean));
        const uniqueNew = olderChatEvents.filter((e) => e.id && !existingIds.has(e.id));
        const merged = [...uniqueNew, ...get().events];
        set({
          events: merged.length > MAX_UI_EVENTS ? merged.slice(0, MAX_UI_EVENTS) : merged,
          hasMoreHistory: resp.result.has_more,
        });
      }
    } finally {
      set({ isLoadingHistory: false });
    }
  },

  openChatWindow: async (groupId: string, centerEventId: string) => {
    const gid = String(groupId || "").trim();
    const eid = String(centerEventId || "").trim();
    if (!gid || !eid) return;

    set({
      isChatWindowLoading: true,
      chatWindow: {
        groupId: gid,
        centerEventId: eid,
        centerIndex: 0,
        events: [],
        hasMoreBefore: false,
        hasMoreAfter: false,
      },
    });
    try {
      const resp = await api.fetchMessageWindow(gid, eid, { before: 30, after: 30 });
      if (!resp.ok) {
        set({ chatWindow: null });
        return;
      }
      // Guard against group switch during load.
      if (get().selectedGroupId !== gid) return;

      const events = (resp.result.events || []).filter((ev) => ev && ev.kind === "chat.message");
      set({
        chatWindow: {
          groupId: gid,
          centerEventId: resp.result.center_id,
          centerIndex: resp.result.center_index,
          events,
          hasMoreBefore: !!resp.result.has_more_before,
          hasMoreAfter: !!resp.result.has_more_after,
        },
      });
    } finally {
      set({ isChatWindowLoading: false });
    }
  },

  closeChatWindow: () => set({ chatWindow: null, isChatWindowLoading: false }),
}));
