import speech_recognition as sr

recognizer = sr.Recognizer()
mic = sr.Microphone(sample_rate=16000)

print("Calibrating microphone...")

with mic as source:
    recognizer.adjust_for_ambient_noise(source, duration=2)

print("Listening for commands...")

commands = [
    ("camera up", 1e-20),
    ("camera down", 1e-20),
    ("camera left", 1e-20),
    ("camera right", 1e-20),
]

while True:
    with mic as source:
        audio = recognizer.listen(source)

    try:
        command = recognizer.recognize_sphinx(
            audio,
            keyword_entries=commands
        )

        print("Detected:", command)

    except sr.UnknownValueError:
        print("...no command")
