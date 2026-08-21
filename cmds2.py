from pocketsphinx import LiveSpeech

speech = LiveSpeech(
    sampling_rate=16000,
    jsgf='commands.gram'
)

print("Listening for commands...")

for phrase in speech:
    command = str(phrase).strip()
    print("Detected:", command)

    if command == "camera up":
        print("ACTION → Move camera up")

    elif command == "camera down":
        print("ACTION → Move camera down")

    elif command == "camera left":
        print("ACTION → Move camera left")

    elif command == "camera right":
        print("ACTION → Move camera right")
