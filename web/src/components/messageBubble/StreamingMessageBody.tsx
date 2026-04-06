import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LedgerEvent, StreamingActivity } from "../../types";
import { classNames } from "../../utils/classNames";
import { selectStreamingReplySession, useGroupStore } from "../../stores";

const EMPTY_STREAMING_ACTIVITIES: StreamingActivity[] = [];
const EMPTY_STREAMING_EVENTS: LedgerEvent[] = [];
const STREAMING_PENDING_MIN_MS = 80;
const STREAMING_STATUS_EXIT_MS = 140;
const STREAMING_ACTIVITY_LOG_LIMIT = 12;

function dedupeStreamingActivities(value: StreamingActivity[]): StreamingActivity[] {
  if (!Array.isArray(value) || value.length <= 0) return EMPTY_STREAMING_ACTIVITIES;
  const dedupedFromLatest: StreamingActivity[] = [];
  const seenIds = new Set<string>();
  for (let index = value.length - 1; index >= 0; index -= 1) {
    const activity = value[index];
    const activityId = String(activity?.id || "").trim();
    const summary = String(activity?.summary || "").trim();
    if (!activityId || !summary || seenIds.has(activityId)) continue;
    seenIds.add(activityId);
    dedupedFromLatest.push({
      ...activity,
      id: activityId,
      summary,
    });
  }
  return dedupedFromLatest.reverse();
}

export function normalizeStreamingActivities(value: unknown): StreamingActivity[] {
  if (!Array.isArray(value)) return EMPTY_STREAMING_ACTIVITIES;
  const normalized = dedupeStreamingActivities(value
    .filter((item): item is StreamingActivity => !!item && typeof item === "object")
    .map((item) => ({
      id: String(item.id || ""),
      kind: String(item.kind || "thinking"),
      status: String(item.status || "updated"),
      summary: String(item.summary || ""),
      detail: item.detail ? String(item.detail) : undefined,
      ts: item.ts ? String(item.ts) : undefined,
      raw_item_type: item.raw_item_type ? String(item.raw_item_type) : undefined,
      tool_name: item.tool_name ? String(item.tool_name) : undefined,
      server_name: item.server_name ? String(item.server_name) : undefined,
      command: item.command ? String(item.command) : undefined,
      cwd: item.cwd ? String(item.cwd) : undefined,
      file_paths: Array.isArray(item.file_paths) ? item.file_paths.map((part) => String(part || "")) : undefined,
      query: item.query ? String(item.query) : undefined,
    }))
    .filter((item) => item.id && item.summary)
    .slice(-STREAMING_ACTIVITY_LOG_LIMIT));
  const hasRealActivities = normalized.some((item) => item.kind !== "queued" || item.summary !== "queued");
  return hasRealActivities
    ? normalized.filter((item) => item.kind !== "queued" || item.summary !== "queued")
    : normalized;
}

export function getMessageBubbleMotionClass({
  isStreaming,
  isOptimistic,
  streamPhase,
}: {
  isStreaming: boolean;
  isOptimistic: boolean;
  streamPhase?: string;
}): string {
  const phase = String(streamPhase || "").trim().toLowerCase();
  if (!isStreaming && !isOptimistic) return "";
  if (phase === "commentary") return "cccc-transient-bubble cccc-transient-bubble-commentary";
  return "cccc-transient-bubble";
}

export function isQueuedOnlyStreamingPlaceholder({
  isStreaming,
  messageText,
  liveStreamingText,
  blobAttachmentCount,
  presentationRefCount,
  activities,
}: {
  isStreaming: boolean;
  messageText: string;
  liveStreamingText: string;
  blobAttachmentCount: number;
  presentationRefCount: number;
  activities: StreamingActivity[];
}): boolean {
  if (!isStreaming) return false;
  if (String(messageText || "").trim()) return false;
  if (String(liveStreamingText || "").trim()) return false;
  if (blobAttachmentCount > 0 || presentationRefCount > 0) return false;
  if (activities.length !== 1) return false;
  const [activity] = activities;
  return activity.kind === "queued" && activity.summary === "queued";
}

