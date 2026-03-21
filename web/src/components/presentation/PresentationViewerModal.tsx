import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { MarkdownRenderer } from "../MarkdownRenderer";
import { ModalFrame } from "../modals/ModalFrame";
import { useModalA11y } from "../../hooks/useModalA11y";
import type { GroupPresentation, PresentationSlot } from "../../types";
import { getPresentationAssetUrl } from "../../services/api";
import { classNames } from "../../utils/classNames";
import { findPresentationSlot } from "../../utils/presentation";
import { PresentationWebPreviewPanel } from "./PresentationWebPreviewPanel";

type PresentationViewerModalProps = {
  isOpen: boolean;
  isDark: boolean;
  readOnly?: boolean;
  groupId: string;
  slotId: string;
  presentation: GroupPresentation | null;
  onReplaceSlot?: (slotId: string) => void;
  onClearSlot?: (slotId: string) => void;
  onClose: () => void;
};

function getReferenceHref(groupId: string, slot: PresentationSlot | null, cacheBust?: string | number): string {
  const card = slot?.card;
  if (!card) return "";
  const url = String(card.content.url || "").trim();
  if (url) return url;
  return getPresentationAssetUrl(groupId, slot.slot_id, cacheBust);
}

function appendQueryParam(url: string, key: string, value: string): string {
  const base = String(url || "").trim();
  if (!base) return "";
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
}

async function copyText(value: string): Promise<boolean> {
  const text = String(value || "");
  if (!text) return false;
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to textarea copy.
  }
  try {
    if (typeof document === "undefined") return false;
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return !!ok;
  } catch {
    return false;
  }
}

