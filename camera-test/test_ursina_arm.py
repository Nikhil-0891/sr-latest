#!/usr/bin/env python3
"""Quick test of Ursina 3D arm visualization"""

import time
import math
from ursina import *

class RoboticArm3D(Entity):
    """Real-time 3D surgical robotic arm with Ursina/Panda3D"""
    
    def __init__(self):
        super().__init__()
        
        self.base_angle = 0        
        self.shoulder = 45         
        self.elbow = 45            
        self.wrist = 0             
        
        self.base_height = 2
        self.shoulder_link = 3
        self.elbow_link = 2.5
        self.wrist_link = 1.5
        
        self.angle_speed = 3
        self.base_platform = None
        self.link_s = None
        self.link_e = None
        self.link_w = None
        self.joint_visuals = {}
        self.trajectory_points = []
        self.trajectory_lines = []
        
        self.setup_arm()
    
    def setup_arm(self):
        """Create 3D arm geometry"""
        self.base_platform = Entity(
            model='cylinder',
            scale=(1.2, 0.15, 1.2),
            color=color.dark_gray,
            position=(0, 0, 0)
        )
        
        self.joint_visuals[0] = Entity(
            model='sphere',
            scale=0.3,
            color=color.red,
            position=(0, -self.base_height, 0)
        )
        
        self.link_s = Entity(
            model='cylinder',
            scale=(0.2, self.shoulder_link/2, 0.2),
            color=color.orange,
        )
        
        self.joint_visuals[1] = Entity(
            model='sphere',
            scale=0.25,
            color=color.yellow,
        )
        
        self.link_e = Entity(
            model='cylinder',
            scale=(0.18, self.elbow_link/2, 0.18),
            color=color.lime,
        )
        
        self.joint_visuals[2] = Entity(
            model='sphere',
            scale=0.22,
            color=color.cyan,
        )
        
        self.link_w = Entity(
            model='cylinder',
            scale=(0.16, self.wrist_link/2, 0.16),
            color=color.magenta,
        )
        
        self.joint_visuals[3] = Entity(
            model='sphere',
            scale=0.25,
            color=color.gold,
        )
        
        self.update_geometry()
    
    def move_up(self):
        self.shoulder = min(85, self.shoulder + self.angle_speed)
        
    def move_down(self):
        self.shoulder = max(-85, self.shoulder - self.angle_speed)
        
    def move_left(self):
        self.base_angle = (self.base_angle - self.angle_speed) % 360
        
    def move_right(self):
        self.base_angle = (self.base_angle + self.angle_speed) % 360
    
    def calculate_joint_positions(self):
        joints = []
        
        base_pos = (0, -self.base_height, 0)
        joints.append(base_pos)
        
        shoulder_rad = math.radians(self.shoulder)
        shoulder_y = -self.base_height + self.shoulder_link * math.cos(shoulder_rad)
        shoulder_z = self.shoulder_link * math.sin(shoulder_rad)
        
        base_rad = math.radians(self.base_angle)
        shoulder_x = 0
        shoulder_y_rot = shoulder_y * math.cos(base_rad)
        shoulder_z_rot = shoulder_z
        
        shoulder_pos = (shoulder_x, shoulder_y_rot, shoulder_z_rot)
        joints.append(shoulder_pos)
        
        cumulative_angle = self.shoulder + self.elbow
        cumulative_rad = math.radians(cumulative_angle)
        
        elbow_y = shoulder_y_rot + self.elbow_link * math.cos(cumulative_rad)
        elbow_z = shoulder_z_rot + self.elbow_link * math.sin(cumulative_rad)
        elbow_x = shoulder_x
        
        elbow_pos = (elbow_x, elbow_y, elbow_z)
        joints.append(elbow_pos)
        
        wrist_rad = math.radians(cumulative_angle + self.wrist)
        
        wrist_y = elbow_y + self.wrist_link * math.cos(wrist_rad)
        wrist_z = elbow_z + self.wrist_link * math.sin(wrist_rad)
        wrist_x = elbow_x
        
        wrist_pos = (wrist_x, wrist_y, wrist_z)
        joints.append(wrist_pos)
        
        return joints
    
    def update_geometry(self):
        joints = self.calculate_joint_positions()
        
        self.base_platform.rotation = (0, self.base_angle, 0)
        
        self.joint_visuals[0].position = joints[0]
        
        mid_s = ((joints[0][0] + joints[1][0])/2, 
                 (joints[0][1] + joints[1][1])/2,
                 (joints[0][2] + joints[1][2])/2)
        self.link_s.position = mid_s
        angle = math.degrees(math.atan2(
            joints[1][2] - joints[0][2],
            joints[1][1] - joints[0][1]
        ))
        self.link_s.rotation = (angle, self.base_angle, 0)
        
        self.joint_visuals[1].position = joints[1]
        
        mid_e = ((joints[1][0] + joints[2][0])/2,
                 (joints[1][1] + joints[2][1])/2,
                 (joints[1][2] + joints[2][2])/2)
        self.link_e.position = mid_e
        angle = math.degrees(math.atan2(
            joints[2][2] - joints[1][2],
            joints[2][1] - joints[1][1]
        ))
        self.link_e.rotation = (angle, self.base_angle, 0)
        
        self.joint_visuals[2].position = joints[2]
        
        mid_w = ((joints[2][0] + joints[3][0])/2,
                 (joints[2][1] + joints[3][1])/2,
                 (joints[2][2] + joints[3][2])/2)
        self.link_w.position = mid_w
        angle = math.degrees(math.atan2(
            joints[3][2] - joints[2][2],
            joints[3][1] - joints[2][1]
        ))
        self.link_w.rotation = (angle, self.base_angle, 0)
        
        self.joint_visuals[3].position = joints[3]
        
        self.trajectory_points.append(joints[3])
        if len(self.trajectory_points) > 100:
            self.trajectory_points.pop(0)


app = Ursina(
    title='🦾 3D Robotic Arm Test',
    icon='',
    fullscreen=False,
    size=(1200, 900)
)

camera.position = (6, 3, 6)
camera.look_at((0, 0, 0))

DirectionalLight(direction=(1, 1, 1), shadows=True)
AmbientLight(intensity=0.6)

Sky(texture='white_cube')

print("Creating arm...")
arm = RoboticArm3D()

info_text = Text(
    text="Test Ursina 3D - Rotating arm ...",
    origin=(0, 0),
    font='arial.ttf',
    size=16,
    color=color.white(),
    background=True,
    background_color=color.dark_gray()
)

commands = [
    ('up', 8),
    ('right', 6),
    ('down', 5),
    ('left', 7),
]

cmd_index = [0]
frame = [0]

def update():
    frame[0] += 1
    
    if frame[0] % 2 == 0:
        cmd_name, count = commands[cmd_index[0] % len(commands)]
        
        for _ in range(count):
            if cmd_name == 'up':
                arm.move_up()
            elif cmd_name == 'down':
                arm.move_down()
            elif cmd_name == 'left':
                arm.move_left()
            elif cmd_name == 'right':
                arm.move_right()
            
            arm.update_geometry()
        
        info_text.text = f"Command: {cmd_name} | Base: {arm.base_angle:.0f}° | Shoulder: {arm.shoulder:.0f}°"
        
        cmd_index[0] += 1
        
        if cmd_index[0] >= 8:
            application.quit()

app.update_task(update)
