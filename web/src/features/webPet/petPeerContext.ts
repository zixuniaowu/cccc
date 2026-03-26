import { useEffect, useState } from "react";
import type { PetPeerContextResponse } from "../../services/api";
import { fetchPetPeerContext } from "../../services/api";
import { derivePetPersonaPolicy, type PetPersonaPolicy } from "./petPersona";
import type { PetReminder } from "./types";

export type PetPeerContext = {
  decisions: PetReminder[];
  persona: string;
  help: string;
  prompt: string;
  snapshot: string;
  policy: PetPersonaPolicy;
  source: "help" | "default";
};

function mapDecision(raw: NonNullable<PetPeerContextResponse["decisions"]>[number]): PetReminder | null {
  const id = String(raw?.id || "").trim();
  const kind = String(raw?.kind || "").trim();
  const fingerprint = String(raw?.fingerprint || "").trim();
  const actionType = String(raw?.action?.type || "").trim();
  if (!id || !kind || !fingerprint || !actionType) return null;

  const action =
    actionType === "restart_actor"
      ? {
          type: "restart_actor" as const,
          groupId: String(raw?.action?.group_id || "").trim(),
          actorId: String(raw?.action?.actor_id || "").trim(),
        }
      : {
          type: "send_suggestion" as const,
          groupId: String(raw?.action?.group_id || "").trim(),
          text: String(raw?.action?.text || "").trim(),
          to: Array.isArray(raw?.action?.to)
            ? raw.action.to.map((entry) => String(entry || "").trim()).filter(Boolean)
            : undefined,
          replyTo: String(raw?.action?.reply_to || "").trim() || undefined,
        };

  if (action.type === "restart_actor" && (!action.groupId || !action.actorId)) return null;
  if (action.type === "send_suggestion" && !action.text) return null;

  return {
    id,
    kind: kind === "actor_down" ? "actor_down" : "suggestion",
    priority: Number(raw?.priority || 0),
    summary: String(raw?.summary || "").trim(),
    suggestion: String(raw?.suggestion || "").trim() || undefined,
    suggestionPreview: String(raw?.suggestion_preview || "").trim() || undefined,
    agent: String(raw?.agent || "").trim(),
    ephemeral: !!raw?.ephemeral,
    source: {
      eventId: String(raw?.source?.event_id || "").trim() || undefined,
      taskId: String(raw?.source?.task_id || "").trim() || undefined,
      actorId: String(raw?.source?.actor_id || "").trim() || undefined,
      actorRole: String(raw?.source?.actor_role || "").trim() || undefined,
      errorReason: String(raw?.source?.error_reason || "").trim() || undefined,
      suggestionKind:
        String(raw?.source?.suggestion_kind || "").trim() === "reply_required"
          ? "reply_required"
          : String(raw?.source?.suggestion_kind || "").trim() === "mention"
            ? "mention"
            : undefined,
    },
    fingerprint,
    action,
  };
}

export function buildPetPeerContext(raw?: Partial<PetPeerContextResponse> | null): PetPeerContext {
  const persona = String(raw?.persona || "").trim();
  const help = String(raw?.help || "").trim();
  const prompt = String(raw?.prompt || "").trim();
  const snapshot = String(raw?.snapshot || "").trim();
  const decisions = Array.isArray(raw?.decisions)
    ? raw.decisions.map((item) => mapDecision(item)).filter((item): item is PetReminder => item !== null)
    : [];

  return {
    decisions,
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
        setState({
          groupId,
          rawContext: resp.ok ? resp.result || null : null,
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
