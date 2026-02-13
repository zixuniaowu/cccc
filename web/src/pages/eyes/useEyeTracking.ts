import type { Dispatch, SetStateAction } from "react";
import { useEffect, useRef, useState } from "react";
import { clamp, IS_MOBILE } from "./constants";

type Setter<T> = Dispatch<SetStateAction<T>>;
type DetectorStatus = "idle" | "loading" | "ready" | "error";

interface UseEyeTrackingReturn {
  camVec: { x: number; y: number };
  ambient: number;
  cameraStarted: boolean;
  setCameraStarted: Setter<boolean>;
  cameraReady: boolean;
  cameraError: string | null;
  camFollow: boolean;
  setCamFollow: Setter<boolean>;
  meshEnabled: boolean;
  setMeshEnabled: Setter<boolean>;
  meshStatus: DetectorStatus;
  mirrorEnabled: boolean;
  setMirrorEnabled: Setter<boolean>;
  handEnabled: boolean;
  setHandEnabled: Setter<boolean>;
  handStatus: DetectorStatus;
  poseEnabled: boolean;
  setPoseEnabled: Setter<boolean>;
  poseStatus: DetectorStatus;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  overlayRef: React.RefObject<HTMLCanvasElement | null>;
}

// ── Hand landmark connections (21 points) ──
const HAND_CONNECTIONS: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4],       // thumb
  [0, 5], [5, 6], [6, 7], [7, 8],       // index
  [0, 9], [9, 10], [10, 11], [11, 12],  // middle
  [0, 13], [13, 14], [14, 15], [15, 16],// ring
  [0, 17], [17, 18], [18, 19], [19, 20],// pinky
  [5, 9], [9, 13], [13, 17],            // palm
];

