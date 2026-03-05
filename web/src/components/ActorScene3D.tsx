import { Suspense, useMemo, useRef, useState, useEffect, useCallback } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { ActorCharacter } from "./ActorCharacter";
import { ProjectBuilding } from "./ProjectBuilding";
import { MCGround, InstancedBeds, type BedInstance } from "./MCFurniture";
import { useCharacterAnimation, type LayoutItem } from "../hooks/useCharacterAnimation";
import type { AgentState, Actor, Task, ProjectBlueprint, GroupContext } from "../types";
import * as THREE from "three";

interface ActorScene3DProps {
  agents: AgentState[];
  actors?: Actor[];
  tasks?: Task[];
  tasksSummary?: GroupContext["tasks_summary"];
  panoramaBlueprint?: ProjectBlueprint | null;
  projectStatus?: string | null;
  isDark: boolean;
  groupId?: string;
  className?: string;
}

// ── Worker layout (surround center building) ──

function siteRadius(agentCount: number, blueprint?: ProjectBlueprint | null): number {
  const base = Math.max(2, agentCount * 0.6 + 1);
  if (blueprint) {
    const footprint = Math.max(blueprint.gridSize[0], blueprint.gridSize[2]) * blueprint.blockScale / 2;
    return Math.max(base, footprint + 3.5);
  }
  return base;
}

/** Place on arc around origin, facing center (mesh default faces -Z) */
function arcPlace(angle: number, r: number): { pos: [number, number, number]; rotY: number } {
  const x = Math.sin(angle) * r;
  const z = Math.cos(angle) * r;
  const rotY = Math.atan2(-x, -z) + Math.PI;
  return { pos: [x, 0, z], rotY };
}

function computeWorkerLayout(
  agents: AgentState[],
  actorMap: Map<string, Actor>,
  blueprint?: ProjectBlueprint | null,
  tasks?: Task[],
): Map<string, LayoutItem> {
  const count = agents.length;
  if (count === 0) return new Map();

  // Build task status lookup
  const taskStatusMap = new Map<string, string>();
  if (tasks) {
    for (const t of tasks) {
      if (t.status) taskStatusMap.set(t.id, t.status);
    }
  }

  const radius = siteRadius(count, blueprint);
  const workRadius = radius * 0.9;
  const idleRadius = radius * 1.2;
  const bedRadius = radius + 2.5;

  const foremanIdx = agents.findIndex((a) => actorMap.get(a.id)?.role === "foreman");

  // Classify agents into working / idle
  const workingIds: string[] = [];
  const idleIds: string[] = [];
  for (let i = 0; i < count; i++) {
    if (i === foremanIdx) continue;
    const agent = agents[i];
    const actor = actorMap.get(agent.id);
    const running = actor?.running !== false && actor?.enabled !== false;
    const ts = agent.active_task_id ? taskStatusMap.get(agent.active_task_id) : undefined;
    const taskDone = ts === "done" || ts === "archived";
    if (running && agent.active_task_id && !taskDone) {
      workingIds.push(agent.id);
    } else {
      idleIds.push(agent.id);
    }
  }

  // Beds: back arc, evenly distributed for all agents
  const bedSpread = Math.PI * 0.8;
  function bedPlace(idx: number): { pos: [number, number, number]; rotY: number } {
    const t = count > 1 ? idx / (count - 1) : 0.5;
    const angle = Math.PI + (t - 0.5) * bedSpread;
    return arcPlace(angle, bedRadius);
  }

  const items = new Map<string, LayoutItem>();
  let bedIdx = 0;

  // Foreman: front-right supervisory position
  if (foremanIdx >= 0) {
    const agentId = agents[foremanIdx].id;
    const { pos, rotY } = arcPlace(Math.PI * 0.15, workRadius * 1.1);
    const bed = bedPlace(bedIdx++);
    items.set(agentId, {
      agentId, charPos: pos, charRotY: rotY,
      bedPos: bed.pos, bedRotY: bed.rotY,
      isForeman: true,
    });
  }

  // Working agents: front arc (±45° around center front)
  const workArc = Math.PI * 0.5;
  for (let i = 0; i < workingIds.length; i++) {
    const t = workingIds.length > 1 ? i / (workingIds.length - 1) : 0.5;
    const angle = (t - 0.5) * workArc;
    const { pos, rotY } = arcPlace(angle, workRadius);
    const bed = bedPlace(bedIdx++);
    items.set(workingIds[i], {
      agentId: workingIds[i], charPos: pos, charRotY: rotY,
      bedPos: bed.pos, bedRotY: bed.rotY,
      isForeman: false,
    });
  }

  // Idle agents: back arc (~120°–240°)
  const idleArc = Math.PI * 0.7;
  for (let i = 0; i < idleIds.length; i++) {
    const t = idleIds.length > 1 ? i / (idleIds.length - 1) : 0.5;
    const angle = Math.PI + (t - 0.5) * idleArc;
    const { pos, rotY } = arcPlace(angle, idleRadius);
    const bed = bedPlace(bedIdx++);
    items.set(idleIds[i], {
      agentId: idleIds[i], charPos: pos, charRotY: rotY,
      bedPos: bed.pos, bedRotY: bed.rotY,
      isForeman: false,
    });
  }

  return items;
}

