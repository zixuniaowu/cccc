import { useState, useRef, useCallback, useEffect } from "react";
import * as api from "../../services/api";
import type { TTSEngine } from "./usePreferences";

/**
 * Split text at sentence boundaries into chunks <= maxLen chars.
 * Chrome bug: long utterances silently cut off (~200+ chars).
 */
function splitForTTS(s: string, maxLen = 140): string[] {
  const text = (s || "").replace(/\s+/g, " ").trim();
  if (!text) return [];

  const parts = text.split(/(?<=[。！？!?；;\n])/);
  const chunks: string[] = [];
  const pushWithFallback = (piece: string) => {
    const p = (piece || "").trim();
    if (!p) return;
    if (p.length <= maxLen) {
      chunks.push(p);
      return;
    }
    // Secondary split for long run-on lines.
    const subs = p.split(/(?<=[，,、：:])/);
    let buf = "";
    for (const sub of subs) {
      if (!sub) continue;
      if (sub.length > maxLen) {
        if (buf.trim()) {
          chunks.push(buf.trim());
          buf = "";
        }
        // Final hard split.
        for (let i = 0; i < sub.length; i += maxLen) {
          const hard = sub.slice(i, i + maxLen).trim();
          if (hard) chunks.push(hard);
        }
        continue;
      }
      if (buf.length + sub.length > maxLen && buf) {
        chunks.push(buf.trim());
        buf = "";
      }
      buf += sub;
    }
    if (buf.trim()) chunks.push(buf.trim());
  };

  let lineBuf = "";
  for (const p of parts) {
    if (!p) continue;
    if (lineBuf.length + p.length > maxLen && lineBuf) {
      pushWithFallback(lineBuf);
      lineBuf = "";
    }
    lineBuf += p;
  }
  if (lineBuf.trim()) pushWithFallback(lineBuf);

  return chunks.filter(Boolean);
}

/**
 * Keep first audible chunk short in GPT mode to reduce "waiting before first sound".
 */
function splitFirstChunkForFastStart(chunks: string[], maxFirstLen = 52): string[] {
  if (!chunks.length) return chunks;
  const first = String(chunks[0] || "").trim();
  if (!first || first.length <= maxFirstLen) return chunks;

  let cut = first.lastIndexOf("，", maxFirstLen);
  if (cut < 0) cut = first.lastIndexOf(",", maxFirstLen);
  if (cut < 0) cut = first.lastIndexOf("。", maxFirstLen);
  if (cut < 0) cut = first.lastIndexOf("；", maxFirstLen);
  if (cut < 0) cut = first.lastIndexOf("：", maxFirstLen);
  if (cut < 16) cut = maxFirstLen;
  else cut += 1;

  const head = first.slice(0, cut).trim();
  const tail = first.slice(cut).trim();
  if (!head || !tail) return chunks;
  return [head, tail, ...chunks.slice(1)];
}

function chunkWatchdogMs(chunk: string, style: TTSStyle, rate: number): number {
  const len = (chunk || "").length;
  // Style-aware watchdog to avoid false timeout on slower dramatic speech.
  const basePerChar =
    style === "horror" ? 520 : style === "ai_long" ? 380 : 300;
  const safeRate = Math.max(0.65, Math.min(1.4, rate || 1));
  const estimate = 12000 + len * (basePerChar / safeRate);
  return Math.max(22000, Math.min(180000, estimate));
}

