// API service layer (centralized API calls).
import type {
  GroupMeta,
  GroupDoc,
  LedgerEvent,
  Actor,
  RuntimeInfo,
  GroupContext,
  GroupSettings,
  Task,
  DirItem,
  DirSuggestion,
  IMConfig,
  IMStatus,
  IMPlatform,
} from "../types";

// ============ Token management ============

// Extract and cache token from URL for dev mode (Vite doesn't proxy /ui/ to backend)
let cachedToken: string | null = null;

function getAuthToken(): string | null {
  if (cachedToken !== null) return cachedToken || null;

  // Check URL param first
  const urlParams = new URLSearchParams(window.location.search);
  const urlToken = urlParams.get("token");
  if (urlToken) {
    cachedToken = urlToken;
    // Store in sessionStorage for page refreshes (dev mode only)
    try {
      sessionStorage.setItem("cccc_dev_token", urlToken);
    } catch {
      // Storage might be unavailable (private mode / disabled cookies).
      void 0;
    }
    return urlToken;
  }

  // Fallback to sessionStorage (for dev mode page refreshes)
  try {
    const stored = sessionStorage.getItem("cccc_dev_token");
    if (stored) {
      cachedToken = stored;
      return stored;
    }
  } catch {
    // Storage might be unavailable (private mode / disabled cookies).
    void 0;
  }

  cachedToken = "";
  return null;
}

function getAuthHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Build URL with token query param (for EventSource which doesn't support headers)
export function withAuthToken(url: string): string {
  const token = getAuthToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

// ============ Base types & helpers ============

export type ApiResponse<T> =
  | { ok: true; result: T; error?: null }
  | { ok: false; result?: unknown; error: { code: string; message: string; details?: unknown } };

// Helper to create a typed error response.
function makeErrorResponse<T>(code: string, message: string): ApiResponse<T> {
  return { ok: false, error: { code, message } };
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...getAuthHeaders(),
        ...(init?.headers || {}),
      },
    });
  } catch (e) {
    // Network error
    return makeErrorResponse("NETWORK_ERROR", e instanceof Error ? e.message : "Network request failed");
  }

  const text = await resp.text();
  if (!text) {
    // Empty body but 2xx
    if (resp.ok) {
      return { ok: true, result: {} as T };
    }
    return makeErrorResponse("EMPTY_RESPONSE", `Server returned ${resp.status} with empty body`);
  }

  try {
    const data = JSON.parse(text);
    return data as ApiResponse<T>;
  } catch {
    // JSON parse error
    return makeErrorResponse("PARSE_ERROR", `Invalid JSON response: ${text.slice(0, 100)}`);
  }
}

export async function apiForm<T>(path: string, form: FormData, init?: RequestInit): Promise<ApiResponse<T>> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      ...(init || {}),
      method: init?.method || "POST",
      body: form,
      headers: {
        ...getAuthHeaders(),
        ...(init?.headers || {}),
      },
    });
  } catch (e) {
    return makeErrorResponse("NETWORK_ERROR", e instanceof Error ? e.message : "Network request failed");
  }

  const text = await resp.text();
  if (!text) {
    if (resp.ok) {
      return { ok: true, result: {} as T };
    }
    return makeErrorResponse("EMPTY_RESPONSE", `Server returned ${resp.status} with empty body`);
  }

  try {
    const data = JSON.parse(text);
    return data as ApiResponse<T>;
  } catch {
    return makeErrorResponse("PARSE_ERROR", `Invalid JSON response: ${text.slice(0, 100)}`);
  }
}

// ============ Groups ============

export async function fetchGroups() {
  return apiJson<{ groups: GroupMeta[] }>("/api/v1/groups");
}

export async function fetchPing() {
  return apiJson<{ home: string; daemon: unknown; version: string; web?: { mode?: string; read_only?: boolean } }>(
    "/api/v1/ping"
  );
}

export async function fetchLanIp() {
  return apiJson<{ lan_ip: string | null }>("/api/v1/server/lan-ip");
}

export async function fetchGroup(groupId: string) {
  return apiJson<{ group: GroupDoc }>(`/api/v1/groups/${encodeURIComponent(groupId)}`);
}

