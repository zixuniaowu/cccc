import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import type {
  AssistantVoiceDocument,
  AssistantVoicePromptDraft,
  AssistantVoiceTranscriptSegmentResult,
  BuiltinAssistant,
} from "../../types";
import { classNames } from "../../utils/classNames";
import { ChevronDownIcon, MaximizeIcon, MicrophoneIcon, StopIcon } from "../../components/Icons";
import { MarkdownDocumentSurface } from "../../components/document/MarkdownDocumentSurface";
import { GroupCombobox } from "../../components/GroupCombobox";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import {
  ackVoiceAssistantPromptDraft,
  appendVoiceAssistantInput,
  appendVoiceAssistantTranscriptSegment,
  archiveVoiceAssistantDocument,
  fetchAssistant,
  saveVoiceAssistantDocument,
  selectVoiceAssistantDocument,
  sendVoiceAssistantDocumentInstruction,
  transcribeVoiceAssistantAudio,
  updateAssistantSettings,
} from "../../services/api";
import { useUIStore } from "../../stores";
import { useModalA11y } from "../../hooks/useModalA11y";
import { AnimatedShinyText } from "../../registry/magicui/animated-shiny-text";
import {
  VOICE_SECRETARY_PROMPT_DRAFT_EVENT,
  type VoiceSecretaryPromptDraftEventDetail,
} from "../../utils/voiceSecretaryEvents";

type VoiceSecretaryComposerControlProps = {
  isDark: boolean;
  selectedGroupId: string;
  busy: string;
  buttonClassName?: string;
  buttonSizePx?: number;
  disabled?: boolean;
  variant?: "button" | "assistantRow";
  captureMode?: VoiceSecretaryCaptureMode;
  onCaptureModeChange?: (mode: VoiceSecretaryCaptureMode) => void;
  composerText?: string;
  composerContext?: Record<string, unknown>;
  onPromptDraft?: (text: string, opts?: { mode?: "replace" | "append" }) => void;
};

export type VoiceSecretaryCaptureMode = "document" | "instruction" | "prompt";

type BrowserSpeechRecognitionAlternative = {
  transcript: string;
};

type BrowserSpeechRecognitionResult = {
  isFinal: boolean;
  length: number;
  [index: number]: BrowserSpeechRecognitionAlternative;
};

type BrowserSpeechRecognitionResultList = {
  length: number;
  [index: number]: BrowserSpeechRecognitionResult;
};

type BrowserSpeechRecognitionEvent = {
  resultIndex: number;
  results: BrowserSpeechRecognitionResultList;
};

type BrowserSpeechRecognitionErrorEvent = {
  error?: string;
  message?: string;
};

type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives?: number;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onspeechstart?: (() => void) | null;
  onspeechend?: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

type VoiceCaptureLock = {
  ownerId: string;
  groupId: string;
  updatedAt: number;
};

type VoiceCaptureChannelMessage = {
  type?: "probe" | "alive";
  ownerId?: string;
  groupId?: string;
  sentAt?: number;
};

type VoiceTranscriptDisplayItem = {
  id: string;
  text: string;
  language: string;
  source: string;
  createdAt: number;
};

type BrowserMicrophoneSupportIssue = "" | "secure_context" | "get_user_media";
type BrowserAudioSupportIssue = BrowserMicrophoneSupportIssue | "media_recorder";
type BrowserSpeechSupportIssue = "" | "unsupported";

const VOICE_CAPTURE_LOCK_KEY = "cccc.voiceSecretary.activeCapture";
const VOICE_CAPTURE_CHANNEL_NAME = "cccc.voiceSecretary.capture";
const VOICE_CAPTURE_LOCK_TTL_MS = 30 * 1000;
const VOICE_CAPTURE_LOCK_PROBE_TIMEOUT_MS = 300;
const BROWSER_DEFAULT_MIC_LABEL = "browser_default";
const SERVICE_DEFAULT_MIC_LABEL = "service_default";
const BROWSER_SPEECH_MIN_QUIET_MS = 1_000;
const BROWSER_SPEECH_MAX_WINDOW_FALLBACK_MS = 120_000;
const BROWSER_SPEECH_MIN_MAX_WINDOW_MS = 10_000;
const VOICE_TRANSCRIPT_DISPLAY_LIMIT = 8;
const BROWSER_SPEECH_RESTART_BASE_MS = 500;
const BROWSER_SPEECH_RESTART_MAX_MS = 8000;
const BROWSER_SPEECH_MAX_TRANSIENT_ERRORS = 8;
const PROMPT_DRAFT_POLL_FALLBACK_MS = 10_000;
const BROWSER_SPEECH_RECOVERABLE_ERRORS = new Set(["no-speech", "aborted", "network", "audio-capture"]);
const BROWSER_SPEECH_FATAL_ERRORS = new Set(["not-allowed", "service-not-allowed"]);
const LOW_VALUE_BROWSER_SPEECH_FRAGMENTS = new Set([
  "嗯",
  "嗯嗯",
  "啊",
  "好",
  "好的",
  "呃",
  "额",
  "那个",
  "えー",
  "ええ",
  "あの",
  "その",
  "はい",
  "うん",
  "uh",
  "um",
  "嗯。",
  "啊。",
  "好。",
  "はい。",
]);
const VOICE_LANGUAGE_OPTION_VALUES = ["auto", "zh-CN", "en-US", "ja-JP", "ko-KR", "fr-FR", "de-DE", "es-ES"] as const;

function slugifyVoiceDocumentDownloadName(value: string): string {
  return (
    value
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9\u3040-\u30ff\u3400-\u9fff]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80) || "voice-secretary-document"
  );
}

function voiceDocumentDownloadFileName(document: AssistantVoiceDocument | null, fallbackTitle: string): string {
  const workspacePath = String(document?.document_path || document?.workspace_path || "").trim().replace(/\\/g, "/");
  const workspaceName = workspacePath.split("/").filter(Boolean).pop() || "";
  if (workspaceName.toLowerCase().endsWith(".md")) return workspaceName;
  return `${slugifyVoiceDocumentDownloadName(fallbackTitle)}.md`;
}

function voiceDocumentKey(document: AssistantVoiceDocument | null | undefined): string {
  return String(document?.document_path || document?.workspace_path || document?.document_id || "").trim();
}

