// ProjectBuilding: renders a single center building from LLM-generated blueprint
// Three-layer visualization: done (solid) / active (pulsing) / planned (ghost)
// Falls back to a 3×3×3 grey placeholder when no blueprint is available

import { useMemo, useRef, useEffect } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { InstancedBlocks, type BlockInstance, type InstancedBlocksHandle } from "./InstancedBlocks";
import { BuildSite } from "./BuildSite";
import { parseBlueprint } from "../utils/blueprintSchema";
import type { ProjectBlueprint, Task } from "../types";
import type { Blueprint } from "../data/blueprints";

interface ProjectBuildingProps {
  blueprint: ProjectBlueprint | null | undefined;
  tasks?: Task[];
  isDark: boolean;
}

/** Convert a validated ProjectBlueprint into the internal Blueprint format */
function toInternalBlueprint(pb: ProjectBlueprint): Blueprint {
  return {
    id: "__project__",
    name: "Project",
    theme: "project",
    blocks: pb.blocks.map((b) => ({
      x: b.x,
      y: b.y,
      z: b.z,
      color: b.color,
      order: b.order,
    })),
    gridSize: pb.gridSize,
    blockScale: pb.blockScale,
  };
}

// Fallback: 3×3×3 grey cube
const FALLBACK_BLUEPRINT: Blueprint = {
  id: "__fallback__",
  name: "Placeholder",
  theme: "fallback",
  blocks: Array.from({ length: 27 }, (_, i) => ({
    x: i % 3,
    y: Math.floor(i / 9),
    z: Math.floor(i / 3) % 3,
    color: "#6b7280",
    order: i,
  })),
  gridSize: [3, 3, 3],
  blockScale: 0.15,
};

const POP_DURATION = 0.3;
const GHOST_OPACITY = 0.15;

function easeOutBack(x: number): number {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

// Label text canvas for fallback hint
function FallbackLabel({ isDark }: { isDark: boolean }) {
  const labelState = useMemo(() => {
    const DPR = 2;
    const fs = 9 * DPR;
    const text = "No blueprint yet";
    const canvas = document.createElement("canvas");
    const CW = 140 * DPR;
    const CH = Math.ceil(fs * 1.3 + 6 * DPR);
    canvas.width = CW;
    canvas.height = CH;
    const ctx = canvas.getContext("2d")!;

    ctx.beginPath();
    ctx.roundRect(0, 0, CW, CH, 4 * DPR);
    ctx.fillStyle = isDark ? "rgba(15,23,42,0.7)" : "rgba(255,255,255,0.8)";
    ctx.fill();

    ctx.font = `500 ${fs}px sans-serif`;
    ctx.fillStyle = isDark ? "#94a3b8" : "#9ca3af";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, CW / 2, CH / 2);

    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const worldW = 0.8;
    return { tex, scale: [worldW, worldW * (CH / CW)] as [number, number] };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark]);

  useEffect(() => {
    return () => { labelState.tex.dispose(); };
  }, [labelState.tex]);

  return (
    <sprite position={[0, 0.8, 0]} scale={[labelState.scale[0], labelState.scale[1], 1]}>
      <spriteMaterial map={labelState.tex} transparent depthWrite={false} />
    </sprite>
  );
}

