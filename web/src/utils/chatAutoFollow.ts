export interface ChatTailSnapshot {
  count: number;
  tailKey: string | number | null;
}

export function getChatTailSnapshot(
  tailKey: string | number | null,
  count: number,
): ChatTailSnapshot {
  return {
    count: Math.max(0, Number(count) || 0),
    tailKey,
  };
}

export function shouldAutoFollowOnTailAppend(
  prev: ChatTailSnapshot,
  next: ChatTailSnapshot,
): boolean {
  if (prev.count <= 0 || next.count <= prev.count) return false;
  if (prev.tailKey == null || next.tailKey == null) return false;
  return prev.tailKey !== next.tailKey;
}
