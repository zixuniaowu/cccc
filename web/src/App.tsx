import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiJson } from "./api";
import { TerminalModal } from "./TerminalModal";
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
  RUNTIME_COLORS,
  getRuntimeColor,
} from "./types";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

// ActorChip component with dropdown menu using Portal
function ActorChip({
  actor,
  isWorking,
  isIdle,
  unreadCount,
  isMenuOpen,
  rtInfo,
  onToggleMenu,
  onTerminal,
  onInbox,
  onEdit,
  onToggleEnabled,
  onRemove,
}: {
  actor: Actor;
  isWorking: boolean;
  isIdle: boolean;
  unreadCount: number;
  isMenuOpen: boolean;
  rtInfo: { label: string; desc: string } | undefined;
  onToggleMenu: () => void;
  onTerminal: () => void;
  onInbox: () => void;
  onEdit: () => void;
  onToggleEnabled: () => void;
  onRemove: () => void;
}) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [menuStyle, setMenuStyle] = useState<React.CSSProperties>({});
  
  // Use 'running' for actual process status, fallback to 'enabled' for backward compat
  const isRunning = actor.running ?? actor.enabled ?? false;

  // Calculate menu position when opened
  useEffect(() => {
    if (isMenuOpen && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      setMenuStyle({
        position: 'fixed',
        top: rect.bottom + 4,
        left: rect.left,
        zIndex: 99999,
      });
    }
  }, [isMenuOpen]);

  const menuContent = (
    <div
      style={menuStyle}
      className="w-44 rounded-lg border border-slate-600 shadow-2xl overflow-hidden"
      data-actor-menu
    >
      <div className="bg-slate-800">
        {actor.runner !== "headless" && isRunning && (
          <button
            className="w-full text-left px-3 py-2.5 text-xs hover:bg-slate-700 flex items-center gap-2 text-slate-200"
            onClick={onTerminal}
          >
            <span>💻</span> Terminal
          </button>
        )}
        <button
          className={classNames(
            "w-full text-left px-3 py-2.5 text-xs hover:bg-slate-700 flex items-center gap-2",
            unreadCount > 0 ? "text-rose-300" : "text-slate-200"
          )}
          onClick={onInbox}
        >
          <span>📥</span> Inbox {unreadCount > 0 && `(${unreadCount})`}
        </button>
        {!isRunning && (
          <button
            className="w-full text-left px-3 py-2.5 text-xs hover:bg-slate-700 flex items-center gap-2 text-slate-200"
            onClick={onEdit}
          >
            <span>✏️</span> Edit
          </button>
        )}
        <button
          className="w-full text-left px-3 py-2.5 text-xs hover:bg-slate-700 flex items-center gap-2 text-slate-200"
          onClick={onToggleEnabled}
        >
          <span>{isRunning ? "⏹" : "▶"}</span> {isRunning ? "Quit" : "Launch"}
        </button>
        <div className="border-t border-slate-700" />
        <button
          className="w-full text-left px-3 py-2.5 text-xs hover:bg-rose-500/30 text-rose-400 flex items-center gap-2"
          onClick={onRemove}
        >
          <span>🗑</span> Remove
        </button>
      </div>
    </div>
  );

  return (
    <div className="relative" data-actor-menu>
      <button
        ref={buttonRef}
        className={classNames(
          "flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs transition-all",
          isRunning
            ? `${getRuntimeColor(actor.runtime).border} ${getRuntimeColor(actor.runtime).bg} hover:brightness-110`
            : "border-slate-700/30 bg-slate-900/40 text-slate-400"
        )}
        onClick={(e) => {
          e.stopPropagation();
          onToggleMenu();
        }}
      >
        <span
          className={classNames(
            "w-2 h-2 rounded-full",
            !isRunning
              ? "bg-slate-600"
              : isWorking
              ? `${getRuntimeColor(actor.runtime).dot} animate-pulse`
              : isIdle
              ? "bg-amber-400"
              : getRuntimeColor(actor.runtime).dot
          )}
        />
        <span className={classNames("font-medium", isRunning ? getRuntimeColor(actor.runtime).text : "text-slate-400")}>
          {actor.id}
        </span>
        {actor.role === "foreman" && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-amber-900/50 text-amber-300 font-medium">foreman</span>
        )}
        {rtInfo && (
          <span className={classNames(
            "text-[9px] px-1.5 py-0.5 rounded",
            isRunning ? `${getRuntimeColor(actor.runtime).bg} ${getRuntimeColor(actor.runtime).text}` : "bg-slate-700/50 text-slate-400"
          )}>{rtInfo.label}</span>
        )}
        {unreadCount > 0 && (
          <span className="bg-rose-500 text-white text-[9px] px-1.5 py-0.5 rounded-full font-medium">
            {unreadCount}
          </span>
        )}
        <span className="text-slate-500 ml-0.5">▾</span>
      </button>
      {isMenuOpen && createPortal(menuContent, document.body)}
    </div>
  );
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
    
    // Within last minute
    if (diffSec < 60) return "just now";
    // Within last hour
    if (diffMin < 60) return `${diffMin}m ago`;
    // Within last 24 hours
    if (diffHour < 24) return `${diffHour}h ago`;
    // Within last 7 days
    if (diffDay < 7) return `${diffDay}d ago`;
    
    // Older: show date
    const month = date.toLocaleString("en", { month: "short" });
    const day = date.getDate();
    const year = date.getFullYear();
    const currentYear = now.getFullYear();
    
    if (year === currentYear) {
      return `${month} ${day}`;
    }
    return `${month} ${day}, ${year}`;
  } catch {
    return isoStr;
  }
}

// Format time for tooltip (full timestamp)
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
    // 只显示消息文本，发送者和收件人已经在头部显示了
    const text = String(ev.data.text || "");
    return text;
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

function getEventKindStyle(kind: string): string {
  if (kind === "chat.message") return "border-slate-800";
  if (kind === "system.notify") return "border-amber-900/50 bg-amber-950/20";
  if (kind === "chat.read") return "border-slate-800/50 opacity-60";
  if (kind.startsWith("actor.")) return "border-blue-900/50 bg-blue-950/20";
  if (kind.startsWith("group.")) return "border-purple-900/50 bg-purple-950/20";
  return "border-slate-800";
}

function getProjectRoot(group: GroupDoc | null): string {
  if (!group) return "";
  const key = String(group.active_scope_key || "");
  if (!key) return "";
  const scopes = Array.isArray(group.scopes) ? group.scopes : [];
  const hit = scopes.find((s) => String(s.scope_key || "") === key);
  return String(hit?.url || "");
}

