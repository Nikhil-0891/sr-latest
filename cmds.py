import speech_recognition as sr

recognizer = sr.Recognizer()
mic = sr.Microphone(sample_rate=16000)

print("Listening for commands...")

commands = [
    ("camera up", 1.0),
    ("camera down", 1.0),
    ("camera left", 1.0),
    ("camera right", 1.0),
]

while True:
    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source, phrase_time_limit=3)

    try:
        command = recognizer.recognize_sphinx(
            audio,
            keyword_entries=commands
        )

        print("Detected:", command)

        if command == "camera up":
            print("ACTION → Move camera up")

        elif command == "camera down":
            print("ACTION → Move camera down")

        elif command == "camera left":
            print("ACTION → Move camera left")

        elif command == "camera right":
            print("ACTION → Move camera right")

    except sr.UnknownValueError:
        pass
