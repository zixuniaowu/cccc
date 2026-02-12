import type { Mood } from "./types";

export const MOOD_COLOR: Record<Mood, string> = {
  idle: "#38bdf8", // sky-400
  listening: "#22c55e", // green-500
  thinking: "#f59e0b", // amber-500
  speaking: "#a855f7", // violet-500
  error: "#ef4444", // red-500
};

export const clamp = (v: number, min: number, max: number) =>
  Math.min(Math.max(v, min), max);

export const IS_MOBILE = /Android|iPhone|iPad|iPod/i.test(
  navigator.userAgent || ""
);

/** Messages matching these prefixes are "thinking" indicators — don't TTS them */
export const THINKING_PREFIXES = ["正在思考", "仍在处理"];

/** Agent reply that means "nothing interesting on screen" — don't TTS or log */
export const SCREEN_CAPTURE_NOOP = "无特别发现";

/** News briefing prefixes — auto-TTS even if user isn't actively chatting */
export const NEWS_PREFIXES = ["[新闻简报]", "[早间简报]"];
