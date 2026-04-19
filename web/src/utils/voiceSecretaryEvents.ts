export const VOICE_SECRETARY_PROMPT_DRAFT_EVENT = "cccc:voice-secretary-prompt-draft";

export type VoiceSecretaryPromptDraftEventDetail = {
  groupId: string;
  requestId: string;
  status: string;
  action: string;
};

export function emitVoiceSecretaryPromptDraftEvent(detail: VoiceSecretaryPromptDraftEventDetail): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<VoiceSecretaryPromptDraftEventDetail>(VOICE_SECRETARY_PROMPT_DRAFT_EVENT, { detail }));
}
