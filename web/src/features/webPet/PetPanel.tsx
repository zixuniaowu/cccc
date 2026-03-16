import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import type { PanelData } from "./types";

interface PetPanelProps {
  panelData: PanelData;
  /** Panel opens on this side of the cat */
  align?: "left" | "right";
  onClose?: () => void;
  catSize?: number;
}

function countAgentsByState(panelData: PanelData) {
  return panelData.agents.reduce(
    (counts, agent) => {
      if (agent.state === "working") counts.working += 1;
      if (agent.state === "busy") counts.busy += 1;
      if (agent.state === "needs_you") counts.needsYou += 1;
      return counts;
    },
    { working: 0, busy: 0, needsYou: 0 },
  );
}

export function PetPanel({ panelData, align = "left", onClose, catSize = 80 }: PetPanelProps) {
  const { t } = useTranslation("modals");
  const counts = countAgentsByState(panelData);
  const actionItems = panelData.actionItems.slice(0, 3);

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
      id="web-pet-panel"
      className="glass-modal pointer-events-auto z-[1100] w-[min(320px,calc(100vw-24px))] rounded-2xl border border-[var(--glass-border-subtle)] text-[var(--color-text-primary)] shadow-2xl"
      style={{
        position: "absolute",
        bottom: 0,
        maxHeight: "calc(100vh - 48px)",
        ...horizontalStyle,
      }}
      aria-label={t("webPet.panelAria", { defaultValue: "Web Pet panel" })}
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
                aria-label={t("webPet.closePanelAria", { defaultValue: "Close panel" })}
              >
                ×
              </button>
            ) : null}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <span className={countPillClass}>
              {t("webPet.countWorking", {
                defaultValue: "{{count}} working",
                count: counts.working,
              })}
            </span>
            <span className={countPillClass}>
              {t("webPet.countBusy", {
                defaultValue: "{{count}} busy",
                count: counts.busy,
              })}
            </span>
            <span className={countPillClass}>
              {t("webPet.countNeedsYou", {
                defaultValue: "{{count}} needs you",
                count: counts.needsYou,
              })}
            </span>
          </div>
        </div>

        <div className="overflow-y-auto px-4 py-3">
          {actionItems.length > 0 ? (
            <div className="space-y-2">
              {actionItems.map((item) => (
                <div
                  key={item.id}
                  className="rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 backdrop-blur-sm dark:border-white/5"
                  title={item.summary}
                >
                  <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
                    {item.agent}
                  </div>
                  <div className="mt-1 text-sm leading-5 text-[var(--color-text-primary)]">
                    {item.summary}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-[var(--glass-border-subtle)] px-3 py-4 text-sm text-[var(--color-text-secondary)]">
              {t("webPet.noActionItems", {
                defaultValue: "No action items right now.",
              })}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
