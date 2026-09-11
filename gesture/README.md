
# Real-Time Gesture-to-Teleoperation Pipeline

Production-grade architecture for controlling a robotic arm / PTZ camera via
long-range, full-body-aware hand gesture recognition.

---

## 1. System Architecture

```
                         ┌─────────────────────────────────────────────────────────┐
                         │                     main.py (Orchestrator)               │
                         │   capture → track → classify → translate → actuate → HUD │
                         └─────────────────────────────────────────────────────────┘
                                   │              │               │            │
                     ┌─────────────┘         ┌────┘          ┌────┘        ┌───┘
                     ▼                       ▼               ▼             ▼
        ┌────────────────────┐   ┌────────────────────┐ ┌───────────┐ ┌──────────────┐
        │   tracker.py        │   │ gesture_classifier  │ │robot_bridge│ │  HUD overlay │
        │ Stage 1: YOLOv8-Pose │   │ .py                 │ │ .py        │ │  (OpenCV)    │
        │  (whole-frame,      │   │ - Static pose FSM    │ │ - JSON cmd │ └──────────────┘
        │   long range person │   │ - Dynamic vector FSM │ │ - ROS2 pub │
        │   + wrist keypoints)│   │ - Debounce/cooldown   │ │ - UDP/Serial│
        │ Stage 2: MediaPipe  │   │ - Confidence scoring  │ │   fallback │
        │  Hands on cropped   │   └────────────────────┘ └───────────┘
        │  ROI around wrist   │
        │  (zoomed, high-res) │
        │ - EMA/Kalman smooth │
        │ - Optical-flow depth│
        │   approximation     │
        └────────────────────┘
```

### Two-Stage Detection Pipeline (why it solves "far away = hand detection fails")

MediaPipe Hands alone needs the hand to occupy a reasonably large fraction of the
frame (roughly >5–8% of frame area) to detect reliably. At 4–8 m distance a hand is a few
dozen pixels wide and detection confidence collapses.

