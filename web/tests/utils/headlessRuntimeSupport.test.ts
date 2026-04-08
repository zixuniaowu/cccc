import { describe, expect, it } from "vitest";

import { supportsStandardWebHeadlessRuntime } from "../../src/utils/headlessRuntimeSupport";

describe("supportsStandardWebHeadlessRuntime", () => {
  it("allows the standard-web headless runtime whitelist", () => {
    expect(supportsStandardWebHeadlessRuntime("codex")).toBe(true);
    expect(supportsStandardWebHeadlessRuntime(" claude ")).toBe(true);
  });

  it("rejects unsupported runtimes", () => {
    expect(supportsStandardWebHeadlessRuntime("custom")).toBe(false);
    expect(supportsStandardWebHeadlessRuntime("gemini")).toBe(false);
    expect(supportsStandardWebHeadlessRuntime("")).toBe(false);
  });
});