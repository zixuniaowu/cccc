import { describe, expect, it } from "vitest";
import { createMentionReminder, projectPetReminders, type ProjectPetRemindersInput } from "./reminders";

describe("projectPetReminders", () => {
  it("does not emit idle-only peer reminders", () => {
    const input: ProjectPetRemindersInput = {
      groupId: "g-1",
      actors: [
        {
          actorId: "peer-1",
          running: true,
          idleSeconds: 3600,
          activeTaskId: "T288",
        },
      ],
      events: [],
    };

    expect(projectPetReminders(input)).toEqual([]);
  });

  it("does not emit waiting_user reminders", () => {
    const input: ProjectPetRemindersInput = {
      groupId: "g-1",
      actors: [],
      events: [],
    };

    expect(projectPetReminders(input)).toEqual([]);
  });

  it("emits reply_required reminders from chat messages", () => {
    const input: ProjectPetRemindersInput = {
      groupId: "g-1",
      actors: [],
      events: [
        {
          eventId: "evt-1",
          kind: "chat.message",
          by: "foreman-1",
          text: "请先回一下用户这条消息",
          to: ["@user"],
          replyRequired: true,
          acked: false,
          replied: false,
        },
      ],
    };

    expect(projectPetReminders(input)).toEqual([
      expect.objectContaining({
        kind: "reply_required",
        summary: "",
        suggestion: "请先回一下用户这条消息",
        action: {
          type: "send_suggestion",
          groupId: "g-1",
          text: "请先回一下用户这条消息",
          to: ["foreman-1"],
          replyTo: "evt-1",
        },
      }),
    ]);
  });

  it("emits mention reminders from chat messages", () => {
    const input: ProjectPetRemindersInput = {
      groupId: "g-1",
      actors: [],
      events: [
        {
          eventId: "evt-mention",
          kind: "chat.message",
          by: "peer-2",
          text: "请先确认这个任务该怎么推进",
          to: ["@user"],
          replyRequired: false,
          acked: false,
          replied: false,
        },
      ],
    };

    expect(projectPetReminders(input)).toEqual([
      expect.objectContaining({
        kind: "mention",
        summary: "",
        suggestion: "请先确认这个任务该怎么推进",
        action: {
          type: "send_suggestion",
          groupId: "g-1",
          text: "请先确认这个任务该怎么推进",
          to: ["peer-2"],
          replyTo: "evt-mention",
        },
      }),
    ]);
  });

  it("creates ephemeral mention reminders from chat messages", () => {
    expect(
      createMentionReminder("g-1", {
        eventId: "evt-mention",
        kind: "chat.message",
        by: "peer-2",
        text: "请先确认这个任务该怎么推进",
        to: ["@user"],
        replyRequired: false,
        acked: false,
        replied: false,
      }),
    ).toEqual(
      expect.objectContaining({
        kind: "mention",
        ephemeral: true,
        fingerprint: "group:g-1:mention:evt-mention",
      }),
    );
  });

  it("drops internal control mentions instead of surfacing view-only reminders", () => {
    expect(
      createMentionReminder("g-1", {
        eventId: "evt-help",
        kind: "chat.message",
        by: "peer-2",
        text: "已按 `help_nudge` 刷新 `cccc_help` 并更新 `agent_state`。",
        to: ["@user"],
        replyRequired: false,
        acked: false,
        replied: false,
      }),
    ).toBeNull();
  });

  it("ignores low-signal status messages for reminders", () => {
    const input: ProjectPetRemindersInput = {
      groupId: "g-1",
      actors: [],
      events: [
        {
          eventId: "evt-done",
          kind: "chat.message",
          by: "peer-2",
          text: "已完成",
          to: ["@user"],
          replyRequired: false,
          acked: false,
          replied: false,
        },
      ],
    };

    expect(projectPetReminders(input)).toEqual([]);
  });

  it("drops internal control reply-required messages", () => {
    const input: ProjectPetRemindersInput = {
      groupId: "g-1",
      actors: [],
      events: [
        {
          eventId: "evt-reply-help",
          kind: "chat.message",
          by: "foreman-1",
          text: "Run `cccc_help` now, then refresh `cccc_agent_state`.",
          to: ["@user"],
          replyRequired: true,
          acked: false,
          replied: false,
        },
      ],
    };

    expect(projectPetReminders(input)).toEqual([]);
  });

  it("understands Japanese actionable mentions", () => {
    const reminder = createMentionReminder("g-1", {
      eventId: "evt-ja",
      kind: "chat.message",
      by: "peer-2",
      text: "この内容を確認してください",
      to: ["@user"],
      replyRequired: false,
      acked: false,
      replied: false,
    });

    expect(reminder).toEqual(
      expect.objectContaining({
        kind: "mention",
        suggestion: "この内容を確認してください",
      }),
    );
  });

  it("drops Japanese sync-only chatter", () => {
    const reminder = createMentionReminder("g-1", {
      eventId: "evt-ja-sync",
      kind: "chat.message",
      by: "peer-2",
      text: "差分なし、共有のみです。返信不要。",
      to: ["@user"],
      replyRequired: false,
      acked: false,
      replied: false,
    });

    expect(reminder).toBeNull();
  });
});