// ── Scene ──

interface SceneProps {
  agents: AgentState[];
  actors?: Actor[];
  tasks?: Task[];
  tasksSummary?: GroupContext["tasks_summary"];
  panoramaBlueprint?: ProjectBlueprint | null;
  projectStatus?: string | null;
  isDark: boolean;
  groupId?: string;
  camZ: number;
}

function Scene({ agents, actors, tasks, tasksSummary, panoramaBlueprint, projectStatus, isDark, groupId: _groupId, camZ }: SceneProps) {
  const characterRefs = useRef<Map<string, THREE.Group>>(new Map());

  const actorMap = useMemo(() => {
    const m = new Map<string, Actor>();
    for (const a of actors || []) m.set(a.id, a);
    return m;
  }, [actors]);

  const taskMap = useMemo(() => {
    const m = new Map<string, { idx: number; name: string; status?: string | null }>();
    if (!tasks || tasks.length === 0) return m;
    for (let i = 0; i < tasks.length; i++) {
      m.set(tasks[i].id, { idx: i, name: tasks[i].name, status: tasks[i].status });
    }
    return m;
  }, [tasks]);

  // Build target: working agents point to center building [0,0,0]
  const buildTargetMap = useMemo(() => {
    const map = new Map<string, [number, number, number]>();
    for (const agent of agents) {
      if (agent.active_task_id) {
        map.set(agent.id, [0, 0, 0]);
      }
    }
    if (tasks) {
      const agentIds = new Set(agents.map((a) => a.id));
      for (const task of tasks) {
        if (task.assignee && agentIds.has(task.assignee) && !map.has(task.assignee)) {
          map.set(task.assignee, [0, 0, 0]);
        }
      }
    }
    return map;
  }, [agents, tasks]);

  const layout = useMemo(
    () => computeWorkerLayout(agents, actorMap, panoramaBlueprint, tasks),
    [agents, actorMap, panoramaBlueprint, tasks],
  );

  const taskStatusMap = useMemo(() => {
    const m = new Map<string, string>();
    if (!tasks) return m;
    for (const t of tasks) {
      if (t.status) m.set(t.id, t.status);
    }
    return m;
  }, [tasks]);

  useCharacterAnimation({
    agents, actorMap, layout, buildTargetMap, characterRefs, taskStatusMap,
    staticMode: true,
  });

  const bedInstances: BedInstance[] = useMemo(
    () => [...layout.values()].map((item) => ({ position: item.bedPos, rotationY: item.bedRotY })),
    [layout],
  );

  return (
    <>
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

      <MCGround />

      <ProjectBuilding blueprint={panoramaBlueprint} tasks={tasks} tasksSummary={tasksSummary} isDark={isDark} projectStatus={projectStatus} />

      <InstancedBeds beds={bedInstances} />

      {agents.map((agent) => {
        const actor = actorMap.get(agent.id);
        const running = actor?.running !== false && actor?.enabled !== false;
        const item = layout.get(agent.id);
        const taskEntry = agent.active_task_id ? taskMap.get(agent.active_task_id) : undefined;
        return (
          <ActorCharacter
            key={agent.id}
            ref={(el: THREE.Group | null) => {
              if (el) characterRefs.current.set(agent.id, el);
              else characterRefs.current.delete(agent.id);
            }}
            agent={agent}
            position={item?.charPos || [0, 0, 0]}
            rotationY={item?.charRotY}
            isDark={isDark}
            role={actor?.role}
            runtime={actor?.runtime}
            title={actor?.title}
            isRunning={running}
            idleSeconds={actor?.idle_seconds}
            activeTaskName={taskEntry?.name.replace(/^T\d+:\s*/, "")}
            taskStatus={taskEntry?.status || undefined}
            focus={agent.focus || undefined}
            blockerText={agent.blockers?.length ? agent.blockers[0] : undefined}
          />
        );
      })}

      <OrbitControls
        makeDefault
        enableDamping={false}
        enablePan={true}
        enableZoom={true}
        enableRotate={true}
        minDistance={2}
        maxDistance={camZ * 3}
        maxPolarAngle={Math.PI / 2.05}
        target={[0, 0.5, 0]}
        mouseButtons={{ LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE }}
        screenSpacePanning={true}
      />
    </>
  );
}

