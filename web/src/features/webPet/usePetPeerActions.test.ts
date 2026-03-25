import { beforeEach, describe, expect, it, vi } from "vitest";

const restartActorMock = vi.fn();
const contextSyncMock = vi.fn();

vi.mock("../../services/api", () => ({
  restartActor: restartActorMock,
  contextSync: contextSyncMock,
}));

describe("runPetPeerAction", () => {
  beforeEach(() => {
    vi.resetModules();
    restartActorMock.mockReset();
    contextSyncMock.mockReset();
  });

  it("keeps cooldown on successful actor restart", async () => {
    restartActorMock.mockResolvedValue({ ok: true });
    const mod = await import("./usePetPeerActions");
    const cooldownRef = { current: { "restart:g-1:peer-1": 12345 } };
    const inflightRef = { current: new Set<string>(["restart:g-1:peer-1"]) };

    await mod.runPetPeerAction({
      action: { kind: "restart_actor", actorId: "peer-1" },
      groupId: "g-1",
      cooldownKey: "restart:g-1:peer-1",
      cooldownRef,
      inflightRef,
    });

    expect(restartActorMock).toHaveBeenCalledWith("g-1", "peer-1");
    expect(cooldownRef.current["restart:g-1:peer-1"]).toBe(12345);
    expect(inflightRef.current.has("restart:g-1:peer-1")).toBe(false);
  });

  it("clears cooldown when task auto-close fails", async () => {
    contextSyncMock.mockRejectedValue(new Error("boom"));
    const mod = await import("./usePetPeerActions");
    const cooldownRef = { current: { "complete:g-1:T1": 99999 } };
    const inflightRef = { current: new Set<string>(["complete:g-1:T1"]) };

    await mod.runPetPeerAction({
      action: { kind: "complete_task", taskId: "T1" },
      groupId: "g-1",
      cooldownKey: "complete:g-1:T1",
      cooldownRef,
      inflightRef,
    });

    expect(contextSyncMock).toHaveBeenCalledWith("g-1", [
      { op: "task.move", task_id: "T1", status: "done" },
    ]);
    expect(cooldownRef.current["complete:g-1:T1"]).toBe(0);
    expect(inflightRef.current.has("complete:g-1:T1")).toBe(false);
  });

  it("clears cooldown when restart API returns ok false", async () => {
    restartActorMock.mockResolvedValue({
      ok: false,
      error: { code: "actor_restart_failed", message: "denied" },
    });
    const mod = await import("./usePetPeerActions");
    const cooldownRef = { current: { "restart:g-1:peer-1": 54321 } };
    const inflightRef = { current: new Set<string>(["restart:g-1:peer-1"]) };

    await mod.runPetPeerAction({
      action: { kind: "restart_actor", actorId: "peer-1" },
      groupId: "g-1",
      cooldownKey: "restart:g-1:peer-1",
      cooldownRef,
      inflightRef,
    });

    expect(cooldownRef.current["restart:g-1:peer-1"]).toBe(0);
    expect(inflightRef.current.has("restart:g-1:peer-1")).toBe(false);
  });
});
