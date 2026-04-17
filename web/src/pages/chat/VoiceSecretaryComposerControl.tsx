import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  AssistantVoiceDocument,
  AssistantVoiceTranscriptSegmentResult,
  BuiltinAssistant,
} from "../../types";
import { classNames } from "../../utils/classNames";
import { MicrophoneIcon, StopIcon } from "../../components/Icons";
import { MarkdownDocumentSurface } from "../../components/document/MarkdownDocumentSurface";
import {
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

type VoiceSecretaryComposerControlProps = {
  isDark: boolean;
  selectedGroupId: string;
  busy: string;
  buttonClassName: string;
  buttonSizePx: number;
  disabled?: boolean;
};

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
const BROWSER_SPEECH_INITIAL_CAPTURE_GRACE_MS = 2500;
const BROWSER_SPEECH_RESTART_BASE_MS = 500;
const BROWSER_SPEECH_RESTART_MAX_MS = 8000;
const BROWSER_SPEECH_RECOVERABLE_ERRORS = new Set(["no-speech", "aborted", "network", "audio-capture"]);
const BROWSER_SPEECH_FATAL_ERRORS = new Set(["not-allowed", "service-not-allowed"]);
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

function mergeTranscriptChunks(previous: string, nextText: string): string {
  const prev = String(previous || "").trim();
  const next = String(nextText || "").trim();
  if (!prev) return next;
  if (!next) return prev;
  const cjkBoundary = /[\u3040-\u30ff\u3400-\u9fff]$/.test(prev) && /^[\u3040-\u30ff\u3400-\u9fff]/.test(next);
  return cjkBoundary ? `${prev}${next}` : `${prev} ${next}`;
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
  buttonClassName,
  buttonSizePx,
  disabled,
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
  const browserSpeechCycleCountRef = useRef(0);
  const browserSpeechCycleStartedAtRef = useRef(0);
  const activeDocumentIdRef = useRef("");
  const captureTargetDocumentIdRef = useRef("");
  const documentBaseTitleRef = useRef("");
  const documentBaseContentRef = useRef("");
  const documentTitleDraftRef = useRef("");
  const documentDraftRef = useRef("");
  const archivedDocumentIdsRef = useRef<Set<string>>(new Set());
  const [open, setOpen] = useState(false);
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
        defaultValue: "Browser speech recognition is not available here. Switch to Assistant service local ASR or use a supported browser.",
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
        defaultValue: "This browser page cannot access the microphone API. Use Edge/Chrome on localhost or HTTPS and allow microphone access.",
      });
    }
    if (issue === "media_recorder") {
      return t("voiceSecretaryMediaRecorderUnavailable", {
        defaultValue: "This browser does not support MediaRecorder audio capture. Use Browser ASR or a browser with MediaRecorder support.",
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
      const resp = await fetchAssistant(gid, "voice_secretary");
      if (seq !== refreshSeq.current) return;
      if (!resp.ok) {
        if (!quiet) showError(resp.error.message);
        return;
      }
      setAssistant(resp.result.assistant || null);
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
    browserSpeechCycleCountRef.current = 0;
    browserSpeechCycleStartedAtRef.current = 0;
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
    await appendTranscriptSegment(text, {
      flush: true,
      triggerKind,
      source: "browser_asr",
      inputDeviceLabel: BROWSER_DEFAULT_MIC_LABEL,
      documentPath,
    });
  }, [
    appendTranscriptSegment,
    clearTranscriptFlushTimer,
    clearTranscriptMaxFlushTimer,
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
    const clean = String(text || "").replace(/\s+/g, " ").trim();
    if (!clean) return;
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
    browserSpeechStopRequestedRef.current = true;
    browserSpeechTransientErrorCountRef.current = 0;
    browserSpeechCycleCountRef.current = 0;
    browserSpeechCycleStartedAtRef.current = 0;
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
    if (mediaRecorderRef.current) {
      stopServiceAudio();
      return;
    }
    stopBrowserSpeech();
  }, [stopBrowserSpeech, stopServiceAudio]);

  const closePanel = useCallback(() => {
    setOpen(false);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event: MouseEvent) => {
      const root = rootRef.current;
      if (!root || !event.target) return;
      if (root.contains(event.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [open]);

  useEffect(() => () => {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    abortBrowserSpeechRecognition(recognition);
    browserSpeechStopRequestedRef.current = true;
    browserSpeechTransientErrorCountRef.current = 0;
    browserSpeechCycleCountRef.current = 0;
    browserSpeechCycleStartedAtRef.current = 0;
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
        defaultValue: "Browser speech recognition is not available here. Switch to Assistant service local ASR or use a supported browser.",
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
    browserSpeechCycleCountRef.current = 0;
    browserSpeechCycleStartedAtRef.current = 0;

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

    mediaStreamRef.current = stream;
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

    const handleBrowserMicEnded = () => {
      if (browserSpeechStopRequestedRef.current) return;
      const message = t("voiceSecretaryMicDisconnected", {
        defaultValue: "Microphone capture ended. Check the input device, then start recording again.",
      });
      stopAfterFatalSpeechFailure(recognitionRef.current, message);
    };
    const audioTracks = stream.getAudioTracks();
    audioTracks.forEach((track) => {
      track.addEventListener("ended", handleBrowserMicEnded);
    });
    browserSpeechMediaCleanupRef.current = () => {
      audioTracks.forEach((track) => {
        track.removeEventListener("ended", handleBrowserMicEnded);
      });
    };

    function startRecognitionCycle(delayMs = 0): void {
      const runCycle = () => {
        browserSpeechRestartTimerRef.current = null;
        const captureAlive = mediaStreamHasLiveAudio(mediaStreamRef.current);
        if (browserSpeechStopRequestedRef.current || !assistantEnabled || !browserSpeechReady || !captureAlive) {
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
        browserSpeechCycleCountRef.current += 1;
        browserSpeechCycleStartedAtRef.current = Date.now();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = effectiveRecognitionLanguage;
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
          const cycleElapsedMs = Date.now() - browserSpeechCycleStartedAtRef.current;
          const initialAudioCaptureFailure = code === "audio-capture"
            && browserSpeechCycleCountRef.current <= 1
            && !browserSpeechReceivedFinalRef.current
            && cycleElapsedMs <= BROWSER_SPEECH_INITIAL_CAPTURE_GRACE_MS
            && !mediaStreamHasLiveAudio(mediaStreamRef.current);
          const fatal = BROWSER_SPEECH_FATAL_ERRORS.has(code) || initialAudioCaptureFailure;
          const recoverable = !fatal && (BROWSER_SPEECH_RECOVERABLE_ERRORS.has(code) || !code);
          if (recoverable) {
            browserSpeechHadErrorRef.current = true;
            browserSpeechTransientErrorCountRef.current += code === "no-speech" ? 0 : 1;
            setInterimTranscript("");
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
          const captureAlive = mediaStreamHasLiveAudio(mediaStreamRef.current);
          const shouldRestart = !stoppedByUser && captureAlive && assistantEnabled && browserSpeechReady;
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
          browserSpeechTransientErrorCountRef.current = 0;
          setSpeechError("");
        } catch {
          if (recognitionRef.current === recognition) recognitionRef.current = null;
          browserSpeechHadErrorRef.current = true;
          browserSpeechTransientErrorCountRef.current += 1;
          setSpeechError(t("voiceSecretarySpeechRecovering", {
            code: "start-failed",
            defaultValue: "Browser speech recognition is reconnecting after a temporary {{code}} event. Recording is still on.",
          }));
          if (mediaStreamHasLiveAudio(mediaStreamRef.current) && !browserSpeechStopRequestedRef.current) {
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
        await appendTranscriptSegment(text, {
          flush: true,
          source: "assistant_service_local_asr",
          triggerKind: "service_transcript",
          inputDeviceLabel: selectedAudioDeviceLabel,
        });
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
    effectiveRecognitionLanguage,
    refreshAssistant,
    selectedAudioDeviceLabel,
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
    selectedAudioDeviceId,
    selectedGroupId,
    serviceAsrReady,
    showError,
    t,
    transcribeServiceAudio,
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

  const archiveDocument = useCallback(async () => {
    const gid = String(selectedGroupId || "").trim();
    const docId = activeDocumentKey || activeDocumentId;
    if (!gid || !docId) return;
    setActionBusy("archive_doc");
    try {
      const resp = await archiveVoiceAssistantDocument(gid, docId, { by: "user" });
      if (!resp.ok) {
        showError(resp.error.message);
        return;
      }
      archivedDocumentIdsRef.current.add(docId);
      setDocuments((prev) => prev.filter((item) => voiceDocumentKey(item) !== docId));
      setActiveDocumentId("");
      if (captureTargetDocumentIdRef.current === docId) {
        captureTargetDocumentIdRef.current = "";
        setCaptureTargetDocumentId("");
      }
      loadDocumentDraft(null);
      showNotice({ message: t("voiceSecretaryDocumentArchived", { defaultValue: "Voice Secretary working document archived." }) });
      await refreshAssistant({ quiet: true });
    } catch {
      showError(t("voiceSecretaryDocumentArchiveFailed", { defaultValue: "Failed to archive the Voice Secretary document." }));
    } finally {
      setActionBusy("");
    }
  }, [activeDocumentKey, activeDocumentId, loadDocumentDraft, refreshAssistant, selectedGroupId, showError, showNotice, t]);

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
  const openButtonLabel = recording
    ? t("voiceSecretaryOpenRecordingWorkspace", { defaultValue: "Open Voice Secretary workspace - recording" })
    : t("voiceSecretaryOpenWorkspace", { defaultValue: "Open Voice Secretary workspace" });
  const openButtonIconSizePx = Math.max(20, Math.min(26, Math.round(buttonSizePx - 14)));

  return (
    <div ref={rootRef} className="relative self-end">
      {open ? (
        <div
          className={classNames(
            "glass-panel absolute bottom-full right-0 z-[160] mb-2 flex h-[min(52rem,calc(100vh-5rem))] w-[min(78rem,calc(100vw-1rem))] flex-col overflow-hidden rounded-3xl border p-3 shadow-2xl",
            isDark ? "border-white/10 bg-slate-950/95" : "border-black/10 bg-white/95",
          )}
        >
          <div className="mb-3 flex shrink-0 items-start justify-between gap-4">
            <div className="min-w-0">
              <div className={classNames("text-base font-semibold tracking-[-0.01em]", isDark ? "text-slate-100" : "text-gray-900")}>
                {t("voiceSecretaryTitle", { defaultValue: "Voice Secretary" })}
              </div>
              <div className={classNames("mt-0.5 truncate text-xs", isDark ? "text-slate-400" : "text-gray-500")}>
                {t("voiceSecretaryWorkspaceHint", {
                  defaultValue: "Capture speech, maintain working documents, and ask the secretary to refine or send them.",
                })}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span
                className={classNames(
                  "rounded-full px-2.5 py-1 text-[11px] font-semibold",
                  assistantEnabled
                    ? isDark
                      ? "bg-emerald-500/15 text-emerald-200"
                      : "bg-emerald-50 text-emerald-700"
                    : isDark
                      ? "bg-white/10 text-slate-300"
                      : "bg-gray-100 text-gray-600",
                )}
              >
                {loading ? t("loadingContext", { defaultValue: "Loading context..." }) : statusLabel}
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={assistantEnabled}
                className={classNames(
                  "inline-flex items-center gap-2 rounded-full border px-2.5 py-1.5 text-[11px] font-semibold transition-colors disabled:opacity-60",
                  assistantEnabled
                    ? isDark
                      ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100 hover:bg-emerald-300/15"
                      : "border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100"
                    : isDark
                      ? "border-white/10 bg-white/[0.04] text-slate-300 hover:bg-white/10"
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
                    assistantEnabled ? "bg-emerald-500" : isDark ? "bg-white/15" : "bg-gray-300",
                  )}
                >
                  <span
                    className={classNames(
                      "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
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
            </div>
          </div>

          <div className="mb-3 shrink-0 border-b border-[var(--glass-border-subtle)] pb-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className={classNames("min-w-0 flex-1 truncate text-[11px]", isDark ? "text-slate-400" : "text-gray-500")}>
                {!assistantEnabled
                  ? t("voiceSecretaryDisabledHint", {
                      defaultValue: "Voice Secretary is off for this group. Enable the assistant here or in Settings > Assistants before recording.",
                    })
                  : speechError || null}
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                {assistantEnabled ? (
                  <>
                    <label className="flex items-center gap-1.5 text-[11px] font-semibold text-[var(--color-text-secondary)]">
                      <span>{t("voiceSecretaryLanguage", { defaultValue: "Language" })}</span>
                      <select
                        value={configuredRecognitionLanguage}
                        onChange={(event) => void updateRecognitionLanguage(event.target.value)}
                        className={classNames(
                          "rounded-lg border px-2 py-1.5 text-[11px] outline-none transition-colors",
                          isDark
                            ? "border-white/10 bg-white/[0.06] text-slate-100 focus:border-cyan-300/45"
                            : "border-black/10 bg-white text-gray-800 focus:border-cyan-400/45",
                        )}
                        disabled={recording || !!actionBusy}
                      >
                        {voiceLanguageOptions.map((optionValue) => (
                          <option key={optionValue} value={optionValue}>
                            {voiceLanguageLabel(optionValue)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {serviceAsrReady ? (
                      <>
                        <label className="flex min-w-[13rem] items-center gap-1.5 text-[11px] font-semibold text-[var(--color-text-secondary)]">
                          <span>{t("voiceSecretaryMicDevice", { defaultValue: "Microphone" })}</span>
                          <select
                            value={selectedAudioDeviceId}
                            onChange={(event) => setSelectedAudioDeviceId(event.target.value)}
                            className={classNames(
                              "min-w-0 flex-1 rounded-lg border px-2 py-1.5 text-[11px] outline-none transition-colors",
                              isDark
                                ? "border-white/10 bg-white/[0.06] text-slate-100 focus:border-cyan-300/45"
                                : "border-black/10 bg-white text-gray-800 focus:border-cyan-400/45",
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
                            "rounded-lg border px-2 py-1.5 text-[11px] font-semibold transition-colors disabled:opacity-60",
                            isDark ? "border-white/10 text-slate-300 hover:bg-white/10" : "border-black/10 text-gray-700 hover:bg-black/5",
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
                        "inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs font-semibold transition-colors disabled:opacity-60",
                        recording
                          ? isDark
                            ? "border-rose-300/35 bg-rose-500/15 text-rose-100 hover:bg-rose-500/22"
                            : "border-rose-200 bg-rose-50 text-rose-800 hover:bg-rose-100"
                          : isDark
                            ? "border-white/10 bg-white/[0.06] text-slate-100 hover:bg-white/10"
                            : "border-black/10 bg-white text-gray-800 hover:bg-black/5",
                      )}
                      onClick={() => recording ? stopCurrentRecording() : void startDictation()}
                      disabled={!!actionBusy || (!recording && !dictationSupported)}
                      title={recording
                        ? t("voiceSecretaryStopDictation", { defaultValue: "Stop recording" })
                        : t("voiceSecretaryStartDictation", { defaultValue: "Start recording" })}
                    >
                      <span
                        aria-hidden="true"
                        className={classNames(
                          "inline-flex h-6 w-6 items-center justify-center rounded-full",
                          recording
                            ? isDark ? "bg-rose-300/15" : "bg-white"
                            : isDark ? "bg-rose-400/15 text-rose-100" : "bg-rose-50 text-rose-700",
                        )}
                      >
                        {recording ? (
                          <StopIcon size={13} />
                        ) : (
                          <span className="h-2.5 w-2.5 rounded-full bg-rose-600" />
                        )}
                      </span>
                      {recording
                        ? t("voiceSecretaryStopDictation", { defaultValue: "Stop recording" })
                        : t("voiceSecretaryStartDictation", { defaultValue: "Start recording" })}
                    </button>
                  </>
                ) : null}
              </div>
            </div>
          </div>

          <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-auto lg:grid-cols-[15rem_minmax(0,1fr)_18rem] lg:overflow-hidden">
            <aside
              className={classNames(
                "flex min-h-0 flex-col rounded-2xl border",
                isDark ? "border-white/10 bg-white/[0.035]" : "border-black/10 bg-gray-50/80",
              )}
            >
              <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--glass-border-subtle)] px-3 py-2">
                <div>
                  <div className={classNames("text-xs font-semibold", isDark ? "text-slate-200" : "text-gray-800")}>
                    {t("voiceSecretaryDocumentsTitle", { defaultValue: "Working documents" })}
                  </div>
                  <div className="text-[10px] text-[var(--color-text-muted)]">
                    {t("voiceSecretaryDocumentsCount", { count: documents.length, defaultValue: "{{count}} docs" })}
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
                    "rounded-full border px-2 py-1 text-[11px] font-semibold transition-colors disabled:opacity-60",
                    isDark ? "border-white/10 text-slate-300 hover:bg-white/10" : "border-black/10 text-gray-700 hover:bg-black/5",
                  )}
                >
                  {actionBusy === "new_doc"
                    ? t("voiceSecretaryCreatingDocument", { defaultValue: "Creating..." })
                    : t("voiceSecretaryNewDocumentShort", { defaultValue: "New" })}
                </button>
              </div>
              <div className="min-h-0 flex-1 space-y-1 overflow-auto p-2">
                {creatingDocument ? (
                  <div
                    className={classNames(
                      "mb-2 space-y-2 rounded-xl border p-2",
                      isDark ? "border-cyan-300/20 bg-cyan-300/8" : "border-cyan-200 bg-cyan-50/70",
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
                          ? "border-white/10 bg-black/20 text-slate-100 placeholder:text-slate-500 focus:border-cyan-300/50"
                          : "border-black/10 bg-white text-gray-900 placeholder:text-gray-400 focus:border-cyan-400/60",
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
                          isDark ? "bg-cyan-300 text-slate-950 hover:bg-cyan-200" : "bg-cyan-600 text-white hover:bg-cyan-500",
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
                        "group flex w-full min-w-0 flex-col gap-1 rounded-xl border px-2.5 py-2 text-left transition-colors",
                        viewing
                          ? isDark
                            ? "border-cyan-300/20 bg-cyan-400/12 text-cyan-100"
                            : "border-cyan-200 bg-cyan-50 text-cyan-800"
                          : isDark
                            ? "border-transparent text-slate-300 hover:border-white/10 hover:bg-white/8"
                            : "border-transparent text-gray-700 hover:border-black/10 hover:bg-white",
                      )}
                    >
                      <span className="flex min-w-0 items-center justify-between gap-2">
                        <span className="truncate text-xs font-semibold">{document.title || docId}</span>
                        <span className="flex shrink-0 items-center gap-1">
                          {viewing ? (
                            <span className="rounded-full bg-current/10 px-1.5 py-0.5 text-[10px] font-medium">
                              {t("voiceSecretaryViewingDocumentBadge", { defaultValue: "Viewing" })}
                            </span>
                          ) : null}
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
                                  ? "border-cyan-200 bg-cyan-300/15 shadow-[0_0_0_3px_rgba(34,211,238,0.08)]"
                                  : "border-cyan-500 bg-white shadow-[0_0_0_3px_rgba(6,182,212,0.08)]"
                                : isDark
                                  ? "border-white/25 bg-white/[0.03] hover:border-cyan-300/60 hover:bg-cyan-300/10 disabled:opacity-45"
                                  : "border-gray-300 bg-white hover:border-cyan-400 hover:bg-cyan-50 disabled:opacity-45",
                            )}
                          >
                            {captureTarget ? (
                              <span
                                aria-hidden="true"
                                className={classNames(
                                  "h-2.5 w-2.5 rounded-full",
                                  isDark ? "bg-cyan-100" : "bg-cyan-600",
                                )}
                              />
                            ) : null}
                          </button>
                        </span>
                      </span>
                      {document.workspace_path ? (
                        <span className="truncate text-[10px] text-[var(--color-text-muted)]">{document.workspace_path}</span>
                      ) : null}
                    </div>
                  );
                }) : (
                  <div className="flex h-full items-center justify-center px-3 text-center text-xs text-[var(--color-text-muted)]">
                    {t("voiceSecretaryNoDocumentsHint", { defaultValue: "Start recording or create a document." })}
                  </div>
                )}
              </div>
              <div className="shrink-0 border-t border-[var(--glass-border-subtle)] p-2">
                <button
                  type="button"
                  className={classNames(
                    "w-full rounded-xl px-2.5 py-2 text-[11px] font-semibold transition-colors disabled:opacity-60",
                    isDark ? "text-slate-400 hover:bg-white/8 hover:text-slate-200" : "text-gray-500 hover:bg-black/5 hover:text-gray-700",
                  )}
                  onClick={() => void archiveDocument()}
                  disabled={!!actionBusy || !activeDocument}
                >
                  {actionBusy === "archive_doc"
                    ? t("voiceSecretaryArchivingDocument", { defaultValue: "Archiving..." })
                    : t("voiceSecretaryArchiveDocument", { defaultValue: "Archive viewed" })}
                </button>
              </div>
            </aside>

            <section
              className={classNames(
                "flex min-h-0 flex-col rounded-2xl border p-3",
                isDark ? "border-white/10 bg-white/[0.04]" : "border-black/10 bg-white",
              )}
            >
              <div className="flex shrink-0 flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className={classNames("truncate text-base font-semibold tracking-[-0.01em]", isDark ? "text-slate-100" : "text-gray-900")}>
                    {documentDisplayTitle}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", isDark ? "bg-cyan-500/10 text-cyan-200" : "bg-cyan-50 text-cyan-700")}>
                      {t("voiceSecretaryMarkdownBadge", { defaultValue: "Markdown" })}
                    </span>
                    <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", activeDocumentPath ? (isDark ? "bg-emerald-500/10 text-emerald-200" : "bg-emerald-50 text-emerald-700") : (isDark ? "bg-slate-800 text-slate-300" : "bg-gray-100 text-gray-600"))}>
                      {activeDocumentPath
                        ? t("voiceSecretaryRepoBackedBadge", { defaultValue: "Repo-backed" })
                        : t("voiceSecretaryWaitingTranscriptBadge", { defaultValue: "Waiting for transcript" })}
                    </span>
                    {voiceDocumentKey(activeDocument) && voiceDocumentKey(activeDocument) === captureTargetDocumentId ? (
                      <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", isDark ? "bg-emerald-500/10 text-emerald-200" : "bg-emerald-50 text-emerald-700")}>
                        {t("voiceSecretaryDefaultDocumentBadge", { defaultValue: "Default document" })}
                      </span>
                    ) : null}
                    {documentHasUnsavedEdits ? (
                      <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", isDark ? "bg-amber-500/10 text-amber-200" : "bg-amber-50 text-amber-700")}>
                        {t("voiceSecretaryUnsavedEditsBadge", { defaultValue: "Unsaved edits" })}
                      </span>
                    ) : null}
                    {documentRemoteChanged ? (
                      <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-medium", isDark ? "bg-blue-500/10 text-blue-200" : "bg-blue-50 text-blue-700")}>
                        {t("voiceSecretaryRemoteChangedBadge", { defaultValue: "Remote update available" })}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 truncate text-[10px] text-[var(--color-text-muted)]">
                    {activeDocumentPath
                      ? t("voiceSecretaryWorkingDocumentPath", {
                          path: activeDocumentPath,
                          defaultValue: "Repo markdown: {{path}}",
                        })
                      : t("voiceSecretaryWorkingDocumentPending", {
                          defaultValue: "Transcript will create a repo markdown document automatically.",
                        })}
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                  {documentRemoteChanged ? (
                    <button
                      type="button"
                      className={classNames(
                        "rounded-full border px-2.5 py-1.5 text-[11px] font-semibold transition-colors disabled:opacity-60",
                        isDark ? "border-blue-300/20 text-blue-200 hover:bg-blue-400/10" : "border-blue-200 text-blue-700 hover:bg-blue-50",
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
                className="mt-3 min-h-0 flex-1 overflow-auto"
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
                "flex min-h-0 flex-col gap-3 rounded-2xl border p-3",
                isDark ? "border-white/10 bg-white/[0.035]" : "border-black/10 bg-gray-50/80",
              )}
            >
              <div className="shrink-0">
                <div className={classNames("text-xs font-semibold", isDark ? "text-slate-200" : "text-gray-800")}>
                  {t("voiceSecretaryInstructionLabel", { defaultValue: "Ask Voice Secretary" })}
                </div>
                <textarea
                  value={documentInstruction}
                  onChange={(event) => setDocumentInstruction(event.target.value)}
                  placeholder={t("voiceSecretaryInstructionPlaceholder", {
                    defaultValue: "Tell Voice Secretary how to refine, split, summarize, or send this document.",
                  })}
                  className={classNames(
                    "mt-2 min-h-[112px] w-full resize-y rounded-2xl border px-3 py-2 text-xs leading-5 outline-none transition-colors",
                    isDark
                      ? "border-white/10 bg-white/[0.06] text-slate-100 placeholder:text-slate-500 focus:border-cyan-300/45"
                      : "border-black/10 bg-white text-gray-900 placeholder:text-gray-400 focus:border-cyan-400/45",
                  )}
                />
                <button
                  type="button"
                  className="mt-2 w-full rounded-xl border border-blue-500 bg-blue-600 px-3 py-2 text-xs font-semibold text-white shadow-lg shadow-blue-500/20 transition-colors hover:bg-blue-500 disabled:opacity-60"
                  onClick={() => void sendDocumentInstruction()}
                  disabled={!!actionBusy || !documentInstruction.trim()}
                >
                  {actionBusy === "instruct_doc"
                    ? t("voiceSecretaryApplyingInstruction", { defaultValue: "Sending..." })
                    : t("voiceSecretaryApplyInstruction", { defaultValue: "Send request" })}
                </button>
              </div>

              <div className={classNames("flex min-h-0 flex-1 flex-col rounded-2xl border text-xs leading-5", isDark ? "border-white/10 bg-black/10 text-slate-300" : "border-black/10 bg-white text-gray-700")}>
                <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--glass-border-subtle)] px-3 py-2">
                  <div className={classNames("font-semibold", isDark ? "text-slate-200" : "text-gray-800")}>
                    {t("voiceSecretaryTranscriptFeedTitle", { defaultValue: "Transcript" })}
                  </div>
                </div>
                <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-2 py-2">
                  {liveTranscriptText ? (
                    <div className={classNames("rounded-lg border-l-2 px-2 py-1.5", isDark ? "border-cyan-300 bg-cyan-300/10 text-cyan-50" : "border-cyan-500 bg-cyan-50 text-cyan-950")}>
                      <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide opacity-70">
                        {t("voiceSecretaryTranscriptLive", { defaultValue: "Live" })}
                      </div>
                      <div className="whitespace-pre-wrap break-words text-[11px] leading-4">{liveTranscriptText}</div>
                    </div>
                  ) : null}
                  {recentTranscriptItems.length ? recentTranscriptItems.map((item) => (
                    <div key={item.id} className={classNames("rounded-lg border-l-2 px-2 py-1.5", isDark ? "border-white/10 bg-white/[0.025] text-slate-300" : "border-gray-200 bg-gray-50 text-gray-700")}>
                      <div className="whitespace-pre-wrap break-words text-[11px] leading-4">{item.text}</div>
                    </div>
                  )) : !liveTranscriptText ? (
                    <div className="rounded-xl border border-dashed border-[var(--glass-border-subtle)] px-2.5 py-4 text-center text-[11px] text-[var(--color-text-muted)]">
                      {recording
                        ? t("voiceSecretaryTranscriptFeedListeningEmpty", { defaultValue: "Listening. Transcript will appear here." })
                        : t("voiceSecretaryTranscriptFeedEmpty", { defaultValue: "Start recording to see transcript here." })}
                    </div>
                  ) : null}
                </div>
              </div>
            </aside>
          </div>
        </div>
      ) : null}

      <button
        type="button"
        className={classNames(
          buttonClassName,
          "relative",
          controlDisabled
            ? ""
            : recording || assistantEnabled
              ? isDark
                ? recording
                  ? "border-rose-400/25 bg-rose-500/15 text-rose-100 hover:bg-rose-500/22"
                  : "border-cyan-400/25 bg-cyan-500/15 text-cyan-100 hover:bg-cyan-500/22"
                : recording
                  ? "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100"
                  : "border-cyan-200 bg-cyan-50 text-cyan-700 hover:bg-cyan-100"
              : "hover:text-[var(--color-text-primary)] active:scale-95",
        )}
        style={{ width: `${buttonSizePx}px`, height: `${buttonSizePx}px` }}
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
    </div>
  );
}
