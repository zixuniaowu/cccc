import type { PetReminder, ReminderAction } from "./types";

export interface ReminderActionButton {
  labelKey: "send" | "restart";
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
    return [
      {
        labelKey: "restart",
        fallback: "Restart",
        action: reminder.action,
      },
    ];
  }

  return [];
}
