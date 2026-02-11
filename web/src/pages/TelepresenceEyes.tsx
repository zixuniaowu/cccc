import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import * as api from "../services/api";
import type { GroupMeta, LedgerEvent } from "../types";
import { classNames } from "../utils/classNames";

type Mood = "idle" | "listening" | "thinking" | "speaking" | "error";

type LogLine = {
  who: "me" | "agent";
  text: string;
  ts: number;
};

const MOOD_COLOR: Record<Mood, string> = {
  idle: "#38bdf8", // sky-400
  listening: "#22c55e", // green-500
  thinking: "#f59e0b", // amber-500
  speaking: "#a855f7", // violet-500
  error: "#ef4444", // red-500
};

const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max);

function usePointerVector() {
  const [vec, setVec] = useState({ x: 0, y: 0 });

  // Full-window mouse / touch tracking
  useEffect(() => {
    const handleMove = (ev: MouseEvent | TouchEvent) => {
      const point = "touches" in ev ? ev.touches[0] : ev;
      if (!point) return;
      const nx = clamp((point.clientX / window.innerWidth) * 2 - 1, -1, 1);
      const ny = clamp((point.clientY / window.innerHeight) * 2 - 1, -1, 1);
      setVec({ x: nx, y: ny });
    };
    window.addEventListener("mousemove", handleMove, { passive: true });
    window.addEventListener("touchmove", handleMove, { passive: true });
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("touchmove", handleMove);
    };
  }, []);

  // Device orientation (mobile tilt)
  useEffect(() => {
    const handle = (e: DeviceOrientationEvent) => {
      const gamma = clamp((e.gamma ?? 0) / 45, -1, 1);
      const beta = clamp(((e.beta ?? 0) - 45) / 45, -1, 1);
      setVec((prev) => ({
        x: clamp(prev.x * 0.6 + gamma * 0.4, -1, 1),
        y: clamp(prev.y * 0.6 + beta * 0.4, -1, 1),
      }));
    };
    window.addEventListener("deviceorientation", handle);
    return () => window.removeEventListener("deviceorientation", handle);
  }, []);

  return vec;
}

function Eye({
  mood,
  blink,
  pupilOffset,
  ambient,
}: {
  mood: Mood;
  blink: boolean;
  pupilOffset: { x: number; y: number };
  ambient: number;
}) {
  const pupilScale = 1 + clamp(0.35 - ambient * 0.35, 0, 0.35);
  return (
    <div
      className="eye-shell"
      data-mood={mood}
      data-blink={blink ? "1" : "0"}
      style={{ "--eye-accent": MOOD_COLOR[mood] } as React.CSSProperties}
    >
      <div className="eye-white">
        <div className="eye-lid eye-lid-top" />
        <div className="eye-lid eye-lid-bottom" />
        <div
          className="eye-pupil"
          style={{
            transform: `translate(${pupilOffset.x * 32}px, ${pupilOffset.y * 28}px) scale(${1 + pupilScale * 0.2})`,
          }}
        />
      </div>
    </div>
  );
}

