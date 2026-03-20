import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchMock = vi.fn();

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

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

const sessionStorageMock = makeStorage();

vi.stubGlobal("fetch", fetchMock);
vi.stubGlobal("window", {
  location: { search: "" },
});
vi.stubGlobal("sessionStorage", sessionStorageMock);

describe("api.fetchActors", () => {
  beforeEach(() => {
    vi.resetModules();
    fetchMock.mockReset();
    sessionStorageMock.clear();
  });

  afterEach(async () => {
    const api = await import("../../src/services/api");
    api.clearAuthToken();
  });

  it("defaults to pure-read actors without unread query", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { actors: [] } }),
    });

    const api = await import("../../src/services/api");
    const resp = await api.fetchActors("g-demo");

    expect(resp.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/groups/g-demo/actors",
      expect.objectContaining({
        headers: expect.objectContaining({ "content-type": "application/json" }),
      }),
    );
  });

  it("keeps explicit unread requests on the slow path", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { actors: [] } }),
    });

    const api = await import("../../src/services/api");
    const resp = await api.fetchActors("g-demo", true);

    expect(resp.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/groups/g-demo/actors?include_unread=true",
      expect.objectContaining({
        headers: expect.objectContaining({ "content-type": "application/json" }),
      }),
    );
  });
});

describe("api bootstrap read cache", () => {
  beforeEach(() => {
    vi.resetModules();
    fetchMock.mockReset();
    sessionStorageMock.clear();
  });

  afterEach(async () => {
    const api = await import("../../src/services/api");
    api.clearAuthToken();
  });

  it("reuses a recent groups response inside the bootstrap window", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { groups: [{ group_id: "g-demo" }] } }),
    });

    const api = await import("../../src/services/api");
    const first = await api.fetchGroups();
    const second = await api.fetchGroups();

    expect(first.ok).toBe(true);
    expect(second.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("invalidates the recent groups response before group writes", async () => {
    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups" && method === "GET") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { groups: [{ group_id: `g-${fetchMock.mock.calls.length}` }] } }),
        });
      }
      if (path === "/api/v1/groups" && method === "POST") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { group_id: "g-new" } }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    await api.fetchGroups();
    await api.createGroup("New Group");
    await api.fetchGroups();

    const groupGets = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(groupGets).toHaveLength(2);
  });

  it("invalidates the recent groups response when auth token changes", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { groups: [{ group_id: "g-demo" }] } }),
    });

    const api = await import("../../src/services/api");
    await api.fetchGroups();
    api.setAuthToken("token-b");
    await api.fetchGroups();

    const groupGets = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(groupGets).toHaveLength(2);
  });

  it("allows event-driven callers to bypass the recent groups response", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { groups: [{ group_id: "g-demo" }] } }),
    });

    const api = await import("../../src/services/api");
    await api.fetchGroups();
    api.invalidateGroupsRead();
    await api.fetchGroups();

    const groupGets = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(groupGets).toHaveLength(2);
  });

  it("does not let an invalidated stale groups read repopulate the recent cache", async () => {
    const staleRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups" && method === "GET") {
        const callCount = fetchMock.mock.calls.filter(
          ([calledPath, calledInit]) =>
            calledPath === "/api/v1/groups" &&
            String((calledInit as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
        ).length;
        if (callCount === 1) {
          return staleRead.promise;
        }
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { groups: [{ group_id: `g-${callCount}` }] } }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const stalePromise = api.fetchGroups();
    await Promise.resolve();

    api.invalidateGroupsRead();
    staleRead.resolve({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { groups: [{ group_id: "g-stale" }] } }),
    });
    await stalePromise;

    await api.fetchGroups();
    await api.fetchGroups();

    const groupGets = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(groupGets).toHaveLength(2);
  });

  it("reuses a recent ping response per query variant", async () => {
    fetchMock.mockImplementation((path: string) => {
      if (path === "/api/v1/ping") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { version: "1.0.0" } }),
        });
      }
      if (path === "/api/v1/ping?include_home=1") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { version: "1.0.0", home: "/tmp/cccc" } }),
        });
      }
      return Promise.reject(new Error(`unexpected request: GET ${path}`));
    });

    const api = await import("../../src/services/api");
    await api.fetchPing();
    await api.fetchPing();
    await api.fetchPing({ includeHome: true });
    await api.fetchPing({ includeHome: true });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("invalidates the recent web access session response before token writes", async () => {
    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/web_access/session" && method === "GET") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: { web_access_session: { can_access_global_settings: fetchMock.mock.calls.length > 1 } },
            }),
        });
      }
      if (path === "/api/v1/access-tokens" && method === "POST") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: {
                access_token: {
                  token_id: "tok-1",
                  user_id: "user-1",
                  is_admin: false,
                  allowed_groups: [],
                  created_at: "2026-03-19T00:00:00Z",
                },
              },
            }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    await api.fetchWebAccessSession();
    await api.createAccessToken("user-1", false, []);
    await api.fetchWebAccessSession();

    const sessionGets = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/web_access/session" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(sessionGets).toHaveLength(2);
  });

  it("invalidates the recent web access session response when auth token changes", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: { web_access_session: { can_access_global_settings: true } },
        }),
    });

    const api = await import("../../src/services/api");
    await api.fetchWebAccessSession();
    api.clearAuthToken();
    await api.fetchWebAccessSession();

    const sessionGets = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/web_access/session" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(sessionGets).toHaveLength(2);
  });
});

