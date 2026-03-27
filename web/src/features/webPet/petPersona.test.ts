import { describe, expect, it } from "vitest";
import { derivePetPersonaPolicy } from "./petPersona";

describe("derivePetPersonaPolicy", () => {
  it("defaults to low-noise display policy only", () => {
    const policy = derivePetPersonaPolicy("");

    expect(policy).toEqual({
      compactMessageEvents: true,
    });
  });

  it("keeps low-noise display flags from persona text", () => {
    expect(
      derivePetPersonaPolicy("Keep it low-noise and terse.").compactMessageEvents,
    ).toBe(true);
  });

  it("ignores auto-action wording in persona text", () => {
    const policy = derivePetPersonaPolicy(`
      允许自动重启 actor。
      允许自动收口 task。
    `);

    expect(policy).toEqual({
      compactMessageEvents: false,
    });
  });

  it("understands equivalent Japanese persona instructions", () => {
    const policy = derivePetPersonaPolicy(`
      低ノイズで簡潔に。
      actorを自動再起動。
      タスクを自動完了しない。
    `);

    expect(policy.compactMessageEvents).toBe(true);
    expect(policy).toEqual({
      compactMessageEvents: true,
    });
  });
});