function clampNumber(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

type TTSStyle = "general" | "news" | "market" | "ai_long" | "horror";
type Prosody = { rate: number; pitch: number; volume: number };

type HorrorAmbientNodes = {
  master: GainNode;
  rumbleOsc: OscillatorNode;
  rumbleGain: GainNode;
  noiseSrc: AudioBufferSourceNode;
  noiseGain: GainNode;
  heartbeatTimer: number;
};

function normalizeTTSEngine(engine?: TTSEngine): TTSEngine {
  return engine === "gpt_sovits_v4" ? "gpt_sovits_v4" : "browser";
}

function isAbortError(err: unknown): boolean {
  if (!err) return false;
  if (typeof DOMException !== "undefined" && err instanceof DOMException) {
    return err.name === "AbortError";
  }
  if (err instanceof Error) {
    return err.name === "AbortError";
  }
  return false;
}

function detectStyle(text: string): TTSStyle {
  const t = String(text || "").trim();
  if (t.startsWith("[恐怖故事]")) return "horror";
  if (t.startsWith("[股市简报]")) return "market";
  if (t.startsWith("[新闻简报]") || t.startsWith("[早间简报]")) return "news";
  if (/(黑暗|脚步|低语|门缝|阴冷|诡异|背后|回头|呼吸|午夜|电台|走廊|空房|回声)/.test(t)) {
    return "horror";
  }
  if (t.startsWith("[AI长文说明]") || t.startsWith("[AI新技术说明]") || t.length >= 120) {
    return "ai_long";
  }
  return "general";
}

function createNoiseBuffer(ctx: AudioContext, durationSec = 2.0): AudioBuffer {
  const frameCount = Math.max(1, Math.floor(ctx.sampleRate * durationSec));
  const buffer = ctx.createBuffer(1, frameCount, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  let last = 0;
  for (let i = 0; i < frameCount; i++) {
    // Lightly filtered noise for smoother ambience.
    const white = Math.random() * 2 - 1;
    last = (last + 0.05 * white) * 0.985;
    data[i] = last;
  }
  return buffer;
}

function chooseBestVoice(
  pool: SpeechSynthesisVoice[],
  scoreFn: (v: SpeechSynthesisVoice) => number
): SpeechSynthesisVoice | null {
  if (!pool.length) return null;
  const ranked = pool
    .map((v) => ({ v, score: scoreFn(v) }))
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      // Stable tie-break to avoid random voice switching between segments.
      return String(a.v.name || "").localeCompare(String(b.v.name || ""));
    });
  return ranked[0]?.v || null;
}

function pickPreferredVoice(lang: string): SpeechSynthesisVoice | null {
  if (!("speechSynthesis" in window)) return null;
  const voices = window.speechSynthesis.getVoices() || [];
  if (!voices.length) return null;

  const normalizedLang = String(lang || "zh-CN").toLowerCase();
  const langPrefix = normalizedLang.split("-")[0];
  const candidates = voices.filter((v) => {
    const l = String(v.lang || "").toLowerCase();
    return l.startsWith(normalizedLang) || l.startsWith(langPrefix);
  });
  const pool = candidates.length ? candidates : voices;

  const score = (v: SpeechSynthesisVoice): number => {
    const name = String(v.name || "").toLowerCase();
    let s = 0;
    if (name.includes("neural")) s += 12;
    if (name.includes("natural")) s += 8;
    if (name.includes("microsoft")) s += 4;
    if (name.includes("google")) s += 3;
    if (name.includes("edge")) s += 2;
    if (name.includes("xiaoxiao") || name.includes("晓晓")) s += 7;
    if (name.includes("xiaoyi") || name.includes("小艺")) s += 6;
    if (name.includes("yunxi") || name.includes("云希")) s += 5;
    if (name.includes("yunyang") || name.includes("云扬")) s += 5;
    if (name.includes("huihui") || name.includes("慧慧")) s += 4;
    if (name.includes("kangkang") || name.includes("康康")) s += 4;
    if (name.includes("zh-cn") || name.includes("chinese")) s += 3;
    if (String(v.lang || "").toLowerCase() === normalizedLang) s += 3;
    if (v.localService) s += 1;
    if (v.default) s += 2;
    return s;
  };

  return chooseBestVoice(pool, score);
}

function pickStyleVoice(lang: string, style: TTSStyle): SpeechSynthesisVoice | null {
  const base = pickPreferredVoice(lang);
  if (!("speechSynthesis" in window)) return base;
  if (style !== "horror") return base;

  const voices = window.speechSynthesis.getVoices() || [];
  if (!voices.length) return base;
  const normalizedLang = String(lang || "zh-CN").toLowerCase();
  const langPrefix = normalizedLang.split("-")[0];
  const pool = voices.filter((v) => {
    const l = String(v.lang || "").toLowerCase();
    return l.startsWith(normalizedLang) || l.startsWith(langPrefix);
  });
  const candidates = pool.length ? pool : voices;

  const score = (v: SpeechSynthesisVoice): number => {
    const name = `${String(v.name || "")} ${String((v as { voiceURI?: string }).voiceURI || "")}`.toLowerCase();
    let s = 0;
    if (name.includes("neural") || name.includes("natural")) s += 7;
    if (name.includes("male") || name.includes("男")) s += 16;
    if (name.includes("yunxi") || name.includes("云希")) s += 14;
    if (name.includes("yunyang") || name.includes("云扬")) s += 14;
    if (name.includes("yunjian") || name.includes("云健")) s += 14;
    if (name.includes("yunhao") || name.includes("云皓")) s += 14;
    if (name.includes("kangkang") || name.includes("康康")) s += 10;
    if (name.includes("xiaomo") || name.includes("晓墨")) s += 9;
    if (name.includes("xiaorui") || name.includes("晓睿")) s += 9;
    if (name.includes("female") || name.includes("女")) s -= 16;
    if (name.includes("xiaoxiao") || name.includes("晓晓")) s -= 14;
    if (name.includes("xiaoyi") || name.includes("小艺")) s -= 12;
    if (name.includes("child") || name.includes("cute")) s -= 10;
    if (String(v.lang || "").toLowerCase() === normalizedLang) s += 3;
    return s;
  };

  const maleOnly = candidates.filter((v) => score(v) >= 10);
  const poolForHorror = maleOnly.length ? maleOnly : candidates;
  const best = chooseBestVoice(poolForHorror, score);
  if (!best) return base;
  return score(best) > 0 ? best : base;
}

