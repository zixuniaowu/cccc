import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Actor, GroupContext, LedgerEvent } from "../../types";
import {
  projectPetReminders,
  type ReminderActorInput,
  type ReminderEventInput,
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
const DISMISS_COOLDOWN_MS = 3_000;

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
      role: String(actor.role || "").trim() || undefined,
      title: String(actor.title || "").trim() || undefined,
      enabled: actor.enabled !== false,
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

export function useWebPetNotifications(input: {
  groupId: string;
  groupState?: string;
  groupContext: GroupContext | null;
  actors: Actor[];
  events: LedgerEvent[];
}): UseWebPetNotificationsResult {
  const groupId = String(input.groupId || "").trim();
  const groupState = String(input.groupState || "").trim().toLowerCase();
  const groupContext = input.groupContext;
  const actors = input.actors;
  const events = input.events;

  const [ephemeralReminder, setEphemeralReminder] = useState<PetReminder | null>(null);
  const [reaction, setReaction] = useState<PetReaction | null>(null);
  const [nowTick, setNowTick] = useState(0);
  const [snoozedUntilSnapshot, setSnoozedUntilSnapshot] = useState<Record<string, number>>({});
  const [dismissCooldownUntil, setDismissCooldownUntil] = useState(0);

  const lastProcessedEventIdRef = useRef("");
  const didHydrateTaskSnapshotRef = useRef(false);
  const previousTaskSnapshotRef = useRef<TaskReactionSnapshotMap>({});
  const snoozedUntilRef = useRef<Record<string, number>>({});
  const dismissCooldownUntilRef = useRef(0);
  const dismissCooldownTimerRef = useRef<number | null>(null);

  const reminderInput = useMemo(
    () => ({
      groupId,
      actors: groupState === "active" ? mapReminderActors(actors, groupContext) : [],
      events: events
        .map((event) => mapReminderEvent(event))
        .filter((event): event is ReminderEventInput => event !== null),
    }),
    [actors, events, groupContext, groupId, groupState],
  );

  const projected = useMemo(
    () => projectPetReminders(reminderInput),
    [reminderInput],
  );

  const reminders = useMemo(() => {
    const filtered = filterExpiredSnoozes(snoozedUntilSnapshot, nowTick);
    return projected.filter((reminder) => {
      const expiresAt = filtered[reminder.fingerprint];
      return !expiresAt || expiresAt <= nowTick;
    });
  }, [nowTick, projected, snoozedUntilSnapshot]);

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
      setSnoozedUntilSnapshot(snoozedUntilRef.current);
      setNowTick(Date.now());
    }, delay + 10);

    return () => window.clearTimeout(timeout);
  }, [nowTick]);

  const activeReminder = useMemo(() => {
    if (ephemeralReminder) return ephemeralReminder;
    if (reminders.length === 0) return null;
    if (nowTick > 0 && dismissCooldownUntil > nowTick) return null;
    return reminders[0] ?? null;
  }, [dismissCooldownUntil, ephemeralReminder, nowTick, reminders]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setEphemeralReminder(null);
      setReaction(null);
    });
    lastProcessedEventIdRef.current = "";
    didHydrateTaskSnapshotRef.current = false;
    previousTaskSnapshotRef.current = {};
    return () => window.cancelAnimationFrame(frame);
  }, [groupId]);

  useEffect(() => {
    const latestEvent = getLatestEvent(events);
    const eventId = String(latestEvent?.id || "").trim();
    if (!eventId || eventId === lastProcessedEventIdRef.current) {
      return;
    }

    lastProcessedEventIdRef.current = eventId;

    const mentionReaction = createMentionReaction(latestEvent);
    if (!mentionReaction) return;

    const frame = window.requestAnimationFrame(() => {
      if (mentionReaction) {
        setReaction(mentionReaction);
      }
    });

    return () => window.cancelAnimationFrame(frame);
  }, [events, groupId]);

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
      const frame = window.requestAnimationFrame(() => {
        setReaction(taskReaction);
      });
      return () => window.cancelAnimationFrame(frame);
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

    const now = Date.now();
    snoozedUntilRef.current = {
      ...snoozedUntilRef.current,
      [normalized]: now + SNOOZE_MS,
    };
    dismissCooldownUntilRef.current = now + DISMISS_COOLDOWN_MS;
    setDismissCooldownUntil(dismissCooldownUntilRef.current);
    setSnoozedUntilSnapshot(snoozedUntilRef.current);
    setNowTick(now);

    setEphemeralReminder((current) => {
      if (!current || current.fingerprint !== normalized) {
        return current;
      }
      return null;
    });

    // Schedule a tick after cooldown to re-enable reminders
    if (dismissCooldownTimerRef.current !== null) {
      window.clearTimeout(dismissCooldownTimerRef.current);
    }
    dismissCooldownTimerRef.current = window.setTimeout(() => {
      dismissCooldownTimerRef.current = null;
      dismissCooldownUntilRef.current = 0;
      setDismissCooldownUntil(0);
      setNowTick(Date.now());
    }, DISMISS_COOLDOWN_MS + 10);
  }, []);

  return {
    reminders,
    activeReminder,
    reaction,
    dismissReminder,
  };
}
