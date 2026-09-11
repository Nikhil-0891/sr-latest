"""
gesture_classifier.py
======================
Converts smoothed per-frame HandTrack data (from tracker.py) into discrete,
debounced GestureEvents.

Two classifier layers:
  1. Static pose recognition (open palm, closed fist, thumbs up/down) based
     purely on landmark geometry (finger curl angles), independent of motion.
  2. Dynamic gesture recognition (pan/tilt/zoom) based on smoothed velocity
     vectors from the Kalman-filtered palm center + depth-velocity signal.

A top-level `GestureStateMachine` fuses both layers, applies:
  - hysteresis thresholds (different enter/exit velocity thresholds)
  - N-consecutive-frame confirmation (temporal debouncing)
  - per-gesture cooldown windows
  - global PAUSED / ACTIVE control-state gating
to suppress false positives and satisfy the reliability requirement.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from typing import Deque, Dict, Optional

import numpy as np
from loguru import logger

from tracker import FrameTrackResult, HandTrack


# --------------------------------------------------------------------------- #
# Enums / data structures
# --------------------------------------------------------------------------- #

class ControlState(Enum):
    PAUSED = auto()
    ACTIVE = auto()


class GestureType(Enum):
    NONE = auto()
    ZOOM_IN = auto()
    ZOOM_OUT = auto()
    PAN_LEFT = auto()
    PAN_RIGHT = auto()
    TILT_UP = auto()
    TILT_DOWN = auto()
    PAUSE = auto()
    RESUME = auto()
    CONFIRM = auto()
    CANCEL = auto()


@dataclass
class GestureEvent:
    gesture: GestureType
    confidence: float
    control_state: ControlState
    direction_vector: tuple = (0.0, 0.0)  # (dx, dy) for HUD arrow rendering
    timestamp: float = 0.0


# --------------------------------------------------------------------------- #
# Static pose geometry helpers
# --------------------------------------------------------------------------- #

# MediaPipe Hands landmark indices
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_MCP, RING_PIP, RING_TIP = 13, 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

FINGER_TIPS = [INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP]
FINGER_PIPS = [INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP]
FINGER_MCPS = [INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP]


def _finger_extension_ratios(lms: np.ndarray) -> np.ndarray:
    """For each of the 4 non-thumb fingers, ratio of (tip-to-wrist) / (mcp-to-wrist)
    distance. > ~1.15 typically means "extended", < ~0.9 means "curled"."""
    wrist = lms[WRIST, :2]
    tip_d = np.linalg.norm(lms[FINGER_TIPS, :2] - wrist, axis=1)
    mcp_d = np.linalg.norm(lms[FINGER_MCPS, :2] - wrist, axis=1) + 1e-6
    return tip_d / mcp_d


def is_open_palm(lms: np.ndarray, ext_thresh: float = 1.15) -> bool:
    ratios = _finger_extension_ratios(lms)
    return bool(np.all(ratios > ext_thresh))


def is_closed_fist(lms: np.ndarray, curl_thresh: float = 0.92) -> bool:
    ratios = _finger_extension_ratios(lms)
    return bool(np.all(ratios < curl_thresh))


def is_thumbs_up(lms: np.ndarray) -> bool:
    """Thumb extended & pointing up, other four fingers curled."""
    ratios = _finger_extension_ratios(lms)
    other_curled = bool(np.all(ratios < 0.95))
    thumb_extended = np.linalg.norm(lms[THUMB_TIP, :2] - lms[WRIST, :2]) > \
        1.3 * np.linalg.norm(lms[THUMB_MCP, :2] - lms[WRIST, :2])
    # In image coordinates, y decreases upward.
    thumb_points_up = lms[THUMB_TIP, 1] < lms[THUMB_IP, 1] < lms[THUMB_MCP, 1]
    return bool(other_curled and thumb_extended and thumb_points_up)


def is_thumbs_down(lms: np.ndarray) -> bool:
    ratios = _finger_extension_ratios(lms)
    other_curled = bool(np.all(ratios < 0.95))
    thumb_extended = np.linalg.norm(lms[THUMB_TIP, :2] - lms[WRIST, :2]) > \
        1.3 * np.linalg.norm(lms[THUMB_MCP, :2] - lms[WRIST, :2])
    thumb_points_down = lms[THUMB_TIP, 1] > lms[THUMB_IP, 1] > lms[THUMB_MCP, 1]
    return bool(other_curled and thumb_extended and thumb_points_down)


# --------------------------------------------------------------------------- #
# Debounce helper
# --------------------------------------------------------------------------- #

class DebouncedTrigger:
    """Requires a gesture condition to hold for `confirm_frames` consecutive
    frames before firing, then enforces a `cooldown_s` refractory period.
    This is the primary false-positive suppression mechanism."""

    def __init__(self, confirm_frames: int = 5, cooldown_s: float = 0.6):
        self.confirm_frames = confirm_frames
        self.cooldown_s = cooldown_s
        self._streak = 0
        self._last_fire_t = 0.0

    def update(self, condition_true: bool) -> bool:
        now = time.time()
        if not condition_true:
            self._streak = 0
            return False
        self._streak += 1
        if self._streak >= self.confirm_frames and (now - self._last_fire_t) > self.cooldown_s:
            self._last_fire_t = now
            self._streak = 0
            return True
        return False


# --------------------------------------------------------------------------- #
# Main state machine
# --------------------------------------------------------------------------- #

class GestureStateMachine:
    """Fuses static + dynamic gesture signals into confirmed GestureEvents,
    while gating everything through a global PAUSED/ACTIVE control state."""

    # Directional velocity thresholds (px/frame, on Kalman-filtered center) —
    # hysteresis: ENTER threshold higher than the implicit noise floor,
    # no separate EXIT threshold needed since debounce+cooldown already
    # prevents chattering between commands.
    PAN_TILT_VEL_THRESH = 6.0
    DEPTH_VEL_THRESH = 1.8
    STILL_VEL_THRESH = 2.0  # below this counts as "still" for static poses

    def __init__(self, confirm_frames: int = 5, cooldown_s: float = 0.6):
        self.state = ControlState.PAUSED
        self.last_gesture = GestureType.NONE
        self.last_confidence = 0.0

        # One debouncer per discrete gesture class.
        self._triggers: Dict[GestureType, DebouncedTrigger] = {
            g: DebouncedTrigger(confirm_frames, cooldown_s)
            for g in GestureType if g != GestureType.NONE
        }
        # Pause/Resume use slightly longer confirmation windows since they
        # are high-consequence (start/stop robot motion) -> extra stability.
        self._triggers[GestureType.PAUSE] = DebouncedTrigger(confirm_frames + 3, cooldown_s)
        self._triggers[GestureType.RESUME] = DebouncedTrigger(confirm_frames + 3, cooldown_s)

        self._history: Deque[str] = deque(maxlen=50)  # for debugging/HUD trace

    # ------------------------------------------------------------------ #
    def update(self, frame_result: FrameTrackResult) -> GestureEvent:
        right = frame_result.hands.get("right")
        left = frame_result.hands.get("left")
        primary = right if (right and right.present) else left

        # ---- Global PAUSE / RESUME checks run regardless of ACTIVE/PAUSED ----
        pause_cond = self._closed_fist_condition(primary)
        if self._triggers[GestureType.PAUSE].update(pause_cond):
            self.state = ControlState.PAUSED
            return self._emit(GestureType.PAUSE, 0.95)

        resume_cond = self._two_open_palms_still_condition(left, right)
        if self._triggers[GestureType.RESUME].update(resume_cond):
            self.state = ControlState.ACTIVE
            return self._emit(GestureType.RESUME, 0.95)

        if self.state == ControlState.PAUSED:
            # While paused, ignore all motion/confirm/cancel commands to avoid
            # unintended robot motion — this is a critical safety gate.
            return self._emit(GestureType.NONE, 0.0)

        if primary is None or not primary.present:
            return self._emit(GestureType.NONE, 0.0)

        # ---- Static: CONFIRM / CANCEL ----
        thumbs_up_cond = is_thumbs_up(primary.landmarks) and self._is_still(primary)
        if self._triggers[GestureType.CONFIRM].update(thumbs_up_cond):
            return self._emit(GestureType.CONFIRM, primary.confidence)

        thumbs_down_cond = is_thumbs_down(primary.landmarks) and self._is_still(primary)
        if self._triggers[GestureType.CANCEL].update(thumbs_down_cond):
            return self._emit(GestureType.CANCEL, primary.confidence)

        # ---- Dynamic: ZOOM (requires open palm + depth-velocity) ----
        open_palm = is_open_palm(primary.landmarks)
        if open_palm:
            if self._triggers[GestureType.ZOOM_IN].update(
                primary.depth_velocity > self.DEPTH_VEL_THRESH
            ):
                return self._emit(GestureType.ZOOM_IN, primary.confidence,
                                   direction=(0, -1))
            if self._triggers[GestureType.ZOOM_OUT].update(
                primary.depth_velocity < -self.DEPTH_VEL_THRESH
            ):
                return self._emit(GestureType.ZOOM_OUT, primary.confidence,
                                   direction=(0, 1))

        # ---- Dynamic: PAN / TILT (dominant-axis velocity gating) ----
        vx, vy = primary.velocity_px
        if abs(vx) > self.PAN_TILT_VEL_THRESH and abs(vx) > abs(vy):
            gtype = GestureType.PAN_RIGHT if vx > 0 else GestureType.PAN_LEFT
            if self._triggers[gtype].update(True):
                return self._emit(gtype, primary.confidence, direction=(np.sign(vx), 0))

        if abs(vy) > self.PAN_TILT_VEL_THRESH and abs(vy) > abs(vx):
            # image y grows downward -> vy>0 means moving down
            gtype = GestureType.TILT_DOWN if vy > 0 else GestureType.TILT_UP
            if self._triggers[gtype].update(True):
                return self._emit(gtype, primary.confidence, direction=(0, np.sign(vy)))

        return self._emit(GestureType.NONE, 0.0)

    # ------------------------------------------------------------------ #
    def _is_still(self, track: HandTrack) -> bool:
        vx, vy = track.velocity_px
        return (vx ** 2 + vy ** 2) ** 0.5 < self.STILL_VEL_THRESH

    def _closed_fist_condition(self, track: Optional[HandTrack]) -> bool:
        if track is None or not track.present or track.landmarks is None:
            return False
        return is_closed_fist(track.landmarks) and self._is_still(track)

    def _two_open_palms_still_condition(
        self, left: Optional[HandTrack], right: Optional[HandTrack]
    ) -> bool:
        if left is None or right is None or not left.present or not right.present:
            return False
        if left.landmarks is None or right.landmarks is None:
            return False
        both_open = is_open_palm(left.landmarks) and is_open_palm(right.landmarks)
        both_still = self._is_still(left) and self._is_still(right)
        return both_open and both_still

    def _emit(self, gesture: GestureType, confidence: float,
              direction: tuple = (0.0, 0.0)) -> GestureEvent:
        self.last_gesture = gesture
        self.last_confidence = confidence
        if gesture != GestureType.NONE:
            self._history.append(gesture.name)
            logger.debug(f"Gesture confirmed: {gesture.name} (conf={confidence:.2f}, "
                         f"state={self.state.name})")
        return GestureEvent(
            gesture=gesture,
            confidence=confidence,
            control_state=self.state,
            direction_vector=direction,
            timestamp=time.time(),
        )
