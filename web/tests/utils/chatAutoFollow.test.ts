import { describe, expect, it } from "vitest";

import { getChatTailSnapshot, shouldAutoFollowOnTailAppend } from "../../src/utils/chatAutoFollow";

describe("chatAutoFollow", () => {
  it("auto-follows real tail append", () => {
    const prev = getChatTailSnapshot("m-1", 1);
    const next = getChatTailSnapshot("m-2", 2);

    expect(shouldAutoFollowOnTailAppend(prev, next)).toBe(true);
  });

  it("does not auto-follow history prepend that keeps the same tail", () => {
    const prev = getChatTailSnapshot("m-9", 9);
    const next = getChatTailSnapshot("m-9", 12);

    expect(shouldAutoFollowOnTailAppend(prev, next)).toBe(false);
  });

  it("does not auto-follow first load or non-growing lists", () => {
    expect(
      shouldAutoFollowOnTailAppend(
        getChatTailSnapshot(null, 0),
        getChatTailSnapshot("m-1", 1),
      )
    ).toBe(false);
    expect(
      shouldAutoFollowOnTailAppend(
        getChatTailSnapshot("m-2", 2),
        getChatTailSnapshot("m-3", 2),
      )
    ).toBe(false);
  });
});
