# 📖 OBS Real-Time Live Captioner — Complete Installation & Setup Guide

This step-by-step guide walks you through setting up the **OBS Real-Time Live Captioner Suite** on Windows.

---

## 📋 Table of Contents
1. [Prerequisites](#-1-prerequisites)
2. [Step 1: One-Click Installation](#-step-1-one-click-installation)
3. [Step 2: Choosing Your Speech Engine](#-step-2-choosing-your-speech-engine)
4. [Step 3: Setting Up OBS Studio](#-step-3-setting-up-obs-studio)
   - [Add the Control Dock](#a-add-the-in-obs-control-dock-recommended)
   - [Add the Stream Overlay](#b-add-the-stream-caption-overlay)
   - [Enable Zero-Touch Auto-Start](#c-enable-zero-touch-auto-start-with-obs)
   - [Enable Twitch/YouTube Closed Captions](#d-enable-twitch--youtube-closed-captions-cea-608)
5. [Step 4: Customizing Filters & Typography](#-step-4-customizing-filters--typography)
6. [Troubleshooting & FAQs](#-troubleshooting--faqs)

---

## ⚙️ 1. Prerequisites

- **Windows 10 / 11**
- **OBS Studio 28.0 or newer** (OBS WebSocket v5 is built-in by default)
- *(Optional)* Python 3.10+ — **If you don't have Python installed, `setup_windows.bat` will automatically install Python 3.11 for you using `winget`!**

---

## 🚀 Step 1: One-Click Installation

1. Open the project folder:
   ```
   vox-stream/
   ```
2. Double-click the file:
   ```cmd
   setup_windows.bat
   ```
3. The script will automatically:
   - Check if Python is installed (if missing, installs Python 3.11 via Windows `winget`).
   - Create an isolated `.venv` Python virtual environment.
   - Install all required audio, translation, and AI packages (`sounddevice`, `websockets`, `aiohttp`, `google-genai`, etc.).
   - Create your default `config.json`.
   - Print a list of all your connected audio devices with their **Index numbers**.

---

## 🔑 Step 2: Choosing Your Speech Engine

You can configure your engine directly in `config.json` or through the Web Control Dashboard after launching:

### Option A: Google Cloud Speech-to-Text (Lowest Latency & Highest Accuracy)
1. Download your Service Account JSON key from [Google Cloud Console](https://console.cloud.google.com/).
2. Place the JSON file in the project folder (e.g., `google_credentials.json`).
3. In `config.json` (or the Web Dashboard):
   ```json
   "general": {
     "engine": "google_stt"
   },
   "google_stt": {
     "credentials_path": "google_credentials.json"
   }
   ```

### Option B: Google Gemini 3.5 Transcribe Live (State-of-the-Art Intelligence & Accuracy)

Gemini 3.5 Transcribe Live is Google's dedicated streaming speech-to-text AI model (`gemini-3.5-transcribe-live`), offering sub-second real-time latency, automatic filler-word cleanup (*"ums"* and *"ahs"*), natural conversational self-corrections, and custom vocabulary support.

#### 1. How to Get a Free Google AI Studio API Key:
1. Visit **[Google AI Studio (aistudio.google.com)](https://aistudio.google.com/)** in your browser.
2. Sign in with your Google account.
3. In the left navigation menu, click **"Get API key"** (or click [this direct link](https://aistudio.google.com/app/apikey)).
4. Click **"Create API key"** ➔ Choose an existing Google Cloud project or click **"Create API key in new project"**.
5. Copy your new API key (it will look like `AIzaSy...`).

---

#### 2. Configure via the Web Dashboard / OBS Dock (Easiest & Recommended):
1. Launch the captioner by double-clicking **`run_captioner.bat`**.
2. Open the **Web Dashboard** at `http://127.0.0.1:8080/dashboard` (or open your **Live Captions** Dock inside OBS).
3. Click on the **🎙️ Audio & Engine** tab.
4. Under **Speech-to-Text Engine**, select:
   - **✨ Gemini 3.5 Transcribe Live (Google AI Studio / Live API)**
5. In the **Gemini 3.5 Transcribe Live Settings** section:
   - **Model Version**: Select `gemini-3.5-transcribe-live` *(Recommended for lowest latency streaming)*.
   - **Google AI Studio API Key**: Paste your `AIzaSy...` key.
   - **Custom Vocabulary**: Enter any specialized gaming gamer tags, church terms, jargon, or names separated by commas (e.g. `OBS Studio, Twitch, Discord, Jesus Christ, Pastor Mike`).
   - **Smart Transcription**: Keep checked to automatically remove *"ums/ahs"* and format proper punctuation and capitalization.
6. Click **💾 Save Audio & Engine**. The system will immediately hot-reload and connect to Gemini Live!

---

#### 3. Alternative: Configure via `config.json`:
Open `config.json` in Notepad or VS Code and edit the `gemini_live` section:
```json
{
  "general": {
    "engine": "gemini_live",
    "language": "en-US"
  },
  "gemini_live": {
    "api_key": "AIzaSyYourActualKeyHere...",
    "model": "gemini-3.5-transcribe-live",
    "smart_transcription": true,
    "custom_vocabulary": [
      "OBS Studio",
      "Twitch",
      "Discord",
      "YouTube",
      "Jesus Christ"
    ],
    "system_instruction": "You are Gemini 3.5 Transcribe, a real-time speech transcriber. Transcribe the incoming audio accurately into text verbatim. Output only the transcribed text without commentary, pleasantries, or conversation."
  }
}
```

*(Note: You can also set a system environment variable `GEMINI_API_KEY=AIzaSy...` and the app will detect it automatically).*

### Option C: Google Speech Recognition (100% Free / Zero-Setup — No API Keys Needed!)
This is the same zero-setup free Google endpoint used by the classic OBS Cloud Closed Captions plugin. It requires **no accounts, no credit cards, and no API keys**:
1. In the Web Dashboard (`http://127.0.0.1:8080/dashboard`), select:
   - **🆓 Google Speech Recognition (100% Free / Zero-Setup - No API Key!)**
2. Or in `config.json`:
   ```json
   "general": {
     "engine": "google_web",
     "language": "en-US"
   }
   ```
3. Click **Save** and start speaking immediately!

### Option D: Local Faster-Whisper (100% Offline, No API Keys Needed)
Runs directly on your computer's NVIDIA GPU (CUDA) or CPU with zero internet required:
1. In the Web Dashboard (`http://127.0.0.1:8080/dashboard`), select:
   - **Local Faster-Whisper (100% Offline GPU/CPU)**
2. **NVIDIA GTX 1660 (6GB VRAM) Recommended Settings**:
   - Click the **🎮 GTX 1660 (Recommended)** 1-click preset button:
     - **Model Size**: `small.en` (or `base.en` for instant 60ms response)
     - **Acceleration Device**: `cuda` (NVIDIA GPU)
     - **GPU Compute Precision**: `float16` (Turing architecture fast FP16)
     - **Beam Width**: `1`
   - **VRAM Impact**: Uses only **~1.8 GB of VRAM**, leaving **~4.2 GB of VRAM completely free** for OBS NVENC encoding and high-FPS gaming!
3. Or configure in `config.json`:
   ```json
   "general": {
     "engine": "local_whisper"
   },
   "local_whisper": {
     "model_size": "small.en",
     "device": "cuda",
     "compute_type": "float16",
     "beam_size": 1
   }
   ```

---

## 🖥️ Step 3: Setting Up OBS Studio

First, launch the captioner by double-clicking:
```cmd
run_captioner.bat
```
*(Leave the window running in the background, or use the auto-start script below).*

---

### A. Add the In-OBS Control Dock (Recommended)
This gives you a visual settings panel, live microphone VU meter, and transcript manager right inside OBS Studio!

1. In OBS Studio, go to the top menu: **Docks ➔ Custom Browser Docks...**
2. In the table:
   - **Dock Name**: `Live Captions`
   - **URL**: `http://127.0.0.1:8080/dashboard`
3. Click **Apply**.
4. Drag and dock the panel anywhere in your OBS workspace (next to Audio Mixer, Scenes, or Chat).

---

### B. Add the Stream Caption Overlay
This displays the smooth animated captions on your stream canvas.

1. In your active OBS Scene, under **Sources**, click **+ ➔ Browser**.
2. Name the source: `Captions Overlay`.
3. Set the properties:
   - **URL**: `http://127.0.0.1:8080/`
   - **Width**: `1920` (or match your canvas width)
   - **Height**: `1080` (or match your canvas height)
   - ✅ Check: **"Shutdown source when not visible"**
   - ✅ Check: **"Refresh browser when scene becomes active"**
4. Click **OK**. The captions will appear transparently over your video.

---

### C. Enable Zero-Touch Auto-Start with OBS
To have captions start automatically whenever OBS opens or when you click "Start Streaming":

1. In OBS Studio, go to **Tools ➔ Scripts**.
2. Click the **Python Settings** tab and ensure your Python folder is selected (e.g. `C:\Users\<Username>\AppData\Local\Programs\Python\Python310`).
3. Click the **Scripts** tab, click **+ (Add Script)**.
4. Select `obs_script/obs_live_captions.py` from the project folder.
5. In the script properties on the right:
   - ✅ Check **"Auto-start when OBS launches"**
   - ✅ Check **"Auto-start when Streaming/Recording starts"**

---

### D. Enable Twitch & YouTube Closed Captions (CEA-608)
If you want viewers to be able to click the **`[CC]`** button on Twitch or YouTube:

1. In OBS Studio, go to **Tools ➔ WebSocket Server Settings**.
2. Ensure **"Enable WebSocket server"** is checked (Server Port: `4455`).
3. The captioner will automatically inject CEA-608 subtitle packets directly into your stream!

---

## 🎨 Step 4: Customizing Filters & Typography

Open the in-OBS Dock or visit `http://127.0.0.1:8080/dashboard`:

### 1. Visual Customizer
- **Fonts**: Pick from Google Fonts (*Montserrat, Inter, Poppins, Bebas Neue, Oswald, Roboto, Bangers*) or system fonts.
- **Sliders**: Adjust **Font Size** (16px–72px), **Max Width** (30%–100%), and **Max Lines** (1–4).
- **Colors**: Pick your Text Color, Word Highlight Color, and Box Background Opacity.
- **Animations**: Choose between *Word Pop (Bouncy)*, *Karaoke Highlight*, *Smooth Fade*, or *Instant*.

### 2. Church & Family-Safe Filter
- **Action Modes**:
  - **Wholesome Word Replacement**: Swaps profanities with family-friendly substitutions.
  - **Asterisk Masking**: Replaces sensitive characters with asterisks (`****`).
  - **`[CENSORED]` Tag**: Replaces filtered words with a bracketed label.
  - **Drop Sentence**: Drops the entire subtitle line if inappropriate language is detected.
- **Sacred Names Whitelist**: Religious and sacred names (*"Jesus Christ"*, *"Jesus"*, *"Christ"*, *"God"*, *"Lord"*, *"worship"*, *"pastor"*) are protected and will never be censored.
- **Custom Blacklist / Whitelist**: Add any custom words or phrases you want to filter or protect.

### 3. Subtitle Exports
- In the **Transcripts & Export** tab, download timed subtitle files in **`.SRT`**, **`.VTT`**, or **`.TXT`** format with 1 click for YouTube captions or video editing in Premiere / DaVinci Resolve.

---

## 🔌 Option: Compiling & Installing the Native C++ Plugin (`.dll`)

If you want a pure compiled in-process C++ `.dll` filter plugin for OBS Studio:

1. Ensure **CMake** and **Visual Studio (C++ Desktop Development)** are installed on your Windows PC.
2. Double-click [**`build_plugin_windows.bat`**](file:///Users/techguyowen/Documents/antigravity/delightful-bohr/build_plugin_windows.bat).
3. The script compiles `obs_native_plugin/` into `build\Release\obs-live-captions.dll`.
4. Copy `obs-live-captions.dll` into:
   ```
   C:\Program Files\obs-studio\obs-plugins\64bit\
   ```
5. Launch OBS Studio:
   - Go to **Tools ➔ Live Speech Captions Settings...** for the native Qt dialog.
   - Right-click any audio source ➔ **Filters ➔ Add "Live Speech Captions (AI)"**.

---

## 🛠️ Troubleshooting & FAQs

### Q: `setup_windows.bat` says Python is not recognized.
- **Fix**: Re-run the Python Windows installer, select **Modify**, and make sure **"Add Python to PATH"** is checked. Restart your terminal or PC after installing.

### Q: How do I select a specific microphone (e.g. Elgato Wave Link, Voicemeeter, USB Mic)?
- **Fix**: Open the Web Dashboard (`http://127.0.0.1:8080/dashboard`), go to the **Audio & Engine** tab, and pick your microphone from the **Audio Input Device** dropdown.

### Q: The Browser Source overlay is black instead of transparent.
- **Fix**: In OBS Browser Source properties, make sure the **Custom CSS** field is empty or `body { background-color: rgba(0, 0, 0, 0); margin: 0px auto; overflow: hidden; }`. The overlay stylesheet handles transparency automatically.

### Q: How do I control it from an Elgato Stream Deck?
- **Fix**: Add a **System ➔ Website / HTTP Request** action to your Stream Deck:
  - Start Captions: `POST http://127.0.0.1:8080/api/control/start`
  - Stop Captions: `POST http://127.0.0.1:8080/api/control/stop`
