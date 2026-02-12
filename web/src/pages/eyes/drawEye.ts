import type { Mood } from "./types";
import { MOOD_COLOR } from "./constants";

export interface DrawEyeParams {
  ctx: CanvasRenderingContext2D;
  cx: number;
  cy: number;
  radius: number;
  pupilOffset: { x: number; y: number }; // normalized [-1, 1]
  blink: number; // 0 = open, 1 = closed (smooth)
  ambient: number; // 0 = dark, 1 = bright
  mood: Mood;
  t: number; // elapsed seconds for procedural animation
}

// ── Mood-dependent parameters ──

/** Lid positions: 0 = fully open, higher = more closed */
const LID_PRESETS: Record<Mood, { top: number; bottom: number }> = {
  idle: { top: 0.18, bottom: 0.12 },
  listening: { top: 0.05, bottom: 0.05 }, // wide open, attentive
  thinking: { top: 0.30, bottom: 0.20 }, // squinting, focused
  speaking: { top: 0.12, bottom: 0.08 },
  error: { top: 0.35, bottom: 0.15 }, // droopy, tired
};

/** Pupil size multiplier per mood (base is 0.48, modified by ambient) */
const PUPIL_SCALE: Record<Mood, number> = {
  idle: 1.0,
  listening: 1.25, // dilated — attentive, interested
  thinking: 0.75, // constricted — focused concentration
  speaking: 1.1,  // slightly dilated — engaged
  error: 1.35,    // very dilated — surprise/alarm
};

/** Iris inner glow intensity per mood */
const IRIS_GLOW: Record<Mood, number> = {
  idle: 0,
  listening: 0.15,
  thinking: 0.25,
  speaking: 0.35, // noticeable glow when speaking
  error: 0.1,
};

/** Sclera redness per mood (0 = clean white, 1 = very red) */
const SCLERA_REDNESS: Record<Mood, number> = {
  idle: 0.06,
  listening: 0.04,
  thinking: 0.08,
  speaking: 0.05,
  error: 0.2, // bloodshot when error
};

/** Lid curve asymmetry — inner vs outer corner height difference */
const LID_EXPRESSIVENESS: Record<Mood, { innerLift: number; outerLift: number }> = {
  idle: { innerLift: 0, outerLift: 0 },
  listening: { innerLift: 0.04, outerLift: 0.02 }, // eyebrows up — attentive
  thinking: { innerLift: -0.03, outerLift: 0.06 }, // inner down, outer up — skeptical/focused
  speaking: { innerLift: 0.02, outerLift: 0.01 },
  error: { innerLift: 0.05, outerLift: -0.04 }, // worried look
};

// ── Color utilities ──

function darken(hex: string, factor: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgb(${Math.round(r * (1 - factor))},${Math.round(g * (1 - factor))},${Math.round(b * (1 - factor))})`;
}

function lighten(hex: string, factor: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgb(${Math.round(r + (255 - r) * factor)},${Math.round(g + (255 - g) * factor)},${Math.round(b + (255 - b) * factor)})`;
}

function hexToRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

function rgbToStr(r: number, g: number, b: number, a = 1): string {
  return a < 1
    ? `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${a})`
    : `rgb(${Math.round(r)},${Math.round(g)},${Math.round(b)})`;
}

// ── Main draw function ──

