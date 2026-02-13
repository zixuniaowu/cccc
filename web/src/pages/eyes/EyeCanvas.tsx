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
 * Smooth spring interpolation for blink, pupil position, ambient, and mood color.
 */

/** Parse hex color to [r, g, b] */
function hexToRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

export function EyeCanvas({ mood, blink, pupilOffset, ambient }: EyeCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const initColor = hexToRgb(MOOD_COLOR.idle);
  const animRef = useRef({
    blink: 0,
    pupilX: 0,
    pupilY: 0,
    ambient: 0.6,
    colorR: initColor[0],
    colorG: initColor[1],
    colorB: initColor[2],
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

    // Cache DPR — only update on resize
    let cachedDpr = window.devicePixelRatio || 1;
    const onResize = () => { cachedDpr = window.devicePixelRatio || 1; };
    window.addEventListener("resize", onResize);

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
      anim.pupilX += (props.pupilOffset.x - anim.pupilX) * 0.35;
      anim.pupilY += (props.pupilOffset.y - anim.pupilY) * 0.35;
      anim.ambient += (props.ambient - anim.ambient) * 0.08;

      // Spring-interpolate mood color for smooth iris transitions
      const targetColor = hexToRgb(MOOD_COLOR[props.mood]);
      anim.colorR += (targetColor[0] - anim.colorR) * 0.08;
      anim.colorG += (targetColor[1] - anim.colorG) * 0.08;
      anim.colorB += (targetColor[2] - anim.colorB) * 0.08;

      // Match canvas resolution to display size
      const rect = canvas.getBoundingClientRect();
      const w = Math.round(rect.width * cachedDpr);
      const h = Math.round(rect.height * cachedDpr);
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
        moodColorRgb: [anim.colorR, anim.colorG, anim.colorB],
      });

      rafId = requestAnimationFrame(render);
    };

    rafId = requestAnimationFrame(render);
    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", onResize);
    };
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
