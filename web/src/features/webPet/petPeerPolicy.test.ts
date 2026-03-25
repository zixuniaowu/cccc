import { describe, expect, it } from "vitest";
import { derivePetPeerActions, shouldPetCompleteTask, shouldPetRestartActor } from "./petPeerPolicy";

describe("petPeerPolicy", () => {
  it("restarts enabled actors that are down while group is active", () => {
    expect(
      shouldPetRestartActor({ id: "peer-1", enabled: true, running: false }, "active"),
    ).toBe(true);
    expect(
      shouldPetRestartActor({ id: "peer-1", enabled: true, running: false }, "paused"),
    ).toBe(false);
    expect(
      shouldPetRestartActor({ id: "user", enabled: true, running: false }, "active"),
    ).toBe(false);
  });

  it("auto-completes only strict done-like active tasks", () => {
    expect(
      shouldPetCompleteTask({
        id: "T1",
        status: "active",
        waiting_on: "none",
        blocked_by: [],
        handoff_to: "",
        outcome: "Finished",
        checklist: [{ id: "c1", text: "done", status: "done" }],
        steps: [{ id: "s1", name: "ship", status: "done" }],
      }),
    ).toBe(true);

    expect(
      shouldPetCompleteTask({
        id: "T2",
        status: "active",
        waiting_on: "user",
        blocked_by: [],
        outcome: "Finished",
      }),
    ).toBe(false);
  });

  it("derives actor restart and task complete actions together", () => {
    expect(
      derivePetPeerActions({
        groupState: "active",
        actors: [{ id: "peer-1", enabled: true, running: false }],
        tasks: [{
          id: "T1",
          status: "active",
          waiting_on: "none",
          blocked_by: [],
          handoff_to: "",
          outcome: "Finished",
        }],
      }),
    ).toEqual([
      { kind: "restart_actor", actorId: "peer-1" },
      { kind: "complete_task", taskId: "T1" },
    ]);
  });
});
