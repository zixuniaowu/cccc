import type { Dispatch, SetStateAction } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
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
  skinEnabled: boolean;
  setSkinEnabled: Setter<boolean>;
  avatarEnabled: boolean;
  setAvatarEnabled: Setter<boolean>;
  avatarStyle: "robot" | "egg";
  setAvatarStyle: Setter<"robot" | "egg">;
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
const POSE_TORSO = [11, 12, 24, 23];
const POSE_SKIN_BONES: [number, number, number][] = [
  [11, 13, 14], [13, 15, 11], // left upper/lower arm
  [12, 14, 14], [14, 16, 11], // right upper/lower arm
  [23, 25, 18], [25, 27, 15], // left upper/lower leg
  [24, 26, 18], [26, 28, 15], // right upper/lower leg
];

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
  const [handEnabled, setHandEnabled] = useState(!IS_MOBILE);
  const [handStatus, setHandStatus] = useState<DetectorStatus>("idle");
  const [poseEnabled, setPoseEnabled] = useState(!IS_MOBILE);
  const [poseStatus, setPoseStatus] = useState<DetectorStatus>("idle");
  const [skinEnabled, setSkinEnabled] = useState(false);
  const [avatarEnabled, setAvatarEnabled] = useState(true);
  const [avatarStyle, setAvatarStyle] = useState<"robot" | "egg">("robot");

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const landmarkerRef = useRef<any>(null);
  const handLandmarkerRef = useRef<any>(null);
  const poseLandmarkerRef = useRef<any>(null);
  const lastFrameRef = useRef<Float32Array | null>(null);
  const missCountRef = useRef(0);
  const handMissCountRef = useRef(0);
  const poseMissCountRef = useRef(0);
  const handDelegateRef = useRef<"GPU" | "CPU">("GPU");
  const poseDelegateRef = useRef<"GPU" | "CPU">("GPU");
  const lastFaceInferAtRef = useRef(0);
  const lastFaceAtRef = useRef(0);
  const lastFacePointsRef = useRef<Array<{ x: number; y: number; z: number }> | null>(null);
  const lastHandInferAtRef = useRef(0);
  const lastPoseInferAtRef = useRef(0);
  const lastHandsRef = useRef<Array<Array<{ x: number; y: number }>> | null>(null);
  const lastPoseRef = useRef<Array<{ x: number; y: number; visibility?: number }> | null>(null);
  const lastHandsAtRef = useRef(0);
  const lastPoseAtRef = useRef(0);

  const createFaceLandmarker = useCallback(async (delegate: "GPU" | "CPU") => {
    const vision = await import("@mediapipe/tasks-vision");
    const { FilesetResolver, FaceLandmarker } = vision as any;
    const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
    const fileset = await FilesetResolver.forVisionTasks(
      `${assetBase}/wasm`
    );
    return FaceLandmarker.createFromOptions(fileset, {
      baseOptions: {
        modelAssetPath: `${assetBase}/face_landmarker.task`,
        delegate,
      },
      runningMode: "VIDEO",
      numFaces: 1,
      minFaceDetectionConfidence: 0.15,
      minFacePresenceConfidence: 0.4,
      minTrackingConfidence: 0.4,
      outputFaceBlendshapes: false,
      outputFacialTransformationMatrixes: false,
    });
  }, []);

  const createHandLandmarker = useCallback(async (delegate: "GPU" | "CPU") => {
    const vision = await import("@mediapipe/tasks-vision");
    const { FilesetResolver, HandLandmarker } = vision as any;
    const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
    const fileset = await FilesetResolver.forVisionTasks(
      `${assetBase}/wasm`
    );
    return HandLandmarker.createFromOptions(fileset, {
      baseOptions: {
        modelAssetPath: `${assetBase}/hand_landmarker.task`,
        delegate,
      },
      runningMode: "VIDEO",
      numHands: 2,
      minHandDetectionConfidence: 0.3,
      minHandPresenceConfidence: 0.3,
      minTrackingConfidence: 0.3,
    });
  }, []);

  const createPoseLandmarker = useCallback(async (delegate: "GPU" | "CPU") => {
    const vision = await import("@mediapipe/tasks-vision");
    const { FilesetResolver, PoseLandmarker } = vision as any;
    const assetBase = `${import.meta.env.BASE_URL}mediapipe`;
    const fileset = await FilesetResolver.forVisionTasks(
      `${assetBase}/wasm`
    );
    return PoseLandmarker.createFromOptions(fileset, {
      baseOptions: {
        modelAssetPath: `${assetBase}/pose_landmarker_lite.task`,
        delegate,
      },
      runningMode: "VIDEO",
      numPoses: 1,
      minPoseDetectionConfidence: 0.3,
      minPosePresenceConfidence: 0.3,
      minTrackingConfidence: 0.3,
    });
  }, []);

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
        const landmarker = await createFaceLandmarker(delegate);
        if (!cancelled) {
          landmarkerRef.current = landmarker;
          setMeshStatus("ready");
          missCountRef.current = 0;
        }
      } catch (e) {
        if (!cancelled && delegate === "GPU") {
          try {
            await load("CPU");
            return;
          } catch {
            // Fall through to error state below.
          }
        }
        if (!cancelled) {
          setMeshStatus("error");
          console.error("Face mesh load failed", e);
        }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [meshEnabled, createFaceLandmarker]);

  // ── Lazy-load MediaPipe hand landmarker ──
  useEffect(() => {
    if (!handEnabled) {
      setHandStatus("idle");
      return;
    }
    if (handLandmarkerRef.current) {
      setHandStatus("ready");
      return;
    }
    let cancelled = false;

    const load = async (delegate: "GPU" | "CPU" = "GPU") => {
      try {
        setHandStatus("loading");
        const landmarker = await createHandLandmarker(delegate);
        if (!cancelled) {
          handLandmarkerRef.current = landmarker;
          handDelegateRef.current = delegate;
          handMissCountRef.current = 0;
          setHandStatus("ready");
        }
      } catch (e) {
        if (!cancelled && delegate === "GPU") {
          try {
            await load("CPU");
            return;
          } catch {
            // Fall through to error state below.
          }
        }
        if (!cancelled) {
          setHandStatus("error");
          console.error("Hand landmarker load failed", e);
        }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [handEnabled, createHandLandmarker]);

  // ── Lazy-load MediaPipe pose landmarker ──
  useEffect(() => {
    if (!poseEnabled) {
      setPoseStatus("idle");
      return;
    }
    if (poseLandmarkerRef.current) {
      setPoseStatus("ready");
      return;
    }
    let cancelled = false;

    const load = async (delegate: "GPU" | "CPU" = "GPU") => {
      try {
        setPoseStatus("loading");
        const landmarker = await createPoseLandmarker(delegate);
        if (!cancelled) {
          poseLandmarkerRef.current = landmarker;
          poseDelegateRef.current = delegate;
          poseMissCountRef.current = 0;
          setPoseStatus("ready");
        }
      } catch (e) {
        if (!cancelled && delegate === "GPU") {
          try {
            await load("CPU");
            return;
          } catch {
            // Fall through to error state below.
          }
        }
        if (!cancelled) {
          setPoseStatus("error");
          console.error("Pose landmarker load failed", e);
        }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [poseEnabled, createPoseLandmarker]);

  // ── Drawing helpers ──
  const drawFace = (
    ctx: CanvasRenderingContext2D,
    pts: Array<{ x: number; y: number }>,
    cw: number,
    ch: number,
    withSkin: boolean
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

    if (withSkin) {
      ctx.save();
      ctx.beginPath();
      FACE_OUTLINE.forEach((idx, i) => {
        const p = pts[idx];
        if (!p) return;
        const x = p.x * cw;
        const y = p.y * ch;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
      const skinGradient = ctx.createLinearGradient(0, 0, 0, ch);
      skinGradient.addColorStop(0, "rgba(251,191,130,0.24)");
      skinGradient.addColorStop(1, "rgba(236,152,92,0.2)");
      ctx.fillStyle = skinGradient;
      ctx.fill();
      ctx.strokeStyle = "rgba(255,220,183,0.35)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
    }

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
    ch: number,
    withSkin: boolean
  ) => {
    const VIS_THRESH = 0.35;

    if (withSkin) {
      const torsoVisible = POSE_TORSO.every((idx) => {
        const p = pts[idx];
        return Boolean(p) && (p!.visibility ?? 1) >= VIS_THRESH;
      });
      if (torsoVisible) {
        ctx.save();
        const ls = pts[11]!;
        const rs = pts[12]!;
        const rh = pts[24]!;
        const lh = pts[23]!;
        ctx.beginPath();
        ctx.moveTo(ls.x * cw, ls.y * ch);
        ctx.lineTo(rs.x * cw, rs.y * ch);
        ctx.lineTo(rh.x * cw, rh.y * ch);
        ctx.lineTo(lh.x * cw, lh.y * ch);
        ctx.closePath();
        const torsoGradient = ctx.createLinearGradient(0, ls.y * ch, 0, lh.y * ch);
        torsoGradient.addColorStop(0, "rgba(251,191,130,0.22)");
        torsoGradient.addColorStop(1, "rgba(215,132,80,0.2)");
        ctx.fillStyle = torsoGradient;
        ctx.fill();
        ctx.restore();
      }

      ctx.save();
      ctx.lineCap = "round";
      ctx.strokeStyle = "rgba(246,170,114,0.25)";
      for (const [a, b, width] of POSE_SKIN_BONES) {
        const pa = pts[a];
        const pb = pts[b];
        if (!pa || !pb) continue;
        if ((pa.visibility ?? 1) < VIS_THRESH || (pb.visibility ?? 1) < VIS_THRESH) continue;
        ctx.lineWidth = width;
        ctx.beginPath();
        ctx.moveTo(pa.x * cw, pa.y * ch);
        ctx.lineTo(pb.x * cw, pb.y * ch);
        ctx.stroke();
      }
      ctx.restore();
    }

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

  const drawRobotCharacter = (
    ctx: CanvasRenderingContext2D,
    facePts: Array<{ x: number; y: number; z: number }> | null,
    posePts: Array<{ x: number; y: number; visibility?: number }> | null,
    cw: number,
    ch: number
  ) => {
    const VIS_THRESH = 0.35;
    const poseOk = (idx: number) => {
      const p = posePts?.[idx];
      if (!p) return false;
      return (p.visibility ?? 1) >= VIS_THRESH;
    };
    const roundRect = (x: number, y: number, w: number, h: number, r: number) => {
      const rr = Math.max(0, Math.min(r, Math.min(w, h) * 0.5));
      ctx.beginPath();
      ctx.moveTo(x + rr, y);
      ctx.lineTo(x + w - rr, y);
      ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
      ctx.lineTo(x + w, y + h - rr);
      ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
      ctx.lineTo(x + rr, y + h);
      ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
      ctx.lineTo(x, y + rr);
      ctx.quadraticCurveTo(x, y, x + rr, y);
      ctx.closePath();
    };

    let neckX = cw * 0.5;
    let neckY = ch * 0.45;
    let bodyBottomY = ch * 0.72;

    // Robot body frame.
    if (posePts && poseOk(11) && poseOk(12) && poseOk(23) && poseOk(24)) {
      const ls = posePts[11];
      const rs = posePts[12];
      const lh = posePts[23];
      const rh = posePts[24];
      neckX = ((ls.x + rs.x) * 0.5) * cw;
      neckY = ((ls.y + rs.y) * 0.5) * ch;
      bodyBottomY = ((lh.y + rh.y) * 0.5) * ch;

      const torsoX = Math.min(ls.x, rs.x) * cw - 12;
      const torsoY = Math.min(ls.y, rs.y) * ch - 2;
      const torsoW = Math.abs(rs.x - ls.x) * cw + 24;
      const torsoH = Math.max(44, bodyBottomY - torsoY + 6);

      ctx.save();
      roundRect(torsoX, torsoY, torsoW, torsoH, 14);
      const torsoGrad = ctx.createLinearGradient(torsoX, torsoY, torsoX + torsoW, torsoY + torsoH);
      torsoGrad.addColorStop(0, "rgba(148,163,184,0.62)");
      torsoGrad.addColorStop(1, "rgba(71,85,105,0.56)");
      ctx.fillStyle = torsoGrad;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(226,232,240,0.62)";
      ctx.stroke();

      // chest panel
      roundRect(torsoX + torsoW * 0.18, torsoY + torsoH * 0.2, torsoW * 0.64, torsoH * 0.44, 8);
      ctx.fillStyle = "rgba(15,23,42,0.52)";
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "rgba(148,163,184,0.55)";
      ctx.stroke();
      ctx.restore();

      // limbs
      ctx.save();
      ctx.lineCap = "round";
      const drawLimb = (a: number, b: number, width: number) => {
        if (!poseOk(a) || !poseOk(b)) return;
        const pa = posePts[a];
        const pb = posePts[b];
        ctx.beginPath();
        ctx.moveTo(pa.x * cw, pa.y * ch);
        ctx.lineTo(pb.x * cw, pb.y * ch);
        ctx.lineWidth = width;
        ctx.strokeStyle = "rgba(148,163,184,0.55)";
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(pa.x * cw, pa.y * ch, Math.max(3, width * 0.36), 0, Math.PI * 2);
        ctx.arc(pb.x * cw, pb.y * ch, Math.max(3, width * 0.36), 0, Math.PI * 2);
        ctx.fillStyle = "rgba(203,213,225,0.6)";
        ctx.fill();
      };

      drawLimb(11, 13, 13);
      drawLimb(13, 15, 11);
      drawLimb(12, 14, 13);
      drawLimb(14, 16, 11);
      drawLimb(23, 25, 15);
      drawLimb(25, 27, 13);
      drawLimb(24, 26, 15);
      drawLimb(26, 28, 13);
      ctx.restore();
    }

    // Robot head anchor from face if available.
    let headCx = neckX;
    let headCy = neckY - 48;
    let headW = 84;
    let headH = 96;
    if (facePts) {
      const left = facePts[234];
      const right = facePts[454];
      const top = facePts[10];
      const chin = facePts[152];
      if (left && right && top && chin) {
        headCx = ((left.x + right.x) * 0.5) * cw;
        headCy = ((top.y + chin.y) * 0.5) * ch;
        headW = Math.max(64, Math.abs(right.x - left.x) * cw * 1.15);
        headH = Math.max(72, Math.abs(chin.y - top.y) * ch * 1.25);
      }
    }

    const hx = headCx - headW * 0.5;
    const hy = headCy - headH * 0.52;

    ctx.save();
    roundRect(hx, hy, headW, headH, 16);
    const headGrad = ctx.createLinearGradient(hx, hy, hx + headW, hy + headH);
    headGrad.addColorStop(0, "rgba(203,213,225,0.72)");
    headGrad.addColorStop(1, "rgba(100,116,139,0.64)");
    ctx.fillStyle = headGrad;
    ctx.fill();
    ctx.lineWidth = 2.4;
    ctx.strokeStyle = "rgba(226,232,240,0.68)";
    ctx.stroke();

    // Antenna
    ctx.beginPath();
    ctx.moveTo(headCx, hy);
    ctx.lineTo(headCx, hy - 16);
    ctx.lineWidth = 2;
    ctx.strokeStyle = "rgba(148,163,184,0.75)";
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(headCx, hy - 19, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(34,211,238,0.9)";
    ctx.fill();

    // Visor
    const visorX = hx + headW * 0.14;
    const visorY = hy + headH * 0.24;
    const visorW = headW * 0.72;
    const visorH = headH * 0.26;
    roundRect(visorX, visorY, visorW, visorH, 9);
    const visorGrad = ctx.createLinearGradient(visorX, visorY, visorX + visorW, visorY + visorH);
    visorGrad.addColorStop(0, "rgba(8,145,178,0.8)");
    visorGrad.addColorStop(1, "rgba(14,116,144,0.75)");
    ctx.fillStyle = visorGrad;
    ctx.fill();
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = "rgba(125,211,252,0.8)";
    ctx.stroke();

    // Eyes (LED)
    const eyeY = visorY + visorH * 0.5;
    const eyeDX = visorW * 0.22;
    for (const dir of [-1, 1]) {
      const ex = headCx + dir * eyeDX;
      ctx.beginPath();
      ctx.arc(ex, eyeY, Math.max(4, headW * 0.05), 0, Math.PI * 2);
      ctx.fillStyle = "rgba(56,189,248,0.95)";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(ex, eyeY, Math.max(7, headW * 0.09), 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(56,189,248,0.35)";
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // Mouth grille
    const grillX = hx + headW * 0.2;
    const grillY = hy + headH * 0.66;
    const grillW = headW * 0.6;
    const grillH = headH * 0.14;
    roundRect(grillX, grillY, grillW, grillH, 6);
    ctx.fillStyle = "rgba(15,23,42,0.5)";
    ctx.fill();
    ctx.strokeStyle = "rgba(148,163,184,0.55)";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.strokeStyle = "rgba(148,163,184,0.42)";
    for (let i = 1; i < 5; i++) {
      const x = grillX + (grillW / 5) * i;
      ctx.beginPath();
      ctx.moveTo(x, grillY + 2);
      ctx.lineTo(x, grillY + grillH - 2);
      ctx.stroke();
    }

    // neck joint
    ctx.beginPath();
    ctx.arc(neckX, neckY + 2, 7, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(148,163,184,0.62)";
    ctx.fill();
    ctx.strokeStyle = "rgba(226,232,240,0.58)";
    ctx.stroke();
    ctx.restore();
  };

  const drawEggCharacter = (
    ctx: CanvasRenderingContext2D,
    facePts: Array<{ x: number; y: number; z: number }> | null,
    posePts: Array<{ x: number; y: number; visibility?: number }> | null,
    cw: number,
    ch: number
  ) => {
    const VIS_THRESH = 0.35;
    const poseOk = (idx: number) => {
      const p = posePts?.[idx];
      if (!p) return false;
      return (p.visibility ?? 1) >= VIS_THRESH;
    };

    let cx = cw * 0.5;
    let cy = ch * 0.45;
    let eggW = 110;
    let eggH = 148;

    if (facePts) {
      const left = facePts[234];
      const right = facePts[454];
      const top = facePts[10];
      const chin = facePts[152];
      if (left && right && top && chin) {
        cx = ((left.x + right.x) * 0.5) * cw;
        cy = ((top.y + chin.y) * 0.5) * ch + 8;
        const fw = Math.abs(right.x - left.x) * cw;
        const fh = Math.abs(chin.y - top.y) * ch;
        eggW = Math.max(90, fw * 1.45);
        eggH = Math.max(120, fh * 1.85);
      }
    }

    if (posePts && poseOk(11) && poseOk(12) && poseOk(23) && poseOk(24)) {
      const shoulderY = ((posePts[11].y + posePts[12].y) * 0.5) * ch;
      const hipY = ((posePts[23].y + posePts[24].y) * 0.5) * ch;
      cy = Math.min(cy, shoulderY + (hipY - shoulderY) * 0.45);
      eggH = Math.max(eggH, (hipY - shoulderY) * 1.55);
      eggW = Math.max(eggW, eggH * 0.7);
    }

    // Egg body.
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(cx, cy + eggH * 0.04, eggW * 0.48, eggH * 0.54, 0, 0, Math.PI * 2);
    const shell = ctx.createLinearGradient(cx, cy - eggH * 0.5, cx, cy + eggH * 0.65);
    shell.addColorStop(0, "rgba(255,248,235,0.82)");
    shell.addColorStop(0.55, "rgba(254,240,216,0.76)");
    shell.addColorStop(1, "rgba(252,224,180,0.68)");
    ctx.fillStyle = shell;
    ctx.fill();
    ctx.lineWidth = 2.2;
    ctx.strokeStyle = "rgba(255,255,255,0.62)";
    ctx.stroke();

    // Small crack / shadow detail.
    ctx.beginPath();
    ctx.moveTo(cx - eggW * 0.12, cy + eggH * 0.12);
    ctx.lineTo(cx - eggW * 0.04, cy + eggH * 0.2);
    ctx.lineTo(cx + eggW * 0.03, cy + eggH * 0.14);
    ctx.lineTo(cx + eggW * 0.1, cy + eggH * 0.22);
    ctx.strokeStyle = "rgba(180,120,80,0.3)";
    ctx.lineWidth = 1.8;
    ctx.stroke();

    // Face.
    let eyeLX = cx - eggW * 0.14;
    let eyeRX = cx + eggW * 0.14;
    let eyeY = cy - eggH * 0.1;
    let mouthY = cy + eggH * 0.08;
    if (facePts) {
      const eyeL = facePts[33];
      const eyeR = facePts[263];
      const mouthL = facePts[61];
      const mouthR = facePts[291];
      const nose = facePts[1];
      if (eyeL && eyeR) {
        eyeLX = eyeL.x * cw;
        eyeRX = eyeR.x * cw;
        eyeY = ((eyeL.y + eyeR.y) * 0.5) * ch;
      }
      if (mouthL && mouthR) {
        mouthY = ((mouthL.y + mouthR.y) * 0.5) * ch + 6;
      } else if (nose) {
        mouthY = nose.y * ch + eggH * 0.13;
      }
    }

    ctx.fillStyle = "rgba(15,23,42,0.82)";
    ctx.beginPath();
    ctx.ellipse(eyeLX, eyeY, Math.max(4, eggW * 0.045), Math.max(6, eggH * 0.05), 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(eyeRX, eyeY, Math.max(4, eggW * 0.045), Math.max(6, eggH * 0.05), 0, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "rgba(255,255,255,0.8)";
    ctx.beginPath();
    ctx.arc(eyeLX + 1.2, eyeY - 1.8, 1.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(eyeRX + 1.2, eyeY - 1.8, 1.4, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = "rgba(190,24,93,0.62)";
    ctx.lineWidth = 2.3;
    ctx.beginPath();
    ctx.moveTo(cx - eggW * 0.11, mouthY);
    ctx.quadraticCurveTo(cx, mouthY + eggH * 0.08, cx + eggW * 0.11, mouthY);
    ctx.stroke();

    // tiny feet
    ctx.fillStyle = "rgba(226,232,240,0.52)";
    ctx.beginPath();
    ctx.ellipse(cx - eggW * 0.11, cy + eggH * 0.52, eggW * 0.08, eggH * 0.03, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(cx + eggW * 0.11, cy + eggH * 0.52, eggW * 0.08, eggH * 0.03, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  };

  // ── Tracking loop ──
  useEffect(() => {
    if (!cameraReady || !videoRef.current) return;
    const shouldTrack = camFollow || meshEnabled || handEnabled || poseEnabled;
    if (!shouldTrack) return;
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
    const FACE_INTERVAL_MS = 30; // ~33 FPS
    const HAND_INTERVAL_MS = 36; // ~28 FPS
    const POSE_INTERVAL_MS = 54; // ~18 FPS
    const DRAW_HOLD_MS = 220;

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
        if (nowMs - lastFaceInferAtRef.current >= FACE_INTERVAL_MS) {
          try {
            lastFaceInferAtRef.current = nowMs;
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
              if (camFollow) {
                setCamVec((prev) => ({
                  x: clamp(prev.x * 0.15 + yaw * 0.85, -1, 1),
                  y: clamp(prev.y * 0.15 + pitch * 0.85, -1, 1),
                }));
              }

              lastFacePointsRef.current = pts;
              lastFaceAtRef.current = nowMs;
              faceDetected = true;
              missCountRef.current = 0;
            } else {
              missCountRef.current += 1;
            }
          } catch {
            // fall through
          }
        }
        if (
          ctxOv &&
          lastFacePointsRef.current &&
          nowMs - lastFaceAtRef.current <= DRAW_HOLD_MS
        ) {
          drawFace(ctxOv, lastFacePointsRef.current, vw, vh, skinEnabled && !avatarEnabled);
          faceDetected = true;
        }
      }

      // ── MediaPipe hand landmarker ──
      if (handEnabled && handStatus === "ready" && handLandmarkerRef.current) {
        if (nowMs - lastHandInferAtRef.current >= HAND_INTERVAL_MS) {
          try {
            lastHandInferAtRef.current = nowMs;
            const result = handLandmarkerRef.current.detectForVideo(video, nowMs);
            if (result?.landmarks?.length) {
              const allHands = (result.landmarks as Array<Array<{ x: number; y: number; z: number }>>).map(
                (hand) => mirrorEnabled
                  ? hand.map((p) => ({ x: 1 - p.x, y: p.y }))
                  : hand.map((p) => ({ x: p.x, y: p.y }))
              );
              lastHandsRef.current = allHands;
              lastHandsAtRef.current = nowMs;
              handMissCountRef.current = 0;
            } else {
              handMissCountRef.current += 1;
            }
          } catch {
            // ignore
          }
        }
        if (
          ctxOv &&
          lastHandsRef.current &&
          nowMs - lastHandsAtRef.current <= DRAW_HOLD_MS
        ) {
          drawHands(ctxOv, lastHandsRef.current, vw, vh);
        }
      }

      // ── MediaPipe pose landmarker ──
      if (poseEnabled && poseStatus === "ready" && poseLandmarkerRef.current) {
        if (nowMs - lastPoseInferAtRef.current >= POSE_INTERVAL_MS) {
          try {
            lastPoseInferAtRef.current = nowMs;
            const result = poseLandmarkerRef.current.detectForVideo(video, nowMs);
            if (result?.landmarks?.length) {
              const ptsRaw = result.landmarks[0] as Array<{
                x: number; y: number; z: number; visibility?: number;
              }>;
              const pts = mirrorEnabled
                ? ptsRaw.map((p) => ({ x: 1 - p.x, y: p.y, visibility: p.visibility }))
                : ptsRaw.map((p) => ({ x: p.x, y: p.y, visibility: p.visibility }));
              lastPoseRef.current = pts;
              lastPoseAtRef.current = nowMs;
              poseMissCountRef.current = 0;
            } else {
              poseMissCountRef.current += 1;
            }
          } catch {
            // ignore
          }
        }
        if (
          ctxOv &&
          lastPoseRef.current &&
          nowMs - lastPoseAtRef.current <= DRAW_HOLD_MS
        ) {
          drawPose(ctxOv, lastPoseRef.current, vw, vh, skinEnabled && !avatarEnabled);
        }
      }

      if (ctxOv && avatarEnabled) {
        const faceForAvatar =
          lastFacePointsRef.current && nowMs - lastFaceAtRef.current <= DRAW_HOLD_MS
            ? lastFacePointsRef.current
            : null;
        const poseForAvatar =
          lastPoseRef.current && nowMs - lastPoseAtRef.current <= DRAW_HOLD_MS
            ? lastPoseRef.current
            : null;
        if (faceForAvatar || poseForAvatar) {
          if (avatarStyle === "robot") {
            drawRobotCharacter(ctxOv, faceForAvatar, poseForAvatar, vw, vh);
          } else {
            drawEggCharacter(ctxOv, faceForAvatar, poseForAvatar, vw, vh);
          }
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
              if (camFollow) {
                setCamVec((prev) => ({
                  x: clamp(prev.x * 0.6 + nx * 0.4, -1, 1),
                  y: clamp(prev.y * 0.6 + ny * 0.4, -1, 1),
                }));
              }
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
              if (camFollow) {
                setCamVec((prevVec) => ({
                  x: clamp(prevVec.x * 0.7 + nx * 0.3, -1, 1),
                  y: clamp(prevVec.y * 0.7 + ny * 0.3, -1, 1),
                }));
              }
            }
          }
          lastFrameRef.current = gray;
        }
      }

      requestAnimationFrame(loop);
    };

    requestAnimationFrame(loop);
    return () => { stopped = true; };
  }, [
    camFollow,
    cameraReady,
    meshEnabled,
    meshStatus,
    mirrorEnabled,
    handEnabled,
    handStatus,
    poseEnabled,
    poseStatus,
    skinEnabled,
    avatarEnabled,
    avatarStyle,
    createHandLandmarker,
    createPoseLandmarker,
  ]);

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
    skinEnabled,
    setSkinEnabled,
    avatarEnabled,
    setAvatarEnabled,
    avatarStyle,
    setAvatarStyle,
    videoRef,
    overlayRef,
  };
}
