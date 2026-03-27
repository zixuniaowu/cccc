import { describe, expect, it } from "vitest";
import { shouldProjectReminderForGroupState } from "./useWebPetNotifications";
import type { PetReminder } from "./types";

function makeReminder(overrides: Partial<PetReminder> = {}): PetReminder {
  return {
    id: "r1",
    kind: "suggestion",
    priority: 70,
    summary: "summary",
    agent: "pet-peer",
    source: {},
    fingerprint: "fp-1",
    action: {
      type: "send_suggestion",
      groupId: "g-1",
      text: "hello",
    },
    ...overrides,
  };
}

describe("shouldProjectReminderForGroupState", () => {
  it("keeps all reminders when group is active", () => {
    expect(shouldProjectReminderForGroupState(makeReminder(), "active")).toBe(true);
  });

  it("hides send_suggestion when group is idle", () => {
    expect(shouldProjectReminderForGroupState(makeReminder(), "idle")).toBe(false);
  });

  it("keeps restart_actor when group is idle", () => {
    expect(
      shouldProjectReminderForGroupState(
        makeReminder({
          kind: "actor_down",
          action: { type: "restart_actor", groupId: "g-1", actorId: "peer-1" },
        }),
        "idle",
      ),
    ).toBe(true);
  });

  it("keeps automation_proposal when group is paused", () => {
    expect(
      shouldProjectReminderForGroupState(
        makeReminder({
          action: {
            type: "automation_proposal",
            groupId: "g-1",
            title: "Apply follow-up",
            actions: [{ type: "create_rule" }],
          },
        }),
        "paused",
      ),
    ).toBe(true);
  });
});
