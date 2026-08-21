#!/usr/bin/env python3
"""
3D User Interface for Robotic Arm
Standalone 3D visualization that can read arm state from file or queue
"""

import math
import time
import threading
import json
import os
from ursina import *
from ursina.prefabs.ground import Ground

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, 'arm_state.json')

class RoboticArmVisual:
    """3D visualization of robotic arm"""
    
    def __init__(self):
        self.base_angle = 0        
        self.shoulder = 45         
        self.elbow = 45            
        self.wrist = 0
        
        self.shoulder_link = 2.5
        self.elbow_link = 2.0
        self.wrist_link = 1.2
        
        self.create_visuals()
        self.update_positions()
    
    def create_visuals(self):
        """Create 3D components"""
        # Base (fixed platform)
        self.base = Entity(
            model='cylinder',
            scale=(1.5, 0.3, 1.5),
            color=color.dark_gray,
            position=(0, -0.2, 0)
        )
        
        # Shoulder joint
        self.joint_shoulder = Entity(
            model='sphere',
            scale=0.35,
            color=color.red,
            position=(0, 0, 0),
            highlight_color=color.orange
        )
        
        # Shoulder link
        self.link_shoulder = Entity(
            model='cube',
            scale=(0.25, self.shoulder_link, 0.25),
            color=color.orange,
            position=(0, self.shoulder_link/2, 0)
        )
        
        # Elbow joint
        self.joint_elbow = Entity(
            model='sphere',
            scale=0.3,
            color=color.blue,
            position=(0, self.shoulder_link, 0),
            highlight_color=color.cyan
        )
        
        # Elbow link
        self.link_elbow = Entity(
            model='cube',
            scale=(0.22, self.elbow_link, 0.22),
            color=color.cyan,
            position=(0, self.shoulder_link + self.elbow_link/2, 0)
        )
        
        # Wrist joint
        self.joint_wrist = Entity(
            model='sphere',
            scale=0.28,
            color=color.green,
            position=(0, self.shoulder_link + self.elbow_link, 0),
            highlight_color=color.lime
        )
        
        # Wrist link
        self.link_wrist = Entity(
            model='cube',
            scale=(0.2, self.wrist_link, 0.2),
            color=color.lime,
            position=(0, self.shoulder_link + self.elbow_link + self.wrist_link/2, 0)
        )
        
        # Gripper (end-effector)
        self.gripper = Entity(
            model='sphere',
            scale=0.35,
            color=color.gold,
            position=(0, self.shoulder_link + self.elbow_link + self.wrist_link, 0),
            highlight_color=color.yellow
        )
        
        # Trajectory trail (subtle line)
        self.trajectory_points = []
        self.max_trajectory = 100
    
    def update_positions(self):
        """Calculate and update all joint positions based on angles"""
        base_rad = math.radians(self.base_angle)
        shoulder_rad = math.radians(self.shoulder)
        
        # Shoulder position (relative to base)
        sy = self.shoulder_link * math.cos(shoulder_rad)
        sz = self.shoulder_link * math.sin(shoulder_rad)
        sx = 0
        
        # Apply base rotation
        sy_rot = sy * math.cos(base_rad)
        
        # Elbow position
        cum_ang = self.shoulder + self.elbow
        cum_rad = math.radians(cum_ang)
        ey = sy_rot + self.elbow_link * math.cos(cum_rad)
        ez = sz + self.elbow_link * math.sin(cum_rad)
        ex = 0
        
        # Wrist position
        w_rad = math.radians(cum_ang + self.wrist)
        wy = ey + self.wrist_link * math.cos(w_rad)
        wz = ez + self.wrist_link * math.sin(w_rad)
        wx = 0
        
        # Update base rotation
        self.base.rotation = (0, self.base_angle, 0)
        
        # Update shoulder
        self.joint_shoulder.position = (sx, sy_rot, sz)
        self.link_shoulder.position = (sx, (sy_rot + sy_rot)/2, sz)
        self.link_shoulder.rotation = (math.degrees(shoulder_rad), self.base_angle, 0)
        
        # Update elbow
        self.joint_elbow.position = (ex, ey, ez)
        self.link_elbow.position = (ex, (sy_rot + ey)/2, (sz + ez)/2)
        self.link_elbow.rotation = (math.degrees(cum_rad), self.base_angle, 0)
        
        # Update wrist
        self.joint_wrist.position = (wx, wy, wz)
        self.link_wrist.position = (wx, (ey + wy)/2, (ez + wz)/2)
        self.link_wrist.rotation = (math.degrees(w_rad), self.base_angle, 0)
        
        # Update gripper
        self.gripper.position = (wx, wy, wz)
        
        # Track trajectory
        self.trajectory_points.append((wx, wy, wz))
        if len(self.trajectory_points) > self.max_trajectory:
            self.trajectory_points.pop(0)
    
    def load_state(self, state_dict):
        """Load arm state from dictionary"""
        if 'base_angle' in state_dict:
            self.base_angle = state_dict['base_angle']
        if 'shoulder' in state_dict:
            self.shoulder = state_dict['shoulder']
        if 'elbow' in state_dict:
            self.elbow = state_dict['elbow']
        if 'wrist' in state_dict:
            self.wrist = state_dict['wrist']
        self.update_positions()


# Initialize Ursina
app = Ursina(
    title='🦾 3D Robotic Arm Visualization',
    fullscreen=False,
    size=(1200, 900)
)

# Camera setup
camera.position = (6, 4, 6)
camera.look_at((0, 2, 0))

# Lighting
DirectionalLight(direction=(1, 1, 1), shadows=True)
AmbientLight(intensity=0.6)

# Background
Sky(texture='white_cube')

# Ground
Ground(size=(20, 1, 20), texture='white_cube')

# Create arm
arm = RoboticArmVisual()

# Info panel
info_entity = Entity()
info_text = Text(
    text="🦾 3D Arm Visualization\nReading state from arm_state.json",
    origin=(0, 0.5),
    scale=1,
    background=True,
    background_color=color.dark_gray
)

last_update = time.time()

def update():
    """Main update loop - read state from JSON file"""
    global last_update
    
    # Check file every 100ms
    if time.time() - last_update > 0.1:
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    arm.load_state(state)
                    
                    # Update info text
                    info_text.text = (
                        f"🦾 3D Arm Visualization\n"
                        f"Base: {arm.base_angle:.0f}° | "
                        f"Shoulder: {arm.shoulder:.0f}° | "
                        f"Elbow: {arm.elbow:.0f}° | "
                        f"Wrist: {arm.wrist:.0f}°\n"
                        f"Gripper: ({arm.gripper.position.x:.2f}, {arm.gripper.position.y:.2f}, {arm.gripper.position.z:.2f})"
                    )
        except Exception as e:
            info_text.text = f"Error reading state: {e}"
        
        last_update = time.time()

# Setup update
class UIUpdater(Entity):
    def update(self):
        update()

updater = UIUpdater()

print("=" * 70)
print("🦾 3D ARM VISUALIZATION STARTED")
print("=" * 70)
print("Waiting for arm_state.json updates...")
print("Close this window to stop.")
print()

try:
    app.run()
except KeyboardInterrupt:
    print("\n🛑 Visualization stopped")