function downloadMarkdownDocument(fileName: string, content: string): void {
  if (typeof document === "undefined") return;
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function createVoiceCaptureOwnerId(): string {
  return `voice-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function readVoiceCaptureLock(): VoiceCaptureLock | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(VOICE_CAPTURE_LOCK_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<VoiceCaptureLock>;
    const ownerId = String(parsed.ownerId || "").trim();
    const groupId = String(parsed.groupId || "").trim();
    const updatedAt = Number(parsed.updatedAt || 0);
    if (!ownerId || !groupId || !Number.isFinite(updatedAt)) return null;
    if (Date.now() - updatedAt > VOICE_CAPTURE_LOCK_TTL_MS) {
      window.localStorage.removeItem(VOICE_CAPTURE_LOCK_KEY);
      return null;
    }
    return { ownerId, groupId, updatedAt };
  } catch {
    return null;
  }
}

function writeVoiceCaptureLock(ownerId: string, groupId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      VOICE_CAPTURE_LOCK_KEY,
      JSON.stringify({ ownerId, groupId, updatedAt: Date.now() }),
    );
  } catch {
    void 0;
  }
}

function clearVoiceCaptureLock(ownerId?: string): void {
  if (typeof window === "undefined") return;
  try {
    if (ownerId) {
      const active = readVoiceCaptureLock();
      if (active && active.ownerId !== ownerId) return;
    }
    window.localStorage.removeItem(VOICE_CAPTURE_LOCK_KEY);
  } catch {
    void 0;
  }
}

function openVoiceCaptureChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  try {
    return new BroadcastChannel(VOICE_CAPTURE_CHANNEL_NAME);
  } catch {
    return null;
  }
}

function probeVoiceCaptureOwner(lock: VoiceCaptureLock): Promise<boolean> {
  const channel = openVoiceCaptureChannel();
  if (!channel) return Promise.resolve(true);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (alive: boolean) => {
      if (settled) return;
      settled = true;
      channel.removeEventListener("message", handleMessage);
      channel.close();
      resolve(alive);
    };
    const handleMessage = (event: MessageEvent<VoiceCaptureChannelMessage>) => {
      const message = event.data || {};
      if (message.type !== "alive") return;
      if (String(message.ownerId || "") !== lock.ownerId) return;
      finish(true);
    };
    channel.addEventListener("message", handleMessage);
    window.setTimeout(() => finish(false), VOICE_CAPTURE_LOCK_PROBE_TIMEOUT_MS);
    channel.postMessage({
      type: "probe",
      ownerId: lock.ownerId,
      groupId: lock.groupId,
      sentAt: Date.now(),
    } satisfies VoiceCaptureChannelMessage);
  });
}

async function claimVoiceCaptureLock(ownerId: string, groupId: string): Promise<VoiceCaptureLock | null> {
  const active = readVoiceCaptureLock();
  if (active && active.ownerId !== ownerId) {
    const ownerAlive = await probeVoiceCaptureOwner(active);
    if (ownerAlive) return active;
    clearVoiceCaptureLock(active.ownerId);
  }
  writeVoiceCaptureLock(ownerId, groupId);
  return null;
}

function refreshVoiceCaptureLock(ownerId: string, groupId: string): void {
  const active = readVoiceCaptureLock();
  if (!active || active.ownerId === ownerId) writeVoiceCaptureLock(ownerId, groupId);
}

function releaseVoiceCaptureLock(ownerId: string): void {
  clearVoiceCaptureLock(ownerId);
}

function getBrowserSpeechRecognitionConstructor(): BrowserSpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const speechWindow = window as typeof window & {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
  };
  return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition || null;
}

function getBrowserSpeechSupportIssue(): BrowserSpeechSupportIssue {
  if (!getBrowserSpeechRecognitionConstructor()) return "unsupported";
  return "";
}

function mediaRecorderSupported(): boolean {
  return !getBrowserAudioSupportIssue();
}

function getBrowserMicrophoneSupportIssue(): BrowserMicrophoneSupportIssue {
  if (typeof window !== "undefined" && window.isSecureContext === false) return "secure_context";
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return "get_user_media";
  return "";
}

function getBrowserAudioSupportIssue(): BrowserAudioSupportIssue {
  const microphoneIssue = getBrowserMicrophoneSupportIssue();
  if (microphoneIssue) return microphoneIssue;
  if (typeof MediaRecorder === "undefined") return "media_recorder";
  return "";
}

function preferredMediaRecorderMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
    "audio/mp4",
  ];
  for (const candidate of candidates) {
    try {
      if (MediaRecorder.isTypeSupported(candidate)) return candidate;
    } catch {
      // ignore unsupported browser probe
    }
  }
  return "";
}

function stopMediaStream(stream: MediaStream | null): void {
  if (!stream) return;
  try {
    stream.getTracks().forEach((track) => track.stop());
  } catch {
    // ignore browser cleanup failure
  }
}

function mediaStreamHasLiveAudio(stream: MediaStream | null): boolean {
  if (!stream) return false;
  try {
    return stream.getAudioTracks().some((track) => track.readyState === "live");
  } catch {
    return false;
  }
}

function browserSpeechRestartDelayMs(transientErrorCount: number): number {
  const count = Math.max(1, transientErrorCount);
  return Math.min(BROWSER_SPEECH_RESTART_MAX_MS, BROWSER_SPEECH_RESTART_BASE_MS * count);
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const value = typeof reader.result === "string" ? reader.result : "";
      const commaIndex = value.indexOf(",");
      resolve(commaIndex >= 0 ? value.slice(commaIndex + 1) : value);
    };
    reader.onerror = () => reject(reader.error || new Error("blob read failed"));
    reader.readAsDataURL(blob);
  });
}

function voiceLanguageOptionValues(configuredLanguage: string): string[] {
  const values: string[] = [...VOICE_LANGUAGE_OPTION_VALUES];
  const configured = String(configuredLanguage || "").trim();
  if (configured && !values.includes(configured)) values.push(configured);
  return values;
}

function numberFromUnknown(value: unknown, fallback: number, min: number, max: number): number {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return fallback;
  return Math.max(min, Math.min(max, Math.round(numberValue)));
}

function normalizeBrowserTranscriptChunk(value: string): string {
  return String(value || "")
    .replace(/\s+/g, " ")
    .replace(/\s+([,.!?;:，。！？；：、])/g, "$1")
    .trim();
}

function isLowValueBrowserSpeechFragment(value: string): boolean {
  const text = normalizeBrowserTranscriptChunk(value).toLowerCase();
  if (!text) return true;
  return LOW_VALUE_BROWSER_SPEECH_FRAGMENTS.has(text);
}

function longestTranscriptOverlap(left: string, right: string): number {
  const max = Math.min(left.length, right.length, 120);
  for (let size = max; size >= 2; size -= 1) {
    if (left.slice(-size) === right.slice(0, size)) return size;
  }
  return 0;
}

function mergeTranscriptChunks(previous: string, nextText: string): string {
  const prev = normalizeBrowserTranscriptChunk(previous);
  const next = normalizeBrowserTranscriptChunk(nextText);
  if (!prev) return next;
  if (!next) return prev;
  if (prev === next || prev.endsWith(next)) return prev;
  if (next.endsWith(prev)) return next;
  const overlap = longestTranscriptOverlap(prev, next);
  if (overlap > 0) return `${prev}${next.slice(overlap)}`;
  const cjkBoundary = /[\u3040-\u30ff\u3400-\u9fff]$/.test(prev) && /^[\u3040-\u30ff\u3400-\u9fff]/.test(next);
  return cjkBoundary ? `${prev}${next}` : `${prev} ${next}`;
}

function hashComposerSnapshot(value: string): string {
  let hash = 2166136261;
  const text = String(value || "");
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function abortBrowserSpeechRecognition(recognition: BrowserSpeechRecognition | null): void {
  if (!recognition) return;
  recognition.onend = null;
  recognition.onerror = null;
  recognition.onresult = null;
  recognition.onspeechstart = null;
  recognition.onspeechend = null;
  try {
    recognition.abort();
  } catch {
    // ignore browser cleanup failure
  }
}

export function VoiceSecretaryComposerControl({
  isDark,
  selectedGroupId,
  busy,
  buttonClassName = "",
  buttonSizePx = 44,
  disabled,
  variant = "button",
  captureMode = "document",
  onCaptureModeChange,
  composerText = "",
  composerContext = {},
  onPromptDraft,
}: VoiceSecretaryComposerControlProps) {
  const { t } = useTranslation("chat");
  const showError = useUIStore((state) => state.showError);
  const showNotice = useUIStore((state) => state.showNotice);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const refreshSeq = useRef(0);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const mediaChunksRef = useRef<Blob[]>([]);
  const voiceCaptureOwnerIdRef = useRef(createVoiceCaptureOwnerId());
  const recordingRef = useRef(false);
  const transcriptFlushTimerRef = useRef<number | null>(null);
  const transcriptMaxFlushTimerRef = useRef<number | null>(null);
  const transcriptSegmentSeqRef = useRef(0);
  const browserFinalTranscriptBufferRef = useRef("");
  const browserSpeechReceivedFinalRef = useRef(false);
  const browserSpeechHadErrorRef = useRef(false);
  const browserSpeechStopRequestedRef = useRef(false);
  const browserSpeechRestartTimerRef = useRef<number | null>(null);
  const browserSpeechStopFinalizeTimerRef = useRef<number | null>(null);
  const browserSpeechMediaCleanupRef = useRef<(() => void) | null>(null);
  const browserSpeechTransientErrorCountRef = useRef(0);
  const promptHoldActiveRef = useRef(false);
  const pendingPromptRequestIdRef = useRef("");
  const pendingPromptComposerHashRef = useRef("");
  const activeDocumentIdRef = useRef("");
  const captureTargetDocumentIdRef = useRef("");
  const documentBaseTitleRef = useRef("");
  const documentBaseContentRef = useRef("");
  const documentTitleDraftRef = useRef("");
  const documentDraftRef = useRef("");
  const archivedDocumentIdsRef = useRef<Set<string>>(new Set());
  const [open, setOpen] = useState(false);
  const [showAssistantModeMenu, setShowAssistantModeMenu] = useState(false);
  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState<"" | "enable" | "voice_language" | "transcribe" | "save_doc" | "new_doc" | "instruct_doc" | "archive_doc" | "capture_target">("");
  const [assistant, setAssistant] = useState<BuiltinAssistant | null>(null);
  const [documents, setDocuments] = useState<AssistantVoiceDocument[]>([]);
  const [activeDocumentId, setActiveDocumentId] = useState("");
  const [captureTargetDocumentId, setCaptureTargetDocumentId] = useState("");
  const [documentTitleDraft, setDocumentTitleDraft] = useState("");
  const [documentDraft, setDocumentDraft] = useState("");
  const [documentBaseTitle, setDocumentBaseTitle] = useState("");
  const [documentBaseContent, setDocumentBaseContent] = useState("");
  const [documentEditing, setDocumentEditing] = useState(false);
  const [documentRemoteChanged, setDocumentRemoteChanged] = useState(false);
  const [creatingDocument, setCreatingDocument] = useState(false);
  const [newDocumentTitleDraft, setNewDocumentTitleDraft] = useState("");
  const [documentInstruction, setDocumentInstruction] = useState("");
  const [recording, setRecording] = useState(false);
  const [interimTranscript, setInterimTranscript] = useState("");
  const [browserFinalTranscriptBufferPreview, setBrowserFinalTranscriptBufferPreview] = useState("");
  const [recentTranscriptItems, setRecentTranscriptItems] = useState<VoiceTranscriptDisplayItem[]>([]);
  const [speechError, setSpeechError] = useState("");
  const [speechSupported, setSpeechSupported] = useState(() => !getBrowserSpeechSupportIssue());
  const [serviceAudioSupported, setServiceAudioSupported] = useState(() => mediaRecorderSupported());
  const [audioDevices, setAudioDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedAudioDeviceId, setSelectedAudioDeviceId] = useState("");
  const [pendingPromptRequestId, setPendingPromptRequestId] = useState("");
  const [pendingPromptDraft, setPendingPromptDraft] = useState<AssistantVoicePromptDraft | null>(null);

  useEffect(() => {
    recordingRef.current = recording;
  }, [recording]);

  useEffect(() => {
    const channel = openVoiceCaptureChannel();
    if (!channel) return undefined;
    const handleMessage = (event: MessageEvent<VoiceCaptureChannelMessage>) => {
      const message = event.data || {};
      if (message.type !== "probe") return;
      if (String(message.ownerId || "") !== voiceCaptureOwnerIdRef.current) return;
      if (!recordingRef.current) return;
      channel.postMessage({
        type: "alive",
        ownerId: voiceCaptureOwnerIdRef.current,
        groupId: selectedGroupId,
        sentAt: Date.now(),
      } satisfies VoiceCaptureChannelMessage);
    };
    channel.addEventListener("message", handleMessage);
    return () => {
      channel.removeEventListener("message", handleMessage);
      channel.close();
    };
  }, [selectedGroupId]);

  useEffect(() => {
    const releaseCurrentLock = () => releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
    window.addEventListener("pagehide", releaseCurrentLock);
    window.addEventListener("beforeunload", releaseCurrentLock);
    return () => {
      window.removeEventListener("pagehide", releaseCurrentLock);
      window.removeEventListener("beforeunload", releaseCurrentLock);
      releaseCurrentLock();
    };
  }, []);

  const activeDocument = useMemo(() => {
    const docId = String(activeDocumentId || "").trim();
    if (docId) {
      const match = documents.find((document) => voiceDocumentKey(document) === docId || document.document_id === docId);
      if (match) return match;
    }
    return documents.find((document) => String(document.status || "active") === "active") || null;
  }, [activeDocumentId, documents]);
  const activeDocumentKey = useMemo(() => voiceDocumentKey(activeDocument), [activeDocument]);
  const activeDocumentTitle = String(activeDocument?.title || "").trim();
  const documentDisplayTitle =
    activeDocumentTitle ||
    documentTitleDraft.trim() ||
    t("voiceSecretaryWorkdocTitle", { defaultValue: "Voice Secretary workdoc" });
  const documentHasUnsavedEdits = documentDraft !== documentBaseContent;
  const assistantEnabled = !!assistant?.enabled;
  const recognitionBackend = String(assistant?.config?.recognition_backend || "browser_asr").trim();
  const configuredRecognitionLanguage = String(assistant?.config?.recognition_language || "auto").trim() || "auto";
  const effectiveRecognitionLanguage = configuredRecognitionLanguage === "auto"
    ? (typeof navigator !== "undefined" && navigator.language ? navigator.language : "en-US")
    : configuredRecognitionLanguage;
  const voiceLanguageOptions = useMemo(
    () => voiceLanguageOptionValues(configuredRecognitionLanguage),
    [configuredRecognitionLanguage],
  );
  const voiceLanguageLabel = useCallback((value: string) => {
    switch (value) {
      case "auto":
        return t("voiceSecretaryLanguageAuto", { defaultValue: "Auto" });
      case "zh-CN":
        return t("voiceSecretaryLanguageChinese", { defaultValue: "Chinese" });
      case "en-US":
        return t("voiceSecretaryLanguageEnglish", { defaultValue: "English" });
      case "ja-JP":
        return t("voiceSecretaryLanguageJapanese", { defaultValue: "Japanese" });
      case "ko-KR":
        return t("voiceSecretaryLanguageKorean", { defaultValue: "Korean" });
      case "fr-FR":
        return t("voiceSecretaryLanguageFrench", { defaultValue: "French" });
      case "de-DE":
        return t("voiceSecretaryLanguageGerman", { defaultValue: "German" });
      case "es-ES":
        return t("voiceSecretaryLanguageSpanish", { defaultValue: "Spanish" });
      default:
        return value;
    }
  }, [t]);
  const autoDocumentQuietMs = useMemo(
    () => numberFromUnknown(
      assistant?.config?.auto_document_quiet_ms,
      BROWSER_SPEECH_MIN_QUIET_MS,
      BROWSER_SPEECH_MIN_QUIET_MS,
      60_000,
    ),
    [assistant?.config?.auto_document_quiet_ms],
  );
  const autoDocumentMaxWindowMs = useMemo(
    () => numberFromUnknown(
      assistant?.config?.auto_document_max_window_seconds,
      BROWSER_SPEECH_MAX_WINDOW_FALLBACK_MS / 1000,
      BROWSER_SPEECH_MIN_MAX_WINDOW_MS / 1000,
      300,
    ) * 1000,
    [assistant?.config?.auto_document_max_window_seconds],
  );
  const browserSpeechReady = recognitionBackend === "browser_asr";
  const serviceAsrReady = recognitionBackend === "assistant_service_local_asr";
  const browserSpeechSupportIssue = browserSpeechReady ? getBrowserSpeechSupportIssue() : "";
  const serviceAudioSupportIssue = serviceAsrReady ? getBrowserAudioSupportIssue() : "";
  const getBrowserSpeechIssueMessage = useCallback((issue: BrowserSpeechSupportIssue) => {
    if (issue === "unsupported") {
      return t("voiceSecretaryBrowserUnsupported", {
        defaultValue: "Browser speech recognition is not available in this browser page. Try another current browser.",
      });
    }
    return "";
  }, [t]);
  const getAudioSupportIssueMessage = useCallback((issue: BrowserAudioSupportIssue) => {
    if (issue === "secure_context") {
      return t("voiceSecretarySecureContextRequired", {
        defaultValue: "Microphone capture requires a secure browser context. Open this page through localhost or HTTPS, not a raw WSL IP over HTTP.",
      });
    }
    if (issue === "get_user_media") {
      return t("voiceSecretaryGetUserMediaUnavailable", {
        defaultValue: "This browser page cannot access the microphone API. Open it through localhost or HTTPS and allow microphone access.",
      });
    }
    if (issue === "media_recorder") {
      return t("voiceSecretaryMediaRecorderUnavailable", {
        defaultValue: "This browser does not support MediaRecorder audio capture. Use Browser ASR or another browser with MediaRecorder support.",
      });
    }
    return "";
  }, [t]);
  const getAudioCaptureErrorMessage = useCallback((error: unknown) => {
    const errorName = typeof error === "object" && error && "name" in error ? String((error as { name?: unknown }).name || "") : "";
    if (errorName === "NotAllowedError" || errorName === "SecurityError") {
      return {
        message: t("voiceSecretaryMicPermissionBlocked", {
          defaultValue: "Microphone permission is blocked. Allow microphone access for this site in the browser, then try again.",
        }),
        resetSelectedDevice: false,
      };
    }
    if (errorName === "NotFoundError" || errorName === "DevicesNotFoundError") {
      return {
        message: t("voiceSecretaryMicNotFound", {
          defaultValue: "No microphone was found or the selected microphone is unavailable.",
        }),
        resetSelectedDevice: false,
      };
    }
    if (errorName === "NotReadableError" || errorName === "TrackStartError") {
      return {
        message: t("voiceSecretaryMicBusyOrUnavailable", {
          defaultValue: "The microphone could not be started. Check whether another app is using it or the OS blocked access.",
        }),
        resetSelectedDevice: false,
      };
    }
    if (errorName === "OverconstrainedError" || errorName === "ConstraintNotSatisfiedError") {
      return {
        message: t("voiceSecretarySelectedMicUnavailable", {
          defaultValue: "The selected microphone is unavailable. Reset to the system default microphone and try again.",
        }),
        resetSelectedDevice: true,
      };
    }
    return {
      message: t("voiceSecretaryAudioCaptureFailed", { defaultValue: "Audio capture failed." }),
      resetSelectedDevice: false,
    };
  }, [t]);
  const controlDisabled = disabled || !selectedGroupId || busy === "send";
  const isAssistantRow = variant === "assistantRow";
  const selectedAudioDeviceLabel = useMemo(() => {
    if (!selectedAudioDeviceId) return SERVICE_DEFAULT_MIC_LABEL;
    const index = audioDevices.findIndex((device) => device.deviceId === selectedAudioDeviceId);
    const device = index >= 0 ? audioDevices[index] : null;
    return device?.label || `microphone_${index + 1 || "selected"}`;
  }, [audioDevices, selectedAudioDeviceId]);

  useEffect(() => {
    activeDocumentIdRef.current = activeDocumentId;
  }, [activeDocumentId]);

  useEffect(() => {
    captureTargetDocumentIdRef.current = captureTargetDocumentId;
  }, [captureTargetDocumentId]);

  useEffect(() => {
    documentTitleDraftRef.current = documentTitleDraft;
    documentDraftRef.current = documentDraft;
    documentBaseTitleRef.current = documentBaseTitle;
    documentBaseContentRef.current = documentBaseContent;
  }, [documentBaseContent, documentBaseTitle, documentDraft, documentTitleDraft]);

  const loadDocumentDraft = useCallback((document: AssistantVoiceDocument | null) => {
    const title = String(document?.title || "");
    const content = String(document?.content || "");
    documentTitleDraftRef.current = title;
    documentDraftRef.current = content;
    documentBaseTitleRef.current = title;
    documentBaseContentRef.current = content;
    setDocumentTitleDraft(title);
    setDocumentDraft(content);
    setDocumentBaseTitle(title);
    setDocumentBaseContent(content);
    setDocumentRemoteChanged(false);
  }, []);

  const updateDocumentDraft = useCallback((value: string) => {
    documentDraftRef.current = value;
    setDocumentDraft(value);
  }, []);

  const loadAudioDevices = useCallback(async () => {
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) {
      setServiceAudioSupported(false);
      setAudioDevices([]);
      return;
    }
    setServiceAudioSupported(mediaRecorderSupported());
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter((device) => device.kind === "audioinput");
      setAudioDevices(inputs);
      setSelectedAudioDeviceId((current) => {
        if (!current || inputs.some((device) => device.deviceId === current)) return current;
        return "";
      });
    } catch {
      setAudioDevices([]);
    }
  }, []);

  const refreshAssistant = useCallback(async (opts?: { quiet?: boolean }) => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid) return;
    const seq = ++refreshSeq.current;
    const quiet = Boolean(opts?.quiet);
    if (!quiet) setLoading(true);
    try {
      const promptRequestId = String(pendingPromptRequestIdRef.current || "").trim();
      const resp = await fetchAssistant(gid, "voice_secretary", { promptRequestId });
      if (seq !== refreshSeq.current) return;
      if (!resp.ok) {
        if (!quiet) showError(resp.error.message);
        return;
      }
      setAssistant(resp.result.assistant || null);
      const promptDraft = resp.result.prompt_draft || null;
      if (promptDraft && pendingPromptRequestIdRef.current && promptDraft.request_id === pendingPromptRequestIdRef.current) {
        setPendingPromptDraft(promptDraft);
      }
      const nextDocuments = resp.result.documents || [];
      const nextCaptureTargetId = String(
          resp.result.capture_target_document_path ||
          resp.result.active_document_path ||
          "",
      ).trim();
      setDocuments(nextDocuments);
      const currentCaptureTargetId = String(captureTargetDocumentIdRef.current || "").trim();
      const currentCaptureTargetExists = currentCaptureTargetId
        ? nextDocuments.some((document) => voiceDocumentKey(document) === currentCaptureTargetId || document.document_id === currentCaptureTargetId)
        : false;
      const resolvedCaptureTargetId = currentCaptureTargetExists
        ? currentCaptureTargetId
        : nextCaptureTargetId || voiceDocumentKey(nextDocuments[0]) || "";
      captureTargetDocumentIdRef.current = resolvedCaptureTargetId;
      setCaptureTargetDocumentId(resolvedCaptureTargetId);
      const currentDocumentId = String(activeDocumentIdRef.current || "").trim();
      const currentDocumentStillExists = currentDocumentId
        ? nextDocuments.some((document) => voiceDocumentKey(document) === currentDocumentId || document.document_id === currentDocumentId)
        : false;
      const nextActiveDocumentId = currentDocumentStillExists
        ? currentDocumentId
        : String(nextCaptureTargetId || voiceDocumentKey(nextDocuments[0]) || "").trim();
      setActiveDocumentId(nextActiveDocumentId);
      const nextActiveDocument = nextDocuments.find((document) => voiceDocumentKey(document) === nextActiveDocumentId || document.document_id === nextActiveDocumentId) || nextDocuments[0] || null;
      if (nextActiveDocument) {
        const serverTitle = String(nextActiveDocument.title || "");
        const serverContent = String(nextActiveDocument.content || "");
        const localDirty = documentDraftRef.current !== documentBaseContentRef.current;
        const sameDocument = currentDocumentStillExists && currentDocumentId === voiceDocumentKey(nextActiveDocument);
        const serverChangedFromBase =
          serverTitle !== documentBaseTitleRef.current || serverContent !== documentBaseContentRef.current;
        if (!localDirty || !sameDocument) {
          loadDocumentDraft(nextActiveDocument);
        } else if (serverChangedFromBase) {
          setDocumentRemoteChanged(true);
        }
      } else {
        loadDocumentDraft(null);
      }
    } finally {
      if (seq === refreshSeq.current && !quiet) setLoading(false);
    }
  }, [loadDocumentDraft, selectedGroupId, showError]);

  useEffect(() => {
    if (!open) return;
    void refreshAssistant();
  }, [open, refreshAssistant]);

  useEffect(() => {
    if (!open || !assistantEnabled) return undefined;
    const interval = window.setInterval(() => {
      void refreshAssistant({ quiet: true });
    }, 5000);
    return () => window.clearInterval(interval);
  }, [assistantEnabled, open, refreshAssistant]);

  useEffect(() => {
    if (!pendingPromptRequestId || pendingPromptDraft) return undefined;
    const onPromptDraftReady = (event: Event) => {
      const detail = (event as CustomEvent<VoiceSecretaryPromptDraftEventDetail>).detail;
      const requested = String(pendingPromptRequestIdRef.current || pendingPromptRequestId || "").trim();
      if (!detail || detail.groupId !== selectedGroupId || detail.requestId !== requested) return;
      if (detail.action && detail.action !== "submit") return;
      void refreshAssistant({ quiet: true });
    };
    window.addEventListener(VOICE_SECRETARY_PROMPT_DRAFT_EVENT, onPromptDraftReady);
    const interval = window.setInterval(() => {
      void refreshAssistant({ quiet: true });
    }, PROMPT_DRAFT_POLL_FALLBACK_MS);
    return () => {
      window.removeEventListener(VOICE_SECRETARY_PROMPT_DRAFT_EVENT, onPromptDraftReady);
      window.clearInterval(interval);
    };
  }, [pendingPromptDraft, pendingPromptRequestId, refreshAssistant, selectedGroupId]);

  const acknowledgePromptDraft = useCallback(async (
    draft: AssistantVoicePromptDraft,
    status: "applied" | "dismissed" | "stale",
  ) => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid || !draft.request_id) return;
    const resp = await ackVoiceAssistantPromptDraft(gid, {
      requestId: draft.request_id,
      status,
      by: "user",
    });
    if (resp.ok && resp.result.assistant) setAssistant(resp.result.assistant);
  }, [selectedGroupId]);

  const applyPromptDraft = useCallback(async (draft: AssistantVoicePromptDraft) => {
    const text = String(draft.draft_text || "").trim();
    if (!text) return;
    pendingPromptRequestIdRef.current = "";
    pendingPromptComposerHashRef.current = "";
    setPendingPromptRequestId("");
    setPendingPromptDraft(null);
    onPromptDraft?.(text, { mode: "replace" });
    try {
      await acknowledgePromptDraft(draft, "applied");
    } catch {
      // Applying locally is the critical path; ack retry is non-critical.
    }
    showNotice({
      message: t("voiceSecretaryPromptDraftFilled", {
        defaultValue: "Refined prompt filled into the composer.",
      }),
    });
  }, [acknowledgePromptDraft, onPromptDraft, showNotice, t]);

  useEffect(() => {
    if (!pendingPromptDraft) return;
    const requested = String(pendingPromptRequestIdRef.current || pendingPromptRequestId || "").trim();
    if (!requested || pendingPromptDraft.request_id !== requested) return;
    void applyPromptDraft(pendingPromptDraft);
  }, [applyPromptDraft, pendingPromptDraft, pendingPromptRequestId]);

  useEffect(() => {
    if (!open || !serviceAsrReady) return;
    void loadAudioDevices();
  }, [loadAudioDevices, open, serviceAsrReady]);

  useEffect(() => {
    refreshSeq.current += 1;
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    abortBrowserSpeechRecognition(recognition);
    browserSpeechStopRequestedRef.current = true;
    browserSpeechTransientErrorCountRef.current = 0;
    const recorder = mediaRecorderRef.current;
    mediaRecorderRef.current = null;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      try {
        recorder.stop();
      } catch {
        // ignore browser cleanup failure
      }
    }
    const cleanupBrowserSpeechMedia = browserSpeechMediaCleanupRef.current;
    browserSpeechMediaCleanupRef.current = null;
    if (cleanupBrowserSpeechMedia) cleanupBrowserSpeechMedia();
    stopMediaStream(mediaStreamRef.current);
    mediaStreamRef.current = null;
    mediaChunksRef.current = [];
    setOpen(false);
    setLoading(false);
    setActionBusy("");
    setAssistant(null);
    setDocuments([]);
    setActiveDocumentId("");
    setCaptureTargetDocumentId("");
    loadDocumentDraft(null);
    setDocumentEditing(false);
    setDocumentInstruction("");
    setRecording(false);
    setInterimTranscript("");
    setBrowserFinalTranscriptBufferPreview("");
    setRecentTranscriptItems([]);
    setSpeechError("");
    setAudioDevices([]);
    setSelectedAudioDeviceId("");
    pendingPromptRequestIdRef.current = "";
    pendingPromptComposerHashRef.current = "";
    setPendingPromptRequestId("");
    setPendingPromptDraft(null);
    releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
    if (transcriptFlushTimerRef.current !== null) {
      window.clearTimeout(transcriptFlushTimerRef.current);
      transcriptFlushTimerRef.current = null;
    }
    if (transcriptMaxFlushTimerRef.current !== null) {
      window.clearTimeout(transcriptMaxFlushTimerRef.current);
      transcriptMaxFlushTimerRef.current = null;
    }
    browserFinalTranscriptBufferRef.current = "";
    if (browserSpeechRestartTimerRef.current !== null) {
      window.clearTimeout(browserSpeechRestartTimerRef.current);
      browserSpeechRestartTimerRef.current = null;
    }
    if (browserSpeechStopFinalizeTimerRef.current !== null) {
      window.clearTimeout(browserSpeechStopFinalizeTimerRef.current);
      browserSpeechStopFinalizeTimerRef.current = null;
    }
  }, [loadDocumentDraft, selectedGroupId]);

  useEffect(() => {
    if (!selectedGroupId) return;
    void refreshAssistant({ quiet: true });
  }, [refreshAssistant, selectedGroupId]);

  const clearTranscriptFlushTimer = useCallback(() => {
    if (transcriptFlushTimerRef.current === null) return;
    window.clearTimeout(transcriptFlushTimerRef.current);
    transcriptFlushTimerRef.current = null;
  }, []);

  const clearTranscriptMaxFlushTimer = useCallback(() => {
    if (transcriptMaxFlushTimerRef.current === null) return;
    window.clearTimeout(transcriptMaxFlushTimerRef.current);
    transcriptMaxFlushTimerRef.current = null;
  }, []);

  const clearBrowserSpeechRestartTimer = useCallback(() => {
    if (browserSpeechRestartTimerRef.current === null) return;
    window.clearTimeout(browserSpeechRestartTimerRef.current);
    browserSpeechRestartTimerRef.current = null;
  }, []);

  const clearBrowserSpeechStopFinalizeTimer = useCallback(() => {
    if (browserSpeechStopFinalizeTimerRef.current === null) return;
    window.clearTimeout(browserSpeechStopFinalizeTimerRef.current);
    browserSpeechStopFinalizeTimerRef.current = null;
  }, []);

  const clearBrowserSpeechMediaHandlers = useCallback(() => {
    const cleanup = browserSpeechMediaCleanupRef.current;
    browserSpeechMediaCleanupRef.current = null;
    if (cleanup) cleanup();
  }, []);

  const applyTranscriptAppendResult = useCallback((result: AssistantVoiceTranscriptSegmentResult) => {
    if (result.assistant) setAssistant(result.assistant);
    if ((result.document_updated || result.input_event_created) && result.document) {
      const document = result.document;
      const docId = voiceDocumentKey(document);
      if (docId && !archivedDocumentIdsRef.current.has(docId) && String(document.status || "active").trim() !== "archived") {
        setDocuments((prev) => {
          const index = prev.findIndex((item) => voiceDocumentKey(item) === docId);
          if (index < 0) return [document, ...prev];
          const next = [...prev];
          next[index] = document;
          return next;
        });
        if (!captureTargetDocumentIdRef.current) {
          captureTargetDocumentIdRef.current = docId;
          setCaptureTargetDocumentId(docId);
        }
        const viewingDocumentId = String(activeDocumentIdRef.current || "").trim();
        const localDirty = documentDraftRef.current !== documentBaseContentRef.current;
        if (!viewingDocumentId || viewingDocumentId === docId) {
          setActiveDocumentId(docId);
          if (!localDirty || !viewingDocumentId) loadDocumentDraft(document);
          else setDocumentRemoteChanged(true);
        }
      }
    }
  }, [loadDocumentDraft]);

  const applyDocumentMutationResult = useCallback((document: AssistantVoiceDocument | undefined, assistantNext?: BuiltinAssistant) => {
    if (assistantNext) setAssistant(assistantNext);
    if (!document) return;
    const docId = voiceDocumentKey(document);
    setDocuments((prev) => {
      const index = prev.findIndex((item) => voiceDocumentKey(item) === docId);
      if (index < 0) return [document, ...prev];
      const next = [...prev];
      next[index] = document;
      return next;
    });
    if (!activeDocumentIdRef.current || activeDocumentIdRef.current === docId) {
      setActiveDocumentId(docId);
      loadDocumentDraft(document);
    }
  }, [loadDocumentDraft]);

  const appendTranscriptSegment = useCallback(async (
    text: string,
    opts?: { flush?: boolean; triggerKind?: string; source?: string; inputDeviceLabel?: string; documentPath?: string },
  ) => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid || !assistantEnabled) return;
    const cleanText = String(text || "").trim();
    const flush = Boolean(opts?.flush);
    if (!cleanText && !flush) return;
    const targetDocumentPath = String(opts?.documentPath || captureTargetDocumentIdRef.current || "").trim();
    const segmentSeq = transcriptSegmentSeqRef.current + 1;
    transcriptSegmentSeqRef.current = segmentSeq;
    try {
      const resp = await appendVoiceAssistantTranscriptSegment(gid, {
        sessionId: voiceCaptureOwnerIdRef.current,
        segmentId: cleanText ? `seg-${segmentSeq}` : "",
        documentPath: targetDocumentPath,
        text: cleanText,
        language: effectiveRecognitionLanguage,
        isFinal: true,
        flush,
        trigger: {
          mode: "meeting",
          trigger_kind: opts?.triggerKind || (flush ? "push_to_talk_stop" : "meeting_window"),
          capture_mode: serviceAsrReady ? "service" : "browser",
          recognition_backend: opts?.source || recognitionBackend,
          client_session_id: voiceCaptureOwnerIdRef.current,
          input_device_label: opts?.inputDeviceLabel || (serviceAsrReady ? selectedAudioDeviceLabel : BROWSER_DEFAULT_MIC_LABEL),
          language: effectiveRecognitionLanguage,
          document_path: targetDocumentPath,
        },
        by: "user",
      });
      if (!resp.ok) {
        showError(resp.error.message);
        return;
      }
      if (cleanText) {
        setRecentTranscriptItems((prev) => [
          {
            id: `transcript-${Date.now()}-${segmentSeq}`,
            text: cleanText,
            language: effectiveRecognitionLanguage,
            source: opts?.source || recognitionBackend,
            createdAt: Date.now(),
          },
          ...prev,
        ].slice(0, VOICE_TRANSCRIPT_DISPLAY_LIMIT));
      }
      applyTranscriptAppendResult(resp.result);
    } catch {
      showError(t("voiceSecretaryTranscriptAppendFailed", {
        defaultValue: "Failed to save Voice Secretary transcript segment.",
      }));
    }
  }, [
    applyTranscriptAppendResult,
    assistantEnabled,
    effectiveRecognitionLanguage,
    recognitionBackend,
    selectedAudioDeviceLabel,
    selectedGroupId,
    serviceAsrReady,
    showError,
    t,
  ]);

  const rememberTranscriptDisplayItem = useCallback((text: string, source: string) => {
    const cleanText = String(text || "").trim();
    if (!cleanText) return;
    const segmentSeq = transcriptSegmentSeqRef.current + 1;
    transcriptSegmentSeqRef.current = segmentSeq;
    setRecentTranscriptItems((prev) => [
      {
        id: `transcript-${Date.now()}-${segmentSeq}`,
        text: cleanText,
        language: effectiveRecognitionLanguage,
        source,
        createdAt: Date.now(),
      },
      ...prev,
    ].slice(0, VOICE_TRANSCRIPT_DISPLAY_LIMIT));
  }, [effectiveRecognitionLanguage]);

  const sendInstructionTranscript = useCallback(async (
    text: string,
    opts?: { triggerKind?: string; documentPath?: string },
  ) => {
    const gid = String(selectedGroupId || "").trim();
    const instruction = normalizeBrowserTranscriptChunk(text);
    if (!gid || !assistantEnabled || !instruction) return;
    if (documentHasUnsavedEdits) {
      showError(t("voiceSecretaryDocumentUnsavedBeforeRequest", {
        defaultValue: "Save or discard local document edits before sending a request to Voice Secretary.",
      }));
      return;
    }
    const docId = String(opts?.documentPath || captureTargetDocumentIdRef.current || activeDocumentKey || activeDocumentId || "").trim();
    const targetDocument = docId
      ? documents.find((document) => voiceDocumentKey(document) === docId || document.document_id === docId) || null
      : null;
    const documentPath = targetDocument && String(targetDocument.status || "active").trim().toLowerCase() !== "archived"
      ? docId
      : "";
    if (docId && targetDocument && String(targetDocument.status || "active").trim().toLowerCase() === "archived") {
      showNotice({
        message: t("voiceSecretaryInstructionSentWithoutArchivedDocument", {
          defaultValue: "The selected document is archived, so this was sent as a general Secretary request.",
        }),
      });
    }
    try {
      const resp = await appendVoiceAssistantInput(gid, {
        kind: "voice_instruction",
        instruction,
        documentPath,
        trigger: {
          trigger_kind: opts?.triggerKind || "voice_instruction",
          mode: "voice_instruction",
          recognition_backend: recognitionBackend,
          language: effectiveRecognitionLanguage,
        },
        by: "user",
      });
      if (!resp.ok) {
        showError(resp.error.message);
        return;
      }
      rememberTranscriptDisplayItem(instruction, "voice_instruction");
      applyDocumentMutationResult(resp.result.document, resp.result.assistant);
      showNotice({
        message: t("voiceSecretaryDocumentInstructionQueued", { defaultValue: "Request sent to Voice Secretary." }),
      });
    } catch {
      showError(t("voiceSecretaryDocumentInstructionFailed", { defaultValue: "Failed to send the request to Voice Secretary." }));
    }
  }, [
    activeDocumentId,
    activeDocumentKey,
    applyDocumentMutationResult,
    assistantEnabled,
    documentHasUnsavedEdits,
    documents,
    effectiveRecognitionLanguage,
    recognitionBackend,
    rememberTranscriptDisplayItem,
    selectedGroupId,
    showError,
    showNotice,
    t,
  ]);

  const requestPromptRefine = useCallback(async (text: string, triggerKind = "prompt_refine") => {
    const gid = String(selectedGroupId || "").trim();
    const voiceTranscript = normalizeBrowserTranscriptChunk(text);
    if (!gid || !assistantEnabled || !voiceTranscript) return;
    const snapshot = String(composerText || "");
    const snapshotHash = hashComposerSnapshot(snapshot);
    const existingRequestId = String(pendingPromptRequestIdRef.current || pendingPromptRequestId || "").trim();
    const requestId = existingRequestId || `voice-prompt-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    pendingPromptRequestIdRef.current = requestId;
    pendingPromptComposerHashRef.current = snapshotHash;
    setPendingPromptRequestId(requestId);
    try {
      const resp = await appendVoiceAssistantInput(gid, {
        kind: "prompt_refine",
        voiceTranscript,
        composerText: snapshot,
        requestId,
        operation: "replace_with_refined_prompt",
        composerSnapshotHash: snapshotHash,
        composerContext,
        language: effectiveRecognitionLanguage,
        trigger: {
          trigger_kind: triggerKind,
          mode: "prompt",
          recognition_backend: recognitionBackend,
          language: effectiveRecognitionLanguage,
        },
        by: "user",
      });
      if (!resp.ok) {
        pendingPromptRequestIdRef.current = "";
        pendingPromptComposerHashRef.current = "";
        setPendingPromptRequestId("");
        showError(resp.error.message);
        return;
      }
      setPendingPromptDraft(null);
      rememberTranscriptDisplayItem(voiceTranscript, "prompt_refine");
      if (resp.result.assistant) setAssistant(resp.result.assistant);
      showNotice({
        message: t("voiceSecretaryPromptRefineQueued", {
          defaultValue: "Voice Secretary is refining the prompt.",
        }),
      });
      void refreshAssistant({ quiet: true });
    } catch {
      pendingPromptRequestIdRef.current = "";
      pendingPromptComposerHashRef.current = "";
      setPendingPromptRequestId("");
      showError(t("voiceSecretaryPromptRefineFailed", { defaultValue: "Failed to send prompt refinement to Voice Secretary." }));
    }
  }, [
    assistantEnabled,
    composerContext,
    composerText,
    effectiveRecognitionLanguage,
    pendingPromptRequestId,
    recognitionBackend,
    refreshAssistant,
    rememberTranscriptDisplayItem,
    selectedGroupId,
    showError,
    showNotice,
    t,
  ]);

  const takeBrowserFinalTranscriptBuffer = useCallback((): string => {
    const text = String(browserFinalTranscriptBufferRef.current || "").trim();
    browserFinalTranscriptBufferRef.current = "";
    setBrowserFinalTranscriptBufferPreview("");
    return text;
  }, []);

  const flushBrowserTranscriptWindow = useCallback(async (
    triggerKind = "meeting_window",
    opts?: { documentPath?: string },
  ): Promise<void> => {
    clearTranscriptFlushTimer();
    clearTranscriptMaxFlushTimer();
    const documentPath = String(opts?.documentPath || captureTargetDocumentIdRef.current || "").trim();
    const text = takeBrowserFinalTranscriptBuffer();
    if (captureMode === "prompt") {
      await requestPromptRefine(text, triggerKind || "prompt_refine");
      return;
    }
    if (captureMode === "instruction") {
      await sendInstructionTranscript(text, { triggerKind, documentPath });
      return;
    }
    await appendTranscriptSegment(text, {
      flush: true,
      triggerKind,
      source: "browser_asr",
      inputDeviceLabel: BROWSER_DEFAULT_MIC_LABEL,
      documentPath,
    });
  }, [
    appendTranscriptSegment,
    captureMode,
    clearTranscriptFlushTimer,
    clearTranscriptMaxFlushTimer,
    requestPromptRefine,
    sendInstructionTranscript,
    takeBrowserFinalTranscriptBuffer,
  ]);

  const scheduleTranscriptFlush = useCallback((triggerKind: string, options?: { preserveExisting?: boolean }) => {
    if (options?.preserveExisting && transcriptFlushTimerRef.current !== null) return;
    clearTranscriptFlushTimer();
    const documentPath = captureTargetDocumentIdRef.current;
    // Browser speech boundary events can lag; use recognition-result idle as
    // the primary quiet window. Delayed speechend must not postpone it.
    transcriptFlushTimerRef.current = window.setTimeout(() => {
      transcriptFlushTimerRef.current = null;
      void flushBrowserTranscriptWindow(triggerKind, { documentPath });
    }, autoDocumentQuietMs);
  }, [autoDocumentQuietMs, clearTranscriptFlushTimer, flushBrowserTranscriptWindow]);

  const scheduleTranscriptMaxFlush = useCallback((triggerKind: string) => {
    if (transcriptMaxFlushTimerRef.current !== null) return;
    const documentPath = captureTargetDocumentIdRef.current;
    transcriptMaxFlushTimerRef.current = window.setTimeout(() => {
      transcriptMaxFlushTimerRef.current = null;
      void flushBrowserTranscriptWindow(triggerKind, { documentPath });
    }, autoDocumentMaxWindowMs);
  }, [autoDocumentMaxWindowMs, flushBrowserTranscriptWindow]);

  const queueBrowserFinalTranscript = useCallback((text: string) => {
    const clean = normalizeBrowserTranscriptChunk(text);
    if (!clean) return;
    if (!browserFinalTranscriptBufferRef.current && isLowValueBrowserSpeechFragment(clean)) return;
    browserSpeechTransientErrorCountRef.current = 0;
    browserSpeechReceivedFinalRef.current = true;
    browserSpeechHadErrorRef.current = false;
    setSpeechError("");
    const merged = mergeTranscriptChunks(browserFinalTranscriptBufferRef.current, clean);
    browserFinalTranscriptBufferRef.current = merged;
    setBrowserFinalTranscriptBufferPreview(merged);
    scheduleTranscriptMaxFlush("max_window");
  }, [scheduleTranscriptMaxFlush]);

  const cleanupServiceAudio = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    mediaRecorderRef.current = null;
    if (recorder) {
      recorder.ondataavailable = null;
      recorder.onerror = null;
      recorder.onstop = null;
    }
    clearBrowserSpeechMediaHandlers();
    stopMediaStream(mediaStreamRef.current);
    mediaStreamRef.current = null;
    mediaChunksRef.current = [];
    releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
    setRecording(false);
    setInterimTranscript("");
  }, [clearBrowserSpeechMediaHandlers]);

  const stopBrowserSpeech = useCallback(() => {
    promptHoldActiveRef.current = false;
    browserSpeechStopRequestedRef.current = true;
    browserSpeechTransientErrorCountRef.current = 0;
    clearBrowserSpeechRestartTimer();
    clearBrowserSpeechStopFinalizeTimer();
    const finalizeStoppedSpeech = (recognition: BrowserSpeechRecognition | null) => {
      if (recognition && recognitionRef.current === recognition) recognitionRef.current = null;
      abortBrowserSpeechRecognition(recognition);
      clearBrowserSpeechMediaHandlers();
      stopMediaStream(mediaStreamRef.current);
      mediaStreamRef.current = null;
      releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
      setRecording(false);
      setInterimTranscript("");
      void flushBrowserTranscriptWindow("push_to_talk_stop");
    };
    const recognition = recognitionRef.current;
    setRecording(false);
    setInterimTranscript("");
    if (!recognition) {
      finalizeStoppedSpeech(null);
      return;
    }
    browserSpeechStopFinalizeTimerRef.current = window.setTimeout(() => {
      browserSpeechStopFinalizeTimerRef.current = null;
      finalizeStoppedSpeech(recognition);
    }, 2000);
    try {
      // stop() lets the browser emit any final result before onend performs the stop flush.
      recognition.stop();
    } catch {
      clearBrowserSpeechStopFinalizeTimer();
      finalizeStoppedSpeech(recognition);
    }
  }, [clearBrowserSpeechMediaHandlers, clearBrowserSpeechRestartTimer, clearBrowserSpeechStopFinalizeTimer, flushBrowserTranscriptWindow]);

  const stopServiceAudio = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder) {
      cleanupServiceAudio();
      return;
    }
    try {
      if (recorder.state !== "inactive") {
        recorder.stop();
        return;
      }
    } catch {
      // fall through to cleanup
    }
    cleanupServiceAudio();
  }, [cleanupServiceAudio]);

  const stopCurrentRecording = useCallback(() => {
    promptHoldActiveRef.current = false;
    if (mediaRecorderRef.current) {
      stopServiceAudio();
      return;
    }
    stopBrowserSpeech();
  }, [stopBrowserSpeech, stopServiceAudio]);

  const closePanel = useCallback(() => {
    setOpen(false);
  }, []);
  const { modalRef } = useModalA11y(open, closePanel);

  useEffect(() => {
    if (!showAssistantModeMenu) return undefined;
    const handlePointerDown = (event: MouseEvent) => {
      const root = rootRef.current;
      if (root && event.target instanceof Node && root.contains(event.target)) return;
      setShowAssistantModeMenu(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowAssistantModeMenu(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [showAssistantModeMenu]);

  useEffect(() => () => {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    abortBrowserSpeechRecognition(recognition);
    browserSpeechStopRequestedRef.current = true;
    browserSpeechTransientErrorCountRef.current = 0;
    clearBrowserSpeechRestartTimer();
    clearBrowserSpeechStopFinalizeTimer();
    clearTranscriptFlushTimer();
    clearTranscriptMaxFlushTimer();
    cleanupServiceAudio();
    releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
  }, [cleanupServiceAudio, clearBrowserSpeechRestartTimer, clearBrowserSpeechStopFinalizeTimer, clearTranscriptFlushTimer, clearTranscriptMaxFlushTimer]);

  const startBrowserSpeech = useCallback(async () => {
    const gid = String(selectedGroupId || "").trim();
    if (!assistantEnabled) {
      showError(t("voiceSecretaryEnableFirst", { defaultValue: "Enable Voice Secretary first." }));
      return;
    }
    if (!browserSpeechReady) {
      showError(t("voiceSecretaryBrowserBackendRequired", { defaultValue: "Switch recognition to Browser ASR in Assistants settings first." }));
      return;
    }
    const microphoneIssue = getBrowserMicrophoneSupportIssue();
    if (microphoneIssue) {
      const message = getAudioSupportIssueMessage(microphoneIssue);
      setSpeechError(message);
      showError(message);
      return;
    }
    const supportIssue = getBrowserSpeechSupportIssue();
    setSpeechSupported(!supportIssue);
    if (supportIssue) {
      const message = getBrowserSpeechIssueMessage(supportIssue);
      setSpeechError(message);
      showError(message);
      return;
    }
    const SpeechRecognition = getBrowserSpeechRecognitionConstructor();
    if (!SpeechRecognition) {
      const message = t("voiceSecretaryBrowserUnsupported", {
        defaultValue: "Browser speech recognition is not available in this browser page. Try another current browser.",
      });
      setSpeechError(message);
      showError(message);
      return;
    }
    const activeLock = await claimVoiceCaptureLock(voiceCaptureOwnerIdRef.current, gid);
    if (activeLock) {
      showError(t("voiceSecretaryAnotherRecording", {
        groupId: activeLock.groupId,
        defaultValue: "Voice Secretary is already recording in group {{groupId}} in another active tab. Stop that recording before starting another one.",
      }));
      return;
    }

    const existingRecognition = recognitionRef.current;
    recognitionRef.current = null;
    clearBrowserSpeechRestartTimer();
    clearBrowserSpeechStopFinalizeTimer();
    clearBrowserSpeechMediaHandlers();
    abortBrowserSpeechRecognition(existingRecognition);
    stopMediaStream(mediaStreamRef.current);
    mediaStreamRef.current = null;
    browserSpeechReceivedFinalRef.current = false;
    browserSpeechHadErrorRef.current = false;
    browserSpeechStopRequestedRef.current = false;
    browserSpeechTransientErrorCountRef.current = 0;

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
      const { message, resetSelectedDevice } = getAudioCaptureErrorMessage(error);
      if (resetSelectedDevice) setSelectedAudioDeviceId("");
      setSpeechError(message);
      showError(message);
      return;
    }
    if (!mediaStreamHasLiveAudio(stream)) {
      stopMediaStream(stream);
      releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
      const message = t("voiceSecretaryMicNotFound", {
        defaultValue: "No microphone was found or the selected microphone is unavailable.",
      });
      setSpeechError(message);
      showError(message);
      return;
    }

    // Browser SpeechRecognition owns its own capture lifecycle. Keep getUserMedia
    // as a permission/device probe only; using its track "ended" as truth can
    // stop otherwise valid dictation sessions when the auxiliary stream closes.
    stopMediaStream(stream);
    mediaStreamRef.current = null;
    setRecording(true);
    setInterimTranscript("");
    setSpeechError("");
    refreshVoiceCaptureLock(voiceCaptureOwnerIdRef.current, gid);
    void loadAudioDevices();

    const SpeechRecognitionCtor = SpeechRecognition;
    const stopAfterFatalSpeechFailure = (recognition: BrowserSpeechRecognition | null, message: string, showToast = true) => {
      browserSpeechHadErrorRef.current = true;
      browserSpeechStopRequestedRef.current = true;
      clearBrowserSpeechRestartTimer();
      clearBrowserSpeechStopFinalizeTimer();
      clearBrowserSpeechMediaHandlers();
      if (recognition && recognitionRef.current === recognition) recognitionRef.current = null;
      abortBrowserSpeechRecognition(recognition);
      stopMediaStream(mediaStreamRef.current);
      mediaStreamRef.current = null;
      void flushBrowserTranscriptWindow("meeting_window");
      releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
      setRecording(false);
      setInterimTranscript("");
      setSpeechError(message);
      if (showToast) showError(message);
    };

    function startRecognitionCycle(delayMs = 0): void {
      const runCycle = () => {
        browserSpeechRestartTimerRef.current = null;
        if (browserSpeechStopRequestedRef.current || !assistantEnabled || !browserSpeechReady) {
          recognitionRef.current = null;
          clearBrowserSpeechMediaHandlers();
          stopMediaStream(mediaStreamRef.current);
          mediaStreamRef.current = null;
          releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
          setRecording(false);
          setInterimTranscript("");
          if (!browserSpeechStopRequestedRef.current) void flushBrowserTranscriptWindow("meeting_window");
          return;
        }

        const recognition = new SpeechRecognitionCtor();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = effectiveRecognitionLanguage;
        recognition.maxAlternatives = 1;
        recognition.onspeechstart = () => {
          clearTranscriptFlushTimer();
        };
        recognition.onspeechend = () => {
          scheduleTranscriptFlush("speech_end", { preserveExisting: true });
        };
        recognition.onresult = (event) => {
          let finalText = "";
          let interimText = "";
          for (let resultIndex = event.resultIndex; resultIndex < event.results.length; resultIndex += 1) {
            const result = event.results[resultIndex];
            let text = "";
            for (let altIndex = 0; altIndex < result.length; altIndex += 1) {
              text += result[altIndex]?.transcript || "";
            }
            if (result.isFinal) finalText += text;
            else interimText += text;
          }
          const hasFinalText = Boolean(finalText.trim());
          const cleanInterimText = interimText.replace(/\s+/g, " ").trim();
          if (hasFinalText || cleanInterimText) {
            browserSpeechHadErrorRef.current = false;
            browserSpeechTransientErrorCountRef.current = 0;
            clearTranscriptFlushTimer();
          }
          if (hasFinalText) {
            queueBrowserFinalTranscript(finalText);
          }
          if (hasFinalText || cleanInterimText) {
            scheduleTranscriptFlush("result_idle");
          }
          setInterimTranscript(cleanInterimText);
        };
        recognition.onerror = (event) => {
          const code = String(event.error || "").trim();
          const fatal = BROWSER_SPEECH_FATAL_ERRORS.has(code);
          const recoverable = !fatal && (BROWSER_SPEECH_RECOVERABLE_ERRORS.has(code) || !code);
          if (recoverable) {
            browserSpeechHadErrorRef.current = true;
            // A real missing microphone is already caught by the start-time
            // getUserMedia probe. During a running Web Speech session,
            // Chromium/Edge can report audio-capture for service lifecycle
            // hiccups, so treat it like no-speech instead of burning the fatal
            // retry budget.
            const countsAsTransientFailure = code !== "no-speech" && code !== "aborted" && code !== "audio-capture";
            if (countsAsTransientFailure) browserSpeechTransientErrorCountRef.current += 1;
            setInterimTranscript("");
            if (
              countsAsTransientFailure
              && browserSpeechTransientErrorCountRef.current >= BROWSER_SPEECH_MAX_TRANSIENT_ERRORS
            ) {
              const message = code === "audio-capture"
                ? t("voiceSecretaryMicNotFound", {
                    defaultValue: "No microphone was found or the selected microphone is unavailable.",
                  })
                : code
                  ? t("voiceSecretarySpeechError", { code, defaultValue: "Speech recognition error: {{code}}" })
                  : t("voiceSecretarySpeechErrorGeneric", { defaultValue: "Speech recognition stopped unexpectedly." });
              stopAfterFatalSpeechFailure(recognition, message, code !== "no-speech" && code !== "aborted");
              return;
            }
            if (code && code !== "no-speech" && code !== "aborted") {
              setSpeechError(t("voiceSecretarySpeechRecovering", {
                code,
                defaultValue: "Browser speech recognition is reconnecting after a temporary {{code}} event. Recording is still on.",
              }));
            }
            return;
          }

          const message = code === "not-allowed" || code === "service-not-allowed"
            ? t("voiceSecretaryMicPermissionBlocked", {
                defaultValue: "Microphone permission is blocked. Allow microphone access for this site in the browser, then try again.",
              })
            : code === "audio-capture"
              ? t("voiceSecretaryMicNotFound", {
                  defaultValue: "No microphone was found or the selected microphone is unavailable.",
                })
              : code
                ? t("voiceSecretarySpeechError", { code, defaultValue: "Speech recognition error: {{code}}" })
                : t("voiceSecretarySpeechErrorGeneric", { defaultValue: "Speech recognition stopped unexpectedly." });
          stopAfterFatalSpeechFailure(recognition, message, code !== "no-speech" && code !== "aborted");
        };
        recognition.onend = () => {
          clearBrowserSpeechStopFinalizeTimer();
          const stoppedByUser = browserSpeechStopRequestedRef.current;
          const shouldRestart = !stoppedByUser && assistantEnabled && browserSpeechReady;
          const restartDelay = browserSpeechHadErrorRef.current
            ? browserSpeechRestartDelayMs(browserSpeechTransientErrorCountRef.current)
            : 250;
          setInterimTranscript("");
          if (stoppedByUser) {
            void flushBrowserTranscriptWindow("push_to_talk_stop");
          } else if (!shouldRestart) {
            void flushBrowserTranscriptWindow("meeting_window");
          }
          if (recognitionRef.current === recognition) recognitionRef.current = null;
          if (shouldRestart) {
            setRecording(true);
            startRecognitionCycle(restartDelay);
            return;
          }
          clearBrowserSpeechMediaHandlers();
          stopMediaStream(mediaStreamRef.current);
          mediaStreamRef.current = null;
          releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
          setRecording(false);
          if (!browserSpeechReceivedFinalRef.current && !browserSpeechHadErrorRef.current) {
            setSpeechError(t("voiceSecretaryBrowserAsrEndedWithoutTranscript", {
              defaultValue: "Browser ASR stopped without returning transcript. Check the microphone connection, site permission, and system input device, then try again.",
            }));
          }
        };

        try {
          recognitionRef.current = recognition;
          setInterimTranscript("");
          setRecording(true);
          refreshVoiceCaptureLock(voiceCaptureOwnerIdRef.current, gid);
          browserSpeechHadErrorRef.current = false;
          recognition.start();
          setSpeechError("");
          if (captureMode === "prompt" && !promptHoldActiveRef.current) {
            window.setTimeout(() => {
              if (recordingRef.current && !promptHoldActiveRef.current) stopBrowserSpeech();
            }, 0);
          }
        } catch {
          if (recognitionRef.current === recognition) recognitionRef.current = null;
          browserSpeechHadErrorRef.current = true;
          browserSpeechTransientErrorCountRef.current += 1;
          setSpeechError(t("voiceSecretarySpeechRecovering", {
            code: "start-failed",
            defaultValue: "Browser speech recognition is reconnecting after a temporary {{code}} event. Recording is still on.",
          }));
          if (
            browserSpeechTransientErrorCountRef.current < BROWSER_SPEECH_MAX_TRANSIENT_ERRORS
            && !browserSpeechStopRequestedRef.current
            && assistantEnabled
            && browserSpeechReady
          ) {
            startRecognitionCycle(browserSpeechRestartDelayMs(browserSpeechTransientErrorCountRef.current));
            return;
          }
          stopAfterFatalSpeechFailure(recognition, t("voiceSecretarySpeechStartFailed", {
            defaultValue: "Could not start browser speech recognition.",
          }));
        }
      };

      clearBrowserSpeechRestartTimer();
      if (delayMs > 0) {
        browserSpeechRestartTimerRef.current = window.setTimeout(runCycle, delayMs);
        return;
      }
      setSpeechError("");
      runCycle();
    }

    startRecognitionCycle();
  }, [
    assistantEnabled,
    browserSpeechReady,
    captureMode,
    clearBrowserSpeechMediaHandlers,
    clearBrowserSpeechRestartTimer,
    clearBrowserSpeechStopFinalizeTimer,
    clearTranscriptFlushTimer,
    effectiveRecognitionLanguage,
    flushBrowserTranscriptWindow,
    getAudioCaptureErrorMessage,
    getAudioSupportIssueMessage,
    getBrowserSpeechIssueMessage,
    loadAudioDevices,
    queueBrowserFinalTranscript,
    scheduleTranscriptFlush,
    selectedGroupId,
    showError,
    stopBrowserSpeech,
    t,
  ]);

  const transcribeServiceAudio = useCallback(async (chunks: Blob[], mimeType: string, gid: string) => {
    const audioBlob = new Blob(chunks, { type: mimeType || "audio/webm" });
    if (!audioBlob.size) {
      showError(t("voiceSecretaryAudioEmpty", { defaultValue: "No audio was captured." }));
      return;
    }
    setActionBusy("transcribe");
    setSpeechError("");
    try {
      const audioBase64 = await blobToBase64(audioBlob);
      const resp = await transcribeVoiceAssistantAudio(gid, {
        audioBase64,
        mimeType: audioBlob.type || mimeType || "audio/webm",
        language: effectiveRecognitionLanguage,
        by: "user",
      });
      if (!resp.ok) {
        const code = String(resp.error.code || "").trim();
        const message = code === "asr_backend_unavailable"
          ? t("voiceSecretaryServiceAsrCommandMissing", {
              defaultValue: "Audio was captured, but assistant service local ASR is not configured. Set CCCC_VOICE_SECRETARY_ASR_COMMAND on the daemon host, or switch to Browser ASR.",
            })
          : resp.error.message || t("voiceSecretaryAudioTranscribeFailed", {
              defaultValue: "Voice Secretary could not transcribe the recorded audio.",
            });
        setSpeechError(message);
        showError(message);
        await refreshAssistant({ quiet: true });
        return;
      }
      const text = String(resp.result.transcript || "").trim();
      if (text) {
        if (captureMode === "prompt") {
          await requestPromptRefine(text, "service_prompt_refine");
        } else if (captureMode === "instruction") {
          await sendInstructionTranscript(text, { triggerKind: "service_voice_instruction" });
        } else {
          await appendTranscriptSegment(text, {
            flush: true,
            source: "assistant_service_local_asr",
            triggerKind: "service_transcript",
            inputDeviceLabel: selectedAudioDeviceLabel,
          });
        }
      }
      if (!text) setAssistant(resp.result.assistant || null);
      await refreshAssistant({ quiet: true });
    } catch {
      const message = t("voiceSecretaryAudioTranscribeFailed", {
        defaultValue: "Voice Secretary could not transcribe the recorded audio.",
      });
      setSpeechError(message);
      showError(message);
    } finally {
      setActionBusy("");
    }
  }, [
    appendTranscriptSegment,
    captureMode,
    effectiveRecognitionLanguage,
    requestPromptRefine,
    refreshAssistant,
    selectedAudioDeviceLabel,
    sendInstructionTranscript,
    showError,
    t,
  ]);

  const startServiceAudio = useCallback(async () => {
    const gid = String(selectedGroupId || "").trim();
    if (!assistantEnabled) {
      showError(t("voiceSecretaryEnableFirst", { defaultValue: "Enable Voice Secretary first." }));
      return;
    }
    if (!serviceAsrReady) {
      showError(t("voiceSecretaryServiceBackendRequired", {
        defaultValue: "Switch recognition to Assistant service local ASR in Assistants settings first.",
      }));
      return;
    }
    const supportIssue = getBrowserAudioSupportIssue();
    if (supportIssue) {
      const message = getAudioSupportIssueMessage(supportIssue);
      setServiceAudioSupported(false);
      setSpeechError(message);
      showError(message);
      return;
    }
    const activeLock = await claimVoiceCaptureLock(voiceCaptureOwnerIdRef.current, gid);
    if (activeLock) {
      showError(t("voiceSecretaryAnotherRecording", {
        groupId: activeLock.groupId,
        defaultValue: "Voice Secretary is already recording in group {{groupId}} in another active tab. Stop that recording before starting another one.",
      }));
      return;
    }
    try {
      const constraints: MediaStreamConstraints = selectedAudioDeviceId
        ? { audio: { deviceId: { exact: selectedAudioDeviceId } } }
        : { audio: true };
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      const mimeType = preferredMediaRecorderMimeType();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      mediaChunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data?.size) mediaChunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        const message = t("voiceSecretaryAudioCaptureFailed", { defaultValue: "Audio capture failed." });
        setSpeechError(message);
        showError(message);
        cleanupServiceAudio();
      };
      recorder.onstop = () => {
        const chunks = [...mediaChunksRef.current];
        const recordedMimeType = recorder.mimeType || mimeType || "audio/webm";
        mediaRecorderRef.current = null;
        stopMediaStream(mediaStreamRef.current);
        mediaStreamRef.current = null;
        mediaChunksRef.current = [];
        releaseVoiceCaptureLock(voiceCaptureOwnerIdRef.current);
        setRecording(false);
        setInterimTranscript("");
        void transcribeServiceAudio(chunks, recordedMimeType, gid);
      };
      setSpeechError("");
      setInterimTranscript("");
      setRecording(true);
      recorder.start(1000);
      if (captureMode === "prompt" && !promptHoldActiveRef.current) {
        window.setTimeout(() => {
          if (recordingRef.current && !promptHoldActiveRef.current) stopServiceAudio();
        }, 0);
      }
      void loadAudioDevices();
    } catch (error) {
      cleanupServiceAudio();
      const { message, resetSelectedDevice } = getAudioCaptureErrorMessage(error);
      if (resetSelectedDevice) setSelectedAudioDeviceId("");
      setSpeechError(message);
      showError(message);
    }
  }, [
    assistantEnabled,
    cleanupServiceAudio,
    getAudioCaptureErrorMessage,
    getAudioSupportIssueMessage,
    loadAudioDevices,
    captureMode,
    selectedAudioDeviceId,
    selectedGroupId,
    serviceAsrReady,
    showError,
    t,
    transcribeServiceAudio,
    stopServiceAudio,
  ]);

  useEffect(() => {
    if (!recording) return undefined;
    const gid = String(selectedGroupId || "").trim();
    const interval = window.setInterval(() => {
      refreshVoiceCaptureLock(voiceCaptureOwnerIdRef.current, gid);
    }, 5000);
    return () => window.clearInterval(interval);
  }, [recording, selectedGroupId]);

  const setAssistantEnabledForGroup = useCallback(async (nextEnabled: boolean) => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid) return false;
    setActionBusy("enable");
    try {
      if (!nextEnabled && recordingRef.current) {
        stopCurrentRecording();
      }
      const resp = await updateAssistantSettings(gid, "voice_secretary", {
        enabled: nextEnabled,
      });
      if (!resp.ok) {
        showError(resp.error.message);
        return false;
      }
      setAssistant(resp.result.assistant || null);
      showNotice({
        message: nextEnabled
          ? t("voiceSecretaryEnabledForGroup", { defaultValue: "Voice Secretary enabled for this group." })
          : t("voiceSecretaryDisabledForGroup", { defaultValue: "Voice Secretary disabled for this group." }),
      });
      return true;
    } catch {
      showError(nextEnabled
        ? t("voiceSecretaryEnableFailed", { defaultValue: "Failed to enable Voice Secretary." })
        : t("voiceSecretaryDisableFailed", { defaultValue: "Failed to disable Voice Secretary." }));
      return false;
    } finally {
      setActionBusy("");
    }
  }, [selectedGroupId, showError, showNotice, stopCurrentRecording, t]);

  const updateRecognitionLanguage = useCallback(async (nextLanguage: string) => {
    const gid = String(selectedGroupId || "").trim();
    const language = String(nextLanguage || "auto").trim() || "auto";
    if (!gid || language === configuredRecognitionLanguage) return;
    const previousAssistant = assistant;
    const nextConfig = { ...(assistant?.config || {}), recognition_language: language };
    setAssistant((current) => current
      ? { ...current, config: { ...(current.config || {}), recognition_language: language } }
      : current);
    setActionBusy("voice_language");
    try {
      const resp = await updateAssistantSettings(gid, "voice_secretary", {
        config: nextConfig,
        by: "user",
      });
      if (!resp.ok) {
        setAssistant(previousAssistant || null);
        showError(resp.error.message);
        return;
      }
      setAssistant(resp.result.assistant || null);
    } catch {
      setAssistant(previousAssistant || null);
      showError(t("voiceSecretaryLanguageSaveFailed", { defaultValue: "Failed to update Voice Secretary language." }));
    } finally {
      setActionBusy("");
    }
  }, [
    assistant,
    configuredRecognitionLanguage,
    selectedGroupId,
    showError,
    t,
  ]);

  const persistCurrentDocument = useCallback(async (): Promise<AssistantVoiceDocument | null> => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid) return null;
    const resp = await saveVoiceAssistantDocument(gid, {
      documentPath: activeDocumentKey || activeDocumentId || captureTargetDocumentId,
      content: documentDraft,
      status: activeDocument?.status || "active",
      by: "user",
    });
    if (!resp.ok) {
      showError(resp.error.message);
      return null;
    }
    applyDocumentMutationResult(resp.result.document, resp.result.assistant);
    return resp.result.document || null;
  }, [
    activeDocumentKey,
    activeDocument?.status,
    activeDocumentId,
    applyDocumentMutationResult,
    captureTargetDocumentId,
    documentDraft,
    selectedGroupId,
    showError,
  ]);

  const saveDocument = useCallback(async () => {
    setActionBusy("save_doc");
    try {
      const document = await persistCurrentDocument();
      if (!document) return;
      setDocumentEditing(false);
      showNotice({ message: t("voiceSecretaryDocumentSaved", { defaultValue: "Voice Secretary working document saved." }) });
    } catch {
      showError(t("voiceSecretaryDocumentSaveFailed", { defaultValue: "Failed to save Voice Secretary working document." }));
    } finally {
      setActionBusy("");
    }
  }, [
    persistCurrentDocument,
    showError,
    showNotice,
    t,
  ]);

  const startCreateDocument = useCallback(() => {
    if (documentHasUnsavedEdits) {
      const confirmed = window.confirm(t("voiceSecretaryNewDocumentConfirm", {
        defaultValue: "Create a new document and discard unsaved edits in this panel?",
      }));
      if (!confirmed) return;
    }
    setNewDocumentTitleDraft(t("voiceSecretaryDefaultDocumentTitle", { defaultValue: "Untitled document" }));
    setCreatingDocument(true);
  }, [documentHasUnsavedEdits, t]);

  const cancelCreateDocument = useCallback(() => {
    if (actionBusy === "new_doc") return;
    setCreatingDocument(false);
    setNewDocumentTitleDraft("");
  }, [actionBusy]);

  const createDocument = useCallback(async () => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid) return;
    const title = newDocumentTitleDraft.trim() || t("voiceSecretaryDefaultDocumentTitle", { defaultValue: "Untitled document" });
    setActionBusy("new_doc");
    try {
      const resp = await saveVoiceAssistantDocument(gid, {
        title,
        content: "",
        createNew: true,
        by: "user",
      });
      if (!resp.ok) {
        showError(resp.error.message);
        return;
      }
      applyDocumentMutationResult(resp.result.document, resp.result.assistant);
      const docId = voiceDocumentKey(resp.result.document);
      if (docId) {
        setActiveDocumentId(docId);
        setCaptureTargetDocumentId(docId);
        captureTargetDocumentIdRef.current = docId;
        loadDocumentDraft(resp.result.document || null);
      }
      setCreatingDocument(false);
      setNewDocumentTitleDraft("");
      setDocumentEditing(true);
      showNotice({ message: t("voiceSecretaryDocumentCreated", { defaultValue: "Voice Secretary working document created." }) });
    } catch {
      showError(t("voiceSecretaryDocumentCreateFailed", { defaultValue: "Failed to create Voice Secretary working document." }));
    } finally {
      setActionBusy("");
    }
  }, [
    applyDocumentMutationResult,
    loadDocumentDraft,
    newDocumentTitleDraft,
    selectedGroupId,
    showError,
    showNotice,
    t,
  ]);

  const sendDocumentInstruction = useCallback(async () => {
    const gid = String(selectedGroupId || "").trim();
    const instruction = documentInstruction.trim();
    if (!gid || !instruction) return;
    if (documentHasUnsavedEdits) {
      showError(t("voiceSecretaryDocumentUnsavedBeforeRequest", {
        defaultValue: "Save or discard local document edits before sending a request to Voice Secretary.",
      }));
      return;
    }
    const docId = activeDocumentKey || activeDocumentId || captureTargetDocumentId;
    const targetDocument = docId
      ? documents.find((document) => voiceDocumentKey(document) === docId || document.document_id === docId) || null
      : null;
    if (!docId || !targetDocument || String(targetDocument.status || "active").trim().toLowerCase() === "archived") {
      showError(t("voiceSecretaryDocumentRequestStale", {
        defaultValue: "This document is no longer active. Refresh or choose another document before sending a request.",
      }));
      await refreshAssistant({ quiet: true });
      return;
    }
    setActionBusy("instruct_doc");
    try {
      const resp = await sendVoiceAssistantDocumentInstruction(gid, docId, {
        instruction,
        documentPath: docId,
        trigger: {
          trigger_kind: "user_instruction",
          mode: "meeting",
          recognition_backend: recognitionBackend,
          language: effectiveRecognitionLanguage,
        },
        by: "user",
      });
      if (!resp.ok) {
        showError(resp.error.message);
        return;
      }
      applyDocumentMutationResult(resp.result.document, resp.result.assistant);
      setDocumentInstruction("");
      setDocumentEditing(false);
      showNotice({
        message: t("voiceSecretaryDocumentInstructionQueued", { defaultValue: "Request sent to Voice Secretary." }),
      });
    } catch {
      showError(t("voiceSecretaryDocumentInstructionFailed", { defaultValue: "Failed to send the request to Voice Secretary." }));
    } finally {
      setActionBusy("");
    }
  }, [
    activeDocumentKey,
    activeDocumentId,
    applyDocumentMutationResult,
    captureTargetDocumentId,
    documentHasUnsavedEdits,
    documentInstruction,
    documents,
    effectiveRecognitionLanguage,
    recognitionBackend,
    refreshAssistant,
    selectedGroupId,
    showError,
    showNotice,
    t,
  ]);

  const selectDocument = useCallback(async (document: AssistantVoiceDocument) => {
    const nextId = voiceDocumentKey(document);
    const currentId = activeDocumentKey || activeDocumentId;
    if (!nextId || nextId === currentId) return;
    if (documentHasUnsavedEdits) {
      const confirmed = window.confirm(t("voiceSecretarySwitchDocumentConfirm", {
        defaultValue: "Switch documents and discard unsaved edits in this panel?",
      }));
      if (!confirmed) return;
    }
    setActiveDocumentId(nextId);
    loadDocumentDraft(document);
    setDocumentEditing(false);
    setCreatingDocument(false);
    setNewDocumentTitleDraft("");
  }, [
    activeDocumentKey,
    activeDocumentId,
    documentHasUnsavedEdits,
    loadDocumentDraft,
    t,
  ]);

  const setCaptureTargetDocument = useCallback(async (document: AssistantVoiceDocument) => {
    const nextId = voiceDocumentKey(document);
    const currentId = String(captureTargetDocumentIdRef.current || "").trim();
    if (!nextId || nextId === currentId) return;
    const gid = String(selectedGroupId || "").trim();
    if (!gid) return;
    setActionBusy("capture_target");
    try {
      clearTranscriptFlushTimer();
      clearTranscriptMaxFlushTimer();
      if (recording && currentId) {
        await flushBrowserTranscriptWindow("document_switch", { documentPath: currentId });
      }
      const resp = await selectVoiceAssistantDocument(gid, nextId, { by: "user" });
      if (!resp.ok) {
        showError(resp.error.message);
        await refreshAssistant({ quiet: true });
        return;
      }
      setCaptureTargetDocumentId(nextId);
      captureTargetDocumentIdRef.current = nextId;
      applyDocumentMutationResult(resp.result.document, resp.result.assistant);
      showNotice({
        message: t("voiceSecretaryCaptureTargetChanged", {
          title: String(resp.result.document?.title || document.title || ""),
          defaultValue: "Default document changed to {{title}}.",
        }),
      });
    } catch {
      showError(t("voiceSecretaryCaptureTargetChangeFailed", { defaultValue: "Failed to change the default document." }));
    } finally {
      setActionBusy("");
    }
  }, [
    applyDocumentMutationResult,
    clearTranscriptMaxFlushTimer,
    clearTranscriptFlushTimer,
    flushBrowserTranscriptWindow,
    recording,
    refreshAssistant,
    selectedGroupId,
    showError,
    showNotice,
    t,
  ]);

  const archiveDocument = useCallback(async (targetDocument?: AssistantVoiceDocument | null) => {
    const gid = String(selectedGroupId || "").trim();
    const docId = targetDocument ? voiceDocumentKey(targetDocument) : (activeDocumentKey || activeDocumentId);
    if (!gid || !docId) return;
    const title = String(targetDocument?.title || documents.find((item) => voiceDocumentKey(item) === docId)?.title || docId).trim();
    const confirmed = window.confirm(t("voiceSecretaryArchiveDocumentConfirm", {
      title,
      defaultValue: "Archive document \"{{title}}\"?",
    }));
    if (!confirmed) return;
    const isActiveTarget = docId === (activeDocumentKey || activeDocumentId);
    setActionBusy("archive_doc");
    try {
      const resp = await archiveVoiceAssistantDocument(gid, docId, { by: "user" });
      if (!resp.ok) {
        showError(resp.error.message);
        return;
      }
      archivedDocumentIdsRef.current.add(docId);
      setDocuments((prev) => prev.filter((item) => voiceDocumentKey(item) !== docId));
      if (isActiveTarget) {
        setActiveDocumentId("");
        loadDocumentDraft(null);
        setDocumentEditing(false);
      }
      if (captureTargetDocumentIdRef.current === docId) {
        captureTargetDocumentIdRef.current = "";
        setCaptureTargetDocumentId("");
      }
      showNotice({ message: t("voiceSecretaryDocumentArchived", { defaultValue: "Voice Secretary working document archived." }) });
      await refreshAssistant({ quiet: true });
    } catch {
      showError(t("voiceSecretaryDocumentArchiveFailed", { defaultValue: "Failed to archive the Voice Secretary document." }));
    } finally {
      setActionBusy("");
    }
  }, [activeDocumentId, activeDocumentKey, documents, loadDocumentDraft, refreshAssistant, selectedGroupId, showError, showNotice, t]);

  const downloadCurrentDocument = useCallback(() => {
    if (!activeDocument) return;
    const fileName = voiceDocumentDownloadFileName(activeDocument, documentDisplayTitle);
    downloadMarkdownDocument(fileName, documentDraft);
    showNotice({
      message: t("voiceSecretaryDocumentDownloaded", {
        fileName,
        defaultValue: "Downloaded {{fileName}}.",
      }),
    });
  }, [activeDocument, documentDisplayTitle, documentDraft, showNotice, t]);

  const workspaceRecordLabel = recording
    ? t("voiceSecretaryStopShort", { defaultValue: "Stop" })
    : t("voiceSecretaryRecordShort", { defaultValue: "Record" });
  const captureStartTitle = captureMode === "prompt"
    ? t("voiceSecretaryPromptModeStartHint", { defaultValue: "Hold to dictate a prompt into the message composer" })
    : captureMode === "instruction"
      ? t("voiceSecretaryInstructionModeStartHint", { defaultValue: "Record a request for Voice Secretary" })
      : t("voiceSecretaryStartDictation", { defaultValue: "Start recording" });
  const assistantRowModeOptions: Array<{ key: VoiceSecretaryCaptureMode; label: string; description: string }> = [
    {
      key: "document",
      label: t("voiceSecretaryModeDocument", { defaultValue: "Doc" }),
      description: t("voiceSecretaryModeDocumentDesc", { defaultValue: "Record into working docs" }),
    },
    {
      key: "prompt",
      label: t("voiceSecretaryModePrompt", { defaultValue: "Prompt" }),
      description: t("voiceSecretaryModePromptDesc", { defaultValue: "Fill the composer" }),
    },
  ];
  const statusLabel = recording
    ? t("voiceSecretaryRecording", { defaultValue: "Recording" })
    : assistantEnabled
      ? t("voiceSecretaryEnabled", { defaultValue: "Enabled" })
      : t("voiceSecretaryNotEnabled", { defaultValue: "Not enabled" });

  const dictationSupported = browserSpeechReady
    ? !browserSpeechSupportIssue && speechSupported
    : serviceAsrReady
      ? serviceAudioSupportIssue
        ? false
        : serviceAudioSupported
      : false;
  const startDictation = serviceAsrReady ? startServiceAudio : startBrowserSpeech;
  const activeDocumentPath = String(activeDocument?.workspace_path || "").trim();
  const liveTranscriptText = mergeTranscriptChunks(browserFinalTranscriptBufferPreview, interimTranscript);
  const openButtonLabel = open
    ? t("voiceSecretaryClose", { defaultValue: "Close Voice Secretary" })
    : recording
      ? t("voiceSecretaryOpenRecordingWorkspace", { defaultValue: "Expand Voice Secretary workspace - recording" })
      : t("voiceSecretaryOpenWorkspace", { defaultValue: "Expand Voice Secretary workspace" });
  const openButtonIconSizePx = buttonClassName
    ? buttonSizePx
    : Math.max(20, Math.min(26, Math.round(buttonSizePx - 14)));
  const promptDraftWaiting = Boolean(pendingPromptRequestId && !pendingPromptDraft);
  const promptDraftTitle = t("voiceSecretaryPromptDraftWaiting", { defaultValue: "Voice Secretary is refining the prompt..." });
  const documentsCountLabel = t("voiceSecretaryDocumentsCount", { count: documents.length, defaultValue: "{{count}} docs" });
  const transcriptFeedHint = t("voiceSecretaryTranscriptFeedHint", {
    title: documentDisplayTitle,
    defaultValue: "Recent ASR text for this tab. Stable chunks go to {{title}}.",
  });
  const headerStatusHint = !assistantEnabled
    ? t("voiceSecretaryDisabledHint", {
        defaultValue: "Voice Secretary is off for this group. Enable the assistant here or in Settings > Assistants before recording.",
      })
    : speechError.trim();
  const startAfterEnableRef = useRef(false);
  const assistantRowCurrentMode = assistantRowModeOptions.find((option) => option.key === captureMode) || assistantRowModeOptions[0];
  const assistantRowControlLabel = recording
    ? t("voiceSecretaryStopDictation", { defaultValue: "Stop recording" })
    : !assistantEnabled
      ? t("voiceSecretaryTurnOnAndRecord", { defaultValue: "Turn on and start recording" })
      : captureStartTitle;
  useEffect(() => {
    if (!assistantEnabled || !startAfterEnableRef.current) return;
    startAfterEnableRef.current = false;
    void startDictation();
  }, [assistantEnabled, startDictation]);
  const handleAssistantRowModeChange = useCallback((nextMode: VoiceSecretaryCaptureMode) => {
    onCaptureModeChange?.(nextMode);
    setShowAssistantModeMenu(false);
  }, [onCaptureModeChange]);
  const handleAssistantRowRecordClick = useCallback(async (event?: ReactMouseEvent<HTMLButtonElement>) => {
    event?.preventDefault();
    if (recording) {
      stopCurrentRecording();
      return;
    }
    if (!assistantEnabled) {
      startAfterEnableRef.current = true;
      const enabled = await setAssistantEnabledForGroup(true);
      if (!enabled) startAfterEnableRef.current = false;
      return;
    }
    void startDictation();
  }, [
    assistantEnabled,
    recording,
    setAssistantEnabledForGroup,
    startDictation,
    stopCurrentRecording,
  ]);
  const handleAssistantRowPromptPointerDown = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (captureMode !== "prompt" || recording || !!actionBusy || controlDisabled || !dictationSupported) return;
    promptHoldActiveRef.current = true;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    void handleAssistantRowRecordClick();
  }, [
    actionBusy,
    captureMode,
    controlDisabled,
    dictationSupported,
    handleAssistantRowRecordClick,
    recording,
  ]);
  const handleAssistantRowPromptPointerUp = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (captureMode !== "prompt") return;
    promptHoldActiveRef.current = false;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    if (recordingRef.current) stopCurrentRecording();
  }, [captureMode, stopCurrentRecording]);
  const handleAssistantRowPromptPointerCancel = useCallback(() => {
    if (captureMode !== "prompt") return;
    promptHoldActiveRef.current = false;
    if (recordingRef.current) stopCurrentRecording();
  }, [captureMode, stopCurrentRecording]);

  return (
    <div ref={rootRef} className={classNames("relative", isAssistantRow ? "w-auto shrink-0" : "self-end")}>
      {open && typeof document !== "undefined"
        ? createPortal(
          <div
            className="fixed inset-0 z-[180] flex items-end justify-center p-0 sm:items-center sm:p-4"
            aria-hidden={undefined}
          >
            <div
              className="absolute inset-0 glass-overlay"
              onPointerDown={(event) => {
                if (event.target === event.currentTarget) closePanel();
              }}
              aria-hidden="true"
            />
            <section
              ref={modalRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby="voice-secretary-sheet-title"
              aria-describedby="voice-secretary-sheet-description"
              className={classNames(
                "relative z-[181] flex h-[min(92vh,58rem)] w-full max-w-[88rem] flex-col overflow-hidden rounded-t-[28px] border p-3 pt-6 shadow-2xl glass-modal sm:w-[min(94vw,88rem)] sm:rounded-[30px]",
                isDark ? "border-white/10 bg-slate-950/96" : "border-black/10 bg-white/96",
              )}
              onPointerDown={(event) => event.stopPropagation()}
            >
            <div id="voice-secretary-sheet-title" className="sr-only">
              {t("voiceSecretaryTitle", { defaultValue: "Voice Secretary" })}
            </div>
            <div id="voice-secretary-sheet-description" className="sr-only">
              {t("voiceSecretaryWorkspaceHint", {
                defaultValue: "Capture speech, maintain working documents, and ask the secretary to refine or send them.",
              })}
            </div>
            <div className={classNames(
              "shrink-0 border-b px-4 pb-3 pt-2 sm:px-5 sm:pb-3 sm:pt-3",
              isDark ? "border-white/10" : "border-black/10",
            )}>
              <div className="flex flex-wrap items-end justify-between gap-4 pr-8">
                <div className="min-w-0 flex-1">
                  <div className={classNames("text-lg font-semibold tracking-[-0.02em]", isDark ? "text-slate-100" : "text-gray-900")}>
                    {t("voiceSecretaryTitle", { defaultValue: "Voice Secretary" })}
                  </div>
                  <div className={classNames("mt-1 text-xs leading-5", isDark ? "text-slate-400" : "text-gray-500")}>
                    {t("voiceSecretaryWorkspaceHint", {
                      defaultValue: "Capture speech, maintain working documents, and ask the secretary to refine or send them.",
                    })}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={assistantEnabled}
                      className={classNames(
                        "inline-flex min-h-[34px] items-center gap-2 rounded-full border px-2.5 py-1.5 text-[11px] font-semibold whitespace-nowrap transition-colors disabled:opacity-60",
                        isDark
                          ? "border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/10"
                          : "border-black/10 bg-white text-gray-700 hover:bg-black/5",
                      )}
                      onClick={() => void setAssistantEnabledForGroup(!assistantEnabled)}
                      disabled={actionBusy === "enable" || !selectedGroupId}
                      title={assistantEnabled
                        ? t("voiceSecretaryTurnOff", { defaultValue: "Turn off" })
                        : t("voiceSecretaryTurnOn", { defaultValue: "Turn on" })}
                    >
                      <span
                        aria-hidden="true"
                        className={classNames(
                          "relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors",
                          assistantEnabled
                            ? isDark ? "bg-white" : "bg-[rgb(35,36,37)]"
                            : isDark ? "bg-white/15" : "bg-gray-300",
                        )}
                      >
                        <span
                          className={classNames(
                            "absolute top-0.5 h-4 w-4 rounded-full shadow-sm transition-transform",
                            assistantEnabled
                              ? isDark ? "bg-slate-950" : "bg-white"
                              : "bg-white",
                            assistantEnabled ? "translate-x-4" : "translate-x-0.5",
                          )}
                        />
                      </span>
                      <span>
                        {actionBusy === "enable"
                          ? t("voiceSecretarySavingState", { defaultValue: "Saving..." })
                          : assistantEnabled
                            ? t("voiceSecretaryEnabledShort", { defaultValue: "On" })
                            : t("voiceSecretaryDisabledShort", { defaultValue: "Off" })}
                      </span>
                    </button>
                    <span
                      className={classNames(
                        "inline-flex min-h-[34px] items-center rounded-full px-2.5 py-1 text-[11px] font-semibold whitespace-nowrap",
                        isDark ? "bg-white/10 text-slate-200" : "bg-[rgb(245,245,245)] text-[rgb(35,36,37)]",
                      )}
                    >
                      {loading ? t("loadingContext", { defaultValue: "Loading context..." }) : statusLabel}
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 self-end">
                  {assistantEnabled ? (
                    <>
                      <label className="inline-flex items-center gap-2 text-[11px] font-semibold text-[var(--color-text-secondary)]">
                        <span>{t("voiceSecretaryLanguage", { defaultValue: "Language" })}</span>
                        <GroupCombobox
                          items={voiceLanguageOptions.map((optionValue) => ({
                            value: optionValue,
                            label: voiceLanguageLabel(optionValue),
                          }))}
                          value={configuredRecognitionLanguage}
                          onChange={(nextValue) => void updateRecognitionLanguage(nextValue)}
                          placeholder={t("voiceSecretaryLanguage", { defaultValue: "Language" })}
                          searchPlaceholder={t("voiceSecretaryLanguage", { defaultValue: "Language" })}
                          emptyText={t("common:noResults", { defaultValue: "没有匹配结果" })}
                          ariaLabel={t("voiceSecretaryLanguage", { defaultValue: "Language" })}
                          triggerClassName={classNames(
                            "min-h-[38px] min-w-[7.5rem] rounded-full border px-3 py-2 text-xs font-semibold transition-colors",
                            isDark
                              ? "border-white/10 bg-white/[0.06] text-slate-100 focus:border-white/30"
                              : "border-black/10 bg-white text-gray-800 focus:border-black/25",
                          )}
                          contentClassName="p-0"
                          disabled={recording || !!actionBusy}
                          searchable={false}
                          matchTriggerWidth
                        />
                      </label>
                      {serviceAsrReady ? (
                        <>
                          <label className="inline-flex min-w-[13rem] items-center gap-1.5 text-[11px] font-semibold text-[var(--color-text-secondary)]">
                            <span>{t("voiceSecretaryMicDevice", { defaultValue: "Microphone" })}</span>
                            <select
                              value={selectedAudioDeviceId}
                              onChange={(event) => setSelectedAudioDeviceId(event.target.value)}
                              className={classNames(
                                "min-w-0 flex-1 rounded-lg border px-2 py-1.5 text-[11px] outline-none transition-colors",
                                isDark
                                  ? "border-white/10 bg-white/[0.06] text-slate-100 focus:border-white/30"
                                  : "border-black/10 bg-white text-gray-800 focus:border-black/25",
                              )}
                              disabled={recording || !!actionBusy}
                            >
                              <option value="">
                                {t("voiceSecretaryDefaultMic", { defaultValue: "System default microphone" })}
                              </option>
                              {audioDevices.map((device, index) => (
                                <option key={device.deviceId || `audio-${index}`} value={device.deviceId}>
                                  {device.label || t("voiceSecretaryMicDeviceFallback", {
                                    index: index + 1,
                                    defaultValue: "Microphone {{index}}",
                                  })}
                                </option>
                              ))}
                            </select>
                          </label>
                          <button
                            type="button"
                            className={classNames(
                              "inline-flex min-h-[34px] items-center rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold whitespace-nowrap transition-colors disabled:opacity-60",
                              isDark ? "border-white/10 text-slate-300 hover:bg-white/10" : "border-black/10 bg-white text-gray-700 hover:bg-black/5",
                            )}
                            onClick={() => void loadAudioDevices()}
                            disabled={recording || !!actionBusy}
                          >
                            {t("voiceSecretaryRefreshDevices", { defaultValue: "Refresh devices" })}
                          </button>
                        </>
                      ) : null}
                      <button
                        type="button"
                        className={classNames(
                          "inline-flex min-h-[38px] items-center gap-2.5 rounded-full border px-3 py-2 text-xs font-semibold whitespace-nowrap transition-colors disabled:opacity-60",
                          recording
                            ? isDark
                              ? "border-rose-300/35 bg-rose-500/15 text-rose-100 hover:bg-rose-500/22"
                              : "border-rose-200 bg-rose-50 text-rose-800 hover:bg-rose-100"
                            : isDark
                              ? "border-white/10 bg-white/[0.06] text-slate-100 hover:bg-white/10"
                              : "border-black/10 bg-white text-gray-800 hover:bg-black/5",
                        )}
                        onPointerDown={captureMode === "prompt" && !recording ? (event) => {
                          if (!!actionBusy || (!recording && !dictationSupported)) return;
                          promptHoldActiveRef.current = true;
                          event.currentTarget.setPointerCapture?.(event.pointerId);
                          void startDictation();
                        } : undefined}
                        onPointerUp={captureMode === "prompt" ? (event) => {
                          promptHoldActiveRef.current = false;
                          event.currentTarget.releasePointerCapture?.(event.pointerId);
                          if (recordingRef.current) stopCurrentRecording();
                        } : undefined}
                        onPointerCancel={captureMode === "prompt" ? () => {
                          promptHoldActiveRef.current = false;
                          if (recordingRef.current) stopCurrentRecording();
                        } : undefined}
                        onClick={(event) => {
                          if (captureMode === "prompt") {
                            event.preventDefault();
                            return;
                          }
                          if (recording) stopCurrentRecording();
                          else void startDictation();
                        }}
                        disabled={!!actionBusy || (!recording && !dictationSupported)}
                        title={recording
                          ? t("voiceSecretaryStopDictation", { defaultValue: "Stop recording" })
                          : captureStartTitle}
                      >
                        <span
                          aria-hidden="true"
                          className={classNames(
                            "inline-flex h-5 w-5 items-center justify-center rounded-full",
                            recording
                              ? isDark ? "bg-rose-300/15" : "bg-white"
                              : isDark ? "bg-rose-400/15 text-rose-100" : "bg-rose-50 text-rose-700",
                          )}
                        >
                          {recording ? <StopIcon size={12} /> : <span className="h-2.5 w-2.5 rounded-full bg-rose-600" />}
                        </span>
                        {recording
                          ? t("voiceSecretaryStopDictation", { defaultValue: "Stop recording" })
                          : workspaceRecordLabel}
                      </button>
                    </>
                  ) : null}
                </div>
              </div>

              <div className="mt-2">
                {headerStatusHint ? (
                  <div className={classNames("text-[11px] leading-5", isDark ? "text-slate-400" : "text-gray-500")}>
                    {headerStatusHint}
                  </div>
                ) : null}
              </div>
            </div>

            {promptDraftWaiting ? (
              <div
                className={classNames(
                  "mx-4 mt-4 flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-2xl border px-3 py-2 sm:mx-5",
                  isDark
                    ? "border-amber-200/15 bg-[linear-gradient(135deg,rgba(245,158,11,0.14),rgba(251,191,36,0.07),rgba(255,255,255,0.03))] text-amber-50"
                    : "border-amber-200 bg-[linear-gradient(135deg,rgba(255,251,235,1),rgba(255,247,237,0.96),rgba(250,250,249,0.94))] text-amber-950",
                )}
              >
                <div className="min-w-0 flex-1 truncate text-xs font-semibold">
                  <AnimatedShinyText
                    className={classNames(
                      isDark
                        ? "bg-[linear-gradient(110deg,rgba(254,243,199,0.78)_18%,rgba(255,255,255,0.98)_48%,rgba(251,191,36,0.92)_68%,rgba(254,243,199,0.78)_84%)]"
                        : "bg-[linear-gradient(110deg,rgb(120,53,15)_18%,rgb(245,158,11)_44%,rgb(255,255,255)_52%,rgb(180,83,9)_66%,rgb(120,53,15)_84%)]",
                    )}
                  >
                    {promptDraftTitle}
                  </AnimatedShinyText>
                </div>
              </div>
            ) : null}

            <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-auto scrollbar-hide px-4 py-4 sm:px-5 sm:py-5 lg:grid-cols-[15rem_minmax(0,1fr)_18rem] lg:overflow-hidden">
            <aside
              className={classNames(
                "flex min-h-0 flex-col rounded-[26px] border",
                isDark ? "border-white/10 bg-white/[0.035]" : "border-black/10 bg-[rgb(250,250,250)]",
              )}
            >
              <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--glass-border-subtle)] px-3.5 py-3">
                <div className="min-w-0">
                  <div className={classNames("text-sm font-semibold", isDark ? "text-slate-100" : "text-gray-900")}>
                    {t("voiceSecretaryDocumentsTitle", { defaultValue: "Working documents" })}
                  </div>
                  <div className="mt-0.5 text-[10px] leading-4 text-[var(--color-text-muted)]">
                    {documentsCountLabel}
                    {documents.length ? (
                      <span>
                        {" · "}
                        {t("voiceSecretaryDefaultDocumentLegend", {
                          defaultValue: "default gets new transcript",
                        })}
                      </span>
                    ) : null}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={startCreateDocument}
                  disabled={!!actionBusy}
                  className={classNames(
                    "rounded-full border px-3 py-1.5 text-[11px] font-semibold whitespace-nowrap transition-colors disabled:opacity-60",
                    isDark ? "border-white/10 text-slate-300 hover:bg-white/10" : "border-black/10 bg-white text-gray-700 hover:bg-black/5",
                  )}
                >
                  {actionBusy === "new_doc"
                    ? t("voiceSecretaryCreatingDocument", { defaultValue: "Creating..." })
                    : t("voiceSecretaryNewDocumentShort", { defaultValue: "New" })}
                </button>
              </div>
              <div className="min-h-0 flex-1 space-y-1.5 overflow-auto scrollbar-hide p-2.5">
                {creatingDocument ? (
                  <div
                    className={classNames(
                      "mb-2 space-y-2 rounded-2xl border p-2.5",
                      isDark ? "border-white/10 bg-white/[0.04]" : "border-black/10 bg-white",
                    )}
                  >
                    <input
                      value={newDocumentTitleDraft}
                      autoFocus
                      onChange={(event) => setNewDocumentTitleDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void createDocument();
                        }
                        if (event.key === "Escape") {
                          event.preventDefault();
                          cancelCreateDocument();
                        }
                      }}
                      placeholder={t("voiceSecretaryNewDocumentNamePlaceholder", {
                        defaultValue: "Document name",
                      })}
                      className={classNames(
                        "w-full rounded-lg border px-2.5 py-1.5 text-xs outline-none transition-colors",
                        isDark
                          ? "border-white/10 bg-black/20 text-slate-100 placeholder:text-slate-500 focus:border-white/30"
                          : "border-black/10 bg-white text-gray-900 placeholder:text-gray-400 focus:border-black/25",
                      )}
                    />
                    <div className="flex items-center justify-end gap-1.5">
                      <button
                        type="button"
                        onClick={cancelCreateDocument}
                        disabled={actionBusy === "new_doc"}
                        className={classNames(
                          "rounded-full px-2 py-1 text-[11px] font-medium transition-colors disabled:opacity-60",
                          isDark ? "text-slate-400 hover:bg-white/8" : "text-gray-500 hover:bg-black/5",
                        )}
                      >
                        {t("cancel", { defaultValue: "Cancel" })}
                      </button>
                      <button
                        type="button"
                        onClick={() => void createDocument()}
                        disabled={actionBusy === "new_doc"}
                        className={classNames(
                          "rounded-full px-2 py-1 text-[11px] font-semibold transition-colors disabled:opacity-60",
                          isDark ? "bg-white text-[rgb(20,20,22)] hover:bg-white/90" : "bg-[rgb(35,36,37)] text-white hover:bg-black",
                        )}
                      >
                        {actionBusy === "new_doc"
                          ? t("voiceSecretaryCreatingDocument", { defaultValue: "Creating..." })
                          : t("voiceSecretaryCreateDocument", { defaultValue: "Create" })}
                      </button>
                    </div>
                  </div>
                ) : null}
                {documents.length ? documents.map((document) => {
                  const docId = voiceDocumentKey(document);
                  const viewing = docId && docId === String(voiceDocumentKey(activeDocument) || activeDocumentId || "").trim();
                  const captureTarget = docId && docId === String(captureTargetDocumentId || "").trim();
                  return (
                    <div
                      key={docId || document.title}
                      role="button"
                      tabIndex={0}
                      onClick={() => void selectDocument(document)}
                      onKeyDown={(event) => {
                        if (event.key !== "Enter" && event.key !== " ") return;
                        event.preventDefault();
                        void selectDocument(document);
                      }}
                      className={classNames(
                        "group flex w-full min-w-0 flex-col gap-1.5 rounded-2xl border px-3 py-2.5 text-left transition-colors",
                        viewing
                          ? isDark
                            ? "border-white/14 bg-white/[0.08] text-white shadow-[0_10px_30px_-24px_rgba(255,255,255,0.32)]"
                            : "border-black/12 bg-white text-[rgb(35,36,37)] shadow-[0_10px_30px_-24px_rgba(15,23,42,0.14)]"
                          : isDark
                            ? "border-transparent text-slate-300 hover:border-white/10 hover:bg-white/8"
                            : "border-transparent text-gray-700 hover:border-black/10 hover:bg-white",
                      )}
                    >
                      <span className="flex min-w-0 items-center justify-between gap-2">
                        <span className="truncate text-sm font-semibold">{document.title || docId}</span>
                        <span className="flex shrink-0 items-center gap-1">
                          <button
                            type="button"
                            aria-label={t("voiceSecretaryArchiveDocumentItemAriaLabel", {
                              title: document.title || docId,
                              defaultValue: "Archive {{title}}",
                            })}
                            title={t("voiceSecretaryArchiveDocument", { defaultValue: "Archive viewed" })}
                            disabled={!docId || actionBusy === "archive_doc"}
                            onClick={(event) => {
                              event.stopPropagation();
                              void archiveDocument(document);
                            }}
                            onKeyDown={(event) => {
                              event.stopPropagation();
                            }}
                            className={classNames(
                              "inline-flex h-6 items-center justify-center rounded-full border px-2 transition-all disabled:cursor-default",
                              viewing ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
                              isDark
                                ? "border-white/10 bg-white/[0.04] text-slate-400 hover:bg-white/10 hover:text-slate-100 disabled:opacity-35"
                                : "border-black/10 bg-white text-gray-500 hover:bg-black/5 hover:text-gray-900 disabled:opacity-35",
                            )}
                          >
                            <span className="text-[10px] font-semibold leading-none">
                              {t("voiceSecretaryArchiveShort", { defaultValue: "Archive" })}
                            </span>
                          </button>
                          <button
                            type="button"
                            aria-pressed={!!captureTarget}
                            aria-label={captureTarget
                              ? t("voiceSecretaryDefaultDocumentActiveAriaLabel", {
                                  title: document.title || docId,
                                  defaultValue: "{{title}} is the default document for new transcript",
                                })
                              : t("voiceSecretarySetDefaultDocumentAriaLabel", {
                                  title: document.title || docId,
                                  defaultValue: "Set {{title}} as the default document for new transcript",
                                })}
                            title={captureTarget
                              ? t("voiceSecretaryDefaultDocumentHint", {
                                  defaultValue: "New transcript is written here by default",
                                })
                              : t("voiceSecretarySetDefaultDocumentHint", {
                                  defaultValue: "Set as the default document for new transcript",
                                })}
                            disabled={!docId || !!captureTarget || actionBusy === "capture_target"}
                            onClick={(event) => {
                              event.stopPropagation();
                              void setCaptureTargetDocument(document);
                            }}
                            onKeyDown={(event) => {
                              event.stopPropagation();
                            }}
                            className={classNames(
                              "relative flex h-5 w-5 items-center justify-center rounded-full border transition-colors disabled:cursor-default",
                              captureTarget
                                ? isDark
                                  ? "border-white/70 bg-white/10 shadow-[0_0_0_3px_rgba(255,255,255,0.08)]"
                                  : "border-[rgb(35,36,37)] bg-white shadow-[0_0_0_3px_rgba(35,36,37,0.08)]"
                                : isDark
                                  ? "border-white/25 bg-white/[0.03] hover:border-white/50 hover:bg-white/[0.08] disabled:opacity-45"
                                  : "border-gray-300 bg-white hover:border-[rgb(35,36,37)]/35 hover:bg-[rgb(245,245,245)] disabled:opacity-45",
                            )}
                          >
                            {captureTarget ? (
                              <span
                                aria-hidden="true"
                                className={classNames(
                                  "h-2.5 w-2.5 rounded-full",
                                  isDark ? "bg-white" : "bg-[rgb(35,36,37)]",
                                )}
                              />
                            ) : null}
                          </button>
                        </span>
                      </span>
                      {document.workspace_path ? (
                        <span className="truncate text-[11px] text-[var(--color-text-muted)]">{document.workspace_path}</span>
                      ) : null}
                    </div>
                  );
                }) : (
                  <div className="flex h-full items-center justify-center px-3 text-center text-xs text-[var(--color-text-muted)]">
                    {t("voiceSecretaryNoDocumentsHint", { defaultValue: "Start recording or create a document." })}
                  </div>
                )}
              </div>
            </aside>

            <section
              className={classNames(
                "flex min-h-0 flex-col rounded-[28px] border p-4",
                isDark ? "border-white/10 bg-white/[0.04]" : "border-black/10 bg-white",
              )}
            >
              <div className="flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-[var(--glass-border-subtle)] pb-4">
                <div className="min-w-0 flex-1">
                  <div className={classNames("break-words text-xl font-semibold tracking-[-0.02em]", isDark ? "text-slate-100" : "text-gray-900")}>
                    {documentDisplayTitle}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", isDark ? "bg-white/10 text-slate-100" : "bg-[rgb(245,245,245)] text-[rgb(35,36,37)]")}>
                      {t("voiceSecretaryMarkdownBadge", { defaultValue: "Markdown" })}
                    </span>
                    <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", activeDocumentPath ? (isDark ? "bg-white/10 text-slate-200" : "bg-[rgb(245,245,245)] text-[rgb(35,36,37)]") : (isDark ? "bg-slate-800 text-slate-300" : "bg-gray-100 text-gray-600"))}>
                      {activeDocumentPath
                        ? t("voiceSecretaryRepoBackedBadge", { defaultValue: "Repo-backed" })
                        : t("voiceSecretaryWaitingTranscriptBadge", { defaultValue: "Waiting for transcript" })}
                    </span>
                    {voiceDocumentKey(activeDocument) && voiceDocumentKey(activeDocument) === captureTargetDocumentId ? (
                      <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", isDark ? "bg-white/10 text-slate-200" : "bg-[rgb(245,245,245)] text-[rgb(35,36,37)]")}>
                        {t("voiceSecretaryDefaultDocumentBadge", { defaultValue: "Default document" })}
                      </span>
                    ) : null}
                    {documentHasUnsavedEdits ? (
                      <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", isDark ? "bg-amber-500/10 text-amber-200" : "bg-amber-50 text-amber-700")}>
                        {t("voiceSecretaryUnsavedEditsBadge", { defaultValue: "Unsaved edits" })}
                      </span>
                    ) : null}
                    {documentRemoteChanged ? (
                      <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", isDark ? "bg-white/10 text-slate-200" : "bg-[rgb(245,245,245)] text-[rgb(35,36,37)]")}>
                        {t("voiceSecretaryRemoteChangedBadge", { defaultValue: "Remote update available" })}
                      </span>
                    ) : null}
                  </div>
                  <div
                    className={classNames(
                      "mt-3 rounded-2xl border px-3 py-2.5 text-[11px] leading-5",
                      isDark ? "border-white/8 bg-black/20 text-[var(--color-text-muted)]" : "border-black/8 bg-[rgb(248,248,248)] text-[rgb(108,114,127)]",
                    )}
                  >
                    <span className="block break-all [overflow-wrap:anywhere]">
                      {activeDocumentPath
                        ? t("voiceSecretaryWorkingDocumentPath", {
                            path: activeDocumentPath,
                            defaultValue: "Repo markdown: {{path}}",
                          })
                        : t("voiceSecretaryWorkingDocumentPending", {
                            defaultValue: "Transcript will create a repo markdown document automatically.",
                          })}
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                  {documentRemoteChanged ? (
                    <button
                      type="button"
                      className={classNames(
                        "rounded-full border px-2.5 py-1.5 text-[11px] font-semibold transition-colors disabled:opacity-60",
                        isDark ? "border-white/10 text-slate-300 hover:bg-white/10" : "border-black/10 text-gray-700 hover:bg-black/5",
                      )}
                      onClick={() => loadDocumentDraft(activeDocument)}
                      disabled={!activeDocument}
                      title={t("voiceSecretaryLoadLatestDocumentHint", {
                        defaultValue: "Load the latest document from the daemon. Unsaved local edits in this panel will be replaced.",
                      })}
                    >
                      {t("voiceSecretaryLoadLatestDocument", { defaultValue: "Load latest" })}
                    </button>
                  ) : null}
                  {documentEditing || documentHasUnsavedEdits ? (
                    <button
                      type="button"
                      className={classNames(
                        "rounded-full border px-2.5 py-1.5 text-[11px] font-semibold transition-colors disabled:opacity-60",
                        isDark ? "border-white/10 text-slate-300 hover:bg-white/10" : "border-black/10 text-gray-700 hover:bg-black/5",
                      )}
                      onClick={() => void saveDocument()}
                      disabled={!!actionBusy}
                    >
                      {actionBusy === "save_doc"
                        ? t("voiceSecretarySavingDocument", { defaultValue: "Saving..." })
                        : t("voiceSecretarySaveDocument", { defaultValue: "Save edits" })}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={downloadCurrentDocument}
                    disabled={!activeDocument}
                    className={classNames(
                      "rounded-full border px-2.5 py-1.5 text-[11px] font-semibold transition-colors disabled:opacity-50",
                      isDark ? "border-white/10 text-slate-300 hover:bg-white/10" : "border-black/10 text-gray-700 hover:bg-black/5",
                    )}
                  >
                    {t("voiceSecretaryDownloadDocument", { defaultValue: "Download .md" })}
                  </button>
                  <button
                    type="button"
                    onClick={() => setDocumentEditing((value) => !value)}
                    className={classNames(
                      "rounded-full border px-2.5 py-1.5 text-[11px] font-semibold transition-colors",
                      isDark ? "border-white/10 text-slate-300 hover:bg-white/10" : "border-black/10 text-gray-700 hover:bg-black/5",
                    )}
                  >
                    {documentEditing
                      ? t("voiceSecretaryPreviewDocument", { defaultValue: "Preview" })
                      : t("voiceSecretaryEditDocument", { defaultValue: "Edit" })}
                  </button>
                </div>
              </div>

              <MarkdownDocumentSurface
                className="mt-4 min-h-0 flex-1 overflow-auto scrollbar-hide"
                content={documentDraft}
                editValue={documentDraft}
                editing={documentEditing}
                editAriaLabel={t("voiceSecretaryDocumentEditAriaLabel", { defaultValue: "Edit Voice Secretary working document markdown" })}
                editPlaceholder={t("voiceSecretaryDocumentPlaceholder", {
                  defaultValue: "Voice Secretary will maintain a markdown working document here as transcript arrives. You can edit it directly.",
                })}
                emptyLabel={t("voiceSecretaryDocumentPreviewEmpty", {
                  defaultValue: "Transcript and Voice Secretary edits will appear here.",
                })}
                isDark={isDark}
                minHeightClassName="min-h-[280px] lg:min-h-0"
                onEditValueChange={updateDocumentDraft}
              />
            </section>

            <aside
              className={classNames(
                "flex min-h-0 flex-col gap-4 rounded-[26px] border p-3.5",
                isDark ? "border-white/10 bg-white/[0.035]" : "border-black/10 bg-[rgb(250,250,250)]",
              )}
            >
              <div
                className={classNames(
                  "shrink-0 rounded-2xl border p-3",
                  isDark ? "border-white/10 bg-white/[0.04]" : "border-black/10 bg-white",
                )}
              >
                <div className={classNames("text-sm font-semibold", isDark ? "text-slate-100" : "text-gray-900")}>
                  {t("voiceSecretaryInstructionLabel", { defaultValue: "Ask Voice Secretary" })}
                </div>
                <div className="mt-1 text-[11px] leading-5 text-[var(--color-text-muted)]">
                  {t("voiceSecretaryInstructionPlaceholder", {
                    defaultValue: "Tell Voice Secretary how to refine, split, summarize, or send this document.",
                  })}
                </div>
                <textarea
                  value={documentInstruction}
                  onChange={(event) => setDocumentInstruction(event.target.value)}
                  placeholder={t("voiceSecretaryInstructionPlaceholder", {
                    defaultValue: "Tell Voice Secretary how to refine, split, summarize, or send this document.",
                  })}
                  className={classNames(
                    "mt-3 min-h-[96px] w-full resize-y rounded-2xl border px-3 py-2 text-xs leading-5 outline-none transition-colors",
                    isDark
                      ? "border-white/10 bg-white/[0.06] text-slate-100 placeholder:text-slate-500 focus:border-white/30"
                      : "border-black/10 bg-white text-gray-900 placeholder:text-gray-400 focus:border-black/25",
                  )}
                />
                <button
                  type="button"
                  className={classNames(
                    "mt-3 w-full rounded-2xl border px-3 py-2.5 text-xs font-semibold transition-colors disabled:opacity-60",
                    isDark
                      ? "border-white bg-white text-[rgb(20,20,22)] hover:bg-white/90"
                      : "border-[rgb(35,36,37)] bg-[rgb(35,36,37)] text-white hover:bg-black",
                  )}
                  onClick={() => void sendDocumentInstruction()}
                  disabled={!!actionBusy || !documentInstruction.trim()}
                >
                  {actionBusy === "instruct_doc"
                    ? t("voiceSecretaryApplyingInstruction", { defaultValue: "Sending..." })
                    : t("voiceSecretaryApplyInstruction", { defaultValue: "Send request" })}
                </button>
              </div>

              <div className={classNames("flex min-h-0 flex-1 flex-col rounded-2xl border text-xs leading-5", isDark ? "border-white/10 bg-black/10 text-slate-300" : "border-black/10 bg-white text-gray-700")}>
                <div className="shrink-0 border-b border-[var(--glass-border-subtle)] px-3 py-3">
                  <div className={classNames("text-sm font-semibold", isDark ? "text-slate-100" : "text-gray-900")}>
                    {t("voiceSecretaryTranscriptFeedTitle", { defaultValue: "Transcript" })}
                  </div>
                  <div className="mt-1 text-[11px] leading-5 text-[var(--color-text-muted)]">
                    {transcriptFeedHint}
                  </div>
                </div>
                <div className="min-h-0 flex-1 space-y-2 overflow-y-auto scrollbar-hide px-2.5 py-2.5">
                  {liveTranscriptText ? (
                    <div className={classNames("rounded-2xl border px-2.5 py-2", isDark ? "border-white/15 bg-white/[0.08] text-white" : "border-black/10 bg-[rgb(245,245,245)] text-[rgb(35,36,37)]")}>
                      <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide opacity-70">
                        {t("voiceSecretaryTranscriptLive", { defaultValue: "Live" })}
                      </div>
                      <div className="whitespace-pre-wrap break-words text-[11px] leading-4">{liveTranscriptText}</div>
                    </div>
                  ) : null}
                  {recentTranscriptItems.length ? recentTranscriptItems.map((item) => (
                    <div key={item.id} className={classNames("rounded-2xl border px-2.5 py-2", isDark ? "border-white/10 bg-white/[0.025] text-slate-300" : "border-gray-200 bg-gray-50 text-gray-700")}>
                      <div className="whitespace-pre-wrap break-words text-[11px] leading-4">{item.text}</div>
                    </div>
                  )) : !liveTranscriptText ? (
                    <div className="rounded-2xl border border-dashed border-[var(--glass-border-subtle)] px-2.5 py-4 text-center text-[11px] text-[var(--color-text-muted)]">
                      {recording
                        ? t("voiceSecretaryTranscriptFeedListeningEmpty", { defaultValue: "Listening. Transcript will appear here." })
                        : t("voiceSecretaryTranscriptFeedEmpty", { defaultValue: "Start recording to see transcript here." })}
                    </div>
                  ) : null}
                </div>
              </div>
            </aside>
          </div>
          <button
            type="button"
            onClick={closePanel}
            className="absolute right-3 top-3 rounded-md p-1 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text-primary)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)]/45 sm:right-4 sm:top-4"
            aria-label={t("voiceSecretaryClose", { defaultValue: "Close Voice Secretary" })}
          >
            <span aria-hidden="true">×</span>
          </button>
            </section>
          </div>,
          document.body,
        )
        : null}

      {isAssistantRow ? (
        <div className="inline-flex max-w-full items-center gap-1.5">
          <div
            className={classNames(
              "inline-flex h-9 shrink-0 items-center gap-0.5 rounded-lg border p-0.5 transition-colors",
              recording
                ? isDark
                  ? "border-rose-400/30 bg-rose-500/10"
                  : "border-rose-200 bg-rose-50/70"
                : "border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)]",
            )}
            role="group"
            aria-label={t("voiceSecretaryTitle", { defaultValue: "Voice Secretary" })}
          >
            <button
              type="button"
              className={classNames(
                "relative inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                recording
                  ? isDark
                    ? "bg-rose-500/20 text-rose-200 hover:bg-rose-500/28"
                    : "bg-rose-500 text-white shadow-sm hover:bg-rose-600"
                  : !dictationSupported
                    ? "text-[var(--color-text-tertiary)]"
                    : isDark
                      ? "text-[var(--color-text-secondary)] hover:bg-white/10 hover:text-[var(--color-text-primary)]"
                      : "text-[var(--color-text-secondary)] hover:bg-black/5 hover:text-gray-900",
                !controlDisabled && !actionBusy && "active:scale-[0.96]",
              )}
              onClick={(event) => {
                if (captureMode === "prompt") {
                  event.preventDefault();
                  return;
                }
                void handleAssistantRowRecordClick(event);
              }}
              onPointerDown={captureMode === "prompt" ? handleAssistantRowPromptPointerDown : undefined}
              onPointerUp={captureMode === "prompt" ? handleAssistantRowPromptPointerUp : undefined}
              onPointerCancel={captureMode === "prompt" ? handleAssistantRowPromptPointerCancel : undefined}
              disabled={!!actionBusy || controlDisabled || (!recording && !dictationSupported)}
              aria-pressed={recording}
              aria-label={assistantRowControlLabel}
              title={`${assistantRowControlLabel} · ${assistantRowCurrentMode.label}`}
            >
              {recording ? (
                <StopIcon size={13} aria-hidden="true" />
              ) : (
                <MicrophoneIcon size={15} aria-hidden="true" />
              )}
            </button>
            {onCaptureModeChange ? (
              <Popover open={showAssistantModeMenu} onOpenChange={setShowAssistantModeMenu}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className={classNames(
                      "inline-flex h-8 shrink-0 items-center justify-center gap-1 rounded-md px-2 text-[11px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                      isDark
                        ? "text-[var(--color-text-secondary)] hover:bg-white/10 hover:text-[var(--color-text-primary)]"
                        : "text-[var(--color-text-secondary)] hover:bg-black/5 hover:text-gray-900",
                    )}
                    disabled={controlDisabled}
                    title={`${t("voiceSecretaryModeSelector", { defaultValue: "Voice Secretary capture mode" })}: ${assistantRowCurrentMode.label}`}
                    aria-label={t("voiceSecretaryModeSelector", { defaultValue: "Voice Secretary capture mode" })}
                  >
                    <span className="min-w-0 truncate">{assistantRowCurrentMode.label}</span>
                    <ChevronDownIcon size={12} aria-hidden="true" />
                  </button>
                </PopoverTrigger>
                <PopoverContent
                  align="start"
                  sideOffset={6}
                  className="w-56 rounded-2xl p-1.5"
                >
                  <div
                    role="menu"
                    aria-label={t("voiceSecretaryModeSelector", { defaultValue: "Voice Secretary capture mode" })}
                  >
                    {assistantRowModeOptions.map((option) => {
                      const active = option.key === captureMode;
                      return (
                        <button
                          key={option.key}
                          type="button"
                          className={classNames(
                            "w-full rounded-xl px-3 py-2.5 text-left flex items-center gap-2.5 transition-colors",
                            active
                              ? isDark
                                ? "bg-white/10"
                                : "bg-black/5"
                              : isDark
                                ? "hover:bg-white/5"
                                : "hover:bg-black/5",
                          )}
                          role="menuitemradio"
                          aria-checked={active}
                          onPointerDown={(event) => {
                            event.preventDefault();
                            handleAssistantRowModeChange(option.key);
                          }}
                        >
                          <span
                            className={classNames(
                              "w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0",
                              option.key === "document"
                                ? isDark
                                  ? "bg-slate-700 text-slate-200"
                                  : "bg-gray-100 text-gray-700"
                                : isDark
                                  ? "bg-indigo-500/25 text-indigo-200"
                                  : "bg-indigo-100 text-indigo-700",
                            )}
                          >
                            {option.key === "document" ? (
                              <MicrophoneIcon size={13} />
                            ) : (
                              <span className="text-[11px] font-black italic leading-none">P</span>
                            )}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className={classNames("block text-sm font-semibold", isDark ? "text-slate-100" : "text-gray-900")}>
                              {option.label}
                            </span>
                            <span className={classNames("block text-[11px]", isDark ? "text-[var(--color-text-tertiary)]" : "text-gray-500")}>
                              {option.description}
                            </span>
                          </span>
                          {active ? (
                            <span className={classNames("text-xs font-semibold", isDark ? "text-slate-200" : "text-[rgb(35,36,37)]")}>✓</span>
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                </PopoverContent>
              </Popover>
            ) : null}
            <button
              type="button"
              className={classNames(
                "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                open
                  ? isDark
                    ? "bg-white/10 text-[var(--color-text-primary)]"
                    : "bg-black/5 text-gray-900"
                  : isDark
                    ? "text-[var(--color-text-secondary)] hover:bg-white/10 hover:text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)] hover:bg-black/5 hover:text-gray-900",
                !controlDisabled && "active:scale-[0.96]",
              )}
              onClick={() => {
                if (open) closePanel();
                else setOpen(true);
              }}
              disabled={controlDisabled}
              aria-pressed={open}
              aria-label={openButtonLabel}
              title={openButtonLabel}
            >
              <MaximizeIcon size={15} />
            </button>
          </div>
          {pendingPromptDraft || promptDraftWaiting ? (
            <div
              className={classNames(
                "inline-flex min-w-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px]",
                pendingPromptDraft
                  ? isDark
                    ? "bg-emerald-400/12 text-emerald-100"
                    : "bg-emerald-50 text-emerald-900"
                  : isDark
                    ? "bg-amber-400/12 text-amber-100"
                    : "bg-amber-50 text-amber-900",
              )}
            >
              <div className="truncate font-semibold">
                {promptDraftWaiting ? (
                  <AnimatedShinyText
                    className={classNames(
                      isDark
                        ? "bg-[linear-gradient(110deg,rgba(254,243,199,0.82)_18%,rgba(255,255,255,0.98)_48%,rgba(251,191,36,0.94)_68%,rgba(254,243,199,0.82)_84%)]"
                        : "bg-[linear-gradient(110deg,rgb(120,53,15)_18%,rgb(217,119,6)_42%,rgb(255,255,255)_52%,rgb(146,64,14)_66%,rgb(120,53,15)_84%)]",
                    )}
                  >
                    {promptDraftTitle}
                  </AnimatedShinyText>
                ) : (
                  promptDraftTitle
                )}
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <button
          type="button"
          className={classNames(
            buttonClassName || classNames(
              "glass-btn flex items-center justify-center rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-60",
              controlDisabled
                ? "text-[var(--color-text-tertiary)]"
                : recording
                  ? isDark
                    ? "border-rose-400/25 bg-rose-500/15 text-rose-100 hover:bg-rose-500/22"
                    : "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100"
                  : isDark
                    ? "text-[var(--color-text-secondary)] hover:bg-white/10 hover:text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)] hover:bg-black/5 hover:text-gray-800",
            ),
            "relative",
            recording && buttonClassName
              ? isDark
                ? "!text-rose-300"
                : "!text-rose-600"
              : "",
          )}
          style={buttonClassName ? undefined : { width: `${buttonSizePx}px`, height: `${buttonSizePx}px` }}
          onClick={() => {
            if (open) closePanel();
            else setOpen(true);
          }}
          disabled={controlDisabled}
          aria-pressed={open}
          aria-label={openButtonLabel}
          title={openButtonLabel}
        >
          <MicrophoneIcon size={openButtonIconSizePx} className="transition-transform" />
          {recording ? (
            <span
              aria-hidden="true"
              className={classNames(
                "absolute right-1.5 top-1.5 h-2 w-2 rounded-full",
                "bg-rose-500 shadow-[0_0_0_3px_rgba(244,63,94,0.16)]",
              )}
            />
          ) : null}
        </button>
      )}
    </div>
  );
}
