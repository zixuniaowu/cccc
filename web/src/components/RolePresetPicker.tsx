import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { classNames } from "../utils/classNames";
import { BUILTIN_ROLE_PRESETS, getRolePresetApplyState, getRolePresetById } from "../utils/rolePresets";

type RolePresetPickerProps = {
  draftValue: string;
  disabled?: boolean;
  onChangeDraft: (value: string) => void;
};

export function RolePresetPicker({ draftValue, disabled = false, onChangeDraft }: RolePresetPickerProps) {
  const { t } = useTranslation("actors");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [notice, setNotice] = useState("");

  const selectedPreset = getRolePresetById(selectedPresetId);

  useEffect(() => {
    setNotice("");
  }, [selectedPresetId]);

  useEffect(() => {
    if (!notice || !selectedPreset) return;
    if (String(draftValue || "").trim() !== String(selectedPreset.content || "").trim()) {
      setNotice("");
    }
  }, [draftValue, notice, selectedPreset]);

  const applyPreset = () => {
    if (!selectedPreset || disabled) return;
    const applyState = getRolePresetApplyState(draftValue, selectedPreset.content);
    if (applyState === "no_change") {
      setNotice(t("rolePresetAlreadyMatchesDraft", { name: selectedPreset.name }));
      return;
    }
    if (
      applyState === "confirm_replace" &&
      !window.confirm(t("rolePresetReplaceConfirm", { name: selectedPreset.name }))
    ) {
      return;
    }
    onChangeDraft(selectedPreset.content);
    setNotice(t("rolePresetAppliedNotice", { name: selectedPreset.name }));
  };

  return (
    <div className="space-y-2">
      <div className="min-w-0">
        <div className="text-xs font-semibold text-[var(--color-text-primary)]">{t("rolePreset")}</div>
        <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">{t("rolePresetHint")}</div>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <select
          className="w-full flex-1 rounded-xl border px-3 py-2.5 text-sm min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)]"
          value={selectedPresetId}
          onChange={(e) => setSelectedPresetId(e.target.value)}
          disabled={disabled}
        >
          <option value="">{t("rolePresetSelectPlaceholder")}</option>
          {BUILTIN_ROLE_PRESETS.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.name}
            </option>
          ))}
        </select>

        <button
          type="button"
          className={classNames(
            "rounded-xl px-4 py-2.5 text-sm font-medium transition-colors min-h-[44px] sm:shrink-0",
            selectedPreset && !disabled
              ? "bg-blue-600 text-white hover:bg-blue-500"
              : "border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-muted)]"
          )}
          onClick={applyPreset}
          disabled={!selectedPreset || disabled}
        >
          {t("applyPreset")}
        </button>
      </div>

      <div className="text-[10px] leading-5 text-[var(--color-text-muted)]">
        {selectedPreset ? selectedPreset.summary : t("rolePresetSelectPrompt")}
      </div>

      {notice ? (
        <div className="rounded-xl border px-3 py-2 text-xs border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-200">
          {notice}
        </div>
      ) : null}
    </div>
  );
}
