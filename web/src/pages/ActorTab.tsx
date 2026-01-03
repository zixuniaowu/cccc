import { lazy, Suspense } from "react";
import { Actor } from "../types";

const LazyAgentTab = lazy(() => import("../components/AgentTab").then((m) => ({ default: m.AgentTab })));

export interface ActorTabProps {
  actor: Actor | null;
  groupId: string;
  termEpoch: number;
  busy: string;
  isDark: boolean;
  onToggleEnabled: () => void;
  onRelaunch: () => void;
  onEdit: () => void;
  onRemove: () => void;
  onInbox: () => void;
  /** Called when actor status may have changed (e.g., process exited) */
  onStatusChange?: () => void;
}

export function ActorTab({
  actor,
  groupId,
  termEpoch,
  busy,
  isDark,
  onToggleEnabled,
  onRelaunch,
  onEdit,
  onRemove,
  onInbox,
  onStatusChange,
}: ActorTabProps) {
  if (!actor) {
    return <div className="flex-1 flex items-center justify-center text-slate-500">Agent not found</div>;
  }

  return (
    <Suspense
      fallback={<div className={`flex-1 flex items-center justify-center ${isDark ? "text-slate-400" : "text-gray-500"}`}>Loading agent…</div>}
    >
      <LazyAgentTab
        key={`${groupId}:${actor.id}:${termEpoch}`}
        actor={actor}
        groupId={groupId}
        isVisible={true}
        onQuit={onToggleEnabled}
        onLaunch={onToggleEnabled}
        onRelaunch={onRelaunch}
        onEdit={onEdit}
        onRemove={onRemove}
        onInbox={onInbox}
        busy={busy}
        isDark={isDark}
        onStatusChange={onStatusChange}
      />
    </Suspense>
  );
}

