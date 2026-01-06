import { useEffect, useState } from "react";
import { apiJson } from "../../../services/api";
import { cardClass, inputClass, labelClass, primaryButtonClass, preClass } from "./types";

type PromptKind = "preamble" | "help" | "standup";

type PromptInfo = {
  kind: PromptKind;
  source: "repo" | "builtin";
  filename: string;
  path?: string | null;
  content: string;
};

type PromptsResponse = {
  scope_root?: string | null;
  preamble: PromptInfo;
  help: PromptInfo;
  standup: PromptInfo;
};

export function PromptsTab({ isDark, groupId }: { isDark: boolean; groupId?: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [scopeRoot, setScopeRoot] = useState<string | null>(null);
  const [prompts, setPrompts] = useState<Record<PromptKind, PromptInfo> | null>(null);
  const hasScope = Boolean(String(scopeRoot || "").trim());

  const load = async () => {
    if (!groupId) return;
    setBusy(true);
    setErr("");
    try {
      const resp = await apiJson<PromptsResponse>(`/api/v1/groups/${encodeURIComponent(groupId)}/prompts`);
      if (!resp.ok) {
        setErr(resp.error?.message || "Failed to load prompts");
        setPrompts(null);
        setScopeRoot(null);
        return;
      }
      setScopeRoot(resp.result?.scope_root ?? null);
      const p = resp.result?.preamble;
      const h = resp.result?.help;
      const s = resp.result?.standup;
      if (!p || !h || !s) {
        setErr("Invalid response");
        setPrompts(null);
        return;
      }
      setPrompts({ preamble: p, help: h, standup: s });
    } catch {
      setErr("Failed to load prompts");
      setPrompts(null);
      setScopeRoot(null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (groupId) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Load when groupId changes.
  }, [groupId]);

  const save = async (kind: PromptKind) => {
    if (!groupId || !prompts) return;
    if (!hasScope) {
      setErr("No scope attached to this group. Repo overrides are unavailable until you attach a scope.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const body = { content: prompts[kind].content, by: "user" };
      const resp = await apiJson<PromptInfo>(`/api/v1/groups/${encodeURIComponent(groupId)}/prompts/${kind}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        setErr(resp.error?.message || `Failed to save ${kind}`);
        return;
      }
      await load();
    } catch {
      setErr(`Failed to save ${kind}`);
    } finally {
      setBusy(false);
    }
  };

  const reset = async (kind: PromptKind) => {
    if (!groupId) return;
    if (!hasScope) {
      setErr("No scope attached to this group. Repo overrides are unavailable until you attach a scope.");
      return;
    }
    const filename = prompts?.[kind]?.filename || kind;
    const ok = window.confirm(`Reset ${kind}? This will delete ${filename} in your repo. This cannot be undone.`);
    if (!ok) return;

    setBusy(true);
    setErr("");
    try {
      const resp = await apiJson<PromptInfo>(
        `/api/v1/groups/${encodeURIComponent(groupId)}/prompts/${kind}?confirm=${encodeURIComponent(kind)}`,
        { method: "DELETE" }
      );
      if (!resp.ok) {
        setErr(resp.error?.message || `Failed to reset ${kind}`);
        return;
      }
      await load();
    } catch {
      setErr(`Failed to reset ${kind}`);
    } finally {
      setBusy(false);
    }
  };

  const setContent = (kind: PromptKind, content: string) => {
    if (!prompts) return;
    setPrompts({ ...prompts, [kind]: { ...prompts[kind], content } });
  };

  if (!groupId) {
    return (
      <div className={cardClass(isDark)}>
        <div className={`text-sm ${isDark ? "text-slate-300" : "text-gray-700"}`}>Open this tab from a group.</div>
      </div>
    );
  }

  const one = (kind: PromptKind, title: string, hint: string) => {
    const p = prompts?.[kind];
    const source = p?.source || "builtin";
    const badge =
      source === "repo"
        ? isDark
          ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
          : "bg-emerald-50 text-emerald-700 border border-emerald-200"
        : isDark
          ? "bg-slate-800 text-slate-300 border border-slate-700"
          : "bg-gray-100 text-gray-700 border border-gray-200";

    return (
      <div className={cardClass(isDark)}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{title}</div>
            <div className={`text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>{hint}</div>
          </div>
          <div className={`shrink-0 px-2 py-1 rounded-md text-[11px] ${badge}`}>{source === "repo" ? "Repo" : "Built-in"}</div>
        </div>

        {p?.path && (
          <div className={preClass(isDark)}>
            <span className="font-mono">{p.path}</span>
          </div>
        )}

        <div className="mt-3">
          <label className={labelClass(isDark)}>Markdown</label>
          <textarea
            className={`${inputClass(isDark)} font-mono text-[12px]`}
            style={{ minHeight: 160 }}
            value={p?.content || ""}
            onChange={(e) => setContent(kind, e.target.value)}
            spellCheck={false}
          />
        </div>

        <div className="mt-3 flex items-center gap-2">
          <button className={primaryButtonClass(busy)} onClick={() => save(kind)} disabled={busy}>
            Save
          </button>
          <button
            className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium disabled:opacity-50 ${
              isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
            }`}
            onClick={() => reset(kind)}
            disabled={busy || source !== "repo"}
            title={source === "repo" ? "Delete override file and fall back to built-in" : "No repo override file to delete"}
          >
            Reset
          </button>
          <button
            className={`ml-auto px-3 py-2 text-sm rounded-lg min-h-[44px] transition-colors disabled:opacity-50 ${
              isDark ? "bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-200"
            }`}
            onClick={load}
            disabled={busy}
            title="Reload from disk/server (discard local edits)"
          >
            Reload
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {err && <div className={`text-sm ${isDark ? "text-rose-300" : "text-red-600"}`}>{err}</div>}
      <div className={`text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>
        {hasScope ? (
          <>
            Repo overrides are stored in your active scope root: <span className="font-mono">{scopeRoot}</span>
          </>
        ) : (
          <>No scope attached. You can view built-in prompts, but cannot save repo overrides.</>
        )}
      </div>
      {one("preamble", "Preamble", "Injected automatically on the first delivery after start/restart. Override via CCCC_PREAMBLE.md.")}
      {one("help", "Help", "Returned by cccc_help. Override via CCCC_HELP.md.")}
      {one("standup", "Standup", "Periodic stand-up reminder template. Override via CCCC_STANDUP.md.")}
    </div>
  );
}
