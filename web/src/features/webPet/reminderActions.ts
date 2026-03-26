import type { PetReminder, ReminderAction } from "./types";

export interface ReminderActionButton {
  labelKey: "send" | "restart" | "restartPeer" | "restartForeman";
  fallback: string;
  action: ReminderAction;
}

export function getReminderActionButtons(
  reminder: PetReminder,
): ReminderActionButton[] {
  if (reminder.action.type === "send_suggestion" && reminder.suggestion?.trim()) {
    return [
      {
        labelKey: "send",
        fallback: "Send",
        action: reminder.action,
      },
    ];
  }

  if (reminder.action.type === "restart_actor") {
    const actorRole = String(reminder.source.actorRole || "").trim().toLowerCase();
    return [
      {
        labelKey: actorRole === "foreman" ? "restartForeman" : actorRole ? "restartPeer" : "restart",
        fallback:
          actorRole === "foreman"
            ? "Restart foreman"
            : actorRole
              ? "Restart peer"
              : "Restart",
        action: reminder.action,
      },
    ];
  }

  return [];
}
