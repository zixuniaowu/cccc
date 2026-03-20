import { describe, expect, it } from "vitest";
import { getEffectiveComposerDestGroupId } from "../../src/stores/useComposerStore";
import {
  getEffectiveComposerRecipientText,
  getOptimisticRecipients,
} from "../../src/utils/chatRecipients";

describe("getEffectiveComposerDestGroupId", () => {
  it("falls back to the selected group while composer state still belongs to the previous group", () => {
    expect(getEffectiveComposerDestGroupId("g-old", "g-old", "g-new")).toBe("g-new");
  });

  it("keeps an explicit cross-group destination once composer state has switched to the current group", () => {
    expect(getEffectiveComposerDestGroupId("g-remote", "g-current", "g-current")).toBe("g-remote");
  });

  it("defaults to the selected group when there is no explicit destination", () => {
    expect(getEffectiveComposerDestGroupId("", "g-current", "g-current")).toBe("g-current");
  });
});

describe("chatRecipients helpers", () => {
  it("drops stale recipient text before the composer switches to the new group", () => {
    expect(getEffectiveComposerRecipientText("@all", "g-old", "g-new")).toBe("");
  });

  it("keeps recipient text once composer state matches the selected group", () => {
    expect(getEffectiveComposerRecipientText("@foreman", "g-current", "g-current")).toBe("@foreman");
  });

  it("aligns optimistic recipients with foreman default routing", () => {
    expect(getOptimisticRecipients([], "foreman")).toEqual(["@foreman"]);
  });

  it("keeps broadcast optimistic recipients empty when no explicit recipient is chosen", () => {
    expect(getOptimisticRecipients([], "broadcast")).toEqual([]);
  });
});
