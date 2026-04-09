import { describe, expect, it } from "vitest";

import { buildHeadlessPreviewTimelineEntries } from "../../../src/pages/chat/headlessPreviewTimeline";

describe("buildHeadlessPreviewTimelineEntries", () => {
  it("merges message and activity rows into one chronological stream across recent sessions", () => {
    const entries = buildHeadlessPreviewTimelineEntries({
      previewSessions: [
        {
          actorId: "coder",
          pendingEventId: "evt-1",
          currentStreamId: "commentary-1",
          phase: "completed",
          streamPhase: "final_answer",
          updatedAt: "2025-01-01T00:00:03Z",
          latestText: "Done",
          transcriptBlocks: [
            {
              id: "commentary-1::commentary",
              streamId: "commentary-1",
              streamPhase: "commentary",
              text: "Inspecting files",
              updatedAt: "2025-01-01T00:00:01Z",
              completed: true,
              transient: true,
            },
            {
              id: "final-1::final_answer",
              streamId: "final-1",
              streamPhase: "final_answer",
              text: "Done",
              updatedAt: "2025-01-01T00:00:03Z",
              completed: true,
              transient: false,
            },
          ],
          activities: [
            { id: "tool-1", kind: "tool", status: "completed", summary: "rg -n reducer", ts: "2025-01-01T00:00:02Z" },
          ],
        },
        {
          actorId: "coder",
          pendingEventId: "evt-2",
          currentStreamId: "commentary-2",
          phase: "streaming",
          streamPhase: "commentary",
          updatedAt: "2025-01-01T00:00:05Z",
          latestText: "Writing fix",
          transcriptBlocks: [
            {
              id: "commentary-2::commentary",
              streamId: "commentary-2",
              streamPhase: "commentary",
              text: "Writing fix",
              updatedAt: "2025-01-01T00:00:05Z",
              completed: false,
              transient: true,
            },
          ],
          activities: [
            { id: "thinking-2", kind: "thinking", status: "started", summary: "Planning patch", ts: "2025-01-01T00:00:04Z" },
          ],
        },
      ],
    });

    expect(entries.map((entry) => entry.kind)).toEqual(["message", "activity", "message", "activity", "message"]);
    expect(entries.map((entry) => entry.pendingEventId)).toEqual(["evt-1", "evt-1", "evt-1", "evt-2", "evt-2"]);
    expect(entries[3]).toMatchObject({ kind: "activity", live: true, pendingEventId: "evt-2" });
    expect(entries[4]).toMatchObject({ kind: "message", live: true, pendingEventId: "evt-2" });
  });

  it("falls back to synthetic entries when only raw fallback text and activities exist", () => {
    const entries = buildHeadlessPreviewTimelineEntries({
      fallbackText: "Fallback output",
      fallbackActivities: [{ id: "activity-1", kind: "tool", status: "started", summary: "search docs", ts: "2025-01-01T00:00:00Z" }],
      fallbackUpdatedAt: "2025-01-01T00:00:01Z",
      fallbackPendingEventId: "fallback-1",
      fallbackStreamId: "stream-fallback",
      fallbackStreamPhase: "final_answer",
    });

    expect(entries).toHaveLength(2);
    expect(entries[0]).toMatchObject({ kind: "activity", pendingEventId: "fallback-1" });
    expect(entries[1]).toMatchObject({ kind: "message", pendingEventId: "fallback-1", streamPhase: "final_answer" });
  });
});