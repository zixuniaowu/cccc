import { shouldProjectReminderForGroupState } from "./useWebPetNotifications";
import { shouldSurfaceReminder } from "./useWebPetData";
import type { PetReminder } from "./types";

export function isManualReviewReminderReady(reminder: PetReminder, groupState: string): boolean {
  return shouldSurfaceReminder(reminder) && shouldProjectReminderForGroupState(reminder, groupState);
}