function getEffectiveStreamingActivities({
  streamId,
  actorId,
  pendingEventId,
  bucket,
  fallbackActivities,
}: {
  streamId: string;
  actorId: string;
  pendingEventId: string;
  bucket?: {
    streamingActivitiesByStreamId?: Record<string, StreamingActivity[]>;
    streamingEvents?: LedgerEvent[];
  } | null;
  fallbackActivities?: StreamingActivity[];
}): StreamingActivity[] {
  const normalizedFallback = Array.isArray(fallbackActivities) ? fallbackActivities : EMPTY_STREAMING_ACTIVITIES;
  const activitiesByStreamId = bucket?.streamingActivitiesByStreamId || {};
  const direct = streamId ? normalizeStreamingActivities(activitiesByStreamId[streamId]) : EMPTY_STREAMING_ACTIVITIES;
  const events = Array.isArray(bucket?.streamingEvents) ? (bucket?.streamingEvents || EMPTY_STREAMING_EVENTS) : EMPTY_STREAMING_EVENTS;

  const latestCandidate = events
    .filter((event) => {
      if (String(event.by || "").trim() !== actorId) return false;
      const data = event.data && typeof event.data === "object"
        ? event.data as { stream_id?: unknown; pending_event_id?: unknown }
        : {};
      const eventStreamId = String(data.stream_id || "").trim();
      const eventPendingEventId = String(data.pending_event_id || "").trim();
      if (streamId && eventStreamId === streamId) return true;
      if (pendingEventId && eventPendingEventId === pendingEventId) return true;
      return false;
    })
    .map((event, index) => {
      const data = event.data && typeof event.data === "object"
        ? event.data as { stream_id?: unknown; activities?: unknown }
        : {};
      const eventStreamId = String(data.stream_id || "").trim();
      const liveActivities = eventStreamId ? normalizeStreamingActivities(activitiesByStreamId[eventStreamId]) : EMPTY_STREAMING_ACTIVITIES;
      return {
        index,
        ts: String(event.ts || "").trim(),
        activities: liveActivities.length > 0 ? liveActivities : normalizeStreamingActivities(data.activities),
      };
    })
    .filter((item) => item.activities.length > 0)
    .sort((left, right) => {
      if (left.ts && right.ts && left.ts !== right.ts) return right.ts.localeCompare(left.ts);
      if (left.ts && !right.ts) return -1;
      if (!left.ts && right.ts) return 1;
      return right.index - left.index;
    })[0];

  if (latestCandidate?.activities?.length) return latestCandidate.activities;
  if (direct.length > 0) return direct;
  return normalizedFallback;
}

function deriveStreamingRenderPhase({
  isStreaming,
  hasText,
  activities,
  previousPhase,
}: {
  isStreaming: boolean;
  hasText: boolean;
  activities: StreamingActivity[];
  previousPhase?: "pending" | "active" | "exiting" | "completed";
}): "pending" | "active" | "completed" {
  if (!isStreaming) return "completed";
  if (hasText || activities.length > 0) return "active";
  if (previousPhase === "active" || previousPhase === "exiting") return "active";
  return "pending";
}

function formatActivityKind(kind: string): string {
  const normalized = String(kind || "").trim();
  switch (normalized) {
    case "queued":
      return "queue";
    case "thinking":
      return "think";
    case "plan":
      return "plan";
    case "search":
      return "search";
    case "command":
      return "run";
    case "patch":
      return "patch";
    case "tool":
      return "tool";
    case "reply":
      return "reply";
    default:
      return normalized || "step";
  }
}

