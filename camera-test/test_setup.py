#!/usr/bin/env python3
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTEXT_PATH = os.path.join(SCRIPT_DIR, 'Camera_en_mac_v4_0_0.rhn')

print(f"Script dir: {SCRIPT_DIR}")
print(f"Context path: {CONTEXT_PATH}")
print(f"Context exists: {os.path.exists(CONTEXT_PATH)}")

print("\nTesting imports...")
try:
    import pvrhino
    print("✓ pvrhino imported")
except Exception as e:
    print(f"✗ pvrhino error: {e}")
    sys.exit(1)

try:
    from pvrecorder import PvRecorder
    print("✓ PvRecorder imported")
except Exception as e:
    print(f"✗ PvRecorder error: {e}")
    sys.exit(1)

try:
    import pygame
    print("✓ pygame imported")
except Exception as e:
    print(f"✗ pygame error: {e}")
    sys.exit(1)

print("\nTesting Rhino initialization...")
try:
    rhino = pvrhino.create(
        access_key='DrXmrvu3jdmujWvWdz/9d0T3sZ4fP26i+8DS6vc5BUCp8nd9zHM3EQ==',
        context_path=CONTEXT_PATH
    )
    print(f"✓ Rhino initialized")
    print(f"  Frame length: {rhino.frame_length}")
    print(f"  Sample rate: {rhino.sample_rate}")
    rhino.delete()
except Exception as e:
    print(f"✗ Rhino error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\nTesting PvRecorder...")
try:
    devices = PvRecorder.get_available_devices()
    print(f"✓ Available devices: {devices}")
except Exception as e:
    print(f"✗ PvRecorder error: {e}")
    import traceback
    traceback.print_exc()

print("\n✓ All systems ready!")
