import { describe, expect, it } from "vitest";

import {
  buildReplySlotTsMap,
  collapseActorStreamingPlaceholders,
  dedupeStreamingEvents,
  mergeLiveChatMessageEvents,
  sortChatMessages,
} from "../../src/hooks/useChatTab";
import type { LedgerEvent } from "../../src/types";

function makeStreamingEvent({
  id,
  by = "claude-1",
  streamId,
  pendingEventId,
  text = "",
  pendingPlaceholder = false,
}: {
  id: string;
  by?: string;
  streamId: string;
  pendingEventId?: string;
  text?: string;
  pendingPlaceholder?: boolean;
}): LedgerEvent {
  return {
    id,
    kind: "chat.message",
    by,
    _streaming: true,
    data: {
      text,
      to: ["user"],
      stream_id: streamId,
      pending_event_id: pendingEventId,
      pending_placeholder: pendingPlaceholder,
      activities: pendingPlaceholder
        ? [{ id: `queued:${id}`, kind: "queued", status: "started", summary: "queued" }]
        : [],
    },
  };
}

describe("dedupeStreamingEvents", () => {
  it("collapses multiple stream ids for the same pending event into one bubble", () => {
    const events = dedupeStreamingEvents([
      makeStreamingEvent({
        id: "stream:commentary-1",
        streamId: "commentary-1",
        pendingEventId: "evt-1",
        text: "先查一下",
      }),
      makeStreamingEvent({
        id: "stream:commentary-2",
        streamId: "commentary-2",
        pendingEventId: "evt-1",
        text: "继续看",
      }),
    ]);

    expect(events).toHaveLength(1);
    expect(String((events[0]?.data as { stream_id?: string }).stream_id || "")).toBe("commentary-1");
    expect(String((events[0]?.data as { text?: string }).text || "")).toBe("先查一下");
  });

  it("prefers commentary text over a same-pending final placeholder", () => {
    const events = dedupeStreamingEvents([
      makeStreamingEvent({
        id: "stream:final-1",
        streamId: "final-1",
        pendingEventId: "evt-1",
        text: "",
      }),
      makeStreamingEvent({
        id: "stream:commentary-1",
        streamId: "commentary-1",
        pendingEventId: "evt-1",
        text: "我先继续查前端合并链路",
      }),
    ]);

    expect(events).toHaveLength(1);
    expect(String((events[0]?.data as { stream_id?: string }).stream_id || "")).toBe("commentary-1");
    expect(String((events[0]?.data as { text?: string }).text || "")).toBe("我先继续查前端合并链路");
  });

  it("collapses placeholder and bound stream variants of the same pending event", () => {
    const events = dedupeStreamingEvents([
      makeStreamingEvent({
        id: "pending:evt-1:claude-1",
        streamId: "pending:evt-1:claude-1",
        pendingEventId: "evt-1",
        pendingPlaceholder: true,
      }),
      makeStreamingEvent({
        id: "stream:final-1",
        streamId: "final-1",
        pendingEventId: "evt-1",
        text: "最终回复",
      }),
    ]);

    expect(events).toHaveLength(1);
    expect(String((events[0]?.data as { stream_id?: string }).stream_id || "")).toBe("final-1");
    expect(String((events[0]?.data as { pending_event_id?: string }).pending_event_id || "")).toBe("evt-1");
  });
});

describe("collapseActorStreamingPlaceholders", () => {
  it("drops process-only pending bubbles when the same actor already has real streaming text", () => {
    const events = collapseActorStreamingPlaceholders([
      makeStreamingEvent({
        id: "stream:commentary-1",
        streamId: "commentary-1",
        pendingEventId: "evt-1",
        text: "我在查前端合并链路",
      }),
      {
        id: "pending:evt-1:claude-1",
        kind: "chat.message",
        by: "claude-1",
        _streaming: true,
        data: {
          text: "",
          to: ["user"],
          stream_id: "pending:evt-1:claude-1",
          pending_event_id: "evt-1",
          pending_placeholder: true,
          activities: [{ id: "tool-1", kind: "tool", status: "started", summary: "chrome-devtools:take_snapshot" }],
        },
      },
    ]);

    expect(events).toHaveLength(1);
    expect(events[0]?.data?.stream_id).toBe("commentary-1");
  });
});

