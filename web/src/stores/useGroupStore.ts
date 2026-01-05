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
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  events: LedgerEvent[];
  actors: Actor[];
  groupContext: GroupContext | null;
  groupSettings: GroupSettings | null;
  runtimes: RuntimeInfo[];

  // Actions
  setGroups: (groups: GroupMeta[]) => void;
  setSelectedGroupId: (id: string) => void;
  setGroupDoc: (doc: GroupDoc | null) => void;
  setEvents: (events: LedgerEvent[]) => void;
  appendEvent: (event: LedgerEvent) => void;
  setActors: (actors: Actor[]) => void;
  setGroupContext: (ctx: GroupContext | null) => void;
  setGroupSettings: (settings: GroupSettings | null) => void;
  setRuntimes: (runtimes: RuntimeInfo[]) => void;
  updateReadStatus: (eventId: string, actorId: string) => void;

  // Async actions
  refreshGroups: () => Promise<void>;
  refreshActors: (groupId?: string) => Promise<void>;
  loadGroup: (groupId: string) => Promise<void>;
}

const MAX_UI_EVENTS = 800;

// In-flight guards
let refreshGroupsInFlight = false;
let refreshGroupsQueued = false;
const refreshActorsInFlight = new Set<string>();
const refreshActorsQueued = new Set<string>();

export const useGroupStore = create<GroupState>((set, get) => ({
  // Initial state
  groups: [],
  selectedGroupId: "",
  groupDoc: null,
  events: [],
  actors: [],
  groupContext: null,
  groupSettings: null,
  runtimes: [],

  // Sync actions
  setGroups: (groups) => set({ groups }),
  setSelectedGroupId: (id) => set({ selectedGroupId: id }),
  setGroupDoc: (doc) => set({ groupDoc: doc }),
  setEvents: (events) => set({ events }),
  appendEvent: (event) =>
    set((state) => {
      const next = state.events.concat([event]);
      return {
        events: next.length > MAX_UI_EVENTS ? next.slice(next.length - MAX_UI_EVENTS) : next,
      };
    }),
  setActors: (actors) => set({ actors }),
  setGroupContext: (ctx) => set({ groupContext: ctx }),
  setGroupSettings: (settings) => set({ groupSettings: settings }),
  setRuntimes: (runtimes) => set({ runtimes }),

  updateReadStatus: (eventId, actorId) =>
    set((state) => {
      const idx = state.events.findIndex(
        (x) => x.kind === "chat.message" && String(x.id || "") === eventId
      );
      if (idx < 0) return state;

      const next = state.events.slice();
      for (let i = 0; i <= idx; i++) {
        const m = next[i];
        if (!m || m.kind !== "chat.message") continue;
        const rs: Record<string, boolean> | null =
          m._read_status && typeof m._read_status === "object" ? { ...m._read_status } : null;
        // Only mark for actual recipients; never add keys for non-recipients.
        if (!rs || !Object.prototype.hasOwnProperty.call(rs, actorId)) continue;
        if (rs[actorId] === true) continue;
        rs[actorId] = true;
        next[i] = { ...m, _read_status: rs };
      }
      return { events: next };
    }),

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
        set({ groups: next });

        const cur = get().selectedGroupId;
        const curExists = !!cur && next.some((g) => String(g.group_id || "") === cur);
        if (!curExists && next.length > 0) {
          set({ selectedGroupId: String(next[0].group_id || "") });
        }
      }
    } catch {
      // Ignore transient failures
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
      if (resp.ok && get().selectedGroupId === gid) {
        set({ actors: resp.result.actors || [] });
      }
    } catch {
      // Ignore transient failures
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

    const [show, tail, a, ctx, settings] = await Promise.all([
      api.fetchGroup(gid),
      api.fetchLedgerTail(gid),
      api.fetchActors(gid),
      api.fetchContext(gid),
      api.fetchSettings(gid),
    ]);

    // Guard against out-of-order resolves when the user switches groups quickly.
    if (get().selectedGroupId !== gid) return;

    set({
      groupDoc: show.ok ? show.result.group : null,
      events: tail.ok
        ? (tail.result.events || []).filter((ev) => ev && ev.kind !== "context.sync")
        : [],
      actors: a.ok ? a.result.actors || [] : [],
      groupContext: ctx.ok ? (ctx.result as GroupContext) : null,
      groupSettings: settings.ok && settings.result.settings ? settings.result.settings : null,
    });
  },
}));
