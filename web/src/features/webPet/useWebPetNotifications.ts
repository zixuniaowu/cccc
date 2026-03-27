import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GroupContext, LedgerEvent } from "../../types";
import { recordPetDecisionOutcome } from "../../services/api";
import type { PetReminder } from "./types";
import {
  createMentionReaction,
  createTaskReactionSnapshot,
  diffTaskReaction,
  type PetReaction,
  type TaskReactionSnapshotMap,
} from "./reactions";

const SUGGESTION_SNOOZE_MS = 60 * 1000;
const GUARDIAN_SNOOZE_MS = 15 * 60 * 1000;
const DISMISS_COOLDOWN_MS = 3_000;
const SNOOZE_STORAGE_KEY_PREFIX = "cccc:web-pet:snoozed:";

export interface UseWebPetNotificationsResult {
  reminders: PetReminder[];
  activeReminder: PetReminder | null;
  reaction: PetReaction | null;
  dismissReminder: (
    fingerprint: string,
    opts?: { outcome?: "dismissed" | "snoozed" | null; cooldownMs?: number }
  ) => void;
}

export function shouldProjectReminderForGroupState(
  reminder: PetReminder,
  groupState: string,
): boolean {
  const normalizedState = String(groupState || "").trim().toLowerCase();
  if (normalizedState === "active") return true;
  return reminder.action.type === "restart_actor" || reminder.action.type === "automation_proposal";
}

function getLatestEvent(events: LedgerEvent[]): LedgerEvent | null {
  return events.length > 0 ? events[events.length - 1] ?? null : null;
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

function getSnoozeStorageKey(groupId: string): string {
  return `${SNOOZE_STORAGE_KEY_PREFIX}${String(groupId || "").trim()}`;
}

function loadPersistedSnoozes(groupId: string, now: number): Record<string, number> {
  if (typeof window === "undefined") return {};
  const key = getSnoozeStorageKey(groupId);
  if (!key.trim()) return {};
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const next: Record<string, number> = {};
    for (const [fingerprint, value] of Object.entries(parsed || {})) {
      const expiresAt = Number(value || 0);
      if (fingerprint.trim() && Number.isFinite(expiresAt) && expiresAt > now) {
        next[fingerprint] = expiresAt;
      }
    }
    return next;
  } catch {
    return {};
  }
}

function persistSnoozes(groupId: string, snoozes: Record<string, number>): void {
  if (typeof window === "undefined") return;
  const key = getSnoozeStorageKey(groupId);
  if (!key.trim()) return;
  try {
    if (Object.keys(snoozes).length === 0) {
      window.localStorage.removeItem(key);
      return;
    }
    window.localStorage.setItem(key, JSON.stringify(snoozes));
  } catch (error) {
    console.warn("failed to persist web pet snoozes", error);
  }
}

function getReminderSnoozeMs(reminder: PetReminder | undefined): number {
  if (!reminder) return SUGGESTION_SNOOZE_MS;
  if (reminder.action.type === "restart_actor" || reminder.kind === "actor_down") {
    return GUARDIAN_SNOOZE_MS;
  }
  return SUGGESTION_SNOOZE_MS;
}

function getInitialNotificationState(groupId: string): {
  now: number;
  snoozedUntil: Record<string, number>;
} {
  const now = Date.now();
  return {
    now,
    snoozedUntil: loadPersistedSnoozes(groupId, now),
  };
}

