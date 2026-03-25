import { describe, expect, it } from "vitest";
import { shouldSurfaceReminder } from "./useWebPetData";
import type { PetReminder } from "./types";
import type { PetPersonaPolicy } from "./petPersona";

function makePolicy(overrides: Partial<PetPersonaPolicy> = {}): PetPersonaPolicy {
  return {
    compactMessageEvents: true,
    autoRestartActors: true,
    autoCompleteTasks: true,
    ...overrides,
  };
}

function makeReminder(overrides: Partial<PetReminder> = {}): PetReminder {
  return {
    id: "mention:evt-1",
    kind: "mention",
    priority: 70,
    summary: "peer 提到了你，需要你查看。",
    agent: "peer",
    source: { eventId: "evt-1" },
    fingerprint: "group:g-1:mention:evt-1",
    action: {
      type: "open_chat",
      groupId: "g-1",
      eventId: "evt-1",
    },
    ...overrides,
  };
}

describe("shouldSurfaceReminder", () => {
  it("hides generic open_chat mention reminders for low-noise persona", () => {
    expect(shouldSurfaceReminder(makeReminder(), makePolicy())).toBe(false);
  });

  it("keeps actionable send_suggestion reminders for low-noise persona", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          action: {
            type: "send_suggestion",
            groupId: "g-1",
            text: "我来处理这条。",
            to: ["peer"],
            replyTo: "evt-1",
          },
        }),
        makePolicy(),
      ),
    ).toBe(true);
  });

  it("keeps restart_actor reminders for low-noise persona", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          id: "actor_down:peer-1",
          kind: "actor_down",
          action: {
            type: "restart_actor",
            groupId: "g-1",
            actorId: "peer-1",
          },
        }),
        makePolicy(),
      ),
    ).toBe(true);
  });

  it("hides non-suggestion reminders even for low-noise persona", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          id: "waiting_user:T1",
          kind: "waiting_user",
          action: {
            type: "open_task",
            groupId: "g-1",
            taskId: "T1",
          },
        }),
        makePolicy(),
      ),
    ).toBe(false);
  });

  it("still hides generic open_chat reminders when persona is not low-noise", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder(),
        makePolicy({ compactMessageEvents: false }),
      ),
    ).toBe(false);
  });
});
