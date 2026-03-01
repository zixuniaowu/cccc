import React, { useMemo, useEffect } from "react";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import type { AgentState } from "../types";

// Shared geometry instances (module-level singletons, never disposed)
const TORSO_GEO = new THREE.BoxGeometry(0.35, 0.5, 0.25);
const HEAD_GEO = new THREE.BoxGeometry(0.28, 0.28, 0.25);
const ARM_GEO = new THREE.BoxGeometry(0.12, 0.4, 0.15);
const LEG_GEO = new THREE.BoxGeometry(0.14, 0.25, 0.18);
// MC-style blocky crown: band + 3 tooth points
const CROWN_BAND_GEO = new THREE.BoxGeometry(0.32, 0.05, 0.29);
const CROWN_POINT_GEO = new THREE.BoxGeometry(0.07, 0.09, 0.07);
const CROWN_MAT = new THREE.MeshStandardMaterial({ color: "#fbbf24" });

// Runtime → body color mapping
const RUNTIME_BODY_COLORS: Record<string, string> = {
  claude:   "#38bdf8", // sky blue
  gemini:   "#34d399", // emerald
  codex:    "#22d3ee", // cyan
  grok:     "#f87171", // red
  copilot:  "#94a3b8", // slate
  aider:    "#a78bfa", // violet
  roo:      "#e879f9", // fuchsia
  neovate:  "#d946ef", // fuchsia-deep
  opencode: "#06b6d4", // cyan-deep
  custom:   "#fb923c", // orange
};

// Runtime → face label (MVP text logos)
const RUNTIME_FACE: Record<string, string> = {
  claude:   "C",
  gemini:   "G",
  codex:    "Cx",
  grok:     "Gk",
  copilot:  "Cp",
  aider:    "A",
  roo:      "R",
  neovate:  "N",
  opencode: "O",
  custom:   "?",
};

// Runtime → PNG logo paths (served from /public/logos/, prefixed with Vite base)
const _base = import.meta.env.BASE_URL;
const RUNTIME_LOGO: Record<string, string> = {
  claude: `${_base}logos/claude.png`,
  codex: `${_base}logos/codex.png`,
  gemini: `${_base}logos/gemini.png`,
};

// Cached textures for head face logos
const FACE_TEX_CACHE = new Map<string, THREE.CanvasTexture>();

function getFaceTexture(label: string, bg: string): THREE.CanvasTexture {
  const key = `txt:${label}:${bg}`;
  let t = FACE_TEX_CACHE.get(key);
  if (t) return t;
  const s = 64;
  const c = document.createElement("canvas");
  c.width = s;
  c.height = s;
  const ctx = c.getContext("2d")!;
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, s, s);
  ctx.fillStyle = "#fff";
  ctx.font = label.length > 1 ? "bold 26px monospace" : "bold 36px monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, s / 2, s / 2);
  t = new THREE.CanvasTexture(c);
  FACE_TEX_CACHE.set(key, t);
  return t;
}

// PNG texture loader with body-color background fill (covers rounded corners)
function getPngTexture(path: string, bgColor: string): THREE.CanvasTexture {
  const key = `png:${path}:${bgColor}`;
  let t = FACE_TEX_CACHE.get(key);
  if (t) return t;
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  t = new THREE.CanvasTexture(canvas);
  t.colorSpace = THREE.SRGBColorSpace;
  FACE_TEX_CACHE.set(key, t);
  const tex = t;
  const img = new Image();
  img.onload = () => {
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, size, size);
    ctx.drawImage(img, 0, 0, size, size);
    tex.needsUpdate = true;
  };
  img.src = path;
  return t;
}

// Deterministic fallback palette
const PALETTE = [
  "#38bdf8", "#818cf8", "#a78bfa", "#e879f9", "#22d3ee",
  "#2dd4bf", "#34d399", "#fbbf24", "#f87171", "#fb923c",
];

// Animation state types and derivation
export type AgentAnimState = "blocked" | "working" | "thinking" | "idle";

/** Derive animation state from agent state data. Priority: blocked > working > thinking > idle */
export function deriveAnimState(agent: AgentState): AgentAnimState {
  if (Array.isArray(agent.blockers) && agent.blockers.length > 0) return "blocked";
  if (agent.active_task_id && agent.focus) return "working";
  if (agent.focus || agent.next_action) return "thinking";
  return "idle";
}

// Body part name → child index mapping (for unified useFrame animation)
export const PART_INDEX = {
  torso: 0,
  head: 1,
  leftArm: 2,
  rightArm: 3,
  leftLeg: 4,
  rightLeg: 5,
} as const;

