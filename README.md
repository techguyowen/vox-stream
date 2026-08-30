# 🎙️ VoxStream — Real-Time OBS Live Captioner & Broadcast Suite

A high-performance, low-latency, real-time speech-to-text captioning and automation ecosystem for **OBS Studio** on Windows. Includes an integrated **In-OBS Web Settings Dashboard & Custom Dock**, **Gemini 3.5 Transcribe Live**, **NVIDIA GPU Faster-Whisper**, **Free Google Speech**, **Multi-Language Live Translation**, **1-Click Theme Gallery**, **Twitch Chat Caption Bot**, **Church & Family-Friendly Filter**, **REST & WebSocket API**, **Projector / Screen Automation**, and **Subtitle Exporter (SRT/VTT/TXT)**.

---

## ✨ Features Overview

- ⚡ **Ultra-Low Latency & High Accuracy**:
  - **🆓 Google Speech Recognition (Zero-Setup)**: 100% Free, zero-setup Google Speech engine with no API keys or accounts required (popularized by classic OBS Cloud Captions).
  - **✨ Gemini 3.5 Transcribe Live**: Google's newest and most intelligent speech AI (`gemini-3.5-transcribe-live`) with custom vocabulary support and automatic disfluency/filler-word cleanup.
  - **☁️ Google Cloud Speech-to-Text**: High-accuracy enterprise streaming with automatic punctuation.
  - **💻 Local Faster-Whisper**: 100% offline speech recognition running locally on your GPU (CUDA) or CPU.
- 🎛️ **In-OBS Web Control Dashboard & Custom Browser Dock**:
  - Full graphical control panel embedded directly inside OBS Studio (`Docks -> Custom Browser Docks`) or accessible at `http://127.0.0.1:8080/dashboard`.
  - **Live Audio VU Meter**: Real-time microphone level monitoring bar.
  - **Interactive Preview Canvas**: Instant visual feedback for fonts, colors, and word-pop animations.
- 🌐 **Real-Time Live Translation**:
  - Automatically translates spoken words into 30+ languages (*Spanish, French, German, Japanese, Portuguese, Chinese, Korean, Italian, etc.*).
  - **Dual Subtitles Mode**: Shows original spoken words with translated subtitles underneath.
- 🎨 **1-Click Theme Gallery & Typography Customizer**:
  - Pre-built themes: *Modern Clean, Cyberpunk Neon, Minimalist Cinema, Twitch Purple, Comic/Gaming Pop, Retro Terminal, YouTube CC*.
  - Google Fonts (*Montserrat, Inter, Roboto, Poppins, Oswald, Bebas Neue, Bangers*) + System fonts.
  - Sliders for Font Size (16px–72px), Max Box Width (% slider), Max Lines (1–4), Text Alignment, and Box Background Opacity.
- ⛪ **Church-Appropriate & Family-Safe Content Filter**:
  - **Tier 1 (Standard Profanities)**: Filters vulgarities, slurs, and offensive words.
  - **Tier 2 (Harsh Profanities & Vulgarities)**: Filters harsh language while safely protecting and preserving religious and sacred terms (*"Jesus Christ"*, *"Jesus"*, *"Lord"*, *"Christ"*).
  - **Tier 3 (Crude Terms)**: Filters crude slang and inappropriate terminology.
  - **Interactive In-Browser CRUD Editor**: Add/remove custom blacklist words, custom whitelists, and wholesome word replacements in clean visual tables.
  - **4 Action Modes**: Wholesome Word Replacement, Asterisk Masking (`****`), `[CENSORED]` Tag, or Drop Sentence.
- 👾 **Twitch Chat Caption Broadcaster**:
  - Automatically broadcasts live finalized captions into your Twitch chat for mobile and hearing-impaired viewers.
- 📜 **Transcript History & Subtitle Export**:
  - Live rolling session transcript with search filter.
  - 1-click export to **`.SRT`**, **`.VTT`**, and **`.TXT`** with millisecond timecodes for Premiere Pro / DaVinci Resolve.
- ⚡ **REST API & Stream Deck Integration**:
  - Full HTTP endpoints (`/api/control/start`, `/api/control/stop`, `/api/status`, `/api/config`, `/api/presets`, `/api/devices`, `/api/transcript/export`).
  - Bidirectional WebSockets (`/ws` for captions, `/api/control/ws` for VU meter and remote telemetry).
- 🤖 **Zero-Touch OBS Auto-Start**:
  - OBS Python Script (`obs_script/obs_live_captions.py`) that hooks into OBS events to start/stop the captioner automatically.
