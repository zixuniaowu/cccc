import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useUIStore } from "../../stores";
import type { Actor, GroupContext, GroupDoc, LedgerEvent } from "../../types";
import { aggregateWebPetState } from "./aggregateWebPetState";
import { useWebPetNotifications } from "./useWebPetNotifications";
import type { PetPersonaPolicy } from "./petPersona";
import type { PetPeerContext } from "./petPeerContext";
import type { PanelData, PetReminder } from "./types";
import { buildCompactMessageSummary } from "./messageSummary";
import { getPetSnapshotHeadline } from "./petSnapshotText";

export function shouldSurfaceReminder(
  reminder: PetReminder,
  policy: PetPersonaPolicy,
): boolean {
  void policy;
  if (reminder.action.type === "restart_actor") {
    return !!reminder.action.groupId && !!reminder.action.actorId;
  }
  return reminder.action.type === "send_suggestion" &&
    (!!reminder.suggestion?.trim() || !!reminder.action.text?.trim());
}

function localizeConnectionMessage(
  sseStatus: "connected" | "connecting" | "disconnected",
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): string {
  if (sseStatus === "connected") {
    return tr("connection.connected", "Connected");
  }
  if (sseStatus === "connecting") {
    return tr("connection.connecting", "Connecting…");
  }
  return tr("connection.disconnected", "Disconnected");
}

function localizeReminder(
  reminder: PetReminder,
  policy: PetPersonaPolicy,
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): PetReminder {
  const agentLabel =
    reminder.agent === "system"
      ? tr("systemAgent", "System")
      : reminder.agent;

  let summary = reminder.summary;
  if (reminder.kind === "actor_down") {
    if (reminder.source.taskId) {
      summary = tr(
        "reminderSummary.actorDownTask",
        "{{actor}} is stopped. Task {{taskId}} may be stuck; restart it.",
        {
          actor: agentLabel,
          taskId: reminder.source.taskId,
        },
      );
    } else {
      summary = tr(
        "reminderSummary.actorDownForeman",
        "{{actor}} is stopped. Restart to restore coordination.",
        {
          actor: agentLabel,
        },
      );
    }
  } else if (reminder.kind === "stalled_peer") {
    summary = tr(
      "reminderSummary.stalledPeer",
      "{{actor}} has been idle for a while on {{taskId}}.",
      {
        actor: reminder.source.actorId || agentLabel,
        taskId: reminder.source.taskId || tr("taskFallback", "this task"),
      },
    );
  } else if (policy.compactMessageEvents && (reminder.kind === "mention" || reminder.kind === "reply_required")) {
    summary = buildCompactMessageSummary(reminder.kind, agentLabel, tr);
  } else if (!summary.trim()) {
    if (reminder.kind === "mention") {
      summary = tr(
        "reminderSummary.mention",
        "{{actor}} mentioned you.",
        { actor: agentLabel },
      );
    } else if (reminder.kind === "reply_required") {
      summary = tr(
        "reminderSummary.replyRequired",
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
      tr("teamFallback", "Team"),
    actionItems: panelData.actionItems.map((item) => ({
      ...item,
      agent:
        item.agent === "system"
          ? tr("systemAgent", "System")
          : item.agent,
    })),
    connection: {
      ...panelData.connection,
      message: localizeConnectionMessage(sseStatus, tr),
    },
  };
}

export function useWebPetData(input: {
  groupId: string;
  groupDoc: GroupDoc | null;
  groupContext: GroupContext | null;
  actors: Actor[];
  events: LedgerEvent[];
  petContext: PetPeerContext;
}) {
  const { t } = useTranslation("webPet");
  const groupId = String(input.groupId || "").trim();
  const groupContext = input.groupContext;
  const groupDocTitle = input.groupDoc?.title ?? "";
  const groupState = input.groupDoc?.state ?? "";
  const actors = input.actors;
  const events = input.events;
  const sseStatus = useUIStore((state) => state.sseStatus);
  const { reminders, activeReminder, reaction, dismissReminder } =
    useWebPetNotifications({ groupId, groupState, groupContext, actors, events });
  const petContext = input.petContext;
  const personaPolicy = petContext.policy;
  const tr = useCallback(
    (key: string, fallback: string, vars?: Record<string, unknown>) =>
      String(t(key, { defaultValue: fallback, ...(vars || {}) })),
    [t]
  );

  return useMemo(() => {
    const { catState, panelData: rawPanelData } = aggregateWebPetState({
      groupContext,
      events,
      sseStatus,
      groupState,
      teamName: groupDocTitle || groupId || "",
      groupId: groupId || "",
    });
    const localizedPanelData = localizePanelData(rawPanelData, sseStatus, tr);
    const localizedReminders = reminders.map((reminder) =>
      localizeReminder(reminder, personaPolicy, tr),
    );
    const filteredReminders = localizedReminders.filter((reminder) =>
      shouldSurfaceReminder(reminder, personaPolicy),
    );
    const localizedActiveReminder = activeReminder
      ? localizeReminder(activeReminder, personaPolicy, tr)
      : null;
    const filteredActiveReminder =
      localizedActiveReminder && shouldSurfaceReminder(localizedActiveReminder, personaPolicy)
        ? localizedActiveReminder
        : filteredReminders[0] || null;

    // Smart hint: prioritize connection > active reminder > task progress > agent focus > team name
    let hint: string;
    if (!localizedPanelData.connection.connected) {
      hint = localizedPanelData.connection.message;
    } else if (filteredActiveReminder?.summary) {
      hint = filteredActiveReminder.summary;
    } else if (
      localizedPanelData.taskProgress &&
      localizedPanelData.taskProgress.total > 0
    ) {
      const { done, total } = localizedPanelData.taskProgress;
      const needsYouCount = localizedPanelData.actionItems.length;
      hint = needsYouCount > 0
        ? tr(
            "hintTaskWithAction",
            "{{done}}/{{total}} done, {{count}} need you",
            { done, total, count: needsYouCount },
          )
        : tr(
            "hintTaskProgress",
            "{{done}}/{{total}} tasks done",
            { done, total },
          );
    } else {
      hint =
        localizedPanelData.actionItems[0]?.summary ||
        localizedPanelData.agents.find((agent) => agent.focus.trim())?.focus ||
        getPetSnapshotHeadline(petContext.snapshot, tr) ||
        localizedPanelData.teamName;
    }

    return {
      catState,
      panelData: localizedPanelData,
      petContext,
      hint,
      reminders: filteredReminders,
      activeReminder: filteredActiveReminder,
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
    groupId,
    sseStatus,
    tr,
    personaPolicy,
    petContext,
  ]);
}