// ── WebGPU / WebGL renderer selection ──

type RenderMode = "loading" | "webgpu" | "webgl";

export function ActorScene3D({ agents, actors, tasks, tasksSummary, panoramaBlueprint, projectStatus, isDark, groupId, className }: ActorScene3DProps) {
  const camZ = useMemo(() => {
    const radius = siteRadius(agents.length, panoramaBlueprint);
    const sceneExtent = radius + 2; // beds at radius + 2.5
    return Math.max(5, sceneExtent * 2 + 1);
  }, [agents.length, panoramaBlueprint]);

  // Phase 1: detect WebGPU + dynamically load three/webgpu module
  const [renderMode, setRenderMode] = useState<RenderMode>("loading");
  const gpuModRef = useRef<any>(null);

  useEffect(() => {
    // WebGPU is opt-in via ?webgpu URL param (R3F v8 has limited WebGPU compat)
    const wantGPU =
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).has("webgpu");

    if (!wantGPU || !navigator.gpu) {
      setRenderMode("webgl");
      return;
    }

    let cancelled = false;
    import("three/webgpu")
      .then((mod) => {
        if (cancelled) return;
        gpuModRef.current = mod;
        setRenderMode("webgpu");
      })
      .catch(() => {
        if (cancelled) return;
        setRenderMode("webgl");
      });
    return () => { cancelled = true; };
  }, []);

  // Stable gl factory for WebGPU -- avoids re-creation on every render.
  // Uses a render-guard so R3F can keep frameloop="always" (required for
  // OrbitControls) while WebGPURenderer.init() resolves asynchronously.
  const createWebGPURenderer = useCallback((canvas: HTMLCanvasElement) => {
    const GPU = gpuModRef.current;
    const renderer = new GPU.WebGPURenderer({
      canvas,
      antialias: true,
      alpha: false,
    });
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;

    // XR stub -- prevents R3F v8 "xr.addEventListener is not a function" error
    if (!(renderer as any).xr) {
      (renderer as any).xr = {
        addEventListener: () => {},
        removeEventListener: () => {},
        getSession: () => null,
        setSession: () => Promise.resolve(),
        enabled: false,
        isPresenting: false,
        setReferenceSpaceType: () => {},
        getReferenceSpace: () => null,
        getCamera: () => new THREE.PerspectiveCamera(),
        setAnimationLoop: () => {},
        dispose: () => {},
      };
    }

    // Guard: R3F calls gl.render() every frame via its rAF loop.
    // WebGPURenderer.render() throws if called before init() resolves,
    // so we patch it with a no-op until the backend is ready.
    const nativeRender = renderer.render.bind(renderer);
    let ready = false;
    renderer.render = (scene: THREE.Scene, camera: THREE.Camera) => {
      if (ready) nativeRender(scene, camera);
    };

    renderer.init()
      .then(() => {
        console.log("[ActorScene3D] WebGPU renderer initialized");
        ready = true;
      })
      .catch((err: unknown) => {
        console.warn("[ActorScene3D] WebGPU init failed, reloading with WebGL:", err);
        setRenderMode("webgl");
      });

    return renderer;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Placeholder while detecting WebGPU
  if (renderMode === "loading") {
    return (
      <div
        className={className}
        style={{
          minHeight: 280,
          borderRadius: 12,
          background: isDark ? "#191970" : "#87CEEB",
        }}
      />
    );
  }

  const cameraConfig = {
    position: [camZ * 0.6, camZ * 0.5, camZ] as [number, number, number],
    fov: 45,
    near: 0.1,
    far: 100,
  };

  const canvasStyle = {
    borderRadius: 12,
    background: isDark ? "#191970" : "#87CEEB",
  };

  const glProp = renderMode === "webgpu"
    ? (createWebGPURenderer as any)
    : { antialias: true, alpha: false };

  return (
    <div className={className} style={{ minHeight: 280 }}>
      <Canvas
        key={renderMode}
        frameloop="always"
        shadows={{ type: THREE.PCFShadowMap }}
        camera={cameraConfig}
        style={canvasStyle}
        gl={glProp}
      >
        <Suspense fallback={null}>
          <Scene agents={agents} actors={actors} tasks={tasks} tasksSummary={tasksSummary} panoramaBlueprint={panoramaBlueprint} projectStatus={projectStatus} isDark={isDark} groupId={groupId} camZ={camZ} />
        </Suspense>
      </Canvas>
    </div>
  );
}
