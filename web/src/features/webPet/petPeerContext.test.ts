import { describe, expect, it } from "vitest";
import { buildPetPeerContext } from "./petPeerContext";

describe("petPeerContext", () => {
  it("maps pet decisions into reminders", () => {
    const context = buildPetPeerContext({
      decisions: [
        {
          id: "dec-1",
          kind: "actor_down",
          priority: 95,
          summary: "peer-1 最近出现执行错误，建议重启。",
          agent: "peer-1",
          fingerprint: "group:g-1:actor_down:peer-1",
          source: {
            actor_id: "peer-1",
            actor_role: "peer",
            task_id: "T1",
            error_reason: "Could not process image",
          },
          action: {
            type: "restart_actor",
            group_id: "g-1",
            actor_id: "peer-1",
          },
        },
      ],
      persona: "Keep low-noise. Auto restart actors.",
      help: "## Pet Persona\nKeep low-noise. Auto restart actors.",
      prompt: "You are the group's independent pet peer.\nRuntime Snapshot:\nGroup: Demo Team\nAgent Snapshot: foreman: T1 | close loop",
      snapshot: "Group: Demo Team\nAgent Snapshot: foreman: T1 | close loop",
      source: "help",
    });

    expect(context.source).toBe("help");
    expect(context.help).toContain("## Pet Persona");
    expect(context.prompt).toContain("independent pet peer");
    expect(context.snapshot).toContain("Group: Demo Team");
    expect(context.decisions[0]?.source.actorId).toBe("peer-1");
    expect(context.decisions[0]?.action.type).toBe("restart_actor");
    expect(context.policy.compactMessageEvents).toBe(true);
  });

  it("falls back to default source when persona is empty", () => {
    const context = buildPetPeerContext(null);

    expect(context.source).toBe("default");
    expect(context.decisions).toEqual([]);
    expect(context.prompt).toBe("");
  });

  it("maps task proposal decisions for foreman handoff", () => {
    const context = buildPetPeerContext({
      decisions: [
        {
          id: "dec-task-1",
          kind: "suggestion",
          priority: 88,
          summary: "建议让 foreman 推进 T315。",
          agent: "pet-peer",
          fingerprint: "group:g-1:suggestion:task-proposal:T315",
          source: {
            task_id: "T315",
          },
          action: {
            type: "task_proposal",
            group_id: "g-1",
            operation: "move",
            task_id: "T315",
            status: "active",
          },
        },
      ],
      persona: "Keep low-noise.",
      help: "",
      prompt: "",
      snapshot: "",
      source: "default",
    });

    expect(context.decisions[0]?.action.type).toBe("task_proposal");
    if (context.decisions[0]?.action.type === "task_proposal") {
      expect(context.decisions[0].action.operation).toBe("move");
      expect(context.decisions[0].action.taskId).toBe("T315");
      expect(context.decisions[0].action.status).toBe("active");
    }
  });
});
