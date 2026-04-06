import { describe, expect, it } from "vitest";

import {
  CHAT_SCROLL_SNAPSHOT_MAX_AGE_MS,
  buildReplySlotTsMap,
  collapseActorStreamingPlaceholders,
  dedupeStreamingEvents,
  mergeLiveChatMessageEvents,
  mergeVisibleChatMessages,
  sortChatMessages,
  shouldRestoreDetachedScrollSnapshot,
  supportsChatStreamingPlaceholder,
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

  it("keeps a new local queued placeholder when the actor only has rich streaming from an older reply slot", () => {
    const events = collapseActorStreamingPlaceholders([
      makeStreamingEvent({
        id: "stream:old-final",
        streamId: "old-final",
        pendingEventId: "evt-0",
        text: "上一轮已经有正文",
      }),
      {
        id: "stream:queued-local",
        kind: "chat.message",
        by: "claude-1",
        _streaming: true,
        data: {
          text: "",
          to: ["user"],
          stream_id: "local:msg-2:claude-1",
          pending_placeholder: true,
          activities: [{ id: "queued:2", kind: "queued", status: "started", summary: "queued" }],
        },
      },
    ]);

    expect(events).toHaveLength(2);
    expect(events.map((event) => String(event.id || ""))).toContain("stream:queued-local");
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
  it("keeps the optimistic user bubble visible until the canonical echo is renderable", () => {
    const optimisticEvent: LedgerEvent = {
      id: "local-1",
      ts: "2026-04-05T02:00:00.000Z",
      kind: "chat.message",
      by: "user",
      data: {
        text: "马上发出去",
        to: ["@foreman"],
        client_id: "local-1",
        _optimistic: true,
      },
    };
    const nonRenderableCanonical: LedgerEvent = {
      id: "evt-1",
      ts: "2026-04-05T02:00:01.000Z",
      kind: "chat.message",
      by: "user",
      data: {
        text: "",
        to: ["@foreman"],
        client_id: "local-1",
      },
    };

    const merged = mergeVisibleChatMessages(
      [nonRenderableCanonical],
      [],
      [optimisticEvent],
      { map: new Map(), next: 0 },
    );

    expect(merged).toHaveLength(1);
    expect(String(merged[0]?.id || "")).toBe("local-1");
    expect(String(((merged[0]?.data as { text?: string })?.text) || "")).toBe("马上发出去");
  });

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

    expect(merged).toHaveLength(1);
    expect(merged.map((event) => String(event.id || ""))).toEqual(["stream:commentary"]);
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

  it("replaces a queued streaming placeholder with the canonical reply in the same logical slot", () => {
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
            to: ["user"],
          },
        },
      ],
      [
        {
          id: "stream:queued",
          ts: "2026-04-04T15:29:00.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "",
            pending_event_id: "user-msg-1",
            stream_id: "pending:user-msg-1:claude-1",
            pending_placeholder: true,
            to: ["user"],
            activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
          },
        },
      ],
      [],
    );

    expect(merged).toHaveLength(1);
    expect(String(merged[0]?.id || "")).toBe("evt-final");
  });

  it("keeps a fresh local queued placeholder visible even when the actor already has older canonical replies", () => {
    const merged = mergeLiveChatMessageEvents(
      [
        {
          id: "evt-old",
          ts: "2026-04-04T15:29:03.000Z",
          kind: "chat.message",
          by: "claude-1",
          data: {
            text: "上一轮已经回复过",
            reply_to: "user-msg-0",
            to: ["user"],
          },
        },
      ],
      [
        {
          id: "stream:queued-local",
          ts: "2026-04-04T15:29:02.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "",
            stream_id: "local:msg-1:claude-1",
            pending_event_id: "local_1",
            pending_placeholder: true,
            to: ["user"],
            activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
          },
        },
      ],
      [],
    );

    expect(merged).toHaveLength(2);
    expect(merged.map((event) => String(event.id || ""))).toContain("stream:queued-local");
  });

  it("keeps pending and real streaming variants in one logical slot", () => {
    const merged = mergeVisibleChatMessages(
      [],
      [
        {
          id: "stream:local",
          ts: "2026-04-04T15:29:00.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "",
            pending_event_id: "user-msg-1",
            stream_id: "pending:user-msg-1:claude-1",
            pending_placeholder: true,
            to: ["user"],
            activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
          },
        },
        {
          id: "stream:real",
          ts: "2026-04-04T15:29:01.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "开始输出",
            pending_event_id: "user-msg-1",
            stream_id: "stream-final-1",
            pending_placeholder: false,
            to: ["user"],
          },
        },
      ],
      [],
      { map: new Map(), next: 0 },
    );

    expect(merged).toHaveLength(1);
    expect(String(merged[0]?.id || "")).toBe("stream:real");
  });

  it("keeps the streaming placeholder instead of replacing it with an empty canonical reply", () => {
    const merged = mergeVisibleChatMessages(
      [
        {
          id: "evt-empty",
          ts: "2026-04-04T15:29:01.000Z",
          kind: "chat.message",
          by: "claude-1",
          data: {
            text: "",
            reply_to: "user-msg-1",
            to: ["user"],
          },
        },
      ],
      [
        {
          id: "stream:queued-local",
          ts: "2026-04-04T15:29:00.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "",
            pending_event_id: "user-msg-1",
            stream_id: "local:msg-1:claude-1",
            pending_placeholder: true,
            to: ["user"],
            activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
          },
        },
      ],
      [],
      { map: new Map(), next: 0 },
    );

    expect(merged).toHaveLength(1);
    expect(String(merged[0]?.id || "")).toBe("stream:queued-local");
  });

  it("treats two local placeholders from the same actor as different logical reply slots", () => {
    const merged = mergeVisibleChatMessages(
      [],
      [
        {
          id: "stream:queued-local-1",
          ts: "2026-04-04T15:29:00.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "",
            pending_event_id: "local_1",
            stream_id: "local:msg-1:claude-1",
            pending_placeholder: true,
            to: ["user"],
            activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
          },
        },
        {
          id: "stream:queued-local-2",
          ts: "2026-04-04T15:29:02.000Z",
          kind: "chat.message",
          by: "claude-1",
          _streaming: true,
          data: {
            text: "",
            pending_event_id: "local_2",
            stream_id: "local:msg-2:claude-1",
            pending_placeholder: true,
            to: ["user"],
            activities: [{ id: "queued:2", kind: "queued", status: "started", summary: "queued" }],
          },
        },
      ],
      [],
      { map: new Map(), next: 0 },
    );

    expect(merged).toHaveLength(2);
    expect(merged.map((event) => String(event.id || ""))).toEqual([
      "stream:queued-local-1",
      "stream:queued-local-2",
    ]);
  });
});

