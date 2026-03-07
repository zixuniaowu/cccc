import { useMemo } from "react";
import { useGLTF } from "@react-three/drei";
import * as THREE from "three";
import type { SemanticZone } from "../utils/panoramaSemanticMap";

const BASE = `${import.meta.env.BASE_URL}assets/kenney/mini-market/Models/GLB format/`;

function modelUrl(name: string): string {
  return encodeURI(`${BASE}${name}.glb`);
}

interface ModelInstanceProps {
  name: string;
  position: [number, number, number];
  rotation?: [number, number, number];
  scale?: number;
}

function ModelInstance({ name, position, rotation = [0, 0, 0], scale = 1 }: ModelInstanceProps) {
  const gltf = useGLTF(modelUrl(name));
  const object = useMemo(() => {
    const cloned = gltf.scene.clone(true);
    cloned.traverse((node) => {
      if ((node as THREE.Mesh).isMesh) {
        const mesh = node as THREE.Mesh;
        mesh.castShadow = true;
        mesh.receiveShadow = true;
      }
    });
    return cloned;
  }, [gltf.scene]);

  return <primitive object={object} position={position} rotation={rotation} scale={scale} />;
}

function RoomShell() {
  const floorTiles: Array<[number, number, number]> = [];
  for (let x = -9; x <= 9; x += 2) {
    for (let z = -9; z <= 9; z += 2) {
      floorTiles.push([x, 0.001, z]);
    }
  }

  const backWalls = Array.from({ length: 10 }, (_, index) => -9 + index * 2);
  const sideWalls = Array.from({ length: 5 }, (_, index) => -8 + index * 2);

  return (
    <group>
      {floorTiles.map((pos, index) => (
        <ModelInstance key={`floor-${index}`} name="floor" position={pos} scale={2} />
      ))}

      {backWalls.map((x, index) => (
        <ModelInstance key={`back-wall-${index}`} name="wall" position={[x, 0, -10]} scale={2} />
      ))}

      {sideWalls.map((z, index) => (
        <group key={`side-wall-${index}`}>
          <ModelInstance name="wall" position={[-10, 0, z]} rotation={[0, Math.PI / 2, 0]} scale={2} />
          <ModelInstance name="wall" position={[10, 0, z]} rotation={[0, -Math.PI / 2, 0]} scale={2} />
        </group>
      ))}

      <ModelInstance name="wall-corner" position={[-10, 0, -10]} scale={2} />
      <ModelInstance name="wall-corner" position={[10, 0, -10]} rotation={[0, Math.PI / 2, 0]} scale={2} />

      {[
        [-10, 0, -10],
        [10, 0, -10],
        [-10, 0, 1],
        [10, 0, 1],
      ].map((pos, index) => (
        <ModelInstance key={`column-${index}`} name="column" position={pos as [number, number, number]} scale={1.8} />
      ))}

      <mesh position={[0, 4.6, -4.6]}>
        <boxGeometry args={[19.8, 0.12, 0.18]} />
        <meshStandardMaterial color="#334155" emissive="#1e293b" emissiveIntensity={0.2} />
      </mesh>
      <mesh position={[0, 4.55, -4.6]}>
        <boxGeometry args={[18, 0.04, 0.04]} />
        <meshStandardMaterial color="#60a5fa" emissive="#60a5fa" emissiveIntensity={0.8} />
      </mesh>
    </group>
  );
}

function ZoneProps({ zones }: { zones: SemanticZone[] }) {
  const byKind = new Map(zones.map((zone) => [zone.kind, zone]));
  const idle = byKind.get("idle");
  const thinking = byKind.get("thinking");
  const offline = byKind.get("offline");
  const foreman = byKind.get("foreman");

  return (
    <group>
      {idle ? (
        <>
          <ModelInstance name="shelf-end" position={[idle.center[0] - 1.5, 0.02, idle.center[2] + 0.65]} rotation={[0, Math.PI / 2, 0]} scale={1.2} />
          <ModelInstance name="shopping-basket" position={[idle.center[0] + 1.5, 0.02, idle.center[2] + 0.9]} scale={1.1} />
        </>
      ) : null}

      {thinking ? (
        <>
          <ModelInstance name="display-fruit" position={[thinking.center[0], 0.02, thinking.center[2] + 0.75]} scale={1.1} />
          <ModelInstance name="shopping-basket" position={[thinking.center[0] + 1.1, 0.02, thinking.center[2] + 0.9]} scale={0.95} />
        </>
      ) : null}

      {offline ? (
        <ModelInstance name="freezer" position={[offline.center[0], 0.02, offline.center[2] + 0.8]} scale={1.25} />
      ) : null}

      {foreman ? (
        <ModelInstance name="cash-register" position={[foreman.center[0], 0.02, foreman.center[2] + 0.8]} scale={1.5} />
      ) : null}
    </group>
  );
}

export function PanoramaRoom({ zones }: { zones: SemanticZone[] }) {
  return (
    <group>
      <RoomShell />
      <ZoneProps zones={zones} />
    </group>
  );
}

useGLTF.preload(modelUrl("floor"));
useGLTF.preload(modelUrl("wall"));
useGLTF.preload(modelUrl("wall-corner"));
useGLTF.preload(modelUrl("column"));
useGLTF.preload(modelUrl("shelf-end"));
useGLTF.preload(modelUrl("cash-register"));
useGLTF.preload(modelUrl("freezer"));
useGLTF.preload(modelUrl("shopping-basket"));
useGLTF.preload(modelUrl("display-fruit"));