**Stage 1 — Full-body / long-range localization**
`YOLOv8-Pose` (or `MediaPipe Holistic`'s pose branch as a lighter-weight alternative)
runs on the **full, un-cropped frame** at low-medium resolution. It is trained on
whole-body data and remains robust at long range because it reasons about body
proportion, not fine hand texture. We extract the **wrist keypoints** (left/right)
and shoulder keypoints (for scale normalization).

**Dynamic ROI crop + digital zoom**
Around each wrist keypoint we cut a square ROI sized proportionally to the
shoulder-to-shoulder distance (a stable proxy for subject depth/scale), then
**upscale (digitally zoom) that crop** back to a fixed resolution (e.g., 256×256)
before feeding it to MediaPipe Hands. This recovers the effective pixel density
that Hands needs, regardless of how far the person is standing.

**Stage 2 — High-precision hand landmarks**
`MediaPipe Hands` runs only on the small, zoomed ROI → cheap (small input) and
accurate (recovered resolution) → 21 3D landmarks per hand.

**Temporal smoothing**
Each landmark stream is smoothed independently with an **Exponential Moving
Average (EMA)** filter (cheap, O(1), good for real-time) with an optional
**Kalman filter** (`filterpy`) for the wrist/palm-center trajectory, which is
what drives directional commands and benefits most from velocity-aware
prediction and jitter rejection.

**Depth (forward/backward) approximation**
True stereo/depth sensors are not assumed. We approximate relative depth using:
1. **Bounding-box scale change** of the hand ROI over time (hand getting bigger
   in frame ⇒ moving toward camera).
2. **Dense optical flow magnitude** (Farneback) inside the hand ROI as a
   secondary corroborating signal, reducing false triggers from lateral motion
   being misread as depth motion.

---

## 2. Benchmark Targets & How the Design Hits Them

| Requirement | Design choice |
|---|---|
| <30 ms end-to-end/frame | YOLOv8-Pose (nano, `yolov8n-pose.pt`) at 320–416px + MediaPipe Hands on a tiny 256×256 crop; both are sub-10ms on a modern GPU / sub-20ms on CPU with `int8`/`ONNX`. Frame budget: ~8ms pose + ~6ms hand + ~2ms classify + ~2ms overlay ≈ <20 ms typical. |
| >95% dynamic gesture accuracy | Debounced state machine requiring N consecutive confirming frames before a gesture is "committed" + confidence-weighted voting + velocity-threshold gating to reject noise, drastically cutting false positives that would otherwise cap real-world accuracy. |
| No jitter / false triggers | EMA + Kalman smoothing on landmark trajectories, cooldown timers per gesture class, hysteresis thresholds (different enter/exit thresholds), min-displacement gating before classifying any directional gesture. |

> **Note on the ">95% / <30ms" numbers:** These are *architectural design
> targets*, achievable with the stated model choices on suitable hardware
> (see Hardware section). Actual numbers must be validated on your target
> dataset/hardware — no vision pipeline can honestly assert these figures
> without empirical benchmarking harness, which `main.py` includes
> (FPS counter + latency logger) so you can validate on your own rig.

---

## 3. Gesture → Command Map

| Gesture | Signal used | Command |
|---|---|---|
| Open palm, moving toward camera | bbox-scale↑ + optical-flow expansion | `ZOOM_IN` |
| Open palm, moving away from camera | bbox-scale↓ + optical-flow contraction | `ZOOM_OUT` |
| Hand moving left | wrist Δx < 0, |Δx| > threshold | `PAN_LEFT` |
| Hand moving right | wrist Δx > 0 | `PAN_RIGHT` |
| Hand moving up | wrist Δy < 0 (image coords) | `TILT_UP` |
| Hand moving down | wrist Δy > 0 | `TILT_DOWN` |
| Closed fist (static, held) | finger-curl geometry, low velocity | `PAUSE` |
| Two open palms, both hands, low velocity | dual-hand static pose | `RESUME` |
| Thumbs up (static) | thumb-extended + other fingers curled + thumb-up orientation | `CONFIRM` |
| Thumbs down (static) | thumb-extended + orientation inverted | `CANCEL` |

---

## 4. Setup Instructions

### 4.1 Python environment
```bash
python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4.2 Download YOLOv8-Pose weights
```python
from ultralytics import YOLO
YOLO("yolov8n-pose.pt")   # auto-downloads to ./ on first run
```
Use `yolov8n-pose.pt` (nano) for real-time CPU/edge use, or `yolov8s-pose.pt` /
`yolov8m-pose.pt` on a GPU box for higher long-range accuracy.

### 4.3 (Optional) ROS2 setup
```bash
# Ubuntu 22.04 + ROS2 Humble (or 24.04 + Jazzy)
sudo apt install ros-humble-desktop python3-colcon-common-extensions
source /opt/ros/humble/setup.bash
pip install --no-deps rclpy   # normally comes with the ROS2 apt install, not pip
```
If `rclpy` is not importable, `robot_bridge.py` **automatically falls back** to
UDP JSON messages so the pipeline still runs end-to-end without ROS2 installed.

### 4.4 Run
```bash
python main.py --source 0 --mode udp --udp-target 127.0.0.1:9000
# or, with ROS2 available:
python main.py --source 0 --mode ros2
```

---

## 5. Hardware Considerations for Long-Range Vision

- **Camera:** Prefer a fixed-focal, low-distortion lens ≥ 1080p @ 30–60 fps.
  A slight telephoto (35–50mm equivalent) improves long-range pixel density
  on the subject vs. a wide-angle security-style lens.
- **Resolution vs. latency trade-off:** Capture at 1080p for long-range
  keypoint quality, but run YOLOv8-Pose inference on a downscaled (416px)
  copy — only use full-res pixels for the *cropped hand ROI* stage, where
  resolution matters most.
- **Lighting:** IR-assisted or well-lit environments significantly improve
  MediaPipe Hands' fine-landmark accuracy on small, zoomed crops.
- **Compute:** A CUDA GPU (even entry-level, e.g., RTX 3050/4060) is
  recommended to comfortably hit <30ms with YOLOv8-Pose. On CPU-only edge
  devices (e.g., Jetson Orin Nano, Raspberry Pi 5 + Coral), export YOLOv8-Pose
  to ONNX/TensorRT/NCNN and use `yolov8n-pose` for acceptable latency.
- **Mounting:** Rigidly mount the camera near the arm's base to keep the
  camera→arm coordinate transform static and avoid recalibration per session.

---

## 6. File Layout

```
gesture_teleop/
├── README.md
├── requirements.txt
├── tracker.py              # Stage 1+2 CV pipeline, smoothing, depth approx
├── gesture_classifier.py   # State machine: static + dynamic gesture logic
├── robot_bridge.py         # Gesture → JSON/ROS2/UDP/Serial command translator
└── main.py                 # Capture loop, HUD, benchmarking
```

## 7. Extending

- Swap `MediaPipe Holistic` in for YOLOv8-Pose in `tracker.py` if you want a
  pure-CPU, dependency-light stack (trade-off: somewhat less robust at extreme
  range than YOLOv8-Pose, but no separate weights file to manage).
- Add inverse-kinematics mapping in `robot_bridge.py` by replacing the
  `Twist`-style relative-velocity command with joint-space targets computed
  via your arm's IK solver (e.g., `ikpy`, `pinocchio`, or vendor SDK).
