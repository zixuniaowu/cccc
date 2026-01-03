import { Actor, GroupDoc, Theme } from "../../types";
import { getGroupStatus, getGroupStatusLight } from "../../utils/groupStatus";
import { classNames } from "../../utils/classNames";
import { ThemeToggleCompact } from "../ThemeToggle";

export interface AppHeaderProps {
  isDark: boolean;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  selectedGroupRunning: boolean;
  actors: Actor[];
  busy: string;
  errorMsg: string;
  onDismissError: () => void;
  onOpenSidebar: () => void;
  onOpenGroupEdit: () => void;
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
  selectedGroupId,
  groupDoc,
  selectedGroupRunning,
  actors,
  busy,
  errorMsg,
  onDismissError,
  onOpenSidebar,
  onOpenGroupEdit,
  onOpenContext,
  onStartGroup,
  onStopGroup,
  onSetGroupState,
  onOpenSettings,
  onOpenMobileMenu,
}: AppHeaderProps) {
  return (
    <header
      className={`flex-shrink-0 border-b backdrop-blur z-20 px-4 h-14 flex items-center justify-between gap-3 transition-colors ${
        isDark ? "border-slate-800/50 bg-slate-900/80" : "border-gray-200 bg-white/80"
      }`}
    >
      <div className="flex items-center gap-3 min-w-0">
        <button
          className={`md:hidden p-2 -ml-2 rounded-lg transition-colors ${
            isDark ? "text-slate-400 hover:text-white hover:bg-slate-800" : "text-gray-500 hover:text-gray-900 hover:bg-gray-100"
          }`}
          onClick={onOpenSidebar}
          aria-label="Open sidebar"
        >
          <div className="space-y-1">
            <div className="w-4 h-0.5 bg-current"></div>
            <div className="w-4 h-0.5 bg-current"></div>
            <div className="w-4 h-0.5 bg-current"></div>
          </div>
        </button>

        <div className="min-w-0 flex flex-col">
          <div className="flex items-center gap-2">
            <h1 className={`text-sm font-semibold truncate ${isDark ? "text-slate-100" : "text-gray-900"}`}>
              {groupDoc?.title || (selectedGroupId ? selectedGroupId : "Select a group")}
            </h1>
            {selectedGroupId &&
              groupDoc &&
              (() => {
                const status = isDark ? getGroupStatus(selectedGroupRunning, groupDoc.state) : getGroupStatusLight(selectedGroupRunning, groupDoc.state);
                return (
                  <span
                    className={`w-2 h-2 rounded-full ${status.colorClass.replace("text-", "bg-").split(" ")[0]}`}
                    title={status.label}
                  />
                );
              })()}
          </div>
        </div>

        {selectedGroupId && (
          <button
            className={classNames(
              "hidden sm:inline-flex items-center justify-center gap-1 text-xs px-2.5 py-1.5 rounded-lg border shadow-sm transition-colors",
              isDark
                ? "border-slate-700 bg-slate-800/60 text-slate-200 hover:bg-slate-800"
                : "border-gray-200 bg-gray-50 text-gray-700 hover:bg-gray-100"
            )}
            onClick={onOpenGroupEdit}
            title="Edit group"
            aria-label="Edit group"
          >
            ✏️
          </button>
        )}
      </div>

      {/* Right Actions */}
      <div className="flex items-center gap-1">
        {/* Desktop Actions - subtle */}
        <div className="hidden sm:flex items-center gap-1.5 mr-2">
          <button
            onClick={onOpenContext}
            disabled={!selectedGroupId}
            className={`p-2 rounded-xl transition-colors ${
              isDark ? "text-slate-400 hover:text-white hover:bg-slate-800" : "text-gray-400 hover:text-gray-900 hover:bg-gray-100"
            }`}
            title="Context (Clipboard)"
          >
            <span className="sr-only">Context</span>
            📋
          </button>

          <div className={`w-px h-4 mx-1 ${isDark ? "bg-slate-800" : "bg-gray-200"}`} />

          <button
            onClick={onStartGroup}
            disabled={!selectedGroupId || busy === "group-start" || actors.length === 0}
            className={`p-2 rounded-xl transition-colors ${
              isDark ? "text-emerald-500 hover:text-emerald-400 hover:bg-emerald-500/10" : "text-emerald-600 hover:bg-emerald-50"
            }`}
            title="Launch All Agents"
          >
            <span className="sr-only">Launch</span>
            🚀
          </button>

          {groupDoc?.state === "paused" ? (
            <button
              onClick={() => void onSetGroupState("active")}
              disabled={!selectedGroupId || busy === "group-state"}
              className={`p-2 rounded-xl transition-colors ${
                isDark ? "text-amber-400 hover:bg-amber-500/10" : "text-amber-600 hover:bg-amber-50"
              }`}
              title="Resume Delivery"
            >
              <span className="sr-only">Resume</span>
              ▶
            </button>
          ) : (
            <button
              onClick={() => void onSetGroupState("paused")}
              disabled={!selectedGroupId || busy === "group-state"}
              className={`p-2 rounded-xl transition-colors ${
                isDark
                  ? "text-slate-400 hover:text-amber-300 hover:bg-amber-500/10"
                  : "text-gray-400 hover:text-amber-600 hover:bg-amber-50"
              }`}
              title="Pause Delivery"
            >
              <span className="sr-only">Pause</span>
              ⏸
            </button>
          )}

          <button
            onClick={onStopGroup}
            disabled={!selectedGroupId || busy === "group-stop"}
            className={`p-2 rounded-xl transition-colors ${
              isDark ? "text-slate-400 hover:text-rose-400 hover:bg-rose-500/10" : "text-gray-400 hover:text-rose-600 hover:bg-rose-50"
            }`}
            title="Stop All Agents"
          >
            <span className="sr-only">Stop</span>
            ⏹
          </button>
          <div className={`w-px h-4 mx-1 ${isDark ? "bg-slate-800" : "bg-gray-200"}`} />
        </div>

        <div className="hidden sm:block">
          <ThemeToggleCompact theme={theme} onThemeChange={onThemeChange} isDark={isDark} />
        </div>

        <button
          onClick={onOpenSettings}
          disabled={!selectedGroupId}
          className={`hidden sm:flex p-2 rounded-lg transition-colors ${
            isDark ? "text-slate-400 hover:text-slate-200 hover:bg-slate-800" : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
          }`}
          title="Settings"
        >
          ⚙️
        </button>

        <button
          className={classNames(
            "sm:hidden flex items-center justify-center w-8 h-8 rounded-lg transition-colors",
            isDark ? "text-slate-400 hover:bg-slate-800" : "text-gray-400 hover:bg-gray-100"
          )}
          onClick={onOpenMobileMenu}
          title="Menu"
        >
          <span className="text-lg leading-none transform rotate-90" aria-hidden="true">
            ⋯
          </span>
        </button>
      </div>

      {/* Error Toast - Floating below header now */}
      {errorMsg && (
        <div className="absolute top-16 left-1/2 -translate-x-1/2 z-50 animate-slide-up">
          <div
            className={`rounded-xl border px-4 py-2.5 text-sm flex items-center gap-3 shadow-xl ${
              isDark
                ? "border-rose-500/30 bg-rose-950/90 text-rose-300 backdrop-blur-md"
                : "border-rose-200 bg-white/90 text-rose-700 backdrop-blur-md"
            }`}
            role="alert"
          >
            <span>{errorMsg}</span>
            <button className="opacity-70 hover:opacity-100" onClick={onDismissError}>
              ×
            </button>
          </div>
        </div>
      )}
    </header>
  );
}
