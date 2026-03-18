import { describe, expect, it } from "vitest";
import { BUILTIN_ROLE_PRESETS, getRolePresetApplyState, getRolePresetById } from "./rolePresets";

describe("rolePresets", () => {
  it("ships the first-wave built-in preset roster", () => {
    expect(BUILTIN_ROLE_PRESETS.map((item) => item.id)).toEqual([
      "coordinator",
      "planner",
      "implementer",
      "reviewer",
      "debugger",
      "explorer",
      "researcher",
    ]);
  });

  it("ships structured content for every preset", () => {
    for (const preset of BUILTIN_ROLE_PRESETS) {
      expect(preset.name.trim()).not.toBe("");
      expect(preset.summary.trim()).not.toBe("");
      expect(preset.useWhen.trim()).not.toBe("");
      expect(preset.content).toContain("### Mission");
      expect(preset.content).toContain("### Hard Rules");
    }
  });

  it("looks up presets by id", () => {
    expect(getRolePresetById("implementer")?.name).toBe("Implementer");
    expect(getRolePresetById("")).toBeNull();
    expect(getRolePresetById("missing")).toBeNull();
  });

  it("computes draft replacement behavior conservatively", () => {
    const preset = "preset body";
    expect(getRolePresetApplyState("", preset)).toBe("apply");
    expect(getRolePresetApplyState("  ", preset)).toBe("apply");
    expect(getRolePresetApplyState("preset body", preset)).toBe("no_change");
    expect(getRolePresetApplyState("existing draft", preset)).toBe("confirm_replace");
    expect(getRolePresetApplyState("existing draft", "")).toBe("no_change");
  });
});
