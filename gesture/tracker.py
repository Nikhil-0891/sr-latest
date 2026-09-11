"""
tracker.py
==========
Stage 1: Long-range full-body localization (YOLOv8-Pose) on the whole frame.
Stage 2: Dynamic ROI crop around each wrist -> digital zoom -> MediaPipe Hands
         for high-precision 21-point 3D hand landmarks even at long distance.

Also provides:
  - EMA + Kalman temporal smoothing of hand-center trajectories.
  - Optical-flow + bbox-scale based depth (forward/backward) approximation.

All public outputs are packaged as `HandTrack` dataclasses consumed by
gesture_classifier.py.

Author: Principal CV/Robotics Engineering (reference implementation)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
from filterpy.kalman import KalmanFilter
from loguru import logger

try:
    import mediapipe as mp
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "mediapipe is required. Install with `pip install mediapipe`."
    ) from e

try:
    from ultralytics import YOLO
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "ultralytics is required for YOLOv8-Pose. `pip install ultralytics`."
    ) from e


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class HandTrack:
    """Per-hand tracking result for a single frame, in smoothed coordinates."""
    hand_id: str                                  # "left" / "right"
    present: bool = False
    center_px: Tuple[float, float] = (0.0, 0.0)    # smoothed wrist/palm center (image px)
    velocity_px: Tuple[float, float] = (0.0, 0.0)  # px/frame, smoothed
    landmarks: Optional[np.ndarray] = None         # (21, 3) normalized xyz within ROI
    landmarks_img: Optional[np.ndarray] = None     # (21, 2) mapped back to full-frame px
    bbox_scale: float = 0.0                        # proxy for "hand size" -> depth cue
    depth_velocity: float = 0.0                    # + = approaching camera, - = receding
    confidence: float = 0.0
    roi: Optional[Tuple[int, int, int, int]] = None  # (x1, y1, x2, y2) crop used


@dataclass
class FrameTrackResult:
    """All tracking output for one processed frame."""
    hands: Dict[str, HandTrack] = field(default_factory=dict)
    person_detected: bool = False
    person_bbox: Optional[Tuple[int, int, int, int]] = None
    stage1_ms: float = 0.0
    stage2_ms: float = 0.0


# --------------------------------------------------------------------------- #
# Smoothing primitives
# --------------------------------------------------------------------------- #

class EMASmoother:
    """Simple, O(1), low-latency exponential moving average smoother.

    Used for high-frequency landmark points where a full Kalman filter would
    be overkill (21 points x 2 hands x every frame).
    """

    def __init__(self, alpha: float = 0.55):
        # alpha closer to 1.0 -> trust new measurement more (less lag, more jitter)
        # alpha closer to 0.0 -> heavier smoothing (more lag, less jitter)
        self.alpha = alpha
        self._state: Optional[np.ndarray] = None

    def update(self, value: np.ndarray) -> np.ndarray:
        if self._state is None:
            self._state = value.astype(np.float32).copy()
        else:
            self._state = self.alpha * value + (1 - self.alpha) * self._state
        return self._state

    def reset(self):
        self._state = None


class Point2DKalman:
    """Constant-velocity Kalman filter for a single 2D point (e.g. palm center).

    State: [x, y, vx, vy]. Chosen over EMA for the *primary* control point
    because it gives us a principled velocity estimate (used directly for
    directional gesture classification) with noise-optimal jitter rejection.
    """

    def __init__(self, process_noise: float = 1e-2, measurement_noise: float = 1e-1):
        self.kf = KalmanFilter(dim_x=4, dim_z=2)
        dt = 1.0  # frame-indexed; real dt scaling applied by caller if needed
        self.kf.F = np.array([[1, 0, dt, 0],
                               [0, 1, 0, dt],
                               [0, 0, 1, 0],
                               [0, 0, 0, 1]], dtype=np.float32)
        self.kf.H = np.array([[1, 0, 0, 0],
                               [0, 1, 0, 0]], dtype=np.float32)
        self.kf.P *= 50.0
        self.kf.R *= measurement_noise
        self.kf.Q *= process_noise
        self._initialized = False

    def update(self, measurement: Tuple[float, float]) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        z = np.array(measurement, dtype=np.float32)
        if not self._initialized:
            self.kf.x = np.array([z[0], z[1], 0, 0], dtype=np.float32)
            self._initialized = True
        else:
            self.kf.predict()
            self.kf.update(z)
        x = self.kf.x
        return (float(x[0]), float(x[1])), (float(x[2]), float(x[3]))

    def reset(self):
        self._initialized = False


# --------------------------------------------------------------------------- #
# Main tracker
# --------------------------------------------------------------------------- #

class LongRangeHandTracker:
    """Two-stage tracker: YOLOv8-Pose (full frame) -> MediaPipe Hands (ROI crop)."""

    # Wrist ROI is scaled relative to shoulder width (a stable body-scale proxy)
    ROI_SCALE_FACTOR = 2.2
    ROI_MIN_PX = 120
    ROI_TARGET_PX = 256  # digital-zoom target resolution fed to MediaPipe Hands

    # COCO keypoint indices used by YOLOv8-Pose
    KPT_L_SHOULDER, KPT_R_SHOULDER = 5, 6
    KPT_L_WRIST, KPT_R_WRIST = 9, 10

    def __init__(
        self,
        pose_model_path: str = "yolov8n-pose.pt",
        pose_conf: float = 0.35,
        hand_min_detection_conf: float = 0.5,
        hand_min_tracking_conf: float = 0.5,
        ema_alpha: float = 0.55,
        device: str = "cpu",
    ):
        logger.info(f"Loading YOLOv8-Pose model: {pose_model_path} (device={device})")
        self.pose_model = YOLO(pose_model_path)
        self.pose_conf = pose_conf
        self.device = device

        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,           # one detector instance per hand ROI
            min_detection_confidence=hand_min_detection_conf,
            min_tracking_confidence=hand_min_tracking_conf,
            model_complexity=1,
        )

        # Per-hand smoothing / depth state, keyed by "left"/"right"
        self._center_kf: Dict[str, Point2DKalman] = {
            "left": Point2DKalman(), "right": Point2DKalman()
        }
        self._landmark_ema: Dict[str, EMASmoother] = {
            "left": EMASmoother(ema_alpha), "right": EMASmoother(ema_alpha)
        }
        self._scale_history: Dict[str, Deque[float]] = {
            "left": deque(maxlen=8), "right": deque(maxlen=8)
        }
        self._prev_gray_roi: Dict[str, Optional[np.ndarray]] = {"left": None, "right": None}

    # ------------------------------------------------------------------ #
    def process(self, frame_bgr: np.ndarray) -> FrameTrackResult:
        """Run the full two-stage pipeline on a single BGR frame."""
        result = FrameTrackResult()
        h, w = frame_bgr.shape[:2]

        # ---------------- Stage 1: full-frame body localization ----------------
        t0 = time.perf_counter()
        yolo_out = self.pose_model.predict(
            frame_bgr, conf=self.pose_conf, verbose=False, device=self.device
        )[0]
        result.stage1_ms = (time.perf_counter() - t0) * 1000.0

        # YOLO can occasionally return an empty keypoint tensor even when the
        # result object itself exists. Treat that frame as "no person detected"
        # instead of indexing into an empty array.
        if yolo_out.keypoints is None or len(yolo_out.keypoints) == 0:
            result.hands = {"left": HandTrack("left"), "right": HandTrack("right")}
            return result

        keypoint_xy = yolo_out.keypoints.xy

        if keypoint_xy is None:
            result.hands = {"left": HandTrack("left"), "right": HandTrack("right")}
            return result

        kpts_all = keypoint_xy.cpu().numpy()

        # Expected shape is (num_people, 17, 2). Guard against malformed or
        # temporarily empty model output before selecting person 0.
        if (
            kpts_all.ndim != 3
            or kpts_all.shape[0] == 0
            or kpts_all.shape[1] < 17
            or kpts_all.shape[2] < 2
        ):
            result.hands = {"left": HandTrack("left"), "right": HandTrack("right")}
            return result

        # Take the highest-confidence detected person (single-operator assumption;
        # extend to multi-person by iterating all boxes for a multi-operator system).
        kpts = kpts_all[0, :, :2]      # (17, 2)

        kconf_tensor = yolo_out.keypoints.conf
        if kconf_tensor is None:
            kconf = np.ones(17, dtype=np.float32)
        else:
            kconf_all = kconf_tensor.cpu().numpy()

            if (
                kconf_all.ndim != 2
                or kconf_all.shape[0] == 0
                or kconf_all.shape[1] < 17
            ):
                kconf = np.ones(17, dtype=np.float32)
            else:
                kconf = kconf_all[0, :17]

        boxes = yolo_out.boxes
        if boxes is not None and len(boxes) > 0:
            result.person_detected = True
            xyxy = boxes.xyxy[0].cpu().numpy()
            result.person_bbox = tuple(int(v) for v in xyxy)

        shoulder_l = kpts[self.KPT_L_SHOULDER]
        shoulder_r = kpts[self.KPT_R_SHOULDER]
        shoulder_dist = float(np.linalg.norm(shoulder_l - shoulder_r))
        shoulder_dist = max(shoulder_dist, 40.0)  # floor to avoid degenerate tiny ROI

        t1 = time.perf_counter()
        for side, wrist_idx in ((("left", self.KPT_L_WRIST)), ("right", self.KPT_R_WRIST)):
            wrist_xy = kpts[wrist_idx]
            wrist_conf = float(kconf[wrist_idx])
            if wrist_conf < 0.25 or np.allclose(wrist_xy, 0.0):
                result.hands[side] = HandTrack(side, present=False)
                continue

            track = self._process_hand_roi(frame_bgr, side, wrist_xy, shoulder_dist, w, h)
            result.hands[side] = track

        result.stage2_ms = (time.perf_counter() - t1) * 1000.0
        return result

    # ------------------------------------------------------------------ #
    def _process_hand_roi(
        self,
        frame: np.ndarray,
        side: str,
        wrist_xy: np.ndarray,
        shoulder_dist: float,
        frame_w: int,
        frame_h: int,
    ) -> HandTrack:
        """Crop a dynamic ROI around the wrist, digitally zoom it, run MediaPipe Hands."""
        roi_size = int(max(self.ROI_MIN_PX, shoulder_dist * self.ROI_SCALE_FACTOR))
        cx, cy = int(wrist_xy[0]), int(wrist_xy[1])

        x1 = max(0, cx - roi_size // 2)
        y1 = max(0, cy - roi_size // 2)
        x2 = min(frame_w, cx + roi_size // 2)
        y2 = min(frame_h, cy + roi_size // 2)
        if x2 - x1 < 10 or y2 - y1 < 10:
            return HandTrack(side, present=False)

        roi = frame[y1:y2, x1:x2]
        # Digital zoom: upscale small ROI to a fixed resolution so MediaPipe Hands
        # effectively "sees" the hand at close range regardless of true distance.
        roi_resized = cv2.resize(roi, (self.ROI_TARGET_PX, self.ROI_TARGET_PX),
                                  interpolation=cv2.INTER_CUBIC)
        roi_rgb = cv2.cvtColor(roi_resized, cv2.COLOR_BGR2RGB)

        mp_result = self.hands.process(roi_rgb)
        if not mp_result.multi_hand_landmarks:
            self._prev_gray_roi[side] = None
            return HandTrack(side, present=False, roi=(x1, y1, x2, y2))

        lm = mp_result.multi_hand_landmarks[0]
        raw_lms = np.array([[p.x, p.y, p.z] for p in lm.landmark], dtype=np.float32)  # (21,3)

        # Smooth landmarks (shape-preserving EMA) for stable static-pose classification.
        smoothed_lms = self._landmark_ema[side].update(raw_lms)

        # Map normalized ROI landmarks back into full-frame pixel coordinates.
        scale_x = (x2 - x1) / self.ROI_TARGET_PX
        scale_y = (y2 - y1) / self.ROI_TARGET_PX
        lms_img = smoothed_lms[:, :2].copy()
        lms_img[:, 0] = lms_img[:, 0] * self.ROI_TARGET_PX * scale_x + x1
        lms_img[:, 1] = lms_img[:, 1] * self.ROI_TARGET_PX * scale_y + y1

        # Palm center = mean of wrist(0) + index_mcp(5) + pinky_mcp(17) -> stable centroid.
        palm_center = lms_img[[0, 5, 17]].mean(axis=0)
        smoothed_center, velocity = self._center_kf[side].update(tuple(palm_center))

        # --- Depth approximation signal #1: bounding-box scale of the hand ---
        hand_bbox_w = float(lms_img[:, 0].max() - lms_img[:, 0].min())
        hand_bbox_h = float(lms_img[:, 1].max() - lms_img[:, 1].min())
        bbox_scale = (hand_bbox_w + hand_bbox_h) / 2.0
        hist = self._scale_history[side]
        prev_scale = hist[-1] if hist else bbox_scale
        hist.append(bbox_scale)
        scale_velocity = bbox_scale - prev_scale

        # --- Depth approximation signal #2: dense optical flow magnitude in ROI ---
        gray_roi = cv2.cvtColor(roi_resized, cv2.COLOR_BGR2GRAY)
        flow_expansion = 0.0
        prev_gray = self._prev_gray_roi[side]
        if prev_gray is not None:
            flow = cv2.calcOpticalFlowFarneback(
                prev_gray, gray_roi, None,
                pyr_scale=0.5, levels=2, winsize=15,
                iterations=2, poly_n=5, poly_sigma=1.1, flags=0,
            )
            # Radial expansion vs. contraction relative to ROI center indicates
            # forward/backward motion, corroborating the bbox-scale signal.
            hgt, wid = flow.shape[:2]
            yy, xx = np.mgrid[0:hgt, 0:wid]
            cx_roi, cy_roi = wid / 2, hgt / 2
            radial_dir = np.stack([xx - cx_roi, yy - cy_roi], axis=-1)
            norm = np.linalg.norm(radial_dir, axis=-1, keepdims=True) + 1e-6
            radial_unit = radial_dir / norm
            radial_component = np.sum(flow * radial_unit, axis=-1)
            flow_expansion = float(np.mean(radial_component))
        self._prev_gray_roi[side] = gray_roi

        # Combine both signals (weighted) into a single depth-velocity scalar.
        # +ve => approaching camera (zoom in cue), -ve => receding (zoom out cue).
        depth_velocity = 0.7 * scale_velocity + 0.3 * flow_expansion

        confidence = float(np.mean([lp.visibility if hasattr(lp, "visibility") else 1.0
                                     for lp in lm.landmark]))

        return HandTrack(
            hand_id=side,
            present=True,
            center_px=smoothed_center,
            velocity_px=velocity,
            landmarks=smoothed_lms,
            landmarks_img=lms_img,
            bbox_scale=bbox_scale,
            depth_velocity=depth_velocity,
            confidence=confidence,
            roi=(x1, y1, x2, y2),
        )

    def close(self):
        self.hands.close()
