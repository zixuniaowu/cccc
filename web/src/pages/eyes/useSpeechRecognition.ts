import { useState, useRef, useCallback, useEffect } from "react";

interface UseSpeechRecognitionOptions {
  /** Called with finalized speech text */
  onResult: (text: string) => void;
  /** Pause recognition (e.g. when TTS is speaking) */
  paused?: boolean;
  lang?: string;
  /** Optional keyword hints to improve recognition accuracy for domain terms */
  hints?: string[];
}

function normalizeTranscriptText(text: string): string {
  return String(text || "")
    .replace(/[，。！？、；：,.!?:;"'`~(){}<>【】（）]/g, " ")
    .replace(/\[/g, " ")
    .replace(/\]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function isLikelyVoiceCommand(text: string): boolean {
  const compact = String(text || "").replace(/\s+/g, "");
  if (!compact) return false;
  return /(新闻简报|股市简报|恐怖故事|停止播报|强制停播|开启播报|预加载|播放长文|切换机器人|蛋角色)/.test(
    compact
  );
}

function isFillerUtterance(text: string): boolean {
  const t = String(text || "").replace(/\s+/g, "");
  if (!t) return true;
  // Keep this strict to avoid dropping real short queries (e.g. “天气呢”, “在吗”).
  return /^(嗯|啊|哦|呃|诶|喂|哈|哎|欸|额|嗯嗯|啊啊)$/.test(t);
}

/**
 * Speech recognition hook with bug fixes:
 * - interimResults enabled + ~0.9s silence timer for auto-commit
 * - eager commit on end punctuation for faster turn-taking
 * - 50s watchdog to restart before Chrome's 60s timeout
 * - `ready` state ensures recognition is initialized before starting
 */
export function useSpeechRecognition({
  onResult,
  paused = false,
  lang = "zh-CN",
  hints = [],
}: UseSpeechRecognitionOptions) {
  const [listening, setListening] = useState(false);
  const [autoListen, setAutoListen] = useState(false);
  const [supported, setSupported] = useState(false);
  const [ready, setReady] = useState(false);
  const [interimText, setInterimText] = useState("");

  const recognitionRef = useRef<any>(null);
  const listeningRef = useRef(false);
  const autoListenRef = useRef(false);
  const pausedRef = useRef(false);
  const lastRecStartRef = useRef(0);
  const lastSpeechRef = useRef<{ text: string; ts: number }>({
    text: "",
    ts: 0,
  });
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const watchdogRef = useRef<ReturnType<typeof setTimeout>>();
  const shortCommitTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const suppressCommitOnEndRef = useRef(false);
  const interimBufferRef = useRef("");
  const pendingShortRef = useRef<{ text: string; ts: number } | null>(null);
  const onResultRef = useRef(onResult);
  const hintsRef = useRef<string[]>(hints);

  // Sync refs
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);
  useEffect(() => {
    autoListenRef.current = autoListen;
  }, [autoListen]);
  useEffect(() => {
    onResultRef.current = onResult;
  }, [onResult]);
  useEffect(() => {
    hintsRef.current = hints;
  }, [hints]);

  const startRecognition = useCallback(() => {
    const rec = recognitionRef.current;
    const now = Date.now();
    if (!rec || listeningRef.current || pausedRef.current) return;
    if (now - lastRecStartRef.current < 280) return; // faster restart rate-limit
    try {
      rec.start();
      lastRecStartRef.current = now;
    } catch {
      // start while active may throw; safe to ignore
    }
  }, []);

  // Initialize speech recognition
  useEffect(() => {
    const SpeechRecognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    const rec = new SpeechRecognition();
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = true; // Bug fix: enable interim results
    rec.maxAlternatives = 1;

    // Optional phrase hints for engines that support contextual biasing.
    try {
      if ("phrases" in rec) {
        (rec as any).phrases = hintsRef.current
          .filter(Boolean)
          .slice(0, 64)
          .map((value) => ({ value, boost: 8.0 }));
      }
    } catch {}
    try {
      const SpeechGrammarList =
        (window as any).SpeechGrammarList ||
        (window as any).webkitSpeechGrammarList;
      if (SpeechGrammarList && hintsRef.current.length > 0) {
        const list = new SpeechGrammarList();
        const escaped = hintsRef.current
          .filter(Boolean)
          .slice(0, 48)
          .map((s) => String(s).replace(/[;|]/g, " "));
        if (escaped.length) {
          const grammar = `#JSGF V1.0; grammar hints; public <hint> = ${escaped.join(" | ")} ;`;
          list.addFromString(grammar, 1);
          rec.grammars = list;
        }
      }
    } catch {}

    let restartPending = false;

    const discardBuffers = () => {
      clearTimeout(silenceTimerRef.current);
      clearTimeout(shortCommitTimerRef.current);
      interimBufferRef.current = "";
      pendingShortRef.current = null;
      setInterimText("");
    };

    const emitRecognizedText = (rawText: string) => {
      const text = normalizeTranscriptText(rawText);
      if (!text) return;
      if (isFillerUtterance(text) && !isLikelyVoiceCommand(text)) return;
      const now = Date.now();

      // Merge likely ASR fragments (e.g. "而是", "然后") with the next chunk.
      if (!isLikelyVoiceCommand(text) && text.length <= 3) {
        pendingShortRef.current = { text, ts: now };
        clearTimeout(shortCommitTimerRef.current);
        shortCommitTimerRef.current = setTimeout(() => {
          const pending = pendingShortRef.current;
          if (!pending) return;
          if (
            lastSpeechRef.current.text === pending.text &&
            Date.now() - lastSpeechRef.current.ts < 4000
          ) {
            pendingShortRef.current = null;
            return;
          }
          lastSpeechRef.current = { text: pending.text, ts: Date.now() };
          onResultRef.current(pending.text);
          pendingShortRef.current = null;
        }, 900);
        return;
      }

      const pending = pendingShortRef.current;
      if (pending && now - pending.ts < 1400) {
        pendingShortRef.current = null;
        clearTimeout(shortCommitTimerRef.current);
        rawText = `${pending.text}${text}`;
      } else {
        rawText = text;
      }

      const merged = normalizeTranscriptText(rawText);
      if (!merged) return;
      if (
        lastSpeechRef.current.text === merged &&
        now - lastSpeechRef.current.ts < 4000
      ) {
        return;
      }
      lastSpeechRef.current = { text: merged, ts: now };
      onResultRef.current(merged);
    };

    const commitInterimBuffer = () => {
      const text = normalizeTranscriptText(interimBufferRef.current);
      interimBufferRef.current = "";
      setInterimText("");
      emitRecognizedText(text);
    };

    const safeRestart = (delayMs: number) => {
      if (restartPending || pausedRef.current || !autoListenRef.current) return;
      restartPending = true;
      setTimeout(() => {
        restartPending = false;
        startRecognition();
      }, delayMs);
    };

    rec.onstart = () => {
      listeningRef.current = true;
      setListening(true);
      // 50s watchdog: restart before Chrome's 60s timeout
      clearTimeout(watchdogRef.current);
      watchdogRef.current = setTimeout(() => {
        if (listeningRef.current && recognitionRef.current) {
          try {
            recognitionRef.current.stop();
          } catch {}
        }
      }, 50000);
    };

    rec.onend = () => {
      listeningRef.current = false;
      setListening(false);
      clearTimeout(watchdogRef.current);
      // If recognition was intentionally stopped (manual toggle / pause),
      // do not auto-submit trailing fragments.
      if (
        suppressCommitOnEndRef.current ||
        pausedRef.current ||
        !autoListenRef.current
      ) {
        suppressCommitOnEndRef.current = false;
        discardBuffers();
      } else {
        commitInterimBuffer();
      }
      if (autoListenRef.current && !pausedRef.current) {
        safeRestart(160);
      }
    };

    rec.onerror = (ev: any) => {
      const errType = ev?.error || "";
      listeningRef.current = false;
      setListening(false);
      clearTimeout(watchdogRef.current);
      // "no-speech" is normal silence timeout
      if (errType === "no-speech" || errType === "aborted") {
        safeRestart(220);
        return;
      }
      // Fatal errors
      if (errType === "not-allowed" || errType === "service-not-allowed") {
        return;
      }
      // Other errors (network, etc.) — retry
      safeRestart(700);
    };

    rec.onresult = (event: any) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const transcript = result[0]?.transcript || "";

        if (result.isFinal) {
          // Final result — send immediately
          clearTimeout(silenceTimerRef.current);
          interimBufferRef.current = "";
          setInterimText("");
          const textNorm = normalizeTranscriptText(transcript);
          if (!textNorm) continue;
          const confidenceRaw = Number(result[0]?.confidence);
          const confidence = Number.isFinite(confidenceRaw) ? confidenceRaw : 1;
          // Some engines report 0 for valid Chinese finals. Only filter ultra-short noise.
          if (confidence >= 0 && confidence < 0.12 && textNorm.length <= 2) {
            continue;
          }
          emitRecognizedText(textNorm);
        } else {
          // Interim result — accumulate and set short silence timer
          interimBufferRef.current = normalizeTranscriptText(transcript);
          setInterimText(transcript);
          clearTimeout(silenceTimerRef.current);
          const trimmed = transcript.trim();
          const normalized = normalizeTranscriptText(trimmed);
          const eagerCommand =
            normalized.length >= 2 && isLikelyVoiceCommand(normalized);
          const eagerCommit = /[。！？!?]$/.test(trimmed) && trimmed.length >= 4;
          silenceTimerRef.current = setTimeout(() => {
            commitInterimBuffer();
          }, eagerCommand ? 130 : eagerCommit ? 220 : 520);
        }
      }
    };

    recognitionRef.current = rec;
    setSupported(true);
    setReady(true);

    return () => {
      suppressCommitOnEndRef.current = true;
      clearTimeout(silenceTimerRef.current);
      clearTimeout(watchdogRef.current);
      clearTimeout(shortCommitTimerRef.current);
      try {
        rec.stop();
      } catch {}
      recognitionRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang, hints]);

  // Auto-start when enabled and ready
  useEffect(() => {
    if (autoListen && supported && ready && !paused) {
      startRecognition();
    }
  }, [autoListen, supported, ready, paused, startRecognition]);

  // Keepalive: periodically check if auto-listen should be active
  useEffect(() => {
    if (!autoListen || !supported) return;
    const keepalive = setInterval(() => {
      if (
        autoListenRef.current &&
        !listeningRef.current &&
        !pausedRef.current
      ) {
        startRecognition();
      }
    }, 1500);
    // Use a tighter keepalive interval so auto-listen recovers faster.
    return () => clearInterval(keepalive);
  }, [autoListen, supported, startRecognition]);

  // Turning off auto-listen should stop current recognition immediately.
  // This prevents trailing fragments from being emitted after user disabled it.
  useEffect(() => {
    if (autoListen) return;
    if (!listeningRef.current || !recognitionRef.current) return;
    suppressCommitOnEndRef.current = true;
    clearTimeout(silenceTimerRef.current);
    clearTimeout(shortCommitTimerRef.current);
    interimBufferRef.current = "";
    pendingShortRef.current = null;
    setInterimText("");
    try {
      recognitionRef.current.stop();
    } catch {}
  }, [autoListen]);

  // Stop recognition when paused (e.g. TTS speaking)
  useEffect(() => {
    if (paused && listeningRef.current && recognitionRef.current) {
      suppressCommitOnEndRef.current = true;
      clearTimeout(silenceTimerRef.current);
      clearTimeout(shortCommitTimerRef.current);
      interimBufferRef.current = "";
      pendingShortRef.current = null;
      setInterimText("");
      try {
        recognitionRef.current.stop();
      } catch {}
    }
  }, [paused]);

  const toggle = useCallback(() => {
    if (!supported || !recognitionRef.current) return;
    if (listening) {
      autoListenRef.current = false;
      setAutoListen(false);
      suppressCommitOnEndRef.current = true;
      clearTimeout(silenceTimerRef.current);
      clearTimeout(shortCommitTimerRef.current);
      interimBufferRef.current = "";
      pendingShortRef.current = null;
      setInterimText("");
      recognitionRef.current.stop();
    } else {
      startRecognition();
    }
  }, [supported, listening, startRecognition]);

  return {
    listening,
    supported,
    ready,
    autoListen,
    setAutoListen,
    toggle,
    startRecognition,
    interimText,
  };
}
