import { useWebPetDrag } from "./useWebPetDrag";
import { CatCanvas } from "./CatCanvas";
import type { CatState, PetReaction } from "./types";
import { WEB_PET_BUBBLE_SIZE } from "./constants";

interface WebPetBubbleProps {
  state: CatState;
  hint: string;
  reaction: PetReaction;
}

export function WebPetBubble({ state, hint, reaction }: WebPetBubbleProps) {
  const { isDragging, handlers } = useWebPetDrag();

  return (
    <div
      {...handlers}
      className={`pointer-events-auto flex items-center justify-center transition-transform ${
        isDragging ? "cursor-grabbing scale-105" : "cursor-grab"
      }`}
      style={{
        width: WEB_PET_BUBBLE_SIZE,
        height: WEB_PET_BUBBLE_SIZE,
        touchAction: "none",
      }}
      role="button"
      tabIndex={0}
      aria-label={hint ? `Web Pet: ${hint}` : "Web Pet"}
    >
      <CatCanvas state={state} hint={hint} reaction={reaction} />
    </div>
  );
}
