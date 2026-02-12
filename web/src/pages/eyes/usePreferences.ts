import { useState, useCallback } from "react";

const STORAGE_KEY = "cccc-eyes-prefs";

export interface EyesPreferences {
  voiceEnabled: boolean;
  autoListen: boolean;
  lastGroupId: string | null;
  screenWatch: boolean;
  /** Screen capture interval in seconds */
  screenInterval: number;
  /** Custom prompt for screen capture AI analysis */
  screenPrompt: string;
}

const DEFAULT_SCREEN_PROMPT =
  "[自动截屏] 这是用户当前桌面截图。如有有趣的内容或建议请简短评论。无特别发现则回复\"无特别发现\"。";

const DEFAULTS: EyesPreferences = {
  voiceEnabled: true,
  autoListen: false,
  lastGroupId: null,
  screenWatch: false,
  screenInterval: 30,
  screenPrompt: DEFAULT_SCREEN_PROMPT,
};

export { DEFAULT_SCREEN_PROMPT };

function loadPrefs(): EyesPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return { ...DEFAULTS, ...parsed };
    }
  } catch {}
  return { ...DEFAULTS };
}

function savePrefs(prefs: EyesPreferences) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {}
}

/**
 * Persistent user preferences via localStorage.
 * Returns current prefs and an update function that merges partial updates.
 */
export function usePreferences() {
  const [prefs, setPrefs] = useState<EyesPreferences>(loadPrefs);

  const update = useCallback((partial: Partial<EyesPreferences>) => {
    setPrefs((prev) => {
      const next = { ...prev, ...partial };
      savePrefs(next);
      return next;
    });
  }, []);

  return { prefs, update };
}
