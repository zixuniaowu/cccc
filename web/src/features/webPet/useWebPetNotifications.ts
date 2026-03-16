import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { selectChatBucketState, useGroupStore } from "../../stores";
import type { Actor, GroupContext, LedgerEvent } from "../../types";
import {
  createMentionReminder,
  projectPetReminders,
  type ReminderActorInput,
  type ReminderEventInput,
  type ReminderTaskInput,
  type ReminderWaitingUserInput,
  type PetReminder,
} from "./reminders";
import {
  createMentionReaction,
  createTaskReactionSnapshot,
  diffTaskReaction,
  type PetReaction,
  type TaskReactionSnapshotMap,
} from "./reactions";

const SNOOZE_MS = 60 * 1000;
const ROTATE_MS = 8_000;

export interface UseWebPetNotificationsResult {
  reminders: PetReminder[];
  activeReminder: PetReminder | null;
  reaction: PetReaction | null;
  dismissReminder: (fingerprint: string) => void;
}

function getLatestEvent(events: LedgerEvent[]): LedgerEvent | null {
  return events.length > 0 ? events[events.length - 1] ?? null : null;
}

function mapReminderEvent(event: LedgerEvent | null | undefined): ReminderEventInput | null {
  if (!event) return null;

  const eventId = String(event.id || "").trim();
  if (!eventId) return null;

  const data = event.data as Record<string, unknown> | undefined;
  const userStatus = event._obligation_status?.user;
  const to = Array.isArray(data?.to)
    ? data.to.map((entry) => String(entry || "").trim()).filter(Boolean)
    : [];

  return {
    eventId,
    kind: String(event.kind || "").trim(),
    by: String(event.by || "").trim(),
    text: String(data?.text || "").trim(),
    to,
    replyRequired: !!userStatus?.reply_required,
    acked: !!userStatus?.acked,
    replied: !!userStatus?.replied,
  };
}

function mapReminderTasks(groupContext: GroupContext | null): ReminderTaskInput[] {
  const tasks: ReminderTaskInput[] = [];

  for (const task of groupContext?.coordination?.tasks ?? []) {
    const taskId = String(task.id || "").trim();
    if (!taskId) continue;

    tasks.push({
      taskId,
      title: String(task.title || "").trim(),
      assignee: String(task.assignee || "").trim() || undefined,
      waitingOn: String(task.waiting_on || "").trim(),
      status: String(task.status || "").trim(),
    });
  }

  return tasks;
}

function mapWaitingUser(groupContext: GroupContext | null): ReminderWaitingUserInput[] {
  const waitingUser = groupContext?.attention?.waiting_user;
  if (!Array.isArray(waitingUser)) return [];

  const entries: ReminderWaitingUserInput[] = [];

  for (const entry of waitingUser) {
    if (typeof entry === "string") {
      const label = entry.trim();
      if (!label) continue;
      entries.push({
        label,
      });
      continue;
    }

    const taskId = String(entry.id || "").trim() || undefined;
    const label = String(entry.title || taskId || "").trim();
    if (!label) continue;

    entries.push({
      taskId,
      label,
      agent: String(entry.assignee || "").trim() || undefined,
    });
  }

  return entries;
}

function mapReminderActors(
  actors: Actor[],
  groupContext: GroupContext | null,
): ReminderActorInput[] {
  const activeTaskByActor = new Map(
    (groupContext?.agent_states ?? []).map((agentState) => [
      String(agentState.id || "").trim(),
      String(agentState.hot?.active_task_id || "").trim(),
    ]),
  );

  const reminderActors: ReminderActorInput[] = [];

  for (const actor of actors) {
    const actorId = String(actor.id || "").trim();
    if (!actorId) continue;

    reminderActors.push({
      actorId,
      running: !!actor.running,
      idleSeconds: Number(actor.idle_seconds || 0),
      activeTaskId: activeTaskByActor.get(actorId) || undefined,
    });
  }

  return reminderActors;
}

function filterExpiredSnoozes(
  snoozedUntil: Record<string, number>,
  now: number,
): Record<string, number> {
  const next: Record<string, number> = {};
  for (const [fingerprint, expiresAt] of Object.entries(snoozedUntil)) {
    if (expiresAt > now) {
      next[fingerprint] = expiresAt;
    }
  }
  return next;
}

