import { describe, expect, it } from "vitest";

import { getAutoFollowTrigger, getStableMessageKey, shouldUseVirtualizedMessageList } from "../../src/components/VirtualMessageList";
import type { LedgerEvent } from "../../src/types";

describe("getStableMessageKey", () => {
  it("keeps a reply-slot key stable once a pending event id is known", () => {
    const base: LedgerEvent = {
      id: "local:msg-1:coder",
      kind: "chat.message",
      by: "coder",
      _streaming: true,
      data: {
        text: "",
        to: ["user"],
        stream_id: "local:msg-1:coder",
      },
    };

    const promoted: LedgerEvent = {
      ...base,
      data: {
        ...base.data,
        stream_id: "pending:evt-1:coder",
        pending_event_id: "evt-1",
        pending_placeholder: true,
      },
    };

    const liveStream: LedgerEvent = {
      ...promoted,
      data: {
        ...promoted.data,
        stream_id: "stream-final-1",
        pending_placeholder: false,
        text: "正在输出",
      },
    };

    const completed: LedgerEvent = {
      ...liveStream,
      _streaming: false,
      data: {
        ...liveStream.data,
        text: "最终回复",
      },
    };

    expect(getStableMessageKey(base, 0)).toBe("message-event:local:msg-1:coder");
    expect(getStableMessageKey(promoted, 0)).toBe("message-event:local:msg-1:coder");
    expect(getStableMessageKey(liveStream, 0)).toBe("message-event:local:msg-1:coder");
    expect(getStableMessageKey(completed, 0)).toBe("message-event:local:msg-1:coder");
  });
});

describe("shouldUseVirtualizedMessageList", () => {
  it("keeps small chat transcripts on the non-virtualized path", () => {
    expect(shouldUseVirtualizedMessageList(0)).toBe(false);
    expect(shouldUseVirtualizedMessageList(1)).toBe(false);
    expect(shouldUseVirtualizedMessageList(79)).toBe(false);
  });

  it("enables virtualization only once the transcript is large enough", () => {
    expect(shouldUseVirtualizedMessageList(80)).toBe(true);
    expect(shouldUseVirtualizedMessageList(120)).toBe(true);
  });
});

describe("getAutoFollowTrigger", () => {
  it("prefers append when a new tail item is added", () => {
    expect(
      getAutoFollowTrigger({
        previousTailSnapshot: { count: 2, tailKey: "m2" },
        nextTailSnapshot: { count: 3, tailKey: "m3" },
        previousTailMutationSnapshot: { tailKey: "m2", signature: "2|m2|assistant|t2||10|0" },
        nextTailMutationSnapshot: { tailKey: "m3", signature: "3|m3|assistant|t3||0|0" },
      }),
    ).toBe("append");
  });

  it("uses mutation when the tail item stays the same but its content changes", () => {
    expect(
      getAutoFollowTrigger({
        previousTailSnapshot: { count: 3, tailKey: "m3" },
        nextTailSnapshot: { count: 3, tailKey: "m3" },
        previousTailMutationSnapshot: { tailKey: "m3", signature: "3|m3|assistant|t3||10|0" },
        nextTailMutationSnapshot: { tailKey: "m3", signature: "3|m3|assistant|t3||40|0" },
      }),
    ).toBe("mutation");
  });

  it("returns null when neither append nor tail mutation happened", () => {
    expect(
      getAutoFollowTrigger({
        previousTailSnapshot: { count: 3, tailKey: "m3" },
        nextTailSnapshot: { count: 3, tailKey: "m3" },
        previousTailMutationSnapshot: { tailKey: "m3", signature: "3|m3|assistant|t3||40|0" },
        nextTailMutationSnapshot: { tailKey: "m3", signature: "3|m3|assistant|t3||40|0" },
      }),
    ).toBeNull();
  });
});
