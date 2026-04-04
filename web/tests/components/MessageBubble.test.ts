import { describe, expect, it } from "vitest";

import { isQueuedOnlyStreamingPlaceholder } from "../../src/components/MessageBubble";

describe("isQueuedOnlyStreamingPlaceholder", () => {
  it("returns true for pure queued placeholder without live text", () => {
    expect(isQueuedOnlyStreamingPlaceholder({
      isStreaming: true,
      messageText: "",
      liveStreamingText: "",
      blobAttachmentCount: 0,
      presentationRefCount: 0,
      activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
    })).toBe(true);
  });

  it("returns false once live streaming text exists", () => {
    expect(isQueuedOnlyStreamingPlaceholder({
      isStreaming: true,
      messageText: "",
      liveStreamingText: "我先查一下",
      blobAttachmentCount: 0,
      presentationRefCount: 0,
      activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
    })).toBe(false);
  });

  it("returns false once live activities are no longer queued-only", () => {
    expect(isQueuedOnlyStreamingPlaceholder({
      isStreaming: true,
      messageText: "",
      liveStreamingText: "",
      blobAttachmentCount: 0,
      presentationRefCount: 0,
      activities: [{ id: "reply:1", kind: "reply", status: "started", summary: "replying" }],
    })).toBe(false);
  });
});
