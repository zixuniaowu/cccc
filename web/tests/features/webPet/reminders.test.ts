import { describe, expect, it } from "vitest";

import {
  createMentionReminder,
  projectPetReminders,
  type ProjectPetRemindersInput,
  type ReminderActorInput,
  type ReminderEventInput,
} from "../../../src/features/webPet/reminders";

function makeInput(overrides: Partial<ProjectPetRemindersInput> = {}): ProjectPetRemindersInput {
  return {
    groupId: "g-demo",
    waitingUser: [],
    tasks: [],
    actors: [],
    events: [],
    ...overrides,
  };
}

function makeActor(actorId: string, overrides: Partial<ReminderActorInput> = {}): ReminderActorInput {
  return {
    actorId,
    running: false,
    idleSeconds: 0,
    ...overrides,
  };
}

function makeChatEvent(eventId: string, overrides: Partial<ReminderEventInput> = {}): ReminderEventInput {
  return {
    eventId,
    kind: "chat.message",
    by: "peer-reviewer",
    text: `请先处理 message:${eventId}`,
    to: [],
    replyRequired: false,
    acked: false,
    replied: false,
    ...overrides,
  };
}

describe("projectPetReminders", () => {
  it("does not project waiting_user from attention entries", () => {
    const reminders = projectPetReminders(
      makeInput({
        waitingUser: [{ taskId: "T249", label: "Need user confirmation" }],
        tasks: [{ taskId: "T249", title: "Need user confirmation" }],
      }),
    );

    expect(reminders).toEqual([]);
  });

  it("does not fall back to coordination tasks for waiting_user", () => {
    const reminders = projectPetReminders(
      makeInput({
        tasks: [
          { taskId: "T250", title: "Wait for approval", waitingOn: "user", status: "active" },
          { taskId: "T251", title: "Done already", waitingOn: "user", status: "done" },
        ],
      }),
    );

    expect(reminders).toEqual([]);
  });

  it("projects reply_required reminders per stable event id", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-2", { replyRequired: true }),
          makeChatEvent("evt-1", { replyRequired: true }),
        ],
      }),
    );

    expect(reminders).toHaveLength(2);
    expect(reminders.map((item) => item.fingerprint)).toEqual([
      "group:g-demo:reply_required:evt-2",
      "group:g-demo:reply_required:evt-1",
    ]);
    expect(reminders[0]?.summary).toBe("");
    expect(reminders[0]?.suggestion).toBe("请先处理 message:evt-2");
    expect(reminders[0]?.suggestionPreview).toBe("请先处理 message:evt-2");
  });

  it("drops mention reminders without a clear workflow suggestion", () => {
    const reminder = createMentionReminder(
      "g-demo",
      makeChatEvent("evt-1", { by: "claude-1", text: "很长很长的实现细节", to: ["user"] }),
    );

    expect(reminder).toBeNull();
  });

  it("ignores acked or replied reply_required events", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-1", { replyRequired: true, acked: true }),
          makeChatEvent("evt-2", { replyRequired: true, replied: true }),
        ],
      }),
    );

    expect(reminders).toHaveLength(0);
  });

  it("replaces stopped stalled peers with actor_down reminders", () => {
    const reminders = projectPetReminders(
      makeInput({
        actors: [
          makeActor("peer-impl-2", { running: true, idleSeconds: 601, activeTaskId: "T249" }),
          makeActor("peer-reviewer", { running: true, idleSeconds: 599, activeTaskId: "T300" }),
          makeActor("peer-impl-1", { running: false, idleSeconds: 1000, activeTaskId: "T301" }),
        ],
      }),
    );

    expect(reminders).toEqual([
      expect.objectContaining({
        kind: "actor_down",
        action: {
          type: "restart_actor",
          groupId: "g-demo",
          actorId: "peer-impl-1",
        },
      }),
    ]);
  });

  it("projects actor_down reminders for a stopped foreman", () => {
    const reminders = projectPetReminders(
      makeInput({
        actors: [
          makeActor("foreman-1", {
            role: "foreman",
            title: "Foreman",
            running: false,
          }),
        ],
      }),
    );

    expect(reminders).toEqual([
      expect.objectContaining({
        kind: "actor_down",
        summary: "",
        action: {
          type: "restart_actor",
          groupId: "g-demo",
          actorId: "foreman-1",
        },
      }),
    ]);
  });

  it("projects actor_down reminders for a stopped peer with active task", () => {
    const reminders = projectPetReminders(
      makeInput({
        actors: [
          makeActor("peer-impl-1", {
            role: "peer",
            title: "Peer Impl 1",
            running: false,
            activeTaskId: "T301",
          }),
        ],
      }),
    );

    expect(reminders).toEqual([
      expect.objectContaining({
        kind: "actor_down",
        summary: "",
        action: {
          type: "restart_actor",
          groupId: "g-demo",
          actorId: "peer-impl-1",
        },
      }),
    ]);
  });

  it("does not project actor_down reminders for running or disabled actors", () => {
    const reminders = projectPetReminders(
      makeInput({
        actors: [
          makeActor("foreman-1", {
            role: "foreman",
            title: "Foreman",
            running: true,
          }),
          makeActor("peer-impl-1", {
            role: "peer",
            title: "Peer Impl 1",
            running: false,
            enabled: false,
            activeTaskId: "T301",
          }),
          makeActor("peer-impl-2", {
            role: "peer",
            title: "Peer Impl 2",
            running: false,
          }),
        ],
      }),
    );

    expect(reminders).toEqual([]);
  });

  it("projects mention reminders with sendable suggestions for agent-to-user messages", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-1", { by: "peer-reviewer", text: "Please review this patch", to: ["user"] }),
          makeChatEvent("evt-2", { by: "user", text: "self mention", to: ["user"] }),
        ],
      }),
    );

    expect(reminders).toHaveLength(1);
    expect(reminders[0]?.kind).toBe("mention");
    expect(reminders[0]?.suggestion).toBe("Please review this patch");
    expect(reminders[0]?.action).toEqual({
      type: "send_suggestion",
      groupId: "g-demo",
      text: "Please review this patch",
      to: ["peer-reviewer"],
      replyTo: "evt-1",
    });
  });

  it("strips leading meta phrases from suggestion text", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-3", {
            by: "peer-reviewer",
            to: ["user"],
            text: "有个增量更新，请先回复用户，代码侧已经过了。",
          }),
        ],
      }),
    );

    expect(reminders[0]?.suggestion).toBe("请先回复用户，代码侧已经过了");
    expect(reminders[0]?.suggestionPreview).toBe("请先回复用户，代码侧已经过了");
  });

  it("keeps full suggestion text in send action while truncating display preview", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-4", {
            by: "peer-reviewer",
            to: ["user"],
            text: "有个补充说明，这条已经不是口头判断了，补丁、测试和验证结论都齐了，只差发给用户，顺手把后续同步和回归结论也一起补掉。",
          }),
        ],
      }),
    );

    expect(reminders[0]?.suggestion).toBe("说明，这条已经不是口头判断了，补丁、测试和验证结论都齐了，只差发给用户，顺手把后续同步和回归结论也一起补掉");
    expect(reminders[0]?.suggestionPreview).toMatch(/…$/);
    expect(reminders[0]?.suggestionPreview?.length).toBeLessThan(reminders[0]?.suggestion?.length || 0);
    expect(reminders[0]?.action).toEqual({
      type: "send_suggestion",
      groupId: "g-demo",
      text: "说明，这条已经不是口头判断了，补丁、测试和验证结论都齐了，只差发给用户，顺手把后续同步和回归结论也一起补掉",
      to: ["peer-reviewer"],
      replyTo: "evt-4",
    });
  });

  it("only keeps actionable message suggestions", () => {
    const reminders = projectPetReminders(
      makeInput({
        waitingUser: [{ taskId: "T249", label: "Need approval" }],
        tasks: [{ taskId: "T249", title: "Need approval" }],
        actors: [makeActor("peer-impl-2", { running: true, idleSeconds: 601, activeTaskId: "T249" })],
        events: [makeChatEvent("evt-1", { replyRequired: true })],
      }),
    );

    expect(reminders.map((item) => item.kind)).toEqual([
      "reply_required",
    ]);
  });

  it("dedupes actionable reminders by fingerprint", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-1", { replyRequired: true }),
          makeChatEvent("evt-1", { replyRequired: true }),
        ],
      }),
    );

    expect(reminders.map((item) => item.fingerprint)).toEqual([
      "group:g-demo:reply_required:evt-1",
    ]);
  });

  it("drops no-delta coordination chatter even when it mentions the user", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-9", {
            by: "claude-1",
            to: ["user"],
            text: "本轮 15 分钟对齐仍是 no-delta。recall 命中的稳定经验没有被新事实推翻。",
          }),
        ],
      }),
    );

    expect(reminders).toEqual([]);
  });

  it("accepts Japanese actionable guidance and strips Japanese lead-in", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-ja", {
            by: "peer-reviewer",
            to: ["user"],
            text: "補足です、ユーザーへの返信内容を確認してください。",
          }),
        ],
      }),
    );

    expect(reminders).toEqual([
      expect.objectContaining({
        kind: "mention",
        suggestion: "補足、ユーザーへの返信内容を確認してください",
        suggestionPreview: "補足、ユーザーへの返信内容を確認してください",
      }),
    ]);
  });

  it("drops Japanese sync-only chatter", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-ja-sync", {
            by: "peer-reviewer",
            to: ["user"],
            text: "差分なし、共有のみです。返信不要。",
          }),
        ],
      }),
    );

    expect(reminders).toEqual([]);
  });
});
