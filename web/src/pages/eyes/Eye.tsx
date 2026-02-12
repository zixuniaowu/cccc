import React from "react";
import type { Mood } from "./types";
import { MOOD_COLOR, clamp } from "./constants";

interface EyeProps {
  mood: Mood;
  blink: boolean;
  pupilOffset: { x: number; y: number };
  ambient: number;
}

export function Eye({ mood, blink, pupilOffset, ambient }: EyeProps) {
  const pupilScale = 1 + clamp(0.35 - ambient * 0.35, 0, 0.35);
  return (
    <div
      className="eye-shell"
      data-mood={mood}
      data-blink={blink ? "1" : "0"}
      style={{ "--eye-accent": MOOD_COLOR[mood] } as React.CSSProperties}
    >
      <div className="eye-white">
        <div className="eye-lid eye-lid-top" />
        <div className="eye-lid eye-lid-bottom" />
        <div
          className="eye-pupil"
          style={{
            transform: `translate(${pupilOffset.x * 32}px, ${pupilOffset.y * 28}px) scale(${1 + pupilScale * 0.2})`,
          }}
        />
      </div>
    </div>
  );
}
