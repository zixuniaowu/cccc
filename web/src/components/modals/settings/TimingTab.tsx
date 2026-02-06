// TimingTab configures timing-related settings with a clean, consistent vertical layout.
import React from "react";
import { cardClass, inputClass, labelClass, primaryButtonClass } from "./types";

interface TimingTabProps {
  isDark: boolean;
  busy: boolean;
  nudgeSeconds: number;
  setNudgeSeconds: (v: number) => void;
  replyRequiredNudgeSeconds: number;
  setReplyRequiredNudgeSeconds: (v: number) => void;
  attentionAckNudgeSeconds: number;
  setAttentionAckNudgeSeconds: (v: number) => void;
  unreadNudgeSeconds: number;
  setUnreadNudgeSeconds: (v: number) => void;
  nudgeDigestMinIntervalSeconds: number;
  setNudgeDigestMinIntervalSeconds: (v: number) => void;
  nudgeMaxRepeatsPerObligation: number;
  setNudgeMaxRepeatsPerObligation: (v: number) => void;
  nudgeEscalateAfterRepeats: number;
  setNudgeEscalateAfterRepeats: (v: number) => void;
  idleSeconds: number;
  setIdleSeconds: (v: number) => void;
  keepaliveSeconds: number;
  setKeepaliveSeconds: (v: number) => void;
  keepaliveMax: number;
  setKeepaliveMax: (v: number) => void;
  silenceSeconds: number;
  setSilenceSeconds: (v: number) => void;
  helpNudgeIntervalSeconds: number;
  setHelpNudgeIntervalSeconds: (v: number) => void;
  helpNudgeMinMessages: number;
  setHelpNudgeMinMessages: (v: number) => void;
  deliveryInterval: number;
  setDeliveryInterval: (v: number) => void;
  standupInterval: number;
  setStandupInterval: (v: number) => void;
  autoMarkOnDelivery: boolean;
  setAutoMarkOnDelivery: (v: boolean) => void;
  onSave: () => void;
  onAutoSave?: (field: string, value: number | boolean) => void;
}

// --- Icons ---

