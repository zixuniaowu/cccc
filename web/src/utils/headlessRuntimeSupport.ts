const STANDARD_WEB_HEADLESS_RUNTIMES = new Set(["codex", "claude"]);

export function supportsStandardWebHeadlessRuntime(runtime: string | null | undefined): boolean {
  return STANDARD_WEB_HEADLESS_RUNTIMES.has(String(runtime || "").trim().toLowerCase());
}