import { describe, expect, it } from "vitest";
import { derivePetPersonaPolicy } from "./petPersona";

describe("derivePetPersonaPolicy", () => {
  it("defaults to manual actor restart and task auto-close", () => {
    const policy = derivePetPersonaPolicy("");

    expect(policy.autoRestartActors).toBe(false);
    expect(policy.autoCompleteTasks).toBe(true);
  });

  it("keeps low-noise display flags from persona text", () => {
    expect(
      derivePetPersonaPolicy("Keep it low-noise and terse.").compactMessageEvents,
    ).toBe(true);
  });

  it("detects actor recovery and task auto-close policies from persona text", () => {
    const policy = derivePetPersonaPolicy(`
      允许自动重启 actor。
      允许自动收口 task。
    `);

    expect(policy.autoRestartActors).toBe(true);
    expect(policy.autoCompleteTasks).toBe(true);
  });

  it("allows persona text to explicitly disable automatic actions", () => {
    const policy = derivePetPersonaPolicy(`
      保持低噪声。
      不要自动重启。
      不要自动收口。
    `);

    expect(policy.autoRestartActors).toBe(false);
    expect(policy.autoCompleteTasks).toBe(false);
  });

  it("understands equivalent Japanese persona instructions", () => {
    const policy = derivePetPersonaPolicy(`
      低ノイズで簡潔に。
      actorを自動再起動。
      タスクを自動完了しない。
    `);

    expect(policy.compactMessageEvents).toBe(true);
    expect(policy.autoRestartActors).toBe(true);
    expect(policy.autoCompleteTasks).toBe(false);
  });
});