function getStructuredActivityLabel(activity: StreamingActivity): string {
  const command = String(activity.command || "").trim();
  if (command) return command;
  const filePaths = Array.isArray(activity.file_paths)
    ? activity.file_paths.map((item) => String(item || "").trim()).filter((item) => item)
    : [];
  if (filePaths.length > 0) return filePaths.join(", ");
  const toolName = String(activity.tool_name || "").trim();
  const serverName = String(activity.server_name || "").trim();
  if (toolName && serverName) return `${serverName}:${toolName}`;
  if (toolName) return toolName;
  const query = String(activity.query || "").trim();
  if (query) return query;
  return String(activity.summary || "").trim();
}

function getStreamingPendingDelayMs(startedAtMs: number | null, nowMs: number): number {
  if (startedAtMs == null) return 0;
  return Math.max(0, STREAMING_PENDING_MIN_MS - Math.max(0, nowMs - startedAtMs));
}

function PlainMessageText({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  return (
    <div
      className={classNames(
        "break-words whitespace-pre-wrap [overflow-wrap:anywhere]",
        className,
      )}
    >
      {text}
    </div>
  );
}

const StreamingMessageText = memo(function StreamingMessageText({
  text,
  showPlaceholder,
  placeholderLabel,
}: {
  text: string;
  showPlaceholder: boolean;
  placeholderLabel?: string;
}) {
  const hasText = !!String(text || "").trim();
  const placeholderText = String(placeholderLabel || "").trim() || "Working...";

  return (
    <div className="w-full">
      <div
        className={classNames(
          "flex min-h-[1.75rem] items-center gap-2 transition-opacity duration-150",
          hasText ? "opacity-100" : "opacity-85 text-[var(--color-text-secondary)]",
        )}
      >
        {!hasText && showPlaceholder ? (
          <span className="inline-flex items-center gap-1 text-[var(--color-text-tertiary)]">
            {[0, 1, 2].map((index) => (
              <span
                key={index}
                className="h-1.5 w-1.5 rounded-full bg-current"
                style={{
                  animation: "ccccMessageTypingDot 1.1s ease-in-out infinite",
                  animationDelay: `${index * 140}ms`,
                }}
              />
            ))}
          </span>
        ) : null}
        <PlainMessageText
          text={hasText ? text : placeholderText}
          className="max-w-full"
        />
      </div>
    </div>
  );
});

const StreamingActivityList = memo(function StreamingActivityList({
  activities,
}: {
  activities: StreamingActivity[];
}) {
  if (activities.length <= 0) return null;

  return (
    <div className="flex flex-col gap-1 rounded-xl border border-[var(--glass-border-subtle)]/80 bg-[var(--glass-tab-bg)]/70 px-2.5 py-2 cccc-streaming-status-panel">
      {activities.map((activity, index) => (
        <div key={activity.id} className="relative min-w-0 pl-4 text-[11px] leading-4 text-[var(--color-text-secondary)]">
          <div className="flex min-w-0 items-baseline gap-2">
            <span
              className={classNames(
                "absolute left-0 top-[0.35rem] h-2 w-2 rounded-full border border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg-hover)]",
                activity.status === "completed" ? "opacity-70" : "opacity-100",
              )}
            />
            {index < activities.length - 1 ? (
              <span className="absolute left-[3px] top-[0.85rem] bottom-[-10px] w-px bg-[var(--glass-border-subtle)]/90" />
            ) : null}
            <span className="min-w-[2.75rem] font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              {formatActivityKind(activity.kind)}
            </span>
            <span className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">
              {getStructuredActivityLabel(activity)}
              {activity.summary && getStructuredActivityLabel(activity) !== String(activity.summary).trim() ? (
                <span className="block text-[10px] text-[var(--color-text-tertiary)]">
                  {activity.summary}
                </span>
              ) : null}
              {activity.detail ? (
                <span className="block text-[10px] text-[var(--color-text-tertiary)]">
                  {activity.detail}
                </span>
              ) : null}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
});

const StreamingContent = memo(function StreamingContent({
  groupId,
  streamId,
  actorId,
  pendingEventId,
  fallbackText,
  fallbackActivities,
  isQueuedOnlyFallbackPlaceholder,
  placeholderLabel,
}: {
  groupId: string;
  streamId: string;
  actorId: string;
  pendingEventId: string;
  fallbackText: string;
  fallbackActivities: StreamingActivity[];
  isQueuedOnlyFallbackPlaceholder: boolean;
  placeholderLabel: string;
}) {
  const replySession = useGroupStore(useCallback((state) => selectStreamingReplySession(state, groupId, {
    pendingEventId,
    streamId,
    actorId,
  }), [actorId, groupId, pendingEventId, streamId]));
  const streamingEvents = useGroupStore(useCallback((state) => {
    const bucket = state.chatByGroup[String(groupId || "").trim()];
    return Array.isArray(bucket?.streamingEvents) ? bucket.streamingEvents : EMPTY_STREAMING_EVENTS;
  }, [groupId]));
  const streamedActivities = useGroupStore(useCallback((state) => {
    if (!streamId) return EMPTY_STREAMING_ACTIVITIES;
    const bucket = state.chatByGroup[String(groupId || "").trim()];
    const activities = bucket?.streamingActivitiesByStreamId?.[streamId];
    return Array.isArray(activities) ? activities : EMPTY_STREAMING_ACTIVITIES;
  }, [groupId, streamId]));
  const liveStreamingActivities = useMemo(() => {
    if (replySession?.activities?.length) return replySession.activities;
    return getEffectiveStreamingActivities({
      streamId,
      actorId,
      pendingEventId,
      bucket: {
        streamingActivitiesByStreamId: streamId
          ? { [streamId]: streamedActivities }
          : undefined,
        streamingEvents,
      },
      fallbackActivities,
    });
  }, [actorId, fallbackActivities, pendingEventId, replySession?.activities, streamId, streamedActivities, streamingEvents]);

  const effectiveText = String(replySession?.text || "") || fallbackText;
  const effectiveStreamingActivities = normalizeStreamingActivities(
    liveStreamingActivities.length > 0 ? liveStreamingActivities : fallbackActivities,
  );
  const hasText = !!String(effectiveText || "").trim();
  const isQueuedOnlyPlaceholder =
    !hasText &&
    effectiveStreamingActivities.length === 1 &&
    effectiveStreamingActivities[0]?.kind === "queued" &&
    effectiveStreamingActivities[0]?.summary === "queued"
      ? true
      : isQueuedOnlyFallbackPlaceholder;
  const [renderPhase, setRenderPhase] = useState<"pending" | "active" | "exiting" | "completed">(() =>
    deriveStreamingRenderPhase({
      isStreaming: true,
      hasText,
      activities: effectiveStreamingActivities,
    })
  );
  const [exitSnapshot, setExitSnapshot] = useState<{
    activities: StreamingActivity[];
    showPlaceholder: boolean;
    placeholderLabel: string;
    queuedOnly: boolean;
  } | null>(null);
  const pendingStartedAtRef = useRef<number | null>(null);
  const desiredPhase = useMemo(() => deriveStreamingRenderPhase({
    isStreaming: true,
    hasText,
    activities: effectiveStreamingActivities,
    previousPhase: renderPhase,
  }), [effectiveStreamingActivities, hasText, renderPhase]);

  useEffect(() => {
    const scheduledTimers: number[] = [];
    const scheduleTask = (task: () => void) => {
      const timerId = window.setTimeout(task, 0);
      scheduledTimers.push(timerId);
      return timerId;
    };
    const scheduleRenderPhase = (nextPhase: "pending" | "active" | "completed") => {
      scheduleTask(() => {
        setRenderPhase(nextPhase);
      });
    };
    const scheduleClearExitSnapshot = () => {
      scheduleTask(() => {
        setExitSnapshot(null);
      });
    };
    if (renderPhase !== "exiting" && desiredPhase === renderPhase) {
      if (renderPhase === "pending" && pendingStartedAtRef.current == null) {
        pendingStartedAtRef.current = Date.now();
      }
      if (renderPhase !== "pending") {
        pendingStartedAtRef.current = null;
      }
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    if (desiredPhase === "pending") {
      scheduleClearExitSnapshot();
      pendingStartedAtRef.current = Date.now();
      if (renderPhase !== "active") {
        scheduleRenderPhase("pending");
      }
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    if (desiredPhase === "completed") {
      pendingStartedAtRef.current = null;
      if (renderPhase !== "completed" && renderPhase !== "exiting") {
        scheduleTask(() => {
          setExitSnapshot({
            activities: effectiveStreamingActivities,
            showPlaceholder: renderPhase === "pending" || (!hasText && effectiveStreamingActivities.length === 0),
            placeholderLabel,
            queuedOnly: isQueuedOnlyPlaceholder,
          });
          setRenderPhase("exiting");
        });
      }
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    scheduleClearExitSnapshot();
    if (renderPhase !== "pending" || pendingStartedAtRef.current == null) {
      pendingStartedAtRef.current = null;
      scheduleRenderPhase("active");
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    const remainingMs = getStreamingPendingDelayMs(pendingStartedAtRef.current, Date.now());
    if (remainingMs <= 0) {
      pendingStartedAtRef.current = null;
      scheduleRenderPhase("active");
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    const timeoutId = window.setTimeout(() => {
      pendingStartedAtRef.current = null;
      setRenderPhase("active");
    }, remainingMs);
    return () => {
      window.clearTimeout(timeoutId);
      scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
    };
  }, [desiredPhase, effectiveStreamingActivities, hasText, isQueuedOnlyPlaceholder, placeholderLabel, renderPhase]);

  useEffect(() => {
    if (renderPhase !== "exiting") return undefined;
    const timeoutId = window.setTimeout(() => {
      setExitSnapshot(null);
      setRenderPhase("completed");
    }, STREAMING_STATUS_EXIT_MS);
    return () => window.clearTimeout(timeoutId);
  }, [renderPhase]);

  const visibleActivities = renderPhase === "pending"
    ? EMPTY_STREAMING_ACTIVITIES
    : renderPhase === "exiting"
      ? (exitSnapshot?.activities || EMPTY_STREAMING_ACTIVITIES)
      : effectiveStreamingActivities;
  const shouldShowText = hasText || renderPhase !== "completed";
  const showActivitiesPanel = visibleActivities.length > 0;

  return (
    <div className="flex min-h-[4.25rem] flex-col gap-1.5">
      {showActivitiesPanel ? (
        <div className="flex min-h-[2rem] items-start transition-opacity duration-150 opacity-100" aria-hidden={false}>
          <div className="flex w-full flex-col gap-2">
            <div className="cccc-streaming-status-layer cccc-streaming-status-layer-active" aria-hidden={false}>
              <StreamingActivityList activities={visibleActivities} />
            </div>
          </div>
        </div>
      ) : null}

      {shouldShowText ? (
        <div className="flex min-h-[1.75rem] items-start">
          <StreamingMessageText
            text={effectiveText}
            showPlaceholder={!hasText}
            placeholderLabel={placeholderLabel}
          />
        </div>
      ) : null}
    </div>
  );
});

export function StreamingMessageBody({
  groupId,
  streamId,
  actorId,
  pendingEventId,
  fallbackText,
  streamingActivities,
  isQueuedOnlyPlaceholder,
  streamingPlaceholderLabel,
}: {
  groupId: string;
  streamId: string;
  actorId: string;
  pendingEventId: string;
  fallbackText: string;
  streamingActivities: StreamingActivity[];
  isQueuedOnlyPlaceholder: boolean;
  streamingPlaceholderLabel: string;
}) {
  return (
    <StreamingContent
      groupId={groupId}
      streamId={streamId}
      actorId={actorId}
      pendingEventId={pendingEventId}
      fallbackText={fallbackText}
      fallbackActivities={streamingActivities}
      isQueuedOnlyFallbackPlaceholder={isQueuedOnlyPlaceholder}
      placeholderLabel={streamingPlaceholderLabel}
    />
  );
}
