#!/usr/bin/env python3
"""Test script to verify Plotly 3D arm visualization works"""

import os
import sys
import math
import numpy as np
import plotly.graph_objects as go

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

class RoboticArm3DPlotly:
    """Professional 3D surgical robotic arm using Plotly"""
    
    def __init__(self):
        self.base_angle = 0        
        self.shoulder = 45         
        self.elbow = 45            
        self.wrist = 0             
        
        self.base_height = 80
        self.shoulder_link = 100
        self.elbow_link = 90
        self.wrist_link = 50
        
        self.angle_speed = 3
        self.trajectory = []
        self.max_trajectory_points = 300
        self.html_file = os.path.join(SCRIPT_DIR, 'arm_visualization.html')
        
        print("✅ RoboticArm3DPlotly initialized")
    
    def move_up(self):
        self.shoulder = min(85, self.shoulder + self.angle_speed)
        
    def move_down(self):
        self.shoulder = max(-85, self.shoulder - self.angle_speed)
        
    def move_left(self):
        self.base_angle = (self.base_angle - self.angle_speed) % 360
        
    def move_right(self):
        self.base_angle = (self.base_angle + self.angle_speed) % 360
    
    def calculate_joint_positions(self):
        """Calculate 3D positions of all joints"""
        joints = []
        
        base_pos = np.array([0.0, -self.base_height, 0.0])
        joints.append(base_pos)
        
        shoulder_rad = math.radians(self.shoulder)
        shoulder_y = 0 + self.shoulder_link * math.cos(shoulder_rad)
        shoulder_z = self.shoulder_link * math.sin(shoulder_rad)
        
        base_rad = math.radians(self.base_angle)
        shoulder_x = 0
        shoulder_y_rot = shoulder_y * math.cos(base_rad)
        shoulder_z_rot = shoulder_z
        
        shoulder_pos = np.array([shoulder_x, shoulder_y_rot, shoulder_z_rot])
        joints.append(shoulder_pos)
        
        cumulative_angle = self.shoulder + self.elbow
        cumulative_rad = math.radians(cumulative_angle)
        
        elbow_y = shoulder_y_rot + self.elbow_link * math.cos(cumulative_rad)
        elbow_z = shoulder_z_rot + self.elbow_link * math.sin(cumulative_rad)
        elbow_x = shoulder_x
        
        elbow_pos = np.array([elbow_x, elbow_y, elbow_z])
        joints.append(elbow_pos)
        
        wrist_rad = math.radians(cumulative_angle + self.wrist)
        
        wrist_y = elbow_y + self.wrist_link * math.cos(wrist_rad)
        wrist_z = elbow_z + self.wrist_link * math.sin(wrist_rad)
        wrist_x = elbow_x
        
        wrist_pos = np.array([wrist_x, wrist_y, wrist_z])
        joints.append(wrist_pos)
        
        return joints
    
    def update_figure(self):
        """Create/update the 3D visualization"""
        fig = go.Figure()
        
        joints = self.calculate_joint_positions()
        
        link_colors = ['rgba(220, 80, 80, 1)', 'rgba(80, 140, 220, 1)', 'rgba(80, 220, 140, 1)', 'rgba(220, 160, 80, 1)']
        
        for i in range(len(joints) - 1):
            j1 = joints[i]
            j2 = joints[i + 1]
            
            fig.add_trace(go.Scatter3d(
                x=[j1[0], j2[0]], y=[j1[1], j2[1]], z=[j1[2], j2[2]],
                mode='lines',
                line=dict(color=link_colors[i], width=15),
                name=f'Link {i+1}',
                hoverinfo='name',
                showlegend=(i == 0)
            ))
        
        joint_colors = ['rgba(255, 100, 100, 0.9)', 'rgba(100, 150, 255, 0.9)', 'rgba(100, 255, 150, 0.9)', 'rgba(255, 180, 100, 0.9)']
        
        for i, joint in enumerate(joints):
            fig.add_trace(go.Scatter3d(
                x=[joint[0]], y=[joint[1]], z=[joint[2]],
                mode='markers',
                marker=dict(size=14, color=joint_colors[i], opacity=0.95, line=dict(color='darkgray', width=2)),
                name=f'Joint {i}',
                hoverinfo='text',
                hovertext=f'J{i}: ({joint[0]:.1f}, {joint[1]:.1f}, {joint[2]:.1f})',
                showlegend=False
            ))
        
        end_effector = joints[-1]
        fig.add_trace(go.Scatter3d(
            x=[end_effector[0]], y=[end_effector[1]], z=[end_effector[2]],
            mode='markers',
            marker=dict(size=20, color='gold', symbol='diamond', opacity=0.95, line=dict(color='orange', width=3)),
            name='End-Effector',
            hoverinfo='text',
            hovertext=f'Gripper: ({end_effector[0]:.1f}, {end_effector[1]:.1f}, {end_effector[2]:.1f})',
            showlegend=True
        ))
        
        self.trajectory.append(end_effector.copy())
        if len(self.trajectory) > self.max_trajectory_points:
            self.trajectory.pop(0)
        
        if len(self.trajectory) > 1:
            traj_array = np.array(self.trajectory)
            fig.add_trace(go.Scatter3d(
                x=traj_array[:, 0], y=traj_array[:, 1], z=traj_array[:, 2],
                mode='lines',
                line=dict(color='rgba(255, 50, 50, 0.5)', width=4),
                name='Trajectory',
                hoverinfo='skip',
                showlegend=True
            ))
        
        axes_length = 180
        fig.add_trace(go.Scatter3d(
            x=[0, axes_length], y=[0, 0], z=[0, 0],
            mode='lines+markers',
            line=dict(color='red', width=6),
            marker=dict(size=[0, 8]),
            name='X-Axis',
            hoverinfo='skip'
        ))
        fig.add_trace(go.Scatter3d(
            x=[0, 0], y=[0, axes_length], z=[0, 0],
            mode='lines+markers',
            line=dict(color='green', width=6),
            marker=dict(size=[0, 8]),
            name='Y-Axis',
            hoverinfo='skip'
        ))
        fig.add_trace(go.Scatter3d(
            x=[0, 0], y=[0, 0], z=[0, axes_length],
            mode='lines+markers',
            line=dict(color='blue', width=6),
            marker=dict(size=[0, 8]),
            name='Z-Axis',
            hoverinfo='skip'
        ))
        
        fig.update_layout(
            title={
                'text': (
                    f'<b>🦾 3D Surgical Robotic Arm Simulator</b><br>'
                    f'<sub>Base: {self.base_angle:.0f}° | '
                    f'Shoulder: {self.shoulder:.0f}° | '
                    f'Elbow: {self.elbow:.0f}° | '
                    f'End-Pos: ({end_effector[0]:.0f}, {end_effector[1]:.0f}, {end_effector[2]:.0f})</sub>'
                ),
                'x': 0.5,
                'xanchor': 'center',
                'font': {'size': 18}
            },
            scene=dict(
                xaxis=dict(range=[-400, 400], title='X (Red)'),
                yaxis=dict(range=[-400, 300], title='Y (Green)'),
                zaxis=dict(range=[-400, 400], title='Z (Blue)'),
                aspectmode='data',
                camera=dict(
                    eye=dict(x=1.3, y=1.2, z=1.3),
                    center=dict(x=0, y=0, z=0)
                ),
                bgcolor='rgba(240, 240, 245, 0.95)'
            ),
            width=1200,
            height=900,
            showlegend=True,
            legend=dict(x=0.02, y=0.98, bgcolor='rgba(255, 255, 255, 0.8)'),
            hovermode='closest',
            margin=dict(l=0, r=0, b=0, t=60)
        )
        
        fig.write_html(self.html_file)
        print(f"✅ Visualization saved to: {self.html_file}")
        return fig


if __name__ == "__main__":
    print("🧪 Testing Plotly 3D Arm Visualization\n")
    
    try:
        # Initialize arm
        arm = RoboticArm3DPlotly()
        
        # Simulate some movements
        print("\n📐 Simulating arm movements...")
        
        for i in range(3):
            arm.move_up()
            arm.update_figure()
            print(f"  Movement {i+1}: shoulder={arm.shoulder:.0f}°")
        
        arm.move_right()
        arm.update_figure()
        print(f"  Rotation: base={arm.base_angle:.0f}°")
        
        print("\n✅ Test completed successfully!")
        print(f"📂 HTML output: {arm.html_file}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
