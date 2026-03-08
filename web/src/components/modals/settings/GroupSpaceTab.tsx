import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  GroupSpaceBinding,
  GroupSpaceProviderAuthStatus,
  GroupSpaceRemoteSpace,
  GroupSpaceStatus,
} from "../../../types";
import * as api from "../../../services/api";
import { cardClass, inputClass } from "./types";

interface GroupSpaceTabProps {
  isDark: boolean;
  groupId?: string;
  isActive?: boolean;
}

type NotebookLane = "work" | "memory";

const NOTEBOOK_LANES: NotebookLane[] = ["work", "memory"];

function normalizeNotebookSpaces(raw: unknown): GroupSpaceRemoteSpace[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item) => Boolean(String((item as GroupSpaceRemoteSpace | null)?.remote_space_id || "").trim()))
    .map((item) => ({
      remote_space_id: String((item as GroupSpaceRemoteSpace | null)?.remote_space_id || "").trim(),
      title: String((item as GroupSpaceRemoteSpace | null)?.title || "").trim(),
      created_at: String((item as GroupSpaceRemoteSpace | null)?.created_at || "").trim(),
      is_owner: Boolean((item as GroupSpaceRemoteSpace | null)?.is_owner),
    }));
}

function resolveDraftNotebookId(
  previousDraft: string,
  boundRemoteId: string,
  options: GroupSpaceRemoteSpace[]
): string {
  const bound = String(boundRemoteId || "").trim();
  if (bound) return bound;
  const previous = String(previousDraft || "").trim();
  if (previous && options.some((item) => String(item.remote_space_id || "").trim() === previous)) {
    return previous;
  }
  return String(options[0]?.remote_space_id || "").trim();
}

function mergeNotebookOptions(
  spaces: GroupSpaceRemoteSpace[],
  extraIds: Array<string | undefined>
): GroupSpaceRemoteSpace[] {
  const byId = new Map<string, GroupSpaceRemoteSpace>();
  for (const item of spaces) {
    const remoteId = String(item.remote_space_id || "").trim();
    if (!remoteId) continue;
    byId.set(remoteId, item);
  }
  for (const rawId of extraIds) {
    const remoteId = String(rawId || "").trim();
    if (!remoteId || byId.has(remoteId)) continue;
    byId.set(remoteId, { remote_space_id: remoteId, title: "", created_at: "", is_owner: false });
  }
  return Array.from(byId.values());
}

function bindingStateText(binding: GroupSpaceBinding | null | undefined, t: (key: string) => string): string {
  const status = String(binding?.status || "").trim();
  const remoteId = String(binding?.remote_space_id || "").trim();
  return status === "bound" && remoteId ? t("groupSpace.bound") : t("groupSpace.notBound");
}

