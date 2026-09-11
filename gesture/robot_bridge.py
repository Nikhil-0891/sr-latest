"""
robot_bridge.py
================
Translates a confirmed GestureEvent into a structured command and dispatches
it to the robot over one of three transports:

  1. ROS2 (rclpy)  — publishes geometry_msgs/Twist-like velocity commands on
                      /teleop/cmd_vel plus std_msgs/String on /teleop/event
                      for discrete events (PAUSE/RESUME/CONFIRM/CANCEL).
  2. UDP JSON       — fallback / simulator-friendly transport, no ROS install
                      required. Sends the same payload as JSON over UDP.
  3. Serial (JSON)  — for direct microcontroller-driven arms (Arduino, etc.)

Transport is selected at construction time; ROS2 unavailability is handled
gracefully (falls back to UDP with a warning) so the pipeline never crashes
due to a missing ROS2 install.

Command smoothing / rate limiting is applied so continuous gestures (e.g. a
held PAN_LEFT) produce a steady velocity stream rather than one-shot spikes,
which is what a real arm/gimbal controller expects for smooth trajectories.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import asdict, dataclass
from typing import Optional

from loguru import logger

from gesture_classifier import ControlState, GestureEvent, GestureType

# --------------------------------------------------------------------------- #
# Gesture -> normalized velocity command mapping
# --------------------------------------------------------------------------- #

# (linear_x=pan, linear_y=tilt, linear_z=zoom) in normalized units [-1, 1]
_GESTURE_VELOCITY = {
    GestureType.PAN_LEFT:  (-1.0, 0.0, 0.0),
    GestureType.PAN_RIGHT: (1.0, 0.0, 0.0),
    GestureType.TILT_UP:   (0.0, 1.0, 0.0),
    GestureType.TILT_DOWN: (0.0, -1.0, 0.0),
    GestureType.ZOOM_IN:   (0.0, 0.0, 1.0),
    GestureType.ZOOM_OUT:  (0.0, 0.0, -1.0),
}

_DISCRETE_EVENTS = {
    GestureType.PAUSE, GestureType.RESUME, GestureType.CONFIRM, GestureType.CANCEL
}


@dataclass
class RobotCommand:
    """Wire-format command payload (JSON-serializable)."""
    seq: int
    timestamp: float
    control_state: str
    gesture: str
    confidence: float
    linear_x: float   # pan velocity, normalized [-1, 1]
    linear_y: float   # tilt velocity, normalized [-1, 1]
    linear_z: float   # zoom velocity, normalized [-1, 1]
    event: Optional[str] = None  # discrete event name, if any

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# --------------------------------------------------------------------------- #
# Transport implementations
# --------------------------------------------------------------------------- #

class _UdpTransport:
    def __init__(self, target_host: str, target_port: int):
        self.addr = (target_host, target_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        logger.info(f"UDP transport -> {self.addr}")

    def send(self, cmd: RobotCommand):
        self.sock.sendto(cmd.to_json().encode("utf-8"), self.addr)

    def close(self):
        self.sock.close()


class _SerialTransport:
    def __init__(self, port: str, baud: int = 115200):
        import serial  # local import: optional dependency
        self.ser = serial.Serial(port, baud, timeout=0.01)
        logger.info(f"Serial transport -> {port}@{baud}")

    def send(self, cmd: RobotCommand):
        self.ser.write((cmd.to_json() + "\n").encode("utf-8"))

    def close(self):
        self.ser.close()


class _Ros2Transport:
    """Thin ROS2 publisher wrapper. Degrades to UDP automatically if rclpy
    or a working ROS2 environment is not available on this machine."""

    def __init__(self, udp_fallback: "_UdpTransport"):
        self._fallback = udp_fallback
        self._ok = False
        try:
            import rclpy
            from geometry_msgs.msg import Twist
            from std_msgs.msg import String

            rclpy.init(args=None)
            self._rclpy = rclpy
            self._node = rclpy.create_node("gesture_teleop_bridge")
            self._twist_pub = self._node.create_publisher(Twist, "/teleop/cmd_vel", 10)
            self._event_pub = self._node.create_publisher(String, "/teleop/event", 10)
            self._Twist = Twist
            self._String = String
            self._ok = True
            logger.info("ROS2 transport initialized -> /teleop/cmd_vel, /teleop/event")
        except Exception as e:  # ImportError or RCL init failure
            logger.warning(f"ROS2 unavailable ({e}); falling back to UDP transport.")

    def send(self, cmd: RobotCommand):
        if not self._ok:
            self._fallback.send(cmd)
            return
        twist = self._Twist()
        twist.linear.x = cmd.linear_x
        twist.linear.y = cmd.linear_y
        twist.linear.z = cmd.linear_z
        self._twist_pub.publish(twist)
        if cmd.event:
            msg = self._String()
            msg.data = cmd.to_json()
            self._event_pub.publish(msg)

    def close(self):
        if self._ok:
            self._node.destroy_node()
            self._rclpy.shutdown()
        else:
            self._fallback.close()


# --------------------------------------------------------------------------- #
# Public bridge
# --------------------------------------------------------------------------- #

class RobotBridge:
    """Rate-limited, smoothed command translator + dispatcher.

    Usage:
        bridge = RobotBridge(mode="udp", udp_target=("127.0.0.1", 9000))
        bridge.dispatch(gesture_event)
    """

    def __init__(
        self,
        mode: str = "udp",                # "udp" | "serial" | "ros2"
        udp_target: tuple = ("127.0.0.1", 9000),
        serial_port: str = "/dev/ttyUSB0",
        serial_baud: int = 115200,
        max_send_hz: float = 30.0,        # command rate limit to the actuator
        velocity_smoothing: float = 0.4,  # EMA alpha applied to outgoing velocities
    ):
        self._udp = _UdpTransport(*udp_target)

        if mode == "serial":
            self._transport = _SerialTransport(serial_port, serial_baud)
        elif mode == "ros2":
            self._transport = _Ros2Transport(self._udp)
        else:
            self._transport = self._udp

        self._min_dt = 1.0 / max_send_hz
        self._last_send_t = 0.0
        self._seq = 0
        self._vel_alpha = velocity_smoothing
        self._smoothed_vel = [0.0, 0.0, 0.0]

    # ------------------------------------------------------------------ #
    def dispatch(self, event: GestureEvent) -> Optional[RobotCommand]:
        """Convert a GestureEvent to a RobotCommand and send it, respecting
        the configured rate limit. Returns the command sent (or None if
        rate-limited / no-op) so the caller (main.py) can render it on the HUD."""
        now = time.time()

        raw_vel = _GESTURE_VELOCITY.get(event.gesture, (0.0, 0.0, 0.0))
        # Smooth outgoing velocity so discrete gesture triggers translate into
        # a gentle acceleration ramp on the physical actuator, not a step input.
        for i in range(3):
            self._smoothed_vel[i] = (
                self._vel_alpha * raw_vel[i] + (1 - self._vel_alpha) * self._smoothed_vel[i]
            )

        is_discrete = event.gesture in _DISCRETE_EVENTS
        is_motion = event.gesture in _GESTURE_VELOCITY
        if not is_discrete and not is_motion and all(abs(v) < 1e-3 for v in self._smoothed_vel):
            return None  # nothing meaningful to send; skip to save bandwidth

        if (now - self._last_send_t) < self._min_dt and not is_discrete:
            return None  # rate-limited (discrete safety events always go through)

        self._seq += 1
        cmd = RobotCommand(
            seq=self._seq,
            timestamp=now,
            control_state=event.control_state.name,
            gesture=event.gesture.name,
            confidence=round(event.confidence, 3),
            linear_x=round(self._smoothed_vel[0], 3),
            linear_y=round(self._smoothed_vel[1], 3),
            linear_z=round(self._smoothed_vel[2], 3),
            event=event.gesture.name if is_discrete else None,
        )

        # Safety interlock: never emit motion commands while PAUSED.
        if event.control_state == ControlState.PAUSED and not is_discrete:
            cmd.linear_x = cmd.linear_y = cmd.linear_z = 0.0

        self._transport.send(cmd)
        self._last_send_t = now
        return cmd

    def close(self):
        self._transport.close()