export default function App() {
  const [groups, setGroups] = useState<GroupMeta[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string>("");
  const [groupDoc, setGroupDoc] = useState<GroupDoc | null>(null);
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [actors, setActors] = useState<Actor[]>([]);
  const [composerText, setComposerText] = useState("");
  const [toText, setToText] = useState("");
  const [attachPath, setAttachPath] = useState("");
  const [createTitle, setCreateTitle] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [showAddActor, setShowAddActor] = useState(false);
  const [newActorId, setNewActorId] = useState("");
  const [newActorRole, setNewActorRole] = useState<"peer" | "foreman">("peer");
  const [newActorRuntime, setNewActorRuntime] = useState<"claude" | "codex" | "droid" | "opencode" | "custom">("custom");
  const [newActorCommand, setNewActorCommand] = useState("");
  const [runtimes, setRuntimes] = useState<RuntimeInfo[]>([]);
  const [inboxOpen, setInboxOpen] = useState(false);
  const [inboxActorId, setInboxActorId] = useState("");
  const [inboxMessages, setInboxMessages] = useState<LedgerEvent[]>([]);
  const [termActorId, setTermActorId] = useState("");
  const [replyTarget, setReplyTarget] = useState<ReplyTarget>(null);
  const [showContext, setShowContext] = useState(false);
  const [groupContext, setGroupContext] = useState<GroupContext | null>(null);
  const [showGroupEdit, setShowGroupEdit] = useState(false);
  const [editGroupTitle, setEditGroupTitle] = useState("");
  const [editGroupTopic, setEditGroupTopic] = useState("");
  const [editingVision, setEditingVision] = useState(false);
  const [editingSketch, setEditingSketch] = useState(false);
  const [editVisionText, setEditVisionText] = useState("");
  const [editSketchText, setEditSketchText] = useState("");
  const [showMentionMenu, setShowMentionMenu] = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  const [mentionSelectedIndex, setMentionSelectedIndex] = useState(0);
  const [editingActor, setEditingActor] = useState<Actor | null>(null);
  const [editActorRuntime, setEditActorRuntime] = useState<"claude" | "codex" | "droid" | "opencode" | "custom">("custom");
  const [editActorCommand, setEditActorCommand] = useState("");
  const [editActorTitle, setEditActorTitle] = useState("");
  const [showScrollButton, setShowScrollButton] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [groupSettings, setGroupSettings] = useState<GroupSettings | null>(null);
  const [editNudgeSeconds, setEditNudgeSeconds] = useState(300);
  const [editActorIdleSeconds, setEditActorIdleSeconds] = useState(600);
  const [editKeepaliveSeconds, setEditKeepaliveSeconds] = useState(120);
  const [editSilenceSeconds, setEditSilenceSeconds] = useState(600);
  const [editDeliveryInterval, setEditDeliveryInterval] = useState(60);
  const [showCreateGroup, setShowCreateGroup] = useState(false);
  const [createGroupPath, setCreateGroupPath] = useState("");
  const [createGroupName, setCreateGroupName] = useState("");
  const [actorMenuOpen, setActorMenuOpen] = useState<string | null>(null);
  // Directory picker state
  const [dirItems, setDirItems] = useState<DirItem[]>([]);
  const [dirSuggestions, setDirSuggestions] = useState<DirSuggestion[]>([]);
  const [currentDir, setCurrentDir] = useState("");
  const [parentDir, setParentDir] = useState<string | null>(null);
  const [showDirBrowser, setShowDirBrowser] = useState(false);
  const [showAdvancedActor, setShowAdvancedActor] = useState(false);
  const [addActorError, setAddActorError] = useState("");  // Error specific to Add Actor modal
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const errorTimeoutRef = useRef<number | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const eventContainerRef = useRef<HTMLDivElement | null>(null);

  // Show error with auto-dismiss after 8 seconds
  const showError = (msg: string) => {
    if (errorTimeoutRef.current) {
      window.clearTimeout(errorTimeoutRef.current);
    }
    setErrorMsg(msg);
    errorTimeoutRef.current = window.setTimeout(() => {
      setErrorMsg("");
      errorTimeoutRef.current = null;
    }, 8000);
  };

  const projectRoot = useMemo(() => getProjectRoot(groupDoc), [groupDoc]);
  
  // Check if foreman already exists
  const hasForeman = useMemo(() => {
    return actors.some((a) => a.role === "foreman");
  }, [actors]);

  // Generate suggested actor ID: agent-1, agent-2, etc.
  const suggestedActorId = useMemo(() => {
    const existing = new Set(actors.map((a) => String(a.id || "")));
    for (let i = 1; i <= 999; i++) {
      const candidate = `agent-${i}`;
      if (!existing.has(candidate)) return candidate;
    }
    return `agent-${Date.now()}`;
  }, [actors]);

  // Check if we can add an actor with current settings
  const canAddActor = useMemo(() => {
    if (busy === "actor-add") return false;
    // Custom runtime requires a command
    if (newActorRuntime === "custom" && !newActorCommand.trim()) return false;
    // Non-custom runtime: check if it's available or has a command override
    if (newActorRuntime !== "custom") {
      const rtInfo = runtimes.find((r) => r.name === newActorRuntime);
      const available = rtInfo?.available ?? false;
      // If runtime not available and no command override, can't add
      if (!available && !newActorCommand.trim()) return false;
    }
    return true;
  }, [busy, newActorRuntime, newActorCommand, runtimes]);

  // Get reason why Add Agent button is disabled
  const addActorDisabledReason = useMemo(() => {
    if (busy === "actor-add") return "";
    if (newActorRuntime === "custom" && !newActorCommand.trim()) {
      return "Enter a command for custom runtime";
    }
    if (newActorRuntime !== "custom") {
      const rtInfo = runtimes.find((r) => r.name === newActorRuntime);
      const available = rtInfo?.available ?? false;
      if (!available && !newActorCommand.trim()) {
        return `${RUNTIME_INFO[newActorRuntime]?.label || newActorRuntime} is not installed. Install it or enter a custom command.`;
      }
    }
    return "";
  }, [busy, newActorRuntime, newActorCommand, runtimes]);

  const termActorTitle = useMemo(() => {
    const aid = termActorId.trim();
    if (!aid) return "";
    const hit = actors.find((a) => String(a.id || "") === aid);
    return String(hit?.title || "");
  }, [actors, termActorId]);

  const toTokens = useMemo(() => {
    return toText
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t.length > 0);
  }, [toText]);

  // Mention suggestions for @autocomplete
  const mentionSuggestions = useMemo(() => {
    const base = ["@all", "@foreman", "@peers", "user"];
    const actorIds = actors.map((a) => String(a.id || "")).filter((id) => id);
    const all = [...base, ...actorIds];
    if (!mentionFilter) return all;
    const lower = mentionFilter.toLowerCase();
    return all.filter((s) => s.toLowerCase().includes(lower));
  }, [actors, mentionFilter]);

  function toggleRecipient(token: string) {
    const t = token.trim();
    if (!t) return;
    const norm = (x: string) => x.trim();
    const cur = toTokens.map(norm);
    const idx = cur.findIndex((x) => x === t);
    if (idx >= 0) {
      const next = cur.slice(0, idx).concat(cur.slice(idx + 1));
      setToText(next.join(", "));
    } else {
      setToText(cur.concat([t]).join(", "));
    }
  }

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
      // Auto-select first available runtime as default
      const available = resp.result.runtimes?.filter((r) => r.available) || [];
      if (available.length > 0 && newActorRuntime === "custom") {
        const first = available[0];
        if (first.name === "claude" || first.name === "codex" || first.name === "droid" || first.name === "opencode") {
          setNewActorRuntime(first.name);
        }
      }
    }
  }

  async function fetchDirSuggestions() {
    const resp = await apiJson<{ suggestions: DirSuggestion[] }>("/api/v1/fs/recent");
    if (resp.ok) {
      setDirSuggestions(resp.result.suggestions || []);
    }
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
      console.error("fetchDirContents error:", resp.error);
      showError(resp.error?.message || "Failed to list directory");
    }
  }

  async function fetchContext(groupId: string) {
    const resp = await apiJson<{ context: GroupContext }>(`/api/v1/groups/${encodeURIComponent(groupId)}/context`);
    if (resp.ok) {
      setGroupContext(resp.result.context || null);
    }
  }

  async function fetchSettings(groupId: string) {
    const resp = await apiJson<{ settings: GroupSettings }>(`/api/v1/groups/${encodeURIComponent(groupId)}/settings`);
    if (resp.ok && resp.result.settings) {
      setGroupSettings(resp.result.settings);
      setEditNudgeSeconds(resp.result.settings.nudge_after_seconds);
      setEditActorIdleSeconds(resp.result.settings.actor_idle_timeout_seconds);
      setEditKeepaliveSeconds(resp.result.settings.keepalive_delay_seconds);
      setEditSilenceSeconds(resp.result.settings.silence_timeout_seconds);
      setEditDeliveryInterval(resp.result.settings.min_interval_seconds);
    }
  }

  async function updateSettings() {
    if (!selectedGroupId) return;
    setBusy("settings-update");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/settings`, {
        method: "PUT",
        body: JSON.stringify({
          nudge_after_seconds: editNudgeSeconds,
          actor_idle_timeout_seconds: editActorIdleSeconds,
          keepalive_delay_seconds: editKeepaliveSeconds,
          silence_timeout_seconds: editSilenceSeconds,
          min_interval_seconds: editDeliveryInterval,
          by: "user",
        }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setShowSettings(false);
      await fetchSettings(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  async function updateVision() {
    if (!selectedGroupId) return;
    setBusy("context-update");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/context`, {
        method: "POST",
        body: JSON.stringify({ ops: [{ op: "vision.update", vision: editVisionText }], by: "user" }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setEditingVision(false);
      await fetchContext(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  async function updateSketch() {
    if (!selectedGroupId) return;
    setBusy("context-update");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/context`, {
        method: "POST",
        body: JSON.stringify({ ops: [{ op: "sketch.update", sketch: editSketchText }], by: "user" }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setEditingSketch(false);
      await fetchContext(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  async function loadGroup(groupId: string) {
    setGroupDoc(null);
    setEvents([]);
    setActors([]);
    setGroupContext(null);
    setGroupSettings(null);
    setErrorMsg("");

    const show = await apiJson<{ group: GroupDoc }>(`/api/v1/groups/${encodeURIComponent(groupId)}`);
    if (show.ok) setGroupDoc(show.result.group);

    const tail = await apiJson<{ events: LedgerEvent[] }>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/tail?lines=120&with_read_status=true`,
    );
    if (tail.ok) setEvents(tail.result.events || []);

    const a = await apiJson<{ actors: Actor[] }>(`/api/v1/groups/${encodeURIComponent(groupId)}/actors?include_unread=true`);
    if (a.ok) setActors(a.result.actors || []);

    // Fetch context and settings
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
        setEvents((prev) => prev.concat([ev]));
        // Refresh actors on new chat messages to update unread counts
        if (ev.kind === "chat.message" || ev.kind === "chat.read") {
          refreshActors();
        }
      } catch {
        // ignore
      }
    });
    eventSourceRef.current = es;
  }

  useEffect(() => {
    refreshGroups();
    fetchRuntimes();
    fetchDirSuggestions();
    const t = window.setInterval(refreshGroups, 5000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // Small delay to ensure DOM is rendered before scrolling
    const timer = setTimeout(() => {
      bottomRef.current?.scrollIntoView({ block: "end" });
    }, 50);
    return () => clearTimeout(timer);
  }, [events.length]);

  // Handle scroll to detect if user scrolled up
  const handleScroll = () => {
    const container = eventContainerRef.current;
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;
    setShowScrollButton(!isNearBottom);
  };

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  };

  // Auto-fill command when runtime changes (for new actor form)
  useEffect(() => {
    if (newActorRuntime !== "custom" && RUNTIME_DEFAULTS[newActorRuntime]) {
      setNewActorCommand(RUNTIME_DEFAULTS[newActorRuntime]);
    }
  }, [newActorRuntime]);

  // Auto-fill command when runtime changes (for edit actor form)
  useEffect(() => {
    if (editActorRuntime !== "custom" && RUNTIME_DEFAULTS[editActorRuntime]) {
      setEditActorCommand(RUNTIME_DEFAULTS[editActorRuntime]);
    }
  }, [editActorRuntime]);

  // Close actor menu when clicking outside
  useEffect(() => {
    if (!actorMenuOpen) return;
    const handleClick = (e: MouseEvent) => {
      // Close menu if click is outside the menu
      const target = e.target as HTMLElement;
      if (!target.closest('[data-actor-menu]')) {
        setActorMenuOpen(null);
      }
    };
    document.addEventListener('click', handleClick);
    return () => document.removeEventListener('click', handleClick);
  }, [actorMenuOpen]);

  async function sendMessage() {
    const txt = composerText.trim();
    if (!txt || !selectedGroupId) return;
    setBusy("send");
    try {
      setErrorMsg("");
      const to = toTokens;
      
      let resp;
      if (replyTarget) {
        // Reply to a message
        resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/reply`, {
          method: "POST",
          body: JSON.stringify({ 
            text: txt, 
            by: "user", 
            to: to.length ? to : [replyTarget.by],
            reply_to: replyTarget.eventId,
          }),
        });
      } else {
        // Send new message
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
      // Don't reload - SSE stream will push the new event
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

  function cancelReply() {
    setReplyTarget(null);
  }

  async function attachRoot() {
    const p = attachPath.trim();
    if (!p || !selectedGroupId) return;
    setBusy("attach");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/attach`, {
        method: "POST",
        body: JSON.stringify({ path: p, by: "user" }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await loadGroup(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  async function createGroup() {
    // New flow: create group with path, auto-generate title from directory name
    const path = createGroupPath.trim();
    if (!path) return;
    
    // Extract directory name for default title
    const dirName = path.split("/").filter(Boolean).pop() || "working-group";
    const title = createGroupName.trim() || dirName;
    
    setBusy("create");
    try {
      setErrorMsg("");
      // Create group
      const resp = await apiJson<{ group_id: string }>("/api/v1/groups", {
        method: "POST",
        body: JSON.stringify({ title, topic: "", by: "user" }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      
      const groupId = resp.result.group_id;
      
      // Attach the path as scope
      const attachResp = await apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/attach`, {
        method: "POST",
        body: JSON.stringify({ path, by: "user" }),
      });
      if (!attachResp.ok) {
        showError(`Created group but failed to attach: ${attachResp.error.message}`);
      }
      
      // Reset and close modal
      setCreateGroupPath("");
      setCreateGroupName("");
      setShowCreateGroup(false);
      await refreshGroups();
      setSelectedGroupId(groupId);
    } finally {
      setBusy("");
    }
  }

  // Legacy inline create (keep for backward compat, but hidden)
  async function createGroupLegacy() {
    const title = createTitle.trim();
    if (!title) return;
    setBusy("create");
    try {
      setErrorMsg("");
      const resp = await apiJson<{ group_id: string }>("/api/v1/groups", {
        method: "POST",
        body: JSON.stringify({ title, topic: "", by: "user" }),
      });
      if (resp.ok) {
        setCreateTitle("");
        await refreshGroups();
        if (resp.result.group_id) {
          setSelectedGroupId(resp.result.group_id);
        }
      } else {
        showError(`${resp.error.code}: ${resp.error.message}`);
      }
    } finally {
      setBusy("");
    }
  }

  async function refreshActors() {
    if (!selectedGroupId) return;
    const a = await apiJson<{ actors: Actor[] }>(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors?include_unread=true`);
    if (a.ok) {
      setActors(a.result.actors || []);
    }
  }

  async function addActor() {
    if (!selectedGroupId) return;
    const actorId = newActorId.trim() || suggestedActorId;
    if (!actorId) return;
    setBusy("actor-add");
    setAddActorError("");  // Clear previous error
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
      setNewActorCommand("");
      setNewActorRole("peer");
      setNewActorRuntime("custom");
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
    // Use 'running' for actual process status, fallback to 'enabled'
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
      await refreshActors();
      await loadGroup(selectedGroupId);
    } finally {
      setBusy("");
    }
  }

  function openEditActor(actor: Actor) {
    // Only allow editing stopped actors (check running status, not enabled)
    const isRunning = actor.running ?? actor.enabled ?? false;
    if (isRunning) {
      showError("Stop the actor before editing. Use stop → edit → start workflow.");
      return;
    }
    setEditingActor(actor);
    const rt = actor.runtime as typeof editActorRuntime || "custom";
    setEditActorRuntime(rt);
    // Set command from actor, or use runtime default
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
      // Only refresh actors, not groups (to avoid focus jump)
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
      // Only refresh actors, not groups (to avoid focus jump)
      await refreshActors();
    } finally {
      setBusy("");
    }
  }

  function openGroupEdit() {
    if (!groupDoc) return;
    setEditGroupTitle(groupDoc.title || "");
    setEditGroupTopic(groupDoc.topic || "");
    setShowGroupEdit(true);
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
      // Clear all state for the deleted group
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
        `/api/v1/groups/${encodeURIComponent(selectedGroupId)}/inbox/${encodeURIComponent(aid)}?by=user&limit=200`,
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

  return (
    <div className="h-full w-full bg-gradient-to-br from-slate-900 via-slate-900 to-slate-800">
      <div className="h-full grid grid-cols-[280px_1fr]">
        {/* Sidebar */}
        <aside className="h-full border-r border-slate-700/50 bg-slate-900/80 backdrop-blur flex flex-col">
          <div className="p-4 border-b border-slate-700/50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-lg">🤖</span>
                <span className="text-sm font-bold text-white tracking-wide">CCCC</span>
              </div>
              <button
                className="text-xs px-3 py-1.5 rounded-lg bg-gradient-to-r from-blue-600 to-blue-500 text-white font-medium hover:from-blue-500 hover:to-blue-400 shadow-lg shadow-blue-500/20 transition-all"
                onClick={() => {
                  setShowCreateGroup(true);
                  fetchDirSuggestions();
                }}
                title="Create new working group"
              >
                + New
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-auto p-3">
            <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-2 px-2">Working Groups</div>
            {groups.map((g) => {
              const gid = String(g.group_id || "");
              const active = gid === selectedGroupId;
              return (
                <button
                  key={gid}
                  className={classNames(
                    "w-full text-left px-3 py-2.5 rounded-lg mb-1 transition-all",
                    active 
                      ? "bg-gradient-to-r from-blue-600/20 to-blue-500/10 border border-blue-500/30" 
                      : "hover:bg-slate-800/50 border border-transparent",
                  )}
                  onClick={() => setSelectedGroupId(gid)}
                >
                  <div className="flex items-center justify-between">
                    <div className={classNames("text-sm font-medium truncate", active ? "text-white" : "text-slate-300")}>{g.title || gid}</div>
                    <div className={classNames(
                      "text-[9px] px-2 py-0.5 rounded-full font-medium",
                      g.running ? "bg-emerald-500/20 text-emerald-400" : "bg-slate-700/50 text-slate-500"
                    )}>
                      {g.running ? "● RUN" : "○ STOP"}
                    </div>
                  </div>
                </button>
              );
            })}
            {!groups.length && (
              <div className="p-6 text-center">
                <div className="text-4xl mb-3">📁</div>
                <div className="text-sm text-slate-400 mb-2">No working groups yet</div>
                <div className="text-xs text-slate-500 mb-4 max-w-[200px] mx-auto">
                  A working group is a collaboration space where multiple AI agents work together on a project.
                </div>
                <button
                  className="text-sm px-4 py-2 rounded-lg bg-gradient-to-r from-blue-600 to-blue-500 text-white font-medium hover:from-blue-500 hover:to-blue-400 shadow-lg shadow-blue-500/20"
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

        <main className="h-full flex flex-col bg-slate-900/50 overflow-hidden">
          {/* Header */}
          <header className="flex-shrink-0 border-b border-slate-700/50 bg-slate-800/30 backdrop-blur px-5 py-4">
            {/* Row 1: Group info + actions */}
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3 min-w-0">
                <div className="text-base font-semibold text-white truncate">
                  {groupDoc?.title || (selectedGroupId ? selectedGroupId : "Select a group")}
                </div>
                {selectedGroupId && (
                  <button
                    className="text-xs px-2 py-1 rounded-md bg-slate-700/50 border border-slate-600/50 text-slate-400 hover:text-white hover:bg-slate-600/50 transition-colors"
                    onClick={openGroupEdit}
                    title="Edit group"
                  >
                    ✎ Edit
                  </button>
                )}
                {projectRoot && (
                  <span className="text-xs text-slate-400 truncate max-w-[200px] bg-slate-800/50 px-2 py-1 rounded" title={projectRoot}>
                    📁 {projectRoot.split("/").pop()}
                  </span>
                )}
              </div>

              <div className="flex gap-2 items-center">
                <button
                  className="rounded-lg bg-gradient-to-r from-emerald-600 to-emerald-500 text-white px-4 py-1.5 text-sm font-medium disabled:opacity-50 hover:from-emerald-500 hover:to-emerald-400 shadow-lg shadow-emerald-500/20 transition-all"
                  onClick={startGroup}
                  disabled={!selectedGroupId || busy === "group-start" || actors.length === 0}
                  title="Launch all agent processes"
                >
                  ▶ Launch All
                </button>
                <button
                  className="rounded-lg bg-slate-700/80 text-slate-200 px-4 py-1.5 text-sm font-medium disabled:opacity-50 hover:bg-slate-600 transition-colors"
                  onClick={stopGroup}
                  disabled={!selectedGroupId || busy === "group-stop"}
                  title="Quit all agent processes"
                >
                  ⏹ Quit All
                </button>
                <div className="w-px h-6 bg-slate-700/50 mx-1" />
                <button
                  className={classNames(
                    "rounded-lg px-4 py-1.5 text-sm font-medium transition-all",
                    showContext 
                      ? "bg-blue-600 text-white shadow-lg shadow-blue-500/20" 
                      : "bg-slate-700/80 text-slate-200 hover:bg-slate-600"
                  )}
                  onClick={() => setShowContext((v) => !v)}
                  disabled={!selectedGroupId}
                  title="View/edit context"
                >
                  📋 Context
                </button>
                <button
                  className="rounded-lg bg-slate-700/80 text-slate-200 px-3 py-1.5 text-sm font-medium hover:bg-slate-600 disabled:opacity-50 transition-colors"
                  onClick={() => setShowSettings(true)}
                  disabled={!selectedGroupId}
                  title="Settings"
                >
                  ⚙️
                </button>
              </div>
            </div>

            {/* Error message */}
            {errorMsg && (
              <div className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-300 flex items-center justify-between gap-3">
                <span>{errorMsg}</span>
                <button className="text-rose-300 hover:text-rose-100" onClick={() => setErrorMsg("")}>×</button>
              </div>
            )}

            {/* Actors row */}
            <div className="mt-4 flex items-center gap-2 flex-wrap">
              <span className="text-xs font-medium text-slate-400 mr-1">Agents:</span>
              {actors.length === 0 && selectedGroupId && (
                <span className="text-xs text-slate-500 italic">No agents yet — add one to get started</span>
              )}
              {actors.map((a) => {
                const presence = groupContext?.presence?.[a.id];
                const isWorking = presence?.status === "working";
                const isIdle = presence?.status === "idle";
                const unreadCount = a.unread_count || 0;
                const isMenuOpen = actorMenuOpen === a.id;
                const rtInfo = RUNTIME_INFO[a.runtime || "custom"];
                
                return (
                  <ActorChip
                    key={a.id}
                    actor={a}
                    isWorking={isWorking}
                    isIdle={isIdle}
                    unreadCount={unreadCount}
                    isMenuOpen={isMenuOpen}
                    rtInfo={rtInfo}
                    onToggleMenu={() => setActorMenuOpen(isMenuOpen ? null : a.id)}
                    onTerminal={() => { setTermActorId(a.id); setActorMenuOpen(null); }}
                    onInbox={() => { openInbox(a.id); setActorMenuOpen(null); }}
                    onEdit={() => { openEditActor(a); setActorMenuOpen(null); }}
                    onToggleEnabled={() => { toggleActorEnabled(a); setActorMenuOpen(null); }}
                    onRemove={() => { removeActor(a); setActorMenuOpen(null); }}
                  />
                );
              })}
              <button
                className="text-xs px-3 py-1.5 rounded-lg bg-blue-600/20 border border-blue-500/30 text-blue-400 hover:bg-blue-600/30 hover:border-blue-500/50 disabled:opacity-50 transition-all font-medium"
                onClick={() => {
                  // First agent must be foreman
                  setNewActorRole(hasForeman ? "peer" : "foreman");
                  setShowAddActor(true);
                }}
                disabled={!selectedGroupId}
              >
                + Add Agent
              </button>
            </div>

            {showContext && groupContext && (
              <div className="mt-3 border border-slate-800 rounded bg-slate-950/40 p-3">
                <div className="grid grid-cols-2 gap-4">
                  {/* Vision */}
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <div className="text-xs font-semibold text-slate-400">Vision</div>
                      {!editingVision && (
                        <button
                          className="text-[9px] px-1 py-0.5 rounded bg-slate-800 text-slate-400 hover:text-slate-200"
                          onClick={() => {
                            setEditVisionText(groupContext.vision || "");
                            setEditingVision(true);
                          }}
                        >
                          edit
                        </button>
                      )}
                    </div>
                    {editingVision ? (
                      <div className="space-y-1">
                        <textarea
                          className="w-full rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm resize-none"
                          rows={3}
                          value={editVisionText}
                          onChange={(e) => setEditVisionText(e.target.value)}
                          placeholder="What is the end goal?"
                        />
                        <div className="flex gap-1">
                          <button
                            className="text-[10px] px-2 py-0.5 rounded bg-slate-200 text-slate-950 disabled:opacity-50"
                            onClick={updateVision}
                            disabled={busy === "context-update"}
                          >
                            Save
                          </button>
                          <button
                            className="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-200"
                            onClick={() => setEditingVision(false)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="text-sm text-slate-200 whitespace-pre-wrap">
                        {groupContext.vision || <span className="text-slate-500 italic">Not set</span>}
                      </div>
                    )}
                  </div>
                  {/* Sketch */}
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <div className="text-xs font-semibold text-slate-400">Sketch</div>
                      {!editingSketch && (
                        <button
                          className="text-[9px] px-1 py-0.5 rounded bg-slate-800 text-slate-400 hover:text-slate-200"
                          onClick={() => {
                            setEditSketchText(groupContext.sketch || "");
                            setEditingSketch(true);
                          }}
                        >
                          edit
                        </button>
                      )}
                    </div>
                    {editingSketch ? (
                      <div className="space-y-1">
                        <textarea
                          className="w-full rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm resize-none"
                          rows={3}
                          value={editSketchText}
                          onChange={(e) => setEditSketchText(e.target.value)}
                          placeholder="High-level approach or architecture"
                        />
                        <div className="flex gap-1">
                          <button
                            className="text-[10px] px-2 py-0.5 rounded bg-slate-200 text-slate-950 disabled:opacity-50"
                            onClick={updateSketch}
                            disabled={busy === "context-update"}
                          >
                            Save
                          </button>
                          <button
                            className="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-200"
                            onClick={() => setEditingSketch(false)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="text-sm text-slate-200 whitespace-pre-wrap">
                        {groupContext.sketch || <span className="text-slate-500 italic">Not set</span>}
                      </div>
                    )}
                  </div>
                </div>

                {/* Milestones */}
                {groupContext.milestones && groupContext.milestones.length > 0 && (
                  <div className="mt-3">
                    <div className="text-xs font-semibold text-slate-400 mb-1">Milestones</div>
                    <div className="space-y-1">
                      {groupContext.milestones.map((m) => (
                        <div key={m.id} className="flex items-center gap-2 text-sm">
                          <span className={classNames(
                            "text-[10px] px-1.5 py-0.5 rounded",
                            m.status === "done" ? "bg-emerald-900/40 text-emerald-300" :
                            m.status === "in_progress" ? "bg-blue-900/40 text-blue-300" :
                            "bg-slate-800 text-slate-400"
                          )}>
                            {m.status || "pending"}
                          </span>
                          <span className="text-slate-200">{m.title}</span>
                          {m.due && <span className="text-xs text-slate-500">due: {m.due}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Tasks */}
                {groupContext.tasks && groupContext.tasks.length > 0 && (
                  <div className="mt-3">
                    <div className="text-xs font-semibold text-slate-400 mb-1">Tasks</div>
                    <div className="space-y-1">
                      {groupContext.tasks.slice(0, 10).map((t) => (
                        <div key={t.id} className="flex items-center gap-2 text-sm">
                          <span className={classNames(
                            "text-[10px] px-1.5 py-0.5 rounded",
                            t.status === "done" ? "bg-emerald-900/40 text-emerald-300" :
                            t.status === "in_progress" ? "bg-blue-900/40 text-blue-300" :
                            "bg-slate-800 text-slate-400"
                          )}>
                            {t.status || "todo"}
                          </span>
                          <span className="text-slate-200 truncate">{t.title}</span>
                          {t.assignee && <span className="text-xs text-slate-500">@{t.assignee}</span>}
                        </div>
                      ))}
                      {groupContext.tasks.length > 10 && (
                        <div className="text-xs text-slate-500">+{groupContext.tasks.length - 10} more tasks</div>
                      )}
                    </div>
                  </div>
                )}

                {/* Presence */}
                {groupContext.presence && Object.keys(groupContext.presence).length > 0 && (
                  <div className="mt-3">
                    <div className="text-xs font-semibold text-slate-400 mb-1">Presence</div>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(groupContext.presence).map(([actorId, p]) => (
                        <div key={actorId} className="flex items-center gap-1.5 text-xs bg-slate-900 rounded px-2 py-1">
                          <span className={classNames(
                            "h-2 w-2 rounded-full",
                            p.status === "working" ? "bg-emerald-400" :
                            p.status === "idle" ? "bg-amber-400" :
                            "bg-slate-600"
                          )} />
                          <span className="text-slate-200">{actorId}</span>
                          {p.activity && <span className="text-slate-500 truncate max-w-[120px]">{p.activity}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Empty state */}
                {!groupContext.vision && !groupContext.sketch && 
                 (!groupContext.milestones || groupContext.milestones.length === 0) &&
                 (!groupContext.tasks || groupContext.tasks.length === 0) && (
                  <div className="text-sm text-slate-500 italic">
                    No context set yet. Agents can update vision, sketch, milestones, and tasks via MCP tools.
                  </div>
                )}
              </div>
            )}

            {showContext && !groupContext && (
              <div className="mt-3 border border-slate-800 rounded bg-slate-950/40 p-3 text-sm text-slate-500 italic">
                Loading context...
              </div>
            )}
          </header>

          <section 
            ref={eventContainerRef}
            className="flex-1 min-h-0 overflow-auto px-4 py-3 relative"
            onScroll={handleScroll}
          >
            <div className="space-y-2">
              {events
                .filter((ev) => {
                  // Only show chat messages in the main conversation view
                  // System notifications are too noisy - they go to actor inbox instead
                  const kind = ev.kind || "";
                  return kind === "chat.message";
                })
                .map((ev, idx) => {
                const isMessage = ev.kind === "chat.message";
                const isNotify = ev.kind === "system.notify";
                const isUserMessage = isMessage && ev.by === "user";
                const isAgentMessage = isMessage && ev.by !== "user";
                const replyTo = ev.data?.reply_to;
                const quoteText = ev.data?.quote_text;
                const readStatus = ev._read_status;
                const recipients = ev.data?.to as string[] | undefined;
                
                // Get sender's runtime for color
                const senderActor = actors.find(a => a.id === ev.by);
                const senderRuntime = isUserMessage ? "user" : (senderActor?.runtime || "custom");
                const senderColor = getRuntimeColor(senderRuntime);
                
                // Get message style based on sender's runtime
                const getMessageStyle = () => {
                  if (isNotify) return "border-amber-900/50 bg-amber-950/20";
                  return `${senderColor.border} ${senderColor.bg}`;
                };
                
                return (
                  <div
                    key={String(ev.id || idx)}
                    className={classNames(
                      "rounded border bg-slate-950/40 px-3 py-2",
                      getMessageStyle(),
                    )}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2 min-w-0">
                        {/* Sender badge */}
                        {isMessage && (
                          <span className={classNames(
                            "text-[10px] px-1.5 py-0.5 rounded font-medium",
                            senderColor.bg, senderColor.text
                          )}>
                            {ev.by || "unknown"}
                          </span>
                        )}
                        {isNotify && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/60 text-amber-200 font-medium">
                            system
                          </span>
                        )}
                        {!isMessage && !isNotify && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                            {ev.by || "—"}
                          </span>
                        )}
                        {/* Recipients */}
                        {isMessage && recipients && recipients.length > 0 && (
                          <span className="text-xs text-slate-500">
                            → {recipients.join(", ")}
                          </span>
                        )}
                        {replyTo && <span className="text-[10px] text-slate-500">(reply)</span>}
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="text-xs text-slate-500 truncate" title={formatFullTime(ev.ts)}>
                          {formatTime(ev.ts)}
                        </div>
                        {isMessage && (
                          <button
                            className="text-[10px] px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 text-slate-400 hover:text-slate-200"
                            onClick={() => startReply(ev)}
                            title="Reply to this message"
                          >
                            ↩
                          </button>
                        )}
                      </div>
                    </div>
                    {quoteText && (
                      <div className="mt-1 text-xs text-slate-500 border-l-2 border-slate-700 pl-2 italic truncate">
                        "{quoteText}"
                      </div>
                    )}
                    <div className={classNames(
                      "mt-1 text-sm whitespace-pre-wrap break-words",
                      isNotify && "text-amber-200/80",
                    )}>
                      {formatEventLine(ev)}
                    </div>
                    {/* Read status for messages */}
                    {isMessage && readStatus && Object.keys(readStatus).length > 0 && (
                      <div className="mt-1.5 flex items-center gap-1.5 text-[10px]">
                        {Object.entries(readStatus).map(([actorId, hasRead]) => (
                          <span
                            key={actorId}
                            className={classNames(
                              "inline-flex items-center gap-0.5",
                              hasRead ? "text-emerald-400" : "text-slate-500"
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
                  <div className="text-sm text-slate-400">No messages yet</div>
                  <div className="text-xs text-slate-500 mt-1">Send a message to start the conversation</div>
                </div>
              )}
            </div>
            {/* Scroll to bottom button */}
            {showScrollButton && (
              <button
                className="absolute bottom-4 right-4 rounded-full bg-slate-800 border border-slate-700 p-2 shadow-lg hover:bg-slate-700 transition-colors"
                onClick={scrollToBottom}
                title="Scroll to bottom"
              >
                <svg className="w-4 h-4 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                </svg>
              </button>
            )}
          </section>

          <footer className="flex-shrink-0 border-t border-slate-800 bg-slate-950/30 px-4 py-3">
            {replyTarget && (
              <div className="mb-2 flex items-center gap-2 text-xs text-slate-400 bg-slate-900/50 rounded px-2 py-1.5">
                <span className="text-slate-500">Replying to</span>
                <span className="font-medium text-slate-300">{replyTarget.by}</span>
                <span className="truncate flex-1 text-slate-500">"{replyTarget.text}"</span>
                <button
                  className="text-slate-400 hover:text-slate-200 px-1"
                  onClick={cancelReply}
                  title="Cancel reply"
                >
                  ×
                </button>
              </div>
            )}
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <div className="text-xs text-slate-400 mr-1">To</div>
              {["@all", "@foreman", "@peers", "user", ...actors.map((a) => String(a.id || ""))].map((tok) => {
                const t = tok.trim();
                if (!t) return null;
                const active = toTokens.includes(t);
                return (
                  <button
                    key={t}
                    className={classNames(
                      "text-[11px] px-2 py-1 rounded border",
                      active
                        ? "bg-emerald-400 text-slate-950 border-emerald-300"
                        : "bg-slate-950/40 text-slate-200 border-slate-800 hover:bg-slate-800/40",
                    )}
                    onClick={() => toggleRecipient(t)}
                    disabled={!selectedGroupId || busy === "send"}
                    title={active ? "Remove recipient" : "Add recipient"}
                  >
                    {t}
                  </button>
                );
              })}
              {toTokens.length > 0 && (
                <button
                  className="text-[11px] px-2 py-1 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 disabled:opacity-50"
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
                className="w-full rounded bg-slate-900 border border-slate-800 px-3 py-2 text-sm resize-none min-h-[40px] max-h-[120px]"
                placeholder="Message… (type @ to mention, Ctrl+Enter to send)"
                rows={1}
                value={composerText}
                onChange={(e) => {
                  const val = e.target.value;
                  setComposerText(val);
                  // Auto-resize textarea
                  const target = e.target;
                  target.style.height = "auto";
                  target.style.height = Math.min(target.scrollHeight, 120) + "px";
                  // Check for @ mention trigger
                  const lastAt = val.lastIndexOf("@");
                  if (lastAt >= 0) {
                    const afterAt = val.slice(lastAt + 1);
                    // Only show menu if @ is at word boundary and no space after
                    if ((lastAt === 0 || val[lastAt - 1] === " " || val[lastAt - 1] === "\n") && !afterAt.includes(" ") && !afterAt.includes("\n")) {
                      setMentionFilter(afterAt);
                      setShowMentionMenu(true);
                      setMentionSelectedIndex(0);  // Reset selection on filter change
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
                        // Also add to recipients (To field)
                        if (selected && !toTokens.includes(selected)) {
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
                    // Ctrl+Enter or Cmd+Enter to send
                    if (e.ctrlKey || e.metaKey) {
                      e.preventDefault();
                      sendMessage();
                    }
                    // Plain Enter without modifier allows newline (default behavior)
                  } else if (e.key === "Escape") {
                    setShowMentionMenu(false);
                    cancelReply();
                  }
                }}
                onBlur={() => {
                  // Delay to allow click on menu item
                  setTimeout(() => setShowMentionMenu(false), 150);
                }}
              />
              {showMentionMenu && mentionSuggestions.length > 0 && (
                <div className="absolute bottom-full left-0 mb-1 w-48 max-h-40 overflow-auto rounded border border-slate-700 bg-slate-900 shadow-lg z-10">
                  {mentionSuggestions.slice(0, 8).map((s, idx) => (
                    <button
                      key={s}
                      className={classNames(
                        "w-full text-left px-3 py-1.5 text-sm text-slate-200",
                        idx === mentionSelectedIndex ? "bg-slate-700" : "hover:bg-slate-800"
                      )}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        // Replace the @... with the selected mention
                        const lastAt = composerText.lastIndexOf("@");
                        if (lastAt >= 0) {
                          const before = composerText.slice(0, lastAt);
                          setComposerText(before + s + " ");
                        }
                        // Also add to recipients (To field)
                        if (s && !toTokens.includes(s)) {
                          setToText((prev) => (prev ? prev + ", " + s : s));
                        }
                        setShowMentionMenu(false);
                        setMentionSelectedIndex(0);
                        composerRef.current?.focus();
                      }}
                      onMouseEnter={() => setMentionSelectedIndex(idx)}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
              <button
                className="rounded bg-emerald-400 text-slate-950 px-4 py-2 text-sm font-semibold disabled:opacity-50"
                onClick={sendMessage}
                disabled={!composerText.trim() || busy === "send"}
              >
                Send
              </button>
            </div>
          </footer>
        </main>
      </div>

      {inboxOpen ? (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-start justify-center p-6 z-50"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setInboxOpen(false);
          }}
        >
          <div className="w-full max-w-2xl mt-16 rounded-xl border border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900 shadow-2xl">
            <div className="px-6 py-4 border-b border-slate-700/50 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-lg font-semibold text-white truncate">Inbox · {inboxActorId}</div>
                <div className="text-sm text-slate-400">{inboxMessages.length} unread messages</div>
              </div>
              <div className="flex gap-2">
                <button
                  className="rounded-lg bg-slate-700 hover:bg-slate-600 px-4 py-2 text-sm font-medium text-slate-200 disabled:opacity-50 transition-colors"
                  onClick={markInboxAllRead}
                  disabled={!inboxMessages.length || busy.startsWith("inbox")}
                >
                  Mark all read
                </button>
                <button
                  className="rounded-lg bg-slate-600 hover:bg-slate-500 px-4 py-2 text-sm font-medium text-white transition-colors"
                  onClick={() => setInboxOpen(false)}
                >
                  Close
                </button>
              </div>
            </div>

            <div className="max-h-[60vh] overflow-auto p-4 space-y-2">
              {inboxMessages.map((ev, idx) => (
                <div
                  key={String(ev.id || idx)}
                  className="rounded-lg border border-slate-700/50 bg-slate-800/50 px-4 py-3"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-xs text-slate-400 truncate" title={formatFullTime(ev.ts)}>
                      {formatTime(ev.ts)}
                    </div>
                    <div className="text-xs font-medium text-slate-300 truncate">{ev.by || "—"}</div>
                  </div>
                  <div className="mt-2 text-sm text-slate-200 whitespace-pre-wrap break-words">{formatEventLine(ev)}</div>
                </div>
              ))}
              {!inboxMessages.length && (
                <div className="text-center py-8">
                  <div className="text-3xl mb-2">📭</div>
                  <div className="text-sm text-slate-400">No unread messages</div>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {showGroupEdit && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-start justify-center p-6 z-50"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowGroupEdit(false);
          }}
        >
          <div className="w-full max-w-md mt-16 rounded-xl border border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900 shadow-2xl">
            <div className="px-6 py-4 border-b border-slate-700/50">
              <div className="text-lg font-semibold text-white">Edit Group</div>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">Name</label>
                <input
                  className="w-full rounded-lg bg-slate-900/80 border border-slate-600/50 px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none"
                  value={editGroupTitle}
                  onChange={(e) => setEditGroupTitle(e.target.value)}
                  placeholder="Group name"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">Description (optional)</label>
                <input
                  className="w-full rounded-lg bg-slate-900/80 border border-slate-600/50 px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none"
                  value={editGroupTopic}
                  onChange={(e) => setEditGroupTopic(e.target.value)}
                  placeholder="What is this group working on?"
                />
              </div>
              <div className="flex gap-3 pt-3">
                <button
                  className="flex-1 rounded-lg bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white px-4 py-2.5 text-sm font-semibold shadow-lg shadow-blue-500/25 disabled:opacity-50 transition-all"
                  onClick={updateGroup}
                  disabled={!editGroupTitle.trim() || busy === "group-update"}
                >
                  Save
                </button>
                <button
                  className="px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium transition-colors"
                  onClick={() => setShowGroupEdit(false)}
                >
                  Cancel
                </button>
                <button
                  className="px-4 py-2.5 rounded-lg bg-rose-500/20 border border-rose-500/30 text-rose-400 text-sm font-medium hover:bg-rose-500/30 disabled:opacity-50 transition-colors"
                  onClick={() => {
                    setShowGroupEdit(false);
                    deleteGroup();
                  }}
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

      {showSettings && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-start justify-center p-6 z-50"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowSettings(false);
          }}
        >
          <div className="w-full max-w-md mt-16 rounded-xl border border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900 shadow-2xl">
            <div className="px-6 py-4 border-b border-slate-700/50">
              <div className="text-lg font-semibold text-white">Settings</div>
              <div className="text-sm text-slate-400 mt-1">
                Configure automation behavior
              </div>
            </div>
            <div className="p-6 space-y-5">
              {/* Auto-nudge toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-200">Auto-remind agents</div>
                  <div className="text-xs text-slate-500 mt-0.5">Nudge agents when they have unread messages ({editNudgeSeconds}s)</div>
                </div>
                <button
                  className={classNames(
                    "w-12 h-6 rounded-full transition-colors relative",
                    editNudgeSeconds > 0 ? "bg-blue-600" : "bg-slate-700"
                  )}
                  onClick={() => setEditNudgeSeconds(editNudgeSeconds > 0 ? 0 : 300)}
                >
                  <span className={classNames(
                    "absolute top-1 w-4 h-4 rounded-full bg-white transition-transform",
                    editNudgeSeconds > 0 ? "left-7" : "left-1"
                  )} />
                </button>
              </div>

              {/* Actor idle detection */}
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-200">Actor idle detection</div>
                  <div className="text-xs text-slate-500 mt-0.5">Notify foreman when actor is idle ({editActorIdleSeconds}s)</div>
                </div>
                <button
                  className={classNames(
                    "w-12 h-6 rounded-full transition-colors relative",
                    editActorIdleSeconds > 0 ? "bg-blue-600" : "bg-slate-700"
                  )}
                  onClick={() => setEditActorIdleSeconds(editActorIdleSeconds > 0 ? 0 : 600)}
                >
                  <span className={classNames(
                    "absolute top-1 w-4 h-4 rounded-full bg-white transition-transform",
                    editActorIdleSeconds > 0 ? "left-7" : "left-1"
                  )} />
                </button>
              </div>

              {/* Keepalive */}
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-200">Keepalive reminders</div>
                  <div className="text-xs text-slate-500 mt-0.5">Remind actors to continue after Next: ({editKeepaliveSeconds}s)</div>
                </div>
                <button
                  className={classNames(
                    "w-12 h-6 rounded-full transition-colors relative",
                    editKeepaliveSeconds > 0 ? "bg-blue-600" : "bg-slate-700"
                  )}
                  onClick={() => setEditKeepaliveSeconds(editKeepaliveSeconds > 0 ? 0 : 120)}
                >
                  <span className={classNames(
                    "absolute top-1 w-4 h-4 rounded-full bg-white transition-transform",
                    editKeepaliveSeconds > 0 ? "left-7" : "left-1"
                  )} />
                </button>
              </div>

              {/* Silence detection */}
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-200">Silence detection</div>
                  <div className="text-xs text-slate-500 mt-0.5">Notify foreman when group is silent ({editSilenceSeconds}s)</div>
                </div>
                <button
                  className={classNames(
                    "w-12 h-6 rounded-full transition-colors relative",
                    editSilenceSeconds > 0 ? "bg-blue-600" : "bg-slate-700"
                  )}
                  onClick={() => setEditSilenceSeconds(editSilenceSeconds > 0 ? 0 : 600)}
                >
                  <span className={classNames(
                    "absolute top-1 w-4 h-4 rounded-full bg-white transition-transform",
                    editSilenceSeconds > 0 ? "left-7" : "left-1"
                  )} />
                </button>
              </div>

              {/* Delivery throttle */}
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-200">Message batching</div>
                  <div className="text-xs text-slate-500 mt-0.5">Batch messages within window ({editDeliveryInterval}s)</div>
                </div>
                <input
                  type="number"
                  min="0"
                  max="300"
                  className="w-20 rounded-lg bg-slate-900/80 border border-slate-600/50 px-3 py-1.5 text-sm text-white text-center focus:border-blue-500 outline-none"
                  value={editDeliveryInterval}
                  onChange={(e) => setEditDeliveryInterval(Math.max(0, parseInt(e.target.value) || 0))}
                />
              </div>

              <div className="flex gap-3 pt-3">
                <button
                  className="flex-1 rounded-lg bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white px-4 py-2.5 text-sm font-semibold shadow-lg shadow-blue-500/25 disabled:opacity-50 transition-all"
                  onClick={updateSettings}
                  disabled={busy === "settings-update"}
                >
                  Save
                </button>
                <button
                  className="px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium transition-colors"
                  onClick={() => setShowSettings(false)}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {editingActor && (
        <div
          className="fixed inset-0 bg-black/60 flex items-start justify-center p-6 z-50"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setEditingActor(null);
          }}
        >
          <div className="w-full max-w-md mt-16 rounded-xl border border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900 shadow-2xl">
            <div className="px-6 py-4 border-b border-slate-700/50">
              <div className="text-lg font-semibold text-white">Switch Runtime: {editingActor.id}</div>
              <div className="text-sm text-slate-400 mt-1">
                Change the AI runtime for this agent
              </div>
            </div>
            <div className="p-6 space-y-5">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">Runtime</label>
                <select
                  className="w-full rounded-lg bg-slate-900/80 border border-slate-600/50 px-4 py-2.5 text-sm text-white focus:border-blue-500 outline-none"
                  value={editActorRuntime}
                  onChange={(e) => setEditActorRuntime(e.target.value as typeof editActorRuntime)}
                >
                  {(["claude", "codex", "droid", "opencode", "gemini", "copilot", "custom"] as const).map((rt) => {
                    const info = RUNTIME_INFO[rt];
                    const rtInfo = runtimes.find((r) => r.name === rt);
                    const available = rt === "custom" || (rtInfo?.available ?? false);
                    return (
                      <option key={rt} value={rt} disabled={!available}>
                        {info?.label || rt}{!available ? " (not installed)" : ""}
                      </option>
                    );
                  })}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">Command</label>
                <input
                  className="w-full rounded-lg bg-slate-900/80 border border-slate-600/50 px-4 py-2.5 text-sm font-mono text-white focus:border-blue-500 outline-none"
                  value={editActorCommand}
                  onChange={(e) => setEditActorCommand(e.target.value)}
                  placeholder={RUNTIME_DEFAULTS[editActorRuntime] || "Enter command..."}
                />
                <div className="text-[10px] text-slate-500 mt-1.5">
                  Default: <code className="bg-slate-800 px-1 rounded">{RUNTIME_DEFAULTS[editActorRuntime] || "custom"}</code>
                </div>
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  className="flex-1 rounded-lg bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white px-4 py-2.5 text-sm font-semibold shadow-lg shadow-blue-500/25 disabled:opacity-50 transition-all"
                  onClick={updateActor}
                  disabled={busy === "actor-update" || (!editActorCommand.trim() && editActorRuntime === "custom")}
                >
                  Save
                </button>
                <button
                  className="px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium transition-colors"
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
          className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-start justify-center p-6 z-50"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowCreateGroup(false);
          }}
        >
          <div className="w-full max-w-lg mt-16 rounded-xl border border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900 shadow-2xl">
            <div className="px-6 py-4 border-b border-slate-700/50">
              <div className="text-lg font-semibold text-white">Create Working Group</div>
              <div className="text-sm text-slate-400 mt-1">
                Select a project directory to start collaborating
              </div>
            </div>
            <div className="p-6 space-y-5">
              {/* Quick suggestions */}
              {dirSuggestions.length > 0 && !createGroupPath && (
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-2">Quick Select</label>
                  <div className="grid grid-cols-2 gap-2">
                    {dirSuggestions.slice(0, 6).map((s) => (
                      <button
                        key={s.path}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-600/50 bg-slate-800/50 hover:bg-slate-700/50 hover:border-slate-500 transition-colors text-left"
                        onClick={() => {
                          setCreateGroupPath(s.path);
                          setCreateGroupName(s.path.split("/").filter(Boolean).pop() || "");
                          fetchDirContents(s.path);
                        }}
                      >
                        <span className="text-lg">{s.icon}</span>
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-slate-200 truncate">{s.name}</div>
                          <div className="text-[10px] text-slate-500 truncate">{s.path}</div>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Path input with browse */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">
                  Project Directory
                </label>
                <div className="flex gap-2">
                  <input
                    className="flex-1 rounded-lg bg-slate-900/80 border border-slate-600/50 px-4 py-2.5 text-sm font-mono text-white placeholder-slate-500 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-colors"
                    value={createGroupPath}
                    onChange={(e) => {
                      setCreateGroupPath(e.target.value);
                      const dirName = e.target.value.split("/").filter(Boolean).pop() || "";
                      if (!createGroupName || createGroupName === createGroupPath.split("/").filter(Boolean).pop()) {
                        setCreateGroupName(dirName);
                      }
                    }}
                    placeholder="/path/to/your/project"
                    autoFocus
                  />
                  <button
                    className="px-4 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium transition-colors"
                    onClick={() => fetchDirContents(createGroupPath || "~")}
                  >
                    Browse
                  </button>
                </div>
              </div>

              {/* Directory browser */}
              {showDirBrowser && (
                <div className="border border-slate-600/50 rounded-lg bg-slate-900/50 max-h-48 overflow-auto">
                  {currentDir && (
                    <div className="px-3 py-1.5 border-b border-slate-700/30 bg-slate-800/30 text-xs text-slate-400 font-mono truncate">
                      {currentDir}
                    </div>
                  )}
                  {parentDir && (
                    <button
                      className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-800/50 text-left border-b border-slate-700/30"
                      onClick={() => {
                        fetchDirContents(parentDir);
                        setCreateGroupPath(parentDir);
                        setCreateGroupName(parentDir.split("/").filter(Boolean).pop() || "");
                      }}
                    >
                      <span className="text-slate-400">📁</span>
                      <span className="text-sm text-slate-400">..</span>
                    </button>
                  )}
                  {dirItems.filter(d => d.is_dir).length === 0 && (
                    <div className="px-3 py-4 text-center text-sm text-slate-500">
                      No subdirectories
                    </div>
                  )}
                  {dirItems.filter(d => d.is_dir).map((item) => (
                    <button
                      key={item.path}
                      className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-800/50 text-left"
                      onClick={() => {
                        setCreateGroupPath(item.path);
                        setCreateGroupName(item.name);
                        fetchDirContents(item.path);
                      }}
                    >
                      <span className="text-blue-400">📁</span>
                      <span className="text-sm text-slate-200">{item.name}</span>
                    </button>
                  ))}
                </div>
              )}

              {/* Group name */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">
                  Group Name
                </label>
                <input
                  className="w-full rounded-lg bg-slate-900/80 border border-slate-600/50 px-4 py-2.5 text-sm text-white placeholder-slate-500 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-colors"
                  value={createGroupName}
                  onChange={(e) => setCreateGroupName(e.target.value)}
                  placeholder="Auto-filled from directory name"
                />
              </div>

              <div className="flex gap-3 pt-2">
                <button
                  className="flex-1 rounded-lg bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white px-4 py-2.5 text-sm font-semibold shadow-lg shadow-blue-500/25 disabled:opacity-50 disabled:shadow-none transition-all"
                  onClick={createGroup}
                  disabled={!createGroupPath.trim() || busy === "create"}
                >
                  {busy === "create" ? "Creating..." : "Create Group"}
                </button>
                <button
                  className="px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium transition-colors"
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
          className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-start justify-center p-6 z-50"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowAddActor(false);
          }}
        >
          <div className="w-full max-w-lg mt-16 rounded-xl border border-slate-700/50 bg-gradient-to-b from-slate-800 to-slate-900 shadow-2xl">
            <div className="px-6 py-4 border-b border-slate-700/50">
              <div className="text-lg font-semibold text-white">Add AI Agent</div>
              <div className="text-sm text-slate-400 mt-1">
                Choose an AI runtime to add to your team
              </div>
            </div>
            <div className="p-6 space-y-5">
              {/* Error message */}
              {addActorError && (
                <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-300 flex items-center justify-between gap-3">
                  <span>{addActorError}</span>
                  <button className="text-rose-300 hover:text-rose-100" onClick={() => setAddActorError("")}>×</button>
                </div>
              )}

              {/* Agent Name */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">
                  Agent Name
                  <span className="text-slate-500 ml-1">(supports 中文/日本語)</span>
                </label>
                <input
                  className="w-full rounded-lg bg-slate-900/80 border border-slate-600/50 px-4 py-2.5 text-sm text-white placeholder-slate-500 focus:border-blue-500 outline-none"
                  value={newActorId}
                  onChange={(e) => setNewActorId(e.target.value)}
                  placeholder={suggestedActorId}
                />
                <div className="text-[10px] text-slate-500 mt-1">
                  Leave empty to use: <code className="bg-slate-800 px-1 rounded">{suggestedActorId}</code>
                </div>
              </div>

              {/* Runtime selection */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">AI Runtime</label>
                <div className="grid grid-cols-2 gap-2">
                  {(["claude", "codex", "droid", "opencode", "gemini", "copilot"] as const).map((rt) => {
                    const info = RUNTIME_INFO[rt] || { label: rt, desc: "" };
                    const rtInfo = runtimes.find((r) => r.name === rt);
                    const available = rtInfo?.available ?? false;
                    const isSelected = newActorRuntime === rt;
                    return (
                      <button
                        key={rt}
                        className={classNames(
                          "flex flex-col items-start px-3 py-2.5 rounded-lg border text-left transition-all",
                          isSelected
                            ? "bg-blue-600/20 border-blue-500 ring-1 ring-blue-500"
                            : available
                            ? "bg-slate-800/50 border-slate-600/50 hover:border-slate-500 hover:bg-slate-700/50"
                            : "bg-slate-900/30 border-slate-700/30 opacity-50 cursor-not-allowed"
                        )}
                        onClick={() => available && setNewActorRuntime(rt as typeof newActorRuntime)}
                        disabled={!available}
                      >
                        <div className="flex items-center gap-2 w-full">
                          <span className={classNames("text-sm font-medium", isSelected ? "text-blue-300" : "text-slate-200")}>
                            {info.label}
                          </span>
                          {!available && <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400">not installed</span>}
                        </div>
                        <div className="text-[10px] text-slate-500 mt-0.5 line-clamp-1">{info.desc}</div>
                      </button>
                    );
                  })}
                </div>
                <button
                  className={classNames(
                    "mt-2 w-full px-3 py-2 rounded-lg border text-left text-sm transition-all",
                    newActorRuntime === "custom"
                      ? "bg-slate-700/50 border-slate-500"
                      : "bg-slate-800/30 border-slate-700/50 hover:border-slate-600"
                  )}
                  onClick={() => setNewActorRuntime("custom")}
                >
                  <span className="text-slate-300">Custom command...</span>
                </button>
              </div>

              {/* Role selection */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-2">Role</label>
                <div className="flex gap-2">
                  <button
                    className={classNames(
                      "flex-1 px-4 py-2.5 rounded-lg border text-sm font-medium transition-all",
                      newActorRole === "foreman"
                        ? "bg-amber-500/20 border-amber-500 text-amber-300"
                        : hasForeman
                        ? "bg-slate-900/30 border-slate-700/30 text-slate-500 cursor-not-allowed"
                        : "bg-slate-800/50 border-slate-600/50 text-slate-300 hover:border-slate-500"
                    )}
                    onClick={() => !hasForeman && setNewActorRole("foreman")}
                    disabled={hasForeman}
                  >
                    ★ Foreman {hasForeman && "(exists)"}
                  </button>
                  <button
                    className={classNames(
                      "flex-1 px-4 py-2.5 rounded-lg border text-sm font-medium transition-all",
                      newActorRole === "peer"
                        ? "bg-blue-500/20 border-blue-500 text-blue-300"
                        : !hasForeman
                        ? "bg-slate-900/30 border-slate-700/30 text-slate-500 cursor-not-allowed"
                        : "bg-slate-800/50 border-slate-600/50 text-slate-300 hover:border-slate-500"
                    )}
                    onClick={() => hasForeman && setNewActorRole("peer")}
                    disabled={!hasForeman}
                  >
                    Peer {!hasForeman && "(need foreman first)"}
                  </button>
                </div>
                <div className="text-[10px] text-slate-500 mt-1.5">
                  {hasForeman ? "Foreman leads the team. Peers are worker agents." : "First agent must be the foreman (team leader)."}
                </div>
              </div>

              {/* Advanced options toggle */}
              <button
                className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-300"
                onClick={() => setShowAdvancedActor(!showAdvancedActor)}
              >
                <span className={classNames("transition-transform", showAdvancedActor && "rotate-90")}>▶</span>
                Advanced options
              </button>

              {showAdvancedActor && (
                <div className="space-y-4 pl-4 border-l-2 border-slate-700/50">
                  {/* Command override */}
                  <div>
                    <label className="block text-xs font-medium text-slate-400 mb-2">Command Override</label>
                    <input
                      className="w-full rounded-lg bg-slate-900/80 border border-slate-600/50 px-3 py-2 text-sm font-mono text-white placeholder-slate-500 focus:border-blue-500 outline-none"
                      value={newActorCommand}
                      onChange={(e) => setNewActorCommand(e.target.value)}
                      placeholder={RUNTIME_DEFAULTS[newActorRuntime] || "Enter command..."}
                    />
                    <div className="text-[10px] text-slate-500 mt-1">
                      Default: <code className="bg-slate-800 px-1 rounded">{RUNTIME_DEFAULTS[newActorRuntime] || "custom"}</code>
                    </div>
                  </div>
                </div>
              )}

              <div className="flex gap-3 pt-2">
                <div className="flex-1">
                  <button
                    className="w-full rounded-lg bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white px-4 py-2.5 text-sm font-semibold shadow-lg shadow-blue-500/25 disabled:opacity-50 disabled:shadow-none disabled:cursor-not-allowed transition-all"
                    onClick={addActor}
                    disabled={!canAddActor}
                  >
                    {busy === "actor-add" ? "Adding..." : "Add Agent"}
                  </button>
                  {addActorDisabledReason && (
                    <div className="text-[10px] text-amber-400 mt-1.5">{addActorDisabledReason}</div>
                  )}
                </div>
                <button
                  className="px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium transition-colors"
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

      {selectedGroupId && termActorId ? (
        <TerminalModal
          groupId={selectedGroupId}
          actorId={termActorId}
          actorTitle={termActorTitle}
          onClose={() => setTermActorId("")}
        />
      ) : null}
    </div>
  );
}