function resolveBaseProsody(style: TTSStyle): Prosody {
  switch (style) {
    case "horror":
      return { rate: 1.19, pitch: 0.64, volume: 0.96 };
    case "ai_long":
      return { rate: 0.95, pitch: 1.0, volume: 1.0 };
    case "news":
      return { rate: 1.12, pitch: 1.03, volume: 1.0 };
    case "market":
      return { rate: 1.08, pitch: 0.98, volume: 1.0 };
    default:
      return { rate: 0.98, pitch: 1.02, volume: 1.0 };
  }
}

function resolveChunkProsody(
  chunk: string,
  index: number,
  total: number,
  base: Prosody,
  style: TTSStyle,
  rateMultiplier = 1
): Prosody {
  const c = String(chunk || "").trim();
  let rate = base.rate;
  let pitch = base.pitch;
  let volume = base.volume;

  if (/[？?]\s*$/.test(c)) {
    pitch += 0.07;
    rate -= 0.03;
  } else if (/[！!]\s*$/.test(c)) {
    pitch += 0.08;
    rate += 0.02;
  } else if (/[；;]\s*$/.test(c)) {
    rate -= 0.03;
  } else if (/[。]\s*$/.test(c)) {
    rate -= 0.01;
  }

  if (style === "horror" && /(黑暗|脚步|低语|门缝|阴冷|诡异|背后|回头|呼吸)/.test(c)) {
    rate += 0.01;
    pitch -= 0.05;
    volume += 0.01;
  }

  if (style === "market" && /([0-9]+(\.[0-9]+)?%|涨|跌|指数|收盘|成交|市值)/.test(c)) {
    rate += 0.02;
    pitch -= 0.02;
  }

  if (index === 0) {
    pitch += 0.02;
  }
  if (index === total - 1) {
    rate -= 0.01;
  }

  // Subtle deterministic variation to reduce robotic monotone.
  const jitter = (((index * 37) % 7) - 3) * 0.005;
  rate += jitter;
  pitch += jitter * 0.7;

  rate *= clampNumber(rateMultiplier, 0.82, 1.28);

  const minRate = style === "horror" ? 0.92 : 0.78;
  const maxRate = style === "horror" ? 1.42 : 1.35;
  const minPitch = style === "horror" ? 0.55 : 0.7;
  const maxPitch = style === "horror" ? 1.22 : 1.35;
  const minVolume = style === "horror" ? 0.82 : 0.8;
  const maxVolume = style === "horror" ? 1.18 : 1.2;

  return {
    rate: clampNumber(rate, minRate, maxRate),
    pitch: clampNumber(pitch, minPitch, maxPitch),
    volume: clampNumber(volume, minVolume, maxVolume),
  };
}

function resolveChunkPauseMs(
  chunk: string,
  style: TTSStyle,
  engine: "browser" | "gpt_sovits_v4" = "browser"
): number {
  const c = String(chunk || "").trim();
  let pause = 90;
  if (/[。！？!?]\s*$/.test(c)) pause = 240;
  else if (/[；;]\s*$/.test(c)) pause = 280;
  else if (/[，,、：:]\s*$/.test(c)) pause = 140;

  if (style === "horror") pause = Math.round(pause * 0.68 + 8);
  if (style === "ai_long") pause += 40;

  if (engine === "gpt_sovits_v4") {
    const factor = style === "horror" ? 0.35 : style === "ai_long" ? 0.24 : 0.18;
    return clampNumber(Math.round(pause * factor), 8, 90);
  }

  return clampNumber(pause, 60, 520);
}

