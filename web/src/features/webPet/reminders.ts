import type { PetReminder } from "./types";
export type { PetReminder } from "./types";

export interface ReminderTaskInput {
  taskId: string;
  title: string;
  assignee?: string;
  waitingOn?: string;
  status?: string;
}

export interface ReminderActorInput {
  actorId: string;
  role?: string;
  title?: string;
  enabled?: boolean;
  running: boolean;
  idleSeconds: number;
  activeTaskId?: string;
}

export interface ReminderEventInput {
  eventId: string;
  kind: string;
  by: string;
  text: string;
  to: string[];
  replyRequired: boolean;
  acked: boolean;
  replied: boolean;
}

export interface ProjectPetRemindersInput {
  groupId: string;
  actors: ReminderActorInput[];
  events: ReminderEventInput[];
}

type ReminderDraft = PetReminder & {
  sortIndex: number;
};

const ACTOR_DOWN_PRIORITY = 90;
const REPLY_REQUIRED_PRIORITY = 80;
const MENTION_PRIORITY = 70;

function truncate(text: string, maxChars = 96): string {
  const cleaned = text.trim().replace(/\s+/g, " ");
  if (cleaned.length <= maxChars) return cleaned;
  return cleaned.slice(0, maxChars - 1) + "…";
}

function trimTrailingSentencePunctuation(text: string): string {
  return text.replace(/[。！？.!?]+$/u, "").trim();
}

function isUserTarget(target: string): boolean {
  const normalized = String(target || "").trim().toLowerCase();
  return normalized === "user" || normalized === "@user";
}

function isLowSignalMessage(text: string): boolean {
  const normalized = trimTrailingSentencePunctuation(
    String(text || "").trim().toLowerCase(),
  );
  return [
    "已完成",
    "done",
    "ok",
    "okay",
    "收到",
    "了解",
    "了解しました",
    "承知しました",
    "完了",
    "完了しました",
  ].includes(normalized);
}

function containsInternalControlSignal(text: string): boolean {
  const normalized = String(text || "").trim().toLowerCase();
  if (!normalized) return false;

  return [
    "help_nudge",
    "actor_idle",
    "auto_idle",
    "silence_check",
    "keepalive",
    "cccc_help",
    "agent_state",
    "system.notify",
  ].some((token) => normalized.includes(token));
}

function isWorkflowAdvancingSuggestion(text: string): boolean {
  const normalized = String(text || "").trim().toLowerCase();
  if (!normalized) return false;

  if (
    [
      "no-delta",
      "recall",
      "stand-up",
      "standup",
      "15min",
      "15 分钟",
      "对齐",
      "静默",
      "无新增",
      "方向没漂",
      "主线",
      "口径",
      "命中",
      "证据",
      "同步状态",
      "状态更新",
      "无需回复",
      "不用回复",
      "已处理",
      "仅供同步",
      "for sync only",
      "no update",
      "差分なし",
      "追加なし",
      "変更なし",
      "共有のみ",
      "同期のみ",
      "参考まで",
      "返信不要",
      "対応不要",
    ].some((token) => normalized.includes(token))
  ) {
    return false;
  }

  return [
    "请",
    "先",
    "需要",
    "麻烦",
    "回复",
    "回一下",
    "确认",
    "处理",
    "跟进",
    "同步",
    "发给用户",
    "补上",
    "修复",
    "检查",
    "更新",
    "安排",
    "联系",
    "review",
    "reply",
    "confirm",
    "follow up",
    "fix",
    "update",
    "check",
    "send",
    "お願いします",
    "確認してください",
    "確認お願いします",
    "確認",
    "対応してください",
    "対応",
    "返信",
    "返答",
    "修正",
    "送って",
    "送信",
    "進めて",
    "進めてください",
  ].some((token) => normalized.includes(token));
}

function stripSuggestionLead(text: string): string {
  let next = String(text || "").trim();
  if (!next) return "";

  next = next.replace(/^有个增量更新[，,、：:\s]*/u, "");
  next = next.replace(/^有个补充说明[，,、：:\s]*/u, "说明，");
  next = next.replace(/^补充说明[，,、：:\s]*/u, "说明，");
  next = next.replace(/^補足(?:です)?[，,、：:\s]*/u, "補足、");
  next = next.replace(/^追加共享[，,、：:\s]*/u, "");
  next = next.replace(/^追加共有[，,、：:\s]*/u, "");
  return trimTrailingSentencePunctuation(next);
}

function buildSuggestionText(text: string): string {
  return stripSuggestionLead(String(text || "").replace(/\s+/g, " "));
}

function buildSuggestionPreview(text: string): string {
  return truncate(text, 48);
}

function shouldGenerateMentionSuggestion(text: string): boolean {
  const normalized = String(text || "").trim().toLowerCase();
  if (!normalized) return false;
  if (
    normalized.includes("实现细节") ||
    normalized.includes("implementation detail") ||
    normalized.includes("実装詳細")
  ) {
    return false;
  }
  return true;
}

function buildFingerprint(
  groupId: string,
  kind: PetReminder["kind"],
  stableSourceId: string
): string {
  return `group:${groupId}:${kind}:${stableSourceId}`;
}

