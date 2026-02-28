import React, { useMemo, useEffect } from "react";
import { Html } from "@react-three/drei";
import * as THREE from "three";
import type { PresenceAgent } from "../types";

// Shared geometry instances (module-level singletons, never disposed)
const TORSO_GEO = new THREE.BoxGeometry(0.35, 0.5, 0.25);
const HEAD_GEO = new THREE.BoxGeometry(0.28, 0.28, 0.25);
const ARM_GEO = new THREE.BoxGeometry(0.12, 0.4, 0.15);
const LEG_GEO = new THREE.BoxGeometry(0.14, 0.25, 0.18);
const CROWN_GEO = new THREE.ConeGeometry(0.15, 0.2, 4);
const CROWN_MAT = new THREE.MeshStandardMaterial({ color: "#fbbf24", flatShading: true });

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

// Deterministic fallback palette
const PALETTE = [
  "#38bdf8", "#818cf8", "#a78bfa", "#e879f9", "#22d3ee",
  "#2dd4bf", "#34d399", "#fbbf24", "#f87171", "#fb923c",
];

// Animation state types and derivation
export type AgentAnimState = "blocked" | "working" | "thinking" | "idle";

/** Derive animation state from agent presence data. Priority: blocked > working > thinking > idle */
export function deriveAnimState(agent: PresenceAgent): AgentAnimState {
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
  agent: PresenceAgent;
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

    // Shared material per agent (1 instead of 6)
    const mat = useMemo(() => new THREE.MeshStandardMaterial({ color, flatShading: true }), [color]);
    useEffect(() => () => { mat.dispose(); }, [mat]);

    const focusText = agent.focus
      ? agent.focus.length > 40
        ? agent.focus.slice(0, 40) + "..."
        : agent.focus
      : "";

    return (
      <group ref={ref} position={position} rotation={[0, rotationY, 0]}>
        {/* Body parts — named for animation targeting via PART_INDEX */}
        <mesh name="torso" position={[0, 0.55, 0]} castShadow geometry={TORSO_GEO} material={mat} />
        <mesh name="head" position={[0, 1.0, 0]} castShadow geometry={HEAD_GEO} material={mat} />
        <mesh name="leftArm" position={[-0.25, 0.5, 0]} castShadow geometry={ARM_GEO} material={mat} />
        <mesh name="rightArm" position={[0.25, 0.5, 0]} castShadow geometry={ARM_GEO} material={mat} />
        <mesh name="leftLeg" position={[-0.1, 0.12, 0]} castShadow geometry={LEG_GEO} material={mat} />
        <mesh name="rightLeg" position={[0.1, 0.12, 0]} castShadow geometry={LEG_GEO} material={mat} />

        {/* Foreman crown (gold low-poly cone) */}
        {isForeman && (
          <mesh position={[0, 1.28, 0]} castShadow geometry={CROWN_GEO} material={CROWN_MAT} />
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