/** Three-layer building: done(solid) / active(pulsing) / planned(ghost) */
function LayeredBuilding({ blueprint, tasks }: {
  blueprint: Blueprint;
  tasks?: Task[];
}) {
  const solidRef = useRef<InstancedBlocksHandle>(null);
  const activeGroupRef = useRef<THREE.Group>(null);
  const prevDoneCountRef = useRef(-1);
  const animMap = useRef(new Map<number, number>());

  const bs = blueprint.blockScale;

  const sortedBlocks = useMemo(
    () => [...blueprint.blocks].sort((a, b) => a.order - b.order),
    [blueprint],
  );

  const total = sortedBlocks.length;
  const cx = -(blueprint.gridSize[0] - 1) * bs / 2;
  const cz = -(blueprint.gridSize[2] - 1) * bs / 2;

  // Calculate block counts from task statuses
  const { doneCount, activeEndCount } = useMemo(() => {
    if (!tasks || tasks.length === 0) {
      return { doneCount: total, activeEndCount: total };
    }
    const totalTasks = tasks.length;
    const doneTasks = tasks.filter(
      (t) => t.status === "done" || t.status === "archived",
    ).length;
    const activeTasks = tasks.filter(
      (t) => t.status === "active",
    ).length;
    return {
      doneCount: Math.floor(total * clamp01(doneTasks / totalTasks)),
      activeEndCount: Math.floor(total * clamp01((doneTasks + activeTasks) / totalTasks)),
    };
  }, [tasks, total]);

  // Pop-in animation when done count increases (skip initial mount)
  useEffect(() => {
    if (prevDoneCountRef.current >= 0 && doneCount > prevDoneCountRef.current) {
      const now = performance.now() / 1000;
      for (let i = prevDoneCountRef.current; i < doneCount; i++) {
        animMap.current.set(i, now);
      }
    }
    prevDoneCountRef.current = doneCount;
  }, [doneCount]);

  // Done blocks: fully solid
  const doneBlocks = useMemo((): BlockInstance[] =>
    sortedBlocks.slice(0, doneCount).map((bl) => ({
      position: [cx + bl.x * bs, bl.y * bs, cz + bl.z * bs] as [number, number, number],
      color: bl.color,
      scale: bs,
    })),
    [sortedBlocks, doneCount, bs, cx, cz],
  );

  // Active blocks: pulsing opacity
  const activeBlocks = useMemo((): BlockInstance[] =>
    sortedBlocks.slice(doneCount, activeEndCount).map((bl) => ({
      position: [cx + bl.x * bs, bl.y * bs, cz + bl.z * bs] as [number, number, number],
      color: bl.color,
      scale: bs,
    })),
    [sortedBlocks, doneCount, activeEndCount, bs, cx, cz],
  );

  // Planned blocks: ghost at 15%
  const plannedBlocks = useMemo((): BlockInstance[] =>
    sortedBlocks.slice(activeEndCount).map((bl) => ({
      position: [cx + bl.x * bs, bl.y * bs, cz + bl.z * bs] as [number, number, number],
      color: bl.color,
      scale: bs,
    })),
    [sortedBlocks, activeEndCount, bs, cx, cz],
  );

  // Keep ref in sync for useFrame
  const doneBlocksRef = useRef(doneBlocks);
  useEffect(() => { doneBlocksRef.current = doneBlocks; });

  useFrame(() => {
    // 1. Pop-in animation for done blocks
    if (animMap.current.size > 0) {
      const handle = solidRef.current;
      if (handle) {
        const blocks = doneBlocksRef.current;
        const now = performance.now() / 1000;
        for (const [idx, startTime] of animMap.current) {
          if (idx >= blocks.length) { animMap.current.delete(idx); continue; }
          const bl = blocks[idx];
          const elapsed = now - startTime;
          if (elapsed >= POP_DURATION) {
            handle.setBlock(idx, bl.position, bl.color, bs);
            animMap.current.delete(idx);
          } else {
            handle.setBlock(idx, bl.position, bl.color, bs * easeOutBack(elapsed / POP_DURATION));
          }
        }
        handle.flush();
      }
    }

    // 2. Active layer pulsing opacity (0.35 → 0.85)
    const group = activeGroupRef.current;
    if (group && group.children.length > 0) {
      const mesh = group.children[0] as THREE.InstancedMesh;
      if (mesh?.material) {
        const mat = mesh.material as THREE.MeshStandardMaterial;
        const t = performance.now() / 1000;
        mat.opacity = 0.35 + 0.25 * (Math.sin(t * 2) + 1);
      }
    }
  });

  return (
    <group position={[0, 0, 0]}>
      {doneBlocks.length > 0 && (
        <InstancedBlocks ref={solidRef} blocks={doneBlocks} />
      )}
      <group ref={activeGroupRef}>
        {activeBlocks.length > 0 && (
          <InstancedBlocks blocks={activeBlocks} opacity={0.5} transparent />
        )}
      </group>
      {plannedBlocks.length > 0 && (
        <InstancedBlocks blocks={plannedBlocks} opacity={GHOST_OPACITY} transparent />
      )}
    </group>
  );
}

export function ProjectBuilding({ blueprint: rawBlueprint, tasks, isDark }: ProjectBuildingProps) {
  const resolved = useMemo(() => {
    if (!rawBlueprint) return null;
    return parseBlueprint(rawBlueprint);
  }, [rawBlueprint]);

  const internalBlueprint = useMemo(
    () => (resolved ? toInternalBlueprint(resolved) : null),
    [resolved],
  );

  if (!internalBlueprint) {
    // Fallback: grey placeholder cube with hint label
    return (
      <group position={[0, 0, 0]}>
        <BuildSite
          blueprint={FALLBACK_BLUEPRINT}
          progress={1}
          status="complete"
          position={[0, 0, 0]}
          isDark={isDark}
        />
        <FallbackLabel isDark={isDark} />
      </group>
    );
  }

  return <LayeredBuilding blueprint={internalBlueprint} tasks={tasks} />;
}