describe("api.fetchGroupPrompts invalidation", () => {
  beforeEach(() => {
    vi.resetModules();
    fetchMock.mockReset();
    sessionStorageMock.clear();
  });

  afterEach(async () => {
    const api = await import("../../src/services/api");
    api.clearAuthToken();
  });

  it("clears the shared read request before updateGroupPrompt", async () => {
    const firstRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/prompts" && method === "GET") {
        if (
          fetchMock.mock.calls.filter(
            ([calledPath, calledInit]) =>
              calledPath === "/api/v1/groups/g-demo/prompts" &&
              String((calledInit as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
          ).length === 1
        ) {
          return firstRead.promise;
        }
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: {
                preamble: { kind: "preamble", source: "builtin", filename: "AGENTS.md", content: "next-preamble" },
                help: { kind: "help", source: "builtin", filename: "AGENTS.help.md", content: "next-help" },
              },
            }),
        });
      }
      if (path === "/api/v1/groups/g-demo/prompts/help" && method === "PUT") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: { kind: "help", source: "home", filename: "AGENTS.help.md", content: "updated-help" },
            }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const staleRead = api.fetchGroupPrompts("g-demo");
    await Promise.resolve();

    await api.updateGroupPrompt("g-demo", "help", "updated-help");
    await api.fetchGroupPrompts("g-demo");

    const getCalls = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups/g-demo/prompts" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(getCalls).toHaveLength(2);

    firstRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            preamble: { kind: "preamble", source: "builtin", filename: "AGENTS.md", content: "stale-preamble" },
            help: { kind: "help", source: "builtin", filename: "AGENTS.help.md", content: "stale-help" },
          },
        }),
    });
    await staleRead;
  });

  it("clears the shared read request before resetGroupPrompt", async () => {
    const firstRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/prompts" && method === "GET") {
        if (
          fetchMock.mock.calls.filter(
            ([calledPath, calledInit]) =>
              calledPath === "/api/v1/groups/g-demo/prompts" &&
              String((calledInit as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
          ).length === 1
        ) {
          return firstRead.promise;
        }
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: {
                preamble: {
                  kind: "preamble",
                  source: "builtin",
                  filename: "AGENTS.md",
                  content: "builtin-preamble",
                },
                help: { kind: "help", source: "builtin", filename: "AGENTS.help.md", content: "builtin-help" },
              },
            }),
        });
      }
      if (path === "/api/v1/groups/g-demo/prompts/help?confirm=help" && method === "DELETE") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: { kind: "help", source: "builtin", filename: "AGENTS.help.md", content: "builtin-help" },
            }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const staleRead = api.fetchGroupPrompts("g-demo");
    await Promise.resolve();

    await api.resetGroupPrompt("g-demo", "help");
    await api.fetchGroupPrompts("g-demo");

    const getCalls = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups/g-demo/prompts" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(getCalls).toHaveLength(2);

    firstRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            preamble: { kind: "preamble", source: "home", filename: "AGENTS.md", content: "stale-preamble" },
            help: { kind: "help", source: "home", filename: "AGENTS.help.md", content: "stale-help" },
          },
        }),
    });
    await staleRead;
  });
});

