// Unified hook: resolves a Task to a Blueprint
// Predefined blueprints returned immediately; Worker validates in background (cached).
// Future LLM-generated blueprints will block on Worker validation.

import { useState, useEffect, useMemo } from "react";
import { BLUEPRINTS, type Blueprint } from "../data/blueprints";
import { matchBlueprint } from "../utils/blueprintMatcher";
import { BlueprintWorkerFacade } from "../utils/blueprintWorkerFacade";
import type { Task } from "../types";

export interface BlueprintResolverResult {
  blueprint: Blueprint | null;
  loading: boolean;
  error: string | null;
}

// Worker-validated blueprint cache (module-level, survives re-renders)
const validatedCache = new Map<string, Blueprint>();

/**
 * Resolve a Task to a Blueprint.
 *
 * - Predefined blueprints: returned immediately, Worker validates in background.
 * - Custom/LLM blueprints (future): loading=true until Worker validates.
 */
export function useBlueprintResolver(task: Task | null): BlueprintResolverResult {
  // Synchronous predefined match (fast path)
  const predefined = useMemo((): Blueprint | null => {
    if (!task) return null;
    const match = matchBlueprint(task);
    return BLUEPRINTS[match.blueprintId] ?? null;
  }, [task?.id, task?.name, task?.goal]);

  // Worker-validated version (replaces predefined once ready)
  const [workerBp, setWorkerBp] = useState<Blueprint | null>(null);

  useEffect(() => {
    if (!predefined) {
      setWorkerBp(null);
      return;
    }

    // Cache hit: use previously validated blueprint
    const cached = validatedCache.get(predefined.id);
    if (cached) {
      setWorkerBp(cached);
      return;
    }

    // Validate through Worker (background, non-blocking)
    let cancelled = false;
    const facade = BlueprintWorkerFacade.getInstance();
    const rawBlocks = predefined.blocks.map(({ x, y, z, color }) => ({ x, y, z, color }));

    facade
      .validate(rawBlocks, predefined.gridSize)
      .then(({ blocks, gridSize }) => {
        if (cancelled) return;
        const bp: Blueprint = { ...predefined, blocks, gridSize };
        validatedCache.set(predefined.id, bp);
        setWorkerBp(bp);
      })
      .catch(() => {
        // Fallback: use predefined as-is on Worker error
        if (cancelled) return;
        validatedCache.set(predefined.id, predefined);
        setWorkerBp(predefined);
      });

    return () => {
      cancelled = true;
    };
  }, [predefined]);

  // Return predefined immediately; swap to Worker-validated once cached
  const blueprint = workerBp ?? predefined;
  return {
    blueprint,
    loading: false,
    error: blueprint ? null : task ? "no matching blueprint" : null,
  };
}