export async function createGroup(title: string, topic: string = "") {
  return apiJson<{ group_id: string }>("/api/v1/groups", {
    method: "POST",
    body: JSON.stringify({ title, topic, by: "user" }),
  });
}

export async function createGroupFromTemplate(
  path: string,
  title: string,
  topic: string,
  file: File
) {
  const form = new FormData();
  form.append("path", path);
  form.append("title", title);
  form.append("topic", topic || "");
  form.append("by", "user");
  form.append("file", file);
  return apiForm<{ group_id: string }>("/api/v1/groups/from_template", form);
}

export async function previewTemplate(file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiForm<{ template: unknown }>("/api/v1/templates/preview", form);
}

export async function updateGroup(groupId: string, title: string, topic: string) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}`, {
    method: "PUT",
    body: JSON.stringify({ title: title.trim(), topic: topic.trim(), by: "user" }),
  });
}

export async function deleteGroup(groupId: string) {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}?confirm=${encodeURIComponent(groupId)}&by=user`,
    { method: "DELETE" }
  );
}

export async function attachScope(groupId: string, path: string) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/attach`, {
    method: "POST",
    body: JSON.stringify({ path, by: "user" }),
  });
}

export async function startGroup(groupId: string) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/start?by=user`, {
    method: "POST",
  });
}

export async function stopGroup(groupId: string) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/stop?by=user`, {
    method: "POST",
  });
}

export async function setGroupState(groupId: string, state: "active" | "idle" | "paused") {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}/state?state=${encodeURIComponent(state)}&by=user`,
    { method: "POST" }
  );
}

// ============ Group Templates (Replace) ============

export async function exportGroupTemplate(groupId: string) {
  return apiJson<{ template: string; filename: string }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/template/export`
  );
}

export async function previewGroupTemplate(groupId: string, file: File) {
  const form = new FormData();
  form.append("by", "user");
  form.append("file", file);
  return apiForm<{ template: unknown; diff: unknown; scope_root: string }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/template/preview_upload`,
    form
  );
}

export async function importGroupTemplateReplace(groupId: string, file: File) {
  const form = new FormData();
  form.append("confirm", groupId);
  form.append("by", "user");
  form.append("file", file);
  return apiForm<{ applied: boolean }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/template/import_replace`,
    form
  );
}

// ============ Ledger ============

export async function fetchLedgerTail(groupId: string, lines = 120) {
  const params = new URLSearchParams({
    kind: "chat",
    limit: String(lines),
    with_read_status: "true",
    with_ack_status: "true",
  });
  return apiJson<{ events: LedgerEvent[]; has_more: boolean; count: number }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/search?${params.toString()}`
  );
}

export async function fetchOlderMessages(
  groupId: string,
  beforeEventId: string,
  limit = 50
) {
  const params = new URLSearchParams({
    kind: "chat",
    before: beforeEventId,
    limit: String(limit),
    with_read_status: "true",
    with_ack_status: "true",
  });
  return apiJson<{ events: LedgerEvent[]; has_more: boolean; count: number }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/search?${params.toString()}`
  );
}

export async function fetchMessageWindow(
  groupId: string,
  centerEventId: string,
  opts?: { before?: number; after?: number }
) {
  const params = new URLSearchParams({
    kind: "chat",
    center: centerEventId,
    before: String(opts?.before ?? 30),
    after: String(opts?.after ?? 30),
    with_read_status: "true",
    with_ack_status: "true",
  });
  return apiJson<{
    center_id: string;
    center_index: number;
    events: LedgerEvent[];
    has_more_before: boolean;
    has_more_after: boolean;
    count: number;
  }>(`/api/v1/groups/${encodeURIComponent(groupId)}/ledger/window?${params.toString()}`);
}

export async function searchChatMessages(
  groupId: string,
  q: string,
  opts?: { limit?: number; before?: string; after?: string }
) {
  const params = new URLSearchParams({
    kind: "chat",
    q: q || "",
    limit: String(opts?.limit ?? 50),
    with_read_status: "true",
    with_ack_status: "true",
  });
  if (opts?.before) params.set("before", opts.before);
  if (opts?.after) params.set("after", opts.after);
  return apiJson<{ events: LedgerEvent[]; has_more: boolean; count: number }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/search?${params.toString()}`
  );
}

