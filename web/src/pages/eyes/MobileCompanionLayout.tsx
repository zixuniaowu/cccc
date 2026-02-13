import React, { useEffect, useRef, useState, useCallback } from "react";
import { QRCodeSVG } from "qrcode.react";
import type { Mood } from "./types";
import { MOOD_COLOR, buildShareUrl, RIGHT_EYE_PARALLAX } from "./constants";
import { EyeCanvas } from "./EyeCanvas";
import { classNames } from "../../utils/classNames";

interface MobileCompanionLayoutProps {
  mood: Mood;
  blink: boolean;
  pupilOffset: { x: number; y: number };
  ambient: number;
  listening: boolean;
  sseConnected: boolean;
  groupId: string | null;
  groupTitle: string;
  onToggleListening: () => void;
  voiceEnabled: boolean;
  onSetVoiceEnabled: (v: boolean) => void;
  autoListen: boolean;
  onSetAutoListen: (v: boolean | ((prev: boolean) => boolean)) => void;
  speechSupported: boolean;
  onRequestTilt?: () => void;
  interimText?: string;
  lastAgentReply?: string;
}

/**
 * Full-screen mobile companion mode.
 * - Eyes fill 60% of viewport
 * - Tap anywhere = toggle listening
 * - Swipe up from bottom = settings panel
 * - Wake Lock API keeps screen on
 * - iOS audio unlock on first touch
 */
