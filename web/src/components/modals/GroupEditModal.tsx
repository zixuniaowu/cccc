import { useTranslation } from "react-i18next";
import { useCopyFeedback } from "../../hooks/useCopyFeedback";
import { useModalA11y } from "../../hooks/useModalA11y";
import { useIMEComposition } from "../../hooks/useIMEComposition";

export interface GroupEditModalProps {
  isOpen: boolean;
  busy: string;
  groupId: string;
  ccccHome: string;
  projectRoot: string;
  title: string;
  topic: string;
  onChangeTitle: (title: string) => void;
  onChangeTopic: (topic: string) => void;
  onSave: () => void;
  onCancel: () => void;
  onDelete: () => void;
}

export function GroupEditModal({
  isOpen,
  busy,
  groupId,
  ccccHome,
  projectRoot,
  title,
  topic,
  onChangeTitle,
  onChangeTopic,
  onSave,
  onCancel,
  onDelete,
}: GroupEditModalProps) {
  const { t } = useTranslation("modals");
  const copyWithFeedback = useCopyFeedback();
  const { modalRef } = useModalA11y(isOpen, onCancel);
  const imeTitle = useIMEComposition({ value: title, onChange: onChangeTitle });
  const imeTopic = useIMEComposition({ value: topic, onChange: onChangeTopic });
  if (!isOpen) return null;

  const homeRoot = String(ccccHome || "").trim();
  const gid = String(groupId || "").trim();
  const groupDataDir = homeRoot && gid ? `${homeRoot}/groups/${gid}` : "";
  const groupConfigFile = groupDataDir ? `${groupDataDir}/group.yaml` : "";
  const groupLedgerFile = groupDataDir ? `${groupDataDir}/ledger.jsonl` : "";

  return (
    <div
      className="fixed inset-0 backdrop-blur-sm flex items-stretch sm:items-start justify-center p-0 sm:p-6 z-50 animate-fade-in glass-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="group-edit-title"
    >
      <div
        ref={modalRef}
        className="w-full h-full sm:h-auto sm:max-w-2xl sm:mt-12 sm:max-h-[calc(100dvh-6rem)] shadow-2xl animate-scale-in flex flex-col overflow-hidden rounded-none sm:rounded-2xl glass-modal"
      >
        <div className="px-6 py-5 sm:px-8 border-b safe-area-inset-top border-[var(--glass-border-subtle)]">
          <div id="group-edit-title" className="text-xl font-semibold text-[var(--color-text-primary)]">
            {t("groupEdit.title")}
          </div>
        </div>
        <div className="p-6 sm:p-8 space-y-5 flex-1 overflow-y-auto">
          <div>
            <label className="block text-sm font-medium mb-2.5 text-[var(--color-text-muted)]">{t("groupEdit.nameLabel")}</label>
            <input
              className="w-full rounded-xl px-4 py-3 text-base min-h-[52px] transition-colors glass-input text-[var(--color-text-primary)]"
              value={imeTitle.value}
              onChange={imeTitle.onChange}
              onCompositionStart={imeTitle.onCompositionStart}
              onCompositionEnd={imeTitle.onCompositionEnd}
              placeholder={t("groupEdit.groupNamePlaceholder")}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-2.5 text-[var(--color-text-muted)]">{t("groupEdit.descriptionLabel")}</label>
            <input
              className="w-full rounded-xl px-4 py-3 text-base min-h-[52px] transition-colors glass-input text-[var(--color-text-primary)]"
              value={imeTopic.value}
              onChange={imeTopic.onChange}
              onCompositionStart={imeTopic.onCompositionStart}
              onCompositionEnd={imeTopic.onCompositionEnd}
              placeholder={t("groupEdit.descriptionPlaceholder")}
            />
          </div>
          <div className="rounded-2xl p-5 sm:p-6 glass-panel">
            <div className="space-y-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                <div className="text-sm text-[var(--color-text-secondary)] sm:w-28 sm:shrink-0">{t("groupEdit.groupId")}</div>
                <div className="min-w-0 flex-1 font-mono text-sm truncate text-[var(--color-text-primary)]">
                  {groupId || "—"}
                </div>
                <button
                  className="self-start px-3 py-1.5 rounded-xl text-sm transition-colors glass-btn text-[var(--color-text-secondary)] sm:self-auto"
                  onClick={async () => {
                    const ok = await copyWithFeedback(groupId, {
                      successMessage: t("common:copied"),
                      errorMessage: t("common:copyFailed"),
                    });
                    if (!ok) return;
                  }}
                  disabled={!groupId}
                  title={t("groupEdit.copyGroupId")}
                  type="button"
                >
                  {t("common:copy")}
                </button>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                <div className="text-sm text-[var(--color-text-secondary)] sm:w-28 sm:shrink-0">{t("groupEdit.projectRoot")}</div>
                <div className="min-w-0 flex-1 font-mono text-sm truncate text-[var(--color-text-primary)]">
                  {projectRoot || t("groupEdit.noScopeAttached")}
                </div>
                <button
                  className="self-start px-3 py-1.5 rounded-xl text-sm transition-colors glass-btn text-[var(--color-text-secondary)] sm:self-auto"
                  onClick={async () => {
                    const ok = await copyWithFeedback(projectRoot, {
                      successMessage: t("common:copied"),
                      errorMessage: t("common:copyFailed"),
                    });
                    if (!ok) return;
                  }}
                  disabled={!projectRoot}
                  title={t("groupEdit.copyProjectRoot")}
                  type="button"
                >
                  {t("common:copy")}
                </button>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                <div className="text-sm text-[var(--color-text-secondary)] sm:w-28 sm:shrink-0">{t("groupEdit.groupDataDirectory")}</div>
                <div className="min-w-0 flex-1 font-mono text-sm truncate text-[var(--color-text-primary)]">
                  {groupDataDir || "—"}
                </div>
                <button
                  className="self-start px-3 py-1.5 rounded-xl text-sm transition-colors glass-btn text-[var(--color-text-secondary)] sm:self-auto"
                  onClick={async () => {
                    const ok = await copyWithFeedback(groupDataDir, {
                      successMessage: t("common:copied"),
                      errorMessage: t("common:copyFailed"),
                    });
                    if (!ok) return;
                  }}
                  disabled={!groupDataDir}
                  title={t("groupEdit.copyDataDir")}
                  type="button"
                >
                  {t("common:copy")}
                </button>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                <div className="text-sm text-[var(--color-text-secondary)] sm:w-28 sm:shrink-0">{t("groupEdit.groupConfigFile")}</div>
                <div className="min-w-0 flex-1 font-mono text-sm truncate text-[var(--color-text-primary)]">
                  {groupConfigFile || "—"}
                </div>
                <button
                  className="self-start px-3 py-1.5 rounded-xl text-sm transition-colors glass-btn text-[var(--color-text-secondary)] sm:self-auto"
                  onClick={async () => {
                    const ok = await copyWithFeedback(groupConfigFile, {
                      successMessage: t("common:copied"),
                      errorMessage: t("common:copyFailed"),
                    });
                    if (!ok) return;
                  }}
                  disabled={!groupConfigFile}
                  title={t("groupEdit.copyConfigFile")}
                  type="button"
                >
                  {t("common:copy")}
                </button>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                <div className="text-sm text-[var(--color-text-secondary)] sm:w-28 sm:shrink-0">{t("groupEdit.groupLedgerFile")}</div>
                <div className="min-w-0 flex-1 font-mono text-sm truncate text-[var(--color-text-primary)]">
                  {groupLedgerFile || "—"}
                </div>
                <button
                  className="self-start px-3 py-1.5 rounded-xl text-sm transition-colors glass-btn text-[var(--color-text-secondary)] sm:self-auto"
                  onClick={async () => {
                    const ok = await copyWithFeedback(groupLedgerFile, {
                      successMessage: t("common:copied"),
                      errorMessage: t("common:copyFailed"),
                    });
                    if (!ok) return;
                  }}
                  disabled={!groupLedgerFile}
                  title={t("groupEdit.copyLedgerFile")}
                  type="button"
                >
                  {t("common:copy")}
                </button>
              </div>
            </div>
          </div>
          <div className="flex gap-3 pt-4 flex-wrap">
            <button
              className="flex-1 rounded-xl bg-blue-600 hover:bg-blue-500 text-white px-5 py-3 text-base font-semibold shadow-lg disabled:opacity-50 transition-all min-h-[52px]"
              onClick={onSave}
              disabled={!title.trim() || busy === "group-update"}
            >
              {t("common:save")}
            </button>
            <button
              className="px-5 py-3 rounded-xl text-base font-medium transition-colors min-h-[52px] glass-btn text-[var(--color-text-secondary)]"
              onClick={onCancel}
            >
              {t("common:cancel")}
            </button>
            <button
              className="px-5 py-3 rounded-xl border text-base font-medium disabled:opacity-50 transition-colors min-h-[52px] bg-rose-500/15 border-rose-500/30 text-rose-600 dark:text-rose-400 hover:bg-rose-500/25"
              onClick={() => {
                onCancel();
                onDelete();
              }}
              disabled={busy === "group-delete"}
              title={t("groupEdit.deleteTitle")}
            >
              {t("common:delete")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