// ============ Actors ============

export async function fetchActors(groupId: string, includeUnread = true) {
  const url = includeUnread
    ? `/api/v1/groups/${encodeURIComponent(groupId)}/actors?include_unread=true`
    : `/api/v1/groups/${encodeURIComponent(groupId)}/actors`;
  return apiJson<{ actors: Actor[] }>(url);
}

export async function addActor(
  groupId: string,
  actorId: string,
  role: "peer" | "foreman",
  runtime: string,
  command: string,
  envPrivate?: Record<string, string>
) {
  return apiJson<{ actor: Actor }>(`/api/v1/groups/${encodeURIComponent(groupId)}/actors`, {
    method: "POST",
    body: JSON.stringify({
      actor_id: actorId,
      role,
      runner: "pty",
      runtime,
      command,
      env: {},
      env_private: envPrivate && Object.keys(envPrivate).length ? envPrivate : undefined,
      default_scope_key: "",
      by: "user",
    }),
  });
}

export async function updateActor(
  groupId: string,
  actorId: string,
  runtime: string,
  command: string,
  title: string
) {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actorId)}`,
    {
      method: "POST",
      body: JSON.stringify({
        runtime,
        command: command.trim(),
        title: title.trim(),
        by: "user",
      }),
    }
  );
}

export async function removeActor(groupId: string, actorId: string) {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actorId)}?by=user`,
    { method: "DELETE" }
  );
}

export async function startActor(groupId: string, actorId: string) {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actorId)}/start`,
    { method: "POST" }
  );
}

export async function stopActor(groupId: string, actorId: string) {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actorId)}/stop`,
    { method: "POST" }
  );
}

export async function restartActor(groupId: string, actorId: string) {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actorId)}/restart?by=user`,
    { method: "POST" }
  );
}

export async function fetchActorPrivateEnvKeys(groupId: string, actorId: string) {
  return apiJson<{ group_id: string; actor_id: string; keys: string[] }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actorId)}/env_private?by=user`
  );
}

export async function updateActorPrivateEnv(
  groupId: string,
  actorId: string,
  setVars: Record<string, string>,
  unsetKeys: string[],
  clear: boolean
) {
  return apiJson<{ group_id: string; actor_id: string; keys: string[] }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actorId)}/env_private`,
    {
      method: "POST",
      body: JSON.stringify({ by: "user", set: setVars, unset: unsetKeys, clear }),
    }
  );
}

// ============ Context & Settings ============

export async function fetchContext(groupId: string) {
  return apiJson<GroupContext>(`/api/v1/groups/${encodeURIComponent(groupId)}/context`);
}

export async function fetchTasks(groupId: string) {
  return apiJson<{ tasks: Task[] }>(`/api/v1/groups/${encodeURIComponent(groupId)}/tasks`);
}

export async function contextSync(
  groupId: string,
  ops: Array<Record<string, unknown>>,
  dryRun: boolean = false
) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/context`, {
    method: "POST",
    body: JSON.stringify({ ops, by: "user", dry_run: dryRun }),
  });
}

export async function updateVision(groupId: string, vision: string) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/context`, {
    method: "POST",
    body: JSON.stringify({ ops: [{ op: "vision.update", vision }], by: "user" }),
  });
}

export async function updateSketch(groupId: string, sketch: string) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/context`, {
    method: "POST",
    body: JSON.stringify({ ops: [{ op: "sketch.update", sketch }], by: "user" }),
  });
}

export async function fetchSettings(groupId: string) {
  return apiJson<{ settings: GroupSettings }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/settings`
  );
}

export async function updateSettings(groupId: string, settings: Partial<GroupSettings>) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/settings`, {
    method: "PUT",
    body: JSON.stringify({ ...settings, by: "user" }),
  });
}

// ============ Inbox ============

