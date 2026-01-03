import { useState, useEffect } from "react";
import { Actor, GroupSettings, IMStatus } from "../types";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  settings: GroupSettings | null;
  onUpdateSettings: (settings: Partial<GroupSettings>) => Promise<void>;
  busy: boolean;
  isDark: boolean;
  groupId?: string;
}

type SettingsScope = "group" | "global";
type GroupTabId = "timing" | "im" | "transcript";
type GlobalTabId = "remote" | "developer";

export function SettingsModal({
  isOpen,
  onClose,
  settings,
  onUpdateSettings,
  busy,
  isDark,
  groupId,
}: SettingsModalProps) {
  const [scope, setScope] = useState<SettingsScope>(groupId ? "group" : "global");
  const [groupTab, setGroupTab] = useState<GroupTabId>("timing");
  const [globalTab, setGlobalTab] = useState<GlobalTabId>("remote");
  
  // Timing settings state
  const [nudgeSeconds, setNudgeSeconds] = useState(300);
  const [idleSeconds, setIdleSeconds] = useState(600);
  const [keepaliveSeconds, setKeepaliveSeconds] = useState(120);
  const [silenceSeconds, setSilenceSeconds] = useState(600);
  const [deliveryInterval, setDeliveryInterval] = useState(0);
  const [standupInterval, setStandupInterval] = useState(900);

  // Terminal transcript (group-scoped policy)
  const [terminalVisibility, setTerminalVisibility] = useState<"off" | "foreman" | "all">("foreman");
  const [terminalNotifyTail, setTerminalNotifyTail] = useState(false);
  const [terminalNotifyLines, setTerminalNotifyLines] = useState(20);

  // Terminal transcript tail viewer
  const [tailActorId, setTailActorId] = useState("");
  const [tailMaxChars, setTailMaxChars] = useState(8000);
  const [tailStripAnsi, setTailStripAnsi] = useState(true);
  const [tailCompact, setTailCompact] = useState(true);
  const [tailText, setTailText] = useState("");
  const [tailHint, setTailHint] = useState("");
  const [tailErr, setTailErr] = useState("");
  const [tailBusy, setTailBusy] = useState(false);
  const [tailCopyInfo, setTailCopyInfo] = useState("");

  // IM Bridge state
  const [imStatus, setImStatus] = useState<IMStatus | null>(null);
  const [imPlatform, setImPlatform] = useState<"telegram" | "slack" | "discord">("telegram");
  const [imBotTokenEnv, setImBotTokenEnv] = useState("");
  const [imAppTokenEnv, setImAppTokenEnv] = useState(""); // Slack only
  const [imBusy, setImBusy] = useState(false);

  // Global observability (developer mode)
  const [developerMode, setDeveloperMode] = useState(false);
  const [logLevel, setLogLevel] = useState<"INFO" | "DEBUG">("INFO");
  const [obsBusy, setObsBusy] = useState(false);

  // Developer-mode debug views
  const [devActors, setDevActors] = useState<Actor[]>([]);
  const [debugSnapshot, setDebugSnapshot] = useState("");
  const [debugSnapshotErr, setDebugSnapshotErr] = useState("");
  const [debugSnapshotBusy, setDebugSnapshotBusy] = useState(false);

  const [logComponent, setLogComponent] = useState<"daemon" | "web" | "im">("daemon");
  const [logLines, setLogLines] = useState(200);
  const [logText, setLogText] = useState("");
  const [logErr, setLogErr] = useState("");
  const [logBusy, setLogBusy] = useState(false);

  // Sync state when modal opens
  useEffect(() => {
    if (isOpen && settings) {
      setNudgeSeconds(settings.nudge_after_seconds);
      setIdleSeconds(settings.actor_idle_timeout_seconds);
      setKeepaliveSeconds(settings.keepalive_delay_seconds);
      setSilenceSeconds(settings.silence_timeout_seconds);
      setDeliveryInterval(settings.min_interval_seconds);
      setStandupInterval(settings.standup_interval_seconds ?? 900);

      setTerminalVisibility(settings.terminal_transcript_visibility || "foreman");
      setTerminalNotifyTail(Boolean(settings.terminal_transcript_notify_tail));
      setTerminalNotifyLines(Number(settings.terminal_transcript_notify_lines || 20));
    }
  }, [isOpen, settings]);

  // Default scope on open (group-first when opened from a group).
  useEffect(() => {
    if (!isOpen) return;
    setScope(groupId ? "group" : "global");
  }, [isOpen, groupId]);

  // Load IM config when modal opens
  useEffect(() => {
    if (isOpen && groupId) {
      loadIMStatus();
    }
  }, [isOpen, groupId]);

  // Load global observability when modal opens
  useEffect(() => {
    if (isOpen) {
      loadObservability();
    }
  }, [isOpen]);

  // Load actor list for transcript/developer tools
  useEffect(() => {
    if (!isOpen) return;
    if (!groupId) return;
    loadDevActors();
  }, [isOpen, groupId]);

  const loadIMStatus = async () => {
    if (!groupId) return;
    try {
      const resp = await fetch(`/api/im/status?group_id=${encodeURIComponent(groupId)}`);
      const data = await resp.json();
      if (data.ok) {
        setImStatus(data.result);
        if (data.result.platform) {
          setImPlatform(data.result.platform);
        }
      }
      // Also load config
      const configResp = await fetch(`/api/im/config?group_id=${encodeURIComponent(groupId)}`);
      const configData = await configResp.json();
      if (configData.ok && configData.result.im) {
        const im = configData.result.im;
        if (im.platform) setImPlatform(im.platform);
        setImBotTokenEnv(im.bot_token_env || im.token_env || im.bot_token || im.token || "");
        setImAppTokenEnv(im.app_token_env || im.app_token || "");
      }
    } catch (e) {
      console.error("Failed to load IM status:", e);
    }
  };

  const loadObservability = async () => {
    try {
      const resp = await fetch("/api/v1/observability");
      const data = await resp.json();
      if (data.ok && data.result?.observability) {
        const obs = data.result.observability;
        setDeveloperMode(Boolean(obs.developer_mode));
        const lvl = String(obs.log_level || "INFO").toUpperCase();
        setLogLevel(lvl === "DEBUG" ? "DEBUG" : "INFO");
      }
    } catch {
      // ignore
    }
  };

  const loadDevActors = async () => {
    if (!groupId) return;
    try {
      const resp = await fetch(`/api/v1/groups/${encodeURIComponent(groupId)}/actors?include_unread=false`);
      const data = await resp.json();
      if (data.ok && data.result?.actors) {
        const actors = Array.isArray(data.result.actors) ? (data.result.actors as Actor[]) : [];
        setDevActors(actors);
        if (!tailActorId && actors.length > 0) {
          setTailActorId(actors[0].id);
        }
      }
    } catch {
      // ignore
    }
  };

  const loadDebugSnapshot = async () => {
    if (!groupId) return;
    setDebugSnapshotBusy(true);
    setDebugSnapshotErr("");
    try {
      const resp = await fetch(`/api/v1/debug/snapshot?group_id=${encodeURIComponent(groupId)}`);
      const data = await resp.json();
      if (data.ok) {
        setDebugSnapshot(JSON.stringify(data.result ?? {}, null, 2));
      } else {
        setDebugSnapshot("");
        setDebugSnapshotErr(data.error?.message || "Failed to load debug snapshot");
      }
    } catch {
      setDebugSnapshot("");
      setDebugSnapshotErr("Failed to load debug snapshot");
    } finally {
      setDebugSnapshotBusy(false);
    }
  };

  const loadTerminalTail = async () => {
    if (!groupId || !tailActorId) return;
    setTailBusy(true);
    setTailErr("");
    try {
      const params = new URLSearchParams({
        actor_id: tailActorId,
        max_chars: String(tailMaxChars || 8000),
        strip_ansi: String(Boolean(tailStripAnsi)),
        compact: String(Boolean(tailCompact)),
      });
      const resp = await fetch(
        `/api/v1/groups/${encodeURIComponent(groupId)}/terminal/tail?${params.toString()}`
      );
      const data = await resp.json();
      if (data.ok) {
        setTailText(String(data.result?.text || ""));
        setTailHint(String(data.result?.hint || ""));
      } else {
        setTailText("");
        setTailHint("");
        setTailErr(data.error?.message || "Failed to load terminal transcript");
      }
    } catch {
      setTailText("");
      setTailHint("");
      setTailErr("Failed to load terminal transcript");
    } finally {
      setTailBusy(false);
    }
  };

  const loadLogTail = async () => {
    setLogBusy(true);
    setLogErr("");
    try {
      const params = new URLSearchParams({
        component: logComponent,
        group_id: groupId || "",
        lines: String(logLines || 200),
      });
      const resp = await fetch(`/api/v1/debug/tail_logs?${params.toString()}`);
      const data = await resp.json();
      if (data.ok) {
        const lines = Array.isArray(data.result?.lines) ? data.result.lines : [];
        setLogText(lines.join("\n"));
      } else {
        setLogText("");
        setLogErr(data.error?.message || "Failed to tail logs");
      }
    } catch {
      setLogText("");
      setLogErr("Failed to tail logs");
    } finally {
      setLogBusy(false);
    }
  };

  if (!isOpen) return null;

  const handleSaveSettings = async () => {
    await onUpdateSettings({
      nudge_after_seconds: nudgeSeconds,
      actor_idle_timeout_seconds: idleSeconds,
      keepalive_delay_seconds: keepaliveSeconds,
      silence_timeout_seconds: silenceSeconds,
      min_interval_seconds: deliveryInterval,
      standup_interval_seconds: standupInterval,
    });
  };

  const handleSaveTranscriptSettings = async () => {
    await onUpdateSettings({
      terminal_transcript_visibility: terminalVisibility,
      terminal_transcript_notify_tail: terminalNotifyTail,
      terminal_transcript_notify_lines: terminalNotifyLines,
    });
  };

  const copyTailLastLines = async (lineCount: number) => {
    const n = Math.max(1, Math.min(200, Number(lineCount || 0) || 50));
    const text = String(tailText || "");
    if (!text.trim()) return;
    const lines = text.split("\n");
    const payload = lines.slice(Math.max(0, lines.length - n)).join("\n").trimEnd();
    if (!payload) return;

    const setToast = (msg: string) => {
      setTailCopyInfo(msg);
      window.setTimeout(() => setTailCopyInfo(""), 1200);
    };

    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(payload);
        setToast(`Copied last ${n} lines`);
        return;
      }
    } catch {
      // fallback below
    }

    try {
      const el = document.createElement("textarea");
      el.value = payload;
      el.style.position = "fixed";
      el.style.left = "-9999px";
      el.style.top = "0";
      document.body.appendChild(el);
      el.focus();
      el.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(el);
      setToast(ok ? `Copied last ${n} lines` : "Copy failed");
    } catch {
      setToast("Copy failed");
    }
  };

  const clearTail = async () => {
    if (!groupId || !tailActorId) return;
    setTailBusy(true);
    setTailErr("");
    try {
      const resp = await fetch(
        `/api/v1/groups/${encodeURIComponent(groupId)}/terminal/clear?actor_id=${encodeURIComponent(tailActorId)}`,
        { method: "POST" }
      );
      const data = await resp.json();
      if (!data.ok) {
        setTailErr(data.error?.message || "Failed to clear terminal transcript");
        return;
      }
      setTailText("");
      setTailHint("");
    } catch {
      setTailErr("Failed to clear terminal transcript");
    } finally {
      setTailBusy(false);
    }
  };

  const handleSaveIMConfig = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      const resp = await fetch("/api/im/set", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          group_id: groupId,
          platform: imPlatform,
          bot_token_env: imBotTokenEnv,
          app_token_env: imPlatform === "slack" ? imAppTokenEnv : undefined,
        }),
      });
      const data = await resp.json();
      if (data.ok) {
        await loadIMStatus();
      }
    } catch (e) {
      console.error("Failed to save IM config:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleRemoveIMConfig = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      const resp = await fetch("/api/im/unset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: groupId }),
      });
      const data = await resp.json();
      if (data.ok) {
        setImBotTokenEnv("");
        setImAppTokenEnv("");
        await loadIMStatus();
      }
    } catch (e) {
      console.error("Failed to remove IM config:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleStartBridge = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      const resp = await fetch("/api/im/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: groupId }),
      });
      await resp.json();
      await loadIMStatus();
    } catch (e) {
      console.error("Failed to start bridge:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleStopBridge = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      const resp = await fetch("/api/im/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: groupId }),
      });
      await resp.json();
      await loadIMStatus();
    } catch (e) {
      console.error("Failed to stop bridge:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleSaveObservability = async () => {
    setObsBusy(true);
    try {
      const resp = await fetch("/api/v1/observability", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          by: "user",
          developer_mode: developerMode,
          log_level: logLevel,
        }),
      });
      const data = await resp.json();
      if (data.ok) {
        await loadObservability();
      }
    } catch {
      // ignore
    } finally {
      setObsBusy(false);
    }
  };

  const groupTabs: { id: GroupTabId; label: string }[] = [
    { id: "timing", label: "Timing" },
    { id: "im", label: "IM Bridge" },
    { id: "transcript", label: "Transcript" },
  ];
  const globalTabs: { id: GlobalTabId; label: string }[] = [
    { id: "remote", label: "Remote Access" },
    { id: "developer", label: "Developer" },
  ];
  const tabs = scope === "group" ? groupTabs : globalTabs;
  const activeTab = scope === "group" ? groupTab : globalTab;
  const setActiveTab = (tab: GroupTabId | GlobalTabId) => {
    if (scope === "group") setGroupTab(tab as GroupTabId);
    else setGlobalTab(tab as GlobalTabId);
  };

  // Token field labels based on platform
  const getBotTokenLabel = () => {
    switch (imPlatform) {
      case "telegram": return "Bot Token (token or env var)";
      case "slack": return "Bot Token (xoxb- or env var)";
      case "discord": return "Bot Token (token or env var)";
    }
  };

  const getBotTokenPlaceholder = () => {
    switch (imPlatform) {
      case "telegram": return "TELEGRAM_BOT_TOKEN (or 123456:ABC...)";
      case "slack": return "SLACK_BOT_TOKEN (or xoxb-...)";
      case "discord": return "DISCORD_BOT_TOKEN (or <token>)";
    }
  };

  const canSaveIM = () => {
    if (!imBotTokenEnv) return false;
    if (imPlatform === "slack" && !imAppTokenEnv) return false;
    return true;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in">
      {/* Backdrop */}
      <div 
        className={isDark ? "absolute inset-0 bg-black/60" : "absolute inset-0 bg-black/40"} 
        onClick={onClose}
        aria-hidden="true"
      />
      
      {/* Modal */}
      <div 
        className={`relative rounded-xl border shadow-2xl w-full max-w-lg max-h-[80vh] flex flex-col animate-scale-in ${
          isDark 
            ? "bg-slate-900 border-slate-700" 
            : "bg-white border-gray-200"
        }`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-modal-title"
      >
        {/* Header */}
        <div className={`flex items-center justify-between px-5 py-4 border-b ${
          isDark ? "border-slate-800" : "border-gray-200"
        }`}>
          <h2 id="settings-modal-title" className={`text-lg font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>
            ⚙️ Settings
          </h2>
          <button
            onClick={onClose}
            className={`text-xl leading-none min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg transition-colors ${
              isDark ? "text-slate-400 hover:text-slate-200 hover:bg-slate-800" : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
            }`}
            aria-label="Close settings"
          >
            ×
          </button>
        </div>

        {/* Scope */}
        <div className={`px-5 py-3 border-b ${isDark ? "border-slate-800" : "border-gray-200"}`}>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setScope("group")}
              disabled={!groupId}
              className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                scope === "group"
                  ? isDark
                    ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                    : "bg-emerald-50 text-emerald-700 border border-emerald-200"
                  : isDark
                    ? "bg-slate-900 text-slate-300 border border-slate-800 hover:bg-slate-800"
                    : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"
              } disabled:opacity-40`}
            >
              This group
            </button>
            <button
              type="button"
              onClick={() => setScope("global")}
              className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                scope === "global"
                  ? isDark
                    ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                    : "bg-emerald-50 text-emerald-700 border border-emerald-200"
                  : isDark
                    ? "bg-slate-900 text-slate-300 border border-slate-800 hover:bg-slate-800"
                    : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"
              }`}
            >
              Global
            </button>
          </div>
          {scope === "group" && groupId && (
            <div className={`mt-2 text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>
              Applies to <span className="font-mono">{groupId}</span> only.
            </div>
          )}
          {scope === "global" && (
            <div className={`mt-2 text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>
              Applies to your whole CCCC instance (daemon + Web).
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className={`flex border-b ${isDark ? "border-slate-800" : "border-gray-200"}`}>
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2.5 text-sm font-medium transition-colors ${
                activeTab === tab.id
                  ? isDark
                    ? "text-emerald-400 border-b-2 border-emerald-400"
                    : "text-emerald-600 border-b-2 border-emerald-600"
                  : isDark
                    ? "text-slate-400 hover:text-slate-200"
                    : "text-gray-500 hover:text-gray-700"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {/* Timing Tab */}
          {activeTab === "timing" && (
            <div className="space-y-4">
              <div>
                <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Group Timing</h3>
                <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  These settings apply to the current working group.
                </p>
              </div>
              
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Nudge After (sec)</label>
                  <input
                    type="number"
                    value={nudgeSeconds}
                    onChange={(e) => setNudgeSeconds(Number(e.target.value))}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  />
                </div>
                <div>
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Actor Idle (sec)</label>
                  <input
                    type="number"
                    value={idleSeconds}
                    onChange={(e) => setIdleSeconds(Number(e.target.value))}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  />
                </div>
                <div>
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Keepalive (sec)</label>
                  <input
                    type="number"
                    value={keepaliveSeconds}
                    onChange={(e) => setKeepaliveSeconds(Number(e.target.value))}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  />
                </div>
                <div>
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Silence (sec)</label>
                  <input
                    type="number"
                    value={silenceSeconds}
                    onChange={(e) => setSilenceSeconds(Number(e.target.value))}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  />
                </div>
                <div>
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Delivery Interval (sec)</label>
                  <input
                    type="number"
                    value={deliveryInterval}
                    onChange={(e) => setDeliveryInterval(Number(e.target.value))}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  />
                </div>
                <div>
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Standup Interval (sec)</label>
                  <input
                    type="number"
                    value={standupInterval}
                    onChange={(e) => setStandupInterval(Number(e.target.value))}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  />
                  <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                    Periodic review reminder (default 900 = 15 min)
                  </p>
                </div>
              </div>

              <button
                onClick={handleSaveSettings}
                disabled={busy}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg disabled:opacity-50 min-h-[44px] transition-colors font-medium"
              >
                {busy ? "Saving..." : "Save Timing Settings"}
              </button>
            </div>
          )}

          {/* IM Bridge Tab */}
          {activeTab === "im" && (
            <div className="space-y-4">
              <div>
                <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>IM Bridge</h3>
                <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  Connect this group to Telegram, Slack, or Discord.
                </p>
              </div>

              {/* Status */}
              {imStatus && (
                <div className={`p-3 rounded-lg ${isDark ? "bg-slate-800" : "bg-gray-100"}`}>
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${imStatus.running ? "bg-emerald-500" : "bg-gray-400"}`} />
                    <span className={`text-sm ${isDark ? "text-slate-300" : "text-gray-700"}`}>
                      {imStatus.running ? "Running" : "Stopped"}
                    </span>
                    {imStatus.running && imStatus.pid && (
                      <span className={`text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                        (PID: {imStatus.pid})
                      </span>
                    )}
                  </div>
                  {imStatus.configured && (
                    <div className={`text-xs mt-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                      Platform: {imStatus.platform} • Subscribers: {imStatus.subscribers}
                    </div>
                  )}
                </div>
              )}

              {/* Configuration */}
              <div className="space-y-3">
                <div>
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Platform</label>
                  <select
                    value={imPlatform}
                    onChange={(e) => setImPlatform(e.target.value as "telegram" | "slack" | "discord")}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  >
                    <option value="telegram">Telegram</option>
                    <option value="slack">Slack</option>
                    <option value="discord">Discord</option>
                  </select>
                </div>

                {/* Bot Token (all platforms) */}
                <div>
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                    {getBotTokenLabel()}
                  </label>
                  <input
                    type="text"
                    value={imBotTokenEnv}
                    onChange={(e) => setImBotTokenEnv(e.target.value)}
                    placeholder={getBotTokenPlaceholder()}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500 placeholder:text-slate-600" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500 placeholder:text-gray-400"
                    }`}
                  />
                  <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                    {imPlatform === "slack"
                      ? "Paste xoxb-… token or an env var name; required for outbound messages."
                      : "Paste the bot token or an env var name; required for bot authentication."}
                  </p>
                </div>

                {/* App Token (Slack only) */}
                {imPlatform === "slack" && (
                  <div>
                    <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                      App Token (xapp- or env var)
                    </label>
                    <input
                      type="text"
                      value={imAppTokenEnv}
                      onChange={(e) => setImAppTokenEnv(e.target.value)}
                      placeholder="SLACK_APP_TOKEN (or xapp-...)"
                      className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                        isDark 
                          ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500 placeholder:text-slate-600" 
                          : "bg-white border-gray-300 text-gray-900 focus:border-blue-500 placeholder:text-gray-400"
                      }`}
                    />
                    <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-400"}`}>
                      Optional; needed for inbound messages (Socket Mode).
                    </p>
                  </div>
                )}
              </div>

              {/* Actions */}
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={handleSaveIMConfig}
                  disabled={imBusy || !canSaveIM()}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg disabled:opacity-50 min-h-[44px] transition-colors font-medium"
                >
                  {imBusy ? "Saving..." : "Save Config"}
                </button>

                {imStatus?.configured && (
                  <>
                    {imStatus.running ? (
                      <button
                        onClick={handleStopBridge}
                        disabled={imBusy}
                        className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium ${
                          isDark
                            ? "bg-red-900/50 hover:bg-red-800/50 text-red-300"
                            : "bg-red-100 hover:bg-red-200 text-red-700"
                        } disabled:opacity-50`}
                      >
                        Stop Bridge
                      </button>
                    ) : (
                      <button
                        onClick={handleStartBridge}
                        disabled={imBusy}
                        className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium ${
                          isDark
                            ? "bg-blue-900/50 hover:bg-blue-800/50 text-blue-300"
                            : "bg-blue-100 hover:bg-blue-200 text-blue-700"
                        } disabled:opacity-50`}
                      >
                        Start Bridge
                      </button>
                    )}

                    <button
                      onClick={handleRemoveIMConfig}
                      disabled={imBusy}
                      className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium ${
                        isDark
                          ? "bg-slate-800 hover:bg-slate-700 text-slate-300"
                          : "bg-gray-200 hover:bg-gray-300 text-gray-700"
                      } disabled:opacity-50`}
                    >
                      Remove Config
                    </button>
                  </>
                )}
              </div>

              {/* Help */}
              <div className={`text-xs space-y-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                <p>To use IM Bridge:</p>
                <ol className="list-decimal list-inside space-y-0.5 ml-2">
                  <li>Create a bot on your IM platform</li>
                  <li>Set the token(s) as environment variable(s)</li>
                  <li>Save the config and start the bridge</li>
                  <li>In your IM chat, send /subscribe to receive messages</li>
                </ol>
              </div>
            </div>
          )}

          {/* Transcript Tab */}
          {activeTab === "transcript" && (
            <div className="space-y-4">
              <div>
                <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Terminal transcript</h3>
                <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  Readable tail for troubleshooting. User can always view; agent access is controlled by the policy below.
                </p>
              </div>

              <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"}`}>
                <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>Policy</div>

                <div className="mt-3 space-y-3">
                  <div>
                    <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>Visibility to agents</label>
                    <select
                      value={terminalVisibility}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === "off" || v === "foreman" || v === "all") setTerminalVisibility(v);
                      }}
                      className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                        isDark
                          ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500"
                          : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                      }`}
                    >
                      <option value="off">off (agents cannot read others)</option>
                      <option value="foreman">foreman (foreman can read peers)</option>
                      <option value="all">all (any agent can read others)</option>
                    </select>
                    <div className={`mt-1 text-[11px] ${isDark ? "text-slate-500" : "text-gray-600"}`}>
                      Tip: This only affects agents. The Web UI user can always view transcripts.
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <label className={`inline-flex items-center gap-2 text-sm ${isDark ? "text-slate-300" : "text-gray-700"}`}>
                      <input
                        type="checkbox"
                        checked={terminalNotifyTail}
                        onChange={(e) => setTerminalNotifyTail(e.target.checked)}
                        className="h-4 w-4"
                      />
                      Include tail in idle notifications
                    </label>
                    <div>
                      <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>Notification lines</label>
                      <input
                        type="number"
                        value={terminalNotifyLines}
                        min={1}
                        max={80}
                        onChange={(e) => setTerminalNotifyLines(Number(e.target.value || 20))}
                        disabled={!terminalNotifyTail}
                        className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                          isDark
                            ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500"
                            : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                        } disabled:opacity-60`}
                      />
                    </div>
                  </div>

                  <button
                    onClick={handleSaveTranscriptSettings}
                    disabled={busy}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg disabled:opacity-50 min-h-[44px] transition-colors font-medium"
                  >
                    {busy ? "Saving..." : "Save transcript settings"}
                  </button>
                </div>
              </div>

              <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"}`}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>Tail viewer</div>
                    <div className={`text-xs mt-0.5 ${isDark ? "text-slate-500" : "text-gray-600"}`}>
                      Best-effort transcript tail from the actor PTY ring buffer.
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={loadTerminalTail}
                      disabled={!groupId || !tailActorId || tailBusy}
                      className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                        isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
                      } disabled:opacity-50`}
                    >
                      {tailBusy ? "Loading..." : "Refresh"}
                    </button>
                    <button
                      onClick={() => copyTailLastLines(50)}
                      disabled={!tailText.trim()}
                      className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                        isDark ? "bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-200"
                      } disabled:opacity-50`}
                    >
                      Copy last 50 lines
                    </button>
                  </div>
                </div>

                <div className="mt-3 grid grid-cols-1 gap-2">
                  <label className={`block text-xs ${isDark ? "text-slate-400" : "text-gray-600"}`}>Actor</label>
                  <select
                    value={tailActorId}
                    onChange={(e) => setTailActorId(e.target.value)}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500"
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  >
                    {devActors.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.id}{a.role ? ` (${a.role})` : ""}
                      </option>
                    ))}
                    {!devActors.length && <option value="">(no actors)</option>}
                  </select>

                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>Max chars</label>
                      <input
                        type="number"
                        value={tailMaxChars}
                        min={1000}
                        max={200000}
                        onChange={(e) => setTailMaxChars(Number(e.target.value || 8000))}
                        className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                          isDark
                            ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500"
                            : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                        }`}
                      />
                    </div>
                    <div className="flex items-end justify-end">
                      <button
                        onClick={clearTail}
                        disabled={!groupId || !tailActorId || tailBusy}
                        className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                          isDark ? "bg-rose-900/30 hover:bg-rose-900/40 text-rose-200 border border-rose-900/30" : "bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200"
                        } disabled:opacity-50`}
                      >
                        Clear (truncate)
                      </button>
                    </div>
                  </div>

                  <div className="flex flex-col gap-2">
                    <label className={`inline-flex items-center gap-2 text-sm ${isDark ? "text-slate-300" : "text-gray-700"}`}>
                      <input
                        type="checkbox"
                        checked={tailStripAnsi}
                        onChange={(e) => setTailStripAnsi(e.target.checked)}
                        className="h-4 w-4"
                      />
                      Strip ANSI
                    </label>
                    <label className={`inline-flex items-center gap-2 text-sm ${isDark ? "text-slate-300" : "text-gray-700"}`}>
                      <input
                        type="checkbox"
                        checked={tailCompact}
                        disabled={!tailStripAnsi}
                        onChange={(e) => setTailCompact(e.target.checked)}
                        className="h-4 w-4"
                      />
                      Compact repeated frames
                    </label>
                  </div>
                </div>

                {!!tailCopyInfo && (
                  <div className={`mt-2 text-xs ${isDark ? "text-emerald-300" : "text-emerald-700"}`}>{tailCopyInfo}</div>
                )}
                {tailErr && (
                  <div className={`mt-2 text-xs ${isDark ? "text-rose-300" : "text-rose-600"}`}>{tailErr}</div>
                )}
                {tailHint && !tailErr && (
                  <div className={`mt-2 text-xs ${isDark ? "text-slate-500" : "text-gray-600"}`}>{tailHint}</div>
                )}

                <pre className={`mt-2 p-2 rounded overflow-x-auto whitespace-pre text-[11px] max-h-[300px] overflow-y-auto ${isDark ? "bg-slate-900 text-slate-200" : "bg-white text-gray-800 border border-gray-200"}`}>
                  <code>{tailText || "—"}</code>
                </pre>
              </div>
            </div>
          )}

          {/* Remote Access Tab */}
          {activeTab === "remote" && (
            <div className="space-y-4">
              <div>
                <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Remote Access (Phone)</h3>
                <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  Recommended for “anywhere access”: use Cloudflare Tunnel or Tailscale. CCCC does not manage these for you yet—this is a setup guide.
                </p>
                <div className={`mt-2 rounded-lg border px-3 py-2 text-[11px] ${
                  isDark ? "border-amber-500/30 bg-amber-500/10 text-amber-200" : "border-amber-200 bg-amber-50 text-amber-800"
                }`}>
                  <div className="font-medium">Security note</div>
                  <div className="mt-1">
                    Treat the Web UI as <span className="font-medium">high privilege</span> (it can control agents and access project files). Do not expose it to the public internet without access control (e.g., Cloudflare Access).
                  </div>
                </div>
              </div>

              <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"}`}>
                <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>Cloudflare Tunnel (recommended)</div>
                <div className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-600"}`}>
                  Easiest for phone access: no VPN app required. Pair with Cloudflare Zero Trust Access for login protection.
                </div>

                <div className={`mt-3 text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Quick (temporary URL)</div>
                <pre className={`mt-1.5 p-2 rounded overflow-x-auto whitespace-pre text-[11px] ${isDark ? "bg-slate-900 text-slate-200" : "bg-white text-gray-800 border border-gray-200"}`}>
                  <code>{`# Install cloudflared first, then:\ncloudflared tunnel --url http://127.0.0.1:8848\n# It will print a https://....trycloudflare.com URL`}</code>
                </pre>

                <div className={`mt-3 text-xs font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Stable (your domain)</div>
                <pre className={`mt-1.5 p-2 rounded overflow-x-auto whitespace-pre text-[11px] ${isDark ? "bg-slate-900 text-slate-200" : "bg-white text-gray-800 border border-gray-200"}`}>
                  <code>{`# 1) Authenticate\ncloudflared tunnel login\n\n# 2) Create a named tunnel\ncloudflared tunnel create cccc\n\n# 3) Route DNS (replace with your hostname)\ncloudflared tunnel route dns cccc cccc.example.com\n\n# 4) Create ~/.cloudflared/config.yml (example):\n# tunnel: <TUNNEL-UUID>\n# credentials-file: /home/<you>/.cloudflared/<TUNNEL-UUID>.json\n# ingress:\n#   - hostname: cccc.example.com\n#     service: http://127.0.0.1:8848\n#   - service: http_status:404\n\n# 5) Run\ncloudflared tunnel run cccc`}</code>
                </pre>

                <div className={`mt-2 text-[11px] ${isDark ? "text-slate-500" : "text-gray-600"}`}>
                  Tip: In Cloudflare Zero Trust → Access → Applications, create a “Self-hosted” app for your hostname to require login.
                </div>
              </div>

              <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"}`}>
                <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>Tailscale (VPN)</div>
                <div className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-600"}`}>
                  Strong option if you’re okay installing Tailscale on your phone. You can keep CCCC bound to a private interface.
                </div>
                <pre className={`mt-2 p-2 rounded overflow-x-auto whitespace-pre text-[11px] ${isDark ? "bg-slate-900 text-slate-200" : "bg-white text-gray-800 border border-gray-200"}`}>
                  <code>{`# 1) Install Tailscale on the server + phone, then on the server:\ntailscale up\n\n# 2) Get your tailnet IP\nTAILSCALE_IP=$(tailscale ip -4)\n\n# 3) Bind Web UI to that IP (so it's only reachable via tailnet)\nCCCC_WEB_HOST=$TAILSCALE_IP CCCC_WEB_PORT=8848 cccc\n\n# 4) On phone browser:\n# http://<TAILSCALE_IP>:8848/ui/`}</code>
                </pre>
              </div>

              <div className={`text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                Phone tip: On iOS/Android you can “Add to Home Screen” for an app-like launcher (PWA-style).
              </div>
            </div>
          )}

          {/* Developer (Global) Tab */}
          {activeTab === "developer" && (
            <div className="space-y-4">
              <div>
                <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Developer Mode (Global)</h3>
                <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                  These settings apply to the whole CCCC instance (daemon + Web). Use this only when debugging.
                </p>
                <div className={`mt-2 rounded-lg border px-3 py-2 text-[11px] ${
                  isDark ? "border-amber-500/30 bg-amber-500/10 text-amber-200" : "border-amber-200 bg-amber-50 text-amber-800"
                }`}>
                  <div className="font-medium">Warning</div>
                  <div className="mt-1">
                    Developer mode enables verbose logs and extra diagnostics. Only enable it when you need it.
                  </div>
                </div>
              </div>

              <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"}`}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>Enable developer mode</div>
                    <div className={`text-xs mt-0.5 ${isDark ? "text-slate-500" : "text-gray-600"}`}>
                      Enables debug snapshot and log tail tools.
                    </div>
                  </div>
                  <label className="inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={developerMode}
                      onChange={(e) => setDeveloperMode(e.target.checked)}
                    />
                    <div className={`w-11 h-6 rounded-full transition-colors ${
                      developerMode
                        ? (isDark ? "bg-emerald-600" : "bg-emerald-500")
                        : (isDark ? "bg-slate-700" : "bg-gray-300")
                    }`}>
                      <div className={`w-5 h-5 bg-white rounded-full shadow transform transition-transform mt-0.5 ${
                        developerMode ? "translate-x-5" : "translate-x-0.5"
                      }`} />
                    </div>
                  </label>
                </div>

                <div className="mt-3">
                  <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Log level</label>
                  <select
                    value={logLevel}
                    onChange={(e) => setLogLevel((e.target.value === "DEBUG" ? "DEBUG" : "INFO"))}
                    className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                        : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                    }`}
                  >
                    <option value="INFO">INFO</option>
                    <option value="DEBUG">DEBUG</option>
                  </select>
                </div>

                <div className="mt-3 flex gap-2">
                  <button
                    onClick={handleSaveObservability}
                    disabled={obsBusy}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg disabled:opacity-50 min-h-[44px] transition-colors font-medium"
                  >
                    {obsBusy ? "Saving..." : "Save Developer Settings"}
                  </button>
                </div>
              </div>

              <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"}`}>
                <div className="flex items-center justify-between gap-3">
	                  <div>
	                    <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>Debug snapshot (this group)</div>
	                    <div className={`text-xs mt-0.5 ${isDark ? "text-slate-500" : "text-gray-600"}`}>
	                      Shows daemon/actors state + delivery throttle summary (developer mode only).
	                    </div>
	                  </div>
	                  <div className="flex gap-2">
	                    <button
	                      onClick={loadDebugSnapshot}
	                      disabled={!developerMode || !groupId || debugSnapshotBusy}
	                      className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
	                        isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
	                      } disabled:opacity-50`}
	                    >
	                      {debugSnapshotBusy ? "Loading..." : "Refresh"}
	                    </button>
	                    <button
	                      onClick={() => {
	                        setDebugSnapshot("");
	                        setDebugSnapshotErr("");
	                      }}
	                      disabled={debugSnapshotBusy}
	                      className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
	                        isDark ? "bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-200"
	                      } disabled:opacity-50`}
	                    >
	                      Clear
	                    </button>
	                  </div>
	                </div>

                {!groupId && (
                  <div className={`mt-2 text-xs ${isDark ? "text-slate-500" : "text-gray-600"}`}>
                    Open Settings from an active group to view group-scoped debug info.
                  </div>
                )}

                {debugSnapshotErr && (
                  <div className={`mt-2 text-xs ${isDark ? "text-rose-300" : "text-rose-600"}`}>{debugSnapshotErr}</div>
                )}

                <pre className={`mt-2 p-2 rounded overflow-x-auto whitespace-pre text-[11px] ${isDark ? "bg-slate-900 text-slate-200" : "bg-white text-gray-800 border border-gray-200"}`}>
                  <code>{debugSnapshot || "—"}</code>
                </pre>
              </div>

              <div className={`rounded-lg border p-3 ${isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-gray-50"}`}>
                <div className="flex items-center justify-between gap-3">
	                  <div>
	                    <div className={`text-sm font-semibold ${isDark ? "text-slate-200" : "text-gray-800"}`}>Log tail</div>
	                    <div className={`text-xs mt-0.5 ${isDark ? "text-slate-500" : "text-gray-600"}`}>
	                      Tails local log files (developer mode only). Component “im” is group-scoped.
	                    </div>
	                  </div>
	                  <div className="flex gap-2">
	                    <button
	                      onClick={loadLogTail}
	                      disabled={!developerMode || logBusy}
	                      className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
	                        isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-800 border border-gray-200"
	                      } disabled:opacity-50`}
	                    >
	                      {logBusy ? "Loading..." : "Refresh"}
	                    </button>
	                    <button
	                      onClick={async () => {
	                        if (!developerMode) return;
	                        if (logComponent === "im" && !groupId) {
	                          setLogErr("IM logs require a group_id; open Settings from a group.");
	                          return;
	                        }
	                        setLogBusy(true);
	                        setLogErr("");
	                        try {
	                          const resp = await fetch("/api/v1/debug/clear_logs", {
	                            method: "POST",
	                            headers: { "Content-Type": "application/json" },
	                            body: JSON.stringify({ component: logComponent, group_id: groupId || "", by: "user" }),
	                          });
	                          const data = await resp.json();
	                          if (!data.ok) {
	                            setLogErr(data.error?.message || "Failed to clear logs");
	                            return;
	                          }
	                          setLogText("");
	                        } catch {
	                          setLogErr("Failed to clear logs");
	                        } finally {
	                          setLogBusy(false);
	                        }
	                      }}
	                      disabled={!developerMode || logBusy}
	                      className={`px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
	                        isDark ? "bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-200"
	                      } disabled:opacity-50`}
	                    >
	                      Clear (truncate)
	                    </button>
	                  </div>
	                </div>

                <div className="mt-3 grid grid-cols-2 gap-2">
                  <div>
                    <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>Component</label>
                    <select
                      value={logComponent}
                      onChange={(e) => setLogComponent((e.target.value === "im" ? "im" : e.target.value === "web" ? "web" : "daemon"))}
                      className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                        isDark 
                          ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                          : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                      }`}
                    >
                      <option value="daemon">daemon</option>
                      <option value="web">web</option>
                      <option value="im">im</option>
                    </select>
                  </div>
                  <div>
                    <label className={`block text-xs mb-1 ${isDark ? "text-slate-400" : "text-gray-600"}`}>Lines</label>
                    <input
                      type="number"
                      value={logLines}
                      min={50}
                      max={2000}
                      onChange={(e) => setLogLines(Number(e.target.value || 200))}
                      className={`w-full px-3 py-2.5 rounded-lg border text-sm min-h-[44px] transition-colors ${
                        isDark 
                          ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                          : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                      }`}
                    />
                  </div>
                </div>

                {logComponent === "im" && !groupId && (
                  <div className={`mt-2 text-xs ${isDark ? "text-slate-500" : "text-gray-600"}`}>
                    IM logs require a group_id; open Settings from a group.
                  </div>
                )}

                {logErr && (
                  <div className={`mt-2 text-xs ${isDark ? "text-rose-300" : "text-rose-600"}`}>{logErr}</div>
                )}

                <pre className={`mt-2 p-2 rounded overflow-x-auto whitespace-pre text-[11px] max-h-[260px] overflow-y-auto ${isDark ? "bg-slate-900 text-slate-200" : "bg-white text-gray-800 border border-gray-200"}`}>
                  <code>{logText || "—"}</code>
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
