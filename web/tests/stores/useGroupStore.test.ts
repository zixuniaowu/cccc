import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../src/services/api", () => ({
  fetchActors: vi.fn(),
  fetchGroup: vi.fn(),
  fetchLedgerTail: vi.fn(),
  fetchContext: vi.fn(),
  fetchSettings: vi.fn(),
  fetchGroups: vi.fn(),
}));

function makeStorage() {
  const data = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      data.set(key, String(value));
    }),
    removeItem: vi.fn((key: string) => {
      data.delete(key);
    }),
    clear: vi.fn(() => {
      data.clear();
    }),
  };
}

const localStorageMock = makeStorage();
vi.stubGlobal("localStorage", localStorageMock);

let useGroupStore: typeof import("../../src/stores/useGroupStore").useGroupStore;
let api: typeof import("../../src/services/api");

beforeAll(async () => {
  api = await import("../../src/services/api");
  ({ useGroupStore } = await import("../../src/stores/useGroupStore"));
});

describe("useGroupStore actors fetch policy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorageMock.clear();
    useGroupStore.setState({
      groups: [{ group_id: "g-demo", title: "Demo", topic: "", state: "active" }],
      groupOrder: ["g-demo"],
      selectedGroupId: "g-demo",
      chatByGroup: {},
      groupDoc: null,
      events: [],
      chatWindow: null,
      actors: [],
      groupContext: null,
      groupSettings: null,
      runtimes: [],
      hasMoreHistory: true,
      isLoadingHistory: false,
      isChatWindowLoading: false,
    });

    vi.mocked(api.fetchActors).mockResolvedValue({ ok: true, result: { actors: [] } });
    vi.mocked(api.fetchGroup).mockImplementation(
      async (groupId: string) =>
        ({
          ok: true,
          result: {
            group: {
              group_id: groupId,
              title: String(groupId || ""),
              topic: "",
              state: "active",
            },
          },
        }) as Awaited<ReturnType<typeof api.fetchGroup>>,
    );
    vi.mocked(api.fetchLedgerTail).mockResolvedValue({
      ok: true,
      result: { events: [], has_more: false },
    } as Awaited<ReturnType<typeof api.fetchLedgerTail>>);
    vi.mocked(api.fetchContext).mockResolvedValue({
      ok: true,
      result: null,
    } as Awaited<ReturnType<typeof api.fetchContext>>);
    vi.mocked(api.fetchSettings).mockResolvedValue({
      ok: true,
      result: { settings: null },
    } as Awaited<ReturnType<typeof api.fetchSettings>>);
    vi.mocked(api.fetchGroups).mockResolvedValue({
      ok: true,
      result: { groups: [] },
    } as Awaited<ReturnType<typeof api.fetchGroups>>);
  });

  it("refreshActors explicitly requests unread counts", async () => {
    await useGroupStore.getState().refreshActors("g-demo");
    expect(api.fetchActors).toHaveBeenCalledWith("g-demo", true);
  });

  it("loadGroup keeps unread counts on the selected group path", async () => {
    await useGroupStore.getState().loadGroup("g-demo");
    expect(api.fetchActors).toHaveBeenCalledWith("g-demo", true);
  });

  it("warmGroup preserves unread counts in the prefetched actor snapshot", async () => {
    const warmGroupId = "g-warm";
    useGroupStore.setState({
      selectedGroupId: "g-current",
      groups: [
        { group_id: "g-current", title: "Current", topic: "", state: "active" },
        { group_id: warmGroupId, title: "Warm", topic: "", state: "active" },
      ],
      groupOrder: ["g-current", warmGroupId],
    });

    await useGroupStore.getState().warmGroup(warmGroupId);
    expect(api.fetchActors).toHaveBeenCalledWith(warmGroupId, true);
  });
});