export function useWebPetNotifications(): UseWebPetNotificationsResult {
  const selectedGroupId = useGroupStore((state) => state.selectedGroupId);
  const groupContext = useGroupStore((state) => state.groupContext);
  const actors = useGroupStore((state) => state.actors);
  const events = useGroupStore((state) =>
    selectChatBucketState(state, state.selectedGroupId).events,
  );

  const [ephemeralReminder, setEphemeralReminder] = useState<PetReminder | null>(null);
  const [reaction, setReaction] = useState<PetReaction | null>(null);
  const [rotationCursor, setRotationCursor] = useState(0);
  const [snoozeVersion, setSnoozeVersion] = useState(0);

  const lastProcessedEventIdRef = useRef("");
  const didHydrateTaskSnapshotRef = useRef(false);
  const previousTaskSnapshotRef = useRef<TaskReactionSnapshotMap>({});
  const snoozedUntilRef = useRef<Record<string, number>>({});
  const previousTopPriorityRef = useRef<number | null>(null);

  const reminderInput = useMemo(
    () => ({
      groupId: selectedGroupId,
      waitingUser: mapWaitingUser(groupContext),
      tasks: mapReminderTasks(groupContext),
      actors: mapReminderActors(actors, groupContext),
      events: events
        .map((event) => mapReminderEvent(event))
        .filter((event): event is ReminderEventInput => event !== null),
    }),
    [actors, events, groupContext, selectedGroupId],
  );

  const projected = useMemo(
    () => projectPetReminders(reminderInput),
    [reminderInput],
  );

  const reminders = useMemo(() => {
    const now = Date.now();
    const filtered = filterExpiredSnoozes(snoozedUntilRef.current, now);
    const hasExpired =
      Object.keys(filtered).length !==
      Object.keys(snoozedUntilRef.current).length;

    if (hasExpired) {
      snoozedUntilRef.current = filtered;
    }

    return projected.filter((reminder) => {
      const expiresAt = filtered[reminder.fingerprint];
      return !expiresAt || expiresAt <= now;
    });
  }, [projected, snoozeVersion]);

  useEffect(() => {
    const entries = Object.entries(snoozedUntilRef.current);
    if (entries.length === 0) return;

    const nextExpiry = entries.reduce<number | null>((soonest, [, expiresAt]) => {
      if (soonest === null || expiresAt < soonest) return expiresAt;
      return soonest;
    }, null);

    if (nextExpiry === null) return;

    const delay = Math.max(0, nextExpiry - Date.now());
    const timeout = window.setTimeout(() => {
      snoozedUntilRef.current = filterExpiredSnoozes(
        snoozedUntilRef.current,
        Date.now(),
      );
      setSnoozeVersion((value) => value + 1);
    }, delay + 10);

    return () => window.clearTimeout(timeout);
  }, [snoozeVersion]);

  useEffect(() => {
    if (reminders.length === 0) {
      previousTopPriorityRef.current = null;
      setRotationCursor(0);
      return;
    }

    const topPriority = reminders[0]?.priority ?? null;
    const previousTopPriority = previousTopPriorityRef.current;
    previousTopPriorityRef.current = topPriority;

    if (previousTopPriority === null || (topPriority !== null && topPriority > previousTopPriority)) {
      setRotationCursor(0);
      return;
    }

    setRotationCursor((cursor) =>
      reminders.length === 0 ? 0 : Math.min(cursor, reminders.length - 1),
    );
  }, [reminders]);

  useEffect(() => {
    if (reminders.length <= 1) return;

    const interval = window.setInterval(() => {
      setRotationCursor((cursor) => (cursor + 1) % reminders.length);
    }, ROTATE_MS);

    return () => window.clearInterval(interval);
  }, [reminders.length]);

  const activeReminder = useMemo(() => {
    if (ephemeralReminder) return ephemeralReminder;
    if (reminders.length === 0) return null;
    return reminders[rotationCursor % reminders.length] ?? null;
  }, [ephemeralReminder, reminders, rotationCursor]);

  useEffect(() => {
    setEphemeralReminder(null);
    setReaction(null);
    setRotationCursor(0);
    lastProcessedEventIdRef.current = "";
    didHydrateTaskSnapshotRef.current = false;
    previousTaskSnapshotRef.current = {};
    previousTopPriorityRef.current = null;
  }, [selectedGroupId]);

  useEffect(() => {
    const latestEvent = getLatestEvent(events);
    const eventId = String(latestEvent?.id || "").trim();
    if (!eventId || eventId === lastProcessedEventIdRef.current) {
      return;
    }

    lastProcessedEventIdRef.current = eventId;

    const mentionReminder = createMentionReminder(
      selectedGroupId,
      mapReminderEvent(latestEvent),
    );
    if (mentionReminder) {
      setEphemeralReminder(mentionReminder);
    }

    const mentionReaction = createMentionReaction(latestEvent);
    if (mentionReaction) {
      setReaction(mentionReaction);
    }
  }, [events, selectedGroupId]);

  useEffect(() => {
    const nextSnapshot = createTaskReactionSnapshot(groupContext);

    if (!didHydrateTaskSnapshotRef.current) {
      previousTaskSnapshotRef.current = nextSnapshot;
      didHydrateTaskSnapshotRef.current = true;
      return;
    }

    const taskReaction = diffTaskReaction(
      previousTaskSnapshotRef.current,
      nextSnapshot,
    );
    previousTaskSnapshotRef.current = nextSnapshot;

    if (taskReaction) {
      setReaction(taskReaction);
    }
  }, [groupContext]);

  useEffect(() => {
    if (!reaction) return;

    const timeout = window.setTimeout(() => {
      setReaction((current) =>
        current === reaction ? null : current,
      );
    }, reaction.durationMs);

    return () => window.clearTimeout(timeout);
  }, [reaction]);

  const dismissReminder = useCallback((fingerprint: string) => {
    const normalized = String(fingerprint || "").trim();
    if (!normalized) return;

    snoozedUntilRef.current = {
      ...snoozedUntilRef.current,
      [normalized]: Date.now() + SNOOZE_MS,
    };
    setSnoozeVersion((value) => value + 1);

    setEphemeralReminder((current) => {
      if (!current || current.fingerprint !== normalized) {
        return current;
      }
      return null;
    });
  }, []);

  return {
    reminders,
    activeReminder,
    reaction,
    dismissReminder,
  };
}
