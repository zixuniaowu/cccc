import { memo } from "react";
import { classNames } from "../utils/classNames";
import type { ChatStreamingIndicatorItem } from "../hooks/useChatTab";

function formatIndicatorKind(kind: string): string {
  const normalized = String(kind || "").trim().toLowerCase();
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
      return normalized || "stream";
  }
}

export const ChatStreamingIndicators = memo(function ChatStreamingIndicators({
  items,
  isDark,
}: {
  items: ChatStreamingIndicatorItem[];
  isDark: boolean;
}) {
  if (items.length <= 0) return null;

  return (
    <div className="flex-shrink-0 px-4 pb-2">
      <div className="flex flex-col gap-2">
        {items.map((item) => (
          <div
            key={item.actorId}
            className={classNames(
              "rounded-2xl border px-3 py-2 backdrop-blur-md",
              isDark
                ? "border-slate-700/50 bg-slate-900/55 text-slate-200"
                : "border-gray-200 bg-white/78 text-gray-800",
            )}
          >
            <div className="flex items-center gap-2 text-[11px]">
              <span className={classNames("font-semibold", isDark ? "text-slate-100" : "text-gray-900")}>
                {item.actorName}
              </span>
              <span className="inline-flex items-center gap-1 text-[var(--color-text-tertiary)]">
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
            {item.activities.length > 0 ? (
              <div className="mt-1 flex flex-col gap-1">
                {item.activities.map((activity) => (
                  <div key={activity.id} className="flex items-start gap-2 text-[11px] leading-4">
                    <span className="min-w-[2.75rem] font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                      {formatIndicatorKind(activity.kind)}
                    </span>
                    <span className="min-w-0 break-words [overflow-wrap:anywhere] text-[var(--color-text-secondary)]">
                      {activity.summary}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-1 text-[11px] text-[var(--color-text-secondary)]">
                {item.placeholderLabel}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
});
