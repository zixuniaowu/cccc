import React, { useEffect, useRef } from "react";
import type { Mood } from "./types";
import { MOOD_COLOR } from "./constants";
import { drawEye } from "./drawEye";

interface EyeCanvasProps {
  mood: Mood;
  blink: boolean;
  pupilOffset: { x: number; y: number };
  ambient: number;
}

/**
 * Canvas2D eye renderer — replaces the CSS-based Eye component.
 * Smooth spring interpolation for blink, pupil position, and ambient.
 */
export function EyeCanvas({ mood, blink, pupilOffset, ambient }: EyeCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef({
    blink: 0,
    pupilX: 0,
    pupilY: 0,
    ambient: 0.6,
  });
  // Store latest props in ref so the rAF loop always sees them
  const propsRef = useRef({ mood, blink, pupilOffset, ambient });
  propsRef.current = { mood, blink, pupilOffset, ambient };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let rafId: number;
    const startTime = performance.now() / 1000;

    const render = () => {
      const now = performance.now() / 1000;
      const t = now - startTime;
      const anim = animRef.current;
      const props = propsRef.current;

      // Spring interpolation (fast for blink, smooth for pupil)
      anim.blink += ((props.blink ? 1 : 0) - anim.blink) * 0.28;
      if (Math.abs(anim.blink - (props.blink ? 1 : 0)) < 0.005) {
        anim.blink = props.blink ? 1 : 0;
      }
      anim.pupilX += (props.pupilOffset.x - anim.pupilX) * 0.15;
      anim.pupilY += (props.pupilOffset.y - anim.pupilY) * 0.15;
      anim.ambient += (props.ambient - anim.ambient) * 0.08;

      // Match canvas resolution to display size
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const w = Math.round(rect.width * dpr);
      const h = Math.round(rect.height * dpr);
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }

      ctx.clearRect(0, 0, w, h);

      drawEye({
        ctx,
        cx: w / 2,
        cy: h / 2,
        radius: Math.min(w, h) * 0.46,
        pupilOffset: { x: anim.pupilX, y: anim.pupilY },
        blink: anim.blink,
        ambient: anim.ambient,
        mood: props.mood,
        t,
      });

      rafId = requestAnimationFrame(render);
    };

    rafId = requestAnimationFrame(render);
    return () => cancelAnimationFrame(rafId);
  }, []);

  return (
    <div
      className="eye-shell"
      data-mood={mood}
      style={{ "--eye-accent": MOOD_COLOR[mood] } as React.CSSProperties}
    >
      <canvas
        ref={canvasRef}
        style={{
          width: "100%",
          height: "100%",
          borderRadius: "inherit",
          display: "block",
        }}
      />
    </div>
  );
}
