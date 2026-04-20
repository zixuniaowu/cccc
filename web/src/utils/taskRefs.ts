import type { TaskMessageRef } from "../types";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function trimString(value: unknown): string {
  return typeof value === "string" ? value.trim() : value == null ? "" : String(value).trim();
}

export function isTaskMessageRef(value: unknown): value is TaskMessageRef {
  const record = asRecord(value);
  if (!record) return false;
  return trimString(record.kind) === "task_ref" && !!trimString(record.task_id);
}

export function getTaskMessageRefs(value: unknown): TaskMessageRef[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isTaskMessageRef);
}

export function getTaskRefChipLabel(ref: TaskMessageRef): string {
  const taskId = trimString(ref.task_id);
  const title = trimString(ref.title);
  if (taskId && title) return `${taskId} · ${title}`;
  return taskId || title || "Task";
}
