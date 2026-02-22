import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  GroupSpaceJob,
  GroupSpaceProviderAuthStatus,
  GroupSpaceProviderCredentialStatus,
  GroupSpaceStatus,
} from "../../../types";
import * as api from "../../../services/api";
import { cardClass, inputClass } from "./types";

interface GroupSpaceTabProps {
  isDark: boolean;
  groupId?: string;
  isActive?: boolean;
}

function parseJsonObject(raw: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  const text = String(raw || "").trim();
  if (!text) return { ok: true, value: {} };
  try {
    const obj = JSON.parse(text);
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      return { ok: false, error: "must be JSON object" };
    }
    return { ok: true, value: obj as Record<string, unknown> };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "invalid JSON" };
  }
}

export function GroupSpaceTab({ isDark, groupId, isActive = true }: GroupSpaceTabProps) {
  const { t } = useTranslation("settings");
  const [provider] = useState("notebooklm");
  const [status, setStatus] = useState<GroupSpaceStatus | null>(null);
  const [credential, setCredential] = useState<GroupSpaceProviderCredentialStatus | null>(null);
  const [authFlow, setAuthFlow] = useState<GroupSpaceProviderAuthStatus | null>(null);
  const [authJsonText, setAuthJsonText] = useState("");
  const [jobs, setJobs] = useState<GroupSpaceJob[]>([]);
  const [jobsFilter, setJobsFilter] = useState("");
  const [bindRemoteId, setBindRemoteId] = useState("");
  const [queryText, setQueryText] = useState("");
  const [queryOptionsText, setQueryOptionsText] = useState("{}");
  const [queryAnswer, setQueryAnswer] = useState("");
  const [queryMeta, setQueryMeta] = useState("");
  const [ingestKind, setIngestKind] = useState<"context_sync" | "resource_ingest">("context_sync");
  const [ingestPayloadText, setIngestPayloadText] = useState("{}");
  const [ingestIdempotencyKey, setIngestIdempotencyKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState("");
  const [err, setErr] = useState("");

  const statusTone = useMemo(() => {
    const mode = String(status?.provider?.mode || "disabled");
    if (mode === "active") return isDark ? "text-emerald-300" : "text-emerald-700";
    if (mode === "degraded") return isDark ? "text-amber-300" : "text-amber-700";
    return isDark ? "text-slate-300" : "text-gray-700";
  }, [isDark, status?.provider?.mode]);

  const readinessReason = String(status?.provider?.readiness_reason || "");
  const readinessReasonText = useMemo(() => {
    if (!readinessReason) return "";
    if (readinessReason === "ok") return t("groupSpace.readiness.ok");
    if (readinessReason === "missing_auth") return t("groupSpace.readiness.missingAuth");
    if (readinessReason === "real_disabled_and_stub_disabled") return t("groupSpace.readiness.realDisabled");
    if (readinessReason === "not_ready") return t("groupSpace.readiness.notReady");
    return t("groupSpace.readiness.unknown", { reason: readinessReason });
  }, [readinessReason, t]);

  const realAdapterEnabled = Boolean(status?.provider?.real_adapter_enabled);
  const authConfigured = Boolean(status?.provider?.auth_configured);
  const writeReady = Boolean(status?.provider?.write_ready);
  const connectionState = String(authFlow?.state || "idle");
  const connectionPhase = String(authFlow?.phase || "");
  const connectionMessage = String(authFlow?.message || "");
  const connectionErrorMessage = String(authFlow?.error?.message || "");
  const connectionRunning = connectionState === "running";
  const connectionSucceeded = connectionState === "succeeded";
  const connected = authConfigured && realAdapterEnabled;

  const loadAll = async () => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    setBusy(true);
    setErr("");
    try {
      const [statusResp, jobsResp, credentialResp, authResp] = await Promise.all([
        api.fetchGroupSpaceStatus(gid, provider),
        api.listGroupSpaceJobs({ groupId: gid, provider, state: jobsFilter, limit: 30 }),
        api.fetchGroupSpaceProviderCredential(provider),
        api.controlGroupSpaceProviderAuth({ provider, action: "status" }),
      ]);
      if (!statusResp.ok) {
        setErr(statusResp.error?.message || t("groupSpace.loadFailed"));
        return;
      }
      setStatus(statusResp.result || null);
      const binding = statusResp.result?.binding;
      if (binding?.status === "bound" && String(binding.remote_space_id || "").trim()) {
        setBindRemoteId(String(binding.remote_space_id || ""));
      }
      if (!jobsResp.ok) {
        setErr(jobsResp.error?.message || t("groupSpace.loadJobsFailed"));
        setJobs([]);
        return;
      }
      if (credentialResp.ok) {
        setCredential(credentialResp.result?.credential || null);
      } else {
        setCredential(null);
      }
      if (authResp.ok) {
        setAuthFlow(authResp.result?.auth || null);
      } else {
        setAuthFlow(null);
      }
      setJobs(Array.isArray(jobsResp.result?.jobs) ? jobsResp.result.jobs : []);
    } catch {
      setErr(t("groupSpace.loadFailed"));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!isActive) return;
    if (!groupId) return;
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Only refresh when tab is active/group changes/filter changes.
  }, [isActive, groupId, jobsFilter]);

  useEffect(() => {
    if (!isActive) return;
    if (!groupId) return;
    if (!connectionRunning) return;
    const pollOnce = async () => {
      try {
        const resp = await api.controlGroupSpaceProviderAuth({ provider, action: "status" });
        if (!resp.ok) return;
        const next = resp.result?.auth || null;
        setAuthFlow(next);
        const nextState = String(next?.state || "");
        if (nextState && nextState !== "running") {
          await loadAll();
          if (nextState === "succeeded") {
            setHintWithTimeout(t("groupSpace.googleConnectSuccess"));
          }
        }
      } catch {
        // keep previous status on transient polling failure
      }
    };
    void pollOnce();
    const timer = window.setInterval(async () => {
      await pollOnce();
    }, 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Poll only while auth flow is running.
  }, [isActive, groupId, connectionRunning]);

  const setHintWithTimeout = (text: string) => {
    setHint(text);
    window.setTimeout(() => setHint(""), 2400);
  };

  const hasStorageCookies = (obj: Record<string, unknown>): boolean => {
    return Array.isArray(obj.cookies) && obj.cookies.length > 0;
  };

  const handleConnectGoogle = async () => {
    setBusy(true);
    setErr("");
    try {
      const resp = await api.controlGroupSpaceProviderAuth({
        provider,
        action: "start",
        timeoutSeconds: 900,
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.connectStartFailed"));
        return;
      }
      setAuthFlow(resp.result?.auth || null);
      setHintWithTimeout(t("groupSpace.connectStarted"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.connectStartFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleCancelConnect = async () => {
    setBusy(true);
    setErr("");
    try {
      const resp = await api.controlGroupSpaceProviderAuth({
        provider,
        action: "cancel",
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.connectCancelFailed"));
        return;
      }
      setAuthFlow(resp.result?.auth || null);
      setHintWithTimeout(t("groupSpace.connectCancelRequested"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.connectCancelFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleBind = async () => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    setBusy(true);
    setErr("");
    try {
      const resp = await api.bindGroupSpace(gid, String(bindRemoteId || "").trim(), provider);
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.bindFailed"));
        return;
      }
      setStatus(resp.result || null);
      await loadAll();
      setHintWithTimeout(t("groupSpace.bindSuccess"));
    } catch {
      setErr(t("groupSpace.bindFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleSyncNow = async () => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    setBusy(true);
    setErr("");
    try {
      const resp = await api.syncGroupSpace({
        groupId: gid,
        provider,
        action: "run",
        force: true,
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.syncFailed"));
        return;
      }
      setHintWithTimeout(t("groupSpace.syncDone"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.syncFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleUnbind = async () => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    setBusy(true);
    setErr("");
    try {
      const resp = await api.unbindGroupSpace(gid, provider);
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.unbindFailed"));
        return;
      }
      setStatus(resp.result || null);
      setHintWithTimeout(t("groupSpace.unbindSuccess"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.unbindFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleIngest = async () => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    const parsed = parseJsonObject(ingestPayloadText);
    if (!parsed.ok) {
      setErr(t("groupSpace.invalidJsonObject", { field: t("groupSpace.ingestPayload") }));
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const resp = await api.ingestGroupSpace({
        groupId: gid,
        provider,
        kind: ingestKind,
        payload: parsed.value,
        idempotencyKey: ingestIdempotencyKey,
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.ingestFailed"));
        return;
      }
      setHintWithTimeout(t("groupSpace.ingestAccepted", { jobId: resp.result?.job_id || "" }));
      await loadAll();
    } catch {
      setErr(t("groupSpace.ingestFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleSaveCredential = async () => {
    const parsed = parseJsonObject(authJsonText);
    if (!parsed.ok) {
      setErr(t("groupSpace.invalidJsonObject", { field: t("groupSpace.authJson") }));
      return;
    }
    if (!hasStorageCookies(parsed.value)) {
      setErr(t("groupSpace.storageStateInvalid"));
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const resp = await api.updateGroupSpaceProviderCredential({
        provider,
        authJson: authJsonText,
        clear: false,
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.credentialSaveFailed"));
        return;
      }
      setCredential(resp.result?.credential || null);
      setAuthJsonText("");
      setHintWithTimeout(t("groupSpace.credentialSaved"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.credentialSaveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleClearCredential = async () => {
    setBusy(true);
    setErr("");
    try {
      const resp = await api.updateGroupSpaceProviderCredential({
        provider,
        authJson: "",
        clear: true,
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.credentialClearFailed"));
        return;
      }
      setCredential(resp.result?.credential || null);
      setAuthJsonText("");
      setHintWithTimeout(t("groupSpace.credentialCleared"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.credentialClearFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleHealthCheck = async () => {
    setBusy(true);
    setErr("");
    try {
      const resp = await api.checkGroupSpaceProviderHealth(provider);
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.healthCheckFailed"));
        return;
      }
      if (resp.result?.credential) {
        setCredential(resp.result.credential);
      }
      if (resp.result?.healthy) {
        setHintWithTimeout(t("groupSpace.healthCheckOk"));
      } else {
        const reason = String(resp.result?.error?.message || "");
        setErr(reason || t("groupSpace.healthCheckFailed"));
      }
      await loadAll();
    } catch {
      setErr(t("groupSpace.healthCheckFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleQuery = async () => {
    const gid = String(groupId || "").trim();
    const query = String(queryText || "").trim();
    if (!gid) return;
    if (!query) {
      setErr(t("groupSpace.queryRequired"));
      return;
    }
    const parsed = parseJsonObject(queryOptionsText);
    if (!parsed.ok) {
      setErr(t("groupSpace.invalidJsonObject", { field: t("groupSpace.queryOptions") }));
      return;
    }
    setBusy(true);
    setErr("");
    setQueryAnswer("");
    setQueryMeta("");
    try {
      const resp = await api.queryGroupSpace({
        groupId: gid,
        provider,
        query,
        options: parsed.value,
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.queryFailed"));
        return;
      }
      const result = resp.result || {
        answer: "",
        degraded: false,
        error: null,
      };
      setQueryAnswer(String(result.answer || ""));
      if (result.degraded) {
        const reason = String(result.error?.message || "");
        setQueryMeta(
          reason ? `${t("groupSpace.degradedResult")}: ${reason}` : t("groupSpace.degradedResult")
        );
      } else {
        setQueryMeta(t("groupSpace.queryOk"));
      }
      await loadAll();
    } catch {
      setErr(t("groupSpace.queryFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleJobAction = async (action: "retry" | "cancel", jobId: string) => {
    const gid = String(groupId || "").trim();
    if (!gid || !jobId) return;
    setBusy(true);
    setErr("");
    try {
      const resp = await api.actionGroupSpaceJob({
        groupId: gid,
        provider,
        action,
        jobId,
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.jobActionFailed"));
        return;
      }
      setHintWithTimeout(action === "retry" ? t("groupSpace.jobRetried") : t("groupSpace.jobCanceled"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.jobActionFailed"));
    } finally {
      setBusy(false);
    }
  };

  if (!groupId) {
    return <div className={`text-sm ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.openFromGroup")}</div>;
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("groupSpace.title")}</h3>
        <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("groupSpace.description")}</p>
      </div>

      <div className={cardClass(isDark)}>
        <div className="flex items-center justify-between gap-2">
          <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("groupSpace.connectionTitle")}</div>
          <div className={`text-xs ${connected ? (isDark ? "text-emerald-300" : "text-emerald-700") : (isDark ? "text-slate-400" : "text-gray-600")}`}>
            {connected ? t("groupSpace.connectionConnected") : t("groupSpace.connectionNotConnected")}
          </div>
        </div>
        <div className={`mt-2 text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>
          {t("groupSpace.connectionDescription")}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            onClick={() => void handleConnectGoogle()}
            disabled={busy || connectionRunning}
            className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
              isDark ? "bg-emerald-900/40 hover:bg-emerald-800/40 text-emerald-300 border border-emerald-700/60" : "bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200"
            } disabled:opacity-50`}
          >
            {connected ? t("groupSpace.reconnectGoogle") : t("groupSpace.connectGoogle")}
          </button>
          <button
            onClick={() => void handleCancelConnect()}
            disabled={busy || !connectionRunning}
            className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
              isDark ? "bg-rose-900/40 hover:bg-rose-800/40 text-rose-300 border border-rose-700/60" : "bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200"
            } disabled:opacity-50`}
          >
            {t("groupSpace.cancelConnect")}
          </button>
          <button
            onClick={() => void loadAll()}
            disabled={busy}
            className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
              isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
            } disabled:opacity-50`}
          >
            {t("groupSpace.refresh")}
          </button>
        </div>
        <div className="mt-3 text-xs space-y-1">
          <div className={isDark ? "text-slate-400" : "text-gray-600"}>
            {t("groupSpace.connectionState")}: {connectionState}
            {connectionPhase ? ` · ${connectionPhase}` : ""}
          </div>
          {connectionMessage ? (
            <div className={isDark ? "text-slate-300" : "text-gray-700"}>{connectionMessage}</div>
          ) : null}
          {connectionErrorMessage ? (
            <div className={isDark ? "text-amber-300" : "text-amber-700"}>{connectionErrorMessage}</div>
          ) : null}
          {!connected ? (
            <div className={isDark ? "text-slate-500" : "text-gray-500"}>
              {t("groupSpace.connectionHint")}
            </div>
          ) : null}
          {connectionSucceeded ? (
            <div className={isDark ? "text-emerald-300" : "text-emerald-700"}>{t("groupSpace.googleConnectSuccess")}</div>
          ) : null}
        </div>
      </div>

      <div className={cardClass(isDark)}>
        <div className="flex items-center justify-between gap-2">
          <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("groupSpace.statusTitle")}</div>
          <button
            onClick={() => void loadAll()}
            disabled={busy}
            className={`px-3 py-2 rounded-lg text-xs min-h-[40px] transition-colors ${
              isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
            } disabled:opacity-50`}
          >
            {busy ? t("common:loading") : t("groupSpace.refresh")}
          </button>
        </div>
        <div className="mt-2 text-xs">
          <div className={`${statusTone}`}>
            {t("groupSpace.providerMode")}: {String(status?.provider?.mode || "disabled")}
          </div>
          <div className={isDark ? "text-slate-400" : "text-gray-600"}>
            {t("groupSpace.providerWriteReady")}: {status?.provider?.write_ready ? t("common:yes") : t("common:no")}
            {readinessReasonText ? ` (${readinessReasonText})` : ""}
          </div>
          <div className={isDark ? "text-slate-400" : "text-gray-600"}>
            {t("groupSpace.realAdapter")}: {status?.provider?.real_adapter_enabled ? t("common:yes") : t("common:no")}
            {" · "}
            {t("groupSpace.stubAdapter")}: {status?.provider?.stub_adapter_enabled ? t("common:yes") : t("common:no")}
            {" · "}
            {t("groupSpace.authConfigured")}: {status?.provider?.auth_configured ? t("common:yes") : t("common:no")}
          </div>
          <div className={isDark ? "text-slate-400" : "text-gray-600"}>
            {t("groupSpace.bindingStatus")}: {String(status?.binding?.status || "unbound")}
            {status?.binding?.remote_space_id ? ` · ${status.binding.remote_space_id}` : ""}
          </div>
          <div className={isDark ? "text-slate-400" : "text-gray-600"}>
            {t("groupSpace.syncConverged")}: {status?.sync?.converged ? t("common:yes") : t("common:no")}
            {" · "}
            {t("groupSpace.syncUnsynced")}: {Number(status?.sync?.unsynced_count || 0)}
          </div>
          {status?.sync?.space_root ? (
            <div className={isDark ? "text-slate-500" : "text-gray-500"}>
              {t("groupSpace.spaceRoot")}: <span className="font-mono break-all">{String(status.sync.space_root)}</span>
            </div>
          ) : null}
          {status?.sync?.last_run_at ? (
            <div className={isDark ? "text-slate-500" : "text-gray-500"}>
              {t("groupSpace.syncLastRunAt")}: {String(status.sync.last_run_at)}
            </div>
          ) : null}
          <div className={isDark ? "text-slate-400" : "text-gray-600"}>
            {t("groupSpace.queueSummary")}: P {Number(status?.queue_summary?.pending || 0)} / R {Number(status?.queue_summary?.running || 0)} / F {Number(status?.queue_summary?.failed || 0)}
          </div>
            {status?.provider?.last_error ? (
            <div className={isDark ? "text-amber-300" : "text-amber-700"}>
              {t("groupSpace.lastError")}: {String(status.provider.last_error)}
            </div>
          ) : null}
          {status?.sync?.last_error ? (
            <div className={isDark ? "text-amber-300" : "text-amber-700"}>
              {t("groupSpace.syncLastError")}: {String(status.sync.last_error)}
            </div>
          ) : null}
        </div>
      </div>

      <div className={cardClass(isDark)}>
        <details>
          <summary className={`cursor-pointer text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>
            {t("groupSpace.advancedCredentialTitle")}
          </summary>
          <div className={`mt-2 text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("groupSpace.advancedCredentialHint")}</div>
          <div className={`mt-2 text-xs space-y-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>
            <div>{t("groupSpace.credentialConfigured")}: {credential?.configured ? t("common:yes") : t("common:no")}</div>
            <div>{t("groupSpace.credentialSource")}: {String(credential?.source || "none")}</div>
            {credential?.masked_value ? (
              <div>{t("groupSpace.credentialMaskedValue")}: <span className="font-mono">{credential.masked_value}</span></div>
            ) : null}
            {credential?.updated_at ? (
              <div>{t("groupSpace.credentialUpdatedAt")}: {String(credential.updated_at)}</div>
            ) : null}
          </div>
          <div className="mt-2 space-y-2">
            <div>
              <label className={`block text-[11px] mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.authJson")}</label>
              <textarea
                value={authJsonText}
                onChange={(e) => setAuthJsonText(e.target.value)}
                rows={3}
                placeholder={t("groupSpace.authJsonPlaceholder")}
                className={`${inputClass(isDark)} resize-y`}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => void handleSaveCredential()}
                disabled={busy}
                className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                  isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
                } disabled:opacity-50`}
              >
                {t("groupSpace.saveCredential")}
              </button>
              <button
                onClick={() => void handleClearCredential()}
                disabled={busy}
                className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                  isDark ? "bg-rose-900/40 hover:bg-rose-800/40 text-rose-300 border border-rose-700/60" : "bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200"
                } disabled:opacity-50`}
              >
                {t("groupSpace.clearCredential")}
              </button>
              <button
                onClick={() => void handleHealthCheck()}
                disabled={busy}
                className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                  isDark ? "bg-emerald-900/40 hover:bg-emerald-800/40 text-emerald-300 border border-emerald-700/60" : "bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200"
                } disabled:opacity-50`}
              >
                {t("groupSpace.healthCheck")}
              </button>
            </div>
          </div>
        </details>
      </div>

      <div className={cardClass(isDark)}>
        <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("groupSpace.bindingTitle")}</div>
        <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2">
          <div className="sm:col-span-2">
            <label className={`block text-[11px] mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.remoteSpaceId")}</label>
            <input
              value={bindRemoteId}
              onChange={(e) => setBindRemoteId(e.target.value)}
              placeholder={t("groupSpace.remoteSpacePlaceholder")}
              className={inputClass(isDark)}
            />
          </div>
          <div className="flex items-end gap-2">
            <button
              onClick={() => void handleBind()}
              disabled={busy || !writeReady}
              className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                isDark ? "bg-emerald-900/40 hover:bg-emerald-800/40 text-emerald-300 border border-emerald-700/60" : "bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200"
              } disabled:opacity-50`}
            >
              {t("groupSpace.bind")}
            </button>
            <button
              onClick={() => void handleUnbind()}
              disabled={busy}
              className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                isDark ? "bg-rose-900/40 hover:bg-rose-800/40 text-rose-300 border border-rose-700/60" : "bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200"
              } disabled:opacity-50`}
            >
              {t("groupSpace.unbind")}
            </button>
            <button
              onClick={() => void handleSyncNow()}
              disabled={busy}
              className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
              } disabled:opacity-50`}
            >
              {t("groupSpace.syncNow")}
            </button>
          </div>
          {!writeReady ? (
            <div className={`sm:col-span-3 text-xs ${isDark ? "text-amber-300" : "text-amber-700"}`}>
              {t("groupSpace.completeGoogleConnectFirst")}
            </div>
          ) : null}
        </div>
      </div>

      <div className={cardClass(isDark)}>
        <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("groupSpace.queryTitle")}</div>
        <div className="mt-2 space-y-2">
          <div>
            <label className={`block text-[11px] mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.query")}</label>
            <input value={queryText} onChange={(e) => setQueryText(e.target.value)} placeholder={t("groupSpace.queryPlaceholder")} className={inputClass(isDark)} />
          </div>
          <div>
            <label className={`block text-[11px] mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.queryOptions")}</label>
            <textarea value={queryOptionsText} onChange={(e) => setQueryOptionsText(e.target.value)} rows={2} className={`${inputClass(isDark)} resize-y`} />
          </div>
          <button
            onClick={() => void handleQuery()}
            disabled={busy}
            className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
              isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
            } disabled:opacity-50`}
          >
            {t("groupSpace.runQuery")}
          </button>
          {queryMeta ? <div className={`text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>{queryMeta}</div> : null}
          {queryAnswer ? (
            <pre className={`text-xs p-2 rounded border whitespace-pre-wrap ${isDark ? "bg-slate-900 border-slate-700 text-slate-200" : "bg-white border-gray-200 text-gray-800"}`}>
              {queryAnswer}
            </pre>
          ) : null}
        </div>
      </div>

      <div className={cardClass(isDark)}>
        <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("groupSpace.ingestTitle")}</div>
        <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2">
          <div>
            <label className={`block text-[11px] mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.ingestKind")}</label>
            <select
              value={ingestKind}
              onChange={(e) => setIngestKind((e.target.value as "context_sync" | "resource_ingest") || "context_sync")}
              className={inputClass(isDark)}
            >
              <option value="context_sync">context_sync</option>
              <option value="resource_ingest">resource_ingest</option>
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className={`block text-[11px] mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.idempotencyKey")}</label>
            <input
              value={ingestIdempotencyKey}
              onChange={(e) => setIngestIdempotencyKey(e.target.value)}
              placeholder={t("groupSpace.idempotencyPlaceholder")}
              className={inputClass(isDark)}
            />
          </div>
          <div className="sm:col-span-3">
            <label className={`block text-[11px] mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.ingestPayload")}</label>
            <textarea value={ingestPayloadText} onChange={(e) => setIngestPayloadText(e.target.value)} rows={3} className={`${inputClass(isDark)} resize-y`} />
          </div>
          <div className="sm:col-span-3">
            <button
              onClick={() => void handleIngest()}
              disabled={busy}
              className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
              } disabled:opacity-50`}
            >
              {t("groupSpace.submitIngest")}
            </button>
          </div>
        </div>
      </div>

      <div className={cardClass(isDark)}>
        <div className="flex items-center justify-between gap-2">
          <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("groupSpace.jobsTitle")}</div>
          <select value={jobsFilter} onChange={(e) => setJobsFilter(e.target.value)} className={`${inputClass(isDark)} max-w-[180px]`}>
            <option value="">{t("groupSpace.filterAll")}</option>
            <option value="pending">pending</option>
            <option value="running">running</option>
            <option value="failed">failed</option>
            <option value="succeeded">succeeded</option>
            <option value="canceled">canceled</option>
          </select>
        </div>
        <div className="mt-2 space-y-2">
          {!jobs.length ? (
            <div className={`text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("groupSpace.noJobs")}</div>
          ) : (
            jobs.map((job) => {
              const canRetry = job.state === "failed" || job.state === "canceled";
              const canCancel = job.state === "pending" || job.state === "running";
              return (
                <div key={job.job_id} className={`rounded border p-2 text-xs ${isDark ? "border-slate-700 bg-slate-900/50 text-slate-200" : "border-gray-200 bg-white text-gray-800"}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-mono break-all">{job.job_id}</div>
                    <div>{job.state}</div>
                  </div>
                  <div className={`mt-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>
                    {job.kind} · {t("groupSpace.attemptLabel")}: {job.attempt}/{job.max_attempts}
                  </div>
                  {job.last_error?.message ? (
                    <div className={`mt-1 ${isDark ? "text-amber-300" : "text-amber-700"}`}>{job.last_error.message}</div>
                  ) : null}
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => void handleJobAction("retry", job.job_id)}
                      disabled={!canRetry || busy}
                      className={`px-2 py-1 rounded border ${
                        isDark ? "border-slate-600 text-slate-200 hover:bg-slate-800" : "border-gray-300 text-gray-800 hover:bg-gray-50"
                      } disabled:opacity-40`}
                    >
                      {t("groupSpace.retry")}
                    </button>
                    <button
                      onClick={() => void handleJobAction("cancel", job.job_id)}
                      disabled={!canCancel || busy}
                      className={`px-2 py-1 rounded border ${
                        isDark ? "border-slate-600 text-slate-200 hover:bg-slate-800" : "border-gray-300 text-gray-800 hover:bg-gray-50"
                      } disabled:opacity-40`}
                    >
                      {t("groupSpace.cancel")}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {err ? <div className={`text-xs ${isDark ? "text-rose-300" : "text-rose-600"}`}>{err}</div> : null}
      {hint ? <div className={`text-xs ${isDark ? "text-emerald-300" : "text-emerald-600"}`}>{hint}</div> : null}
    </div>
  );
}
