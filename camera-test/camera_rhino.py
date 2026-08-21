import time
import threading
import queue
import pvrhino
from pvrecorder import PvRecorder
import math
import os
import json

audio_queue = queue.Queue()
command_queue = queue.Queue()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTEXT_PATH = os.path.join(SCRIPT_DIR, 'Camera_en_mac_v4_0_0.rhn')
STATE_FILE = os.path.join(SCRIPT_DIR, 'arm_state.json')


class RoboticArm:
    """Command-based robotic arm (NO graphics - terminal only)"""
    
    def __init__(self):
        self.base_angle = 0        
        self.shoulder = 45         
        self.elbow = 45            
        self.wrist = 0             
        self.angle_speed = 5
        self.save_state()  # Initialize state file
    
    def save_state(self):
        """Save arm state to JSON file for 3D UI"""
        state = {
            'base_angle': self.base_angle,
            'shoulder': self.shoulder,
            'elbow': self.elbow,
            'wrist': self.wrist
        }
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
        except:
            pass
    
    def move_up(self):
        self.shoulder = min(85, self.shoulder + self.angle_speed)
        self.save_state()
        
    def move_down(self):
        self.shoulder = max(-85, self.shoulder - self.angle_speed)
        self.save_state()
        
    def move_left(self):
        self.base_angle = (self.base_angle - self.angle_speed) % 360
        self.save_state()
        
    def move_right(self):
        self.base_angle = (self.base_angle + self.angle_speed) % 360
        self.save_state()
    
    def get_status(self):
        """Return current arm state"""
        return {
            'base_angle': self.base_angle,
            'shoulder': self.shoulder,
            'elbow': self.elbow,
            'wrist': self.wrist
        }


# Initialize Rhino
rhino = pvrhino.create(
    access_key='DrXmrvu3jdmujWvWdz/9d0T3sZ4fP26i+8DS6vc5BUCp8nd9zHM3EQ==',
    context_path=CONTEXT_PATH
)

recorder = PvRecorder(device_index=-1, frame_length=rhino.frame_length)
recorder.start()

print("=" * 70)
print("🦾 ROBOTIC ARM - COMMAND RECOGNITION (Terminal Only)")
print("=" * 70)
print(f"🎙️  Device: {recorder.selected_device}")
print(f"📢 Ready for voice commands...")
print()
print("💡 To see 3D visualization, run in another terminal:")
print("   /Users/pmk/Desktop/Targets/SR/.venv/bin/python ui_3d.py")
print()
print("Listening for commands:")
print("  • 'camera up'")
print("  • 'camera down'")
print("  • 'camera left'")
print("  • 'camera right'")
print()

def audio_capture():
    while True:
        try:
            frame = recorder.read()
            audio_queue.put(frame)
        except Exception as e:
            break

def inference_worker():
    command_start_time = None
    while True:
        frame = audio_queue.get()
        if command_start_time is None:
            command_start_time = time.time()
        is_finalized = rhino.process(frame)
        if is_finalized:
            inference = rhino.get_inference()
            total_time = time.time() - command_start_time
            if inference.is_understood:
                command_queue.put(inference.slots)
            command_start_time = None

# Start threads
t1 = threading.Thread(target=audio_capture, daemon=True)
t2 = threading.Thread(target=inference_worker, daemon=True)
t1.start()
t2.start()

# Create arm
arm = RoboticArm()

# Main loop - command processing only
try:
    while True:
        try:
            slots = command_queue.get(timeout=1)
            direction = slots.get('direction', '')
            
            if direction == 'up':
                arm.move_up()
                state = arm.get_status()
                print(f"✅ UP      | Shoulder: {state['shoulder']:6.0f}° | Base: {state['base_angle']:6.0f}° | Elbow: {state['elbow']:6.0f}° | Wrist: {state['wrist']:6.0f}°")
            elif direction == 'down':
                arm.move_down()
                state = arm.get_status()
                print(f"✅ DOWN    | Shoulder: {state['shoulder']:6.0f}° | Base: {state['base_angle']:6.0f}° | Elbow: {state['elbow']:6.0f}° | Wrist: {state['wrist']:6.0f}°")
            elif direction == 'left':
                arm.move_left()
                state = arm.get_status()
                print(f"✅ LEFT    | Shoulder: {state['shoulder']:6.0f}° | Base: {state['base_angle']:6.0f}° | Elbow: {state['elbow']:6.0f}° | Wrist: {state['wrist']:6.0f}°")
            elif direction == 'right':
                arm.move_right()
                state = arm.get_status()
                print(f"✅ RIGHT   | Shoulder: {state['shoulder']:6.0f}° | Base: {state['base_angle']:6.0f}° | Elbow: {state['elbow']:6.0f}° | Wrist: {state['wrist']:6.0f}°")
        except queue.Empty:
            pass
except KeyboardInterrupt:
    print("\n🛑 Stopping...")
finally:
    recorder.stop()
    recorder.delete()
    rhino.delete()
    print("✋ Done!")
