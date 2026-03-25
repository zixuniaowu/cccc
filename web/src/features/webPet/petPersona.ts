import { useEffect, useState } from "react";
import { fetchGroupPrompts } from "../../services/api";
import { parseHelpMarkdown } from "../../utils/helpMarkdown";
import { getDefaultPetPersonaSeed } from "../../utils/rolePresets";

export type PetPersonaPolicy = {
  compactMessageEvents: boolean;
};

const DEFAULT_PET_PERSONA = getDefaultPetPersonaSeed();
const DEFAULT_PET_PERSONA_POLICY = derivePetPersonaPolicy("");

function normalize(text: string): string {
  return String(text || "").trim().toLowerCase();
}

function includesAny(content: string, patterns: string[]): boolean {
  return patterns.some((pattern) => content.includes(pattern));
}

export function derivePetPersonaPolicy(persona: string): PetPersonaPolicy {
  const content = normalize(persona) || normalize(DEFAULT_PET_PERSONA);
  const compactMessageEvents = includesAny(content, [
    "low-noise",
    "low noise",
    "terse",
    "route and exit",
    "不要复述",
    "低噪声",
    "简短",
    "只说状态",
    "低ノイズ",
    "簡潔",
    "短く",
    "要点だけ",
    "状態だけ",
  ]);
  return {
    compactMessageEvents,
  };
}

export function usePetPersonaPolicy(groupId: string | null | undefined): PetPersonaPolicy {
  const gid = String(groupId || "").trim();
  const [state, setState] = useState<{
    groupId: string;
    policy: PetPersonaPolicy;
  }>({
    groupId: "",
    policy: DEFAULT_PET_PERSONA_POLICY,
  });

  useEffect(() => {
    if (!gid) return;

    let cancelled = false;

    void fetchGroupPrompts(gid)
      .then((resp) => {
        if (cancelled || !resp.ok) return;
        const helpContent = String(resp.result?.help?.content || "");
        const petPersona = parseHelpMarkdown(helpContent).pet;
        setState({
          groupId: gid,
          policy: derivePetPersonaPolicy(petPersona),
        });
      })
      .catch(() => {
        if (cancelled) return;
        setState({
          groupId: gid,
          policy: DEFAULT_PET_PERSONA_POLICY,
        });
      });

    return () => {
      cancelled = true;
    };
  }, [gid]);

  if (!gid || state.groupId !== gid) {
    return DEFAULT_PET_PERSONA_POLICY;
  }

  return state.policy;
}
