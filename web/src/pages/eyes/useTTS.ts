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
  const speakingRef = useRef(false);

  const speak = useCallback(
    (text: string, onDone?: () => void) => {
      if (!("speechSynthesis" in window) || !text) {
        onDone?.();
        return;
      }

      window.speechSynthesis.cancel();
      const chunks = splitForTTS(text);

      setSpeaking(true);
      speakingRef.current = true;

      const resetAfterSpeak = () => {
        speakingRef.current = false;
        setSpeaking(false);
        onDone?.();
      };

      // Watchdog: force-reset if TTS stalls
      const watchdog = setTimeout(() => {
        if (speakingRef.current) {
          window.speechSynthesis.cancel();
          resetAfterSpeak();
        }
      }, 30000);

      let i = 0;
      const speakNext = () => {
        if (i >= chunks.length) {
          clearTimeout(watchdog);
          resetAfterSpeak();
          return;
        }
        const u = new SpeechSynthesisUtterance(chunks[i]);
        u.lang = lang;
        i++;
        u.onend = () => speakNext();
        u.onerror = () => speakNext();
        window.speechSynthesis.speak(u);
      };
      speakNext();
    },
    [lang]
  );

  const cancel = useCallback(() => {
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    speakingRef.current = false;
    setSpeaking(false);
  }, []);

  return { speak, cancel, speaking };
}
