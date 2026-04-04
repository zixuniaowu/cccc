import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { FloatingPortal, autoUpdate, flip, offset, shift, useFloating } from "@floating-ui/react";
import { useTranslation } from "react-i18next";
import { LedgerEvent, Actor, AgentState, getActorAccentColor, ChatMessageData, MessageAttachment, PresentationMessageRef, StreamingActivity } from "../types";
import { formatFullTime, formatMessageTimestamp, formatTime } from "../utils/time";
import { classNames } from "../utils/classNames";
import { getReplyEventId } from "../utils/chatReply";
import { getRecipientDisplayName } from "../hooks/useActorDisplayName";
import { getPresentationMessageRefs, getPresentationRefChipLabel } from "../utils/presentationRefs";
import { MessageAttachments } from "./messageBubble/MessageAttachments";
import { ActorAvatar } from "./ActorAvatar";
import { useGroupStore } from "../stores";

const LazyMarkdownRenderer = lazy(() =>
    import("./MarkdownRenderer").then((module) => ({ default: module.MarkdownRenderer }))
);

const TYPING_DOT_STYLE_ID = "cccc-message-bubble-typing-dot-style";
const EMPTY_STREAMING_ACTIVITIES: StreamingActivity[] = [];

function ensureTypingDotStyle(): void {
    if (typeof document === "undefined") return;
    if (document.getElementById(TYPING_DOT_STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = TYPING_DOT_STYLE_ID;
    style.textContent = `
      @keyframes ccccMessageTypingDot {
        0%, 70%, 100% { transform: translateY(0) scale(0.92); opacity: 0.28; }
        35% { transform: translateY(-3px) scale(1); opacity: 0.95; }
      }
    `;
    document.head.appendChild(style);
}

function formatEventLine(ev: LedgerEvent): string {
    if (ev.kind === "chat.message" && ev.data && typeof ev.data === "object") {
        const msg = ev.data as ChatMessageData;
        return String(msg.text || "");
    }
    return "";
}

function mayContainMarkdown(text: string): boolean {
    const value = String(text || "");
    if (!value.trim()) return false;
    return /(```|`[^`\n]+`|\[[^\]]+\]\([^)]+\)|^#{1,6}\s|^\s*[-*+]\s|^\s*\d+\.\s|^\s*>\s)/m.test(value);
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

function isQueuedOnlyStreamingPlaceholder({
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

function getStreamingPlaceholderText({
    isQueuedOnlyPlaceholder,
    placeholderLabel,
}: {
    isQueuedOnlyPlaceholder: boolean;
    placeholderLabel: string;
}): string {
    if (isQueuedOnlyPlaceholder) return "queued";
    return String(placeholderLabel || "").trim() || "working";
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
                className
            )}
        >
            {text}
        </div>
    );
}

const StreamingMessageText = memo(function StreamingMessageText({
    groupId,
    streamId,
    fallbackText,
    showPlaceholder,
    placeholderLabel,
}: {
    groupId: string;
    streamId: string;
    fallbackText: string;
    showPlaceholder: boolean;
    placeholderLabel?: string;
}) {
    const streamingText = useGroupStore(useCallback((state) => {
        if (!streamId) return "";
        const bucket = state.chatByGroup[String(groupId || "").trim()];
        return String(bucket?.streamingTextByStreamId?.[streamId] || "");
    }, [groupId, streamId]));
    const text = streamingText || fallbackText;
    const hasText = !!String(text || "").trim();
    const placeholderText = String(placeholderLabel || "").trim() || "Working...";

    return (
        <div className="w-full">
            <div
                className={classNames(
                    "flex min-h-[1.75rem] items-center gap-2 transition-opacity duration-150",
                    hasText ? "opacity-100" : "opacity-85 text-[var(--color-text-secondary)]"
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
    groupId,
    streamId,
    fallbackActivities,
}: {
    groupId: string;
    streamId: string;
    fallbackActivities: StreamingActivity[];
}) {
    const streamedActivities = useGroupStore(useCallback((state) => {
        if (!streamId) return undefined;
        const bucket = state.chatByGroup[String(groupId || "").trim()];
        return bucket?.streamingActivitiesByStreamId?.[streamId];
    }, [groupId, streamId]));
    const activities = Array.isArray(streamedActivities) && streamedActivities.length > 0
        ? streamedActivities
        : fallbackActivities;

    if (activities.length <= 0) return null;

    return (
        <div className="mb-2 flex flex-col gap-1 rounded-xl border border-[var(--glass-border-subtle)]/80 bg-[var(--glass-tab-bg)]/70 px-2.5 py-2">
            {activities.map((activity) => (
                <div key={activity.id} className="flex items-start gap-2 text-[11px] leading-4 text-[var(--color-text-secondary)]">
                    <span className="min-w-[2.75rem] font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                        {formatActivityKind(activity.kind)}
                    </span>
                    <span className="min-w-0 break-words [overflow-wrap:anywhere]">
                        {activity.summary}
                    </span>
                </div>
            ))}
        </div>
    );
});

const StreamingContent = memo(function StreamingContent({
    groupId,
    streamId,
    fallbackText,
    fallbackActivities,
    isQueuedOnlyFallbackPlaceholder,
    placeholderLabel,
}: {
    groupId: string;
    streamId: string;
    fallbackText: string;
    fallbackActivities: StreamingActivity[];
    isQueuedOnlyFallbackPlaceholder: boolean;
    placeholderLabel: string;
}) {
    const liveStreamingText = useGroupStore(useCallback((state) => {
        if (!streamId) return "";
        const bucket = state.chatByGroup[String(groupId || "").trim()];
        return String(bucket?.streamingTextByStreamId?.[streamId] || "");
    }, [groupId, streamId]));
    const liveStreamingActivities = useGroupStore(useCallback((state) => {
        if (!streamId) return EMPTY_STREAMING_ACTIVITIES;
        const bucket = state.chatByGroup[String(groupId || "").trim()];
        const streamed = bucket?.streamingActivitiesByStreamId?.[streamId];
        return Array.isArray(streamed) ? streamed : EMPTY_STREAMING_ACTIVITIES;
    }, [groupId, streamId]));

    const effectiveStreamingActivities = liveStreamingActivities.length > 0 ? liveStreamingActivities : fallbackActivities;
    const hasText = !!String(liveStreamingText || fallbackText || "").trim();
    const isQueuedOnlyPlaceholder =
        !hasText &&
        effectiveStreamingActivities.length === 1 &&
        effectiveStreamingActivities[0]?.kind === "queued" &&
        effectiveStreamingActivities[0]?.summary === "queued"
            ? true
            : isQueuedOnlyFallbackPlaceholder;
    const streamingTextMinHeightClass = isQueuedOnlyPlaceholder
        ? "min-h-[44px]"
        : hasText
            ? "min-h-[44px]"
            : "min-h-[52px]";

    return (
        <>
            <div className="mb-2 min-h-[52px]">
                {effectiveStreamingActivities.length > 0 ? (
                    <StreamingActivityList
                        groupId={groupId}
                        streamId={streamId}
                        fallbackActivities={effectiveStreamingActivities}
                    />
                ) : (
                    <div className="flex min-h-[52px] items-start">
                        <StreamingStatusPlaceholder
                            label={getStreamingPlaceholderText({
                                isQueuedOnlyPlaceholder,
                                placeholderLabel,
                            })}
                            queuedOnly={isQueuedOnlyPlaceholder}
                        />
                    </div>
                )}
            </div>

            <div className={classNames("flex items-start", streamingTextMinHeightClass)}>
                <StreamingMessageText
                    groupId={groupId}
                    streamId={streamId}
                    fallbackText={fallbackText}
                    showPlaceholder={!hasText}
                    placeholderLabel={placeholderLabel}
                />
            </div>
        </>
    );
});

const StreamingStatusPlaceholder = memo(function StreamingStatusPlaceholder({
    label,
    queuedOnly,
}: {
    label: string;
    queuedOnly?: boolean;
}) {
    return (
        <div className="inline-flex items-center gap-2 rounded-full border border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)]/75 px-3 py-1.5 text-[11px] font-medium text-[var(--color-text-secondary)]">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--color-text-tertiary)]">
                {queuedOnly ? "queue" : "stream"}
            </span>
            <span>{label}</span>
            <span className="ml-1 inline-flex items-center gap-1 text-[var(--color-text-tertiary)]">
                {[0, 1, 2].map((index) => (
                    <span
                        key={index}
                        className="h-1.5 w-1.5 rounded-full bg-current"
                        style={{
                            animation: "ccccMessageTypingDot 1.05s ease-in-out infinite",
                            animationDelay: `${index * 120}ms`,
                        }}
                    />
                ))}
            </span>
        </div>
    );
});

async function copyText(value: string): Promise<boolean> {
    const text = String(value || "");
    if (!text.trim()) return false;
    try {
        if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch {
        // Fall through to the prompt fallback below.
    }
    if (typeof window !== "undefined" && typeof window.prompt === "function") {
        window.prompt("Copy to clipboard:", text);
        return true;
    }
    return false;
}

function buildMessageCopyText({
    quoteText,
    messageText,
    presentationRefs,
    attachments,
}: {
    quoteText?: string;
    messageText: string;
    presentationRefs: PresentationMessageRef[];
    attachments: { title: string; path: string }[];
}): string {
    const sections: string[] = [];
    const trimmedQuote = String(quoteText || "").trim();
    const trimmedMessage = String(messageText || "").trim();

    if (trimmedQuote) {
        sections.push(`> ${trimmedQuote}`);
    }
    if (trimmedMessage) {
        sections.push(trimmedMessage);
    }
    if (presentationRefs.length > 0) {
        sections.push([
            "Presentation refs:",
            ...presentationRefs.map((ref) => `- ${getPresentationRefChipLabel(ref)}`),
        ].join("\n"));
    }
    if (attachments.length > 0) {
        sections.push([
            "Attachments:",
            ...attachments.map((attachment) => {
                const rawTitle = String(attachment.title || "").trim();
                if (rawTitle) return `- ${rawTitle}`;
                const parts = String(attachment.path || "").split("/");
                return `- ${parts[parts.length - 1] || "file"}`;
            }),
        ].join("\n"));
    }
    return sections.join("\n\n").trim();
}

export interface MessageBubbleProps {
    event: LedgerEvent;
    actorById: Map<string, Actor>;
    actors: Actor[];
    displayNameMap: Map<string, string>;
    agentState: AgentState | null;
    isDark: boolean;
    readOnly?: boolean;
    groupId: string;
    groupLabelById: Record<string, string>;
    isHighlighted?: boolean;
    onReply: () => void;
    onShowRecipients: () => void;
    onCopyLink?: (eventId: string) => void;
    onCopyContent?: (ev: LedgerEvent) => void;
    onRelay?: (ev: LedgerEvent) => void;
    onOpenSource?: (srcGroupId: string, srcEventId: string) => void;
    onOpenPresentationRef?: (ref: PresentationMessageRef, event: LedgerEvent) => void;
}

export const MessageBubble = memo(function MessageBubble({
    event: ev,
    actorById,
    actors,
    displayNameMap,
    agentState,
    isDark,
    readOnly,
    groupId,
    groupLabelById,
    isHighlighted,
    onReply,
    onShowRecipients,
    onCopyLink,
    onCopyContent: _onCopyContent,
    onRelay,
    onOpenSource,
    onOpenPresentationRef,
}: MessageBubbleProps) {
    const isUserMessage = ev.by === "user";
    const isOptimistic = !!(ev.data as Record<string, unknown> | undefined)?._optimistic;
    const senderAccent = !isUserMessage ? getActorAccentColor(String(ev.by || ""), isDark) : null;
    const isStreaming = !!ev._streaming;
    const streamId = ev.data && typeof ev.data === "object"
        ? String((ev.data as { stream_id?: unknown }).stream_id || "").trim()
        : "";
    const canReply = !!getReplyEventId(ev);
    const messageText = useMemo(() => formatEventLine(ev), [ev]);

    const [isAgentStateOpen, setIsAgentStateOpen] = useState(false);
    const [copiedMessageText, setCopiedMessageText] = useState(false);
    const floatingMiddleware = useMemo(() => [offset(8), flip(), shift({ padding: 8 })], []);
    const { refs, floatingStyles, context } = useFloating({
        open: isAgentStateOpen,
        onOpenChange: setIsAgentStateOpen,
        placement: "bottom-start",
        middleware: floatingMiddleware,
        whileElementsMounted: autoUpdate,
        strategy: "fixed",
    });
    const isAgentStatePositioned = context.isPositioned;
    const setAgentStateReference = useCallback((node: HTMLElement | null) => {
        refs.setReference(node);
    }, [refs]);
    const setAgentStateFloating = useCallback((node: HTMLElement | null) => {
        refs.setFloating(node);
    }, [refs]);

    const canShowAgentState = useMemo(() => {
        if (isUserMessage) return false;
        const id = String(ev.by || "");
        if (!id) return false;
        return true;
    }, [ev.by, isUserMessage]);

    const { t } = useTranslation('chat');

    useEffect(() => {
        ensureTypingDotStyle();
    }, []);
    const agentStateText = String(agentState?.hot?.focus || "").trim();
    const agentStateDisplay = agentStateText || t('noAgentStateYet');
    const stateTask = String(agentState?.hot?.active_task_id || "").trim();
    const stateNext = String(agentState?.hot?.next_action || "").trim();
    const stateChanged = String(agentState?.warm?.what_changed || "").trim();
    const blockerCount = Array.isArray(agentState?.hot?.blockers) ? agentState.hot.blockers.length : 0;


    // Treat data as ChatMessageData.
    const msgData = ev.data as ChatMessageData | undefined;
    const quoteText = msgData?.quote_text;
    const isAttention = String(msgData?.priority || "normal") === "attention";
    const replyRequired = !!msgData?.reply_required;
    const srcGroupId = typeof msgData?.src_group_id === "string" ? String(msgData.src_group_id || "").trim() : "";
    const srcEventId = typeof msgData?.src_event_id === "string" ? String(msgData.src_event_id || "").trim() : "";
    const hasSource = !!(srcGroupId && srcEventId);
    const dstGroupId = typeof msgData?.dst_group_id === "string" ? String(msgData.dst_group_id || "").trim() : "";
    const dstTo = useMemo(() => {
        const raw = msgData?.dst_to;
        if (!Array.isArray(raw)) return [];
        return raw.map((t) => String(t || "").trim()).filter((t) => t);
    }, [msgData?.dst_to]);
    const hasDestination = !!dstGroupId;
    const rawAttachments: MessageAttachment[] = Array.isArray(msgData?.attachments) ? msgData.attachments : [];
    const blobAttachments = rawAttachments
        .filter((a): a is MessageAttachment => a != null && typeof a === "object")
        .map((a) => ({
            kind: String(a.kind || "file"),
            path: String(a.path || ""),
            title: String(a.title || ""),
            bytes: Number(a.bytes || 0),
            mime_type: String(a.mime_type || ""),
            local_preview_url: "local_preview_url" in a ? String(a.local_preview_url || "") : "",
        }))
        .filter((a) => a.path.startsWith("state/blobs/") || a.local_preview_url.startsWith("blob:"));
    const presentationRefs = useMemo(() => getPresentationMessageRefs(msgData?.refs), [msgData?.refs]);
    const shouldRenderMarkdown = useMemo(() => !isStreaming && mayContainMarkdown(messageText), [isStreaming, messageText]);
    const streamingActivities = useMemo(() => {
        const raw = (msgData as { activities?: unknown } | undefined)?.activities;
        if (!Array.isArray(raw)) return [] as StreamingActivity[];
        return raw
            .filter((item): item is StreamingActivity => !!item && typeof item === "object")
            .map((item) => ({
                id: String(item.id || ""),
                kind: String(item.kind || "thinking"),
                status: String(item.status || "updated"),
                summary: String(item.summary || ""),
                detail: item.detail ? String(item.detail) : undefined,
                ts: item.ts ? String(item.ts) : undefined,
            }))
            .filter((item) => item.id && item.summary)
            .slice(-5);
    }, [msgData]);
    const isQueuedOnlyPlaceholder = useMemo(() => {
        return isQueuedOnlyStreamingPlaceholder({
            isStreaming,
            messageText,
            liveStreamingText: "",
            blobAttachmentCount: blobAttachments.length,
            presentationRefCount: presentationRefs.length,
            activities: streamingActivities,
        });
    }, [blobAttachments.length, isStreaming, messageText, presentationRefs.length, streamingActivities]);
    const streamPhase = String((msgData as { stream_phase?: unknown } | undefined)?.stream_phase || "").trim().toLowerCase();
    const streamingPlaceholderLabel = useMemo(() => {
        if (!isStreaming) return "";
        if (streamPhase === "commentary") {
            return t("streamCommentaryPending");
        }
        if (streamPhase === "final_answer") {
            return t("streamFinalAnswerPending");
        }
        return t("streamWorkingPending");
    }, [isStreaming, streamPhase, t]);
    const stableMessageAttachmentKey = useMemo(() => {
        const clientId = typeof msgData?.client_id === "string" ? String(msgData.client_id || "").trim() : "";
        if (clientId) return `client:${clientId}`;
        const eventId = typeof ev.id === "string" ? String(ev.id || "").trim() : "";
        return eventId || `row:${String(ev.ts || "")}:${String(ev.by || "")}`;
    }, [ev.id, ev.ts, ev.by, msgData]);
    const copyableMessageText = useMemo(
        () =>
            buildMessageCopyText({
                quoteText,
                messageText,
                presentationRefs,
                attachments: blobAttachments.map((attachment) => ({
                    title: attachment.title,
                    path: attachment.path || attachment.local_preview_url,
                })),
            }),
        [blobAttachments, messageText, presentationRefs, quoteText]
    );
    const messageTimestamp = formatMessageTimestamp(ev.ts);
    const fullMessageTimestamp = formatFullTime(ev.ts);

    // Use event's group_id for blob URLs (attachments are stored in the event's original group)
    const blobGroupId = String(ev.group_id || "").trim() || groupId;

    const readStatus = ev._read_status;
    const ackStatus = ev._ack_status;
    const recipients = msgData?.to;

    const visibleReadStatusEntries = useMemo(() => {
        if (!readStatus) return [];
        return actors
            .map((a) => String(a.id || ""))
            .filter((id) => id && Object.prototype.hasOwnProperty.call(readStatus, id))
            .map((id) => [id, !!readStatus[id]] as const);
    }, [actors, readStatus]);

    const isDirectUserMessage = useMemo(() => {
        if (isUserMessage) return false;
        if (!Array.isArray(recipients)) return false;
        const ids = recipients.map((id) => String(id || "").trim()).filter((id) => id);
        return ids.length === 1 && ids[0] === "user";
    }, [isUserMessage, recipients]);

    const hideDirectUserObligationSummary = useMemo(() => {
        if (isUserMessage) return false;
        const os = ev._obligation_status;
        if (os && typeof os === "object") {
            const ids = Object.keys(os);
            return ids.length === 1 && ids[0] === "user";
        }
        if (ackStatus && typeof ackStatus === "object") {
            const ids = Object.keys(ackStatus);
            return ids.length === 1 && ids[0] === "user";
        }
        return isDirectUserMessage;
    }, [ackStatus, ev._obligation_status, isDirectUserMessage, isUserMessage]);

    const ackSummary = useMemo(() => {
        if (hideDirectUserObligationSummary) return null;
        if ((!isAttention && !replyRequired) || !ackStatus || typeof ackStatus !== "object") return null;
        const ids = Object.keys(ackStatus);
        if (ids.length === 0) return null;
        const done = ids.reduce((n, id) => n + (ackStatus[id] ? 1 : 0), 0);
        const needsUserAck =
            Object.prototype.hasOwnProperty.call(ackStatus, "user") && !ackStatus["user"] && !isUserMessage;
        return { done, total: ids.length, needsUserAck };
    }, [ackStatus, hideDirectUserObligationSummary, isAttention, isUserMessage, replyRequired]);

    const obligationSummary = useMemo(() => {
        if (hideDirectUserObligationSummary) return null;
        const os = ev._obligation_status;
        if (!os || typeof os !== "object") return null;
        const ids = Object.keys(os);
        if (ids.length === 0) return null;

        const requiresReply = ids.some((id) => !!os[id]?.reply_required);
        if (requiresReply) {
            const done = ids.reduce((n, id) => n + (os[id]?.replied ? 1 : 0), 0);
            return { kind: "reply" as const, done, total: ids.length };
        }
        const done = ids.reduce((n, id) => n + (os[id]?.acked ? 1 : 0), 0);
        return { kind: "ack" as const, done, total: ids.length };
    }, [ev._obligation_status, hideDirectUserObligationSummary]);

    const toLabel = useMemo(() => {
        if (hasDestination) {
            const dstLabel = String(groupLabelById?.[dstGroupId] || "").trim() || dstGroupId;
            const dstToLabel = dstTo.length > 0 ? dstTo.join(", ") : "@all";
            return `group: ${dstLabel} · ${dstToLabel}`;
        }
        if (!recipients || recipients.length === 0) return "@all";
        return recipients
            .map(r => getRecipientDisplayName(r, displayNameMap))
            .join(", ");
    }, [displayNameMap, dstGroupId, dstTo, groupLabelById, hasDestination, recipients]);

    // Sender display name (use title if available)
    const senderActor = useMemo(() => {
        if (isUserMessage) return null;
        const senderId = String(ev.by || "").trim();
        if (!senderId) return null;
        return actorById.get(senderId) || null;
    }, [actorById, ev.by, isUserMessage]);

    const senderDisplayName = useMemo(() => {
        const by = String(ev.by || "");
        if (!by || by === "user") return by;
        return String(senderActor?.title || "").trim() || displayNameMap.get(by) || by;
    }, [displayNameMap, ev.by, senderActor]);

    const readPreviewEntries = visibleReadStatusEntries.slice(0, 3);
    const readPreviewOverflow = Math.max(0, visibleReadStatusEntries.length - readPreviewEntries.length);
    const relayChipClass = isUserMessage
        ? "border-white/14 bg-white/8 text-blue-100 shadow-none hover:bg-white/12"
        : "glass-btn border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]";

    useEffect(() => {
        if (!copiedMessageText) return undefined;
        const timer = window.setTimeout(() => {
            setCopiedMessageText(false);
        }, 1400);
        return () => window.clearTimeout(timer);
    }, [copiedMessageText]);

    const handleCopyMessageText = useCallback(async () => {
        const ok = await copyText(copyableMessageText);
        if (ok) {
            setCopiedMessageText(true);
        }
    }, [copyableMessageText]);

    return (
        <div
            className={classNames(
                "relative flex w-full min-w-0 gap-2 sm:gap-3 group",
                isUserMessage
                    ? "flex-col items-end sm:items-start sm:flex-row-reverse"
                    : "flex-col items-start sm:flex-row",
                isOptimistic ? "opacity-95" : ""
            )}
        >
            {/* Desktop Avatar (Hidden on mobile) */}
            <div className="relative hidden sm:block">
                <div
                    className={classNames(
                        "mt-1 h-8 w-8 flex-shrink-0",
                        canShowAgentState ? "cursor-help" : ""
                    )}
                    ref={canShowAgentState ? setAgentStateReference : undefined}
                    onMouseEnter={canShowAgentState ? () => setIsAgentStateOpen(true) : undefined}
                    onMouseLeave={canShowAgentState ? () => setIsAgentStateOpen(false) : undefined}
                    onFocus={canShowAgentState ? () => setIsAgentStateOpen(true) : undefined}
                    onBlur={canShowAgentState ? () => setIsAgentStateOpen(false) : undefined}
                    tabIndex={canShowAgentState ? 0 : undefined}
                    aria-label={canShowAgentState ? t('agentStateTooltipLabel', { defaultValue: 'View agent state' }) : undefined}
                >
                    <ActorAvatar
                        avatarUrl={senderActor?.avatar_url}
                        runtime={senderActor?.runtime}
                        title={senderDisplayName}
                        isUser={isUserMessage}
                        isDark={isDark}
                        accentRingClassName={senderAccent?.ring}
                    />
                </div>
            </div>
            <FloatingPortal>
                {isAgentStateOpen && canShowAgentState ? (
                    <div
                        ref={setAgentStateFloating}
                        style={floatingStyles}
                        className={classNames(
                            "pointer-events-none z-[80] w-[min(360px,calc(100vw-32px))] rounded-2xl px-3 py-2 shadow-2xl transition-opacity duration-150",
                            "glass-modal text-[var(--color-text-primary)]",
                            isAgentStatePositioned ? "opacity-100" : "opacity-0"
                        )}
                        role="status"
                    >
                        <div className="flex items-center gap-2">
                            <div className="text-xs font-semibold text-[var(--color-text-primary)]">
                                {senderDisplayName}
                            </div>
                            {agentState?.updated_at ? (
                                <div
                                    className={classNames(
                                        "ml-auto text-xs tabular-nums",
                                        "text-[var(--color-text-tertiary)]"
                                    )}
                                    title={formatFullTime(agentState.updated_at)}
                                >
                                    {t('updated', { time: formatTime(agentState.updated_at) })}
                                </div>
                            ) : null}
                        </div>
                        <div className="mt-1 text-xs whitespace-pre-wrap text-[var(--color-text-secondary)]">
                            {agentStateDisplay}
                        </div>
                        {(stateTask || blockerCount > 0 || stateNext || stateChanged) ? (
                            <div className="mt-2 space-y-1">
                                <div className="flex flex-wrap items-center gap-1.5">
                                    {stateTask ? (
                                        <span className="text-[11px] px-2 py-0.5 rounded bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                                            {t("taskShort", { id: stateTask })}
                                        </span>
                                    ) : null}
                                    {blockerCount > 0 ? (
                                        <span className="text-[11px] px-2 py-0.5 rounded bg-rose-500/15 text-rose-600 dark:text-rose-300">
                                            {t("blockersShort", { count: blockerCount })}
                                        </span>
                                    ) : null}
                                </div>
                                {stateNext ? (
                                    <div className="text-[11px] text-[var(--color-text-tertiary)]">
                                        {t("nextShort", { value: stateNext })}
                                    </div>
                                ) : null}
                                {stateChanged ? (
                                    <div className={classNames("text-[11px]", "text-[var(--color-text-tertiary)]")}>
                                        {t("changedShort", { value: stateChanged })}
                                    </div>
                                ) : null}
                            </div>
                        ) : null}
                    </div>
                ) : null}
            </FloatingPortal>

            {/* Message Content */}
            <div
                className={classNames(
                    "flex min-w-0 flex-col w-full md:w-auto md:max-w-[82%] xl:max-w-[75%]",
                    isUserMessage ? "items-end" : "items-start"
                )}
            >
                {/* Mobile Header Row (Visible only on mobile) */}
                <div
                    className={classNames(
                        "flex items-center gap-2 mb-1 sm:hidden min-w-0",
                        isUserMessage ? "justify-end" : "justify-start"
                    )}
                >
                    <ActorAvatar
                        avatarUrl={senderActor?.avatar_url}
                        runtime={senderActor?.runtime}
                        title={senderDisplayName}
                        isUser={isUserMessage}
                        isDark={isDark}
                        accentRingClassName={senderAccent?.ring}
                        sizeClassName="h-6 w-6"
                        textClassName="text-[10px]"
                    />
                    <span
                        className={classNames(
                            "text-xs font-medium flex-shrink-0",
                            isUserMessage
                                ? isDark
                                    ? "text-slate-300"
                                    : "text-gray-700"
                                : senderAccent
                                    ? senderAccent.text
                                    : isDark
                                        ? "text-slate-300"
                                        : "text-gray-700"
                        )}
                    >
                        {senderDisplayName}
                    </span>
                    <span className={`text-[10px] flex-shrink-0 text-[var(--color-text-tertiary)]`}>
                        <span title={fullMessageTimestamp}>{messageTimestamp}</span>
                    </span>
                    <span
                        className={classNames(
                            "text-[10px] min-w-0 truncate",
                            "text-[var(--color-text-tertiary)]"
                        )}
                        title={`to ${toLabel}`}
                    >
                        to {toLabel}
                    </span>
                </div>

                {/* Desktop Metadata Header (Hidden on mobile) */}
                <div className="hidden sm:flex items-center gap-2 mb-1 px-1 min-w-0">
                    <span
                        className={classNames(
                            "text-[11px] font-medium flex-shrink-0",
                            isUserMessage
                                ? isDark
                                    ? "text-[var(--color-text-secondary)]"
                                    : "text-gray-500"
                                : senderAccent
                                    ? senderAccent.text
                                    : isDark
                                        ? "text-[var(--color-text-secondary)]"
                                        : "text-gray-500"
                        )}
                    >
                        {senderDisplayName}
                    </span>
                    <span className={`text-[10px] flex-shrink-0 text-[var(--color-text-tertiary)]`}>
                        <span title={fullMessageTimestamp}>{messageTimestamp}</span>
                    </span>
                    <span className={classNames("text-[10px] min-w-0 truncate", "text-[var(--color-text-tertiary)]")} title={`to ${toLabel}`}>
                        to {toLabel}
                    </span>
                </div>

                {/* Bubble wrapper (allows badge to overflow) */}
                <div
                    className="relative w-full max-w-full min-w-0 md:w-auto"
                    style={isAttention ? { minWidth: "min(8.5rem, 85vw)" } : undefined}
                >
                    {isAttention && (
                        <span
                            className={classNames(
                                "absolute -top-2 z-10 text-[10px] font-semibold px-2 py-0.5 rounded-full border shadow-sm",
                                isUserMessage ? "left-3" : "right-3",
                                "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200 border-amber-300 dark:border-amber-700"
                            )}
                        >
                            {t('important')}
                        </span>
                    )}
                <div
                    className={classNames(
                        "inline-flex max-w-full flex-col px-4 py-2.5 text-sm leading-relaxed transition-[opacity,transform,box-shadow] duration-200 ease-out",
                        isQueuedOnlyPlaceholder ? "min-h-0 px-3 py-2" : "",
                        isStreaming ? "opacity-95 translate-y-0.5" : "opacity-100 translate-y-0",
                        isUserMessage
                            ? "bg-blue-600 text-white rounded-2xl rounded-tr-none shadow-sm"
                            : "glass-bubble rounded-2xl rounded-tl-none text-[var(--color-text-primary)]"
                        ,
                        isAttention ? "ring-1 ring-amber-400/40 dark:ring-amber-500/40" : ""
                        ,
                        isHighlighted ? "outline outline-2 outline-sky-500/30 outline-offset-2" : ""
                    )}
                >

                    {/* Cross-group relay provenance */}
                    {hasSource ? (
                        <button
                            type="button"
                            className={classNames(
                                "mb-2 inline-flex items-center gap-2 text-xs font-medium rounded-lg px-2 py-1 border",
                                relayChipClass,
                                onOpenSource ? "cursor-pointer" : "cursor-default"
                            )}
                            onClick={() => onOpenSource?.(srcGroupId, srcEventId)}
                            disabled={!onOpenSource}
                            title={t('openOriginalMessage')}
                        >
                            <span className="opacity-70">↗</span>
                            <span className="truncate">
                                {t('relayedFrom', { groupId: srcGroupId, eventId: srcEventId.slice(0, 8) })}
                            </span>
                        </button>
                    ) : null}
                    {/* Outbound cross-group send record */}
                    {hasDestination ? (() => {
                        const dstLabel = String(groupLabelById?.[dstGroupId] || "").trim() || dstGroupId;
                        const dstToLabel = dstTo.length > 0 ? dstTo.join(", ") : "@all";
                        return (
                            <div
                                className={classNames(
                                    "mb-2 inline-flex items-center gap-2 text-xs font-medium rounded-lg px-2 py-1 border",
                                    relayChipClass
                                )}
                                title={t('sentTo', { label: dstGroupId, to: dstToLabel })}
                            >
                                <span className="opacity-70">↗</span>
                                <span className="truncate">
                                    {t('sentTo', { label: dstLabel, to: dstToLabel })}
                                </span>
                            </div>
                        );
                    })() : null}
                    {/* Reply Context */}
                    {quoteText && (
                        <div
                            className={`mb-2 text-xs border-l-2 pl-2 italic truncate opacity-80 ${isUserMessage ? "border-blue-400" : "border-[var(--glass-border-subtle)]"
                                }`}
                        >
                            "{quoteText}"
                        </div>
                    )}

                    {presentationRefs.length > 0 ? (
                        <div className="mb-2 flex flex-wrap gap-1.5">
                            {presentationRefs.map((ref, index) => (
                                <button
                                    key={`${String(ev.id || "message")}:presentation-ref:${index}:${String(ref.slot_id || "")}`}
                                    type="button"
                                    onClick={() => onOpenPresentationRef?.(ref, ev)}
                                    className={classNames(
                                        "inline-flex max-w-full items-center rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors",
                                        isUserMessage
                                            ? "border-white/15 bg-white/10 text-white hover:bg-white/15"
                                            : "border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                                    )}
                                    title={getPresentationRefChipLabel(ref)}
                                >
                                    <span className="truncate">{getPresentationRefChipLabel(ref)}</span>
                                </button>
                            ))}
                        </div>
                    ) : null}

                    {isStreaming ? null : streamingActivities.length > 0 ? (
                        <StreamingActivityList
                            groupId={groupId}
                            streamId=""
                            fallbackActivities={streamingActivities}
                        />
                    ) : null}

                    {/* Text Content */}
                    {isStreaming ? (
                        <StreamingContent
                            groupId={groupId}
                            streamId={streamId}
                            fallbackText={messageText}
                            fallbackActivities={streamingActivities}
                            isQueuedOnlyFallbackPlaceholder={isQueuedOnlyPlaceholder}
                            placeholderLabel={streamingPlaceholderLabel}
                        />
                    ) : shouldRenderMarkdown ? (
                        <Suspense
                            fallback={
                                <PlainMessageText
                                    text={messageText}
                                    className="max-w-full"
                                />
                            }
                        >
                            <LazyMarkdownRenderer
                                content={messageText}
                                isDark={isDark}
                                invertText={isUserMessage}
                                className="break-words [overflow-wrap:anywhere] max-w-full"
                            />
                        </Suspense>
                    ) : (
                        <PlainMessageText
                            text={messageText}
                            className="max-w-full"
                        />
                    )}

                    {/* Attachments */}
                    <MessageAttachments
                        attachments={blobAttachments}
                        blobGroupId={blobGroupId}
                        isUserMessage={isUserMessage}
                        isDark={isDark}
                        attachmentKeyPrefix={stableMessageAttachmentKey}
                        downloadTitle={(name) => t('download', { name })}
                    />
                </div>
                </div>

                <div
                    className={classNames(
                        "flex items-center gap-3 mt-1 px-1 text-[10px] transition-opacity",
                        (obligationSummary || ackSummary || visibleReadStatusEntries.length > 0 || replyRequired) ? "justify-between" : "justify-end",
                        "opacity-85 group-hover:opacity-100",
                        "text-[var(--color-text-tertiary)]"
                    )}
                >
                    {obligationSummary ? (
                        readOnly ? (
                            <div className="flex items-center gap-2 min-w-0 rounded-lg px-2 py-1">
                                <span
                                    className={classNames(
                                        "text-[10px] font-semibold tracking-tight",
                                        obligationSummary.done >= obligationSummary.total
                                            ? "text-emerald-600 dark:text-emerald-400"
                                            : "text-amber-600 dark:text-amber-400"
                                    )}
                                >
                                    {obligationSummary.kind === "reply" ? t('reply') : t('ack')} {obligationSummary.done}/{obligationSummary.total}
                                </span>
                            </div>
                        ) : (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm flex items-center gap-2 min-w-0 rounded-lg px-2 py-1",
                                    "hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={onShowRecipients}
                                aria-label={t('showObligationStatus')}
                            >
                                <span
                                    className={classNames(
                                        "text-[10px] font-semibold tracking-tight",
                                        obligationSummary.done >= obligationSummary.total
                                            ? "text-emerald-600 dark:text-emerald-400"
                                            : "text-amber-600 dark:text-amber-400"
                                    )}
                                >
                                    {obligationSummary.kind === "reply" ? t('reply') : t('ack')} {obligationSummary.done}/{obligationSummary.total}
                                </span>
                            </button>
                        )
                    ) : ackSummary ? (
                        readOnly ? (
                            <div className="flex items-center gap-2 min-w-0 rounded-lg px-2 py-1">
                                <span
                                    className={classNames(
                                        "text-[10px] font-semibold tracking-tight",
                                        ackSummary.done >= ackSummary.total
                                            ? "text-emerald-600 dark:text-emerald-400"
                                            : "text-amber-600 dark:text-amber-400"
                                    )}
                                >
                                    {t('ack')} {ackSummary.done}/{ackSummary.total}
                                </span>
                            </div>
                        ) : (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm flex items-center gap-2 min-w-0 rounded-lg px-2 py-1",
                                    "hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={onShowRecipients}
                                aria-label={t('showAckStatus')}
                            >
                                <span
                                    className={classNames(
                                        "text-[10px] font-semibold tracking-tight",
                                        ackSummary.done >= ackSummary.total
                                            ? "text-emerald-600 dark:text-emerald-400"
                                            : "text-amber-600 dark:text-amber-400"
                                    )}
                                >
                                    {t('ack')} {ackSummary.done}/{ackSummary.total}
                                </span>
                            </button>
                        )
                    ) : visibleReadStatusEntries.length > 0 ? (
                        readOnly ? (
                            <div className="flex items-center gap-2 min-w-0 rounded-lg px-2 py-1">
                                <div className="flex items-center gap-2 min-w-0">
                                    {readPreviewEntries.map(([id, cleared]) => (
                                        <span key={id} className="inline-flex items-center gap-1 min-w-0">
                                            <span className="truncate max-w-[10ch]">{displayNameMap.get(id) || id}</span>
                                            <span
                                                className={classNames(
                                                    "text-[10px] font-semibold tracking-tight",
                                                    cleared
                                                        ? isDark
                                                            ? "text-emerald-400"
                                                            : "text-emerald-600"
                                                        : isDark
                                                            ? "text-slate-500"
                                                            : "text-gray-500"
                                                )}
                                                aria-label={cleared ? t('read') : t('pending')}
                                            >
                                                {cleared ? "✓✓" : "✓"}
                                            </span>
                                        </span>
                                    ))}
                                    {readPreviewOverflow > 0 && (
                                        <span className={classNames("text-[10px]", "text-[var(--color-text-tertiary)]")}>
                                            +{readPreviewOverflow}
                                        </span>
                                    )}
                                </div>
                            </div>
                        ) : (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm flex items-center gap-2 min-w-0 rounded-lg px-2 py-1",
                                    "hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={onShowRecipients}
                                aria-label={t('showRecipientStatus')}
                            >
                                <div className="flex items-center gap-2 min-w-0">
                                    {readPreviewEntries.map(([id, cleared]) => (
                                        <span key={id} className="inline-flex items-center gap-1 min-w-0">
                                            <span className="truncate max-w-[10ch]">{displayNameMap.get(id) || id}</span>
                                            <span
                                                className={classNames(
                                                    "text-[10px] font-semibold tracking-tight",
                                                    cleared
                                                        ? isDark
                                                            ? "text-emerald-400"
                                                            : "text-emerald-600"
                                                        : isDark
                                                            ? "text-slate-500"
                                                            : "text-gray-500"
                                                )}
                                                aria-label={cleared ? t('read') : t('pending')}
                                            >
                                                {cleared ? "✓✓" : "✓"}
                                            </span>
                                        </span>
                                    ))}
                                    {readPreviewOverflow > 0 && (
                                        <span className={classNames("text-[10px]", "text-[var(--color-text-tertiary)]")}>
                                            +{readPreviewOverflow}
                                        </span>
                                    )}
                                </div>
                            </button>
                        )
                    ) : null}

                    {!obligationSummary && !ackSummary && replyRequired && (
                        <span
                            className={classNames(
                                "text-[10px] font-semibold tracking-tight",
                                "text-violet-700 dark:text-violet-300"
                            )}
                        >
                            {t('needReply')}
                        </span>
                    )}

                    {!readOnly && (
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {copyableMessageText ? (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm px-2 py-1 rounded-lg text-[11px] font-medium transition-colors",
                                    "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={() => void handleCopyMessageText()}
                                title={copiedMessageText ? t("common:copied") : t("copyText")}
                            >
                                {copiedMessageText ? t("common:copied") : t("copyText")}
                            </button>
                        ) : null}
                        {ev.id && onCopyLink ? (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm px-2 py-1 rounded-lg text-[11px] font-medium transition-colors",
                                    "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={() => onCopyLink(String(ev.id))}
                                title={t('copyLink')}
                            >
                                {t('copyLink')}
                            </button>
                        ) : null}
                        {ev.id && onRelay ? (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm px-2 py-1 rounded-lg text-[11px] font-medium transition-colors",
                                    "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={() => onRelay(ev)}
                                title={t('relayToGroup')}
                            >
                                {t('relay')}
                            </button>
                        ) : null}
                        {canReply ? (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm px-2 py-1 rounded-lg text-[11px] font-medium transition-colors",
                                    "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={onReply}
                            >
                                {t('reply')}
                            </button>
                        ) : null}
                      </div>
                    )}
                </div>
            </div>
        </div>
    );
}, (prevProps, nextProps) => {
    return (
        prevProps.event === nextProps.event &&
        prevProps.actors === nextProps.actors &&
        prevProps.displayNameMap === nextProps.displayNameMap &&
        prevProps.agentState === nextProps.agentState &&
        prevProps.isDark === nextProps.isDark &&
        prevProps.groupId === nextProps.groupId &&
        prevProps.groupLabelById === nextProps.groupLabelById &&
        prevProps.isHighlighted === nextProps.isHighlighted &&
        prevProps.onRelay === nextProps.onRelay &&
        prevProps.onOpenSource === nextProps.onOpenSource &&
        prevProps.onOpenPresentationRef === nextProps.onOpenPresentationRef
    );
});
