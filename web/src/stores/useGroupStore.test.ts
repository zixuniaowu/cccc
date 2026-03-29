import { beforeEach, describe, expect, it, vi } from "vitest";

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

const SELECTED_GROUP_ID_KEY = "cccc-selected-group-id";
const localStorageMock = makeStorage();

vi.stubGlobal("localStorage", localStorageMock);

describe("useGroupStore selected group persistence", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorageMock.clear();
    vi.doUnmock("../services/api");
  });

  async function importStore(fetchGroups = vi.fn()) {
    vi.doMock("../services/api", () => ({
      fetchGroups,
    }));
    return await import("./useGroupStore");
  }

  it("initializes selectedGroupId from localStorage", async () => {
    localStorageMock.setItem(SELECTED_GROUP_ID_KEY, "g-2");

    const mod = await importStore();

    expect(mod.useGroupStore.getState().selectedGroupId).toBe("g-2");
  });

  it("persists explicit group selection changes", async () => {
    const mod = await importStore();

    mod.useGroupStore.getState().setSelectedGroupId("g-9");

    expect(localStorageMock.getItem(SELECTED_GROUP_ID_KEY)).toBe("g-9");
    expect(mod.useGroupStore.getState().selectedGroupId).toBe("g-9");
  });

  it("refreshGroups prefers the persisted selection when current state is empty", async () => {
    localStorageMock.setItem(SELECTED_GROUP_ID_KEY, "g-2");
    const fetchGroups = vi.fn().mockResolvedValue({
      ok: true,
      result: {
        groups: [
          { group_id: "g-1", title: "One", state: "idle", topic: "" },
          { group_id: "g-2", title: "Two", state: "active", topic: "" },
        ],
      },
    });
    const mod = await importStore(fetchGroups);
    mod.useGroupStore.setState({ selectedGroupId: "" });

    await mod.useGroupStore.getState().refreshGroups();

    expect(mod.useGroupStore.getState().selectedGroupId).toBe("g-2");
  });

  it("refreshGroups falls back to the first group when the persisted one no longer exists", async () => {
    localStorageMock.setItem(SELECTED_GROUP_ID_KEY, "g-missing");
    const fetchGroups = vi.fn().mockResolvedValue({
      ok: true,
      result: {
        groups: [
          { group_id: "g-1", title: "One", state: "idle", topic: "" },
          { group_id: "g-2", title: "Two", state: "active", topic: "" },
        ],
      },
    });
    const mod = await importStore(fetchGroups);

    await mod.useGroupStore.getState().refreshGroups();

    expect(mod.useGroupStore.getState().selectedGroupId).toBe("g-1");
    expect(localStorageMock.getItem(SELECTED_GROUP_ID_KEY)).toBe("g-1");
  });

  it("clears the persisted selection when no groups remain", async () => {
    localStorageMock.setItem(SELECTED_GROUP_ID_KEY, "g-2");
    const fetchGroups = vi.fn().mockResolvedValue({
      ok: true,
      result: {
        groups: [],
      },
    });
    const mod = await importStore(fetchGroups);

    await mod.useGroupStore.getState().refreshGroups();

    expect(mod.useGroupStore.getState().selectedGroupId).toBe("");
    expect(localStorageMock.getItem(SELECTED_GROUP_ID_KEY)).toBeNull();
  });
});
