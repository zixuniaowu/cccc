import { useEffect, useMemo, useRef, useState, useCallback, lazy, Suspense } from "react";
import { apiJson } from "./api";
import { TabBar } from "./components/TabBar";
import { ContextModal } from "./components/ContextModal";
import { SettingsModal } from "./components/SettingsModal";
import { SearchModal } from "./components/SearchModal";
import { ThemeToggleCompact } from "./components/ThemeToggle";
import { useTheme } from "./hooks/useTheme";
import {
  GroupMeta,
  GroupDoc,
  LedgerEvent,
  Actor,
  RuntimeInfo,
  ReplyTarget,
  GroupContext,
  GroupSettings,
  DirItem,
  DirSuggestion,
  RUNTIME_DEFAULTS,
  RUNTIME_INFO,
  getRuntimeColor,
} from "./types";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

const SUPPORTED_RUNTIMES = ["claude", "codex", "droid", "opencode", "copilot"] as const;
type SupportedRuntime = typeof SUPPORTED_RUNTIMES[number];

const COPILOT_MCP_CONFIG_SNIPPET = JSON.stringify(
  {
    mcpServers: {
      cccc: { command: "cccc", args: ["mcp"], tools: ["*"] },
    },
  },
  null,
  2
);

const OPENCODE_MCP_CONFIG_SNIPPET = JSON.stringify(
  {
    mcp: {
      cccc: { type: "local", command: ["cccc", "mcp"] },
    },
  },
  null,
  2
);

function isSupportedRuntime(rt: string): rt is SupportedRuntime {
  return (SUPPORTED_RUNTIMES as readonly string[]).includes(rt);
}

// Format ISO timestamp to friendly relative/absolute time
function formatTime(isoStr: string | undefined): string {
  if (!isoStr) return "—";
  try {
    const date = new Date(isoStr);
    if (isNaN(date.getTime())) return isoStr;
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);
    if (diffSec < 60) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    const month = date.toLocaleString("en", { month: "short" });
    const day = date.getDate();
    const year = date.getFullYear();
    const currentYear = now.getFullYear();
    if (year === currentYear) return `${month} ${day}`;
    return `${month} ${day}, ${year}`;
  } catch {
    return isoStr;
  }
}

function formatFullTime(isoStr: string | undefined): string {
  if (!isoStr) return "";
  try {
    const date = new Date(isoStr);
    if (isNaN(date.getTime())) return isoStr;
    return date.toLocaleString();
  } catch {
    return isoStr;
  }
}

function formatEventLine(ev: LedgerEvent): string {
  if (ev.kind === "chat.message" && ev.data && typeof ev.data === "object") {
    return String(ev.data.text || "");
  }
  if (ev.kind === "system.notify" && ev.data && typeof ev.data === "object") {
    const kind = String(ev.data.kind || "info");
    const title = String(ev.data.title || "");
    const message = String(ev.data.message || "");
    const target = ev.data.target_actor_id ? ` → ${ev.data.target_actor_id}` : "";
    return `[${kind}]${target}: ${title}${message ? ` - ${message}` : ""}`;
  }
  const k = String(ev.kind || "event");
  const by = ev.by ? ` by ${ev.by}` : "";
  return `${k}${by}`;
}

function getRecipientActorIdsForEvent(ev: LedgerEvent, actors: Actor[]): string[] {
  if (!actors.length) return [];
  const actorIds = actors.map((a) => String(a.id || "")).filter((id) => id);
  const actorIdSet = new Set(actorIds);

  const toRaw = (ev.data && typeof ev.data === "object" && Array.isArray((ev.data as any).to))
    ? (ev.data as any).to
    : [];
  const tokens = (toRaw as unknown[])
    .map((x) => String(x || "").trim())
    .filter((s) => s.length > 0);
  const tokenSet = new Set(tokens);

  const by = String(ev.by || "").trim();

  // Empty 'to' means broadcast for visibility; effective delivery is all actors (except sender).
  if (tokenSet.size === 0 || tokenSet.has("@all")) {
    return actorIds.filter((id) => id !== by);
  }

  const out = new Set<string>();
  for (const t of tokenSet) {
    if (t === "user" || t === "@user") continue;
    if (t === "@peers") {
      for (const a of actors) {
        if (a.role === "peer") out.add(String(a.id));
      }
      continue;
    }
    if (t === "@foreman") {
      for (const a of actors) {
        if (a.role === "foreman") out.add(String(a.id));
      }
      continue;
    }
    if (actorIdSet.has(t)) out.add(t);
  }

  out.delete(by);
  return Array.from(out);
}

function getProjectRoot(group: GroupDoc | null): string {
  if (!group) return "";
  const key = String(group.active_scope_key || "");
  if (!key) return "";
  const scopes = Array.isArray(group.scopes) ? group.scopes : [];
  const hit = scopes.find((s) => String(s.scope_key || "") === key);
  return String(hit?.url || "");
}

// Get group status display info based on running and state
function getGroupStatus(running: boolean, state?: string): { label: string; colorClass: string } {
  if (!running) {
    return { label: "○ STOP", colorClass: "bg-slate-700/50 text-slate-500" };
  }
  // Running - check state
  switch (state) {
    case "paused":
      return { label: "⏸ PAUSED", colorClass: "bg-amber-500/20 text-amber-500" };
    case "idle":
      return { label: "✓ IDLE", colorClass: "bg-blue-500/20 text-blue-400" };
    default: // active or undefined
      return { label: "● RUN", colorClass: "bg-emerald-500/20 text-emerald-500" };
  }
}

// Light theme variant
function getGroupStatusLight(running: boolean, state?: string): { label: string; colorClass: string } {
  if (!running) {
    return { label: "○ STOP", colorClass: "bg-gray-200 text-gray-500" };
  }
  switch (state) {
    case "paused":
      return { label: "⏸ PAUSED", colorClass: "bg-amber-100 text-amber-600" };
    case "idle":
      return { label: "✓ IDLE", colorClass: "bg-blue-100 text-blue-600" };
    default:
      return { label: "● RUN", colorClass: "bg-emerald-100 text-emerald-600" };
  }
}

const LazyAgentTab = lazy(() => import("./components/AgentTab").then((m) => ({ default: m.AgentTab })));
const MAX_UI_EVENTS = 800;

