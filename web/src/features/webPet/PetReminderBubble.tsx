import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("modals");
  const [closingFingerprint, setClosingFingerprint] = useState<string | null>(null);
  const autoHideTimeoutRef = useRef<number | null>(null);
  const dismissTimeoutRef = useRef<number | null>(null);

  const clearAutoHideTimer = useCallback(() => {
    if (autoHideTimeoutRef.current === null) return;
    window.clearTimeout(autoHideTimeoutRef.current);
    autoHideTimeoutRef.current = null;
  }, []);

  const clearDismissTimer = useCallback(() => {
    if (dismissTimeoutRef.current === null) return;
    window.clearTimeout(dismissTimeoutRef.current);
    dismissTimeoutRef.current = null;
  }, []);

  const startDismiss = useCallback(
    (fingerprint: string) => {
      if (!fingerprint) return;
      if (closingFingerprint === fingerprint) return;

      clearAutoHideTimer();
      clearDismissTimer();
      setClosingFingerprint(fingerprint);

      dismissTimeoutRef.current = window.setTimeout(() => {
        dismissTimeoutRef.current = null;
        onDismiss(fingerprint);
      }, EXIT_ANIMATION_MS);
    },
    [clearAutoHideTimer, clearDismissTimer, closingFingerprint, onDismiss],
  );

  useEffect(() => {
    clearAutoHideTimer();
    clearDismissTimer();

    if (!reminder) {
      return;
    }

    autoHideTimeoutRef.current = window.setTimeout(() => {
      autoHideTimeoutRef.current = null;
      startDismiss(reminder.fingerprint);
    }, AUTO_HIDE_MS);

    return () => {
      clearAutoHideTimer();
      clearDismissTimer();
    };
  }, [clearAutoHideTimer, clearDismissTimer, reminder, startDismiss]);

  const handleDismiss = useCallback(() => {
    if (!reminder) return;
    startDismiss(reminder.fingerprint);
  }, [reminder, startDismiss]);

  const handleAction = useCallback(() => {
    if (!reminder) return;
    onAction?.(reminder.action);
    startDismiss(reminder.fingerprint);
  }, [onAction, reminder, startDismiss]);

  const displayAgent = reminder?.agent === "system"
    ? t("webPet.systemAgent", { defaultValue: "System" })
    : reminder?.agent || "";

  const label = useMemo(() => {
    if (!reminder) return "";
    return reminder.agent === "system"
      ? reminder.summary
      : `${displayAgent}: ${reminder.summary}`;
  }, [displayAgent, reminder]);

  if (!reminder) {
    return null;
  }

  const isClosing = closingFingerprint === reminder.fingerprint;
  const kindLabel = String(
    t(`webPet.kind.${reminder.kind}` as never, {
      defaultValue: reminder.kind.replace(/_/g, " "),
    } as never),
  );

  return (
    <div
      className={`pointer-events-auto absolute bottom-[calc(100%+14px)] left-1/2 z-[1110] w-[min(280px,calc(100vw-32px))] -translate-x-1/2 transform transition-all duration-200 ${
        isClosing ? "translate-y-2 opacity-0" : "translate-y-0 opacity-100"
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
      aria-live={reminder.ephemeral ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <div className="glass-modal rounded-2xl border border-[var(--glass-border-subtle)] px-3 py-2.5 shadow-2xl">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
              {kindLabel}
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
            aria-label={t("webPet.dismissReminderAria", {
              defaultValue: "Dismiss reminder",
            })}
          >
            ×
          </button>
        </div>
      </div>
    </div>
  );
}