describe("api.fetchContext dedupe", () => {
  beforeEach(() => {
    vi.resetModules();
    fetchMock.mockReset();
    sessionStorageMock.clear();
  });

  afterEach(async () => {
    const api = await import("../../src/services/api");
    api.clearAuthToken();
  });

  it("reuses the in-flight context request for the same group", async () => {
    const firstRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/context" && method === "GET") {
        return firstRead.promise;
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const req1 = api.fetchContext("g-demo");
    const req2 = api.fetchContext("g-demo");

    expect(fetchMock).toHaveBeenCalledTimes(1);

    firstRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 0, planned: 0, active: 0, done: 0, archived: 0 },
            meta: {},
          },
        }),
    });

    const [resp1, resp2] = await Promise.all([req1, req2]);
    expect(resp1.ok).toBe(true);
    expect(resp2.ok).toBe(true);
  });

  it("clears the shared context request before contextSync", async () => {
    const firstRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/context" && method === "GET") {
        if (
          fetchMock.mock.calls.filter(
            ([calledPath, calledInit]) =>
              calledPath === "/api/v1/groups/g-demo/context" &&
              String((calledInit as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
          ).length === 1
        ) {
          return firstRead.promise;
        }
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: {
                coordination: { tasks: [] },
                agent_states: [],
                attention: null,
                board: null,
                tasks_summary: { total: 1, planned: 1, active: 0, done: 0, archived: 0 },
                meta: {},
              },
            }),
        });
      }
      if (path === "/api/v1/groups/g-demo/context" && method === "POST") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { version: "next" } }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const staleRead = api.fetchContext("g-demo");
    await Promise.resolve();

    await api.contextSync("g-demo", [{ op: "coordination.brief.update", current_focus: "updated" }]);
    await api.fetchContext("g-demo");

    const getCalls = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups/g-demo/context" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(getCalls).toHaveLength(2);

    firstRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 0, planned: 0, active: 0, done: 0, archived: 0 },
            meta: {},
          },
        }),
    });
    await staleRead;
  });

  it("bypasses shared context inflight when fresh is requested", async () => {
    const firstRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/context" && method === "GET") {
        return firstRead.promise;
      }
      if (typeof path === "string" && path.startsWith("/api/v1/groups/g-demo/context?fresh=1") && method === "GET") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: {
                coordination: { tasks: [] },
                agent_states: [],
                attention: null,
                board: null,
                tasks_summary: { total: 1, planned: 1, active: 0, done: 0, archived: 0 },
                meta: { version: "fresh" },
              },
            }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const staleRead = api.fetchContext("g-demo");
    await Promise.resolve();

    const freshRead = await api.fetchContext("g-demo", { fresh: true });
    expect(freshRead.ok).toBe(true);
    expect(
      fetchMock.mock.calls.filter(([path]) => String(path).startsWith("/api/v1/groups/g-demo/context")),
    ).toHaveLength(2);

    firstRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 0, planned: 0, active: 0, done: 0, archived: 0 },
            meta: { version: "stale" },
          },
        }),
    });
    await staleRead;
  });

  it("invalidates the shared context read for external write notifications", async () => {
    const firstRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/context" && method === "GET") {
        if (
          fetchMock.mock.calls.filter(
            ([calledPath, calledInit]) =>
              calledPath === "/api/v1/groups/g-demo/context" &&
              String((calledInit as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
          ).length === 1
        ) {
          return firstRead.promise;
        }
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () =>
            JSON.stringify({
              ok: true,
              result: {
                coordination: { tasks: [] },
                agent_states: [],
                attention: null,
                board: null,
                tasks_summary: { total: 1, planned: 1, active: 0, done: 0, archived: 0 },
                meta: { version: "fresh-after-external-write" },
              },
            }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const staleRead = api.fetchContext("g-demo");
    await Promise.resolve();

    api.invalidateContextRead("g-demo");
    const freshRead = await api.fetchContext("g-demo");
    expect(freshRead.ok).toBe(true);
    if (freshRead.ok) {
      expect(freshRead.result.meta?.version).toBe("fresh-after-external-write");
    }

    const getCalls = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups/g-demo/context" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(getCalls).toHaveLength(2);

    firstRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 0, planned: 0, active: 0, done: 0, archived: 0 },
            meta: { version: "stale" },
          },
        }),
    });
    await staleRead;
  });

  it("reuses an in-flight fresh context request for follow-up reads", async () => {
    const staleRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();
    const freshRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/context" && method === "GET") {
        return staleRead.promise;
      }
      if (typeof path === "string" && path.startsWith("/api/v1/groups/g-demo/context?fresh=1") && method === "GET") {
        return freshRead.promise;
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const stalePromise = api.fetchContext("g-demo");
    await Promise.resolve();

    const freshPromise = api.fetchContext("g-demo", { fresh: true });
    await Promise.resolve();
    const followerPromise = api.fetchContext("g-demo");

    expect(
      fetchMock.mock.calls.filter(([path]) => String(path).startsWith("/api/v1/groups/g-demo/context")),
    ).toHaveLength(2);

    freshRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 1, planned: 1, active: 0, done: 0, archived: 0 },
            meta: { version: "fresh-shared" },
          },
        }),
    });

    const [freshResp, followerResp] = await Promise.all([freshPromise, followerPromise]);
    expect(freshResp.ok).toBe(true);
    expect(followerResp.ok).toBe(true);
    if (followerResp.ok) {
      expect(followerResp.result.meta?.version).toBe("fresh-shared");
    }

    staleRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 0, planned: 0, active: 0, done: 0, archived: 0 },
            meta: { version: "stale" },
          },
        }),
    });
    await stalePromise;
  });

  it("does not let an older request finally clear a newer shared mapping", async () => {
    const staleRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();
    const freshRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();
    const postFreshRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/context" && method === "GET") {
        const samePathCalls = fetchMock.mock.calls.filter(
          ([calledPath, calledInit]) =>
            calledPath === "/api/v1/groups/g-demo/context" &&
            String((calledInit as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
        ).length;
        if (samePathCalls === 1) return staleRead.promise;
        if (samePathCalls === 2) return postFreshRead.promise;
      }
      if (typeof path === "string" && path.startsWith("/api/v1/groups/g-demo/context?fresh=1") && method === "GET") {
        return freshRead.promise;
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const stalePromise = api.fetchContext("g-demo");
    await Promise.resolve();

    const freshPromise = api.fetchContext("g-demo", { fresh: true });
    await Promise.resolve();

    staleRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 0, planned: 0, active: 0, done: 0, archived: 0 },
            meta: { version: "stale" },
          },
        }),
    });
    await stalePromise;

    const afterStaleWhileFreshInFlight = api.fetchContext("g-demo");
    expect(
      fetchMock.mock.calls.filter(([path]) => String(path).startsWith("/api/v1/groups/g-demo/context")),
    ).toHaveLength(2);

    freshRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 1, planned: 1, active: 0, done: 0, archived: 0 },
            meta: { version: "fresh" },
          },
        }),
    });

    const [freshResp, followerResp] = await Promise.all([freshPromise, afterStaleWhileFreshInFlight]);
    expect(freshResp.ok).toBe(true);
    expect(followerResp.ok).toBe(true);
    if (followerResp.ok) {
      expect(followerResp.result.meta?.version).toBe("fresh");
    }

    const postFreshPromise = api.fetchContext("g-demo");
    expect(
      fetchMock.mock.calls.filter(([path]) => String(path).startsWith("/api/v1/groups/g-demo/context")),
    ).toHaveLength(3);

    postFreshRead.resolve({
      status: 200,
      ok: true,
      text: async () =>
        JSON.stringify({
          ok: true,
          result: {
            coordination: { tasks: [] },
            agent_states: [],
            attention: null,
            board: null,
            tasks_summary: { total: 2, planned: 2, active: 0, done: 0, archived: 0 },
            meta: { version: "post-fresh" },
          },
        }),
    });

    const postFreshResp = await postFreshPromise;
    expect(postFreshResp.ok).toBe(true);
    if (postFreshResp.ok) {
      expect(postFreshResp.result.meta?.version).toBe("post-fresh");
    }
  });
});