const ClockIcon = ({ className }: { className?: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    <circle cx="12" cy="12" r="10" />
    <polyline points="12 6 12 12 16 14" />
  </svg>
);

const TruckIcon = ({ className }: { className?: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    <path d="M5 18H3c-.6 0-1-.4-1-1V7c0-.6.4-1 1-1h10c.6 0 1 .4 1 1v11" />
    <path d="M14 9h4l4 4v4c0 .6-.4 1-1 1h-2" />
    <circle cx="7" cy="18" r="2" />
    <circle cx="17" cy="18" r="2" />
  </svg>
);

const BellIcon = ({ className }: { className?: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
  </svg>
);

const ShieldAlertIcon = ({ className }: { className?: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" />
    <path d="M12 8v4" />
    <path d="M12 16h.01" />
  </svg>
);

// --- Utilities ---

const formatDuration = (secondsRaw: number): string => {
  const seconds = Number.isFinite(secondsRaw) ? Math.max(0, Math.trunc(secondsRaw)) : 0;
  if (seconds <= 0) return "Off";
  const parts: string[] = [];
  let rem = seconds;
  const units: Array<[number, string]> = [
    [86400, "d"],
    [3600, "h"],
    [60, "m"],
    [1, "s"],
  ];
  for (const [unit, label] of units) {
    if (rem < unit) continue;
    const v = Math.floor(rem / unit);
    rem -= v * unit;
    parts.push(`${v}${label}`);
    if (parts.length >= 2) break;
  }
  return parts.join(" ");
};

// --- Components ---

const TimingSection = ({
  isDark,
  icon: Icon,
  title,
  description,
  children,
}: {
  isDark: boolean;
  icon: React.ElementType;
  title: string;
  description: string;
  children: React.ReactNode;
}) => (
  <div className={cardClass(isDark)}>
    <div className="flex items-center gap-2 mb-1">
      <div className={`p-1.5 rounded-md ${isDark ? "bg-slate-800 text-indigo-400" : "bg-indigo-50 text-indigo-600"}`}>
        <Icon className="w-4 h-4" />
      </div>
      <h3 className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{title}</h3>
    </div>
    <p className={`text-xs ml-9 mb-4 ${isDark ? "text-slate-500" : "text-gray-500"}`}>{description}</p>
    <div className="space-y-4 ml-1">{children}</div>
  </div>
);

const ToggleRow = ({
  label,
  checked,
  onChange,
  isDark,
  helperText,
  onAutoSave,
}: {
  label: string;
  checked: boolean;
  onChange: (val: boolean) => void;
  isDark: boolean;
  helperText?: React.ReactNode;
  onAutoSave?: (newValue: boolean) => void;
}) => (
  <div className="w-full">
    <label className="flex items-center justify-between cursor-pointer">
      <span className={labelClass(isDark)}>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => {
          const newValue = !checked;
          onChange(newValue);
          onAutoSave?.(newValue);
        }}
        className={`
          relative inline-flex h-6 w-11 items-center rounded-full transition-colors
          focus:outline-none focus:ring-2 focus:ring-offset-2
          ${checked
            ? "bg-emerald-500 focus:ring-emerald-500"
            : isDark
              ? "bg-slate-600 focus:ring-slate-500"
              : "bg-gray-300 focus:ring-gray-400"
          }
          ${isDark ? "focus:ring-offset-slate-900" : "focus:ring-offset-white"}
        `}
      >
        <span
          className={`
            inline-block h-4 w-4 rounded-full bg-white shadow-sm transform transition-transform
            ${checked ? "translate-x-6" : "translate-x-1"}
          `}
        />
      </button>
    </label>
    {helperText && (
      <div className={`mt-1.5 text-[11px] leading-snug ${isDark ? "text-slate-500" : "text-gray-500"}`}>
        {helperText}
      </div>
    )}
  </div>
);

const NumberInputRow = ({
  label,
  value,
  onChange,
  isDark,
  min = 0,
  helperText,
  formatValue = true,
  onAutoSave,
}: {
  label: string;
  value: number;
  onChange: (val: number) => void;
  isDark: boolean;
  min?: number;
  helperText?: React.ReactNode;
  formatValue?: boolean;
  onAutoSave?: () => void;
}) => (
  <div className="w-full">
    <label className={labelClass(isDark)}>{label}</label>
    <div className="relative">
      <input
        type="number"
        min={min}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        onBlur={() => onAutoSave?.()}
        className={inputClass(isDark)}
      />
      {formatValue && (
        <div
          className={`
          absolute right-3 top-1/2 -translate-y-1/2 text-xs font-mono
          pointer-events-none transition-opacity duration-200
          ${isDark ? "text-slate-600" : "text-gray-400"}
        `}
        >
          {formatDuration(value)}
        </div>
      )}
    </div>
    {helperText && (
      <div className={`mt-1.5 text-[11px] leading-snug ${isDark ? "text-slate-500" : "text-gray-500"}`}>
        {helperText}
      </div>
    )}
  </div>
);

// --- Main Export ---

export function TimingTab(props: TimingTabProps) {
  const { isDark, busy, onSave, onAutoSave } = props;

  // Create auto-save handlers for each field
  const autoSave = (field: string, getValue: () => number | boolean) => {
    if (onAutoSave) {
      // Use setTimeout to ensure state is updated before saving
      setTimeout(() => onAutoSave(field, getValue()), 0);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
      {/* Header */}
      <div>
        <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Group Timing</h3>
        <p className={`text-xs mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
          Configure automation cadence, delays, and activity thresholds. Set values to <span className="font-mono">0</span>{" "}
          to disable.
        </p>
      </div>

      {/* Delivery Section */}
      <TimingSection
        isDark={isDark}
        icon={TruckIcon}
        title="Delivery"
        description="Control how fast the system delivers messages to actors."
      >
        <NumberInputRow
          isDark={isDark}
          label="Delivery Interval (sec)"
          value={props.deliveryInterval}
          onChange={props.setDeliveryInterval}
          helperText="Minimum delay between message deliveries (throttling)."
          onAutoSave={() => autoSave("min_interval_seconds", () => props.deliveryInterval)}
        />
        <ToggleRow
          isDark={isDark}
          label="Auto-mark as read on delivery"
          checked={props.autoMarkOnDelivery}
          onChange={props.setAutoMarkOnDelivery}
          helperText="Automatically mark messages as read after successful delivery to PTY."
          onAutoSave={(newValue) => onAutoSave?.("auto_mark_on_delivery", newValue)}
        />
      </TimingSection>

      {/* Actor Reminders Section */}
      <TimingSection
        isDark={isDark}
        icon={BellIcon}
        title="Actor Reminders"
        description="Automated system nudges sent to active actors."
      >
        <NumberInputRow
          isDark={isDark}
          label="Unread Messages Reminder (sec)"
          value={props.nudgeSeconds}
          onChange={props.setNudgeSeconds}
          helperText="Nudge actor when unread messages exceed this age."
          onAutoSave={() => autoSave("nudge_after_seconds", () => props.nudgeSeconds)}
        />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <NumberInputRow
            isDark={isDark}
            label="Need Reply Reminder (sec)"
            value={props.replyRequiredNudgeSeconds}
            onChange={props.setReplyRequiredNudgeSeconds}
            helperText="For messages with reply_required=true."
            onAutoSave={() => autoSave("reply_required_nudge_after_seconds", () => props.replyRequiredNudgeSeconds)}
          />
          <NumberInputRow
            isDark={isDark}
            label="Attention ACK Reminder (sec)"
            value={props.attentionAckNudgeSeconds}
            onChange={props.setAttentionAckNudgeSeconds}
            helperText="For important messages awaiting acknowledgement."
            onAutoSave={() => autoSave("attention_ack_nudge_after_seconds", () => props.attentionAckNudgeSeconds)}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <NumberInputRow
            isDark={isDark}
            label="Unread Digest Reminder (sec)"
            value={props.unreadNudgeSeconds}
            onChange={props.setUnreadNudgeSeconds}
            helperText="For plain unread backlog nudges."
            onAutoSave={() => autoSave("unread_nudge_after_seconds", () => props.unreadNudgeSeconds)}
          />
          <NumberInputRow
            isDark={isDark}
            label="Digest Min Interval (sec)"
            value={props.nudgeDigestMinIntervalSeconds}
            onChange={props.setNudgeDigestMinIntervalSeconds}
            helperText="Minimum spacing between digest nudges per actor."
            onAutoSave={() => autoSave("nudge_digest_min_interval_seconds", () => props.nudgeDigestMinIntervalSeconds)}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <NumberInputRow
            isDark={isDark}
            label="Max Nudge Repeats"
            value={props.nudgeMaxRepeatsPerObligation}
            onChange={props.setNudgeMaxRepeatsPerObligation}
            formatValue={false}
            helperText="Max reminders per pending obligation item."
            onAutoSave={() => autoSave("nudge_max_repeats_per_obligation", () => props.nudgeMaxRepeatsPerObligation)}
          />
          <NumberInputRow
            isDark={isDark}
            label="Escalate After Repeats"
            value={props.nudgeEscalateAfterRepeats}
            onChange={props.setNudgeEscalateAfterRepeats}
            formatValue={false}
            helperText="Escalate to foreman when repeats reach this count."
            onAutoSave={() => autoSave("nudge_escalate_after_repeats", () => props.nudgeEscalateAfterRepeats)}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <NumberInputRow
            isDark={isDark}
            label="Keepalive Delay (sec)"
            value={props.keepaliveSeconds}
            onChange={props.setKeepaliveSeconds}
            helperText="Wait time after an actor says 'Next:'."
            onAutoSave={() => autoSave("keepalive_delay_seconds", () => props.keepaliveSeconds)}
          />
          <NumberInputRow
            isDark={isDark}
            label="Keepalive Max Retries"
            value={props.keepaliveMax}
            onChange={props.setKeepaliveMax}
            formatValue={false}
            helperText={props.keepaliveMax <= 0 ? "Infinite retries" : `Retry up to ${props.keepaliveMax} times`}
            onAutoSave={() => autoSave("keepalive_max_per_actor", () => props.keepaliveMax)}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <NumberInputRow
            isDark={isDark}
            label="Help Refresh Interval (sec)"
            value={props.helpNudgeIntervalSeconds}
            onChange={props.setHelpNudgeIntervalSeconds}
            helperText="Time since last reminder."
            onAutoSave={() => autoSave("help_nudge_interval_seconds", () => props.helpNudgeIntervalSeconds)}
          />
          <NumberInputRow
            isDark={isDark}
            label="Help Refresh Min Msgs"
            value={props.helpNudgeMinMessages}
            onChange={props.setHelpNudgeMinMessages}
            formatValue={false}
            helperText="Minimum accumulated messages."
            onAutoSave={() => autoSave("help_nudge_min_messages", () => props.helpNudgeMinMessages)}
          />
        </div>
      </TimingSection>

      {/* Foreman Alerts Section */}
      <TimingSection
        isDark={isDark}
        icon={ShieldAlertIcon}
        title="Foreman Alerts"
        description="System monitoring alerts sent to the foreman."
      >
        <NumberInputRow
          isDark={isDark}
          label="Actor Idle Alert (sec)"
          value={props.idleSeconds}
          onChange={props.setIdleSeconds}
          helperText="Alert foreman if actor is inactive for this long."
          onAutoSave={() => autoSave("actor_idle_timeout_seconds", () => props.idleSeconds)}
        />

        <NumberInputRow
          isDark={isDark}
          label="Group Silence Check (sec)"
          value={props.silenceSeconds}
          onChange={props.setSilenceSeconds}
          helperText="Alert foreman if the entire group is silent."
          onAutoSave={() => autoSave("silence_timeout_seconds", () => props.silenceSeconds)}
        />

        <NumberInputRow
          isDark={isDark}
          label="Review Cadence / Standup (sec)"
          value={props.standupInterval}
          onChange={props.setStandupInterval}
          helperText="Periodic reminder for foreman to review group status."
          onAutoSave={() => autoSave("standup_interval_seconds", () => props.standupInterval)}
        />
      </TimingSection>

      {/* Actions */}
      <div className="pt-2">
        <button onClick={onSave} disabled={busy} className={primaryButtonClass(busy)}>
          {busy ? (
            "Saving..."
          ) : (
            <span className="flex items-center gap-2">
              <ClockIcon className="w-4 h-4" /> Save Timing Settings
            </span>
          )}
        </button>
      </div>
    </div>
  );
}
