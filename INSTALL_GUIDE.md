# 📖 VoxStream Live Captioner — Complete Installation & Setup Guide

This step-by-step guide walks you through setting up the **VoxStream Real-Time Live Captioner Suite** on **macOS** and **Windows**.

---

## 📋 Table of Contents
1. [Prerequisites](#-1-prerequisites)
2. [Step 1: Installation & Launch](#-step-1-installation--launch)
   - [macOS / Linux Installation](#-macos--linux-setup)
   - [Windows Installation](#-windows-setup)
3. [Step 2: Choosing Your Speech Engine](#-step-2-choosing-your-speech-engine)
   - [Option A: Google Speech (100% Free / Zero-Setup)](#option-a-google-speech-recognition-100-free--zero-setup)
   - [Option B: Local Moonshine (Fast Offline Neural Model - Mac GPU / MPS)](#option-b-local-moonshine-fast-offline-ai--mac-gpu-mps)
   - [Option C: Vosk (Ultra-Fast CPU Streaming - Zero Latency)](#option-c-vosk-kaldi-ultra-fast-cpu-streaming)
   - [Option D: Google Gemini 3.5 Transcribe Live](#option-d-google-gemini-35-transcribe-live)
   - [Option E: Local Faster-Whisper (NVIDIA GPU / CUDA)](#option-e-local-faster-whisper-nvidia-gpu)
   - [Option F: Bandwidth Labs Live STT (Streaming Cloud)](#option-f-bandwidth-labs-live-stt-streaming-cloud)
4. [Step 3: Setting Up OBS Studio](#-step-3-setting-up-obs-studio)
   - [Add the Control Dock](#a-add-the-in-obs-control-dock-recommended)
   - [Add the Stream Overlay](#b-add-the-stream-caption-overlay)
   - [Add the Room / Stage Confidence Monitor](#c-add-the-room--stage-confidence-monitor)
   - [Enable Zero-Touch Auto-Start](#d-enable-zero-touch-auto-start-with-obs)
   - [Enable Native Closed Captions (CEA-608)](#e-enable-twitch--youtube-closed-captions-cea-608)
5. [Step 4: Customizing Filters, Glossary & Typography](#-step-4-customizing-filters-glossary--typography)
6. [macOS Specific Tips & Audio Routing](#-macos-specific-tips--audio-routing)
7. [Troubleshooting & FAQs](#-troubleshooting--faqs)

---

## ⚙️ 1. Prerequisites

* **Operating System**:
  * **macOS**: macOS 12 (Monterey), 13 (Ventura), 14 (Sonoma), 15 (Sequoia) on Apple Silicon (M1/M2/M3/M4) or Intel.
  * **Windows**: Windows 10 or 11 (64-bit).
* **OBS Studio**: OBS Studio 28.0 or newer (OBS WebSocket v5 is built-in by default).
* **Python**: Python 3.9, 3.10, 3.11, or 3.12.

---

## 🚀 Step 1: Installation & Launch

### 🍏 macOS & Linux Setup

1. Open **Terminal** and navigate to the project directory:
   ```bash
   cd /path/to/vox-stream
   ```
2. Run the automated setup script:
   ```bash
   ./setup_mac.sh
   ```
   *(This script automatically checks Python, initializes the isolated `.venv` environment, and installs all required dependencies).*

3. Start VoxStream:
   ```bash
   ./run_captioner.sh
   ```
   *(Leave this running in Terminal. You can now access the Web Dashboard at `http://localhost:8080/dashboard` or `http://localhost:8765/dashboard`)*.

---

### 🪟 Windows Setup

1. Open the project folder in File Explorer.
2. Double-click:
   ```cmd
   setup_windows.bat
   ```
   *(If Python is missing, the script will automatically install Python 3.11 for you via `winget`, set up `.venv`, and install dependencies).*

3. Start VoxStream:
   ```cmd
   run_captioner.bat
   ```
   *(Leave this window open in the background while broadcasting).*

---

## 🔑 Step 2: Choosing Your Speech Engine

You can select and configure your engine directly in the **Web Control Dashboard** (`/dashboard`) under the **🎙️ Audio & Engine** tab, or in `config.json`:

### Option A: Google Speech Recognition (100% Free / Zero-Setup) ⭐ DEFAULT
* **Best for**: Instant, zero-friction setup without needing API keys or cloud accounts.
* **Cost**: 100% Free.
* **How to use**: Selected by default! Simply start speaking.

---

### Option B: Local Moonshine (Fast Offline AI — Mac GPU / MPS)
* **Best for**: 100% offline, private neural transcription that is **~5x faster than Whisper**.
* **Hardware Acceleration**:
  * **On Mac**: Automatically utilizes Apple Silicon GPU/Neural Engine via PyTorch **MPS (Metal Performance Shaders)**.
  * **On Windows/Linux**: Uses NVIDIA CUDA GPU or CPU.
* **Preset Models**:
  * `moonshine/tiny`: Ultra-fast ~27M parameter model with ~50ms latency.
  * `moonshine/base`: High-accuracy ~61M parameter model with built-in punctuation.

---

### Option C: Vosk (Kaldi) (Ultra-Fast CPU Streaming)
* **Best for**: Instant token-by-token continuous streaming with lowest latency (~20–40ms) and minimal CPU load (<3%).
* **Models**:
  * `small`: `vosk-model-small-en-us-0.15` (~40MB, auto-downloads on first launch).
  * `accurate`: `vosk-model-en-us-0.22` (~1.8GB broadcast model).

---

### Option D: Google Gemini 3.5 Transcribe Live
* **Best for**: State-of-the-art conversational intelligence, custom vocabulary recognition, and automatic removal of filler words (*"ums"* and *"ahs"*).
* **Setup**:
  1. Get a free API key at **[Google AI Studio (aistudio.google.com)](https://aistudio.google.com/)**.
  2. Paste your `AIzaSy...` key in the Web Dashboard under **Gemini 3.5 Transcribe Live Settings**.
  3. Click **💾 Save Audio & Engine**.

---

### Option E: Local Faster-Whisper (NVIDIA GPU)
* **Best for**: Windows PCs with dedicated NVIDIA GPUs (e.g. GTX 1660, RTX 3060/4070+).
* **Setup**: Select **Local Faster-Whisper** and click the **🎮 GTX 1660 (Recommended)** 1-click preset button.

---

### Option F: Bandwidth Labs Live STT (Streaming Cloud)
* **Best for**: Ultra-fast enterprise real-time streaming over WebSocket.
* **Setup**:
  1. Get an API key from [Bandwidth Labs (labs.bandwidth.com)](https://labs.bandwidth.com/docs/speech-to-text).
  2. Paste your `bwa_key_...` in the Web Dashboard under **Bandwidth Labs Settings** (or set `BANDWIDTH_API_KEY` env var).
  3. Click **💾 Save Audio & Engine**.

---

## 🖥️ Step 3: Setting Up OBS Studio

### A. Add the In-OBS Control Dock (Recommended)
This puts the live control dashboard, microphone VU meter, and status controls directly inside OBS:
1. In OBS Studio, open the top menu: **Docks ➔ Custom Browser Docks...**
2. In the table:
   * **Dock Name**: `Live Captions`
   * **URL**: `http://127.0.0.1:8765/dashboard` *(or port 8080)*
3. Click **Apply**.
4. Drag and snap the dock anywhere in your OBS workspace.

---

### B. Add the Stream Caption Overlay
This adds transparent, beautifully styled captions over your broadcast video:
1. In your active OBS Scene, under **Sources**, click **+ ➔ Browser**.
2. Name the source: `Captions Overlay`.
3. Set the properties:
   * **URL**: `http://127.0.0.1:8765/`
   * **Width**: `1920` (or match your base canvas)
   * **Height**: `1080` (or match your base canvas)
   * ✅ Check: **"Shutdown source when not visible"**
   * ✅ Check: **"Refresh browser when scene becomes active"**
4. Click **OK**.

---

### C. Add the Room / Stage Confidence Monitor
For secondary displays, stage confidence monitors, or TVs in another room:
1. Open a browser on the remote display or TV.
2. Navigate to:
   ```
   http://<YOUR_COMPUTER_IP>:8765/display
   ```
3. Features:
   * Giant, high-contrast, scalable text (`+` / `-` buttons or keys).
   * Live clock and status badge (`🔴 LIVE`).
   * 1-Click fullscreen (`⛶` button or `F11`). Press `H` to toggle header controls on/off.

---

### D. Enable Zero-Touch Auto-Start with OBS (Launch when OBS Opens) 🚀
If you want VoxStream to **automatically start running in the background the moment OBS Studio opens**, you can link the included OBS Python script:
1. In OBS Studio, go to **Tools ➔ Scripts**.
2. On **macOS**:
   * Under **Python Settings**, verify your Python framework path is selected (e.g. `/opt/homebrew/Frameworks/Python.framework/Versions/3.11` or `/Library/Frameworks/Python.framework/Versions/3.11`).
3. On **Windows**:
   * Under **Python Settings**, select your Python directory (e.g. `C:\Users\<User>\AppData\Local\Programs\Python\Python311`).
4. Click the **Scripts** tab, click **+ (Add Script)**, and choose `obs_script/obs_live_captions.py`.
5. In the script properties on the right:
   * ✅ Check **"Auto-start when OBS launches"**
   * ✅ Check **"Auto-start when Streaming/Recording starts"**

---

### E. Enable YouTube & Twitch Closed Captions (Native [CC] Button) 📺
To send viewer-toggleable closed captions directly into your YouTube and Twitch video player:
1. **In OBS Studio**:
   * Go to **Tools ➔ WebSocket Server Settings**.
   * Check **"Enable WebSocket server"** (Port `4455`).
2. **In YouTube Studio (Live Control Room)**:
   * Go to your stream's **Stream Settings** tab.
   * Under **Closed captions**, toggle the switch **ON**.
   * Select **"Embedded 608/708"** as the caption source.
3. **In VoxStream**:
   * In the Web Dashboard (`/dashboard`), verify that **"Send CEA-608 Captions to Stream"** is checked in settings.
   * VoxStream will automatically inject standard CEA-608/708 packets into OBS H.264 stream headers, activating the native `[CC]` button on YouTube and Twitch!

---

## 🎨 Step 4: Customizing Filters, Glossary & Typography

Open the Web Dashboard (`/dashboard`):

### 1. 📖 Custom Glossary & Vocabulary Tab
* Fix misheard names, proper nouns, church terms, or gaming slang (e.g. automatically replace `"box stream"` with `"VoxStream"`, or `"jhon"` with `"John"`).
* Includes a live testing sandbox to verify replacements instantly.

### 2. 🛡️ Safety & Church Filter Tab
* Filter profanities, crude slang, and harsh curses with 4 action modes: **Wholesome Word Replacement**, **Asterisk Masking (`****`)**, **`[CENSORED]` Tag**, or **Drop Sentence**.
* Religious and sacred names (*"Jesus Christ"*, *"God"*, *"Lord"*, *"scripture"*, *"worship"*) are whitelisted and protected from censorship.

### 3. 🎨 Style & Themes Tab
* Select from 1-click theme presets (*Modern Clean, Broadcast Lower-Third, Sanctuary & Worship, Corporate Keynote, Minimal Cinema, Stage Confidence, Editorial Talk Show, Classic Broadcast CEA-708*).
* Customize fonts (Google Fonts), font sizes, text stroke, highlight colors, and word-pop animations.

---

## 🍎 macOS Specific Tips & Audio Routing

### 1. Microphone Permissions in macOS
When launching VoxStream for the first time on macOS:
* If prompted, click **Allow** to give Terminal / Python access to the microphone.
* If audio is not picking up, verify in macOS **System Settings ➔ Privacy & Security ➔ Microphone** that **Terminal** (or your code editor) is toggled **ON**.

### 2. Capturing Audio from Mixing Consoles & USB Interfaces
* macOS CoreAudio supports USB mixers and audio interfaces (Focusrite Scarlett, Behringer X32/Wing, Rode, PreSonus, Yamaha) natively without third-party drivers.
* In the Web Dashboard under **Audio & Engine**, pick your interface directly from the **Audio Input Device** dropdown.

### 3. Capturing OBS Audio Output on macOS (Virtual Routing)
* If you want VoxStream to capture audio being monitored out of OBS on macOS:
  1. Install the free [BlackHole 2ch](https://existential.audio/blackhole/) virtual audio driver.
  2. In OBS: Go to **Settings ➔ Audio ➔ Advanced ➔ Monitoring Device** and select **BlackHole 2ch**.
  3. In VoxStream Dashboard: Select **BlackHole 2ch** as your Audio Input Device.

---

## 🛠️ Troubleshooting & FAQs

### Q: How do I restart or stop VoxStream?
* **In the Dashboard**: Click the **🔄 Restart** or **🛑 Quit** button in the top navigation header.
* **In Terminal**: Press `Ctrl+C` to shut down cleanly.

### Q: Port 8765 is already in use by another app (e.g. REAPER).
* VoxStream automatically detects occupied ports and will automatically bind to the next available port (e.g. `8766` or `8080`) without crashing! Check your terminal log for the active port.

### Q: How do I control it from an Elgato Stream Deck?
* Add an HTTP request action to your Stream Deck:
  * Start: `POST http://127.0.0.1:8765/api/control/start`
  * Stop: `POST http://127.0.0.1:8765/api/control/stop`
  * Restart: `POST http://127.0.0.1:8765/api/control/restart`
