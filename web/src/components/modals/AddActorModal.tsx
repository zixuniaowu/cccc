import {
  ActorProfile,
  RuntimeInfo,
  SupportedRuntime,
  SUPPORTED_RUNTIMES,
  RUNTIME_INFO,
} from "../../types";
import { useTranslation } from "react-i18next";
import { BASIC_MCP_CONFIG_SNIPPET, COPILOT_MCP_CONFIG_SNIPPET, OPENCODE_MCP_CONFIG_SNIPPET } from "../../utils/mcpConfigSnippets";
import { classNames } from "../../utils/classNames";
import { useModalA11y } from "../../hooks/useModalA11y";
import { CapabilityPicker } from "../CapabilityPicker";
import { formatCapabilityIdInput, parseCapabilityIdInput } from "../../utils/capabilityAutoload";

export interface AddActorModalProps {
  isOpen: boolean;
  isDark: boolean;
  busy: string;
  hasForeman: boolean;
  runtimes: RuntimeInfo[];

  suggestedActorId: string;
  newActorId: string;
  setNewActorId: (id: string) => void;

  newActorRole: "peer" | "foreman";
  setNewActorRole: (role: "peer" | "foreman") => void;

  newActorUseProfile: boolean;
  setNewActorUseProfile: (v: boolean) => void;
  newActorProfileId: string;
  setNewActorProfileId: (id: string) => void;
  actorProfiles: ActorProfile[];
  actorProfilesBusy: boolean;

  newActorRuntime: SupportedRuntime;
  setNewActorRuntime: (runtime: SupportedRuntime) => void;

  newActorCommand: string;
  setNewActorCommand: (cmd: string) => void;
  newActorUseDefaultCommand: boolean;
  setNewActorUseDefaultCommand: (v: boolean) => void;

  newActorSecretsSetText: string;
  setNewActorSecretsSetText: (v: string) => void;
  newActorCapabilityAutoloadText: string;
  setNewActorCapabilityAutoloadText: (v: string) => void;

  showAdvancedActor: boolean;
  setShowAdvancedActor: (show: boolean) => void;

  addActorError: string;
  setAddActorError: (msg: string) => void;

  canAddActor: boolean;
  addActorDisabledReason: string;

  onAddActor: () => void;
  onSaveAsProfile: () => void;
  onClose: () => void;
  onCancelAndReset: () => void;
}

