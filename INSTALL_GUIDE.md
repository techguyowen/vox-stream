# 📖 VoxStream Live Captioner — Complete Installation & Setup Guide

This step-by-step guide walks you through setting up the **VoxStream Real-Time Live Captioner & Broadcast Suite** on **macOS**, **Windows**, and **Linux**.

---

## 📋 Table of Contents
1. [Prerequisites](#-1-prerequisites)
2. [Step 1: Installation & Launch](#-step-1-installation--launch)
   - [macOS / Linux Installation](#-macos--linux-setup)
   - [Windows Installation](#-windows-setup)
3. [Step 2: Choosing Your Speech Engine](#-step-2-choosing-your-speech-engine)
   - [Option A: Vosk (Ultra-Fast CPU Streaming - Zero Latency)](#option-a-vosk-kaldi-ultra-fast-cpu-streaming)
   - [Option B: Local Moonshine (Fast Offline Neural Model - Mac GPU / MPS)](#option-b-local-moonshine-fast-offline-ai--mac-gpu-mps)
   - [Option C: Google Gemini 3.5 Transcribe Live](#option-c-google-gemini-35-transcribe-live)
   - [Option D: Local Faster-Whisper (NVIDIA GPU / CUDA)](#option-d-local-faster-whisper-nvidia-gpu)
   - [Option E: Google Speech (100% Free / Zero-Setup)](#option-e-google-speech-recognition-100-free--zero-setup)
   - [Option F: Bandwidth Labs Live STT (Streaming Cloud)](#option-f-bandwidth-labs-live-stt-streaming-cloud)
4. [Step 3: Setting Up OBS Studio](#-step-3-setting-up-obs-studio)
   - [Add the In-OBS Control Dock](#a-add-the-in-obs-control-dock-recommended)
   - [Add the Transparent Stream Overlay](#b-add-the-transparent-stream-overlay)
   - [Enable Native Closed Captions (YouTube & Twitch [CC])](#c-enable-youtube--twitch-closed-captions-native-cc-button-)
   - [Enable Zero-Touch Auto-Start with OBS](#d-enable-zero-touch-auto-start-with-obs-launch-when-obs-opens-)
5. [Step 4: PWA Stage Confidence Monitor & Multi-Device Setup](#-step-4-pwa-stage-confidence-monitor--multi-device-setup)
   - [Accessing on Local Wi-Fi / Network](#1-accessing-on-local-wi-fi--network)
   - [Installing as a PWA (iPad, iPhone, Android, Laptop)](#2-installing-as-a-pwa-standalone-app)
   - [2 Tailored Display Modes](#3-choosing-a-display-mode)
   - [Screen Wake Lock (No Sleep on Stage)](#4-automatic-screen-wake-lock)
6. [Step 5: Customizing Filters, Glossary & Themes](#-step-5-customizing-filters-glossary--themes)
7. [macOS Specific Tips & Audio Routing](#-macos-specific-tips--audio-routing)
8. [Troubleshooting & FAQs](#-troubleshooting--faqs)

---

## ⚙️ 1. Prerequisites

* **Operating System**:
  * **macOS**: macOS 12 (Monterey), 13 (Ventura), 14 (Sonoma), 15 (Sequoia) on Apple Silicon (M1/M2/M3/M4) or Intel.
  * **Windows**: Windows 10 or 11 (64-bit).
  * **Linux**: Ubuntu 20.04+, Debian, Fedora, Arch.
* **OBS Studio**: OBS Studio 28.0 or newer (OBS WebSocket v5 is built-in by default).
* **Python**: Python 3.9, 3.10, 3.11, or 3.12.

---

## 🚀 Step 1: Installation & Launch

### 🍏 macOS & Linux Setup

1. Open **Terminal** and navigate to the project directory:
   ```bash
   git clone https://github.com/techguyowen/vox-stream.git
   cd vox-stream
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
   *(Leave this running in Terminal. You can now access the Web Dashboard at `http://127.0.0.1:8765/dashboard`)*.

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

### Option A: Vosk (Kaldi) (Ultra-Fast CPU Streaming) ⭐ LOWEST LATENCY
* **Best for**: Instant token-by-token continuous streaming with lowest latency (~20–40ms), 100% offline, zero API keys, and minimal CPU load (<3%).
* **Models**:
  * `small`: `vosk-model-small-en-us-0.15` (~40MB, auto-downloads on first launch).
  * `accurate`: `vosk-model-en-us-0.22` (~1.8GB broadcast model).

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

### Option C: Google Gemini 3.5 Transcribe Live
* **Best for**: State-of-the-art conversational intelligence, custom vocabulary recognition, and automatic removal of filler words (*"ums"* and *"ahs"*).
* **Setup**:
  1. Get a free API key at **[Google AI Studio (aistudio.google.com)](https://aistudio.google.com/)**.
  2. Paste your `AIzaSy...` key in the Web Dashboard under **Gemini 3.5 Transcribe Live Settings**.
  3. Click **💾 Save Audio & Engine**.

---

### Option D: Local Faster-Whisper (NVIDIA GPU)
* **Best for**: Windows PCs with dedicated NVIDIA GPUs (e.g. GTX 1660, RTX 3060/4070+).
* **Setup**: Select **Local Faster-Whisper** and click the **🎮 GTX 1660 (Recommended)** 1-click preset button.

---

### Option E: Google Speech Recognition (100% Free / Zero-Setup)
* **Best for**: Instant, zero-friction setup without needing API keys or cloud accounts.
* **Cost**: 100% Free.

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
   * **URL**: `http://127.0.0.1:8765/dashboard`
3. Click **Apply**.
4. Drag and snap the dock anywhere in your OBS workspace.

---


### B. Add the Transparent Stream Overlay (Live Captions)
1. In your active OBS Scene, under **Sources**, click **+ ➔ Browser**.
2. Name the source: `Captions Overlay`.
3. Set the properties:
   * **URL**: `http://127.0.0.1:8765/` *(or `http://127.0.0.1:8765/?lang=es` for Spanish subtitles)*
   * **Width**: `1920` (or match your base canvas)
   * **Height**: `1080` (or match your base canvas)
   * ✅ Check: **"Shutdown source when not visible"**
   * ✅ Check: **"Refresh browser when scene becomes active"**
4. Click **OK**.

---

### C. Add the Dedicated Scripture Overlay (Pure Bible Passages) 📖
If you want an isolated lower-third specifically for **Bible Passages & Scripture Verses** (with zero speech captions appearing on it):
1. In OBS Studio, under **Sources**, click **+ ➔ Browser**.
2. Name the source: `Scripture Overlay`.
3. Set the properties:
   * **URL**: `http://127.0.0.1:8765/bible`
   * **Width**: `1920`
   * **Height**: `1080`
   * ✅ Check: **"Shutdown source when not visible"**
   * ✅ Check: **"Refresh browser when scene becomes active"**
4. Click **OK**.
*(This overlay exclusively renders beautiful gold-badged scripture lower-thirds whenever a Bible verse is spoken or cued via the dashboard!)*


---

### D. Enable YouTube & Twitch Closed Captions (Native [CC] Button) 📺
To send viewer-toggleable closed captions directly into your YouTube and Twitch video player:
1. **In OBS Studio**:
   * Go to **Tools ➔ WebSocket Server Settings**.
   * Check **"Enable WebSocket server"** (Port `4455`).
2. **In YouTube Studio (Live Control Room)**:
   * Go to your stream's **Stream Settings** tab.
   * Under **Closed captions**, toggle the switch **ON**.
   * Select **"Embedded 608/708"** as the caption source.
3. **In VoxStream**:
   * In the Web Dashboard (`/dashboard`), verify that **"Send CEA-608 Captions to Stream"** is checked.
   * VoxStream will automatically inject standard CEA-608/708 packets into OBS H.264 stream headers, activating the native `[CC]` button on YouTube and Twitch!

---

### E. Enable Zero-Touch Auto-Start with OBS (Launch when OBS Opens) 🚀
1. In OBS Studio, go to **Tools ➔ Scripts**.
2. Under **Python Settings**, select your Python path.
3. Click the **Scripts** tab, click **+ (Add Script)**, and choose `obs_script/obs_live_captions.py`.
4. Check **"Auto-start when OBS launches"** and **"Auto-start when Streaming/Recording starts"**.

---

## 📱 Step 4: PWA Stage Confidence Monitor & Multi-Device Setup

VoxStream includes a dedicated **Progressive Web App (PWA)** confidence monitor at `/display` built for stage monitors, podium iPads, overflow rooms, and choir screens.

### 1. Accessing on Local Wi-Fi / Network
VoxStream automatically binds to `0.0.0.0:8765`, making it accessible to any device on the same local network:
* **On Main Computer**: `http://127.0.0.1:8765/display`
* **On Any iPad, Phone, or Stage TV**: `http://<YOUR_COMPUTER_IP>:8765/display` *(e.g. `http://192.168.1.145:8765/display`)*

---

### 2. Installing as a PWA (Standalone App)
* **iPad / iPhone (Safari)**:
  1. Open `http://<YOUR_IP>:8765/display` in Safari.
  2. Tap the **Share** button (`⎙` / `↑`).
  3. Tap **"Add to Home Screen"**.
* **Android (Chrome)**:
  1. Open `http://<YOUR_IP>:8765/display`.
  2. Tap the **`📲 Install`** button in the top header (or Chrome Menu ➔ *"Install App"*).
* **Mac / PC (Chrome / Edge / Brave)**:
  1. Click the **`📲 Install`** button in the header bar or the install icon in the URL bar to run VoxStream as a separate desktop window.

---

### 3. Choosing a Display Mode
The display bar includes two dedicated modes:
* **`📜 Scrollable History` (Default)**: Displays the complete continuous transcript of the sermon or presentation. Presenters can scroll up with touch or mouse wheel to read previous points or Bible verses.
* **`⚡ Live Prompter (Auto-Fade)`**: Shows only the active 2 lines on screen and automatically fades out on silence for clean broadcast teleprompting.

---

### 4. Automatic Screen Wake Lock
### 5. 👁️ Visual Aid & Reading Comfort Controls
On the Stage Display toolbar:
* **`[ ] 👁️ Visual Aid Mode`**: 1-click toggle that expands character tracking (`0.06em`), word spacing (`0.15em`), and line height (`1.65x`) while keeping your custom font and theme active.
* **`[ ] 🐢 Slow Down Text`**: Intelligently paces fast speech bursts to a calm **135 WPM** flow and holds completed sentences on screen for **10.0 seconds** to eliminate reading fatigue.
* **`⬇️ Jump to Live` Button**: When scrolling up to review earlier notes, auto-scroll pauses so your view stays undisturbed. A floating button appears at the bottom right to smoothly glide back to the live words.

The display monitor automatically invokes the **Screen Wake Lock API**, preventing tablets and phones on podiums or stage stands from dimming or falling asleep during live speech.

---

## 🎨 Step 5: Customizing Filters, Glossary & Themes

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
* VoxStream automatically detects occupied ports and will automatically bind to the next available port (e.g. `8766` or `8767`) without crashing! Check your terminal log for the active port.

### Q: How do I control it from an Elgato Stream Deck or Bitfocus Companion?
* See the full guide and downloadable profile configs in [**`integrations/streamdeck_companion.md`**](integrations/streamdeck_companion.md).
* Quick HTTP actions:
  * Panic Wipe: `POST http://127.0.0.1:8765/api/control/panic`
  * Toggle Speech: `POST http://127.0.0.1:8765/api/control/toggle`
  * Start: `POST http://127.0.0.1:8765/api/control/start`
  * Stop: `POST http://127.0.0.1:8765/api/control/stop`
  * Restart: `POST http://127.0.0.1:8765/api/control/restart`
