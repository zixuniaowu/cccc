import { useEffect, useMemo, useState } from "react";
import type { Mood } from "./types";

/** Natural blink patterns: single, double, with varied timing */
export function useBlink() {
  const [blink, setBlink] = useState(false);
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    const doBlink = () => {
      const dur = 120 + Math.random() * 60;
      setBlink(true);
      setTimeout(() => {
        setBlink(false);
        // 25% chance of double-blink
        if (Math.random() < 0.25) {
          setTimeout(() => {
            setBlink(true);
            setTimeout(() => setBlink(false), 100 + Math.random() * 50);
          }, 160);
        }
      }, dur);
      timeout = setTimeout(doBlink, 2000 + Math.random() * 4000);
    };
    timeout = setTimeout(doBlink, 1200 + Math.random() * 2000);
    return () => clearTimeout(timeout);
  }, []);
  return blink;
}

/** Idle drift: gentle sine-based wandering gaze */
export function useIdleDrift() {
  const [drift, setDrift] = useState({ x: 0, y: 0 });
  useEffect(() => {
    const timer = setInterval(() => {
      const t = performance.now() / 1000;
      setDrift({
        x: Math.sin(t * 0.7) * 0.2 + Math.sin(t * 1.3) * 0.1,
        y: Math.cos(t * 0.5) * 0.15 + Math.sin(t * 1.1 + 2) * 0.08,
      });
    }, 110);
    return () => clearInterval(timer);
  }, []);
  return drift;
}

/** Micro-saccades: tiny rapid eye jumps (real eyes do this) */
export function useSaccade() {
  const [saccade, setSaccade] = useState({ x: 0, y: 0 });
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    const fire = () => {
      setSaccade({
        x: (Math.random() - 0.5) * 0.15,
        y: (Math.random() - 0.5) * 0.1,
      });
      setTimeout(() => setSaccade({ x: 0, y: 0 }), 90);
      timeout = setTimeout(fire, 1800 + Math.random() * 3200);
    };
    timeout = setTimeout(fire, 800 + Math.random() * 1500);
    return () => clearTimeout(timeout);
  }, []);
  return saccade;
}

/** Autonomous gaze shifts: intentional "glances" to random directions */
export function useGazeShift() {
  const [shift, setShift] = useState({ x: 0, y: 0 });
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    const doShift = () => {
      setShift({
        x: (Math.random() - 0.5) * 0.6,
        y: (Math.random() - 0.5) * 0.4,
      });
      // Hold the glance for 0.8-2s then return
      setTimeout(() => setShift({ x: 0, y: 0 }), 800 + Math.random() * 1200);
      timeout = setTimeout(doShift, 4000 + Math.random() * 7000);
    };
    timeout = setTimeout(doShift, 2500 + Math.random() * 3500);
    return () => clearTimeout(timeout);
  }, []);
  return shift;
}

/** Mood-driven gaze bias (thinking -> up-left, listening -> center-up, etc.) */
export function useMoodOffset(mood: Mood) {
  return useMemo(() => {
    switch (mood) {
      case "thinking":
        return { x: -0.18, y: -0.28 };
      case "listening":
        return { x: 0, y: -0.08 };
      case "speaking":
        return { x: 0.06, y: 0.02 };
      case "error":
        return { x: 0, y: 0.15 };
      default:
        return { x: 0, y: 0 };
    }
  }, [mood]);
}
