from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
GESTURE_DIR = ROOT / "gesture"
if str(GESTURE_DIR) not in sys.path:
    sys.path.insert(0, str(GESTURE_DIR))

from tracker import LongRangeHandTracker  # noqa: E402
from gesture_classifier import (  # noqa: E402
    GestureStateMachine,
    ControlState,
    GestureType,
)
from robot_bridge import RobotBridge  # noqa: E402


app = FastAPI(title="Robotic Camera Control Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------
status = {
    "connection": "Connected",
    "control": "---",
    "camera": "Waiting",
    "voice": "Ready",
    "gesture": "NONE",
    "last_command": "---",
    "pan": 0.0,
    "tilt": 0.0,
    "position_x": 0.0,
    "position_y": 0.0,
    "mode": None,
    "gesture_confidence": 0.0,
}

state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# WebSocket clients
# ---------------------------------------------------------------------------
clients: set[WebSocket] = set()
clients_lock = asyncio.Lock()


async def broadcast(payload: dict) -> None:
    dead = []
    async with clients_lock:
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)


def broadcast_from_thread(payload: dict) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast(payload))
    except RuntimeError:
        # The HTTP status polling remains available even if no event loop is
        # available in this worker thread.
        pass


# ---------------------------------------------------------------------------
# Gesture service
# ---------------------------------------------------------------------------
class GestureService:
    """Runs the existing tracker + classifier in a background camera thread."""

    def __init__(self) -> None:
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.frame_lock = threading.Lock()
        self.latest_jpeg: Optional[bytes] = None
        self.running = False
        self.camera_open = False
        self.tracker: Optional[LongRangeHandTracker] = None
        self.classifier: Optional[GestureStateMachine] = None
        self.bridge: Optional[RobotBridge] = None

    def start(self) -> None:
        if self.running:
            return

        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run,
            name="gesture-camera",
            daemon=True,
        )
        self.running = True
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        thread = self.thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        self.running = False
        self.camera_open = False

        with self.frame_lock:
            self.latest_jpeg = None

        with state_lock:
            status["camera"] = "Waiting"
            status["gesture"] = "NONE"
            status["gesture_confidence"] = 0.0

    def _set_status(self, **values) -> None:
        with state_lock:
            status.update(values)

    def _run(self) -> None:
        cap = None
        try:
            # Use the same normal OpenCV camera path that was already verified
            # on this machine. Keep the processing resolution at 640x480.
            cap = cv2.VideoCapture(0)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            if not cap.isOpened():
                self.camera_open = False
                self._set_status(camera="Unavailable", control="PAUSED")
                return

            self.camera_open = True
            self._set_status(camera="Ready", control="PAUSED")

            model_path = str(GESTURE_DIR / "yolov8n-pose.pt")
            self.tracker = LongRangeHandTracker(
                pose_model_path=model_path,
                device="cpu",
            )
            self.classifier = GestureStateMachine(
                confirm_frames=5,
                cooldown_s=0.6,
            )
            # Keep the existing UDP bridge in simulator-friendly mode. No
            # physical robot is connected by this integration yet.
            self.bridge = RobotBridge(
                mode="udp",
                udp_target=("127.0.0.1", 9000),
            )

            while not self.stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    self._set_status(camera="Frame Error")
                    time.sleep(0.05)
                    continue

                frame = cv2.flip(frame, 1)
                frame_result = self.tracker.process(frame)
                event = self.classifier.update(frame_result)

                # IMPORTANT: the web-selected mode is the final safety gate.
                with state_lock:
                    selected_mode = status["mode"]

                if selected_mode != "gesture":
                    # Do not dispatch gesture commands when Voice mode is
                    # selected, even if the CV classifier detects one.
                    event_for_bridge = None
                else:
                    event_for_bridge = event

                if event_for_bridge is not None:
                    cmd = self.bridge.dispatch(event_for_bridge)
                else:
                    cmd = None

                if selected_mode == "gesture":
                    self._handle_gesture_event(event, cmd)
                else:
                    self._set_status(
                        control="ACTIVE" if selected_mode == "voice" else "---",
                        gesture="NONE",
                        gesture_confidence=0.0,
                    )

                self._annotate(frame, frame_result, event, selected_mode)
                ok_jpg, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 78],
                )
                if ok_jpg:
                    with self.frame_lock:
                        self.latest_jpeg = encoded.tobytes()

        except Exception as exc:
            print(f"[GESTURE ERROR] {exc}")
            self._set_status(camera="Error", control="PAUSED")
        finally:
            if cap is not None:
                cap.release()
            if self.tracker is not None:
                try:
                    self.tracker.close()
                except Exception:
                    pass
            if self.bridge is not None:
                try:
                    self.bridge.close()
                except Exception:
                    pass

            self.tracker = None
            self.classifier = None
            self.bridge = None
            self.camera_open = False
            self.running = False

    def _handle_gesture_event(self, event, cmd) -> None:
        gesture_name = event.gesture.name
        self._set_status(
            gesture=gesture_name,
            gesture_confidence=round(float(event.confidence), 3),
            control=event.control_state.name,
        )

        if event.gesture == GestureType.NONE:
            return

        command = gesture_to_command(event.gesture)
        if command is not None:
            with state_lock:
                status["last_command"] = command
                update_telemetry(command)

        payload = {
            "type": "gesture",
            "gesture": gesture_name,
            "confidence": round(float(event.confidence), 3),
            "control_state": event.control_state.name,
            "command": command,
        }
        broadcast_from_thread(payload)

    def _annotate(self, frame, frame_result, event, selected_mode) -> None:
        h, w = frame.shape[:2]

        # Top bar
        cv2.rectangle(frame, (0, 0), (w, 42), (20, 20, 20), -1)
        mode_text = (selected_mode or "SELECT MODE").upper()
        state_text = event.control_state.name
        cv2.putText(
            frame,
            f"MODE: {mode_text}  STATE: {state_text}",
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (240, 240, 240),
            2,
        )

        if frame_result.person_bbox:
            x1, y1, x2, y2 = frame_result.person_bbox
            cv2.rectangle(frame, (x1, y1), (x2, y2), (120, 120, 120), 1)

        for side, track in frame_result.hands.items():
            if not track.present:
                continue
            draw_color = (0, 255, 255) if side == "right" else (255, 180, 0)
            if track.roi:
                cv2.rectangle(
                    frame,
                    (track.roi[0], track.roi[1]),
                    (track.roi[2], track.roi[3]),
                    draw_color,
                    1,
                )
            if track.landmarks_img is not None:
                for lx, ly in track.landmarks_img.astype(int):
                    cv2.circle(frame, (int(lx), int(ly)), 2, draw_color, -1)
            cx, cy = int(track.center_px[0]), int(track.center_px[1])
            cv2.circle(frame, (cx, cy), 5, draw_color, -1)

        command = event.gesture.name if event.gesture != GestureType.NONE else "NONE"
        cv2.rectangle(frame, (0, h - 54), (w, h), (20, 20, 20), -1)
        cv2.putText(
            frame,
            f"GESTURE: {command}",
            (10, h - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 200, 255),
            2,
        )


