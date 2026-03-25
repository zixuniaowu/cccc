import { describe, expect, it } from "vitest";
import { getDefaultPetPersonaSeed } from "../../utils/rolePresets";
import {
  getWaitingOnOptions,
  petPersonaDraftDirty,
  petPersonaDraftMatches,
  resolvePetPersonaDraft,
  waitingLabel,
} from "./model";

describe("ContextModal pet persona draft baseline", () => {
  it("treats the untouched pre-load state as clean", () => {
    expect(petPersonaDraftDirty("", "", { loaded: false })).toBe(false);
  });

  it("treats the default seed as clean when the saved pet block is empty", () => {
    expect(resolvePetPersonaDraft("")).toBe(getDefaultPetPersonaSeed());
    expect(petPersonaDraftMatches("", getDefaultPetPersonaSeed())).toBe(true);
    expect(petPersonaDraftDirty("", getDefaultPetPersonaSeed(), { loaded: true })).toBe(false);
  });

  it("marks the draft dirty after the user changes the seeded content", () => {
    expect(petPersonaDraftMatches("", `${getDefaultPetPersonaSeed()}\nextra rule`)).toBe(false);
    expect(petPersonaDraftDirty("", `${getDefaultPetPersonaSeed()}\nextra rule`, { loaded: true })).toBe(true);
  });

  it("uses the saved pet note as the clean baseline when one exists", () => {
    expect(petPersonaDraftMatches("Keep it terse.", "Keep it terse.")).toBe(true);
    expect(petPersonaDraftMatches("Keep it terse.", "Keep it terse.\nMore detail")).toBe(false);
  });
});

describe("ContextModal waiting_on labels", () => {
  const tr = (key: string, fallback: string) => `tx:${key}:${fallback}`;

  it("builds waiting_on options from the translator", () => {
    expect(getWaitingOnOptions(tr).map((item) => item.label)).toEqual([
      "tx:context.none:None",
      "tx:context.waitingOnUser:Waiting on user",
      "tx:context.waitingOnActor:Waiting on agent",
      "tx:context.waitingOnExternal:Waiting on external",
    ]);
  });

  it("formats waiting_on labels through the translator", () => {
    expect(waitingLabel("user", tr)).toBe("tx:context.waitingOnUser:Waiting on user");
    expect(waitingLabel("", tr)).toBe("tx:context.none:None");
  });
});
