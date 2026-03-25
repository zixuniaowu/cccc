import { useEffect, useState } from "react";
import type { PetPeerContextResponse } from "../../services/api";
import { fetchPetPeerContext } from "../../services/api";
import { derivePetPersonaPolicy, type PetPersonaPolicy } from "./petPersona";

export type PetPeerContext = {
  persona: string;
  help: string;
  prompt: string;
  snapshot: string;
  policy: PetPersonaPolicy;
  source: "help" | "default";
};

export function buildPetPeerContext(raw?: Partial<PetPeerContextResponse> | null): PetPeerContext {
  const persona = String(raw?.persona || "").trim();
  const help = String(raw?.help || "").trim();
  const prompt = String(raw?.prompt || "").trim();
  const snapshot = String(raw?.snapshot || "").trim();

  return {
    persona,
    help,
    prompt,
    snapshot,
    policy: derivePetPersonaPolicy(persona || prompt),
    source: raw?.source === "help" ? "help" : "default",
  };
}

export function usePetPeerContext(input: {
  groupId: string | null | undefined;
}): PetPeerContext {
  const groupId = String(input.groupId || "").trim();
  const [state, setState] = useState<{
    groupId: string;
    rawContext: Partial<PetPeerContextResponse> | null;
  }>({
    groupId: "",
    rawContext: null,
  });

  useEffect(() => {
    if (!groupId) return;

    let cancelled = false;
    void fetchPetPeerContext(groupId)
      .then((resp) => {
        if (cancelled) return;
        if (!resp.ok) {
          setState({
            groupId,
            rawContext: null,
          });
          return;
        }
        setState({
          groupId,
          rawContext: resp.result || null,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setState({
          groupId,
          rawContext: null,
        });
      });

    return () => {
      cancelled = true;
    };
  }, [groupId]);

  if (!groupId || state.groupId !== groupId) {
    return buildPetPeerContext(null);
  }

  return buildPetPeerContext(state.rawContext);
}