export default function App() {
  // Theme
  const { theme, setTheme, isDark } = useTheme();

  // Core state
  const [groups, setGroups] = useState<GroupMeta[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string>("");
  const [groupDoc, setGroupDoc] = useState<GroupDoc | null>(null);
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [actors, setActors] = useState<Actor[]>([]);
  const [groupContext, setGroupContext] = useState<GroupContext | null>(null);
  const [groupSettings, setGroupSettings] = useState<GroupSettings | null>(null);
  const [runtimes, setRuntimes] = useState<RuntimeInfo[]>([]);

  // Tab state
  const [activeTab, setActiveTab] = useState<string>("chat"); // "chat" or actor id

  // UI state
  const [busy, setBusy] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [showScrollButton, setShowScrollButton] = useState(false);
  const [chatUnreadCount, setChatUnreadCount] = useState(0);
  const [isSmallScreen, setIsSmallScreen] = useState(false);

  // Responsive hint for mobile-first copy (align with Tailwind `sm` breakpoint).
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    const update = () => setIsSmallScreen(mq.matches);
    update();
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", update);
      return () => mq.removeEventListener("change", update);
    }
    // Safari < 14
    mq.addListener(update);
    return () => mq.removeListener(update);
  }, []);

  // Modal state
  const [showContextModal, setShowContextModal] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [showSearchModal, setShowSearchModal] = useState(false);
  const [showAddActor, setShowAddActor] = useState(false);
  const [showCreateGroup, setShowCreateGroup] = useState(false);
  const [showGroupEdit, setShowGroupEdit] = useState(false);
  const [inboxOpen, setInboxOpen] = useState(false);
  const [editingActor, setEditingActor] = useState<Actor | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Composer state
  const [composerText, setComposerText] = useState("");
  const [toText, setToText] = useState("");
  const [replyTarget, setReplyTarget] = useState<ReplyTarget>(null);
  const [showMentionMenu, setShowMentionMenu] = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  const [mentionSelectedIndex, setMentionSelectedIndex] = useState(0);

  // Add actor form state
  const [newActorId, setNewActorId] = useState("");
  const [newActorRole, setNewActorRole] = useState<"peer" | "foreman">("peer");
  const [newActorRuntime, setNewActorRuntime] = useState<SupportedRuntime>("codex");
  const [newActorCommand, setNewActorCommand] = useState("");
  const [showAdvancedActor, setShowAdvancedActor] = useState(false);
  const [addActorError, setAddActorError] = useState("");

  // Edit actor form state
  const [editActorRuntime, setEditActorRuntime] = useState<SupportedRuntime>("codex");
  const [editActorCommand, setEditActorCommand] = useState("");
  const [editActorTitle, setEditActorTitle] = useState("");

  // Group edit state
  const [editGroupTitle, setEditGroupTitle] = useState("");
  const [editGroupTopic, setEditGroupTopic] = useState("");

  // Create group state
  const [createGroupPath, setCreateGroupPath] = useState("");
  const [createGroupName, setCreateGroupName] = useState("");
  const [dirItems, setDirItems] = useState<DirItem[]>([]);
  const [dirSuggestions, setDirSuggestions] = useState<DirSuggestion[]>([]);
  const [currentDir, setCurrentDir] = useState("");
  const [parentDir, setParentDir] = useState<string | null>(null);
  const [showDirBrowser, setShowDirBrowser] = useState(false);

  // Inbox state
  const [inboxActorId, setInboxActorId] = useState("");
  const [inboxMessages, setInboxMessages] = useState<LedgerEvent[]>([]);

  // Refs
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const errorTimeoutRef = useRef<number | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const eventContainerRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const activeTabRef = useRef<string>("chat");
  const chatAtBottomRef = useRef<boolean>(true);
  const actorsRef = useRef<Actor[]>([]);

  // Swipe gesture state
  const touchStartX = useRef<number>(0);
  const touchStartY = useRef<number>(0);

  // Show error with auto-dismiss
  const showError = useCallback((msg: string) => {
    if (errorTimeoutRef.current) window.clearTimeout(errorTimeoutRef.current);
    setErrorMsg(msg);
    errorTimeoutRef.current = window.setTimeout(() => {
      setErrorMsg("");
      errorTimeoutRef.current = null;
    }, 8000);
  }, []);

  // Computed values
  const projectRoot = useMemo(() => getProjectRoot(groupDoc), [groupDoc]);
  const hasForeman = useMemo(() => actors.some((a) => a.role === "foreman"), [actors]);
  const selectedGroupMeta = useMemo(
    () => groups.find((g) => String(g.group_id || "") === selectedGroupId) || null,
    [groups, selectedGroupId]
  );
  const selectedGroupRunning = selectedGroupMeta?.running ?? false;

  const suggestedActorId = useMemo(() => {
    const prefix = newActorRuntime;
    const existing = new Set(actors.map((a) => String(a.id || "")));
    for (let i = 1; i <= 999; i++) {
      const candidate = `${prefix}-${i}`;
      if (!existing.has(candidate)) return candidate;
    }
    return `${prefix}-${Date.now()}`;
  }, [actors, newActorRuntime]);

  const canAddActor = useMemo(() => {
    if (busy === "actor-add") return false;
    const rtInfo = runtimes.find((r) => r.name === newActorRuntime);
    const available = rtInfo?.available ?? false;
    if (!available && !newActorCommand.trim()) return false;
    return true;
  }, [busy, newActorRuntime, newActorCommand, runtimes]);

  const addActorDisabledReason = useMemo(() => {
    if (busy === "actor-add") return "";
    const rtInfo = runtimes.find((r) => r.name === newActorRuntime);
    const available = rtInfo?.available ?? false;
    if (!available && !newActorCommand.trim()) {
      return `${RUNTIME_INFO[newActorRuntime]?.label || newActorRuntime} is not installed. Install it, or set a command override.`;
    }
    return "";
  }, [busy, newActorRuntime, newActorCommand, runtimes]);

  const toTokens = useMemo(() => {
    return toText.split(",").map((t) => t.trim()).filter((t) => t.length > 0);
  }, [toText]);

  const mentionSuggestions = useMemo(() => {
    const base = ["@all", "@foreman", "@peers", "user"];
    const actorIds = actors.map((a) => String(a.id || "")).filter((id) => id);
    const all = [...base, ...actorIds];
    if (!mentionFilter) return all;
    const lower = mentionFilter.toLowerCase();
    return all.filter((s) => s.toLowerCase().includes(lower));
  }, [actors, mentionFilter]);

  // Get all tabs for swipe navigation
  const allTabs = useMemo(() => {
    return ["chat", ...actors.map((a) => a.id)];
  }, [actors]);

  // API functions
  async function refreshGroups() {
    const resp = await apiJson<{ groups: GroupMeta[] }>("/api/v1/groups");
    if (resp.ok) {
      setGroups(resp.result.groups || []);
      if (!selectedGroupId && resp.result.groups?.length) {
        setSelectedGroupId(String(resp.result.groups[0].group_id || ""));
      }
    }
  }

  async function fetchRuntimes() {
    const resp = await apiJson<{ runtimes: RuntimeInfo[]; available: string[] }>("/api/v1/runtimes");
    if (resp.ok) {
      setRuntimes(resp.result.runtimes || []);
      const availableNames = (resp.result.runtimes || []).filter((r) => r.available).map((r) => r.name);
      if (availableNames.length > 0 && !availableNames.includes(newActorRuntime)) {
        const pick = SUPPORTED_RUNTIMES.find((rt) => availableNames.includes(rt));
        if (pick) setNewActorRuntime(pick);
      }
    }
  }

  async function fetchDirSuggestions() {
    const resp = await apiJson<{ suggestions: DirSuggestion[] }>("/api/v1/fs/recent");
    if (resp.ok) setDirSuggestions(resp.result.suggestions || []);
  }

  async function fetchDirContents(path: string) {
    setShowDirBrowser(true);
    const resp = await apiJson<{ path: string; parent: string | null; items: DirItem[] }>(
      `/api/v1/fs/list?path=${encodeURIComponent(path)}`
    );
    if (resp.ok) {
      setDirItems(resp.result.items || []);
      setCurrentDir(resp.result.path || path);
      setParentDir(resp.result.parent || null);
    } else {
      showError(resp.error?.message || "Failed to list directory");
    }
  }

  async function fetchContext(groupId: string) {
    const resp = await apiJson<{ context: GroupContext }>(`/api/v1/groups/${encodeURIComponent(groupId)}/context`);
    if (resp.ok) setGroupContext(resp.result.context || null);
  }

  async function fetchSettings(groupId: string) {
    const resp = await apiJson<{ settings: GroupSettings }>(`/api/v1/groups/${encodeURIComponent(groupId)}/settings`);
    if (resp.ok && resp.result.settings) setGroupSettings(resp.result.settings);
  }

  async function loadGroup(groupId: string) {
    setGroupDoc(null);
    setEvents([]);
    setActors([]);
    setGroupContext(null);
    setGroupSettings(null);
    setErrorMsg("");
    setActiveTab("chat");

    const show = await apiJson<{ group: GroupDoc }>(`/api/v1/groups/${encodeURIComponent(groupId)}`);
    if (show.ok) setGroupDoc(show.result.group);

    const tail = await apiJson<{ events: LedgerEvent[] }>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/tail?lines=120&with_read_status=true`
    );
    if (tail.ok) setEvents(tail.result.events || []);

    const a = await apiJson<{ actors: Actor[] }>(`/api/v1/groups/${encodeURIComponent(groupId)}/actors?include_unread=true`);
    if (a.ok) setActors(a.result.actors || []);

    await fetchContext(groupId);
    await fetchSettings(groupId);
  }

  function connectStream(groupId: string) {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    const es = new EventSource(`/api/v1/groups/${encodeURIComponent(groupId)}/ledger/stream`);
    es.addEventListener("ledger", (e) => {
      const msg = e as MessageEvent;
      try {
        const ev = JSON.parse(String(msg.data || "{}"));

        // Real-time read status: apply chat.read without requiring a full refresh.
        if (ev && typeof ev === "object" && ev.kind === "chat.read") {
          const actorId = String(ev.data?.actor_id || "");
          const eventId = String(ev.data?.event_id || "");

          if (actorId && eventId) {
            setEvents((prev) => {
              const idx = prev.findIndex((x) => x.kind === "chat.message" && String(x.id || "") === eventId);
              if (idx < 0) return prev;

              const next = prev.slice();
              for (let i = 0; i <= idx; i++) {
                const m = next[i];
                if (!m || m.kind !== "chat.message") continue;

                const recipients = getRecipientActorIdsForEvent(m, actorsRef.current);
                if (!recipients.includes(actorId)) continue;

                const rs: Record<string, boolean> = (m._read_status && typeof m._read_status === "object") ? { ...m._read_status } : {};
                if (rs[actorId] === true) continue;
                rs[actorId] = true;
                next[i] = { ...m, _read_status: rs };
              }
              return next;
            });
          }

          refreshActors();
          return;
        }

        // Initialize read-status keys for new messages so "○/✓" can update live.
        if (ev && typeof ev === "object" && ev.kind === "chat.message" && !ev._read_status) {
          const recipients = getRecipientActorIdsForEvent(ev, actorsRef.current);
          if (recipients.length > 0) {
            const rs: Record<string, boolean> = {};
            for (const id of recipients) rs[id] = false;
            ev._read_status = rs;
          }
        }

        setEvents((prev) => {
          const next = prev.concat([ev]);
          return next.length > MAX_UI_EVENTS ? next.slice(next.length - MAX_UI_EVENTS) : next;
        });
        if (ev && typeof ev === "object" && ev.kind === "chat.message") {
          const by = String(ev.by || "");
          if (by && by !== "user") {
            const chatActive = activeTabRef.current === "chat";
            const atBottom = chatAtBottomRef.current;
            if (!chatActive || !atBottom) {
              setChatUnreadCount((c) => c + 1);
            }
          }
        }
        if (ev.kind === "chat.message") {
          refreshActors();
        }
      } catch { /* ignore */ }
    });
    eventSourceRef.current = es;
  }

  async function refreshActors() {
    if (!selectedGroupId) return;
    const a = await apiJson<{ actors: Actor[] }>(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors?include_unread=true`);
    if (a.ok) setActors(a.result.actors || []);
  }


  // Message functions
  function toggleRecipient(token: string) {
    const t = token.trim();
    if (!t) return;
    const cur = toTokens;
    const idx = cur.findIndex((x) => x === t);
    if (idx >= 0) {
      const next = cur.slice(0, idx).concat(cur.slice(idx + 1));
      setToText(next.join(", "));
    } else {
      setToText(cur.concat([t]).join(", "));
    }
  }

  async function sendMessage() {
    const txt = composerText.trim();
    if (!txt || !selectedGroupId) return;
    setBusy("send");
    try {
      setErrorMsg("");
      const to = toTokens;
      let resp;
      if (replyTarget) {
        const replyBy = String(replyTarget.by || "").trim();
        const replyFallbackTo =
          replyBy && replyBy !== "user" && replyBy !== "unknown"
            ? [replyBy]
            : ["@all"];
        const replyTo = to.length ? to : replyFallbackTo;
        resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/reply`, {
          method: "POST",
          body: JSON.stringify({
            text: txt,
            by: "user",
            to: replyTo,
            reply_to: replyTarget.eventId,
          }),
        });
      } else {
        resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/send`, {
          method: "POST",
          body: JSON.stringify({ text: txt, by: "user", to, path: "" }),
        });
      }
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setComposerText("");
      setReplyTarget(null);
    } finally {
      setBusy("");
    }
  }

  function startReply(ev: LedgerEvent) {
    if (!ev.id || ev.kind !== "chat.message") return;
    const text = ev.data?.text ? String(ev.data.text) : "";
    setReplyTarget({
      eventId: String(ev.id),
      by: String(ev.by || "unknown"),
      text: text.slice(0, 100) + (text.length > 100 ? "..." : ""),
    });
  }

  // Actor functions
  async function addActor() {
    if (!selectedGroupId) return;
    const actorId = newActorId.trim();
    setBusy("actor-add");
    setAddActorError("");
    try {
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors`, {
        method: "POST",
        body: JSON.stringify({
          actor_id: actorId,
          role: newActorRole,
          runner: "pty",
          runtime: newActorRuntime,
          command: newActorCommand,
          env: {},
          default_scope_key: "",
          by: "user",
        }),
      });
      if (!resp.ok) {
        setAddActorError(resp.error?.message || "Failed to add agent");
        return;
      }
      setShowAddActor(false);
      setNewActorId("");
      setNewActorCommand(RUNTIME_DEFAULTS.codex || "");
      setNewActorRole("peer");
      setNewActorRuntime("codex");
      setAddActorError("");
      await refreshActors();
    } finally {
      setBusy("");
    }
  }

  async function toggleActorEnabled(actor: Actor) {
    if (!selectedGroupId) return;
    const actorId = String(actor.id || "").trim();
    if (!actorId) return;
    const isRunning = actor.running ?? actor.enabled ?? false;
    setBusy(`actor-${isRunning ? "stop" : "start"}:${actorId}`);
    try {
      setErrorMsg("");
      const action = isRunning ? "stop" : "start";
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors/${encodeURIComponent(actorId)}/${action}`, {
        method: "POST",
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await refreshActors();
    } finally {
      setBusy("");
    }
  }

  async function removeActor(actor: Actor) {
    if (!selectedGroupId) return;
    const actorId = String(actor.id || "").trim();
    if (!actorId) return;
    if (!window.confirm(`Remove actor ${actorId}?`)) return;
    setBusy(`actor-remove:${actorId}`);
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors/${encodeURIComponent(actorId)}?by=user`, {
        method: "DELETE",
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      // If we're viewing this actor's tab, switch to chat
      if (activeTab === actorId) setActiveTab("chat");
      await refreshActors();
      await loadGroup(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  function openEditActor(actor: Actor) {
    const isRunning = actor.running ?? actor.enabled ?? false;
    if (isRunning) {
      showError("Stop the actor before editing. Use stop → edit → start workflow.");
      return;
    }
    setEditingActor(actor);
    const rtRaw = String(actor.runtime || "").trim();
    const rt: SupportedRuntime = isSupportedRuntime(rtRaw) ? rtRaw : "codex";
    setEditActorRuntime(rt);
    const cmd = Array.isArray(actor.command) ? actor.command.join(" ") : "";
    setEditActorCommand(cmd || RUNTIME_DEFAULTS[rt] || "");
    setEditActorTitle(actor.title || "");
  }

  async function updateActor() {
    if (!selectedGroupId || !editingActor) return;
    const actorId = String(editingActor.id || "").trim();
    if (!actorId) return;
    setBusy("actor-update");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors/${encodeURIComponent(actorId)}`, {
        method: "POST",
        body: JSON.stringify({
          runtime: editActorRuntime,
          command: editActorCommand.trim(),
          title: editActorTitle.trim(),
          by: "user",
        }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setEditingActor(null);
      await refreshActors();
    } finally {
      setBusy("");
    }
  }

  // Group functions
  async function startGroup() {
    if (!selectedGroupId) return;
    setBusy("group-start");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/start?by=user`, { method: "POST" });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await refreshActors();
    } finally {
      setBusy("");
    }
  }

  async function stopGroup() {
    if (!selectedGroupId) return;
    setBusy("group-stop");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/stop?by=user`, { method: "POST" });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await refreshActors();
    } finally {
      setBusy("");
    }
  }

  async function createGroup() {
    const path = createGroupPath.trim();
    if (!path) return;
    const dirName = path.split("/").filter(Boolean).pop() || "working-group";
    const title = createGroupName.trim() || dirName;
    setBusy("create");
    try {
      setErrorMsg("");
      const resp = await apiJson<{ group_id: string }>("/api/v1/groups", {
        method: "POST",
        body: JSON.stringify({ title, topic: "", by: "user" }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      const groupId = resp.result.group_id;
      const attachResp = await apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/attach`, {
        method: "POST",
        body: JSON.stringify({ path, by: "user" }),
      });
      if (!attachResp.ok) {
        showError(`Created group but failed to attach: ${attachResp.error.message}`);
      }
      setCreateGroupPath("");
      setCreateGroupName("");
      setShowCreateGroup(false);
      await refreshGroups();
      setSelectedGroupId(groupId);
    } finally {
      setBusy("");
    }
  }

  async function updateGroup() {
    if (!selectedGroupId) return;
    setBusy("group-update");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}`, {
        method: "PUT",
        body: JSON.stringify({ title: editGroupTitle.trim(), topic: editGroupTopic.trim(), by: "user" }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setShowGroupEdit(false);
      await refreshGroups();
      await loadGroup(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  async function deleteGroup() {
    if (!selectedGroupId) return;
    if (!window.confirm(`Delete group "${groupDoc?.title || selectedGroupId}"? This cannot be undone.`)) return;
    setBusy("group-delete");
    try {
      setErrorMsg("");
      const resp = await apiJson(
        `/api/v1/groups/${encodeURIComponent(selectedGroupId)}?confirm=${encodeURIComponent(selectedGroupId)}&by=user`,
        { method: "DELETE" }
      );
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setSelectedGroupId("");
      setGroupDoc(null);
      setEvents([]);
      setActors([]);
      setGroupContext(null);
      setGroupSettings(null);
      await refreshGroups();
    } finally {
      setBusy("");
    }
  }

  // Context/Settings update functions
  async function updateVision(vision: string) {
    if (!selectedGroupId) return;
    setBusy("context-update");
    try {
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/context`, {
        method: "POST",
        body: JSON.stringify({ ops: [{ op: "vision.update", vision }], by: "user" }),
      });
      if (!resp.ok) showError(`${resp.error.code}: ${resp.error.message}`);
      await fetchContext(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  async function updateSketch(sketch: string) {
    if (!selectedGroupId) return;
    setBusy("context-update");
    try {
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/context`, {
        method: "POST",
        body: JSON.stringify({ ops: [{ op: "sketch.update", sketch }], by: "user" }),
      });
      if (!resp.ok) showError(`${resp.error.code}: ${resp.error.message}`);
      await fetchContext(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  async function updateSettings(settings: Partial<GroupSettings>) {
    if (!selectedGroupId) return;
    setBusy("settings-update");
    try {
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/settings`, {
        method: "PUT",
        body: JSON.stringify({ ...settings, by: "user" }),
      });
      if (!resp.ok) showError(`${resp.error.code}: ${resp.error.message}`);
      else setShowSettingsModal(false);
      await fetchSettings(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  async function setGroupState(state: "active" | "idle" | "paused") {
    if (!selectedGroupId) return;
    setBusy("group-state");
    try {
      const resp = await apiJson(
        `/api/v1/groups/${encodeURIComponent(selectedGroupId)}/state?state=${encodeURIComponent(state)}&by=user`,
        { method: "POST" }
      );
      if (!resp.ok) showError(`${resp.error.code}: ${resp.error.message}`);
      // Refresh groupDoc to update UI state
      await loadGroup(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  // Inbox functions
  async function openInbox(actorId: string) {
    const aid = String(actorId || "").trim();
    if (!aid || !selectedGroupId) return;
    setBusy(`inbox:${aid}`);
    try {
      setErrorMsg("");
      setInboxActorId(aid);
      setInboxMessages([]);
      setInboxOpen(true);
      const resp = await apiJson<{ messages: LedgerEvent[] }>(
        `/api/v1/groups/${encodeURIComponent(selectedGroupId)}/inbox/${encodeURIComponent(aid)}?by=user&limit=200`
      );
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setInboxMessages(resp.result.messages || []);
    } finally {
      setBusy("");
    }
  }

  async function markInboxAllRead() {
    if (!selectedGroupId || !inboxActorId) return;
    const last = inboxMessages.length ? inboxMessages[inboxMessages.length - 1] : null;
    const eventId = last?.id ? String(last.id) : "";
    if (!eventId) return;
    setBusy(`inbox-read:${inboxActorId}`);
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/inbox/${encodeURIComponent(inboxActorId)}/read`, {
        method: "POST",
        body: JSON.stringify({ event_id: eventId, by: "user" }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await openInbox(inboxActorId);
    } finally {
      setBusy("");
    }
  }


  // Effects
  useEffect(() => {
    refreshGroups();
    fetchRuntimes();
    fetchDirSuggestions();
    const t = window.setInterval(refreshGroups, 5000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 640px)");
    const update = () => setIsSmallScreen(mq.matches);
    update();
    try {
      mq.addEventListener("change", update);
      return () => mq.removeEventListener("change", update);
    } catch {
      // Safari / older browsers
      // eslint-disable-next-line deprecation/deprecation
      mq.addListener(update);
      // eslint-disable-next-line deprecation/deprecation
      return () => mq.removeListener(update);
    }
  }, []);

  useEffect(() => {
    if (!selectedGroupId) return;
    loadGroup(selectedGroupId);
    connectStream(selectedGroupId);
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [selectedGroupId]);

  useEffect(() => {
    activeTabRef.current = activeTab;
    if (activeTab === "chat") {
      setChatUnreadCount(0);
      const timer = window.setTimeout(() => {
        bottomRef.current?.scrollIntoView({ block: "end" });
        chatAtBottomRef.current = true;
        setShowScrollButton(false);
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [activeTab]);

  useEffect(() => {
    actorsRef.current = actors;
  }, [actors]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (activeTabRef.current !== "chat") return;
      const container = eventContainerRef.current;
      if (!container) return;

      const { scrollTop, scrollHeight, clientHeight } = container;
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 200;
      chatAtBottomRef.current = isNearBottom;

      const last = events.length ? events[events.length - 1] : null;
      const lastIsOwn = !!last && last.kind === "chat.message" && last.by === "user";

      if (isNearBottom || lastIsOwn) {
        bottomRef.current?.scrollIntoView({ block: "end" });
        chatAtBottomRef.current = true;
        setShowScrollButton(false);
        setChatUnreadCount(0);
      } else {
        setShowScrollButton(true);
      }
    }, 50);
    return () => window.clearTimeout(timer);
  }, [events.length]);

  useEffect(() => {
    if (RUNTIME_DEFAULTS[newActorRuntime]) {
      setNewActorCommand(RUNTIME_DEFAULTS[newActorRuntime]);
    }
  }, [newActorRuntime]);

  useEffect(() => {
    if (RUNTIME_DEFAULTS[editActorRuntime]) {
      setEditActorCommand(RUNTIME_DEFAULTS[editActorRuntime]);
    }
  }, [editActorRuntime]);

  // Scroll handling
  const handleScroll = () => {
    const container = eventContainerRef.current;
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 200;
    chatAtBottomRef.current = isNearBottom;
    setShowScrollButton(!isNearBottom);
    if (isNearBottom) setChatUnreadCount(0);
  };

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    chatAtBottomRef.current = true;
    setShowScrollButton(false);
    setChatUnreadCount(0);
  };

  // Swipe gesture handlers for mobile tab switching
  const handleTouchStart = (e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX;
    touchStartY.current = e.touches[0].clientY;
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    const touchEndX = e.changedTouches[0].clientX;
    const touchEndY = e.changedTouches[0].clientY;
    const deltaX = touchEndX - touchStartX.current;
    const deltaY = touchEndY - touchStartY.current;

    // Only trigger if horizontal swipe is dominant and significant
    if (Math.abs(deltaX) > 50 && Math.abs(deltaX) > Math.abs(deltaY) * 1.5) {
      const currentIndex = allTabs.indexOf(activeTab);
      if (deltaX > 0 && currentIndex > 0) {
        // Swipe right - go to previous tab
        setActiveTab(allTabs[currentIndex - 1]);
      } else if (deltaX < 0 && currentIndex < allTabs.length - 1) {
        // Swipe left - go to next tab
        setActiveTab(allTabs[currentIndex + 1]);
      }
    }
  };

  // Tab change handler
  const handleTabChange = (tab: string) => {
    setActiveTab(tab);
  };

  // Get current actor for agent tab
  const currentActor = useMemo(() => {
    if (activeTab === "chat") return null;
    return actors.find((a) => a.id === activeTab) || null;
  }, [activeTab, actors]);

  // Open group edit modal
  function openGroupEdit() {
    if (!groupDoc) return;
    setEditGroupTitle(groupDoc.title || "");
    setEditGroupTopic(groupDoc.topic || "");
    setShowGroupEdit(true);
  }

  // Render
  return (
    <div className={`h-full w-full ${isDark ? "bg-gradient-to-br from-slate-900 via-slate-900 to-slate-800" : "bg-gradient-to-br from-gray-50 via-white to-gray-100"}`}>
      <div className="h-full grid grid-cols-1 md:grid-cols-[280px_1fr] transition-all duration-300">
        {/* Sidebar */}
        <aside className={classNames(
          "h-full border-r flex flex-col",
          "fixed md:relative z-40 w-[280px] transition-transform duration-300",
          sidebarOpen ? "translate-x-0" : "-translate-x-full",
          isDark 
            ? "border-slate-700/50 bg-slate-900/80 backdrop-blur" 
            : "border-gray-200 bg-white/80 backdrop-blur"
        )}>
          <div className={`p-4 border-b ${isDark ? "border-slate-700/50" : "border-gray-200"}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-lg">🤖</span>
                <span className={`text-sm font-bold tracking-wide ${isDark ? "text-white" : "text-gray-900"}`}>CCCC</span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  className={`text-xs px-3 py-1.5 rounded-lg font-medium shadow-lg transition-all min-h-[36px] ${
                    isDark 
                      ? "bg-gradient-to-r from-blue-600 to-blue-500 text-white hover:from-blue-500 hover:to-blue-400 shadow-blue-500/20" 
                      : "bg-blue-600 text-white hover:bg-blue-500"
                  }`}
                  onClick={() => {
                    setShowCreateGroup(true);
                    fetchDirSuggestions();
                  }}
                  title="Create new working group"
                  aria-label="Create new working group"
                >
                  + New
                </button>
                <button
                  className={`md:hidden p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg transition-colors ${
                    isDark ? "text-slate-400 hover:text-white hover:bg-slate-800" : "text-gray-500 hover:text-gray-900 hover:bg-gray-100"
                  }`}
                  onClick={() => setSidebarOpen(false)}
                  aria-label="Close sidebar"
                >
                  ✕
                </button>
              </div>
            </div>
          </div>

          <div className="flex-1 overflow-auto p-3">
            <div className={`text-[10px] font-medium uppercase tracking-wider mb-2 px-2 ${isDark ? "text-slate-500" : "text-gray-500"}`}>Working Groups</div>
            {groups.map((g) => {
              const gid = String(g.group_id || "");
              const active = gid === selectedGroupId;
              return (
                <button
                  key={gid}
                  className={classNames(
                    "w-full text-left px-3 py-2.5 rounded-lg mb-1 transition-all min-h-[44px]",
                    active
                      ? isDark 
                        ? "bg-gradient-to-r from-blue-600/20 to-blue-500/10 border border-blue-500/30"
                        : "bg-blue-50 border border-blue-200"
                      : isDark
                        ? "hover:bg-slate-800/50 border border-transparent"
                        : "hover:bg-gray-100 border border-transparent"
                  )}
                  onClick={() => {
                    setSelectedGroupId(gid);
                    setSidebarOpen(false); // Close sidebar on mobile after selection
                  }}
                >
                  <div className="flex items-center justify-between">
                    <div className={classNames(
                      "text-sm font-medium truncate", 
                      active 
                        ? isDark ? "text-white" : "text-blue-700"
                        : isDark ? "text-slate-300" : "text-gray-700"
                    )}>
                      {g.title || gid}
                    </div>
                    {(() => {
                      const status = isDark 
                        ? getGroupStatus(g.running ?? false, g.state)
                        : getGroupStatusLight(g.running ?? false, g.state);
                      return (
                        <div className={classNames("text-[9px] px-2 py-0.5 rounded-full font-medium", status.colorClass)}>
                          {status.label}
                        </div>
                      );
                    })()}
                  </div>
                </button>
              );
            })}
            {!groups.length && (
              <div className="p-6 text-center">
                <div className="text-4xl mb-3">📁</div>
                <div className={`text-sm mb-2 ${isDark ? "text-slate-400" : "text-gray-600"}`}>No working groups yet</div>
                <div className={`text-xs mb-4 max-w-[200px] mx-auto ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  A working group is a collaboration space where multiple AI agents work together on a project.
                </div>
                <button
                  className={`text-sm px-4 py-2 rounded-lg font-medium shadow-lg min-h-[44px] transition-all ${
                    isDark 
                      ? "bg-gradient-to-r from-blue-600 to-blue-500 text-white hover:from-blue-500 hover:to-blue-400 shadow-blue-500/20"
                      : "bg-blue-600 text-white hover:bg-blue-500"
                  }`}
                  onClick={() => {
                    setShowCreateGroup(true);
                    fetchDirSuggestions();
                  }}
                >
                  Create Your First Group
                </button>
              </div>
            )}
          </div>
        </aside>

        {/* Sidebar overlay for mobile */}
        {sidebarOpen && (
          <div
            className={`fixed inset-0 z-30 md:hidden ${isDark ? "bg-black/50" : "bg-black/30"}`}
            onClick={() => setSidebarOpen(false)}
            aria-hidden="true"
          />
        )}

        {/* Main content */}
        <main className={`h-full flex flex-col overflow-hidden ${isDark ? "bg-slate-900/50" : "bg-white"}`}>
          {/* Header */}
          <header className={`flex-shrink-0 border-b backdrop-blur px-4 py-3 ${
            isDark ? "border-slate-700/50 bg-slate-800/30" : "border-gray-200 bg-gray-50/80"
          }`}>
            <div className="flex items-center justify-between gap-3">
              {/* Left: hamburger + title */}
              <div className="flex items-center gap-3 min-w-0">
                <button
                  className={`md:hidden p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg transition-colors ${
                    isDark ? "text-slate-400 hover:text-white hover:bg-slate-800" : "text-gray-500 hover:text-gray-900 hover:bg-gray-100"
                  }`}
                  onClick={() => setSidebarOpen(true)}
                  aria-label="Open sidebar"
                >
                  ☰
                </button>
                <div className={`text-base font-semibold truncate ${isDark ? "text-white" : "text-gray-900"}`}>
                  {groupDoc?.title || (selectedGroupId ? selectedGroupId : "Select a group")}
                </div>
                {/* Group status badge */}
                {selectedGroupId && groupDoc && (() => {
                  const status = isDark 
                    ? getGroupStatus(selectedGroupRunning, groupDoc.state)
                    : getGroupStatusLight(selectedGroupRunning, groupDoc.state);
                  return (
                    <span className={classNames("text-[10px] px-2 py-0.5 rounded-full font-medium hidden sm:inline-block", status.colorClass)}>
                      {status.label}
                    </span>
                  );
                })()}
                {selectedGroupId && (
                  <button
                    className={`hidden sm:flex items-center justify-center text-xs px-2 py-1 rounded-md border transition-colors min-h-[36px] ${
                      isDark 
                        ? "bg-slate-700/50 border-slate-600/50 text-slate-400 hover:text-white hover:bg-slate-600/50"
                        : "bg-gray-100 border-gray-200 text-gray-500 hover:text-gray-700 hover:bg-gray-200"
                    }`}
                    onClick={openGroupEdit}
                    title="Edit group"
                    aria-label="Edit group"
                  >
                    ✎
                  </button>
                )}
                {projectRoot && (
                  <span className={`hidden sm:block text-xs truncate max-w-[150px] px-2 py-1 rounded ${
                    isDark ? "bg-slate-800/50 text-slate-400" : "bg-gray-100 text-gray-500"
                  }`} title={projectRoot}>
                    📁 {projectRoot.split("/").pop()}
                  </span>
                )}
              </div>

              {/* Right: action buttons */}
              <div className="flex gap-2 items-center">
                <ThemeToggleCompact theme={theme} onThemeChange={setTheme} isDark={isDark} />
                <button
                  className={classNames(
                    "sm:hidden flex items-center justify-center w-9 h-9 rounded-lg transition-colors min-h-[44px] min-w-[44px]",
                    isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-300" : "bg-gray-100 hover:bg-gray-200 text-gray-600"
                  )}
                  onClick={() => setMobileMenuOpen(true)}
                  title="Menu"
                  aria-label="Open menu"
                >
                  <span className="text-xl leading-none" aria-hidden="true">⋯</span>
                </button>
                <button
                  className={`rounded-lg text-white px-3 py-1.5 text-sm font-medium disabled:opacity-50 shadow-lg transition-all hidden sm:flex items-center min-h-[44px] ${
                    isDark 
                      ? "bg-gradient-to-r from-emerald-600 to-emerald-500 hover:from-emerald-500 hover:to-emerald-400 shadow-emerald-500/20"
                      : "bg-emerald-600 hover:bg-emerald-500"
                  }`}
                  onClick={startGroup}
                  disabled={!selectedGroupId || busy === "group-start" || actors.length === 0}
                  title="Launch all agent processes"
                  aria-label="Launch all agents"
                >
                  ▶ Launch All
                </button>
                <button
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50 transition-colors hidden sm:flex items-center min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700/80 text-slate-200 hover:bg-slate-600"
                      : "bg-gray-200 text-gray-700 hover:bg-gray-300"
                  }`}
                  onClick={stopGroup}
                  disabled={!selectedGroupId || busy === "group-stop"}
                  title="Quit all agent processes"
                  aria-label="Quit all agents"
                >
                  ⏹ Quit All
                </button>
                {/* Pause/Resume toggle button */}
                {groupDoc?.state === "paused" ? (
                  <button
                    className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50 transition-all hidden sm:flex items-center min-h-[44px] ${
                      isDark 
                        ? "bg-amber-600/20 border border-amber-500/30 text-amber-400 hover:bg-amber-600/30"
                        : "bg-amber-50 border border-amber-200 text-amber-600 hover:bg-amber-100"
                    }`}
                    onClick={() => setGroupState("active")}
                    disabled={!selectedGroupId || busy === "group-state"}
                    title="Resume message delivery"
                    aria-label="Resume message delivery"
                  >
                    ▶ Resume
                  </button>
                ) : (
                  <button
                    className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50 transition-colors hidden sm:flex items-center min-h-[44px] ${
                      isDark 
                        ? "bg-slate-700/80 text-slate-200 hover:bg-slate-600"
                        : "bg-gray-200 text-gray-700 hover:bg-gray-300"
                    }`}
                    onClick={() => setGroupState("paused")}
                    disabled={!selectedGroupId || busy === "group-state"}
                    title="Pause message delivery"
                    aria-label="Pause message delivery"
                  >
                    ⏸ Pause
                  </button>
                )}
                <div className={`w-px h-6 mx-1 hidden sm:block ${isDark ? "bg-slate-700/50" : "bg-gray-300"}`} />
                <button
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50 transition-colors hidden sm:flex items-center min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700/80 text-slate-200 hover:bg-slate-600"
                      : "bg-gray-200 text-gray-700 hover:bg-gray-300"
                  }`}
                  onClick={() => setShowSearchModal(true)}
                  disabled={!selectedGroupId}
                  title="Search messages"
                  aria-label="Search messages"
                >
                  🔍
                </button>
                <button
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50 transition-colors hidden sm:flex items-center min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700/80 text-slate-200 hover:bg-slate-600"
                      : "bg-gray-200 text-gray-700 hover:bg-gray-300"
                  }`}
                  onClick={() => setShowContextModal(true)}
                  disabled={!selectedGroupId}
                  title="Context"
                  aria-label="Open project context"
                >
                  📋
                </button>
                <button
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50 transition-colors hidden sm:flex items-center min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700/80 text-slate-200 hover:bg-slate-600"
                      : "bg-gray-200 text-gray-700 hover:bg-gray-300"
                  }`}
                  onClick={() => setShowSettingsModal(true)}
                  disabled={!selectedGroupId}
                  title="Settings"
                  aria-label="Open settings"
                >
                  ⚙️
                </button>
              </div>
            </div>

            {/* Error message */}
            {errorMsg && (
              <div className={`mt-3 rounded-lg border px-4 py-2.5 text-sm flex items-center justify-between gap-3 animate-slide-up ${
                isDark 
                  ? "border-rose-500/30 bg-rose-500/10 text-rose-300"
                  : "border-rose-300 bg-rose-50 text-rose-700"
              }`} role="alert">
                <span>{errorMsg}</span>
                <button 
                  className={isDark ? "text-rose-300 hover:text-rose-100" : "text-rose-500 hover:text-rose-700"} 
                  onClick={() => setErrorMsg("")}
                  aria-label="Dismiss error"
                >
                  ×
                </button>
              </div>
            )}
          </header>

          {/* Tab Bar */}
          {selectedGroupId && (
            <TabBar
              actors={actors}
              activeTab={activeTab}
              onTabChange={handleTabChange}
              unreadChatCount={chatUnreadCount}
              isDark={isDark}
              onAddAgent={() => {
                setNewActorRole(hasForeman ? "peer" : "foreman");
                setShowAddActor(true);
              }}
              canAddAgent={!!selectedGroupId}
            />
          )}

          {/* Tab Content */}
          <div
            ref={contentRef}
            className="flex-1 min-h-0 flex flex-col overflow-hidden"
            onTouchStart={handleTouchStart}
            onTouchEnd={handleTouchEnd}
          >
            {activeTab === "chat" ? (
              /* Chat Tab Content */
              <>
                <section
                  ref={eventContainerRef}
                  className="flex-1 min-h-0 overflow-auto px-4 py-3 relative"
                  onScroll={handleScroll}
                  role="log"
                  aria-label="Chat messages"
                >
                  <div className="space-y-2">
                    {events
                      .filter((ev) => ev.kind === "chat.message")
                      .map((ev, idx) => {
                        const isMessage = ev.kind === "chat.message";
                        const isUserMessage = isMessage && ev.by === "user";
                        const replyTo = ev.data?.reply_to;
                        const quoteText = ev.data?.quote_text;
                        const readStatus = ev._read_status;
                        const recipients = ev.data?.to as string[] | undefined;
                        const visibleReadStatusEntries = readStatus
                          ? actors
                              .map((a) => String(a.id || ""))
                              .filter((id) => id && Object.prototype.hasOwnProperty.call(readStatus, id))
                              .map((id) => [id, !!readStatus[id]] as const)
                          : [];
                        const senderActor = actors.find((a) => a.id === ev.by);
                        const senderRuntime = isUserMessage ? "user" : (senderActor?.runtime || "codex");
                        const senderColor = getRuntimeColor(senderRuntime, isDark);

                        return (
                          <div
                            key={String(ev.id || idx)}
                            className={classNames(
                              "rounded border px-3 py-2 transition-colors",
                              isDark ? "bg-slate-950/40" : "bg-white",
                              senderColor.border, senderColor.bg
                            )}
                          >
                            <div className="flex items-center justify-between gap-3">
                              <div className="flex items-center gap-2 min-w-0">
                                <span className={classNames(
                                  "text-[10px] px-1.5 py-0.5 rounded font-medium",
                                  senderColor.bg, senderColor.text
                                )}>
                                  {ev.by || "unknown"}
                                </span>
                                {recipients && recipients.length > 0 && (
                                  <span className={`text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>→ {recipients.join(", ")}</span>
                                )}
                                {replyTo && <span className={`text-[10px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>(reply)</span>}
                              </div>
                              <div className="flex items-center gap-2">
                                <div className={`text-xs truncate ${isDark ? "text-slate-500" : "text-gray-500"}`} title={formatFullTime(ev.ts)}>
                                  {formatTime(ev.ts)}
                                </div>
                                <button
                                  className={`text-[10px] px-1.5 py-0.5 rounded border min-h-[28px] transition-colors ${
                                    isDark 
                                      ? "bg-slate-900 border-slate-800 hover:bg-slate-800/60 text-slate-400 hover:text-slate-200"
                                      : "bg-gray-100 border-gray-200 hover:bg-gray-200 text-gray-500 hover:text-gray-700"
                                  }`}
                                  onClick={() => startReply(ev)}
                                  title="Reply"
                                  aria-label={`Reply to ${ev.by}`}
                                >
                                  ↩
                                </button>
                              </div>
                            </div>
                            {quoteText && (
                              <div className={`mt-1 text-xs border-l-2 pl-2 italic truncate ${
                                isDark ? "text-slate-500 border-slate-700" : "text-gray-500 border-gray-300"
                              }`}>
                                "{quoteText}"
                              </div>
                            )}
                            <div className={`mt-1 text-sm whitespace-pre-wrap break-words ${isDark ? "text-slate-200" : "text-gray-800"}`}>
                              {formatEventLine(ev)}
                            </div>
                            {visibleReadStatusEntries.length > 0 && (
                              <div className="mt-1.5 flex items-center gap-1.5 text-[10px]">
                                {visibleReadStatusEntries.map(([actorId, hasRead]) => (
                                  <span
                                    key={actorId}
                                    className={classNames(
                                      "inline-flex items-center gap-0.5",
                                      hasRead ? "text-emerald-500" : isDark ? "text-slate-500" : "text-gray-400"
                                    )}
                                    title={hasRead ? `${actorId} has read` : `${actorId} hasn't read`}
                                  >
                                    {hasRead ? "✓" : "○"} {actorId}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    <div ref={bottomRef} />
                    {events.filter((ev) => ev.kind === "chat.message").length === 0 && (
                      <div className="text-center py-8">
                        <div className="text-3xl mb-2">💬</div>
                        <div className={`text-sm ${isDark ? "text-slate-400" : "text-gray-600"}`}>No messages yet</div>
                        <div className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>Send a message to start the conversation</div>
                      </div>
                    )}
                  </div>
                  {showScrollButton && (
                    <button
                      className={`absolute bottom-4 right-4 rounded-full border p-2 shadow-lg transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center relative ${
                        isDark 
                          ? "bg-slate-800 border-slate-700 hover:bg-slate-700"
                          : "bg-white border-gray-300 hover:bg-gray-50"
                      }`}
                      onClick={scrollToBottom}
                      title="Scroll to bottom"
                      aria-label="Scroll to bottom"
                    >
                      {chatUnreadCount > 0 && (
                        <span
                          className="absolute -top-1 -right-1 bg-rose-500 text-white text-[10px] px-1.5 py-0.5 rounded-full font-medium min-w-[18px] text-center"
                          aria-label={`${chatUnreadCount} new messages`}
                        >
                          {chatUnreadCount > 99 ? "99+" : chatUnreadCount}
                        </span>
                      )}
                      <svg className={`w-4 h-4 ${isDark ? "text-slate-300" : "text-gray-600"}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                      </svg>
                    </button>
                  )}
                </section>

                {/* Composer */}
                <footer className={`flex-shrink-0 border-t px-4 pt-3 pb-4 safe-area-inset-bottom ${
                  isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"
                }`}>
                  {replyTarget && (
                    <div className={`mb-2 flex items-center gap-2 text-xs rounded px-2 py-1.5 ${
                      isDark ? "text-slate-400 bg-slate-900/50" : "text-gray-500 bg-gray-100"
                    }`}>
                      <span className={isDark ? "text-slate-500" : "text-gray-400"}>Replying to</span>
                      <span className={`font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{replyTarget.by}</span>
                      <span className={`truncate flex-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>"{replyTarget.text}"</span>
                      <button 
                        className={`px-1 min-h-[28px] ${isDark ? "text-slate-400 hover:text-slate-200" : "text-gray-400 hover:text-gray-600"}`} 
                        onClick={() => setReplyTarget(null)} 
                        title="Cancel reply"
                        aria-label="Cancel reply"
                      >
                        ×
                      </button>
                    </div>
                  )}
                  <div className="mb-2 flex items-center gap-2">
                    <div className={`text-xs flex-shrink-0 ${isDark ? "text-slate-400" : "text-gray-500"}`}>To</div>
                    <div className="flex-1 min-w-0 overflow-x-auto scrollbar-hide sm:overflow-visible">
                      <div className="flex items-center gap-2 flex-nowrap sm:flex-wrap">
                        {["@all", "@foreman", "@peers", "user", ...actors.map((a) => String(a.id || ""))].map((tok) => {
                          const t = tok.trim();
                          if (!t) return null;
                          const active = toTokens.includes(t);
                          return (
                            <button
                              key={t}
                              className={classNames(
                                "flex-shrink-0 whitespace-nowrap text-[11px] px-2 py-1.5 rounded border min-h-[32px] transition-colors",
                                active
                                  ? "bg-emerald-500 text-white border-emerald-400"
                                  : isDark 
                                    ? "bg-slate-950/40 text-slate-200 border-slate-800 hover:bg-slate-800/40"
                                    : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                              )}
                              onClick={() => toggleRecipient(t)}
                              disabled={!selectedGroupId || busy === "send"}
                              title={active ? "Remove recipient" : "Add recipient"}
                              aria-pressed={active}
                            >
                              {t}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    {toTokens.length > 0 && (
                      <button
                        className={`flex-shrink-0 text-[11px] px-2 py-1.5 rounded border disabled:opacity-50 min-h-[32px] transition-colors ${
                          isDark 
                            ? "bg-slate-900 border-slate-800 hover:bg-slate-800/60 text-slate-300"
                            : "bg-gray-100 border-gray-200 hover:bg-gray-200 text-gray-600"
                        }`}
                        onClick={() => setToText("")}
                        disabled={busy === "send"}
                        title="Clear recipients"
                      >
                        clear
                      </button>
                    )}
                  </div>
                  <div className="flex gap-2 relative items-end">
                    <textarea
                      ref={composerRef}
                      className={`w-full rounded border px-3 py-2 text-sm resize-none min-h-[44px] max-h-[120px] transition-colors ${
                        isDark 
                          ? "bg-slate-900 border-slate-800 text-slate-200 placeholder-slate-500"
                          : "bg-white border-gray-300 text-gray-900 placeholder-gray-400"
                        }`}
                      placeholder={isSmallScreen ? "Message…" : "Message… (type @ to mention, Ctrl/⌘+Enter to send)"}
                      rows={1}
                      value={composerText}
                      onChange={(e) => {
                        const val = e.target.value;
                        setComposerText(val);
                        const target = e.target;
                        target.style.height = "auto";
                        target.style.height = Math.min(target.scrollHeight, 120) + "px";
                        const lastAt = val.lastIndexOf("@");
                        if (lastAt >= 0) {
                          const afterAt = val.slice(lastAt + 1);
                          if ((lastAt === 0 || val[lastAt - 1] === " " || val[lastAt - 1] === "\n") && !afterAt.includes(" ") && !afterAt.includes("\n")) {
                            setMentionFilter(afterAt);
                            setShowMentionMenu(true);
                            setMentionSelectedIndex(0);
                          } else {
                            setShowMentionMenu(false);
                          }
                        } else {
                          setShowMentionMenu(false);
                        }
                      }}
                      onKeyDown={(e) => {
                        if (showMentionMenu && mentionSuggestions.length > 0) {
                          const maxIndex = Math.min(mentionSuggestions.length, 8) - 1;
                          if (e.key === "ArrowDown") {
                            e.preventDefault();
                            setMentionSelectedIndex((prev) => (prev >= maxIndex ? 0 : prev + 1));
                            return;
                          }
                          if (e.key === "ArrowUp") {
                            e.preventDefault();
                            setMentionSelectedIndex((prev) => (prev <= 0 ? maxIndex : prev - 1));
                            return;
                          }
                          if (e.key === "Enter" || e.key === "Tab") {
                            e.preventDefault();
                            const selected = mentionSuggestions[mentionSelectedIndex];
                            if (selected) {
                              const lastAt = composerText.lastIndexOf("@");
                              if (lastAt >= 0) {
                                const before = composerText.slice(0, lastAt);
                                setComposerText(before + selected + " ");
                              }
                              if (!toTokens.includes(selected)) {
                                setToText((prev) => (prev ? prev + ", " + selected : selected));
                              }
                              setShowMentionMenu(false);
                              setMentionSelectedIndex(0);
                            }
                            return;
                          }
                          if (e.key === "Escape") {
                            e.preventDefault();
                            setShowMentionMenu(false);
                            setMentionSelectedIndex(0);
                            return;
                          }
                        }
                        if (e.key === "Enter" && !showMentionMenu) {
                          if (e.ctrlKey || e.metaKey) {
                            e.preventDefault();
                            sendMessage();
                          }
                        } else if (e.key === "Escape") {
                          setShowMentionMenu(false);
                          setReplyTarget(null);
                        }
                      }}
                      onBlur={() => setTimeout(() => setShowMentionMenu(false), 150)}
                      aria-label="Message input"
                    />
                    {showMentionMenu && mentionSuggestions.length > 0 && (
                      <div 
                        className={`absolute bottom-full left-0 mb-1 w-48 max-h-40 overflow-auto rounded border shadow-lg z-10 ${
                          isDark ? "border-slate-700 bg-slate-900" : "border-gray-200 bg-white"
                        }`}
                        role="listbox"
                        aria-label="Mention suggestions"
                      >
                        {mentionSuggestions.slice(0, 8).map((s, idx) => (
                          <button
                            key={s}
                            className={classNames(
                              "w-full text-left px-3 py-2 text-sm min-h-[40px]",
                              isDark ? "text-slate-200" : "text-gray-700",
                              idx === mentionSelectedIndex 
                                ? isDark ? "bg-slate-700" : "bg-blue-50"
                                : isDark ? "hover:bg-slate-800" : "hover:bg-gray-100"
                            )}
                            onMouseDown={(e) => {
                              e.preventDefault();
                              const lastAt = composerText.lastIndexOf("@");
                              if (lastAt >= 0) {
                                const before = composerText.slice(0, lastAt);
                                setComposerText(before + s + " ");
                              }
                              if (!toTokens.includes(s)) {
                                setToText((prev) => (prev ? prev + ", " + s : s));
                              }
                              setShowMentionMenu(false);
                              setMentionSelectedIndex(0);
                              composerRef.current?.focus();
                            }}
                            onMouseEnter={() => setMentionSelectedIndex(idx)}
                            role="option"
                            aria-selected={idx === mentionSelectedIndex}
                          >
                            {s}
                          </button>
                        ))}
                      </div>
                    )}
                    <button
                      className="rounded bg-emerald-500 hover:bg-emerald-400 text-white px-4 py-2 text-sm font-semibold disabled:opacity-50 min-h-[44px] transition-colors"
                      onClick={sendMessage}
                      disabled={!composerText.trim() || busy === "send"}
                      aria-label="Send message"
                    >
                      Send
                    </button>
                  </div>
                </footer>
              </>
            ) : currentActor ? (
              /* Agent Tab Content */
              <Suspense
                fallback={
                  <div className={`flex-1 flex items-center justify-center ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                    Loading agent…
                  </div>
                }
              >
                <LazyAgentTab
                  actor={currentActor}
                  groupId={selectedGroupId}
                  isVisible={true}
                  onQuit={() => toggleActorEnabled(currentActor)}
                  onLaunch={() => toggleActorEnabled(currentActor)}
                  onRelaunch={async () => {
                    if (!selectedGroupId) return;
                    setBusy(`actor-relaunch:${currentActor.id}`);
                    try {
                      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors/${encodeURIComponent(currentActor.id)}/restart?by=user`, {
                        method: "POST",
                      });
                      if (!resp.ok) {
                        showError(`${resp.error.code}: ${resp.error.message}`);
                      }
                      await refreshActors();
                    } finally {
                      setBusy("");
                    }
                  }}
                  onEdit={() => openEditActor(currentActor)}
                  onRemove={() => removeActor(currentActor)}
                  onInbox={() => openInbox(currentActor.id)}
                  busy={busy}
                  isDark={isDark}
                />
              </Suspense>
            ) : (
              <div className="flex-1 flex items-center justify-center text-slate-500">
                Agent not found
              </div>
            )}
          </div>
        </main>
      </div>

      {/* Mobile menu (single entry point for actions) */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 z-50 sm:hidden animate-fade-in">
          <div
            className={isDark ? "absolute inset-0 bg-black/60" : "absolute inset-0 bg-black/40"}
            onClick={() => setMobileMenuOpen(false)}
            aria-hidden="true"
          />

          <div
            className={classNames(
              "absolute bottom-0 left-0 right-0 rounded-t-2xl border shadow-2xl animate-slide-up",
              isDark ? "bg-slate-900 border-slate-700" : "bg-white border-gray-200"
            )}
            role="dialog"
            aria-modal="true"
            aria-label="Menu"
          >
            <div className={classNames("px-4 pt-4 pb-3 border-b flex items-center justify-between gap-3", isDark ? "border-slate-800" : "border-gray-200")}>
              <div className="min-w-0">
                <div className={classNames("text-sm font-semibold truncate", isDark ? "text-slate-100" : "text-gray-900")}>
                  {groupDoc?.title || (selectedGroupId ? selectedGroupId : "Menu")}
                </div>
                {selectedGroupId && groupDoc && (
                  <div className={classNames("text-xs mt-0.5", isDark ? "text-slate-400" : "text-gray-500")}>
                    {(isDark ? getGroupStatus(selectedGroupRunning, groupDoc.state) : getGroupStatusLight(selectedGroupRunning, groupDoc.state)).label}
                  </div>
                )}
              </div>
              <button
                onClick={() => setMobileMenuOpen(false)}
                className={classNames(
                  "text-xl leading-none min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg transition-colors",
                  isDark ? "text-slate-400 hover:text-slate-200 hover:bg-slate-800" : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                )}
                aria-label="Close menu"
              >
                ×
              </button>
            </div>

            <div className="p-4 space-y-2 safe-area-inset-bottom">
              {!selectedGroupId && (
                <div className={classNames("text-sm px-1 pb-2", isDark ? "text-slate-400" : "text-gray-500")}>
                  Select a group to enable actions.
                </div>
              )}

              <button
                className={classNames(
                  "w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-50",
                  isDark ? "bg-slate-800/60 hover:bg-slate-800 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
                )}
                onClick={() => {
                  setMobileMenuOpen(false);
                  setShowSearchModal(true);
                }}
                disabled={!selectedGroupId}
              >
                <span aria-hidden="true">🔍</span>
                <span>Search Messages</span>
              </button>

              <div className={classNames("h-px my-2", isDark ? "bg-slate-800" : "bg-gray-200")} />

              <button
                className={classNames(
                  "w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-50",
                  isDark ? "bg-slate-800/60 hover:bg-slate-800 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
                )}
                onClick={() => {
                  setMobileMenuOpen(false);
                  setShowContextModal(true);
                }}
                disabled={!selectedGroupId}
              >
                <span aria-hidden="true">📋</span>
                <span>Context</span>
              </button>

              <button
                className={classNames(
                  "w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-50",
                  isDark ? "bg-slate-800/60 hover:bg-slate-800 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
                )}
                onClick={() => {
                  setMobileMenuOpen(false);
                  setShowSettingsModal(true);
                }}
                disabled={!selectedGroupId}
              >
                <span aria-hidden="true">⚙️</span>
                <span>Settings</span>
              </button>

              <button
                className={classNames(
                  "w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-50",
                  isDark ? "bg-slate-800/60 hover:bg-slate-800 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
                )}
                onClick={() => {
                  setMobileMenuOpen(false);
                  openGroupEdit();
                }}
                disabled={!selectedGroupId}
              >
                <span aria-hidden="true">✎</span>
                <span>Edit Group</span>
              </button>

              <div className={classNames("h-px my-2", isDark ? "bg-slate-800" : "bg-gray-200")} />

              <button
                className={classNames(
                  "w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-50",
                  isDark
                    ? "bg-emerald-600/20 border border-emerald-500/30 text-emerald-300 hover:bg-emerald-600/30"
                    : "bg-emerald-50 border border-emerald-200 text-emerald-700 hover:bg-emerald-100"
                )}
                onClick={() => {
                  setMobileMenuOpen(false);
                  void startGroup();
                }}
                disabled={!selectedGroupId || busy === "group-start" || actors.length === 0}
              >
                <span aria-hidden="true">▶</span>
                <span>Launch All</span>
              </button>

              <button
                className={classNames(
                  "w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-50",
                  isDark ? "bg-slate-800/60 hover:bg-slate-800 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
                )}
                onClick={() => {
                  setMobileMenuOpen(false);
                  void stopGroup();
                }}
                disabled={!selectedGroupId || busy === "group-stop"}
              >
                <span aria-hidden="true">⏹</span>
                <span>Quit All</span>
              </button>

              {groupDoc?.state === "paused" ? (
                <button
                  className={classNames(
                    "w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-50",
                    isDark
                      ? "bg-amber-600/20 border border-amber-500/30 text-amber-300 hover:bg-amber-600/30"
                      : "bg-amber-50 border border-amber-200 text-amber-700 hover:bg-amber-100"
                  )}
                  onClick={() => {
                    setMobileMenuOpen(false);
                    void setGroupState("active");
                  }}
                  disabled={!selectedGroupId || busy === "group-state"}
                >
                  <span aria-hidden="true">▶</span>
                  <span>Resume Delivery</span>
                </button>
              ) : (
                <button
                  className={classNames(
                    "w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-colors min-h-[44px] disabled:opacity-50",
                    isDark ? "bg-slate-800/60 hover:bg-slate-800 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
                  )}
                  onClick={() => {
                    setMobileMenuOpen(false);
                    void setGroupState("paused");
                  }}
                  disabled={!selectedGroupId || busy === "group-state"}
                >
                  <span aria-hidden="true">⏸</span>
                  <span>Pause Delivery</span>
                </button>
              )}
            </div>
          </div>
        </div>
      )}


      {/* Modals */}
      <SearchModal
        isOpen={showSearchModal}
        onClose={() => setShowSearchModal(false)}
        groupId={selectedGroupId}
        actors={actors}
        isDark={isDark}
        onReply={(ev) => {
          startReply(ev);
          setActiveTab("chat");
          setShowSearchModal(false);
          window.setTimeout(() => composerRef.current?.focus(), 0);
        }}
      />

      <ContextModal
        isOpen={showContextModal}
        onClose={() => setShowContextModal(false)}
        groupId={selectedGroupId}
        context={groupContext}
        onUpdateVision={updateVision}
        onUpdateSketch={updateSketch}
        busy={busy === "context-update"}
        isDark={isDark}
      />

      <SettingsModal
        isOpen={showSettingsModal}
        onClose={() => setShowSettingsModal(false)}
        settings={groupSettings}
        onUpdateSettings={updateSettings}
        busy={busy.startsWith("settings")}
        isDark={isDark}
        groupId={selectedGroupId}
      />

      {/* Inbox Modal */}
      {inboxOpen && (
        <div
          className={`fixed inset-0 backdrop-blur-sm flex items-start justify-center p-4 sm:p-6 z-50 animate-fade-in ${
            isDark ? "bg-black/50" : "bg-black/30"
          }`}
          onMouseDown={(e) => { if (e.target === e.currentTarget) setInboxOpen(false); }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="inbox-title"
        >
          <div className={`w-full max-w-2xl mt-8 sm:mt-16 rounded-xl border shadow-2xl animate-scale-in ${
            isDark 
              ? "border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900"
              : "border-gray-200 bg-white"
          }`}>
            <div className={`px-4 sm:px-6 py-4 border-b flex items-center justify-between gap-3 ${
              isDark ? "border-slate-700/50" : "border-gray-200"
            }`}>
              <div className="min-w-0">
                <div id="inbox-title" className={`text-lg font-semibold truncate ${isDark ? "text-white" : "text-gray-900"}`}>
                  Inbox · {inboxActorId}
                </div>
                <div className={`text-sm ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                  {inboxMessages.length} unread messages
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  className={`rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50 transition-colors min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700 hover:bg-slate-600 text-slate-200"
                      : "bg-gray-100 hover:bg-gray-200 text-gray-700"
                  }`}
                  onClick={markInboxAllRead}
                  disabled={!inboxMessages.length || busy.startsWith("inbox")}
                >
                  Mark all read
                </button>
                <button
                  className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors min-h-[44px] ${
                    isDark 
                      ? "bg-slate-600 hover:bg-slate-500 text-white"
                      : "bg-gray-200 hover:bg-gray-300 text-gray-800"
                  }`}
                  onClick={() => setInboxOpen(false)}
                >
                  Close
                </button>
              </div>
            </div>
            <div className="max-h-[60vh] overflow-auto p-4 space-y-2">
              {inboxMessages.map((ev, idx) => (
                <div key={String(ev.id || idx)} className={`rounded-lg border px-4 py-3 ${
                  isDark ? "border-slate-700/50 bg-slate-800/50" : "border-gray-200 bg-gray-50"
                }`}>
                  <div className="flex items-center justify-between gap-3">
                    <div className={`text-xs truncate ${isDark ? "text-slate-400" : "text-gray-500"}`} title={formatFullTime(ev.ts)}>
                      {formatTime(ev.ts)}
                    </div>
                    <div className={`text-xs font-medium truncate ${isDark ? "text-slate-300" : "text-gray-700"}`}>
                      {ev.by || "—"}
                    </div>
                  </div>
                  <div className={`mt-2 text-sm whitespace-pre-wrap break-words ${isDark ? "text-slate-200" : "text-gray-800"}`}>
                    {formatEventLine(ev)}
                  </div>
                </div>
              ))}
              {!inboxMessages.length && (
                <div className="text-center py-8">
                  <div className="text-3xl mb-2">📭</div>
                  <div className={`text-sm ${isDark ? "text-slate-400" : "text-gray-500"}`}>No unread messages</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Group Edit Modal */}
      {showGroupEdit && (
        <div
          className={`fixed inset-0 backdrop-blur-sm flex items-start justify-center p-4 sm:p-6 z-50 animate-fade-in ${
            isDark ? "bg-black/50" : "bg-black/30"
          }`}
          onMouseDown={(e) => { if (e.target === e.currentTarget) setShowGroupEdit(false); }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="group-edit-title"
        >
          <div className={`w-full max-w-md mt-8 sm:mt-16 rounded-xl border shadow-2xl animate-scale-in ${
            isDark 
              ? "border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900"
              : "border-gray-200 bg-white"
          }`}>
            <div className={`px-6 py-4 border-b ${isDark ? "border-slate-700/50" : "border-gray-200"}`}>
              <div id="group-edit-title" className={`text-lg font-semibold ${isDark ? "text-white" : "text-gray-900"}`}>
                Edit Group
              </div>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Name</label>
                <input
                  className={`w-full rounded-lg border px-4 py-2.5 text-sm min-h-[44px] transition-colors ${
                    isDark 
                      ? "bg-slate-900/80 border-slate-600/50 text-white focus:border-blue-500"
                      : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                  }`}
                  value={editGroupTitle}
                  onChange={(e) => setEditGroupTitle(e.target.value)}
                  placeholder="Group name"
                />
              </div>
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Description (optional)</label>
                <input
                  className={`w-full rounded-lg border px-4 py-2.5 text-sm min-h-[44px] transition-colors ${
                    isDark 
                      ? "bg-slate-900/80 border-slate-600/50 text-white focus:border-blue-500"
                      : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                  }`}
                  value={editGroupTopic}
                  onChange={(e) => setEditGroupTopic(e.target.value)}
                  placeholder="What is this group working on?"
                />
              </div>
              <div className="flex gap-3 pt-3 flex-wrap">
                <button
                  className="flex-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-white px-4 py-2.5 text-sm font-semibold shadow-lg disabled:opacity-50 transition-all min-h-[44px]"
                  onClick={updateGroup}
                  disabled={!editGroupTitle.trim() || busy === "group-update"}
                >
                  Save
                </button>
                <button
                  className={`px-4 py-2.5 rounded-lg text-sm font-medium transition-colors min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700 hover:bg-slate-600 text-slate-200"
                      : "bg-gray-100 hover:bg-gray-200 text-gray-700"
                  }`}
                  onClick={() => setShowGroupEdit(false)}
                >
                  Cancel
                </button>
                <button
                  className={`px-4 py-2.5 rounded-lg border text-sm font-medium disabled:opacity-50 transition-colors min-h-[44px] ${
                    isDark 
                      ? "bg-rose-500/20 border-rose-500/30 text-rose-400 hover:bg-rose-500/30"
                      : "bg-rose-50 border-rose-200 text-rose-600 hover:bg-rose-100"
                  }`}
                  onClick={() => { setShowGroupEdit(false); deleteGroup(); }}
                  disabled={busy === "group-delete"}
                  title="Delete this group permanently"
                >
                  Delete
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit Actor Modal */}
      {editingActor && (
        <div
          className={`fixed inset-0 flex items-start justify-center p-4 sm:p-6 z-50 animate-fade-in ${
            isDark ? "bg-black/60" : "bg-black/40"
          }`}
          onMouseDown={(e) => { if (e.target === e.currentTarget) setEditingActor(null); }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="edit-actor-title"
        >
          <div className={`w-full max-w-md mt-8 sm:mt-16 rounded-xl border shadow-2xl animate-scale-in ${
            isDark 
              ? "border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900"
              : "border-gray-200 bg-white"
          }`}>
            <div className={`px-6 py-4 border-b ${isDark ? "border-slate-700/50" : "border-gray-200"}`}>
              <div id="edit-actor-title" className={`text-lg font-semibold ${isDark ? "text-white" : "text-gray-900"}`}>
                Edit Agent: {editingActor.id}
              </div>
              <div className={`text-sm mt-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                Change the AI runtime for this agent
              </div>
            </div>
            <div className="p-6 space-y-5">
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Runtime</label>
                <select
                  className={`w-full rounded-lg border px-4 py-2.5 text-sm min-h-[44px] transition-colors ${
                    isDark 
                      ? "bg-slate-900/80 border-slate-600/50 text-white focus:border-blue-500"
                      : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                  }`}
                  value={editActorRuntime}
                  onChange={(e) => setEditActorRuntime(e.target.value as SupportedRuntime)}
                >
                  {SUPPORTED_RUNTIMES.map((rt) => {
                    const info = RUNTIME_INFO[rt];
                    const rtInfo = runtimes.find((r) => r.name === rt);
                    const available = rtInfo?.available ?? false;
                    return (
                      <option key={rt} value={rt} disabled={!available}>
                        {info?.label || rt}{!available ? " (not installed)" : ""}
                      </option>
                    );
                  })}
                </select>
                {(editActorRuntime === "opencode" || editActorRuntime === "copilot") && (
                  <div className={`mt-2 rounded-lg border px-3 py-2 text-[11px] ${
                    isDark ? "border-amber-500/30 bg-amber-500/10 text-amber-200" : "border-amber-200 bg-amber-50 text-amber-800"
                  }`}>
                    <div className="font-medium">Manual MCP install required</div>
                    {editActorRuntime === "opencode" ? (
                      <>
                        <div className="mt-1">
                          1) Create/edit{" "}
                          <code className={`px-1 rounded ${isDark ? "bg-amber-900/30" : "bg-amber-100"}`}>~/.config/opencode/opencode.json</code>
                        </div>
                        <div className="mt-1">2) Add this MCP server config:</div>
                      </>
                    ) : (
                      <>
                        <div className="mt-1">
                          1) Create/edit <code className={`px-1 rounded ${isDark ? "bg-amber-900/30" : "bg-amber-100"}`}>~/.copilot/mcp-config.json</code>
                        </div>
                        <div className="mt-1">2) Add this MCP server config (or pass it via <code className={`px-1 rounded ${isDark ? "bg-amber-900/30" : "bg-amber-100"}`}>--additional-mcp-config</code>):</div>
                      </>
                    )}
                    <pre className={`mt-1.5 p-2 rounded overflow-x-auto whitespace-pre ${isDark ? "bg-amber-900/20 text-amber-100" : "bg-amber-50 text-amber-900"}`}>
                      <code>{editActorRuntime === "opencode" ? OPENCODE_MCP_CONFIG_SNIPPET : COPILOT_MCP_CONFIG_SNIPPET}</code>
                    </pre>
                    <div className={`mt-1 text-[10px] ${isDark ? "text-amber-200/80" : "text-amber-800/80"}`}>
                      Restart the runtime after updating this config.
                    </div>
                  </div>
                )}
              </div>
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Command</label>
                <input
                  className={`w-full rounded-lg border px-4 py-2.5 text-sm font-mono min-h-[44px] transition-colors ${
                    isDark 
                      ? "bg-slate-900/80 border-slate-600/50 text-white focus:border-blue-500"
                      : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                  }`}
                  value={editActorCommand}
                  onChange={(e) => setEditActorCommand(e.target.value)}
                  placeholder={RUNTIME_DEFAULTS[editActorRuntime] || "Enter command..."}
                />
                <div className={`text-[10px] mt-1.5 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  Default: <code className={`px-1 rounded ${isDark ? "bg-slate-800" : "bg-gray-100"}`}>{RUNTIME_DEFAULTS[editActorRuntime] || ""}</code>
                </div>
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  className="flex-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-white px-4 py-2.5 text-sm font-semibold shadow-lg disabled:opacity-50 transition-all min-h-[44px]"
                  onClick={updateActor}
                  disabled={busy === "actor-update" || !editActorCommand.trim()}
                >
                  Save
                </button>
                <button
                  className={`px-4 py-2.5 rounded-lg text-sm font-medium transition-colors min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700 hover:bg-slate-600 text-slate-200"
                      : "bg-gray-100 hover:bg-gray-200 text-gray-700"
                  }`}
                  onClick={() => setEditingActor(null)}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Create Group Modal */}
      {showCreateGroup && (
        <div
          className={`fixed inset-0 backdrop-blur-sm flex items-start justify-center p-4 sm:p-6 z-50 animate-fade-in ${
            isDark ? "bg-black/50" : "bg-black/30"
          }`}
          onMouseDown={(e) => { if (e.target === e.currentTarget) setShowCreateGroup(false); }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-group-title"
        >
          <div className={`w-full max-w-lg mt-8 sm:mt-16 rounded-xl border shadow-2xl animate-scale-in ${
            isDark 
              ? "border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900"
              : "border-gray-200 bg-white"
          }`}>
            <div className={`px-6 py-4 border-b ${isDark ? "border-slate-700/50" : "border-gray-200"}`}>
              <div id="create-group-title" className={`text-lg font-semibold ${isDark ? "text-white" : "text-gray-900"}`}>
                Create Working Group
              </div>
              <div className={`text-sm mt-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                Select a project directory to start collaborating
              </div>
            </div>
            <div className="p-6 space-y-5">
              {dirSuggestions.length > 0 && !createGroupPath && (
                <div>
                  <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Quick Select</label>
                  <div className="grid grid-cols-2 gap-2">
                    {dirSuggestions.slice(0, 6).map((s) => (
                      <button
                        key={s.path}
                        className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors text-left min-h-[56px] ${
                          isDark 
                            ? "border-slate-600/50 bg-slate-800/50 hover:bg-slate-700/50 hover:border-slate-500"
                            : "border-gray-200 bg-gray-50 hover:bg-gray-100 hover:border-gray-300"
                        }`}
                        onClick={() => {
                          setCreateGroupPath(s.path);
                          setCreateGroupName(s.path.split("/").filter(Boolean).pop() || "");
                          fetchDirContents(s.path);
                        }}
                      >
                        <span className="text-lg">{s.icon}</span>
                        <div className="min-w-0">
                          <div className={`text-sm font-medium truncate ${isDark ? "text-slate-200" : "text-gray-700"}`}>{s.name}</div>
                          <div className={`text-[10px] truncate ${isDark ? "text-slate-500" : "text-gray-500"}`}>{s.path}</div>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Project Directory</label>
                <div className="flex gap-2">
                  <input
                    className={`flex-1 rounded-lg border px-4 py-2.5 text-sm font-mono min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-900/80 border-slate-600/50 text-white placeholder-slate-500 focus:border-blue-500"
                        : "bg-white border-gray-300 text-gray-900 placeholder-gray-400 focus:border-blue-500"
                    }`}
                    value={createGroupPath}
                    onChange={(e) => {
                      setCreateGroupPath(e.target.value);
                      const dirName = e.target.value.split("/").filter(Boolean).pop() || "";
                      if (!createGroupName || createGroupName === currentDir.split("/").filter(Boolean).pop()) {
                        setCreateGroupName(dirName);
                      }
                    }}
                    placeholder="/path/to/your/project"
                    autoFocus
                  />
                  <button
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors min-h-[44px] ${
                      isDark 
                        ? "bg-slate-700 hover:bg-slate-600 text-slate-200"
                        : "bg-gray-100 hover:bg-gray-200 text-gray-700"
                    }`}
                    onClick={() => fetchDirContents(createGroupPath || "~")}
                  >
                    Browse
                  </button>
                </div>
              </div>
              {showDirBrowser && (
                <div className={`border rounded-lg max-h-48 overflow-auto ${
                  isDark ? "border-slate-600/50 bg-slate-900/50" : "border-gray-200 bg-gray-50"
                }`}>
                  {currentDir && (
                    <div className={`px-3 py-1.5 border-b text-xs font-mono truncate ${
                      isDark ? "border-slate-700/30 bg-slate-800/30 text-slate-400" : "border-gray-200 bg-gray-100 text-gray-500"
                    }`}>
                      {currentDir}
                    </div>
                  )}
                  {parentDir && (
                    <button
                      className={`w-full flex items-center gap-2 px-3 py-2 text-left border-b min-h-[44px] ${
                        isDark ? "hover:bg-slate-800/50 border-slate-700/30" : "hover:bg-gray-100 border-gray-200"
                      }`}
                      onClick={() => {
                        fetchDirContents(parentDir);
                        setCreateGroupPath(parentDir);
                        setCreateGroupName(parentDir.split("/").filter(Boolean).pop() || "");
                      }}
                    >
                      <span className={isDark ? "text-slate-400" : "text-gray-400"}>📁</span>
                      <span className={`text-sm ${isDark ? "text-slate-400" : "text-gray-500"}`}>..</span>
                    </button>
                  )}
                  {dirItems.filter((d) => d.is_dir).length === 0 && (
                    <div className={`px-3 py-4 text-center text-sm ${isDark ? "text-slate-500" : "text-gray-500"}`}>No subdirectories</div>
                  )}
                  {dirItems.filter((d) => d.is_dir).map((item) => (
                    <button
                      key={item.path}
                      className={`w-full flex items-center gap-2 px-3 py-2 text-left min-h-[44px] ${
                        isDark ? "hover:bg-slate-800/50" : "hover:bg-gray-100"
                      }`}
                      onClick={() => {
                        setCreateGroupPath(item.path);
                        setCreateGroupName(item.name);
                        fetchDirContents(item.path);
                      }}
                    >
                      <span className="text-blue-500">📁</span>
                      <span className={`text-sm ${isDark ? "text-slate-200" : "text-gray-700"}`}>{item.name}</span>
                    </button>
                  ))}
                </div>
              )}
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Group Name</label>
                <input
                  className={`w-full rounded-lg border px-4 py-2.5 text-sm min-h-[44px] transition-colors ${
                    isDark 
                      ? "bg-slate-900/80 border-slate-600/50 text-white placeholder-slate-500 focus:border-blue-500"
                      : "bg-white border-gray-300 text-gray-900 placeholder-gray-400 focus:border-blue-500"
                  }`}
                  value={createGroupName}
                  onChange={(e) => setCreateGroupName(e.target.value)}
                  placeholder="Auto-filled from directory name"
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  className="flex-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-white px-4 py-2.5 text-sm font-semibold shadow-lg disabled:opacity-50 transition-all min-h-[44px]"
                  onClick={createGroup}
                  disabled={!createGroupPath.trim() || busy === "create"}
                >
                  {busy === "create" ? "Creating..." : "Create Group"}
                </button>
                <button
                  className={`px-4 py-2.5 rounded-lg text-sm font-medium transition-colors min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700 hover:bg-slate-600 text-slate-200"
                      : "bg-gray-100 hover:bg-gray-200 text-gray-700"
                  }`}
                  onClick={() => {
                    setShowCreateGroup(false);
                    setCreateGroupPath("");
                    setCreateGroupName("");
                    setDirItems([]);
                    setShowDirBrowser(false);
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Add Actor Modal */}
      {showAddActor && (
        <div
          className={`fixed inset-0 backdrop-blur-sm flex items-start justify-center p-4 sm:p-6 z-50 animate-fade-in ${
            isDark ? "bg-black/50" : "bg-black/30"
          }`}
          onMouseDown={(e) => { if (e.target === e.currentTarget) setShowAddActor(false); }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="add-actor-title"
        >
          <div className={`w-full max-w-lg mt-8 sm:mt-16 rounded-xl border shadow-2xl max-h-[80vh] overflow-y-auto animate-scale-in ${
            isDark 
              ? "border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900"
              : "border-gray-200 bg-white"
          }`}>
            <div className={`px-6 py-4 border-b sticky top-0 ${
              isDark ? "border-slate-700/50 bg-slate-800" : "border-gray-200 bg-white"
            }`}>
              <div id="add-actor-title" className={`text-lg font-semibold ${isDark ? "text-white" : "text-gray-900"}`}>Add AI Agent</div>
              <div className={`text-sm mt-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Choose an AI runtime to add to your team</div>
            </div>
            <div className="p-6 space-y-5">
              {addActorError && (
                <div className={`rounded-lg border px-4 py-2.5 text-sm flex items-center justify-between gap-3 ${
                  isDark 
                    ? "border-rose-500/30 bg-rose-500/10 text-rose-300"
                    : "border-rose-300 bg-rose-50 text-rose-700"
                }`} role="alert">
                  <span>{addActorError}</span>
                  <button className={isDark ? "text-rose-300 hover:text-rose-100" : "text-rose-500 hover:text-rose-700"} onClick={() => setAddActorError("")}>×</button>
                </div>
              )}
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                  Agent Name <span className={isDark ? "text-slate-500" : "text-gray-400"}>(supports 中文/日本語)</span>
                </label>
                <input
                  className={`w-full rounded-lg border px-4 py-2.5 text-sm min-h-[44px] transition-colors ${
                    isDark 
                      ? "bg-slate-900/80 border-slate-600/50 text-white placeholder-slate-500 focus:border-blue-500"
                      : "bg-white border-gray-300 text-gray-900 placeholder-gray-400 focus:border-blue-500"
                  }`}
                  value={newActorId}
                  onChange={(e) => setNewActorId(e.target.value)}
                  placeholder={suggestedActorId}
                />
                <div className={`text-[10px] mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  Leave empty to use: <code className={`px-1 rounded ${isDark ? "bg-slate-800" : "bg-gray-100"}`}>{suggestedActorId}</code>
                </div>
              </div>
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>AI Runtime</label>
                <div className="grid grid-cols-2 gap-2">
                  {SUPPORTED_RUNTIMES.map((rt) => {
                    const info = RUNTIME_INFO[rt] || { label: rt, desc: "" };
                    const rtInfo = runtimes.find((r) => r.name === rt);
                    const available = rtInfo?.available ?? false;
                    const isSelected = newActorRuntime === rt;
                    return (
                      <button
                        key={rt}
                        className={classNames(
                          "flex flex-col items-start px-3 py-2.5 rounded-lg border text-left transition-all min-h-[60px]",
                          isSelected
                            ? "bg-blue-600/20 border-blue-500 ring-1 ring-blue-500"
                            : available
                            ? isDark 
                              ? "bg-slate-800/50 border-slate-600/50 hover:border-slate-500 hover:bg-slate-700/50"
                              : "bg-gray-50 border-gray-200 hover:border-gray-300 hover:bg-gray-100"
                            : isDark
                              ? "bg-slate-900/30 border-slate-700/30 opacity-50 cursor-not-allowed"
                              : "bg-gray-100 border-gray-200 opacity-50 cursor-not-allowed"
                        )}
                        onClick={() => available && setNewActorRuntime(rt)}
                        disabled={!available}
                      >
                        <div className="flex items-center gap-2 w-full">
                          <span className={classNames(
                            "text-sm font-medium", 
                            isSelected ? "text-blue-400" : isDark ? "text-slate-200" : "text-gray-700"
                          )}>
                            {info.label}
                          </span>
                          {!available && (
                            <span className={`text-[9px] px-1.5 py-0.5 rounded ${isDark ? "bg-slate-700 text-slate-400" : "bg-gray-200 text-gray-500"}`}>
                              not installed
                            </span>
                          )}
                        </div>
                        {info.desc ? (
                          <div className={`text-[10px] mt-0.5 line-clamp-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>{info.desc}</div>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
                {(newActorRuntime === "opencode" || newActorRuntime === "copilot") && (
                  <div className={`mt-2 rounded-lg border px-3 py-2 text-[11px] ${
                    isDark ? "border-amber-500/30 bg-amber-500/10 text-amber-200" : "border-amber-200 bg-amber-50 text-amber-800"
                  }`}>
                    <div className="font-medium">Manual MCP install required</div>
                    {newActorRuntime === "opencode" ? (
                      <>
                        <div className="mt-1">
                          1) Create/edit{" "}
                          <code className={`px-1 rounded ${isDark ? "bg-amber-900/30" : "bg-amber-100"}`}>~/.config/opencode/opencode.json</code>
                        </div>
                        <div className="mt-1">2) Add this MCP server config:</div>
                      </>
                    ) : (
                      <>
                        <div className="mt-1">
                          1) Create/edit <code className={`px-1 rounded ${isDark ? "bg-amber-900/30" : "bg-amber-100"}`}>~/.copilot/mcp-config.json</code>
                        </div>
                        <div className="mt-1">2) Add this MCP server config (or pass it via <code className={`px-1 rounded ${isDark ? "bg-amber-900/30" : "bg-amber-100"}`}>--additional-mcp-config</code>):</div>
                      </>
                    )}
                    <pre className={`mt-1.5 p-2 rounded overflow-x-auto whitespace-pre ${isDark ? "bg-amber-900/20 text-amber-100" : "bg-amber-50 text-amber-900"}`}>
                      <code>{newActorRuntime === "opencode" ? OPENCODE_MCP_CONFIG_SNIPPET : COPILOT_MCP_CONFIG_SNIPPET}</code>
                    </pre>
                    <div className={`mt-1 text-[10px] ${isDark ? "text-amber-200/80" : "text-amber-800/80"}`}>
                      Restart the runtime after updating this config.
                    </div>
                  </div>
                )}
              </div>
              <div>
                <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Role</label>
                <div className="flex gap-2">
                  <button
                    className={classNames(
                      "flex-1 px-4 py-2.5 rounded-lg border text-sm font-medium transition-all min-h-[44px]",
                      newActorRole === "foreman"
                        ? "bg-amber-500/20 border-amber-500 text-amber-600"
                        : hasForeman
                        ? isDark ? "bg-slate-900/30 border-slate-700/30 text-slate-500 cursor-not-allowed" : "bg-gray-100 border-gray-200 text-gray-400 cursor-not-allowed"
                        : isDark ? "bg-slate-800/50 border-slate-600/50 text-slate-300 hover:border-slate-500" : "bg-gray-50 border-gray-200 text-gray-600 hover:border-gray-300"
                    )}
                    onClick={() => !hasForeman && setNewActorRole("foreman")}
                    disabled={hasForeman}
                  >
                    ★ Foreman {hasForeman && "(exists)"}
                  </button>
                  <button
                    className={classNames(
                      "flex-1 px-4 py-2.5 rounded-lg border text-sm font-medium transition-all min-h-[44px]",
                      newActorRole === "peer"
                        ? "bg-blue-500/20 border-blue-500 text-blue-600"
                        : !hasForeman
                        ? isDark ? "bg-slate-900/30 border-slate-700/30 text-slate-500 cursor-not-allowed" : "bg-gray-100 border-gray-200 text-gray-400 cursor-not-allowed"
                        : isDark ? "bg-slate-800/50 border-slate-600/50 text-slate-300 hover:border-slate-500" : "bg-gray-50 border-gray-200 text-gray-600 hover:border-gray-300"
                    )}
                    onClick={() => hasForeman && setNewActorRole("peer")}
                    disabled={!hasForeman}
                  >
                    Peer {!hasForeman && "(need foreman first)"}
                  </button>
                </div>
                <div className={`text-[10px] mt-1.5 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  {hasForeman ? "Foreman leads the team. Peers are worker agents." : "First agent must be the foreman (team leader)."}
                </div>
              </div>
              <button
                className={`flex items-center gap-2 text-xs min-h-[36px] ${isDark ? "text-slate-400 hover:text-slate-300" : "text-gray-500 hover:text-gray-700"}`}
                onClick={() => setShowAdvancedActor(!showAdvancedActor)}
              >
                <span className={classNames("transition-transform", showAdvancedActor && "rotate-90")}>▶</span>
                Advanced options
              </button>
              {showAdvancedActor && (
                <div className={`space-y-4 pl-4 border-l-2 ${isDark ? "border-slate-700/50" : "border-gray-200"}`}>
                  <div>
                    <label className={`block text-xs font-medium mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Command Override</label>
                    <input
                      className={`w-full rounded-lg border px-3 py-2 text-sm font-mono min-h-[44px] transition-colors ${
                        isDark 
                          ? "bg-slate-900/80 border-slate-600/50 text-white placeholder-slate-500 focus:border-blue-500"
                          : "bg-white border-gray-300 text-gray-900 placeholder-gray-400 focus:border-blue-500"
                      }`}
                      value={newActorCommand}
                      onChange={(e) => setNewActorCommand(e.target.value)}
                      placeholder={RUNTIME_DEFAULTS[newActorRuntime] || "Enter command..."}
                    />
                    <div className={`text-[10px] mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                      Default: <code className={`px-1 rounded ${isDark ? "bg-slate-800" : "bg-gray-100"}`}>{RUNTIME_DEFAULTS[newActorRuntime] || ""}</code>
                    </div>
                  </div>
                </div>
              )}
              <div className="flex gap-3 pt-2">
                <div className="flex-1">
                  <button
                    className="w-full rounded-lg bg-blue-600 hover:bg-blue-500 text-white px-4 py-2.5 text-sm font-semibold shadow-lg disabled:opacity-50 disabled:cursor-not-allowed transition-all min-h-[44px]"
                    onClick={addActor}
                    disabled={!canAddActor}
                  >
                    {busy === "actor-add" ? "Adding..." : "Add Agent"}
                  </button>
                  {addActorDisabledReason && (
                    <div className="text-[10px] text-amber-500 mt-1.5">{addActorDisabledReason}</div>
                  )}
                </div>
                <button
                  className={`px-4 py-2.5 rounded-lg text-sm font-medium transition-colors min-h-[44px] ${
                    isDark 
                      ? "bg-slate-700 hover:bg-slate-600 text-slate-200"
                      : "bg-gray-100 hover:bg-gray-200 text-gray-700"
                  }`}
                  onClick={() => {
                    setShowAddActor(false);
                    setNewActorId("");
                    setNewActorCommand("");
                    setNewActorRole("peer");
                    setShowAdvancedActor(false);
                    setAddActorError("");
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
