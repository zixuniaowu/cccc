import { useRef, useEffect } from "react";
import { Actor, getRuntimeColor } from "../types";
import { classNames } from "../utils/classNames";

interface TabBarProps {
  actors: Actor[];
  activeTab: string; // "chat" or actor id
  onTabChange: (tab: string) => void;
  unreadChatCount: number;
  isDark: boolean;
  onAddAgent?: () => void;
  canAddAgent?: boolean;
}

export function TabBar({ actors, activeTab, onTabChange, unreadChatCount, isDark, onAddAgent, canAddAgent = true }: TabBarProps) {
  const tabBarRef = useRef<HTMLDivElement>(null);
  const activeTabRef = useRef<HTMLButtonElement>(null);

  // Auto-scroll active tab into view
  useEffect(() => {
    if (activeTabRef.current && tabBarRef.current) {
      const container = tabBarRef.current;
      const tab = activeTabRef.current;
      const containerRect = container.getBoundingClientRect();
      const tabRect = tab.getBoundingClientRect();

      if (tabRect.left < containerRect.left) {
        container.scrollLeft -= containerRect.left - tabRect.left + 16;
      } else if (tabRect.right > containerRect.right) {
        container.scrollLeft += tabRect.right - containerRect.right + 16;
      }
    }
  }, [activeTab]);

  return (
    <div
      ref={tabBarRef}
      className={`flex items-center gap-2 px-4 border-b overflow-x-auto ${isDark ? "bg-slate-900 border-slate-800" : "bg-white border-gray-100" // Cleaner background
        }`}
      style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
      role="tablist"
      aria-label="Navigation tabs"
    >
      {/* Chat Tab */}
      <button
        ref={activeTab === "chat" ? activeTabRef : null}
        onClick={() => onTabChange("chat")}
        className={classNames(
          "relative flex items-center gap-2 px-3 py-3 text-sm font-medium whitespace-nowrap transition-colors",
          activeTab === "chat"
            ? isDark ? "text-white" : "text-gray-900"
            : isDark ? "text-slate-500 hover:text-slate-300" : "text-gray-500 hover:text-gray-700"
        )}
        role="tab"
        aria-selected={activeTab === "chat"}
      >
        <span>Chat</span>
        {unreadChatCount > 0 && (
          <span className="bg-blue-600 text-white text-[10px] px-1.5 py-0.5 rounded-full font-bold">
            {unreadChatCount}
          </span>
        )}
        {/* Active Line Indicator */}
        {activeTab === "chat" && (
          <span className={`absolute bottom-0 left-0 right-0 h-0.5 rounded-t-full ${isDark ? "bg-blue-500" : "bg-blue-600"
            }`} />
        )}
      </button>

      {/* Separator */}
      <div className={`w-px h-4 flex-shrink-0 ${isDark ? "bg-slate-800" : "bg-gray-200"}`} />

      {/* Agent Tabs */}
      {actors.map((actor) => {
        const isActive = activeTab === actor.id;
        const isRunning = actor.running ?? actor.enabled ?? false;

        return (
          <button
            key={actor.id}
            ref={isActive ? activeTabRef : null}
            onClick={() => onTabChange(actor.id)}
            className={classNames(
              "relative flex items-center gap-2 px-3 py-3 text-sm font-medium whitespace-nowrap transition-colors",
              isActive
                ? isDark ? "text-white" : "text-gray-900"
                : isDark ? "text-slate-500 hover:text-slate-300" : "text-gray-500 hover:text-gray-700"
            )}
            role="tab"
            aria-selected={isActive}
          >
            {/* Run Indicator */}
            <span
              className={classNames(
                "w-1.5 h-1.5 rounded-full",
                isRunning ? "bg-emerald-500" : isDark ? "bg-slate-700" : "bg-gray-300"
              )}
            />

            <span>{actor.id}</span>

            {actor.role === "foreman" && (
              <span className={`text-[9px] px-1 py-0.5 rounded ${isDark ? "bg-amber-900/30 text-amber-500" : "bg-amber-50 text-amber-600"
                }`}>
                F
              </span>
            )}

            {(actor.unread_count ?? 0) > 0 && (
              <span className="bg-rose-500 text-white text-[10px] px-1.5 py-0.5 rounded-full font-bold">
                {actor.unread_count}
              </span>
            )}

            {isActive && (
              <span className={`absolute bottom-0 left-0 right-0 h-0.5 rounded-t-full ${isDark ? "bg-blue-500" : "bg-blue-600"
                }`} />
            )}
          </button>
        );
      })}

      {onAddAgent && (
        <button
          onClick={onAddAgent}
          disabled={!canAddAgent}
          className={classNames(
            "ml-2 flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors disabled:opacity-30 whitespace-nowrap",
            actors.length === 0
              ? "bg-blue-600 text-white border-blue-500 hover:bg-blue-500"
              : isDark
                ? "bg-slate-900 border-slate-700 text-slate-300 hover:bg-slate-800"
                : "bg-white border-gray-200 text-gray-700 hover:bg-gray-50"
          )}
          title={actors.length === 0 ? "Add your first agent (foreman)" : "Add agent"}
          aria-label="Add agent"
        >
          <span className="text-base leading-none">+</span>
          <span className="hidden sm:inline">Add</span>
        </button>
      )}
    </div>
  );
}
