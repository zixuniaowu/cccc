import { useState, useEffect } from "react";
import { GroupSettings, Theme } from "../types";
import { ThemeToggle } from "./ThemeToggle";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  settings: GroupSettings | null;
  onUpdateSettings: (settings: Partial<GroupSettings>) => Promise<void>;
  busy: boolean;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  isDark: boolean;
}

export function SettingsModal({
  isOpen,
  onClose,
  settings,
  onUpdateSettings,
  busy,
  theme,
  onThemeChange,
  isDark,
}: SettingsModalProps) {
  const [nudgeSeconds, setNudgeSeconds] = useState(300);
  const [idleSeconds, setIdleSeconds] = useState(600);
  const [keepaliveSeconds, setKeepaliveSeconds] = useState(120);
  const [silenceSeconds, setSilenceSeconds] = useState(600);
  const [deliveryInterval, setDeliveryInterval] = useState(60);
  const [standupInterval, setStandupInterval] = useState(900);

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

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {/* Appearance */}
          <div className="space-y-3">
            <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Appearance</h3>
            <div>
              <label className={`block text-xs mb-2 ${isDark ? "text-slate-400" : "text-gray-500"}`}>Theme</label>
              <ThemeToggle theme={theme} onThemeChange={onThemeChange} isDark={isDark} />
            </div>
          </div>

          {/* Group Timing Settings */}
          <div className="space-y-3">
            <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Group Timing</h3>
            <p className={`text-xs ${isDark ? "text-slate-500" : "text-gray-500"}`}>
              These settings apply to the current working group.
            </p>
            
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
        </div>
      </div>
    </div>
  );
}
