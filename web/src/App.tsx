import { useEffect, useMemo, useRef, useState } from "react";
import { apiJson } from "./api";
import { TerminalModal } from "./TerminalModal";

type GroupMeta = {
  group_id: string;
  title?: string;
  topic?: string;
  updated_at?: string;
  created_at?: string;
  running?: boolean;
};

type GroupDoc = {
  group_id: string;
  title?: string;
  topic?: string;
  active_scope_key?: string;
  scopes?: Array<{ scope_key?: string; url?: string; label?: string }>;
};

type LedgerEvent = {
  id?: string;
  ts?: string;
  kind?: string;
  by?: string;
  data?: any;
  _read_status?: Record<string, boolean>; // actor_id -> has_read
};

type Actor = {
  id: string;
  role?: string;
  title?: string;
  enabled?: boolean;
  command?: string[];
  runner?: string;
  runtime?: string;
  updated_at?: string;
};

type RuntimeInfo = {
  name: string;
  display_name: string;
  command: string;
  available: boolean;
  path?: string;
  capabilities: string;
};

type ReplyTarget = {
  eventId: string;
  by: string;
  text: string;
} | null;

type GroupContext = {
  vision?: string;
  sketch?: string;
  milestones?: Array<{ id: string; title: string; status?: string; due?: string }>;
  tasks?: Array<{ id: string; title: string; status?: string; assignee?: string; milestone_id?: string }>;
  notes?: Array<{ id: string; title: string; content?: string }>;
  references?: Array<{ id: string; url: string; title?: string }>;
  presence?: Record<string, { status?: string; activity?: string; updated_at?: string }>;
};

