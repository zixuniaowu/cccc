import React, { useCallback, useMemo, useRef, useState, useEffect } from "react";
import { QRCodeSVG } from "qrcode.react";
import * as api from "../../services/api";
import type { GroupMeta } from "../../types";
import { classNames } from "../../utils/classNames";
import type { Mood, LogLine } from "./types";
import { MOOD_COLOR, THINKING_PREFIXES, SCREEN_CAPTURE_NOOP, NEWS_PREFIXES, clamp, IS_MOBILE, IS_LOCALHOST, buildShareUrl, getLanIp, setLanIp, RIGHT_EYE_PARALLAX } from "./constants";
import { fetchLanIp } from "../../services/api";
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
import { usePreferences, type TTSEngine } from "./usePreferences";
import { useDeviceTilt } from "./useDeviceTilt";

// ────────────────────────────────────────────
//  Orchestrator
// ────────────────────────────────────────────

type BroadcastMode = "news" | "market" | "horror";
type ActiveBroadcastMode = BroadcastMode | "ai_long";
type NowPlayingMode = ActiveBroadcastMode | "ai_long_preload";
const VOICE_COMMAND_COOLDOWN_MS = 1200;

function isAlreadyRunningError(resp: { ok: boolean; error?: { code?: string; message?: string } | null }): boolean {
  return !resp.ok && String(resp.error?.code || "") === "already_running";
}

function detectBroadcastModeFromText(text: string): ActiveBroadcastMode | null {
  const t = String(text || "").trim();
  if (t.startsWith("[新闻简报]") || t.startsWith("[早间简报]")) return "news";
  if (t.startsWith("[股市简报]")) return "market";
  if (t.startsWith("[AI新技术说明]") || t.startsWith("[AI长文说明]")) return "ai_long";
  if (t.startsWith("[恐怖故事]")) return "horror";
  return null;
}

function modeLabel(mode: NowPlayingMode | null): string {
  if (mode === "news") return "新闻简报";
  if (mode === "market") return "股市简报";
  if (mode === "horror") return "恐怖故事";
  if (mode === "ai_long") return "AI长文";
  if (mode === "ai_long_preload") return "AI长文预加载";
  return "待命";
}

