import React, { useEffect, useMemo, useRef, useState } from "react";
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
};

type Actor = {
  id: string;
  role?: string;
  title?: string;
  enabled?: boolean;
  command?: string[];
  updated_at?: string;
};

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
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
  const k = String(ev.kind || "event");
  const by = ev.by ? ` by ${ev.by}` : "";
  return `${k}${by}`;
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
  const [newActorTitle, setNewActorTitle] = useState("");
  const [newActorCommand, setNewActorCommand] = useState("");
  const [inboxOpen, setInboxOpen] = useState(false);
  const [inboxActorId, setInboxActorId] = useState("");
  const [inboxMessages, setInboxMessages] = useState<LedgerEvent[]>([]);
  const [termActorId, setTermActorId] = useState("");

  const eventSourceRef = useRef<EventSource | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const projectRoot = useMemo(() => getProjectRoot(groupDoc), [groupDoc]);
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

  async function loadGroup(groupId: string) {
    setGroupDoc(null);
    setEvents([]);
    setActors([]);
    setErrorMsg("");

    const show = await apiJson<{ group: GroupDoc }>(`/api/v1/groups/${encodeURIComponent(groupId)}`);
    if (show.ok) setGroupDoc(show.result.group);

    const tail = await apiJson<{ events: LedgerEvent[] }>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/tail?lines=120`,
    );
    if (tail.ok) setEvents(tail.result.events || []);

    const a = await apiJson<{ actors: Actor[] }>(`/api/v1/groups/${encodeURIComponent(groupId)}/actors`);
    if (a.ok) setActors(a.result.actors || []);
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

  async function sendMessage() {
    const txt = composerText.trim();
    if (!txt || !selectedGroupId) return;
    setBusy("send");
    try {
      setErrorMsg("");
      const to = toTokens;
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/send`, {
        method: "POST",
        body: JSON.stringify({ text: txt, by: "user", to, path: "" }),
      });
      if (!resp.ok) {
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setComposerText("");
      await loadGroup(selectedGroupId);
    } finally {
      setBusy("");
    }
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
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
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
      } else {
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
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
    const actorId = newActorId.trim();
    if (!actorId) return;
    setBusy("actor-add");
    try {
      setErrorMsg("");
      const resp = await apiJson(`/api/v1/groups/${encodeURIComponent(selectedGroupId)}/actors`, {
        method: "POST",
        body: JSON.stringify({
          actor_id: actorId,
          role: newActorRole,
          title: newActorTitle.trim(),
          command: newActorCommand,
          env: {},
          default_scope_key: "",
          by: "user",
        }),
      });
      if (!resp.ok) {
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      setShowAddActor(false);
      setNewActorId("");
      setNewActorTitle("");
      setNewActorCommand("");
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
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
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
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await refreshActors();
      await loadGroup(selectedGroupId);
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
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
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
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
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
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
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
        setErrorMsg(`${resp.error.code}: ${resp.error.message}`);
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
            {!groups.length && <div className="text-sm text-slate-400 p-3">No groups yet.</div>}
          </div>
        </aside>

        <main className="h-full flex flex-col">
          <header className="border-b border-slate-800 bg-slate-950/30 px-4 py-3">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-sm font-semibold truncate">
                  {groupDoc?.title || (selectedGroupId ? selectedGroupId : "—")}
                </div>
                <div className="text-xs text-slate-400 truncate">
                  Project root: {projectRoot ? projectRoot : "(not set)"}
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
              <div className="text-xs text-slate-400 mr-1">Actors</div>
              {actors.map((a) => (
                <div
                  key={a.id}
                  className="flex items-center gap-2 rounded border border-slate-800 bg-slate-950/40 px-2 py-1"
                >
                  <div className={classNames("h-2 w-2 rounded-full", a.enabled ? "bg-emerald-400" : "bg-slate-600")} />
                  <div className="text-xs font-medium">{a.id}</div>
                  <div className="text-[10px] text-slate-400">{a.role || "peer"}</div>
                  <button
                    className="text-[10px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 disabled:opacity-50"
                    onClick={() => openInbox(a.id)}
                    disabled={busy.startsWith("actor-") || busy.startsWith("inbox")}
                    title="Open unread inbox for this actor"
                  >
                    inbox
                  </button>
                  <button
                    className="text-[10px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800/60 disabled:opacity-50"
                    onClick={() => setTermActorId(String(a.id || ""))}
                    disabled={!selectedGroupId || busy.startsWith("actor-") || busy.startsWith("inbox")}
                    title="Open web terminal"
                  >
                    term
                  </button>
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
              <div className="mt-2 grid grid-cols-5 gap-2">
                <input
                  className="col-span-1 rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                  placeholder="actor id (e.g. peer-a)"
                  value={newActorId}
                  onChange={(e) => setNewActorId(e.target.value)}
                />
                <select
                  className="col-span-1 rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                  value={newActorRole}
                  onChange={(e) => setNewActorRole(e.target.value === "foreman" ? "foreman" : "peer")}
                >
                  <option value="peer">peer</option>
                  <option value="foreman">foreman</option>
                </select>
                <input
                  className="col-span-1 rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                  placeholder="title (optional)"
                  value={newActorTitle}
                  onChange={(e) => setNewActorTitle(e.target.value)}
                />
                <input
                  className="col-span-2 rounded bg-slate-900 border border-slate-800 px-2 py-1 text-sm"
                  placeholder="command (e.g. codex)"
                  value={newActorCommand}
                  onChange={(e) => setNewActorCommand(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addActor();
                  }}
                />
                <div className="col-span-5 flex gap-2">
                  <button
                    className="rounded bg-slate-200 text-slate-950 px-3 py-1 text-sm font-medium disabled:opacity-50"
                    onClick={addActor}
                    disabled={!newActorId.trim() || busy === "actor-add"}
                  >
                    Add
                  </button>
                  <button
                    className="rounded bg-slate-900 border border-slate-800 px-3 py-1 text-sm font-medium"
                    onClick={() => setShowAddActor(false)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}
          </header>

          <section className="flex-1 overflow-auto px-4 py-3">
            <div className="space-y-2">
              {events.map((ev, idx) => (
                <div
                  key={String(ev.id || idx)}
                  className="rounded border border-slate-800 bg-slate-950/40 px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-xs text-slate-400 truncate">
                      {ev.ts || "—"} · {ev.kind || "event"}
                    </div>
                    <div className="text-xs text-slate-500 truncate">{ev.by || "—"}</div>
                  </div>
                  <div className="mt-1 text-sm whitespace-pre-wrap break-words">{formatEventLine(ev)}</div>
                </div>
              ))}
              <div ref={bottomRef} />
              {!events.length && <div className="text-sm text-slate-400">No events yet.</div>}
            </div>
          </section>

          <footer className="border-t border-slate-800 bg-slate-950/30 px-4 py-3">
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

            <div className="flex gap-2">
              <input
                className="w-full rounded bg-slate-900 border border-slate-800 px-3 py-2 text-sm"
                placeholder="Message…"
                value={composerText}
                onChange={(e) => setComposerText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") sendMessage();
                }}
              />
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
                    <div className="text-xs text-slate-400 truncate">
                      {ev.ts || "—"} · {ev.kind || "chat.message"}
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
