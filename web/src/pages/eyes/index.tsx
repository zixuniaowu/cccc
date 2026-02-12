import React, { useCallback, useMemo, useRef, useState, useEffect } from "react";
import * as api from "../../services/api";
import type { GroupMeta } from "../../types";
import { classNames } from "../../utils/classNames";
import type { Mood, LogLine } from "./types";
import { MOOD_COLOR, THINKING_PREFIXES, SCREEN_CAPTURE_NOOP, NEWS_PREFIXES, clamp, IS_MOBILE } from "./constants";
import { EyeCanvas } from "./EyeCanvas";
import { usePointerVector } from "./usePointerVector";
import {
  useBlink,
  useIdleDrift,
  useSaccade,
  useGazeShift,
  useMoodOffset,
} from "./useEyeAnimation";
import { useSpeechRecognition } from "./useSpeechRecognition";
import { useTTS } from "./useTTS";
import { useEyeTracking } from "./useEyeTracking";
import { useSSEMessages } from "./useSSEMessages";
import { MobileCompanionLayout } from "./MobileCompanionLayout";
import { useScreenCapture } from "./useScreenCapture";
import { usePreferences } from "./usePreferences";

// ────────────────────────────────────────────
//  Orchestrator
// ────────────────────────────────────────────

export default function TelepresenceEyes() {
  const stageRef = useRef<HTMLDivElement>(null);

  // ── Core state ──
  const [mood, setMood] = useState<Mood>("idle");
  const [group, setGroup] = useState<GroupMeta | null>(null);
  const [log, setLog] = useState<LogLine[]>([]);
  const [textInput, setTextInput] = useState("");
  const [apiError, setApiError] = useState<string | null>(null);
  const [connectBusy, setConnectBusy] = useState(false);
  const [tiltEnabled, setTiltEnabled] = useState(false);
  const emptyReplyCountRef = useRef(0);
  const [healthWarning, setHealthWarning] = useState(false);

  // ── Persistent preferences ──
  const { prefs, update: updatePrefs } = usePreferences();
  const voiceEnabled = prefs.voiceEnabled;
  const setVoiceEnabled = useCallback(
    (v: boolean) => updatePrefs({ voiceEnabled: v }),
    [updatePrefs]
  );

  // ── Reduced motion preference ──
  const prefersReducedMotion = useMemo(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches,
    []
  );

  // ── Animation hooks ──
  const pointerVec = usePointerVector();
  const blink = useBlink();
  const idleDrift = useIdleDrift();
  const saccade = useSaccade();
  const gazeShift = useGazeShift();

  // ── TTS ──
  const tts = useTTS();

  // ── Eye tracking (camera + face detection) ──
  const tracking = useEyeTracking();

  // ── Push log helper ──
  const pushLog = useCallback((line: LogLine) => {
    setLog((prev) => [...prev.slice(-8), line]);
  }, []);

  // ── Group bootstrap (supports ?group=<gid> for cross-device sharing) ──
  const connectGroup = useCallback(async (): Promise<string | null> => {
    setConnectBusy(true);
    setApiError(null);
    try {
      // Check URL param for shared group
      const urlGroupId = new URLSearchParams(window.location.search).get("group");

      const resp = await api.fetchGroups();
      if (!resp.ok) {
        setApiError(resp.error?.message || "无法连接后端 /api/v1/groups");
        return null;
      }

      // If URL has a specific group, try to use it
      if (urlGroupId) {
        const match = resp.result.groups.find((g) => g.group_id === urlGroupId);
        if (match) {
          setGroup(match);
          updatePrefs({ lastGroupId: match.group_id });
          return match.group_id;
        }
      }

      // Try last-used group from preferences
      if (prefs.lastGroupId) {
        const lastMatch = resp.result.groups.find((g) => g.group_id === prefs.lastGroupId);
        if (lastMatch) {
          setGroup(lastMatch);
          return lastMatch.group_id;
        }
      }

      if (resp.result.groups.length > 0) {
        const g = resp.result.groups[0];
        setGroup(g);
        updatePrefs({ lastGroupId: g.group_id });
        return g.group_id;
      }
      const created = await api.createGroup("Telepresence", "Mobile eyes link");
      if (!created.ok) {
        setApiError(created.error?.message || "无法创建默认工作组");
        return null;
      }
      const newGroup: GroupMeta = {
        group_id: created.result.group_id,
        title: "Telepresence",
      };
      setGroup(newGroup);
      updatePrefs({ lastGroupId: newGroup.group_id });
      return newGroup.group_id;
    } catch (e: any) {
      setApiError(e?.message || "网络错误：无法连接后端");
      return null;
    } finally {
      setConnectBusy(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefs.lastGroupId, updatePrefs]);

  useEffect(() => {
    void connectGroup();
  }, [connectGroup]);

  const ensureGroupId = async (): Promise<string | null> => {
    if (group) return group.group_id;
    return connectGroup();
  };

  // ── Send message ──
  const handleSend = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      const groupId = await ensureGroupId();
      if (!groupId) {
        setMood("error");
        pushLog({
          who: "agent",
          text: "未找到工作组，无法发送",
          ts: Date.now(),
        });
        return;
      }
      pushLog({ who: "me", text: trimmed, ts: Date.now() });
      setMood("thinking");

      // Track consecutive send-without-reply for health check
      const sendTs = Date.now();
      setTimeout(() => {
        // If mood is still "thinking" after 60s, count as empty reply
        if (Date.now() - sendTs >= 59000) {
          emptyReplyCountRef.current += 1;
          if (emptyReplyCountRef.current >= 3) {
            setHealthWarning(true);
          }
        }
      }, 60000);

      const resp = await api.sendMessage(groupId, trimmed, []);
      if (!resp.ok) {
        setMood("error");
        pushLog({
          who: "agent",
          text: `发送失败: ${resp.error?.message || "unknown"}`,
          ts: Date.now(),
        });
        return;
      }
      setTextInput("");
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [group, pushLog]
  );

  // ── SSE messages (replaces 3.5s polling) ──
  const onAgentMessage = useCallback(
    (text: string, _eventId: string) => {
      // Detect "thinking" indicator messages
      const isThinking = THINKING_PREFIXES.some((p) => text.startsWith(p));
      if (isThinking) {
        setMood("thinking");
        return; // Don't log or TTS thinking indicators
      }

      // Filter out "nothing interesting" replies from screen capture
      if (text.includes(SCREEN_CAPTURE_NOOP)) {
        return; // Silent discard
      }

      // Detect news briefing — always TTS regardless of voiceEnabled
      const isNews = NEWS_PREFIXES.some((p) => text.startsWith(p));

      pushLog({ who: "agent", text, ts: Date.now() });
      window.dispatchEvent(
        new CustomEvent("cccc:agent-reply", { detail: text })
      );

      // Reset empty-reply health counter on successful agent message
      emptyReplyCountRef.current = 0;
      setHealthWarning(false);

      if (voiceEnabled || isNews) {
        setMood("speaking");
        tts.speak(text, () => {
          setMood("idle");
        });
      } else {
        setMood("idle");
      }
    },
    [voiceEnabled, tts, pushLog]
  );

  const { connected: sseConnected } = useSSEMessages({
    groupId: group?.group_id ?? null,
    onAgentMessage,
  });

  // ── Speech recognition ──
  const speech = useSpeechRecognition({
    onResult: handleSend,
    paused: tts.speaking,
  });

  // ── Screen capture (desktop only) ──
  const [screenSettingsOpen, setScreenSettingsOpen] = useState(false);
  const screenCapture = useScreenCapture({
    groupId: group?.group_id ?? null,
    intervalSec: prefs.screenInterval,
    prompt: prefs.screenPrompt,
  });

  // ── News agent status ──
  const [newsRunning, setNewsRunning] = useState(false);
  const [newsBusy, setNewsBusy] = useState(false);

  // Poll news agent status when group is connected
  useEffect(() => {
    if (!group?.group_id) return;
    const check = async () => {
      const resp = await api.fetchNewsStatus(group.group_id);
      if (resp.ok) setNewsRunning(resp.result.running);
    };
    void check();
    const timer = setInterval(() => void check(), 15000);
    return () => clearInterval(timer);
  }, [group?.group_id]);

  const toggleNewsAgent = useCallback(async () => {
    if (!group?.group_id || newsBusy) return;
    setNewsBusy(true);
    try {
      if (newsRunning) {
        await api.stopNewsAgent(group.group_id);
        setNewsRunning(false);
      } else {
        const resp = await api.startNewsAgent(group.group_id);
        if (resp.ok) setNewsRunning(true);
      }
    } finally {
      setNewsBusy(false);
    }
  }, [group?.group_id, newsRunning, newsBusy]);

  // ── Tilt permission ──
  const requestTiltPermission = async () => {
    try {
      if (
        typeof (DeviceOrientationEvent as any)?.requestPermission === "function"
      ) {
        const res = await (DeviceOrientationEvent as any).requestPermission();
        if (res === "granted") {
          setTiltEnabled(true);
          return;
        }
      } else {
        setTiltEnabled(true);
      }
    } catch {
      setTiltEnabled(false);
    }
  };

  // ── Compute eye mood + pupil offset ──
  const eyeMood: Mood = speech.listening ? "listening" : mood;
  const moodOffset = useMoodOffset(eyeMood);
  const accent = useMemo(() => MOOD_COLOR[eyeMood], [eyeMood]);

  const combinedOffset = useMemo(() => {
    // With reduced motion, skip idle drift, saccades, and gaze shifts
    const rm = prefersReducedMotion;
    return {
      x: clamp(
        tracking.camVec.x * 0.45 +
          pointerVec.x * 0.15 +
          (rm ? 0 : idleDrift.x + saccade.x + gazeShift.x) +
          moodOffset.x,
        -1,
        1
      ),
      y: clamp(
        tracking.camVec.y * 0.45 +
          pointerVec.y * 0.15 +
          (rm ? 0 : idleDrift.y + saccade.y + gazeShift.y) +
          moodOffset.y,
        -1,
        1
      ),
    };
  }, [pointerVec, tracking.camVec, idleDrift, saccade, gazeShift, moodOffset, prefersReducedMotion]);

  // ── Keyboard shortcut: Space = toggle listening ──
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        e.code === "Space" &&
        !e.repeat &&
        document.activeElement?.tagName !== "TEXTAREA" &&
        document.activeElement?.tagName !== "INPUT"
      ) {
        e.preventDefault();
        speech.toggle();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [speech]);

  // ────────────────────────────────────────
  //  Render
  // ────────────────────────────────────────

  // Mobile companion mode
  if (IS_MOBILE) {
    return (
      <MobileCompanionLayout
        mood={mood}
        blink={blink}
        pupilOffset={combinedOffset}
        ambient={tracking.ambient}
        listening={speech.listening}
        sseConnected={sseConnected}
        groupId={group?.group_id ?? null}
        groupTitle={group?.title || group?.group_id || ""}
        onToggleListening={speech.toggle}
        voiceEnabled={voiceEnabled}
        onSetVoiceEnabled={setVoiceEnabled}
        autoListen={speech.autoListen}
        onSetAutoListen={speech.setAutoListen}
        speechSupported={speech.supported}
      />
    );
  }

  return (
    <div
      ref={stageRef}
      className="min-h-screen eyes-stage text-white"
      style={{ "--eye-accent": accent } as React.CSSProperties}
    >
      <div className="max-w-4xl mx-auto px-4 py-6 flex flex-col gap-6">
        {/* Header */}
        <header className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xl font-semibold tracking-tight">
              Telepresence Eyes
            </div>
            <div className="flex items-center gap-2 text-sm flex-wrap">
              <span className="px-2 py-1 rounded-full bg-white/10 border border-white/10">
                {group
                  ? `连接工作组: ${group.title || group.group_id}`
                  : "正在寻找工作组..."}
              </span>
              <span
                className={classNames(
                  "px-2 py-1 rounded-full border text-xs",
                  sseConnected
                    ? "bg-emerald-500/20 border-emerald-400/50 text-emerald-100"
                    : "bg-white/5 border-white/20"
                )}
              >
                {sseConnected ? "SSE 已连" : "SSE 未连"}
              </span>
              <span
                className={classNames(
                  "px-2 py-1 rounded-full border text-xs",
                  tracking.cameraReady
                    ? "bg-emerald-500/20 border-emerald-400/50 text-emerald-100"
                    : "bg-white/5 border-white/20"
                )}
              >
                {tracking.cameraReady
                  ? "Camera ON"
                  : tracking.cameraError
                    ? "Camera blocked"
                    : "Camera..."}
              </span>
              <span
                className={classNames(
                  "px-2 py-1 rounded-full border text-xs",
                  tracking.camFollow
                    ? "bg-cyan-500/20 border-cyan-400/50 text-cyan-50"
                    : "bg-white/5 border-white/20 text-white/70"
                )}
              >
                {tracking.camFollow ? "摄像跟随" : "摄像未跟随"}
              </span>
              {apiError && (
                <span className="px-2 py-1 rounded-full bg-red-500/15 border border-red-400/50 text-red-100 text-xs">
                  后端未连: {apiError}
                </span>
              )}
              {healthWarning && (
                <span className="px-2 py-1 rounded-full bg-amber-500/15 border border-amber-400/50 text-amber-100 text-xs animate-pulse">
                  Agent 无响应 (连续 3 次超时)
                </span>
              )}
              <button
                onClick={() => void connectGroup()}
                disabled={connectBusy}
                className="px-2 py-1 rounded-lg border border-white/15 bg-white/5 text-white/80 text-xs hover:bg-white/10 disabled:opacity-40"
              >
                {connectBusy ? "重试中..." : "重试连接"}
              </button>
            </div>
          </div>
          <p className="text-sm text-white/70 leading-relaxed">
            两只大眼睛会跟随你的触控/姿态，前置摄像头捕捉环境光，语音可直接向
            Agent 提问。SSE 实时推送回复。按 Space 切换聆听。
          </p>
        </header>

        {/* Eyes */}
        <section className="flex flex-col items-center gap-4">
          <div className="eyes-pair" aria-label="Animated eyes">
            <EyeCanvas
              mood={eyeMood}
              blink={blink}
              pupilOffset={combinedOffset}
              ambient={tracking.ambient}
            />
            <EyeCanvas
              mood={eyeMood}
              blink={blink}
              pupilOffset={{
                x: combinedOffset.x * 0.9,
                y: combinedOffset.y,
              }}
              ambient={tracking.ambient}
            />
          </div>

          {/* Controls */}
          <div className="flex items-center gap-3 flex-wrap justify-center">
            <button
              onClick={speech.toggle}
              className={classNames(
                "px-4 py-2 rounded-xl border font-medium transition-all",
                speech.listening
                  ? "bg-emerald-500/20 border-emerald-400/60 text-emerald-50 shadow-[0_0_0_3px_rgba(16,185,129,0.15)]"
                  : "bg-white/5 border-white/20 text-white/90 hover:bg-white/10"
              )}
              disabled={!speech.supported}
            >
              {speech.autoListen
                ? "自动聆听中"
                : speech.listening
                  ? "正在聆听…"
                  : speech.supported
                    ? "语音提问"
                    : "浏览器不支持语音"}
            </button>
            <button
              onClick={() => speech.setAutoListen((v) => !v)}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                speech.autoListen
                  ? "bg-emerald-500/20 border-emerald-400/60"
                  : "bg-white/5 border-white/15"
              )}
              disabled={!speech.supported}
            >
              {speech.autoListen ? "自动聆听已开" : "开启自动聆听"}
            </button>
            <button
              onClick={() => setVoiceEnabled(!voiceEnabled)}
              className="px-3 py-2 rounded-xl bg-white/5 border border-white/15 text-white/80 hover:bg-white/10 transition"
            >
              {voiceEnabled ? "静音回复" : "开启播报"}
            </button>
            <button
              onClick={() => setMood("idle")}
              className="px-3 py-2 rounded-xl bg-white/5 border border-white/15 text-white/80 hover:bg-white/10 transition"
            >
              重置表情
            </button>
            <button
              onClick={() => void requestTiltPermission()}
              className="px-3 py-2 rounded-xl bg-white/5 border border-white/15 text-white/80 hover:bg-white/10 transition"
            >
              {tiltEnabled ? "体感已开" : "启用体感跟随"}
            </button>
            <button
              onClick={() => tracking.setCamFollow((v) => !v)}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                tracking.camFollow
                  ? "bg-cyan-500/20 border-cyan-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {tracking.camFollow ? "关闭摄像跟随" : "启用摄像跟随"}
            </button>
            <button
              onClick={() =>
                tracking.setCameraStarted(!tracking.cameraStarted)
              }
              className="px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition bg-amber-500/20 border-amber-400/60"
            >
              {tracking.cameraStarted ? "关闭摄像头" : "开启摄像头"}
            </button>
            <button
              onClick={() => tracking.setMeshEnabled((v) => !v)}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                tracking.meshEnabled
                  ? "bg-emerald-500/20 border-emerald-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {tracking.meshEnabled
                ? "关闭网格"
                : tracking.meshStatus === "loading"
                  ? "网格加载中..."
                  : "开启网格"}
            </button>
            <button
              onClick={() => tracking.setMirrorEnabled((v) => !v)}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                tracking.mirrorEnabled
                  ? "bg-indigo-500/20 border-indigo-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {tracking.mirrorEnabled ? "镜像视图" : "正常视图"}
            </button>
            <button
              onClick={() =>
                screenCapture.capturing
                  ? screenCapture.stop()
                  : void screenCapture.start()
              }
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                screenCapture.capturing
                  ? "bg-rose-500/20 border-rose-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {screenCapture.capturing ? "停止观察桌面" : "观察桌面"}
            </button>
            <button
              onClick={() => void toggleNewsAgent()}
              disabled={newsBusy}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition disabled:opacity-40",
                newsRunning
                  ? "bg-amber-500/20 border-amber-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {newsBusy ? "处理中..." : newsRunning ? "停止新闻播报" : "开启新闻播报"}
            </button>
          </div>
          {screenCapture.error && (
            <div className="text-xs text-red-400 text-center mt-2">
              {screenCapture.error}
            </div>
          )}
          {/* Screen capture settings */}
          <div className="flex items-center justify-center mt-2">
            <button
              onClick={() => setScreenSettingsOpen((v) => !v)}
              className="text-[10px] text-white/40 hover:text-white/70 transition"
            >
              {screenSettingsOpen ? "隐藏截屏设置" : "截屏设置"}
            </button>
          </div>
          {screenSettingsOpen && (
            <div className="mt-2 p-3 rounded-xl bg-black/30 border border-white/10 flex flex-col gap-2">
              <label className="flex items-center justify-between gap-3">
                <span className="text-xs text-white/70">截取间隔 (秒)</span>
                <input
                  type="number"
                  min={10}
                  max={300}
                  value={prefs.screenInterval}
                  onChange={(e) =>
                    updatePrefs({ screenInterval: Math.max(10, Math.min(300, Number(e.target.value) || 30)) })
                  }
                  className="w-20 px-2 py-1 rounded-lg bg-black/50 border border-white/15 text-white/90 text-xs text-right"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-white/70">AI 分析提示词</span>
                <textarea
                  value={prefs.screenPrompt}
                  onChange={(e) => updatePrefs({ screenPrompt: e.target.value })}
                  rows={3}
                  className="w-full px-2 py-1 rounded-lg bg-black/50 border border-white/15 text-white/80 text-xs resize-none"
                />
              </label>
            </div>
          )}
        </section>

        {/* Camera preview */}
        <section className="bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-white/90">
              摄像头预览 / 网格
              <span
                className={classNames(
                  "px-2 py-0.5 rounded-full text-[11px] border",
                  tracking.camFollow
                    ? "bg-cyan-500/20 border-cyan-400/50 text-cyan-50"
                    : "bg-white/5 border-white/20 text-white/70"
                )}
              >
                {tracking.camFollow ? "跟随中" : "未跟随"}
              </span>
              <span
                className={classNames(
                  "px-2 py-0.5 rounded-full text-[11px] border",
                  tracking.meshStatus === "ready"
                    ? "bg-emerald-500/20 border-emerald-400/60 text-emerald-50"
                    : tracking.meshStatus === "loading"
                      ? "bg-amber-500/20 border-amber-400/60 text-amber-50"
                      : tracking.meshStatus === "error"
                        ? "bg-red-500/20 border-red-400/60 text-red-50"
                        : "bg-white/5 border-white/20 text-white/70"
                )}
              >
                {tracking.meshStatus === "ready"
                  ? "Face mesh 就绪"
                  : tracking.meshStatus === "loading"
                    ? "Face mesh 加载中"
                    : "Face mesh 未启用"}
              </span>
            </div>
            <div className="text-xs text-white/60">
              若未显示视频，请确认摄像头权限。
            </div>
          </div>
          <div className="camera-frame">
            <video
              ref={tracking.videoRef as React.RefObject<HTMLVideoElement>}
              className="camera-video"
              muted
              playsInline
              autoPlay
            />
            <canvas ref={tracking.overlayRef as React.RefObject<HTMLCanvasElement>} className="camera-overlay" />
          </div>
        </section>

        {/* Text input */}
        <section className="bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="flex items-center gap-2 text-sm font-semibold text-white/90 mb-3">
            文字/语音 转发到 Agent
          </div>
          <div className="flex flex-col gap-3">
            <textarea
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend(textInput);
                }
              }}
              placeholder="直接输入或用语音提问… (Enter 发送, Shift+Enter 换行, Space 切换聆听)"
              className="w-full min-h-[88px] rounded-xl bg-black/30 border border-white/10 px-3 py-2 text-white/90 placeholder:text-white/40 focus:outline-none focus:ring-2 focus:ring-cyan-400/60"
            />
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs text-white/60">
                SSE 实时推送回复，请确保电脑端 Agent 已在工作组里运行。
              </div>
              <button
                onClick={() => handleSend(textInput)}
                className="px-4 py-2 rounded-xl bg-cyan-500 text-black font-semibold hover:bg-cyan-400 transition"
              >
                发送
              </button>
            </div>
          </div>
        </section>

        {/* Chat log */}
        <section className="bg-black/30 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="flex items-center gap-2 text-sm font-semibold text-white/90 mb-3">
            实时对话记录
            <span className="text-[10px] px-2 py-1 rounded-full bg-white/10 text-white/70">
              最近 10 条
            </span>
          </div>
          <div className="flex flex-col gap-2 max-h-56 overflow-auto pr-1">
            {log.length === 0 && (
              <div className="text-white/50 text-sm">等待第一条消息…</div>
            )}
            {log.map((line) => (
              <div
                key={line.ts + line.text}
                className={classNames(
                  "rounded-xl px-3 py-2 text-sm",
                  line.who === "me"
                    ? "bg-cyan-500/15 text-cyan-100 self-end"
                    : "bg-white/8 text-white/90 self-start"
                )}
              >
                <span className="text-xs uppercase tracking-wide opacity-60 mr-2">
                  {line.who === "me" ? "ME" : "AGENT"}
                </span>
                {line.text}
              </div>
            ))}
          </div>
        </section>

        {/* External device hook info */}
        <section className="bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="text-sm font-semibold text-white/90 mb-2">
            外设 / 机器人臂预留接口
          </div>
          <p className="text-sm text-white/70 leading-relaxed">
            本页面已暴露一个极简事件流：每当收到 Agent 回复时，可在浏览器控制台监听{" "}
            <code>
              {
                'window.dispatchEvent(new CustomEvent("cccc:agent-reply", { detail: "<text>" }))'
              }
            </code>
            。
          </p>
        </section>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────
//  Error boundary
// ────────────────────────────────────────────

export class TelepresenceEyesBoundary extends React.Component<
  { children?: React.ReactNode },
  { hasError: boolean; msg?: string }
> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, msg: "" };
  }
  static getDerivedStateFromError(error: any) {
    return { hasError: true, msg: String(error?.message || error) };
  }
  componentDidCatch(error: any, info: any) {
    console.error("TelepresenceEyes error", error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-6 text-center">
          <div className="text-xl font-semibold mb-2">页面出错了</div>
          <div className="text-sm text-white/70 mb-4">
            {this.state.msg || "Unknown error"}
          </div>
          <div className="text-xs text-white/60">
            请刷新再试，或在桌面浏览器打开查看控制台日志。
          </div>
        </div>
      );
    }
    return <>{this.props.children}</>;
  }
}
