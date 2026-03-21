import type { GroupPresentation, PresentationSlot } from "../types";

function buildEmptySlot(index: number): PresentationSlot {
  return {
    slot_id: `slot-${index}`,
    index,
    card: null,
  };
}

export function ensurePresentation(presentation: GroupPresentation | null | undefined): GroupPresentation {
  const slots = Array.isArray(presentation?.slots) ? presentation.slots : [];
  const slotsById = new Map<string, PresentationSlot>();
  for (const slot of slots) {
    const normalizedId = String(slot?.slot_id || "").trim();
    if (!normalizedId) continue;
    slotsById.set(normalizedId, slot);
  }

  return {
    v: Number(presentation?.v || 1) || 1,
    updated_at: String(presentation?.updated_at || "").trim(),
    highlight_slot_id: String(presentation?.highlight_slot_id || "").trim(),
    slots: Array.from({ length: 4 }, (_, index) => {
      const slotId = `slot-${index + 1}`;
      return slotsById.get(slotId) || buildEmptySlot(index + 1);
    }),
  };
}

export function findPresentationSlot(
  presentation: GroupPresentation | null | undefined,
  slotId: string,
): PresentationSlot | null {
  const normalizedSlotId = String(slotId || "").trim();
  if (!normalizedSlotId) return null;
  return ensurePresentation(presentation).slots.find((slot) => slot.slot_id === normalizedSlotId) || null;
}