export async function fetchInbox(groupId: string, actorId: string, limit = 200) {
  return apiJson<{ messages: LedgerEvent[] }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/inbox/${encodeURIComponent(actorId)}?by=user&limit=${limit}`
  );
}

export async function markInboxRead(groupId: string, actorId: string, eventId: string) {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}/inbox/${encodeURIComponent(actorId)}/read`,
    {
      method: "POST",
      body: JSON.stringify({ event_id: eventId, by: "user" }),
    }
  );
}

// ============ Messages ============

export async function sendMessage(
  groupId: string,
  text: string,
  to: string[],
  files?: File[],
  priority: "normal" | "attention" = "normal"
) {
  if (files && files.length > 0) {
    const form = new FormData();
    form.append("by", "user");
    form.append("text", text);
    form.append("to_json", JSON.stringify(to));
    form.append("path", "");
    form.append("priority", priority);
    for (const f of files) form.append("files", f);
    return apiForm(`/api/v1/groups/${encodeURIComponent(groupId)}/send_upload`, form);
  }
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/send`, {
    method: "POST",
    body: JSON.stringify({ text, by: "user", to, path: "", priority }),
  });
}

export async function replyMessage(
  groupId: string,
  text: string,
  to: string[],
  replyTo: string,
  files?: File[],
  priority: "normal" | "attention" = "normal"
) {
  if (files && files.length > 0) {
    const form = new FormData();
    form.append("by", "user");
    form.append("text", text);
    form.append("to_json", JSON.stringify(to));
    form.append("reply_to", replyTo);
    form.append("priority", priority);
    for (const f of files) form.append("files", f);
    return apiForm(`/api/v1/groups/${encodeURIComponent(groupId)}/reply_upload`, form);
  }
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/reply`, {
    method: "POST",
    body: JSON.stringify({ text, by: "user", to, reply_to: replyTo, priority }),
  });
}

export async function relayMessage(
  dstGroupId: string,
  text: string,
  to: string[],
  src: { groupId: string; eventId: string }
) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(dstGroupId)}/send`, {
    method: "POST",
    body: JSON.stringify({
      text,
      by: "user",
      to,
      path: "",
      priority: "normal",
      src_group_id: src.groupId,
      src_event_id: src.eventId,
    }),
  });
}

export async function sendCrossGroupMessage(
  srcGroupId: string,
  dstGroupId: string,
  text: string,
  to: string[],
  priority: "normal" | "attention" = "normal"
) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(srcGroupId)}/send_cross_group`, {
    method: "POST",
    body: JSON.stringify({
      text,
      by: "user",
      dst_group_id: dstGroupId,
      to,
      priority,
    }),
  });
}

export async function ackMessage(groupId: string, eventId: string) {
  return apiJson(`/api/v1/groups/${encodeURIComponent(groupId)}/events/${encodeURIComponent(eventId)}/ack`, {
    method: "POST",
    body: JSON.stringify({ by: "user" }),
  });
}

// ============ Runtimes ============

export async function fetchRuntimes() {
  return apiJson<{ runtimes: RuntimeInfo[]; available: string[] }>("/api/v1/runtimes");
}

// ============ File System ============

export async function fetchDirSuggestions() {
  return apiJson<{ suggestions: DirSuggestion[] }>("/api/v1/fs/recent");
}

export async function fetchDirContents(path: string) {
  return apiJson<{ path: string; parent: string | null; items: DirItem[] }>(
    `/api/v1/fs/list?path=${encodeURIComponent(path)}`
  );
}

export async function resolveScopeRoot(path: string) {
  return apiJson<{ path: string; scope_root: string; scope_key: string; git_remote: string }>(
    `/api/v1/fs/scope_root?path=${encodeURIComponent(path)}`
  );
}

// ============ IM Bridge ============

export async function fetchIMStatus(groupId: string) {
  return apiJson<IMStatus>(`/api/im/status?group_id=${encodeURIComponent(groupId)}`);
}

export async function fetchIMConfig(groupId: string) {
  return apiJson<{ im: IMConfig | null }>(`/api/im/config?group_id=${encodeURIComponent(groupId)}`);
}

