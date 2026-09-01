# OBS Native Audio Router Plugin

**Developer Note / Status (As of v1.0.0)**

This native C++ plugin is designed to act purely as an **ultra-low-latency audio router** for VoxStream. It does NOT run heavy AI models (like Vosk or CTranslate2) directly inside OBS, as this would bloat the plugin and cause OBS to crash if the AI engine fails.

Instead, this plugin adds an Audio Filter to OBS ("Live Speech Captions (AI)"). When attached to a specific audio source, it grabs the raw audio frames, computes the noise gate, and streams the PCM chunks via `HTTP POST` directly to the Python backend (`http://127.0.0.1:8765/api/audio/chunk`).

### ⚠️ Important Compilation Warning (macOS / Linux)

The `deps/obs-sdk` folder included in this repository contains **incomplete "mock" headers** (likely left over from a Windows IDE environment). They are missing critical OBS Studio SDK definitions (like `obs_source_get_settings`, `obs_output_t`, etc.).

**Because of this, the plugin will fail to compile on macOS or Linux out-of-the-box.**

To successfully compile this plugin on macOS/Linux, you must:
1. Clone the official [obs-studio](https://github.com/obsproject/obs-studio) repository to your local machine.
2. Install `Qt6` and `CMake`.
3. Modify the `CMakeLists.txt` in this folder to point `target_include_directories` to the real OBS Studio source code instead of the bundled `deps/obs-sdk`.

For most users, the standard Python daemon (which uses `sounddevice` to capture global audio) is highly recommended and requires zero C++ compilation. Use this plugin only if you specifically need to isolate and caption individual OBS audio sources (e.g., separating game audio from a specific microphone).
