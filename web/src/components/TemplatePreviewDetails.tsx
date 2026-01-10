import { useState } from "react";

import { cardClass } from "./modals/settings/types";

type TemplatePromptKind = "preamble" | "help" | "standup";

type TemplatePromptPreview = {
  source?: "builtin" | "repo";
  chars?: number;
  preview?: string;
};

type TemplateActor = {
  id?: string;
  title?: string;
  runtime?: string;
  runner?: string;
  command?: unknown;
  submit?: string;
  enabled?: boolean;
};

type TemplateSettings = Record<string, unknown>;

type TemplatePayload = {
  v?: number;
  cccc_version?: string;
  title?: string;
  topic?: string;
  exported_at?: string;
  actors?: TemplateActor[];
  settings?: TemplateSettings;
  prompts?: Record<string, TemplatePromptPreview>;
};

type TemplateDiff = {
  actors_add?: string[];
  actors_update?: string[];
  actors_remove?: string[];
  settings_changed?: Record<string, { from: unknown; to: unknown }>;
  prompts_changed?: Record<
    string,
    {
      changed: boolean;
      current_chars?: number;
      new_chars?: number;
      current_source?: "builtin" | "repo";
      new_source?: "builtin" | "repo";
    }
  >;
};

export interface TemplatePreviewDetailsProps {
  isDark: boolean;
  template: TemplatePayload;
  diff?: TemplateDiff | null;
  scopeRoot?: string;
  promptOverwriteFiles?: string[];
  wrap?: boolean;
  detailsOpenByDefault?: boolean;
}

function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((x) => String(x || "").trim()).filter((s) => s);
}

function formatCommand(cmd: unknown): string {
  if (Array.isArray(cmd)) {
    return cmd.map((x) => String(x || "").trim()).filter((s) => s).join(" ");
  }
  return String(cmd || "").trim();
}