describe("api.fetchActors invalidation", () => {
  beforeEach(() => {
    vi.resetModules();
    fetchMock.mockReset();
    sessionStorageMock.clear();
  });

  afterEach(async () => {
    const api = await import("../../src/services/api");
    api.clearAuthToken();
  });

  it("clears the shared pure-read request before actor writes", async () => {
    const firstRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/actors" && method === "GET") {
        if (
          fetchMock.mock.calls.filter(
            ([calledPath, calledInit]) =>
              calledPath === "/api/v1/groups/g-demo/actors" &&
              String((calledInit as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
          ).length === 1
        ) {
          return firstRead.promise;
        }
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { actors: [{ id: "peer-new" }] } }),
        });
      }
      if (path === "/api/v1/groups/g-demo/actors/peer-1" && method === "POST") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { actor: { id: "peer-1" } } }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const staleRead = api.fetchActors("g-demo", false);
    await Promise.resolve();

    await api.updateActor("g-demo", "peer-1", "codex");
    await api.fetchActors("g-demo", false);

    const getCalls = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups/g-demo/actors" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(getCalls).toHaveLength(2);

    firstRead.resolve({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { actors: [{ id: "peer-old" }] } }),
    });
    await staleRead;
  });

  it("clears the shared pure-read request before group lifecycle writes", async () => {
    const firstRead = createDeferred<{
      status: number;
      ok: boolean;
      text: () => Promise<string>;
    }>();

    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      const method = String(init?.method || "GET").toUpperCase();
      if (path === "/api/v1/groups/g-demo/actors" && method === "GET") {
        if (
          fetchMock.mock.calls.filter(
            ([calledPath, calledInit]) =>
              calledPath === "/api/v1/groups/g-demo/actors" &&
              String((calledInit as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
          ).length === 1
        ) {
          return firstRead.promise;
        }
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: { actors: [{ id: "peer-started" }] } }),
        });
      }
      if (path === "/api/v1/groups/g-demo/start?by=user" && method === "POST") {
        return Promise.resolve({
          status: 200,
          ok: true,
          text: async () => JSON.stringify({ ok: true, result: {} }),
        });
      }
      return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
    });

    const api = await import("../../src/services/api");
    const staleRead = api.fetchActors("g-demo", false);
    await Promise.resolve();

    await api.startGroup("g-demo");
    await api.fetchActors("g-demo", false);

    const getCalls = fetchMock.mock.calls.filter(
      ([path, init]) =>
        path === "/api/v1/groups/g-demo/actors" &&
        String((init as RequestInit | undefined)?.method || "GET").toUpperCase() === "GET",
    );
    expect(getCalls).toHaveLength(2);

    firstRead.resolve({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { actors: [{ id: "peer-stale" }] } }),
    });
    await staleRead;
  });
});
