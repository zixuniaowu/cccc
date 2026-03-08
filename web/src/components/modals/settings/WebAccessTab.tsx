import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { GroupMeta, RemoteAccessState, WebAccessSession } from "../../../types";
import * as api from "../../../services/api";
import { cardClass, inputClass, labelClass, primaryButtonClass } from "./types";

interface WebAccessTabProps {
  isDark: boolean;
  isActive?: boolean;
}

type WebModeState = {
  mode?: string;
  read_only?: boolean;
};

function statusChipClass(isDark: boolean, tone: "neutral" | "good" | "warn") {
  if (tone === "good") {
    return isDark
      ? "border-emerald-700/60 bg-emerald-900/30 text-emerald-300"
      : "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (tone === "warn") {
    return isDark
      ? "border-amber-700/60 bg-amber-900/30 text-amber-300"
      : "border-amber-200 bg-amber-50 text-amber-700";
  }
  return isDark
    ? "border-slate-700 bg-slate-800 text-slate-300"
    : "border-gray-200 bg-gray-50 text-gray-700";
}

async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const el = document.createElement("textarea");
    el.value = value;
    el.style.position = "fixed";
    el.style.left = "-9999px";
    document.body.appendChild(el);
    el.focus();
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    return ok;
  }
}

export function WebAccessTab({ isDark, isActive = true }: WebAccessTabProps) {
  const { t } = useTranslation("settings");

  const [remoteState, setRemoteState] = useState<RemoteAccessState | null>(null);
  const [webMode, setWebMode] = useState<WebModeState | null>(null);
  const [accessTokens, setAccessTokens] = useState<api.AccessTokenEntry[]>([]);
  const [session, setSession] = useState<WebAccessSession | null>(null);
  const [groups, setGroups] = useState<GroupMeta[]>([]);

  const [busy, setBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [startBusy, setStartBusy] = useState(false);
  const [stopBusy, setStopBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");

  const [provider, setProvider] = useState<"off" | "manual" | "tailscale">("off");
  const [mode, setMode] = useState("tailnet_only");
  const [requireAccessToken, setRequireAccessToken] = useState(true);
  const [webHost, setWebHost] = useState("127.0.0.1");
  const [webPort, setWebPort] = useState("8848");
  const [webPublicUrl, setWebPublicUrl] = useState("");

  const [userId, setUserId] = useState("");
  const [customToken, setCustomToken] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [scopeAllGroups, setScopeAllGroups] = useState(true);
  const [selectedGroups, setSelectedGroups] = useState<Set<string>>(new Set());
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState("");
  const [newToken, setNewToken] = useState<string | null>(null);
  const [copiedNewToken, setCopiedNewToken] = useState(false);

  const [editingTokenId, setEditingTokenId] = useState<string | null>(null);
  const [editGroups, setEditGroups] = useState<Set<string>>(new Set());
  const [editBusy, setEditBusy] = useState(false);
  const [copiedTokenId, setCopiedTokenId] = useState<string | null>(null);

  const accessTokenCount = accessTokens.length;
  const hasAdminToken = accessTokens.some((item) => item.is_admin);
  const effectiveRequireAccessToken = provider === "off" ? accessTokenCount > 0 : requireAccessToken;

  const pushHint = (value: string) => {
    setHint(value);
    window.setTimeout(() => setHint(""), 1800);
  };

  const load = async () => {
    if (!isActive) return;
    setBusy(true);
    setError("");
    try {
      const [pingResp, remoteResp, tokensResp, groupsResp, sessionResp] = await Promise.all([
        api.fetchPing(),
        api.fetchRemoteAccessState(),
        api.fetchAccessTokens(),
        api.fetchGroups(),
        api.fetchWebAccessSession(),
      ]);
      if (pingResp.ok) {
        setWebMode(pingResp.result?.web || null);
      }
      if (remoteResp.ok && remoteResp.result?.remote_access) {
        const state = remoteResp.result.remote_access;
        setRemoteState(state);
        setProvider((state.provider as "off" | "manual" | "tailscale") || "off");
        setMode(String(state.mode || "tailnet_only"));
        setRequireAccessToken(Boolean(state.require_access_token ?? true));
        setWebHost(String(state.config?.web_host || state.diagnostics?.web_host || "127.0.0.1"));
        setWebPort(String(state.config?.web_port || state.diagnostics?.web_port || 8848));
        setWebPublicUrl(String(state.config?.web_public_url || state.diagnostics?.web_public_url || ""));
      } else if (!remoteResp.ok) {
        setError(remoteResp.error?.message || t("webAccess.loadFailed"));
      }
      if (tokensResp.ok && tokensResp.result?.access_tokens) {
        setAccessTokens(tokensResp.result.access_tokens);
      } else if (!tokensResp.ok) {
        setError(tokensResp.error?.message || t("webAccess.loadFailed"));
      }
      if (groupsResp.ok && groupsResp.result?.groups) {
        setGroups(groupsResp.result.groups);
      }
      if (sessionResp.ok && sessionResp.result?.web_access_session) {
        setSession(sessionResp.result.web_access_session);
      }
    } catch {
      setError(t("webAccess.loadFailed"));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive]);

  useEffect(() => {
    if (!scopeAllGroups) return;
    setSelectedGroups(new Set());
  }, [scopeAllGroups]);

  const reachabilitySummary = useMemo(() => {
    if (!remoteState || provider === "off") {
      return { label: t("webAccess.summary.localOnly"), detail: t("webAccess.summary.localOnlyHint"), tone: "neutral" as const };
    }
    if (remoteState.status === "running") {
      return {
        label: t("webAccess.summary.remoteEnabled"),
        detail: remoteState.endpoint || t("webAccess.summary.remoteEnabledHint", { provider: provider === "tailscale" ? "Tailscale" : t("webAccess.providers.manual") }),
        tone: "good" as const,
      };
    }
    return { label: t("webAccess.summary.remoteNeedsAttention"), detail: t("webAccess.summary.remoteNeedsAttentionHint"), tone: "warn" as const };
  }, [provider, remoteState, t]);

  const accessSummary = useMemo(() => {
    if (accessTokenCount <= 0) {
      return { label: t("webAccess.summary.open"), detail: t("webAccess.summary.openHint"), tone: "neutral" as const };
    }
    return {
      label: t("webAccess.summary.protected"),
      detail: t("webAccess.summary.protectedHint", { count: accessTokenCount }),
      tone: "good" as const,
    };
  }, [accessTokenCount, t]);

  const modeSummary = useMemo(() => {
    const exhibit = Boolean(webMode?.read_only) || String(webMode?.mode || "").toLowerCase() === "exhibit";
    return exhibit
      ? { label: t("webAccess.summary.exhibit"), detail: t("webAccess.summary.exhibitHint"), tone: "warn" as const }
      : { label: t("webAccess.summary.normal"), detail: t("webAccess.summary.normalHint"), tone: "good" as const };
  }, [t, webMode]);

  const handleSaveReachability = async () => {
    setSaveBusy(true);
    setError("");
    try {
      const parsedPort = Number.parseInt(webPort, 10);
      if (!Number.isFinite(parsedPort) || parsedPort <= 0 || parsedPort > 65535) {
        setError(t("webAccess.invalidPort"));
        return;
      }
      const resp = await api.updateRemoteAccessConfig({
        provider,
        mode,
        requireAccessToken,
        webHost,
        webPort: parsedPort,
        webPublicUrl,
      });
      if (!resp.ok || !resp.result?.remote_access) {
        setError(resp.error?.message || t("webAccess.saveFailed"));
        return;
      }
      setRemoteState(resp.result.remote_access);
      pushHint(t("common:saved"));
    } catch {
      setError(t("webAccess.saveFailed"));
    } finally {
      setSaveBusy(false);
    }
  };

  const handleStart = async () => {
    setStartBusy(true);
    setError("");
    try {
      const resp = await api.startRemoteAccess();
      if (!resp.ok || !resp.result?.remote_access) {
        setError(resp.error?.message || t("webAccess.startFailed"));
        return;
      }
      setRemoteState(resp.result.remote_access);
      pushHint(t("webAccess.started"));
    } catch {
      setError(t("webAccess.startFailed"));
    } finally {
      setStartBusy(false);
    }
  };

  const handleStop = async () => {
    setStopBusy(true);
    setError("");
    try {
      const resp = await api.stopRemoteAccess();
      if (!resp.ok || !resp.result?.remote_access) {
        setError(resp.error?.message || t("webAccess.stopFailed"));
        return;
      }
      setRemoteState(resp.result.remote_access);
      pushHint(t("webAccess.stopped"));
    } catch {
      setError(t("webAccess.stopFailed"));
    } finally {
      setStopBusy(false);
    }
  };

  const handleCreateAccessToken = async () => {
    const trimmedUserId = userId.trim();
    if (!trimmedUserId) {
      setCreateError(t("webAccess.userIdRequired"));
      return;
    }
    setCreateBusy(true);
    setCreateError("");
    try {
      const effectiveAdmin = !hasAdminToken || isAdmin;
      const allowedGroups = scopeAllGroups ? [] : [...selectedGroups];
      const resp = await api.createAccessToken(trimmedUserId, effectiveAdmin, allowedGroups, customToken.trim() || undefined);
      if (!resp.ok || !resp.result?.access_token) {
        setCreateError(resp.error?.message || t("webAccess.createFailed"));
        return;
      }
      const created = resp.result.access_token;
      if (created.token) {
        setNewToken(created.token);
        api.setAuthToken(created.token);
      }
      setUserId("");
      setCustomToken("");
      setIsAdmin(false);
      setScopeAllGroups(true);
      setSelectedGroups(new Set());
      await load();
    } catch {
      setCreateError(t("webAccess.createFailed"));
    } finally {
      setCreateBusy(false);
    }
  };

  const handleDeleteAccessToken = async (tokenId: string) => {
    if (!tokenId || !window.confirm(t("webAccess.deleteConfirm"))) return;
    try {
      const resp = await api.deleteAccessToken(tokenId);
      if (!resp.ok) {
        setError(resp.error?.message || t("webAccess.deleteFailed"));
        return;
      }
      if (!resp.result?.access_tokens_remain) {
        document.cookie = "cccc_access_token=; path=/; max-age=0";
        api.clearAuthToken();
        window.location.reload();
        return;
      }
      await load();
    } catch {
      setError(t("webAccess.deleteFailed"));
    }
  };

  const startEdit = (entry: api.AccessTokenEntry) => {
    setEditingTokenId(entry.token_id || null);
    setEditGroups(new Set(entry.allowed_groups || []));
  };

  const saveEdit = async () => {
    if (!editingTokenId) return;
    setEditBusy(true);
    try {
      const resp = await api.updateAccessToken(editingTokenId, { allowed_groups: [...editGroups] });
      if (!resp.ok) {
        setError(resp.error?.message || t("webAccess.updateFailed"));
        return;
      }
      setEditingTokenId(null);
      setEditGroups(new Set());
      await load();
    } catch {
      setError(t("webAccess.updateFailed"));
    } finally {
      setEditBusy(false);
    }
  };

  const revealAndCopy = async (tokenId: string) => {
    try {
      const resp = await api.revealAccessToken(tokenId);
      if (!resp.ok || !resp.result?.token) {
        setError(resp.error?.message || t("webAccess.revealFailed"));
        return;
      }
      const ok = await copyText(resp.result.token);
      if (ok) {
        setCopiedTokenId(tokenId);
        window.setTimeout(() => setCopiedTokenId(null), 1500);
      }
    } catch {
      setError(t("webAccess.revealFailed"));
    }
  };

  const copyNewToken = async () => {
    if (!newToken) return;
    const ok = await copyText(newToken);
    if (ok) {
      setCopiedNewToken(true);
      window.setTimeout(() => setCopiedNewToken(false), 1500);
    }
  };

  const remoteStatusTone: "neutral" | "good" | "warn" =
    remoteState?.status === "running"
      ? "good"
      : remoteState?.status && remoteState.status !== "stopped"
        ? "warn"
        : "neutral";
  const remoteStatusLabel = t(`webAccess.status.${remoteState?.status || "stopped"}`);

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{t("webAccess.title")}</h3>
          <p className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>{t("webAccess.description")}</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={busy || saveBusy || startBusy || stopBusy || createBusy || editBusy}
          className={`px-3 py-2 rounded-lg text-xs min-h-[40px] transition-colors ${
            isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white border border-gray-200 hover:bg-gray-50 text-gray-800"
          } disabled:opacity-50`}
        >
          {busy ? t("common:loading") : t("webAccess.refresh")}
        </button>
      </div>

      <div className={`rounded-lg border px-4 py-3 ${isDark ? "border-sky-500/20 bg-sky-500/10" : "border-sky-200 bg-sky-50"}`}>
        <div className={`text-sm font-semibold ${isDark ? "text-sky-100" : "text-sky-900"}`}>{t("webAccess.howItWorksTitle")}</div>
        <ul className={`mt-2 space-y-1.5 text-xs leading-6 ${isDark ? "text-sky-100/85" : "text-sky-800"}`}>
          <li>• {t("webAccess.howItWorksActivateLogin")}</li>
          <li>• {t("webAccess.howItWorksAutoLogin")}</li>
          <li>• {t("webAccess.howItWorksRemotePolicy")}</li>
        </ul>
      </div>

      {(error || hint) && (
        <div className={`rounded-lg border px-3 py-2 text-xs ${error
          ? isDark ? "border-red-500/30 bg-red-500/10 text-red-200" : "border-red-200 bg-red-50 text-red-700"
          : isDark ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-emerald-200 bg-emerald-50 text-emerald-700"
        }`}>
          {error || hint}
        </div>
      )}

      <div className={`rounded-lg border px-4 py-3 ${isDark ? "border-violet-500/20 bg-violet-500/10" : "border-violet-200 bg-violet-50"}`}>
        <div className={`text-sm font-semibold ${isDark ? "text-violet-100" : "text-violet-900"}`}>{t("webAccess.currentBrowserTitle")}</div>
        <div className={`mt-2 inline-flex rounded-full border px-2.5 py-1 text-xs ${statusChipClass(
          isDark,
          !session?.login_active ? "neutral" : session?.current_browser_signed_in ? "good" : "warn",
        )}`}>
          {!session?.login_active
            ? t("webAccess.currentBrowserOpen")
            : session?.current_browser_signed_in
              ? t("webAccess.currentBrowserSignedIn")
              : t("webAccess.currentBrowserNotSignedIn")}
        </div>
        <div className={`mt-3 text-xs leading-6 ${isDark ? "text-violet-100/85" : "text-violet-800"}`}>
          {!session?.login_active
            ? t("webAccess.currentBrowserOpenHint")
            : session?.current_browser_signed_in
              ? t("webAccess.currentBrowserSignedInHint", {
                  userId: session?.user_id || t("webAccess.unknownUser"),
                  role: session?.is_admin ? t("webAccess.adminBadge") : t("webAccess.scopedBadge"),
                })
              : t("webAccess.currentBrowserNotSignedInHint")}
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        {[
          { title: t("webAccess.cards.reachability"), ...reachabilitySummary },
          { title: t("webAccess.cards.accessControl"), ...accessSummary },
          { title: t("webAccess.cards.interactionMode"), ...modeSummary },
        ].map((item) => (
          <div key={item.title} className={cardClass(isDark)}>
            <div className={`text-[11px] uppercase tracking-wide ${isDark ? "text-slate-500" : "text-gray-500"}`}>{item.title}</div>
            <div className={`mt-2 inline-flex rounded-full border px-2.5 py-1 text-xs ${statusChipClass(isDark, item.tone)}`}>{item.label}</div>
            <div className={`mt-3 text-sm leading-6 ${isDark ? "text-slate-300" : "text-gray-700"}`}>{item.detail}</div>
          </div>
        ))}
      </div>

      <section className={cardClass(isDark)}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{t("webAccess.accessControlTitle")}</h4>
            <p className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>{t("webAccess.accessControlDescription")}</p>
          </div>
          <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>
            {t("webAccess.tokenCount", { count: accessTokenCount })}
          </div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,340px)_1fr]">
          <div className="space-y-3">
            <div>
              <label className={labelClass(isDark)}>{t("webAccess.userIdLabel")}</label>
              <input value={userId} onChange={(e) => setUserId(e.target.value)} className={inputClass(isDark)} placeholder={t("webAccess.userIdPlaceholder")} />
            </div>


            <label className={`flex items-start gap-3 rounded-lg border px-3 py-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
              <input
                type="checkbox"
                checked={!hasAdminToken || isAdmin}
                onChange={(e) => setIsAdmin(e.target.checked)}
                disabled={!hasAdminToken}
                className="mt-1"
              />
              <div>
                <div className={`text-sm font-medium ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("webAccess.adminTokenLabel")}</div>
                <div className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                  {!hasAdminToken ? t("webAccess.firstTokenAdminHint") : t("webAccess.adminTokenHint")}
                </div>
              </div>
            </label>

            <label className={`flex items-start gap-3 rounded-lg border px-3 py-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
              <input type="checkbox" checked={scopeAllGroups} onChange={(e) => setScopeAllGroups(e.target.checked)} className="mt-1" />
              <div>
                <div className={`text-sm font-medium ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("webAccess.allGroupsLabel")}</div>
                <div className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>{t("webAccess.allGroupsHint")}</div>
              </div>
            </label>

            {!scopeAllGroups && (
              <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
                <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("webAccess.groupScopeTitle")}</div>
                <div className="mt-2 max-h-44 overflow-y-auto space-y-2 pr-1">
                  {groups.length === 0 ? (
                    <div className={`text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("webAccess.noGroups")}</div>
                  ) : groups.map((group) => {
                    const groupId = group.group_id;
                    return (
                      <label key={groupId} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={selectedGroups.has(groupId)}
                          onChange={(e) => {
                            setSelectedGroups((prev) => {
                              const next = new Set(prev);
                              if (e.target.checked) next.add(groupId); else next.delete(groupId);
                              return next;
                            });
                          }}
                        />
                        <span className={isDark ? "text-slate-200" : "text-gray-800"}>{group.title || groupId}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}

            <div>
              <label className={labelClass(isDark)}>{t("webAccess.customTokenLabel")}</label>
              <input value={customToken} onChange={(e) => setCustomToken(e.target.value)} className={inputClass(isDark)} placeholder={t("webAccess.customTokenPlaceholder")} />
            </div>

            {createError && <div className={`text-xs ${isDark ? "text-red-300" : "text-red-600"}`}>{createError}</div>}

            <button type="button" onClick={() => void handleCreateAccessToken()} disabled={createBusy} className={primaryButtonClass(createBusy)}>
              {createBusy ? t("webAccess.creating") : t("webAccess.createAccessToken")}
            </button>

            {newToken && (
              <div className={`rounded-lg border p-3 ${isDark ? "border-emerald-500/30 bg-emerald-500/10" : "border-emerald-200 bg-emerald-50"}`}>
                <div className={`text-sm font-semibold ${isDark ? "text-emerald-100" : "text-emerald-800"}`}>{t("webAccess.newTokenTitle")}</div>
                <div className={`mt-1 text-xs break-all ${isDark ? "text-emerald-200" : "text-emerald-700"}`}>{newToken}</div>
                <div className={`mt-2 space-y-1 text-xs ${isDark ? "text-emerald-200/80" : "text-emerald-700"}`}>
                  <div>{t("webAccess.newTokenHint")}</div>
                  <div>{t("webAccess.newTokenAutoLoginHint")}</div>
                  <div>{t("webAccess.newTokenVerifyHint")}</div>
                </div>
                <div className="mt-3 flex gap-2">
                  <button type="button" onClick={() => void copyNewToken()} className={`px-3 py-2 rounded-lg text-xs ${isDark ? "bg-emerald-500/20 text-emerald-100 hover:bg-emerald-500/30" : "bg-white text-emerald-700 border border-emerald-200 hover:bg-emerald-50"}`}>
                    {copiedNewToken ? t("webAccess.copied") : t("webAccess.copyToken")}
                  </button>
                  <button type="button" onClick={() => { setNewToken(null); setCopiedNewToken(false); }} className={`px-3 py-2 rounded-lg text-xs ${isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"}`}>
                    {t("webAccess.close")}
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="space-y-3">
            {accessTokens.length === 0 ? (
              <div className={`rounded-lg border border-dashed p-6 text-sm ${isDark ? "border-slate-700 text-slate-400" : "border-gray-300 text-gray-500"}`}>
                {t("webAccess.noAccessTokens")}
              </div>
            ) : (
              accessTokens.map((token) => {
                const tokenId = token.token_id || "";
                const isEditing = editingTokenId === tokenId;
                return (
                  <div key={tokenId || token.user_id} className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          <div className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{token.user_id}</div>
                          <span className={`rounded-full border px-2 py-0.5 text-[11px] ${statusChipClass(isDark, token.is_admin ? "good" : "neutral")}`}>
                            {token.is_admin ? t("webAccess.adminBadge") : t("webAccess.scopedBadge")}
                          </span>
                          <span className={`rounded-full border px-2 py-0.5 text-[11px] ${statusChipClass(isDark, "neutral")}`}>
                            {token.token_preview || "****"}
                          </span>
                        </div>
                        <div className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                          {(token.allowed_groups || []).length > 0
                            ? t("webAccess.scopedGroupsHint", { count: token.allowed_groups.length })
                            : t("webAccess.allGroupsAccess")}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={() => startEdit(token)} className={`px-3 py-2 rounded-lg text-xs ${isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"}`}>
                          {t("webAccess.edit")}
                        </button>
                        <button type="button" onClick={() => void revealAndCopy(tokenId)} className={`px-3 py-2 rounded-lg text-xs ${isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"}`}>
                          {copiedTokenId === tokenId ? t("webAccess.copied") : t("webAccess.copyToken")}
                        </button>
                        <button type="button" onClick={() => void handleDeleteAccessToken(tokenId)} className={`px-3 py-2 rounded-lg text-xs ${isDark ? "bg-red-500/15 text-red-200 hover:bg-red-500/25" : "bg-red-50 text-red-700 border border-red-200 hover:bg-red-100"}`}>
                          {t("webAccess.delete")}
                        </button>
                      </div>
                    </div>

                    {isEditing && (
                      <div className={`mt-3 rounded-lg border p-3 ${isDark ? "border-slate-700 bg-slate-950/60" : "border-gray-200 bg-gray-50"}`}>
                        <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("webAccess.editScopeTitle")}</div>
                        <div className="mt-2 max-h-44 overflow-y-auto space-y-2 pr-1">
                          {groups.length === 0 ? (
                            <div className={`text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("webAccess.noGroups")}</div>
                          ) : groups.map((group) => {
                            const groupId = group.group_id;
                            return (
                              <label key={groupId} className="flex items-center gap-2 text-sm">
                                <input
                                  type="checkbox"
                                  checked={editGroups.has(groupId)}
                                  onChange={(e) => {
                                    setEditGroups((prev) => {
                                      const next = new Set(prev);
                                      if (e.target.checked) next.add(groupId); else next.delete(groupId);
                                      return next;
                                    });
                                  }}
                                />
                                <span className={isDark ? "text-slate-200" : "text-gray-800"}>{group.title || groupId}</span>
                              </label>
                            );
                          })}
                        </div>
                        <div className="mt-3 flex gap-2">
                          <button type="button" onClick={() => void saveEdit()} disabled={editBusy} className={primaryButtonClass(editBusy)}>
                            {editBusy ? t("webAccess.saving") : t("webAccess.save")}
                          </button>
                          <button type="button" onClick={() => { setEditingTokenId(null); setEditGroups(new Set()); }} className={`px-3 py-2 rounded-lg text-xs ${isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"}`}>
                            {t("webAccess.cancel")}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </section>

      <section className={cardClass(isDark)}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{t("webAccess.reachabilityTitle")}</h4>
            <p className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>{t("webAccess.reachabilityDescription")}</p>
          </div>
          <div className={`inline-flex rounded-full border px-2.5 py-1 text-xs ${statusChipClass(isDark, remoteStatusTone)}`}>
            {remoteStatusLabel}
          </div>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div>
            <label className={labelClass(isDark)}>{t("webAccess.providerLabel")}</label>
            <select value={provider} onChange={(e) => setProvider((e.target.value as "off" | "manual" | "tailscale") || "off")} className={inputClass(isDark)}>
              <option value="off">{t("webAccess.providers.off")}</option>
              <option value="manual">{t("webAccess.providers.manual")}</option>
              <option value="tailscale">{t("webAccess.providers.tailscale")}</option>
            </select>
          </div>
          <div>
            <label className={labelClass(isDark)}>{t("webAccess.modeLabel")}</label>
            <input value={mode} onChange={(e) => setMode(e.target.value)} className={inputClass(isDark)} placeholder="tailnet_only" />
          </div>
          <div>
            <label className={labelClass(isDark)}>{t("webAccess.webHostLabel")}</label>
            <input value={webHost} onChange={(e) => setWebHost(e.target.value)} className={inputClass(isDark)} placeholder="127.0.0.1" />
          </div>
          <div>
            <label className={labelClass(isDark)}>{t("webAccess.webPortLabel")}</label>
            <input value={webPort} onChange={(e) => setWebPort(e.target.value)} className={inputClass(isDark)} inputMode="numeric" placeholder="8848" />
          </div>
          <div className="md:col-span-2">
            <label className={labelClass(isDark)}>{t("webAccess.webPublicUrlLabel")}</label>
            <input value={webPublicUrl} onChange={(e) => setWebPublicUrl(e.target.value)} className={inputClass(isDark)} placeholder="https://example.com/ui/" />
          </div>
        </div>

        <label className={`mt-4 flex items-start gap-3 rounded-lg border px-3 py-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
          <input type="checkbox" checked={requireAccessToken} onChange={(e) => setRequireAccessToken(e.target.checked)} className="mt-1" />
          <div>
            <div className={`text-sm font-medium ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("webAccess.requireAccessTokenLabel")}</div>
            <div className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>{t("webAccess.requireAccessTokenHint")}</div>
          </div>
        </label>

        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" onClick={() => void handleSaveReachability()} disabled={saveBusy} className={primaryButtonClass(saveBusy)}>
            {saveBusy ? t("webAccess.saving") : t("webAccess.save")}
          </button>
          <button type="button" onClick={() => void handleStart()} disabled={startBusy || provider === "off"} className={`px-4 py-2 rounded-lg text-sm min-h-[44px] ${isDark ? "bg-slate-800 text-slate-100 hover:bg-slate-700" : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"} disabled:opacity-50`}>
            {startBusy ? t("common:loading") : t("webAccess.start")}
          </button>
          <button type="button" onClick={() => void handleStop()} disabled={stopBusy || remoteState?.enabled !== true} className={`px-4 py-2 rounded-lg text-sm min-h-[44px] ${isDark ? "bg-slate-800 text-slate-100 hover:bg-slate-700" : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"} disabled:opacity-50`}>
            {stopBusy ? t("common:loading") : t("webAccess.stop")}
          </button>
          {remoteState?.endpoint && (
            <button
              type="button"
              onClick={async () => {
                const ok = await copyText(remoteState.endpoint || "");
                if (ok) pushHint(t("webAccess.copied"));
              }}
              className={`px-4 py-2 rounded-lg text-sm min-h-[44px] ${isDark ? "bg-slate-800 text-slate-100 hover:bg-slate-700" : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"}`}
            >
              {t("webAccess.copyEndpoint")}
            </button>
          )}
        </div>

        <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
            <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("webAccess.reachabilityStatusTitle")}</div>
            <div className={`mt-2 text-sm ${isDark ? "text-slate-200" : "text-gray-800"}`}>{remoteState?.endpoint || t("webAccess.noEndpoint")}</div>
            <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
              <div>
                <dt className={isDark ? "text-slate-500" : "text-gray-500"}>{t("webAccess.requirementLabel")}</dt>
                <dd className={isDark ? "text-slate-200" : "text-gray-800"}>{effectiveRequireAccessToken ? t("webAccess.required") : t("webAccess.optional")}</dd>
              </div>
              <div>
                <dt className={isDark ? "text-slate-500" : "text-gray-500"}>{t("webAccess.accessTokenPresenceLabel")}</dt>
                <dd className={isDark ? "text-slate-200" : "text-gray-800"}>{remoteState?.config?.access_token_configured ? t("webAccess.configured") : t("webAccess.notConfigured")}</dd>
              </div>
              <div>
                <dt className={isDark ? "text-slate-500" : "text-gray-500"}>{t("webAccess.bindingLabel")}</dt>
                <dd className={isDark ? "text-slate-200" : "text-gray-800"}>{`${webHost}:${webPort}`}</dd>
              </div>
              <div>
                <dt className={isDark ? "text-slate-500" : "text-gray-500"}>{t("webAccess.updatedAtLabel")}</dt>
                <dd className={isDark ? "text-slate-200" : "text-gray-800"}>{remoteState?.updated_at || "-"}</dd>
              </div>
            </dl>
          </div>

          <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
            <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("webAccess.nextStepsTitle")}</div>
            <ul className={`mt-2 space-y-2 text-xs ${isDark ? "text-slate-300" : "text-gray-700"}`}>
              {(remoteState?.next_steps || []).length > 0
                ? (remoteState?.next_steps || []).map((step) => <li key={step}>• {step}</li>)
                : <li>• {t("webAccess.noNextSteps")}</li>}
            </ul>
          </div>
        </div>
      </section>

      <section className={cardClass(isDark)}>
        <h4 className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{t("webAccess.interactionModeTitle")}</h4>
        <p className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-500"}`}>{t("webAccess.interactionModeDescription")}</p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
            <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("webAccess.currentModeTitle")}</div>
            <div className={`mt-2 inline-flex rounded-full border px-2.5 py-1 text-xs ${statusChipClass(isDark, modeSummary.tone)}`}>{modeSummary.label}</div>
            <div className={`mt-3 text-sm ${isDark ? "text-slate-300" : "text-gray-700"}`}>{modeSummary.detail}</div>
          </div>
          <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
            <div className={`text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("webAccess.modeGuideTitle")}</div>
            <ul className={`mt-2 space-y-2 text-xs ${isDark ? "text-slate-300" : "text-gray-700"}`}>
              <li>• {t("webAccess.modeGuideNormal")}</li>
              <li>• {t("webAccess.modeGuideExhibit")}</li>
              <li>• {t("webAccess.modeGuideSwitch")}</li>
            </ul>
          </div>
        </div>
      </section>
    </div>
  );
}
