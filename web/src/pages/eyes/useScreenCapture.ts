import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../../services/api";

interface UseScreenCaptureOptions {
  groupId: string | null;
  /** Capture interval in seconds (default 30) */
  intervalSec?: number;
  /** JPEG quality 0-1 (default 0.6) */
  jpegQuality?: number;
  /** Max image dimension in pixels (default 1280) */
  maxDimension?: number;
  /** Custom AI analysis prompt */
  prompt?: string;
}

/**
 * Desktop screen capture hook.
 * Uses getDisplayMedia to grab a screen stream, periodically captures a JPEG frame,
 * and uploads it to the CCCC group for the AI agent to analyze.
 */
export function useScreenCapture(opts: UseScreenCaptureOptions) {
  const defaultPrompt = `[自动截屏] 这是用户当前桌面截图。如有有趣的内容或建议请简短评论。无特别发现则回复"无特别发现"。`;
  const {
    groupId,
    intervalSec = 30,
    jpegQuality = 0.6,
    maxDimension = 1280,
    prompt = defaultPrompt,
  } = opts;

  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastCaptureTs, setLastCaptureTs] = useState<number | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Lazy canvas creation (off-screen)
  const getCanvas = () => {
    if (!canvasRef.current) {
      canvasRef.current = document.createElement("canvas");
    }
    return canvasRef.current;
  };

  const captureFrame = useCallback(async () => {
    const stream = streamRef.current;
    if (!stream || !groupId) return;

    const track = stream.getVideoTracks()[0];
    if (!track || track.readyState !== "live") {
      // Stream ended (user stopped sharing)
      stop();
      return;
    }

    try {
      const canvas = getCanvas();
      const video = document.createElement("video");
      video.srcObject = stream;
      video.muted = true;

      await new Promise<void>((resolve) => {
        video.onloadeddata = () => resolve();
        video.play().catch(() => resolve());
      });

      // Scale down to maxDimension while preserving aspect ratio
      let w = video.videoWidth;
      let h = video.videoHeight;
      if (w > maxDimension || h > maxDimension) {
        const scale = maxDimension / Math.max(w, h);
        w = Math.round(w * scale);
        h = Math.round(h * scale);
      }

      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d") as CanvasRenderingContext2D | null;
      if (!ctx) return;

      ctx.drawImage(video, 0, 0, w, h);
      video.srcObject = null;

      // Convert to JPEG blob
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob((b) => resolve(b), "image/jpeg", jpegQuality)
      );
      if (!blob) return;

      // Only upload if under 500KB (skip if somehow huge)
      if (blob.size > 500_000) {
        console.warn("[screen-capture] frame too large, skipping:", blob.size);
        return;
      }

      const file = new File(
        [blob],
        `screen_${Date.now()}.jpg`,
        { type: "image/jpeg" }
      );

      await api.sendMessage(groupId, prompt, [], [file]);
      setLastCaptureTs(Date.now());
    } catch (e) {
      console.error("[screen-capture] frame error:", e);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId, jpegQuality, maxDimension, prompt]);

  const start = useCallback(async () => {
    if (capturing) return;
    setError(null);

    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 1 }, // Low framerate — we only need stills
        audio: false,
      });

      streamRef.current = stream;
      setCapturing(true);

      // Listen for user stopping share via browser UI
      stream.getVideoTracks()[0]?.addEventListener("ended", () => {
        stop();
      });

      // Capture first frame immediately
      setTimeout(() => void captureFrame(), 2000); // Small delay for stream init

      // Then interval
      timerRef.current = setInterval(() => {
        void captureFrame();
      }, intervalSec * 1000);
    } catch (e: any) {
      if (e?.name === "NotAllowedError") {
        setError("用户取消了屏幕共享");
      } else {
        setError(e?.message || "无法启动屏幕捕获");
      }
      setCapturing(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capturing, captureFrame, intervalSec]);

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setCapturing(false);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);

  return { capturing, error, start, stop, lastCaptureTs };
}
