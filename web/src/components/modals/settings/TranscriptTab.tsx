// TranscriptTab configures terminal transcript visibility and viewing.
import { useTranslation } from "react-i18next";
import { Actor } from "../../../types";
import { inputClass, primaryButtonClass, cardClass, preClass } from "./types";

interface TranscriptTabProps {
  isDark: boolean;
  busy: boolean;
  groupId?: string;
  devActors: Actor[];
  // Policy settings
  terminalVisibility: "off" | "foreman" | "all";
  setTerminalVisibility: (v: "off" | "foreman" | "all") => void;
  terminalNotifyTail: boolean;
  setTerminalNotifyTail: (v: boolean) => void;
  terminalNotifyLines: number;
  setTerminalNotifyLines: (v: number) => void;
  onSaveTranscriptSettings: () => void;
  // Tail viewer
  tailActorId: string;
  setTailActorId: (v: string) => void;
  tailMaxChars: number;
  setTailMaxChars: (v: number) => void;
  tailStripAnsi: boolean;
  setTailStripAnsi: (v: boolean) => void;
  tailCompact: boolean;
  setTailCompact: (v: boolean) => void;
  tailText: string;
  tailHint: string;
  tailErr: string;
  tailBusy: boolean;
  tailCopyInfo: string;
  onLoadTail: () => void;
  onCopyTail: (lines: number) => void;
  onClearTail: () => void;
}

export function TranscriptTab({
  isDark: _isDark,
  busy,
  groupId,
  devActors,
  terminalVisibility,
  setTerminalVisibility,
  terminalNotifyTail,
  setTerminalNotifyTail,
  terminalNotifyLines,
  setTerminalNotifyLines,
  onSaveTranscriptSettings,
  tailActorId,
  setTailActorId,
  tailMaxChars,
  setTailMaxChars,
  tailStripAnsi,
  setTailStripAnsi,
  tailCompact,
  setTailCompact,
  tailText,
  tailHint,
  tailErr,
  tailBusy,
  tailCopyInfo,
  onLoadTail,
  onCopyTail,
  onClearTail,
}: TranscriptTabProps) {
  const { t } = useTranslation("settings");

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-medium text-[var(--color-text-secondary)]">{t("transcript.title")}</h3>
        <p className="text-xs mt-1 text-[var(--color-text-muted)]">
          {t("transcript.description")}
        </p>
      </div>

      {/* Policy */}
      <div className={cardClass()}>
        <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("transcript.policy")}</div>

        <div className="mt-3 space-y-3">
          <div>
            <label className="block text-xs mb-1 text-[var(--color-text-tertiary)]">{t("transcript.visibilityLabel")}</label>
            <select
              value={terminalVisibility}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "off" || v === "foreman" || v === "all") setTerminalVisibility(v);
              }}
              className={inputClass()}
            >
              <option value="off">{t("transcript.visibilityOff")}</option>
              <option value="foreman">{t("transcript.visibilityForeman")}</option>
              <option value="all">{t("transcript.visibilityAll")}</option>
            </select>
            <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              {t("transcript.visibilityTip")}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
              <input
                type="checkbox"
                checked={terminalNotifyTail}
                onChange={(e) => setTerminalNotifyTail(e.target.checked)}
                className="h-4 w-4"
              />
              {t("transcript.includeTail")}
            </label>
            <div>
              <label className="block text-xs mb-1 text-[var(--color-text-tertiary)]">{t("transcript.notificationLines")}</label>
              <input
                type="number"
                value={terminalNotifyLines}
                min={1}
                max={80}
                onChange={(e) => setTerminalNotifyLines(Number(e.target.value || 20))}
                disabled={!terminalNotifyTail}
                className={`${inputClass()} disabled:opacity-60`}
              />
            </div>
          </div>

          <button
            onClick={onSaveTranscriptSettings}
            disabled={busy}
            className={primaryButtonClass(busy)}
          >
            {busy ? t("common:saving") : t("transcript.saveTranscript")}
          </button>
        </div>
      </div>

      {/* Tail Viewer */}
      <div className={cardClass()}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("transcript.tailViewer")}</div>
            <div className="text-xs mt-0.5 text-[var(--color-text-muted)]">
              {t("transcript.tailViewerHint")}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={onLoadTail}
              disabled={!groupId || !tailActorId || tailBusy}
              className="glass-btn text-[var(--color-text-secondary)] px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors disabled:opacity-50"
            >
              {tailBusy ? t("common:loading") : t("transcript.refresh")}
            </button>
            <button
              onClick={() => onCopyTail(50)}
              disabled={!tailText.trim()}
              className="glass-btn text-[var(--color-text-secondary)] px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors disabled:opacity-50"
            >
              {t("transcript.copyLast50")}
            </button>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-2">
          <label className="block text-xs text-[var(--color-text-tertiary)]">{t("transcript.actor")}</label>
          <select
            value={tailActorId}
            onChange={(e) => setTailActorId(e.target.value)}
            className={inputClass()}
          >
            {devActors.map((a) => (
              <option key={a.id} value={a.id}>
                {a.id}{a.role ? ` (${a.role})` : ""}
              </option>
            ))}
            {!devActors.length && <option value="">{t("transcript.noActors")}</option>}
          </select>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs mb-1 text-[var(--color-text-tertiary)]">{t("transcript.maxChars")}</label>
              <input
                type="number"
                value={tailMaxChars}
                min={1000}
                max={200000}
                onChange={(e) => setTailMaxChars(Number(e.target.value || 8000))}
                className={inputClass()}
              />
            </div>
            <div className="flex items-end justify-end">
              <button
                onClick={onClearTail}
                disabled={!groupId || !tailActorId || tailBusy}
                className="px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30 hover:bg-rose-500/25 disabled:opacity-50"
              >
                {t("transcript.clearTruncate")}
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
              <input
                type="checkbox"
                checked={tailStripAnsi}
                onChange={(e) => setTailStripAnsi(e.target.checked)}
                className="h-4 w-4"
              />
              {t("transcript.stripAnsi")}
            </label>
            <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
              <input
                type="checkbox"
                checked={tailCompact}
                disabled={!tailStripAnsi}
                onChange={(e) => setTailCompact(e.target.checked)}
                className="h-4 w-4"
              />
              {t("transcript.compactFrames")}
            </label>
          </div>
        </div>

        {!!tailCopyInfo && (
          <div className="mt-2 text-xs text-emerald-600 dark:text-emerald-400">{tailCopyInfo}</div>
        )}
        {tailErr && (
          <div className="mt-2 text-xs text-rose-600 dark:text-rose-400">{tailErr}</div>
        )}
        {tailHint && !tailErr && (
          <div className="mt-2 text-xs text-[var(--color-text-muted)]">{tailHint}</div>
        )}

        <pre className={`${preClass()} max-h-[300px] overflow-y-auto`}>
          <code>{tailText || "\u2014"}</code>
        </pre>
      </div>
    </div>
  );
}