export function AddActorModal({
  isOpen,
  isDark,
  busy,
  hasForeman,
  runtimes,
  suggestedActorId,
  newActorId,
  setNewActorId,
  newActorRole,
  setNewActorRole,
  newActorUseProfile,
  setNewActorUseProfile,
  newActorProfileId,
  setNewActorProfileId,
  actorProfiles,
  actorProfilesBusy,
  newActorRuntime,
  setNewActorRuntime,
  newActorCommand,
  setNewActorCommand,
  newActorUseDefaultCommand,
  setNewActorUseDefaultCommand,
  newActorSecretsSetText,
  setNewActorSecretsSetText,
  newActorCapabilityAutoloadText,
  setNewActorCapabilityAutoloadText,
  showAdvancedActor,
  setShowAdvancedActor,
  addActorError,
  setAddActorError,
  canAddActor,
  addActorDisabledReason,
  onAddActor,
  onSaveAsProfile,
  onClose,
  onCancelAndReset,
}: AddActorModalProps) {
  const { t } = useTranslation('actors');
  const { modalRef } = useModalA11y(isOpen, onClose);
  if (!isOpen) return null;

  const defaultCommand = runtimes.find((r) => r.name === newActorRuntime)?.recommended_command || "";
  const selectedProfile = actorProfiles.find((item) => String(item.id || "") === String(newActorProfileId || ""));

  return (
    <div
      className="fixed inset-0 backdrop-blur-sm flex items-stretch sm:items-start justify-center p-0 sm:p-6 z-50 animate-fade-in glass-overlay"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-actor-title"
    >
      <div
        ref={modalRef}
        className="w-full h-full sm:h-auto sm:max-w-lg sm:mt-16 sm:max-h-[80vh] overflow-y-auto border shadow-2xl animate-scale-in rounded-none sm:rounded-2xl glass-modal"
      >
        <div className="px-6 py-4 border-b sticky top-0 safe-area-inset-top border-[var(--glass-border-subtle)] glass-header">
          <div id="add-actor-title" className="text-lg font-semibold text-[var(--color-text-primary)]">
            {t('addAiAgent')}
          </div>
          <div className="text-sm mt-1 text-[var(--color-text-muted)]">{t('chooseRuntime')}</div>
        </div>
        <div className="p-6 space-y-5">
          {addActorError && (
            <div
              className={`rounded-xl border px-4 py-2.5 text-sm flex items-center justify-between gap-3 ${
                "border-rose-500/30 bg-rose-500/15 text-rose-600 dark:text-rose-400"
              }`}
              role="alert"
            >
              <span>{addActorError}</span>
              <button
                className="text-rose-600 dark:text-rose-400 hover:opacity-80"
                onClick={() => setAddActorError("")}
              >
                ×
              </button>
            </div>
          )}

          <div>
            <label className={`block text-xs font-medium mb-2 text-[var(--color-text-muted)]`}>
              {t('agentName')} <span className={"text-[var(--color-text-muted)]"}>{t('unicodeSupport')}</span>
            </label>
            <input
              className={`w-full rounded-xl border px-4 py-2.5 text-sm min-h-[44px] transition-colors ${
                "glass-input text-[var(--color-text-primary)]"
              }`}
              value={newActorId}
              onChange={(e) => setNewActorId(e.target.value)}
              placeholder={suggestedActorId}
            />
            <div className={`text-[10px] mt-1 text-[var(--color-text-muted)]`}>
              {t('leaveEmptyToUse')}{" "}
              <code className={`px-1 rounded bg-[var(--glass-tab-bg)]`}>{suggestedActorId}</code>
            </div>
          </div>

          <div>
            <label className={`block text-xs font-medium mb-2 text-[var(--color-text-muted)]`}>{t("creationMode")}</label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                className={classNames(
                  "px-3 py-2.5 rounded-xl border text-sm min-h-[44px] font-medium transition-colors",
                  newActorUseProfile
                    ? "glass-btn border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]"
                    : "bg-blue-600 text-white border-blue-600"
                )}
                onClick={() => setNewActorUseProfile(false)}
              >
                {t("customAgent")}
              </button>
              <button
                type="button"
                className={classNames(
                  "px-3 py-2.5 rounded-xl border text-sm min-h-[44px] font-medium transition-colors",
                  newActorUseProfile
                    ? "bg-blue-600 text-white border-blue-600"
                    : "glass-btn border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]"
                )}
                onClick={() => setNewActorUseProfile(true)}
              >
                {t("fromActorProfile")}
              </button>
            </div>
          </div>

          {newActorUseProfile ? (
            <div>
              <label className={`block text-xs font-medium mb-2 text-[var(--color-text-muted)]`}>{t("actorProfile")}</label>
              <select
                className={`w-full rounded-xl border px-4 py-2.5 text-sm min-h-[44px] transition-colors ${
                  "glass-input text-[var(--color-text-primary)]"
                }`}
                value={newActorProfileId}
                onChange={(e) => setNewActorProfileId(e.target.value)}
                disabled={actorProfilesBusy}
              >
                <option value="">{actorProfilesBusy ? t("loadingProfiles") : t("selectActorProfile")}</option>
                {actorProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name || profile.id}
                  </option>
                ))}
              </select>
              {selectedProfile ? (
                <div className={`text-[10px] mt-1 text-[var(--color-text-muted)]`}>
                  {RUNTIME_INFO[String(selectedProfile.runtime) as SupportedRuntime]?.label || selectedProfile.runtime}
                </div>
              ) : null}
            </div>
          ) : (
            <div>
              <label className={`block text-xs font-medium mb-2 text-[var(--color-text-muted)]`}>{t('aiRuntime')}</label>
            <select
              className={`w-full rounded-xl border px-4 py-2.5 text-sm min-h-[44px] transition-colors ${
                "glass-input text-[var(--color-text-primary)]"
              }`}
              value={newActorRuntime}
              onChange={(e) => {
                const next = e.target.value as SupportedRuntime;
                setNewActorRuntime(next);
                setNewActorCommand("");
                setNewActorUseDefaultCommand(next !== "custom");
              }}
            >
              {SUPPORTED_RUNTIMES.map((rt) => {
                const info = RUNTIME_INFO[rt];
                const rtInfo = runtimes.find((r) => r.name === rt);
                const available = rtInfo?.available ?? false;
                const selectable = available || rt === "custom";
                return (
                  <option key={rt} value={rt} disabled={!selectable}>
                    {info?.label || rt}
                    {!available && rt !== "custom" ? ` ${t('notInstalled')}` : ""}
                  </option>
                );
              })}
            </select>
            {RUNTIME_INFO[newActorRuntime]?.desc ? (
              <div className={`text-[10px] mt-1 text-[var(--color-text-muted)]`}>{RUNTIME_INFO[newActorRuntime].desc}</div>
            ) : null}

            {(newActorRuntime === "cursor" ||
              newActorRuntime === "kilocode" ||
              newActorRuntime === "opencode" ||
              newActorRuntime === "copilot" ||
              newActorRuntime === "custom") && (
              <div
                className={`mt-2 rounded-xl border px-3 py-2 text-[11px] ${
                  "border-amber-500/30 bg-amber-500/15 text-amber-600 dark:text-amber-400"
                }`}
              >
                <div className="font-medium">{t('manualMcpRequired')}</div>
                {newActorRuntime === "custom" ? (
                  <>
                    <div className="mt-1">
                      {t('customCommandHint').replace(/<1>|<\/1>/g, '')}
                    </div>
                    <div className="mt-1">
                      {t('configureMcpStdio')}{" "}
                      <code className={`px-1 rounded ${"bg-amber-500/15"}`}>cccc</code> {t('thatRuns')}{" "}
                      <code className={`px-1 rounded ${"bg-amber-500/15"}`}>cccc mcp</code>.
                    </div>
                  </>
                ) : newActorRuntime === "cursor" ? (
                  <>
                    <div className="mt-1">
                      {t('createEditFile')}{" "}
                      <code className={`px-1 rounded ${"bg-amber-500/15"}`}>~/.cursor/mcp.json</code> (or{" "}
                      <code className={`px-1 rounded ${"bg-amber-500/15"}`}>.cursor/mcp.json</code> {t('orInProject')})
                    </div>
                    <div className="mt-1">{t('addMcpConfig')}</div>
                  </>
                ) : newActorRuntime === "kilocode" ? (
                  <>
                    <div className="mt-1">
                      {t('createEditFile')}{" "}
                      <code className={`px-1 rounded ${"bg-amber-500/15"}`}>.kilocode/mcp.json</code> {t('inProjectRoot')}
                    </div>
                    <div className="mt-1">{t('addMcpConfig')}</div>
                  </>
                ) : newActorRuntime === "opencode" ? (
                  <>
                    <div className="mt-1">
                      {t('createEditFile')}{" "}
                      <code className={`px-1 rounded ${"bg-amber-500/15"}`}>~/.config/opencode/opencode.json</code>
                    </div>
                    <div className="mt-1">{t('addMcpConfig')}</div>
                  </>
                ) : (
                  <>
                    <div className="mt-1">
                      {t('createEditFile')}{" "}
                      <code className={`px-1 rounded ${"bg-amber-500/15"}`}>~/.copilot/mcp-config.json</code>
                    </div>
                    <div className="mt-1">
                      {t('addMcpConfigOrFlag')}{" "}
                      <code className={`px-1 rounded ${"bg-amber-500/15"}`}>--additional-mcp-config</code>):
                    </div>
                  </>
                )}

                {newActorRuntime !== "custom" ? (
                  <pre className={`mt-1.5 p-2 rounded overflow-x-auto whitespace-pre ${"bg-amber-500/10 text-amber-600 dark:text-amber-300"}`}>
                    <code>
                      {newActorRuntime === "opencode"
                        ? OPENCODE_MCP_CONFIG_SNIPPET
                        : newActorRuntime === "copilot"
                          ? COPILOT_MCP_CONFIG_SNIPPET
                          : BASIC_MCP_CONFIG_SNIPPET}
                    </code>
                  </pre>
                ) : null}

                <div className={`mt-1 text-[10px] ${"text-amber-600/80 dark:text-amber-400/80"}`}>
                  {t('restartAfterConfig')}
                </div>
              </div>
            )}
            </div>
          )}

          {!newActorUseProfile ? (
          <div>
            <label className={`block text-xs font-medium mb-2 text-[var(--color-text-muted)]`}>{t('role')}</label>
            <div className="flex gap-2">
              <button
                className={classNames(
                  "flex-1 px-4 py-2.5 rounded-xl border text-sm font-medium transition-all min-h-[44px]",
                  newActorRole === "foreman"
                    ? "bg-amber-500/20 border-amber-500 text-amber-600"
                    : hasForeman
                      ? "bg-[var(--glass-panel-bg)] border-[var(--glass-border-subtle)] text-[var(--color-text-muted)] cursor-not-allowed"
                      : "glass-panel text-[var(--color-text-secondary)] hover:border-[var(--glass-border-subtle)]"
                )}
                onClick={() => {
                  if (!hasForeman) setNewActorRole("foreman");
                }}
                disabled={hasForeman}
              >
                {t('foremanRole')} {hasForeman && t('foremanExists')}
              </button>
              <button
                className={classNames(
                  "flex-1 px-4 py-2.5 rounded-xl border text-sm font-medium transition-all min-h-[44px]",
                  newActorRole === "peer"
                    ? "bg-blue-500/20 border-blue-500 text-blue-600"
                    : !hasForeman
                      ? "bg-[var(--glass-panel-bg)] border-[var(--glass-border-subtle)] text-[var(--color-text-muted)] cursor-not-allowed"
                      : "glass-panel text-[var(--color-text-secondary)] hover:border-[var(--glass-border-subtle)]"
                )}
                onClick={() => {
                  if (hasForeman) setNewActorRole("peer");
                }}
                disabled={!hasForeman}
              >
                {t('peerRole')} {!hasForeman && t('needForemanFirst')}
              </button>
            </div>
            <div className={`text-[10px] mt-1.5 text-[var(--color-text-muted)]`}>
              {hasForeman ? t('foremanLeads') : t('firstAgentForeman')}
            </div>
          </div>
          ) : null}

          {!newActorUseProfile ? (
          <button
            className={`flex items-center gap-2 text-xs min-h-[36px] text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]`}
            onClick={() => setShowAdvancedActor(!showAdvancedActor)}
          >
            <span className={classNames("transition-transform", showAdvancedActor && "rotate-90")}>▶</span>
            {t('advancedOptions')}
          </button>
          ) : null}

          {!newActorUseProfile && showAdvancedActor && (
            <div className={`space-y-4 pl-4 border-l-2 border-[var(--glass-border-subtle)]`}>
              <CapabilityPicker
                isDark={isDark}
                value={parseCapabilityIdInput(newActorCapabilityAutoloadText)}
                onChange={(next) => setNewActorCapabilityAutoloadText(formatCapabilityIdInput(next))}
                disabled={busy === "actor-add"}
                label={t("autoloadCapabilities")}
                hint={t("autoloadCapabilitiesHint")}
              />

              <div>
                <label className={`block text-xs font-medium mb-2 text-[var(--color-text-muted)]`}>{t('commandOverrideOptional')}</label>
                {newActorRuntime !== "custom" ? (
                  <label className={`inline-flex items-center gap-2 text-xs mb-2 text-[var(--color-text-secondary)]`}>
                    <input
                      type="checkbox"
                      checked={newActorUseDefaultCommand}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setNewActorUseDefaultCommand(checked);
                        if (checked) setNewActorCommand("");
                      }}
                    />
                    {t("useRuntimeDefaultCommand")}
                  </label>
                ) : null}
                {!newActorUseDefaultCommand || newActorRuntime === "custom" ? (
                  <input
                    className="w-full rounded-xl border px-3 py-2 text-sm font-mono min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)]"
                    value={newActorCommand}
                    onChange={(e) => setNewActorCommand(e.target.value)}
                    placeholder={defaultCommand || t('enterCommand')}
                  />
                ) : null}
                {defaultCommand.trim() ? (
                  <div className={`text-[10px] mt-1 text-[var(--color-text-muted)]`}>
                    {newActorUseDefaultCommand && newActorRuntime !== "custom" ? t("usingRuntimeDefaultCommand") : t('default')}{" "}
                    <code className={`px-1 rounded bg-[var(--glass-tab-bg)]`}>{defaultCommand}</code>
                  </div>
                ) : null}
              </div>

              <div>
                <label className={`block text-xs font-medium mb-2 text-[var(--color-text-muted)]`}>{t('secretsWriteOnly')}</label>
                <textarea
                  className="w-full rounded-xl border px-3 py-2 text-sm font-mono min-h-[96px] transition-colors glass-input text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)]"
                  value={newActorSecretsSetText}
                  onChange={(e) => setNewActorSecretsSetText(e.target.value)}
                  placeholder={'export OPENAI_API_KEY="...";\nexport ANTHROPIC_API_KEY="...";'}
                />
                <div className={`text-[10px] mt-1 text-[var(--color-text-muted)]`}>
                  {t('secretsStoredLocally').replace(/<1>|<\/1>/g, '')}
                </div>
                <div className={`text-[10px] mt-1 text-[var(--color-text-muted)]`}>
                  {t('secretsFormat').replace(/<1>|<\/1>|<2>|<\/2>/g, '')}
                </div>
              </div>
            </div>
          )}

          {!newActorUseProfile ? (
            <button
              type="button"
              className={`px-4 py-2.5 rounded-xl text-sm font-medium transition-colors min-h-[40px] ${
                "glass-btn text-[var(--color-text-secondary)]"
              }`}
              onClick={onSaveAsProfile}
              disabled={busy === "actor-profile-save" || busy === "actor-add"}
            >
              {busy === "actor-profile-save" ? t("savingProfile") : t("addToActorProfiles")}
            </button>
          ) : null}

          <div className="flex gap-3 pt-2">
            <div className="flex-1">
              <button
                className="w-full rounded-xl bg-blue-600 hover:bg-blue-500 text-white px-4 py-2.5 text-sm font-semibold shadow-lg disabled:opacity-50 disabled:cursor-not-allowed transition-all min-h-[44px]"
                onClick={onAddActor}
                disabled={!canAddActor}
              >
                {busy === "actor-add" ? t('adding') : (newActorUseProfile ? t("createFromProfile") : t('addAgent'))}
              </button>
              {addActorDisabledReason && <div className="text-[10px] text-amber-500 mt-1.5">{addActorDisabledReason}</div>}
            </div>
            <button
              className={`px-4 py-2.5 rounded-xl text-sm font-medium transition-colors min-h-[44px] ${
                "glass-btn text-[var(--color-text-secondary)]"
              }`}
              onClick={onCancelAndReset}
            >
              {t('common:cancel')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
