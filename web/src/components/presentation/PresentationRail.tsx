import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { GroupPresentation, PresentationSlot } from "../../types";
import { classNames } from "../../utils/classNames";
import { ensurePresentation } from "../../utils/presentation";

type PresentationRailProps = {
  mode: "rail" | "panel";
  presentation: GroupPresentation | null;
  isDark: boolean;
  readOnly?: boolean;
  onOpenSlot: (slotId: string) => void;
  onPinSlot?: (slotId: string) => void;
};

function formatUpdatedAt(value: string | undefined, locale: string): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleString(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getCardTypeLabel(type: string, t: (key: string, options?: Record<string, unknown>) => string): string {
  switch (String(type || "").trim()) {
    case "markdown":
      return t("presentationTypeMarkdown", { defaultValue: "Markdown" });
    case "table":
      return t("presentationTypeTable", { defaultValue: "Table" });
    case "image":
      return t("presentationTypeImage", { defaultValue: "Image" });
    case "pdf":
      return t("presentationTypePdf", { defaultValue: "PDF" });
    case "web_preview":
      return t("presentationTypeWebPreview", { defaultValue: "Web" });
    default:
      return t("presentationTypeFile", { defaultValue: "File" });
  }
}

function getFilledSlots(presentation: GroupPresentation | null): PresentationSlot[] {
  return Array.isArray(presentation?.slots)
    ? presentation!.slots.filter((slot) => !!slot.card)
    : [];
}

function getPreviewText(slot: PresentationSlot, t: (key: string, options?: Record<string, unknown>) => string): string {
  const card = slot.card;
  if (!card) return "";
  const summary = String(card.summary || "").trim();
  if (summary) return summary;
  const sourceLabel = String(card.source_label || "").trim();
  if (sourceLabel) return sourceLabel;
  if (card.card_type === "table") {
    const rowCount = card.content.table?.rows?.length || 0;
    return t("presentationRowsSummary", {
      count: rowCount,
      defaultValue: `${rowCount} rows`,
    });
  }
  return getCardTypeLabel(card.card_type, t);
}

function getRailSlotTone(cardType: string, isDark: boolean): {
  buttonClassName: string;
  overlayClassName: string;
  dotClassName: string;
} {
  switch (String(cardType || "").trim()) {
    case "markdown":
      return isDark
        ? {
            buttonClassName: "border-emerald-400/28 bg-emerald-400/[0.08] text-emerald-100 hover:border-emerald-300/45 hover:bg-emerald-400/[0.12]",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(52,211,153,0.18),transparent_42%),linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02))]",
            dotClassName: "bg-emerald-300 ring-emerald-100/20",
          }
        : {
            buttonClassName: "border-emerald-500/25 bg-emerald-50/90 text-emerald-900 hover:border-emerald-500/40 hover:bg-emerald-50",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(16,185,129,0.16),transparent_38%),linear-gradient(180deg,rgba(255,255,255,0.94),rgba(255,255,255,0.42))]",
            dotClassName: "bg-emerald-500 ring-emerald-100",
          };
    case "table":
      return isDark
        ? {
            buttonClassName: "border-amber-400/28 bg-amber-400/[0.08] text-amber-100 hover:border-amber-300/45 hover:bg-amber-400/[0.12]",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(251,191,36,0.18),transparent_42%),linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02))]",
            dotClassName: "bg-amber-300 ring-amber-100/20",
          }
        : {
            buttonClassName: "border-amber-500/25 bg-amber-50/90 text-amber-900 hover:border-amber-500/40 hover:bg-amber-50",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(245,158,11,0.16),transparent_38%),linear-gradient(180deg,rgba(255,255,255,0.94),rgba(255,255,255,0.42))]",
            dotClassName: "bg-amber-500 ring-amber-100",
          };
    case "image":
      return isDark
        ? {
            buttonClassName: "border-sky-400/28 bg-sky-400/[0.08] text-sky-100 hover:border-sky-300/45 hover:bg-sky-400/[0.12]",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(56,189,248,0.18),transparent_42%),linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02))]",
            dotClassName: "bg-sky-300 ring-sky-100/20",
          }
        : {
            buttonClassName: "border-sky-500/25 bg-sky-50/90 text-sky-900 hover:border-sky-500/40 hover:bg-sky-50",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(14,165,233,0.16),transparent_38%),linear-gradient(180deg,rgba(255,255,255,0.94),rgba(255,255,255,0.42))]",
            dotClassName: "bg-sky-500 ring-sky-100",
          };
    case "pdf":
      return isDark
        ? {
            buttonClassName: "border-rose-400/28 bg-rose-400/[0.08] text-rose-100 hover:border-rose-300/45 hover:bg-rose-400/[0.12]",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(251,113,133,0.18),transparent_42%),linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02))]",
            dotClassName: "bg-rose-300 ring-rose-100/20",
          }
        : {
            buttonClassName: "border-rose-500/25 bg-rose-50/90 text-rose-900 hover:border-rose-500/40 hover:bg-rose-50",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(244,63,94,0.16),transparent_38%),linear-gradient(180deg,rgba(255,255,255,0.94),rgba(255,255,255,0.42))]",
            dotClassName: "bg-rose-500 ring-rose-100",
          };
    case "web_preview":
      return isDark
        ? {
            buttonClassName: "border-cyan-400/28 bg-cyan-400/[0.08] text-cyan-100 hover:border-cyan-300/45 hover:bg-cyan-400/[0.12]",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(34,211,238,0.18),transparent_42%),linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02))]",
            dotClassName: "bg-cyan-300 ring-cyan-100/20",
          }
        : {
            buttonClassName: "border-cyan-500/25 bg-cyan-50/90 text-cyan-900 hover:border-cyan-500/40 hover:bg-cyan-50",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(6,182,212,0.16),transparent_38%),linear-gradient(180deg,rgba(255,255,255,0.94),rgba(255,255,255,0.42))]",
            dotClassName: "bg-cyan-500 ring-cyan-100",
          };
    default:
      return isDark
        ? {
            buttonClassName: "border-slate-300/18 bg-slate-200/[0.07] text-slate-100 hover:border-slate-200/28 hover:bg-slate-200/[0.1]",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(226,232,240,0.14),transparent_42%),linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02))]",
            dotClassName: "bg-slate-200 ring-slate-50/10",
          }
        : {
            buttonClassName: "border-slate-400/20 bg-slate-50/90 text-slate-800 hover:border-slate-400/34 hover:bg-slate-50",
            overlayClassName: "bg-[radial-gradient(circle_at_top,rgba(148,163,184,0.14),transparent_38%),linear-gradient(180deg,rgba(255,255,255,0.94),rgba(255,255,255,0.42))]",
            dotClassName: "bg-slate-500 ring-slate-100",
          };
  }
}