- 🔌 **Native OBS C++ Plugin Source**:
  - Includes full CMake C++ project (`obs_native_plugin/`) for building a native `.dll` filter plugin.

---

## 📋 Quick Start Guide (Windows)

### 1. Automated Installation
1. Open the project folder and double-click **`setup_windows.bat`**.
2. **`setup_windows.bat`** automatically:
   - Detects if Python is installed (if missing, installs Python 3.11 automatically via Windows `winget`).
   - Creates the `.venv` virtual environment and installs all dependencies.
   - Generates `config.json`.
   - Lists your audio input devices and their numbers.

---

### 2. Launching the Suite
Double-click **`run_captioner.bat`** (or run `python -m obs_captioner.main`).

Once running:
- **Web Settings Dashboard & OBS Dock**: `http://127.0.0.1:8080/dashboard`
- **Transparent Browser Source Overlay**: `http://127.0.0.1:8080/`
- **Gemini 3.5 Transcribe Setup**: In the dashboard's **🎙️ Audio & Engine** tab, paste your Google AI Studio API key to activate real-time Gemini 3.5 Transcribe streaming (see [INSTALL_GUIDE.md](file:///Users/techguyowen/Documents/antigravity/delightful-bohr/INSTALL_GUIDE.md#option-b-google-gemini-35-transcribe-live-state-of-the-art-intelligence--accuracy) for detailed steps).

---

## 🖥️ OBS Studio Setup

### 1. Add the In-OBS Control Dock (Recommended)
1. In OBS Studio, go to the top menu: **Docks -> Custom Browser Docks...**
2. **Dock Name**: `Live Captions`
3. **URL**: `http://127.0.0.1:8080/dashboard`
4. Click **Apply** and dock the panel anywhere in your OBS workspace.

---

### 2. Add the Transparent Stream Overlay
1. In your OBS Scene, click **+ (Add Source) -> Browser**.
2. **URL**: `http://127.0.0.1:8080/`
3. **Width**: `1920`, **Height**: `1080` (or match your canvas resolution).
4. Check **"Shutdown source when not visible"** and **"Refresh browser when scene becomes active"**.

---

### 3. Native Closed Captions (CEA-608 for Twitch / YouTube)
- In OBS Studio, ensure **Tools -> WebSocket Server Settings** is enabled (Port `4455`).
- The captioner sends `SendStreamCaption` requests automatically. Viewers on Twitch and YouTube can toggle captions on/off via the player's native `[CC]` button.

---

### 4. Zero-Touch Auto-Start with OBS
1. In OBS Studio, go to **Tools -> Scripts**.
2. Under **Python Settings**, select your Python install directory.
3. Under **Scripts**, click **+** and choose `obs_script/obs_live_captions.py`.
4. Check **"Auto-start when OBS launches"** or **"Auto-start when Streaming/Recording starts"**.

---

## ⚡ REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/control/reopen-screen` | 🚨 Emergency restore: Re-launches conference screen (LONTIUM) & starts captions |
| `POST` | `/api/obs/projector/open` | Opens fullscreen/windowed projector on target monitor |
| `GET` | `/api/obs/monitors` | Returns list of connected displays detected by OBS |
| `POST` | `/api/control/start` | Starts / unpauses speech captioning |
| `POST` | `/api/control/stop` | Pauses speech captioning |
| `GET` | `/api/status` | Returns system status, audio level, engine, uptime |
| `GET` | `/api/config` | Retrieves live configuration JSON |
| `POST` | `/api/config` | Updates live configuration & hot-reloads |
| `GET` | `/api/presets` | Returns all pre-built theme presets |
| `POST` | `/api/presets/apply` | Applies a visual theme preset by ID |
| `GET` | `/api/devices` | Returns list of available audio input devices |
| `GET` | `/api/filter/state` | Returns full censorship rules dictionary |
| `POST` | `/api/filter/blacklist/add` | Adds word to custom blacklist |
| `POST` | `/api/filter/whitelist/add` | Adds word to custom whitelist |
| `POST` | `/api/filter/replacements/set` | Adds/updates a wholesome word substitution |
| `GET` | `/api/transcript/history` | Returns recent transcript entries |
| `GET` | `/api/transcript/export` | Downloads subtitle file (`?format=srt\|vtt\|txt`) |
| `WS` | `/ws` | Real-time caption event broadcast stream |
| `WS` | `/api/control/ws` | Real-time telemetry, VU meter, and control stream |
