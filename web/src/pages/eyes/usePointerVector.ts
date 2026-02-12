import { useEffect, useState } from "react";
import { clamp } from "./constants";

/** Tracks mouse/touch position and device orientation as a normalized {x, y} vector in [-1, 1]. */
export function usePointerVector() {
  const [vec, setVec] = useState({ x: 0, y: 0 });

  // Full-window mouse / touch tracking
  useEffect(() => {
    const handleMove = (ev: MouseEvent | TouchEvent) => {
      const point = "touches" in ev ? ev.touches[0] : ev;
      if (!point) return;
      const nx = clamp((point.clientX / window.innerWidth) * 2 - 1, -1, 1);
      const ny = clamp((point.clientY / window.innerHeight) * 2 - 1, -1, 1);
      setVec({ x: nx, y: ny });
    };
    window.addEventListener("mousemove", handleMove, { passive: true });
    window.addEventListener("touchmove", handleMove, { passive: true });
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("touchmove", handleMove);
    };
  }, []);

  // Device orientation (mobile tilt)
  useEffect(() => {
    const handle = (e: DeviceOrientationEvent) => {
      const gamma = clamp((e.gamma ?? 0) / 45, -1, 1);
      const beta = clamp(((e.beta ?? 0) - 45) / 45, -1, 1);
      setVec((prev) => ({
        x: clamp(prev.x * 0.6 + gamma * 0.4, -1, 1),
        y: clamp(prev.y * 0.6 + beta * 0.4, -1, 1),
      }));
    };
    window.addEventListener("deviceorientation", handle);
    return () => window.removeEventListener("deviceorientation", handle);
  }, []);

  return vec;
}
