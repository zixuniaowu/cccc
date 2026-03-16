import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { selectChatBucketState, useGroupStore, useUIStore } from "../../stores";
import { aggregateWebPetState } from "./aggregateWebPetState";
import { useWebPetNotifications } from "./useWebPetNotifications";
import type { PanelData, PetReminder } from "./types";

function localizeConnectionMessage(
  sseStatus: "connected" | "connecting" | "disconnected",
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): string {
  if (sseStatus === "connected") {
    return tr("webPet.connection.connected", "Connected");
  }
  if (sseStatus === "connecting") {
    return tr("webPet.connection.connecting", "Connecting…");
  }
  return tr("webPet.connection.disconnected", "Disconnected");
}

function localizeReminder(
  reminder: PetReminder,
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): PetReminder {
  const agentLabel =
    reminder.agent === "system"
      ? tr("webPet.systemAgent", "System")
      : reminder.agent;

  let summary = reminder.summary;
  if (reminder.kind === "stalled_peer") {
    summary = tr(
      "webPet.reminderSummary.stalledPeer",
      "{{actor}} has been idle for a while on {{taskId}}.",
      {
        actor: reminder.source.actorId || agentLabel,
        taskId: reminder.source.taskId || tr("webPet.taskFallback", "this task"),
      },
    );
  } else if (!summary.trim()) {
    if (reminder.kind === "mention") {
      summary = tr(
        "webPet.reminderSummary.mention",
        "{{actor}} mentioned you.",
        { actor: agentLabel },
      );
    } else if (reminder.kind === "reply_required") {
      summary = tr(
        "webPet.reminderSummary.replyRequired",
        "{{actor}} is waiting for your reply.",
        { actor: agentLabel },
      );
    }
  }

  return {
    ...reminder,
    summary,
  };
}

function localizePanelData(
  panelData: PanelData,
  sseStatus: "connected" | "connecting" | "disconnected",
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): PanelData {
  return {
    ...panelData,
    teamName:
      panelData.teamName.trim() ||
      tr("webPet.teamFallback", "Team"),
    actionItems: panelData.actionItems.map((item) => ({
      ...item,
      agent:
        item.agent === "system"
          ? tr("webPet.systemAgent", "System")
          : item.agent,
    })),
    connection: {
      ...panelData.connection,
      message: localizeConnectionMessage(sseStatus, tr),
    },
  };
}

export function useWebPetData() {
  const { t } = useTranslation("modals");
  const selectedGroupId = useGroupStore((state) => state.selectedGroupId);
  const groupContext = useGroupStore((state) => state.groupContext);
  const groupDocTitle = useGroupStore((state) => state.groupDoc?.title ?? "");
  const groupState = useGroupStore((state) => state.groupDoc?.state ?? "");
  const events = useGroupStore((state) => selectChatBucketState(state, state.selectedGroupId).events);
  const sseStatus = useUIStore((state) => state.sseStatus);
  const { reminders, activeReminder, reaction, dismissReminder } =
    useWebPetNotifications();
  const tr = (key: string, fallback: string, vars?: Record<string, unknown>) =>
    String(t(key as never, { defaultValue: fallback, ...(vars || {}) } as never));

  return useMemo(() => {
    const { catState, panelData: rawPanelData } = aggregateWebPetState({
      groupContext,
      events,
      sseStatus,
      groupState,
      teamName: groupDocTitle || selectedGroupId || "",
    });
    const localizedPanelData = localizePanelData(rawPanelData, sseStatus, tr);
    const localizedReminders = reminders.map((reminder) =>
      localizeReminder(reminder, tr),
    );
    const localizedActiveReminder = activeReminder
      ? localizeReminder(activeReminder, tr)
      : null;

    const hint =
      !localizedPanelData.connection.connected
        ? localizedPanelData.connection.message
        : localizedActiveReminder?.summary ||
          localizedPanelData.actionItems[0]?.summary ||
          localizedPanelData.agents.find((agent) => agent.focus.trim())?.focus ||
          localizedPanelData.teamName;

    return {
      catState,
      panelData: localizedPanelData,
      hint,
      reminders: localizedReminders,
      activeReminder: localizedActiveReminder,
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
    tr,
  ]);
}