export function drawEye(p: DrawEyeParams) {
  const { ctx, cx, cy, radius: r, pupilOffset, blink, ambient, mood, t } = p;
  const lidPreset = LID_PRESETS[mood];
  const moodColor = MOOD_COLOR[mood];
  const [mR, mG, mB] = hexToRgb(moodColor);
  const pupilMult = PUPIL_SCALE[mood];
  const glowIntensity = IRIS_GLOW[mood];
  const redness = SCLERA_REDNESS[mood];
  const lidExpr = LID_EXPRESSIVENESS[mood];

  // Effective lid coverage: mood base + blink closes the rest
  const topCoverage = lidPreset.top + blink * (0.52 - lidPreset.top);
  const bottomCoverage = lidPreset.bottom + blink * (0.52 - lidPreset.bottom);

  ctx.save();

  // ── 1. Eye socket shadow ──
  const socketGrad = ctx.createRadialGradient(cx, cy, r * 0.4, cx, cy, r * 1.05);
  socketGrad.addColorStop(0, "rgba(15, 15, 25, 0)");
  socketGrad.addColorStop(0.65, "rgba(10, 10, 20, 0.08)");
  socketGrad.addColorStop(1, "rgba(5, 5, 15, 0.35)");
  ctx.fillStyle = socketGrad;
  ctx.beginPath();
  ctx.arc(cx, cy, r * 1.05, 0, Math.PI * 2);
  ctx.fill();

  // ── 2. Sclera (eye white) ──
  const scleraRx = r * 0.88;
  const scleraRy = r * 0.72;

  ctx.save();
  ctx.beginPath();
  ctx.ellipse(cx, cy, scleraRx, scleraRy, 0, 0, Math.PI * 2);
  ctx.clip();

  // Sclera gradient — tinted with redness for error mood
  const scleraGrad = ctx.createRadialGradient(
    cx - r * 0.12, cy - r * 0.1, 0,
    cx, cy, scleraRx
  );
  const sR = Math.round(242 + (255 - 242) * redness * 2);
  const sG = Math.round(242 - 40 * redness);
  const sB = Math.round(245 - 50 * redness);
  scleraGrad.addColorStop(0, rgbToStr(sR, sG, sB));
  scleraGrad.addColorStop(0.5, rgbToStr(sR - 8, sG - 8, sB - 6));
  scleraGrad.addColorStop(0.8, rgbToStr(sR - 26, sG - 26, sB - 21));
  scleraGrad.addColorStop(1, rgbToStr(sR - 58, sG - 58, sB - 45));
  ctx.fillStyle = scleraGrad;
  ctx.fill();

  // Blood vessels — more visible in error mood
  const vesselAlpha = 0.04 + redness * 0.6;
  const vesselCount = mood === "error" ? 10 : 6;
  ctx.globalAlpha = vesselAlpha;
  ctx.strokeStyle = mood === "error" ? "#d04040" : "#c06060";
  ctx.lineWidth = mood === "error" ? 1.0 : 0.7;
  for (let i = 0; i < vesselCount; i++) {
    const angle = (i / vesselCount) * Math.PI * 2 + t * 0.005;
    const sr = scleraRx * 0.45;
    const er = scleraRx * 0.95;
    ctx.beginPath();
    ctx.moveTo(
      cx + Math.cos(angle) * sr,
      cy + Math.sin(angle) * sr * (scleraRy / scleraRx)
    );
    const ma = angle + Math.sin(t * 0.1 + i * 2) * 0.08;
    const mr = (sr + er) / 2;
    ctx.quadraticCurveTo(
      cx + Math.cos(ma) * mr,
      cy + Math.sin(ma) * mr * (scleraRy / scleraRx),
      cx + Math.cos(angle + 0.04) * er,
      cy + Math.sin(angle + 0.04) * er * (scleraRy / scleraRx)
    );
    ctx.stroke();

    // Branch veins for error mood
    if (mood === "error" && i % 2 === 0) {
      const branchAngle = angle + 0.15;
      const branchR = er * 0.85;
      ctx.beginPath();
      ctx.moveTo(
        cx + Math.cos(ma) * mr,
        cy + Math.sin(ma) * mr * (scleraRy / scleraRx)
      );
      ctx.lineTo(
        cx + Math.cos(branchAngle) * branchR,
        cy + Math.sin(branchAngle) * branchR * (scleraRy / scleraRx)
      );
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;

  // ── 3. Iris ──
  const irisR = r * 0.36;
  const irisX = cx + pupilOffset.x * r * 0.22;
  const irisY = cy + pupilOffset.y * r * 0.18;

  // Iris base gradient — color shifts with mood
  const irisGrad = ctx.createRadialGradient(irisX, irisY, irisR * 0.15, irisX, irisY, irisR);
  irisGrad.addColorStop(0, lighten(moodColor, 0.3));
  irisGrad.addColorStop(0.35, lighten(moodColor, 0.1));
  irisGrad.addColorStop(0.6, moodColor);
  irisGrad.addColorStop(0.8, darken(moodColor, 0.25));
  irisGrad.addColorStop(1, darken(moodColor, 0.55));
  ctx.fillStyle = irisGrad;
  ctx.beginPath();
  ctx.arc(irisX, irisY, irisR, 0, Math.PI * 2);
  ctx.fill();

  // Iris inner glow — pulsing ring of light for speaking/thinking
  if (glowIntensity > 0) {
    const glowPulse = glowIntensity * (0.7 + 0.3 * Math.sin(t * 3.5));
    const glowGrad = ctx.createRadialGradient(
      irisX, irisY, irisR * 0.25,
      irisX, irisY, irisR * 0.75
    );
    glowGrad.addColorStop(0, rgbToStr(mR, mG, mB, glowPulse * 0.6));
    glowGrad.addColorStop(0.5, rgbToStr(mR, mG, mB, glowPulse * 0.3));
    glowGrad.addColorStop(1, rgbToStr(mR, mG, mB, 0));
    ctx.fillStyle = glowGrad;
    ctx.beginPath();
    ctx.arc(irisX, irisY, irisR * 0.75, 0, Math.PI * 2);
    ctx.fill();
  }

  // Iris fibers (60 procedural radial lines)
  ctx.save();
  ctx.beginPath();
  ctx.arc(irisX, irisY, irisR, 0, Math.PI * 2);
  ctx.clip();

  for (let i = 0; i < 60; i++) {
    const angle = (i / 60) * Math.PI * 2;
    const wobble = Math.sin(i * 7.3 + t * 0.15) * 0.025;
    const innerR = irisR * 0.3;
    const outerR = irisR * 0.97;
    ctx.strokeStyle = i % 3 === 0
      ? `rgba(255,255,255,${0.1 + glowIntensity * 0.15})`
      : `rgba(0,0,0,${0.08 + glowIntensity * 0.05})`;
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    ctx.moveTo(
      irisX + Math.cos(angle + wobble) * innerR,
      irisY + Math.sin(angle + wobble) * innerR
    );
    ctx.lineTo(
      irisX + Math.cos(angle) * outerR,
      irisY + Math.sin(angle) * outerR
    );
    ctx.stroke();
  }
  ctx.restore();

  // Collarette ring — inner iris detail ring
  ctx.strokeStyle = rgbToStr(mR * 0.7, mG * 0.7, mB * 0.7, 0.3);
  ctx.lineWidth = irisR * 0.04;
  ctx.beginPath();
  ctx.arc(irisX, irisY, irisR * 0.45, 0, Math.PI * 2);
  ctx.stroke();

  // Limbic ring (dark outer edge)
  ctx.strokeStyle = `rgba(15, 25, 45, ${0.5 + glowIntensity * 0.2})`;
  ctx.lineWidth = irisR * 0.09;
  ctx.beginPath();
  ctx.arc(irisX, irisY, irisR - irisR * 0.045, 0, Math.PI * 2);
  ctx.stroke();

  // ── 4. Pupil ──
  // Mood-dependent pupil size: dilates in dark, constricts in bright,
  // further modified by emotional state
  const basePupil = 0.48 - ambient * 0.22;
  const pupilR = irisR * basePupil * pupilMult;
  const pupilGrad = ctx.createRadialGradient(irisX, irisY, 0, irisX, irisY, pupilR);
  pupilGrad.addColorStop(0, "#020206");
  pupilGrad.addColorStop(0.6, "#060610");
  pupilGrad.addColorStop(0.85, "#0a0a1a");
  pupilGrad.addColorStop(1, "rgba(15, 15, 30, 0.85)");
  ctx.fillStyle = pupilGrad;
  ctx.beginPath();
  ctx.arc(irisX, irisY, pupilR, 0, Math.PI * 2);
  ctx.fill();

  // Pupil edge glow for speaking mood (iris light bleeds into pupil edge)
  if (mood === "speaking") {
    const pupilEdgeGlow = ctx.createRadialGradient(
      irisX, irisY, pupilR * 0.85,
      irisX, irisY, pupilR * 1.15
    );
    const pulse = 0.15 + 0.1 * Math.sin(t * 4);
    pupilEdgeGlow.addColorStop(0, rgbToStr(mR, mG, mB, pulse));
    pupilEdgeGlow.addColorStop(1, rgbToStr(mR, mG, mB, 0));
    ctx.fillStyle = pupilEdgeGlow;
    ctx.beginPath();
    ctx.arc(irisX, irisY, pupilR * 1.15, 0, Math.PI * 2);
    ctx.fill();
  }

  // ── 5. Specular highlights ──
  // Main highlight (upper-left) — slightly animated for liveliness
  const hlBob = Math.sin(t * 0.8) * irisR * 0.015;
  const hlX = irisX - irisR * 0.28 + hlBob;
  const hlY = irisY - irisR * 0.28 + hlBob * 0.5;
  const hlR = irisR * 0.17;
  const hlGrad = ctx.createRadialGradient(hlX, hlY, 0, hlX, hlY, hlR);
  hlGrad.addColorStop(0, "rgba(255,255,255,0.95)");
  hlGrad.addColorStop(0.35, "rgba(255,255,255,0.6)");
  hlGrad.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = hlGrad;
  ctx.beginPath();
  ctx.arc(hlX, hlY, hlR, 0, Math.PI * 2);
  ctx.fill();

  // Secondary highlight (lower-right, smaller)
  const hl2X = irisX + irisR * 0.22;
  const hl2Y = irisY + irisR * 0.18;
  const hl2R = irisR * 0.06;
  ctx.fillStyle = "rgba(255,255,255,0.6)";
  ctx.beginPath();
  ctx.arc(hl2X, hl2Y, hl2R, 0, Math.PI * 2);
  ctx.fill();

  // Tertiary highlight — tiny sparkle that moves slightly
  const hl3Angle = t * 0.3;
  const hl3X = irisX + Math.cos(hl3Angle) * irisR * 0.12;
  const hl3Y = irisY - irisR * 0.1 + Math.sin(hl3Angle) * irisR * 0.05;
  const hl3R = irisR * 0.03;
  ctx.fillStyle = "rgba(255,255,255,0.4)";
  ctx.beginPath();
  ctx.arc(hl3X, hl3Y, hl3R, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore(); // end sclera clip

  // ── 6. Eyelids (expressive bezier curves) ──
  const BG = "#0a0a14";
  const eyeLeft = cx - scleraRx * 1.08;
  const eyeRight = cx + scleraRx * 1.08;
  const eyeTop = cy - scleraRy * 1.15;
  const eyeBottom = cy + scleraRy * 1.15;

  // Top lid with expressive asymmetry
  const topEdgeY = eyeTop + (cy - eyeTop) * topCoverage * 2;
  const innerLiftPx = lidExpr.innerLift * scleraRy;
  const outerLiftPx = lidExpr.outerLift * scleraRy;
  ctx.fillStyle = BG;
  ctx.beginPath();
  ctx.moveTo(eyeLeft, eyeTop);
  ctx.lineTo(eyeRight, eyeTop);
  ctx.lineTo(eyeRight, topEdgeY - outerLiftPx);
  ctx.bezierCurveTo(
    cx + scleraRx * 0.4, topEdgeY - scleraRy * 0.18 * (1 - blink) - outerLiftPx * 0.5,
    cx - scleraRx * 0.4, topEdgeY - scleraRy * 0.18 * (1 - blink) - innerLiftPx * 0.5,
    eyeLeft, topEdgeY - innerLiftPx
  );
  ctx.closePath();
  ctx.fill();

  // Eyelash emphasis on top lid edge — thicker for some moods
  const lashThickness = mood === "thinking" ? r * 0.018 : Math.max(1.5, r * 0.014);
  ctx.strokeStyle = "rgba(25, 25, 45, 0.85)";
  ctx.lineWidth = lashThickness;
  ctx.beginPath();
  ctx.moveTo(eyeLeft + scleraRx * 0.12, topEdgeY - innerLiftPx - 1);
  ctx.bezierCurveTo(
    cx - scleraRx * 0.3, topEdgeY - scleraRy * 0.16 * (1 - blink) - innerLiftPx * 0.5,
    cx + scleraRx * 0.3, topEdgeY - scleraRy * 0.16 * (1 - blink) - outerLiftPx * 0.5,
    eyeRight - scleraRx * 0.12, topEdgeY - outerLiftPx - 1
  );
  ctx.stroke();

  // Bottom lid — less expressive, subtle mirror
  const bottomEdgeY = eyeBottom - (eyeBottom - cy) * bottomCoverage * 2;
  ctx.fillStyle = BG;
  ctx.beginPath();
  ctx.moveTo(eyeLeft, eyeBottom);
  ctx.lineTo(eyeRight, eyeBottom);
  ctx.lineTo(eyeRight, bottomEdgeY + outerLiftPx * 0.3);
  ctx.bezierCurveTo(
    cx + scleraRx * 0.5, bottomEdgeY + scleraRy * 0.1 * (1 - blink) + outerLiftPx * 0.15,
    cx - scleraRx * 0.5, bottomEdgeY + scleraRy * 0.1 * (1 - blink) + innerLiftPx * 0.15,
    eyeLeft, bottomEdgeY + innerLiftPx * 0.3
  );
  ctx.closePath();
  ctx.fill();

  // ── 7. Mood-specific ambient glow around the eye ──
  if (glowIntensity > 0.1) {
    const outerGlow = ctx.createRadialGradient(cx, cy, r * 0.7, cx, cy, r * 1.15);
    const glowAlpha = glowIntensity * 0.12 * (0.8 + 0.2 * Math.sin(t * 2.5));
    outerGlow.addColorStop(0, rgbToStr(mR, mG, mB, 0));
    outerGlow.addColorStop(0.6, rgbToStr(mR, mG, mB, glowAlpha));
    outerGlow.addColorStop(1, rgbToStr(mR, mG, mB, 0));
    ctx.fillStyle = outerGlow;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 1.15, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}
