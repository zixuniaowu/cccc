import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PetReminder, ReminderAction } from "./types";

const AUTO_HIDE_MS = 6000;
const EXIT_ANIMATION_MS = 220;

interface PetReminderBubbleProps {
  reminder: PetReminder | null;
  onDismiss: (fingerprint: string) => void;
  onAction?: (action: ReminderAction) => void;
}

export function PetReminderBubble({
  reminder,
  onDismiss,
  onAction,
}: PetReminderBubbleProps) {
  const [renderedReminder, setRenderedReminder] = useState<PetReminder | null>(
    reminder,
  );
  const [visible, setVisible] = useState(Boolean(reminder));
  const autoHideTimeoutRef = useRef<number | null>(null);

  const clearAutoHideTimer = useCallback(() => {
    if (autoHideTimeoutRef.current === null) return;
    window.clearTimeout(autoHideTimeoutRef.current);
    autoHideTimeoutRef.current = null;
  }, []);

  useEffect(() => {
    if (!reminder) {
      clearAutoHideTimer();
      setVisible(false);
      const timeout = window.setTimeout(() => {
        setRenderedReminder(null);
      }, EXIT_ANIMATION_MS);
      return () => window.clearTimeout(timeout);
    }

    setRenderedReminder(reminder);
    const frame = window.requestAnimationFrame(() => {
      setVisible(true);
    });

    return () => window.cancelAnimationFrame(frame);
  }, [clearAutoHideTimer, reminder]);

  useEffect(() => {
    clearAutoHideTimer();
    if (!reminder) return;

    autoHideTimeoutRef.current = window.setTimeout(() => {
      autoHideTimeoutRef.current = null;
      onDismiss(reminder.fingerprint);
    }, AUTO_HIDE_MS);

    return clearAutoHideTimer;
  }, [clearAutoHideTimer, onDismiss, reminder]);

  const handleDismiss = useCallback(() => {
    if (!renderedReminder) return;
    clearAutoHideTimer();
    onDismiss(renderedReminder.fingerprint);
  }, [clearAutoHideTimer, onDismiss, renderedReminder]);

  const handleAction = useCallback(() => {
    if (!renderedReminder) return;
    clearAutoHideTimer();
    onAction?.(renderedReminder.action);
    onDismiss(renderedReminder.fingerprint);
  }, [clearAutoHideTimer, onAction, onDismiss, renderedReminder]);

  const label = useMemo(() => {
    if (!renderedReminder) return "";
    return renderedReminder.agent === "system"
      ? renderedReminder.summary
      : `${renderedReminder.agent}: ${renderedReminder.summary}`;
  }, [renderedReminder]);

  if (!renderedReminder) {
    return null;
  }

  return (
    <div
      className={`pointer-events-auto absolute bottom-[calc(100%+14px)] left-1/2 z-[1110] w-[min(280px,calc(100vw-32px))] -translate-x-1/2 transform transition-all duration-200 ${
        visible
          ? "translate-y-0 opacity-100"
          : "translate-y-2 opacity-0"
      }`}
      onClick={handleAction}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          handleAction();
        }
      }}
      aria-live={renderedReminder.ephemeral ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <div className="glass-modal rounded-2xl border border-[var(--glass-border-subtle)] px-3 py-2.5 shadow-2xl">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
              {renderedReminder.kind.replace("_", " ")}
            </div>
            <div
              className="mt-1 text-sm leading-5 text-[var(--color-text-primary)]"
              title={label}
            >
              {label}
            </div>
          </div>
          <button
            type="button"
            className="rounded-full px-2 py-1 text-xs text-[var(--color-text-secondary)] transition hover:bg-white/10 hover:text-[var(--color-text-primary)]"
            onClick={(event) => {
              event.stopPropagation();
              handleDismiss();
            }}
            aria-label="关闭提醒"
          >
            ×
          </button>
        </div>
      </div>
    </div>
  );
}