export function hashCode(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function agentColor(id: string, runtime?: string): string {
  if (runtime && RUNTIME_BODY_COLORS[runtime]) {
    return RUNTIME_BODY_COLORS[runtime];
  }
  return PALETTE[hashCode(id) % PALETTE.length];
}

export interface ActorCharacterProps {
  agent: AgentState;
  position: [number, number, number];
  rotationY?: number;
  isDark: boolean;
  role?: string;
  runtime?: string;
  title?: string;
}

export const ActorCharacter = React.forwardRef<THREE.Group, ActorCharacterProps>(
  function ActorCharacter({ agent, position, rotationY = 0, isDark, role, runtime, title }, ref) {
    const color = agentColor(agent.id, runtime);
    const hasBlockers = Array.isArray(agent.blockers) && agent.blockers.length > 0;
    const hasFocus = !!agent.focus;
    const isForeman = role === "foreman";

    // Shared material per agent (body color)
    const mat = useMemo(() => new THREE.MeshStandardMaterial({ color, flatShading: true }), [color]);
    useEffect(() => () => { mat.dispose(); }, [mat]);

    // Head face texture: PNG logo if available, else text fallback
    const faceLabel = runtime
      ? (RUNTIME_FACE[runtime] || runtime.charAt(0).toUpperCase())
      : agent.id.charAt(0).toUpperCase();
    const logoPath = runtime ? RUNTIME_LOGO[runtime] : undefined;
    const faceMat = useMemo(() => {
      if (logoPath) {
        const tex = getPngTexture(logoPath, color);
        return new THREE.MeshStandardMaterial({ map: tex, flatShading: true });
      }
      const tex = getFaceTexture(faceLabel, color);
      return new THREE.MeshStandardMaterial({ map: tex, flatShading: true });
    }, [logoPath, faceLabel, color]);
    useEffect(() => () => { faceMat.dispose(); }, [faceMat]);
    // Material array: all sides = body color, front face (-Z, index 5) = logo
    const headMats = useMemo(
      () => [mat, mat, mat, mat, mat, faceMat],
      [mat, faceMat],
    );

    const focusText = agent.focus
      ? agent.focus.length > 40
        ? agent.focus.slice(0, 40) + "..."
        : agent.focus
      : "";

    return (
      <group ref={ref} position={position} rotation={[0, rotationY, 0]}>
        {/* Body parts — named for animation targeting via PART_INDEX */}
        <mesh name="torso" position={[0, 0.55, 0]} castShadow geometry={TORSO_GEO} material={mat} />
        <mesh name="head" position={[0, 1.0, 0]} castShadow geometry={HEAD_GEO} material={headMats} />
        <mesh name="leftArm" position={[-0.25, 0.5, 0]} castShadow geometry={ARM_GEO} material={mat} />
        <mesh name="rightArm" position={[0.25, 0.5, 0]} castShadow geometry={ARM_GEO} material={mat} />
        <mesh name="leftLeg" position={[-0.1, 0.12, 0]} castShadow geometry={LEG_GEO} material={mat} />
        <mesh name="rightLeg" position={[0.1, 0.12, 0]} castShadow geometry={LEG_GEO} material={mat} />

        {/* Foreman crown (MC-style blocky gold crown) */}
        {isForeman && (
          <group position={[0, 1.165, 0]}>
            <mesh castShadow geometry={CROWN_BAND_GEO} material={CROWN_MAT} />
            <mesh position={[0, 0.065, -0.1]} castShadow geometry={CROWN_POINT_GEO} material={CROWN_MAT} />
            <mesh position={[-0.11, 0.065, 0.09]} castShadow geometry={CROWN_POINT_GEO} material={CROWN_MAT} />
            <mesh position={[0.11, 0.065, 0.09]} castShadow geometry={CROWN_POINT_GEO} material={CROWN_MAT} />
          </group>
        )}

        {/* Status bubble above head */}
        <Html
          position={[0, isForeman ? 1.65 : 1.55, 0]}
          center
          distanceFactor={6}
          style={{ pointerEvents: "none", userSelect: "none" }}
        >
          <div
            style={{
              background: isDark ? "rgba(15,23,42,0.92)" : "rgba(255,255,255,0.95)",
              border: `1px solid ${isDark ? "rgba(100,116,139,0.4)" : "rgba(209,213,219,0.8)"}`,
              borderRadius: 8,
              padding: "4px 8px",
              minWidth: 60,
              maxWidth: 180,
              textAlign: "center",
              boxShadow: isDark
                ? "0 2px 8px rgba(0,0,0,0.4)"
                : "0 2px 8px rgba(0,0,0,0.1)",
            }}
          >
            {/* Agent name */}
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: color,
                lineHeight: "14px",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {title || agent.id}
            </div>

            {/* Task ID */}
            {agent.active_task_id && (
              <div
                style={{
                  fontSize: 9,
                  color: isDark ? "#94a3b8" : "#6b7280",
                  marginTop: 2,
                  lineHeight: "12px",
                }}
              >
                {agent.active_task_id}
              </div>
            )}

            {/* Focus */}
            {focusText && (
              <div
                style={{
                  fontSize: 9,
                  color: isDark ? "#cbd5e1" : "#374151",
                  marginTop: 2,
                  lineHeight: "12px",
                  wordBreak: "break-word",
                }}
              >
                {focusText}
              </div>
            )}

            {/* Blockers indicator */}
            {hasBlockers && (
              <div
                style={{
                  fontSize: 9,
                  color: "#f87171",
                  marginTop: 2,
                  fontWeight: 600,
                  lineHeight: "12px",
                }}
              >
                BLOCKED
              </div>
            )}

            {/* Idle indicator */}
            {!hasFocus && !hasBlockers && (
              <div
                style={{
                  fontSize: 9,
                  color: isDark ? "#64748b" : "#9ca3af",
                  marginTop: 1,
                  fontStyle: "italic",
                  lineHeight: "12px",
                }}
              >
                idle
              </div>
            )}
          </div>
        </Html>
      </group>
    );
  },
);
