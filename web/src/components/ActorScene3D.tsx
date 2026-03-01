import { Suspense, useMemo, useRef, useCallback } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { ActorCharacter, hashCode, deriveAnimState, PART_INDEX } from "./ActorCharacter";
import { Workstation } from "./OfficeFurniture";
import type { AgentState, Actor } from "../types";
import * as THREE from "three";

interface ActorScene3DProps {
  agents: AgentState[];
  actors?: Actor[];
  isDark: boolean;
  className?: string;
}

// ── MC Grass Block ground (green top, brown sides) ──
const GROUND_GEO = new THREE.BoxGeometry(20, 0.5, 20);
const GRASS_TOP = new THREE.MeshStandardMaterial({ color: "#5B8731", flatShading: true });
const DIRT_SIDE = new THREE.MeshStandardMaterial({ color: "#8B6B3E", flatShading: true });
// Material array order: [+X, -X, +Y, -Y, +Z, -Z]
const GROUND_MATS = [DIRT_SIDE, DIRT_SIDE, GRASS_TOP, DIRT_SIDE, DIRT_SIDE, DIRT_SIDE];

function MCGround() {
  return <mesh position={[0, -0.25, 0]} receiveShadow geometry={GROUND_GEO} material={GROUND_MATS} />;
}

// ── Workstation layout ──
interface LayoutItem {
  charPos: [number, number, number];
  wsPos: [number, number, number];
  charRotY: number;
  wsRotY: number;
}

function layoutRadius(agentCount: number): number {
  const peerCount = Math.max(0, agentCount - 1);
  return Math.max(2.5, peerCount * 0.7 + 1);
}

function computeWorkstationLayout(
  agents: AgentState[],
  actorMap: Map<string, Actor>,
): LayoutItem[] {
  const count = agents.length;
  if (count === 0) return [];

  const foremanIdx = agents.findIndex((a) => actorMap.get(a.id)?.role === "foreman");
  const peerCount = foremanIdx >= 0 ? count - 1 : count;
  const radius = layoutRadius(count);
  const gap = 0.7;

  const items: LayoutItem[] = [];
  let pi = 0;

  for (let i = 0; i < count; i++) {
    if (i === foremanIdx) {
      // Foreman: center-back (positive Z)
      const fz = radius * 0.5;
      const ry = Math.atan2(0, fz + gap); // 0 — faces -Z toward center
      items.push({
        charPos: [0, 0, fz + gap],
        wsPos: [0, 0, fz],
        charRotY: ry,
        wsRotY: ry + Math.PI,
      });
    } else {
      // Peers on semi-circle arc in front (negative Z half)
      const span = Math.min(Math.PI * 0.8, Math.max(0.6, peerCount * 0.4));
      const theta =
        peerCount > 1 ? -span / 2 + pi * (span / (peerCount - 1)) : 0;
      const x = radius * Math.sin(theta);
      const z = -radius * Math.cos(theta);
      const cx = (radius + gap) * Math.sin(theta);
      const cz = -(radius + gap) * Math.cos(theta);
      const ry = Math.atan2(cx, cz); // face toward center
      items.push({
        charPos: [cx, 0, cz],
        wsPos: [x, 0, z],
        charRotY: ry,
        wsRotY: ry + Math.PI,
      });
      pi++;
    }
  }

  return items;
}

// ── Scene ──
interface SceneProps {
  agents: AgentState[];
  actors?: Actor[];
  isDark: boolean;
  camZ: number;
}