export async function setIMConfig(
  groupId: string,
  platform: IMPlatform,
  botTokenEnv: string,
  appTokenEnv?: string,
  extra?: {
    feishu_domain?: string;
    feishu_app_id?: string;
    feishu_app_secret?: string;
    dingtalk_app_key?: string;
    dingtalk_app_secret?: string;
    dingtalk_robot_code?: string;
  }
) {
  const body: Record<string, unknown> = {
    group_id: groupId,
    platform,
  };

  // Telegram/Slack/Discord use bot_token_env
  if (platform === "telegram" || platform === "slack" || platform === "discord") {
    body.bot_token_env = botTokenEnv;
    if (platform === "slack" && appTokenEnv) {
      body.app_token_env = appTokenEnv;
    }
  }

  // Feishu uses app_id and app_secret
  if (platform === "feishu" && extra) {
    body.feishu_domain = extra.feishu_domain;
    body.feishu_app_id = extra.feishu_app_id;
    body.feishu_app_secret = extra.feishu_app_secret;
  }

  // DingTalk uses app_key and app_secret
  if (platform === "dingtalk" && extra) {
    body.dingtalk_app_key = extra.dingtalk_app_key;
    body.dingtalk_app_secret = extra.dingtalk_app_secret;
    body.dingtalk_robot_code = extra.dingtalk_robot_code;
  }

  return apiJson("/api/im/set", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function unsetIMConfig(groupId: string) {
  return apiJson("/api/im/unset", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId }),
  });
}

export async function startIMBridge(groupId: string) {
  return apiJson("/api/im/start", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId }),
  });
}

export async function stopIMBridge(groupId: string) {
  return apiJson("/api/im/stop", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId }),
  });
}

// ============ News Agent ============

export interface NewsAgentStatus {
  group_id: string;
  kind?: string;
  enabled: boolean;
  running: boolean;
  pid: number;
  interests: string;
  schedule: string;
}

export async function fetchNewsStatus(groupId: string) {
  return apiJson<NewsAgentStatus>(`/api/news/status?group_id=${encodeURIComponent(groupId)}`);
}

export async function startNewsAgent(groupId: string, interests = "AI,科技,编程", schedule = "8,11,14,17,20") {
  return apiJson<{ group_id: string; pid: number }>("/api/news/start", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId, interests, schedule }),
  });
}

export async function stopNewsAgent(groupId: string) {
  return apiJson<{ group_id: string; stopped: number }>("/api/news/stop", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId }),
  });
}

export async function fetchMarketStatus(groupId: string) {
  return apiJson<NewsAgentStatus>(`/api/market/status?group_id=${encodeURIComponent(groupId)}`);
}

export async function startMarketAgent(groupId: string, interests = "股市,美股,A股,港股,宏观,财报", schedule = "9,12,15,18,22") {
  return apiJson<{ group_id: string; pid: number }>("/api/market/start", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId, interests, schedule }),
  });
}

export async function stopMarketAgent(groupId: string) {
  return apiJson<{ group_id: string; stopped: number }>("/api/market/stop", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId }),
  });
}

export async function fetchAiLongStatus(groupId: string) {
  return apiJson<NewsAgentStatus>(`/api/ai_long/status?group_id=${encodeURIComponent(groupId)}`);
}

export interface AILongScript {
  key: string;
  title: string;
  aliases: string[];
  sections: number;
}

export async function fetchAiLongScripts() {
  return apiJson<{ scripts: AILongScript[] }>("/api/ai_long/scripts");
}

export async function startAiLongAgent(groupId: string, interests = "CCCC,框架,多Agent,协作,消息总线,语音播报", schedule = "10,16,21") {
  return apiJson<{ group_id: string; pid: number }>("/api/ai_long/start", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId, interests, schedule }),
  });
}

export async function stopAiLongAgent(groupId: string) {
  return apiJson<{ group_id: string; stopped: number }>("/api/ai_long/stop", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId }),
  });
}