describe("mergeVisibleChatMessages", () => {
  it("keeps an optimistic user reply visible while the matching canonical event is still empty", () => {
    const localId = "local-user-reply-1";
    const merged = mergeVisibleChatMessages(
      [
        {
          id: "evt-empty-user",
          ts: "2026-04-04T16:40:02.000Z",
          kind: "chat.message",
          by: "user",
          data: {
            text: "",
            client_id: localId,
            reply_to: "agent-msg-1",
            to: ["@assistant"],
          },
        },
      ],
      [],
      [
        {
          id: localId,
          ts: "2026-04-04T16:40:01.000Z",
          kind: "chat.message",
          by: "user",
          data: {
            text: "这是 optimistic reply",
            client_id: localId,
            reply_to: "agent-msg-1",
            to: ["@assistant"],
            _optimistic: true,
          },
        },
      ],
      { map: new Map(), next: 0 },
    );

    expect(merged).toHaveLength(1);
    expect(String(merged[0]?.id || "")).toBe(localId);
    expect(String((merged[0]?.data as { text?: unknown })?.text || "")).toBe("这是 optimistic reply");
  });

  it("replaces the optimistic user reply once the canonical event has renderable content", () => {
    const localId = "local-user-reply-2";
    const merged = mergeVisibleChatMessages(
      [
        {
          id: "evt-user-final",
          ts: "2026-04-04T16:41:02.000Z",
          kind: "chat.message",
          by: "user",
          data: {
            text: "这是最终 canonical reply",
            client_id: localId,
            reply_to: "agent-msg-1",
            to: ["@assistant"],
          },
        },
      ],
      [],
      [
        {
          id: localId,
          ts: "2026-04-04T16:41:01.000Z",
          kind: "chat.message",
          by: "user",
          data: {
            text: "这是 optimistic reply",
            client_id: localId,
            reply_to: "agent-msg-1",
            to: ["@assistant"],
            _optimistic: true,
          },
        },
      ],
      { map: new Map(), next: 0 },
    );

    expect(merged).toHaveLength(1);
    expect(String(merged[0]?.id || "")).toBe("evt-user-final");
    expect(String((merged[0]?.data as { text?: unknown })?.text || "")).toBe("这是最终 canonical reply");
  });
});

describe("shouldRestoreDetachedScrollSnapshot", () => {
  it("restores only fresh detached snapshots with anchors", () => {
    const now = 1_700_000_000_000;
    expect(shouldRestoreDetachedScrollSnapshot({
      mode: "detached",
      anchorId: "evt-1",
      updatedAt: now - 1000,
    }, now)).toBe(true);

    expect(shouldRestoreDetachedScrollSnapshot({
      mode: "follow",
      anchorId: "",
      updatedAt: now,
    }, now)).toBe(false);

    expect(shouldRestoreDetachedScrollSnapshot({
      mode: "detached",
      anchorId: "evt-1",
      updatedAt: now - CHAT_SCROLL_SNAPSHOT_MAX_AGE_MS - 1,
    }, now)).toBe(false);

    expect(shouldRestoreDetachedScrollSnapshot({
      mode: "detached",
      anchorId: "",
      updatedAt: now,
    }, now)).toBe(false);
  });
});

describe("supportsChatStreamingPlaceholder", () => {
  it("returns true for codex headless actors", () => {
    expect(supportsChatStreamingPlaceholder({
      runtime: "codex",
      runner: "pty",
      runner_effective: "headless",
    })).toBe(true);
  });

  it("returns false for codex pty actors", () => {
    expect(supportsChatStreamingPlaceholder({
      runtime: "codex",
      runner: "pty",
      runner_effective: "pty",
    })).toBe(false);
  });

  it("returns false for non-codex actors", () => {
    expect(supportsChatStreamingPlaceholder({
      runtime: "claude",
      runner: "headless",
      runner_effective: "headless",
    })).toBe(false);
  });
});