export function TemplatePreviewDetails({
  isDark,
  template,
  diff,
  scopeRoot,
  promptOverwriteFiles,
  wrap = true,
  detailsOpenByDefault = false,
}: TemplatePreviewDetailsProps) {
  const [detailsOpen, setDetailsOpen] = useState(Boolean(detailsOpenByDefault));

  const actors = Array.isArray(template.actors) ? template.actors : [];
  const settings = (template.settings && typeof template.settings === "object" ? template.settings : {}) as TemplateSettings;
  const prompts = (template.prompts && typeof template.prompts === "object" ? template.prompts : {}) as Record<
    string,
    TemplatePromptPreview
  >;

  const addIds = asStringArray(diff?.actors_add);
  const updateIds = asStringArray(diff?.actors_update);
  const removeIds = asStringArray(diff?.actors_remove);

  const overwrite = Array.isArray(promptOverwriteFiles) ? promptOverwriteFiles : [];

  const promptMeta = (kind: TemplatePromptKind) => {
    const p = prompts[kind] || {};
    const ch = diff?.prompts_changed?.[kind];
    const changed = typeof ch?.changed === "boolean" ? ch.changed : undefined;
    const currentChars = ch?.current_chars;
    const newChars = ch?.new_chars;
    const currentSource = ch?.current_source;
    const newSource = ch?.new_source;
    return { p, changed, currentChars, newChars, currentSource, newSource };
  };

  const formatPromptLabel = (kind: TemplatePromptKind) => {
    if (kind === "help") return "CCCC_HELP.md";
    if (kind === "standup") return "CCCC_STANDUP.md";
    return "CCCC_PREAMBLE.md";
  };

  const formatSource = (s: unknown): string => {
    if (s === "repo") return "repo";
    if (s === "builtin") return "builtin";
    return "";
  };

  const settingsChangedKeys = diff?.settings_changed ? Object.keys(diff.settings_changed) : [];
  const promptRepoCount = (["preamble", "help", "standup"] as const).filter((k) => prompts[k]?.source === "repo").length;

  const formatSettingLine = (key: string) => {
    if (diff?.settings_changed && diff.settings_changed[key]) {
      const row = diff.settings_changed[key];
      return (
        <div key={key} className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
          <span className="font-mono">{key}</span>: <span className="font-mono">{String(row.from)}</span> →{" "}
          <span className="font-mono">{String(row.to)}</span>
        </div>
      );
    }
    return (
      <div key={key} className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
        <span className="font-mono">{key}</span>: <span className="font-mono">{String(settings[key])}</span>
      </div>
    );
  };

  const stableSettingsKeys: string[] = [
    "nudge_after_seconds",
    "actor_idle_timeout_seconds",
    "keepalive_delay_seconds",
    "keepalive_max_per_actor",
    "silence_timeout_seconds",
    "help_nudge_interval_seconds",
    "help_nudge_min_messages",
    "min_interval_seconds",
    "standup_interval_seconds",
    "terminal_transcript_visibility",
    "terminal_transcript_notify_tail",
    "terminal_transcript_notify_lines",
  ].filter((k) => k in settings || (diff?.settings_changed && k in diff.settings_changed));

  const body = (
    <>
      <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>Template preview</div>
      <div className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-600"}`}>
        Template title/topic are informational only (not applied automatically).
      </div>

      <div className="mt-3 space-y-1">
        <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
          Template:{" "}
          <span className="font-mono">
            v{String(template.v || "")} {String(template.cccc_version || "")}
          </span>
          {template.title ? (
            <span className="ml-2">
              • <span className="font-mono">{String(template.title)}</span>
            </span>
          ) : null}
        </div>
        {scopeRoot ? (
          <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
            Repo scope root: <span className="font-mono">{scopeRoot}</span>
          </div>
        ) : null}
        {overwrite.length > 0 ? (
          <div
            className={`mt-2 rounded-lg border px-3 py-2 text-xs ${
              isDark
                ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                : "border-amber-200 bg-amber-50 text-amber-800"
            }`}
          >
            Will modify existing repo prompt files: <span className="font-mono">{overwrite.join(", ")}</span>
          </div>
        ) : null}

        {diff ? (
          <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
            Actors: +{addIds.length} / ~{updateIds.length} / -{removeIds.length} • Settings changes:{" "}
            {settingsChangedKeys.length} • Prompts changes:{" "}
            {diff.prompts_changed
              ? Object.keys(diff.prompts_changed).filter((k) => diff.prompts_changed?.[k]?.changed).length
              : 0}
          </div>
        ) : (
          <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
            Actors: {actors.length} • Settings: {stableSettingsKeys.length} keys • Prompts:{" "}
            {promptRepoCount > 0 ? `${promptRepoCount} file(s)` : "builtin"}
          </div>
        )}
      </div>

      <details
        className={`mt-3 rounded-lg border px-3 py-2 ${
          isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"
        }`}
        open={detailsOpen}
        onToggle={(e) => setDetailsOpen(e.currentTarget.open)}
      >
        <summary className={`cursor-pointer text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>
          Preview details
        </summary>

        <div className="mt-2 space-y-3">
          {(addIds.length || updateIds.length || removeIds.length) && diff ? (
            <div className="space-y-1">
              {addIds.length ? (
                <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
                  Add: <span className="font-mono">{addIds.slice(0, 12).join(", ")}</span>
                  {addIds.length > 12 ? ` (+${addIds.length - 12} more)` : ""}
                </div>
              ) : null}
              {updateIds.length ? (
                <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
                  Update: <span className="font-mono">{updateIds.slice(0, 12).join(", ")}</span>
                  {updateIds.length > 12 ? ` (+${updateIds.length - 12} more)` : ""}
                </div>
              ) : null}
              {removeIds.length ? (
                <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
                  Remove: <span className="font-mono">{removeIds.slice(0, 12).join(", ")}</span>
                  {removeIds.length > 12 ? ` (+${removeIds.length - 12} more)` : ""}
                </div>
              ) : null}
            </div>
          ) : null}

          {actors.length > 0 ? (
            <div>
              <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Actors</div>
              <div className="mt-1 space-y-1">
                {actors.slice(0, 20).map((a) => {
                  const id = String(a.id || "").trim();
                  if (!id) return null;
                  const title = String(a.title || "").trim();
                  const rt = String(a.runtime || "").trim();
                  const runner = String(a.runner || "").trim();
                  const enabled = a.enabled === false ? "disabled" : "enabled";
                  const cmd = formatCommand(a.command);
                  return (
                    <div key={id} className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
                      <span className="font-mono">{id}</span>
                      {title ? <span className="ml-2">• {title}</span> : null}
                      {rt ? <span className="ml-2 font-mono">• {rt}</span> : null}
                      {runner ? <span className="ml-2 font-mono">• {runner}</span> : null}
                      <span className="ml-2">• {enabled}</span>
                      {cmd ? <div className="mt-1 font-mono text-[11px] opacity-90">{cmd}</div> : null}
                    </div>
                  );
                })}
                {actors.length > 20 ? (
                  <div className={`text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                    …and {actors.length - 20} more
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {stableSettingsKeys.length > 0 ? (
            <div>
              <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Settings</div>
              <div className="mt-1 space-y-1">
                {stableSettingsKeys.slice(0, 12).map((k) => formatSettingLine(k))}
                {stableSettingsKeys.length > 12 ? (
                  <div className={`text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                    …and {stableSettingsKeys.length - 12} more
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          <div>
            <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Prompts</div>
            {(["preamble", "help", "standup"] as const).map((kind) => {
              const meta = promptMeta(kind);
              const label = formatPromptLabel(kind);
              const changed =
                typeof meta.changed === "boolean" ? meta.changed : undefined;
              const changedLabel =
                changed === undefined ? "" : changed ? "(changed)" : "(unchanged)";
              const srcLabel = (() => {
                const cur = formatSource(meta.currentSource);
                const nxt = formatSource(meta.newSource);
                if (cur && nxt) return `${cur} → ${nxt}`;
                if (meta.p.source === "builtin") return "builtin";
                if (meta.p.source === "repo") return "repo";
                return "";
              })();
              const charLabel = (() => {
                if (typeof meta.currentChars === "number" || typeof meta.newChars === "number") {
                  return `${String(meta.currentChars ?? "")} → ${String(meta.newChars ?? "")} chars`;
                }
                if (typeof meta.p.chars === "number") return `${String(meta.p.chars)} chars`;
                return "";
              })();
              const summaryMeta = [srcLabel, charLabel].filter(Boolean).join(" • ");
              return (
                <details key={kind} className="mt-2">
                  <summary className={`cursor-pointer text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
                    {label} {changedLabel}{" "}
                    <span className="ml-2 text-[11px] opacity-80">
                      {summaryMeta}
                    </span>
                  </summary>
                  {meta.p.preview ? (
                    <pre
                      className={`mt-2 p-2 rounded overflow-x-auto whitespace-pre text-[11px] ${
                        isDark ? "bg-slate-900 text-slate-200" : "bg-white text-gray-800 border border-gray-200"
                      }`}
                    >
                      {String(meta.p.preview)}
                    </pre>
                  ) : meta.p.source === "builtin" ? (
                    <div className={`mt-2 text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                      Uses built-in defaults (no repo file).
                    </div>
                  ) : null}
                </details>
              );
            })}
          </div>
        </div>
      </details>
    </>
  );

  if (!wrap) return <div>{body}</div>;

  return <div className={cardClass(isDark)}>{body}</div>;
}
