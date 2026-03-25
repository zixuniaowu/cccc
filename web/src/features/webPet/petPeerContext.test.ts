import { describe, expect, it } from "vitest";
import { buildPetPeerContext } from "./petPeerContext";

describe("petPeerContext", () => {
  it("injects pet persona into a dedicated pet peer prompt", () => {
    const context = buildPetPeerContext({
      persona: "Keep low-noise. Auto restart actors.",
      help: "## Pet Persona\nKeep low-noise. Auto restart actors.",
      prompt: "You are the group's independent pet peer.\nRuntime Snapshot:\nGroup: Demo Team\nAgent Snapshot: foreman: T1 | close loop",
      snapshot: "Group: Demo Team\nAgent Snapshot: foreman: T1 | close loop",
      source: "help",
    });

    expect(context.source).toBe("help");
    expect(context.help).toContain("## Pet Persona");
    expect(context.prompt).toContain("You are the group's independent pet peer.");
    expect(context.snapshot).toContain("Group: Demo Team");
    expect(context.policy.autoRestartActors).toBe(true);
  });

  it("falls back to default source when persona is empty", () => {
    const context = buildPetPeerContext(null);

    expect(context.source).toBe("default");
    expect(context.prompt).toBe("");
  });
});
