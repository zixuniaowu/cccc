import { describe, expect, it } from "vitest";

import { buildReplyComposerState, getReplyEventId, isEphemeralMessageEventId } from "../../src/utils/chatReply";
import type { LedgerEvent } from "../../src/types";

describe("chatReply", () => {
  it("recognizes all temporary message id shapes", () => {
    expect(isEphemeralMessageEventId("stream:msg_123")).toBe(true);
    expect(isEphemeralMessageEventId("pending:evt-1:peer-1")).toBe(true);
    expect(isEphemeralMessageEventId("local:evt-1:peer-1")).toBe(true);
    expect(isEphemeralMessageEventId("local_123_abc")).toBe(true);
    expect(isEphemeralMessageEventId("evt_789")).toBe(false);
  });

  it("rejects transient stream ids without a canonical pending event id", () => {
    const event: LedgerEvent = {
      id: "stream:msg_123",
      kind: "chat.message",
      by: "peer-1",
      data: { text: "working on it" },
      _streaming: true,
    };

    expect(getReplyEventId(event)).toBe("");
    expect(buildReplyComposerState(event, "g-1", [], null)).toBe(null);
  });

  it("rejects optimistic local ids before the canonical event arrives", () => {
    const event: LedgerEvent = {
      id: "local_1712300000_abcd12",
      kind: "chat.message",
      by: "user",
      data: { text: "sending..." },
    };

    expect(getReplyEventId(event)).toBe("");
    expect(buildReplyComposerState(event, "g-1", [], null)).toBe(null);
  });

  it("uses pending_event_id as the reply target for streaming messages", () => {
    const event: LedgerEvent = {
      id: "stream:msg_456",
      kind: "chat.message",
      by: "peer-1",
      data: {
        text: "finalizing answer",
        pending_event_id: "evt_789",
      },
      _streaming: true,
    };

    expect(getReplyEventId(event)).toBe("evt_789");
    expect(buildReplyComposerState(event, "g-1", [{ id: "peer-1", title: "Peer 1" }], null)).toEqual({
      destGroupId: "g-1",
      toText: "peer-1",
      replyTarget: {
        eventId: "evt_789",
        by: "peer-1",
        text: "finalizing answer",
      },
    });
  });
});
