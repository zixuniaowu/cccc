import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { getReminderActionButtons } from "./reminderActions";
import type { PetReminder, ReminderAction } from "./types";

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
  const { t } = useTranslation("webPet");

  const handleDismiss = useCallback(() => {
    if (!reminder) return;
    onDismiss(reminder.fingerprint);
  }, [onDismiss, reminder]);

  const handleButtonAction = useCallback(
    (action: ReminderAction) => {
      if (!reminder) return;
      onAction?.(action);
      onDismiss(reminder.fingerprint);
    },
    [onAction, onDismiss, reminder],
  );

  const displayAgent = reminder?.agent === "system"
    ? t("systemAgent", { defaultValue: "System" })
    : reminder?.agent || "";

  const label = useMemo(() => {
    if (!reminder) return "";
    if (reminder.kind === "mention" || reminder.kind === "reply_required") {
      return reminder.summary;
    }
    return reminder.agent === "system"
      ? reminder.summary
      : `${displayAgent}: ${reminder.summary}`;
  }, [displayAgent, reminder]);

  const actionButtons = useMemo(
    () =>
      reminder
        ? getReminderActionButtons(reminder).map((button) => ({
            label: String(
              t(`action.${button.labelKey}`, { defaultValue: button.fallback }),
            ),
            action: button.action,
          }))
        : [],
    [reminder, t],
  );

  if (!reminder) {
    return null;
  }

  const kindLabel = String(
    t(`kind.${reminder.kind}`, {
      defaultValue: reminder.kind.replace(/_/g, " "),
    }),
  );
  const suggestion = String(reminder.suggestion || "").trim();
  const suggestionPreview = String(reminder.suggestionPreview || "").trim();
  const bodyText = suggestionPreview || suggestion || label;
  const showMeta = !suggestion && !suggestionPreview;

  return (
    <div
      className="pointer-events-auto absolute z-[1110] w-[min(280px,calc(100vw-32px))]"
      style={{
        bottom: "calc(100% + 14px)",
        left: "50%",
        transform: "translateX(-50%)",
      }}
      onPointerDown={(event) => {
        event.stopPropagation();
      }}
      aria-live={reminder.ephemeral ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <div className="glass-modal relative rounded-2xl border border-[var(--glass-border-subtle)] px-3 py-2 shadow-2xl">
        <button
          type="button"
          className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full text-sm text-[var(--color-text-secondary)] transition hover:bg-white/10 hover:text-[var(--color-text-primary)]"
          onPointerDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            handleDismiss();
          }}
          aria-label={t("dismissReminderAria", {
            defaultValue: "Dismiss reminder",
          })}
        >
          ×
        </button>
        <div className="pr-8">
          <div className="min-w-0">
            {showMeta ? (
              <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
                {kindLabel}
              </div>
            ) : null}
            <div
              className={`${showMeta ? "mt-1" : ""} text-sm leading-5 text-[var(--color-text-primary)]`}
              title={bodyText}
            >
              {bodyText}
            </div>
            <div className="mt-1.5 flex gap-1.5">
              {actionButtons.map((btn) => (
                <button
                  key={btn.action.type}
                  type="button"
                  className="rounded-md bg-white/10 px-2 py-0.5 text-xs font-medium text-[var(--color-text-primary)] transition hover:bg-white/20 active:bg-white/25"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    handleButtonAction(btn.action);
                  }}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                  }}
                >
                  {btn.label}
                </button>
              ))}
              {suggestion ? (
                <button
                  type="button"
                  className="rounded-md bg-white/5 px-2 py-0.5 text-xs font-medium text-[var(--color-text-secondary)] transition hover:bg-white/10 hover:text-[var(--color-text-primary)] active:bg-white/15"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    handleDismiss();
                  }}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                  }}
                >
                  {t("action.dismiss", { defaultValue: "Dismiss" })}
                </button>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
