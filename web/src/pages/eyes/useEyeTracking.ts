import type { Dispatch, SetStateAction } from "react";
import { useEffect, useRef, useState, useCallback } from "react";
import { clamp, IS_MOBILE } from "./constants";

type Setter<T> = Dispatch<SetStateAction<T>>;

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
  meshStatus: "idle" | "loading" | "ready" | "error";
  mirrorEnabled: boolean;
  setMirrorEnabled: Setter<boolean>;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  overlayRef: React.RefObject<HTMLCanvasElement | null>;
}

/**
 * Camera stream management, ambient light sampling, and face/motion tracking.
 * Supports MediaPipe face landmarker, FaceDetector API, and motion-diff fallback.
 */
export function useEyeTracking(): UseEyeTrackingReturn {
  const [camVec, setCamVec] = useState({ x: 0, y: 0 });
  const [ambient, setAmbient] = useState(0.6);
  const [cameraStarted, setCameraStarted] = useState(!IS_MOBILE);
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [camFollow, setCamFollow] = useState(true);
  const [meshEnabled, setMeshEnabled] = useState(!IS_MOBILE);
  const [meshStatus, setMeshStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [mirrorEnabled, setMirrorEnabled] = useState(true);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const landmarkerRef = useRef<any>(null);
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
    return () => {
      cancelled = true;
    };
  }, [meshEnabled]);

  // ── Face/motion tracking loop ──
  useEffect(() => {
    if (!camFollow || !cameraReady || !videoRef.current) return;
    const video = videoRef.current;
    const overlay = overlayRef.current;
    const w = 96;
    const h = 72;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true }) as CanvasRenderingContext2D | null;
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

      // Base crosshair
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
        const outlineIdx = [
          10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365,
          379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93,
          234, 127, 162, 21, 54, 103, 67, 109, 10,
        ];
        const lipsOuter = [
          61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 308, 324, 318,
          402, 317, 14, 87, 178, 88, 95, 185, 40, 39, 37, 0, 267, 269, 270,
          409, 415, 310, 311, 312, 13, 82, 81, 80, 191, 78, 95, 62, 61,
        ];
        const eyeLeft = [
          33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160,
          161, 246, 33,
        ];
        const eyeRight = [
          263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386,
          387, 388, 466, 263,
        ];

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
          ctxOv.beginPath();
          ctxOv.arc(p.x * vw, p.y * vh, 1.6, 0, Math.PI * 2);
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

      // ── MediaPipe landmarker (highest fidelity) ──
      if (meshEnabled && meshStatus === "ready" && landmarkerRef.current) {
        try {
          const result = landmarkerRef.current.detectForVideo(video, nowMs);
          if (result?.faceLandmarks?.length) {
            const ptsRaw = result.faceLandmarks[0] as Array<{
              x: number;
              y: number;
              z: number;
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
            const yaw = clamp(
              (-(noseR.x - faceCx) / faceW) * 3.5,
              -1,
              1
            );
            const pitch = clamp(
              ((noseR.y - faceCy) / faceH) * 2.5,
              -1,
              1
            );
            setCamVec((prev) => ({
              x: clamp(prev.x * 0.3 + yaw * 0.7, -1, 1),
              y: clamp(prev.y * 0.3 + pitch * 0.7, -1, 1),
            }));

            const nosePt = pts[1];
            drawGrid(
              null,
              nosePt
                ? {
                    x: mirrorEnabled ? 1 - nosePt.x : nosePt.x,
                    y: nosePt.y,
                  }
                : null,
              pts
            );

            // Key landmarks overlay
            if (overlay) {
              const ctxOv = overlay.getContext("2d");
              if (ctxOv) {
                const vw = overlay.width;
                const vh = overlay.height;
                ctxOv.strokeStyle = "rgba(56,189,248,0.6)";
                ctxOv.lineWidth = 0.8;
                const outlineIdx = [
                  10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288,
                  397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136,
                  172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109, 10,
                ];
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

          // GPU fallback to CPU if too many misses
          missCountRef.current += 1;
          if (
            missCountRef.current > 15 &&
            currentDelegateRef.current === "GPU"
          ) {
            landmarkerRef.current = null;
            setMeshStatus("loading");
            try {
              const vision = await import("@mediapipe/tasks-vision");
              const { FilesetResolver, FaceLandmarker } = vision as any;
              const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
              const fileset = await FilesetResolver.forVisionTasks(
                `${assetBase}/wasm`
              );
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
        } catch {
          // fall through to other detectors
        }
      }

      // ── FaceDetector API ──
      if (detector) {
        try {
          const faces = await detector.detect(canvas);
          if (faces && faces[0]?.boundingBox) {
            const box = faces[0].boundingBox as DOMRect;
            const nx = clamp(
              ((box.x + box.width / 2) / w) * 2 - 1,
              -1,
              1
            );
            const ny = clamp(
              ((box.y + box.height / 2) / h) * 2 - 1,
              -1,
              1
            );
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
            drawGrid(scaled, {
              x: mirrorEnabled ? 1 - cxNorm : cxNorm,
              y: cyNorm,
            });
            requestAnimationFrame(loop);
            return;
          }
        } catch {
          // fall back to motion diff
        }
      }

      // ── Motion difference centroid (fallback) ──
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
          const nx = clamp(
            (cx / sum / (w - 1)) * 2 - 1,
            -1,
            1
          );
          const ny = clamp(
            (cy / sum / (h - 1)) * 2 - 1,
            -1,
            1
          );
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
            { x: cx / sum / (w - 1), y: cy / sum / (h - 1) }
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
    videoRef,
    overlayRef,
  };
}
