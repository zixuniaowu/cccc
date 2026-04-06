import { describe, expect, it } from "vitest";

import { computeSelectedGroupRuntime } from "../../src/hooks/useSelectedGroupRuntime";

describe("useSelectedGroupRuntime", () => {
  it("prefers live actor runtime over stale group meta", () => {
    const result = computeSelectedGroupRuntime({
      groups: [{ group_id: "g1", running: false, state: "active" }],
      selectedGroupId: "g1",
      groupDoc: { group_id: "g1", state: "active" },
      actors: [{ id: "a1", running: true }],
    });

    expect(result.selectedGroupRunning).toBe(true);
    expect(result.selectedGroupRuntimeStatus.runtime_running).toBe(true);
  });

  it("produces a sidebar patch for the selected group", () => {
    const result = computeSelectedGroupRuntime({
      groups: [{ group_id: "g1", running: false, state: "paused" }],
      selectedGroupId: "g1",
      groupDoc: {
        group_id: "g1",
        runtime_status: {
          lifecycle_state: "paused",
          runtime_running: true,
          running_actor_count: 1,
          has_running_foreman: true,
        },
      },
      actors: [],
    });

    expect(result.orderedSelectedGroupPatch).toEqual({
      running: true,
      state: "paused",
      runtime_status: {
        lifecycle_state: "paused",
        runtime_running: true,
        running_actor_count: 1,
        has_running_foreman: true,
      },
    });
  });
});
