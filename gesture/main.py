"""
main.py
=======
Orchestrates the full pipeline:
    webcam capture -> tracker.LongRangeHandTracker -> gesture_classifier.GestureStateMachine
    -> robot_bridge.RobotBridge -> OpenCV HUD overlay + FPS/latency benchmarking.

Run:
    python main.py --source 0 --mode udp --udp-host 127.0.0.1 --udp-port 9000
    python main.py --source 0 --mode ros2
"""

from __future__ import annotations

import argparse
import time
from collections import deque

import cv2
import numpy as np
from loguru import logger

from tracker import LongRangeHandTracker, FrameTrackResult
from gesture_classifier import GestureStateMachine, ControlState, GestureType
from robot_bridge import RobotBridge


# --------------------------------------------------------------------------- #
# HUD rendering
# --------------------------------------------------------------------------- #

COLOR_ACTIVE = (60, 220, 60)
COLOR_PAUSED = (40, 40, 220)
COLOR_TEXT = (240, 240, 240)
COLOR_ACCENT = (0, 200, 255)


def draw_hud(frame: np.ndarray, frame_result: FrameTrackResult, gesture_event,
             fps: float, latency_ms: float) -> np.ndarray:
    h, w = frame.shape[:2]
    state_color = COLOR_ACTIVE if gesture_event.control_state == ControlState.ACTIVE else COLOR_PAUSED

    # --- top status bar ---
    cv2.rectangle(frame, (0, 0), (w, 46), (20, 20, 20), -1)
    cv2.putText(frame, f"STATE: {gesture_event.control_state.name}", (12, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, state_color, 2)
    cv2.putText(frame, f"FPS: {fps:5.1f}", (260, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_TEXT, 1)
    cv2.putText(frame, f"Latency: {latency_ms:5.1f} ms", (390, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_TEXT, 1)

    # --- active command panel ---
    cmd_text = gesture_event.gesture.name if gesture_event.gesture != GestureType.NONE else "—"
    cv2.rectangle(frame, (0, h - 70), (w, h), (20, 20, 20), -1)
    cv2.putText(frame, f"COMMAND: {cmd_text}", (12, h - 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_ACCENT, 2)
    cv2.putText(frame, f"Confidence: {gesture_event.confidence * 100:5.1f}%", (12, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_TEXT, 1)

    # --- trajectory direction arrow ---
    dx, dy = gesture_event.direction_vector
    if abs(dx) > 0 or abs(dy) > 0:
        origin = (w - 90, h - 90)
        tip = (int(origin[0] + dx * 50), int(origin[1] + dy * 50))
        cv2.arrowedLine(frame, origin, tip, COLOR_ACCENT, 4, tipLength=0.35)

    # --- person / hand overlays ---
    if frame_result.person_bbox:
        x1, y1, x2, y2 = frame_result.person_bbox
        cv2.rectangle(frame, (x1, y1), (x2, y2), (120, 120, 120), 1)

    for side, track in frame_result.hands.items():
        if not track.present:
            continue
        color = (0, 255, 255) if side == "right" else (255, 180, 0)
        if track.roi:
            cv2.rectangle(frame, (track.roi[0], track.roi[1]),
                          (track.roi[2], track.roi[3]), color, 1)
        if track.landmarks_img is not None:
            for (lx, ly) in track.landmarks_img.astype(int):
                cv2.circle(frame, (lx, ly), 2, color, -1)
        cx, cy = int(track.center_px[0]), int(track.center_px[1])
        cv2.circle(frame, (cx, cy), 5, color, -1)
        cv2.putText(frame, f"{side} d_vel:{track.depth_velocity:+.1f}",
                    (cx + 8, cy - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

    return frame


# --------------------------------------------------------------------------- #
# Benchmarking helper
# --------------------------------------------------------------------------- #

class Benchmark:
    def __init__(self, window: int = 60, log_path: str = "latency_log.csv"):
        self.frame_times = deque(maxlen=window)
        self.log_path = log_path
        self._f = open(log_path, "w")
        self._f.write("timestamp,total_ms,stage1_ms,stage2_ms,classify_ms,dispatch_ms\n")

    def record(self, total_ms, stage1_ms, stage2_ms, classify_ms, dispatch_ms):
        self.frame_times.append(total_ms)
        self._f.write(f"{time.time():.4f},{total_ms:.2f},{stage1_ms:.2f},"
                       f"{stage2_ms:.2f},{classify_ms:.2f},{dispatch_ms:.2f}\n")

    @property
    def fps(self) -> float:
        if not self.frame_times:
            return 0.0
        avg_ms = sum(self.frame_times) / len(self.frame_times)
        return 1000.0 / avg_ms if avg_ms > 0 else 0.0

    def close(self):
        self._f.close()


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Gesture-to-Teleoperation Pipeline")
    parser.add_argument("--source", default=0, help="Camera index or video file path")
    parser.add_argument("--pose-model", default="yolov8n-pose.pt")
    parser.add_argument("--device", default="cpu", help="'cpu' or 'cuda:0'")
    parser.add_argument("--mode", default="udp", choices=["udp", "serial", "ros2"])
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=9000)
    parser.add_argument("--serial-port", default="/dev/ttyUSB0")
    parser.add_argument("--confirm-frames", type=int, default=5,
                         help="Consecutive frames required to confirm a gesture")
    parser.add_argument("--cooldown", type=float, default=0.6,
                         help="Seconds between repeated firings of the same gesture")
    parser.add_argument("--no-display", action="store_true")
    args = parser.parse_args()

    try:
        source = int(args.source)
    except ValueError:
        source = args.source

    cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
    if not cap.isOpened():
        logger.error(f"Could not open video source: {source}")
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    tracker = LongRangeHandTracker(pose_model_path=args.pose_model, device=args.device)
    classifier = GestureStateMachine(confirm_frames=args.confirm_frames,
                                      cooldown_s=args.cooldown)
    bridge = RobotBridge(mode=args.mode,
                          udp_target=(args.udp_host, args.udp_port),
                          serial_port=args.serial_port)
    bench = Benchmark()

    logger.info("Pipeline started. Press 'q' to quit, 'r' to reset control state.")

    try:
        while True:
            t_start = time.perf_counter()
            ok, frame = cap.read()
            if not ok:
                logger.warning("Frame grab failed; exiting.")
                break
            frame = cv2.flip(frame, 1)  # mirror for intuitive left/right mapping

            frame_result = tracker.process(frame)

            t_classify0 = time.perf_counter()
            gesture_event = classifier.update(frame_result)
            classify_ms = (time.perf_counter() - t_classify0) * 1000.0

            t_dispatch0 = time.perf_counter()
            bridge.dispatch(gesture_event)
            dispatch_ms = (time.perf_counter() - t_dispatch0) * 1000.0

            total_ms = (time.perf_counter() - t_start) * 1000.0
            bench.record(total_ms, frame_result.stage1_ms, frame_result.stage2_ms,
                         classify_ms, dispatch_ms)

            if not args.no_display:
                hud_frame = draw_hud(frame, frame_result, gesture_event,
                                      bench.fps, total_ms)
                cv2.imshow("Gesture Teleoperation", hud_frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("r"):
                    classifier.state = ControlState.PAUSED
                    logger.info("Control state manually reset to PAUSED.")

    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        tracker.close()
        bridge.close()
        bench.close()
        logger.info(f"Session ended. Avg FPS: {bench.fps:.1f}. "
                    f"Latency log written to {bench.log_path}")


if __name__ == "__main__":
    main()
