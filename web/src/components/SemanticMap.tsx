import { useEffect, useMemo } from "react";
import * as THREE from "three";
import type { SemanticZone } from "../utils/panoramaSemanticMap";

interface SemanticMapProps {
  zones: SemanticZone[];
  isDark: boolean;
  projectStatus?: string | null;
}

function ZoneLabel({ primary, secondary, color, isDark, position }: {
  primary: string;
  secondary?: string;
  color: string;
  isDark: boolean;
  position: [number, number, number];
}) {
  const labelState = useMemo(() => {
    const dpr = 2;
    const primaryFs = 11 * dpr;
    const secondaryFs = 8 * dpr;
    const canvas = document.createElement("canvas");
    const measure = canvas.getContext("2d")!;
    measure.font = `700 ${primaryFs}px sans-serif`;
    const primaryWidth = measure.measureText(primary).width;
    measure.font = `500 ${secondaryFs}px sans-serif`;
    const secondaryWidth = secondary ? measure.measureText(secondary).width : 0;
    const padX = 14 * dpr;
    const padY = 8 * dpr;
    const width = Math.ceil(Math.max(primaryWidth, secondaryWidth) + padX * 2);
    const height = Math.ceil(primaryFs + (secondary ? secondaryFs + 6 * dpr : 0) + padY * 2);
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d")!;

    ctx.beginPath();
    ctx.roundRect(0, 0, width, height, 8 * dpr);
    ctx.fillStyle = isDark ? "rgba(15,23,42,0.82)" : "rgba(255,255,255,0.92)";
    ctx.fill();

    ctx.strokeStyle = color;
    ctx.lineWidth = 2 * dpr;
    ctx.stroke();

    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `700 ${primaryFs}px sans-serif`;
    ctx.fillStyle = isDark ? "#f8fafc" : "#0f172a";
    ctx.fillText(primary, width / 2, secondary ? height / 2 - 8 * dpr : height / 2);

    if (secondary) {
      ctx.font = `500 ${secondaryFs}px sans-serif`;
      ctx.fillStyle = isDark ? "#cbd5e1" : "#475569";
      ctx.fillText(secondary, width / 2, height / 2 + 11 * dpr);
    }

    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const worldW = Math.max(1.1, Math.min(2.2, width / dpr / 95));
    return { tex, scale: [worldW, worldW * (height / width)] as [number, number] };
  }, [primary, secondary, color, isDark]);

  useEffect(() => () => { labelState.tex.dispose(); }, [labelState.tex]);

  return (
    <sprite position={position} scale={[labelState.scale[0], labelState.scale[1], 1]}>
      <spriteMaterial map={labelState.tex} transparent depthWrite={false} />
    </sprite>
  );
}

function StatusBanner({ text, isDark }: { text: string; isDark: boolean }) {
  return (
    <ZoneLabel
      primary={text}
      color={isDark ? "#38bdf8" : "#2563eb"}
      isDark={isDark}
      position={[0, 0.7, 8.7]}
    />
  );
}

function ZoneAnchor({ zone, isDark }: { zone: SemanticZone; isDark: boolean }) {
  const accent = zone.color;
  const base = isDark ? "#0f172a" : "#ffffff";
  return (
    <group position={zone.center}>
      {/* 轻量锚点：保留分区提示，但不再用整块地板染色 */}
      <mesh position={[0, 0.035, 0]}>
        <cylinderGeometry args={[0.26, 0.26, 0.04, 18]} />
        <meshStandardMaterial color={base} transparent opacity={0.9} />
      </mesh>
      <mesh position={[0, 0.06, 0]}>
        <cylinderGeometry args={[0.14, 0.14, 0.02, 18]} />
        <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.16} />
      </mesh>
      <ZoneLabel
        primary={zone.label}
        secondary={zone.subtitle}
        color={zone.color}
        isDark={isDark}
        position={[0, 0.72, 0]}
      />
    </group>
  );
}

export function SemanticMap({ zones, isDark, projectStatus }: SemanticMapProps) {
  return (
    <>
      {zones.map((zone) => (
        <ZoneAnchor key={zone.id} zone={zone} isDark={isDark} />
      ))}
      {projectStatus ? <StatusBanner text={projectStatus} isDark={isDark} /> : null}
    </>
  );
}
