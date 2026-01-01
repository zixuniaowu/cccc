import { Actor, GroupDoc } from "../../types";
import { getGroupStatus, getGroupStatusLight } from "../../utils/groupStatus";
import { classNames } from "../../utils/classNames";

export interface MobileMenuSheetProps {
  isOpen: boolean;
  isDark: boolean;
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  selectedGroupRunning: boolean;
  actors: Actor[];
  busy: string;
  onClose: () => void;
  onToggleTheme: () => void;
  onOpenSearch: () => void;
  onOpenContext: () => void;
  onOpenSettings: () => void;
  onOpenGroupEdit: () => void;
  onStartGroup: () => void;
  onStopGroup: () => void;
  onSetGroupState: (state: "active" | "paused" | "idle") => void | Promise<void>;
}

export function MobileMenuSheet({
  isOpen,
  isDark,
  selectedGroupId,
  groupDoc,
  selectedGroupRunning,
  actors,
  busy,
  onClose,
  onToggleTheme,
  onOpenSearch,
  onOpenContext,
  onOpenSettings,
  onOpenGroupEdit,
  onStartGroup,
  onStopGroup,
  onSetGroupState,
}: MobileMenuSheetProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 sm:hidden animate-fade-in">
      <div
        className={isDark ? "absolute inset-0 bg-black/60 backdrop-blur-sm" : "absolute inset-0 bg-black/40 backdrop-blur-sm"}
        onClick={onClose}
        aria-hidden="true"
      />

      <div
        className={classNames(
          "absolute bottom-0 left-0 right-0 rounded-t-3xl border shadow-2xl animate-slide-up transform transition-transform",
          isDark ? "bg-slate-900 border-slate-700" : "bg-white border-gray-200"
        )}
        role="dialog"
        aria-modal="true"
        aria-label="Menu"
      >
        <div className="flex justify-center pt-3 pb-1" onClick={onClose}>
          <div className={`w-12 h-1.5 rounded-full opacity-50 ${isDark ? "bg-slate-600" : "bg-gray-300"}`} />
        </div>

        <div className="px-6 pb-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className={classNames("text-lg font-bold truncate", isDark ? "text-slate-100" : "text-gray-900")}>
              {groupDoc?.title || (selectedGroupId ? selectedGroupId : "Menu")}
            </div>
            {selectedGroupId && groupDoc && (
              <div className="flex items-center gap-2 mt-1">
                <span
                  className={classNames(
                    "text-xs px-2 py-0.5 rounded-full font-medium",
                    isDark ? getGroupStatus(selectedGroupRunning, groupDoc.state).colorClass : getGroupStatusLight(selectedGroupRunning, groupDoc.state).colorClass
                  )}
                >
                  {(isDark ? getGroupStatus(selectedGroupRunning, groupDoc.state) : getGroupStatusLight(selectedGroupRunning, groupDoc.state)).label}
                </span>
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className={classNames(
              "p-2 rounded-full transition-colors",
              isDark ? "text-slate-400 hover:text-slate-200 hover:bg-slate-800" : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
            )}
            aria-label="Close menu"
          >
            <div className="text-2xl leading-none">×</div>
          </button>
        </div>

        <div className="p-4 space-y-2 safe-area-inset-bottom">
          {!selectedGroupId && (
            <div className={classNames("text-sm px-1 pb-2", isDark ? "text-slate-400" : "text-gray-500")}>
              Select a group to enable actions.
            </div>
          )}

          <button
            className={classNames(
              "w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50",
              isDark ? "bg-slate-800/80 hover:bg-slate-700 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
            )}
            onClick={() => {
              onClose();
              onOpenSearch();
            }}
            disabled={!selectedGroupId}
          >
            <span className="text-lg" aria-hidden="true">
              🔍
            </span>
            <span>Search Messages</span>
          </button>

          <div className="grid grid-cols-2 gap-2">
            <button
              className={classNames(
                "w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50",
                isDark ? "bg-slate-800/80 hover:bg-slate-700 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
              )}
              onClick={() => {
                onClose();
                onOpenContext();
              }}
              disabled={!selectedGroupId}
            >
              <span className="text-lg" aria-hidden="true">
                📋
              </span>
              <span>Context</span>
            </button>

            <button
              className={classNames(
                "w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50",
                isDark ? "bg-slate-800/80 hover:bg-slate-700 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
              )}
              onClick={() => {
                onClose();
                onOpenSettings();
              }}
              disabled={!selectedGroupId}
            >
              <span className="text-lg" aria-hidden="true">
                ⚙️
              </span>
              <span>Settings</span>
            </button>
          </div>

          <button
            className={classNames(
              "w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50",
              isDark ? "bg-slate-800/80 hover:bg-slate-700 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
            )}
            onClick={onToggleTheme}
          >
            <span className="text-lg" aria-hidden="true">
              {isDark ? "☀️" : "🌙"}
            </span>
            <span>{isDark ? "Light Mode" : "Dark Mode"}</span>
          </button>

          <button
            className={classNames(
              "w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50",
              isDark ? "bg-slate-800/80 hover:bg-slate-700 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
            )}
            onClick={() => {
              onClose();
              onOpenGroupEdit();
            }}
            disabled={!selectedGroupId}
          >
            <span className="text-lg" aria-hidden="true">
              ✎
            </span>
            <span>Edit Group Details</span>
          </button>

          <div className={classNames("h-px my-3 mx-2", isDark ? "bg-slate-800" : "bg-gray-200")} />

          <div className="grid grid-cols-2 gap-2">
            <button
              className={classNames(
                "w-full flex flex-col items-center justify-center gap-1 px-2 py-3 rounded-2xl text-sm font-medium transition-all min-h-[64px] disabled:opacity-50",
                isDark
                  ? "bg-emerald-900/30 border border-emerald-500/20 text-emerald-300 hover:bg-emerald-900/50"
                  : "bg-emerald-50 border border-emerald-100 text-emerald-700 hover:bg-emerald-100"
              )}
              onClick={() => {
                onClose();
                onStartGroup();
              }}
              disabled={!selectedGroupId || busy === "group-start" || actors.length === 0}
            >
              <span className="text-xl" aria-hidden="true">
                ▶
              </span>
              <span>Launch All</span>
            </button>

            <button
              className={classNames(
                "w-full flex flex-col items-center justify-center gap-1 px-2 py-3 rounded-2xl text-sm font-medium transition-all min-h-[64px] disabled:opacity-50",
                isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-300" : "bg-gray-100 hover:bg-gray-200 text-gray-600"
              )}
              onClick={() => {
                onClose();
                onStopGroup();
              }}
              disabled={!selectedGroupId || busy === "group-stop"}
            >
              <span className="text-xl" aria-hidden="true">
                ⏹
              </span>
              <span>Quit All</span>
            </button>
          </div>

          {groupDoc?.state === "paused" ? (
            <button
              className={classNames(
                "w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50",
                isDark
                  ? "bg-amber-900/30 border border-amber-500/20 text-amber-300 hover:bg-amber-900/50"
                  : "bg-amber-50 border border-amber-100 text-amber-700 hover:bg-amber-100"
              )}
              onClick={() => {
                onClose();
                void onSetGroupState("active");
              }}
              disabled={!selectedGroupId || busy === "group-state"}
            >
              <span className="text-lg" aria-hidden="true">
                ▶
              </span>
              <span>Resume Message Delivery</span>
            </button>
          ) : (
            <button
              className={classNames(
                "w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50",
                isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-300" : "bg-gray-100 hover:bg-gray-200 text-gray-600"
              )}
              onClick={() => {
                onClose();
                void onSetGroupState("paused");
              }}
              disabled={!selectedGroupId || busy === "group-state"}
            >
              <span className="text-lg" aria-hidden="true">
                ⏸
              </span>
              <span>Pause Message Delivery</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