// ── Pose landmark connections (33 points) ──
const POSE_CONNECTIONS: [number, number][] = [
  // Face
  [0, 1], [1, 2], [2, 3], [3, 7],
  [0, 4], [4, 5], [5, 6], [6, 8],
  // Torso
  [9, 10],
  [11, 12], [11, 23], [12, 24], [23, 24],
  // Left arm
  [11, 13], [13, 15], [15, 17], [15, 19], [15, 21], [17, 19],
  // Right arm
  [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [18, 20],
  // Left leg
  [23, 25], [25, 27], [27, 29], [27, 31], [29, 31],
  // Right leg
  [24, 26], [26, 28], [28, 30], [28, 32], [30, 32],
];

// ── Face mesh polyline indices ──
const FACE_OUTLINE = [
  10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365,
  379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93,
  234, 127, 162, 21, 54, 103, 67, 109, 10,
];
const FACE_LIPS = [
  61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 308, 324, 318,
  402, 317, 14, 87, 178, 88, 95, 185, 40, 39, 37, 0, 267, 269, 270,
  409, 415, 310, 311, 312, 13, 82, 81, 80, 191, 78, 95, 62, 61,
];
const FACE_EYE_LEFT = [
  33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160,
  161, 246, 33,
];
const FACE_EYE_RIGHT = [
  263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386,
  387, 388, 466, 263,
];
const FACE_KEY_POINTS = [1, 33, 263, 61, 291, 13];

/**
 * Camera stream management, ambient light sampling, and multi-detector tracking.
 * Supports face mesh, hand landmarks, and pose landmarks via MediaPipe.
 */
export function useEyeTracking(): UseEyeTrackingReturn {
  const [camVec, setCamVec] = useState({ x: 0, y: 0 });
  const [ambient, setAmbient] = useState(0.6);
  const [cameraStarted, setCameraStarted] = useState(!IS_MOBILE);
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [camFollow, setCamFollow] = useState(true);
  const [meshEnabled, setMeshEnabled] = useState(!IS_MOBILE);
  const [meshStatus, setMeshStatus] = useState<DetectorStatus>("idle");
  const [mirrorEnabled, setMirrorEnabled] = useState(true);
  const [handEnabled, setHandEnabled] = useState(false);
  const [handStatus, setHandStatus] = useState<DetectorStatus>("idle");
  const [poseEnabled, setPoseEnabled] = useState(false);
  const [poseStatus, setPoseStatus] = useState<DetectorStatus>("idle");

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const landmarkerRef = useRef<any>(null);
  const handLandmarkerRef = useRef<any>(null);
  const poseLandmarkerRef = useRef<any>(null);
  const lastFrameRef = useRef<Float32Array | null>(null);
  const missCountRef = useRef(0);
  const currentDelegateRef = useRef<"GPU" | "CPU">("GPU");

  // Reset cam vector when follow off
  useEffect(() => {
    if (!camFollow) setCamVec({ x: 0, y: 0 });
  }, [camFollow]);

  // ── Camera stream setup ──
  useEffect(() => {
    if (!cameraStarted) {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      if (videoRef.current) videoRef.current.srcObject = null;
      setCameraReady(false);
      setCamFollow(false);
      setCameraError(null);
      return;
    }

    let stream: MediaStream | null = null;
    const media = navigator.mediaDevices;
    if (!media || typeof media.getUserMedia !== "function") {
      setCameraError("当前浏览器不支持或未授予摄像头权限");
      return;
    }

    media
      .getUserMedia({
        video: { facingMode: "user", width: { ideal: 640 } },
        audio: false,
      })
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
      .catch((err) => setCameraError(err?.message || "无法打开前置摄像头"));

    return () => {
      if (stream) stream.getTracks().forEach((t) => t.stop());
    };
  }, [cameraStarted]);

  // ── Ambient light sampling ──
  useEffect(() => {
    if (!cameraReady || !videoRef.current) return;
    const video = videoRef.current;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true }) as CanvasRenderingContext2D | null;
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

  // ── Lazy-load MediaPipe face landmarker ──
  useEffect(() => {
    if (!meshEnabled) return;
    if (landmarkerRef.current) return;
    let cancelled = false;

    const load = async (delegate: "GPU" | "CPU" = "GPU") => {
      try {
        setMeshStatus("loading");
        const vision = await import("@mediapipe/tasks-vision");
        const { FilesetResolver, FaceLandmarker } = vision as any;
        const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
        const fileset = await FilesetResolver.forVisionTasks(
          `${assetBase}/wasm`
        );
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
    return () => { cancelled = true; };
  }, [meshEnabled]);

  // ── Lazy-load MediaPipe hand landmarker ──
  useEffect(() => {
    if (!handEnabled) return;
    if (handLandmarkerRef.current) return;
    let cancelled = false;

    const load = async () => {
      try {
        setHandStatus("loading");
        const vision = await import("@mediapipe/tasks-vision");
        const { FilesetResolver, HandLandmarker } = vision as any;
        const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
        const fileset = await FilesetResolver.forVisionTasks(
          `${assetBase}/wasm`
        );
        const landmarker = await HandLandmarker.createFromOptions(fileset, {
          baseOptions: {
            modelAssetPath: `${assetBase}/hand_landmarker.task`,
          },
          runningMode: "VIDEO",
          numHands: 2,
          minHandDetectionConfidence: 0.4,
          minHandPresenceConfidence: 0.4,
          minTrackingConfidence: 0.4,
        });
        if (!cancelled) {
          handLandmarkerRef.current = landmarker;
          setHandStatus("ready");
        }
      } catch (e) {
        if (!cancelled) {
          setHandStatus("error");
          console.error("Hand landmarker load failed", e);
        }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [handEnabled]);

  // ── Lazy-load MediaPipe pose landmarker ──
  useEffect(() => {
    if (!poseEnabled) return;
    if (poseLandmarkerRef.current) return;
    let cancelled = false;

    const load = async () => {
      try {
        setPoseStatus("loading");
        const vision = await import("@mediapipe/tasks-vision");
        const { FilesetResolver, PoseLandmarker } = vision as any;
        const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
        const fileset = await FilesetResolver.forVisionTasks(
          `${assetBase}/wasm`
        );
        const landmarker = await PoseLandmarker.createFromOptions(fileset, {
          baseOptions: {
            modelAssetPath: `${assetBase}/pose_landmarker_lite.task`,
          },
          runningMode: "VIDEO",
          numPoses: 1,
          minPoseDetectionConfidence: 0.4,
          minPosePresenceConfidence: 0.4,
          minTrackingConfidence: 0.4,
        });
        if (!cancelled) {
          poseLandmarkerRef.current = landmarker;
          setPoseStatus("ready");
        }
      } catch (e) {
        if (!cancelled) {
          setPoseStatus("error");
          console.error("Pose landmarker load failed", e);
        }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [poseEnabled]);

  // ── Drawing helpers ──
  const drawFace = (
    ctx: CanvasRenderingContext2D,
    pts: Array<{ x: number; y: number }>,
    cw: number,
    ch: number
  ) => {
    const drawPolyline = (idxs: number[]) => {
      ctx.beginPath();
      idxs.forEach((idx, i) => {
        const p = pts[idx];
        if (!p) return;
        const x = p.x * cw;
        const y = p.y * ch;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    };

    // Outline, lips, eyes
    ctx.strokeStyle = "rgba(34,211,238,0.8)"; // cyan
    ctx.lineWidth = 1.2;
    drawPolyline(FACE_OUTLINE);
    drawPolyline(FACE_LIPS);
    drawPolyline(FACE_EYE_LEFT);
    drawPolyline(FACE_EYE_RIGHT);

    // All points
    ctx.fillStyle = "rgba(34,211,238,0.6)";
    for (const p of pts) {
      ctx.beginPath();
      ctx.arc(p.x * cw, p.y * ch, 1.4, 0, Math.PI * 2);
      ctx.fill();
    }

    // Key landmarks
    ctx.fillStyle = "rgba(34,211,238,0.95)";
    for (const idx of FACE_KEY_POINTS) {
      const p = pts[idx];
      if (!p) continue;
      ctx.beginPath();
      ctx.arc(p.x * cw, p.y * ch, 2.8, 0, Math.PI * 2);
      ctx.fill();
    }

    // Nose highlight
    const nose = pts[1];
    if (nose) {
      ctx.fillStyle = "rgba(34,211,238,0.9)";
      ctx.beginPath();
      ctx.arc(nose.x * cw, nose.y * ch, 3.6, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  const drawHands = (
    ctx: CanvasRenderingContext2D,
    handsLandmarks: Array<Array<{ x: number; y: number }>>,
    cw: number,
    ch: number
  ) => {
    for (const pts of handsLandmarks) {
      // Connections
      ctx.strokeStyle = "rgba(52,211,153,0.8)"; // emerald
      ctx.lineWidth = 1.5;
      for (const [a, b] of HAND_CONNECTIONS) {
        const pa = pts[a];
        const pb = pts[b];
        if (!pa || !pb) continue;
        ctx.beginPath();
        ctx.moveTo(pa.x * cw, pa.y * ch);
        ctx.lineTo(pb.x * cw, pb.y * ch);
        ctx.stroke();
      }

      // Points
      ctx.fillStyle = "rgba(52,211,153,0.9)";
      for (const p of pts) {
        ctx.beginPath();
        ctx.arc(p.x * cw, p.y * ch, 2.5, 0, Math.PI * 2);
        ctx.fill();
      }

      // Fingertips larger
      ctx.fillStyle = "rgba(110,231,183,0.95)";
      for (const idx of [4, 8, 12, 16, 20]) {
        const p = pts[idx];
        if (!p) continue;
        ctx.beginPath();
        ctx.arc(p.x * cw, p.y * ch, 4, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  };

  const drawPose = (
    ctx: CanvasRenderingContext2D,
    pts: Array<{ x: number; y: number; visibility?: number }>,
    cw: number,
    ch: number
  ) => {
    const VIS_THRESH = 0.5;

    // Connections
    ctx.strokeStyle = "rgba(251,191,36,0.7)"; // amber
    ctx.lineWidth = 2;
    for (const [a, b] of POSE_CONNECTIONS) {
      const pa = pts[a];
      const pb = pts[b];
      if (!pa || !pb) continue;
      if ((pa.visibility ?? 1) < VIS_THRESH || (pb.visibility ?? 1) < VIS_THRESH) continue;
      ctx.beginPath();
      ctx.moveTo(pa.x * cw, pa.y * ch);
      ctx.lineTo(pb.x * cw, pb.y * ch);
      ctx.stroke();
    }

    // Points
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i];
      if (!p || (p.visibility ?? 1) < VIS_THRESH) continue;
      ctx.fillStyle = "rgba(251,191,36,0.9)";
      ctx.beginPath();
      ctx.arc(p.x * cw, p.y * ch, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  // ── Tracking loop ──
  useEffect(() => {
    if (!camFollow || !cameraReady || !videoRef.current) return;
    const video = videoRef.current;
    const overlay = overlayRef.current;
    const w = 96;
    const h = 72;
    const diffCanvas = document.createElement("canvas");
    diffCanvas.width = w;
    diffCanvas.height = h;
    const diffCtx = diffCanvas.getContext("2d", { willReadFrequently: true }) as CanvasRenderingContext2D | null;
    let detector: any = null;
    if ((window as any).FaceDetector) {
      try {
        detector = new (window as any).FaceDetector({ fastMode: true });
      } catch {
        detector = null;
      }
    }
    let stopped = false;

    const loop = async () => {
      if (stopped || !diffCtx) return;
      if ((video.videoWidth || 0) === 0 || (video.videoHeight || 0) === 0) {
        requestAnimationFrame(loop);
        return;
      }

      diffCtx.drawImage(video, 0, 0, w, h);
      const { data } = diffCtx.getImageData(0, 0, w, h);
      const nowMs = performance.now();

      // Canvas dimensions for the mesh overlay
      const vw = video.videoWidth || 640;
      const vh = video.videoHeight || 480;

      // Prepare overlay canvas (black background, separate from video)
      let ctxOv: CanvasRenderingContext2D | null = null;
      if (overlay) {
        overlay.width = vw;
        overlay.height = vh;
        ctxOv = overlay.getContext("2d");
        if (ctxOv) {
          ctxOv.fillStyle = "#020617";
          ctxOv.fillRect(0, 0, vw, vh);

          // Crosshair
          ctxOv.strokeStyle = "rgba(148,163,184,0.3)";
          ctxOv.lineWidth = 1;
          ctxOv.beginPath();
          ctxOv.moveTo(vw / 2 - 12, vh / 2);
          ctxOv.lineTo(vw / 2 + 12, vh / 2);
          ctxOv.moveTo(vw / 2, vh / 2 - 12);
          ctxOv.lineTo(vw / 2, vh / 2 + 12);
          ctxOv.stroke();
        }
      }

      let faceDetected = false;

      // ── MediaPipe face landmarker ──
      if (meshEnabled && meshStatus === "ready" && landmarkerRef.current) {
        try {
          const result = landmarkerRef.current.detectForVideo(video, nowMs);
          if (result?.faceLandmarks?.length) {
            const ptsRaw = result.faceLandmarks[0] as Array<{
              x: number; y: number; z: number;
            }>;
            const pts = mirrorEnabled
              ? ptsRaw.map((p) => ({ x: 1 - p.x, y: p.y, z: p.z }))
              : ptsRaw;

            // Head rotation via nose offset
            const noseR = ptsRaw[1];
            const leftEar = ptsRaw[234];
            const rightEar = ptsRaw[454];
            const foreheadPt = ptsRaw[10];
            const chinPt = ptsRaw[152];
            const faceCx = (leftEar.x + rightEar.x) / 2;
            const faceCy = (foreheadPt.y + chinPt.y) / 2;
            const faceW = Math.abs(rightEar.x - leftEar.x) || 0.15;
            const faceH = Math.abs(chinPt.y - foreheadPt.y) || 0.15;
            const yaw = clamp((-(noseR.x - faceCx) / faceW) * 5.0, -1, 1);
            const pitch = clamp(((noseR.y - faceCy) / faceH) * 4.0, -1, 1);
            setCamVec((prev) => ({
              x: clamp(prev.x * 0.15 + yaw * 0.85, -1, 1),
              y: clamp(prev.y * 0.15 + pitch * 0.85, -1, 1),
            }));

            if (ctxOv) {
              drawFace(ctxOv, pts, vw, vh);
            }

            faceDetected = true;
            missCountRef.current = 0;
          } else {
            // GPU fallback to CPU if too many misses
            missCountRef.current += 1;
            if (missCountRef.current > 15 && currentDelegateRef.current === "GPU") {
              landmarkerRef.current = null;
              setMeshStatus("loading");
              try {
                const vision = await import("@mediapipe/tasks-vision");
                const { FilesetResolver, FaceLandmarker } = vision as any;
                const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
                const fileset = await FilesetResolver.forVisionTasks(`${assetBase}/wasm`);
                const lm = await FaceLandmarker.createFromOptions(fileset, {
                  baseOptions: {
                    modelAssetPath: `${assetBase}/face_landmarker.task`,
                  },
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
            }
          }
        } catch {
          // fall through
        }
      }

      // ── MediaPipe hand landmarker ──
      if (handEnabled && handStatus === "ready" && handLandmarkerRef.current) {
        try {
          const result = handLandmarkerRef.current.detectForVideo(video, nowMs + 0.1);
          if (result?.landmarks?.length && ctxOv) {
            const allHands = (result.landmarks as Array<Array<{ x: number; y: number; z: number }>>).map(
              (hand) => mirrorEnabled
                ? hand.map((p) => ({ x: 1 - p.x, y: p.y }))
                : hand.map((p) => ({ x: p.x, y: p.y }))
            );
            drawHands(ctxOv, allHands, vw, vh);
          }
        } catch {
          // ignore
        }
      }

      // ── MediaPipe pose landmarker ──
      if (poseEnabled && poseStatus === "ready" && poseLandmarkerRef.current) {
        try {
          const result = poseLandmarkerRef.current.detectForVideo(video, nowMs + 0.2);
          if (result?.landmarks?.length && ctxOv) {
            const ptsRaw = result.landmarks[0] as Array<{
              x: number; y: number; z: number; visibility?: number;
            }>;
            const pts = mirrorEnabled
              ? ptsRaw.map((p) => ({ x: 1 - p.x, y: p.y, visibility: p.visibility }))
              : ptsRaw.map((p) => ({ x: p.x, y: p.y, visibility: p.visibility }));
            drawPose(ctxOv, pts, vw, vh);
          }
        } catch {
          // ignore
        }
      }

      // ── Fallback face detection (when mesh disabled) ──
      if (!faceDetected && !meshEnabled) {
        if (detector) {
          try {
            const faces = await detector.detect(diffCanvas);
            if (faces && faces[0]?.boundingBox) {
              const box = faces[0].boundingBox as DOMRect;
              const nx = clamp(((box.x + box.width / 2) / w) * 2 - 1, -1, 1);
              const ny = clamp(((box.y + box.height / 2) / h) * 2 - 1, -1, 1);
              setCamVec((prev) => ({
                x: clamp(prev.x * 0.6 + nx * 0.4, -1, 1),
                y: clamp(prev.y * 0.6 + ny * 0.4, -1, 1),
              }));
              faceDetected = true;

              if (ctxOv) {
                const cxNorm = (box.x + box.width / 2) / w;
                const cyNorm = (box.y + box.height / 2) / h;
                const dx = mirrorEnabled ? 1 - cxNorm : cxNorm;
                ctxOv.strokeStyle = "rgba(56,189,248,0.9)";
                ctxOv.lineWidth = 1.2;
                ctxOv.beginPath();
                ctxOv.ellipse(
                  dx * vw, cyNorm * vh,
                  (box.width / w) * vw * 0.55,
                  (box.height / h) * vh * 0.65,
                  0, 0, Math.PI * 2
                );
                ctxOv.stroke();
                ctxOv.fillStyle = "rgba(56,189,248,0.85)";
                ctxOv.beginPath();
                ctxOv.arc(dx * vw, cyNorm * vh, 6, 0, Math.PI * 2);
                ctxOv.fill();
              }
            }
          } catch {
            // fall through to motion diff
          }
        }

        // ── Motion difference centroid (ultimate fallback) ──
        if (!faceDetected) {
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
              const nx = clamp((cx / sum / (w - 1)) * 2 - 1, -1, 1);
              const ny = clamp((cy / sum / (h - 1)) * 2 - 1, -1, 1);
              setCamVec((prevVec) => ({
                x: clamp(prevVec.x * 0.7 + nx * 0.3, -1, 1),
                y: clamp(prevVec.y * 0.7 + ny * 0.3, -1, 1),
              }));
            }
          }
          lastFrameRef.current = gray;
        }
      }

      requestAnimationFrame(loop);
    };

    requestAnimationFrame(loop);
    return () => { stopped = true; };
  }, [camFollow, cameraReady, meshEnabled, meshStatus, mirrorEnabled, handEnabled, handStatus, poseEnabled, poseStatus]);

  return {
    camVec,
    ambient,
    cameraStarted,
    setCameraStarted,
    cameraReady,
    cameraError,
    camFollow,
    setCamFollow,
    meshEnabled,
    setMeshEnabled,
    meshStatus,
    mirrorEnabled,
    setMirrorEnabled,
    handEnabled,
    setHandEnabled,
    handStatus,
    poseEnabled,
    setPoseEnabled,
    poseStatus,
    videoRef,
    overlayRef,
  };
}
