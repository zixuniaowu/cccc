import { useEffect, useRef, useState } from "react";
import { useTranslation, Trans } from "react-i18next";
import { apiJson } from "../../../services/api";
import type { GroupSettings } from "../../../types";
import { cardClass, inputClass, labelClass, primaryButtonClass, preClass } from "./types";

type PromptKind = "preamble" | "help";

type PromptInfo = {
  kind: PromptKind;
  source: "home" | "builtin";
  filename: string;
  path?: string | null;
  content: string;
};

type PromptsResponse = {
  preamble?: PromptInfo | null;
  help?: PromptInfo | null;
};

function isPromptInfo(value: unknown): value is PromptInfo {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return typeof record.kind === "string"
    && typeof record.source === "string"
    && typeof record.filename === "string"
    && typeof record.content === "string";
}

function fallbackPrompt(kind: PromptKind): PromptInfo {
  return {
    kind,
    source: "builtin",
    filename: kind === "preamble" ? "CCCC_PREAMBLE.md" : "CCCC_HELP.md",
    path: null,
    content: "",
  };
}

export function GuidanceTab({ isDark: _isDark, groupId, settings, onUpdateSettings, scopeRootUrl, scopeResolved }: {
  isDark: boolean;
  groupId?: string;
  settings?: GroupSettings | null;
  onUpdateSettings?: (s: Partial<GroupSettings>) => Promise<void>;
  scopeRootUrl?: string;
  scopeResolved?: boolean;
}) {
  const { t } = useTranslation("settings");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [prompts, setPrompts] = useState<Record<PromptKind, PromptInfo> | null>(null);
  const [expandedKind, setExpandedKind] = useState<PromptKind | null>(null);
  const loadTokenRef = useRef(0);

  // Features
  const [panoramaEnabled, setPanoramaEnabled] = useState(false);
  useEffect(() => {
    if (settings) setPanoramaEnabled(Boolean(settings.panorama_enabled));
  }, [settings]);

  const handleTogglePanorama = async (enabled: boolean) => {
    setPanoramaEnabled(enabled);
    if (onUpdateSettings) await onUpdateSettings({ panorama_enabled: enabled });
  };

  const load = async () => {
    if (!groupId) return;
    const token = loadTokenRef.current + 1;
    loadTokenRef.current = token;
    setBusy(true);
    setErr("");
    try {
      const resp = await apiJson<PromptsResponse>(`/api/v1/groups/${encodeURIComponent(groupId)}/prompts`);
      if (loadTokenRef.current !== token) return;
      if (!resp.ok) {
        setErr(resp.error?.message || t("guidance.failedToLoad"));
        setPrompts(null);
        return;
      }
      const p = isPromptInfo(resp.result?.preamble) ? resp.result.preamble : prompts?.preamble || fallbackPrompt("preamble");
      const h = isPromptInfo(resp.result?.help) ? resp.result.help : prompts?.help || fallbackPrompt("help");
      setPrompts({ preamble: p, help: h });
    } catch {
      if (loadTokenRef.current !== token) return;
      setErr(t("guidance.failedToLoad"));
      setPrompts(null);
    } finally {
      if (loadTokenRef.current === token) setBusy(false);
    }
  };

  useEffect(() => {
    if (groupId) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Load when groupId changes.
  }, [groupId]);

  const save = async (kind: PromptKind) => {
    if (!groupId || !prompts) return;
    setBusy(true);
    setErr("");
    try {
      const body = { content: prompts[kind].content, by: "user" };
      const resp = await apiJson<PromptInfo>(`/api/v1/groups/${encodeURIComponent(groupId)}/prompts/${kind}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("guidance.failedToSave", { kind }));
        return;
      }
      await load();
    } catch {
      setErr(t("guidance.failedToSave", { kind }));
    } finally {
      setBusy(false);
    }
  };

  const reset = async (kind: PromptKind) => {
    if (!groupId) return;
    const filename = prompts?.[kind]?.filename || kind;
    const ok = window.confirm(t("automation.resetGuidanceConfirm", { kind, filename }));
    if (!ok) return;

    setBusy(true);
    setErr("");
    try {
      const resp = await apiJson<PromptInfo>(
        `/api/v1/groups/${encodeURIComponent(groupId)}/prompts/${kind}?confirm=${encodeURIComponent(kind)}`,
        { method: "DELETE" }
      );
      if (!resp.ok) {
        setErr(resp.error?.message || t("guidance.failedToReset", { kind }));
        return;
      }
      await load();
    } catch {
      setErr(t("guidance.failedToReset", { kind }));
    } finally {
      setBusy(false);
    }
  };

  const setContent = (kind: PromptKind, content: string) => {
    if (!prompts) return;
    setPrompts({ ...prompts, [kind]: { ...prompts[kind], content } });
  };

  if (!groupId) {
    return (
      <div className={cardClass()}>
        <div className="text-sm text-[var(--color-text-secondary)]">{t("guidance.openFromGroup")}</div>
      </div>
    );
  }

  const one = (kind: PromptKind, title: string, hint: string) => {
    const p = prompts?.[kind];
    const source = p?.source || "builtin";
    const badge =
      source === "home"
        ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30"
        : "bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] border border-[var(--glass-border-subtle)]";

    return (
      <div className={cardClass()}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</div>
            <div className="text-[11px] text-[var(--color-text-muted)]">{hint}</div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              className="glass-btn text-[var(--color-text-secondary)] px-2 py-1 rounded-md text-[11px] transition-colors"
              onClick={() => setExpandedKind(kind)}
              disabled={busy}
              title={t("guidance.expandTitle")}
            >
              {t("guidance.expand")}
            </button>
            <div className={`px-2 py-1 rounded-md text-[11px] ${badge}`}>{source === "home" ? t("guidance.overrideBadge") : t("guidance.builtinBadge")}</div>
          </div>
        </div>

        {p?.path && (
          <div className={preClass()}>
            <span className="font-mono">{p.path}</span>
          </div>
        )}

        <div className="mt-3">
          <label className={labelClass()}>{t("guidance.markdown")}</label>
          <textarea
            className={`${inputClass()} font-mono text-[12px]`}
            style={{ minHeight: 220 }}
            value={p?.content || ""}
            onChange={(e) => setContent(kind, e.target.value)}
            spellCheck={false}
          />
        </div>

        <div className="mt-3 flex items-center gap-2">
          <button className={primaryButtonClass(busy)} onClick={() => save(kind)} disabled={busy}>
            {t("common:save")}
          </button>
          <button
            className="glass-btn text-[var(--color-text-secondary)] px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium disabled:opacity-50"
            onClick={() => reset(kind)}
            disabled={busy || source !== "home"}
            title={source === "home" ? t("guidance.resetHint") : t("guidance.noOverride")}
          >
            {t("common:reset")}
          </button>
          <button
            className="glass-btn text-[var(--color-text-secondary)] ml-auto px-3 py-2 text-sm rounded-lg min-h-[44px] transition-colors disabled:opacity-50"
            onClick={load}
            disabled={busy}
            title={t("guidance.discardChanges")}
          >
            {t("guidance.discardChanges")}
          </button>
        </div>
      </div>
    );
  };

  const expanded = expandedKind ? prompts?.[expandedKind] : null;

  return (
    <div className="space-y-4">
      {err && <div className="text-sm text-rose-600 dark:text-rose-400">{err}</div>}

      {/* Features */}
      <div className={cardClass()}>
        <div className="text-sm font-semibold mb-3 text-[var(--color-text-primary)]">
          {t("guidance.featuresTitle", "Features")}
        </div>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={panoramaEnabled}
            onChange={(e) => handleTogglePanorama(e.target.checked)}
            className="w-4 h-4 rounded accent-blue-500"
          />
          <div>
            <div className="text-sm text-[var(--color-text-primary)]">
              Panorama 3D
            </div>
            <div className="text-[11px] text-[var(--color-text-muted)]">
              {t("guidance.panoramaHint", "Enable to show the 3D panorama tab")}
            </div>
          </div>
        </label>
      </div>

      <div className="text-[11px] text-[var(--color-text-muted)]">
        <Trans i18nKey="guidance.overridesHint" ns="settings" components={[<span className="font-mono" />]} />
      </div>
      {scopeResolved && !scopeRootUrl ? (
        <div className="text-[11px] text-amber-700 dark:text-amber-300 rounded-lg border border-amber-200 dark:border-amber-500/20 bg-amber-50/80 dark:bg-amber-500/10 px-3 py-2">
          {t("guidance.noScopeWarning", "No scope attached to this group yet. Guidance overrides still work because they are stored under CCCC_HOME, but repo-linked features may stay unavailable until a scope is attached.")}
        </div>
      ) : null}
      {one("preamble", t("guidance.preambleTitle"), t("guidance.preambleHint"))}
      {one("help", t("guidance.helpTitle"), t("guidance.helpHint"))}

      {expandedKind && expanded ? (
        <div
          className="fixed inset-0 z-[1000]"
          role="dialog"
          aria-modal="true"
          onPointerDown={(e) => {
            if (e.target === e.currentTarget) setExpandedKind(null);
          }}
        >
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="absolute inset-0 sm:inset-6 md:inset-10 rounded-none sm:rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] shadow-2xl flex flex-col overflow-hidden"
          >
            <div className="px-4 py-3 border-b border-[var(--glass-border-subtle)] flex items-start gap-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-[var(--color-text-primary)]">
                  {t("guidance.editKind", { kind: expandedKind })}
                </div>
                {expanded.path ? (
                  <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)] break-all font-mono">
                    {expanded.path}
                  </div>
                ) : null}
              </div>

              <div className="ml-auto flex items-center gap-2">
                <button
                  className={primaryButtonClass(busy)}
                  onClick={() => save(expandedKind)}
                  disabled={busy}
                  title={t("guidance.saveOverrideTitle")}
                >
                  {t("common:save")}
                </button>
                <button
                  type="button"
                  className="glass-btn text-[var(--color-text-secondary)] px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium disabled:opacity-50"
                  onClick={() => reset(expandedKind)}
                  disabled={busy || expanded.source !== "home"}
                  title={expanded.source === "home" ? t("guidance.resetHint") : t("guidance.noOverride")}
                >
                  {t("common:reset")}
                </button>
                <button
                  type="button"
                  className="glass-btn text-[var(--color-text-secondary)] px-3 py-2 text-sm rounded-lg min-h-[44px] transition-colors"
                  onClick={() => setExpandedKind(null)}
                >
                  {t("common:close")}
                </button>
              </div>
            </div>

            <div className="p-4 flex-1 overflow-hidden">
              <textarea
                className={`${inputClass()} font-mono text-[12px] w-full h-full resize-none`}
                value={expanded.content || ""}
                onChange={(e) => setContent(expandedKind, e.target.value)}
                spellCheck={false}
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
