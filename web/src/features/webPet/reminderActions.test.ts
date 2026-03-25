import { describe, expect, it } from "vitest";
import { getReminderActionButtons } from "./reminderActions";
import type { PetReminder } from "./types";

describe("getReminderActionButtons", () => {
  it("prefers send for reply_required reminders with suggestion", () => {
    const reminder: PetReminder = {
      id: "reply_required:evt-1",
      kind: "reply_required",
      priority: 80,
      summary: "请先回复用户",
      suggestion: "收到，我来跟进这条，稍后给你同步进展。",
      agent: "foreman",
      source: { eventId: "evt-1" },
      fingerprint: "group:g-1:reply_required:evt-1",
      action: {
        type: "send_suggestion",
        groupId: "g-1",
        text: "收到，我来跟进这条，稍后给你同步进展。",
        to: ["foreman"],
        replyTo: "evt-1",
      },
    };

    expect(getReminderActionButtons(reminder)).toEqual([
      {
        labelKey: "send",
        fallback: "Send",
        action: reminder.action,
      },
    ]);
  });

  it("returns restart button for actor_down reminders", () => {
    const reminder: PetReminder = {
      id: "actor_down:peer-1",
      kind: "actor_down",
      priority: 90,
      summary: "Peer 1 已停止，当前任务 T1 可能卡住，建议重启。",
      agent: "Peer 1",
      source: { actorId: "peer-1", taskId: "T1" },
      fingerprint: "group:g-1:actor_down:peer-1",
      action: {
        type: "restart_actor",
        groupId: "g-1",
        actorId: "peer-1",
      },
    };

    expect(getReminderActionButtons(reminder)).toEqual([
      {
        labelKey: "restart",
        fallback: "Restart",
        action: reminder.action,
      },
    ]);
  });
});