export function PresentationRail({
  mode,
  presentation,
  isDark,
  readOnly,
  onOpenSlot,
  onPinSlot,
}: PresentationRailProps) {
  const { t, i18n } = useTranslation("chat");
  const [hoveredSlotId, setHoveredSlotId] = useState("");

  const normalizedPresentation = useMemo(() => ensurePresentation(presentation), [presentation]);
  const filledSlots = useMemo(() => getFilledSlots(normalizedPresentation), [normalizedPresentation]);
  const hasCards = filledSlots.length > 0;
  const highlightSlotId = String(normalizedPresentation.highlight_slot_id || "").trim();

  const previewSlot = useMemo(() => {
    if (!hoveredSlotId) return null;
    return normalizedPresentation.slots.find((slot) => slot.slot_id === hoveredSlotId) || null;
  }, [hoveredSlotId, normalizedPresentation]);

  if (mode === "panel") {
    const updatedAt = formatUpdatedAt(normalizedPresentation?.updated_at, i18n.language);
    return (
      <section
        className={classNames(
          "flex h-full min-h-0 flex-col",
          isDark ? "bg-slate-950/20" : "bg-white/40"
        )}
        aria-label={t("presentationSectionLabel", { defaultValue: "Presentation" })}
      >
        <div
          className={classNames(
            "flex items-center justify-between gap-3 px-4 py-3 border-b",
            isDark ? "border-white/5" : "border-black/5"
          )}
        >
          <div>
            <h2 className={classNames("text-sm font-semibold", isDark ? "text-slate-100" : "text-gray-900")}>
              {t("presentationTitle", { defaultValue: "Presentation" })}
            </h2>
            <p className={classNames("text-xs", isDark ? "text-slate-400" : "text-gray-600")}>
              {hasCards && updatedAt
                ? t("presentationUpdatedAt", {
                    value: updatedAt,
                    defaultValue: `Updated ${updatedAt}`,
                  })
                : t("presentationEmptyHelp", {
                    defaultValue: "Tap an empty slot to pin a URL or a local file.",
                  })}
            </p>
          </div>
          <div className={classNames("text-xs font-medium", isDark ? "text-slate-400" : "text-gray-500")}>
            {filledSlots.length}/{normalizedPresentation?.slots?.length || 4}
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-auto p-4">
          <div className="grid grid-cols-2 gap-3">
            {normalizedPresentation.slots.map((slot) => {
              const card = slot.card;
              const isHighlighted = slot.slot_id === highlightSlotId;
              return (
                <button
                  key={slot.slot_id}
                  type="button"
                  onClick={() => {
                    if (card) {
                      onOpenSlot(slot.slot_id);
                      return;
                    }
                    if (!readOnly) {
                      onPinSlot?.(slot.slot_id);
                    }
                  }}
                  className={classNames(
                    "rounded-3xl border p-4 text-left transition-all",
                    "min-h-[164px] shadow-sm hover:-translate-y-0.5",
                    isDark
                      ? "border-white/10 bg-slate-900/70 hover:border-cyan-400/40"
                      : "border-black/10 bg-white/85 hover:border-cyan-500/40",
                    !card && readOnly && (isDark ? "cursor-default opacity-80" : "cursor-default opacity-90"),
                    isHighlighted && (isDark ? "ring-2 ring-cyan-400/50" : "ring-2 ring-cyan-500/40")
                  )}
                  aria-label={t("presentationOpenSlot", {
                    index: slot.index,
                    title: card?.title || t("presentationSlotEmpty", { defaultValue: "Empty" }),
                    defaultValue: `Open presentation slot ${slot.index}: ${card?.title || "Empty"}`,
                  })}
                >
                  <div className="flex items-start justify-between gap-3">
                    <span
                      className={classNames(
                        "inline-flex h-8 min-w-[2rem] items-center justify-center rounded-full px-2 text-xs font-semibold",
                        isDark ? "bg-slate-800 text-slate-200" : "bg-gray-100 text-gray-700"
                      )}
                    >
                      {slot.index}
                    </span>
                    <span
                      className={classNames(
                        "rounded-full px-2 py-1 text-[11px] font-medium",
                        card
                          ? isDark ? "bg-cyan-500/10 text-cyan-200" : "bg-cyan-50 text-cyan-700"
                          : isDark ? "bg-slate-800 text-slate-300" : "bg-gray-100 text-gray-600"
                      )}
                    >
                      {card
                        ? getCardTypeLabel(card.card_type, t)
                        : t("presentationPinAction", { defaultValue: "Pin" })}
                    </span>
                  </div>
                  <div className={classNames("mt-4 text-sm font-semibold leading-5", isDark ? "text-slate-100" : "text-gray-900")}>
                    {card ? card.title : t("presentationSlotEmptyTitle", { defaultValue: "Empty slot" })}
                  </div>
                  <div className={classNames("mt-2 text-xs leading-5", isDark ? "text-slate-400" : "text-gray-600")}>
                    {card
                      ? getPreviewText(slot, t)
                      : readOnly
                        ? t("presentationEmptyReadOnlyHint", {
                            defaultValue: "Waiting for an agent or an authorized user to publish here.",
                          })
                        : t("presentationEmptyActionHint", {
                            defaultValue: "Tap to pin a URL or upload a local file.",
                          })}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </section>
    );
  }

  return (
    <aside
      className="absolute right-4 top-4 z-30"
      onMouseLeave={() => setHoveredSlotId("")}
      aria-label={t("presentationTitle", { defaultValue: "Presentation" })}
    >
      {previewSlot ? (
        <div className="pointer-events-none absolute right-full top-0 z-10 flex w-[292px] pr-4">
          <div
            className={classNames(
              "relative w-full overflow-hidden rounded-[28px] border px-5 py-5 shadow-[0_32px_80px_-36px_rgba(15,23,42,0.45)] backdrop-blur-2xl",
              isDark
                ? "border-white/12 bg-slate-950/72 text-slate-100 ring-1 ring-white/6"
                : "border-white/75 bg-white/72 text-gray-900 ring-1 ring-black/5"
            )}
          >
            <div
              className={classNames(
                "pointer-events-none absolute inset-0",
                isDark
                  ? "bg-[radial-gradient(circle_at_top_right,rgba(34,211,238,0.18),transparent_42%),linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02))]"
                  : "bg-[radial-gradient(circle_at_top_right,rgba(14,165,233,0.14),transparent_38%),linear-gradient(180deg,rgba(255,255,255,0.78),rgba(255,255,255,0.42))]"
              )}
            />
            <div className="relative">
              <div className="flex items-center justify-between gap-3">
                <span
                  className={classNames(
                    "inline-flex h-7 min-w-[1.75rem] items-center justify-center rounded-full px-2 text-[11px] font-semibold",
                    previewSlot.card
                      ? isDark
                        ? "bg-cyan-400/12 text-cyan-200"
                        : "bg-cyan-50 text-cyan-700"
                      : isDark
                        ? "bg-white/[0.06] text-slate-300"
                        : "bg-black/[0.04] text-gray-700"
                  )}
                >
                  {previewSlot.index}
                </span>
                {previewSlot.card ? (
                  <span
                    className={classNames(
                      "rounded-full px-2.5 py-1 text-[11px] font-medium tracking-[0.08em] uppercase",
                      isDark ? "bg-cyan-400/12 text-cyan-200" : "bg-cyan-50 text-cyan-700"
                    )}
                  >
                    {getCardTypeLabel(previewSlot.card.card_type, t)}
                  </span>
                ) : null}
              </div>
              <div className={classNames("mt-3 text-[15px] font-semibold leading-6", isDark ? "text-slate-50" : "text-gray-950")}>
                {previewSlot.card
                  ? previewSlot.card.title
                  : t("presentationSlotEmptyTitle", { defaultValue: "Empty slot" })}
              </div>
              <div className={classNames("mt-2 text-xs leading-5", isDark ? "text-slate-300" : "text-gray-600")}>
                {previewSlot.card
                  ? getPreviewText(previewSlot, t)
                  : readOnly
                    ? t("presentationEmptyReadOnlyHint", {
                        defaultValue: "Waiting for an agent or an authorized user to publish here.",
                      })
                    : t("presentationEmptyActionHint", {
                        defaultValue: "Tap to pin a URL or upload a local file.",
                      })}
              </div>
              {previewSlot.card ? (
                <div className={classNames("mt-4 flex items-center justify-between text-[11px]", isDark ? "text-slate-500" : "text-gray-500")}>
                  <span>
                    {t("presentationOpenSlot", {
                      index: previewSlot.index,
                      title: previewSlot.card.title,
                      defaultValue: `Open presentation slot ${previewSlot.index}: ${previewSlot.card.title}`,
                    })}
                  </span>
                  <span>{formatUpdatedAt(previewSlot.card.published_at, i18n.language)}</span>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

          <div
            className={classNames(
              "pointer-events-auto relative flex flex-col items-center gap-1.5 overflow-hidden rounded-[26px] border px-2 py-2 shadow-[0_24px_64px_-34px_rgba(15,23,42,0.42)] backdrop-blur-2xl",
              isDark
                ? "border-white/12 bg-slate-950/58 ring-1 ring-white/6"
                : "border-white/80 bg-white/60 ring-1 ring-black/5"
        )}
      >
        <div
          className={classNames(
            "pointer-events-none absolute inset-0",
            isDark
              ? "bg-[linear-gradient(180deg,rgba(255,255,255,0.08),rgba(255,255,255,0.02)),radial-gradient(circle_at_top,rgba(34,211,238,0.12),transparent_38%)]"
              : "bg-[linear-gradient(180deg,rgba(255,255,255,0.92),rgba(255,255,255,0.46)),radial-gradient(circle_at_top,rgba(14,165,233,0.12),transparent_36%)]"
          )}
        />
        <div className="relative z-10 flex flex-col items-center gap-1.5">
          {normalizedPresentation.slots.map((slot) => {
              const card = slot.card;
              const isHighlighted = slot.slot_id === highlightSlotId;
              const isActive = slot.slot_id === hoveredSlotId;
              const tone = card ? getRailSlotTone(card.card_type, isDark) : null;
              return (
                <button
                  key={slot.slot_id}
                  type="button"
                  onMouseEnter={() => {
                    setHoveredSlotId(slot.slot_id);
                  }}
                  onFocus={() => {
                    setHoveredSlotId(slot.slot_id);
                  }}
                  onBlur={() => setHoveredSlotId("")}
                  onClick={() => {
                    if (card) {
                      onOpenSlot(slot.slot_id);
                      return;
                    }
                    if (!readOnly) {
                      onPinSlot?.(slot.slot_id);
                    }
                  }}
                  className={classNames(
                    "group relative flex h-[54px] w-[54px] origin-top-right items-center justify-center overflow-hidden rounded-[18px] border text-center transition-all duration-200",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/50",
                    card || !readOnly ? "cursor-pointer" : "cursor-default",
                    card
                      ? tone?.buttonClassName
                      : isDark
                        ? "border-dashed border-white/10 bg-white/[0.025] text-slate-400 hover:border-white/14 hover:bg-white/[0.05]"
                        : "border-dashed border-black/10 bg-white/46 text-gray-500 hover:border-black/15 hover:bg-white/68",
                    !card && !readOnly && (isDark ? "hover:border-cyan-300/30 hover:bg-white/[0.05]" : "hover:border-cyan-500/25 hover:bg-white/60"),
                    card && isActive && "scale-[1.08] shadow-[0_18px_34px_-24px_rgba(15,23,42,0.8)]",
                    !card && isActive && "scale-[1.04]",
                    isHighlighted && (isDark ? "ring-1 ring-cyan-300/45" : "ring-1 ring-cyan-500/35")
                  )}
                  aria-label={
                    card
                      ? t("presentationOpenSlot", {
                          index: slot.index,
                          title: card.title,
                          defaultValue: `Open presentation slot ${slot.index}: ${card.title}`,
                        })
                      : t("presentationEmptySlot", {
                          index: slot.index,
                          defaultValue: `Presentation slot ${slot.index} is empty`,
                        })
                  }
                >
                  <div
                    className={classNames(
                      "pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-200",
                      card
                        ? tone?.overlayClassName
                        : "bg-transparent",
                      isActive && "opacity-100",
                      card && "opacity-100"
                    )}
                  />
                  {card ? (
                    <span
                      className={classNames(
                        "pointer-events-none absolute right-1.5 top-1.5 h-2.5 w-2.5 rounded-full ring-1",
                        tone?.dotClassName
                      )}
                    />
                  ) : null}
                  <span className="relative text-[14px] font-semibold tracking-[0.02em]">{slot.index}</span>
                </button>
              );
            })}
        </div>
      </div>
    </aside>
  );
}
