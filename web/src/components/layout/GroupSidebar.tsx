import { GroupMeta } from "../../types";
import { getGroupStatus, getGroupStatusLight } from "../../utils/groupStatus";
import { classNames } from "../../utils/classNames";

export interface GroupSidebarProps {
  groups: GroupMeta[];
  selectedGroupId: string;
  isOpen: boolean;
  isDark: boolean;
  onSelectGroup: (groupId: string) => void;
  onCreateGroup: () => void;
  onClose: () => void;
}

export function GroupSidebar({
  groups,
  selectedGroupId,
  isOpen,
  isDark,
  onSelectGroup,
  onCreateGroup,
  onClose,
}: GroupSidebarProps) {
  return (
    <>
      <aside
        className={classNames(
          "h-full border-r flex flex-col",
          "fixed md:relative z-40 w-[280px] transition-transform duration-300",
          isOpen ? "translate-x-0" : "-translate-x-full",
          "md:translate-x-0",
          isDark ? "border-slate-700/50 bg-slate-900/80 backdrop-blur" : "border-gray-200 bg-white/80 backdrop-blur"
        )}
      >
        <div className={`p-4 border-b ${isDark ? "border-slate-700/50" : "border-gray-200"}`}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <img src="/ui/logo.svg" alt="CCCC Logo" className="w-8 h-8 object-contain" />
              <span className={`text-lg font-bold tracking-tight ${isDark ? "text-white" : "text-gray-900"}`}>CCCC</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                className={`text-xs px-3 py-1.5 rounded-xl font-medium shadow-lg transition-all min-h-[36px] ${
                  isDark
                    ? "bg-gradient-to-r from-blue-600 to-blue-500 text-white hover:from-blue-500 hover:to-blue-400 shadow-blue-500/20"
                    : "bg-blue-600 text-white hover:bg-blue-500"
                }`}
                onClick={onCreateGroup}
                title="Create new working group"
                aria-label="Create new working group"
              >
                + New
              </button>
              <button
                className={`md:hidden p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-xl transition-colors ${
                  isDark ? "text-slate-400 hover:text-white hover:bg-slate-800" : "text-gray-500 hover:text-gray-900 hover:bg-gray-100"
                }`}
                onClick={onClose}
                aria-label="Close sidebar"
              >
                ✕
              </button>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-3">
          <div className={`text-[10px] font-medium uppercase tracking-wider mb-2 px-2 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
            Working Groups
          </div>
          {groups.map((g) => {
            const gid = String(g.group_id || "");
            const active = gid === selectedGroupId;
            return (
              <button
                key={gid}
                className={classNames(
                  "w-full text-left px-3 py-2.5 rounded-xl mb-1 transition-all min-h-[44px]",
                  active
                    ? isDark
                      ? "bg-gradient-to-r from-blue-600/20 to-blue-500/10 border border-blue-500/30"
                      : "bg-blue-50 border border-blue-200"
                    : isDark
                      ? "hover:bg-slate-800/50 border border-transparent"
                      : "hover:bg-gray-100 border border-transparent"
                )}
                onClick={() => {
                  onSelectGroup(gid);
                  // Close sidebar after selection on mobile/tablet (Tailwind `md` breakpoint).
                  if (window.matchMedia("(max-width: 767px)").matches) onClose();
                }}
              >
                <div className="flex items-center justify-between">
                  <div
                    className={classNames(
                      "text-sm font-medium truncate",
                      active ? (isDark ? "text-white" : "text-blue-700") : isDark ? "text-slate-300" : "text-gray-700"
                    )}
                  >
                    {g.title || gid}
                  </div>
                  {(() => {
                    const status = isDark ? getGroupStatus(g.running ?? false, g.state) : getGroupStatusLight(g.running ?? false, g.state);
                    return (
                      <div className={classNames("text-[9px] px-2 py-0.5 rounded-full font-medium", status.colorClass)}>{status.label}</div>
                    );
                  })()}
                </div>
              </button>
            );
          })}
          {!groups.length && (
            <div className="p-6 text-center">
              <div className="text-4xl mb-3">📁</div>
              <div className={`text-sm mb-2 ${isDark ? "text-slate-400" : "text-gray-600"}`}>No working groups yet</div>
              <div className={`text-xs mb-4 max-w-[200px] mx-auto ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                A working group is a collaboration space where multiple AI agents work together on a project.
              </div>
              <button
                className={`text-sm px-4 py-2 rounded-xl font-medium shadow-lg min-h-[44px] transition-all ${
                  isDark
                    ? "bg-gradient-to-r from-blue-600 to-blue-500 text-white hover:from-blue-500 hover:to-blue-400 shadow-blue-500/20"
                    : "bg-blue-600 text-white hover:bg-blue-500"
                }`}
                onClick={onCreateGroup}
              >
                Create Your First Group
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Sidebar overlay for mobile */}
      {isOpen && (
        <div
          className={`fixed inset-0 z-30 md:hidden ${isDark ? "bg-black/50" : "bg-black/30"}`}
          onClick={onClose}
          aria-hidden="true"
        />
      )}
    </>
  );
}