function Scene({ agents, actors, isDark, camZ }: SceneProps) {
  const characterRefs = useRef<Map<string, THREE.Group>>(new Map());
  const refCallbacks = useRef<Map<string, (el: THREE.Group | null) => void>>(new Map());

  const actorMap = useMemo(() => {
    const m = new Map<string, Actor>();
    for (const a of actors || []) m.set(a.id, a);
    return m;
  }, [actors]);

  const layout = useMemo(
    () => computeWorkstationLayout(agents, actorMap),
    [agents, actorMap],
  );

  // Stable callback ref factory (same function per id across renders)
  const getRef = useCallback((id: string) => {
    let cb = refCallbacks.current.get(id);
    if (!cb) {
      cb = (el: THREE.Group | null) => {
        if (el) characterRefs.current.set(id, el);
        else characterRefs.current.delete(id);
      };
      refCallbacks.current.set(id, cb);
    }
    return cb;
  }, []);

  // Body part base positions (must match ActorCharacter JSX)
  const BASE_Y = { torso: 0.55, head: 1.0, leftArm: 0.5, rightArm: 0.5, leftLeg: 0.12, rightLeg: 0.12 };

  // Unified animation with lerp smoothing (~0.5s transitions)
  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;
    const lf = 1 - Math.exp(-6 * delta); // lerp factor: ~95% at 0.5s

    for (let i = 0; i < agents.length; i++) {
      const agent = agents[i];
      const group = characterRefs.current.get(agent.id);
      if (!group || group.children.length < 6) continue;

      const item = layout[i];
      if (!item) continue;

      const baseX = item.charPos[0];
      const baseY = item.charPos[1];
      const baseZ = item.charPos[2];
      const phase = hashCode(agent.id) * 0.1;
      const animState = deriveAnimState(agent);

      const torso = group.children[PART_INDEX.torso];
      const head = group.children[PART_INDEX.head];
      const lArm = group.children[PART_INDEX.leftArm];
      const rArm = group.children[PART_INDEX.rightArm];
      const lLeg = group.children[PART_INDEX.leftLeg];
      const rLeg = group.children[PART_INDEX.rightLeg];

      // Compute target pose based on animation state
      let gY = baseY, gX = baseX, gZ = baseZ;
      let tRx = 0, tPy = BASE_Y.torso;
      let hRx = 0, hRy = 0, hPy = BASE_Y.head;
      let laRx = 0, laRz = 0, laPy = BASE_Y.leftArm;
      let raRx = 0, raRz = 0, raPy = BASE_Y.rightArm;
      let llPy = BASE_Y.leftLeg, rlPy = BASE_Y.rightLeg;
      let llRx = 0, rlRx = 0;

      switch (animState) {
        case "working": {
          gY = baseY + Math.sin(t * 1.2 + phase) * 0.04;
          tRx = 0.08;
          hRx = 0.12;
          laRx = Math.sin(t * 8 + phase) * 0.3;
          raRx = Math.sin(t * 8 + phase + Math.PI) * 0.3;
          break;
        }
        case "thinking": {
          gY = baseY + Math.sin(t * 0.8 + phase) * 0.03;
          hRy = Math.sin(t * 1.5 + phase) * 0.25;
          raRz = -0.6;
          raRx = -0.4;
          raPy = BASE_Y.rightArm + 0.1;
          break;
        }
        case "blocked": {
          gY = baseY + Math.sin(t * 2 + phase) * 0.03;
          // Pace along character's local X axis (perpendicular to facing)
          const pace = Math.sin(t * 2 + phase) * 0.15;
          const cosR = Math.cos(item.charRotY);
          const sinR = Math.sin(item.charRotY);
          gX = baseX + pace * cosR;
          gZ = baseZ - pace * sinR;
          hRx = 0.15;
          llPy = BASE_Y.leftLeg + Math.max(0, Math.sin(t * 4 + phase)) * 0.08;
          rlPy = BASE_Y.rightLeg + Math.max(0, Math.sin(t * 4 + phase + Math.PI)) * 0.08;
          laRx = Math.sin(t * 2 + phase) * 0.15;
          raRx = Math.sin(t * 2 + phase + Math.PI) * 0.15;
          break;
        }
        case "idle":
        default: {
          gY = baseY + Math.sin(t * 0.6 + phase) * 0.02;
          tPy = BASE_Y.torso - 0.03;
          hPy = BASE_Y.head - 0.03;
          hRx = 0.1;
          laPy = BASE_Y.leftArm - 0.03;
          raPy = BASE_Y.rightArm - 0.03;
          break;
        }
      }

      // Lerp all values for smooth state transitions
      const L = THREE.MathUtils.lerp;
      group.position.x = L(group.position.x, gX, lf);
      group.position.y = L(group.position.y, gY, lf);
      group.position.z = L(group.position.z, gZ, lf);
      torso.rotation.x = L(torso.rotation.x, tRx, lf);
      torso.position.y = L(torso.position.y, tPy, lf);
      head.rotation.x = L(head.rotation.x, hRx, lf);
      head.rotation.y = L(head.rotation.y, hRy, lf);
      head.position.y = L(head.position.y, hPy, lf);
      lArm.rotation.x = L(lArm.rotation.x, laRx, lf);
      lArm.rotation.z = L(lArm.rotation.z, laRz, lf);
      lArm.position.y = L(lArm.position.y, laPy, lf);
      rArm.rotation.x = L(rArm.rotation.x, raRx, lf);
      rArm.rotation.z = L(rArm.rotation.z, raRz, lf);
      rArm.position.y = L(rArm.position.y, raPy, lf);
      lLeg.rotation.x = L(lLeg.rotation.x, llRx, lf);
      lLeg.position.y = L(lLeg.position.y, llPy, lf);
      rLeg.rotation.x = L(rLeg.rotation.x, rlRx, lf);
      rLeg.position.y = L(rLeg.position.y, rlPy, lf);
    }
  });

  return (
    <>
      {/* Lighting */}
      <ambientLight intensity={isDark ? 0.35 : 0.6} />
      <directionalLight
        position={[5, 8, 5]}
        intensity={isDark ? 0.7 : 0.9}
        castShadow
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
      />
      <directionalLight
        position={[-3, 4, -3]}
        intensity={isDark ? 0.15 : 0.25}
      />

      {/* MC Grass Block ground */}
      <MCGround />

      {/* Workstations */}
      {layout.map((item, i) => {
        const agent = agents[i];
        if (!agent) return null;
        const anim = deriveAnimState(agent);
        return (
          <Workstation
            key={`ws-${agent.id}`}
            position={item.wsPos}
            rotation={item.wsRotY}
            isOn={anim === "working" || anim === "thinking"}
          />
        );
      })}

      {/* Characters */}
      {agents.map((agent, i) => {
        const actor = actorMap.get(agent.id);
        const item = layout[i];
        return (
          <ActorCharacter
            key={agent.id}
            ref={getRef(agent.id)}
            agent={agent}
            position={item?.charPos || [0, 0, 0]}
            rotationY={item?.charRotY}
            isDark={isDark}
            role={actor?.role}
            runtime={actor?.runtime}
            title={actor?.title}
          />
        );
      })}

      {/* Camera controls */}
      <OrbitControls
        enablePan={false}
        enableZoom={true}
        enableRotate={true}
        minDistance={2}
        maxDistance={camZ * 2}
        maxPolarAngle={Math.PI / 2.2}
        target={[0, 0.5, 0]}
        autoRotate={false}
      />
    </>
  );
}

export function ActorScene3D({ agents, actors, isDark, className }: ActorScene3DProps) {
  const camZ = useMemo(() => {
    const radius = layoutRadius(agents.length);
    return Math.max(4, radius * 2 + 2);
  }, [agents.length]);

  return (
    <div className={className} style={{ minHeight: 280 }}>
      <Canvas
        shadows
        camera={{
          position: [camZ * 0.6, camZ * 0.5, camZ],
          fov: 45,
          near: 0.1,
          far: 100,
        }}
        style={{
          borderRadius: 12,
          background: isDark ? "#191970" : "#87CEEB",
        }}
        gl={{ antialias: true, alpha: false }}
      >
        <Suspense fallback={null}>
          <Scene agents={agents} actors={actors} isDark={isDark} camZ={camZ} />
        </Suspense>
      </Canvas>
    </div>
  );
}
