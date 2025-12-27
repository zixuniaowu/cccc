import { useRef, useEffect } from "react";
import { Actor, getRuntimeColor } from "../types";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

interface TabBarProps {
  actors: Actor[];
  activeTab: string; // "chat" or actor id
  onTabChange: (tab: string) => void;
  unreadChatCount: number;
  isDark: boolean;
}

export function TabBar({ actors, activeTab, onTabChange, unreadChatCount, isDark }: TabBarProps) {
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
      className={`flex items-center gap-1 px-3 py-2 border-b overflow-x-auto scrollbar-hide ${
        isDark ? "bg-slate-900/50 border-slate-800" : "bg-gray-50 border-gray-200"
      }`}
      style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
      role="tablist"
      aria-label="Navigation tabs"
    >
      {/* Chat Tab - Always first */}
      <button
        ref={activeTab === "chat" ? activeTabRef : null}
        onClick={() => onTabChange("chat")}
        className={classNames(
          "flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-all min-h-[44px]",
          activeTab === "chat"
            ? isDark ? "bg-slate-700 text-white" : "bg-white text-gray-900 shadow-sm border border-gray-200"
            : isDark ? "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"
        )}
        role="tab"
        aria-selected={activeTab === "chat"}
        aria-controls="chat-panel"
      >
        <span aria-hidden="true">💬</span>
        <span>Chat</span>
        {unreadChatCount > 0 && (
          <span className="bg-rose-500 text-white text-[10px] px-1.5 py-0.5 rounded-full font-medium min-w-[18px] text-center" aria-label={`${unreadChatCount} unread messages`}>
            {unreadChatCount > 99 ? "99+" : unreadChatCount}
          </span>
        )}
      </button>

      {/* Agent Tabs */}
      {actors.map((actor) => {
        const isActive = activeTab === actor.id;
        const isRunning = actor.running ?? actor.enabled ?? false;
        const color = getRuntimeColor(actor.runtime, isDark);
        
        return (
          <button
            key={actor.id}
            ref={isActive ? activeTabRef : null}
            onClick={() => onTabChange(actor.id)}
            className={classNames(
              "flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-all min-h-[44px]",
              isActive
                ? `${color.bg} ${color.text} ${color.border} border`
                : isDark ? "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"
            )}
            role="tab"
            aria-selected={isActive}
            aria-controls={`agent-panel-${actor.id}`}
          >
            {/* Status dot - unified colors for running state */}
            <span
              className={classNames(
                "w-2 h-2 rounded-full flex-shrink-0",
                isRunning
                  ? "bg-emerald-500"
                  : isDark ? "bg-slate-600" : "bg-gray-400"
              )}
              aria-hidden="true"
            />
            {/* Actor name */}
            <span className="truncate max-w-[120px]">{actor.id}</span>
            {/* Foreman badge */}
            {actor.role === "foreman" && (
              <span className={`text-[9px] px-1 py-0.5 rounded font-medium flex-shrink-0 ${
                isDark ? "bg-amber-900/50 text-amber-300" : "bg-amber-100 text-amber-700"
              }`}>
                foreman
              </span>
            )}
            {/* Unread count */}
            {(actor.unread_count ?? 0) > 0 && (
              <span 
                className="bg-rose-500 text-white text-[10px] px-1.5 py-0.5 rounded-full font-medium min-w-[18px] text-center flex-shrink-0"
                aria-label={`${actor.unread_count} unread messages`}
              >
                {(actor.unread_count ?? 0) > 99 ? "99+" : actor.unread_count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
