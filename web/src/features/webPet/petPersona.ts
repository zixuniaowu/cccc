import { useEffect, useState } from "react";
import { fetchGroupPrompts } from "../../services/api";
import { parseHelpMarkdown } from "../../utils/helpMarkdown";
import { getDefaultPetPersonaSeed } from "../../utils/rolePresets";

export type PetPersonaPolicy = {
  compactMessageEvents: boolean;
  autoRestartActors: boolean;
  autoCompleteTasks: boolean;
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

  // Pet peer should inherit normal peer action surface by default.
  // Persona text can explicitly narrow that authority when needed.
  const enableActorRestart = includesAny(content, [
    "auto restart actor",
    "auto restart actors",
    "allow auto restart",
    "允许自动重启 actor",
    "允许自动重启 actors",
    "允许自动重启",
    "自动重启 actor",
    "自动重启 actors",
    "actor を自動再起動",
    "actors を自動再起動",
    "actorを自動再起動",
    "actorsを自動再起動",
    "自動で actor を再起動",
    "自動で actorを再起動",
    "自動再起動を許可",
  ]);

  const disableTaskAutoClose = includesAny(content, [
    "do not complete task",
    "don't complete task",
    "do not close task",
    "don't close task",
    "never complete task",
    "禁止完成 task",
    "禁止关闭 task",
    "不要自动完成 task",
    "不要自动关闭 task",
    "不要自动收口 task",
    "不要自动完成任务",
    "不要自动收口",
    "禁止自动收口",
    "task を完了しない",
    "taskを完了しない",
    "task を閉じない",
    "taskを閉じない",
    "タスクを自動完了しない",
    "タスクを自動で完了しない",
    "タスクを自動クローズしない",
    "タスクを自動で閉じない",
    "自動収束しない",
  ]);

  return {
    compactMessageEvents,
    autoRestartActors: enableActorRestart,
    autoCompleteTasks: !disableTaskAutoClose,
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
