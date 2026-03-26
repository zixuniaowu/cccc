import { describe, expect, it } from "vitest";
import { shouldSurfaceReminder } from "./useWebPetData";
import type { PetReminder } from "./types";
import type { PetPersonaPolicy } from "./petPersona";

function makePolicy(overrides: Partial<PetPersonaPolicy> = {}): PetPersonaPolicy {
  return {
    compactMessageEvents: true,
    ...overrides,
  };
}

function makeReminder(overrides: Partial<PetReminder> = {}): PetReminder {
  return {
    id: "mention:evt-1",
    kind: "suggestion",
    priority: 70,
    summary: "peer 给了一个可直接发送的建议。",
    agent: "peer",
    source: { eventId: "evt-1", suggestionKind: "mention" },
    fingerprint: "group:g-1:suggestion:mention:evt-1",
    action: {
      type: "send_suggestion",
      groupId: "g-1",
      text: "我来处理这条。",
      to: ["peer"],
      replyTo: "evt-1",
    },
    ...overrides,
  };
}

describe("shouldSurfaceReminder", () => {
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

  it("hides malformed send_suggestion reminders", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          action: {
            type: "send_suggestion",
            groupId: "g-1",
            text: "",
          },
        }),
        makePolicy(),
      ),
    ).toBe(false);
  });
});
