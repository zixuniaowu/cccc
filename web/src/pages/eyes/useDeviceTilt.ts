import { useEffect, useRef, useState } from "react";
import { clamp } from "./constants";

interface TiltVec {
  x: number; // -1 … 1 (left … right)
  y: number; // -1 … 1 (up … down)
}

const ZERO: TiltVec = { x: 0, y: 0 };

/**
 * Maps device orientation (gyroscope) to a normalized {x, y} vector.
 * - gamma → left/right tilt → x axis
 * - beta  → forward/back tilt → y axis
 *
 * Only activates when `enabled` is true.
 * On iOS 13+, caller must first request DeviceOrientationEvent.requestPermission().
 */
export function useDeviceTilt(enabled: boolean): TiltVec {
  const [vec, setVec] = useState<TiltVec>(ZERO);
  const rafRef = useRef<number>();
  const latestRef = useRef<TiltVec>(ZERO);

  useEffect(() => {
    if (!enabled) {
      setVec(ZERO);
      return;
    }

    const handler = (e: DeviceOrientationEvent) => {
      const gamma = e.gamma ?? 0; // -90 … 90 (left-right)
      const beta = e.beta ?? 0;   // -180 … 180 (front-back)

      // Map to -1…1 range, with ±30° as full deflection
      latestRef.current = {
        x: clamp(gamma / 30, -1, 1),
        y: clamp((beta - 45) / 30, -1, 1), // 45° is neutral holding angle
      };
    };

    // Throttle to ~30fps via rAF to avoid excessive re-renders
    let running = true;
    const tick = () => {
      if (!running) return;
      const cur = latestRef.current;
      setVec((prev) => {
        // Simple low-pass filter for smoothness
        const nx = prev.x + (cur.x - prev.x) * 0.15;
        const ny = prev.y + (cur.y - prev.y) * 0.15;
        if (Math.abs(nx - prev.x) < 0.001 && Math.abs(ny - prev.y) < 0.001) {
          return prev;
        }
        return { x: nx, y: ny };
      });
      rafRef.current = requestAnimationFrame(tick);
    };

    window.addEventListener("deviceorientation", handler);
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      running = false;
      window.removeEventListener("deviceorientation", handler);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [enabled]);

  return vec;
}
