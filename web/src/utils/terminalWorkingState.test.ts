import { describe, expect, it } from "vitest";

import type { Actor } from "../types";
import { getActorDisplayWorkingState } from "./terminalWorkingState";

function makeActor(overrides: Partial<Actor> = {}): Actor {
  return {
    id: "peer-1",
    title: "Peer 1",
    enabled: true,
    running: true,
    runner: "pty",
    runner_effective: "pty",
    effective_working_state: "idle",
    idle_seconds: 0.8,
    ...overrides,
  };
}

describe("getActorDisplayWorkingState", () => {
  it("treats recently active PTY actors as working even before a local terminal signal exists", () => {
    expect(getActorDisplayWorkingState(makeActor(), null)).toBe("working");
  });

  it("keeps idle when the visible prompt says the actor is waiting for input", () => {
    expect(
      getActorDisplayWorkingState(
        makeActor(),
        { kind: "idle_prompt", updatedAt: Date.now() },
      ),
    ).toBe("idle");
  });

  it("does not apply the PTY heuristic to headless actors", () => {
    expect(
      getActorDisplayWorkingState(
        makeActor({ runner: "headless", runner_effective: "headless" }),
        null,
      ),
    ).toBe("idle");
  });
});