export function useWebPetNotifications(input: {
  groupId: string;
  groupState?: string;
  groupContext: GroupContext | null;
  events: LedgerEvent[];
  decisions?: PetReminder[];
}): UseWebPetNotificationsResult {
  const groupId = String(input.groupId || "").trim();
  const groupState = String(input.groupState || "").trim().toLowerCase();
  const groupContext = input.groupContext;
  const events = input.events;
  const initialState = useMemo(() => getInitialNotificationState(groupId), [groupId]);

  const [ephemeralReminder, setEphemeralReminder] = useState<PetReminder | null>(null);
  const [reaction, setReaction] = useState<PetReaction | null>(null);
  const [nowTick, setNowTick] = useState(() => initialState.now);
  const [snoozedUntilSnapshot, setSnoozedUntilSnapshot] = useState<Record<string, number>>(
    () => initialState.snoozedUntil,
  );
  const [dismissCooldownUntil, setDismissCooldownUntil] = useState(0);

  const lastProcessedEventIdRef = useRef("");
  const didHydrateTaskSnapshotRef = useRef(false);
  const previousTaskSnapshotRef = useRef<TaskReactionSnapshotMap>({});
  const snoozedUntilRef = useRef<Record<string, number>>(initialState.snoozedUntil);
  const dismissCooldownUntilRef = useRef(0);
  const dismissCooldownTimerRef = useRef<number | null>(null);

  const projected = useMemo(
    () => {
      const decisions = Array.isArray(input.decisions) ? input.decisions : [];
      return decisions.filter((reminder) => shouldProjectReminderForGroupState(reminder, groupState));
    },
    [groupState, input.decisions],
  );
  const reminderByFingerprint = useMemo(
    () => new Map(projected.map((reminder) => [reminder.fingerprint, reminder])),
    [projected],
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
      persistSnoozes(groupId, snoozedUntilRef.current);
      setSnoozedUntilSnapshot(snoozedUntilRef.current);
      setNowTick(Date.now());
    }, delay + 10);

    return () => window.clearTimeout(timeout);
  }, [groupId, nowTick]);

  const activeReminder = useMemo(() => {
    if (ephemeralReminder) return ephemeralReminder;
    if (reminders.length === 0) return null;
    if (nowTick > 0 && dismissCooldownUntil > nowTick) return null;
    return reminders[0] ?? null;
  }, [dismissCooldownUntil, ephemeralReminder, nowTick, reminders]);

  useEffect(() => {
    const nextState = getInitialNotificationState(groupId);
    snoozedUntilRef.current = nextState.snoozedUntil;
    dismissCooldownUntilRef.current = 0;
    lastProcessedEventIdRef.current = "";
    didHydrateTaskSnapshotRef.current = false;
    previousTaskSnapshotRef.current = {};
    // eslint-disable-next-line react-hooks/set-state-in-effect 
    setNowTick(nextState.now);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSnoozedUntilSnapshot(nextState.snoozedUntil);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDismissCooldownUntil(0);
    const frame = window.requestAnimationFrame(() => {
      setEphemeralReminder(null);
      setReaction(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [groupId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const key = getSnoozeStorageKey(groupId);
    if (!key.trim()) return;

    const handleStorage = (event: StorageEvent) => {
      if (event.storageArea !== window.localStorage || event.key !== key) return;
      const now = Date.now();
      const next = loadPersistedSnoozes(groupId, now);
      snoozedUntilRef.current = next;
      setSnoozedUntilSnapshot(next);
      setNowTick(now);
    };

    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener("storage", handleStorage);
    };
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

  const dismissReminder = useCallback((fingerprint: string, opts?: { outcome?: "dismissed" | "snoozed" | null; cooldownMs?: number }) => {
    const normalized = String(fingerprint || "").trim();
    if (!normalized) return;

    const now = Date.now();
    const reminder = reminderByFingerprint.get(normalized);
    const snoozeMs = Math.max(0, Number(opts?.cooldownMs || 0)) || getReminderSnoozeMs(reminder);
    snoozedUntilRef.current = {
      ...snoozedUntilRef.current,
      [normalized]: now + snoozeMs,
    };
    persistSnoozes(groupId, snoozedUntilRef.current);
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

    const outcome = opts?.outcome === undefined ? "dismissed" : opts.outcome;
    if (outcome && reminder) {
      void recordPetDecisionOutcome(groupId, {
        fingerprint: normalized,
        outcome,
        decisionId: reminder.id,
        actionType: reminder.action.type,
        cooldownMs: snoozeMs,
        sourceEventId: reminder.source.eventId,
      });
    }
  }, [groupId, reminderByFingerprint]);

  return {
    reminders,
    activeReminder,
    reaction,
    dismissReminder,
  };
}
