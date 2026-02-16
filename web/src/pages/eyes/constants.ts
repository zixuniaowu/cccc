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

/** Parallax factor for the right eye (slight offset for depth effect) */
export const RIGHT_EYE_PARALLAX = 0.9;

export const IS_MOBILE = /Android|iPhone|iPad|iPod/i.test(
  navigator.userAgent || ""
);

/** Messages matching these prefixes are "thinking" indicators — don't TTS them */
export const THINKING_PREFIXES = ["正在思考", "仍在处理"];

/** Agent reply that means "nothing interesting on screen" — don't TTS or log */
export const SCREEN_CAPTURE_NOOP = "无特别发现";

/** News briefing prefixes — auto-TTS even if user isn't actively chatting */
export const NEWS_PREFIXES = [
  "[新闻简报]",
  "[早间简报]",
  "[股市简报]",
  "[AI新技术说明]",
  "[AI长文说明]",
  "[恐怖故事]",
];

/** Whether the page is opened via localhost */
export const IS_LOCALHOST =
  typeof window !== "undefined" &&
  (window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1");

const LAN_IP_KEY = "cccc_lan_ip";

/** Get the saved LAN IP (defaults to empty) */
export function getLanIp(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(LAN_IP_KEY) || "";
}

/** Persist LAN IP to localStorage */
export function setLanIp(ip: string): void {
  localStorage.setItem(LAN_IP_KEY, ip.trim());
}

/**
 * Build a share URL that works across devices on the same LAN.
 * When the page is opened via localhost, replaces hostname with saved LAN IP.
 */
export function buildShareUrl(groupId: string): string {
  const { protocol, port, pathname } = window.location;
  if (!IS_LOCALHOST) {
    return `${window.location.origin}${pathname}?group=${groupId}`;
  }
  const lanIp = getLanIp();
  if (!lanIp) return ""; // No LAN IP configured yet
  const host = lanIp + (port ? `:${port}` : "");
  return `${protocol}//${host}${pathname}?group=${groupId}`;
}