function formatTimestamp(value: string | undefined, locale: string): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleString(locale, {
    year: "numeric",
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

function PresentationWindowExpandIcon({ expanded }: { expanded: boolean }) {
  if (expanded) {
    return (
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" className="h-4 w-4">
        <path d="M7 3.75H4.75v2.5M13 3.75h2.25v2.5M7 16.25H4.75v-2.5M13 16.25h2.25v-2.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M8 8l-3.25-3.25M12 8l3.25-3.25M8 12l-3.25 3.25M12 12l3.25 3.25" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" className="h-4 w-4">
      <path d="M7 3.75H4.75v2.5M13 3.75h2.25v2.5M7 16.25H4.75v-2.5M13 16.25h2.25v-2.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8 4.75H4.75V8M12 4.75h3.25V8M8 15.25H4.75V12M12 15.25h3.25V12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function PresentationViewerModal({
  isOpen,
  isDark,
  readOnly,
  groupId,
  slotId,
  presentation,
  onReplaceSlot,
  onClearSlot,
  onClose,
}: PresentationViewerModalProps) {
  const { t, i18n } = useTranslation("chat");
  const { modalRef } = useModalA11y(isOpen, onClose);
  const [refreshTick, setRefreshTick] = useState(0);
  const [isExpanded, setIsExpanded] = useState(false);
  const [linkedMarkdown, setLinkedMarkdown] = useState("");
  const [linkedMarkdownError, setLinkedMarkdownError] = useState("");
  const [copiedReference, setCopiedReference] = useState(false);
  const copyResetTimerRef = useRef<number | null>(null);

  const slot = useMemo(() => findPresentationSlot(presentation, slotId), [presentation, slotId]);
  const card = slot?.card || null;
  const isWorkspaceLinked = !!card && card.content.mode === "workspace_link" && !!String(card.content.workspace_rel_path || "").trim();
  const cacheBust = isWorkspaceLinked ? `${card?.published_at || "linked"}:${refreshTick}` : undefined;
  const href = useMemo(() => getReferenceHref(groupId, slot, cacheBust), [cacheBust, groupId, slot]);
  const publishedAt = formatTimestamp(card?.published_at, i18n.language);
  const useSandboxedPreview = !!card && card.card_type === "web_preview" && !String(card.content.url || "").trim();
  const cardType = String(card?.card_type || "").trim();
  const cardMode = String(card?.content.mode || "inline").trim();
  const allowLiveBrowser = !!card && card.card_type === "web_preview" && !!String(card.content.url || "").trim() && !readOnly;
  const canRefresh = !!card && (isWorkspaceLinked || card.card_type === "web_preview");
  const copyReferenceValue = String(card?.content.url || card?.content.workspace_rel_path || href || "").trim();
  const downloadHref = useMemo(() => {
    if (!card || card.card_type !== "file" || !href) return "";
    return appendQueryParam(href, "download", "1");
  }, [card, href]);
  const viewerPanelClassName = isExpanded
    ? "h-full w-full sm:h-[96vh] sm:max-w-[96vw]"
    : "h-full w-full sm:h-[90vh] sm:max-w-6xl";
  const immersiveViewportClassName = isExpanded
    ? "min-h-[calc(96vh-11rem)]"
    : "min-h-[72vh]";
  const imageViewportClassName = isExpanded
    ? "max-h-[calc(96vh-12rem)]"
    : "max-h-[70vh]";
  const fullScreenLabel = isExpanded
    ? t("presentationExitFullScreenAction", { defaultValue: "Exit full screen" })
    : t("presentationFullScreenAction", { defaultValue: "Full screen" });
  const headerActions = card ? (
    <button
      type="button"
      onClick={() => setIsExpanded((value) => !value)}
      className={classNames(
        "hidden sm:inline-flex min-h-[40px] min-w-[40px] items-center justify-center rounded-lg transition-colors glass-btn",
        isDark ? "text-slate-300 hover:text-slate-100" : "text-gray-600 hover:text-gray-900"
      )}
      aria-label={fullScreenLabel}
      title={fullScreenLabel}
    >
      <PresentationWindowExpandIcon expanded={isExpanded} />
    </button>
  ) : null;

  useEffect(() => {
    if (isOpen) return;
    setIsExpanded(false);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || !isWorkspaceLinked) return;
    const timer = window.setInterval(() => {
      setRefreshTick((value) => value + 1);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [isOpen, isWorkspaceLinked, slotId, card?.published_at]);

  useEffect(() => {
    if (!isOpen || cardType !== "markdown") return;
    if (cardMode === "inline") return;
    if (!href) {
      setLinkedMarkdown("");
      setLinkedMarkdownError("");
      return;
    }

    const controller = new AbortController();
    let active = true;

    const run = async () => {
      try {
        const resp = await fetch(href, { cache: "no-store", signal: controller.signal });
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }
        const text = await resp.text();
        if (!active) return;
        setLinkedMarkdown(text);
        setLinkedMarkdownError("");
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        setLinkedMarkdown("");
        setLinkedMarkdownError(error instanceof Error ? error.message : String(error));
      }
    };

    void run();
    return () => {
      active = false;
      controller.abort();
    };
  }, [cardMode, cardType, href, isOpen]);

  useEffect(() => {
    return () => {
      if (copyResetTimerRef.current !== null) {
        window.clearTimeout(copyResetTimerRef.current);
      }
    };
  }, []);

  const handleCopyReference = async () => {
    if (!copyReferenceValue) return;
    const ok = await copyText(copyReferenceValue);
    if (!ok) return;
    setCopiedReference(true);
    if (copyResetTimerRef.current !== null) {
      window.clearTimeout(copyResetTimerRef.current);
    }
    copyResetTimerRef.current = window.setTimeout(() => {
      copyResetTimerRef.current = null;
      setCopiedReference(false);
    }, 1600);
  };

  if (!isOpen) return null;

  return (
    <ModalFrame
      isDark={isDark}
      onClose={onClose}
      titleId="presentation-viewer-title"
      title={card?.title || t("presentationTitle", { defaultValue: "Presentation" })}
      closeAriaLabel={t("presentationCloseViewer", { defaultValue: "Close presentation viewer" })}
      panelClassName={viewerPanelClassName}
      headerActions={headerActions}
      modalRef={modalRef}
    >
      <div className="flex min-h-0 flex-1 flex-col">
        <div
          className={classNames(
            "flex flex-wrap items-center gap-2 border-b px-5 py-3 text-xs",
            isDark ? "border-white/10 text-slate-400" : "border-black/10 text-gray-600"
          )}
        >
          {card ? (
            <>
              <span className={classNames("rounded-full px-2 py-1 font-medium", isDark ? "bg-cyan-500/10 text-cyan-200" : "bg-cyan-50 text-cyan-700")}>
                {getCardTypeLabel(card.card_type, t)}
              </span>
              {isWorkspaceLinked ? (
                <span className={classNames("rounded-full px-2 py-1 font-medium", isDark ? "bg-emerald-500/10 text-emerald-200" : "bg-emerald-50 text-emerald-700")}>
                  {t("presentationWorkspaceLiveBadge", { defaultValue: "Live workspace link" })}
                </span>
              ) : null}
              {card.source_label ? <span>{card.source_label}</span> : null}
              {publishedAt ? <span>{publishedAt}</span> : null}
              <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                {canRefresh ? (
                  <button
                    type="button"
                    onClick={() => setRefreshTick((value) => value + 1)}
                    className={classNames(
                      "rounded-full px-3 py-1 font-medium transition-colors",
                      isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-gray-100 text-gray-800 hover:bg-gray-200"
                    )}
                  >
                    {t("presentationRefreshAction", { defaultValue: "Refresh" })}
                  </button>
                ) : null}
                {copyReferenceValue ? (
                  <button
                    type="button"
                    onClick={() => {
                      void handleCopyReference();
                    }}
                    className={classNames(
                      "rounded-full px-3 py-1 font-medium transition-colors",
                      isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-gray-100 text-gray-800 hover:bg-gray-200"
                    )}
                  >
                    {copiedReference
                      ? t("presentationCopyReferenceCopied", { defaultValue: "Copied" })
                      : t("presentationCopyReferenceAction", { defaultValue: "Copy URL/path" })}
                  </button>
                ) : null}
                {!readOnly ? (
                  <button
                    type="button"
                    onClick={() => slot && onReplaceSlot?.(slot.slot_id)}
                    className={classNames(
                      "rounded-full px-3 py-1 font-medium transition-colors",
                      isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-gray-100 text-gray-800 hover:bg-gray-200"
                    )}
                  >
                    {t("presentationReplaceAction", { defaultValue: "Edit" })}
                  </button>
                ) : null}
                {!readOnly ? (
                  <button
                    type="button"
                    onClick={() => slot && onClearSlot?.(slot.slot_id)}
                    className={classNames(
                      "rounded-full px-3 py-1 font-medium transition-colors",
                      isDark ? "bg-rose-500/15 text-rose-200 hover:bg-rose-500/25" : "bg-rose-50 text-rose-700 hover:bg-rose-100"
                    )}
                  >
                    {t("presentationClearAction", { defaultValue: "Clear" })}
                  </button>
                ) : null}
              </div>
            </>
          ) : (
            <span>{t("presentationMissingCard", { defaultValue: "This presentation slot is empty." })}</span>
          )}
        </div>

        <div className="flex-1 min-h-0 overflow-auto px-5 py-5">
          {!card ? (
            <div className={classNames("flex h-full min-h-[320px] items-center justify-center rounded-3xl border border-dashed text-sm", isDark ? "border-white/10 text-slate-500" : "border-black/10 text-gray-500")}>
              {t("presentationMissingCard", { defaultValue: "This presentation slot is empty." })}
            </div>
          ) : card.card_type === "markdown" ? (
            <div className={classNames("rounded-3xl border p-5", isDark ? "border-white/10 bg-slate-950/60" : "border-black/10 bg-white/90")}>
              {linkedMarkdownError ? (
                <div className={classNames("text-sm", isDark ? "text-rose-300" : "text-rose-600")}>{linkedMarkdownError}</div>
              ) : (
                <MarkdownRenderer
                  content={String(card.content.mode === "inline" ? card.content.markdown || "" : linkedMarkdown || "")}
                  isDark={isDark}
                  className="break-words [overflow-wrap:anywhere]"
                />
              )}
            </div>
          ) : card.card_type === "table" ? (
            <div className={classNames("overflow-hidden rounded-3xl border", isDark ? "border-white/10 bg-slate-950/60" : "border-black/10 bg-white/95")}>
              <div className="overflow-auto">
                <table className="min-w-full border-collapse text-sm">
                  <thead className={isDark ? "bg-slate-900/80 text-slate-200" : "bg-gray-50 text-gray-800"}>
                    <tr>
                      {(card.content.table?.columns || []).map((column) => (
                        <th key={column} className="border-b border-inherit px-4 py-3 text-left font-semibold">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className={isDark ? "text-slate-300" : "text-gray-700"}>
                    {(card.content.table?.rows || []).map((row, rowIndex) => (
                      <tr key={`row-${rowIndex}`} className={rowIndex % 2 === 0 ? (isDark ? "bg-slate-950/40" : "bg-white") : (isDark ? "bg-slate-900/30" : "bg-gray-50/80")}>
                        {row.map((cell, cellIndex) => (
                          <td key={`cell-${rowIndex}:${cellIndex}`} className="border-b border-[var(--glass-border-subtle)] px-4 py-3 align-top">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : card.card_type === "image" ? (
            <div className="flex min-h-[360px] items-center justify-center">
              <img
                src={href}
                alt={card.title}
                className={classNames(
                  imageViewportClassName,
                  "max-w-full rounded-3xl border border-[var(--glass-border-subtle)] object-contain shadow-xl"
                )}
              />
            </div>
          ) : card.card_type === "pdf" ? (
            <iframe
              title={card.title}
              src={href}
              className={classNames(
                immersiveViewportClassName,
                "w-full rounded-3xl border border-[var(--glass-border-subtle)] bg-white"
              )}
            />
          ) : card.card_type === "web_preview" ? (
            <PresentationWebPreviewPanel
              key={`${slot?.slot_id || ""}:${card.published_at}:${href}`}
              groupId={groupId}
              title={card.title}
              href={href}
              isDark={isDark}
              useSandboxedPreview={useSandboxedPreview}
              allowLiveBrowser={allowLiveBrowser}
              refreshNonce={refreshTick}
              viewportClassName={immersiveViewportClassName}
            />
          ) : (
            <div className={classNames("rounded-3xl border p-6", isDark ? "border-white/10 bg-slate-950/60" : "border-black/10 bg-white/90")}>
              <div className={classNames("text-base font-semibold", isDark ? "text-slate-100" : "text-gray-900")}>
                {card.title}
              </div>
              <div className={classNames("mt-2 text-sm", isDark ? "text-slate-400" : "text-gray-600")}>
                {card.summary || card.source_label || t("presentationFileReady", { defaultValue: "This file cannot be previewed in Presentation yet." })}
              </div>
              {downloadHref ? (
                <div className="mt-5">
                  <a
                    href={downloadHref}
                    download=""
                    className={classNames(
                      "inline-flex items-center rounded-full px-4 py-2 text-sm font-medium transition-colors",
                      isDark ? "bg-slate-800 text-slate-100 hover:bg-slate-700" : "bg-gray-100 text-gray-900 hover:bg-gray-200"
                    )}
                  >
                    {t("presentationDownloadFile", { defaultValue: "Download file" })}
                  </a>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </ModalFrame>
  );
}