export interface AILongPreloadStatus {
  group_id: string;
  status: "idle" | "queued" | "preparing" | "synthesizing" | "ready" | "error";
  running: boolean;
  title: string;
  interests: string;
  script_key: string;
  topic: string;
  message: string;
  script_hash: string;
  script_chars: number;
  total_chunks: number;
  completed_chunks: number;
  manifest_ready: boolean;
  error?: string;
  updated_at?: number;
}

export interface AILongPreloadChunkMeta {
  index: number;
  text: string;
  media_type: string;
  bytes: number;
}

export interface AILongPreloadManifest {
  group_id: string;
  title: string;
  interests: string;
  script_key: string;
  topic: string;
  script_hash: string;
  script_chars: number;
  chunks: AILongPreloadChunkMeta[];
}

export async function fetchAiLongPreloadStatus(groupId: string) {
  return apiJson<AILongPreloadStatus>(`/api/ai_long/preload/status?group_id=${encodeURIComponent(groupId)}`);
}

export async function startAiLongPreload(
  groupId: string,
  interests = "CCCC,框架,多Agent,协作,消息总线,语音播报",
  force = false,
  opts?: { scriptKey?: string; topic?: string }
) {
  return apiJson<{ group_id: string; started: boolean }>("/api/ai_long/preload/start", {
    method: "POST",
    body: JSON.stringify({
      group_id: groupId,
      interests,
      force,
      script_key: opts?.scriptKey || "",
      topic: opts?.topic || "",
    }),
  });
}

export async function fetchAiLongPreloadManifest(groupId: string) {
  return apiJson<AILongPreloadManifest>(`/api/ai_long/preload/manifest?group_id=${encodeURIComponent(groupId)}`);
}

export type AudioBlobResponse =
  | { ok: true; blob: Blob; contentType: string }
  | { ok: false; error: { code: string; message: string } };

export async function fetchAiLongPreloadChunk(
  groupId: string,
  index: number,
  signal?: AbortSignal
): Promise<AudioBlobResponse> {
  let resp: Response;
  try {
    resp = await fetch(
      `/api/ai_long/preload/chunk?group_id=${encodeURIComponent(groupId)}&index=${encodeURIComponent(String(index))}`,
      {
        headers: getAuthHeaders(),
        signal,
      }
    );
  } catch (e) {
    return {
      ok: false,
      error: {
        code: "NETWORK_ERROR",
        message: e instanceof Error ? e.message : "Network request failed",
      },
    };
  }
  if (!resp.ok) {
    let code = "chunk_fetch_failed";
    let message = `chunk fetch failed (${resp.status})`;
    try {
      const data = await resp.json();
      if (data && typeof data === "object") {
        const err = (data as { error?: { code?: string; message?: string } }).error;
        if (err?.code) code = String(err.code);
        if (err?.message) message = String(err.message);
      }
    } catch {
      // ignore
    }
    return { ok: false, error: { code, message } };
  }
  const contentType = resp.headers.get("content-type") || "audio/wav";
  const blob = await resp.blob();
  return { ok: true, blob, contentType };
}

export async function fetchHorrorStatus(groupId: string) {
  return apiJson<NewsAgentStatus>(`/api/horror/status?group_id=${encodeURIComponent(groupId)}`);
}

export async function startHorrorAgent(groupId: string, interests = "深夜,公寓,都市传说,悬疑,心理惊悚", schedule = "21,23,1") {
  return apiJson<{ group_id: string; pid: number }>("/api/horror/start", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId, interests, schedule }),
  });
}

export async function stopHorrorAgent(groupId: string) {
  return apiJson<{ group_id: string; stopped: number }>("/api/horror/stop", {
    method: "POST",
    body: JSON.stringify({ group_id: groupId }),
  });
}

// ============ TTS Providers ============

export type TTSEngine = "browser" | "gpt_sovits_v4";

export type TTSStyle = "general" | "news" | "market" | "ai_long" | "horror";

export interface TTSProviderStatus {
  engine: TTSEngine;
  label: string;
  available: boolean;
  endpoint?: string;
}

export async function fetchTTSProviders() {
  return apiJson<{ default_engine: TTSEngine; providers: TTSProviderStatus[] }>("/api/tts/providers");
}

