import { useState, useRef, useCallback, useEffect } from "react";

interface UseSpeechRecognitionOptions {
  /** Called with finalized speech text */
  onResult: (text: string) => void;
  /** Pause recognition (e.g. when TTS is speaking) */
  paused?: boolean;
  lang?: string;
}

/**
 * Speech recognition hook with bug fixes:
 * - interimResults enabled + 1.5s silence timer for auto-commit
 * - 50s watchdog to restart before Chrome's 60s timeout
 * - `ready` state ensures recognition is initialized before starting
 */
export function useSpeechRecognition({
  onResult,
  paused = false,
  lang = "zh-CN",
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
  const interimBufferRef = useRef("");
  const onResultRef = useRef(onResult);

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

  const startRecognition = useCallback(() => {
    const rec = recognitionRef.current;
    const now = Date.now();
    if (!rec || listeningRef.current || pausedRef.current) return;
    if (now - lastRecStartRef.current < 600) return; // rate-limit
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

    let restartPending = false;

    const commitInterimBuffer = () => {
      const text = interimBufferRef.current.trim();
      interimBufferRef.current = "";
      setInterimText("");
      if (!text) return;
      const now = Date.now();
      if (
        lastSpeechRef.current.text === text &&
        now - lastSpeechRef.current.ts < 4000
      ) {
        return;
      }
      lastSpeechRef.current = { text, ts: now };
      onResultRef.current(text);
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
      // Flush any accumulated interim text
      commitInterimBuffer();
      if (autoListenRef.current && !pausedRef.current) {
        safeRestart(300);
      }
    };

    rec.onerror = (ev: any) => {
      const errType = ev?.error || "";
      listeningRef.current = false;
      setListening(false);
      clearTimeout(watchdogRef.current);
      // "no-speech" is normal silence timeout
      if (errType === "no-speech" || errType === "aborted") {
        safeRestart(400);
        return;
      }
      // Fatal errors
      if (errType === "not-allowed" || errType === "service-not-allowed") {
        return;
      }
      // Other errors (network, etc.) — retry
      safeRestart(1000);
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
          const textNorm = transcript.trim();
          if (!textNorm) continue;
          const now = Date.now();
          if (
            lastSpeechRef.current.text === textNorm &&
            now - lastSpeechRef.current.ts < 4000
          ) {
            continue; // skip duplicate
          }
          lastSpeechRef.current = { text: textNorm, ts: now };
          onResultRef.current(textNorm);
        } else {
          // Interim result — accumulate and set 1.5s silence timer
          interimBufferRef.current = transcript;
          setInterimText(transcript);
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = setTimeout(() => {
            commitInterimBuffer();
          }, 1500);
        }
      }
    };

    recognitionRef.current = rec;
    setSupported(true);
    setReady(true);

    return () => {
      clearTimeout(silenceTimerRef.current);
      clearTimeout(watchdogRef.current);
      try {
        rec.stop();
      } catch {}
      recognitionRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang]);

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
    }, 3000);
    return () => clearInterval(keepalive);
  }, [autoListen, supported, startRecognition]);

  // Stop recognition when paused (e.g. TTS speaking)
  useEffect(() => {
    if (paused && listeningRef.current && recognitionRef.current) {
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