export default function TelepresenceEyes() {
  const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || "");
  const stageRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const recognitionRef = useRef<any>(null);
  const seenIdsRef = useRef<Set<string>>(new Set());
  const [mood, setMood] = useState<Mood>("idle");
  const [blink, setBlink] = useState(false);
  const [ambient, setAmbient] = useState(0.6);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const [listening, setListening] = useState(false);
  const listeningRef = useRef(false);
  const [autoListen, setAutoListen] = useState(!isMobile); // mobile 默认不开自动聆听
  const autoListenRef = useRef(!isMobile);
  const ttsSpeakingRef = useRef(false);
  const lastRecStartRef = useRef(0);
  const [cameraStarted, setCameraStarted] = useState(!isMobile);
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [group, setGroup] = useState<GroupMeta | null>(null);
  const [log, setLog] = useState<LogLine[]>([]);
  const [textInput, setTextInput] = useState("");
  const [apiError, setApiError] = useState<string | null>(null);
  const [connectBusy, setConnectBusy] = useState(false);
  const [tiltEnabled, setTiltEnabled] = useState(false);
  const [camFollow, setCamFollow] = useState(true);
  const [camVec, setCamVec] = useState({ x: 0, y: 0 });
  const lastFrameRef = useRef<Float32Array | null>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const landmarkerRef = useRef<any>(null);
  const [meshEnabled, setMeshEnabled] = useState(!isMobile); // mobile 默认关闭网格，避免 wasm/性能问题
  const [meshStatus, setMeshStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [mirrorEnabled, setMirrorEnabled] = useState(true);
  const missCountRef = useRef(0);
  const currentDelegateRef = useRef<"GPU" | "CPU">("GPU");
  // Avoid replaying historic messages on refresh
  const pageStartTsRef = useRef<number>(Date.now());
  const lastSpeechRef = useRef<{ text: string; ts: number }>({ text: "", ts: 0 });

  const pointerVec = usePointerVector();

  // Idle drift — gentle sine-based wandering gaze
  const [idleDrift, setIdleDrift] = useState({ x: 0, y: 0 });
  useEffect(() => {
    const timer = setInterval(() => {
      const t = performance.now() / 1000;
      setIdleDrift({
        x: Math.sin(t * 0.7) * 0.2 + Math.sin(t * 1.3) * 0.1,
        y: Math.cos(t * 0.5) * 0.15 + Math.sin(t * 1.1 + 2) * 0.08,
      });
    }, 110);
    return () => clearInterval(timer);
  }, []);

  // Micro-saccades — tiny rapid eye jumps (real eyes do this)
  const [saccade, setSaccade] = useState({ x: 0, y: 0 });
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    const fire = () => {
      setSaccade({
        x: (Math.random() - 0.5) * 0.15,
        y: (Math.random() - 0.5) * 0.1,
      });
      setTimeout(() => setSaccade({ x: 0, y: 0 }), 90);
      timeout = setTimeout(fire, 1800 + Math.random() * 3200);
    };
    timeout = setTimeout(fire, 800 + Math.random() * 1500);
    return () => clearTimeout(timeout);
  }, []);

  // Autonomous gaze shifts — intentional "glances" to random directions
  const [gazeShift, setGazeShift] = useState({ x: 0, y: 0 });
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    const shift = () => {
      setGazeShift({
        x: (Math.random() - 0.5) * 0.6,
        y: (Math.random() - 0.5) * 0.4,
      });
      // Hold the glance for 0.8–2s then return
      setTimeout(() => setGazeShift({ x: 0, y: 0 }), 800 + Math.random() * 1200);
      timeout = setTimeout(shift, 4000 + Math.random() * 7000);
    };
    timeout = setTimeout(shift, 2500 + Math.random() * 3500);
    return () => clearTimeout(timeout);
  }, []);

  const startRecognition = useCallback(() => {
    const rec = recognitionRef.current;
    const now = Date.now();
    if (!rec || listeningRef.current || ttsSpeakingRef.current) return;
    if (now - lastRecStartRef.current < 600) return; // rate-limit to avoid rapid flicker
    try {
      rec.start();
      lastRecStartRef.current = now;
    } catch {
      // start while active may throw; safe to ignore because we guard with listeningRef
    }
  }, []);

  const requestTiltPermission = async () => {
    try {
      if (typeof (DeviceOrientationEvent as any)?.requestPermission === "function") {
        const res = await (DeviceOrientationEvent as any).requestPermission();
        if (res === "granted") {
          setTiltEnabled(true);
          return;
        }
      } else {
        // Non-iOS: orientation events already firing
        setTiltEnabled(true);
      }
    } catch {
      setTiltEnabled(false);
    }
  };

  // Reset camera vector when follow off
  useEffect(() => {
    if (!camFollow) {
      setCamVec({ x: 0, y: 0 });
    }
  }, [camFollow]);

  // Lazy-load MediaPipe face landmarker when mesh enabled
  useEffect(() => {
    if (!meshEnabled) return;
    if (landmarkerRef.current) return;
    let cancelled = false;
    const load = async (delegate: "GPU" | "CPU" = "GPU") => {
      try {
        setMeshStatus("loading");
        // Dynamic import to avoid bundling heavy wasm by default
        const vision = await import("@mediapipe/tasks-vision");
        const { FilesetResolver, FaceLandmarker } = vision as any;
        const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
        const fileset = await FilesetResolver.forVisionTasks(`${assetBase}/wasm`);
        const landmarker = await FaceLandmarker.createFromOptions(fileset, {
          baseOptions: {
            modelAssetPath: `${assetBase}/face_landmarker.task`,
          },
          runningMode: "VIDEO",
          numFaces: 1,
          minFaceDetectionConfidence: 0.15,
          minFacePresenceConfidence: 0.4,
          minTrackingConfidence: 0.4,
          outputFaceBlendshapes: false,
          outputFacialTransformationMatrixes: false,
          delegate,
        });
        if (!cancelled) {
          landmarkerRef.current = landmarker;
          currentDelegateRef.current = delegate;
          setMeshStatus("ready");
          missCountRef.current = 0;
        }
        } catch (e) {
          if (!cancelled) {
            setMeshStatus("error");
            console.error("Face mesh load failed", e);
            setMeshEnabled(false);
          }
        }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [meshEnabled]);

  // Natural blink patterns: single, double, slow blink with varied timing
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    const doBlink = () => {
      const dur = 120 + Math.random() * 60;
      setBlink(true);
      setTimeout(() => {
        setBlink(false);
        // 25% chance of double-blink
        if (Math.random() < 0.25) {
          setTimeout(() => {
            setBlink(true);
            setTimeout(() => setBlink(false), 100 + Math.random() * 50);
          }, 160);
        }
      }, dur);
      timeout = setTimeout(doBlink, 2000 + Math.random() * 4000);
    };
    timeout = setTimeout(doBlink, 1200 + Math.random() * 2000);
    return () => clearTimeout(timeout);
  }, []);

  // Camera (front) setup
  useEffect(() => {
    // When关闭时，清理并退出
    if (!cameraStarted) {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }
      setCameraReady(false);
      setCamFollow(false);
      setCameraError(null);
      return;
    }

    let stream: MediaStream | null = null;

    const media = navigator.mediaDevices;
    if (!media || typeof media.getUserMedia !== "function") {
      setCameraError("当前浏览器不支持或未授予摄像头权限（需 https 或新版浏览器）");
      return;
    }

    media
      .getUserMedia({ video: { facingMode: "user", width: { ideal: 640 } }, audio: false })
      .then((s) => {
        stream = s;
        streamRef.current = s;
        if (videoRef.current) {
          videoRef.current.srcObject = s;
          return videoRef.current.play().then(() => {
            setCameraReady(true);
            setCamFollow(true);
          });
        }
        setCameraReady(true);
        setCamFollow(true);
      })
      .catch((err) => setCameraError(err?.message || "无法打开前置摄像头"))
      .finally(() => setMood((m) => (m === "idle" ? "idle" : m)));

    return () => {
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
      }
    };
  }, [cameraStarted]);

  const stopCamera = () => {
    setCameraStarted(false);
  };

  // Ambient light sampling (keeps camera active + feeds pupil size)
  useEffect(() => {
    if (!cameraReady || !videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d", { willReadFrequently: true } as any);
    let rafId = 0;

    const sample = () => {
      if (video.videoWidth > 0 && ctx) {
        canvas.width = 48;
        canvas.height = 36;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
        let sum = 0;
        for (let i = 0; i < data.length; i += 4) {
          sum += (data[i] + data[i + 1] + data[i + 2]) / 3;
        }
        const avg = sum / (data.length / 4) / 255;
        setAmbient((prev) => prev * 0.8 + avg * 0.2);
      }
      rafId = requestAnimationFrame(sample);
    };
    rafId = requestAnimationFrame(sample);
    return () => cancelAnimationFrame(rafId);
  }, [cameraReady]);

  // Speech recognition bootstrap
  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;
    const rec = new SpeechRecognition();
    rec.lang = "zh-CN";
    rec.continuous = true;       // stay open — avoids constant stop/restart gaps
    rec.interimResults = false;
    let restartPending = false;

    const safeRestart = (delayMs: number) => {
      if (restartPending || ttsSpeakingRef.current || !autoListenRef.current) return;
      restartPending = true;
      setTimeout(() => {
        restartPending = false;
        startRecognition();
      }, delayMs);
    };

    rec.onstart = () => {
      listeningRef.current = true;
      setListening(true);
      setMood("listening");
    };
    rec.onend = () => {
      // Chrome can end continuous recognition silently (~60s) — always restart
      listeningRef.current = false;
      setListening(false);
      if (!autoListenRef.current) {
        setMood("idle");
      } else {
        safeRestart(300);
      }
    };
    rec.onerror = (ev: any) => {
      const errType = ev?.error || "";
      listeningRef.current = false;
      setListening(false);
      // "no-speech" is normal silence timeout — don't flash error
      if (errType === "no-speech" || errType === "aborted") {
        safeRestart(400);
        return;
      }
      // "not-allowed" / "service-not-allowed" — fatal, don't retry
      if (errType === "not-allowed" || errType === "service-not-allowed") {
        setMood("error");
        console.warn("Speech recognition denied:", errType);
        return;
      }
      // Other errors (network, etc.) — brief error then retry
      setMood("error");
      console.warn("Speech recognition error:", errType);
      safeRestart(1000);
    };
    rec.onresult = (event: any) => {
      // With continuous mode, iterate over new results
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (!event.results[i].isFinal) continue;
        const transcript = event.results[i][0]?.transcript;
        const textNorm = String(transcript || "").trim();
        if (!textNorm) continue;
        const now = Date.now();
        // Skip duplicate transcripts within 4s
        if (lastSpeechRef.current.text === textNorm && now - lastSpeechRef.current.ts < 4000) {
          continue;
        }
        lastSpeechRef.current = { text: textNorm, ts: now };
        handleSend(textNorm);
      }
    };
    recognitionRef.current = rec;
    setVoiceSupported(true);
  }, [startRecognition]);

  // Auto (re)start recognition when toggled on
  useEffect(() => {
    autoListenRef.current = autoListen;
    if (autoListen && voiceSupported) {
      startRecognition();
    }
  }, [autoListen, voiceSupported, startRecognition]);

  // Keepalive: periodically check if auto-listen should be active but isn't
  useEffect(() => {
    if (!autoListen || !voiceSupported) return;
    const keepalive = setInterval(() => {
      if (autoListenRef.current && !listeningRef.current && !ttsSpeakingRef.current) {
        startRecognition();
      }
    }, 3000);
    return () => clearInterval(keepalive);
  }, [autoListen, voiceSupported, startRecognition]);

  // Group bootstrap (pick first group; fallback create one)
  const connectGroup = useCallback(async (): Promise<string | null> => {
    setConnectBusy(true);
    setApiError(null);
    try {
      const resp = await api.fetchGroups();
      if (!resp.ok) {
        setApiError(resp.error?.message || "无法连接后端 /api/v1/groups");
        return null;
      }
      if (resp.result.groups.length > 0) {
        const g = resp.result.groups[0];
        setGroup(g);
        return g.group_id;
      }
      const created = await api.createGroup("Telepresence", "Mobile eyes link");
      if (!created.ok) {
        setApiError(created.error?.message || "无法创建默认工作组");
        return null;
      }
      const newGroup: GroupMeta = { group_id: created.result.group_id, title: "Telepresence" };
      setGroup(newGroup);
      return newGroup.group_id;
    } catch (e: any) {
      setApiError(e?.message || "网络错误：无法连接后端");
      return null;
    } finally {
      setConnectBusy(false);
    }
  }, []);

  useEffect(() => {
    void connectGroup();
  }, [connectGroup]);

  // Poll agent responses
  useEffect(() => {
    if (!group) return;
    let stopped = false;
    const tick = async () => {
      const resp = await api.fetchLedgerTail(group.group_id, 40);
      if (!resp.ok || stopped) return;
      const events = resp.result.events || [];
      // API returns chat.message; keep forward-ordered for stable TTS/logging
      const ordered = [...events]
        .filter((e) => (e.kind === "chat.message" || e.kind === "chat") && e.id)
        .sort((a, b) => {
          const at = new Date(a.ts || 0).getTime();
          const bt = new Date(b.ts || 0).getTime();
          return at - bt;
        });
      ordered.forEach((ev) => {
        const id = String(ev.id);
        if (seenIdsRef.current.has(id)) return;
        // Skip historical events that happened before this page loaded (prevents replay on refresh)
        const evTs = new Date(ev.ts || 0).getTime();
        if (evTs && evTs < pageStartTsRef.current - 500) {
          seenIdsRef.current.add(id);
          return;
        }
        seenIdsRef.current.add(id);
        const text = (ev.data as any)?.text || "";
        if (!text) return;
        const who: LogLine["who"] = ev.by === "user" ? "me" : "agent";
        if (who === "agent") {
          pushLog({ who, text, ts: Date.now() });
          window.dispatchEvent(new CustomEvent("cccc:agent-reply", { detail: text }));
          if (voiceEnabled && "speechSynthesis" in window) {
            // Chrome bug: long utterances silently cut off (~200+ chars).
            // Split into sentence-sized chunks and speak sequentially.
            window.speechSynthesis.cancel();

            const splitForTTS = (s: string): string[] => {
              const parts = s.split(/(?<=[。！？!?；;\n])/);
              const chunks: string[] = [];
              let buf = "";
              for (const p of parts) {
                if (buf.length + p.length > 150 && buf) {
                  chunks.push(buf.trim());
                  buf = "";
                }
                buf += p;
              }
              if (buf.trim()) chunks.push(buf.trim());
              return chunks.filter(Boolean);
            };

            const chunks = splitForTTS(text);
            setMood("speaking");
            ttsSpeakingRef.current = true;
            if (listeningRef.current && recognitionRef.current) {
              recognitionRef.current.stop();
            }

            const resetAfterSpeak = () => {
              ttsSpeakingRef.current = false;
              setMood("idle");
              if (autoListenRef.current) {
                setTimeout(() => startRecognition(), 500);
              }
            };

            // Watchdog: force-reset if TTS stalls
            const watchdog = setTimeout(() => {
              if (ttsSpeakingRef.current) {
                window.speechSynthesis.cancel();
                resetAfterSpeak();
              }
            }, 30000);

            let i = 0;
            const speakNext = () => {
              if (i >= chunks.length) {
                clearTimeout(watchdog);
                resetAfterSpeak();
                return;
              }
              const u = new SpeechSynthesisUtterance(chunks[i]);
              u.lang = "zh-CN";
              i++;
              u.onend = () => speakNext();
              u.onerror = () => speakNext();
              window.speechSynthesis.speak(u);
            };
            speakNext();
          }
        }
      });
    };
    const handle = setInterval(() => void tick(), 3500);
    void tick();
    return () => {
      stopped = true;
      clearInterval(handle);
    };
  }, [group, voiceEnabled]);

  const pushLog = (line: LogLine) => {
    setLog((prev) => [...prev.slice(-8), line]);
  };

  const ensureGroupId = async (): Promise<string | null> => {
    if (group) return group.group_id;
    return connectGroup();
  };

  // Camera-based motion/face tracking to steer pupils + overlay grid
  useEffect(() => {
    if (!camFollow || !cameraReady || !videoRef.current) return;
    const video = videoRef.current;
    const overlay = overlayRef.current;
    const w = 96;
    const h = 72;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true } as any);
    let detector: any = null;
    if ((window as any).FaceDetector) {
      try {
        detector = new (window as any).FaceDetector({ fastMode: true });
      } catch {
        detector = null;
      }
    }
    let stopped = false;

    const drawGrid = (
      box: DOMRect | null,
      hint: { x: number; y: number } | null,
      meshPoints?: Array<{ x: number; y: number }>
    ) => {
      if (!overlay) return;
      const ctxOv = overlay.getContext("2d");
      if (!ctxOv) return;
      const vw = video.videoWidth || 640;
      const vh = video.videoHeight || 480;
      overlay.width = vw;
      overlay.height = vh;
      ctxOv.clearRect(0, 0, vw, vh);
      // Base crosshair for visibility even没有检测到人脸
      ctxOv.strokeStyle = "rgba(148,163,184,0.4)";
      ctxOv.lineWidth = 1;
      ctxOv.beginPath();
      ctxOv.moveTo(vw / 2 - 12, vh / 2);
      ctxOv.lineTo(vw / 2 + 12, vh / 2);
      ctxOv.moveTo(vw / 2, vh / 2 - 12);
      ctxOv.lineTo(vw / 2, vh / 2 + 12);
      ctxOv.stroke();
      if (box) {
        ctxOv.strokeStyle = "rgba(56,189,248,0.9)";
        ctxOv.lineWidth = 1.2;
        // draw soft ellipse instead of grid to look like face outline
        ctxOv.beginPath();
        ctxOv.ellipse(
          box.x + box.width / 2,
          box.y + box.height / 2,
          box.width * 0.55,
          box.height * 0.65,
          0,
          0,
          Math.PI * 2
        );
        ctxOv.stroke();
      }
      if (meshPoints && meshPoints.length) {
        // Connections for a clearer face shape
        const outlineIdx = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109, 10];
        const lipsOuter = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95, 185, 40, 39, 37, 0, 267, 269, 270, 409, 415, 310, 311, 312, 13, 82, 81, 80, 191, 78, 95, 62, 61];
        const eyeLeft = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246, 33];
        const eyeRight = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466, 263];
        const drawPolyline = (idxs: number[]) => {
          ctxOv.beginPath();
          idxs.forEach((idx, i) => {
            const p = meshPoints[idx];
            const x = p.x * vw;
            const y = p.y * vh;
            if (i === 0) ctxOv.moveTo(x, y);
            else ctxOv.lineTo(x, y);
          });
          ctxOv.stroke();
        };
        ctxOv.strokeStyle = "rgba(56,189,248,0.7)";
        ctxOv.lineWidth = 1.2;
        drawPolyline(outlineIdx);
        drawPolyline(lipsOuter);
        drawPolyline(eyeLeft);
        drawPolyline(eyeRight);

        ctxOv.fillStyle = "rgba(56,189,248,0.7)";
        const nose = meshPoints[1];
        if (nose) {
          ctxOv.beginPath();
          ctxOv.arc(nose.x * vw, nose.y * vh, 3.6, 0, Math.PI * 2);
          ctxOv.fill();
        }
        for (const p of meshPoints) {
          const x = p.x * vw;
          const y = p.y * vh;
          ctxOv.beginPath();
          ctxOv.arc(x, y, 1.6, 0, Math.PI * 2);
          ctxOv.fill();
        }
      }
      if (hint) {
        ctxOv.fillStyle = "rgba(56,189,248,0.85)";
        ctxOv.beginPath();
        ctxOv.arc(hint.x * vw, hint.y * vh, 6, 0, Math.PI * 2);
        ctxOv.fill();
      }
    };

    const loop = async () => {
      if (stopped || !ctx) return;
      if ((video.videoWidth || 0) === 0 || (video.videoHeight || 0) === 0) {
        requestAnimationFrame(loop);
        return;
      }
      ctx.drawImage(video, 0, 0, w, h);
      const { data } = ctx.getImageData(0, 0, w, h);

      const nowMs = performance.now();

      // Highest fidelity: MediaPipe landmarker (if loaded)
      if (meshEnabled && meshStatus === "ready" && landmarkerRef.current) {
        try {
          const result = landmarkerRef.current.detectForVideo(video, nowMs);
          if (result?.faceLandmarks?.length) {
            const ptsRaw = result.faceLandmarks[0] as Array<{ x: number; y: number; z: number }>;
            const pts = mirrorEnabled ? ptsRaw.map((p) => ({ x: 1 - p.x, y: p.y, z: p.z })) : ptsRaw;
            // Head rotation: nose offset from face center (much more sensitive than absolute position)
            const noseR = ptsRaw[1];
            const leftEar = ptsRaw[234];
            const rightEar = ptsRaw[454];
            const foreheadPt = ptsRaw[10];
            const chinPt = ptsRaw[152];
            const faceCx = (leftEar.x + rightEar.x) / 2;
            const faceCy = (foreheadPt.y + chinPt.y) / 2;
            const faceW = Math.abs(rightEar.x - leftEar.x) || 0.15;
            const faceH = Math.abs(chinPt.y - foreheadPt.y) || 0.15;
            // Negate x so avatar mirrors the person's head turn
            const yaw = clamp(-(noseR.x - faceCx) / faceW * 3.5, -1, 1);
            const pitch = clamp((noseR.y - faceCy) / faceH * 2.5, -1, 1);
            setCamVec((prev) => ({
              x: clamp(prev.x * 0.3 + yaw * 0.7, -1, 1),
              y: clamp(prev.y * 0.3 + pitch * 0.7, -1, 1),
            }));

            // Draw mesh (project to overlay size)
            const nosePt = pts[1];
            drawGrid(
              null,
              nosePt ? { x: mirrorEnabled ? 1 - nosePt.x : nosePt.x, y: nosePt.y } : null,
              pts
            );
            if (overlay) {
            const ctxOv = overlay.getContext("2d");
            if (ctxOv) {
                const vw = overlay.width;
                const vh = overlay.height;
                ctxOv.strokeStyle = "rgba(56,189,248,0.6)";
                ctxOv.lineWidth = 0.8;
                const outlineIdx = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109, 10];
                ctxOv.beginPath();
                outlineIdx.forEach((idx, i) => {
                  const p = pts[idx];
                  const x = p.x * vw;
                  const y = p.y * vh;
                  if (i === 0) ctxOv.moveTo(x, y);
                  else ctxOv.lineTo(x, y);
                });
                ctxOv.stroke();
                ctxOv.fillStyle = "rgba(56,189,248,0.9)";
                // key landmarks
                [1, 33, 263, 61, 291, 13].forEach((idx) => {
                  const p = pts[idx];
                  ctxOv.beginPath();
                  ctxOv.arc(p.x * vw, p.y * vh, 2.5, 0, Math.PI * 2);
                  ctxOv.fill();
                });
              }
            }
            missCountRef.current = 0;
            requestAnimationFrame(loop);
            return;
          }
          missCountRef.current += 1;
          if (missCountRef.current > 15 && currentDelegateRef.current === "GPU") {
            // GPU path seems not returning landmarks; retry with CPU once
            landmarkerRef.current = null;
            setMeshStatus("loading");
            await (async () => {
              try {
                const vision = await import("@mediapipe/tasks-vision");
                const { FilesetResolver, FaceLandmarker } = vision as any;
                const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
                const fileset = await FilesetResolver.forVisionTasks(`${assetBase}/wasm`);
                const lm = await FaceLandmarker.createFromOptions(fileset, {
                  baseOptions: { modelAssetPath: `${assetBase}/face_landmarker.task` },
                  runningMode: "VIDEO",
                  numFaces: 1,
                  minFaceDetectionConfidence: 0.1,
                  minFacePresenceConfidence: 0.2,
                  minTrackingConfidence: 0.2,
                  delegate: "CPU",
                });
                landmarkerRef.current = lm;
                currentDelegateRef.current = "CPU";
                setMeshStatus("ready");
                missCountRef.current = 0;
              } catch (e) {
                setMeshStatus("error");
                console.error("Face mesh CPU fallback failed", e);
              }
            })();
          }
        } catch {
          // fall through to other detectors
        }
      }

      // Preferred: FaceDetector API
      if (detector) {
        try {
          const faces = await detector.detect(canvas);
          if (faces && faces[0]?.boundingBox) {
            const box = faces[0].boundingBox as DOMRect;
            const nx = clamp((box.x + box.width / 2) / w * 2 - 1, -1, 1);
            const ny = clamp((box.y + box.height / 2) / h * 2 - 1, -1, 1);
            setCamVec((prev) => ({
              x: clamp(prev.x * 0.6 + nx * 0.4, -1, 1),
              y: clamp(prev.y * 0.6 + ny * 0.4, -1, 1),
            }));
            const vw = video.videoWidth || 640;
            const vh = video.videoHeight || 480;
            const scaled = new DOMRect(
              (box.x / w) * vw,
              (box.y / h) * vh,
              (box.width / w) * vw,
              (box.height / h) * vh
            );
            const cxNorm = (box.x + box.width / 2) / w;
            const cyNorm = (box.y + box.height / 2) / h;
            drawGrid(
              scaled,
              {
                x: mirrorEnabled ? 1 - cxNorm : cxNorm,
                y: cyNorm,
              }
            );
            requestAnimationFrame(loop);
            return;
          }
        } catch {
          // fall back to motion diff
        }
      }

      // Fallback: motion difference centroid
      const prev = lastFrameRef.current;
      const gray = new Float32Array(w * h);
      for (let i = 0, p = 0; i < data.length; i += 4, p++) {
        gray[p] = (data[i] + data[i + 1] + data[i + 2]) / 3;
      }
      if (prev) {
        let sum = 0;
        let cx = 0;
        let cy = 0;
        const thresh = 18;
        for (let p = 0; p < gray.length; p++) {
          const d = Math.abs(gray[p] - prev[p]);
          if (d > thresh) {
            sum += d;
            cx += d * (p % w);
            cy += d * Math.floor(p / w);
          }
        }
        if (sum > 1200) {
          const nx = clamp((cx / sum) / (w - 1) * 2 - 1, -1, 1);
          const ny = clamp((cy / sum) / (h - 1) * 2 - 1, -1, 1);
          setCamVec((prevVec) => ({
            x: clamp(prevVec.x * 0.7 + nx * 0.3, -1, 1),
            y: clamp(prevVec.y * 0.7 + ny * 0.3, -1, 1),
          }));
          drawGrid(
            new DOMRect(
              (cx / sum / (w - 1)) * (video.videoWidth || 640) - 60,
              (cy / sum / (h - 1)) * (video.videoHeight || 480) - 60,
              120,
              120
            ),
            { x: (cx / sum) / (w - 1), y: (cy / sum) / (h - 1) }
          );
        }
      }
      lastFrameRef.current = gray;
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    return () => {
      stopped = true;
    };
  }, [camFollow, cameraReady, meshEnabled, meshStatus, mirrorEnabled]);

  const handleSend = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const groupId = await ensureGroupId();
    if (!groupId) {
      setMood("error");
      setLog((prev) => [...prev, { who: "agent", text: "未找到工作组，无法发送", ts: Date.now() }]);
      return;
    }
    pushLog({ who: "me", text: trimmed, ts: Date.now() });
    setMood("thinking");
    const resp = await api.sendMessage(groupId, trimmed, []);
    if (!resp.ok) {
      setMood("error");
      pushLog({ who: "agent", text: `发送失败: ${resp.error?.message || "unknown"}`, ts: Date.now() });
      return;
    }
    setTextInput("");
  };

  const handleVoiceToggle = () => {
    if (!voiceSupported || !recognitionRef.current) return;
    if (listening) {
      autoListenRef.current = false;
      setAutoListen(false);
      recognitionRef.current.stop();
      return;
    }
    startRecognition();
  };

  const eyeMood = listening ? "listening" : mood;

  // Mood-driven gaze bias (thinking → up-left, listening → center-up, etc.)
  const moodOffset = useMemo(() => {
    switch (eyeMood) {
      case "thinking": return { x: -0.18, y: -0.28 };   // classic "thinking" up-left
      case "listening": return { x: 0, y: -0.08 };       // slightly upward, attentive
      case "speaking": return { x: 0.06, y: 0.02 };      // small drift
      case "error": return { x: 0, y: 0.15 };            // downcast
      default: return { x: 0, y: 0 };
    }
  }, [eyeMood]);

  const accent = useMemo(() => MOOD_COLOR[eyeMood], [eyeMood]);
  const combinedOffset = useMemo(
    () => ({
      x: clamp(camVec.x * 0.45 + pointerVec.x * 0.15 + idleDrift.x + saccade.x + gazeShift.x + moodOffset.x, -1, 1),
      y: clamp(camVec.y * 0.45 + pointerVec.y * 0.15 + idleDrift.y + saccade.y + gazeShift.y + moodOffset.y, -1, 1),
    }),
    [pointerVec, camVec, idleDrift, saccade, gazeShift, moodOffset]
  );

  return (
    <div
      ref={stageRef}
      className="min-h-screen eyes-stage text-white"
      style={{ "--eye-accent": accent } as React.CSSProperties}
    >
      <div className="max-w-4xl mx-auto px-4 py-6 flex flex-col gap-6">
        <header className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xl font-semibold tracking-tight">Telepresence Eyes</div>
            <div className="flex items-center gap-2 text-sm">
              <span className="px-2 py-1 rounded-full bg-white/10 border border-white/10">
                {group ? `连接工作组: ${group.title || group.group_id}` : "正在寻找工作组..."}
              </span>
              <span
                className={classNames(
                  "px-2 py-1 rounded-full border text-xs",
                  cameraReady ? "bg-emerald-500/20 border-emerald-400/50 text-emerald-100" : "bg-white/5 border-white/20"
                )}
              >
                {cameraReady ? "Camera ON" : cameraError ? "Camera blocked" : "Camera..."}
              </span>
              <span
                className={classNames(
                  "px-2 py-1 rounded-full border text-xs",
                  camFollow ? "bg-cyan-500/20 border-cyan-400/50 text-cyan-50" : "bg-white/5 border-white/20 text-white/70"
                )}
              >
                {camFollow ? "摄像跟随" : "摄像未跟随"}
              </span>
              {apiError && (
                <span className="px-2 py-1 rounded-full bg-red-500/15 border border-red-400/50 text-red-100 text-xs">
                  后端未连: {apiError}
                </span>
              )}
              <button
                onClick={() => void connectGroup()}
                disabled={connectBusy}
                className="px-2 py-1 rounded-lg border border-white/15 bg-white/5 text-white/80 text-xs hover:bg-white/10 disabled:opacity-40"
              >
                {connectBusy ? "重试中..." : "重试连接"}
              </button>
            </div>
          </div>
          <p className="text-sm text-white/70 leading-relaxed">
            两只大眼睛会跟随你的触控/姿态，前置摄像头用于捕捉环境光，语音可直接向电脑上的 Agent 提问。响应会通过语音合成播报。
          </p>
        </header>

        <section className="flex flex-col items-center gap-4">
          <div className="eyes-pair" aria-label="Animated eyes">
            <Eye mood={eyeMood} blink={blink} pupilOffset={combinedOffset} ambient={ambient} />
            <Eye mood={eyeMood} blink={blink} pupilOffset={{ x: combinedOffset.x * 0.9, y: combinedOffset.y }} ambient={ambient} />
          </div>
          <div className="flex items-center gap-3 flex-wrap justify-center">
            <button
              onClick={handleVoiceToggle}
              className={classNames(
                "px-4 py-2 rounded-xl border font-medium transition-all",
                listening
                  ? "bg-emerald-500/20 border-emerald-400/60 text-emerald-50 shadow-[0_0_0_3px_rgba(16,185,129,0.15)]"
                  : "bg-white/5 border-white/20 text-white/90 hover:bg-white/10"
              )}
              disabled={!voiceSupported}
            >
              {autoListen ? "自动聆听中" : listening ? "正在聆听…" : voiceSupported ? "语音提问" : "浏览器不支持语音"}
            </button>
            <button
              onClick={() => setAutoListen((v) => !v)}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                autoListen ? "bg-emerald-500/20 border-emerald-400/60" : "bg-white/5 border-white/15"
              )}
              disabled={!voiceSupported}
            >
              {autoListen ? "自动聆听已开" : "开启自动聆听"}
            </button>
            <button
              onClick={() => setVoiceEnabled((v) => !v)}
              className="px-3 py-2 rounded-xl bg-white/5 border border-white/15 text-white/80 hover:bg-white/10 transition"
            >
              {voiceEnabled ? "静音回复" : "开启播报"}
            </button>
            <button
              onClick={() => setMood("idle")}
              className="px-3 py-2 rounded-xl bg-white/5 border border-white/15 text-white/80 hover:bg-white/10 transition"
            >
              重置表情
            </button>
            <button
              onClick={() => void requestTiltPermission()}
              className="px-3 py-2 rounded-xl bg-white/5 border border-white/15 text-white/80 hover:bg-white/10 transition"
            >
              {tiltEnabled ? "体感已开" : "启用体感跟随"}
            </button>
            <button
              onClick={() => setCamFollow((v) => !v)}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                camFollow ? "bg-cyan-500/20 border-cyan-400/60" : "bg-white/5 border-white/15"
              )}
            >
              {camFollow ? "关闭摄像跟随" : "启用摄像跟随"}
            </button>
            {!cameraStarted ? (
              <button
                onClick={() => setCameraStarted(true)}
                className="px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition bg-amber-500/20 border-amber-400/60"
              >
                开启摄像头
              </button>
            ) : (
              <button
                onClick={stopCamera}
                className="px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition bg-amber-500/20 border-amber-400/60"
              >
                关闭摄像头
              </button>
            )}

            {!cameraStarted && (
              <button
                onClick={() => setCameraStarted(true)}
                className="px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition bg-amber-500/20 border-amber-400/60"
              >
                开启摄像头
              </button>
            )}

            <button
              onClick={() => setMeshEnabled((v) => !v)}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                meshEnabled ? "bg-emerald-500/20 border-emerald-400/60" : "bg-white/5 border-white/15"
              )}
            >
              {meshEnabled ? "关闭网格" : meshStatus === "loading" ? "网格加载中..." : "开启网格"}
            </button>
            <button
              onClick={() => setMirrorEnabled((v) => !v)}
              className={classNames(
                "px-3 py-2 rounded-xl border text-white/80 hover:bg-white/10 transition",
                mirrorEnabled ? "bg-indigo-500/20 border-indigo-400/60" : "bg-white/5 border-white/15"
              )}
            >
              {mirrorEnabled ? "镜像视图" : "正常视图"}
            </button>
          </div>
        </section>

        <section className="bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-white/90">
              摄像头预览 / 网格
              <span
                className={classNames(
                  "px-2 py-0.5 rounded-full text-[11px] border",
                  camFollow ? "bg-cyan-500/20 border-cyan-400/50 text-cyan-50" : "bg-white/5 border-white/20 text-white/70"
                )}
              >
                {camFollow ? "跟随中" : "未跟随"}
              </span>
              <span
                className={classNames(
                  "px-2 py-0.5 rounded-full text-[11px] border",
                  meshStatus === "ready"
                    ? "bg-emerald-500/20 border-emerald-400/60 text-emerald-50"
                    : meshStatus === "loading"
                    ? "bg-amber-500/20 border-amber-400/60 text-amber-50"
                    : meshStatus === "error"
                    ? "bg-red-500/20 border-red-400/60 text-red-50"
                    : "bg-white/5 border-white/20 text-white/70"
                )}
              >
                {meshStatus === "ready" ? "Face mesh 就绪" : meshStatus === "loading" ? "Face mesh 加载中" : "Face mesh 未启用"}
              </span>
            </div>
            <div className="text-xs text-white/60">
              若未显示视频，请确认摄像头权限；面部网格在支持 FaceDetector 的浏览器上效果更好。
            </div>
          </div>
          <div className="camera-frame">
            <video ref={videoRef} className="camera-video" muted playsInline autoPlay />
            <canvas ref={overlayRef} className="camera-overlay" />
          </div>
        </section>

        <section className="bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="flex items-center gap-2 text-sm font-semibold text-white/90 mb-3">
            文字/语音 转发到 Agent
          </div>
          <div className="flex flex-col gap-3">
            <textarea
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend(textInput);
                }
              }}
              placeholder="直接输入或用语音提问… (Enter 发送, Shift+Enter 换行)"
              className="w-full min-h-[88px] rounded-xl bg-black/30 border border-white/10 px-3 py-2 text-white/90 placeholder:text-white/40 focus:outline-none focus:ring-2 focus:ring-cyan-400/60"
            />
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs text-white/60">
                发送后会轮询最近回复；请确保电脑端 Agent 已在工作组里运行。
              </div>
              <button
                onClick={() => handleSend(textInput)}
                className="px-4 py-2 rounded-xl bg-cyan-500 text-black font-semibold hover:bg-cyan-400 transition"
              >
                发送
              </button>
            </div>
          </div>
        </section>

        <section className="bg-black/30 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="flex items-center gap-2 text-sm font-semibold text-white/90 mb-3">
            实时对话记录
            <span className="text-[10px] px-2 py-1 rounded-full bg-white/10 text-white/70">最近 10 条</span>
          </div>
          <div className="flex flex-col gap-2 max-h-56 overflow-auto pr-1">
            {log.length === 0 && <div className="text-white/50 text-sm">等待第一条消息…</div>}
            {log.map((line) => (
              <div
                key={line.ts + line.text}
                className={classNames(
                  "rounded-xl px-3 py-2 text-sm",
                  line.who === "me" ? "bg-cyan-500/15 text-cyan-100 self-end" : "bg-white/8 text-white/90 self-start"
                )}
              >
                <span className="text-xs uppercase tracking-wide opacity-60 mr-2">{line.who === "me" ? "ME" : "AGENT"}</span>
                {line.text}
              </div>
            ))}
          </div>
        </section>

        <section className="bg-white/5 border border-white/10 rounded-2xl p-4 backdrop-blur">
          <div className="text-sm font-semibold text-white/90 mb-2">外设 / 机器人臂预留接口</div>
          <p className="text-sm text-white/70 leading-relaxed">
            本页面已暴露一个极简事件流：每当收到 Agent 回复时，可在浏览器控制台监听{" "}
            <code>{'window.dispatchEvent(new CustomEvent("cccc:agent-reply", { detail: "<text>" }))'}</code>。
            你可以在本机或同一局域网的 WebSocket 服务中桥接这些事件，驱动机械臂或其它外设。
          </p>
        </section>
      </div>

      {/* Hidden canvas for ambient sampling */}
      <canvas ref={canvasRef} className="hidden" />
    </div>
  );
}

// Simple error boundary to avoid blank screen on mobile if runtime errors occur
export class TelepresenceEyesBoundary extends React.Component<{ children?: React.ReactNode }, { hasError: boolean; msg?: string }> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, msg: "" };
  }
  static getDerivedStateFromError(error: any) {
    return { hasError: true, msg: String(error?.message || error) };
  }
  componentDidCatch(error: any, info: any) {
    console.error("TelepresenceEyes error", error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-6 text-center">
          <div className="text-xl font-semibold mb-2">页面出错了</div>
          <div className="text-sm text-white/70 mb-4">{this.state.msg || "Unknown error"}</div>
          <div className="text-xs text-white/60">请刷新再试，或在桌面浏览器打开查看控制台日志。</div>
        </div>
      );
    }
    return <>{this.props.children}</>;
  }
}
