import { memo, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { LedgerEvent, Actor, PresenceAgent, getActorAccentColor, ChatMessageData, EventAttachment } from "../types";
import { formatFullTime, formatTime } from "../utils/time";
import { classNames } from "../utils/classNames";
import { useActorDisplayNameMap, getRecipientDisplayName } from "../hooks/useActorDisplayName";

function formatEventLine(ev: LedgerEvent): string {
    if (ev.kind === "chat.message" && ev.data && typeof ev.data === "object") {
        const msg = ev.data as ChatMessageData;
        return String(msg.text || "");
    }
    return "";
}

// Image preview component with loading state and error handling
function ImagePreview({
    href,
    alt,
    isUserMessage,
    isDark,
}: {
    href: string;
    alt: string;
    isUserMessage: boolean;
    isDark: boolean;
}) {
    const [loadError, setLoadError] = useState(false);

    if (loadError) {
        // Fallback to file download link on error
        return (
            <a
                href={href}
                className={classNames(
                    "inline-flex items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors max-w-full",
                    isUserMessage
                        ? "bg-blue-700/50 hover:bg-blue-700 text-white border border-blue-500"
                        : isDark
                            ? "bg-slate-900/50 hover:bg-slate-900 text-slate-300 border border-slate-700"
                            : "bg-gray-50 hover:bg-gray-100 text-gray-700 border border-gray-200"
                )}
                title={`Download ${alt}`}
                download
            >
                <span className="opacity-70">🖼️</span>
                <span className="truncate">{alt}</span>
            </a>
        );
    }

    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="block rounded-lg overflow-hidden max-w-full"
        >
            <img
                src={href}
                alt={alt}
                className="max-w-full max-h-64 sm:max-h-80 object-contain rounded-lg"
                loading="lazy"
                onError={() => setLoadError(true)}
            />
        </a>
    );
}

export interface MessageBubbleProps {
    event: LedgerEvent;
    actors: Actor[];
    presenceAgent: PresenceAgent | null;
    isDark: boolean;
    groupId: string;
    onReply: () => void;
    onShowRecipients: () => void;
}

