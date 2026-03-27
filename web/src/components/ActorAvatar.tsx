import { memo, useMemo } from "react";
import { classNames } from "../utils/classNames";

const RUNTIME_LOGO_BASE = import.meta.env.BASE_URL;
const RUNTIME_LOGO: Record<string, string> = {
  claude: `${RUNTIME_LOGO_BASE}logos/claude.png`,
  codex: `${RUNTIME_LOGO_BASE}logos/codex.png`,
  gemini: `${RUNTIME_LOGO_BASE}logos/gemini.png`,
};

export type ActorAvatarProps = {
  runtime?: string | null;
  title?: string | null;
  isUser?: boolean;
  isDark: boolean;
  accentRingClassName?: string | null;
  sizeClassName?: string;
  textClassName?: string;
  className?: string;
};

export const ActorAvatar = memo(function ActorAvatar({
  runtime,
  title,
  isUser = false,
  isDark,
  accentRingClassName,
  sizeClassName = "h-8 w-8",
  textClassName = "text-xs",
  className,
}: ActorAvatarProps) {
  const logoSrc = useMemo(() => {
    if (isUser) return null;
    const normalizedRuntime = String(runtime || "").trim().toLowerCase();
    return normalizedRuntime ? RUNTIME_LOGO[normalizedRuntime] || null : null;
  }, [isUser, runtime]);

  const fallbackText = isUser ? "U" : (String(title || "").trim() || "?")[0].toUpperCase();

  return (
    <div
      className={classNames(
        "flex flex-shrink-0 items-center justify-center overflow-hidden rounded-full font-bold shadow-sm",
        sizeClassName,
        textClassName,
        isUser
          ? "bg-gradient-to-br from-blue-500 to-blue-600 text-white"
          : isDark
            ? "bg-slate-700 text-slate-200"
            : "border border-gray-200 bg-white text-gray-700",
        !isUser && accentRingClassName ? `ring-1 ring-inset ${accentRingClassName}` : "",
        className,
      )}
    >
      {logoSrc ? <img src={logoSrc} alt="" className="h-full w-full object-cover" /> : fallbackText}
    </div>
  );
});