export function GroupSpaceTab({ isDark, groupId, isActive = true }: GroupSpaceTabProps) {
  const { t } = useTranslation("settings");
  const [provider] = useState("notebooklm");
  const [status, setStatus] = useState<GroupSpaceStatus | null>(null);
  const [authFlow, setAuthFlow] = useState<GroupSpaceProviderAuthStatus | null>(null);
  const [spaces, setSpaces] = useState<GroupSpaceRemoteSpace[]>([]);
  const [workBindRemoteId, setWorkBindRemoteId] = useState("");
  const [memoryBindRemoteId, setMemoryBindRemoteId] = useState("");
  const [loading, setLoading] = useState(false);
  const [spacesBusy, setSpacesBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [hint, setHint] = useState("");
  const [err, setErr] = useState("");
  const connectHintedFlowRef = useRef("");

  const providerState = status?.provider || null;
  const workBinding = status?.bindings?.work || null;
  const memoryBinding = status?.bindings?.memory || null;
  const workBoundRemoteId = String(workBinding?.remote_space_id || "").trim();
  const memoryBoundRemoteId = String(memoryBinding?.remote_space_id || "").trim();
  const authConfigured = Boolean(providerState?.auth_configured);
  const connectionState = String(authFlow?.state || "idle").trim() || "idle";
  const connectionRunning = connectionState === "running";
  const connectionConnected = authConfigured;
  const connectionWarning =
    String(authFlow?.error?.message || "").trim() || String(providerState?.last_error || "").trim();

  const notebookOptions = useMemo(
    () => mergeNotebookOptions(spaces, [workBoundRemoteId, memoryBoundRemoteId]),
    [spaces, workBoundRemoteId, memoryBoundRemoteId]
  );

  const workBoundNotebook = notebookOptions.find((item) => String(item.remote_space_id || "").trim() === workBoundRemoteId) || null;
  const memoryBoundNotebook = notebookOptions.find((item) => String(item.remote_space_id || "").trim() === memoryBoundRemoteId) || null;

  const connectionStatusText = useMemo(() => {
    if (connectionRunning) return t("groupSpace.accountConnecting");
    if (connectionConnected) return t("groupSpace.accountConnected");
    if (connectionState === "failed") return t("groupSpace.accountReconnectRequired");
    return t("groupSpace.accountNotConnected");
  }, [connectionConnected, connectionRunning, connectionState, t]);

  const connectionStatusTone = useMemo(() => {
    if (connectionRunning) return isDark ? "text-sky-300" : "text-sky-700";
    if (connectionConnected) return isDark ? "text-emerald-300" : "text-emerald-700";
    if (connectionState === "failed") return isDark ? "text-amber-300" : "text-amber-700";
    return isDark ? "text-slate-300" : "text-gray-700";
  }, [connectionConnected, connectionRunning, connectionState, isDark]);

  const setHintWithTimeout = (text: string) => {
    setHint(text);
    window.setTimeout(() => setHint(""), 2400);
  };

  const loadAll = async () => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    setLoading(true);
    setErr("");
    try {
      let nextStatus: GroupSpaceStatus | null = null;
      let nextAuth: GroupSpaceProviderAuthStatus | null = null;
      let nextSpaces: GroupSpaceRemoteSpace[] = [];

      const statusResp = await api.fetchGroupSpaceStatus(gid, provider);
      if (!statusResp.ok) {
        setErr(statusResp.error?.message || t("groupSpace.loadFailed"));
      } else {
        nextStatus = statusResp.result || null;
        setStatus(nextStatus);
      }

      const authResp = await api.controlGroupSpaceProviderAuth({ provider, action: "status" });
      if (authResp.ok) {
        nextAuth = authResp.result?.auth || null;
        setAuthFlow(nextAuth);
      } else {
        setAuthFlow(null);
      }

      const shouldLoadSpaces = Boolean(nextStatus?.provider?.auth_configured);
      if (shouldLoadSpaces) {
        setSpacesBusy(true);
        try {
          const spacesResp = await api.fetchGroupSpaceSpaces(gid, provider);
          if (spacesResp.ok) {
            nextSpaces = normalizeNotebookSpaces(spacesResp.result?.spaces);
            setSpaces(nextSpaces);
          } else {
            setSpaces([]);
          }
        } finally {
          setSpacesBusy(false);
        }
      } else {
        setSpaces([]);
      }

      const nextWorkBoundRemoteId = String(nextStatus?.bindings?.work?.remote_space_id || "").trim();
      const nextMemoryBoundRemoteId = String(nextStatus?.bindings?.memory?.remote_space_id || "").trim();
      setWorkBindRemoteId((prev) => resolveDraftNotebookId(prev, nextWorkBoundRemoteId, nextSpaces));
      setMemoryBindRemoteId((prev) => resolveDraftNotebookId(prev, nextMemoryBoundRemoteId, nextSpaces));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("groupSpace.loadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isActive || !groupId) return;
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh when active/group changes
  }, [isActive, groupId]);

  useEffect(() => {
    if (!isActive || !groupId || !connectionRunning) return;
    const pollOnce = async () => {
      try {
        const resp = await api.controlGroupSpaceProviderAuth({ provider, action: "status" });
        if (!resp.ok) {
          setErr(resp.error?.message || t("groupSpace.loadFailed"));
          return;
        }
        const nextAuth = resp.result?.auth || null;
        setAuthFlow(nextAuth);
        const nextState = String(nextAuth?.state || "").trim();
        if (nextState && nextState !== "running") {
          await loadAll();
          if (nextState === "succeeded") {
            const flowKey = String(nextAuth?.session_id || nextAuth?.started_at || "succeeded").trim();
            if (flowKey && connectHintedFlowRef.current !== flowKey) {
              connectHintedFlowRef.current = flowKey;
              setHintWithTimeout(t("groupSpace.googleConnectSuccess"));
            }
          }
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : t("groupSpace.loadFailed"));
      }
    };
    void pollOnce();
    const timer = window.setInterval(() => {
      void pollOnce();
    }, 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll only while auth flow is running
  }, [isActive, groupId, connectionRunning]);

  const handleConnectGoogle = async () => {
    setActionBusy(true);
    setErr("");
    try {
      const resp = await api.controlGroupSpaceProviderAuth({ provider, action: "start", timeoutSeconds: 900 });
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.connectStartFailed"));
        return;
      }
      setAuthFlow(resp.result?.auth || null);
      setHintWithTimeout(t("groupSpace.connectStarted"));
    } catch {
      setErr(t("groupSpace.connectStartFailed"));
    } finally {
      setActionBusy(false);
    }
  };

  const handleCancelConnect = async () => {
    setActionBusy(true);
    setErr("");
    try {
      const resp = await api.controlGroupSpaceProviderAuth({ provider, action: "cancel" });
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
      setActionBusy(false);
    }
  };

  const handleBind = async (lane: NotebookLane, remoteSpaceId: string) => {
    const gid = String(groupId || "").trim();
    const rid = String(remoteSpaceId || "").trim();
    if (!gid || !rid) return;
    setActionBusy(true);
    setErr("");
    try {
      const resp = await api.bindGroupSpace(gid, rid, provider, lane);
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.bindFailed"));
        return;
      }
      setStatus(resp.result || null);
      if (lane === "work") setWorkBindRemoteId(rid);
      else setMemoryBindRemoteId(rid);
      setHintWithTimeout(t("groupSpace.bindSuccess"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.bindFailed"));
    } finally {
      setActionBusy(false);
    }
  };

  const handleCreateAndBind = async (lane: NotebookLane) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    setActionBusy(true);
    setErr("");
    try {
      const resp = await api.bindGroupSpace(gid, "", provider, lane);
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.bindFailed"));
        return;
      }
      const nextRemoteId = String(resp.result?.bindings?.[lane]?.remote_space_id || "").trim();
      setStatus(resp.result || null);
      if (lane === "work") setWorkBindRemoteId(nextRemoteId);
      else setMemoryBindRemoteId(nextRemoteId);
      setHintWithTimeout(t("groupSpace.bindCreated"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.bindFailed"));
    } finally {
      setActionBusy(false);
    }
  };

  const handleUnbind = async (lane: NotebookLane) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    const confirmText = t(lane === "work" ? "groupSpace.workUnbindConfirm" : "groupSpace.memoryUnbindConfirm");
    if (!window.confirm(confirmText)) return;
    setActionBusy(true);
    setErr("");
    try {
      const resp = await api.unbindGroupSpace(gid, provider, lane);
      if (!resp.ok) {
        setErr(resp.error?.message || t("groupSpace.unbindFailed"));
        return;
      }
      setStatus(resp.result || null);
      if (lane === "work") setWorkBindRemoteId("");
      else setMemoryBindRemoteId("");
      setHintWithTimeout(t("groupSpace.unbindSuccess"));
      await loadAll();
    } catch {
      setErr(t("groupSpace.unbindFailed"));
    } finally {
      setActionBusy(false);
    }
  };

  const renderNotebookCard = (lane: NotebookLane) => {
    const isWork = lane === "work";
    const binding = (isWork ? workBinding : memoryBinding) || null;
    const boundRemoteId = isWork ? workBoundRemoteId : memoryBoundRemoteId;
    const selectedRemoteId = isWork ? workBindRemoteId : memoryBindRemoteId;
    const setSelectedRemoteId = isWork ? setWorkBindRemoteId : setMemoryBindRemoteId;
    const boundNotebook = isWork ? workBoundNotebook : memoryBoundNotebook;
    const titleKey = isWork ? "groupSpace.workNotebookTitle" : "groupSpace.memoryNotebookTitle";
    const hintKey = isWork ? "groupSpace.workNotebookHint" : "groupSpace.memoryNotebookHint";
    const notebookStateText = bindingStateText(binding, t);
    const canManage = connectionConnected && !actionBusy;

    return (
      <div key={lane} className={cardClass(isDark)}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t(titleKey)}</div>
            <div className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t(hintKey)}</div>
          </div>
          <div className={`text-xs font-medium ${isDark ? "text-slate-400" : "text-gray-600"}`}>{notebookStateText}</div>
        </div>

        <div className={`mt-3 rounded-lg border px-3 py-2 ${isDark ? "border-slate-700 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
          <div className={`text-[11px] uppercase tracking-wide ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("groupSpace.currentNotebook")}</div>
          <div className={`mt-1 text-sm font-medium break-all ${isDark ? "text-slate-200" : "text-gray-900"}`}>
            {boundRemoteId
              ? (String(boundNotebook?.title || "").trim() || boundRemoteId)
              : t("groupSpace.notBound")}
          </div>
          {boundRemoteId ? (
            <div className={`mt-1 text-[11px] font-mono break-all ${isDark ? "text-slate-500" : "text-gray-500"}`}>{boundRemoteId}</div>
          ) : null}
        </div>

        <div className="mt-3">
          <label className={`block text-[11px] mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.chooseNotebook")}</label>
          <select
            value={selectedRemoteId}
            onChange={(e) => setSelectedRemoteId(String(e.target.value || ""))}
            disabled={!connectionConnected || actionBusy || spacesBusy || !notebookOptions.length}
            className={inputClass(isDark)}
          >
            {!notebookOptions.length ? (
              <option value="">{t("groupSpace.noNotebookOptions")}</option>
            ) : null}
            {notebookOptions.map((item) => {
              const remoteId = String(item.remote_space_id || "").trim();
              const title = String(item.title || "").trim() || remoteId;
              return (
                <option key={remoteId} value={remoteId}>
                  {title}
                </option>
              );
            })}
          </select>
          {!connectionConnected ? (
            <div className={`mt-1 text-[11px] ${isDark ? "text-amber-300" : "text-amber-700"}`}>{t("groupSpace.connectGoogleFirst")}</div>
          ) : connectionConnected && !notebookOptions.length ? (
            <div className={`mt-1 text-[11px] ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.noNotebookOptionsHint")}</div>
          ) : null}
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            onClick={() => void handleBind(lane, selectedRemoteId)}
            disabled={!canManage || !String(selectedRemoteId || "").trim()}
            className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
              isDark ? "bg-emerald-900/40 hover:bg-emerald-800/40 text-emerald-300 border border-emerald-700/60" : "bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200"
            } disabled:opacity-50`}
          >
            {t("groupSpace.bindSelected")}
          </button>
          <button
            onClick={() => void handleCreateAndBind(lane)}
            disabled={!canManage}
            className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
              isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
            } disabled:opacity-50`}
          >
            {t("groupSpace.createAndBind")}
          </button>
          <button
            onClick={() => void handleUnbind(lane)}
            disabled={!canManage || !boundRemoteId}
            className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
              isDark ? "bg-rose-900/40 hover:bg-rose-800/40 text-rose-300 border border-rose-700/60" : "bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200"
            } disabled:opacity-50`}
          >
            {t("groupSpace.unbind")}
          </button>
        </div>
      </div>
    );
  };

  if (!groupId) {
    return <div className={`text-sm ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.openFromGroup")}</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("groupSpace.title")}</h3>
          <p className={`text-xs mt-1 max-w-3xl ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("groupSpace.description")}</p>
        </div>
        <button
          onClick={() => void loadAll()}
          disabled={actionBusy || loading}
          className={`px-3 py-2 rounded-lg text-sm min-h-[44px] transition-colors ${
            isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
          } disabled:opacity-50`}
        >
          {loading ? t("common:loading") : t("groupSpace.refresh")}
        </button>
      </div>

      <div className={cardClass(isDark)}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("groupSpace.accountTitle")}</div>
            <div className={`mt-1 text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>{t("groupSpace.accountHint")}</div>
          </div>
          <div className={`text-xs font-medium ${connectionStatusTone}`}>{connectionStatusText}</div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            onClick={() => void handleConnectGoogle()}
            disabled={actionBusy || connectionRunning}
            className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
              isDark ? "bg-emerald-900/40 hover:bg-emerald-800/40 text-emerald-300 border border-emerald-700/60" : "bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200"
            } disabled:opacity-50`}
          >
            {connectionConnected ? t("groupSpace.reconnectGoogle") : t("groupSpace.connectGoogle")}
          </button>
          {connectionRunning ? (
            <button
              onClick={() => void handleCancelConnect()}
              disabled={actionBusy}
              className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                isDark ? "bg-rose-900/40 hover:bg-rose-800/40 text-rose-300 border border-rose-700/60" : "bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200"
              } disabled:opacity-50`}
            >
              {t("groupSpace.cancelConnect")}
            </button>
          ) : null}
        </div>

        {authFlow?.message ? (
          <div className={`mt-3 text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>{String(authFlow.message)}</div>
        ) : null}
        {authFlow?.error?.message ? (
          <div className={`mt-2 text-xs ${isDark ? "text-amber-300" : "text-amber-700"}`}>{String(authFlow.error.message)}</div>
        ) : null}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {NOTEBOOK_LANES.map((lane) => renderNotebookCard(lane))}
      </div>

      <div className={cardClass(isDark)}>
        <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t("groupSpace.summaryTitle")}</div>
        <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className={`rounded-lg border px-3 py-2 ${isDark ? "border-slate-700 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
            <div className={`text-[11px] uppercase tracking-wide ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("groupSpace.summaryGoogle")}</div>
            <div className={`mt-1 text-sm font-medium ${connectionStatusTone}`}>{connectionStatusText}</div>
          </div>
          <div className={`rounded-lg border px-3 py-2 ${isDark ? "border-slate-700 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
            <div className={`text-[11px] uppercase tracking-wide ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("groupSpace.summaryWork")}</div>
            <div className={`mt-1 text-sm font-medium ${isDark ? "text-slate-200" : "text-gray-900"}`}>{bindingStateText(workBinding, t)}</div>
          </div>
          <div className={`rounded-lg border px-3 py-2 ${isDark ? "border-slate-700 bg-slate-900/40" : "border-gray-200 bg-white"}`}>
            <div className={`text-[11px] uppercase tracking-wide ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("groupSpace.summaryMemory")}</div>
            <div className={`mt-1 text-sm font-medium ${isDark ? "text-slate-200" : "text-gray-900"}`}>{bindingStateText(memoryBinding, t)}</div>
          </div>
        </div>
        {connectionWarning ? (
          <div className={`mt-3 text-xs ${isDark ? "text-amber-300" : "text-amber-700"}`}>
            {t("groupSpace.summaryWarning")}: {connectionWarning}
          </div>
        ) : null}
      </div>

      {err ? <div className={`text-xs ${isDark ? "text-rose-300" : "text-rose-600"}`}>{err}</div> : null}
      {hint ? <div className={`text-xs ${isDark ? "text-emerald-300" : "text-emerald-600"}`}>{hint}</div> : null}
    </div>
  );
}
