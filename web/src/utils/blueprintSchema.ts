// ProjectBlueprint schema validation
// LLM-generated blueprints are validated before rendering

import type { ProjectBlueprint } from "../types";

const MAX_BLOCKS = 500;
const MAX_AXIS = 20;

interface ValidationResult {
  valid: boolean;
  errors: string[];
}

/** Validate a ProjectBlueprint JSON object (from LLM or context) */
export function validateBlueprint(raw: unknown): ValidationResult {
  const errors: string[] = [];

  if (!raw || typeof raw !== "object") {
    return { valid: false, errors: ["Blueprint must be a non-null object"] };
  }
  const obj = raw as Record<string, unknown>;

  // version
  if (obj.version !== 1) {
    errors.push(`version must be 1, got ${String(obj.version)}`);
  }

  // style_note
  if (typeof obj.style_note !== "string") {
    errors.push("style_note must be a string");
  }

  // blockScale
  if (typeof obj.blockScale !== "number" || obj.blockScale <= 0) {
    errors.push("blockScale must be a positive number");
  }

  // gridSize
  if (!Array.isArray(obj.gridSize) || obj.gridSize.length !== 3) {
    errors.push("gridSize must be a [number, number, number] tuple");
  } else {
    for (let i = 0; i < 3; i++) {
      const v = obj.gridSize[i] as number;
      if (typeof v !== "number" || v < 1 || v > MAX_AXIS) {
        errors.push(`gridSize[${i}] must be 1..${MAX_AXIS}, got ${String(v)}`);
      }
    }
  }

  // blocks
  if (!Array.isArray(obj.blocks)) {
    errors.push("blocks must be an array");
    return { valid: false, errors };
  }

  const blocks = obj.blocks as Array<Record<string, unknown>>;

  if (blocks.length === 0) {
    errors.push("blocks must not be empty");
  }
  if (blocks.length > MAX_BLOCKS) {
    errors.push(`blocks.length (${blocks.length}) exceeds max ${MAX_BLOCKS}`);
  }

  const gs = Array.isArray(obj.gridSize) ? (obj.gridSize as number[]) : null;

  // Validate each block: types, integer coords, range, order continuity (0..n-1)
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i];
    if (typeof b.x !== "number" || typeof b.y !== "number" || typeof b.z !== "number") {
      errors.push(`blocks[${i}]: x/y/z must be numbers`);
    } else {
      // Integer check
      if (!Number.isInteger(b.x) || !Number.isInteger(b.y) || !Number.isInteger(b.z)) {
        errors.push(`blocks[${i}]: x/y/z must be integers`);
      }
      // Coordinate range check against gridSize
      if (gs && gs.length === 3) {
        if ((b.x as number) < 0 || (b.x as number) >= gs[0]) {
          errors.push(`blocks[${i}]: x=${b.x} out of range [0, ${gs[0]})`);
        }
        if ((b.y as number) < 0 || (b.y as number) >= gs[1]) {
          errors.push(`blocks[${i}]: y=${b.y} out of range [0, ${gs[1]})`);
        }
        if ((b.z as number) < 0 || (b.z as number) >= gs[2]) {
          errors.push(`blocks[${i}]: z=${b.z} out of range [0, ${gs[2]})`);
        }
      }
    }
    if (typeof b.color !== "string" || !b.color) {
      errors.push(`blocks[${i}]: color must be a non-empty string`);
    }
    if (typeof b.order !== "number") {
      errors.push(`blocks[${i}]: order must be a number`);
    } else if (b.order !== i) {
      errors.push(`blocks[${i}]: order must be sequential 0..n-1 (expected ${i}, got ${b.order})`);
    }
  }

  return { valid: errors.length === 0, errors };
}

/** Parse and validate raw JSON, returning a typed ProjectBlueprint or null */
export function parseBlueprint(raw: unknown): ProjectBlueprint | null {
  const result = validateBlueprint(raw);
  if (!result.valid) return null;
  return raw as ProjectBlueprint;
}
