import { useEffect, useRef, useState } from "react";
import "./App.css";

function App() {
  // =========================================================
  // VOICE STATE
  // =========================================================

  const [listening, setListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(true);

  // =========================================================
  // CONTROL MODE
  // =========================================================

  const [controlMode, setControlMode] = useState(null);
  const controlModeRef = useRef(null);


  // =========================================================
  // BACKEND STATUS
  // =========================================================

  const [backendStatus, setBackendStatus] = useState({
    connection: "Connecting...",
    control: "---",
    camera: "---",
    voice: "---",
    gesture: "NONE",
    last_command: "---",
    pan: 0,
    tilt: 0,
    position_x: 0,
    position_y: 0,
  });

  const [backendError, setBackendError] = useState("");

  const [voiceMessage, setVoiceMessage] = useState(
    "Voice control is ready"
  );

  const [transcript, setTranscript] = useState("");
  const [lastCommand, setLastCommand] = useState("No command yet");
  const [history, setHistory] = useState([]);

  const recognitionRef = useRef(null);
  const shouldListenRef = useRef(false);
  const restartTimerRef = useRef(null);

  // Tracks the last command reported by FastAPI so gesture-generated
  // commands can be added to the React command history exactly once.
  const lastBackendCommandRef = useRef(null);

  const selectControlMode = async (mode) => {
    try {
      const response = await fetch(
        "http://127.0.0.1:8000/api/mode",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ mode }),
        }
      );

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.error || `Backend returned HTTP ${response.status}`);
      }

      controlModeRef.current = mode;
      setControlMode(mode);

      if (mode !== "voice") {
        shouldListenRef.current = false;
        clearTimeout(restartTimerRef.current);

        if (recognitionRef.current) {
          try {
            recognitionRef.current.stop();
          } catch (error) {
            console.log("Voice stop:", error.message);
          }
        }

        setListening(false);
        setVoiceMessage("Gesture Control selected");
      } else {
        setVoiceMessage("Voice Control selected");
      }

      setBackendError("");
    } catch (error) {
      console.error("Control mode error:", error);
      setBackendError("Could not change control mode");
      setVoiceMessage(`Unable to select ${mode} control`);
    }
  };

  // =========================================================
  // BACKEND STATUS
  // =========================================================

  useEffect(() => {
    let mounted = true;

    const fetchBackendStatus = async () => {
      try {
        const response = await fetch(
          "http://127.0.0.1:8000/api/status"
        );

        if (!response.ok) {
          throw new Error(
            `Backend returned HTTP ${response.status}`
          );
        }

        const data = await response.json();

        if (!mounted) {
          return;
        }

        setBackendStatus(data);

        // Pick up commands produced by the Python gesture service.
        // The backend status is polled every second, so a new gesture
        // command becomes visible in React even without a WebSocket.
        if (data.last_command && data.last_command !== "---") {
          const previousBackendCommand = lastBackendCommandRef.current;

          if (
            previousBackendCommand !== null &&
            previousBackendCommand !== data.last_command
          ) {
            setHistory((previous) => [
              {
                time: new Date().toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                }),
                command: data.last_command,
                status: "Detected",
              },
              ...previous,
            ]);
            setLastCommand(data.last_command);
          }

          lastBackendCommandRef.current = data.last_command;
        }

        setBackendError("");
      } catch (error) {
        console.error(
          "Backend status error:",
          error
        );

        if (!mounted) {
          return;
        }

        setBackendError(
          "Backend connection unavailable"
        );

        setBackendStatus((previous) => ({
          ...previous,
          connection: "Disconnected",
        }));
      }
    };

    fetchBackendStatus();

    const interval = setInterval(
      fetchBackendStatus,
      1000
    );

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  // =========================================================
  // SPEECH RECOGNITION SETUP
  // =========================================================

  useEffect(() => {
    const SpeechRecognition =
      window.SpeechRecognition ||
      window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
      console.error(
        "Speech Recognition API is not supported by this browser."
      );

      setVoiceSupported(false);

      setVoiceMessage(
        "Speech recognition is not supported by this browser"
      );

      return;
    }

    console.log("Speech Recognition API detected.");

    const recognition = new SpeechRecognition();

    // ---------------------------------------------------------
    // Recognition configuration
    // ---------------------------------------------------------

    recognition.lang = "en-US";

    /*
     * We intentionally use one phrase at a time.
     *
     * This is more reliable for commands such as:
     *
     * camera left
     * camera right
     * camera up
     * camera down
     */
    recognition.continuous = false;

    /*
     * We want the final recognized command.
     */
    recognition.interimResults = false;

    /*
     * Ask the browser for more than one possible result.
     */
    recognition.maxAlternatives = 3;

    // =========================================================
    // AUDIO START
    // =========================================================

    recognition.onaudiostart = () => {
      console.log("Audio capture started.");

      setVoiceMessage(
        "Microphone active — speak a command"
      );
    };

    // =========================================================
    // SPEECH START
    // =========================================================

    recognition.onspeechstart = () => {
      console.log("Speech detected.");

      setVoiceMessage(
        "Speech detected — processing..."
      );
    };

    // =========================================================
    // SPEECH END
    // =========================================================

    recognition.onspeechend = () => {
      console.log("Speech ended.");
    };

    // =========================================================
    // AUDIO END
    // =========================================================

    recognition.onaudioend = () => {
      console.log("Audio capture ended.");
    };

    // =========================================================
    // RESULT
    // =========================================================

    recognition.onresult = (event) => {
      console.log(
        "Speech recognition result:",
        event
      );

      let recognizedText = "";

      for (
        let i = event.resultIndex;
        i < event.results.length;
        i++
      ) {
        const result = event.results[i];

        if (result && result[0]) {
          recognizedText += result[0].transcript + " ";
        }
      }

      recognizedText = recognizedText
        .trim()
        .toLowerCase();

      console.log(
        "Recognized speech:",
        recognizedText
      );

      if (!recognizedText) {
        setVoiceMessage(
          "No command detected — please try again"
        );

        return;
      }

      setTranscript(recognizedText);

      setVoiceMessage(
        `Heard: "${recognizedText}"`
      );

      processVoiceCommand(recognizedText);
    };

    // =========================================================
    // NO MATCH
    // =========================================================

    recognition.onnomatch = () => {
      console.log(
        "Speech detected but no recognized result matched."
      );

      setVoiceMessage(
        "Speech detected, but command was not understood"
      );
    };

    // =========================================================
    // ERROR
    // =========================================================

    recognition.onerror = (event) => {
      console.error(
        "Speech recognition error:",
        event.error
      );

      console.error(
        "Full speech error event:",
        event
      );

      // -------------------------------------------------------
      // Permission problem
      // -------------------------------------------------------

      if (event.error === "not-allowed") {
        setVoiceMessage(
          "Microphone permission denied"
        );

        shouldListenRef.current = false;
        setListening(false);

        return;
      }

      // -------------------------------------------------------
      // No speech
      // -------------------------------------------------------

      if (event.error === "no-speech") {
        console.log(
          "No speech detected by browser."
        );

        setVoiceMessage(
          "No speech detected — please speak clearly"
        );

        return;
      }

      // -------------------------------------------------------
      // Network problem
      // -------------------------------------------------------

      if (event.error === "network") {
        setVoiceMessage(
          "Speech recognition network error"
        );

        return;
      }

      // -------------------------------------------------------
      // Other errors
      // -------------------------------------------------------

      setVoiceMessage(
        `Speech error: ${event.error}`
      );
    };

    // =========================================================
    // RECOGNITION END
    // =========================================================

    recognition.onend = () => {
      console.log(
        "Speech recognition ended."
      );

      /*
       * If the user still wants voice control active,
       * start another recognition session.
       */
      if (shouldListenRef.current) {
        console.log(
          "Preparing next speech recognition session..."
        );

        clearTimeout(restartTimerRef.current);

        restartTimerRef.current = setTimeout(() => {
          try {
            recognition.start();

            console.log(
              "Speech recognition restarted."
            );

            setListening(true);

            setVoiceMessage(
              "Listening for commands..."
            );
          } catch (error) {
            console.log(
              "Recognition restart skipped:",
              error.message
            );
          }
        }, 400);
      } else {
        setListening(false);
      }
    };

    recognitionRef.current = recognition;

    // =========================================================
    // CLEANUP
    // =========================================================

    return () => {
      shouldListenRef.current = false;

      clearTimeout(restartTimerRef.current);

      try {
        recognition.stop();
      } catch (error) {
        console.log(
          "Recognition cleanup:",
          error.message
        );
      }
    };
  }, []);

  // =========================================================
  // COMMAND HISTORY
  // =========================================================

  const addCommandToHistory = async (command, sourceOverride = null) => {
    const source = sourceOverride || controlModeRef.current;
    const newCommand = {
      time: new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),

      command: command,

      status: "Sending",
    };

    setHistory((previous) => [
      newCommand,
      ...previous,
    ]);

    setLastCommand(command);

    try {
      const response = await fetch(
        "http://127.0.0.1:8000/api/command",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            command: command,
            source: source || "frontend",
          }),
        }
      );

      if (!response.ok) {
        throw new Error(
          `Backend returned HTTP ${response.status}`
        );
      }

      const data = await response.json();

      console.log(
        "Backend command response:",
        data
      );

      setHistory((previous) =>
        previous.map((item, index) =>
          index === 0 &&
          item.command === command
            ? {
                ...item,
                status: data.success
                  ? "Sent"
                  : "Rejected",
              }
            : item
        )
      );

      setBackendError("");
    } catch (error) {
      console.error(
        "Command send error:",
        error
      );

      setBackendError(
        "Command could not reach backend"
      );

      setHistory((previous) =>
        previous.map((item, index) =>
          index === 0 &&
          item.command === command
            ? {
                ...item,
                status: "Failed",
              }
            : item
        )
      );
    }
  };

  // =========================================================
  // VOICE COMMAND PROCESSOR
  // =========================================================

  const processVoiceCommand = (spokenText) => {
    if (controlModeRef.current !== "voice") {
      console.log("Voice command ignored - Gesture mode is active.");
      return;
    }

    const command = spokenText
      .toLowerCase()
      .trim();

    console.log(
      "Processing voice command:",
      command
    );

    // =======================================================
    // CAMERA UP
    // =======================================================

    if (
      command.includes("camera up") ||
      command === "up" ||
      command.includes("move up") ||
      command.includes("go up")
    ) {
      addCommandToHistory("Camera Up", "voice");

      setVoiceMessage(
        "Command detected: Camera Up"
      );

      return;
    }

    // =======================================================
    // CAMERA DOWN
    // =======================================================

    if (
      command.includes("camera down") ||
      command === "down" ||
      command.includes("move down") ||
      command.includes("go down")
    ) {
      addCommandToHistory("Camera Down", "voice");

      setVoiceMessage(
        "Command detected: Camera Down"
      );

      return;
    }

    // =======================================================
    // CAMERA LEFT
    // =======================================================

    if (
      command.includes("camera left") ||
      command.includes("camera lift") ||
      command.includes("move left") ||
      command.includes("go left") ||
      command === "left" ||
      command === "lift"
    ) {
      addCommandToHistory("Camera Left", "voice");

      setVoiceMessage(
        "Command detected: Camera Left"
      );

      return;
    }

    // =======================================================
    // CAMERA RIGHT
    // =======================================================

    if (
      command.includes("camera right") ||
      command.includes("move right") ||
      command.includes("go right") ||
      command === "right"
    ) {
      addCommandToHistory("Camera Right", "voice");

      setVoiceMessage(
        "Command detected: Camera Right"
      );

      return;
    }

    // =======================================================
    // CAMERA HOME
    // =======================================================

    if (
      command.includes("camera home") ||
      command.includes("go home") ||
      command === "home"
    ) {
      addCommandToHistory("Camera Home", "voice");

      setVoiceMessage(
        "Command detected: Camera Home"
      );

      return;
    }

    // =======================================================
    // EMERGENCY STOP
    // =======================================================

    if (
      command.includes("emergency stop") ||
      command.includes("camera stop") ||
      command === "stop"
    ) {
      addCommandToHistory("Emergency Stop", "voice");

      setVoiceMessage(
        "Command detected: Emergency Stop"
      );

      return;
    }

    // =======================================================
    // UNKNOWN COMMAND
    // =======================================================

    console.log(
      "Unknown voice command:",
      command
    );

    setLastCommand(
      `"${spokenText}"`
    );

    setVoiceMessage(
      `Command not recognized: "${spokenText}"`
    );
  };

  // =========================================================
  // START / STOP LISTENING
  // =========================================================

  const toggleListening = () => {
    if (controlModeRef.current !== "voice") {
      setVoiceMessage("Please select Voice Control first.");
      return;
    }

    const recognition =
      recognitionRef.current;

    if (!recognition) {
      setVoiceMessage(
        "Speech recognition is unavailable"
      );

      return;
    }

    // =======================================================
    // STOP
    // =======================================================

    if (listening) {
      console.log(
        "Stopping voice recognition..."
      );

      shouldListenRef.current = false;

      clearTimeout(
        restartTimerRef.current
      );

      try {
        recognition.stop();
      } catch (error) {
        console.log(
          "Stop error:",
          error.message
        );
      }

      setListening(false);

      setVoiceMessage(
        "Voice control stopped"
      );

      return;
    }

    // =======================================================
    // START
    // =======================================================

    console.log(
      "Starting voice recognition..."
    );

    setTranscript("");

    shouldListenRef.current = true;

    try {
      recognition.start();

      setListening(true);

      setVoiceMessage(
        "Listening for commands..."
      );
    } catch (error) {
      console.error(
        "Start recognition error:",
        error
      );

      setListening(false);

      setVoiceMessage(
        `Unable to start voice recognition: ${error.message}`
      );
    }
  };

  // =========================================================
  // MANUAL CAMERA COMMANDS
  // =========================================================

  const executeCommand = (command) => {
    if (!controlMode) {
      setVoiceMessage("Please select Voice or Gesture Control first.");
      return;
    }

    console.log(
      "Manual command selected:",
      command
    );

    addCommandToHistory(command, controlModeRef.current);

    setVoiceMessage(
      `Command selected: ${command}`
    );
  };

  // =========================================================
  // UI
  // =========================================================

  return (
    <div className="app">

      {/* =====================================================
          HEADER
      ====================================================== */}

      <header className="header">

        <div>

          <h1>
            Robotic Camera System
          </h1>

          <p>
            Voice-Controlled Camera & Robotic Arm
          </p>

        </div>

        <div className="system-status">

          <span className="status-dot"></span>

          FRONTEND ONLINE

        </div>

      </header>


      {/* =====================================================
          DASHBOARD
      ====================================================== */}

      <main className="dashboard">

        {/* ===================================================
            CONTROL MODE
        ==================================================== */}

        <section className="card mode-card">
          <div className="card-header">
            <div>
              <h2>Control Mode</h2>
              <p>Select one control method before operating</p>
            </div>

            <span className="waiting-badge">
              {controlMode
                ? controlMode === "voice"
                  ? "VOICE"
                  : "GESTURE"
                : "SELECT MODE"}
            </span>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "16px",
              marginTop: "20px",
            }}
          >
            <button
              onClick={() => selectControlMode("voice")}
              style={{
                padding: "22px",
                borderRadius: "14px",
                border:
                  controlMode === "voice"
                    ? "3px solid #2563eb"
                    : "2px solid #d1d5db",
                background:
                  controlMode === "voice"
                    ? "#eff6ff"
                    : "#ffffff",
                cursor: "pointer",
                fontSize: "18px",
                fontWeight: "700",
              }}
            >
              🎤
              <br />
              VOICE CONTROL
            </button>

            <button
              onClick={() => selectControlMode("gesture")}
              style={{
                padding: "22px",
                borderRadius: "14px",
                border:
                  controlMode === "gesture"
                    ? "3px solid #2563eb"
                    : "2px solid #d1d5db",
                background:
                  controlMode === "gesture"
                    ? "#eff6ff"
                    : "#ffffff",
                cursor: "pointer",
                fontSize: "18px",
                fontWeight: "700",
              }}
            >
              ✋
              <br />
              GESTURE CONTROL
            </button>
          </div>

          <div style={{ marginTop: "16px", textAlign: "center" }}>
            {controlMode ? (
              <strong>
                Active Mode:{" "}
                {controlMode === "voice"
                  ? "🎤 Voice Control"
                  : "✋ Gesture Control"}
              </strong>
            ) : (
              <span>
                Please select Voice or Gesture Control before operating.
              </span>
            )}
          </div>
        </section>

        {/* ===================================================
            LIVE CAMERA
        ==================================================== */}

        <section className="card camera-card">

          <div className="card-header">

            <div>

              <h2>
                Live Camera
              </h2>

              <span className="small-status">

                <span className={backendStatus.camera === "Ready" ? "status-dot" : "neutral-dot"}></span>

                {backendStatus.camera === "Ready"
                  ? "Camera ready"
                  : backendStatus.camera === "Unavailable"
                  ? "Camera unavailable"
                  : "Waiting for camera connection"}

              </span>

            </div>

            <span className="waiting-badge">
              {backendStatus.connection === "Connected"
                ? "ONLINE"
                : "WAITING"}
            </span>

          </div>


          <div className="camera-screen">

            {controlMode === "gesture" ? (
              <img
                src="http://127.0.0.1:8000/api/gesture/video"
                alt="Gesture camera feed"
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  display: "block",
                }}
              />
            ) : (
              <div className="camera-placeholder">
                <div className="camera-icon">
                  ◉
                </div>

                <h3>
                  {controlMode === "voice"
                    ? "Camera Feed Standby"
                    : "Camera Feed"}
                </h3>

                <p>
                  {controlMode === "voice"
                    ? "Select Gesture Control to start the vision pipeline."
                    : "Select Voice or Gesture Control to begin."}
                </p>
              </div>
            )}

            <div className="camera-overlay">

              <span>
                CAM-01
              </span>

              <span>
                {backendStatus.gesture || "---"}
              </span>

            </div>

          </div>

        </section>


        {/* ===================================================
            CAMERA CONTROL
        ==================================================== */}

        <section className="card controls-card">

          <div className="card-header">

            <div>

              <h2>
                Camera Control
              </h2>

              <p>
                Directional movement
              </p>

            </div>

          </div>


          <div className="direction-pad">

            {/* UP */}

            <button
              className="direction-button"
              onClick={() =>
                executeCommand("Camera Up")
              }
            >
              ↑
            </button>


            <div className="middle-controls">

              {/* LEFT */}

              <button
                className="direction-button"
                onClick={() =>
                  executeCommand("Camera Left")
                }
              >
                ←
              </button>


              {/* CENTER */}

              <button
                className="center-button"
                onClick={() =>
                  executeCommand("Camera Center")
                }
              >
                ●
              </button>


              {/* RIGHT */}

              <button
                className="direction-button"
                onClick={() =>
                  executeCommand("Camera Right")
                }
              >
                →
              </button>

            </div>


            {/* DOWN */}

            <button
              className="direction-button"
              onClick={() =>
                executeCommand("Camera Down")
              }
            >
              ↓
            </button>

          </div>


          <div className="control-actions">

            <button
              className="secondary-button"
              onClick={() =>
                executeCommand("Camera Home")
              }
            >
              HOME
            </button>


            <button
              className="danger-button"
              onClick={() =>
                executeCommand("Emergency Stop")
              }
            >
              STOP
            </button>

          </div>

        </section>


        {/* ===================================================
            VOICE CONTROL
        ==================================================== */}

        <section className="card voice-card">

          <div className="card-header">

            <div>

              <h2>
                Voice Control
              </h2>

              <p>
                Browser microphone voice interface
              </p>

            </div>

          </div>


          {/* MICROPHONE */}

          <div
            className={`microphone ${
              listening ? "active" : ""
            }`}
          >
            🎤
          </div>


          {/* VOICE STATUS */}

          <div
            className={`voice-status ${
              listening
                ? "voice-active"
                : ""
            }`}
          >
            {voiceMessage}
          </div>


          {/* LIVE TRANSCRIPT */}

          <div
            style={{
              background: "#f6f8fa",
              borderRadius: "10px",
              padding: "14px",
              marginBottom: "15px",
              minHeight: "45px",
              color: "#35404b",
              fontSize: "13px",
            }}
          >

            <strong>
              Live Transcript:
            </strong>

            <div
              style={{
                marginTop: "6px",
                color: "#68727d",
              }}
            >
              {transcript ||
                "Waiting for speech..."}
            </div>

          </div>


          {/* START / STOP */}

          <button
            className={`listen-button ${
              listening
                ? "listening"
                : ""
            }`}
            onClick={toggleListening}
            disabled={!voiceSupported}
          >

            {!voiceSupported
              ? "VOICE NOT SUPPORTED"
              : listening
              ? "STOP LISTENING"
              : "START LISTENING"}

          </button>


          {/* LAST COMMAND */}

          <div className="last-command">

            <span>
              Last Command
            </span>

            <strong>
              {lastCommand}
            </strong>

          </div>

        </section>


        {/* ===================================================
            ROBOT STATUS
        ==================================================== */}

        <section className="card robot-card">

          <div className="card-header">

            <div>

              <h2>
                Robot Status
              </h2>

              <p>
                Current robotic system state
              </p>

            </div>

            <span className="waiting-badge">
              WAITING
            </span>

          </div>


          {/* SYSTEM STATUS */}

          <div className="status-list">

            <div className="status-item">

              <span>
                Connection
              </span>

              <strong
                className={
                  backendStatus.connection === "Connected"
                    ? "status-active"
                    : "status-neutral"
                }
              >
                {backendStatus.connection}
              </strong>

            </div>


            <div className="status-item">

              <span>
                Control
              </span>

              <strong className="status-active">
                {backendStatus.control}
              </strong>

            </div>


            <div className="status-item">

              <span>
                Camera
              </span>

              <strong className="status-active">
                {backendStatus.camera}
              </strong>

            </div>


            <div className="status-item">

              <span>
                Voice
              </span>

              <strong className="status-active">
                {voiceSupported
                  ? "Ready"
                  : "Unavailable"}
              </strong>

            </div>

          </div>


          {/* ROBOT TELEMETRY */}

          <div className="telemetry-section">

            <h3>
              Robot Telemetry
            </h3>


            <div className="robot-data">

              <div className="robot-value">

                <span>
                  Pan
                </span>

                <strong>
                  {Number(backendStatus.pan || 0).toFixed(1)}
                </strong>

              </div>


              <div className="robot-value">

                <span>
                  Tilt
                </span>

                <strong>
                  {Number(backendStatus.tilt || 0).toFixed(1)}
                </strong>

              </div>


              <div className="robot-value">

                <span>
                  Position X
                </span>

                <strong>
                  {Number(backendStatus.position_x || 0).toFixed(1)}
                </strong>

              </div>


              <div className="robot-value">

                <span>
                  Position Y
                </span>

                <strong>
                  {Number(backendStatus.position_y || 0).toFixed(1)}
                </strong>

              </div>

            </div>

          </div>


          {/* ROBOT CONNECTION STATE */}

          <div className="robot-state">

            <span
              className={
                backendStatus.connection === "Connected"
                  ? "status-dot"
                  : "neutral-dot"
              }
            ></span>

            {backendError ||
              (backendStatus.connection === "Connected"
                ? "Backend connected"
                : "Waiting for backend connection")}

          </div>

        </section>


        {/* ===================================================
            COMMAND HISTORY
        ==================================================== */}

        <section className="card history-card">

          <div className="card-header">

            <div>

              <h2>
                Command History
              </h2>

              <p>
                Commands detected or selected
              </p>

            </div>

            <span className="history-count">

              {history.length} commands

            </span>

          </div>


          <div className="history-table">

            {/* TABLE HEADER */}

            <div className="history-row history-header">

              <span>
                TIME
              </span>

              <span>
                COMMAND
              </span>

              <span>
                STATUS
              </span>

            </div>


            {/* EMPTY STATE */}

            {history.length === 0 ? (

              <div className="empty-history">
                No commands yet
              </div>

            ) : (

              history.map(
                (item, index) => (

                  <div
                    className="history-row"
                    key={index}
                  >

                    <span>
                      {item.time}
                    </span>

                    <span>
                      {item.command}
                    </span>

                    <span className="completed">
                      {item.status}
                    </span>

                  </div>

                )
              )

            )}

          </div>

        </section>

      </main>

    </div>
  );
}

export default App;