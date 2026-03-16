import type { PanelData } from "./types";

interface PetPanelProps {
  panelData: PanelData;
  align?: "left" | "right";
  onClose?: () => void;
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

export function PetPanel({ panelData, align = "left", onClose }: PetPanelProps) {
  const counts = countAgentsByState(panelData);
  const actionItems = panelData.actionItems.slice(0, 3);

  return (
    <section
      className={`glass-modal pointer-events-auto absolute bottom-[calc(100%+12px)] z-[1100] w-[min(320px,calc(100vw-24px))] rounded-2xl border border-[var(--glass-border-subtle)] px-4 py-3 text-[var(--color-text-primary)] shadow-2xl ${
        align === "right" ? "right-0" : "left-0"
      }`}
      aria-label="Web Pet panel"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                panelData.connection.connected ? "bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.65)]" : "bg-rose-400 shadow-[0_0_12px_rgba(251,113,133,0.45)]"
              }`}
              aria-hidden="true"
            />
            <span className="truncate">{panelData.teamName || "Team"}</span>
          </div>
          <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
            {panelData.connection.message}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-[11px] text-[var(--color-text-secondary)]">
          <span>{counts.working} working</span>
          <span>{counts.busy} busy</span>
          <span>{counts.needsYou} needs you</span>
        </div>
        {onClose ? (
          <button
            type="button"
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-sm text-[var(--color-text-secondary)] transition hover:bg-white/10 hover:text-[var(--color-text-primary)]"
            onClick={onClose}
            aria-label="Close panel"
          >
            ×
          </button>
        ) : null}
      </div>

      <div className="mt-3 border-t border-[var(--glass-border-subtle)] pt-3">
        {actionItems.length > 0 ? (
          <div className="space-y-2">
            {actionItems.map((item) => (
              <div
                key={item.id}
                className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 backdrop-blur-sm dark:border-white/5"
                title={item.summary}
              >
                <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
                  {item.agent || "system"}
                </div>
                <div className="mt-1 text-sm leading-5 text-[var(--color-text-primary)]">
                  {item.summary}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-[var(--glass-border-subtle)] px-3 py-4 text-sm text-[var(--color-text-secondary)]">
            暂无待处理事项
          </div>
        )}
      </div>
    </section>
  );
}
