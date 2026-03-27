import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { getReminderActionButtons } from "./reminderActions";
import type { PanelData, PetReminder } from "./types";

interface PetPanelProps {
  panelData: PanelData;
  reminders: PetReminder[];
  panelId?: string;
  /** Panel opens on this side of the cat */
  align?: "left" | "right";
  onClose?: () => void;
  onAction?: (reminder: PetReminder) => void;
  catSize?: number;
}

function countAgentsByState(panelData: PanelData) {
  return panelData.agents.reduce(
    (counts, agent) => {
      if (agent.state === "working") counts.working += 1;
      if (agent.state === "busy") counts.busy += 1;
      return counts;
    },
    { working: 0, busy: 0 },
  );
}

export function PetPanel({
  panelData,
  reminders,
  panelId = "web-pet-panel",
  align = "left",
  onClose,
  onAction,
  catSize = 80,
}: PetPanelProps) {
  const { t } = useTranslation("webPet");
  const counts = countAgentsByState(panelData);
  const actionableReminders = reminders.slice(0, 1);

  // Panel opens horizontally beside the cat (not above).
  // "right" align = cat is on right side → panel opens to the LEFT of the cat.
  // "left" align = cat is on left side → panel opens to the RIGHT of the cat.
  const gap = 12;
  const horizontalStyle: CSSProperties =
    align === "right"
      ? { right: catSize + gap }
      : { left: catSize + gap };
  const countPillClass =
    "inline-flex items-center rounded-full border border-[var(--glass-border-subtle)] bg-white/5 px-2.5 py-1 text-[11px] font-medium text-[var(--color-text-secondary)]";

  return (
    <section
      id={panelId}
      className="glass-modal pointer-events-auto z-[1100] w-[min(320px,calc(100vw-24px))] rounded-2xl border border-[var(--glass-border-subtle)] text-[var(--color-text-primary)] shadow-2xl"
      style={{
        position: "absolute",
        bottom: 0,
        maxHeight: "calc(100vh - 48px)",
        ...horizontalStyle,
      }}
      aria-label={t("panelAria", { defaultValue: "Web Pet panel" })}
    >
      <div className="flex max-h-[inherit] flex-col overflow-hidden rounded-2xl">
        <div className="shrink-0 border-b border-[var(--glass-border-subtle)] px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">
                {panelData.teamName}
              </div>
              <div className="mt-1 flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    panelData.connection.connected
                      ? "bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.65)]"
                      : "bg-rose-400 shadow-[0_0_12px_rgba(251,113,133,0.45)]"
                  }`}
                  aria-hidden="true"
                />
                <span>{panelData.connection.message}</span>
              </div>
            </div>
            {onClose ? (
              <button
                type="button"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-sm text-[var(--color-text-secondary)] transition hover:bg-white/10 hover:text-[var(--color-text-primary)]"
                onClick={onClose}
                aria-label={t("closePanelAria", { defaultValue: "Close panel" })}
              >
                ×
              </button>
            ) : null}
          </div>
          {panelData.taskProgress && panelData.taskProgress.total > 0 ? (
            <div className="mt-3">
              <div className="flex items-center justify-between text-[11px] text-[var(--color-text-secondary)]">
                <span>
                  {t("taskProgress", {
                    defaultValue: "{{done}}/{{total}} done",
                    done: panelData.taskProgress.done,
                    total: panelData.taskProgress.total,
                  })}
                </span>
                {panelData.taskProgress.active > 0 ? (
                  <span>
                    {t("taskActive", {
                      defaultValue: "{{count}} active",
                      count: panelData.taskProgress.active,
                    })}
                  </span>
                ) : null}
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-emerald-400/80 transition-all duration-500"
                  style={{
                    width: `${Math.round((panelData.taskProgress.done / panelData.taskProgress.total) * 100)}%`,
                  }}
                />
              </div>
            </div>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2">
            <span className={countPillClass}>
              {t("countWorking", {
                defaultValue: "{{count}} working",
                count: counts.working,
              })}
            </span>
            <span className={countPillClass}>
              {t("countBusy", {
                defaultValue: "{{count}} busy",
                count: counts.busy,
              })}
            </span>
          </div>
        </div>

        <div className="overflow-y-auto px-4 py-3">
          {actionableReminders.length > 0 ? (
            <div className="space-y-2">
              {actionableReminders.map((reminder) => {
                const actionButtons = getReminderActionButtons(reminder);
                const clickable =
                  actionButtons.length > 0 && !!onAction;
                const suggestion = String(reminder.suggestion || "").trim();
                const suggestionPreview = String(reminder.suggestionPreview || "").trim();
                const bodyText = suggestionPreview || suggestion || reminder.summary;
                const showMeta = !suggestion && !suggestionPreview;
                const kindLabel = String(
                  t(`kind.${reminder.kind}`, {
                    defaultValue: reminder.kind.replace(/_/g, " "),
                  }),
                );
                return (
                  <div
                    key={reminder.id}
                    role={clickable ? "button" : undefined}
                    tabIndex={clickable ? 0 : undefined}
                    className={`rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 backdrop-blur-sm dark:border-white/5 ${
                      clickable
                        ? "cursor-pointer transition hover:border-white/20 hover:bg-white/10 active:scale-[0.98]"
                        : ""
                    }`}
                    title={reminder.summary}
                    onClick={
                      clickable
                        ? () => onAction(reminder)
                        : undefined
                    }
                    onKeyDown={
                      clickable
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onAction(reminder);
                            }
                          }
                        : undefined
                    }
                  >
                    {showMeta ? (
                      <div className="flex items-center justify-between">
                        <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
                          {reminder.agent}
                        </div>
                        <span className="text-[10px] text-[var(--color-text-secondary)] opacity-60">
                          {kindLabel}
                        </span>
                      </div>
                    ) : null}
                    <div className={`${showMeta ? "mt-1" : ""} text-sm leading-5 text-[var(--color-text-primary)]`}>
                      {bodyText}
                    </div>
                    {clickable ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {actionButtons.map((button) => (
                          <button
                            key={`${reminder.id}:${button.labelKey}`}
                            type="button"
                            className="rounded-md bg-white/10 px-2 py-0.5 text-xs font-medium text-[var(--color-text-primary)] transition hover:bg-white/20 active:bg-white/25"
                            onClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              onAction(reminder);
                            }}
                            onPointerDown={(event) => {
                              event.stopPropagation();
                            }}
                          >
                            {t(`action.${button.labelKey}`, {
                              defaultValue: button.fallback,
                            })}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-[var(--glass-border-subtle)] px-3 py-4 text-sm text-[var(--color-text-secondary)]">
              {t("noActionItems", {
                defaultValue: "No action items right now.",
              })}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
