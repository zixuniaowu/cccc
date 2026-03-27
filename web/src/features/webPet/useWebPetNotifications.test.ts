import { describe, expect, it } from "vitest";
import type { LedgerEvent } from "../../types";
import {
  shouldProjectReminderForGroupState,
  shouldSuppressTaskProposalEcho,
} from "./useWebPetNotifications";
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

describe("shouldSuppressTaskProposalEcho", () => {
  it("suppresses task proposal when user just sent the same task request to foreman", () => {
    const reminder = makeReminder({
      id: "task-proposal:T192",
      summary: "先把 T192 的解阻路径定下来。",
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        title: "M1-2: Bridge 出站链路 stream_emit daemon op",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T16:00:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: 'Pet task proposal: please use cccc_task to update this task (task_id=T192, title="M1-2: Bridge 出站链路 stream_emit daemon op").',
          to: ["@foreman"],
        },
      },
    ];

    expect(
      shouldSuppressTaskProposalEcho(
        reminder,
        events,
        Date.parse("2026-03-27T16:03:00.000Z"),
      ),
    ).toBe(true);
  });

  it("keeps task proposal when user is only discussing the same task with foreman", () => {
    const reminder = makeReminder({
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        title: "M1-2: Bridge 出站链路 stream_emit daemon op",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T16:00:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: "先协调 T192 的解阻路径：确认 M1-2: Bridge 出站链路 stream_emit daemon op 还缺什么运行态样本。",
          to: ["@foreman"],
        },
      },
    ];

    expect(
      shouldSuppressTaskProposalEcho(
        reminder,
        events,
        Date.parse("2026-03-27T16:03:00.000Z"),
      ),
    ).toBe(false);
  });

  it("keeps task proposal when recent user message targets someone else", () => {
    const reminder = makeReminder({
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        title: "M1-2: Bridge 出站链路 stream_emit daemon op",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T16:00:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: "先协调 T192 的解阻路径。",
          to: ["peer-reviewer"],
        },
      },
    ];

    expect(
      shouldSuppressTaskProposalEcho(
        reminder,
        events,
        Date.parse("2026-03-27T16:03:00.000Z"),
      ),
    ).toBe(false);
  });

  it("keeps task proposal when matching user message is too old", () => {
    const reminder = makeReminder({
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        title: "M1-2: Bridge 出站链路 stream_emit daemon op",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T15:40:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: "先协调 T192 的解阻路径。",
          to: ["@foreman"],
        },
      },
    ];

    expect(
      shouldSuppressTaskProposalEcho(
        reminder,
        events,
        Date.parse("2026-03-27T16:03:00.000Z"),
      ),
    ).toBe(false);
  });
});
