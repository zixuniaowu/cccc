import { Theme } from "../types";
import { classNames } from "../utils/classNames";

interface ThemeToggleProps {
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  isDark: boolean;
}

export function ThemeToggle({ theme, onThemeChange, isDark }: ThemeToggleProps) {
  const themes: { value: Theme; label: string; icon: string }[] = [
    { value: "light", label: "Light", icon: "☀️" },
    { value: "dark", label: "Dark", icon: "🌙" },
    { value: "system", label: "System", icon: "💻" },
  ];

  return (
    <div className="flex items-center gap-1 p-1 rounded-lg bg-slate-800/50 dark:bg-slate-800/50">
      {themes.map((t) => (
        <button
          key={t.value}
          onClick={() => onThemeChange(t.value)}
          className={classNames(
            "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-all min-h-[36px]",
            theme === t.value
              ? isDark
                ? "bg-slate-700 text-white shadow-sm"
                : "bg-white text-gray-900 shadow-sm"
              : isDark
                ? "text-slate-400 hover:text-slate-200"
                : "text-gray-500 hover:text-gray-700"
          )}
          aria-label={`Switch to ${t.label} theme`}
          aria-pressed={theme === t.value}
        >
          <span aria-hidden="true">{t.icon}</span>
          <span className="hidden sm:inline">{t.label}</span>
        </button>
      ))}
    </div>
  );
}

// Compact version for header
export function ThemeToggleCompact({ theme, onThemeChange, isDark }: ThemeToggleProps) {
  const nextTheme = (): Theme => {
    if (theme === "light") return "dark";
    if (theme === "dark") return "system";
    return "light";
  };

  const icon = theme === "light" ? "☀️" : theme === "dark" ? "🌙" : "💻";
  const label = theme === "light" ? "Light" : theme === "dark" ? "Dark" : "System";

  return (
    <button
      onClick={() => onThemeChange(nextTheme())}
      className={classNames(
        "flex items-center justify-center w-9 h-9 rounded-lg transition-colors min-h-[44px] min-w-[44px]",
        isDark
          ? "bg-slate-800 hover:bg-slate-700 text-slate-300"
          : "bg-gray-100 hover:bg-gray-200 text-gray-600"
      )}
      title={`Theme: ${label}. Click to change.`}
      aria-label={`Current theme: ${label}. Click to cycle through themes.`}
    >
      <span className="text-lg" aria-hidden="true">{icon}</span>
    </button>
  );
}