export interface TTSSynthesizeRequest {
  text: string;
  style: TTSStyle;
  lang: string;
  engine: Exclude<TTSEngine, "browser">;
  rate?: number;
  pitch?: number;
  volume?: number;
}

export type TTSAudioResponse =
  | { ok: true; blob: Blob; contentType: string }
  | { ok: false; error: { code: string; message: string } };

export async function synthesizeTTSAudio(
  req: TTSSynthesizeRequest,
  signal?: AbortSignal
): Promise<TTSAudioResponse> {
  let resp: Response;
  try {
    resp = await fetch("/api/tts/synthesize", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...getAuthHeaders(),
      },
      body: JSON.stringify(req),
      signal,
    });
  } catch (e) {
    return {
      ok: false,
      error: {
        code: "NETWORK_ERROR",
        message: e instanceof Error ? e.message : "Network request failed",
      },
    };
  }

  if (!resp.ok) {
    let code = "tts_failed";
    let message = `TTS failed (${resp.status})`;
    try {
      const data = await resp.json();
      if (data && typeof data === "object") {
        const err = (data as { error?: { code?: string; message?: string } }).error;
        if (err?.code) code = String(err.code);
        if (err?.message) message = String(err.message);
      }
    } catch {
      // ignore body parse errors
    }
    return { ok: false, error: { code, message } };
  }

  const contentType = resp.headers.get("content-type") || "audio/wav";
  const blob = await resp.blob();
  return { ok: true, blob, contentType };
}

// ============ Observability ============

export interface Observability {
  developer_mode?: boolean;
  log_level?: string;
  terminal_transcript?: {
    enabled?: boolean;
    per_actor_bytes?: number;
    persist?: boolean;
    strip_ansi?: boolean;
  };
  terminal_ui?: {
    scrollback_lines?: number;
  };
}

export async function fetchObservability() {
  return apiJson<{ observability: Observability }>("/api/v1/observability");
}

export async function updateObservability(args: {
  developerMode: boolean;
  logLevel: "INFO" | "DEBUG";
  terminalTranscriptPerActorBytes?: number;
  terminalUiScrollbackLines?: number;
}) {
  return apiJson<{ observability: Observability }>("/api/v1/observability", {
    method: "PUT",
    body: JSON.stringify({
      by: "user",
      developer_mode: args.developerMode,
      log_level: args.logLevel,
      terminal_transcript_per_actor_bytes: args.terminalTranscriptPerActorBytes,
      terminal_ui_scrollback_lines: args.terminalUiScrollbackLines,
    }),
  });
}

// ============ Debug ============

export async function fetchDebugSnapshot(groupId: string) {
  return apiJson<Record<string, unknown>>(`/api/v1/debug/snapshot?group_id=${encodeURIComponent(groupId)}`);
}

export async function fetchTerminalTail(
  groupId: string,
  actorId: string,
  maxChars = 8000,
  stripAnsi = true,
  compact = true
) {
  const params = new URLSearchParams({
    actor_id: actorId,
    max_chars: String(maxChars),
    strip_ansi: String(stripAnsi),
    compact: String(compact),
  });
  return apiJson<{ text: string; hint: string }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/terminal/tail?${params.toString()}`
  );
}

export async function clearTerminalTail(groupId: string, actorId: string) {
  return apiJson(
    `/api/v1/groups/${encodeURIComponent(groupId)}/terminal/clear?actor_id=${encodeURIComponent(actorId)}`,
    { method: "POST" }
  );
}

export async function fetchLogTail(component: "daemon" | "web" | "im", groupId: string, lines = 200) {
  const params = new URLSearchParams({
    component,
    group_id: groupId,
    lines: String(lines),
  });
  return apiJson<{ lines: string[] }>(`/api/v1/debug/tail_logs?${params.toString()}`);
}

export async function clearLogs(component: "daemon" | "web" | "im", groupId: string) {
  return apiJson("/api/v1/debug/clear_logs", {
    method: "POST",
    body: JSON.stringify({ component, group_id: groupId, by: "user" }),
  });
}
