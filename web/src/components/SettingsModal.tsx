import { useState, useEffect } from "react";
import { GroupSettings, IMStatus } from "../types";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  settings: GroupSettings | null;
  onUpdateSettings: (settings: Partial<GroupSettings>) => Promise<void>;
  busy: boolean;
  isDark: boolean;
  groupId?: string;
}

// Note: "remote" is informational only (no settings persisted yet).
type TabId = "timing" | "im" | "remote";

export function SettingsModal({
  isOpen,
  onClose,
  settings,
  onUpdateSettings,
  busy,
  isDark,
  groupId,
}: SettingsModalProps) {
  const [activeTab, setActiveTab] = useState<TabId>("timing");
  
  // Timing settings state
  const [nudgeSeconds, setNudgeSeconds] = useState(300);
  const [idleSeconds, setIdleSeconds] = useState(600);
  const [keepaliveSeconds, setKeepaliveSeconds] = useState(120);
  const [silenceSeconds, setSilenceSeconds] = useState(600);
  const [deliveryInterval, setDeliveryInterval] = useState(60);
  const [standupInterval, setStandupInterval] = useState(900);

  // IM Bridge state
  const [imStatus, setImStatus] = useState<IMStatus | null>(null);
  const [imPlatform, setImPlatform] = useState<"telegram" | "slack" | "discord">("telegram");
  const [imBotTokenEnv, setImBotTokenEnv] = useState("");
  const [imAppTokenEnv, setImAppTokenEnv] = useState(""); // Slack only
  const [imBusy, setImBusy] = useState(false);

  // Sync state when modal opens
  useEffect(() => {
    if (isOpen && settings) {
      setNudgeSeconds(settings.nudge_after_seconds);
      setIdleSeconds(settings.actor_idle_timeout_seconds);
      setKeepaliveSeconds(settings.keepalive_delay_seconds);
      setSilenceSeconds(settings.silence_timeout_seconds);
      setDeliveryInterval(settings.min_interval_seconds);
      setStandupInterval(settings.standup_interval_seconds ?? 900);
    }
  }, [isOpen, settings]);

  // Load IM config when modal opens
  useEffect(() => {
    if (isOpen && groupId) {
      loadIMStatus();
    }
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

  const tabs: { id: TabId; label: string }[] = [
    { id: "timing", label: "Timing" },
    { id: "im", label: "IM Bridge" },
    { id: "remote", label: "Remote Access" },
  ];

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
        </div>
      </div>
    </div>
  );
}
