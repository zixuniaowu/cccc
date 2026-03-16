import { useMemo } from "react";
import { selectChatBucketState, useGroupStore, useUIStore } from "../../stores";
import { aggregateWebPetState } from "./aggregateWebPetState";
import { useWebPetNotifications } from "./useWebPetNotifications";

export function useWebPetData() {
  const selectedGroupId = useGroupStore((state) => state.selectedGroupId);
  const groupContext = useGroupStore((state) => state.groupContext);
  const groupDocTitle = useGroupStore((state) => state.groupDoc?.title ?? "");
  const groupState = useGroupStore((state) => state.groupDoc?.state ?? "");
  const events = useGroupStore((state) => selectChatBucketState(state, state.selectedGroupId).events);
  const sseStatus = useUIStore((state) => state.sseStatus);
  const { reminders, activeReminder, reaction, dismissReminder } =
    useWebPetNotifications();

  return useMemo(() => {
    const { catState, panelData } = aggregateWebPetState({
      groupContext,
      events,
      sseStatus,
      groupState,
      teamName: groupDocTitle || selectedGroupId || "Team",
    });

    const hint =
      !panelData.connection.connected
        ? panelData.connection.message
        : activeReminder?.summary ||
          panelData.actionItems[0]?.summary ||
          panelData.agents.find((agent) => agent.focus.trim())?.focus ||
          panelData.teamName;

    return {
      catState,
      panelData,
      hint,
      reminders,
      activeReminder,
      reaction,
      dismissReminder,
    };
  }, [
    activeReminder,
    dismissReminder,
    events,
    groupContext,
    groupDocTitle,
    groupState,
    reminders,
    reaction,
    selectedGroupId,
    sseStatus,
  ]);
}
