import { useTranslation } from 'react-i18next';
import { Actor, GroupDoc, Theme } from "../../types";
import { getGroupStatus, getGroupStatusLight } from "../../utils/groupStatus";
import { classNames } from "../../utils/classNames";
import { ThemeToggleCompact } from "../ThemeToggle";
import { LanguageSwitcher } from "../LanguageSwitcher";
import {
  ClipboardIcon,
  SearchIcon,
  RocketIcon,
  PlayIcon,
  PauseIcon,
  StopIcon,
  SettingsIcon,
  EditIcon,
  MoreIcon,
  MenuIcon
} from "../Icons";

export interface AppHeaderProps {
  isDark: boolean;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  webReadOnly?: boolean;
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  selectedGroupRunning: boolean;
  actors: Actor[];
  sseStatus: "connected" | "connecting" | "disconnected";
  busy: string;
  onOpenSidebar: () => void;
  onOpenGroupEdit?: () => void;
  onOpenSearch: () => void;
  onOpenContext: () => void;
  onStartGroup: () => void;
  onStopGroup: () => void;
  onSetGroupState: (state: "active" | "paused" | "idle") => void | Promise<void>;
  onOpenSettings: () => void;
  onOpenMobileMenu: () => void;
}

export function AppHeader({
  isDark,
  theme,
  onThemeChange,
  webReadOnly,
  selectedGroupId,
  groupDoc,
  selectedGroupRunning,
  actors,
  busy,
  onOpenSidebar,
  onOpenGroupEdit,
  onOpenSearch,
  onOpenContext,
  onStartGroup,
  onStopGroup,
  onSetGroupState,
  onOpenSettings,
  onOpenMobileMenu,
  sseStatus,
}: AppHeaderProps) {
  const { t } = useTranslation('layout');
  return (
    <header
      className="flex-shrink-0 z-20 px-4 h-14 flex items-center justify-between gap-3 glass-header"
    >
      <div className="flex items-center gap-3 min-w-0">
        <button
          className={classNames(
            "md:hidden p-2 -ml-2 rounded-xl transition-all glass-btn",
            isDark ? "text-slate-400 hover:text-white" : "text-gray-500 hover:text-gray-900"
          )}
          onClick={onOpenSidebar}
          aria-label={t('openSidebar')}
        >
          <MenuIcon size={18} />
        </button>

        <div className="min-w-0 flex flex-col">
          <div className="flex items-center gap-2">
            <h1 className={`text-sm font-semibold truncate ${isDark ? "text-slate-100" : "text-gray-900"}`}>
              {groupDoc?.title || (selectedGroupId ? selectedGroupId : t('selectGroup'))}
            </h1>
            {selectedGroupId && sseStatus !== "connected" && (
              <span
                className={classNames(
                  "flex-shrink-0 w-2 h-2 rounded-full",
                  sseStatus === "connecting" ? "bg-amber-400 animate-pulse" : "bg-rose-500"
                )}
                title={sseStatus === "connecting" ? t('reconnecting') : t('disconnected')}
              />
            )}
            {selectedGroupId &&
              groupDoc &&
              (() => {
                const status = isDark
                  ? getGroupStatus(selectedGroupRunning, groupDoc.state)
                  : getGroupStatusLight(selectedGroupRunning, groupDoc.state);
                return (
                  <span
                    className={classNames(
                      "w-2 h-2 rounded-full ring-2",
                      status.dotClass,
                      isDark ? "ring-white/10" : "ring-black/10"
                    )}
                    title={status.label}
                  />
                );
              })()}
          </div>
        </div>

        {selectedGroupId && !webReadOnly && onOpenGroupEdit && (
          <button
            className={classNames(
              "hidden md:inline-flex items-center justify-center gap-1 text-xs px-2.5 py-1.5 rounded-xl transition-all glass-btn",
              isDark ? "text-slate-200" : "text-gray-700"
            )}
            onClick={onOpenGroupEdit}
            title={t('editGroup')}
            aria-label={t('editGroup')}
          >
            <EditIcon size={14} />
          </button>
        )}
      </div>

      {/* Right Actions */}
      <div className="flex items-center gap-1">
        {!webReadOnly && (
          <>
            {/* Desktop Actions */}
            <div className="hidden md:flex items-center gap-1.5 mr-2">
              <button
                onClick={onOpenSearch}
                disabled={!selectedGroupId}
                className={classNames(
                  "p-2 rounded-xl transition-all glass-btn",
                  isDark ? "text-slate-400 hover:text-white" : "text-gray-400 hover:text-gray-900"
                )}
                title={t('searchMessages')}
              >
                <span className="sr-only">{t('searchMessages')}</span>
                <SearchIcon size={18} />
              </button>

              <button
                onClick={onOpenContext}
                disabled={!selectedGroupId}
                className={classNames(
                  "p-2 rounded-xl transition-all glass-btn",
                  isDark ? "text-slate-400 hover:text-white" : "text-gray-400 hover:text-gray-900"
                )}
                title={t('context')}
              >
                <span className="sr-only">{t('context')}</span>
                <ClipboardIcon size={18} />
              </button>

              <div className={`w-px h-4 mx-1 ${isDark ? "bg-white/10" : "bg-black/10"}`} />

              <button
                onClick={onStartGroup}
                disabled={!selectedGroupId || busy === "group-start" || actors.length === 0}
                className={classNames(
                  "p-2 rounded-xl transition-all",
                  isDark
                    ? "text-emerald-400 hover:bg-emerald-500/15 glass-btn"
                    : "text-emerald-600 hover:bg-emerald-50/80 glass-btn"
                )}
                title={t('launchAllAgents')}
              >
                <span className="sr-only">{t('launchAllAgents')}</span>
                <RocketIcon size={18} />
              </button>

              {groupDoc?.state === "paused" ? (
                <button
                  onClick={() => void onSetGroupState("active")}
                  disabled={!selectedGroupId || busy === "group-state"}
                  className={classNames(
                    "p-2 rounded-xl transition-all glass-btn",
                    isDark ? "text-amber-400" : "text-amber-600"
                  )}
                  title={t('resumeDelivery')}
                >
                  <span className="sr-only">{t('resumeDelivery')}</span>
                  <PlayIcon size={18} />
                </button>
              ) : (
                <button
                  onClick={() => void onSetGroupState("paused")}
                  disabled={!selectedGroupId || busy === "group-state"}
                  className={classNames(
                    "p-2 rounded-xl transition-all glass-btn",
                    isDark ? "text-slate-400 hover:text-amber-300" : "text-gray-400 hover:text-amber-600"
                  )}
                  title={t('pauseDelivery')}
                >
                  <span className="sr-only">{t('pauseDelivery')}</span>
                  <PauseIcon size={18} />
                </button>
              )}

              <button
                onClick={onStopGroup}
                disabled={!selectedGroupId || busy === "group-stop"}
                className={classNames(
                  "p-2 rounded-xl transition-all glass-btn",
                  isDark ? "text-slate-400 hover:text-rose-400" : "text-gray-400 hover:text-rose-600"
                )}
                title={t('stopAllAgents')}
              >
                <span className="sr-only">{t('stopAllAgents')}</span>
                <StopIcon size={18} />
              </button>
            </div>

            <div className="hidden md:flex items-center gap-1">
              <ThemeToggleCompact theme={theme} onThemeChange={onThemeChange} isDark={isDark} />
            </div>

            <button
              onClick={onOpenSettings}
              disabled={!selectedGroupId}
              className={classNames(
                "hidden md:flex p-2 rounded-xl transition-all glass-btn",
                isDark ? "text-slate-400 hover:text-slate-200" : "text-gray-400 hover:text-gray-600"
              )}
              title={t('settings')}
            >
              <SettingsIcon size={18} />
            </button>

            <div className={`hidden md:block w-px h-4 ${isDark ? "bg-white/10" : "bg-black/10"}`} />
            <div className="hidden md:block">
              <LanguageSwitcher isDark={isDark} />
            </div>

            <button
              className={classNames(
                "md:hidden flex items-center justify-center w-11 h-11 rounded-xl transition-all glass-btn",
                isDark ? "text-slate-400" : "text-gray-400"
              )}
              onClick={onOpenMobileMenu}
              title={t('menu')}
            >
              <MoreIcon size={18} />
            </button>
          </>
        )}
      </div>

    </header>
  );
}