function collectActorDownReminders(
  input: ProjectPetRemindersInput,
  startIndex: number,
): ReminderDraft[] {
  const reminders: ReminderDraft[] = [];
  let sortIndex = startIndex;
  for (const actor of input.actors) {
    const actorId = String(actor.actorId || "").trim();
    const role = String(actor.role || "").trim().toLowerCase();
    const title = String(actor.title || actorId || "").trim() || actorId;
    const activeTaskId = String(actor.activeTaskId || "").trim();
    const enabled = actor.enabled !== false;
    const shouldGuard = role === "foreman" || !!activeTaskId;
    if (!actorId || !enabled || actor.running || !shouldGuard) continue;

    reminders.push({
      id: `actor_down:${actorId}`,
      kind: "actor_down",
      priority: role === "foreman" ? ACTOR_DOWN_PRIORITY + 5 : ACTOR_DOWN_PRIORITY,
      summary: "",
      agent: title,
      ephemeral: false,
      source: { actorId, taskId: activeTaskId || undefined },
      fingerprint: buildFingerprint(input.groupId, "actor_down", actorId),
      action: {
        type: "restart_actor",
        groupId: input.groupId,
        actorId,
      },
      sortIndex,
    });
    sortIndex += 1;
  }
  return reminders;
}

export function createMentionReminder(
  groupId: string,
  event: ReminderEventInput | null | undefined
): PetReminder | null {
  if (!event) return null;
  const eventId = String(event.eventId || "").trim();
  const actor = String(event.by || "").trim();
  if (!eventId || !actor || actor === "user") return null;
  if (event.kind !== "chat.message") return null;
  if (event.replyRequired) return null;
  if (!event.to.some((target) => isUserTarget(target))) return null;
  if (isLowSignalMessage(event.text)) return null;

  const suggestion = buildSuggestionText(event.text);
  const hasSuggestion =
    suggestion.length > 0 &&
    !containsInternalControlSignal(event.text) &&
    !containsInternalControlSignal(suggestion) &&
    shouldGenerateMentionSuggestion(suggestion) &&
    isWorkflowAdvancingSuggestion(suggestion);
  if (!hasSuggestion) return null;
  return {
    id: `mention:${eventId}`,
    kind: "mention",
    priority: MENTION_PRIORITY,
    summary: "",
    suggestion: hasSuggestion ? suggestion : undefined,
    suggestionPreview: hasSuggestion ? buildSuggestionPreview(suggestion) : undefined,
    agent: actor,
    ephemeral: true,
    source: { eventId },
    fingerprint: buildFingerprint(groupId, "mention", eventId),
    action: {
      type: "send_suggestion",
      groupId,
      text: suggestion,
      to: [actor],
      replyTo: eventId,
    },
  };
}

function collectMentionReminders(
  input: ProjectPetRemindersInput,
  startIndex: number
): ReminderDraft[] {
  const reminders: ReminderDraft[] = [];
  let sortIndex = startIndex;
  for (const event of input.events) {
    const reminder = createMentionReminder(input.groupId, event);
    if (!reminder) continue;
    reminders.push({ ...reminder, sortIndex });
    sortIndex += 1;
  }
  return reminders;
}

function collectReplyRequiredReminders(
  input: ProjectPetRemindersInput,
  startIndex: number
): ReminderDraft[] {
  const reminders: ReminderDraft[] = [];
  let sortIndex = startIndex;

  for (const event of input.events) {
    const eventId = String(event.eventId || "").trim();
    const actor = String(event.by || "").trim() || "system";
    if (!eventId || event.kind !== "chat.message") continue;
    if (!event.replyRequired || event.acked || event.replied) continue;

    const suggestion = buildSuggestionText(event.text);
    const hasSuggestion =
      suggestion.length > 0 &&
      !containsInternalControlSignal(event.text) &&
      !containsInternalControlSignal(suggestion) &&
      isWorkflowAdvancingSuggestion(suggestion);
    if (!hasSuggestion) continue;
    reminders.push({
      id: `reply_required:${eventId}`,
      kind: "reply_required",
      priority: REPLY_REQUIRED_PRIORITY,
      summary: "",
      suggestion: hasSuggestion ? suggestion : undefined,
      suggestionPreview: hasSuggestion ? buildSuggestionPreview(suggestion) : undefined,
      agent: actor,
      ephemeral: false,
      source: { eventId },
      fingerprint: buildFingerprint(input.groupId, "reply_required", eventId),
      action: {
        type: "send_suggestion",
        groupId: input.groupId,
        text: suggestion,
        to: actor === "system" ? [] : [actor],
        replyTo: eventId,
      },
      sortIndex,
    });
    sortIndex += 1;
  }

  return reminders;
}

function collectStalledPeerReminders(
  input: ProjectPetRemindersInput,
  startIndex: number,
): ReminderDraft[] {
  void input;
  void startIndex;
  return [];
}

function dedupeAndSortReminders(reminders: ReminderDraft[]): PetReminder[] {
  const sorted = [...reminders].sort((left, right) => {
    if (right.priority !== left.priority) {
      return right.priority - left.priority;
    }
    return left.sortIndex - right.sortIndex;
  });

  const deduped = new Map<string, PetReminder>();
  for (const reminder of sorted) {
    if (deduped.has(reminder.fingerprint)) continue;
    const { sortIndex: _sortIndex, ...rest } = reminder;
    deduped.set(reminder.fingerprint, rest);
  }

  return [...deduped.values()];
}

export function projectPetReminders(
  input: ProjectPetRemindersInput
): PetReminder[] {
  const actorDownReminders = collectActorDownReminders(
    input,
    0
  );
  const mentionReminders = collectMentionReminders(
    input,
    actorDownReminders.length
  );
  const stalledPeerReminders = collectStalledPeerReminders(
    input,
    actorDownReminders.length + mentionReminders.length
  );
  const replyRequiredReminders = collectReplyRequiredReminders(
    input,
    actorDownReminders.length + mentionReminders.length + stalledPeerReminders.length
  );

  return dedupeAndSortReminders([
    ...actorDownReminders,
    ...mentionReminders,
    ...stalledPeerReminders,
    ...replyRequiredReminders,
  ]);
}
