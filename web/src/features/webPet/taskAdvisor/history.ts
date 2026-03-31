type TaskAdvisorHistoryEntry = {
  firstSeenAtMs: number;
  lastSeenAtMs: number;
  lastFocus: string;
  lastTaskUpdatedAt: string;
  lastTaskStatus: string;
  focusUnchangedCycles: number;
};

const historyByKey = new Map<string, TaskAdvisorHistoryEntry>();

function buildHistoryKey(groupId: string, actorId: string, taskId: string): string {
  return `${groupId}::${actorId}::${taskId}`;
}

export function getTaskAdvisorHistoryEntry(
  groupId: string,
  actorId: string,
  taskId: string,
): TaskAdvisorHistoryEntry | null {
  return historyByKey.get(buildHistoryKey(groupId, actorId, taskId)) ?? null;
}

export function upsertTaskAdvisorHistoryEntry(
  groupId: string,
  actorId: string,
  taskId: string,
  nextEntry: TaskAdvisorHistoryEntry,
): void {
  historyByKey.set(buildHistoryKey(groupId, actorId, taskId), nextEntry);
}

export function resetTaskAdvisorHistory(): void {
  historyByKey.clear();
}