describe("sortChatMessages", () => {
  it("keeps finalized replies in their original streaming slot order", () => {
    const streaming: LedgerEvent[] = [
      {
        id: "stream:a",
        ts: "2026-04-04T15:29:00.100Z",
        kind: "chat.message",
        by: "backend-expert",
        _streaming: true,
        data: {
          text: "",
          pending_event_id: "user-msg-1",
          stream_id: "stream-a",
          to: ["user"],
        },
      },
      {
        id: "stream:b",
        ts: "2026-04-04T15:29:00.200Z",
        kind: "chat.message",
        by: "project-director",
        _streaming: true,
        data: {
          text: "",
          pending_event_id: "user-msg-2",
          stream_id: "stream-b",
          to: ["user"],
        },
      },
    ];
    const canonical: LedgerEvent[] = [
      {
        id: "evt-b",
        ts: "2026-04-04T15:29:03.000Z",
        kind: "chat.message",
        by: "project-director",
        data: {
          text: "第二条先完成",
          reply_to: "user-msg-2",
          stream_id: "stream-b",
          to: ["user"],
        },
      },
      {
        id: "evt-a",
        ts: "2026-04-04T15:29:04.000Z",
        kind: "chat.message",
        by: "backend-expert",
        data: {
          text: "第一条后完成",
          reply_to: "user-msg-1",
          stream_id: "stream-a",
          to: ["user"],
        },
      },
    ];

    const ordered = sortChatMessages(canonical, buildReplySlotTsMap(streaming));

    expect(ordered.map((event) => String(event.id || ""))).toEqual(["evt-a", "evt-b"]);
  });
});

describe("mergeLiveChatMessageEvents", () => {
  it("does not collapse multiple canonical replies from the same actor to the same parent", () => {
    const merged = mergeLiveChatMessageEvents(
      [
        {
          id: "evt-1",
          ts: "2026-04-04T16:28:01.000Z",
          kind: "chat.message",
          by: "requirements-expert",
          data: {
            text: "我先查",
            reply_to: "user-msg-1",
            to: ["user"],
          },
        },
        {
          id: "evt-2",
          ts: "2026-04-04T16:28:05.000Z",
          kind: "chat.message",
          by: "requirements-expert",
          data: {
            text: "查到了，继续给结论",
            reply_to: "user-msg-1",
            to: ["user"],
          },
        },
      ],
      [],
      [],
    );

    expect(merged.map((event) => String(event.id || ""))).toEqual(["evt-1", "evt-2"]);
  });

  it("keeps commentary streaming text when canonical message with the same stream id is still empty", () => {
    const merged = mergeLiveChatMessageEvents(
      [
        {
          id: "evt-empty",
          ts: "2026-04-04T15:29:03.000Z",
          kind: "chat.message",
          by: "claude-1",
          data: {
            text: "",
            reply_to: "user-msg-1",
            stream_id: "stream-commentary",
            to: ["user"],
          },
        },
      ],
      [
        {
          id: "stream:commentary",
          ts: "2026-04-04T15:29:00.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "我先检查 commentary 合并",
            pending_event_id: "user-msg-1",
            stream_id: "stream-commentary",
            to: ["user"],
          },
        },
      ],
      [],
    );

    expect(merged).toHaveLength(2);
    expect(merged.map((event) => String(event.id || ""))).toEqual(["stream:commentary", "evt-empty"]);
    expect(String((merged[0]?.data as { text?: unknown })?.text || "")).toBe("我先检查 commentary 合并");
  });

  it("drops streaming commentary once canonical message with the same stream id has renderable content", () => {
    const merged = mergeLiveChatMessageEvents(
      [
        {
          id: "evt-final",
          ts: "2026-04-04T15:29:03.000Z",
          kind: "chat.message",
          by: "claude-1",
          data: {
            text: "已经定位到问题",
            reply_to: "user-msg-1",
            stream_id: "stream-commentary",
            to: ["user"],
          },
        },
      ],
      [
        {
          id: "stream:commentary",
          ts: "2026-04-04T15:29:00.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "我先检查 commentary 合并",
            pending_event_id: "user-msg-1",
            stream_id: "stream-commentary",
            to: ["user"],
          },
        },
      ],
      [],
    );

    expect(merged).toHaveLength(1);
    expect(String(merged[0]?.id || "")).toBe("evt-final");
    expect(String((merged[0]?.data as { text?: unknown })?.text || "")).toBe("已经定位到问题");
  });
});