/**
 * TTS hook with chunk splitting and watchdog timer.
 * Returns { speak, cancel, speaking }.
 */
export function useTTS(
  lang = "zh-CN",
  engine: TTSEngine = "browser",
  rateMultiplier = 1
) {
  const [speaking, setSpeaking] = useState(false);
  const [ttsError, setTtsError] = useState(false);
  const [ttsErrorMessage, setTtsErrorMessage] = useState("");
  /** Progress: [current 1-based chunk index, total chunks] */
  const [ttsProgress, setTtsProgress] = useState<[number, number]>([0, 0]);
  const speakingRef = useRef(false);
  const speakSessionRef = useRef(0);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pauseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pauseResolveRef = useRef<(() => void) | null>(null);
  const voiceRef = useRef<SpeechSynthesisVoice | null>(null);
  const horrorVoiceRef = useRef<SpeechSynthesisVoice | null>(null);
  const lastNarrationStyleRef = useRef<TTSStyle | null>(null);
  const lastNarrationAtRef = useRef<number>(0);
  const gptAbortRef = useRef<AbortController | null>(null);
  const gptAudioRef = useRef<HTMLAudioElement | null>(null);
  const gptAudioUrlRef = useRef<string>("");
  const audioCtxRef = useRef<AudioContext | null>(null);
  const horrorAmbientRef = useRef<HorrorAmbientNodes | null>(null);

  useEffect(() => {
    if (!("speechSynthesis" in window)) return;
    const refresh = () => {
      voiceRef.current = pickPreferredVoice(lang);
      horrorVoiceRef.current = pickStyleVoice(lang, "horror");
    };
    refresh();
    window.speechSynthesis.addEventListener("voiceschanged", refresh);
    return () => {
      window.speechSynthesis.removeEventListener("voiceschanged", refresh);
    };
  }, [lang]);

  const clearWatchdog = useCallback(() => {
    if (watchdogRef.current) {
      clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
  }, []);

  const clearPauseTimer = useCallback(() => {
    if (pauseTimerRef.current) {
      clearTimeout(pauseTimerRef.current);
      pauseTimerRef.current = null;
    }
    const resolve = pauseResolveRef.current;
    pauseResolveRef.current = null;
    if (resolve) {
      try {
        resolve();
      } catch {}
    }
  }, []);

  const waitPauseOrCancel = useCallback(
    (ms: number, sessionId: number): Promise<void> =>
      new Promise((resolve) => {
        if (sessionId !== speakSessionRef.current || !speakingRef.current) {
          resolve();
          return;
        }
        clearPauseTimer();
        pauseResolveRef.current = resolve;
        pauseTimerRef.current = window.setTimeout(() => {
          pauseTimerRef.current = null;
          const done = pauseResolveRef.current;
          pauseResolveRef.current = null;
          done?.();
        }, Math.max(0, ms));
      }),
    [clearPauseTimer]
  );

  const abortGptSynthesis = useCallback(() => {
    const ctrl = gptAbortRef.current;
    gptAbortRef.current = null;
    if (ctrl) {
      try {
        ctrl.abort();
      } catch {}
    }
  }, []);

  const stopGptAudio = useCallback(() => {
    const audio = gptAudioRef.current;
    gptAudioRef.current = null;
    if (audio) {
      try {
        audio.pause();
      } catch {}
      try {
        audio.src = "";
        audio.load();
      } catch {}
    }
    const url = gptAudioUrlRef.current;
    gptAudioUrlRef.current = "";
    if (url) {
      try {
        URL.revokeObjectURL(url);
      } catch {}
    }
  }, []);

  const stopHorrorAmbience = useCallback(() => {
    const ambient = horrorAmbientRef.current;
    if (!ambient) return;
    horrorAmbientRef.current = null;

    try {
      window.clearInterval(ambient.heartbeatTimer);
    } catch {}

    const ctx = audioCtxRef.current;
    if (ctx) {
      const now = ctx.currentTime;
      try {
        ambient.master.gain.cancelScheduledValues(now);
        ambient.master.gain.setValueAtTime(Math.max(ambient.master.gain.value, 0.0001), now);
        ambient.master.gain.exponentialRampToValueAtTime(0.0001, now + 0.2);
      } catch {}
    }

    const safeStop = (node: { stop?: (when?: number) => void; disconnect?: () => void }) => {
      try {
        node.stop?.();
      } catch {}
      try {
        node.disconnect?.();
      } catch {}
    };

    setTimeout(() => {
      safeStop(ambient.rumbleOsc);
      safeStop(ambient.noiseSrc);
      safeStop(ambient.rumbleGain);
      safeStop(ambient.noiseGain);
      safeStop(ambient.master);
    }, 260);
  }, []);

  const startHorrorAmbience = useCallback(() => {
    if (horrorAmbientRef.current) return;
    if (typeof window === "undefined") return;

    const Ctx = (window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext) as
      | (new () => AudioContext)
      | undefined;
    if (!Ctx) return;

    let ctx = audioCtxRef.current;
    if (!ctx) {
      try {
        ctx = new Ctx();
      } catch {
        return;
      }
      audioCtxRef.current = ctx;
    }

    // Best effort; if blocked by autoplay policy we just skip ambience.
    ctx.resume().catch(() => {});

    try {
      const master = ctx.createGain();
      master.gain.value = 0.0001;
      master.connect(ctx.destination);
      master.gain.exponentialRampToValueAtTime(0.038, ctx.currentTime + 0.8);

      const rumbleOsc = ctx.createOscillator();
      rumbleOsc.type = "triangle";
      rumbleOsc.frequency.setValueAtTime(43, ctx.currentTime);
      const rumbleGain = ctx.createGain();
      rumbleGain.gain.setValueAtTime(0.016, ctx.currentTime);
      rumbleOsc.connect(rumbleGain).connect(master);
      rumbleOsc.start();

      const noiseSrc = ctx.createBufferSource();
      noiseSrc.buffer = createNoiseBuffer(ctx, 2.2);
      noiseSrc.loop = true;
      const noiseLP = ctx.createBiquadFilter();
      noiseLP.type = "lowpass";
      noiseLP.frequency.setValueAtTime(1200, ctx.currentTime);
      noiseLP.Q.setValueAtTime(0.6, ctx.currentTime);
      const noiseGain = ctx.createGain();
      noiseGain.gain.setValueAtTime(0.009, ctx.currentTime);
      noiseSrc.connect(noiseLP).connect(noiseGain).connect(master);
      noiseSrc.start();

      const thump = (when: number, amp = 0.14) => {
        const beat = ctx!.createOscillator();
        beat.type = "sine";
        beat.frequency.setValueAtTime(82, when);
        const beatGain = ctx!.createGain();
        beatGain.gain.setValueAtTime(0.0001, when);
        beatGain.gain.exponentialRampToValueAtTime(amp, when + 0.012);
        beatGain.gain.exponentialRampToValueAtTime(0.0001, when + 0.17);
        beat.connect(beatGain).connect(master);
        beat.start(when);
        beat.stop(when + 0.2);
      };

      const triggerHeartbeat = () => {
        const t0 = ctx!.currentTime + 0.03;
        thump(t0, 0.11);
        thump(t0 + 0.18, 0.085);
      };

      // Light eerie "music box" notes to add atmosphere without overpowering speech.
      const chime = (when: number, freq: number, amp = 0.018) => {
        const osc = ctx!.createOscillator();
        osc.type = "sine";
        osc.frequency.setValueAtTime(freq, when);
        const g = ctx!.createGain();
        g.gain.setValueAtTime(0.0001, when);
        g.gain.exponentialRampToValueAtTime(amp, when + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, when + 1.0);
        osc.connect(g).connect(master);
        osc.start(when);
        osc.stop(when + 1.05);
      };

      let beatCount = 0;
      triggerHeartbeat();
      const heartbeatTimer = window.setInterval(() => {
        triggerHeartbeat();
        beatCount += 1;
        if (beatCount % 2 === 0) {
          const now = ctx!.currentTime + 0.06;
          chime(now, 294, 0.016); // D4
          chime(now + 0.28, 247, 0.013); // B3
          chime(now + 0.55, 220, 0.012); // A3
        }
      }, 2900);
      horrorAmbientRef.current = { master, rumbleOsc, rumbleGain, noiseSrc, noiseGain, heartbeatTimer };
    } catch {
      stopHorrorAmbience();
    }
  }, [stopHorrorAmbience]);

  useEffect(
    () => () => {
      clearPauseTimer();
      abortGptSynthesis();
      stopGptAudio();
      stopHorrorAmbience();
      const ctx = audioCtxRef.current;
      audioCtxRef.current = null;
      if (ctx) {
        ctx.close().catch(() => {});
      }
    },
    [abortGptSynthesis, clearPauseTimer, stopGptAudio, stopHorrorAmbience]
  );

  const playGptChunkBlob = useCallback(
    (blob: Blob, sessionId: number, timeoutMs: number): Promise<void> =>
      new Promise((resolve, reject) => {
        stopGptAudio();
        const objectUrl = URL.createObjectURL(blob);
        gptAudioUrlRef.current = objectUrl;
        const audio = new Audio(objectUrl);
        audio.preload = "auto";
        gptAudioRef.current = audio;

        let settled = false;
        const settle = (handler?: () => void) => {
          if (settled) return;
          settled = true;
          cleanup();
          handler?.();
        };
        const cleanup = () => {
          try {
            audio.pause();
          } catch {}
          audio.removeEventListener("ended", onEnded);
          audio.removeEventListener("error", onError);
          window.clearInterval(cancelWatch);
          window.clearTimeout(timeoutTimer);
          if (gptAudioRef.current === audio) {
            gptAudioRef.current = null;
          }
          if (gptAudioUrlRef.current === objectUrl) {
            gptAudioUrlRef.current = "";
            try {
              URL.revokeObjectURL(objectUrl);
            } catch {}
          }
        };
        const onEnded = () => settle(resolve);
        const onError = () => settle(() => reject(new Error("GPT-SoVITS 音频播放失败")));
        const cancelWatch = window.setInterval(() => {
          if (sessionId !== speakSessionRef.current) {
            settle(resolve);
          }
        }, 120);
        const timeoutTimer = window.setTimeout(() => {
          settle(() => reject(new Error("GPT-SoVITS 音频播放超时")));
        }, Math.max(4000, timeoutMs));

        audio.addEventListener("ended", onEnded);
        audio.addEventListener("error", onError);
        const playPromise = audio.play();
        if (playPromise && typeof playPromise.catch === "function") {
          playPromise.catch((err: unknown) => {
            if (sessionId !== speakSessionRef.current) {
              settle(resolve);
              return;
            }
            settle(() => reject(err instanceof Error ? err : new Error("GPT-SoVITS 播放被浏览器阻止")));
          });
        }
      }),
    [stopGptAudio]
  );

  const speak = useCallback(
    (text: string, onDone?: () => void) => {
      const input = String(text || "").trim();
      if (!input) {
        onDone?.();
        return;
      }
      const configuredEngine = normalizeTTSEngine(engine);

      const sessionId = speakSessionRef.current + 1;
      speakSessionRef.current = sessionId;
      clearWatchdog();
      clearPauseTimer();
      abortGptSynthesis();
      stopGptAudio();
      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }

      const detectedStyle = detectStyle(input);
      let style = detectedStyle;
      // Keep long-form narration style stable across prefix-less continuation chunks.
      if (style === "general") {
        const carry = lastNarrationStyleRef.current;
        const recentlyNarrated = Date.now() - lastNarrationAtRef.current < 180000;
        if (carry && recentlyNarrated && input.length >= 70) {
          style = carry;
        }
      }
      if (style === "horror" || style === "ai_long") {
        lastNarrationStyleRef.current = style;
        lastNarrationAtRef.current = Date.now();
      } else {
        lastNarrationStyleRef.current = null;
      }
      // Keep briefings on browser voice for continuity and low latency.
      const engineMode: "browser" | "gpt_sovits_v4" =
        style === "news" || style === "market" ? "browser" : configuredEngine;
      if (engineMode === "browser" && !("speechSynthesis" in window)) {
        onDone?.();
        return;
      }
      const maxChunkLen =
        engineMode === "gpt_sovits_v4"
          ? style === "horror"
            ? 88
            : style === "ai_long"
              ? 160
              : style === "news" || style === "market"
                ? 72
              : 150
          : style === "horror"
            ? 74
            : 140;
      let chunks = splitForTTS(input, maxChunkLen);
      if (engineMode === "gpt_sovits_v4" && (style === "horror" || style === "ai_long")) {
        const firstChunkMax = style === "horror" ? 48 : 56;
        chunks = splitFirstChunkForFastStart(chunks, firstChunkMax);
      }
      const safeRateMultiplier = clampNumber(Number(rateMultiplier) || 1, 0.82, 1.28);
      const baseProsody = resolveBaseProsody(style);
      const resolvedHorrorVoice =
        pickStyleVoice(lang, "horror") || horrorVoiceRef.current || voiceRef.current;
      if (resolvedHorrorVoice) {
        horrorVoiceRef.current = resolvedHorrorVoice;
      }
      const styleVoice = style === "horror" ? resolvedHorrorVoice : voiceRef.current;
      if (!chunks.length) {
        onDone?.();
        return;
      }

      if (style === "horror") {
        startHorrorAmbience();
      } else {
        stopHorrorAmbience();
      }

      setSpeaking(true);
      setTtsError(false);
      setTtsErrorMessage("");
      setTtsProgress([0, chunks.length]);
      speakingRef.current = true;

      const resetAfterSpeak = () => {
        clearWatchdog();
        clearPauseTimer();
        abortGptSynthesis();
        stopGptAudio();
        speakingRef.current = false;
        setSpeaking(false);
        setTtsProgress([0, 0]);
        stopHorrorAmbience();
        onDone?.();
      };

      const isCancelled = () => sessionId !== speakSessionRef.current;

      const runBrowserFrom = (startIndex = 0) => {
        let i = Math.max(0, Math.min(startIndex, chunks.length));
        const armChunkWatchdog = (
          chunk: string,
          chunkStyle: TTSStyle,
          rate: number
        ) => {
          clearWatchdog();
          watchdogRef.current = setTimeout(() => {
            if (speakingRef.current && !isCancelled()) {
              window.speechSynthesis.cancel();
              setTtsError(true);
              setTtsErrorMessage("播报超时已停止");
              setTimeout(() => setTtsError(false), 5000); // auto-dismiss
              resetAfterSpeak();
            }
          }, chunkWatchdogMs(chunk, chunkStyle, rate));
        };
        const speakNext = () => {
          if (isCancelled() || !speakingRef.current) {
            return;
          }
          if (i >= chunks.length) {
            resetAfterSpeak();
            return;
          }
          const chunk = chunks[i];
          const u = new SpeechSynthesisUtterance(chunk);
          u.lang = lang;
          if (styleVoice) {
            u.voice = styleVoice;
          }
          const chunkProsody = resolveChunkProsody(
            chunk,
            i,
            chunks.length,
            baseProsody,
            style,
            safeRateMultiplier
          );
          u.rate = chunkProsody.rate;
          u.pitch = chunkProsody.pitch;
          u.volume = chunkProsody.volume;
          i++;
          setTtsProgress([i, chunks.length]);
          armChunkWatchdog(chunk, style, chunkProsody.rate);
          u.onend = () => {
            clearWatchdog();
            clearPauseTimer();
            pauseTimerRef.current = window.setTimeout(() => {
              pauseTimerRef.current = null;
              if (isCancelled() || !speakingRef.current) {
                return;
              }
              speakNext();
            }, resolveChunkPauseMs(chunk, style));
          };
          u.onerror = () => {
            clearWatchdog();
            if (isCancelled() || !speakingRef.current) {
              return;
            }
            speakNext();
          };
          window.speechSynthesis.speak(u);
        };
        speakNext();
      };

      if (engineMode === "gpt_sovits_v4") {
        const run = async () => {
          type GptChunkResult = {
            chunk: string;
            chunkProsody: Prosody;
            blob: Blob;
          };
          const synthChunk = async (index: number): Promise<GptChunkResult> => {
            const chunk = chunks[index];
            const chunkProsody = resolveChunkProsody(
              chunk,
              index,
              chunks.length,
              baseProsody,
              style,
              safeRateMultiplier
            );
            const maxBusyRetries = style === "ai_long" ? 12 : 6;
            for (let attempt = 0; attempt <= maxBusyRetries; attempt++) {
              const synthTimeoutMs = Math.max(
                22000,
                chunkWatchdogMs(chunk, style, chunkProsody.rate) + 10000
              );
              const controller = new AbortController();
              gptAbortRef.current = controller;
              const timeoutId = window.setTimeout(() => {
                try {
                  controller.abort();
                } catch {}
              }, synthTimeoutMs);
              try {
                const synthResp = await api.synthesizeTTSAudio(
                  {
                    engine: "gpt_sovits_v4",
                    text: chunk,
                    style,
                    lang,
                    rate: chunkProsody.rate,
                    pitch: chunkProsody.pitch,
                    volume: chunkProsody.volume,
                  },
                  controller.signal
                );
                if (synthResp.ok) {
                  return { chunk, chunkProsody, blob: synthResp.blob };
                }
                const code = String(synthResp.error.code || "").trim().toLowerCase();
                const retryableBusy = code === "tts_busy" && attempt < maxBusyRetries;
                const retryableUpstream =
                  (code === "tts_proxy_error" || code === "tts_upstream_http_error") &&
                  attempt < 2;
                if (retryableBusy || retryableUpstream) {
                  const backoff = retryableBusy ? 600 : 450 + attempt * 300;
                  await waitPauseOrCancel(backoff, sessionId);
                  continue;
                }
                throw new Error(synthResp.error.message || "GPT-SoVITS 合成失败");
              } finally {
                window.clearTimeout(timeoutId);
                if (gptAbortRef.current === controller) {
                  gptAbortRef.current = null;
                }
              }
            }
            throw new Error("GPT-SoVITS 忙碌，请稍后重试");
          };

          try {
            // AI long-form: pre-synthesize all chunks first, then play continuously.
            if (style === "ai_long") {
              const prepared: GptChunkResult[] = [];
              for (let i = 0; i < chunks.length; i++) {
                if (isCancelled()) break;
                setTtsProgress([i + 1, chunks.length]);
                prepared.push(await synthChunk(i));
              }
              if (isCancelled()) return;
              for (let i = 0; i < prepared.length; i++) {
                if (isCancelled()) break;
                const item = prepared[i];
                setTtsProgress([i + 1, prepared.length]);
                const playTimeout = Math.max(
                  14000,
                  chunkWatchdogMs(item.chunk, style, item.chunkProsody.rate) + 7000
                );
                await playGptChunkBlob(item.blob, sessionId, playTimeout);
                if (isCancelled()) break;
                await waitPauseOrCancel(
                  resolveChunkPauseMs(item.chunk, style, "gpt_sovits_v4"),
                  sessionId
                );
              }
              if (!isCancelled()) {
                resetAfterSpeak();
              }
              return;
            }

            for (let i = 0; i < chunks.length; i++) {
              if (isCancelled()) break;
              setTtsProgress([i + 1, chunks.length]);
              const { chunk, chunkProsody, blob } = await synthChunk(i);
              if (isCancelled()) break;

              const playTimeout = Math.max(
                14000,
                chunkWatchdogMs(chunk, style, chunkProsody.rate) + 7000
              );
              await playGptChunkBlob(blob, sessionId, playTimeout);
              if (isCancelled()) break;
              await waitPauseOrCancel(
                resolveChunkPauseMs(chunk, style, "gpt_sovits_v4"),
                sessionId
              );
            }
            if (!isCancelled()) {
              resetAfterSpeak();
            }
          } catch (err: unknown) {
            abortGptSynthesis();
            if (isCancelled() || isAbortError(err)) {
              return;
            }
            const canFallbackToBrowser =
              configuredEngine === "gpt_sovits_v4" && "speechSynthesis" in window;
            if (canFallbackToBrowser) {
              setTtsError(true);
              setTtsErrorMessage("GPT-SoVITS 暂不可用，已自动回退到浏览器语音");
              window.setTimeout(() => setTtsError(false), 5000);
              runBrowserFrom(0);
              return;
            }
            setTtsError(true);
            setTtsErrorMessage(err instanceof Error ? err.message : "GPT-SoVITS 播报失败");
            window.setTimeout(() => setTtsError(false), 5000);
            resetAfterSpeak();
          }
        };
        void run();
        return;
      }
      runBrowserFrom(0);
    },
    [
      abortGptSynthesis,
      clearPauseTimer,
      clearWatchdog,
      engine,
      lang,
      playGptChunkBlob,
      rateMultiplier,
      startHorrorAmbience,
      stopGptAudio,
      stopHorrorAmbience,
      waitPauseOrCancel,
    ]
  );

  const cancel = useCallback(() => {
    speakSessionRef.current += 1;
    clearWatchdog();
    clearPauseTimer();
    abortGptSynthesis();
    stopGptAudio();
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    stopHorrorAmbience();
    speakingRef.current = false;
    setSpeaking(false);
    setTtsProgress([0, 0]);
  }, [abortGptSynthesis, clearPauseTimer, clearWatchdog, stopGptAudio, stopHorrorAmbience]);

  return { speak, cancel, speaking, ttsError, ttsErrorMessage, ttsProgress };
}
