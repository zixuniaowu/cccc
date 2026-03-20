import { describe, expect, it } from "vitest";
import {
  shouldInvalidateGroupsReadAfterGlobalEventsOpen,
  shouldRefreshGroupsAfterGlobalEventsOpen,
} from "../../src/hooks/useGlobalEvents";

describe("useGlobalEvents open refresh policy", () => {
  it("requires catch-up refresh on the first successful open", () => {
    expect(shouldRefreshGroupsAfterGlobalEventsOpen(false)).toBe(true);
  });

  it("requires catch-up refresh on reconnects too", () => {
    expect(shouldRefreshGroupsAfterGlobalEventsOpen(true)).toBe(true);
  });

  it("keeps first-open catch-up off invalidateRecent", () => {
    expect(shouldInvalidateGroupsReadAfterGlobalEventsOpen(false)).toBe(false);
  });

  it("still invalidates recent groups reads on reconnects", () => {
    expect(shouldInvalidateGroupsReadAfterGlobalEventsOpen(true)).toBe(true);
  });
});