// Runtime default commands - used for template auto-fill
const RUNTIME_DEFAULTS: Record<string, string> = {
  claude: "claude",
  codex: "codex",
  droid: "droid",
  opencode: "opencode",
};

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
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
    const by = String(ev.by || ev.data.by || "unknown");
    const to = Array.isArray(ev.data.to)
      ? ev.data.to.map((x: any) => String(x || "").trim()).filter((x: string) => x.length > 0)
      : [];
    const text = String(ev.data.text || "");
    const arrow = to.length ? ` → ${to.join(", ")}` : "";
    return `${by}${arrow}: ${text}`;
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
  const [newActorRunner, setNewActorRunner] = useState<"pty" | "headless">("pty");
  const [newActorRuntime, setNewActorRuntime] = useState<"claude" | "codex" | "droid" | "opencode" | "custom">("custom");
  const [newActorTitle, setNewActorTitle] = useState("");
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
  const [editingActor, setEditingActor] = useState<Actor | null>(null);
  const [editActorRuntime, setEditActorRuntime] = useState<"claude" | "codex" | "droid" | "opencode" | "custom">("custom");
  const [editActorCommand, setEditActorCommand] = useState("");
  const [editActorTitle, setEditActorTitle] = useState("");
  const [showScrollButton, setShowScrollButton] = useState(false);
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

  // Generate suggested actor ID based on runtime and role
  const suggestedActorId = useMemo(() => {
    const prefix = newActorRole === "foreman" ? "foreman" : newActorRuntime !== "custom" ? newActorRuntime : "peer";
    const existing = actors.map((a) => String(a.id || ""));
    if (!existing.includes(prefix)) return prefix;
    for (let i = 1; i <= 99; i++) {
      const candidate = `${prefix}-${i}`;
      if (!existing.includes(candidate)) return candidate;
    }
    return `${prefix}-${Date.now()}`;
  }, [newActorRole, newActorRuntime, actors]);

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

  async function fetchContext(groupId: string) {
    const resp = await apiJson<{ context: GroupContext }>(`/api/v1/groups/${encodeURIComponent(groupId)}/context`);
    if (resp.ok) {
      setGroupContext(resp.result.context || null);
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
    setErrorMsg("");

    const show = await apiJson<{ group: GroupDoc }>(`/api/v1/groups/${encodeURIComponent(groupId)}`);
    if (show.ok) setGroupDoc(show.result.group);

    const tail = await apiJson<{ events: LedgerEvent[] }>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/tail?lines=120&with_read_status=true`,
    );
    if (tail.ok) setEvents(tail.result.events || []);

    const a = await apiJson<{ actors: Actor[] }>(`/api/v1/groups/${encodeURIComponent(groupId)}/actors`);
    if (a.ok) setActors(a.result.actors || []);

    // Fetch context
    await fetchContext(groupId);
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
      } catch {
        // ignore
      }
    });
    eventSourceRef.current = es;
  }

  useEffect(() => {
    refreshGroups();
    fetchRuntimes();
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
    bottomRef.current?.scrollIntoView({ block: "end" });
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
      await loadGroup(selectedGroupId);
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
        // Auto-select the newly created group
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
    const a = await apiJson<{ actors: Actor[] }>(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors`);
    if (a.ok) setActors(a.result.actors || []);
  }

  async function addActor() {
    if (!selectedGroupId) return;
    const actorId = newActorId.trim() || suggestedActorId;
    if (!actorId) return;
    setBusy("actor-add");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors`, {
        method: "POST",
        body: JSON.stringify({
          actor_id: actorId,
          role: newActorRole,
          runner: newActorRunner,
          runtime: newActorRuntime,
          title: newActorTitle.trim(),
          command: newActorCommand,
          env: {},
          default_scope_key: "",
          by: "user",
        }),
      });
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setShowAddActor(false);
      setNewActorId("");
      setNewActorTitle("");
      setNewActorCommand("");
      setNewActorRunner("pty");
      setNewActorRuntime("custom");
      await refreshActors();
    } finally {
      setBusy("");
    }
  }

  async function toggleActorEnabled(actor: Actor) {
    if (!selectedGroupId) return;
    const actorId = String(actor.id || "").trim();
    if (!actorId) return;
    setBusy(`actor-${actor.enabled ? "stop" : "start"}:${actorId}`);
    try {
      setErrorMsg("");
      const path = actor.enabled ? "stop" : "start";
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors/${encodeURIComponent(actorId)}/${path}`, {
        method: "POST",
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
    // Only allow editing stopped actors
    if (actor.enabled) {
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
      await refreshGroups();
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
      await refreshGroups();
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
      setSelectedGroupId("");
      setGroupDoc(null);
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
    <div className="h-full w-full">
      <div className="h-full grid grid-cols-[320px_1fr]">
        <aside className="h-full border-r border-slate-800 bg-slate-950/60">
          <div className="p-3 border-b border-slate-800">
            <div className="text-sm font-semibold tracking-wide">Working Groups</div>
            <div className="mt-2 flex gap-2">
              <input
                className="w-full rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                placeholder="New group title…"
                value={createTitle}
                onChange={(e) => setCreateTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") createGroup();
                }}
              />
              <button
                className="rounded bg-slate-200 text-slate-950 px-3 text-sm font-medium disabled:opacity-50"
                onClick={createGroup}
                disabled={!createTitle.trim() || busy === "create"}
              >
                +
              </button>
            </div>
          </div>

          <div className="p-2 overflow-auto h-[calc(100%-64px)]">
            {groups.map((g) => {
              const gid = String(g.group_id || "");
              const active = gid === selectedGroupId;
              return (
                <button
                  key={gid}
                  className={classNames(
                    "w-full text-left px-3 py-2 rounded mb-1",
                    active ? "bg-slate-800/70" : "hover:bg-slate-900/70",
                  )}
                  onClick={() => setSelectedGroupId(gid)}
                >
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-medium truncate">{g.title || gid}</div>
                    <div className={classNames("text-[10px] px-2 py-0.5 rounded", g.running ? "bg-emerald-900/60 text-emerald-200" : "bg-slate-900 text-slate-400")}>
                      {g.running ? "RUN" : "STOP"}
                    </div>
                  </div>
                  <div className="text-xs text-slate-400 truncate">{g.topic || "—"}</div>
                </button>
              );
            })}
            {!groups.length && (
              <div className="p-3 text-center">
                <div className="text-sm text-slate-400 mb-2">No working groups yet</div>
                <div className="text-xs text-slate-500">
                  Enter a title above and click + to create your first group
                </div>
              </div>
            )}
          </div>
        </aside>

        <main className="h-full flex flex-col">
          <header className="border-b border-slate-800 bg-slate-950/30 px-4 py-3">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <div className="text-sm font-semibold truncate">
                    {groupDoc?.title || (selectedGroupId ? selectedGroupId : "—")}
                  </div>
                  {selectedGroupId && (
                    <button
                      className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200"
                      onClick={openGroupEdit}
                      title="Edit group title/topic"
                    >
                      edit
                    </button>
                  )}
                </div>
                {groupDoc?.topic && (
                  <div className="text-xs text-slate-400 truncate" title={groupDoc.topic}>
                    {groupDoc.topic}
                  </div>
                )}
                <div className="text-xs text-slate-500">
                  {groupDoc?.scopes && groupDoc.scopes.length > 0 ? (
                    <details className="inline">
                      <summary className="cursor-pointer hover:text-slate-400">
                        Scopes: {groupDoc.scopes.length} attached
                        {projectRoot && <span className="text-slate-400"> (active: {projectRoot.split("/").pop()})</span>}
                      </summary>
                      <div className="mt-1 ml-2 space-y-0.5">
                        {groupDoc.scopes.map((s, i) => {
                          const isActive = s.scope_key === groupDoc.active_scope_key;
                          const scopeKey = s.scope_key || "";
                          return (
                            <div key={scopeKey || i} className="flex items-center gap-2 text-xs group/scope">
                              <span className={isActive ? "text-emerald-400" : "text-slate-500"}>
                                {isActive ? "●" : "○"}
                              </span>
                              <span className="text-slate-400 truncate max-w-[280px]" title={s.url}>
                                {s.label || s.url || scopeKey}
                              </span>
                              {!isActive && scopeKey && (
                                <button
                                  className="opacity-0 group-hover/scope:opacity-100 text-[9px] px-1 py-0.5 rounded bg-slate-800 text-slate-500 hover:text-rose-400 hover:bg-rose-950/30"
                                  onClick={async (e) => {
                                    e.preventDefault();
                                    if (!window.confirm(`Detach scope "${s.label || s.url || scopeKey}"?`)) return;
                                    setBusy("detach-scope");
                                    try {
                                      const resp = await apiJson(
                                        `/api/v1/groups/${encodeURIComponent(selectedGroupId)}/scopes/${encodeURIComponent(scopeKey)}?by=user`,
                                        { method: "DELETE" }
                                      );
                                      if (!resp.ok) {
                                        showError(`${resp.error.code}: ${resp.error.message}`);
                                      } else {
                                        await loadGroup(selectedGroupId);
                                      }
                                    } finally {
                                      setBusy("");
                                    }
                                  }}
                                  disabled={busy === "detach-scope"}
                                  title="Detach this scope"
                                >
                                  ×
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </details>
                  ) : (
                    <span>No scopes attached</span>
                  )}
                </div>
              </div>

              <div className="flex gap-2 items-center">
                <button
                  className="rounded bg-emerald-400 text-slate-950 px-3 py-1 text-sm font-semibold disabled:opacity-50"
                  onClick={startGroup}
                  disabled={!selectedGroupId || busy === "group-start"}
                  title="Start group actors"
                >
                  Start
                </button>
                <button
                  className="rounded bg-slate-800 border border-slate-700 px-3 py-1 text-sm font-semibold disabled:opacity-50"
                  onClick={stopGroup}
                  disabled={!selectedGroupId || busy === "group-stop"}
                  title="Stop group runners"
                >
                  Stop
                </button>
                <button
                  className={classNames(
                    "rounded border px-3 py-1 text-sm font-medium",
                    showContext ? "bg-blue-600 border-blue-500 text-white" : "bg-slate-800 border-slate-700 text-slate-200"
                  )}
                  onClick={() => setShowContext((v) => !v)}
                  disabled={!selectedGroupId}
                  title="Toggle context panel (vision/tasks/milestones)"
                >
                  Context
                </button>
                <input
                  className="w-[360px] max-w-[45vw] rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                  placeholder="Set project root path…"
                  value={attachPath}
                  onChange={(e) => setAttachPath(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") attachRoot();
                  }}
                />
                <button
                  className="rounded bg-slate-200 text-slate-950 px-3 py-1 text-sm font-medium disabled:opacity-50"
                  onClick={attachRoot}
                  disabled={!attachPath.trim() || busy === "attach"}
                >
                  Attach
                </button>
              </div>
            </div>

            {errorMsg ? (
              <div className="mt-3 rounded border border-rose-900/70 bg-rose-950/40 px-3 py-2 text-sm text-rose-200 flex items-start justify-between gap-3">
                <div className="min-w-0 break-words">{errorMsg}</div>
                <button
                  className="text-rose-200/80 hover:text-rose-100 text-sm"
                  onClick={() => setErrorMsg("")}
                  title="Dismiss"
                >
                  ×
                </button>
              </div>
            ) : null}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <div className="text-xs text-slate-400 mr-1" title="Actors are AI agents that collaborate in this group">Actors</div>
              {actors.length === 0 && selectedGroupId && (
                <div className="text-xs text-slate-500 italic">
                  No actors yet. Click "+ actor" to add an agent.
                </div>
              )}
              {actors.map((a) => (
                <div
                  key={a.id}
                  className="flex items-center gap-2 rounded border border-slate-800 bg-slate-950/40 px-2 py-1"
                >
                  <div className={classNames("h-2 w-2 rounded-full", a.enabled ? "bg-emerald-400" : "bg-slate-600")} />
                  <div className="text-xs font-medium">{a.id}</div>
                  <div 
                    className={classNames(
                      "text-[10px] px-1 py-0.5 rounded cursor-help",
                      a.role === "foreman" ? "bg-amber-900/40 text-amber-300" : "text-slate-400"
                    )}
                    title={a.role === "foreman" 
                      ? "Foreman: Lead agent that can create/manage other peers" 
                      : "Peer: Worker agent that executes tasks"}
                  >
                    {a.role || "peer"}
                  </div>
                  <div 
                    className={classNames(
                      "text-[9px] px-1 py-0.5 rounded cursor-help",
                      a.runner === "headless" ? "bg-purple-900/40 text-purple-300" : "bg-slate-800 text-slate-400"
                    )}
                    title={a.runner === "headless" 
                      ? "Headless: MCP-only mode, no terminal" 
                      : "PTY: Interactive terminal mode"}
                  >
                    {a.runner || "pty"}
                  </div>
                  {a.runtime && a.runtime !== "custom" && (
                    <div className="text-[9px] px-1 py-0.5 rounded bg-blue-900/40 text-blue-300">
                      {a.runtime}
                    </div>
                  )}
                  <button
                    className="text-[10px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 disabled:opacity-50"
                    onClick={() => openInbox(a.id)}
                    disabled={busy.startsWith("actor-") || busy.startsWith("inbox")}
                    title="Open unread inbox for this actor"
                  >
                    inbox
                  </button>
                  {(a.runner !== "headless") && (
                    <button
                      className="text-[10px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 disabled:opacity-50"
                      onClick={() => setTermActorId(String(a.id || ""))}
                      disabled={!selectedGroupId || busy.startsWith("actor-") || busy.startsWith("inbox")}
                      title="Open web terminal"
                    >
                      term
                    </button>
                  )}
                  {!a.enabled && (
                    <button
                      className="text-[10px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800 hover:bg-blue-950/40 hover:border-blue-900/70 disabled:opacity-50"
                      onClick={() => openEditActor(a)}
                      disabled={busy.startsWith("actor-")}
                      title="Edit actor configuration (runtime/command)"
                    >
                      edit
                    </button>
                  )}
                  <button
                    className="text-[10px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 disabled:opacity-50"
                    onClick={() => toggleActorEnabled(a)}
                    disabled={busy.startsWith("actor-")}
                    title={a.enabled ? "Stop (disable) actor" : "Start (enable) actor"}
                  >
                    {a.enabled ? "stop" : "start"}
                  </button>
                  <button
                    className="text-[10px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800 hover:bg-rose-950/40 hover:border-rose-900/70 disabled:opacity-50"
                    onClick={() => removeActor(a)}
                    disabled={busy.startsWith("actor-")}
                    title="Remove actor"
                  >
                    remove
                  </button>
                </div>
              ))}
              <button
                className="text-xs px-2 py-1 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 disabled:opacity-50"
                onClick={() => setShowAddActor((v) => !v)}
                disabled={!selectedGroupId}
              >
                + actor
              </button>
            </div>

            {showAddActor ? (
              <div className="mt-2 space-y-2">
                <div className="text-xs text-slate-400 mb-1">
                  Add a new actor (AI agent) to this group. 
                  <span className="text-slate-500"> Foreman can manage peers; peers execute tasks.</span>
                  {hasForeman && newActorRole === "foreman" && (
                    <span className="text-amber-400 ml-2">⚠ A foreman already exists in this group.</span>
                  )}
                </div>
                <div className="grid grid-cols-6 gap-2">
                <div className="col-span-1 relative">
                  <input
                    className="w-full rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                    placeholder={suggestedActorId}
                    value={newActorId}
                    onChange={(e) => setNewActorId(e.target.value)}
                    title="Unique identifier for this actor"
                  />
                  {!newActorId && (
                    <button
                      className="absolute right-1 top-1/2 -translate-y-1/2 text-[9px] px-1 py-0.5 rounded bg-slate-800 text-slate-400 hover:text-slate-200"
                      onClick={() => setNewActorId(suggestedActorId)}
                      title="Use suggested ID"
                    >
                      use
                    </button>
                  )}
                </div>
                <select
                  className={classNames(
                    "col-span-1 rounded bg-slate-900 border px-2 py-1 text-sm",
                    hasForeman && newActorRole === "foreman" ? "border-amber-700" : "border-slate-800"
                  )}
                  value={newActorRole}
                  onChange={(e) => setNewActorRole(e.target.value === "foreman" ? "foreman" : "peer")}
                  title="Role: foreman (lead, can create peers) or peer (worker)"
                >
                  <option value="peer">peer (worker)</option>
                  <option value="foreman" disabled={hasForeman}>foreman (lead){hasForeman ? " ✓" : ""}</option>
                </select>
                <select
                  className="col-span-1 rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                  value={newActorRuntime}
                  onChange={(e) => setNewActorRuntime(e.target.value as any)}
                  title="Agent CLI runtime (auto-sets command)"
                >
                  {(() => {
                    const runtimeOptions = [
                      { value: "claude", label: "Claude Code" },
                      { value: "codex", label: "Codex CLI" },
                      { value: "droid", label: "Droid" },
                      { value: "opencode", label: "OpenCode" },
                      { value: "custom", label: "Custom" },
                    ];
                    return runtimeOptions.map((opt) => {
                      const rt = runtimes.find((r) => r.name === opt.value);
                      const available = opt.value === "custom" || (rt?.available ?? false);
                      return (
                        <option
                          key={opt.value}
                          value={opt.value}
                          disabled={!available}
                        >
                          {opt.label}{!available && rt ? " (not installed)" : ""}
                        </option>
                      );
                    });
                  })()}
                </select>
                <select
                  className="col-span-1 rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                  value={newActorRunner}
                  onChange={(e) => setNewActorRunner(e.target.value === "headless" ? "headless" : "pty")}
                  title="Runner: pty (interactive terminal) or headless (MCP-only, no terminal)"
                >
                  <option value="pty">PTY (terminal)</option>
                  <option value="headless">Headless (MCP)</option>
                </select>
                <input
                  className="col-span-1 rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                  placeholder="title (optional)"
                  value={newActorTitle}
                  onChange={(e) => setNewActorTitle(e.target.value)}
                  title="Display name for this actor"
                />
                <input
                  className="col-span-1 rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm font-mono"
                  placeholder={newActorRuntime === "custom" ? "command (required)" : RUNTIME_DEFAULTS[newActorRuntime] || "command"}
                  value={newActorCommand}
                  onChange={(e) => setNewActorCommand(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addActor();
                  }}
                  disabled={newActorRunner === "headless"}
                  title={newActorRuntime === "custom" ? "Enter custom command" : `Default: ${RUNTIME_DEFAULTS[newActorRuntime] || ""} (editable)`}
                />
                </div>
                <div className="flex gap-2 items-center">
                  <button
                    className="rounded bg-slate-200 text-slate-950 px-3 py-1 text-sm font-medium disabled:opacity-50"
                    onClick={addActor}
                    disabled={(!newActorId.trim() && !suggestedActorId) || busy === "actor-add" || (hasForeman && newActorRole === "foreman") || (newActorRuntime === "custom" && !newActorCommand.trim() && newActorRunner !== "headless")}
                  >
                    Add Actor
                  </button>
                  <button
                    className="rounded bg-slate-900 border border-slate-800 px-3 py-1 text-sm font-medium"
                    onClick={() => setShowAddActor(false)}
                  >
                    Cancel
                  </button>
                  {newActorRuntime !== "custom" && newActorCommand && (
                    <span className="text-xs text-slate-500">
                      Using: <code className="bg-slate-800 px-1 rounded">{newActorCommand}</code>
                    </span>
                  )}
                </div>
              </div>
            ) : null}

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
            className="flex-1 overflow-auto px-4 py-3 relative"
            onScroll={handleScroll}
          >
            <div className="space-y-2">
              {events.map((ev, idx) => {
                const isMessage = ev.kind === "chat.message";
                const isNotify = ev.kind === "system.notify";
                const isUserMessage = isMessage && ev.by === "user";
                const isAgentMessage = isMessage && ev.by !== "user";
                const replyTo = ev.data?.reply_to;
                const quoteText = ev.data?.quote_text;
                const readStatus = ev._read_status;
                const recipients = ev.data?.to as string[] | undefined;
                
                // Get message style based on sender
                const getMessageStyle = () => {
                  if (isUserMessage) return "border-emerald-900/50 bg-emerald-950/20";
                  if (isAgentMessage) return "border-blue-900/50 bg-blue-950/20";
                  if (isNotify) return "border-amber-900/50 bg-amber-950/20";
                  return getEventKindStyle(ev.kind || "");
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
                            isUserMessage ? "bg-emerald-900/60 text-emerald-200" : "bg-blue-900/60 text-blue-200"
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
              {!events.length && <div className="text-sm text-slate-400">No events yet.</div>}
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

          <footer className="border-t border-slate-800 bg-slate-950/30 px-4 py-3">
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
              {["@all", "@foreman", "user", ...actors.map((a) => String(a.id || ""))].map((tok) => {
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
              <button
                className="text-[11px] px-2 py-1 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 disabled:opacity-50"
                onClick={() => setToText("")}
                disabled={!toTokens.length || busy === "send"}
                title="Clear recipients"
              >
                clear
              </button>
              <div className="flex-1" />
              <input
                className="w-[280px] max-w-[45vw] rounded bg-slate-900 border border-slate-800 px-3 py-2 text-sm"
                placeholder="To (optional, comma-separated)…"
                value={toText}
                onChange={(e) => setToText(e.target.value)}
              />
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
                    } else {
                      setShowMentionMenu(false);
                    }
                  } else {
                    setShowMentionMenu(false);
                  }
                }}
                onKeyDown={(e) => {
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
                  {mentionSuggestions.slice(0, 8).map((s) => (
                    <button
                      key={s}
                      className="w-full text-left px-3 py-1.5 text-sm hover:bg-slate-800 text-slate-200"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        // Replace the @... with the selected mention
                        const lastAt = composerText.lastIndexOf("@");
                        if (lastAt >= 0) {
                          const before = composerText.slice(0, lastAt);
                          setComposerText(before + s + " ");
                        }
                        setShowMentionMenu(false);
                        composerRef.current?.focus();
                      }}
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
          className="fixed inset-0 bg-black/60 flex items-start justify-center p-6"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setInboxOpen(false);
          }}
        >
          <div className="w-full max-w-3xl rounded border border-slate-800 bg-slate-950/95 shadow-xl">
            <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold truncate">Inbox · {inboxActorId}</div>
                <div className="text-xs text-slate-400">{inboxMessages.length} unread messages</div>
              </div>
              <div className="flex gap-2">
                <button
                  className="rounded bg-slate-900 border border-slate-800 px-3 py-1 text-sm font-medium disabled:opacity-50"
                  onClick={markInboxAllRead}
                  disabled={!inboxMessages.length || busy.startsWith("inbox")}
                >
                  Mark all read
                </button>
                <button
                  className="rounded bg-slate-200 text-slate-950 px-3 py-1 text-sm font-medium"
                  onClick={() => setInboxOpen(false)}
                >
                  Close
                </button>
              </div>
            </div>

            <div className="max-h-[70vh] overflow-auto p-4 space-y-2">
              {inboxMessages.map((ev, idx) => (
                <div
                  key={String(ev.id || idx)}
                  className="rounded border border-slate-800 bg-slate-950/40 px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-xs text-slate-400 truncate" title={formatFullTime(ev.ts)}>
                      {formatTime(ev.ts)} · {ev.kind || "chat.message"}
                    </div>
                    <div className="text-xs text-slate-500 truncate">{ev.by || "—"}</div>
                  </div>
                  <div className="mt-1 text-sm whitespace-pre-wrap break-words">{formatEventLine(ev)}</div>
                </div>
              ))}
              {!inboxMessages.length ? <div className="text-sm text-slate-400">No unread messages.</div> : null}
            </div>
          </div>
        </div>
      ) : null}

      {showGroupEdit && (
        <div
          className="fixed inset-0 bg-black/60 flex items-start justify-center p-6"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowGroupEdit(false);
          }}
        >
          <div className="w-full max-w-md rounded border border-slate-800 bg-slate-950/95 shadow-xl">
            <div className="px-4 py-3 border-b border-slate-800">
              <div className="text-sm font-semibold">Edit Working Group</div>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Title</label>
                <input
                  className="w-full rounded bg-slate-900 border border-slate-800 px-3 py-2 text-sm"
                  value={editGroupTitle}
                  onChange={(e) => setEditGroupTitle(e.target.value)}
                  placeholder="Group title"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Topic (optional)</label>
                <input
                  className="w-full rounded bg-slate-900 border border-slate-800 px-3 py-2 text-sm"
                  value={editGroupTopic}
                  onChange={(e) => setEditGroupTopic(e.target.value)}
                  placeholder="What is this group working on?"
                />
              </div>
              <div className="flex gap-2 pt-2">
                <button
                  className="rounded bg-slate-200 text-slate-950 px-4 py-2 text-sm font-medium disabled:opacity-50"
                  onClick={updateGroup}
                  disabled={!editGroupTitle.trim() || busy === "group-update"}
                >
                  Save
                </button>
                <button
                  className="rounded bg-slate-800 border border-slate-700 px-4 py-2 text-sm font-medium"
                  onClick={() => setShowGroupEdit(false)}
                >
                  Cancel
                </button>
                <div className="flex-1" />
                <button
                  className="rounded bg-rose-900/50 border border-rose-800 text-rose-200 px-4 py-2 text-sm font-medium hover:bg-rose-900/70 disabled:opacity-50"
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

      {editingActor && (
        <div
          className="fixed inset-0 bg-black/60 flex items-start justify-center p-6 z-50"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setEditingActor(null);
          }}
        >
          <div className="w-full max-w-lg rounded border border-slate-800 bg-slate-950/95 shadow-xl">
            <div className="px-4 py-3 border-b border-slate-800">
              <div className="text-sm font-semibold">Edit Actor: {editingActor.id}</div>
              <div className="text-xs text-slate-400 mt-0.5">
                Change runtime or command, then start the actor with new configuration
              </div>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Title (display name)</label>
                <input
                  className="w-full rounded bg-slate-900 border border-slate-800 px-3 py-2 text-sm"
                  value={editActorTitle}
                  onChange={(e) => setEditActorTitle(e.target.value)}
                  placeholder="Optional display name"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Runtime</label>
                <select
                  className="w-full rounded bg-slate-900 border border-slate-800 px-3 py-2 text-sm"
                  value={editActorRuntime}
                  onChange={(e) => setEditActorRuntime(e.target.value as typeof editActorRuntime)}
                >
                  {[
                    { value: "claude", label: "Claude Code" },
                    { value: "codex", label: "Codex CLI" },
                    { value: "droid", label: "Droid" },
                    { value: "opencode", label: "OpenCode" },
                    { value: "custom", label: "Custom" },
                  ].map((opt) => {
                    const rt = runtimes.find((r) => r.name === opt.value);
                    const available = opt.value === "custom" || (rt?.available ?? false);
                    return (
                      <option key={opt.value} value={opt.value} disabled={!available}>
                        {opt.label}{!available && rt ? " (not installed)" : ""}
                      </option>
                    );
                  })}
                </select>
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">
                  Command
                  {editActorRuntime !== "custom" && (
                    <span className="text-slate-500 ml-1">(auto-filled from runtime, editable)</span>
                  )}
                </label>
                <input
                  className="w-full rounded bg-slate-900 border border-slate-800 px-3 py-2 text-sm font-mono"
                  value={editActorCommand}
                  onChange={(e) => setEditActorCommand(e.target.value)}
                  placeholder={editActorRuntime === "custom" ? "Enter command..." : RUNTIME_DEFAULTS[editActorRuntime] || ""}
                />
                <div className="text-xs text-slate-500 mt-1">
                  You can customize the command, e.g., add flags like <code className="bg-slate-800 px-1 rounded">--model sonnet</code>
                </div>
              </div>
              <div className="flex gap-2 pt-2">
                <button
                  className="rounded bg-emerald-500 text-slate-950 px-4 py-2 text-sm font-medium disabled:opacity-50"
                  onClick={updateActor}
                  disabled={busy === "actor-update" || (!editActorCommand.trim() && editActorRuntime === "custom")}
                >
                  Save & Close
                </button>
                <button
                  className="rounded bg-slate-800 border border-slate-700 px-4 py-2 text-sm font-medium"
                  onClick={() => setEditingActor(null)}
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