export const MessageBubble = memo(function MessageBubble({
    event: ev,
    actors,
    presenceAgent,
    isDark,
    groupId,
    onReply,
    onShowRecipients,
}: MessageBubbleProps) {
    const isUserMessage = ev.by === "user";
    const senderAccent = !isUserMessage ? getActorAccentColor(String(ev.by || ""), isDark) : null;

    const [showPresence, setShowPresence] = useState(false);
    const [presencePos, setPresencePos] = useState<{ x: number; y: number } | null>(null);
    const hideTimerRef = useRef<number | null>(null);

    const canShowPresence = useMemo(() => {
        if (isUserMessage) return false;
        const id = String(ev.by || "");
        if (!id) return false;
        return true;
    }, [ev.by, isUserMessage]);

    const clearHide = () => {
        if (hideTimerRef.current != null) {
            window.clearTimeout(hideTimerRef.current);
            hideTimerRef.current = null;
        }
    };

    const scheduleHide = () => {
        clearHide();
        hideTimerRef.current = window.setTimeout(() => {
            setShowPresence(false);
            setPresencePos(null);
            hideTimerRef.current = null;
        }, 160);
    };

    const handlePresenceEnter = (el: HTMLElement) => {
        if (!canShowPresence) return;
        clearHide();
        const rect = el.getBoundingClientRect();
        const width = 360;
        const estimatedHeight = 140;
        const margin = 10;
        let x = rect.left;
        x = Math.min(x, window.innerWidth - width - margin);
        x = Math.max(margin, x);
        const below = rect.bottom + 10;
        const above = rect.top - estimatedHeight - 10;
        let y = below;
        if (below + estimatedHeight > window.innerHeight - margin && above > margin) {
            y = above;
        }
        y = Math.min(y, window.innerHeight - estimatedHeight - margin);
        y = Math.max(margin, y);
        setPresencePos({ x, y });
        setShowPresence(true);
    };

    // Treat data as ChatMessageData.
    const msgData = ev.data as ChatMessageData | undefined;
    const quoteText = msgData?.quote_text;
    const rawAttachments: EventAttachment[] = Array.isArray(msgData?.attachments) ? msgData.attachments : [];
    const blobAttachments = rawAttachments
        .filter((a): a is EventAttachment => a != null && typeof a === "object")
        .map((a) => ({
            kind: String(a.kind || "file"),
            path: String(a.path || ""),
            title: String(a.title || ""),
            bytes: Number(a.bytes || 0),
            mime_type: String(a.mime_type || ""),
        }))
        .filter((a) => a.path.startsWith("state/blobs/"));

    const readStatus = ev._read_status;
    const recipients = msgData?.to;

    // Memoized lookup map for O(1) display name access
    const displayNameMap = useActorDisplayNameMap(actors);

    const visibleReadStatusEntries = useMemo(() => {
        if (!readStatus) return [];
        return actors
            .map((a) => String(a.id || ""))
            .filter((id) => id && Object.prototype.hasOwnProperty.call(readStatus, id))
            .map((id) => [id, !!readStatus[id]] as const);
    }, [actors, readStatus]);

    const toLabel = useMemo(() => {
        if (!recipients || recipients.length === 0) return "@all";
        return recipients
            .map(r => getRecipientDisplayName(r, displayNameMap))
            .join(", ");
    }, [recipients, displayNameMap]);

    const readPreviewEntries = visibleReadStatusEntries.slice(0, 3);
    const readPreviewOverflow = Math.max(0, visibleReadStatusEntries.length - readPreviewEntries.length);

    return (
        <div
            className={classNames(
                "flex gap-2 sm:gap-3 group",
                isUserMessage
                    ? "flex-col items-end sm:items-start sm:flex-row-reverse"
                    : "flex-col items-start sm:flex-row"
            )}
        >
            {/* Desktop Avatar (Hidden on mobile) */}
            <div
                className={classNames(
                    "hidden sm:flex flex-shrink-0 w-8 h-8 rounded-full items-center justify-center text-xs font-bold shadow-sm mt-1",
                    isUserMessage
                        ? "bg-gradient-to-br from-blue-500 to-blue-600 text-white"
                        : isDark
                            ? "bg-slate-700 text-slate-200"
                            : "bg-white border border-gray-200 text-gray-700",
                    !isUserMessage && senderAccent ? `ring-1 ring-inset ${senderAccent.ring}` : ""
                )}
                onPointerEnter={(e) => handlePresenceEnter(e.currentTarget)}
                onPointerLeave={() => scheduleHide()}
            >
                {isUserMessage ? "U" : (ev.by || "?")[0].toUpperCase()}
            </div>

            {/* Message Content */}
            <div
                className={classNames(
                    "flex flex-col w-full sm:w-auto sm:max-w-[75%]",
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
                    <div
                        className={classNames(
                            "flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shadow-sm",
                            isUserMessage
                                ? "bg-gradient-to-br from-blue-500 to-blue-600 text-white"
                                : isDark
                                    ? "bg-slate-700 text-slate-200"
                                    : "bg-white border border-gray-200 text-gray-700",
                            !isUserMessage && senderAccent ? `ring-1 ring-inset ${senderAccent.ring}` : ""
                        )}
                        onPointerEnter={(e) => handlePresenceEnter(e.currentTarget)}
                        onPointerLeave={() => scheduleHide()}
                    >
                        {isUserMessage ? "U" : (ev.by || "?")[0].toUpperCase()}
                    </div>
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
                        {ev.by}
                    </span>
                    <span className={`text-[10px] flex-shrink-0 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                        {formatTime(ev.ts)}
                    </span>
                    <span
                        className={classNames(
                            "text-[10px] min-w-0 truncate",
                            isDark ? "text-slate-500" : "text-gray-500"
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
                                    ? "text-slate-400"
                                    : "text-gray-500"
                                : senderAccent
                                    ? senderAccent.text
                                    : isDark
                                        ? "text-slate-400"
                                        : "text-gray-500"
                        )}
                    >
                        {ev.by}
                    </span>
                    <span className={`text-[10px] flex-shrink-0 ${isDark ? "text-slate-600" : "text-gray-400"}`}>
                        {formatTime(ev.ts)}
                    </span>
                    <span className={classNames("text-[10px] min-w-0 truncate", isDark ? "text-slate-500" : "text-gray-500")} title={`to ${toLabel}`}>
                        to {toLabel}
                    </span>
                </div>

                {/* Bubble */}
                <div
                    className={classNames(
                        "relative px-4 py-2.5 shadow-sm text-sm leading-relaxed max-w-[85%] sm:max-w-none",
                        isUserMessage
                            ? "bg-blue-600 text-white rounded-2xl rounded-tr-none"
                            : isDark
                                ? "bg-slate-800 text-slate-200 border border-slate-700 rounded-2xl rounded-tl-none"
                                : "bg-white text-gray-800 border border-gray-200 rounded-2xl rounded-tl-none"
                    )}
                >
                    {/* Reply Context */}
                    {quoteText && (
                        <div
                            className={`mb-2 text-xs border-l-2 pl-2 italic truncate opacity-80 ${isUserMessage ? "border-blue-400" : isDark ? "border-slate-600" : "border-gray-300"
                                }`}
                        >
                            "{quoteText}"
                        </div>
                    )}

                    {/* Text Content */}
                    <div className="whitespace-pre-wrap break-words">{formatEventLine(ev)}</div>

                    {/* Attachments */}
                    {blobAttachments.length > 0 && groupId && (() => {
                        const imageAttachments = blobAttachments.filter((a) =>
                            a.mime_type.startsWith("image/")
                        );
                        const fileAttachments = blobAttachments.filter((a) =>
                            !a.mime_type.startsWith("image/")
                        );
                        return (
                            <>
                                {/* Image previews */}
                                {imageAttachments.length > 0 && (
                                    <div className="mt-3 flex flex-wrap gap-2">
                                        {imageAttachments.map((a, i) => {
                                            const parts = a.path.split("/");
                                            const blobName = parts[parts.length - 1] || "";
                                            const href = `/api/v1/groups/${encodeURIComponent(groupId)}/blobs/${encodeURIComponent(blobName)}`;
                                            const label = a.title || blobName;
                                            return (
                                                <ImagePreview
                                                    key={`img-${blobName}:${i}`}
                                                    href={href}
                                                    alt={label}
                                                    isUserMessage={isUserMessage}
                                                    isDark={isDark}
                                                />
                                            );
                                        })}
                                    </div>
                                )}
                                {/* File attachments */}
                                {fileAttachments.length > 0 && (
                                    <div className="mt-3 flex flex-wrap gap-2">
                                        {fileAttachments.map((a, i) => {
                                            const parts = a.path.split("/");
                                            const blobName = parts[parts.length - 1] || "";
                                            const href = `/api/v1/groups/${encodeURIComponent(groupId)}/blobs/${encodeURIComponent(blobName)}`;
                                            const label = a.title || blobName || "file";
                                            return (
                                                <a
                                                    key={`file-${blobName}:${i}`}
                                                    href={href}
                                                    className={classNames(
                                                        "inline-flex items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors max-w-full",
                                                        isUserMessage
                                                            ? "bg-blue-700/50 hover:bg-blue-700 text-white border border-blue-500"
                                                            : isDark
                                                                ? "bg-slate-900/50 hover:bg-slate-900 text-slate-300 border border-slate-700"
                                                                : "bg-gray-50 hover:bg-gray-100 text-gray-700 border border-gray-200"
                                                    )}
                                                    title={`Download ${label}`}
                                                    download
                                                >
                                                    <span className="opacity-70">📎</span>
                                                    <span className="truncate">{label}</span>
                                                </a>
                                            );
                                        })}
                                    </div>
                                )}
                            </>
                        );
                    })()}
                </div>

                <div
                    className={classNames(
                        "flex items-center gap-3 mt-1 px-1 text-[10px] transition-opacity",
                        visibleReadStatusEntries.length > 0 ? "justify-between" : "justify-end",
                        "opacity-70 group-hover:opacity-100",
                        isDark ? "text-slate-500" : "text-gray-500"
                    )}
                >
                    {visibleReadStatusEntries.length > 0 && (
                        <button
                            type="button"
                            className={classNames(
                                "touch-target-sm flex items-center gap-2 min-w-0 rounded-lg px-2 py-1",
                                isDark ? "hover:bg-slate-800/60" : "hover:bg-gray-100"
                            )}
                            onClick={onShowRecipients}
                            aria-label="Show recipient status"
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
                                            aria-label={cleared ? "read" : "pending"}
                                        >
                                            {cleared ? "✓✓" : "✓"}
                                        </span>
                                    </span>
                                ))}
                                {readPreviewOverflow > 0 && (
                                    <span className={classNames("text-[10px]", isDark ? "text-slate-500" : "text-gray-500")}>
                                        +{readPreviewOverflow}
                                    </span>
                                )}
                            </div>
                        </button>
                    )}

                    <button
                        type="button"
                        className={classNames(
                            "touch-target-sm px-1 rounded hover:underline transition-colors",
                            isDark ? "text-slate-400 hover:text-slate-200" : "text-gray-500 hover:text-gray-700"
                        )}
                        onClick={onReply}
                    >
                        Reply
                    </button>
                </div>
            </div>

            {showPresence && presencePos && canShowPresence && typeof document !== "undefined" &&
                createPortal(
                    <div
                        className={classNames(
                            "fixed z-[200] w-[360px] rounded-xl border shadow-2xl px-3 py-2",
                            isDark
                                ? "bg-slate-900/95 border-white/10 text-slate-200"
                                : "bg-white/95 border-black/10 text-gray-900"
                        )}
                        style={{ left: presencePos.x, top: presencePos.y }}
                        onPointerEnter={() => clearHide()}
                        onPointerLeave={() => scheduleHide()}
                        role="status"
                    >
                        <div className="flex items-center gap-2">
                            <div
                                className={classNames("text-xs font-semibold", isDark ? "text-slate-200" : "text-gray-900")}
                            >
                                {String(ev.by || "")}
                            </div>
                            {presenceAgent?.updated_at ? (
                                <div
                                    className={classNames(
                                        "ml-auto text-xs tabular-nums",
                                        isDark ? "text-slate-400" : "text-gray-500"
                                    )}
                                    title={formatFullTime(presenceAgent.updated_at)}
                                >
                                    Updated {formatTime(presenceAgent.updated_at)}
                                </div>
                            ) : null}
                        </div>
                        <div
                            className={classNames("mt-1 text-xs whitespace-pre-wrap", isDark ? "text-slate-300" : "text-gray-700")}
                        >
                            {presenceAgent?.status ? presenceAgent.status : "No presence yet"}
                        </div>
                    </div>,
                    document.body
                )}
        </div>
    );
}, (prevProps, nextProps) => {
    return (
        prevProps.event === nextProps.event &&
        prevProps.actors === nextProps.actors &&
        prevProps.presenceAgent === nextProps.presenceAgent &&
        prevProps.isDark === nextProps.isDark &&
        prevProps.groupId === nextProps.groupId
    );
});
