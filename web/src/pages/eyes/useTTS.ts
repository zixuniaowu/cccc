import { useState, useRef, useCallback } from "react";

/**
 * Split text at sentence boundaries into chunks <= maxLen chars.
 * Chrome bug: long utterances silently cut off (~200+ chars).
 */
function splitForTTS(s: string, maxLen = 150): string[] {
  const parts = s.split(/(?<=[。！？!?；;\n])/);
  const chunks: string[] = [];
  let buf = "";
  for (const p of parts) {
    if (buf.length + p.length > maxLen && buf) {
      chunks.push(buf.trim());
      buf = "";
    }
    buf += p;
  }
  if (buf.trim()) chunks.push(buf.trim());
  return chunks.filter(Boolean);
}

/**
 * TTS hook with chunk splitting and watchdog timer.
 * Returns { speak, cancel, speaking }.
 */
export function useTTS(lang = "zh-CN") {
  const [speaking, setSpeaking] = useState(false);
  const [ttsError, setTtsError] = useState(false);
  /** Progress: [current 1-based chunk index, total chunks] */
  const [ttsProgress, setTtsProgress] = useState<[number, number]>([0, 0]);
  const speakingRef = useRef(false);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearWatchdog = useCallback(() => {
    if (watchdogRef.current) {
      clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
  }, []);

  const speak = useCallback(
    (text: string, onDone?: () => void) => {
      if (!("speechSynthesis" in window) || !text) {
        onDone?.();
        return;
      }

      clearWatchdog();
      window.speechSynthesis.cancel();
      const chunks = splitForTTS(text);

      setSpeaking(true);
      setTtsError(false);
      setTtsProgress([1, chunks.length]);
      speakingRef.current = true;

      const resetAfterSpeak = () => {
        clearWatchdog();
        speakingRef.current = false;
        setSpeaking(false);
        setTtsProgress([0, 0]);
        onDone?.();
      };

      // Watchdog: force-reset if TTS stalls
      watchdogRef.current = setTimeout(() => {
        if (speakingRef.current) {
          window.speechSynthesis.cancel();
          setTtsError(true);
          setTimeout(() => setTtsError(false), 5000); // auto-dismiss
          resetAfterSpeak();
        }
      }, 30000);

      let i = 0;
      const speakNext = () => {
        if (i >= chunks.length) {
          resetAfterSpeak();
          return;
        }
        const u = new SpeechSynthesisUtterance(chunks[i]);
        u.lang = lang;
        i++;
        setTtsProgress([i, chunks.length]);
        u.onend = () => speakNext();
        u.onerror = () => speakNext();
        window.speechSynthesis.speak(u);
      };
      speakNext();
    },
    [clearWatchdog, lang]
  );

  const cancel = useCallback(() => {
    clearWatchdog();
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    speakingRef.current = false;
    setSpeaking(false);
    setTtsProgress([0, 0]);
  }, [clearWatchdog]);

  return { speak, cancel, speaking, ttsError, ttsProgress };
}
