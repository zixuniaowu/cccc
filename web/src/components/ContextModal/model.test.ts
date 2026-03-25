import { describe, expect, it } from "vitest";
import { getDefaultPetPersonaSeed } from "../../utils/rolePresets";
import { petPersonaDraftDirty, petPersonaDraftMatches, resolvePetPersonaDraft } from "./model";

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
