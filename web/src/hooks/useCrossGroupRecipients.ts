// useCrossGroupRecipients - Manage recipient actors for cross-group messaging
// Extracts recipientActors, recipientActorsBusy, destGroupScopeLabel state and sync logic

import { useState, useRef, useEffect } from "react";
import * as api from "../services/api";
import type { Actor, GroupDoc } from "../types";

interface UseCrossGroupRecipientsOptions {
  /** Current group's actors */
  actors: Actor[];
  /** Current group's document */
  groupDoc: GroupDoc | null;
  /** Currently selected group ID */
  selectedGroupId: string;
  /** Target group ID for sending (from useComposerStore.destGroupId) */
  sendGroupId: string;
}

interface UseCrossGroupRecipientsResult {
  /** Actors in the target (destination) group for recipient validation */
  recipientActors: Actor[];
  /** Whether recipientActors is being fetched */
  recipientActorsBusy: boolean;
  /** Label for the target group's active scope */
  destGroupScopeLabel: string;
  /** Set recipientActors (for direct updates like startReply) */
  setRecipientActors: React.Dispatch<React.SetStateAction<Actor[]>>;
  /** Set recipientActorsBusy (for direct updates like startReply) */
  setRecipientActorsBusy: React.Dispatch<React.SetStateAction<boolean>>;
}

function getActiveScopeLabel(doc: GroupDoc | null): string {
  if (!doc) return "";
  const key = String(doc.active_scope_key || "").trim();
  if (!key) return "";
  const scopes = Array.isArray(doc.scopes) ? doc.scopes : [];
  const hit = scopes.find((s) => String(s?.scope_key || "").trim() === key);
  const label = String(hit?.label || "").trim();
  const url = String(hit?.url || "").trim();
  return label || url;
}

export function useCrossGroupRecipients({
  actors,
  groupDoc,
  selectedGroupId,
  sendGroupId,
}: UseCrossGroupRecipientsOptions): UseCrossGroupRecipientsResult {
  // State
  const [recipientActors, setRecipientActors] = useState<Actor[]>([]);
  const [recipientActorsBusy, setRecipientActorsBusy] = useState(false);
  const [destGroupScopeLabel, setDestGroupScopeLabel] = useState("");

  // Caches
  const recipientActorsCacheRef = useRef<Record<string, Actor[]>>({});
  const groupDocCacheRef = useRef<Record<string, GroupDoc>>({});

  // Cache current group's actors
  useEffect(() => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid) return;
    recipientActorsCacheRef.current[gid] = actors;
  }, [actors, selectedGroupId]);

  // Sync destGroupScopeLabel based on sendGroupId
  useEffect(() => {
    const gid = String(sendGroupId || "").trim();
    if (!gid) {
      setDestGroupScopeLabel("");
      return;
    }

    // Same group - use current groupDoc
    if (gid === String(selectedGroupId || "").trim()) {
      setDestGroupScopeLabel(getActiveScopeLabel(groupDoc));
      if (groupDoc) groupDocCacheRef.current[gid] = groupDoc;
      return;
    }

    // Different group - check cache first
    const cached = groupDocCacheRef.current[gid];
    if (cached) {
      setDestGroupScopeLabel(getActiveScopeLabel(cached));
      return;
    }

    // Fetch from API
    let cancelled = false;
    setDestGroupScopeLabel("");
    void api.fetchGroup(gid).then((resp) => {
      if (cancelled) return;
      if (!resp.ok) {
        setDestGroupScopeLabel("");
        return;
      }
      const doc = resp.result.group;
      groupDocCacheRef.current[gid] = doc;
      setDestGroupScopeLabel(getActiveScopeLabel(doc));
    });

    return () => {
      cancelled = true;
    };
  }, [groupDoc, selectedGroupId, sendGroupId]);

  // Sync recipientActors based on sendGroupId
  useEffect(() => {
    const gid = String(sendGroupId || "").trim();
    if (!gid) {
      setRecipientActors([]);
      setRecipientActorsBusy(false);
      return;
    }

    // Same group - use current actors
    if (gid === String(selectedGroupId || "").trim()) {
      setRecipientActors(actors);
      setRecipientActorsBusy(false);
      return;
    }

    // Different group - check cache first
    const cached = recipientActorsCacheRef.current[gid];
    if (cached) {
      setRecipientActors(cached);
      setRecipientActorsBusy(false);
      return;
    }

    // Fetch from API
    let cancelled = false;
    setRecipientActorsBusy(true);
    setRecipientActors([]);
    void api
      .fetchActors(gid)
      .then((resp) => {
        if (cancelled) return;
        if (!resp.ok) {
          setRecipientActors([]);
          return;
        }
        const next = resp.result.actors || [];
        recipientActorsCacheRef.current[gid] = next;
        setRecipientActors(next);
      })
      .finally(() => {
        if (cancelled) return;
        setRecipientActorsBusy(false);
      });

    return () => {
      cancelled = true;
    };
  }, [actors, selectedGroupId, sendGroupId]);

  return {
    recipientActors,
    recipientActorsBusy,
    destGroupScopeLabel,
    setRecipientActors,
    setRecipientActorsBusy,
  };
}