service = GestureService()


# ---------------------------------------------------------------------------
# Command processing
# ---------------------------------------------------------------------------
class ModeRequest(BaseModel):
    mode: str


class CommandRequest(BaseModel):
    command: str
    source: str = "frontend"


GESTURE_COMMANDS = {
    GestureType.PAN_LEFT: "Camera Left",
    GestureType.PAN_RIGHT: "Camera Right",
    GestureType.TILT_UP: "Camera Up",
    GestureType.TILT_DOWN: "Camera Down",
    GestureType.ZOOM_IN: "Zoom In",
    GestureType.ZOOM_OUT: "Zoom Out",
    GestureType.PAUSE: "Gesture Pause",
    GestureType.RESUME: "Gesture Resume",
    GestureType.CONFIRM: "Gesture Confirm",
    GestureType.CANCEL: "Gesture Cancel",
}


def gesture_to_command(gesture: GestureType) -> Optional[str]:
    return GESTURE_COMMANDS.get(gesture)


def update_telemetry(command: str) -> None:
    step = 5.0
    if command == "Camera Left":
        status["pan"] -= step
        status["position_x"] -= step
    elif command == "Camera Right":
        status["pan"] += step
        status["position_x"] += step
    elif command == "Camera Up":
        status["tilt"] += step
        status["position_y"] += step
    elif command == "Camera Down":
        status["tilt"] -= step
        status["position_y"] -= step
    elif command == "Camera Home":
        status["pan"] = 0.0
        status["tilt"] = 0.0
        status["position_x"] = 0.0
        status["position_y"] = 0.0


def command_allowed_for_mode(command: str, source: str, mode: Optional[str]) -> bool:
    if mode not in {"voice", "gesture"}:
        return False

    if source == "voice":
        return mode == "voice"
    if source == "gesture":
        return mode == "gesture"

    # Backward-compatible frontend/manual commands follow the selected mode.
    return source == mode


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {"message": "Robotic Camera Control Backend", "status": "online"}


@app.get("/api/status")
def get_status():
    with state_lock:
        return dict(status)


@app.post("/api/mode")
def set_mode(request: ModeRequest):
    mode = request.mode.lower().strip()
    if mode not in {"voice", "gesture"}:
        return {"success": False, "error": "Mode must be 'voice' or 'gesture'."}

    with state_lock:
        status["mode"] = mode
        status["control"] = "ACTIVE" if mode == "voice" else "PAUSED"
        status["voice"] = "Ready" if mode == "voice" else "Standby"

    if mode == "gesture":
        service.start()
    else:
        service.stop()
        with state_lock:
            status["camera"] = "Ready"
            status["gesture"] = "NONE"
            status["gesture_confidence"] = 0.0

    return {"success": True, "mode": mode}


@app.post("/api/command")
def receive_command(request: CommandRequest):
    command = request.command.strip()
    source = request.source.lower().strip()

    with state_lock:
        mode = status["mode"]

    if not command_allowed_for_mode(command, source, mode):
        return {
            "success": False,
            "rejected": True,
            "reason": f"Command source '{source}' is not active. Selected mode: {mode or 'none'}.",
        }

    with state_lock:
        status["last_command"] = command
        update_telemetry(command)

    payload = {
        "type": "command",
        "command": command,
        "source": source,
    }
    # No physical actuator is connected in this stage. This endpoint updates
    # the common command/telemetry layer only.
    try:
        asyncio.create_task(broadcast(payload))
    except RuntimeError:
        pass

    return {
        "success": True,
        "command": command,
        "source": source,
        "mode": mode,
    }


@app.get("/api/gesture/video")
def gesture_video():
    def generate():
        while True:
            with service.frame_lock:
                frame = service.latest_jpeg
            if frame is None:
                time.sleep(0.1)
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache\r\n\r\n"
                + frame
                + b"\r\n"
            )
            time.sleep(0.03)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    async with clients_lock:
        clients.add(websocket)
    try:
        await websocket.send_json({"type": "status", "data": get_status()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        async with clients_lock:
            clients.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