export function MobileCompanionLayout(props: MobileCompanionLayoutProps) {
  const {
    mood,
    blink,
    pupilOffset,
    ambient,
    listening,
    sseConnected,
    groupId,
    groupTitle,
    onToggleListening,
    voiceEnabled,
    onSetVoiceEnabled,
    autoListen,
    onSetAutoListen,
    speechSupported,
    onRequestTilt,
    interimText,
    lastAgentReply,
  } = props;

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [visibleReply, setVisibleReply] = useState("");

  // 2.6: Show agent reply text for 3 seconds then fade out
  useEffect(() => {
    if (!lastAgentReply) return;
    setVisibleReply(lastAgentReply);
    const timer = setTimeout(() => setVisibleReply(""), 4000);
    return () => clearTimeout(timer);
  }, [lastAgentReply]);
  const [iosUnlocked, setIosUnlocked] = useState(false);
  const touchStartRef = useRef<{ y: number; ts: number } | null>(null);
  const wakeLockRef = useRef<WakeLockSentinel | null>(null);

  const eyeMood: Mood = listening ? "listening" : mood;
  const accent = MOOD_COLOR[eyeMood];

  // ── Wake Lock API ──
  useEffect(() => {
    let lock: WakeLockSentinel | null = null;
    const request = async () => {
      try {
        if ("wakeLock" in navigator) {
          lock = await navigator.wakeLock.request("screen");
          wakeLockRef.current = lock;
        }
      } catch {
        // Wake Lock not supported or denied
      }
    };
    void request();

    // Re-acquire on visibility change (Chrome releases on tab switch)
    const handleVisibility = () => {
      if (document.visibilityState === "visible") void request();
    };
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      if (lock) {
        try {
          void lock.release();
        } catch {}
      }
    };
  }, []);

  // ── iOS audio unlock on first touch ──
  const handleFirstTouch = useCallback(() => {
    if (iosUnlocked) return;
    if ("speechSynthesis" in window) {
      const u = new SpeechSynthesisUtterance("");
      u.volume = 0;
      window.speechSynthesis.speak(u);
    }
    // Also try AudioContext unlock
    try {
      const ac = new AudioContext();
      ac.resume().then(() => ac.close());
    } catch {}
    // Request gyroscope permission (iOS 13+ requires user gesture)
    onRequestTilt?.();
    setIosUnlocked(true);
  }, [iosUnlocked, onRequestTilt]);

  // ── Tap = toggle listening, Swipe up = settings ──
  const handleTouchStart = (e: React.TouchEvent) => {
    handleFirstTouch();
    const touch = e.touches[0];
    if (!touch) return;
    touchStartRef.current = { y: touch.clientY, ts: Date.now() };
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    const start = touchStartRef.current;
    if (!start) return;
    touchStartRef.current = null;

    const touch = e.changedTouches[0];
    if (!touch) return;
    const dy = start.y - touch.clientY;
    const elapsed = Date.now() - start.ts;

    // Swipe up from bottom 25% of screen
    if (
      dy > 80 &&
      elapsed < 500 &&
      start.y > window.innerHeight * 0.75
    ) {
      setSettingsOpen(true);
      return;
    }

    // Simple tap (small movement, short duration)
    if (Math.abs(dy) < 20 && elapsed < 400) {
      if (settingsOpen) {
        setSettingsOpen(false);
      } else {
        onToggleListening();
      }
    }
  };

  // Share URL for connecting from another device
  const shareUrl = groupId ? buildShareUrl(groupId) : "";

  return (
    <div
      className="fixed inset-0 bg-black flex flex-col select-none"
      style={{ "--eye-accent": accent } as React.CSSProperties}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onClick={(e) => {
        // Desktop click fallback for toggle
        if (!(e.target as HTMLElement).closest("button")) {
          handleFirstTouch();
          if (!settingsOpen) onToggleListening();
        }
      }}
    >
      {/* Eyes area — 60% height */}
      <div className="flex-1 flex items-center justify-center" style={{ minHeight: "60vh" }}>
        <div className="eyes-pair" style={{ width: "85vw", maxWidth: 420 }}>
          <EyeCanvas
            mood={eyeMood}
            blink={blink}
            pupilOffset={pupilOffset}
            ambient={ambient}
          />
          <EyeCanvas
            mood={eyeMood}
            blink={blink}
            pupilOffset={{ x: pupilOffset.x * RIGHT_EYE_PARALLAX, y: pupilOffset.y }}
            ambient={ambient}
          />
        </div>
      </div>

      {/* Listening pulse indicator */}
      {listening && (
        <div className="absolute top-8 left-1/2 -translate-x-1/2">
          <div
            className="w-3 h-3 rounded-full animate-pulse"
            style={{ backgroundColor: MOOD_COLOR.listening, boxShadow: `0 0 12px ${MOOD_COLOR.listening}` }}
          />
        </div>
      )}

      {/* 1.1: Interim speech text bubble */}
      {interimText && (
        <div className="px-6 pb-2 text-center animate-bubble-in">
          <span className="inline-block px-4 py-2 rounded-2xl bg-white/10 text-white/70 text-sm backdrop-blur">
            {interimText}
          </span>
        </div>
      )}

      {/* 2.6: Agent reply text (fades after 4s) */}
      {visibleReply && (
        <div className="px-6 pb-2 text-center animate-bubble-in">
          <span className="inline-block px-4 py-2 rounded-2xl bg-white/10 text-white/80 text-sm backdrop-blur max-w-[90vw] line-clamp-3">
            {visibleReply}
          </span>
        </div>
      )}

      {/* Bottom status bar */}
      <div className="flex flex-col items-center gap-1 pb-4 pt-2 safe-area-inset-bottom">
        {/* 1.4: Swipe-up hint arrow */}
        {!settingsOpen && (
          <div className="text-white/20 text-xs animate-bounce mb-1">↑ 上滑设置</div>
        )}
        <div className="flex items-center gap-3">
          <span
            className="px-3 py-1 rounded-full text-xs font-medium transition-colors duration-500"
            style={{
              backgroundColor: `${accent}22`,
              color: accent,
              border: `1px solid ${accent}44`,
            }}
          >
            {eyeMood === "listening"
              ? "聆听中"
              : eyeMood === "thinking"
                ? "思考中"
                : eyeMood === "speaking"
                  ? "播报中"
                  : eyeMood === "error"
                    ? "错误"
                    : "待命"}
          </span>
          <span
            className={classNames(
              "w-2 h-2 rounded-full",
              sseConnected ? "bg-emerald-400" : "bg-red-400"
            )}
            title={sseConnected ? "SSE 已连" : "SSE 断开"}
          />
        </div>
        <span className="text-white/30 text-[10px]">
          {groupTitle || "未连接"} · 点击说话
        </span>
      </div>

      {/* Settings panel (swipe-up) */}
      <div
        className={classNames(
          "absolute bottom-0 left-0 right-0 transition-transform duration-300 ease-out",
          settingsOpen ? "translate-y-0" : "translate-y-full"
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="bg-white/10 backdrop-blur-xl border-t border-white/15 rounded-t-2xl px-6 py-5 safe-area-inset-bottom max-h-[70vh] overflow-y-auto">
          {/* Drag handle */}
          <div className="w-10 h-1 rounded-full bg-white/30 mx-auto mb-5" />

          <div className="flex flex-col gap-4">
            <SettingsToggle
              label="自动聆听"
              active={autoListen}
              disabled={!speechSupported}
              onToggle={() => onSetAutoListen((v) => !v)}
            />
            <SettingsToggle
              label="语音播报"
              active={voiceEnabled}
              onToggle={() => onSetVoiceEnabled(!voiceEnabled)}
            />

            {/* Group info + QR */}
            {groupId && (
              <div className="pt-2 border-t border-white/10">
                <div className="text-white/50 text-xs mb-2">工作组</div>
                <div className="text-white/80 text-sm font-mono break-all mb-3">
                  {groupId}
                </div>
                <div className="flex items-center gap-4">
                  <div className="bg-white rounded-lg p-2 flex-shrink-0">
                    <QRCodeSVG value={shareUrl} size={80} level="M" />
                  </div>
                  <div className="flex flex-col gap-1">
                    <div className="text-white/50 text-xs">共享链接</div>
                    <div className="text-cyan-300/80 text-[10px] font-mono break-all">
                      {shareUrl}
                    </div>
                  </div>
                </div>
              </div>
            )}

            <button
              onClick={() => setSettingsOpen(false)}
              className="mt-2 w-full py-3 rounded-xl bg-white/10 text-white/80 text-sm font-medium active:bg-white/20"
            >
              关闭设置
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SettingsToggle({
  label,
  active,
  disabled,
  onToggle,
}: {
  label: string;
  active: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      disabled={disabled}
      className="flex items-center justify-between w-full py-2 disabled:opacity-40"
    >
      <span className="text-white/80 text-sm">{label}</span>
      <div
        className={classNames(
          "w-11 h-6 rounded-full transition-colors relative",
          active ? "bg-emerald-500" : "bg-white/20"
        )}
      >
        <div
          className={classNames(
            "absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform",
            active ? "translate-x-[22px]" : "translate-x-0.5"
          )}
        />
      </div>
    </button>
  );
}