function normalizeVoiceCommandText(text: string): string {
  return String(text || "")
    .toLowerCase()
    .replace(/[，。！？、；：,.!?:;"'`~(){}<>【】（）\s]+/g, "")
    .replace(/\[/g, "")
    .replace(/\]/g, "")
    .trim();
}

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
  const [lastAgentReply, setLastAgentReply] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);
  const skipAutoScrollRef = useRef(false);
  const ttsProviderWarnedRef = useRef(false);
  const setAutoListenRef = useRef<((next: boolean | ((v: boolean) => boolean)) => void) | null>(null);
  const [connectTimeoutReached, setConnectTimeoutReached] = useState(false);

  // ── Persistent preferences ──
  const { prefs, update: updatePrefs } = usePreferences();
  const voiceEnabled = prefs.voiceEnabled;
  const showCameraPreview = prefs.showCameraPreview;
  const ttsEngine = prefs.ttsEngine;
  const ttsRateMultiplier = Math.max(
    0.82,
    Math.min(1.28, Number(prefs.ttsRateMultiplier || 1))
  );
  const setVoiceEnabled = useCallback(
    (v: boolean) => updatePrefs({ voiceEnabled: v }),
    [updatePrefs]
  );
  const setShowCameraPreview = useCallback(
    (v: boolean) => updatePrefs({ showCameraPreview: v }),
    [updatePrefs]
  );
  const setTTSEngine = useCallback(
    (v: TTSEngine) => updatePrefs({ ttsEngine: v }),
    [updatePrefs]
  );
  const setTTSRateMultiplier = useCallback(
    (v: number) =>
      updatePrefs({
        ttsRateMultiplier: Math.max(0.82, Math.min(1.28, Number(v) || 1)),
      }),
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
  const tts = useTTS("zh-CN", ttsEngine, ttsRateMultiplier);

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

  // 1.5: Connection timeout — show error if no group after 10s
  useEffect(() => {
    if (group) { setConnectTimeoutReached(false); return; }
    const timer = setTimeout(() => {
      if (!group) setConnectTimeoutReached(true);
    }, 10000);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group]);

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

  // ── TTS queue — prevents rapid messages from canceling each other ──
  const ttsQueueRef = useRef<string[]>([]);
  const ttsPlayingRef = useRef(false);
  const broadcastMuteUntilRef = useRef(0);
  const broadcastAutoplayArmedRef = useRef(false);
  const expectedBroadcastModeRef = useRef<ActiveBroadcastMode | null>(null);
  const lastBroadcastModeRef = useRef<ActiveBroadcastMode | null>(null);
  const broadcastArmAtRef = useRef(0);
  const lastBroadcastAtRef = useRef(0);
  const lastVoiceCommandRef = useRef<{ key: string; ts: number }>({
    key: "",
    ts: 0,
  });
  const aiLongPreloadAudioRef = useRef<HTMLAudioElement | null>(null);
  const aiLongPreloadAbortRef = useRef<AbortController | null>(null);
  const aiLongPreloadStopRef = useRef(false);
  const MAX_PENDING_BROADCAST_TTS = 1;

  const playNextInQueue = useCallback(() => {
    if (ttsQueueRef.current.length === 0) {
      ttsPlayingRef.current = false;
      setMood("idle");
      return;
    }
    ttsPlayingRef.current = true;
    setMood("speaking");
    const next = ttsQueueRef.current.shift()!;
    tts.speak(next, () => {
      playNextInQueue();
    });
  }, [tts]);

  const enqueueTTS = useCallback(
    (text: string, opts?: { broadcastLike?: boolean }) => {
      if (opts?.broadcastLike) {
        // Keep broadcast playback near-real-time: drop stale backlog and keep latest.
        if (ttsQueueRef.current.length >= MAX_PENDING_BROADCAST_TTS) {
          ttsQueueRef.current = [text];
        } else {
          ttsQueueRef.current.push(text);
        }
      } else {
        ttsQueueRef.current.push(text);
      }
      if (!ttsPlayingRef.current) {
        playNextInQueue();
      }
    },
    [playNextInQueue]
  );

  const stopSpeechNow = useCallback(() => {
    ttsQueueRef.current = [];
    ttsPlayingRef.current = false;
    aiLongPreloadStopRef.current = true;
    if (aiLongPreloadAbortRef.current) {
      try {
        aiLongPreloadAbortRef.current.abort();
      } catch {}
      aiLongPreloadAbortRef.current = null;
    }
    const preloadAudio = aiLongPreloadAudioRef.current;
    if (preloadAudio) {
      try {
        preloadAudio.pause();
        preloadAudio.src = "";
        preloadAudio.load();
      } catch {}
      aiLongPreloadAudioRef.current = null;
    }
    setAiLongPreloadPlaying(false);
    setAiLongPreloadProgress([0, 0]);
    tts.cancel();
    setMood("idle");
  }, [tts]);

  const muteBroadcastTTS = useCallback((ms = 15000) => {
    broadcastMuteUntilRef.current = Date.now() + Math.max(0, ms);
  }, []);

  const clearBroadcastMuteTTS = useCallback(() => {
    broadcastMuteUntilRef.current = 0;
  }, []);

  // ── SSE messages (replaces 3.5s polling) ──
  const onAgentMessage = useCallback(
    (text: string, _eventId: string) => {
      const now = Date.now();
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

      // Broadcast-tagged chunks are always recognized as broadcast candidates.
      const isBroadcastTagged = NEWS_PREFIXES.some((p) => text.startsWith(p));
      const detectedMode = detectBroadcastModeFromText(text);
      if (detectedMode) {
        lastBroadcastModeRef.current = detectedMode;
      }
      // Prefix-less long chunks may be broadcast continuations.
      const isNarrationChunk = text.length >= 80 && /[。！？!?]/.test(text);
      const isBroadcastContinuation =
        !isBroadcastTagged &&
        isNarrationChunk &&
        now - lastBroadcastAtRef.current < 45000;
      const messageBroadcastMode =
        detectedMode ||
        (isBroadcastContinuation ? lastBroadcastModeRef.current : null);
      const isBroadcastLike = isBroadcastTagged || isBroadcastContinuation;
      if (isBroadcastLike) {
        lastBroadcastAtRef.current = now;
        setNowPlayingText(text);
        setNowPlayingMode((prev) => messageBroadcastMode || prev || null);
        setNowPlayingTs(now);
      }

      // Hard stop window: drop late broadcast chunks after user clicked stop.
      if (now < broadcastMuteUntilRef.current && isBroadcastLike) {
        setMood("idle");
        return;
      }

      // Keep current viewport/focus during periodic news briefings.
      if (isBroadcastLike) {
        skipAutoScrollRef.current = true;
      }

      pushLog({ who: "agent", text, ts: Date.now() });
      if (!isBroadcastLike) {
        setLastAgentReply(text);
      }
      window.dispatchEvent(
        new CustomEvent("cccc:agent-reply", { detail: text })
      );

      // Reset empty-reply health counter on successful agent message
      emptyReplyCountRef.current = 0;
      setHealthWarning(false);

      const expectedMode = expectedBroadcastModeRef.current;
      const modeMatches = Boolean(
        messageBroadcastMode &&
          expectedMode &&
          messageBroadcastMode === expectedMode
      );
      const shouldAutoplayBroadcast =
        isBroadcastLike &&
        broadcastAutoplayArmedRef.current &&
        modeMatches &&
        now >= broadcastArmAtRef.current;
      if (shouldAutoplayBroadcast) {
        enqueueTTS(text, { broadcastLike: true });
      } else if (voiceEnabled && !isBroadcastLike) {
        enqueueTTS(text);
      } else {
        setMood("idle");
      }
    },
    [voiceEnabled, enqueueTTS, pushLog]
  );

  const { connected: sseConnected, mode: sseMode, reconnecting: sseReconnecting } = useSSEMessages({
    groupId: group?.group_id ?? null,
    onAgentMessage,
  });

  const speechHints = useMemo(
    () => [
      "CCCC",
      "新闻简报",
      "股市简报",
      "恐怖故事",
      "AI长文",
      "停止播报",
      "强制停播",
      "开启播报",
      "机器人角色",
      "蛋角色",
      "停止语音",
      "开启自动聆听",
      "关闭自动聆听",
      "静音回复播报",
      "开启回复播报",
      "隐藏摄像画面",
      "显示摄像画面",
      "切换浏览器语音",
      "切换GPT语音",
      "语速快一点",
      "语速慢一点",
      "恢复默认语速",
      "自动聆听",
      "语音提问",
    ],
    []
  );

  // ── Broadcast agents status ──
  const [newsRunning, setNewsRunning] = useState(false);
  const [marketRunning, setMarketRunning] = useState(false);
  const [aiLongRunning, setAiLongRunning] = useState(false);
  const [horrorRunning, setHorrorRunning] = useState(false);
  const [newsBusy, setNewsBusy] = useState(false);
  const [marketBusy, setMarketBusy] = useState(false);
  const [aiLongBusy, setAiLongBusy] = useState(false);
  const [horrorBusy, setHorrorBusy] = useState(false);
  const [stopAllBusy, setStopAllBusy] = useState(false);
  const [selectedBroadcastMode, setSelectedBroadcastMode] =
    useState<BroadcastMode>("news");
  const [aiLongPreloadBusy, setAiLongPreloadBusy] = useState(false);
  const [aiLongPreloadPlaying, setAiLongPreloadPlaying] = useState(false);
  const [aiLongPreloadProgress, setAiLongPreloadProgress] = useState<[number, number]>([0, 0]);
  const [aiLongPreloadState, setAiLongPreloadState] = useState<api.AILongPreloadStatus | null>(null);
  const [aiLongScripts, setAiLongScripts] = useState<api.AILongScript[]>([]);
  const [aiLongSourceMode, setAiLongSourceMode] = useState<"preset" | "topic">("preset");
  const [aiLongScriptKey, setAiLongScriptKey] = useState("cccc_intro_v1");
  const [aiLongTopic, setAiLongTopic] = useState("cccc 框架介绍");
  const [nowPlayingText, setNowPlayingText] = useState("");
  const [nowPlayingMode, setNowPlayingMode] = useState<NowPlayingMode | null>(null);
  const [nowPlayingTs, setNowPlayingTs] = useState<number | null>(null);
  const [gptTtsAvailable, setGptTtsAvailable] = useState<boolean | null>(null);
  const [gptTtsEndpoint, setGptTtsEndpoint] = useState("");
  const [ttsProviderLoading, setTtsProviderLoading] = useState(false);
  const [lastVoiceAction, setLastVoiceAction] = useState("");
  const [lastVoiceActionTs, setLastVoiceActionTs] = useState<number | null>(null);

  // ── LAN IP for QR code (when on localhost) ──
  const [lanIp, setLanIpState] = useState(() => getLanIp());
  const handleLanIpChange = useCallback((ip: string) => {
    setLanIpState(ip);
    setLanIp(ip);
  }, []);

  // Auto-detect LAN IP from backend on mount
  useEffect(() => {
    if (!IS_LOCALHOST) return;
    if (getLanIp()) return; // Already have a saved IP
    fetchLanIp().then((resp) => {
      if (resp.ok && resp.result.lan_ip) {
        handleLanIpChange(resp.result.lan_ip);
      }
    }).catch(() => {});
  }, [handleLanIpChange]);

  useEffect(() => {
    let cancelled = false;
    const loadScripts = async () => {
      try {
        const resp = await api.fetchAiLongScripts();
        if (!resp.ok || cancelled) return;
        const scripts = resp.result.scripts || [];
        setAiLongScripts(scripts);
        if (!scripts.length) return;
        if (!scripts.some((s) => s.key === aiLongScriptKey)) {
          setAiLongScriptKey(scripts[0].key);
        }
      } catch {
        // ignore
      }
    };
    void loadScripts();
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshTTSProviders = useCallback(async () => {
    setTtsProviderLoading(true);
    try {
      const resp = await api.fetchTTSProviders();
      if (!resp.ok) {
        setGptTtsAvailable(null);
        return;
      }
      const providers = resp.result.providers || [];
      const gpt = providers.find((p) => p.engine === "gpt_sovits_v4");
      const available = Boolean(gpt?.available);
      setGptTtsAvailable(available);
      setGptTtsEndpoint(String(gpt?.endpoint || ""));
      if (available) {
        ttsProviderWarnedRef.current = false;
      } else if (ttsEngine === "gpt_sovits_v4" && !ttsProviderWarnedRef.current) {
        ttsProviderWarnedRef.current = true;
        pushLog({
          who: "agent",
          text: "GPT-SoVITS 当前不可达，播报将自动回退浏览器语音",
          ts: Date.now(),
        });
      }
    } catch {
      setGptTtsAvailable(null);
    } finally {
      setTtsProviderLoading(false);
    }
  }, [ttsEngine, pushLog]);

  useEffect(() => {
    void refreshTTSProviders();
    const timer = setInterval(
      () => void refreshTTSProviders(),
      ttsEngine === "gpt_sovits_v4" ? 6000 : 12000
    );
    return () => clearInterval(timer);
  }, [ttsEngine, refreshTTSProviders]);

  const refreshBroadcastStatus = useCallback(async (groupId?: string | null) => {
    const gid = String(groupId || group?.group_id || "").trim();
    if (!gid) return;
    try {
      const [newsResp, marketResp, aiLongResp, horrorResp, preloadResp] =
        await Promise.all([
          api.fetchNewsStatus(gid),
          api.fetchMarketStatus(gid),
          api.fetchAiLongStatus(gid),
          api.fetchHorrorStatus(gid),
          api.fetchAiLongPreloadStatus(gid),
        ]);
      if (newsResp.ok) setNewsRunning(newsResp.result.running);
      if (marketResp.ok) setMarketRunning(marketResp.result.running);
      if (aiLongResp.ok) setAiLongRunning(aiLongResp.result.running);
      if (horrorResp.ok) setHorrorRunning(horrorResp.result.running);
      if (preloadResp.ok) setAiLongPreloadState(preloadResp.result);
    } catch {
      // Ignore transient status polling errors.
    }
  }, [group?.group_id]);

  // Poll broadcast agents status when group is connected
  useEffect(() => {
    if (!group?.group_id) return;
    void refreshBroadcastStatus(group.group_id);
    const timer = setInterval(
      () => void refreshBroadcastStatus(group.group_id),
      12000
    );
    return () => clearInterval(timer);
  }, [group?.group_id, refreshBroadcastStatus]);

  const toggleNewsAgent = useCallback(async () => {
    if (!group?.group_id || newsBusy) return;
    setNewsBusy(true);
    try {
      if (newsRunning) {
        broadcastAutoplayArmedRef.current = false;
        expectedBroadcastModeRef.current = null;
        muteBroadcastTTS();
        stopSpeechNow();
        const resp = await api.stopNewsAgent(group.group_id);
        if (resp.ok) {
          setNewsRunning(false);
        } else {
          pushLog({
            who: "agent",
            text: `停止新闻失败: ${resp.error?.message || "unknown"}`,
            ts: Date.now(),
          });
        }
      } else {
        stopSpeechNow();
        clearBroadcastMuteTTS();
        if (marketRunning) {
          await api.stopMarketAgent(group.group_id);
          setMarketRunning(false);
        }
        if (aiLongRunning) {
          await api.stopAiLongAgent(group.group_id);
          setAiLongRunning(false);
        }
        if (horrorRunning) {
          await api.stopHorrorAgent(group.group_id);
          setHorrorRunning(false);
        }
        const resp = await api.startNewsAgent(group.group_id);
        if (resp.ok || isAlreadyRunningError(resp)) {
          broadcastAutoplayArmedRef.current = true;
          expectedBroadcastModeRef.current = "news";
          broadcastArmAtRef.current = Date.now();
          setNewsRunning(true);
          if (isAlreadyRunningError(resp)) {
            pushLog({
              who: "agent",
              text: "新闻播报已在运行",
              ts: Date.now(),
            });
          }
        } else {
          pushLog({
            who: "agent",
            text: `启动新闻失败: ${resp.error?.message || "unknown"}`,
            ts: Date.now(),
          });
        }
      }
    } catch (e: any) {
      pushLog({
        who: "agent",
        text: `新闻操作失败: ${e?.message || "network error"}`,
        ts: Date.now(),
      });
    } finally {
      setNewsBusy(false);
      void refreshBroadcastStatus(group.group_id);
    }
  }, [group?.group_id, newsRunning, newsBusy, muteBroadcastTTS, stopSpeechNow, clearBroadcastMuteTTS, marketRunning, aiLongRunning, horrorRunning, pushLog, refreshBroadcastStatus]);

  const toggleMarketAgent = useCallback(async () => {
    if (!group?.group_id || marketBusy) return;
    setMarketBusy(true);
    try {
      if (marketRunning) {
        broadcastAutoplayArmedRef.current = false;
        expectedBroadcastModeRef.current = null;
        muteBroadcastTTS();
        stopSpeechNow();
        const resp = await api.stopMarketAgent(group.group_id);
        if (resp.ok) {
          setMarketRunning(false);
        } else {
          pushLog({
            who: "agent",
            text: `停止股市失败: ${resp.error?.message || "unknown"}`,
            ts: Date.now(),
          });
        }
      } else {
        stopSpeechNow();
        clearBroadcastMuteTTS();
        if (newsRunning) {
          await api.stopNewsAgent(group.group_id);
          setNewsRunning(false);
        }
        if (aiLongRunning) {
          await api.stopAiLongAgent(group.group_id);
          setAiLongRunning(false);
        }
        if (horrorRunning) {
          await api.stopHorrorAgent(group.group_id);
          setHorrorRunning(false);
        }
        const resp = await api.startMarketAgent(group.group_id);
        if (resp.ok || isAlreadyRunningError(resp)) {
          broadcastAutoplayArmedRef.current = true;
          expectedBroadcastModeRef.current = "market";
          broadcastArmAtRef.current = Date.now();
          setMarketRunning(true);
          if (isAlreadyRunningError(resp)) {
            pushLog({
              who: "agent",
              text: "股市播报已在运行",
              ts: Date.now(),
            });
          }
        } else {
          pushLog({
            who: "agent",
            text: `启动股市失败: ${resp.error?.message || "unknown"}`,
            ts: Date.now(),
          });
        }
      }
    } catch (e: any) {
      pushLog({
        who: "agent",
        text: `股市操作失败: ${e?.message || "network error"}`,
        ts: Date.now(),
      });
    } finally {
      setMarketBusy(false);
      void refreshBroadcastStatus(group.group_id);
    }
  }, [group?.group_id, marketBusy, marketRunning, muteBroadcastTTS, stopSpeechNow, clearBroadcastMuteTTS, newsRunning, aiLongRunning, horrorRunning, pushLog, refreshBroadcastStatus]);

  const toggleAiLongAgent = useCallback(async () => {
    if (!group?.group_id || aiLongBusy) return;
    setAiLongBusy(true);
    try {
      if (aiLongRunning) {
        broadcastAutoplayArmedRef.current = false;
        expectedBroadcastModeRef.current = null;
        muteBroadcastTTS();
        stopSpeechNow();
        const resp = await api.stopAiLongAgent(group.group_id);
        if (resp.ok) {
          setAiLongRunning(false);
        } else {
          pushLog({
            who: "agent",
            text: `停止AI长文失败: ${resp.error?.message || "unknown"}`,
            ts: Date.now(),
          });
        }
      } else {
        stopSpeechNow();
        clearBroadcastMuteTTS();
        if (newsRunning) {
          await api.stopNewsAgent(group.group_id);
          setNewsRunning(false);
        }
        if (marketRunning) {
          await api.stopMarketAgent(group.group_id);
          setMarketRunning(false);
        }
        if (horrorRunning) {
          await api.stopHorrorAgent(group.group_id);
          setHorrorRunning(false);
        }
        const resp = await api.startAiLongAgent(group.group_id);
        if (resp.ok || isAlreadyRunningError(resp)) {
          broadcastAutoplayArmedRef.current = true;
          expectedBroadcastModeRef.current = "ai_long";
          broadcastArmAtRef.current = Date.now();
          setAiLongRunning(true);
          if (isAlreadyRunningError(resp)) {
            pushLog({
              who: "agent",
              text: "AI长文播报已在运行",
              ts: Date.now(),
            });
          }
        } else {
          pushLog({
            who: "agent",
            text: `启动AI长文失败: ${resp.error?.message || "unknown"}`,
            ts: Date.now(),
          });
        }
      }
    } catch (e: any) {
      pushLog({
        who: "agent",
        text: `AI长文操作失败: ${e?.message || "network error"}`,
        ts: Date.now(),
      });
    } finally {
      setAiLongBusy(false);
      void refreshBroadcastStatus(group.group_id);
    }
  }, [group?.group_id, aiLongBusy, aiLongRunning, muteBroadcastTTS, stopSpeechNow, clearBroadcastMuteTTS, newsRunning, marketRunning, horrorRunning, pushLog, refreshBroadcastStatus]);

  const toggleHorrorAgent = useCallback(async () => {
    if (!group?.group_id || horrorBusy) return;
    setHorrorBusy(true);
    try {
      if (horrorRunning) {
        broadcastAutoplayArmedRef.current = false;
        expectedBroadcastModeRef.current = null;
        muteBroadcastTTS();
        stopSpeechNow();
        const resp = await api.stopHorrorAgent(group.group_id);
        if (resp.ok) {
          setHorrorRunning(false);
        } else {
          pushLog({
            who: "agent",
            text: `停止恐怖故事失败: ${resp.error?.message || "unknown"}`,
            ts: Date.now(),
          });
        }
      } else {
        stopSpeechNow();
        clearBroadcastMuteTTS();
        if (newsRunning) {
          await api.stopNewsAgent(group.group_id);
          setNewsRunning(false);
        }
        if (marketRunning) {
          await api.stopMarketAgent(group.group_id);
          setMarketRunning(false);
        }
        if (aiLongRunning) {
          await api.stopAiLongAgent(group.group_id);
          setAiLongRunning(false);
        }
        const resp = await api.startHorrorAgent(group.group_id);
        if (resp.ok || isAlreadyRunningError(resp)) {
          broadcastAutoplayArmedRef.current = true;
          expectedBroadcastModeRef.current = "horror";
          broadcastArmAtRef.current = Date.now();
          setHorrorRunning(true);
          if (isAlreadyRunningError(resp)) {
            pushLog({
              who: "agent",
              text: "恐怖故事播报已在运行",
              ts: Date.now(),
            });
          }
        } else {
          pushLog({
            who: "agent",
            text: `启动恐怖故事失败: ${resp.error?.message || "unknown"}`,
            ts: Date.now(),
          });
        }
      }
    } catch (e: any) {
      pushLog({
        who: "agent",
        text: `恐怖故事操作失败: ${e?.message || "network error"}`,
        ts: Date.now(),
      });
    } finally {
      setHorrorBusy(false);
      void refreshBroadcastStatus(group.group_id);
    }
  }, [group?.group_id, horrorBusy, horrorRunning, muteBroadcastTTS, stopSpeechNow, clearBroadcastMuteTTS, newsRunning, marketRunning, aiLongRunning, pushLog, refreshBroadcastStatus]);

  const forceStopBroadcast = useCallback(async () => {
    if (stopAllBusy) return;
    setStopAllBusy(true);
    try {
      broadcastAutoplayArmedRef.current = false;
      expectedBroadcastModeRef.current = null;
      broadcastArmAtRef.current = 0;
      muteBroadcastTTS(20000);
      stopSpeechNow();
      const gid = String(group?.group_id || "").trim();
      if (gid) {
        await Promise.allSettled([
          api.stopNewsAgent(gid),
          api.stopMarketAgent(gid),
          api.stopAiLongAgent(gid),
          api.stopHorrorAgent(gid),
        ]);
      }
      setNewsRunning(false);
      setMarketRunning(false);
      setAiLongRunning(false);
      setHorrorRunning(false);
      if (gid) {
        await refreshBroadcastStatus(gid);
        window.setTimeout(() => {
          void refreshBroadcastStatus(gid);
        }, 1200);
      }
    } finally {
      setNewsBusy(false);
      setMarketBusy(false);
      setAiLongBusy(false);
      setHorrorBusy(false);
      setStopAllBusy(false);
    }
  }, [group?.group_id, stopAllBusy, muteBroadcastTTS, stopSpeechNow, refreshBroadcastStatus]);

  const activeBroadcastMode: ActiveBroadcastMode | null = useMemo(() => {
    if (newsRunning) return "news";
    if (marketRunning) return "market";
    if (aiLongRunning) return "ai_long";
    if (horrorRunning) return "horror";
    return null;
  }, [newsRunning, marketRunning, aiLongRunning, horrorRunning]);

  const broadcastBusy = newsBusy || marketBusy || aiLongBusy || horrorBusy || stopAllBusy;
  const hasActivePlayback = Boolean(
    activeBroadcastMode || aiLongPreloadPlaying || tts.speaking
  );

  const preloadAiLongAudio = useCallback(async () => {
    if (!group?.group_id || aiLongPreloadBusy) return;
    const trimmedTopic = aiLongTopic.trim();
    if (aiLongSourceMode === "topic" && !trimmedTopic) {
      pushLog({
        who: "agent",
        text: "请先填写 AI 长文主题",
        ts: Date.now(),
      });
      return;
    }
    const selectedScript = aiLongScripts.find((s) => s.key === aiLongScriptKey);
    const resolvedInterests =
      aiLongSourceMode === "topic"
        ? trimmedTopic
        : selectedScript?.aliases?.join(",") || "CCCC,框架,多Agent,协作,消息总线,语音播报";
    setAiLongPreloadBusy(true);
    try {
      const resp = await api.startAiLongPreload(
        group.group_id,
        resolvedInterests,
        false,
        {
          scriptKey: aiLongSourceMode === "preset" ? aiLongScriptKey : "",
          topic: aiLongSourceMode === "topic" ? trimmedTopic : "",
        }
      );
      if (resp.ok || isAlreadyRunningError(resp)) {
        pushLog({
          who: "agent",
          text: isAlreadyRunningError(resp)
            ? "AI长文后台预加载正在进行"
            : aiLongSourceMode === "preset"
              ? `已开始预加载：${selectedScript?.title || aiLongScriptKey}`
              : `已开始按主题预加载：${trimmedTopic}`,
          ts: Date.now(),
        });
      } else {
        pushLog({
          who: "agent",
          text: `启动AI长文预加载失败: ${resp.error?.message || "unknown"}`,
          ts: Date.now(),
        });
      }
      const statusResp = await api.fetchAiLongPreloadStatus(group.group_id);
      if (statusResp.ok) setAiLongPreloadState(statusResp.result);
    } catch (e: any) {
      pushLog({
        who: "agent",
        text: `AI长文预加载失败: ${e?.message || "network error"}`,
        ts: Date.now(),
      });
    } finally {
      setAiLongPreloadBusy(false);
    }
  }, [
    group?.group_id,
    aiLongPreloadBusy,
    aiLongTopic,
    aiLongSourceMode,
    aiLongScriptKey,
    aiLongScripts,
    pushLog,
  ]);

  const playPreloadedAiLong = useCallback(async () => {
    if (!group?.group_id || aiLongPreloadPlaying) return;
    if (activeBroadcastMode) {
      pushLog({
        who: "agent",
        text: "请先停止当前播报，再播放预加载AI长文",
        ts: Date.now(),
      });
      return;
    }
    stopSpeechNow();
    aiLongPreloadStopRef.current = false;
    setAiLongPreloadPlaying(true);
    setMood("speaking");
    try {
      const manifestResp = await api.fetchAiLongPreloadManifest(group.group_id);
      if (!manifestResp.ok) {
        throw new Error(manifestResp.error?.message || "预加载音频未准备好");
      }
      const chunks = manifestResp.result.chunks || [];
      if (!chunks.length) {
        throw new Error("预加载音频为空");
      }
      setAiLongPreloadProgress([0, chunks.length]);
      for (let i = 0; i < chunks.length; i++) {
        if (aiLongPreloadStopRef.current) break;
        setNowPlayingMode("ai_long_preload");
        setNowPlayingText(String(chunks[i].text || "").trim() || `AI长文片段 ${i + 1}`);
        setNowPlayingTs(Date.now());
        const chunkIndex = chunks[i].index;
        const controller = new AbortController();
        aiLongPreloadAbortRef.current = controller;
        const audioResp = await api.fetchAiLongPreloadChunk(
          group.group_id,
          chunkIndex,
          controller.signal
        );
        if (aiLongPreloadAbortRef.current === controller) {
          aiLongPreloadAbortRef.current = null;
        }
        if (!audioResp.ok) {
          throw new Error(audioResp.error.message || "读取预加载音频失败");
        }
        if (aiLongPreloadStopRef.current) break;
        setAiLongPreloadProgress([i + 1, chunks.length]);
        const url = URL.createObjectURL(audioResp.blob);
        try {
          await new Promise<void>((resolve, reject) => {
            const audio = new Audio(url);
            aiLongPreloadAudioRef.current = audio;
            let settled = false;
            const done = (err?: Error) => {
              if (settled) return;
              settled = true;
              audio.onended = null;
              audio.onerror = null;
              if (aiLongPreloadAudioRef.current === audio) {
                aiLongPreloadAudioRef.current = null;
              }
              if (err) reject(err);
              else resolve();
            };
            audio.onended = () => done();
            audio.onerror = () => done(new Error(`音频片段播放失败（第 ${i + 1} 段）`));
            const p = audio.play();
            if (p && typeof p.catch === "function") {
              p.catch(() => done(new Error("浏览器阻止自动播放，请先与页面交互")));
            }
          });
        } finally {
          URL.revokeObjectURL(url);
        }
      }
      if (!aiLongPreloadStopRef.current) {
        pushLog({
          who: "agent",
          text: "AI长文预加载音频播放完成",
          ts: Date.now(),
        });
      }
    } catch (e: any) {
      if (!aiLongPreloadStopRef.current) {
        pushLog({
          who: "agent",
          text: `播放预加载AI长文失败: ${e?.message || "unknown"}`,
          ts: Date.now(),
        });
      }
    } finally {
      aiLongPreloadStopRef.current = false;
      setAiLongPreloadPlaying(false);
      setAiLongPreloadProgress([0, 0]);
      setMood("idle");
    }
  }, [group?.group_id, aiLongPreloadPlaying, activeBroadcastMode, stopSpeechNow, pushLog]);

  const aiLongPreloadRunning = Boolean(aiLongPreloadState?.running);
  const aiLongPreloadReady = Boolean(aiLongPreloadState?.manifest_ready);
  const speakingProgress = aiLongPreloadPlaying ? aiLongPreloadProgress : tts.ttsProgress;
  const nowPlayingDisplayMode: NowPlayingMode | null = useMemo(() => {
    if (aiLongPreloadPlaying) return "ai_long_preload";
    if (activeBroadcastMode) return activeBroadcastMode;
    return nowPlayingMode;
  }, [aiLongPreloadPlaying, activeBroadcastMode, nowPlayingMode]);

  const aiLongPreloadSummary = useMemo(() => {
    if (!aiLongPreloadState) return "AI长文预加载：未开始";
    const total = Math.max(0, Number(aiLongPreloadState.total_chunks || 0));
    const done = Math.max(0, Number(aiLongPreloadState.completed_chunks || 0));
    if (aiLongPreloadState.running) {
      return `AI长文预加载中 ${done}/${total || "?"}`;
    }
    if (aiLongPreloadState.status === "ready" && aiLongPreloadState.manifest_ready) {
      return `AI长文已就绪 ${done}/${total}`;
    }
    if (aiLongPreloadState.status === "error") {
      return `AI长文预加载失败: ${aiLongPreloadState.error || aiLongPreloadState.message || "unknown"}`;
    }
    return aiLongPreloadState.message || "AI长文预加载：待命";
  }, [aiLongPreloadState]);
  const canTriggerAiLongPreload = Boolean(group?.group_id)
    && !aiLongPreloadBusy
    && !aiLongPreloadRunning
    && (aiLongSourceMode === "preset" ? Boolean(aiLongScriptKey) : Boolean(aiLongTopic.trim()));

  const toggleSelectedBroadcast = useCallback(async () => {
    if (broadcastBusy) return;
    if (activeBroadcastMode === "news") {
      await toggleNewsAgent();
      return;
    }
    if (activeBroadcastMode === "market") {
      await toggleMarketAgent();
      return;
    }
    if (activeBroadcastMode === "ai_long") {
      await toggleAiLongAgent();
      return;
    }
    if (activeBroadcastMode === "horror") {
      await toggleHorrorAgent();
      return;
    }
    if (selectedBroadcastMode === "news") {
      await toggleNewsAgent();
      return;
    }
    if (selectedBroadcastMode === "market") {
      await toggleMarketAgent();
      return;
    }
    await toggleHorrorAgent();
  }, [
    activeBroadcastMode,
    broadcastBusy,
    selectedBroadcastMode,
    toggleNewsAgent,
    toggleMarketAgent,
    toggleAiLongAgent,
    toggleHorrorAgent,
  ]);

  const runLocalVoiceCommand = useCallback(
    async (rawText: string): Promise<boolean> => {
      const compact = normalizeVoiceCommandText(rawText);
      if (!compact) return false;

      const runCommand = async (
        key: string,
        fn: () => Promise<void> | void,
        ack: string
      ): Promise<boolean> => {
        const now = Date.now();
        if (
          lastVoiceCommandRef.current.key === key &&
          now - lastVoiceCommandRef.current.ts < VOICE_COMMAND_COOLDOWN_MS
        ) {
          return true;
        }
        lastVoiceCommandRef.current = { key, ts: now };
        await fn();
        setLastVoiceAction(ack);
        setLastVoiceActionTs(Date.now());
        pushLog({ who: "agent", text: `语音指令已执行：${ack}`, ts: Date.now() });
        return true;
      };

      if (
        /(强制停播|停止当前播报|停止播报|停止播放|停止语音|停止朗读|停播|先停一下|先停播)/.test(
          compact
        )
      ) {
        return runCommand("stop-broadcast", async () => {
          await forceStopBroadcast();
        }, "强制停播");
      }

      if (/(语速快一点|加快语速|快一点|说快点)/.test(compact)) {
        const next = Math.max(
          0.82,
          Math.min(1.28, Number((ttsRateMultiplier + 0.08).toFixed(2)))
        );
        return runCommand("tts-rate-up", () => {
          setTTSRateMultiplier(next);
        }, `语速 ${next}x`);
      }

      if (/(语速慢一点|放慢语速|慢一点|说慢点)/.test(compact)) {
        const next = Math.max(
          0.82,
          Math.min(1.28, Number((ttsRateMultiplier - 0.08).toFixed(2)))
        );
        return runCommand("tts-rate-down", () => {
          setTTSRateMultiplier(next);
        }, `语速 ${next}x`);
      }

      if (/(恢复默认语速|语速默认|默认语速)/.test(compact)) {
        return runCommand("tts-rate-default", () => {
          setTTSRateMultiplier(1);
        }, "语速 1.00x");
      }

      const startIntent =
        /(开启|开始|播放|播报|开播|切到|切换到|来点|来个|我要听|听一下)/.test(
          compact
        );
      const commandLike = compact.length <= 28;
      const hasNews =
        /新闻简报|新闻播报|播报新闻|新闻模式/.test(compact);
      const hasMarket =
        /股市简报|股票简报|股市播报|股票播报|财经简报/.test(compact);
      const hasHorror =
        /恐怖故事|鬼故事|惊悚故事|悬疑故事|夜间故事/.test(compact);

      if (hasNews && startIntent) {
        return runCommand("start-news", async () => {
          setSelectedBroadcastMode("news");
          if (activeBroadcastMode && activeBroadcastMode !== "news") {
            await forceStopBroadcast();
          }
          if (!newsRunning) {
            await toggleNewsAgent();
          }
        }, "新闻简报");
      }

      if (hasMarket && startIntent) {
        return runCommand("start-market", async () => {
          setSelectedBroadcastMode("market");
          if (activeBroadcastMode && activeBroadcastMode !== "market") {
            await forceStopBroadcast();
          }
          if (!marketRunning) {
            await toggleMarketAgent();
          }
        }, "股市简报");
      }

      if (hasHorror && startIntent) {
        return runCommand("start-horror", async () => {
          setSelectedBroadcastMode("horror");
          if (activeBroadcastMode && activeBroadcastMode !== "horror") {
            await forceStopBroadcast();
          }
          if (!horrorRunning) {
            await toggleHorrorAgent();
          }
        }, "恐怖故事");
      }

      if (
        /(开启选中播报|开始选中播报|开启播报|开始播报|继续播报|恢复播报)/.test(
          compact
        )
      ) {
        return runCommand("start-selected", async () => {
          await toggleSelectedBroadcast();
        }, "播报开关");
      }

      if (
        commandLike &&
        /(预加载|预热|后台准备).*(ai长文|长文|长稿)|长文预加载/.test(compact)
      ) {
        return runCommand("preload-ai-long", async () => {
          await preloadAiLongAudio();
        }, "AI长文预加载");
      }

      if (
        commandLike &&
        /(播放|开始|继续).*(预加载|长文|长稿)|播放预加载长文|开始长文播报/.test(
          compact
        )
      ) {
        return runCommand("play-ai-long", async () => {
          await playPreloadedAiLong();
        }, "播放预加载长文");
      }

      if (/(蛋角色|蛋形象|切换蛋|换蛋)/.test(compact)) {
        return runCommand("avatar-egg", async () => {
          tracking.setAvatarEnabled(true);
          tracking.setAvatarStyle("egg");
        }, "切换为蛋角色");
      }

      if (/(机器人|机甲角色|机械角色|切换机器人)/.test(compact)) {
        return runCommand("avatar-robot", async () => {
          tracking.setAvatarEnabled(true);
          tracking.setAvatarStyle("robot");
        }, "切换为机器人");
      }

      if (/(开启自动聆听|打开自动聆听|自动聆听开启)/.test(compact)) {
        return runCommand("auto-listen-on", () => {
          setAutoListenRef.current?.(true);
        }, "开启自动聆听");
      }

      if (/(关闭自动聆听|停止自动聆听|自动聆听关闭)/.test(compact)) {
        return runCommand("auto-listen-off", () => {
          setAutoListenRef.current?.(false);
        }, "关闭自动聆听");
      }

      if (/(静音回复播报|关闭回复播报|关闭语音回复)/.test(compact)) {
        return runCommand("reply-voice-off", () => {
          setVoiceEnabled(false);
        }, "关闭回复播报");
      }

      if (/(开启回复播报|打开回复播报|开启语音回复)/.test(compact)) {
        return runCommand("reply-voice-on", () => {
          setVoiceEnabled(true);
        }, "开启回复播报");
      }

      if (/(隐藏摄像画面|关闭摄像画面|只显示网格)/.test(compact)) {
        return runCommand("camera-preview-hide", () => {
          setShowCameraPreview(false);
        }, "隐藏摄像画面");
      }

      if (/(显示摄像画面|打开摄像画面)/.test(compact)) {
        return runCommand("camera-preview-show", () => {
          setShowCameraPreview(true);
        }, "显示摄像画面");
      }

      if (
        /(切换浏览器语音|使用浏览器语音|切换到浏览器tts|切换浏览器tts)/.test(
          compact
        )
      ) {
        return runCommand("tts-browser", () => {
          setTTSEngine("browser");
        }, "切换到浏览器语音");
      }

      if (
        /(切换gpt语音|切换gptsovits|切换到gpt语音|使用gpt语音)/.test(compact)
      ) {
        return runCommand("tts-gpt", () => {
          if (gptTtsAvailable === false) {
            pushLog({
              who: "agent",
              text: "GPT-SoVITS 当前离线，暂不切换",
              ts: Date.now(),
            });
            return;
          }
          setTTSEngine("gpt_sovits_v4");
        }, "切换到 GPT-SoVITS");
      }

      return false;
    },
    [
      activeBroadcastMode,
      gptTtsAvailable,
      forceStopBroadcast,
      horrorRunning,
      marketRunning,
      newsRunning,
      playPreloadedAiLong,
      preloadAiLongAudio,
      pushLog,
      setShowCameraPreview,
      setTTSRateMultiplier,
      setTTSEngine,
      setVoiceEnabled,
      ttsRateMultiplier,
      toggleHorrorAgent,
      toggleMarketAgent,
      toggleNewsAgent,
      toggleSelectedBroadcast,
      tracking,
    ]
  );

  const handleSpeechResult = useCallback(
    (text: string) => {
      void (async () => {
        const handled = await runLocalVoiceCommand(text);
        if (handled) return;
        await handleSend(text);
      })();
    },
    [runLocalVoiceCommand, handleSend]
  );

  // ── Speech recognition ──
  const speech = useSpeechRecognition({
    onResult: handleSpeechResult,
    paused: tts.speaking,
    lang: "zh-CN",
    hints: speechHints,
  });

  useEffect(() => {
    setAutoListenRef.current = speech.setAutoListen;
  }, [speech.setAutoListen]);

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

  // ── Device tilt (gyroscope) ──
  const tiltVec = useDeviceTilt(tiltEnabled);

  // ── Compute eye mood + pupil offset ──
  const eyeMood: Mood = speech.listening ? "listening" : mood;
  const moodOffset = useMoodOffset(eyeMood);
  const accent = useMemo(() => MOOD_COLOR[eyeMood], [eyeMood]);

  const combinedOffset = useMemo(() => {
    // With reduced motion, skip idle drift, saccades, and gaze shifts
    const rm = prefersReducedMotion;
    return {
      x: clamp(
        tracking.camVec.x * 0.7 +
          pointerVec.x * 0.15 +
          tiltVec.x * 0.5 +
          (rm ? 0 : idleDrift.x + saccade.x + gazeShift.x) +
          moodOffset.x,
        -1,
        1
      ),
      y: clamp(
        tracking.camVec.y * 0.7 +
          pointerVec.y * 0.15 +
          tiltVec.y * 0.5 +
          (rm ? 0 : idleDrift.y + saccade.y + gazeShift.y) +
          moodOffset.y,
        -1,
        1
      ),
    };
  }, [pointerVec, tracking.camVec, tiltVec, idleDrift, saccade, gazeShift, moodOffset, prefersReducedMotion]);

  // ── Auto-scroll chat log on new messages ──
  useEffect(() => {
    if (skipAutoScrollRef.current) {
      skipAutoScrollRef.current = false;
      return;
    }
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

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
        onRequestTilt={requestTiltPermission}
        interimText={speech.interimText}
        lastAgentReply={lastAgentReply}
      />
    );
  }

  return (
    <div
      ref={stageRef}
      className="min-h-screen eyes-stage text-white relative"
      style={{ "--eye-accent": accent } as React.CSSProperties}
    >
      {/* Mood ambient overlay — smooth color transition */}
      <div
        className="absolute inset-0 pointer-events-none transition-[background-color] duration-700"
        style={{ backgroundColor: `${accent}0a` }}
      />
      <div className="relative max-w-4xl mx-auto px-4 py-4 md:py-5 flex flex-col gap-4">
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
                  : connectTimeoutReached
                    ? "无法连接后端"
                    : "正在寻找工作组..."}
              </span>
              <span
                className={classNames(
                  "px-2 py-1 rounded-full border text-xs",
                  sseConnected
                    ? "bg-emerald-500/20 border-emerald-400/50 text-emerald-100"
                    : sseReconnecting
                      ? "bg-amber-500/20 border-amber-400/50 text-amber-100 animate-pulse"
                      : "bg-white/5 border-white/20"
                )}
              >
                {sseConnected
                  ? sseMode === "polling" ? "轮询中" : "SSE 已连"
                  : sseReconnecting ? "重连中…" : "SSE 未连"}
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
          {/* Mood status pill */}
          <div
            className="px-3 py-1 rounded-full text-xs font-medium transition-colors duration-500"
            style={{
              backgroundColor: `${accent}22`,
              color: accent,
              border: `1px solid ${accent}44`,
            }}
          >
            {eyeMood === "listening"
              ? "聆听中…"
              : eyeMood === "thinking"
                ? "思考中…"
                : eyeMood === "speaking"
                  ? `播报中${speakingProgress[1] > 1 ? ` (${speakingProgress[0]}/${speakingProgress[1]})` : "…"}`
                  : eyeMood === "error"
                    ? "错误"
                    : "待命"}
          </div>
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
                x: combinedOffset.x * RIGHT_EYE_PARALLAX,
                y: combinedOffset.y,
              }}
              ambient={tracking.ambient}
            />
          </div>

          {/* Camera + Mesh preview */}
          <section className="w-full bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
            <div className="flex items-center justify-between gap-3 mb-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-white/90 flex-wrap">
                {showCameraPreview ? "摄像头 / 识别网格" : "识别网格（摄像画面已隐藏）"}
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
                <DetectorStatusPill label="Face" status={tracking.meshStatus} color="cyan" />
                <DetectorStatusPill label="Hand" status={tracking.handStatus} color="emerald" />
                <DetectorStatusPill label="Pose" status={tracking.poseStatus} color="amber" />
                <span
                  className={classNames(
                    "px-2 py-0.5 rounded-full text-[11px] border",
                    tracking.avatarEnabled
                      ? "bg-cyan-500/20 border-cyan-400/50 text-cyan-50"
                      : "bg-white/5 border-white/20 text-white/70"
                  )}
                >
                  {tracking.avatarEnabled
                    ? `角色开（${tracking.avatarStyle === "robot" ? "机器人" : "蛋"}）`
                    : "角色关"}
                </span>
              </div>
              <div className="text-xs text-white/60">
                若未显示视频，请确认摄像头权限。
              </div>
            </div>
            <div className={classNames("camera-pair", !showCameraPreview && "camera-pair--mesh-only")}>
              <div className={classNames("camera-frame", !showCameraPreview && "camera-frame--hidden")}>
                <video
                  ref={tracking.videoRef as React.RefObject<HTMLVideoElement>}
                  className="camera-video"
                  muted
                  playsInline
                  autoPlay
                />
              </div>
              <div className={classNames("mesh-frame", !showCameraPreview && "mesh-frame--solo")}>
                <canvas ref={tracking.overlayRef as React.RefObject<HTMLCanvasElement>} className="mesh-canvas" />
              </div>
            </div>
          </section>

          <section className="w-full max-w-[1080px] rounded-2xl border border-emerald-300/25 bg-emerald-500/5 p-2.5 backdrop-blur">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-[10px] md:text-[11px] font-semibold text-emerald-100/90 whitespace-nowrap">
                  {activeBroadcastMode || aiLongPreloadPlaying ? "当前播报内容" : "最近播报内容"}
                </span>
                <span className="px-1.5 py-0.5 rounded-full border border-emerald-300/35 bg-emerald-500/10 text-[10px] text-emerald-100/85">
                  {modeLabel(nowPlayingDisplayMode)}
                </span>
              </div>
              <span className="text-[10px] text-white/45 whitespace-nowrap">
                {nowPlayingTs
                  ? new Date(nowPlayingTs).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })
                  : ""}
              </span>
            </div>
            <div className="mt-1.5 rounded-lg border border-white/10 bg-black/25 px-2 py-1.5 min-h-[2.8rem] max-h-24 overflow-auto text-[11px] md:text-[12px] leading-relaxed text-white/90 whitespace-pre-wrap break-words">
              {nowPlayingText || "播报开始后，这里会显示当前内容。"}
            </div>
          </section>

          <div className="w-full max-w-[1080px] grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_auto] gap-2">
            <label className="flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-1.5">
              <span className="text-[10px] md:text-[11px] text-white/70 whitespace-nowrap">
                语音引擎
              </span>
              <select
                value={ttsEngine}
                onChange={(e) => setTTSEngine(e.target.value as TTSEngine)}
                className="min-w-0 flex-1 bg-transparent text-[10px] md:text-[11px] text-white/90 outline-none"
                disabled={tts.speaking || aiLongPreloadPlaying}
              >
                <option value="browser" className="bg-slate-900">
                  浏览器内置 TTS（当前）
                </option>
                <option
                  value="gpt_sovits_v4"
                  className="bg-slate-900"
                  disabled={gptTtsAvailable === false}
                >
                  GPT-SoVITS v4（实验）
                </option>
              </select>
            </label>
            <div className="flex items-center justify-between rounded-xl border border-white/15 bg-white/5 px-2 py-1.5 text-[10px] md:text-[11px] text-white/70">
              <span>
                当前: {ttsEngine === "gpt_sovits_v4" ? "GPT-SoVITS v4" : "浏览器 TTS"}
              </span>
              {ttsEngine === "gpt_sovits_v4" && (
                <span
                  className={classNames(
                    gptTtsAvailable
                      ? "text-emerald-300/90"
                      : gptTtsAvailable === false
                        ? "text-rose-300/90"
                        : "text-amber-300/90"
                  )}
                >
                  {ttsProviderLoading
                    ? "检测中..."
                    : gptTtsAvailable
                      ? "服务在线"
                      : gptTtsAvailable === false
                        ? "服务离线（自动回退浏览器）"
                        : "需本地启动 127.0.0.1:9880"}
                </span>
              )}
            </div>
          </div>
          {ttsEngine === "gpt_sovits_v4" && gptTtsEndpoint && (
            <div className="w-full max-w-[1080px] -mt-1 text-[10px] md:text-[11px] text-white/45 truncate">
              GPT-SoVITS: {gptTtsEndpoint}
            </div>
          )}
          <div className="w-full max-w-[1080px] flex items-center justify-between gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-1.5 text-[10px] md:text-[11px] text-white/75">
            <span>语速倍率: {ttsRateMultiplier.toFixed(2)}x</span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setTTSRateMultiplier(Number(Math.max(0.82, ttsRateMultiplier - 0.08).toFixed(2)))}
                disabled={tts.speaking || aiLongPreloadPlaying}
                className="px-2 py-1 rounded-lg border border-white/20 bg-white/5 text-white/80 hover:bg-white/10 disabled:opacity-40"
              >
                慢一点
              </button>
              <button
                onClick={() => setTTSRateMultiplier(1)}
                disabled={tts.speaking || aiLongPreloadPlaying}
                className="px-2 py-1 rounded-lg border border-white/20 bg-white/5 text-white/80 hover:bg-white/10 disabled:opacity-40"
              >
                默认
              </button>
              <button
                onClick={() => setTTSRateMultiplier(Number(Math.min(1.28, ttsRateMultiplier + 0.08).toFixed(2)))}
                disabled={tts.speaking || aiLongPreloadPlaying}
                className="px-2 py-1 rounded-lg border border-white/20 bg-white/5 text-white/80 hover:bg-white/10 disabled:opacity-40"
              >
                快一点
              </button>
            </div>
          </div>

          {/* Controls */}
          <div className="grid w-full max-w-[1080px] grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2">
            <button
              onClick={speech.toggle}
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border font-medium transition-all",
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
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
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
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
                voiceEnabled
                  ? "bg-emerald-500/20 border-emerald-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {voiceEnabled ? "静音回复播报" : "开启回复播报"}
            </button>
            <button
              onClick={() => tracking.setCamFollow((v) => !v)}
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
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
              className="px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition bg-amber-500/20 border-amber-400/60"
            >
              {tracking.cameraStarted ? "关闭摄像头" : "开启摄像头"}
            </button>
            <button
              onClick={() => tracking.setMeshEnabled((v) => !v)}
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
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
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
                tracking.mirrorEnabled
                  ? "bg-indigo-500/20 border-indigo-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {tracking.mirrorEnabled ? "镜像视图" : "正常视图"}
            </button>
            <button
              onClick={() => setShowCameraPreview(!showCameraPreview)}
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
                showCameraPreview
                  ? "bg-slate-500/20 border-slate-400/60"
                  : "bg-emerald-500/20 border-emerald-400/60"
              )}
            >
              {showCameraPreview ? "隐藏摄像画面" : "显示摄像画面"}
            </button>
            <button
              onClick={() => tracking.setHandEnabled((v) => !v)}
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
                tracking.handEnabled
                  ? "bg-emerald-500/20 border-emerald-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {tracking.handEnabled
                ? "关闭手部"
                : tracking.handStatus === "loading"
                  ? "手部加载中..."
                  : "开启手部"}
            </button>
            <button
              onClick={() => tracking.setPoseEnabled((v) => !v)}
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
                tracking.poseEnabled
                  ? "bg-amber-500/20 border-amber-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {tracking.poseEnabled
                ? "关闭身体"
                : tracking.poseStatus === "loading"
                  ? "身体加载中..."
                  : "开启身体"}
            </button>
            <button
              onClick={() => tracking.setAvatarEnabled((v) => !v)}
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
                tracking.avatarEnabled
                  ? "bg-cyan-500/20 border-cyan-400/60"
                  : "bg-white/5 border-white/15"
              )}
              disabled={!tracking.meshEnabled && !tracking.poseEnabled}
            >
              {tracking.avatarEnabled ? "关闭角色叠层" : "开启角色叠层"}
            </button>
            <button
              onClick={() =>
                tracking.setAvatarStyle((prev) => (prev === "robot" ? "egg" : "robot"))
              }
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/80 hover:bg-white/10 transition",
                "bg-white/5 border-white/15"
              )}
              disabled={!tracking.avatarEnabled}
            >
              {tracking.avatarStyle === "robot" ? "切换为蛋角色" : "切换为机器人"}
            </button>
          </div>
          {lastVoiceAction && (
            <div className="w-full max-w-[1080px] -mt-1 text-[10px] md:text-[11px] text-cyan-100/75 truncate">
              最近语音命令: {lastVoiceAction}
              {lastVoiceActionTs
                ? ` · ${new Date(lastVoiceActionTs).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}`
                : ""}
            </div>
          )}
          <div className="mt-2 w-full max-w-[900px] mx-auto grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_auto_auto] gap-2">
            <label className="flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-1.5">
              <span className="text-[10px] md:text-[11px] text-white/70 whitespace-nowrap">
                播报类型
              </span>
              <select
                value={selectedBroadcastMode}
                onChange={(e) =>
                  setSelectedBroadcastMode(e.target.value as BroadcastMode)
                }
                className="min-w-0 flex-1 bg-transparent text-[10px] md:text-[11px] text-white/90 outline-none"
                disabled={broadcastBusy}
              >
                <option value="news" className="bg-slate-900">
                  新闻简报
                </option>
                <option value="market" className="bg-slate-900">
                  股市简报
                </option>
                <option value="horror" className="bg-slate-900">
                  恐怖故事
                </option>
              </select>
            </label>
            <button
              onClick={() =>
                void ((activeBroadcastMode || aiLongPreloadPlaying)
                  ? forceStopBroadcast()
                  : toggleSelectedBroadcast())
              }
              disabled={(activeBroadcastMode || aiLongPreloadPlaying) ? stopAllBusy : broadcastBusy}
              className={classNames(
                "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/85 hover:bg-white/10 transition disabled:opacity-40",
                (activeBroadcastMode || aiLongPreloadPlaying)
                  ? "bg-amber-500/20 border-amber-400/60"
                  : "bg-white/5 border-white/15"
              )}
            >
              {(activeBroadcastMode || aiLongPreloadPlaying)
                ? stopAllBusy
                  ? "停止中..."
                  : "停止当前播报"
                : broadcastBusy
                  ? "处理中..."
                  : "开启选中播报"}
            </button>
            <button
              onClick={() => void forceStopBroadcast()}
              disabled={stopAllBusy || !hasActivePlayback}
              className="px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/85 hover:bg-white/10 transition disabled:opacity-40 bg-rose-500/20 border-rose-400/60"
            >
              {stopAllBusy ? "停止中..." : activeBroadcastMode || aiLongPreloadPlaying ? "强制停播" : "停止语音"}
            </button>
          </div>
          <div className="w-full max-w-[900px] mx-auto rounded-xl border border-cyan-300/25 bg-cyan-500/5 p-2.5">
            <div className="text-[10px] md:text-[11px] text-cyan-100/90 mb-2">
              AI长文独立流程（与新闻/股市/恐怖分离）
            </div>
            <div className="grid grid-cols-1 md:grid-cols-[auto_minmax(0,1fr)_auto_auto] gap-2">
              <label className="flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-1.5">
                <span className="text-[10px] md:text-[11px] text-white/70 whitespace-nowrap">来源</span>
                <select
                  value={aiLongSourceMode}
                  onChange={(e) => setAiLongSourceMode(e.target.value as "preset" | "topic")}
                  className="min-w-0 flex-1 bg-transparent text-[10px] md:text-[11px] text-white/90 outline-none"
                  disabled={aiLongPreloadRunning || aiLongPreloadPlaying}
                >
                  <option value="preset" className="bg-slate-900">预置长文</option>
                  <option value="topic" className="bg-slate-900">按主题生成</option>
                </select>
              </label>
              {aiLongSourceMode === "preset" ? (
                <label className="flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-1.5">
                  <span className="text-[10px] md:text-[11px] text-white/70 whitespace-nowrap">长文</span>
                  <select
                    value={aiLongScriptKey}
                    onChange={(e) => setAiLongScriptKey(e.target.value)}
                    className="min-w-0 flex-1 bg-transparent text-[10px] md:text-[11px] text-white/90 outline-none"
                    disabled={aiLongPreloadRunning || aiLongPreloadPlaying}
                  >
                    {aiLongScripts.length === 0 ? (
                      <option value="cccc_intro_v1" className="bg-slate-900">CCCC框架介绍</option>
                    ) : (
                      aiLongScripts.map((s) => (
                        <option key={s.key} value={s.key} className="bg-slate-900">
                          {s.title}
                        </option>
                      ))
                    )}
                  </select>
                </label>
              ) : (
                <label className="flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-1.5">
                  <span className="text-[10px] md:text-[11px] text-white/70 whitespace-nowrap">主题</span>
                  <input
                    value={aiLongTopic}
                    onChange={(e) => setAiLongTopic(e.target.value)}
                    placeholder="例如：GPT-SoVITS v4 原理与调优"
                    className="min-w-0 flex-1 bg-transparent text-[10px] md:text-[11px] text-white/90 outline-none placeholder:text-white/40"
                    disabled={aiLongPreloadRunning || aiLongPreloadPlaying}
                  />
                </label>
              )}
              <button
                onClick={() => void preloadAiLongAudio()}
                disabled={!canTriggerAiLongPreload}
                className={classNames(
                  "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/85 hover:bg-white/10 transition disabled:opacity-40",
                  aiLongPreloadRunning ? "bg-amber-500/20 border-amber-400/60" : "bg-cyan-500/20 border-cyan-400/60"
                )}
              >
                {aiLongPreloadBusy || aiLongPreloadRunning ? "预加载中..." : "触发预加载"}
              </button>
              <button
                onClick={() => void playPreloadedAiLong()}
                disabled={!group?.group_id || !aiLongPreloadReady || aiLongPreloadPlaying || Boolean(activeBroadcastMode)}
                className={classNames(
                  "px-2 py-1.5 text-[10px] md:text-[11px] leading-tight whitespace-nowrap rounded-xl border text-white/85 hover:bg-white/10 transition disabled:opacity-40",
                  aiLongPreloadPlaying ? "bg-amber-500/20 border-amber-400/60" : "bg-emerald-500/20 border-emerald-400/60"
                )}
              >
                {aiLongPreloadPlaying ? "播放中..." : "播放预加载长文"}
              </button>
            </div>
            <div className="mt-2 flex items-center rounded-xl border border-white/15 bg-white/5 px-2 py-1.5 text-[10px] md:text-[11px] text-white/70 min-w-0">
              <span className="truncate" title={aiLongPreloadSummary}>
                {aiLongPreloadSummary}
              </span>
            </div>
          </div>
          {/* TTS error feedback */}
          {tts.ttsError && (
            <div className="text-xs text-amber-400 text-center mt-1">
              {tts.ttsErrorMessage || "播报失败，已停止"}
            </div>
          )}
        </section>

        {/* Text input */}
        <section className="bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="flex items-center gap-2 text-sm font-semibold text-white/90 mb-3">
            文字/语音 转发到 Agent
          </div>
          <div className="flex flex-col gap-3">
            {/* 1.1: Interim speech text */}
            {speech.interimText && (
              <div className="text-sm text-white/50 italic px-1 animate-bubble-in">
                {speech.interimText}
              </div>
            )}
            <textarea
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend(textInput);
                }
              }}
              placeholder="输入消息… (Space 聆听)"
              className="w-full min-h-[88px] rounded-xl bg-black/30 border border-white/10 px-3 py-2 text-white/90 placeholder:text-white/40 focus:outline-none focus:ring-2 focus:ring-cyan-400/60"
            />
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs text-white/60">
                SSE 实时推送回复，请确保电脑端 Agent 已在工作组里运行。
              </div>
              <button
                onClick={() => handleSend(textInput)}
                disabled={!textInput.trim()}
                className={classNames(
                  "px-4 py-2 rounded-xl font-semibold transition",
                  textInput.trim()
                    ? "bg-cyan-500 text-black hover:bg-cyan-400"
                    : "bg-cyan-500/40 text-black/50 cursor-not-allowed"
                )}
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
                    : line.text.startsWith("发送失败")
                      ? "bg-red-500/15 text-red-200 self-start"
                      : "bg-white/8 text-white/90 self-start"
                )}
              >
                <span className="text-xs uppercase tracking-wide opacity-60 mr-2">
                  {line.who === "me" ? "ME" : "AGENT"}
                </span>
                <span className="text-[10px] text-white/40 mr-2">
                  {new Date(line.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
                {line.text}
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>
        </section>

        {/* Mobile companion QR code */}
        {group?.group_id && (() => {
          const shareUrl = buildShareUrl(group.group_id);
          return (
            <section className="bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
              <div className="text-sm font-semibold text-white/90 mb-3">
                手机扫码连接
              </div>
              {IS_LOCALHOST && (
                <div className="mb-3 flex items-center gap-2">
                  <span className="text-xs text-white/50">局域网 IP:</span>
                  <input
                    type="text"
                    value={lanIp}
                    onChange={(e) => handleLanIpChange(e.target.value)}
                    placeholder="自动检测中..."
                    className="w-40 px-2 py-1 rounded-lg bg-black/40 border border-white/15 text-white/90 text-xs font-mono placeholder:text-white/30 focus:outline-none focus:ring-1 focus:ring-cyan-400/60"
                  />
                  <span className="text-[10px] text-white/40">:{window.location.port || "80"}</span>
                </div>
              )}
              <div className="flex items-center gap-6">
                {shareUrl ? (
                  <>
                    <div className="bg-white rounded-xl p-3 flex-shrink-0">
                      <QRCodeSVG value={shareUrl} size={120} level="M" />
                    </div>
                    <div className="flex flex-col gap-2">
                      <p className="text-sm text-white/70">
                        用手机扫描二维码，打开全屏伴侣模式。
                        两端共享同一工作组，对话实时同步。
                      </p>
                      <div className="text-[10px] text-white/40 font-mono break-all">
                        {shareUrl}
                      </div>
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-white/50">
                    请先输入局域网 IP 以生成二维码。
                  </p>
                )}
              </div>
            </section>
          );
        })()}

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
//  Detector status pill
// ────────────────────────────────────────────
function DetectorStatusPill({
  label,
  status,
  color,
}: {
  label: string;
  status: "idle" | "loading" | "ready" | "error";
  color: "cyan" | "emerald" | "amber";
}) {
  const colorMap = {
    cyan: {
      ready: "bg-cyan-500/20 border-cyan-400/60 text-cyan-50",
      loading: "bg-amber-500/20 border-amber-400/60 text-amber-50",
      error: "bg-red-500/20 border-red-400/60 text-red-50",
      idle: "bg-white/5 border-white/20 text-white/70",
    },
    emerald: {
      ready: "bg-emerald-500/20 border-emerald-400/60 text-emerald-50",
      loading: "bg-amber-500/20 border-amber-400/60 text-amber-50",
      error: "bg-red-500/20 border-red-400/60 text-red-50",
      idle: "bg-white/5 border-white/20 text-white/70",
    },
    amber: {
      ready: "bg-amber-500/20 border-amber-400/60 text-amber-50",
      loading: "bg-amber-500/20 border-amber-400/60 text-amber-50 animate-pulse",
      error: "bg-red-500/20 border-red-400/60 text-red-50",
      idle: "bg-white/5 border-white/20 text-white/70",
    },
  };
  const statusLabel = {
    ready: "就绪",
    loading: "加载中",
    error: "错误",
    idle: "未启用",
  };
  return (
    <span
      className={classNames(
        "px-2 py-0.5 rounded-full text-[11px] border",
        colorMap[color][status]
      )}
    >
      {label} {statusLabel[status]}
    </span>
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
